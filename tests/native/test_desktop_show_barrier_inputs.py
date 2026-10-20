"""#1047 / #1202：桌面拥有的运行停在 `show()` 屏障上时的输入观察，与「第一个屏障即终点」（`final_barrier`）。

* `show()` 屏障上的 build 带回**屏障之前**读过的全部（`observed_inputs_complete=False`）；之后放没放行都不改变它。脚本跑完后
  在结束屏障上的 build 给全部（`complete=True`）。停着时 helper 被改：之后的屏障 / 结束都不报（进程执行的是旧内容）。
* 凭「只到 show() 为止」的依据沿用 native 的运行带 `final_barrier`：用户进程里的 runner 拒绝放行，断线 / 终止直接结束进程，
  屏障之后的代码一行不跑——连吞掉 `SystemExit` 的用户代码也拦得住。
真 spawn、真 Bridge Runner（`desktopproc.start`，命令行带 `--observe-inputs`）；记录与路由的端到端在 `test_run_routing_e2e.py`。
"""

from __future__ import annotations

import pytest

from support import desktopkit as dk, nativekit
from tavotto.engine import nativesession, runcodes
from tavotto.engine.runcodes import RunError

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

#: show() 之后写一个标记：屏障之后的代码有没有跑过。`try/except BaseException` 吞掉 SystemExit——`final_barrier` 不能靠它
TAIL = (
    dk.FIGURE_HEADER
    + """\
from pathlib import Path
here = Path(__file__).resolve().parent
plt.plot([0, 1])
try:
    plt.show()
except BaseException:
    pass
(here / "tail.txt").write_text("ran")
"""
)


def _files(inputs) -> set[str]:
    return {f["path"] for f in inputs["files"]}


def _project(tmp_path, script=SCRIPT, **kw):
    proj = tmp_path / "proj"
    nativekit.write(proj / "helper.py", "SCALE = 2.0\n")
    nativekit.write(proj / "before.txt", "1\n")
    nativekit.write(proj / "after.txt", "3\n")
    return dk.start(tmp_path, script, **kw)


def test_the_show_barrier_build_reports_what_was_read_before_it_and_the_end_build_reports_all(
    tmp_path,
):
    session = _project(tmp_path)
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "show"
    built = session.ensure_built()
    assert _files(built["observed_inputs"]) == {"before.txt"}, "屏障之后才读的文件不该出现"
    assert "helper.py" in {m["path"] for m in built["observed_inputs"]["local_modules"]}
    assert session.observed_inputs_complete is False

    session.resume()
    # 放行不改变「屏障之前读了什么」
    assert _files(session.observed_inputs) == {"before.txt"}
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "script_end"
    session.ensure_built()
    assert _files(session.observed_inputs) == {"before.txt", "after.txt"}
    assert session.observed_inputs_complete is True
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


def test_a_helper_changed_while_paused_is_not_reported_at_the_script_end_either(tmp_path):
    """同样的改动，脚本之后直接跑完：结束屏障上的定格对不上 → 不报；也不许退回去用 `show()` 那一刻的旧观察。"""
    session = _project(tmp_path)
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.ensure_built()["observed_inputs"] is not None
    nativekit.write(tmp_path / "proj" / "helper.py", "SCALE = 5.0\n")
    session.resume()
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "script_end"
    assert "observed_inputs" not in session.ensure_built()
    dk.finish(session)


def _parked_final(tmp_path):
    session = dk.start(tmp_path, TAIL, final_barrier=True)
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.barrier_reason == "show"
    session.ensure_built()
    return session, tmp_path / "proj" / "tail.txt"


def test_a_final_barrier_run_refuses_to_be_released(tmp_path):
    """拒绝在用户进程的 runner 里做（父进程不另挡）：继续 / 放手都被拒，脚本仍停着，show() 之后一行没跑。"""
    session, tail = _parked_final(tmp_path)
    with pytest.raises(RunError) as refused:
        session.resume()
    assert refused.value.code == runcodes.NATIVE_BARRIER_FINAL
    with pytest.raises(RunError):
        session.detach()
    assert session.state == nativesession.BARRIER
    assert not tail.exists()


@pytest.mark.parametrize("leave", ["disconnect", "terminate"])
def test_leaving_a_final_barrier_ends_the_process_without_running_user_code(tmp_path, leave):
    """断线（runner 读到 EOF）/ 终止（`SystemExit`，用户代码会吞掉它）：都直接结束进程，show() 之后的标记写不出来。"""
    session, tail = _parked_final(tmp_path)
    if leave == "disconnect":
        session.transport.close()
    else:
        session.terminate()
    assert session.owned.wait(60)
    assert not tail.exists(), "第一个屏障之后的用户代码跑了"


def test_without_final_barrier_the_same_script_does_continue(tmp_path):
    """对照：不带 `final_barrier` 时同一个脚本放行后确实写出标记（上面几条的「没写出」才有意义）。"""
    session = dk.start(tmp_path, TAIL)
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.resume()
    dk.wait_until(lambda: (tmp_path / "proj" / "tail.txt").exists(), what="show() 之后的标记")
    dk.finish(session)


def test_the_show_barrier_build_reports_where_it_stopped_and_how_many_threads_still_run(tmp_path):
    """屏障位置事实（#1202）：目标脚本里调用 `show()` 的那一行、除主线程外的活线程数。"""
    session = _project(tmp_path)
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    show_line = SCRIPT.splitlines().index("plt.show()") + 1
    assert session.barrier_site == {"line": show_line, "threads": 0}
    session.resume()
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    assert session.barrier_site is None, "脚本跑完后的 build 不带屏障位置"
    dk.finish(session)


def test_a_thread_started_before_show_is_counted(tmp_path):
    session = _project(
        tmp_path,
        SCRIPT.replace(
            "plt.show()",
            "import threading, time\nthreading.Thread(target=time.sleep, args=(30,), daemon=True).start()\nplt.show()",
        ),
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    assert session.barrier_site["threads"] >= 1
