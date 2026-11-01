"""失败卡「回滚 AI 的修改」与修复助手的「刚被 AI 改过」事实（TVD-H1M5，ADR 0148 修订 2026-11-01）。

那次反馈：脚本改前跑通过，用户让改图助手改了一次，之后每次运行都被脚本自己的检查抛错；失败卡与修复助手都不知道
脚本刚被 AI 改过——助手把「改前 native 成功、改后 safe 失败」读成运行方式差异，绕了一圈，用户没出路。

判据的主语分三层，各钉一处：

* `ai_bridge.revertible_edit`：**此刻磁盘上的脚本字节**是不是**最近一次改过它的 AI 会话**结束时留下的那一版
  （与回滚自己的版本判据同一条：之后人工又改过 / 已回滚 / 又被另一次 AI 改过，一律不算）——走真实的 `run()` 与真实的
  历史库，不手捏会话字典；
* 准备会话：这次失败是 `script_error`，且那次 AI 修改**开始之前**本会话里有跑通的尝试 → remedy `revert_ai_edit`；
  报告的 `ai_edit` 给出 `{session, succeeded_before}`；
* 修复助手的运行报告（`runreport`）：把这条事实摆在前面，并说清卡上已有的出路。
"""

# ruff: noqa: F811 — 夹具（client / fake_pool / sessions）从别的测试文件导入复用，参数名与导入名相同
from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from tavotto.engine import ai_bridge, ai_history, pool as engine_pool, prepsession
from tavotto.engine.fixtools.runreport import _ai_edit_fact
from test_preparation_api import client, fake_pool  # noqa: F401 — 夹具复用
from test_preparation_session import (  # noqa: F401
    _act,
    _action,
    _create_ready,
    _settle,
    sessions,
)

BEFORE_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4])\n"
AFTER_AI = b"import matplotlib.pyplot as plt\nplt.plot([1, 2], [3, 4], color='red')\n"
SECOND_AI = AFTER_AI + b"plt.grid(True)\n"


# --------------------------------------------------------------------------- revertible_edit（真实 run + 真实历史库）
@pytest.fixture
def ai_env(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "fig1.py"
    script.write_bytes(BEFORE_AI)
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "ai_history.sqlite3")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge, "_build_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(
        ai_bridge, "_refresh_after_ai", lambda *a, **k: {"status": "ok"}, raising=False
    )
    started: list[str] = []
    yield {"project": project, "script": script, "started": started}
    for sid in started:
        ai_bridge.SESSIONS.pop(sid, None)


def _ai_session(env, monkeypatch, writes: bytes) -> str:
    code = f"open('fig1.py', 'wb').write({writes!r})"
    monkeypatch.setattr(ai_bridge, "_cmd", lambda *a, **k: ([sys.executable, "-c", code], {}))
    done = threading.Event()
    sid = ai_bridge.run(
        "codex",
        "fig1.py",
        "改成红色",
        str(env["project"]),
        on_event=lambda name, data: done.set() if name == "ai.done" else None,
    )
    env["started"].append(sid)
    assert done.wait(30), "假 CLI 没在 30 秒内结束"
    # `ai.done` 发在落库之前也可能：等历史库里这一行进终态
    for _ in range(100):
        row = ai_history.get(sid) or {}
        if row.get("status") not in (None, "running"):
            break
        time.sleep(0.05)
    assert env["script"].read_bytes() == writes
    return sid


def test_the_version_the_ai_just_left_is_revertible(ai_env, monkeypatch):
    sid = _ai_session(ai_env, monkeypatch, AFTER_AI)
    got = ai_bridge.revertible_edit(str(ai_env["project"]), "fig1.py")
    assert got is not None and got["session"] == sid
    assert got["started_ms"] > 0


def test_a_later_human_edit_is_not_the_ais_version(ai_env, monkeypatch):
    _ai_session(ai_env, monkeypatch, AFTER_AI)
    ai_env["script"].write_bytes(AFTER_AI + b"plt.title('mine')\n")
    assert ai_bridge.revertible_edit(str(ai_env["project"]), "fig1.py") is None


def test_an_already_reverted_edit_is_not_offered_again(ai_env, monkeypatch):
    sid = _ai_session(ai_env, monkeypatch, AFTER_AI)
    ai_bridge.revert(sid)
    assert ai_env["script"].read_bytes() == BEFORE_AI
    assert ai_bridge.revertible_edit(str(ai_env["project"]), "fig1.py") is None


def test_only_the_most_recent_edit_counts(ai_env, monkeypatch):
    """两次 AI 修改：只给最近那次；把它撤掉之后也不退回去给更早那次（更早那次之后脚本被改过，撤它会丢东西）。"""
    _first = _ai_session(ai_env, monkeypatch, AFTER_AI)
    second = _ai_session(ai_env, monkeypatch, SECOND_AI)
    got = ai_bridge.revertible_edit(str(ai_env["project"]), "fig1.py")
    assert got is not None and got["session"] == second
    ai_bridge.revert(second)
    assert ai_env["script"].read_bytes() == AFTER_AI
    assert ai_bridge.revertible_edit(str(ai_env["project"]), "fig1.py") is None


def test_an_unknown_script_has_no_revertible_edit(ai_env, monkeypatch):
    _ai_session(ai_env, monkeypatch, AFTER_AI)
    assert ai_bridge.revertible_edit(str(ai_env["project"]), "other.py") is None


# --------------------------------------------------------------------------- 准备会话：remedy 与 ai_edit
@pytest.fixture(autouse=False)
def no_ai_probe(monkeypatch):
    monkeypatch.setattr(prepsession.SESSIONS, "ai_connected", lambda: True)


def _run(client, sid, report, fake_pool, error=None):
    fake_pool["error"] = error
    assert (
        _act(client, sid, _action(report, "run")["id"], report["config_revision"]).status_code
        == 202
    )
    return _settle(client, sid)


def _edit_at(ms: int | None):
    """可撤销 AI 修改的查询替身：`ms` = 那次修改开始的时刻；None = 没有。记下被问过几次（缓存用例用）。"""
    calls: list[tuple[str, str]] = []

    def lookup(root: str, script: str):
        calls.append((root, script))
        return None if ms is None else {"session": "ai-s1", "started_ms": ms}

    return lookup, calls


def test_failure_after_an_ai_edit_that_ran_before_offers_revert(
    client, tmp_path, fake_pool, sessions, monkeypatch, no_ai_probe
):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    ok = _run(client, sid, report, fake_pool)
    assert ok["phase"] == "completed"
    lookup, _calls = _edit_at(int(time.time() * 1000) + 1)
    monkeypatch.setattr(prepsession.SESSIONS, "ai_edit_lookup", lookup)
    final = _run(
        client,
        sid,
        ok,
        fake_pool,
        engine_pool.WorkerError("ValueError: legend", code="script_error"),
    )
    assert final["outcome"]["kind"] == "failed"
    assert final["remedy"] == {"kind": "revert_ai_edit", "session": "ai-s1"}
    assert final["ai_edit"] == {"session": "ai-s1", "succeeded_before": True}


def test_no_success_before_the_edit_keeps_ai_fix_but_still_reports_the_fact(
    client, tmp_path, fake_pool, sessions, monkeypatch, no_ai_probe
):
    """改前从没跑通过（或后端重启后账本空了）：不知道撤回去能不能跑，不给确定的「回滚」——但事实照样交给修复助手。"""
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    lookup, _calls = _edit_at(int(time.time() * 1000) - 60_000)
    monkeypatch.setattr(prepsession.SESSIONS, "ai_edit_lookup", lookup)
    final = _run(
        client, sid, report, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    assert final["remedy"] == {"kind": "ai_fix"}
    assert final["ai_edit"] == {"session": "ai-s1", "succeeded_before": False}


def test_a_success_only_after_the_edit_does_not_count(
    client, tmp_path, fake_pool, sessions, monkeypatch, no_ai_probe
):
    """主语是「那次修改**之前**的成功」：修改之后跑通过、后来才坏，不是这次修改造成的。"""
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    lookup, _calls = _edit_at(int(time.time() * 1000) - 60_000)
    monkeypatch.setattr(prepsession.SESSIONS, "ai_edit_lookup", lookup)
    ok = _run(client, sid, report, fake_pool)
    final = _run(client, sid, ok, fake_pool, engine_pool.WorkerError("boom", code="script_error"))
    assert final["remedy"] == {"kind": "ai_fix"}
    assert final["ai_edit"]["succeeded_before"] is False


def test_no_revertible_edit_leaves_the_report_unchanged(
    client, tmp_path, fake_pool, sessions, monkeypatch, no_ai_probe
):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    lookup, _calls = _edit_at(None)
    monkeypatch.setattr(prepsession.SESSIONS, "ai_edit_lookup", lookup)
    final = _run(
        client, sid, report, fake_pool, engine_pool.WorkerError("boom", code="script_error")
    )
    assert final["remedy"] == {"kind": "ai_fix"}
    assert final["ai_edit"] is None


def test_a_successful_run_carries_no_ai_edit_and_polling_does_not_requery(
    client, tmp_path, fake_pool, sessions, monkeypatch, no_ai_probe
):
    _root, _pj, report = _create_ready(client, tmp_path)
    sid = report["session_id"]
    lookup, calls = _edit_at(int(time.time() * 1000) - 60_000)
    monkeypatch.setattr(prepsession.SESSIONS, "ai_edit_lookup", lookup)
    ok = _run(client, sid, report, fake_pool)
    assert ok["ai_edit"] is None and ok["remedy"] is None
    failed = _run(client, sid, ok, fake_pool, engine_pool.WorkerError("boom", code="script_error"))
    assert failed["ai_edit"] is not None
    asked = len(calls)
    for _ in range(3):  # 报告轮询：同一次尝试、脚本没变 → 不再查库读文件
        assert client.get(f"/api/engine/preparation-sessions/{sid}").status_code == 200
    assert len(calls) == asked


# --------------------------------------------------------------------------- 修复助手的运行报告
def test_the_fix_report_states_the_ai_edit_and_points_at_the_card():
    fact = _ai_edit_fact({"session": "ai-s1", "succeeded_before": True})
    text = json.dumps(fact)
    assert "AI assistant" in text and "ran successfully" in text
    assert "undoes that edit" in text
    # 别再把它读成运行方式的差异
    assert "execution mode" in text


def test_the_fix_report_is_honest_when_it_did_not_run_before():
    fact = _ai_edit_fact({"session": "ai-s1", "succeeded_before": False})
    assert fact is not None and "not known" in fact["fact"]
    assert "fix" not in fact


def test_no_fact_without_a_session():
    assert _ai_edit_fact(None) is None
    assert _ai_edit_fact({}) is None
