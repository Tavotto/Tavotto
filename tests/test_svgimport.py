"""外部 SVG 导入（ADR 0185 M5，`rendercore/svgimport.py`）。

1. 样例（Illustrator / Inkscape 风格）的导入结果与快照 `tests/golden/svgimport/*.json` 逐字段相同；
2. 几何：单位与 viewBox 换算、变换矩阵烤进锚点、弧换三次贝塞尔（中点落在圆上）、二次曲线精确升三次、S / T 反射、标志位紧写；
3. 样式：CSS 类 / id / 标签选择器按优先级、style 属性最高、继承、currentColor、渐变（userSpaceOnUse / 比例）、虚线换 pt；
4. 结构：组（两个以上子对象才成组）、use / defs / symbol、clipPath → 剪切组的蒙版（ADR 0188）；
5. 文字：字号、粗体、对齐、旋转；做不准的 dx / textPath / 斜切文字 → 不支持；
6. 安全与兜底：XXE 不展开、外链不取、滤镜 / 图片 / 复杂选择器记不支持；体积与元素数上限；接口。
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


# ------------------------------------------------------------------ 安全与兜底
def test_xxe_is_not_expanded():
    r = svgimport.import_svg((FIX / "xxe.svg").read_text(encoding="utf-8"))
    assert "root:" not in json.dumps(r)
    assert r["unsupported"] == {"external_ref": 1}


@pytest.mark.parametrize(
    "body,code",
    [
        ('<image href="http://example.invalid/a.png" width="1" height="1"/>', "image"),
        ('<rect width="1" height="1" filter="url(#f)"/>', "filter"),
        ('<rect width="1" height="1" mask="url(#m)"/>', "mask"),
        ('<rect width="1" height="1" fill="url(#p)"/><pattern id="p"/>', "pattern"),
        ('<use href="other.svg#a"/>', "external_ref"),
        ('<style>g > rect{fill:red}</style><rect width="1" height="1"/>', "css_selector"),
        ('<foreignObject width="1" height="1"/>', "foreign_object"),
        ('<path d="M0 0 L1 1" stroke="#000" marker-end="url(#m)"/>', "marker"),
    ],
)
def test_unsupported_features_are_reported(body, code):
    assert code in imp(body)["unsupported"]


def test_script_is_ignored_and_never_becomes_an_object():
    r = imp('<script>alert(1)</script><rect width="1" height="1" onload="alert(2)"/>')
    assert [o["type"] for o in r["objects"]] == ["path"]
    assert "onload" not in json.dumps(r)


@pytest.mark.parametrize("bad,code", [("<html/>", "not_svg"), ("<svg", "bad_xml")])
def test_not_svg(bad, code):
    with pytest.raises(svgimport.SvgImportError) as e:
        svgimport.import_svg(bad)
    assert e.value.code == code


def test_limits(monkeypatch):
    monkeypatch.setattr(svgimport, "MAX_ELEMENTS", 5)
    with pytest.raises(svgimport.SvgImportError) as e:
        imp("".join('<rect width="1" height="1"/>' for _ in range(10)))
    assert e.value.code == "too_many_elements"
    monkeypatch.setattr(svgimport, "MAX_BYTES", 100)
    with pytest.raises(svgimport.SvgImportError) as e:
        imp("x" * 200)
    assert e.value.code == "too_large"


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
