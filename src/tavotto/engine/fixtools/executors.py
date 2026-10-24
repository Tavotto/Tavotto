"""「改」组里装 / 卸包的执行器（ADR 0149 §七）。从 `change.py` 分出来，让 `change.py` 与 `snapshots.py`（回滚也要装 / 卸）都能用而不互相 import。

缺省执行器走应用里现有的 pip 路径：`deprepair._pip_install` / `_pip_uninstall`（同一份 argv 出处、只装 wheel、镜像回退）+
`pool.mutating_environment`（环境独占锁）。`FixContext.installer` / `uninstaller` 注入的假执行器优先（测试一律用假的）。
换 Python 的缺省执行器走 `pyversionfix`（ADR 0114 修订 2026-10-23）的公开入口，同样可由 `FixContext.python_changer` 注入替换。
"""

from __future__ import annotations

import threading

from .base import FixContext


def _default_installer(python: str, requirements: list[str]) -> dict:
    from .. import deprepair, pool

    cancel = threading.Event()
    done: list[str] = []
    key = deprepair._env_key(deprepair.TARGET_PROJECT_VENV, python, "")
    try:
        with pool.mutating_environment(key, python):
            for req in requirements:
                code, _out = deprepair._pip_install(python, req, cancel, None)
                if code:
                    return {"ok": False, "code": str(code), "done": done}
                done.append(req)
    except pool.EnvironmentBusy:
        return {"ok": False, "code": "env_busy", "done": done}
    return {"ok": True, "code": "", "done": done}


def _default_uninstaller(python: str, distributions: list[str]) -> dict:
    from .. import deprepair, pool

    cancel = threading.Event()
    done: list[str] = []
    key = deprepair._env_key(deprepair.TARGET_PROJECT_VENV, python, "")
    try:
        with pool.mutating_environment(key, python):
            for dist in distributions:
                code, _out = deprepair._pip_uninstall(python, dist, cancel, None)
                if code:
                    return {"ok": False, "code": str(code), "done": done}
                done.append(dist)
    except pool.EnvironmentBusy:
        return {"ok": False, "code": "env_busy", "done": done}
    return {"ok": True, "code": "", "done": done}


def _default_python_changer(req: dict, cancel: threading.Event | None = None) -> dict:
    """另建一个指定 Python 版本的 Tavotto 受管环境（`pyversionfix.plan_python_change` → `apply_python_change`）。

    只在 Tavotto 自己的受管环境里另建，用户的环境 / 系统 Python / conda 一个字节不碰；基础解释器的挑选、私有 Python 的
    下载（锁里没有的版本不会凭空下载）、联合计划补依赖、代事务全在 `pyversionfix` 里，这里只把「要这个版本」翻成它认的
    `failure`，不复制逻辑。请求 `{"version", "project", "script"?}`；返回 `{"ok", "code", "message", "python"?}`。
    本函数不碰界面进度（修复会话没有对应的事件通道）。`cancel` = 修复会话的取消信号：规划完、动手前先看一眼，
    之后交给 `apply_python_change`（下载 / 建环境会按它中途停下）。"""
    from .. import projectenv, pyversionfix

    cancel = cancel or threading.Event()

    version = str(req.get("version") or "").strip()
    project = str(req.get("project") or "")
    if not pyversionfix.auto_change_enabled():
        return {
            "ok": False,
            "code": pyversionfix.ERROR_UNAVAILABLE,
            "message": "Switching Python versions is turned off on this computer.",
        }
    spec = f"=={version}" if version.count(".") >= 2 else f"=={version}.*"
    failure = {
        "code": pyversionfix.CODE_UNSUPPORTED,
        "script": str(req.get("script") or ""),
        "requires_python": spec,
    }
    plan = pyversionfix.plan_python_change(failure, project)
    if plan is None:
        return {
            "ok": False,
            "code": "python_change_not_possible",
            "message": f"Python {version} cannot be provided here: it is outside the supported range, "
            "conflicts with what the project declares, or no installable Python of that version is available.",
        }
    if cancel.is_set():
        return {
            "ok": False,
            "code": "cancelled",
            "message": "The fix was cancelled; nothing was changed.",
        }
    result = pyversionfix.apply_python_change(plan, None, cancel)
    if not result.get("ok"):
        return {
            "ok": False,
            "code": str(result.get("code") or pyversionfix.ERROR_FAILED),
            "message": f"Creating the Python {plan.to_version} environment did not finish; the existing "
            "environments were not touched.",
        }
    python = projectenv.remembered(project)
    return {
        "ok": True,
        "code": "",
        "message": f"Created a Tavotto environment with Python {plan.to_version} and set it for this project.",
        "python": python or "",
    }


def call_executor(fn, *a) -> dict:
    try:
        out = fn(*a)
    except Exception as exc:  # noqa: BLE001 - 执行器的缺陷是这次调用的失败，不抛出会话
        return {"ok": False, "code": f"executor_{type(exc).__name__}"[:60], "done": []}
    return (
        out if isinstance(out, dict) else {"ok": False, "code": "executor_bad_result", "done": []}
    )


def run_installer(ctx: FixContext, python: str, requirements: list[str]) -> dict:
    return call_executor(ctx.installer or _default_installer, python, requirements)


def run_uninstaller(ctx: FixContext, python: str, distributions: list[str]) -> dict:
    return call_executor(ctx.uninstaller or _default_uninstaller, python, distributions)


def run_python_changer(ctx: FixContext, req: dict) -> dict:
    if ctx.python_changer is not None:
        return call_executor(ctx.python_changer, req)
    return call_executor(_default_python_changer, req, ctx.cancel)
