"""`engine/vectoredit`（ADR 0212 第 1 期）：选择器 + 补丁式写入（改字段、挪位置、删除）、复述、核对、一步撤销。

排版与假 `measure` 复用 `test_layoutannotate.py` 那一套（一张 80 × 40 mm 的子图在画布 (10, 20)，手写 manifest）。"""

from __future__ import annotations

import json

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
