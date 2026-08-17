"""AI 桥的能力探测：模型清单必须来自本机 codex 配置，不是源码里的猜测。

背景：写死的模型名会随 OpenAI 换代失效，且失败得很难看——服务端直接回
400 `The 'gpt-5' model is not supported when using Codex with a ChatGPT
account.`，用户在面板里只能看到一个不能用的下拉项。
"""
import pytest

from magplot.engine import ai_bridge


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
