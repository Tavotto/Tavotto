"""编码 Agent 的能力探测：模型清单必须来自本机 codex 配置，不是源码里的猜测。

背景：写死的模型名会随 OpenAI 换代失效，且失败得很难看——服务端直接回
400 `The 'gpt-5' model is not supported when using Codex with a ChatGPT
account.`，用户在面板里只能看到一个不能用的下拉项。

探测本身（候选来源、启动验证、就绪检查、注册表纪律）在
`tests/test_ai_agents.py`；这里盯的是 capabilities 这层的输出。
"""

import json
import os
import sys

import pytest

from tavotto.engine import ai_agents, ai_bridge


@pytest.fixture(autouse=True)
def _fake_cli(monkeypatch, tmp_path_factory):
    """假装两个 CLI 都装了，并清掉能力缓存（模块级全局，会串味）。

    候选枚举 + 启动验证是探测的新缝隙：candidates 给一个假路径、
    probe_version 恒成功 = 「装了且能启动」。就绪检查一律钉成「查不了」——
    它会真去 spawn 子进程，断言不该跟着跑测试这台机器上装了什么走。"""
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate(f"/usr/bin/{agent.id}", "path")],
    )
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "test 0.0.0")
    monkeypatch.setattr(ai_agents, "_run_probe", lambda argv, timeout=10: None)
    # 默认把 Claude Code 家目录指向空目录：不读这台机器真实的 ~/.claude
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-home")))
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
    caps = _codex_caps(
        tmp_path, monkeypatch, 'model = "gpt-5.6-luna"\nmodel_reasoning_effort = "max"\n'
    )
    assert caps["models"] == ["gpt-5.6-luna"]
    assert caps["default_model"] == "gpt-5.6-luna"
    assert caps["default_effort"] == "max"
    assert "max" in caps["efforts"]


def test_profiles_listed_after_the_active_model(tmp_path, monkeypatch):
    caps = _codex_caps(
        tmp_path,
        monkeypatch,
        """
model = "model-a"

[profiles.fast]
model = "model-b"

[profiles.same]
model = "model-a"
""",
    )
    assert caps["models"] == ["model-a", "model-b"]  # 去重且当前模型在首位


def test_no_guessed_model_without_config(tmp_path, monkeypatch):
    """读不到配置就交白卷——前端据此显示「跟随 CLI 默认」，
    绝不能塞一个我们猜的模型名进去。"""
    caps = _codex_caps(tmp_path, monkeypatch)
    assert caps["models"] == []
    assert caps["default_model"] is None
    assert caps["installed"] is True  # CLI 本身仍然是可用的
    assert caps["efforts"], "推理强度是 CLI 侧开关，与模型清单无关，不该一起清空"


def test_broken_config_does_not_break_the_panel(tmp_path, monkeypatch):
    caps = _codex_caps(tmp_path, monkeypatch, "model = 这不是合法 TOML\n[[[")
    assert caps["installed"] is True
    assert caps["models"] == []  # 退化解析取不到带引号的值


def _level(effort):
    return {"effort": effort, "description": f"{effort} desc"}


def _write_cache(home, models):
    (home / "models_cache.json").write_text(
        json.dumps(
            {"fetched_at": "2026-10-22T00:00:00Z", "client_version": "0.163.0", "models": models}
        ),
        encoding="utf-8",
    )


def _cache_model(slug, *, visibility="list", priority=1, levels=("low", "medium", "high"), **extra):
    return {
        "slug": slug,
        "display_name": slug.upper(),
        "description": f"about {slug}",
        "visibility": visibility,
        "priority": priority,
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [_level(e) for e in levels],
        **extra,
    }


def test_catalog_adds_listed_models_after_the_config_model(tmp_path, monkeypatch):
    """models_cache.json 里 visibility=list 的型号并入清单：config 当前模型在首位且是默认，
    hide 的不列，其余按 priority 升序；每个型号带自己的显示名与强度档位。"""
    _write_cache(
        tmp_path,
        [
            _cache_model("b-model", priority=5, levels=("low", "medium")),
            _cache_model("hidden", visibility="hide", priority=0),
            _cache_model(
                "a-model",
                priority=2,
                levels=("low", "xhigh", "ultra"),
                default_reasoning_level="low",
            ),
            _cache_model("cfg-model", priority=9),
        ],
    )
    caps = _codex_caps(
        tmp_path, monkeypatch, 'model = "cfg-model"\nmodel_reasoning_effort = "high"\n'
    )
    assert caps["models"] == ["cfg-model", "a-model", "b-model"]
    assert caps["default_model"] == "cfg-model"
    by_id = {d["id"]: d for d in caps["model_details"]}
    assert list(by_id) == caps["models"]  # 与 models 并行
    assert by_id["a-model"] == {
        "id": "a-model",
        "display_name": "A-MODEL",
        "description": "about a-model",
        "efforts": ["low", "xhigh", "ultra"],
        "efforts_known": True,
        "default_effort": "low",
    }
    assert by_id["b-model"]["efforts"] == ["low", "medium"]
    # Agent 级档位是并集（旧前端与 prunePrefs 用），不丢 xhigh / ultra
    assert {"xhigh", "ultra", "medium"} <= set(caps["efforts"])


def test_config_model_missing_from_catalog_still_listed_without_levels(tmp_path, monkeypatch):
    _write_cache(tmp_path, [_cache_model("x")])
    caps = _codex_caps(tmp_path, monkeypatch, 'model = "mine"\n')
    d = {d["id"]: d for d in caps["model_details"]}["mine"]
    assert d["display_name"] == "mine" and d["efforts"] == [] and d["default_effort"] is None
    assert d["efforts_known"] is False  # config 里有、目录里没有 = 未知


def test_catalog_works_without_config_toml(tmp_path, monkeypatch):
    _write_cache(tmp_path, [_cache_model("only")])
    caps = _codex_caps(tmp_path, monkeypatch)
    assert caps["models"] == ["only"]
    assert caps["default_model"] is None  # 默认仍是 CLI 自己的，不替它猜


@pytest.mark.parametrize(
    "content",
    [
        None,  # 缺失
        "{不是 json",
        "[]",
        '{"models": "nope"}',
        '{"models": [1, "x", null, {"slug": 3}, {"visibility": "list"}]}',
        '{"models": [{"slug": "ok", "visibility": "list", "supported_reasoning_levels": 7}]}',
    ],
)
def test_bad_cache_degrades_to_config_only(tmp_path, monkeypatch, content):
    if content is not None:
        (tmp_path / "models_cache.json").write_text(content, encoding="utf-8")
    caps = _codex_caps(tmp_path, monkeypatch, 'model = "cfg"\n')
    assert caps["installed"] is True
    ids = caps["models"]
    assert ids[0] == "cfg"
    assert set(ids) <= {"cfg", "ok"}  # 坏条目被丢，好条目（若有）仍在，不报错


def test_catalog_survives_without_tomllib(tmp_path, monkeypatch):
    """py3.10 没有 tomllib：config 走逐行退化解析，目录缓存（JSON）照常并入。"""
    monkeypatch.setitem(sys.modules, "tomllib", None)  # import tomllib -> ImportError
    _write_cache(tmp_path, [_cache_model("cat")])
    caps = _codex_caps(tmp_path, monkeypatch, 'model = "cfg"\nmodel_reasoning_effort = "low"\n')
    assert caps["models"] == ["cfg", "cat"]
    assert caps["default_model"] == "cfg"
    assert caps["default_effort"] == "low"


def _model(mid, name, section="main", family=None, efforts=("low", "high"), default="high"):
    rt = {"family": family or mid.split("-")[1]}
    if efforts is not None:
        rt["effort_levels"] = list(efforts)
        rt["default_effort"] = default
    return {"id": mid, "name": name, "description": f"{name} d", "section": section, "runtime": rt}


def _write_catalog(home, models, *, fetched=1000, name="a-cc.json", targets=None, settings=None):
    d = home / "cache" / "model-catalog"
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"id": "cc", "models": models}
    if targets is not None:
        cfg["provider_alias_targets"] = targets
    (d / name).write_text(
        json.dumps({"version": 2, "fetchedAt": fetched, "catalog": {"config": cfg}}),
        encoding="utf-8",
    )
    if settings is not None:
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _claude(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return _agent(ai_bridge.capabilities(refresh=True), "claude")


_CATALOG = [
    _model("claude-opus-5-5", "Opus 5.5", default="medium"),
    _model("claude-sonnet-5-5", "Sonnet 5.5", efforts=("low", "medium", "high"), default="medium"),
    _model("claude-opus-4-8", "Opus 4.8", section="overflow"),
]


def test_claude_without_catalog_falls_back_to_aliases(tmp_path, monkeypatch):
    caps = _claude(tmp_path, monkeypatch)
    assert caps["models"] == ["sonnet", "opus", "haiku"]
    assert caps["default_model"] == "sonnet"
    assert [d["display_name"] for d in caps["model_details"]] == ["Sonnet", "Opus", "Haiku"]
    assert caps["efforts"] == []
    assert caps["default_effort"] is None


def test_claude_lists_only_main_section_in_file_order(tmp_path, monkeypatch):
    _write_catalog(tmp_path, _CATALOG)
    caps = _claude(tmp_path, monkeypatch)
    assert caps["models"] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    d = caps["model_details"][0]
    assert (d["id"], d["display_name"], d["description"]) == (
        "claude-opus-5-5",
        "Opus 5.5",
        "Opus 5.5 d",
    )
    assert d["efforts"] == ["low", "high"] and d["default_effort"] == "medium"
    assert caps["efforts"] == ["low", "high", "medium"]  # 各型号并集，保序


def test_claude_default_is_first_without_user_model(tmp_path, monkeypatch):
    _write_catalog(tmp_path, _CATALOG)
    caps = _claude(tmp_path, monkeypatch)
    assert caps["default_model"] == "claude-opus-5-5"
    assert caps["default_effort"] == "medium"


@pytest.mark.parametrize(
    "setting,expected",
    [
        ("sonnet", "claude-sonnet-5-5"),  # 家族名
        ("sonnet[1m]", "claude-sonnet-5-5"),  # 去掉后缀
        ("claude-sonnet-5-5", "claude-sonnet-5-5"),  # 完整 id
        ("haiku", "claude-opus-5-5"),  # 目录里没有该家族：取第一项
        ("claude-opus-4-8", "claude-opus-5-5"),  # overflow 型号：不在列表里，取第一项
    ],
)
def test_claude_default_maps_user_setting(tmp_path, monkeypatch, setting, expected):
    _write_catalog(tmp_path, _CATALOG, settings={"model": setting})
    assert _claude(tmp_path, monkeypatch)["default_model"] == expected


def test_claude_alias_targets_take_priority(tmp_path, monkeypatch):
    _write_catalog(
        tmp_path,
        _CATALOG,
        targets={"best": {"default": "claude-sonnet-5-5"}},
        settings={"model": "best"},
    )
    assert _claude(tmp_path, monkeypatch)["default_model"] == "claude-sonnet-5-5"


def test_claude_newest_parseable_catalog_wins(tmp_path, monkeypatch):
    _write_catalog(tmp_path, [_model("claude-old-1", "Old")], fetched=1, name="a.json")
    _write_catalog(tmp_path, [_model("claude-new-1", "New")], fetched=9, name="b.json")
    d = tmp_path / "cache" / "model-catalog"
    (d / "c.json").write_text("{not json", encoding="utf-8")
    (d / "d.json").write_text(
        json.dumps({"fetchedAt": 99, "catalog": {"config": {"models": "oops"}}}), encoding="utf-8"
    )
    assert _claude(tmp_path, monkeypatch)["models"] == ["claude-new-1"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"catalog": {}}',
        '{"fetchedAt": 1, "catalog": {"config": {"models": []}}}',
    ],
)
def test_claude_broken_catalog_falls_back(tmp_path, monkeypatch, content):
    d = tmp_path / "cache" / "model-catalog"
    d.mkdir(parents=True)
    (d / "x.json").write_text(content, encoding="utf-8")
    assert _claude(tmp_path, monkeypatch)["models"] == ["sonnet", "opus", "haiku"]


def test_claude_catalog_without_main_models_falls_back(tmp_path, monkeypatch):
    _write_catalog(tmp_path, [_model("claude-opus-4-8", "Opus 4.8", section="overflow")])
    assert _claude(tmp_path, monkeypatch)["models"] == ["sonnet", "opus", "haiku"]


def test_claude_model_without_effort_data_exposes_none(tmp_path, monkeypatch):
    _write_catalog(tmp_path, [_model("claude-haiku-5", "Haiku 5", efforts=None)])
    caps = _claude(tmp_path, monkeypatch)
    assert caps["efforts"] == [] and caps["model_details"][0]["efforts"] == []


def test_claude_catalog_model_without_levels_is_explicitly_none(tmp_path, monkeypatch):
    _write_catalog(
        tmp_path,
        [_model("claude-opus-5-5", "Opus 5.5"), _model("claude-haiku-5", "Haiku 5", efforts=None)],
    )
    caps = _claude(tmp_path, monkeypatch)
    by_id = {d["id"]: d for d in caps["model_details"]}
    assert by_id["claude-opus-5-5"]["efforts_known"] is True
    assert (
        by_id["claude-haiku-5"]["efforts"] == []
        and by_id["claude-haiku-5"]["efforts_known"] is True
    )
    assert caps["efforts"] == ["low", "high"]  # Agent 级仍是并集
    # 回落别名（无目录）与 config.toml-only 型号 = 未知
    assert ai_agents.ModelDetail(id="x", display_name="x").efforts_known is False


def test_claude_build_command_skips_effort_for_no_effort_model(tmp_path, monkeypatch):
    _write_catalog(
        tmp_path,
        [_model("claude-opus-5-5", "Opus 5.5"), _model("claude-haiku-5", "Haiku 5", efforts=None)],
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def argv(model, effort):
        ctx = ai_agents.RunContext(
            argv=["claude"], prompt="p", cwd=str(tmp_path), model=model, effort=effort
        )
        return ai_agents.ClaudeAgent().build_command(ctx).argv

    assert "--effort" not in argv("claude-haiku-5", "high")  # 明确无档位
    assert "--effort" not in argv("claude-opus-5-5", "max")  # 不在该型号档位内
    assert argv("claude-opus-5-5", "high")[-2:] == ["--effort", "high"]
    assert "--effort" in argv("unlisted-model", "high")  # 目录里没有 = 未知，沿用


def test_claude_gateway_hides_efforts_and_skips_flag(tmp_path, monkeypatch):
    from tavotto.engine import ai_providers

    _write_catalog(tmp_path, _CATALOG)
    ai_providers.save(
        {
            "id": "gw",
            "label": "GW",
            "agent": "claude",
            "api_key": "sk-x",
            "base_url": "https://gw.example/anthropic",
            "models": ["glm-x"],
        }
    )
    ai_providers.set_active("claude", "gw")
    caps = _claude(tmp_path, monkeypatch)
    assert caps["models"] == ["glm-x"] and caps["model_details"] == []
    assert caps["efforts"] == [] and caps["default_effort"] is None
    assert caps["features"]["effort_selection"] is False
    ctx = ai_agents.RunContext(
        argv=["claude"],
        prompt="p",
        cwd=str(tmp_path),
        model="glm-x",
        effort="high",
        endpoint_env={"ANTHROPIC_BASE_URL": "https://gw.example/anthropic"},
    )
    assert "--effort" not in ai_agents.ClaudeAgent().build_command(ctx).argv


def test_claude_build_command_passes_effort(tmp_path):
    ctx = ai_agents.RunContext(
        argv=["claude"], prompt="p", cwd=str(tmp_path), model="claude-opus-5-5", effort="high"
    )
    cmd = ai_agents.ClaudeAgent().build_command(ctx).argv
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
    ctx2 = ai_agents.RunContext(argv=["claude"], prompt="p", cwd=str(tmp_path))
    assert "--effort" not in ai_agents.ClaudeAgent().build_command(ctx2).argv


def test_spawn_env_appends_cli_dir_and_common_bins(tmp_path, monkeypatch):
    """GUI 进程的最小 PATH 里没有 /opt/homebrew/bin 之类目录：npm shim 的
    `#!/usr/bin/env node` 找不到 node（env: node: No such file or directory）。
    spawn_env 必须把 CLI 自己所在目录补进 PATH，且不动已有排序。"""
    cli_dir = tmp_path / "some-bin"
    cli_dir.mkdir()
    monkeypatch.setenv("PATH", "/usr/bin")
    env = ai_agents.spawn_env(str(cli_dir / "codex"), {"X_EXTRA": "1"})
    parts = env["PATH"].split(os.pathsep)
    assert parts[0] == "/usr/bin"  # 原有排序保持
    assert str(cli_dir) in parts  # CLI 所在目录补上了（env node 可解析）
    assert env["X_EXTRA"] == "1"  # 第三方接口的注入变量原样保留


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
        "/Applications/ChatGPT.app/Contents/Resources"
    )
    # 来源如实标记（诊断区要显示它，且它绝不参与「能不能用」的判断）
    assert next(loc.source for loc in locs if "ChatGPT" in loc.path) == "chatgpt_bundle"
    assert all("ChatGPT" not in loc.path for loc in ai_agents.agent_search_locations(claude))

    monkeypatch.setattr(sys, "platform", "linux")
    assert all("ChatGPT" not in loc.path for loc in ai_agents.agent_search_locations(codex))


def test_capabilities_echo_the_saved_path_override_and_leak_nothing(monkeypatch):
    """详情页要回显已存的自定义路径（不回显 = 用户看不到自己设过什么，
    issue #89 的一半）。同时：整份 `ai` 配置里还躺着第三方接口的密钥，
    capabilities **一个字节都不能整份透出**。"""
    from tavotto.engine import ai_providers, config

    config.set_ai_agent_settings("codex", {"path_override": "/somewhere/codex"})
    ai_providers.save(
        {
            "label": "Kimi",
            "agent": "claude",
            "api_key": "sk-secret-abcdef",
            "base_url": "https://api.moonshot.cn/anthropic",
        }
    )
    caps = ai_bridge.capabilities(refresh=True)
    assert _agent(caps, "codex")["path_override"] == "/somewhere/codex"
    assert _agent(caps, "claude")["path_override"] is None
    import json

    blob = json.dumps(caps, ensure_ascii=False)
    assert "sk-secret-abcdef" not in blob  # 密钥永不整串回传
    assert "api_key" not in blob
    assert '"argv"' not in blob  # 前端没有消费者，就不公开


def test_refresh_really_reprobes_the_resolver():
    """refresh=True 必须连解析缓存一起作废：改完自定义路径或点「重新检测」
    时，旧结论不清掉的话新路径根本没被 --version 验过，界面一直说
    「未检测到」。"""
    ai_agents._RESOLVE_CACHE["codex"] = ai_agents.Resolution()
    caps = ai_bridge.capabilities(refresh=True)
    # 候选与探测都被 _fake_cli 钉成可用：重新探测后必须翻案成「已安装」
    assert _agent(caps, "codex")["installed"] is True
    assert ai_agents._RESOLVE_CACHE["codex"].argv == ["/usr/bin/codex"]


def test_a_resolve_started_before_clear_cache_does_not_publish(monkeypatch):
    """诊断超预算留在后台的那次解析（#512）卡在 --version 时用户改了路径：
    clear_cache + 重新探测拿到新路径之后，旧解析跑完不许把旧候选写回
    `_RESOLVE_CACHE`——`_cmd()` 直接读这份缓存，会启动已经换掉的那个文件。"""
    import threading

    started, release = threading.Event(), threading.Event()
    path = {"p": "/old/codex"}

    def candidates(agent, override=None):
        return [ai_agents.CliCandidate(path["p"], "path")]

    def probe_version(argv):
        if argv[-1] == "/old/codex":
            started.set()
            release.wait(10)
        return "test 0.0.0"

    monkeypatch.setattr(ai_agents, "candidates", candidates)
    monkeypatch.setattr(ai_agents, "probe_version", probe_version)
    monkeypatch.setattr(ai_agents, "resolve_shim", lambda p: None)
    codex = next(a for a in ai_agents.agents() if a.id == "codex")

    old: list = []
    t = threading.Thread(target=lambda: old.append(ai_agents.resolve(codex, probe_readiness=False)))
    t.start()
    try:
        assert started.wait(10)
        path["p"] = "/new/codex"
        ai_agents.clear_cache()
        assert ai_agents.resolve(codex, probe_readiness=False).path == "/new/codex"
    finally:
        release.set()
        t.join(10)
    assert old[0].path == "/old/codex"
    assert ai_agents._RESOLVE_CACHE["codex"].path == "/new/codex"


def test_codex_build_command_filters_unsupported_effort(tmp_path, monkeypatch):
    """Codex 与 Claude 共用同一兜底：目录明确的型号不支持的档位不传；未知型号照传。"""
    _write_cache(
        tmp_path,
        [
            _cache_model("plain", levels=()),
            _cache_model("limited", levels=("low", "medium")),
            _cache_model("rich", levels=("low", "xhigh")),
        ],
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    def argv(model, effort):
        ctx = ai_agents.RunContext(
            argv=["codex"], prompt="p", cwd=str(tmp_path), model=model, effort=effort
        )
        return ai_agents.CodexAgent().build_command(ctx).argv

    assert not any("model_reasoning_effort" in a for a in argv("plain", "high"))  # 明确无档位
    assert not any("model_reasoning_effort" in a for a in argv("limited", "xhigh"))  # 兄弟独有
    assert "model_reasoning_effort=low" in argv("limited", "low")  # 合法
    assert "model_reasoning_effort=high" in argv("unlisted", "high")  # 未知照传
