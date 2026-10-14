"""V2 PR1：准备会话里的 Safe→Native 执行回退（ADR 0127）——**假 pool、假 native runner**。

验会话自己的行为：safe 尝试以 `safe_file_operation_blocked` 收场且策略允许 → **同一会话**接上第二个 attempt（native
runner），不新建会话；native 失败报 native 自己的真实错误、不回 safe；其余原因 / 策略 / 开关 / 取消一律不回退。
真子进程穿过同一条路的验收在 `test_execution_fallback_e2e.py`。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import execpolicy, pool as engine_pool, preparation, prepsession
from test_preparation_api import (  # noqa: F401
    _build_resp,
    _FakeWorker,
    _open,
    _project,
    client,
    fake_pool,
)
from test_preparation_session import (  # noqa: F401
    SESSIONS,
    _act,
    _action,
    _create,
    _get,
    _settle,
    sessions,
)

BLOCKED = engine_pool.WorkerError("blocked", code="safe_file_operation_blocked")


@pytest.fixture
def native(monkeypatch):
    """可控的 native runner 替身：只数调用、可注入错误 / 阻塞；与真 runner 同一个调用约定。"""
    box = {"calls": [], "error": None, "gate": threading.Event()}
    box["gate"].set()

    def runner(pl, before_retry=None):
        box["calls"].append(pl)
        box["gate"].wait(30)
        if box["error"] is not None:
            raise box["error"]
        return _FakeWorker(Path(pl.project_root)), _build_resp(), True

    monkeypatch.setattr(m, "_native_preparation_runner", runner)
    return box


def _start(client, tmp_path, fake_pool, error=BLOCKED, name="p", root=None):
    _open(client, root if root is not None else _project(tmp_path, name))
    fake_pool["error"] = error
    report = _create(client, {"script": "fig.py"}).get_json()
    resp = _act(
        client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    assert resp.status_code == 202, resp.get_json()
    return report["session_id"]


def _history(report):
    return report["attempt_history"]


def test_blocked_safe_attempt_falls_back_to_native_in_the_same_session(
    client, tmp_path, fake_pool, native, sessions
):
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    assert final["session_id"] == sid  # 同一会话，不新建
    assert fake_pool["build_calls"] == 1  # safe 恰一次
    assert len(native["calls"]) == 1  # native 恰一次
    plan = native["calls"][0]
    assert plan.execution == "native" and plan.native_spec is not None
    assert final["phase"] == "completed" and final["outcome"]["kind"] == "succeeded", final
    safe, nat = _history(final)
    assert (safe["profile"], safe["outcome"], safe["code"]) == (
        "safe",
        "blocked",
        "safe_file_operation_blocked",
    )
    assert (nat["profile"], nat["seq"], nat["cause"]) == (
        "native",
        2,
        "safe_file_operation_blocked",
    )
    assert nat["changed"][0] == "profile" and set(nat["changed"]) <= set(
        execpolicy.CHANGED_DIMENSIONS
    )
    assert nat["outcome"] == "succeeded"
    assert final["provider"]["attempts"] == 2
    # 同一个 session 里 sessions 表只有一条
    assert len(prepsession.SESSIONS._by_id) == 1


def test_the_second_attempt_carries_what_changed_into_the_task_diagnostics(
    client, tmp_path, fake_pool, native, sessions
):
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    second_id = _history(final)[1]["attempt_id"]
    first_id = _history(final)[0]["attempt_id"]
    plan, result = preparation.SERVICE.get(second_id, final["project_id"])
    proj = preparation.diagnostic_projection(plan, result)
    assert proj["retry"] == {
        "of": first_id,
        "cause": "safe_file_operation_blocked",
        "changed": _history(final)[1]["changed"],
        "execution": "native",
    }
    first_plan, first_result = preparation.SERVICE.get(first_id, final["project_id"])
    assert preparation.diagnostic_projection(first_plan, first_result).get("retry") is None
    assert str(tmp_path) not in json.dumps(proj)


def test_native_failure_reports_the_native_error_and_never_returns_to_safe(
    client, tmp_path, fake_pool, native, sessions
):
    native["error"] = engine_pool.WorkerError("boom", code="script_error")
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    assert final["outcome"]["kind"] == "failed" and final["outcome"]["code"] == "script_error"
    assert fake_pool["build_calls"] == 1 and len(native["calls"]) == 1  # 没有第三次
    assert [h["profile"] for h in _history(final)] == ["safe", "native"]
    assert _history(final)[1]["outcome"] == "failed"
    assert final["result"]["error"]["code"] == "script_error"


@pytest.mark.parametrize(
    "code",
    ["script_error", "missing_dependency", "script_exited", "missing_input", "worker_crashed"],
)
def test_non_blocked_failures_never_fall_back(client, tmp_path, fake_pool, native, sessions, code):
    err = engine_pool.WorkerError(
        "x", code=code, module="alpha" if code == "missing_dependency" else ""
    )
    sid = _start(client, tmp_path, fake_pool, error=err)
    final = _settle(client, sid)
    assert fake_pool["build_calls"] == 1 and native["calls"] == []
    assert final["provider"]["attempts"] == 1 and len(_history(final)) == 1
    assert final["outcome"]["kind"] != "succeeded"


def test_safe_only_project_is_respected(client, tmp_path, fake_pool, native, sessions):
    root = _project(tmp_path, "p")
    execpolicy.set_mode(root, execpolicy.MODE_SAFE_ONLY)
    sid = _start(client, tmp_path, fake_pool, root=root)
    final = _settle(client, sid)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1
    assert final["outcome"]["code"] == "safe_file_operation_blocked"
    assert _history(final)[0]["declined"] == "policy_safe_only"


def test_kill_switch_disables_the_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, "0")
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    assert native["calls"] == [] and final["outcome"]["code"] == "safe_file_operation_blocked"
    assert _history(final)[0]["declined"] == "fallback_disabled"


def test_cancel_before_the_fallback_starts_no_native_run(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """用户在「safe 已结束、回退还没接上」的窗口里点了取消：不再起 native。"""
    entered, release = threading.Event(), threading.Event()
    real = prepsession.SessionService._plan_fallback

    def slow(self, *a, **kw):
        entered.set()
        assert release.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(prepsession.SessionService, "_plan_fallback", slow)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    assert mid["phase"] == "running"  # 回退窗口里 safe 的结局还没对外
    cancel = _action(mid, "cancel")
    resp = _act(client, sid, cancel["id"], mid["config_revision"])
    assert resp.status_code == 200, resp.get_json()
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "cancelled"
    assert final["provider"]["attempts"] == 1


def test_cancel_landing_after_planning_but_before_the_handoff_still_stops_the_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """取消落在「锁外规划完、锁内交出去」之间：最后一刻的复核（`_start_fallback`）必须再看一次取消标记。"""
    planned, release = threading.Event(), threading.Event()
    real = prepsession.SessionService._plan_fallback

    def after(self, *a, **kw):
        got = real(self, *a, **kw)
        assert got is not None  # 规划本身是放行的
        planned.set()
        assert release.wait(10)
        return got

    monkeypatch.setattr(prepsession.SessionService, "_plan_fallback", after)
    sid = _start(client, tmp_path, fake_pool)
    assert planned.wait(10)
    mid = _get(client, sid).get_json()
    resp = _act(client, sid, _action(mid, "cancel")["id"], mid["config_revision"])
    assert resp.status_code == 200, resp.get_json()
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "cancelled" and len(_history(final)) == 1


def test_cancelling_the_native_attempt_hits_the_native_attempt(
    client, tmp_path, fake_pool, native, sessions
):
    """取消动作当初指着 safe 那次；回退接上之后，取消要落在最新的 native 上。"""
    native["gate"].clear()
    sid = _start(client, tmp_path, fake_pool)
    deadline = time.time() + 10
    while not native["calls"]:
        assert time.time() < deadline
        time.sleep(0.02)
    mid = _get(client, sid).get_json()
    assert mid["phase"] == "running" and len(_history(mid)) == 2
    resp = _act(client, sid, _action(mid, "cancel")["id"], mid["config_revision"])
    assert resp.status_code == 200
    native["gate"].set()
    final = _settle(client, sid)
    assert final["phase"] == "cancelled", final
    deadline = time.time() + 10
    while _history(_get(client, sid).get_json())[1]["outcome"] == "pending":  # 收尾线程落账
        assert time.time() < deadline
        time.sleep(0.02)
    assert _history(_get(client, sid).get_json())[1]["outcome"] == "cancelled"


def test_an_explicit_rerun_gets_a_fresh_fallback_budget(
    client, tmp_path, fake_pool, native, sessions
):
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    assert len(native["calls"]) == 1
    resp = _act(client, sid, _action(final, "run")["id"], final["config_revision"])
    assert resp.status_code == 202, resp.get_json()
    again = _settle(client, sid)
    assert fake_pool["build_calls"] == 2 and len(native["calls"]) == 2
    assert again["provider"]["attempts"] == 4
    assert [h["run"] for h in _history(again)] == [1, 1, 2, 2]


def test_the_legacy_preparation_endpoint_never_falls_back(
    client, tmp_path, fake_pool, native, sessions
):
    """旧的 `/api/engine/preparation` 不经会话认领，不触发回退（回退只在会话 `run` 动作之后）。"""
    _open(client, _project(tmp_path, "p"))
    fake_pool["error"] = BLOCKED
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    pid = body["plan"]["plan_id"]
    deadline = time.time() + 10
    while (
        preparation.SERVICE.get(pid, body["plan"]["project_id"])[1].status
        not in preparation.TERMINAL
    ):
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    final = preparation.SERVICE.get(pid, body["plan"]["project_id"])[1]
    assert final.error["code"] == "safe_file_operation_blocked"
    assert native["calls"] == [] and fake_pool["build_calls"] == 1


def test_attempt_history_is_public_safe(client, tmp_path, fake_pool, native, sessions):
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    blob = json.dumps(_history(final))
    assert str(tmp_path) not in blob and "/envs/fake" not in blob
    for rec in _history(final):
        assert set(rec) == {
            "run",
            "seq",
            "attempt_id",
            "profile",
            "interpreter",
            "launch",
            "cause",
            "changed",
            "outcome",
            "code",
            "declined",
        }


def test_fallback_is_not_an_intent_step(client, tmp_path, fake_pool, native, sessions):
    """回退不属于意图步数：`run_when_ready` 的 `steps` 仍是 1，意图名下有两次尝试。"""
    _open(client, _project(tmp_path, "p"))
    fake_pool["error"] = BLOCKED
    report = _create(client, {"script": "fig.py"}).get_json()
    arm = _action(report, "run_when_ready")
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": arm["id"],
            "expected_config_revision": report["config_revision"],
            "impact_digest": arm["impact"]["impact_digest"],
        },
    )
    assert resp.status_code in (200, 202), resp.get_json()
    final = _settle(client, report["session_id"])
    assert len(native["calls"]) == 1 and fake_pool["build_calls"] == 1
    assert final["intent"]["status"] == "launched" and final["intent"]["steps"] == 1
    assert final["intent"]["attempts"] == 2
