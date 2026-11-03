"""`scriptbackup.git_state` 在任何情况下都有界返回（QA-WIN-25：预览卡在 `git status`）。

跨平台：假 git 是普通的「挂住 / 起孙进程」Python 脚本，不是攻击性夹具。
- POSIX：shebang 脚本放 PATH 最前面当 `git`（最贴近真实解析）。
- Windows：`Popen(["git", ...])` 走 CreateProcess，只会找 `git.exe`，`.cmd`/`.bat` 找不到、也不好伪造 exe，
  所以把 `scriptbackup.subprocess.Popen` 换成一层薄包装，只把 argv[0]=="git" 改写成
  `[sys.executable, 假git.py, ...]`，其余（含 `taskkill`）原样。超时后杀进程树走真实的 `taskkill /F /T`。
进程是否被清用 PID 判（Windows 用 OpenProcess/GetExitCodeProcess，**不能**用 `os.kill(pid, 0)`——
Windows 上它会真的终止进程），不按名字杀。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import procprobe
from tavotto.engine import scriptbackup

_IS_NT = os.name == "nt"
# 孙进程显式继承 fd 1/2（Windows 的 Popen 默认 close_fds，不显式传就不会继承父进程的 stdout 句柄）
_SPAWN_SLEEPER = (
    "import subprocess, sys\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdout=1, stderr=2)\n"
)


def _read_pids(path: Path) -> list[int]:
    return [int(x) for x in path.read_text().split()]


def _assert_all_gone(pids: list[int], grace: float = 8.0) -> None:
    deadline = time.monotonic() + grace
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        alive = [p for p in alive if procprobe.alive(p)]
        if alive:
            time.sleep(0.1)
    if alive:  # 万一没清掉，别把睡眠进程留在机器上（只杀自己登记的 PID）
        for p in alive:
            try:
                if _IS_NT:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p)], capture_output=True)
                else:
                    os.kill(p, 9)
            except OSError:
                pass
    assert not alive, f"进程没被清掉：{alive}"


def _fake_git(tmp_path: Path, body: str, name: str = "bin") -> Path:
    bindir = tmp_path / name
    bindir.mkdir(exist_ok=True)
    if _IS_NT:
        exe = bindir / "fake_git.py"
        exe.write_text(body, encoding="utf-8")
        return exe
    exe = bindir / "git"
    exe.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    exe.chmod(0o755)
    return bindir


@pytest.fixture
def script(tmp_path):
    p = tmp_path / "proj" / "fig.py"
    p.parent.mkdir()
    p.write_text("print(1)\n", encoding="utf-8")
    return p


def _use(monkeypatch, fake: Path):
    if not _IS_NT:
        monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
        return
    real_popen = subprocess.Popen

    def popen(argv, *a, **kw):
        if isinstance(argv, list) and argv and argv[0] == "git":
            argv = [sys.executable, str(fake), *argv[1:]]
        return real_popen(argv, *a, **kw)

    monkeypatch.setattr(scriptbackup.subprocess, "Popen", popen)


_HANG_TIMEOUT = 3.0  # Windows 上 Python 冷启动 + Defender 可能数百毫秒，给假 git 足够时间先登记 PID
_HANG_BOUND = 12.0  # 上限只防「永远等」，不卡具体秒数


def _pid_prelude(pidfile: Path, with_grandchild: bool) -> str:
    spawn = _SPAWN_SLEEPER if with_grandchild else "g = None\n"
    return (
        "import os\n"
        f"{spawn}"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()) + ' ' + str(g.pid if g else ''))\n"
    )


def test_hanging_git_returns_none_near_timeout_and_is_killed(tmp_path, script, monkeypatch):
    pidfile = tmp_path / "pids.txt"
    body = _pid_prelude(pidfile, with_grandchild=False) + "import time\ntime.sleep(60)\n"
    _use(monkeypatch, _fake_git(tmp_path, body))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", _HANG_TIMEOUT)
    t0 = time.monotonic()
    assert scriptbackup.git_state(script) is None
    assert _HANG_TIMEOUT <= time.monotonic() - t0 < _HANG_BOUND
    _assert_all_gone(_read_pids(pidfile))


def test_grandchild_holding_stdout_does_not_block_and_tree_is_killed(tmp_path, script, monkeypatch):
    """核心：孙进程继承 stdout 不退、git 本身也挂住。`subprocess.run(capture_output, timeout)` 在这里会
    一直等管道 EOF（Windows 上尤其，kill 只杀直接子进程）；这里要有界返回，且整棵进程树被清。"""
    pidfile = tmp_path / "pids.txt"
    body = _pid_prelude(pidfile, with_grandchild=True) + "import time\ntime.sleep(60)\n"
    _use(monkeypatch, _fake_git(tmp_path, body))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", _HANG_TIMEOUT)
    t0 = time.monotonic()
    assert scriptbackup.git_state(script) is None
    assert time.monotonic() - t0 < _HANG_BOUND
    pids = _read_pids(pidfile)
    assert len(pids) == 2  # git 自己 + 孙进程
    _assert_all_gone(pids)


def test_git_that_exits_but_leaves_daemon_on_pipe_still_returns_output(
    tmp_path, script, monkeypatch
):
    """git 正常退出、却留下握着 stdout 的守护进程（fsmonitor 之类）：照样拿到结果，不等守护进程。"""
    pidfile = tmp_path / "pids.txt"
    body = _pid_prelude(pidfile, with_grandchild=True) + "print(' M fig.py')\n"
    _use(monkeypatch, _fake_git(tmp_path, body))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", 10.0)
    t0 = time.monotonic()
    try:
        assert scriptbackup.git_state(script) == {"tracked": True, "dirty": True}
        assert time.monotonic() - t0 < _HANG_BOUND  # 远小于孙进程的 60s
    finally:  # 守护进程本来就不归 git_state 管；测试自己按登记的 PID 收尾
        gpid = _read_pids(pidfile)[1]
        try:
            if _IS_NT:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(gpid)], capture_output=True)
            else:
                os.kill(gpid, 9)
        except OSError:
            pass


def test_stdin_is_devnull_and_env_disables_prompts(tmp_path, script, monkeypatch):
    out = tmp_path / "seen.txt"
    # 判据：stdin 读到 EOF 立刻返回（DEVNULL / NUL）。继承来的管道（写端还开着）会一直阻塞，直到超时。
    # POSIX 另加 fstat 比对 /dev/null 的强判据；Windows 上没有同等可靠的句柄比对，只用 EOF 判据——
    # 且 Windows 下 Popen 总是显式传 DEVNULL，替换本进程 fd 0 影响不到子进程，这条在 Windows 上偏弱（不假装更强）。
    strong = "os.path.samestat(os.fstat(0), os.stat(os.devnull))" if not _IS_NT else "None"
    body = (
        "import os, sys\n"
        f"eof = sys.stdin.buffer.read(1) == b''\n"
        f"open({str(out)!r}, 'w').write(repr((eof, {strong}, os.environ.get('GIT_OPTIONAL_LOCKS'), "
        "os.environ.get('GIT_TERMINAL_PROMPT'), sys.argv[1:4])))\n"
    )
    _use(monkeypatch, _fake_git(tmp_path, body))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", 10.0)
    if _IS_NT:
        assert scriptbackup.git_state(script) == {"tracked": True, "dirty": False}
    else:
        # 把本进程的 fd 0 换成一根管道（桌面 sidecar 的 stdin 就可能是这样）：子进程不许继承它
        r, w = os.pipe()
        saved = os.dup(0)
        os.dup2(r, 0)
        try:
            assert scriptbackup.git_state(script) == {"tracked": True, "dirty": False}
        finally:
            os.dup2(saved, 0)
            for fd in (r, w, saved):
                os.close(fd)
    seen = eval(out.read_text())  # noqa: S307 — 自己写的 repr
    assert seen[0] is True  # stdin 立即 EOF
    assert seen[1] in (True, None)  # POSIX: fd 0 就是 /dev/null；Windows: 不判
    assert seen[2] == "0" and seen[3] == "0"
    assert seen[4] == ["-c", "core.fsmonitor=false", "-C"]


def test_untracked_and_failure_paths(tmp_path, script, monkeypatch):
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", 10.0)
    _use(monkeypatch, _fake_git(tmp_path, "print('?? fig.py')\n"))
    assert scriptbackup.git_state(script) == {"tracked": False, "dirty": True}
    _use(monkeypatch, _fake_git(tmp_path, "import sys\nsys.exit(128)\n", name="bin2"))
    assert scriptbackup.git_state(script) is None
