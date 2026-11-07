"""计算 SVG 的真实 HTTP 导出闭环：独立读取最终 SVG/PDF，而非只信计算 facts。

绑定、计算与导出均使用真实 worker 和 RenderCore；只在 inspect 完成后注入外部
改源，验证发布守卫。测试不制作 FrozenSource，不桩计算或产物。
"""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import exportjob, pool, project_watch

PT_MM = 25.4 / 72
SVG_NS = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"[-+]?(?:\d*\.)?\d+(?:e[-+]?\d+)?", re.I)
SCENE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" '
    b'viewBox="0 0 100 100">'
    b'<rect width="100" height="100" fill="white"/>'
    b'<path id="curve" d="M10 90L90 10" fill="none" stroke="#888888"/>'
    b'<text x="3" y="98" font-size="6" font-family="Liberation Sans">Source keep</text>'
    b"</svg>"
)
GRADIENT = (
    b'<defs><linearGradient id="decoration"><stop offset="0" stop-color="red"/>'
    b'<stop offset="1" stop-color="green"/></linearGradient></defs>'
    b'<rect x="1" y="1" width="7" height="7" fill="url(#decoration)"/>'
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto.rendercore import fonts, renderhost

    pytest.importorskip("pikepdf", reason="RenderCore PDF 依赖未安装（not-run）")
    pdfium = pytest.importorskip("pypdfium2", reason="独立 PDF 读取器未安装（not-run）")
    registry = fonts.FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"批准字体不全（not-run）：{registry.missing}；先运行 scripts/fetch_fonts.py")
    try:
        worker_python = pool.find_worker_python()
    except pool.WorkerError as exc:
        pytest.skip(f"真实计算 worker 不可用（not-run）：{exc}")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_python)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setitem(m.app.config, "TESTING", True)
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "cache")
    m.reset_projects()
    root = tmp_path / "project"
    root.mkdir()
    (root / "chart.svg").write_bytes(SCENE)
    exportjob.reset_for_tests()
    m.open_project(str(root))
    try:
        yield m.app.test_client(), root, pdfium
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects()
        project_watch.stop()
        exportjob.reset_for_tests()
        renderhost.shutdown_shared()


def _bind(client, y):
    evidence = {
        "p1_user": 0.0,
        "p1_value": 0.0,
        "p2_user": 1.0,
        "p2_value": 1.0,
        "scale": "linear",
    }
    response = client.post(
        "/api/svg/chart/bind",
        json={
            "file": "chart.svg",
            "axes_id": "ax1",
            "rect": [10.0, 10.0, 80.0, 80.0],
            "x": evidence,
            "y": evidence,
            "series": [
                {
                    "node": "curve",
                    "x_col": "x",
                    "y_col": "y",
                    "kind": "line",
                    "style": {"color": "#0000ff", "linewidth": 3.0},
                }
            ],
            "completeness": "self-contained",
            "dataset_text": f"x,y\n0,{y}\n1,{y}\n",
        },
    )
    assert response.status_code == 200, response.get_json()
    state = response.get_json()
    chart = {"model": state["model"], "binding": state["binding"]}
    computed = client.post("/api/svg/chart/compute", json={"file": "chart.svg", **chart})
    assert computed.status_code == 200, computed.get_json()
    # 独立公式：rect 的 y = 10 + (1-y_data)*80；不能拿 scaffold facts 反推。
    assert _line_geometries(computed.get_json()["svg"].encode()) == [
        pytest.approx((10.0, 90.0 - 80.0 * y, 90.0, 90.0 - 80.0 * y), abs=1e-6)
    ]
    return chart


def _original(chart, **changes):
    return {
        "figure_id": "chart.svg",
        "source_kind": "vector",
        "chart": chart,
        **changes,
    }


def _panel(chart, *, x=0.0, y=0.0):
    return {
        "type": "panel",
        "id": "chart.svg",
        "chart": chart,
        "x_mm": x * PT_MM,
        "y_mm": y * PT_MM,
        "w_mm": 100 * PT_MM,
        "h_mm": 100 * PT_MM,
    }


def _spec(
    *, original=None, canvas=None, merge_pdf=None, filename="computed", formats=("svg", "pdf")
):
    return {
        "scope": "canvas" if canvas is not None else "original",
        "filename": filename,
        "formats": list(formats),
        "overwrite": "replace",
        **({"original": original} if original is not None else {}),
        **({"canvas": canvas} if canvas is not None else {}),
        **({"merge_pdf": merge_pdf} if merge_pdf is not None else {}),
    }


def _export(client, spec):
    response = client.post("/api/export", json=spec)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    outputs = {item["format"]: item for item in body["outputs"]}
    assert set(outputs) == set(spec["formats"]), body
    return body, outputs


def _path(body, item):
    assert item["status"] == "done", body
    path = Path(body["export_dir"]) / item["name"]
    assert path.is_file(), body
    return path


def _line_geometries(raw):
    root = ET.fromstring(raw)
    groups = [el for el in root.iter() if el.get("data-tavotto") == "series:s1"]
    assert groups, "最终 SVG 缺少计算系列，而不是一份成功的旧原图"
    points = []
    for group in groups:
        paths = list(group.iter(f"{SVG_NS}path"))
        assert len(paths) == 1, "本夹具的一条无 marker 直线应恰有一条路径"
        points.append(tuple(float(n) for n in NUMBER.findall(paths[0].get("d", ""))))
    return points


def _pdf_page(pdfium, path, index=0, *, scale=2):
    doc = pdfium.PdfDocument(str(path))
    page = None
    textpage = None
    bitmap = None
    try:
        page = doc[index]
        textpage = page.get_textpage()
        size = page.get_size()
        text = textpage.get_text_range()
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        return len(doc), size, text, image
    finally:
        if bitmap is not None:
            bitmap.close()
        if textpage is not None:
            textpage.close()
        if page is not None:
            page.close()
        doc.close()


def _blue_at(image, x, y, *, scale=2, max_red_green=80):
    # PDFium 位图为顶原点；取邻域避免浮点尺寸经像素取整造成半像素差。
    pixels = [
        image.getpixel((round(scale * x) + dx, round(scale * y) + dy))
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    ]
    return any(b > 160 and r < max_red_green and g < max_red_green for r, g, b in pixels)


def _assert_pdf(pdfium, path, *, y, size=(100, 100), offset=(0, 0), index=0):
    pages, observed_size, text, image = _pdf_page(pdfium, path, index)
    assert observed_size == pytest.approx(size, abs=0.02)
    assert "Source keep" in text
    assert _blue_at(image, 50 + offset[0], y + offset[1]), "计算曲线未画在独立公式指定的位置"
    other_y = 30 if y == 70 else 70
    assert not _blue_at(image, 50 + offset[0], other_y + offset[1]), "混用了另一计算版本"
    return pages, text


def _error_codes(body):
    errors = [body.get("error") or {}, *[o.get("error") or {} for o in body.get("outputs", [])]]
    return {e.get("code") for e in errors} | {body.get("code")}


def test_original_svg_and_pdf_deliver_the_current_computed_curve(project):
    client, root, pdfium = project
    chart = _bind(client, 0.25)
    before = (root / "chart.svg").read_bytes()
    body, outputs = _export(client, _spec(original=_original(chart)))
    assert body["status"] == "done", body
    assert _line_geometries(_path(body, outputs["svg"]).read_bytes()) == [
        pytest.approx((10, 70, 90, 70), abs=1e-6)
    ]
    _assert_pdf(pdfium, _path(body, outputs["pdf"]), y=70)
    assert outputs["pdf"]["vector"] is True
    assert (root / "chart.svg").read_bytes() == before


@pytest.mark.parametrize("mode", ["objects", "pad"])
def test_original_annotations_and_padding_use_the_computed_source(project, mode):
    client, _root, pdfium = project
    chart = _bind(client, 0.25)
    changes = {"w_mm": 100 * PT_MM, "h_mm": 100 * PT_MM}
    if mode == "objects":
        changes["objects"] = [
            {
                "type": "text",
                "id": "note",
                "text": "Chart annotation",
                "x_mm": 5 * PT_MM,
                "y_mm": 2 * PT_MM,
                "w_mm": 90 * PT_MM,
                "h_mm": 12 * PT_MM,
                "size_pt": 8,
            }
        ]
    else:
        changes["pad_mm"] = [n * PT_MM for n in (5, 7, 3, 4)]
    body, outputs = _export(client, _spec(original=_original(chart, **changes)))
    assert body["status"] == "done", body
    svg = _path(body, outputs["svg"]).read_bytes()
    assert _line_geometries(svg) == [pytest.approx((10, 70, 90, 70), abs=1e-6)]
    _, text = _assert_pdf(
        pdfium,
        _path(body, outputs["pdf"]),
        y=70,
        size=(108, 111) if mode == "pad" else (100, 100),
        offset=(5, 7) if mode == "pad" else (0, 0),
    )
    if mode == "objects":
        assert "Chart annotation" in text
        assert "Chart annotation" in "".join(ET.fromstring(svg).itertext())


def test_canvas_two_panels_of_one_file_keep_distinct_models_in_svg_and_pdf(project):
    client, _root, pdfium = project
    low, high = _bind(client, 0.25), _bind(client, 0.75)
    canvas = {
        "page_w_mm": 220 * PT_MM,
        "page_h_mm": 120 * PT_MM,
        "objects": [_panel(low, x=10, y=10), _panel(high, x=110, y=10)],
    }
    body, outputs = _export(client, _spec(canvas=canvas))
    assert body["status"] == "done", body
    assert _line_geometries(_path(body, outputs["svg"]).read_bytes()) == [
        pytest.approx((10, 70, 90, 70), abs=1e-6),
        pytest.approx((10, 30, 90, 30), abs=1e-6),
    ]
    _, size, text, image = _pdf_page(pdfium, _path(body, outputs["pdf"]))
    assert size == pytest.approx((220, 120), abs=0.02)
    assert text.count("Source keep") == 2
    assert _blue_at(image, 60, 80)
    assert not _blue_at(image, 60, 40)
    assert _blue_at(image, 160, 40)
    assert not _blue_at(image, 160, 80)


def test_merged_pdf_deduplicates_by_calculation_version_and_preserves_page_order(project):
    client, _root, pdfium = project
    low, high = _bind(client, 0.25), _bind(client, 0.75)
    spec = _spec(
        merge_pdf={"pages": [_original(low), _original(high), _original(low)]}, formats=("pdf",)
    )
    body, outputs = _export(client, spec)
    assert body["status"] == "done", body
    path = _path(body, outputs["pdf"])
    for index, y in enumerate((70, 30, 70)):
        count, _text = _assert_pdf(pdfium, path, y=y, index=index)
        assert count == 3


def test_preview_and_export_both_refuse_a_different_actual_engine(project):
    client, root, _pdfium = project
    chart = copy.deepcopy(_bind(client, 0.25))
    chart["model"]["engine"]["matplotlib"] = "0.0.0"
    original = (root / "chart.svg").read_bytes()
    preview = client.post("/api/svg/chart/compute", json={"file": "chart.svg", **chart})
    assert preview.status_code == 409, preview.get_json()
    assert preview.get_json()["code"] == "chart_engine_mismatch"
    response = client.post(
        "/api/export", json=_spec(original=_original(chart), filename="mismatch")
    )
    body = response.get_json()
    # 同步导出沿用既有终局失败 HTTP 500；语义错误由稳定 code 表达。
    assert response.status_code == 500, body
    assert "chart_engine_mismatch" in _error_codes(body), body
    assert not list((root / "tavottofile" / "export").glob("mismatch.*"))
    assert (root / "chart.svg").read_bytes() == original


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_source_changed_during_inspection_refuses_publication_and_preserves_existing_exports(
    project, monkeypatch, scope
):
    client, root, _pdfium = project
    low, high = _bind(client, 0.25), _bind(client, 0.75)

    def request_for(chart):
        if scope == "original":
            return _spec(original=_original(chart), filename="protected")
        return _spec(
            canvas={
                "page_w_mm": 100 * PT_MM,
                "page_h_mm": 100 * PT_MM,
                "objects": [_panel(chart)],
            },
            filename="protected",
        )

    successful, outputs = _export(client, request_for(low))
    old_files = {
        _path(successful, out): _path(successful, out).read_bytes() for out in outputs.values()
    }
    source = root / "chart.svg"
    initial_bytes = source.read_bytes()
    changed_bytes = initial_bytes + b"\n<!-- external edit during inspection -->\n"
    real_inspect = m._export_inspect
    inspected = []

    def change_after_real_inspection(job, produced):
        result = real_inspect(job, produced)
        assert any(item.tmp_path is not None for item in result), "未生成真实产物，改源探针未到落点"
        inspected.append(True)
        source.write_bytes(changed_bytes)
        return result

    monkeypatch.setattr(m, "_export_inspect", change_after_real_inspection)
    response = client.post("/api/export", json=request_for(high))
    body = response.get_json()
    assert response.status_code == 500, body
    assert inspected == [True], "探针必须实际越过产物检查，再由发布守卫拒绝"
    assert "chart_source_changed" in _error_codes(body), body
    assert body.get("status") == "failed", body
    assert source.read_bytes() == changed_bytes
    assert b"tv-series" not in source.read_bytes()
    for path, expected in old_files.items():
        assert path.read_bytes() == expected, f"拒绝前已覆盖既有导出：{path}"


def test_unsupported_ir_feature_fails_only_conversion_formats_and_still_delivers_svg(project):
    client, root, _pdfium = project
    scene = SCENE.replace(b"</svg>", GRADIENT + b"</svg>")
    (root / "chart.svg").write_bytes(scene)
    chart = _bind(client, 0.25)
    body, outputs = _export(
        client, _spec(original=_original(chart), formats=("svg", "pdf", "png", "tiff"))
    )
    assert body["status"] == "partial", body
    svg = _path(body, outputs["svg"]).read_bytes()
    assert _line_geometries(svg) == [pytest.approx((10, 70, 90, 70), abs=1e-6)]
    assert list(ET.fromstring(svg).iter(f"{SVG_NS}linearGradient"))
    for fmt in ("pdf", "png", "tiff"):
        assert outputs[fmt]["status"] == "failed", body
        assert outputs[fmt]["error"]["code"], body
        assert outputs[fmt]["name"] is None, body
    assert (root / "chart.svg").read_bytes() == scene


def test_eps_unavailability_does_not_discard_the_computed_svg_or_pdf(project):
    client, root, pdfium = project
    # Complete opaque computed scenes now support EPS. A real partial-alpha
    # decoration remains legal in SVG/PDF but cannot be expressed by PostScript.
    decoration = (
        b'<rect id="eps-glass" x="2" y="2" width="6" height="6" fill="red" fill-opacity=".5"/>'
    )
    scene = SCENE.replace(b"</svg>", decoration + b"</svg>")
    (root / "chart.svg").write_bytes(scene)
    chart = _bind(client, 0.25)
    body, outputs = _export(client, _spec(original=_original(chart), formats=("svg", "pdf", "eps")))
    assert body["status"] == "partial", body
    svg = _path(body, outputs["svg"]).read_bytes()
    assert _line_geometries(svg) == [pytest.approx((10, 70, 90, 70), abs=1e-6)]
    glass = next(
        element for element in ET.fromstring(svg).iter() if element.get("id") == "eps-glass"
    )
    assert float(glass.get("fill-opacity")) == 0.5
    pdf = _path(body, outputs["pdf"])
    _assert_pdf(pdfium, pdf, y=70)
    _, _, _, image = _pdf_page(pdfium, pdf)
    assert image.getpixel((5 * 2, 5 * 2)) == pytest.approx((255, 128, 128), abs=2)
    eps = outputs["eps"]
    assert eps["status"] == "failed" and eps["name"] is None, body
    assert eps["error"]["code"] == "unsupported_capability", body
    assert eps["error"]["params"]["operation"] == "object_alpha", body
    assert not (Path(body["export_dir"]) / "computed.eps").exists()
    assert (root / "chart.svg").read_bytes() == scene


def test_canvas_unsupported_ir_feature_preserves_the_native_computed_svg(project):
    client, root, _pdfium = project
    scene = SCENE.replace(b"</svg>", GRADIENT + b"</svg>")
    (root / "chart.svg").write_bytes(scene)
    chart = _bind(client, 0.25)
    body, outputs = _export(
        client,
        _spec(
            canvas={
                "page_w_mm": 100 * PT_MM,
                "page_h_mm": 100 * PT_MM,
                "objects": [_panel(chart)],
            }
        ),
    )
    assert body["status"] == "partial", body
    svg = _path(body, outputs["svg"]).read_bytes()
    assert _line_geometries(svg) == [pytest.approx((10, 70, 90, 70), abs=1e-6)]
    assert list(ET.fromstring(svg).iter(f"{SVG_NS}linearGradient"))
    assert outputs["pdf"]["status"] == "failed", body
    assert outputs["pdf"]["error"]["code"], body
    assert outputs["pdf"]["name"] is None
    assert not (Path(body["export_dir"]) / "computed.pdf").exists()
    assert (root / "chart.svg").read_bytes() == scene


def test_original_raster_budget_failure_preserves_the_valid_svg_and_pdf(project):
    client, root, pdfium = project
    scene = SCENE.replace(b'width="100pt" height="100pt"', b'width="10000pt" height="10000pt"')
    (root / "chart.svg").write_bytes(scene)
    chart = _bind(client, 0.25)
    body, outputs = _export(
        client,
        {
            **_spec(original=_original(chart), formats=("svg", "pdf", "png", "tiff")),
            "ppi": 1200,
        },
    )
    assert body["status"] == "partial", body
    svg = _path(body, outputs["svg"]).read_bytes()
    assert _line_geometries(svg) == [pytest.approx((10, 70, 90, 70), abs=1e-6)]
    # 导出的 PDF 仍可读；独立读取器只渲 100×100 像素，不分配请求中超预算的巨大位图。
    _, size, text, image = _pdf_page(pdfium, _path(body, outputs["pdf"]), scale=0.01)
    assert size == pytest.approx((10000, 10000), abs=0.02)
    assert "Source keep" in text
    # 3pt 描边在此倍率只覆盖部分像素；PDFium 的边缘蓝色为 (127,127,255)。
    assert _blue_at(image, 5000, 7000, scale=0.01, max_red_green=200)
    assert not _blue_at(image, 5000, 3000, scale=0.01, max_red_green=200)
    for fmt in ("png", "tiff"):
        assert outputs[fmt]["status"] == "failed", body
        assert "pixel_budget_exceeded" in json.dumps(outputs[fmt]["error"]), body
        assert outputs[fmt]["name"] is None
        assert not (Path(body["export_dir"]) / f"computed.{fmt}").exists()
    assert (root / "chart.svg").read_bytes() == scene
