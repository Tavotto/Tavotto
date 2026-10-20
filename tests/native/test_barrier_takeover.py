"""#1202 Codex P1：沿用屏障观察的新计划原子接管旧运行（`NativeSession.seize_for_takeover`）。

核对与接管在同一把锁里，放行（继续 / 放手）与断线推进放行代数也在这把锁里：接管成功之后旧运行不会再被放行，
脚本不会在「核对通过、取消之前」的空档里接着往下读别的输入。真 spawn、真 Bridge Runner（`desktopproc.start`）。
"""

from __future__ import annotations

import types

import pytest

from support import desktopkit as dk, nativekit
from tavotto.engine import nativesession, preparation, runcodes

pytestmark = nativekit.needs_user_python

LAUNCH = "plan-takeover"

#: 屏障之后做一件可观察的事：在 cwd（`tmp_path/work`）里留一个标记文件
SCRIPT = (
    dk.FIGURE_HEADER
    + """\
from pathlib import Path
plt.plot([0, 1])
plt.show()
Path("released.txt").write_text("x")
"""
)


@pytest.fixture(autouse=True)
def _reap_owned():
    yield
    for s in nativesession.REGISTRY.list():
        if s.owned is not None and not s.owned.finished:
            try:
                s.cancel_owned()
            except Exception:  # noqa: BLE001
                pass


def _parked(tmp_path):
    session = dk.start(tmp_path, SCRIPT, launch_id=LAUNCH)
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    epoch = nativesession.REGISTRY.barrier_hold_epoch(LAUNCH)
    assert epoch is not None
    return session, epoch, tmp_path / "work" / "released.txt"


def test_a_seized_session_refuses_to_be_released_and_the_script_stays_parked(tmp_path):
    session, epoch, marker = _parked(tmp_path)
    assert nativesession.REGISTRY.seize_for_takeover(LAUNCH, epoch) is True
    assert nativesession.REGISTRY.barrier_hold_epoch(LAUNCH) is None, "被接管的不再给别的计划作保"
    for release in (session.resume, session.detach):
        with pytest.raises(runcodes.RunError) as exc:
            release()
        assert exc.value.code == runcodes.NATIVE_SESSION_ENDED
    assert session.state == nativesession.BARRIER
    assert not marker.exists(), "屏障之后的代码一行都没跑"
    session.cancel_owned()
    assert not marker.exists()


def test_a_session_released_first_cannot_be_seized(tmp_path):
    session, epoch, marker = _parked(tmp_path)
    session.resume()
    assert nativesession.REGISTRY.seize_for_takeover(LAUNCH, epoch) is False
    dk.finish(session)
    assert marker.exists()


def test_a_stale_epoch_cannot_seize(tmp_path):
    session, epoch, _marker = _parked(tmp_path)
    assert session.seize_for_takeover(epoch + 1) is False
    assert session.seize_for_takeover(epoch - 1) is False
    assert session.seize_for_takeover(epoch) is True, "代数对得上才接管；失败的尝试不留痕"
    assert session.seize_for_takeover(epoch) is False, "只能被接管一次"
    session.cancel_owned()


def test_a_lost_connection_cannot_be_seized(tmp_path):
    session, epoch, _marker = _parked(tmp_path)
    session.transport.close()
    dk.wait_until(lambda: session.state in nativesession.TERMINAL_STATES, what="会话进终态")
    assert nativesession.REGISTRY.seize_for_takeover(LAUNCH, epoch) is False
    session.owned.wait(30)


def test_an_unknown_launch_cannot_be_seized():
    assert nativesession.REGISTRY.seize_for_takeover("no-such-plan", 0) is False
    assert nativesession.REGISTRY.seize_for_takeover("", 0) is False


def _plan_for(session, *, epoch):
    """runner 只读这几个字段；其余走到接管就停，不需要整份计划。"""
    return types.SimpleNamespace(
        native_spec=session.spec,
        barrier_hold=LAUNCH,
        barrier_hold_epoch=epoch,
        project_id="pj",
        project_root=session.project_root,
        script="fig.py",
        plan_id="plan-new",
        launch_order=10**9,
        project_epoch=None,
    )


def test_the_runner_ends_stale_without_running_anything_when_the_epoch_moved(tmp_path):
    """计划记下的代数与会话此刻的不同（核对之后、接管之前被放行过）：runner 不起新会话、不取消旧的，以过期收场。"""
    from tavotto import app as appmod

    session, epoch, marker = _parked(tmp_path)
    session.resume()  # 核对通过之后放行
    with pytest.raises(preparation.StaleBeforeRun) as stale:
        appmod._native_preparation_runner(_plan_for(session, epoch=epoch))
    assert stale.value.reason == preparation.STALE_DATA_BINDING
    assert stale.value.executed is False
    dk.finish(session)
    assert marker.exists()  # 放行的是旧运行自己；新会话没起
    assert len(nativesession.REGISTRY.list()) == 1


def test_the_runner_seizes_before_it_cancels_the_old_session(tmp_path, monkeypatch):
    """接管先于取消：取消发生时旧会话已被接管，再也放行不了；脚本的后半段始终没执行。"""
    from tavotto import app as appmod

    session, epoch, marker = _parked(tmp_path)
    session.launch_order = 1  # 比新计划（10**9）早登记：让位名单里有它
    seen = {}
    real_cancel = nativesession.REGISTRY.cancel_project

    def cancel_after_seize(*a, **kw):
        seen["seized"] = session._seized
        return real_cancel(*a, **kw)

    monkeypatch.setattr(nativesession.REGISTRY, "cancel_project", cancel_after_seize)

    class _Stop(Exception):
        pass

    def _no_start(_launch):
        raise _Stop

    monkeypatch.setattr(appmod.engine_desktopproc, "start", _no_start)
    with pytest.raises(_Stop):
        appmod._native_preparation_runner(_plan_for(session, epoch=epoch))
    assert seen == {"seized": True}
    assert session.owned.finished or session.owned.wait(30)
    assert not marker.exists()
