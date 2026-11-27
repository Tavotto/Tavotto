"""外部 SVG 导入（ADR 0189，`rendercore/svgimport.py`）。

1. 样例（Illustrator / Inkscape 风格）的导入结果与快照 `tests/golden/svgimport/*.json` 逐字段相同；
2. 几何：单位与 viewBox 换算、变换矩阵烤进锚点、弧换三次贝塞尔（中点落在圆上）、二次曲线精确升三次、S / T 反射、标志位紧写；
3. 样式：CSS 类 / id / 标签选择器按优先级、style 属性最高、继承、currentColor、渐变（userSpaceOnUse / 比例）、虚线换 pt；
4. 结构：组（两个以上子对象才成组）、use / defs / symbol、clipPath → 剪切组的蒙版（ADR 0191）；
5. 文字：字号、粗体、对齐、旋转；做不准的 dx / textPath / 斜切文字 → 不支持；tspan 的上下标（baseline-shift / dy 配小字号）与局部粗斜
   保留成文字对象的 LaTeX 子集（ADR 0208 第 5 期）；
6. 线端标记：简单三角 / 开口箭头 / 圆点的 `marker-start / marker-end` → 路径的 `headStart / headEnd`（ADR 0208 第 5 期），别的仍不支持；
7. 兜底：图片 / 蒙版 / 图案 / 复杂标记 / 复杂选择器 / 非静态元素记不支持；编辑器命名空间与无作用 CSS 放行；接口。攻击样例在 `test_svgimport_security.py`。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import geometry, svgimport

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "fixtures" / "svgimport"
GOLD = ROOT / "golden" / "svgimport"
PX = 25.4 / 96


def imp(body: str, attrs: str = 'viewBox="0 0 100 100" width="100" height="100"') -> dict:
    return svgimport.import_svg(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>')


def page_nodes(o: dict) -> list[list[tuple[float, float]]]:
    return [
        [(o["x"] + n["p"][0] * o["w"], o["y"] + n["p"][1] * o["h"]) for n in sp["nodes"]]
        for sp in o["subpaths"]
    ]


@pytest.mark.parametrize("name", sorted(p.stem for p in FIX.glob("*.svg")))
def test_fixture_matches_snapshot(name):
    got = svgimport.import_svg((FIX / f"{name}.svg").read_text(encoding="utf-8"))
    want = json.loads((GOLD / f"{name}.json").read_text(encoding="utf-8"))
    assert got == want


# ------------------------------------------------------------------ 几何
def test_units_viewbox_and_rect():
    r = imp(
        '<rect x="10" y="20" width="30" height="40" fill="red"/>',
        'viewBox="0 0 100 100" width="50mm" height="50mm"',
    )
    (o,) = r["objects"]
    assert r["size_mm"] == [50.0, 50.0]
    assert (o["x"], o["y"], o["w"], o["h"]) == pytest.approx((5, 10, 15, 20))
    assert o["fill"] == "#ff0000" and o["stroke"] is False


def test_transform_is_baked_into_anchors():
    r = imp('<rect width="10" height="10" transform="translate(50 50) rotate(45)" fill="#000"/>')
    (o,) = r["objects"]
    pts = page_nodes(o)[0]
    assert pts[0] == pytest.approx((50 * PX, 50 * PX))
    assert pts[2] == pytest.approx((50 * PX, (50 + 10 * math.sqrt(2)) * PX))
    assert "rotationDeg" not in o


def test_arc_becomes_cubics_on_the_circle():
    r = imp('<path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#000"/>')
    (o,) = r["objects"]
    segs = geometry.path_segments(
        {**o, "x_mm": o["x"], "y_mm": o["y"], "w_mm": o["w"], "h_mm": o["h"]}, o["w"], o["h"]
    )["fill"]
    assert [s[0] for s in segs] == ["M", "C", "C"]
    # 每段中点到圆心 (50, 50) 的距离 = 40（误差 < 0.03%）
    cx, cy = 50 * PX - o["x"], 50 * PX - o["y"]
    prev = segs[0][1:3]
    for s in segs[1:]:
        p0, c1, c2, p3 = prev, s[1:3], s[3:5], s[5:7]
        mid = [0.125 * p0[k] + 0.375 * c1[k] + 0.375 * c2[k] + 0.125 * p3[k] for k in (0, 1)]
        assert math.hypot(mid[0] - cx, mid[1] - cy) == pytest.approx(40 * PX, rel=3e-4)
        prev = p3


def test_relative_commands_reflections_and_compact_flags():
    r = imp(
        '<path d="m10 10 h20 v20 h-20 z" fill="#000"/>'
        '<path d="M0 50 Q 10 40 20 50 T 40 50" fill="none" stroke="#000"/>'
        '<path d="M0 80 C 0 70 10 70 10 80 S 20 90 20 80" fill="none" stroke="#000"/>'
        '<path d="M50 50a10 10 0 1020 0" fill="none" stroke="#000"/>'
    )
    sq, q, c, a = r["objects"]
    assert [tuple(round(v / PX, 4) for v in p) for p in page_nodes(sq)[0]] == [
        (10, 10),
        (30, 10),
        (30, 30),
        (10, 30),
    ]
    assert sq["subpaths"][0]["closed"] is True
    # T 反射：第二段的控制点 = 第一段控制点关于 (20,50) 的对称点 (30,60)，升三次后入手柄 = 终点 + 2/3·(Q − 终点)
    n = q["subpaths"][0]["nodes"]
    end_in = (q["x"] + n[2]["in"][0] * q["w"], q["y"] + n[2]["in"][1] * q["h"])
    assert end_in == pytest.approx(((40 + 2 / 3 * (30 - 40)) * PX, (50 + 2 / 3 * (60 - 50)) * PX))
    # S 反射：第二段的出手柄 = (10,80) 关于前一段入手柄 (10,70) 的对称 = (10,90)
    m = c["subpaths"][0]["nodes"]
    out1 = (c["x"] + m[1]["out"][0] * c["w"], c["y"] + m[1]["out"][1] * c["h"])
    assert out1 == pytest.approx((10 * PX, 90 * PX))
    # 标志位紧写（large=1 sweep=0，读成 1 / 0 / 20）：半圆，sweep=0 在 y 向下的屏幕上逆时针 = 从下面绕，y 从 50 到 60
    assert a["y"] == pytest.approx(50 * PX, abs=1e-6)
    assert a["h"] == pytest.approx(10 * PX, abs=1e-6)
    assert a["w"] == pytest.approx(20 * PX, abs=1e-6)


# ------------------------------------------------------------------ 样式
def test_css_specificity_style_attribute_and_inheritance():
    r = imp(
        "<style>#x{fill:yellow} .a{fill:green} rect{fill:blue}</style>"
        '<g fill="red" stroke="#123456" stroke-width="2">'
        '<rect width="1" height="1"/><rect class="a" width="1" height="1"/>'
        '<rect id="x" class="a" width="1" height="1"/><rect id="x2" class="a" style="fill:#abcdef" width="1" height="1"/>'
        '<circle r="1"/></g>'
    )
    fills = [o["fill"] for o in r["objects"]]
    assert fills == ["#0000ff", "#008000", "#ffff00", "#abcdef", "#ff0000"]
    assert all(o["color"] == "#123456" for o in r["objects"])
    assert r["objects"][0]["strokePt"] == pytest.approx(2 * PX / (25.4 / 72))


def test_current_color_opacity_and_dash_in_points():
    r = imp(
        '<g color="#00ff00" opacity="0.5"><path d="M0 0 L10 0" fill="none" stroke="currentColor" '
        'stroke-dasharray="4 2 1" stroke-opacity="0.8" stroke-linecap="round" stroke-linejoin="bevel"/></g>'
    )
    (o,) = r["objects"]
    assert o["color"] == "#00ff00"
    assert o["strokeOpacity"] == pytest.approx(0.4)
    k = PX / (25.4 / 72)
    assert o["dashPattern"] == pytest.approx([4 * k, 2 * k, 1 * k, 4 * k, 2 * k, 1 * k])
    assert o["lineCap"] == "round" and o["lineJoin"] == "bevel"


def test_gradients_map_to_object_box_fractions():
    r = imp(
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#f00"/>'
        '<stop offset="1" stop-color="#00f" stop-opacity="0.5"/></linearGradient>'
        '<linearGradient id="u" gradientUnits="userSpaceOnUse" x1="20" y1="0" x2="30" y2="0" xlink:href="#g" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"/></defs>'
        '<rect x="10" y="10" width="40" height="20" fill="url(#g)"/>'
        '<rect x="10" y="40" width="40" height="20" fill="url(#u)"/>'
    )
    a, b = r["objects"]
    assert a["fill"] == "#ff0000"
    assert a["fillGradient"] == {
        "kind": "linear",
        "stops": [
            {"offset": 0.0, "color": "#ff0000"},
            {"offset": 1.0, "color": "#0000ff", "opacity": 0.5},
        ],
        "from": [0.0, 0.0],
        "to": [1.0, 0.0],
    }
    # userSpaceOnUse：x 20..30 在 10..50 的框里 = 0.25..0.5；y = 0 在 40..60 的框外（−2，水平线，方向不变）；色标从 href 继承
    assert b["fillGradient"]["from"] == pytest.approx([0.25, -2.0])
    assert b["fillGradient"]["to"] == pytest.approx([0.5, -2.0])
    assert len(b["fillGradient"]["stops"]) == 2


# ------------------------------------------------------------------ 结构
def test_groups_need_two_children_use_and_symbol():
    r = imp(
        '<g><rect width="5" height="5"/></g>'
        '<g><rect width="5" height="5"/><circle r="2"/><g><rect width="1" height="1"/><rect width="2" height="2"/></g></g>'
        '<defs><symbol id="s"><rect width="3" height="3"/><rect x="4" width="3" height="3"/></symbol></defs>'
        '<use href="#s" x="50" y="50"/>'
    )
    o = r["objects"]
    assert "groupId" not in o[0]
    outer = o[1]["groupId"]
    assert o[2]["groupId"] == outer and "groupPath" not in o[1]
    assert o[3]["groupPath"][0] == outer and len(o[3]["groupPath"]) == 2
    assert o[5]["groupId"] == o[6]["groupId"] != outer
    assert o[5]["x"] == pytest.approx(50 * PX)


def test_clip_path_becomes_a_clip_group():
    r = imp(
        '<clipPath id="c"><circle cx="50" cy="50" r="20"/></clipPath>'
        '<g clip-path="url(#c)"><rect width="100" height="100" fill="#f00"/><rect width="10" height="10" fill="#0f0"/></g>'
    )
    o = r["objects"]
    mask = o[-1]
    assert mask["clipMask"] is True and mask["hidden"] is True
    assert (
        mask["groupId"] == o[0]["groupPath"][0]
        if o[0].get("groupPath")
        else mask["groupId"] == o[0]["groupId"]
    )
    assert mask["w"] == pytest.approx(40 * PX)


def test_a_group_wrapping_everything_is_dropped_but_not_a_clip_group():
    # SVG 写入器为翻 y 包的根组、Inkscape 的图层：包住全部对象的那一层不留
    r = imp(
        '<g><g><rect width="1" height="1"/><rect x="2" width="1" height="1"/></g><circle r="1"/></g>'
    )
    o = r["objects"]
    assert "groupId" not in o[2]
    assert o[0]["groupId"] == o[1]["groupId"] and "groupPath" not in o[0]


def test_clip_path_in_bounding_box_units_is_unsupported():
    r = imp(
        '<clipPath id="c" clipPathUnits="objectBoundingBox"><rect width="0.5" height="1"/></clipPath>'
        '<rect width="10" height="10" clip-path="url(#c)"/>'
    )
    assert r["unsupported"] == {"clip_units": 1}


# ------------------------------------------------------------------ 文字
def test_text_size_weight_anchor_rotation():
    r = imp(
        '<text x="50" y="40" font-size="12" font-weight="700" text-anchor="middle" font-family="Arial" '
        'fill="#333" transform="rotate(90 50 40)">Hi <tspan>there</tspan></text>'
    )
    (t,) = r["objects"]
    assert t["text"] == "Hi there"
    assert t["sizePt"] == pytest.approx(12 * PX / (25.4 / 72))
    assert t["bold"] is True and t["align"] == "center" and t["fontFamily"] == "sans-serif"
    assert t["rotationDeg"] == pytest.approx(90)
    assert t["color"] == "#333333"


def test_illustrator_postscript_names_carry_weight_and_style():
    r = imp(
        '<text x="0" y="10" font-family="\'Arial-BoldMT\'">a</text>'
        '<text x="0" y="20" font-family="Helvetica-Oblique">b</text>'
        '<text x="0" y="30" font-family="Times-Roman">c</text>'
    )
    a, b, c = r["objects"]
    assert (a["bold"], a["italic"], a["fontFamily"]) == (True, False, "sans-serif")
    assert (b["bold"], b["italic"]) == (False, True)
    assert (c["bold"], c["italic"]) == (False, False) and "fontFamily" not in c


@pytest.mark.parametrize(
    "body,code",
    [
        ('<text x="0" y="10" dx="1">a</text>', "text_layout"),
        ('<text x="0 5" y="10">ab</text>', "text_layout"),
        ('<path id="p" d="M0 0 L10 0"/><text><textPath href="#p">a</textPath></text>', "text_path"),
        ('<text x="0" y="10" transform="skewX(20)">a</text>', "skew_text"),
    ],
)
def test_text_that_cannot_be_placed_exactly_is_unsupported(body, code):
    assert code in imp(body)["unsupported"]


# ------------------------------------------------------------------ 做不准 → 不拆（攻击样例在 test_svgimport_security.py）
@pytest.mark.parametrize(
    "body,code",
    [
        (
            '<image href="data:image/png;base64,'
            + "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
            + '" width="1" height="1"/>',
            "image",
        ),
        (
            '<defs><mask id="m"><rect width="1" height="1"/></mask></defs><rect width="1" height="1" mask="url(#m)"/>',
            "mask",
        ),
        (
            '<defs><pattern id="p" width="1" height="1"><rect width="1" height="1"/></pattern></defs><rect width="1" height="1" fill="url(#p)"/>',
            "pattern",
        ),
        (
            '<defs><marker id="k"><path d="M0 0 L1 1"/></marker></defs><path d="M0 0 L1 1" stroke="#000" marker-end="url(#k)"/>',
            "marker",
        ),
        ('<style>g > rect{fill:red}</style><rect width="1" height="1"/>', "css_selector"),
        (
            '<defs><filter id="f"/></defs><rect width="1" height="1" filter="url(#f)"/>',
            "unsupported_element",
        ),
        ('<foreignObject width="1" height="1"/>', "unsupported_element"),
    ],
)
def test_unsupported_features_are_reported(body, code):
    assert code in imp(body)["unsupported"]


@pytest.mark.parametrize("bad,code", [("<html/>", "not_svg"), ("<svg>", "bad_xml")])
def test_not_svg(bad, code):
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.import_svg(bad)
    assert e.value.code == code


def test_editor_namespaces_and_inert_css_are_tolerated():
    r = svgimport.import_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" viewBox="0 0 10 10" '
        'style="enable-background:new 0 0 10 10" inkscape:version="1.3">'
        '<sodipodi:namedview id="nv"/><metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></metadata>'
        '<g inkscape:groupmode="layer"><rect width="2" height="2" style="fill:#f00;-inkscape-stroke:none;overflow:visible"/></g></svg>'
    )
    assert r["unsupported"] == {}
    assert [o["fill"] for o in r["objects"]] == ["#ff0000"]


def test_public_svg_doctype_without_subset_is_tolerated():
    r = svgimport.import_svg(
        '<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4">'
        '<rect width="1" height="1"/></svg>'
    )
    assert r["unsupported"] == {} and len(r["objects"]) == 1


def test_endpoint(tmp_path):
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    r = client.post(
        "/api/svg/import", json={"svg": (FIX / "illustrator.svg").read_text(encoding="utf-8")}
    )
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert len(r.get_json()["objects"]) == 7
    assert client.post("/api/svg/import", json={"svg": "<p/>"}).get_json() == {
        "ok": False,
        "error": "not_svg",
    }
    assert client.post("/api/svg/import", json={}).status_code == 400


# ------------------------------------------------------------------ 线端标记 → 端型（ADR 0208 第 5 期）
def _marker(inner: str, **attrs) -> str:
    a = {
        "id": "m",
        "markerWidth": "4",
        "markerHeight": "4",
        "refX": "4",
        "refY": "2",
        "orient": "auto",
    }
    a.update(attrs)
    return (
        "<defs><marker "
        + " ".join(f'{k}="{v}"' for k, v in a.items())
        + f">{inner}</marker></defs>"
    )


TRI = '<path d="M0,0 L4,2 L0,4 z"/>'
HEADS = ("headStart", "headEnd", "headSizeStart", "headSizeEnd")


def heads(o: dict) -> dict:
    return {k: o[k] for k in HEADS if k in o}


def test_marker_end_triangle_path_with_orient_auto():
    r = imp(
        _marker(TRI) + '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    (o,) = r["objects"]
    assert r["unsupported"] == {} and "hints" not in r
    assert heads(o) == {"headEnd": "triangle"}  # 帽长 4 = 4 × 线宽 → 中档，不写档位


def test_marker_end_polygon_content_and_diagonal_line():
    r = imp(
        _marker('<polygon points="0,0 4,2 0,4"/>')
        + '<line x1="5" y1="5" x2="50" y2="50" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    assert r["unsupported"] == {}
    assert heads(r["objects"][0]) == {"headEnd": "triangle"}


def test_marker_units_stroke_width_scales_with_the_line_and_picks_the_size():
    # markerUnits 缺省 = strokeWidth：线越粗帽越大；帽长 = 4 × 标记单位 × 线宽 → 与线宽无关，档位由 markerWidth 决定
    body = '<path d="M5 5 L50 5" stroke="#000" stroke-width="{w}" marker-end="url(#m)"/>'
    small = imp(
        _marker(TRI, markerWidth="3", markerHeight="3", viewBox="0 0 4 4") + body.format(w=2)
    )
    assert heads(small["objects"][0]) == {
        "headEnd": "triangle",
        "headSizeEnd": "small",
    }  # 3/4 × 4 = 3 倍线宽
    big = imp(_marker(TRI, markerWidth="6", markerHeight="6", viewBox="0 0 4 4") + body.format(w=2))
    assert heads(big["objects"][0]) == {
        "headEnd": "triangle",
        "headSizeEnd": "large",
    }  # 6 倍线宽 → 1.5 倍


def test_marker_units_user_space_on_use_ignores_the_line_width():
    body = '<path d="M5 5 L50 5" stroke="#000" stroke-width="{w}" marker-end="url(#m)"/>'
    m = _marker(
        TRI, markerUnits="userSpaceOnUse", viewBox="0 0 4 4", markerWidth="8", markerHeight="8"
    )
    assert heads(imp(m + body.format(w=2))["objects"][0]) == {
        "headEnd": "triangle"
    }  # 8 = 4 × 2，中档
    assert heads(imp(m + body.format(w=1))["objects"][0]) == {
        "headEnd": "triangle",
        "headSizeEnd": "large",
    }


def test_marker_size_outside_the_three_steps_is_unsupported():
    m = _marker(
        TRI, markerUnits="userSpaceOnUse", viewBox="0 0 4 4", markerWidth="40", markerHeight="40"
    )
    r = imp(m + '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>')
    assert r["unsupported"] == {"marker": 1}


def test_marker_start_needs_the_tip_to_point_away_from_the_line():
    line = '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-start="url(#m)"/>'
    # orient=auto：标记的 +x 沿线前进，起点的尖朝线里面 → 不是起点箭头
    assert imp(_marker(TRI) + line)["unsupported"] == {"marker": 1}
    # auto-start-reverse：起点反向，尖朝外
    r = imp(_marker(TRI, orient="auto-start-reverse") + line)
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {"headStart": "triangle"}
    # 尖朝 -x 的三角 + orient=auto 同样朝外
    r = imp(_marker('<path d="M4,0 L0,2 L4,4 z"/>', refX="0") + line)
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {"headStart": "triangle"}


def test_marker_on_both_ends():
    r = imp(
        _marker(TRI, orient="auto-start-reverse")
        + '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-start="url(#m)" marker-end="url(#m)"/>'
    )
    assert heads(r["objects"][0]) == {"headStart": "triangle", "headEnd": "triangle"}


def test_marker_via_css_class_and_the_shorthand_is_not_accepted_by_the_parser():
    r = imp(
        "<style>.a{marker-start:url(#m);marker-end:url(#m)}</style>"
        + _marker(TRI, orient="auto-start-reverse")
        + '<path class="a" d="M5 5 L50 5" stroke="#000" stroke-width="1"/>'
    )
    assert r["unsupported"] == {}
    assert heads(r["objects"][0]) == {"headStart": "triangle", "headEnd": "triangle"}
    # 简写 `marker:` 过不了受限解析器的 CSS 白名单（svgcheck），整段拒绝；安全边界不动
    short = imp(
        "<style>.a{marker:url(#m)}</style>"
        + _marker(TRI)
        + '<path class="a" d="M5 5 L50 5" stroke="#000"/>'
    )
    assert short["objects"] == [] and short["unsupported"]


def test_marker_open_arrow_and_dot():
    line = '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    r = imp(_marker('<path d="M0,0 L4,2 L0,4" fill="none" stroke="#000"/>') + line)
    assert heads(r["objects"][0]) == {"headEnd": "open"}
    r = imp(
        _marker('<circle cx="1.7" cy="1.7" r="1.7"/>', refX="1.7", refY="1.7", orient="0") + line
    )
    assert r["unsupported"] == {} and r["objects"][0]["headEnd"] == "dot"


def test_marker_tip_off_the_endpoint_moves_the_endpoint_to_the_tip():
    # refX=0：参考点在底边中点、尖在前面 4 个单位 → 路径末端要往前挪 4，尖才落在 SVG 里尖的位置
    ref_at_tip = imp(
        _marker(TRI) + '<path d="M0 5 L40 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    ref_at_base = imp(
        _marker(TRI, refX="0")
        + '<path d="M0 5 L36 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    a, b = ref_at_tip["objects"][0], ref_at_base["objects"][0]
    assert b["w"] == pytest.approx(a["w"]) and heads(a) == heads(b) == {"headEnd": "triangle"}


@pytest.mark.parametrize(
    "marker,line",
    [
        # 颜色与线不同：端型总是线色
        (_marker('<path d="M0,0 L4,2 L0,4 z" fill="#f00"/>'), 'stroke="#000"'),
        # 渐变 / 多个图形 / 曲线 / 四边形 / 不等腰
        (_marker('<path d="M0,0 L4,2 L0,4 z"/><circle r="1"/>'), 'stroke="#000"'),
        (_marker('<path d="M0,0 C2,0 4,1 4,2 L0,4 z"/>'), 'stroke="#000"'),
        (_marker('<path d="M0,0 L4,0 L4,4 L0,4 z"/>'), 'stroke="#000"'),
        (_marker('<path d="M0,0 L9,0 L0,4 z"/>'), 'stroke="#000"'),
        # 固定角度的朝向、与线不一致
        (_marker(TRI, orient="90"), 'stroke="#000"'),
        # 标记里的图形套了一层组
        (_marker('<g><path d="M0,0 L4,2 L0,4 z"/></g>'), 'stroke="#000"'),
    ],
)
def test_complex_markers_stay_unsupported_with_a_hint(marker, line):
    r = imp(marker + f'<path d="M5 5 L50 5" {line} stroke-width="1" marker-end="url(#m)"/>')
    assert r["unsupported"] == {"marker": 1}
    assert "arrow" in r["hints"]["marker"]


@pytest.mark.parametrize(
    "line",
    [
        '<path d="M5 5 L50 5 L50 30" stroke="#000" marker-mid="url(#m)"/>',  # marker-mid
        '<path d="M5 5 L50 5 L50 30 z" stroke="#000" marker-end="url(#m)"/>',  # 闭合路径：起点终点是同一个顶点
        '<g marker-end="url(#m)"><path d="M5 5 L50 5" stroke="#000"/></g>',  # 组上的（继承）
        '<rect width="10" height="10" stroke="#000" marker-end="url(#m)"/>',  # rect 本来就没有标记
        '<path d="M5 5 L50 5" stroke="none" fill="none" marker-end="url(#m)"/>',  # 线没有描边：端型没有颜色可借
    ],
)
def test_marker_in_unsupported_places_stays_unsupported(line):
    assert imp(_marker(TRI) + line)["unsupported"] == {"marker": 1}


def test_marker_pointing_at_a_missing_id_is_rejected_by_the_parser():
    r = imp('<path d="M5 5 L50 5" stroke="#000" marker-end="url(#nope)"/>')
    assert r["unsupported"] == {"external_ref": 1}


def test_marker_with_a_curved_last_segment_and_tip_offset_is_unsupported():
    # 尖不在端点上又要移端点，但末段是曲线：移不了
    r = imp(
        _marker(TRI, refX="0")
        + '<path d="M5 5 C20 5 30 20 50 20" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    assert r["unsupported"] == {"marker": 1}
    # 尖在端点上：曲线末段也能换（切线取手柄方向）
    r = imp(
        _marker(TRI)
        + '<path d="M5 5 C20 5 30 20 50 20" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {"headEnd": "triangle"}


# ------------------------------------------------------------------ tspan 的上下标与局部粗斜（ADR 0208 第 5 期）
def one_text(body: str) -> dict:
    r = imp(body)
    assert r["unsupported"] == {}, r["unsupported"]
    (t,) = r["objects"]
    return t


def runs_of(t: dict):
    from tavotto import richtext

    return [
        (r.text, r.script, r.bold, r.italic)
        for r in richtext.parse_runs(t["text"], t.get("textFormat"))
    ]


def test_tspan_baseline_shift_super_keeps_the_superscript():
    t = one_text(
        '<text x="5" y="20" font-size="12">Ca<tspan baseline-shift="super" font-size="8">2+</tspan> ok</text>'
    )
    assert t["textFormat"] == "latex" and t["text"] == "Ca\\textsuperscript{2+} ok"
    assert runs_of(t) == [
        ("Ca", "", False, False),
        ("2+", "sup", False, False),
        (" ok", "", False, False),
    ]


def test_tspan_baseline_shift_keyword_alone_and_subscript():
    t = one_text('<text x="5" y="20" font-size="12">H<tspan baseline-shift="sub">2</tspan>O</text>')
    assert runs_of(t) == [
        ("H", "", False, False),
        ("2", "sub", False, False),
        ("O", "", False, False),
    ]


def test_tspan_baseline_shift_length_from_css_class():
    t = one_text(
        "<style>.s{baseline-shift:4px;font-size:8px}</style>"
        '<text x="5" y="20" font-size="12">m<tspan class="s">2</tspan></text>'
    )
    assert runs_of(t)[1] == ("2", "sup", False, False)


def test_tspan_dy_with_small_font_is_a_script_until_dy_is_reset():
    t = one_text(
        '<text x="5" y="20" font-size="12">Ca<tspan dy="-4" font-size="8">2+</tspan><tspan dy="4" font-size="12"> ion</tspan></text>'
    )
    assert runs_of(t) == [
        ("Ca", "", False, False),
        ("2+", "sup", False, False),
        (" ion", "", False, False),
    ]
    t = one_text(
        '<text x="5" y="20" font-size="12">x<tspan dy="0.3em" font-size="70%">i</tspan></text>'
    )
    assert runs_of(t)[1] == ("i", "sub", False, False)  # dy 向下 = 下标；em / % 按正文字号


def test_tspan_script_never_flattens_into_plain_digits():
    for body in (
        '<text x="5" y="20" font-size="12">Ca<tspan baseline-shift="super" font-size="8">2+</tspan></text>',
        '<text x="5" y="20" font-size="12">Ca<tspan dy="-4" font-size="8">2+</tspan></text>',
    ):
        assert one_text(body)["text"] != "Ca2+"


def test_tspan_shift_that_is_not_a_script_is_unsupported_not_flattened():
    for body in (
        '<text x="5" y="20" font-size="12">x<tspan dy="-4">2</tspan></text>',  # 偏了但字号没缩小
        '<text x="5" y="20" font-size="12">Ca<tspan dy="-4" font-size="8">2+</tspan> ion</text>',  # dy 没复位，后面的字还在抬高
        '<text x="5" y="20"><tspan dy="1 2">ab</tspan></text>',  # 逐字 dy
        '<text x="5" y="20" font-size="12">x<tspan baseline-shift="middle">2</tspan></text>',
        '<text x="5" y="20" font-size="12">x<tspan x="9" dy="-4" font-size="8">2</tspan></text>',  # 新起一段又带 dy
    ):
        assert imp(body)["unsupported"] == {"text_layout": 1}, body


def test_tspan_local_bold_and_italic():
    t = one_text(
        '<text x="5" y="20" font-size="12">a <tspan font-weight="bold">b</tspan> <tspan font-style="italic">c</tspan></text>'
    )
    assert t["bold"] is False and t["italic"] is False
    assert runs_of(t) == [
        ("a ", "", False, False),
        ("b", "", True, False),
        (" ", "", False, False),
        ("c", "", False, True),
    ]


def test_tspan_whole_text_bold_stays_on_the_object_and_script_still_kept():
    t = one_text(
        '<text x="5" y="20" font-size="12" font-weight="bold">E<tspan baseline-shift="super" font-size="8">2</tspan></text>'
    )
    assert t["bold"] is True
    assert runs_of(t) == [("E", "", False, False), ("2", "sup", False, False)]


def test_tspan_nested_bold_and_script():
    t = one_text(
        '<text x="5" y="20" font-size="12">a<tspan font-weight="bold">b<tspan baseline-shift="super" font-size="8">c</tspan>d</tspan></text>'
    )
    assert runs_of(t) == [
        ("a", "", False, False),
        ("b", "", True, False),
        ("c", "sup", True, False),
        ("d", "", True, False),
    ]


def test_unicode_superscripts_are_kept_as_text():
    t = one_text('<text x="5" y="20">Ca²⁺ 10⁻³</text>')
    assert t["text"] == "Ca²⁺ 10⁻³" and "textFormat" not in t


def test_literal_markup_characters_in_imported_text_are_escaped():
    # `_{on}` 在旧标记里是下标：字面的下划线花括号要走 LaTeX 子集的转义，显示出来仍是 k_{on}
    t = one_text('<text x="5" y="20">k_{on} a\\b</text>')
    assert t["textFormat"] == "latex" and runs_of(t) == [("k_{on} a\\b", "", False, False)]


def test_plain_text_objects_are_unchanged():
    t = one_text('<text x="5" y="20">Hello <tspan>world</tspan></text>')
    assert t["text"] == "Hello world" and "textFormat" not in t


# ------------------------------------------------------------------ filter 与 pattern 分开测（ADR 0208 修订）
def test_filter_element_alone_rejects_the_whole_svg_even_when_unused():
    # `<filter>` 是受限解析器的非静态元素：只要出现就整段拒绝（原因码 unsupported_element），不用也拒
    r = imp(
        '<defs><filter id="f"><feGaussianBlur stdDeviation="2"/></filter></defs><rect width="10" height="10"/>'
    )
    assert r["objects"] == [] and r["unsupported"] == {"unsupported_element": 1}


def test_pattern_element_alone_is_fine_only_a_pattern_fill_is_unsupported():
    pat = '<defs><pattern id="p" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="2" height="2"/></pattern></defs>'
    unused = imp(pat + '<rect width="10" height="10" fill="#f00"/>')
    assert unused["unsupported"] == {} and len(unused["objects"]) == 1
    used = imp(pat + '<rect width="10" height="10" fill="url(#p)"/>')
    assert used["unsupported"] == {"pattern": 1}


# ------------------------------------------------------------------ 标记图形「看起来」的样子（Codex P1）
LINE_M = '<path d="M5 5 L50 5" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'


@pytest.mark.parametrize(
    "inner",
    [
        '<path d="M0,0 L4,2 L0,4 z" display="none"/>',
        '<path d="M0,0 L4,2 L0,4 z" opacity="0"/>',
        '<path d="M0,0 L4,2 L0,4 z" fill-opacity="0"/>',
        '<path d="M0,0 L4,2 L0,4 z" visibility="hidden"/>',
        '<path d="M0,0 L4,2 L0,4" fill="none" stroke="#000" stroke-opacity="0"/>',
        '<path d="M0,0 L4,2 L0,4 z" style="opacity:0"/>',
    ],
)
def test_invisible_marker_shape_gives_no_head(inner):
    r = imp(_marker(inner) + LINE_M)
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {}


def test_invisible_through_marker_element_or_inherited_paint():
    assert heads(imp(_marker(TRI, opacity="0") + LINE_M)["objects"][0]) == {}
    assert heads(imp(_marker(TRI, visibility="hidden") + LINE_M)["objects"][0]) == {}
    assert heads(imp(_marker(TRI, **{"fill-opacity": "0"}) + LINE_M)["objects"][0]) == {}


@pytest.mark.parametrize(
    "inner",
    [
        '<path d="M0,0 L4,2 L0,4 z" opacity="0.5"/>',
        '<path d="M0,0 L4,2 L0,4 z" fill-opacity="0.5"/>',
        '<path d="M0,0 L4,2 L0,4 z" fill="rgba(0,0,0,0.5)"/>',
        '<path d="M0,0 L4,2 L0,4" fill="none" stroke="#000" stroke-opacity="0.5"/>',
        '<path d="M0,0 L4,2 L0,4 z" clip-path="url(#c)"/>',
        '<path d="M0,0 L4,2 L0,4 z" paint-order="stroke"/>',
    ],
)
def test_partly_transparent_or_clipped_marker_shape_stays_unsupported(inner):
    defs = '<defs><clipPath id="c"><rect width="2" height="4"/></clipPath></defs>'
    assert imp(defs + _marker(inner) + LINE_M)["unsupported"] == {"marker": 1}


def test_partly_transparent_marker_element_stays_unsupported():
    assert imp(_marker(TRI, opacity="0.5") + LINE_M)["unsupported"] == {"marker": 1}


def test_context_fill_and_context_stroke_use_their_own_paint():
    red_black = (
        '<path d="M5 5 L50 5" fill="red" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    )
    r = imp(_marker('<path d="M0,0 L4,2 L0,4 z" fill="context-fill"/>') + red_black)
    assert r["unsupported"] == {"marker": 1}  # 头是红的、线是黑的：端型只能是线色
    r = imp(_marker('<path d="M0,0 L4,2 L0,4 z" fill="context-stroke"/>') + red_black)
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {"headEnd": "triangle"}
    same = '<path d="M5 5 L50 5" fill="#000" stroke="#000" stroke-width="1" marker-end="url(#m)"/>'
    r = imp(_marker('<path d="M0,0 L4,2 L0,4 z" fill="context-fill"/>') + same)
    assert r["unsupported"] == {} and heads(r["objects"][0]) == {"headEnd": "triangle"}
