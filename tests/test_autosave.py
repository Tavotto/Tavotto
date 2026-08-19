"""文档自动保存（磁盘原子写）：PUT/GET 往返、非法载荷、删除、跨标签页乐观并发。"""
import json

import pytest

from tavotto import app as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path)
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / "_autosave")
    m.app.config["TESTING"] = True
    return m.app.test_client()


PD = {"schema": 3, "project": {"id": "p", "name": "n"},
      "canvases": [{"id": "c1", "name": "Fig 1", "page": {"w": 10, "h": 10},
                    "objects": [], "guides": []}],
      "activeCanvasId": "c1", "createdAt": 0, "updatedAt": 1}


def test_put_get_roundtrip(client, tmp_path):
    resp = client.put("/api/autosave/d_1", json=PD)
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    body = client.get("/api/autosave/d_1").get_json()
    assert body == PD
    # 原子写：没有 .tmp 残留
    assert not list((tmp_path / "_autosave").glob("*.tmp"))


def test_get_missing_404(client):
    assert client.get("/api/autosave/nope").status_code == 404


def test_put_rejects_bad_schema(client):
    assert client.put("/api/autosave/d_1", json={"schema": 1}).status_code == 400
    assert client.put("/api/autosave/d_1", json=[1, 2]).status_code == 400


def test_doc_id_sanitized(client, tmp_path):
    client.put("/api/autosave/..%2Fevil", json=PD)
    # 槽位文件都落在 _autosave 里，没有路径逃逸
    files = list((tmp_path / "_autosave").glob("*.json"))
    assert all(f.parent == tmp_path / "_autosave" for f in files)


def test_delete_slot(client, tmp_path):
    client.put("/api/autosave/d_2", json=PD)
    assert client.delete("/api/autosave/d_2").get_json()["ok"] is True
    assert client.get("/api/autosave/d_2").status_code == 404
    # 幂等
    assert client.delete("/api/autosave/d_2").get_json()["ok"] is True


# ------------------- 乐观并发：同一文档被两个标签页同时开着 -------------------
# base = 该标签页最后一次成功落盘时的 updatedAt。磁盘上比它新 = 另一个标签页
# 在这中间存过，此时整份覆盖就是静默丢数据。


def doc(updated_at):
    return {**PD, "updatedAt": updated_at}


def test_stale_base_rejected_and_disk_untouched(client):
    """A 存了 100，B 拿着 100 的基线存 → 200；A 再拿旧基线存 → 409，磁盘不动。"""
    assert client.put("/api/autosave/d_c", json=doc(100)).status_code == 200
    # 另一个标签页（基线 100，磁盘也是 100）写入一份更新的
    assert client.put("/api/autosave/d_c?base=100", json=doc(200)).status_code == 200
    # 本标签页还拿着 100 的基线 → 磁盘上的 200 更新，拒绝覆盖
    resp = client.put("/api/autosave/d_c?base=100", json=doc(150))
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "stale_write" and body["theirs"] == 200
    # 磁盘上仍是对方那一份，没被半路覆盖
    assert client.get("/api/autosave/d_c").get_json()["updatedAt"] == 200


def test_fresh_base_accepted_and_advances(client):
    """基线跟上磁盘就放行；推进基线后可以接着写。"""
    assert client.put("/api/autosave/d_d", json=doc(100)).status_code == 200
    assert client.put("/api/autosave/d_d?base=100", json=doc(200)).status_code == 200
    assert client.put("/api/autosave/d_d?base=200", json=doc(300)).status_code == 200
    assert client.get("/api/autosave/d_d").get_json()["updatedAt"] == 300


def test_same_updated_at_is_not_a_conflict(client):
    """相等不算冲突：同一标签页同一毫秒内连写两次不该被自己挡住。"""
    client.put("/api/autosave/d_e", json=doc(100))
    assert client.put("/api/autosave/d_e?base=100", json=doc(100)).status_code == 200


def test_no_base_never_checked(client):
    """不带基线 = 旧路径 / 首次写：一律照常覆盖，兼容不破。"""
    client.put("/api/autosave/d_f", json=doc(500))
    assert client.put("/api/autosave/d_f", json=doc(1)).status_code == 200
    assert client.get("/api/autosave/d_f").get_json()["updatedAt"] == 1


def test_base_without_existing_file_writes(client):
    """磁盘上还没有这一份：带基线也照写（没有别人的东西可覆盖）。"""
    assert client.put("/api/autosave/d_g?base=100", json=doc(200)).status_code == 200
    assert client.get("/api/autosave/d_g").get_json()["updatedAt"] == 200


def test_unreadable_or_baseless_disk_copy_writes(client, tmp_path):
    """磁盘那份坏了 / 没有 updatedAt：放行——自动保存不能被一个坏槽位卡死。"""
    slots = tmp_path / "_autosave"
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "d_h.json").write_text("{ 这不是 JSON", encoding="utf-8")
    assert client.put("/api/autosave/d_h?base=100", json=doc(200)).status_code == 200

    (slots / "d_i.json").write_text(json.dumps({"schema": 3}), encoding="utf-8")
    assert client.put("/api/autosave/d_i?base=100", json=doc(200)).status_code == 200


def test_garbage_base_ignored(client):
    """基线参数被人乱填：当作没带，不 500 也不误判成冲突。"""
    client.put("/api/autosave/d_j", json=doc(500))
    assert client.put("/api/autosave/d_j?base=abc", json=doc(1)).status_code == 200
