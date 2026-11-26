"""U10 against the authenticated desktop sidecar, optionally in a built app."""

import hashlib
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

import pikepdf
import pymupdf
import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart

from support.foundation_app import HttpError, sidecar_app

MPL_SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    plt.rcParams["pdf.fonttype"] = 42
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.bar([0.45, 1.55], [0.7, 1.3], width=0.25, color="#617883", edgecolor="#352B58")
    ax.fill_between([0, 1, 2], [.8, 3.6, 1.8], [1.2, 4.35, 2.2], color="#559944", alpha=.4)
    ax.plot([0, 1, 2], [1, 4, 2], color="#317A75", marker="o")
    ax.errorbar([0, 1, 2], [1, 4, 2], yerr=[.4, .7, .5], fmt="none", capsize=3)
    ax.set(xlabel="Time (min)", ylabel="Signal (mV)", title="Original signal")
    fig.tight_layout()
    fig.savefig("Signal.pdf")
"""
R_SCRIPT = r"""
cat("built\n", file = "r-builds.txt", append = TRUE)
trend <- data.frame(displ = c(1.8, 3.3, 5.2), hwy = c(34, 31, 23))
reference <- data.frame(displ = c(2.3, 4.2), hwy = c(7, 9))
plot <- ggplot2::ggplot(ggplot2::mpg, ggplot2::aes(displ, hwy)) +
  ggplot2::geom_point(ggplot2::aes(shape = drv, colour = cty), alpha = .5) +
  ggplot2::geom_line(data = trend, colour = "#1156A3", show.legend = FALSE) +
  ggplot2::geom_col(data = reference, width = .36, fill = "#226688", colour = "#182834") +
  ggplot2::geom_errorbar(data = trend, ggplot2::aes(ymin = hwy - 2, ymax = hwy + 1), width = .25) +
  ggplot2::geom_ribbon(data = trend, ggplot2::aes(ymin = hwy - 1, ymax = hwy + 1),
                       fill = "#559944", alpha = .4) +
  ggplot2::scale_colour_gradient(name = "City mileage", breaks = c(10, 17, 27),
    guide = ggplot2::guide_colourbar(position = "left", display = "rectangles")) +
  ggplot2::labs(x = "Displacement (L)", y = "Highway mileage", title = "Fuel economy",
                 shape = "Drive type") +
  ggplot2::theme_minimal() +
  ggplot2::theme(plot.title = ggplot2::element_text(colour = "#112233"))
"""
LAYOUT = "/api/layouts/Mixed%20native"
MPL_EDIT = "Reviewed signal"
R_EDIT = "#6C3799"
AXIS_FACES = {"weight": "bold", "style": "italic", "fontfamily": "monospace"}
AXIS_PAGE_SIZE = 12.4
R_AXIS_PAGE_SIZE = 13.8
R_PANEL_SCALE = 0.8
R_LEGEND_POSITION = [0.42, 0.64]
R_CURVE_COLOR = "#923579"
R_CURVE_WIDTH = 2.4
R_TICK_FORMATS = {
    "axis.x.bottom.ticks": "%.2f",
    "legend.left.1.colourbar.ticks": "%.1f",
}
R_COLOR_SCALE = {
    "vmin": 10,
    "vmax": 30,
    "cmap": "plasma",
}
R_COLOR_SCALE_GID = "legend.left.1.colourbar"
BAR_PAINT = {"facecolor": "#E25832", "edgecolor": "#663399", "linewidth": 2.2, "alpha": 0.75}
ERRORBAR_PAINT = {"color": "#803239", "linewidth": 1.8, "alpha": 0.6}
RANGE_PAINT = {"facecolor": "#695DB2", "alpha": 0.6}
R_AXIS_VIEW = {"xlim": [1, 8], "xscale": "log"}
MPL_AXIS_VIEW = {"xlim": [-0.25, 2.25], "ylim": [-0.3, 5.5]}


def _series_patches(baseline, role, paint):
    (series,) = [el for el in baseline["manifest"]["elements"] if el["role"] == role]
    fields = {field["prop"]: field for field in series["editable"]}
    assert set(paint) <= fields.keys()
    if "linewidth" in paint:
        assert fields["linewidth"]["unit"] == "pt"
    identity = {"identity": series["identity"]} if "identity" in series else {}
    return series, [
        {"gid": series["gid"], "prop": prop, "value": value, **identity}
        for prop, value in paint.items()
    ]


def _run_ggplot(app, script, rscript, library=None):
    status, started = app.call(
        "/api/native/ggplot2",
        {
            "script": script,
            "object": "plot",
            "frame_pt": [216, 144],
            "rscript": rscript,
            **({"library": str(library)} if library is not None else {}),
        },
    )
    assert status == 202, started
    session = started["session"]
    assert session["owner"] == "desktop"
    route = f"/api/native/sessions/{session['session_id']}"
    deadline = time.monotonic() + 45
    while session["state"] not in {"barrier", "ended", "detached", "failed"}:
        assert time.monotonic() < deadline, session
        time.sleep(0.2)
        _, state = app.call(route, timeout=5)
        session = state["session"]
    assert session["state"] == "barrier", session
    _, built = app.call(f"{route}/build", {}, timeout=60)
    (descriptor,) = built["descriptors"]
    return descriptor["asset_id"]


def _render(app, asset, patches):
    _, body = app.call(
        "/api/engine/render",
        {"id": asset, "patches": patches, "inline_svg": True},
        timeout=120,
    )
    return body


def _title(body, prop):
    return next(
        element
        for element in body["manifest"]["elements"]
        if element["role"] == "title" and any(item["prop"] == prop for item in element["editable"])
    )


def _panel(id_, asset, x, patches, *, scale=1):
    return {
        "id": id_,
        "type": "panel",
        "fileId": asset,
        "fileKind": "runtime",
        "x": x,
        "y": 5,
        "w": 76.2 * scale,
        "h": 50.8 * scale,
        "nativeW": 76.2,
        "nativeH": 50.8,
        "figureFrame": 1,
        "overrides": patches,
    }


def _axis_style_patches(render, size):
    titles = [
        element for element in render["manifest"]["elements"] if element["role"] == "axis_label"
    ]
    assert len(titles) == 2
    return [
        {
            "gid": element["gid"],
            "prop": prop,
            "value": value,
            **({"identity": element["identity"]} if "identity" in element else {}),
        }
        for element in titles
        for prop, value in {**AXIS_FACES, "fontsize": size}.items()
    ]


def _canvas_export_request(doc, filename):
    (canvas,) = doc["canvases"]
    return {
        "scope": "canvas",
        "filename": filename,
        "formats": ["pdf", "svg", "png"],
        "ppi": 144,
        "canvas": {
            "page_w_mm": canvas["page"]["w"],
            "page_h_mm": canvas["page"]["h"],
            "objects": [
                {
                    "type": "panel",
                    "id": panel["fileId"],
                    "x_mm": panel["x"],
                    "y_mm": panel["y"],
                    "w_mm": panel["w"],
                    "h_mm": panel["h"],
                    "overrides": panel["overrides"],
                    "figure_snapshot": panel["figureSnapshot"],
                }
                for panel in canvas["objects"]
            ],
        },
    }


def _export_files(app, request, *, svg_unavailable=None):
    _, result = app.call("/api/export", request)
    assert result["status"] == ("partial" if svg_unavailable else "done"), result
    out_dir = Path(result["export_dir"])
    outputs = {out["format"]: out for out in result["outputs"]}
    assert set(outputs) == set(request["formats"])
    if svg_unavailable:
        failed = outputs.pop("svg")
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "svg_source_unsupported"
        assert failed["error"]["params"]["id"] == svg_unavailable
        assert not (out_dir / f"{request['filename']}.svg").exists()
    files = {}
    semantics = set()
    for fmt, out in outputs.items():
        assert out["status"] == "done", out
        path = out_dir / out["name"]
        data = path.read_bytes()
        manifest = out["manifest"]
        assert manifest["format"] == fmt and manifest["checks"]["integrity"] == "verified"
        assert manifest["bytes"] == len(data)
        assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
        assert manifest["identity"]["run"] == result["job_id"]
        semantics.add(manifest["identity"]["semantic"])
        files[fmt] = path
    assert len(semantics) == 1 and None not in semantics
    return files


def _assert_canvas_size(page):
    assert page.rect.width == pytest.approx(170 * 72 / 25.4, abs=0.01)
    assert page.rect.height == pytest.approx(61 * 72 / 25.4, abs=0.01)


def _pdf_pixels(path):
    with pymupdf.open(path) as pdf:
        assert len(pdf) == 1
        (page,) = pdf
        _assert_canvas_size(page)
        lines = [
            line["spans"]
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
        ]
        by_text = {"".join(span["text"] for span in line): line for line in lines}
        assert "Original signal" not in by_text
        mpl = by_text[MPL_EDIT]
        ggplot = by_text["Fuel economy"]
        assert max(span["bbox"][2] for span in mpl) < page.rect.width / 2
        assert min(span["bbox"][0] for span in ggplot) > page.rect.width / 2
        assert {span["color"] for span in ggplot} == {int(R_EDIT[1:], 16)}
        legend_left = 88 * 72 / 25.4 + R_LEGEND_POSITION[0] * 216 * R_PANEL_SCALE
        assert legend_left < by_text["Drive type"][0]["bbox"][0] < legend_left + 10
        assert page.search_for("2.00") and page.search_for("15.0")
        assert not page.search_for("3.00")
        low = tuple(component / 255 for component in bytes.fromhex("0D0887"))
        high = tuple(component / 255 for component in bytes.fromhex("F0F921"))
        drawn = page.get_drawings()
        for endpoint in (low, high):
            matches = [path for path in drawn if path["fill"] == pytest.approx(endpoint, abs=0.003)]
            assert any(path["rect"].width > 8 for path in matches), matches  # Guide strip
            assert any(path["rect"].width < 7 for path in matches), matches  # Data markers
        curve_color = tuple(component / 255 for component in bytes.fromhex(R_CURVE_COLOR[1:]))
        (curve,) = [
            path
            for path in page.get_drawings()
            if path["color"] is not None and path["color"] == pytest.approx(curve_color, abs=0.002)
        ]
        assert curve["width"] == pytest.approx(R_CURVE_WIDTH * R_PANEL_SCALE, abs=0.02)
        assert curve["dashes"] != "[] 0"
        first, second = curve["items"]
        assert first[0] == second[0] == "l" and first[2] == second[1]
        actual_ratio = (first[2].x - first[1].x) / (second[2].x - first[1].x)
        assert actual_ratio == pytest.approx(math.log(3.3 / 1.8) / math.log(5.2 / 1.8), abs=0.003)
        face = tuple(component / 255 for component in bytes.fromhex(BAR_PAINT["facecolor"][1:]))
        bars = [path for path in drawn if path["fill"] == pytest.approx(face, abs=0.003)]
        assert len(bars) == 4
        assert sum(path["rect"].x0 > page.rect.width / 2 for path in bars) == 2
        assert all(path["fill_opacity"] == pytest.approx(0.75, abs=0.01) for path in bars)
        error_color = tuple(
            component / 255 for component in bytes.fromhex(ERRORBAR_PAINT["color"][1:])
        )
        errors = [path for path in drawn if path["color"] == pytest.approx(error_color, abs=0.003)]
        assert any(path["rect"].x0 < page.rect.width / 2 for path in errors)
        assert any(path["rect"].x0 > page.rect.width / 2 for path in errors)
        assert any(path["width"] == pytest.approx(1.8 * R_PANEL_SCALE, abs=0.02) for path in errors)
        assert all(path["stroke_opacity"] == pytest.approx(0.6, abs=0.01) for path in errors)
        ribbon_color = tuple(n / 255 for n in bytes.fromhex(RANGE_PAINT["facecolor"][1:]))
        ribbons = [path for path in drawn if path["fill"] == pytest.approx(ribbon_color, abs=0.003)]
        assert len(ribbons) == 2
        assert sum(path["rect"].x0 > page.rect.width / 2 for path in ribbons) == 1
        assert all(path["fill_opacity"] == pytest.approx(0.6, abs=0.01) for path in ribbons)
        bold_italic = pymupdf.TEXT_FONT_BOLD | pymupdf.TEXT_FONT_ITALIC
        for title, size in {
            "Time (min)": AXIS_PAGE_SIZE,
            "Signal (mV)": AXIS_PAGE_SIZE,
            "Displacement (L)": R_AXIS_PAGE_SIZE,
            "Highway mileage": AXIS_PAGE_SIZE,
        }.items():
            spans = [span for span in by_text[title] if span["text"].strip()]
            assert all(span["flags"] & bold_italic == bold_italic for span in spans), by_text[title]
            assert all(
                any(
                    name in span["font"].lower()
                    for name in ("mono", "courier", "consolas", "menlo")
                )
                for span in spans
            ), by_text[title]
            assert [span["size"] for span in spans] == pytest.approx([size] * len(spans), abs=0.025)
        return page.get_pixmap().samples


def _format_pixels(files):
    svg_tree = ElementTree.parse(files["svg"]).getroot()
    ns = "{http://www.w3.org/2000/svg}"
    assert svg_tree.tag == f"{ns}svg"
    assert svg_tree.find(f".//{ns}path") is not None
    assert svg_tree.find(f".//{ns}image") is None
    (title,) = [node for node in svg_tree.iter(f"{ns}text") if node.text == "Fuel economy"]
    assert R_EDIT.lower() in title.attrib["style"].lower()
    assert {"2.00", "15.0"} <= {node.text for node in svg_tree.iter(f"{ns}text")}
    assert not any(node.text == "3.00" for node in svg_tree.iter(f"{ns}text"))
    (curve,) = [
        node
        for node in svg_tree.iter(f"{ns}polyline")
        if f"stroke: {R_CURVE_COLOR.lower()}" in node.attrib.get("style", "").lower()
    ]
    assert "stroke-width: 2.40" in curve.attrib["style"]
    assert "stroke-dasharray" in curve.attrib["style"]
    bar_styles = [
        node.attrib["style"].lower()
        for node in svg_tree.iter(f"{ns}rect")
        if f"fill: {BAR_PAINT['facecolor'].lower()}" in node.attrib.get("style", "").lower()
    ]
    assert len(bar_styles) == 2  # R rectangles. Matplotlib emits paths.
    assert all("stroke: #663399" in style and "stroke-width: 2.20" in style for style in bar_styles)
    (ribbon,) = [
        node
        for node in svg_tree.iter(f"{ns}polygon")
        if f"fill: {RANGE_PAINT['facecolor'].lower()}" in node.attrib.get("style", "").lower()
    ]
    assert "fill-opacity: 0.60" in ribbon.attrib["style"]
    for label, page_size in (
        ("Displacement (L)", R_AXIS_PAGE_SIZE),
        ("Highway mileage", AXIS_PAGE_SIZE),
    ):
        (axis_title,) = [node for node in svg_tree.iter(f"{ns}text") if node.text == label]
        assert "font-weight: bold" in axis_title.attrib["style"]
        assert "font-style: italic" in axis_title.attrib["style"]
        css = dict(
            item.strip().split(":", 1)
            for item in axis_title.attrib["style"].split(";")
            if ":" in item
        )
        assert float(css["font-size"].strip().removesuffix("px")) == pytest.approx(
            page_size / R_PANEL_SCALE
        )
    with pymupdf.open(files["svg"]) as svg:
        assert len(svg) == 1
        _assert_canvas_size(svg[0])
        svg_pixels = svg[0].get_pixmap().samples
    return _pdf_pixels(files["pdf"]), svg_pixels, _png_pixels(files["png"])


def _png_pixels(path):
    with Image.open(path) as png:
        assert png.size == (964, 346)
        assert png.info["dpi"] == pytest.approx((144, 144), abs=0.01)
        rgba = png.convert("RGBA")
    right = rgba.crop((rgba.width // 2, 0, rgba.width, rgba.height))
    title_color = (*bytes.fromhex(R_EDIT[1:]), 255)
    assert any(rgb == title_color for _, rgb in right.getcolors(right.width * right.height))
    return rgba.tobytes()


def _assert_structured_pdf_export(app, doc):
    request = _canvas_export_request(doc, "Plain native figures")
    request["formats"] = ["pdf"]
    mpl, r = request["canvas"]["objects"]
    mpl["bookmark_title"] = "Matplotlib signal"
    r["bookmark_title"] = "ggplot2 fuel economy"
    caption = "Notes on native figures"
    request["canvas"]["objects"].append(
        {
            "type": "text",
            "text": caption,
            "x_mm": 5,
            "y_mm": 56,
            "w_mm": 155,
            "h_mm": 4,
            "size_pt": 8,
        }
    )
    plain = _export_files(app, request)["pdf"]
    structured = _export_files(
        app,
        {
            **request,
            "filename": "Native figures with bookmarks and notes",
            "pdf_panel_bookmarks": True,
            "pdf_panel_layers": True,
            "pdf_annotation_layer": True,
            "pdf_text_attachment": True,
        },
    )["pdf"]
    titles = ["1. Matplotlib signal", "2. ggplot2 fuel economy"]
    with pikepdf.Pdf.open(structured) as pdf:
        outline = pdf.Root.Outlines
        first = outline.First
        second = first.Next
        assert outline.Count == 2 and second.objgen == outline.Last.objgen
        rectangles_mm = [
            (5, 5.2, 81.2, 56),
            (88, 56 - 50.8 * R_PANEL_SCALE, 88 + 76.2 * R_PANEL_SCALE, 56),
        ]
        for node, title, rect in zip((first, second), titles, rectangles_mm, strict=True):
            page, fit, *coords = node.Dest
            assert str(node.Title) == title
            assert page.objgen == pdf.pages[0].obj.objgen
            assert fit == pikepdf.Name.FitR
            assert [float(value) for value in coords] == pytest.approx(
                [mm * 72 / 25.4 for mm in rect], abs=0.001
            )

    with pymupdf.open(plain) as baseline, pymupdf.open(structured) as actual:
        assert baseline.get_toc() == [] and baseline.embfile_names() == []
        assert actual.get_toc() == [[1, title, 1] for title in titles]
        attachment = "tavotto-canvas-text.json"
        assert actual.embfile_names() == [attachment]
        payload = json.loads(actual.embfile_get(attachment))
        assert payload["source"] == "tavotto.canvas.text"
        assert payload["page"] == payload["schema_version"] == 1
        assert payload["annotations"] == [{"text": caption}]
        assert caption in actual[0].get_text()
        assert actual[0].get_pixmap(dpi=144).samples == baseline[0].get_pixmap(dpi=144).samples
        layers = actual.layer_ui_configs()
        assert [layer["text"] for layer in layers] == [*titles, "Canvas annotations"]
        assert all(layer["on"] for layer in layers)
        left = pymupdf.Rect(0, 0, actual[0].rect.width / 2, actual[0].rect.height)
        right = pymupdf.Rect(left.x1, 0, actual[0].rect.width, actual[0].rect.height)
        before = actual[0].get_pixmap(clip=right).samples
        actual.set_layer_ui_config(layers[1]["number"], action=pymupdf.PDF_OC_OFF)
        assert actual[0].get_pixmap(clip=right).samples != before
        assert actual[0].get_pixmap(clip=left).samples == baseline[0].get_pixmap(clip=left).samples
        actual.set_layer_ui_config(layers[1]["number"], action=pymupdf.PDF_OC_ON)
        assert actual[0].get_pixmap(clip=right).samples == before


def _assert_partial_svg_export(app, doc, project):
    supplement = "supplement.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=96, height=24)
        page.insert_text((6, 16), "Supplement", fontsize=9)
        pdf.save(project / supplement)
    request = _canvas_export_request(doc, "With supplement")
    request["canvas"]["objects"].append(
        {"type": "panel", "id": supplement, "x_mm": 135, "y_mm": 53, "w_mm": 24, "h_mm": 6}
    )
    files = _export_files(app, request, svg_unavailable=supplement)
    with pymupdf.open(files["pdf"]) as pdf:
        assert "Supplement" in pdf[0].get_text()
    _png_pixels(files["png"])


def _install(app, package, destination):
    with package.open("rb") as source:
        boundary, payload = encode_multipart(
            {
                "package": FileStorage(source, filename=package.name),
                "options": json.dumps({"destination": str(destination)}),
            }
        )
    request = urllib.request.Request(
        f"{app.base}/api/package/install",
        data=payload,
        headers={**app.auth, "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            assert response.status == 201
            return json.load(response)["doc"]
    except urllib.error.HTTPError as exc:
        pytest.fail(f"Package installation returned HTTP {exc.code}: {exc.read().decode()}")


def _archived_previews(app, doc):
    (canvas,) = doc["canvases"]
    previews = {}
    for panel in canvas["objects"]:
        route = "/api/figure-preview"
        request = {
            "id": panel["fileId"],
            "patches": panel["overrides"],
            "figure_snapshot": panel["figureSnapshot"],
        }
        _, preview = app.call(route, request)
        assert preview["svg"]
        previews[panel["id"]] = preview["svg"]
        with pytest.raises(HttpError) as mismatch:
            app.call(route, {**request, "patches": []})
        assert mismatch.value.status == 422
    return previews


def test_mixed_native_project_survives_portable_install(tmp_path):
    executable = os.environ.get("TAVOTTO_TEST_EXE")
    rscript = os.environ.get("TAVOTTO_TEST_RSCRIPT")
    if not executable and not rscript:
        pytest.skip("set TAVOTTO_TEST_RSCRIPT to qualify a real R runtime")
    assert rscript and Path(rscript).is_file(), "A real Rscript is required"
    app_binary = Path(executable).resolve(strict=True) if executable else None
    project = tmp_path / "原始 项目"
    project.mkdir()
    (project / "mpl_response.py").write_text(MPL_SCRIPT, encoding="utf-8")
    library = tmp_path / "Project R library"
    library.mkdir()
    subprocess.run(
        [
            rscript,
            "--vanilla",
            "-e",
            'stopifnot(file.copy(find.package("ggplot2"), commandArgs(TRUE)[[1]], recursive = TRUE))',
            str(library),
        ],
        check=True,
        timeout=45,
    )
    (project / "fuel.R").write_text(
        'stopifnot(identical(normalizePath(find.package("ggplot2"), winslash = "/"), '
        'normalizePath(file.path(.libPaths()[[1L]], "ggplot2"), winslash = "/")), '
        'basename(.libPaths()[[1L]]) == "Project R library")\n' + R_SCRIPT,
        encoding="utf-8",
    )
    (project / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "mpl_response.py": {"entry": "main", "cost": "light", "stems": ["Signal"]}
                },
            }
        ),
        encoding="utf-8",
    )
    with sidecar_app(project, tmp_path / "warm", executable=app_binary) as app:
        if app_binary is not None:
            _, environment = app.call("/api/engine/environment?probe=matplotlib", timeout=120)
            assert environment["source"] == "bundled", environment
        status, run = app.call("/api/registry/probe", {"script": "mpl_response.py"}, timeout=120)
        assert status == 200 and run["registered"], run
        (mpl,) = [item for item in run["descriptors"] if item["stem"] == "Signal"]
        mpl_asset = mpl["asset_id"]
        with pytest.raises(HttpError) as missing_library:
            _run_ggplot(app, "fuel.R", rscript, library / "absent")
        assert missing_library.value.status == 400
        r_asset = _run_ggplot(app, "fuel.R", rscript, library)
        mpl_baseline = _render(app, mpl_asset, [])
        r_baseline = _render(app, r_asset, [])
        mpl_title = _title(mpl_baseline, "text")
        r_title = _title(r_baseline, "color")
        mpl_patch = {"gid": mpl_title["gid"], "prop": "text", "value": MPL_EDIT}
        r_patch = {
            "gid": r_title["gid"],
            "prop": "color",
            "value": R_EDIT,
            "identity": r_title["identity"],
        }
        for asset, patch in ((mpl_asset, mpl_patch), (r_asset, r_patch)):
            after = _title(_render(app, asset, [patch]), patch["prop"])
            assert (
                next(item["value"] for item in after["editable"] if item["prop"] == patch["prop"])
                == patch["value"]
            )
        mpl_patches = [mpl_patch, *_axis_style_patches(mpl_baseline, AXIS_PAGE_SIZE)]
        _, mpl_view_patches = _series_patches(mpl_baseline, "axes", MPL_AXIS_VIEW)
        mpl_patches.extend(mpl_view_patches)
        r_bulk = [r_patch, *_axis_style_patches(r_baseline, AXIS_PAGE_SIZE / R_PANEL_SCALE)]
        r_single = [
            {**patch, "value": R_AXIS_PAGE_SIZE / R_PANEL_SCALE}
            if patch["gid"] == "axis.x.bottom.title" and patch["prop"] == "fontsize"
            else patch
            for patch in r_bulk
        ]
        bulk_svg = _render(app, r_asset, r_bulk)["svg"]
        sized_svg = _render(app, r_asset, r_single)["svg"]
        assert sized_svg != bulk_svg
        assert _render(app, r_asset, r_bulk)["svg"] == bulk_svg
        r_elements = {el["gid"]: el for el in r_baseline["manifest"]["elements"]}
        legend = r_elements["legend.right"]
        moved_patches = [
            *r_single,
            {
                "gid": legend["gid"],
                "prop": "loc_frac",
                "value": R_LEGEND_POSITION,
                "identity": legend["identity"],
            },
        ]
        moved = _render(app, r_asset, moved_patches)
        assert moved["svg"] != sized_svg
        assert _render(app, r_asset, r_single)["svg"] == sized_svg
        assert _render(app, r_asset, moved_patches)["svg"] == moved["svg"]
        curve = r_elements["layer.2.line"]
        scale = r_elements[R_COLOR_SCALE_GID]
        assert scale["role"] == "colorbar"
        assert curve["role"] == "line" and curve["geometry"]["kind"] == "polyline"
        r_patches = [
            *moved_patches,
            *[
                {
                    "gid": gid,
                    "prop": "format",
                    "value": value,
                    "identity": r_elements[gid]["identity"],
                }
                for gid, value in R_TICK_FORMATS.items()
            ],
            *[
                {
                    "gid": "axis.x.bottom.ticks",
                    "prop": prop,
                    "value": value,
                    "identity": r_elements["axis.x.bottom.ticks"]["identity"],
                }
                for prop, value in {"major_mode": "step", "major_step": 2}.items()
            ],
            *[
                {"gid": curve["gid"], "prop": prop, "value": value, "identity": curve["identity"]}
                for prop, value in {
                    "linewidth": R_CURVE_WIDTH,
                    "linestyle": "--",
                    "color": R_CURVE_COLOR,
                }.items()
            ],
            *[
                {"gid": scale["gid"], "prop": prop, "value": value, "identity": scale["identity"]}
                for prop, value in R_COLOR_SCALE.items()
            ],
        ]
        r_axes, r_view_patches = _series_patches(r_baseline, "axes", R_AXIS_VIEW)
        assert r_axes["identity"].startswith("r10:")
        before_view = _render(app, r_asset, r_patches)["svg"]
        precision = _render(app, r_asset, [*r_patches, *r_view_patches])
        assert precision["svg"] != before_view
        (edited_axes,) = [el for el in precision["manifest"]["elements"] if el["role"] == "axes"]
        assert edited_axes["identity"] == r_axes["identity"]
        view_fields = {f["prop"]: f["value"] for f in edited_axes["editable"]}
        assert {prop: view_fields[prop] for prop in R_AXIS_VIEW} == R_AXIS_VIEW
        with pytest.raises(HttpError):
            _render(app, r_asset, [{**r_view_patches[0], "identity": "r10:stale"}])
        assert _render(app, r_asset, r_patches)["svg"] == before_view
        assert precision["svg"] != moved["svg"]
        assert _render(app, r_asset, moved_patches)["svg"] == moved["svg"]
        r_patches.extend(r_view_patches)
        assert _render(app, r_asset, r_patches)["svg"] == precision["svg"]
        edited_scale = next(
            el for el in precision["manifest"]["elements"] if el["gid"] == R_COLOR_SCALE_GID
        )
        assert {
            field["prop"]: field["value"] for field in edited_scale["editable"]
        } == R_COLOR_SCALE
        _, mpl_bar_patches = _series_patches(mpl_baseline, "bar_series", BAR_PAINT)
        r_bars, r_bar_patches = _series_patches(precision, "bar_series", BAR_PAINT)
        mpl_errors, mpl_error_patches = _series_patches(mpl_baseline, "errorbar", ERRORBAR_PAINT)
        edited_mpl = _render(app, mpl_asset, [*mpl_patches, *mpl_bar_patches, *mpl_error_patches])
        edited_mpl_errors = next(
            el for el in edited_mpl["manifest"]["elements"] if el["gid"] == mpl_errors["gid"]
        )
        mpl_fields = {field["prop"]: field["value"] for field in edited_mpl_errors["editable"]}
        for prop, value in ERRORBAR_PAINT.items():
            assert mpl_fields[prop] == value
        (original_cap,) = [
            field["value"] for field in mpl_errors["editable"] if field["prop"] == "cap_thickness"
        ]
        assert mpl_fields["cap_thickness"] == original_cap
        r_errors, r_error_patches = _series_patches(precision, "errorbar", ERRORBAR_PAINT)
        _, mpl_range_patches = _series_patches(mpl_baseline, "fill", RANGE_PAINT)
        r_range, r_range_patches = _series_patches(precision, "fill", RANGE_PAINT)
        changed = _render(
            app, r_asset, [*r_patches, *r_bar_patches, *r_error_patches, *r_range_patches]
        )
        edited_bars = next(
            el for el in changed["manifest"]["elements"] if el["gid"] == r_bars["gid"]
        )
        assert edited_bars["identity"] == r_bars["identity"]
        assert edited_bars["geometry"]["paths"] == r_bars["geometry"]["paths"]
        assert {field["prop"]: field["value"] for field in edited_bars["editable"]} == BAR_PAINT
        edited_errors = next(
            el for el in changed["manifest"]["elements"] if el["gid"] == r_errors["gid"]
        )
        assert edited_errors["geometry"]["paths"] == r_errors["geometry"]["paths"]
        assert {
            field["prop"]: field["value"] for field in edited_errors["editable"]
        } == ERRORBAR_PAINT
        with pytest.raises(HttpError):
            _render(app, r_asset, [{**r_bar_patches[0], "identity": "r7:stale"}])
        edited_range = next(
            el for el in changed["manifest"]["elements"] if el["gid"] == r_range["gid"]
        )
        assert edited_range["geometry"]["paths"] == r_range["geometry"]["paths"]
        assert {field["prop"]: field["value"] for field in edited_range["editable"]} == RANGE_PAINT
        with pytest.raises(HttpError):
            _render(app, r_asset, [{**r_range_patches[0], "identity": "r9:stale"}])
        assert _render(app, r_asset, r_patches)["svg"] == precision["svg"]
        r_patches.extend([*r_bar_patches, *r_error_patches, *r_range_patches])
        mpl_patches.extend([*mpl_bar_patches, *mpl_error_patches, *mpl_range_patches])
        assert _render(app, r_asset, r_patches)["svg"] == changed["svg"]
        document = {
            "schema": 3,
            "project": {"id": "mixed", "name": "Mixed native"},
            "activeCanvasId": "first",
            "canvases": [
                {
                    "id": "first",
                    "name": "Mixed native",
                    "schema": 2,
                    "page": {"w": 170, "h": 61},
                    "guides": [],
                    "objects": [
                        _panel("mpl", mpl_asset, 5, mpl_patches),
                        _panel("r", r_asset, 88, r_patches, scale=R_PANEL_SCALE),
                    ],
                }
            ],
        }
        _, saved = app.call(LAYOUT + "?target=project", document)
        assert set(saved["figure_snapshots"]) == {"mpl", "r"}
        _, confirmed = app.call(LAYOUT)
        expected_previews = _archived_previews(app, confirmed)
        expected = _format_pixels(
            _export_files(app, _canvas_export_request(confirmed, "Before transfer"))
        )
        canvas_ids = [canvas["id"] for canvas in confirmed["canvases"]]
        _, packed = app.call("/api/package", {"doc": confirmed, "canvas_ids": canvas_ids})
        assert packed["document_format"] == "v4", packed
        package = project / "tavottofile" / "export" / packed["name"]
        assert (project / "r-builds.txt").read_text(encoding="utf-8").splitlines() == ["built"]
        assert app.call(LAYOUT)[1] == confirmed
    assert app.proc.returncode == 0

    empty = tmp_path / "空白 项目"
    empty.mkdir()
    receiver = tmp_path / "接收 项目"
    with sidecar_app(empty, tmp_path / "receiving", executable=app_binary) as app:
        installed = _install(app, package, receiver)
        for script in ("mpl_response.py", "fuel.R"):
            (receiver / script).unlink(missing_ok=True)
        _, opened = app.call("/api/projects/open", {"path": str(receiver)})
        assert opened["open"], opened
        _, saved = app.call(LAYOUT + "?target=project", installed)
        assert saved["ok"], saved
    assert app.proc.returncode == 0

    with sidecar_app(receiver, tmp_path / "reopened", executable=app_binary) as app:
        _, reopened = app.call(LAYOUT)
        assert reopened["canvases"] == confirmed["canvases"]
        assert _archived_previews(app, reopened) == expected_previews
        exported = _export_files(app, _canvas_export_request(reopened, "After transfer"))
        assert _format_pixels(exported) == expected
        _assert_structured_pdf_export(app, reopened)
        _assert_partial_svg_export(app, reopened, receiver)
        query = urllib.parse.urlencode({"project_root": str(receiver.resolve())})
        _, sessions = app.call(f"/api/native/sessions?{query}")
        assert sessions["sessions"] == []
        assert not (receiver / "r-builds.txt").exists()
    assert app.proc.returncode == 0
