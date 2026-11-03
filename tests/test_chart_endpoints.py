"""bind/compute 服务端点测试（SG10 / ADR 0166）：真实子进程脚手架 + 组合。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import pool, project_watch

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "support" / "svg_corpus"

BIND_REQUEST = {
    "axes_id": "ax1",
    "rect": [51.562344, 26.16, 297.637656, 183.717656],
    "x": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 10.0, "scale": "linear"},
    "y": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 100.0, "scale": "linear"},
    "series": [{"node": "line2d_13", "x_col": "x", "y_col": "y1", "style": {"color": "#1f77b4"}}],
    "completeness": "self-contained",
    "dataset_text": "x,y1\n0,0.0\n5,25.0\n10,100.0\n",
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "chart.svg").write_bytes((CORPUS / "line_two_series.svg").read_bytes())
    m.open_project(str(root))
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(REPO / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("chart compute built a script session")
    )
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def test_bind_endpoint_returns_model_and_binding(project):
    client, _root = project
    response = client.post(
        "/api/svg/chart/bind",
        json={**BIND_REQUEST, "file": "chart.svg"},
    )
    assert response.status_code == 200
    body = response.json
    assert body["model"]["series"][0]["node"] == "line2d_13"
    assert body["binding"]["entries"] == [{"object": "series:s1", "node": "line2d_13"}]


def test_bind_endpoint_refuses_dirty_data_and_missing_file(project):
    client, root = project
    bad = client.post(
        "/api/svg/chart/bind",
        json={**BIND_REQUEST, "file": "chart.svg", "dataset_text": "x,y1\noops\n"},
    )
    assert bad.status_code == 422
    assert bad.json["code"] == "bind_evidence_invalid"
    missing = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": ""})
    assert missing.status_code == 422


def test_compute_endpoint_renders_bound_region(project):
    client, root = project
    bound = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": "chart.svg"})
    model, binding = bound.json["model"], bound.json["binding"]
    before = (root / "chart.svg").read_bytes()
    response = client.post(
        "/api/svg/chart/compute", json={"file": "chart.svg", "model": model, "binding": binding}
    )
    assert response.status_code == 200
    body = response.json
    assert "<svg" in body["svg"]
    assert body["scene_digest"] == model["scene_digest"]
    # 独立核对：重算点位置落在公式上（x=5 → 0.5 轴分数 + rect 偏移由 facts 表达）
    pts = body["facts"]["series_points"]["s1"]
    assert pts[1]["x"] == pytest.approx(0.5, abs=1e-9)
    # 原文件零改动
    assert (root / "chart.svg").read_bytes() == before


def test_compute_endpoint_refuses_changed_scene(project):
    client, root = project
    bound = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": "chart.svg"})
    model, binding = bound.json["model"], bound.json["binding"]
    (root / "chart.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'
    )
    response = client.post(
        "/api/svg/chart/compute", json={"file": "chart.svg", "model": model, "binding": binding}
    )
    assert response.status_code == 422
    assert response.json["code"] == "chart_source_mismatch"


def test_compute_endpoint_refuses_engine_mismatch(project):
    client, _root = project
    bound = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": "chart.svg"})
    model = bound.json["model"]
    model["engine"]["matplotlib"] = "9.9.9-fake"
    response = client.post(
        "/api/svg/chart/compute",
        json={"file": "chart.svg", "model": model, "binding": bound.json["binding"]},
    )
    assert response.status_code == 409
    assert response.json["code"] == "chart_engine_mismatch"
