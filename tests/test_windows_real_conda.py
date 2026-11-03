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


_PREFIX = Path(os.environ.get("TAVOTTO_TEST_WIN_CONDA_PREFIX") or _ROOT.parent / "conda-mpl")
_PREFIX_PY = _PREFIX / "python.exe"

needs_real_win_prefix = pytest.mark.skipif(
    os.name != "nt" or not (_PREFIX / "conda-meta").is_dir() or not _PREFIX_PY.is_file(),
    reason="没有真 Windows `conda create -p` 环境（TAVOTTO_TEST_WIN_CONDA_PREFIX）：not_run",
)


@needs_real_win_prefix
def test_dash_p_env_outside_any_install_root_runs_numpy_linalg_without_activation():
    """`conda create -p <任意目录>`：不激活（不跑 activate.d），但经 `launcher.activate_env` 取的 env 要让 numpy 的 MKL/BLAS 能加载。
    没有 `Library\\bin` 在 PATH 上时，`numpy.linalg.inv` 以 0xC06D007F 崩溃（Windows Lab 环境矩阵 conda-prefix 腿，run 20261024T153250Z-94de7e）。"""
    import subprocess

    assert launcher.launcher_for_interpreter(_PREFIX_PY) is None  # 仍然不激活
    env = launcher.activate_env(str(_PREFIX_PY), dict(os.environ))
    done = subprocess.run(
        [str(_PREFIX_PY), "-I", "-c", "import numpy; print(numpy.linalg.inv(numpy.eye(2)).sum())"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-600:])
    assert done.stdout.strip() == "2.0"
