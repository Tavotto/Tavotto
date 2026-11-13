"""自包含 SVG 的真实 inspect/bind 恢复入口，不以元数据 hash 代替重放。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import app as m, chartbundle
from tavotto.engine import pool, project_watch

SOURCE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100"><path id="curve" d="M0 100L100 0" '
    b'fill="none" stroke="blue"/></svg>'
)
BIND = {
    "axes_id": "ax1",
    "rect": [0, 0, 100, 100],
    "x": {"p1_user": 0, "p1_value": 0, "p2_user": 1, "p2_value": 10, "scale": "linear"},
    "y": {"p1_user": 0, "p1_value": 0, "p2_user": 1, "p2_value": 10, "scale": "linear"},
    "series": [{"node": "curve", "x_col": "x", "y_col": "y", "kind": "line"}],
    "completeness": "self-contained",
    "dataset_text": "x,y\n0,0\n5,3\n10,10\n",
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "original.svg").write_bytes(SOURCE)
    m.open_project(str(root))
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", pool.find_worker_python())
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("restoration ran a user script"))
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def exported_bundle(client, root: Path) -> tuple[dict, bytes]:
    bound = client.post("/api/svg/chart/bind", json={"file": "original.svg", **BIND})
    assert bound.status_code == 200, bound.json
    chart = bound.json
    result = client.post(
        "/api/export",
        json={
            "scope": "original",
            "formats": ["svg"],
            "original": {
                "figure_id": "original.svg",
                "source_kind": "vector",
                "chart": chart,
            },
            "overwrite": "replace",
        },
    )
    assert result.status_code == 200, result.json
    return chart, next((root / "tavottofile" / "export").glob("*.svg")).read_bytes()


def test_inspect_plain_source_preserves_contract_without_computation(project, monkeypatch):
    client, _root = project
    monkeypatch.setattr(
        m, "_chart_scaffold_via_worker", lambda *a, **k: pytest.fail("ordinary SVG executed")
    )
    result = client.get("/api/svg/chart/inspect?id=original.svg")
    assert result.status_code == 200, result.json
    assert "restored_chart" not in result.json
    assert any(node["id"] == "curve" for node in result.json["nodes"])


def test_inspect_exported_bundle_restores_original_nodes_then_rebinds(project):
    client, root = project
    chart, exported = exported_bundle(client, root)
    (root / "portable.svg").write_bytes(exported)
    (root / "original.svg").unlink()
    result = client.get("/api/svg/chart/inspect?id=portable.svg")
    assert result.status_code == 200, result.json
    inventory = result.json
    assert inventory["restored_chart"] == chart
    assert inventory["scene_digest"] == chart["model"]["scene_digest"]
    assert any(node["id"] == "curve" for node in inventory["nodes"])
    rebound = client.post(
        "/api/svg/chart/bind",
        json={"file": "portable.svg", **BIND, "dataset_text": "x,y\n0,2\n5,8\n10,4\n"},
    )
    assert rebound.status_code == 200, rebound.json
    assert rebound.json["model"]["dataset"]["columns"]["y"] == [2.0, 8.0, 4.0]
    computed = client.post("/api/svg/chart/compute", json={"file": "portable.svg", **rebound.json})
    assert computed.status_code == 200, computed.json
    assert computed.json["facts"]["series_points"]["s1"][1]["y"] == pytest.approx(0.8)
    assert (root / "portable.svg").read_bytes() == exported


@pytest.mark.parametrize("endpoint", ["inspect", "bind"])
def test_restoration_rejects_resigned_but_unreplayable_visible_content(project, endpoint):
    client, root = project
    chart, _exported = exported_bundle(client, root)
    fake_visible = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt">'
        b'<circle cx="50" cy="50" r="20"/></svg>'
    )
    tampered = chartbundle.embed_chart(fake_visible, SOURCE, chart)
    (root / "tampered.svg").write_bytes(tampered)
    if endpoint == "inspect":
        result = client.get("/api/svg/chart/inspect?id=tampered.svg")
    else:
        result = client.post("/api/svg/chart/bind", json={"file": "tampered.svg", **BIND})
    assert result.status_code == 422, result.json
    assert result.json["code"] == "chart_source_mismatch"
    assert "computed SVG differs" in result.json["error"]
    assert (root / "tampered.svg").read_bytes() == tampered
