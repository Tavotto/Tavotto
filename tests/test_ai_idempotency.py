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
import sqlite3
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
