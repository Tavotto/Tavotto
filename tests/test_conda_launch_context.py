"""P09：复用成功启动上下文 + Conda 激活（EX07 / EX11，AC-11 / AC-19）。

**激活一次、捕获环境、直接启动**：对 Conda 环境只跑一次 `conda run … python -c`（结果走 0600 结果文件）取激活后的环境变量，
所有用目标解释器起子进程的地方都**直接**起它、env 换成「当前环境 + 激活增量」。启动方式是解释器路径的纯函数
（`launcher.launcher_for_interpreter`）：与脚本、项目、成功记录无关，任何线程、任何入口结果相同。

两层夹具：

* **模拟 conda**（`FAKE_CONDA`，POSIX only）：按官方 `conda run` 文档语义写的小脚本——认 `run` / `--prefix|-p` /
  `--no-capture-output|-s|--live-stream` / `--cwd`，命令部分是 REMAINDER（**没有** `--` 分隔符：老 conda 24.5.0 会把它当可执行文件），
  没带 `--no-capture-output` 就拒绝；激活 = 往子进程环境里放 `CONDA_PREFIX` 和一个只有激活后才有的「SDK 许可串」。它证明 Tavotto 的
  argv / 缓存 / fail-closed 行为，**不证明**真 conda 的行为。
* **真 conda**（`TAVOTTO_TEST_CONDA_PREFIX` / `TAVOTTO_TEST_OLD_CONDA_EXE`，没有就 skip = not_run）：26.7.2 与 24.5.0。

Windows：`conda.exe` / DLL 搜索路径 / 厂商软件 `not_run`；本文件所有依赖 shell 脚本假 conda 或 POSIX 路径的用例一律 `skipif(nt)`。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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


#: 用例里“用户自己的 conda 安装根”（替 `launcher._STANDARD_ROOTS`）：`make_fake_conda` 往里登记，autouse 夹具每个用例清空
_FAKE_ROOTS: list[str] = []


@pytest.fixture(autouse=True)
def _isolated_conda_roots(monkeypatch):
    """本机真实装着的 conda（~/miniconda3 之类）不许影响用例：常见安装位置只认这个用例自己建的假安装根。"""
    _FAKE_ROOTS.clear()
    monkeypatch.setattr(launcher, "_standard_conda_roots", lambda: list(_FAKE_ROOTS))
    yield
    _FAKE_ROOTS.clear()


@pytest.fixture(autouse=True)
def _fresh_captures():
    launcher.reset_captures()  # 激活增量只缓存在进程内存里；用例之间不串
    yield
    # 上一个用例留下的进程级状态 / 后台线程不许在下一个用例里拿旧解释器去捕获（捕获读的是“此刻”的环境变量与数据目录，
    # 会串进下一个用例的 conda 日志）：收掉会话、清各层按解释器的缓存，再等 Tavotto 自己的后台线程退出
    from tavotto.engine import depplan, deprepair, projectenv, userenvs

    _pool.shutdown_all(wait=True)
    _pool.reset_worker_python()
    projectenv.reset_cache()
    depplan.reset_cache()
    userenvs.reset_cache()
    deprepair.reset_state()
    deadline = time.time() + 15
    while time.time() < deadline and any(
        t.is_alive() and t.name.startswith(("tavotto-", "mm-"))
        for t in threading.enumerate()
        if t is not threading.current_thread()
    ):
        time.sleep(0.05)
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
if os.environ.get("FAKE_CONDA_SLEEP"):  # 模拟 activate.d 卡住
    import time

    time.sleep(float(os.environ["FAKE_CONDA_SLEEP"]))
with open(os.environ["FAKE_CONDA_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"opts": opts, "cmd": cmd}}) + "\n")
if os.environ.get("FAKE_CONDA_HANG_CHILD"):  # 模拟 conda 起了子进程（activate.d 里的后台进程）然后一直等它
    child = subprocess.Popen(["/bin/sleep", "300"])
    open(os.environ["FAKE_CONDA_HANG_CHILD"], "w").write(str(child.pid))
    child.wait()
env = dict(os.environ, CONDA_PREFIX=prefix, FAKE_SDK_LICENSE="{license}", SECRET_TOKEN="xyz-secret-token-123")
for _name in filter(None, os.environ.get("FAKE_CONDA_UNSET", "").split(",")):  # activate.d 里的 `unset NAME`
    env.pop(_name, None)
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
sys.exit(code)
"""


def make_fake_conda(
    tmp_path: Path, real_python: str, *, broken: bool = False, name: str = "miniconda"
) -> tuple[Path, Path, Path]:
    """root/bin/conda（模拟）+ root/envs/e1（prefix，bin/python 是转发到 `real_python` 的脚本）。
    回 (prefix, 模拟 conda, 日志文件)。"""
    root = tmp_path / name
    prefix = root / "envs" / "e1"
    (prefix / "conda-meta").mkdir(parents=True)
    (prefix / "conda-meta" / "history").write_text("==> 2026-10-13 <==\n", encoding="utf-8")
    (prefix / "bin").mkdir()
    py = prefix / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{real_python}" "$@"\n', encoding="utf-8")
    (root / "bin").mkdir(parents=True)
    (root / "conda-meta").mkdir(exist_ok=True)  # 真正的安装根（base）也是一个带 conda-meta 的环境
    conda = root / "bin" / "conda"
    conda.write_text(
        "#!/bin/sh\nexit 3\n"
        if broken
        else FAKE_CONDA.format(python=sys.executable, license=LICENSE),
        encoding="utf-8",
    )
    for p in (py, conda):
        os.chmod(p, 0o755)
    # 这是“用户自己的 conda 安装”（像 ~/miniconda3）：它的 `envs/<名字>` 才会被激活
    _FAKE_ROOTS.append(os.path.realpath(root))
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


@posix_only
def test_public_reference_has_no_environment(tmp_path):
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    assert set(lau.public()) == {"kind", "conda_exe", "prefix"}
    assert not hasattr(launcher, "from_public")  # 引用只对账，不提供「读回来就执行」的入口


@posix_only
def test_detect_conda_env_and_negative_controls(tmp_path, monkeypatch):
    prefix, conda, _ = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.delenv("CONDA_EXE", raising=False)
    found = launcher.launcher_for_interpreter(prefix / "bin" / "python")
    assert found == launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    # 不是 Conda 环境（没有 conda-meta）：裸起
    plain = tmp_path / "venv" / "bin"
    plain.mkdir(parents=True)
    (plain / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    assert launcher.launcher_for_interpreter(plain / "python") is None
    # 找不到 conda：裸起，不报错
    lone = tmp_path / "lone"
    (lone / "conda-meta").mkdir(parents=True)
    (lone / "bin").mkdir()
    (lone / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert launcher.launcher_for_interpreter(lone / "bin" / "python") is None
    # 显式关闭
    monkeypatch.setenv(launcher.ENV_DISABLE, "0")
    assert launcher.launcher_for_interpreter(prefix / "bin" / "python") is None


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
        # 应用子进程里没有夹具的假安装根：像用户的终端一样给 CONDA_EXE
        "CONDA_EXE": str(prefix.parent.parent / "bin" / "conda"),
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


REAL_SRC = os.environ.get("TAVOTTO_TEST_CONDA_PREFIX", "")
needs_real_conda = pytest.mark.skipif(
    os.name == "nt" or not REAL_SRC or not (Path(REAL_SRC) / "conda-meta").is_dir(),
    reason="没有真实 conda 环境（TAVOTTO_TEST_CONDA_PREFIX 指向带 matplotlib 的 conda prefix）：not_run",
)


def _real_conda() -> str:
    found = launcher.launcher_for_interpreter(Path(REAL_SRC) / "bin" / "python")
    assert found is not None, "真实 prefix 下找不到 conda 可执行文件"
    return found.conda_exe


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
    assert launcher.launcher_for_interpreter(prefix / "bin" / "python") is not None  # 正对照
    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    assert launcher.launcher_for_interpreter(prefix / "bin" / "python") is None


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
        # 应用子进程里没有夹具的假安装根：像用户的终端一样给 CONDA_EXE
        "CONDA_EXE": str(prefix.parent.parent / "bin" / "conda"),
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
    """成功后写下可验证的启动上下文；但「再次打开」不靠省略 argv 推断沿用：省略 argv 就是无参数运行。`reuse_last_success` 由 V2 PR4（#982）接上消费端，
    这里最后一次成功是无参数运行，没有可沿用的，所以带着它也是无参数（沿用本身见 tests/test_reuse_last_success.py）。"""
    proj, prefix, log, counter = conda_world
    work = tmp_path / "work"
    env = {
        "TAVOTTO_USER_ENV_DISCOVERY": "0",
        "PYTHONPATH": str(ROOT / "src"),
        "TAVOTTO_WORKER_PYTHON": str(prefix / "bin" / "python"),
        "FAKE_CONDA_LOG": str(log),
        # 应用子进程里没有夹具的假安装根：像用户的终端一样给 CONDA_EXE
        "CONDA_EXE": str(prefix.parent.parent / "bin" / "conda"),
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
        assert [r["argv"] for r in runs(counter)][-1] == []  # 上一次成功是无参数运行：没有可沿用的


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
        assert bare is not None and bare.context.startswith("direct")
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
    assert any(c.startswith("direct") for c in contexts) and any(
        c.startswith("conda|") for c in contexts
    )

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
    # 捕获命令：conda run --no-capture-output --prefix <p> <python 绝对路径> -I -S -c <代码> <结果文件>；没有 `--`
    assert call["opts"] == ["--no-capture-output", "--prefix", str(prefix)]
    assert call["cmd"][:4] == [py, "-I", "-S", "-c"] and "--" not in call["cmd"]
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
def test_capture_runs_no_code_from_the_environment_and_does_not_count_sitecustomize_as_activation(
    tmp_path, monkeypatch
):
    """捕获只读 os.environ：环境里的 sitecustomize（经 PYTHONPATH 或 site-packages）不执行，它对环境的改动也不进增量。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    hook = tmp_path / "hook"
    hook.mkdir()
    ran = tmp_path / "sitecustomize-ran"
    # 模拟的 conda 自己也是 Python 脚本、同样继承 PYTHONPATH：只在“被探测的解释器”里生效，conda 进程自己跳过
    (hook / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if not any(str(a).endswith('/bin/conda') for a in sys.orig_argv):\n"
        f"    open({str(ran)!r}, 'w').close()\n"
        "    os.environ['FROM_SITECUSTOMIZE'] = '1'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(hook))
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    delta = launcher.activated_delta(py, lau)
    assert delta["FAKE_SDK_LICENSE"] == LICENSE  # 激活本身照常捕获
    assert "FROM_SITECUSTOMIZE" not in delta
    assert not ran.exists()


@posix_only
def test_a_caller_waiting_behind_another_capture_only_waits_its_own_budget(tmp_path, monkeypatch):
    """同一个环境正在被别的请求捕获（例如 60s 的预热）：后来的 7s 体检只等自己的预算，到点 fail-closed，不缓存。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    key = (lau.prefix, launcher.prefix_state(lau.prefix), lau.conda_exe)
    with launcher._captured_lock:
        gate = launcher._capture_gates.setdefault(key, threading.Lock())
    gate.acquire()  # 模拟另一个请求正在捕获
    try:
        t0 = time.monotonic()
        with pytest.raises(launcher.LauncherUnavailable) as exc:
            launcher.activated_delta(py, lau, timeout=0.5)
        assert time.monotonic() - t0 < 5
        assert exc.value.code == "conda_launcher_unavailable"
    finally:
        gate.release()
    assert conda_calls(log) == []
    assert launcher.activated_delta(py, lau)["FAKE_SDK_LICENSE"] == LICENSE  # 失败没有被缓存


@posix_only
def test_startup_revisits_result_files_that_were_too_recent_to_sweep(tmp_path, monkeypatch):
    """上一个实例刚被杀、留下的结果文件还没到过期线：启动那次扫不掉，过了过期线必须再扫一次。"""
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    results = tmp_path / "data" / "probe-results"
    results.mkdir(parents=True)
    leftover = results / "activated-left.json"
    leftover.write_text('{"SECRET_TOKEN": "x"}', encoding="utf-8")
    scheduled, started = [], []

    class FakeTimer:
        def __init__(self, delay, fn):
            scheduled.append((delay, fn))
            self.daemon = False

        def start(self):
            started.append(self.daemon)

    monkeypatch.setattr(launcher.threading, "Timer", FakeTimer)
    monkeypatch.setattr(launcher, "_startup_done", False)
    monkeypatch.setattr(launcher.atexit, "register", lambda fn: None)
    launcher.startup()
    assert leftover.exists()  # 太新：启动那次不删
    ((delay, fn),) = scheduled
    assert started == [True]  # 真的排上了，而且是守护线程（不拖住退出）
    assert delay > launcher._STALE_RESULT_S
    old = time.time() - launcher._STALE_RESULT_S - 1
    os.utime(leftover, (old, old))  # 时间到了
    fn()
    assert not leftover.exists()


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
    captures: list[dict] = []  # 每次真捕获的键与来源：断言失败时指向第二次捕获的原因
    real_capture = launcher._capture  # noqa: SLF001

    def recording_capture(python, lau, timeout):
        import traceback

        captures.append(
            {
                "python": python,
                "prefix": lau.prefix,
                "state": launcher.prefix_state(lau.prefix),
                "exe": lau.conda_exe,
                "timeout": timeout,
                "thread": threading.current_thread().name,
                "stack": [f.name for f in traceback.extract_stack()[-8:-1]],
            }
        )
        return real_capture(python, lau, timeout)

    monkeypatch.setattr(launcher, "_capture", recording_capture)
    expected = launcher.activate_env(py, {})
    assert expected["FAKE_SDK_LICENSE"] == LICENSE

    def mine(argv):
        return argv and str(argv[0]) == py

    worker = None
    try:
        assert _pool._has_matplotlib(py) is True  # 选解释器的预检
        assert projectenv.probe_environment(py)["ok"] is True
        assert depplan.target_facts(py) is not None
        assert deprepair.probe_imports(py, ("vendor_sdk",)) == {"vendor_sdk": ""}
        assert deprepair.worker_self_test(py) == {"ok": True, "figures": 1}
        assert deprepair._run([py, "-c", "import vendor_sdk"], 60)[0] == 0
        assert deprepair._run_pip([py, "-c", "import vendor_sdk"], threading.Event(), None)[0] == ""
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
    assert len(ours) >= 8, [a[:3] for a, _ in ours]
    # 目标解释器从来不是 conda 的子进程：每个 `ours` 的 argv[0] 就是解释器本身（`mine` 的判据），conda 只出现在捕获那一次
    calls = conda_calls(log)
    assert len(calls) == 1, json.dumps(
        {"conda_calls": calls, "captures": captures}, ensure_ascii=False, indent=1
    )  # conda 只在捕获那一次被调用
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


@pytest.fixture(scope="module")
def real_prefix(tmp_path_factory):
    """真 conda 用例各用自己的克隆环境（`conda create --clone --offline`）：它们会写 activate.d 钩子 / `conda env config vars`，
    不能踩共享的 `TAVOTTO_TEST_CONDA_PREFIX`，也不能和别的进程并行跑时互相踩。"""
    exe = _real_conda()
    dest = tmp_path_factory.mktemp("realclone") / "env"
    done = subprocess.run(
        [exe, "create", "-p", str(dest), "--clone", REAL_SRC, "--offline", "-y", "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert done.returncode == 0, done.stderr[-400:]
    return str(dest)


@pytest.fixture
def real_hooks(real_prefix, monkeypatch):
    """真 conda 环境里放 activate.d / deactivate.d 钩子（设变量、打印横幅，含 JSON 形状的），用完清掉。"""
    created: list[Path] = []
    monkeypatch.setenv("CONDA_EXE", _real_conda())  # 克隆环境在 tmp 里，找 conda 靠它
    for d in ("activate.d", "deactivate.d"):
        folder = Path(real_prefix) / "etc" / "conda" / d
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("p09_*.sh"):
            stale.unlink()

    def add(kind: str, name: str, text: str) -> None:
        f = Path(real_prefix) / "etc" / "conda" / kind / f"p09_{name}.sh"
        f.write_text(text, encoding="utf-8")
        created.append(f)

    yield add
    for f in created:
        f.unlink(missing_ok=True)


@needs_real_conda
@pytest.mark.parametrize("which", ["current", "old"])
def test_real_conda_capture_sees_activate_d_variables_whatever_the_hooks_print(
    real_hooks, real_prefix, tmp_path, monkeypatch, which
):
    """真 conda（26.7.2 与 24.5.0）：activate.d 设的变量被捕获；hook 往 stdout 打印什么（没有换行的、JSON 形状的）都不影响。"""
    exe = _real_conda() if which == "current" else os.environ.get("TAVOTTO_TEST_OLD_CONDA_EXE", "")
    if not exe or not Path(exe).is_file():
        pytest.skip("没有老版 conda（TAVOTTO_TEST_OLD_CONDA_EXE）：not_run")
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    real_hooks("activate.d", "vars", "export P09_VENDOR_VAR=activated-ok\n")
    real_hooks("activate.d", "json", 'printf \'{"P09_VENDOR_VAR": "from-a-hook"}\'\n')
    real_hooks("deactivate.d", "json", 'echo \'{"P09_VENDOR_VAR": "from-deactivate"}\'\n')
    lau = launcher.CondaLauncher(conda_exe=exe, prefix=real_prefix)
    delta = launcher.activated_delta(str(Path(real_prefix) / "bin" / "python"), lau)
    assert delta["P09_VENDOR_VAR"] == "activated-ok"
    assert delta["CONDA_PREFIX"] == real_prefix


@needs_real_conda
def test_real_conda_four_sites_see_activate_d_changes_and_kill_switch_is_the_negative_control(
    real_hooks, real_prefix, tmp_path, monkeypatch
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
    py = str(Path(real_prefix) / "bin" / "python")
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
        assert deprepair.probe_imports(py, ("vendor_sdk_p09",)) == {"vendor_sdk_p09": ""}
        assert projectenv.probe_environment(py, "vendor_sdk_p09")["ok"] is True
        assert depplan.target_facts(py) is not None
        assert deprepair.worker_self_test(py) == {"ok": True, "figures": 1}
        assert deprepair._run([py, "-c", "import vendor_sdk_p09"], 60)[0] == 0
        assert (
            deprepair._run_pip([py, "-c", "import vendor_sdk_p09"], threading.Event(), None)[0]
            == ""
        )
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


# ---------------------------------------------------------------------------
# #975 独立对抗审查：启动方式是解释器的纯函数；捕获失败一律 fail-closed 且不缓存
# ---------------------------------------------------------------------------
def _fail_capture(monkeypatch):
    monkeypatch.setenv("FAKE_CONDA_SKIP_CMD", "1")  # conda run 本身退出码非 0：捕获失败


@needs_worker
def test_a_failed_capture_never_falls_back_to_a_bare_start_at_any_entry(
    activation_only_env, monkeypatch
):
    """P1-2：任何入口（范围内外）捕获失败都是 `LauncherUnavailable`，不退回裸起；条件恢复后同一入口立即可用。"""
    py, prefix, log = activation_only_env
    _fail_capture(monkeypatch)
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launcher.activate_env(py, {})
    assert exc.value.code == "conda_launcher_unavailable"
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")
    assert launcher.activate_env(py, {})["FAKE_SDK_LICENSE"] == LICENSE  # 失败没有被缓存


@needs_worker
def test_a_failed_activation_is_a_transient_verdict_that_no_cache_keeps(
    activation_only_env, monkeypatch
):
    """P1-2：体检 / 预检遇到捕获失败返回「不可用：conda_launcher_unavailable」，且不写任何 verdict / facts 缓存。"""
    from tavotto.engine import depplan, projectenv, userenvs

    py, prefix, log = activation_only_env
    projectenv.reset_cache()
    depplan.reset_cache()
    _pool.reset_worker_python()
    _fail_capture(monkeypatch)
    health = projectenv.probe_environment(py, "vendor_sdk")
    assert health["ok"] is False and health["code"] == "conda_launcher_unavailable"
    entry = {"python": py, "source": "system"}
    assert projectenv.reprobe_system_candidate(entry, "vendor_sdk")["code"] == (
        "conda_launcher_unavailable"
    )
    assert projectenv.probe_system_candidates([(py, "system")], "vendor_sdk")[0]["code"] == (
        "conda_launcher_unavailable"
    )
    with pytest.raises(launcher.LauncherUnavailable):  # 不折成 None（那是“目标不可用”）
        depplan.target_facts(py)
    userenvs._probe(py, ("vendor_sdk",))
    userenvs.remember_probe(py, ("vendor_sdk",), health)
    assert not projectenv._system_probe_cache and not depplan._facts_cache
    assert not userenvs._probe_cache and not _pool._project_python_ok
    # 条件恢复：同一批入口立刻给出真结论，没有被“不可用”钉住
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")
    assert projectenv.probe_environment(py, "vendor_sdk")["ok"] is True
    assert projectenv.probe_system_candidates([(py, "system")], "vendor_sdk")[0]["ok"] is True
    assert depplan.target_facts(py) is not None


@needs_worker
def test_a_failed_activation_on_the_remembered_interpreter_hands_over_the_code_and_forgets_nothing(
    activation_only_env, tmp_path, monkeypatch
):
    """P1-1：项目记住的 Conda 解释器遇到捕获失败：稳定码直接给用户；不作废记住的解释器、不记体检结论、不静默换别的。"""
    from tavotto.engine import projectenv

    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    _pool.reset_worker_python()
    projectenv.reset_cache()
    assert projectenv.remember(proj, py, automatic=True, trigger=projectenv.TRIGGER_FIRST_OPEN)
    _fail_capture(monkeypatch)
    with pytest.raises(_pool.WorkerError) as exc:
        _pool.resolve_worker_python(str(proj), script="fig.py")
    assert exc.value.code == "conda_launcher_unavailable"
    assert projectenv.remembered(proj) == py  # 没被作废
    assert _pool.invalidated_decision(proj) is None
    assert not _pool._project_python_ok
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")
    assert _pool.resolve_worker_python(str(proj), script="fig.py")[0] == py  # 恢复后照常


@needs_worker
def test_the_default_chain_skips_one_failing_candidate_and_an_explicit_choice_gets_the_code(
    tmp_path, monkeypatch
):
    """P2-5：默认链条里一个坏候选只跳过它；显式选择把稳定码交给用户；全是坏的说真话（不是 no_worker_python）。"""
    bad_prefix, _, _ = make_fake_conda(tmp_path, WORKER_PY, broken=True, name="badconda")
    bad = str(bad_prefix / "bin" / "python")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.setattr(
        _pool,
        "_prioritized_candidates",
        lambda: [(bad, _pool.SOURCE_SYSTEM), (WORKER_PY, "system")],
    )
    _pool.reset_worker_python()
    assert _pool.select_worker_python()[0] == WORKER_PY  # 跳过坏的，继续试后面的
    _pool.reset_worker_python()
    monkeypatch.setattr(_pool, "_prioritized_candidates", lambda: [(bad, _pool.SOURCE_SYSTEM)])
    with pytest.raises(_pool.WorkerError) as only_bad:
        _pool.select_worker_python()
    assert only_bad.value.code == "conda_launcher_unavailable"
    _pool.reset_worker_python()
    monkeypatch.setattr(_pool, "_prioritized_candidates", lambda: [(bad, _pool.SOURCE_ENV)])
    with pytest.raises(_pool.WorkerError) as explicit:
        _pool.select_worker_python()
    assert explicit.value.code == "conda_launcher_unavailable"  # 不是 explicit_python_unusable
    _pool.reset_worker_python()


@needs_worker
def test_a_failed_activation_surfaces_the_stable_code_at_session_start_and_in_the_plan(
    activation_only_env, tmp_path, monkeypatch
):
    from tavotto.engine import preparation

    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    _fail_capture(monkeypatch)
    with pytest.raises(_pool.WorkerError) as werr:
        _pool._new_worker("fig.py", str(proj), "__main__")
    assert werr.value.code == "conda_launcher_unavailable"
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
    _pool.reset_worker_python()


@needs_worker
def test_a_previous_bare_success_does_not_pin_a_bare_start_any_more(
    conda_world, tmp_path, monkeypatch
):
    """启动方式是解释器的纯函数：以前裸起成功过（成功记录是 direct）的 Conda 脚本，今后带上激活环境。记录只是事实。"""
    import hashlib

    proj, prefix, log, counter = conda_world
    py = str(prefix / "bin" / "python")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    rcpt = _good_rcpt(proj, py)
    rcpt.python_source = "env_override"
    rcpt.source_revision = hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest()
    assert launchctx.record_success(proj, "fig.py", rcpt)
    assert runconfig.launch_record(proj, "fig.py")["launcher"]["kind"] == "direct"
    worker, resp = _pool.build("fig.py", str(proj), "__main__")  # 脚本需要激活才能 import
    try:
        assert worker.launcher is not None and resp.get("ok", True)
    finally:
        worker.force_kill()
        _pool.shutdown_all(wait=True)
    assert runs(counter)[0]["conda_prefix"] == str(prefix)


@posix_only
def test_the_same_interpreter_gets_the_same_env_from_any_thread(tmp_path, monkeypatch):
    """P1-3：没有 ContextVar 之类的范围——任意线程（含线程池）、任意入口，同一个解释器拿到的 env 都相同。"""
    from concurrent.futures import ThreadPoolExecutor

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    py = str(prefix / "bin" / "python")
    main_env = launcher.activate_env(py, {})
    with ThreadPoolExecutor(max_workers=4) as ex:
        envs = list(ex.map(lambda _: launcher.activate_env(py, {}), range(8)))
    assert all(e == main_env for e in envs)
    assert main_env["FAKE_SDK_LICENSE"] == LICENSE
    assert launcher.launcher_for_interpreter(py) == launcher.CondaLauncher(
        conda_exe=str(conda), prefix=str(prefix)
    )
    assert len(conda_calls(log)) == 1  # 并发 / 重复调用不重复捕获（命中缓存）


@posix_only
def test_prefix_state_sees_env_config_vars_and_activate_d_changes(tmp_path):
    """P2-1：`conda env config vars set`（写 conda-meta/state）与 activate.d / deactivate.d 的新增 / 改动都换指纹。"""
    prefix, _, _ = make_fake_conda(tmp_path, sys.executable)
    base = launcher.prefix_state(str(prefix))
    state = prefix / "conda-meta" / "state"
    state.write_text('{"env_vars": {"A": "1"}}', encoding="utf-8")
    s1 = launcher.prefix_state(str(prefix))
    assert s1 != base
    state.write_text('{"env_vars": {"A": "22"}}', encoding="utf-8")
    s2 = launcher.prefix_state(str(prefix))
    assert s2 != s1
    hook = prefix / "etc" / "conda" / "activate.d" / "x.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("export A=1\n", encoding="utf-8")
    s3 = launcher.prefix_state(str(prefix))
    assert s3 != s2
    hook.write_text("export A=2222\n", encoding="utf-8")  # 钩子内容改了
    s4 = launcher.prefix_state(str(prefix))
    assert s4 != s3
    dehook = prefix / "etc" / "conda" / "deactivate.d" / "y.sh"
    dehook.parent.mkdir(parents=True)
    dehook.write_text("unset A\n", encoding="utf-8")
    assert launcher.prefix_state(str(prefix)) != s4
    assert launcher.prefix_state(str(prefix)) == launcher.prefix_state(str(prefix))  # 纯读不换


@posix_only
def test_capture_setup_errors_are_the_stable_error_not_a_raw_oserror(tmp_path, monkeypatch):
    """P2-2：mkdir / mkstemp 失败（数据目录不可写）也转成 `LauncherUnavailable`。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(launcher.config, "data_dir", lambda: blocker / "data")  # mkdir 必败
    with pytest.raises(launcher.LauncherUnavailable):
        launcher.activated_delta(py, lau)
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")

    def boom(*a, **k):
        raise PermissionError("no space")

    monkeypatch.setattr(launcher.tempfile, "mkstemp", boom)  # mkstemp 必败
    with pytest.raises(launcher.LauncherUnavailable):
        launcher.activated_delta(py, lau)


@posix_only
def test_stale_result_files_are_swept_at_startup_and_inflight_ones_at_exit(tmp_path, monkeypatch):
    """P2-3：结果文件含完整环境：启动清崩溃残留（只清过期的，不碰别的实例在途的），退出清本进程在途的。"""
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    results = tmp_path / "data" / "probe-results"
    results.mkdir(parents=True)
    old, fresh = results / "activated-old.json", results / "activated-fresh.json"
    old.write_text('{"SECRET": "x"}', encoding="utf-8")
    fresh.write_text("{}", encoding="utf-8")
    ancient = time.time() - 3600
    os.utime(old, (ancient, ancient))
    monkeypatch.setattr(launcher, "_startup_done", False)
    monkeypatch.setattr(launcher.atexit, "register", lambda fn: None)  # 不真登记进测试进程
    launcher.startup()
    assert not old.exists() and fresh.exists()
    launcher._inflight.add(str(fresh))  # noqa: SLF001 — 模拟在途
    launcher._cleanup_inflight()  # noqa: SLF001 — atexit 处理函数
    assert not fresh.exists()


@posix_only
def test_capture_honours_the_callers_budget_and_leaves_no_process(tmp_path, monkeypatch):
    """P2-4：调用方的预算（体检 30s 等）覆盖到捕获本身；超时杀整棵树并转成稳定错误。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setenv("FAKE_CONDA_SLEEP", "30")
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    t0 = time.time()
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launcher.activate_env(py, {}, timeout=1.0)
    assert time.time() - t0 < 10 and "超时" in str(exc.value)
    assert not list((tmp_path / "data" / "probe-results").glob("*"))


@needs_worker
def test_the_capture_happens_outside_the_pool_lock(conda_world, monkeypatch):
    """P2-4：钉下的解释器不经体检直接起会话时，捕获也在池锁之外做完（锁内起会话只命中缓存）。"""
    proj, prefix, log, counter = conda_world
    py = str(prefix / "bin" / "python")
    locked_during_capture: list[bool] = []
    real = launcher._capture  # noqa: SLF001 — 真正起 conda 的那一步（命中缓存不算）

    def spy(*a, **k):
        locked_during_capture.append(_pool._lock.locked())  # noqa: SLF001
        return real(*a, **k)

    monkeypatch.setattr(launcher, "_capture", spy)
    launcher.reset_captures()
    pin = _pool.EnvironmentDecision(python=py, source=_pool.SOURCE_ENV)
    worker, _ = _pool.acquire("fig.py", str(proj), "__main__", pinned=pin)
    try:
        assert locked_during_capture and not any(locked_during_capture)
    finally:
        worker.force_kill()
        _pool.shutdown_all(wait=True)


@posix_only
def test_the_success_record_uses_the_session_start_environment(tmp_path, monkeypatch):
    """P2-7a：记录里的环境版本 / 代取起会话那一刻的值（回执的 `session_env`），不取收口时的。"""
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    prefix, _, _ = make_fake_conda(tmp_path, sys.executable)
    py = str(prefix / "bin" / "python")
    rcpt = _good_rcpt(proj, py)
    rcpt.session_env = {"generation": "gen-at-start", "env_state": "state-at-start"}
    rec = launchctx.build_record(proj, "fig.py", rcpt)
    assert (
        rec["env_state"] == "state-at-start" and rec["interpreter"]["generation"] == "gen-at-start"
    )
    assert (
        launchctx.verify(
            rec, project_root=proj, script="fig.py", python=py, python_source="env_override"
        )
        == launchctx.REASON_ENVIRONMENT
    )  # 与此刻的环境对不上 = 重新核验，而不是把收口时的状态当成“跑成功时的样子”

    # 真实 worker 把这两个值带进回执
    class W:
        python_generation = "g"
        env_state = "s"

    assert receipt._session_env(W()) == {"generation": "g", "env_state": "s"}  # noqa: SLF001


@needs_real_conda
def test_real_conda_env_config_vars_and_new_hooks_trigger_a_recapture(
    real_hooks, real_prefix, tmp_path, monkeypatch
):
    """P2-1（真 conda）：`conda env config vars set` 与新增 activate.d 脚本之后，缓存键换了，重新捕获到新值。"""
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(Path(real_prefix) / "bin" / "python")
    exe = _real_conda()
    lau = launcher.launcher_for_interpreter(py)
    assert lau is not None and lau.conda_exe == exe
    base = launcher.activate_env(py, {})
    assert "P09_CFG_VAR" not in base
    state0 = launcher.prefix_state(real_prefix)

    def conda(*args):
        return subprocess.run(
            [exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )

    try:
        assert (
            conda("env", "config", "vars", "set", "-p", real_prefix, "P09_CFG_VAR=one").returncode
            == 0
        )
        assert launcher.prefix_state(real_prefix) != state0
        assert launcher.activate_env(py, {})["P09_CFG_VAR"] == "one"
        state1 = launcher.prefix_state(real_prefix)
        real_hooks("activate.d", "newhook", "export P09_HOOK_VAR=hooked\n")
        assert launcher.prefix_state(real_prefix) != state1
        env = launcher.activate_env(py, {})
        assert env["P09_HOOK_VAR"] == "hooked" and env["P09_CFG_VAR"] == "one"
    finally:
        conda("env", "config", "vars", "unset", "-p", real_prefix, "P09_CFG_VAR")


# ---------------------------------------------------------------------------
# 第二轮独立审查：找不到 conda 不是“结论”；桌面 native 会话同一个启动决策；预算 / 透传
# ---------------------------------------------------------------------------
def _no_conda_anywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))


@needs_worker
def test_a_conda_env_without_a_findable_conda_is_not_a_verdict_on_the_interpreter(
    activation_only_env, tmp_path, monkeypatch
):
    """找不到 conda 的 Conda 环境，直接启动失败不是对环境的结论：记住的解释器不被作废、不静默换别的、稳定码给到用户。"""
    from tavotto.engine import projectenv

    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    _pool.reset_worker_python()
    projectenv.reset_cache()
    assert projectenv.remember(proj, py, automatic=True, trigger=projectenv.TRIGGER_FIRST_OPEN)
    (prefix.parent.parent / "bin" / "conda").unlink()  # conda 不见了
    _no_conda_anywhere(tmp_path, monkeypatch)
    assert launcher.launcher_for_interpreter(py) is None
    assert launcher.unlocated_conda_prefix(py) == str(prefix)
    with pytest.raises(_pool.WorkerError) as exc:
        _pool.resolve_worker_python(str(proj), script="fig.py")
    assert exc.value.code == "conda_launcher_unavailable"
    assert projectenv.remembered(proj) == py and _pool.invalidated_decision(proj) is None
    assert not _pool._project_python_ok
    health = projectenv.probe_environment(py, "vendor_sdk")
    assert health["ok"] is False and health["code"] == "conda_launcher_unavailable"
    assert not projectenv._system_probe_cache
    # 条件恢复（conda 回来）：同一个解释器照常可用
    (prefix.parent.parent / "bin" / "conda").write_text(
        FAKE_CONDA.format(python=sys.executable, license=LICENSE), encoding="utf-8"
    )
    (prefix.parent.parent / "bin" / "conda").chmod(0o755)
    monkeypatch.setenv("CONDA_EXE", str(prefix.parent.parent / "bin" / "conda"))
    assert _pool.resolve_worker_python(str(proj), script="fig.py")[0] == py


@needs_worker
def test_a_conda_env_that_works_bare_is_unaffected_when_conda_is_missing(tmp_path, monkeypatch):
    """对照（与 main 一致）：不需要激活的 Conda 环境，找不到 conda 也照常裸起、照常可用。"""
    from tavotto.engine import projectenv

    lone = tmp_path / "lone"
    (lone / "conda-meta").mkdir(parents=True)
    (lone / "bin").mkdir()
    py = lone / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{WORKER_PY}" "$@"\n', encoding="utf-8")
    py.chmod(0o755)
    _no_conda_anywhere(tmp_path, monkeypatch)
    assert launcher.unlocated_conda_prefix(py) == str(lone)
    assert _pool._has_matplotlib(str(py)) is True
    assert projectenv.probe_environment(str(py))["ok"] is True


@posix_only
def test_conda_is_found_from_the_users_install_root_and_never_from_a_project(tmp_path, monkeypatch):
    """Finder 启动（没有 CONDA_EXE / PATH）时靠常见安装位置里的用户 conda 找到它；项目里自带的 `conda-meta/` + `bin/conda`
    （不在用户 conda 安装里）既不被激活、它的 conda 也不被执行；`conda-meta/history` / environments.txt 都不是来源。"""
    root = tmp_path / "miniforge"
    (root / "conda-meta").mkdir(parents=True)
    (root / "bin").mkdir()
    conda = root / "bin" / "conda"
    conda.write_text("#!/bin/sh\n", encoding="utf-8")
    conda.chmod(0o755)
    (root / "envs" / "e1" / "conda-meta").mkdir(parents=True)
    _no_conda_anywhere(tmp_path, monkeypatch)
    _FAKE_ROOTS.append(os.path.realpath(root))
    assert launcher.find_conda_exe(str(root / "envs" / "e1")) == str(conda)  # 正对照
    assert launcher.find_conda_exe(str(root)) == str(conda)  # base 本身
    proj = tmp_path / "proj"
    evil = proj / "evil"
    (evil / "bin").mkdir(parents=True)
    (evil / "conda-meta").mkdir()
    (evil / "bin" / "conda").write_text("#!/bin/sh\n", encoding="utf-8")
    (evil / "bin" / "conda").chmod(0o755)
    (proj / "env" / "conda-meta").mkdir(parents=True)
    (proj / "env" / "conda-meta" / "history").write_text(
        f"# cmd: {evil / 'bin' / 'conda'} create\n", encoding="utf-8"
    )
    assert launcher.find_conda_exe(str(proj / "env")) is None
    assert launcher.find_conda_exe(str(evil)) is None  # 项目自带的“安装根”不是用户的
    assert not launcher._trusted_prefix(str(evil))  # noqa: SLF001
    assert not hasattr(launcher, "_history_conda") and not hasattr(launcher, "_registered_prefix")


@needs_worker
def test_the_probe_and_the_preflight_pass_the_callers_budget_to_the_capture(
    activation_only_env, monkeypatch
):
    """预算接到体检上：`probe_environment(timeout=…)` 的捕获不超过它，`_has_matplotlib` 的捕获不超过 30s。"""
    from tavotto.engine import projectenv

    py, prefix, log = activation_only_env
    seen: list[float] = []
    real = launcher._capture  # noqa: SLF001

    def spy(python, lau, timeout):
        seen.append(timeout)
        return real(python, lau, timeout)

    monkeypatch.setattr(launcher, "_capture", spy)
    launcher.reset_captures()
    projectenv.probe_environment(py, timeout=7.0)
    assert seen and seen[-1] <= 7.0
    launcher.reset_captures()
    _pool._has_matplotlib(py)  # noqa: SLF001
    assert seen[-1] <= 30.0


@needs_worker
def test_a_transient_activation_failure_is_not_reported_as_a_failed_self_test(
    activation_only_env, monkeypatch
):
    """自检遇到激活失败：透传稳定码，不报成“自检失败”。"""
    from tavotto.engine import deprepair

    py, prefix, log = activation_only_env
    _fail_capture(monkeypatch)
    res = deprepair.worker_self_test(py)
    assert res["ok"] is False and res["code"] == "conda_launcher_unavailable"
    assert deprepair._selftest_error(res).code == "conda_launcher_unavailable"  # noqa: SLF001
    assert deprepair._selftest_error({"ok": False, "detail": "x"}).code == (  # noqa: SLF001
        deprepair.ERROR_SELFTEST_FAILED
    )


@posix_only
@pytest.mark.skipif(
    __import__("support.nativekit", fromlist=["x"]).USER_PYTHON is None,
    reason="需要装有 Bridge 依赖的用户 Python",
)
def test_a_desktop_owned_native_session_uses_the_same_launch_decision(tmp_path, monkeypatch):
    """P06 桌面拥有的 native 会话没有用户 shell 替它激活：与 safe 档同一个决策。成功 = 脚本看得到激活变量；激活失败 = 零执行 + 稳定码。"""
    from support import desktopkit as dk
    from tavotto.engine import desktopproc, envlease, nativesession, runcodes

    py_real = dk.current_python()
    prefix, conda, log = make_fake_conda(tmp_path, py_real)
    py = str(prefix / "bin" / "python")
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    script = (
        "import os\nopen('seen.txt', 'w').write(os.environ.get('FAKE_SDK_LICENSE', 'MISSING'))\n"
    )
    session = desktopproc.start(dk.launch_for(tmp_path, script, interpreter=py))
    try:
        assert session.owned.wait(60)
    finally:
        dk.finish(session)
    before = list(nativesession.REGISTRY.list())
    assert (tmp_path / "work" / "seen.txt").read_text(encoding="utf-8") == LICENSE
    # 激活失败：什么都没跑、没有租约、没有会话
    (tmp_path / "work" / "seen.txt").unlink()
    launcher.reset_captures()
    _fail_capture(monkeypatch)
    with pytest.raises(runcodes.RunError) as err:
        desktopproc.start(dk.launch_for(tmp_path, script, interpreter=py))
    assert err.value.code == "conda_launcher_unavailable"
    assert not (tmp_path / "work" / "seen.txt").exists()
    assert nativesession.REGISTRY.list() == before and envlease.native_sessions_on(py) == []
    # 环境正在装包：租约先于激活——拿不到租约就一行环境代码都不跑（conda / activate.d 都不调）
    launcher.reset_captures()
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")  # 捕获本身恢复正常：下面拦住它的只能是租约
    calls_before = len(conda_calls(log))
    with envlease.mutating(envlease.env_key_of(py), py):
        with pytest.raises(envlease.EnvironmentBusy):
            desktopproc.start(dk.launch_for(tmp_path, script, interpreter=py))
    assert len(conda_calls(log)) == calls_before
    assert not (tmp_path / "work" / "seen.txt").exists()
    assert nativesession.REGISTRY.list() == before and envlease.native_sessions_on(py) == []


@posix_only
def test_a_capture_that_raced_an_environment_change_is_neither_used_nor_cached(
    tmp_path, monkeypatch
):
    """捕获期间环境被改了（装 / 卸包追加 history）：这份结果可能取自改到一半的 prefix，fail-closed、不缓存；下一次按新版本重新捕获。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    real = launcher._capture

    def racing(*a, **k):
        out = real(*a, **k)
        with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
            fh.write("# cmd: conda install something\n")
        return out

    monkeypatch.setattr(launcher, "_capture", racing)
    with pytest.raises(launcher.LauncherUnavailable) as exc:
        launcher.activated_delta(py, lau)
    assert exc.value.code == "conda_launcher_unavailable"
    assert launcher._captured == {}
    monkeypatch.setattr(launcher, "_capture", real)
    assert launcher.activated_delta(py, lau)["FAKE_SDK_LICENSE"] == LICENSE


# ---------------------------------------------------------------------------
# Codex #975 第三轮：激活去掉的变量启动时也要去掉；超时收尾叶子先死
# ---------------------------------------------------------------------------
@needs_worker
def test_a_variable_the_activation_removes_is_removed_at_every_launch_site(
    activation_only_env, tmp_path, monkeypatch
):
    """激活 `unset` 掉的变量（如 PYTHONPATH / 许可变量）：捕获进增量（None = 删），每个启动点的子进程都看不到它。"""
    from tavotto.engine import depplan, deprepair, projectenv

    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import os, matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "open('unset-seen.txt', 'w').write(repr(os.environ.get('P09_UNSET_ME')))\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("P09_UNSET_ME", "leaks-if-not-removed")
    monkeypatch.setenv("FAKE_CONDA_UNSET", "P09_UNSET_ME,SHLVL")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    lau = launcher.launcher_for_interpreter(py)
    delta = launcher.activated_delta(py, lau)
    assert delta["P09_UNSET_ME"] is None  # 去掉的记成 None
    assert "SHLVL" not in delta  # 每个进程自己会变的永不删
    seen = _spy_popen(monkeypatch)
    worker = None
    try:
        assert launcher.activate_env(py, {"KEEP": "1"}) == {
            **{k: v for k, v in launcher.merge_env(lau, py, {"KEEP": "1"}).items()},
        }
        assert "P09_UNSET_ME" not in launcher.activate_env(py, None)
        assert _pool._has_matplotlib(py) is True  # noqa: SLF001
        assert projectenv.probe_environment(py)["ok"] is True
        assert depplan.target_facts(py) is not None
        assert deprepair.probe_imports(py, ("vendor_sdk",)) == {"vendor_sdk": ""}
        assert deprepair.worker_self_test(py)["ok"] is True
        assert deprepair._run([py, "-c", "pass"], 60)[0] == 0  # noqa: SLF001
        assert deprepair._run_pip([py, "-c", "pass"], threading.Event(), None)[0] == ""  # noqa: SLF001
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
        _pool.reset_worker_python()
        worker, _ = _pool.build("fig.py", str(proj), "__main__")
    finally:
        if worker is not None:
            worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()
    # `bootstrap._probe`（后台问系统 Python 能不能 `import venv`）问的不是 worker 的解释器环境，PR 表里列为“不改”
    ours = [
        (a, e)
        for a, e in seen
        if a and str(a[0]) == py and not any("import venv" in str(x) for x in a)
    ]
    assert len(ours) >= 7
    for argv, env in ours:
        assert env is not None and "P09_UNSET_ME" not in env, [str(a)[:50] for a in argv[:4]]
        assert env.get("FAKE_SDK_LICENSE") == LICENSE  # 对照：增量里“加的”照常生效


@posix_only
@pytest.mark.skipif(
    __import__("support.nativekit", fromlist=["x"]).USER_PYTHON is None,
    reason="需要装有 Bridge 依赖的用户 Python",
)
def test_the_desktop_owned_session_also_drops_what_the_activation_removed(tmp_path, monkeypatch):
    from support import desktopkit as dk
    from tavotto.engine import desktopproc

    prefix, conda, log = make_fake_conda(tmp_path, dk.current_python())
    py = str(prefix / "bin" / "python")
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setenv("P09_UNSET_ME", "leaks-if-not-removed")
    monkeypatch.setenv("FAKE_CONDA_UNSET", "P09_UNSET_ME")
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    script = "import os\nopen('seen.txt', 'w').write(repr(os.environ.get('P09_UNSET_ME')))\n"
    session = desktopproc.start(dk.launch_for(tmp_path, script, interpreter=py))
    try:
        assert session.owned.wait(60)
    finally:
        dk.finish(session)
    assert (tmp_path / "work" / "seen.txt").read_text(encoding="utf-8") == "None"


@posix_only
def test_capture_timeout_kills_the_descendants_before_conda_itself(tmp_path, monkeypatch):
    """超时收尾叶子先死：conda 已经起了子进程再超时，子进程先于 conda 被杀（conda 还活着时收尸），最后没有残留。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    pidfile = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setenv("FAKE_CONDA_HANG_CHILD", str(pidfile))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    kills: list[int] = []
    real_kill = launcher.os.kill

    def spy_kill(pid, sig):
        kills.append(pid)
        return real_kill(pid, sig)

    monkeypatch.setattr(launcher.os, "kill", spy_kill)
    conda_pids: list[int] = []
    real_popen = subprocess.Popen

    class Spy(real_popen):
        def __init__(self, args, *a, **k):
            super().__init__(args, *a, **k)
            if args and str(args[0]) == str(conda):
                conda_pids.append(self.pid)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    with pytest.raises(launcher.LauncherUnavailable):
        launcher.activate_env(py, {}, timeout=3.0)
    child = int(pidfile.read_text())  # 超时之前 conda 已经起了它
    (conda_pid,) = conda_pids
    assert child in kills, kills
    # 叶子先死：对 conda 本身的 os.kill（Popen.kill 走 os.kill）排在子进程之后（或根本只靠 wait 收尾）
    assert conda_pid not in kills or kills.index(child) < kills.index(conda_pid)
    assert wait_gone(child) and wait_gone(conda_pid)


# ---------------------------------------------------------------------------
# Codex #975 第六轮：热会话随 Conda 环境版本重建；体检与捕获共用预算；激活钩子算执行过环境代码
# ---------------------------------------------------------------------------
@needs_worker
def test_a_hot_worker_is_rebuilt_when_the_conda_environment_changes_in_place(
    activation_only_env, tmp_path, monkeypatch
):
    """外部 `conda install` / 改 activate.d 原地改了 prefix：热会话的 import 与激活变量都是旧的，不能复用，要重建。"""
    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    try:
        w1, _ = _pool.build("fig.py", str(proj), "__main__")
        w2, _ = _pool.build("fig.py", str(proj), "__main__")
        assert w2 is w1  # 环境没变：热会话复用
        with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
            fh.write("# cmd: conda install vendor-sdk\n")
        assert _pool.generation_stale(w1)
        w3, _ = _pool.build("fig.py", str(proj), "__main__")
        assert w3 is not w1 and w3.env_state == launcher.env_state_of(py)
    finally:
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


@needs_worker
def test_the_probe_shares_one_deadline_with_the_activation_capture(
    activation_only_env, monkeypatch
):
    """捕获用掉的时间从体检子进程的预算里扣；捕获用完了预算 = 瞬时的 conda_launcher_unavailable，不是对环境的结论。"""
    from tavotto.engine import projectenv

    py, prefix, log = activation_only_env
    real_capture = launcher._capture  # noqa: SLF001
    real_run = projectenv.subprocess.run
    run_timeouts: list[float] = []

    def slow_capture(python, lau, timeout):
        time.sleep(1.5)
        return real_capture(python, lau, timeout)

    def spy_run(*a, **k):
        run_timeouts.append(k.get("timeout"))
        return real_run(*a, **k)

    monkeypatch.setattr(launcher, "_capture", slow_capture)
    monkeypatch.setattr(projectenv.subprocess, "run", spy_run)
    projectenv.reset_cache()
    launcher.reset_captures()
    projectenv.probe_environment(py, timeout=20.0)
    assert run_timeouts and run_timeouts[-1] <= 20.0 - 1.5
    # 捕获把预算用光：瞬时码、不缓存，激活钩子算执行过环境代码
    run_timeouts.clear()
    launcher.reset_captures()
    projectenv.reset_cache()

    def eats_the_budget(python, lau, timeout):
        out = real_capture(python, lau, timeout)
        time.sleep(1.2)
        return out

    monkeypatch.setattr(launcher, "_capture", eats_the_budget)
    res = projectenv.probe_environment(py, timeout=1.0)
    assert res["ok"] is False and res["code"] == "conda_launcher_unavailable"
    assert res["execution"]["ran_environment_code"] is True
    assert run_timeouts == []  # 体检子进程没起


@needs_worker
def test_a_failed_activation_is_reported_as_environment_code_that_may_have_run(
    activation_only_env, monkeypatch
):
    """`conda run` 跑 activate.d 钩子之后才失败 / 超时：体检的执行声明不许说「什么都没跑」。"""
    from tavotto.engine import projectenv

    py, prefix, log = activation_only_env
    _fail_capture(monkeypatch)
    res = projectenv.probe_environment(py, "vendor_sdk", import_mode="spec")
    assert res["code"] == "conda_launcher_unavailable"
    assert (
        res["execution"]["ran_environment_code"] is True and res["execution"]["incomplete"] is True
    )


# ---------------------------------------------------------------------------
# Codex #975 第七轮：依赖操作透传激活失败码；部分捕获不记成功；超时收尾一个总期限
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("failed", [True, False])
def test_a_partial_capture_never_records_a_successful_launch(tmp_path, monkeypatch, failed):
    """有图失败（partial）不是完整成功：启动上下文记录按依据不全处理（rcpt=None → 清掉旧记录、不写新的），默认配置照常。"""
    import types

    from tavotto import app as m
    from tavotto.engine import discover

    figs = tmp_path / "figs"
    figs.mkdir()
    (figs / "s.py").write_text("print()\n", encoding="utf-8")
    discover.register(figs, "s.py", ["good", "bad"], entry="main")
    captured = {"stems": ["good"], "descriptors": [], "remap_generation": None}
    if failed:
        captured.update(
            partial=True,
            failed_figures=[{"stem": "bad", "code": "preview_capture_failed", "message": "m"}],
        )
    receipt_obj = object()
    seen = []
    monkeypatch.setattr(m, "_materialize_runtime", lambda *a, **k: None)
    monkeypatch.setattr(m, "refresh_project", lambda *a, **k: None)
    monkeypatch.setattr(m, "_plan_pin", lambda plan: None)
    monkeypatch.setattr(
        m.engine_launchctx, "record_success", lambda root, script, rcpt, **k: seen.append((rcpt, k))
    )
    ctx = types.SimpleNamespace(path=str(figs))
    plan = types.SimpleNamespace(script="s.py", entry="main", run=None)
    m._finalize_script_attempt(
        ctx, plan, types.SimpleNamespace(captured=captured, receipt=receipt_obj)
    )
    ((rcpt, kw),) = seen
    assert rcpt is (None if failed else receipt_obj)
    assert kw["update_default"] is True


def test_a_failed_activation_before_a_dependency_operation_keeps_its_stable_code(monkeypatch):
    """装 / 卸之前的 `pip --version`：激活取环境失败报 conda_launcher_unavailable，不报成“这个环境没有 pip”。"""
    from tavotto.engine import deprepair

    def boom(python, base=None, **k):
        raise launcher.LauncherUnavailable("x")

    with monkeypatch.context() as m:
        m.setattr(deprepair.launcher, "activate_env", boom)
        rc, out = deprepair._run([sys.executable, "-m", "pip", "--version"], 30)
        assert rc == deprepair.RC_ACTIVATION_UNAVAILABLE
        with pytest.raises(deprepair.RepairError) as err:
            deprepair._require_pip(sys.executable, deprepair.ERROR_PIP_UNAVAILABLE)
        assert err.value.code == "conda_launcher_unavailable"
    deprepair._require_pip(sys.executable, deprepair.ERROR_PIP_UNAVAILABLE)  # 正常环境：不抛


@posix_only
def test_timeout_cleanup_has_one_overall_deadline_however_deep_the_tree(monkeypatch):
    """再深的进程链，叶子先死的收尾也只在一个总期限内（不是每层 0.1s 累加）；余下的由整组兜底杀掉。"""
    deep = [[100000 + i] for i in range(200)]  # 200 层
    monkeypatch.setattr(launcher, "_descendant_levels", lambda pid, **k: deep)
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: None)
    killed_group = []
    monkeypatch.setattr(launcher.os, "killpg", lambda pid, sig: killed_group.append(pid))

    class P:
        pid = 4242

        def kill(self):
            pass

        def wait(self, timeout=None):
            return -9

    t0 = time.monotonic()
    launcher._kill_tree(P())
    assert time.monotonic() - t0 < launcher._KILL_TREE_BUDGET_S + 1.0
    assert killed_group == [4242]


@needs_worker
def test_the_interpreter_preflight_shares_its_budget_with_the_activation(
    activation_only_env, monkeypatch
):
    """选解释器预检：激活捕获用掉的时间从 import 探针的 30s 里扣；用完了 = 瞬时不可用，不是“没有 matplotlib”。"""
    py, prefix, log = activation_only_env
    real_capture = launcher._capture  # noqa: SLF001
    real_run = _pool.subprocess.run
    run_timeouts: list[float] = []

    def slow_capture(python, lau, timeout):
        time.sleep(1.5)
        return real_capture(python, lau, timeout)

    def spy_run(*a, **k):
        run_timeouts.append(k.get("timeout"))
        return real_run(*a, **k)

    monkeypatch.setattr(launcher, "_capture", slow_capture)
    monkeypatch.setattr(_pool.subprocess, "run", spy_run)
    launcher.reset_captures()
    assert _pool._has_matplotlib(py) is True  # noqa: SLF001
    assert run_timeouts and run_timeouts[-1] <= _pool._HAS_MPL_BUDGET_S - 1.5  # noqa: SLF001
    run_timeouts.clear()
    launcher.reset_captures()
    monkeypatch.setattr(_pool, "_HAS_MPL_BUDGET_S", 1.0)
    with pytest.raises(launcher.LauncherUnavailable):
        _pool._has_matplotlib(py)  # noqa: SLF001
    assert run_timeouts == []


@needs_worker
def test_target_facts_and_import_verification_keep_the_activation_code(
    activation_only_env, monkeypatch
):
    """依赖计划的目标事实、装后的 import 核验：激活取环境失败报 conda_launcher_unavailable，
    不报成“目标不可用”或“装了还 import 不了”（后者会把这一代标成不完整、叫用户去查包）。"""
    from tavotto.engine import depplan, deprepair

    py, prefix, log = activation_only_env
    depplan.reset_cache()
    _fail_capture(monkeypatch)
    with pytest.raises(launcher.LauncherUnavailable):
        depplan.target_facts(py, use_cache=False)
    with pytest.raises(deprepair.RepairError) as err:
        deprepair._facts_for(deprepair.TARGET_PROJECT_VENV, py, str(prefix), use_cache=False)
    assert err.value.code == "conda_launcher_unavailable"
    with pytest.raises(deprepair.RepairError) as err:
        deprepair._verify_imports(py, ("vendor_sdk",))
    assert err.value.code == "conda_launcher_unavailable"
    assert not depplan._facts_cache  # noqa: SLF001
    monkeypatch.delenv("FAKE_CONDA_SKIP_CMD")
    deprepair._verify_imports(py, ("vendor_sdk",))  # 条件恢复：同一入口立刻正常


def test_the_environment_package_probe_reports_the_activation_code(monkeypatch):
    """`/api/engine/environment?probe=` 遇到激活取环境失败：报 conda_launcher_unavailable，不把每个包报成 import 不到。"""
    from tavotto import app as m
    from tavotto.engine import runtime

    def boom(python, base=None, **k):
        raise launcher.LauncherUnavailable("x")

    with pytest.raises(launcher.LauncherUnavailable):
        runtime.probe_packages(sys.executable, ["json"], env_for=boom)
    assert runtime.probe_packages(sys.executable, ["json"])["json"]  # 不注入：照常问到版本
    monkeypatch.setattr(
        m.engine_bootstrap, "status", lambda: {"python": sys.executable, "bundled": False}
    )
    monkeypatch.setattr(m.engine_launcher, "activate_env", boom)
    monkeypatch.setattr(m, "_project_environment_state", lambda: {"open": False})
    body = m.app.test_client().get("/api/engine/environment?probe=json").get_json()
    assert "imports" not in body
    assert body["imports_error"]["code"] == "conda_launcher_unavailable"


@posix_only
def test_environment_advice_verdicts_are_partitioned_by_launch_context(tmp_path, monkeypatch):
    """环境建议的检查结论：conda install / 改 activate.d 不改环境代，但激活后看到的东西变了——旧结论不能复用；
    直接启动与 Conda 激活两种上下文的结论互不冒充。"""
    from tavotto.engine import envadvice, projectenv

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("CONDA_EXE", str(conda))
    py = str(prefix / "bin" / "python")
    envadvice.reset_cache()
    gen = projectenv.environment_generation(py)
    envadvice._store_verdict(py, gen, {"ok": True})  # noqa: SLF001
    assert envadvice._verdict(py, gen)["ok"] is True  # noqa: SLF001
    with monkeypatch.context() as m:
        m.setenv(launcher.ENV_DISABLE, "0")  # 直接启动的上下文：没验过
        assert envadvice._verdict(py, gen) is None  # noqa: SLF001
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")
    assert projectenv.environment_generation(py) == gen  # 环境代没变……
    assert envadvice._verdict(py, gen) is None  # noqa: SLF001 — ……结论照样不复用


@posix_only
def test_the_first_open_outcome_is_invalidated_when_the_conda_environment_changes(
    tmp_path, monkeypatch
):
    """首次打开的环境结论：conda install / 改 activate.d 不动解释器文件，结论的指纹照样要变（带启动上下文）。"""
    from tavotto.engine import projectenv

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("CONDA_EXE", str(conda))
    py = str(prefix / "bin" / "python")
    outcome = {"ok": True, "python": py, "rejected": []}
    before = projectenv._outcome_fingerprint(outcome)  # noqa: SLF001
    assert projectenv._outcome_fingerprint(outcome) == before  # noqa: SLF001
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")
    assert projectenv._outcome_fingerprint(outcome) != before  # noqa: SLF001


@posix_only
def test_a_cloned_project_cannot_plant_its_own_conda_next_to_an_unregistered_prefix(
    tmp_path, monkeypatch
):
    """克隆来的项目自带 `.venv/conda-meta/` + 可执行的 `.venv/bin/conda`：这个 prefix 没在用户的 environments.txt 里登记，
    旁边的 conda 一律不信（否则“打开项目做体检”就执行了项目里的文件）；登记过的 prefix 照常找得到。"""
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    proj = tmp_path / "cloned"
    venv = proj / ".venv"
    (venv / "conda-meta").mkdir(parents=True)
    (venv / "bin").mkdir()
    planted = venv / "bin" / "conda"
    marker = tmp_path / "planted-ran"
    planted.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
    os.chmod(planted, 0o755)
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    assert launcher.find_conda_exe(str(venv)) is None
    assert launcher.launcher_for_interpreter(str(venv / "bin" / "python")) is None
    assert not marker.exists()


@posix_only
def test_a_coverage_result_measured_before_an_in_place_conda_change_is_not_stored_under_the_new_context(
    tmp_path, monkeypatch
):
    """明确的环境检查与 conda install / 改 activate.d 重叠：探测前取的启动上下文对不上写入时的，覆盖度结论丢掉（下次重量）。"""
    from tavotto.engine import userenvs

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    py = str(prefix / "bin" / "python")
    userenvs.reset_cache()
    before = launcher.context_key(py)
    assert before.startswith("conda|")
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")
    userenvs.remember_probe(py, ("vendor_sdk",), {"ok": True, "python": py}, context=before)
    assert userenvs._probe_cache == {}  # noqa: SLF001
    userenvs.remember_probe(
        py, ("vendor_sdk",), {"ok": True, "python": py}, context=launcher.context_key(py)
    )
    assert len(userenvs._probe_cache) == 1  # noqa: SLF001


@needs_worker
def test_an_environment_change_after_prewarm_never_runs_the_capture_under_the_pool_lock(
    activation_only_env, tmp_path, monkeypatch
):
    """预热之后、进池锁之前 Conda 环境被改：锁内缓存没命中——出锁重新预热再起会话，捕获从不在池锁里跑。"""
    py, prefix, log = activation_only_env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(); ax.plot([1, 2]); fig.savefig('r.pdf')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", py)
    _pool.reset_worker_python()
    real_prewarm = launcher.prewarm
    real_capture = launcher._capture  # noqa: SLF001
    prewarms: list[int] = []
    under_lock: list[bool] = []

    def racing_prewarm(python, **k):
        real_prewarm(python, **k)
        prewarms.append(1)
        if len(prewarms) == 1:  # 第一次预热之后、进锁之前：外部 conda install
            with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
                fh.write("# cmd: conda install vendor-sdk\n")

    def spy_capture(*a, **k):
        under_lock.append(_pool._lock.locked())  # noqa: SLF001
        return real_capture(*a, **k)

    # 只测“锁内缓存没命中不在锁里捕获”：不让检测模式钉下决定（钉下之后环境再变是另一条路——`environment_changed`，
    # 由 test_a_pinned_decision_and_a_plan_go_stale_when_the_conda_environment_changes_in_place 管）
    monkeypatch.setattr(_pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(launcher, "prewarm", racing_prewarm)
    monkeypatch.setattr(launcher, "_capture", spy_capture)
    launcher.reset_captures()
    try:
        w, _ = _pool.build("fig.py", str(proj), "__main__")
        assert w.alive()
        assert len(prewarms) >= 2
        assert under_lock and not any(under_lock)
    finally:
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()


@posix_only
def test_the_final_reap_in_timeout_cleanup_is_bounded(monkeypatch):
    """卡在不可中断 I/O 的进程 SIGKILL 后也不可收：收尾的最后一次 wait 有期限，不无限挡住调用方。"""
    monkeypatch.setattr(launcher, "_descendant_levels", lambda pid, **k: [])
    monkeypatch.setattr(launcher.os, "killpg", lambda pid, sig: None)
    waits: list = []

    class Stuck:
        pid = 4242

        def kill(self):
            pass

        def wait(self, timeout=None):
            waits.append(timeout)
            if timeout is None:
                time.sleep(30)  # 模拟永远收不掉
            raise subprocess.TimeoutExpired("conda", timeout)

    t0 = time.monotonic()
    launcher._kill_tree(Stuck())  # noqa: SLF001
    assert time.monotonic() - t0 < 5
    assert waits and all(w is not None for w in waits)


@posix_only
def test_a_result_file_that_cannot_be_deleted_stays_registered_for_exit_cleanup(
    tmp_path, monkeypatch
):
    """捕获成功后结果文件删不掉（瞬时文件系统错误）：不能被遗忘——留在在途登记里，退出清理再删；下一次捕获前也会扫过期的。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    real_unlink = os.unlink
    failed: list[str] = []

    def flaky_unlink(path, *a, **k):
        if str(path).endswith(".json") and "probe-results" in str(path) and not failed:
            failed.append(str(path))
            raise PermissionError("busy")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(launcher.os, "unlink", flaky_unlink)
    assert launcher.activated_delta(py, lau)["FAKE_SDK_LICENSE"] == LICENSE
    (left,) = failed
    assert os.path.exists(left) and left in launcher._inflight  # noqa: SLF001
    launcher._cleanup_inflight()  # noqa: SLF001
    assert not os.path.exists(left)


def test_a_short_probe_shares_its_budget_with_the_activation(monkeypatch):
    """`pip --version` 之类的短探测：激活捕获用掉的时间从命令自己的超时里扣；用完了 = 瞬时的激活不可用。"""
    from tavotto.engine import deprepair

    seen: dict = {}
    real_run = deprepair.subprocess.run

    def slow_activate(python, base=None, *, timeout=None):
        seen["activate_timeout"] = timeout
        time.sleep(0.6)
        return base

    def spy_run(*a, **k):
        seen["run_timeout"] = k.get("timeout")
        return real_run(*a, **k)

    monkeypatch.setattr(deprepair.launcher, "activate_env", slow_activate)
    monkeypatch.setattr(deprepair.subprocess, "run", spy_run)
    rc, _ = deprepair._run([sys.executable, "-c", "pass"], 5)  # noqa: SLF001
    assert rc == 0 and seen["activate_timeout"] == 5 and seen["run_timeout"] <= 5 - 0.6
    seen.clear()
    rc, _ = deprepair._run([sys.executable, "-c", "pass"], 0.5)  # noqa: SLF001
    assert rc == deprepair.RC_ACTIVATION_UNAVAILABLE and "run_timeout" not in seen


def test_a_cancel_during_a_slow_activation_never_starts_pip(monkeypatch):
    """装包前取激活环境（可能几十秒）期间用户点了取消：激活回来之后再查一次，pip 一次都不起。"""
    from tavotto.engine import deprepair

    cancel = threading.Event()
    started: list = []

    def slow_activate(python, base=None, *, timeout=None):
        cancel.set()  # 激活期间用户取消
        return base

    monkeypatch.setattr(deprepair.launcher, "activate_env", slow_activate)
    monkeypatch.setattr(deprepair.subprocess, "Popen", lambda *a, **k: started.append(a))
    code, _ = deprepair._run_pip([sys.executable, "-m", "pip", "--version"], cancel, None)  # noqa: SLF001
    assert code == deprepair.ERROR_CANCELLED and started == []
    # 激活用完了安装期限：同样不起 pip
    cancel.clear()
    monkeypatch.setattr(
        deprepair.launcher, "activate_env", lambda python, base=None, *, timeout=None: base
    )
    code, _ = deprepair._run_pip(  # noqa: SLF001
        [sys.executable, "-m", "pip", "--version"], cancel, None, deadline=time.time() - 1
    )
    assert code == deprepair.ERROR_TIMEOUT and started == []


@posix_only
def test_a_cache_miss_while_holding_the_pool_lock_is_deferred_not_captured(tmp_path, monkeypatch):
    """`no_capture` 里（持着池锁起会话）激活缓存没命中：抛 `CaptureDeferred` 交回调用方，一次捕获都不跑。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    monkeypatch.setattr(launcher.config, "data_dir", lambda: tmp_path / "data")
    py = str(prefix / "bin" / "python")
    lau = launcher.CondaLauncher(conda_exe=str(conda), prefix=str(prefix))
    with launcher.no_capture():
        with pytest.raises(launcher.CaptureDeferred):
            launcher.activated_delta(py, lau)
    assert conda_calls(log) == []
    launcher.activated_delta(py, lau)  # 出了 no_capture：照常捕获
    with launcher.no_capture():
        assert launcher.activated_delta(py, lau)["FAKE_SDK_LICENSE"] == LICENSE  # 命中缓存：照常


def test_the_package_inventory_shares_its_budget_with_the_activation(monkeypatch):
    """`/api/engine/environment?probe=` 的版本盘点：激活用掉的时间从探测子进程的预算里扣；用完了 = 瞬时激活不可用。"""
    from tavotto.engine import runtime

    seen: dict = {}
    real_run = runtime.subprocess.run

    def slow_env(python, base=None, *, timeout=None):
        seen["activate_timeout"] = timeout
        time.sleep(0.6)
        return base

    def spy_run(*a, **k):
        seen["run_timeout"] = k.get("timeout")
        return real_run(*a, **k)

    monkeypatch.setattr(runtime.subprocess, "run", spy_run)
    assert runtime.probe_packages(sys.executable, ["json"], env_for=slow_env)["json"]
    assert seen["run_timeout"] <= runtime.PROBE_TIMEOUT_S - 0.6
    seen.clear()
    monkeypatch.setattr(runtime, "PROBE_TIMEOUT_S", 0.5)
    with pytest.raises(runtime.ActivationUnavailable) as exc:
        runtime.probe_packages(sys.executable, ["json"], env_for=slow_env)
    assert exc.value.code == "conda_launcher_unavailable" and "run_timeout" not in seen
    assert issubclass(launcher.LauncherUnavailable, runtime.ActivationUnavailable)


@posix_only
def test_a_pinned_decision_and_a_plan_go_stale_when_the_conda_environment_changes_in_place(
    tmp_path, monkeypatch
):
    """计划 / 钉下的决定之后 conda install / 改 activate.d：环境代不变，但激活后的环境变了——决定对不上、计划判过期，
    不许在计划没验证过的环境里跑。"""
    import types

    from tavotto.engine import preparation, projectenv

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    monkeypatch.setenv("CONDA_EXE", str(conda))
    py = str(prefix / "bin" / "python")
    decision = _pool.EnvironmentDecision(
        python=py,
        source="system",
        generation=projectenv.environment_generation(py),
        launch_context=launcher.context_key(py),
    )
    assert decision.launch_context.startswith("conda|") and decision.matches(py)
    worker = types.SimpleNamespace(
        python=py, python_generation=decision.generation, launch_context=decision.launch_context
    )
    assert decision.matches_worker(worker)
    plan = types.SimpleNamespace(
        project_root=str(tmp_path),
        grant=None,
        input_remap_generation=None,
        interpreter=py,
        script=None,
        script_revision=None,
        binding=None,
        environment={"generation": decision.generation},
        environment_state=decision.launch_context,
    )
    monkeypatch.setattr(preparation.workdir, "grant_for", lambda root: None)
    monkeypatch.setattr(
        preparation.pool, "resolve_worker_python", lambda root, script=None: (py, "system")
    )
    assert preparation.PreparationService._stale_reason(plan) is None  # 环境没变：计划有效
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")
    assert projectenv.environment_generation(py) == decision.generation  # 代没变……
    assert preparation.PreparationService._stale_reason(plan)[0] == preparation.STALE_ENVIRONMENT
    assert not decision.matches(py)
    assert _pool.pin_of_worker(worker).launch_context == worker.launch_context


def test_activate_within_shares_one_budget(monkeypatch):
    """有总预算的探测：激活用掉的时间从子进程的预算里扣；用完了 = 瞬时的激活不可用。"""
    monkeypatch.setattr(
        launcher, "activate_env", lambda python, base=None, *, timeout=None: time.sleep(0.4) or base
    )
    env, remaining = launcher.activate_within(sys.executable, {"A": "1"}, 5.0)
    assert env == {"A": "1"} and remaining <= 5.0 - 0.4
    with pytest.raises(launcher.LauncherUnavailable):
        launcher.activate_within(sys.executable, {}, 0.2)


@posix_only
def test_a_plan_made_while_conda_was_missing_goes_stale_once_conda_is_back(tmp_path, monkeypatch):
    """计划时找不到 conda（直接启动），确认运行前 conda 回来了（会改成激活启动）：环境代与环境版本都没变，启动上下文变了——
    计划过期、钉下的决定对不上，不在计划没验证过的上下文里跑。"""
    import types

    from tavotto.engine import preparation, projectenv

    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    py = str(prefix / "bin" / "python")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    _FAKE_ROOTS.clear()  # 计划那一刻：Finder 启动，哪儿都找不到用户的 conda
    assert launcher.context_key(py).startswith("direct")
    decision = _pool.EnvironmentDecision(
        python=py,
        source="system",
        generation=projectenv.environment_generation(py),
        launch_context=launcher.context_key(py),
    )
    plan = types.SimpleNamespace(
        project_root=str(tmp_path),
        grant=None,
        input_remap_generation=None,
        interpreter=py,
        script=None,
        script_revision=None,
        binding=None,
        environment={"generation": decision.generation},
        environment_state=decision.launch_context,
    )
    monkeypatch.setattr(preparation.workdir, "grant_for", lambda root: None)
    monkeypatch.setattr(
        preparation.pool, "resolve_worker_python", lambda root, script=None: (py, "system")
    )
    assert preparation.PreparationService._stale_reason(plan) is None and decision.matches(py)
    monkeypatch.setenv("CONDA_EXE", str(conda))  # conda 回来了
    assert launcher.context_key(py).startswith("conda|")
    assert preparation.PreparationService._stale_reason(plan)[0] == preparation.STALE_ENVIRONMENT
    assert not decision.matches(py)
    worker = types.SimpleNamespace(
        python=py, python_generation=decision.generation, launch_context=decision.launch_context
    )
    assert _pool.generation_stale(worker)  # 热会话同样判陈旧、重建


@posix_only
def test_an_unclaimed_project_prefix_is_never_activated_even_with_the_users_own_conda(
    tmp_path, monkeypatch
):
    """克隆来的项目自带 `.venv/conda-meta/` + `etc/conda/activate.d` 钩子，主机上又有用户自己的 conda（CONDA_EXE / PATH）：
    激活会 source 这个钩子——没被用户的 conda 认领过（没登记、不是它的 base / envs）的 prefix 一律不激活，钩子一次都不跑。"""
    users_prefix, users_conda, log = make_fake_conda(tmp_path / "home", sys.executable)
    monkeypatch.setenv("CONDA_EXE", str(users_conda))
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    proj = tmp_path / "cloned"
    venv = proj / ".venv"
    (venv / "conda-meta").mkdir(parents=True)
    (venv / "bin").mkdir()
    hook_dir = venv / "etc" / "conda" / "activate.d"
    hook_dir.mkdir(parents=True)
    ran = tmp_path / "hook-ran"
    (hook_dir / "evil.sh").write_text(f"touch {ran}\n", encoding="utf-8")
    py = venv / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    os.chmod(py, 0o755)
    assert launcher.conda_prefix_of(str(py)) == str(venv)
    assert launcher.launcher_for_interpreter(str(py)) is None  # 直接启动（与 P09 之前一致）
    assert launcher.context_key(str(py)).startswith("direct")
    # 只报“这是个不会被激活的 Conda 形状的目录”（直接启动失败不当结论），什么都不执行
    assert launcher.unlocated_conda_prefix(str(py)) == str(venv)
    launcher.activate_env(str(py), {})
    assert conda_calls(log) == [] and not ran.exists()
    # 用户自己的 conda 建的（登记过）/ 它的 envs/<名字>：照常激活
    assert launcher.launcher_for_interpreter(str(users_prefix / "bin" / "python")) is not None


@posix_only
def test_a_prefix_outside_the_users_conda_install_is_never_activated(tmp_path, monkeypatch):
    """不在用户 conda 安装里（安装根本身 / 它的 envs/<名字>）的 prefix——过期的 environments.txt 条目上克隆的项目、
    没有任何项目标记的普通作图目录、`conda create -p` 到任意目录——一律直接启动，钩子一次都不跑。"""
    users_prefix, users_conda, log = make_fake_conda(tmp_path / "home", sys.executable)
    monkeypatch.setenv("CONDA_EXE", str(users_conda))
    monkeypatch.setenv("FAKE_CONDA_LOG", str(log))
    plain = tmp_path / "figs" / "env"  # 没有 .git / pyproject 的普通目录
    (plain / "conda-meta").mkdir(parents=True)
    (plain / "bin").mkdir()
    (plain / "etc" / "conda" / "activate.d").mkdir(parents=True)
    py = plain / "bin" / "python"
    py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    os.chmod(py, 0o755)
    home = tmp_path / "fakehome"
    (home / ".conda").mkdir(parents=True)
    (home / ".conda" / "environments.txt").write_text(f"{plain}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))  # 就算 environments.txt 登记了它
    assert launcher.launcher_for_interpreter(str(py)) is None
    launcher.activate_env(str(py), {})
    assert conda_calls(log) == []


@posix_only
def test_a_directly_launched_conda_env_still_records_its_version_in_the_context(
    tmp_path, monkeypatch
):
    """找不到 conda 而直接启动的 Conda 环境：启动上下文仍带环境版本——外部 conda install / 改钩子后，热会话与计划看得见。"""
    prefix, conda, log = make_fake_conda(tmp_path, sys.executable)
    py = str(prefix / "bin" / "python")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    _FAKE_ROOTS.clear()
    before = launcher.context_key(py)
    assert before.startswith("direct|")
    with open(prefix / "conda-meta" / "history", "a", encoding="utf-8") as fh:
        fh.write("# cmd: conda install vendor-sdk\n")
    assert launcher.context_key(py) != before
