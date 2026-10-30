"""装依赖后的续跑里，校验才暴露出**可修正**的失败（脚本跑到了此前被缺包挡住的代码，如相对路径写 CSV 被写入根绊线拦下）：
让原会话的 Agent 修正**一次**，再校验一次（ADR 0143 修订，2026-10-30）。

真实桥 + 假 Agent CLI（一小段 python）+ 假校验 runner；不联网、不装包。看护：
* 恰好一次修正，修正指令与任务内修正同一份文字（`ai_convert.correction_prompt`），预算只有这一轮，不循环；
* 修正后通过 → `ready`（暂存 / 对照 / 预览与首跑同一份代码），修正后仍失败 → `failed`、状态可读、不再修正；
* 不可修正的失败 / Agent 起不来 → 不调 Agent、行为同旧；
* 修正期间取消 → 与续跑取消同一语义（候选删、`cancelled`），Agent 进程不残留；
* 修正会话沿用原会话的 Agent / 模型 / 强度 / 接口，账本里指回原任务。
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from tavotto.engine import ai_bridge, ai_convert, ai_history, ai_tasks
from tests.test_ai_convert_foreign import (  # noqa: F401  —— 夹具复用
    CARD,
    WRITE_GOOD,
    _hold_hook,
    _project,
    _wait,
    env,
    ok_runner,
)

FIXED = (
    "import matplotlib.pyplot as plt\nimport pandas as pd\n"
    "fig, ax = plt.subplots()\nax.plot([3, 4])\nfig.savefig('fixed.png')\n"
)
WRITE_FIXED = f"(d / 'fig_matplotlib.py').write_text({FIXED!r}, encoding='utf-8')"
OUTSIDE = {
    "error": {
        "code": "script_probe_failed",
        "message": "x",
        "cause": "write_outside_roots",
        "write_violation": {"op": "open", "path": "data/values.csv"},
    }
}
NO_DEPS = lambda project, rel, cancel=None, **kw: {"status": "none"}  # noqa: E731


def _seq_runner(outs):
    """依次返回 `outs`（用完后一直返回最后一个）；记下调用次数。"""
    calls = []

    def runner(figures_dir, rel, write_roots=None):
        calls.append(rel)
        return outs[min(len(calls), len(outs)) - 1]

    runner.calls = calls
    return runner


@pytest.fixture
def conn(env, monkeypatch):  # noqa: F811
    """记下每次 CLI 起的连接参数与接口解析（原会话与修正会话是否同一个连接）。"""
    seen = {"cmd": [], "endpoint": []}
    base = ai_bridge._cmd

    def cmd(agent_id, prompt, cwd, model=None, effort=None, endpoint=None, **kw):
        seen["cmd"].append((agent_id, model, effort))
        return base(agent_id, prompt, cwd, model=model, effort=effort, endpoint=endpoint)

    def resolve(agent, endpoint_id):
        seen["endpoint"].append(endpoint_id)
        return None

    monkeypatch.setattr(ai_bridge, "_cmd", cmd)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", resolve)
    return seen


def _held(env):  # noqa: F811
    """首跑：Agent 写出候选，校验前依赖步骤停住 → awaiting_dependencies。"""
    sid = ai_bridge.run(
        "codex",
        "fig.R",
        "生成可编辑的 Matplotlib 版本（R）",
        str(env["figs"]),
        model="m-1",
        effort="high",
        endpoint_id="ep-1",
        task_hooks={"runner": ok_runner, "deps": _hold_hook()},
        convert={"library": None, "source_language": "r", "original_rel": "out.png"},
    )
    _wait(sid)
    assert ai_convert.status(sid, _project(env))["state"] == ai_convert.STATE_AWAITING
    return sid


def _resume(env, sid, runner, *, correct=ai_bridge.correct_convert):  # noqa: F811
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    out: dict = {}
    t = threading.Thread(
        target=lambda: out.update(
            ai_convert.run_resume(
                sid,
                _project(env),
                cdir,
                meta,
                cancel,
                runner=runner,
                ensure=NO_DEPS,
                correct=correct,
            )
        )
    )
    return t, out, cancel


def _correction_text(env):  # noqa: F811
    check = ai_convert._failure_check(
        {
            "status": "error",
            "code": "write_outside_roots",
            "message": ai_convert.WRITE_OUTSIDE_HINT,
        },
        "precommit",
    )
    # 详情里带「被拒的写入」那一行（`run_validation` 的同一拼法）
    check.observed["detail"] += "\nRejected write: " + json.dumps(
        OUTSIDE["error"]["write_violation"]
    )
    return ai_convert.correction_prompt("fig_matplotlib.py", [check])


def test_resume_failure_that_is_correctable_runs_one_agent_correction_then_ready(env, conn):  # noqa: F811
    before = {p.name: p.read_bytes() for p in env["figs"].iterdir() if p.is_file()}
    sid = _held(env)
    env["codes"] = [WRITE_FIXED]
    runner = _seq_runner([OUTSIDE, {"stems": ["out"], "size_mm": [100.0, 50.0], "svg": "<svg/>"}])
    t, out, _c = _resume(env, sid, runner)
    t.start()
    t.join(30)
    assert not t.is_alive()
    assert out["state"] == "ready", out
    assert len(env["prompts"]) == 2, "首跑 1 次 + 修正 1 次，不多"
    assert _correction_text(env) in env["prompts"][1], "修正指令与任务内修正同一份文字"
    assert len(runner.calls) == 2, "修正前 1 次（暴露失败）+ 修正后提交前检查 1 次"
    assert conn["cmd"] == [("codex", "m-1", "high")] * 2, "同一个 Agent / 模型 / 强度"
    assert set(conn["endpoint"]) == {"ep-1"}, "同一个接口"
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == "ready"
    cdir = ai_convert.CONVERT_DIR / sid
    body = (cdir / "fig_matplotlib.py").read_bytes()
    assert b"ax.plot([3, 4])" in body, "留存的是修正后的脚本"
    meta = json.loads((cdir / ai_convert.META_NAME).read_text(encoding="utf-8"))
    assert meta["candidate_sha256"] == ai_convert._sha256(body)
    assert not ai_convert.resuming(sid)
    after = {p.name: p.read_bytes() for p in env["figs"].iterdir() if p.is_file()}
    assert after == before, "项目里一个字节没动"
    # 账本：修正会话是一条 convert 任务，指回原任务，终局 completed
    rows = [
        r
        for r in (ai_history.get(s) for s in list(ai_bridge.SESSIONS) if s != sid)
        if r and r.get("task_type") == ai_convert.TASK_TYPE
    ]
    assert len(rows) == 1 and rows[0]["parent_task_id"] == sid
    assert rows[0]["outcome"] == "completed" and rows[0]["provider"] == "codex"
    assert ai_tasks.load(rows[0]["id"])["final"] is True


def test_correction_that_still_fails_ends_failed_without_a_second_correction(env, conn):  # noqa: F811
    sid = _held(env)
    env["codes"] = [WRITE_FIXED]
    runner = _seq_runner([OUTSIDE])  # 修正后仍然写项目目录
    t, out, _c = _resume(env, sid, runner)
    t.start()
    t.join(30)
    assert out["state"] == "failed", out
    assert out["reason"] == "run_failed" and out["code"] == "write_outside_roots"
    assert len(env["prompts"]) == 2, "只修正一次，不循环"
    assert len(runner.calls) == 2
    st = ai_convert.status(sid, _project(env))
    assert st["state"] == "failed" and st["failure"]["code"] == "write_outside_roots"
    assert (ai_convert.CONVERT_DIR / sid).is_dir(), "失败原因要能被轮询读到"
    with pytest.raises(ai_convert.ConvertError):
        ai_convert.adopt(sid, _project(env))


def test_non_correctable_failure_does_not_call_the_agent(env, conn):  # noqa: F811
    sid = _held(env)
    runner = _seq_runner([{"error": {"code": "missing_dependency", "message": "no module"}}])
    t, out, _c = _resume(env, sid, runner)
    t.start()
    t.join(30)
    assert out["state"] == "failed" and out["reason"] == "missing_dependency", out
    assert len(env["prompts"]) == 1, "不可修正：不调 Agent"
    assert len(runner.calls) == 1


def test_resume_without_a_correct_hook_keeps_the_old_behavior(env, conn):  # noqa: F811
    sid = _held(env)
    t, out, _c = _resume(env, sid, _seq_runner([OUTSIDE]), correct=None)
    t.start()
    t.join(30)
    assert out["state"] == "failed" and out["code"] == "write_outside_roots"
    assert len(env["prompts"]) == 1


def test_agent_that_cannot_start_keeps_the_original_failure(env, conn, monkeypatch):  # noqa: F811
    sid = _held(env)

    def refuse(agent):
        raise ai_bridge.AgentError("ai_agent_not_usable")

    monkeypatch.setattr(ai_bridge, "require_usable", refuse)
    t, out, _c = _resume(env, sid, _seq_runner([OUTSIDE]))
    t.start()
    t.join(30)
    assert out["state"] == "failed" and out["code"] == "write_outside_roots", out
    assert len(env["prompts"]) == 1
    assert ai_convert.status(sid, _project(env))["state"] == "failed"


def test_cancel_during_the_correction_stops_the_agent_and_drops_the_candidate(
    env,  # noqa: F811
    conn,
    tmp_path,
):
    sid = _held(env)
    pidfile = tmp_path / "agent.pid"
    env["codes"] = [
        f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    ]
    t, out, _c = _resume(env, sid, _seq_runner([OUTSIDE]))
    t.start()
    deadline = time.time() + 20
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert pidfile.exists(), "修正的 Agent 进程没起来"
    pid = int(pidfile.read_text())
    assert ai_convert.discard(sid, _project(env)) == {"ok": True}  # 用户点停止
    t.join(30)
    assert not t.is_alive()
    assert out["state"] == "cancelled", out
    assert not (ai_convert.CONVERT_DIR / sid).exists(), "候选删了"
    assert not ai_convert.resuming(sid)
    end = time.time() + 10
    alive = True
    while alive and time.time() < end:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            alive = False
    assert not alive, "Agent 进程残留"
    assert len(env["prompts"]) == 2


def test_correction_budget_is_exactly_one_agent_run_and_no_further_correction(env):  # noqa: F811
    cand = type(
        "C",
        (),
        {
            "sid": "s1",
            "script_rel": "fig.R",
            "base_sha256": "0" * 64,
            "original_rel": None,
            "target_name": "fig_matplotlib.py",
        },
    )()
    env_ = ai_convert.envelope_for(
        agent="codex", project_key="/p", cand=cand, prompt="x", rid=None, correction_round=True
    )
    assert env_.budget.max_agent_runs == 1 and env_.budget.max_corrections == 0
    assert env_.budget.max_renders == 1 and env_.parent_task_id == "s1"
    first = ai_convert.envelope_for(
        agent="codex", project_key="/p", cand=cand, prompt="x", rid=None
    )
    assert (first.budget.max_agent_runs, first.budget.max_corrections) == (2, 1)


def test_correction_that_pauses_for_a_new_dependency_card_persists_the_corrected_candidate(
    env,  # noqa: F811
    conn,
):
    """Codex #1509 P2：修正改了候选字节，修正后的校验又停住等依赖决定——meta 的 candidate_sha256 必须是**修正后**的字节，
    否则点新卡片的安装后 `_rebuild` 判 convert_candidate_changed。"""
    sid = _held(env)
    env["codes"] = [WRITE_FIXED]
    asks = []

    def ensure(project, rel, cancel=None, **kw):
        asks.append(rel)
        if len(asks) == 1:  # 续跑第一遍校验：不缺包，暴露写项目目录的失败
            return {"status": "none"}
        return {
            "status": "hold",
            "card": dict(CARD),
            "missing": ["openpyxl"],
        }  # 修正后的校验：又要装

    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    out = ai_convert.run_resume(
        sid,
        _project(env),
        cdir,
        meta,
        cancel,
        runner=_seq_runner([OUTSIDE]),
        ensure=ensure,
        correct=ai_bridge.correct_convert,
    )
    assert out["state"] == ai_convert.STATE_AWAITING, out
    assert len(env["prompts"]) == 2
    saved = json.loads((cdir / ai_convert.META_NAME).read_text(encoding="utf-8"))
    on_disk = (cdir / "fig_matplotlib.py").read_bytes()
    assert b"ax.plot([3, 4])" in on_disk
    assert saved["state"] == ai_convert.STATE_AWAITING and saved["dependency_card"] == CARD
    assert saved["candidate_sha256"] == ai_convert._sha256(on_disk), "meta 里是修正后字节的哈希"
    # 用户点新卡片的安装：同一个候选，不再是 convert_candidate_changed，装好后 ready
    cdir, meta, cancel = ai_convert.prepare_resume(sid, _project(env))
    res = ai_convert.run_resume(
        sid,
        _project(env),
        cdir,
        meta,
        cancel,
        runner=ok_runner,
        ensure=NO_DEPS,
        correct=ai_bridge.correct_convert,
    )
    assert res["state"] == "ready", res
    assert len(env["prompts"]) == 2, "第二次续跑不再叫 Agent"
