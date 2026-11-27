"""桌面改图助手新增标注（ADR 0208 第 ③ 期 3a）：补丁词汇的 `add` 操作，只用子图外框求解。

盯的是：外框能解的条目（box / text 标注 / bracket / arrow / connector，锚在 panel 方位点上）真能出新对象、id 确定、带持久锚点；
数据 / 元素锚点（还没量过）回 `needs_measurement` 而不是崩；统计标注来源核对（原话里核得到才收、`data` 不收、`confirmed` 才放行）；
`add` 与编辑操作混在一批；逐条勾选（只留 `add`）重算新对象 id 不变；端点把 `instruction_text` / `confirmed` 带进来。
求解 / 对象校验 / 复述是 0208 那一份（`vectorscene` / `agentobjects` / `layoutannotate.describe`），这里不测它们本身。"""

from __future__ import annotations

import copy

import pytest

from tavotto.engine import vectoredit as ve, vectorplan as vp
from test_layoutannotate import (
    _doc,
    measure as stub_measure,
)  # stub：数据 (x, y) ∈ [0, 10]² 按坐标轴框线性换算
from test_vectoredit import MINE


def _canvas():
    return copy.deepcopy(_doc(MINE)["canvases"][0])


def _add(*items):
    return {"op": "add", "items": list(items)}


FRAME = {"kind": "box", "name": "frame", "fit": [{"panel": {"id": "P1"}}]}
TAG = {
    "kind": "text",
    "name": "tag",
    "text": "(a)",
    "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}, "offset_mm": [2, -3]},
    "attach": "sw",
}
BRACKET = {
    "kind": "bracket",
    "name": "br",
    "between": [
        {"anchor": {"panel": {"id": "P1", "point": "nw"}}},
        {"anchor": {"panel": {"id": "P1", "point": "ne"}}},
    ],
    "side": "above",
}
ARROW = {
    "kind": "arrow",
    "name": "pt",
    "from": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [8, -6]},
    "to": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [-2, 2]},
}
CONNECTOR = {
    "kind": "connector",
    "name": "cn",
    "from": {"anchor": {"panel": {"id": "P1", "point": "e"}}, "offset_mm": [2, 0]},
    "to": {"anchor": {"panel": {"id": "P1", "point": "e"}}, "offset_mm": [12, 8]},
}
DATA_ARROW = {
    "kind": "arrow",
    "name": "peak",
    "from": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [8, -6]},
    "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 10, "y": 0.8}}},
}


def _stat(prov=None, text="**"):
    it = {
        "kind": "text",
        "name": "sig",
        "text": text,
        "at": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [0, -3]},
    }
    if prov:
        it["provenance"] = prov
    return it


def _plan(ops, context=None):
    return ve.propose_memory(_canvas(), ops, context=context)


@pytest.mark.parametrize(
    "item,kind",
    [(FRAME, "shape"), (TAG, "text"), (BRACKET, "path"), (ARROW, "arrow"), (CONNECTOR, "path")],
)
def test_items_the_panel_frame_can_solve_become_new_objects(item, kind):
    res = _plan([_add(item)])
    assert res["ok"], res
    added = [c for c in res["changes"] if c.get("added")]
    assert len(added) == 1
    new = next(o for o in res["changed"] if o["id"] == added[0]["id"])
    assert new["type"] == kind
    if item is not FRAME:  # box 框的是目标不是锚点，0208 没给它存锚点
        assert new["anchor"]["by"] == "agent"  # 持久锚点（0208 §六）
    assert added[0]["op"] == 0  # 6c 逐条勾选按 op 下标
    assert any(e.startswith("新增 ") for e in res["echo"])


def test_new_ids_are_deterministic_and_stable_when_other_steps_are_unchecked():
    edit = {"op": "set", "id": "t_user0001", "set": {"sizePt": 10}}
    both = _plan([edit, _add(FRAME, TAG)])
    again = _plan([edit, _add(FRAME, TAG)])
    only = _plan([_add(FRAME, TAG)])  # 取消勾选前一步后用剩下的 ops 重算
    ids = lambda r: [c["id"] for c in r["changes"] if c.get("added")]  # noqa: E731
    assert ids(both) == ids(again) == ids(only) and len(ids(both)) == 2
    assert both["echo_digest"] == again["echo_digest"]
    # 与编辑混在一批：编辑照常生效，`op` 下标各自对得上
    assert {c["op"] for c in both["changes"] if c.get("added")} == {1}
    assert {c["op"] for c in both["changes"] if c.get("field") == "sizePt"} == {0}


def test_added_objects_land_on_top():
    res = _plan([_add(FRAME)])
    assert res["order"][-1] == next(c["id"] for c in res["changes"] if c.get("added"))


@pytest.mark.parametrize(
    "item",
    [
        DATA_ARROW,
        {**FRAME, "fit": [{"element": {"panel": "P1", "gid": "axes_0.lines_0"}}]},
        {**TAG, "at": {"anchor": {"element": {"panel": "P1", "gid": "axes_0", "point": "n"}}}},
    ],
)
def test_data_and_element_anchors_need_measurement(item):
    res = _plan([_add(item)])
    assert not res["ok"] and res["errors"][0]["code"] == "needs_measurement"
    assert res["errors"][0]["field"] == item["name"]


def test_a_dry_run_drops_only_the_unmeasurable_step():
    out = vp.validate_ops(
        {"ops": [_add(FRAME), _add(DATA_ARROW), {"op": "delete", "id": "t_user0001"}]}, _canvas()
    )
    assert [o["op"] for o in out["ops"]] == ["add", "delete"]
    (u,) = out["unhandled"]
    assert u["code"] == "needs_measurement" and "需要" not in u["reason"] and u["op"]["op"] == "add"


def test_other_solver_errors_are_reported_with_the_item_name():
    bad_point = {**TAG, "at": {"anchor": {"panel": {"id": "P1", "point": "zz"}}}}
    res = _plan([_add(bad_point)])
    assert res["errors"] == [{"code": "bad_point", "index": 0, "field": "tag"}]
    assert _plan([_add({**FRAME, "fit": ["P1"]})])["errors"][0]["code"] == "bad_item"
    assert _plan([_add(TAG, TAG)])["errors"][0]["code"] == "duplicate_name"
    assert _plan([_add()])["errors"][0]["code"] == "bad_op"
    assert _plan([{"op": "add", "items": [FRAME], "x": 1}])["errors"][0]["code"] == "bad_op"


# ------------------------------------------------------------------ 统计标注


def _ctx(said, confirmed=()):
    return {"instruction": said, "confirmed": tuple(confirmed)}


def test_a_star_needs_the_users_own_words():
    ok = _plan([_add(_stat("user"))], _ctx("在 P1 上方加 ** 表示显著"))
    assert ok["ok"], ok
    assert any("统计标注" in e for e in ok["echo"])
    for case in (
        _plan([_add(_stat(None))], _ctx("在 P1 上方加 **")),  # 没写来源
        _plan([_add(_stat("user"))], _ctx("给 P1 加个标注")),  # 原话里没有
        _plan([_add(_stat("user"))], _ctx(None)),  # ops 模式没带原话
    ):
        assert not case["ok"] and case["errors"][0] == {
            "code": "needs_confirmation",
            "index": 0,
            "field": "sig",
        }


def test_data_provenance_is_refused_in_the_assistant():
    res = _plan([_add(_stat("data"))], _ctx("加 **"))
    assert res["errors"][0]["code"] == "provenance_data"


def test_confirmed_by_the_user_on_the_card_passes_but_only_from_the_request():
    res = _plan([_add(_stat(None))], _ctx(None, ["sig"]))
    assert res["ok"], res
    # 模型自己在条目里写 confirmed 没有用
    sneaky = {**_stat("user"), "confirmed": True}
    assert not _plan([_add(sneaky)], _ctx("没说星号"))["ok"]


def test_the_plugin_path_without_context_keeps_the_old_meaning():
    # 插件的 edit 路径没有「用户原话」：只核写没写来源（旧口径），宿主另行确认
    assert _plan([_add(_stat("user"))])["ok"]


def test_p_values_and_n_equals_and_ns_count_as_statistical():
    for text in ("p < 0.05", "n = 5", "n.s."):
        res = _plan([_add(_stat("user", text))], _ctx("加个标注"))
        assert res["errors"][0]["code"] == "needs_confirmation", text
    assert _plan([_add(_stat("user", "p<0.05"))], _ctx("标上 p < 0.05"))["ok"]  # 空白不敏感


def test_validate_ops_passes_the_instruction_through():
    obj = {"ops": [_add(_stat("user"))]}
    assert vp.validate_ops(obj, _canvas(), "加 **")["ops"]
    out = vp.validate_ops(obj, _canvas(), "加个标注")
    assert not out["ops"] and out["unhandled"][0]["code"] == "needs_confirmation"


def test_prompt_snapshot_lists_the_panel_frames():
    snap = vp.snapshot(_canvas(), [])
    assert snap["panels"]["P1"]["measured"] is False and len(snap["panels"]["P1"]["box_mm"]) == 4
    assert "add" in ve.OPS_DOC and "provenance" in ve.OPS_DOC


# ------------------------------------------------------------------ 3b：量过的子图上，数据 / 元素锚点可用


PEAK = {
    "kind": "arrow",
    "name": "peak",
    "from": {"anchor": {"panel": {"id": "P1", "point": "ne"}}, "offset_mm": [8, -6]},
    "to": {"anchor": {"data": {"panel": "P1", "axes": "axes_0", "x": 5, "y": 5}}},
}


def _unavailable(code):
    from tavotto.engine import layoutannotate as la

    def measure(panel, requests):
        raise la.PanelUnavailable(code)

    return measure


def test_a_measured_panel_takes_data_and_element_anchors():
    res = ve.propose_memory(_canvas(), [_add(PEAK)], measure=stub_measure)
    assert res["ok"] or res["errors"] == [{"code": "checks_failed"}], res["errors"]
    new = next(o for o in res["changed"] if o.get("type") == "arrow")
    assert new["anchor"]["anchors"][1]["data"]["panel"]  # 数据锚点落了盘（子图换成子图对象 id）
    el = {
        "kind": "text",
        "name": "lab",
        "text": "Treatment",
        "at": {"anchor": {"element": {"panel": "P1", "gid": "axes_0.lines_0", "point": "ne"}}},
    }
    assert ve.propose_memory(_canvas(), [_add(el)], measure=stub_measure)["changes"]
    assert res["scene_fingerprint"]


def test_an_unmeasurable_panel_falls_back_to_the_frame_with_its_reason():
    res = ve.propose_memory(_canvas(), [_add(PEAK)], measure=_unavailable("measure_timeout"))
    assert res["errors"][0]["code"] == "needs_measurement"
    assert res["unmeasured"] == {"P1": "measure_timeout"}
    ok = ve.propose_memory(_canvas(), [_add(FRAME)], measure=_unavailable("composed_from_snapshot"))
    assert ok["ok"] and ok["unmeasured"] == {"P1": "composed_from_snapshot"}  # 外框照样能框


def test_the_prompt_carries_the_measured_scene_and_says_which_panels_are_not():
    measured = vp.measure_scene(_canvas(), stub_measure)
    assert measured["fingerprint"] and "annotations" not in measured["scene"]
    _s, user = vp.build_prompt("指一下峰", _canvas(), [], max_chars=48_000, measured=measured)
    import json as _json

    scene = _json.loads(user.split("<scene>\n", 1)[1].split("\n</scene>", 1)[0])
    assert scene["panels"]["P1"]["measured"] is True
    assert scene["scene"]["panels"][0]["axes"][0]["gid"] == "axes_0"
    bad = vp.measure_scene(_canvas(), _unavailable("runtime_panel"))
    _s, user = vp.build_prompt("指一下峰", _canvas(), [], max_chars=48_000, measured=bad)
    scene = _json.loads(user.split("<scene>\n", 1)[1].split("\n</scene>", 1)[0])
    assert scene["panels"]["P1"] == {
        "box_mm": scene["panels"]["P1"]["box_mm"],
        "measured": False,
        "unmeasured": "runtime_panel",
    }


def test_the_dry_run_measures_once_with_the_add_items_data_requests():
    seen = []

    def spy(panel, requests):
        seen.append(requests)
        return stub_measure(panel, requests)

    out = vp.validate_ops({"ops": [_add(PEAK)]}, _canvas(), None, spy)
    assert [o["op"] for o in out["ops"]] == ["add"] and not out["unhandled"]
    assert seen == [[{"axes": "axes_0", "x": 5, "y": 5}]]
    assert out["unmeasured"] == {} and out["scene_fingerprint"]


def test_an_added_object_names_the_panels_it_was_placed_against():
    """判据的主语：`changes[].depends_on` 量的是**新对象的位置依据**（锚点 / 贴合的子图对象 id），不是它自己；
    没锚在任何子图上的新对象没有这个键。"""
    panel_id = ve.panel_refs(_canvas())["P1"]
    for item in (TAG, BRACKET, ARROW, CONNECTOR, FRAME):
        added = next(c for c in _plan([_add(item)])["changes"] if c.get("added"))
        assert added["depends_on"] == [panel_id], item["name"]


def test_stat_text_check_is_linear_on_long_whitespace_and_keeps_its_meaning():
    """判据的主语：`is_stat_text` 的**墙钟时间**（用户 / 模型文字里成千上万个制表符不能拖慢）与**结果**（压空白不改语义）。"""
    import time

    from tavotto.engine import vectorscene as vsc

    for evil in (
        "p" + "\t" * 50_000 + "x",
        "p\t" * 25_000 + "x",
        " " * 100_000 + "x",
        "n" + " " * 60_000 + "=x",
    ):
        t0 = time.perf_counter()
        assert vsc.is_stat_text(evil) is False
        assert time.perf_counter() - t0 < 1.0, evil[:8]
    for text in (
        "p" + "\t" * 5000 + "< 0.05",
        "n\t=\t5",
        "n. s.",
        "  **  ",
        "p -  value",
        "P\n<\n.01",
    ):
        assert vsc.is_stat_text(text) is True, repr(text[:12])
    for text in ("peak", "n", "a * b", "ns data"):
        assert vsc.is_stat_text(text) is False, text


def test_a_data_anchored_object_depends_on_its_measured_panel():
    """3b：量过的子图上的数据 / 元素锚点对象，同样把所在子图写进 `depends_on`（子图挪了 / 覆盖项变了 = 图改过了）。"""
    res = ve.propose_memory(_canvas(), [_add(PEAK)], measure=stub_measure)
    added = next(c for c in res["changes"] if c.get("added"))
    assert added["depends_on"] == [ve.panel_refs(_canvas())["P1"]]
