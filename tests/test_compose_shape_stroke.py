"""描边属性补齐的端到端（ADR 0174）：经契约层合成进真实 PDF，再从内容流 / 资源里读回来。

无边框 = 内容流里这个形状只有填充指令；描边不透明度 = ExtGState 的 `/CA`；线端 / 拐角 = `J` / `j` 指令；
线型 = `d` 指令。导出 PDF 之外，SVG 与 PNG 走同一份 IR，这里再看 SVG 里没有 stroke 属性。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tavotto import pdfbackend

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.fixture(scope="module", autouse=True)
def _fonts_ready():
    from tavotto.rendercore import fonts

    if fonts.FontRegistry.discover().missing:
        pytest.skip("批准字体不全（not_run）；先跑 scripts/fetch_fonts.py")


def _pdf(o: dict) -> tuple[bytes, str]:
    """合成一页只含这一个对象的 PDF → (整份字节, 内容流文本)。"""
    out = Path(tempfile.mkdtemp(prefix="compose-stroke-")) / "a.pdf"
    canvas = pdfbackend.compose(60.0, 40.0)
    try:
        canvas.place(
            {"id": "o", "x_mm": 10, "y_mm": 10, "w_mm": 30, "h_mm": 20, "color": "#336699", **o},
            150,
            lambda x, d: None,
        )
        canvas.save_pdf(out)
    finally:
        canvas.close()
    raw = out.read_bytes()
    _head, content = pdfread.page(pdfread.objects(raw))
    return raw, content.decode("latin-1")


RECT = {"type": "shape", "shape": "rect", "stroke_pt": 2.0, "fill": "#ffcc00"}


def _paint_ops(content: str) -> list[str]:
    """内容流里的路径绘制指令（令牌流；整页白底那一条之后）。"""
    return [t for t in content.split() if t in {"f", "f*", "S", "s", "B", "B*", "b", "b*"}][1:]


def test_a_rect_with_a_stroke_is_filled_and_stroked():
    _raw, content = _pdf(RECT)
    assert _paint_ops(content) == ["B"]


def test_no_stroke_rect_is_fill_only_in_the_pdf():
    _raw, content = _pdf({**RECT, "stroke": False})
    assert _paint_ops(content) == ["f"]


def test_no_stroke_and_no_fill_shape_draws_nothing_and_does_not_fail():
    _raw, content = _pdf({**RECT, "stroke": False, "fill": None})
    assert _paint_ops(content) == []


def test_stroke_opacity_becomes_the_stroke_alpha_of_an_extgstate():
    raw, _content = _pdf({**RECT, "stroke_opacity": 0.4})
    assert re.search(rb"/CA 0\.4", raw)
    raw0, _c0 = _pdf(RECT)
    assert b"/CA " not in raw0  # 缺省：不出任何透明度资源


def test_cap_join_and_dash_operators():
    _raw, content = _pdf({**RECT, "line_cap": "butt", "line_join": "bevel", "dash": "dashdot"})
    assert " 0 J " in content and " 2 j " in content
    assert re.search(r"\[8 5 2 5\] 0 d", content)
    _raw, legacy = _pdf(RECT)
    assert " 1 J " in legacy and " 1 j " in legacy and " d " not in legacy


def test_dotted_stays_round_capped_even_when_the_cap_says_flat():
    _raw, content = _pdf({**RECT, "line_cap": "butt", "dash": "dotted"})
    assert " 1 J " in content


def test_new_arrow_head_kinds_compose_and_a_dot_is_a_filled_curve():
    o = {
        "type": "arrow",
        "stroke_pt": 2.0,
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "head_start": "stealth",
        "head_end": "dot",
        "head_size_end": "large",
    }
    _raw, content = _pdf(o)
    ops = _paint_ops(content)
    assert ops.count("f") == 2 and ops.count("S") == 1  # 两个实心端型 + 一条杆
    assert " c " in content  # 圆点是贝塞尔曲线


def test_svg_export_of_a_no_stroke_shape_has_no_stroke_attribute(tmp_path):
    from tavotto.rendercore import fonts, geometry, ir, svgwriter
    from tavotto.rendercore.hbshaper import HbFaceProvider

    [p] = geometry.shape_paths({**RECT, "stroke": False, "id": "o"}, 40.0, 20.0)
    page = ir.validate(ir.Page(60.0, 40.0, (p,), {}, None))
    out = tmp_path / "a.svg"
    svgwriter.write_svg(page, out, HbFaceProvider(fonts.FontRegistry.discover()))
    text = out.read_text(encoding="utf-8")
    assert "<path" in text and 'stroke="none"' in text and "stroke-width" not in text
    [q] = geometry.shape_paths({**RECT, "id": "o"}, 40.0, 20.0)
    out2 = tmp_path / "b.svg"
    svgwriter.write_svg(
        ir.validate(ir.Page(60.0, 40.0, (q,), {}, None)),
        out2,
        HbFaceProvider(fonts.FontRegistry.discover()),
    )
    assert "stroke-width" in out2.read_text(encoding="utf-8")


@pytest.mark.parametrize("dash", [None, "dashed"])
@pytest.mark.parametrize("head", ["diamond", "dot", "stealth", "triangle", "open"])
def test_translucent_arrow_does_not_darken_where_the_shaft_overlaps_the_head(tmp_path, head, dash):
    """显示与导出一致的判据（逐像素）：半透明箭头里，杆伸进端型的重叠处与端型单独的地方一样深。
    各路径自带 alpha 的旧做法会在重叠处叠深（0.4 → 0.64）。"""
    from PIL import Image

    out = tmp_path / "a.png"
    canvas = pdfbackend.compose(60.0, 20.0)
    try:
        canvas.place(
            {
                "id": "o",
                "type": "arrow",
                "x_mm": 5,
                "y_mm": 8,
                "w_mm": 50,
                "h_mm": 0,
                "stroke_pt": 6.0,
                "color": "#000000",
                "start": {"rx": 0, "ry": 0},
                "end": {"rx": 1, "ry": 0},
                "head_start": "none",
                "head_end": head,
                **({"dash": dash} if dash else {}),
                "head_size_end": "large",
                "stroke_opacity": 0.4,
            },
            300,
            lambda x, d: None,
        )
        canvas.save_png(out, 300)
    finally:
        canvas.close()
    im = Image.open(out).convert("L")
    w, h = im.size
    row = h * 8 // 20  # 箭杆所在的像素行
    vals = [im.getpixel((x, row)) for x in range(w)]
    darkest = min(vals)
    # 单层 0.4 黑在白底上 = 255 × 0.6 = 153；叠深会到 ≈ 92
    assert darkest >= 150, darkest
    assert any(140 <= v <= 160 for v in vals)
