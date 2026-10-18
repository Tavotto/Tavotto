"""MCP 工具调用的匿名用量事件 `mcp_tool_call`（ADR 0134 §MCP）。

主语：**MCP server 进程**里，一次 `tools/call` 派发之后交给 `engine/telemetry.capture` 的那条事件——
它的属性只能是白名单闭集，参数 / 返回内容 / 错误原文在结构上进不去；同意与硬开关仍由引擎那一份判。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine import telemetry as tel  # noqa: E402
from tavotto_mcp import (  # noqa: E402
    bridge,
    server as srv,  # noqa: E402
    usage,
)
from tavotto_mcp.rpc import RpcError  # noqa: E402

SECRET = "/Users/alice/secret-project/fig_private.py"


@pytest.fixture
def captured(monkeypatch):
    """截下 `bridge.note_tool_call` 交给引擎的属性（真走 `telemetry.validate`）。"""
    seen: list[dict] = []

    def fake_capture(event, props):
        seen.append({"event": event, "properties": tel.validate(event, props)})
        return True

    monkeypatch.setattr(bridge.engine_telemetry, "capture", fake_capture)
    return seen


def _server(client_name: str | None = "codex-mcp-client") -> srv.Server:
    s = srv.Server(conn=object())  # 不走 stdio：只用 dispatch
    s.initialize({"protocolVersion": "2025-06-18", "clientInfo": {"name": client_name}})
    return s


def test_tool_names_mirror_handlers_and_engine_enum():
    assert set(usage.TOOLS) == set(srv.HANDLERS)
    assert set(tel.EVENTS["mcp_tool_call"]["tool"]["values"]) == set(usage.TOOLS) | {"other"}


def test_ok_call_emits_closed_set_only(monkeypatch, captured):
    monkeypatch.setitem(
        srv.HANDLERS,
        "tavotto_health",
        lambda args: {
            "content": [{"type": "text", "text": SECRET}],
            "structuredContent": {"path": SECRET},
        },
    )
    _server().dispatch("tools/call", {"name": "tavotto_health", "arguments": {"p": SECRET}})
    (ev,) = captured
    assert ev["event"] == "mcp_tool_call"
    assert ev["properties"] == {
        "tool": "tavotto_health",
        "outcome": "ok",
        "error_class": "none",
        "duration_bucket": "lt_1s",
        "host": "codex",
        "elided": False,
    }
    assert "secret" not in json.dumps(ev)


def test_bridge_error_is_tool_error_with_coarse_class(monkeypatch, captured):
    def boom(args):
        raise bridge.BridgeError(f"越界：{SECRET}", code="path_out_of_scope")

    monkeypatch.setitem(srv.HANDLERS, "tavotto_open_figure", boom)
    result = _server("claude-code").dispatch(
        "tools/call", {"name": "tavotto_open_figure", "arguments": {"script_path": SECRET}}
    )
    assert result["isError"] is True
    (ev,) = captured
    assert ev["properties"]["outcome"] == "tool_error"
    assert ev["properties"]["error_class"] == "workspace"
    assert ev["properties"]["host"] == "claude"
    assert "secret" not in json.dumps(ev)


def test_unknown_tool_is_protocol_error_named_other(captured):
    with pytest.raises(RpcError):
        _server(None).dispatch("tools/call", {"name": SECRET, "arguments": {}})
    (ev,) = captured
    assert ev["properties"]["tool"] == "other"
    assert ev["properties"]["outcome"] == "protocol_error"
    assert ev["properties"]["host"] == "other"


def test_unexpected_exception_is_internal_error_and_still_raises(monkeypatch, captured):
    def crash(args):
        raise RuntimeError(SECRET)

    monkeypatch.setitem(srv.HANDLERS, "tavotto_export", crash)
    with pytest.raises(RuntimeError):
        _server().dispatch("tools/call", {"name": "tavotto_export", "arguments": {}})
    (ev,) = captured
    assert ev["properties"]["outcome"] == "internal_error"
    assert ev["properties"]["error_class"] == "none"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("workspace_confirmation_declined", "workspace"),
        ("no_workspace_root", "workspace"),
        ("preflight_blocked", "needs_input"),
        ("bad_patches", "request"),
        ("engine_too_old", "environment"),
        ("render_failed", "render"),
        ("某个 worker 透传上来的码", "other"),
        (None, "other"),
    ],
)
def test_error_class_folds_known_codes_only(code, expected):
    assert usage.error_class(code) == expected


@pytest.mark.parametrize(
    ("seconds", "bucket"),
    [
        (0.0, "lt_1s"),
        (0.999, "lt_1s"),
        (1.0, "1_5s"),
        (29.9, "5_30s"),
        (30.0, "30_120s"),
        (120.0, "gt_120s"),
    ],
)
def test_duration_buckets(seconds, bucket):
    assert usage.duration_bucket(seconds) == bucket


def test_elided_result_is_flagged():
    props = usage.properties(
        "tavotto_open_figure",
        "ok",
        seconds=0.1,
        client_name="codex",
        result={"structuredContent": {"elided": {"fields": ["manifest"]}}},
    )
    assert props["elided"] is True
    tel.validate("mcp_tool_call", props)


def test_every_property_value_passes_the_engine_allowlist():
    """`usage` 能折出的每一种值都在引擎白名单里——否则那条事件会在 validate 被安静丢掉，看起来全绿。"""
    spec = tel.EVENTS["mcp_tool_call"]
    classes = {
        usage.error_class(c)
        for c in (
            "path_out_of_scope",
            "preflight_blocked",
            "bad_dpi",
            "engine_too_old",
            "render_failed",
            "x",
        )
    }
    assert classes | {"none"} == set(spec["error_class"]["values"])
    buckets = {usage.duration_bucket(s) for s in (0, 1, 5, 30, 120)}
    assert buckets == set(spec["duration_bucket"]["values"])
    assert {usage.host_kind(n) for n in ("codex", "claude-code", None)} == set(
        spec["host"]["values"]
    )


def test_capture_respects_consent_and_hard_switch(monkeypatch):
    """端到端走真的 `capture`（conftest 已把配置目录隔离到临时目录）：
    unset 不发；硬开关压过同意；当前版本同意才发。"""
    posted: list[dict] = []
    monkeypatch.setattr(tel, "_post", posted.append)
    monkeypatch.delenv("TAVOTTO_TELEMETRY_ENDPOINT", raising=False)
    tel.reset_for_tests()
    props = usage.properties("tavotto_health", "ok", seconds=0.2, client_name="codex")

    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    assert tel.settings()["consent"] == tel.CONSENT_UNSET
    bridge.note_tool_call(props)
    tel.flush(2)
    assert posted == []

    tel.set_consent(tel.CONSENT_ENABLED)
    tel.flush(2)
    posted.clear()
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    bridge.note_tool_call(props)
    tel.flush(2)
    assert posted == []

    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY")
    bridge.note_tool_call(props)
    assert tel.flush(2)
    assert [p["event"] for p in posted] == ["mcp_tool_call"]
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    tel.reset_for_tests()
