"""小型号 + 最低档策略与订阅登录（CLI）连接的分界（ADR 0178，2026-11-21 / 11-22）。

主语：**同一个 `call_structured` / `_identity` 入口上，两种连接各自实际发出的东西**——
* CLI 连接（`{"kind": "cli", ...}`）：永远走 `_call_cli`，发给 CLI 的型号是连接自己的，`auto_small` 是空操作，不发任何 HTTP 请求；
* 官方 OpenAI Key 连接：`auto_small=True` 仍是 `gpt-6-luna` + `none`，兜底换回原型号照旧；
* 缓存身份：CLI 用 `cli_identity`，Key 用「provider | 实际型号 | 强度」，互不相同。
全部是假 runner / 假 post，不起进程、不联网。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_cases as D  # noqa: E402

from tavotto.engine import ai_data_roles as R, ai_structured as S  # noqa: E402
from tavotto.engine.agent_runner import wires  # noqa: E402
from tests.test_ai_structured_cli import (  # noqa: E402, F401
    CLAUDE_CONN,
    CODEX_CONN,
    JSON_OK,
    FakeRunner,
    hermetic,
    validate_answer,
)

OFFICIAL = {
    "wire": "openai-responses",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-test-NOT-A-REAL-KEY",
    "model": "gpt-6.2-sol",
    "auth": "key",
    "provider_id": "openai",
}


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    S.reset_small_for_tests()
    yield
    S.reset_small_for_tests()


@pytest.mark.parametrize("conn", [CODEX_CONN, CLAUDE_CONN], ids=["codex", "claude"])
def test_cli_connection_ignores_auto_small_and_uses_its_own_model_without_http(conn):
    runner = FakeRunner([JSON_OK], conn["agent"])
    res = S.call_structured(
        system="SYS",
        user="USR",
        validate=validate_answer,
        conn=conn,
        runner=runner,
        auto_small=True,
        post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("http used")),
    )
    assert res.ok and res.model == conn["model"] and res.wire == f"cli:{conn['agent']}"
    assert len(runner.runs) == 1 and not res.fell_back
    argv = runner.runs[0].argv
    assert conn["model"] in argv and not any("gpt-6-luna" in a for a in argv)


def test_cli_connection_has_no_small_plan_and_keeps_its_own_model():
    for conn in (
        CODEX_CONN,
        CLAUDE_CONN,
        {**CODEX_CONN, "auth": "key", "wire": "openai-responses"},
    ):
        assert S.small_plan(conn) is None
        assert S.effective_choice(conn, "low") == (conn["model"], "low")


def test_official_key_connection_still_gets_the_small_model_and_the_fallback():
    t = D.FakeTransport([D.reply_body("openai-responses", JSON_OK)])
    res = S.call_structured(
        system="s", user="u", validate=validate_answer, conn=dict(OFFICIAL), post=t, auto_small=True
    )
    assert res.ok and t.calls[0]["body"]["model"] == "gpt-6-luna"
    assert t.calls[0]["body"]["reasoning"] == {"effort": "none"}
    S.reset_small_for_tests()
    t2 = D.FakeTransport([404, D.reply_body("openai-responses", JSON_OK)])
    res2 = S.call_structured(
        system="s",
        user="u",
        validate=validate_answer,
        conn=dict(OFFICIAL),
        post=t2,
        auto_small=True,
    )
    assert res2.ok and res2.fell_back and res2.requests == 2
    assert [c["body"]["model"] for c in t2.calls] == ["gpt-6-luna", "gpt-6.2-sol"]


def test_cache_identity_shapes_for_cli_and_key_connections():
    assert R._identity(CODEX_CONN) == "cli:codex|gpt-test"
    assert R._identity(CLAUDE_CONN) == "cli:claude|claude-test"
    assert R._identity(OFFICIAL) == "openai|gpt-6-luna|none"
    S.mark_small_unusable(OFFICIAL, "gpt-6-luna")
    assert R._identity(OFFICIAL) == "openai|gpt-6.2-sol|low"
    ids = {
        R._identity(c)
        for c in (CODEX_CONN, CLAUDE_CONN, OFFICIAL, {**OFFICIAL, "model": "gpt-6-luna"})
    }
    assert len(ids) == 4
    S.reset_small_for_tests()
    key_ids = {R._identity(OFFICIAL), R._identity({**OFFICIAL, "model": "gpt-6-luna"})}
    assert key_ids == {"openai|gpt-6-luna|none", "openai|gpt-6-luna|low"}
