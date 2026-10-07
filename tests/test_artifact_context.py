"""Selected disk admission: real decoded sources, no original writes or worker imports."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, PngImagePlugin

from tavotto import pdfbackend
from tavotto.engine import artifactcontext as ac


@pytest.fixture(scope="module", autouse=True)
def reap_renderer():
    yield
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()


def png(path, *, dpi=100, size=(73, 47), pixel=None, metadata="first"):
    im = Image.new("RGBA", size, (30, 50, 80, 90))
    if pixel is not None:
        im.putpixel((2, 3), pixel)
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Description", metadata)
    opts = {"dpi": (dpi, dpi)} if dpi is not None else {}
    im.save(path, pnginfo=meta, **opts)
    return path


def pdf(path, *, size=(72, 48), pages=1, content=b"1 0 0 rg 10 10 20 20 re f", title="first"):
    with pikepdf.new() as doc:
        for _ in range(pages):
            page = doc.add_blank_page(page_size=size)
            page.Contents = doc.make_stream(content)
        doc.docinfo["/Title"] = title
        doc.save(path)
    return path


def failure(reason, call):
    with pytest.raises(ac.ArtifactContextError) as exc:
        call()
    assert exc.value.code == "artifact_source_unavailable"
    assert exc.value.reason == reason
    assert exc.value.retryable == (reason == "source_changed")


@pytest.mark.parametrize("kind", ["pdf", "png"])
def test_metadata_changes_accept_exact_pixels_without_original_writes(tmp_path, kind):
    source, candidate = tmp_path / f"a.{kind}", tmp_path / f"b.{kind}"
    factory = pdf if kind == "pdf" else png
    factory(source)
    factory(candidate, **({"title": "second"} if kind == "pdf" else {"metadata": "second"}))
    before = source.read_bytes(), source.stat().st_mtime_ns
    ctx = ac.create_context(source, "nested/plot." + kind)
    assert ctx["origin"] == "static" and ctx["source_id"] == "nested/plot." + kind
    assert ctx["bytes_sha256"] != ac.create_context(candidate, "other")["bytes_sha256"]
    facts = ac.validate_candidate(
        source, candidate, {**ctx, "render_policy": "selected-artifact-v1"}
    )
    assert facts["pixels_equal"] is True and facts["total_pixels"] > 0
    assert facts["format"] == kind
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


@pytest.mark.parametrize("dpi", [72, 100, 300])
def test_png_grid_and_quantized_dpi(tmp_path, dpi):
    source, candidate = png(tmp_path / "a.png", dpi=dpi), png(tmp_path / "b.png", dpi=dpi)
    facts = ac.validate_candidate(source, candidate, ac.create_context(source, "a.png"))
    assert facts["frame"] == {
        "size_px": [73, 47],
        "dpi": (float(dpi), float(dpi)),
        "ppm": (round(dpi / 0.0254),) * 2,
    }


def test_one_rgba_level_is_a_mismatch_even_below_existing_noise_floor(tmp_path):
    a = png(tmp_path / "a.png")
    b = png(tmp_path / "b.png", pixel=(31, 50, 80, 90))
    assert pdfbackend.compare_png(a, b)["changed_pixel_ratio"] == 0
    failure("source_mismatch", lambda: ac.validate_candidate(a, b, ac.create_context(a, "a.png")))


@pytest.mark.parametrize(
    "options", [{"size": (74, 47)}, {"dpi": 101}, {"dpi": None}, {"pixel": (30, 50, 80, 91)}]
)
def test_png_frame_density_and_alpha_mismatch(tmp_path, options):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png", **options)
    failure("source_mismatch", lambda: ac.validate_candidate(a, b, ac.create_context(a, "a.png")))


@pytest.mark.parametrize("options", [{"size": (73, 48)}, {"content": b"0 0 1 rg 10 10 20 20 re f"}])
def test_pdf_frame_or_content_mismatch(tmp_path, options):
    a, b = pdf(tmp_path / "a.pdf"), pdf(tmp_path / "b.pdf", **options)
    failure("source_mismatch", lambda: ac.validate_candidate(a, b, ac.create_context(a, "a.pdf")))


def test_source_change_before_validation_and_publication_gate(tmp_path):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    ctx = ac.create_context(a, "a.png")
    a.write_bytes(a.read_bytes() + b"changed")
    failure("source_changed", lambda: ac.validate_candidate(a, b, ctx))
    failure("source_changed", lambda: ac.assert_current(a, ctx))


def test_source_changes_during_decoded_comparison(tmp_path, monkeypatch):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    ctx = ac.create_context(a, "a.png")
    compare = pdfbackend.compare_png

    def mutate(*args):
        facts = compare(*args)
        a.write_bytes(a.read_bytes() + b"changed")
        return facts

    monkeypatch.setattr(pdfbackend, "compare_png", mutate)
    failure("source_changed", lambda: ac.validate_candidate(a, b, ctx))


@pytest.mark.parametrize(
    "kind,damage", [("png", "truncate"), ("png", "crc"), ("pdf", "truncate"), ("pdf", "garbage")]
)
def test_corrupt_sources_refuse(tmp_path, kind, damage):
    a, b = tmp_path / f"a.{kind}", tmp_path / f"b.{kind}"
    factory = pdf if kind == "pdf" else png
    factory(a)
    factory(b)
    data = a.read_bytes()
    a.write_bytes(
        data[:-12]
        if damage == "truncate"
        else data[:40] + b"?" + data[41:]
        if damage == "crc"
        else b"garbage %%EOF"
    )
    failure("source_unreadable", lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name)))


@pytest.mark.parametrize("target", ["source", "candidate"])
def test_bytes_budget_before_probe_or_decode(tmp_path, monkeypatch, target):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    ctx = ac.create_context(a, "a.png")
    if target == "candidate":
        b.write_bytes(b.read_bytes() + b"large" * 100)
    monkeypatch.setattr(
        ac, "MAX_BYTES", a.stat().st_size - 1 if target == "source" else a.stat().st_size + 1
    )
    monkeypatch.setattr(
        pdfbackend, "probe_asset", lambda *a, **k: pytest.fail("Over-budget input probed")
    )
    failure("validation_budget_exceeded", lambda: ac.validate_candidate(a, b, ctx))


@pytest.mark.parametrize("kind", ["png", "pdf"])
def test_pixel_budget_before_decode_or_render(tmp_path, monkeypatch, kind):
    factory = png if kind == "png" else pdf
    a, b = factory(tmp_path / f"a.{kind}"), factory(tmp_path / f"b.{kind}")
    monkeypatch.setattr(ac, "MAX_PIXELS", 10)
    monkeypatch.setattr(
        pdfbackend, "compare_png", lambda *a: pytest.fail("Over-budget pixels decoded")
    )
    monkeypatch.setattr(
        pdfbackend, "render_preview_png", lambda *a: pytest.fail("Over-budget PDF rendered")
    )
    failure(
        "validation_budget_exceeded",
        lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name)),
    )


@pytest.mark.parametrize("target", ["source", "candidate"])
def test_pdf_page_count_before_render(tmp_path, monkeypatch, target):
    a, b = (
        pdf(tmp_path / "a.pdf", pages=2 if target == "source" else 1),
        pdf(tmp_path / "b.pdf", pages=2 if target == "candidate" else 1),
    )
    monkeypatch.setattr(
        pdfbackend, "render_preview_png", lambda *a: pytest.fail("Multi-page PDF rendered")
    )
    failure(
        "validation_budget_exceeded",
        lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name)),
    )


def test_pdf_probe_page_count_is_opt_in_without_legacy_shape_changes(tmp_path):
    a = pdf(tmp_path / "a.pdf", pages=2)
    legacy = pdfbackend.probe_asset(a, "pdf")
    assert set(legacy) == {"kind", "w_pt", "h_pt"}
    assert pdfbackend.probe_asset(a, "pdf", include_pages=True) == {**legacy, "pages": 2}
    assert pdfbackend.probe_asset(a, "pdf") == legacy


def test_renderer_timeout_is_a_budget_refusal(tmp_path, monkeypatch):
    a, b = pdf(tmp_path / "a.pdf"), pdf(tmp_path / "b.pdf")
    from tavotto.rendercore.renderhost import RenderChildError

    def timeout(*args):
        raise RenderChildError("render_child_timeout", "test deadline")

    monkeypatch.setattr(pdfbackend, "render_preview_png", timeout)
    failure(
        "validation_budget_exceeded",
        lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name)),
    )


def test_import_does_not_load_worker_scientific_or_native_dependencies():
    src = Path(__file__).resolve().parents[1] / "src"
    code = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'matplotlib', 'numpy', 'pikepdf', 'pypdfium2', 'PIL'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from tavotto.engine import artifactcontext
assert not any(n.endswith(('.worker', '.figsession')) for n in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("delta,accepted", [(1, True), (2, False)])
def test_png_density_tolerance_is_exactly_one_metadata_quantum(tmp_path, delta, accepted):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    data = bytearray(b.read_bytes())
    pos = data.index(b"pHYs")
    x, y, unit = struct.unpack_from(">IIB", data, pos + 4)
    struct.pack_into(">IIB", data, pos + 4, x + delta, y + delta, unit)
    struct.pack_into(">I", data, pos + 13, zlib.crc32(data[pos : pos + 13]))
    b.write_bytes(data)
    if accepted:
        assert ac.validate_candidate(a, b, ac.create_context(a, a.name))["pixels_equal"]
    else:
        failure(
            "source_mismatch", lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name))
        )


def test_candidate_format_and_context_hash_mutation_refuse(tmp_path):
    a, b = png(tmp_path / "a.png"), pdf(tmp_path / "b.pdf")
    ctx = ac.create_context(a, a.name)
    failure("source_mismatch", lambda: ac.validate_candidate(a, b, ctx))
    ctx["bytes_sha256"] = "0" * 64
    failure("source_changed", lambda: ac.assert_current(a, ctx))


def test_private_source_snapshot_must_match_context_even_after_source_restored(
    tmp_path, monkeypatch
):
    a, b = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    ctx, original = ac.create_context(a, a.name), a.read_bytes()
    snapshot = ac._snapshot

    def changed_copy(path, out):
        if path == a:
            png(a, pixel=(31, 50, 80, 90))
            try:
                return snapshot(path, out)
            finally:
                a.write_bytes(original)
        return snapshot(path, out)

    monkeypatch.setattr(ac, "_snapshot", changed_copy)
    failure("source_changed", lambda: ac.validate_candidate(a, b, ctx))
    assert a.read_bytes() == original
    assert list((ac.config.data_dir() / "artifact-validation").iterdir()) == []


@pytest.mark.parametrize("variant", ["sixteen_bit", "animated"])
def test_png_forms_that_cannot_be_compared_losslessly_refuse(tmp_path, variant):
    a, b = tmp_path / "a.png", png(tmp_path / "b.png")
    if variant == "sixteen_bit":
        Image.new("I;16", (73, 47), 12345).save(a)
    else:
        first = Image.new("RGBA", (73, 47), (30, 50, 80, 90))
        second = Image.new("RGBA", (73, 47), (50, 30, 80, 90))
        first.save(a, save_all=True, append_images=[second])
    failure("source_unreadable", lambda: ac.validate_candidate(a, b, ac.create_context(a, a.name)))


@pytest.mark.parametrize("publication_gate", [False, True])
def test_growing_source_hash_stops_after_at_most_one_excess_chunk(
    tmp_path, monkeypatch, publication_gate
):
    source = png(tmp_path / "a.png")
    context = ac.create_context(source, source.name)
    limit = (1 << 16) + 4
    monkeypatch.setattr(ac, "MAX_BYTES", limit)

    class GrowingSource(io.BytesIO):
        bytes_read = 0

        def read(self, count=-1):
            assert count == 1 << 16
            self.bytes_read += count
            assert self.bytes_read <= limit + (1 << 16), "Unbounded hashing continued"
            return b"x" * count  # never reaches EOF, despite the small pre-read stat

    reader = GrowingSource()
    monkeypatch.setattr(ac.figcapture, "open", lambda *a, **k: reader, raising=False)
    if publication_gate:
        failure("validation_budget_exceeded", lambda: ac.assert_current(source, context))
    else:
        failure("validation_budget_exceeded", lambda: ac.create_context(source, source.name))
    assert limit < reader.bytes_read <= limit + (1 << 16)


def test_hash_file_legacy_default_and_exact_budget_are_unchanged(tmp_path):
    source = png(tmp_path / "a.png")
    data = source.read_bytes()
    expected = hashlib.sha256(data).hexdigest(), len(data)
    assert ac.figcapture.hash_file(source) == expected
    assert ac.figcapture.hash_file(source, max_bytes=len(data)) == expected
    with pytest.raises(ac.figcapture.FileHashBudgetExceeded):
        ac.figcapture.hash_file(source, max_bytes=len(data) - 1)


@pytest.mark.parametrize("chunk", [b"iCCP", b"sRGB", b"gAMA", b"cHRM"])
@pytest.mark.parametrize("target", ["source", "candidate"])
def test_color_managed_png_refuses_even_with_identical_raw_rgba(tmp_path, chunk, target):
    from PIL import ImageCms

    source, candidate = png(tmp_path / "a.png"), png(tmp_path / "b.png")
    managed = source if target == "source" else candidate
    metadata = PngImagePlugin.PngInfo()
    options = {}
    if chunk == b"iCCP":
        # A real ICC profile through Pillow's native save path, also used by
        # Matplotlib's savefig(pil_kwargs={"icc_profile": ...}).
        options["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    else:
        body = {
            b"sRGB": b"\x00",
            b"gAMA": struct.pack(">I", 45455),
            b"cHRM": struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000),
        }[chunk]
        metadata.add(chunk, body)
    with Image.open(managed) as original:
        image = original.copy()
    image.save(managed, dpi=(100, 100), pnginfo=metadata, **options)
    assert chunk in managed.read_bytes(), "The native PNG writer must preserve the test metadata"
    assert pdfbackend.compare_png(source, candidate)["max_abs_diff"] == 0
    before = source.read_bytes()
    failure(
        "source_unreadable",
        lambda: ac.validate_candidate(source, candidate, ac.create_context(source, source.name)),
    )
    assert source.read_bytes() == before
