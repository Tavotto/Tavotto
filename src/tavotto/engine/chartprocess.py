"""有界的一次性 chart command；复用现有 process owner、环境与 JSON/SVG 预算。

只有固定 svgchart 命令，无用户脚本、任意 argv、常驻 Figure 或第二套 owner。
chart 任务全局 admission 只约束本类任务，数值消费 pool.max_alive；既有
resident pool / native / one_shot 的容量边界没有在这里改写。未知关闭继续
占本类 admission 与 envlease，只有原 owner 确认退出才能释放。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import math
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from tavotto.rendercore.epscontainer import MAX_BYTES as MAX_EPS_BYTES
from tavotto.rendercore.svgcheck import SVGBudget

from . import chartprofile, config, desktopproc, envlease, launcher, pool, runtime

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
_POLICY = desktopproc.OwnedPolicy()
_LOCK = threading.Lock()
_ACTIVE: set[str] = set()


def active_count() -> int:
    """真实活跃/尚未确认关闭的 chart tasks 数；不代表全产品进程数。"""
    with _LOCK:
        return len(_ACTIVE)


def _error(code: str, message: str, *, reason: str = "") -> pool.WorkerError:
    error = pool.WorkerError(message, code=code)
    if reason:
        error.extra = {"reason": reason}
    return error


def _admit() -> str:
    cap = pool.max_alive()  # can read platform memory: never call under our lock
    with _LOCK:
        if len(_ACTIVE) >= cap:
            raise _error(
                "chart_engine_unavailable", "Chart rendering capacity is busy", reason="capacity"
            )
        token = "chart-" + uuid.uuid4().hex
        _ACTIVE.add(token)
        return token


def _json_bytes(value, limit: int) -> bytes:
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
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise _error("svg_scaffold_invalid", "Chart request is not finite UTF-8 JSON") from exc
    return bytes(chunks)


class _Run:
    """One request's buffers/lease, not a process owner: DesktopProcess owns it."""

    def __init__(self, token):
        self.token = token
        self.lock = threading.Lock()
        self.activity = threading.Event()
        self.python = ""
        self.owner_done = False
        self.abandoned = False
        self.released = False
        self.overflow = False
        self.io_failed = False
        self.stdout_bytes = 0
        self.spool = None

    def open_spool(self):
        directory = config.data_dir() / "cache" / "chart-requests"
        directory.mkdir(parents=True, exist_ok=True)
        self.spool = tempfile.TemporaryFile(mode="w+b", dir=directory)

    def output(self, name, chunk):
        if name != "stdout":
            return  # existing owner counts/drains stderr and retains its bounded tail
        with self.lock:
            self.stdout_bytes += len(chunk)
            if self.stdout_bytes > MAX_STDOUT_BYTES:
                self.overflow = True
                self.activity.set()  # pump never waits for its own owner to cancel
                return
            if self.overflow or self.io_failed:
                return
            try:
                self.spool.write(chunk)
            except OSError:
                self.io_failed = True
                self.activity.set()

    def finish(self, _code=None, *, cancelled=False):
        # Called only after DesktopProcess confirmed its scope and reaped its
        # child, or explicitly when no process was started.
        with self.lock:
            self.owner_done = True
            python, self.python = self.python, ""
        if python:
            envlease.release_native(python, self.token)
        self.activity.set()
        self.dispose_if_abandoned()

    def dispose_if_abandoned(self):
        with self.lock:
            if not self.abandoned or not self.owner_done or self.released:
                return
            self.released = True
            try:
                if self.spool is not None:
                    self.spool.close()
            finally:
                with _LOCK:
                    _ACTIVE.discard(self.token)

    def abandon(self):
        with self.lock:
            self.abandoned = True
        self.dispose_if_abandoned()


def _response(raw: bytes, probe: bool, *, prepared: dict | None = None) -> dict:
    def nonfinite(_value):
        raise ValueError("nonfinite JSON")

    try:
        result = json.loads(raw.decode("utf-8"), parse_constant=nonfinite)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            raise ValueError("response envelope")
        chartprofile.check_json_budget(result)
        metadata = dict(result)
        encoded = metadata.pop("eps_b64" if prepared is not None else "fragment_b64", None)
        _json_bytes(metadata, MAX_METADATA_BYTES)
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
    except (ValueError, UnicodeError, RecursionError, binascii.Error) as exc:
        raise _error("svg_scaffold_invalid", "Invalid chart response") from exc


def run_request(figures_dir: str | Path, payload: dict, *, timeout: float) -> dict:
    """Fixed chart request → complete bounded JSON, or WorkerError.

    `timeout` comes from the caller's existing chart deadline; it starts at
    spawn and includes stdin transmission. Interpreter/activation retain their
    existing resolver budgets. Cleanup uses OwnedPolicy's existing limits.
    """
    if not isinstance(payload, dict):
        raise _error("svg_scaffold_invalid", "Chart request must be an object")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (float, int))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise _error("svg_scaffold_invalid", "Invalid chart timeout")
    request = _json_bytes(payload, MAX_REQUEST_BYTES)
    # Match the reply to the exact serialized request, not the mutable caller's
    # dict after waiting on a child. Discard the large PS after taking identity.
    frozen = json.loads(request)
    prepared = None
    if frozen.get("action") == "pack_eps":
        value = frozen.get("prepared")
        prepared = (
            {
                key: value.get(key)
                for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt")
            }
            if isinstance(value, dict)
            else {}
        )
    del frozen
    run = _Run(_admit())
    owner = proc = None
    writer = None
    cancel_attempted = False
    try:
        run.open_spool()
        python, source = pool.resolve_worker_python(figures_dir)
        try:
            envlease.acquire_native(python, run.token)
        except envlease.EnvironmentBusy as exc:
            raise _error(exc.code, str(exc)) from exc
        run.python = python
        bundled = source == pool.SOURCE_BUNDLED
        env = pool.launch_env(
            launcher.launcher_for_interpreter(python),
            python,
            pool.worker_env(python, source),
            pool._overlay_for(figures_dir, python, None),
            bundled=bundled,
        )
        import tavotto

        deadline = time.monotonic() + timeout
        proc = subprocess.Popen(
            [python, *runtime.probe_args(bundled=bundled), "-m", "tavotto.engine.svgchart"],
            cwd=str(Path(tavotto.__file__).resolve().parent.parent),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=runtime.CREATE_NO_WINDOW,
            start_new_session=os.name != "nt",
        )
        owner = desktopproc.own_process(proc, _POLICY, on_output=run.output, on_exit=run.finish)
        owner.start()

        def send():
            if not owner.write_stdin(request):
                with run.lock:
                    run.io_failed = True
                run.activity.set()
            owner.close_stdin()

        writer = threading.Thread(target=send, daemon=True, name="tavotto-chart-stdin")
        writer.start()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not run.activity.wait(remaining):
                cancel_attempted = True
                owner.cancel()
                code = (
                    "chart_engine_unavailable"
                    if payload.get("probe") is True
                    else "svg_scaffold_timeout"
                )
                raise _error(code, "Chart process timed out")
            with run.lock:
                overflow, failed, done = run.overflow, run.io_failed, run.owner_done
            if overflow or failed:
                cancel_attempted = True
                owner.cancel()
                raise _error(
                    "svg_resource_limit" if overflow else "chart_engine_unavailable",
                    "Chart output budget exceeded" if overflow else "Chart process I/O failed",
                )
            if done:
                break
            run.activity.clear()
        if owner.exit_code != 0:
            raise _error("chart_engine_unavailable", "Chart process exited unsuccessfully")
        run.spool.seek(0)
        if prepared is not None:
            result = _response(run.spool.read(), False, prepared=prepared)
        else:
            result = _response(run.spool.read(), payload.get("probe") is True)
        if time.monotonic() > deadline:
            code = (
                "chart_engine_unavailable"
                if payload.get("probe") is True
                else "svg_scaffold_timeout"
            )
            raise _error(code, "Chart response exceeded the deadline")
        return result
    except launcher.LauncherUnavailable as exc:
        raise _error(exc.code, "Chart environment activation failed") from exc
    except OSError as exc:
        raise _error(
            "chart_engine_unavailable", "Chart process could not start or buffer output"
        ) from exc
    finally:
        if owner is not None and not run.owner_done and not cancel_attempted:
            owner.cancel()
        if owner is None:
            # own_process's exceptional path has already killed/reaped a child
            # that could not be attached. A Popen failure started no child.
            if proc is None or proc.poll() is not None:
                run.finish()
        run.abandon()  # unknown exit retains its lease/spool/admission until finish
        if writer is not None and run.owner_done:
            writer.join(_POLICY.kill_timeout)
        if proc is not None and run.owner_done:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError, ValueError):
                        stream.close()
