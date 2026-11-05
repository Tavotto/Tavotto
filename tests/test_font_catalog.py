"""本机字体清单（ADR 0169）：列出来的脸都过得了 `userfonts.admit`，列不出来的是选了也嵌不进去的。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.rendercore import fontcatalog, fonts, userfonts
from tavotto.rendercore.fontcatalog import _Face

pytest.importorskip("fontTools")
mpl_font_manager = pytest.importorskip("matplotlib.font_manager")

DEJAVU = Path(
    str(mpl_font_manager.findfont(mpl_font_manager.FontProperties(family="DejaVu Serif")))
)


def _catalog(tmp_path: Path, monkeypatch) -> fontcatalog.FontCatalog:
    loc = userfonts.FontLocator(None, system_dirs=lambda: [tmp_path / "fonts"])
    monkeypatch.setattr(fontcatalog, "system_font_dirs", lambda: [tmp_path / "fonts"])
    return fontcatalog.FontCatalog(tmp_path / "cat.json", loc)


def _drop(tmp_path: Path, name: str, data: bytes) -> Path:
    d = tmp_path / "fonts"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_bytes(data)
    return p


def _renamed(family: str, *, fs_type: int = 0) -> bytes:
    """DejaVu（随 matplotlib，OFL，不进 git）改个名字，每个用例都是一张新脸。"""
    import io

    from fontTools.ttLib import TTFont

    tt = TTFont(str(DEJAVU))
    tt["name"].setName(family, 1, 3, 1, 0x409)
    tt["name"].setName(family, 1, 1, 0, 0)
    tt["OS/2"].fsType = fs_type
    buf = io.BytesIO()
    tt.save(buf)
    return buf.getvalue()


def test_a_listed_face_is_one_admit_accepts(tmp_path, monkeypatch):
    p = _drop(tmp_path, "a.ttf", _renamed("Lab Serif"))
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    rows = {f["name"]: f for f in fontcatalog._family_rows(cat._files[str(p)][2])}
    face = rows["Lab Serif"]["faces"][0]
    ref = userfonts.FaceRef("Lab Serif", face["sha256"], face["face"])
    assert userfonts.admit(ref, locator=cat._locator).path == p
    assert rows["Lab Serif"]["generic"] == "serif"
    assert rows["Lab Serif"]["cjk"] is False


def test_a_face_that_forbids_embedding_is_not_listed(tmp_path, monkeypatch):
    p = _drop(tmp_path, "r.ttf", _renamed("Restricted", fs_type=2))
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    assert cat._files[str(p)][2] == []


def test_the_approved_faces_are_not_listed_twice(tmp_path, monkeypatch):
    p = _drop(tmp_path, "l.ttf", _renamed("Pretend Approved"))
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    assert [f["name"] for f in cat.snapshot()["families"]] == ["Pretend Approved"]
    allow = fonts.load_allowlist()
    pretend = fonts.sha256_of(p)
    stub = SimpleNamespace(
        faces=allow.faces, by_sha256=lambda: {pretend: next(iter(allow.faces.values()))}
    )
    monkeypatch.setattr(fonts, "load_allowlist", lambda: stub)
    snap = cat.snapshot()
    assert snap["families"] == []
    assert snap["builtin"]["serif"]["name"] == "Liberation Serif"
    assert len(snap["builtin"]["serif"]["faces"]) == 4
    assert {"serif", "sans-serif", "monospace"} == set(snap["builtin"])


def test_an_unchanged_file_is_not_read_again(tmp_path, monkeypatch):
    _drop(tmp_path, "a.ttf", _renamed("Lab Serif"))
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    fresh = fontcatalog.FontCatalog(tmp_path / "cat.json", cat._locator)
    fresh._load()
    monkeypatch.setattr(fontcatalog, "faces_in", lambda *a: pytest.fail("文件没变不该重读"))
    fresh.scan()
    assert len(fresh._files) == 1


def test_the_response_and_family_rows_carry_no_path(tmp_path, monkeypatch):
    _drop(tmp_path, "a.ttf", _renamed("Lab Serif"))
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    assert str(tmp_path) not in json.dumps(cat.snapshot())


def test_a_style_keeps_one_face_per_family(tmp_path, monkeypatch):
    _drop(tmp_path, "a.ttf", _renamed("Lab Serif"))
    _drop(tmp_path, "b.ttf", _renamed("Lab Serif") + b"\0")
    cat = _catalog(tmp_path, monkeypatch)
    cat.scan()
    rows = cat.snapshot()["families"]
    assert [len(r["faces"]) for r in rows] == [1]


def _faces_of(path: Path) -> list[str]:
    import pikepdf

    pdf = pikepdf.open(path)
    return [str(f.BaseFont) for _, f in pdf.pages[0].Resources.Font.items()]


def test_a_chinese_font_with_built_in_english_exports_both_faces(tmp_path, monkeypatch):
    """中文格选本机字体、英文留在内置：`source_font` 的正文脸点名批准字体，导出两张脸都嵌进去、不报问题。"""
    from tavotto.rendercore import pdfwriter, plan, sources
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    p = _drop(tmp_path, "zh.ttf", _renamed("Lab CJK"))
    loc = userfonts.FontLocator(tmp_path / "loc.json", system_dirs=lambda: [tmp_path / "fonts"])
    loc.add_hint_dirs([fonts.fonts_dir()])
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    serif = fontcatalog.builtin_rows()["serif"]
    regular = next(f for f in serif["faces"] if not f["bold"] and not f["italic"])
    obj = {
        "type": "text",
        "id": "t1",
        "text": "Ab",
        "x_mm": 10.0,
        "y_mm": 10.0,
        "w_mm": 120.0,
        "h_mm": 10.0,
        "size_pt": 12.0,
        "font_family": "serif",
        "source_font": {
            "family": serif["name"],
            "sha256": regular["sha256"],
            "face": 0,
            "cjk": {"family": "Lab CJK", "sha256": fonts.sha256_of(p), "face": 0},
        },
    }
    provider = HbFaceProvider(reg)
    compiled = plan.compile_page(
        150.0, 40.0, [obj], sources=sources.StaticSourceResolver(Path(".")), faces=provider
    )
    assert not compiled.problems
    out = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider)
    assert any("LiberationSerif" in n for n in _faces_of(out))


def test_the_row_fields_match_the_frontend_types():
    """响应字段 ↔ `web/src/lib/fontCatalog.ts`：改一边另一边读到 undefined，类型检查看不见后端。"""
    ts = (Path(__file__).parent.parent / "web/src/lib/fontCatalog.ts").read_text(encoding="utf-8")
    face = _Face("F", None, "0" * 64, 0, False, False, False, None)
    row = fontcatalog._family_rows([face])[0]
    for key in [*row, *row["faces"][0]]:
        assert f"  {key}: " in ts, key
