"""Lab-only probe (TVD-MHQJ): how does the frozen sidecar fail when antivirus-like damage hits its files?

Not for main. Mutates the installed sidecar in place (the task uninstalls afterwards), launches
`sidecar/Tavotto/Tavotto.exe --desktop-sidecar` the way the shell does (hello on stdin, handshake file,
stdout/stderr -> one log), and records per scenario: outcome (ready / exited / timeout), exit code, last
startup-phase marker, visible top-level windows of the process (bootloader dialogs), and a short log tail.
The test always fails at the end with the JSON so the facts come back in the Lab report's `failures`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

MARK = "TAVOTTO_STARTUP_PHASE="
BUDGET_S = 75


def _sidecar_dir() -> Path:
    shell = Path(os.environ["TAVOTTO_DESKTOP_EXE"])
    d = shell.parent / "sidecar" / "Tavotto"
    if (d / "Tavotto.exe").is_file():
        return d
    for hit in shell.parent.rglob("_internal"):
        if (hit.parent / "Tavotto.exe").is_file():
            return hit.parent
    raise AssertionError(f"sidecar not found under {shell.parent}")


def _windows_of(pid: int) -> list[str]:
    user32 = ctypes.windll.user32
    out: list[str] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            out.append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return out


def _close_windows_of(pid: int) -> None:
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        return True

    user32.EnumWindows(cb, 0)


def _launch(exe: Path, kill_at_phase: str | None = None, close_dialog: bool = False) -> dict:
    work = Path(tempfile.mkdtemp(prefix="avprobe-"))
    log = work / "sidecar.log"
    hs = work / "handshake.json"
    env = dict(os.environ)
    env["TAVOTTO_DESKTOP_HANDSHAKE"] = str(hs)
    t0 = time.monotonic()
    with open(log, "wb") as lf:
        p = subprocess.Popen(
            [str(exe), "--desktop-sidecar"],
            stdin=subprocess.PIPE,
            stdout=lf,
            stderr=lf,
            env=env,
            creationflags=0x08000000,  # CREATE_NO_WINDOW, same as the shell
        )
        hello_err = None
        try:
            p.stdin.write(json.dumps({"nonce": "n", "parent_pid": os.getpid()}).encode() + b"\n")
            p.stdin.flush()
        except OSError as e:
            hello_err = f"{type(e).__name__}"
        outcome, windows, killed = "timeout", [], False
        dialog_closed = False
        while time.monotonic() - t0 < BUDGET_S:
            if hs.exists():
                try:
                    if json.loads(hs.read_text("utf-8")).get("ready"):
                        outcome = "ready"
                        break
                except ValueError:
                    pass
            if p.poll() is not None:
                outcome = "exited"
                break
            text = log.read_text("utf-8", errors="replace") if log.exists() else ""
            if kill_at_phase and not killed and f"{MARK}{kill_at_phase}" in text:
                subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
                killed = True
            if time.monotonic() - t0 > 8 and not windows:
                windows = _windows_of(p.pid)
                if windows and close_dialog:
                    _close_windows_of(p.pid)
                    dialog_closed = True
            time.sleep(0.2)
        if outcome != "exited":
            windows = windows or _windows_of(p.pid)
            p.kill()
        code = p.wait(timeout=30)
    text = log.read_text("utf-8", errors="replace")
    phases = [ln[len(MARK) :].strip() for ln in text.splitlines() if ln.startswith(MARK)]
    return {
        "outcome": outcome,
        "exit_code": code if outcome == "exited" else None,
        "exit_code_hex": (f"0x{code & 0xFFFFFFFF:08X}" if outcome == "exited" else None),
        "seconds": round(time.monotonic() - t0, 1),
        "last_phase": phases[-1] if phases else "none",
        "phases": phases,
        "windows": windows,
        "hello_write_error": hello_err,
        "killed": killed,
        "dialog_closed": dialog_closed,
        "tail": " ".join(text[-150:].split()),
    }


def _pick_pyd(internal: Path) -> Path:
    for name in ("_socket.pyd", "select.pyd", "_ssl.pyd"):
        hit = next(internal.rglob(name), None)
        if hit is not None:
            return hit
    raise AssertionError("no startup .pyd found")


def _collect() -> dict:
    sd = _sidecar_dir()
    exe = sd / "Tavotto.exe"
    internal = sd / "_internal"
    facts: dict = {}
    files = [p for p in sd.rglob("*") if p.is_file()]
    t = time.monotonic()
    for p in files:
        p.stat()
    facts["n_files"] = len(files)
    facts["stat_all_s"] = round(time.monotonic() - t, 3)
    t = time.monotonic()
    for p in files:
        with open(p, "rb") as fh:
            fh.read(1)
    facts["open_read1_all_s"] = round(time.monotonic() - t, 3)
    facts["total_mb"] = round(sum(p.stat().st_size for p in files) / 1e6, 1)

    pyd = _pick_pyd(internal)
    off = pyd.with_name(pyd.name + ".off")
    pyd.rename(off)
    try:
        facts["pyd_missing_closed"] = _launch(exe, close_dialog=True)
    finally:
        off.rename(pyd)

    dll = next(iter(internal.glob("python3*.dll")), None) or next(
        iter(sd.glob("python3*.dll")), None
    )
    if dll is not None:
        off = dll.with_name(dll.name + ".off")
        dll.rename(off)
        try:
            facts["python_dll_missing_closed"] = _launch(exe, close_dialog=True)
        finally:
            off.rename(dll)

    subprocess.run(["icacls", str(pyd), "/deny", "*S-1-1-0:(RX)"], capture_output=True, text=True)
    try:
        t = time.monotonic()
        try:
            with open(pyd, "rb") as fh:
                fh.read(1)
            facts["denied_open"] = "ok"
        except OSError as e:
            facts["denied_open"] = f"{type(e).__name__} winerror={getattr(e, 'winerror', None)}"
        facts["pyd_read_denied_closed"] = _launch(exe, close_dialog=True)
    finally:
        subprocess.run(["icacls", str(pyd), "/remove:d", "*S-1-1-0"], capture_output=True)
    return facts


CHUNK = 170
N_CHUNKS = 60


@pytest.fixture(scope="module")
def facts_text() -> str:
    try:
        facts = _collect()
    except Exception as e:  # noqa: BLE001 - lab probe: report whatever broke
        facts = {"probe_error": f"{type(e).__name__}: {e}"}
    facts.pop("sidecar_dir", None)
    return json.dumps(facts, ensure_ascii=True, separators=(",", ":"))


@pytest.mark.parametrize("i", range(N_CHUNKS))
def test_av_like_damage_probe(facts_text, i):
    """The Lab report keeps 200 chars per failure: ship the JSON back in numbered chunks."""
    part = facts_text[i * CHUNK : (i + 1) * CHUNK]
    if part:
        raise AssertionError(f"C{i:02d}|{part}")
