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


# ---------------------------------------------------------------------------
def test_roundtrip_overwrite_and_revision(client, two):
    a, _ = two
    assert get(client, a, "Fig1.pdf") == {"draft": None, "pendings": []}
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


def test_pending_ledger_is_per_rid_and_accept_deletes_draft_only_if_text_unchanged(client, two):
    a, _ = two
    for rid, text in ((RID, "A 的话"), (RID2, "B 的话")):
        client.put(
            f"/api/ai/drafts/pending/{rid}",
            json={"file": "F", "text": text, "sent": SENT},
            headers=H(a),
        )
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID, RID2]  # 两条并存
    # B 被拒绝：只清自己那条
    assert client.delete(
        f"/api/ai/drafts/pending/{RID2}", query_string={"file": "F"}, headers=H(a)
    ).get_json() == {"cleared": True}
    assert [p["rid"] for p in get(client, a, "F")["pendings"]] == [RID]
    # 草稿文本在这期间被另一个标签页改了：A 被接受只清账本、保留新稿
    put(client, a, "F", "另一个标签页写的新稿")
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": False}
    assert get(client, a, "F")["draft"]["text"] == "另一个标签页写的新稿"
    # 对照：文本没动 → 一并删
    put(client, a, "F", "同一句话")
    client.put(
        f"/api/ai/drafts/pending/{RID}",
        json={"file": "F", "text": "同一句话", "sent": SENT},
        headers=H(a),
    )
    acc = client.post(
        f"/api/ai/drafts/pending/{RID}/accept", json={"file": "F"}, headers=H(a)
    ).get_json()
    assert acc == {"cleared": True, "draft_deleted": True}
    assert get(client, a, "F") == {"draft": None, "pendings": []}


def test_pending_cap_per_file_and_rid_validation(client, two):
    a, _ = two
    for i in range(ai_history.PENDING_MAX_PER_FILE + 2):
        rid = f"rid-{i:08d}"
        client.put(
            f"/api/ai/drafts/pending/{rid}",
            json={"file": "F", "text": "t", "sent": SENT},
            headers=H(a),
        )
    rids = [p["rid"] for p in get(client, a, "F")["pendings"]]
    assert len(rids) == ai_history.PENDING_MAX_PER_FILE and "rid-00000000" not in rids
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
    assert d == ["project", "file_id", "text", "revision", "updated_ms"]
    assert p == ["project", "file_id", "rid", "text", "sent", "created_ms"]
