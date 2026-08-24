"""AI 桥的能力探测：模型清单必须来自本机 codex 配置，不是源码里的猜测。

背景：写死的模型名会随 OpenAI 换代失效，且失败得很难看——服务端直接回
400 `The 'gpt-5' model is not supported when using Codex with a ChatGPT
account.`，用户在面板里只能看到一个不能用的下拉项。
"""
import os

import pytest

from tavotto.engine import ai_bridge


@pytest.fixture(autouse=True)
def _fake_cli(monkeypatch):
    """假装两个 CLI 都装了，并清掉能力缓存（模块级全局，会串味）。

    候选枚举 + 启动验证是探测的新缝隙：candidates 给一个假路径、
    _probe_version 恒成功 = 「装了且能启动」。"""
    monkeypatch.setattr(ai_bridge, "_cli_candidates", lambda name: [f"/usr/bin/{name}"])
    monkeypatch.setattr(ai_bridge, "_probe_version", lambda argv: "test 0.0.0")
    monkeypatch.setattr(ai_bridge, "_CAPS_CACHE", {})
    monkeypatch.setattr(ai_bridge, "_RESOLVE_CACHE", {})
    yield
    ai_bridge._CAPS_CACHE = {}
    ai_bridge._RESOLVE_CACHE.clear()


def _codex_caps(tmp_path, monkeypatch, config_text=None):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    if config_text is not None:
        (tmp_path / "config.toml").write_text(config_text, encoding="utf-8")
    return ai_bridge.capabilities(refresh=True)["providers"]["codex"]


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
    caps = ai_bridge.capabilities(refresh=True)["providers"]["claude"]
    assert caps["models"] == ["sonnet", "opus", "haiku"]
    assert caps["efforts"] == []              # claude CLI 没有强度开关，不假装有


def test_spawn_env_appends_cli_dir_and_common_bins(tmp_path, monkeypatch):
    """GUI 进程的最小 PATH 里没有 /opt/homebrew/bin 之类目录：npm shim 的
    `#!/usr/bin/env node` 找不到 node（env: node: No such file or directory）。
    _spawn_env 必须把 CLI 自己所在目录补进 PATH，且不动已有排序。"""
    cli_dir = tmp_path / "some-bin"
    cli_dir.mkdir()
    monkeypatch.setenv("PATH", "/usr/bin")
    env = ai_bridge._spawn_env(str(cli_dir / "codex"), {"X_EXTRA": "1"})
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/usr/bin"          # 原有排序保持
    assert str(cli_dir) in parts           # CLI 所在目录补上了（env node 可解析）
    assert env["X_EXTRA"] == "1"           # 第三方接口的注入变量原样保留


def test_spawn_env_does_not_duplicate_existing_dirs(tmp_path, monkeypatch):
    cli_dir = tmp_path / "bin"
    cli_dir.mkdir()
    monkeypatch.setenv("PATH", str(cli_dir))
    env = ai_bridge._spawn_env(str(cli_dir / "codex"))
    assert env["PATH"].split(os.pathsep).count(str(cli_dir)) == 1


def test_macos_searches_chatgpt_bundled_codex(monkeypatch):
    """macOS 的 ChatGPT 桌面应用自带 codex CLI（issue #89）：探测候选要包含
    它的 Resources 目录——不在 PATH 上，只装了 ChatGPT 的用户以前会被判成
    「未安装」。只在 darwin、只对 codex 给；Linux 没有这套布局。"""
    import sys

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    dirs = ai_bridge._search_dirs("codex")
    assert "/Applications/ChatGPT.app/Contents/Resources" in dirs
    home = os.path.expanduser("~")
    assert home + "/Applications/ChatGPT.app/Contents/Resources" in dirs
    # 常规安装位置优先：单独装的 codex 通常比 ChatGPT 内置的新
    assert dirs.index("/opt/homebrew/bin") < dirs.index(
        "/Applications/ChatGPT.app/Contents/Resources")
    assert all("ChatGPT" not in d for d in ai_bridge._search_dirs("claude"))

    monkeypatch.setattr(sys, "platform", "linux")
    assert all("ChatGPT" not in d for d in ai_bridge._search_dirs("codex"))


def test_capabilities_expose_saved_cli_paths_and_nothing_else(monkeypatch):
    """设置界面要回显已存的自定义路径（不回显 = 空输入框失焦一次就把它
    删掉，issue #89）。settings 是白名单：cfg["ai"] 里还有第三方接口的
    记录，绝不能整份透出。"""
    from tavotto.engine import config

    config.set_ai_settings({"codex_path": "/somewhere/codex"})
    caps = ai_bridge.capabilities(refresh=True)
    assert caps["settings"] == {"codex_path": "/somewhere/codex",
                                "claude_path": None}


def test_refresh_really_reprobes_the_resolver(monkeypatch):
    """refresh=True 必须连 _RESOLVE_CACHE 一起作废：改完 codex_path 或点
    「重新探测」时，旧结论不清掉的话新路径根本没被 --version 验过，
    界面一直说「未检测到」。"""
    ai_bridge._RESOLVE_CACHE["codex"] = {
        "argv": None, "version": None, "broken_path": None}
    caps = ai_bridge.capabilities(refresh=True)
    # 候选与探测都被 _fake_cli 钉成可用：重新探测后必须翻案成「已安装」
    assert caps["providers"]["codex"]["installed"] is True
    assert ai_bridge._RESOLVE_CACHE["codex"]["argv"] == ["/usr/bin/codex"]
