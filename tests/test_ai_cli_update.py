"""CLI 比模型清单旧（2026-11-04 用户反馈）：Codex 桌面版先升级、刷新了共用的 `models_cache.json`，Tavotto 启动的
那份旧 codex 选到新型号就 404。

看护三件事：
1. 判据：`models_cache.json` 的 `client_version` 比实际启动的 CLI 新 → capabilities 带 `cli_update`，否则不带；
2. 怎么更新（`action`）：本机有够新的另一份 → switch；托管锁版本够新 → install；都没有 → manual；用户自定义的路径不替他换；
3. 失败归类：Codex `unexpected status 404` 在官方登录下是 `agent_model_unavailable`，第三方接口下不归这一类。
"""

from __future__ import annotations

import json

import pytest

from tavotto.engine import ai_agents, ai_bridge, managedcli

CODEX = ai_agents.get_agent("codex")


def _cache(tmp_path, monkeypatch, client_version: str | None) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    body: dict = {"models": []}
    if client_version is not None:
        body["client_version"] = client_version
    (tmp_path / "models_cache.json").write_text(json.dumps(body), encoding="utf-8")


def _res(version: str, source: str = "path", path: str = "/opt/old/codex") -> ai_agents.Resolution:
    return ai_agents.Resolution(argv=[path], path=path, version=version, source=source)  # type: ignore[arg-type]


@pytest.fixture
def no_other_candidates(monkeypatch):
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: [])


@pytest.fixture
def lock(monkeypatch):
    """托管锁里的 codex 版本；None = 这个平台不提供托管安装。"""

    def set_lock(version: str | None) -> None:
        fake = None if version is None else type("Cli", (), {"version": version})()
        monkeypatch.setattr(managedcli, "cli_for", lambda name, *a, **k: fake)

    set_lock(None)
    return set_lock


def test_version_key_and_older_than():
    assert ai_agents.version_key("codex-cli 0.166.0") == (0, 166, 0)
    assert ai_agents.version_key("没有版本") is None
    assert ai_agents.older_than("codex-cli 0.165.0", "0.166.0")
    assert not ai_agents.older_than("codex-cli 0.166.0", "0.166.0")
    assert not ai_agents.older_than("codex-cli 0.167.1", "0.166.0")
    # 判不出就不提示
    assert not ai_agents.older_than(None, "0.166.0")
    assert not ai_agents.older_than("codex-cli 0.165.0", None)
    assert not ai_agents.older_than("codex-cli 0.165.0", "garbage")


def test_catalog_client_version_reads_codex_cache(tmp_path, monkeypatch):
    _cache(tmp_path, monkeypatch, "0.166.0")
    assert CODEX.catalog_client_version() == "0.166.0"
    _cache(tmp_path, monkeypatch, None)
    assert CODEX.catalog_client_version() is None
    (tmp_path / "models_cache.json").write_text("{坏的", encoding="utf-8")
    assert CODEX.catalog_client_version() is None
    (tmp_path / "models_cache.json").unlink()
    assert CODEX.catalog_client_version() is None


def test_no_plan_when_cli_is_not_older(tmp_path, monkeypatch, no_other_candidates, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    assert ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.166.0")) is None
    assert ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.170.0")) is None
    _cache(tmp_path, monkeypatch, None)
    assert ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.100.0")) is None


def test_switch_when_a_newer_copy_is_on_this_machine(tmp_path, monkeypatch, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.170.0")  # switch 优先于 install：本机已有的那份不用下载
    cands = [
        ai_agents.CliCandidate("/opt/old/codex", "path"),
        ai_agents.CliCandidate("/opt/older/codex", "npm_global"),
        ai_agents.CliCandidate(
            "/Applications/ChatGPT.app/Contents/Resources/codex", "chatgpt_bundle"
        ),
    ]
    versions = {
        "/opt/older/codex": "codex-cli 0.150.0",
        "/Applications/ChatGPT.app/Contents/Resources/codex": "codex-cli 0.166.0",
    }
    probed: list[str] = []
    monkeypatch.setattr(ai_agents, "candidates", lambda agent, override=None: cands)
    monkeypatch.setattr(ai_agents, "resolve_shim", lambda p: None)

    def probe(argv):
        probed.append(argv[-1])
        return versions.get(argv[-1])

    monkeypatch.setattr(ai_agents, "probe_version", probe)
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.165.0"))
    assert plan == {"current": "0.165.0", "seen": "0.166.0", "source": "path", "action": "switch"}
    # 当前那份不再探测
    assert "/opt/old/codex" not in probed


def test_install_when_managed_lock_is_newer(tmp_path, monkeypatch, no_other_candidates, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.166.0")
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.150.0", source="homebrew"))
    assert plan and plan["action"] == "install"


def test_no_install_when_managed_lock_is_still_behind_the_catalog(
    tmp_path, monkeypatch, no_other_candidates, lock
):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.165.0")  # 比当前新，但追不上清单：装了新型号照样 404
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.150.0", source="homebrew"))
    assert plan and plan["action"] == "manual"


def test_manual_when_nothing_newer_is_reachable(tmp_path, monkeypatch, no_other_candidates, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.165.0")  # 托管锁和当前一样旧：装了也没用
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.165.0", source="managed"))
    assert plan and plan["action"] == "manual" and plan["source"] == "managed"


def test_custom_path_is_never_switched(tmp_path, monkeypatch, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate("/opt/new/codex", "path")],
    )
    monkeypatch.setattr(ai_agents, "resolve_shim", lambda p: None)
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "codex-cli 0.200.0")
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.165.0", source="custom"))
    assert plan and plan["action"] == "manual"


def test_custom_path_never_gets_install_even_if_lock_is_new_enough(
    tmp_path, monkeypatch, no_other_candidates, lock
):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.166.0")  # 托管版够新，但 custom 排在托管前面：装了也顶不掉
    plan = ai_bridge.cli_update_plan(CODEX, _res("codex-cli 0.150.0", source="custom"))
    assert plan and plan["action"] == "manual" and plan["source"] == "custom"


def test_start_cli_update_switch_goes_through_path_override(tmp_path, monkeypatch, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    monkeypatch.setattr(
        ai_agents, "resolve", lambda agent, probe_readiness=True: _res("codex-cli 0.165.0")
    )
    monkeypatch.setattr(
        ai_agents,
        "candidates",
        lambda agent, override=None: [ai_agents.CliCandidate("/opt/new/codex", "path")],
    )
    monkeypatch.setattr(ai_agents, "resolve_shim", lambda p: None)
    monkeypatch.setattr(ai_agents, "probe_version", lambda argv: "codex-cli 0.166.0")
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        ai_bridge, "set_agent_path_override", lambda a, p: calls.append((a, p)) or {}
    )
    assert ai_bridge.start_cli_update("codex") == {"status": "done", "via": "switch"}
    # 与设置页同一个入口（同一套 --version 验证），路径由后端现算，请求体里没有
    assert calls == [("codex", "/opt/new/codex")]


def test_start_cli_update_install_reuses_install(tmp_path, monkeypatch, no_other_candidates, lock):
    _cache(tmp_path, monkeypatch, "0.166.0")
    lock("0.166.0")
    monkeypatch.setattr(
        ai_agents, "resolve", lambda agent, probe_readiness=True: _res("codex-cli 0.150.0")
    )
    monkeypatch.setattr(
        ai_bridge, "start_install", lambda a: {"status": "running", "via": "managed"}
    )
    assert ai_bridge.start_cli_update("codex")["status"] == "running"


def test_start_cli_update_refuses_when_nothing_to_do(
    tmp_path, monkeypatch, no_other_candidates, lock
):
    _cache(tmp_path, monkeypatch, "0.166.0")
    monkeypatch.setattr(
        ai_agents, "resolve", lambda agent, probe_readiness=True: _res("codex-cli 0.166.0")
    )
    with pytest.raises(ai_bridge.AgentError) as e:
        ai_bridge.start_cli_update("codex")
    assert e.value.code == "ai_cli_update_unavailable"
    monkeypatch.setattr(
        ai_agents, "resolve", lambda agent, probe_readiness=True: _res("codex-cli 0.165.0")
    )
    with pytest.raises(ai_bridge.AgentError) as e:
        ai_bridge.start_cli_update("codex")  # manual：Tavotto 做不了
    assert e.value.code == "ai_cli_update_unavailable"


_404 = json.dumps(
    {"type": "turn.failed", "error": {"message": "unexpected status 404 Not Found: x"}}
)


def test_codex_404_is_model_unavailable_on_official_login():
    st: dict = {"endpoint_backed": False}
    CODEX.classify_event(_404, st)
    assert st["runner_error"] == "agent_model_unavailable"
    assert st["runner_error"] in ai_agents.CLI_RUNNER_ERRORS


def test_codex_404_on_third_party_endpoint_is_not_classified():
    st: dict = {"endpoint_backed": True}
    CODEX.classify_event(_404, st)
    assert "runner_error" not in st  # 保持 agent_exit_nonzero → 「检查连接」


def test_claude_404_is_not_reclassified():
    claude = ai_agents.get_agent("claude")
    st: dict = {}
    claude.classify_event(
        json.dumps({"type": "result", "is_error": True, "api_error_status": 404}), st
    )
    assert "runner_error" not in st
