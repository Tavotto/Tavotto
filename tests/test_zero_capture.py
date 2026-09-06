"""脚本跑完一张图都没有：报「没出图」并带上脚本自己的输出，不报「stem 不存在」。

真实来源（2026-09-06）：一个目录里九个 ovito 脚本用 `os.path.exists("1/…")` 找数据，
worker 的 cwd 是沙盒，全部「未找到」→ 零张图；界面只说「stem 不存在」，而脚本
自己那句「[ERROR] 文件未找到」就躺在 worker.log 里。这里用真 worker 走 Python 池。
"""

import pytest

from tavotto.engine import pool as engine_pool

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT = """\
import os
import matplotlib.pyplot as plt

if os.path.exists("1/etch_5-5.lammpstrj"):
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4])
    fig.savefig("impact_histogram.png")
else:
    print("[ERROR] 文件未找到! 尝试了: '1/etch_5-5.lammpstrj'")
    print("--- 跳过此组合 ---")
"""


@pytest.fixture
def figs(tmp_path):
    root = tmp_path / "figs"
    root.mkdir()
    (root / "run_all.py").write_text(SCRIPT, encoding="utf-8")
    (root / "1").mkdir()
    (root / "1" / "etch_5-5.lammpstrj").write_text("", encoding="utf-8")  # 数据明明在项目里
    yield root
    engine_pool.shutdown_all(str(root), wait=True)


def test_zero_figures_report_the_script_output_not_unknown_stem(figs):
    worker, resp = engine_pool.build("run_all.py", str(figs), "__main__")
    assert resp.get("stems") == {}, (
        "沙盒 cwd 下 exists() 为假，脚本一张图都不画（这是本用例的前提）"
    )
    with pytest.raises(engine_pool.WorkerError) as err:
        worker.override("impact_histogram", [])
    assert err.value.code == engine_pool.NO_FIGURES_CODE
    assert "run_all.py" in str(err.value)
    assert "[ERROR] 文件未找到" in err.value.traceback_text
    assert "跳过此组合" in err.value.traceback_text
    assert err.value.script_name == "run_all.py"
