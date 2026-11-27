"""误差线 / 误差带画出来的数对不对：用手算的已知结果对拍真实 Matplotlib 图元（不经过模板里的 `summarize` / `sd_of`）。

判据的主语：生成的脚本在真解释器里跑完之后，**图上**误差带的上下沿、误差线的两端，与按定义手算的
均值 ± SD / SEM / 95% CI 一致；缺数的位置带要断开。数据出图的数据核对（`figureverify`）不核误差线 / 误差带
（`fingerprint_coverage` 里是 unchecked），而且期望值与脚本用的是同一个函数——两边一起算错它查不出来，
所以这里用与实现无关的常数当 oracle。

手算（样本标准差 n−1；SEM = SD/√n；CI 半宽 = t(0.975, n−1) × SEM，df = 2 时 t = 4.3027，查公开 t 分布表）：

| x | 值 | 均值 | SD | SEM | CI 半宽 |
|---|---|---:|---:|---:|---:|
| 1 | 1, 2, 3 | 2 | 1 | 0.5773503 | 2.4841655 |
| 2 | 4, 6, 8 | 6 | 2 | 1.1547005 | 4.9683310 |
| 3 | 7 | 7 | — | — | —（只有 1 个值，算不出，带在这里断开） |
| 4 | 9, 10, 11 | 10 | 1 | 0.5773503 | 2.4841655 |
| 5 | 12, 14, 16 | 14 | 2 | 1.1547005 | 4.9683310 |
"""

from __future__ import annotations

import pytest

from tavotto.engine import ai_data_template as T
from tests.test_ai_data_template import Q, _csv, _render_in, mk, needs_worker, probe

ROWS = [["x", "v"]] + [
    [x, v]
    for x, vals in ((1, (1, 2, 3)), (2, (4, 6, 8)), (3, (7,)), (4, (9, 10, 11)), (5, (12, 14, 16)))
    for v in vals
]
CENTER = {1: 2.0, 2: 6.0, 4: 10.0, 5: 14.0}
HALF = {
    "mean_sd": {1: 1.0, 2: 2.0, 4: 1.0, 5: 2.0},
    "mean_sem": {1: 0.5773503, 2: 1.1547005, 4: 0.5773503, 5: 1.1547005},
    "mean_ci95": {1: 2.4841655, 2: 4.9683310, 4: 2.4841655, 5: 4.9683310},
}

# 误差带：fill_between 的每条路径是「下沿往前 + 上沿往回」，按 x 取这一位置的最低 / 最高顶点
_BANDS = (
    "result = {}\n"
    "for ax in fig.axes:\n"
    "    for c in ax.collections:\n"
    "        if c.get_label().startswith('band'):\n"
    "            for p in c.get_paths():\n"
    "                for x, y in p.vertices:\n"
    "                    k = str(round(float(x), 6))\n"
    "                    lo, hi = result.get(k, [float('inf'), float('-inf')])\n"
    "                    result[k] = [min(lo, float(y)), max(hi, float(y))]\n"
)
# 误差线：ErrorbarContainer 的竖线段 [(x, 下端), (x, 上端)]
_BARS = (
    "result = {}\n"
    "for ax in fig.axes:\n"
    "    for c in ax.containers:\n"
    "        for seg in c.lines[2][0].get_segments():\n"
    "            (x0, y0), (x1, y1) = seg\n"
    "            result[str(round(float(x0), 6))] = [float(min(y0, y1)), float(max(y0, y1)), float(x1 - x0)]\n"
)


def _folder(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.csv").write_text(_csv(ROWS), encoding="utf-8")
    return tmp_path


@needs_worker
@pytest.mark.parametrize("summary", ["mean_sd", "mean_sem", "mean_ci95"])
def test_the_band_on_the_figure_is_the_hand_computed_interval(tmp_path, summary):
    work = _folder(tmp_path / "w")
    rendered, _ = _render_in(
        work, "t.csv", mk("t.csv", "trend", [("line", summary), ("band", summary, Q)], ["v"], x="x")
    )
    got = probe(work, rendered, _BANDS)
    for x, c in CENTER.items():
        lo, hi = got[str(float(x))]
        h = HALF[summary][x]
        assert lo == pytest.approx(c - h, abs=1e-6) and hi == pytest.approx(c + h, abs=1e-6), (
            summary,
            x,
            lo,
            hi,
        )
    # x=3 只有 1 个值：没有误差带，带在这里断开（不许把 x=2 与 x=4 连起来，也不许画成 0 宽）
    assert "3.0" not in got, got


@needs_worker
@pytest.mark.parametrize("summary", ["mean_sd", "mean_sem", "mean_ci95"])
def test_the_error_bars_on_the_figure_are_the_hand_computed_interval(tmp_path, summary):
    work = _folder(tmp_path / "w")
    rendered, _ = _render_in(
        work,
        "t.csv",
        mk("t.csv", "trend", [("line", summary), ("errorbar", summary, Q)], ["v"], x="x"),
    )
    got = probe(work, rendered, _BARS)
    assert sorted(got) == ["1.0", "2.0", "4.0", "5.0"], got  # x=3 只有 1 个值：没有误差线
    for x, c in CENTER.items():
        lo, hi, dx = got[str(float(x))]
        h = HALF[summary][x]
        assert dx == 0  # 竖直的
        assert lo == pytest.approx(c - h, abs=1e-6) and hi == pytest.approx(c + h, abs=1e-6), (
            summary,
            x,
        )


def test_the_two_merged_branches_compute_different_statistics():
    """合画成对的两种画法是两个不同的统计问题：「按条件再分开」每个格子 n=2，「只按文件比较」每个文件 n=4。
    手算（x=1；x=2 时中心各 +1，离散不变）：a/p 2±√2、a/q 10±√2、b/p 22±√2、b/q 30±√2；只按文件 a 6±√(68/3)、b 26±√(68/3)。"""
    raw = {
        "a": [(1, "p", 1), (1, "p", 3), (1, "q", 9), (1, "q", 11)],
        "b": [(1, "p", 21), (1, "p", 23), (1, "q", 29), (1, "q", 31)],
    }
    header = ["x", "condition", "y", "file"]
    rows = []
    for f, obs in raw.items():
        for x, c, y in obs:
            rows += [[str(x), c, str(y), f], [str(x + 1), c, str(y + 1), f]]
    split = T.trend_data(header, rows, "x", ["y"], "file", "condition", None, False)
    by_file = T.trend_data(header, rows, "x", ["y"], "file", None, None, False)
    r2, r68_3 = 2**0.5, (68 / 3) ** 0.5

    def at_x1(d, p, g):
        xs, c, lo, hi = T.trend_centers(T.trend_pool(d, p, g, "y"), "mean_sd")
        i = xs.index(1.0)
        return c[i], hi[i] - c[i]

    for (p, g), (m, h) in {
        ("p", "a"): (2, r2),
        ("q", "a"): (10, r2),
        ("p", "b"): (22, r2),
        ("q", "b"): (30, r2),
    }.items():
        assert at_x1(split, p, g) == pytest.approx((m, h)), (p, g)
    assert at_x1(by_file, "", "a") == pytest.approx((6, r68_3))
    assert at_x1(by_file, "", "b") == pytest.approx((26, r68_3))
