"""一次 `build_manifest` 里，**刻度模型只重算固定几趟**（issue #220）。

这不是一条性能断言（性能数字在这台机器上受别的进程影响，写进用例就是偶发红），
是它背后的**结构性**约束：`Axis._update_ticks()`（locator + formatter + 视区
取舍）的调用次数**与这张图有多少条刻度无关**。

为什么盯这一条：`TickLabel.live()` 与 `drawn_tick_label_entries()` 都是
`ax.get_[xyz]ticklabels()` 的调用方，而 matplotlib 每次都会重跑一遍
`_update_ticks()`。`build_manifest` 对**每个**刻度伪元素要问三次（`_fields_for`
的 text 字段、几何分支、缺字形扫描），刻度一多就成了「一张图重算几十遍同一份
刻度模型」——`Fig1_kinetics` 上 manifest 21.8 ms 里有 10 ms 是它。修法是
`overrides.ticklabel_memo()`：一次 build 之内同一条轴只算一次。

**判据的主语**：worker 解释器里、一次 `build_manifest` 之内、matplotlib 侧
`Axis._update_ticks` 的**调用次数**（不是 wall time）。事实由
`tests/support/manifest_ticklabel_probe.py` 采，判定在这里。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "tests", "support", "manifest_ticklabel_probe.py")


@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run(
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def test_the_two_figures_really_differ_in_tick_count(probe):
    """先证明尺子量得到东西：两张图的刻度伪元素数必须真的差着一大截。

    少了这一条，下面那条「次数相等」在两张图**碰巧一样**时也会绿——那是最典型
    的恒真判据：它量的维度上根本没有差异可言。
    """
    few, many = probe["few"], probe["many"]
    assert few["ticklabel_elements"] == 8, few
    assert many["ticklabel_elements"] == 48, many
    assert many["ticklabel_elements"] >= 6 * few["ticklabel_elements"]


def test_tick_model_is_recomputed_a_fixed_number_of_times_per_manifest(probe):
    """刻度多六倍，`_update_ticks` 的次数**一次都不许多**。

    没有 `ticklabel_memo` 时它是 `常数 + 3 × 刻度伪元素数`：48 条刻度的那张图
    会比 8 条的多出 120 次重算。有它时两张图都是同一个常数（matplotlib 自己
    在 draw / tight bbox 里踩的那几趟），差值为 0。
    """
    few, many = probe["few"], probe["many"]
    assert many["update_ticks"] == few["update_ticks"], (
        f"刻度从 {few['ticklabel_elements']} 条涨到 {many['ticklabel_elements']} 条，"
        f"`Axis._update_ticks` 从 {few['update_ticks']} 次涨到 {many['update_ticks']} 次"
        f"——刻度模型的重算次数跟着刻度条数走了"
    )


def test_the_memo_does_not_outlive_one_build(probe):
    """记忆表**不许跨 build 存活**：改完刻度再建一次，manifest 看见的是新刻度。

    缓存这类改动唯一会致命的失效形状：数字更好看了，而用户改完刻度界面上还是
    上一帧那排字。`ticklabel_memo` 的作用域必须严格是一次 `build_manifest`。

    **反证记录（这条守卫被实现了两遍）**：进入时新建一张表、退出时还原上一层，
    单独拆掉任何一条都被另一条兜住，用例照绿——这不是判据空，是同一条保证有两
    份实现。两条一起拆掉时它见红：`after` 变成
    `["AA", "BB", "CC", "0.75", "1.00"]`，正是上一帧那两条字留在了元素表里。
    """
    got = probe["across_builds"]
    assert got["before"] == ["0.00", "0.25", "0.50", "0.75", "1.00"], got
    assert got["after"] == ["AA", "BB", "CC"], got
