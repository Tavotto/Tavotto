"""一次 tools/call → 一条 `mcp_tool_call` 用量事件的**属性**（只算属性，不发）。

发送走引擎那一份 `engine/telemetry.capture`（三档同意、白名单、硬开关、有界队列都在那里），
这里只把一次调用折成闭集：哪个工具、怎么结束、错误的大类、耗时分档、哪类宿主、结果是否被裁剪。
**参数与返回内容一个字节都不进来**：路径、脚本名、stem、gid、图内文字、错误原文都不是闭集，
`engine/telemetry.EVENTS["mcp_tool_call"]` 也没有接得住它们的属性（ADR 0134 §MCP）。

错误码 → 大类是**插件自己的表**：码是 bridge / roots 里写死的稳定标识，worker 透传上来的码
不在表里就落 `other`——判不出就不判，不按子串猜。
"""

from __future__ import annotations

#: 与 `server.HANDLERS` 同一组工具名；不认识的（含协议层就被拒的未知工具）一律 `other`。
#: 两侧由 tests/test_mcp_usage.py 对拍。
TOOLS = (
    "tavotto_health",
    "tavotto_open_figure",
    "tavotto_apply_overrides",
    "tavotto_normalize_figure",
    "tavotto_preflight",
    "tavotto_export",
    "tavotto_verify_replay",
    "tavotto_refresh_project",
    "tavotto_session_state",
    "tavotto_close_session",
)

_WORKSPACE = frozenset(
    {
        "no_workspace_root",
        "path_out_of_scope",
        "ambiguous_workspace_root",
        "argv_path_out_of_scope",
    }
)
_NEEDS_INPUT = frozenset(
    {
        "preflight_blocked",
        "dependency_impact_required",
        "environment_generation_required",
        "workdir_confirmation_required",
        "argv_unverifiable",
    }
)
_REQUEST = frozenset(
    {
        "bad_format",
        "bad_dpi",
        "bad_patches",
        "unknown_profile",
        "unknown_session",
        "stem_required",
        "stem_not_parameterizable",
        "not_found",
        "no_figure",
        "no_project",
        "ambiguous_project",
        "no_registry",
        "bad_registry",
        "no_manifest",
        "export_conflict",
    }
)
_ENVIRONMENT = frozenset(
    {
        "engine_too_old",
        "explicit_python_unusable",
        "project_python_unusable",
        "dependency_target_invalid",
    }
)
_RENDER = frozenset(
    {
        "render_failed",
        "batch_all_failed",
        "preview_unreadable",
        "acceptance_failed",
        "normalize_internal",
        "worker_error",
        "session_restore_failed",
        "handoff_failed",
        "remote_open_failed",
    }
)

#: 耗时分档的上界（秒，左闭右开）。量的是工具本身，**不含**等用户点授权框的那段。
_DURATION_BUCKETS = ((1.0, "lt_1s"), (5.0, "1_5s"), (30.0, "5_30s"), (120.0, "30_120s"))


def error_class(code: object) -> str:
    if not isinstance(code, str) or not code:
        return "other"
    # `workspace_*` 是 roots.py 的一族码（确认 / roots 应答 / 根过宽 / 根变了），全是同一类问题
    if code in _WORKSPACE or code.startswith("workspace_"):
        return "workspace"
    for name, codes in (
        ("needs_input", _NEEDS_INPUT),
        ("request", _REQUEST),
        ("environment", _ENVIRONMENT),
        ("render", _RENDER),
    ):
        if code in codes:
            return name
    return "other"


def duration_bucket(seconds: float) -> str:
    for upper, name in _DURATION_BUCKETS:
        if seconds < upper:
            return name
    return "gt_120s"


def host_kind(client_name: object) -> str:
    """宿主粗分，与 `ai_assistant_invoked.agent` 同一组取值。名字本身不发。"""
    name = client_name.lower() if isinstance(client_name, str) else ""
    if "codex" in name:
        return "codex"
    if "claude" in name:
        return "claude"
    return "other"


def properties(
    tool: object,
    outcome: str,
    *,
    seconds: float,
    client_name: object,
    result: dict | None = None,
) -> dict:
    """`outcome`：ok / tool_error（工具回了 isError）/ protocol_error（RpcError）/ internal_error。"""
    body = (result or {}).get("structuredContent") if isinstance(result, dict) else None
    body = body if isinstance(body, dict) else {}
    return {
        "tool": tool if tool in TOOLS else "other",
        "outcome": outcome,
        "error_class": error_class(body.get("code")) if outcome == "tool_error" else "none",
        "duration_bucket": duration_bucket(seconds),
        "host": host_kind(client_name),
        "elided": isinstance(body.get("elided"), dict),
    }
