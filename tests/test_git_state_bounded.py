"""`scriptbackup.git_state` 在任何情况下都有界返回（QA-WIN-25：预览卡在 `git status`）。

假 git 放 PATH 最前面：挂住的、起孙进程握着 stdout 管道不放的、读 stdin 的。POSIX 才能用 shebang 脚本
当 `git`；Windows 专属行为（`taskkill /T`、CREATE_NO_WINDOW）要 Lab 真机验。
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import scriptbackup

pytestmark = pytest.mark.skipif(os.name == "nt", reason="假 git 用 shebang 脚本；Windows 走 Lab")


def _fake_git(tmp_path: Path, body: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "git"
    exe.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return bindir


@pytest.fixture
def script(tmp_path):
    p = tmp_path / "proj" / "fig.py"
    p.parent.mkdir()
    p.write_text("print(1)\n", encoding="utf-8")
    return p


def _use(monkeypatch, bindir: Path):
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


def test_hanging_git_returns_none_near_timeout(tmp_path, script, monkeypatch):
    _use(monkeypatch, _fake_git(tmp_path, "import time\ntime.sleep(60)\n"))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", 0.5)
    t0 = time.monotonic()
    assert scriptbackup.git_state(script) is None
    assert time.monotonic() - t0 < 4


def test_grandchild_holding_pipe_does_not_block(tmp_path, script, monkeypatch):
    """核心：孙进程继承 stdout 不退。`subprocess.run(capture_output, timeout)` 在这里会一直等管道 EOF。"""
    body = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    )
    _use(monkeypatch, _fake_git(tmp_path, body))
    monkeypatch.setattr(scriptbackup, "_GIT_TIMEOUT_S", 0.5)
    t0 = time.monotonic()
    assert scriptbackup.git_state(script) is None
    assert time.monotonic() - t0 < 4


def test_git_that_exits_but_leaves_daemon_on_pipe_still_returns_output(
    tmp_path, script, monkeypatch
):
    """git 正常退出、却留下握着 stdout 的守护进程（fsmonitor 之类）：照样拿到结果，不等守护进程。"""
    body = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(' M fig.py')\n"
    )
    _use(monkeypatch, _fake_git(tmp_path, body))
    t0 = time.monotonic()
    assert scriptbackup.git_state(script) == {"tracked": True, "dirty": True}
    assert time.monotonic() - t0 < 4


def test_stdin_is_devnull_and_env_disables_prompts(tmp_path, script, monkeypatch):
    out = tmp_path / "seen.txt"
    body = (
        "import os, sys\n"
        f"open({str(out)!r}, 'w').write(repr((os.path.samestat(os.fstat(0), os.stat(os.devnull)), os.environ.get('GIT_OPTIONAL_LOCKS'), "
        "os.environ.get('GIT_TERMINAL_PROMPT'), sys.argv[1:4])))\n"
    )
    _use(monkeypatch, _fake_git(tmp_path, body))
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
    assert seen[0] is True  # fd 0 就是 /dev/null，不是继承来的管道 / 终端
    assert seen[1] == "0" and seen[2] == "0"
    assert seen[3] == ["-c", "core.fsmonitor=false", "-C"]


def test_untracked_and_failure_paths(tmp_path, script, monkeypatch):
    _use(monkeypatch, _fake_git(tmp_path, "print('?? fig.py')\n"))
    assert scriptbackup.git_state(script) == {"tracked": False, "dirty": True}
    bindir2 = tmp_path / "bin2"
    bindir2.mkdir()
    exe = bindir2 / "git"
    exe.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(128)\n")
    exe.chmod(0o755)
    _use(monkeypatch, bindir2)
    assert scriptbackup.git_state(script) is None
