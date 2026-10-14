"""桌面拥有的运行遇到活图路由冲突(Codex #1022 P1):不许报成功,也不许发布被拒的描述符。"""

from __future__ import annotations

import pytest

from support import desktopkit as dk, nativekit
from tavotto import app as m
from tavotto.engine import nativesession, pool, preparation, runcodes

pytestmark = nativekit.needs_user_python


@pytest.fixture(autouse=True)
def _reap_owned():
    """用例红了也不许留下活着的脚本进程(conftest 的收尾只放手、不结束)。"""
    yield
    for s in nativesession.REGISTRY.list():
        if s.owned is not None and not s.owned.finished:
            try:
                s.cancel_owned()
            except Exception:  # noqa: BLE001
                pass


SCRIPT = dk.FIGURE_HEADER + "fig, ax = plt.subplots()\nax.plot([0, 1], [0, 1])\n"


def test_route_conflict_fails_the_run_and_ends_the_new_process(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")

    # 另一条还活着的会话(这里用已到屏障的桌面会话扮演「终端里的那一条」)占着同一个 (项目, 脚本, stem)
    spec_launch = dk.launch_for(tmp_path, SCRIPT, name="fig.py")
    holder = dk.desktopproc.start(spec_launch)
    nativekit.wait_state(holder, [nativesession.BARRIER])
    holder.ensure_built()
    assert nativesession.REGISTRY.bind_assets(holder) == []
    holder.spec = None
    nativesession.REGISTRY._starting.clear()  # noqa: SLF001 — 终端拥有的会话没有启动票据:别让它被重跑的取消带走  # 不是本脚本的桌面运行:重新运行时的「旧图让位」不会收它,冲突必须由路由判

    plan = preparation.plan_for(
        project_id="p",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    # 两条会话的路由键要对上:holder 的项目根是 tmp_path/proj,脚本相对路径同为 fig.py
    with pytest.raises(pool.WorkerError) as err:
        m._native_preparation_runner(plan)
    assert err.value.code == runcodes.NATIVE_ASSET_CONFLICT
    new = [s for s in nativesession.REGISTRY.list(str(proj)) if s is not holder]
    assert len(new) == 1
    dk.wait_until(lambda: new[0].owned.finished, what="新进程被结束")
    # 旧图仍是该路由的持有者,没有被抢走
    stem = holder.stems[0]
    assert nativesession.REGISTRY.route_for(str(proj), "fig.py", stem) is holder
    holder.cancel_owned()


def _holder_and_second(tmp_path):
    """holder 占着 (项目, fig.py, stem) 的活路由;second 是同脚本的另一条桌面会话,已到屏障、还没绑路由。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    holder = dk.desktopproc.start(dk.launch_for(tmp_path, SCRIPT, name="fig.py", launch_id="h1"))
    nativekit.wait_state(holder, [nativesession.BARRIER])
    holder.ensure_built()
    assert nativesession.REGISTRY.bind_assets(holder) == []
    holder.spec = None
    nativesession.REGISTRY._starting.clear()  # noqa: SLF001 — 终端拥有的会话没有启动票据:别让它被重跑的取消带走  # 不是本脚本的桌面运行:重新运行时的「旧图让位」不会收它
    second = dk.desktopproc.start(dk.launch_for(tmp_path, SCRIPT, name="fig.py", launch_id="s1"))
    nativekit.wait_state(second, [nativesession.BARRIER])
    return proj, holder, second


def test_ui_build_endpoint_never_publishes_a_route_conflict(tmp_path, monkeypatch):
    """SSE 触发的 `/build` 与准备 runner 是两条发布路径:冲突的 stem 在 build 端点上也不许物化 / 回给前端
    (否则预览是这次运行的、编辑与导出却仍落在旧会话上;Codex #1022 P1)。"""
    from tavotto import app as m
    from tavotto.engine import runtimeasset

    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    proj, holder, second = _holder_and_second(tmp_path)
    m.app.config["TESTING"] = True
    resp = m.app.test_client().post(f"/api/native/sessions/{second.session_id}/build")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    stem = holder.stems[0]
    assert body["conflicts"]["stems"] == [stem]
    assert [d["stem"] for d in body["descriptors"]] == [], "被拒的描述符不应回给前端去放进画布"
    asset = f"runtime:fig.py#{stem}"
    assert runtimeasset.load_metadata(str(proj), asset) is None, "被拒的预览不应物化进缓存"
    assert nativesession.REGISTRY.route_for(str(proj), "fig.py", stem) is holder


def test_a_build_that_captures_no_figure_ends_the_process(tmp_path):
    """脚本跑完却没有图:进程停在 script_end 屏障上,不收的话它一直活着占着环境租约(Codex #1022 P2)。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    # 没有任何 Figure、却走到 plt.show() 的屏障:build 得到空 stem 表,进程还活在屏障上
    (proj / "fig.py").write_text(dk.FIGURE_HEADER + "plt.show()\n", encoding="utf-8")
    plan = preparation.plan_for(
        project_id="p",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    session, resp, _ = m._native_preparation_runner(plan)
    assert not resp.get("stems")
    dk.wait_until(lambda: session.owned.finished, what="无图的进程被收掉")
    assert not dk.holds_lease(session)


def test_closing_a_project_cancels_a_launch_that_has_not_registered_yet(tmp_path, monkeypatch):
    """启动在 `begin_start` 与 `adopt` 之间时关项目:在途登记也要被取消并结束进程(Codex #1022 P2)。"""
    import threading

    proj = tmp_path / "proj"
    proj.mkdir()
    gate = threading.Event()
    reached = threading.Event()
    real_adopt = nativesession.REGISTRY.adopt

    def slow_adopt(session):
        reached.set()
        gate.wait(30)
        real_adopt(session)

    # 补丁打在实例上(与 test_desktop_owned_process 同写法):别的用例会在实例上留下 adopt 属性,类上的补丁会被它遮住
    monkeypatch.setattr(nativesession.REGISTRY, "adopt", slow_adopt)
    launch = dk.launch_for(tmp_path, "import time\ntime.sleep(600)\n", name="fig.py")
    box = {}

    def go():
        try:
            box["session"] = dk.desktopproc.start(launch)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert reached.wait(60), f"启动没走到登记前: {box.get('error')!r}"
    nativesession.REGISTRY.cancel_project(str(proj))  # 项目关闭
    gate.set()
    t.join(60)
    owned = (
        box["session"].owned
        if "session" in box
        else next(iter(nativesession.REGISTRY._starting.values()), None)  # noqa: SLF001
    )
    if owned is not None:
        dk.wait_until(lambda: owned.finished, timeout=30, what="关项目后在途进程被结束")
    live = [s for s in nativesession.REGISTRY.list(str(proj)) if s.owned and not s.owned.finished]
    assert live == []


def test_closing_a_project_blocks_a_launch_that_has_not_reserved_yet(tmp_path, monkeypatch):
    """准备线程在 `reserve` 之前被挂住时关项目:此时既没有会话也没有票据,但关闭之后它再往下走也不许 spawn
    (Codex #1022 r4267320690;顺带堵上 `reserve` 到 `begin_start` 之间的窗口)。"""
    import threading

    proj = tmp_path / "proj"
    proj.mkdir()
    gate = threading.Event()
    reached = threading.Event()
    real_reserve = nativesession.REGISTRY.reserve

    def slow_reserve(launch_id, project_root=""):
        reached.set()
        gate.wait(30)
        return real_reserve(launch_id, project_root)

    monkeypatch.setattr(nativesession.REGISTRY, "reserve", slow_reserve)
    launch = dk.launch_for(tmp_path, "open('ran.txt','w').write('x')\n", name="fig.py")
    box = {}

    def go():
        try:
            box["session"] = dk.desktopproc.start(launch)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert reached.wait(60)
    nativesession.REGISTRY.close_project(str(proj))
    gate.set()
    t.join(60)
    assert "session" not in box, "项目已关闭,启动不许再进行"
    assert getattr(box.get("error"), "code", "") == runcodes.NATIVE_REGISTRY_CLOSING
    assert not (tmp_path / "work" / "ran.txt").exists(), "关项目之后不应跑任何用户代码"
    # 重新打开项目之后可以再启动
    nativesession.REGISTRY.reopen_project(str(proj))
    session = dk.desktopproc.start(dk.launch_for(tmp_path, "pass\n", name="fig.py"))
    session.cancel_owned()


def test_preparation_service_cancels_every_plan_of_a_closed_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    plan = preparation.plan_for(
        project_id="pj-closed",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    preparation.SERVICE.register(plan)
    other = preparation.SERVICE.register(
        preparation.plan_for(
            project_id="pj-other",
            project_root=str(proj),
            asset_id="",
            stem="",
            script="fig.py",
            entry="__main__",
            original_artifact=None,
            target=preparation.TARGET_SCRIPT,
            execution="native",
        )
    )
    assert preparation.SERVICE.cancel_project("pj-closed") == 1
    _, result = preparation.SERVICE.get(plan.plan_id, "pj-closed")
    assert result.status == preparation.STATUS_CANCELLED
    assert other.status != preparation.STATUS_CANCELLED  # 别的项目的计划不受影响


def test_registration_failure_ends_the_native_session(tmp_path, monkeypatch):
    """准备跑完、登记被拒(`multiple_stem_conflict` 等)时,桌面拥有的进程不能留在屏障上占着租约与路由
    (Codex #1022 r4267320696)。"""
    import types

    from tavotto.engine import probe

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    session = dk.desktopproc.start(
        dk.launch_for(tmp_path, SCRIPT, name="fig.py", launch_id="plan-reg-fail")
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    resp = session.ensure_built()
    monkeypatch.setattr(
        probe,
        "register_probed",
        lambda *a, **k: {"registered": False, "error": {"code": "multiple_stem_conflict"}},
    )
    from tavotto import app as m

    ctx = types.SimpleNamespace(path=proj)
    plan = types.SimpleNamespace(
        script="fig.py", entry="__main__", run=None, execution="native", plan_id="plan-reg-fail"
    )
    captured = {"stems": list(resp["stems"]), "descriptors": resp["descriptors"]}
    out = m._finalize_script_attempt(ctx, plan, types.SimpleNamespace(captured=captured))
    assert out["registered"] is False
    dk.wait_until(lambda: session.owned.finished, what="登记失败后进程被结束")
    assert not dk.holds_lease(session)


def test_app_close_project_cancels_preparations_and_marks_the_project_closed(tmp_path):
    """`app.close_project` 的接线:该项目的准备被取消、注册表标记关闭;重新 `open_project` 后标记清掉。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    info = m.open_project(str(proj), make_default=False)
    pid = info["id"]
    plan = preparation.plan_for(
        project_id=pid,
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )
    preparation.SERVICE.register(plan)
    assert m.close_project(pid) is True
    _, result = preparation.SERVICE.get(plan.plan_id, pid)
    assert result.status == preparation.STATUS_CANCELLED
    with pytest.raises(runcodes.RunError) as err:
        nativesession.REGISTRY.reserve("after-close", str(proj))
    assert err.value.code == runcodes.NATIVE_REGISTRY_CLOSING
    m.open_project(str(proj), make_default=False)
    nativesession.REGISTRY.reserve("after-reopen", str(proj))
    nativesession.REGISTRY.unreserve("after-reopen")
    m.close_project(pid)


def _native_plan(proj, project_id):
    return preparation.plan_for(
        project_id=project_id,
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        execution="native",
    )


def test_rerun_cancels_an_in_flight_launch_of_the_same_script(tmp_path, monkeypatch):
    """同一脚本的重新运行 = 取消该脚本上还在启动的那一条(票据),不只是已登记的会话(Codex #1022 r4267467883)。"""
    import threading

    proj = tmp_path / "proj"
    proj.mkdir()
    gate, reached = threading.Event(), threading.Event()
    real_adopt = nativesession.REGISTRY.adopt

    def slow_adopt(session):
        reached.set()
        gate.wait(30)
        real_adopt(session)

    monkeypatch.setattr(nativesession.REGISTRY, "adopt", slow_adopt)
    launch = dk.launch_for(tmp_path, "import time\ntime.sleep(600)\n", name="fig.py")
    box = {}

    def go():
        try:
            box["session"] = dk.desktopproc.start(launch)
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert reached.wait(60)
    # 别的脚本的重跑不碰它
    assert nativesession.REGISTRY.cancel_project(str(proj), script="other.py") == 0
    assert nativesession.REGISTRY.cancel_project(str(proj), script="fig.py") == 1
    gate.set()
    t.join(60)
    owned = box["session"].owned
    dk.wait_until(lambda: owned.finished, timeout=30, what="重跑取消了在途启动")


def test_preparation_service_rerun_cancels_older_plans_of_the_same_script(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    old, new = _native_plan(proj, "pj-r"), _native_plan(proj, "pj-r")
    preparation.SERVICE.register(old)
    preparation.SERVICE.register(new)
    assert preparation.SERVICE.cancel_project("pj-r", script="fig.py", keep=new.plan_id) == 1
    assert preparation.SERVICE.get(old.plan_id, "pj-r")[1].status == preparation.STATUS_CANCELLED
    assert preparation.SERVICE.get(new.plan_id, "pj-r")[1].status != preparation.STATUS_CANCELLED


def test_close_project_cancels_and_marks_before_the_blocking_pool_shutdown(tmp_path, monkeypatch):
    """`close_project(wait=True)` 里 `pool.shutdown_all` 可能阻塞数秒:此时准备已被取消、项目已标记关闭
    (Codex #1022 r4267467886)。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    pid = m.open_project(str(proj), make_default=False)["id"]
    plan = _native_plan(proj, pid)
    preparation.SERVICE.register(plan)
    seen = {}

    def fake_shutdown(*a, **k):
        seen["plan"] = preparation.SERVICE.get(plan.plan_id, pid)[1].status
        try:
            nativesession.REGISTRY.reserve("during-shutdown", str(proj))
            seen["closed"] = False
        except runcodes.RunError:
            seen["closed"] = True

    monkeypatch.setattr(m.engine_pool, "shutdown_all", fake_shutdown)
    m.close_project(pid, wait=True)
    assert seen == {"plan": preparation.STATUS_CANCELLED, "closed": True}


def test_native_runner_supersedes_older_plans_of_the_same_script(tmp_path):
    """runner 的重跑入口:先取消同脚本更早的准备计划,再处理票据 / 会话(与关项目同一个顺序)。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    old, new = _native_plan(proj, "pj-super"), _native_plan(proj, "pj-super")
    preparation.SERVICE.register(old)
    session, _resp, _ = m._native_preparation_runner(new)
    try:
        assert preparation.SERVICE.get(old.plan_id, "pj-super")[1].status == (
            preparation.STATUS_CANCELLED
        )
    finally:
        session.cancel_owned()
