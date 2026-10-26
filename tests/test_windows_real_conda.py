"""真 Windows + 真 Miniforge 的 Conda 环境集成用例（环境矩阵 conda 腿的两种布局，ADR 0142）。

没有真 Miniforge 就 skip（= not_run）：`TAVOTTO_TEST_WIN_CONDA_ROOT` 指向安装根（带 `Scripts\\conda.exe`），
Windows Lab 上默认取 `C:\\TavottoLab\\cache\\envs\\miniforge`。两种布局各一个环境，由 Lab 的 `prepare_envs` 建好：

* `-n mpl` → `<根>\\envs\\mpl`（用户最常见的用法，属于可信安装根，走激活捕获）
* `-p <任意目录>` → `<根>\\..\\conda-mpl`（不在任何安装根下，既定行为是直接启动）
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tavotto.engine import bootstrap, launcher, pool

_LAB_ROOT = Path(r"C:\TavottoLab\cache\envs\miniforge")
_ROOT = Path(os.environ.get("TAVOTTO_TEST_WIN_CONDA_ROOT") or _LAB_ROOT)
_NAMED = _ROOT / "envs" / "mpl" / "python.exe"

needs_real_win_conda = pytest.mark.skipif(
    os.name != "nt" or not (_ROOT / "Scripts" / "conda.exe").is_file() or not _NAMED.is_file(),
    reason="没有真 Windows Miniforge（TAVOTTO_TEST_WIN_CONDA_ROOT）：not_run",
)


@needs_real_win_conda
def test_named_env_is_adopted_through_activation_and_status_is_ok(monkeypatch):
    """`conda create -n mpl` 建在安装根 `envs\\` 下：被识别为要激活，体检过，环境状态 ok 且来源 env_override。"""
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", str(_NAMED))
    pool.reset_worker_python()
    assert launcher.launcher_for_interpreter(_NAMED) is not None
    st = bootstrap.status()
    assert st["ok"] is True, st
    assert st["source"] == pool.SOURCE_ENV
