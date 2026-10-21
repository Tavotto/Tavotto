"""显式修复入口 `POST /api/versions/<doc>/rebuild`（ADR 0140 §八，D3）。

索引损坏 / 缺失 / 与信封对不上 → 在版本锁内从信封重建索引；只写新索引与一份旧索引字节副本，不删任何文件。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

from tavotto import app as m
from tavotto.engine import layoutsession, versionstore as vs
from tests.support import versionfiles


def _doc(tag: str) -> dict:
    return {
        "schema": 2,
        "name": "fig",
        "page": {"w": 150, "h": 100},
        "objects": [
            {
                "id": "o0",
                "type": "text",
                "text": tag,
                "x": 0,
                "y": 0,
                "w": 10,
                "h": 5,
                "sizePt": 9,
                "bold": False,
                "color": "#000",
                "align": "left",
            }
        ],
        "guides": [],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _seed(client, n: int = 4) -> list[str]:
    ids = []
    for i in range(n):
        r = client.post(
            "/api/versions/d1", json={"doc": _doc(f"t{i}"), "name": f"v{i}", "auto": False}
        )
        assert r.status_code == 200, r.get_json()
        ids.append(r.get_json()["version"]["id"])
    return ids


def _listing(client) -> list[dict]:
    r = client.get("/api/versions/d1")
    assert r.status_code == 200
    return [{k: v for k, v in e.items() if k != "thumb"} for e in r.get_json()["versions"]]


def _index_path():
    return versionfiles.store_root("d1") / "index.json"


@pytest.mark.parametrize("damage", ["delete", "truncate", "garbage"])
def test_rebuild_restores_a_damaged_index_to_match_envelopes(client, damage):
    ids = _seed(client)
    client.patch(f"/api/versions/d1/{ids[1]}", json={"name": "改过的名字"})
    expected = _listing(client)
    envelopes = {p.name: p.read_bytes() for p in (versionfiles.store_root("d1") / "s").iterdir()}
    idx = _index_path()
    raw = idx.read_bytes()
    if damage == "delete":
        idx.unlink()
    elif damage == "truncate":
        idx.write_bytes(raw[: len(raw) // 2])
    else:
        idx.write_bytes(b"\x00\xffnot json")

    r = client.post("/api/versions/d1/rebuild")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True and body["count"] == len(ids)
    assert body["added"] == [] and body["dropped"] == []
    # 落盘的索引是好的（读侧不再标 recovered），列表与信封一致
    assert not vs.list_index(m._versions_path("d1").parent, "d1").get("recovered")
    assert _listing(client) == expected
    # 不删任何文件：信封逐字节不变；坏索引的原字节留了副本
    assert {
        p.name: p.read_bytes() for p in (versionfiles.store_root("d1") / "s").iterdir()
    } == envelopes
    kept = list(versionfiles.store_root("d1").glob("index.corrupt-*.json"))
    assert len(kept) == (0 if damage == "delete" else 1)


def test_rebuild_reports_resurrected_and_vanished_ids_and_deletes_nothing(client):
    ids = _seed(client)
    root = versionfiles.store_root("d1")
    # 模拟同步工具：一个信封凭空出现（已删版本的残留），一个信封被拿走
    stray = root / "s" / f"{ids[0]}.json"
    stray_bytes = stray.read_bytes()
    assert client.delete(f"/api/versions/d1/{ids[0]}").status_code == 200
    stray.write_bytes(stray_bytes)
    (root / "s" / f"{ids[3]}.json").rename(root.parent / "taken-away.json")
    before_files = sorted(p.name for p in (root / "s").iterdir())

    body = client.post("/api/versions/d1/rebuild").get_json()
    assert body["added"] == [ids[0]]
    assert body["dropped"] == [ids[3]]
    assert sorted(p.name for p in (root / "s").iterdir()) == before_files


def test_rebuild_keeps_the_legacy_unreadable_marker(client, tmp_path):
    legacy = m._versions_path("d1")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b'{"versions": [truncated')
    ids = _seed(client, 2)
    marker = json.loads(_index_path().read_bytes()).get("legacy_unreadable")
    assert marker, "摆场景失败：索引里没有 legacy_unreadable"
    idx = json.loads(_index_path().read_bytes())
    _index_path().write_bytes(json.dumps({**idx, "entries": "broken-shape"}).encode())
    assert client.post("/api/versions/d1/rebuild").status_code == 200
    again = json.loads(_index_path().read_bytes())
    assert [e["id"] for e in again["entries"]] == ids
    assert again["legacy_unreadable"] == marker


def test_rebuild_without_a_store_is_404_and_writes_nothing(client):
    assert client.post("/api/versions/nope/rebuild").status_code == 404
    assert not versionfiles.snapshot("nope")


def test_rebuild_refuses_an_index_from_the_future_without_touching_it(client):
    _seed(client, 2)
    idx = _index_path()
    future = json.dumps({"format": 99, "entries": []}).encode()
    idx.write_bytes(future)
    before = versionfiles.snapshot("d1")
    r = client.post("/api/versions/d1/rebuild")
    assert r.status_code == 409 and r.get_json()["code"] == "versions_unreadable"
    assert versionfiles.snapshot("d1") == before


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="用 fcntl 模拟另一个持锁者")


@posix_only
def test_rebuild_holds_the_versions_lock_and_serializes_with_writers(client, monkeypatch):
    """重建在版本锁内：别的进程占着锁 → 503 且零写入；锁内的写者与重建串行（不丢那次追加）。"""
    import fcntl

    _seed(client, 3)
    _index_path().unlink()
    before = versionfiles.snapshot("d1")
    path = m._versions_path("d1")
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 0.3)
    lock_file = layoutsession.doclock_path(m._lock_stripe(path.with_suffix(".versions")))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = client.post("/api/versions/d1/rebuild")
        assert r.status_code == 503 and r.get_json()["code"] == "session_unavailable"
        assert versionfiles.snapshot("d1") == before
    finally:
        os.close(fd)

    # 同进程：重建持锁期间，另一线程的追加必须等它做完，之后两者都在
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 10.0)
    entered, release = threading.Event(), threading.Event()
    real = vs.rebuild_index

    def slow(*a, **kw):
        entered.set()
        assert release.wait(5)
        return real(*a, **kw)

    monkeypatch.setattr(vs, "rebuild_index", slow)
    out: dict = {}

    def do_rebuild():
        with m.app.test_client() as c:
            out["rebuild"] = c.post("/api/versions/d1/rebuild").status_code

    def do_append():
        with m.app.test_client() as c:
            out["append"] = c.post(
                "/api/versions/d1", json={"doc": _doc("late"), "name": "late", "auto": False}
            ).status_code

    t1 = threading.Thread(target=do_rebuild)
    t1.start()
    assert entered.wait(5)
    t2 = threading.Thread(target=do_append)
    t2.start()
    time.sleep(0.3)
    assert "append" not in out, "追加没等重建就进去了"
    release.set()
    t1.join(10)
    t2.join(10)
    assert out == {"rebuild": 200, "append": 200}
    assert len(_listing(client)) == 4


def test_rebuild_aborts_when_the_envelope_directory_cannot_be_listed(client, monkeypatch):
    """枚举失败（网络盘瞬时错误）不是「没有信封」：不写索引、旧索引逐字节不变。"""
    _seed(client, 3)
    before = versionfiles.snapshot("d1")
    real = os.listdir

    def flaky(path="."):
        if str(path).endswith(os.sep + "s"):
            raise OSError(5, "I/O error")
        return real(path)

    monkeypatch.setattr(vs.os, "listdir", flaky)
    r = client.post("/api/versions/d1/rebuild")
    assert r.status_code == 409 and r.get_json()["code"] == "versions_unreadable"
    assert versionfiles.snapshot("d1") == before


def test_rebuild_aborts_on_an_unreadable_envelope_but_skips_a_corrupt_one(client, monkeypatch):
    ids = _seed(client, 3)
    root = versionfiles.store_root("d1")
    (root / "s" / f"{ids[0]}.json").write_bytes(b"not an envelope\n")  # 内容损坏：跳过
    before = versionfiles.snapshot("d1")
    real_head = vs._read_head

    def denied(path):
        if path.name.startswith(ids[2]):
            raise PermissionError(13, "denied")
        return real_head(path)

    monkeypatch.setattr(vs, "_read_head", denied)
    r = client.post("/api/versions/d1/rebuild")
    assert r.status_code == 409 and r.get_json()["code"] == "versions_unreadable"
    assert versionfiles.snapshot("d1") == before
    monkeypatch.setattr(vs, "_read_head", real_head)  # I/O 恢复后：损坏的那条才被跳过
    body = client.post("/api/versions/d1/rebuild").get_json()
    assert body["count"] == 2 and body["dropped"] == [ids[0]]


@pytest.mark.parametrize("shape", [{"format": 99}, {"format": 99, "entries": "x"}])
def test_rebuild_treats_a_future_index_without_valid_entries_as_future_not_corrupt(client, shape):
    _seed(client, 2)
    _index_path().write_bytes(json.dumps(shape).encode())
    before = versionfiles.snapshot("d1")
    r = client.post("/api/versions/d1/rebuild")
    assert r.status_code == 409 and r.get_json()["code"] == "versions_unreadable"
    assert versionfiles.snapshot("d1") == before
    # 读路径同一判据：列表也是未来格式 409，而不是在内存里当损坏重建
    assert client.get("/api/versions/d1").status_code == 409
