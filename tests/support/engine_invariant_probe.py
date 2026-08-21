"""在 **worker 解释器**里跑的结构性探针：不变式 4（完整性）与 5（单一权威）。

这两条问的是引擎内部的自洽，而 `tests/` 跑在 Flask 的 .venv 里、import 不动
matplotlib，所以它们只能在这一侧回答。用法：

    python tests/support/engine_invariant_probe.py            # 打印 JSON 报告
    python tests/support/engine_invariant_probe.py --pretty   # 人看的排版

退出码永远是 0（除非探针自己崩了）——判定归调用方
`tests/test_invariants_engine.py`，这里只**如实报事实**。探针自己下判决的话，
「这条该不该算违规」的裁决就藏进了被测的那一侧。
"""
from __future__ import annotations

import json
import os
import sys

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块**平铺
# import**。走 `tavotto.engine.manifest` 会当场 ModuleNotFoundError——manifest
# 自己 `import pathgeom`，那是 worker 侧的约定，不是包路径。
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib                                      # noqa: E402
matplotlib.use("Agg")
import numpy as np                                     # noqa: E402
import matplotlib.pyplot as plt                        # noqa: E402
from matplotlib.artist import Artist                   # noqa: E402
from matplotlib.axes import Axes                       # noqa: E402
from matplotlib.axis import Axis                       # noqa: E402
from matplotlib.collections import LineCollection      # noqa: E402
from matplotlib.lines import Line2D                    # noqa: E402
from matplotlib.patches import Arc, Circle, Rectangle  # noqa: E402
from matplotlib.text import Text                       # noqa: E402

import manifest as M                                   # noqa: E402
import overrides as O                                  # noqa: E402


class GhostArtist(Artist):
    """只实现 `draw()`、没重写 `get_window_extent()` —— 量不出几何的自定义
    artist。它登记得进去、却会在 build_manifest 里被丢掉，**必须报出来**。"""

    def draw(self, renderer):
        return None


class MyPatch(Rectangle):
    pass


class MyLine(Line2D):
    pass


def build_figure():
    """一张尽量宽的图：Collection（映射 / 未映射）、Patch、三种容器、
    色条 ↔ mappable、图例、图像、自定义子类与认不出的 artist 各来一份。"""
    rng = np.random.RandomState(0)
    fig, axs = plt.subplots(2, 2, figsize=(7.0, 5.0))
    ax, ax2, ax3, ax4 = axs.ravel()
    x = np.linspace(0.5, 6.0, 24)

    # --- Collection 族 ---
    ax.scatter(x, np.sin(x), label="pts")
    ax.scatter(x, np.cos(x) + 3, c=x, label="mapped")
    ax.fill_between(x, -1.2, np.sin(x) - 1.0, alpha=0.3)
    ax.pcolormesh(np.linspace(7, 9, 9), np.linspace(0, 2, 9), rng.rand(8, 8))
    ax.contour(np.linspace(7, 9, 8), np.linspace(3, 5, 8), rng.rand(8, 8))
    ax.eventplot([[1.0, 2.0, 3.0]], lineoffsets=5.0, linelengths=0.6)
    ax.hlines([4.2], 1.0, 3.0, color="#B34700")
    # 标量映射的线组：**不算线组那一族**（登记与 dispatch 必须同判据）
    ax.add_collection(LineCollection(
        [[(0.5, -2.0), (6.0, -2.0)], [(0.5, -1.6), (6.0, -1.6)]],
        array=np.array([0.2, 0.8]), cmap="viridis", linestyles="--"))
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.5, 6)
    ax.legend()
    ax.set_title("collections")

    # --- Patch 族 ---
    ax2.add_patch(Circle((0.4, 0.5), 0.15, facecolor="#B34700"))
    ax2.add_patch(MyPatch((0.6, 0.2), 0.2, 0.2, facecolor="#2A6F3C"))
    ax2.add_patch(Arc((0.3, 0.2), 0.25, 0.25, theta1=0, theta2=270))
    ax2.axhspan(0.75, 0.9, alpha=0.2)
    ax2.stairs(np.arange(1, 5) / 5.0, np.linspace(0.1, 0.9, 5))
    ax2.set_xlabel("patches")

    # --- 容器族 + 自定义 / 未知 artist ---
    ax3.stem([1.0, 2.0, 3.0], [1.0, 2.0, 1.5], linefmt="--", label="stems")
    ax3.bar([5.0, 6.0], [1.0, 2.0], label="bars")
    ax3.errorbar([8.0, 9.0], [1.0, 1.5], yerr=0.2, label="err", capsize=3)
    ax3.add_line(MyLine([0.0, 1.0], [0.0, 0.4], color="#123456"))
    ax3.add_artist(GhostArtist())
    ax3.legend()

    # --- 子 axes：插图 + 次坐标轴（**不在 `fig.axes` 里**） ---
    ins = ax3.inset_axes([0.62, 0.62, 0.34, 0.34])
    ins.plot([0.0, 1.0], [0.0, 1.0], color="#804000")
    ins.add_artist(GhostArtist())          # 插图里也放一个量不出几何的
    ax3.secondary_xaxis("top")
    # **3D 插图**：`plot_surface` 出的 `Poly3DCollection` 是普查真正报得出来
    # 的那一类（CompatBench 的「Top unrecognized artists」里排第一）。放在
    # 子 axes 里，是为了让「普查走不走 child_axes」这件事**有用例可证**——
    # 少了它，插图里能被普查抓到的东西一个都没有，那条遍历就成了没人验的代码。
    d3 = ax2.inset_axes([0.05, 0.55, 0.38, 0.4], projection="3d")
    gx, gy = np.meshgrid(np.linspace(0, 1, 6), np.linspace(0, 1, 6))
    d3.plot_surface(gx, gy, gx * gy)

    # **插图上的色条**：宿主只存在于 `child_axes` 里。`colorbar_maps` 只扫
    # `fig.axes` 的时候，这条色条整个不被认出来——不但没有 ColorbarProxy，
    # 连 Collection 族的登记闸也挡不住它，`cb.solids` / `cb.dividers` 会被
    # 当成用户图元登记，而它们每次 `_draw_all()` 都被删掉重建。
    ins2 = ax4.inset_axes([0.05, 0.05, 0.3, 0.3])
    im2 = ins2.imshow(rng.rand(6, 6), cmap="viridis")
    fig.colorbar(im2, ax=ins2)

    # --- 图像 + 色条（两个 gid 一份状态） ---
    im = ax4.imshow(rng.rand(8, 8), cmap="magma")
    cb = fig.colorbar(im, ax=ax4, extend="both")
    cb.set_label("signal")
    ax4.text(1.0, 1.0, "note", color="#804000")

    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# 不变式 4：登记范围内的 artist 不许静默消失
# ---------------------------------------------------------------------------
def completeness(fig, state, man) -> dict:
    """`elements` XOR `unsupported` —— 两头都不出现的报出来。

    判据分两条，缺一不可：

    * **登记过的必须有代表**：`state.index` 里的每个 artist 要么进 `elements`、
      要么是别名（与某个已在元素表里的 artist 同一个对象）、要么被容器消费
      （由容器代表）、要么进 `unsupported`。登记了却被 build_manifest 丢掉
      是最阴的一种：普查判「已知」用的正是登记表，于是它两头都不出现。
    * **树里的必须被普查看见**：census 的那一层遍历里，不属于上面任何一类的
      artist 必须出现在 `unsupported` 行里。

    正常的生命周期来去不算漏（刻度 / 空文字 / 色条内部件 / 图例结构件），
    它们各有各的理由，列在 `churn` 里——**这张表要显式**，不然 unsupported
    会退化成一个没人看的垃圾桶。
    """
    element_ids = {id(el["artist"]) for el in state.elements}
    manifest_gids = {e["gid"] for e in man["elements"]}
    live_ids = {id(el["artist"]) for el in state.elements if el["gid"] in manifest_gids}

    consumed: set[int] = set()
    for el in state.elements:
        art = el["artist"]
        if isinstance(art, M.SeriesGroup):
            consumed.update(id(m) for m in art.members())
            if isinstance(art.artists, list):
                consumed.update(id(m) for m in art.artists)

    internal = M._internal_ids(fig, state.colorbar_axes)      # noqa: SLF001
    unsupported_classes = {row["cls"] for row in man.get("unsupported", [])}

    def qual(a) -> str:
        c = type(a)
        return f"{c.__module__}.{c.__qualname__}"

    # ① 登记过、却在 manifest 里没有条目、也没被报进 unsupported 的。
    #
    # **正常的生命周期来去要摘掉，而且只有两条**（与 `build_manifest._drop`
    # 里那两句同源）：刻度不是常驻 artist（换 locator / 改 xlim / 翻色条方向
    # 都会让整组重来），空文字的标题与轴标签本来就不该进元素树。摘掉的那些
    # 照样报出来（`churn`）交给调用方核对形状——不然这张豁免表迟早变成
    # 「凡是没进元素表的都算正常」，而那时 unsupported 就成了没人看的垃圾桶。
    role_by_id = {id(el["artist"]): el["role"] for el in state.elements}

    def churn_reason(gid, art):
        role = role_by_id.get(id(art))
        if role in ("ticks", "ticklabel"):
            return "tick_churn"
        if isinstance(art, Text) and not art.get_text():
            return "empty_text"
        return None

    orphans, churn = [], []
    for gid, art in state.index.items():
        if id(art) in live_ids or id(art) in consumed:
            continue
        if gid in manifest_gids or qual(art) in unsupported_classes:
            continue
        why = churn_reason(gid, art)
        if why is not None:
            churn.append({"gid": gid, "cls": qual(art), "why": why})
        else:
            orphans.append({"gid": gid, "cls": qual(art)})

    # ② census 那一层遍历里，既不在元素表、又没进 unsupported 的
    unseen = []
    known = state.index_ids() | internal | consumed
    # 与 `census` / `instrument` 同一条遍历：`inset_axes` 与 `secondary_[xy]axis`
    # 挂在 `ax.child_axes` 上，**不在 `fig.axes` 里**。只走 fig.axes 的话，
    # 插图里漏掉的 artist 连这条不变式都看不见——探针自己成了那个报平安的门禁。
    ordered, _child_ids = M._ordered_axes(fig)          # noqa: SLF001
    for owner_gid, owner in ([("figure", fig)]
                             + [(f"axes_{i}", ax) for i, ax in enumerate(ordered)]):
        for child in owner.get_children():
            if id(child) in known or isinstance(child, (Axes, Axis)):
                continue
            if isinstance(child, Text) and not child.get_text():
                continue
            if qual(child) not in unsupported_classes:
                unseen.append({"where": owner_gid, "cls": qual(child)})

    # `unsupported` 只报类名与位置、不报对象 id，所以「同一个 artist 是不是
    # 同时出现在两边」从 manifest 侧**判不出来**——同一个类完全可能一半在元素
    # 表、一半没进（`Rectangle` 既是柱形系列的柱，又是插图的背景 patch）。
    # 曾经按类名比过一次，当场误报。XOR 的这一半交给 ① 与 ②：登记过的必须有
    # 代表、树里的必须被普查看见，两者合起来已经不留缝。

    # 色条轴上**只该有色条自己**。内部件（色带 QuadMesh、分隔线
    # LineCollection、extend 的延伸三角）每次 `_draw_all()` 都被删掉重建，
    # 登记它们等于让 override 挂在幽灵上。
    # `is_colorbar` 是 **build_manifest 出的那份**上的标记，`state.elements`
    # 只有登记信息（gid/artist/role/label/draggable）——第一版读错了地方，
    # 于是这条检查恒回空集：一条永远绿的检查，正是本轮在收的那种空门禁。
    cbar_gids = {e["gid"] for e in man["elements"] if e.get("is_colorbar")}
    cbar_leaks = sorted(
        g for g in manifest_gids
        for cg in cbar_gids
        if g.startswith(f"{cg}.")
        and not g.startswith((f"{cg}.colorbar", f"{cg}.x", f"{cg}.y")))

    # 随行关系：拖动宿主时该一起走的那些 axes。插图上的色条被认出来之后，
    # 这条关系还会在 `follow_map` 里被丢一次（宿主不在 `fig.axes` 里 →
    # `gid_of_ax.get(host)` 是 None → link 直接返回，没有任何提示）。
    follow = {k: sorted(v) for k, v in sorted(state.axes_follow.items())}

    return {"orphans": orphans, "churn": churn, "unseen": unseen,
            "colorbar_axes": sorted(cbar_gids), "colorbar_leaks": cbar_leaks,
            "axes_follow": follow,
            "unsupported": man.get("unsupported", []),
            "element_count": len(man["elements"]),
            "registered_count": len(state.index),
            "consumed_count": len(consumed),
            "unused": sorted(element_ids - live_ids - consumed)[:0]}


# ---------------------------------------------------------------------------
# 不变式 5：同一个判断只能有一处出处
# ---------------------------------------------------------------------------
#: manifest 挑的 gid 前缀 / role → `overrides._cls_key` 必须回的那个 family。
#: 三条兼容前缀（scatter / fill / collections）指的是同一族，前缀是历史命名、
#: 不是分类；`linecoll` 才是真的另一族（对外 prop 叫 `color`）。
_ROLE_TO_FAMILY = {
    "scatter": "collection", "fill": "collection", "collection": "collection",
    "linecoll": "linecoll", "patch": "patch", "bar": "bar", "line": "line",
    "image": "image", "text": "text", "title": "text", "axis_label": "text",
    "legend_text": "text", "legend": "legend", "axes": "axes", "axes3d": "axes",
    "arrow_patch": "arrowpatch", "stem_series": "stem_series",
    "bar_series": "bar_series", "errorbar": "errorbar", "colorbar": "colorbar",
    "ticks": "ticks", "ticklabel": "ticklabel", "artist": "artist",
}


def single_authority(fig, state, man) -> dict:
    """四条「这件事谁说了算」的核对。"""
    family_conflicts, missing_handlers, prefix_conflicts = [], [], []

    by_gid = {el["gid"]: el for el in state.elements}
    for entry in man["elements"]:
        el = by_gid.get(entry["gid"])
        if el is None or entry["gid"] == "figure":
            continue
        art, role = el["artist"], el["role"]
        key = O._cls_key(art)                            # noqa: SLF001
        want = _ROLE_TO_FAMILY.get(role)
        if want is not None and key != want:
            family_conflicts.append(
                {"gid": entry["gid"], "role": role, "cls_key": key, "expected": want})
        # 元素表宣称可编辑的每一条，dispatch 侧都必须有 handler
        for f in entry["editable"]:
            if (key, f["prop"]) not in O.HANDLERS:
                missing_handlers.append(
                    {"gid": entry["gid"], "cls_key": key, "prop": f["prop"]})

    # gid 前缀这条判断只有 `_collection_gid_prefix` 一处；它与 dispatch 侧的
    # `is_linecoll_family` 必须给出同一个答案——这正是「映射的线组」那个 bug
    for i, ax in enumerate(fig.axes):
        for j, coll in enumerate(ax.collections):
            prefix = M._collection_gid_prefix(coll)       # noqa: SLF001
            key = O._cls_key(coll)                        # noqa: SLF001
            want = _ROLE_TO_FAMILY.get(prefix if prefix != "collections" else "collection")
            if want != key:
                prefix_conflicts.append(
                    {"where": f"axes_{i}.{prefix}_{j}", "prefix": prefix,
                     "cls_key": key, "cls": type(coll).__name__})

    # 别名表：广播端自己得有 handler，否则那一组永远解析不出来
    alias_without_handler = [f"{c}.{p}" for (c, p) in O.ALIAS_GROUPS
                             if (c, p) not in O.HANDLERS]

    return {"family_conflicts": family_conflicts,
            "missing_handlers": missing_handlers,
            "prefix_conflicts": prefix_conflicts,
            "alias_without_handler": sorted(alias_without_handler),
            "alias_group_count": len(O.ALIAS_GROUPS)}


# ---------------------------------------------------------------------------
# 例外表的复测：`honours_stroke_style` 是按实测写死的，就得每次真渲染一遍
# ---------------------------------------------------------------------------
def stroke_style_table() -> list[dict]:
    """逐个 Collection 真画一遍，回报 hatch / linestyle 各改动了多少像素。

    `overrides.honours_stroke_style` 是一张**按实测写下来的例外表**（网格类的
    渲染原语不接花纹与虚线）。写死的例外最怕悄悄过期——哪天 matplotlib 给
    `draw_quad_mesh` 补上花纹，那张表就开始撒谎，而症状是「界面上少了个能用
    的开关」，没有任何报错。所以这里不看类名、只看像素，判定归调用方。
    """
    def ink(fig):
        fig.canvas.draw()
        return np.asarray(fig.canvas.buffer_rgba()).copy()

    def changed(a, b):
        return int((np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 0).sum())

    rng = np.random.RandomState(0)
    grid = np.linspace(0, 1, 4)
    z = rng.rand(3, 3)
    cases = {
        "QuadMesh": lambda ax: ax.pcolormesh(grid, grid, z),
        "TriMesh": lambda ax: ax.tripcolor(rng.rand(20), rng.rand(20), rng.rand(20),
                                           shading="gouraud"),
        "PolyQuadMesh": lambda ax: ax.pcolor(grid, grid, z),
        "PolyCollection": lambda ax: ax.fill_between(np.linspace(0, 1, 20), 0,
                                                     np.linspace(0, 1, 20)),
        "PathCollection": lambda ax: ax.scatter(rng.rand(9), rng.rand(9), s=900),
        "LineCollection": lambda ax: ax.add_collection(LineCollection(
            [[(0.0, 0.0), (1.0, 1.0)]], linewidths=3)),
    }
    rows = []
    for name, make in cases.items():
        fig, ax = plt.subplots(figsize=(3.0, 3.0), dpi=100)
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        coll = make(ax)
        if isinstance(coll, list):
            coll = coll[0]
        coll.set_edgecolor("#ff00ff")
        coll.set_linewidth(2.0)
        base = ink(fig)
        coll.set_hatch("//")
        hatch_px = changed(base, ink(fig))
        coll.set_hatch(None)
        ink(fig)
        coll.set_linestyle("--")
        dash_px = changed(base, ink(fig))
        rows.append({"case": name, "cls": type(coll).__name__,
                     "hatch_px": hatch_px, "dash_px": dash_px,
                     "predicate": bool(O.honours_stroke_style(coll))})
        plt.close(fig)
    return rows


def main() -> int:
    fig = build_figure()
    state = O.FigState(fig)
    M.instrument(state)
    man = M.build_manifest(state, "Probe")
    report = {"matplotlib": matplotlib.__version__,
              "completeness": completeness(fig, state, man),
              "single_authority": single_authority(fig, state, man),
              "stroke_style_table": stroke_style_table()}
    if "--pretty" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
