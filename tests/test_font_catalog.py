"""本机字体清单（ADR 0169）：列出来的脸都过得了 `userfonts.admit`，列不出来的是选了也嵌不进去的。"""

from __future__ import annotations

import hashlib
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


def _renamed(family: str, *, fs_type: int = 0, covers: str = "") -> bytes:
    """DejaVu（随 matplotlib，OFL，不进 git）改个名字，每个用例都是一张新脸。`covers` 里的字映射到
    字形 A：DejaVu 没有汉字，要让一张脸落在中文层就得给它几个码位。"""
    import io

    from fontTools.ttLib import TTFont

    tt = TTFont(str(DEJAVU))
    tt["name"].setName(family, 1, 3, 1, 0x409)
    tt["name"].setName(family, 1, 1, 0, 0)
    tt["OS/2"].fsType = fs_type
    for table in tt["cmap"].tables:
        if table.isUnicode():
            table.cmap.update({ord(ch): "A" for ch in covers})
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


def _text_obj(text: str, source_font: dict, **over) -> dict:
    return {
        "type": "text",
        "id": "t1",
        "text": text,
        "x_mm": 10.0,
        "y_mm": 10.0,
        "w_mm": 120.0,
        "h_mm": 10.0,
        "size_pt": 12.0,
        "font_family": "serif",
        "source_font": source_font,
        "text_format": "latex",
        **over,
    }


def _compile(tmp_path: Path, monkeypatch, obj: dict):
    from tavotto.rendercore import plan, sources
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    loc = userfonts.FontLocator(tmp_path / "loc.json", system_dirs=lambda: [tmp_path / "fonts"])
    monkeypatch.setattr(userfonts, "_LOCATOR", loc)
    provider = HbFaceProvider(reg)
    compiled = plan.compile_page(
        150.0, 40.0, [obj], sources=sources.StaticSourceResolver(Path(".")), faces=provider
    )
    return compiled, provider


def _exported_faces(tmp_path: Path, compiled, provider) -> list[str]:
    from tavotto.rendercore import pdfwriter

    out = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider)
    return _faces_of(out)


def _sibling(p: Path, *, bold=False, italic=False) -> dict:
    return {"sha256": fonts.sha256_of(p), "face": 0, "bold": bold, "italic": italic}


def test_inline_bold_uses_the_family_bold_face(tmp_path, monkeypatch):
    """`\\textbf{}` 用同族粗体脸，不退到批准字体（ADR 0169 修订）。"""
    regular = _drop(tmp_path, "lab.ttf", _renamed("Lab Serif"))
    bold = _drop(tmp_path, "lab-b.ttf", _renamed("Lab Serif Bold File"))
    sf = {
        "family": "Lab Serif",
        "sha256": fonts.sha256_of(regular),
        "face": 0,
        "siblings": [_sibling(bold, bold=True)],
    }
    compiled, provider = _compile(tmp_path, monkeypatch, _text_obj("Ab \\textbf{Cd}", sf))
    assert not compiled.problems
    assert {sf["sha256"], fonts.sha256_of(bold)} == _used(compiled)
    assert len(_exported_faces(tmp_path, compiled, provider)) == 2


def _runs(compiled) -> list:
    out = []

    def walk(node):
        for child in getattr(node, "children", ()):
            walk(child)
        out.extend(getattr(node, "runs", ()))

    walk(compiled.page)
    return [r for r in out if not r.invisible]


def test_inline_bold_without_a_sibling_is_simulated_on_the_same_face(tmp_path, monkeypatch):
    """同族没有这张脸：模拟，字体不换（ADR 0169 修订，修订 0164 裁决 10）。"""
    regular = _drop(tmp_path, "lab.ttf", _renamed("Lab Serif"))
    sf = {"family": "Lab Serif", "sha256": fonts.sha256_of(regular), "face": 0}
    compiled, provider = _compile(tmp_path, monkeypatch, _text_obj("Ab \\textbf{Cd}", sf))
    assert not compiled.problems
    assert _used(compiled) == {sf["sha256"]}
    assert [r.synth_bold for r in _runs(compiled)] == [False, True]
    assert len(_exported_faces(tmp_path, compiled, provider)) == 1


def test_a_sibling_gone_from_this_machine_warns_and_simulates(tmp_path, monkeypatch):
    regular = _drop(tmp_path, "lab.ttf", _renamed("Lab Serif"))
    gone = _renamed("Lab Serif Bold File")
    sf = {
        "family": "Lab Serif",
        "sha256": fonts.sha256_of(regular),
        "face": 0,
        "siblings": [{"sha256": hashlib.sha256(gone).hexdigest(), "face": 0, "bold": True}],
    }
    compiled, _ = _compile(tmp_path, monkeypatch, _text_obj("Ab \\textbf{Cd}", sf))
    problems = [p for p in compiled.problems if p["code"] == "source_font_unavailable"]
    assert [p["reason"] for p in problems] == ["not_found"]
    assert _used(compiled) == {sf["sha256"]}
    assert _runs(compiled)[-1].synth_bold


def test_an_unused_sibling_file_is_not_read(tmp_path, monkeypatch):
    """用不上的兄弟不入场：缺了也不报，一张中文字体十几 MB，不白读。"""
    regular = _drop(tmp_path, "lab.ttf", _renamed("Lab Serif"))
    gone = hashlib.sha256(b"gone").hexdigest()
    sf = {
        "family": "Lab Serif",
        "sha256": fonts.sha256_of(regular),
        "face": 0,
        "siblings": [{"sha256": gone, "face": 0, "bold": True}],
    }
    compiled, _ = _compile(tmp_path, monkeypatch, _text_obj("Ab", sf))
    assert not compiled.problems


HANZI = "中文粗体"


def _cjk_font_setup(tmp_path: Path) -> dict:
    regular = _drop(tmp_path, "zh.ttf", _renamed("Lab CJK", covers=HANZI))
    bold = _drop(tmp_path, "zh-b.ttf", _renamed("Lab CJK Bold File", covers=HANZI))
    latin = _drop(tmp_path, "lab.ttf", _renamed("Lab Serif"))
    return {
        "family": "Lab Serif",
        "sha256": fonts.sha256_of(latin),
        "face": 0,
        "cjk": {
            "family": "Lab CJK",
            "sha256": fonts.sha256_of(regular),
            "face": 0,
            "siblings": [_sibling(bold, bold=True)],
        },
    }


def _used(compiled) -> set[str]:
    return {r.sha256 for r in compiled.page.resources.values() if hasattr(r, "sha256")}


def test_a_bold_object_uses_the_bold_chinese_face(tmp_path, monkeypatch):
    sf = _cjk_font_setup(tmp_path)
    bold_sha = sf["cjk"]["siblings"][0]["sha256"]
    compiled, _ = _compile(tmp_path, monkeypatch, _text_obj("中文", sf, bold=True))
    assert bold_sha in _used(compiled)
    assert sf["cjk"]["sha256"] not in _used(compiled)


def test_inline_bold_switches_the_chinese_face_too(tmp_path, monkeypatch):
    sf = _cjk_font_setup(tmp_path)
    bold_sha = sf["cjk"]["siblings"][0]["sha256"]
    compiled, _ = _compile(tmp_path, monkeypatch, _text_obj("中文\\textbf{粗体}", sf))
    assert {bold_sha, sf["cjk"]["sha256"]} <= _used(compiled)


def test_chinese_without_bold_is_simulated_like_word(tmp_path, monkeypatch):
    """宋体这类只有常规体的：对象加粗、加斜都模拟，不算问题。"""
    sf = _cjk_font_setup(tmp_path)
    del sf["cjk"]["siblings"]
    obj = _text_obj("中文", sf, bold=True, italic=True)
    compiled, _ = _compile(tmp_path, monkeypatch, obj)
    assert not [p for p in compiled.problems if p["code"] == "source_font_unavailable"]
    assert sf["cjk"]["sha256"] in _used(compiled)
    run = _runs(compiled)[0]
    assert run.synth_bold and run.synth_italic


def test_built_in_chinese_italic_is_simulated_and_bold_is_real(tmp_path, monkeypatch):
    from tavotto.rendercore import plan, sources
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    obj = _text_obj("中文", {}, bold=True, italic=True)
    del obj["source_font"]
    compiled = plan.compile_page(
        150.0,
        40.0,
        [obj],
        sources=sources.StaticSourceResolver(Path(".")),
        faces=HbFaceProvider(reg),
    )
    run = _runs(compiled)[0]
    assert "NotoSerifSC-Bold" in run.font
    assert run.synth_italic and not run.synth_bold


@pytest.mark.parametrize("italic", [False, True])
def test_simulation_reaches_every_writer(tmp_path, monkeypatch, italic):
    """PDF：描边 + 错切，文本层还是原字；SVG 可编辑文字与轮廓、EPS 都带上。"""
    import pikepdf

    from tavotto.rendercore import ir, pdfwriter, svgwriter

    sf = {
        "family": "Lab",
        "sha256": fonts.sha256_of(_drop(tmp_path, "l.ttf", _renamed("Lab"))),
        "face": 0,
    }
    obj = _text_obj("Ab", sf, bold=True, italic=italic)
    compiled, provider = _compile(tmp_path, monkeypatch, obj)
    pdf = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, pdf, provider)
    with pikepdf.open(pdf) as doc:
        stream = doc.pages[0].Contents.read_bytes().decode("latin-1")
    assert "2 Tr" in stream and " RG " in stream
    assert f"{ir.synth_bold_width(12.0):.4f}".rstrip("0") in stream
    assert ("1 0 0.25 1 " in stream) is italic
    for policy, needle in (("text", 'font-weight="700"'), ("outlines", "stroke-width")):
        out = tmp_path / f"o-{policy}.svg"
        svgwriter.write_svg(compiled.page, out, provider, text_policy=policy)
        assert needle in out.read_text(encoding="utf-8")


def test_stroke_width_follows_the_browser_table():
    from tavotto.rendercore import ir

    assert ir.synth_bold_width(9.0) == pytest.approx(9.0 / 24)
    assert ir.synth_bold_width(36.0) == pytest.approx(36.0 / 32)
    assert 20.0 / 32 < ir.synth_bold_width(20.0) < 20.0 / 24


def test_the_preview_bold_chinese_faces_are_the_approved_ones():
    """画布预览写死的两个 sha256 ↔ 批准集合：换字体版本时不同步，预览就静默退回系统字体。"""
    ts = (Path(__file__).parent.parent / "web/src/lib/sourceFontPreview.ts").read_text(
        encoding="utf-8"
    )
    faces = fonts.load_allowlist().faces
    for face_id in ("NotoSansSC-Bold", "NotoSerifSC-Bold"):
        assert faces[face_id].sha256 in ts


def test_the_row_fields_match_the_frontend_types():
    """响应字段 ↔ `web/src/lib/fontCatalog.ts`：改一边另一边读到 undefined，类型检查看不见后端。"""
    ts = (Path(__file__).parent.parent / "web/src/lib/fontCatalog.ts").read_text(encoding="utf-8")
    face = _Face("F", None, "0" * 64, 0, False, False, False, None)
    row = fontcatalog._family_rows([face])[0]
    for key in [*row, *row["faces"][0]]:
        assert f"  {key}: " in ts, key
