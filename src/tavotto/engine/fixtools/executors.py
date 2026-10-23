"""「改」组里装 / 卸包的执行器（ADR 0149 §七）。从 `change.py` 分出来，让 `change.py` 与 `snapshots.py`（回滚也要装 / 卸）都能用而不互相 import。

缺省执行器走应用里现有的 pip 路径：`deprepair._pip_install` / `_pip_uninstall`（同一份 argv 出处、只装 wheel、镜像回退）+
`pool.mutating_environment`（环境独占锁）。`FixContext.installer` / `uninstaller` 注入的假执行器优先（测试一律用假的）。
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
