"""Opaque evidence of crossing the safe worker's user-code execution boundary.

Markers are request-scoped, disposable IPC, not documents or execution history.
They contain no script names, paths, argv, or user output. A failed write is not
proof that execution did not happen; the response is an independent channel.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_WATCH = re.compile(r"[0-9a-f]{32}\Z")
_EXECUTION = re.compile(r"[0-9a-f]{48}\Z")


def valid_watch(value) -> bool:
    return isinstance(value, str) and _WATCH.fullmatch(value) is not None


def valid_id(value) -> bool:
    return isinstance(value, str) and _EXECUTION.fullmatch(value) is not None


def new_id() -> str:
    # Same-host monotonic clock orders late facts across child process restarts.
    return f"{time.monotonic_ns():016x}{uuid.uuid4().hex}"


def started_ns(value: str) -> int:
    return int(value[:16], 16) if valid_id(value) else 0


@contextmanager
def on_user_entry(script, started):
    """Observe compiled user-frame entry, without replacing import/runpy semantics.

    A missing file/import or SyntaxError has no Python frame and is not execution.
    The one-shot trace restores the previous tracer before the first instruction;
    existing tracing still receives all calls and owns their local trace handlers.
    """
    target = os.path.normcase(os.path.abspath(script))
    previous = sys.gettrace()

    def trace(frame, event, arg):
        if event == "call" and (
            frame.f_globals.get("__name__") == "paper_style"
            or os.path.normcase(os.path.abspath(frame.f_code.co_filename)) == target
        ):
            sys.settrace(previous)
            started()
        return previous(frame, event, arg) if previous is not None else None

    sys.settrace(trace)
    try:
        yield
    finally:
        if sys.gettrace() is trace:
            sys.settrace(previous)


def marker_path(directory, watch: str) -> Path:
    if not valid_watch(watch):
        raise ValueError("Invalid execution observation identity")
    return Path(directory) / f".execution-{watch}"


def write(directory, watch: str | None, execution_id: str) -> bool:
    if not valid_watch(watch) or not valid_id(execution_id):
        return False
    try:
        # One fixed-size write; partial writes cannot validate as execution facts.
        with marker_path(directory, watch).open("wb") as stream:
            stream.write(execution_id.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except OSError:
        return False


def read(directory, watch: str) -> str | None:
    try:
        with marker_path(directory, watch).open("rb") as stream:
            value = stream.read(49).decode("ascii")
    except (OSError, UnicodeError):
        return None
    return value if valid_id(value) else None
