"""标注公式的 PDF 文本层（ADR 0152 §七）：公式仍是填充轮廓；文本层里在公式位置放源码 `$…$` 本身的字形，
**不画**（文字渲染模式 3）、压到公式宽度，外面再包一层整段 ActualText——PDF 阅读器里选中 / 复制拿到
LaTeX 源码；SVG 与画面不多画任何东西。

两层各自要被证明：字形层（macOS PDFKit 不读 ActualText，只认字形）用「剥掉 ActualText 后仍抽得回源码」量；
ActualText 层（poppler 对挤在一起的窄字形会按位置重排）用它出现在内容流里量。

尺子：文本由 pypdfium2（PDFium，独立读取器）抽回、画面由它渲染；公式盒是本文件手写的合成排版结果。
跑的前提：`tavotto[rendercore]` 候选包 + 批准字体，缺则 skip（not_run）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, ir, pdfwriter, plan, sources, svgwriter, typography

_BOX = {
    "ok": True,
    "width": 2.0,
    "ascent": 0.8,
    "descent": 0.2,
    "segments": [["M", 0.0, -0.2], ["L", 2.0, -0.2], ["L", 2.0, 0.8], ["Z"]],
}
_SRC = "\\frac{a}{b}"


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz", "pypdfium2")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"候选包未装（not_run）：{exc}")
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    return HbFaceProvider(reg)


def _text(text: str) -> dict:
    return {
        "type": "text",
        "id": "t1",
        "text": text,
        "x_mm": 5.0,
        "y_mm": 5.0,
        "w_mm": 80.0,
        "h_mm": 8.0,
        "size_pt": 10.0,
        "text_format": "latex",
    }


def _compile(provider, tmp_path: Path, text: str, math: dict | None):
    return plan.compile_page(
        100.0,
        40.0,
        [_text(text)],
        sources=sources.StaticSourceResolver(tmp_path),
        faces=provider,
        math=math,
    )


def _pdf_text(path: Path) -> str:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[0]
        tp = page.get_textpage()
        text = tp.get_text_range()
        tp.close()
        page.close()
        return text
    finally:
        doc.close()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def test_pdf_text_layer_carries_the_formula_source_in_reading_order(provider, tmp_path):
    math = {typography.math_key(_SRC, "serif"): _BOX}
    compiled = _compile(provider, tmp_path, f"x ${_SRC}$ y", math)
    out = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider)
    assert _norm(_pdf_text(out)) == f"x ${_SRC}$ y"
    # 公式的轮廓仍在；文字不再在公式处断成两段（占位字形撑开了宽度）
    grp = compiled.page.children[0]
    assert sum(isinstance(n, ir.Path) for n in grp.children) == 1
    assert sum(isinstance(n, ir.ShapedText) for n in grp.children) == 1
    # 没有公式排版结果时（缩略图等）：行为不变，文字层是源码本身
    plain = _compile(provider, tmp_path, f"x ${_SRC}$ y", None)
    out2 = tmp_path / "p.pdf"
    pdfwriter.write_pdf(plain.page, out2, provider)
    assert _norm(_pdf_text(out2)) == f"x ${_SRC}$ y"


def test_text_after_the_formula_starts_where_the_outline_ends(provider, tmp_path):
    math = {typography.math_key(_SRC, "serif"): _BOX}
    compiled = _compile(provider, tmp_path, f"x ${_SRC}$ y", math)
    grp = compiled.page.children[0]
    st = next(n for n in grp.children if isinstance(n, ir.ShapedText))
    path = next(n for n in grp.children if isinstance(n, ir.Path))
    fx = path.segments[0][1]  # 公式左缘
    mid = next(r for r in st.runs if r.actual_text == f"${_SRC}$")
    face = provider.face_for("serif", False, False)
    pen = st.x
    for r in st.runs:
        if r is mid:
            break
        pen += sum(g.x_advance for g in r.glyphs) * r.size / face.resource.units_per_em
    assert pen == pytest.approx(fx, abs=1e-6)
    after = pen + sum(g.x_advance for g in mid.glyphs) * mid.size / face.resource.units_per_em
    assert after == pytest.approx(fx + 20.0, abs=0.01)  # 2 em × 10 pt，取整到字体单位
    assert mid.invisible
    assert "".join(g.text for g in mid.glyphs) == f"${_SRC}$"


def test_the_glyph_layer_alone_carries_the_source_and_draws_nothing(provider, tmp_path):
    """macOS PDFKit 不读 ActualText：剥掉 ActualText 之后字形本身仍抽得回源码；渲染模式 3，画面上一个像素都不多。"""
    import dataclasses

    import pikepdf
    import pypdfium2 as pdfium

    math = {typography.math_key(_SRC, "serif"): _BOX}
    compiled = _compile(provider, tmp_path, f"x ${_SRC}$ y", math)
    out = tmp_path / "o.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider)
    with pikepdf.open(out) as pdf:
        page = pdf.pages[0]
        data = page.Contents.read_bytes()
        assert b"3 Tr" in data and b"/ActualText" in data
        page.Contents = pdf.make_stream(
            re.sub(rb"/Span <</ActualText <[0-9A-F]+>>> BDC|EMC", b"", data)
        )
        bare = tmp_path / "bare.pdf"
        pdf.save(bare)
    assert _norm(_pdf_text(bare)) == f"x ${_SRC}$ y"

    # 同一页把不可见那段换成一个等宽的空格字形（什么都不画、后面的字落位不变）：画面逐像素相同
    space = provider.face_for("serif", False, False).shape(" ")[0].gid

    def blank(run):
        if not run.invisible:
            return run
        total = sum(g.x_advance for g in run.glyphs)
        return ir.GlyphRun(run.font, run.size, (ir.Glyph(space, " ", total),), run.rise)

    def swap(node):
        if isinstance(node, ir.ShapedText):
            return dataclasses.replace(node, runs=tuple(blank(r) for r in node.runs))
        if isinstance(node, ir.Group):
            return dataclasses.replace(node, children=tuple(swap(c) for c in node.children))
        return node

    blanked = dataclasses.replace(
        compiled.page, children=tuple(swap(c) for c in compiled.page.children)
    )
    out2 = tmp_path / "s.pdf"
    pdfwriter.write_pdf(blanked, out2, provider)

    def pixels(path):
        doc = pdfium.PdfDocument(str(path))
        try:
            return doc[0].render(scale=4).to_pil().convert("L").tobytes()
        finally:
            doc.close()

    assert pixels(out) == pixels(out2)


def test_svg_draws_nothing_new_for_the_text_layer_placeholder(provider, tmp_path):
    math = {typography.math_key(_SRC, "serif"): _BOX}
    with_math = _compile(provider, tmp_path, f"x ${_SRC}$ y", math)
    svg = tmp_path / "o.svg"
    svgwriter.write_svg(with_math.page, svg, provider)
    body = svg.read_text(encoding="utf-8")
    # 文字里不出现 `$` 与源码；不可见那段不留 <text>；公式只有那一条填充路径
    assert "$" not in body and "frac" not in body
    texts = re.findall(r"<text\b[^>]*>([^<]*)</text>", body)
    assert texts == ["x ", " y"]
    assert body.count("<path") == 1 and "<use" not in body
    # 轮廓策略：不可见那段一个字形都不描（只描 x、空格、y 这些看得见的段）
    outline = svgwriter.write_svg(
        with_math.page, tmp_path / "w.svg", provider, text_policy=svgwriter.TEXT_POLICY_OUTLINES
    )
    grp = with_math.page.children[0]
    visible = sum(
        len(r.glyphs)
        for n in grp.children
        if isinstance(n, ir.ShapedText)
        for r in n.runs
        if not r.invisible
    )
    assert outline.outlined_glyphs == visible
    assert outline.notdef_codes == 0


def test_a_face_that_cannot_draw_the_source_gives_no_text_layer(provider, tmp_path):
    """源码里有这张脸画不了的字（gid 0）：文本层不给这个公式，不在里面放 .notdef。"""
    face = provider.face_for("serif", False, False)

    class NoBackslash:
        def __init__(self, inner):
            self._i = inner

        def __getattr__(self, k):
            return getattr(self._i, k)

        def shape(self, text):
            from tavotto.rendercore.ir import Glyph

            return tuple(
                Glyph(0, g.text, g.x_advance) if g.text == "\\" else g for g in self._i.shape(text)
            )

    assert typography._math_text_piece(NoBackslash(face), _SRC, 20.0, 10.0) is None
    assert typography._math_text_piece(face, _SRC, 20.0, 10.0) is not None
