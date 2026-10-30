"""标注公式的 PDF 文本层（ADR 0152 §七）：公式仍是填充轮廓，但文本层里在公式位置有一个空格字形占位，
整段 ActualText = `$源码$`，PDF 阅读器里选中 / 复制拿到 LaTeX 源码；SVG 不多画任何可见元素。

尺子：文本由 pypdfium2（PDFium，独立读取器）抽回；公式盒是本文件手写的合成排版结果。
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
    assert {g.gid for g in mid.glyphs} == {face.shape(" ")[0].gid}


def test_svg_draws_nothing_new_for_the_text_layer_placeholder(provider, tmp_path):
    math = {typography.math_key(_SRC, "serif"): _BOX}
    with_math = _compile(provider, tmp_path, f"x ${_SRC}$ y", math)
    svg = tmp_path / "o.svg"
    svgwriter.write_svg(with_math.page, svg, provider)
    body = svg.read_text(encoding="utf-8")
    # 文字里不出现 `$` 与源码；占位不留空 <text>；公式只有那一条填充路径
    assert "$" not in body and "frac" not in body
    texts = re.findall(r"<text\b[^>]*>([^<]*)</text>", body)
    assert texts == ["x ", " y"]
    assert body.count("<path") == 1 and "<use" not in body
    # 轮廓策略：占位是空轮廓的空格字形——没有 .notdef 方框
    outline = svgwriter.write_svg(
        with_math.page, tmp_path / "w.svg", provider, text_policy=svgwriter.TEXT_POLICY_OUTLINES
    )
    assert outline.notdef_codes == 0


def test_no_space_glyph_falls_back_to_no_text_layer(provider, tmp_path):
    """这张脸没有空格字形（gid 0）：不画 .notdef 方框，文字层照旧没有这个公式。"""
    face = provider.face_for("serif", False, False)

    class NoSpace:
        def __init__(self, inner):
            self._i = inner

        def __getattr__(self, k):
            return getattr(self._i, k)

        def shape(self, text):
            if text == " ":
                from tavotto.rendercore.ir import Glyph

                return (Glyph(0, " ", 300),)
            return self._i.shape(text)

    assert typography._math_text_piece(NoSpace(face), _SRC, 20.0, 10.0) is None
    assert typography._math_text_piece(face, _SRC, 20.0, 10.0) is not None
