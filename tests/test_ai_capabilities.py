"""编码 Agent 的能力探测：模型清单必须来自本机 codex 配置，不是源码里的猜测。

背景：写死的模型名会随 OpenAI 换代失效，且失败得很难看——服务端直接回
400 `The 'gpt-5' model is not supported when using Codex with a ChatGPT
account.`，用户在面板里只能看到一个不能用的下拉项。

探测本身（候选来源、启动验证、就绪检查、注册表纪律）在
`tests/test_ai_agents.py`；这里盯的是 capabilities 这层的输出。
"""
import os

import pytest

from tavotto.engine import ai_agents, ai_bridge


@pytest.fixture(autouse=True)
def _fake_cli(monkeypatch):
    """假装两个 CLI 都装了，并清掉能力缓存（模块级全局，会串味）。

    候选枚举 + 启动验证是探测的新缝隙：candidates 给一个假路径、
    probe_version 恒成功 = 「装了且能启动」。就绪检查一律钉成「查不了」——
    它会真去 spawn 子进程，断言不该跟着跑测试这台机器上装了什么走。"""
    monkeypatch.setattr(
        ai_agents, "candidates",
        lambda agent, override=None: [
            ai_agents.CliCandidate(f"/usr/bin/{agent.id}", "path")])
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "test 0.0.0")
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    ai_bridge.invalidate_capabilities()
    yield
    ai_bridge.invalidate_capabilities()


def _agent(caps, agent_id):
    return next(a for a in caps["agents"] if a["id"] == agent_id)


def _codex_caps(tmp_path, monkeypatch, config_text=None):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    if config_text is not None:
        (tmp_path / "config.toml").write_text(config_text, encoding="utf-8")
    return _agent(ai_bridge.capabilities(refresh=True), "codex")


def test_models_come_from_codex_config(tmp_path, monkeypatch):
    caps = _codex_caps(tmp_path, monkeypatch,
                       'model = "gpt-5.6-luna"\nmodel_reasoning_effort = "max"\n')
    assert caps["models"] == ["gpt-5.6-luna"]
    assert caps["default_model"] == "gpt-5.6-luna"
    assert caps["default_effort"] == "max"
    assert "max" in caps["efforts"]


def test_profiles_listed_after_the_active_model(tmp_path, monkeypatch):
    caps = _codex_caps(tmp_path, monkeypatch, """
model = "model-a"

[profiles.fast]
model = "model-b"

[profiles.same]
model = "model-a"
""")
    assert caps["models"] == ["model-a", "model-b"]   # 去重且当前模型在首位


def test_no_guessed_model_without_config(tmp_path, monkeypatch):
    """读不到配置就交白卷——前端据此显示「跟随 CLI 默认」，
    绝不能塞一个我们猜的模型名进去。"""
    caps = _codex_caps(tmp_path, monkeypatch)
    assert caps["models"] == []
    assert caps["default_model"] is None
    assert caps["installed"] is True          # CLI 本身仍然是可用的
    assert caps["efforts"], "推理强度是 CLI 侧开关，与模型清单无关，不该一起清空"


def test_broken_config_does_not_break_the_panel(tmp_path, monkeypatch):
    caps = _codex_caps(tmp_path, monkeypatch, "model = 这不是合法 TOML\n[[[")
    assert caps["installed"] is True
    assert caps["models"] == []               # 退化解析取不到带引号的值


def test_claude_side_reports_no_effort_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    caps = _agent(ai_bridge.capabilities(refresh=True), "claude")
    assert caps["models"] == ["sonnet", "opus", "haiku"]
    assert caps["efforts"] == []              # claude CLI 没有强度开关，不假装有
    assert caps["default_effort"] is None


def test_spawn_env_appends_cli_dir_and_common_bins(tmp_path, monkeypatch):
    """GUI 进程的最小 PATH 里没有 /opt/homebrew/bin 之类目录：npm shim 的
    `#!/usr/bin/env node` 找不到 node（env: node: No such file or directory）。
    spawn_env 必须把 CLI 自己所在目录补进 PATH，且不动已有排序。"""
    cli_dir = tmp_path / "some-bin"
    cli_dir.mkdir()
    monkeypatch.setenv("PATH", "/usr/bin")
    env = ai_agents.spawn_env(str(cli_dir / "codex"), {"X_EXTRA": "1"})
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/usr/bin"          # 原有排序保持
    assert str(cli_dir) in parts           # CLI 所在目录补上了（env node 可解析）
    assert env["X_EXTRA"] == "1"           # 第三方接口的注入变量原样保留


def test_spawn_env_does_not_duplicate_existing_dirs(tmp_path, monkeypatch):
    cli_dir = tmp_path / "bin"
    cli_dir.mkdir()
    monkeypatch.setenv("PATH", str(cli_dir))
    env = ai_agents.spawn_env(str(cli_dir / "codex"))
    assert env["PATH"].split(os.pathsep).count(str(cli_dir)) == 1


def test_macos_searches_chatgpt_bundled_codex(monkeypatch):
    """macOS 的 ChatGPT 桌面应用自带 codex CLI（issue #89）：探测候选要包含
    它的 Resources 目录——不在 PATH 上，只装了 ChatGPT 的用户以前会被判成
    「未安装」。只在 darwin、只对 codex 给；Linux 没有这套布局。

    ChatGPT 落点属于 **codex 适配器自己的** extra_search_locations，所以断言
    打在「该 Agent 的完整候选目录表」上，而不是通用的 search_dirs。"""
    import sys

    codex = ai_agents.get_agent("codex")
    claude = ai_agents.get_agent("claude")
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    locs = ai_agents.agent_search_locations(codex)
    dirs = [loc.path for loc in locs]
    assert "/Applications/ChatGPT.app/Contents/Resources" in dirs
    home = os.path.expanduser("~")
    assert home + "/Applications/ChatGPT.app/Contents/Resources" in dirs
    # 常规安装位置优先：单独装的 codex 通常比 ChatGPT 内置的新
    assert dirs.index("/opt/homebrew/bin") < dirs.index(
        "/Applications/ChatGPT.app/Contents/Resources")
    # 来源如实标记（诊断区要显示它，且它绝不参与「能不能用」的判断）
    assert next(loc.source for loc in locs
                if "ChatGPT" in loc.path) == "chatgpt_bundle"
    assert all("ChatGPT" not in loc.path
               for loc in ai_agents.agent_search_locations(claude))

    monkeypatch.setattr(sys, "platform", "linux")
    assert all("ChatGPT" not in loc.path
               for loc in ai_agents.agent_search_locations(codex))


def test_capabilities_echo_the_saved_path_override_and_leak_nothing(monkeypatch):
    """详情页要回显已存的自定义路径（不回显 = 用户看不到自己设过什么，
    issue #89 的一半）。同时：整份 `ai` 配置里还躺着第三方接口的密钥，
    capabilities **一个字节都不能整份透出**。"""
    from tavotto.engine import ai_providers, config

    config.set_ai_agent_settings("codex", {"path_override": "/somewhere/codex"})
    ai_providers.save({"label": "Kimi", "agent": "claude",
                       "api_key": "sk-secret-abcdef",
                       "base_url": "https://api.moonshot.cn/anthropic"})
    caps = ai_bridge.capabilities(refresh=True)
    assert _agent(caps, "codex")["path_override"] == "/somewhere/codex"
    assert _agent(caps, "claude")["path_override"] is None
    import json
    blob = json.dumps(caps, ensure_ascii=False)
    assert "sk-secret-abcdef" not in blob        # 密钥永不整串回传
    assert "api_key" not in blob
    assert '"argv"' not in blob                  # 前端没有消费者，就不公开


def test_refresh_really_reprobes_the_resolver():
    """refresh=True 必须连解析缓存一起作废：改完自定义路径或点「重新检测」
    时，旧结论不清掉的话新路径根本没被 --version 验过，界面一直说
    「未检测到」。"""
    ai_agents._RESOLVE_CACHE["codex"] = ai_agents.Resolution()
    caps = ai_bridge.capabilities(refresh=True)
    # 候选与探测都被 _fake_cli 钉成可用：重新探测后必须翻案成「已安装」
    assert _agent(caps, "codex")["installed"] is True
    assert ai_agents._RESOLVE_CACHE["codex"].argv == ["/usr/bin/codex"]
