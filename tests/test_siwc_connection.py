"""Sign in with ChatGPT 的连接、路由与起跑注入（ADR 0209 §四 / §五）。全 Mock，不联网。

判据的主语：
* 连接视图（`vendors()` / `connections()` 的返回值）：开关关时与原来一致；开时 OpenAI 首选「Continue with ChatGPT」，Codex 登录只给已装的；
* 路由：`siwc:chatgpt` 只解析到内置执行器；
* 起跑注入（父进程交给 runner 的 argv / env）：runner 自己的硬约束 `siwc_wire.runner_args_ok` 接受它；
* 用户不能把一条 Key 记录存成 / 探测成 SIWC（「挑第一个能用的连接」现在会挑到它，见 `test_siwc_parity.py`）。
"""

from __future__ import annotations

import pytest

from tavotto.engine import (
    ai_agents,
    ai_bridge,
    ai_connections,
    ai_providers,
    siwc_auth,
    siwc_login,
    siwc_store,
)
from tavotto.engine.agent_runner import siwc_wire

CID = "oaiapp_conn1"
TOKEN = "acc-CONN-SECRET"


def _agents(codex_installed: bool) -> dict:
    return {
        "codex": {
            "installed": codex_installed,
            "enabled": codex_installed,
            "usable": codex_installed,
        },
        "claude": {"installed": False, "enabled": False, "usable": False},
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(siwc_wire.ENV_FLAG, "0")  # 基线：显式关（默认是开）；要开的用例自己设 1
    monkeypatch.delenv(ai_connections.ENV_BUILTIN, raising=False)
    monkeypatch.setattr(ai_providers, "list_providers", lambda: [])
    yield monkeypatch
    siwc_auth.set_provider_for_tests(None)


def _sign_in(
    scopes=(siwc_auth.REQUIRED_SCOPE,),
    models=(
        {"id": "gpt-a", "efforts": ["low", "medium", "high"], "default_effort": "low"},
        {"id": "gpt-b"},
    ),
):
    acc = siwc_store.Account(
        client_id=CID, subject="sub1", email="a@example.com", models=tuple(models)
    ).with_tokens(
        siwc_auth.TokenSet(
            access=TOKEN,
            refresh="ref",
            expires_at_ms=siwc_auth._now_ms() + 3_600_000,
            scopes=frozenset(scopes),
        )
    )
    siwc_store.save(acc)
    siwc_store.set_active(CID)
    siwc_auth.set_provider_for_tests(siwc_auth.MemoryProvider(acc.tokens()))


def _openai(vendors):
    return next(v for v in vendors if v["id"] == "openai")


# --------------------------------------------------------------------------- 开关关
def test_flag_off_views_unchanged(env):
    """`TAVOTTO_EXPERIMENTAL_SIWC=0`：与没有 SIWC 时完全一样。"""
    _sign_in()
    for installed in (False, True):
        v = _openai(ai_connections.vendors(_agents(installed)))
        assert [m["id"] for m in v["methods"]][0] == "openai:login"
        assert all(m["kind"] != "siwc" for m in v["methods"])
        assert all(
            c["id"] != "siwc:chatgpt" for c in ai_connections.connections(_agents(installed))
        )
    with pytest.raises(ai_connections.ConnectionRefused) as ei:
        ai_connections.resolve_connection("siwc:chatgpt", _agents(True))
    assert ei.value.status == 404
    assert ai_providers.resolve(ai_agents.BUILTIN_ID, "siwc:chatgpt") is None


# --------------------------------------------------------------------------- 开关开
def test_continue_with_chatgpt_replaces_codex_login_for_everyone(env):
    """用户 2026-11-25：老用户也直接切换——装没装 Codex，OpenAI 一家都只剩「Continue with ChatGPT」（+ API Key）。"""
    env.setenv(siwc_wire.ENV_FLAG, "1")
    for installed in (False, True):
        v = _openai(ai_connections.vendors(_agents(installed)))
        assert [m["id"] for m in v["methods"] if m["kind"] != "key"] == ["openai:siwc"]
        assert v["connect"] == "siwc"
        ids = [c["id"] for c in ai_connections.connections(_agents(installed))]
        assert "sub:codex" not in ids and "siwc:chatgpt" not in ids  # 没登录 ChatGPT：两行都没有
    # Claude 订阅不受影响
    claude = next(v for v in ai_connections.vendors(_agents(True)) if v["id"] == "anthropic")
    assert any(m["id"] == "anthropic:login" for m in claude["methods"])


def test_signed_in_row_is_builtin_with_per_model_efforts(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    (row,) = [c for c in ai_connections.connections(_agents(True)) if c["id"] == "siwc:chatgpt"]
    assert row["state"] == "ready" and row["route"] == "builtin" and row["method"] == "openai:siwc"
    assert row["models"] == ["gpt-a", "gpt-b"] and row["default_model"] == "gpt-a"
    # 与 Key 连接同形：默认型号的档位在行上，每个型号的在 model_details（来自 /v1/models 的 supported_reasoning_levels）
    assert (row["efforts"], row["default_effort"]) == (["low", "medium", "high"], "low")
    assert [d["efforts"] for d in row["model_details"]] == [["low", "medium", "high"], []]
    assert TOKEN not in repr(row)
    agent, rec = ai_connections.resolve_connection("siwc:chatgpt", _agents(True))
    assert agent == ai_agents.BUILTIN_ID and rec["id"] == "siwc:chatgpt"
    assert "api_key" not in rec and TOKEN not in repr(rec)
    # 永不 cli:codex：CLI 家族按 id 解析不到它，不兼容 / 只走内置等按 agent 分流的判据也不当它是 Codex 记录
    assert ai_providers.resolve("codex", "siwc:chatgpt") is None
    assert rec["agent"] == ai_agents.BUILTIN_ID and ai_providers.incompatibility(rec) is None


def test_plan_disabled_and_signed_out_states(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in(scopes=("openid",))
    (row,) = [c for c in ai_connections.connections(_agents(False)) if c["id"] == "siwc:chatgpt"]
    assert (row["state"], row["error"]) == ("error", "plan_usage_disabled")
    assert ai_providers.siwc_record() is None
    siwc_store.save(siwc_store.active().signed_out())
    (row,) = [c for c in ai_connections.connections(_agents(False)) if c["id"] == "siwc:chatgpt"]
    assert row["state"] == "needs_auth"


def test_forced_builtin_off_makes_siwc_unusable(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    env.setenv(ai_connections.ENV_BUILTIN, "0")
    _sign_in()
    with pytest.raises(ai_connections.ConnectionRefused) as ei:
        ai_connections.resolve_connection("siwc:chatgpt", _agents(True))
    assert ei.value.code == "ai_agent_not_usable"


def test_spawn_injection_satisfies_runner_constraints(env):
    """主语：父进程真正交给 runner 的 argv / env。用 runner 自己的判据核，effort 给了也不能带出去。"""
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    rec = ai_providers.resolve(ai_agents.BUILTIN_ID, "siwc:chatgpt")
    args, extra = ai_providers.spawn_overrides(ai_agents.BUILTIN_ID, rec, "gpt-a", "high")
    assert extra == {ai_agents.BUILTIN_KEY_ENV: TOKEN, siwc_wire.ENV_FLAG: "1"}
    assert TOKEN not in " ".join(args)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    assert (
        opt("--wire") == siwc_wire.WIRE_NAME
        and opt("--model") == "gpt-a"
        and opt("--effort") == "high"
    )
    assert siwc_wire.runner_args_ok(
        base_url=opt("--base-url"),
        auth=opt("--auth", "key"),
        fallback=[a for i, a in enumerate(args) if i and args[i - 1] == "--fallback"],
        effort=opt("--effort"),
        thinking=opt("--thinking", "none"),
    )


def test_no_token_means_no_spawn(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    siwc_auth.set_provider_for_tests(siwc_auth.DisabledProvider())
    rec = ai_providers.resolve(ai_agents.BUILTIN_ID, "siwc:chatgpt")
    assert ai_providers.spawn_overrides(ai_agents.BUILTIN_ID, rec, None) == ([], {})


@pytest.mark.parametrize(
    ("prov", "code"),
    [
        (siwc_auth.DisabledProvider(), "ai_endpoint_needs_login"),
        (
            siwc_auth.MemoryProvider(siwc_auth.TokenSet("a", "r", 0, frozenset())),
            "ai_siwc_plan_disabled",
        ),
        (
            siwc_auth.MemoryProvider(
                siwc_auth.TokenSet(
                    "a", "r", siwc_auth._now_ms() + 60_000, frozenset({siwc_auth.REQUIRED_SCOPE})
                )
            ),
            "ai_siwc_token_unavailable",  # 快过期、刷新暂时失败
        ),
    ],
)
def test_preflight_refusals(env, prov, code):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    siwc_auth.set_provider_for_tests(prov)
    err = ai_bridge._siwc_preflight(ai_providers.siwc_record())
    assert err is not None and err.code == code


def test_preflight_ok_with_fresh_token(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    assert ai_bridge._siwc_preflight(ai_providers.siwc_record()) is None


# --------------------------------------------------------------------------- 存储与探测碰不到它
def test_cannot_save_or_probe_a_record_as_siwc(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    with pytest.raises(ai_connections.ConnectionRefused):
        ai_connections.add_connection(
            {
                "vendor": "custom",
                "api_key": "k",
                "base_url": "https://x.example/v1",
                "wire": "siwc-responses",
            }
        )
    with pytest.raises(ai_connections.ConnectionRefused):
        ai_connections.add_connection({"id": "siwc:chatgpt", "api_key": "k"})
    with pytest.raises(ai_connections.ConnectionRefused):
        ai_connections.check_connection("siwc:chatgpt")


def test_disconnect_signs_out(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    calls = []
    env.setattr(siwc_login, "logout", lambda: calls.append(1) or {"revoked": True})
    assert ai_connections.delete_connection("siwc:chatgpt") == {"revoked": True}
    assert calls == [1]
    assert ai_connections.login_target("siwc:chatgpt") == ("siwc", "siwc:chatgpt")
    assert ai_connections.login_target("openai:siwc") == ("siwc", "siwc:chatgpt")


# --------------------------------------------------------------------------- 经 ai_bridge 的命令与会话后处理
def test_bridge_command_for_siwc_is_accepted_by_the_runner_parser(env):
    """主语：`ai_bridge._cmd` 拼出的完整子进程命令，交给 runner 自己的 argparse + 硬约束。"""
    from tavotto.engine.agent_runner import runner_cli

    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    rec = ai_providers.resolve(ai_agents.BUILTIN_ID, "siwc:chatgpt")
    cmd, extra = ai_bridge._cmd(
        "builtin", "p", "/tmp/cand", model="gpt-a", endpoint=rec, target="fig1.py", effort="high"
    )
    assert TOKEN not in " ".join(cmd)
    assert extra[ai_agents.BUILTIN_KEY_ENV] == TOKEN and extra[siwc_wire.ENV_FLAG] == "1"
    argv = cmd[cmd.index("agent-run") + 1 :]
    args = runner_cli._parser().parse_args(argv)
    assert siwc_wire.runner_args_ok(
        base_url=args.base_url,
        auth=args.auth,
        fallback=args.fallback or [],
        effort=args.effort,
        thinking=args.thinking,
    )


def test_runner_auth_failure_forces_refresh_next_time(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    calls = []

    def refresh(ts):
        calls.append(1)
        return siwc_auth.TokenSet("acc-new", "ref-new", siwc_auth._now_ms() + 3_600_000, ts.scopes)

    siwc_auth.set_provider_for_tests(
        siwc_auth.MemoryProvider(siwc_store.active().tokens(), refresh=refresh)
    )
    rec = ai_providers.siwc_record()
    ai_bridge._siwc_after_runner_error(rec, "agent_network")  # 别的失败不动令牌
    assert siwc_login.provider().access_token() == TOKEN and calls == []
    ai_bridge._siwc_after_runner_error(rec, "agent_auth")
    assert siwc_login.provider().access_token() == "acc-new" and calls == [1]


def test_store_provider_is_one_instance_per_process(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    siwc_login.reset_for_tests()
    assert siwc_login.provider() is siwc_login.provider()
    siwc_login.reset_for_tests()


def test_effort_not_declared_by_the_model_is_not_sent(env):
    env.setenv(siwc_wire.ENV_FLAG, "1")
    _sign_in()
    rec = ai_providers.resolve(ai_agents.BUILTIN_ID, "siwc:chatgpt")
    for model, effort in (("gpt-b", "high"), ("gpt-a", "xhigh"), ("gpt-a", "minimal")):
        args, _ = ai_providers.spawn_overrides(ai_agents.BUILTIN_ID, rec, model, effort)
        assert "--effort" not in args, (model, effort)


def test_codex_fallback_choice_brings_codex_login_back(env, tmp_path):
    """失败选择题里选了「改用 Codex 登录」（维护者 2026-11-25）：之后 Codex 登录方式与 sub:codex 连接重新出现；没选过的人不受影响。"""
    from tavotto.engine import config

    env.setenv(siwc_wire.ENV_FLAG, "1")
    env.setattr(config, "ai_settings", lambda: dict(_settings))
    env.setattr(config, "set_ai_settings", lambda patch: _settings.update(patch) or dict(_settings))
    _settings: dict = {}
    hidden = [m["id"] for m in _openai(ai_connections.vendors(_agents(True)))["methods"]]
    assert "openai:login" not in hidden
    ai_connections.set_codex_fallback()
    shown = [m["id"] for m in _openai(ai_connections.vendors(_agents(True)))["methods"]]
    assert shown[:2] == ["openai:siwc", "openai:login"]  # ChatGPT 登录仍在最前
