"""桌面拥有的 native 脚本进程（P06，ADR 0125）：**真 spawn、真 Figure、真取消**。

链条：`desktopproc.start` 在本进程里 spawn 用户 Python（真 Bridge Runner、真 Matplotlib）→
认证握手 → `NativeTransport` 单 reader → `NativeSession`。唯一被替掉的是窗口与 Flask。

Linux / macOS 协议级；Windows 的 Job 路径见 `test_desktop_windows_job.py`（替身 kernel32），
真机与 GUI 归 P07 / P10，本文件不代表它们。
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

from support import desktopkit as dk, nativekit, previewkit
from tavotto.engine import desktopproc, envlease, execspec, nativesession, runcodes
from tavotto.engine.runcodes import RunError

pytestmark = nativekit.needs_user_python

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="用 shell 桩当解释器")


@pytest.fixture(autouse=True)
def _reap_owned():
    """用例红了也不许留下活着的脚本进程（先于 conftest 的清理收，因为后注册先拆）。"""
    yield
    for s in nativesession.REGISTRY.list():
        if s.owned is not None and not s.owned.finished:
            try:
                s.cancel_owned()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 启动身份：指定的 argv / cwd / env 真的到了脚本里；savefig / replace 是真文件语义（AC-02/03）
# ---------------------------------------------------------------------------
OBSERVE_SCRIPT = (
    dk.FIGURE_HEADER
    + """\
import json, os, sys
from PIL import Image

with open("cache.json", "w") as f:
    json.dump({"v": 1}, f)
with open("cache.json.tmp", "w") as f:
    json.dump({"v": 2}, f)
os.replace("cache.json.tmp", "cache.json")          # 桌面保留的普通文件语义：值是 2

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot([0, 1], [0, 1])
fig.savefig("stage.png", dpi=100)                   # 真写盘……
img = Image.open("stage.png")                       # ……脚本自己读回来

with open("observed.json", "w") as f:
    json.dump({
        "v": json.load(open("cache.json"))["v"],
        "size": list(img.size),
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "marker": os.environ.get("TAVOTTO_P06_MARKER"),
        "exe": sys.executable,
        "token_visible": "TAVOTTO_BRIDGE_TOKEN" in os.environ,
    }, f)
print("OBSERVED-DONE", flush=True)
plt.show()
"""
)


def test_spawn_uses_the_launch_identity_and_real_file_semantics(tmp_path):
    """指定的 argv / cwd / env 原样到脚本里；savefig 真写盘、`os.replace` 值为 2（AC-02/03）。

    cwd 故意不是脚本目录也不是项目根；env 是叠在 sidecar 环境上的增量；token 一起来就被摘掉。
    """
    session = dk.start(
        tmp_path,
        OBSERVE_SCRIPT,
        argv=("--seed", "7", "中文 参数"),
        env={"TAVOTTO_P06_MARKER": "marker-17"},
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    work = tmp_path / "work"
    seen = dk.read_json(work / "observed.json")

    assert seen["v"] == 2, "os.replace 的结果脚本没看到（safe 的旧行为是 1）"
    assert seen["size"] == [400, 300]
    assert seen["argv"] == [str(tmp_path / "proj" / "figure.py"), "--seed", "7", "中文 参数"]
    assert os.path.realpath(seen["cwd"]) == os.path.realpath(work)
    assert seen["marker"] == "marker-17"
    assert os.path.realpath(seen["exe"]) == os.path.realpath(nativekit.USER_PYTHON)
    assert seen["token_visible"] is False

    # 脚本自己写出的真文件：测试进程独立读，尺寸与脚本读到的一致，不是捕获缓存冒充
    with Image.open(work / "stage.png") as img:
        assert list(img.size) == seen["size"]
    assert (work / "cache.json").read_text() == '{"v": 2}'
    assert "OBSERVED-DONE" in dk.tail(session)
    assert session.process_pid == session.owned.pid

    dk.finish(session)
    assert session.exit_code == 0 and session.owned.finished


# ---------------------------------------------------------------------------
# 12pt → 22pt → 临时导出 10pt → continue（AC-17）
# ---------------------------------------------------------------------------
STATE_SCRIPT = (
    dk.FIGURE_HEADER
    + """\
import os
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("Stage", fontsize=12)
ax.tick_params(labelsize=6)
print("PID1:" + str(os.getpid()), flush=True)
plt.show()
print("PID2:" + str(os.getpid()), flush=True)
print("SAW:" + str(ax.title.get_fontsize()), flush=True)
"""
)

_TF = re.compile(rb"/F\d+ (\d+(?:\.\d+)?) Tf")


def pdf_font_sizes(path: Path) -> set[float]:
    """独立读：把 PDF 里每条（解压后的）内容流扫一遍，收 `/Fn <size> Tf`。不经过产品的任何读取器。"""
    data = Path(path).read_bytes()
    sizes: set[float] = set()
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        blob = m.group(1)
        try:
            blob = zlib.decompress(blob)
        except zlib.error:
            pass
        sizes.update(float(x) for x in _TF.findall(blob))
    return sizes


def _title_gid(session, stem):
    man = dk.read_json(previewkit.preview_file(session.out_dir, stem, ".json"))
    return next(e["gid"] for e in man["elements"] if e.get("role") == "title")


def test_temporary_export_is_state_neutral_and_continue_sees_the_original(tmp_path):
    session = dk.start(tmp_path, STATE_SCRIPT)
    nativekit.wait_state(session, [nativesession.BARRIER])
    stem = list(session.ensure_built()["stems"])[0]
    gid = _title_gid(session, stem)

    edit22 = [{"gid": gid, "prop": "fontsize", "value": 22}]
    edit10 = [{"gid": gid, "prop": "fontsize", "value": 10}]
    session.override(stem, edit22)
    png_before = session.render_png(stem, 600).read_bytes()

    pdf22, pdf10 = tmp_path / "e22.pdf", tmp_path / "e10.pdf"
    session.export(stem, edit22, str(pdf22))
    session.export(stem, edit10, str(pdf10))  # 临时导出：套用别的值，导完还原回会话的 22
    png_after = session.render_png(stem, 600).read_bytes()

    s22, s10 = pdf_font_sizes(pdf22), pdf_font_sizes(pdf10)
    assert 22.0 in s22 and 12.0 not in s22 and 10.0 not in s22, s22
    assert 10.0 in s10 and 22.0 not in s10 and 12.0 not in s10, s10
    assert png_before == png_after, "临时导出改动了会话里当前（22pt）的画面"

    pid1 = int(re.search(r"PID1:(\d+)", dk.tail(session)).group(1))
    session.resume()
    nativekit.wait_state(session, [nativesession.BARRIER])  # script_end 屏障：脚本已经跑过 show
    out = dk.tail(session)
    assert f"PID2:{pid1}" in out, "续跑不是同一个进程"
    assert "SAW:12.0" in out, "脚本在 continue 之后看到的不是原始 12pt"
    assert pid1 == session.process_pid
    dk.finish(session)
    assert session.exit_code == 0


# ---------------------------------------------------------------------------
# 脚本自己的退出码保留（源码异常 / sys.exit）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tail_code", "want"),
    [
        ('raise ValueError("boom-from-script")', 1),
        ("sys.exit(3)", 3),
    ],
)
def test_the_scripts_own_exit_code_is_preserved(tmp_path, tail_code, want):
    session = dk.start(
        tmp_path,
        dk.FIGURE_HEADER
        + f"""\
import sys
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.png")
{tail_code}
""",
    )
    nativekit.wait_state(session, [nativesession.BARRIER])  # 出错也交出已有的图
    assert list(session.ensure_built()["stems"]) == ["Fig1"]
    dk.finish(session)
    assert session.exit_code == want
    assert session.owned.exit_code == want
    assert session.cancelled is False
    if want == 1:
        assert session.script_error and "boom-from-script" in session.script_error["message"]
        assert "boom-from-script" in dk.tail(session, "stderr")


# ---------------------------------------------------------------------------
# 取消：running / barrier；进程组整组结束、租约在确认后才放（AC-16）
# ---------------------------------------------------------------------------
RUNNING_SCRIPT = """\
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open("child.pid", "w").write(str(child.pid))
open("ready", "w").write("1")
time.sleep(600)
"""


def test_cancel_while_running_kills_the_group_and_releases_the_lease_after_confirmation(tmp_path):
    session = dk.start(tmp_path, RUNNING_SCRIPT)
    work = tmp_path / "work"
    dk.wait_until(lambda: (work / "ready").exists(), what="脚本就绪")
    child_pid = int((work / "child.pid").read_text())
    interpreter = session.interpreter

    assert session.state in (nativesession.WAITING_FOR_FIGURE, nativesession.RUNNING_SCRIPT)
    assert dk.holds_lease(session)
    assert dk.install_is_refused(interpreter) == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION
    assert dk.pid_alive(child_pid)

    result = session.cancel_owned()

    assert result.confirmed and result.scope in ("process_group", "job_object")
    assert session.owned.finished and session.owned.exited
    assert session.cancelled is True and session.state == nativesession.ENDED
    assert not dk.pid_alive(session.owned.pid), "直接子进程没被 reap"
    assert not dk.pid_alive(child_pid), "进程组里的孙进程泄漏了"
    assert not dk.holds_lease(session), "确认结束后租约没放"
    assert dk.install_is_refused(interpreter) is None
    assert session.exit_code == result.exit_code


BARRIER_SCRIPT = (
    dk.FIGURE_HEADER
    + """\
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
open("child.pid", "w").write(str(child.pid))
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
"""
)


def test_cancel_at_a_barrier_also_takes_the_whole_group(tmp_path):
    session = dk.start(tmp_path, BARRIER_SCRIPT)
    nativekit.wait_state(session, [nativesession.BARRIER])
    child_pid = int((tmp_path / "work" / "child.pid").read_text())

    result = session.cancel_owned()

    assert result.confirmed
    assert not dk.pid_alive(session.owned.pid) and not dk.pid_alive(child_pid)
    assert not dk.holds_lease(session)
    assert session.cancelled and session.state == nativesession.ENDED


# ---------------------------------------------------------------------------
# 断开不是结束：控制连接没了，进程与租约还在，直到所有者确认
# ---------------------------------------------------------------------------
def test_disconnect_while_running_keeps_the_process_and_the_lease(tmp_path):
    session = dk.start(tmp_path, RUNNING_SCRIPT)
    work = tmp_path / "work"
    dk.wait_until(lambda: (work / "ready").exists(), what="脚本就绪")
    child_pid = int((work / "child.pid").read_text())
    interpreter = session.interpreter

    session.shutdown()  # WebView / socket 断开的等价物：只关控制连接
    session.transport.close()
    time.sleep(0.5)

    assert session.state == nativesession.DETACHED, "断开后会话应已放手"
    assert not session.owned.exited and dk.pid_alive(session.owned.pid), "断开把进程弄没了"
    assert dk.pid_alive(child_pid)
    assert dk.holds_lease(session), "进程还活着，租约被提前放了"
    assert dk.install_is_refused(interpreter) == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION

    session.cancel_owned()  # 断开后仍可由所有者结束
    assert session.owned.finished and not dk.pid_alive(child_pid)
    assert not dk.holds_lease(session)
    assert dk.install_is_refused(interpreter) is None


def test_the_transport_dropping_at_a_barrier_lets_the_script_finish_then_releases(tmp_path):
    """屏障上控制连接被拔：runner 先恢复 baseline 再放开，脚本跑完；租约等 reap 之后才放。"""
    session = dk.start(
        tmp_path,
        dk.FIGURE_HEADER
        + """\
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
open("after-show", "w").write("1")
""",
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.transport.close()
    dk.wait_until(lambda: (tmp_path / "work" / "after-show").exists(), what="断开后脚本继续")
    assert session.owned.wait(30)
    assert session.owned.exit_code == 0 and not session.cancelled
    assert not dk.holds_lease(session)


# ---------------------------------------------------------------------------
# 两个会话互不误杀
# ---------------------------------------------------------------------------
def test_two_sessions_do_not_kill_each_other(tmp_path):
    a = dk.start(tmp_path / "a", RUNNING_SCRIPT, launch_id="a")
    b = dk.start(tmp_path / "b", RUNNING_SCRIPT, launch_id="b")
    for t in ("a", "b"):
        dk.wait_until(lambda t=t: (tmp_path / t / "work" / "ready").exists(), what=f"{t} 就绪")
    child_a = int((tmp_path / "a" / "work" / "child.pid").read_text())
    child_b = int((tmp_path / "b" / "work" / "child.pid").read_text())
    assert a.owned.pid != b.owned.pid
    assert a.interpreter == b.interpreter  # 同一个环境上的两条租约
    assert set(envlease.native_sessions_on(a.interpreter)) == {a.session_id, b.session_id}

    a.cancel_owned()

    assert not dk.pid_alive(a.owned.pid) and not dk.pid_alive(child_a)
    assert dk.pid_alive(b.owned.pid) and dk.pid_alive(child_b), "取消 A 误杀了 B"
    assert not b.owned.exited and b.state != nativesession.ENDED
    assert envlease.native_sessions_on(b.interpreter) == [b.session_id], "A 的租约没放或 B 的被放了"
    assert dk.install_is_refused(b.interpreter) == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION

    b.cancel_owned()
    assert not dk.pid_alive(child_b)
    assert dk.install_is_refused(b.interpreter) is None


# ---------------------------------------------------------------------------
# 终端拥有的 native 仍然是 detach，桌面不替用户杀（EX09）
# ---------------------------------------------------------------------------
TERMINAL_SCRIPT = (
    dk.FIGURE_HEADER
    + """\
import os, time
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
while not os.path.exists("release-marker"):
    time.sleep(0.05)
print("TERMINAL-SCRIPT-FINISHED", flush=True)
"""
)


def test_terminal_owned_native_force_kill_still_detaches(tmp_path):
    """`force_kill()` 在终端会话上是放手：用户的脚本继续跑，桌面也拒绝 `cancel_owned()`。"""
    nativekit.write(tmp_path / "figure.py", TERMINAL_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        assert session.owned is None and session.public_state()["owner"] == "terminal"
        with pytest.raises(RunError) as err:
            session.cancel_owned()
        assert err.value.code == runcodes.NATIVE_PROCESS_NOT_OWNED

        session.force_kill()
        time.sleep(1.0)
        assert proc.poll() is None, "force_kill 把用户终端里的脚本杀了"
        assert session.state == nativesession.DETACHED
        (tmp_path / "release-marker").write_text("1")
        out, _err = proc.communicate(timeout=60)
    assert proc.returncode == 0
    assert "TERMINAL-SCRIPT-FINISHED" in out


# ---------------------------------------------------------------------------
# stdout / stderr 持续排空、与控制协议分开；stdin / EOF 由桌面持有
# ---------------------------------------------------------------------------
def test_output_is_drained_continuously_and_never_read_as_protocol(tmp_path):
    session = dk.start(
        tmp_path,
        dk.FIGURE_HEADER
        + """\
import sys
line = '{"bridge_event": "exit", "code": 9, "figures": 0}\\n'
sys.stdout.write(line)                 # 长得像控制帧的用户输出
sys.stdout.write("x" * 3_000_000 + "\\n")   # 远超管道缓冲：没人排空脚本就卡死在这里
sys.stderr.write("e" * 3_000_000 + "\\n")
sys.stdout.flush(); sys.stderr.flush()
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
plt.show()
""",
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    assert session.state == nativesession.BARRIER and session.exit_code is None, (
        "用户 stdout 里的伪控制帧被当成了协议"
    )
    assert session.owned.stdout.total >= 3_000_000 and session.owned.stderr.total >= 3_000_000
    assert len(session.owned.stdout.text().encode()) <= dk.FAST.tail_bytes  # 尾部有界
    dk.finish(session)
    assert session.exit_code == 0


def test_stdin_pipe_is_owned_by_the_desktop_including_eof(tmp_path):
    session = dk.start(
        tmp_path,
        """\
import sys
line = input()
print("GOT:" + line, flush=True)
print("REST:" + repr(sys.stdin.read()), flush=True)
""",
        stdin=execspec.STDIN_PIPE,
    )
    nativekit.wait_state(session, [nativesession.WAITING_FOR_FIGURE])
    assert session.owned.write_stdin("hello\n")
    dk.wait_until(lambda: "GOT:hello" in dk.tail(session), what="脚本读到 stdin")
    assert not session.owned.finished, "还没送 EOF，脚本不该结束"
    session.owned.close_stdin()
    assert session.owned.wait(30)
    assert "REST:''" in dk.tail(session)
    assert session.owned.exit_code == 0
    assert session.owned.write_stdin("late") is False


def test_stdin_null_is_an_immediate_eof_not_a_hang(tmp_path):
    session = dk.start(tmp_path, "input()\n", stdin=execspec.STDIN_NULL)
    assert session.owned.wait(60)
    assert session.owned.exit_code == 1
    assert "EOFError" in dk.tail(session, "stderr")


# ---------------------------------------------------------------------------
# 先取租约再起进程；起不来 / 连不上都不留进程、不留租约
# ---------------------------------------------------------------------------
def test_an_environment_being_installed_refuses_the_launch_before_any_user_code(tmp_path):
    launch = dk.launch_for(tmp_path, 'open("ran", "w").write("1")\n')
    with envlease.mutating("p06-install", python=launch.spec.interpreter):
        with pytest.raises(envlease.EnvironmentBusy) as err:
            desktopproc.start(launch)
    assert err.value.code == envlease.ENVIRONMENT_MUTATING
    assert not (tmp_path / "work" / "ran").exists(), "租约没取到却跑了用户代码"
    assert nativesession.REGISTRY.list() == []
    assert envlease.native_sessions_on(launch.spec.interpreter) == []


def _stub_interpreter(tmp_path: Path, body: str) -> str:
    stub = tmp_path / "fake-python"
    stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return str(stub)


@POSIX_ONLY
def test_a_child_that_never_connects_is_killed_and_the_lease_released(tmp_path):
    stub = _stub_interpreter(tmp_path, f'echo $$ > "{tmp_path}/stub.pid"\nexec sleep 600\n')
    policy = desktopproc.OwnedPolicy(connect_timeout=1.0, term_grace=1.0, kill_timeout=5.0)
    launch = dk.launch_for(tmp_path, "pass\n", interpreter=stub, policy=policy)
    with pytest.raises(RunError) as err:
        desktopproc.start(launch)
    assert err.value.code == runcodes.DESKTOP_RUN_CONNECT_TIMEOUT
    pid = int((tmp_path / "stub.pid").read_text())
    dk.wait_until(lambda: not dk.pid_alive(pid), timeout=10, what="桩进程被收掉")
    dk.wait_until(lambda: envlease.native_sessions_on(stub) == [], timeout=10, what="租约释放")
    assert nativesession.REGISTRY.list() == []


@POSIX_ONLY
def test_a_child_that_dies_before_connecting_reports_it_and_releases_the_lease(tmp_path):
    stub = _stub_interpreter(tmp_path, "exit 7\n")
    launch = dk.launch_for(tmp_path, "pass\n", interpreter=stub)
    with pytest.raises(RunError) as err:
        desktopproc.start(launch)
    assert err.value.code == runcodes.BRIDGE_CHILD_EXITED and err.value.fields["code"] == 7
    dk.wait_until(lambda: envlease.native_sessions_on(stub) == [], timeout=10, what="租约释放")


def test_a_missing_interpreter_or_cwd_is_refused_without_a_lease(tmp_path):
    ok = dk.launch_for(tmp_path, "pass\n")
    bad_interp = desktopproc.OwnedLaunch(
        execspec.desktop_native_spec(
            ok.spec.raw_target,
            interpreter=str(tmp_path / "no-such-python"),
            cwd=ok.spec.cwd,
            project_root=ok.spec.project_root,
        )
    )
    with pytest.raises(RunError) as err:
        desktopproc.start(bad_interp)
    assert err.value.code == runcodes.INTERPRETER_NOT_EXECUTABLE
    bad_cwd = desktopproc.OwnedLaunch(
        execspec.desktop_native_spec(
            ok.spec.raw_target,
            interpreter=ok.spec.interpreter,
            cwd=str(tmp_path / "no-such-dir"),
            project_root=ok.spec.project_root,
        )
    )
    with pytest.raises(RunError) as err:
        desktopproc.start(bad_cwd)
    assert err.value.code == runcodes.DESKTOP_RUN_LAUNCH_INVALID
    assert envlease.snapshot()["native"] == {}


def test_a_terminal_owned_spec_cannot_start_a_desktop_process():
    spec = execspec.native_spec(
        "/p/fig.py", interpreter=sys.executable, cwd="/p", project_root="/p"
    )
    with pytest.raises(RunError) as err:
        desktopproc.OwnedLaunch(spec)
    assert err.value.code == runcodes.DESKTOP_RUN_LAUNCH_INVALID


def test_receipt_and_public_state_never_carry_argv_or_env_values(tmp_path):
    """回执 / 公开状态 / 诊断只带数量、运行配置引用与 env 键名；值永远不出进程。"""
    from tavotto.engine import receipt

    session = dk.start(
        tmp_path,
        "import time; time.sleep(600)\n",
        argv=("--token", "s3cret-argv-value"),
        env={"P06_SECRET_ENV": "s3cret-env-value"},
    )
    try:
        rcpt = receipt.from_native_session(session, "figure.py")
        blob = repr(session.public_state()) + repr(session.diagnostics()) + repr(rcpt.to_payload())
        blob += repr(rcpt.public_facts()) + rcpt.public_identity() + repr(rcpt.launch_context)
        assert "s3cret" not in blob
        assert session.public_state()["owner"] == "desktop"
        assert session.public_state()["arg_count"] == 2
        ctx = rcpt.launch_context
        assert (ctx["owner"], ctx["argv_count"], ctx["run_config"]) == ("desktop", 2, "rc_p06test")
        assert ctx["env_keys"] == ["P06_SECRET_ENV"] and ctx["cwd_origin"] == "desktop.cwd"
        assert rcpt.spec_stable["owner"] == "desktop" and rcpt.spec_stable["argv"] == []
        assert "s3cret" not in repr(session.spec)  # repr 也不带值
    finally:
        session.cancel_owned()


# ---------------------------------------------------------------------------
# 只动自己的组：外部进程不被牵连；组外逃走的子进程不是我们纳管的
# ---------------------------------------------------------------------------
@POSIX_ONLY
def test_cancel_touches_only_the_owned_group_never_an_unrelated_process(tmp_path):
    """外部进程（同一个父进程 = 本测试进程，另一个进程组）与脚本自己 `setsid` 走的孙进程，取消后都还活着。

    判据的主语是「取消发给了谁」：本进程自己的 pgid。按 PPID / 进程名猜的实现会把这两个一起带走。
    """
    bystander = subprocess.Popen(  # noqa: S603 — 外部服务的替身：与 Tavotto 无关的进程
        [sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True
    )
    session = dk.start(
        tmp_path,
        """\
import subprocess, sys, time
escaped = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True
)
open("escaped.pid", "w").write(str(escaped.pid))
open("ready", "w").write("1")
time.sleep(600)
""",
    )
    escaped_pid = 0
    try:
        work = tmp_path / "work"
        dk.wait_until(lambda: (work / "ready").exists(), what="脚本就绪")
        escaped_pid = int((work / "escaped.pid").read_text())

        assert session.cancel_owned().confirmed
        assert bystander.poll() is None and dk.pid_alive(bystander.pid), "取消牵连了外部进程"
        assert dk.pid_alive(escaped_pid), "取消杀了不属于纳管范围的进程（按 PPID 猜了）"
    finally:
        bystander.kill()
        bystander.wait()
        if escaped_pid:
            os.kill(escaped_pid, 9)


# ---------------------------------------------------------------------------
# 编辑只在进程活着时；不为进入编辑重跑脚本（AC-17 / EX13）
# ---------------------------------------------------------------------------
def test_after_the_process_ends_editing_is_unavailable_and_the_script_never_reruns(tmp_path):
    session = dk.start(
        tmp_path,
        dk.FIGURE_HEADER
        + """\
with open("runs.log", "a") as f:
    f.write("run\\n")
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title("T")
plt.show()
""",
    )
    nativekit.wait_state(session, [nativesession.BARRIER])
    stem = list(session.ensure_built()["stems"])[0]
    gid = _title_gid(session, stem)
    session.override(stem, [{"gid": gid, "prop": "fontsize", "value": 20}])  # 活着时可编辑
    dk.finish(session)

    for call in (
        lambda: session.override(stem, [{"gid": gid, "prop": "fontsize", "value": 9}]),
        lambda: session.export(stem, [], str(tmp_path / "late.pdf")),
        session.ensure_built,
    ):
        with pytest.raises(RunError) as err:
            call()
        assert err.value.code == runcodes.NATIVE_SESSION_ENDED
    assert (tmp_path / "work" / "runs.log").read_text() == "run\n", "为了编辑把用户脚本又跑了一遍"
    assert session.public_state()["editable"] is False


# ---------------------------------------------------------------------------
# 对上层仍是 Worker-like：live route + enginesession.resolve，不新建第二个 router
# ---------------------------------------------------------------------------
def test_the_owned_session_is_routed_by_the_existing_resolver(tmp_path):
    from tavotto.engine import enginesession

    session = dk.start(tmp_path, BARRIER_SCRIPT)
    nativekit.wait_state(session, [nativesession.BARRIER])
    session.ensure_built()
    assert nativesession.REGISTRY.bind_assets(session) == []
    desc = session.descriptors[0]
    got = enginesession.resolve(
        project_root=session.project_root,
        script=desc["script"],
        entry="__main__",
        stem=desc["stem"],
        execution_profile=enginesession.PROFILE_NATIVE,
    )
    assert got is session and enginesession.is_native(got)
    for member in enginesession.WORKER_LIKE:
        assert hasattr(got, member), f"owned 会话缺 Worker-like 成员 {member}"
    session.cancel_owned()
    with pytest.raises(RunError) as err:  # 结束后路由随之失效：不退回 safe
        enginesession.resolve(
            project_root=session.project_root,
            script=desc["script"],
            entry="__main__",
            stem=desc["stem"],
            execution_profile=enginesession.PROFILE_NATIVE,
        )
    assert err.value.code == runcodes.NATIVE_SESSION_OFFLINE


# ---------------------------------------------------------------------------
# Codex P2：登记失败时，已 spawn 的子进程必须收掉，租约不泄漏
# ---------------------------------------------------------------------------
def test_adoption_failure_cancels_the_spawned_child_and_releases_the_lease(tmp_path, monkeypatch):
    seen = {}

    def boom(session):
        seen["session"] = session
        raise RunError(runcodes.NATIVE_SESSION_CONFLICT)

    monkeypatch.setattr(nativesession.REGISTRY, "adopt", boom)
    launch = dk.launch_for(
        tmp_path,
        'open("pid", "w").write(str(__import__("os").getpid()))\nimport time; time.sleep(600)\n',
    )
    with pytest.raises(RunError) as err:
        desktopproc.start(launch)
    assert err.value.code == runcodes.NATIVE_SESSION_CONFLICT
    session = seen["session"]
    assert session.owned.finished, "登记失败后所有者没有收掉子进程"
    assert not dk.pid_alive(session.owned.pid)
    assert session.transport.closed, "传输没关"
    assert envlease.native_sessions_on(launch.spec.interpreter) == []


def test_a_reused_launch_id_is_refused_before_it_can_touch_the_first_sessions_lease(tmp_path):
    first = dk.start(tmp_path / "a", RUNNING_SCRIPT, launch_id="dup")
    dk.wait_until(lambda: (tmp_path / "a" / "work" / "ready").exists(), what="脚本就绪")
    with pytest.raises(RunError) as err:
        dk.start(tmp_path / "b", 'open("ran", "w").write("1")\n', launch_id="dup")
    assert err.value.code == runcodes.NATIVE_SESSION_CONFLICT
    assert not (tmp_path / "b" / "work" / "ran").exists(), "冲突的启动还是跑了用户代码"
    assert dk.pid_alive(first.owned.pid) and dk.holds_lease(first), "第一条的租约被第二次启动牵连"
    first.cancel_owned()


# ---------------------------------------------------------------------------
# Codex r4254594336：launch_id 保留到上一任所有者真正 finished；原子占位
# ---------------------------------------------------------------------------
def test_a_launch_id_stays_reserved_until_the_previous_owner_finished(tmp_path, monkeypatch):
    import threading

    gate = threading.Event()
    real = desktopproc.DesktopProcess._settle_group

    def slow(self):
        gate.wait(30)
        real(self)

    monkeypatch.setattr(desktopproc.DesktopProcess, "_settle_group", slow)
    first = dk.start(tmp_path / "a", RUNNING_SCRIPT, launch_id="dup")
    dk.wait_until(lambda: (tmp_path / "a" / "work" / "ready").exists(), what="脚本就绪")
    t = threading.Thread(target=first.cancel_owned, daemon=True)
    t.start()
    # 控制通道 EOF 先把会话落成终态，而所有者还在清组、仍持租约
    dk.wait_until(lambda: first.state in nativesession.TERMINAL_STATES, what="会话先落终态")
    assert not first.owned.finished and dk.holds_lease(first)
    try:
        with pytest.raises(RunError) as err:
            dk.start(tmp_path / "b", 'open("ran", "w").write("1")\n', launch_id="dup")
        assert err.value.code == runcodes.NATIVE_SESSION_CONFLICT
        assert not (tmp_path / "b" / "work" / "ran").exists()
    finally:
        gate.set()
        t.join(30)
    assert first.owned.finished and not dk.holds_lease(first)


def test_concurrent_starts_with_the_same_launch_id_admit_exactly_one(tmp_path):
    import threading

    results = []

    def go(tag):
        try:
            results.append(dk.start(tmp_path / tag, RUNNING_SCRIPT, launch_id="race"))
        except RunError as exc:
            results.append(exc)

    ts = [threading.Thread(target=go, args=(t,)) for t in ("x", "y", "z")]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    started = [r for r in results if not isinstance(r, RunError)]
    refused = [r for r in results if isinstance(r, RunError)]
    try:
        assert len(started) == 1 and len(refused) == 2, results
        assert all(r.code == runcodes.NATIVE_SESSION_CONFLICT for r in refused)
        assert envlease.native_sessions_on(started[0].interpreter) == [started[0].session_id]
    finally:
        for s in started:
            s.cancel_owned()


# ---------------------------------------------------------------------------
# Codex r4254594346：判失败之前先等传输层的最后一帧
# ---------------------------------------------------------------------------
def test_owner_exit_waits_for_the_buffered_exit_frame_before_judging_failure(tmp_path):
    import json
    import socket
    import threading

    a, b = socket.socketpair()
    session = nativesession.NativeSession(
        session_id="native-owned-drain",
        descriptor={"native_id": "drain", "out_dir": str(tmp_path), "metadata": {}},
    )
    session.transport = nativesession.NativeTransport(
        a, on_event=session._on_frame, on_close=session._on_transport_closed
    )
    session._set_state(nativesession.WAITING_FOR_FIGURE)
    session.transport.start()

    def late_exit_frame():
        time.sleep(0.4)  # 所有者的等待线程先到，读线程的最后一帧还没处理
        b.sendall((json.dumps({"bridge_event": "exit", "code": 0, "figures": 1}) + "\n").encode())
        b.close()

    threading.Thread(target=late_exit_frame, daemon=True).start()
    session._on_owner_exit(0, cancelled=False)
    assert session.state == nativesession.ENDED, f"误报失败: {session.terminal_error}"
    assert session.terminal_error is None and session.exit_code == 0


# ---------------------------------------------------------------------------
# Codex r4254641150：取消未确认时占位不放；租约键每次启动唯一
# ---------------------------------------------------------------------------
def test_an_unconfirmed_cleanup_keeps_the_launch_id_reserved_until_the_owner_finished(
    tmp_path, monkeypatch
):
    import threading

    gate = threading.Event()
    real = desktopproc.DesktopProcess._settle_group

    def stuck(self):
        gate.wait(60)
        real(self)

    monkeypatch.setattr(desktopproc.DesktopProcess, "_settle_group", stuck)
    monkeypatch.setattr(
        nativesession.REGISTRY,
        "adopt",
        lambda s: (_ for _ in ()).throw(RunError(runcodes.NATIVE_SESSION_CONFLICT)),
    )
    policy = desktopproc.OwnedPolicy(term_grace=0.3, kill_timeout=0.3, linger_grace=0.0)
    launch = dk.launch_for(tmp_path / "a", RUNNING_SCRIPT, launch_id="stuck", policy=policy)
    try:
        with pytest.raises(RunError):
            desktopproc.start(launch)  # 登记失败 -> cancel 清组超时（未确认）
        monkeypatch.undo()
        with pytest.raises(RunError) as err:  # 同 id 重试必须被拒
            dk.start(tmp_path / "b", 'open("ran", "w").write("1")\n', launch_id="stuck")
        assert err.value.code == runcodes.NATIVE_SESSION_CONFLICT
        assert not (tmp_path / "b" / "work" / "ran").exists()
    finally:
        gate.set()
    # 第一任确认结束之后占位才放：同 id 可以再启动
    dk.wait_until(
        lambda: "stuck" not in nativesession.REGISTRY._reserved,  # noqa: SLF001
        timeout=30,
        what="所有者完成后释放占位",
    )
    again = dk.start(tmp_path / "c", RUNNING_SCRIPT, launch_id="stuck")
    again.cancel_owned()


def test_each_launch_has_its_own_lease_key_so_owners_cannot_delete_each_others_lease(tmp_path):
    a = dk.start(tmp_path / "a", RUNNING_SCRIPT, launch_id="same")
    a.cancel_owned()
    b = dk.start(tmp_path / "b", RUNNING_SCRIPT, launch_id="same")
    try:
        assert a.session_id != b.session_id
        assert a.launch_id == b.launch_id == "same"
        # 第一任迟到的释放（回调晚于第二任拿租约）不能删掉第二任的租约
        envlease.release_native(a.interpreter, a.session_id)
        assert dk.holds_lease(b), "第一任的释放删掉了第二任的租约"
        assert dk.install_is_refused(b.interpreter) == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION
    finally:
        b.cancel_owned()


# ---------------------------------------------------------------------------
# Codex r4254674722 / r4254674730：启动时序——回调与退出不得早于 reader / 登记完成
# ---------------------------------------------------------------------------
def test_a_fast_exit_before_the_reader_starts_is_not_reported_as_a_failure(tmp_path, monkeypatch):
    real = nativesession.NativeTransport.start

    def late_start(self):
        time.sleep(0.8)  # reader 迟迟不起：子进程早已发完 exit 帧并退出
        real(self)

    monkeypatch.setattr(nativesession.NativeTransport, "start", late_start)
    session = dk.start(tmp_path, "print('x')\n")
    assert session.owned.wait(30)
    assert session.state == nativesession.ENDED, session.terminal_error
    assert session.terminal_error is None and session.exit_code == 0


def test_the_launch_reservation_survives_until_adoption_has_finished(tmp_path, monkeypatch):
    import threading

    real = nativesession.REGISTRY.adopt

    def slow_adopt(session):
        time.sleep(0.8)  # 登记慢：快退出的子进程此时早已结束
        real(session)

    monkeypatch.setattr(nativesession.REGISTRY, "adopt", slow_adopt)
    outcome = {}

    def probe():
        time.sleep(0.4)
        try:
            nativesession.REGISTRY.reserve("early")
            outcome["admitted"] = True
            nativesession.REGISTRY.unreserve("early")
        except RunError as exc:
            outcome["code"] = exc.code

    t = threading.Thread(target=probe)
    t.start()
    session = dk.start(tmp_path, "print('x')\n", launch_id="early")
    t.join(10)
    assert outcome == {"code": runcodes.NATIVE_SESSION_CONFLICT}, "登记结束前占位被放了"
    assert session.owned.wait(30)


def test_cancel_during_startup_waits_for_the_gate_and_still_confirms(tmp_path):
    """闸门不是死锁：启动流程里随时取消，等登记走完后照常确认。"""
    session = dk.start(tmp_path, RUNNING_SCRIPT)
    assert session.cancel_owned().confirmed and session.owned.finished


# ---------------------------------------------------------------------------
# Codex r4254707207：launch_id 拼进缓存路径之前先校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "../evil",
        "..",
        "a/b",
        "a\\b",
        "/abs/path",
        "C:\\x",
        "x" * 65,
        "with space",
        "a\x00b",
        "中文",
        ".",
    ],
)
def test_a_launch_id_that_could_escape_the_cache_root_is_refused(tmp_path, bad):
    base = dk.launch_for(tmp_path, "pass\n")
    marker = tmp_path / "ran"
    with pytest.raises(RunError) as err:
        desktopproc.start(desktopproc.OwnedLaunch(base.spec, launch_id=bad))
    assert err.value.code == runcodes.DESKTOP_RUN_LAUNCH_INVALID
    assert not marker.exists() and envlease.snapshot()["native"] == {}


def test_the_default_session_dir_stays_directly_under_the_cache_root(tmp_path):
    from tavotto.engine import config

    base = dk.launch_for(tmp_path, "pass\n")
    session = desktopproc.start(desktopproc.OwnedLaunch(base.spec, launch_id="ok-ID_1"))
    try:
        root = Path(config.data_path("cache", "native")).resolve()
        assert session.out_dir.resolve().parent == root
    finally:
        session.owned.wait(30)


# ---------------------------------------------------------------------------
# Codex r4254777860：sidecar 真实清理路径收掉桌面拥有的进程，终端会话不受影响
# ---------------------------------------------------------------------------
def test_sidecar_cleanup_ends_desktop_owned_processes_but_leaves_terminal_native_alone(tmp_path):
    from tavotto import app as appmod, desktop

    term_dir = tmp_path / "term"
    term_dir.mkdir()
    nativekit.write(term_dir / "figure.py", TERMINAL_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=term_dir) as (
        term_session,
        proc,
        _,
    ):
        nativekit.wait_state(term_session, [nativesession.BARRIER])
        owned = dk.start(tmp_path / "own", RUNNING_SCRIPT)
        work = tmp_path / "own" / "work"
        dk.wait_until(lambda: (work / "ready").exists(), what="桌面脚本就绪")
        child_pid = int((work / "child.pid").read_text())

        srv = desktop.SidecarServer(
            appmod.app, desktop.DesktopState("n"), tmp_path / "hs" / "handshake.json"
        )
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        srv.announce_ready()
        srv.shutdown()
        assert srv.wait_stopped(timeout=60)
        t.join(10)

        assert owned.owned.finished and not dk.pid_alive(owned.owned.pid)
        assert not dk.pid_alive(child_pid), "sidecar 关闭后孙进程成了孤儿"
        assert not dk.holds_lease(owned)
        # 终端拥有的：只放手，脚本继续跑
        assert proc.poll() is None, "sidecar 清理误杀了终端里的脚本"
        assert term_session.state == nativesession.DETACHED
        (term_dir / "release-marker").write_text("1")
        out, _err = proc.communicate(timeout=60)
    assert proc.returncode == 0 and "TERMINAL-SCRIPT-FINISHED" in out


# ---------------------------------------------------------------------------
# Codex r4254813164：握手中、还没登记的所有者，sidecar 关停也要收
# ---------------------------------------------------------------------------
def test_shutdown_all_also_ends_owners_that_are_still_handshaking(tmp_path, monkeypatch):
    from tavotto.engine import nativerelay

    gate = threading.Event()
    real = nativerelay.NativeRelay.adopt_child

    def stalled(self, *a, **kw):
        real(self, *a, **kw)  # 子进程已认证、用户脚本已在跑……
        gate.wait(60)  # ……但卡在登记之前：会话还不在注册表里
        raise RunError(runcodes.DESKTOP_RUN_CONNECT_TIMEOUT, seconds=1)

    monkeypatch.setattr(nativerelay.NativeRelay, "adopt_child", stalled)
    box = {}

    def go():
        try:
            box["session"] = dk.start(tmp_path, RUNNING_SCRIPT)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    work = tmp_path / "work"
    try:
        dk.wait_until(lambda: (work / "ready").exists(), what="子进程已起")
        child_pid = int((work / "child.pid").read_text())
        assert nativesession.REGISTRY.list() == []  # 确实还没登记

        nativesession.REGISTRY.shutdown_all()  # sidecar 关停

        dk.wait_until(lambda: not dk.pid_alive(child_pid), timeout=20, what="孙进程消失")
    finally:
        gate.set()
        t.join(60)
    assert "session" not in box and isinstance(box.get("error"), RunError), box
    dk.wait_until(lambda: envlease.snapshot()["native"] == {}, timeout=20, what="租约释放")
    assert nativesession.REGISTRY._starting == {}  # noqa: SLF001


def test_a_registered_owned_session_cannot_be_forgotten_or_pruned_while_unfinished(tmp_path):
    s = dk.start(tmp_path, RUNNING_SCRIPT)
    dk.wait_until(lambda: (tmp_path / "work" / "ready").exists(), what="就绪")
    s.shutdown()  # 终态（detached），进程仍在
    nativesession.REGISTRY.forget(s.session_id)
    nativesession.REGISTRY.prune(keep=0)
    assert s in nativesession.REGISTRY.list(), (
        "未结束的桌面进程从注册表消失了：shutdown_all 将看不见它"
    )
    nativesession.REGISTRY.shutdown_all()
    assert s.owned.finished and not dk.pid_alive(s.owned.pid)


def test_end_start_only_removes_its_own_ticket_aba(tmp_path):
    reg = nativesession.NativeSessionRegistry()
    first = reg.begin_start("same")
    second = reg.begin_start("same")  # 同 id 重试领了新的在途登记
    reg.end_start(first)  # 第一次启动迟到的收尾
    assert reg._starting.get("same") is second, "ABA：收尾删掉了后来者的登记"  # noqa: SLF001
    reg.end_start(second)
    assert reg._starting == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# 关停与启动的交界：closing 状态、spawn 之前领登记、关停同步升级
# ---------------------------------------------------------------------------
def test_registry_closing_atomically_refuses_every_new_entry_point():
    reg = nativesession.NativeSessionRegistry()
    reg.shutdown_all()
    for call in (
        lambda: reg.reserve("x"),
        lambda: reg.begin_start("x"),
        lambda: reg.attach_owner(nativesession.StartTicket("x"), object()),
        lambda: reg.adopt(object()),
    ):
        with pytest.raises(RunError) as err:
            call()
        assert err.value.code == runcodes.NATIVE_REGISTRY_CLOSING
    reg.reopen()
    reg.reserve("x")  # 重新打开后恢复


def test_shutdown_during_the_gap_between_popen_and_owner_attach_never_leaves_a_child(
    tmp_path, monkeypatch
):
    """把 Popen 之后、挂所有者之前的窗口撑开，并发关停：启动被拒，子进程被启动线程自己收掉。"""
    gate = threading.Event()
    spawned = threading.Event()
    box = {}
    real = desktopproc._scope_for

    def slow_scope(proc, **kw):
        box["pid"] = proc.pid
        spawned.set()
        gate.wait(60)
        return real(proc, **kw)

    monkeypatch.setattr(desktopproc, "_scope_for", slow_scope)

    def go():
        try:
            box["session"] = dk.start(tmp_path, RUNNING_SCRIPT)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert spawned.wait(30)
    try:
        nativesession.REGISTRY.shutdown_all()  # 此刻进程已起、所有者还没挂：快照里是在途登记
    finally:
        gate.set()
        t.join(60)
    assert "session" not in box, "关停之后启动仍然成功了"
    assert isinstance(box.get("error"), RunError)
    assert box["error"].code == runcodes.NATIVE_REGISTRY_CLOSING
    dk.wait_until(lambda: not dk.pid_alive(box["pid"]), timeout=20, what="子进程被收掉")
    dk.wait_until(lambda: envlease.snapshot()["native"] == {}, timeout=20, what="租约释放")
    assert nativesession.REGISTRY._starting == {}  # noqa: SLF001


SIGTERM_IGNORING = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    'open("ready", "w").write("1")\n'
    "time.sleep(600)\n"
)


@POSIX_ONLY
def test_shutdown_all_returns_only_after_a_sigterm_ignoring_registered_child_is_gone(tmp_path):
    policy = desktopproc.OwnedPolicy(term_grace=0.5, kill_timeout=5.0, linger_grace=0.0)
    session = dk.start(tmp_path, SIGTERM_IGNORING, policy=policy)
    dk.wait_until(lambda: (tmp_path / "work" / "ready").exists(), what="就绪")
    pid = session.owned.pid
    nativesession.REGISTRY.shutdown_all()
    assert not dk.pid_alive(pid), "shutdown_all 返回时忽略 SIGTERM 的子进程还在"


@POSIX_ONLY
def test_shutdown_all_returns_only_after_a_sigterm_ignoring_starting_child_is_gone(
    tmp_path, monkeypatch
):
    from tavotto.engine import nativerelay

    gate = threading.Event()
    real = nativerelay.NativeRelay.adopt_child

    def stalled(self, *a, **kw):
        real(self, *a, **kw)
        gate.wait(60)  # 脚本已在跑，但卡在登记之前
        raise RunError(runcodes.DESKTOP_RUN_CONNECT_TIMEOUT, seconds=1)

    monkeypatch.setattr(nativerelay.NativeRelay, "adopt_child", stalled)
    policy = desktopproc.OwnedPolicy(term_grace=0.5, kill_timeout=5.0, linger_grace=0.0)
    box = {}

    def go():
        try:
            dk.start(tmp_path, SIGTERM_IGNORING, policy=policy)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=go, daemon=True)
    t.start()
    work = tmp_path / "work"
    dk.wait_until(lambda: (work / "ready").exists(), what="就绪")
    owner_pid = next(iter(nativesession.REGISTRY._starting.values())).owner.pid  # noqa: SLF001
    try:
        nativesession.REGISTRY.shutdown_all()
        assert not dk.pid_alive(owner_pid), "shutdown_all 返回时在途的、忽略 SIGTERM 的子进程还在"
    finally:
        gate.set()
        t.join(60)


def test_shutdown_waits_for_a_start_that_has_not_popen_yet_and_it_never_spawns(
    tmp_path, monkeypatch
):
    """begin_start 之后、Popen 之前撑开窗口并发关停：shutdown_all 等那条启动线程收尾，之后也不会出现子进程。"""
    from tavotto.engine import nativerelay

    gate, in_window = threading.Event(), threading.Event()
    spawns = []
    real_init = nativerelay.NativeRelay.__init__
    real_popen = subprocess.Popen

    def slow_init(self, *a, **kw):
        real_init(self, *a, **kw)
        in_window.set()
        gate.wait(60)  # 已领在途登记、还没 Popen

    def counting_popen(*a, **kw):
        spawns.append(1)
        return real_popen(*a, **kw)

    monkeypatch.setattr(nativerelay.NativeRelay, "__init__", slow_init)
    monkeypatch.setattr(desktopproc.subprocess, "Popen", counting_popen)
    box = {}

    def go():
        try:
            box["session"] = dk.start(tmp_path, RUNNING_SCRIPT)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    starter = threading.Thread(target=go, daemon=True)
    starter.start()
    assert in_window.wait(30)
    stopper = threading.Thread(target=nativesession.REGISTRY.shutdown_all, daemon=True)
    stopper.start()
    time.sleep(0.6)
    assert stopper.is_alive(), "shutdown_all 没等在途的启动线程就返回了"
    gate.set()
    stopper.join(60)
    starter.join(60)
    assert not stopper.is_alive()
    assert spawns == [], "关停之后启动线程仍然 Popen 了"
    assert isinstance(box.get("error"), RunError)
    assert box["error"].code == runcodes.NATIVE_REGISTRY_CLOSING
    assert envlease.snapshot()["native"] == {}


def test_a_reused_launch_id_does_not_inherit_the_previous_runs_artifacts(tmp_path):
    from tavotto.engine import figcapture

    fig_script = (
        dk.FIGURE_HEADER + 'fig, ax = plt.subplots()\nax.plot([0, 1])\nfig.savefig("A.png")\n'
    )
    first = dk.start(tmp_path / "a", fig_script, launch_id="reuse")
    dk.finish(first)
    assert figcapture.published_preview_stems(str(first.out_dir)) != []
    second = dk.start(tmp_path / "b", "print('no figure')\n", launch_id="reuse")
    assert second.owned.wait(30)
    assert second.out_dir != first.out_dir
    assert figcapture.published_preview_stems(str(second.out_dir)) == [], "继承了上一次的产物"


def test_owned_launch_accepts_no_caller_chosen_output_dir(tmp_path):
    with pytest.raises(TypeError):
        desktopproc.OwnedLaunch(dk.launch_for(tmp_path, "pass\n").spec, out_dir=str(tmp_path))


def test_a_failed_start_keeps_its_ticket_until_the_owner_finished(tmp_path, monkeypatch):
    """握手 / 登记失败后清组超时：票据留着，`shutdown_all` 仍看得见并等这个还在收尾的所有者。"""
    gate = threading.Event()
    real = desktopproc.DesktopProcess._settle_group

    def stuck(self):
        gate.wait(60)
        real(self)

    monkeypatch.setattr(desktopproc.DesktopProcess, "_settle_group", stuck)
    real_adopt = nativesession.REGISTRY.adopt
    monkeypatch.setattr(
        nativesession.REGISTRY,
        "adopt",
        lambda s: (_ for _ in ()).throw(RunError(runcodes.NATIVE_SESSION_CONFLICT)),
    )
    policy = desktopproc.OwnedPolicy(term_grace=0.3, kill_timeout=0.3, linger_grace=0.0)
    launch = dk.launch_for(tmp_path, RUNNING_SCRIPT, launch_id="tick", policy=policy)
    stopper = None
    try:
        with pytest.raises(RunError):
            desktopproc.start(launch)  # 登记失败 -> cancel 清组超时（未确认）
        assert len(nativesession.REGISTRY._starting) == 1, "未确认就撤了票据"  # noqa: SLF001
        stopper = threading.Thread(target=nativesession.REGISTRY.shutdown_all, daemon=True)
        stopper.start()
        time.sleep(0.8)
        assert stopper.is_alive(), "shutdown_all 没等仍在收尾的所有者"
    finally:
        monkeypatch.setattr(nativesession.REGISTRY, "adopt", real_adopt)
        gate.set()
        if stopper is not None:
            stopper.join(60)
    dk.wait_until(
        lambda: nativesession.REGISTRY._starting == {},  # noqa: SLF001
        timeout=30,
        what="所有者完成后撤票据",
    )


def test_run_config_reaches_the_bridge_so_asset_ids_carry_it(tmp_path):
    from tavotto.engine import figcapture

    script = (
        dk.FIGURE_HEADER
        + 'fig, ax = plt.subplots()\nax.plot([0, 1])\nfig.savefig("A.png")\nplt.show()\n'
    )
    with_cfg = dk.start(tmp_path / "a", script, argv=("--k", "v"))
    plain = dk.start(tmp_path / "b", script)
    try:
        nativekit.wait_state(with_cfg, [nativesession.BARRIER])
        nativekit.wait_state(plain, [nativesession.BARRIER])
        d1 = with_cfg.ensure_built()["descriptors"][0]
        d2 = plain.ensure_built()["descriptors"][0]
        assert d1["asset_id"].endswith(f"{figcapture.RUN_CONFIG_SEP}rc_p06test"), d1["asset_id"]
        assert d1["run_config"] == "rc_p06test"
        assert d2["asset_id"] == figcapture.runtime_asset_id(d2["script"], d2["stem"])
        assert "run_config" not in d2 and d1["asset_id"] != d2["asset_id"]
    finally:
        with_cfg.cancel_owned()
        plain.cancel_owned()


def test_two_identical_desktop_launches_have_different_receipt_ids(tmp_path):
    from tavotto.engine import receipt

    a = dk.start(tmp_path, "import time; time.sleep(600)\n", launch_id="same")
    a.cancel_owned()
    b = dk.start(tmp_path, "import time; time.sleep(600)\n", launch_id="same")
    try:
        ra, rb = (
            receipt.from_native_session(a, "figure.py"),
            receipt.from_native_session(b, "figure.py"),
        )
        assert ra.receipt_id != rb.receipt_id
    finally:
        b.cancel_owned()


@pytest.mark.parametrize("failing", ["watch", "pool", "ai"])
def test_sidecar_cleanup_still_ends_owned_processes_when_another_step_raises(
    tmp_path, monkeypatch, failing
):
    from tavotto import app as appmod, desktop

    def boom(*a, **kw):
        raise RuntimeError(f"injected {failing} failure")

    if failing == "watch":
        monkeypatch.setattr(desktop.engine_watch, "stop", boom)
    elif failing == "pool":
        monkeypatch.setattr(desktop.engine_pool, "shutdown_all", boom)
    else:
        monkeypatch.setattr(desktop.engine_ai, "interrupt_all", boom)
    owned = dk.start(tmp_path, RUNNING_SCRIPT)
    work = tmp_path / "work"
    dk.wait_until(lambda: (work / "ready").exists(), what="就绪")
    child_pid = int((work / "child.pid").read_text())
    srv = desktop.SidecarServer(
        appmod.app, desktop.DesktopState("n"), tmp_path / "hs" / "handshake.json"
    )
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    srv.announce_ready()
    srv.shutdown()
    assert srv.wait_stopped(timeout=60)
    t.join(10)
    assert owned.owned.finished and not dk.pid_alive(owned.owned.pid)
    assert not dk.pid_alive(child_pid), f"{failing} 清理抛错后桌面拥有的进程成了孤儿"
