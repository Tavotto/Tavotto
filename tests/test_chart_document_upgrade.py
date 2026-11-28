"""原地升级的后端边界（#1920 合同 / ADR 0182 §3）。

这里覆盖后端能强制、也能验证的部分：同一 documentId / 文件 / 槽位 / 时间线；正文
提交前来源附件必须先落定（冻结失败时已存在的文件零改动）；升级前的 schema 3 版本
节点与升级后的 schema 4 节点在同一条时间线上都读得回来。

**不覆盖**前端「先 `takeProjectCheckpoint()` 再第一次把图表写进正文」的接线——那条
入口属于 7/9，本层只有原语（`web/src/lib/timelineCheckpoint.ts`）；ADR 0182 §3 与
前后端规则文档已如实标注接线状态，别把本文件读成那条行为的看护。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tavotto import app as m
from tavotto.engine import documentassets, documents, exportreq as engine_exportreq
from test_chart_document_assets import (
    SCENE_DIGEST,
    SOURCE,
    SOURCE_ID,
    chart as chart_state,
    reference as chart_reference,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.reset_projects()
    monkeypatch.setattr(m, "LAYOUT_DIR", tmp_path)
    monkeypatch.setattr(m, "AUTOSAVE_DIR", tmp_path / documents.AUTOSAVE_DIRNAME)
    monkeypatch.setattr(m, "VERSIONS_DIR", tmp_path / documents.VERSIONS_DIRNAME)
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "userdata"))
    m.app.config["TESTING"] = True
    yield m.app.test_client()
    m.reset_projects()


def _schema3_document():
    return {
        "schema": 3,
        "project": {"id": "p", "name": "升级中的项目"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 150, "h": 100},
                "objects": [
                    {
                        "id": "o1",
                        "type": "text",
                        "text": "升级前",
                        "x": 0.0,
                        "y": 0.0,
                        "w": 20.0,
                        "h": 8.0,
                        "sizePt": 9,
                        "bold": False,
                        "color": "#000",
                        "align": "left",
                    }
                ],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 1,
    }


def _schema4_document():
    doc = _schema3_document()
    doc["schema"] = documents.SCHEMA_CHART_PROJECT
    doc["updatedAt"] = 2
    doc["canvases"][0]["objects"].append(
        {
            "id": "o2",
            "type": "panel",
            "fileId": SOURCE_ID,
            "fileKind": "svg",
            "x": 0.0,
            "y": 10.0,
            "w": 40.0,
            "h": 30.0,
            "chart": chart_state(),
            "chartSource": chart_reference(),
        }
    )
    return doc


def _install_source(tmp_path):
    return documentassets.freeze_source(tmp_path, SOURCE_ID, SOURCE, scene_digest=SCENE_DIGEST)


def test_source_freeze_failure_keeps_the_existing_body_untouched(client, tmp_path, monkeypatch):
    """快照失败不升级：正文在来源落定之后才提交，失败时磁盘上的旧稿一个字节都不动。"""
    assert client.post("/api/layouts/升级", json=_schema3_document()).status_code == 200
    path = tmp_path / "升级.json"
    before = path.read_bytes()

    def boom(*args, **kwargs):
        raise engine_exportreq.ExportRequestError("chart_source_changed", "source changed", {})

    monkeypatch.setattr(m, "_render_chart_snapshot", boom)
    candidate = _schema4_document()
    candidate["canvases"][0]["objects"][-1].pop("chartSource")
    resp = client.post("/api/layouts/升级", json=candidate)
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "chart_source_changed"
    assert path.read_bytes() == before
    assert not (tmp_path / "chart_sources").exists()


def test_upgrade_keeps_one_named_file_and_one_timeline(client, tmp_path):
    """升级不改身份：同一个命名文件、同一条版本时间线；升级前的 schema 3 节点
    与升级后的 schema 4 节点都在，且都能取回整份项目正文。"""
    _install_source(tmp_path)
    three = _schema3_document()
    assert client.post("/api/layouts/升级", json=three).status_code == 200
    first = client.post("/api/versions/d_up", json={"doc": three, "name": "升级前"})
    assert first.status_code == 200, first.get_json()

    four = _schema4_document()
    upgraded = client.post("/api/layouts/升级", json=four)
    assert upgraded.status_code == 200, upgraded.get_json()
    second = client.post("/api/versions/d_up", json={"doc": four, "name": "升级后"})
    assert second.status_code == 200, second.get_json()

    versions = client.get("/api/versions/d_up").get_json()["versions"]
    assert [v["name"] for v in versions] == ["升级前", "升级后"]
    assert [v["schema"] for v in versions] == [3, 4]
    for version in versions:
        full = client.get(f"/api/versions/d_up/{version['id']}").get_json()
        assert len(full["doc"]["canvases"]) == 1
        assert full["doc"]["project"]["name"] == "升级中的项目"

    # 同一个命名文件只有一份；读回来是升级后的正文与来源引用
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["升级.json"]
    got = client.get("/api/layouts/升级").get_json()
    assert got["schema"] == 4
    assert got["canvases"][0]["objects"][-1]["chartSource"] == chart_reference()
    assert (tmp_path / "chart_sources" / f"{chart_reference()['bytesSha256']}.svg").is_file()


def test_upgrade_reuses_the_autosave_slot(client, tmp_path):
    _install_source(tmp_path)
    assert client.put("/api/autosave/d_up", json=_schema3_document()).status_code == 200
    upgraded = client.put("/api/autosave/d_up", json=_schema4_document())
    assert upgraded.status_code == 200, upgraded.get_json()
    assert sorted(p.name for p in (tmp_path / documents.AUTOSAVE_DIRNAME).glob("*.json")) == [
        "d_up.json"
    ]
    assert client.get("/api/autosave/d_up").get_json()["schema"] == 4


def test_deleted_attachment_is_not_a_recoverable_slot(client, tmp_path):
    ref = _install_source(tmp_path)
    assert client.put("/api/autosave/d_up", json=_schema4_document()).status_code == 200
    documentassets.source_path(tmp_path, ref).unlink()
    response = client.get("/api/autosave/d_up")
    assert response.status_code == 422
    assert response.get_json()["code"] == "chart_source_mismatch"
    rows = client.get("/api/layout-session/candidates?scope=project").get_json()["items"]
    assert next(r for r in rows if r["doc_id"] == "d_up")["status"] == "chart_source_mismatch"
    assert not m._slot_recoverable(tmp_path / documents.AUTOSAVE_DIRNAME / "d_up.json")[0]
    result = client.put("/api/layout-session/last", json={"doc_id": "d_up", "name": "test"})
    assert result.status_code == 200, result.get_json()
    assert result.get_json()["state"] == "pending"


def test_slot_attachment_is_checked_in_its_owner_store_after_switch(client, tmp_path):
    ref = _install_source(tmp_path)
    assert client.put("/api/autosave/d_up", json=_schema4_document()).status_code == 200
    other = tmp_path / "other-project"
    other.mkdir()
    m.open_project(str(other))
    assert client.get("/api/autosave/d_up").status_code == 200
    documentassets.freeze_source(
        m.project_layout_dir(), SOURCE_ID, SOURCE, scene_digest=SCENE_DIGEST
    )
    documentassets.source_path(tmp_path, ref).unlink()
    # 另一个项目中同摘要的附件不能掩盖所属存储缺失。
    assert client.get("/api/autosave/d_up").status_code == 422


def test_local_named_attachment_is_read_locally_with_a_project_open(client, tmp_path):
    _install_source(tmp_path)
    assert client.post("/api/layouts/local_chart", json=_schema4_document()).status_code == 200
    project = tmp_path / "project"
    project.mkdir()
    m.open_project(str(project))
    response = client.get("/api/layouts/local_chart")
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["schema"] == 4


def test_stale_owner_revision_does_not_select_old_project_attachments(
    client, tmp_path, monkeypatch
):
    def render(file_id, chart, **kwargs):
        ref = chart_reference()
        return SimpleNamespace(
            input_svg=SOURCE,
            scene={"scene_digest": SCENE_DIGEST},
            path=documentassets.source_path(m.project_layout_dir(), ref),
            source_sha256=ref["bytesSha256"],
        )

    monkeypatch.setattr(m, "_render_chart_snapshot", render)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    m.open_project(str(project_a))
    _install_source(m.project_layout_dir())
    assert client.put("/api/autosave/d_stale_owner", json=_schema4_document()).status_code == 200
    m.open_project(str(project_b))
    ref = _install_source(m.project_layout_dir())
    newer = _schema4_document()
    newer["updatedAt"] += 1
    with monkeypatch.context() as failed:
        failed.setattr(
            m.engine_layoutsession,
            "commit_slot",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("metadata failed")),
        )
        saved = client.put("/api/autosave/d_stale_owner", json=newer)
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()["metadata"] == "failed"
    assert client.get("/api/autosave/d_stale_owner").status_code == 200
    candidates = client.get("/api/layout-session/candidates?scope=unknown").get_json()["items"]
    assert next(c for c in candidates if c["doc_id"] == "d_stale_owner")["status"] == "ok"
    documentassets.source_path(m.project_layout_dir(), ref).unlink()
    # A 中仍然完整的相同附件不能代替 B 槽位需要的附件。
    assert client.get("/api/autosave/d_stale_owner").status_code == 422


@pytest.mark.parametrize("endpoint", ["named", "autosave", "checkpoint"])
def test_local_only_save_checks_actual_bound_nodes(client, tmp_path, endpoint):
    _install_source(tmp_path)
    doc = _schema4_document()
    doc["canvases"][0]["objects"][-1]["chart"]["binding"]["entries"][0]["node"] = "absent-node"
    if endpoint == "named":
        path = tmp_path / "bound.json"
        assert client.post("/api/layouts/bound", json=_schema3_document()).status_code == 200
        before = path.read_bytes()
        response = client.post("/api/layouts/bound", json=doc)
    elif endpoint == "autosave":
        assert client.put("/api/autosave/d_bound", json=_schema3_document()).status_code == 200
        path = tmp_path / documents.AUTOSAVE_DIRNAME / "d_bound.json"
        before = path.read_bytes()
        response = client.put("/api/autosave/d_bound", json=doc)
    else:
        response = client.post("/api/versions/d_bound", json={"doc": doc, "name": "bad binding"})
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "chart_node_missing"
    if endpoint != "checkpoint":
        assert path.read_bytes() == before
    else:
        assert client.get("/api/versions/d_bound").get_json()["versions"] == []
