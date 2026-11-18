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


def _launch(exe: Path, kill_at_phase: str | None = None) -> dict:
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
        "tail": " ".join(text[-400:].split()),
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
    facts: dict = {"sidecar_dir": str(sd)}

    facts["baseline"] = _launch(exe)

    pyd = _pick_pyd(internal)
    facts["pyd_name"] = pyd.relative_to(internal).as_posix()
    off = pyd.with_name(pyd.name + ".off")
    pyd.rename(off)
    try:
        facts["pyd_missing"] = _launch(exe)
    finally:
        off.rename(pyd)

    dll = next(
        iter(sorted(internal.glob("python3*.dll"), key=lambda p: len(p.name), reverse=True)), None
    )
    if dll is None:
        dll = next(iter(sd.glob("python3*.dll")), None)
    if dll is not None:
        facts["dll_name"] = dll.name
        off = dll.with_name(dll.name + ".off")
        dll.rename(off)
        try:
            facts["python_dll_missing"] = _launch(exe)
        finally:
            off.rename(dll)

    deny = subprocess.run(
        ["icacls", str(pyd), "/deny", "*S-1-1-0:(RX)"], capture_output=True, text=True
    )
    facts["deny_icacls_rc"] = deny.returncode
    try:
        facts["pyd_read_denied"] = _launch(exe)
    finally:
        subprocess.run(["icacls", str(pyd), "/remove:d", "*S-1-1-0"], capture_output=True)

    facts["killed_after_imports_done"] = _launch(exe, kill_at_phase="imports_done")
    facts["killed_after_interpreter"] = _launch(exe, kill_at_phase="interpreter")

    facts["probe_after"] = {
        "sidecar_dir": sd.is_dir(),
        "internal_dir": internal.is_dir(),
        "exe": exe.is_file(),
    }
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
