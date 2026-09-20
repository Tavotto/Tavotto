"""渲染进程没了的时候，用户必须知道它**怎么**没的（#435）。

以前协议管道一 EOF 就是一句「渲染进程崩溃（无响应），会话需要重建」——四种截然
不同的事实（脚本 `sys.exit()`、Python 致命错误、C 扩展 access violation、关了 stdout
却还活着）共用一句话，worker.log 里往往还是空的（硬崩溃不经过 Python 的异常机制）。
issue #435 的诊断包里正是 24 条这样的空记录。

三层各一组用例，两条控制面都要走一遍（pool 是 workerd 的参考实现，判据分叉就是
两套语义）：

* **worker 自己**：脚本 `sys.exit(0)` 不再把 worker 带走（`python fig.py` 的语义就是
  0 = 正常结束）；非零是脚本自己报的失败 → `script_error`，说得出是哪一句；
  硬崩溃时 faulthandler 把 Python 栈写进 worker.log。
* **控制面**：EOF 之后先问退出状态再说话——退出码 / 信号进错误信封（`extra["exit"]`），
  文案由 `pool.session_dead_message` 一处产出；日志这一代是空的要**说出来**。
* **解释表**：`pool.describe_exit` 是退出码 → 人话的唯一出处；Windows 的 NTSTATUS
  两种写法（Python 的无符号 / Rust 的 i32）查到同一条。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import pool, probe as engine_probe

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _workerd_binary() -> str | None:
    saved = os.environ.pop("TAVOTTO_WORKERD", None)
    try:
        from tavotto.engine import workerd_client

        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["TAVOTTO_WORKERD"] = saved


WORKERD_EXE = _workerd_binary()
needs_workerd = pytest.mark.skipif(
    WORKERD_EXE is None, reason="没有 tavotto-workerd 产物（先在 workerd/ 里 cargo build）"
)

EXIT_OK = """\
import sys
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("Fig1.png")
sys.exit(0)          # 教科书写法：`sys.exit(main())`，main 回 0
"""

EXIT_FAIL = """\
import sys
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("Fig1.png")
print("data incomplete, giving up")
sys.exit(2)
"""

#: faulthandler 崩溃报告的头：POSIX 与 Windows 各一句（CPython faulthandler.c）。
_FAULTHANDLER_HEADER = re.compile(r"Fatal Python error|Windows fatal exception")

ARGPARSE = """\
import argparse
import matplotlib.pyplot as plt

parser = argparse.ArgumentParser()
parser.add_argument("-c", "--clstr", required=True)
parser.add_argument("-x", "--metadata", required=True)
args = parser.parse_args()          # Tavotto 不带参数运行 → usage + sys.exit(2)

fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("metrics.png")
"""

SEGV = """\
import faulthandler
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4])
fig.savefig("Fig1.png")
faulthandler._sigsegv()   # 模拟 C 扩展越界：不经过 Python 异常机制，进程当场消失
"""


def _assert_segv_report(report: dict, message: str) -> None:
    """段错误的退出状态该长什么样——POSIX 与 Windows 各一种。

    Windows 上 access violation 之后 Windows Error Reporting 可能把进程多按住几秒
    （找调试器 / 收集转储），超过 `EXIT_GRACE` 就是被我们收掉的（`lingered`）；
    那时退出码是 TerminateProcess 的，不是 0xC0000005。两种结局都如实报，
    这里两种都认——但不认「什么都没报」。
    """
    if os.name != "nt":
        assert report["signal"] == 11, report
        assert report["lingered"] is False, report
        assert "SIGSEGV" in message
        return
    if report["lingered"]:
        assert "没有退出，已被终止" in message
        return
    # 真 C 扩展越界走 SEH → 0xC0000005；`faulthandler._sigsegv()` 走的是 C 运行时的
    # `raise(SIGSEGV)`，Windows 上默认动作是 abort() → 退出码 3（CI windows 腿实测）。
    # 两种都是「进程级崩溃」，解释表各有一行，两个都认。
    assert report["code"] in (0xC0000005, -1073741819, 3), report
    assert "access violation" in message or "abort()" in message, message


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    yield root
    pool.shutdown_all(str(root), wait=True)


@pytest.fixture
def workerd_figs(tmp_path, monkeypatch):
    from tavotto.engine import workerd_client

    monkeypatch.setenv("TAVOTTO_WORKERD", WORKERD_EXE or "0")
    workerd_client.reset_client()
    root = tmp_path / "figs"
    root.mkdir()
    try:
        yield root
    finally:
        pool.shutdown_all(str(root), wait=True)
        workerd_client.reset_client()


# ------------------------------------------------------------ worker 自己
@needs_worker
def test_sys_exit_zero_at_the_end_of_a_script_is_a_normal_ending(figs):
    """`sys.exit(0)` 以前会穿过 `except Exception`，落到主循环给协议 shutdown 用的
    `except SystemExit: break` 上——worker 悄悄退出，用户看到「崩溃」。"""
    (figs / "fig_ok.py").write_text(EXIT_OK, encoding="utf-8")
    worker, resp = pool.build("fig_ok.py", str(figs), "__main__")
    assert "Fig1" in resp["stems"], resp
    assert worker.alive(), "脚本正常结束，会话必须还活着"


@needs_worker
def test_a_nonzero_sys_exit_is_the_scripts_own_failure(figs):
    (figs / "fig_fail.py").write_text(EXIT_FAIL, encoding="utf-8")
    with pytest.raises(pool.WorkerError) as err:
        pool.build("fig_fail.py", str(figs), "__main__")
    assert err.value.code == "script_exited"
    assert "sys.exit(2)" in str(err.value)
    assert "SystemExit" in err.value.traceback_text


@needs_worker
def test_a_script_that_wants_cli_arguments_gets_its_own_code_and_the_usage_text(figs):
    """微信截图里那一例：traceback 区最后两行是 argparse 的 `usage: … -c CLSTR -x METADATA`。

    argparse 报缺参数后 `sys.exit(2)`，以前 worker 随之退出、上层报「崩溃」。
    现在是一条有出路的错误：code 单独一条（前端文案讲「给默认值 / tavotto run」），
    而 argparse 打到 stderr 的 usage 要从 worker.log 接到 traceback 里——用户要
    知道它要哪些参数，答案只在那儿。
    """
    (figs / "metrics.py").write_text(ARGPARSE, encoding="utf-8")
    with pytest.raises(pool.WorkerError) as err:
        pool.build("metrics.py", str(figs), "__main__")
    e = err.value
    assert e.code == "script_needs_arguments"
    assert "usage: metrics.py" in e.traceback_text, e.traceback_text
    assert "-c CLSTR" in e.traceback_text and "-x METADATA" in e.traceback_text
    assert "SystemExit: 2" in e.traceback_text
    assert "tavotto run" in str(e)
    assert e.extra.get("exit_code") == 2


@needs_worker
@needs_workerd
def test_workerd_gives_the_same_answer_for_a_script_that_wants_arguments(workerd_figs):
    (workerd_figs / "metrics.py").write_text(ARGPARSE, encoding="utf-8")
    w = pool.get("metrics.py", str(workerd_figs), "__main__")
    assert isinstance(w, pool.WorkerdWorker), type(w)
    with pytest.raises(pool.WorkerError) as err:
        w.ensure_built()
    e = err.value
    assert e.code == "script_needs_arguments"
    assert "usage: metrics.py" in e.traceback_text, e.traceback_text
    assert "SystemExit: 2" in e.traceback_text
    assert w.alive(), "脚本要参数不是会话故障：进程还在，换个脚本照常用"


@needs_worker
def test_a_hard_crash_reports_the_exit_status_and_the_python_stack(figs):
    """段错误 / access violation：退出状态进信封，faulthandler 的栈进 worker.log。

    变异反证：拿掉 worker.py 里的 `faulthandler.enable(...)`，`traceback_text` 里
    就没有 `Fatal Python error`；拿掉 `exit_report()`，`extra["exit"]` 就不在。
    """
    (figs / "fig_segv.py").write_text(SEGV, encoding="utf-8")
    with pytest.raises(pool.WorkerError) as err:
        pool.build("fig_segv.py", str(figs), "__main__")
    e = err.value
    assert e.code == "session_dead"
    report = e.extra["exit"]
    _assert_segv_report(report, str(e))
    assert "渲染进程退出了" in str(e)
    assert "崩溃（无响应）" not in str(e)
    # faulthandler 在 worker 里是开着的：栈落在 worker.log，也就是错误的 traceback 区
    # （POSIX 的头是 `Fatal Python error: Segmentation fault`，Windows 的是
    # `Windows fatal exception: access violation`——CPython 两个平台两句话）
    assert _FAULTHANDLER_HEADER.search(e.traceback_text), e.traceback_text
    # **崩在哪一帧**才是用户要的那一行；3.14 的 faulthandler 默认还附一段 C 栈，
    # 30 行的尾巴装不下——worker 关掉 c_stack、pool 取 FATAL_TAIL_LINES 行，两头都钉
    assert "fig_segv.py" in e.traceback_text, e.traceback_text
    # 日志非空，文案就不许说「没有留下任何输出」
    assert "没有留下任何输出" not in str(e)


@needs_worker
@needs_workerd
def test_workerd_gives_the_same_answer_for_a_hard_crash(workerd_figs):
    """同一件事在 Rust supervisor 上：同一个 code、同一份解释、同一份栈。"""
    (workerd_figs / "fig_segv.py").write_text(SEGV, encoding="utf-8")
    w = pool.get("fig_segv.py", str(workerd_figs), "__main__")
    assert isinstance(w, pool.WorkerdWorker), type(w)
    with pytest.raises(pool.WorkerError) as err:
        w.ensure_built()
    e = err.value
    assert e.code == "session_dead"
    _assert_segv_report(e.extra["exit"], str(e))
    assert "渲染进程退出了" in str(e)
    assert _FAULTHANDLER_HEADER.search(e.traceback_text), e.traceback_text
    assert "fig_segv.py" in e.traceback_text, e.traceback_text
    assert not w.alive()


# ------------------------------------------------------------ 解释表
@pytest.mark.parametrize(
    "report, needle",
    [
        ({"code": 0, "signal": None, "lingered": False}, "sys.exit()"),
        ({"code": 1, "signal": None, "lingered": False}, "退出码 1"),
        ({"code": -1073741819, "signal": None, "lingered": False}, "access violation"),
        # Python 的 `Popen.returncode` 在 Windows 上交的是无符号 DWORD：同一个死因
        ({"code": 0xC0000005, "signal": None, "lingered": False}, "access violation"),
        ({"code": -1073741515, "signal": None, "lingered": False}, "找不到某个 DLL"),
        ({"code": None, "signal": 11, "lingered": False}, "SIGSEGV"),
        ({"code": None, "signal": 9, "lingered": False}, "SIGKILL"),
        ({"code": 77, "signal": None, "lingered": False}, "退出码 77"),
        ({"code": None, "signal": 31, "lingered": False}, "信号 31"),
        ({"code": 1, "signal": None, "lingered": True}, "没有退出，已被终止"),
        (None, "退出状态未知"),
        ({}, "退出状态未知"),
    ],
)
def test_describe_exit_names_the_cause_or_reports_the_raw_number(report, needle):
    text = pool.describe_exit(report)
    assert needle in text, text


def test_the_two_spellings_of_an_ntstatus_land_on_the_same_line():
    signed = pool.describe_exit({"code": -1073741819, "signal": None, "lingered": False})
    unsigned = pool.describe_exit({"code": 3221225477, "signal": None, "lingered": False})
    assert signed == unsigned


def test_session_dead_message_says_when_the_log_is_empty(tmp_path):
    log = tmp_path / "worker.log"
    silent = pool.session_dead_message({"code": None, "signal": 11, "lingered": False}, "", log)
    assert "没有留下任何输出" in silent
    assert str(log) in silent
    talkative = pool.session_dead_message(
        {"code": None, "signal": 11, "lingered": False},
        "Fatal Python error: Segmentation fault",
        log,
    )
    assert "没有留下任何输出" not in talkative
    assert "SIGSEGV" in talkative
    # 评审 #443 P2：响应里只带尾巴，全文在哪两种情况都要说
    assert str(log) in talkative


# ------------------------------------------------------------ 退出状态的采集
class _Proc:
    """一个只会回答 `wait()` / `kill()` 的进程替身。"""

    def __init__(self, returncode=None, exits_after_kill=True):
        self.returncode = returncode
        self.killed = False
        self._exits_after_kill = exits_after_kill

    def wait(self, timeout=None):
        if self.returncode is None:
            if self.killed and self._exits_after_kill:
                self.returncode = -9 if os.name != "nt" else 1
                return self.returncode
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self):
        self.killed = True


def test_exit_report_reads_the_status_of_a_process_that_already_exited():
    proc = _Proc(returncode=3)
    assert pool.exit_report(proc, grace=0.01) == {"code": 3, "signal": None, "lingered": False}
    assert not proc.killed, "已经退出的进程没什么可杀的——杀了会把它真正的死因盖掉"


def test_exit_report_kills_a_lingering_process_and_says_so():
    proc = _Proc(returncode=None)
    report = pool.exit_report(proc, grace=0.01)
    assert proc.killed
    assert report["lingered"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX 的 Popen 用负数表示信号")
def test_exit_report_turns_a_negative_returncode_into_a_signal():
    proc = _Proc(returncode=-11)
    assert pool.exit_report(proc, grace=0.01) == {"code": None, "signal": 11, "lingered": False}


def test_exit_report_still_kills_a_process_object_it_cannot_ask():
    class NoWait:
        def __init__(self):
            self.killed = False

        def kill(self):
            self.killed = True

    proc = NoWait()
    assert pool.exit_report(proc, grace=0.01) is None
    assert proc.killed, "问不出退出状态也得杀：这条路径以前就是「EOF 即 kill」"


def test_this_interpreter_reports_its_own_exit_code_through_the_real_popen():
    """`exit_report` 对着真的 `Popen`：退出码就是子进程说的那个。"""
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(5)"])
    assert pool.exit_report(proc, grace=10.0) == {"code": 5, "signal": None, "lingered": False}


# ------------------------------------------------------------ 同源对
def test_the_exit_grace_is_one_number_on_both_control_planes():
    """`pool.EXIT_GRACE` ↔ `workerd/src/worker.rs` 的 `EXIT_GRACE`（同源对总表有它一行）。

    宽限不一致的表现是：同一个「关了管道赖着不退」的进程，一条控制面说它自己
    退了、另一条说它被终止——同一件事两个答案。
    """
    rs = Path(__file__).resolve().parents[1] / "workerd" / "src" / "worker.rs"
    if not rs.is_file():
        pytest.skip("没有 workerd 源码（wheel/sdist 里不含）")
    m = re.search(
        r"pub const EXIT_GRACE: Duration = Duration::from_millis\((\d+)\);",
        rs.read_text(encoding="utf-8"),
    )
    assert m, "worker.rs 里找不到 EXIT_GRACE 的定义（改了写法就同步这条正则）"
    assert int(m.group(1)) == round(pool.EXIT_GRACE * 1000)


# ------------------------------------------------------------ probe 的归类
def _dead(report):
    err = pool.WorkerError("渲染进程退出了", "", code="session_dead")
    err.extra = {"exit": report}
    return err


@pytest.mark.parametrize(
    "report, code",
    [
        # 自己死的：段错误 / access violation / sys.exit → 脚本的问题，不是「被中断」
        ({"code": None, "signal": 11, "lingered": False}, engine_probe.ERROR_PROBE_FAILED),
        ({"code": -1073741819, "signal": None, "lingered": False}, engine_probe.ERROR_PROBE_FAILED),
        ({"code": 0, "signal": None, "lingered": False}, engine_probe.ERROR_PROBE_FAILED),
        # 被杀 / 被收掉 / 不知道：维持「被中断」
        ({"code": None, "signal": 9, "lingered": False}, engine_probe.ERROR_CANCELLED),
        ({"code": 1, "signal": None, "lingered": True}, engine_probe.ERROR_CANCELLED),
        (None, engine_probe.ERROR_CANCELLED),
    ],
)
def test_probe_tells_a_self_inflicted_death_from_an_interruption(report, code):
    out = engine_probe._error_from_worker(_dead(report), "__main__")
    assert out["code"] == code, out


def test_probe_passes_the_two_script_exit_codes_through_with_the_last_line():
    """试运行那条路同样认这两个码：归成通用的 script_probe_failed 会把出路说丢。"""
    err = pool.WorkerError(
        "脚本要求命令行参数…",
        "usage: x.py -c C\nTraceback…\nSystemExit: 2",
        code="script_needs_arguments",
    )
    out = engine_probe._error_from_worker(err, "__main__")
    assert out["code"] == engine_probe.ERROR_NEEDS_ARGUMENTS == "script_needs_arguments"
    assert out["params"]["error"] == "SystemExit: 2"
    assert "usage: x.py" in out["traceback"]
    err2 = pool.WorkerError("脚本调用了 sys.exit(3)…", "…\nSystemExit: 3", code="script_exited")
    out2 = engine_probe._error_from_worker(err2, "main")
    assert out2["code"] == engine_probe.ERROR_SCRIPT_EXITED
    assert out2["params"]["error"] == "SystemExit: 3"


@pytest.mark.skipif(os.name != "nt", reason="Windows 的 TerminateProcess 退出码是 1")
def test_on_windows_exit_code_one_is_not_called_a_crash():
    """退出码 1 在 Windows 上与我们自己的 kill 撞车：宁可少报一次崩溃。"""
    assert not pool.exited_on_its_own({"code": 1, "signal": None, "lingered": False})
