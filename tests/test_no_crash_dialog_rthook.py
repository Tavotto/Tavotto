"""PyInstaller 运行时钩子：Windows 冻结产物里未处理的异常不弹报错框，写日志后以退出码 1 退出（TVD-MHQJ）。"""

from __future__ import annotations

import ast
import io
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "packaging" / "rthooks" / "pyi_rth_tavotto_no_crash_dialog.py"


class _Exited(Exception):
    pass


def _run_hook(monkeypatch, *, platform: str, frozen: bool):
    monkeypatch.setattr(sys, "platform", platform)
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    runpy.run_path(str(HOOK))


def test_windows_frozen_unhandled_exception_logs_runs_exit_funcs_and_exits_1(monkeypatch):
    import atexit
    import os

    _run_hook(monkeypatch, platform="win32", frozen=True)
    assert sys.excepthook is not sys.__excepthook__
    codes: list[int] = []
    ran: list[str] = []

    def fake_exit(code):
        codes.append(code)
        raise _Exited

    monkeypatch.setattr(os, "_exit", fake_exit)
    monkeypatch.setattr(atexit, "_run_exitfuncs", lambda: ran.append("atexit"))
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    try:
        raise ModuleNotFoundError("No module named '_socket'")
    except ModuleNotFoundError:
        exc_info = sys.exc_info()
    with pytest.raises(_Exited):
        sys.excepthook(*exc_info)
    assert codes == [1], "与 bootloader 原本的退出码一致"
    assert ran == ["atexit"], "已登记的退出清理照跑（如 Conda 激活结果文件）"
    assert "ModuleNotFoundError: No module named '_socket'" in err.getvalue()


def test_with_no_stderr_stream_the_traceback_goes_to_fd_2(monkeypatch):
    import os

    _run_hook(monkeypatch, platform="win32", frozen=True)
    written: list[tuple[int, bytes]] = []
    monkeypatch.setattr(os, "write", lambda fd, b: written.append((fd, b)) or len(b))
    monkeypatch.setattr(os, "_exit", lambda code: (_ for _ in ()).throw(_Exited()))
    monkeypatch.setattr(sys, "stderr", None)
    with pytest.raises(_Exited):
        sys.excepthook(ValueError, ValueError("boom"), None)
    assert written and written[0][0] == 2 and b"ValueError: boom" in written[0][1]


@pytest.mark.parametrize(
    ("platform", "frozen"), [("darwin", True), ("win32", False), ("linux", True)]
)
def test_elsewhere_the_hook_leaves_excepthook_alone(monkeypatch, platform, frozen):
    _run_hook(monkeypatch, platform=platform, frozen=frozen)
    assert sys.excepthook is sys.__excepthook__


def test_the_spec_runs_this_hook_first():
    """自定义运行时钩子先于 PyInstaller 自带的执行；导入失败（被隔离的 .pyd）就发生在自带钩子里。"""
    tree = ast.parse((ROOT / "packaging" / "tavotto.spec").read_text("utf-8"))
    hooks = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis"
        for kw in node.keywords
        if kw.arg == "runtime_hooks"
    ]
    assert len(hooks) == 1 and isinstance(hooks[0], ast.List) and hooks[0].elts
    assert HOOK.name in ast.unparse(hooks[0].elts[0])
    assert HOOK.is_file()
