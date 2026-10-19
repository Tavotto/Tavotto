"""助手草稿与待确认账本存后端（ADR 0132）。

主语是 (当前项目, fileId[, rid])：按项目隔离、过会话认证、单稿 20 KB（413 不截断）、删除带条件
（不删另一个标签页的新稿）、30 天过期有界清理、文本不进日志与诊断包。以前存在浏览器 localStorage 里，
桌面端口一变 origin 就换、草稿全丢——用户裁决（2026-10-15）：不得以 localStorage 的便利代替持久化设计。
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import zipfile
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import ai_history, project_watch as engine_watch

SECRET = "SECRET-DRAFT-TEXT-未发表"
RID = "11111111-2222-4333-8444-555555555555"
RID2 = "99999999-2222-4333-8444-555555555555"
SENT = {"scope": "figure", "gid": None, "label": None, "target": "整张图"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "hist.sqlite3")
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _project(tmp_path, client, name):
    d = tmp_path / name
    d.mkdir()
    (d / "tavotto_registry.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
    body = client.post(
        "/api/projects/open", json={"path": str(d), "default": name == "a"}
    ).get_json()
    return body["id"]


@pytest.fixture
def two(client, tmp_path):
    return _project(tmp_path, client, "a"), _project(tmp_path, client, "b")


def H(pj):
    return {"X-Tavotto-Project": pj}


def put(client, pj, file, text):
    return client.put("/api/ai/drafts", json={"file": file, "text": text}, headers=H(pj))


def get(client, pj, file):
    return client.get("/api/ai/drafts", query_string={"file": file}, headers=H(pj)).get_json()


def _seed_legacy_pending(project_dir, file, rid, text="t", script=None, db_path=None):
    """直接写库：「同面板 / 同脚本一次只有一条未落定」守住之前留下的数据。API 已经写不出这种状态（见
    `test_a_second_unresolved_submission_on_the_same_panel_or_script_is_refused`），名额、按 rid 的账本语义只能这样构造。"""
    with ai_history._connect(db_path) as con:
        con.execute(
            "INSERT INTO ai_pending (project, file_id, rid, text, sent, script, draft_stamp, created_ms) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                str(project_dir.resolve()),
                file,
                rid,
                text,
                json.dumps(SENT),
                script,
                None,
                ai_history._now_ms(),
            ),
        )


# ---------------------------------------------------------------------------
def test_roundtrip_overwrite_and_revision(client, two):
    a, _ = two
    got = get(client, a, "Fig1.pdf")
    assert got["draft"] is None and got["pendings"] == []
    r1 = put(client, a, "Fig1.pdf", "第一版").get_json()
    r2 = put(client, a, "Fig1.pdf", "第二版").get_json()  # 全文覆盖，最后写入为准
    assert r2["revision"] == r1["revision"] + 1
    d = get(client, a, "Fig1.pdf")["draft"]
    assert d["text"] == "第二版" and d["revision"] == r2["revision"]
    put(client, a, "Fig1.pdf", "")  # 空文本 = 删
    assert get(client, a, "Fig1.pdf")["draft"] is None


def test_isolated_by_project_and_by_file(client, two):
    a, b = two
    put(client, a, "Fig1.pdf", "A 项目的话")
    assert get(client, b, "Fig1.pdf")["draft"] is None  # 别的项目读不到
    assert get(client, a, "Fig2.pdf")["draft"] is None  # 同项目别的面板读不到
    assert get(client, a, "Fig1.pdf")["draft"]["text"] == "A 项目的话"  # 对照
    # 账本同样按项目隔离
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "Fig1.pdf", "text": "x", "sent": SENT},
        headers=H(a),
    )
    assert get(client, b, "Fig1.pdf")["pendings"] == []
    assert [p["rid"] for p in get(client, a, "Fig1.pdf")["pendings"]] == [RID]


def test_size_limit_is_413_with_a_stable_code_and_never_truncates(client, two):
    a, _ = two
    put(client, a, "F", "较短的旧稿")
    ok = put(client, a, "F", "a" * ai_history.DRAFT_MAX_BYTES)
    assert ok.status_code == 200
    big = put(client, a, "F", "a" * (ai_history.DRAFT_MAX_BYTES + 1))
    assert big.status_code == 413
    assert big.get_json()["code"] == "ai_draft_too_large" and big.get_json()["params"] == {
        "limit_kb": 20
    }
    assert get(client, a, "F")["draft"] is None  # 旧稿也不留：恢复出来的不能是一句过期的话
    # 按字节不按字符：7000 个汉字 = 21000 字节
    assert put(client, a, "F", "图" * 7000).status_code == 413


def test_413_carries_the_tombstone_stamp(client, two):
    """超限写入服务端留了墓碑（带戳）：413 响应把这个戳带回，客户端靠它抬高下限、退出「基线未知」。"""
    a, _ = two
    r = client.put(
        "/api/ai/drafts",
        json={"file": "F", "text": "a" * (ai_history.DRAFT_MAX_BYTES + 1), "stamp": 123_456},
        headers=H(a),
    )
    assert r.status_code == 413 and r.get_json()["code"] == "ai_draft_too_large"
    assert r.get_json()["stamp"] == 123_456
    assert get(client, a, "F")["stamp"] == 123_456  # 与墓碑上的戳是同一个
    # 不带戳（老前端）时带回服务端取的戳
    r2 = put(client, a, "F", "b" * (ai_history.DRAFT_MAX_BYTES + 1))
    assert (
        r2.status_code == 413 and r2.get_json()["stamp"] == get(client, a, "F")["stamp"] > 123_456
    )


def test_conditional_delete_never_removes_a_newer_draft(client, two):
    a, _ = two
    put(client, a, "F", "旧的话")
    # 标签页 B 在这之间写了新稿
    put(client, a, "F", "B 后来写的话")
    res = client.delete("/api/ai/drafts", json={"file": "F", "if_text": "旧的话"}, headers=H(a))
    assert res.get_json() == {"deleted": False}
    assert get(client, a, "F")["draft"]["text"] == "B 后来写的话"
    ok = client.delete(
        "/api/ai/drafts", json={"file": "F", "if_text": "B 后来写的话"}, headers=H(a)
    )
    assert ok.get_json() == {"deleted": True}
    assert get(client, a, "F")["draft"] is None


def test_pending_ledger_is_per_rid_and_accept_deletes_draft_only_if_text_unchanged(
    client, two, tmp_path
):
    a, _ = two
    assert (
        client.put(
            f"/api/ai/drafts/pending/{RID}",
            json={"file": "F", "text": "A 的话", "sent": SENT},
            headers=H(a),
        ).status_code
        == 200
    )
    second = client.put(
        f"/api/ai/drafts/pending/{RID2}",
        json={"file": "F", "text": "B 的话", "sent": SENT},
        headers=H(a),
    )  # 同面板第二条未落定的：API 拒绝
    assert second.status_code == 409 and second.get_json()["rids"] == [RID]
    _seed_legacy_pending(tmp_path / "a", "F", RID2, "B 的话")  # 守住之前留下的两条并存
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID, RID2]  # 两条并存
    # B 被拒绝：只清自己那条
    assert client.delete(
        f"/api/ai/drafts/pending/{RID2}", query_string={"file": "F"}, headers=H(a)
    ).get_json() == {"cleared": True}
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID]
    # 提交时记下草稿的写入戳；之后另一个标签页改了草稿（戳变了）：A 被接受只清账本、保留新稿
    r = put_s(client, a, "F", "提交时的草稿", 10_000)
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": "提交时的草稿", "sent": SENT, "draft_stamp": r["stamp"]},
        headers=H(a),
    )
    put_s(client, a, "F", "另一个标签页写的新稿", 20_000)
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": False}
    assert get(client, a, "F")["draft"]["text"] == "另一个标签页写的新稿"
    # 对照：草稿自提交后没被改过（戳没变）→ 一并删
    r = put_s(client, a, "F", "同一句话", 30_000)
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": "同一句话", "sent": SENT, "draft_stamp": r["stamp"]},
        headers=H(a),
    )
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": True}
    final = get(client, a, "F")
    assert final["draft"] is None and final["pendings"] == []


def test_accept_compares_the_draft_stamp_not_the_text(client, two):
    """账本里是 trim 过的提交文本、草稿是原样（前后带空白 / 换行）：按文本比永远对不上，已被接受的指令会留在输入框里。
    按草稿戳比：草稿自提交后没被改过 → 删，不管文本差异。"""
    a, _ = two
    r = put_s(client, a, "F", "  把线条加粗一点\n", 10_000)  # 草稿原样
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={
            "file": "F",
            "text": "把线条加粗一点",
            "sent": SENT,
            "draft_stamp": r["stamp"],
        },  # 提交文本 trim 过
        headers=H(a),
    )
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": True}
    assert get(client, a, "F")["draft"] is None


def test_accept_without_a_recorded_draft_stamp_leaves_the_draft_alone(client, two):
    """账本没记戳 = 不知道草稿此刻是不是提交时那一份：只清账本，不动草稿。"""
    a, _ = two
    put_s(client, a, "F", "不知道是不是同一份", 10_000)
    client.put(
        f"/api/ai/drafts/pending/{RID}", json={"file": "F", "text": "x", "sent": SENT}, headers=H(a)
    )
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": False}
    assert get(client, a, "F")["draft"]["text"] == "不知道是不是同一份"
    assert (
        client.put(
            f"/api/ai/drafts/pending/{RID}",
            json={"file": "F", "text": "x", "sent": SENT, "draft_stamp": -1},
            headers=H(a),
        ).status_code
        == 400
    )


def test_get_reports_the_row_stamp_even_for_a_tombstone(client, two):
    """读回的 stamp 让客户端抬高自己的戳下限（墙钟回拨 / 新开页面从 0 起步也不会被判 stale）；墓碑也带。"""
    a, _ = two
    assert get(client, a, "F")["stamp"] == 0
    put_s(client, a, "F", "稿", 7_000)
    assert get(client, a, "F")["stamp"] == 7_000 and get(client, a, "F")["draft"]["stamp"] == 7_000
    put_s(client, a, "F", "", 8_000)  # 清空 = 墓碑
    g = get(client, a, "F")
    assert g["draft"] is None and g["stamp"] == 8_000


def test_pending_cap_per_file_and_rid_validation(client, two, tmp_path):
    a, _ = two
    n = ai_history.PENDING_MAX_PER_FILE
    for i in range(n):  # 守住之前留下的满额旧数据
        _seed_legacy_pending(tmp_path / "a", "F", f"rid-{i:08d}")
    codes = []
    for i in range(n, n + 2):
        rid = f"rid-{i:08d}"
        r = client.put(
            f"/api/ai/drafts/pending/{rid}",
            json={"file": "F", "text": "t", "sent": SENT},
            headers=H(a),
        )
        codes.append((r.status_code, r.get_json()["code"]))
    # 满了**拒绝新的、不删旧的**：旧的可能正是后端已接受、等着被查询的那条（删了就可能被同一条指令换新 id 再执行）
    assert codes == [(409, "ai_pending_limit")] * 2
    rids = [p["rid"] for p in get(client, a, "F")["pendings"]]
    assert sorted(rids) == [f"rid-{i:08d}" for i in range(n)]  # 最老的那条还在，新的两条没进
    over = client.put(
        "/api/ai/drafts/pending/rid-99999999",
        json={"file": "F", "text": "t", "sent": SENT},
        headers=H(a),
    )
    assert over.get_json()["code"] == "ai_pending_limit" and over.get_json()["params"] == {
        "count": n
    }
    # 同 rid 重写（重试写账本）不占新名额
    assert (
        client.put(
            "/api/ai/drafts/pending/rid-00000000",
            json={"file": "F", "text": "t2", "sent": SENT},
            headers=H(a),
        ).status_code
        == 200
    )
    # 解决一条（确认 / 清掉）之后名额腾出来了；但同面板其余的仍没落定——新的照样进不来（换成「还有没落定的」）
    client.delete("/api/ai/drafts/pending/rid-00000003?file=F", headers=H(a))
    after = client.put(
        "/api/ai/drafts/pending/rid-99999999",
        json={"file": "F", "text": "t", "sent": SENT},
        headers=H(a),
    )
    assert after.status_code == 409 and after.get_json()["code"] == "ai_pending_unresolved"
    assert client.put(
        "/api/ai/drafts/pending/bad rid!",
        json={"file": "F", "text": "t", "sent": SENT},
        headers=H(a),
    ).status_code in (400, 404)
    assert (
        client.put(
            f"/api/ai/drafts/pending/{RID}",
            json={"file": "F", "text": 1, "sent": SENT},
            headers=H(a),
        ).status_code
        == 400
    )
    assert client.get("/api/ai/drafts", headers=H(a)).status_code == 400  # 没给 file


def test_expiry_is_bounded_and_runs_on_write_and_at_startup(client, two, tmp_path):
    a, _ = two
    put(client, a, "Old", "老稿")
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "Old", "text": "x", "sent": SENT},
        headers=H(a),
    )
    long_ago = ai_history._now_ms() - (ai_history.DRAFT_KEEP_DAYS + 1) * 86400 * 1000
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        raw.execute("UPDATE ai_drafts SET updated_ms=?", (long_ago,))
        raw.execute("UPDATE ai_pending SET created_ms=?", (long_ago,))
    put(client, a, "New", "新稿")  # 写入时顺手清
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        assert raw.execute("SELECT COUNT(*) FROM ai_drafts WHERE file_id='Old'").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM ai_pending").fetchone()[0] == 0
    # 启动清扫
    put(client, a, "Old2", "x")
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        raw.execute("UPDATE ai_drafts SET updated_ms=? WHERE file_id='Old2'", (long_ago,))
    assert ai_history.purge_drafts() == 1


def test_per_project_draft_count_is_bounded(client, two):
    a, _ = two
    for i in range(ai_history.DRAFT_MAX_PER_PROJECT + 3):
        put(client, a, f"F{i}", "t")
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        assert (
            raw.execute("SELECT COUNT(*) FROM ai_drafts").fetchone()[0]
            == ai_history.DRAFT_MAX_PER_PROJECT
        )


def test_survives_a_backend_restart_because_it_is_in_the_database(client, two):
    """内存里什么都不留：换一条连接读（等价于重启）仍在。"""
    a, _ = two
    put(client, a, "F", "重启也在")
    with sqlite3.connect(ai_history.DB_PATH) as raw:
        assert raw.execute("SELECT text FROM ai_drafts").fetchone()[0] == "重启也在"


# ---------------------------------------------------------------------------
# 隐私：文本只在库里
# ---------------------------------------------------------------------------
def test_draft_text_never_reaches_the_log_or_the_diagnostics_bundle(client, two, caplog):
    a, _ = two
    with caplog.at_level(logging.DEBUG):
        put(client, a, "F", SECRET)
        client.put(
            f"/api/ai/drafts/pending/{RID}",
            json={"file": "F", "text": SECRET, "sent": SENT},
            headers=H(a),
        )
        get(client, a, "F")
        client.post(f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a))
        put(client, a, "F", SECRET)
        put(client, a, "G", "x" * (ai_history.DRAFT_MAX_BYTES + 1))
    assert SECRET not in "\n".join(r.getMessage() for r in caplog.records)
    assert RID not in "\n".join(r.getMessage() for r in caplog.records)
    # 诊断包（GET 与 POST 两条路）里搜不到
    raw = client.get("/api/diagnostics/bundle").data
    z = zipfile.ZipFile(io.BytesIO(raw))
    blob = b"".join(z.read(n) for n in z.namelist())
    assert SECRET.encode() not in blob


def test_tables_hold_only_the_documented_columns():
    with sqlite3.connect(":memory:") as raw:
        raw.executescript(ai_history._SCHEMA)
        d = [r[1] for r in raw.execute("PRAGMA table_info(ai_drafts)")]
        p = [r[1] for r in raw.execute("PRAGMA table_info(ai_pending)")]
    assert d == ["project", "file_id", "text", "revision", "updated_ms", "stamp"]
    assert p == ["project", "file_id", "rid", "text", "sent", "script", "draft_stamp", "created_ms"]


def test_pendings_are_listed_by_project_across_panels_with_their_script(client, two):
    """发起请求的面板可能已被删 / 换图：账本带 script，按项目跨面板列出，不依赖当前有哪些面板。"""
    a, b = two
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "Gone.pdf", "text": "x", "sent": SENT, "script": "sub/fig1.py"},
        headers=H(a),
    )
    client.put(
        f"/api/ai/drafts/pending/{RID2}",
        json={"file": "Other.pdf", "text": "y", "sent": SENT},
        headers=H(a),
    )
    got = client.get("/api/ai/drafts/pending", headers=H(a)).get_json()["pendings"]
    assert [(p["file"], p["rid"], p["script"]) for p in got] == [
        ("Gone.pdf", RID, "sub/fig1.py"),
        ("Other.pdf", RID2, None),
    ]
    assert client.get("/api/ai/drafts/pending", headers=H(b)).get_json() == {
        "pendings": []
    }  # 按项目隔离
    # 被接受 / 拒绝之后不再列出
    client.post(f"/api/ai/drafts/pending/{RID}/accept", json={"file": "Gone.pdf"}, headers=H(a))
    assert [
        p["rid"] for p in client.get("/api/ai/drafts/pending", headers=H(a)).get_json()["pendings"]
    ] == [RID2]
    assert (
        client.put(
            f"/api/ai/drafts/pending/{RID}",
            json={"file": "F", "text": "t", "sent": SENT, "script": 5},
            headers=H(a),
        ).status_code
        == 400
    )


def test_pending_table_without_script_column_is_migrated(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as raw:
        raw.executescript(
            "CREATE TABLE ai_pending (project TEXT NOT NULL, file_id TEXT NOT NULL, rid TEXT NOT NULL, "
            "text TEXT NOT NULL, sent TEXT NOT NULL, created_ms INTEGER NOT NULL, PRIMARY KEY (project, file_id, rid));"
            f"INSERT INTO ai_pending VALUES ('/p','F','rid-aaaaaaaa','t','{{}}',{ai_history._now_ms()});"
        )
    got = ai_history.list_pendings("/p", db_path=db)
    assert got[0]["rid"] == "rid-aaaaaaaa" and got[0]["script"] is None
    # 另一个面板（同面板会被「还有没落定的」挡住）：看的是迁移出来的 script 列能写能读
    ai_history.put_pending("/p", "G", "rid-bbbbbbbb", "t", {}, db_path=db, script="a/plot.py")
    assert {p["rid"]: p["script"] for p in ai_history.list_pendings("/p", db_path=db)} == {
        "rid-aaaaaaaa": None,
        "rid-bbbbbbbb": "a/plot.py",
    }


# ---------------------------------------------------------------------------
# 写入戳：旧请求赢在服务器端就不可能发生
# ---------------------------------------------------------------------------
def put_s(client, pj, file, text, stamp):
    return client.put(
        "/api/ai/drafts", json={"file": file, "text": text, "stamp": stamp}, headers=H(pj)
    ).get_json()


def test_a_write_with_an_older_stamp_is_rejected_and_never_overwrites(client, two):
    """PUT A 在途时用户又写了 B：B（keepalive）先落地、A 后落地——A 的戳更旧，必须被拒，服务器最终留着 B。"""
    a, _ = two
    assert put_s(client, a, "F", "B（新）", 2_000)["revision"] >= 1  # 新戳先到
    late = put_s(client, a, "F", "A（旧）", 1_000)  # 旧戳后到
    assert late["stale"] is True and late["stamp"] == 2_000
    assert get(client, a, "F")["draft"]["text"] == "B（新）"
    # 对照：更新的戳被接受
    ok = put_s(client, a, "F", "C（更新）", 3_000)
    assert "stale" not in ok and ok["stamp"] == 3_000
    assert get(client, a, "F")["draft"]["text"] == "C（更新）"


def test_an_equal_stamp_is_not_newer_and_is_rejected(client, two):
    a, _ = two
    put_s(client, a, "F", "先到", 5_000)
    assert put_s(client, a, "F", "同戳后到", 5_000)["stale"] is True
    assert get(client, a, "F")["draft"]["text"] == "先到"


def test_a_late_old_write_cannot_resurrect_a_deleted_draft(client, two):
    """空文本 = 删，但留墓碑（戳还在）：删除之后迟到的旧写入同样被拒，不会复活。"""
    a, _ = two
    put_s(client, a, "F", "旧稿", 1_000)
    put_s(client, a, "F", "", 2_000)  # 用户清空（删）
    assert get(client, a, "F")["draft"] is None
    assert put_s(client, a, "F", "迟到的旧稿", 1_500)["stale"] is True
    assert get(client, a, "F")["draft"] is None


def test_server_side_deletes_advance_the_stamp_so_earlier_writes_are_rejected(client, two):
    """条件删除 / accept 清掉草稿后，在那之前发出的旧写入（戳更小）随后落地也被拒。"""
    a, _ = two
    now_stamp = ai_history._now_stamp()
    r = put_s(client, a, "F", "已被接受的话", now_stamp - 5_000_000)
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": "已被接受的话", "sent": SENT, "draft_stamp": r["stamp"]},
        headers=H(a),
    )
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc["draft_deleted"] is True
    # 被取代的那一版（戳 = 当初记进账本的 draft_stamp）迟到的 PUT / 重试复活不了
    assert put_s(client, a, "F", "已被接受的话", now_stamp - 5_000_000)["stale"] is True
    assert put_s(client, a, "F", "更早的", now_stamp - 6_000_000)["stale"] is True
    assert get(client, a, "F")["draft"] is None
    # 墓碑只推进 +1（取代的恰好是被删的那一版）：用户**之后**新写的（戳更大）不会被服务端清理当成「更新的写入」挡掉
    assert "stale" not in put_s(client, a, "F", "提交之后新写的", now_stamp - 4_000_000)
    assert get(client, a, "F")["draft"]["text"] == "提交之后新写的"


def test_stamps_are_per_project_and_per_file(client, two):
    a, b = two
    put_s(client, a, "F", "A 项目", 9_000)
    assert "stale" not in put_s(client, b, "F", "B 项目戳更小也不受影响", 1_000)
    assert "stale" not in put_s(client, a, "G", "别的面板", 1_000)


def test_unstamped_legacy_writes_are_treated_as_newest(client, two):
    a, _ = two
    put_s(client, a, "F", "带戳的", ai_history._now_stamp() + 50_000_000)
    r = client.put(
        "/api/ai/drafts", json={"file": "F", "text": "老前端不带戳"}, headers=H(a)
    ).get_json()
    assert "stale" not in r
    assert get(client, a, "F")["draft"]["text"] == "老前端不带戳"
    assert (
        client.put(
            "/api/ai/drafts", json={"file": "F", "text": "x", "stamp": -1}, headers=H(a)
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/ai/drafts", json={"file": "F", "text": "x", "stamp": "9"}, headers=H(a)
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/ai/drafts", json={"file": "F", "text": "x", "stamp": True}, headers=H(a)
        ).status_code
        == 400
    )


def test_old_drafts_table_without_stamp_is_migrated(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as raw:
        raw.executescript(
            "CREATE TABLE ai_drafts (project TEXT NOT NULL, file_id TEXT NOT NULL, text TEXT NOT NULL, "
            "revision INTEGER NOT NULL, updated_ms INTEGER NOT NULL, PRIMARY KEY (project, file_id));"
            f"INSERT INTO ai_drafts VALUES ('/p','F','旧稿',1,{ai_history._now_ms()});"
        )
    assert (
        ai_history.get_draft("/p", "F", db_path=db)["draft"]["text"] == "旧稿"
    )  # 迁移补列后老行仍在
    assert (
        ai_history.put_draft("/p", "F", "新稿", db_path=db, stamp=10)["stamp"] == 10
    )  # 老行戳为 0，新戳更大被接受
    assert ai_history.put_draft("/p", "F", "更旧", db_path=db, stamp=5)["stale"] is True
    assert ai_history.get_draft("/p", "F", db_path=db)["draft"]["text"] == "新稿"


def test_instruction_limit_is_one_number_on_both_sides():
    """草稿与账本共用一个「指令最大长度」，且与前端常量同源（上限不一致 = 草稿存不下、指令却发得出去、草稿清不掉）。"""
    from pathlib import Path

    from support.tsconst import exported_number

    ts = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "aiDraft.ts"
    assert (
        exported_number(ts.read_text(encoding="utf-8"), "INSTRUCTION_MAX_BYTES")
        == ai_history.INSTRUCTION_MAX_BYTES
    )
    assert (
        ai_history.DRAFT_MAX_BYTES
        == ai_history.PENDING_MAX_BYTES
        == ai_history.INSTRUCTION_MAX_BYTES
    )


def test_pending_text_over_the_shared_limit_is_413_like_a_draft(client, two):
    a, _ = two
    over = "a" * (ai_history.INSTRUCTION_MAX_BYTES + 1)
    r = client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": over, "sent": SENT},
        headers=H(a),
    )
    assert r.status_code == 413 and r.get_json()["code"] == "ai_draft_too_large"
    ok = client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": "a" * ai_history.INSTRUCTION_MAX_BYTES, "sent": SENT},
        headers=H(a),
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# 读旧戳 → 判断 → 写 必须在同一个写事务里（Codex #1098 P1 r4289174818）
# ---------------------------------------------------------------------------
def test_put_draft_read_check_write_is_one_transaction(tmp_path, monkeypatch):
    """两个独立连接：旧戳的写入先读完旧状态、卡在「写」之前；新戳的写入此时进来。读-判-写不在同一个写事务里时，
    旧的会带着过期的视图覆盖回去；在 `BEGIN IMMEDIATE` 里，旧的持锁期间新的等着，之后按真实戳判——结果恒为新的。"""
    import threading
    import time as _time

    db = tmp_path / "hist.sqlite3"
    ai_history.get_draft("/p", "F", db_path=db)  # 先把库建好、迁移完：下面只量读-判-写的窗口
    real_connect = ai_history._connect
    old_reached_write = threading.Event()

    def slow_connect(db_path=None):
        con = real_connect(db_path)

        def trace(stmt):
            if "INSERT OR REPLACE INTO ai_drafts" in stmt and "旧戳的稿" in stmt:
                old_reached_write.set()  # 旧戳的写入已读完 prev、马上要写
                _time.sleep(0.5)  # 放大「读完到写之间」的窗口

        con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(ai_history, "_connect", slow_connect)
    errors: list[BaseException] = []

    def put(text, stamp):
        try:
            ai_history.put_draft("/p", "F", text, db_path=db, stamp=stamp)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_old = threading.Thread(target=put, args=("旧戳的稿", 100))
    t_old.start()
    assert old_reached_write.wait(5)
    t_new = threading.Thread(target=put, args=("新戳的稿", 200))
    t_new.start()
    t_old.join()
    t_new.join()
    assert errors == []
    got = ai_history.get_draft("/p", "F", db_path=db)
    assert got["draft"]["text"] == "新戳的稿" and got["stamp"] == 200  # 旧戳没有覆盖回去


def test_pending_gate_holds_under_concurrent_writers(tmp_path, monkeypatch):
    """判「同面板还有没落定的」与写入在同一个写事务里：两个连接同时给同一个面板提交，恰好一个成功、一个被拒（两条都写进去
    就等于两条指令都放行了）。让先到的那个在「判完、写之前」卡一下，放大窗口。名额（`PENDING_MAX_PER_FILE`）与它同一个事务，
    守住之后只对旧数据起作用，见 `test_pending_cap_per_file_and_rid_validation`。"""
    import threading
    import time as _time

    db = tmp_path / "hist.sqlite3"
    real_connect = ai_history._connect
    first_counted = threading.Event()

    def slow_connect(db_path=None):
        con = real_connect(db_path)

        def trace(stmt):
            if "INSERT OR REPLACE INTO ai_pending" in stmt and "rid-slow-00000001" in stmt:
                first_counted.set()  # 已经数过名额，马上要写
                _time.sleep(0.5)

        con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(ai_history, "_connect", slow_connect)
    results: dict[str, str] = {}

    def submit(rid):
        try:
            ai_history.put_pending("/p", "F", rid, "t", SENT, db_path=db)
            results[rid] = "ok"
        except ai_history.PendingUnresolved:
            results[rid] = "refused"

    a = threading.Thread(target=submit, args=("rid-slow-00000001",))
    a.start()
    assert first_counted.wait(5)
    b = threading.Thread(target=submit, args=("rid-fast-00000001",))
    b.start()
    a.join()
    b.join()
    assert sorted(results.values()) == ["ok", "refused"], results
    assert len(ai_history.get_draft("/p", "F", db_path=db)["pendings"]) == 1


def test_concurrent_first_connections_to_a_new_database_do_not_race_on_alter(tmp_path):
    """两个服务进程（或线程）同时打开同一个还没迁移过的库：「先查列有没有再 ALTER」不是原子的，后到的撞
    `duplicate column name`。窗口很窄，所以多轮、每轮一个全新的库。"""
    import threading

    errors: list[BaseException] = []
    for rnd in range(150):
        db = tmp_path / f"new{rnd}.sqlite3"
        barrier = threading.Barrier(6)

        def open_it(db=db):
            barrier.wait()
            try:
                ai_history.get_draft("/p", "F", db_path=db)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        ts = [threading.Thread(target=open_it) for _ in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    assert errors == []


# ---------------------------------------------------------------------------
# 账本行的删除只发生在后端事务里（accept、run 明确拒绝、supersedes）——客户端不单方面 DELETE
# ---------------------------------------------------------------------------
def _put_pending(client, pj, rid, text="t", **extra):
    return client.put(
        f"/api/ai/drafts/pending/{rid}",
        json={"file": "F", "text": text, "sent": SENT, **extra},
        headers=H(pj),
    )


def test_supersedes_deletes_the_old_row_in_the_same_transaction_as_the_new_write(client, two):
    a, _ = two
    assert _put_pending(client, a, "rid-old-00000001", "旧").status_code == 200
    assert (
        _put_pending(
            client, a, "rid-new-00000001", "新", supersedes=["rid-old-00000001"]
        ).status_code
        == 200
    )
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [
        "rid-new-00000001"
    ]  # 旧行随新写入一起没了


def test_supersedes_deletes_the_old_row_even_when_it_was_filed_under_a_sibling_panel(client, two):
    """同一份脚本的兄弟面板：旧等待挂在面板 F 下，「改写后再发」从面板 G 发——旧行照样随新写入删掉（项目内按 rid），
    不按新行的 file_id 删（否则旧行之后又被还原成「未确认」）。"""
    a, _ = two
    assert _put_pending(client, a, "rid-old-00000001", "旧", script="s.py").status_code == 200
    r = client.put(
        "/api/ai/drafts/pending/rid-new-00000001",
        json={
            "file": "G",
            "text": "新",
            "sent": SENT,
            "script": "s.py",
            "supersedes": ["rid-old-00000001"],
        },
        headers=H(a),
    )
    assert r.status_code == 200
    assert get(client, a, "F")["pendings"] == []
    assert [p["rid"] for p in get(client, a, "G")["pendings"]] == ["rid-new-00000001"]


def test_a_second_unresolved_submission_on_the_same_panel_or_script_is_refused(client, two):
    """「这份脚本有没有还没落定的指令」由后端在写账本的事务里判：同面板、或同一份脚本的兄弟面板（`same_script`：分隔符不同、
    旧数据的绝对路径与相对后缀都算同一份）上还有未落定的提交、这次又没在 `supersedes` 里显式取代它 → 409 `ai_pending_unresolved`，
    回它们的 rid、什么都不写。别的脚本不受影响；旧的那条被确认（accept）之后照常放行。"""
    a, _ = two
    old = "rid-old-00000009"
    assert _put_pending(client, a, old, "旧", script="figs/plot.py").status_code == 200
    for file, script in (
        ("F", None),  # 同面板
        ("G", "figs/plot.py"),  # 兄弟面板
        ("G", "figs\\plot.py"),  # 分隔符不同
        ("G", "./figs//plot.py"),
    ):
        r = client.put(
            "/api/ai/drafts/pending/rid-new-00000009",
            json={"file": file, "text": "新", "sent": SENT, "script": script},
            headers=H(a),
        )
        assert r.status_code == 409, (file, script)
        assert r.get_json()["code"] == "ai_pending_unresolved" and r.get_json()["rids"] == [old]
        # 挡路的行本身也带回来（与 GET 列表同形）：客户端照着还原，不靠之后另读——那之前别的标签页可能已确认并删了它
        assert r.get_json()["pendings"] == [
            {"file": "F", "rid": old, "text": "旧", "sent": SENT, "script": "figs/plot.py"}
        ]
    assert get(client, a, "G")["pendings"] == []  # 被拒：什么都没写
    # 别的脚本、别的面板：不受影响
    other = client.put(
        "/api/ai/drafts/pending/rid-other-0000009",
        json={"file": "H", "text": "别的", "sent": SENT, "script": "figs/other.py"},
        headers=H(a),
    )
    assert other.status_code == 200
    # 旧的那条被确认之后：同一份脚本照常可以发新的
    assert client.post(
        f"/api/ai/drafts/pending/{old}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()["cleared"]
    assert (
        _put_pending(client, a, "rid-new-00000009", "新", script="figs/plot.py").status_code == 200
    )


def test_same_script_matches_the_frontend_case_table():
    """`ai_history.same_script` ↔ `web/src/store/aiStore.ts` 的 `sameScript` 严格同源：两边跑同一张用例表。"""
    cases = json.loads(
        (Path(__file__).resolve().parents[1] / "web/src/store/sameScriptCases.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(cases) >= 8
    for a, b, want in cases:
        assert ai_history.same_script(a, b) is want, (a, b)
        assert ai_history.same_script(b, a) is want, (b, a)


def test_a_failed_new_write_keeps_the_superseded_row(client, two):
    """新 PUT 失败 = 整个事务回滚 = 旧行还在，状态仍然一致（旧行之后只能被查询 / 确认，不会凭空消失）。"""
    a, _ = two
    _put_pending(client, a, "rid-old-00000001", "旧")
    big = "x" * (ai_history.INSTRUCTION_MAX_BYTES + 1)
    r = _put_pending(client, a, "rid-new-00000001", big, supersedes=["rid-old-00000001"])
    assert r.status_code == 413
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == ["rid-old-00000001"]


def test_supersedes_frees_a_slot_before_the_cap_is_counted(client, two, tmp_path):
    a, _ = two
    n = ai_history.PENDING_MAX_PER_FILE
    fill = [f"rid-fill-{i:08d}" for i in range(n)]
    for rid in fill:  # 守住之前留下的满额旧数据
        _seed_legacy_pending(tmp_path / "a", "F", rid)
    assert _put_pending(client, a, "rid-over-00000001").get_json()["code"] == "ai_pending_limit"
    # 只取代一条：名额腾出来了，但其余的仍没落定——照样拒绝
    r = _put_pending(client, a, "rid-over-00000001", supersedes=fill[:1])
    assert r.status_code == 409 and r.get_json()["code"] == "ai_pending_unresolved"
    assert r.get_json()["rids"] == fill[1:]  # 取代之后仍没落定的那些
    assert [
        p["rid"] for p in get(client, a, "F")["pendings"]
    ] == fill  # 整个事务回滚：取代的那条也还在
    # 全部显式取代：先删再数名额、再判，才放行
    r = _put_pending(client, a, "rid-over-00000001", supersedes=fill)
    assert r.status_code == 200
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == ["rid-over-00000001"]


def test_supersedes_is_validated(client, two):
    a, _ = two
    assert (
        _put_pending(client, a, "rid-new-00000001", supersedes="rid-old-00000001").status_code
        == 400
    )
    assert _put_pending(client, a, "rid-new-00000001", supersedes=["bad rid!"]).status_code == 400


def test_a_rejected_run_deletes_its_pending_row_on_the_backend(client, two):
    """/api/ai/run 明确拒绝（这里是未知 agent，4xx）：后端自己把这个 rid 的待确认行删掉。
    客户端不再 DELETE——它的 DELETE 可能失败，旧行随后被还原成「未确认」，而新 rid 已经执行过。"""
    a, _ = two
    _put_pending(client, a, RID, "指令")
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID]
    r = client.post(
        "/api/ai/run",
        json={"agent": "no-such-agent", "id": "F", "prompt": "p", "client_request_id": RID},
        headers=H(a),
    )
    assert r.status_code >= 400
    assert get(client, a, "F")["pendings"] == []
    # 别的 rid 的行不受影响
    _put_pending(client, a, RID2, "另一条")
    client.post(
        "/api/ai/run",
        json={"agent": "no-such-agent", "id": "F", "prompt": "p", "client_request_id": RID},
        headers=H(a),
    )
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID2]


# ---------------------------------------------------------------------------
# stale 响应带 text_hash（服务器那一行当前文本的哈希）：客户端在「戳相等」时拿它确认是不是自己的内容
# ---------------------------------------------------------------------------
TEXT_HASH_FIXTURES = [
    ("", "cbf29ce484222325"),
    ("a", "af63dc4c8601ec8c"),
    ("你好，图例", "38f6689529094f15"),
    ("😀 emoji + 中文 + \n换行", "34de6d5cb9e118b8"),
    ("x" * 5000, "5d45fa080be4e345"),
]


@pytest.mark.parametrize("text,expected", TEXT_HASH_FIXTURES)
def test_text_hash_matches_the_frontend_fixtures(text, expected):
    """前端 `textHash`（web/src/lib/aiDraft.ts）用同一组固定文本钉住；两边任何一边改了算法，两个测试同时红。"""
    assert ai_history.text_hash(text) == expected


def test_stale_response_carries_the_hash_of_the_current_row_text(client, two):
    a, _ = two
    put_s(client, a, "F", "服务器上的稿", 5_000)
    r = put_s(client, a, "F", "更旧的稿", 4_000)  # 旧戳 → stale
    assert r["stale"] is True and r["stamp"] == 5_000
    assert r["text_hash"] == ai_history.text_hash(
        "服务器上的稿"
    )  # 当前那一行的文本，不是被拒的这份
    # 等戳（同戳的另一份内容）同样回当前行的哈希：客户端据此判是不是自己的内容
    r2 = put_s(client, a, "F", "同戳但别的内容", 5_000)
    assert r2["stale"] is True and r2["text_hash"] == ai_history.text_hash("服务器上的稿")
    # 墓碑（空文本）：哈希是空串的
    put_s(client, a, "F", "", 6_000)
    r3 = put_s(client, a, "F", "旧", 5_500)
    assert r3["text_hash"] == ai_history.text_hash("")
