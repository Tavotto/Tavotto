"""V2 PR2：运行前路由接进桌面准备会话（ADR 0133）——**假 pool、假 native runner**。

验接线：桌面准备会话不指定 `execution` 时由 `runroute` 在 safe / native 之间挑；高置信读回直达 native（safe 一次都不跑）；
普通绘图走 safe；`safe_only` / 运维开关 / 上次 native 成功各自的优先级；显式 `execution` 与 CLI / MCP 路径不经路由。
真子进程穿过同一条路的验收在 `test_run_routing_e2e.py`。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tavotto import app as m, security
from tavotto.engine import (
    execpolicy,
    launchctx,
    pool as engine_pool,
    preparation,
    prepsession,
    runconfig,
    runroute,
)
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
from test_preparation_session import (  # noqa: F401
    SESSIONS,
    _act,
    _action,
    _create,
    _get,
    _settle,
    sessions,
)


@pytest.fixture(autouse=True)
def _desktop_window(request, client, _restore_auth_config):
    """默认：桌面窗口发起（cookie 兑换自启动 nonce）。非桌面来源的反例用例用 `no_desktop` 标记自己建来源。"""
    if "nondesktop" in request.node.name:
        yield
        return
    state = _mode(desktop=True, secret=None)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    yield


READBACK = """\
import matplotlib.pyplot as plt
from PIL import Image
fig, ax = plt.subplots()
ax.plot([1, 2])
fig.savefig("stage.png")
Image.open("stage.png").load()
"""
#: 动态路径：静态看不出读回（R4，低置信），safe 实跑才被拦
DYNAMIC = """\
import os
import matplotlib.pyplot as plt
from PIL import Image
out = os.environ.get("OUT_PNG", "stage.png")
fig, ax = plt.subplots()
ax.plot([1, 2])
fig.savefig(out)
Image.open(out).load()
"""


def _setup(client, tmp_path, source=None, name="p"):
    root = _project(tmp_path, name)
    if source is not None:
        (root / "fig.py").write_text(source, encoding="utf-8")
    _open(client, root)
    return root


def _report(client, **target):
    return _create(client, {"script": "fig.py", **target}).get_json()


def _run(client, report):
    resp = _act(
        client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    assert resp.status_code == 202, resp.get_json()
    return _settle(client, report["session_id"])


def _route(report):
    return report["plan"]["route"]


def test_an_ordinary_plot_is_routed_safe_and_runs_in_safe(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path)
    report = _report(client)
    route = _route(report)
    assert (route["execution"], route["reason"], route["confidence"]) == (
        "safe",
        "ordinary_plot",
        "high",
    )
    final = _run(client, report)
    assert fake_pool["build_calls"] == 1 and native["calls"] == []
    assert [h["profile"] for h in final["attempt_history"]] == ["safe"]


def test_a_savefig_readback_goes_straight_to_native_and_safe_never_runs(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    report = _report(client)
    route = _route(report)
    assert (route["execution"], route["reason"], route["confidence"]) == (
        "native",
        "savefig_readback",
        "high",
    )
    assert route["required_execution"] == "preserve_file_semantics"
    assert route["rules"] == ["R1"] and route["elapsed_ms"] > 0
    final = _run(client, report)
    assert fake_pool["build_calls"] == 0, "路由已经定了 native：safe 白跑一遍的那次不存在"
    assert len(native["calls"]) == 1 and native["calls"][0].execution == "native"
    assert final["phase"] == "completed"
    assert [h["profile"] for h in final["attempt_history"]] == ["native"]
    assert final["attempt_history"][0]["cause"] == ""  # 第一次尝试，不是重试
    assert len(prepsession.SESSIONS._by_id) == 1


def test_a_dynamic_path_is_routed_safe_and_the_fallback_catches_it(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, DYNAMIC)
    report = _report(client)
    route = _route(report)
    assert (route["execution"], route["reason"], route["confidence"]) == (
        "safe",
        "ordinary_plot",
        "low",
    )
    assert route["dynamic_paths"] >= 1 and "R4" in route["rules"]
    fake_pool["error"] = BLOCKED  # 真账本在 safe 里观察到读回
    final = _run(client, report)
    assert fake_pool["build_calls"] == 1 and len(native["calls"]) == 1
    assert [h["profile"] for h in final["attempt_history"]] == ["safe", "native"]


def test_safe_only_beats_both_the_static_route_and_the_previous_native_success(
    client, tmp_path, fake_pool, native, sessions
):
    root = _setup(client, tmp_path, READBACK)
    execpolicy.set_mode(root, execpolicy.MODE_SAFE_ONLY)
    report = _report(client)
    route = _route(report)
    assert (route["execution"], route["reason"]) == ("safe", "user_explicit_profile")
    fake_pool["error"] = BLOCKED
    final = _run(client, report)
    assert native["calls"] == [] and fake_pool["build_calls"] == 1
    assert final["outcome"]["code"] == "safe_file_operation_blocked"


def test_the_operator_kill_switch_stops_direct_native_too(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    monkeypatch.setenv(execpolicy.FALLBACK_ENV, "0")
    _setup(client, tmp_path, READBACK)
    route = _route(_report(client))
    assert (route["execution"], route["reason"]) == ("safe", "operator_disabled")


def test_a_native_success_is_recorded_but_not_reused_while_its_helper_inputs_were_never_observed(
    client, tmp_path, fake_pool, native, sessions
):
    """Codex #1034 r6：native 成功没报输入观察（这里的假 native 不报；`basis_observed: false`），与 #982 的 reuse_last_success 一样保守——
    不据此沿用 native，回到静态路由（动态路径 → safe + 回退）。记录本身仍写（#982 / 诊断读它）。"""
    root = _setup(client, tmp_path, DYNAMIC)
    first = _report(client)
    fake_pool["error"] = BLOCKED
    final = _run(client, first)
    assert [h["profile"] for h in final["attempt_history"]] == ["safe", "native"]
    record = runconfig.launch_record(root, "fig.py")
    assert (
        launchctx.profile_of(record) == "native" and record["version"] == launchctx.RECORD_VERSION
    )
    assert record["source"]["basis_observed"] is False

    again = _report(client)
    route = _route(again)
    assert (route["execution"], route["reason"]) == ("safe", "ordinary_plot")
    builds, natives = fake_pool["build_calls"], len(native["calls"])
    final2 = _run(client, again)
    assert fake_pool["build_calls"] == builds + 1 and len(native["calls"]) == natives + 1
    assert [h["profile"] for h in final2["attempt_history"][-2:]] == ["safe", "native"]


def test_a_native_record_whose_inputs_were_observed_is_reused(
    client, tmp_path, fake_pool, native, sessions
):
    root = _setup(client, tmp_path, DYNAMIC)
    fake_pool["error"] = BLOCKED
    _run(client, _report(client))
    record = runconfig.launch_record(root, "fig.py")
    observed = {**record, "source": {**record["source"], "basis_observed": True}}
    runconfig.set_launch_record(root, "fig.py", observed)
    fake_pool["error"] = None
    route = _route(_report(client))
    assert (route["execution"], route["reason"], route["confidence"]) == (
        "native",
        "previous_successful_profile",
        "high",
    )


def test_a_remembered_profile_is_reused_only_for_the_same_run_configuration(
    client, tmp_path, fake_pool, native, sessions
):
    """Codex #1034 r6：记录是对某个运行配置（argv 引用）的成功；请求的配置不同就不沿用，回到静态路由。"""
    root = _setup(client, tmp_path, READBACK)
    plain = _report(client)
    plan = prepsession.SESSIONS.session(plain["session_id"], plain["project_id"]).plan
    record = launchctx.build_native_record(
        root, "fig.py", dataclasses.replace(plan, execution="native"), run=None
    )
    assert record is not None and record["run_config"] is None
    runconfig.set_launch_record(root, "fig.py", {**record, "profile": "safe"})
    same = _route(_report(client))
    assert (same["execution"], same["reason"]) == ("safe", "previous_successful_profile")
    with_args = _create(client, {"script": "fig.py", "argv": ["--n", "3"]}).get_json()
    other = _route(with_args)
    assert other["reason"] == "savefig_readback" and other["execution"] == "native"
    # 记录里是有 argv 的配置、请求没有：同样不沿用
    run = runconfig.selection_for(root, "fig.py", ["--n", "3"], sensitive=False)
    runconfig.set_launch_record(
        root, "fig.py", {**record, "profile": "safe", "run_config": run.config_id}
    )
    assert _route(_report(client))["reason"] == "savefig_readback"


def test_editing_the_script_invalidates_the_remembered_profile(
    client, tmp_path, fake_pool, native, sessions
):
    root = _setup(client, tmp_path, DYNAMIC)
    fake_pool["error"] = BLOCKED
    _run(client, _report(client))
    assert launchctx.profile_of(runconfig.launch_record(root, "fig.py")) == "native"
    (root / "fig.py").write_text(DYNAMIC + "\n# edited\n", encoding="utf-8")
    route = _route(_report(client))
    assert (route["execution"], route["reason"]) == ("safe", "ordinary_plot")


def test_a_previous_safe_success_keeps_safe_even_when_the_static_rule_says_native(
    client, tmp_path, fake_pool, native, sessions
):
    root = _setup(client, tmp_path, READBACK)
    report = _report(client)
    assert _route(report)["execution"] == "native"
    # app 的成功收口按回执写下的 safe 记录：同一份脚本 safe 实跑完整成功过（这里用 native 记录的同形字段构造，profile 改 safe）
    plan = prepsession.SESSIONS.session(report["session_id"], report["project_id"]).plan
    record = launchctx.build_native_record(
        root, "fig.py", dataclasses.replace(plan, execution="native"), run=None
    )
    assert record is not None
    runconfig.set_launch_record(root, "fig.py", {**record, "profile": "safe"})
    route = _route(_report(client))
    assert (route["execution"], route["reason"]) == ("safe", "previous_successful_profile")


def test_no_interpreter_means_no_direct_native(client, tmp_path, fake_pool, native, sessions):
    _setup(client, tmp_path, READBACK)

    def boom(root=None, **kw):
        raise engine_pool.WorkerError("none", code="no_worker_python")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(engine_pool, "resolve_worker_python", boom)
        route = _route(_report(client))
    assert route["execution"] == "safe" and route["declined"] == "no_interpreter"
    assert route["reason"] == "savefig_readback"  # 原因与证据保留，如实说为什么没直达


def test_an_explicit_execution_and_asset_targets_are_not_routed(
    client, tmp_path, fake_pool, native, sessions
):
    root = _setup(client, tmp_path, READBACK)
    plan_native = preparation.plan_for(
        project_id="x",
        project_root=str(root),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    assert plan_native.execution == "native" and plan_native.route is None
    plan_safe = preparation.plan_for(
        project_id="x",
        project_root=str(root),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    assert plan_safe.execution == "safe" and plan_safe.route is None  # 默认值仍是显式 safe
    assert "route" not in plan_safe.to_payload()


def test_cli_and_mcp_entries_never_route(monkeypatch, tmp_path):
    """CLI / MCP 不 import `runroute`，且 `plan_for` 的默认（它们走的）永远不调路由。"""
    import ast

    src = Path(__file__).resolve().parent.parent / "src" / "tavotto"
    for rel in ("runcli.py", "mcp_server.py"):
        path = src / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert "runroute" not in imported and "runroute" not in names, rel

    def boom(*a, **kw):
        raise AssertionError("显式 safe 不许碰路由")

    monkeypatch.setattr(runroute, "route_for_file", boom)
    (tmp_path / "fig.py").write_text(READBACK, encoding="utf-8")
    plan = preparation.plan_for(
        project_id="x",
        project_root=str(tmp_path),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
    )
    assert plan.execution == "safe"


def test_the_route_reaches_the_local_diagnostics_as_closed_sets_only(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    report = _report(client)
    final = _run(client, report)
    attempt_id = final["attempt_history"][0]["attempt_id"]
    plan, result = preparation.SERVICE.get(attempt_id, final["project_id"])
    proj = preparation.diagnostic_projection(plan, result)
    assert proj["route"]["execution"] == "native"
    assert proj["route"]["reason"] == "savefig_readback"
    assert proj["route"]["rules"] == ["R1"] and proj["route"]["elapsed_ms"] > 0
    blob = json.dumps(proj)
    assert str(tmp_path) not in blob and "stage.png" not in blob and "Image" not in blob


def test_session_key_of_auto_is_prefixed_and_the_default_key_is_unchanged():
    base = {"kind": "script", "script": "fig.py"}
    assert prepsession.target_key(base) == "script:fig.py"
    assert prepsession.target_key({**base, "execution": "auto"}) == "auto|script:fig.py"
    assert prepsession.target_key({**base, "execution": "native"}).startswith("native|")


@pytest.mark.parametrize("old_version", [1])
def test_a_v1_record_is_still_readable_and_means_safe(old_version):
    assert launchctx.profile_of({"version": old_version}) == "safe"
    assert launchctx.profile_of({"version": 2, "profile": "native"}) == "native"
    assert launchctx.profile_of({"version": 2, "profile": "bogus"}) is None
    assert launchctx.profile_of(None) is None
    assert launchctx.verify(
        {"version": 3}, project_root=".", script="s.py", python=None, python_source=None
    ) == (launchctx.REASON_VERSION)


def _route_after_run(client, hdr=None):
    kw = {"headers": hdr} if hdr else {}
    report = client.post(SESSIONS, json={"script": "fig.py"}, **kw).get_json()
    assert report["plan"]["route"]["execution"] == "native"  # 检查时拿不到来源，路由只是预判
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
        **kw,
    )
    assert resp.status_code == 202, resp.get_json()
    return report, kw


def _settled_via(client, report, kw):
    import time

    deadline = time.time() + 10
    while True:
        final = client.get(f"{SESSIONS}/{report['session_id']}", **kw).get_json()
        if final["phase"] not in ("running", "awaiting_runtime_input"):
            break
        assert time.time() < deadline
        time.sleep(0.02)
    time.sleep(0.2)
    return final


def _assert_not_routed(final, native, fake_pool):
    assert native["calls"] == [], "非桌面来源不能被路由到 native"
    assert fake_pool["build_calls"] == 1
    assert [h["profile"] for h in final["attempt_history"]] == ["safe"]
    assert final["plan"]["route"]["declined"] == "not_desktop_initiated"
    assert final["plan"]["route"]["execution"] == "safe"


def test_nondesktop_plain_client_is_never_routed_to_native(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    m.app.config.pop("TAVOTTO_DESKTOP_MODE", None)
    report, kw = _route_after_run(client)
    _assert_not_routed(_settled_via(client, report, kw), native, fake_pool)


def test_nondesktop_browser_mode_is_never_routed_to_native(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    state = _mode(desktop=False, secret=SECRET)
    client.set_cookie(security.COOKIE_NAME, state.redeem(NONCE))
    report, kw = _route_after_run(client)
    _assert_not_routed(_settled_via(client, report, kw), native, fake_pool)


def test_nondesktop_api_client_mcp_style_is_never_routed_to_native(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    _mode(desktop=True, secret=SECRET)
    report, kw = _route_after_run(client, {AUTH_HEADER: SECRET})
    _assert_not_routed(_settled_via(client, report, kw), native, fake_pool)


def test_nondesktop_run_when_ready_is_never_routed_to_native(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    _mode(desktop=True, secret=SECRET)
    kw = {"headers": {AUTH_HEADER: SECRET}}
    report = client.post(SESSIONS, json={"script": "fig.py"}, **kw).get_json()
    arm = _action(report, "run_when_ready")
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": arm["id"],
            "expected_config_revision": report["config_revision"],
            "impact_digest": arm["impact"]["impact_digest"],
        },
        **kw,
    )
    assert resp.status_code in (200, 202), resp.get_json()
    _assert_not_routed(_settled_via(client, report, kw), native, fake_pool)


def test_nondesktop_legacy_preparation_endpoint_is_never_routed(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
    resp = client.post("/api/engine/preparation", json={"id": "fig.pdf"})
    assert resp.status_code == 202, resp.get_json()
    assert resp.get_json()["plan"].get("route") is None  # 旧端点走显式 safe，不经路由
    import time

    time.sleep(0.5)
    assert native["calls"] == []


def test_the_desktop_window_run_when_ready_is_routed_to_native(
    client, tmp_path, fake_pool, native, sessions
):
    _setup(client, tmp_path, READBACK)
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
    assert len(native["calls"]) == 1 and fake_pool["build_calls"] == 0
    assert [h["profile"] for h in final["attempt_history"]] == ["native"]


def test_an_explicit_safe_request_is_never_overridden_by_the_router(
    client, tmp_path, fake_pool, native, sessions
):
    """Codex #1034 r2 P1：请求里显式写了 `execution: "safe"` 就是 safe；只有省略才是 auto。"""
    _setup(client, tmp_path, READBACK)
    report = client.post(SESSIONS, json={"script": "fig.py", "execution": "safe"}).get_json()
    assert report["plan"].get("route") is None
    fake_pool["error"] = BLOCKED
    resp = client.post(
        f"{SESSIONS}/{report['session_id']}/actions",
        json={
            "action_id": _action(report, "run")["id"],
            "expected_config_revision": report["config_revision"],
        },
    )
    assert resp.status_code == 202, resp.get_json()
    final = _settle(client, report["session_id"])
    assert fake_pool["build_calls"] == 1
    assert final["attempt_history"][0]["profile"] == "safe"


def test_explicit_safe_and_auto_execution_are_different_sessions():
    """引擎层省略 = safe（会话键与 main 逐字节相同）；`auto` 由 HTTP 入口显式带进目标，与 safe / native 各有各的会话。"""
    base = {"kind": "script", "script": "fig.py"}
    assert prepsession.target_key({**base, "execution": "safe"}) == prepsession.target_key(base)
    auto = prepsession.target_key({**base, "execution": "auto"})
    assert auto != prepsession.target_key(base)
    assert auto != prepsession.target_key({**base, "execution": "native"})


def test_an_omitted_execution_in_the_request_reaches_the_router_as_auto(client, tmp_path):
    _setup(client, tmp_path, READBACK)
    report = client.post(SESSIONS, json={"script": "fig.py"}).get_json()
    assert report["plan"].get("route") is not None


def _claim_with_hook(client, tmp_path, fake_pool, native, hook, monkeypatch):
    """桌面窗口点运行；`hook(plan)` 在计划登记之后、交给执行线程之前被调用（模拟那一刻的竞态）。"""
    _setup(client, tmp_path, READBACK)
    report = _report(client)
    assert _route(report)["execution"] == "native"
    real = preparation.PreparationService.register
    fired: list[int] = []

    def register(self, plan, **kw):
        out = real(self, plan, **kw)
        if plan.execution == "native" and not fired:
            fired.append(1)
            hook(plan, report)
        return out

    # 类级补丁：对实例打补丁撤销时会留下遮住类属性的实例属性，污染别的用例的类级补丁
    monkeypatch.setattr(preparation.PreparationService, "register", register)
    resp = _act(
        client, report["session_id"], _action(report, "run")["id"], report["config_revision"]
    )
    return report, resp


def test_a_routed_native_run_superseded_by_a_newer_same_script_plan_never_launches(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    from test_execution_fallback import _register_newer_plan

    report, resp = _claim_with_hook(
        client,
        tmp_path,
        fake_pool,
        native,
        lambda plan, rep: _register_newer_plan({**rep, "provider": {"attempt_id": plan.plan_id}}),
        monkeypatch,
    )
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["params"]["reason"] == "superseded"
    assert native["calls"] == [] and fake_pool["build_calls"] == 0
    # 没有留下孤儿尝试：会话里没有任何执行历史
    assert _get(client, report["session_id"]).get_json()["attempt_history"] == []


def test_a_routed_native_run_begun_before_the_project_was_reopened_never_launches(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    registry = m.engine_nativesession.REGISTRY

    def reopen(plan, rep):
        root = plan.project_root
        registry.mark_project_closed(root)
        registry.reopen_project(root)

    report, resp = _claim_with_hook(client, tmp_path, fake_pool, native, reopen, monkeypatch)
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["params"]["reason"] == "project_reopened"
    assert native["calls"] == [] and fake_pool["build_calls"] == 0


@pytest.fixture(autouse=True)
def _no_instance_level_patches_leak():
    """本文件的竞态钩子必须打在类上：留在 `PreparationService` 实例上的补丁会遮住后面用例的类级补丁（PR1 的代次用例因此假红过）。"""
    yield
    assert "register" not in vars(preparation.SERVICE)


def test_a_script_rewritten_after_the_route_was_read_is_not_routed_native(
    client, tmp_path, fake_pool, native, sessions, monkeypatch
):
    """#1034 P1：路由读的是旧内容、revision 算的是新内容——native 的依据不能嫁接到新脚本上。"""
    root = _setup(client, tmp_path, READBACK)
    real_decide = runroute.decide

    def rewrite_after_analysis(*a, **kw):
        route = real_decide(*a, **kw)
        (root / "fig.py").write_text("import os\nos.remove('victim.txt')\n", encoding="utf-8")
        return route

    monkeypatch.setattr(runroute, "decide", rewrite_after_analysis)
    route = _route(_report(client))
    assert route["execution"] == "safe" and route["declined"] == "script_changed", route
