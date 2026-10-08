"""环境建议 / 检查 / 采用的测试世界（T05，ADR 0114）。

两种夹具，各管一件事：

* `World`：**假**解释器 + 假登录 shell，一被启动就在项目之外的哨兵目录里写一个文件。用来证明「谁没执行」
  （推荐、GET、扫描、报告读取时哨兵计数 = 0；明确的检查动作只触发被点名的那一个）。假解释器不会通过体检——
  它存在的意义是计数，不是被采用。
* `real_venv`：`python -m venv` 现建、再把宿主的科学栈接进去的**真** venv（`venvfixture`）。用来证明「采用之后实际
  worker 的 prefix / 环境代」与「重建之后同一路径不是同一个环境」。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from support import venvfixture


def venv_python(venv: Path) -> Path:
    """venv 目录 → 里面解释器的路径，按平台（POSIX `bin/python`，Windows `Scripts/python.exe`）。
    与产品侧 `projectenv._interpreter_names` 同一份布局，夹具别再手写 `bin/python`。"""
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def venv_rel(name: str) -> str:
    """`GET` 报告里 `python_relative` 的样子（POSIX 形态分隔符，Windows 是 `Scripts/python.exe`）。"""
    return f"{name}/Scripts/python.exe" if os.name == "nt" else f"{name}/bin/python"


def pyenv_python(home: Path, version: str) -> Path:
    """`~/.pyenv` 下某个版本的解释器：POSIX `versions/<v>/bin/python3`，pyenv-win `pyenv-win/versions/<v>/python.exe`
    （与 `userenvs._pyenv_version_dirs` / `_prefix_python` 同一份布局）。"""
    if os.name == "nt":
        return home / ".pyenv" / "pyenv-win" / "versions" / version / "python.exe"
    return home / ".pyenv" / "versions" / version / "bin" / "python3"


def set_home(monkeypatch, home: Path) -> None:
    """让 `os.path.expanduser("~")` 指向 `home`：POSIX 读 HOME，Windows 读 USERPROFILE（只设 HOME 在 Windows 无效）。"""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


class World:
    """一个带哨兵的项目。哨兵目录在项目**之外**。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.sentinel = base / "sentinel"
        self.sentinel.mkdir()
        self.root = base / "项目 空格"  # 中文 + 空格路径同时钉着
        self.root.mkdir()
        (self.root / "plot.py").write_text(
            "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.plot([1, 2])\n"
            "fig.savefig('curve.pdf')\n",
            "utf-8",
        )
        (self.root / "requirements.txt").write_text("numpy>=1.20\nmatplotlib\n", "utf-8")
        self.venv_python = self.fake_venv(".venv")
        self.shell = base / "fakeshell.sh"
        self.shell.write_text(
            f'#!/bin/sh\ntouch "{self.sentinel}/login_shell.$$"\necho nothing\n', "utf-8"
        )
        self.shell.chmod(0o755)

    def fake_venv(self, name: str) -> Path:
        venv = self.root / name
        python = venv_python(venv)
        python.parent.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /nowhere\n", "utf-8")
        python.write_text(
            f'#!/bin/sh\ntouch "{self.sentinel}/{name.strip(".")}_python.$$"\nexit 0\n', "utf-8"
        )
        python.chmod(0o755)
        return python

    def fired(self) -> list[str]:
        return sorted(p.name.split(".")[0] for p in self.sentinel.iterdir())

    def reset_sentinels(self) -> None:
        for p in self.sentinel.iterdir():
            p.unlink()


def real_venv(root: Path, name: str = ".venv", *, python: str) -> str:
    """真 venv（能 import matplotlib、能起 Tavotto 的 worker）；回它的解释器路径。"""
    venv = venvfixture.make_project_venv(root, name, python=python)
    from tavotto.engine import projectenv

    found = projectenv.interpreter_of(venv)
    assert found
    return found


def rebuild_venv(root: Path, name: str, *, python: str) -> str:
    """删掉并在**同一路径**重建一个 venv：路径一个字没变，环境却是另一代。"""
    shutil.rmtree(root / name)
    return real_venv(root, name, python=python)


def tree_digest(path: Path) -> str:
    """一个目录树的内容指纹（相对路径 + 大小 + mtime_ns）：「采用不改动环境」的证据。"""
    import hashlib

    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        try:
            st = p.lstat()
        except OSError:
            continue
        # 解释器 .pyc 是读环境时可能被写出来的东西：体检带 -B，这里不豁免它，豁免就等于放过
        h.update(f"{p.relative_to(path).as_posix()}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def python_identity(python: str) -> dict:
    """问那个解释器它自己的 `sys.prefix` / `sys.executable`——「实际 worker 的解释器」的独立证据。"""
    out = subprocess.run(
        [
            python,
            "-c",
            "import sys,json;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    import json

    return json.loads(out.stdout.strip().splitlines()[-1])


HOST_PYTHON = sys.executable
POSIX = os.name != "nt"
