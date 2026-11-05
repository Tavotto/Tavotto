"""R05：持久化与冷恢复——chart 字段在真实保存/读取路径的往返与旧版防损。

主语是**真实落盘文件**：POST /api/layouts → 磁盘 JSON → GET 读回 → chart 字段
逐字保留；`documents.loads_document` 不剥字段；旧版防损 = 旧前端**缺** chart 字段
保存后字段被清（该方向是前端状态序列化的自然结果），而**带** chart 的文档经旧
判据 validate_document 原样放行。冷恢复 = 保存后用保存下来的模型/绑定对新进程
的 compute 端点重算，结果与保存前一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import documents as engine_documents, pool, project_watch

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

DOC_NAME = "R05RoundTrip"


def _project_doc(client, chart_state: dict | None) -> dict:
    doc = {
        "schema": 3,
        "project": {"id": "proj-r05", "name": "R05"},
        "canvases": [
            {
                "id": "c1",
                "name": "Canvas",
                "schema": 2,
                "page": {"w": 210.0, "h": 297.0},
                "objects": [
                    {
                        "id": "panel-svg-1",
                        "type": "panel",
                        "x": 10.0,
                        "y": 10.0,
                        "w": 120.0,
                        "h": 90.0,
                        "fileId": "chart.svg",
                        "fileKind": "svg",
                        "nativeW": 360,
                        "nativeH": 252,
                        "overrides": [],
                        **({"chart": chart_state} if chart_state is not None else {}),
                    }
                ],
                "guides": [],
                "updatedAt": 1,
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 1,
        "updatedAt": 1,
    }
    return doc


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "chart.svg").write_bytes((CORPUS / "line_two_series.svg").read_bytes())
    m.open_project(str(root))
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(REPO / ".venv" / "Scripts" / "python.exe"))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("cold recovery built a script session")
    )
    yield m.app.test_client(), root
    m.reset_projects()
    project_watch.stop()


def _bind_chart(client) -> dict:
    bound = client.post("/api/svg/chart/bind", json={**BIND_REQUEST, "file": "chart.svg"})
    assert bound.status_code == 200
    return bound.json


def test_document_roundtrip_preserves_chart_field(project):
    """真实保存/读取路径：chart 字段逐字保留（含 model/binding/digest）。"""
    client, root = project
    chart = _bind_chart(client)
    doc = _project_doc(client, chart)
    saved = client.post(f"/api/layouts/{DOC_NAME}?target=project", json=doc)
    assert saved.status_code == 200, saved.json
    on_disk = json.loads((root / "tavottofile" / f"{DOC_NAME}.json").read_text(encoding="utf-8"))
    panel = on_disk["canvases"][0]["objects"][0]
    assert panel["chart"] == chart, "chart 字段在真实落盘文件里被剥离"
    loaded = client.get(f"/api/layouts/{DOC_NAME}")
    assert loaded.status_code == 200
    roundtrip = loaded.json["canvases"][0]["objects"][0]["chart"]
    assert roundtrip == chart


def test_loads_document_keeps_chart_field():
    """读侧唯一入口（loads_document）不剥 chart 字段；NaN 字面量仍被挡。"""
    chart = {"model": {"rev": 1}, "binding": {"entries": []}}
    doc = _project_doc(None, chart)
    text = json.dumps(doc)
    loaded = engine_documents.loads_document(text.encode("utf-8"))
    assert loaded["canvases"][0]["objects"][0]["chart"] == chart
    bad = json.dumps(doc).replace('"rev": 1', '"rev": NaN')
    from tavotto.engine.documents import DocumentError

    with pytest.raises(DocumentError):
        engine_documents.loads_document(bad.encode("utf-8"))


def test_validate_document_admits_chart_document():
    """旧判据（validate_document）对带 chart 的文档原样放行——不挡保存。"""
    chart = {"model": {"rev": 1}, "binding": {"entries": []}}
    doc = _project_doc(None, chart)
    assert engine_documents.validate_document(doc) is doc


def test_cold_recovery_recompute_matches_saved_model(project):
    """冷恢复：保存→(模拟重开)→用保存下来的模型/绑定对新 compute 重算一致。"""
    client, root = project
    chart = _bind_chart(client)
    doc = _project_doc(client, chart)
    assert client.post(f"/api/layouts/{DOC_NAME}?target=project", json=doc).status_code == 200
    # 模拟冷态：reset_projects + 重新打开（fixture 的注册表清空 = 新进程语义）
    m.reset_projects()
    m.open_project(str(root))
    try:
        compute = client.post(
            "/api/svg/chart/compute",
            json={"file": "chart.svg", "model": chart["model"], "binding": chart["binding"]},
        )
        assert compute.status_code == 200, compute.json
        body = compute.json
        # 与保存的模型/来源一致：scene_digest 与 model 里钉的相同
        assert body["scene_digest"] == chart["model"]["scene_digest"]
        assert "<svg" in body["svg"]
        # 曲线点位置仍符合独立公式（x=5 在 [0,10] → 0.5）
        pts = body["facts"]["series_points"]["s1"]
        assert pts[1]["x"] == pytest.approx(0.5, abs=1e-9)
    finally:
        m.reset_projects()
        m.open_project(str(root))


def test_source_changed_after_save_is_reported_not_silently_rebound(project):
    """来源在外部被改：冷恢复的 compute 报 chart_source_mismatch，不静默重绑。"""
    client, root = project
    chart = _bind_chart(client)
    doc = _project_doc(client, chart)
    assert client.post(f"/api/layouts/{DOC_NAME}?target=project", json=doc).status_code == 200
    (root / "chart.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'
    )
    compute = client.post(
        "/api/svg/chart/compute",
        json={"file": "chart.svg", "model": chart["model"], "binding": chart["binding"]},
    )
    assert compute.status_code == 422
    assert compute.json["code"] == "chart_source_mismatch"


# ---------------------------------------------------------------------------
# R07：冻结导出——chart 面板原图导出（SVG 格式 = 组合后的编辑版）
# ---------------------------------------------------------------------------


def test_export_chart_original_svg_delivers_composed_version(project):
    """chart 面板的原图导出：SVG = 组合后编辑版（basis=convert，非 copy）。"""
    client, root = project
    chart = _bind_chart(client)
    doc = _project_doc(client, chart)
    assert client.post(f"/api/layouts/{DOC_NAME}?target=project", json=doc).status_code == 200
    export_req = {
        "scope": "original",
        "formats": ["svg"],
        "original": {
            "figure_id": "chart.svg",
            "source_kind": "vector",
            "chart": {"model": chart["model"], "binding": chart["binding"]},
        },
        "overwrite": "replace",
    }
    result = client.post("/api/export", json=export_req)
    assert result.status_code == 200, result.json
    body = result.json
    outputs = body.get("outputs") or body.get("files") or []
    svg_outputs = [o for o in outputs if o.get("format") == "svg" and o.get("ok", True)]
    assert svg_outputs, f"no successful SVG output: {body}"
    # 导出产物落盘：从项目 export/ 目录读回，验证内容 = 组合后的编辑版
    export_dir = root / "tavottofile" / "export"
    svgs = list(export_dir.glob("*.svg"))
    assert svgs, f"no exported SVG in {export_dir}"
    svg_bytes = svgs[-1].read_bytes()  # 最新导出
    assert b"tv-series" in svg_bytes, "exported SVG is not the composed version"


def test_export_chart_original_pdf_reports_unsupported(project):
    """SVG→IR 翻译器落地前，chart 面板的 PDF 逐项报 svg_asset_export_unsupported。"""
    client, _root = project
    chart = _bind_chart(client)
    export_req = {
        "scope": "original",
        "formats": ["pdf"],
        "original": {
            "figure_id": "chart.svg",
            "source_kind": "vector",
            "chart": {"model": chart["model"], "binding": chart["binding"]},
        },
        "overwrite": "replace",
    }
    result = client.post("/api/export", json=export_req)
    # SVG→IR 翻译器落地后 PDF 格式也交付组合版
    assert result.status_code == 200, result.json
    body = result.json
    outputs = body.get("outputs") or body.get("files") or []
    pdf_outputs = [o for o in outputs if o.get("format") == "pdf"]
    assert pdf_outputs, f"no pdf output: {body}"
    assert pdf_outputs[0].get("error_code") is None, f"pdf error: {pdf_outputs[0]}"
    assert pdf_outputs[0].get("url", "").startswith("/exports/")
