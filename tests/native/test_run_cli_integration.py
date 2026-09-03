"""`tavotto run` 的**进程级**契约（ADR 0021 §1 / §10）。

整批真起进程：CLI 契约的主张有一半只在进程里成立——stdout 归谁、stdin 通不通、
退出码是多少、Ctrl+C 怎么走。在进程内调函数验不到任何一条。

链条：真 `tavotto run` → 假桌面 attach（走真控制面）→ 真用户 Python →
真 Bridge Runner → 真 Matplotlib Figure。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from support import nativekit
from tavotto.engine import nativehandoff, nativesession, runcodes

pytestmark = nativekit.needs_user_python


PROBE_SCRIPT = """\
import json, os, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.pdf")
print(json.dumps({
    "argv": sys.argv,
    "cwd": os.getcwd(),
    "executable": sys.executable,
    "marker": os.environ.get("TAVOTTO_TEST_MARKER"),
    "bridge_token_visible": "TAVOTTO_BRIDGE_TOKEN" in os.environ,
    "path0": sys.path[0],
    "package": __package__,
    "file": __file__,
}))
"""


def test_the_script_keeps_the_users_stdout_cwd_env_and_argv(tmp_path):
    """**一次把五条承诺量完**：argv 原样、cwd 原样、解释器是用户那个、
    env 原样继承、stdout 干净到能直接 `json.loads`。

    最后一条是 native 的核心：Tavotto 往那条流上写一个字，用户的
    `python fig.py > out.json` 就坏了。
    """
    nativekit.write(tmp_path / "figure.py", PROBE_SCRIPT)
    env_marker = "marker-42"
    os.environ["TAVOTTO_TEST_MARKER"] = env_marker
    try:
        with nativekit.product_run(
            nativekit.USER_PYTHON, "figure.py", "--sample", "A", cwd=tmp_path
        ) as (session, proc, _):
            code, out, err = nativekit.finish(session, proc)
    finally:
        os.environ.pop("TAVOTTO_TEST_MARKER", None)

    assert code == 0, err
    info = json.loads(out.strip())  # ← stdout 上**只有**脚本自己的输出
    assert info["argv"] == ["figure.py", "--sample", "A"]
    assert info["cwd"] == str(tmp_path)
    assert os.path.realpath(info["executable"]) == os.path.realpath(nativekit.USER_PYTHON)
    assert info["marker"] == env_marker, "用户的环境变量没有原样传下去"
    assert info["bridge_token_visible"] is False, "控制通道的 token 泄漏给了用户脚本"
    assert info["path0"] == str(tmp_path), "sys.path[0] 不是脚本目录（与真 python 不一致）"
    assert info["package"] is None, "__package__ 不是 None（与真 python 不一致）"


def test_run_messages_only_stderr(tmp_path):
    """**用户的 Python 起来之后**，Tavotto 自己的话全部写 stderr。

    "全部"只在这条路上成立——`--help` 归 stdout，见
    `test_help_goes_to_stdout_and_shows_the_delimiter`（issue #198）。
    """
    nativekit.write(tmp_path / "figure.py", PROBE_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        code, out, err = nativekit.finish(session, proc)
    assert code == 0, err
    assert "Tavotto Run" in err and "Beta" in err
    assert "Tavotto" not in out, f"Tavotto 的文字混进了用户的 stdout: {out!r}"
    json.loads(out.strip())


def test_quiet_silences_tavotto_but_not_the_user(tmp_path):
    nativekit.write(tmp_path / "figure.py", PROBE_SCRIPT)
    with nativekit.product_run(
        nativekit.USER_PYTHON, "figure.py", cwd=tmp_path, tavotto_args=("--quiet",)
    ) as (session, proc, _):
        code, out, err = nativekit.finish(session, proc)
    assert code == 0, err
    assert "Tavotto Run" not in err
    json.loads(out.strip())  # 用户的输出一个字节都没少


# --------------------------------------------------------------------------
EXIT_SCRIPT = """\
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.pdf")
print("BEFORE-EXIT")
sys.exit(3)
"""


def test_script_exit_code_is_passed_through(tmp_path):
    """`sys.exit(3)` → `tavotto run` 返回 3。**不是"Tavotto 失败了"。**"""
    nativekit.write(tmp_path / "figure.py", EXIT_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        code, out, err = nativekit.finish(session, proc)
    assert "BEFORE-EXIT" in out
    assert code == 3, f"退出码被改写了: {code}\n{err}"


ERROR_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.pdf")
raise ValueError("boom from the script")
"""


def test_script_error_still_delivers_the_figures(tmp_path):
    """脚本出错，但图已经画出来了：**仍然进屏障、仍然能编辑**，退出码非零。"""
    nativekit.write(tmp_path / "figure.py", ERROR_SCRIPT)
    status = tmp_path / "status.json"
    with nativekit.product_run(
        nativekit.USER_PYTHON,
        "figure.py",
        cwd=tmp_path,
        tavotto_args=("--status-file", str(status)),
    ) as (session, proc, _):
        nativekit.wait_state(session, [nativesession.BARRIER])
        build = session.ensure_built()
        assert list(build["stems"]) == ["Fig1"]
        assert session.script_error, "屏障里没有带上脚本的错误"
        assert "ValueError" in session.script_error["message"]
        code, out, err = nativekit.finish(session, proc)
    assert code != 0
    assert "ValueError: boom from the script" in err, "traceback 没有原样出现在用户终端上"
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["script_exit_code"] == code
    assert data["figures_captured"] == 1


NO_FIGURE_SCRIPT = "print('no figure here')\n"


def test_no_figure_captured_is_a_product_result_not_a_script_failure(tmp_path):
    """脚本 exit 0 但没有 Figure：**退出码仍然是 0**，产品结果记在 status file。

    折进退出码会让 `tavotto run … && next` 与直接跑 python 的行为分叉。
    """
    nativekit.write(tmp_path / "figure.py", NO_FIGURE_SCRIPT)
    status = tmp_path / "status.json"
    with nativekit.product_run(
        nativekit.USER_PYTHON,
        "figure.py",
        cwd=tmp_path,
        tavotto_args=("--status-file", str(status)),
    ) as (session, proc, _):
        code, out, err = nativekit.finish(session, proc)
    assert "no figure here" in out
    assert code == 0, err
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["script_exit_code"] == 0
    assert data["session_result"] == runcodes.NO_FIGURE_CAPTURED
    assert data["figures_captured"] == 0
    assert runcodes.NO_FIGURE_CAPTURED in err


# --------------------------------------------------------------------------
STDIN_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sample = input("sample id: ")
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ax.set_title(sample)
fig.savefig("Fig1.pdf")
print("GOT:" + sample)
"""


def test_stdin_reaches_the_script(tmp_path):
    """`input()` 拿到的是用户在终端里敲的那一行。

    **Tavotto 的控制通道不占 stdin**——它在独立的 socket 上。占了的表现是
    `input()` 当场 EOF，而用户的脚本"莫名其妙就崩了"。
    """
    nativekit.write(tmp_path / "figure.py", STDIN_SCRIPT)
    with nativekit.product_run(
        nativekit.USER_PYTHON, "figure.py", cwd=tmp_path, stdin="XPS-7\n"
    ) as (session, proc, _):
        nativekit.wait_state(session, [nativesession.BARRIER, nativesession.ENDED])
        code, out, err = nativekit.finish(session, proc)
    assert "GOT:XPS-7" in out, f"脚本没收到 stdin: {out!r}\n{err}"
    assert code == 0, err


# --------------------------------------------------------------------------
def test_module_target_end_to_end(tmp_path):
    """`python -m paper.figures.xps`：`__package__` / `__spec__` / `sys.path[0]`
    都指向真实 module，**不是 bridge_runner**。"""
    pkg = tmp_path / "paper" / "figures"
    nativekit.write(pkg.parent / "__init__.py", "")
    nativekit.write(pkg / "__init__.py", "")
    nativekit.write(
        pkg / "xps.py",
        "import json, sys\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [0, 1])\n"
        "fig.savefig('Fig1.pdf')\n"
        "print(json.dumps({'package': __package__, 'spec': __spec__.name,\n"
        "                  'path0': sys.path[0], 'file': __file__, 'argv': sys.argv[1:]}))\n"
        "plt.show()\n",
    )
    with nativekit.product_run(
        nativekit.USER_PYTHON, "-m", "paper.figures.xps", "--sample", "A", cwd=tmp_path
    ) as (session, proc, _):
        nativekit.wait_state(session, [nativesession.BARRIER])
        build = session.ensure_built()
        desc = session.descriptors[0]
        assert desc["script"].endswith("xps.py"), desc["script"]
        assert "bridge_runner" not in desc["script"], "asset 挂到了 runner 自己身上"
        assert "bridge_runner" not in desc["asset_id"]
        assert list(build["stems"]) == ["Fig1"]
        code, out, err = nativekit.finish(session, proc)
    info = json.loads(out.strip())
    assert info["package"] == "paper.figures"
    assert info["spec"] == "paper.figures.xps"
    assert info["path0"] == str(tmp_path), "真实 `-m` 把 cwd 放在 sys.path[0]"
    assert info["argv"] == ["--sample", "A"]
    assert code == 0, err


# --------------------------------------------------------------------------
SENTINEL_SCRIPT = """\
import pathlib
pathlib.Path("RAN").write_text("yes", encoding="utf-8")
print("SHOULD-NOT-HAPPEN")
"""


def test_not_a_single_line_runs_before_the_user_confirms(tmp_path):
    """**顺序就是产品语义**（ADR 0021 §7）：用户点取消 → 脚本一行都没跑。

    反过来做（先跑起来再找 UI）的表现是：脚本已经写了文件、发了请求、
    跑了半小时，然后 Tavotto 才说"没有桌面应用"。
    """
    nativekit.write(tmp_path / "figure.py", SENTINEL_SCRIPT)
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path, attach=False) as (
        _session,
        proc,
        native_id,
    ):
        time.sleep(0.5)
        assert not (tmp_path / "RAN").exists(), "确认之前脚本就跑了"
        nativehandoff.cancel(native_id)  # ← 用户点了「取消」
        out, err = proc.communicate(timeout=120)
        code = proc.returncode
    assert not (tmp_path / "RAN").exists(), "取消之后脚本还是跑了"
    assert "SHOULD-NOT-HAPPEN" not in out
    assert code == runcodes.EXIT_CANCELLED, f"取消的退出码不对: {code}\n{err}"
    assert runcodes.NATIVE_ATTACH_CANCELLED in err or "取消" in err


# --------------------------------------------------------------------------
def test_usage_errors_exit_two_and_run_nothing(tmp_path):
    """invocation 层的失败**固定退出码 2**，写 stderr，而且什么都没起。

    与 `test_help_goes_to_stdout_and_shows_the_delimiter` **是一对，必须一起
    看**（ADR 0021 §10.1）：用法文本有两个流向——`--help` 是用户要的输出
    （stdout / 退 0），用法错误是用户没要的诊断（stderr / 退 2）。只钉一边，
    下一个人就会把两种情况改成同一个流向（issue #198）。
    """
    nativekit.write(tmp_path / "figure.py", SENTINEL_SCRIPT)
    cases = [
        (["run", "python", "figure.py"], runcodes.RUN_COMMAND_MISSING),  # 缺 `--`
        (["run", "--", "make", "all"], runcodes.UNSUPPORTED_RUN_COMMAND),
        (["run", "--", "python", "-c", "print(1)"], runcodes.UNSUPPORTED_PYTHON_OPTION),
        (["run", "--", "python", "nope.py"], runcodes.SCRIPT_TARGET_MISSING),
    ]
    for argv, code in cases:
        res = nativekit.run_cli(*argv, cwd=tmp_path)
        assert res.returncode == runcodes.EXIT_USAGE, f"{argv}: {res.returncode}\n{res.stderr}"
        assert res.stdout == "", f"{argv}: 失败信息写到了 stdout: {res.stdout!r}"
        assert _stable_prefix(code) in res.stderr, f"{argv}: {res.stderr}"
    assert not (tmp_path / "RAN").exists()


def test_an_unknown_option_before_the_delimiter_goes_to_stderr(tmp_path):
    """`--` **左边**不认识的选项：用法文本走 stderr、退 2、stdout 一个字节都没有。

    单列一条是因为它是 `usage_text()` 的**第三个**消费点（另外两个是 `--help`
    与 `_fail(with_usage=True)`），而且是最容易被"顺手统一"到 stdout 的那个
    ——它印的正是同一段文字。
    """
    nativekit.write(tmp_path / "figure.py", SENTINEL_SCRIPT)
    res = nativekit.run_cli("run", "--nope", "--", "python", "figure.py", cwd=tmp_path)
    assert res.returncode == runcodes.EXIT_USAGE, f"{res.returncode}\n{res.stderr}"
    assert res.stdout == "", f"用法错误写到了 stdout: {res.stdout!r}"
    assert "--nope" in res.stderr and "tavotto run" in res.stderr, res.stderr
    assert not (tmp_path / "RAN").exists()


def _stable_prefix(code: str) -> str:
    """错误文案里第一个占位符之前的那一段。

    判据挂在**文案模板**上而不是整句：整句要靠用例自己填占位符，那等于把
    文案抄了第二份；而只判 code 串又验不到"用户看到的那句话对不对"
    （code 是给机器的，它不出现在 CLI 的输出里）。
    """
    return runcodes.MESSAGES[code]["zh"].split("{", 1)[0]


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_goes_to_stdout_and_shows_the_delimiter(tmp_path, flag):
    """`--help` 是**被请求的输出**：stdout 非空、stderr 为空、退 0（issue #198）。

    "Tavotto 的话全写 stderr"守的是"stdout 归用户程序"，而这条路上根本没有
    用户程序——它在解析阶段就返回，一个子进程都没起。写反了的后果是
    `tavotto run --help | less` 与 `> help.txt` 在用户那儿都是空的（2026-08-28
    在真 Windows 产物上实测到）。另一半在
    `test_usage_errors_exit_two_and_run_nothing`：**两条一起钉**。
    """
    res = nativekit.run_cli("run", flag, cwd=tmp_path)
    assert res.returncode == 0, f"{res.returncode}\n{res.stderr}"
    assert res.stdout.strip(), "帮助没进 stdout（`| less` / `> help.txt` 会是空的）"
    assert res.stderr == "", f"帮助写到了 stderr: {res.stderr!r}"
    assert "--" in res.stdout and "tavotto run" in res.stdout
    assert "--x-" not in res.stdout, "内部测试 flag 出现在正式 help 里"


# --------------------------------------------------------------------------
LONG_SCRIPT = """\
import pathlib, sys, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("Fig1.pdf")
print("READY", flush=True)
# **同步点用文件不用 stdout**：stdout 是一条管道，测试进程要等 communicate()
# 才读得到；靠它判"可以发信号了"就会在 import matplotlib 那几秒里提前发出去，
# 于是量到的是"import 期间被打断"而不是"运行期间被打断"。
pathlib.Path("READY").write_text("y", encoding="utf-8")
try:
    time.sleep(120)
except KeyboardInterrupt:
    print("INTERRUPTED", flush=True)
    sys.exit(130)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 进程组信号语义")
def test_ctrl_c_reaches_the_script_and_leaves_no_orphan(tmp_path):
    """终端里的 Ctrl+C **送给整个前台进程组**，CLI 不吞、不抢在孩子前面退出。

    抢先退出的表现是：终端回到提示符了，而用户的 Python 还在后台跑——
    他不会知道，也没有地方去关它。
    """
    nativekit.write(tmp_path / "figure.py", LONG_SCRIPT)
    before = nativekit.pending_ids()
    proc = subprocess.Popen(  # noqa: S603
        nativekit.cli_argv("run", "--x-no-desktop", "--", nativekit.USER_PYTHON, "figure.py"),
        cwd=str(tmp_path),
        env=nativekit.cli_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,  # 自己一个进程组 = 模拟"前台作业"
    )
    session = None
    try:
        native_id = nativekit.wait_for_pending(before)
        session = nativekit.attach_as_desktop(native_id)
        nativekit.wait_state(session, [nativesession.WAITING_FOR_FIGURE])
        child_pid = session.process_pid
        assert child_pid, "没拿到用户 Python 的 pid"
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not (tmp_path / "READY").exists():
            time.sleep(0.05)
        assert (tmp_path / "READY").exists(), "脚本没跑到 sleep 那一步"
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)  # ← 用户按了 Ctrl+C
        out, err = proc.communicate(timeout=90)
    finally:
        if session is not None:
            session.shutdown()
            nativesession.REGISTRY.forget(session.session_id)
        if proc.poll() is None:  # pragma: no cover - 只在实现挂了时走到
            proc.kill()
            proc.wait(timeout=10)
    assert "READY" in out
    assert "INTERRUPTED" in out, f"用户脚本没收到 KeyboardInterrupt: {out!r}\n{err}"
    assert proc.returncode == 130, f"没有透传脚本的退出码: {proc.returncode}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _alive(child_pid):
        time.sleep(0.05)
    assert not _alive(child_pid), f"孤儿进程留下了: pid={child_pid}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - 别人的进程，不在本用例范围
        return True
    return True


# --------------------------------------------------------------------------
def test_terminate_gives_a_deterministic_exit_code(tmp_path):
    """用户在 UI 里点"终止脚本"：退出码固定 5，**不伪装成正常 continue**。"""
    nativekit.write(
        tmp_path / "figure.py",
        nativekit.FIGURE_SCRIPT + "\nimport matplotlib.pyplot as plt\nplt.show()\nprint('AFTER')\n",
    )
    with nativekit.product_run(nativekit.USER_PYTHON, "figure.py", cwd=tmp_path) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        session.ensure_built()
        session.terminate()
        out, err = proc.communicate(timeout=120)
    assert "AFTER" not in out, "terminate 之后脚本还接着往下跑了"
    assert proc.returncode == runcodes.EXIT_TERMINATED, f"{proc.returncode}\n{err}"


def test_the_runner_and_the_cli_agree_on_the_terminate_code():
    """**严格同源对**：`bridge_runner.py` 被用户的解释器按路径执行，
    import 不到 `tavotto.*`，所以那个常量只能各写一份——由这条逐字节对拍。"""
    from tavotto.engine import bridge_runner

    assert bridge_runner.TERMINATE_EXIT == runcodes.EXIT_TERMINATED
