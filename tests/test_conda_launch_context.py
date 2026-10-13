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
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import databinding, launchctx, launcher, pool as _pool, runconfig

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
if cwd:
    os.chdir(cwd)
sys.exit(subprocess.Popen(cmd, env=env).wait())
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
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return prefix, conda, tmp_path / "conda.log"


def conda_calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_dead(pid: int, timeout: float = 8.0) -> bool:
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
    assert wait_dead(child), "kill() 之后用户 Python 仍在运行"  # 不靠 wait() 补刀
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
        (call,) = conda_calls(log)
        assert call["opts"] == ["--no-capture-output", "--prefix", str(prefix)]
        assert call["cmd"][0] == str(prefix / "bin" / "python")  # wrapper 之后仍是用户的解释器路径
        assert runs(counter)[0]["conda_prefix"] == str(prefix)
        user_pid = resp["runtime"]["pid"]  # worker 自报：真正跑脚本的用户 Python
        assert user_pid != worker.child_pid  # child_pid 是 conda wrapper
        assert alive(user_pid)
        rcpt_launcher = worker.launcher.public()
        assert rcpt_launcher["kind"] == "conda"
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
def test_success_is_remembered_and_reopening_needs_no_argv_but_changes_reverify(
    conda_world, tmp_path, monkeypatch
):
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
        first = _run_session(app, {"script": "fig.py", "argv": ["--scale", "2"]})
        assert first["phase"] == "completed", first
        assert [r["argv"] for r in runs(counter)] == [["--scale", "2"]]
        assert len(conda_calls(log)) == 1

    # ---- 持久化的是引用与摘要：没有激活后的环境、没有 argv 值
    text = store_text(work)
    record = json.loads(text)["launches"]["fig.py"]
    assert record["launcher"] == {
        "kind": "conda",
        "conda_exe": str(prefix.parent.parent / "bin" / "conda"),
        "prefix": str(prefix),
    }
    assert record["run_config"].startswith("rc_") and record["env_state"]
    # 激活后的环境一个字都不落盘；成功记录本身不含 argv 值（用户给的参数原有的登记是 runconfigs 既有行为，不是 P09 新增）
    assert LICENSE not in text and "CONDA_PREFIX" not in text
    assert "--scale" not in json.dumps(record) and "argv" not in json.dumps(record)

    # ---- 再次打开（新进程）：请求不再给参数，上次成功的配置被带回来
    with fa.running_app(proj, work, env_overrides=env) as app:
        again = _run_session(app, {"script": "fig.py"})
        assert again["phase"] == "completed", again
        assert [r["argv"] for r in runs(counter)][-1] == ["--scale", "2"]
        # 明确的空数组 = 用户要无参数：不复用
        none = _run_session(app, {"script": "fig.py", "argv": []})
        assert none["phase"] == "completed"
        assert [r["argv"] for r in runs(counter)][-1] == []

    # ---- 复用不是永久的：成功记录现在是「无参数」那一条，所以再恢复一条带参数的成功，再改脚本
    with fa.running_app(proj, work, env_overrides=env) as app:
        assert (
            _run_session(app, {"script": "fig.py", "argv": ["--scale", "3"]})["phase"]
            == "completed"
        )
    script = proj / "fig.py"
    script.write_text(script.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    with fa.running_app(proj, work, env_overrides=env) as app:
        _run_session(app, {"script": "fig.py"})
        assert [r["argv"] for r in runs(counter)][-1] == []  # 脚本变了：重新核验，不沿用 --scale 3


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


@posix_only
def test_sensitive_run_config_is_never_auto_reused(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(runconfig.config, "data_dir", lambda: tmp_path / "data")
    sel = runconfig.selection_for(proj, "fig.py", ["--token", "SECRET-777"], sensitive=True)
    import hashlib

    rec = launchctx.build_record(
        proj,
        "fig.py",
        _Rcpt(
            sys.executable,
            hashlib.sha1((proj / "fig.py").read_bytes()).hexdigest(),
            [],
            None,
        ),
        run=sel,
    )
    assert rec["run_sensitive"] is True
    runconfig.set_launch_record(proj, "fig.py", rec)
    assert "SECRET-777" not in (tmp_path / "data").rglob("*.json").__next__().read_text("utf-8")
    assert (
        launchctx.reusable_run(proj, "fig.py", python=sys.executable, python_source="env_override")
        is None
    )


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
    finally:
        worker.force_kill()
        _pool.shutdown_all(wait=True)
        _pool.reset_worker_python()
    assert wait_dead(user_pid), "取消 wrapper 后用户 Python 仍在运行"
    ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
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
