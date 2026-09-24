"""色条（colorbar）族：`ColorbarProxy` 伪元素、方向 / 延伸的就地结构改造、position override 落到色条轴时
的长宽比处置、色条反查与「拖它时谁跟着走」的随行表。

2026-09-18 审计任务书 PR D 第二步的第三刀（`docs/architecture/figstate-dependencies.md` 的顺序：
spine → tick → **colorbar** → legend），从 `overrides.py` 按 artist family 切出来。只依赖标准库 +
matplotlib + 更早切出的族（`axestraversal` / `tickmodel`）：不 import `overrides`，也不 import
`manifest`——`overrides.HANDLERS` 只登记这里导出的 getter / setter（`HANDLERS` 按原位置展开进去，
顺序一个字节不变），撤销登记走 `RESTORE`，`manifest` 直接从这里取只读判据与随行表。正文逐字未改。

与 `overrides.FigState` 的关系是**协议**而不是 import（`FollowState`）：方向翻转要读 `.pending`
（这一次 apply 之后宿主落在哪）、翻完把 `.colorbar_axes` / `.axes_follow` 重算回去；
`_cb_release_aspect` / `_cb_restore_aspect` 是 axes 几何那一侧（`overrides._set_axes_position` /
`_restore_axes_position`）留下的两个调用点。
"""

from __future__ import annotations

from typing import Protocol

import tickmodel
from axestraversal import ordered_axes


class FollowState(Protocol):
    """色条族对 `overrides.FigState` 的全部要求，族模块不 import 它。

    前四样是方向翻转要的；`elements` / `originals` 是**共用色阶组**要的
    （`scale_siblings` 只在登记表里找兄弟、`_restore_cb_cmap` 按各自的原样放回）。
    """

    fig: object
    pending: dict | None
    colorbar_axes: set
    axes_follow: dict
    elements: list
    originals: dict

    def has_handler(self, artist, prop: str) -> bool: ...


class ColorbarProxy:
    """色条伪元素：字段落在 Colorbar 对象与其 mappable 上，命中/位置走宿主轴。

    **语义身份**（`identity`）：色条在 artist 树上没有自己的名字，现有 gid
    `axes_<色条轴序号>.colorbar` 是按 `fig.axes` 里的序号编的。方向翻转是
    **就地**改造（同一个 Axes 对象、`fig.axes` 顺序一个字节不动），所以那个
    gid 今天不会漂；但「色条是谁的色条」本来就该由宿主轴 + mappable 决定，
    而不是由邻居的排序决定。`identity` 记的就是这条语义身份，随 manifest
    下发（`colorbar_key`），并在 `state.index` 里登记成别名——将来真要重建
    色条轴时，旧文档的 `axes_i.colorbar` 与新身份都还认得出同一个对象。
    """

    def __init__(self, cb, host=None, cbax_gid: str = "", host_gid: str = "", ordinal: int = 0):
        self.cb = cb
        self.host = host  # 宿主 Axes（方向翻转的落位参照）
        self.cbax_gid = cbax_gid  # 色条**轴**的 gid（axes_i）
        self.host_gid = host_gid
        self.ordinal = ordinal  # 同一宿主上的第几条色条
        # box_aspect 基线必须在这一刻采：`instrument` 跑在 build 之后、任何
        # override 之前，而 extend 的 locator 会在渲染时把 box_aspect 改成
        # `aspect*shrink`——晚一步采就把它的中间态当成了脚本原样
        _cb_box_aspect0(cb)

    @property
    def identity(self) -> str:
        """稳定语义身份：宿主轴 + 色条序号（与 fig.axes 的排序无关）。"""
        return f"cbar:{self.host_gid or '?'}:{self.ordinal}"

    def set_gid(self, gid) -> None:
        """宿主轴已有 axes_i gid；伪元素靠 manifest bbox 命中。"""


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
# 色条方向：一次**就地**的结构改造，不是普通 setter
#
# `cb.orientation` 只是个属性，直接写它不会动布局、不会换刻度所属的轴、
# 也不会重画色带——图上什么都不变，界面却显示成横的，是最坏的那种「假支持」。
# 反过来，销毁重建色条（`cb.remove()` + `fig.colorbar(...)`）会往 `fig.axes`
# 里换一个新对象，全图 `axes_i` 的编号跟着漂，已有 override 与撤销全废。
#
# 这里走第三条：**同一个 Axes 对象原地改造**——
#   ① 换 orientation / ticklocation（决定长短轴与刻度落在哪条轴）；
#   ② 长短轴互换后重算落位（见 `_cb_place`，vertical↔horizontal 逐位可逆）；
#   ③ `_reset_locator_formatter_scale()` + `_draw_all()` 让 matplotlib 自己
#      重建色带网格、outline、刻度与 xlim/ylim；
#   ④ 把长轴标签搬到新的长轴上（旧轴那份要清掉，否则两条轴各有一份）。
# `fig.axes` 顺序一个字节不动 → gid 稳定 → 撤销 / 写回 / 重开全链路照旧。
# ---------------------------------------------------------------------------
_CB_TICKLOC = {"vertical": "right", "horizontal": "bottom"}

#: 翻转时「原来那一侧」映到哪一侧。`fig.colorbar(location="left"/"top")` 是
#: 完全合法的写法，而旧实现无论从哪儿来都只会落到 right/bottom：一个左侧
#: 竖色条翻成横的再翻回来，就永久搬到了右边——**方向明明转回原值了，
#: 图却回不去**，刻度也跟着换了边。撤销那条路（`_restore_cb_orientation`）
#: 一直是对的，走「把值设回去」这条路的才坏，两条路必须给出同一张图。
_CB_SIDE_FLIP = {"right": "bottom", "bottom": "right", "left": "top", "top": "left"}
#: 每种方向合法的侧（防止外部塞进来的怪值把落位算成 NaN）
_CB_SIDES = {"vertical": ("left", "right"), "horizontal": ("top", "bottom")}


def _cb_side0(cb) -> str:
    """脚本原本把色条放在哪一侧（首次改动前记下，之后一直用它当基准）。"""
    if not hasattr(cb, "_mm_cb_side0"):
        side = str(getattr(cb, "ticklocation", "") or "")
        orient = str(getattr(cb, "orientation", "vertical"))
        if side not in _CB_SIDES.get(orient, ()):
            side = _CB_TICKLOC.get(orient, "right")
        cb._mm_cb_side0 = side  # noqa: SLF001
        cb._mm_cb_orient0 = orient  # noqa: SLF001
    return cb._mm_cb_side0  # noqa: SLF001


def _cb_target_side(cb, to: str) -> str:
    """翻到 `to` 之后该落在哪一侧：回到原方向就用原侧，否则按 flip 表映过去。"""
    side0 = _cb_side0(cb)
    orient0 = getattr(cb, "_mm_cb_orient0", "vertical")
    side = side0 if to == orient0 else _CB_SIDE_FLIP.get(side0, "")
    return side if side in _CB_SIDES[to] else _CB_TICKLOC[to]


#: `Colorbar._inside` 是按 extend 切出来的那段 boundaries。它**只在 `__init__`
#: 里设过一次**——改 `cb.extend` 不动它，于是 `_draw_all()` 会拿 259 条边界去配
#: 256 块颜色，当场 TypeError。两者必须一起改。
_CB_INSIDE = {
    "neither": slice(0, None),
    "both": slice(1, -1),
    "min": slice(1, None),
    "max": slice(0, -1),
}
_CB_EXTENDS = ["neither", "both", "min", "max"]


def _cb_box_aspect0(cb):
    """色条轴的「没有 extend 时」的 box_aspect 基线。

    落位其实由 matplotlib 自己的 `_ColorbarAxesLocator` 每帧重算：它按 extend
    把位置收一收给三角让地方，并顺手把 `box_aspect` 改成 `aspect*shrink`。
    但它在 extend=='neither' 时**提前 return**，那个 box_aspect 再也收不回去
    ——于是「开了 extend 又关掉」的色条比从没开过的宽 10%。这里记下基线，
    每次改 extend 前先放回去，让 locator 每次都从同一个起点算。
    """
    if not hasattr(cb, "_mm_box_aspect0"):
        cb._mm_box_aspect0 = cb.ax.get_box_aspect()  # noqa: SLF001
    return cb._mm_box_aspect0  # noqa: SLF001


def _set_cb_extend(p: "ColorbarProxy", v) -> None:
    """开/关色条两端的延伸三角（neither / both / min / max）。

    与方向一样是结构改造：`extend` 决定 boundaries 的切法、outline 的形状、
    以及给三角让出来的地方。做完之后落位与原生
    `fig.colorbar(..., extend=…)` **逐位相同**（用例断言）。
    """
    cb = p.cb
    to = str(v) if str(v) in _CB_INSIDE else "neither"
    cb.ax.set_box_aspect(_cb_box_aspect0(cb))
    cb.extend = to
    cb._inside = _CB_INSIDE[to]  # noqa: SLF001 — 见 _CB_INSIDE 的注释
    cb._draw_all()  # noqa: SLF001


def _restore_cb_extend(p: "ColorbarProxy", orig) -> None:
    _set_cb_extend(p, orig)


def scale_siblings(state: FollowState, mappable) -> list:
    """与 `mappable` **共用同一份 norm 对象**的其它已登记 mappable——它的色阶兄弟。

    判据是 norm 的**对象身份**，不是名字、也不是数值相等：脚本把同一个
    `Normalize` / `PowerNorm` 实例交给几个 `pcolormesh` / `imshow`，就是在声明
    「这几块是同一个色阶」——matplotlib 自己也这么理解，`set_clim` 改的是那一份
    norm，几块一起变。而 cmap 是各拿各的引用（哪怕脚本传的是同一个对象，
    `set_cmap` 只换自己那份），所以「同一个色阶」这件事在 cmap 上要由我们兑现。
    2026-09-21 用户的 PRB 三联图：(b)(c) 两块网格共用一份 `PowerNorm`、只在 (c)
    旁边挂一条色条——从色条换色图，只有 (b) 变了，紧挨着色条的 (c) 纹丝不动。

    只在 `state.elements` 里找：不是登记元素的 mappable 没有 gid，别名组里放不下
    它、原样也无处可采。色条代理自己不算（它的 `norm` 是转发 mappable 的）。
    **原样采不到的也不算**（`state.has_handler(a, "cmap")`）：没有数组、归线组族的
    LineCollection 传了共用的 norm 时有 `set_cmap` 却没有 cmap handler，别名组替它
    代采不到原样，撤销时只能拿 mappable 的冒充（#474 评审第三轮）——不如不动它，
    反正它没在映射、色图换不换画面都一样。
    """
    norm = getattr(mappable, "norm", None)
    if norm is None:
        return []
    out = []
    for el in state.elements:
        a = el["artist"]
        if a is mappable or isinstance(a, ColorbarProxy):
            continue
        if (
            getattr(a, "norm", None) is norm
            and hasattr(a, "set_cmap")
            and state.has_handler(a, "cmap")
        ):
            out.append(a)
    return out


def scale_gids(state: FollowState, mappable) -> list[str]:
    """`scale_siblings` 的 gid 版。manifest 下发 `scale_gids` 时还会按「此刻真在映射」
    再筛一遍（`color_mapping_is_live`，在 manifest 那侧）：组员按 family 定、恒定，
    事实按此刻说。"""
    sibs = scale_siblings(state, mappable)
    return [el["gid"] for el in state.elements if el["artist"] in sibs]


def _set_cb_cmap(p: "ColorbarProxy", v, state: FollowState) -> None:
    """色条的色图写到它的 mappable **和全部色阶兄弟**上（见 `scale_siblings`）。"""
    m = p.cb.mappable
    m.set_cmap(v)
    for sib in scale_siblings(state, m):
        sib.set_cmap(v)


_set_cb_cmap._needs_state = True  # noqa: SLF001


def _restore_cb_cmap(p: "ColorbarProxy", orig, state: FollowState) -> None:
    """撤销：mappable 放回它自己的原样；兄弟各放各的——它们的原样由别名组在
    广播动手之前代采（`overrides.apply` 的 `alias_seeded`）。**记录不在就不动它**：
    组员都采得到原样（`scale_siblings` 只收有 cmap handler 的），记录不在只有一种
    情形——兄弟自己的 override 在同一轮撤销里排在色条之前、刚被它自己的 key 还原
    并把记录收走了，这时它已经站在原样上；再拿 mappable 的原样盖上去就是把别人的
    色图安在它头上（第九轮评审 P2）。"""
    m = p.cb.mappable
    m.set_cmap(orig)
    sibs = scale_siblings(state, m)
    for el in state.elements:
        if el["artist"] in sibs and (el["gid"], "cmap") in state.originals:
            el["artist"].set_cmap(state.originals[(el["gid"], "cmap")])


_restore_cb_cmap._needs_state = True  # noqa: SLF001


def _cb_label_text(cb) -> str:
    return (
        cb.ax.get_ylabel()
        if getattr(cb, "orientation", "vertical") == "vertical"
        else cb.ax.get_xlabel()
    )


def _cb_place(
    host_rect, cur_rect, to: str, *, from_side: str = "", to_side: str = ""
) -> list[float]:
    """翻转后色条轴该落在哪儿（figure 分数，matplotlib 的 bottom-origin）。

    规则：厚度取色条自己的短边、间距沿用它与宿主之间原本那道缝，长边跟宿主
    对齐——竖条在左/右，横条在上/下，长度铺满宿主。竖↔横来回翻**逐位可逆**
    （thick 与 pad 都能从对侧原样反解出来），所以撤销回来的图与没改过的
    完全一样。

    `from_side` 决定那道缝从哪个方向反解：色条在宿主左边时缝是
    `hx - (cx + cw)`，在右边时是 `cx - (hx + hw)`——按右侧一种算法反解一个
    左侧色条，得到的是一个负得离谱的 pad，随后被兜底成 0.04，缝就变了。
    """
    hx, hy, hw, hh = (float(v) for v in host_rect)
    cx, cy, cw, ch = (float(v) for v in cur_rect)
    thick = min(cw, ch)
    from_side = from_side or ("bottom" if to == "vertical" else "right")
    to_side = to_side or _CB_TICKLOC[to]
    pad = {
        "right": cx - (hx + hw),
        "left": hx - (cx + cw),
        "bottom": hy - (cy + ch),
        "top": cy - (hy + hh),
    }.get(from_side, 0.04)
    if not 0.0 <= pad <= 0.4:
        pad = 0.04
    if to == "horizontal":
        return (
            [hx, hy + hh + pad, hw, thick]
            if to_side == "top"
            else [hx, hy - pad - thick, hw, thick]
        )
    return (
        [hx - pad - thick, hy, thick, hh] if to_side == "left" else [hx + hw + pad, hy, thick, hh]
    )


def _cb_current_side(cb) -> str:
    """色条**此刻**在宿主的哪一侧（用来反解那道缝）。"""
    orient = str(getattr(cb, "orientation", "vertical"))
    side = str(getattr(cb, "ticklocation", "") or "")
    if side in _CB_SIDES.get(orient, ()):
        return side
    return _CB_TICKLOC.get(orient, "right")


def _cb_target_rect(p: "ColorbarProxy", to: str, state: FollowState):
    """翻转后的落位；用户自己摆过色条轴时返回 None（位置归 position override）。

    宿主的落位取**这一次 apply 之后**的值（pending 里点名了就用点名的），
    不是此刻的实况：热会话里 position 可能已经先改过，全量重放里它还没轮到，
    只看实况两条路会算出不同的位置——「所见 == 重放」当场就断了。
    """
    pending = state.pending or {}
    if (p.cbax_gid, "position") in pending:
        return None
    host_rect = pending.get((p.host_gid, "position"))
    if not (isinstance(host_rect, (list, tuple)) and len(host_rect) == 4):
        if p.host is None:
            return None
        host_rect = p.host.get_position().bounds
    # 这里要的是**画出来**的那个矩形（厚度、与宿主之间的缝），不是分配到的整格
    # ——`original` 还没经过 box_aspect 收缩，拿它反解厚度会粗好几倍。
    # extend 的收缩只发生在长轴上，而 `_cb_place` 读的恰好是短边与短轴方向的
    # 间距，两者不打架。
    return _cb_place(
        host_rect,
        p.cb.ax.get_position().bounds,
        to,
        from_side=_cb_current_side(p.cb),
        to_side=_cb_target_side(p.cb, to),
    )


def _cb_reorient(p: "ColorbarProxy", to: str, state: FollowState) -> None:
    cb = p.cb
    label = _cb_label_text(cb)
    # **必须在改 orientation/ticklocation 之前问一次**：`_cb_side0` 是惰性
    # 记账的，晚一步记下的就已经是被我们改过的值了
    side = _cb_target_side(cb, to)
    rect = _cb_target_rect(p, to, state)
    cb.orientation = to
    cb.ticklocation = side
    # 两条轴的标签都先清掉：旧长轴那份不清就会变成「横过来了但左边还挂着
    # 一行竖排文字」
    cb.ax.set_xlabel("")
    cb.ax.set_ylabel("")
    # make_axes_gridspec 给竖色条按了 box_aspect=20（强制细高）；不解开的话
    # set_position 会被它按回去
    cb.ax.set_box_aspect(None)
    cb.ax.set_aspect("auto")
    if rect is not None:
        cb.ax.set_position(rect)
    # 落位从此归我们（`_cb_place`）。`_ColorbarAxesLocator` 在 extend≠neither 时
    # 会按 `_colorbar_info['aspect']` 反推厚度，两套规则一起上只会打架——关掉
    # 它的 aspect 那一支，位置收缩（给延伸三角让地方）照旧由它做。
    info = getattr(cb.ax, "_colorbar_info", None)
    if isinstance(info, dict):
        info["aspect"] = False
    cb._mm_box_aspect0 = None  # noqa: SLF001 — 新的 box_aspect 基线
    cb._reset_locator_formatter_scale()  # noqa: SLF001 — 官方也是这么重建的
    cb._draw_all()  # noqa: SLF001
    if label:
        cb.set_label(label)
    # locator/formatter 被上面整套换掉了：刻度模型的「脚本原样」必须重采
    for which in ("x", "y"):
        tickmodel.invalidate_tick_cfg(cb.ax, which)
    _refresh_axes_follow(state)


def _cb_orientation_snapshot(p: "ColorbarProxy") -> dict:
    """撤销用的原始快照：方向 + 刻度侧 + 完整落位 + 长轴标签。"""
    ax = p.cb.ax
    info = getattr(ax, "_colorbar_info", None)
    return {
        "orientation": str(getattr(p.cb, "orientation", "vertical")),
        "ticklocation": str(getattr(p.cb, "ticklocation", "right")),
        # 落位记 original：locator 每帧从它推出 extend 收缩后的实际位置，
        # 记实际位置的话还原一次就再收缩一次
        "position": list(ax.get_position(original=True).bounds),
        # box_aspect 记**基线**而不是此刻观察到的值：extend 开着时
        # locator 已经把它改成了 aspect*shrink，那是中间态不是原样
        "box_aspect0": _cb_box_aspect0(p.cb),
        "info_aspect": info.get("aspect") if isinstance(info, dict) else None,
        "aspect": ax.get_aspect(),
        "anchor": ax.get_anchor(),
        "label": _cb_label_text(p.cb),
    }


def _colorbar_of_axes(a):
    """这个 axes 是色条轴时回它的 Colorbar，否则 None。判据是 matplotlib 自己
    挂的 `_ColorbarAxesLocator`（`Colorbar.__init__` 无条件装上，`cax=` 给的
    用户轴也有），不猜类名、不看 `_colorbar_info`（后者只有 `make_axes` 那条
    路才有）。"""
    return getattr(a.get_axes_locator(), "_cbar", None)


def _cb_release_aspect(a) -> None:
    """落了 position override 的色条轴：把厚度交给用户，不再由长宽比反推。

    `fig.colorbar(im, ax=ax)` 造出来的色条轴带着 `box_aspect=20`（`make_axes` /
    `make_axes_gridspec` 按的，强制细高），`apply_aspect` 每次 draw 都按它把
    宽度重算成高度的 1/20——`set_position` 给多宽都没用，用户在画布上把色条
    拖粗，下一帧就弹回去（实测 3.10.8：请求宽 0.1，画出来 0.02）。这正是
    「设了、界面也变了、下一帧弹回去」那种最坏的假支持，所以落位归用户的那一刻
    就把长宽比解开——与色条方向翻转（`_cb_reorient`）「落位从此归我们」同一套
    处置：`box_aspect` 清掉、`_mm_box_aspect0` 基线跟着清（extend 的 setter 每次
    从基线放回，基线不清的话开一次 extend 又把 20 按回去）、
    `_colorbar_info['aspect']` 关掉（extend≠neither 时 locator 会按它反推厚度）。

    解开之前的三个值记在轴上（`_mm_cb_aspect_stash`，**只记第一次**：拖动是
    连着落好几条 position，第二次再记就记成了已经解开的状态），撤销 position
    时原样放回——否则撤销之后色条停在解开的样子，比从没改过时粗四倍。
    `cax=` 是用户自己摆的轴、没有 box_aspect，这里什么都不动，撤销也不放回。
    """
    cb = _colorbar_of_axes(a)
    if cb is None or a.get_box_aspect() is None:
        return
    if not hasattr(a, "_mm_cb_aspect_stash"):
        info = getattr(a, "_colorbar_info", None)
        a._mm_cb_aspect_stash = (  # noqa: SLF001
            _cb_box_aspect0(cb),
            info.get("aspect") if isinstance(info, dict) else None,
        )
    a.set_box_aspect(None)
    cb._mm_box_aspect0 = None  # noqa: SLF001 — 新的 box_aspect 基线（与 _cb_reorient 同）
    info = getattr(a, "_colorbar_info", None)
    if isinstance(info, dict):
        info["aspect"] = False


def _cb_restore_aspect(a) -> None:
    """撤销 position 时把 `_cb_release_aspect` 解开的长宽比放回去。"""
    stash = getattr(a, "_mm_cb_aspect_stash", None)
    if stash is None:
        return
    del a._mm_cb_aspect_stash  # noqa: SLF001
    box_aspect0, info_aspect = stash
    cb = _colorbar_of_axes(a)
    a.set_box_aspect(box_aspect0)
    if cb is not None:
        cb._mm_box_aspect0 = box_aspect0  # noqa: SLF001
    info = getattr(a, "_colorbar_info", None)
    if isinstance(info, dict) and info_aspect is not None:
        info["aspect"] = info_aspect


def _set_cb_orientation(p: "ColorbarProxy", v, state: FollowState) -> None:
    # **第二个消费点。** manifest 那边多宿主时已经不宣称这条能力了，但
    # 「不宣称」挡不住一份**旧文档**：用户在 1.0 之前存过一条 orientation
    # override，重开时它照样会被发过来。只修一处等于没修
    # （见 CLAUDE.md「共享判据修一处不算修完」）。
    #
    # 这里**抛**而不是静默忽略：抛出去会变成 worker 的 warning，
    # 而 warning 一条即阻断写回——用户会看到「这条改不动」，
    # 而不是「写回成功了，但图和屏幕上不一样」。判据与 manifest 共用
    # `colorbar_host_count` 这一份实现。
    hosts = colorbar_host_count(p.cb)
    if hosts > 1:
        raise ValueError(
            f"multi_host_colorbar: 这条色条横跨 {hosts} 个子图，"
            f"方向切换在 1.0 里不支持（落位只按第一个宿主算，翻转后会被缩到"
            f"一图宽）。issue #69"
        )
    to = "horizontal" if str(v) == "horizontal" else "vertical"
    _cb_reorient(p, to, state)


_set_cb_orientation._needs_state = True  # noqa: SLF001


def _restore_cb_orientation(p: "ColorbarProxy", orig, state: FollowState) -> None:
    """按快照原样放回（落位/长宽比/锚点一并还原），再让 matplotlib 重画。"""
    if not isinstance(orig, dict):
        return
    cb = p.cb
    ax = cb.ax
    cb.orientation = orig["orientation"]
    cb.ticklocation = orig["ticklocation"]
    # 基准也一并放回：还原之后再改一次方向，得从脚本那份原样重新起算
    cb._mm_cb_side0 = orig["ticklocation"]  # noqa: SLF001
    cb._mm_cb_orient0 = orig["orientation"]  # noqa: SLF001
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_box_aspect(orig["box_aspect0"])
    ax.set_aspect(orig["aspect"])
    ax.set_anchor(orig["anchor"])
    ax.set_position(orig["position"])
    cb._mm_box_aspect0 = orig["box_aspect0"]  # noqa: SLF001
    info = getattr(ax, "_colorbar_info", None)
    if isinstance(info, dict) and orig.get("info_aspect") is not None:
        info["aspect"] = orig["info_aspect"]
    cb._reset_locator_formatter_scale()  # noqa: SLF001
    cb._draw_all()  # noqa: SLF001
    if orig["label"]:
        cb.set_label(orig["label"])
    for which in ("x", "y"):
        tickmodel.invalidate_tick_cfg(ax, which)
    _refresh_axes_follow(state)


_restore_cb_orientation._needs_state = True  # noqa: SLF001


# ---------------------------------------------------------------------------
# 色图反解的位图：独立 mappable 的色条 ↔ 已经成色的 RGB(A) 位图
# ---------------------------------------------------------------------------
#: 一个像素离色图查表最近那格的最大通道差（0–255）在这之内，才算「这格颜色画的」。
#: 8 位 PNG 的量化误差是 ±0.5，gouraud 着色在两格之间线性插值、偏离色图曲线也只有
#: 一两个单位；4 留出余量，又远小于叠加物（流线、标注）与色图之间的差距。
_FIELD_TOL = 4.0
#: 至少这么大比例的不透明像素落在色图上，才认这张位图是这条色条画出来的。
_FIELD_MIN_ON = 0.6
#: 落在色图上的像素至少要铺开色图全长的这么一段：白底照片会整片贴在「从白色起步」
#: 的色图端点上，on 比例很高，但它不是一张场图。
_FIELD_MIN_SPREAD = 0.1
#: 配对时抽样的像素上限（全分辨率反解只在第一次真要重着色时做）。
_FIELD_SAMPLE = 200_000
#: 唯一颜色多到这个数就不是色图渲染（照片、截图）：不配对，也不花时间反解。
_FIELD_MAX_UNIQUE = 250_000
#: 叠加物（流线、箭头）抗锯齿边缘向外找「底下的场」最多找几个像素。
_FIELD_BG_RADIUS = 6
#: 抗锯齿像素是叠加色 L 与底色 B 的线性混合 `a·L + (1-a)·B`，离底色的距离 `a·|L-B|`
#: 与覆盖率成正比；`|L-B|` 取邻域（这么多像素半径）里离底色最远的那个叠加像素——
#: 线芯。固定一个常数的话，深色细线与浅色粗线只能对上一种。
_FIELD_CORE_RADIUS = 2
#: `|L-B|` 的下限。两种情况会让邻域线芯低估它：半透明叠加物（用户 (a) 的流线
#: `alpha=0.69`：线芯本身就混着 31% 的底色，它离底色只有 0.55，真正的叠加色更远），
#: 以及只是略出容差的场像素（gouraud 插值、有损压缩）。按实测调的：深色不透明
#: 细线离浅底 0.8，走邻域线芯；半透明灰线在这个下限上，线芯带过去 8% 的底色变化。
_FIELD_CORE_FLOOR = 0.6


def _rgb8(arr):
    """位图数组 → (M, N, 3) uint8。float 位图按 0–1、整型按 0–255。"""
    import numpy as np

    a = np.asarray(arr)
    rgb = a[..., :3]
    if rgb.dtype.kind == "f":
        rgb = np.clip(np.rint(rgb * 255.0), 0, 255)
    return rgb.astype(np.uint8)


def _cmap_lut8(cmap):
    import numpy as np

    n = max(int(getattr(cmap, "N", 256)), 2)
    return (np.asarray(cmap(np.linspace(0.0, 1.0, n)))[:, :3] * 255.0).astype(np.float32)


def _nearest_lut(rgb8, lut):
    """每个像素离查表最近的那一格 (序号, 最大通道差)。先去重再比：色图渲染的
    唯一颜色通常只有几万种，远少于像素数。唯一颜色过多时回 None。"""
    import numpy as np

    flat = rgb8.reshape(-1, 3)
    packed = (flat[:, 0].astype(np.uint32) << 16) | (flat[:, 1].astype(np.uint32) << 8) | flat[:, 2]
    uniq, inv = np.unique(packed, return_inverse=True)
    if len(uniq) > _FIELD_MAX_UNIQUE:
        return None
    uc = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], 1).astype(np.float32)
    best = np.empty(len(uniq), np.int32)
    dist = np.empty(len(uniq), np.float32)
    step = max(1, 4_000_000 // (len(lut) * 3))
    for s in range(0, len(uniq), step):
        d = np.abs(uc[s : s + step, None, :] - lut[None]).max(-1)
        best[s : s + step] = d.argmin(1)
        dist[s : s + step] = d.min(1)
    shape = rgb8.shape[:2]
    return best[inv].reshape(shape), dist[inv].reshape(shape)


def _opaque(arr):
    import numpy as np

    a = np.asarray(arr)
    if a.shape[-1] < 4:
        return np.ones(a.shape[:2], bool)
    alpha = a[..., 3]
    return alpha > (0.5 if alpha.dtype.kind == "f" else 127)


class RasterField:
    """一张**已经成色**的位图（`imshow` 吃进去的 RGB(A)），经一条独立 mappable 色条的
    色图反解回标量场——色条换色图 / 改上下限时，位图跟着重新着色。

    2026-09-24 用户的 PRB 三联图 (a)：COMSOL 场图先离线渲染成 PNG，再 `imshow` 进
    子图；旁边的色条由 `ScalarMappable(norm=PowerNorm(...), cmap=FIELD_YELLOW)` 单独
    建出来——两者在 matplotlib 眼里毫无关系，从色条换色图，位图纹丝不动，色条与图
    从此对不上。这类「位图 + 独立色条」是拼论文图的常见做法（离线渲染、别的软件导出）。

    **原样是模式**：色条的色图与 norm 没被动过时画的就是脚本原件，一个像素不改；
    只有真要重着色时才在第一次做全分辨率反解（`_decode`）。判断放在 `draw` 前
    （`sync`），热会话、全量重放、导出走的是同一条路——只要 override 相同，画出来就
    相同。落在色图上的像素换成新色图下的颜色；不在色图上的（流线、球、文字）原样
    保留，其抗锯齿边缘按「离底色多近」把底色的变化量按比例带过去，免得流线四周
    留一圈旧色图的光晕。
    """

    def __init__(self, image, cb):
        import copy

        self.image = image
        self.cb = cb
        m = cb.mappable
        self.original = image.get_array()
        self.cmap0 = m.get_cmap()
        self.norm0 = copy.deepcopy(m.norm)
        self.key0 = self._key()
        self._applied = self.key0
        self._decoded = None
        self._cache: dict = {}
        # 钩在 `make_image` 而不是 `draw` 上：一个子图里有多张位图、后端又合成位图
        # （PDF / SVG 的 `image.composite_image`）时，`Axes.draw` 绕过每张图的 draw、
        # 直接调 `make_image` 拼成一张——钩在 draw 上导出会漏掉重着色
        base_make = image.make_image

        def make_image(*args, **kwargs):
            self.sync()
            return base_make(*args, **kwargs)

        image.make_image = make_image
        image._mm_field = self  # noqa: SLF001 — colorbar_maps / manifest 反查

    def _key(self):
        m = self.cb.mappable
        n = m.norm
        return (
            id(m.get_cmap()),
            type(n).__name__,
            getattr(n, "vmin", None),
            getattr(n, "vmax", None),
            getattr(n, "gamma", None),
            bool(getattr(n, "clip", False)),
        )

    def sync(self) -> None:
        key = self._key()
        if key == self._applied:
            return
        self._applied = key
        if key == self.key0:
            self.image.set_data(self.original)
            return
        try:
            self.image.set_data(self._recolor())
        except Exception:  # noqa: BLE001 — 重着色失败就画原件，不拦渲染
            self.image.set_data(self.original)

    def _decode(self):
        import numpy as np

        if self._decoded is not None:
            return self._decoded
        arr = np.asarray(self.original)
        rgb8 = _rgb8(arr)
        lut = _cmap_lut8(self.cmap0)
        near = _nearest_lut(rgb8, lut)
        if near is None:
            raise ValueError("too many colours")
        idx, dist = near
        on = (dist <= _FIELD_TOL) & _opaque(arr)
        t = idx.astype(np.float64) / (len(lut) - 1)
        values = np.asarray(self.norm0.inverse(t), dtype=np.float64)
        # 叠加物边缘：从场里往外逐像素找最近的场值（4 邻域膨胀，找 _FIELD_BG_RADIUS 圈）
        bg_idx = np.where(on, idx, -1)
        for _ in range(_FIELD_BG_RADIUS):
            miss = bg_idx < 0
            if not miss.any():
                break
            grown = bg_idx.copy()
            for sl_dst, sl_src in (
                ((slice(1, None), slice(None)), (slice(None, -1), slice(None))),
                ((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
                ((slice(None), slice(1, None)), (slice(None), slice(None, -1))),
                ((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
            ):
                src = bg_idx[sl_src]
                dst = grown[sl_dst]
                take = (dst < 0) & (src >= 0)
                dst[take] = src[take]
            bg_idx = grown
        edge = (~on) & (bg_idx >= 0)
        bg_old = lut[np.clip(bg_idx, 0, None)] / 255.0
        d = np.abs(rgb8.astype(np.float32) / 255.0 - bg_old).max(-1)
        # 找不到底色的叠加像素（球心这类大块叠加物的内部）按「完全是叠加物」算
        d_off = np.where(on, 0.0, np.where(bg_idx >= 0, d, 1.0)).astype(np.float32)
        core = d_off.copy()
        r = _FIELD_CORE_RADIUS
        h, w = core.shape
        pad = np.pad(d_off, r)
        for dy in range(2 * r + 1):
            for dx in range(2 * r + 1):
                np.maximum(core, pad[dy : dy + h, dx : dx + w], out=core)
        span = np.maximum(core, _FIELD_CORE_FLOOR)
        weight = np.where(edge, np.clip(1.0 - d / span, 0.0, 1.0), 0.0)
        bg_t = np.clip(bg_idx, 0, None).astype(np.float64) / (len(lut) - 1)
        bg_values = np.asarray(self.norm0.inverse(bg_t), dtype=np.float64)
        self._decoded = (on, values, edge, weight, bg_values, bg_old)
        return self._decoded

    def _recolor(self):
        import numpy as np

        key = self._applied
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        on, values, edge, weight, bg_values, bg_old = self._decode()
        m = self.cb.mappable
        arr = np.asarray(self.original)
        out = arr.astype(np.float32, copy=True)
        if arr.dtype.kind != "f":
            out /= 255.0
        out[on, :3] = np.asarray(m.to_rgba(values[on]), dtype=np.float32)[:, :3]
        if edge.any():
            new_bg = np.asarray(m.to_rgba(bg_values[edge]), dtype=np.float32)[:, :3]
            shift = (new_bg - bg_old[edge]) * weight[edge][:, None]
            out[edge, :3] = np.clip(out[edge, :3] + shift, 0.0, 1.0)
        self._cache = {key: out}  # 只留最近一份：拖色阶滑块时不攒内存
        return out


def _orphan_mappable(m) -> bool:
    """色条的 mappable 不画在任何地方（`ScalarMappable(norm, cmap)` 单独建出来的）。"""
    from matplotlib.artist import Artist

    return m is not None and not isinstance(m, Artist) and getattr(m, "axes", None) is None


def _field_fit(arr, cmap) -> float:
    """这张位图有多大比例的不透明像素是 `cmap` 画的（抽样）；铺不开色图全长回 0。"""
    import numpy as np

    a = np.asarray(arr)
    if a.ndim != 3 or a.shape[-1] not in (3, 4) or a.size == 0:
        return 0.0
    stride = max(1, int(np.ceil(np.sqrt(a.shape[0] * a.shape[1] / _FIELD_SAMPLE))))
    sub = a[::stride, ::stride]
    lut = _cmap_lut8(cmap)
    near = _nearest_lut(_rgb8(sub), lut)
    if near is None:
        return 0.0
    idx, dist = near
    opaque = _opaque(sub)
    if not opaque.any():
        return 0.0
    on = (dist <= _FIELD_TOL) & opaque
    if not on.any():
        return 0.0
    lo, hi = np.percentile(idx[on], [2, 98])
    if (hi - lo) / (len(lut) - 1) < _FIELD_MIN_SPREAD:
        return 0.0
    return float(on.sum()) / float(opaque.sum())


def _declared_parents(cb):
    """色条自己声明的宿主（`fig.colorbar(..., ax=...)` 记在 `_colorbar_info["parents"]`）；
    `cax=` 建的没有这份记录，回 None。"""
    info = getattr(getattr(cb, "ax", None), "_colorbar_info", None)
    parents = info.get("parents") if isinstance(info, dict) else None
    return list(parents) if parents else None


def _orphan_scopes(cbar_of_ax: dict, axes) -> list[tuple]:
    """独立 mappable 色条 → 它可以认领的那几个子图，**声明了宿主的排在前面**。

    `ax=` 建的色条已经说了自己描述谁：只在它的 parents 里找（#527 评审 P1：多面板图上
    `ax=ax0` 的色条曾认领 ax1 上一张恰好同色阶的无关图像；两条各挂一边的色条，先处理的
    那条把两张都拿走）。`cax=` 建的没说，才在全图里找——排在后面，只拿声明过宿主的
    色条挑剩下的，于是「一条色条 + 一格共用色条的多张图」照旧整组认领。
    """
    free = [ax for ax in axes if ax not in cbar_of_ax]
    out = []
    for cb in cbar_of_ax.values():
        if not _orphan_mappable(getattr(cb, "mappable", None)):
            continue
        parents = _declared_parents(cb)
        scope = [ax for ax in free if ax in parents] if parents else free
        out.append((parents is None, cb, scope))
    out.sort(key=lambda t: t[0])  # 稳定排序：有宿主的在前，各组内保持原序
    return [(cb, scope) for _, cb, scope in out]


def _unique_colours_ok(arr) -> bool:
    """全分辨率下唯一颜色数不超 `_FIELD_MAX_UNIQUE`——`_decode` 要的正是这一条。
    配对时的吻合度只看抽样（≤ `_FIELD_SAMPLE` 像素），抽样里的唯一颜色永远到不了上限，
    于是一张嵌着照片的大图会先报「已绑定」、第一次重着色才在反解里失败、默默画原件
    （#527 评审 P2）。配对的最后一步用同一个判据量全图。"""
    import numpy as np

    rgb = _rgb8(arr).reshape(-1, 3)
    packed = (rgb[:, 0].astype(np.uint32) << 16) | (rgb[:, 1].astype(np.uint32) << 8) | rgb[:, 2]
    return len(np.unique(packed)) <= _FIELD_MAX_UNIQUE


def bind_raster_fields(cbar_of_ax: dict, axes) -> None:
    """给每条**独立 mappable** 的色条找它画的那张 RGB(A) 位图，找到就绑成 `RasterField`。

    只在两边都没有别的解释时才配对：色条的 mappable 不画在任何地方、也没有已画出的
    图元与它共用 norm（那是色阶兄弟，`scale_siblings` 管）；位图是已经成色的三 / 四
    通道数组。一张位图只认一条色条，取吻合度最高、且过 `_FIELD_MIN_ON`、全图唯一颜色
    不超上限的那张；只在色条声明的宿主里找（`_orphan_scopes`）。
    可重入：已经绑过的位图（`_mm_field`）不再动。
    """
    drawn = [
        a
        for ax in axes
        if ax not in cbar_of_ax
        for a in [*getattr(ax, "images", []), *getattr(ax, "collections", [])]
    ]

    def _rasters(scope):
        return [
            im
            for ax in scope
            for im in getattr(ax, "images", [])
            if getattr(getattr(im, "get_array", lambda: None)(), "ndim", 0) == 3
        ]

    images = _rasters([ax for ax in axes if ax not in cbar_of_ax])
    taken = {id(im) for im in images if getattr(im, "_mm_field", None) is not None}
    for cb, scope in _orphan_scopes(cbar_of_ax, axes):
        m = cb.mappable
        if getattr(m.norm, "vmin", None) is None:
            continue
        try:
            m.norm.inverse(0.5)  # BoundaryNorm 这类不可逆：反解不出数值，不配对
        except Exception:  # noqa: BLE001
            continue
        if any(
            getattr(im, "_mm_field", None) is not None and im._mm_field.cb is cb for im in images
        ):
            continue
        if any(getattr(a, "norm", None) is m.norm for a in drawn):
            continue
        fits = []
        for im in _rasters(scope):
            if id(im) in taken:
                continue
            try:
                fit = _field_fit(im.get_array(), m.get_cmap())
            except Exception:  # noqa: BLE001 — 量不了就当不吻合
                fit = 0.0
            if fit >= _FIELD_MIN_ON:
                fits.append((fit, im))
        fits.sort(key=lambda t: -t[0])
        for _, im in fits:
            if _unique_colours_ok(im.get_array()):
                RasterField(im, cb)
                taken.add(id(im))
                break


def _norm_signature(norm):
    """判「两个 norm 画出来一样」用的签名：类型 + 上下限 + 已知的形状参数。
    认不全的 norm（自定义子类、带额外状态的）回 None——宁可不认，不可认错。"""
    from matplotlib import colors as mcolors

    known = (
        mcolors.Normalize,
        mcolors.LogNorm,
        mcolors.PowerNorm,
        mcolors.SymLogNorm,
        mcolors.AsinhNorm,
        mcolors.CenteredNorm,
        mcolors.TwoSlopeNorm,
    )
    if type(norm) not in known or getattr(norm, "vmin", None) is None:
        return None
    extra = tuple(
        getattr(norm, k, None)
        for k in ("gamma", "linthresh", "linscale", "linear_width", "vcenter")
    )
    return (type(norm), float(norm.vmin), float(norm.vmax), bool(norm.clip), extra)


def _same_cmap(a, b) -> bool:
    return a is b or (
        getattr(a, "name", None) is not None and a.name == getattr(b, "name", None) and a == b
    )


def adopt_equal_scales(cbar_of_ax: dict, axes) -> None:
    """独立 mappable 的色条认领**画出来一模一样**的图元：让它们共用色条那份 norm。

    `fig.colorbar(ScalarMappable(Normalize(0, 1), "viridis"), ax=ax)` 配
    `imshow(z, cmap="viridis", vmin=0, vmax=1)` 是常见写法：色条与图像各拿一份 norm，
    数值相同，matplotlib 眼里却毫无关系——从色条换色图、改上下限，图像都不动。
    色阶兄弟的判据是 norm 的**对象身份**（`scale_siblings`），这里只在一种情况下把
    「数值相同」提升成「同一个对象」：色条的 mappable 不画在任何地方（它存在的唯一
    意义就是描述别的图元），图元没有自己的色条，色图相同，norm 签名逐项相同。
    换上的 norm 与原来的数值一样，画面一个像素不变；换完之后兄弟、别名组、
    `scale_gids` 全走原有那一套。只在色条声明的宿主里认领（`_orphan_scopes`），一个图元
    只归一条色条。
    """

    def _mapped(scope):
        return [
            a
            for ax in scope
            for a in [*getattr(ax, "images", []), *getattr(ax, "collections", [])]
            if hasattr(a, "norm")
            and hasattr(a, "get_cmap")
            and getattr(a, "get_array", lambda: None)() is not None
        ]

    drawn = _mapped([ax for ax in axes if ax not in cbar_of_ax])
    for cb, scope in _orphan_scopes(cbar_of_ax, axes):
        m = cb.mappable
        sig = _norm_signature(m.norm)
        if sig is None or any(a.norm is m.norm for a in drawn):
            continue
        for a in _mapped(scope):
            if getattr(a, "colorbar", None) is not None or getattr(a, "_mm_adopted", False):
                continue
            if getattr(getattr(a, "get_array", lambda: None)(), "ndim", 0) == 3:
                continue  # 已经成色的位图不走色图，归 `bind_raster_fields`
            if _norm_signature(a.norm) == sig and _same_cmap(a.get_cmap(), m.get_cmap()):
                a.norm = m.norm
                a._mm_adopted = True  # noqa: SLF001 — 可重入：认领过的不再比


def field_image_of(cb, axes):
    """这条色条绑定的那张反解位图（`bind_raster_fields` 绑的）；没有回 None。"""
    for ax in axes:
        for im in getattr(ax, "images", []):
            field = getattr(im, "_mm_field", None)
            if field is not None and field.cb is cb:
                return im
    return None


# ---------------------------------------------------------------------------
# 色条反查与「拖它时谁跟着走」（manifest.instrument 与色条方向事务共用）
# ---------------------------------------------------------------------------
def colorbar_host_count(cb) -> int:
    """这条色条**声明了几个宿主**。1 = 常规；>1 = 横跨多个子图。

    唯一判据是 matplotlib 自己记的 `cax._colorbar_info["parents"]`。
    实测（3.10.8，六种建法逐个量过，见
    `tests/test_colorbar_orientation.py::test_the_multi_host_predicate_matches_matplotlib`）::

        ax=ax                    parents=1
        ax=[a1, a2]              parents=2
        ax=[a, b, c]             parents=3
        cax=<用户自己建的轴>       没有 _colorbar_info      → 按 1 算
        ScalarMappable + ax=ax   parents=1（mappable.axes 是 None）
        ScalarMappable + ax=[..] parents=2

    `cax=` 那条按 1 算是对的、不是兜底：用户自己建了色条轴、自己摆好了位置，
    「宿主是谁」这个问题在那条路上根本不存在，落位也不归我们算。

    **为什么要有这个函数**：`_cb_target_rect()` 反解新矩形时只拿得到
    `cb.mappable.axes`，也就是**第一个**宿主。多宿主色条翻转方向之后会被缩到
    一图宽（实测 3.10.8 / 3.11.1：应当 0.620 宽，实际 0.282）。
    真修法要把宿主从一个 axes 改成一组、`_cb_place` / `_cb_target_rect` /
    `axes_follow` 三处按并集算——那是落位模型的改动，1.0 稳定期不做（issue #69）。
    在那之前**不宣称这条能力**：宁可少开放一个，不可开放了却画错。
    """
    cax = getattr(cb, "ax", None)
    info = getattr(cax, "_colorbar_info", None)
    parents = info.get("parents") if isinstance(info, dict) else None
    return len(parents) if parents else 1


def colorbar_maps(fig, axes) -> tuple[dict, dict]:
    """(色条轴 → Colorbar, 色条轴 → 宿主 axes)。**两个方向取并集**。

    **只走 `mappable.colorbar` 是不够的**：那是一个 mappable 上的**单个**引用，
    同一个 mappable 交给 `fig.colorbar()` 两次（左边一条竖的、下面一条横的，
    论文图里很常见），它只指向**最后**建的那条，先建的那条整个不被认出来。
    一根色条轴只承载一条色条，所以从**轴**反查（`cax._colorbar`）才是一对一的。
    实测（3.8.4 / 3.10.8 / 3.11.1 一致，`ax=` / `cax=` / `ax=[多宿主]` 三种建法
    也一致）：正查认出 1 条、漏 1 条，反查两条都在。

    **宿主也要两条路**：主判据是 `cb.mappable.axes`，`_colorbar_info["parents"]`
    是回退。两者各有各的盲区，谁都不能单独用：

      * 显式 `fig.colorbar(im, cax=…)` 那条路上 `_colorbar_info` **根本不存在**；
      * 文档里的独立 mappable 用法 `fig.colorbar(ScalarMappable(...), ax=ax)`
        里，那个 mappable **不属于任何 axes**，`mappable.axes` 是 None。

    没有宿主不是「少一条随行关系」那么轻：`host_gid` 空 → 语义身份退化成
    `cbar:?:0` → 不进 `axes_follow`（拖宿主色条不跟着走）→ **方向翻转算不出
    新矩形**。实测：翻成横向之后色条轴仍是 `0.116 × 0.77` 的竖条（有宿主的
    对照是 `0.462 × 0.116`），一根横色条被塞在竖框里，全程无报错。

    `axes` **要传 `axestraversal.ordered_axes(fig)[0]`**，别让它退回 `fig.axes`：
    `ax.inset_axes()` 的宿主只存在于 `child_axes` 里，扫不到它就扫不到它身上的
    mappable，于是那条色条**整个不被认出来**。后果不是「少一个元素」：

      * 色条轴不在 `cbar_of_ax` 里 → `instrument` 不建 `ColorbarProxy`，
        方向 / extend / 刻度那一整套没了；
      * 更糟的是它也不再挡住 Collection 族的登记闸（`ax in cbar_of_ax`），
        于是 `cb.solids`（QuadMesh）与 `cb.dividers`（LineCollection）被当成
        用户的图元登记成可编辑 collection——而它们**每次 `_draw_all()` 都被
        删掉重建**。override 于是挂在一个随时换身份的幽灵上。

    实测（`fig.colorbar(im, ax=ax.inset_axes(...))`）：认出 0 个色条轴、
    没有 colorbar 元素、`axes_1.collections_1` 泄漏进元素表。

    `axes` **是必填的**，不给默认值。给了 `axes=None → fig.axes` 那种兜底之后，
    「哪些 axes 存在」这个判断在本函数里仍然写着一次，于是
    `tests/test_axes_traversal_authority.py` 那条源码级看护只能按函数放行整个
    函数——而实测：把函数体里另一处改回 `fig.axes`，那条看护照样绿。
    **一个放行整函数的豁免挡不住函数内部的回归**，不如让兜底根本不存在。
    """
    cbar_of_ax: dict = {}
    host_of_cbax: dict = {}

    def _remember(cb, cax, host) -> None:
        cbar_of_ax[cax] = cb
        if host is not None and host is not cax and host in axes:
            host_of_cbax[cax] = host

    # ① 从**色条轴自己**反查。这是完整的那一半：一根轴只承载一条色条，
    #    所以 `cax._colorbar` 是一对一的，同一个 mappable 建了几条都数得清。
    def _host_of(cb, cax):
        host = getattr(getattr(cb, "mappable", None), "axes", None)
        if host is not None:
            return host
        # 独立 mappable（`ScalarMappable(...)` 不挂在任何 axes 上）走这条。
        info = getattr(cax, "_colorbar_info", None)
        parents = info.get("parents") if isinstance(info, dict) else None
        if parents:
            return parents[0]
        # `cax=` 显式建的独立 mappable 色条两条都落空：它描述的对象所在的子图就是
        # 宿主——绑定的反解位图（`bind_raster_fields`），或与它共用 norm 的图元
        # （脚本传的同一个对象，或 `adopt_equal_scales` 认领的）
        field = field_image_of(cb, axes)
        if field is not None:
            return field.axes
        norm = getattr(getattr(cb, "mappable", None), "norm", None)
        for other in axes:
            if getattr(other, "_colorbar", None) is not None:
                continue
            for a in [*getattr(other, "images", []), *getattr(other, "collections", [])]:
                if norm is not None and getattr(a, "norm", None) is norm:
                    return other
        return None

    for ax in axes:
        cb = getattr(ax, "_colorbar", None)
        if cb is not None and getattr(cb, "ax", None) is ax:
            _remember(cb, ax, _host_of(cb, ax))

    # ② 再从 mappable 正查一遍。①用的是**私有**属性，哪天上游改名，只剩这一条
    #    也还认得出单色条的常规图——而不是一个色条都认不出来（那会让每张带色条
    #    的图都泄漏内部件，是静默的全面失效）。两个方向取并集，谁先谁后不影响
    #    结果：同一根 cax 反查出来的必然是同一个 Colorbar。
    for ax in axes:
        for sm in [*ax.images, *ax.collections]:
            cb = getattr(sm, "colorbar", None)
            if cb is not None and cb.ax is not ax:
                _remember(cb, cb.ax, ax)
    return cbar_of_ax, host_of_cbax


def follow_map(fig, cbar_of_ax: dict, host_of_cbax: dict, axes) -> dict[str, list[str]]:
    """宿主 axes gid → 拖动它时该一起走的其他 axes gid。

    子图自己的标题 / 轴标签 / 刻度是 Axes 的孩子，set_position 一挪它们天然
    跟着走（被用户 override 过位置的那些例外，见前端 axesCompanions）。这里
    收的是**另外的 axes**——它们和宿主在视觉上是一体，在 artist 树上却是平级：

      * 色条轴：`fig.colorbar` 造出来的独立 axes，宿主挪走它自己留在原地；
      * 孪生轴：`twinx()` / `twiny()` 叠在同一块地方的第二套刻度。

    共享 ≠ 孪生。`subplots(sharex=True)` 同样共享 x 轴，但那是并排的另一个
    子图——只看共享关系会把整行子图一起拖走，所以判据必须再加「position
    基本重合」。判据用公开的 get_shared_[xy]_axes()，不碰 `_twinned_axes`；
    判据本身只有 `coincident_shared_axes_pairs` 一份（manifest 的孪生轴
    标签也吃它，别再写第二份）。
    """
    # **编号与遍历都必须用 `axestraversal.ordered_axes`**（由调用方传进来）。用 `fig.axes`
    # 的话，插图宿主不在里面 → `gid_of_ax.get(host)` 是 None → `link()` 直接
    # 返回，这条随行关系**被无声丢掉**。实测
    # `fig.colorbar(im, ax=ax.inset_axes(...))`：`colorbar_maps` 认出来了、
    # `follow_map` 回 `{}`，于是拖动宿主时色条留在原地。
    # 这是同一条纪律的第四个入口——而它是**上一个修复才让它够得着的**：色条
    # 先要被认出来，这条关系才有机会被丢。
    # `axes` 必填，理由同 `colorbar_maps`：留一个 `fig.axes` 兜底，源码级看护
    # 就只能整函数放行，函数内部改回去它照样绿（实测过）。
    ordered = axes
    gid_of_ax = {ax: f"axes_{i}" for i, ax in enumerate(ordered)}
    follow: dict[str, list[str]] = {}

    def link(host, other) -> None:
        h, o = gid_of_ax.get(host), gid_of_ax.get(other)
        if h is None or o is None or h == o:
            return
        bucket = follow.setdefault(h, [])
        if o not in bucket:
            bucket.append(o)

    for cbax, host in host_of_cbax.items():
        link(host, cbax)

    for ax, other in coincident_shared_axes_pairs(ordered, cbar_of_ax):
        link(ax, other)

    return follow


def coincident_shared_axes_pairs(ordered, cbar_of_ax) -> list[tuple]:
    """「孪生轴」判据的**唯一出处**：共享 x 或 y + position 基本重合。

    两个消费方：`follow_map`（拖动宿主时孪生轴一起走）与 manifest 的
    `_twin_axes_labels`（「子图 N（右轴）」的可区分标签）。判据只有这一份
    ——分开写的话，「拖动时跟着走的」与「标着（右轴）的」迟早不是同一批。
    用公开的 `get_shared_[xy]_axes()`，不碰 `_twinned_axes`（follow_map
    定下的裁决），顺带把 `fig.add_axes(同位置, sharex=…)` 手搓出来的孪生
    也认进来——它们与 `twinx()` 在用户眼里是同一个东西。

    对 (ax, other) 双向各出现一次；按 `ordered`（`axestraversal.ordered_axes` 的遍历序）
    枚举而不是遍历 siblings 集合：集合序不稳定，manifest 要逐字节可复现
    （写回校验拿它比对）。
    """
    pairs: list[tuple] = []
    for ax in ordered:
        if ax in cbar_of_ax:
            continue
        try:
            pos = ax.get_position().bounds
            siblings = set()
            for grouper in (ax.get_shared_x_axes(), ax.get_shared_y_axes()):
                siblings.update(grouper.get_siblings(ax))
        except Exception:  # noqa: BLE001 — 关联判定失败只是少一条联动，不拦渲染
            continue
        for other in ordered:
            if other is ax or other in cbar_of_ax or other not in siblings:
                continue
            if all(abs(a - b) < 1e-6 for a, b in zip(pos, other.get_position().bounds)):
                pairs.append((ax, other))
    return pairs


def _refresh_axes_follow(state: FollowState) -> None:
    """结构改造之后重算随行关系（色条方向翻转会改变谁和谁挨着）。"""
    try:
        # 与 `instrument` 同一条遍历（插图里的宿主不在 `fig.axes` 里）。
        _ordered = ordered_axes(state.fig)[0]
        cbar_of_ax, host_of_cbax = colorbar_maps(state.fig, _ordered)
        state.colorbar_axes = set(cbar_of_ax)
        state.axes_follow = follow_map(state.fig, cbar_of_ax, host_of_cbax, _ordered)
    except Exception:  # noqa: BLE001 — 少一条联动不该拦渲染
        pass


#: `overrides.HANDLERS` 里色条那一段（`ColorbarProxy` 伪元素），按原位置 `**` 展开。
HANDLERS: dict[tuple[str, str], tuple] = {
    ("colorbar", "label"): (
        lambda p: _cb_axis(p).label.get_text(),
        lambda p, v: p.cb.set_label(str(v)),
    ),
    # 色图写到 mappable 与它的色阶兄弟（共用 norm 对象的那些，`scale_siblings`）；
    # 原样仍是 mappable 自己那张 Colormap（`manifest._cmap_original` 读它）
    ("colorbar", "cmap"): (lambda p: p.cb.mappable.get_cmap(), _set_cb_cmap),
    # vmin / vmax 写的是 norm，而 norm 是兄弟们共用的那一份——不必逐个写，
    # 但别名组要把兄弟算进来（`overrides._alias_colorbar_mappable`），否则兄弟的
    # 「脚本原样」会在色条动过之后才采
    ("colorbar", "vmin"): (
        lambda p: p.cb.mappable.get_clim()[0],
        lambda p, v: p.cb.mappable.set_clim(vmin=(None if v is None else float(v))),
    ),
    ("colorbar", "vmax"): (
        lambda p: p.cb.mappable.get_clim()[1],
        lambda p, v: p.cb.mappable.set_clim(vmax=(None if v is None else float(v))),
    ),
    ("colorbar", "tick_fontsize"): (
        _cb_tick_fontsize,
        lambda p, v: p.cb.ax.tick_params(labelsize=float(v)),
    ),
    ("colorbar", "tick_color"): (_cb_tick_color, lambda p, v: p.cb.ax.tick_params(labelcolor=v)),
    ("colorbar", "outline_visible"): (
        lambda p: bool(p.cb.outline.get_visible()),
        lambda p, v: p.cb.outline.set_visible(bool(v)),
    ),
    ("colorbar", "outline_width"): (
        lambda p: float(p.cb.outline.get_linewidth()),
        lambda p, v: p.cb.outline.set_linewidth(float(v)),
    ),
    ("colorbar", "visible"): (
        lambda p: p.cb.ax.get_visible(),
        lambda p, v: p.cb.ax.set_visible(bool(v)),
    ),
    # 方向：就地结构改造（见上方 `_cb_reorient`），不是普通 setter。
    # 原生值是一整份快照，撤销走 _RESTORE 里的专用函数
    ("colorbar", "orientation"): (_cb_orientation_snapshot, _set_cb_orientation),
    # 两端的延伸三角。同样是结构改造：改 extend 必须连 `_inside` 一起改，
    # 否则 `_draw_all()` 会拿错长度的边界去配颜色（见 _CB_INSIDE）
    ("colorbar", "extend"): (lambda p: str(getattr(p.cb, "extend", "neither")), _set_cb_extend),
}

#: 撤销：方向按快照原样放回（落位 / 长宽比 / 锚点一并还原），延伸退回原值并同步 `_inside`。
RESTORE: dict[tuple[str, str], object] = {
    ("colorbar", "cmap"): _restore_cb_cmap,
    ("colorbar", "orientation"): _restore_cb_orientation,
    ("colorbar", "extend"): _restore_cb_extend,
}
