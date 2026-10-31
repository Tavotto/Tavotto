"""桌面宿主 L2 的窗口探针（交互会话一侧；规则 docs/rules/ci/windows-lab.md「桌面宿主 L2」）。

    python window_probe.py --exe <主程序> --root <安装目录> --out <结果.json> [--shot <截图.png>] [-- <额外参数…>]

由 `gui_host.py` 在 tavotto-lab 的交互会话里拉起（`agent.run_in_gui`），本文件只做一件事：把这个宿主
启动起来，等它的**主窗口真的出现**——可见、未被 DWM 隐藏（cloaked）、有标题、尺寸像个窗口、且属于
安装目录里的进程——并连续稳定 `STABLE_S` 秒；记下标题 / 窗口类 / 尺寸 / 进程映像，能截图就截一张，
然后结束它启动的所有进程。没等到就如实写 `window: null`，不猜。

宿主自己拉起的子进程都在同一个 Job 里（`agent.ProcessTree`）；有的宿主用启动器另起主进程，所以
窗口按「进程映像在安装目录下」认，收尾也按映像路径把安装目录下的残留进程一并结束（只会是本账户的进程）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as A  # noqa: E402 — 同目录部署：Job Object 的唯一出处

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 主窗口出现后要连续稳定这么久才算（闪一下的启动画面不算）
STABLE_S = 5.0
MIN_W, MIN_H = 300, 200


def _image_path(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def under(path: str | None, root: Path) -> bool:
    if not path:
        return False
    p = os.path.normcase(os.path.normpath(path))
    r = os.path.normcase(os.path.normpath(str(root)))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def top_windows(root: Path) -> list[dict]:
    """当前桌面上属于安装目录进程的、像主窗口的顶层窗口。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    found: list[dict] = []
    images: dict[int, str | None] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        cloaked = wintypes.DWORD(0)
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < MIN_W or h < MIN_H:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in images:
            images[pid.value] = _image_path(pid.value)
        if not under(images[pid.value], root):
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title.value[:200],
                "class": cls.value,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "pid": pid.value,
                "image": Path(images[pid.value] or "").name,
            }
        )
        return True

    user32.EnumWindows(cb, 0)
    return found


def kill_under(root: Path) -> list[str]:
    """结束映像在安装目录下的残留进程（宿主经启动器另起、不在 Job 里的那些）。"""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi")
    arr = (wintypes.DWORD * 4096)()
    got = wintypes.DWORD()
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(got)):
        return []
    killed = []
    for pid in list(arr)[: got.value // ctypes.sizeof(wintypes.DWORD)]:
        if pid in (0, os.getpid()):
            continue
        img = _image_path(pid)
        if not under(img, root):
            continue
        h = k32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if h:
            if k32.TerminateProcess(h, 1):
                killed.append(Path(img or "").name)
            k32.CloseHandle(h)
    return killed


def _screenshot(rect: list[int], shot: Path) -> bool:
    try:
        from PIL import ImageGrab  # 只在 venv 有 Pillow 时截图；截不到不影响结论
    except ImportError:
        return False
    try:
        ImageGrab.grab(bbox=tuple(rect), all_screens=True).save(shot)
        return True
    except Exception:  # noqa: BLE001 — 截图只是证据，不是判据
        return False


def probe(exe: Path, root: Path, extra: list[str], cwd: Path, timeout: float, shot: Path | None):
    t0 = time.monotonic()
    log = cwd / "host-output.log"
    with open(log, "wb") as out:
        tree = A.ProcessTree(
            [str(exe), *extra], cwd=cwd, env=dict(os.environ), stdout=out, stderr=out
        )
        window, stable_since, seen_any = None, None, False
        try:
            while time.monotonic() - t0 < timeout:
                wins = top_windows(root)
                if wins:
                    seen_any = True
                    best = max(
                        wins,
                        key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]),
                    )
                    if window is None or best["hwnd"] != window["hwnd"]:
                        window, stable_since = best, time.monotonic()
                    else:
                        window = best
                    if time.monotonic() - stable_since >= STABLE_S:
                        break
                else:
                    window, stable_since = None, None
                time.sleep(0.5)
            else:
                if window is not None and time.monotonic() - stable_since < STABLE_S:
                    window = None
            exit_code = tree.proc.poll()
            shot_ok = bool(window and shot and _screenshot(window["rect"], shot))
        finally:
            tree.close()
            leftovers = kill_under(root)
    return {
        "window": window,
        "seen_any_window": seen_any,
        "seconds_to_window": round(time.monotonic() - t0, 1) if window else None,
        "launcher_exit_code": exit_code,
        "screenshot": shot_ok,
        "killed_leftovers": leftovers,
    }


def main(argv: list[str]) -> int:
    extra: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1 :]
    p = argparse.ArgumentParser()
    p.add_argument("--exe", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--shot")
    p.add_argument("--timeout", type=float, default=120)
    ns = p.parse_args(argv)
    out = Path(ns.out)
    res = probe(
        Path(ns.exe),
        Path(ns.root),
        extra,
        out.parent,
        ns.timeout,
        Path(ns.shot) if ns.shot else None,
    )
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
