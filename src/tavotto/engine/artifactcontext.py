"""Parent-side, zero-edit admission of a selected disk artifact. Never writes originals."""

from __future__ import annotations

import math
import struct
import tempfile
import zlib
from pathlib import Path

from tavotto import pdfbackend

from . import config, figcapture, originalspec
from .figcapture import ArtifactContextError, artifact_request as artifact_request

# Two RGBA buffers + scratch: 1/4 of RenderCore's 64M cap; PDF keeps 60s/2GiB child bounds.
MAX_BYTES = figcapture.ARTIFACT_VALIDATION_MAX_BYTES
MAX_PIXELS = figcapture.ARTIFACT_VALIDATION_MAX_PIXELS
PDF_DPI = 300


def _require(ok, reason, message):
    if not ok:
        raise ArtifactContextError(reason, message)


def create_context(path: Path, source_id: str) -> dict:
    """Called only after the controller resolves/authorizes this exact file ID."""
    path = Path(path)
    try:
        _require(path.is_file(), "source_unreadable", "Source unavailable.")
        _require(
            path.suffix.lower() in (".png", ".pdf"), "source_unreadable", "Unsupported format."
        )
        _require(
            path.stat().st_size <= MAX_BYTES, "validation_budget_exceeded", "Source byte limit."
        )
        sha, size = figcapture.hash_file(path, max_bytes=MAX_BYTES)
        return figcapture.SourceArtifact(
            source_id, figcapture.ORIGIN_STATIC, path.suffix[1:].lower(), sha, size
        ).to_payload()
    except figcapture.FileHashBudgetExceeded as exc:
        raise ArtifactContextError("validation_budget_exceeded", "Source byte limit.") from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, ArtifactContextError):
            raise
        raise ArtifactContextError("source_unreadable", "Cannot read source.") from exc


def assert_current(path: Path, context: dict) -> None:
    """Also usable as the final publication gate after a cached render/export."""
    current = create_context(path, context["source_id"])
    _require(
        current == {k: v for k, v in context.items() if k != "render_policy"},
        "source_changed",
        "The selected source changed; reopen it.",
    )


def _snapshot(path, out):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    _require(len(data) <= MAX_BYTES, "validation_budget_exceeded", "Input byte limit.")
    out.write_bytes(data)
    return data


def _png_density(data):
    # Pillow can accept a missing IEND or damaged ancillary CRC. Admission requires
    # a complete PNG; 16-bit normalization and animated PNG would lose source pixels.
    _require(data.startswith(b"\x89PNG\r\n\x1a\n"), "source_unreadable", "Invalid PNG signature.")
    pos, ended, ppm = 8, False, None
    while pos + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        end = pos + 12 + size
        _require(end <= len(data), "source_unreadable", "Truncated PNG chunk.")
        crc = struct.unpack_from(">I", data, end - 4)[0]
        _require(
            zlib.crc32(data[pos + 4 : end - 4]) == crc, "source_unreadable", "Invalid PNG CRC."
        )
        _require(kind != b"acTL", "source_unreadable", "Animated PNG is unsupported.")
        # Raw RGBA equality cannot establish equality of color-managed appearance.
        _require(
            kind not in (b"iCCP", b"sRGB", b"gAMA", b"cHRM"),
            "source_unreadable",
            "PNG color metadata is unsupported.",
        )
        if kind == b"IHDR":
            _require(
                size == 13 and data[pos + 16] <= 8, "source_unreadable", "Unsupported PNG depth."
            )
        if kind == b"pHYs" and size == 9:
            x, y, unit = struct.unpack_from(">IIB", data, pos + 8)
            ppm = (x, y) if unit == 1 and x > 0 and y > 0 else None
        pos = end
        if kind == b"IEND":
            ended = size == 0 and pos == len(data)
            break
    _require(ended, "source_unreadable", "Incomplete PNG.")
    return originalspec._png_dpi(data), ppm


def _frame(path, kind, data):
    probe = pdfbackend.probe_asset(path, "pdf" if kind == "pdf" else "raster", include_pages=True)
    if kind == "pdf":
        _require(data.rstrip().endswith(b"%%EOF"), "source_unreadable", "Incomplete PDF.")
        _require(probe["pages"] == 1, "validation_budget_exceeded", "Single-page PDFs only.")
        size = [probe["w_pt"], probe["h_pt"]]
        _require(
            all(math.isfinite(v) and v > 0 for v in size), "source_unreadable", "Invalid PDF frame."
        )
        width = max(1, round(size[0] * PDF_DPI / 72))
        pixels = width * max(1, round(size[1] * width / size[0]))
        frame = {"size_pt": size, "validation_width_px": width}
    else:
        size = [probe["px_w"], probe["px_h"]]
        dpi, ppm = _png_density(data)
        frame = {"size_px": size, "dpi": dpi, "ppm": ppm}
        pixels = size[0] * size[1]
    _require(0 < pixels <= MAX_PIXELS, "validation_budget_exceeded", "Decoded pixel limit.")
    return frame


def _same_frame(a, b, kind):
    if kind == "pdf":
        return a["size_pt"] == b["size_pt"]
    if a["size_px"] != b["size_px"] or (a["ppm"] is None) != (b["ppm"] is None):
        return False
    # pHYs stores integer pixels/metre: at most one quantization step, never a
    # broad mm threshold. Effective DPI facts still come from originalspec.
    return a["ppm"] is None or all(abs(x - y) <= 1 for x, y in zip(a["ppm"], b["ppm"]))


def validate_candidate(source_path: Path, candidate_path: Path, context: dict) -> dict:
    """Compare a private zero-edit save in the source format, before applying edits."""
    assert_current(source_path, context)
    kind = context["kind"]
    same_kind = Path(candidate_path).suffix.lower() == "." + kind
    _require(same_kind, "source_mismatch", "Format differs.")
    root = config.data_dir() / "artifact-validation"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="check-", dir=root) as directory:
            a, b = (Path(directory) / f"{name}.{kind}" for name in ("source", "candidate"))
            data_a, data_b = _snapshot(source_path, a), _snapshot(candidate_path, b)
            _require(
                figcapture.hash_file(a) == (context["bytes_sha256"], context["size_bytes"]),
                "source_changed",
                "Source changed during validation.",
            )
            fa, fb = _frame(a, kind, data_a), _frame(b, kind, data_b)
            _require(_same_frame(fa, fb, kind), "source_mismatch", "Source frame differs.")
            if kind == "pdf":
                pa, pb = a.with_suffix(".png"), b.with_suffix(".png")
                pdfbackend.render_preview_png(a, fa["validation_width_px"], pa)
                pdfbackend.render_preview_png(b, fa["validation_width_px"], pb)
                a, b = pa, pb
            metrics = pdfbackend.compare_png(a, b)
            _require(
                metrics.get("ok") and metrics.get("max_abs_diff") == 0,
                "source_mismatch",
                "Zero-edit source pixels differ.",
            )
            return dict(
                format=kind, frame=fa, pixels_equal=True, total_pixels=metrics["total_pixels"]
            )
    except Exception as exc:
        if isinstance(exc, ArtifactContextError):
            raise
        budget = getattr(exc, "code", "") in (
            "pixel_budget_exceeded",
            "raster_too_large",
            "render_child_timeout",
        ) or isinstance(exc, (MemoryError, TimeoutError))
        reason = "validation_budget_exceeded" if budget else "source_unreadable"
        raise ArtifactContextError(reason, "Unable to validate the selected source.") from exc
    finally:
        assert_current(source_path, context)
