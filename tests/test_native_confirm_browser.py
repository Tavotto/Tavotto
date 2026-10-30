"""浏览器模式的显式确认（#1500，ADR 0127 修订 2026-10-30）。

safe 被自己的守卫拦下、而这次运行不是桌面窗口发起的：没有静默回退（ADR 0127 D1 不变）；失败卡改给「允许并运行」，
用户点一次 = 授权**这一次**以 native 重跑。授权来源是服务端核得到的「人在页面上点了」：

* 请求靠**会话 cookie** 认证，本机凭据头（MCP / CLI / Agent / AI 修复工具链用的那条）不算；
* 请求回显的 `confirm_token` 是服务端为**那一次失败尝试**签发的，只随页面 cookie 认证的报告交出，用过即废。

看护的主语是**授权来源与一次性标识**，不是「按钮出现了」。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tavotto import app as m, security
from tavotto.engine import execpolicy, fixrouter, pool as engine_pool, prepsession
from tavotto.engine.session_client import AUTH_HEADER
from test_execution_fallback import BLOCKED, _history  # noqa: F401
from test_execution_fallback_origin import (  # noqa: F401
    NONCE,
    SECRET,
    _mode,
    _restore_auth_config,
    _run,
    native,
)
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
    _action,
    _settle,
    sessions,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "tavotto"
KIND = "confirm_native_run"


def _kinds(report):
    return [a["kind"] for a in report["actions"]]


def _browser(client):
    """浏览器模式：有本机凭据 + 兑换了 nonce 的 cookie 的标签页。"""
    state = _mode(desktop=False, secret=SECRET)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    return state


def _confirm(client, report, *, token="from-report", headers=None, action=None):
    action = action or _action(report, KIND)
    body = {
        "action_id": action["id"],
        "expected_config_revision": report["config_revision"],
    }
    if token == "from-report":
        token = action.get("confirm_token")
    if token is not None:
        body["confirm_token"] = token
    return client.post(
        f"{SESSIONS}/{report['session_id']}/actions", json=body, headers=headers or {}
    )


def _get(client, report, headers=None):
    return client.get(f"{SESSIONS}/{report['session_id']}", headers=headers or {}).get_json()


@pytest.fixture
def blocked(client, tmp_path, fake_pool, native, sessions):
    """浏览器标签页跑了一次、被 safe 守卫拦下的会话。"""
    _open(client, _project(tmp_path, "p"))
    _browser(client)
    final = _run(client, tmp_path, fake_pool)
    assert final["outcome"]["code"] == "safe_file_operation_blocked"
    assert native["calls"] == []
    return {"report": final, "native": native, "pool": fake_pool}


# ---------------------------------------------------------------- A：卡片与路由


def test_the_page_sees_the_confirm_action_and_the_remedy_is_not_ai(blocked):
    report = blocked["report"]
    assert KIND in _kinds(report)
    assert _action(report, KIND)["confirm_token"]
    # 优先于 ai_fix / connect_ai：不路由进 AI 修复
    assert report["remedy"] == {"kind": "switch_native", "silent": False}
    assert _history(report)[0]["declined"] == "not_desktop_initiated"


def test_header_only_readers_get_neither_the_action_nor_the_token(client, blocked):
    """MCP / CLI / AI 修复工具链读报告（本机凭据头）：看不到确认动作，更拿不到 token。"""
    report = blocked["report"]
    for headers in ({AUTH_HEADER: SECRET},):
        got = _get(client, report, headers)  # 浏览器 cookie 还在，但带了凭据头 = 非页面
        assert KIND not in _kinds(got)
        assert "confirm_token" not in json.dumps(got)
    client.delete_cookie(security.COOKIE_NAME)
    got = _get(client, report, {AUTH_HEADER: SECRET})
    assert KIND not in _kinds(got) and "confirm_token" not in json.dumps(got)


def test_the_token_is_not_in_the_diagnostic_fix_journey(blocked):
    report = blocked["report"]
    token = _action(report, KIND)["confirm_token"]
    journeys = prepsession.SESSIONS.fix_journeys(report["project_id"])
    assert token not in json.dumps(journeys, default=str)


# ---------------------------------------------------------------- B：确认只认页面 + token


def test_a_page_click_with_the_token_reruns_natively_exactly_once(client, blocked):
    report, native = blocked["report"], blocked["native"]
    resp = _confirm(client, report)
    assert resp.status_code == 202, resp.get_json()
    final = _settle(client, report["session_id"])
    assert len(native["calls"]) == 1 and final["phase"] == "completed"
    hist = _history(final)
    assert [h["profile"] for h in hist] == ["safe", "native"]
    assert hist[1]["cause"] == "safe_file_operation_blocked" and "profile" in hist[1]["changed"]
    # 同一会话、同一次运行（共用 RetryGuard 账本），做完不再给第二次
    assert hist[0]["run"] == hist[1]["run"]
    assert KIND not in _kinds(final)


def test_the_confirmation_is_not_remembered(client, blocked):
    """不写 nativeperm、不改项目 execution_policy：下一次运行仍要再点。"""
    report, native = blocked["report"], blocked["native"]
    from tavotto.engine import nativeperm

    root = str(m.CTX.path) if hasattr(m, "CTX") else None  # noqa: F841 — 见下：用会话自己的 project_root
    sess = prepsession.SESSIONS.session(report["session_id"], report["project_id"])
    before_mode = execpolicy.state(sess.project_root)
    assert _confirm(client, report).status_code == 202
    _settle(client, report["session_id"])
    assert execpolicy.state(sess.project_root) == before_mode
    assert execpolicy.decided(sess.project_root) is False
    assert nativeperm.__name__  # 模块在；它的存储不应被本路径碰到（下面的源码扫描钉住）
    assert len(native["calls"]) == 1


def test_a_second_run_in_the_browser_is_offered_a_fresh_confirmation(client, tmp_path, blocked):
    report = blocked["report"]
    assert _confirm(client, report).status_code == 202
    done = _settle(client, report["session_id"])
    assert KIND not in _kinds(done)
    # 用户再点一次运行 = 新的运行：safe 再被拦，又出现一张新的确认（新 token）
    blocked["pool"]["error"] = BLOCKED
    old = _action(report, KIND)
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(done, "run")["id"],
            "expected_config_revision": done["config_revision"],
        },
    )
    assert resp.status_code == 202, resp.get_json()
    again = _settle(client, report["session_id"])
    assert KIND in _kinds(again)
    assert _action(again, KIND)["confirm_token"] != old["confirm_token"]


def test_the_local_secret_header_cannot_confirm_even_with_the_right_token(client, blocked):
    """持有本机凭据头（MCP / CLI / AI 修复工具链）+ 从页面报告里拿到的正确 token，仍不行。"""
    report, native = blocked["report"], blocked["native"]
    action = _action(report, KIND)
    client.delete_cookie(security.COOKIE_NAME)
    resp = _confirm(client, report, headers={AUTH_HEADER: SECRET}, action=action)
    assert resp.status_code == 403
    assert resp.get_json()["code"] == prepsession.ERROR_CONFIRM_REQUIRES_PAGE
    assert native["calls"] == []


def test_cookie_plus_secret_header_is_not_a_page_click(client, blocked):
    report, native = blocked["report"], blocked["native"]
    resp = _confirm(client, report, headers={AUTH_HEADER: SECRET})  # jar 里还有 cookie
    assert resp.status_code == 403
    assert resp.get_json()["code"] == prepsession.ERROR_CONFIRM_REQUIRES_PAGE
    assert native["calls"] == []


def test_the_unauthenticated_test_mode_cannot_confirm(client, blocked):
    """没有会话状态（测试 / --insecure-no-auth）：fail closed——没有可验证的页面来源。"""
    report, native = blocked["report"], blocked["native"]
    action = _action(report, KIND)
    m.app.config.pop(security.STATE_KEY, None)
    client.delete_cookie(security.COOKIE_NAME)
    resp = _confirm(client, report, action=action)
    assert resp.status_code == 403
    assert resp.get_json()["code"] == prepsession.ERROR_CONFIRM_REQUIRES_PAGE
    assert native["calls"] == []


@pytest.mark.parametrize("token", [None, "", "guess", "x" * 43])
def test_a_missing_or_wrong_token_is_refused_with_no_execution(client, blocked, token):
    report, native = blocked["report"], blocked["native"]
    resp = _confirm(client, report, token=token)
    assert resp.status_code == 403
    assert resp.get_json()["code"] == prepsession.ERROR_CONFIRM_INVALID
    assert native["calls"] == []
    # 猜错不烧掉真 token（否则任何人能让用户的按钮失效）：真 token 仍可用
    assert _confirm(client, report).status_code == 202


def test_a_self_reported_user_field_in_the_body_is_rejected(client, blocked):
    report, native = blocked["report"], blocked["native"]
    action = _action(report, KIND)
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": action["id"],
            "expected_config_revision": report["config_revision"],
            "user_confirmed": True,
            "confirmed_by": "user",
        },
    )
    assert resp.status_code == 400 and native["calls"] == []


def test_the_token_is_single_use_a_replay_adds_no_execution(client, blocked):
    report, native = blocked["report"], blocked["native"]
    action = _action(report, KIND)
    assert _confirm(client, report, action=action).status_code == 202
    _settle(client, report["session_id"])
    replay = _confirm(client, report, action=action)  # 同一 id + 同一 token
    assert replay.status_code == 200 and replay.get_json()["claimed"] is False
    assert len(native["calls"]) == 1


def test_a_token_dies_with_the_failed_attempt_it_was_issued_for(client, blocked):
    """token 绑定「那一次」失败尝试：用户重跑出了新的失败，旧确认（id 与 token）作废。"""
    report = blocked["report"]
    old = _action(report, KIND)
    blocked["pool"]["error"] = BLOCKED
    run = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
    )
    assert run.status_code == 202, run.get_json()
    fresh = _settle(client, report["session_id"])
    new = _action(fresh, KIND)
    assert new["id"] != old["id"] and new["confirm_token"] != old["confirm_token"]
    stale = _confirm(client, fresh, action=old)
    assert stale.status_code in (403, 404) and blocked["native"]["calls"] == []


# ---------------------------------------------------------------- 闸门照旧：开关 / 策略 / 账本


def test_safe_only_gives_no_button(client, tmp_path, fake_pool, native, sessions):
    _open(client, _project(tmp_path, "p"))
    _browser(client)
    from tavotto.engine import config

    root = str(next(iter(m.PROJECTS.values())).path)
    execpolicy.set_mode(root, execpolicy.MODE_SAFE_ONLY)
    try:
        final = _run(client, tmp_path, fake_pool)
    finally:
        config.set_project_settings(root, {execpolicy.SETTINGS_KEY: None})
    assert KIND not in _kinds(final) and native["calls"] == []


def test_the_ops_switch_gives_no_button(client, tmp_path, fake_pool, native, sessions, monkeypatch):
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, "0")
    _open(client, _project(tmp_path, "p"))
    _browser(client)
    final = _run(client, tmp_path, fake_pool)
    assert KIND not in _kinds(final) and native["calls"] == []


def test_policy_changed_after_the_card_appeared_still_vetoes_a_valid_click(
    client, blocked, monkeypatch
):
    """竞态：卡片出现之后用户 / 运维才改了策略；带着有效 cookie + token 的点击也不执行。"""
    report, native = blocked["report"], blocked["native"]
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, "0")
    action = _action(report, KIND)
    resp = _confirm(client, report, action=action)
    assert resp.status_code == 409 and native["calls"] == []
    # 验证过的 token 即废：策略恢复之后同一个 id + token 也不能再用（要重新读报告拿新的）
    monkeypatch.delenv(execpolicy.FALLBACK_ENV)
    again = _confirm(client, report, action=action)
    assert again.status_code in (403, 404) and native["calls"] == []


def test_the_desktop_decline_reasons_are_not_overridable_by_a_click():
    """只有「不是桌面发起」这一种没回退可以被确认顶替；其它否决理由（只用安全运行 / 开关 / 取消…）一道不少。"""
    ledger = execpolicy.AttemptLedger()
    ledger.begin_run()
    safe = execpolicy.SpecKey("safe", "i", "l")
    native = execpolicy.SpecKey("native", "i", "")
    ledger.record("a1", safe)
    kw = dict(
        mode="compatible", enabled=True, ledger=ledger, cause_code="safe_file_operation_blocked"
    )
    assert execpolicy.decide_fallback(**kw, nxt=native).reason == execpolicy.REASON_NOT_DESKTOP
    assert execpolicy.decide_fallback(**kw, nxt=native, user_confirmed=True).ok
    assert (
        execpolicy.decide_fallback(
            **{**kw, "mode": "safe_only"}, nxt=native, user_confirmed=True
        ).reason
        == execpolicy.REASON_SAFE_ONLY
    )
    assert (
        execpolicy.decide_fallback(
            **{**kw, "enabled": False}, nxt=native, user_confirmed=True
        ).reason
        == execpolicy.REASON_DISABLED
    )
    assert (
        execpolicy.decide_fallback(
            **{**kw, "cause_code": "script_error"}, nxt=native, user_confirmed=True
        ).reason
        == execpolicy.REASON_NOT_BLOCKED
    )
    assert (
        execpolicy.decide_fallback(**kw, nxt=native, user_confirmed=True, cancelled=True).reason
        == execpolicy.REASON_CANCELLED
    )
    ledger.record("a2", native, cause="safe_file_operation_blocked")
    assert (
        execpolicy.decide_fallback(**kw, nxt=native, user_confirmed=True).reason
        == execpolicy.REASON_ALREADY_FELL_BACK
    )  # 与桌面回退共用账本：一次运行至多一次


# ---------------------------------------------------------------- 例外只有这一条：显式 native 仍是 403


def test_explicit_native_from_the_browser_stays_forbidden_even_with_a_page_cookie(
    client, tmp_path, fake_pool, native, sessions
):
    _open(client, _project(tmp_path, "p"))
    _browser(client)
    resp = client.post(SESSIONS, json={"script": "fig.py", "execution": "native"})
    assert resp.status_code == 403
    assert resp.get_json()["code"] == prepsession.ERROR_NATIVE_REQUIRES_DESKTOP
    assert native["calls"] == []


def test_a_desktop_flag_alone_does_not_confirm(client, blocked):
    """`desktop_initiated` 不能顶替页面确认：直接调服务层，传 desktop_initiated=True 但没有 page_confirmed。"""
    report = blocked["report"]
    action = _action(report, KIND)
    with pytest.raises(prepsession.SessionError) as exc:
        prepsession.SESSIONS.act(
            report["session_id"],
            report["project_id"],
            action["id"],
            report["config_revision"],
            launch=lambda plan, on_done: pytest.fail("不该起执行"),
            desktop_initiated=True,
            confirm_token=action["confirm_token"],
        )
    assert exc.value.code == prepsession.ERROR_CONFIRM_REQUIRES_PAGE
    assert blocked["native"]["calls"] == []


# ---------------------------------------------------------------- 其它入口走不到这个确认


def test_no_module_but_the_app_endpoint_grants_page_confirmation():
    """`page_confirmed=` / `user_confirmed=` 为真的字面量只出现在两个地方：app 的会话动作端点（传 `_page_authenticated()`）
    与 prepsession 自己的确认认领。MCP 服务、CLI、AI 修复工具链（engine/fixtools）、旧 /api/engine/preparation 都不碰。"""
    offenders: list[str] = []
    allowed = {
        "app.py": {"page_confirmed"},
        "engine/prepsession.py": {"page_confirmed", "user_confirmed", "confirmed"},
        "engine/execpolicy.py": {"user_confirmed"},
    }
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in (
                "page_confirmed",
                "user_confirmed",
                "confirm_token",
            ):
                if node.arg not in allowed.get(rel, set()) and node.arg != "confirm_token":
                    offenders.append(f"{rel}:{node.lineno}:{node.arg}")
                if rel == "app.py" and node.arg == "page_confirmed":
                    # 唯一的传值是 `_page_authenticated()` 的返回——不是常量
                    assert isinstance(node.value, ast.Call) and node.value.func.id == (
                        "_page_authenticated"
                    ), f"app.py:{node.lineno}"
    assert offenders == []


def test_fix_tools_and_mcp_never_touch_the_session_confirmation():
    """AI 修复工具链（含 `switch_execution`）与 MCP / 插件侧不 import prepsession，也不提确认动作 / token。"""
    roots = [SRC / "engine" / "fixtools", SRC.parent.parent / "codex-plugin"]
    needles = ("prepsession", "confirm_native_run", "confirm_token", "page_confirmed")
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".mjs", ".js", ".ts"} or "node_modules" in path.parts:
                continue
            text = path.read_text("utf-8", errors="ignore")
            hits += [f"{path}:{n}" for n in needles if n in text]
    assert hits == []


def test_switch_execution_only_edits_the_project_setting_and_runs_nothing(
    client, blocked, monkeypatch
):
    """AI 修复的 `switch_execution` 只改项目策略，不替用户确认：改成 compatible 之后，浏览器里仍不静默回退、仍要点。"""
    from tavotto.engine.fixtools import change

    report, native = blocked["report"], blocked["native"]
    sess = prepsession.SESSIONS.session(report["session_id"], report["project_id"])

    class _Ctx:
        project_root = Path(sess.project_root)
        cancel = None

    monkeypatch.setattr(change, "_snapshot_or_error", lambda *a, **k: None)
    monkeypatch.setattr(change, "_done", lambda ctx, note, summary: summary)
    change.switch_execution(_Ctx(), {"mode": "native"})
    try:
        assert native["calls"] == []
        assert _confirm(client, report, headers={AUTH_HEADER: SECRET}).status_code == 403
    finally:
        execpolicy.forget(sess.project_root)


# ---------------------------------------------------------------- fixrouter：A 优先于 AI


@pytest.mark.parametrize("connected", [True, False])
def test_router_prefers_confirmation_over_ai_when_offered(connected):
    got = fixrouter.choose(
        "safe_file_operation_blocked",
        fixrouter.Ctx(ai_connected=connected, confirm_native=True),
    )
    assert got == {"kind": "switch_native", "silent": False}


def test_router_without_the_offer_is_unchanged():
    got = fixrouter.choose("safe_file_operation_blocked", fixrouter.Ctx(ai_connected=True))
    assert got == {"kind": "ai_fix"}


def test_engine_pool_still_reports_the_blocked_code():
    assert engine_pool.FILE_OPERATION_BLOCKED_CODE == "safe_file_operation_blocked"


def test_a_desktop_run_declined_for_another_reason_gets_no_button(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """只有「不是桌面发起」这一种没回退可以靠点击顶替：桌面窗口里 native 计划算不出来（plan_unavailable）时不出确认卡。"""
    import dataclasses

    _open(client, _project(tmp_path, "p"))
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    real = prepsession.SessionService._plan_of

    def plan_of(project_id, project_root, target):
        plan = real(project_id, project_root, target)
        if target.get("execution") == "native":
            plan = dataclasses.replace(plan, native_spec=None)
        return plan

    monkeypatch.setattr(prepsession.SessionService, "_plan_of", staticmethod(plan_of))
    final = _run(client, tmp_path, fake_pool)
    assert _history(final)[0]["declined"] == "plan_unavailable"
    assert KIND not in _kinds(final) and native["calls"] == []
