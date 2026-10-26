"""「改」组里装 / 卸包的执行器（ADR 0149 §七）。从 `change.py` 分出来，让 `change.py` 与 `snapshots.py`（回滚也要装 / 卸）都能用而不互相 import。

缺省执行器走应用里现有的 pip 路径：`deprepair._pip_install` / `_pip_uninstall`（同一份 argv 出处、只装 wheel、镜像回退）+
`pool.mutating_environment`（环境独占锁）。`FixContext.installer` / `uninstaller` 注入的假执行器优先（测试一律用假的）。
"""

from __future__ import annotations

import re
import threading

from .base import FixContext

_ERROR_LINE_RE = re.compile(r"^\s*(?:ERROR|error)\b[:\s].*", re.M)


def failure_detail(out: str, limit: int = 2) -> str:
    """pip / 安装器输出里最能说明原因的一两行（`ERROR:` 开头的），脱敏、单行截断；没有回空串。
    给修复会话里的模型看：它看不到完整 stderr，只靠一个 code 分不清「没轮子」与「没这个包」。"""
    from .readscope import scrub

    lines: list[str] = []
    for m in _ERROR_LINE_RE.finditer(out or ""):
        line = m.group(0).strip()
        if line and line not in lines:
            lines.append(line[:240])
    return scrub("\n".join(lines[-limit:]))


def _default_installer(
    python: str, requirements: list[str], cancel: threading.Event | None = None
) -> dict:
    from .. import deprepair, pool

    cancel = cancel or threading.Event()
    done: list[str] = []
    key = deprepair._env_key(deprepair.TARGET_PROJECT_VENV, python, "")
    try:
        with pool.mutating_environment(key, python):
            for req in requirements:
                if cancel.is_set():
                    return {"ok": False, "code": "cancelled", "done": done}
                code, out = deprepair._pip_install(python, req, cancel, None)
                if code:
                    # 只按安装器自己的输出分类（`deprepair.classify_pip_failure`）：不再起第二个 pip 去「查索引」——
                    # `pip index` 不继承 `[install]` 配置的源（no-index / find-links / 私有 index），会把包名发到公网
                    # PyPI，判断也不对。`dependency_not_found` 给模型的提示不断言「不存在」（见 `change._INSTALL_HINTS`）
                    res = {"ok": False, "code": str(code), "done": done}
                    if detail := failure_detail(out):
                        res["detail"] = detail
                    return res
                done.append(req)
    except pool.EnvironmentBusy:
        return {"ok": False, "code": "env_busy", "done": done}
    return {"ok": True, "code": "", "done": done}


def _default_uninstaller(
    python: str, distributions: list[str], cancel: threading.Event | None = None
) -> dict:
    from .. import deprepair, pool

    cancel = cancel or threading.Event()
    done: list[str] = []
    key = deprepair._env_key(deprepair.TARGET_PROJECT_VENV, python, "")
    try:
        with pool.mutating_environment(key, python):
            for dist in distributions:
                if cancel.is_set():
                    return {"ok": False, "code": "cancelled", "done": done}
                code, _out = deprepair._pip_uninstall(python, dist, cancel, None)
                if code:
                    return {"ok": False, "code": str(code), "done": done}
                done.append(dist)
    except pool.EnvironmentBusy:
        return {"ok": False, "code": "env_busy", "done": done}
    return {"ok": True, "code": "", "done": done}


def _default_python_changer(req: dict, cancel: threading.Event | None = None) -> dict:
    """`change_python` 的缺省执行器（D3 补）：走 `pyversionfix` 的计划 + 执行（只在 Tavotto 受管环境里另建一代，
    基础解释器只有 `managedenv.base_pick` 一个挑选处；用户的环境一个字节不碰），建好后按现有联合计划补脚本要的依赖。
    `req = {"version": 可省, "project", "script"}`。`version` 省略 = 取脚本 / 项目声明的 `requires-python` 与 Tavotto 支持区间的交集；
    指定了就把范围钉成这个小版本。推不出 / 没有可供应的基础解释器 → `python_change_unavailable`。返回里 `python` 是新环境的解释器。"""
    from .. import managedenv, pyversionfix

    cancel = cancel or threading.Event()
    if not pyversionfix.auto_change_enabled():  # 运维开关（#1381）
        return {
            "ok": False,
            "code": pyversionfix.ERROR_UNAVAILABLE,
            "message": "Switching Python versions is turned off on this computer.",
        }
    version = req.get("version")
    failure: dict = {"code": pyversionfix.CODE_UNSUPPORTED, "script": str(req.get("script") or "")}
    if isinstance(version, str) and version:
        failure["requires_python"] = f"=={version}" + (".*" if version.count(".") == 1 else "")
    project = str(req.get("project") or "")
    plan = pyversionfix.plan_python_change(failure, project)
    if plan is None:
        return {
            "ok": False,
            "code": pyversionfix.ERROR_UNAVAILABLE,
            "message": "Tavotto could not find a Python version to build an environment with here "
            "(no suitable Python on this computer and none it may download).",
        }
    if cancel.is_set():  # 规划完、动手前先看一眼（与 #1381 同一处）
        return {
            "ok": False,
            "code": "cancelled",
            "message": "The fix was cancelled; nothing was changed.",
        }
    res = pyversionfix.apply_python_change(plan, None, cancel)
    if not res.get("ok"):
        return {"ok": False, "code": str(res.get("code") or pyversionfix.ERROR_FAILED)[:60]}
    python = managedenv.python_of(project)
    return {
        "ok": True,
        "to": plan.to_version,
        "python": str(python) if python else None,
        "message": f"Created a Tavotto environment with Python {plan.to_version} and installed what the script imports.",
    }


def call_executor(fn, *a) -> dict:
    try:
        out = fn(*a)
    except Exception as exc:  # noqa: BLE001 - 执行器的缺陷是这次调用的失败，不抛出会话
        return {"ok": False, "code": f"executor_{type(exc).__name__}"[:60], "done": []}
    return (
        out if isinstance(out, dict) else {"ok": False, "code": "executor_bad_result", "done": []}
    )


def run_python_changer(ctx: FixContext, req: dict) -> dict:
    if ctx.python_changer is not None:
        return call_executor(ctx.python_changer, req)
    return call_executor(_default_python_changer, req, ctx.cancel)


def run_installer(ctx: FixContext, python: str, requirements: list[str]) -> dict:
    from .. import depplan, projectenv

    ctx.verification = None
    try:
        if ctx.cancel is not None and ctx.cancel.is_set():
            return {"ok": False, "code": "cancelled", "done": []}
        if ctx.installer is not None:
            return call_executor(ctx.installer, python, requirements)
        return call_executor(_default_installer, python, requirements, ctx.cancel)
    finally:
        # Pip may have changed some packages before failing. Both facts and the
        # distribution index must describe the post-operation environment.
        depplan.reset_cache(python)
        projectenv.reset_cache(ctx.project_root)


def run_uninstaller(ctx: FixContext, python: str, distributions: list[str]) -> dict:
    from .. import depplan, projectenv

    ctx.verification = None
    try:
        if ctx.cancel is not None and ctx.cancel.is_set():
            return {"ok": False, "code": "cancelled", "done": []}
        if ctx.uninstaller is not None:
            return call_executor(ctx.uninstaller, python, distributions)
        return call_executor(_default_uninstaller, python, distributions, ctx.cancel)
    finally:
        depplan.reset_cache(python)
        projectenv.reset_cache(ctx.project_root)
