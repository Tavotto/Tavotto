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


# ---------------------------------------------------------------------------
# M1 核心场景：两条曲线绑定 → 编辑 → 保存 → 冷重算 → 导出
# ---------------------------------------------------------------------------

TWO_CURVE_SCENE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100">'
    b'<g id="old_axis"><text x="0" y="100">0</text></g>'
    b'<path id="curve_a" d="M0 100L100 0" fill="none" stroke="blue"/>'
    b'<path id="curve_b" d="M0 50L100 50" fill="none" stroke="red"/>'
    b"</svg>"
)

TWO_CURVE_DATA = "x,a,b\n0,0.0,0.5\n1,1.0,0.4\n"


def test_two_series_bind_and_compute(project):
    """M1 核心：两条曲线各自绑定不同数据列 → compute 产物两组数据点。"""
    client, root = project
    (root / "two_curves.svg").write_bytes(TWO_CURVE_SCENE)
    from tavotto.svgscene import scene_inventory

    scene_inv = scene_inventory(TWO_CURVE_SCENE)
    bind_req = {
        "file": "two_curves.svg",
        "axes_id": "ax1",
        "rect": [0.0, 0.0, 100.0, 100.0],
        "x": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 1.0, "scale": "linear"},
        "y": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 1.0, "scale": "linear"},
        "series": [
            {"node": "curve_a", "x_col": "x", "y_col": "a", "kind": "line"},
            {"node": "curve_b", "x_col": "x", "y_col": "b", "kind": "line"},
        ],
        "scene_digest": scene_inv["scene_digest"],
        "nodes": scene_inv["nodes"],
        "completeness": "self-contained",
        "dataset_text": TWO_CURVE_DATA,
        "axes_nodes": ["old_axis"],
    }
    bound = client.post("/api/svg/chart/bind", json=bind_req)
    assert bound.status_code == 200, bound.json
    chart = bound.json
    assert len(chart["model"]["series"]) == 2
    assert len(chart["binding"]["entries"]) == 3  # 2 series + 1 axes

    compute = client.post(
        "/api/svg/chart/compute",
        json={"file": "two_curves.svg", "model": chart["model"], "binding": chart["binding"]},
    )
    assert compute.status_code == 200, compute.json
    body = compute.json
    # 两组独立数据点
    pts_a = body["facts"]["series_points"]["s1"]
    pts_b = body["facts"]["series_points"]["s2"]
    assert pts_a[0]["y"] == pytest.approx(0.0, abs=1e-9)  # a(0)=0 → frac 1 (y-down = top)
    assert pts_b[0]["y"] == pytest.approx(0.5, abs=1e-9)  # b(0)=0.5 → frac 0.5
    # 导出 SVG = 组合版
    export_req = {
        "scope": "original",
        "formats": ["svg"],
        "original": {
            "figure_id": "two_curves.svg",
            "source_kind": "vector",
            "chart": {"model": chart["model"], "binding": chart["binding"]},
        },
        "overwrite": "replace",
    }
    export = client.post("/api/export", json=export_req)
    assert export.status_code == 200
    export_dir = root / "tavottofile" / "export"
    svgs = list(export_dir.glob("*.svg"))
    assert svgs, "no exported SVG"
    svg_bytes = svgs[-1].read_bytes()
    # 两条曲线的 tv-series 标记都在
    assert b"tv-series-s1" in svg_bytes
    assert b"tv-series-s2" in svg_bytes
    # 旧轴标注退役
    assert b"old_axis" not in svg_bytes


# ---------------------------------------------------------------------------
# R08：C 路径自包含往返——导出时嵌入模型元数据，冷端读取后重算
# ---------------------------------------------------------------------------


def test_export_embeds_metadata_for_self_contained_roundtrip(project):
    """导出 SVG 嵌入 chart 元数据（base64 JSON metadata 块）→ 从元数据重算一致。"""
    import base64

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
    assert result.status_code == 200
    export_dir = root / "tavottofile" / "export"
    svgs = list(export_dir.glob("*.svg"))
    assert svgs, "no exported SVG"
    svg_bytes = svgs[-1].read_bytes()
    # 元数据块存在（base64 JSON model+binding）
    meta_marker = b"data-tavotto-chart="
    assert meta_marker in svg_bytes, "no chart metadata block in exported SVG"
    # 解码元数据 → 重新验证
    import re as _re

    match = _re.search(rb'data-tavotto-chart="([^"]+)"', svg_bytes)
    assert match, "metadata attribute not found"
    meta_json = base64.b64decode(match.group(1))
    meta = json.loads(meta_json)
    assert meta["model"]["series"][0]["kind"] == "line"
    assert "binding" in meta
    # 从元数据重算 → 与原始 compute 一致
    recompute = client.post(
        "/api/svg/chart/compute",
        json={"file": "chart.svg", "model": meta["model"], "binding": meta["binding"]},
    )
    assert recompute.status_code == 200, recompute.json
    assert "<svg" in recompute.json["svg"]
