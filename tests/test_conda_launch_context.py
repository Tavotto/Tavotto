"""P09：复用成功启动上下文 + Conda 激活 launcher（EX07 / EX11，AC-11 / AC-19）。

**这里没有真实 Conda。** 本机（和 CI 的 Linux 腿）没有 conda / mamba / micromamba，所以「真实受支持 Conda 启动」那一档
是 `not_run`（见 `test_real_conda_launch`，无 conda 时 skip）。其余用例用一个**模拟 conda**：一个按官方 `conda run` 文档
（<https://docs.conda.io/projects/conda/en/stable/commands/run.html>）语义写的小脚本——认 `run` / `--prefix|-p` /
`--no-capture-output|-s|--live-stream` / `--cwd` / `--`，**没带 `--no-capture-output` 就拒绝**（真 conda 会捕获 stdout，
worker 协议死锁），激活 = 往子进程环境里放 `CONDA_PREFIX` 和一个只有激活后才有的「SDK 许可串」，然后以继承的 stdio
运行命令。它证明的是 Tavotto 的 argv / 进程树 / 持久化行为，**不证明**真 conda 的行为。

Windows 的 `conda.exe` / DLL 搜索路径 / 厂商软件：`not_run`（POSIX-only 夹具）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import (
    databinding,
    launchctx,
    launcher,
    pool as _pool,
    receipt,
    runconfig,
)

ROOT = Path(__file__).resolve().parent.parent
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="模拟 conda 夹具只做 POSIX（Windows not_run）"
)

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None
needs_worker = pytest.mark.skipif(
    WORKER_PY is None or os.name == "nt", reason="需要装有 matplotlib 的解释器（POSIX）"
)

LICENSE = "activated-secret-xyz"

FAKE_CONDA = r"""#!{python}
import json, os, subprocess, sys

args = sys.argv[1:]
if not args or args[0] != "run" or "--" not in args:
    sys.exit(2)
sep = args.index("--")
opts, cmd = args[1:sep], args[sep + 1 :]
live, prefix, cwd, i = False, None, None, 0
while i < len(opts):
    o = opts[i]
    if o in ("--no-capture-output", "-s", "--live-stream"):
        live = True
    elif o in ("--prefix", "-p"):
        i += 1
        prefix = opts[i]
    elif o == "--cwd":
        i += 1
        cwd = opts[i]
    else:
        sys.exit(2)
    i += 1
if not live:
    sys.exit(97)  # 真 conda 会捕获 stdout：worker 协议在 stdout 上，必须直通
with open(os.environ["FAKE_CONDA_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"opts": opts, "cmd": cmd}}) + "\n")
env = dict(os.environ, CONDA_PREFIX=prefix, FAKE_SDK_LICENSE="{license}")
site = os.path.join(prefix, "activated_site")  # 激活才会加进 PYTHONPATH 的目录（activate.d 设置变量的效果）
if os.path.isdir(site):
    env["PYTHONPATH"] = site + os.pathsep + env.get("PYTHONPATH", "")
if cwd:
    os.chdir(cwd)
banner = os.environ.get("FAKE_CONDA_BANNER")
if banner:  # activate.d 钩子往 stdout 打印的横幅：在用户 Python 启动之前、直通进协议管道
    sys.stdout.write(banner + ("" if os.environ.get("FAKE_CONDA_BANNER_NO_NEWLINE") else "\n"))
    sys.stdout.flush()
# 真 conda（POSIX）：先起一个临时 shell，再由 shell 起用户 Python——Python 的父进程是 shell，不是 conda
sys.exit(subprocess.Popen(["/bin/sh", "-c", '"$@"; exit $?', "sh", *cmd], env=env).wait())
"""


def make_fake_conda(tmp_path: Path, real_python: str) -> tuple[Path, Path, Path]:
    """root/bin/conda（模拟）+ root/envs/e1（prefix，bin/python 是转发到 `real_python` 的脚本）。
    回 (prefix, 模拟 conda, 日志文件)。"""
    root = tmp_path / "miniconda"
    prefix = root / "envs" / "e1"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "conda-meta" / "history").write_text("==> 2026-10-13 <==\n", encoding="utf-8")
    (prefix / "bin").mkdir()
    py = prefix / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{real_python}" "$@"\n', encoding="utf-8")
    (root / "bin").mkdir(parents=True)
    conda = root / "bin" / "conda"
    conda.write_text(FAKE_CONDA.format(python=sys.executable, license=LICENSE), encoding="utf-8")
    for p in (py, conda):
        os.chmod(p, 0o755)
    return prefix, conda, tmp_path / "conda.log"


def conda_calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def alive(pid: int) -> bool:
    if (
        os.name == "nt"
    ):  # Windows 上 os.kill(pid, 0) 会终止进程（#523）；这些用例本来就只在 POSIX 上跑
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_dead(pid: int, timeout: float = 8.0) -> bool:
    """`pid` 不再**运行**（僵尸不算运行：它不再执行任何代码，只是还没被父进程收尸）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not launcher.is_running(pid):
            return True
        time.sleep(0.05)
    return not launcher.is_running(pid)


def wait_gone(pid: int, timeout: float = 8.0) -> bool:
    """`pid` 完全消失（被父进程回收，而不是留成僵尸）。PID 1 不收尸的容器里，只有「父进程先收尸」的终止顺序能做到。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


# ---------------------------------------------------------------------------
# launcher：结构化 argv，不拼 shell
# ---------------------------------------------------------------------------
def test_wrap_is_an_argv_array_with_documented_conda_run_shape():
    lau = launcher.CondaLauncher(
        conda_exe="/opt/c onda/bin/conda", prefix="/opt/c onda/envs/my env"
    )
    cmd = ["/opt/c onda/envs/my env/bin/python", "w.py", "--arg", "a b", "--", "$(rm -rf ~)", ""]
    out = lau.wrap(cmd)
    assert out[:5] == [
        "/opt/c onda/bin/conda",
        "run",
        "--no-capture-output",
        "--prefix",
        "/opt/c onda/envs/my env",
    ]
    sep = out.index("--")
    assert (
        out[sep + 1 :] == cmd
    )  # `--` 之后原样：空格 / 引号 / 命令替换 / 空串 / 内嵌 `--` 都不被解释
    assert out[5:sep] == []  # 没有 cwd 时 conda 选项只有这几个
    with_cwd = lau.wrap(cmd, cwd="/tmp/work dir")
    assert with_cwd[5:7] == ["--cwd", "/tmp/work dir"] and with_cwd.index("--") == 7
    assert all(isinstance(t, str) for t in out)


def test_public_reference_has_no_environment(tmp_path):
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    assert set(lau.public()) == {"kind", "conda_exe", "prefix"}
    assert not hasattr(launcher, "from_public")  # 引用只对账，不提供「读回来就执行」的入口


@posix_only
def test_detect_conda_env_and_negative_controls(tmp_path, monkeypatch):
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    found = launcher.detect(prefix / "bin" / "python")
    assert found == launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    # 不是 Conda 环境（没有 conda-meta）：裸起
    plain = tmp_path / "venv" / "bin"
    plain.mkdir(parents=True)
    (plain / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    assert launcher.detect(plain / "python") is None
    # 找不到 conda：裸起，不报错
    lone = tmp_path / "lone"
    (lone / "conda-meta").mkdir(parents=True)
    (lone / "bin").mkdir()
    (lone / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert launcher.detect(lone / "bin" / "python") is None
    # 显式关闭
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launcher.detect(prefix / "bin" / "python") is None


@posix_only
def test_conda_bat_is_not_used(tmp_path, monkeypatch):
    """Windows 的 conda.bat 需要 cmd.exe 引号规则：不拼 shell，所以不采用（只认真正的可执行文件）。"""
    prefix = tmp_path / "root" / "envs" / "e"
    (prefix / "conda-meta").mkdir(parents=True)
    bat = tmp_path / "bin" / "conda.bat"
    bat.parent.mkdir()
    bat.write_text("@echo off\n", encoding="utf-8")
    bat.chmod(0o755)
    monkeypatch.setenv("CONDA_EXE", str(bat))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert launcher.find_conda_exe(str(prefix)) is None


@posix_only
def test_prefix_state_changes_on_a_conda_transaction_not_on_reads(tmp_path):
    prefix, _, _ = make_fake_conda(tmp_path, sys.executable)
    before = launcher.prefix_state(str(prefix))
    assert before and launcher.prefix_state(str(prefix)) == before
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install numpy\n")
    assert launcher.prefix_state(str(prefix)) != before


# ---------------------------------------------------------------------------
# 进程树：取消 wrapper 不留下仍运行的用户 Python
# ---------------------------------------------------------------------------
CHILD = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); print('up',flush=True); time.sleep(300)"


def _spawn_wrapped(tmp_path, popen_cls):
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    pidfile = tmp_path / "child.pid"
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    proc = popen_cls(
        lau.wrap([sys.executable, "-c", CHILD, str(pidfile)]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, "FAKE_CONDA_LOG": str(log)},
        text=True,
    )
    assert proc.stdout.readline().strip() == "up"  # stdout 直通（--no-capture-output）
    return proc, int(pidfile.read_text())


@posix_only
def test_killing_the_wrapper_group_leaves_no_user_python(tmp_path):
    proc, child = _spawn_wrapped(tmp_path, launcher.GroupPopen)
    assert alive(child)
    proc.kill()
    assert wait_gone(child), "kill() 之后用户 Python 仍在（或成了没人收的僵尸）"  # 不靠 wait() 补刀
    proc.wait(timeout=10)
    for fh in (proc.stdin, proc.stdout):
        fh.close()


@posix_only
def test_negative_control_plain_popen_kill_orphans_the_user_python(tmp_path):
    """移除修复的对照：普通 Popen 只杀 conda，用户 Python 成孤儿继续跑——这正是 GroupPopen 要消除的。"""
    proc, child = _spawn_wrapped(tmp_path, subprocess.Popen)
    try:
        proc.kill()
        proc.wait(timeout=10)
        time.sleep(0.5)
        assert alive(child)
    finally:
        os.kill(child, signal.SIGKILL)
        for fh in (proc.stdin, proc.stdout):
            fh.close()


@posix_only
def test_wait_after_wrapper_exit_reaps_a_surviving_group_member(tmp_path):
    """conda 先死、孙进程活着（kill 之外的退出路径）：wait() 也要确认组里没有残留。"""
    proc, child = _spawn_wrapped(tmp_path, launcher.GroupPopen)
    os.kill(proc.pid, signal.SIGKILL)  # 只杀 wrapper
    proc.wait(timeout=10)
    assert wait_dead(child)
    for fh in (proc.stdin, proc.stdout):
        fh.close()


# ---------------------------------------------------------------------------
# 池：真 worker 经模拟 conda 激活启动；取消 / 关停不留进程
# ---------------------------------------------------------------------------
SDK_SCRIPT = """\
import os, sys, json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

if os.environ.get("FAKE_SDK_LICENSE") != {license!r}:
    raise RuntimeError("SDK needs the activated environment")
args = sys.argv[1:]
scale = float(args[args.index("--scale") + 1]) if "--scale" in args else 1.0
with open({counter!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"argv": args, "conda_prefix": os.environ.get("CONDA_PREFIX")}}) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [v * scale for v in (1.0, 2.0, 4.0)])
fig.savefig("result.pdf")
"""


@pytest.fixture
def conda_world(tmp_path, monkeypatch):
    prefix, conda, log = make_fake_conda(tmp_path, WORKER_PY or sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    counter = tmp_path / "runs.jsonl"
    (proj / "fig.py").write_text(
        SDK_SCRIPT.format(license=LICENSE, counter=str(counter)), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(prefix / "bin" / "python"))
    monkeypatch.delenv("CONDA_EXE", raising=False)
    _pool.reset_worker_python()  # 模块级缓存：别的用例选定的解释器不能替这个用例作答
    yield proj, prefix, log, counter
    _pool.shutdown_all(wait=True)
    _pool.reset_worker_python()


def runs(counter: Path) -> list[dict]:
    if not counter.exists():
        return []
    return [json.loads(x) for x in counter.read_text(encoding="utf-8").splitlines() if x]


@needs_worker
def test_activation_dependent_script_runs_only_through_the_launcher_and_shutdown_leaves_nothing(
    conda_world, monkeypatch
):
    proj, prefix, log, counter = conda_world
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        assert worker.launcher is not None and worker.launcher.prefix == str(prefix)
        # 预检（`_has_matplotlib`）也经 conda run 起；worker 自己那一次是命令里带 worker.py 的
        (call,) = [c for c in conda_calls(log) if any(a.endswith("worker.py") for a in c["cmd"])]
        assert call["opts"] == ["--no-capture-output", "--prefix", str(prefix)]
        assert call["cmd"][0] == str(prefix / "bin" / "python")  # wrapper 之后仍是用户的解释器路径
        assert runs(counter)[0]["conda_prefix"] == str(prefix)
        user_pid = resp["runtime"]["pid"]  # worker 自报：真正跑脚本的用户 Python
        assert user_pid != worker.child_pid  # child_pid 是 conda wrapper
        assert alive(user_pid)
        rcpt_launcher = worker.launcher.public()
        assert rcpt_launcher["kind"] == "conda"
        # 用户 Python 的父进程是 conda 起的临时 shell（不是 child_pid）：回执仍要完整，pid 经进程组核
        rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
        assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE, rcpt.runtime_rejected
        assert rcpt.pid_check == receipt.PID_CHECK_PROCESS_GROUP
        assert resp["runtime"]["ppid"] not in (worker.child_pid, user_pid)
    finally:
        worker.force_kill()
    assert wait_dead(user_pid), "worker 关停后用户 Python 仍在运行"
    assert not alive(worker.child_pid) or wait_dead(worker.child_pid)


@needs_worker
def test_without_the_launcher_the_same_script_fails(conda_world, monkeypatch):
    """负对照：关掉 Conda 激活，同一份脚本在裸起的同一个解释器里失败——证明过的是 launcher。"""
    proj, _, log, _ = conda_world
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    with pytest.raises(_pool.WorkerError) as exc:
        _pool.build("fig.py", str(proj), "__main__")
    assert "SDK needs the activated environment" in str(exc.value)
    assert conda_calls(log) == []


# ---------------------------------------------------------------------------
# 真实公共入口：成功 → 记忆 → 再次打开
# ---------------------------------------------------------------------------
SESSIONS = "/api/engine/preparation-sessions"
SETTLED = ("completed", "partial", "action_required", "cancelled", "awaiting_configuration")


def _run_session(app, body: dict) -> dict:
    status, report = app.call(SESSIONS, body, timeout=120)
    assert status in (200, 201), report
    (run,) = [a for a in report["actions"] if a["kind"] == "run"]
    status, claimed = app.call(
        f"{SESSIONS}/{report['session_id']}/actions",
        {"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        timeout=60,
    )
    assert status == 202, claimed
    deadline = time.time() + 300
    while True:
        _, rep = app.call(f"{SESSIONS}/{report['session_id']}", timeout=30)
        if rep["phase"] in SETTLED:
            return rep
        assert time.time() < deadline, rep
        time.sleep(0.2)


def store_text(work: Path) -> str:
    files = sorted((work / "data" / "runconfigs").glob("*.json"))
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


@needs_worker
def test_failed_run_leaves_no_success_memory(conda_world, tmp_path):
    proj, prefix, log, counter = conda_world
    (proj / "fig.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    work = tmp_path / "work"
    env = {
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_WORKER_PYTHON": str(prefix / "bin" / "python"),
        "FAKE_CONDA_LOG": str(log),
        "TAVOTTO_WORKERD": "0",
    }
    with fa.running_app(proj, work, env_overrides=env) as app:
        rep = _run_session(app, {"script": "fig.py", "argv": ["--scale", "2"]})
        assert rep["phase"] != "completed"
    assert "launches" not in store_text(work)


# ---------------------------------------------------------------------------
# launchctx.verify：每个依据变了都要重新核验（纯函数级，逐项变异）
# ---------------------------------------------------------------------------
class _Rcpt:
    """最小回执替身（只给 build_record 读的那几项）。"""

    profile = "safe"
    completeness = "complete"
    python_source = "env_override"
    spec_stable = {"cwd_mode": "sandbox"}
    binding = {"revision": ""}

    def __init__(self, interpreter, script_sha1, local_modules, launcher_ref, binding_revision=""):
        self.binding = {"revision": binding_revision}
        self.interpreter = interpreter
        self.source_revision = script_sha1
        self._mods = local_modules
        self.launcher = launcher_ref

    @property
    def inputs(self):
        return {"local_modules": self._mods, "files": []}

    def observed_files(self):
        return {}


@posix_only
def test_verify_each_basis_change_forces_reverification(tmp_path, monkeypatch):
    import hashlib

    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    (proj / "helper.py").write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")

    def good_record():
        sha1 = hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest()
        sha256 = hashlib.sha256((proj / "helper.py").read_bytes()).hexdigest()
        py = str(prefix / "bin" / "python")
        rcpt = _Rcpt(
            py,
            sha1,
            [{"name": "helper", "path": "helper.py", "sha256": sha256}],
            launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix)).public(),
            databinding.binding_for(proj / "fig.py", proj, "sandbox")["revision"],
        )
        return py, launchctx.build_record(proj, "fig.py", rcpt)

    py, record = good_record()
    kw = {"project_root": proj, "script": "fig.py", "python": py, "python_source": "env_override"}
    assert launchctx.verify(record, **kw) is None  # 正对照：什么都没变 = 成立

    (proj / "helper.py").write_text("X = 2\n", encoding="utf-8")
    assert launchctx.verify(record, **kw) == launchctx.REASON_HELPER
    py, record = good_record()
    (proj / "fig.py").write_text("print(2)\n", encoding="utf-8")
    assert launchctx.verify(record, **kw) == launchctx.REASON_SCRIPT
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    assert launchctx.verify(record, **kw) is None

    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda update python\n")  # 环境版本变了
    assert launchctx.verify(record, **kw) == launchctx.REASON_ENVIRONMENT
    assert launchctx.verify(record, **{**kw, "python_source": "project_venv"}) == (
        launchctx.REASON_INTERPRETER
    )
    assert launchctx.verify(record, **{**kw, "python": str(tmp_path / "other")}) == (
        launchctx.REASON_INTERPRETER
    )


@posix_only
def test_verify_data_binding_and_cwd_mode_changes_force_reverification(tmp_path, monkeypatch):
    import hashlib

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text('open("data.csv").read()\n', encoding="utf-8")
    (proj / "data.csv").write_text("1,2\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    py = sys.executable
    rcpt = _Rcpt(
        py,
        hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest(),
        [],
        None,
        databinding.binding_for(proj / "fig.py", proj, "sandbox")["revision"],
    )
    record = launchctx.build_record(proj, "fig.py", rcpt)
    assert record["binding_revision"]
    kw = {"project_root": proj, "script": "fig.py", "python": py, "python_source": "env_override"}
    assert launchctx.verify(record, **kw) is None
    (proj / "data.csv").write_text("9,9\n", encoding="utf-8")  # 数据绑定的文件内容变了
    assert launchctx.verify(record, **kw) == launchctx.REASON_BINDING
    (proj / "data.csv").write_text("1,2\n", encoding="utf-8")
    assert launchctx.verify(record, **kw) is None
    assert launchctx.verify({**record, "cwd_mode": "project"}, **kw) == launchctx.REASON_CWD


@posix_only
def test_partial_receipt_or_unavailable_evidence_is_not_recorded(tmp_path):
    class Partial(_Rcpt):
        completeness = "partial"

    assert launchctx.build_record(tmp_path, "fig.py", Partial("/x/python", "s", [], None)) is None
    assert launchctx.build_record(tmp_path, "fig.py", None) is None


REAL_PREFIX = os.environ.get("TAVOTTO_TEST_CONDA_PREFIX", "")
needs_real_conda = pytest.mark.skipif(
    os.name == "nt" or not REAL_PREFIX or not (Path(REAL_PREFIX) / "conda-meta").is_dir(),
    reason="没有真实 conda 环境（TAVOTTO_TEST_CONDA_PREFIX 指向带 matplotlib 的 conda prefix）：not_run",
)


def _real_conda() -> str:
    found = launcher.detect(Path(REAL_PREFIX) / "bin" / "python")
    assert found is not None, "真实 prefix 下找不到 conda 可执行文件"
    return found.conda_exe


@needs_real_conda
def test_real_conda_launch():
    """真 conda：`conda run --no-capture-output` 把 stdin / stdout 直通并返回子进程退出码。"""
    lau = launcher.detect(Path(REAL_PREFIX) / "bin" / "python")
    code = (
        "import sys\nfor l in sys.stdin:\n print('echo',l.strip(),flush=True)\n"
        " if l.strip()=='q': sys.exit(3)"
    )
    proc = subprocess.Popen(
        lau.wrap([str(Path(REAL_PREFIX) / "bin" / "python"), "-u", "-c", code]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    got = []
    for msg in ("a", "b", "q"):  # 逐行双向往返：缓冲或 stdin 不通会在这里卡住（readline 无限等）
        proc.stdin.write(msg + "\n")
        proc.stdin.flush()
        got.append(proc.stdout.readline().strip())
    assert got == ["echo a", "echo b", "echo q"]
    assert proc.wait(timeout=30) == 3
    proc.stdin.close()
    proc.stdout.close()


@needs_real_conda
def test_real_conda_worker_roundtrip_and_cancel_leaves_no_user_python(tmp_path, monkeypatch):
    """真 conda：完整 worker 协议往返（build → 出图 → 渲染），再强杀 wrapper，用户 Python 不得残留。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt, os\n"
        "assert os.environ.get('CONDA_PREFIX')\n"
        "fig, ax = plt.subplots(figsize=(3, 2)); ax.plot([0, 1, 2], [1, 4, 2])\n"
        "fig.savefig('result.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(Path(REAL_PREFIX) / "bin" / "python"))
    _pool.reset_worker_python()
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        assert worker.launcher is not None and worker.launcher.prefix == REAL_PREFIX
        assert resp["runtime"]["prefix"] == REAL_PREFIX  # 脚本真的跑在这个 conda 环境里
        user_pid = resp["runtime"]["pid"]
        assert user_pid != worker.child_pid and alive(user_pid)
        assert worker.render_png  # 热会话还能服务
        # 真 conda 的 `bash 临时脚本` 夹在中间：回执完整，成功记录能写入（此前 pid_mismatch → partial → 不记录）
        rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
        assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE, rcpt.runtime_rejected
        assert rcpt.pid_check == receipt.PID_CHECK_PROCESS_GROUP
        monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
        assert launchctx.record_success(proj, "fig.py", rcpt) is True
        assert runconfig.launch_record(proj, "fig.py")["launcher"]["kind"] == "conda"
    finally:
        worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()
    assert wait_dead(user_pid), "取消 wrapper 后用户 Python 仍在运行"
    ps = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    assert not [ln for ln in ps.splitlines() if str(tmp_path) in ln and "worker.py" in ln], ps


@needs_real_conda
def test_real_conda_cancel_mid_computation_leaves_no_user_python(tmp_path, monkeypatch):
    """真 conda：脚本正在长计算时硬取消（force_kill），wrapper 和用户 Python 都不得残留。"""
    import threading

    proj = tmp_path / "proj"
    proj.mkdir()
    pidfile = tmp_path / "user.pid"
    (proj / "slow.py").write_text(
        f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(300)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(Path(REAL_PREFIX) / "bin" / "python"))
    _pool.reset_worker_python()
    worker = _pool.get("slow.py", str(proj), "__main__")
    box: list = []

    def run() -> None:
        try:
            worker.ensure_built()
        except Exception as exc:  # noqa: BLE001 — 被取消的 build 本来就会失败
            box.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    deadline = time.time() + 120
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.1)
    user_pid = int(pidfile.read_text())
    wrapper = worker.child_pid
    assert alive(user_pid) and alive(wrapper)
    try:
        worker.force_kill()
        t.join(timeout=30)
    finally:
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()
    assert wait_dead(user_pid) and wait_dead(wrapper)
    assert box, "被取消的 build 应当报错而不是成功"


@posix_only
def test_a_planted_conda_exe_in_the_record_is_never_executed(tmp_path, monkeypatch):
    """登记里的 `conda_exe` 是不可信数据（可被克隆来的 / 同步来的文件写入）：沿用记录时不得执行它，
    实际包装用的是从解释器现场检测出的 conda。"""
    import hashlib

    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    marker = tmp_path / "PWNED"
    evil = proj / "evil-conda"
    evil.write_text(f"#!/bin/sh\necho x > {marker}\nexit 0\n", encoding="utf-8")
    evil.chmod(0o755)
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    py = str(prefix / "bin" / "python")
    rcpt = _Rcpt(
        py,
        hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest(),
        [],
        {"kind": "conda", "conda_exe": str(evil), "prefix": str(prefix)},  # 被篡改的引用
        databinding.binding_for(proj / "fig.py", proj, "sandbox")["revision"],
    )
    assert launchctx.record_success(proj, "fig.py", rcpt) is True
    got = launchctx.launcher_for(proj, "fig.py", py, "env_override", bundled="bundled")
    assert got is not None and got.conda_exe == str(conda)
    log = tmp_path / "plant.log"
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    subprocess.run(got.wrap([sys.executable, "-c", "pass"]), check=True, timeout=30)
    assert not marker.exists(), "登记里的 conda_exe 被执行了"
    assert len(conda_calls(log)) == 1
    # 记录里的 prefix 与解释器所在环境对不上：记录整体不可信，不沿用
    forged = {"kind": "conda", "conda_exe": str(conda), "prefix": str(tmp_path / "elsewhere")}
    record = runconfig.launch_record(proj, "fig.py")
    record["launcher"] = forged
    assert (
        launchctx.verify(
            record,
            project_root=proj,
            script="fig.py",
            python=py,
            python_source="env_override",
            facets=launchctx.FACETS_LAUNCHER,
        )
        == launchctx.REASON_LAUNCHER
    )


@posix_only
def test_kill_switch_beats_a_recorded_conda_launcher(tmp_path, monkeypatch):
    """`TAVOTTO_CONDA_ACTIVATION=0` 对沿用的记录同样生效：即使记录说上次是 Conda 启动也裸起。"""
    import hashlib

    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    py = str(prefix / "bin" / "python")
    rcpt = _Rcpt(
        py,
        hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest(),
        [],
        launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix)).public(),
        databinding.binding_for(proj / "fig.py", proj, "sandbox")["revision"],
    )
    assert launchctx.record_success(proj, "fig.py", rcpt) is True
    args = (proj, "fig.py", py, "env_override")
    assert launchctx.launcher_for(*args, bundled="bundled") is not None  # 正对照
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launchctx.launcher_for(*args, bundled="bundled") is None


@posix_only
def test_incomplete_basis_is_not_recorded_so_it_can_never_authorize_reuse(tmp_path, monkeypatch):
    """依据超过上限（或观察器截断）时存不全：整条记录不写，而不是截断后冒充充分证据。"""
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    sha = "0" * 40

    def mods(n):
        return [{"name": f"m{i}", "path": f"m{i}.py", "sha256": "a" * 64} for i in range(n)]

    ok = _Rcpt(sys.executable, sha, mods(launchctx.MAX_BASIS), None)
    assert launchctx.build_record(proj, "fig.py", ok) is not None  # 恰好在上限：存得下
    over = _Rcpt(sys.executable, sha, mods(launchctx.MAX_BASIS + 1), None)
    assert launchctx.build_record(proj, "fig.py", over) is None
    assert launchctx.record_success(proj, "fig.py", over) is False
    assert runconfig.launch_record(proj, "fig.py") is None

    class Truncated(_Rcpt):
        @property
        def inputs(self):
            return {"local_modules": [], "files": [], "truncated": True}

    assert launchctx.build_record(proj, "fig.py", Truncated(sys.executable, sha, [], None)) is None


@needs_worker
def test_activation_banner_before_the_protocol_is_diverted_to_the_log(conda_world, monkeypatch):
    """`activate.d` 钩子往 stdout 打印的内容在协议之前直通进管道：转进 worker.log 并继续，会话照常工作。"""
    proj, prefix, log, counter = conda_world
    monkeypatch.setenv("FAKE_CONDA_BANNER", "Welcome to the vendor SDK (license ok)")
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        assert "[activation stdout] Welcome to the vendor SDK" in worker.log_path.read_text(
            encoding="utf-8", errors="replace"
        )
        assert worker.render_png  # 之后的协议往返不受影响
    finally:
        worker.force_kill()


@needs_worker
def test_activation_banner_cap_does_not_swallow_a_flood(conda_world, monkeypatch):
    """上限：横幅刷屏超限后不再吞，按 protocol_mismatch 如实失败（不是无限跳过）。"""
    proj, prefix, log, counter = conda_world
    monkeypatch.setattr(_pool.EngineWorker, "ACTIVATION_BANNER_MAX_LINES", 3)
    monkeypatch.setenv("FAKE_CONDA_BANNER", "\n".join(f"noise {i}" for i in range(20)))
    with pytest.raises(_pool.WorkerError) as exc:
        _pool.build("fig.py", str(proj), "__main__")
    assert exc.value.code == "protocol_mismatch"


@needs_real_conda
def test_real_conda_activate_d_hook_stdout_does_not_break_the_protocol(tmp_path, monkeypatch):
    """真 conda：env 的 `activate.d` 钩子往 stdout 打印，会话照常建立、出图，横幅在 worker.log 里。"""
    hook_dir = Path(REAL_PREFIX) / "etc" / "conda" / "activate.d"
    hook_dir.mkdir(parents=True, exist_ok=True)
    for stale in hook_dir.glob("p09_*.sh"):  # 上次被打断的运行可能留下钩子，会污染别的真 conda 用例
        stale.unlink()
    hook = hook_dir / "p09_banner_test.sh"
    hook.write_text('echo "P09 vendor banner: license server ready"\n', encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(Path(REAL_PREFIX) / "bin" / "python"))
    _pool.reset_worker_python()
    worker = None
    try:
        worker, resp = _pool.build("fig.py", str(proj), "__main__")
        assert resp["runtime"]["prefix"] == REAL_PREFIX
        assert "P09 vendor banner" in worker.log_path.read_text(encoding="utf-8", errors="replace")
        assert worker.render_png
    finally:
        hook.unlink(missing_ok=True)
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


# ---------------------------------------------------------------------------
# 登记事务：最近成功才是记录
# ---------------------------------------------------------------------------
def _good_rcpt(proj, py, *, mods=0):
    import hashlib

    return _Rcpt(
        py,
        hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest(),
        [{"name": f"m{i}", "path": f"m{i}.py", "sha256": "a" * 64} for i in range(mods)],
        None,
        databinding.binding_for(proj / "fig.py", proj, "sandbox")["revision"],
    )


@posix_only
def test_default_and_launch_record_commit_in_one_transaction(tmp_path, monkeypatch):
    """两个 argv 的会话并发收尾：默认配置与启动记录永远指向同一个（同一次写入）。"""
    import threading

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    py = sys.executable
    real_write = runconfig._write

    def slow_write(*a, **k):  # 放大两次写入之间的窗口
        time.sleep(0.01)
        return real_write(*a, **k)

    monkeypatch.setattr(runconfig, "_write", slow_write)
    runs_ = [runconfig.selection_for(proj, "fig.py", ["--n", str(i)]) for i in range(2)]
    rcpt = _good_rcpt(proj, py)

    def txn(run):
        launchctx.record_success(proj, "fig.py", rcpt, run=run, update_default=True)

    # 旧写法（两次各自加锁的写入）允许的一种交错：A.默认 → B.默认 → B.记录 → A.记录，结果默认指 B、记录是 A
    rec = {r.config_id: launchctx.build_record(proj, "fig.py", rcpt, run=r) for r in runs_}
    a, b = runs_
    runconfig.set_default(proj, "fig.py", a.config_id)
    runconfig.set_default(proj, "fig.py", b.config_id)
    runconfig.set_launch_record(proj, "fig.py", rec[b.config_id])
    runconfig.set_launch_record(proj, "fig.py", rec[a.config_id])
    data = runconfig._read(proj)
    assert data["defaults"]["fig.py"] != data["launches"]["fig.py"]["run_config"]  # 旧写法确会错配

    # 事务写法：并发收尾无论谁先谁后，默认与记录永远指向同一个
    mismatches = 0
    for _ in range(15):
        ts = [threading.Thread(target=txn, args=(r,)) for r in runs_]
        [t.start() for t in ts]
        [t.join() for t in ts]
        data = runconfig._read(proj)
        if data["defaults"]["fig.py"] != data["launches"]["fig.py"]["run_config"]:
            mismatches += 1
    assert mismatches == 0


# ---------------------------------------------------------------------------
# #975 第三轮评审
# ---------------------------------------------------------------------------
@posix_only
def test_windows_gets_no_conda_wrapping(tmp_path, monkeypatch):
    """本期 Windows 不做 Conda 包装（没有真机验证，回执也核不了 pid）：模拟 nt 时 detect 返回 None。"""
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    assert launcher.detect(prefix / "bin" / "python") is not None  # 正对照
    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    assert launcher.detect(prefix / "bin" / "python") is None


@posix_only
def test_an_input_without_a_digest_makes_the_record_unverifiable(tmp_path, monkeypatch):
    """输入过大没算出摘要：只核「还在」会让原地改内容也照样沿用——这样的依据不记录；旧 / 手改的记录 verify 也拒。"""
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    (proj / "big.dat").write_text("x", encoding="utf-8")
    rcpt = _good_rcpt(proj, sys.executable)
    rcpt.observed_files = lambda: {"big.dat": None}
    assert launchctx.build_record(proj, "fig.py", rcpt) is None
    good = launchctx.build_record(proj, "fig.py", _good_rcpt(proj, sys.executable))
    good["source"]["basis"] = {"big.dat": None}
    kw = {
        "project_root": proj,
        "script": "fig.py",
        "python": sys.executable,
        "python_source": "env_override",
    }
    assert launchctx.verify(good, **kw) == launchctx.REASON_HELPER


def test_a_json_object_printed_by_a_hook_is_not_a_protocol_envelope():
    is_env = _pool.EngineWorker._is_envelope
    pv = _pool.PROTOCOL_VERSION
    assert is_env(json.dumps({"protocol_version": pv, "request_id": "r1", "ok": True}))
    assert not is_env('{"status": "license ready"}')
    assert not is_env(json.dumps({"request_id": "r1"}))  # 缺 protocol_version
    assert not is_env(json.dumps({"protocol_version": pv}))  # 缺 request_id
    assert not is_env(json.dumps({"protocol_version": pv + 1, "request_id": "r1"}))
    assert not is_env(json.dumps({"protocol_version": pv, "request_id": 7}))
    assert not is_env("[1, 2]") and not is_env("plain text")


@needs_worker
def test_a_json_banner_from_a_hook_is_diverted_not_mistaken_for_a_reply(conda_world, monkeypatch):
    proj, prefix, log, counter = conda_world
    monkeypatch.setenv("FAKE_CONDA_BANNER", '{"status": "license ready"}')
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        text = worker.log_path.read_text(encoding="utf-8", errors="replace")
        assert '[activation stdout] {"status": "license ready"}' in text
        assert worker.render_png
    finally:
        worker.force_kill()


BAD_LAUNCH_SHAPES = {
    "source_is_list": lambda r: r.update(source=[1, 2]),
    "basis_is_list": lambda r: r["source"].update(basis=["a.py"]),
    "basis_value_int": lambda r: r["source"].update(basis={"a.py": 7}),
    "interpreter_is_str": lambda r: r.update(interpreter="/usr/bin/python"),
    "interpreter_path_missing": lambda r: r["interpreter"].pop("path"),
    "launcher_is_null": lambda r: r.update(launcher=None),
    "launcher_prefix_list": lambda r: r["launcher"].update(prefix=["x"]),
    "run_config_not_a_ref": lambda r: r.update(run_config="../../etc/passwd"),
    "env_state_int": lambda r: r.update(env_state=3),
    "run_sensitive_str": lambda r: r.update(run_sensitive="yes"),
    "cwd_mode_missing": lambda r: r.pop("cwd_mode"),
}


@posix_only
@pytest.mark.parametrize("shape", sorted(BAD_LAUNCH_SHAPES))
def test_malformed_launch_records_are_dropped_not_raised(tmp_path, monkeypatch, shape):
    """嵌套字段坏了（source 是 list 等）：登记解析时丢掉这一条，之后的读 / 核验不抛 AttributeError（不是 500）。"""
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    run = runconfig.selection_for(proj, "fig.py", ["--n", "1"])
    assert launchctx.record_success(proj, "fig.py", _good_rcpt(proj, sys.executable), run=run)
    path = runconfig.store_path(proj)
    data = json.loads(path.read_text(encoding="utf-8"))
    BAD_LAUNCH_SHAPES[shape](data["launches"]["fig.py"])
    path.write_text(json.dumps(data), encoding="utf-8")
    assert runconfig.launch_record(proj, "fig.py") is None
    assert (
        launchctx.verify(
            runconfig.launch_record(proj, "fig.py"),
            project_root=proj,
            script="fig.py",
            python=sys.executable,
            python_source="env_override",
        )
        == launchctx.REASON_NO_RECORD
    )
    assert (
        launchctx.launcher_for(proj, "fig.py", sys.executable, "env_override", bundled="bundled")
        is None
    )  # 坏记录 = 没有记录：照常检测，不抛
    # 坏的只是这一条：配置本身与别的脚本的记录不受影响
    assert runconfig.get(proj, run.config_id, script="fig.py").argv == ("--n", "1")


# ---------------------------------------------------------------------------
# #975 第四轮评审
# ---------------------------------------------------------------------------
@needs_worker
def test_the_legacy_probe_success_clears_an_older_launch_record(conda_world, tmp_path):
    """A 经准备会话成功、B 再经旧 `/api/registry/probe` 成功：之后不带 argv 的打开不得悄悄沿用 A。"""
    proj, prefix, log, counter = conda_world
    work = tmp_path / "work"
    env = {
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_WORKER_PYTHON": str(prefix / "bin" / "python"),
        "FAKE_CONDA_LOG": str(log),
        "TAVOTTO_WORKERD": "0",
    }
    with fa.running_app(proj, work, env_overrides=env) as app:
        assert _run_session(app, {"script": "fig.py", "argv": ["--scale", "2"]})["phase"] == (
            "completed"
        )
        assert "fig.py" in json.loads(store_text(work))["launches"]  # A 的记录在
        status, probed = app.call(
            "/api/registry/probe", {"script": "fig.py", "argv": ["--scale", "3"]}, timeout=300
        )
        assert status == 200 and probed["registered"] is True
        stored = json.loads(store_text(work))
        assert "fig.py" not in stored.get("launches", {})  # 旧路径与 default 同事务清掉 A
        assert _run_session(app, {"script": "fig.py"})["phase"] == "completed"
        assert [r["argv"] for r in runs(counter)][-1] == []  # 不是 A，也不是 B：让用户重新给


# ---------------------------------------------------------------------------
# #975 收缩后：记录的写入与清除（消费端见 #982）、argv 行为不变
# ---------------------------------------------------------------------------
@needs_worker
def test_success_is_recorded_and_argv_behaviour_is_unchanged(conda_world, tmp_path):
    """成功后写下可验证的启动上下文；但「再次打开」不沿用：省略 argv（含带着已废弃的 `reuse_last_success`）就是无参数运行。"""
    proj, prefix, log, counter = conda_world
    work = tmp_path / "work"
    env = {
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_WORKER_PYTHON": str(prefix / "bin" / "python"),
        "FAKE_CONDA_LOG": str(log),
        "TAVOTTO_WORKERD": "0",
    }
    with fa.running_app(proj, work, env_overrides=env) as app:
        assert _run_session(app, {"script": "fig.py", "argv": ["--scale", "2"]})["phase"] == (
            "completed"
        )
        record = json.loads(store_text(work))["launches"]["fig.py"]
        assert record["launcher"]["kind"] == "conda" and record["run_config"].startswith("rc_")
        assert LICENSE not in store_text(work) and "CONDA_PREFIX" not in store_text(work)
        assert "--scale" not in json.dumps(record)
        assert _run_session(app, {"script": "fig.py"})["phase"] == "completed"
        assert [r["argv"] for r in runs(counter)][-1] == []
        assert (
            _run_session(app, {"script": "fig.py", "reuse_last_success": True})["phase"]
            == "completed"
        )
        assert [r["argv"] for r in runs(counter)][-1] == []  # 字段不被认：与 P09 之前逐字节一致


@posix_only
def test_a_later_success_without_sufficient_basis_clears_the_older_record(tmp_path, monkeypatch):
    """B 成功但依据存不全：旧记录 A 必须清掉，不能让 A 继续冒充「最近成功」。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    py = sys.executable
    run_a = runconfig.selection_for(proj, "fig.py", ["--scale", "2"])
    assert launchctx.record_success(proj, "fig.py", _good_rcpt(proj, py), run=run_a) is True
    assert runconfig.launch_record(proj, "fig.py")["run_config"] == run_a.config_id
    run_b = runconfig.selection_for(proj, "fig.py", ["--scale", "3"])
    over = _good_rcpt(proj, py, mods=launchctx.MAX_BASIS + 1)
    assert launchctx.record_success(proj, "fig.py", over, run=run_b) is False
    assert runconfig.launch_record(proj, "fig.py") is None


@posix_only
def test_a_sensitive_run_config_is_recorded_as_such_and_fails_verification(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    sel = runconfig.selection_for(proj, "fig.py", ["--token", "SECRET-777"], sensitive=True)
    assert launchctx.record_success(proj, "fig.py", _good_rcpt(proj, sys.executable), run=sel)
    stored = "\n".join(f.read_text("utf-8") for f in (tmp_path / "data").rglob("*.json"))
    assert "SECRET-777" not in stored
    rec = runconfig.launch_record(proj, "fig.py")
    assert rec["run_sensitive"] is True
    assert (
        launchctx.verify(
            rec,
            project_root=proj,
            script="fig.py",
            python=sys.executable,
            python_source="env_override",
        )
        == launchctx.REASON_RUN_CONFIG
    )


# ---------------------------------------------------------------------------
# #975 第七轮
# ---------------------------------------------------------------------------
@posix_only
def test_a_recorded_conda_launch_never_silently_degrades_to_a_bare_launch(tmp_path, monkeypatch):
    """记录说上次是 Conda 启动、现在找不到 conda：不悄悄裸起（会重演激活变量缺失），报稳定码；关开关是明确选择，不在此列。"""
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    py = str(prefix / "bin" / "python")
    rcpt = _good_rcpt(proj, py)
    rcpt.python_source = "env_override"
    rcpt.launcher = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix)).public()
    assert launchctx.record_success(proj, "fig.py", rcpt)
    args = (proj, "fig.py", py, "env_override")
    assert launchctx.launcher_for(*args, bundled="bundled") is not None  # 正对照
    conda.unlink()  # conda 没了（卸载 / 挪走）
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launchctx.launcher_for(*args, bundled="bundled")
    assert exc.value.code == "conda_launcher_unavailable"
    # 用户明确关掉开关：裸起，不报错
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launchctx.launcher_for(*args, bundled="bundled") is None


def _spawn_group_with_member(tmp_path):
    """起一个自成组的 leader，里面再有一个 **pid 不同于组长** 的同组成员；回 (leader Popen, member pid)。"""
    pidfile = tmp_path / "member.pid"
    leader_src = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {CHILD!r}, {str(pidfile)!r}], stdout=subprocess.DEVNULL)\n"
        "time.sleep(300)\n"
    )
    leader = subprocess.Popen([sys.executable, "-c", leader_src], start_new_session=True)
    deadline = time.time() + 20
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    return leader, int(pidfile.read_text())


@posix_only
def test_process_group_check_accepts_a_member_that_is_not_the_leader(tmp_path):
    """`via_group` 分支必须用「pid 不同于组长的同组成员」单独测到（用测试进程自己会走到 pid==expected 的直核分支）。"""
    leader, member = _spawn_group_with_member(tmp_path)
    try:
        assert member != leader.pid and os.getpgid(member) == leader.pid
        rt = {"report_origin": "build", "pid": member, "ppid": leader.pid + 99999}
        ok = receipt.accept_runtime(rt, expected_pid=leader.pid, via_group=True)
        assert ok[1] is None and ok[2] == receipt.PID_CHECK_PROCESS_GROUP
        # 没有 launcher 时同一份自报不放行；组长对不上也不放行
        assert receipt.accept_runtime(rt, expected_pid=leader.pid, via_group=False)[1] == (
            "pid_mismatch"
        )
        assert receipt.accept_runtime(rt, expected_pid=os.getpid(), via_group=True)[1] == (
            "pid_mismatch"
        )
    finally:
        for pid in (member, leader.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        leader.wait(timeout=10)


@posix_only
def test_group_members_lists_the_group_with_parents_and_tells_zombies_apart(tmp_path):
    leader, member = _spawn_group_with_member(tmp_path)
    try:
        members = {pid: (ppid, st) for pid, ppid, st in launcher.group_members(leader.pid)}
        assert members[member][0] == leader.pid and members[member][1] != "Z"
        assert members[leader.pid][1] != "Z"
        # 让成员死但没人收尸：leader 是它的父进程且不 wait → 僵尸
        os.kill(member, signal.SIGKILL)
        deadline = time.time() + 10
        state = None
        while time.time() < deadline:
            state = {pid: st for pid, _pp, st in launcher.group_members(leader.pid)}.get(member)
            if state == "Z":
                break
            time.sleep(0.05)
        assert state == "Z", state
        assert launcher.is_running(leader.pid) and not launcher.is_running(member)
    finally:
        os.kill(leader.pid, signal.SIGKILL)
        leader.wait(timeout=10)


@posix_only
def test_kill_terminates_the_process_tree_from_the_leaves_up(tmp_path, monkeypatch):
    """conda → shell → Python：先杀最深的 Python，等 shell 把它回收掉（pid 消失，不是 Z），再杀 shell，最后 wrapper。
    对整组同时发 SIGKILL 时 shell 可能先死，Python 成孤儿；PID 1 不收尸的容器里它永远是僵尸。"""
    proc, child = _spawn_wrapped(tmp_path, launcher.GroupPopen)
    tree = {pid: ppid for pid, ppid, _ in launcher.group_members(proc.pid)}
    (shell,) = [pid for pid, ppid in tree.items() if ppid == proc.pid]
    assert tree[child] == shell  # 真的是三层
    sent: list[int] = []
    real = launcher.GroupPopen._signal_pid

    def spy(self, pid):
        sent.append(pid)
        real(self, pid)

    monkeypatch.setattr(launcher.GroupPopen, "_signal_pid", spy)
    proc.kill()
    proc.wait(timeout=10)
    assert sent[:2] == [child, shell], sent  # 叶子在前，shell 其次；wrapper 最后由 kill / wait 处理
    assert wait_gone(child) and wait_gone(shell)  # 被父进程回收了，不是留成僵尸
    assert proc.unreaped == []
    for fh in (proc.stdin, proc.stdout):
        fh.close()


@posix_only
def test_a_zombie_with_a_live_parent_is_still_residue_but_an_orphan_zombie_of_init_is_reported(
    tmp_path, monkeypatch
):
    """僵尸只有在父进程是 PID 1（没有任何人能收尸）时才不算残留；父进程还在的僵尸迟早被收尸，仍算残留。
    被 init 接管的僵尸也不能伪装成「已回收」：如实记在 `unreaped` 里并告警。
    真正的「PID 1 不收尸的容器」本机构造不出来（macOS 没有 subreaper / PID 命名空间）：这里用注入的成员表覆盖这两个判据。"""
    gp = launcher.GroupPopen.__new__(launcher.GroupPopen)
    gp.pid = 4242
    monkeypatch.setattr(launcher, "group_members", lambda pgid: [(4242, 1, "S"), (4300, 4242, "Z")])
    assert gp._group_alive() is True  # 父进程（wrapper）还在：它会收尸，等它
    monkeypatch.setattr(launcher, "group_members", lambda pgid: [(4300, 1, "Z")])
    assert gp._group_alive() is False  # 被 PID 1 接管：没人能收，不再执行代码
    gp.unreaped = []
    # wait() 不把它伪装成干净
    monkeypatch.setattr(launcher.subprocess.Popen, "wait", lambda self, timeout=None: 0)
    assert gp.wait() == 0 and gp.unreaped == [(4300, "Z")]


@needs_worker
def test_the_pool_turns_an_unavailable_conda_launcher_into_a_stable_error(conda_world, monkeypatch):
    proj, prefix, log, counter = conda_world
    import hashlib

    rcpt = _good_rcpt(proj, str(prefix / "bin" / "python"))
    rcpt.python_source = "env_override"
    rcpt.launcher = launcher.CondaLauncher(
        conda_exe=str(prefix.parent.parent / "bin" / "conda"), prefix=str(prefix)
    ).public()
    (proj / "fig.py").write_text((proj / "fig.py").read_text(encoding="utf-8"), encoding="utf-8")
    rcpt.source_revision = hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest()
    assert launchctx.record_success(proj, "fig.py", rcpt)
    (prefix.parent.parent / "bin" / "conda").unlink()
    monkeypatch.setenv(
        "PATH", str(proj / "nothing")
    )  # CI 的机器上 PATH / CONDA_EXE 里可能真有 conda
    monkeypatch.delenv("CONDA_EXE", raising=False)
    with pytest.raises(_pool.WorkerError) as werr:
        _pool.EngineWorker("fig.py", str(proj), "__main__")
    assert werr.value.code == "conda_launcher_unavailable"


def test_envelope_suffix_splits_an_unterminated_banner_from_the_first_envelope():
    pv = _pool.PROTOCOL_VERSION
    env = json.dumps({"protocol_version": pv, "request_id": "r1", "ok": True})
    suffix = _pool.EngineWorker._envelope_suffix
    assert suffix("initializing…" + env + "\n") == env + "\n"
    # 键序不固定：真实的 build 回复是 {"ok": true, "protocol_version": 1, "request_id": ...}
    real = json.dumps({"ok": True, "protocol_version": pv, "request_id": "r2", "stems": {"a": 1}})
    assert suffix("init {x" + real + "\n") == real + "\n"  # 钩子输出里自带 `{` 也能找到真起点
    assert suffix(real + "\n") is None
    assert suffix(env + "\n") is None  # 本来就整行是信封：不拆
    assert suffix("plain banner\n") is None
    assert suffix('x {"protocol_version": 1, broken\n') is None  # 形似不合法：不拆
    assert suffix("a" + json.dumps({"request_id": "r"}) + "\n") is None  # 不带 protocol_version


@needs_worker
def test_an_unterminated_hook_banner_does_not_swallow_the_first_reply(conda_world, monkeypatch):
    """钩子 `printf 'initializing…'` 没有换行：它和 worker 的第一条信封粘成一行，不能整行被当横幅吞掉（会话随后超时）。"""
    proj, prefix, log, counter = conda_world
    monkeypatch.setenv("FAKE_CONDA_BANNER", "initializing vendor SDK...")
    monkeypatch.setenv("FAKE_CONDA_BANNER_NO_NEWLINE", "1")
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        assert "[activation stdout] initializing vendor SDK..." in worker.log_path.read_text(
            encoding="utf-8", errors="replace"
        )
        assert worker.render_png
    finally:
        worker.force_kill()


@needs_real_conda
def test_real_conda_unterminated_activate_d_printf_does_not_break_the_protocol(
    tmp_path, monkeypatch
):
    hook_dir = Path(REAL_PREFIX) / "etc" / "conda" / "activate.d"
    hook_dir.mkdir(parents=True, exist_ok=True)
    for stale in hook_dir.glob("p09_*.sh"):  # 上次被打断的运行可能留下钩子，会污染别的真 conda 用例
        stale.unlink()
    hook = hook_dir / "p09_printf_test.sh"
    hook.write_text("printf 'initializing vendor SDK...'\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(Path(REAL_PREFIX) / "bin" / "python"))
    _pool.reset_worker_python()
    worker = None
    try:
        worker, resp = _pool.build("fig.py", str(proj), "__main__")
        assert resp["runtime"]["prefix"] == REAL_PREFIX
        assert "initializing vendor SDK..." in worker.log_path.read_text(
            encoding="utf-8", errors="replace"
        )
    finally:
        hook.unlink(missing_ok=True)
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


# ---------------------------------------------------------------------------
# 预检探针与 worker 同一个启动上下文（#975 r4255384823）
# ---------------------------------------------------------------------------
SHIM_MATPLOTLIB = """\
import os, sys
if not os.environ.get("FAKE_SDK_LICENSE"):
    raise ImportError("matplotlib 只有激活之后才能 import（模拟 activate.d 设置的 DLL / 变量）")
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:] = [p for p in sys.path if os.path.abspath(p) != _here]
del sys.modules[__name__]
import matplotlib as _real  # noqa: E402 — 此刻找到的是真的那个

sys.modules[__name__] = _real
"""


@pytest.fixture
def activation_only_env(tmp_path, monkeypatch):
    """模拟 conda 环境：`matplotlib` 与 `vendor_sdk` 只有经 conda 激活才 import 得到（裸起解释器会 ImportError）。"""
    prefix, conda, log = make_fake_conda(tmp_path, WORKER_PY or sys.executable)
    bare = prefix / "bare_site"
    (bare / "matplotlib").mkdir(parents=True)
    (bare / "matplotlib" / "__init__.py").write_text(SHIM_MATPLOTLIB, encoding="utf-8")
    site = prefix / "activated_site"
    site.mkdir()
    (site / "vendor_sdk.py").write_text("VALUE = 42\n", encoding="utf-8")
    py = prefix / "bin" / "python"
    py.write_text(
        f'#!/bin/sh\nexport PYTHONPATH="{bare}${{PYTHONPATH:+:$PYTHONPATH}}"\nexec "{WORKER_PY or sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(py, 0o755)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.delenv("CONDA_EXE", raising=False)
    return str(py), prefix, log


@needs_worker
def test_preflight_probes_run_in_the_same_activation_context_as_the_worker(
    activation_only_env, tmp_path, monkeypatch
):
    from tavotto.engine import deprepair, projectenv

    py, prefix, log = activation_only_env
    # 负对照：关掉 Conda 激活，同一个解释器裸起——预检全部失败（这正是 AC-11 要消除的假阴性）
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")
        assert _pool._has_matplotlib(py) is False
        bare = projectenv.probe_environment(py)
        assert bare["ok"] is False
        assert deprepair.probe_imports(py, ("vendor_sdk",))["vendor_sdk"]  # 非空 = 错误文本
    assert conda_calls(log) == []
    # 经 conda 激活：同一个解释器的预检全部通过
    assert _pool._has_matplotlib(py) is True
    probed = projectenv.probe_environment(py)
    assert probed["ok"] is True and probed["matplotlib_version"], probed
    assert deprepair.probe_imports(py, ("vendor_sdk",)) == {"vendor_sdk": ""}
    assert len(conda_calls(log)) >= 3  # 三个预检都是经 conda run 起的


@needs_worker
def test_selecting_an_activation_only_interpreter_and_drawing_end_to_end(
    activation_only_env, tmp_path, monkeypatch
):
    """显式选中这个解释器：选择（`_has_matplotlib`）不再误报 `explicit_python_unusable`，一路到 worker 出图。"""
    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    worker = None
    try:
        with monkeypatch.context() as m:  # 负对照：不激活 → 选择阶段就报显式解释器不可用
            m.setenv(launcher.ENV_DISABLE, "0")
            with pytest.raises(_pool.WorkerError) as bad:
                _pool.select_worker_python()
            assert bad.value.code == "explicit_python_unusable"
        _pool.reset_worker_python()
        worker, resp = _pool.build("fig.py", str(proj), "__main__")
        assert worker.launcher is not None and resp["runtime"]["prefix"]
    finally:
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


@needs_worker
def test_probe_banner_and_timeout_are_handled_for_wrapped_probes(activation_only_env, monkeypatch):
    py, prefix, log = activation_only_env
    monkeypatch.setenv("FAKE_CONDA_BANNER", "initializing vendor SDK...")
    monkeypatch.setenv("FAKE_CONDA_BANNER_NO_NEWLINE", "1")
    from tavotto.engine import deprepair

    # 横幅（没有换行结尾）粘在探针的 JSON 前面：照样解析出结果
    assert deprepair.probe_imports(py, ("vendor_sdk",)) == {"vendor_sdk": ""}
    # 超时：整组收掉，不留探针里的 Python
    pidfile = prefix.parent / "probe.pid"
    started = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        launcher.run_probe(
            [py, "-c", CHILD, str(pidfile)],
            py,
            capture_output=True,
            text=True,
            timeout=3,
        )
    assert time.time() - started < 30
    assert wait_dead(int(pidfile.read_text()))


def test_json_tail_strips_only_what_precedes_the_result_object():
    assert launcher.json_tail('{"a": 1}') == '{"a": 1}'
    assert launcher.json_tail('banner {"a": {"b": 2}}') == '{"a": {"b": 2}}'
    assert launcher.json_tail("banner only") == "banner only"
    assert launcher.json_tail('x {oops {"a": 1}') == '{"a": 1}'


@needs_real_conda
def test_real_conda_preflight_sees_variables_set_by_activate_d(tmp_path, monkeypatch):
    """真 conda：activate.d 设置 PYTHONPATH 之后才 import 得到的模块——预检（经 conda run）能看见，裸起看不见。"""
    from tavotto.engine import deprepair

    hook_dir = Path(REAL_PREFIX) / "etc" / "conda" / "activate.d"
    hook_dir.mkdir(parents=True, exist_ok=True)
    for stale in hook_dir.glob("p09_*.sh"):
        stale.unlink()
    site = tmp_path / "vendor_site"
    site.mkdir()
    (site / "vendor_sdk_p09.py").write_text("VALUE = 7\n", encoding="utf-8")
    hook = hook_dir / "p09_pythonpath_test.sh"
    hook.write_text(f'export PYTHONPATH="{site}${{PYTHONPATH:+:$PYTHONPATH}}"\n', encoding="utf-8")
    real_py = str(Path(REAL_PREFIX) / "bin" / "python")
    try:
        assert deprepair.probe_imports(real_py, ("vendor_sdk_p09",)) == {"vendor_sdk_p09": ""}
        with monkeypatch.context() as m:
            m.setenv(launcher.ENV_DISABLE, "0")
            assert deprepair.probe_imports(real_py, ("vendor_sdk_p09",))["vendor_sdk_p09"]
    finally:
        hook.unlink(missing_ok=True)
