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

#: 用例里的 mesh 边长。40 000 个 cell/格，是 `MESH_CELL_BUDGET` 的两倍——
#: 够越线，又比基线那个 470 快一个数量级（用例不该为了越线跑十几秒）。
DEFAULT_ISSUE181_N = 200


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


def _triangulation(n: int):
    from matplotlib.tri import Triangulation

    rng = np.random.default_rng(8)
    return Triangulation(rng.random(n * n), rng.random(n * n)), rng.random(n * n)


def _fig_trimesh_gouraud():
    """gouraud `tripcolor` → `TriMesh`。

    这一条**是**量得出来的：`ax.tripcolor` 走 `add_collection`（autolim 默认
    开），`get_datalim()` 顺手就把 paths 建好了。它在这里是为了钉住「量得出来
    的时候就按 family 正常定价」，与下面那条是一对。
    """
    tri, z = _triangulation(120)
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.tripcolor(tri, z, shading="gouraud")
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
        "trimesh_gouraud": _fig_trimesh_gouraud,
        "trimesh_unbuilt": _fig_trimesh_unbuilt,
        "many_medium_meshes": _fig_many_medium_meshes,
        "imshow_colorbar": _fig_imshow_colorbar,
        "issue_181": lambda: _fig_issue181(issue181_n),
    }


# ------------------------------ 报事实 ---------------------------------------
def _plan_json(plan) -> dict:
    return {
        "mode": plan.mode,
        "reason": plan.reason,
        "estimated_primitives": plan.estimated_primitives,
        "estimated_vertices": plan.estimated_vertices,
        "vector_primitives": plan.vector_primitives,
        "vector_vertices": plan.vector_vertices,
        "rasterized_artist_count": plan.rasterized_artist_count,
        "rasterized_families": sorted({c.family for c in plan.costs if c.should_rasterize}),
        "families": sorted({c.family for c in plan.costs}),
        "unknown_families": sorted({c.family for c in plan.unknown}),
        "detail": plan.detail,
        "costs": [
            {
                "family": c.family,
                "primitive_count": c.primitive_count,
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


def _cross_mesh(n: int):
    rng = np.random.default_rng(181)
    edges = np.linspace(0, 1, n + 1)
    fig, ax = _bare_axes()
    ax.pcolormesh(edges, edges, rng.standard_normal((n, n)), shading="flat")
    return fig


def _cross_scatter(n: int):
    rng = np.random.default_rng(3)
    fig, ax = _bare_axes()
    ax.scatter(rng.standard_normal(n), rng.standard_normal(n), s=6)
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


def _fig_linecoll(n: int):
    from matplotlib.collections import LineCollection

    xs = np.linspace(0, 1, n)
    segs = [[(x, 0.0), (x, 1.0)] for x in xs]
    fig, ax = _bare_axes()
    ax.add_collection(LineCollection(segs, colors="#333333"))
    return fig


def _svg_tag_counts(fig) -> dict:
    """这张图的预览 SVG 里各出现了多少个绘制节点、多少个坐标对。

    坐标对按 `d="…"` 里的 `M` / `L` 指令数——mpl 的折线路径就是
    `M x y L x y …`，所以这是**后端自己写出来的那个数**，与模型的算法毫无
    关系。对拍要的正是这个：两侧同源就等于自己验自己。
    """
    import io
    import re

    buf = io.StringIO()
    fig.savefig(buf, format="svg")
    svg = buf.getvalue()
    ds = re.findall(r'\bd="([^"]*)"', svg)
    return {
        "path": svg.count("<path"),
        "use": svg.count("<use"),
        "image": svg.count("<image"),
        "ml": sum(len(re.findall(r"[ML]", d)) for d in ds),
        "bytes": len(svg.encode("utf-8")),
    }


#: 拿去对拍的图。**必须小**——每条要 savefig 两次；而对拍验的是**机制**
#: （模型算的那个 N 与后端画的次数是不是同一个 N），不是规模。
_CROSSCHECK = {
    # 名字 -> (带这个 artist 的图, 除它之外完全相同的对照图)
    "mesh": (lambda: _cross_mesh(24), _fig_blank),
    "scatter": (lambda: _cross_scatter(500), _fig_blank),
    "polycollection": (lambda: _fig_polys(300), _fig_blank),
    "linecoll": (lambda: _fig_linecoll(400), _fig_blank),
    "contour": (_cross_contour, _fig_blank),
}


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
        costs = [c for c in pc.analyze_preview_complexity(fig_with).costs if c.rasterizable]
        model_primitives = sum(c.primitive_count for c in costs)
        model_vertices = sum(c.vertex_count for c in costs)
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
                "svg_delta_path": with_counts["path"] - without_counts["path"],
                "svg_delta_use": with_counts["use"] - without_counts["use"],
                "svg_delta_ml": with_counts["ml"] - without_counts["ml"],
                "svg_delta_bytes": with_counts["bytes"] - without_counts["bytes"],
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
    }
    if args.bench:
        payload["bench"] = _bench(args.issue181_n, args.bench_repeat)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
