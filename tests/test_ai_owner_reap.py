"""读路径收尾属主已死的 AI 会话行（#1113 的一半）：不再只在启动时 `mark_interrupted_running`。

真实子进程（被 SIGKILL 的属主）+ 真实 Flask test client + 真实 SQLite（临时目录）。
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading

import pytest

from tavotto import app as m
from tavotto.engine import ai_bridge, ai_history, project_watch as engine_watch

PROJECT = "/p-reap"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(
        m, "require_project", lambda: type("P", (), {"resolve": lambda s: PROJECT})()
    )
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _spawn_owner() -> subprocess.Popen:
    """一个真实的、活着的「另一个服务进程」；测试里 SIGKILL 它来造死属主。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def _kill(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    proc.wait(timeout=10)


def _start(sid: str, owner_pid: int, script: str = "a.py") -> None:
    ok, got = ai_history.record_start(
        {
            "id": sid,
            "project": PROJECT,
            "provider": "codex",
            "script": script,
            "owner_pid": owner_pid,
            "client_request_id": f"rid-{sid}-0000001",
        }
    )
    assert (ok, got) == (True, sid)


def _row(sid: str) -> dict:
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        raw.row_factory = sqlite3.Row
        return dict(raw.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())


def _open_ids(client) -> list[str]:
    data = client.get("/api/ai/history?status=open").get_json()
    return [s["id"] for s in data["sessions"]]


def test_dead_owner_row_is_closed_on_list_and_detail(client):
    owner = _spawn_owner()
    _start("sid-dead-0001", owner.pid)
    assert _open_ids(client) == ["sid-dead-0001"]  # 属主活着：在 open 清单里
    _kill(owner)

    assert _open_ids(client) == []
    row = _row("sid-dead-0001")
    assert row["status"] == "interrupted" and row["ended_ms"] is not None

    det = client.get("/api/ai/sessions/sid-dead-0001").get_json()
    assert det["status"] == "interrupted" and det["final"] is True
    assert det["ended_ms"] is not None


def test_detail_alone_closes_a_dead_owner_row(client):
    """不经过列表，直接取详情也收尾（详情是 final 的权威）。"""
    owner = _spawn_owner()
    _start("sid-dead-0002", owner.pid)
    _kill(owner)
    det = client.get("/api/ai/sessions/sid-dead-0002").get_json()
    assert det["final"] is True and det["status"] == "interrupted"
    assert det["ended_ms"] is not None


def test_live_owner_row_is_left_alone(client):
    owner = _spawn_owner()
    try:
        _start("sid-live-0001", owner.pid)
        assert _open_ids(client) == ["sid-live-0001"]
        det = client.get("/api/ai/sessions/sid-live-0001").get_json()
        assert det["status"] == "running" and det["final"] is False
        assert _row("sid-live-0001")["ended_ms"] is None
    finally:
        _kill(owner)


def test_own_process_row_is_never_reaped_on_the_read_path(client):
    """`record_start` 之后、进 `SESSIONS` 之前（中间还隔着一次 Popen）：本进程的行不能被读路径收成 interrupted。"""
    _start("sid-own-0001", os.getpid())
    assert "sid-own-0001" not in ai_bridge.SESSIONS
    assert _open_ids(client) == ["sid-own-0001"]
    det = client.get("/api/ai/sessions/sid-own-0001").get_json()
    assert det["status"] == "running" and det["final"] is False
    assert _row("sid-own-0001")["status"] == "running"
    # 启动清理的语义不变：它照旧会把本进程 pid 的旧行当上一实例遗留收掉
    assert ai_history.mark_interrupted_running() == 1


@pytest.mark.parametrize("status", ["cancelled", "interrupted"])
def test_non_running_open_row_only_gets_ended_ms(client, status):
    owner = _spawn_owner()
    sid = f"sid-{status}-001"
    _start(sid, owner.pid)
    ai_history.update_status(sid, status)
    _kill(owner)

    assert ai_history.reap_dead_owners(PROJECT) == 0  # 回值只数 running → interrupted
    row = _row(sid)
    assert row["status"] == status and row["ended_ms"] is not None
    assert _open_ids(client) == []


def test_detail_of_dead_owner_cancelled_row_has_ended_ms(client):
    owner = _spawn_owner()
    _start("sid-canc-0001", owner.pid)
    ai_history.update_status("sid-canc-0001", "cancelled")
    _kill(owner)
    det = client.get("/api/ai/sessions/sid-canc-0001").get_json()
    assert det["status"] == "cancelled" and det["final"] is True
    assert det["ended_ms"] is not None


def test_reap_is_scoped_to_the_project(client):
    owner = _spawn_owner()
    _start("sid-proj-0001", owner.pid)
    _kill(owner)
    assert ai_history.reap_dead_owners("/another-project") == 0
    assert _row("sid-proj-0001")["status"] == "running"
    assert ai_history.reap_dead_owners(PROJECT) == 1


def test_nothing_dead_means_no_write_lock(client, monkeypatch):
    """列表轮询很勤：没有死属主时不该去拿写锁。"""
    owner = _spawn_owner()
    try:
        _start("sid-lock-0001", owner.pid)

        def boom(*_a, **_k):
            raise AssertionError("不该拿写锁")

        monkeypatch.setattr(ai_history, "_immediate", boom)
        assert ai_history.reap_dead_owners(PROJECT) == 0
    finally:
        _kill(owner)


def test_concurrent_reap_is_idempotent(client):
    owner = _spawn_owner()
    ids = [f"sid-conc-{i:04d}" for i in range(3)]
    for i, sid in enumerate(ids):
        _start(sid, owner.pid, script=f"s{i}.py")
    _kill(owner)

    results: list[int] = []
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        results.append(ai_history.reap_dead_owners(PROJECT))

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == len(ids), results  # 每行只转一次
    ended = {sid: _row(sid)["ended_ms"] for sid in ids}
    assert all(v is not None for v in ended.values())
    assert ai_history.reap_dead_owners(PROJECT) == 0
    assert {sid: _row(sid)["ended_ms"] for sid in ids} == ended  # 再收一次不改写 ended_ms
