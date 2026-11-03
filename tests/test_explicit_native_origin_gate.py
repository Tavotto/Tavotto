"""显式 `execution: "native"` 与路由选出的 native 共用同一道来源闸门（ADR 0127 D1 / ADR 0133）——假 pool、假 native runner。

桌面用户发起（`app._desktop_initiated()`）是 native 的授权边界：非桌面来源（浏览器标签页 / 本机凭据 API 客户端 / 无认证入口）
显式要 native 一律拒绝（`native_requires_desktop`，不静默降成 safe）；路由选的 native 在非桌面来源仍按原样降 safe。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import time

import pytest

from tavotto import app as m, security
from tavotto.engine import prepsession
from tavotto.engine.session_client import AUTH_HEADER
from test_execution_fallback import BLOCKED  # noqa: F401
from test_execution_fallback_origin import (  # noqa: F401
    NONCE,
    SECRET,
    _mode,
    _restore_auth_config,
    native,
)
from test_preparation_api import _open, _project, client, fake_pool  # noqa: F401
from test_preparation_session import SESSIONS, _action, sessions  # noqa: F401
from test_runroute_wiring import READBACK

CODE = prepsession.ERROR_NATIVE_REQUIRES_DESKTOP


def _setup(client, tmp_path, source=READBACK):
    root = _project(tmp_path, "p")
    (root / "fig.py").write_text(source, encoding="utf-8")
    _open(client, root)


def _settled(client, sid, kw):
    deadline = time.time() + 10
    while True:
        final = client.get(f"{SESSIONS}/{sid}", **kw).get_json()
        if final["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    return final


def _create(client, kw, **body):
    return client.post(SESSIONS, json={"script": "fig.py", **body}, **kw)


def _claim_run(client, report, kw):
    return client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
        **kw,
    )


def _assert_refused(resp, native, fake_pool):
    assert resp.status_code == 403, resp.get_json()
    assert resp.get_json()["code"] == CODE
    assert native["calls"] == [] and fake_pool["build_calls"] == 0


def test_nondesktop_browser_mode_explicit_native_is_refused(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path)
    state = _mode(desktop=False, secret=SECRET)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    _assert_refused(_create(client, {}, execution="native"), native, fake_pool)


def test_nondesktop_api_client_explicit_native_is_refused(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path)
    _mode(desktop=True, secret=SECRET)
    kw = {"headers": {AUTH_HEADER: SECRET}}
    _assert_refused(_create(client, kw, execution="native"), native, fake_pool)


def test_nondesktop_plain_client_explicit_native_is_refused(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path)
    m.app.config.pop("TAVOTTO_DESKTOP_MODE", None)
    _assert_refused(_create(client, {}, execution="native"), native, fake_pool)


def test_nondesktop_claim_of_a_desktop_created_native_session_is_refused(
    client, tmp_path, fake_pool, native, sessions
):
    """纵深：会话由桌面窗口建好，认领 `run` 的却是本机凭据客户端——认领那一刻再核一次来源。"""
    _setup(client, tmp_path)
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    resp = _create(client, {}, execution="native")
    assert resp.status_code == 201, resp.get_json()
    report = resp.get_json()
    state.api_secret = SECRET  # 之后换成带本机凭据头的 API 客户端认领
    kw = {"headers": {AUTH_HEADER: SECRET}}
    _assert_refused(_claim_run(client, report, kw), native, fake_pool)
    assert _settled(client, report["session_id"], kw)["phase"] != "running"


def test_desktop_window_explicit_native_runs_native(client, tmp_path, fake_pool, native, sessions):
    _setup(client, tmp_path)
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    resp = _create(client, {}, execution="native")
    assert resp.status_code == 201, resp.get_json()
    report = resp.get_json()
    assert _claim_run(client, report, {}).status_code == 202
    final = _settled(client, report["session_id"], {})
    assert len(native["calls"]) == 1 and fake_pool["build_calls"] == 0
    assert [h["profile"] for h in final["attempt_history"]] == ["native"]


@pytest.mark.parametrize("who", ["plain", "browser", "api"])
def test_nondesktop_explicit_safe_still_runs_safe(
    who, client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path)
    kw = {}
    if who == "plain":
        m.app.config.pop("TAVOTTO_DESKTOP_MODE", None)
    elif who == "browser":
        state = _mode(desktop=False, secret=SECRET)
        client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    else:
        _mode(desktop=True, secret=SECRET)
        kw = {"headers": {AUTH_HEADER: SECRET}}
    resp = _create(client, kw, execution="safe")
    assert resp.status_code == 201, resp.get_json()
    report = resp.get_json()
    assert _claim_run(client, report, kw).status_code == 202
    final = _settled(client, report["session_id"], kw)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1
    assert [h["profile"] for h in final["attempt_history"]] == ["safe"]


def test_nondesktop_auto_routed_native_still_degrades_to_safe(
    client, tmp_path, fake_pool, native, sessions
):
    """路由选的 native（省略 execution）在非桌面来源的现有降级行为不变：safe + `declined`，不是新错误码。"""
    _setup(client, tmp_path)
    _mode(desktop=True, secret=SECRET)
    kw = {"headers": {AUTH_HEADER: SECRET}}
    resp = _create(client, kw)
    assert resp.status_code == 201, resp.get_json()
    report = resp.get_json()
    assert report["plan"]["route"]["execution"] == "native"
    assert _claim_run(client, report, kw).status_code == 202
    final = _settled(client, report["session_id"], kw)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1
    assert final["plan"]["route"]["declined"] == "not_desktop_initiated"
    assert [h["profile"] for h in final["attempt_history"]] == ["safe"]
