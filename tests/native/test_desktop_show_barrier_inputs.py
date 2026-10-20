"""#1047 修订：桌面拥有的运行停在 `show()` 屏障上时，build 带回**到此为止**的输入观察。

桌面界面对桌面拥有的运行只给「停止运行」，停在屏障上之后只会被结束或重跑——屏障之后的代码不执行，这一刻读过的就是这次
运行的全部输入，成功收口据此记 `basis_observed: true`。真被放行时观察作废，脚本跑完后在结束屏障上的 build 再给全部。
真 spawn、真 Bridge Runner（`desktopproc.start`，命令行带 `--observe-inputs`）；记录与路由的端到端在 `test_run_routing_e2e.py`。
"""

from __future__ import annotations

import pytest

from support import desktopkit as dk, nativekit
from tavotto.engine import nativesession

pytestmark = nativekit.needs_user_python


@pytest.fixture(autouse=True)
def _reap_owned():
    yield
    for s in nativesession.REGISTRY.list():
        if s.owned is not None and not s.owned.finished:
            try:
                s.cancel_owned()
            except Exception:  # noqa: BLE001
                pass


SCRIPT = (
    dk.FIGURE_HEADER
    + """\
from pathlib import Path
import helper
here = Path(__file__).resolve().parent
before = float((here / "before.txt").read_text())
plt.plot([0, before * helper.SCALE])
plt.show()
after = float((here / "after.txt").read_text())
"""
)


def _files(inputs) -> set[str]:
    return {f["path"] for f in inputs["files"]}


def _project(tmp_path, script=SCRIPT):
    proj = tmp_path / "proj"
    nativekit.write(proj / "helper.py", "SCALE = 2.0\n")
    nativekit.write(proj / "before.txt", "1\n")
    nativekit.write(proj / "after.txt", "3\n")
    return dk.start(tmp_path, script)


def test_the_show_barrier_build_reports_what_was_read_so_far_and_a_release_voids_it(tmp_path):
    session = _project(tmp_path)
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "show"
    built = session.ensure_built()
    assert _files(built["observed_inputs"]) == {"before.txt"}, "屏障之后才读的文件不该出现"
    assert "helper.py" in {m["path"] for m in built["observed_inputs"]["local_modules"]}
    assert _files(session.observed_inputs) == {"before.txt"}

    session.resume()
    assert session.observed_inputs is None, "放行之后，那份观察已不是这次运行的全部输入"
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "script_end"
    assert session.observed_inputs is None, "结束屏障上还没 build"
    session.ensure_built()
    assert _files(session.observed_inputs) == {"before.txt", "after.txt"}
    dk.finish(session)


def test_the_parent_can_void_the_observation_before_releasing(tmp_path):
    session = _project(tmp_path)
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    session.forget_observed_inputs()
    assert session.observed_inputs is None
    dk.finish(session)


def test_a_helper_changed_while_paused_voids_the_next_barriers_observation(tmp_path):
    """第一次停下时 helper 被改：进程用的是旧内容，第二个屏障那一刻哈希到的却是新字节——不报（同 Codex #1180 P2 的口径）。"""
    session = _project(tmp_path, SCRIPT + "plt.plot([after])\nplt.show()\n")
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.ensure_built()["observed_inputs"] is not None
    nativekit.write(tmp_path / "proj" / "helper.py", "SCALE = 5.0\n")
    session.resume()
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "show"
    assert "observed_inputs" not in session.ensure_built()
    assert session.observed_inputs is None
    dk.finish(session)
