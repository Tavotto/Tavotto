"""跨进程取消 AI 会话（#1113 主体）：持久取消意图 + 属主执行。

真实子进程（A：登记并跑一个带假 CLI 的会话的"另一个服务进程"）+ 真实 Flask test client（B）+ 真实 SQLite（临时目录）。
取消 = B 写意图（对观察到的 owner_token 做 CAS），A 的心跳线程收取并走它自己进程内的 `_cancel_local`；B 绝不对 A 的子进程发信号。
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from support import procprobe
from tavotto import app as m
from tavotto.engine import ai_bridge, ai_history, project_watch as engine_watch

posix_only = pytest.mark.skipif(
    os.name == "nt",
    reason="需要 POSIX 进程组（start_new_session / killpg）与 SIGKILL 来造「属主被杀」；Windows 的 taskkill 路径由 test_ai_idempotency 覆盖",
)

PROJECT = "/p-xcancel"

# 子进程 A：登记一个带假 CLI 的会话（假 CLI 把自己的 pid 写进 marker 后睡眠），把 sid 打印出来，然后活着等心跳线程干活
OWNER_SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from tavotto.engine import ai_bridge

    figs, marker = sys.argv[1], sys.argv[2]
    code = (
        "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(120)" % marker
    )
    ai_bridge.require_usable = lambda agent: None
    ai_bridge.ai_providers.resolve = lambda *a, **k: None
    ai_bridge._cmd = lambda *a, **k: ([sys.executable, "-c", code], {})
    sid = ai_bridge.run("codex", "fig1.py", "p", figs, client_request_id=sys.argv[3])
    print(sid, flush=True)
    time.sleep(120)
    """
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "data" / "cache" / "ai_history.sqlite3"
    monkeypatch.setattr(ai_history, "DB_PATH", path)
    return path


@pytest.fixture
def client(db, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    holder = {"project": PROJECT}
    monkeypatch.setattr(
        m, "require_project", lambda: type("P", (), {"resolve": lambda s: holder["project"]})()
    )
    c = m.app.test_client()
    c.holder = holder
    yield c
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture
def sleepers():
    procs: list[subprocess.Popen] = []

    def spawn() -> subprocess.Popen:
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        return p

    yield spawn
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


def _row(db, sid: str) -> dict:
    with sqlite3.connect(db) as raw:
        raw.row_factory = sqlite3.Row
        return dict(raw.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())


def _start(sid, owner_pid, *, token="tok-A", beat_age_ms=0, script="a.py", **extra) -> None:
    """替"另一个服务进程"登记一条 running 行。"""
    ok, got = ai_history.record_start(
        {
            "id": sid,
            "project": PROJECT,
            "provider": "codex",
            "script": script,
            "owner_pid": owner_pid,
            "owner_token": token,
            "client_request_id": f"rid-{sid}-0000001",
            **extra,
        }
    )
    assert (ok, got) == (True, sid)
    if token is not None and beat_age_ms:
        with sqlite3.connect(ai_history.DB_PATH) as raw:
            raw.execute(
                "UPDATE sessions SET owner_beat_ms=? WHERE id=?",
                (int(time.time() * 1000) - beat_age_ms, sid),
            )


def _cancel(client, sid):
    resp = client.post(f"/api/ai/sessions/{sid}/cancel")
    assert resp.status_code == 200
    return resp.get_json()


def _alive(pid: int) -> bool:
    return procprobe.alive(pid)  # Windows 上 os.kill(pid, 0) 会杀进程（#523），探测只经 procprobe


def _wait(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def owner(tmp_path, db, monkeypatch):
    """真实的另一个服务进程 A：共享数据目录，登记会话、起假 CLI；收尾时连假 CLI 的孤儿一起清掉。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text("print('x')\n", encoding="utf-8")
    marker = tmp_path / "cli.pid"
    env = dict(
        os.environ,
        TAVOTTO_DATA_DIR=str(tmp_path / "data"),
        PYTHONPATH=os.path.dirname(os.path.dirname(m.__file__)),
        PYTHONDONTWRITEBYTECODE="1",
        TAVOTTO_NO_TELEMETRY="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", OWNER_SCRIPT, str(figs), str(marker), "rid-xproc-00000001"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    sid = proc.stdout.readline().strip()
    assert sid, "A 没登记出会话"
    assert _wait(marker.exists) and _wait(lambda: marker.read_text().strip() != "")
    cli_pid = int(marker.read_text())
    info = {"proc": proc, "sid": sid, "cli_pid": cli_pid, "project": str(figs.resolve())}
    yield info
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)
    if _alive(cli_pid):
        os.kill(cli_pid, signal.SIGKILL)


# ----------------------------------------------------------------------------------- 真实两进程
@posix_only
def test_b_cancels_a_session_owned_by_a_and_a_kills_its_own_cli(client, db, owner):
    sid, cli_pid = owner["sid"], owner["cli_pid"]
    client.holder["project"] = owner["project"]
    row = _row(db, sid)
    assert (
        row["status"] == "running" and row["owner_token"] and row["owner_pid"] == owner["proc"].pid
    )
    assert sid not in ai_bridge.SESSIONS  # B 的内存里没有它

    body = _cancel(client, sid)
    assert body == {"ok": True, "mode": "owner"}  # 「已转交属主」，不是「已在本进程中止」
    assert _row(db, sid)["cancel_requested_ms"] is not None

    # A 的心跳线程收取意图 → A 自己终止它的（假）CLI → A 的泵线程收尾
    assert _wait(lambda: not _alive(cli_pid)), "A 没有终止它的假 CLI"
    assert _wait(lambda: _row(db, sid)["ended_ms"] is not None), "A 没有把行收尾"
    row = _row(db, sid)
    assert row["status"] == "cancelled" and row["cancel_result"] == "ok"
    det = client.get(f"/api/ai/sessions/{sid}").get_json()
    assert det["status"] == "cancelled" and det["final"] is True
    assert owner["proc"].poll() is None  # A 本身没被动过


@posix_only
def test_owner_killed_then_b_cancel_closes_the_row_instead_of_hanging(client, db, owner):
    sid = owner["sid"]
    client.holder["project"] = owner["project"]
    owner["proc"].send_signal(signal.SIGKILL)
    owner["proc"].wait(timeout=10)

    body = _cancel(client, sid)
    assert body["ok"] is False and body["reason"] == "owner_gone"
    row = _row(db, sid)
    assert row["status"] != "running" and row["ended_ms"] is not None
    assert row["cancel_requested_ms"] is None  # 属主已死：不写意图
    det = client.get(f"/api/ai/sessions/{sid}").get_json()
    assert det["final"] is True


# ----------------------------------------------------------------------------------- 意图的写入
def test_cancel_is_idempotent_and_writes_the_intent_once(client, db, sleepers):
    owner = sleepers()
    _start("sid-idem-0001", owner.pid)
    first = _cancel(client, "sid-idem-0001")
    stamp = _row(db, "sid-idem-0001")["cancel_requested_ms"]
    assert first == {"ok": True, "mode": "owner"} and stamp is not None
    time.sleep(0.01)
    assert _cancel(client, "sid-idem-0001") == {"ok": True, "mode": "owner"}
    assert _row(db, "sid-idem-0001")["cancel_requested_ms"] == stamp  # 重复取消不重复写
    row = _row(db, "sid-idem-0001")
    assert (
        row["cancel_token"] == "tok-A" and row["status"] == "running"
    )  # B 只写意图，行状态由属主翻


def test_cas_miss_when_owner_generation_changed_after_observation(db, sleepers):
    """B 读到 owner_token=tok-A 之后、写之前，行的属主换了代（pid 复用的新进程接手同一行）：意图不落。"""
    owner = sleepers()
    _start("sid-cas-00001", owner.pid, token="tok-A")
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE sessions SET owner_token='tok-B' WHERE id='sid-cas-00001'")
    assert ai_history.request_cancel("sid-cas-00001", PROJECT, expect_token="tok-A") == "stale"
    row = _row(db, "sid-cas-00001")
    assert row["cancel_requested_ms"] is None and row["cancel_token"] is None


def test_old_intent_never_executes_under_a_new_owner_token(db, sleepers):
    owner = sleepers()
    _start("sid-old-00001", owner.pid, token="tok-A")
    assert ai_history.request_cancel("sid-old-00001", PROJECT) == "requested"
    assert ai_history.owner_tick("tok-A") == ["sid-old-00001"]  # 意图属于 tok-A：它自己收得到
    with sqlite3.connect(db) as raw:  # 同一行换了代
        raw.execute("UPDATE sessions SET owner_token='tok-B' WHERE id='sid-old-00001'")
    assert (
        ai_history.owner_tick("tok-B") == []
    )  # 新属主看到的是旧意图（cancel_token=tok-A）：不执行
    assert ai_history.owner_tick("tok-A") == []  # 旧属主也不再拥有这一行


def test_intent_is_executed_only_for_this_process_token(db):
    ai_history.record_start(
        {
            "id": "sid-mine-0001",
            "project": PROJECT,
            "provider": "codex",
            "script": "m.py",
            "client_request_id": "rid-mine-000000001",
        }
    )
    with sqlite3.connect(db) as raw:
        raw.execute(
            "UPDATE sessions SET cancel_requested_ms=1, cancel_token='someone-else' WHERE id='sid-mine-0001'"
        )
    assert ai_history.owner_tick() == []  # 针对别的 token 的意图，本进程不执行
    with sqlite3.connect(db) as raw:
        raw.execute(
            "UPDATE sessions SET cancel_token=? WHERE id='sid-mine-0001'", (ai_history.OWNER_TOKEN,)
        )
    assert ai_history.owner_tick() == ["sid-mine-0001"]


# ----------------------------------------------------------------------------------- 属主执行（进程内）
def test_owner_tick_runs_the_local_cancel_path_and_records_the_result(db, tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text("print('x')\n", encoding="utf-8")
    code = "import time; time.sleep(60)"
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(ai_bridge, "_cmd", lambda *a, **k: ([sys.executable, "-c", code], {}))
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-tick-00000001")
    try:
        assert ai_bridge._OWNER_LOOP is not None  # run() 起了心跳线程
        with sqlite3.connect(db) as raw:  # 别的进程写下的意图（针对本进程这一代）
            raw.execute(
                "UPDATE sessions SET cancel_requested_ms=1, cancel_token=? WHERE id=?",
                (ai_history.OWNER_TOKEN, sid),
            )
        assert ai_bridge._owner_tick() == [sid]
        assert ai_bridge.SESSIONS[sid]["status"] == "cancelled"
        assert _row(db, sid)["cancel_result"] == "ok"
        assert ai_bridge._owner_tick() == []  # 已有结果：不重复执行
        assert _wait(lambda: _row(db, sid)["ended_ms"] is not None)
    finally:
        ai_bridge.stop_owner_loop()
        ai_bridge.SESSIONS.pop(sid, None)


# ----------------------------------------------------------------------------------- 授权
def test_cross_project_cancel_is_rejected_and_writes_nothing(client, db, sleepers):
    owner = sleepers()
    _start("sid-proj-0001", owner.pid)
    client.holder["project"] = "/some-other-project"
    body = _cancel(client, "sid-proj-0001")
    assert body == {"ok": False, "reason": "not_found"}  # 与不存在的 sid 同一个回答，不泄露存在与否
    assert _row(db, "sid-proj-0001")["cancel_requested_ms"] is None
    assert _cancel(client, "sid-never-0001") == body
    client.holder["project"] = PROJECT
    assert _cancel(client, "sid-proj-0001")["ok"] is True


# ----------------------------------------------------------------------------------- 属主死活判据
def test_very_stale_heartbeat_counts_as_dead_even_if_the_pid_is_alive(client, db, sleepers):
    """pid 被复用（新进程不会替我们续心跳）：pid 还在，心跳超过判死线 → 收尾而不是写悬挂的意图。"""
    owner = sleepers()
    _start("sid-beat-0001", owner.pid, beat_age_ms=ai_history.OWNER_BEAT_DEAD_MS + 60_000)
    body = _cancel(client, "sid-beat-0001")
    assert body == {"ok": False, "reason": "owner_gone"}
    row = _row(db, "sid-beat-0001")
    assert row["status"] == "interrupted" and row["ended_ms"] is not None


def test_moderately_stale_heartbeat_is_unresponsive_not_dead(client, db, sleepers):
    """pid 活 + 心跳 40 s 旧（高负载下线程被饿住）：读路径不收尾，取消不写意图、不改行。"""
    owner = sleepers()
    _start("sid-slow-0001", owner.pid, beat_age_ms=40_000)
    assert ai_history.reap_dead_owners(PROJECT) == 0
    assert _row(db, "sid-slow-0001")["status"] == "running"
    det = client.get("/api/ai/sessions/sid-slow-0001").get_json()
    assert det["status"] == "running" and det["final"] is False
    assert _cancel(client, "sid-slow-0001") == {"ok": False, "reason": "owner_unresponsive"}
    row = _row(db, "sid-slow-0001")
    assert (
        row["status"] == "running"
        and row["ended_ms"] is None
        and row["cancel_requested_ms"] is None
    )


def test_stale_heartbeat_still_blocks_a_new_session_but_a_dead_one_does_not(db, sleepers):
    owner = sleepers()
    _start("sid-busy-0001", owner.pid, beat_age_ms=40_000, script="s.py")
    with pytest.raises(ai_history.ScriptBusy):  # 40 s 旧：pid 活着，仍然忙
        ai_history.record_start(
            {
                "id": "sid-busy-0002",
                "project": PROJECT,
                "provider": "codex",
                "script": "s.py",
                "client_request_id": "rid-busy-000000002",
            }
        )
    _start(
        "sid-busy-0003",
        owner.pid,
        beat_age_ms=ai_history.OWNER_BEAT_DEAD_MS + 60_000,
        script="t.py",
    )
    ok, _ = ai_history.record_start(
        {
            "id": "sid-busy-0004",
            "project": PROJECT,
            "provider": "codex",
            "script": "t.py",
            "client_request_id": "rid-busy-000000004",
        }
    )
    assert ok  # 超过判死线：忙碌判定与收尾同一个判据


def test_pending_intent_of_a_dead_owner_resolves_to_cancelled(db, sleepers):
    owner = sleepers()
    _start("sid-pend-0001", owner.pid)
    assert ai_history.request_cancel("sid-pend-0001", PROJECT) == "requested"
    owner.kill()
    owner.wait(timeout=10)
    ai_history.reap_dead_owners(PROJECT)
    row = _row(db, "sid-pend-0001")
    assert (
        row["status"] == "cancelled" and row["ended_ms"] is not None
    )  # 用户要的是「取消」，不是「中断」
    assert row["cancel_result"] == "owner_gone"


def test_legacy_row_without_token_keeps_the_old_semantics(client, db, sleepers):
    owner = sleepers()
    _start("sid-leg-00001", owner.pid, token=None)
    row = _row(db, "sid-leg-00001")
    assert row["owner_token"] is None and row["owner_beat_ms"] is None
    # 活着的老属主不会轮询意图：不写悬挂的意图
    assert _cancel(client, "sid-leg-00001") == {"ok": False, "reason": "owner_unsupported"}
    assert _row(db, "sid-leg-00001")["cancel_requested_ms"] is None
    # 没有心跳可比：只要 pid 活着、不超过 24h 就仍算活着（不被心跳判据误杀）
    assert ai_history.reap_dead_owners(PROJECT) == 0
    assert _row(db, "sid-leg-00001")["status"] == "running"
    # 老属主死了：沿用原收尾
    owner.kill()
    owner.wait(timeout=10)
    assert ai_history.reap_dead_owners(PROJECT) == 1
    assert _row(db, "sid-leg-00001")["status"] == "interrupted"


def test_committing_task_refuses_the_cross_process_cancel(client, db, sleepers):
    owner = sleepers()
    _start("sid-comm-0001", owner.pid)
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE sessions SET phase='committing' WHERE id='sid-comm-0001'")
    assert _cancel(client, "sid-comm-0001") == {"ok": False, "reason": "committing"}
    assert _row(db, "sid-comm-0001")["cancel_requested_ms"] is None


def test_finished_session_has_nothing_to_cancel(client, db, sleepers):
    owner = sleepers()
    _start("sid-done-0001", owner.pid)
    ai_history.record_end("sid-done-0001", "done")
    assert _cancel(client, "sid-done-0001") == {"ok": False, "reason": "not_running"}


def test_own_process_row_not_in_memory_is_not_written_as_an_intent(client, db):
    """本进程这一代登记、却不在 SESSIONS 里（登记与进内存之间的窗口 / 刚收尾）：不对自己写意图。"""
    _start("sid-own-00001", os.getpid(), token=ai_history.OWNER_TOKEN)
    assert _cancel(client, "sid-own-00001") == {"ok": False, "reason": "own"}
    assert _row(db, "sid-own-00001")["cancel_requested_ms"] is None


def test_new_columns_are_added_to_an_old_database(tmp_path):
    """老库（没有 #1113 的五列）打开即补列、老行全 NULL，不动 user_version。"""
    old = tmp_path / "old.sqlite3"
    with sqlite3.connect(old) as raw:
        raw.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, project TEXT NOT NULL, provider TEXT NOT NULL, "
            "prompt TEXT NOT NULL, status TEXT NOT NULL, started_ms INTEGER NOT NULL)"
        )
        raw.execute("INSERT INTO sessions VALUES ('old1', '/p', 'codex', 'x', 'running', 1)")
        before = raw.execute("PRAGMA user_version").fetchone()[0]
    with ai_history._connect(old) as con:
        row = dict(con.execute("SELECT * FROM sessions WHERE id='old1'").fetchone())
    for col in (
        "owner_token",
        "owner_beat_ms",
        "cancel_requested_ms",
        "cancel_token",
        "cancel_result",
    ):
        assert row[col] is None
    with sqlite3.connect(old) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == before


# ----------------------------------------------------------------------------------- 登记与入内存之间的窗口（Codex #1229 P1）
@pytest.fixture
def window_env(db, tmp_path, monkeypatch):
    """`record_start` 已提交、`SESSIONS[sid]` 还没放进去的窗口：用 Popen 桩停在里面，让"另一个进程"在此刻写意图并让 tick 跑一次。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(
        ai_bridge,
        "_cmd",
        lambda *a, **k: ([sys.executable, "-c", "import time; time.sleep(60)"], {}),
    )
    monkeypatch.setattr(ai_bridge, "OWNER_TICK_S", 3600.0)  # 后台线程不抢 tick：用例自己驱动
    real_popen = subprocess.Popen
    state = {"in_window": None}

    def intent_arrives(sid):
        with sqlite3.connect(db) as raw:  # 别的进程写下的意图（针对本进程这一代）
            raw.execute(
                "UPDATE sessions SET cancel_requested_ms=1, cancel_token=? WHERE id=?",
                (ai_history.OWNER_TOKEN, sid),
            )

    def popen_in_window(*a, **k):
        proc = real_popen(*a, **k)
        if state["in_window"] is not None:
            return proc  # 只有 CLI 那一次 spawn 停在窗口里；之后的 Popen（Windows 的 taskkill 等）一律透传
        with sqlite3.connect(db) as raw:
            sid = raw.execute("SELECT id FROM sessions ORDER BY started_ms DESC").fetchone()[0]
        intent_arrives(sid)
        state["in_window"] = ai_bridge._owner_tick()  # 此刻会话不在 SESSIONS 里
        return proc

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", popen_in_window)
    yield figs, state
    ai_bridge.stop_owner_loop()
    for sid in list(ai_bridge.SESSIONS):
        sess = ai_bridge.SESSIONS.pop(sid)
        ai_bridge._terminate(sess)


def test_intent_arriving_between_register_and_memory_is_not_swallowed(window_env, db):
    figs, state = window_env
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-window-0000001")
    assert state["in_window"] == []  # tick 在窗口里看到的是"不在内存"：既没执行也没消费
    # 不等下一个 tick（它被调成 1 小时）：入内存后 run() 自己补查，会话已经被取消
    assert ai_bridge.SESSIONS[sid]["status"] == "cancelled"
    assert _row(db, sid)["cancel_result"] == "ok"
    assert _wait(lambda: _row(db, sid)["ended_ms"] is not None)
    assert _row(db, sid)["status"] == "cancelled"


def test_tick_in_the_window_leaves_the_intent_pending(window_env, db, monkeypatch):
    """去掉 run() 的补查也不丢：tick 不消费，意图仍待执行，下一个 tick 照样执行。"""
    figs, state = window_env
    real = ai_bridge._execute_cancel_intent
    monkeypatch.setattr(
        ai_bridge, "_execute_cancel_intent", lambda sid: False
    )  # run() 的补查被拿掉
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-window-0000002")
    assert _row(db, sid)["cancel_result"] is None and ai_bridge.SESSIONS[sid]["status"] == "running"
    monkeypatch.setattr(ai_bridge, "_execute_cancel_intent", real)  # 下一个 tick 用真实的执行入口
    assert ai_bridge._owner_tick() == [sid]
    assert (
        ai_bridge.SESSIONS[sid]["status"] == "cancelled" and _row(db, sid)["cancel_result"] == "ok"
    )


def test_intent_in_the_window_settles_with_the_terminal_state_when_spawn_fails(
    db, tmp_path, monkeypatch
):
    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "fig1.py").write_text("print('x')\n", encoding="utf-8")
    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(ai_bridge, "_cmd", lambda *a, **k: ([sys.executable, "-c", "pass"], {}))
    monkeypatch.setattr(ai_bridge, "OWNER_TICK_S", 3600.0)

    spawned = []

    def boom(*a, **k):
        spawned.append(1)
        assert len(spawned) == 1, "只有 CLI 那一次 spawn 被替换"
        with sqlite3.connect(db) as raw:
            sid = raw.execute("SELECT id FROM sessions").fetchone()[0]
            raw.execute(
                "UPDATE sessions SET cancel_requested_ms=1, cancel_token=? WHERE id=?",
                (ai_history.OWNER_TOKEN, sid),
            )
        raise OSError("spawn failed")

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", boom)
    with pytest.raises(OSError):
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-window-0000003")
    ai_bridge.stop_owner_loop()
    with sqlite3.connect(db) as raw:
        raw.row_factory = sqlite3.Row
        row = dict(raw.execute("SELECT * FROM sessions").fetchone())
    assert row["status"] == "failed" and row["cancel_result"] == "failed"  # 意图随终态收尾，不悬挂
    assert ai_history.owner_tick() == []


def test_terminating_inside_the_intent_executor_cannot_deadlock(window_env, db, monkeypatch):
    """执行意图时 `_terminate` 会 spawn 子进程（Windows 的 taskkill 就是一次 Popen）；任何在这期间重入执行入口的路径
    （这里用 `_terminate` 里再跑一次 tick 模拟）都不能撞上自己持有的锁。"""
    import threading

    figs, state = window_env
    state["in_window"] = []  # 这条用例不要窗口里的注入
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-window-0000004")
    with sqlite3.connect(db) as raw:
        raw.execute(
            "UPDATE sessions SET cancel_requested_ms=1, cancel_token=? WHERE id=?",
            (ai_history.OWNER_TOKEN, sid),
        )
    real_terminate = ai_bridge._terminate
    reentered = []

    def terminate_that_reenters(sess):
        reentered.append(ai_bridge._owner_tick())  # 重入：同一条意图此刻正在执行中
        real_terminate(sess)

    monkeypatch.setattr(ai_bridge, "_terminate", terminate_that_reenters)
    out = []
    t = threading.Thread(target=lambda: out.append(ai_bridge._owner_tick()), daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive(), "执行意图时重入执行入口死锁了"
    assert out == [[sid]] and reentered == [[]]  # 重入的那次什么都没做（幂等），外层执行了一次
    assert _row(db, sid)["cancel_result"] == "ok"
