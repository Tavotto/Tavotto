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

    # **父进程这一侧也必须指名 UTF-8**：`text=True` 用的是父进程 locale
    # （Windows runner 上是 cp1252），而工具的 stdout 已经钉成 UTF-8——
    # subprocess 的读线程会在解码时抛 UnicodeDecodeError 而**死掉**，
    # `communicate()` 于是把那一路交成 `None`，症状是
    # `TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'`
    # ——看上去像用例写错了，其实是编码。CI 的 windows 腿实测逮到过。
    proc = subprocess.run(
        [WORKER_PY, TOOL, os.path.join("sub", "fig.py")],
        cwd=str(tmp_path), capture_output=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # 干净的图退 0；有缺口的退非 0，见下一条
    assert "FileNotFoundError" not in (proc.stdout + proc.stderr)
    # 真的跑了这张图：捕获到的 stem 是脚本 savefig 出来的那个
    assert "census_probe" in proc.stdout, proc.stdout


GAP_SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig = plt.figure()
    ax = fig.add_subplot(projection="3d")
    gx, gy = np.meshgrid(np.linspace(0, 1, 6), np.linspace(0, 1, 6))
    # Poly3DCollection：instrument 不登记，只有普查报得出来
    ax.plot_surface(gx, gy, gx * gy)
    fig.savefig("gap_probe.pdf")


main()
"""


def test_a_census_that_lists_gaps_must_not_exit_zero(tmp_path):
    """普查报告里列着 MISSING，退出码就不许是 0。

    `print_report()` 早就算好了漏掉几类，`main()` 却把返回值扔了、无条件
    `return 0`——于是升级检查单与 CI 拿到的是「通过」，而报告正文里列着一串
    漏掉的类。**一份报平安的门禁比没有门禁更坏**，何况这个工具存在的唯一理由
    就是回答「有没有东西被我们悄悄漏掉了」。

    `--json` 那条路仍然回 0（除非样本自己跑挂了）：那时判定归读 JSON 的调用方，
    工具只负责如实吐数据。
    """
    (tmp_path / "gap.py").write_text(GAP_SCRIPT, encoding="utf-8")
    clean = tmp_path / "clean.py"
    clean.write_text(SCRIPT, encoding="utf-8")

    def run(script, *extra):
        return subprocess.run([WORKER_PY, TOOL, str(script), *extra],
                              cwd=str(tmp_path), capture_output=True,
                              text=True, encoding="utf-8", timeout=300)

    ok = run(clean)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    gap = run(tmp_path / "gap.py")
    assert gap.returncode != 0, (
        "普查列出了漏掉的 artist，退出码却是 0——调用方会把它当成审计通过：\n"
        + gap.stdout[-1500:])
    assert "Poly3DCollection" in gap.stdout, gap.stdout[-1500:]

    # JSON 模式把判定交给调用方，照旧回 0
    js = run(tmp_path / "gap.py", "--json")
    assert js.returncode == 0, js.stdout + js.stderr
