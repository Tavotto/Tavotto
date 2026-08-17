"""布局版本时间线 API：创建 / 去重 / 列表 / 重命名 / 复制 / 删除 / 裁剪。"""
import pytest

from magplot import app as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / "_versions")
    # 清掉别的测试模块残留的已打开项目：版本历史现在优先写进项目的
    # magplotfile/versions/，不隔离的话状态会串到那个项目里
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _doc(n_objects=1, name="fig_layout"):
    return {
        "schema": 2, "name": name,
        "page": {"w": 150, "h": 100},
        "objects": [
            {"id": f"o{i}", "type": "text", "text": f"t{i}",
             "x": 0, "y": 0, "w": 10, "h": 5,
             "sizePt": 9, "bold": False, "color": "#000", "align": "left"}
            for i in range(n_objects)
        ],
        "guides": [],
    }


def _create(client, doc_id="d1", **kw):
    resp = client.post(f"/api/versions/{doc_id}", json={"doc": _doc(), **kw})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def test_create_and_list(client):
    _create(client, name="初稿")
    _create(client, doc=_doc(2), auto=True)
    resp = client.get("/api/versions/d1")
    versions = resp.get_json()["versions"]
    assert len(versions) == 2
    assert versions[0]["name"] == "初稿"
    assert versions[0]["objects"] == 1
    assert versions[1]["auto"] is True
    assert versions[1]["objects"] == 2
    # 完整快照可取回
    full = client.get(f"/api/versions/d1/{versions[0]['id']}").get_json()
    assert full["doc"]["schema"] == 2


def test_auto_checkpoint_dedup(client):
    _create(client, auto=True)
    second = client.post("/api/versions/d1", json={"doc": _doc(), "auto": True})
    assert second.get_json().get("skipped") is True
    assert len(client.get("/api/versions/d1").get_json()["versions"]) == 1


def test_rejects_bad_doc(client):
    resp = client.post("/api/versions/d1", json={"doc": {"schema": 1}})
    assert resp.status_code == 400


def test_accepts_schema3_snapshot(client):
    pd = {"schema": 3, "project": {"id": "p", "name": "n"},
          "canvases": [{"id": "c1", "name": "Fig 1", "page": {"w": 10, "h": 10},
                        "objects": [{"id": "o1", "type": "text"}], "guides": []}],
          "activeCanvasId": "c1", "createdAt": 0, "updatedAt": 0}
    resp = client.post("/api/versions/d1", json={"doc": pd, "name": "v3"})
    assert resp.status_code == 200
    meta = resp.get_json()["version"]
    assert meta["objects"] == 1  # schema 3 也能数出对象数


def test_rename_promote_duplicate_delete(client):
    vid = _create(client, name="a")["version"]["id"]
    # 重命名 + 描述
    resp = client.patch(f"/api/versions/d1/{vid}",
                        json={"name": "b", "description": "调整后"})
    assert resp.get_json()["version"]["name"] == "b"
    # 复制
    copy = client.post(f"/api/versions/d1/{vid}/duplicate").get_json()["version"]
    assert copy["name"] == "b 副本"
    # 删除原版
    assert client.delete(f"/api/versions/d1/{vid}").get_json()["ok"] is True
    left = client.get("/api/versions/d1").get_json()["versions"]
    assert [v["id"] for v in left] == [copy["id"]]
    # 删不存在的 → 404
    assert client.delete(f"/api/versions/d1/{vid}").status_code == 404


def test_docs_are_isolated(client):
    _create(client, doc_id="d1")
    _create(client, doc_id="d2")
    assert len(client.get("/api/versions/d1").get_json()["versions"]) == 1
    assert len(client.get("/api/versions/d2").get_json()["versions"]) == 1


def test_prune_keeps_manual_over_auto(client, monkeypatch):
    monkeypatch.setattr(m, "VERSION_KEEP_AUTO", 3)
    monkeypatch.setattr(m, "VERSION_KEEP_TOTAL", 10)
    manual = _create(client, name="手动")["version"]["id"]
    for i in range(6):
        client.post("/api/versions/d1", json={"doc": _doc(i + 2), "auto": True})
    versions = client.get("/api/versions/d1").get_json()["versions"]
    autos = [v for v in versions if v["auto"]]
    assert len(autos) == 3  # 自动检查点滚动清理
    assert any(v["id"] == manual for v in versions)  # 手动版本保留
