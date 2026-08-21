"""开发工具 `scripts/dev/matplotlib_artist_census.py` 的最小看护。

它不在产品路径上（普查是诊断，权威是 `manifest.instrument`），但它是排
兼容缺口时第一个被拿起来的东西——跑不起来就等于没有。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "scripts", "dev", "matplotlib_artist_census.py")

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    fig.savefig("census_probe.pdf")


main()
"""


def test_relative_path_with_directories_still_finds_the_script(tmp_path):
    """`python …/matplotlib_artist_census.py sub/fig.py` 必须跑得起来。

    `abspath` 是相对**当前** cwd 算的。工具会 `chdir` 到脚本目录再跑脚本，
    所以绝对路径必须在 chdir **之前**解出来——解晚了 `sub/fig.py` 会变成
    `<脚本目录>/sub/fig.py`，README 里那条 `examples/figure.py` 的用法当场
    FileNotFoundError（实测拼成 `…/examples/examples/figure.py`）。
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "fig.py").write_text(SCRIPT, encoding="utf-8")

    proc = subprocess.run(
        [WORKER_PY, TOOL, os.path.join("sub", "fig.py")],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FileNotFoundError" not in (proc.stdout + proc.stderr)
    # 真的跑了这张图：捕获到的 stem 是脚本 savefig 出来的那个
    assert "census_probe" in proc.stdout, proc.stdout
