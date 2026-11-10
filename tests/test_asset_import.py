"""POST /api/assets/import（ADR 0176）：拖 / 粘贴文件进画布的后端那一半。

守的不变式：只写项目根、不覆盖用户文件、按内容判类型、落盘前先验证（不出现「复制进去了却没有素材」）、
同名同内容复用 / 异内容改名、超限 / 只读 / 磁盘满有明确 code、会话认证不被绕过。
"""

from __future__ import annotations

import errno
import io
import os
import struct
import threading
import zlib
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import assetimport, atomicio, pool, project_refresh, project_watch


def _png(w=4, h=3, fill=b"\x10\x20\x30") -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + fill * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 96 48"><path d="M0 0L96 48" stroke="red"/></svg>'


def _pdf(pages=1, encryption=None) -> bytes:
    import pikepdf

    pdf = pikepdf.Pdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(72, 36))
    out = io.BytesIO()
    if encryption:
        pdf.save(out, encryption=encryption)
    else:
        pdf.save(out)
    return out.getvalue()


def _pil(fmt: str, mode="RGB") -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new(mode, (5, 4)).save(out, fmt)
    return out.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    m.open_project(str(root))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("asset import executed a script"))
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def _post(client, data: bytes, name: str | None = "fig.png"):
    kw = {"query_string": {"name": name}} if name is not None else {}
    return client.post(
        "/api/assets/import", data=data, content_type="application/octet-stream", **kw
    )


#: 打开项目时 Tavotto 自己建的两样；其余都是用户的文件或我们导入的
_OWN = {"tavotto_registry.json", "tavottofile"}


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name not in _OWN)


# ---------------------------------------------------------------- 类型与内容


@pytest.mark.parametrize(
    ("make", "name", "kind"),
    [
        (lambda: _png(), "a.png", "raster"),
        (lambda: _pil("JPEG"), "a.jpg", "raster"),
        (lambda: _pil("TIFF"), "a.tif", "raster"),
        (lambda: _pdf(), "a.pdf", "pdf"),
        (lambda: SVG, "a.svg", "svg"),
    ],
)
def test_every_scanned_asset_type_imports_and_shows_in_panels(project, make, name, kind):
    client, root = project
    data = make()
    resp = _post(client, data, name)
    assert resp.status_code == 201, resp.json
    body = resp.json
    assert (body["file"], body["kind"], body["reused"], body["renamed"]) == (
        name,
        kind,
        False,
        False,
    )
    assert (root / name).read_bytes() == data
    assert [p["id"] for p in client.get("/api/panels").json["panels"]] == [body["id"]]
    assert _files(root) == [name]  # 没有留下临时文件


def test_extension_list_matches_the_scanner_and_the_frontend_golden():
    import json

    scanned = project_refresh.PDF_EXT | project_refresh.SVG_EXT | project_refresh.IMG_EXT
    ours = {e for exts in assetimport.EXTENSIONS.values() for e in exts}
    assert ours == scanned
    golden = json.loads(
        (Path(__file__).parent / "golden" / "asset_import_extensions.json").read_text(
            encoding="utf-8"
        )
    )
    assert ours == set(golden["extensions"])


def test_type_comes_from_content_not_from_the_name(project):
    client, root = project
    resp = _post(client, _png(), "photo.jpg")  # 改了后缀的 PNG：按内容存成 .png
    assert resp.status_code == 201 and resp.json["file"] == "photo.png"
    resp = _post(client, _png(5, 5), "image")  # 剪贴板图片没有后缀
    assert resp.status_code == 201 and resp.json["file"] == "image.png"
    resp = _post(client, _pil("JPEG"), "x.JPEG")  # .jpeg 是 JPEG 的别写法：沿用
    assert resp.status_code == 201 and resp.json["file"] == "x.jpeg"


@pytest.mark.parametrize(
    "data",
    [
        b"hello world",
        b"MZ\x90\x00" + b"\0" * 64,
        b"PK\x03\x04" + b"\0" * 64,
        b"GIF89a" + b"\0" * 32,
        b"RIFF\x24\x00\x00\x00WEBPVP8 ",
        b"print('hi')\n",
    ],
)
def test_other_content_is_refused_and_nothing_is_written(project, data):
    client, root = project
    resp = _post(client, data, "evil.png")  # 后缀说是 png 也没用
    assert resp.status_code == 415 and resp.json["code"] == "asset_import_unsupported_type"
    assert _files(root) == []


def test_empty_body_is_refused(project):
    client, root = project
    resp = _post(client, b"", "a.png")
    assert resp.status_code == 400 and resp.json["code"] == "asset_import_empty"


@pytest.mark.parametrize(
    "data",
    [
        _png()[:20],  # PNG 截断
        b"%PDF-1.4\nnot really a pdf",
        b'<svg width="1" height="1"><script>alert(1)</script></svg>',
        b'<svg width="100%" height="100%"/>',
    ],
)
def test_recognised_but_unreadable_content_leaves_nothing_behind(project, data):
    client, root = project
    resp = _post(client, data, "broken")
    assert resp.status_code == 422, resp.json
    assert resp.json["code"] in {"asset_import_unreadable", "svg_asset_unsupported"}
    assert _files(root) == []  # 绝不出现「复制进去了、素材库里却没有」


def test_unsupported_tiff_is_refused_with_the_scanners_own_code(project):
    client, root = project
    resp = _post(client, _pil("TIFF", "CMYK"), "cmyk.tif")
    assert resp.status_code == 422 and resp.json["code"].startswith("tiff_"), resp.json
    assert _files(root) == []


def test_encrypted_pdf_is_refused(project):
    import pikepdf

    client, root = project
    locked = _pdf(encryption=pikepdf.Encryption(owner="o", user="u"))
    resp = _post(client, locked, "locked.pdf")
    assert resp.status_code == 422 and resp.json["code"] == "asset_import_unreadable", resp.json
    assert _files(root) == []


def test_multipage_pdf_is_accepted_like_the_scanner_does(project):
    client, root = project
    resp = _post(client, _pdf(pages=3), "pages.pdf")
    assert resp.status_code == 201
    assert [p["id"] for p in client.get("/api/panels").json["panels"]] == ["pages.pdf"]


# ---------------------------------------------------------------- 文件名


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.png", "passwd.png"),
        ("..\\..\\win\\evil.png", "evil.png"),
        ("/abs/path/fig.png", "fig.png"),
        ("con.png", "_con.png"),
        ("NUL", "_NUL.png"),
        ("aux.txt", "_aux.txt.png"),
        ("trailing. .png", "trailing.png"),
        ("dots...png", "dots.png"),
        (".hidden.png", "hidden.png"),
        ('a<b>c:d|e?f*g"h.png', "a_b_c_d_e_f_g_h.png"),
        ("图 1（对照）.png", "图 1（对照）.png"),
        ("é.png", "é.png"),  # NFD → NFC
        ("tab\tname\n.png", "tab_name_.png"),
        ("   .png", "image.png"),
        ("", "image.png"),
        ("v1.2", "v1.2.png"),
    ],
)
def test_names_are_cleaned_to_a_basename_inside_the_project(project, raw, expected):
    client, root = project
    resp = _post(client, _png(), raw)
    assert resp.status_code == 201, resp.json
    assert resp.json["file"] == expected
    assert _files(root) == [expected]
    assert not (root.parent / "passwd.png").exists()


def test_long_names_are_cut_to_a_safe_length(project):
    client, root = project
    resp = _post(client, _png(), "长" * 400 + ".png")
    assert resp.status_code == 201
    assert len(resp.json["file"].encode()) <= 255
    assert (root / resp.json["file"]).is_file()


def test_missing_name_gets_a_default(project):
    client, root = project
    resp = _post(client, _png(), None)
    assert resp.status_code == 201 and resp.json["file"] == "image.png"


def test_client_cannot_choose_the_directory(project):
    client, root = project
    (root / "sub").mkdir()
    for i, field in enumerate(("dir", "path", "folder", "target")):
        resp = client.post(
            "/api/assets/import",
            query_string={"name": f"x{i}.png", field: str(root / "sub")},
            data=_png(),
            content_type="application/octet-stream",
        )
        assert resp.status_code == 201
    assert _files(root / "sub") == []


# ---------------------------------------------------------------- 同名


def test_same_name_same_content_reuses_the_existing_file(project):
    client, root = project
    first = _post(client, _png(), "fig.png").json
    again = _post(client, _png(), "fig.png")
    assert again.status_code == 200
    assert again.json["id"] == first["id"] and again.json["reused"] is True
    assert _files(root) == ["fig.png"]


def test_same_name_different_content_is_renamed_never_overwritten(project):
    client, root = project
    a, b, c = _png(4, 3), _png(5, 3), _png(6, 3)
    assert _post(client, a, "fig.png").json["file"] == "fig.png"
    resp = _post(client, b, "fig.png")
    assert (
        resp.status_code == 201
        and resp.json["file"] == "fig (2).png"
        and resp.json["renamed"] is True
    )
    assert _post(client, c, "fig.png").json["file"] == "fig (3).png"
    assert (root / "fig.png").read_bytes() == a
    assert (root / "fig (2).png").read_bytes() == b
    again = _post(client, b, "fig.png")  # 再拖一次改过名的那张：复用，不再生第四份
    assert again.json["file"] == "fig (2).png" and again.json["reused"] is True
    assert len(_files(root)) == 3


def test_a_users_own_file_is_never_overwritten(project):
    client, root = project
    (root / "fig.png").write_bytes(b"users own bytes")  # 用户已有的、内容不是图的同名文件
    resp = _post(client, _png(), "fig.png")
    assert resp.status_code == 201 and resp.json["file"] == "fig (2).png"
    assert (root / "fig.png").read_bytes() == b"users own bytes"


def test_name_clash_is_case_insensitive(project):
    client, root = project
    _post(client, _png(4, 3), "Fig.png")
    resp = _post(client, _png(5, 3), "fig.png")
    assert resp.json["file"] == "fig (2).png"


def test_raster_beside_a_pdf_of_the_same_stem_is_renamed_so_it_is_not_hidden(project):
    client, root = project
    (root / "fig.pdf").write_bytes(_pdf())
    resp = _post(client, _png(), "fig.png")
    assert resp.status_code == 201 and resp.json["file"] == "fig (2).png"
    assert {"fig.pdf", "fig (2).png"} <= {p["id"] for p in client.get("/api/panels").json["panels"]}


def test_pdf_beside_an_existing_raster_does_not_hide_that_raster(project):
    client, root = project
    (root / "fig.png").write_bytes(_png())
    resp = _post(client, _pdf(), "fig.pdf")
    assert resp.json["file"] == "fig (2).pdf"
    assert {p["id"] for p in client.get("/api/panels").json["panels"]} == {"fig.png", "fig (2).pdf"}


def test_reuse_of_a_hidden_twin_falls_back_to_a_new_name(project):
    client, root = project
    png = _png()
    (root / "fig.png").write_bytes(png)
    (root / "fig.pdf").write_bytes(_pdf())  # 用户自己后来放的 PDF 把 fig.png 让位藏了起来
    resp = _post(client, png, "fig.png")
    assert resp.status_code == 201 and resp.json["file"] == "fig (2).png"
    assert resp.json["id"] in {p["id"] for p in client.get("/api/panels").json["panels"]}


def test_concurrent_imports_never_pick_the_same_name(project):
    client, root = project
    results: list[str] = []

    def go(i: int) -> None:
        results.append(_post(m.app.test_client(), _png(4 + i, 3), "fig.png").json["file"])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 6 and len(_files(root)) == 6


# ---------------------------------------------------------------- 大小 / 写盘失败


def test_over_the_limit_is_refused_without_writing(project, monkeypatch):
    client, root = project
    monkeypatch.setattr(assetimport, "MAX_BYTES", 64)
    resp = _post(client, _png(20, 20), "big.png")
    assert resp.status_code == 413 and resp.json["code"] == "asset_import_too_large"
    assert "limit_mb" in resp.json["params"]
    assert _files(root) == []


def test_limit_is_enforced_while_reading_without_content_length(project, monkeypatch):
    client, root = project
    monkeypatch.setattr(assetimport, "MAX_BYTES", 64)
    resp = client.post(
        "/api/assets/import?name=big.png",
        data=io.BytesIO(_png(20, 20)),
        content_type="application/octet-stream",
        environ_overrides={"wsgi.input_terminated": True, "CONTENT_LENGTH": None},
    )
    assert resp.status_code == 413
    assert _files(root) == []
    # 对照：同样的分块请求在上限之内是收得进来的（只断言「超大的被拒」抓不住把上限挂回 Content-Length 的 bug）
    monkeypatch.setattr(assetimport, "MAX_BYTES", 10 * 1024 * 1024)
    ok = client.post(
        "/api/assets/import?name=ok.png",
        data=io.BytesIO(_png(20, 20)),
        content_type="application/octet-stream",
        environ_overrides={"wsgi.input_terminated": True, "CONTENT_LENGTH": None},
    )
    assert ok.status_code == 201 and (root / "ok.png").read_bytes() == _png(20, 20)


@pytest.mark.parametrize(
    ("err", "status", "code"),
    [
        (errno.ENOSPC, 507, "asset_import_disk_full"),
        (errno.EROFS, 403, "asset_import_not_writable"),
        (errno.EACCES, 403, "asset_import_not_writable"),
        (errno.EIO, 500, "asset_import_write_failed"),
    ],
)
def test_write_failures_have_their_own_codes(project, monkeypatch, err, status, code):
    client, root = project

    def boom(path, data, **kw):
        raise atomicio.AtomicWriteError("write_failed", "x", Path(path)) from OSError(err, "x")

    monkeypatch.setattr(atomicio, "write_bytes", boom)
    resp = _post(client, _png(), "a.png")
    assert (resp.status_code, resp.json["code"]) == (status, code)
    assert _files(root) == []


def test_readonly_directory_is_reported(project):
    client, root = project
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("目录权限位在 Windows / root 下不限制写入")
    root.chmod(0o555)
    try:
        resp = _post(client, _png(), "a.png")
    finally:
        root.chmod(0o755)
    assert resp.status_code == 403 and resp.json["code"] == "asset_import_not_writable"


def test_failed_placement_cleans_the_temp_file(project, monkeypatch):
    client, root = project

    def boom(tmp, dest):
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(assetimport, "place_exclusive", boom)
    resp = _post(client, _png(), "a.png")
    assert resp.status_code == 507
    assert _files(root) == []


def test_not_visible_after_placement_is_rolled_back(project, monkeypatch):
    client, root = project
    real = project_refresh.iter_assets

    def hide_new(root_, **kw):
        return [(p, k) for p, k in real(root_, **kw) if p.name != "a.png"]

    monkeypatch.setattr(project_refresh, "iter_assets", hide_new)
    resp = _post(client, _png(), "a.png")
    assert resp.status_code == 409 and resp.json["code"] == "asset_import_not_visible"
    assert _files(root) == []


# ---------------------------------------------------------------- 项目 / 认证


def test_no_project_open_is_the_usual_no_project_error():
    m.reset_projects()
    resp = _post(m.app.test_client(), _png(), "a.png")
    assert resp.status_code == 409 and resp.json["code"] == "no_project"


def test_unknown_project_id_is_refused(project):
    client, root = project
    resp = client.post(
        "/api/assets/import?name=a.png&pj=not-open",
        data=_png(),
        content_type="application/octet-stream",
    )
    assert resp.status_code == 409
    assert _files(root) == []


def test_import_is_not_reachable_with_get(project):
    client, root = project
    assert client.get("/api/assets/import").status_code in (404, 405)


def test_endpoint_sits_behind_the_session_guard(tmp_path):
    """真服务器：没有会话 cookie 是 401 且不写任何东西；带 bootstrap 出来的 cookie 才写得进去（ADR 0008，无旁路）"""
    import json
    import urllib.error
    import urllib.request

    from werkzeug.serving import make_server

    from tavotto import security
    from tavotto.engine import session_client

    m.reset_projects()
    root = tmp_path / "p"
    root.mkdir()
    m.open_project(str(root))
    srv = make_server("127.0.0.1", 0, m.app, threaded=True)
    port = srv.server_port
    state, nonce = security.new_browser_state(port)
    m.app.config[security.STATE_KEY] = state
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    def post(path, body, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    try:
        octet = {"Content-Type": "application/octet-stream"}
        status, _ = post("/api/assets/import?name=a.png", _png(), octet)
        assert status == 401 and _files(root) == []
        status, headers = post(
            security.BOOTSTRAP_PATH,
            json.dumps({"nonce": nonce}).encode(),
            {"Content-Type": "application/json"},
        )
        assert status == 200
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _ = post("/api/assets/import?name=a.png", _png(), {**octet, "Cookie": cookie})
        assert status == 201 and _files(root) == ["a.png"]
    finally:
        srv.shutdown()
        thread.join(timeout=5)
        m.app.config.pop(security.STATE_KEY, None)
        session_client.remove_secret(port)
        m.reset_projects()
        project_watch.stop()
