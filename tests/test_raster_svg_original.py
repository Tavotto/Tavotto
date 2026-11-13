"""Raster originals become SVG Image scenes, never fabricated vector Figures."""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from xml.etree import ElementTree as ET

import pypdfium2 as pdfium
import pytest
from PIL import Image

from tavotto import app as m, pdfbackend
from tavotto.engine import exportjob, pool
from tavotto.rendercore import facade, job as rc_job, renderhost, sources


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects(wait=True)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    root = tmp_path / "project"
    root.mkdir()
    m.open_project(str(root))
    m.app.config["TESTING"] = True
    calls = []

    def deny(*args, **kwargs):
        calls.append("user_execution")
        raise AssertionError("A static Image SVG must not start or serialize a Figure")

    for name in ("get", "build", "build_owned", "one_shot"):
        monkeypatch.setattr(pool, name, deny)
    monkeypatch.setattr(m, "_serialize_figure", deny)
    try:
        yield root, m.app.test_client()
    finally:
        m.reset_projects(wait=True)
        renderhost.shutdown_shared()
        facade.reset_for_tests()
        exportjob.reset_for_tests()
        assert calls == []


def _image(path, *, dpi=None, alpha=True):
    image = Image.new("RGBA", (16, 12))
    image.putdata(
        [
            (
                220,
                30 + x * 4,
                20 + y * 8,
                ((0 if x < 3 else 255 if x > 11 else x * 17) if alpha else 255),
            )
            for y in range(12)
            for x in range(16)
        ]
    )
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        image = image.convert("RGB")
    image.save(path, **({"dpi": dpi} if dpi else {}))
    with Image.open(path) as read:
        return read.convert("RGBA").tobytes()


def _request(name, *, formats=("svg",), **original):
    return {
        "scope": "original",
        "formats": list(formats),
        "filename": "raster-original",
        "ppi": 144,
        "overwrite": "replace",
        "background": "white",
        "original": {
            "figure_id": name,
            "source_kind": "raster",
            "w_mm": 32,
            "h_mm": 24,
            **original,
        },
    }


def _run(project, spec):
    root, client = project
    response = client.post("/api/export", json=spec)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    outputs = {row["format"]: row for row in body["outputs"]}
    paths = {
        fmt: Path(body["export_dir"]) / row["name"]
        for fmt, row in outputs.items()
        if row["status"] == "done"
    }
    assert all(path.is_relative_to(root) for path in paths.values())
    return body, outputs, paths


def _embedded(path):
    root = ET.fromstring(path.read_bytes())
    images = root.findall(".//{http://www.w3.org/2000/svg}image")
    assert len(images) == 1
    uri = images[0].get("{http://www.w3.org/1999/xlink}href") or images[0].get("href")
    assert uri.startswith("data:image/")
    raw = base64.b64decode(uri.split(",", 1)[1], validate=True)
    with Image.open(io.BytesIO(raw)) as image:
        return root, image.size, image.convert("RGBA").tobytes()


@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg", "tif", "tiff"])
def test_existing_raster_families_export_native_grid_and_static_provenance(project, extension):
    root, _ = project
    name = "source." + extension
    expected = _image(root / name)
    original = (root / name).read_bytes()
    body, outputs, paths = _run(project, _request(name))
    assert body["status"] == "done"
    svg, pixels, actual = _embedded(paths["svg"])
    assert pixels == (16, 12)
    assert actual == expected
    assert [float(x) for x in svg.get("viewBox").split()][2:] == pytest.approx(
        [32 * 72 / 25.4, 24 * 72 / 25.4], abs=1e-5
    )
    assert outputs["svg"]["vector"] is False
    manifest = outputs["svg"]["manifest"]
    assert not manifest.get("receipts")
    assert "execution" not in manifest.get("provenance", {})
    source = manifest["provenance"]["sources"][0]
    assert source["origin"] == "static"
    assert source["source_id"] == name
    assert source["bytes_sha256"] == hashlib.sha256(original).hexdigest()
    assert source.get("receipt_id") is None


def test_original_svg_preserves_alpha_even_when_original_background_option_is_white(project):
    root, _ = project
    expected = _image(root / "alpha.png")
    _, _, paths = _run(project, _request("alpha.png"))
    _, _, actual = _embedded(paths["svg"])
    assert actual == expected
    converted = root / "alpha-svg.pdf"
    pdfbackend.original_pdf(paths["svg"], converted)
    with pdfium.PdfDocument(converted) as pdf:
        page = pdf[0]
        bitmap = page.render(scale=16 / page.get_width(), fill_color=(0, 0, 0, 0))
        try:
            sample = bitmap.to_pil().convert("RGBA")
            assert sample.size == (16, 12)
            assert sample.getpixel((0, 5))[3] == 0
            assert sample.getpixel((14, 5))[3] == 255
        finally:
            bitmap.close()
            page.close()


@pytest.mark.parametrize("density", [None, (300, 150)])
def test_missing_physical_hint_uses_original_spec_density_not_export_ppi(project, density):
    root, _ = project
    _image(root / "density.png", dpi=density)
    expected = m._asset_original_spec(root / "density.png", "raster")
    spec = _request("density.png", w_mm=None, h_mm=None)
    _, outputs, paths = _run(project, spec)
    svg, pixels, _ = _embedded(paths["svg"])
    assert pixels == (16, 12)
    assert outputs["svg"]["dimensions"]["mm"] == pytest.approx(
        [expected["logical_w_mm"], expected["logical_h_mm"]], abs=0.001
    )
    assert [float(x) for x in svg.get("viewBox").split()][2:] == pytest.approx(
        [expected["logical_w_mm"] * 72 / 25.4, expected["logical_h_mm"] * 72 / 25.4], abs=1e-5
    )


def test_png_copy_and_other_original_formats_share_frozen_bytes_after_disk_change(
    project, monkeypatch
):
    root, _ = project
    expected = _image(root / "source.png", alpha=False)
    original = (root / "source.png").read_bytes()
    original_pdf = pdfbackend.original_pdf

    def change_after_pdf(source, target, *args, **kwargs):
        facts = original_pdf(source, target, *args, **kwargs)
        Image.new("RGB", (16, 12), (10, 20, 245)).save(root / "source.png")
        return facts

    monkeypatch.setattr(pdfbackend, "original_pdf", change_after_pdf)
    body, outputs, paths = _run(
        project, _request("source.png", formats=("pdf", "png", "tiff", "svg"))
    )
    assert body["status"] == "done"
    assert (root / "source.png").read_bytes() != original
    assert paths["png"].read_bytes() == original
    assert _embedded(paths["svg"])[2] == expected
    with Image.open(paths["tiff"]) as image:
        assert image.size == (16, 12)
        assert image.convert("RGBA").tobytes() == expected
    for output in outputs.values():
        source = output["manifest"]["provenance"]["sources"][0]
        assert source["bytes_sha256"] == hashlib.sha256(original).hexdigest()


def test_changed_bytes_between_freeze_and_read_are_rejected_before_publication(
    project, monkeypatch
):
    root, client = project
    _image(root / "source.png")
    read = sources.read_frozen
    changed = False

    def replace_before_read(source):
        nonlocal changed
        if source.path == root / "source.png" and not changed:
            changed = True
            Image.new("RGB", (16, 12), (1, 2, 3)).save(source.path)
        return read(source)

    monkeypatch.setattr(sources, "read_frozen", replace_before_read)
    response = client.post("/api/export", json=_request("source.png", formats=("png", "svg")))
    assert response.status_code >= 400
    assert changed
    assert "source_changed" in str(response.get_json())
    assert not list((root / "tavottofile/export").glob("raster-original.*"))


def test_existing_source_bytes_budget_is_applied_before_any_frozen_read(project, monkeypatch):
    root, client = project
    _image(root / "source.png")
    monkeypatch.setattr(rc_job, "SOURCE_BYTES_BUDGET", (root / "source.png").stat().st_size * 2 - 1)

    def deny(*args, **kwargs):
        pytest.fail("Budget must reject before allocating source bytes")

    monkeypatch.setattr(sources, "read_frozen", deny)
    response = client.post("/api/export", json=_request("source.png"))
    assert response.status_code >= 400
    assert "source_budget_exceeded" in str(response.get_json())


def test_annotations_and_padding_keep_original_image_and_same_scene_size(project):
    root, _ = project
    expected = _image(root / "source.png")
    spec = _request(
        "source.png",
        formats=("pdf", "png", "svg"),
        px_w=16,
        px_h=12,
        pad_mm=[2, 3, 4, 5],
        objects=[
            {
                "type": "shape",
                "id": "overlay",
                "kind": "rect",
                "x_mm": 10,
                "y_mm": 9,
                "w_mm": 4,
                "h_mm": 3,
                "fill": "#20dd40",
                "stroke": "none",
                "stroke_width_pt": 0,
            }
        ],
    )
    body, outputs, paths = _run(project, spec)
    assert body["status"] == "done"
    assert _embedded(paths["svg"])[1:] == ((16, 12), expected)
    with pdfium.PdfDocument(paths["pdf"]) as pdf:
        assert [value * 25.4 / 72 for value in pdf[0].get_size()] == pytest.approx(
            [38, 32], abs=0.001
        )
    for fmt in ("pdf", "png", "svg"):
        assert outputs[fmt]["dimensions"]["mm"] == pytest.approx([38, 32])


def test_static_pdf_without_companion_is_not_rasterized_into_an_svg(project):
    import pikepdf

    root, _ = project
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        pdf.save(root / "opaque.pdf")
    body, outputs, paths = _run(
        project, _request("opaque.pdf", formats=("pdf", "svg"), source_kind="vector")
    )
    assert body["status"] == "partial"
    assert outputs["pdf"]["status"] == "done"
    assert outputs["svg"]["error"]["code"] == "svg_source_unsupported"
    assert "svg" not in paths
