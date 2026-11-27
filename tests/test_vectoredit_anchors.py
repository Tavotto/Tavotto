"""`engine/vectoredit` 的路径锚点操作（ADR 0212 第 4 期）：校验、复述、记账、落位、场景锚点清单。几何与前端逐数一致由
`test_pathedit_golden.py` 看护；这里看操作层自己的规矩。"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import atomicio, layoutannotate as la, vectoredit as ve
from test_layoutannotate import _doc, measure
from test_vectoredit import (
    MINE,
    _base,
    _by,
    _circle,
    _err,
    _legacy_layout_file_form,  # noqa: F401 — 夹具：钉旧 <名>.json 形态（ADR 0211 第二期 PR 3）
    _line,
    _path,
    _run,
    _t,
    _two_circles,
)


def A(sp, i):
    return {"sp": sp, "i": i}


def _open3():
    return _line("ln", [(0, 0), (10, 0), (20, 10)], name="折线")


def test_every_anchor_op_is_registered_and_dispatched():
    assert set(ve._ANCHOR_OPS) <= set(ve._OPS)  # noqa: SLF001


def test_addresses_are_validated_strictly():
    ln = _open3()
    base = {"op": "move_anchors", "id": "ln", "by_mm": [1, 1]}
    cases = [
        ({"anchors": [A(0, 3)]}, ("bad_value", "anchors:unknown")),
        ({"anchors": [A(1, 0)]}, ("bad_value", "anchors:unknown")),
        ({"anchors": [A(0, -1)]}, ("bad_value", "anchors:unknown")),
        ({"anchors": [A(0, 1), A(0, 1)]}, ("bad_value", "anchors:duplicate")),
        ({"anchors": []}, ("bad_value", "anchors")),
        ({"anchors": [{"sp": 0}]}, ("bad_value", "anchors")),
        ({"anchors": [{"sp": 0, "i": 1.0}]}, ("bad_value", "anchors")),
        ({"anchors": [{"sp": True, "i": 1}]}, ("bad_value", "anchors")),
        ({"anchors": [{"sp": 0, "i": 1, "x": 1}]}, ("bad_value", "anchors")),
        ({"anchors": "0:1"}, ("bad_value", "anchors")),
        ({"anchors": [A(0, 1)], "by_mm": [0, 0]}, ("bad_value", "by_mm")),
        ({"anchors": [A(0, 1)], "by_mm": [1]}, ("bad_value", "by_mm")),
        ({"anchors": [A(0, 1)], "by_mm": [float("nan"), 1]}, ("bad_value", "by_mm")),
        ({"anchors": [A(0, 1)], "by_mm": [1e9, 1]}, ("out_of_range", "by_mm")),
        ({"anchors": [A(0, 1)], "extra": 1}, ("bad_op", None)),
    ]
    for patch, (code, field) in cases:
        e = _err([ln], [{**base, **patch}])
        assert (e["code"], e.get("field")) == (code, field), patch


def test_only_unlocked_visible_paths_are_accepted():
    op = {"op": "reverse_path", "id": "x"}
    assert _err([_t("x", 0, 0)], [op])["code"] == "not_vector"
    assert _err([_line("x", [(0, 0), (1, 1)], locked=True)], [op]) == {
        "code": "bad_op",
        "index": 0,
        "field": "locked",
    }
    assert _err([_line("x", [(0, 0), (1, 1)], hidden=True)], [op])["field"] == "hidden"
    shape = {
        "id": "x",
        "type": "shape",
        "shape": "ellipse",
        "x": 0,
        "y": 0,
        "w": 5,
        "h": 5,
        "color": "#000000",
        "fill": None,
        "strokePt": 1,
    }
    assert _err([shape], [op])["field"] == "needs_path"
    assert _err([], [op])["code"] == "unknown_object"


def test_move_anchors_moves_the_page_delta_even_when_the_path_is_rotated():
    ln = _line("ln", [(0, 0), (10, 0), (10, 10)], rotationDeg=90)
    before = ve.path_anchors(ln)
    r = _run([ln], [{"op": "move_anchors", "id": "ln", "anchors": [A(0, 2)], "by_mm": [4, -3]}])
    after = ve.path_anchors(_by(r)["ln"])
    assert after[2]["at_mm"] == pytest.approx(
        [before[2]["at_mm"][0] + 4, before[2]["at_mm"][1] - 3], abs=0.01
    )
    for a, b in zip(after[:2], before[:2]):  # 没点名的不动
        assert a["at_mm"] == pytest.approx(b["at_mm"], abs=0.01)


def test_the_echo_names_who_and_the_counts_and_the_new_shape():
    r = _run([_open3()], [{"op": "add_anchors", "id": "ln"}])
    assert r["echo"] == [
        "加锚点 路径折线（ln，约在 10, 5 mm） 在每一段的中点：锚点 3 → 5（现为 sp0 开放 5 个）；新锚点 sp0:i1、sp0:i3"
    ]
    assert r["changes"][-1] == {"id": "ln", "field": "nodes", "before": 3, "after": 5, "op": 0}
    assert [o["id"] for o in r["changed"]] == ["ln"]


def test_add_anchors_reports_skipped_open_end_and_stays_deterministic():
    r = _run([_open3()], [{"op": "add_anchors", "id": "ln", "anchors": [A(0, 2)]}])
    assert (
        r["changes"] == [] and "没有变化" in r["echo"][0] and "sp0:i2 后面没有线段" in r["echo"][0]
    )
    again = _run([_open3()], [{"op": "add_anchors", "id": "ln", "anchors": [A(0, 2)]}])
    assert again["echo"] == r["echo"]


def test_add_at_seg_t_validates_t_and_segment():
    ln = _open3()
    for at, field in (
        ({"sp": 0, "seg": 1, "t": 0}, "at"),
        ({"sp": 0, "seg": 1, "t": 1}, "at"),
        ({"sp": 0, "seg": 1}, "at"),
        ({"sp": 0, "seg": 2, "t": 0.5}, "at:unknown"),  # 开放路径的最后一个锚点后面没有段
        ({"sp": 3, "seg": 0, "t": 0.5}, "at:unknown"),
    ):
        assert _err([ln], [{"op": "add_anchors", "id": "ln", "at": at}])["field"] == field, at
    assert (
        _err(
            [ln],
            [
                {
                    "op": "add_anchors",
                    "id": "ln",
                    "at": {"sp": 0, "seg": 0, "t": 0.5},
                    "anchors": [A(0, 0)],
                }
            ],
        )["field"]
        == "anchors"
    )
    r = _run([ln], [{"op": "add_anchors", "id": "ln", "at": {"sp": 0, "seg": 0, "t": 0.25}}])
    assert ve.path_anchors(_by(r)["ln"])[1]["at_mm"] == [2.5, 0.0]


def test_a_later_op_addresses_the_state_the_earlier_one_left():
    ops = [
        {"op": "add_anchors", "id": "ln", "at": {"sp": 0, "seg": 0, "t": 0.5}},  # 新锚点 sp0:i1
        {"op": "move_anchors", "id": "ln", "anchors": [A(0, 1)], "by_mm": [0, 5]},
        {"op": "delete_anchors", "id": "ln", "anchors": [A(0, 3)]},  # 原来的 i2 现在是 i3
    ]
    r = _run([_open3()], ops)
    pts = [a["at_mm"] for a in ve.path_anchors(_by(r)["ln"])]
    assert pts == [[0.0, 0.0], [5.0, 5.0], [10.0, 0.0]]
    assert (
        _err([_open3()], [*ops, {"op": "delete_anchors", "id": "ln", "anchors": [A(0, 3)]}])[
            "index"
        ]
        == 3
    )


def test_deleting_every_anchor_deletes_the_object_and_later_ops_cannot_find_it():
    ln = _open3()
    ops = [{"op": "delete_anchors", "id": "ln", "anchors": [A(0, 0), A(0, 1), A(0, 2)]}]
    r = _run([ln, _t("t", 1, 1)], ops)
    assert [o["id"] for o in r["objects"]] == ["t"] and r["deleted"] == ["ln"]
    assert r["changes"] == [{"id": "ln", "deleted": True, "op": 0}] and "一并删除" in r["echo"][0]
    assert _err([ln], [*ops, {"op": "reverse_path", "id": "ln"}])["code"] == "unknown_object"


def test_delete_says_when_a_subpath_disappears_and_numbers_shift():
    obj = _line("m", [(0, 0), (5, 0)])
    obj["subpaths"].append(
        {"closed": False, "nodes": [{"p": [0, 1]}, {"p": [1, 1]}, {"p": [1, 0.5]}]}
    )
    r = _run([obj], [{"op": "delete_anchors", "id": "m", "anchors": [A(0, 0)]}])
    assert "子路径 sp0 不足 2 个锚点，已去掉，后面的编号前移" in r["echo"][0]
    assert "现为 sp0 开放 3 个" in r["echo"][0]


def test_convert_explicit_target_and_toggle_default():
    c = _circle("c", 10, 10, 5)
    corner = _run(
        [c], [{"op": "convert_anchors", "id": "c", "anchors": [A(0, 0), A(0, 1)], "to": "corner"}]
    )
    nodes = _by(corner)["c"]["subpaths"][0]["nodes"]
    assert [bool(n.get("smooth")) for n in nodes] == [False, False, True, True]
    assert "→ 尖角" in corner["echo"][0]
    toggled = _run(
        [c], [{"op": "convert_anchors", "id": "c", "anchors": [A(0, 0)]}]
    )  # 全是平滑点 → 尖角
    assert "→ 尖角" in toggled["echo"][0]
    again = _run(
        corner["objects"],
        [{"op": "convert_anchors", "id": "c", "anchors": [A(0, 0)], "to": "corner"}],
    )
    assert "没有变化" in again["echo"][0] and again["changes"] == []
    assert (
        _err([c], [{"op": "convert_anchors", "id": "c", "anchors": [A(0, 0)], "to": "round"}])[
            "field"
        ]
        == "to"
    )


def test_handle_drag_validates_and_can_break_smooth():
    c = _circle("c", 10, 10, 5)
    op = {"op": "move_handle", "id": "c", "anchor": A(0, 0), "which": "out", "to_mm": [20, 20]}
    assert _err([c], [{**op, "which": "left"}])["field"] == "which"
    assert _err([c], [{**op, "break_smooth": "yes"}])["field"] == "break_smooth"
    assert _err([c], [{**op, "anchor": A(0, 9)}])["field"] == "anchor:unknown"
    r = _run([c], [{**op, "break_smooth": True}])
    assert "断开平滑" in r["echo"][0] and not _by(r)["c"]["subpaths"][0]["nodes"][0].get("smooth")


def test_result_beyond_the_field_limits_is_out_of_range_not_silently_written():
    ln = _open3()
    move = {"op": "move_anchors", "id": "ln", "anchors": [A(0, 2)], "by_mm": [9000, 0]}
    e = _err([ln], [move, move])  # 第二次把框撑到 18000 mm
    assert e["code"] == "out_of_range" and e["index"] == 1 and e["field"] in ("w", "x")


def test_cut_makes_new_paths_above_with_derived_ids_and_no_name():
    ln = _line("ln", [(0, 0), (10, 0), (20, 0), (30, 0)], name="线", groupId="g_1")
    objs = [_t("below", 0, 0), ln, _t("above", 1, 1)]
    ops = [{"op": "cut_path", "id": "ln", "anchors": [A(0, 1), A(0, 2)]}]
    r = _run(objs, ops)
    ids = [o["id"] for o in r["objects"]]
    assert ids[0] == "below" and ids[1] == "ln" and ids[3] == "above" and ids[2].startswith("p_")
    new = _by(r)[ids[2]]
    assert "name" not in new and new["groupId"] == "g_1" and new["color"] == ln["color"]
    assert {c["id"] for c in r["changes"] if c.get("added")} == {ids[2]}
    assert _by(r)["ln"]["name"] == "线"
    assert _run(objs, ops)["echo"] == r["echo"]  # 方案与写入各算一遍，复述与 id 必须逐位一致
    assert "原对象" in r["echo"][0] and ids[2] in r["echo"][0]


def test_cut_at_an_end_is_a_reported_noop():
    r = _run([_open3()], [{"op": "cut_path", "id": "ln", "anchors": [A(0, 0)]}])
    assert r["changes"] == [] and "未改动" in r["echo"][0] and r["changed"] == []


def test_join_within_one_object_validates_ends():
    c = _circle("c", 10, 10, 5)
    assert (
        _err([c], [{"op": "join_paths", "id": "c", "anchors": [A(0, 0), A(0, 2)]}])["field"]
        == "anchors:not_end"
    )
    ln = _open3()
    assert (
        _err([ln], [{"op": "join_paths", "id": "ln", "anchors": [A(0, 0), A(0, 1)]}])["field"]
        == "anchors:not_end"
    )
    assert (
        _err([ln], [{"op": "join_paths", "id": "ln", "anchors": [A(0, 0)]}])["field"] == "anchors"
    )
    assert (
        _err([ln], [{"op": "join_paths", "id": "ln", "anchors": [A(0, 0), A(0, 0)]}])["field"]
        == "anchors:duplicate"
    )
    r = _run([ln], [{"op": "join_paths", "id": "ln", "anchors": [A(0, 0), A(0, 2)]}])
    assert "闭合" in r["echo"][0] and "现为 sp0 闭合 3 个" in r["echo"][0]


def test_join_two_objects_near_the_node_budget_counts_the_result_once(monkeypatch):
    """合成之后的锚点数没超总量预算就能连上：被并掉的那个不能和合成结果一起算（Codex #2272）"""
    real = ve.agentobjects.table()
    monkeypatch.setattr(
        ve.agentobjects, "table", lambda: {**real, "budgets": {**real["budgets"], "max_nodes": 30}}
    )
    a = _line("a", [(i, 0) for i in range(12)])
    b = _line("b", [(20 + i, 0) for i in range(12)])
    r = _run([a, b], [{"op": "join_paths", "ids": ["a", "b"]}])
    assert [o["id"] for o in r["objects"]] == ["b"]
    assert len(r["objects"][0]["subpaths"][0]["nodes"]) == 24


def test_join_two_objects_keeps_the_last_id_and_deletes_the_other():
    a = _line("a", [(0, 0), (10, 0)], name="甲", color="#ff0000")
    b = _line("b", [(11, 0), (20, 5)], name="乙", color="#0000ff")
    ops = [{"op": "join_paths", "ids": ["a", "b"]}]
    r = _run([a, _t("mid", 1, 1), b], ops)
    assert [o["id"] for o in r["objects"]] == ["mid", "b"] and r["deleted"] == ["a"]
    assert _by(r)["b"]["color"] == "#0000ff" and _by(r)["b"]["name"] == "乙"
    assert len(_by(r)["b"]["subpaths"][0]["nodes"]) == 4
    assert "留 b" in r["echo"][0] and "删去 a" in r["echo"][0]
    r2 = _run([a, b], [{"op": "join_paths", "ids": ["b", "a"]}])
    assert [o["id"] for o in r2["objects"]] == ["a"]
    for bad in (["a"], ["a", "a"], ["a", "b", "c"], "ab"):
        assert _err([a, b], [{"op": "join_paths", "ids": bad}])["field"] == "ids"
    closed = _circle("c", 5, 5, 2)
    assert _err([a, closed], [{"op": "join_paths", "ids": ["a", "c"]}])["field"] == "no_open_ends"


def test_reverse_swaps_handles_and_keeps_the_curve():
    c = _circle("c", 10, 10, 5, headEnd="triangle")
    r = _run([c], [{"op": "reverse_path", "id": "c"}])
    out = _by(r)["c"]
    n0, m0 = c["subpaths"][0]["nodes"], out["subpaths"][0]["nodes"]
    assert (
        m0[0]["p"] == n0[-1]["p"] and m0[0]["in"] == n0[-1]["out"] and m0[0]["out"] == n0[-1]["in"]
    )
    assert out["headEnd"] == "triangle" and "端型字段没换" in r["echo"][0]
    back = _run(r["objects"], [{"op": "reverse_path", "id": "c"}])
    assert _by(back)["c"]["subpaths"] == c["subpaths"]


def test_compound_make_and_release_roundtrip_ids_and_fill_rule():
    objs = [*_two_circles(), _t("t", 0, 0)]
    r = _run(objs, [{"op": "make_compound", "ids": ["p_front", "p_back"]}])
    assert [o["id"] for o in r["objects"]] == ["p_back", "t"]  # 沿用最靠后的那个
    comp = _by(r)["p_back"]
    assert comp["fillRule"] == "evenodd" and len(comp["subpaths"]) == 2 and comp["name"] == "后"
    assert r["deleted"] == ["p_front"]
    rel = _run(r["objects"], [{"op": "release_compound", "id": "p_back"}])
    assert [o["id"] for o in rel["objects"]][0] == "p_back" and len(rel["objects"]) == 3
    assert all("fillRule" not in o for o in rel["objects"] if o["type"] == "path")
    assert (
        _err(rel["objects"], [{"op": "release_compound", "id": "p_back"}])["field"]
        == "not_compound"
    )
    for bad in (["p_back"], ["p_back", "p_back"], "x"):
        assert _err(objs, [{"op": "make_compound", "ids": bad}])["field"] == "ids"


def test_scene_lists_anchors_in_page_space_with_a_budget(project=None):
    ln = _line("ln", [(0, 0), (10, 0), (10, 10)], rotationDeg=90)
    b = ve._brief(ln)  # noqa: SLF001
    assert b["anchor_count"] == 3 and b["subpaths"] == [{"sp": 0, "closed": False, "count": 3}]
    rows = ve.path_anchors(ln)
    assert [(r["sp"], r["i"]) for r in rows] == [(0, 0), (0, 1), (0, 2)]
    assert all(set(r) == {"sp", "i", "at_mm", "smooth"} for r in rows)  # 直线锚点没有手柄字段
    circ = ve.path_anchors(_circle("c", 10, 10, 5))
    assert all("in_mm" in r and "out_mm" in r and r["smooth"] for r in circ)


@pytest.fixture
def anchors_project(tmp_path):
    store = tmp_path / "tavottofile"
    store.mkdir()
    big = _line("big", [(i * 0.5, (i % 7) * 1.0) for i in range(90)])
    mid = _line("mid", [(i, i % 3) for i in range(50)])
    many = [_line(f"l{k}", [(0, k), (5, k + 1), (9, k)]) for k in range(150)]
    atomicio.write_json(store / "组图.json", _doc([*MINE, big, mid, *many]), indent=1)
    return tmp_path


def test_scene_anchor_list_obeys_the_per_path_and_per_scene_caps(anchors_project):
    out = ve.read_scene(anchors_project, "组图", measure)
    by = {o["id"]: o for o in out["vector_objects"]}
    assert "anchors" not in by["big"] and by["big"]["anchor_count"] == 90  # 90 > 60：只给计数
    assert by["big"]["subpaths"] == [{"sp": 0, "closed": False, "count": 90}]
    assert len(by["mid"]["anchors"]) == 50
    listed = sum(len(o.get("anchors", [])) for o in out["vector_objects"])
    assert listed <= ve.ANCHORS_PER_SCENE and "anchors_note" in out
    only = ve.read_scene(anchors_project, "组图", measure, select={"ids": ["big"]})
    assert len(only["vector_objects"][0]["anchors"]) == 90 and "anchors_note" not in only


def test_cut_and_join_roundtrip_through_propose_commit_and_undo(tmp_path):
    store = tmp_path / "tavottofile"
    store.mkdir()
    ln = _line("ln", [(60, 20), (70, 20), (80, 20), (90, 20)], name="线")
    far = _line("far", [(92, 20), (100, 23)], name="远")
    atomicio.write_json(store / "组图.json", _doc([*MINE, ln, far]), indent=1)
    before = _path(tmp_path).read_bytes()
    ops = [
        {"op": "cut_path", "id": "ln", "anchors": [A(0, 1)]},
        {"op": "join_paths", "ids": ["ln", "far"]},
    ]
    plan = ve.propose(tmp_path, "组图", ops, measure, base_revision=_base(tmp_path))
    assert plan["ok"], plan.get("failed") or plan.get("errors")
    out = ve.commit(
        tmp_path,
        "组图",
        ops,
        measure,
        base_revision=plan["base_revision"],
        echo_digest_seen=plan["echo_digest"],
    )
    assert out["written"]
    written = json.loads(_path(tmp_path).read_text(encoding="utf-8"))["canvases"][0]["objects"]
    assert [o["id"] for o in written] == plan["order"]
    assert {o["id"]: o for o in written} == {o["id"]: o for o in plan["objects"]}
    assert "far" in {o["id"] for o in written} and "ln" in set(out["deleted"])
    la.undo(tmp_path, "组图", out["undo_token"])
    assert _path(tmp_path).read_bytes() == before
