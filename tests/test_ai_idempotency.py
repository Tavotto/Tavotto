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

from support.registrystub import stem_registry
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
    """假 CLI：每次被 spawn 往 counter 追加一行，然后睡 `delay` 秒。

    `state["hold"] = True` 时不按时间睡，改成一直等到 `release` 文件出现（测试 `state["release"]()` 放行，fixture 收尾也会放行）：
    需要「这个会话**一定**还在跑」的用例用它——负载很高的 Windows runner 上，固定秒数的 sleep 会在两次请求之间走完。"""
    counter = tmp_path / "spawned.txt"
    release_file = tmp_path / "release.flag"
    state = {"delay": 0.0, "hold": False}
    state["release"] = lambda: release_file.write_text("go", encoding="utf-8")

    def fake_cmd(*_a, **_k):
        if state["hold"]:
            wait = "end = time.time() + 120\nwhile time.time() < end and not os.path.exists({r!r}):\n    time.sleep(0.02)"
        else:
            wait = "time.sleep({d})"
        code = ("import os, time; open({c!r}, 'a').write('x\\n')\n" + wait).format(
            c=str(counter), d=state["delay"], r=str(release_file)
        )
        return [sys.executable, "-c", code], {}

    monkeypatch.setattr(ai_bridge, "SNAP_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(ai_bridge, "require_usable", lambda agent: None)
    monkeypatch.setattr(ai_bridge.ai_providers, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(ai_bridge, "_cmd", fake_cmd)
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    before = set(ai_bridge.SESSIONS)
    yield state, counter
    state["release"]()
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


def _wait_for(pred, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("条件没在时限内成立")


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
    sid = _post(client).get_json()["session"]
    _wait_idle()
    # `status` 先翻成终态、`record_end`（SQLite 提交）之后才发 `ai.done`：只等 status 会在慢盘上抢在事件前面。
    # 只看**本用例这个会话**的事件：前一条用例在后台收尾的会话也可能在补丁装上之后才发它自己的 `ai.done`
    _wait_for(lambda: any(ev == "ai.done" and d.get("session") == sid for ev, d in events))
    done = [d for ev, d in events if ev == "ai.done" and d.get("session") == sid]
    assert len(done) == 1
    assert done[0]["client_request_id"] == RID and done[0]["pj"] == status["id"]


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


def test_access_log_lines_written_by_the_real_server_carry_no_request_id(tmp_path, monkeypatch):
    """test client 不经过 werkzeug 的访问日志（`WSGIRequestHandler.log_request` 把原始请求行交给 `werkzeug` logger）：
    起一个真的 werkzeug server、按产品的 `setup_logging` 接线，读 stderr 同源的 app.log 本身（Codex #1098 P2）。"""
    import urllib.error
    import urllib.request
    from logging.handlers import RotatingFileHandler

    from werkzeug.serving import make_server

    monkeypatch.setattr(m, "CACHE_DIR", tmp_path)
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    root.handlers = [h for h in saved if not isinstance(h, RotatingFileHandler)]
    try:
        m.setup_logging()
        srv = make_server("127.0.0.1", 0, m.app, threaded=True)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            for method, path in (
                ("PUT", f"/api/ai/drafts/pending/{RID}"),
                ("POST", f"/api/ai/drafts/pending/{RID}/accept"),
                ("GET", f"/api/ai/requests/{RID}"),
            ):
                req = urllib.request.Request(base + path, data=b"{}", method=method)
                if method == "GET":
                    req = urllib.request.Request(base + path, method=method)
                try:
                    urllib.request.urlopen(req, timeout=10).close()
                except urllib.error.HTTPError:
                    pass  # 鉴权 / 校验拒绝也照样写访问日志：这里只看日志
        finally:
            srv.shutdown()
            t.join(10)
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "/api/ai/drafts/pending/<rid>" in text and "/api/ai/requests/<rid>" in text
        assert RID not in text
    finally:
        for h in root.handlers:
            if h not in saved:
                h.close()
        root.handlers = saved
        root.setLevel(level)


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
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        encoding="utf-8",
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

    # 注册表里这个面板的映射没了
    monkeypatch.setattr(m, "current_registry", lambda: stem_registry(None))
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


# ---------------------------------------------------------------------------
# 会话详情只给显式白名单的、可序列化的字段（本进程正在运行的会话曾因 `exited` 的 Event 一律 500）
# ---------------------------------------------------------------------------
def test_detail_of_a_session_running_in_this_process_is_200_and_valid_json(
    client, tmp_path, cli, monkeypatch
):
    state, _ = cli
    state["delay"] = 1.5
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    sid = _post(client).get_json()["session"]
    r = client.get(f"/api/ai/sessions/{sid}")  # 任务还在跑
    assert r.status_code == 200, r.data
    body = json.loads(r.data)  # 合法 JSON
    assert body["id"] == sid and body["status"] == "running"
    assert (
        "proc" not in body
        and "exited" not in body
        and "script_path" not in body
        and "snapshot" not in body
    )
    _wait_idle()
    assert client.get(f"/api/ai/sessions/{sid}").status_code == 200


def test_every_key_of_a_live_session_is_classified_and_the_dto_is_serializable(tmp_path, cli):
    """结构性保护：会话 dict 的每个键要么在对外白名单、要么在「有意不对外」清单里。以后往会话里加字段必须表态——
    不会悄悄漏进响应，也不会悄悄把详情变成 500；白名单里的每个值都必须能 `json.dumps`。"""
    state, _ = cli
    state["delay"] = 1.0
    figs = _project(tmp_path)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    live = dict(ai_bridge.SESSIONS[sid])
    unclassified = set(live) - set(ai_bridge.SESSION_DTO_KEYS) - ai_bridge.SESSION_INTERNAL_KEYS
    assert unclassified == set(), f"会话里有没表态的新字段: {sorted(unclassified)}"
    for key in ai_bridge.SESSION_DTO_KEYS:
        if key in live:
            json.dumps(live[key])  # 白名单里的值必须可序列化
    json.dumps(ai_bridge.get(sid))
    _wait_idle()
    engine_watch.stop()


def test_a_failing_history_write_leaves_no_snapshot_files_behind(tmp_path, cli, monkeypatch):
    """record_start 抛了（SQLite 锁住 / 磁盘满）：快照 / sidecar / 日志一并清掉，不留半成品去挤占 `_prune_snapshots` 的保留名额。"""
    _, counter = cli
    figs = _project(tmp_path)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is full")

    monkeypatch.setattr(ai_history, "record_start", boom)
    with pytest.raises(sqlite3.OperationalError):
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id=RID)
    time.sleep(0.3)
    assert _spawned(counter) == 0
    snaps = list((tmp_path / "snapshots").glob("*")) if (tmp_path / "snapshots").exists() else []
    assert snaps == [], snaps


def _pending_rids(client):
    return [p["rid"] for p in client.get("/api/ai/drafts/pending").get_json()["pendings"]]


def test_a_rejected_run_deletes_its_pending_row_for_every_kind_of_rejection(
    client, tmp_path, cli, monkeypatch
):
    """两类拒绝路径：被 `abort()` 打断的（HTTPException），和正常返回 4xx/5xx 响应的（AgentError → `_agent_error`、404 not_parameterizable）。
    两类都由后端把这个 rid 的待确认行删掉——客户端没有 DELETE。"""
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    sent = {"scope": "figure", "gid": None, "label": None, "target": "整张图"}

    def put_pending(rid):
        r = client.put(
            f"/api/ai/drafts/pending/{rid}", json={"file": "Fig1.pdf", "text": "t", "sent": sent}
        )
        assert r.status_code == 200, r.data

    # 1) 正常返回的错误响应：同一脚本上已有任务在跑 → 409 ai_script_busy（不同 rid）
    state, _ = cli
    state["hold"] = True  # 第一个会话一直占着脚本，直到下面放行——不靠固定秒数
    assert _post(client, rid="rid-first-00000001").status_code == 200
    put_pending(RID)
    r = _post(client, rid=RID)
    assert r.status_code == 409 and r.get_json()["code"] == "ai_script_busy"
    assert RID not in _pending_rids(client)
    # 2) abort()：空提示词 → 400
    rid2 = "rid-abort-00000001"
    put_pending(rid2)
    r = client.post(
        "/api/ai/run",
        json={"agent": "codex", "id": "Fig1.pdf", "prompt": "  ", "client_request_id": rid2},
    )
    assert r.status_code == 400
    assert rid2 not in _pending_rids(client)
    # 3) 成功的请求不删（只有 accept 删）
    state["release"]()
    _wait_idle()
    state["hold"] = False
    state["delay"] = 0.1
    rid3 = "rid-okreq-00000001"
    put_pending(rid3)
    assert _post(client, rid=rid3).status_code == 200
    assert rid3 in _pending_rids(client)
    _wait_idle()


# ---------------------------------------------------------------------------
# supersedes 的墓碑预留；spawn 之前 / 之后的异常（Codex #1098 P1 r4289529467 / r4289529472）
# ---------------------------------------------------------------------------
SENT = {"scope": "figure", "gid": None, "label": None, "target": "整张图"}


def _put_pending(client, rid, text="t", **extra):
    return client.put(
        f"/api/ai/drafts/pending/{rid}",
        json={"file": "Fig1.pdf", "text": text, "sent": SENT, **extra},
    )


def test_a_superseded_rid_can_never_run_even_if_its_post_arrives_late(
    client, tmp_path, cli, monkeypatch
):
    """旧 rid 的 POST 只是慢，在 supersedes 之后才到：它不能再登记并执行（否则旧指令和改写后的指令都执行了）。"""
    _, counter = cli
    figs = _project(tmp_path)
    _open(client, figs)
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    old, new = "rid-old-000000001", "rid-new-000000001"
    assert _put_pending(client, old).status_code == 200
    assert _put_pending(client, new, supersedes=[old]).status_code == 200
    # 晚到的旧请求：被明确拒绝，不 spawn
    late = _post(client, rid=old)
    assert late.status_code == 409 and late.get_json()["code"] == "ai_request_superseded"
    # 查询返回 superseded，不是 404（前端据此不再提供同 rid 重发）
    q = client.get(f"/api/ai/requests/{old}")
    assert q.status_code == 200 and q.get_json()["status"] == "superseded"
    time.sleep(0.3)
    assert _spawned(counter) == 0
    # 新 rid 照常执行
    assert _post(client, rid=new).status_code == 200
    _wait_idle()
    assert _spawned(counter) == 1


def test_superseded_is_enforced_by_the_atomic_reservation_not_just_the_fast_path(
    tmp_path, cli, monkeypatch
):
    """并发里快速路径可能看不见墓碑：逼 run() 走到原子预留，同样被拒绝，不 spawn、不留半成品。"""
    _, counter = cli
    figs = _project(tmp_path)
    project_key = str(figs.resolve())
    ai_history.put_pending(
        project_key, "Fig1.pdf", "rid-new-000000002", "t", SENT, supersedes=["rid-old-000000002"]
    )
    monkeypatch.setattr(ai_bridge, "find_request", lambda *_a, **_k: None)
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-old-000000002")
    assert ei.value.code == "ai_request_superseded"
    time.sleep(0.3)
    assert _spawned(counter) == 0
    snaps = list((tmp_path / "snapshots").glob("*")) if (tmp_path / "snapshots").exists() else []
    assert snaps == [], snaps


def test_run_fast_path_also_refuses_a_superseded_rid(tmp_path, cli):
    """`run()` 开头的进程内快速路径（直接调 run、不经路由）同样把墓碑当明确拒绝，而不是「重复」去重到一个不存在的会话。"""
    _, counter = cli
    figs = _project(tmp_path)
    project_key = str(figs.resolve())
    ai_history.put_pending(
        project_key, "Fig1.pdf", "rid-new-000000004", "t", SENT, supersedes=["rid-old-000000004"]
    )
    with pytest.raises(ai_bridge.AgentError) as ei:
        ai_bridge.run("codex", "fig1.py", "p", str(figs), client_request_id="rid-old-000000004")
    assert ei.value.code == "ai_request_superseded"
    assert _spawned(counter) == 0


def test_supersede_is_refused_when_the_old_request_was_already_registered(client, tmp_path, cli):
    """旧请求其实已经到达并登记了：不能再当成「没收到」取代它——整个 PUT 回滚，新行不写，旧行保留。"""
    figs = _project(tmp_path)
    _open(client, figs)
    project_key = str(figs.resolve())
    old = "rid-old-000000003"
    ai_history.record_start(
        {
            "id": "realsess0001",
            "project": project_key,
            "provider": "codex",
            "script": "x.py",
            "client_request_id": old,
            "owner_pid": 0,
        }
    )
    assert _put_pending(client, old).status_code == 200
    r = _put_pending(client, "rid-new-000000003", supersedes=[old])
    assert r.status_code == 409 and r.get_json()["code"] == "ai_supersede_conflict"
    rids = [p["rid"] for p in client.get("/api/ai/drafts/pending").get_json()["pendings"]]
    assert rids == [old]


def test_an_exception_before_spawn_deletes_the_pending_row_on_the_backend(
    client, tmp_path, cli, monkeypatch
):
    """spawn 之前的非 HTTP 异常（record_start 抛 OperationalError）：被全局处理器转成 500，前端当成明确拒绝、换新 rid——
    旧 rid 的账本行必须由后端删掉。"""
    _, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    monkeypatch.setitem(m.app.config, "PROPAGATE_EXCEPTIONS", False)
    _put_pending(client, RID)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ai_history, "record_start", boom)
    r = _post(client, rid=RID)
    assert r.status_code == 500
    assert RID not in _pending_rids(client)
    time.sleep(0.3)
    assert _spawned(counter) == 0


def test_an_error_after_spawn_never_deletes_the_pending_row(client, tmp_path, cli, monkeypatch):
    """spawn 之后才出的异常：那条指令可能正在执行，账本行是「响应丢了之后还能查到它」的线索，绝不删。"""
    state, counter = cli
    state["delay"] = 0.3
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    _put_pending(client, RID)

    real_thread = ai_bridge.threading.Thread

    class FaultyThread(real_thread):
        def start(self):
            if str(self.name).startswith("ai-"):
                raise RuntimeError("can't start new thread")  # Popen 之后、返回之前
            super().start()

    monkeypatch.setattr(ai_bridge.threading, "Thread", FaultyThread)
    r = _post(client, rid=RID)
    # 不是带码的 500：前端把它当明确拒绝、换新 rid 重发，原进程还在改脚本 → 同一条指令执行两次
    assert r.status_code == 503 and r.get_json()["code"] == "ai_request_unresolved"
    time.sleep(0.6)
    assert _spawned(counter) == 1  # 进程确实起来了
    assert RID in _pending_rids(client)  # 所以行还在


def test_an_http_exception_or_error_response_after_spawn_is_unresolved(
    client, tmp_path, cli, monkeypatch
):
    """spawn 之后的 abort()（HTTPException）与 ≥400 响应同样是「不确定」：503 ai_request_unresolved，行还在，不再 spawn。"""
    from werkzeug.exceptions import BadRequest

    state, counter = cli
    state["delay"] = 0.3
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    real_thread = ai_bridge.threading.Thread

    class FaultyThread(real_thread):
        def start(self):
            if str(self.name).startswith("ai-"):
                raise BadRequest("late abort")
            super().start()

    monkeypatch.setattr(ai_bridge.threading, "Thread", FaultyThread)
    _put_pending(client, RID)
    r = _post(client, rid=RID)
    assert r.status_code == 503 and r.get_json()["code"] == "ai_request_unresolved"
    assert RID in _pending_rids(client)
    _wait_for(lambda: _spawned(counter) == 1)  # 进程确实起来了（起 Python 要点时间）


def test_the_post_spawn_log_never_contains_the_request_id(
    client, tmp_path, cli, monkeypatch, caplog
):
    """请求 id 不进日志（docs/rules/backend/ai-agent-bridge.md）：异常消息里带着 rid 也一样——只留类型名与栈帧。"""
    import logging

    state, _ = cli
    state["delay"] = 0.3
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    real_thread = ai_bridge.threading.Thread
    leaked = f"boom {RID}"

    class FaultyThread(real_thread):
        def start(self):
            if str(self.name).startswith("ai-"):
                raise ValueError(leaked)
            super().start()

    monkeypatch.setattr(ai_bridge.threading, "Thread", FaultyThread)
    _put_pending(client, RID)
    with caplog.at_level(logging.DEBUG):
        r = _post(client, rid=RID)
    assert r.status_code == 503
    logged = [rec for rec in caplog.records if "AI 任务已启动但随后出错" in rec.getMessage()]
    assert logged, "出错必须留一条日志"
    for rec in caplog.records:
        text = rec.getMessage() + (rec.exc_text or "") + str(rec.args) + str(rec.exc_info)
        assert RID not in text and leaked not in text
    assert "ValueError" in logged[0].getMessage()  # 类型名还在，能定位


def test_an_error_response_after_spawn_is_unresolved(client, tmp_path, cli, monkeypatch):
    state, counter = cli
    state["delay"] = 0.3
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    real_impl = m._ai_run_impl
    # 真 spawn 之后，实现层返回一个带码 500
    monkeypatch.setattr(
        m, "_ai_run_impl", lambda: (real_impl(), (m.jsonify({"code": "x"}), 500))[1]
    )
    _put_pending(client, RID)
    r = _post(client, rid=RID)
    assert r.status_code == 503 and r.get_json()["code"] == "ai_request_unresolved"
    assert RID in _pending_rids(client)
    _wait_for(lambda: _spawned(counter) == 1)
    _wait_idle()


def test_failed_cleanup_after_a_pre_spawn_error_is_unresolved_not_a_definite_rejection(
    client, tmp_path, cli, monkeypatch
):
    """spawn 之前出错、删待确认行的清理也失败了（SQLite 锁住 / 磁盘满）：不能返回明确的 500（前端会换新 rid，旧行之后被还原、
    同一条指令执行两次）——回 503 `ai_request_unresolved`（不确定），行还在。"""
    _, counter = cli
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    monkeypatch.setitem(m.app.config, "PROPAGATE_EXCEPTIONS", False)
    _put_pending(client, RID)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    real_record, real_drop = ai_history.record_start, ai_history.drop_rejected_pending
    monkeypatch.setattr(ai_history, "record_start", boom)
    monkeypatch.setattr(ai_history, "drop_rejected_pending", boom)  # 清理也失败
    r = _post(client, rid=RID)
    assert r.status_code == 503 and r.get_json()["code"] == "ai_request_unresolved"
    monkeypatch.setattr(ai_history, "record_start", real_record)
    monkeypatch.setattr(ai_history, "drop_rejected_pending", real_drop)
    assert RID in _pending_rids(client)  # 行还在（所以客户端必须保持同一个 rid）
    time.sleep(0.3)
    assert _spawned(counter) == 0


def test_a_rejection_whose_cleanup_fails_is_unresolved_too(client, tmp_path, cli, monkeypatch):
    """正常返回的拒绝（409 busy）也一样：清理失败就不能说成明确拒绝。"""
    state, _ = cli
    state["delay"] = 1.0
    _open(client, _project(tmp_path))
    monkeypatch.setattr(m, "sse_publish", lambda *a: None)
    assert _post(client, rid="rid-first-00000009").status_code == 200
    _put_pending(client, RID)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("disk full")

    real_drop = ai_history.drop_rejected_pending
    monkeypatch.setattr(ai_history, "drop_rejected_pending", boom)
    r = _post(client, rid=RID)
    assert r.status_code == 503 and r.get_json()["code"] == "ai_request_unresolved"
    monkeypatch.setattr(ai_history, "drop_rejected_pending", real_drop)
    assert RID in _pending_rids(client)
    _wait_idle()


@pytest.mark.parametrize("status", ["cancelled", "interrupted"])
def test_a_cancelled_but_unfinished_session_in_another_process_still_blocks_the_script(
    tmp_path, status
):
    """cancel / `interrupt_all()`（关机收尾）之后状态立刻变了，但 CLI 进程组可能还在写文件：另一个进程里属主存活、`ended_ms`
    为空的 cancelled / interrupted 会话仍算「忙」；写了 `ended_ms`（真正结束）之后才放开。"""
    db = tmp_path / "hist.sqlite3"
    live = _live_other_pid()
    ai_history.record_start(_sess("othercanc01", "rid-canc-000009", owner_pid=live), db_path=db)
    ai_history.update_status("othercanc01", status, db_path=db)  # 只改 status，不写 ended_ms
    with pytest.raises(ai_history.ScriptBusy):
        ai_history.record_start(
            _sess("mine00000001", "rid-mine-000009", owner_pid=live), db_path=db
        )
    ai_history.record_end("othercanc01", status, db_path=db)  # 真正结束：写了 ended_ms
    assert (
        ai_history.record_start(
            _sess("mine00000002", "rid-mine-000010", owner_pid=live), db_path=db
        )[0]
        is True
    )
    # 对照：cancelled 但属主已死 → 不挡
    ai_history.record_end("mine00000002", "done", db_path=db)
    ai_history.record_start(
        _sess("deadcanc001", "rid-dead-000009", owner_pid=_dead_pid()), db_path=db
    )
    ai_history.update_status("deadcanc001", status, db_path=db)
    assert (
        ai_history.record_start(
            _sess("mine00000003", "rid-mine-000011", owner_pid=live), db_path=db
        )[0]
        is True
    )


def test_live_session_detail_has_ended_ms_only_after_the_pump_finishes(tmp_path, cli, monkeypatch):
    """cancel 之后 status 立刻是 cancelled，但 changed / diff / refresh 要等 pump 收尾——收尾之前的详情没有 ended_ms，
    前端据此不把 cancelled 快照当终态（SSE 断线期间丢了 ai.done 时靠对账拿到真正的结果）。"""
    state, _ = cli
    state["delay"] = 2.0
    figs = _project(tmp_path)
    gate = threading.Event()
    real_refresh = ai_bridge.refresh_outcome

    def slow_refresh(*a, **k):
        gate.wait(10)
        return real_refresh(*a, **k)

    monkeypatch.setattr(ai_bridge, "refresh_outcome", slow_refresh)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    assert ai_bridge.cancel(sid) is True
    time.sleep(0.4)
    detail = ai_bridge.get(sid)
    assert detail["status"] == "cancelled" and not detail.get(
        "ended_ms"
    )  # pump 卡在 refresh 上：还没结束
    gate.set()
    deadline = time.time() + 15
    while time.time() < deadline and not ai_bridge.SESSIONS[sid].get("ended_ms"):
        time.sleep(0.05)
    assert isinstance(ai_bridge.get(sid)["ended_ms"], int)
    _wait_idle()
    engine_watch.stop()


def test_detail_after_restart_with_sidecar_carries_the_error(tmp_path, cli, monkeypatch):
    """服务重启、sidecar 还在时走「sidecar 合并历史」那条路：终态字段整体以历史库为准——失败会话的 error 不能丢
    （以前逐个字段抄，漏了 error：final 为 true 但失败原因是空的）。"""
    figs = _project(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("刷新炸了：scan 失败")

    monkeypatch.setattr(ai_bridge, "refresh_outcome", boom)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    deadline = time.time() + 15
    while time.time() < deadline and ai_history.get(sid)["status"] == "running":
        time.sleep(0.05)
    _wait_idle()
    ai_bridge.SESSIONS.pop(sid)  # 模拟重启：只剩 sidecar + 历史库
    assert (ai_bridge.SNAP_DIR / f"{sid}.json").is_file()  # 前提：确实走 sidecar+历史 这一支
    detail = ai_bridge.get(sid)
    assert detail["status"] == "failed"
    assert detail["error"] == "刷新炸了：scan 失败"  # 对照：以前这里没有 error
    assert isinstance(detail["ended_ms"], int)
    engine_watch.stop()


def test_every_terminal_field_exists_on_a_history_row(tmp_path):
    """结构性保护：终态字段清单里的每个键，历史行里都有——清单与历史库不会悄悄分叉（清单里多一个历史库没有的键，合并路径会丢掉它）。"""
    db = tmp_path / "hist.sqlite3"
    ai_history.record_start(
        {"id": "termfields01", "project": "/p", "provider": "codex"}, db_path=db
    )
    ai_history.record_end("termfields01", "failed", error="x", db_path=db)
    row = ai_history.get("termfields01", db_path=db)
    missing = [k for k in ai_bridge.SESSION_TERMINAL_FIELDS if k not in row]
    assert missing == [], missing


def test_startup_cleanup_finalizes_cancelled_rows_of_dead_owners_and_keeps_live_ones(tmp_path):
    """已取消但 `_pump` 还没收尾（cancelled 且 ended_ms 为空）的行：属主已死 → 补上 ended_ms、状态保持 cancelled（不改成 failed /
    interrupted）；属主还活着 → 不动。谓词与 `OPEN_SESSION_SQL` 一致，这些行不再永远开着、永远占着脚本的忙碌判定。"""
    db = tmp_path / "hist.sqlite3"
    ai_history.record_start(
        _sess("canc-dead", "rid-cdead-0001", "a.py", owner_pid=_dead_pid()), db_path=db
    )
    ai_history.update_status("canc-dead", "cancelled", db_path=db)
    ai_history.record_start(
        _sess("canc-live", "rid-clive-0001", "b.py", owner_pid=_live_other_pid()), db_path=db
    )
    ai_history.update_status("canc-live", "cancelled", db_path=db)
    ai_history.record_start(
        _sess("intr-dead", "rid-idead-0001", "d.py", owner_pid=_dead_pid()), db_path=db
    )
    ai_history.update_status("intr-dead", "interrupted", db_path=db)  # interrupt_all 只写 status
    ai_history.record_start(
        _sess("canc-done", "rid-cdone-0001", "c.py", owner_pid=_dead_pid()), db_path=db
    )
    ai_history.record_end(
        "canc-done", "cancelled", db_path=db
    )  # 已经收尾的 cancelled：本来就有 ended_ms
    before_done = {r["id"]: r["ended_ms"] for r in _ended(db)}["canc-done"]
    assert ai_history.mark_interrupted_running(db_path=db) == 0  # 没有 running 被转成 interrupted
    rows = {r["id"]: (r["status"], r["ended_ms"]) for r in _ended(db)}
    assert rows["canc-dead"][0] == "cancelled" and isinstance(
        rows["canc-dead"][1], int
    )  # 补上了 ended_ms，状态保持
    assert rows["canc-live"] == ("cancelled", None)  # 属主活着：不动
    assert rows["intr-dead"][0] == "interrupted" and isinstance(rows["intr-dead"][1], int)
    assert rows["canc-done"] == ("cancelled", before_done)  # 已收尾的不被改写
    # 死属主的 cancelled 不再占着忙碌判定：同一脚本可以再认领
    assert ai_history.record_start(
        _sess("next-a", "rid-next-0000a", "a.py", owner_pid=_live_other_pid()), db_path=db
    )[0]


def _ended(db):
    import sqlite3 as _sq

    with _sq.connect(db) as raw:
        raw.row_factory = _sq.Row
        return list(raw.execute("SELECT id, status, ended_ms FROM sessions"))


# 会话详情的 `final`：cancel 之后 status 立刻是 cancelled，但 changed / diff / refresh 要等 _pump 收尾才算出来
# ---------------------------------------------------------------------------
def test_session_detail_is_not_final_until_the_pump_finishes(tmp_path, cli, monkeypatch):
    state, _ = cli
    state["delay"] = 2.0
    figs = _project(tmp_path)
    gate = threading.Event()
    real_refresh = ai_bridge.refresh_outcome

    def slow_refresh(*a, **k):
        gate.wait(10)  # 把 pump 钉在「changed / diff 已算、refresh 还没出」的位置
        return real_refresh(*a, **k)

    monkeypatch.setattr(ai_bridge, "refresh_outcome", slow_refresh)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    assert ai_bridge.get(sid)["final"] is False  # 刚起：不是终态
    assert ai_bridge.cancel(sid) is True
    detail = ai_bridge.get(sid)
    assert (
        detail["status"] == "cancelled" and detail["final"] is False
    )  # cancel 之后、pump 结束之前
    time.sleep(0.5)  # 进程已被终止，pump 卡在 refresh 上：仍不是终态
    assert ai_bridge.get(sid)["final"] is False
    gate.set()
    deadline = time.time() + 15
    while time.time() < deadline and not ai_bridge.SESSIONS[sid].get("final"):
        time.sleep(0.05)
    done = ai_bridge.get(sid)
    assert done["final"] is True and done["status"] in ("cancelled", "done", "failed")
    _wait_idle()
    engine_watch.stop()


def test_detail_from_history_is_final_only_when_the_row_was_ended(tmp_path, cli):
    figs = _project(tmp_path)
    project_key = str(figs.resolve())

    def start(sid, rid, owner, script="fig1.py"):
        ai_history.record_start(
            {
                "id": sid,
                "project": project_key,
                "provider": "codex",
                "script": script,
                "client_request_id": rid,
                "owner_pid": owner,
            }
        )

    start("histdone0001", "rid-hist-000001", 0)
    ai_history.record_end("histdone0001", "done", diff="D", changed=True)
    assert ai_bridge.get("histdone0001")["final"] is True  # 历史库里的终态行
    start("histcancel01", "rid-hist-000002", _live_other_pid(), "fig2.py")
    ai_history.update_status(
        "histcancel01", "cancelled"
    )  # cancel 只改 status、不写 ended_ms：属主（另一个活着的服务进程）的 pump 还没收尾
    assert ai_bridge.get("histcancel01")["final"] is False
    ai_history.record_end("histcancel01", "cancelled")  # 那边的 pump 收尾：写了 ended_ms
    assert ai_bridge.get("histcancel01")["final"] is True
    # 属主已不在（owner_pid=0 → 未知 / 进程没了）：不会再有 pump 补结果——不等启动清理就是终态，前端不会永远轮询
    start("histcancel02", "rid-hist-000003", 0, "fig3.py")
    ai_history.update_status("histcancel02", "cancelled")
    assert ai_bridge.get("histcancel02")["final"] is True
    assert ai_history.mark_interrupted_running() >= 0  # 启动清理补上 ended_ms 之后照旧
    assert ai_bridge.get("histcancel02")["final"] is True


def test_session_detail_endpoint_carries_final(client, tmp_path, cli):
    figs = _project(tmp_path)
    _open(client, figs)
    ai_history.record_start(
        {
            "id": "endpoint0001",
            "project": str(figs.resolve()),
            "provider": "codex",
            "script": "fig1.py",
            "owner_pid": 0,
        }
    )
    ai_history.record_end("endpoint0001", "done")
    body = client.get("/api/ai/sessions/endpoint0001").get_json()
    assert body["final"] is True


def test_interrupted_rows_are_final_even_without_ended_ms(tmp_path, cli):
    """/api/shutdown 的 `interrupt_all` 写 interrupted 却不写 ended_ms，进程可能在 `_pump` 的 record_end 之前就退出：
    属主已不在 → 不会再有结果，算 final；属主（另一个共用数据目录的服务进程）还活着 → 它的 pump 之后还会补 changed / diff / refresh，
    不是 final（与 `OPEN_SESSION_SQL` 同一个概念，cancelled 同理）。确定终态的 status 直接算 final。"""
    figs = _project(tmp_path)
    project_key = str(figs.resolve())

    def start(sid, rid, owner):
        ai_history.record_start(
            {
                "id": sid,
                "project": project_key,
                "provider": "codex",
                "script": f"{sid}.py",
                "client_request_id": rid,
                "owner_pid": owner,
            }
        )

    dead, live = _dead_pid(), _live_other_pid()
    for status in ("interrupted", "cancelled"):
        start(f"{status[:4]}-dead1", f"rid-{status[:4]}-dead01", dead)
        ai_history.update_status(f"{status[:4]}-dead1", status)  # 没有 ended_ms
        assert ai_bridge.get(f"{status[:4]}-dead1")["final"] is True, status
        start(f"{status[:4]}-live1", f"rid-{status[:4]}-live01", live)
        ai_history.update_status(f"{status[:4]}-live1", status)
        assert ai_bridge.get(f"{status[:4]}-live1")["final"] is False, (
            status
        )  # 属主的 pump 还会补结果
    for status in ("done", "failed", "timeout"):
        start(f"{status}-1", f"rid-{status}-000001", dead)
        ai_history.update_status(f"{status}-1", status)
        assert ai_bridge.get(f"{status}-1")["final"] is True, status
    assert ai_history.mark_interrupted_running() >= 0
    # 启动清理给属主已死、ended_ms 为空的 interrupted / cancelled 行补上 ended_ms
    ended = {
        r["id"]: r["ended_ms"] for r in ai_history.list_sessions(project_key, limit=100)["sessions"]
    }
    assert ended["inte-dead1"] is not None and ended["canc-dead1"] is not None
    assert ended["inte-live1"] is None and ended["canc-live1"] is None  # 属主活着：不动
    assert ai_bridge.get("canc-dead1")["final"] is True  # 补上之后照旧收尾


def test_pump_exception_puts_the_error_on_the_in_memory_session_too(tmp_path, cli, monkeypatch):
    """_pump 抛异常的分支：error 必须同时写进内存会话和历史库。`get()` 优先返回内存会话——只写历史库的话，
    对账拿到的是 final:true 却没有 error 的快照，恢复后的失败提示是空白的。"""
    figs = _project(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("刷新炸了：scan 失败")

    monkeypatch.setattr(ai_bridge, "refresh_outcome", boom)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    deadline = time.time() + 15
    while time.time() < deadline and not ai_bridge.SESSIONS[sid].get("final"):
        time.sleep(0.05)
    detail = ai_bridge.get(sid)  # 内存会话仍在
    assert detail["final"] is True and detail["status"] == "failed"
    assert detail["error"] == "刷新炸了：scan 失败"  # 对照：以前这里没有 error
    hist = ai_history.get(sid)
    assert hist["error"] == "刷新炸了：scan 失败"  # 历史库是同一份
    assert hist["changed"] == detail["changed"] and hist["diff"] == detail["diff"]  # 两边不分叉
    _wait_idle()
    engine_watch.stop()


def test_finishing_a_live_session_sets_every_terminal_field(tmp_path, cli):
    """内存会话和历史库同一份终态字段清单：`_finish_session` 收尾之后，清单里的每个键在内存会话上都有（与合并路径用的清单一致）。"""
    figs = _project(tmp_path)
    sid = ai_bridge.run("codex", "fig1.py", "p", str(figs))
    deadline = time.time() + 15
    while time.time() < deadline and not ai_bridge.SESSIONS[sid].get("final"):
        time.sleep(0.05)
    sess = ai_bridge.SESSIONS[sid]
    missing = [k for k in ai_bridge.SESSION_TERMINAL_FIELDS if k not in sess]
    assert missing == [], missing
    _wait_idle()
    engine_watch.stop()
