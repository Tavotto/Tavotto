"""AI 会话版本号与增量对账（#1115）：`sessions.rev` 单调递增、`GET /api/ai/sessions/changes?since=` 的过滤与项目隔离。

真实子进程（另一个服务进程里的写入）+ 真实 Flask test client + 真实 SQLite（临时目录）。
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from tavotto import app as m
from tavotto.engine import ai_bridge, ai_history, project_watch as engine_watch

PROJECT = "/p-rev"
OTHER = "/p-rev-other"


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


def _start(sid: str, project: str = PROJECT, script: str = "a.py", **extra) -> None:
    ok, _ = ai_history.record_start(
        {
            "id": sid,
            "project": project,
            "provider": "codex",
            "script": script,
            "prompt": "p",
            **extra,
        }
    )
    assert ok


def _revs(*sids: str) -> list[int]:
    return [ai_history.get(s)["rev"] for s in sids]


def test_every_visible_write_bumps_rev_strictly(db):
    _start("s1")
    r_start = ai_history.get("s1")["rev"]
    assert r_start == 1
    assert ai_history.record_task_phase("s1", "planning")
    r_phase = ai_history.get("s1")["rev"]
    ai_history.update_status("s1", "cancelled")
    r_status = ai_history.get("s1")["rev"]
    r_end = ai_history.record_end("s1", "cancelled", diff="d", changed=True)
    assert r_start < r_phase < r_status < r_end
    assert ai_history.get("s1")["rev"] == r_end
    assert ai_history.head_rev() == r_end


def test_rev_is_table_wide_monotonic_across_sessions_and_projects(db):
    _start("s1")
    _start("s2", project=OTHER, script="b.py")
    ai_history.record_end("s1", "done")
    _start("s3", script="c.py")
    got = [ai_history.get(s)["rev"] for s in ("s2", "s1", "s3")]
    assert got == sorted(got) and len(set(got)) == 3


def test_heartbeat_does_not_bump_rev(db):
    _start("s1")
    before = ai_history.head_rev()
    assert ai_history.owner_tick() == []
    assert ai_history.head_rev() == before


def test_two_processes_writing_get_strictly_increasing_unique_revs(db):
    """两个进程、各自的连接同时写：版本号不撞、不回退（`BEGIN IMMEDIATE` 里 MAX+1）。"""
    ai_history._connect().close()  # 先建库，避免两个进程同时建表
    child = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from tavotto.engine import ai_history
        ai_history.DB_PATH = Path(sys.argv[1])
        tag = sys.argv[2]
        for i in range(40):
            sid = f"{tag}-{i}"
            ai_history.record_start({"id": sid, "project": "/p-rev", "provider": "codex", "script": sid, "prompt": "p"})
            ai_history.record_end(sid, "done")
        """
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", child, str(db), tag], stderr=subprocess.PIPE)
        for tag in ("a", "b")
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    for i in range(40):
        _start(f"c-{i}", script=f"c{i}")  # 本进程也写
    with ai_history._connect() as con:
        revs = [r[0] for r in con.execute("SELECT rev FROM sessions ORDER BY rev")]
    assert len(revs) == 120
    assert len(set(revs)) == 120, "版本号撞了"
    # 每个会话最终的 rev 是它最后一次写（终态）拿到的：同一进程里后结束的会话 rev 更大
    for tag in ("a", "b"):
        ends = [ai_history.get(f"{tag}-{i}")["rev"] for i in range(40)]
        assert ends == sorted(ends)


def test_changes_filters_by_since_and_returns_slim_dto(client):
    _start("s1")
    _start("s2", script="b.py")
    ai_history.record_end(
        "s1", "done", diff="DIFF", changed=True, transcript=[{"kind": "message", "text": "x"}]
    )
    head0 = client.get("/api/ai/sessions/changes").get_json()["head"]
    first = client.get("/api/ai/sessions/changes?since=0").get_json()
    assert {s["id"] for s in first["sessions"]} == {"s1", "s2"}
    assert first["head"] == head0 and first["more"] is False
    s1 = next(s for s in first["sessions"] if s["id"] == "s1")
    assert s1["status"] == "done" and s1["final"] is True and s1["changed"] is True
    assert set(s1) == set(ai_bridge.CHANGE_DTO_KEYS), "增量 DTO 是闭集"
    assert "diff" not in s1 and "transcript" not in s1, "终态详情走 GET sessions/<sid>，不在增量里"
    # 水位之后只有新的写
    assert (
        client.get(f"/api/ai/sessions/changes?since={first['head']}").get_json()["sessions"] == []
    )
    ai_history.record_end("s2", "failed", error="boom")
    again = client.get(f"/api/ai/sessions/changes?since={first['head']}").get_json()
    assert [s["id"] for s in again["sessions"]] == ["s2"]
    assert again["sessions"][0]["error"] == "boom" and again["head"] > first["head"]


def test_changes_without_since_is_baseline_only(client):
    _start("s1")
    out = client.get("/api/ai/sessions/changes").get_json()
    assert out == {"head": ai_history.head_rev(), "more": False, "sessions": []}


def test_changes_bad_since_is_400(client):
    assert client.get("/api/ai/sessions/changes?since=abc").status_code == 400
    assert client.get("/api/ai/sessions/changes?since=-1").status_code == 400


def test_changes_is_project_isolated_but_watermark_is_global(client):
    _start("mine")
    _start("theirs", project=OTHER, script="b.py")
    ai_history.record_end("theirs", "done", diff="SECRET", changed=True)
    out = client.get("/api/ai/sessions/changes?since=0").get_json()
    assert [s["id"] for s in out["sessions"]] == ["mine"]
    assert "theirs" not in str(out) and "SECRET" not in str(out)
    assert out["head"] == ai_history.head_rev()  # 水位全局：别项目的写只让它变大
    client.holder["project"] = OTHER
    assert [
        s["id"] for s in client.get("/api/ai/sessions/changes?since=0").get_json()["sessions"]
    ] == ["theirs"]


def test_changes_paginates_with_more_and_head_only_advances_to_last_row(client, monkeypatch):
    monkeypatch.setattr(ai_history, "CHANGES_PAGE", 2)
    for i in range(5):
        _start(f"s{i}", script=f"s{i}.py")
    seen: list[str] = []
    since = 0
    for _ in range(10):
        page = client.get(f"/api/ai/sessions/changes?since={since}").get_json()
        seen += [s["id"] for s in page["sessions"]]
        since = page["head"]
        if not page["more"]:
            break
    assert seen == [f"s{i}" for i in range(5)]


def test_live_cancelled_session_in_this_process_is_not_final(client):
    """本进程内存里"已取消、`_pump` 还没收尾"的会话：磁盘行 `ended_ms` 为空且属主是本进程，不能被当终态。"""
    _start("live", owner_pid=None)
    ai_history.update_status("live", "cancelled")
    ai_bridge.SESSIONS["live"] = {"id": "live", "status": "cancelled", "final": False}
    try:
        row = client.get("/api/ai/sessions/changes?since=0").get_json()["sessions"][0]
    finally:
        ai_bridge.SESSIONS.pop("live", None)
    assert row["status"] == "cancelled" and row["final"] is False


def test_dead_owner_row_is_reaped_into_a_visible_change(client):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _start("orphan", owner_pid=dead.pid)
    rev_before = ai_history.get("orphan")["rev"]
    out = client.get(f"/api/ai/sessions/changes?since={rev_before}").get_json()
    row = next(s for s in out["sessions"] if s["id"] == "orphan")
    assert row["status"] == "interrupted" and row["final"] is True and row["rev"] > rev_before


def test_ai_done_event_carries_the_rev_of_the_terminal_write(db):
    sess = {
        "id": "ev1",
        "status": "done",
        "diff": "",
        "changed": False,
        "error": None,
        "transcript": [],
        "refresh": None,
        "ended_ms": None,
    }
    _start("ev1")
    ai_bridge._finish_session(sess)
    assert sess["rev"] == ai_history.get("ev1")["rev"] == ai_history.head_rev()


def test_old_rows_without_rev_are_before_the_epoch(client, db):
    """升级前的老行（rev 为 NULL）不出现在增量里，也不破坏 `head`。"""
    _start("old")
    with ai_history._connect() as con:
        con.execute("UPDATE sessions SET rev=NULL")
        con.execute("UPDATE ai_rev_counter SET rev=0")  # 升级前的库：还没有任何带版本的写
    assert client.get("/api/ai/sessions/changes?since=0").get_json() == {
        "head": 0,
        "more": False,
        "sessions": [],
    }
    _start("new", script="n.py")
    assert [
        s["id"] for s in client.get("/api/ai/sessions/changes?since=0").get_json()["sessions"]
    ] == ["new"]


def test_rev_never_falls_back_after_the_newest_row_is_deleted(client):
    """删掉最新一行（用户删历史 / 启动清理）后，新会话的 rev 仍必须大于任何客户端可能持有的水位。"""
    _start("keep")
    _start("newest", script="b.py")
    assert ai_history.get("newest")["rev"] == 2
    watermark = client.get("/api/ai/sessions/changes").get_json()["head"]
    assert ai_history.delete("newest")
    _start("later", script="c.py")
    assert ai_history.get("later")["rev"] > watermark
    page = client.get(f"/api/ai/sessions/changes?since={watermark}").get_json()
    assert [s["id"] for s in page["sessions"]] == ["later"], "水位之后的新会话必须出现在增量里"


def test_counter_survives_purge_and_starts_from_existing_max(db):
    _start("a")
    with ai_history._connect() as con:
        con.execute("DROP TABLE ai_rev_counter")  # 升级前的库没有计数器
        con.execute("UPDATE sessions SET rev=41")
    _start("b", script="b.py")
    assert ai_history.get("b")["rev"] == 42  # 从现有最大版本起算
    ai_history.purge(keep_days=0)
    _start("c", script="c.py")
    assert ai_history.get("c")["rev"] == 43


def test_connecting_to_an_up_to_date_db_never_waits_for_the_write_lock(db):
    """别的进程正持着写锁时，新连接一个已迁移好的库只读探测就够了：不能为"补计数器 / 补账本"去抢写锁。

    每次连接都无条件写的话，两个进程同时打开 / 写库会互相排队，慢机器上排满 busy 超时就是 `database is locked`（Windows CI）。
    子进程在 4 s 内必须连上；修前它会在写锁上等到 10 s 超时。"""
    import sqlite3

    _start("seed", client_request_id="rid-seed-0000001")
    holder = sqlite3.connect(db, timeout=10, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # 另一个进程里的写事务，一直不提交
    try:
        code = (
            "import sys; from pathlib import Path; from tavotto.engine import ai_history;"
            "ai_history.DB_PATH = Path(sys.argv[1]); ai_history._connect().close(); print('ok')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code, str(db)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=4,
        )
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert out.returncode == 0 and "ok" in out.stdout, out.stderr


def test_a_missing_counter_is_initialised_once_under_the_write_lock(db):
    """升级前的库（没有计数器行）：初始化走显式写事务，等锁而不是抛错；之后的连接不再重复写。"""
    import sqlite3
    import threading
    import time

    _start("seed")
    with ai_history._connect() as con:
        con.execute("DELETE FROM ai_rev_counter")
    holder = sqlite3.connect(db, timeout=10, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    result: dict = {}

    def connect():
        try:
            ai_history._connect().close()
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["err"] = exc

    t = threading.Thread(target=connect)
    t.start()
    time.sleep(0.5)
    holder.execute("ROLLBACK")  # 释放：等锁的初始化应当接着完成
    holder.close()
    t.join(timeout=15)
    assert result == {"ok": True}, result
    assert ai_history.head_rev() == ai_history.get("seed")["rev"]
