"""TVD-BCZ0 方案 B：Windows 设置里的开关「转换时允许 AI 不受沙盒限制」（默认关）。

判据的主语：

* **`CodexAgent.build_command` 的 argv**：只有 (Windows ∧ 转换会话 ∧ 开关为真) 是 `--sandbox danger-full-access` 且不带
  `windows.sandbox` 覆盖；开关关着时与方案 A（#1628）逐字一致；其余三种组合（非 Windows / 非转换 / 关）都不变；
* **配置**：`ai.convert_unsandboxed_windows` 默认 False，只认明确的 True；
* **诊断**：「AI 会话结束」日志与 exectrace 的 `sandbox_mode` 是闭集事实，由**实际 argv** 反推；
* **API**：只在 Windows 暴露开关与写入口。

不起真实 codex，不做真实模型调用。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tavotto import app as appmod
from tavotto.engine import ai_agents, ai_bridge, config, exectrace


def _argv(monkeypatch, *, windows: bool, convert_task: bool, unsandboxed: bool) -> list[str]:
    monkeypatch.setattr(ai_agents.os, "name", "nt" if windows else "posix")
    ctx = ai_agents.RunContext(
        argv=["codex"],
        prompt="p",
        cwd="w",
        convert_task=convert_task,
        convert_unsandboxed=unsandboxed,
    )
    return ai_agents.CodexAgent().build_command(ctx).argv


def _sandbox_value(argv: list[str]) -> str:
    return argv[argv.index("--sandbox") + 1]


def test_windows_convert_with_switch_on_is_danger_full_access_without_override(monkeypatch):
    argv = _argv(monkeypatch, windows=True, convert_task=True, unsandboxed=True)
    assert _sandbox_value(argv) == "danger-full-access"
    assert not any("windows.sandbox" in a for a in argv)
    assert ai_agents.sandbox_mode_of(argv) == "unsandboxed"


def test_windows_convert_with_switch_off_is_identical_to_plan_a(monkeypatch):
    off = _argv(monkeypatch, windows=True, convert_task=True, unsandboxed=False)
    assert _sandbox_value(off) == "workspace-write"
    assert 'windows.sandbox="unelevated"' in off
    assert ai_agents.sandbox_mode_of(off) == "unelevated"
    # 与「根本没有这两个字段」（方案 A 的构造）逐字一致
    monkeypatch.setattr(ai_agents.os, "name", "nt")
    a = (
        ai_agents.CodexAgent()
        .build_command(ai_agents.RunContext(argv=["codex"], prompt="p", cwd="w"))
        .argv
    )
    assert off == a


def test_non_windows_ignores_the_switch(monkeypatch):
    argv = _argv(monkeypatch, windows=False, convert_task=True, unsandboxed=True)
    assert _sandbox_value(argv) == "workspace-write"
    assert not any("windows.sandbox" in a for a in argv)
    assert ai_agents.sandbox_mode_of(argv) == "workspace_write"


def test_non_convert_task_on_windows_ignores_the_switch(monkeypatch):
    argv = _argv(monkeypatch, windows=True, convert_task=False, unsandboxed=True)
    assert _sandbox_value(argv) == "workspace-write"
    assert 'windows.sandbox="unelevated"' in argv


def test_fix_session_never_uses_the_switch(monkeypatch):
    monkeypatch.setattr(ai_agents.os, "name", "nt")
    fix = ai_agents.CodexAgent().build_fix_command(
        ai_agents.FixRunContext(
            argv=["codex"],
            prompt="p",
            cwd="w",
            mcp_command=["m", "fix-mcp"],
            mcp_name="tavotto_fix",
            token_env="T",
            tool_timeout_s=10,
        )
    )
    assert _sandbox_value(fix.argv) == "read-only"


def test_bridge_passes_task_and_setting_explicitly_into_the_context(monkeypatch):
    """`_cmd` 把「是否转换」与设置值显式交给适配器；适配器不读全局配置。"""
    seen = {}

    def build(self, ctx):
        seen["ctx"] = ctx
        return ai_agents.SpawnSpec(["codex"], {})

    monkeypatch.setattr(ai_agents.CodexAgent, "build_command", build)
    monkeypatch.setattr(ai_agents, "resolve", lambda agent: SimpleNamespace(argv=["codex"]))
    ai_bridge._cmd("codex", "p", "w", convert_task=True, convert_unsandboxed=True)
    assert seen["ctx"].convert_task is True and seen["ctx"].convert_unsandboxed is True


def test_config_default_off_and_roundtrip():
    assert config.convert_unsandboxed_windows() is False
    config.set_convert_unsandboxed_windows(True)
    assert config.convert_unsandboxed_windows() is True
    assert config.ai_settings()[config.CONVERT_UNSANDBOXED_KEY] is True
    config.set_convert_unsandboxed_windows(False)
    assert config.convert_unsandboxed_windows() is False


def test_config_only_accepts_a_real_true():
    for bad in ("true", 1, "yes", [True]):
        config.set_ai_settings({config.CONVERT_UNSANDBOXED_KEY: bad})
        assert config.convert_unsandboxed_windows() is False
    config.set_ai_settings({config.CONVERT_UNSANDBOXED_KEY: None})  # 删键


def test_sandbox_mode_is_a_closed_set_fact_in_logs_and_trace():
    assert ai_agents.SANDBOX_MODES == {"workspace_write", "unelevated", "unsandboxed"}
    assert exectrace.ATTRIBUTES["cli_sandbox_mode"] == "enum:sandbox_mode"
    assert ai_bridge._CLI_FACT_ATTRS["sandbox_mode"] == "cli_sandbox_mode"
    for mode in ai_agents.SANDBOX_MODES:
        facts = {"sandbox_mode": mode}
        assert ai_bridge._cli_fact_attributes(facts) == {"cli_sandbox_mode": mode}
    # 日志里是明文码；集合外的值不会被当成闭集码放行（见 test_codex_run_facts 的哈希用例）
    sess = {"cli_runs": [_minimal_snapshot("unsandboxed")]}
    shown = ai_bridge._cli_log_facts(sess)[-1]
    assert str(shown) == "unsandboxed"
    assert str(ai_bridge._cli_log_facts({})[-1]) == "-"


def _minimal_snapshot(mode: str) -> dict:
    import time

    facts = ai_agents.CliRunFacts("c", "t.py", clock=time.monotonic)
    facts.sandbox_mode = mode
    return facts.snapshot(False)


def test_merge_run_facts_takes_the_last_sandbox_mode():
    runs = [_minimal_snapshot("unelevated"), _minimal_snapshot("unsandboxed")]
    assert ai_agents.merge_run_facts(runs)["sandbox_mode"] == "unsandboxed"


@pytest.fixture
def client():
    return appmod.app.test_client()


def test_api_hides_the_switch_off_windows(client, monkeypatch):
    monkeypatch.setattr(appmod, "_IS_WINDOWS", False)
    assert "convert_unsandboxed_windows" not in client.get("/api/ai/capabilities").get_json()
    r = client.put("/api/ai/convert-unsandboxed", json={"enabled": True})
    assert r.status_code == 400
    assert config.convert_unsandboxed_windows() is False


def test_api_reads_and_writes_the_switch_on_windows(client, monkeypatch):
    monkeypatch.setattr(appmod, "_IS_WINDOWS", True)
    assert client.get("/api/ai/capabilities").get_json()["convert_unsandboxed_windows"] is False
    r = client.put("/api/ai/convert-unsandboxed", json={"enabled": True})
    assert r.status_code == 200 and r.get_json() == {"convert_unsandboxed_windows": True}
    assert config.convert_unsandboxed_windows() is True
    assert client.get("/api/ai/capabilities").get_json()["convert_unsandboxed_windows"] is True
    assert client.put("/api/ai/convert-unsandboxed", json={"enabled": "yes"}).status_code == 400
    client.put("/api/ai/convert-unsandboxed", json={"enabled": False})
    assert config.convert_unsandboxed_windows() is False
