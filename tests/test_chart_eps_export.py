"""计算原图 EPS：真实 HTTP/worker，独立 Ghostscript 与 PDFium 几何/墨迹资格。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import app as m, chartbundle
from tavotto.engine import chartprofile, documentassets
from test_chart_native_api import SOURCE, _bind, _color
from test_chart_native_export import _original, _panel
from test_native_scene_eps import assert_same_ink, pdf_image, ps_image

# These are the existing real HTTP project / approved fonts / independent reader
# fixtures. No worker, rendering output or source identity is fabricated here.
pytest_plugins = ("test_chart_native_api", "test_native_scene_eps")
PT_MM = 25.4 / 72


def scene(*, partial_alpha=False):
    opacity = ' fill-opacity=".5"' if partial_alpha else ""
    decoration = (
        f'<rect id="keep-green" x="94" y="2" width="4" height="4" fill="#00aa00"{opacity}/>'
        '<path id="keep-magenta" d="M94 90L98 98L90 98Z" fill="#aa00aa"/>'
        '<text id="keep-cjk" x="12" y="97" font-family="sans-serif" font-size="4" '
        'transform="rotate(-8 12 97)">原图 Á 42</text>'
    ).encode()
    return SOURCE.replace(b"</svg>", decoration + b"</svg>")


def edits():
    return [
        _color("#00ff00"),
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5},
        _color("#ff0000"),
        {"gid": "axes_0", "prop": "xlim", "value": [0, 2]},
    ]


def bind_scene(project, *, partial_alpha=False):
    client, root = project
    source = scene(partial_alpha=partial_alpha)
    (root / "chart.svg").write_bytes(source)
    return client, root, source, _bind(client)


def export(client, original=None, *, canvas=None, name="native-eps", formats=("svg", "pdf", "eps")):
    response = client.post(
        "/api/export",
        json={
            "scope": "canvas" if canvas is not None else "original",
            "formats": list(formats),
            "filename": name,
            "overwrite": "replace",
            **({"original": original} if original is not None else {}),
            **({"canvas": canvas} if canvas is not None else {}),
        },
    )
    body = response.get_json()
    assert isinstance(body, dict) and set(o["format"] for o in body["outputs"]) == set(formats), (
        body
    )
    rows = {o["format"]: o for o in body["outputs"]}
    paths = {
        fmt: Path(body["export_dir"]) / item["name"]
        for fmt, item in rows.items()
        if item["status"] == "done"
    }
    for path in paths.values():
        assert path.is_file(), body
    return response.status_code, body, rows, paths


def pdf_pixels(path):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    page = document[0]
    size = tuple(page.get_size())
    bitmap = page.render(scale=4)
    image = bitmap.to_pil().convert("RGB")
    textpage = page.get_textpage()
    text = textpage.get_text_range()
    textpage.close()
    bitmap.close()
    page.close()
    document.close()
    return size, image, text


def assert_red(image, x, y, *, present=True):
    samples = [
        image.getpixel((round(x * 4) + dx, round(y * 4) + dy))
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
    ]
    found = any(p[0] > 200 and p[1] < 40 and p[2] < 40 for p in samples)
    assert found == present, (x, y, samples)


def assert_success_and_geometry(tmp_path, gs, faces, result, *, size, offset=(0, 0), scale=(1, 1)):
    status, body, rows, paths = result
    assert status == 200 and body["status"] == "done", body
    assert set(paths) == {"svg", "pdf", "eps"}, body
    actual_size, pdf, text = pdf_pixels(paths["pdf"])
    assert actual_size == pytest.approx(size, abs=0.02)
    eps = ps_image(tmp_path, gs, paths["eps"].read_bytes())
    svg, _ = pdf_image(tmp_path, paths["svg"].read_bytes(), *size, faces, name="svg-reference")
    assert eps.size == (round(size[0] * 4), round(size[1] * 4))
    assert_same_ink(eps, pdf)
    assert_same_ink(eps, svg)
    # From binding: x data0..1 in xlim0..2 maps x10..50, y=.25 maps y70.
    # Panel scale and padding are independent request facts, not output metadata.
    for image in (eps, pdf, svg):
        assert_red(image, offset[0] + 30 * scale[0], offset[1] + 70 * scale[1])
        assert_red(image, offset[0] + 70 * scale[0], offset[1] + 70 * scale[1], present=False)
        green = image.getpixel(
            (round((offset[0] + 96 * scale[0]) * 4), round((offset[1] + 4 * scale[1]) * 4))
        )
        assert green == (0, 170, 0)
        magenta = image.getpixel(
            (round((offset[0] + 94 * scale[0]) * 4), round((offset[1] + 95 * scale[1]) * 4))
        )
        assert magenta == (170, 0, 170)
    assert "Source keep" in text and "原图" in text
    # Export receipt dimensions must describe the actual complete EPS page.
    assert rows["eps"]["dimensions"]["mm"] == pytest.approx([v * PT_MM for v in size], abs=0.02)
    return paths, text


def save_reference(client, chart, patches):
    document = {
        "schema": 4,
        "project": {"id": "p", "name": "EPS"},
        "canvases": [
            {
                "id": "c",
                "page": {"w": 100, "h": 80},
                "objects": [
                    {
                        "id": "panel",
                        "type": "panel",
                        "fileId": "chart.svg",
                        "fileKind": "svg",
                        "chart": chart,
                        "overrides": patches,
                    }
                ],
            }
        ],
        "activeCanvasId": "c",
    }
    response = client.post("/api/documents/v4/layouts/EpsReference?target=project", json=document)
    assert response.status_code == 200, response.get_json()
    return response.get_json()["chart_sources"][0]["chart_source"]


def test_original_eps_preserves_full_scene_raw_edits_and_valid_saved_source_after_deletion(
    project, tmp_path, gs, faces
):
    client, root, source, chart = bind_scene(project)
    patches = edits()
    reference = save_reference(client, chart, patches)
    (root / "chart.svg").unlink()
    result = export(client, _original(chart, patches, chart_source=reference))
    paths, _ = assert_success_and_geometry(tmp_path, gs, faces, result, size=(100, 100))
    bundle = chartbundle.extract_chart(paths["svg"].read_bytes())
    assert bundle is not None and bundle.render_profile == chartprofile.NATIVE_PROFILE
    assert bundle.source_svg == source and bundle.chart == chart and bundle.overrides == patches
    assert not (root / "chart.svg").exists()
    # Source-backed complete vector EPS, not a whole-figure raster wrapper.
    eps = paths["eps"].read_bytes()
    assert b"colorimage" not in eps and b"imagemask" not in eps


@pytest.mark.parametrize("scope", ["annotations", "padding", "resized-annotations-padding"])
def test_original_eps_annotations_padding_and_nonuniform_panel_match_svg_and_pdf(
    project, tmp_path, gs, faces, scope
):
    client, root, source, chart = bind_scene(project)
    changes = {"w_mm": 100 * PT_MM, "h_mm": 100 * PT_MM}
    size, offset, scale = (100, 100), (0, 0), (1, 1)
    if scope != "annotations":
        changes["pad_mm"] = [v * PT_MM for v in (5, 7, 3, 4)]
        size, offset = (108, 111), (5, 7)
    if scope == "resized-annotations-padding":
        changes.update(w_mm=160 * PT_MM, h_mm=80 * PT_MM)
        size, scale = (168, 91), (1.6, 0.8)
    if scope != "padding":
        changes["objects"] = [
            {
                "type": "text",
                "id": "annotation",
                "text": "EPS 标注 Á",
                "x_mm": 15 * PT_MM,
                "y_mm": 3 * PT_MM,
                "w_mm": 70 * PT_MM,
                "h_mm": 8 * PT_MM,
                "size_pt": 6,
            }
        ]
    result = export(client, _original(chart, edits(), **changes), name=scope)
    _paths, text = assert_success_and_geometry(
        tmp_path, gs, faces, result, size=size, offset=offset, scale=scale
    )
    if scope != "padding":
        assert "EPS 标注" in text
    assert (root / "chart.svg").read_bytes() == source


@pytest.mark.parametrize("fault", ["missing", "tampered"])
def test_bad_chart_reference_never_falls_back_to_original_or_deleted_source(project, fault):
    client, root, source, chart = bind_scene(project)
    patches = edits()
    reference = save_reference(client, chart, patches)
    asset = documentassets.source_path(root / "tavottofile", reference)
    if fault == "missing":
        asset.unlink()
    else:
        asset.write_bytes(asset.read_bytes() + b" ")
    damaged = asset.read_bytes() if asset.exists() else None
    for has_source in (True, False):
        if not has_source:
            (root / "chart.svg").unlink()
        status, body, rows, paths = export(
            client,
            _original(chart, patches, chart_source=reference),
            name=f"bad-reference-{fault}-{has_source}",
        )
        assert status == 500 and body["status"] == "failed", body
        assert paths == {} and all(row["status"] == "failed" for row in rows.values()), body
        assert all(row["error"]["code"] == "chart_source_mismatch" for row in rows.values()), body
        assert all(row["name"] is None for row in rows.values()), body
        assert (asset.read_bytes() if asset.exists() else None) == damaged
        if has_source:
            assert (root / "chart.svg").read_bytes() == source


@pytest.mark.parametrize("location", ["source", "native", "annotation"])
def test_partial_alpha_fails_eps_item_honestly_while_svg_pdf_survive(project, location):
    client, root, source, chart = bind_scene(project, partial_alpha=location == "source")
    patches = edits()
    changes = {}
    if location == "native":
        patches.append({"gid": "axes_0.lines_0", "prop": "alpha", "value": 0.5})
    if location == "annotation":
        changes.update(w_mm=100 * PT_MM, h_mm=100 * PT_MM)
        changes["objects"] = [
            {
                "type": "shape",
                "id": "glass",
                "shape": "rect",
                "x_mm": 20 * PT_MM,
                "y_mm": 40 * PT_MM,
                "w_mm": 30 * PT_MM,
                "h_mm": 20 * PT_MM,
                "fill": "#00ff00",
                "fill_opacity": 0.5,
                "stroke_pt": 0,
            }
        ]
    status, body, rows, paths = export(client, _original(chart, patches, **changes), name=location)
    assert status == 200 and body["status"] == "partial", body
    assert set(paths) == {"svg", "pdf"}, body
    eps = rows["eps"]
    assert eps["status"] == "failed" and eps["name"] is None, body
    assert eps["error"]["code"] != "eps_needs_script", body
    assert eps["error"]["params"]["operation"] in {"object_alpha", "group_opacity"}, body
    assert not (Path(body["export_dir"]) / f"{location}.eps").exists()
    _size, image, _text = pdf_pixels(paths["pdf"])
    if location == "source":
        pixel = image.getpixel((96 * 4, 4 * 4))
        assert pixel == pytest.approx((128, 212, 128), abs=2)
    elif location == "native":
        pixel = image.getpixel((30 * 4, 70 * 4))
        assert pixel == pytest.approx((255, 128, 128), abs=2)
    assert (root / "chart.svg").read_bytes() == source


def test_canvas_eps_still_refuses_while_other_chart_formats_succeed(project):
    client, _root, _source, chart = bind_scene(project)
    status, body, rows, paths = export(
        client,
        canvas={
            "page_w_mm": 100 * PT_MM,
            "page_h_mm": 100 * PT_MM,
            "objects": [_panel(chart, edits())],
        },
        name="canvas",
    )
    assert status == 200 and body["status"] == "partial", body
    assert set(paths) == {"svg", "pdf"}, body
    assert (
        rows["eps"]["status"] == "failed" and rows["eps"]["error"]["code"] == "eps_not_for_canvas"
    ), body


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_validate_route_uses_actual_chart_fact_and_keeps_canvas_eps_refusal(project, scope):
    client, _root, _source, chart = bind_scene(project)
    payload = {"scope": scope, "formats": ["pdf", "svg", "eps"], "filename": "route"}
    if scope == "original":
        payload["original"] = _original(chart, edits())
    else:
        payload["canvas"] = {
            "page_w_mm": 100 * PT_MM,
            "page_h_mm": 100 * PT_MM,
            "objects": [_panel(chart, edits())],
        }
    response = client.post("/api/export/validate", json=payload)
    body = response.get_json()
    assert response.status_code == 200, body
    panels = body["route"]["panels"]
    assert set(panels) == {"chart.svg"}, body
    route = panels["chart.svg"]
    assert route["source"] == "declarative" and route["embeds_disk_bitmap"] is False
    assert route["formats"]["pdf"] == route["formats"]["svg"] == {"how": "convert"}
    expected = (
        {"how": "unavailable", "reason": "eps_not_for_canvas"}
        if scope == "canvas"
        else {"how": "convert"}
    )
    assert route["formats"]["eps"] == route["offers"]["eps"] == expected


@pytest.mark.parametrize("formats", [("eps",), ("pdf", "eps")])
def test_eps_only_annotation_padding_uses_internal_complete_svg(project, tmp_path, gs, formats):
    client, root, source, chart = bind_scene(project)
    original = _original(
        chart,
        edits(),
        w_mm=160 * PT_MM,
        h_mm=80 * PT_MM,
        pad_mm=[v * PT_MM for v in (5, 7, 3, 4)],
        objects=[
            {
                "type": "text",
                "id": "annotation",
                "text": "EPS 标注 Á",
                "x_mm": 15 * PT_MM,
                "y_mm": 3 * PT_MM,
                "w_mm": 70 * PT_MM,
                "h_mm": 8 * PT_MM,
                "size_pt": 6,
            }
        ],
    )
    status, body, rows, paths = export(client, original, name="eps-only", formats=formats)
    assert status == 200 and body["status"] == "done", body
    assert set(paths) == set(formats)
    assert all(row["status"] == "done" for row in rows.values()), body
    image = ps_image(tmp_path, gs, paths["eps"].read_bytes(), name="eps-only")
    assert image.size == (168 * 4, 91 * 4)
    assert_red(image, 53, 63)
    assert_red(image, 117, 63, present=False)
    assert b"colorimage" not in paths["eps"].read_bytes()
    assert (root / "chart.svg").read_bytes() == source


def test_worker_failure_preserves_the_requested_format(project, monkeypatch):
    client, _root, _source, chart = bind_scene(project)

    def failed(*_args, **_kwargs):
        raise m.engine_pool.WorkerError("injected worker failure", code="worker_failed")

    monkeypatch.setattr(m, "_export_chart_canonical", failed)
    status, body, rows, paths = export(
        client, _original(chart, edits()), name="worker-format", formats=("pdf", "svg")
    )
    assert status == 200 and body["status"] == "partial", body
    assert rows["pdf"]["status"] == "failed"
    assert rows["pdf"]["error"]["code"] == "worker_failed"
    assert rows["svg"]["status"] == "done" and set(paths) == {"svg"}
