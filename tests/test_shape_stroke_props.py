"""形状 / 箭头的描边属性补齐（ADR 0174）：无边框、描边不透明度、线型、端型、线端 / 拐角。

三件事一起钉：
1. **旧文档逐字不变**：`golden/shape_legacy_paths.json` 是加这些属性**之前**的 `geometry.py` 对 510 个旧写法对象
   （每种形状 × 线型 × 有无填充 × 圆角 / 边数 / 极细线，箭头的每种旧端型组合 × 旧 `head` 字段）出的全部路径的 repr；
   现在的实现必须逐字相同。
2. **前后端同源**：`golden/shape_geometry_vectors.json` 的 cap / join_default / head_size_factor / arrow 各格，
   前端 `shapeGeometry.golden.test.ts` 与这里各跑一遍。
3. **每个新属性的合同**：无边框时一个描边都不出（rect 铺满框）、不透明度进 Paint.alpha、点线强制圆头……
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import geometry, ir

GOLDEN = Path(__file__).resolve().parent / "golden"
VECTORS = json.loads((GOLDEN / "shape_geometry_vectors.json").read_text(encoding="utf-8"))
LEGACY = json.loads((GOLDEN / "shape_legacy_paths.json").read_text(encoding="utf-8"))

BASE = {"id": "o", "stroke_pt": 1.5, "color": "#336699"}


# ---------------------------------------------------------------------------
# 1. 旧文档逐字不变
# ---------------------------------------------------------------------------
def _legacy_case(name: str) -> list[str]:
    """按 golden 里的案例名还原当初喂给旧实现的对象（与生成脚本同一套枚举）。"""
    kind, *rest = name.split("-")
    if kind == "shape":
        shape, dash, fill, extra = rest
        o = dict(BASE, shape=shape)
        o.update(
            {
                "plain": {},
                "fillop": {"fill_opacity": 0.4},
                "round": {"corner_radius_mm": 3.0},
                "sides": {"sides": 5},
                "thin": {"stroke_pt": 0.01},
            }[extra]
        )
        if dash != "None":
            o["dash"] = dash
        if fill == "True":
            o["fill"] = "#ffcc00"
        if shape == "line":
            o["start"] = {"rx": 0.1, "ry": 0.9}
            o["end"] = {"rx": 0.8, "ry": 0.2}
        return [repr(p) for p in geometry.shape_paths(o, 56.7, 34.0)]
    hs, he, dash, legacy = rest
    o = dict(BASE, start={"rx": 0.05, "ry": 0.1}, end={"rx": 0.95, "ry": 0.7})
    if hs != "None":
        o["head_start"] = hs
    if he != "None":
        o["head_end"] = he
    if legacy != "None":
        o["head"] = legacy
    if dash != "None":
        o["dash"] = dash
    return [repr(p) for p in geometry.arrow_paths(o, 70.0, 20.0)]


@pytest.mark.parametrize("name", sorted(LEGACY))
def test_legacy_documents_produce_byte_identical_paths(name):
    assert _legacy_case(name) == LEGACY[name]


def test_the_legacy_corpus_is_not_trivially_small():
    assert len(LEGACY) >= 500
    assert any(k.startswith("arrow-") for k in LEGACY) and any(
        k.startswith("shape-") for k in LEGACY
    )


# ---------------------------------------------------------------------------
# 2. 共享向量（前端另一侧在 shapeGeometry.golden.test.ts）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("vec", VECTORS["cap"], ids=lambda v: f"{v['cap']}+{v['dash']}")
def test_stroke_cap_matches_the_shared_vectors(vec):
    assert geometry.stroke_cap(vec["cap"], vec["dash"]) == vec["expect"]


def test_dotted_is_always_round_capped_and_other_lines_keep_the_choice():
    for cap in ("butt", "round", "square"):
        assert geometry.stroke_cap(cap, "dotted") == "round"
    assert geometry.stroke_cap("butt", "dashed") == "butt"
    assert geometry.stroke_cap("square", None) == "square"
    assert geometry.stroke_cap(None, None) == geometry.DEFAULT_LINE_CAP == "round"
    assert geometry.stroke_cap("nonsense", None) == "round"


def test_default_join_and_head_size_factor_match_the_shared_vectors():
    for shape, join in VECTORS["join_default"].items():
        assert geometry.default_line_join(shape) == join
    assert geometry.HEAD_SIZE_FACTOR == VECTORS["head_size_factor"]


def _enc(h):
    if h is None:
        return None
    if h[0] == "dot":
        return {"shape": "dot", "cx": round(h[1], 9), "cy": round(h[2], 9), "r": round(h[3], 9)}
    return {"shape": h[0], "points": [[round(x, 9), round(y, 9)] for x, y in h[1]]}


@pytest.mark.parametrize("vec", VECTORS["arrow"], ids=lambda v: v["name"])
def test_arrow_geometry_matches_the_shared_vectors(vec):
    o = {
        "stroke_pt": vec["sw"],
        "start": {"rx": 0.1, "ry": 0.2},
        "end": {"rx": 0.9, "ry": 0.8},
        "head": vec["head"],
        "head_start": vec["head_start"],
        "head_end": vec["head_end"],
        "head_size_start": vec["head_size_start"],
        "head_size_end": vec["head_size_end"],
    }
    g = geometry.arrow_geometry(o, vec["w"], vec["h"])
    assert [round(v, 9) for v in g["p1"]] == pytest.approx(vec["p1"], abs=1e-8)
    assert [round(v, 9) for v in g["p2"]] == pytest.approx(vec["p2"], abs=1e-8)
    assert _enc(g["start_head"]) == vec["start_head"]
    assert _enc(g["end_head"]) == vec["end_head"]


# ---------------------------------------------------------------------------
# 3. 各属性的合同
# ---------------------------------------------------------------------------
CLOSED = ("rect", "ellipse", "triangle", "diamond", "polygon")


@pytest.mark.parametrize("shape", ("rect", "ellipse", "triangle", "diamond"))
def test_no_stroke_draws_fill_only_and_fills_the_whole_box(shape):
    o = dict(BASE, shape=shape, fill="#ffcc00", stroke=False)
    [p] = geometry.shape_paths(o, 40.0, 20.0)
    assert p.stroke is None and p.fill is not None
    xs = [seg[-2] for seg in p.segments if len(seg) > 2]
    ys = [seg[-1] for seg in p.segments if len(seg) > 2]
    # 填充铺满整个 40×20 框（有边框时内缩半个线宽 0.75）
    assert min(xs) == pytest.approx(0.0, abs=1e-6) and max(xs) == pytest.approx(40.0, abs=1e-6)
    assert min(ys) == pytest.approx(0.0, abs=1e-6) and max(ys) == pytest.approx(20.0, abs=1e-6)


def test_no_stroke_polygon_is_inscribed_without_the_stroke_inset():
    o = dict(BASE, shape="polygon", sides=6, fill="#ffcc00", stroke=False)
    [p] = geometry.shape_paths(o, 40.0, 20.0)
    assert p.stroke is None
    want = geometry.polygon_points(6, 40.0, 20.0, 0.0)
    got = [c for seg in p.segments[:6] for c in seg[1:]]
    assert got == pytest.approx([c for pt in want for c in pt])


@pytest.mark.parametrize("shape", CLOSED)
def test_no_stroke_and_no_fill_draws_nothing(shape):
    assert geometry.shape_paths(dict(BASE, shape=shape, stroke=False), 40.0, 20.0) == []


@pytest.mark.parametrize("shape", ("line", "brace"))
def test_line_and_brace_ignore_the_no_stroke_flag(shape):
    [p] = geometry.shape_paths(dict(BASE, shape=shape, stroke=False), 40.0, 20.0)
    assert p.stroke is not None


def test_stroke_true_or_absent_is_the_same_as_before():
    a = geometry.shape_paths(dict(BASE, shape="rect", fill="#fff"), 40.0, 20.0)
    b = geometry.shape_paths(dict(BASE, shape="rect", fill="#fff", stroke=True), 40.0, 20.0)
    assert a == b and a[0].stroke is not None


def test_rounded_no_stroke_rect_uses_the_full_box_for_the_radius():
    [p] = geometry.shape_paths(
        dict(BASE, shape="rect", fill="#fff", stroke=False, corner_radius_mm=1.0), 40.0, 20.0
    )
    r = 1.0 * 72.0 / 25.4
    assert p.segments[0] == ("M", pytest.approx(r), 0.0)


def test_stroke_opacity_goes_into_the_stroke_paint_not_the_fill():
    [p] = geometry.shape_paths(
        dict(BASE, shape="rect", fill="#fff", stroke_opacity=0.4), 40.0, 20.0
    )
    assert p.stroke.paint.alpha == pytest.approx(0.4) and p.fill.alpha == 1.0


@pytest.mark.parametrize(
    ("raw", "alpha"),
    [
        (0, 0.0),
        (0.0, 0.0),
        (1, 1.0),
        (1.5, 1.0),
        (-1, 0.0),
        (None, 1.0),
        ("x", 1.0),
        (float("nan"), 1.0),
        (True, 1.0),
    ],
)
def test_stroke_opacity_edge_values(raw, alpha):
    [p] = geometry.shape_paths(dict(BASE, shape="rect", stroke_opacity=raw), 40.0, 20.0)
    assert p.stroke.paint.alpha == alpha


def test_arrow_opacity_is_one_transparency_group_and_no_child_carries_alpha():
    """杆伸进端型、开口端型与杆在尖上重叠：各路径自己带 alpha 会叠深。所以不透明度只在整支箭头的组上，
    路径本身一律不透明（画布 `ArrowView` 同一做法：外层 `<g opacity>`）。"""
    from tavotto.rendercore import plan

    for kind in ("triangle", "stealth", "diamond", "dot", "open", "bar"):
        o = dict(
            BASE,
            type="arrow",
            x_mm=0,
            y_mm=0,
            w_mm=20,
            h_mm=4,
            start={"rx": 0, "ry": 0},
            end={"rx": 1, "ry": 0},
            head_start=kind,
            head_end=kind,
            stroke_opacity=0.3,
            dash="dashed",
        )
        paths = geometry.arrow_paths(o, 50.0, 10.0)
        assert len(paths) == 3
        for p in paths:
            alpha = p.stroke.paint.alpha if p.stroke is not None else p.fill.alpha
            assert alpha == 1.0, kind
        assert geometry.arrow_opacity(o) == pytest.approx(0.3)
        node = plan._shape_node(o, 100.0)
        assert node.opacity == pytest.approx(0.3) and len(node.children) == 3
    opaque = plan._shape_node(dict(o, stroke_opacity=None), 100.0)
    assert opaque.opacity == 1.0


def test_zero_opacity_is_valid_and_validates_in_the_ir():
    [p] = geometry.shape_paths(dict(BASE, shape="rect", stroke_opacity=0), 40.0, 20.0)
    ir.validate(ir.Page(100.0, 100.0, (p,), {}, None))
    assert p.stroke.paint.alpha == 0.0


def test_line_cap_and_join_are_read_from_the_object():
    [p] = geometry.shape_paths(
        dict(BASE, shape="rect", line_cap="butt", line_join="bevel"), 40.0, 20.0
    )
    assert (p.stroke.cap, p.stroke.join) == ("butt", "bevel")
    [b] = geometry.shape_paths(dict(BASE, shape="brace"), 40.0, 20.0)
    assert (b.stroke.cap, b.stroke.join) == ("round", "miter")
    [b2] = geometry.shape_paths(dict(BASE, shape="brace", line_join="round"), 40.0, 20.0)
    assert b2.stroke.join == "round"


def test_dotted_shape_keeps_round_caps_whatever_the_cap_says():
    [p] = geometry.shape_paths(dict(BASE, shape="rect", dash="dotted", line_cap="butt"), 40.0, 20.0)
    assert p.stroke.cap == "round" and p.stroke.dash
    [q] = geometry.shape_paths(dict(BASE, shape="rect", dash="dashed", line_cap="butt"), 40.0, 20.0)
    assert q.stroke.cap == "butt"


def test_bogus_cap_join_dash_fall_back_to_the_old_look():
    [p] = geometry.shape_paths(
        dict(BASE, shape="rect", line_cap="x", line_join="y", dash="z"), 40.0, 20.0
    )
    assert (p.stroke.cap, p.stroke.join, p.stroke.dash) == ("round", "round", ())


ARROW = dict(BASE, start={"rx": 0, "ry": 0.5}, end={"rx": 1, "ry": 0.5})


def test_new_head_kinds_shape_and_trim():
    sw = 1.5
    length, half = sw * 4, sw * 1.7
    w, h = 60.0, 10.0

    def heads(kind, **extra):
        paths = geometry.arrow_paths(dict(ARROW, head_end=kind, head_start="none", **extra), w, h)
        return paths[0], paths[1]

    # 燕尾：4 个点，凹口在 0.7 帽长处；杆回缩 0.6 帽长
    shaft, head = heads("stealth")
    assert [s[0] for s in head.segments] == ["M", "L", "L", "L", "Z"]
    assert head.segments[2][1] == pytest.approx(w - length * 0.7)
    assert shaft.segments[1][1] == pytest.approx(w - length * 0.6)
    # 菱形：宽的地方在帽长一半处；杆回缩 0.5 帽长
    shaft, head = heads("diamond")
    assert head.segments[1][1] == pytest.approx(w - length / 2)
    assert head.segments[2][1] == pytest.approx(w - length)
    assert shaft.segments[1][1] == pytest.approx(w - length * 0.5)
    # 圆点：四段贝塞尔，直径 = 帽全宽，贴着端点；杆回缩半径
    shaft, head = heads("dot")
    assert [s[0] for s in head.segments] == ["M", "C", "C", "C", "C", "Z"]
    assert head.segments[0][1] == pytest.approx(w)  # 最右点就是端点
    assert shaft.segments[1][1] == pytest.approx(w - half)
    assert head.fill is not None and head.stroke is None


def test_head_size_scales_length_and_width_and_defaults_to_the_old_size():
    def tip_back(size):
        o = dict(ARROW, head_end="triangle", head_start="none")
        if size:
            o["head_size_end"] = size
        _shaft, head = geometry.arrow_paths(o, 60.0, 10.0)
        return 60.0 - head.segments[1][1], abs(head.segments[1][2] - 5.0)

    base_len, base_half = tip_back(None)
    assert (base_len, base_half) == pytest.approx((6.0, 2.55))
    assert tip_back("medium") == pytest.approx((base_len, base_half))
    assert tip_back("small") == pytest.approx((base_len * 0.7, base_half * 0.7))
    assert tip_back("large") == pytest.approx((base_len * 1.5, base_half * 1.5))
    assert tip_back("huge") == pytest.approx((base_len, base_half))  # 不认识的档位 = 缺省


def test_unknown_head_kind_draws_no_head_and_does_not_trim():
    paths = geometry.arrow_paths(dict(ARROW, head_end="future-kind", head_start="none"), 60.0, 10.0)
    assert len(paths) == 1 and paths[0].segments[1][1] == 60.0


def test_open_head_join_follows_the_object_and_bar_stays_miter_by_default():
    o = dict(ARROW, head_end="open", head_start="bar")
    _shaft, open_head, bar = geometry.arrow_paths(o, 60.0, 10.0)
    assert open_head.stroke.join == "round" and bar.stroke.join == "miter"
    _shaft, open_head, _bar = geometry.arrow_paths(dict(o, line_join="miter"), 60.0, 10.0)
    assert open_head.stroke.join == "miter"
    # 端型不画虚线，也不受点线强制圆头影响
    _shaft, open_head, _bar = geometry.arrow_paths(
        dict(o, dash="dotted", line_cap="butt"), 60.0, 10.0
    )
    assert open_head.stroke.dash == () and open_head.stroke.cap == "butt"


def test_everything_the_new_attributes_can_emit_validates_in_the_ir():
    """IR 的闸（bad_path / bad_stroke）不拒收任何新属性的组合：包括只有填充、点线 + 方头、透明度 0。"""
    objs = []
    for shape in CLOSED + ("brace", "line"):
        for extra in (
            {"stroke": False, "fill": "#fff"},
            {"stroke_opacity": 0.5, "dash": "dashdot"},
            {"line_cap": "square", "line_join": "bevel", "dash": "longdashdot"},
        ):
            objs.append(("shape", dict(BASE, shape=shape, **extra)))
    for kind in ("triangle", "stealth", "diamond", "dot", "open", "bar"):
        objs.append(
            (
                "arrow",
                dict(
                    ARROW,
                    head_end=kind,
                    head_start=kind,
                    head_size_end="large",
                    stroke_opacity=0.2,
                    line_cap="butt",
                    dash="longdash",
                ),
            )
        )
    for typ, o in objs:
        paths = (
            geometry.shape_paths(o, 30.0, 20.0)
            if typ == "shape"
            else geometry.arrow_paths(o, 30.0, 20.0)
        )
        for p in paths:
            ir.validate(ir.Page(100.0, 100.0, (p,), {}, None))
        assert all(math.isfinite(v) for p in paths for seg in p.segments for v in seg[1:])
