"""Versioned, opt-in annotations for the Tavotto canvas PDF writer."""

import hashlib
import io
import json

TEXT_ATTACHMENT = "tavotto-canvas-text.json"
_SOURCE = "tavotto.canvas.text"
_SCHEMA = 1
_ALGORITHM = "qpdf-page-sha256-v1"


class StaleTextAttachment(ValueError):
    """The page no longer matches the embedded canvas annotations."""


class _HashSink(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.digest = hashlib.sha256()
        self.offset = 0

    def writable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.offset

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        self.offset += len(data)
        return len(data)


def _page_digest(pdf) -> str:
    """Hash a standalone copy of the page and its resources, apart from catalog data."""
    import pikepdf

    with pikepdf.Pdf.new() as projection, _HashSink() as sink:
        projection.pages.append(pdf.pages[0])
        projection.save(
            sink,
            deterministic_id=True,
            compress_streams=False,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
        )
        return sink.digest.hexdigest()


def attach_canvas_text(pdf, texts: tuple[str, ...]) -> None:
    import pikepdf

    payload = {
        "schema_version": _SCHEMA,
        "source": _SOURCE,
        "page": 1,
        "page_fingerprint": {
            "algorithm": _ALGORITHM,
            "qpdf_version": pikepdf.__libqpdf_version__,
            "sha256": _page_digest(pdf),
        },
        "annotations": [{"text": text} for text in texts],
    }
    pdf.attachments[TEXT_ATTACHMENT] = pikepdf.AttachedFileSpec(
        pdf,
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        mime_type="application/json",
    )


def read_canvas_text(pdf) -> tuple[str, ...] | None:
    """Read the attachment from a canvas output only if its page still matches."""
    import pikepdf

    if TEXT_ATTACHMENT not in pdf.attachments:
        return None
    raw = pdf.attachments[TEXT_ATTACHMENT].get_file().read_bytes()
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] != _SCHEMA
        or payload.get("source") != _SOURCE
        or type(payload.get("page")) is not int
        or payload["page"] != 1
    ):
        raise ValueError("Invalid Tavotto canvas text attachment")
    fingerprint = payload.get("page_fingerprint")
    if (
        not isinstance(fingerprint, dict)
        or fingerprint.get("algorithm") != _ALGORITHM
        or fingerprint.get("qpdf_version") != pikepdf.__libqpdf_version__
    ):
        raise ValueError("Unsupported Tavotto canvas page fingerprint")
    annotations = payload.get("annotations")
    if not isinstance(annotations, list) or not all(
        isinstance(item, dict) and isinstance(item.get("text"), str) for item in annotations
    ):
        raise ValueError("Invalid annotations in Tavotto canvas text attachment")
    if len(pdf.pages) != 1 or fingerprint.get("sha256") != _page_digest(pdf):
        raise StaleTextAttachment("Tavotto canvas text attachment does not match the PDF page")
    return tuple(item["text"] for item in annotations)
