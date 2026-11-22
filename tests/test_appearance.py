"""填充与描边补齐（ADR 0185 第二批）的后端一侧：共享向量对拍；经契约层合成 PDF 读回 `d` / `M` 指令与描边对齐后的几何；
SVG 的 `stroke-dasharray` / `stroke-miterlimit`、EPS 的 `setdash` / `setmiterlimit`；路径的填充规则。"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tavotto import pdfbackend
from tavotto.rendercore import geometry, plan

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

VEC = json.loads((ROOT / "golden" / "appearance_vectors.json").read_text(encoding="utf-8"))
HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
needs_rc = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.mark.parametrize("v", VEC["dash"], ids=lambda v: repr(v["in"])[:30])
def test_valid_dash_pattern_matches_the_vectors(v):
    got = geometry.valid_dash_pattern(v["in"])
    assert (list(got) if got else None) == v["out"]


@pytest.mark.parametrize("v", VEC["miter"], ids=lambda v: repr(v["in"]))
def test_miter_limit_matches_the_vectors(v):
    assert geometry.miter_limit(v["in"]) == v["out"]


@pytest.mark.parametrize("v", VEC["inset"], ids=lambda v: repr(v["in"]))
def test_shape_inset_matches_the_vectors(v):
    assert geometry.shape_inset(v["in"], v["sw"]) == v["out"]


@pytest.mark.parametrize("v", VEC["stroke"], ids=lambda v: repr(v["in"])[:40])
def test_effective_stroke_matches_the_vectors(v):
    s = geometry._stroke({"color": "#000000", **v["in"]}, float(v["in"]["stroke_pt"]), join="round")
    assert (
        list(s.dash) == pytest.approx(v["dash"])
        and s.cap == v["cap"]
        and s.miter_limit == v["miter_limit"]
    )


def test_legacy_shapes_are_unchanged_by_the_new_fields():
    """没有新字段的旧对象：内缩、虚线、斜接上限与加属性之前一样（旧文档逐字不变的另一半在 test_shape_stroke_props）。"""
    o = {"shape": "rect", "stroke_pt": 2.0, "color": "#000000", "fill": "#ffffff", "dash": "dashed"}
    [p] = geometry.shape_paths(o, 50.0, 30.0)
    assert p.segments[0] == ("M", 1.0, 1.0)
    assert p.stroke.dash == (8.0, 5.0) and p.stroke.miter_limit == 10.0


def test_stroke_align_moves_the_rect_outline():
    base = {"shape": "rect", "stroke_pt": 2.0, "color": "#000000", "fill": None}
    pts = {
        a: geometry.shape_paths({**base, **({"stroke_align": a} if a else {})}, 50.0, 30.0)[
            0
        ].segments[0]
        for a in (None, "center", "outside")
    }
    assert pts[None] == ("M", 1.0, 1.0)  # 内侧：外沿贴框
    assert pts["center"] == ("M", 0.0, 0.0)  # 居中：框边是线的中线
    assert pts["outside"] == ("M", -1.0, -1.0)  # 外侧：整个在框外


def test_path_dash_pattern_and_miter_limit_reach_the_stroke():
    o = {
        "stroke_pt": 1.0,
        "color": "#000000",
        "fill": None,
        "dash_pattern": [6, 2],
        "miter_limit": 2.5,
        "subpaths": [{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 1]}]}],
    }
    [p] = geometry.path_paths(o, 40.0, 40.0)
    assert p.stroke.dash == (6.0, 2.0) and p.stroke.miter_limit == 2.5


def _pdf(o: dict) -> str:
    out = Path(tempfile.mkdtemp(prefix="compose-appearance-")) / "a.pdf"
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
    _head, content = pdfread.page(pdfread.objects(out.read_bytes()))
    return content.decode("latin-1")


@needs_rc
def test_pdf_has_the_custom_dash_and_the_miter_limit():
    c = _pdf(
        {
            "type": "shape",
            "shape": "triangle",
            "stroke_pt": 2.0,
            "fill": None,
            "dash_pattern": [4, 1.5],
            "miter_limit": 3,
            "line_join": "miter",
        }
    )
    assert re.search(r"\[4 1\.5\] 0 d", c)
    assert re.search(r" 3 M\b", c)
    legacy = _pdf({"type": "shape", "shape": "triangle", "stroke_pt": 2.0, "fill": None})
    assert re.search(r" 10 M\b", legacy) and " d " not in legacy


@needs_rc
def test_pdf_stroke_align_outside_grows_the_rect():
    """外侧对齐：矩形画在框外半个线宽处（`re` 或 m/l 的第一个点比框原点再往外半个线宽）。"""
    sw = 2.0
    inside = _pdf({"type": "shape", "shape": "rect", "stroke_pt": sw, "fill": None})
    outside = _pdf(
        {"type": "shape", "shape": "rect", "stroke_pt": sw, "fill": None, "stroke_align": "outside"}
    )

    def first_move(c: str) -> tuple[float, float]:
        m = re.findall(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?) m", c)
        return float(m[-1][0]), float(m[-1][1])

    xi, _ = first_move(inside)
    xo, _ = first_move(outside)
    assert xi - xo == pytest.approx(sw, abs=1e-6)  # 内侧 +sw/2、外侧 −sw/2：差一个线宽


def _page(o: dict):
    return plan.compile_page(60.0, 40.0, [o], sources=None, faces=None).page  # type: ignore[arg-type]


PATH = {
    "type": "path",
    "id": "p",
    "x_mm": 5,
    "y_mm": 5,
    "w_mm": 30,
    "h_mm": 20,
    "stroke_pt": 1.5,
    "color": "#224466",
    "fill": "#aaccee",
    "fill_rule": "evenodd",
    "dash_pattern": [3, 2],
    "miter_limit": 4,
    "subpaths": [
        {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]},
        {
            "closed": True,
            "nodes": [{"p": [0.3, 0.3]}, {"p": [0.7, 0.3]}, {"p": [0.7, 0.7]}, {"p": [0.3, 0.7]}],
        },
    ],
}


@needs_rc
def test_svg_export_has_dasharray_miterlimit_and_fill_rule(tmp_path):
    from tavotto.rendercore import fonts, svgwriter
    from tavotto.rendercore.hbshaper import HbFaceProvider

    out = tmp_path / "p.svg"
    svgwriter.write_svg(_page(PATH), out, HbFaceProvider(fonts.FontRegistry.discover()))
    text = out.read_text(encoding="utf-8")
    assert 'stroke-dasharray="3 2"' in text
    assert 'stroke-miterlimit="4"' in text
    assert 'fill-rule="evenodd"' in text


@needs_rc
def test_eps_export_has_setdash_and_setmiterlimit():
    from tavotto.rendercore import epswriter, fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    ps, _ = epswriter.overlay_ps(_page(PATH), HbFaceProvider(fonts.FontRegistry.discover()))
    assert "[3 2] 0 setdash" in ps
    assert "4 setmiterlimit" in ps
    assert "eofill" in ps
