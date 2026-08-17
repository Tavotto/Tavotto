"""safe_resolve 越权防护、layout_path 清洗、baked 历史的迁移与截断。"""
import json

import pytest
from werkzeug.exceptions import HTTPException

from magplot import app as m
from magplot.pdfbackend import pymupdf_backend as pb


@pytest.fixture
def figs(tmp_path, monkeypatch):
    (tmp_path / "ok.pdf").write_bytes(b"%PDF-1.4 fake")
    (tmp_path / "evil.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "img.png").write_bytes(b"\x89PNG fake")
    (tmp_path.parent / "outside.pdf").write_bytes(b"%PDF-1.4 fake")
    m.open_project(str(tmp_path))
    return tmp_path


def _code(fn, *args):
    with pytest.raises(HTTPException) as e:
        fn(*args)
    return e.value.code


def test_safe_resolve_ok(figs):
    assert m.safe_resolve("ok.pdf") == figs / "ok.pdf"
    assert m.safe_resolve("sub/img.png") == figs / "sub" / "img.png"


def test_safe_resolve_blocks_traversal(figs):
    assert _code(m.safe_resolve, "../outside.pdf") == 403


def test_safe_resolve_blocks_wrong_ext(figs):
    assert _code(m.safe_resolve, "evil.txt") == 403


def test_safe_resolve_404_on_missing(figs):
    assert _code(m.safe_resolve, "missing.pdf") == 404


def test_layout_path_sanitizes_separators():
    p = m.layout_path("我的布局/../x")
    assert p.parent == m.LAYOUT_DIR
    assert "/" not in p.name and ".." not in p.name
    assert p.suffix == ".json"


def test_layout_path_rejects_empty():
    assert _code(m.layout_path, "") == 400


def test_layout_path_collapses_illegal_chars_to_underscore():
    # 现状：非法字符折叠为 "_"（可能同名碰撞，但不构成路径逃逸）
    assert m.layout_path("###").name == "_.json"


@pytest.fixture
def baked(tmp_path, monkeypatch):
    path = tmp_path / "baked.json"
    monkeypatch.setattr(m, "BAKED_PATH", path)
    return path


def test_load_baked_missing_file(baked):
    assert m.load_baked() == {}


def test_load_baked_migrates_legacy_single_version(baked):
    baked.write_text(json.dumps(
        {"Fig1": {"patches": [{"gid": "g", "prop": "p", "value": 1}],
                  "updated_at": "2026-01-01"}}))
    data = m.load_baked()
    assert data["Fig1"]["versions"] == [
        {"ts": "2026-01-01", "patches": [{"gid": "g", "prop": "p", "value": 1}]}]


def test_append_baked_concurrent_no_lost_updates(baked):
    """threaded Flask 下多线程并发追加，30 条版本一条不丢。"""
    from concurrent.futures import ThreadPoolExecutor

    def add(i: int) -> None:
        m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": i}])

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(add, range(30)))
    versions = m.load_baked()["Fig1"]["versions"]
    assert len(versions) == 30
    assert {v["patches"][0]["value"] for v in versions} == set(range(30))


def test_append_baked_appends_and_trims_to_50(baked):
    for i in range(55):
        m.append_baked("Fig1", [{"gid": "g", "prop": "p", "value": i}])
    versions = m.load_baked()["Fig1"]["versions"]
    assert len(versions) == 50
    assert versions[-1]["patches"][0]["value"] == 54  # 末位 = 当前基线
    assert versions[0]["patches"][0]["value"] == 5


def test_crop_clip_maps_normalized_rect():
    import pymupdf
    src = pymupdf.Rect(0, 0, 100, 200)
    clip = pb._crop_clip(src, {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25})
    assert (clip.x0, clip.y0, clip.x1, clip.y1) == (25, 100, 75, 150)
    assert pb._crop_clip(src, None) is None


def test_hex2rgb():
    assert pb.hex2rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert pb.hex2rgb("#0f0") == (0.0, 1.0, 0.0)
    assert pb.hex2rgb("garbage") == (0.0, 0.0, 0.0)
    assert pb.hex2rgb(None) == (0.0, 0.0, 0.0)
