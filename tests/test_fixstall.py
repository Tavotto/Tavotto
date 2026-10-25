"""AI 修复 D3：进度可见（activity）与卡住检测（stalled）——Codex CLI / Claude CLI / 内置循环三种模式都要有。

判据的主语：**会话对外的公开状态**（`SESSIONS.get` 的 `activity` / `stalled` / `outcome` / `reason`）、**磁盘上的审计行**
（卡住与自动取消的原因码进 `fix_audit.jsonl`，诊断包「修复经过」据此）、**假 CLI 进程的生死**。
假 CLI = 打印几行事件就睡着的小 Python 进程；假模型 = `complete()` 阻塞的对象。不起真实 codex / claude，不联网。
"""

# ruff: noqa: F811 — 夹具从 test_fixmcp 导入复用
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import replace

import pytest

from tavotto.engine import ai_agents, fixsession
from tavotto.engine.agent_runner.wires import ToolCall, Turn
from tavotto.engine.fixtools import REGISTRY, FixTool
from tavotto.engine.fixtools.base import obj, ok
from test_fixmcp import audit_rows, box, procprobe, start_session, wait_finished  # noqa: F401

CODEX_BEGIN = json.dumps(
    {
        "type": "item.started",
        "item": {"type": "mcp_tool_call", "server": "tavotto_fix", "tool": "install_packages"},
    }
)
CODEX_APPROVAL = json.dumps(
    {"type": "item.started", "item": {"type": "approval_request", "tool": "change_python"}}
)


def sleeper(*lines: str) -> list[str]:
    code = (
        "import sys,time\n"
        + "".join(f"print({ln!r}); sys.stdout.flush()\n" for ln in lines)
        + "time.sleep(25)"
    )
    return [sys.executable, "-c", code]


@pytest.fixture(autouse=True)
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(fixsession, "WATCH_TICK", 0.1)
    monkeypatch.setattr(fixsession, "DELIVER_WAIT", 0.6)
    monkeypatch.setattr(fixsession, "STALL_IDLE", 0.8)
    monkeypatch.setattr(fixsession, "STALL_HARD", 2.0)
    monkeypatch.setattr(fixsession, "FORCE_SETTLE", 0.5)


def public(s):
    return fixsession.SESSIONS.get(s.id, "pj")


def wait_until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition never became true")


def codes(box, s) -> list[str]:
    return [
        r["result"]["code"]
        for r in audit_rows(box)
        if r["session"] == s.id and r["tool"] == "_session"
    ]


# =========================================================================== CLI：工具没送达 / 在等审批
def test_codex_tool_begin_without_a_callback_is_flagged_then_auto_cancelled(box):
    parse = ai_agents.CodexAgent().fix_event_parser()
    s, _ = start_session(box, sleeper(CODEX_BEGIN), parse=parse)
    wait_until(lambda: public(s)["stalled"] is not None)
    assert public(s)["stalled"] == {
        "reason": "cli_tool_not_delivered",
        "detail": "install_packages",
    }
    assert public(s)["state"] == "running"  # 先让界面说原因，不是立刻杀
    wait_finished(s, 15)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "cli_tool_not_delivered")
    assert "cli_tool_not_delivered" in codes(box, s)  # 原因进审计（诊断包「修复经过」）
    assert public(s)["stalled"] is None and fixsession.SESSIONS.active() is None


def test_a_begin_that_is_delivered_in_time_is_not_a_stall(box, monkeypatch):
    monkeypatch.setattr(fixsession, "STALL_IDLE", 60.0)  # 这里只看「没送达」这一条
    parse = ai_agents.CodexAgent().fix_event_parser()
    s, token = start_session(box, sleeper(CODEX_BEGIN), parse=parse)
    time.sleep(0.2)
    fixsession.SESSIONS.call_tool(s.id, token, "install_packages", {"env": "zz", "packages": ["x"]})
    time.sleep(1.2)  # 超过 DELIVER_WAIT 也不该报：回调已经到过
    assert public(s)["stalled"] is None
    fixsession.SESSIONS.cancel(s.id, "pj")


def test_cli_approval_request_names_the_tool_and_clears_when_a_callback_arrives(box, monkeypatch):
    monkeypatch.setattr(fixsession, "STALL_HARD", 60.0)  # 不让自动取消把「卡住」清掉而冒充恢复
    monkeypatch.setattr(fixsession, "STALL_IDLE", 60.0)
    parse = ai_agents.CodexAgent().fix_event_parser()
    s, token = start_session(box, sleeper(CODEX_APPROVAL), parse=parse)
    wait_until(lambda: public(s)["stalled"] is not None)
    assert public(s)["stalled"] == {"reason": "cli_waiting_approval", "detail": "change_python"}
    fixsession.SESSIONS.call_tool(
        s.id, token, "read_run_report", {}
    )  # 用户在 CLI 里点了批准，调用送达
    wait_until(lambda: public(s)["stalled"] is None)
    time.sleep(0.5)
    assert public(s)["stalled"] is None and public(s)["state"] == "running"
    fixsession.SESSIONS.cancel(s.id, "pj")


def test_a_silent_cli_with_no_tool_activity_is_flagged_as_stalled(box):
    s, _ = start_session(box, sleeper())
    wait_until(lambda: public(s)["stalled"] is not None)
    assert public(s)["stalled"]["reason"] == "stalled"
    wait_finished(s, 15)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "stalled")
    assert "stalled" in codes(box, s)


def test_waiting_for_the_user_to_click_a_card_is_never_a_stall(box):
    s, token = start_session(box, sleeper())
    threading.Thread(
        target=lambda: fixsession.SESSIONS.call_tool(
            s.id, token, "propose_fix", {"summary": "装 numpy"}
        ),
        daemon=True,
    ).start()
    wait_until(lambda: public(s)["state"] == "waiting")
    time.sleep(2.5)  # 远超 STALL_IDLE 与 STALL_HARD
    assert public(s)["stalled"] is None and public(s)["state"] == "waiting"
    fixsession.SESSIONS.cancel(s.id, "pj")


def test_a_tool_that_is_still_running_is_not_a_stall(box, monkeypatch):
    release = threading.Event()
    monkeypatch.setitem(
        REGISTRY,
        "slow_tool",
        FixTool("slow_tool", "d", obj({}), lambda c, a: (release.wait(20), ok("x"))[1]),
    )
    s, token = start_session(box, sleeper())
    t = threading.Thread(
        target=lambda: fixsession.SESSIONS.call_tool(s.id, token, "slow_tool", {}), daemon=True
    )
    t.start()
    time.sleep(2.5)
    assert public(s)["stalled"] is None  # 工具自己在跑（长安装）不是卡住
    release.set()
    t.join(10)
    fixsession.SESSIONS.cancel(s.id, "pj")


@pytest.mark.parametrize("cli", [False, True])
@pytest.mark.parametrize("custom_limit", [False, True])
def test_tool_over_total_budget_is_visible_and_cancellation_reaches_it(
    box, monkeypatch, cli, custom_limit
):
    if custom_limit:
        limits = replace(fixsession.FIX_LIMITS, max_seconds=0.4)
    else:
        monkeypatch.setattr(fixsession, "TOOL_TIMEOUT", 0.4)
        limits = fixsession.FIX_LIMITS
    entered, exited = threading.Event(), threading.Event()

    def cooperative(ctx, _args):
        entered.set()
        assert ctx.cancel.wait(8), "the watchdog never cancelled the tool"
        exited.set()
        return ok("cancelled")

    monkeypatch.setitem(
        REGISTRY, "hung_install", FixTool("hung_install", "d", obj({}), cooperative)
    )
    if cli:
        s, token = start_session(box, sleeper(), limits=limits)
        thread = threading.Thread(
            target=lambda: fixsession.SESSIONS.call_tool(s.id, token, "hung_install", {}),
            daemon=True,
        )
        thread.start()
    else:
        s = start_builtin(box, Scripted([("hung_install", {})]), limits=limits)
    assert entered.wait(5)
    assert exited.wait(8)
    wait_finished(s, 10)
    reason = "budget" if custom_limit else "stalled"
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, reason)
    assert reason in codes(box, s)
    assert fixsession.SESSIONS.active() is None


# =========================================================================== activity：三种模式各一条
def test_activity_follows_tool_calls_in_cli_mode(box, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(_c, _a):
        entered.set()
        release.wait(20)
        return ok("x")

    monkeypatch.setitem(REGISTRY, "slow_tool", FixTool("slow_tool", "d", obj({}), slow))
    s, token = start_session(box, sleeper())
    assert public(s)["activity"] is None
    t = threading.Thread(
        target=lambda: fixsession.SESSIONS.call_tool(
            s.id, token, "slow_tool", {"packages": ["colorcet"]}
        ),
        daemon=True,
    )
    t.start()
    assert entered.wait(10)
    a = public(s)["activity"]
    assert (a["tool"], a["phase"], a["detail"]) == ("slow_tool", "start", "colorcet") and a[
        "at_ms"
    ] > 0
    release.set()
    t.join(10)
    assert public(s)["activity"]["phase"] == "end"
    fixsession.SESSIONS.cancel(s.id, "pj")


class Scripted:
    def __init__(self, steps):
        self.steps = list(steps)

    def complete(self, **_kw) -> Turn:
        if not self.steps:
            return Turn(text="done", stop="end")
        n, a = self.steps.pop(0)
        return Turn(tool_calls=[ToolCall("c", n, a)], stop="tool_use")


def start_builtin(box, model, **kwargs):
    return fixsession.SESSIONS.start(
        scope="pj",
        prep_session_id="p1",
        script="fig.py",
        project_root=box.proj,
        model=model,
        ctx_kwargs={"home": box.home, "case_insensitive": False},
        **kwargs,
    )


def test_activity_follows_tool_calls_in_builtin_mode(box, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(_c, _a):
        entered.set()
        release.wait(20)
        return ok("x")

    monkeypatch.setitem(REGISTRY, "slow_tool", FixTool("slow_tool", "d", obj({}), slow))
    s = start_builtin(
        box,
        Scripted(
            [("slow_tool", {"package": "numpy"}), ("finish", {"outcome": "fixed", "summary": "ok"})]
        ),
    )
    assert entered.wait(10)
    a = public(s)["activity"]
    assert (a["tool"], a["phase"], a["detail"]) == ("slow_tool", "start", "numpy")
    release.set()
    wait_finished(s, 10)
    assert public(s)["activity"]["tool"] == "finish" and public(s)["activity"]["phase"] == "end"


def test_activity_detail_never_carries_paths_or_other_arguments():
    assert fixsession.activity_detail({"path": "/Users/x/secret", "env": "e1"}) == ""
    assert fixsession.activity_detail({"packages": ["a", "b"], "path": "/p"}) == "a, b"


# =========================================================================== 内置循环：模型请求挂住
def test_builtin_model_that_hangs_is_flagged_then_auto_cancelled_and_frees_the_slot(box):
    gate = threading.Event()

    class Hung:
        def complete(self, **_kw):
            gate.wait(30)
            return Turn(text="late", stop="end")

    s = start_builtin(box, Hung())
    wait_until(lambda: public(s)["stalled"] is not None)
    assert public(s)["stalled"]["reason"] == "stalled"
    wait_finished(s, 15)
    assert (s.outcome, s.reason) == (fixsession.OUTCOME_FAILED, "stalled")
    assert fixsession.SESSIONS.active() is None  # 循环线程还挂着，但会话不再占位
    assert "stalled" in codes(box, s)
    gate.set()


# =========================================================================== CLI 结构化输出的解析
def test_codex_parser_reads_begin_end_and_approval_events():
    p = ai_agents.CodexAgent().fix_event_parser()
    assert p(CODEX_BEGIN) == [{"kind": "mcp_begin", "tool": "install_packages"}]
    done = json.dumps(
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "install_packages"}}
    )
    assert p(done) == [{"kind": "mcp_end", "tool": "install_packages"}]
    assert p(CODEX_APPROVAL) == [{"kind": "approval", "tool": "change_python"}]
    other = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "server": "someone_else", "tool": "x"},
        }
    )
    assert p(other)[0]["kind"] == "output"  # 别家 MCP 服务的调用不算我们的
    assert p("not json") == []


def test_claude_parser_reads_tool_use_results_and_permission_denials():
    p = ai_agents.ClaudeAgent().fix_event_parser()
    use = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "t1", "name": "mcp__tavotto_fix__try_run"}]
            },
        }
    )
    assert p(use) == [{"kind": "mcp_begin", "tool": "try_run"}]
    res = json.dumps(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        }
    )
    assert p(res) == [{"kind": "mcp_end", "tool": "try_run"}]
    bash = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash"}]},
        }
    )
    assert p(bash)[0]["kind"] == "output"  # 自带工具不是我们的回调
    denied = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t2",
                        "is_error": True,
                        "content": "Permission to use Bash has been denied",
                    }
                ]
            },
        }
    )
    assert p(denied) == [{"kind": "denied", "tool": "Bash"}]
    final = json.dumps({"type": "result", "permission_denials": [{"tool_name": "Edit"}]})
    assert p(final) == [{"kind": "denied", "tool": "Edit"}]


def test_claude_permission_denial_is_audited_and_shown_but_is_not_a_stall(box):
    parse = ai_agents.ClaudeAgent().fix_event_parser()
    denial = json.dumps({"type": "result", "permission_denials": [{"tool_name": "Edit"}]})
    s, _ = start_session(box, sleeper(denial), parse=parse)
    wait_until(lambda: public(s)["activity"] is not None)
    assert (public(s)["activity"]["tool"], public(s)["activity"]["phase"]) == ("Edit", "denied")
    assert "cli_permission_denied" in codes(box, s)
    time.sleep(0.5)
    assert public(s)["stalled"] is None
    fixsession.SESSIONS.cancel(s.id, "pj")


# =========================================================================== 启动参数
def test_cli_commands_ask_for_structured_output_and_codex_ignores_user_config_only_with_an_endpoint():
    base = {
        "argv": ["x"],
        "prompt": "p",
        "cwd": ".",
        "mcp_command": ["m", "fix-mcp"],
        "mcp_name": "tavotto_fix",
        "token_env": "TAVOTTO_FIX_TOKEN",
        "tool_timeout_s": 10,
    }
    codex = ai_agents.CodexAgent()
    plain = codex.build_fix_command(ai_agents.FixRunContext(**base)).argv
    assert (
        "--json" in plain and "--ignore-user-config" not in plain
    )  # 官方登录态：用户 config.toml 里可能有默认型号 / 认证方式
    gw = codex.build_fix_command(
        ai_agents.FixRunContext(**base, endpoint_args=["-c", 'model_provider="x"'], model="m1")
    ).argv
    assert "--ignore-user-config" in gw and gw[gw.index("-m") + 1] == "m1"
    claude = ai_agents.ClaudeAgent().build_fix_command(ai_agents.FixRunContext(**base)).argv
    assert claude[claude.index("--output-format") + 1] == "stream-json" and "--verbose" in claude
