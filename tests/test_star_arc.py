"""星形 / 弧线（ADR 0185 M1）的后端一侧：共享向量对拍、画进 PDF / SVG、缺省值不写也画得对、只有描边的形状不认无边框。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.rendercore import geometry, ir, plan

VEC = json.loads(
    (Path(__file__).resolve().parent / "golden" / "star_arc_vectors.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("v", VEC["star"], ids=lambda v: f"{v['points']}-{v['ratio']}")
def test_star_points_match_the_shared_vectors(v):
    got = geometry.star_points(v["points"], v["w"], v["h"], v["inset"], v["ratio"])
    assert [x for p in got for x in p] == pytest.approx([x for p in v["out"] for x in p], abs=1e-9)


@pytest.mark.parametrize("v", VEC["arc"], ids=lambda v: f"{v['w']}x{v['h']}")
def test_arc_segments_match_the_shared_vectors(v):
    got = geometry.arc_segments(v["w"], v["h"], v["inset"])
    assert [s[0] for s in got] == [s[0] for s in v["segments"]]
    assert [x for s in got for x in s[1:]] == pytest.approx(
        [x for s in v["segments"] for x in s[1:]], abs=1e-9
    )


BASE = {
    "type": "shape",
    "id": "s",
    "x_mm": 10,
    "y_mm": 10,
    "w_mm": 30,
    "h_mm": 30,
    "stroke_pt": 2.0,
    "color": "#000000",
}


def test_star_defaults_are_five_points_and_0382():
    [p] = geometry.shape_paths({**BASE, "shape": "star", "fill": "#ff0"}, 85.0, 85.0)
    assert [s[0] for s in p.segments] == ["M"] + ["L"] * 9 + ["Z"]
    want = geometry.star_points(5, 85.0, 85.0, 1.0, 0.382)
    assert p.segments[1][1:] == pytest.approx(want[1])
    assert p.fill is not None and p.stroke is not None


def test_star_ratio_and_points_from_the_payload():
    [p] = geometry.shape_paths(
        {**BASE, "shape": "star", "sides": 7, "star_ratio": 0.6, "fill": None}, 85.0, 85.0
    )
    assert len(p.segments) == 2 * 7 + 1
    assert p.segments[1][1:] == pytest.approx(geometry.star_points(7, 85.0, 85.0, 1.0, 0.6)[1])


def test_arc_is_stroke_only_and_ignores_stroke_false_and_fill():
    [p] = geometry.shape_paths(
        {**BASE, "shape": "arc", "fill": "#ff0", "stroke": False}, 85.0, 60.0
    )
    assert p.fill is None and p.stroke is not None
    assert [s[0] for s in p.segments] == ["M", "C"]
    assert p.stroke.join == "miter"  # 只有描边的形状缺省尖角（与直线 / 大括号同一条）
    # 「无边框」对弧线不生效：几何与有描边时逐字相同（仍按半个线宽内缩，外沿贴框）
    [q] = geometry.shape_paths({**BASE, "shape": "arc", "fill": None}, 85.0, 60.0)
    assert p.segments == q.segments
    assert p.segments == geometry.arc_segments(85.0, 60.0, 1.0)


def test_plan_compiles_star_and_arc():
    page = plan.compile_page(
        60.0,
        60.0,
        [
            {**BASE, "shape": "star", "fill": "#ff0"},
            {**BASE, "shape": "arc", "fill": None, "rotation_deg": 30},
        ],
        sources=None,  # type: ignore[arg-type]
        faces=None,  # type: ignore[arg-type]
    ).page
    assert len(page.children) == 2
    assert all(isinstance(g, ir.Group) for g in page.children)
