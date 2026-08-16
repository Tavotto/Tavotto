"""文档自动保存（磁盘原子写）：PUT/GET 往返、非法载荷、删除。"""
import json

import pytest

from magplot import app as m


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
