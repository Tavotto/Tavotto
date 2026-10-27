"""AI 修复工具箱的注册表（ADR 0149）。

一张 `REGISTRY`，两种接法共用：内置执行器经 `build_agent_tools(ctx)` 变成原生工具调用；CLI 经 MCP 的接法（D3）读同一张表。
D1 登记「看」「试」两组；D2a 加「改」组（change.py：装包 / 切执行方式 / 换 Python / 换环境 / 跑命令 / 转写 / 回滚，改动前自动打快照）；D2b 加「问与报」五个（`ask.py`：弹卡片等用户点，都是 waits_for_user）。
工具拿到注入的 `FixContext`，不 import app；每次调用写一行本地审计（`audit.py`，内容不落盘）。
"""

from __future__ import annotations

from .. import exectrace
from ..agent_runner.tools import Tool
from ..agent_runner.wires import ToolResult
from .ask import ASK_TOOLS
from .audit import AuditLog
from .base import BLOCKED_CODES, FIX_SESSION_TIERS, TIERS, FixContext, FixTool
from .change import CHANGE_TOOLS
from .command import COMMAND_TOOLS
from .link import LINK_TOOLS
from .observe import OBSERVE_TOOLS
from .readscope import scrub
from .trial import TRIAL_TOOLS

REGISTRY: dict[str, FixTool] = {
    t.name: t
    for t in (*OBSERVE_TOOLS, *TRIAL_TOOLS, *CHANGE_TOOLS, *LINK_TOOLS, *COMMAND_TOOLS, *ASK_TOOLS)
}


exectrace.register_vocabulary("tool", REGISTRY)
exectrace.register_vocabulary("tier", TIERS)


def _failed(name: str, code: str, message: str) -> ToolResult:
    return ToolResult("", name, message, is_error=True, code=code, summary=f"error: {code}")


def _approved_by(result: ToolResult, ctx: FixContext) -> str:
    if result.is_error and result.code in BLOCKED_CODES:
        return "blocked"
    return "user" if ctx.call_meta.get("approved_by") == "user" else "policy"


def run_tool(
    tool: FixTool, ctx: FixContext, args: object, audit: AuditLog | None = None
) -> ToolResult:
    """所有接法共用的调用入口：档位检查 → handler → 脱敏 → 审计。不抛。

    执行追踪的 tool_call **只在这里记**（内置执行器与 CLI 经 MCP 都走这一个入口）：父层「等工具返回」不另记一次调用。"""
    ctx.step += 1
    ctx.call_meta = {}
    trace = getattr(ctx, "trace", None) or exectrace.NullTrace()
    span = trace.start_span(
        "tool_call",
        "tool",
        parent=getattr(ctx, "trace_parent", None),
        attributes={"tool": tool.name, "tier": tool.tier},
    )
    ctx.trace_tool_span = span
    if tool.tier not in ctx.allowed_tiers:
        result = _failed(
            tool.name, "tier_not_allowed", "This tool is not available in this session."
        )
    elif not isinstance(args, dict):
        result = _failed(tool.name, "bad_arguments", "Arguments must be a JSON object.")
    else:
        changes_execution = tool.tier in {"tavotto", "listed", "user_env", "outward"}
        if changes_execution:
            # Even a failed command/install can have made a partial change. A
            # previous application run must never certify that new state.
            ctx.verification = None
        try:
            result = tool.handler(ctx, args)
        except Exception as exc:  # noqa: BLE001 - 工具缺陷不抛出会话；只报类型名
            result = _failed(
                tool.name, "tool_crashed", f"The tool failed unexpectedly ({type(exc).__name__})."
            )
        finally:
            if changes_execution:
                from .. import depplan, projectenv

                # Arbitrary approved commands can touch any registered/current
                # environment. Retire facts and distribution indexes together.
                depplan.reset_cache()
                projectenv.reset_cache(ctx.project_root)
    if result.mutated:
        ctx.verification = None
    result.content = scrub(result.content)
    ctx.trace_tool_span = None
    approved_by = _approved_by(result, ctx)
    # 等用户的那张卡已经有了决定（批准 / 拒绝）：工具这一层与它的 user_wait 说同一句话（审计的 approved_by 不动）
    decided = getattr(ctx, "trace_wait_decision", None)
    ctx.trace_wait_decision = None
    # 被策略挡下的调用根本没执行——是护栏在工作，不是失败：记 cancelled（码照留），不当成「最早失败」（真实 Codex 验证发现）
    blocked = approved_by == "blocked"
    trace.end_span(
        span,
        exectrace.STATUS_CANCELLED
        if blocked
        else exectrace.STATUS_FAILED
        if result.is_error
        else exectrace.STATUS_OK,
        error_code=result.code if result.is_error else None,
        attributes={"approved_by": decided if decided and not blocked else approved_by},
    )
    if audit is not None:
        audit.record(
            session=ctx.session_id,
            step=ctx.step,
            tool=tool.name,
            tier=tool.tier,
            args=args,
            approved_by=_approved_by(result, ctx),
            result={"ok": not result.is_error, "code": result.code, "summary": result.summary},
            snapshot=ctx.call_meta.get("snapshot"),
            approved_step=ctx.call_meta.get("approved_step"),
            sandbox=ctx.call_meta.get("sandbox"),
            command_tier=ctx.call_meta.get("command_tier"),
        )
    return result


def build_agent_tools(ctx: FixContext, audit: AuditLog | None = None) -> list[Tool]:
    """注册表 → 内置执行器的工具（`agent_runner.tools.Tool`）。tier 随工具带上，但不会发给模型。"""
    audit = audit if audit is not None else AuditLog()
    tools: list[Tool] = []
    for ft in REGISTRY.values():

        def handler(args: dict, _ft: FixTool = ft) -> ToolResult:
            return run_tool(_ft, ctx, args, audit)

        tools.append(
            Tool(
                ft.name,
                ft.description,
                ft.schema,
                handler,
                tier=ft.tier,
                waits_for_user=ft.waits_for_user,
            )
        )
    return tools


__all__ = [
    "REGISTRY",
    "TIERS",
    "FIX_SESSION_TIERS",
    "AuditLog",
    "FixContext",
    "FixTool",
    "build_agent_tools",
    "run_tool",
]
