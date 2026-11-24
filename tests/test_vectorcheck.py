"""`engine/vectorcheck`（ADR 0208 §四）：每一项核对的 pass / fail / unknown / waived。
manifest 是手写的最小图（一根坐标轴、一条折线、一根柱、一个只有外框的误差棒、一个刻度文字），子图铺在画布 (10, 20) 起、
80 × 40 mm、不裁剪，图幅分数 × (80, 40) + (10, 20) 就是画布毫米。端到端（真 worker + 真导出）在 `test_vectorscene_e2e.py`。"""

from __future__ import annotations

import pytest

from tavotto.engine import agentobjects, profiles, vectorcheck, vectorscene

PANEL = {"x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}
MANIFEST = {
    "size_mm": [80, 40],
    "elements": [
        {"gid": "axes_0", "role": "axes", "bbox": [0.1, 0.1, 0.8, 0.8]},
        # 一条从 (0.1, 0.9) 到 (0.9, 0.1) 的斜线
        {
            "gid": "axes_0.lines_0",
            "role": "line",
            "bbox": [0.1, 0.1, 0.8, 0.8],
            "geometry": {"paths": [{"points": [[0.1, 0.9], [0.9, 0.1]], "closed": False}]},
        },
        # 一根柱子：x 0.70–0.80，y 0.60–0.90（闭合填充）
        {
            "gid": "axes_0.bar_0",
            "role": "patch",
            "bbox": [0.7, 0.6, 0.1, 0.3],
            "geometry": {
                "paths": [
                    {"points": [[0.7, 0.6], [0.8, 0.6], [0.8, 0.9], [0.7, 0.9]], "closed": True}
                ]
            },
        },
        # 只有外框的误差棒：x 0.20–0.25，y 0.20–0.30
        {"gid": "axes_0.err_0", "role": "errorbar", "bbox": [0.2, 0.2, 0.05, 0.1]},
        # 刻度文字：x 0.02–0.09，y 0.45–0.50
        {"gid": "axes_0.yticklabels_0", "role": "ticklabel", "bbox": [0.02, 0.45, 0.07, 0.05]},
    ],
}
PAGE = {"w": 120.0, "h": 80.0}


def _panels():
    return {
        "P1": {"panel": dict(PANEL), "draw_manifest": MANIFEST, "requests": [], "data_points": []}
    }


def _measure(text, size, bold, italic, family):
    return 6.0


def _run(items, *, profile=None, page=PAGE):
    solved = vectorscene.solve(items, _panels(), measure=_measure)
    assert solved["ok"], solved["errors"]
    checked = agentobjects.validate_new_objects(solved["objects"])
    assert checked["ok"], checked["errors"]
    return vectorcheck.check(
        solved, checked["objects"], _panels(), items, page=page, profile=profile
    )


def _status(verdict, check, item):
    return [f["status"] for f in verdict["facts"] if f["check"] == check and f.get("item") == item]


def _at(x, y):
    """画布毫米 → 锚在子图左上角 + 偏移（不依赖数据点，单元用例里没有 worker）。"""
    return {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [x - 10.0, y - 20.0]}


def test_clear_text_passes_every_check():
    v = _run(
        [
            {
                "kind": "text",
                "name": "t",
                "text": "ok",
                "at": _at(60, 26),
                "attach": "nw",
                "sizePt": 8,
            }
        ]
    )
    assert v["ok"] and v["verified"], v["facts"]
    assert _status(v, "anchor", "t") == ["pass"]


def test_text_on_the_line_fails_data():
    # 斜线在画布上过 (50, 40)
    v = _run([{"kind": "text", "name": "t", "text": "x", "at": _at(50, 40), "attach": "center"}])
    assert _status(v, "data", "t") == ["fail"] and not v["ok"]


def test_text_inside_a_bar_fails_data_even_without_crossing_its_edge():
    # 柱子在画布上 x 66–74、y 44–56；字框在里面、碰不到边
    v = _run(
        [
            {
                "kind": "text",
                "name": "t",
                "text": "x",
                "at": _at(70, 50),
                "attach": "center",
                "sizePt": 4,
            }
        ]
    )
    assert _status(v, "data", "t") == ["fail"]


def test_bbox_only_data_is_unknown_not_pass():
    # 误差棒在画布上 x 26–30、y 28–32
    v = _run(
        [
            {
                "kind": "text",
                "name": "t",
                "text": "x",
                "at": _at(28, 30),
                "attach": "center",
                "sizePt": 4,
            }
        ]
    )
    assert _status(v, "data", "t") == ["unknown"]
    assert v["ok"] and not v["verified"]


def test_over_data_is_waived():
    v = _run(
        [{"kind": "shape", "name": "ring", "at": _at(50, 40), "size_mm": [3, 3], "over_data": True}]
    )
    assert _status(v, "data", "ring") == ["waived"]


def test_unfilled_ring_judged_by_its_outline():
    # 圈在柱子正中、半径小于柱子：圆环这一圈线落在柱子里面 → 遮住
    v = _run([{"kind": "shape", "name": "ring", "at": _at(70, 50), "size_mm": [2, 2]}])
    assert _status(v, "data", "ring") == ["fail"]
    # 空白处的圆环不遮数据（斜线在 x = 82 mm 处已经结束，柱子在 66–74 mm）
    v = _run([{"kind": "shape", "name": "ring", "at": _at(86, 40), "size_mm": [3, 3]}])
    assert _status(v, "data", "ring") == ["pass"]


def test_text_over_a_tick_label_fails_text():
    # 刻度文字在画布上 x 11.6–17.2、y 38–40
    v = _run([{"kind": "text", "name": "t", "text": "x", "at": _at(14, 39), "attach": "center"}])
    assert _status(v, "text", "t") == ["fail"]


def test_arrow_tip_into_data_is_allowed_but_its_shaft_is_not():
    tip = {"anchor": {"element": {"panel": "P1", "gid": "axes_0.bar_0", "point": "n"}}}
    ok = _run([{"kind": "arrow", "name": "a", "from": {**tip, "offset_mm": [0, -8]}, "to": tip}])
    assert _status(ok, "data", "a") == ["pass"], ok["facts"]
    # 同一根箭头若从柱子底部穿过整根柱子指向顶部：杆遮住数据
    through = {"anchor": {"element": {"panel": "P1", "gid": "axes_0.bar_0", "point": "s"}}}
    bad = _run(
        [{"kind": "arrow", "name": "a", "from": {**through, "offset_mm": [0, 4]}, "to": tip}]
    )
    assert _status(bad, "data", "a") == ["fail"]


def test_label_anchored_to_its_arrow_does_not_count_as_a_clash():
    items = [
        {"kind": "arrow", "name": "a", "from": _at(85, 25), "to": _at(80, 30)},
        {
            "kind": "text",
            "name": "t",
            "text": "x",
            "at": {"anchor": {"item": {"name": "a", "point": "ne"}}},
        },
    ]
    v = _run(items)
    assert _status(v, "text", "a") == ["pass"] and _status(v, "text", "t") == ["pass"]


def test_off_page_fails_page():
    v = _run(
        [{"kind": "shape", "name": "s", "at": _at(119, 79), "size_mm": [4, 4], "attach": "nw"}]
    )
    assert _status(v, "page", "s") == ["fail"]


def test_journal_floors_from_preflight():
    profile = profiles.load(
        "journal-base-v1", journal={"min_line_width_pt": 0.5, "min_effective_font_size_pt": 6}
    )
    v = _run(
        [
            {
                "kind": "shape",
                "name": "thin",
                "at": _at(86, 40),
                "size_mm": [3, 3],
                "strokePt": 0.2,
            },
            {
                "kind": "text",
                "name": "tiny",
                "text": "x",
                "at": _at(60, 26),
                "attach": "nw",
                "sizePt": 4,
            },
        ],
        profile=profile,
    )
    assert _status(v, "journal", "thin") == ["fail"]
    assert _status(v, "journal", "tiny") == ["fail"]


def test_anchor_residual_is_measured_on_the_export_geometry():
    v = _run([{"kind": "arrow", "name": "a", "from": _at(85, 25), "to": _at(80, 30)}])
    res = [f["detail"]["residual_mm"] for f in v["facts"] if f["check"] == "anchor"]
    assert len(res) == 2 and max(res) <= vectorcheck.RESIDUAL_MM


@pytest.mark.parametrize("text", ["**", "n.s.", "p < 0.05", "n = 6"])
def test_statistical_text_needs_provenance(text):
    item = {"kind": "text", "name": "t", "text": text, "at": _at(60, 26)}
    r = vectorscene.solve([item], _panels(), measure=_measure)
    assert not r["ok"] and r["errors"][0]["code"] == "needs_confirmation"
    r = vectorscene.solve([{**item, "provenance": "user"}], _panels(), measure=_measure)
    assert r["ok"]


def test_item_anchor_link_does_not_depend_on_key_order():
    items = [
        {"kind": "arrow", "name": "a", "from": _at(85, 25), "to": _at(80, 30)},
        # point 写在 name 前面：配对仍按 name 认
        {
            "kind": "text",
            "name": "t",
            "text": "x",
            "at": {"anchor": {"item": {"point": "ne", "name": "a"}}},
        },
    ]
    v = _run(items)
    assert _status(v, "text", "a") == ["pass"] and _status(v, "text", "t") == ["pass"]


def test_text_family_must_match_the_figure():
    # 手写的图里刻度文字是衬线
    manifest = {
        **MANIFEST,
        "elements": MANIFEST["elements"]
        + [
            {
                "gid": "axes_0.yticklabels_1",
                "role": "ticklabel",
                "bbox": [0.02, 0.3, 0.07, 0.05],
                "editable": [{"prop": "fontfamily", "type": "enum", "value": "Times New Roman"}],
            },
        ],
    }
    panels = {
        "P1": {"panel": dict(PANEL), "draw_manifest": manifest, "requests": [], "data_points": []}
    }

    def run(item):
        solved = vectorscene.solve([item], panels, measure=_measure)
        checked = agentobjects.validate_new_objects(solved["objects"])
        return vectorcheck.check(solved, checked["objects"], panels, [item], page=PAGE)

    item = {"kind": "text", "name": "t", "text": "ok", "at": _at(60, 26), "attach": "nw"}
    assert _status(run(item), "style", "t") == ["pass"]  # 缺省跟着图走：衬线
    assert _status(run({**item, "fontFamily": "sans-serif"}), "style", "t") == ["fail"]


def test_arrowhead_and_stroke_count_for_the_page_check():
    """箭头贴着页面上边缘：中线在页内，但端型的一个顶点与描边伸出页外——要判出页（Codex #2201）。"""
    v = _run(
        [
            {
                "kind": "arrow",
                "name": "a",
                "from": _at(10, 0.1),
                "to": _at(20, 0.1),
                "headEnd": "triangle",
            }
        ]
    )
    assert _status(v, "page", "a") == ["fail"]
    # 离边缘留够端型的半宽：页内
    v = _run(
        [
            {
                "kind": "arrow",
                "name": "a",
                "from": _at(10, 2.0),
                "to": _at(20, 2.0),
                "headEnd": "triangle",
            }
        ]
    )
    assert _status(v, "page", "a") == ["pass"]


def test_arrowhead_over_a_tick_label_is_a_clash():
    """杆从刻度文字下方擦过（中线离文字底边 0.5 mm，杆本身碰不到），但端型的半宽约 1.2 mm，伸进文字——
    压字（Codex #2201）。刻度文字在画布 x 11.6–17.2、y 38–40。"""
    item = {
        "kind": "arrow",
        "name": "a",
        "from": _at(24, 40.5),
        "to": _at(15, 40.5),
        "headEnd": "triangle",
        "strokePt": 2.0,
    }
    assert _status(_run([item]), "text", "a") == ["fail"]
    # 同一根箭头不带端型：杆离文字 0.5 mm、半线宽 0.35 mm，不压字
    assert _status(_run([{**item, "head": "none", "headEnd": "none"}]), "text", "a") == ["pass"]


def test_short_arrow_between_two_anchored_ends_does_not_crash():
    """两端都锚在元素上、只有 1 mm 长：让出两头的尖端余量后没有取样点——判 pass，不抛错（Codex #2201）。"""
    tip = {"anchor": {"element": {"panel": "P1", "gid": "axes_0.bar_0", "point": "n"}}}
    v = _run([{"kind": "arrow", "name": "a", "from": {**tip, "offset_mm": [1.0, 0.0]}, "to": tip}])
    data = [f for f in v["facts"] if f["check"] == "data" and f.get("item") == "a"]
    assert (
        data
        and data[0]["status"] == "pass"
        and data[0]["detail"]["reason"] == "within_tip_allowance"
    )


def test_open_arrowhead_has_no_phantom_base():
    """开口 V 端型导出时不闭合（Codex #2201 复核给的例子）：10 pt 粗的水平箭头 (20, 40) → (40, 40)，开口端型长
    4 × 10 pt ≈ 14 mm、半宽 1.7 × 10 pt ≈ 6 mm，两臂从 (26, 34) / (26, 46) 收到尖端；V 口处 (26, 42.6) 放一个 3 pt 的小字（离杆的上沿与臂的内沿各约 0.2 mm），
    它离两条臂和杆（半线宽 1.76 mm）都远——只有把 V 口连成一条底边时才会「压字」。"""
    item = {
        "kind": "arrow",
        "name": "a",
        "from": _at(20, 40),
        "to": _at(40, 40),
        "headEnd": "open",
        "strokePt": 10,
    }
    label = {
        "kind": "text",
        "name": "t",
        "text": "x",
        "sizePt": 3,
        "attach": "center",
        "at": _at(26, 42.6),
    }
    v = _run([item, label])
    assert _status(v, "text", "a") == ["pass"], v["facts"]
