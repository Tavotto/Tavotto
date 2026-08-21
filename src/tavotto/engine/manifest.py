"""元素清单（worker 子进程内使用）。

instrument(state)：build 后调用一次——走 Figure 的 artist 树，
按确定性树序赋 gid（axes_0.title / axes_0.texts_2 / fig.texts_0 …），
把可编辑元素登记进 FigState。

build_manifest(state)：每次渲染后调用——读取元素当前属性值与 bbox
（figure 分数坐标、top-origin），产出发给前端的 manifest dict。
"""
from __future__ import annotations

import math
import sys

from matplotlib.axes import Axes
from matplotlib.collections import (Collection, LineCollection, PathCollection,
                                    PolyCollection)
from matplotlib.container import BarContainer, ErrorbarContainer
from matplotlib.patches import FancyArrowPatch, Patch
from matplotlib.text import Text

import pathgeom
from overrides import (ColorbarProxy, FigState, HANDLERS, SeriesGroup, TickLabel,
                       TickSet, _ARROWSTYLES, _CB_EXTENDS, _LEGEND_LOCS,
                       _TICK_FORMATS, _TICK_MINOR_FORMATS,
                       _arrow_style, _arrowstyle_name, _axis_arrows_on,
                       _linestyle_name, _linecoll_linestyle_name,
                       _boxstyle_info, _cb_axis, _cb_tick_color,
                       _cb_tick_fontsize, _cls_key, _grid_prop, _grid_visible,
                       _legend_entry_order, _legend_loc_name,
                       _stroke_state, _tick0, colorbar_maps, follow_map,
                       gradient_base_hex, scale_options, text_linespacing,
                       spine_all_color, spine_all_width, spine_cfg,
                       spine_side_color, spine_side_width, tick_cfg,
                       tick_format_name, tick_major_mode, tick_major_step,
                       tick_major_values, tick_minor_format, tick_minor_mode,
                       tick_minor_step, tick_minor_visible, to_hex)

CMAPS = ["viridis", "plasma", "inferno", "magma", "cividis", "Greys", "gray",
         "hot", "afmhot", "coolwarm", "RdBu_r", "seismic", "jet", "turbo"]

_SKIP_LABELS = ("_child", "_nolegend_")


def _snippet(text: str, n: int = 18) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _relabel(registered: str, text: str) -> str:
    """把登记名里引号中的那段换成当前文字（前缀是角色名，原样保留）。"""
    head = registered.split("“", 1)[0]
    return f"{head}“{_snippet(text)}”"


def _register(state: FigState, gid: str, artist, role: str, label: str,
              draggable: bool = False, **flags) -> None:
    """登记一个可编辑元素。

    `flags` 是挂在元素上的**编辑能力标记**（`position_locked` /
    `limits_slaved`），由 `_fields_for` 与 `build_manifest` 读取。它们必须
    挂在元素上而不是现算：`_fields_for(el)` 只拿得到 el，而「这个 axes 是不是
    子 axes」是遍历时才知道的信息。`sync_tick_elements` 重建刻度伪元素时
    原样保留非 TickLabel 的元素对象，所以标记不会在同步中丢。
    """
    artist.set_gid(gid)
    state.index[gid] = artist
    state.elements.append({"gid": gid, "artist": artist, "role": role,
                           "label": label, "draggable": draggable, **flags})


def _ordered_axes(fig) -> tuple[list, set]:
    """(全部 axes（含子 axes）, 子 axes 的 id 集合)。

    `fig.axes` 只收 `add_subplot` / `add_axes` 建出来的那些；
    `ax.inset_axes(...)` 与 `ax.secondary_[xy]axis(...)` 建出来的挂在
    `ax.child_axes` 上，`in fig.axes` 为 False——不遍历它们的话，插图里的
    曲线选不中、次坐标轴的标签也改不了。

    **子 axes 一律排在所有 `fig.axes` 之后**，编号继续 `axes_{i}`。这条不是
    风格问题：`axes_i` 会进用户文档（override 的 gid），存量文档里的编号
    一个字节都不能变。插在中间会让「同一张图、同一个 gid」在升级前后指向
    不同的 axes——那是数据级的错位。

    逐层广度优先（同一层的兄弟排完再下一层），所以同一个脚本每次跑出来的
    gid 串完全一致；插图里再开插图也照样确定。按 `id()` 去重防环。
    """
    out = list(fig.axes)
    seen = {id(a) for a in out}
    children: set = set()
    layer = list(out)
    while layer:
        nxt = []
        for parent in layer:
            for child in getattr(parent, "child_axes", None) or []:
                if id(child) in seen:
                    continue
                seen.add(id(child))
                children.add(id(child))
                out.append(child)
                nxt.append(child)
        layer = nxt
    return out, children


def _is_secondary_axis(ax) -> bool:
    """是不是 `secondary_[xy]axis()` 建出来的轴。

    **这一条只能按类判**，与 position 那条不同：position 的理由（落位由
    locator 每帧重算）对插图与次坐标轴是同一个，而「数据范围由父轴经换算
    函数每帧重算」只对次坐标轴成立。行为上没有公开的判据可用。

    `matplotlib.axes.SecondaryAxis` **不是公开名字**（3.8 上 import 得到、
    3.11 上 import 不到），所以走私有模块路径，再退回类名字符串。两条都
    失效时返回 False——那时最坏的后果是次坐标轴上多出一组会被顶回去的
    数据范围字段，不会崩。`test_secondary_axis_detection_still_works`
    看护这条依赖，matplotlib 升版把它弄坏时会当场红。
    """
    try:
        from matplotlib.axes._secondary_axes import SecondaryAxis
    except ImportError:                                 # pragma: no cover - 版本相关
        return type(ax).__name__ == "SecondaryAxis"
    return isinstance(ax, SecondaryAxis)


def _tick_label_entries(ax, which: str, ax_gid: str) -> list[tuple]:
    """该轴上每个**有文字**的主刻度 → (gid, TickLabel 伪元素, 显示名)。

    序号 j 是 `get_[xyz]ticklabels()` 里的下标——`TickLabel.live()` 也按它取，
    两边必须是同一个口径，否则「第 j 个刻度」在登记与应用时指的不是同一条。
    """
    raw = getattr(ax, f"get_{which}ticklabels")()
    return [(f"{ax_gid}.{which}ticklabels_{j}", TickLabel(ax, which, j),
             f"刻度 “{_snippet(t.get_text())}”")
            for j, t in enumerate(raw) if t.get_text()]


def _sync_tick_labels(state: FigState, ax, which: str, ax_gid: str) -> None:
    for gid, tl, label in _tick_label_entries(ax, which, ax_gid):
        _register(state, gid, tl, "ticklabel", label)


def sync_tick_elements(state: FigState) -> None:
    """把 ticklabel 伪元素与**当前**刻度状态对齐（每次 build_manifest 里跑一次）。

    刻度不是常驻 artist：改 xlim、换 locator、翻转色条方向，都会让 matplotlib
    把整组刻度重来。`instrument` 只在 build 那一刻登记过一次，之后**新出现**
    的刻度既选不中也改不了，**消失**的那些则会把已有 override 静默吞掉。

    这里按当前状态重建每条轴的 ticklabel 块，并且是**就地替换**（插在它原来
    所属的刻度组后面），所以 manifest 的元素顺序不会因为同步而变——热会话与
    全量重放的元素顺序仍然逐位一致。已经不存在的 gid 从 `state.index` 里摘掉，
    它的 override 于是变成界面上可见、可清理的孤儿，而不是一条永远不生效
    却毫无提示的记录。
    """
    out: list[dict] = []
    for el in state.elements:
        if isinstance(el["artist"], TickLabel):
            continue                      # 旧的一律丢掉，按当前状态重发
        out.append(el)
        ts = el["artist"]
        if isinstance(ts, TickSet):
            ax_gid = el["gid"].rsplit(".", 1)[0]
            for gid, tl, label in _tick_label_entries(ts.ax, ts.which, ax_gid):
                state.index[gid] = tl
                out.append({"gid": gid, "artist": tl, "role": "ticklabel",
                            "label": label, "draggable": False})
    live = {el["gid"] for el in out}
    for gid in [g for g, a in state.index.items() if isinstance(a, TickLabel)]:
        if gid not in live:
            state.index.pop(gid, None)
    state.elements = out


def instrument(state: FigState) -> None:
    fig = state.fig
    state.elements.clear()
    state.index.clear()

    # figure 本体（点击空白处选中，可改尺寸）——不占用 artist gid
    state.index["figure"] = fig
    state.elements.append({"gid": "figure", "artist": fig, "role": "figure",
                           "label": "整张图", "draggable": False})

    for i, t in enumerate(fig.texts):
        if t.get_text():
            _register(state, f"fig.texts_{i}", t, "text",
                      f"文字 “{_snippet(t.get_text())}”", draggable=True)
    for i, leg in enumerate(getattr(fig, "legends", []) or []):
        _register(state, f"fig.legend_{i}", leg, "legend", "图例", draggable=True)

    # 色条反查：mappable.colorbar → 宿主轴（与色条方向事务共用同一份实现）
    cbar_of_ax, host_of_cbax = colorbar_maps(fig)
    state.colorbar_axes = set(cbar_of_ax)
    state.axes_follow = follow_map(fig, cbar_of_ax, host_of_cbax)
    # `fig.axes` 之后再接子 axes（inset / secondary），编号继续往下走——
    # 存量文档里的 axes_i 因此一个字节不变，见 `_ordered_axes`。
    all_axes, child_ids = _ordered_axes(fig)
    gid_of_ax = {ax: f"axes_{i}" for i, ax in enumerate(all_axes)}
    cbar_ordinal: dict[int, int] = {}
    # 插图与次坐标轴**各数各的**：共用一个计数器会让「只有一个次坐标轴」的图
    # 上出现「次坐标轴 2」，因为前面那个 1 被插图占掉了。
    child_ordinal: dict[str, int] = {"inset": 0, "secondary": 0}

    for i, ax in enumerate(all_axes):
        is3d = getattr(ax, "name", "") == "3d"
        is_child = id(ax) in child_ids
        secondary = is_child and _is_secondary_axis(ax)
        # **落位不给编辑**：子 axes 的位置由父级的 `_axes_locator` 每帧重算，
        # `set_position` 之后立刻读回是新值、`draw()` 一次就被顶回原值（实测）。
        # 开放它就是「设了、界面也变了、下一帧弹回去」——比不支持严重得多。
        # 判据是「子 axes **且** 有 locator」而不是光看 locator：色条轴也带
        # `_ColorbarAxesLocator`，而色条的 position override 是**支持**的
        # （用户自己摆过色条时就靠它），光判 locator 会把那条功能一起砍掉。
        position_locked = is_child and ax.get_axes_locator() is not None
        if is_child:
            kind = "secondary" if secondary else "inset"
            child_ordinal[kind] += 1
            label = (f"次坐标轴 {child_ordinal['secondary']}" if secondary
                     else f"插图 {child_ordinal['inset']}")
        else:
            label = "色条轴" if ax in cbar_of_ax else f"子图 {i + 1}"
        _register(state, f"axes_{i}", ax, "axes3d" if is3d else "axes", label,
                  position_locked=position_locked, limits_slaved=secondary)
        if ax in cbar_of_ax:
            host = host_of_cbax.get(ax)
            n = cbar_ordinal.get(id(host), 0)
            cbar_ordinal[id(host)] = n + 1
            proxy = ColorbarProxy(cbar_of_ax[ax], host, f"axes_{i}",
                                  gid_of_ax.get(host, ""), n)
            _register(state, f"axes_{i}.colorbar", proxy, "colorbar", "色条")
            # 语义身份也进 index：`axes_i.colorbar` 是按邻居排序编出来的名字，
            # 语义身份（宿主 + 序号）才是「这是谁的色条」。两个 gid 指同一个
            # 代理对象，旧文档与将来可能的重建都认得出同一条色条。
            state.index[proxy.identity] = proxy
        for suffix, t in (("title", ax.title),
                          ("title_left", getattr(ax, "_left_title", None)),
                          ("title_right", getattr(ax, "_right_title", None))):
            if t is not None and t.get_text():
                t._mm_drag = ("title", ax)  # noqa: SLF001 — 拖动需绕过自动定位
                _register(state, f"axes_{i}.{suffix}", t, "title",
                          f"标题 “{_snippet(t.get_text())}”", draggable=True)
        label_axes = [("x", ax.xaxis), ("y", ax.yaxis)]
        if is3d and getattr(ax, "zaxis", None) is not None:
            label_axes.append(("z", ax.zaxis))
        for name, axis in label_axes:
            t = axis.label
            # **无条件登记**（此刻空着的也登记）：色条方向一翻，长轴标签就从
            # ylabel 搬到 xlabel 上，而 xlabel 在 build 那一刻是空的——只登记
            # 「现在有字的」会让翻转之后那行字整个从元素表里消失，选不中也改不了。
            # 当下真的没有文字的，build_manifest 量到零尺寸包围盒会自动丢掉，
            # 界面上不会凭空多出条目。
            if is3d and not t.get_text():
                continue
            if is3d:
                # mplot3d 每次 draw 按投影轴线重算标签位置，set_label_coords
                # 会被覆盖——3D 轴标签不可拖，位置微调走 labelpad（推远/拉近）
                t._mm_axis = axis  # noqa: SLF001 — labelpad 字段/handler 反查轴
                _register(state, f"axes_{i}.{name}label", t, "axis_label",
                          f"{name.upper()} 轴 “{_snippet(t.get_text())}”")
            else:
                t._mm_drag = (f"{name}label", ax)  # noqa: SLF001
                _register(state, f"axes_{i}.{name}label", t, "axis_label",
                          f"{name.upper()} 轴 “{_snippet(t.get_text())}”",
                          draggable=True)
        for j, t in enumerate(ax.texts):
            if t.get_text():
                _register(state, f"axes_{i}.texts_{j}", t, "text",
                          f"文字 “{_snippet(t.get_text())}”", draggable=True)
            # annotate(...) 的箭头单独成元素；`annotate("", …)` 纯箭头也要能选中
            ap = getattr(t, "arrow_patch", None)
            if ap is not None:
                _register(state, f"axes_{i}.texts_{j}.arrow", ap,
                          "arrow_patch", "标注箭头")
        if not is3d:
            # 数据系列容器先注册（其成员不再作为独立曲线/集合重复注册）
            skip_ids: set[int] = set()
            for j, cont in enumerate(getattr(ax, "containers", []) or []):
                if isinstance(cont, BarContainer):
                    grp = SeriesGroup("bar_series", list(cont.patches), cont)
                    lab = str(cont.get_label() or "")
                    nice = f"柱形系列 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"柱形系列 {j + 1}"
                    _register(state, f"axes_{i}.barseries_{j}", grp, "bar_series", nice)
                    for k, rect in enumerate(cont.patches):
                        rect._mm_bar = True  # noqa: SLF001 — _cls_key 识别标记
                        skip_ids.add(id(rect))   # 柱也在 ax.patches 里，别再当独立形状登记
                        _register(state, f"axes_{i}.barseries_{j}.bar_{k}", rect,
                                  "bar", f"柱 {k + 1}")
                elif isinstance(cont, ErrorbarContainer):
                    line, caps, bars = cont.lines
                    grp = SeriesGroup("errorbar",
                                      {"line": line, "caps": list(caps), "bars": list(bars)},
                                      cont)
                    _register(state, f"axes_{i}.errorbar_{j}", grp, "errorbar",
                              f"误差棒 {j + 1}")
                    for m in grp.members():
                        skip_ids.add(id(m))
            for j, ln in enumerate(ax.lines):
                if id(ln) in skip_ids:
                    continue
                lab = str(ln.get_label())
                nice = f"曲线 “{_snippet(lab)}”" if lab and not lab.startswith("_") else f"曲线 {j + 1}"
                _register(state, f"axes_{i}.lines_{j}", ln, "line", nice)
            for j, im in enumerate(ax.images):
                _register(state, f"axes_{i}.images_{j}", im, "image", f"图像 {j + 1}")
            for j, coll in enumerate(ax.collections):
                if id(coll) in skip_ids:
                    continue
                if isinstance(coll, PathCollection):
                    lab = str(coll.get_label())
                    nice = f"散点 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"散点系列 {j + 1}"
                    _register(state, f"axes_{i}.scatter_{j}", coll, "scatter", nice)
                elif isinstance(coll, PolyCollection):
                    _register(state, f"axes_{i}.fill_{j}", coll, "fill", f"填充区域 {j + 1}")
                elif isinstance(coll, LineCollection) and coll.get_array() is None:
                    # 线组：`hlines`/`vlines` 的参考线、`stem` 的竖线、
                    # `eventplot` 的事件线（EventCollection 是它的子类）、
                    # `streamplot` 的流线、`violinplot` 的极值线。这是 artist
                    # 普查里权重最高的缺口（8 处 / 5 个 case），2026-08-21 之前
                    # 它们在界面上根本不存在。
                    #
                    # **标量映射的一律不登记**（`get_array()` 非空）：颜色由
                    # colormap 每次 draw 重算（`update_scalarmappable`），开放
                    # `color` 会「设了但下一帧被顶回去」——那比不支持更坏。
                    # 这道闸是唯一出处，`overrides._cls_key` 不重复判。
                    # 等值线不受影响：`contour`/`contourf` 在 3.8 与 3.11 上都
                    # 只产出**一个** `QuadContourSet`，它既不是 LineCollection
                    # 子类、又是标量映射的，两条判据各自都挡得住（实测）。
                    lab = str(coll.get_label())
                    nice = f"线组 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"线组 {j + 1}"
                    _register(state, f"axes_{i}.linecoll_{j}", coll, "linecoll", nice)
            # 脚本直接 add_patch 的独立箭头（XPS 峰位标注这类画法）与独立形状。
            # 形状这一档**认任何 Patch**，不只是 Polygon / PathPatch：
            # `Rectangle`（axhspan/axvspan）、`Circle`、`Ellipse`、`Wedge`
            # （ax.pie）同样是用户画出来的东西，只认两种的话它们在界面上根本
            # 不存在——CompatBench 的 art_shapes / art_axhspan_axvspan /
            # art_pie 就是这么现形的。`patch` 那组 handler（facecolor /
            # edgecolor / linewidth / linestyle / alpha / visible / zorder）
            # 本来就建在 Patch 的通用 API 上，泛化不需要新写 handler。
            # gid 用 patches 里的树序 j 保证重建稳定，label 各自计数。
            # 柱形系列的 Rectangle 也在 ax.patches 里，已经登记过，这里必须
            # 跳过（skip_ids 收了它们）；FancyArrowPatch 在上一支被拦掉，
            # 它有自己的端点契约；色条轴上的 patch 由 is_cbax 挡住。
            arrow_n = 0
            shape_n = 0
            # 色条轴上的 patch 不是用户的形状：`extend` 的两个延伸三角就是
            # PathPatch，而且每次 `_draw_all()` 都会被删掉重建——登记它们等于
            # 在元素表里放两个随时换身份的幽灵条目
            is_cbax = ax in cbar_of_ax
            for j, pt in enumerate(ax.patches):
                if isinstance(pt, FancyArrowPatch):
                    arrow_n += 1
                    # 独立箭头的端点归自己管（set_positions 持久生效），可拖；
                    # annotate 的 arrow_patch 每次 draw 被注释机制重定位，不标
                    pt._mm_arrow_standalone = True  # noqa: SLF001
                    _register(state, f"axes_{i}.arrows_{j}", pt,
                              "arrow_patch", f"箭头 {arrow_n}")
                elif (isinstance(pt, Patch)
                      and id(pt) not in skip_ids and not is_cbax):
                    shape_n += 1
                    _register(state, f"axes_{i}.patches_{j}", pt, "patch",
                              f"形状 {shape_n}")
        leg = ax.get_legend()
        if leg is not None:
            _register(state, f"axes_{i}.legend", leg, "legend", "图例", draggable=True)
            title = leg.get_title()
            if title is not None and title.get_text():
                _register(state, f"axes_{i}.legend.title", title, "legend_text",
                          f"图例标题 “{_snippet(title.get_text())}”")
            for j, t in enumerate(leg.get_texts()):
                if t.get_text():
                    _register(state, f"axes_{i}.legend.texts_{j}", t, "legend_text",
                              f"图例项 “{_snippet(t.get_text())}”")
        if not is3d:
            # 边框模型的「脚本原样」也在这里采（与刻度模型同一时机：build 之后、
            # 任何 override 之前）
            spine_cfg(ax)
        tick_axes = (("x", "X"), ("y", "Y"), ("z", "Z")) if is3d else (("x", "X"), ("y", "Y"))
        for which, cn in tick_axes:
            if getattr(ax, f"{which}axis", None) is None:
                continue
            # 刻度模型的「脚本原样」在这里采：build 之后、任何 override 之前，
            # 采到的才是脚本自己那套 locator/formatter（见 overrides.tick_cfg）
            tick_cfg(ax, which)
            # **无条件登记**刻度组：此刻没有刻度不代表以后没有——色条方向一翻，
            # 长短轴互换，原来空着的那条轴就成了带刻度的那条。build_manifest 会
            # 把当下真的没有刻度的组丢掉，所以多登记一个不会在界面上多出东西
            _register(state, f"axes_{i}.{which}ticks", TickSet(ax, which), "ticks",
                      f"{cn} 刻度文字")
            _sync_tick_labels(state, ax, which, f"axes_{i}")


# ---------------------------------------------------------------------------
# 每类元素暴露的可编辑字段（读取当前值）
# ---------------------------------------------------------------------------
def _text_fields(t) -> list[dict]:
    alpha = t.get_alpha()
    fam = (t.get_fontfamily() or ["serif"])[0]
    fam_opts = ["serif", "sans-serif", "monospace", "Times New Roman", "Arial", "Helvetica"]
    if fam not in fam_opts:
        fam_opts = [fam] + fam_opts
    patch = t.get_bbox_patch()
    if patch is not None:
        pad, rounded = _boxstyle_info(patch)
        bb = {"visible": bool(patch.get_visible()),
              "face": to_hex(patch.get_facecolor()),
              "edge": to_hex(patch.get_edgecolor()),
              "lw": round(float(patch.get_linewidth()), 2),
              "alpha": 1.0 if patch.get_alpha() is None else round(float(patch.get_alpha()), 2),
              "pad": round(pad, 2), "rounded": rounded}
    else:
        bb = {"visible": False, "face": "#FFFFFF", "edge": "#000000",
              "lw": 0.0, "alpha": 1.0, "pad": 0.3, "rounded": False}
    st = _stroke_state(t)
    axis3d = getattr(t, "_mm_axis", None)  # 3D 轴标签：labelpad 是唯一的位置旋钮
    return [
        {"prop": "text", "type": "text", "value": t.get_text()},
        *([{"prop": "labelpad", "type": "number",
            "value": round(float(axis3d.labelpad), 1),
            "min": -30, "max": 60, "step": 1, "unit": "pt"}]
          if axis3d is not None else []),
        {"prop": "fontsize", "type": "number", "value": round(float(t.get_fontsize()), 2),
         "min": 3, "max": 36, "step": 0.5, "unit": "pt"},
        {"prop": "color", "type": "color", "value": to_hex(t.get_color())},
        {"prop": "weight", "type": "enum", "value": str(t.get_fontweight()),
         "options": ["normal", "bold"]},
        {"prop": "style", "type": "enum", "value": str(t.get_fontstyle()),
         "options": ["normal", "italic"]},
        {"prop": "fontfamily", "type": "enum", "value": str(fam), "options": fam_opts},
        {"prop": "rotation", "type": "number", "value": round(float(t.get_rotation()), 1),
         "min": -180, "max": 180, "step": 5, "unit": "°"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if alpha is None else round(float(alpha), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(t.get_visible())},

        {"prop": "ha", "type": "enum", "value": str(t.get_ha()),
         "options": ["left", "center", "right"], "group": "排版"},
        {"prop": "va", "type": "enum", "value": str(t.get_va()),
         "options": ["top", "center", "bottom", "baseline"], "group": "排版"},
        {"prop": "linespacing", "type": "number",
         "value": round(text_linespacing(t), 2),
         "min": 0.5, "max": 3, "step": 0.05, "group": "排版"},
        {"prop": "zorder", "type": "number", "value": round(float(t.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排版"},

        {"prop": "bbox_visible", "type": "bool", "value": bb["visible"], "group": "背景"},
        {"prop": "bbox_facecolor", "type": "color", "value": bb["face"], "group": "背景"},
        {"prop": "bbox_alpha", "type": "number", "value": bb["alpha"],
         "min": 0, "max": 1, "step": 0.05, "group": "背景"},
        {"prop": "bbox_edgecolor", "type": "color", "value": bb["edge"], "group": "背景"},
        {"prop": "bbox_linewidth", "type": "number", "value": bb["lw"],
         "min": 0, "max": 3, "step": 0.25, "unit": "pt", "group": "背景"},
        {"prop": "bbox_pad", "type": "number", "value": bb["pad"],
         "min": 0, "max": 2, "step": 0.05, "group": "背景"},
        {"prop": "bbox_rounded", "type": "bool", "value": bb["rounded"], "group": "背景"},

        {"prop": "stroke_enabled", "type": "bool", "value": bool(st["enabled"]), "group": "描边"},
        {"prop": "stroke_color", "type": "color", "value": to_hex(st["color"]), "group": "描边"},
        {"prop": "stroke_width", "type": "number", "value": round(float(st["width"]), 2),
         "min": 0.25, "max": 6, "step": 0.25, "unit": "pt", "group": "描边"},
    ]


def _line_fields(ln) -> list[dict]:
    lab = str(ln.get_label())
    marker = str(ln.get_marker())
    m_opts = ["None", "o", "s", "D", "^", "v", "<", ">", "x", "+", "*", "."]
    if marker not in m_opts:
        m_opts = [marker] + m_opts
    return [
        {"prop": "label", "type": "text", "value": "" if lab.startswith("_") else lab},
        {"prop": "color", "type": "color", "value": to_hex(ln.get_color())},
        {"prop": "linewidth", "type": "number", "value": round(float(ln.get_linewidth()), 2),
         "min": 0.1, "max": 8, "step": 0.1, "unit": "pt"},
        {"prop": "linestyle", "type": "enum", "value": str(ln.get_linestyle()),
         "options": ["-", "--", ":", "-."]},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if ln.get_alpha() is None else round(float(ln.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(ln.get_visible())},
        {"prop": "marker", "type": "enum", "value": marker, "options": m_opts,
         "group": "线条与标记"},
        {"prop": "markersize", "type": "number", "value": round(float(ln.get_markersize()), 2),
         "min": 0, "max": 20, "step": 0.5, "unit": "pt", "group": "线条与标记"},
        {"prop": "markerfacecolor", "type": "color",
         "value": to_hex(ln.get_markerfacecolor()), "group": "线条与标记"},
        {"prop": "markeredgecolor", "type": "color",
         "value": to_hex(ln.get_markeredgecolor()), "group": "线条与标记"},
        {"prop": "zorder", "type": "number", "value": round(float(ln.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


def _collection_fields(coll, with_size: bool) -> list[dict]:
    import numpy as np  # noqa: PLC0415 — worker 侧有科学栈
    fc = coll.get_facecolor()
    ec = coll.get_edgecolor()
    lw = coll.get_linewidths()
    lab = str(coll.get_label())
    fields = [
        {"prop": "label", "type": "text", "value": "" if lab.startswith("_") else lab},
        {"prop": "facecolor", "type": "color",
         "value": to_hex(fc[0]) if len(fc) else "#000000"},
        {"prop": "edgecolor", "type": "color",
         "value": to_hex(ec[0]) if len(ec) else "#000000"},
        {"prop": "linewidth", "type": "number",
         "value": round(float(lw[0]), 2) if len(lw) else 0.0,
         "min": 0, "max": 8, "step": 0.1, "unit": "pt"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if coll.get_alpha() is None else round(float(coll.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(coll.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(coll.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]
    if with_size:
        sizes = coll.get_sizes()
        fields.insert(2, {"prop": "size", "type": "number",
                          "value": round(float(np.mean(sizes)), 1) if len(sizes) else 20.0,
                          "min": 1, "max": 400, "step": 1, "unit": "pt²"})
        # marker 形状可整体替换（set_paths）；"original" = 脚本原始路径
        cur = getattr(coll, "_mm_marker", None) or "original"
        m_opts = ["original", "o", "s", "D", "^", "v", "<", ">", "x", "+", "*", ".",
                  "p", "h"]
        fields.insert(3, {"prop": "marker", "type": "enum", "value": cur,
                          "options": ([cur] if cur not in m_opts else []) + m_opts})
    else:
        fields.pop(0)  # fill 无 label 语义
    return fields


def _linecoll_fields(coll) -> list[dict]:
    """线组（LineCollection）暴露的可编辑字段。

    **只有样式**。「几条线、落在哪」是脚本的数据，改它该回代码——与 3D 盒内
    属性、散点数据同一条产品边界。整组共用一套样式，单条线不可分别编辑
    （matplotlib 允许逐条上色，但那属于数据表达，不是界面旋钮）。

    `linestyle` 的显示值按**未缩放**规格反查成枚举名；认不出的自定义 dash
    显示成实线占位（与 Line2D 那条同一约定）。还原走的是
    `HANDLERS["linecoll","linestyle"]` 的 getter，存的是未缩放规格本身，
    与这里的显示值不是同一个东西——显示可以有损，还原不行。
    """
    import numpy as np  # noqa: PLC0415 — worker 侧有科学栈
    # `get_color()` 的形状**不统一**：`hlines` 出的 LineCollection 回二维
    # `[[r,g,b,a]]`，而 `eventplot` 出的 EventCollection 回一维 `[r,g,b,a]`
    # （实测，两个 matplotlib 版本都如此）。直接取 `colors[0]` 在后者身上
    # 拿到的是一个浮点数，`to_hex` 会把它变成一个毫无意义的颜色。
    colors = np.atleast_2d(coll.get_color())
    lw = coll.get_linewidths()
    alpha = coll.get_alpha()
    return [
        {"prop": "color", "type": "color",
         "value": to_hex(colors[0]) if len(colors) else "#000000"},
        {"prop": "linewidth", "type": "number",
         "value": round(float(lw[0]), 2) if len(lw) else 1.0,
         "min": 0, "max": 8, "step": 0.1, "unit": "pt"},
        {"prop": "linestyle", "type": "enum",
         "value": _linecoll_linestyle_name(coll),
         "options": ["-", "--", "-.", ":"]},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if alpha is None else round(float(alpha), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(coll.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(coll.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


def _bar_series_fields(grp) -> list[dict]:
    rects = grp.artists
    r0 = rects[0] if rects else None
    if r0 is None:
        return []
    lab = str(grp.container.get_label() or "") if grp.container is not None else ""
    return [
        {"prop": "label", "type": "text", "value": "" if lab.startswith("_") else lab},
        {"prop": "facecolor", "type": "color", "value": to_hex(r0.get_facecolor())},
        {"prop": "edgecolor", "type": "color", "value": to_hex(r0.get_edgecolor())},
        {"prop": "linewidth", "type": "number", "value": round(float(r0.get_linewidth()), 2),
         "min": 0, "max": 5, "step": 0.1, "unit": "pt"},
        {"prop": "bar_width", "type": "number", "value": round(float(r0.get_width()), 3),
         "min": 0.01, "max": 5, "step": 0.02, "unit": "数据单位"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if r0.get_alpha() is None else round(float(r0.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(r0.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(r0.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


def _bar_fields(rect) -> list[dict]:
    return [
        {"prop": "facecolor", "type": "color", "value": to_hex(rect.get_facecolor())},
        {"prop": "edgecolor", "type": "color", "value": to_hex(rect.get_edgecolor())},
        {"prop": "linewidth", "type": "number", "value": round(float(rect.get_linewidth()), 2),
         "min": 0, "max": 5, "step": 0.1, "unit": "pt"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if rect.get_alpha() is None else round(float(rect.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(rect.get_visible())},
    ]


def _errorbar_fields(grp) -> list[dict]:
    line = grp.artists.get("line")
    caps = grp.artists["caps"]
    probe = line if line is not None else (caps[0] if caps else None)
    if probe is None and grp.artists["bars"]:
        probe = grp.artists["bars"][0]
    if probe is None:
        return []
    color = probe.get_color()
    if hasattr(color, "__len__") and not isinstance(color, str) and len(color) \
            and not isinstance(color[0], (int, float)):
        color = color[0]
    cap0 = caps[0] if caps else None
    lw = probe.get_linewidth()
    if hasattr(lw, "__len__"):
        lw = lw[0] if len(lw) else 1.0
    return [
        {"prop": "color", "type": "color", "value": to_hex(color)},
        {"prop": "linewidth", "type": "number", "value": round(float(lw), 2),
         "min": 0.1, "max": 5, "step": 0.1, "unit": "pt"},
        {"prop": "capsize", "type": "number",
         "value": round(float(cap0.get_markersize()), 2) if cap0 is not None else 0.0,
         "min": 0, "max": 15, "step": 0.5, "unit": "pt"},
        {"prop": "cap_thickness", "type": "number",
         "value": round(float(cap0.get_markeredgewidth()), 2) if cap0 is not None else 1.0,
         "min": 0.1, "max": 5, "step": 0.1, "unit": "pt"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if probe.get_alpha() is None else round(float(probe.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(probe.get_visible())},
    ]


def _cmap_options(current: str) -> list[str]:
    return ([current] if current not in CMAPS else []) + CMAPS


def _arrowpatch_fields(a) -> list[dict]:
    """图内箭头（FancyArrowPatch）：样式 / 颜色 / 线宽 / 帽大小 / 线型 /
    透明度 / 显隐。独立箭头（脚本 add_patch 的）另有端点可在画布上直接拖动
    （manifest 的 arrow_endpoints + endpoints_frac override）；annotate 的箭头
    端点由注释机制每次 draw 重定位，只放样式。"""
    alpha = a.get_alpha()
    style = _arrowstyle_name(a)
    style_opts = ([style] if style not in _ARROWSTYLES else []) + _ARROWSTYLES
    return [
        {"prop": "arrowstyle", "type": "enum", "value": style, "options": style_opts},
        {"prop": "color", "type": "color", "value": to_hex(a.get_edgecolor())},
        {"prop": "linewidth", "type": "number",
         "value": round(float(a.get_linewidth()), 2),
         "min": 0.1, "max": 6, "step": 0.05, "unit": "pt"},
        {"prop": "mutation_scale", "type": "number",
         "value": round(float(a.get_mutation_scale()), 1),
         "min": 1, "max": 40, "step": 0.5, "unit": "pt"},
        {"prop": "linestyle", "type": "enum", "value": _linestyle_name(a),
         "options": ["-", "--", "-.", ":"]},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if alpha is None else round(float(alpha), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "zorder", "type": "number", "value": round(float(a.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
        {"prop": "visible", "type": "bool", "value": bool(a.get_visible())},
    ]


def _patch_fields(pt) -> list[dict]:
    """独立形状 patch（`ax.fill()` 的 Polygon / 手搓的 PathPatch）。

    几何不给编辑字段——它由脚本的数据决定，改它等于改数据。选中 / 命中 /
    框选靠 manifest 的 `geometry`（沿真实闭合路径），样式在这里。
    """
    alpha = pt.get_alpha()
    return [
        {"prop": "facecolor", "type": "color", "value": to_hex(pt.get_facecolor())},
        {"prop": "fill", "type": "bool", "value": bool(pt.get_fill())},
        {"prop": "edgecolor", "type": "color", "value": to_hex(pt.get_edgecolor())},
        {"prop": "linewidth", "type": "number", "value": round(float(pt.get_linewidth()), 2),
         "min": 0, "max": 8, "step": 0.1, "unit": "pt"},
        {"prop": "linestyle", "type": "enum", "value": _linestyle_name(pt),
         "options": ["-", "--", "-.", ":"]},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if alpha is None else round(float(alpha), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(pt.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(pt.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


def _image_fields(im) -> list[dict]:
    arr = im.get_array()
    mappable = arr is not None and getattr(arr, "ndim", 0) == 2
    fields = []
    # 单色渐变位图（imshow 渐变 + 裁剪路径的「形状渐变填充」画法）：
    # 基色可整体替换，渐变形状与透明度原样保留。不是这种图就不出字段。
    grad = None if mappable else gradient_base_hex(im)
    if grad is not None:
        fields.append({"prop": "gradient_color", "type": "color", "value": grad,
                       "group": "渐变填充"})
    if mappable:
        vmin, vmax = im.get_clim()
        span = abs(float(vmax) - float(vmin)) if vmin is not None and vmax is not None else 1.0
        step = max(span / 100.0, 1e-6)
        cname = im.get_cmap().name
        fields += [
            {"prop": "cmap", "type": "enum", "value": cname,
             "options": _cmap_options(cname), "group": "颜色映射"},
            {"prop": "vmin", "type": "number",
             "value": None if vmin is None else round(float(vmin), 4),
             "step": round(step, 4), "group": "颜色映射"},
            {"prop": "vmax", "type": "number",
             "value": None if vmax is None else round(float(vmax), 4),
             "step": round(step, 4), "group": "颜色映射"},
        ]
    interp = str(im.get_interpolation())
    i_opts = ["auto", "nearest", "bilinear", "bicubic", "lanczos", "none"]
    if interp not in i_opts:
        i_opts = [interp] + i_opts
    fields += [
        {"prop": "interpolation", "type": "enum", "value": interp, "options": i_opts},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if im.get_alpha() is None else round(float(im.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "origin", "type": "enum", "value": str(im.origin),
         "options": ["upper", "lower"], "group": "高级"},
        {"prop": "zorder", "type": "number", "value": round(float(im.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
        {"prop": "visible", "type": "bool", "value": bool(im.get_visible())},
    ]
    return fields


def _colorbar_fields(p) -> list[dict]:
    cb = p.cb
    vmin, vmax = cb.mappable.get_clim()
    span = abs(float(vmax) - float(vmin)) if vmin is not None and vmax is not None else 1.0
    step = max(span / 100.0, 1e-6)
    cname = cb.mappable.get_cmap().name
    return [
        {"prop": "label", "type": "text", "value": _cb_axis(p).label.get_text()},
        # 方向：就地结构改造（长短轴互换 + 重画色带 + 刻度换轴），实现见
        # overrides._cb_reorient。`fig.axes` 顺序不动，所以 gid / 撤销 / 写回照旧
        {"prop": "orientation", "type": "enum",
         "value": str(getattr(cb, "orientation", "vertical")),
         "options": ["vertical", "horizontal"]},
        # 两端的延伸三角（「超出色阶的值画成箭头」）。同样是结构改造：
        # 见 overrides._set_cb_extend
        {"prop": "extend", "type": "enum",
         "value": str(getattr(cb, "extend", "neither")),
         "options": list(_CB_EXTENDS)},
        {"prop": "cmap", "type": "enum", "value": cname, "options": _cmap_options(cname),
         "group": "颜色映射"},
        {"prop": "vmin", "type": "number",
         "value": None if vmin is None else round(float(vmin), 4),
         "step": round(step, 4), "group": "颜色映射"},
        {"prop": "vmax", "type": "number",
         "value": None if vmax is None else round(float(vmax), 4),
         "step": round(step, 4), "group": "颜色映射"},
        {"prop": "tick_fontsize", "type": "number", "value": round(_cb_tick_fontsize(p), 2),
         "min": 3, "max": 24, "step": 0.5, "unit": "pt", "group": "刻度"},
        {"prop": "tick_color", "type": "color", "value": to_hex(_cb_tick_color(p)),
         "group": "刻度"},
        {"prop": "outline_visible", "type": "bool", "value": bool(cb.outline.get_visible()),
         "group": "高级"},
        {"prop": "outline_width", "type": "number",
         "value": round(float(cb.outline.get_linewidth()), 2),
         "min": 0, "max": 3, "step": 0.1, "unit": "pt", "group": "高级"},
        {"prop": "visible", "type": "bool", "value": bool(cb.ax.get_visible())},
    ]


def _legend_fields(leg) -> list[dict]:
    sizes = [t.get_fontsize() for t in leg.get_texts()]
    frame = leg.get_frame()
    loc_name = _legend_loc_name(leg)
    loc_opts = (["custom"] if loc_name == "custom" else []) + _LEGEND_LOCS
    return [
        {"prop": "loc", "type": "enum", "value": loc_name, "options": loc_opts},
        {"prop": "fontsize", "type": "number",
         "value": round(float(sizes[0]), 2) if sizes else 8,
         "min": 3, "max": 24, "step": 0.5, "unit": "pt"},
        {"prop": "frameon", "type": "bool", "value": bool(leg.get_frame_on())},
        {"prop": "visible", "type": "bool", "value": bool(leg.get_visible())},
        {"prop": "title", "type": "text", "value": leg.get_title().get_text(),
         "group": "样式"},
        {"prop": "title_fontsize", "type": "number",
         "value": round(float(leg.get_title().get_fontsize()), 2),
         "min": 3, "max": 24, "step": 0.5, "unit": "pt", "group": "样式"},
        {"prop": "facecolor", "type": "color", "value": to_hex(frame.get_facecolor()),
         "group": "样式"},
        {"prop": "framealpha", "type": "number",
         "value": 1.0 if frame.get_alpha() is None else round(float(frame.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05, "group": "样式"},
        {"prop": "edgecolor", "type": "color", "value": to_hex(frame.get_edgecolor()),
         "group": "样式"},
        # 条目顺序：value 是按显示顺序排的原始序号；options 给当前显示的文字
        # （前端画上下移动列表，不是普通下拉）
        {"prop": "entry_order", "type": "order",
         "value": _legend_entry_order(leg),
         "options": [t.get_text() for t in leg.get_texts()], "group": "布局"},
        {"prop": "ncol", "type": "number", "value": int(getattr(leg, "_ncols", 1)),
         "min": 1, "max": 6, "step": 1, "group": "布局"},
        {"prop": "borderpad", "type": "number", "value": round(float(leg.borderpad), 2),
         "min": 0, "max": 3, "step": 0.1, "group": "布局"},
        {"prop": "labelspacing", "type": "number", "value": round(float(leg.labelspacing), 2),
         "min": 0, "max": 3, "step": 0.1, "group": "布局"},
        {"prop": "handlelength", "type": "number", "value": round(float(leg.handlelength), 2),
         "min": 0, "max": 5, "step": 0.1, "group": "布局"},
    ]


def _tick_fields(ts: TickSet) -> list[dict]:
    t0 = _tick0(ts)
    is3d = getattr(ts.ax, "name", "") == "3d"
    fields = [
        {"prop": "fontsize", "type": "number",
         "value": round(float(ts._first(lambda t: t.get_fontsize(), 8.5)), 2),
         "min": 3, "max": 24, "step": 0.5, "unit": "pt"},
        {"prop": "color", "type": "color",
         "value": to_hex(ts._first(lambda t: t.get_color(), "#000000"))},
        {"prop": "rotation", "type": "number",
         "value": round(float(ts._first(lambda t: t.get_rotation(), 0.0)), 1),
         "min": -90, "max": 90, "step": 5, "unit": "°"},
        {"prop": "visible", "type": "bool",
         "value": bool(ts._first(lambda t: t.get_visible(), True))},
        {"prop": "direction", "type": "enum",
         "value": str(getattr(t0, "_tickdir", "out")),
         "options": ["out", "in", "inout"], "group": "刻度线"},
        {"prop": "length", "type": "number",
         "value": round(float(getattr(t0, "_size", 3.5)), 2),
         "min": 0, "max": 12, "step": 0.5, "unit": "pt", "group": "刻度线"},
        # 刻度是 marker，线宽落在 markeredgewidth 上（get_linewidth 读到的是
        # lines.linewidth，改了也不会变）
        {"prop": "width", "type": "number",
         "value": round(float(t0.tick1line.get_markeredgewidth()), 2) if t0 is not None else 0.8,
         "min": 0.1, "max": 3, "step": 0.1, "unit": "pt", "group": "刻度线"},
        # 数值格式（主刻度 Formatter）。"auto" = 回到脚本原样，不是「换成
        # ScalarFormatter」——对数轴上后者会把 10³ 写成 1000
        {"prop": "format", "type": "enum", "value": tick_format_name(ts.ax, ts.which),
         "options": list(_TICK_FORMATS), "group": "刻度线"},

        # ---- 刻度定位（Locator）：几个刻度、落在哪 ----
        {"prop": "major_mode", "type": "enum",
         "value": tick_major_mode(ts.ax, ts.which),
         "options": ["auto", "step", "fixed"], "group": "刻度定位"},
        {"prop": "major_step", "type": "number",
         "value": tick_major_step(ts.ax, ts.which),
         "min": 0, "step": 0.1, "group": "刻度定位"},
        {"prop": "major_values", "type": "number_list",
         "value": tick_major_values(ts.ax, ts.which), "group": "刻度定位"},
        {"prop": "minor_visible", "type": "bool",
         "value": tick_minor_visible(ts.ax, ts.which), "group": "刻度定位"},
        {"prop": "minor_mode", "type": "enum",
         "value": tick_minor_mode(ts.ax, ts.which),
         "options": ["auto", "step"], "group": "刻度定位"},
        {"prop": "minor_step", "type": "number",
         "value": tick_minor_step(ts.ax, ts.which),
         "min": 0, "step": 0.1, "group": "刻度定位"},
        # 次刻度默认不标数字（"none"）；"auto" 与主刻度一样是「脚本原样」
        {"prop": "minor_format", "type": "enum",
         "value": tick_minor_format(ts.ax, ts.which),
         "options": list(_TICK_MINOR_FORMATS), "group": "刻度定位"},
    ]
    if is3d:
        # mplot3d 的刻度朝向由投影决定；label 显隐的 tick_params 键也不含 z
        fields = [f for f in fields if f["prop"] not in ("direction", "visible")]
    return fields


def _axes_fields(ax, el: dict | None = None) -> list[dict]:
    """axes 的可编辑字段。

    `el` 带着遍历时才知道的能力标记（见 `_register`）：

    * `position_locked` —— 子 axes（inset / secondary）的落位由父级的
      `_axes_locator` 每帧重算，`set_position` 一 draw 就被顶回去。**不出这个
      字段**，宁可不支持也不给一个按了会弹回来的旋钮。
    * `limits_slaved` —— 次坐标轴的数据范围由父轴经换算函数每帧重算。实测：
      `set_xlim` 与 `invert_xaxis` 被顶回去、`set_aspect` 被 matplotlib 自己
      拒绝（"Secondary Axes can't set the aspect ratio"）、`get_xscale()` 回的
      是 `'function'`（`scale_options` 给不出合理选项）。整组不出。

    两条标记的**理由不同**，所以是两个字段而不是一个「这是子 axes」：
    插图的 xlim / scale 是真能改的，只有落位不能。
    """
    flags = el or {}
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    aspect = ax.get_aspect()
    return [
        *([] if flags.get("position_locked") else [
            {"prop": "position", "type": "rect",
             "value": [round(float(v), 4) for v in ax.get_position().bounds]}]),
        {"prop": "visible", "type": "bool", "value": bool(ax.get_visible())},

        *([] if flags.get("limits_slaved") else [
        {"prop": "xlim", "type": "pair", "value": [float(x0), float(x1)],
         "group": "数据范围"},
        {"prop": "ylim", "type": "pair", "value": [float(y0), float(y1)],
         "group": "数据范围"},
        # 选项由**当前这套 matplotlib 真正注册了的 scale** 决定，不写死清单：
        # 列一个 set_[xy]scale 吃不下的名字，用户点了只会得到一次渲染失败
        {"prop": "xscale", "type": "enum", "value": str(ax.get_xscale()),
         "options": scale_options(ax.get_xscale()), "group": "数据范围"},
        {"prop": "yscale", "type": "enum", "value": str(ax.get_yscale()),
         "options": scale_options(ax.get_yscale()), "group": "数据范围"},
        {"prop": "invert_x", "type": "bool", "value": bool(ax.xaxis_inverted()),
         "group": "数据范围"},
        {"prop": "invert_y", "type": "bool", "value": bool(ax.yaxis_inverted()),
         "group": "数据范围"},
        {"prop": "aspect", "type": "text",
         "value": aspect if isinstance(aspect, str) else str(round(float(aspect), 3)),
         "group": "数据范围"},
        ]),

        {"prop": "grid_x", "type": "bool", "value": _grid_visible(ax, "x"),
         "group": "网格与边框"},
        {"prop": "grid_y", "type": "bool", "value": _grid_visible(ax, "y"),
         "group": "网格与边框"},
        {"prop": "grid_color", "type": "color",
         "value": to_hex(_grid_prop(lambda g: g.get_color(), "#b0b0b0")(ax)),
         "group": "网格与边框"},
        {"prop": "grid_linestyle", "type": "enum",
         "value": str(_grid_prop(lambda g: g.get_linestyle(), ":")(ax)),
         "options": ["-", "--", ":", "-."], "group": "网格与边框"},
        {"prop": "grid_linewidth", "type": "number",
         "value": round(float(_grid_prop(lambda g: g.get_linewidth(), 0.5)(ax)), 2),
         "min": 0.1, "max": 3, "step": 0.1, "unit": "pt", "group": "网格与边框"},
        {"prop": "grid_alpha", "type": "number",
         "value": round(float(_grid_prop(lambda g: g.get_alpha(), None)(ax) or 1.0), 2),
         "min": 0, "max": 1, "step": 0.05, "group": "网格与边框"},
        {"prop": "spine_top", "type": "bool",
         "value": bool(ax.spines["top"].get_visible()) if "top" in ax.spines else True,
         "group": "网格与边框"},
        {"prop": "spine_right", "type": "bool",
         "value": bool(ax.spines["right"].get_visible()) if "right" in ax.spines else True,
         "group": "网格与边框"},
        {"prop": "spine_bottom", "type": "bool",
         "value": bool(ax.spines["bottom"].get_visible()) if "bottom" in ax.spines else True,
         "group": "网格与边框"},
        {"prop": "spine_left", "type": "bool",
         "value": bool(ax.spines["left"].get_visible()) if "left" in ax.spines else True,
         "group": "网格与边框"},
        # 「全部」这一档：四条边（含色条轴的 outline）统一改
        {"prop": "spine_color", "type": "color",
         "value": to_hex(spine_all_color(ax)), "group": "网格与边框"},
        {"prop": "spine_linewidth", "type": "number",
         "value": round(spine_all_width(ax), 2),
         "min": 0.1, "max": 3, "step": 0.1, "unit": "pt", "group": "网格与边框"},
        # 逐条覆盖（只画左下两条粗边框是论文图的常见做法）。没表态的落回
        # 「全部」，「全部」也没表态就用脚本原样——优先级见 apply_spine_model
        *[f for side in ("top", "right", "bottom", "left") for f in (
            {"prop": f"spine_{side}_color", "type": "color",
             "value": to_hex(spine_side_color(ax, side)), "group": "边框（逐条）"},
            {"prop": f"spine_{side}_linewidth", "type": "number",
             "value": round(spine_side_width(ax, side), 2),
             "min": 0.1, "max": 3, "step": 0.1, "unit": "pt",
             "group": "边框（逐条）"},
        )],
        {"prop": "facecolor", "type": "color", "value": to_hex(ax.get_facecolor()),
         "group": "网格与边框"},
    ]


def _axes3d_fields(ax) -> list[dict]:
    """3D 轴：整体几何/可见性 + 视角（elev/azim/roll）+ 轴线与背景面板样式。
    盒内数据属性（spines/lim/scale）在 mplot3d 里语义不同，继续禁用。
    注意 Axes3D.set_position 之后 matplotlib 会按三维盒比例微调实际落位——
    manifest 重建返回真实 bbox，前端以它为准。"""
    fields = [
        {"prop": "position", "type": "rect",
         "value": [round(float(v), 4) for v in ax.get_position().bounds]},
        {"prop": "visible", "type": "bool", "value": bool(ax.get_visible())},
        {"prop": "elev", "type": "number", "value": round(float(ax.elev), 1),
         "min": -90, "max": 90, "step": 5, "unit": "°", "group": "视角"},
        {"prop": "azim", "type": "number", "value": round(float(ax.azim), 1),
         "min": -180, "max": 180, "step": 5, "unit": "°", "group": "视角"},
    ]
    if hasattr(ax, "roll"):  # matplotlib ≥3.6
        fields.append({"prop": "roll", "type": "number",
                       "value": round(float(ax.roll or 0.0), 1),
                       "min": -180, "max": 180, "step": 5, "unit": "°",
                       "group": "视角"})
    line0, pane0 = ax.xaxis.line, ax.xaxis.pane
    fields += [
        {"prop": "axline_color", "type": "color", "value": to_hex(line0.get_color()),
         "group": "坐标轴"},
        {"prop": "axline_width", "type": "number",
         "value": round(float(line0.get_linewidth()), 2),
         "min": 0.1, "max": 5, "step": 0.1, "unit": "pt", "group": "坐标轴"},
        {"prop": "pane_visible", "type": "bool", "value": bool(pane0.get_visible()),
         "group": "坐标轴"},
        {"prop": "pane_color", "type": "color", "value": to_hex(pane0.get_facecolor()),
         "group": "坐标轴"},
        {"prop": "grid_visible", "type": "bool",
         "value": bool(getattr(ax, "_draw_grid", True)), "group": "坐标轴"},
        {"prop": "proj_type", "type": "enum",
         "value": str(getattr(ax, "_proj_type", "persp")),
         "options": ["persp", "ortho"], "group": "视角"},
    ]
    st = _arrow_style(ax)
    fields += [
        {"prop": "axis_arrows", "type": "bool", "value": _axis_arrows_on(ax),
         "group": "轴箭头"},
        {"prop": "arrow_color", "type": "color", "value": to_hex(st["color"]),
         "group": "轴箭头"},
        {"prop": "arrow_width", "type": "number",
         "value": round(float(st["width"]), 2),
         "min": 0.1, "max": 3, "step": 0.1, "unit": "pt", "group": "轴箭头"},
        {"prop": "arrow_head", "type": "number",
         "value": round(float(st["head"]), 1),
         "min": 2, "max": 20, "step": 0.5, "group": "轴箭头"},
    ]
    return fields


def _fields_for(el) -> list[dict]:
    artist, role = el["artist"], el["role"]
    if role == "figure":
        w, h = artist.get_size_inches()
        return [
            {"prop": "size_mm", "type": "pair",
             "value": [round(w * 25.4, 1), round(h * 25.4, 1)], "unit": "mm"},
            {"prop": "facecolor", "type": "color",
             "value": to_hex(artist.patch.get_facecolor()), "group": "背景"},
            {"prop": "transparent", "type": "bool",
             "value": not artist.patch.get_visible(), "group": "背景"},
        ]
    key = _cls_key(artist)
    if key == "ticklabel":
        return [{"prop": "text", "type": "text", "value": artist.get_text()}]
    if key == "ticks":
        return _tick_fields(artist)
    if key == "text":
        return _text_fields(artist)
    if key == "line":
        return _line_fields(artist)
    if key == "legend":
        return _legend_fields(artist)
    if key == "axes":
        return (_axes3d_fields(artist) if role == "axes3d"
                else _axes_fields(artist, el))
    if key == "image":
        return _image_fields(artist)
    if key == "arrowpatch":
        return _arrowpatch_fields(artist)
    if key == "patch":
        return _patch_fields(artist)
    if key == "scatter":
        return _collection_fields(artist, with_size=True)
    if key == "fill":
        return _collection_fields(artist, with_size=False)
    if key == "linecoll":
        return _linecoll_fields(artist)
    if key == "bar_series":
        return _bar_series_fields(artist)
    if key == "bar":
        return _bar_fields(artist)
    if key == "errorbar":
        return _errorbar_fields(artist)
    if key == "colorbar":
        return _colorbar_fields(artist)
    return []


_MIN_HIT_PX = 4.0  # 扁平元素最小命中厚度（display 像素）


def _finite_box(bb) -> bool:
    """包围盒的四个数都是有限值。

    matplotlib 3.8 的 `PolyCollection.get_window_extent()` 回的是 **-inf**
    （空 Bbox 的默认值），而不是零尺寸框——只判 `width <= 0` 会误以为
    「这是个扁平元素」而不是「这个 artist 根本没给出框」。
    """
    try:
        return all(math.isfinite(v) for v in (bb.x0, bb.y0, bb.x1, bb.y1))
    except (TypeError, ValueError):
        return False


def _collection_datalim(artist):
    """Collection 自己不给包围盒时，用**数据范围**换算 display 框；不适用返回 None。

    散点（PathCollection）当年就栽在这里——`Artist.get_window_extent` 对集合
    是空框，散点根本进不了 manifest。同一个坑在 **matplotlib 3.8** 上更宽：
    那一版 `fill_between` / `fill_betweenx` / `stackplot` 出的 PolyCollection
    的 window extent 是 `-inf`，于是**整片填充区在界面上不存在**（3.10+ 换成
    了 `FillBetweenPolyCollection`，自带可用的框，所以只在旧版本上发作）。
    CompatBench 的 minimum 档（matplotlib 3.8.4）是这么把它抓出来的：
    `art_fill_between` 是 Tier 1。

    **标量映射的集合刻意排除**（`get_array()` 非空：pcolor / pcolormesh /
    hexbin / 带 C 的 barbs）。它们的颜色由 colormap 每次 draw 重算
    （`update_scalarmappable`），放进 manifest 会让 `facecolor` 这类编辑
    「设了但下一帧被顶回去」——那比不支持更坏。它们要的是一族网格感知的
    handler（cmap / alpha / visible），记在 artist 普查里等排期。
    """
    if not isinstance(artist, Collection) or artist.get_array() is not None:
        return None
    ax = getattr(artist, "axes", None)
    if ax is None:
        return None
    try:
        bb = ax.transData.transform_bbox(artist.get_datalim(ax.transData))
    except Exception:                                   # noqa: BLE001
        return None
    if not _finite_box(bb) or (bb.width <= 0 and bb.height <= 0):
        return None
    return bb


def _padded_bbox(bb, W: float, H: float) -> list[float]:
    """display Bbox → figure 分数（top-origin），零厚度的边垫到可点中。"""
    w = max(float(bb.width), _MIN_HIT_PX)
    h = max(float(bb.height), _MIN_HIT_PX)
    x0 = float(bb.x0) - (w - float(bb.width)) / 2
    y1 = float(bb.y1) + (h - float(bb.height)) / 2
    return [x0 / W, 1.0 - y1 / H, w / W, h / H]


def _ensure_agg_canvas(fig):
    """保证 fig 挂着 Agg canvas，然后返回 renderer。

    脚本里 `fig.savefig(...); plt.close(fig)` 是极常见的写法（我们自己的
    examples 就这么写）。worker 的 CAPTURE 仍持有 Figure 对象，但 matplotlib
    3.11 起 `plt.close` 会把 canvas 退回 FigureCanvasBase——它没有
    get_renderer，量文字包围盒时直接 AttributeError，整张图起不来。
    这里当场补一个 Agg canvas，不依赖脚本把 figure 留在什么状态。
    """
    if not hasattr(fig.canvas, "get_renderer"):
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)          # 构造即绑定到 fig.canvas
    fig.canvas.draw()
    return fig.canvas.get_renderer()


def build_manifest(state: FigState, stem: str) -> dict:
    fig = state.fig
    renderer = _ensure_agg_canvas(fig)
    W, H = float(fig.bbox.width), float(fig.bbox.height)
    # 刻度伪元素按**当前**刻度状态对齐（必须在 draw 之后：标签的文字是 draw
    # 那一刻由 Formatter 填进去的）
    sync_tick_elements(state)
    budget = pathgeom.Budget()

    elements = []
    for el in state.elements:
        artist = el["artist"]
        entry = {"gid": el["gid"], "role": el["role"], "label": el["label"],
                 "draggable": el["draggable"], "editable": _fields_for(el)}
        # 文字类元素的显示名跟着**当前**文字走：登记名是 build 那一刻的快照，
        # 改过字（或色条翻转把标签搬了家）之后它就成了旧内容，元素树里对不上
        if el["role"] in ("title", "axis_label", "text", "legend_text"):
            live_text = artist.get_text()
            if not live_text:
                continue
            entry["label"] = _relabel(el["label"], live_text)
        if el["role"] in ("axes", "axes3d"):
            # 前端可拖动/缩放子图占比（override axes position）。子 axes 的
            # 落位归父级的 locator 管，给不了这个能力——`_axes_fields` 那边
            # 同步不出 `position` 字段，两处必须一致，否则前端会拿着一个
            # 后端根本不认的 prop 发 override。
            entry["resizable"] = not el.get("position_locked", False)
            if artist in state.colorbar_axes:
                entry["is_colorbar"] = True
                entry["colorbar_gid"] = f"{el['gid']}.colorbar"
            follow = state.axes_follow.get(el["gid"])
            if follow:
                entry["follow_gids"] = follow
        elif el["role"] == "image":
            # imshow 位图铺满宿主 axes，会在命中测试里盖住它——把几何编辑
            # 代理回宿主 axes（前端对 geom_gid 发 position override）
            entry["resizable"] = True
            entry["geom_gid"] = el["gid"].rsplit(".images_", 1)[0]
        if el["role"] == "figure":
            entry["bbox"] = [0.0, 0.0, 1.0, 1.0]
        elif el["role"] == "ticklabel":
            t = artist.live()
            if t is None or not t.get_text():
                continue
            entry["label"] = f"刻度 “{_snippet(t.get_text())}”"  # 改字后名字跟着变
            try:
                bb = t.get_window_extent(renderer)
                if bb.width <= 0 or bb.height <= 0:
                    continue
                entry["bbox"] = [bb.x0 / W, 1.0 - bb.y1 / H, bb.width / W, bb.height / H]
            except Exception:
                continue
        elif el["role"] == "ticks":
            boxes = []
            for t in artist.labels:
                try:
                    bb = t.get_window_extent(renderer)
                    if bb.width > 0 and bb.height > 0:
                        boxes.append(bb)
                except Exception:
                    pass
            if not boxes:
                continue
            x0 = min(b.x0 for b in boxes); y0 = min(b.y0 for b in boxes)
            x1 = max(b.x1 for b in boxes); y1 = max(b.y1 for b in boxes)
            entry["bbox"] = [x0 / W, 1.0 - y1 / H, (x1 - x0) / W, (y1 - y0) / H]
        elif isinstance(artist, SeriesGroup):
            boxes = []
            members = artist.members() if artist.kind == "errorbar" else artist.artists
            for m in members:
                try:
                    bb = m.get_window_extent(renderer)
                    if bb.width > 0 or bb.height > 0:
                        boxes.append(bb)
                except Exception:
                    pass
            if not boxes:
                continue
            x0 = min(b.x0 for b in boxes); y0 = min(b.y0 for b in boxes)
            x1 = max(b.x1 for b in boxes); y1 = max(b.y1 for b in boxes)
            entry["bbox"] = [x0 / W, 1.0 - y1 / H, (x1 - x0) / W, (y1 - y0) / H]
        elif isinstance(artist, ColorbarProxy):
            try:
                bb = artist.cb.ax.get_window_extent(renderer)
                entry["bbox"] = [bb.x0 / W, 1.0 - bb.y1 / H, bb.width / W, bb.height / H]
            except Exception:
                continue
            # 稳定语义身份（宿主 + 序号）：`axes_i.colorbar` 是按邻居排序编的
            # 名字，这个才是「这是谁的色条」。两者都在 state.index 里认得出
            entry["colorbar_key"] = artist.identity
            entry["host_gid"] = artist.host_gid
        elif isinstance(artist, PathCollection):
            # Artist 默认的 get_window_extent 对散点集合是空框，此前散点
            # 根本进不了 manifest——改用数据范围换算 display 框
            try:
                ax = artist.axes
                bb = ax.transData.transform_bbox(artist.get_datalim(ax.transData))
                if bb.width <= 0 and bb.height <= 0:
                    continue
                entry["bbox"] = _padded_bbox(bb, W, H)
            except Exception:
                continue
        else:
            try:
                bb = artist.get_window_extent(renderer)
                if not _finite_box(bb) or (bb.width <= 0 and bb.height <= 0):
                    # artist 自己给不出框：Collection 还能用数据范围换算一次
                    # （见 `_collection_datalim`），其余的老老实实丢掉。
                    bb = _collection_datalim(artist)
                    if bb is None:
                        continue
                # 水平 / 垂直的扁平线（基线、参考线）单边为 0，垫成可点中的窄条
                entry["bbox"] = _padded_bbox(bb, W, H)
            except Exception:
                continue
        # 路径几何（figure 分数、top-origin）：曲线 / 填充 / 独立形状的选中轮廓
        # 与命中判据。**渲染派生数据**，不进用户文档、不是 override——xlim /
        # scale / position / figsize / aspect / 色条方向一变，下一版就是新的。
        # 没有 geometry 的元素（文字、图例、容器、散点）前端照旧用 bbox。
        if el["role"] in ("line", "fill", "patch"):
            geom = pathgeom.element_geometry(artist, W, H, budget)
            if geom is not None:
                entry["geometry"] = geom
        # 独立箭头：端点（figure 分数、top-origin）随 manifest 下发，
        # 前端据此画端点手柄、整体拖动 / 单端拖动都写 endpoints_frac override
        if el["role"] == "arrow_patch" and getattr(artist, "_mm_arrow_standalone", False):
            pts = getattr(artist, "_posA_posB", None)
            if pts is not None:
                try:
                    conv = getattr(artist, "_convert_xy_units", lambda p: p)
                    disp = artist.get_transform().transform(
                        [conv(pts[0]), conv(pts[1])])
                    entry["arrow_endpoints"] = [
                        [round(float(x) / W, 4), round(1.0 - float(y) / H, 4)]
                        for x, y in disp]
                except Exception:
                    pass
        # 可拖元素附带锚点（figure 分数、top-origin），拖动换算用
        if el["draggable"]:
            try:
                if isinstance(artist, Text):
                    dx, dy = artist.get_transform().transform(artist.get_position())
                else:  # Legend：锚点用 bbox 左下角
                    bb = artist.get_window_extent(renderer)
                    dx, dy = bb.x0, bb.y0
                entry["anchor"] = [dx / W, 1.0 - dy / H]
                entry["drag_prop"] = "pos_frac" if isinstance(artist, Text) else "loc_frac"
            except Exception:
                entry["draggable"] = False
        elements.append(entry)

    if budget.skipped:
        # 降级要说出来：同一张图上有的曲线沿路径选、有的退回 bbox，
        # 不打这一行的话没人知道为什么
        print(f"[geometry] 点数预算用尽，{budget.skipped} 个元素退回 bbox "
              f"（TOTAL_BUDGET={pathgeom.TOTAL_BUDGET}）", file=sys.stderr)

    w_in, h_in = fig.get_size_inches()
    return {"stem": stem, "size_mm": [round(float(w_in) * 25.4, 2), round(float(h_in) * 25.4, 2)],
            "elements": elements}
