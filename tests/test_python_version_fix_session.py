"""版本不合时自动换 Python：接进准备会话（ADR 0114 修订 2026-10-23）——**假 pool、假 apply**。

验会话自己的行为：桌面发起的运行以「版本不合」收场且推得出范围 → **同一会话**先把 phase 换成
`preparing_environment`（带进度）、换完再重跑一次；每次运行至多一次；取消可停；失败 / 不是桌面发起 / 推不出就保持原
结局、不重跑。账本（`attempt_history`）里多一条 `kind: python_changed`，报告里有 `python_change`。
真代事务的验收在 `test_python_version_fix.py::TestRealTransaction`。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import threading
import time

import pytest

from tavotto import app as m
from tavotto.engine import managedenv, pool as engine_pool, prepsession, pyversionfix
from test_preparation_api import (  # noqa: F401
    _open,
    _project,
    client,
    fake_pool,
)
from test_preparation_session import (  # noqa: F401
    _act,
    _action,
    _create,
    _get,
    sessions,
)

VERSION_MISMATCH = engine_pool.WorkerError("too old", code="project_env_unsupported_python")


@pytest.fixture
def change(monkeypatch, fake_pool, tmp_path):
    """可控的「换 Python」替身：真 `plan_python_change`（挑选处替成假的 3.12），假 `apply_python_change`。"""
    box = {
        "applied": [],
        "ok": True,
        "heal": True,  # 换成功后让假 pool 的重跑成功
        "gate": threading.Event(),
        "entered": threading.Event(),
        "progress": [],
    }
    box["gate"].set()
    monkeypatch.setattr(managedenv, "base_pick", lambda **kw: ("/sys/python3.12", "3.12"))
    monkeypatch.setattr(m, "_desktop_initiated", lambda: True, raising=False)
    monkeypatch.setattr(
        engine_pool,
        "resolve_worker_python",
        lambda root=None, **kw: (str(tmp_path / "envs" / "fake" / "bin" / "python"), "system"),
    )

    def apply(plan, progress, cancel):
        box["applied"].append(plan)
        box["entered"].set()
        for snap in box["progress"]:
            progress(snap)
        deadline = time.time() + 20
        while not box["gate"].is_set() and time.time() < deadline:
            if cancel.is_set():
                return {"ok": False, "code": pyversionfix.ERROR_CANCELLED, "to": plan.to_version}
            time.sleep(0.01)
        if box["ok"] and box["heal"]:
            fake_pool["error"] = None
        return {
            "ok": box["ok"],
            "code": "" if box["ok"] else "managed_env_create_failed",
            "to": plan.to_version,
        }

    monkeypatch.setattr(pyversionfix, "apply_python_change", apply)
    return box


def _start(client, tmp_path, fake_pool, error=VERSION_MISMATCH):
    _open(client, _project(tmp_path, "p"))
    fake_pool["error"] = error
    report = _create(client, {"script": "fig.py"}).get_json()
    resp = _act(
        client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    assert resp.status_code == 202, resp.get_json()
    return report["session_id"]


def _until(client, sid, done, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    while True:
        report = _get(client, sid).get_json()
        if done(report):
            return report
        assert time.time() < deadline, report
        time.sleep(0.02)


def _finished(report) -> bool:
    return report["phase"] not in ("running", "awaiting_runtime_input", "preparing_environment")


def _runs(report):
    return [h for h in report["attempt_history"] if h.get("kind") != "python_changed"]


def _changes(report):
    return [h for h in report["attempt_history"] if h.get("kind") == "python_changed"]


def test_version_mismatch_changes_python_and_reruns_once_in_the_same_session(
    client, tmp_path, fake_pool, change, sessions
):
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert final["session_id"] == sid
    assert fake_pool["build_calls"] == 2  # 失败一次 + 换完重跑一次
    assert len(change["applied"]) == 1
    assert final["phase"] == "completed" and final["outcome"]["kind"] == "succeeded", final
    pc = final["python_change"]
    assert pc["to"] == "3.12" and pc["base"] == "system" and pc["state"] == "done"
    assert pc["download_bytes"] == 0
    # 账本：失败的尝试 → 换版本 → 重跑；时间线顺序如实
    assert [h.get("kind", "run") for h in final["attempt_history"]] == [
        "run",
        "python_changed",
        "run",
    ]
    changed = _changes(final)[0]
    assert (changed["to"], changed["outcome"], changed["download_bytes"]) == (
        "3.12",
        "succeeded",
        0,
    )
    first, second = _runs(final)
    assert first["outcome"] == "failed" and second["outcome"] == "succeeded"
    assert second["cause"] == "python_changed"
    assert final["provider"]["attempts"] == 2


def test_the_change_shows_progress_and_phase_while_it_runs(
    client, tmp_path, fake_pool, change, sessions
):
    change["gate"].clear()
    change["progress"] = [{"stage": "downloading", "done": 5_000_000, "total": 25_000_000}]
    sid = _start(client, tmp_path, fake_pool)
    assert change["entered"].wait(10)
    mid = _until(client, sid, lambda r: (r["python_change"] or {}).get("done", 0) > 0)
    assert mid["phase"] == "preparing_environment"
    assert mid["outcome"] == {"kind": "running", "reason": "python_change"}
    pc = mid["python_change"]
    assert (pc["state"], pc["stage"], pc["done"], pc["total"]) == (
        "running",
        "downloading",
        5_000_000,
        25_000_000,
    )
    assert [h.get("kind", "run") for h in mid["attempt_history"]] == ["run", "python_changed"]
    assert _changes(mid)[0]["outcome"] == "pending"
    change["gate"].set()
    final = _until(client, sid, _finished)
    assert final["phase"] == "completed" and final["python_change"]["state"] == "done"


def test_cancel_stops_the_change_and_keeps_the_original_failure(
    client, tmp_path, fake_pool, change, sessions
):
    change["gate"].clear()
    sid = _start(client, tmp_path, fake_pool)
    assert change["entered"].wait(10)
    mid = _until(client, sid, lambda r: r["phase"] == "preparing_environment")
    cancel = _action(mid, "cancel")
    resp = _act(client, sid, cancel["id"], mid["config_revision"])
    assert resp.status_code == 200, resp.get_json()
    final = _until(client, sid, _finished)
    assert fake_pool["build_calls"] == 1  # 取消之后一行用户代码都不再跑
    assert final["python_change"]["state"] == "cancelled"
    assert final["outcome"]["kind"] == "failed"
    assert _changes(final)[0]["outcome"] == "cancelled"
    assert len(_runs(final)) == 1


def test_cancel_while_planning_probes_never_downloads_or_builds(
    client, tmp_path, fake_pool, change, sessions, monkeypatch
):
    """P1：取消落在规划（探测系统解释器）期间——这时 `python_change` 还不存在，取消事件要早已挂在会话上。
    探测返回后不得开始换：不下载、不建环境、不激活，结局是取消。"""
    probing, release = threading.Event(), threading.Event()

    def slow_pick(**kw):
        probing.set()
        assert release.wait(20)
        return ("/sys/python3.12", "3.12")

    monkeypatch.setattr(managedenv, "base_pick", slow_pick)
    sid = _start(client, tmp_path, fake_pool)
    assert probing.wait(10)
    mid = _get(client, sid).get_json()
    assert mid["python_change"] is None  # 还在规划：换版本的状态不存在
    cancel = _action(mid, "cancel")
    resp = _act(client, sid, cancel["id"], mid["config_revision"])
    assert resp.status_code == 200, resp.get_json()
    release.set()
    final = _until(client, sid, _finished)
    assert change["applied"] == []  # 没有进到执行（下载 / 建环境 / 激活都在里面）
    assert fake_pool["build_calls"] == 1
    assert final["python_change"]["state"] == "cancelled"
    assert _changes(final)[0]["outcome"] == "cancelled"
    assert final["outcome"]["kind"] == "failed" and len(_runs(final)) == 1


def test_a_failed_change_keeps_the_original_failure_and_does_not_rerun(
    client, tmp_path, fake_pool, change, sessions
):
    change["ok"] = False
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert fake_pool["build_calls"] == 1
    assert final["outcome"]["kind"] == "failed"
    assert final["python_change"]["state"] == "failed"
    assert final["python_change"]["code"] == "managed_env_create_failed"
    assert _changes(final)[0]["outcome"] == "failed"


def test_at_most_one_change_per_run_even_if_the_rerun_fails_the_same_way(
    client, tmp_path, fake_pool, change, sessions
):
    change["heal"] = False  # 换完重跑仍然版本不合：不许再换第二次
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert len(change["applied"]) == 1
    assert fake_pool["build_calls"] == 2
    assert final["outcome"]["kind"] == "failed"
    assert len(_changes(final)) == 1 and len(_runs(final)) == 2


def test_a_new_user_run_may_change_again(client, tmp_path, fake_pool, change, sessions):
    """ "每次运行"至多一次：用户再点一次运行是新的一次运行，预算重新算。"""
    change["heal"] = False
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert len(change["applied"]) == 1
    run = _action(final, "run")
    assert _act(client, sid, run["id"], final["config_revision"]).status_code == 202
    _until(client, sid, lambda r: len(change["applied"]) == 2 and _finished(r))
    assert len(change["applied"]) == 2


def test_not_desktop_initiated_runs_never_change_python(
    client, tmp_path, fake_pool, change, sessions, monkeypatch
):
    monkeypatch.setattr(m, "_desktop_initiated", lambda: False, raising=False)
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert change["applied"] == [] and fake_pool["build_calls"] == 1
    assert final["python_change"] is None and final["outcome"]["kind"] == "failed"


@pytest.mark.parametrize("code", ["script_error", "missing_dependency", "worker_crashed"])
def test_other_failures_never_change_python(client, tmp_path, fake_pool, change, sessions, code):
    err = engine_pool.WorkerError(
        "x", code=code, module="alpha" if code == "missing_dependency" else ""
    )
    sid = _start(client, tmp_path, fake_pool, error=err)
    final = _until(client, sid, _finished)
    assert change["applied"] == [] and fake_pool["build_calls"] == 1
    assert final["python_change"] is None


def test_nothing_to_infer_means_no_change(
    client, tmp_path, fake_pool, change, sessions, monkeypatch
):
    monkeypatch.setattr(pyversionfix, "plan_python_change", lambda failure, project: None)
    sid = _start(client, tmp_path, fake_pool)
    final = _until(client, sid, _finished)
    assert change["applied"] == [] and fake_pool["build_calls"] == 1
    assert final["python_change"] is None
    assert prepsession.SESSIONS.get(sid, final["project_id"]) is not None
