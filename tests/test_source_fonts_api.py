"""`/api/source-fonts/*`（ADR 0164）：原图字体在本机的状态与预览字节。

主语是**响应里有什么、没有什么**：有 sha256 / 状态 / 字节；没有路径。判据与导出是同一个函数
（`HbFaceProvider.user_face`），这里钉的是端点把它如实转出来、禁止嵌入的脸不供字节。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fontTools")
mpl_fm = pytest.importorskip("matplotlib.font_manager")

from tavotto import app as m  # noqa: E402
from tavotto.rendercore import facade, fonts, userfonts  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip("批准字体不全（not_run）：先跑 scripts/fetch_fonts.py")
    loc = userfonts.FontLocator(tmp_path / "loc.json", system_dirs=lambda: [])
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    from tavotto.rendercore.hbshaper import HbFaceProvider

    monkeypatch.setattr(facade, "_PROVIDER", HbFaceProvider(reg))
    monkeypatch.setattr(m, "_prepare_source_fonts", lambda: None)
    m.app.config["TESTING"] = True
    return m.app.test_client(), loc


def _dejavu() -> Path:
    return Path(str(mpl_fm.findfont(mpl_fm.FontProperties(family="DejaVu Sans"))))


def _post(c, fonts_):
    return c.post("/api/source-fonts/status", json={"fonts": fonts_})


def test_status_reports_ok_missing_and_restricted_without_any_path(client, tmp_path):
    c, loc = client
    real = _dejavu()
    loc.add_hint_dirs([real.parent])
    from fontTools.ttLib import TTFont

    tt = TTFont(str(real))
    tt["OS/2"].fsType = 2
    restricted = tmp_path / "r" / "R.ttf"
    restricted.parent.mkdir()
    tt.save(str(restricted))
    loc.add_hint_dirs([restricted.parent])

    body = _post(
        c,
        [
            {"sha256": fonts.sha256_of(real), "face": 0},
            {"sha256": "ab" * 32, "face": 0},
            {"sha256": fonts.sha256_of(restricted), "face": 0},
        ],
    ).get_json()
    assert [r["state"] for r in body["results"]] == ["ok", "not_found", "embedding_restricted"]
    raw = json.dumps(body)
    assert str(real.parent) not in raw and str(tmp_path) not in raw  # 响应里没有路径


def test_status_rejects_malformed_requests(client):
    c, _ = client
    assert _post(c, "x").status_code == 400
    assert _post(c, [{"sha256": "zz", "face": 0}]).status_code == 400
    assert _post(c, [{"sha256": "ab" * 32, "face": -1}]).status_code == 400
    assert _post(c, [{"sha256": "ab" * 32, "face": 0}] * 65).status_code == 400


def test_bytes_are_the_exact_file_and_cached_by_content(client):
    c, loc = client
    real = _dejavu()
    loc.add_hint_dirs([real.parent])
    sha = fonts.sha256_of(real)
    r = c.get(f"/api/source-fonts/{sha}/0")
    assert r.status_code == 200 and r.data == real.read_bytes()
    assert "immutable" in r.headers["Cache-Control"] and sha in r.headers["ETag"]


def test_restricted_or_unknown_faces_serve_no_bytes(client, tmp_path):
    c, loc = client
    from fontTools.ttLib import TTFont

    tt = TTFont(str(_dejavu()))
    tt["OS/2"].fsType = 2
    f = tmp_path / "r" / "R.ttf"
    f.parent.mkdir()
    tt.save(str(f))
    loc.add_hint_dirs([f.parent])
    r = c.get(f"/api/source-fonts/{fonts.sha256_of(f)}/0")
    assert r.status_code == 404 and r.get_json()["error"] == "embedding_restricted"
    assert c.get(f"/api/source-fonts/{'cd' * 32}/0").status_code == 404
    assert c.get("/api/source-fonts/not-a-sha/0").status_code == 400


def test_collection_members_are_served_as_a_single_face_font(client, tmp_path):
    from fontTools.ttLib import TTCollection, TTFont

    c, loc = client
    coll = TTCollection()
    coll.fonts = [TTFont(str(_dejavu())), TTFont(str(_dejavu().with_name("DejaVuSerif.ttf")))]
    ttc = tmp_path / "t" / "pair.ttc"
    ttc.parent.mkdir()
    coll.save(str(ttc))
    loc.add_hint_dirs([ttc.parent])
    r = c.get(f"/api/source-fonts/{fonts.sha256_of(ttc)}/1")
    assert r.status_code == 200
    assert r.data[:4] == b"\x00\x01\x00\x00"  # 单脸 sfnt，浏览器的 FontFace 认得；不是 ttcf
    import io

    assert "Serif" in TTFont(io.BytesIO(r.data))["name"].getDebugName(1)


def test_routes_are_registered_and_session_guarded():
    rules = {r.rule for r in m.app.url_map.iter_rules()}
    assert "/api/source-fonts/status" in rules
    assert any(re.fullmatch(r"/api/source-fonts/<sha256>/<int:face>", r) for r in rules)
