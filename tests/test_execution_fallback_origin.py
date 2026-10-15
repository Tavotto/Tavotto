"""V2 PR1 第二轮（Codex P1）：执行回退只授权给**桌面用户主动发起**的运行（ADR 0127 D1）。

「桌面用户发起」是**服务端可验证的事实**，不是请求体里的自报字段：进程以 `--desktop-sidecar` 启动
（`app.config["TAVOTTO_DESKTOP_MODE"]`，只由 `SidecarServer` 设置）、没有磁盘本机凭据（`api_secret is None`），
且这个请求带着由启动 nonce 兑换的会话 cookie。浏览器模式 / 带本机凭据的 API 客户端（MCP、Agent、脚本）/ 无认证的
测试与 `--insecure-no-auth` / 旧的 `/api/engine/preparation` 都不是——它们遇到 `safe_file_operation_blocked`
照旧停在 safe 的限制上，**一个 native 进程都不起**。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tavotto import app as m, security
from tavotto.engine import pool as engine_pool, preparation
from tavotto.engine.session_client import AUTH_HEADER
from test_execution_fallback import BLOCKED, _history  # noqa: F401
from test_preparation_api import (  # noqa: F401
    _build_resp,
    _FakeWorker,
    _open,
    _project,
    client,
    fake_pool,
)
from test_preparation_session import (  # noqa: F401
    SESSIONS,
    _act,
    _action,
    _create,
    _settle,
    sessions,
)

NONCE, SECRET = "nonce-origin-test", "secret-origin-test"


@pytest.fixture
def native(monkeypatch, fake_pool, tmp_path):
    """只数调用的 native runner 替身；**不**改 `_desktop_initiated`（走真判据）。"""
    box = {"calls": []}

    def runner(pl, before_retry=None):
        box["calls"].append(pl)
        return _FakeWorker(Path(pl.project_root)), _build_resp(), True

    monkeypatch.setattr(m, "_native_preparation_runner", runner)
    # 假解释器必须是**本平台的绝对路径**：桌面拥有的 native 计划（`execspec.desktop_native_spec`）用 `os.path.isabs`
    # 校验解释器与 cwd，Windows 上 `/envs/fake/bin/python` 不是绝对路径，回退计划就算不出来
    fake_python = str(tmp_path / "envs" / "fake" / "bin" / "python")
    monkeypatch.setattr(
        engine_pool, "resolve_worker_python", lambda root=None, **kw: (fake_python, "system")
    )
    return box


@pytest.fixture(autouse=True)
def _restore_auth_config():
    keys = (security.STATE_KEY, "TAVOTTO_DESKTOP_MODE")
    before = {k: m.app.config.get(k) for k in keys if k in m.app.config}
    yield
    for k in keys:
        m.app.config.pop(k, None)
    m.app.config.update(before)


def _mode(*, desktop: bool, secret: str | None, with_state: bool = True):
    if with_state:
        state = security.SessionState(NONCE, api_secret=secret)
        m.app.config[security.STATE_KEY] = state
    else:
        state = None
    if desktop:
        m.app.config["TAVOTTO_DESKTOP_MODE"] = True
    return state


def _run(client, tmp_path, fake_pool, headers=None):
    fake_pool["error"] = BLOCKED
    kw = {"headers": headers or {}}
    report = client.post(SESSIONS, json={"script": "fig.py"}, **kw).get_json()
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
        **kw,
    )
    assert resp.status_code == 202, resp.get_json()
    deadline = time.time() + 10
    while True:
        final = client.get(f"{SESSIONS}/{report['session_id']}", **kw).get_json()
        if final["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    return final


# ---------------------------------------------------------------- 判据本身


def _verdict(headers=None, cookies=None):
    with m.app.test_request_context("/x", headers=headers or {}):
        if cookies:
            for k, v in cookies.items():
                m.request.cookies = {**m.request.cookies, k: v}
        return m._desktop_initiated()


def test_without_any_session_state_the_run_is_not_desktop_initiated():
    """无认证的测试 / `--insecure-no-auth`：没有任何可验证的来源，按非桌面（fail closed）。"""
    _mode(desktop=True, secret=None, with_state=False)
    assert _verdict() is False


def test_browser_mode_is_not_desktop_initiated():
    state = _mode(desktop=False, secret=SECRET)
    token = state.redeem(NONCE)
    with m.app.test_request_context("/x", headers={"Cookie": f"{security.COOKIE_NAME}={token}"}):
        assert m._desktop_initiated() is False  # 浏览器标签页（有 cookie）也不是桌面窗口
    with m.app.test_request_context("/x", headers={AUTH_HEADER: SECRET}):
        assert m._desktop_initiated() is False


def test_a_local_api_secret_client_is_not_desktop_initiated_even_in_desktop_mode():
    _mode(desktop=True, secret=SECRET)
    with m.app.test_request_context("/x", headers={AUTH_HEADER: SECRET}):
        assert m._desktop_initiated() is False


def test_desktop_mode_without_the_window_cookie_is_not_desktop_initiated():
    _mode(desktop=True, secret=None)
    with m.app.test_request_context("/x"):
        assert m._desktop_initiated() is False
    with m.app.test_request_context(
        "/x", headers={"Cookie": f"{security.COOKIE_NAME}=forged-token"}
    ):
        assert m._desktop_initiated() is False


def test_a_self_reported_body_field_cannot_make_a_run_desktop_initiated():
    _mode(desktop=False, secret=SECRET)
    with m.app.test_request_context(
        "/x", method="POST", json={"desktop": True, "owner": "desktop", "execution": "native"}
    ):
        assert m._desktop_initiated() is False


def test_the_desktop_window_cookie_is_desktop_initiated():
    state = _mode(desktop=True, secret=None)
    token = state.redeem(NONCE)
    with m.app.test_request_context("/x", headers={"Cookie": f"{security.COOKIE_NAME}={token}"}):
        assert m._desktop_initiated() is True


# ---------------------------------------------------------------- 经真实会话端点：每类非桌面来源都不回退


def test_plain_test_client_never_falls_back(client, tmp_path, fake_pool, native, sessions):
    _open(client, _project(tmp_path, "p"))
    final = _run(client, tmp_path, fake_pool)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1
    assert final["outcome"]["code"] == "safe_file_operation_blocked"
    assert _history(final)[0]["declined"] == "not_desktop_initiated"


def test_browser_mode_session_never_falls_back(client, tmp_path, fake_pool, native, sessions):
    _open(client, _project(tmp_path, "p"))
    state = _mode(desktop=False, secret=SECRET)
    token = state.redeem(NONCE)
    client.set_cookie(security.COOKIE_NAME, token)
    final = _run(client, tmp_path, fake_pool)
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "not_desktop_initiated"


def test_an_mcp_or_agent_style_api_client_never_falls_back(
    client, tmp_path, fake_pool, native, sessions
):
    """MCP / Agent / 脚本走本机凭据头：即使进程是桌面模式，也不是「桌面用户发起」。"""
    _open(client, _project(tmp_path, "p"))
    _mode(desktop=True, secret=SECRET)
    final = _run(client, tmp_path, fake_pool, headers={AUTH_HEADER: SECRET})
    assert native["calls"] == []
    assert _history(final)[0]["declined"] == "not_desktop_initiated"


def test_run_when_ready_from_a_non_desktop_source_never_falls_back(
    client, tmp_path, fake_pool, native, sessions
):
    """意图在创建时记下来源：非桌面来源「打开并运行」之后自动推进的执行同样不回退。"""
    _open(client, _project(tmp_path, "p"))
    _mode(desktop=True, secret=SECRET)
    hdr = {"headers": {AUTH_HEADER: SECRET}}
    fake_pool["error"] = BLOCKED
    report = client.post(SESSIONS, json={"script": "fig.py"}, **hdr).get_json()
    arm = _action(report, "run_when_ready")
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": arm["id"],
            "expected_config_revision": report["config_revision"],
            "impact_digest": arm["impact"]["impact_digest"],
        },
        **hdr,
    )
    assert resp.status_code in (200, 202), resp.get_json()
    deadline = time.time() + 10
    while True:
        final = client.get(f"{SESSIONS}/{report['session_id']}", **hdr).get_json()
        if final["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1


def test_the_legacy_preparation_endpoint_never_falls_back_even_for_the_desktop(
    client, tmp_path, fake_pool, native, sessions
):
    _open(client, _project(tmp_path, "p"))
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    fake_pool["error"] = BLOCKED
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert resp.status_code == 202, resp.get_json()
    plan = resp.get_json()["plan"]
    deadline = time.time() + 10
    while preparation.SERVICE.get(plan["plan_id"], plan["project_id"])[1].status not in (
        preparation.TERMINAL
    ):
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1


# ---------------------------------------------------------------- 正对照：桌面窗口发起的真走一遍判据


def test_the_desktop_window_session_does_fall_back_through_the_real_check(
    client, tmp_path, fake_pool, native, sessions
):
    _open(client, _project(tmp_path, "p"))
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    final = _run(client, tmp_path, fake_pool)
    assert len(native["calls"]) == 1 and final["phase"] == "completed"
    assert [h["profile"] for h in _history(final)] == ["safe", "native"]


def test_run_when_ready_from_the_desktop_window_falls_back_after_the_automatic_run(
    client, tmp_path, fake_pool, native, sessions
):
    """正对照：桌面窗口「打开并运行」创建的意图记下来源，自动推进的那次执行被拦时同样回退。"""
    _open(client, _project(tmp_path, "p"))
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    fake_pool["error"] = BLOCKED
    report = client.post(SESSIONS, json={"script": "fig.py"}).get_json()
    arm = _action(report, "run_when_ready")
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": arm["id"],
            "expected_config_revision": report["config_revision"],
            "impact_digest": arm["impact"]["impact_digest"],
        },
    )
    assert resp.status_code in (200, 202), resp.get_json()
    final = _settle(client, report["session_id"])
    assert len(native["calls"]) == 1 and final["phase"] == "completed"
    assert final["intent"]["steps"] == 1 and final["intent"]["attempts"] == 2


def test_a_valid_window_cookie_plus_a_local_secret_is_still_not_desktop_initiated():
    """进程里存在本机凭据 = 不是纯桌面窗口（桌面 sidecar 没有磁盘凭据）：带着有效 cookie 也不算。"""
    state = _mode(desktop=True, secret=SECRET)
    token = state.redeem(NONCE)
    with m.app.test_request_context(
        "/x",
        headers={"Cookie": f"{security.COOKIE_NAME}={token}", AUTH_HEADER: SECRET},
    ):
        assert m._desktop_initiated() is False


def test_a_valid_cookie_without_the_sidecar_process_flag_is_not_desktop_initiated():
    """进程不是 `--desktop-sidecar`（`TAVOTTO_DESKTOP_MODE` 没设）：即使状态里没有本机凭据、cookie 有效，也不算桌面。"""
    state = _mode(desktop=False, secret=None)
    token = state.redeem(NONCE)
    with m.app.test_request_context("/x", headers={"Cookie": f"{security.COOKIE_NAME}={token}"}):
        assert m._desktop_initiated() is False
