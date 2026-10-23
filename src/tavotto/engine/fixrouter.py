"""智能修复路由（ADR 0148）：用户脚本跑不了时，按失败类型选**唯一**一种修法。

一个纯函数 `choose(failure, ctx) -> Remedy`，输出是闭集（`REMEDY_KINDS`），写进准备会话报告的 `remedy` 字段，
前端 `prepView` 只按它画主按钮。本模块**不执行**任何修法：

* `switch_native`：只是标注——真正的 safe→native 静默回退仍由 `execpolicy.decide_fallback` 做（ADR 0127，不改）；
* `install_packages`：装包仍走 `deprepair` / `convertdeps` 的既有授权（本模块只说装什么、要不要用户点）；
* `change_python`：本版只产出 remedy（执行在后续 PR），前端此时走现有「选择运行环境」入口；
* `convert_to_matplotlib`：复用现有「生成可编辑版本」（ADR 0143）；
* `ai_fix` / `connect_ai`：AI 环境修复任务在后续 PR；
* `feedback`：发送问题反馈（ADR 0118，每次确认、不记住）。

顺序即优先级（取第一条成立的）：safe 守卫拦下 → 缺依赖 → Python 版本类 → 转写 → 其余按「AI 连没连」。
**一次「运行」最多走 3 种修法**（读 `attempt_history`），第 4 次直接 `feedback`；同一种修法一次运行只走一回。

纯标准库 + 常量：不起进程、不读磁盘。「AI 连没连」「环境允不允许直接装」由调用方（`prepsession`）量好放进 `Ctx`。
"""

from __future__ import annotations

import dataclasses
import re

KIND_SWITCH_NATIVE = "switch_native"
KIND_INSTALL_PACKAGES = "install_packages"
KIND_CHANGE_PYTHON = "change_python"
KIND_CONVERT = "convert_to_matplotlib"
KIND_AI_FIX = "ai_fix"
KIND_CONNECT_AI = "connect_ai"
KIND_FEEDBACK = "feedback"
#: remedy 的 `kind` 闭集（前端 `Remedy` 联合类型的同源对：`tests/test_fixrouter_pair.py`）
REMEDY_KINDS = (
    KIND_SWITCH_NATIVE,
    KIND_INSTALL_PACKAGES,
    KIND_CHANGE_PYTHON,
    KIND_CONVERT,
    KIND_AI_FIX,
    KIND_CONNECT_AI,
    KIND_FEEDBACK,
)
#: 一次运行里最多走几种修法；已经走满再失败就直接 `feedback`
MAX_REMEDIES_PER_RUN = 3

#: 字面量是有意的（同 `userenvs._HEALTH_CODE_TO_REJECT`）：它们是跨版本稳定的对外码，路由不为取常量依赖更多内部名字
CODE_BLOCKED = "safe_file_operation_blocked"
CODE_MISSING_DEPENDENCY = "missing_dependency"
#: Python 版本类失败码：项目环境的 Python 不在支持范围 / 叠加的 ROOT 与 Python 小版本对不上 / 运行配置的版本不被支持
PYTHON_VERSION_CODES = frozenset(
    {
        "project_env_unsupported_python",
        "root_python_mismatch",
        "python_out_of_range",
        "unsupported_python_version",
    }
)

_DIST = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclasses.dataclass(frozen=True)
class Ctx:
    """路由要的全部事实（调用方量好，本模块不读环境）。"""

    #: 本机有没有 `usable` 的 AI（`ai_bridge.capabilities()['agents'][*].usable`——界面选择器与后端 run 同一判据）
    ai_connected: bool = False
    #: AI 修复的轮数用完了（后续 PR 的 AI 环境修复任务才会置真；本版恒 False）
    ai_rounds_exhausted: bool = False
    #: 允许 native 回退（项目没设「只用安全运行」、运维开关没关）——只影响是否标注，执行仍由 execpolicy 裁
    native_allowed: bool = False
    #: 缺的包能不能在会话里装（`prepsession._observe_missing` 的 `installable`）与它们的发行包名（已规范，非空才算）
    missing_installable: bool = False
    packages: tuple[str, ...] = ()
    #: 这个环境允许不问就装（自动安装开关开着，且环境是 Tavotto 自管的或用户已按环境同意过——`convertdeps` 的口径）
    auto_install_allowed: bool = False
    #: 当前 Python 版本（可展示字符串）与可建议的目标版本（没有就 None）
    python_from: str = ""
    python_to: str | None = None
    #: `no_figure_hint`（`rasterhint.detect` 的结果）与来源语言（`sourcelang`：`r` / `matlab` / `python` / None）
    no_figure_hint: dict | None = None
    source_language: str | None = None
    #: 账本 `attempt_history`（`AttemptLedger.history()`）与这次运行里已经装过包的事实
    history: tuple[dict, ...] = ()
    installed_this_run: bool = False


def tried_in_run(ctx: Ctx) -> tuple[str, ...]:
    """这一次运行里已经走过的修法（按先后、去重）。来源只有账本与依赖作业，不另记一份状态：

    * 账本里本次 run 相对上一次换了执行语义 -> `switch_native`；换了解释器 -> `change_python`；
    * 本次 run 里装过包 -> `install_packages`（调用方从依赖作业读出）。"""
    rows = [r for r in ctx.history if isinstance(r, dict)]
    run = max((int(r.get("run") or 0) for r in rows), default=0)
    out: list[str] = []
    if ctx.installed_this_run:
        out.append(KIND_INSTALL_PACKAGES)
    for r in rows:
        if int(r.get("run") or 0) != run:
            continue
        changed = r.get("changed") or ()
        if "profile" in changed and KIND_SWITCH_NATIVE not in out:
            out.append(KIND_SWITCH_NATIVE)
        if "interpreter" in changed and KIND_CHANGE_PYTHON not in out:
            out.append(KIND_CHANGE_PYTHON)
    return tuple(out)


def dist_name(requirement: str) -> str:
    """需求串 -> 发行包名（不规范化大小写之外的东西；`scipy>=1.2` -> `scipy`）。认不出回空串。"""
    m = _DIST.match(requirement or "")
    return m.group(1) if m else ""


def choose(failure: str, ctx: Ctx) -> dict:
    """失败码 + 事实 -> remedy（`REMEDY_KINDS` 里的一种，形状见 ADR 0148）。纯函数。"""
    code = str(failure or "")
    tried = tried_in_run(ctx)
    if len(tried) >= MAX_REMEDIES_PER_RUN:
        return {"kind": KIND_FEEDBACK, "tried": list(tried)}
    if code == CODE_BLOCKED and ctx.native_allowed and KIND_SWITCH_NATIVE not in tried:
        return {"kind": KIND_SWITCH_NATIVE, "silent": True}
    if (
        code == CODE_MISSING_DEPENDENCY
        and ctx.missing_installable
        and ctx.packages
        and KIND_INSTALL_PACKAGES not in tried
    ):
        from . import (
            convertdeps,
        )  # 延迟：convertdeps 经 deprepair 通向既有 import 环（同 ai_convert 的说明）

        listed = all(convertdeps.in_allowlist(p) for p in ctx.packages)
        return {
            "kind": KIND_INSTALL_PACKAGES,
            "packages": list(ctx.packages),
            "ask": not (listed and ctx.auto_install_allowed),
        }
    if code in PYTHON_VERSION_CODES and KIND_CHANGE_PYTHON not in tried:
        return {"kind": KIND_CHANGE_PYTHON, "from": ctx.python_from, "to": ctx.python_to}
    hint = ctx.no_figure_hint or {}
    if KIND_CONVERT not in tried:
        if hint.get("kind") == "raster_script":
            return {"kind": KIND_CONVERT, "source": "python"}
        if ctx.source_language in ("r", "matlab"):
            return {"kind": KIND_CONVERT, "source": ctx.source_language}
    if ctx.ai_rounds_exhausted:
        return {"kind": KIND_FEEDBACK, "tried": list(tried)}
    if ctx.ai_connected:
        return {"kind": KIND_AI_FIX}
    return {"kind": KIND_CONNECT_AI}
