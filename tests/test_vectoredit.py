"""`engine/vectoredit`（ADR 0212 第 1 期）：选择器 + 补丁式写入（改字段、挪位置、删除）、复述、核对、一步撤销。

排版与假 `measure` 复用 `test_layoutannotate.py` 那一套（一张 80 × 40 mm 的子图在画布 (10, 20)，手写 manifest）。"""

from __future__ import annotations

import copy
import json
import re

import pytest

from tavotto.engine import atomicio, layoutannotate as la, vectoredit as ve
from test_layoutannotate import _doc, measure

MINE = [
    {
        "id": "a_user0001",
        "type": "arrow",
        "x": 30,
        "y": 5,
        "w": 20,
        "h": 5,
        "start": {"rx": 0, "ry": 0},
        "end": {"rx": 1, "ry": 1},
        "color": "#000000",
        "strokePt": 0.5,
        "head": "end",
        "name": "红箭头",
    },
    {
        "id": "t_user0001",
        "type": "text",
        "x": 95,
        "y": 5,
        "w": 15,
        "h": 4,
        "text": "note",
        "sizePt": 8,
        "bold": False,
        "align": "left",
        "color": "#000000",
    },
    {
        "id": "s_user0001",
        "type": "shape",
        "x": 95,
        "y": 30,
        "w": 10,
        "h": 10,
        "shape": "ellipse",
        "color": "#000000",
        "fill": None,
        "strokePt": 0.75,
    },
]


@pytest.fixture
def project(tmp_path):
    store = tmp_path / "tavottofile"
    store.mkdir()
    atomicio.write_json(store / "组图.json", _doc(MINE), indent=1)
    return tmp_path


def _path(project):
    return project / "tavottofile" / "组图.json"


def _objs(project):
    return {
        o["id"]: o
        for o in json.loads(_path(project).read_text(encoding="utf-8"))["canvases"][0]["objects"]
    }


def _base(project):
    return la.open_layout(project, "组图").revision


def _canvas(project):
    return la.open_layout(project, "组图").doc["canvases"][0]


def test_select_filters_vector_objects_only(project):
    c = _canvas(project)
    assert {o["id"] for o in ve.select(c)} == {
        "a_user0001",
        "t_user0001",
        "s_user0001",
    }  # 子图不在里面
    assert [o["id"] for o in ve.select(c, {"types": ["text"]})] == ["t_user0001"]
    assert [o["id"] for o in ve.select(c, {"name": "红"})] == ["a_user0001"]
    assert [o["id"] for o in ve.select(c, {"text": "no"})] == ["t_user0001"]
    assert [o["id"] for o in ve.select(c, {"within": [90, 25, 20, 20]})] == ["s_user0001"]
    assert ve.select(c, {"by_agent": True}) == []


def test_set_fields_echo_before_and_after_and_only_touch_named_fields(project):
    before = _objs(project)
    ops = [{"op": "set", "id": "a_user0001", "set": {"strokePt": 0.75, "color": "#cc0000"}}]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert plan["ok"], plan
    assert plan["echo"] == [
        "箭头红箭头（a_user0001，约在 40, 8 mm）：strokePt 0.5 → 0.75，color #000000 → #cc0000"
    ]
    out = ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    after = _objs(project)
    assert after["a_user0001"] == {**before["a_user0001"], "strokePt": 0.75, "color": "#cc0000"}
    assert (
        after["t_user0001"] == before["t_user0001"] and after["s_user0001"] == before["s_user0001"]
    )
    la.undo(project, "组图", out["undo_token"])
    assert _objs(project) == before


@pytest.mark.parametrize(
    "op,code,field",
    [
        ({"op": "set", "id": "a_user0001", "set": {"strokePt": 99}}, "out_of_range", "strokePt"),
        (
            {"op": "set", "id": "a_user0001", "set": {"sizePt": 8}},
            "bad_field",
            "sizePt",
        ),  # 箭头没有字号
        (
            {"op": "set", "id": "a_user0001", "set": {"groupPath": ["g_x"]}},
            "bad_field",
            "groupPath",
        ),  # 编组归第 2 期
        ({"op": "set", "id": "t_user0001", "set": {"color": "red"}}, "bad_value", "color"),
        ({"op": "set", "id": "Fig1", "set": {"x": 1}}, "unknown_object", None),
        (
            {"op": "set", "id": "figures/fig1.pdf", "set": {"x": 1}},
            "not_vector",
            None,
        ),  # 子图不归这里改
        ({"op": "rotate", "id": "a_user0001"}, "bad_op", None),
        ({"op": "move", "id": "a_user0001"}, "bad_op", None),
    ],
)
def test_bad_ops_reject_the_whole_batch(project, op, code, field):
    before = _path(project).read_bytes()
    ops = [{"op": "set", "id": "t_user0001", "set": {"sizePt": 9}}, op]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert not plan["ok"]
    err = plan["errors"][0]
    assert err["code"] == code and err["index"] == 1 and err.get("field") == field
    assert _path(project).read_bytes() == before


def test_move_by_and_move_tip_to_an_anchor(project):
    ops = [
        {"op": "move", "id": "t_user0001", "by_mm": [-2, 1]},
        # 箭头尖（end，框的右下角）对到子图左上角往里 (40, 10)：整支平移、不改形
        {
            "op": "move",
            "id": "a_user0001",
            "attach": "to",
            "to": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [40, 10]},
        },
    ]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert plan["ok"], plan
    ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    o = _objs(project)
    assert (o["t_user0001"]["x"], o["t_user0001"]["y"]) == (93.0, 6.0)
    a = o["a_user0001"]
    assert (a["x"] + a["w"], a["y"] + a["h"]) == pytest.approx((10 + 40, 20 + 10))
    assert (a["w"], a["h"]) == (20, 5)
    assert any("to 对到 (50.00, 30.00) mm" in e for e in plan["echo"])


def test_delete_and_hide(project):
    ops = [
        {"op": "delete", "id": "s_user0001"},
        {"op": "set", "id": "t_user0001", "set": {"hidden": True}},
    ]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert plan["ok"] and plan["echo"][0].startswith("删除 形状")
    out = ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    o = _objs(project)
    assert "s_user0001" not in o and o["t_user0001"]["hidden"] is True
    assert out["deleted"] == ["s_user0001"] and out["changed"] == ["t_user0001"]


def test_edits_are_checked_like_new_elements(project):
    """改后的对象照 0208 核对：挪出页面 / 压到别的字就不写。没动的对象不重新报。"""
    before = _path(project).read_bytes()
    ops = [{"op": "move", "id": "s_user0001", "by_mm": [30, 0]}]  # 页面宽 120：挪到 125–135，出页
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert not plan["ok"] and [f["check"] for f in plan["failed"]] == ["page"]
    out = ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    assert out["written"] is False and _path(project).read_bytes() == before
    # 压到画布上没动的那段字
    ops = [
        {
            "op": "move",
            "id": "s_user0001",
            "to": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [87.5, -13]},
        }
    ]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    clash = [f for f in plan["failed"] if f["check"] == "text"]
    assert clash and "canvas:t_user0001" in clash[0]["detail"]["with"]


def test_apply_must_carry_the_plans_digest(project):
    before = _path(project).read_bytes()
    ops = [{"op": "set", "id": "t_user0001", "set": {"sizePt": 9}}]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    changed = [{"op": "set", "id": "t_user0001", "set": {"sizePt": 10}}]
    with pytest.raises(la.LayoutError) as e:
        ve.commit(
            project,
            "组图",
            changed,
            measure,
            base_revision=plan["base_revision"],
            echo_digest_seen=plan["echo_digest"],
        )
    assert e.value.code == "echo_mismatch" and _path(project).read_bytes() == before


def test_stale_base_is_refused(project):
    ops = [{"op": "set", "id": "t_user0001", "set": {"sizePt": 9}}]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    doc["canvases"][0]["name"] = "别处改过"
    atomicio.write_json(_path(project), doc, indent=1)
    with pytest.raises(la.LayoutError) as e:
        ve.commit(
            project,
            "组图",
            ops,
            measure,
            base_revision=plan["base_revision"],
            echo_digest_seen=plan["echo_digest"],
        )
    assert e.value.code == "layout_changed"


def test_an_incomplete_object_is_reported_not_crashed(project):
    """外部工具写进来的对象缺了必填键：改它可以，核对如实标「量不了」，不让整批崩。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    doc["canvases"][0]["objects"].append(
        {"id": "t_bare0001", "type": "text", "x": 5, "y": 5, "w": 5, "h": 3, "text": "x"}
    )
    atomicio.write_json(_path(project), doc, indent=1)
    ops = [{"op": "set", "id": "t_bare0001", "set": {"sizePt": 9}}]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert plan["ok"] and plan["unverified"][0]["detail"]["reason"] == "incomplete_object"


# ------------------------------------------------------------------ 第 2 期：排（对齐 / 分布 / 层级 / 编组 / 旋转）

PAGE = {"w": 120, "h": 80}


def _t(oid, x, y, w=10.0, h=4.0, **kw):
    return {
        "id": oid,
        "type": "text",
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "text": "t",
        "sizePt": 8,
        "color": "#000000",
        **kw,
    }


def _run(objs, ops, **kw):
    return ve.apply_ops(objs, ops, **kw)


def _xs(res):
    return {o["id"]: (o["x"], o["y"]) for o in res["objects"]}


def _err(objs, ops, **kw):
    with pytest.raises(ve.EditError) as e:
        _run(objs, ops, **kw)
    return e.value.as_dict()


def test_align_to_selection_edges_and_echo():
    objs = [_t("a", 10, 5, 10), _t("b", 40, 20, 20), _t("c", 25, 50, 6)]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "left", "to": "selection"}])
    assert [o["x"] for o in r["objects"]] == [10, 10, 10]
    assert r["echo"] == ["左对齐 3 个对象到选区左边（x = 10.0 mm）"]
    assert {c["id"] for c in r["changes"]} == {"b", "c"}  # a 本来就在线上：不算改
    assert {o["id"] for o in r["changed"]} == {"b", "c"}
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "right"}])  # to 缺省 = 选区
    assert [o["x"] for o in r["objects"]] == [50, 40, 54]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "hcenter"}])
    assert [o["x"] for o in r["objects"]] == [30, 25, 32]  # 选区 10–60，中线 35
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "top"}])
    assert [o["y"] for o in r["objects"]] == [5, 5, 5]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "bottom"}])
    assert [o["y"] for o in r["objects"]] == [50, 50, 50]  # 选区 5–54，底 = 54，h = 4
    assert "底对齐 3 个对象到选区下边（y = 54.0 mm）" in r["echo"][0]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "vcenter"}])
    assert [o["y"] for o in r["objects"]] == [27.5, 27.5, 27.5]  # 选区 5–54，中线 29.5


def test_align_uses_the_visible_box_of_rotated_objects():
    # 文字 15 × 4 转 90°：看得见的框 4 × 15，中心不变 → 左缘 = x + 7.5 − 2
    objs = [
        {
            "id": "a",
            "type": "arrow",
            "x": 30,
            "y": 5,
            "w": 20,
            "h": 5,
            "start": {"rx": 0, "ry": 0},
            "end": {"rx": 1, "ry": 1},
        },
        _t("r", 95, 5, 15, 4, rotationDeg=90),
    ]
    r = _run(objs, [{"op": "align", "ids": ["a", "r"], "mode": "left"}])
    assert _xs(r)["r"] == (24.5, 5)  # 看得见的左缘 = 24.5 + 5.5 = 30
    assert r["objects"][1]["rotationDeg"] == 90 and r["objects"][1]["w"] == 15
    # 对到页面右边：看得见的右缘 = 120
    r = _run(objs, [{"op": "align", "ids": ["r"], "mode": "right", "to": "page"}], page=PAGE)
    assert _xs(r)["r"][0] + 7.5 + 2 == pytest.approx(120)
    # 竖直方向同理：看得见的上缘 = 7 − 7.5 = −0.5 → 顶对齐页面后上缘 = 0
    r = _run(objs, [{"op": "align", "ids": ["r"], "mode": "top", "to": "page"}], page=PAGE)
    assert _xs(r)["r"][1] + 2 - 7.5 == pytest.approx(0)


def test_align_references_key_panel_and_page():
    objs = [_t("a", 10, 5, 10), _t("b", 40, 20, 20), _t("c", 25, 50, 6)]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "right", "to": {"key": "a"}}])
    assert [o["x"] for o in r["objects"]] == [10, 0, 14]  # 关键对象 a 不动
    assert "到关键对象文字「t」右边（x = 20.0 mm）" in r["echo"][0]
    r = _run(objs, [{"op": "align", "ids": ["b"], "mode": "bottom", "to": "page"}], page=PAGE)
    assert _xs(r)["b"] == (40, 76)
    panels = {"P1": {"panel": {"x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}}}
    r = _run(
        objs,
        [{"op": "align", "ids": ["c"], "mode": "hcenter", "to": {"panel": "P1"}}],
        panels=panels,
    )
    assert _xs(r)["c"] == (47, 50)
    assert "到子图 P1 水平中线（x = 50.0 mm）" in r["echo"][0]


def test_align_skips_locked_but_counts_them_in_the_reference_box():
    objs = [_t("a", 10, 5, 10), _t("b", 40, 20, 20, locked=True), _t("c", 25, 50, 6)]
    r = _run(objs, [{"op": "align", "ids": ["a", "b", "c"], "mode": "right"}])
    assert [o["x"] for o in r["objects"]] == [50, 40, 54]  # 锁定的 b 不动，却撑着右边界 60
    assert r["echo"][0].endswith("；1 个锁定的没动")
    assert _err(
        [_t("a", 1, 1, locked=True), _t("b", 5, 5, locked=True)],
        [{"op": "align", "ids": ["a", "b"], "mode": "left"}],
    ) == {"code": "bad_op", "index": 0, "field": "locked"}


def test_align_moves_whole_groups_like_a_click_selection():
    objs = [
        _t("a", 10, 5, 10, groupId="g_1"),
        _t("b", 30, 5, 10, groupId="g_1"),
        _t("c", 50, 30, 10),
    ]
    r = _run(objs, [{"op": "align", "ids": ["a", "c"], "mode": "right"}])
    # 选中 a = 选中整组 {a, b}：右缘都对到选区右边 60
    assert [o["x"] for o in r["objects"]] == [50, 50, 50]


@pytest.mark.parametrize(
    "op,code,field",
    [
        ({"op": "align", "ids": ["a"], "mode": "middle"}, "bad_op", "mode"),
        ({"op": "align", "ids": ["a"], "mode": "left", "to": "nowhere"}, "bad_op", "to"),
        ({"op": "align", "ids": ["a"], "mode": "left", "to": "page"}, "bad_op", "to"),  # 没给页面
        ({"op": "align", "ids": ["a", "b"], "mode": "left", "to": {"key": "zz"}}, "bad_op", "to"),
        (
            {"op": "align", "ids": ["a"], "mode": "left", "to": {"panel": "P9"}},
            "unknown_object",
            "to",
        ),
        ({"op": "align", "ids": ["a"], "mode": "left", "to": {"key": "a"}}, "bad_op", "ids"),
        ({"op": "align", "ids": [], "mode": "left"}, "bad_op", "ids"),
        ({"op": "align", "ids": ["a", "a"], "mode": "left"}, "bad_op", "ids"),
        ({"op": "align", "ids": ["a", "nope"], "mode": "left"}, "unknown_object", "ids"),
        ({"op": "align", "ids": ["a", "P"], "mode": "left"}, "not_vector", "ids"),
        ({"op": "align", "ids": ["a"], "mode": "left", "extra": 1}, "bad_op", None),
        ({"op": "distribute", "ids": ["a", "b"], "axis": "h"}, "bad_op", "ids"),  # 等间距至少 3 个
        ({"op": "distribute", "ids": ["a"], "axis": "h", "gap_mm": 2}, "bad_op", "ids"),
        ({"op": "distribute", "ids": ["a", "b"], "axis": "x"}, "bad_op", "axis"),
        (
            {"op": "distribute", "ids": ["a", "b"], "axis": "h", "gap_mm": "2"},
            "bad_value",
            "gap_mm",
        ),
        (
            {"op": "distribute", "ids": ["a", "b"], "axis": "h", "gap_mm": 1e9},
            "out_of_range",
            "gap_mm",
        ),
        ({"op": "order", "id": "a", "to": "up"}, "bad_op", "to"),
        ({"op": "order", "id": "P", "to": "front"}, "not_vector", None),
        ({"op": "order", "id": "nope", "to": "front"}, "unknown_object", None),
        ({"op": "group", "ids": ["a"]}, "bad_op", "ids"),  # 不足两个单元
        ({"op": "group", "ids": ["a", "b"], "name": ""}, "bad_value", "name"),
        ({"op": "group", "ids": ["a", "P"]}, "not_vector", "ids"),
        ({"op": "ungroup", "group": "g_nope"}, "unknown_object", "group"),
        ({"op": "ungroup"}, "bad_op", "group"),
        ({"op": "rotate", "id": "a"}, "bad_op", None),
        ({"op": "rotate", "id": "a", "deg": "30"}, "bad_value", "deg"),
        ({"op": "rotate", "id": "a", "deg": True}, "bad_value", "deg"),
        ({"op": "rotate", "id": "a", "deg": 400}, "out_of_range", "deg"),
        ({"op": "rotate", "id": "P", "deg": 10}, "not_vector", None),
    ],
)
def test_arrange_bad_params_reject_the_whole_batch(op, code, field):
    objs = [
        _t("a", 1, 1),
        _t("b", 20, 1),
        {"id": "P", "type": "panel", "x": 0, "y": 0, "w": 5, "h": 5},
    ]
    ops = [{"op": "rotate", "id": "a", "deg": 10}, op]
    assert _err(objs, ops) == {"code": code, "index": 1, **({"field": field} if field else {})}


def test_distribute_equal_gaps_keep_both_ends_and_measure_visible_boxes():
    objs = [_t("a", 0, 0, 10), _t("m", 15, 9, 10), _t("c", 60, 20, 10)]
    r = _run(objs, [{"op": "distribute", "ids": ["a", "m", "c"], "axis": "h"}])
    assert _xs(r) == {"a": (0, 0), "m": (30, 9), "c": (60, 20)}  # 总宽 70、对象 30 → 间距 20
    assert r["echo"] == ["水平等间距分布 3 个对象（间距 20.0 mm，x 方向两端不动）"]
    assert {o["id"] for o in r["changed"]} == {"m"}
    # 中间那个转 90°：看得见的宽 4 → 间距 (70 − 24) / 2 = 23，它的框左缘落在 33
    objs[1]["rotationDeg"] = 90
    r = _run(objs, [{"op": "distribute", "ids": ["a", "m", "c"], "axis": "h"}])
    assert "间距 23.0 mm" in r["echo"][0]
    assert _xs(r)["m"][0] + 5 - 2 == pytest.approx(33)  # x + w/2 − 看得见的宽/2
    # 乱序给 id 也按位置排；竖向
    objs = [_t("a", 0, 0, 10, 10), _t("b", 5, 70, 10, 10), _t("c", 9, 12, 10, 10)]
    r = _run(objs, [{"op": "distribute", "ids": ["b", "c", "a"], "axis": "v"}])
    assert _xs(r)["c"] == (9, 35) and r["echo"][0].startswith(
        "垂直等间距分布 3 个对象（间距 25.0 mm"
    )


def test_distribute_by_gap_keeps_the_first_in_place():
    objs = [_t("a", 7, 0, 10), _t("b", 40, 3, 10), _t("c", 90, 6, 10)]
    r = _run(objs, [{"op": "distribute", "ids": ["c", "b", "a"], "axis": "h", "gap_mm": 2.5}])
    assert [o["x"] for o in r["objects"]] == [7, 19.5, 32]
    assert r["echo"] == ["水平按 2.5 mm 间距排 3 个对象（最左的不动）"]
    r = _run(objs[:2], [{"op": "distribute", "ids": ["a", "b"], "axis": "v", "gap_mm": 1}])
    assert _xs(r)["b"] == (40, 5)  # 竖向：a 占 0–4，b 贴在 5
    assert r["echo"][0].endswith("（最上的不动）")
    # 转过的对象按看得见的框：转 90° 的 a 看得见的宽 4（左缘 = 7 + 5 − 2 = 10）
    objs[0]["rotationDeg"] = 90
    r = _run(objs[:2], [{"op": "distribute", "ids": ["a", "b"], "axis": "h", "gap_mm": 1}])
    assert _xs(r)["a"][0] == 7 and _xs(r)["b"][0] == pytest.approx(40 + (10 + 4 + 1 - 40))
    assert (
        _err(
            [_t("a", 0, 0, locked=True), _t("b", 20, 0)],
            [{"op": "distribute", "ids": ["a", "b"], "axis": "h", "gap_mm": 1}],
        )["field"]
        == "locked"
    )


def test_order_front_back_forward_backward():
    def seq(to, oid, objs=None):
        objs = objs or [_t("a", 0, 0), _t("b", 0, 0), _t("c", 0, 0), _t("d", 0, 0)]
        r = _run(objs, [{"op": "order", "id": oid, "to": to}])
        return "".join(o["id"] for o in r["objects"]), r

    assert seq("front", "a")[0] == "bcda"
    assert seq("back", "c")[0] == "cabd"
    assert seq("forward", "a")[0] == "bacd"
    assert seq("backward", "c")[0] == "acbd"
    assert seq("forward", "d")[0] == "abcd" and seq("backward", "a")[0] == "abcd"
    order, r = seq("front", "a")
    assert r["echo"][0].startswith("把 文字「t」（a，") and r["echo"][0].endswith("移到最上层")
    assert r["reordered"] and r["order"] == list("bcda")
    # 动了位置的才算「改了」（核对只看它们）：a 从 0 到 3，b c d 各退一格但没被点名也算改了层级的矢量对象
    assert {c["id"] for c in r["changes"] if c["field"] == "z"} == {"a", "b", "c", "d"}
    assert {"id": "a", "field": "z", "before": 0, "after": 3} in r["changes"]
    assert not seq("front", "d")[1]["changes"] and not seq("front", "d")[1]["reordered"]
    # 组成员一起动：a b 同组，a 到最上层，b 跟着去，顺序不变
    objs = [
        _t("a", 0, 0, groupId="g_1"),
        _t("x", 0, 0),
        _t("b", 0, 0, groupId="g_1"),
        _t("y", 0, 0),
    ]
    o, r = seq("front", "a", copy.deepcopy(objs))
    assert o == "xyab" and r["echo"][0].endswith("移到最上层（连同同组的 1 个）")
    assert seq("backward", "b", copy.deepcopy(objs))[0] == "abxy"


def test_group_creates_one_layer_gathers_members_and_names_the_group():
    objs = [_t("a", 0, 0), _t("x", 0, 0), _t("b", 0, 0), _t("y", 0, 0)]
    ops = [{"op": "group", "ids": ["a", "b"], "name": "标注"}]
    r = _run(objs, ops)
    gid = r["objects"][1]["groupId"]
    assert re.fullmatch(r"g_[a-z0-9]{8}", gid)
    assert [o["id"] for o in r["objects"]] == [
        "x",
        "a",
        "b",
        "y",
    ]  # 成员挪成连续一段，落在最上面那个（b）的位置
    assert r["echo"] == ["编组 2 个对象：组名 标注"]
    assert r["reordered"]
    assert gid == _run(objs, ops)["objects"][1]["groupId"]  # 方案与写入各算一遍要得到同一个组 id
    for o in r["objects"]:
        assert ("groupPath" not in o) and (o.get("groupId") == gid) == (o["id"] in "ab")
    assert {c["field"] for c in r["changes"]} == {"groupId"}


def test_group_nests_and_caps_depth():
    objs = [_t(f"x{n}", n, 0) for n in range(1, 7)]
    ops = [{"op": "group", "ids": ["x1", "x2"]}]
    for n in range(3, 6):
        ops.append({"op": "group", "ids": ["x1", f"x{n}"]})  # x1 所在的整组 + 新对象
    r = _run(objs, ops)
    x1 = r["objects"][0]
    assert len(x1["groupPath"]) == 4 and x1["groupId"] == x1["groupPath"][-1]
    assert x1["groupPath"][0] != x1["groupPath"][1]
    assert len(r["objects"][2]["groupPath"]) == 3  # x3 在第二次编组时进组，之后又被两层包住
    err = _err(objs, [*ops, {"op": "group", "ids": ["x1", "x6"]}])
    assert err == {"code": "out_of_range", "index": 4, "field": "groupPath"}
    # 同一组里的两个对象只算一个单元：不足两个单元
    two = _run(objs, ops[:1])["objects"]
    assert _err(two, [{"op": "group", "ids": ["x1", "x2"]}]) == {
        "code": "bad_op",
        "index": 0,
        "field": "ids",
    }
    # 组里混着子图：整批拒（这一步会改到它）
    mixed = [
        _t("a", 0, 0, groupId="g_1"),
        {"id": "P", "type": "panel", "x": 0, "y": 0, "w": 5, "h": 5, "groupId": "g_1"},
        _t("b", 9, 9),
    ]
    assert _err(mixed, [{"op": "group", "ids": ["a", "b"]}])["field"] == "group_member"


def test_ungroup_peels_one_layer_and_releases_masks():
    objs = [_t("a", 0, 0), _t("b", 5, 0), _t("c", 9, 0)]
    r = _run(
        objs,
        [
            {"op": "group", "ids": ["a", "b"]},
            {"op": "group", "ids": ["a", "c"]},
        ],
    )
    outer = r["objects"][0]["groupPath"][0]
    inner = r["objects"][0]["groupId"]
    r2 = _run(r["objects"], [{"op": "ungroup", "group": outer}])
    assert r2["echo"] == [f"取消编组 {outer}（3 个对象升到上一层）"]
    a, b, c = r2["objects"]
    assert a["groupId"] == b["groupId"] == inner and "groupPath" not in a and "groupId" not in c
    r3 = _run(r2["objects"], [{"op": "ungroup", "group": inner}])
    assert all("groupId" not in o and "groupPath" not in o for o in r3["objects"])
    assert {(c["id"], c["field"]) for c in r3["changes"]} == {("a", "groupId"), ("b", "groupId")}
    # 拆剪切组：蒙版还原成普通可见对象
    mask = {
        "id": "m",
        "type": "shape",
        "shape": "rect",
        "x": 0,
        "y": 0,
        "w": 9,
        "h": 9,
        "color": "#000",
        "groupId": "g_c",
        "clipMask": True,
        "hidden": True,
    }
    r4 = _run([mask, _t("a", 0, 0, groupId="g_c")], [{"op": "ungroup", "group": "g_c"}])
    m = r4["objects"][0]
    assert "clipMask" not in m and "hidden" not in m and "groupId" not in m
    # 蒙版所在组里已没有被裁的对象（拆掉外层后它那一层组里只剩它自己）：蒙版不再藏着，这一层组也拿掉
    r5 = _run(
        [{**mask, "groupId": "g_c", "groupPath": ["g_o", "g_c"]}, _t("a", 0, 0, groupId="g_o")],
        [{"op": "ungroup", "group": "g_o"}],
    )
    # 外面还有别的对象时，被拆那一层自己的蒙版照样还原（不能靠「孤立蒙版」兜底）
    r6 = _run(
        [
            {**mask, "groupId": "g_c", "groupPath": ["g_o", "g_c"]},
            _t("b", 0, 0, groupId="g_c", groupPath=["g_o", "g_c"]),
            _t("a", 0, 0, groupId="g_o"),
        ],
        [{"op": "ungroup", "group": "g_c"}],
    )
    m6 = r6["objects"][0]
    assert "clipMask" not in m6 and "hidden" not in m6 and m6["groupId"] == "g_o"
    m5 = r5["objects"][0]
    assert "clipMask" not in m5 and "hidden" not in m5 and "groupId" not in m5


def test_rotate_sets_absolute_angle_normalized_around_the_centre():
    objs = [_t("a", 10, 5, 20, 4), _t("b", 0, 0, rotationDeg=45)]
    r = _run(objs, [{"op": "rotate", "id": "a", "deg": 30}])
    a = r["objects"][0]
    assert (a["x"], a["y"], a["w"], a["h"], a["rotationDeg"]) == (10, 5, 20, 4, 30)
    assert r["echo"][0].endswith("：转到 30°（原 0°）")
    assert r["changes"] == [{"id": "a", "field": "rotationDeg", "before": None, "after": 30.0}]
    assert _run(objs, [{"op": "rotate", "id": "a", "deg": 270}])["objects"][0]["rotationDeg"] == -90
    assert (
        _run(objs, [{"op": "rotate", "id": "a", "deg": -180}])["objects"][0]["rotationDeg"] == 180
    )
    assert (
        _run(objs, [{"op": "rotate", "id": "a", "deg": 12.34}])["objects"][0]["rotationDeg"] == 12.3
    )
    r = _run(objs, [{"op": "rotate", "id": "b", "deg": 0}])  # 0 度 = 不写这个字段
    assert "rotationDeg" not in r["objects"][1] and r["echo"][0].endswith("转到 0°（原 45°）")
    assert r["changes"][0]["after"] == 0
    assert (
        _run(objs, [{"op": "rotate", "id": "b", "deg": 360}])["objects"][1].get("rotationDeg")
        is None
    )
    assert _run(objs, [{"op": "rotate", "id": "b", "deg": 45}])["changes"] == []


def test_arrange_ops_roundtrip_through_propose_commit_and_undo(project):
    before = _path(project).read_bytes()
    ops = [
        {"op": "group", "ids": ["a_user0001", "s_user0001"], "name": "一对"},
        {"op": "order", "id": "a_user0001", "to": "front"},
        {"op": "rotate", "id": "t_user0001", "deg": 15},
    ]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    assert plan["ok"], plan["failed"]
    out = ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    assert out["written"]
    doc = json.loads(_path(project).read_text(encoding="utf-8"))["canvases"][0]["objects"]
    ids = [o["id"] for o in doc]
    assert ids == [
        "figures/fig1.pdf",
        "t_user0001",
        "a_user0001",
        "s_user0001",
    ]  # 编组把 a、s 并成一段（t 让到下面）；order front 点名 a = 整组一起到顶
    by = {o["id"]: o for o in doc}
    assert by["a_user0001"]["groupId"] == by["s_user0001"]["groupId"]
    assert by["t_user0001"]["rotationDeg"] == 15 and "groupId" not in by["t_user0001"]
    la.undo(project, "组图", out["undo_token"])
    assert _path(project).read_bytes() == before


# ------------------------------------------------------------------ 第 3 期：几何（内核 = 前端同一份 vecops.wasm）

_K = 0.5522847498


def _circle(oid, cx, cy, r, **kw):
    """四个平滑锚点的圆（比例坐标）；描边 1 pt、填充红。"""
    nodes = []
    for px, py, tx, ty in ((1, 0.5, 0, 1), (0.5, 1, -1, 0), (0, 0.5, 0, -1), (0.5, 0, 1, 0)):
        k = _K / 2
        nodes.append(
            {
                "p": [px, py],
                "in": [px - tx * k, py - ty * k],
                "out": [px + tx * k, py + ty * k],
                "smooth": True,
            }
        )
    return {
        "id": oid,
        "type": "path",
        "x": cx - r,
        "y": cy - r,
        "w": 2 * r,
        "h": 2 * r,
        "subpaths": [{"closed": True, "nodes": nodes}],
        "strokePt": 1,
        "color": "#112233",
        "fill": "#ff0000",
        **kw,
    }


def _line(oid, pts, **kw):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    return {
        "id": oid,
        "type": "path",
        "x": x0,
        "y": y0,
        "w": w,
        "h": h,
        "subpaths": [
            {
                "closed": False,
                "nodes": [{"p": [(x - x0) / (w or 1), (y - y0) / (h or 1)]} for x, y in pts],
            }
        ],
        "strokePt": 2,
        "color": "#0000ff",
        "fill": None,
        **kw,
    }


def _two_circles(**kw):
    return [
        _circle("p_back", 20, 20, 10, name="后", **kw),
        _circle("p_front", 28, 20, 10, name="前", fill="#00ff00", **kw),
    ]


def _by(res):
    return {o["id"]: o for o in res["objects"]}


def test_pathfinder_unite_keeps_the_front_style_id_and_name_at_the_top_position():
    objs = [_t("below", 0, 0, 5), *_two_circles(), _t("above", 1, 1, 5)]
    r = _run(objs, [{"op": "pathfinder", "ids": ["p_front", "p_back"], "mode": "unite"}])
    assert [o["id"] for o in r["objects"]] == ["below", "p_front", "above"]
    out = _by(r)["p_front"]
    assert (out["name"], out["fill"]) == ("前", "#00ff00") and out["type"] == "path"
    assert abs(out["x"] - 10) < 0.02 and abs(out["w"] - 28) < 0.02  # 两圆并起来的范围
    assert r["deleted"] == ["p_back"] and r["changed"] == [out]
    assert r["echo"] == ["合并 2 个路径（p_back、p_front）→ 1 个路径（p_front）"]


def test_pathfinder_result_lands_where_the_topmost_source_was_not_the_lowest():
    a, b = _two_circles()
    objs = [a, _t("mid", 0, 0, 5), b, _t("top", 1, 1, 5)]  # 两个源之间隔着 mid
    r = _run(objs, [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"}])
    assert [o["id"] for o in r["objects"]] == ["mid", "p_front", "top"]  # 在 mid 的上面，不是下面


def test_pathfinder_minus_front_uses_the_back_style():
    r = _run(
        _two_circles(), [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "minus_front"}]
    )
    assert [o["id"] for o in r["objects"]] == ["p_back"]
    assert _by(r)["p_back"]["fill"] == "#ff0000" and r["deleted"] == ["p_front"]


def test_pathfinder_divide_gives_each_original_its_id_once_and_mints_ids_deterministically():
    ops = [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "divide"}]
    a, b = _run(_two_circles(), ops), _run(_two_circles(), ops)
    ids = [o["id"] for o in a["objects"]]
    assert len(ids) == 3 and {"p_back", "p_front"} <= set(ids) and a["deleted"] == []
    assert ids == [o["id"] for o in b["objects"]]  # 方案与写入各算一遍：新 id 必须逐位一致
    new = next(i for i in ids if i not in ("p_back", "p_front"))
    assert re.fullmatch(r"p_[a-z0-9]{8}", new) and _by(a)[new].get("name") is None
    assert [c for c in a["changes"] if c.get("added")] == [{"id": new, "added": True}]
    assert {c["id"] for c in a["changes"] if c.get("replaced")} == {"p_back", "p_front"}


def test_pathfinder_result_stays_in_the_sources_common_group():
    objs = _two_circles(groupId="g_one")
    r = _run(objs, [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"}])
    assert _by(r)["p_front"]["groupId"] == "g_one"
    objs[1]["groupId"] = "g_two"
    r = _run(objs, [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"}])
    assert "groupId" not in _by(r)["p_front"]


def test_pathfinder_without_overlap_is_an_error_and_deletes_nothing():
    far = [_circle("a", 10, 10, 5), _circle("b", 80, 10, 5)]
    assert _err(far, [{"op": "pathfinder", "ids": ["a", "b"], "mode": "intersect"}]) == {
        "code": "bad_op",
        "index": 0,
        "field": "empty_result",
    }


@pytest.mark.parametrize(
    "op,field",
    [
        ({"op": "pathfinder", "ids": ["p_back"], "mode": "unite"}, "ids"),
        ({"op": "pathfinder", "ids": ["p_back", "p_back"], "mode": "unite"}, "ids"),
        ({"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "nope"}, "mode"),
        ({"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite", "x": 1}, None),
    ],
)
def test_pathfinder_bad_params(op, field):
    e = _err(_two_circles(), [op])
    assert e["code"] == "bad_op" and e.get("field") == field


def test_geometry_ops_take_only_unlocked_visible_paths():
    shape = {**_t("s", 0, 0, 5), "type": "shape", "shape": "rect", "fill": "#fff", "strokePt": 1}
    objs = [
        *_two_circles(),
        shape,
        _circle("lk", 5, 5, 3, locked=True),
        _circle("hd", 5, 5, 3, hidden=True),
    ]
    two = lambda other: [{"op": "pathfinder", "ids": ["p_back", other], "mode": "unite"}]  # noqa: E731
    assert _err(objs, two("s")) == {"code": "bad_op", "index": 0, "field": "needs_path"}
    assert _err(objs, two("lk"))["field"] == "locked"
    assert _err(objs, two("hd"))["field"] == "hidden"
    assert _err(objs, two("ghost"))["code"] == "unknown_object"
    assert _err([*objs, _t("tx", 0, 0, 1)], two("tx"))["code"] == "not_vector"
    assert _err(objs, [{"op": "offset", "id": "s", "delta_mm": 1}])["field"] == "needs_path"


def test_offset_adds_one_new_path_right_above_and_leaves_the_original():
    objs = _two_circles()
    r = _run(objs, [{"op": "offset", "id": "p_back", "delta_mm": 2}])
    ids = [o["id"] for o in r["objects"]]
    assert ids[0] == "p_back" and ids[2] == "p_front" and len(ids) == 3
    new = _by(r)[ids[1]]
    assert _by(r)["p_back"] == objs[0]  # 原对象逐字不动
    assert abs(new["w"] - 24) < 0.05 and new["fill"] == "#ff0000" and "name" not in new
    assert new["type"] == "path" and new["id"] == ids[1]
    assert r["changed"] == [new] and "往外 2 mm" in r["echo"][0] and ids[1] in r["echo"][0]
    inward = _run(objs, [{"op": "offset", "id": "p_back", "delta_mm": -2}])
    assert abs(_by(inward)[[o["id"] for o in inward["objects"]][1]]["w"] - 16) < 0.05


def test_offset_join_overrides_only_the_geometry_not_the_new_objects_style():
    sq = _line("sq", [(0, 0), (10, 0), (10, 10), (0, 10)], fill="#ffffff")
    sq["subpaths"][0]["closed"] = True
    miter = _run([sq], [{"op": "offset", "id": "sq", "delta_mm": 2, "join": "miter"}])
    rnd = _run([sq], [{"op": "offset", "id": "sq", "delta_mm": 2, "join": "round"}])
    m, n = miter["objects"][1], rnd["objects"][1]
    assert len(m["subpaths"][0]["nodes"]) < len(
        n["subpaths"][0]["nodes"]
    )  # 斜接 4 个角，圆角每角多出弧段
    assert "lineJoin" not in m  # 原对象没写拐角，新对象也不写


@pytest.mark.parametrize(
    "op,code,field",
    [
        ({"op": "offset", "id": "p_back", "delta_mm": 0}, "bad_value", "delta_mm"),
        ({"op": "offset", "id": "p_back", "delta_mm": "2"}, "bad_value", "delta_mm"),
        ({"op": "offset", "id": "p_back", "delta_mm": 1e9}, "out_of_range", "delta_mm"),
        ({"op": "offset", "id": "p_back", "delta_mm": 1, "join": "x"}, "bad_value", "join"),
        ({"op": "simplify", "id": "p_back", "tolerance_pt": 0}, "bad_value", "tolerance_pt"),
        ({"op": "outline_stroke", "id": "p_back", "extra": 1}, "bad_op", None),
    ],
)
def test_geometry_bad_values(op, code, field):
    e = _err(_two_circles(), [op])
    assert (e["code"], e.get("field")) == (code, field)


def test_outline_stroke_without_fill_replaces_the_object_in_place():
    ln = _line("ln", [(0, 0), (30, 0)], name="线", strokeOpacity=0.5)
    r = _run([ln], [{"op": "outline_stroke", "id": "ln"}])
    out = r["objects"][0]
    assert [o["id"] for o in r["objects"]] == ["ln"] and out["name"] == "线"
    assert out["fill"] == "#0000ff" and out["stroke"] is False and out["fillOpacity"] == 0.5
    assert out["subpaths"][0]["closed"] is True and "strokeOpacity" not in out
    assert abs(out["h"] - 2 * 25.4 / 72) < 0.02  # 线宽 2 pt 拓成的带高（mm）
    assert r["deleted"] == [] and r["echo"] == [
        "轮廓化描边 路径线（ln，约在 15, 0 mm）：描边变成填充轮廓（同一个对象，id 不变）"
    ]


def test_outline_stroke_with_fill_turns_the_stroke_off_and_adds_the_outline_above():
    r = _run(_two_circles(), [{"op": "outline_stroke", "id": "p_back"}])
    ids = [o["id"] for o in r["objects"]]
    assert ids[0] == "p_back" and ids[2] == "p_front" and len(ids) == 3
    assert _by(r)["p_back"]["stroke"] is False and _by(r)["p_back"]["fill"] == "#ff0000"
    ring = _by(r)[ids[1]]
    assert ring["fill"] == "#112233" and ring["stroke"] is False and "name" not in ring
    assert set(r["changed"][i]["id"] for i in range(2)) == {"p_back", ids[1]}


@pytest.mark.parametrize(
    "extra,field",
    [
        ({"dash": "dashed"}, "stroke:dash"),
        ({"dashPattern": [3, 2]}, "stroke:dash"),
        ({"headEnd": "triangle"}, "stroke:head"),
        ({"strokeAlign": "inside"}, "stroke:strokeAlign"),
        ({"stroke": False}, "no_stroke"),
    ],
)
def test_outline_stroke_says_what_it_cannot_do_yet(extra, field):
    assert (
        _err([_circle("c", 10, 10, 5, **extra)], [{"op": "outline_stroke", "id": "c"}])["field"]
        == field
    )


def test_simplify_drops_redundant_anchors_in_place_and_says_so_when_nothing_to_drop():
    pts = [(i * 1.0, 0.0) for i in range(21)]  # 共线的一串点
    ln = _line("dense", pts, rotationDeg=30, name="n")
    r = _run([ln], [{"op": "simplify", "id": "dense"}])
    out = r["objects"][0]
    assert len(out["subpaths"][0]["nodes"]) < 21 and out["id"] == "dense"
    assert (out["rotationDeg"], out["name"], out["color"]) == (30, "n", "#0000ff")
    assert r["echo"][0].startswith("简化 路径n（dense") and "锚点 21 →" in r["echo"][0]
    again = _run([out], [{"op": "simplify", "id": "dense"}])
    assert again["changes"] == [] and "没有可省的锚点" in again["echo"][0]
    assert again["objects"][0] == out


def test_kernel_unavailable_only_blocks_the_geometry_ops(monkeypatch):
    from tavotto.engine import vecops

    monkeypatch.setattr(vecops, "available", lambda: False)
    objs = [*_two_circles(), _t("t", 1, 1, 5)]
    for op in (
        {"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"},
        {"op": "offset", "id": "p_back", "delta_mm": 1},
        {"op": "outline_stroke", "id": "p_back"},
        {"op": "simplify", "id": "p_back"},
    ):
        assert _err(objs, [op]) == {"code": "kernel_unavailable", "index": 0}
    r = _run(
        objs,
        [{"op": "move", "id": "t", "by_mm": [2, 0]}, {"op": "rotate", "id": "p_back", "deg": 10}],
    )
    assert _by(r)["t"]["x"] == 3 and _by(r)["p_back"]["rotationDeg"] == 10
    # 校验在前：坏参数照报原来的错，不被「内核不可用」盖住
    assert _err(objs, [{"op": "offset", "id": "p_back", "delta_mm": 0}])["code"] == "bad_value"


def test_a_batch_can_chain_geometry_with_other_ops():
    r = _run(
        _two_circles(),
        [
            {"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"},
            {"op": "offset", "id": "p_front", "delta_mm": 1},
            {"op": "set", "id": "p_front", "set": {"strokePt": 2}},
        ],
    )
    assert len(r["objects"]) == 2 and _by(r)["p_front"]["strokePt"] == 2
    assert len(r["echo"]) == 3


@pytest.fixture
def geo_project(tmp_path):
    store = tmp_path / "tavottofile"
    store.mkdir()
    atomicio.write_json(store / "组图.json", _doc([*MINE, *_two_circles()]), indent=1)
    return tmp_path


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "divide"}],
        [{"op": "pathfinder", "ids": ["p_back", "p_front"], "mode": "unite"}],
        [
            {"op": "offset", "id": "p_back", "delta_mm": 1.5},
            {"op": "outline_stroke", "id": "p_front"},
        ],
    ],
)
def test_geometry_ops_roundtrip_through_propose_commit_and_undo(geo_project, ops):
    before = _path(geo_project).read_bytes()
    plan = ve.propose(geo_project, "组图", ops, measure, base_revision=_base(geo_project))
    assert plan["ok"], plan.get("failed") or plan.get("errors")
    out = ve.commit(
        geo_project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    assert out["written"]
    written = json.loads(_path(geo_project).read_text(encoding="utf-8"))["canvases"][0]["objects"]
    # 写进文件的 == 方案里算出来的（含新长出来的对象与顺序）
    assert [o["id"] for o in written] == plan["order"]
    assert {o["id"]: o for o in written} == {o["id"]: o for o in plan["objects"]}
    assert set(plan["deleted"]) == set(out["deleted"])
    la.undo(geo_project, "组图", out["undo_token"])
    assert _path(geo_project).read_bytes() == before


def test_moving_a_rotated_arrow_puts_its_visible_tip_on_target(project):
    """转过 90° 的 20 mm 水平箭头：尖端对到 (50, 30)，**画出来**的尖端就在那里（Codex #2220 的例子）。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    for o in doc["canvases"][0]["objects"]:
        if o["id"] == "a_user0001":
            o.update(
                x=30,
                y=5,
                w=20,
                h=0.0001,
                start={"rx": 0, "ry": 0},
                end={"rx": 1, "ry": 0},
                rotationDeg=90,
            )
    atomicio.write_json(_path(project), doc, indent=1)
    ops = [
        {
            "op": "move",
            "id": "a_user0001",
            "attach": "to",
            "to": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [40, 10]},
        }
    ]
    plan = ve.propose(project, "组图", ops, measure, base_revision=_base(project))
    ve.commit(
        project,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    a = _objs(project)["a_user0001"]
    # 尖端在框里是右端中点；绕框中心顺时针转 90° → 画出来在框中心正下方 w/2 处
    cx, cy = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    assert (cx, cy + a["w"] / 2) == pytest.approx((50, 30), abs=1e-3)


def test_rotation_counts_for_the_page_check(project):
    """有填充的椭圆 (95, 1, 20, 10) 转 90°：画出来的顶在 −4 mm，出页（Codex #2220 的例子）；不转则在页内。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    for o in doc["canvases"][0]["objects"]:
        if o["id"] == "t_user0001":
            o["y"] = 60  # 文字挪开：这条只看出页
    for o in doc["canvases"][0]["objects"]:
        if o["id"] == "s_user0001":
            o.update(x=95, y=1, w=20, h=10, fill="#88ccee")
    atomicio.write_json(_path(project), doc, indent=1)
    ok = ve.propose(
        project,
        "组图",
        [{"op": "set", "id": "s_user0001", "set": {"strokePt": 1}}],
        measure,
        base_revision=_base(project),
    )
    assert ok["ok"], ok["failed"]
    bad = ve.propose(
        project,
        "组图",
        [{"op": "set", "id": "s_user0001", "set": {"rotationDeg": 90}}],
        measure,
        base_revision=_base(project),
    )
    assert [f["check"] for f in bad["failed"]] == ["page"]


def test_curved_paths_are_checked_along_the_curve_not_just_their_anchors(project):
    """两个锚点都在页内、手柄把弧线拉出页面上沿：判出页（只看锚点会放过，Codex #2220）。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    for o in doc["canvases"][0]["objects"]:
        if o["id"] == "t_user0001":
            o["y"] = 60  # 文字挪开：这条只看出页
    doc["canvases"][0]["objects"].append(
        {
            "id": "p_curve001",
            "type": "path",
            "x": 95,
            "y": 10,
            "w": 20,
            "h": 5,
            "strokePt": 0.5,
            "color": "#000000",
            "fill": None,
            "subpaths": [
                {
                    "closed": False,
                    "nodes": [{"p": [0, 0], "out": [0.3, -4]}, {"p": [1, 0], "in": [0.7, -4]}],
                }
            ],
        }
    )
    atomicio.write_json(_path(project), doc, indent=1)
    plan = ve.propose(
        project,
        "组图",
        [{"op": "set", "id": "p_curve001", "set": {"strokePt": 0.75}}],
        measure,
        base_revision=_base(project),
    )
    assert [f["check"] for f in plan["failed"]] == ["page"]


def test_a_filled_path_covering_text_is_a_clash(project):
    """有填充的闭合路径整块盖住没动的那段字：压字（只看轮廓线会放过）。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    doc["canvases"][0]["objects"].append(
        {
            "id": "p_blob0001",
            "type": "path",
            "x": 90,
            "y": 2,
            "w": 25,
            "h": 10,
            "strokePt": 0.5,
            "color": "#000000",
            "fill": "#ffffff",
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}],
                }
            ],
        }
    )
    atomicio.write_json(_path(project), doc, indent=1)
    plan = ve.propose(
        project,
        "组图",
        [{"op": "set", "id": "p_blob0001", "set": {"strokePt": 0.75}}],
        measure,
        base_revision=_base(project),
    )
    clash = [f for f in plan["failed"] if f["check"] == "text"]
    assert clash and "canvas:t_user0001" in clash[0]["detail"]["with"]


def test_rotating_a_filled_circle_does_not_count_its_bounding_box_corners(project):
    """有填充的圆 (95, 2, 20, 20) 转 45°：画出来没变、仍在页内——不许因为外接框的角伸出页面就判出页（Codex #2220）。"""
    doc = json.loads(_path(project).read_text(encoding="utf-8"))
    for o in doc["canvases"][0]["objects"]:
        if o["id"] == "s_user0001":
            o.update(x=95, y=2, w=20, h=20, fill="#88ccee")
        if o["id"] == "t_user0001":
            o["y"] = 60
    atomicio.write_json(_path(project), doc, indent=1)
    plan = ve.propose(
        project,
        "组图",
        [{"op": "set", "id": "s_user0001", "set": {"rotationDeg": 45}}],
        measure,
        base_revision=_base(project),
    )
    assert plan["ok"], plan["failed"]
