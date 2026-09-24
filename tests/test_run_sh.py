"""`run.sh`（源码树一步启动）的安装判据：依赖声明一变就重装（Codex #539）。

旧 `.venv` 里 `import tavotto` 照样成功——包本身很轻——只凭它判断会跳过 `pip install -e .`，
U10 新加的 RenderCore 原生包一个都没装上，第一次预览才报 `backend_unavailable`。`run.sh` 记一个
`pyproject.toml` 校验和的戳，戳对不上就重装、装成功才写戳。这里用假的 `.venv`（每个可执行文件只记账）
在临时目录里把 `run.sh` 真跑几遍。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="run.sh 是 POSIX shell 脚本")


def _fake_venv(tmp: Path, *, pip_ok: bool = True) -> Path:
    log = tmp / "calls.log"
    bindir = tmp / ".venv" / "bin"
    bindir.mkdir(parents=True)
    scripts = {
        # `-c "import tavotto"` 与 `scripts/fetch_fonts.py --check` 都算成功：只看安装判据
        "python": 'echo "python $*" >> "$LOG"; exit 0',
        "pip": f'echo "pip $*" >> "$LOG"; exit {0 if pip_ok else 1}',
        "tavotto": 'echo "tavotto $*" >> "$LOG"; exit 0',
    }
    for name, body in scripts.items():
        p = bindir / name
        p.write_text(f"#!/bin/sh\nLOG={log}\n{body}\n", encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return log


def _run(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(tmp / "run.sh"), "--no-browser"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )


def _calls(log: Path) -> list[str]:
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    log.write_text("", encoding="utf-8")
    return lines


@pytest.fixture
def tree(tmp_path):
    shutil.copy(ROOT / "run.sh", tmp_path / "run.sh")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["flask"]\n', encoding="utf-8"
    )
    return tmp_path


def test_reinstalls_when_the_dependency_declaration_changes(tree):
    log = _fake_venv(tree)
    assert _run(tree).returncode == 0
    first = _calls(log)
    assert "pip install -e ." in first, first  # 没有戳：装
    assert first[-1] == "tavotto --no-browser"

    assert _run(tree).returncode == 0
    second = _calls(log)
    assert not any(c.startswith("pip ") for c in second), second  # 什么都没变：不装

    # 依赖声明变了（U10 往 dependencies 里加了 RenderCore 的原生包）：旧 .venv 里 import tavotto
    # 照样成功，但必须重装
    (tree / "pyproject.toml").write_text(
        '[project]\ndependencies = ["flask", "pikepdf"]\n', encoding="utf-8"
    )
    assert _run(tree).returncode == 0
    third = _calls(log)
    assert "pip install -e ." in third, third


def test_a_failed_install_stops_and_leaves_no_stamp(tree):
    """装失败就停，不带病启动；戳不写，下一次还会再试。"""
    log = _fake_venv(tree, pip_ok=False)
    assert _run(tree).returncode != 0
    calls = _calls(log)
    assert "pip install -e ." in calls and not any(c.startswith("tavotto") for c in calls), calls
    assert not (tree / ".venv" / ".tavotto-install-stamp").exists()
