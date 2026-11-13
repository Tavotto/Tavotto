"""An original annotation/pad export has one sealed scene for all formats."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import exportjob
from tavotto.rendercore import facade, pdfwriter, renderhost
from test_native_scene_eps import gs as gs
from test_static_svg_canvas import _read_pdf, provider as provider


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects(wait=True)
    for key, directory in (
        ("TAVOTTO_DATA_DIR", "data"),
        ("TAVOTTO_CONFIG_DIR", "config"),
        ("MPLCONFIGDIR", "mpl"),
    ):
        monkeypatch.setenv(key, str(tmp_path / directory))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    root = tmp_path / "project"
    root.mkdir()
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield root
    m.reset_projects(wait=True)
    renderhost.shutdown_shared()
    facade.reset_for_tests()
    exportjob.reset_for_tests()


def _source(path, color, *, px=(100, 50)):
    if path.suffix == ".svg":
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="50pt" '
            f'viewBox="0 0 100 50"><rect width="100" height="50" fill="{color}"/></svg>',
            encoding="utf-8",
        )
    elif path.suffix == ".pdf":
        import pikepdf

        with pikepdf.new() as document:
            page = document.add_blank_page(page_size=(100, 50))
            rgb = "1 0 0" if color == "red" else "0 0 1"
            page.Contents = document.make_stream(f"{rgb} rg 0 0 100 50 re f".encode())
            document.save(path)
    else:
        from PIL import Image

        Image.new("RGB", px, color).save(path)


def _run(project, source, original, monkeypatch, *, formats=("pdf", "png", "tiff")):
    """Change the actual source after the real canonical writer seals its PDF."""
    canonical = pdfwriter.write_pdf
    sealed = []
    captured = []
    first_sha = hashlib.sha256(source.read_bytes()).hexdigest()

    def seal(*args, **kwargs):
        result = canonical(*args, **kwargs)
        sealed.append(Path(args[1]))
        if len(sealed) == 1:
            _source(source, "blue", px=(original.get("px_w", 100), original.get("px_h", 50)))
        return result

    monkeypatch.setattr(pdfwriter, "write_pdf", seal)
    request = {
        "scope": "original",
        "filename": "composed",
        "formats": list(formats),
        "overwrite": "replace",
        "ppi": 144,
        "background": "white",
        "original": {"figure_id": source.name, "overrides": [], **original},
    }
    job = exportjob.prepare(request, project / "exports")

    def produce(actual_job, stage):
        products = m._export_produce(actual_job, stage)
        captured.extend(products)
        return products

    receipt = exportjob.run(job, produce)
    assert receipt["status"] == "done", receipt
    assert sealed and hashlib.sha256(source.read_bytes()).hexdigest() != first_sha
    outputs = {item["format"]: item for item in receipt["outputs"]}
    paths = {fmt: project / "exports" / item["name"] for fmt, item in outputs.items()}
    return paths, outputs, captured


def _check_rasters(paths, samples, expected_px, dpi):
    from PIL import Image

    contents = []
    for fmt in ("png", "tiff"):
        with Image.open(paths[fmt]) as image:
            rgb = image.convert("RGB")
            assert rgb.size == expected_px
            for x_mm, y_mm, wanted in samples:
                pixel = rgb.getpixel((round(x_mm * dpi[0] / 25.4), round(y_mm * dpi[1] / 25.4)))
                assert pixel == pytest.approx(wanted, abs=2), (fmt, pixel, wanted)
            assert image.info["dpi"] == pytest.approx(dpi, abs=0.02)
            contents.append(rgb.tobytes())
    assert contents[0] == contents[1]


@pytest.mark.parametrize("kind", ["svg", "pdf", "png"])
@pytest.mark.parametrize("decoration", ["pad", "annotations"])
def test_original_outputs_keep_one_scene_after_source_changes(
    project, provider, monkeypatch, kind, decoration
):
    source = project / f"source.{kind}"
    _source(source, "red")
    width, height = 100 * 25.4 / 72, 50 * 25.4 / 72
    original = {"w_mm": width, "h_mm": height, "source_kind": "vector"}
    if kind == "png":
        original.update(source_kind="raster", px_w=100, px_h=50)
    if decoration == "pad":
        original["pad_mm"] = [2, 2, 2, 2]
        width += 4
        height += 4
    else:
        original["objects"] = [
            {
                "type": "text",
                "id": "label",
                "text": "Sealed scene",
                "x_mm": 1,
                "y_mm": 12,
                "w_mm": 30,
                "h_mm": 5,
                "size_pt": 8,
            },
        ]
    paths, _, captured = _run(project, source, original, monkeypatch)
    samples = [(10, 6, (255, 0, 0))]
    if decoration == "pad":
        samples.append((1, 1, (255, 255, 255)))
    measured = _read_pdf(paths["pdf"], samples, page_mm=(width, height))
    if decoration == "annotations":
        assert measured["text"].strip() == "Sealed scene"
    dpi = 72 if kind == "png" else 144
    _check_rasters(
        paths, samples, (round(width * dpi / 25.4), round(height * dpi / 25.4)), (dpi, dpi)
    )
    # Every format describes the same actual frozen source, not the changed file.
    identities = [item.manifest["plan"]["sources"] for item in captured]
    assert identities and all(facts == identities[0] for facts in identities)


def test_one_scene_keeps_unequal_axis_density_pad_and_annotation_order(
    project, provider, monkeypatch
):
    source = project / "source.png"
    _source(source, "red", px=(100, 50))
    shape = {
        "type": "shape",
        "shape": "rect",
        "w_mm": 4,
        "h_mm": 4,
        "stroke_pt": 0,
        "x_mm": 28,
        "y_mm": 3,
    }
    original = {
        "w_mm": 25.4,
        "h_mm": 25.4,
        "px_w": 100,
        "px_h": 50,
        "source_kind": "raster",
        "pad_mm": [25.4, 0, 0, 12.7],
        "objects_below": 1,
        "objects": [
            {**shape, "id": "below", "fill": "#0000ff"},
            {**shape, "id": "above", "x_mm": 36, "fill": "#00ff00"},
            {
                "type": "text",
                "id": "label",
                "text": "Axis density",
                "x_mm": 1,
                "y_mm": 27,
                "w_mm": 30,
                "h_mm": 5,
                "size_pt": 8,
            },
        ],
    }
    paths, outputs, _ = _run(project, source, original, monkeypatch)
    samples = [(30, 5, (255, 0, 0)), (38, 5, (0, 255, 0)), (5, 5, (255, 255, 255))]
    measured = _read_pdf(paths["pdf"], samples, page_mm=(50.8, 38.1))
    assert measured["text"].strip() == "Axis density"
    _check_rasters(paths, samples, (200, 75), (100, 50))
    assert all(outputs[fmt]["dimensions"]["px"] == [200, 75] for fmt in ("png", "tiff"))


@pytest.mark.parametrize("formats", [("pdf", "png", "eps"), ("eps",)])
def test_chart_eps_internal_svg_keeps_the_same_frozen_scene(
    project, provider, monkeypatch, tmp_path, gs, formats
):
    from tavotto.engine import pool
    from test_chart_eps_export import assert_red, bind_scene, edits, export, pdf_pixels
    from test_chart_native_export import _original
    from test_native_scene_eps import ps_image

    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", pool.find_worker_python())
    monkeypatch.setitem(m.app.config, "TESTING", True)
    client = m.app.test_client()
    client, root, raw, chart = bind_scene((client, project))
    source = root / "chart.svg"
    freeze = m._export_chart_canonical
    write = pdfwriter.write_pdf
    frozen = []
    changed = []

    def capture_companion(*args, **kwargs):
        result = freeze(*args, **kwargs)
        frozen.append(result)
        return result

    def seal_then_replace(*args, **kwargs):
        result = write(*args, **kwargs)
        if Path(args[1]).name == "out.pdf" and not changed:
            companion = frozen[0].svg.path
            data = companion.read_bytes()
            replacement = data.replace(b"#00aa00", b"#0000ff")
            assert data != replacement
            companion.write_bytes(replacement)
            changed.append(companion)
        return result

    monkeypatch.setattr(m, "_export_chart_canonical", capture_companion)
    monkeypatch.setattr(pdfwriter, "write_pdf", seal_then_replace)
    pt_mm = 25.4 / 72
    original = _original(
        chart, edits(), w_mm=100 * pt_mm, h_mm=100 * pt_mm, pad_mm=[v * pt_mm for v in (5, 7, 3, 4)]
    )
    status, receipt, rows, paths = export(client, original, formats=formats)
    assert status == 200 and receipt["status"] == "done", receipt
    assert set(paths) == set(rows) == set(formats)  # Internal SVG is not a user output.
    assert frozen and changed and source.read_bytes() == raw
    image = ps_image(tmp_path, gs, paths["eps"].read_bytes())
    assert image.size == (432, 444)
    assert_red(image, 35, 77)
    assert image.getpixel((101 * 4, 11 * 4)) == (0, 170, 0)
    if "pdf" in paths:
        size, pdf, text = pdf_pixels(paths["pdf"])
        assert size == pytest.approx((108, 111), abs=0.02)
        assert_red(pdf, 35, 77)
        assert pdf.getpixel((101 * 4, 11 * 4)) == (0, 170, 0)
        assert "Source keep" in text and "原图" in text
        from PIL import Image

        with Image.open(paths["png"]) as png:
            dpi = m.engine_exportreq.PPI_DEFAULT
            assert png.convert("RGB").getpixel((round(101 * dpi / 72), round(11 * dpi / 72))) == (
                0,
                170,
                0,
            )
