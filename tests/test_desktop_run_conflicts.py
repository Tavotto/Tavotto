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
    holder.spec = None  # 不是本脚本的桌面运行:重新运行时的「旧图让位」不会收它,冲突必须由路由判

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
    holder.spec = None  # 不是本脚本的桌面运行:重新运行时的「旧图让位」不会收它
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
