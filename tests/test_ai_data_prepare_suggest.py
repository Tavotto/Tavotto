# ruff: noqa: F811 - `proj` 是从 test_ai_data_prepare 导入的 fixture，用例参数同名
"""数据准备收尾时模型顺带给的「默认画法」（`finish(plot=…)`，ADR 0215 修订 2026-11-28）。

来源：真模型验收里，表整理对了，但交给规则选图时用户那句话的意图丢了（「看油耗和马力的关系，按气缸数分组」→ 规则拿气缸数当了横轴）。
判据的主语是**留下来的建议**（`candidate_roles`）：只收输出表里真有的列、纵轴只收数值列；不合法的逐项丢掉；建议收不收都不改变任务结论，
也不进任务历史。全程假模型，不发请求。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import ai_data_prepare as dp
from tavotto.engine.agent_runner import prepare_tools
from tests.test_ai_data_prepare import (  # noqa: F401 - proj 是 fixture
    GOOD,
    call,
    drive,
    finished,
    proj,
    run_task,
    toolmap,
    turn,
)


def turns_with(plot) -> list[dict]:
    args = {"summary": "done"} if plot is None else {"summary": "done", "plot": plot}
    return [turn(call("create_file", content=GOOD)), turn(call("finish", **args))]


def suggested_after(proj, plot) -> dict:
    res, events, *_ = drive(proj, turns_with(plot))
    assert finished(res, events), events[-1]
    return dict(proj.cand.extras.get("suggested") or {})


def test_valid_picks_are_kept_exactly(proj):
    assert suggested_after(proj, {"y": ["temp_c"], "x": "time_s", "group": None}) == {
        "y": ["temp_c"],
        "x": "time_s",
    }


@pytest.mark.parametrize(
    ("plot", "want"),
    [
        ({"y": ["ghost"], "x": "time_s"}, {}),  # 纵轴一个都不合法：整份不收
        ({"y": ["temp_c", "ghost", "temp_c"], "x": "ghost"}, {"y": ["temp_c"]}),  # 逐项丢、去重
        ({"y": "temp_c", "x": "temp_c"}, {"y": ["temp_c"]}),  # 横轴不能就是纵轴；y 给成字符串也认
        ({"y": ["temp_c"], "x": "time_s", "group": "time_s"}, {"y": ["temp_c"], "x": "time_s"}),
        ({"y": ["temp_c"], "x": 3, "group": ["time_s"]}, {"y": ["temp_c"]}),  # 类型不对的丢掉
        ({"x": "time_s"}, {}),  # 没有纵轴
        ({}, {}),
    ],
)
def test_invalid_picks_are_dropped_one_by_one_and_never_fail_the_task(proj, plot, want):
    assert suggested_after(proj, plot) == want


def test_without_plot_nothing_is_recorded(proj):
    assert suggested_after(proj, None) == {}


def test_a_plot_that_is_not_an_object_is_a_tool_error(proj):
    bad = toolmap(proj)["finish"].handler({"summary": "x", "plot": "time_s"})
    assert bad.is_error and "plot" in bad.content


def test_the_suggestion_reaches_the_ready_candidate_but_not_the_history_row(proj, tmp_path):
    plot = {"y": ["temp_c"], "x": "time_s"}
    out, _ = run_task(proj, tmp_path, turns_with(plot))
    assert out["outcome"] == "completed", out
    assert dp.candidate_roles(proj.cand.sid) == plot
    meta = json.loads((proj.cand.dir / dp.META_NAME).read_text(encoding="utf-8"))
    assert meta["suggested"] == plot
    blob = json.dumps(out, ensure_ascii=False, default=str)
    assert "temp_c" not in blob and "time_s" not in blob  # 列名不进任务历史


def test_candidate_roles_is_empty_for_unknown_or_plain_candidates(proj, tmp_path):
    assert dp.candidate_roles("0" * 12) == {}
    out, _ = run_task(proj, tmp_path, turns_with(None))
    assert out["outcome"] == "completed" and dp.candidate_roles(proj.cand.sid) == {}


def test_a_later_check_voids_an_earlier_suggestion(proj):
    """建议绑在「那一次通过检查的表」上：表重新核过，上一份建议作废。"""
    hooks = dp.make_hooks(proj.cand, remaining=lambda: 600.0)
    proj.cand.script.write_text(GOOD, encoding="utf-8", newline="")
    assert hooks.check()[0]
    hooks.suggest({"y": ["temp_c"], "x": "time_s"})
    assert proj.cand.extras["suggested"] == {"y": ["temp_c"], "x": "time_s"}
    assert hooks.check()[0]
    assert "suggested" not in proj.cand.extras


def test_at_most_eight_y_columns_and_the_tool_schema_names_the_three_roles(proj):
    assert dp.SUGGEST_MAX_Y == 8
    (finish,) = [
        t for t in prepare_tools.DataPrepareToolset().tools(_env(proj)) if t.name == "finish"
    ]
    props = finish.schema["properties"]["plot"]["properties"]
    assert set(props) == {"y", "x", "group"}
    assert finish.schema.get("required") in ([], None)


def _env(proj):
    from tests.test_ai_data_prepare import envelope, hooks_of

    return envelope(proj, hooks_of(proj))
