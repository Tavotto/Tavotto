"""AI 修改任务的幂等与并发保护（PR4「AI 草稿与提交可靠性」）。

不变式：**任何操作都不得让同一条 AI 修改被无意执行两次**。前端点「发送」时生成
`client_request_id`；响应丢了 / 超时了，它带着同一个 id 再发，后端按 (项目, id) 认出这是
同一条、返回既有会话、**不再 spawn**。另一半是并发保护：同一脚本上已有任务在跑，不同请求
一律 409 `ai_script_busy`（以前只有前端的 `runningHere` 挡，刷新 / 回切 / 双标签都绕得过）。

用例走真实的 `run()`（假 CLI = 一个往计数文件里追加一行的 Python 子进程）：数 spawn 次数，
不数函数调用。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import ai_bridge, ai_history, project_watch as engine_watch

RID = "11111111-2222-4333-8444-555555555555"
RID2 = "99999999-2222-4333-8444-555555555555"
SECRET = "SECRET-PROMPT-DO-NOT-LOG"

SCRIPT = "def main():\n    pass\n"


@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """假 CLI：每次被 spawn 往 counter 追加一行，然后睡 `delay` 秒。"""
    counter = tmp_path / "spawned.txt"
    state = {"delay": 0.0}

    def fake_cmd(*_a, **_k):
        code = "import time; open({c!r}, 'a').write('x\\n'); time.sleep({d})".format(
            c=str(counter), d=state["delay"]
        )
        return [sys.executable, "-c", code], {}

    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(ai_bridge, "_cmd", fake_cmd)
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    before = set(ai_bridge.SESSIONS)
    yield state, counter
    for sid in set(ai_bridge.SESSIONS) - before:
        ai_bridge.SESSIONS.pop(sid, None)


def _project(tmp_path, name="figs"):
    figs = tmp_path / name
    figs.mkdir()
    (figs / "fig1.py").write_text(SCRIPT, encoding="utf-8")
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "Fig1.pdf")
    doc.close()
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    return figs


def _open(client, figs):
    return client.post("/api/projects/open", json={"path": str(figs), "default": True}).get_json()


def _post(client, rid=RID, prompt="把图例挪到右上角"):
    body = {"agent": "codex", "id": "Fig1.pdf", "prompt": prompt}
    if rid is not None:
        body["client_request_id"] = rid
    return client.post("/api/ai/run", json=body)


def _wait_idle(timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(s["status"] != "running" for s in ai_bridge.SESSIONS.values()):
            return
        time.sleep(0.05)
    raise AssertionError("会话没在时限内结束")


def _spawned(counter) -> int:
    return len(counter.read_text().splitlines()) if counter.exists() else 0


# ---------------------------------------------------------------------------
# 幂等：同 (项目, rid) 只 spawn 一次
# ---------------------------------------------------------------------------
def test_same_request_id_spawns_exactly_once(client, tmp_path, cli, monkeypatch):
    state, counter = cli
    state["delay"] = 0.6
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)

    first = _post(client)
    again = _post(client)  # 任务还在跑：响应丢了、前端用同一个 id 重发
    assert first.status_code == 200 and again.status_code == 200
    assert again.get_json()["session"] == first.get_json()["session"]
    assert again.get_json()["deduped"] is True and "deduped" not in first.get_json()

    _wait_idle()
    third = _post(client)  # 任务结束之后再重发，仍是既有会话
    assert third.get_json()["session"] == first.get_json()["session"]
    assert _spawned(counter) == 1, "同一个请求 id 被执行了不止一次"


def test_dedupe_survives_a_backend_restart(client, tmp_path, cli, monkeypatch):
    """内存会话没了（后端重启）：历史库里的 (项目, rid) 仍然认得出这是同一条。"""
    state, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    sid = _post(client).get_json()["session"]
    _wait_idle()
    ai_bridge.SESSIONS.pop(sid)

    again = _post(client)
    assert again.get_json() == {"session": sid, "script": "fig1.py", "deduped": True}
    assert _spawned(counter) == 1


def test_different_request_ids_are_independent_tasks(client, tmp_path, cli, monkeypatch):
    _, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    a = _post(client, RID).get_json()["session"]
    _wait_idle()
    b = _post(client, RID2).get_json()["session"]
    assert a != b
    _wait_idle()
    assert _spawned(counter) == 2


def test_requests_without_an_id_still_work(client, tmp_path, cli, monkeypatch):
    """老前端不带 id：行为不变（不去重，但仍受忙检查保护）。"""
    _, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    assert _post(client, None).status_code == 200
    _wait_idle()
    assert _post(client, None).status_code == 200
    _wait_idle()
    assert _spawned(counter) == 2


@pytest.mark.parametrize("bad", ["", "short", "has space in it!!", "x" * 65, 12345, ["a"]])
def test_malformed_request_id_is_rejected_not_ignored(client, tmp_path, cli, bad):
    _, counter = cli
    _open(client, _project(tmp_path))
    assert _post(client, bad).status_code == 400
    assert _spawned(counter) == 0


# ---------------------------------------------------------------------------
# 并发保护：同脚本上不同请求 → 409 ai_script_busy
# ---------------------------------------------------------------------------
def test_second_request_on_a_busy_script_is_409(client, tmp_path, cli, monkeypatch):
    state, counter = cli
    state["delay"] = 1.5
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    assert _post(client, RID).status_code == 200

    busy = _post(client, RID2)
    assert busy.status_code == 409
    body = busy.get_json()
    assert body["code"] == "ai_script_busy" and body["params"] == {"script": "fig1.py"}

    # 被拒的请求没有留下任何东西：它的 id 查不到，之后空闲了可以正常发
    assert client.get(f"/api/ai/requests/{RID2}").status_code == 404
    _wait_idle()
    assert _post(client, RID2).status_code == 200
    _wait_idle()
    assert _spawned(counter) == 2


def test_retrying_a_running_request_is_not_a_busy_error(client, tmp_path, cli, monkeypatch):
    """去重先于忙检查：重发「正在跑的那一条」得到既有会话，不是 409。"""
    state, _ = cli
    state["delay"] = 1.0
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    _post(client, RID)
    assert _post(client, RID).status_code == 200


def test_concurrent_posts_with_one_id_spawn_once(client, tmp_path, cli, monkeypatch):
    state, counter = cli
    state["delay"] = 0.5
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    results: list = []

    def go():
        with m.app.test_client() as c:
            results.append(_post(c).get_json())

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    _wait_idle()
    assert len({r["session"] for r in results}) == 1
    assert _spawned(counter) == 1


# ---------------------------------------------------------------------------
# 查询端点：按项目隔离
# ---------------------------------------------------------------------------
def test_request_lookup_reports_status_and_404_when_unknown(client, tmp_path, cli, monkeypatch):
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    missing = client.get(f"/api/ai/requests/{RID}")
    assert missing.status_code == 404 and missing.get_json()["code"] == "ai_request_not_found"

    sid = _post(client).get_json()["session"]
    _wait_idle()
    found = client.get(f"/api/ai/requests/{RID}")
    assert found.status_code == 200
    assert found.get_json() == {
        "session": sid,
        "status": "done",
        "changed": False,
        "script": "fig1.py",
    }
    assert client.get("/api/ai/requests/not valid!").status_code in (400, 404)


def test_request_lookup_is_scoped_to_the_project(tmp_path, cli, monkeypatch):
    """同一个 rid 在项目 B 里查不到项目 A 的会话；B 里用同一个 rid 发是一条全新的请求。"""
    state, counter = cli
    pa, pb = _project(tmp_path, "a"), _project(tmp_path, "b")
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    sid_a = ai_bridge.run("codex", "fig1.py", "p", str(pa), client_request_id=RID)
    _wait_idle()
    assert ai_bridge.find_request(str(pa), RID)["session"] == sid_a
    assert ai_bridge.find_request(str(pb), RID) is None
    # 历史库层面也隔离（内存里的会话被清掉后）
    ai_bridge.SESSIONS.pop(sid_a)
    assert ai_bridge.find_request(str(pb), RID) is None
    sid_b = ai_bridge.run("codex", "fig1.py", "p", str(pb), client_request_id=RID)
    assert sid_b != sid_a
    _wait_idle()
    assert _spawned(counter) == 2


# ---------------------------------------------------------------------------
# 事件带 rid（前端据此认领早于 HTTP 响应到达的事件）
# ---------------------------------------------------------------------------
def test_events_carry_the_request_id(client, tmp_path, cli, monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(m, "sse_publish", lambda ev, data: events.append((ev, data)))
    status = _open(client, _project(tmp_path))
    _post(client)
    _wait_idle()
    done = [d for ev, d in events if ev == "ai.done"]
    assert done and all(d["client_request_id"] == RID and d["pj"] == status["id"] for d in done)


# ---------------------------------------------------------------------------
# 迁移：老库补列 + 唯一索引，NULL 不参与
# ---------------------------------------------------------------------------
def test_old_database_is_migrated_and_unique_per_project(tmp_path):
    db = tmp_path / "old.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        """CREATE TABLE sessions (
          id TEXT PRIMARY KEY, project TEXT NOT NULL, canvas TEXT, panel TEXT, element TEXT,
          provider TEXT NOT NULL, model TEXT, effort TEXT, scope TEXT, target TEXT, script TEXT,
          prompt TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '[]', diff TEXT NOT NULL DEFAULT '',
          changed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, error TEXT,
          started_ms INTEGER NOT NULL, ended_ms INTEGER, snapshot_path TEXT,
          pinned INTEGER NOT NULL DEFAULT 0);
        INSERT INTO sessions (id, project, provider, prompt, status, started_ms)
          VALUES ('old1', '/p', 'codex', '旧记录', 'done', 1);"""
    )
    con.commit()
    con.close()

    # 反复打开幂等
    for _ in range(2):
        assert ai_history.get("old1", db_path=db)["client_request_id"] is None

    def start(sid, project, rid):
        ai_history.record_start(
            {
                "id": sid,
                "project": project,
                "provider": "codex",
                "prompt": "p",
                "client_request_id": rid,
            },
            db_path=db,
        )

    start("n1", "/p", None)
    start("n2", "/p", None)  # NULL 不参与唯一
    start("a", "/p", RID)
    start("b", "/q", RID)  # 别的项目同 id 没问题
    assert ai_history.find_by_request("/p", RID, db_path=db)["id"] == "a"
    assert ai_history.find_by_request("/q", RID, db_path=db)["id"] == "b"
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(db) as raw:
            raw.execute(
                "INSERT INTO sessions (id, project, provider, prompt, status, started_ms, "
                "client_request_id) VALUES ('c', '/p', 'codex', 'p', 'done', 2, ?)",
                (RID,),
            )


# ---------------------------------------------------------------------------
# 隐私：提示词与请求 id 不进日志
# ---------------------------------------------------------------------------
def test_prompt_and_request_id_never_reach_the_log(client, tmp_path, cli, monkeypatch, caplog):
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    _open(client, _project(tmp_path))
    with caplog.at_level(logging.DEBUG):
        _post(client, RID, prompt=SECRET)
        _post(client, RID, prompt=SECRET)  # 去重那条日志也一样
        _wait_idle()
        client.get(f"/api/ai/requests/{RID}")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in text and RID not in text


# ---------------------------------------------------------------------------
# 幂等账本独立于用户可删的历史（Codex #1098 P2）
# ---------------------------------------------------------------------------
def test_deleting_history_does_not_make_the_request_look_unseen(client, tmp_path, cli, monkeypatch):
    """请求跨后端重启仍未确认时，用户在任务历史里删了那一行：查询不得 404、同 rid 再 POST 不得 spawn。"""
    _, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    sid = _post(client).get_json()["session"]
    _wait_idle()
    ai_bridge.SESSIONS.pop(sid)  # 后端重启：内存会话没了
    assert client.delete(f"/api/ai/history/{sid}").get_json() == {"ok": True}
    assert ai_history.get(sid) is None  # 对照：历史行真的没了

    found = client.get(f"/api/ai/requests/{RID}")
    assert found.status_code == 200, "删了历史就 404 = 前端放开同 rid 重发 = 同一修改再执行一次"
    assert found.get_json() == {
        "session": sid,
        "status": "history_deleted",
        "changed": False,
        "script": None,
    }
    again = _post(client)
    assert again.status_code == 200 and again.get_json()["session"] == sid
    assert again.get_json()["deduped"] is True
    assert _spawned(counter) == 1


def test_unknown_request_is_still_404_after_the_ledger_exists(client, tmp_path, cli, monkeypatch):
    """对照：账本只认登记过的 (项目, rid)，别的 rid 照旧 404。"""
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    _post(client)
    _wait_idle()
    assert client.get(f"/api/ai/requests/{RID2}").status_code == 404


def test_ledger_is_scoped_to_the_project_and_holds_no_prompt(tmp_path):
    db = tmp_path / "led.sqlite3"
    ai_history.record_start(
        {
            "id": "s1",
            "project": "/p",
            "provider": "codex",
            "prompt": SECRET,
            "client_request_id": RID,
        },
        db_path=db,
    )
    ai_history.delete("s1", db_path=db)
    assert ai_history.find_request_record("/p", RID, db_path=db)["session"] == "s1"
    assert ai_history.find_request_record("/q", RID, db_path=db) is None
    with sqlite3.connect(db) as raw:
        cols = [r[1] for r in raw.execute("PRAGMA table_info(ai_requests)")]
        dump = repr(raw.execute("SELECT * FROM ai_requests").fetchall())
    assert cols == ["project", "client_request_id", "session", "created_ms"]
    assert SECRET not in dump


def test_ledger_is_backfilled_for_old_rows_and_purged_by_age(tmp_path):
    db = tmp_path / "led2.sqlite3"
    ai_history.record_start(
        {
            "id": "old",
            "project": "/p",
            "provider": "codex",
            "prompt": "p",
            "client_request_id": RID,
        },
        db_path=db,
    )
    with sqlite3.connect(db) as raw:  # 模拟「账本表出现之前登记的会话」
        raw.execute("DELETE FROM ai_requests")
    assert ai_history.find_request_record("/p", RID, db_path=db)["session"] == "old"  # 迁移补齐
    assert ai_history.purge_requests(30, db_path=db) == 0  # 对照：没超龄不清
    with sqlite3.connect(db) as raw:
        raw.execute("UPDATE ai_requests SET created_ms = 1")
    ai_history.delete("old", db_path=db)  # 历史行还在时迁移会把账本补回来；删了才看得出账本被清
    assert ai_history.purge_requests(30, db_path=db) == 1
    assert ai_history.find_request_record("/p", RID, db_path=db) is None


# ---------------------------------------------------------------------------
# 幂等记录先于 spawn 落库（Codex #1098 P1）
# ---------------------------------------------------------------------------
def test_a_failing_history_write_means_the_cli_is_never_started(tmp_path, cli, monkeypatch):
    """写库抛了（SQLite 被锁 / 满 / 损坏）：一个进程都不起。先 spawn 再写库的话，CLI 已经在改脚本，
    调用方却拿到「明确失败」、换新 id 重试——同一条修改执行两次。"""
    _, counter = cli
    figs = _project(tmp_path)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ai_history, "record_start", boom)
    before = set(ai_bridge.SESSIONS)
    with pytest.raises(sqlite3.OperationalError):
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    time.sleep(0.5)
    assert _spawned(counter) == 0
    assert set(ai_bridge.SESSIONS) == before  # 内存里也没留下半个会话


def test_the_record_exists_before_the_process_starts(tmp_path, cli, monkeypatch):
    """顺序本身：Popen 被调用的那一刻，(项目, rid) 已经能查到。"""
    figs = _project(tmp_path)
    seen: list[object] = []
    real_popen = ai_bridge.subprocess.Popen

    def spy(*a, **k):
        seen.append(ai_history.find_by_request(str(figs.resolve()), RID))
        return real_popen(*a, **k)

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", spy)
    ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    _wait_idle()
    assert seen and seen[0] is not None and seen[0]["status"] == "running"


def test_a_failed_spawn_leaves_a_failed_record_not_a_missing_one(tmp_path, cli, monkeypatch):
    figs = _project(tmp_path)

    def no_such_cli(*_a, **_k):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", no_such_cli)
    with pytest.raises(FileNotFoundError):
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    found = ai_bridge.find_request(str(figs.resolve()), RID)
    assert found is not None and found["status"] == "failed"
    assert not ai_bridge.SESSIONS or all(
        s["id"] != found["session"] for s in ai_bridge.SESSIONS.values()
    )


# ---------------------------------------------------------------------------
# 跨进程原子预留（Codex #1098 P1）：两个 Tavotto 服务进程共用同一个数据目录
# ---------------------------------------------------------------------------
def test_reserve_request_is_atomic_across_connections(tmp_path):
    """每个调用各自 `_connect`（= 各自一条 sqlite 连接，等价于两个进程）同时预留同一个 rid：恰好一个成功，
    另一个拿到成功者的会话 id——不是各自成功。"""
    import threading

    db = tmp_path / "hist.sqlite3"
    for i in range(20):
        rid = f"rid-race-{i:04d}"
        barrier = threading.Barrier(2)
        out: dict[str, tuple[bool, str]] = {}

        def reserve(who: str, rid=rid):
            barrier.wait()
            out[who] = ai_history.reserve_request("/p", rid, f"sid-{who}", db_path=db)

        ts = [threading.Thread(target=reserve, args=(w,)) for w in ("a", "b")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        winners = [w for w, (ok, _sid) in out.items() if ok]
        assert len(winners) == 1, out
        assert out["a"][1] == out["b"][1] == f"sid-{winners[0]}"


def test_record_start_does_not_replace_a_reserved_request(tmp_path):
    db = tmp_path / "hist.sqlite3"

    def sess(sid):
        return {"id": sid, "project": "/p", "provider": "codex", "client_request_id": RID}

    assert ai_history.record_start(sess("first"), db_path=db) == (True, "first")
    assert ai_history.record_start(sess("second"), db_path=db) == (False, "first")
    found = ai_history.find_by_request("/p", RID, db_path=db)
    assert found is not None and found["id"] == "first"  # 后来者没有顶替前者


def test_run_on_a_request_reserved_by_another_process_never_spawns(tmp_path, cli, monkeypatch):
    """另一个进程已经预留了 (项目, rid)（会话行还没落到本进程能看到的内存里）：本进程的 run() 不 spawn，
    回去重语义，并清掉为这次尝试准备的快照 / sidecar。"""
    _, counter = cli
    figs = _project(tmp_path)
    project_key = str(figs.resolve())
    assert ai_history.reserve_request(project_key, RID, "otherproc001") == (True, "otherproc001")
    # 进程内的快速路径（run() 开头的 find_request）在竞态里看不到对方：它检查之后、我们预留之前，另一个进程才预留。
    # 这里让快速路径「看不见」，逼 run() 走到原子预留这一步
    monkeypatch.setattr(ai_bridge, "find_request", lambda *_a, **_k: None)
    before = set(ai_bridge.SESSIONS)
    with pytest.raises(ai_bridge.DuplicateRequest) as ei:
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    assert ei.value.sid == "otherproc001"
    time.sleep(0.5)
    assert _spawned(counter) == 0
    assert set(ai_bridge.SESSIONS) == before
    snaps = list((tmp_path / "snapshots").glob("*")) if (tmp_path / "snapshots").exists() else []
    assert snaps == [], snaps  # 没有留下半成品
    engine_watch.stop()


def test_a_failed_spawn_keeps_the_failed_record_and_a_new_id_can_run(tmp_path, cli, monkeypatch):
    """spawn 失败（明确没执行）：记录标成 failed 留着（查询查得到、明确没执行）；同 rid 不会再 spawn（去重到这条 failed），
    前端按「明确失败」换新 id 重发则正常执行——不会被永久挡住。"""
    _, counter = cli
    figs = _project(tmp_path)
    real_popen = ai_bridge.subprocess.Popen

    def no_such_cli(*_a, **_k):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(ai_bridge.subprocess, "Popen", no_such_cli)
    with pytest.raises(FileNotFoundError):
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    monkeypatch.setattr(ai_bridge.subprocess, "Popen", real_popen)
    with pytest.raises(ai_bridge.DuplicateRequest):  # 同 rid：去重到那条 failed 记录，不 spawn
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    assert _spawned(counter) == 0
    ai_bridge.run(
        "codex", "fig1.py", "p", str(figs), client_request_id="rid-fresh-00000001"
    )  # 新 id 正常
    _wait_idle()
    assert _spawned(counter) == 1
    engine_watch.stop()


# ---------------------------------------------------------------------------
# 跨进程的「同一脚本忙」（Codex #1098 P1 r4289060059）：属主进程还活着的 running 会话挡住别的进程
# ---------------------------------------------------------------------------
def _dead_pid() -> int:
    """一个确定已经结束的进程的 pid。"""
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
    )
    return int(proc.stdout)


def _live_other_pid() -> int:
    """另一个确定还活着的进程（pytest 的父进程）；不是本进程。"""
    return os.getppid()


def _sess(sid, rid, script="fig1.py", owner_pid=None, **kw):
    d = {
        "id": sid,
        "project": "/p",
        "provider": "codex",
        "script": script,
        "client_request_id": rid,
        **kw,
    }
    if owner_pid is not None:
        d["owner_pid"] = owner_pid
    return d


def test_two_processes_with_different_rids_cannot_both_claim_one_script(tmp_path):
    """两个独立连接（各自 `_connect`，等价两个服务进程）用不同 rid 同时认领同一脚本：恰好一个成功，另一个 busy。"""
    import threading

    db = tmp_path / "hist.sqlite3"
    live = _live_other_pid()
    for i in range(20):
        script = f"fig{i}.py"
        barrier = threading.Barrier(2)
        out: dict[str, str] = {}

        def claim(who, i=i, script=script):
            barrier.wait()
            try:
                ai_history.record_start(
                    _sess(f"sid-{who}-{i}", f"rid-{who}-{i:08d}", script, owner_pid=live),
                    db_path=db,
                )
                out[who] = "ok"
            except ai_history.ScriptBusy:
                out[who] = "busy"

        ts = [threading.Thread(target=claim, args=(w,)) for w in ("a", "b")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(out.values()) == ["busy", "ok"], out
        loser = next(w for w, v in out.items() if v == "busy")
        # 被拒的那一边连请求预留一起回滚：这个 rid 没被消耗，可以在脚本空闲后重发
        assert ai_history.find_request_record("/p", f"rid-{loser}-{i:08d}", db_path=db) is None


def test_a_running_row_whose_owner_is_dead_does_not_block(tmp_path):
    db = tmp_path / "hist.sqlite3"
    ai_history.record_start(_sess("zombie", "rid-zombie-0001", owner_pid=_dead_pid()), db_path=db)
    assert ai_history.record_start(
        _sess("fresh", "rid-fresh-00001", owner_pid=_live_other_pid()), db_path=db
    ) == (
        True,
        "fresh",
    )
    # 属主未知（老行 NULL）/ 就是本进程的旧实例也不挡
    ai_history.record_end("fresh", "done", db_path=db)
    ai_history.record_start(_sess("oldrow", "rid-oldrow-0001", owner_pid=0), db_path=db)
    assert ai_history.record_start(_sess("next", "rid-next-000001"), db_path=db)[0] is True


def test_a_very_old_running_row_does_not_block_even_if_the_pid_looks_alive(tmp_path):
    """pid 会被复用：一条 running 行太老（超过上限）就当属主已死，不让僵尸行永久锁住脚本。"""
    db = tmp_path / "hist.sqlite3"
    ai_history.record_start(
        _sess("ancient", "rid-ancient-001", owner_pid=_live_other_pid()), db_path=db
    )
    import sqlite3 as _sq

    with _sq.connect(db) as raw:
        raw.execute(
            "UPDATE sessions SET started_ms = started_ms - ?",
            (ai_history.RUNNING_OWNER_MAX_AGE_MS + 1000,),
        )
    assert ai_history.record_start(
        _sess("fresh2", "rid-fresh-00002", owner_pid=_live_other_pid()), db_path=db
    )[0]


def test_startup_cleanup_only_marks_rows_whose_owner_is_dead(tmp_path):
    """启动清理不碰另一个仍活着的服务进程里的会话。"""
    db = tmp_path / "hist.sqlite3"
    ai_history.record_start(
        _sess("alive-other", "rid-aaaaaaaa01", "a.py", owner_pid=_live_other_pid()), db_path=db
    )
    ai_history.record_start(
        _sess("dead-other", "rid-bbbbbbbb01", "b.py", owner_pid=_dead_pid()), db_path=db
    )
    ai_history.record_start(
        _sess("own-old", "rid-cccccccc01", "c.py", owner_pid=os.getpid()), db_path=db
    )
    ai_history.record_start(_sess("unknown", "rid-dddddddd01", "d.py", owner_pid=0), db_path=db)
    import sqlite3 as _sq

    with _sq.connect(db) as raw:
        raw.execute("UPDATE sessions SET owner_pid=NULL WHERE id='unknown'")
    assert ai_history.mark_interrupted_running(db_path=db) == 3
    status = {r["id"]: r["status"] for r in _rows(db)}
    assert status == {
        "alive-other": "running",  # 另一个活着的进程里的会话没被标掉
        "dead-other": "interrupted",
        "own-old": "interrupted",
        "unknown": "interrupted",
    }


def _rows(db):
    import sqlite3 as _sq

    with _sq.connect(db) as raw:
        raw.row_factory = _sq.Row
        return list(raw.execute("SELECT id, status FROM sessions"))


def test_run_is_busy_when_another_process_owns_the_script(tmp_path, cli):
    """进程内看不见对方：另一个服务进程里同一脚本有活着的 running 会话 → 本进程 run() 回 ai_script_busy，
    不 spawn、不留半成品，请求 id 也没被消耗。"""
    _, counter = cli
    figs = _project(tmp_path)
    project_key = str(figs.resolve())
    ai_history.record_start(
        {
            "id": "othersess001",
            "project": project_key,
            "provider": "codex",
            "script": "fig1.py",
            "client_request_id": "rid-other-00001",
            "owner_pid": _live_other_pid(),
        }
    )
    before = set(ai_bridge.SESSIONS)
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    assert ei.value.code == "ai_script_busy"
    time.sleep(0.4)
    assert _spawned(counter) == 0
    assert set(ai_bridge.SESSIONS) == before
    assert ai_history.find_request_record(project_key, RID) is None  # rid 没被消耗
    snaps = list((tmp_path / "snapshots").glob("*")) if (tmp_path / "snapshots").exists() else []
    assert snaps == [], snaps
    # 对方结束之后，同一个 rid 可以正常执行
    ai_history.record_end("othersess001", "done")
    ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    _wait_idle()
    assert _spawned(counter) == 1
    engine_watch.stop()


def test_pid_alive_tells_a_finished_process_from_a_live_one():
    from tavotto.engine.procalive import pid_alive

    assert pid_alive(os.getpid()) is True
    assert pid_alive(_dead_pid()) is False
    assert pid_alive(0) is False


def test_resend_is_deduped_before_the_panel_and_registry_are_revalidated(
    client, tmp_path, cli, monkeypatch
):
    """原请求登记之后，输出文件 / 注册表映射变了（可变目标）：同 id 的重发要先去重、返回既有会话，而不是 404——
    前端把 404 当「明确拒绝」，会删掉账本、换新 id 重发，同一条修改执行两次。"""
    state, counter = cli
    state["delay"] = 0.3
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    first = _post(client)
    assert first.status_code == 200
    _wait_idle()

    class _Gone:  # 注册表里这个面板的映射没了
        def for_stem(self, _stem):
            return None

    monkeypatch.setattr(m, "current_registry", lambda: _Gone())
    again = _post(client)  # 同一个 id
    assert again.status_code == 200, (
        again.get_json()
    )  # 对照：先校验后去重时这里是 404 not_parameterizable
    assert (
        again.get_json()["session"] == first.get_json()["session"]
        and again.get_json()["deduped"] is True
    )
    fresh = _post(client, rid="rid-fresh-00000002")  # 新 id 仍然要过校验
    assert fresh.status_code == 404 and fresh.get_json()["code"] == "not_parameterizable"
    assert _spawned(counter) == 1
