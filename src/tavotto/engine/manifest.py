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

from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.collections import (Collection, LineCollection, PathCollection,
                                    PolyCollection)
from matplotlib.axis import Axis
from matplotlib.container import (BarContainer, ErrorbarContainer,
                                  StemContainer)
from matplotlib.patches import FancyArrowPatch, Patch
from matplotlib.text import Text

import pathgeom
from overrides import (ColorbarProxy, FigState, HANDLERS, HATCHES, SeriesGroup,
                       TickLabel, TickSet, _ARROWSTYLES, _CB_EXTENDS, _LEGEND_LOCS,
                       _TICK_FORMATS, _TICK_MINOR_FORMATS,
                       collection_caps, is_color_mapped, is_linecoll_family,
                       patch_can_fill,
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


#: Collection 的显示名。`isinstance` 链**只影响这行中文**，不影响能力——
#: 能改什么由 `collection_caps()` 按真实 getter 实况说了算。认不出来的类回落到
#: 类名本身：显示 "QuadMesh 3" 比显示「集合 3」有用得多，也不会假装认识它。
_COLL_NAMES = [
    ("QuadMesh", "彩色网格"), ("PolyQuadMesh", "彩色网格"),
    ("ContourSet", "等值线"), ("QuadContourSet", "等值线"),
    ("EventCollection", "事件标记"), ("LineCollection", "线集合"),
    ("Quiver", "矢量场"), ("Barbs", "风羽"),
    ("FillBetweenPolyCollection", "填充区域"), ("PolyCollection", "填充区域"),
]


def _coll_label(coll, j: int) -> str:
    names = {c.__name__ for c in type(coll).__mro__}
    for cls_name, nice in _COLL_NAMES:
        if cls_name in names:
            return f"{nice} {j + 1}"
    return f"{type(coll).__name__} {j + 1}"


def _is_seq(obj) -> bool:
    """是不是「一串 artist」。`StemContainer.stemlines` 在不同 matplotlib 版本
    上可能是一个 LineCollection，也可能是一串 Line2D（`use_line_collection=False`
    的旧写法），两种都要认。"""
    return isinstance(obj, (list, tuple))


def _collection_gid_prefix(coll) -> str:
    """`ax.collections` 里的这一条对外用哪个 gid 前缀。**唯一出处**。

    三条兼容前缀（`scatter` / `fill` / `linecoll`）加一条通用的
    （`collections`）。登记循环按它编 gid，`_alias_consumed_member` 也按它
    还原「这个成员从前叫什么」——分开写的话，容器化一个成员时算出来的旧
    gid 会与当初真正发出去的那个不一样，而症状是历史 override 静默变成孤儿。
    """
    if isinstance(coll, PathCollection):
        return "scatter"
    if isinstance(coll, PolyCollection):
        return "fill"
    if is_linecoll_family(coll):
        return "linecoll"
    return "collections"


def _alias_consumed_member(state: FigState, ax, ax_gid: str, artist) -> None:
    """给被容器消费掉的成员登记它**从前**的 gid 别名（只进 index，不进 elements）。

    容器化是把「三个 artist」收成「一条系列」，代价是那几个成员从前各自的
    gid 不再出现在元素表里。历史文档里可能有针对它们的 override——别名让那些
    override 继续落在同一个 artist 上，而不是变成孤儿。

    **两条列表都要认**。`ax.stem()` 的成员横跨两处：markerline 在 `ax.lines`
    里（旧名 `axes_i.lines_k`），而 stemlines 是一条 **LineCollection**、在
    `ax.collections` 里（旧名 `axes_i.linecoll_j`）。只补前者的话，把茎的
    颜色/线宽/线型改过的历史文档一打开，那几条 override 指着一个再也解析不
    出来的 gid——worker 报「元素不存在」，而按写回事务的规矩**一条 warning
    就阻断写回**：用户的图从此写不回原件，提示还与真实原因毫不相干。

    别名只进 `state.index`：界面上不会多出条目，`_reverse_index()` 却认得出
    它与系列指着同一个 artist，于是撤掉任一侧都会让另一侧重放
    （见 `overrides.ALIAS_GROUPS` 与 `apply` 的 dirty_groups）。
    """
    if artist is None:
        return
    if isinstance(artist, Collection):
        try:
            j = list(ax.collections).index(artist)
        except ValueError:
            return
        state.index.setdefault(f"{ax_gid}.{_collection_gid_prefix(artist)}_{j}", artist)
        return
    try:
        k = list(ax.lines).index(artist)
    except ValueError:
        return
    state.index.setdefault(f"{ax_gid}.lines_{k}", artist)


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
                elif isinstance(cont, StemContainer):
                    # 一次 `ax.stem()` 在用户眼里是**一条**系列，在 artist 树上
                    # 却是三样东西：markerline / stemlines / baseline。前两样
                    # 归这个容器，baseline（零线）继续以普通曲线的身份单独可编辑
                    grp = SeriesGroup("stem_series",
                                      {"marker": cont.markerline,
                                       "stems": list(cont.stemlines)
                                       if _is_seq(cont.stemlines) else [cont.stemlines]},
                                      cont)
                    lab = str(cont.get_label() or "")
                    nice = f"茎叶系列 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"茎叶系列 {j + 1}"
                    _register(state, f"axes_{i}.stemseries_{j}", grp, "stem_series", nice)
                    for m in grp.members():
                        skip_ids.add(id(m))
                    # **旧 gid 别名**：容器化之前 markerline 是一条普通曲线
                    # （`axes_i.lines_k`）、stemlines 是一条线组
                    # （`axes_i.linecoll_j`），历史文档里可能有针对它们的
                    # override。**两个都要留**——只留 markerline 那条的话，
                    # 改过茎的颜色/线宽/线型的文档一打开就报「元素不存在」，
                    # 而那条 warning 会直接把写回整个阻断掉。
                    # 别名只进 index、不进 elements（ColorbarProxy 同样思路）。
                    for _m in grp.members():
                        _alias_consumed_member(state, ax, f"axes_{i}", _m)
            for j, ln in enumerate(ax.lines):
                if id(ln) in skip_ids:
                    continue
                lab = str(ln.get_label())
                nice = f"曲线 “{_snippet(lab)}”" if lab and not lab.startswith("_") else f"曲线 {j + 1}"
                _register(state, f"axes_{i}.lines_{j}", ln, "line", nice)
            for j, im in enumerate(ax.images):
                _register(state, f"axes_{i}.images_{j}", im, "image", f"图像 {j + 1}")
            # Collection family：三条 gid 分支的**唯一理由是向后兼容**——
            # `axes_i.scatter_j` 与 `axes_i.fill_j` 是已经发出去的名字，历史
            # 文档里有针对它们的 override，不能换。序号 j 是 `ax.collections`
            # 里的下标（不是每种角色各自计数），所以把从前没登记的那些补登记
            # 进来**不会挪动**已有 gid。能改什么全部由 `collection_caps()` 按
            # 真实 getter 实况决定，与这里挑哪个前缀无关。
            for j, coll in enumerate(ax.collections):
                # 色条轴上的 collection 不是用户的图元：`cb.solids` 是那条色带
                # 本身、`cb.dividers` 是分隔线，两者每次 `_draw_all()` 都被删掉
                # 重建（与 extend 的延伸三角同一类）。登记它们等于在元素表里放
                # 两个随时换身份的幽灵条目，而且与色条代理重复——色条轴对外
                # 只有一个元素，就是 `axes_i.colorbar`
                if id(coll) in skip_ids or ax in cbar_of_ax:
                    continue
                # gid 前缀只有 `_collection_gid_prefix` 一处出处——
                # `_alias_consumed_member` 要按同一条规则还原「这个成员从前
                # 叫什么」，两边分开写会让历史 override 悄悄变成孤儿。
                prefix = _collection_gid_prefix(coll)
                gid = f"axes_{i}.{prefix}_{j}"
                if prefix == "scatter":
                    lab = str(coll.get_label())
                    nice = f"散点 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"散点系列 {j + 1}"
                    _register(state, gid, coll, "scatter", nice)
                elif prefix == "fill":
                    _register(state, gid, coll, "fill", _coll_label(coll, j))
                elif prefix == "linecoll":
                    # 线组：`hlines`/`vlines` 的参考线、`stem` 的竖线、
                    # `eventplot` 的事件线（EventCollection 是它的子类）、
                    # `streamplot` 的流线、`violinplot` 的极值线。这是 artist
                    # 普查里权重最高的缺口（8 处 / 5 个 case），2026-08-21 之前
                    # 它们在界面上根本不存在。
                    #
                    # **`linecoll` 是自己一族、不并进下面的通用 collection**：
                    # 它对外的 prop 是 `color`（Line2D 那套口径），而
                    # Collection 族给的是 facecolor/edgecolor——两套命名已经
                    # 发出去了，合并等于换掉存量文档里的 prop 名。
                    #
                    # **标量映射的一律走下面那支通用分支**：那时颜色由
                    # colormap 每次 draw 重算，`color` 这个单值口径表达不了
                    # 逐条颜色；通用分支按 `collection_caps()` 的实况说话，
                    # 反而不会假装认识它。判据是 `overrides.is_linecoll_family`
                    # ——**登记与 dispatch 共用同一个函数**，`_cls_key` 问的也
                    # 是它。分开写必然漂开，而漂开的表现是元素表说通用、
                    # 检查器却按线组给字段，那个控件一个像素都改不动。
                    lab = str(coll.get_label())
                    nice = f"线组 “{_snippet(lab)}”" if lab and not lab.startswith("_") \
                        else f"线组 {j + 1}"
                    _register(state, gid, coll, "linecoll", nice)
                else:
                    _register(state, gid, coll, "collection", _coll_label(coll, j))
            # 脚本直接 add_patch 的独立箭头（XPS 峰位标注这类画法）与独立形状。
            # 形状按 **Patch family** 认，不逐个列类名：`ax.fill()` 的 Polygon、
            # 手搓的 PathPatch 之外还有 pie 的 Wedge、axhspan/axvspan 的
            # Rectangle、Circle / Ellipse / Arc / FancyBboxPatch / stairs 的
            # StepPatch，以及用户自己继承的子类——它们的样式契约完全相同
            # （CompatBench 的 art_shapes / art_axhspan_axvspan / art_pie 就是
            # 这么现形的）。`patch` 那组能力建在 Patch 的通用 API 上，泛化不
            # 需要新写 handler。
            # gid 用 patches 里的树序 j 保证重建稳定，label 各自计数。柱形系列的
            # Rectangle 也在 ax.patches 里，已经登记过，这里必须跳过（skip_ids 收了它们）；
            # FancyArrowPatch 在上一支被拦掉，它有自己的端点契约；色条轴上的
            # patch 由 is_cbax 挡住。
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
        # `ax.add_artist(...)` 放进来的东西（AnchoredText、自定义 Artist…）。
        # matplotlib 会把认得的类型改道进 lines/patches/collections，所以这里
        # 剩下的基本都是「我们不认识的」——**登记但只开 visible/zorder**
        # （见 overrides._GENERIC_CAPS）。不登记的话它们在元素树里根本不存在，
        # 用户看得见画面上有东西却点不中，还不知道为什么。
        for j, art in enumerate(getattr(ax, "artists", []) or []):
            if id(art) in state.index_ids():
                continue
            _register(state, f"axes_{i}.artists_{j}", art, "artist",
                      f"{type(art).__name__} {j + 1}")
        for j, tbl in enumerate(getattr(ax, "tables", []) or []):
            _register(state, f"axes_{i}.tables_{j}", tbl, "artist", f"表格 {j + 1}")
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

    state.unregistered = census(fig, state)


#: 每张 Axes 上属于 matplotlib 自己的结构件——它们不是「Tavotto 漏掉的用户
#: 元素」。轴与它的整棵子树（刻度线、刻度标签、offset text）由刻度模型代表，
#: 边框由边框模型代表，背景矩形由 axes 的 facecolor 代表。
def _internal_ids(fig, colorbar_axes=()) -> set[int]:
    ids = {id(fig.patch)}
    for ax in fig.axes:
        ids.add(id(ax))
        ids.add(id(ax.patch))
        ids.update(id(sp) for sp in getattr(ax, "spines", {}).values())
        for name in ("xaxis", "yaxis", "zaxis"):
            axis = getattr(ax, name, None)
            if axis is not None:
                ids.add(id(axis))
        if ax in colorbar_axes:
            # 色条轴的内部件（色带 solids、分隔线 dividers、extend 的延伸三角）
            # **有意**不登记：全是 `_draw_all()` 每次删掉重建的幽灵，而且色条
            # 对外只有 `axes_i.colorbar` 一个元素。不把它们归成结构件的话，
            # 每张带 extend 的图都会凭空多出一条「漏掉了 PathPatch」——
            # 普查一旦开始喊狼来了，真正的缺口就没人看了
            ids.update(id(a) for a in getattr(ax, "patches", []))
            ids.update(id(a) for a in getattr(ax, "collections", []))
    return ids


def census(fig, state: FigState) -> list[dict]:
    """诊断用的 artist 普查：**画在图上、却没有进元素表**的那些。

    产品路径不依赖它——`instrument` 的语义化遍历才是权威，这里只回答一个
    问题：「有没有东西被我们悄悄漏掉了」。§35 的底线是**不许静默消失**：
    漏掉的类名要说得出来，才有可能被修；说不出来就只剩用户一句「我的图里
    那块东西点不中」。

    只走一层 `get_children()`，不递归——图例、注释、色条内部件各有自己的
    代表元素，递归进去只会把结构件重新数一遍。空文字（还没写字的标题、
    轴标签）不算漏：它们本来就不该出现在元素树里。

    每次 build 跑一次（不是每帧），代价是每张 Axes 一次列表拼接。
    """
    known = state.index_ids() | _internal_ids(fig, state.colorbar_axes)
    # 被语义容器消费掉的成员（柱形系列的柱、误差棒的横杠、茎叶的茎）已经由
    # 容器代表了，不是「漏掉的」——`skip_ids` 那条纪律在普查这一侧的对应物
    for el in state.elements:
        art = el["artist"]
        if isinstance(art, SeriesGroup):
            known.update(id(m) for m in art.members())
            known.update(id(m) for m in (art.artists if isinstance(art.artists, list) else []))
    seen: dict[tuple, int] = {}
    for gid, owner in [("figure", fig)] + [(f"axes_{i}", ax) for i, ax in enumerate(fig.axes)]:
        try:
            children = list(owner.get_children())
        except Exception:  # noqa: BLE001 — 普查失败绝不能拖垮渲染
            continue
        for child in children:
            if id(child) in known or isinstance(child, (Axes, Axis)):
                continue
            if isinstance(child, Text) and not child.get_text():
                continue
            cls = type(child)
            key = (f"{cls.__module__}.{cls.__qualname__}", gid)
            seen[key] = seen.get(key, 0) + 1
    return [{"cls": cls, "where": where, "count": n}
            for (cls, where), n in sorted(seen.items())]


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


def _collection_fields(coll, *, label: bool) -> list[dict]:
    """Collection family 的字段表——**由能力探针驱动，不由类名驱动**。

    `collection_caps()` 读的是这个对象此刻真实的 getter 实况，所以：
    * `scatter(x, y)` 出 facecolor，`scatter(x, y, c=z)` 出 cmap/vmin/vmax
      而**不出** facecolor——后者的 facecolors 每次 draw 由
      `update_scalarmappable()` 从数组重算，给了也是白给（见 overrides 的
      能力层抬头）；
    * LineCollection 没有 face 可填，只出描边——**连花纹都不出**（花纹画在
      面上，见 `collection_caps` 的 `faces`）；
    * `pcolormesh` 的 QuadMesh 现在没有边，但**加得上**边（网格线），
      所以描边照出。

    `label` 参数只是为了不给历史上的「填充区域」凭空多一个字段——
    那是显示口径的取舍，不是能力问题。
    """
    import numpy as np  # noqa: PLC0415 — worker 侧有科学栈
    caps = collection_caps(coll)
    ec = coll.get_edgecolor()
    lw = np.atleast_1d(coll.get_linewidths())
    lab = str(coll.get_label())
    fields: list[dict] = []
    if label:
        fields.append({"prop": "label", "type": "text",
                       "value": "" if lab.startswith("_") else lab})
    if "fill" in caps:
        fc = coll.get_facecolor()
        fields.append({"prop": "facecolor", "type": "color",
                       "value": to_hex(fc[0]) if len(fc) else "#000000"})
    if "sizes" in caps:
        sizes = coll.get_sizes()
        fields.append({"prop": "size", "type": "number",
                       "value": round(float(np.mean(sizes)), 1) if len(sizes) else 20.0,
                       "min": 1, "max": 400, "step": 1, "unit": "pt²"})
    if "marker" in caps:
        # marker 形状可整体替换（set_paths）；"original" = 脚本原始路径
        cur = getattr(coll, "_mm_marker", None) or "original"
        m_opts = ["original", "o", "s", "D", "^", "v", "<", ">", "x", "+", "*", ".",
                  "p", "h"]
        fields.append({"prop": "marker", "type": "enum", "value": cur,
                       "options": ([cur] if cur not in m_opts else []) + m_opts})
    fields += [
        {"prop": "edgecolor", "type": "color",
         "value": to_hex(ec[0]) if len(ec) else "#000000"},
        {"prop": "linewidth", "type": "number",
         "value": round(float(lw[0]), 2) if len(lw) else 0.0,
         "min": 0, "max": 8, "step": 0.1, "unit": "pt"},
        # **显示值与 handler 的 getter 必须同源**：这一族的 linestyle 走
        # `_get_linecoll_ls`（未缩放规格），所以反查也只能用 Collection 那条
        # `_linecoll_linestyle_name`。用 Line2D 那条 `_linestyle_name` 的话，
        # `Collection.get_linestyle()` 回的是 dash 元组列表、不是字符串，于是
        # **任何**虚线都被当成自定义 dash 显示成实线占位——
        # `LineCollection(..., linestyles="--")` 画出来是虚线、检查器说实线。
        # 这正是「同一个判据写两遍」的标准症状（见 `is_linecoll_family`）。
        {"prop": "linestyle", "type": "enum", "value": _linecoll_linestyle_name(coll),
         "options": ["-", "--", "-.", ":"], "group": "线条与填充"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if coll.get_alpha() is None else round(float(coll.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(coll.get_visible())},
    ]
    if "faces" in caps:
        # 花纹画在**面**上。没有面的（LineCollection、`contour`）给了也白给
        # ——设得进状态、画面上一个像素都不变，那正是这套能力探针要挡的东西。
        # 注意判据是 `faces` 而不是 `fill`：映射的 QuadMesh / contourf 有面，
        # 只是那个面的颜色不归用户改。
        fields.append(
            {"prop": "hatch", "type": "enum", "value": str(coll.get_hatch() or ""),
             "options": _hatch_options(coll.get_hatch()), "group": "线条与填充"})
    if "mapped" in caps:
        fields += _colormap_fields(coll)
    fields.append({"prop": "zorder", "type": "number",
                   "value": round(float(coll.get_zorder()), 1),
                   "min": -5, "max": 50, "step": 1, "group": "排列"})
    return fields


def _hatch_options(current) -> list[str]:
    cur = str(current or "")
    return ([cur] if cur and cur not in HATCHES else []) + HATCHES


def _colormap_fields(m) -> list[dict]:
    """ScalarMappable（Collection / AxesImage 共用）的颜色映射字段。

    `norm` **刻意不开放**：换 norm 改的是「数据怎么被解释成颜色」，那是
    科学结论的一部分，不是排版。vmin/vmax 只是同一个 norm 的定义域，改它
    等价于脚本里写 `clim=`——仍在展示范畴里。
    """
    vmin, vmax = m.get_clim()
    span = abs(float(vmax) - float(vmin)) if vmin is not None and vmax is not None else 1.0
    step = max(span / 100.0, 1e-6)
    cname = m.get_cmap().name
    return [
        {"prop": "cmap", "type": "enum", "value": cname,
         "options": _cmap_options(cname), "group": "颜色映射"},
        {"prop": "vmin", "type": "number",
         "value": None if vmin is None else round(float(vmin), 4),
         "step": round(step, 4), "group": "颜色映射"},
        {"prop": "vmax", "type": "number",
         "value": None if vmax is None else round(float(vmax), 4),
         "step": round(step, 4), "group": "颜色映射"},
    ]


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
    """Patch family 的形状：`ax.fill()` 的 Polygon、手搓的 PathPatch、pie 的
    Wedge、axhspan 的 Rectangle、Circle / Ellipse / Arc / FancyBboxPatch /
    StepPatch，以及用户自己继承出来的子类——这些 getter 全在 `Patch` 基类上。

    几何不给编辑字段——它由脚本的数据决定，改它等于改数据。选中 / 命中 /
    框选靠 manifest 的 `geometry`（沿真实闭合路径），样式在这里。
    """
    alpha = pt.get_alpha()
    fields: list[dict] = []
    if patch_can_fill(pt):
        # `Arc` 画不出面：facecolor / fill 设得进去、`get_*` 也照回，
        # 但 `Arc.draw()` 从不画那个面（实测红色像素 0 vs Circle 4122）。
        # **花纹不在此列**——它在 Arc 上是真画的，判据见 `patch_can_fill`。
        fields += [
            {"prop": "facecolor", "type": "color", "value": to_hex(pt.get_facecolor())},
            {"prop": "fill", "type": "bool", "value": bool(pt.get_fill())},
        ]
    fields += [
        {"prop": "edgecolor", "type": "color", "value": to_hex(pt.get_edgecolor())},
        {"prop": "linewidth", "type": "number", "value": round(float(pt.get_linewidth()), 2),
         "min": 0, "max": 8, "step": 0.1, "unit": "pt"},
        {"prop": "linestyle", "type": "enum", "value": _linestyle_name(pt),
         "options": ["-", "--", "-.", ":"]},
        {"prop": "hatch", "type": "enum", "value": str(pt.get_hatch() or ""),
         "options": _hatch_options(pt.get_hatch())},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if alpha is None else round(float(alpha), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(pt.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(pt.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]
    return fields


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
        # 与 Collection 共用同一份「颜色映射」字段：AxesImage 与 Collection 在
        # matplotlib 里同属 ColorizingArtist，cmap/clim 的语义逐字相同
        fields += _colormap_fields(im)
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


def _stem_fields(grp) -> list[dict]:
    """茎叶系列（StemContainer）：markerline + stemlines 统一改。

    baseline 不在这里——它是零线，以普通曲线的身份单独可编辑。
    """
    marker = grp.artists.get("marker")
    stems = grp.artists["stems"]
    probe = marker if marker is not None else (stems[0] if stems else None)
    if probe is None:
        return []
    lab = str(grp.container.get_label() or "") if grp.container is not None else ""
    color = probe.get_color()
    if hasattr(color, "__len__") and not isinstance(color, str) and len(color) \
            and not isinstance(color[0], (int, float)):
        color = color[0]
    stem0 = stems[0] if stems else None
    lw = stem0.get_linewidth() if stem0 is not None else 1.0
    if hasattr(lw, "__len__"):
        lw = lw[0] if len(lw) else 1.0
    m_name = str(marker.get_marker()) if marker is not None else "None"
    m_opts = ["None", "o", "s", "D", "^", "v", "<", ">", "x", "+", "*", "."]
    if m_name not in m_opts:
        m_opts = [m_name] + m_opts
    return [
        {"prop": "label", "type": "text", "value": "" if lab.startswith("_") else lab},
        {"prop": "color", "type": "color", "value": to_hex(color)},
        {"prop": "linewidth", "type": "number", "value": round(float(lw), 2),
         "min": 0.1, "max": 8, "step": 0.1, "unit": "pt"},
        # 茎是 **LineCollection**，反查要用未缩放规格那一套：`_linestyle_name`
        # 是 Line2D 那条（`get_linestyle()` 回字符串），喂给 Collection 时它
        # 拿到的是 `(offset, seq)`，于是**任何** dash 都显示成实线占位——
        # `ax.stem(..., linefmt="--")` 画出来是虚线、检查器却说实线。
        {"prop": "linestyle", "type": "enum",
         "value": _linecoll_linestyle_name(stem0) if stem0 is not None else "-",
         "options": ["-", "--", "-.", ":"]},
        {"prop": "marker", "type": "enum", "value": m_name, "options": m_opts,
         "group": "标记"},
        {"prop": "markersize", "type": "number",
         "value": round(float(marker.get_markersize()), 2) if marker is not None else 6.0,
         "min": 0, "max": 20, "step": 0.5, "unit": "pt", "group": "标记"},
        {"prop": "alpha", "type": "number",
         "value": 1.0 if probe.get_alpha() is None else round(float(probe.get_alpha()), 2),
         "min": 0, "max": 1, "step": 0.05},
        {"prop": "visible", "type": "bool", "value": bool(probe.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(probe.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


def _generic_fields(a) -> list[dict]:
    """认不出来的 Artist：只给 `visible` 与 `zorder`。

    两者由 draw 的公共机制兑现，任何 Artist 子类都逃不掉——所以这两个开关
    **一定**是真的。`alpha` 不给：它要靠每个 artist 自己在 draw 里读，基类
    不保证，给了就又多一个「点了没反应」的控件（§36：宁可少开放，不可开放
    了却不对）。识别 + 可选中 + 能藏起来，对第一版已经够用了。
    """
    return [
        {"prop": "visible", "type": "bool", "value": bool(a.get_visible())},
        {"prop": "zorder", "type": "number", "value": round(float(a.get_zorder()), 1),
         "min": -5, "max": 50, "step": 1, "group": "排列"},
    ]


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
    if key == "collection":
        # 历史上「填充区域」没有 label 字段，保持原样；其余 Collection 都给
        return _collection_fields(artist, label=(role != "fill"))
    if key == "linecoll":
        return _linecoll_fields(artist)
    if key == "artist":
        return _generic_fields(artist)
    if key == "stem_series":
        return _stem_fields(artist)
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


def _finite_geometry(entry: dict) -> bool:
    """entry 里的几何字段全是有限值。见 `build_manifest` 里那道总闸的说明。"""
    for field in ("bbox", "anchor", "arrow_endpoints", "geometry"):
        v = entry.get(field)
        if v is None:
            continue
        for x in _flatten_numbers(v):
            if not math.isfinite(x):
                return False
    return True


def _flatten_numbers(v):
    """任意嵌套结构里的所有数字（bool 不算——它不是几何）。"""
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, (list, tuple)):
        for item in v:
            yield from _flatten_numbers(item)
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        yield float(v)


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

    **这里不再排除标量映射的集合**。那句 `get_array() is not None` 是**登记期**
    的判据（当年映射的集合根本不进元素表），不是几何判据——它们现在照常登记，
    再把它们的包围盒挡回去只会让 pcolormesh 退回 tightbbox（= 整块子图）。
    能不能编辑由 `overrides.collection_caps()` 说了算，与量框无关。
    """
    if not isinstance(artist, Collection):
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


def _collection_bbox(coll, renderer):
    """Collection 的 display 包围盒——**全仓库唯一一处**；量不出来返回 None。

    三级判据，先精确后兜底：

    1. ``get_window_extent(renderer)``——`fill_between` / `quiver` /
       `violinplot` 的多边形自己就给得出有限框，原样用（包围盒一个像素不变，
       写回自检比的就是这个框）；
    2. ``get_datalim(transData)`` 换算——**多数 Collection 的 window extent 是
       无穷大空框**（`Bbox([[inf, inf], [-inf, -inf]])`，实测 `hlines` /
       `eventplot` / `contour` / `scatter` / `pcolormesh`… 都是）。数据范围是
       它们真正画在哪儿的权威来源；
    3. ``get_tightbbox(renderer)``——连数据范围都解不出时（非 data 坐标系的
       集合）的最后一手。

    ## 为什么 datalim 必须排在 tightbbox **之前**

    `get_tightbbox` 会与 artist 的裁剪框求交，而稀疏集合的裁剪框就是**整块
    子图**——实测（4×3 in / 100 dpi，子图 x[50,360] y[33,264]）：

        hlines      tightbbox x[ 50,360] y[ 33,264]   datalim x[81,143] y[128,128]
        eventplot   tightbbox x[ 50,360] y[ 33,264]   datalim x[81,143] y[229,245]
        contour     tightbbox x[ 50,360] y[ 33,264]   datalim x[267,329] y[182,237]

    前端的命中与框选用的正是 manifest 的 bbox（没有路径几何的元素只有它，
    见 `web/src/canvas/interactions.ts`），而普通元素的命中代价低于 axes
    ——拿整块子图当命中框的后果是**点子图里任何一处空白都会选中这条参考线，
    框选也几乎必然把它圈进去**。这不是「偏大一点」，是让同一张图上其余元素
    全都难以选中。

    ## 为什么这个函数必须是唯一出处

    这里从前有三份实现：散点分支自己拼一次 datalim、Collection 分支走
    window_extent→tightbbox、`else` 分支再挂一次 `_collection_datalim`
    （Collection 永远走不到它，是死代码）。于是「散点的命中框」与「参考线的
    命中框」按两套规则算，而两套规则会各自演进——这正是 §单一权威 要挡的
    那类分叉。SeriesGroup 的成员（误差棒的横杠、茎）也问同一个函数。
    """
    def _ok(bb):
        if bb is None:
            return False
        w, h = float(bb.width), float(bb.height)
        return (w == w and h == h                      # NaN 自比不等
                and abs(w) != float("inf") and abs(h) != float("inf")
                and (w > 0 or h > 0))

    try:
        bb = coll.get_window_extent(renderer)
    except Exception:  # noqa: BLE001
        bb = None
    if _ok(bb):
        return bb
    bb = _collection_datalim(coll)
    if _ok(bb):
        return bb
    try:
        bb = coll.get_tightbbox(renderer)
    except Exception:  # noqa: BLE001
        return None
    return bb if _ok(bb) else None


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
    #: 登记了、却在这一轮 build 里被丢掉的元素（量不出几何 / 文字空了 / 刻度
    #: 没了）。**必须报出去**：`census` 判「已知」用的是登记表，所以这些元素
    #: 既不在 `elements` 里、也不会被普查报成漏掉——两头都不出现，正是普查
    #: 要防的那种静默消失（§35）。自定义 Artist 尤其容易撞上：只实现 `draw()`
    #: 而没重写 `get_window_extent()` 的，基类回的是空框。
    dropped: dict[tuple, int] = {}

    #: 这几种「丢弃」是**正常的**，报出去只会让诊断喊狼来了：刻度不是常驻
    #: artist（换 locator、改 xlim、翻色条方向都会让整组重来），空文字的标题
    #: 与轴标签本来就不该进元素树（`census` 的 docstring 写着同一条）。
    _DROP_CHURN_ROLES = ("ticks", "ticklabel")

    def _drop(el, why: str):
        if el["role"] in _DROP_CHURN_ROLES or why in ("empty_text", "gone"):
            return
        cls = type(el["artist"])
        key = (f"{cls.__module__}.{cls.__qualname__}",
               el["gid"].split(".", 1)[0], why)
        dropped[key] = dropped.get(key, 0) + 1

    for el in state.elements:
        artist = el["artist"]
        entry = {"gid": el["gid"], "role": el["role"], "label": el["label"],
                 "draggable": el["draggable"], "editable": _fields_for(el)}
        # 文字类元素的显示名跟着**当前**文字走：登记名是 build 那一刻的快照，
        # 改过字（或色条翻转把标签搬了家）之后它就成了旧内容，元素树里对不上
        if el["role"] in ("title", "axis_label", "text", "legend_text"):
            live_text = artist.get_text()
            if not live_text:
                _drop(el, "empty_text")
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
                _drop(el, "gone")
                continue
            entry["label"] = f"刻度 “{_snippet(t.get_text())}”"  # 改字后名字跟着变
            try:
                bb = t.get_window_extent(renderer)
                if bb.width <= 0 or bb.height <= 0:
                    _drop(el, "no_geometry")
                    continue
                entry["bbox"] = [bb.x0 / W, 1.0 - bb.y1 / H, bb.width / W, bb.height / H]
            except Exception:
                _drop(el, "no_geometry")
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
                _drop(el, "no_geometry")
                continue
            x0 = min(b.x0 for b in boxes); y0 = min(b.y0 for b in boxes)
            x1 = max(b.x1 for b in boxes); y1 = max(b.y1 for b in boxes)
            entry["bbox"] = [x0 / W, 1.0 - y1 / H, (x1 - x0) / W, (y1 - y0) / H]
        elif isinstance(artist, SeriesGroup):
            boxes = []
            members = (artist.artists if artist.kind == "bar_series"
                       else artist.members())
            for m in members:
                try:
                    # 成员里混着 Collection（误差棒的横杠、茎叶的茎）——它们的
                    # get_window_extent 多半是无穷大空框，走同一条退路
                    bb = (_collection_bbox(m, renderer) if isinstance(m, Collection)
                          else m.get_window_extent(renderer))
                    if bb is not None and (bb.width > 0 or bb.height > 0):
                        boxes.append(bb)
                except Exception:
                    pass
            if not boxes:
                _drop(el, "no_geometry")
                continue
            x0 = min(b.x0 for b in boxes); y0 = min(b.y0 for b in boxes)
            x1 = max(b.x1 for b in boxes); y1 = max(b.y1 for b in boxes)
            entry["bbox"] = [x0 / W, 1.0 - y1 / H, (x1 - x0) / W, (y1 - y0) / H]
        elif isinstance(artist, ColorbarProxy):
            try:
                bb = artist.cb.ax.get_window_extent(renderer)
                entry["bbox"] = [bb.x0 / W, 1.0 - bb.y1 / H, bb.width / W, bb.height / H]
            except Exception:
                _drop(el, "no_geometry")
                continue
            # 稳定语义身份（宿主 + 序号）：`axes_i.colorbar` 是按邻居排序编的
            # 名字，这个才是「这是谁的色条」。两者都在 state.index 里认得出
            entry["colorbar_key"] = artist.identity
            entry["host_gid"] = artist.host_gid
        elif isinstance(artist, Collection):
            # 散点（PathCollection）**不再单开一支**：它当年之所以有自己的
            # 分支，是因为 `get_window_extent` 对集合回空框、需要用数据范围
            # 换算——而那正是 `_collection_bbox` 的第二级判据。两份实现同一
            # 件事就会各自演进，合成一处（见该函数的抬头）。
            bb = _collection_bbox(artist, renderer)
            if bb is None:
                _drop(el, "no_geometry")
                continue
            entry["bbox"] = _padded_bbox(bb, W, H)
        else:
            try:
                bb = artist.get_window_extent(renderer)
                if not _finite_box(bb) or (bb.width <= 0 and bb.height <= 0):
                    # 这一支只剩**非 Collection** 的 artist（上面那支已经把
                    # 整族接走了），它们没有数据范围可换算——量不出框就如实
                    # 报进 `unsupported`，不许静默消失。
                    _drop(el, "no_geometry")
                    continue
                # 水平 / 垂直的扁平线（基线、参考线）单边为 0，垫成可点中的窄条
                entry["bbox"] = _padded_bbox(bb, W, H)
            except Exception:
                _drop(el, "no_geometry")
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
        # ---- 几何总闸：非有限值一个都不许出去 ----
        # 逐个分支补 `isfinite` 是补不完的（分支还会再长），而漏一个的后果
        # **取决于走哪条控制面**：Python 的 `json.dumps` 照写 `NaN` /
        # `Infinity` 字面量、`json.loads` 也照收，于是 Python 渲染池一路绿灯；
        # 而 workerd（Rust serde_json）**严格按 RFC 8259 拒收整帧**，报
        # 「渲染进程往协议管道里写了非 JSON 的内容」并重启会话——同一份
        # manifest，两条控制面两个结果。
        #
        # 真实触发路径（CompatBench 的 ax_secondary_x 抓到的）：
        # `secondary_xaxis(functions=(1000/v, 1000/v))` 在 v=0 处映射出 inf，
        # 刻度标签的包围盒变成 `[inf, nan, nan, nan]`。而 ticklabel 那条分支
        # 的守卫是 `bb.width <= 0`——**`nan <= 0` 为假**，NaN 大摇大摆地过。
        #
        # 量不出位置的元素本来也选不中、画不了描边，丢掉与既有「零尺寸包围盒
        # 就 continue」是同一个取舍。丢了要说出来，别静默。
        if not _finite_geometry(entry):
            print(f"[manifest] {el['gid']} 的几何不是有限值，已丢弃"
                  f"（bbox={entry.get('bbox')} anchor={entry.get('anchor')}）",
                  file=sys.stderr)
            _drop(el, "not_finite")
            continue

        # 可拖元素附带锚点（figure 分数、top-origin），拖动换算用
        if el["draggable"]:
            try:
                if isinstance(artist, Text):
                    dx, dy = artist.get_transform().transform(artist.get_position())
                else:  # Legend：锚点用 bbox 左下角
                    bb = artist.get_window_extent(renderer)
                    dx, dy = bb.x0, bb.y0
                anchor = [dx / W, 1.0 - dy / H]
                if not all(math.isfinite(v) for v in anchor):
                    # 锚点是在总闸之后算的，自己再过一遍（见上面那段说明）
                    raise ValueError("anchor 不是有限值")
                entry["anchor"] = anchor
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
    out = {"stem": stem,
           "size_mm": [round(float(w_in) * 25.4, 2), round(float(h_in) * 25.4, 2)],
           "elements": elements}
    # 诊断字段：画在图上、却没进元素表的 artist（`census` 在 instrument 里采）。
    # 可选、只在非空时出现——旧前端不认识它会原样忽略，写回自检只比 gid 集合
    # 与几何，不看这里。有它才谈得上「知道自己漏了什么」（§35）。
    # 登记了却量不出几何的那些同样要报：`census` 判「已知」用的是登记表，
    # 不并进来的话它们在 `elements` 与 `unsupported` 两头都不出现——那正是
    # 「不许静默消失」要防的情况（自定义 Artist 只实现 draw、没重写
    # get_window_extent 时基类回空框，就会走到这儿）。
    rows = list(state.unregistered)
    rows += [{"cls": cls, "where": where, "count": n, "reason": why}
             for (cls, where, why), n in sorted(dropped.items())]
    if rows:
        out["unsupported"] = rows
    return out
