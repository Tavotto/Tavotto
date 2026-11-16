"""坐标与变换链测试（SG05 / ADR 0182）。

看护：变换累积（嵌套组、use 实例位移与自身 transform）、viewBox→pt 的
preserveAspectRatio 全语义（none / meet / slice × 九宫对齐）、以及语料上
「独立公式核对」的已知绑定（plain_shapes 的轴框映射）。
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
from tavotto.svgcoord import (
    IDENTITY,
    apply,
    compose,
    node_matrix,
    parse_transform,
    viewbox_to_pt,
)

CORPUS = Path(__file__).resolve().parent / "support" / "svg_corpus"


def corpus_root(name: str) -> ET.Element:
    return parse_svg(strip_matplotlib_doctype((CORPUS / name).read_bytes()))


# ---------------------------------------------------------------------------
# transform 解析与累积
# ---------------------------------------------------------------------------


def test_translate_scale_rotate_matrices():
    assert parse_transform("translate(10,20)") == (1.0, 0.0, 0.0, 1.0, 10.0, 20.0)
    assert parse_transform("scale(2)") == (2.0, 0.0, 0.0, 2.0, 0.0, 0.0)
    assert parse_transform("scale(2,3)") == (2.0, 0.0, 0.0, 3.0, 0.0, 0.0)
    rot = parse_transform("rotate(90)")
    assert apply(rot, 1.0, 0.0) == pytest.approx((0.0, 1.0), abs=1e-12)
    # rotate(cx,cy) 固定点旋转：点 (cx,cy) 不动
    rot_c = parse_transform("rotate(90,5,7)")
    assert apply(rot_c, 5.0, 7.0) == pytest.approx((5.0, 7.0), abs=1e-12)
    mtx = parse_transform("matrix(1,0,0,1,3,4)")
    assert mtx == (1.0, 0.0, 0.0, 1.0, 3.0, 4.0)


def test_nested_group_accumulation_on_corpus():
    root = corpus_root("use_and_groups.svg")
    # chip 定义在 defs 里（外层 translate(10,10) · 内层 translate(20,30)）
    m = node_matrix(root, "chip")
    # 定义段本身不嵌在使用点里：defs 的 chip 没有祖先变换
    assert m == IDENTITY
    # 第一个 use 实例：外层 + 内层（无自身 x/y —— href="#chip" 且无 x/y 属性）
    m1 = node_matrix(root, "outer")
    assert apply(m1, 0.0, 0.0) == (10.0, 10.0)  # 外层平移
    # 第二个 use：x=60 y=40 且自身 rotate(15)
    inner_uses = [el for el in root.iter() if el.tag.split("}")[-1] == "use"]
    assert len(inner_uses) == 2
    anchored = inner_uses[1]
    own = parse_transform(anchored.attrib["transform"])
    dx, dy = float(anchored.attrib["x"]), float(anchored.attrib["y"])
    outer_m = node_matrix(root, "inner")
    # 行向量约定：最深的变换最先作用于点 —— x/y 位移 → 自身 rotate → 祖先平移
    total = compose(compose((1.0, 0.0, 0.0, 1.0, dx, dy), own), outer_m)
    # 旋转 15° 后再平移：原点落在 内层+外层+ 位移 的旋转后位置
    ex, ey = apply(total, 0.0, 0.0)
    wx, wy = (
        10.0 + 20.0 + (60.0 * math.cos(math.radians(15)) - 40.0 * math.sin(math.radians(15))),
        10.0 + 30.0 + (60.0 * math.sin(math.radians(15)) + 40.0 * math.cos(math.radians(15))),
    )
    assert (ex, ey) == pytest.approx((wx, wy), abs=1e-9)


def test_unknown_transform_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        parse_transform("wand(3)")


# ---------------------------------------------------------------------------
# viewBox → 物理 pt（preserveAspectRatio 全语义）
# ---------------------------------------------------------------------------


def test_identity_viewbox_matches_corpus_pt_units():
    # line_two_series：viewBox 0 0 360 252，width 360pt → 1 unit = 1 pt
    m = viewbox_to_pt([0, 0, 360, 252], 360.0, 252.0, None)
    assert apply(m, 51.562344, 26.16) == pytest.approx((51.562344, 26.16), abs=1e-9)


def test_plain_shapes_known_binding_maps_through_px_units():
    # plain_shapes：width 400 height 300（px），viewBox 0 0 400 300 → 1 unit = 0.75pt
    m = viewbox_to_pt([0, 0, 400, 300], 300.0, 225.0, None)
    # README 的已知绑定：轴框 (60,40)-(360,260) 用户单位
    ax = apply(m, 60.0, 40.0)
    assert ax == pytest.approx((45.0, 30.0), abs=1e-9)


def test_meet_centers_and_uses_uniform_scale():
    m = viewbox_to_pt([0, 0, 100, 100], 200.0, 100.0, "xMidYMid meet")
    assert m[0] == 1.0 and m[3] == 1.0  # min(2, 1) = 1
    assert apply(m, 0.0, 0.0) == (50.0, 0.0)  # 水平居中
    assert apply(m, 100.0, 100.0) == (150.0, 100.0)


def test_slice_fills_and_overflows():
    m = viewbox_to_pt([0, 0, 100, 100], 200.0, 100.0, "xMidYMid slice")
    assert m[0] == 2.0  # max(2, 1) = 2
    # 内容 200x200，垂直居中 → 上溢 50
    assert apply(m, 0.0, 0.0) == (0.0, -50.0)


def test_none_is_non_uniform():
    m = viewbox_to_pt([0, 0, 100, 100], 200.0, 100.0, "none")
    assert apply(m, 10.0, 10.0) == (20.0, 10.0)


def test_align_corners():
    m = viewbox_to_pt([0, 0, 100, 100], 200.0, 100.0, "xMaxYMin meet")
    assert apply(m, 0.0, 0.0) == (100.0, 0.0)


def test_bad_viewbox_and_preserve_are_rejected():
    with pytest.raises(ValueError):
        viewbox_to_pt([0, 0, 0, 100], 10.0, 10.0, None)
    with pytest.raises(ValueError):
        viewbox_to_pt([0, 0, 100, 100], 10.0, 10.0, "squish")


def test_compose_order_matches_rendercore_convention():
    # 与 rendercore.ir.compose 逐位一致（同一约定，不同实现必须同形）
    from tavotto.rendercore import ir

    a, b = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0), (7.0, 8.0, 9.0, 10.0, 11.0, 12.0)
    assert compose(a, b) == ir.compose(a, b)
