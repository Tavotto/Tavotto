"""在 **worker 解释器**里跑的探针：复杂度分析器对一组图分别裁出了什么。

本进程（Flask 侧的 `.venv`）没有 matplotlib，而分析器吃的就是 matplotlib 的
artist 图——所以判据留在 `tests/test_preview_complexity.py`，这里只负责**如实
报事实**：每张图的逐族账目、裁出来的 mode、名单里有谁，以及三条「机制真的
成立」的读数：

* `rasterized_before` / `rasterized_after`——分析器有没有偷偷改 artist；
* `quadmesh_paths_built`——`QuadMesh.get_paths()` 有没有被谁调过（调了就等于
  在热路径上现建 22 万个 `Path`，那是 render 本身都不付的钱）；
* `twice_identical`——同一张图连analyze 两次，结论逐字段相同。

用法：

    python tests/support/preview_complexity_probe.py               # 全部用例
    python tests/support/preview_complexity_probe.py --bench       # 加开销测量
    python tests/support/preview_complexity_probe.py --issue181-n 470 --bench
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块平铺 import。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))
sys.path.insert(0, os.path.join(_REPO, "tests", "fixtures", "large_figures"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.artist import Artist  # noqa: E402
from matplotlib.collections import PolyCollection, QuadMesh  # noqa: E402

import figcapture  # noqa: E402
import figsession  # noqa: E402
import preview_complexity as pc  # noqa: E402

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），而这个
# 探针把**带中文的 JSON** 打给父进程——第一次 print 就 UnicodeEncodeError，
# 退出码变成 1，于是所有用例只看得见「returned non-zero exit status 1」。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 用例里的 mesh 边长。40 000 个 cell/格，是 `MESH_CELL_BUDGET` 的两倍——
#: 够越线，又比基线那个 470 快一个数量级（用例不该为了越线跑十几秒）。
DEFAULT_ISSUE181_N = 200


class BlindDoodad(Artist):
    """`get_visible()` 会抛的 artist。**未知 → 按会画算**这条方向选择要有人钉。

    真实来源是第三方库里包装过 `visible` 属性的 artist；这里用注入的方式把它
    摆出来，因为 matplotlib 自己的 artist 不会在这上面失败。
    """

    def get_visible(self):
        raise RuntimeError("这个 artist 答不出自己可不可见")

    def draw(self, renderer):
        return None


class Doodad(Artist):
    """完全不在 matplotlib 体系里的自定义 Artist——分析器不许被它绊倒，
    也不许把它静默当成 0 就当图很小。与 `test_artist_families.py` 里那个同名
    的是同一类东西（那边测语义登记，这边测成本分析）。"""

    def draw(self, renderer):
        return None


class MyMesh(QuadMesh):
    """用户自己继承出来的网格。**family 抽象的全部意义就是它不用我们改代码**
    ——按类名分派的实现会把它判成 unknown，于是 #181 那张图换个子类就漏了。"""


# --------------------------------- 用例 -------------------------------------
def _fig_normal_line():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    t = np.linspace(0, 10, 400)
    ax.plot(t, np.exp(-t / 4), lw=1.2, label="decay")
    ax.plot(t, np.exp(-t / 9), lw=1.2, ls="--", label="slow")
    ax.set_title("normal")
    ax.set_xlabel("t")
    ax.legend()
    return fig


def _mesh(n: int, cls=None):
    rng = np.random.default_rng(181)
    edges = np.linspace(0, 1, n + 1)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    mesh = ax.pcolormesh(edges, edges, rng.standard_normal((n, n)), shading="flat")
    if cls is not None:
        # 同样的数据换一个用户子类：`ax.pcolormesh` 不接受 cls，所以直接建一个
        # 同族对象挂上去——测的是 family 分派，不是 pyplot 的参数表。
        mesh.remove()
        sub = cls(mesh.get_coordinates(), array=rng.standard_normal(n * n))
        ax.add_collection(sub)
    return fig


def _fig_small_scatter():
    rng = np.random.default_rng(3)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.scatter(rng.standard_normal(500), rng.standard_normal(500), s=6)
    return fig


def _fig_huge_scatter():
    rng = np.random.default_rng(4)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.scatter(rng.standard_normal(120_000), rng.standard_normal(120_000), s=2)
    return fig


def _fig_large_polycollection():
    """30 000 个四边形的 `PolyCollection`：`hexbin` / `stackplot` /
    `violinplot` 都落在这一族，这里直接用族的基类建，避免测成某个 pyplot API。"""
    rng = np.random.default_rng(5)
    xy = rng.random((30_000, 2))
    verts = [[(x, y), (x + 0.002, y), (x + 0.002, y + 0.002), (x, y + 0.002)] for x, y in xy]
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.add_collection(PolyCollection(verts, facecolors="#4477aa"))
    return fig


def _fig_small_polycollection():
    x = np.linspace(0, 1, 200)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.fill_between(x, 0.0, np.sin(x * 10))
    return fig


def _fig_large_contour():
    rng = np.random.default_rng(6)
    z = np.cumsum(np.cumsum(rng.standard_normal((300, 300)), 0), 1)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.contourf(z, levels=40)
    return fig


def _fig_small_contour():
    rng = np.random.default_rng(7)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.contour(rng.standard_normal((40, 40)), levels=8)
    return fig


def _fig_custom_artist():
    """认不出来的轻量 Artist：不许崩，也不许因为它把裁决改掉。"""
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1], [0, 1])
    ax.add_artist(Doodad())
    return fig


def _fig_blind_artist():
    """连「可不可见」都问不出来的 artist：**照样要记账**，且不许崩。"""
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1], [0, 1])
    ax.add_artist(BlindDoodad())
    return fig


def _triangulation(n: int):
    from matplotlib.tri import Triangulation

    rng = np.random.default_rng(8)
    return Triangulation(rng.random(n * n), rng.random(n * n)), rng.random(n * n)


def _fig_trimesh_built():
    """paths **已经建好**的 `TriMesh`：量得出来的时候就按 family 正常定价。

    **这里由探针自己把 paths 建出来**（`get_paths()`），不指望 matplotlib 顺手
    替我们建——那件事是**版本相关**的，第一版就栽在这上面：本机 3.10.8 上
    `ax.tripcolor` 走 `add_collection`（autolim 默认开），`get_datalim()` 会把
    paths 建好；而 CI 的 **3.11.1 不会**，于是这一格在本机绿、在 CI 红。

    被测的是分析器**面对两种状态各自怎么办**，所以状态该由探针确定地摆出来，
    不该借一条会随版本变的副作用。分析器对真实图里的 gouraud TriMesh 是哪种
    状态**不做断言**——两条路它都安全（正常定价，或标成量不出来）。
    """
    tri, z = _triangulation(120)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    mesh = ax.tripcolor(tri, z, shading="gouraud")
    mesh.get_paths()  # 明确把状态摆成「已建好」，不依赖版本相关的副作用
    return fig


def _fig_trimesh_unbuilt():
    """paths **还没建**的 `TriMesh`：`autolim=False` 时没人替它建过。

    `pcolormesh` 用的就是 `add_collection(..., autolim=False)`，所以这不是
    捏出来的形状。这一条是 `_materialised_paths` 那道守卫的**唯一现场**：
    分析器要是顺手 `get_paths()` 一下，热路径上就凭空多出 8 万个 `Path`
    （实测 75 ms），而这张图的 draw 走 `draw_gouraud_triangles`、**根本不经过
    paths**——那笔钱连 render 自己都不付。
    """
    from matplotlib.collections import TriMesh

    tri, z = _triangulation(200)
    mesh = TriMesh(tri)
    mesh.set_array(z)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.add_collection(mesh, autolim=False)
    return fig


def _fig_many_medium_meshes():
    """四格各 15 000 个 cell：**逐族预算一个都不越**，合计 60 000 越图级预算。
    没有第二轮裁决的话这张图会被判成 vector，然后把 6 万个节点交给 DOM。"""
    rng = np.random.default_rng(9)
    n = 123  # 123² = 15 129
    edges = np.linspace(0, 1, n + 1)
    fig, axes = plt.subplots(2, 2, figsize=(6.0, 4.5))
    for ax in axes.flat:
        ax.pcolormesh(edges, edges, rng.standard_normal((n, n)), shading="flat")
    return fig


def _fig_imshow_colorbar():
    rng = np.random.default_rng(10)
    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    im = ax.imshow(rng.random((64, 64)), cmap="magma")
    fig.colorbar(im, ax=ax, label="signal")
    return fig


def _fig_issue181(n: int):
    import issue_181_large_pcolormesh as fx

    return fx.build(n)


def _cases(issue181_n: int) -> dict:
    return {
        "normal_line": _fig_normal_line,
        "small_pcolormesh": lambda: _mesh(24),
        "large_pcolormesh": lambda: _mesh(issue181_n),
        "user_subclass_mesh": lambda: _mesh(issue181_n, cls=MyMesh),
        "small_scatter": _fig_small_scatter,
        "huge_scatter": _fig_huge_scatter,
        "small_polycollection": _fig_small_polycollection,
        "large_polycollection": _fig_large_polycollection,
        "small_contour": _fig_small_contour,
        "large_contour": _fig_large_contour,
        "custom_artist": _fig_custom_artist,
        "blind_artist": _fig_blind_artist,
        "trimesh_built": _fig_trimesh_built,
        "trimesh_unbuilt": _fig_trimesh_unbuilt,
        "many_medium_meshes": _fig_many_medium_meshes,
        "imshow_colorbar": _fig_imshow_colorbar,
        # 节点预算的两侧。`Line2D` 不可 rasterize，所以「收不动」那条路只有
        # 它走得出来——而这正是 #181 的残余缺口（primitive 与字节两侧都不响）。
        "many_lines_under_node_budget": lambda: _fig_many_lines(10_000),
        "many_lines_over_node_budget": lambda: _fig_many_lines(40_000),
        "issue_181": lambda: _fig_issue181(issue181_n),
    }


def _fig_many_lines(n: int):
    """`n` 条独立的 `Line2D`，四格分摊——用户写 `for … : ax.plot(…)` 的形状。

    每条只有 3 个点：**顶点预算与字节都不会响**（4 万条也才 9.33 MB，在
    16 MiB 硬闸之下），响的只能是节点预算。
    """
    rng = np.random.default_rng(181)
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.0))
    x = np.linspace(0, 1, 3)
    for ax in axes.ravel():
        for _ in range(n // 4):
            ax.plot(x, rng.standard_normal(3) * 0.01, lw=0.4)
    return fig


# ------------------------------ 报事实 ---------------------------------------
def _plan_json(plan) -> dict:
    return {
        "mode": plan.mode,
        "reason": plan.reason,
        "estimated_primitives": plan.estimated_primitives,
        "estimated_vertices": plan.estimated_vertices,
        "estimated_nodes": plan.estimated_nodes,
        "vector_primitives": plan.vector_primitives,
        "vector_vertices": plan.vector_vertices,
        "vector_nodes": plan.vector_nodes,
        "rasterized_artist_count": plan.rasterized_artist_count,
        "rasterized_families": sorted({c.family for c in plan.costs if c.should_rasterize}),
        "families": sorted({c.family for c in plan.costs}),
        "unknown_families": sorted({c.family for c in plan.unknown}),
        "detail": plan.detail,
        "costs": [
            {
                "family": c.family,
                "primitive_count": c.primitive_count,
                "node_count": c.node_count,
                "vertex_count": c.vertex_count,
                "vertex_count_exact": c.vertex_count_exact,
                "rasterizable": c.rasterizable,
                "should_rasterize": c.should_rasterize,
                "type": type(c.artist).__name__,
            }
            for c in plan.costs
        ],
    }


def _all_artists(fig):
    return list(pc._iter_artists(fig, frozenset()))  # noqa: SLF001


def _run_case(name: str, build) -> dict:
    fig = build()
    artists = _all_artists(fig)
    before = [bool(a.get_rasterized()) for a in artists]
    t0 = time.perf_counter()
    plan = pc.analyze_preview_complexity(fig)
    analyze_ms = (time.perf_counter() - t0) * 1000.0
    after = [bool(a.get_rasterized()) for a in artists]

    # 同一张图再分析一次：结论必须逐字段相同（确定性）
    again = _plan_json(pc.analyze_preview_complexity(fig))
    out = _plan_json(plan)

    row = dict(out)
    row["case"] = name
    row["analyze_ms"] = round(analyze_ms, 4)
    row["rasterized_before"] = before
    row["rasterized_after"] = after
    row["twice_identical"] = again == out
    # `QuadMesh` 的 paths 一旦被谁建过就不再是 None——这是「分析器没有替它
    # 建 22 万个 Path」的直接读数，不是「看起来挺快」。
    row["quadmesh_paths_built"] = [
        getattr(a, "_paths", None) is not None for a in artists if isinstance(a, QuadMesh)
    ]
    plt.close(fig)
    return row


def _run_state_case(issue181_n: int) -> dict:
    """走 `plan_for_state`：色条轴上的内部件（`cb.solids` / `cb.dividers`）
    必须不在账上——它们每次 `_draw_all()` 都被删掉重建。"""
    fig = _fig_imshow_colorbar()
    session = figsession.LiveFigureSession(os.environ.get("TMPDIR", "/tmp"))
    session.add_figure("CbarProbe", fig, figcapture.SOURCE_SAVEFIG)
    session.instrument_all()
    state = session.states["CbarProbe"]
    with_cbar = pc.analyze_preview_complexity(fig)
    without = pc.plan_for_state(state)
    plt.close(fig)
    return {
        "case": "plan_for_state_skips_colorbar_axes",
        "colorbar_axes_count": len(state.colorbar_axes),
        "with_colorbar_internals": _plan_json(with_cbar),
        "skipping_colorbar_axes": _plan_json(without),
    }


# --------------------- 对拍：模型 vs 后端真的吐出来的东西 ---------------------
def _bare_axes():
    """对拍专用的画布：**坐标轴整个关掉**。

    第一版没关，差分当场被污染：scatter 那条量出来的 `<use>` 差是 476 而不是
    500——因为两张图的刻度值不同，而**刻度文字在 SVG 里也是 `<use>`**。
    A/B 差分只有在「两侧除了这一个 artist 之外完全相同」时才是对照，否则它
    就是两个样本。
    """
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.set_axis_off()
    return fig, ax


def _fig_blank():
    return _bare_axes()[0]


def _cross_mesh(n: int, rasterized: bool = False):
    rng = np.random.default_rng(181)
    edges = np.linspace(0, 1, n + 1)
    fig, ax = _bare_axes()
    mesh = ax.pcolormesh(edges, edges, rng.standard_normal((n, n)), shading="flat")
    mesh.set_rasterized(rasterized)
    return fig


def _cross_scatter(n: int, **kw):
    """三种散点**落在后端三条不同的分支上**，而它们在 pyplot 层只差一个参数。

    这正是「不按 API 特判、按 artist 实况定价」要证的东西：同一个
    `PathCollection`，`s=标量` 共享几何、`s=数组` 逐个内联（顶点数差 500 倍）。
    """
    rng = np.random.default_rng(3)
    fig, ax = _bare_axes()
    ax.scatter(rng.standard_normal(n), rng.standard_normal(n), **kw)
    return fig


def _cross_contour():
    rng = np.random.default_rng(7)
    fig, ax = _bare_axes()
    ax.contour(rng.standard_normal((40, 40)), levels=8)
    return fig


def _fig_polys(n: int):
    rng = np.random.default_rng(5)
    xy = rng.random((n, 2))
    verts = [[(x, y), (x + 0.01, y), (x + 0.01, y + 0.01), (x, y + 0.01)] for x, y in xy]
    fig, ax = _bare_axes()
    ax.add_collection(PolyCollection(verts, facecolors="#4477aa"))
    return fig


def _fig_polys_head_heavy(n_small: int, n_big: int, big_verts: int):
    """**头重**的异构 collection：重几何全排在最前面。

    与 `_fig_polys_tail_heavy` 成对。少了这一张，「抽样要跨全序列」这条判据
    只钉住了一侧——把抽样从「取前 N 条」换成「取后 N 条」照样绿，而那是同一个
    缺陷换了个方向（[[gate-pinned-on-one-side-only]]）。
    """
    rng = np.random.default_rng(5)
    t = np.linspace(0, 2 * np.pi, big_verts, endpoint=False)
    verts = []
    for k in range(n_big):
        r = 0.2 + 0.02 * k
        verts.append(list(zip(0.5 + r * np.cos(t), 0.5 + r * np.sin(t))))
    xy = rng.random((n_small, 2))
    verts += [[(x, y), (x + 0.005, y), (x + 0.005, y + 0.005), (x, y + 0.005)] for x, y in xy]
    fig, ax = _bare_axes()
    ax.add_collection(PolyCollection(verts, facecolors="#aa7744"))
    return fig


def _fig_polys_tail_heavy(n_small: int, n_big: int, big_verts: int):
    """**异构** collection：前面一堆四边形，后面少数几个顶点极多的多边形。

    前缀抽样在这个形状上必然偏低——重几何全排在取样窗口之外。真实世界里
    `PolyCollection` 先小后大很常见（等值面、分箱统计、地理边界）。
    """
    rng = np.random.default_rng(5)
    xy = rng.random((n_small, 2))
    verts = [[(x, y), (x + 0.005, y), (x + 0.005, y + 0.005), (x, y + 0.005)] for x, y in xy]
    t = np.linspace(0, 2 * np.pi, big_verts, endpoint=False)
    for k in range(n_big):
        r = 0.2 + 0.02 * k
        verts.append(list(zip(0.5 + r * np.cos(t), 0.5 + r * np.sin(t))))
    fig, ax = _bare_axes()
    ax.add_collection(PolyCollection(verts, facecolors="#4477aa"))
    return fig


def _fig_hidden_mesh(n: int):
    """一块 `visible=False` 的大网格：**后端一个节点都不写**。

    按全价记进账的话，一块藏起来的图层就能凭空逼出一次 hybrid——用户看到的是
    「明明没显示那层，画面却糊了」。
    """
    fig = _cross_mesh(n)
    fig.axes[0].collections[0].set_visible(False)
    return fig


def _fig_hidden_axes(n: int):
    """整个 axes 隐掉：它的孩子一起不画。"""
    fig = _cross_mesh(n)
    fig.axes[0].set_visible(False)
    return fig


def _fig_linecoll(n: int):
    from matplotlib.collections import LineCollection

    xs = np.linspace(0, 1, n)
    segs = [[(x, 0.0), (x, 1.0)] for x in xs]
    fig, ax = _bare_axes()
    ax.add_collection(LineCollection(segs, colors="#333333"))
    return fig


#: 一条 SVG 路径指令吃掉几个 matplotlib 顶点。
#:
#: **`C` 是 3 不是 1**：一段三次贝塞尔在 `Path.vertices` 里占两个控制点 + 一个
#: 终点。第一版按「指令数 == 顶点数」算，散点那条对拍就恒等成立——而恒等成立
#: 的判据挡不住任何东西（变异实测：把 `_shares_geometry` 改成恒 False，
#: 顶点估值从 26 变成 13 000，整套用例全绿）。
_VERTICES_PER_CMD = {"M": 1, "L": 1, "C": 3, "Q": 2, "z": 1}


def _svg_tag_counts(fig) -> dict:
    """这张图的预览 SVG 里有多少个绘制节点、后端写进 `d="…"` 的顶点有多少个。

    顶点数从**后端自己写出来的指令**反推（`M`/`L`/`C`/`Q`/`z` 各吃几个顶点），
    与模型的算法毫无关系——对拍要的正是这个：两侧同源就等于自己验自己。
    """
    import io
    import re

    buf = io.StringIO()
    fig.savefig(buf, format="svg")
    svg = buf.getvalue()
    ds = re.findall(r'\bd="([^"]*)"', svg)
    joined = "".join(ds)
    return {
        "path": svg.count("<path"),
        "use": svg.count("<use"),
        "image": svg.count("<image"),
        # `<g ` 带空格：`<glyph`/`<gradient` 之类不能算进来
        "g": svg.count("<g "),
        "vertices": sum(joined.count(c) * k for c, k in _VERTICES_PER_CMD.items()),
        "unknown_cmds": sorted({c for c in joined if c.isalpha()} - set(_VERTICES_PER_CMD)),
        "bytes": len(svg.encode("utf-8")),
    }


#: 拿去对拍的图。**必须小**——每条要 savefig 两次；而对拍验的是**机制**
#: （模型算的那个 N 与后端画的次数是不是同一个 N），不是规模。
_CROSSCHECK = {
    # 名字 -> (带这个 artist 的图, 除它之外完全相同的对照图)
    "mesh": (lambda: _cross_mesh(24), _fig_blank),
    # 单形状快路（`draw_markers`）：几何进 defs 写一遍
    "scatter_uniform": (lambda: _cross_scatter(500, s=6), _fig_blank),
    # 逐点大小 → 掉出快路，且取舍式把 uses 压成 1 → **每个 marker 各自内联**
    "scatter_sized": (
        lambda: _cross_scatter(500, s=np.random.default_rng(11).uniform(5, 40, 500)),
        _fig_blank,
    ),
    # **简单 marker**（`_` 只有 2 个顶点）：`draw_path_collection` 的取舍式在这里
    # 判「逐个内联」，而单形状快路先一步把它接走了 → 仍然共享。这一格是快路
    # 本身的**唯一现场**——`scatter_uniform` 那个圆形 marker 走取舍式也能得出
    # 同样的答案，所以它证明不了快路存在的必要性（变异实测：把快路关掉，
    # 只有这一格会红）。
    "scatter_simple_marker": (lambda: _cross_scatter(500, s=20, marker="_"), _fig_blank),
    # 逐点颜色 → 也掉出快路，但取舍式仍然判共享
    "scatter_mapped": (
        lambda: _cross_scatter(500, s=6, c=np.random.default_rng(12).random(500)),
        _fig_blank,
    ),
    # 已经是位图的 artist：SVG 里是 **1 个 `<image>`**，不是 576 个 `<path>`
    "mesh_prerasterized": (lambda: _cross_mesh(24, rasterized=True), _fig_blank),
    "polycollection": (lambda: _fig_polys(300), _fig_blank),
    # 异构、且 path 数**超过取样上限**：前缀抽样在这一格上会把顶点数估到零头
    "poly_tail_heavy": (lambda: _fig_polys_tail_heavy(4200, 6, 2000), _fig_blank),
    # 成对的另一侧：重几何全在最前面。少了它，「取后 N 条」的实现照样绿
    "poly_head_heavy": (lambda: _fig_polys_head_heavy(4200, 6, 2000), _fig_blank),
    # 不可见的那两格：后端一个节点都不写，模型也必须报 0
    "mesh_hidden": (lambda: _fig_hidden_mesh(24), _fig_blank),
    "mesh_hidden_axes": (lambda: _fig_hidden_axes(24), _fig_blank),
    "linecoll": (lambda: _fig_linecoll(400), _fig_blank),
    "contour": (_cross_contour, _fig_blank),
    # `line` 这一族**原本被对拍豁免掉了**（`skip` 里有 FAMILY_LINE），而
    # #181 的残余缺口恰恰在它身上：模型记 1 个 primitive 是对的（SVG 里确实
    # 只有一个 `<path>`），但 DOM 里是 2 个元素——matplotlib 还包一个 `<g>`。
    # 豁免掉的那一族正好是出问题的那一族，所以这一格补上。
    "lines": (lambda: _fig_lines(400), _fig_blank),
}


def _vertex_sampling() -> dict:
    """同一个异构 collection 上，三种数法各得多少个顶点。

    两张成对的图（重几何在尾 / 在头）× 四个数：`exact` 是逐条数出来的真值，
    `stride` 是模型现在用的等距抽样，`prefix` / `suffix` 是两种**只看一端**的
    数法。摆成这个形状，「抽样必须跨全序列」才是两侧都钉住的——只有尾重那张
    的话，把实现换成「取后 N 条」照样全绿。

    数是**这里现算**的，不调模型的私有函数——两侧同源就等于自己验自己。
    """
    out = {"sample_cap": pc._VERTEX_SAMPLE_PATHS}
    for name, build in (
        ("tail_heavy", lambda: _fig_polys_tail_heavy(4200, 6, 2000)),
        ("head_heavy", lambda: _fig_polys_head_heavy(4200, 6, 2000)),
    ):
        fig = build()
        paths = fig.axes[0].collections[0].get_paths()
        n = len(paths)
        cap = pc._VERTEX_SAMPLE_PATHS
        exact = sum(len(q.vertices) for q in paths)
        head = paths[:cap]
        tail = paths[-cap:]
        cost = next(
            c for c in pc.analyze_preview_complexity(fig).costs if c.family == pc.FAMILY_POLY
        )
        plt.close(fig)
        out[name] = {
            "paths": n,
            "exact": exact,
            # 两种**只看一端**的数法，各自现算——两侧都得有人钉着
            "prefix": round(sum(len(q.vertices) for q in head) / len(head) * n),
            "suffix": round(sum(len(q.vertices) for q in tail) / len(tail) * n),
            "stride": cost.vertex_count,
            "exact_flag": cost.vertex_count_exact,
        }
    return out


def _fig_lines(n: int):
    """`n` 条独立的 `Line2D`——每条都是一次 `ax.plot()`，与用户写法一致。"""
    fig, ax = _bare_axes()
    rng = np.random.default_rng(7)
    x = np.linspace(0, 1, 3)
    for _ in range(n):
        ax.plot(x, rng.standard_normal(3) * 0.01, lw=0.4)
    return fig


def _crosscheck() -> list[dict]:
    """模型说的 `primitive_count` / `vertex_count`，与**后端真的吐出来的**节点
    数、坐标对数对一次。

    做法是 A/B 差分：同一张图，一张带这个 artist、一张不带，两侧的 SVG 节点数
    相减 = 这个 artist 自己摊出来的量。整图直接比会被坐标轴、刻度、文字字形
    （它们在 SVG 里也是 `<use>`）淹掉。
    """
    rows = []
    for name, (build_with, build_without) in _CROSSCHECK.items():
        fig_with = build_with()
        # 只算数据层那几笔（对照图里的 axes patch 之类两侧都有，差分自然抵消）。
        # **`line` 只在它自己那一格里算**：别的格子的对照图里也有普通曲线，
        # 把它算进去会污染差分——而 `lines` 那一格的对照图是空的。
        skip = {pc.FAMILY_UNKNOWN, pc.FAMILY_UNMEASURED}
        if name != "lines":
            skip.add(pc.FAMILY_LINE)
        costs = [c for c in pc.analyze_preview_complexity(fig_with).costs if c.family not in skip]
        model_primitives = sum(c.primitive_count for c in costs)
        model_vertices = sum(c.vertex_count for c in costs)
        model_nodes = sum(c.node_count for c in costs)
        with_counts = _svg_tag_counts(fig_with)
        plt.close(fig_with)
        fig_without = build_without()
        without_counts = _svg_tag_counts(fig_without)
        plt.close(fig_without)
        rows.append(
            {
                "case": name,
                "model_primitives": model_primitives,
                "model_vertices": model_vertices,
                "model_nodes": model_nodes,
                "svg_delta_path": with_counts["path"] - without_counts["path"],
                "svg_delta_use": with_counts["use"] - without_counts["use"],
                "svg_delta_image": with_counts["image"] - without_counts["image"],
                "svg_delta_g": with_counts["g"] - without_counts["g"],
                "svg_delta_vertices": with_counts["vertices"] - without_counts["vertices"],
                "svg_delta_bytes": with_counts["bytes"] - without_counts["bytes"],
                # 出现了换算表里没有的指令 = 上面那个数不可信，别让它静默通过
                "unknown_cmds": sorted(
                    set(with_counts["unknown_cmds"]) | set(without_counts["unknown_cmds"])
                ),
            }
        )
    return rows


def _bench(issue181_n: int, repeat: int) -> list[dict]:
    """开销：普通科研图 vs #181 fixture，**与它要替代的那一步比**。

    分子是 `analyze_preview_complexity()`，分母是 `savefig(svg)` ——后者正是
    complexity-aware hybrid 要省掉的那一段（#181 上实测 11 789 ms）。
    """
    import io

    rows = []
    for name, build in (
        ("normal_figure", _fig_normal_line),
        ("issue_181", lambda: _fig_issue181(issue181_n)),
    ):
        fig = build()
        samples = []
        for _ in range(repeat):
            t0 = time.perf_counter()
            pc.analyze_preview_complexity(fig)
            samples.append((time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        buf = io.StringIO()
        fig.savefig(buf, format="svg")
        savefig_ms = (time.perf_counter() - t0) * 1000.0
        svg_bytes = len(buf.getvalue().encode("utf-8"))
        samples.sort()
        rows.append(
            {
                "case": name,
                "n": issue181_n if name == "issue_181" else None,
                "analyze_ms_median": round(samples[len(samples) // 2], 4),
                "analyze_ms_min": round(samples[0], 4),
                "analyze_ms_max": round(samples[-1], 4),
                "savefig_svg_ms": round(savefig_ms, 1),
                "svg_bytes": svg_bytes,
                "repeat": repeat,
            }
        )
        plt.close(fig)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--issue181-n", type=int, default=DEFAULT_ISSUE181_N)
    ap.add_argument("--bench", action="store_true", help="加测分析器开销（会跑 savefig）")
    ap.add_argument("--bench-repeat", type=int, default=5)
    args = ap.parse_args(argv)

    payload = {
        "matplotlib": matplotlib.__version__,
        "issue181_n": args.issue181_n,
        "budgets": {
            "MESH_CELL_BUDGET": pc.previewbudget.MESH_CELL_BUDGET,
            "SCATTER_INSTANCE_BUDGET": pc.previewbudget.SCATTER_INSTANCE_BUDGET,
            "COLLECTION_VERTEX_BUDGET": pc.previewbudget.COLLECTION_VERTEX_BUDGET,
            "TOTAL_VECTOR_PRIMITIVE_BUDGET": pc.previewbudget.TOTAL_VECTOR_PRIMITIVE_BUDGET,
        },
        "cases": {n: _run_case(n, b) for n, b in _cases(args.issue181_n).items()},
        "state_case": _run_state_case(args.issue181_n),
        "crosscheck": _crosscheck(),
        "vertex_sampling": _vertex_sampling(),
    }
    if args.bench:
        payload["bench"] = _bench(args.issue181_n, args.bench_repeat)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
