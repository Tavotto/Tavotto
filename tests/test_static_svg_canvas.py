"""Raw static SVG sources use the original canvas job without execution provenance.

The hand-written physical coordinates and independent PDFium reads describe the
final artifact. The tiny no-font PDF golden predates the static SVG adapter;
tests never load old source or generate their expected bytes with the writer.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
import struct
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from tavotto.engine import exportjob, exportroute
from tavotto.rendercore import (
    fonts,
    ir,
    job,
    pdfwriter,
    plan,
    raster,
    renderhost,
    sources,
    svgwriter,
)
from tavotto.rendercore.hbshaper import CandidatePackagesMissing, HbFaceProvider, require


@pytest.fixture(scope="module")
def provider():
    try:
        require("pikepdf", "fontTools", "uharfbuzz")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"RenderCore dependencies unavailable (not_run): {exc}")
    if importlib.util.find_spec("pypdfium2") is None:
        pytest.skip("pypdfium2 unavailable (not_run)")
    registry = fonts.FontRegistry.discover()
    if registry.missing:
        pytest.skip(f"Approved fonts unavailable (not_run): {registry.missing}")
    return HbFaceProvider(registry)


@pytest.fixture(autouse=True)
def reset_jobs():
    exportjob.reset_for_tests()
    yield
    exportjob.reset_for_tests()


ORDINARY = b'<svg xmlns="http://www.w3.org/2000/svg" width="120pt" height="60pt" viewBox="0 0 120 60"><rect width="120" height="60" fill="#ffe8a0"/><circle cx="30" cy="30" r="18" fill="#007799"/><text x="55" y="35" font-size="10" font-family="Liberation Sans">ordinary SVG</text></svg>'
RING = b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100"><defs><clipPath id="ring"><path clip-rule="evenodd" d="M10 10H90V90H10Z M40 40H60V60H40Z"/></clipPath></defs><g id="alpha-ring" opacity=".5" clip-path="url(#ring)"><rect width="100" height="100" fill="red"/><rect x="50" width="50" height="100" fill="red"/></g><text x="2" y="98" font-size="8" font-family="sans-serif">Clip \xe4\xb8\xad\xe6\x96\x87</text></svg>'
EMBEDDED = b'<svg xmlns="http://www.w3.org/2000/svg" width="40pt" height="20pt" viewBox="0 0 40 20"><g opacity=".5"><image width="40" height="20" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAADklEQVR4nGP4z8AAQg0AD3oDfnfpf5cAAAAASUVORK5CYII="/></g></svg>'
NONZERO_MEET = b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="50pt" viewBox="10 20 100 100" preserveAspectRatio="xMidYMid meet"><rect x="10" y="20" width="100" height="100" fill="red"/></svg>'

UNSUPPORTED = {
    "gradient": b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100"><defs><linearGradient id="g"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/></svg>',
    "mask": b'<svg xmlns="http://www.w3.org/2000/svg" width="100pt" height="100pt" viewBox="0 0 100 100"><defs><mask id="m"><rect width="100" height="100" fill="white"/></mask></defs><rect width="100" height="100" fill="red" mask="url(#m)"/></svg>',
}

# Fixed output of the pre-adapter PdfWriter, source SHA256
# f2bbafda7459f8e32630f860449424ccddb002d077dd3761894d378f15a54c67.
# PDF SHA256 d5a67a47db5670e5343477b95113ebb6c7a568ad97e02eb1e0370d3a676b4d28; no fonts/images/version metadata.
_OLD_NO_SVG_PDF = base64.b64decode(
    "JVBERi0xLjUKJb/3ov4KMSAwIG9iago8PCAvUGFnZXMgMiAwIFIgL1R5cGUgL0NhdGFsb2cgPj4KZW5kb2JqCjIgMCBvYmoK"
    "PDwgL0NvdW50IDEgL0tpZHMgWyAzIDAgUiBdIC9UeXBlIC9QYWdlcyA+PgplbmRvYmoKMyAwIG9iago8PCAvQ29udGVudHMg"
    "NCAwIFIgL01lZGlhQm94IFsgMCAwIDEyMCA2MCBdIC9QYXJlbnQgMiAwIFIgL1Jlc291cmNlcyA8PCA+PiAvVHlwZSAvUGFn"
    "ZSA+PgplbmRvYmoKNCAwIG9iago8PCAvRmlsdGVyIC9GbGF0ZURlY29kZSAvTGVuZ3RoIDU2ID4+CnN0cmVhbQp4nCtUMATD"
    "onQFAyA0NDJQMDNQKEpVSFMI5CoESoBEgZKmQJirYGoApHJAlIkBkM4AqwIA5XEORQplbmRzdHJlYW0KZW5kb2JqCnhyZWYK"
    "MCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMDY0IDAwMDAwIG4gCjAwMDAwMDAx"
    "MjMgMDAwMDAgbiAKMDAwMDAwMDIyOCAwMDAwMCBuIAp0cmFpbGVyIDw8IC9Sb290IDEgMCBSIC9TaXplIDUgL0lEIFs8NTY3"
    "NDI3N2JmYTM5NmViZDY2ODFjOGNiYzkzNzQ5NDY+PDU2NzQyNzdiZmEzOTZlYmQ2NjgxYzhjYmM5Mzc0OTQ2Pl0gPj4Kc3Rh"
    "cnR4cmVmCjM1NQolJUVPRgo="
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _tiny_png():
    def chunk(kind, raw):
        return struct.pack(">I", len(raw)) + kind + raw + struct.pack(">I", zlib.crc32(kind + raw))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff\0\0\xff\x80"))
        + chunk(b"IEND", b"")
    )


def _source(body, *, width=100, height=100, attrs='viewBox="0 0 100 100"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" {attrs}>{body}</svg>'.encode()


def _read_pdf(path, samples, *, page_mm):
    import pikepdf
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as document:
        assert len(document) == 1
        page = document[0]
        size = list(page.get_size())
        assert all(
            abs(actual - mm * 72 / 25.4) < 0.01 for actual, mm in zip(size, page_mm, strict=True)
        )
        text = page.get_textpage()
        extracted = text.get_text_range()
        text.close()
        bitmap = page.render(scale=2)
        image = bitmap.to_pil().convert("RGB")
        pixels = []
        for x_mm, y_mm, wanted in samples:
            x, y = round(x_mm * 72 / 25.4 * 2), round(y_mm * 72 / 25.4 * 2)
            actual = image.getpixel((x, y))
            assert all(abs(a - b) <= 2 for a, b in zip(actual, wanted, strict=True)), (
                x_mm,
                y_mm,
                actual,
                wanted,
            )
            pixels.append({"mm": [x_mm, y_mm], "actual_rgb": actual, "expected_rgb": wanted})
        bitmap.close()
        page.close()
    with pikepdf.open(path) as document:
        image_sizes = [
            list((int(obj.Width), int(obj.Height)))
            for obj in document.objects
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image"
        ]
        form_count = sum(
            isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Form"
            for obj in document.objects
        )
    return {
        "size_pt": size,
        "text": extracted,
        "pixels": pixels,
        "image_sizes": image_sizes,
        "form_count": form_count,
        "sha256": sha(path.read_bytes()),
    }


def compile_raw(tmp_path, provider, data, *, w=40, h=40, opacity=1, objects=None):
    source = tmp_path / "source.svg"
    source.write_bytes(data)
    obj = {
        "type": "panel",
        "id": "source.svg",
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": w,
        "h_mm": h,
        "opacity": opacity,
        "overrides": [],
    }
    compiled = plan.compile_page(
        100,
        70,
        objects or [obj],
        sources=sources.StaticSourceResolver(tmp_path),
        faces=provider,
        background=(1, 1, 1),
    )
    files = {key: sources.read_frozen(fs) for key, fs in compiled.sources.items()}
    svgs = {
        key: sources.read_frozen(fs.svg_representation()) for key, fs in compiled.sources.items()
    }
    assert all(isinstance(child, ir.ImportedPage) for child in compiled.page.children)
    assert all(
        fs.artifact.origin == "static"
        and fs.artifact.kind == "svg"
        and fs.artifact.receipt_id is None
        and fs.artifact.receipt_identity is None
        and fs.artifact.generation is None
        and fs.artifact.patch_hash is None
        and fs.artifact.render_revision is None
        and fs.artifact.bytes_sha256 == sha(fs.path.read_bytes())
        and fs.svg is None
        and fs.receipt is None
        and fs.svg_representation() is fs
        for fs in compiled.sources.values()
    )
    assert not ir.unsupported_for(compiled.page, "svg")
    return compiled, files, svgs


def test_original_ordinary_raw_identity_and_independent_pdf(tmp_path, provider):
    data = ORDINARY
    compiled, files, svgs = compile_raw(tmp_path, provider, data, w=48, h=24)
    out = tmp_path / "canvas.pdf"
    facts = pdfwriter.write_pdf(compiled.page, out, provider, files)
    measured = _read_pdf(
        out, [(22, 22, (0, 119, 153)), (12, 12, (255, 232, 160))], page_mm=(100, 70)
    )
    assert measured["text"] == "ordinary SVG" and measured["image_sizes"] == []
    assert facts.imported_pages[0]["native_svg"]["source_sha256"] == sha(data)
    assert facts.imported_pages[0]["native_svg"]["text_objects"] == 1
    svgwriter.write_svg(compiled.page, tmp_path / "out.svg", provider, files, imported_svgs=svgs)
    assert [
        el.text for el in ET.parse(tmp_path / "out.svg").iter() if el.tag.endswith("}text")
    ] == ["ordinary SVG"]


def test_group_alpha_clip_holes_chinese_and_panel_alpha(tmp_path, provider):
    data = RING
    compiled, files, _ = compile_raw(tmp_path, provider, data, opacity=0.5)
    out = tmp_path / "ring.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider, files)
    measured = _read_pdf(
        out,
        [
            (20, 20, (255, 191, 191)),
            (40, 20, (255, 191, 191)),
            (30, 30, (255, 255, 255)),
            (12, 12, (255, 255, 255)),
        ],
        page_mm=(100, 70),
    )
    assert "Clip 中文" in measured["text"] and measured["image_sizes"] == []


def test_original_embedded_image_bytes_alpha_are_handed_off(tmp_path, provider):
    compiled, files, _ = compile_raw(tmp_path, provider, EMBEDDED, h=20)
    out = tmp_path / "embedded.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider, files)
    measured = _read_pdf(
        out, [(20, 20, (255, 128, 128)), (40, 20, (191, 191, 255))], page_mm=(100, 70)
    )
    assert measured["image_sizes"] == [[2, 1], [2, 1]]


def test_nonzero_viewbox_meet_physical_frame_both_writers(tmp_path, provider):
    compiled, files, svgs = compile_raw(tmp_path, provider, NONZERO_MEET, h=20)
    out = tmp_path / "meet.pdf"
    pdfwriter.write_pdf(compiled.page, out, provider, files)
    _read_pdf(
        out,
        [(12, 20, (255, 255, 255)), (30, 20, (255, 0, 0)), (48, 20, (255, 255, 255))],
        page_mm=(100, 70),
    )
    svgwriter.write_svg(compiled.page, tmp_path / "out.svg", provider, files, imported_svgs=svgs)
    nested = [el for el in ET.parse(tmp_path / "out.svg").iter() if el.tag.endswith("}svg")][1]
    assert nested.get("viewBox") == "10 20 100 100"
    assert nested.get("preserveAspectRatio") == "xMidYMid meet"
    assert nested.get("width") == "100" and nested.get("height") == "50"


@pytest.mark.parametrize("family", ["gradient", "mask"])
def test_per_format_failure_preserves_original_svg_representation(tmp_path, provider, family):
    (tmp_path / "source.svg").write_bytes(UNSUPPORTED[family])
    request = exportjob.prepare(
        {
            "scope": "canvas",
            "formats": ["pdf", "png", "svg"],
            "ppi": 72,
            "filename": "partial",
            "overwrite": "replace",
            "canvas": {
                "page_w_mm": 100,
                "page_h_mm": 70,
                "objects": [
                    {
                        "type": "panel",
                        "id": "source.svg",
                        "x_mm": 10,
                        "y_mm": 10,
                        "w_mm": 40,
                        "h_mm": 40,
                        "overrides": [],
                    }
                ],
            },
        },
        tmp_path / "out",
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    result = job.produce(
        request, stage, sources=sources.StaticSourceResolver(tmp_path), provider=provider
    )
    indexed = {item.format: item for item in result}
    assert indexed["svg"].tmp_path.is_file()
    assert indexed["pdf"].error_code == indexed["png"].error_code == "format_failed"
    assert indexed["pdf"].error_params["reason"] == "svgir_" + family
    assert not (stage / "out.pdf").exists()
    assert ET.parse(indexed["svg"].tmp_path).getroot().tag.endswith("}svg")


def test_two_svg_child_writers_share_existing_document_raster_budget(
    tmp_path, provider, monkeypatch
):
    encoded = base64.b64encode(_tiny_png()).decode()
    data = _source(f'<image width="100" height="100" href="data:image/png;base64,{encoded}"/>')
    (tmp_path / "source.svg").write_bytes(data)
    (tmp_path / "second.svg").write_bytes(
        data.replace(b"<image", b"<!-- second raw identity --><image")
    )
    objects = [
        {"type": "panel", "id": name, "x_mm": x, "y_mm": 10, "w_mm": 20, "h_mm": 20}
        for name, x in (("source.svg", 10), ("second.svg", 40))
    ]
    compiled, files, _ = compile_raw(tmp_path, provider, data, objects=objects)
    monkeypatch.setattr(raster, "DOCUMENT_MAX_PIXELS", 3)
    with pytest.raises(pdfwriter.WriterError) as failure:
        pdfwriter.write_pdf(compiled.page, tmp_path / "out.pdf", provider, files)
    assert failure.value.params["why"] == "raster_budget_exceeded"
    assert failure.value.params["pixels_used"] == 2
    assert not (tmp_path / "out.pdf").exists()


def test_writer_rechecks_original_raw_hash_before_parsing(tmp_path, provider):
    data = ORDINARY
    compiled, files, svgs = compile_raw(tmp_path, provider, data)
    frozen = next(iter(compiled.sources.values()))
    frozen.path.write_bytes(data.replace(b"ordinary SVG", b"changed  SVG"))
    with pytest.raises(sources.SourceError) as changed:
        sources.read_frozen(frozen)
    assert changed.value.code == "source_changed"
    frozen.path.write_bytes(data)
    key = next(iter(files))
    files[key] = data.replace(b"ordinary SVG", b"changed  SVG")
    with pytest.raises(pdfwriter.WriterError, match="sha256") as failure:
        pdfwriter.write_pdf(compiled.page, tmp_path / "out.pdf", provider, files)
    assert failure.value.code == "source_identity"
    with pytest.raises(svgwriter.WriterError) as failure:
        svgwriter.write_svg(
            compiled.page, tmp_path / "out.svg", provider, files, imported_svgs=svgs
        )
    assert failure.value.code == "source_identity"


def test_static_svg_overrides_still_require_execution(tmp_path, provider):
    (tmp_path / "source.svg").write_bytes(ORDINARY)
    with pytest.raises(sources.SourceError) as failure:
        sources.StaticSourceResolver(tmp_path).resolve(
            {"id": "source.svg", "overrides": [{"x": 1}]}
        )
    assert failure.value.code == "source_needs_execution"


def test_existing_pdf_writer_default_bytes_unchanged(tmp_path, provider):
    page = ir.Page(
        120,
        60,
        (ir.Path((("M", 5, 5), ("L", 50, 5), ("L", 50, 40), ("Z",)), fill=ir.Paint((1, 0, 0))),),
    )
    out = tmp_path / "unchanged.pdf"
    pdfwriter.write_pdf(page, out, provider)
    assert out.read_bytes() == _OLD_NO_SVG_PDF
    measured = _read_pdf(out, [], page_mm=(120 * 25.4 / 72, 60 * 25.4 / 72))
    assert measured["image_sizes"] == [] and measured["text"] == ""


def test_backend_route_attempts_canvas_conversion_keeps_original_and_eps(tmp_path):
    route = exportroute.panel_route(
        scope="canvas",
        formats=["pdf", "png", "tiff", "svg", "eps"],
        file_kind="svg",
        overrides=[],
        has_script=False,
    )
    assert route["source"] == "disk" and route["embeds_disk_bitmap"] is False
    assert all(route["formats"][fmt] == {"how": "convert"} for fmt in ("pdf", "png", "tiff", "svg"))
    assert route["formats"]["eps"] == {"how": "unavailable", "reason": "eps_not_for_canvas"}
    original = exportroute.panel_route(
        scope="original",
        formats=["svg", "pdf", "eps"],
        file_kind="svg",
        overrides=[],
        has_script=False,
    )
    assert original["formats"]["svg"] == {"how": "copy"}
    assert original["formats"]["pdf"] == {"how": "convert"}
    assert original["formats"]["eps"]["reason"] == "eps_needs_script"


def test_real_original_job_pdf_png_tiff_from_one_raw_static_source(tmp_path, provider):
    from PIL import Image

    (tmp_path / "source.svg").write_bytes(ORDINARY)
    request = exportjob.prepare(
        {
            "scope": "canvas",
            "formats": ["pdf", "png", "tiff", "svg"],
            "ppi": 72,
            "filename": "native",
            "overwrite": "replace",
            "canvas": {
                "page_w_mm": 100,
                "page_h_mm": 70,
                "objects": [
                    {
                        "type": "panel",
                        "id": "source.svg",
                        "x_mm": 10,
                        "y_mm": 10,
                        "w_mm": 48,
                        "h_mm": 24,
                        "overrides": [],
                    }
                ],
            },
        },
        tmp_path / "out",
    )
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join([str(root / "src"), env.get("PYTHONPATH", "")])
    (tmp_path / "rgba").mkdir()
    host = renderhost.RenderHost(env=env, scratch_dir=tmp_path / "rgba")
    try:
        result = exportjob.run(
            request,
            lambda j, stage: job.produce(
                j,
                stage,
                sources=sources.StaticSourceResolver(tmp_path),
                provider=provider,
                host=host,
            ),
        )
    finally:
        host.close()
    assert result["status"] == "done", result
    assert len(result["outputs"]) == 4 and all(
        item["status"] == "done" for item in result["outputs"]
    )
    assert any("mapped to" in message for message in result["warnings"])
    paths = {item["format"]: tmp_path / "out" / item["name"] for item in result["outputs"]}
    with Image.open(paths["png"]) as png, Image.open(paths["tiff"]) as tiff:
        assert png.size == tiff.size == (283, 198)
        assert png.convert("RGB").tobytes() == tiff.convert("RGB").tobytes()
    _read_pdf(paths["pdf"], [(22, 22, (0, 119, 153)), (12, 12, (255, 232, 160))], page_mm=(100, 70))
