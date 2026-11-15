"""Bounded chart payloads shared by both sides of the worker protocol."""

from __future__ import annotations

import base64
import binascii
import json

from tavotto.rendercore.epscontainer import MAX_BYTES as MAX_EPS_BYTES
from tavotto.rendercore.svgcheck import SVGBudget

from . import chartprofile


class ChartWireError(ValueError):
    def __init__(self, code, message, *, reason=""):
        super().__init__(message)
        self.code = code
        self.extra = {"reason": reason} if reason else {}


def _error(code, message, *, reason=""):
    return ChartWireError(code, message, reason=reason)


_SVG = SVGBudget()
MAX_REQUEST_BYTES = _SVG.max_bytes
MAX_METADATA_BYTES = _SVG.max_bytes
MAX_FRAGMENT_BYTES = _SVG.max_bytes
_B64_FRAGMENT_BYTES = ((MAX_FRAGMENT_BYTES + 2) // 3) * 4
_B64_EPS_BYTES = ((MAX_EPS_BYTES + 2) // 3) * 4
MAX_STDOUT_BYTES = (
    max(_B64_FRAGMENT_BYTES, _B64_EPS_BYTES)
    + MAX_METADATA_BYTES
    + len(json.dumps({"fragment_b64": ""}, separators=(",", ":")).encode("utf-8"))
)

# Envelope fields are structural; scientific metadata keeps its existing budget.
MAX_FRAME_BYTES = MAX_STDOUT_BYTES + 8192


def json_bytes(value, limit: int) -> bytes:
    chunks = bytearray()
    try:
        chartprofile.check_json_budget(value)
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for piece in encoder.iterencode(value):
            raw = piece.encode("utf-8")
            if len(chunks) + len(raw) > limit:
                raise _error("svg_resource_limit", "Chart JSON byte budget exceeded")
            chunks.extend(raw)
    except chartprofile.ChartProfileError as exc:
        raise _error(exc.code, "Chart JSON structure budget exceeded") from exc
    except ChartWireError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise _error("svg_scaffold_invalid", "Chart request is not finite UTF-8 JSON") from exc
    return bytes(chunks)


def response(raw: bytes, probe: bool, *, prepared: dict | None = None) -> dict:
    def nonfinite(_value):
        raise ValueError("nonfinite JSON")

    try:
        result = json.loads(raw.decode("utf-8"), parse_constant=nonfinite)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("response envelope")
        chartprofile.check_json_budget(result)
        metadata = dict(result)
        encoded = metadata.pop("eps_b64" if prepared is not None else "fragment_b64", None)
        json_bytes(metadata, MAX_METADATA_BYTES)
        if result["ok"]:
            engine = result.get("engine")
            if not isinstance(engine, dict) or any(
                not isinstance(engine.get(key), str) or not engine[key]
                for key in ("matplotlib", "numpy", "tavotto")
            ):
                raise ValueError("engine identity")
            if prepared is not None:
                fields = {
                    "ok",
                    "engine",
                    "eps_b64",
                    "source_svg_sha256",
                    "ps_sha256",
                    "width_pt",
                    "height_pt",
                }
                if set(result) != fields:
                    raise ValueError("EPS response fields")
                if not isinstance(encoded, str) or len(encoded) > _B64_EPS_BYTES:
                    raise ValueError("EPS envelope")
                eps = base64.b64decode(encoded, validate=True)
                if len(eps) > MAX_EPS_BYTES:
                    raise _error("svg_resource_limit", "EPS byte budget exceeded")
                if (
                    not eps.startswith(b"%!PS-Adobe-3.0 EPSF-3.0\n")
                    or b"\n%%EndComments\n" not in eps
                    or b"\n%%EndProlog\n" not in eps
                ):
                    raise ValueError("EPS container signature")
                for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt"):
                    if result[key] != prepared.get(key):
                        raise ValueError("EPS request identity")
                if any(type(result[key]) not in (int, float) for key in ("width_pt", "height_pt")):
                    raise ValueError("EPS physical size")
            elif not probe:
                if not isinstance(encoded, str) or len(encoded) > _B64_FRAGMENT_BYTES:
                    raise ValueError("fragment envelope")
                fragment = base64.b64decode(encoded, validate=True)
                if not fragment:
                    raise ValueError("empty fragment")
                if len(fragment) > MAX_FRAGMENT_BYTES:
                    raise _error("svg_resource_limit", "Chart SVG byte budget exceeded")
                if not isinstance(result.get("facts"), dict):
                    raise ValueError("facts envelope")
        return result
    except chartprofile.ChartProfileError as exc:
        raise _error(exc.code, "Chart response structure budget exceeded") from exc
    except ChartWireError:
        raise
    except (ValueError, UnicodeError, RecursionError, binascii.Error) as exc:
        raise _error("svg_scaffold_invalid", "Invalid chart response") from exc
