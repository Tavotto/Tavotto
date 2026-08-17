"""Override 应用层（worker 子进程内使用）。

设计：文档每次发来**全量** override 列表（[{gid, prop, value}...]），
worker 维护每张图的 applied / originals 两张表：
  - 新列表缺少上次已应用的 (gid,prop) → 用 originals 恢复原值（支持 undo）
  - 首次修改某 (gid,prop) 时先记录原值
坐标约定：前端发来的位置一律是 figure 分数坐标、y 轴向下（top-origin），
worker 在此转换为各 artist 自己的坐标系。
"""
from __future__ import annotations

import numpy as np

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.patheffects as mpatheffects
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.container import BarContainer, ErrorbarContainer
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.patches import BoxStyle, FancyArrowPatch, Rectangle
from mpl_toolkits.mplot3d import proj3d
from matplotlib.text import Text
from matplotlib.ticker import FormatStrFormatter, ScalarFormatter
from matplotlib.image import AxesImage


class FigState:
    """一张常驻内存 Figure 的可变状态。"""

    def __init__(self, fig: Figure):
        self.fig = fig
        self.elements: list[dict] = []      # manifest.instrument 填充（含 artist 引用）
        self.index: dict[str, object] = {}  # gid -> artist（"figure" -> Figure）
        self.applied: dict[tuple, object] = {}    # (gid,prop) -> 请求值
        self.originals: dict[tuple, object] = {}  # (gid,prop) -> 原生值
        self.colorbar_axes: set = set()     # 承载色条的轴（manifest 标记用）


class SeriesGroup:
    """一组同质 artist 的伪元素（柱形系列 / 误差棒），属性统一应用、按成员还原。

    kind="bar_series": artists = [Rectangle...]
    kind="errorbar":   artists = {"line": Line2D|None, "caps": [Line2D], "bars": [LineCollection]}
    """

    def __init__(self, kind: str, artists, container=None):
        self.kind = kind
        self.artists = artists
        self.container = container

    def set_gid(self, gid) -> None:
        """伪元素不进 SVG；前端命中靠 manifest 的并集 bbox。"""

    def members(self) -> list:
        if self.kind == "errorbar":
            line = self.artists.get("line")
            return ([line] if line is not None else []) + self.artists["caps"] + self.artists["bars"]
        return list(self.artists)


class ColorbarProxy:
    """色条伪元素：字段落在 Colorbar 对象与其 mappable 上，命中/位置走宿主轴。"""

    def __init__(self, cb):
        self.cb = cb

    def set_gid(self, gid) -> None:
        """宿主轴已有 axes_i gid；伪元素靠 manifest bbox 命中。"""


class TickSet:
    """一条坐标轴全部刻度标签的伪元素（tick label 会随 draw 重建，
    属性必须走 tick_params 才能持久）。3D 轴多一条 "z"。"""

    def __init__(self, ax: Axes, which: str):  # which: "x" | "y" | "z"
        self.ax = ax
        self.which = which

    def set_gid(self, gid) -> None:
        """伪元素不进 SVG；前端命中靠 manifest bbox。"""

    @property
    def labels(self):
        get = getattr(self.ax, f"get_{self.which}ticklabels")
        return [t for t in get() if t.get_text()]

    def _first(self, getter, default):
        labs = self.labels
        return getter(labs[0]) if labs else default

    def tick_params(self, **kw) -> None:
        self.ax.tick_params(axis=self.which, which="both", **kw)


class TickLabel:
    """单个刻度标签的伪元素（按主刻度序号定位；标签会随 draw 重建，
    改文字要通过 set_xticks(ticks, labels) 冻结整组后替换）。3D 轴含 "z"。"""

    def __init__(self, ax: Axes, which: str, index: int):
        self.ax = ax
        self.which = which
        self.index = index

    def set_gid(self, gid) -> None:
        """伪元素不进 SVG；前端命中靠 manifest bbox。"""

    def live(self):
        labels = getattr(self.ax, f"get_{self.which}ticklabels")()
        return labels[self.index] if self.index < len(labels) else None

    def get_text(self) -> str:
        t = self.live()
        return t.get_text() if t is not None else ""

    def set_text(self, value) -> None:
        labels = getattr(self.ax, f"get_{self.which}ticklabels")()
        texts = [t.get_text() for t in labels]
        if self.index >= len(texts):
            return
        texts[self.index] = str(value)
        ticks = getattr(self.ax, f"get_{self.which}ticks")()
        getattr(self.ax, f"set_{self.which}ticks")(ticks, texts)


# ---------------------------------------------------------------------------
# 坐标换算
# ---------------------------------------------------------------------------
def _frac_to_display(fig: Figure, fx: float, fy_top: float) -> tuple[float, float]:
    """figure 分数（top-origin）→ display 像素（bottom-origin）。"""
    return fx * fig.bbox.width, (1.0 - fy_top) * fig.bbox.height


def _set_text_pos_frac(t: Text, value) -> None:
    """拖动文字。轴标签/标题被 matplotlib 每次 draw 自动重定位，
    需分别走 set_label_coords / 关闭 _autotitlepos，否则 set_position 会被覆盖。
    （manifest.instrument 在这些 artist 上打了 _mm_drag 标记。）"""
    fig = t.get_figure()
    disp = _frac_to_display(fig, float(value[0]), float(value[1]))
    kind, ax = getattr(t, "_mm_drag", (None, None))
    if kind in ("xlabel", "ylabel"):
        axis = ax.xaxis if kind == "xlabel" else ax.yaxis
        fx, fy = ax.transAxes.inverted().transform(disp)
        axis.set_label_coords(float(fx), float(fy))
        return
    if kind == "title":
        ax._autotitlepos = False  # noqa: SLF001
    t.set_position(tuple(t.get_transform().inverted().transform(disp)))


def _get_text_pos(t: Text):
    kind, _ax = getattr(t, "_mm_drag", (None, None))
    if kind in ("xlabel", "ylabel"):
        # set_label_coords 会同时替换 transform，恢复时两者都要还原
        return (t.get_position(), t.get_transform())
    return t.get_position()


def _restore_text_pos(t: Text, orig) -> None:
    kind, ax = getattr(t, "_mm_drag", (None, None))
    if kind in ("xlabel", "ylabel"):
        axis = ax.xaxis if kind == "xlabel" else ax.yaxis
        pos, trans = orig
        t.set_transform(trans)
        t.set_position(tuple(pos))
        axis._autolabelpos = True  # noqa: SLF001 — 恢复自动定位
        return
    if kind == "title":
        ax._autotitlepos = True  # noqa: SLF001
    t.set_position(tuple(orig))


def _set_text_fontfamily(t: Text, v) -> None:
    """改字体连同 mathtext 一起改。set_fontfamily 只影响正文，$…$ 里的上下标
    仍按 mathtext 字体集渲染——同一个文字框里两种字体。把该 artist 的
    math_fontfamily 切到 custom 字体集，再让 rcParams 的 mathtext.* 指向同一
    字体，正文与上下标才一致。rcParams 是进程级：多个文字分别改成**不同**
    字体时 custom 集只能指向最后一次的选择（明示的边界）；未改字体的文字
    不在 custom 集上，不受影响。"""
    fam = str(v[0]) if isinstance(v, (list, tuple)) else str(v)
    t.set_fontfamily(fam)
    mpl.rcParams["mathtext.rm"] = fam
    mpl.rcParams["mathtext.it"] = f"{fam}:italic"
    mpl.rcParams["mathtext.bf"] = f"{fam}:bold"
    mpl.rcParams["mathtext.sf"] = fam
    t.set_math_fontfamily("custom")


def _get_text_fontfamily(t: Text):
    # 原生状态 = (family, math_fontfamily)，恢复时两者都要还原
    return (t.get_fontfamily(), t.get_math_fontfamily())


def _restore_text_fontfamily(t: Text, orig) -> None:
    fam, math = orig
    t.set_fontfamily(fam)
    t.set_math_fontfamily(math)


def _set_legend_loc_frac(leg: Legend, value) -> None:
    """拖动图例。若脚本用了 bbox_to_anchor（如 fig11 把图例锚在轴上方），
    必须先清掉锚框——否则 loc 坐标会被解释为相对锚框的位置，图例乱飞。"""
    fig = leg.get_figure()
    disp = _frac_to_display(fig, float(value[0]), float(value[1]))
    parent = leg.parent  # Axes 或 Figure
    trans = parent.transAxes if isinstance(parent, Axes) else fig.transFigure
    leg.set_bbox_to_anchor(None)
    leg.set_loc(tuple(trans.inverted().transform(disp)))


def _get_legend_loc(leg: Legend):
    # 原生状态 = (loc, 锚框)，两者都要随快照恢复
    return (leg._loc, leg._bbox_to_anchor)  # noqa: SLF001


def _restore_legend_loc(leg: Legend, orig) -> None:
    loc, bta = orig
    # 原对象直接放回：set_bbox_to_anchor 会把 TransformedBbox 再包一层变换，坐标爆炸
    leg._bbox_to_anchor = bta  # noqa: SLF001
    leg.set_loc(loc)


# ---------------------------------------------------------------------------
# 文字背景框（Text.set_bbox 的 FancyBboxPatch）与描边（path_effects.withStroke）
# ---------------------------------------------------------------------------
_BBOX_CREATE = dict(boxstyle="square,pad=0.3", facecolor="#FFFFFF",
                    edgecolor="#000000", linewidth=0.0, alpha=1.0)


def _bbox_ensure(t: Text):
    patch = t.get_bbox_patch()
    if patch is None:
        t.set_bbox(dict(_BBOX_CREATE))
        patch = t.get_bbox_patch()
    return patch


def _bbox_handler(read, write, default) -> tuple:
    """背景框子属性：无 patch 时 getter 返回默认值（restore 会把已建 patch
    的属性写回默认，配合 bbox_visible 恢复 False 达成视觉还原）；
    setter 按需建 patch（首次改任何背景属性即出现背景框）。"""
    def g(t):
        p = t.get_bbox_patch()
        return read(p) if p is not None else default

    def s(t, v):
        write(_bbox_ensure(t), v)
    return (g, s)


def _boxstyle_info(p) -> tuple[float, bool]:
    bs = p.get_boxstyle()
    return float(getattr(bs, "pad", 0.3)), isinstance(bs, BoxStyle.Round)


def _boxstyle_set(p, pad=None, rounded=None) -> None:
    cur_pad, cur_round = _boxstyle_info(p)
    pad = cur_pad if pad is None else max(0.0, float(pad))
    rounded = cur_round if rounded is None else bool(rounded)
    p.set_boxstyle(("round" if rounded else "square") + f",pad={pad}")


def _set_bbox_visible(t: Text, v) -> None:
    if not v and t.get_bbox_patch() is None:
        return  # 本来就没有背景框，无需为「关」建 patch
    _bbox_ensure(t).set_visible(bool(v))


def text_linespacing(t) -> float:
    """matplotlib ≥3.11 的 Text 默认 _linespacing 是字符串 'normal'；
    命名值按传统默认 1.2 计（manifest 与 handler 共用）。"""
    v = getattr(t, "_linespacing", 1.2)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 1.2


def _stroke_state(t: Text) -> dict:
    """当前描边三元组，缓存在 artist 上；脚本自带 withStroke 时读入其参数。"""
    st = getattr(t, "_mm_stroke", None)
    if st is None:
        st = {"enabled": False, "color": "#FFFFFF", "width": 1.5}
        for eff in (t.get_path_effects() or []):
            if isinstance(eff, mpatheffects.withStroke):
                kw = getattr(eff, "_gc", {})
                st = {"enabled": True,
                      "color": to_hex(kw.get("foreground", "#FFFFFF")),
                      "width": float(kw.get("linewidth", 1.5))}
                break
        t._mm_stroke = st  # noqa: SLF001
    return st


def _stroke_set(t: Text, key: str, v) -> None:
    _stroke_state(t)[key] = v
    st = t._mm_stroke  # noqa: SLF001
    rest = [e for e in (t.get_path_effects() or [])
            if not isinstance(e, mpatheffects.withStroke)]
    if st["enabled"]:
        rest = [mpatheffects.withStroke(linewidth=float(st["width"]),
                                        foreground=st["color"])] + rest
    t.set_path_effects(rest)


# ---------------------------------------------------------------------------
# 轴网格 / spine / 刻度样式辅助
# ---------------------------------------------------------------------------
def _gridline0(ax: Axes):
    for axis in (ax.xaxis, ax.yaxis):
        gl = axis.get_gridlines()
        if gl:
            return gl[0]
    return None


def _grid_visible(ax: Axes, which: str) -> bool:
    axis = ax.xaxis if which == "x" else ax.yaxis
    gl = axis.get_gridlines()
    return bool(gl and gl[0].get_visible())


def _grid_prop(read, default):
    def g(ax):
        gl = _gridline0(ax)
        return read(gl) if gl is not None else default
    return g


def _spines_get(ax: Axes, fn, default):
    sp = ax.spines.get("left") or next(iter(ax.spines.values()), None)
    return fn(sp) if sp is not None else default


def _spines_set(ax: Axes, fn) -> None:
    for sp in ax.spines.values():
        fn(sp)


def _tick_axis(ts: "TickSet"):
    return getattr(ts.ax, f"{ts.which}axis")


def _tick0(ts: "TickSet"):
    ticks = _tick_axis(ts).get_major_ticks()
    return ticks[0] if ticks else None


def _set_tick_format(ts: "TickSet", v) -> None:
    axis = _tick_axis(ts)
    if v == "auto":
        axis.set_major_formatter(ScalarFormatter())
    elif v == "sci":
        fmt = ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((0, 0))
        axis.set_major_formatter(fmt)
    else:  # "%.0f" / "%.1f" / "%.2f"
        axis.set_major_formatter(FormatStrFormatter(str(v)))


def _set_tick_width(ts: "TickSet", v) -> None:
    ts.tick_params(width=float(v))
    # mplot3d 的 axis3d.draw 每次都会用 _axinfo 覆盖刻度线宽，
    # 只走 tick_params 会在下一次 draw 被打回去
    info = getattr(_tick_axis(ts), "_axinfo", None)
    if info and "tick" in info:
        lw = info["tick"].get("linewidth")
        if isinstance(lw, dict):
            lw[True] = float(v)  # 只动主刻度
        else:
            info["tick"]["linewidth"] = float(v)


def _get_tick_formatter(ts: "TickSet"):
    return _tick_axis(ts).get_major_formatter()


def _restore_tick_format(ts: "TickSet", orig) -> None:
    _tick_axis(ts).set_major_formatter(orig)


# ---------------------------------------------------------------------------
# 3D 轴（Axes3D）：视角 / 轴线 / 背景面板 / 网格
# ---------------------------------------------------------------------------
def _axes3d_axes(a):
    return [a.xaxis, a.yaxis, a.zaxis]


class _AxisArrow3D(FancyArrowPatch):
    """带箭头的 3D 轴线替身。

    mplot3d 的轴线每帧按投影重画、没有箭头样式；这里在每次投影时用
    axis3d 自己的几何助手现算它当前选用的盒边，把箭头画在完全相同的
    位置上、指向坐标增大的一端——视角怎么转都自动跟随（大幅跨象限
    旋转换边也没问题，因为每帧重算）。
    """

    def __init__(self, axis, index: int, overhang: float = 0.06, **kw):
        super().__init__((0, 0), (0, 0), shrinkA=0, shrinkB=0,
                         clip_on=False, **kw)
        self._mm_axis3d = axis
        self._mm_index = index      # 0/1/2 = x/y/z
        self._mm_overhang = overhang

    def do_3d_projection(self, renderer=None):
        axis = self._mm_axis3d
        try:
            mins, maxs, _tc, highs = axis._get_coord_info()[:4]  # noqa: SLF001
        except TypeError:  # 旧版 matplotlib 需要 renderer
            mins, maxs, _tc, highs = axis._get_coord_info(renderer)[:4]  # noqa: SLF001
        minmax = np.where(highs, maxs, mins)
        maxmin = np.where(~highs, maxs, mins)
        p1, p2 = axis._get_axis_line_edge_points(minmax, maxmin)  # noqa: SLF001
        i = self._mm_index
        if p1[i] > p2[i]:
            p1, p2 = p2, p1  # 箭头指向坐标增大的一端（含反转轴也语义正确）
        p2 = p2 + (p2 - p1) * self._mm_overhang
        xs, ys, zs = proj3d.proj_transform(
            (p1[0], p2[0]), (p1[1], p2[1]), (p1[2], p2[2]), self.axes.M)
        self.set_positions((xs[0], ys[0]), (xs[1], ys[1]))
        return min(zs)


_ARROW_STYLE_DEFAULT = {"color": "#000000", "width": 0.8, "head": 6.0}


def _arrow_style(a) -> dict:
    """3D 轴箭头样式，缓存在 axes 上；开关与样式旋钮共用（先调色后开启也生效）。"""
    st = getattr(a, "_mm_arrow_style", None)
    if st is None:
        st = dict(_ARROW_STYLE_DEFAULT)
        a._mm_arrow_style = st  # noqa: SLF001
    return st


def _axis_arrows_on(a) -> bool:
    return bool(getattr(a, "_mm_axis_arrows", None))


def _set_axis_arrows(a, v) -> None:
    """开 = 隐藏原生轴线 + 挂三支 _AxisArrow3D；关 = 移除并恢复原生轴线。"""
    on = bool(v)
    cur = getattr(a, "_mm_axis_arrows", None)
    if on and not cur:
        st = _arrow_style(a)
        arrows = []
        for i, axis in enumerate(_axes3d_axes(a)):
            axis.line.set_visible(False)
            arr = _AxisArrow3D(axis, i, arrowstyle="-|>",
                               mutation_scale=float(st["head"]),
                               lw=float(st["width"]), color=st["color"],
                               zorder=60)
            a.add_artist(arr)
            arrows.append(arr)
        a._mm_axis_arrows = arrows  # noqa: SLF001
    elif not on and cur:
        for arr in cur:
            arr.remove()
        for axis in _axes3d_axes(a):
            axis.line.set_visible(True)
        a._mm_axis_arrows = None  # noqa: SLF001


def _mk_arrow_style_handler(key: str, apply):
    """样式旋钮：写进缓存，并即时作用到已存在的箭头上。"""
    def g(a):
        return _arrow_style(a)[key]

    def s(a, v) -> None:
        st = _arrow_style(a)
        st[key] = v
        for arr in getattr(a, "_mm_axis_arrows", None) or []:
            apply(arr, v)
    return (g, s)


def _view3d_get(which: str):
    def g(a):
        v = getattr(a, which, 0.0)
        return 0.0 if v is None else float(v)
    return g


def _view3d_set(which: str):
    """view_init 未传的角度会被重置回初始视角，所以每次都全量带上现值。"""
    def s(a, v) -> None:
        kw = {"elev": a.elev, "azim": a.azim}
        if hasattr(a, "roll"):
            kw["roll"] = a.roll
        kw[which] = float(v)
        a.view_init(**kw)
        a.stale = True
    return s


def _tri_handler(get1, set1):
    """x/y/z 三条 3D 轴的同名属性：getter 收集各轴原值，统一应用、按轴还原。"""
    def g(a): return [get1(ax) for ax in _axes3d_axes(a)]
    def s(a, v): [set1(ax, v) for ax in _axes3d_axes(a)]
    def r(a, orig): [set1(ax, o) for ax, o in zip(_axes3d_axes(a), orig)]
    return (g, s), r


# ---------------------------------------------------------------------------
# 系列伪元素：统一应用、按成员列表还原
# ---------------------------------------------------------------------------
def _bar_handler(get1, set1):
    """柱形系列：getter 收集每根柱的原值列表，setter 统一应用，restore 逐柱还原。"""
    def g(grp): return [get1(r) for r in grp.artists]
    def s(grp, v): [set1(r, v) for r in grp.artists]
    def r(grp, orig): [set1(rct, o) for rct, o in zip(grp.artists, orig)]
    return (g, s), r


def _bar_width_get(rect: Rectangle):
    return (rect.get_x(), rect.get_width())


def _bar_width_set(rect: Rectangle, v) -> None:
    if isinstance(v, (tuple, list)):  # restore 路径：(x, width) 原样放回
        rect.set_x(float(v[0]))
        rect.set_width(float(v[1]))
        return
    cx = rect.get_x() + rect.get_width() / 2  # 保持柱中心不动
    rect.set_x(cx - float(v) / 2)
    rect.set_width(float(v))


def _eb_handler(getter_each, setter_each, members_fn=None):
    """误差棒：作用于 members()（或指定子集），原值按成员列表还原。"""
    def pick(grp):
        return members_fn(grp) if members_fn else grp.members()
    def g(grp): return [getter_each(a) for a in pick(grp)]
    def s(grp, v): [setter_each(a, v) for a in pick(grp)]
    def r(grp, orig): [setter_each(a, o) for a, o in zip(pick(grp), orig)]
    return (g, s), r


def _eb_caps(grp): return grp.artists["caps"]


def _eb_linewidth_members(grp):
    line = grp.artists.get("line")
    return ([line] if line is not None else []) + grp.artists["bars"]


# ---------------------------------------------------------------------------
# axes / image / collection / colorbar 零散 setter
# ---------------------------------------------------------------------------
def _mk_set_invert(which: str):
    def s(a: Axes, v) -> None:
        cur = a.xaxis_inverted() if which == "x" else a.yaxis_inverted()
        if bool(v) != bool(cur):
            (a.invert_xaxis if which == "x" else a.invert_yaxis)()
    return s


def _set_aspect(a: Axes, v) -> None:
    v = str(v)
    a.set_aspect(v if v in ("auto", "equal") else float(v))


def _mk_spine_get(name: str):
    return lambda a: bool(a.spines[name].get_visible()) if name in a.spines else True


def _mk_spine_set(name: str):
    def s(a: Axes, v) -> None:
        if name in a.spines:
            a.spines[name].set_visible(bool(v))
    return s


def _set_image_origin(im: AxesImage, v) -> None:
    im.origin = str(v)
    im.stale = True


def _set_collection_sizes(coll, v) -> None:
    coll.set_sizes([float(v)] if isinstance(v, (int, float)) else v)


def _set_scatter_marker(coll, v) -> None:
    """换散点 marker：PathCollection 的路径可以整体替换（并非烘焙死）。
    首次修改前缓存原始路径列表；选 "original" 放回原样。"""
    if not hasattr(coll, "_mm_orig_paths"):
        coll._mm_orig_paths = list(coll.get_paths())  # noqa: SLF001
    v = str(v)
    if v in ("original", ""):
        coll.set_paths(coll._mm_orig_paths)  # noqa: SLF001
        coll._mm_marker = None  # noqa: SLF001
        return
    ms = MarkerStyle(v)
    coll.set_paths([ms.get_path().transformed(ms.get_transform())])
    coll._mm_marker = v  # noqa: SLF001


def _restore_scatter_marker(coll, orig) -> None:
    coll.set_paths(list(orig))
    coll._mm_marker = None  # noqa: SLF001


def _set_collection_lw(coll, v) -> None:
    coll.set_linewidth(float(v) if isinstance(v, (int, float)) else v)


def _cb_axis(p: "ColorbarProxy"):
    cb = p.cb
    return cb.ax.yaxis if getattr(cb, "orientation", "vertical") == "vertical" else cb.ax.xaxis


def _cb_tick_fontsize(p: "ColorbarProxy") -> float:
    labs = _cb_axis(p).get_ticklabels()
    return float(labs[0].get_fontsize()) if labs else 8.0


def _cb_tick_color(p: "ColorbarProxy"):
    labs = _cb_axis(p).get_ticklabels()
    return labs[0].get_color() if labs else "#000000"


# ---------------------------------------------------------------------------
# 图例：loc 预设与「重建型」布局属性（ncol/labelspacing/…）
# ---------------------------------------------------------------------------
_LEGEND_LOCS = ["best", "upper right", "upper left", "lower left", "lower right",
                "right", "center left", "center right", "lower center",
                "upper center", "center"]


def _set_legend_loc_preset(leg: Legend, v) -> None:
    leg.set_bbox_to_anchor(None)
    leg.set_loc(str(v))


def _legend_loc_name(leg: Legend) -> str:
    loc = leg._loc  # noqa: SLF001
    if isinstance(loc, (tuple, list)):
        return "custom"
    inv = {v: k for k, v in Legend.codes.items()}
    return inv.get(loc, "best")


_LEGEND_LAYOUT_ATTRS = {"ncol": "_ncols", "borderpad": "borderpad",
                        "labelspacing": "labelspacing", "handlelength": "handlelength"}


def _legend_rebuild_setter(prop: str):
    """ncol/borderpad/labelspacing/handlelength 是构建期参数，改动需要
    _init_legend_box 重排——文字/标题对象会被重建，必须重挂 gid、更新
    state.index 并重放这些 gid 上已应用的 override。"""
    attr = _LEGEND_LAYOUT_ATTRS[prop]

    def setter(leg: Legend, v, state) -> None:
        setattr(leg, attr, int(v) if prop == "ncol" else float(v))
        handles = leg.legend_handles
        labels = [t.get_text() for t in leg.get_texts()]
        title = leg.get_title().get_text()
        leg._init_legend_box(handles, labels)  # noqa: SLF001
        # _init_legend_box 会换掉 _legend_box，定位回调必须重挂——
        # 否则图例内容画在默认偏移上（导出里整块消失）
        leg._legend_box.set_offset(leg._findoffset)  # noqa: SLF001
        leg.set_title(title)
        _reindex_legend_children(leg, state)

    setter._needs_state = True  # noqa: SLF001
    return setter


def _legend_orig_entries(leg: Legend) -> tuple[list, list]:
    """图例条目的原始 (handles, labels)。首次访问时缓存——重排后
    legend_handles 顺序会变，原始序必须只取一次。"""
    orig = getattr(leg, "_mm_entry_orig", None)
    if orig is None:
        orig = (list(leg.legend_handles), [t.get_text() for t in leg.get_texts()])
        leg._mm_entry_orig = orig  # noqa: SLF001
    return orig


def _legend_entry_order(leg: Legend) -> list[int]:
    n = len(leg.get_texts())
    cur = getattr(leg, "_mm_entry_order", None)
    return list(cur) if cur is not None else list(range(n))


def _set_legend_entry_order(leg: Legend, v, state) -> None:
    """图例条目重排：按原始序号的排列重建图例盒。文字对象会被重建，
    与 ncol 等重建型属性一样要重挂 gid 并重放已应用的 override
    （注意 texts_j 的 gid 指的是**显示顺序**的第 j 项）。"""
    handles, labels = _legend_orig_entries(leg)
    idx = [int(i) for i in (v or []) if 0 <= int(i) < len(handles)]
    idx += [i for i in range(len(handles)) if i not in idx]  # 缺漏的按原序补尾
    title = leg.get_title().get_text()
    leg._init_legend_box([handles[i] for i in idx], [labels[i] for i in idx])  # noqa: SLF001
    leg._legend_box.set_offset(leg._findoffset)  # noqa: SLF001 — 重挂定位回调
    leg.set_title(title)
    leg._mm_entry_order = idx  # noqa: SLF001
    _reindex_legend_children(leg, state)


_set_legend_entry_order._needs_state = True  # noqa: SLF001


def _reindex_legend_children(leg: Legend, state: "FigState") -> None:
    leg_gid = leg.get_gid() or ""
    if not leg_gid:
        return
    remap = {}
    for j, t in enumerate(leg.get_texts()):
        remap[f"{leg_gid}.texts_{j}"] = t
    title = leg.get_title()
    if title is not None:
        remap[f"{leg_gid}.title"] = title
    for gid, artist in remap.items():
        artist.set_gid(gid)
        if gid in state.index:
            state.index[gid] = artist
        for el in state.elements:
            if el["gid"] == gid:
                el["artist"] = artist
    # 重放这些 gid 上已应用的 override（旧对象被扔掉，效果要落到新对象上）
    for (gid, prop), value in list(state.applied.items()):
        if gid in remap:
            handler = HANDLERS.get((_cls_key(remap[gid]), prop))
            if handler is not None:
                handler[1](remap[gid], value)


# ---------------------------------------------------------------------------
# (artist 类别, prop) → (getter, setter)
# getter 返回原生值（仅用于恢复）；setter 接受请求值
# ---------------------------------------------------------------------------
def _cls_key(artist) -> str | None:
    if isinstance(artist, TickLabel):
        return "ticklabel"
    if isinstance(artist, TickSet):
        return "ticks"
    if isinstance(artist, SeriesGroup):
        return artist.kind  # "bar_series" | "errorbar"
    if isinstance(artist, ColorbarProxy):
        return "colorbar"
    if isinstance(artist, Figure):
        return "figure"
    if isinstance(artist, Text):
        return "text"
    if isinstance(artist, FancyArrowPatch):
        return "arrowpatch"
    if isinstance(artist, Line2D):
        return "line"
    if isinstance(artist, Legend):
        return "legend"
    if isinstance(artist, Axes):
        return "axes"
    if isinstance(artist, AxesImage):
        return "image"
    if isinstance(artist, Rectangle) and getattr(artist, "_mm_bar", False):
        return "bar"
    if isinstance(artist, PathCollection):
        return "scatter"
    if isinstance(artist, PolyCollection):
        return "fill"
    return None


# ---------------------------------------------------------------------------
# 单色渐变位图（imshow 渐变 + set_clip_path 裁剪，是「形状渐变填充」的标准画法）
# ---------------------------------------------------------------------------
def _gradient_decompose(a: np.ndarray):
    """RGB(A) 数组 → (基色 rgb, 每像素与白的混合系数 s)；解不出返回 None。

    这种图的每个像素都是 `base·(1−s) + 白·s` 的凸组合（s∈[0,1]）：
    基色取离白最远的像素，逐通道反解 s 并要求通道间一致。满足者可整体换
    基色而完全保留渐变形状与 alpha；不满足（真实照片、多色图）绝不硬套。"""
    if a.ndim != 3 or a.shape[2] not in (3, 4):
        return None
    rgb = a[..., :3].astype(float)
    if rgb.size == 0:
        return None
    if a.dtype.kind in "ui" or float(np.nanmax(rgb)) > 1.0001:
        rgb = rgb / 255.0
    flat = rgb.reshape(-1, 3)
    dist = ((1.0 - flat) ** 2).sum(axis=1)
    if float(dist.max()) < 1e-3:   # 全白：没有可辨识的基色
        return None
    base = flat[int(np.argmax(dist))]
    ok = np.abs(1.0 - base) > 1e-3  # 基色为 1 的通道恒等于 1，解不出 s
    if not ok.any():
        return None
    s = (flat[:, ok] - base[ok]) / (1.0 - base[ok])
    if s.shape[1] > 1:
        spread = s.max(axis=1) - s.min(axis=1)
        if float(np.mean(spread > 0.04)) > 0.02:
            return None            # 通道间不一致 → 不是单色渐变
    sm = s.mean(axis=1)
    if float(sm.min()) < -0.04 or float(sm.max()) > 1.04:
        return None
    return base, np.clip(sm, 0.0, 1.0)


def gradient_base_hex(im) -> str | None:
    """渐变位图的基色 '#rrggbb'；不是单色渐变返回 None（字段就不出现）。

    先抽样（≤64×64）做门槛判定——真照片级 imshow 不必逐像素扫，也过不了
    一致性检查；判定通过再在**全量**数组上取精确基色：抽样步长会漏掉渐变
    最深的那一行，基色差一档，界面里显示的就不是脚本写的那个色号。"""
    a = np.asarray(im.get_array())
    if a.ndim != 3:
        return None
    sub = a[::max(1, a.shape[0] // 64), ::max(1, a.shape[1] // 64)]
    if _gradient_decompose(np.asarray(sub)) is None:
        return None
    dec = _gradient_decompose(a)
    if dec is None:
        return None
    return mcolors.to_hex(dec[0])


def _set_image_gradient(im, v) -> None:
    """整体换渐变基色：从**原始**数组解 s 再套新基色（首改前缓存原数组——
    对着改过的数组反解，用户一旦选了近白色 s 就会退化、渐变形状回不去了）。"""
    orig = getattr(im, "_mm_gradient_orig", None)
    if orig is None:
        orig = np.array(im.get_array(), copy=True)
        im._mm_gradient_orig = orig
    a = np.asarray(orig)
    dec = _gradient_decompose(a)
    if dec is None:
        return
    _base, s = dec
    new = np.asarray(mcolors.to_rgb(v), dtype=float)
    out = (new[None, :] * (1.0 - s[:, None]) + s[:, None]).reshape(a.shape[:2] + (3,))
    if a.dtype.kind in "ui":
        result = a.copy()
        result[..., :3] = np.clip(np.rint(out * 255.0), 0, 255).astype(a.dtype)
    else:
        result = a.astype(float, copy=True)
        result[..., :3] = out
    im.set_data(result)


def _restore_image_gradient(im, orig) -> None:
    im.set_data(orig)
    if hasattr(im, "_mm_gradient_orig"):
        del im._mm_gradient_orig


HANDLERS: dict[tuple[str, str], tuple] = {
    ("text", "text"):     (lambda a: a.get_text(),          lambda a, v: a.set_text(str(v))),
    ("text", "fontsize"): (lambda a: a.get_fontsize(),      lambda a, v: a.set_fontsize(float(v))),
    ("text", "color"):    (lambda a: a.get_color(),         lambda a, v: a.set_color(v)),
    ("text", "weight"):   (lambda a: a.get_fontweight(),    lambda a, v: a.set_fontweight(v)),
    ("text", "style"):    (lambda a: a.get_fontstyle(),     lambda a, v: a.set_fontstyle(v)),
    ("text", "rotation"): (lambda a: a.get_rotation(),      lambda a, v: a.set_rotation(float(v))),
    ("text", "visible"):  (lambda a: a.get_visible(),       lambda a, v: a.set_visible(bool(v))),
    ("text", "pos_frac"): (_get_text_pos,                   _set_text_pos_frac),
    ("text", "alpha"):    (lambda a: a.get_alpha(),
                           lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("text", "fontfamily"): (_get_text_fontfamily,          _set_text_fontfamily),
    ("text", "ha"):       (lambda a: a.get_ha(),            lambda a, v: a.set_ha(v)),
    ("text", "va"):       (lambda a: a.get_va(),            lambda a, v: a.set_va(v)),
    ("text", "linespacing"): (lambda a: text_linespacing(a),
                              lambda a, v: a.set_linespacing(float(v))),
    # 仅 3D 轴标签（manifest 打了 _mm_axis 标记）：沿投影轴推远/拉近
    ("text", "labelpad"): (lambda a: float(a._mm_axis.labelpad),
                           lambda a, v: setattr(a._mm_axis, "labelpad", float(v))),
    ("text", "zorder"):   (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),

    ("text", "bbox_visible"): (
        lambda a: a.get_bbox_patch() is not None and bool(a.get_bbox_patch().get_visible()),
        _set_bbox_visible,
    ),
    ("text", "bbox_facecolor"): _bbox_handler(
        lambda p: p.get_facecolor(), lambda p, v: p.set_facecolor(v), "#FFFFFF"),
    ("text", "bbox_edgecolor"): _bbox_handler(
        lambda p: p.get_edgecolor(), lambda p, v: p.set_edgecolor(v), "#000000"),
    ("text", "bbox_linewidth"): _bbox_handler(
        lambda p: float(p.get_linewidth()), lambda p, v: p.set_linewidth(float(v)), 0.0),
    ("text", "bbox_alpha"): _bbox_handler(
        lambda p: p.get_alpha(),
        lambda p, v: p.set_alpha(None if v is None else float(v)), 1.0),
    ("text", "bbox_pad"): _bbox_handler(
        lambda p: _boxstyle_info(p)[0], lambda p, v: _boxstyle_set(p, pad=v), 0.3),
    ("text", "bbox_rounded"): _bbox_handler(
        lambda p: _boxstyle_info(p)[1], lambda p, v: _boxstyle_set(p, rounded=v), False),

    ("text", "stroke_enabled"): (lambda a: bool(_stroke_state(a)["enabled"]),
                                 lambda a, v: _stroke_set(a, "enabled", bool(v))),
    ("text", "stroke_color"):   (lambda a: _stroke_state(a)["color"],
                                 lambda a, v: _stroke_set(a, "color", v)),
    ("text", "stroke_width"):   (lambda a: float(_stroke_state(a)["width"]),
                                 lambda a, v: _stroke_set(a, "width", float(v))),

    # 图内独立箭头（FancyArrowPatch：脚本 add_patch 的与 annotate 的 arrow_patch
    # 同一个类）。set_color 同时写 edge+face——"-|>" 这类实心帽两者必须一致，
    # 分开暴露只会做出「帽黑杆红」的半成品
    ("arrowpatch", "color"): (lambda a: a.get_edgecolor(), lambda a, v: a.set_color(v)),
    ("arrowpatch", "linewidth"): (lambda a: a.get_linewidth(),
                                  lambda a, v: a.set_linewidth(float(v))),
    ("arrowpatch", "mutation_scale"): (lambda a: a.get_mutation_scale(),
                                       lambda a, v: a.set_mutation_scale(float(v))),
    ("arrowpatch", "alpha"): (lambda a: a.get_alpha(),
                              lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("arrowpatch", "visible"): (lambda a: a.get_visible(),
                                lambda a, v: a.set_visible(bool(v))),
    ("arrowpatch", "zorder"): (lambda a: float(a.get_zorder()),
                               lambda a, v: a.set_zorder(float(v))),

    ("line", "color"):     (lambda a: a.get_color(),      lambda a, v: a.set_color(v)),
    ("line", "linewidth"): (lambda a: a.get_linewidth(),  lambda a, v: a.set_linewidth(float(v))),
    ("line", "linestyle"): (lambda a: a.get_linestyle(),  lambda a, v: a.set_linestyle(v)),
    ("line", "marker"):    (lambda a: a.get_marker(),     lambda a, v: a.set_marker(v)),
    ("line", "markersize"):(lambda a: a.get_markersize(), lambda a, v: a.set_markersize(float(v))),
    ("line", "visible"):   (lambda a: a.get_visible(),    lambda a, v: a.set_visible(bool(v))),

    ("legend", "visible"):  (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("legend", "frameon"):  (lambda a: a.get_frame_on(), lambda a, v: a.set_frame_on(bool(v))),
    ("legend", "fontsize"): (
        lambda a: [t.get_fontsize() for t in a.get_texts()],
        lambda a, v: [t.set_fontsize(float(v)) for t in a.get_texts()],
    ),
    ("legend", "loc_frac"): (_get_legend_loc, _set_legend_loc_frac),

    ("axes", "xlim"):     (lambda a: a.get_xlim(),  lambda a, v: a.set_xlim(float(v[0]), float(v[1]))),
    ("axes", "ylim"):     (lambda a: a.get_ylim(),  lambda a, v: a.set_ylim(float(v[0]), float(v[1]))),
    ("axes", "position"): (
        lambda a: list(a.get_position().bounds),
        lambda a, v: a.set_position([float(x) for x in v]),
    ),
    ("axes", "visible"):  (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),

    ("image", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    # 原生值 = 整个像素数组（恢复走 set_data，见 _restore_image_gradient）
    ("image", "gradient_color"): (lambda a: np.array(a.get_array(), copy=True),
                                  _set_image_gradient),

    ("ticklabel", "text"): (
        lambda a: a.get_text(),
        lambda a, v: a.set_text(v),
    ),

    ("ticks", "fontsize"): (
        lambda a: float(a._first(lambda t: t.get_fontsize(), 8.5)),
        lambda a, v: a.tick_params(labelsize=float(v)),
    ),
    ("ticks", "color"): (
        lambda a: a._first(lambda t: t.get_color(), "#000000"),
        lambda a, v: a.tick_params(labelcolor=v),
    ),
    ("ticks", "rotation"): (
        lambda a: float(a._first(lambda t: t.get_rotation(), 0.0)),
        lambda a, v: a.tick_params(labelrotation=float(v)),
    ),
    ("ticks", "visible"): (
        lambda a: bool(a._first(lambda t: t.get_visible(), True)),
        lambda a, v: a.tick_params(
            **({"labelbottom": bool(v)} if a.which == "x" else {"labelleft": bool(v)})),
    ),

    ("figure", "size_mm"): (
        lambda f: [x * 25.4 for x in f.get_size_inches()],
        lambda f, v: f.set_size_inches(float(v[0]) / 25.4, float(v[1]) / 25.4, forward=False),
    ),
    ("figure", "facecolor"): (lambda f: f.patch.get_facecolor(),
                              lambda f, v: f.patch.set_facecolor(v)),
    ("figure", "transparent"): (lambda f: not f.patch.get_visible(),
                                lambda f, v: f.patch.set_visible(not bool(v))),

    # ---- axes: 比例 / 反转 / 缩放 / 网格 / spine / 底色 ----
    ("axes", "xscale"): (lambda a: a.get_xscale(), lambda a, v: a.set_xscale(str(v))),
    ("axes", "yscale"): (lambda a: a.get_yscale(), lambda a, v: a.set_yscale(str(v))),
    ("axes", "invert_x"): (lambda a: bool(a.xaxis_inverted()), _mk_set_invert("x")),
    ("axes", "invert_y"): (lambda a: bool(a.yaxis_inverted()), _mk_set_invert("y")),
    ("axes", "aspect"): (lambda a: a.get_aspect(), _set_aspect),
    ("axes", "facecolor"): (lambda a: a.get_facecolor(), lambda a, v: a.set_facecolor(v)),
    ("axes", "grid_x"): (lambda a: _grid_visible(a, "x"),
                         lambda a, v: a.grid(visible=bool(v), axis="x")),
    ("axes", "grid_y"): (lambda a: _grid_visible(a, "y"),
                         lambda a, v: a.grid(visible=bool(v), axis="y")),
    ("axes", "grid_color"): (
        _grid_prop(lambda g: g.get_color(), "#b0b0b0"),
        lambda a, v: a.tick_params(axis="both", which="both", grid_color=v)),
    ("axes", "grid_linestyle"): (
        _grid_prop(lambda g: g.get_linestyle(), ":"),
        lambda a, v: a.tick_params(axis="both", which="both", grid_linestyle=str(v))),
    ("axes", "grid_linewidth"): (
        _grid_prop(lambda g: float(g.get_linewidth()), 0.5),
        lambda a, v: a.tick_params(axis="both", which="both", grid_linewidth=float(v))),
    ("axes", "grid_alpha"): (
        _grid_prop(lambda g: g.get_alpha(), None),
        lambda a, v: a.tick_params(axis="both", which="both",
                                   grid_alpha=(None if v is None else float(v)))),
    ("axes", "spine_top"): (_mk_spine_get("top"), _mk_spine_set("top")),
    ("axes", "spine_right"): (_mk_spine_get("right"), _mk_spine_set("right")),
    ("axes", "spine_bottom"): (_mk_spine_get("bottom"), _mk_spine_set("bottom")),
    ("axes", "spine_left"): (_mk_spine_get("left"), _mk_spine_set("left")),
    ("axes", "spine_color"): (
        lambda a: _spines_get(a, lambda s: s.get_edgecolor(), (0, 0, 0, 1)),
        lambda a, v: _spines_set(a, lambda s: s.set_edgecolor(v))),
    ("axes", "spine_linewidth"): (
        lambda a: _spines_get(a, lambda s: float(s.get_linewidth()), 0.8),
        lambda a, v: _spines_set(a, lambda s: s.set_linewidth(float(v)))),

    # ---- axes3d: 视角 / 网格（manifest 只对 3D 轴放出这些字段）----
    ("axes", "elev"): (_view3d_get("elev"), _view3d_set("elev")),
    ("axes", "azim"): (_view3d_get("azim"), _view3d_set("azim")),
    ("axes", "roll"): (_view3d_get("roll"), _view3d_set("roll")),
    ("axes", "grid_visible"): (lambda a: bool(getattr(a, "_draw_grid", True)),
                               lambda a, v: a.grid(bool(v))),
    ("axes", "proj_type"): (lambda a: str(getattr(a, "_proj_type", "persp")),
                            lambda a, v: a.set_proj_type(str(v))),

    # ---- axes3d: 轴箭头（隐藏原生轴线，按当前投影的盒边画带箭头的轴）----
    ("axes", "axis_arrows"): (_axis_arrows_on, _set_axis_arrows),
    ("axes", "arrow_color"): _mk_arrow_style_handler(
        "color", lambda p, v: p.set_color(v)),
    ("axes", "arrow_width"): _mk_arrow_style_handler(
        "width", lambda p, v: p.set_linewidth(float(v))),
    ("axes", "arrow_head"): _mk_arrow_style_handler(
        "head", lambda p, v: p.set_mutation_scale(float(v))),

    # ---- image: 颜色映射 / 显示 ----
    ("image", "cmap"): (lambda a: a.get_cmap(), lambda a, v: a.set_cmap(v)),
    ("image", "vmin"): (lambda a: a.get_clim()[0],
                        lambda a, v: a.set_clim(vmin=(None if v is None else float(v)))),
    ("image", "vmax"): (lambda a: a.get_clim()[1],
                        lambda a, v: a.set_clim(vmax=(None if v is None else float(v)))),
    ("image", "interpolation"): (lambda a: a.get_interpolation(),
                                 lambda a, v: a.set_interpolation(str(v))),
    ("image", "alpha"): (lambda a: a.get_alpha(),
                         lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("image", "origin"): (lambda a: a.origin, _set_image_origin),
    ("image", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),

    # ---- line: 标签 / 透明度 / 层级 / marker 颜色 ----
    ("line", "label"): (lambda a: str(a.get_label()), lambda a, v: a.set_label(str(v))),
    ("line", "alpha"): (lambda a: a.get_alpha(),
                        lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("line", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    ("line", "markerfacecolor"): (lambda a: a.get_markerfacecolor(),
                                  lambda a, v: a.set_markerfacecolor(v)),
    ("line", "markeredgecolor"): (lambda a: a.get_markeredgecolor(),
                                  lambda a, v: a.set_markeredgecolor(v)),

    # ---- scatter (PathCollection) / fill (PolyCollection) ----
    ("scatter", "marker"): (lambda a: list(a.get_paths()), _set_scatter_marker),
    ("scatter", "facecolor"): (lambda a: a.get_facecolor().copy(),
                               lambda a, v: a.set_facecolor(v)),
    ("scatter", "edgecolor"): (lambda a: a.get_edgecolor().copy(),
                               lambda a, v: a.set_edgecolor(v)),
    ("scatter", "size"): (lambda a: a.get_sizes().copy(), _set_collection_sizes),
    ("scatter", "linewidth"): (lambda a: a.get_linewidths().copy(), _set_collection_lw),
    ("scatter", "alpha"): (lambda a: a.get_alpha(),
                           lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("scatter", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    ("scatter", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("scatter", "label"): (lambda a: str(a.get_label()), lambda a, v: a.set_label(str(v))),
    ("fill", "facecolor"): (lambda a: a.get_facecolor().copy(), lambda a, v: a.set_facecolor(v)),
    ("fill", "edgecolor"): (lambda a: a.get_edgecolor().copy(), lambda a, v: a.set_edgecolor(v)),
    ("fill", "linewidth"): (lambda a: a.get_linewidths().copy(), _set_collection_lw),
    ("fill", "alpha"): (lambda a: a.get_alpha(),
                        lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("fill", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    ("fill", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),

    # ---- 单根柱（BarContainer 成员，_mm_bar 标记）----
    ("bar", "facecolor"): (lambda a: a.get_facecolor(), lambda a, v: a.set_facecolor(v)),
    ("bar", "edgecolor"): (lambda a: a.get_edgecolor(), lambda a, v: a.set_edgecolor(v)),
    ("bar", "linewidth"): (lambda a: float(a.get_linewidth()),
                           lambda a, v: a.set_linewidth(float(v))),
    ("bar", "alpha"): (lambda a: a.get_alpha(),
                       lambda a, v: a.set_alpha(None if v is None else float(v))),
    ("bar", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),

    # ---- legend: 预设位置 / 标题 / 边框样式 ----
    ("legend", "loc"): (_get_legend_loc, _set_legend_loc_preset),
    ("legend", "title"): (lambda a: a.get_title().get_text(),
                          lambda a, v: a.set_title(str(v))),
    ("legend", "title_fontsize"): (lambda a: float(a.get_title().get_fontsize()),
                                   lambda a, v: a.get_title().set_fontsize(float(v))),
    ("legend", "facecolor"): (lambda a: a.get_frame().get_facecolor(),
                              lambda a, v: a.get_frame().set_facecolor(v)),
    ("legend", "framealpha"): (lambda a: a.get_frame().get_alpha(),
                               lambda a, v: a.get_frame().set_alpha(
                                   None if v is None else float(v))),
    ("legend", "edgecolor"): (lambda a: a.get_frame().get_edgecolor(),
                              lambda a, v: a.get_frame().set_edgecolor(v)),
    ("legend", "entry_order"): (_legend_entry_order, _set_legend_entry_order),
    ("legend", "ncol"): (lambda a: int(getattr(a, "_ncols", 1)), _legend_rebuild_setter("ncol")),
    ("legend", "borderpad"): (lambda a: float(a.borderpad), _legend_rebuild_setter("borderpad")),
    ("legend", "labelspacing"): (lambda a: float(a.labelspacing),
                                 _legend_rebuild_setter("labelspacing")),
    ("legend", "handlelength"): (lambda a: float(a.handlelength),
                                 _legend_rebuild_setter("handlelength")),

    # ---- ticks: 方向 / 长度 / 线宽 / 数字格式 ----
    ("ticks", "direction"): (
        lambda a: str(getattr(_tick0(a), "_tickdir", "out")),
        lambda a, v: a.tick_params(direction=str(v))),
    ("ticks", "length"): (
        lambda a: float(getattr(_tick0(a), "_size", 3.5)),
        lambda a, v: a.tick_params(length=float(v))),
    # 刻度是 marker：线宽在 markeredgewidth 上，get_linewidth 是错误口径
    ("ticks", "width"): (
        lambda a: float(_tick0(a).tick1line.get_markeredgewidth()) if _tick0(a) else 0.8,
        _set_tick_width),
    ("ticks", "format"): (_get_tick_formatter, _set_tick_format),

    # ---- colorbar（ColorbarProxy 伪元素）----
    ("colorbar", "label"): (lambda p: _cb_axis(p).label.get_text(),
                            lambda p, v: p.cb.set_label(str(v))),
    ("colorbar", "cmap"): (lambda p: p.cb.mappable.get_cmap(),
                           lambda p, v: p.cb.mappable.set_cmap(v)),
    ("colorbar", "vmin"): (lambda p: p.cb.mappable.get_clim()[0],
                           lambda p, v: p.cb.mappable.set_clim(
                               vmin=(None if v is None else float(v)))),
    ("colorbar", "vmax"): (lambda p: p.cb.mappable.get_clim()[1],
                           lambda p, v: p.cb.mappable.set_clim(
                               vmax=(None if v is None else float(v)))),
    ("colorbar", "tick_fontsize"): (_cb_tick_fontsize,
                                    lambda p, v: p.cb.ax.tick_params(labelsize=float(v))),
    ("colorbar", "tick_color"): (_cb_tick_color,
                                 lambda p, v: p.cb.ax.tick_params(labelcolor=v)),
    ("colorbar", "outline_visible"): (lambda p: bool(p.cb.outline.get_visible()),
                                      lambda p, v: p.cb.outline.set_visible(bool(v))),
    ("colorbar", "outline_width"): (lambda p: float(p.cb.outline.get_linewidth()),
                                    lambda p, v: p.cb.outline.set_linewidth(float(v))),
    ("colorbar", "visible"): (lambda p: p.cb.ax.get_visible(),
                              lambda p, v: p.cb.ax.set_visible(bool(v))),
}

# 系列伪元素：统一应用、按成员还原（restore 函数暂存，随后并入 _RESTORE）
_PENDING_RESTORES: dict[tuple[str, str], object] = {}

for _prop, _g1, _s1 in [
    ("facecolor", lambda r: r.get_facecolor(), lambda r, v: r.set_facecolor(v)),
    ("edgecolor", lambda r: r.get_edgecolor(), lambda r, v: r.set_edgecolor(v)),
    ("linewidth", lambda r: float(r.get_linewidth()), lambda r, v: r.set_linewidth(float(v))),
    ("alpha", lambda r: r.get_alpha(), lambda r, v: r.set_alpha(None if v is None else float(v))),
    ("visible", lambda r: r.get_visible(), lambda r, v: r.set_visible(bool(v))),
    ("zorder", lambda r: float(r.get_zorder()), lambda r, v: r.set_zorder(float(v))),
    ("bar_width", _bar_width_get, _bar_width_set),
]:
    _h, _r = _bar_handler(_g1, _s1)
    HANDLERS[("bar_series", _prop)] = _h
    _PENDING_RESTORES[("bar_series", _prop)] = _r
HANDLERS[("bar_series", "label")] = (
    lambda g: str(g.container.get_label()) if g.container is not None else "",
    lambda g, v: g.container.set_label(str(v)) if g.container is not None else None)

for _prop, _pair in [
    ("color", _eb_handler(lambda a: a.get_color(), lambda a, v: a.set_color(v))),
    ("linewidth", _eb_handler(lambda a: a.get_linewidth(), lambda a, v: a.set_linewidth(v),
                              _eb_linewidth_members)),
    ("capsize", _eb_handler(lambda a: float(a.get_markersize()),
                            lambda a, v: a.set_markersize(float(v)), _eb_caps)),
    ("cap_thickness", _eb_handler(lambda a: float(a.get_markeredgewidth()),
                                  lambda a, v: a.set_markeredgewidth(float(v)), _eb_caps)),
    ("alpha", _eb_handler(lambda a: a.get_alpha(),
                          lambda a, v: a.set_alpha(None if v is None else float(v)))),
    ("visible", _eb_handler(lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v)))),
]:
    HANDLERS[("errorbar", _prop)] = _pair[0]
    _PENDING_RESTORES[("errorbar", _prop)] = _pair[1]

# 3D 轴线 / 背景面板：作用于 x/y/z 三条轴，原值按轴列表还原
for _prop, _g3, _s3 in [
    ("axline_color", lambda ax: ax.line.get_color(), lambda ax, v: ax.line.set_color(v)),
    ("axline_width", lambda ax: float(ax.line.get_linewidth()),
     lambda ax, v: ax.line.set_linewidth(float(v))),
    ("pane_visible", lambda ax: bool(ax.pane.get_visible()),
     lambda ax, v: ax.pane.set_visible(bool(v))),
    ("pane_color", lambda ax: ax.pane.get_facecolor(),
     lambda ax, v: ax.pane.set_facecolor(v)),
]:
    _h3, _r3 = _tri_handler(_g3, _s3)
    HANDLERS[("axes", _prop)] = _h3
    _PENDING_RESTORES[("axes", _prop)] = _r3

# 恢复原值时 pos_frac / loc_frac 的原生值需要走原生 setter
_RESTORE: dict[tuple[str, str], object] = {
    ("scatter", "marker"):  _restore_scatter_marker,
    ("text", "pos_frac"):   _restore_text_pos,
    ("text", "fontfamily"): _restore_text_fontfamily,
    ("image", "gradient_color"): _restore_image_gradient,
    ("legend", "loc_frac"): _restore_legend_loc,
    ("legend", "loc"):      _restore_legend_loc,  # loc 预设的原生值同为 (loc, 锚框)
    ("ticks", "format"):    _restore_tick_format,
    ("figure", "size_mm"):  lambda f, v: f.set_size_inches(v[0] / 25.4, v[1] / 25.4, forward=False),
}
_RESTORE.update(_PENDING_RESTORES)


def to_hex(color) -> str:
    try:
        return mcolors.to_hex(color)
    except (ValueError, TypeError):
        return "#000000"


def apply(state: FigState, patches: list[dict]) -> list[str]:
    """把全量 patch 列表同步到 Figure。返回 warning 列表（孤儿 gid 等）。"""
    warnings: list[str] = []
    new: dict[tuple, object] = {}
    for p in patches:
        key = (str(p["gid"]), str(p["prop"]))
        new[key] = p["value"]

    # 上次应用、这次不在 → 恢复原值
    for key in list(state.applied):
        if key in new:
            continue
        artist = state.index.get(key[0])
        orig = state.originals.get(key)
        if artist is not None and key in state.originals:
            ck = _cls_key(artist)
            try:
                restore = _RESTORE.get((ck, key[1]))
                if restore is not None:
                    restore(artist, orig)
                else:
                    setter = HANDLERS[(ck, key[1])][1]
                    if getattr(setter, "_needs_state", False):
                        setter(artist, orig, state)
                    else:
                        setter(artist, orig)
            except Exception as exc:  # noqa: BLE001 — 单条失败不拖垮整次渲染
                warnings.append(f"还原失败 {key[0]}.{key[1]}: {exc}")
        state.applied.pop(key)
        state.originals.pop(key, None)

    # 应用新值
    for key, value in new.items():
        gid, prop = key
        artist = state.index.get(gid)
        if artist is None:
            warnings.append(f"元素不存在（脚本可能已改动）: {gid}")
            continue
        handler = HANDLERS.get((_cls_key(artist), prop))
        if handler is None:
            warnings.append(f"属性不支持: {gid}.{prop}")
            continue
        if state.applied.get(key) == value:
            continue
        getter, setter = handler
        try:
            if key not in state.originals:
                state.originals[key] = getter(artist)
            if getattr(setter, "_needs_state", False):
                setter(artist, value, state)
            else:
                setter(artist, value)
            state.applied[key] = value
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"应用失败 {gid}.{prop}: {exc}")

    return warnings
