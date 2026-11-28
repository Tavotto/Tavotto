# ruff: noqa: F811 - `env` / `licensed` 是从别的用例导入的 fixture，用例参数同名
"""修改方案里候选多出 warning（真机 2026-11-28「把字号改按照期刊标准修改」）：**只拿掉引起 warning 的改动**，其余照常出卡。

判据的主语：

* 「拿掉的是谁」量的是方案的组 / 未处理（`render_warning`）与假 worker **最后一次出图**用的 override 列表——
  它必须等于方案的 `candidate_patches`（预览的就是要应用的），且不含引起 warning 的那条；
* 「整张失败」只在一条不剩（且没有上一版带过来的组）或试渲染到顶时发生；
* 批注重改的输出上限量的是交给 `call_structured` 的 `limits.max_output_tokens`。
"""

from __future__ import annotations

import logging

from tavotto.engine import changeplan as cp
from tests.test_changeplan import ch
from tests.test_changeplan_endpoints import B0, env, run, wait  # noqa: F401
from tests.test_changeplan_revise import first_plan, licensed, run_revise  # noqa: F401

TITLE_BIG = {"gid": "ax0.title", "prop": "fontsize", "value": 12}


def warns_on(gid: str, prop: str, value):
    """假 worker 的 warning：列表里（last-wins 生效的）这一条 = 这个值时报一条。"""

    def warn(patches):
        last = None
        for p in patches:
            if (p["gid"], p["prop"]) == (gid, prop):
                last = p["value"]
        return [f"应用失败 {gid}.{prop}: 模拟"] if last == value else []

    return warn


def last_snapshot(worker) -> list:
    return [p for k, p in worker.calls if k == "snap"][-1]


def test_only_the_group_that_warns_is_dropped_and_the_rest_make_the_card(env, caplog):
    _m, client, _figs, worker, model, _retired = env
    worker.warn = warns_on("ax0.title", "fontsize", 12)
    model.result = {
        "groups": [
            {"comment_id": "c1", "changes": [ch("ax0.line0", "linewidth", 3)]},
            {"comment_id": "c2", "changes": [ch("ax0.title", "fontsize", 12)]},
        ],
        "unhandled": [],
    }
    with caplog.at_level(logging.INFO, logger="tavotto.changeplan"):
        out = run(client)
    assert out["state"] == "ready", out
    plan = out["plan"]
    assert [g["comment_id"] for g in plan["groups"]] == ["c1"]
    assert {"comment_id": "c2", "reason": "render_warning"} in plan["unhandled"]
    assert all(p["gid"] != "ax0.title" or p["value"] != 12 for p in plan["candidate_patches"])
    assert cp.patchspec.canonicalize(last_snapshot(worker)) == plan["candidate_patches"]
    # 日志记下了新 warning 的摘要（截断），不是只有一句 render_failed
    assert any("应用失败 ax0.title.fontsize" in r.getMessage() for r in caplog.records)


def test_every_group_warning_fails_the_whole_plan(env, caplog):
    _m, client, _figs, worker, model, retired = env
    worker.warn = lambda patches: ["boom"] if len(patches) > len(B0) else []
    model.result = {
        "groups": [
            {"comment_id": "c1", "changes": [ch("ax0.line0", "linewidth", 3)]},
            {"comment_id": "c2", "changes": [ch("ax0.title", "fontsize", 12)]},
        ],
        "unhandled": [],
    }
    with caplog.at_level(logging.INFO, logger="tavotto.changeplan"):
        out = run(client)
    assert out["state"] == "failed" and out["code"] == "change_plan_render_failed"
    assert out["detail"] == "warning" and len(retired) == 1
    assert any("整张不出" in r.getMessage() and "boom" in r.getMessage() for r in caplog.records)


def test_inside_one_group_only_the_offending_changes_are_dropped(env):
    """助手来源的「具体的修改」永远只有一组 `a1`：组内二分，只拿掉引起 warning 的那条。"""
    _m, client, _figs, worker, model, _retired = env
    worker.warn = warns_on("ax0.title", "fontsize", 12)
    model.result = {
        "intent": "edit",
        "groups": [
            {
                "comment_id": "a1",
                "changes": [
                    ch("ax0.line0", "linewidth", 3),
                    ch("ax0.title", "fontsize", 12),
                    ch("ax0.line0", "color", "#ff0000"),
                ],
            }
        ],
        "unhandled": [],
    }
    resp = client.post(
        "/api/change-plans",
        json={"id": "Kin.pdf", "patches": B0, "source": "assistant", "instruction": "按期刊改"},
    )
    out = wait(client, resp.get_json()["plan_id"])
    assert out["state"] == "ready", out
    plan = out["plan"]
    (group,) = plan["groups"]
    assert sorted((c["target_gid"], c["prop"]) for c in group["changes"]) == [
        ("ax0.line0", "color"),
        ("ax0.line0", "linewidth"),
    ]
    assert {"comment_id": "a1", "reason": "render_warning"} in plan["unhandled"]
    assert cp.patchspec.canonicalize(last_snapshot(worker)) == plan["candidate_patches"]


def test_revise_note_that_warns_gives_back_the_change_it_displaced(env, licensed):
    """批注改了上一版同一 (gid, prop)、又引出 warning：批注拿掉，上一版那条回来（预览 == 应用）。"""
    _m, client, _figs, worker, model, _retired = env
    v1 = first_plan(client)  # 上一版：c1 线宽 3
    worker.warn = warns_on("ax0.line0", "linewidth", 5)
    model.result = {
        "groups": [{"comment_id": "n1", "changes": [ch("ax0.line0", "linewidth", 5)]}],
        "unhandled": [],
    }
    out = run_revise(client, v1["id"])
    assert out["state"] == "ready", out
    v2 = out["plan"]
    assert [(g["comment_id"], g["changes"][0]["after"]) for g in v2["groups"]] == [("c1", 3)]
    assert {"comment_id": "n1", "reason": "render_warning"} in v2["unhandled"]
    assert cp.patchspec.canonicalize(last_snapshot(worker)) == v2["candidate_patches"]


def test_running_out_of_trial_renders_fails_the_whole_plan(env, monkeypatch):
    _m, client, _figs, worker, model, _retired = env
    monkeypatch.setattr(cp, "MAX_WARNING_TRIALS", 2)
    worker.warn = warns_on("ax0.title", "fontsize", 12)
    model.result = {
        "groups": [
            {"comment_id": "c1", "changes": [ch("ax0.line0", "linewidth", 3)]},
            {"comment_id": "c2", "changes": [ch("ax0.title", "fontsize", 12)]},
        ],
        "unhandled": [],
    }
    out = run(client)
    assert out["state"] == "failed" and out["code"] == "change_plan_render_failed"


def test_render_warning_is_a_known_unhandled_reason():
    assert "render_warning" in cp.UNHANDLED_REASONS


def test_revise_gets_the_larger_output_budget_and_review_keeps_its_own(env, licensed):
    _m, client, _figs, _worker, model, _retired = env
    seen: list[int] = []
    real = model.__call__

    def spy(**kw):
        seen.append(kw["limits"].max_output_tokens)
        return real(**kw)

    cp._CALL = spy
    v1 = first_plan(client)
    model.result = {
        "groups": [{"comment_id": "n1", "changes": [TITLE_BIG]}],
        "unhandled": [],
    }
    assert run_revise(client, v1["id"])["state"] == "ready"
    assert seen == [cp.MODEL_MAX_OUTPUT_TOKENS, cp.MODEL_MAX_OUTPUT_TOKENS_ASSISTANT]
