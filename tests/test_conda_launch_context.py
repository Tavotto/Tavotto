"""P09：复用成功启动上下文 + Conda 激活 launcher（EX07 / EX11，AC-11 / AC-19）。

**这里没有真实 Conda。** 本机（和 CI 的 Linux 腿）没有 conda / mamba / micromamba，所以「真实受支持 Conda 启动」那一档
是 `not_run`（见 `test_real_conda_launch`，无 conda 时 skip）。其余用例用一个**模拟 conda**：一个按官方 `conda run` 文档
（<https://docs.conda.io/projects/conda/en/stable/commands/run.html>）语义写的小脚本——认 `run` / `--prefix|-p` /
`--no-capture-output|-s|--live-stream` / `--cwd` / 命令部分是 REMAINDER（**没有** `--` 分隔符：老 conda 24.5.0 会把它当可执行文件），**没带 `--no-capture-output` 就拒绝**（真 conda 会捕获 stdout，
worker 协议死锁），激活 = 往子进程环境里放 `CONDA_PREFIX` 和一个只有激活后才有的「SDK 许可串」，然后以继承的 stdio
运行命令。它证明的是 Tavotto 的 argv / 进程树 / 持久化行为，**不证明**真 conda 的行为。

Windows 的 `conda.exe` / DLL 搜索路径 / 厂商软件：`not_run`（POSIX-only 夹具）。
"""

from __future__ import annotations

import json
import os
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


@pytest.fixture(autouse=True)
def _fresh_captures():
    launcher.reset_captures()  # 激活增量只缓存在进程内存里；用例之间不串
    yield
    launcher.reset_captures()


FAKE_CONDA = r"""#!{python}
import json, os, subprocess, sys

args = sys.argv[1:]
if not args or args[0] != "run":
    sys.exit(2)
# conda run 的命令部分是 argparse REMAINDER：选项只认到第一个非选项的位置参数（可执行文件）为止，之后原样透传。
# 较老的 conda（24.5.0）不认 `--`：它也算「位置参数」，于是被当成可执行文件（`--: command not found`，退出码 127）。
rest = args[1:]
n = 0
while n < len(rest) and rest[n].startswith("-") and rest[n] != "--":
    n += 2 if rest[n] in ("--prefix", "-p", "--cwd", "-n", "--name") else 1
opts, cmd = rest[:n], rest[n:]
if cmd and cmd[0] == "--":
    sys.stderr.write("--: command not found\n")
    sys.exit(127)
if not cmd:
    sys.exit(2)
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
env = dict(os.environ, CONDA_PREFIX=prefix, FAKE_SDK_LICENSE="{license}", SECRET_TOKEN="xyz-secret-token-123")
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
if os.environ.get("FAKE_CONDA_SKIP_CMD"):  # 模拟「钩子打印了东西，但被包装的命令根本没成功」
    code = 1
else:
    code = subprocess.Popen(["/bin/sh", "-c", '"$@"; exit $?', "sh", *cmd], env=env).wait()
after = os.environ.get("FAKE_CONDA_AFTER_BANNER")
if after:  # deactivate.d 钩子在命令结束之后往 stdout 打印的内容
    sys.stdout.write(after + "\n")
    sys.stdout.flush()
marker = os.environ.get("FAKE_CONDA_CLEANUP_MARKER")
if marker:  # deactivate.d 的清理：先打印一行，做一会儿事，再留下「清理完成」的记号
    sys.stdout.write("deactivate: cleaning up\n")
    sys.stdout.flush()
    import time

    time.sleep(0.7)
    open(marker, "w").write("done")
sys.exit(code)
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
    args = (proj, "fig.py", py)
    assert launchctx.decide(*args) is not None  # 正对照
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launchctx.decide(*args) is None


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
        launchctx.decide(proj, "fig.py", sys.executable) is None
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
    args = (proj, "fig.py", py)
    assert launchctx.decide(*args) is not None  # 正对照
    conda.unlink()  # conda 没了（卸载 / 挪走）
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launchctx.decide(*args)
    assert exc.value.code == "conda_launcher_unavailable"
    # 用户明确关掉开关：裸起，不报错
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launchctx.decide(*args) is None


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


PROBE_ARGV = (
    "import sys, json\n"
    "print(json.dumps({'argv': sys.argv, 'utf8': sys.flags.utf8_mode, "
    "'nobyte': sys.flags.dont_write_bytecode, 'x': sys._xoptions.get('tavotto_x')}))\n"
)


@needs_worker
def test_per_interpreter_caches_are_partitioned_by_launch_context(activation_only_env, monkeypatch):
    """同一个解释器：裸起得出的事实 / 体检结论 / 选择结论，不能在 Conda 激活下原样复用（反之亦然）。"""
    from tavotto.engine import depplan, projectenv, userenvs

    py, prefix, log = activation_only_env
    depplan.reset_cache()
    projectenv.reset_cache()
    _pool.reset_worker_python()

    # ---- depplan 目标事实
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")
        bare = depplan.target_facts(py)
        assert bare is not None and bare.context == "direct"
        assert depplan.target_facts(py) is bare  # 同一上下文：命中缓存
    assert conda_calls(log) == []
    activated = depplan.target_facts(py)  # 换成激活：必须重新探测
    assert activated is not None and activated.context.startswith("conda|")
    assert activated is not bare and len(conda_calls(log)) == 1
    assert depplan.target_facts(py) is activated and len(conda_calls(log)) == 1
    with monkeypatch.context() as m:  # 再换回裸起：命中当初裸起的那份，没有新的 conda 调用
        m.setenv(launcher.ENV_DISABLE, "0")
        assert depplan.target_facts(py) is bare
    assert len(conda_calls(log)) == 1
    # 该解释器的 reset 一并清掉所有上下文
    depplan.reset_cache(py)
    assert depplan.target_facts(py) is not activated

    # ---- projectenv 系统候选体检缓存
    entry = {"python": py, "source": "system"}
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")
        assert projectenv.reprobe_system_candidate(entry, "vendor_sdk")["ok"] is False
    assert projectenv.reprobe_system_candidate(entry, "vendor_sdk")["ok"] is True
    contexts = {k[2] for k in projectenv._system_probe_cache if k[1] == "vendor_sdk"}
    assert "direct" in contexts and any(c.startswith("conda|") for c in contexts)

    # ---- userenvs 体检缓存键
    key_conda = userenvs._cache_key(py, ("x",), False)
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")
        key_direct = userenvs._cache_key(py, ("x",), False)
    assert key_conda != key_direct

    # ---- pool 的 import matplotlib 体检结论
    _pool._project_python_ok.clear()
    _pool._record_verdict(py, True)  # 在激活上下文里记下
    assert _pool._cached_verdict(py) is True
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")
        assert _pool._cached_verdict(py) is None  # 裸起上下文里没验过
    _pool.forget_python_verdict(py)
    assert _pool._cached_verdict(py) is None

    # ---- 选定的渲染解释器
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    try:
        assert _pool.select_worker_python()[0] == py  # 激活下选定
        with monkeypatch.context() as m:
            m.setenv(launcher.ENV_DISABLE, "0")
            with pytest.raises(_pool.WorkerError) as bad:  # 裸起：不能拿激活下的结论冒充
                _pool.select_worker_python()
            assert bad.value.code == "explicit_python_unusable"
    finally:
        _pool.reset_worker_python()


@needs_worker
def test_spawn_gates_run_inside_the_per_script_launch_scope(conda_world, tmp_path, monkeypatch):
    """`_new_worker` 里的依赖门（SPAWN_GATES）也要用与 worker 相同的每脚本启动决策：记录为 direct、开关打开时，门里的预检也裸起。"""
    import hashlib

    proj, prefix, log, counter = conda_world
    py = str(prefix / "bin" / "python")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    seen: list[str] = []

    def gate(figures_dir, script_name):
        seen.append(launcher.context_key(py))

    monkeypatch.setattr(_pool, "SPAWN_GATES", [gate])
    rcpt = _good_rcpt(proj, py)
    rcpt.python_source = "env_override"
    rcpt.source_revision = hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest()
    assert launchctx.record_success(proj, "fig.py", rcpt)  # 启动方式：direct
    worker = None
    try:
        # 构造函数里 worker 本身会因记录是 direct 而裸起（脚本需要激活 → 会失败），这里只关心门看到的上下文
        with pytest.raises(_pool.WorkerError):
            _pool._new_worker("fig.py", str(proj), "__main__").ensure_built()
    finally:
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
    assert seen == [
        "direct"
    ]  # 门在范围里：记录为 direct → 门里的探针也是 direct（无范围时会是 conda|…）
    assert launcher.context_key(py).startswith("conda|")  # 对照：范围外现场检测是 conda


# ---------------------------------------------------------------------------
# 启动决策收口（#975 r4256685537 / 545 / 555）
# ---------------------------------------------------------------------------
@posix_only
def test_a_direct_record_in_a_conda_env_is_invalidated_when_the_environment_changes(
    tmp_path, monkeypatch
):
    """解释器在 Conda 环境里时，direct 记录也记环境版本：装 / 升降包之后「裸起成功过」不再成立，要重新判断启动方式。"""
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    py = str(prefix / "bin" / "python")
    rcpt = _good_rcpt(proj, py)  # launcher=None：裸起成功
    assert launchctx.record_success(proj, "fig.py", rcpt)
    record = runconfig.launch_record(proj, "fig.py")
    assert record["launcher"]["kind"] == "direct" and record["env_state"]  # 记了环境版本
    assert launchctx.decide(proj, "fig.py", py) is None  # 环境没变：沿用裸起
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")  # conda 事务：环境版本变了
    found = launchctx.decide(proj, "fig.py", py)
    assert found is not None and found.prefix == str(prefix)  # 重新判断：现在要激活
    assert (
        launchctx.verify(
            record,
            project_root=proj,
            script="fig.py",
            python=py,
            python_source=None,
            facets=launchctx.FACETS_LAUNCHER,
        )
        == launchctx.REASON_ENVIRONMENT
    )


@needs_worker
def test_a_vanished_conda_surfaces_the_stable_error_before_any_preflight_can_misreport(
    conda_world, tmp_path, monkeypatch
):
    """记录说要 Conda、现在找不到 conda：打开脚本时预检 / 依赖门也不降级成裸起（会误报缺依赖、提议修复），
    直接是 `conda_launcher_unavailable`（与 worker 同一个稳定码）。"""
    from tavotto.engine import preparation

    proj, prefix, log, counter = conda_world
    py = str(prefix / "bin" / "python")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    rcpt = _good_rcpt(proj, py)
    rcpt.python_source = "env_override"
    rcpt.launcher = launcher.CondaLauncher(
        conda_exe=str(prefix.parent.parent / "bin" / "conda"), prefix=str(prefix)
    ).public()
    import hashlib

    rcpt.source_revision = hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest()
    assert launchctx.record_success(proj, "fig.py", rcpt)
    (prefix.parent.parent / "bin" / "conda").unlink()
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.delenv("CONDA_EXE", raising=False)
    # 起会话：预检（选解释器）与依赖门都在范围里，错误码就是稳定码，不是缺依赖 / 显式解释器不可用
    with pytest.raises(_pool.WorkerError) as werr:
        _pool._new_worker("fig.py", str(proj), "__main__")
    assert werr.value.code == "conda_launcher_unavailable"
    # 准备计划（检查，不执行）里环境错误同样是这个码
    plan = preparation.plan_for(
        project_id="pj",
        project_root=str(proj),
        asset_id="",
        stem="",
        script="fig.py",
        entry="__main__",
        original_artifact=None,
        target="script",
    )
    assert (plan.environment.get("error") or {}).get("code") == "conda_launcher_unavailable"


# ---------------------------------------------------------------------------
# 激活一次、捕获环境、直接启动（#975 重构后的合同）
# ---------------------------------------------------------------------------
def _spy_popen(monkeypatch):
    """记录所有子进程（`subprocess.Popen`，`subprocess.run` 也经它）的 argv 与 env。"""
    seen: list[tuple[list, dict | None]] = []
    real = subprocess.Popen

    class Spy(real):
        def __init__(self, args, *a, **k):
            seen.append((list(args) if not isinstance(args, str) else [args], k.get("env")))
            super().__init__(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    return seen


@posix_only
def test_activation_is_captured_once_cached_in_memory_and_recaptured_when_the_env_changes(
    tmp_path, monkeypatch
):
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    delta = launcher.activated_delta(py, lau)
    assert delta["FAKE_SDK_LICENSE"] == LICENSE and delta["CONDA_PREFIX"] == str(prefix)
    assert delta["SECRET_TOKEN"] == "xyz-secret-token-123"
    assert "SHLVL" not in delta and "_" not in delta  # 每个进程自己都会变的不算激活增量
    (call,) = conda_calls(log)
    # 捕获命令：conda run --no-capture-output --prefix <p> <python 绝对路径> -c <代码> <结果文件>；没有 `--`
    assert call["opts"] == ["--no-capture-output", "--prefix", str(prefix)]
    assert call["cmd"][0] == py and call["cmd"][1] == "-c" and "--" not in call["cmd"]
    assert (
        launcher.activated_delta(py, lau) is delta and len(conda_calls(log)) == 1
    )  # 命中进程内缓存
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")  # 环境版本变了
    assert launcher.activated_delta(py, lau) is not delta and len(conda_calls(log)) == 2
    # 结果文件用完即删，而且缓存不落盘
    assert not list((tmp_path / "data" / "probe-results").glob("*"))


@posix_only
def test_capture_failure_is_the_stable_error_and_hook_output_never_matters(tmp_path, monkeypatch):
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    # activate.d / deactivate.d 往 stdout 打印什么（含 JSON 形状、没有换行）都不影响结果：结果走结果文件
    monkeypatch.setenv("FAKE_CONDA_BANNER", '{"FAKE_SDK_LICENSE": "from-a-hook"}')
    monkeypatch.setenv("FAKE_CONDA_BANNER_NO_NEWLINE", "1")
    monkeypatch.setenv("FAKE_CONDA_AFTER_BANNER", '{"CONDA_PREFIX": "also-a-hook"}')
    delta = launcher.activated_delta(py, lau)
    assert delta["FAKE_SDK_LICENSE"] == LICENSE and delta["CONDA_PREFIX"] == str(prefix)
    # conda 起不来 / 退出非 0：LauncherUnavailable（pool 转成 conda_launcher_unavailable）
    launcher.reset_captures()
    monkeypatch.setenv("FAKE_CONDA_SKIP_CMD", "1")
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launcher.activated_delta(py, lau)
    assert exc.value.code == "conda_launcher_unavailable"
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")
    ghost = launcher.CondaLauncher(conda_exe=str(tmp_path / "no-such-conda"), prefix=str(prefix))
    with pytest.raises(launcher.LauncherUnavailable):
        launcher.activated_delta(py, ghost)


@posix_only
def test_merge_env_keeps_tavottos_own_increment_and_adds_the_activation(tmp_path, monkeypatch):
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    base = {"MPLCONFIGDIR": "/tavotto/mpl", "PATH": "/usr/bin"}
    env = launcher.merge_env(lau, py, base)
    assert env["MPLCONFIGDIR"] == "/tavotto/mpl" and env["FAKE_SDK_LICENSE"] == LICENSE
    assert base == {"MPLCONFIGDIR": "/tavotto/mpl", "PATH": "/usr/bin"}  # 不改调用方的 dict
    assert launcher.merge_env(None, py, base) is base  # 直接启动：原样


@needs_worker
def test_every_spawn_site_starts_the_target_directly_with_the_same_activated_env(
    activation_only_env, tmp_path, monkeypatch
):
    """worker / 预检（选解释器、体检、目标事实、导入核验）/ 依赖门 / 自检 / 模块盘点：都直接起目标解释器（我们的直接子进程，
    不套 conda run），env 里都有同一份激活增量；conda 只在捕获那一次被调用。"""
    from tavotto.engine import depplan, deprepair, projectenv, runtime

    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    seen = _spy_popen(monkeypatch)
    expected = launchctx.env_for(proj, "fig.py", py, {})
    assert expected["FAKE_SDK_LICENSE"] == LICENSE

    def mine(argv):
        return argv and str(argv[0]) == py

    worker = None
    try:
        with _pool.launch_scope(str(proj), "fig.py"):
            assert _pool._has_matplotlib(py) is True  # 选解释器的预检
            assert projectenv.probe_environment(py)["ok"] is True
            assert depplan.target_facts(py) is not None
            assert deprepair.probe_imports(py, ("vendor_sdk",)) == {"vendor_sdk": ""}
            assert deprepair.worker_self_test(py) == {"ok": True, "figures": 1}
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
        _pool.reset_worker_python()
        worker, resp = _pool.build("fig.py", str(proj), "__main__")
        assert (
            resp["runtime"]["pid"] == worker.child_pid
        )  # 直接子进程：回执的 pid 判据与没有 Conda 时一样
        rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
        assert rcpt.completeness == receipt.COMPLETENESS_COMPLETE and rcpt.pid_check == "ok"
        # 版本盘点（环境状态 API，app 注入 env_for）：激活环境里 import 得到 vendor_sdk，不注入则看不到
        assert runtime.probe_packages(py, ["vendor_sdk"], env_for=launcher.activate_env) == {
            "vendor_sdk": "unknown"
        }
    finally:
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()
    # `bootstrap._probe`（问系统 Python 能不能 `import venv`）问的不是 worker 的解释器环境，PR 表里列为「不改」
    ours = [(a, e) for a, e in seen if mine(a) and not any("import venv" in str(x) for x in a)]
    assert runtime.probe_packages(py, ["vendor_sdk"]) == {
        "vendor_sdk": None
    }  # 对照：不注入激活就看不到
    assert len(ours) >= 6, [a[:3] for a, _ in ours]
    # 目标解释器从来不是 conda 的子进程：每个 `ours` 的 argv[0] 就是解释器本身（`mine` 的判据），conda 只出现在捕获那一次
    assert len(conda_calls(log)) == 1  # conda 只在捕获那一次被调用
    for argv, env in ours:
        assert env is not None and env.get("FAKE_SDK_LICENSE") == LICENSE, [
            str(a)[:60] for a in argv[:5]
        ]
        assert any(
            x.endswith("activated_site") for x in env.get("PYTHONPATH", "").split(os.pathsep)
        ), argv[:3]


@needs_worker
def test_kill_switch_makes_every_site_fail_the_way_a_bare_start_does(
    activation_only_env, monkeypatch
):
    from tavotto.engine import deprepair, projectenv

    py, prefix, log = activation_only_env
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert _pool._has_matplotlib(py) is False
    assert projectenv.probe_environment(py)["ok"] is False
    assert deprepair.worker_self_test(py)["ok"] is False
    assert conda_calls(log) == []


@needs_worker
def test_the_activation_secret_never_reaches_any_persisted_or_logged_place(
    conda_world, tmp_path, monkeypatch, caplog
):
    """激活环境里放了 SECRET_TOKEN：runconfig 登记、回执（含私有投影）、worker.log、数据目录里的任何文件、日志都找不到它。"""
    import logging

    caplog.set_level(logging.DEBUG)
    proj, prefix, log, counter = conda_world
    secret = "xyz-secret-token-123"
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    try:
        rcpt = receipt.from_worker(worker, resp, control_plane="python_pool", grant=None)
        assert launchctx.record_success(proj, "fig.py", rcpt)
        blob = json.dumps(rcpt.to_payload(include_private=True), ensure_ascii=False)
        assert secret not in blob
        assert secret not in worker.log_path.read_text(encoding="utf-8", errors="replace")
    finally:
        worker.force_kill()
        _pool.shutdown_all(wait=True)
    assert secret not in caplog.text
    from tavotto.engine import config as _config

    for f in Path(_config.data_dir()).rglob("*"):
        if f.is_file():
            assert secret.encode() not in f.read_bytes(), f
    assert "SECRET_TOKEN" not in json.dumps(runconfig.launch_record(proj, "fig.py"))


@needs_worker
def test_a_worker_started_with_the_captured_env_is_a_plain_child_and_cancel_leaves_nothing(
    conda_world,
):
    proj, prefix, log, counter = conda_world
    worker, resp = _pool.build("fig.py", str(proj), "__main__")
    pid = resp["runtime"]["pid"]
    assert pid == worker.child_pid and alive(pid)
    assert runs(counter)[0]["conda_prefix"] == str(prefix)  # 脚本里看得到激活变量
    worker.force_kill()
    _pool.shutdown_all(wait=True)
    assert wait_gone(pid)


@pytest.fixture
def real_hooks():
    """真 conda 环境里放 activate.d / deactivate.d 钩子（设变量、打印横幅，含 JSON 形状的），用完清掉。"""
    created: list[Path] = []
    for d in ("activate.d", "deactivate.d"):
        folder = Path(REAL_PREFIX) / "etc" / "conda" / d
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("p09_*.sh"):
            stale.unlink()

    def add(kind: str, name: str, text: str) -> None:
        f = Path(REAL_PREFIX) / "etc" / "conda" / kind / f"p09_{name}.sh"
        f.write_text(text, encoding="utf-8")
        created.append(f)

    yield add
    for f in created:
        f.unlink(missing_ok=True)


@needs_real_conda
@pytest.mark.parametrize("which", ["current", "old"])
def test_real_conda_capture_sees_activate_d_variables_whatever_the_hooks_print(
    real_hooks, tmp_path, monkeypatch, which
):
    """真 conda（26.7.2 与 24.5.0）：activate.d 设的变量被捕获；hook 往 stdout 打印什么（没有换行的、JSON 形状的）都不影响。"""
    exe = _real_conda() if which == "current" else os.environ.get("TAVOTTO_TEST_OLD_CONDA_EXE", "")
    if not exe or not Path(exe).is_file():
        pytest.skip("没有老版 conda（TAVOTTO_TEST_OLD_CONDA_EXE）：not_run")
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    real_hooks("activate.d", "vars", "export P09_VENDOR_VAR=activated-ok\n")
    real_hooks("activate.d", "json", 'printf \'{"P09_VENDOR_VAR": "from-a-hook"}\'\n')
    real_hooks("deactivate.d", "json", 'echo \'{"P09_VENDOR_VAR": "from-deactivate"}\'\n')
    lau = launcher.CondaLauncher(conda_exe=exe, prefix=REAL_PREFIX)
    delta = launcher.activated_delta(str(Path(REAL_PREFIX) / "bin" / "python"), lau)
    assert delta["P09_VENDOR_VAR"] == "activated-ok"
    assert delta["CONDA_PREFIX"] == REAL_PREFIX


@needs_real_conda
def test_real_conda_four_sites_see_activate_d_changes_and_kill_switch_is_the_negative_control(
    real_hooks, tmp_path, monkeypatch
):
    """真 conda：activate.d 把一个目录加进 PYTHONPATH，目录里的 vendor_sdk_p09 只有激活后才 import 得到——预检（体检、
    导入核验、目标事实）、自检、worker 四处都能用；关开关作负对照。"""
    from tavotto.engine import depplan, deprepair, projectenv

    site = tmp_path / "vendor_site"
    site.mkdir()
    (site / "vendor_sdk_p09.py").write_text("VALUE = 7\n", encoding="utf-8")
    real_hooks(
        "activate.d", "pythonpath", f'export PYTHONPATH="{site}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
    )
    real_hooks("activate.d", "banner", "printf 'initializing vendor SDK...'\n")
    py = str(Path(REAL_PREFIX) / "bin" / "python")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import vendor_sdk_p09, matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, vendor_sdk_p09.VALUE]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    worker = None
    try:
        with monkeypatch.context() as m:
            m.setenv(launcher.ENV_DISABLE, "0")  # 负对照：直接启动全部看不到
            assert deprepair.probe_imports(py, ("vendor_sdk_p09",))["vendor_sdk_p09"]
            assert projectenv.probe_environment(py, "vendor_sdk_p09")["ok"] is False
        with _pool.launch_scope(str(proj), "fig.py"):
            assert deprepair.probe_imports(py, ("vendor_sdk_p09",)) == {"vendor_sdk_p09": ""}
            assert projectenv.probe_environment(py, "vendor_sdk_p09")["ok"] is True
            assert depplan.target_facts(py) is not None
            assert deprepair.worker_self_test(py) == {"ok": True, "figures": 1}
        worker, resp = _pool.build("fig.py", str(proj), "__main__")
        assert resp["runtime"]["pid"] == worker.child_pid  # 直接子进程
    finally:
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


@posix_only
def test_worker_self_test_only_trusts_a_protocol_envelope_that_echoes_its_request_id(tmp_path):
    """自检只认 v1 信封：别的 JSON（`{"ok": true}`）冒充不了 worker 的回复。用一个假的「worker 解释器」脚本验证。"""
    from tavotto.engine import deprepair

    def fake(name: str, body: str) -> str:
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    spoof = 'print(\'{"ok": true, "stems": {"x": 1}}\', flush=True)\n'
    honest = (
        "import json, sys\n"
        "req = json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'protocol_version': req['protocol_version'], 'request_id': req['request_id'],"
        " 'ok': True, 'stems': {'a': 1, 'b': 2}}), flush=True)\n"
    )
    assert deprepair.worker_self_test(fake("spoof", spoof))["ok"] is False
    assert deprepair.worker_self_test(fake("honest", spoof + honest)) == {"ok": True, "figures": 2}
