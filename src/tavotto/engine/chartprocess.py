"""Chart payload adapter over the existing worker pool (ADR 0182)."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

from . import chartwire, pool


def run_request(figures_dir: str | Path, payload: dict, *, timeout: float) -> dict:
    """Run one fixed chart operation; retain the existing public result shape."""
    if not isinstance(payload, dict):
        raise pool.WorkerError("Chart request must be an object", code="svg_scaffold_invalid")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (float, int))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise pool.WorkerError("Invalid chart timeout", code="svg_scaffold_invalid")
    worker = None
    try:
        request = chartwire.json_bytes(payload, chartwire.MAX_REQUEST_BYTES)
        frozen = json.loads(request)
        prepared = frozen.get("prepared") if frozen.get("action") == "pack_eps" else None
        if frozen.get("action") == "pack_eps" and not isinstance(prepared, dict):
            prepared = {}
        worker = pool.acquire_chart(figures_dir)
        deadline = time.monotonic() + timeout
        result = worker.chart_request(frozen, timeout)
        # Transport echoes belong to the shared protocol, not the chart contract.
        result = {
            k: v
            for k, v in result.items()
            if k
            not in {
                "protocol_version",
                "request_id",
                "worker_generation",
                "render_revision",
                "canonical_patch_hash",
                "hash_mismatch",
                "worker_patch_hash",
                "timings",
                "generation",
                "session_id",
                "supervisor_protocol_version",
                "queue_wait_ms",
                "total_ms",
            }
        }
        raw = chartwire.json_bytes(result, chartwire.MAX_STDOUT_BYTES)
        result = chartwire.response(raw, frozen.get("probe") is True, prepared=prepared)
        if time.monotonic() > deadline:
            worker.force_kill()
            raise pool.WorkerError("Chart response exceeded the deadline", code="worker_timeout")
        return result
    except chartwire.ChartWireError as exc:
        if worker is not None:
            worker.force_kill()
        error = pool.WorkerError(str(exc), code=exc.code)
        error.extra = exc.extra
        raise error from exc
    except pool.WorkerError as exc:
        if exc.code == "svg_resource_limit" and worker is not None:
            worker.force_kill()
        if exc.extra.get("chart_error") and exc.code != "svg_resource_limit":
            return {"ok": False, "code": exc.code, "error": str(exc)}
        if exc.code == "worker_timeout":
            code = (
                "chart_engine_unavailable"
                if payload.get("probe") is True
                else "svg_scaffold_timeout"
            )
            raise pool.WorkerError(str(exc), code=code) from exc
        raise
