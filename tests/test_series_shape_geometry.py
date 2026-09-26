"""误差棒 / 茎叶系列、Patch 全家族、注释文字与箭头的**选中轮廓与命中几何**（ADR 0086）。

2026-09-26 用户反馈：选中误差棒时蓝色高亮贴不住它——罩着一个把数据点、误差线、帽
连同其间大片空白一起圈进去的矩形，几组误差棒交错时还点这组选中那组。排查
（按族逐个量「命中区 / 高亮」与 Agg 实际墨迹）又找出同形状的几处：圆 / 椭圆 / 饼图
扇形 / 圆角框只有 bbox，注释文字的框一直伸到箭头尖、带字注释的箭头只有 bbox，带
底框的文字框只圈着字。这里钉住修完之后的形状：

1. 误差棒 / 茎叶系列出**成员几何的并**：误差线、帽、茎是两点线段，数据点是
   逐颗 marker 轮廓，其间的空白离任何一段都远；成员给不出就整组退回 bbox；
2. Patch 全家族描真实路径（斜椭圆的 bbox 角不在轮廓上、饼图小扇形的 bbox 里、
   大扇形的那块不算小扇形的内部；只有阴影线的形状内部也算墨迹）；
3. 注释文字的框只是字（不含箭头），带字注释的箭头描真实箭杆、只按描边命中；
   带底框的文字的框含底框。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import math
import re
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_series_geometry.py"
ENTRY = "main"

#: 与 `test_manifest_geometry.py` 同一个读法：本进程 import 不了 pathgeom
MAX_MARKERS = int(
    re.search(
        r"^MAX_MARKERS = (\d+)$",
        (Path(__file__).resolve().parents[1] / "src/tavotto/engine/pathgeom.py").read_text(
            encoding="utf-8"
        ),
        re.M,
    ).group(1)
)

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Rectangle


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    xs = np.array([1.0, 3.0, 5.0, 7.0, 9.0])
    # errorbar_0：只有 marker 的数据点 + 竖误差线 + 帽
    ax.errorbar(xs, np.full(5, 2.0), yerr=0.5, fmt="o", capsize=4, ms=5)
    # errorbar_1：连线的数据线 + 横竖两组误差线
    ax.errorbar(xs, [4.0, 4.6, 4.0, 4.6, 4.0], yerr=0.3, xerr=0.4, fmt="-s", capsize=3)
    # errorbar_2：不画数据线（fmt="none"），只有误差线与帽
    ax.errorbar(xs, np.full(5, 6.0), yerr=0.4, fmt="none", capsize=3)
    # errorbar_3：脚本把帽藏了——图上没有它们的墨迹，轮廓也不该有
    eb = ax.errorbar(xs, np.full(5, 8.0), yerr=0.4, fmt="o", capsize=3)
    for cap in eb[1]:
        cap.set_visible(False)
    ax.set_xlim(0.0, 10.0)
    ax.set_ylim(0.0, 10.0)
    fig.savefig("SeriesFig.pdf")

    fig2, ax2 = plt.subplots(figsize=(4.0, 3.0))
    ax2.stem([1.0, 2.0, 3.0, 4.0], [1.0, 3.0, 2.0, 4.0])
    ax2.set_xlim(0.0, 5.0)
    ax2.set_ylim(-0.5, 5.0)
    fig2.savefig("StemFig.pdf")

    fig3, (a, b) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    # patches_0：转了 45° 的椭圆——bbox 的四个角全是空白
    a.add_patch(Ellipse((5.0, 5.0), 8.0, 2.0, angle=45.0, fc="#2A6F3C"))
    # patches_1：只有阴影线、不填色的矩形——阴影线画在内部
    a.add_patch(Rectangle((1.0, 1.0), 2.0, 2.0, fill=False, hatch="//", ec="k"))
    # texts_0 + texts_0.arrow：带字注释 + 弯箭头
    a.annotate("peak", xy=(8.0, 2.0), xytext=(2.0, 8.0),
               arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0.4"))
    # texts_1 / texts_2：同一段字，一个带底框、一个不带
    a.text(1.0, 9.2, "boxed", bbox=dict(boxstyle="round,pad=0.8", fc="wheat"))
    a.text(6.0, 9.2, "boxed")
    a.set_xlim(0.0, 10.0)
    a.set_ylim(0.0, 10.0)
    # axes_1.patches_0..1：饼图的两个扇形，0–120° 与 120–360°——小扇形的 bbox 伸进大扇形
    b.pie([1.0, 2.0])
    fig3.savefig("ShapeFig.pdf")

    # 误差棒的数据点与散点同一个上限：正好 MAX_MARKERS 颗仍出几何，多一颗整组退回
    # bbox。帽的尺寸默认 0（不画帽），整张图只有这一组，点数远在 TOTAL_BUDGET 之内
    for stem, n in (("EbCapFig", __CAP__), ("EbOverCapFig", __CAP__ + 1)):
        f, axx = plt.subplots(figsize=(4.0, 3.0))
        axx.errorbar(np.arange(n), np.ones(n), yerr=0.1, fmt="o", ms=1)
        f.savefig(f"{stem}.pdf")
"""


@pytest.fixture(scope="module")
def manifests(tmp_path_factory):
    """每张图起一次 worker、取一次 manifest，整个模块共用。"""
    figs = tmp_path_factory.mktemp("series-geometry")
    (figs / SCRIPT_NAME).write_text(LIBRARY.replace("__CAP__", str(MAX_MARKERS)), encoding="utf-8")
    cache: dict[str, dict] = {}

    def get(stem: str) -> dict:
        if stem not in cache:
            w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
            try:
                w.ensure_built()
                resp = w.override(stem, [])
                assert not resp.get("warnings"), resp["warnings"]
                cache[stem] = resp["manifest"]
            finally:
                pool.discard(w)
        return cache[stem]

    return get


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _box(points):
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _dist_mm(man, geom, fx, fy):
    """点到整份 geometry 的最短距离（mm），与前端 `geomDistMm` 同一口径。"""
    sw, sh = man["size_mm"]
    px, py = fx * sw, fy * sh
    best = math.inf
    for p in geom["paths"]:
        pts = [(x * sw, y * sh) for x, y in p["points"]]
        segs = list(zip(pts, pts[1:], strict=False))
        if (p["closed"] or geom["fill"]) and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            t = 0.0 if not L else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
            best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _contains(geom, fx, fy):
    """nonzero 缠绕数，与前端 `geomContains` 同一判据。"""
    if not geom["fill"]:
        return False
    winding = 0
    for p in geom["paths"]:
        pts = p["points"]
        if len(pts) < 3:
            continue
        j = len(pts) - 1
        for i in range(len(pts)):
            (xi, yi), (xj, yj) = pts[i], pts[j]
            left = (xi - xj) * (fy - yj) - (fx - xj) * (yi - yj)
            if yj <= fy:
                if yi > fy and left > 0:
                    winding += 1
            elif yi <= fy and left < 0:
                winding -= 1
            j = i
    return winding != 0


def _data_frac(man, gid, dx, dy, xlim, ylim):
    """数据坐标 → figure 分数（top-origin），axes 框取自 manifest。"""
    x, y, w, h = _el(man, gid)["bbox"]
    return (
        x + w * (dx - xlim[0]) / (xlim[1] - xlim[0]),
        y + h * (1.0 - (dy - ylim[0]) / (ylim[1] - ylim[0])),
    )


def _split(geom):
    """(两点开放线段, 多于两点的开放折线, 闭合轮廓)"""
    seg = [p for p in geom["paths"] if not p["closed"] and len(p["points"]) == 2]
    poly = [p for p in geom["paths"] if not p["closed"] and len(p["points"]) > 2]
    closed = [p for p in geom["paths"] if p["closed"]]
    return seg, poly, closed


XS = [1.0, 3.0, 5.0, 7.0, 9.0]


# ---------------------------------------------------------------------------
# 误差棒 / 茎叶系列
# ---------------------------------------------------------------------------
def test_errorbar_outlines_its_members_not_the_union_box(manifests):
    """五根竖误差线 + 十个帽（两点线段）+ 五颗实心 marker（闭合轮廓），而不是罩住
    整组的 bbox；两根误差线之间的空白离每一段都远于命中容差（1.5 mm）。"""
    man = manifests("SeriesFig")
    el = _el(man, "axes_0.errorbar_0")
    assert el["role"] == "errorbar"
    geom = el["geometry"]
    assert geom["kind"] == "multi_path"
    seg, poly, closed = _split(geom)
    assert (len(seg), len(poly), len(closed)) == (15, 0, 5), [
        len(p["points"]) for p in geom["paths"]
    ]
    # 实心 marker、没有长的开放折线 → 按面积命中
    assert geom["fill"] is True and geom["stroke"] is True
    # 竖误差线正好落在每个数据点的 x 上
    lim = ((0.0, 10.0), (0.0, 10.0))
    vertical = sorted(
        p["points"][0][0] for p in seg if abs(p["points"][0][0] - p["points"][1][0]) < 1e-4
    )
    want = [_data_frac(man, "axes_0", x, 2.0, *lim)[0] for x in XS]
    assert len(vertical) == 5 and vertical == pytest.approx(want, abs=2e-3)
    # 两个数据点正中间：在并集 bbox 里、却不在任何一段墨迹附近
    fx, fy = _data_frac(man, "axes_0", 2.0, 2.0, *lim)
    bx, by, bw, bh = el["bbox"]
    assert bx <= fx <= bx + bw and by <= fy <= by + bh, "那个点在并集 bbox 里"
    assert _dist_mm(man, geom, fx, fy) > 3.0
    assert not _contains(geom, fx, fy)
    # 每个点都落在自己的 bbox 里（bbox 仍是那个并集框，geometry 不替代它）
    for p in geom["paths"]:
        for px, py in p["points"]:
            assert bx - 2e-3 <= px <= bx + bw + 2e-3 and by - 2e-3 <= py <= by + bh + 2e-3


def test_errorbar_with_a_connected_line_has_no_fill_semantics(manifests):
    """数据线连着（`fmt="-s"`）：那条折线不能混进 `fill`——前端把 fill 下的子路径
    一律当面积，折线会被当成一个多边形，其下方的空白全成了它的内部。"""
    geom = _el(manifests("SeriesFig"), "axes_0.errorbar_1")["geometry"]
    seg, poly, _ = _split(geom)
    assert len(poly) == 1, "连线的数据线是一条折线"
    # 横竖两组误差线各 5 根，帽各 10 个
    assert len(seg) == 30
    assert geom["fill"] is False and geom["stroke"] is True


def test_errorbar_without_data_line_has_only_bars_and_caps(manifests):
    geom = _el(manifests("SeriesFig"), "axes_0.errorbar_2")["geometry"]
    seg, poly, closed = _split(geom)
    assert (len(seg), len(poly), len(closed)) == (15, 0, 0)
    assert geom["fill"] is False


def test_hidden_errorbar_caps_are_not_outlined(manifests):
    """脚本藏了帽：五根误差线 + 五颗 marker，没有帽。"""
    geom = _el(manifests("SeriesFig"), "axes_0.errorbar_3")["geometry"]
    seg, poly, closed = _split(geom)
    assert (len(seg), len(poly), len(closed)) == (5, 0, 5)


def test_errorbar_marker_cap_falls_back_to_bbox_as_a_whole(manifests):
    """数据点超过 `MAX_MARKERS` 时那个成员给不出几何——**整组**退回 bbox，不出一份
    缺了数据点的几何（缺的那部分墨迹会既点不中也框不到）。"""
    at_cap = _el(manifests("EbCapFig"), "axes_0.errorbar_0")
    over = _el(manifests("EbOverCapFig"), "axes_0.errorbar_0")
    _, _, closed = _split(at_cap["geometry"])
    assert len(closed) == MAX_MARKERS
    assert "geometry" not in over and over["bbox"]


def test_stem_series_outlines_stems_and_markers(manifests):
    """茎叶系列：四根茎（两点线段）+ 四颗 marker；零线不是系列的一部分。"""
    man = manifests("StemFig")
    geom = _el(man, "axes_0.stemseries_0")["geometry"]
    seg, poly, closed = _split(geom)
    assert (len(seg), len(poly), len(closed)) == (4, 0, 4)
    lim = ((0.0, 5.0), (-0.5, 5.0))
    # 两根茎之间、高处的空白：在并集 bbox 里，离墨迹远
    fx, fy = _data_frac(man, "axes_0", 1.5, 3.5, *lim)
    assert _dist_mm(man, geom, fx, fy) > 3.0


# ---------------------------------------------------------------------------
# Patch 全家族
# ---------------------------------------------------------------------------
def test_rotated_ellipse_outline_leaves_its_bbox_corners_blank(manifests):
    man = manifests("ShapeFig")
    el = _el(man, "axes_0.patches_0")
    geom = el["geometry"]
    assert geom["kind"] == "path" and geom["paths"][0]["closed"]
    assert geom["fill"] is True
    x, y, w, h = el["bbox"]
    # 斜 45° 的椭圆：左上、右下两个角离轮廓远，也不在内部
    for fx, fy in ((x + 0.1 * w, y + 0.1 * h), (x + 0.9 * w, y + 0.9 * h)):
        assert not _contains(geom, fx, fy)
        assert _dist_mm(man, geom, fx, fy) > 3.0
    # 中心在内部
    assert _contains(geom, x + w / 2, y + h / 2)


def test_hatch_only_patch_counts_its_interior_as_ink(manifests):
    """`fill=False, hatch="//"`：阴影线画在内部，点进去就是点在它的墨迹上。"""
    el = _el(manifests("ShapeFig"), "axes_0.patches_1")
    x, y, w, h = el["bbox"]
    assert el["geometry"]["fill"] is True
    assert _contains(el["geometry"], x + w / 2, y + h / 2)


def test_pie_wedges_are_their_own_shapes_not_overlapping_boxes(manifests):
    """饼图：大扇形（120–360°）的一块落在小扇形的 bbox 里，从前按 bbox 命中时点那儿
    选中的是 bbox 更小的小扇形。现在按真实扇形：那一点只在大扇形内部。"""
    man = manifests("ShapeFig")
    small, big = (_el(man, f"axes_1.patches_{k}") for k in range(2))
    assert all(w["geometry"]["paths"][0]["closed"] and w["geometry"]["fill"] for w in (small, big))
    x, y, w, h = small["bbox"]
    probes = [
        (x + w * i / 20, y + h * j / 20)
        for i in range(1, 20)
        for j in range(1, 20)
        if _contains(big["geometry"], x + w * i / 20, y + h * j / 20)
    ]
    assert len(probes) > 20, "应当找得到落在小扇形 bbox 里、却属于大扇形的点"
    for fx, fy in probes:
        assert not _contains(small["geometry"], fx, fy)


# ---------------------------------------------------------------------------
# 注释文字、带字注释的箭头、带底框的文字
# ---------------------------------------------------------------------------
def test_annotation_text_box_is_the_text_not_the_arrow(manifests):
    """`annotate("peak", xy=…, xytext=…)`：文字的框只是字——箭头尖在它外面很远。"""
    man = manifests("ShapeFig")
    x, y, w, h = _el(man, "axes_0.texts_0")["bbox"]
    tip = _data_frac(man, "axes_0", 8.0, 2.0, (0.0, 10.0), (0.0, 10.0))
    assert not (x <= tip[0] <= x + w and y <= tip[1] <= y + h)
    sw, sh = man["size_mm"]
    assert w * sw < 15.0 and h * sh < 8.0, "「peak」四个字的框不该有几厘米大"


def test_annotation_arrow_with_text_traces_its_curved_shaft(manifests):
    """带字注释的箭头不出端点（端点归注释管），从前只有罩住弯箭杆的 bbox。现在描
    真实箭杆 + 箭头、只按描边命中（开放的弯箭杆按面积算会把弧与弦之间的空白也算进来）。"""
    man = manifests("ShapeFig")
    el = _el(man, "axes_0.texts_0.arrow")
    assert "arrow_endpoints" not in el
    geom = el["geometry"]
    assert geom["fill"] is False and geom["stroke"] is True
    # 弦的中点：rad=0.4 的弧离它有一段距离——那是 bbox 会罩住的空白
    a = _data_frac(man, "axes_0", 2.0, 8.0, (0.0, 10.0), (0.0, 10.0))
    b = _data_frac(man, "axes_0", 8.0, 2.0, (0.0, 10.0), (0.0, 10.0))
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    assert _dist_mm(man, geom, *mid) > 3.0
    # 箭头尖附近（shrinkB 默认 2pt）有墨迹
    assert _dist_mm(man, geom, *b) < 1.5


def test_boxed_text_box_includes_its_background(manifests):
    """`bbox=dict(boxstyle="round,pad=0.8")`：底框在字外四周各多 0.8 个字号，选中框
    与命中都要含它。对照同一段不带底框的字。"""
    man = manifests("ShapeFig")
    boxed = _el(man, "axes_0.texts_1")["bbox"]
    plain = _el(man, "axes_0.texts_2")["bbox"]
    sw, sh = man["size_mm"]
    # pad = 0.8 × 10pt = 8pt ≈ 2.8mm，左右各一份
    assert (boxed[2] - plain[2]) * sw > 4.0
    assert (boxed[3] - plain[3]) * sh > 4.0
