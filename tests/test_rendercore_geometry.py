"""形状 / 箭头 → 路径（统一实施包 U06，ADR 0059）。

两条严格同源对（`web/src/lib/shapeGeometry.ts` ↔ `rendercore/geometry.py` 的 `polygon_points` /
`dash_pattern`）由**共享向量** `tests/golden/shape_geometry_vectors.json` 接住：数字是退役前从旧 facade
（`_polygon_points` / `_dash_pattern`，当年的权威）记下的，pytest 与 vitest（`shapeGeometry.golden.test.ts`）
各跑一遍同一份（U10，ADR 0072；U08 之前这里直接拿旧实现当 oracle）。其余形状只钉几何合同：描边内缩
半线宽、箭头帽的长宽与回缩、line 的缺省端点。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import geometry, ir

VECTORS = json.loads(
    (Path(__file__).resolve().parent / "golden" / "shape_geometry_vectors.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("vec", VECTORS["polygon"], ids=lambda v: f"{v['sides']}@{v['w']}x{v['h']}")
def test_polygon_points_match_the_shared_vectors_exactly(vec):
    got = geometry.polygon_points(vec["sides"], vec["w"], vec["h"], vec["inset"])
    assert [[round(x, 12), round(y, 12)] for x, y in got] == vec["points"]


@pytest.mark.parametrize("vec", VECTORS["dash"], ids=lambda v: f"{v['dash']}@{v['stroke_pt']}")
def test_dash_pattern_numbers_match_the_shared_vectors(vec):
    mine = list(geometry.dash_pattern(vec["dash"], vec["stroke_pt"]))
    assert mine == pytest.approx(vec["pattern"], abs=1e-3)


def test_the_shared_vectors_cover_the_edge_cases_the_formula_clamps():
    """向量集不是随手抄的：边数 2 / 13 要被夹到 3 / 12，dotted 在细线上要碰到 0.01 pt 下限（`pdf_floor`）。"""
    sides = {v["sides"] for v in VECTORS["polygon"]}
    assert {2, 13} <= sides and len({len(v["points"]) for v in VECTORS["polygon"]}) >= 4
    assert any(v["pdf_floor"] for v in VECTORS["dash"])
    assert all(v["pattern"] == [] for v in VECTORS["dash"] if v["dash"] in (None, "solid"))


def test_rect_stroke_is_inset_by_half_the_line_width():
    [p] = geometry.shape_paths({"shape": "rect", "stroke_pt": 2.0}, 40.0, 20.0)
    assert p.segments[0] == ("M", 1.0, 1.0)
    assert p.segments[2] == ("L", 39.0, 19.0)
    assert p.stroke is not None and p.stroke.width == 2.0 and p.stroke.cap == "round"
    assert p.fill is None


def test_rounded_rect_radius_is_relative_to_the_shorter_edge_and_capped_at_half():
    [p] = geometry.shape_paths(
        {"shape": "rect", "stroke_pt": 1.0, "corner_radius_mm": 100.0}, 40.0, 20.0
    )
    # 内缩后 39 × 19，半径封顶 9.5 → 第一段从 (0.5 + 9.5, 0.5) 起
    assert p.segments[0] == ("M", 10.0, 0.5)
    assert sum(1 for s in p.segments if s[0] == "C") == 4


def test_ellipse_is_four_beziers_inscribed_in_the_inset_box():
    [p] = geometry.shape_paths(
        {"shape": "ellipse", "stroke_pt": 1.0, "fill": "#0000ff"}, 40.0, 20.0
    )
    xs = [v for s in p.segments if s[0] in ("M", "C") for v in s[1::2]]
    ys = [v for s in p.segments if s[0] in ("M", "C") for v in s[2::2]]
    assert (min(xs), max(xs)) == (0.5, 39.5)
    assert (min(ys), max(ys)) == (0.5, 19.5)
    assert p.fill == ir.Paint((0.0, 0.0, 1.0), 1.0)


def test_fill_opacity_lands_on_the_fill_paint_not_the_stroke():
    [p] = geometry.shape_paths(
        {"shape": "triangle", "fill": "#ff0000", "fill_opacity": 0.3, "color": "#00ff00"},
        10.0,
        10.0,
    )
    assert p.fill == ir.Paint((1.0, 0.0, 0.0), 0.3)
    assert p.stroke is not None and p.stroke.paint == ir.Paint((0.0, 1.0, 0.0), 1.0)


def test_zero_fill_opacity_is_zero_not_missing():
    """`fill_opacity: 0` 是合法取值（完全透明的填充），不许被当成「没设」→ 1.0。旧 facade 有这个 bug，
    RenderCore 不照抄（U08 对拍时这是有意的差异）。"""
    [p] = geometry.shape_paths({"shape": "rect", "fill": "#ff0000", "fill_opacity": 0}, 10.0, 10.0)
    assert p.fill == ir.Paint((1.0, 0.0, 0.0), 0.0)
    [q] = geometry.shape_paths({"shape": "rect", "fill": "#ff0000"}, 10.0, 10.0)
    assert q.fill == ir.Paint((1.0, 0.0, 0.0), 1.0)


def test_line_defaults_to_the_horizontal_midline():
    [p] = geometry.shape_paths({"shape": "line"}, 40.0, 20.0)
    assert p.segments == (("M", 0.0, 10.0), ("L", 40.0, 10.0))
    assert p.fill is None and p.stroke is not None


def test_brace_uses_the_old_curve_construction():
    [p] = geometry.shape_paths({"shape": "brace", "stroke_pt": 1.0}, 10.0, 40.0)
    kinds = [s[0] for s in p.segments]
    assert kinds == ["M", "C", "L", "C", "C", "L", "C"]
    # 第一段曲线：p1=(9.5,0.5) 控制点 (5,0.5) 终点 (5,10)；k1 = p1 + (p2−p1)·κ
    c = p.segments[1]
    assert c[1] == pytest.approx(9.5 + (5 - 9.5) * geometry.KAPPA)
    assert c[2] == pytest.approx(0.5)
    assert (c[5], c[6]) == (5.0, 10.0)


def test_arrow_head_geometry_is_the_front_end_contract():
    """帽长 4×线宽、帽半宽 1.7×线宽、triangle 端线段回缩 0.75×帽长。水平箭头 (0,5)→(40,5)，线宽 2。"""
    o = {
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "stroke_pt": 2.0,
        "head_end": "triangle",
    }
    shaft, head = geometry.arrow_paths(o, 40.0, 10.0)
    assert shaft.segments == (("M", 0.0, 5.0), ("L", 40.0 - 8.0 * 0.75, 5.0))
    assert shaft.stroke is not None and shaft.stroke.cap == "round" and shaft.stroke.width == 2.0
    tip, w1, w2 = head.segments[0][1:], head.segments[1][1:], head.segments[2][1:]
    assert tip == (40.0, 5.0)
    assert w1 == pytest.approx((32.0, 5.0 + 3.4)) and w2 == pytest.approx((32.0, 5.0 - 3.4))
    assert head.segments[-1] == ("Z",) and head.fill is not None and head.stroke is None


def test_legacy_head_field_and_open_and_bar_heads():
    both = geometry.arrow_paths(
        {"start": {"rx": 0, "ry": 0.5}, "end": {"rx": 1, "ry": 0.5}, "head": "both"}, 40.0, 10.0
    )
    assert len(both) == 3 and both[0].segments == (("M", 3.0, 5.0), ("L", 37.0, 5.0))
    o = {
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "head_start": "bar",
        "head_end": "open",
    }
    shaft, open_head, bar = geometry.arrow_paths(o, 40.0, 10.0)
    assert shaft.segments == (("M", 0.0, 5.0), ("L", 40.0, 5.0))  # 非 triangle 端不回缩
    assert (
        open_head.fill is None and open_head.stroke is not None and open_head.stroke.join == "round"
    )
    assert [s[0] for s in open_head.segments] == ["M", "L", "L"]
    assert bar.segments == (("M", 0.0, 5.0 + 1.7), ("L", 0.0, 5.0 - 1.7))


def test_a_degenerate_arrow_does_not_divide_by_zero():
    o = {"start": {"rx": 0.5, "ry": 0.5}, "end": {"rx": 0.5, "ry": 0.5}, "head_end": "triangle"}
    paths = geometry.arrow_paths(o, 40.0, 10.0)
    assert all(math.isfinite(v) for p in paths for s in p.segments for v in s[1:])
