"""被 `set_clip_path` 裁成任意形状的填充集合：geometry 描**画出来的那块**。

渐变填充的惯用写法是一组铺满矩形的色带（`PolyCollection`，逐条一个颜色）再
`set_clip_path(PathPatch(峰形多边形))`。从前 geometry 按色带出：选中框是一整条
横贯子图的矩形，几个峰相互重叠时点这个峰选中那个峰；几百条色带还吃光整张图的
点数预算，后面的子图全部退回 bbox（2026-11-03，用户同门的 XPS 分峰图，36 个峰
只有 12 个有几何）。

钉四件事：

1. 色带拼成矩形 R：geometry = 裁剪路径（一条闭合路径、只按填充命中），
   `clip` = R ∩ 子图框；
2. 重叠的两个峰各自只罩自己：A 峰峰顶不在 B 峰的几何里；
3. 裁剪路径整个落在（非矩形的）填充里：geometry = 裁剪路径；
4. 填充只盖住裁剪路径一部分、又不是矩形：表达不了，照旧按填充本身出（不冒充）；
   以及很多个这样的峰不再把预算吃光。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_clipfill.py"
ENTRY = "main"
STEM = "ClipFill"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Circle, PathPatch
from matplotlib.path import Path


def band(ax, x, y, bg, n=256):
    # 与用户脚本同一写法：色带横贯 x 全程（比 xlim 还宽），再裁到「峰 ~ 本底」多边形
    edges = np.linspace(float(y.min()), float(y.max()), n + 1)
    verts = [[(x[0], a), (x[-1], a), (x[-1], b), (x[0], b)] for a, b in zip(edges[:-1], edges[1:])]
    t = np.linspace(0.0, 1.0, n)
    coll = PolyCollection(verts, facecolors=np.column_stack([t, 0.3 + 0 * t, 1 - t]),
                          edgecolors="none", linewidths=0, antialiaseds=False)
    ax.add_collection(coll)
    poly = np.vstack([np.column_stack([x, y]), np.column_stack([x[::-1], bg[::-1]]), [[x[0], y[0]]]])
    coll.set_clip_path(PathPatch(Path(poly), transform=ax.transData))
    return coll


def main():
    fig, (ax, ax2, ax3, ax4, ax5) = plt.subplots(1, 5, figsize=(15.0, 3.0))
    x = np.linspace(-1.0, 11.0, 400)
    bg = 0.1 + 0.0 * x
    # axes_0.fill_0 / fill_1：两个相互重叠的峰（A 在 4、B 在 6），B 后画、在上层
    band(ax, x, bg + np.exp(-((x - 4.0) / 1.0) ** 2), bg)
    band(ax, x, bg + 0.6 * np.exp(-((x - 6.0) / 1.2) ** 2), bg)
    ax.set_xlim(0.0, 10.0)
    ax.set_ylim(0.0, 1.3)

    # axes_1.fill_0：非矩形的大填充（三角形）裁成落在它里面的小圆 → 几何 = 圆
    tri = PolyCollection([[(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]], facecolors="tab:green")
    ax2.add_collection(tri)
    tri.set_clip_path(Circle((5.0, 3.0), 1.5, transform=ax2.transData))
    # axes_1.fill_1：小三角形只盖住大圆的一部分 → 表达不了，照旧按三角形出
    small = PolyCollection([[(6.0, 6.0), (9.0, 6.0), (7.5, 9.0)]], facecolors="tab:red")
    ax2.add_collection(small)
    small.set_clip_path(Circle((7.5, 6.0), 3.0, transform=ax2.transData))
    ax2.set_xlim(0.0, 10.0)
    ax2.set_ylim(0.0, 10.0)

    # axes_2.fill_0..23：24 个裁剪后的渐变峰——从前 256 条 × 24 远超整张图的点数预算
    for k in range(24):
        c = 0.4 * k
        band(ax3, x, bg + 0.5 * np.exp(-((x - c) / 0.3) ** 2), bg)
    ax3.set_xlim(0.0, 10.0)
    ax3.set_ylim(0.0, 1.0)

    # axes_3.fill_0：U 形填充（凹口在中间），三角形裁剪的三个顶点都在 U 里、(5,6) 在凹口里
    u = PolyCollection([[(0, 0), (10, 0), (10, 10), (7, 10), (7, 3), (3, 3), (3, 10), (0, 10)]],
                       facecolors="tab:blue")
    ax4.add_collection(u)
    u.set_clip_path(PathPatch(Path([(1, 8), (9, 8), (5, 1), (1, 8)]), transform=ax4.transData))
    ax4.set_xlim(0.0, 10.0)
    ax4.set_ylim(0.0, 10.0)

    # axes_4.fill_0：三块互相重叠的矩形，面积和 = 3x2 外包盒，但右上角 1x1 是空的
    ov = PolyCollection([[(0, 0), (2, 0), (2, 1), (0, 1)], [(1, 0), (3, 0), (3, 1), (1, 1)],
                         [(0, 1), (2, 1), (2, 2), (0, 2)]], facecolors="tab:orange")
    ax5.add_collection(ov)
    ov.set_clip_path(PathPatch(Path([(0, 0), (3, 0), (3, 2), (0, 2), (0, 0)]), transform=ax5.transData))
    ax5.set_xlim(0.0, 4.0)
    ax5.set_ylim(0.0, 4.0)
    fig.savefig("ClipFill.pdf")
"""


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    figs = tmp_path_factory.mktemp("clipfill-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, [])
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _box(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _in_polygon(pt, points) -> bool:
    """射线法：点在不在闭合多边形里（figure 分数，本进程不 import matplotlib）。"""
    x, y = pt
    inside = False
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _data_to_frac(man, gid_axes, dx, dy, xlim, ylim):
    ax, ay, aw, ah = _el(man, gid_axes)["bbox"]
    fx = ax + (dx - xlim[0]) / (xlim[1] - xlim[0]) * aw
    fy = ay + ah - (dy - ylim[0]) / (ylim[1] - ylim[0]) * ah  # y 向下
    return fx, fy


def test_clipped_gradient_band_traces_the_clip_path_not_the_stripes(manifest):
    geom = _el(manifest, "axes_0.fill_0")["geometry"]
    assert geom["kind"] == "path", "256 条色带不该原样当几何发出去"
    assert geom["fill"] is True and geom["stroke"] is False
    # 色带比 xlim 宽：clip = 色带矩形 ∩ 子图框，左右两边就是子图框
    axes = _el(manifest, "axes_0")["bbox"]
    clip = geom["clip"]
    assert clip[0] == pytest.approx(axes[0], abs=2e-3)
    assert clip[0] + clip[2] == pytest.approx(axes[0] + axes[2], abs=2e-3)
    # 上沿是峰顶，不是子图顶：clip 只罩色带那一截
    assert clip[1] > axes[1] + 0.05 * axes[3]


def test_overlapping_peaks_each_cover_only_their_own_shape(manifest):
    a = _el(manifest, "axes_0.fill_0")["geometry"]["paths"][0]["points"]
    b = _el(manifest, "axes_0.fill_1")["geometry"]["paths"][0]["points"]
    lims = dict(xlim=(0.0, 10.0), ylim=(0.0, 1.3))
    apex_a = _data_to_frac(manifest, "axes_0", 4.0, 1.0, **lims)
    apex_b = _data_to_frac(manifest, "axes_0", 6.0, 0.62, **lims)
    assert _in_polygon(apex_a, a) and _in_polygon(apex_b, b)
    # 修复前 B 的几何是横贯子图、高到 B 峰顶的整条色带：A 峰在那个高度以下的部分全被 B 罩住
    below_b_top = _data_to_frac(manifest, "axes_0", 4.0, 0.5, **lims)
    assert _in_polygon(below_b_top, a)
    assert not _in_polygon(below_b_top, b), "点 A 峰选中的是在上层的 B 峰"


def test_clip_path_inside_a_non_rect_fill_is_the_drawn_shape(manifest):
    geom = _el(manifest, "axes_1.fill_0")["geometry"]
    x0, y0, x1, y1 = _box(geom["paths"][0]["points"])
    c = _data_to_frac(manifest, "axes_1", 5.0, 3.0, xlim=(0, 10), ylim=(0, 10))
    # 圆心在中央、外接框是圆（宽高都是 3/10 个子图），而不是三角形的整片
    ax = _el(manifest, "axes_1")["bbox"]
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == pytest.approx(c, abs=3e-3)
    assert x1 - x0 == pytest.approx(0.3 * ax[2], rel=0.03)
    assert y1 - y0 == pytest.approx(0.3 * ax[3], rel=0.03)


def test_partial_cover_is_not_faked_as_the_clip_shape(manifest):
    geom = _el(manifest, "axes_1.fill_1")["geometry"]
    # 照旧按三角形本身出：三个角（闭合点不计），而不是那个大圆
    x0, y0, x1, y1 = _box(geom["paths"][0]["points"])
    ax = _el(manifest, "axes_1")["bbox"]
    assert x1 - x0 == pytest.approx(0.3 * ax[2], rel=0.03), "三角形宽 3，不是圆的直径 6"


def test_many_clipped_bands_all_keep_their_geometry(manifest):
    fills = [e for e in manifest["elements"] if e["gid"].startswith("axes_2.fill_")]
    assert len(fills) == 24
    missing = [e["gid"] for e in fills if "geometry" not in e]
    assert not missing, f"点数预算被色带吃光、退回 bbox：{missing}"


def test_concave_fill_is_not_faked_as_the_clip_triangle(manifest):
    geom = _el(manifest, "axes_3.fill_0")["geometry"]
    pts = geom["paths"][0]["points"]
    notch = _data_to_frac(manifest, "axes_3", 5.0, 6.0, xlim=(0, 10), ylim=(0, 10))
    # 凹口里的点不在 U 形填充里；退回填充本身的路径后它不会落在几何里
    assert not _in_polygon(notch, pts), "凹口（空的）被三角形裁剪路径冒充成了墨迹"


def test_overlapping_tiles_with_a_hole_do_not_count_as_a_rect(manifest):
    geom = _el(manifest, "axes_4.fill_0")["geometry"]
    pts = geom["paths"][0]["points"]
    hole = _data_to_frac(manifest, "axes_4", 2.5, 1.5, xlim=(0, 4), ylim=(0, 4))
    assert not _in_polygon(hole, pts), "右上角的空洞被裁剪路径（整个 3x2 矩形）盖住了"
