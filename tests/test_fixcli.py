"""AI 修复 D3 的命令行侧（ADR 0149 修订 D3）：Codex / Claude Code 起法、权限表、令牌与主目录的卫生、可用性。

判据的主语：**最终交给 `subprocess.Popen` 的 argv 与 env**（`fixsession.cli_launcher` 的产物，不是适配器内部的中间件）、
**Claude `--settings` JSON 里的 deny 规则**（与 `readscope` 硬拦表的关系）、**假主目录里有没有多出 `.codex` / `.claude`**。
全程假 CLI、假主目录、假配置目录；不起真实 codex / claude，不做真实模型调用。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import ai_agents, ai_bridge, ai_providers, fixsession
from tavotto.engine.fixtools import FixContext, readscope

# 假密钥 / 假令牌运行时拼接（GitHub 推送保护按形状拦字面量）
TOKEN = "fixtok-" + "CANARY" + "abcdef0123456789"
KEY = "sk-" + "test-" + "CANARYkey1234567890"
FIX_ID = "fx_test"


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    return type("Box", (), {"home": home, "proj": proj, "tmp": tmp_path})


def _tomllib():
    """TOML 解析器：3.11+ 用标准库 `tomllib`；3.10 用 `tomli`（同一份实现），都没有就跳过。"""
    try:
        import tomllib
    except ModuleNotFoundError:
        return pytest.importorskip("tomli")
    return tomllib


def fake_resolution(monkeypatch, agent_id: str):
    cli = f"/fake/bin/{agent_id}"
    res = ai_agents.Resolution(
        argv=[cli],
        path=cli,
        version="9.9.9",
        source="test",
        readiness=ai_agents.ReadinessResult("ready", "test"),
    )
    monkeypatch.setattr(ai_agents, "resolve", lambda agent, probe_readiness=True: res)


def launch(box, monkeypatch, agent_id: str, prompt: str = "PROMPT") -> fixsession.CliLaunch:
    fake_resolution(monkeypatch, agent_id)
    monkeypatch.setattr(ai_bridge, "_spawn_env", lambda cli, extra=None: dict(extra or {}))
    build = fixsession.cli_launcher(
        agent_id, callback_url="http://127.0.0.1:5089", project_root=box.proj
    )
    return build(FIX_ID, TOKEN, prompt)


def claude_settings(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--settings") + 1])


def claude_mcp(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--mcp-config") + 1])


# =========================================================================== Claude Code
def test_claude_deny_rules_are_generated_from_the_readscope_tables(box, monkeypatch):
    rules = claude_settings(launch(box, monkeypatch, "claude").argv)["permissions"]["deny"]
    for rel in readscope.DENY_HOME_RELATIVE:
        assert f"Read(~/{rel})" in rules and f"Read(~/{rel}/**)" in rules
    for pat in readscope.DENY_NAME_PATTERNS:
        assert f"Read(**/{pat})" in rules and f"Read(//**/{pat})" in rules
    # 同源：改表的一侧，生成物跟着变（没有第二份手抄的清单）
    monkeypatch.setattr(
        readscope, "DENY_HOME_RELATIVE", (*readscope.DENY_HOME_RELATIVE, ".canarydir")
    )
    monkeypatch.setattr(
        readscope, "DENY_NAME_PATTERNS", (*readscope.DENY_NAME_PATTERNS, "canary_*.dat")
    )
    again = claude_settings(launch(box, monkeypatch, "claude").argv)["permissions"]["deny"]
    assert "Read(~/.canarydir)" in again and "Read(//**/canary_*.dat)" in again
    # 这些是用户点名的凭据位置，表里必须真有（防止表被清空后上面的循环空转）
    for must in (
        "Read(~/.ssh/**)",
        "Read(~/.aws/**)",
        "Read(~/.codex/auth.json)",
        "Read(//**/*.pem)",
    ):
        assert must in rules


def test_claude_denies_every_write_and_network_tool_and_allows_only_read_only_commands(
    box, monkeypatch
):
    perms = claude_settings(launch(box, monkeypatch, "claude").argv)["permissions"]
    for tool in ("Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
        assert tool in perms["deny"]
        assert tool not in perms["allow"]
    assert "Bash" not in perms["allow"] and not any(a.startswith("Bash(*") for a in perms["allow"])
    # 重定向 / tee / sed -i 之类写法被明确拒
    assert {"Bash(*>*)", "Bash(*tee *)", "Bash(*sed -i*)"} <= set(perms["deny"])
    # 能绕过 Read deny 去读凭据的命令不在允许表里；MCP 工具与读文件工具在
    allowed_cmds = {a for a in perms["allow"] if a.startswith("Bash(")}
    assert not any(
        c in a for a in allowed_cmds for c in ("cat", "head", "tail", "grep", "rm", "mv", "cp")
    )
    assert "Bash(ls *)" in allowed_cmds and "mcp__tavotto_fix__*" in perms["allow"]
    assert {"Read", "Grep", "Glob"} <= set(perms["allow"])


def test_claude_runs_without_prompting_and_ignores_every_other_mcp_config(box, monkeypatch):
    argv = launch(box, monkeypatch, "claude").argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--strict-mcp-config" in argv
    assert "--allowedTools=mcp__tavotto_fix__*" in argv
    assert "acceptEdits" not in argv and "bypassPermissions" not in argv


def test_claude_mcp_config_names_the_launcher_and_forwards_the_token_by_name_only(box, monkeypatch):
    spec = launch(box, monkeypatch, "claude")
    server = claude_mcp(spec.argv)["mcpServers"]["tavotto_fix"]
    assert server["command"] and "fix-mcp" in server["args"]
    assert server["args"][-4:] == ["--url", "http://127.0.0.1:5089", "--fix-id", FIX_ID]
    assert server["env"] == {"TAVOTTO_FIX_TOKEN": "${TAVOTTO_FIX_TOKEN}"}
    # 值只在子进程环境里
    assert spec.env["TAVOTTO_FIX_TOKEN"] == TOKEN
    assert spec.env["MCP_TOOL_TIMEOUT"] == str((int(fixsession.ASK_TIMEOUT) + 120) * 1000)


# =========================================================================== Codex
def test_codex_runs_read_only_with_the_mcp_server_given_only_on_this_command_line(box, monkeypatch):
    spec = launch(box, monkeypatch, "codex")
    argv = spec.argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "workspace-write" not in argv and "danger-full-access" not in argv
    overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert any(o.startswith("mcp_servers.tavotto_fix.command=") for o in overrides)
    args_line = next(o for o in overrides if o.startswith("mcp_servers.tavotto_fix.args="))
    assert "fix-mcp" in args_line and FIX_ID in args_line
    assert 'mcp_servers.tavotto_fix.env_vars=["TAVOTTO_FIX_TOKEN"]' in overrides
    assert (
        f"mcp_servers.tavotto_fix.tool_timeout_sec={int(fixsession.ASK_TIMEOUT) + 120}" in overrides
    )
    # 10-24 真机：不放行的话 exec 模式里 Codex 自己的 MCP 审批没人点，工具调用一直挂着、根本不发给服务
    assert 'mcp_servers.tavotto_fix.default_tools_approval_mode="approve"' in overrides
    assert argv[-1] == "PROMPT"


# =========================================================================== 令牌 / 密钥 / 主目录
@pytest.mark.parametrize("agent_id", ["codex", "claude"])
def test_neither_the_token_nor_an_api_key_is_ever_in_argv(box, monkeypatch, agent_id):
    ai_providers.save(
        {
            "id": "x",
            "label": "x",
            "agent": agent_id,
            "base_url": "https://api.example.com",
            "api_key": KEY,
            "default_model": "m1",
        }
    )
    ai_providers.set_active(agent_id, "x")
    spec = launch(box, monkeypatch, agent_id)
    blob = "\0".join(spec.argv)
    assert TOKEN not in blob and KEY not in blob
    assert spec.env["TAVOTTO_FIX_TOKEN"] == TOKEN
    if (
        agent_id == "claude"
    ):  # 第三方接口的密钥照老规矩走环境变量，且确实到了环境里（证明上面的断言不空）
        assert KEY in spec.env.values()


@pytest.mark.parametrize("agent_id", ["codex", "claude"])
def test_building_the_command_writes_nothing_under_codex_or_claude_config_dirs(
    box, monkeypatch, agent_id
):
    launch(box, monkeypatch, agent_id)
    assert not (box.home / ".codex").exists() and not (box.home / ".claude").exists()


def test_an_adapter_that_does_not_support_fix_sessions_is_refused(box, monkeypatch):
    fake_resolution(monkeypatch, "codex")
    monkeypatch.setattr(
        ai_agents.CodexAgent, "build_fix_command", ai_agents.AgentDefinition.build_fix_command
    )
    with pytest.raises(ai_bridge.AgentError):
        ai_bridge.fix_cli_command("codex", _run_ctx())


def _run_ctx() -> ai_agents.FixRunContext:
    return ai_agents.FixRunContext(
        argv=[],
        prompt="p",
        cwd=".",
        mcp_command=["x", "fix-mcp"],
        mcp_name="tavotto_fix",
        token_env="TAVOTTO_FIX_TOKEN",
        tool_timeout_s=10,
        deny=readscope.deny_spec(),
    )


# =========================================================================== 可用性
def caps(monkeypatch, **usable: bool):
    def agent_caps(agent_id):
        return {"id": agent_id, "usable": usable.get(agent_id, False)}

    monkeypatch.setattr(ai_bridge, "agent_caps", agent_caps)


def test_ai_fix_is_available_with_only_a_cli_and_not_with_nothing(box, monkeypatch):
    caps(monkeypatch)
    assert fixsession.connection() is None and fixsession.available() is False
    caps(monkeypatch, codex=True)
    assert fixsession.cli_agent() == "codex" and fixsession.available() is True
    caps(monkeypatch, claude=True)
    assert fixsession.cli_agent() == "claude" and fixsession.available() is True


def test_ai_fix_availability_ignores_a_cli_the_user_cannot_use(box, monkeypatch):
    caps(monkeypatch, codex=False, claude=False)
    assert fixsession.available() is False


def test_prompt_is_the_same_text_as_the_builtin_loop_plus_a_cli_note(box):
    ctx = FixContext.for_fix_session("s", box.proj, home=box.home)
    text = fixsession.cli_prompt(ctx, "fig.py")
    assert text.startswith(fixsession.SYSTEM_PROMPT) and "finish" in text and "tavotto_fix" in text
    assert "fig.py" in text and len(text) <= fixsession.CLI_PROMPT_MAX + 100


# =========================================================================== TOML 字符串编码（Codex #1383 P2）
@pytest.mark.parametrize(
    "text",
    [
        "/tmp/plain",
        "/tmp/🧪 实验/数据",
        "中文 路径/图.py",
        "C:\\Users\\me\\proj\\fig.py",
        '\\\\server\\share "quoted"\\x',
        "tab\there\nnewline\rcr\x00nul\x1b\x7f\x08\x0c",
        'a"b\\"c',
    ],
)
def test_toml_strings_round_trip_through_tomllib(text):
    tomllib = _tomllib()

    assert tomllib.loads(f"v = {ai_agents.toml_value(text)}")["v"] == text
    assert tomllib.loads(f"v = {ai_agents.toml_value([text, text])}")["v"] == [text, text]


def test_codex_overrides_for_an_emoji_windows_path_parse_back(box, monkeypatch):
    from dataclasses import replace

    tomllib = _tomllib()

    weird = "C:\\Users\\张三\\🧪 proj\\tavotto-cli.exe"
    fake_resolution(monkeypatch, "codex")
    run = replace(_run_ctx(), mcp_command=[weird, "fix-mcp", "--fix-id", 'x"y'])
    argv, _env = ai_bridge.fix_cli_command("codex", run)
    overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    parsed = {}
    for o in overrides:
        k, v = o.split("=", 1)
        parsed[k] = tomllib.loads(f"v = {v}")["v"]
    assert parsed["mcp_servers.tavotto_fix.command"] == weird
    assert parsed["mcp_servers.tavotto_fix.args"][-1] == 'x"y'
