# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「读懂数据」有没有可用连接（`ai_data_roles.available()`；前端「连接 AI」胶囊的依据，ADR 0178）。

判据的主语：`available()` 与 `start()` 用同一个 `_available_connection()`——没连 / 运维关掉内置 = 不可用；订阅登录只有在它的 CLI 能做
无工具单轮调用时才算可用；有 Key 连接 = 可用，且不刷新令牌、不联网；`/api/ai/data-files` 的响应如实带上这个结论。
"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_connections, ai_data_roles as R, ai_structured
from tests.test_ai_data_roles import (  # noqa: F401
    CLI_CONN,
    D,
    isolate,
    project,
)


@pytest.mark.parametrize("why", ["none", "builtin_off"])
def test_available_is_false_without_any_usable_connection(monkeypatch, why):
    """没连 / 运维关掉内置：`available()` 为 False；判据与 `start()` 同一个 `_available_connection()`。"""
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    monkeypatch.setattr(
        ai_connections, "builtin_forced", lambda: False if why == "builtin_off" else None
    )
    assert R.available() is False  # conftest：默认没有 CLI 连接


def test_available_follows_the_subscription_login_when_its_cli_can_do_structured_calls(monkeypatch):
    """只有订阅登录：适配器支持无工具单轮（#2090）= 可用，胶囊不该再出现；不支持 / 还没有能力缓存 = 不可用。运维开关关掉时一并不可用。"""
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(
        ai_structured,
        "find_cli_connection",
        lambda *, cached_only: (None, "unsupported_connection", "cli_only"),
    )
    assert R.available() is False
    monkeypatch.setattr(
        ai_structured, "find_cli_connection", lambda *, cached_only: (CLI_CONN, "", "")
    )
    assert R.available() is True
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: False)
    assert R.available() is False


def test_available_is_true_with_a_key_connection_and_never_refreshes_or_touches_the_network(
    monkeypatch,
):
    seen: list[bool] = []

    def first(refresh=False):
        seen.append(refresh)
        return D.fake_conn()

    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", first)
    assert R.available() is True
    assert seen == [
        False
    ]  # 不刷新令牌；联网由导入的 autouse 夹具 `isolate`（任何 socket 访问即失败）兜底


def test_data_files_response_carries_understanding_available(project, monkeypatch):
    m, client, figs, _state = project
    (figs / "d.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: None)
    assert (
        client.get("/api/ai/data-files?formats=all").get_json()["understanding_available"] is False
    )
    monkeypatch.setattr(ai_connections, "builtin_forced", lambda: None)
    monkeypatch.setattr(ai_connections, "first_key_connection", lambda refresh=False: D.fake_conn())
    body = client.get("/api/ai/data-files?formats=all").get_json()
    assert body["understanding_available"] is True and body["files"][0]["path"] == "d.csv"
