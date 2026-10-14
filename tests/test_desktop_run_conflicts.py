"""桌面拥有的运行遇到活图路由冲突(Codex #1022 P1):不许报成功,也不许发布被拒的描述符。"""

from __future__ import annotations

import dataclasses

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

    def slow_reserve(launch_id, project_root="", epoch=None, cancelled=None):
        reached.set()
        gate.wait(30)
        return real_reserve(launch_id, project_root, epoch, cancelled)

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
    preparation.SERVICE.register(new)  # 真实路径里 runner 跑的计划一定已登记（登记顺序 = 先后）
    session, _resp, _ = m._native_preparation_runner(new)
    try:
        assert preparation.SERVICE.get(old.plan_id, "pj-super")[1].status == (
            preparation.STATUS_CANCELLED
        )
    finally:
        session.cancel_owned()


def test_rerun_only_cancels_older_plans_never_newer_ones(tmp_path):
    """两个窗口几乎同时重跑同一脚本:各自只取消**比自己早**登记的计划,不互相取消(Codex #1022 r4267606089)。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    first, second = _native_plan(proj, "pj-age"), _native_plan(proj, "pj-age")
    preparation.SERVICE.register(first)
    preparation.SERVICE.register(second)
    # 先登记的那个重跑:后登记的更新,不能被取消
    assert preparation.SERVICE.cancel_project("pj-age", script="fig.py", keep=first.plan_id) == 0
    assert preparation.SERVICE.get(second.plan_id, "pj-age")[1].status != (
        preparation.STATUS_CANCELLED
    )
    assert preparation.SERVICE.get(first.plan_id, "pj-age")[1].status != (
        preparation.STATUS_CANCELLED
    )
    # 后登记的那个重跑:更早的被取消
    assert preparation.SERVICE.cancel_project("pj-age", script="fig.py", keep=second.plan_id) == 1
    assert preparation.SERVICE.get(first.plan_id, "pj-age")[1].status == (
        preparation.STATUS_CANCELLED
    )


def test_partial_route_conflict_rejects_the_whole_build_and_binds_nothing(tmp_path, monkeypatch):
    """多图时只有一张冲突:`/build` 与 runner 一样「冲突即整体失败」——不物化、不回任何描述符、也不绑任何路由
    (Codex #1022 r4267749664)。"""
    from tavotto import app as m
    from tavotto.engine import runtimeasset

    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    holder = dk.desktopproc.start(dk.launch_for(tmp_path, SCRIPT, name="fig.py", launch_id="h1"))
    nativekit.wait_state(holder, [nativesession.BARRIER])
    holder.ensure_built()
    assert nativesession.REGISTRY.bind_assets(holder) == []  # 占着 `fig`
    holder.spec = None
    nativesession.REGISTRY._starting.clear()  # noqa: SLF001
    two = SCRIPT + "fig2, ax2 = plt.subplots()\n"  # stems: fig + fig-2
    second = dk.desktopproc.start(dk.launch_for(tmp_path, two, name="fig.py", launch_id="s1"))
    nativekit.wait_state(second, [nativesession.BARRIER])
    m.app.config["TESTING"] = True
    body = m.app.test_client().post(f"/api/native/sessions/{second.session_id}/build").get_json()
    assert body["conflicts"]["stems"] == ["fig"]
    assert body["descriptors"] == [] and body["stems"] == {}
    assert runtimeasset.load_metadata(str(proj), "runtime:fig.py#fig-2") is None
    assert nativesession.REGISTRY.route_for(str(proj), "fig.py", "fig-2") is None
    assert nativesession.REGISTRY.route_for(str(proj), "fig.py", "fig") is holder


def test_a_reopened_project_is_not_blocked_by_the_old_marker_but_old_plans_stay_blocked(
    tmp_path,
):
    """关→开→新准备成功;而关闭前就登记的旧计划即使项目已重新打开也不能起进程(代次判定,Codex #1022 第九轮)。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    old = _native_plan(proj, "pj-gen")
    preparation.SERVICE.register(old)
    old_plan = preparation.SERVICE._entry(old.plan_id).plan  # noqa: SLF001 — runner 拿到的是登记后的计划
    nativesession.REGISTRY.close_project(str(proj))  # 关
    nativesession.REGISTRY.reopen_project(str(proj))  # 又很快打开
    with pytest.raises(pool.WorkerError) as err:
        m._native_preparation_runner(old_plan)  # 旧线程此刻才走到起进程这一步
    assert err.value.code == runcodes.NATIVE_REGISTRY_CLOSING
    assert nativesession.REGISTRY.list(str(proj)) == [], "旧计划不应起出进程"
    # 重新打开之后的新准备:正常
    fresh = _native_plan(proj, "pj-gen")
    preparation.SERVICE.register(fresh)
    session, _resp, _ = m._native_preparation_runner(preparation.SERVICE._entry(fresh.plan_id).plan)  # noqa: SLF001
    session.cancel_owned()


def test_an_older_rerun_never_stops_a_newer_launch_of_the_same_script(tmp_path):
    """两个重跑交错:较新的计划先起了会话,较老的(已被标取消的)线程随后才走到 runner——它只能停比自己更早的启动,
    不能把较新的那条杀掉(Codex #1022 第十轮)。较老的那条自己则被 `note_owner` 的取消收掉。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SCRIPT, encoding="utf-8")
    older, newer = _native_plan(proj, "pj-order"), _native_plan(proj, "pj-order")
    preparation.SERVICE.register(older)
    preparation.SERVICE.register(newer)
    plan_older = preparation.SERVICE._entry(older.plan_id).plan  # noqa: SLF001
    plan_newer = preparation.SERVICE._entry(newer.plan_id).plan  # noqa: SLF001
    assert plan_older.launch_order < plan_newer.launch_order
    session_newer, _resp, _ = m._native_preparation_runner(plan_newer)  # 较新的先走完 runner
    try:
        with pytest.raises(pool.WorkerError):  # 较老的随后才到:自己因计划已被取消而收场
            m._native_preparation_runner(plan_older)
        assert not session_newer.owned.finished, "较老的重跑把较新的启动杀了"
        assert session_newer.state == nativesession.BARRIER
    finally:
        session_newer.cancel_owned()


def test_registry_rerun_cancel_follows_launch_order_for_sessions(tmp_path):
    """会话带着计划序号:`older_than=N` 只停序号 < N 的;序号未知的(终端拥有的 / 不经计划的)不碰。"""
    import dataclasses

    proj = tmp_path / "proj"
    proj.mkdir()
    base = dk.launch_for(tmp_path, SCRIPT, name="fig.py", launch_id="ord-5")
    session = dk.desktopproc.start(dataclasses.replace(base, launch_order=5))
    nativekit.wait_state(session, [nativesession.BARRIER])
    nativesession.REGISTRY._starting.clear()  # noqa: SLF001 — 只量会话自己带的序号（票据的序号另有用例）
    try:
        assert nativesession.REGISTRY.cancel_project(str(proj), script="fig.py", older_than=5) == 0
        assert nativesession.REGISTRY.cancel_project(str(proj), script="fig.py", older_than=3) == 0
        assert not session.owned.finished
        assert nativesession.REGISTRY.cancel_project(str(proj), script="fig.py", older_than=6) == 1
        assert session.owned.finished
    finally:
        if not session.owned.finished:
            session.cancel_owned()


def _spawn_witness(tmp_path):
    return tmp_path / "work" / "spawned.txt"


def test_a_cancelled_plan_never_spawns(tmp_path):
    """计划在走到起进程之前就已被取消 / 取代:一行用户代码都不跑（Codex #1022 第十一轮）。"""
    from tavotto import app as m

    proj = tmp_path / "proj"
    proj.mkdir()
    script = "open('spawned.txt', 'w').write('x')\n"
    (proj / "fig.py").write_text(script, encoding="utf-8")
    older, newer = _native_plan(proj, "pj-pre"), _native_plan(proj, "pj-pre")
    preparation.SERVICE.register(older)
    preparation.SERVICE.register(newer)
    plan_older = preparation.SERVICE._entry(older.plan_id).plan  # noqa: SLF001
    preparation.SERVICE.cancel_project(
        "pj-pre", script="fig.py", keep=newer.plan_id
    )  # 较新的取代了它
    # 走真实 runner,但它的 spec 必须指向 tmp 下的 work 目录以便观测:用 dk 的 cwd 约定重建启动身份
    launch = dk.launch_for(tmp_path, script, name="fig.py")
    plan_older = dataclasses.replace(plan_older, native_spec=launch.spec)
    with pytest.raises(pool.WorkerError):
        m._native_preparation_runner(plan_older)
    assert not _spawn_witness(tmp_path).exists(), "已取消的计划仍然起了进程、跑了用户代码"
    assert nativesession.REGISTRY.list(str(proj)) == []


@pytest.mark.parametrize("where", ["begin_start", "check_start"])
def test_cancellation_between_the_checkpoints_and_the_spawn_is_still_caught(
    tmp_path, monkeypatch, where
):
    """取消恰好落在 `reserve` 之后 / `Popen` 之前的两个窗口：都在注册表的同一处判定里被拦住，没有进程。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    script = "open('spawned.txt', 'w').write('x')\n"
    plan = _native_plan(proj, "pj-mid")
    preparation.SERVICE.register(plan)
    plan = preparation.SERVICE._entry(plan.plan_id).plan  # noqa: SLF001
    launch = dk.launch_for(tmp_path, script, name="fig.py", launch_id=plan.plan_id)
    launch = dataclasses.replace(
        launch,
        project_epoch=plan.project_epoch,
        launch_order=plan.launch_order,
        cancelled=lambda: preparation.SERVICE.is_cancelled(plan.plan_id),
    )
    real = getattr(nativesession.REGISTRY, where)

    def cancel_then(*a, **k):
        # 取消发生在这一步「之前」: 随后这一步（或它后面的 Popen 前检查）必须看到
        preparation.SERVICE.cancel(plan.plan_id, "pj-mid")
        return real(*a, **k)

    monkeypatch.setattr(nativesession.REGISTRY, where, cancel_then)
    with pytest.raises(runcodes.RunError) as err:
        dk.desktopproc.start(launch)
    assert err.value.code == runcodes.NATIVE_REGISTRY_CLOSING
    assert not _spawn_witness(tmp_path).exists()
    assert nativesession.REGISTRY.list(str(proj)) == []
