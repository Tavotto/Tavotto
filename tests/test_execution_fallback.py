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
def native(monkeypatch, fake_pool, tmp_path):
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
    # 假解释器必须是**本平台的绝对路径**：桌面拥有的 native 计划（`execspec.desktop_native_spec`）用 `os.path.isabs`
    # 校验解释器与 cwd，Windows 上 `/envs/fake/bin/python` 不是绝对路径，回退计划就算不出来
    fake_python = str(tmp_path / "envs" / "fake" / "bin" / "python")
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda root=None, **kw: (fake_python, "system")
    )
    # 回退只授权给桌面用户主动发起的运行（ADR 0127）：本文件的用例默认就是桌面发起；非桌面来源的用例在
    # `test_execution_fallback_origin.py` 里走真判据
    monkeypatch.setattr(m, "_desktop_initiated", lambda: True, raising=False)
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


def test_closing_the_project_before_the_fallback_starts_no_native_run(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """关项目（`cancel_project`）落在「safe 已终局、回退还没接上」的窗口：没有未终局计划可取消，标记必须拦住 native。"""
    entered, release = threading.Event(), threading.Event()
    real = prepsession.SessionService._plan_fallback

    def slow(self, *a, **kw):
        entered.set()
        assert release.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(prepsession.SessionService, "_plan_fallback", slow)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    project_id = _get(client, sid).get_json()["project_id"]
    preparation.SERVICE.cancel_project(project_id)
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "cancelled" and len(_history(final)) == 1


def _register_newer_plan(report, plan_id="prep-newer-rerun", script=None):
    """登记一份比会话现有尝试更晚的同脚本计划（只登记不执行），当 `cancel_project(keep=)` 的「新计划」。"""
    import dataclasses

    template, _ = preparation.SERVICE.get(report["provider"]["attempt_id"], report["project_id"])
    changed = {"plan_id": plan_id, **({"script": script} if script else {})}
    preparation.SERVICE.register(dataclasses.replace(template, **changed))
    return plan_id


def _window(monkeypatch):
    """把 `_plan_fallback` 停在「safe 已终局、回退还没接上」的窗口里。"""
    entered, release = threading.Event(), threading.Event()
    real = prepsession.SessionService._plan_fallback

    def slow(self, *a, **kw):
        entered.set()
        assert release.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(prepsession.SessionService, "_plan_fallback", slow)
    return entered, release


def test_a_same_script_rerun_elsewhere_cancels_a_session_waiting_to_fall_back(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """别处同脚本重跑（`cancel_project(script=, keep=别的计划)`）让位：本会话窗口里的回退不再起 native。"""
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    keep = _register_newer_plan(mid)
    preparation.SERVICE.cancel_project(mid["project_id"], script="fig.py", keep=keep)
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == [] and _history(final)[0]["declined"] == "cancelled"


def test_an_older_rerun_elsewhere_does_not_cancel_a_newer_attempt(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """只让比 `keep` 更早登记的尝试让位：本会话的第二次运行比 keep（第一次运行的旧计划）更新，它的回退不受影响。"""
    sid = _start(client, tmp_path, fake_pool)
    first = _settle(client, sid)
    older_attempt = first["provider"]["attempt_id"]
    entered, release = _window(monkeypatch)
    resp = _act(client, sid, _action(first, "run")["id"], first["config_revision"])
    assert resp.status_code == 202, resp.get_json()
    assert entered.wait(10)
    preparation.SERVICE.cancel_project(first["project_id"], script="fig.py", keep=older_attempt)
    release.set()
    final = _settle(client, sid)
    assert len(native["calls"]) == 2 and final["phase"] == "completed"


def test_a_fallback_superseded_by_a_newer_same_script_run_never_launches_native(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """Codex #1028 r4268196418：两个桌面运行交错，旧的那次走到回退时已有更新的同脚本计划登记——回退的 native runner 会无条件让位
    （结束新一次的 native），所以最后一刻（和取消标记的复核同一把锁、同一时刻）必须再核一次登记顺序。"""
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    _register_newer_plan(mid)  # 另一个窗口的更新重跑已登记（没有取消标记：不经 cancel_project）
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "superseded" and len(_history(final)) == 1


def test_the_fallback_native_plan_is_stamped_with_the_project_epoch(
    client, tmp_path, fake_pool, native, sessions
):
    """#1022：桌面 native 计划登记时盖项目关闭代次；回退的 native 计划走同一个 `register`，同样拿到。"""
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    nplan, _ = preparation.SERVICE.get(_history(final)[1]["attempt_id"], final["project_id"])
    assert nplan.execution == "native"
    assert nplan.project_epoch == m.engine_nativesession.REGISTRY.project_epoch(str(tmp_path / "p"))
    assert nplan.project_epoch is not None


def test_a_fallback_begun_before_the_project_was_closed_and_reopened_never_launches_native(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """项目关闭再重开：关闭前开始的运行，回退不得在重开后的项目里起进程。就算关闭路径漏了取消标记（这里只升代次、不经
    `cancel_project`），运行开始时记下的代次与此刻对不上也拒绝。"""
    registry = m.engine_nativesession.REGISTRY
    root = str(tmp_path / "p")
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    registry.mark_project_closed(root)
    registry.reopen_project(root)
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "project_reopened"


def test_closing_and_reopening_the_project_through_the_real_close_order_declines_the_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    registry = m.engine_nativesession.REGISTRY
    root = str(tmp_path / "p")
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    project_id = _get(client, sid).get_json()["project_id"]
    registry.mark_project_closed(root)  # 与 app 关项目同一顺序：标记 → 取消准备
    preparation.SERVICE.cancel_project(project_id)
    registry.reopen_project(root)
    release.set()
    final = _settle(client, sid)
    time.sleep(0.2)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] in ("cancelled", "project_reopened")


def test_a_rerun_of_another_script_does_not_stop_the_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    keep = _register_newer_plan(mid, script="other.py")
    preparation.SERVICE.cancel_project(mid["project_id"], script="other.py", keep=keep)
    release.set()
    final = _settle(client, sid)
    assert len(native["calls"]) == 1 and final["phase"] == "completed"


def test_the_sessions_own_rerun_plan_is_kept_and_does_not_block_its_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """`keep` 是本会话自己的计划（回退的 native 尝试启动时自己调 `cancel_project(script=, keep=自己)`）：不标自己。"""
    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    own = mid["provider"]["attempt_id"]
    preparation.SERVICE.cancel_project(mid["project_id"], script="fig.py", keep=own)
    release.set()
    final = _settle(client, sid)
    assert len(native["calls"]) == 1 and final["phase"] == "completed"


def test_a_same_script_rerun_elsewhere_cancels_a_running_fallback_attempt(
    client, tmp_path, fake_pool, native, sessions
):
    native["gate"].clear()
    sid = _start(client, tmp_path, fake_pool)
    deadline = time.time() + 10
    while not native["calls"]:
        assert time.time() < deadline
        time.sleep(0.02)
    mid = _get(client, sid).get_json()
    got = preparation.SERVICE.cancel_project(
        mid["project_id"], script="fig.py", keep=_register_newer_plan(mid)
    )
    assert got >= 1
    native["gate"].set()
    assert _settle(client, sid)["phase"] == "cancelled"


def test_closing_the_project_cancels_a_running_fallback_attempt(
    client, tmp_path, fake_pool, native, sessions
):
    """回退已接上、native 尝试在跑时关项目：这次 native 尝试也被取消。"""
    native["gate"].clear()
    sid = _start(client, tmp_path, fake_pool)
    deadline = time.time() + 10
    while not native["calls"]:
        assert time.time() < deadline
        time.sleep(0.02)
    mid = _get(client, sid).get_json()
    assert preparation.SERVICE.cancel_project(mid["project_id"]) >= 1
    native["gate"].set()
    final = _settle(client, sid)
    assert final["phase"] == "cancelled", final


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


def test_a_cancel_action_issued_against_the_safe_attempt_cancels_the_native_one_after_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """用户拿着回退之前发出的取消动作（指着 safe 那次）点取消：回退已经接上了，取消必须落在 native 上。"""
    entered, release = threading.Event(), threading.Event()
    real = prepsession.SessionService._plan_fallback

    def slow(self, *a, **kw):
        entered.set()
        assert release.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(prepsession.SessionService, "_plan_fallback", slow)
    native["gate"].clear()  # native 起来后停住
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    stale_cancel = _action(mid, "cancel")  # 指着 safe 那次
    release.set()
    deadline = time.time() + 10
    while not native["calls"]:
        assert time.time() < deadline
        time.sleep(0.02)
    resp = _act(client, sid, stale_cancel["id"], mid["config_revision"])  # 不重新读报告：就用旧动作
    assert resp.status_code == 200, resp.get_json()
    native["gate"].set()
    final = _settle(client, sid)
    assert final["phase"] == "cancelled", final


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


def _record_native_retirements(monkeypatch):
    calls = []
    real = m.engine_nativesession.REGISTRY.cancel_project
    monkeypatch.setattr(
        m.engine_nativesession.REGISTRY,
        "cancel_project",
        lambda root, *, script="", older_than=None: (
            calls.append((str(root), script, older_than))
            or real(root, script=script, older_than=older_than)
        ),
    )
    return calls


def test_a_safe_rerun_retires_the_previous_native_session_of_the_same_script(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """P2：回退成功后 native 进程停在屏障上占着环境租约；用户改完脚本再点运行（safe 成功）时，上一次桌面 native 会话要让位，
    复用 #1022 的 `REGISTRY.cancel_project(script=)` 与计划票据取消，不再造一套。"""
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    assert len(native["calls"]) == 1 and final["phase"] == "completed"
    retired = _record_native_retirements(monkeypatch)
    cancelled = []
    real = preparation.SERVICE.cancel_project
    monkeypatch.setattr(
        preparation.SERVICE,
        "cancel_project",
        lambda pid, script=None, keep=None: (
            cancelled.append((script, keep)) or real(pid, script, keep)
        ),
    )
    fake_pool["error"] = None  # 用户改掉了被拦的操作：这次 safe 成功
    resp = _act(client, sid, _action(final, "run")["id"], final["config_revision"])
    assert resp.status_code == 202, resp.get_json()
    again = _settle(client, sid)
    assert again["phase"] == "completed" and len(native["calls"]) == 1  # 这次没有回退
    assert [r[1] for r in retired] == ["fig.py"]  # 上一次桌面 native 会话让位（只此一次）
    newest = again["provider"]["attempt_id"]
    assert cancelled == [("fig.py", newest)]  # 同脚本更早的计划票据也让位，放过本次自己


def _plan_copy(template, plan_id):
    import dataclasses

    plan = dataclasses.replace(template, plan_id=plan_id)
    preparation.SERVICE.register(plan)
    return plan


def test_a_superseded_safe_launch_does_not_retire_the_newer_reruns_native_session(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """Codex #1028 r4268029512：两个重跑交错——旧的 safe 动作登记了计划，却在新的那次已经回退到 native 之后才轮到启动。
    它的让位必须和 `cancel_project(keep=)` 同一个登记顺序：比自己新的同脚本计划还在，就是被取代了，不能去结束新一次的 native 会话。"""
    sid = _start(client, tmp_path, fake_pool)
    report = _settle(client, sid)
    template, _ = preparation.SERVICE.get(
        report["attempt_history"][0]["attempt_id"], report["project_id"]
    )  # safe 计划
    older = _plan_copy(template, "prep-older-safe")  # 先登记（旧动作）
    newer = _plan_copy(template, "prep-newer-rerun")  # 后登记（新动作，已回退到 native）
    retired = _record_native_retirements(monkeypatch)
    started = []
    monkeypatch.setattr(
        m, "_launch_preparation", lambda ctx, plan, on_done=None: started.append(plan.plan_id)
    )

    m._launch_session_attempt(None, older)  # 迟到的旧动作
    m._launch_session_attempt(None, newer)  # 最新的那次
    older_order = preparation.SERVICE.get("prep-older-safe", report["project_id"])[0].launch_order
    newer_order = preparation.SERVICE.get("prep-newer-rerun", report["project_id"])[0].launch_order
    assert older_order is not None and newer_order is not None and older_order < newer_order
    # 让位用 #1022 的 `older_than=` 语义：只结束登记序号**比自己小**的 native 会话 / 票据——旧动作的调用结束不了新一次的
    assert [(r[1], r[2]) for r in retired] == [("fig.py", older_order), ("fig.py", newer_order)]
    assert preparation.SERVICE.get("prep-newer-rerun", report["project_id"])[1].status == "pending"
    assert started == [
        "prep-older-safe",
        "prep-newer-rerun",
    ]  # 旧动作自己的执行不受影响，仍交给执行线程


def test_the_fallback_native_plan_has_a_launch_order_after_its_safe_attempt(
    client, tmp_path, fake_pool, native, sessions
):
    """#1022 的 `launch_order`：回退的 native 计划登记时拿到序号，且晚于它的 safe 尝试（native runner 据此只结束更早的）。"""
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    safe_id, native_id = (h["attempt_id"] for h in _history(final))
    safe_plan, _ = preparation.SERVICE.get(safe_id, final["project_id"])
    native_plan, _ = preparation.SERVICE.get(native_id, final["project_id"])
    assert safe_plan.launch_order is not None and native_plan.launch_order is not None
    assert native_plan.launch_order > safe_plan.launch_order


def test_the_fallback_plan_keeps_the_run_start_epoch_even_if_the_project_is_reopened_at_registration(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """Codex #1028 r4268453923：代次比较之后、登记之前项目关闭又重开——登记不能盖「登记那一刻」的新代次，
    否则关闭前的旧回退能通过注册表层的代次检查。回退计划必须继承运行开始时记下的代次。"""
    registry = m.engine_nativesession.REGISTRY
    root = str(tmp_path / "p")
    real_register = preparation.PreparationService.register

    def bump_then_register(self, plan, **kw):
        if plan.execution == "native" and not getattr(bump_then_register, "done", False):
            bump_then_register.done = True
            registry.mark_project_closed(root)
            registry.reopen_project(root)
        return real_register(self, plan, **kw)

    start_epoch = registry.project_epoch(root)
    monkeypatch.setattr(preparation.PreparationService, "register", bump_then_register)
    sid = _start(client, tmp_path, fake_pool)
    _settle(client, sid)
    time.sleep(0.3)
    assert registry.project_epoch(root) == start_epoch + 1  # 确实在登记前升了代次
    stamped = [p.project_epoch for p in native["calls"]]
    assert stamped in ([], [start_epoch]), "回退计划盖了登记时的新代次，关闭前的旧运行就能起进程"


def test_a_first_safe_run_and_a_legacy_asset_preparation_do_not_retire_native_sessions(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """只有会话里的重跑让位；旧端点按素材做准备（面板展示）不能顺手结束用户正在编辑的 native 活图。"""
    retired = _record_native_retirements(monkeypatch)
    _open(client, _project(tmp_path, "p"))
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert resp.status_code == 202
    time.sleep(0.3)
    assert retired == []


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


def test_a_newer_run_registered_between_the_supersede_check_and_the_fallback_registration_blocks_native(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """Codex #1028 r4273826766：`superseded()` 复核之后、回退计划登记之前，另一个窗口登记了更新的重跑——回退计划的序号会比它大，
    native runner 启动就把它结束了。登记后必须再核一次；确定性地在「检查之后、登记之前」插入更新的运行（不靠睡眠）。"""
    import dataclasses

    real_register = preparation.PreparationService.register
    injected = []

    def register_with_newer_run_first(self, plan, **kw):
        if plan.retry_of and not injected:
            template, _ = self.get(plan.retry_of, plan.project_id)
            injected.append("prep-newer-window")
            real_register(self, dataclasses.replace(template, plan_id="prep-newer-window"))
        return real_register(self, plan, **kw)

    monkeypatch.setattr(preparation.PreparationService, "register", register_with_newer_run_first)
    sid = _start(client, tmp_path, fake_pool)
    final = _settle(client, sid)
    time.sleep(0.2)
    assert injected == ["prep-newer-window"]
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "superseded" and len(_history(final)) == 1


def test_an_asset_preparation_of_the_same_script_does_not_supersede_the_fallback(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """Codex #1028 r4273826779：旧端点按素材做准备（`TARGET_ASSET`）同脚本登记，不是替代运行——不能让合格的回退被记成 superseded。"""
    import dataclasses

    entered, release = _window(monkeypatch)
    sid = _start(client, tmp_path, fake_pool)
    assert entered.wait(10)
    mid = _get(client, sid).get_json()
    template, _ = preparation.SERVICE.get(mid["provider"]["attempt_id"], mid["project_id"])
    preparation.SERVICE.register(
        dataclasses.replace(template, plan_id="prep-asset", target=preparation.TARGET_ASSET)
    )
    release.set()
    final = _settle(client, sid)
    assert len(native["calls"]) == 1
    assert final["phase"] == "completed" and len(_history(final)) == 2
    assert not _history(final)[0].get("declined")
