"""Original SVG formats share frozen raw identity and intrinsic physical geometry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto import app as m, pdfbackend, svgasset
from tavotto.engine import exportjob
from tavotto.rendercore import facade, pdfwriter, renderhost, svgir
from test_static_svg_canvas import (
    EMBEDDED,
    NONZERO_MEET,
    ORDINARY,
    UNSUPPORTED,
    _read_pdf,
    provider as provider,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects(wait=True)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.svg").write_bytes(ORDINARY)
    (root / "canary.py").write_text(
        'from pathlib import Path\ndef main():\n    Path("counter.txt").write_text("executed")\n',
        encoding="utf-8",
    )
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "canary.py": {
                        "entry": "main",
                        "stems": ["source"],
                        "cost": "light",
                        "notes": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield root
    m.reset_projects(wait=True)
    renderhost.shutdown_shared()
    facade.reset_for_tests()
    exportjob.reset_for_tests()


def _run(root, *, formats=("pdf", "png", "tiff", "svg"), background="white", ppi=144):
    spec = {
        "scope": "original",
        "filename": "original",
        "formats": list(formats),
        "overwrite": "replace",
        "ppi": ppi,
        "background": background,
        "original": {
            "figure_id": "source.svg",
            "overrides": [],
            "source_kind": "vector",
            "w_mm": 900,
            "h_mm": 800,
        },
    }
    job = exportjob.prepare(spec, root / "exports")
    captured = []

    def produce(actual_job, stage):
        actual = m._export_produce(actual_job, stage)
        captured.extend(actual)
        return actual

    result = exportjob.run(job, produce)
    outputs = {item["format"]: item for item in result["outputs"]}
    paths = {
        fmt: root / "exports" / item["name"]
        for fmt, item in outputs.items()
        if item["status"] == "done"
    }
    return result, outputs, paths, captured


@pytest.mark.parametrize("with_svg", [True, False])
def test_original_job_same_raw_basis_zero_execution_and_natural_size(project, provider, with_svg):
    from PIL import Image

    formats = ["pdf", "png", "eps", "tiff"] + (["svg"] if with_svg else [])
    result, outputs, paths, captured = _run(project, formats=formats)
    assert result["status"] == "partial", result
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"
    assert all(outputs[fmt]["status"] == "done" for fmt in formats if fmt != "eps")
    assert not (project / "counter.txt").exists()
    raw_sha = hashlib.sha256(ORDINARY).hexdigest()
    for item in captured:
        if item.error_code is not None:
            continue
        assert item.basis.source_sha256 == raw_sha
        assert item.basis.how == ("copy" if item.format == "svg" else "convert")
    for fmt, item in outputs.items():
        if fmt == "eps":
            continue
        artifact = item["manifest"]["provenance"]["sources"][0]
        assert artifact["source_id"] == "source.svg" and artifact["origin"] == "static"
        assert artifact["kind"] == "svg" and artifact["bytes_sha256"] == raw_sha
        assert artifact["receipt_id"] is None and artifact["receipt_identity"] is None
        assert item["dimensions"]["mm"] == pytest.approx([120 * 25.4 / 72, 60 * 25.4 / 72])
    measured = _read_pdf(
        paths["pdf"],
        [(30 * 25.4 / 72, 30 * 25.4 / 72, (0, 119, 153)), (1, 1, (255, 232, 160))],
        page_mm=(120 * 25.4 / 72, 60 * 25.4 / 72),
    )
    assert measured["text"] == "ordinary SVG" and measured["image_sizes"] == []
    with Image.open(paths["png"]) as png, Image.open(paths["tiff"]) as tiff:
        assert png.size == tiff.size == (240, 120)
        assert png.convert("RGBA").tobytes() == tiff.convert("RGBA").tobytes()
    if with_svg:
        assert paths["svg"].read_bytes() == ORDINARY


def test_public_original_pdf_uses_intrinsic_svg_frame_not_page_hint(tmp_path, provider):
    source = tmp_path / "source.svg"
    source.write_bytes(ORDINARY)
    out = tmp_path / "source.pdf"
    facts = pdfbackend.original_pdf(source, out, (900, 800))
    assert (facts["w_pt"], facts["h_pt"]) == (120, 60)
    assert facts["vector"] is True and facts["px_w"] is facts["px_h"] is None
    assert any("mapped to" in note for note in facts["notes"])
    measured = _read_pdf(
        out,
        [(30 * 25.4 / 72, 30 * 25.4 / 72, (0, 119, 153))],
        page_mm=(120 * 25.4 / 72, 60 * 25.4 / 72),
    )
    assert measured["text"] == "ordinary SVG"


def test_public_original_png_tiff_preserve_embedded_alpha_and_bytes(tmp_path, provider):
    from PIL import Image

    source = tmp_path / "source.svg"
    source.write_bytes(EMBEDDED)
    png, tiff = tmp_path / "source.png", tmp_path / "source.tiff"
    png_facts = pdfbackend.original_png(source, png, 144, transparent=True)
    tiff_facts = pdfbackend.original_tiff(source, tiff, 144, transparent=True)
    assert (png_facts["px_w"], png_facts["px_h"]) == (80, 40)
    assert tiff_facts == png_facts
    assert png_facts["notes"] == []
    with Image.open(png) as first, Image.open(tiff) as second:
        first_rgba, second_rgba = first.convert("RGBA"), second.convert("RGBA")
        assert first_rgba.tobytes() == second_rgba.tobytes()
        assert first_rgba.getpixel((10, 20)) == pytest.approx((255, 0, 0, 128), abs=1)
        assert first_rgba.getpixel((60, 20)) == pytest.approx((0, 0, 255, 64), abs=1)


def test_original_nonzero_viewbox_preserves_physical_meet_and_white_margins(project, provider):
    (project / "source.svg").write_bytes(NONZERO_MEET)
    result, _, paths, _ = _run(project)
    assert result["status"] == "done"
    _read_pdf(
        paths["pdf"],
        [
            (5 * 25.4 / 72, 25 * 25.4 / 72, (255, 255, 255)),
            (50 * 25.4 / 72, 25 * 25.4 / 72, (255, 0, 0)),
            (95 * 25.4 / 72, 25 * 25.4 / 72, (255, 255, 255)),
        ],
        page_mm=(100 * 25.4 / 72, 50 * 25.4 / 72),
    )


@pytest.mark.parametrize("family", ["gradient", "mask"])
def test_original_unexpressed_paints_fail_only_requested_conversions(project, provider, family):
    (project / "source.svg").write_bytes(UNSUPPORTED[family])
    result, outputs, paths, _ = _run(project)
    assert result["status"] == "partial"
    assert set(paths) == {"svg"}
    assert paths["svg"].read_bytes() == UNSUPPORTED[family]
    for fmt in ("pdf", "png", "tiff"):
        assert outputs[fmt]["status"] == "failed"
        assert outputs[fmt]["error"]["code"] == "format_failed"
        assert outputs[fmt]["error"]["params"]["reason"] == "svgir_" + family
    assert not (project / "counter.txt").exists()


def test_original_freeze_precedes_conversion_and_raw_mutation_cannot_mix_sources(
    project, provider, monkeypatch
):
    original = pdfbackend.original_pdf
    seen = []

    def during_conversion(src, out, page_pt=None):
        seen.append(Path(src).read_bytes())
        (project / "source.svg").write_bytes(ORDINARY.replace(b"#007799", b"#ff0000"))
        return original(src, out, page_pt)

    monkeypatch.setattr(pdfbackend, "original_pdf", during_conversion)
    result, _, paths, captured = _run(project)
    assert result["status"] == "done"
    assert seen == [ORDINARY]
    assert paths["svg"].read_bytes() == ORDINARY
    assert all(
        item.basis.source_sha256 == hashlib.sha256(ORDINARY).hexdigest() for item in captured
    )
    measured = _read_pdf(
        paths["pdf"],
        [(30 * 25.4 / 72, 30 * 25.4 / 72, (0, 119, 153))],
        page_mm=(120 * 25.4 / 72, 60 * 25.4 / 72),
    )
    assert measured["text"] == "ordinary SVG"


def test_stock_matplotlib_doctype_normalization_keeps_original_raw_basis(project, provider):
    declaration = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"\n  "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
    raw = declaration + ORDINARY
    (project / "source.svg").write_bytes(raw)
    old_data, old_facts = svgasset.read(project / "source.svg")
    result, outputs, paths, captured = _run(project)
    assert result["status"] == "done"
    assert paths["svg"].read_bytes() == old_data == ORDINARY
    assert (old_facts["w_pt"], old_facts["h_pt"]) == (120, 60)
    assert all(item.basis.source_sha256 == hashlib.sha256(raw).hexdigest() for item in captured)
    assert all(
        item["manifest"]["provenance"]["sources"][0]["bytes_sha256"]
        == hashlib.sha256(raw).hexdigest()
        for item in outputs.values()
    )


def test_canvas_multiple_static_svg_id_css_crop_rotation_and_opacity_remain_separate(
    project, provider
):
    body = '<style>#paint .top{fill:lime}#paint .bottom{fill:yellow}</style><g id="paint" opacity=".5"><path class="top" d="M0 0H100V25H0Z"/><path class="bottom" d="M0 25H100V50H0Z"/></g>'
    source = f'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="50pt" viewBox="0 0 100 50">{body}</svg>'.encode()
    (project / "source.svg").write_bytes(source)
    (project / "other.svg").write_bytes(source.replace(b"lime", b"red").replace(b"yellow", b"blue"))
    objects = [
        {
            "type": "panel",
            "id": "source.svg",
            "x_mm": 10,
            "y_mm": 10,
            "w_mm": 20,
            "h_mm": 20,
            "crop": {"x": 0.5, "y": 0, "w": 0.5, "h": 1},
            "flip_v": True,
            "rotation": 90,
            "opacity": 0.5,
        },
        {"type": "panel", "id": "other.svg", "x_mm": 40, "y_mm": 10, "w_mm": 40, "h_mm": 20},
    ]
    job = exportjob.prepare(
        {
            "scope": "canvas",
            "filename": "instances",
            "formats": ["pdf", "svg"],
            "overwrite": "replace",
            "canvas": {"page_w_mm": 100, "page_h_mm": 70, "objects": objects},
        },
        project / "exports",
    )
    result = exportjob.run(job, m._export_produce)
    assert result["status"] == "done", result
    paths = {item["format"]: project / "exports" / item["name"] for item in result["outputs"]}
    measured = _read_pdf(
        paths["pdf"],
        [
            (15, 20, (191, 255, 191)),
            (25, 20, (255, 255, 191)),
            (50, 15, (255, 128, 128)),
            (50, 25, (128, 128, 255)),
        ],
        page_mm=(100, 70),
    )
    assert measured["image_sizes"] == []
    root = ET.parse(paths["svg"]).getroot()
    ids = [node.get("id") for node in root.iter() if node.get("id")]
    assert len(ids) == len(set(ids))
    assert len(list(root.iter("{http://www.w3.org/2000/svg}style"))) == 2
    # The assembled SVG is consumed as a complete scene. This exercises its
    # rewritten CSS/IDs and placements, rather than inferring them from the PDF.
    image_files = {}
    page, notes = svgir.svg_to_ir(
        paths["svg"].read_bytes(),
        100 * 72 / 25.4,
        70 * 72 / 25.4,
        facade.provider(),
        files=image_files,
    )
    assert notes == []
    replay = project / "assembled-svg.pdf"
    pdfwriter.write_pdf(page, replay, facade.provider(), image_files)
    _read_pdf(
        replay,
        [
            (15, 20, (191, 255, 191)),
            (25, 20, (255, 255, 191)),
            (50, 15, (255, 128, 128)),
            (50, 25, (128, 128, 255)),
        ],
        page_mm=(100, 70),
    )
