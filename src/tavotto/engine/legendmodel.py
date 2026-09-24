"""图例（legend）族：loc 预设与位置模型、条目模型（稳定序号 / 源对象绑定 / 隐藏 / 重排）、
示意线读写、重建与重建后的接回，以及 `apply` 每轮末尾的同步。

2026-09-18 审计任务书 PR D 第二步的第四刀（`docs/architecture/figstate-dependencies.md` 的顺序：
spine → tick → colorbar → **legend**），从 `overrides.py` 按 artist family 切出来。只依赖标准库 +
matplotlib + 基座 `pathgeom`（`frac_to_display`）：不 import `overrides`，也不 import `manifest`——
`overrides.HANDLERS` 只登记这里导出的 getter / setter（`HANDLERS_BASIC` / `HANDLERS_LAYOUT` 按原位置
展开进去，顺序一个字节不变），撤销登记走 `RESTORE`，`legend_text` 那组镜像登记仍由 overrides 接线
（它是分发层的事）。正文逐字未改。

与 `overrides.FigState` 的关系是**协议**而不是 import（`RebuildState`）：重建后把新文字对象接回
`.index` / `.elements`，并按 `.applied` 用 `.reapply(artist, prop, value)` 重放已应用的 override——
分发（HANDLERS 查表）留在 overrides，族模块只提要求。
"""

from __future__ import annotations

import copy
import inspect
import math
import sys
from typing import Protocol

import matplotlib.colors as mcolors
import numpy as np
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.collections import Collection, LineCollection
from matplotlib.legend import Legend
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.patches import BoxStyle, Patch
from matplotlib.text import Text
from matplotlib.transforms import Affine2D

import pathgeom
from axestraversal import ordered_axes


class RebuildState(Protocol):
    """图例族对 `overrides.FigState` 的全部要求——族模块不 import 它。"""

    fig: object
    index: dict
    elements: list
    applied: dict

    def reapply(self, artist, prop: str, value) -> None: ...


def _set_legend_fontsize(leg, value) -> None:
    """图例字号：标量作用于每一条，序列逐条对应（多余的忽略、缺的沿用最后一个）。

    序列那一支是给**撤销**用的：`originals` 里存的就是 getter 回的那份逐条
    列表。只认标量的话，改过图例字号之后就再也还原不回去。
    """
    texts = list(leg.get_texts())
    if isinstance(value, (list, tuple)):
        if not value:
            return
        for i, t in enumerate(texts):
            t.set_fontsize(float(value[min(i, len(value) - 1)]))
        return
    size = float(value)
    for t in texts:
        t.set_fontsize(size)


# ---------------------------------------------------------------------------
# 图例：loc 预设、条目模型（稳定序号 / 源对象绑定 / 隐藏 / 重排）与重建
# ---------------------------------------------------------------------------
_LEGEND_LOCS = [
    "best",
    "upper right",
    "upper left",
    "lower left",
    "lower right",
    "right",
    "center left",
    "center right",
    "lower center",
    "upper center",
    "center",
]


# ---------------------------------------------------------------------------
# 图例位置模型（2026-09-07，ADR 0034 修订：外侧锚点）
#
# 「图例摆在哪」有三条 prop，它们改的是**同一件事**：
#
#   loc         预设档位（九宫格 + best），matplotlib 的 `Legend.set_loc`
#   loc_frac    画布上拖出来的绝对位置（figure 分数，见 `_FRAC_ANCHORED`）
#   loc_anchor  锚点 `bbox_to_anchor`（父容器分数坐标）——把图例放到子图**外面**
#
# 三条各自当独立 setter 的话它们会互相盖写：`set_loc` 之前必须清锚框
# （否则 loc 被解释成相对锚框的位置，图例乱飞），而设锚框又不能动 loc。
# 于是**谁先谁后就是两张图**——而应用顺序在同一档里就是 patch 列表序，
# 热会话的增量应用与冷启动的全量重放会在这里分叉，写回自检报 divergence
# （运气不好时几何差在容差内，静默写出与用户所见不同的图）。
#
# 所以走边框 / 刻度那套路数：**三条 prop 各写自己的槽位，再整体重建**。
# 顺序从此不影响结果，撤销一条 = 那个槽位退回「未表态」（落回脚本原样），
# 而不是把当前推断出来的值钉死。
#
# 优先级（写在这里，不写在三个 setter 里）：
#   * 拖动过（loc_frac 在）→ 位置就是那个点，**锚框强制清掉**——拖动是绝对
#     定位，留着锚框的话那个点会被解释成相对锚框，图例飞出画面。
#   * 否则位置看 loc 槽、锚框看 anchor 槽；两个槽都空 = 脚本原样。
# 前端选预设时会把 loc_frac 那条 override 一起删掉（`setLegendPlacement`），
# 否则用户点了预设却看不见变化。
# ---------------------------------------------------------------------------

#: 「这个槽位没人表态」。**不能用 None 代替**：`loc_anchor` 的 `None` 是一个
#: 合法取值（「不要锚框，回到子图内侧」），与「没表态」（用脚本原样的锚框）
#: 是两个不同的答案——脚本自己写了 bbox_to_anchor 时两者画出来的图不一样。
_POS_UNSET = object()

_LEGEND_POS_SLOTS = ("loc", "loc_frac", "anchor")


def legend_pos_cfg(leg: Legend) -> dict:
    """取（必要时新建）一个图例的位置模型。

    `orig` 是**脚本原样**的 `(loc, 锚框)`——`_register_legend` 在 instrument
    时调一次，保证采的是任何 override 之前的样子。锚框存的是**原对象**：
    它多半是个 `TransformedBbox`，交给 `set_bbox_to_anchor` 会被再包一层
    变换，坐标当场爆炸，所以还原时只能直接放回属性。
    """
    cfg = getattr(leg, "_mm_pos_cfg", None)
    if cfg is None:
        cfg = {k: _POS_UNSET for k in _LEGEND_POS_SLOTS}
        cfg["orig"] = (leg._loc, leg._bbox_to_anchor)  # noqa: SLF001
        leg._mm_pos_cfg = cfg  # noqa: SLF001
    return cfg


def _legend_parent_transform(leg: Legend):
    """锚点坐标的参照系：Axes 图例是子图分数，figure 图例是图幅分数。

    与 `set_bbox_to_anchor(bbox, transform=None)` 的默认值（`BboxTransformTo`
    of `parent.bbox`）是同一个变换——`legend_anchor_state` 的同源判据就是拿它
    去比的。
    """
    parent = leg.parent  # Axes 或 Figure
    return parent.transAxes if isinstance(parent, Axes) else parent.transFigure


def apply_legend_pos_model(leg: Legend) -> None:
    """按 cfg **整体重建**图例的位置与锚框（优先级见本节顶部）。"""
    cfg = legend_pos_cfg(leg)
    orig_loc, orig_bbox = cfg["orig"]
    frac = cfg["loc_frac"]
    anchor = cfg["anchor"]

    if frac is not _POS_UNSET or anchor is None:
        leg.set_bbox_to_anchor(None)
    elif anchor is _POS_UNSET:
        leg._bbox_to_anchor = orig_bbox  # noqa: SLF001
    else:
        leg.set_bbox_to_anchor(
            (float(anchor[0]), float(anchor[1])), transform=_legend_parent_transform(leg)
        )

    if frac is not _POS_UNSET:
        fig = leg.get_figure()
        disp = pathgeom.frac_to_display(fig, float(frac[0]), float(frac[1]))
        leg.set_loc(tuple(_legend_parent_transform(leg).inverted().transform(disp)))
    elif cfg["loc"] is _POS_UNSET:
        leg.set_loc(orig_loc)
    else:
        leg.set_loc(str(cfg["loc"]))
    leg.stale = True


def _mk_legend_pos_setter(slot: str):
    def setter(leg: Legend, v) -> None:
        legend_pos_cfg(leg)[slot] = v
        apply_legend_pos_model(leg)

    return setter


def _mk_legend_pos_restore(slot: str):
    """撤销一条位置 prop = 那个槽位**退回未表态**，整份模型重建一次。

    不是「把此刻推断出来的值钉死」：`loc` 撤掉之后该回到脚本原样的 loc，
    而不是回到「刚才那个锚点算出来的落位」。
    """

    def restore(leg: Legend, _orig) -> None:
        legend_pos_cfg(leg)[slot] = _POS_UNSET
        apply_legend_pos_model(leg)

    return restore


#: 锚点**表达不出来**的两种形状，各带一个 reason code（界面按 code 翻，
#: 出口 `web/src/components/inspector/UnsupportedProps.tsx`）。判不出就别判，
#: 把盲点写在明处——把一个 4 元组锚框显示成「没有锚点」是个语义错的精确值。
LEGEND_ANCHOR_UNSUPPORTED = ("legend_anchor_box", "legend_anchor_transform")


def _same_transform(a, b) -> bool:
    """两个变换在数值上是不是同一个（拿三个点量，不比对象身份）。"""
    pts = [(0.0, 0.0), (1.0, 1.0), (0.37, 0.62)]
    try:
        return bool(np.allclose(a.transform(pts), b.transform(pts), atol=1e-6))
    except Exception:  # noqa: BLE001 — 量不了就当不同源
        return False


def legend_anchor_state(leg: Legend) -> tuple[list[float] | None, str | None]:
    """`(锚点 [x, y] 或 None, 表达不出来的原因 code 或 None)`。

    只有**父容器分数坐标里的一个点**才是这个模型认的锚点。实测 3.10.8：

      * `bbox_to_anchor=(1.02, 1)` → `TransformedBbox(Bbox(1.02,1,1.02,1),
        BboxTransformTo(parent.bbox))`，零尺寸，逆变换回来就是 `[1.02, 1.0]`；
      * 4 元组 `(0.1, 0.1, 0.5, 0.5)` → 逆变换回来是个**有尺寸的框**，
        这个模型摆不出来（`legend_anchor_box`）；
      * `bbox_transform=fig.transFigure` / `ax.transData` → 逆变换回来的数字
        此刻落位正确，但它钉的是另一个参照系，改成子图分数就是**换了语义**
        （子图一动两者就分家）——照实说不支持（`legend_anchor_transform`）。

    这两种形状**不发字段**：脚本原样照常渲染（没人写 override 就没人动它），
    撤销也照常（模型里存着原对象）。少的只是「在这里改它」这个能力。
    """
    bbox = leg._bbox_to_anchor  # noqa: SLF001
    if bbox is None:
        return None, None
    trans = getattr(bbox, "_transform", None)
    if trans is None or not _same_transform(trans, _legend_parent_transform(leg)):
        return None, "legend_anchor_transform"
    inv = _legend_parent_transform(leg).inverted()
    try:
        (x0, y0), (x1, y1) = inv.transform([(bbox.x0, bbox.y0), (bbox.x1, bbox.y1)])
    except Exception:  # noqa: BLE001 — 算不出就当表达不出来
        return None, "legend_anchor_transform"
    if not all(math.isfinite(float(v)) for v in (x0, y0, x1, y1)):
        return None, "legend_anchor_transform"
    if abs(float(x1) - float(x0)) > 1e-6 or abs(float(y1) - float(y0)) > 1e-6:
        return None, "legend_anchor_box"
    return [round(float(x0), 4), round(float(y0), 4)], None


def _get_legend_loc(leg: Legend):
    """`state.originals` 里存的「脚本原样」。

    **撤销不走它**——三条位置 prop 的还原都是 `_mk_legend_pos_restore`
    （槽位退回未表态 + 整体重建），脚本原样存在模型的 `orig` 里。这里回同
    一个形状只是为了让 originals 表里那条记录说得出它记的是什么。
    """
    return (leg._loc, leg._bbox_to_anchor)  # noqa: SLF001


def _legend_loc_name(leg: Legend) -> str:
    loc = leg._loc  # noqa: SLF001
    if isinstance(loc, (tuple, list)):
        return "custom"
    inv = {v: k for k, v in Legend.codes.items()}
    return inv.get(loc, "best")


#: 图例项的绑定模式（ADR 0034）。`follow_source`：图例上那条示意线由图中
#: 源对象**派生**，源变它就变；`custom`：示意线自己是一份状态，源变它不动。
#: 「没有源」不是第三档——那时根本没有 `binding` 这条字段（见
#: `manifest._legend_entry_fields`），界面显示「未关联图中对象」。
LEGEND_BINDINGS = ("follow_source", "custom")
#: 图例项示意线的样式 prop。任何一条落在 `state.applied` 里，这一项就是
#: `custom`——判据是**文档里有没有这条 override**，不是「值和源一不一样」：
#: 用户把颜色改成与源相同的值，仍然是「我要自己管这一项」。
LEGEND_ENTRY_STYLE_PROPS = (
    "handle_color",
    "handle_linestyle",
    "handle_linewidth",
    "handle_marker",
    "handle_markersize",
)
#: 条目的**状态类** prop：它们改的是条目模型（隐藏集 / 绑定表），不是某个
#: artist 的属性，重建后**不重放**（模型自己就是它们的落点）。
_LEGEND_ENTRY_STATE_PROPS = frozenset({"binding", "visible"})
#: `_init_legend_box` 里 handlebox 的几何（heuristic 与 matplotlib 逐字相同，
#: 3.10 `Legend._init_legend_box`）。改版时以它为准重对一次。
_LEGEND_HANDLE_MARKER_OPTS = ["None", "o", "s", "D", "^", "v", "<", ">", "x", "+", "*", "."]


class LegendEntries:
    """一个图例的**条目模型**（挂在 `leg._mm_entries` 上，`instrument` 时建）。

    条目按**原始序号** j 编号，`axes_i.legend.texts_j` 从此指的是原始第 j 项，
    不再是显示顺序的第 j 项——重排之后用户改过的那一项才不会「跟着位置跑」
    （改了第一项的字，再把它移到最后，字得跟着它走）。

    每一项记：源对象（图中那条曲线 / 散点 / 柱系列，找不到就是 None）、
    脚本原样的绑定（源找到且示意线与源一致 → follow_source；找到但脚本
    自己改过示意线 → custom）、当前 Text（隐藏的项保留最后那个 Text 对象，
    gid 与 override 都挂在它上面）、示意线的**脚本原样快照**（`pristine`）。

    **一个 custom 项「不带任何 handle_* override 时长什么样」= `pristine`，没有第二个
    答案（2026-09-19，#414）。** 第一版另有一份会话内的快照：从跟随状态脱开的项记成
    「脱开那一刻从源派生出来的样子」。那份样子**只活在会话里**——文档里只有
    `binding = custom`（或某条 handle_*），重放时脱开点落在源的所有 override 之后，
    源随后变过的话热态与重放就不是一张图（源改 marker 后脱开 vs 脱开后改 marker）。
    确定性只能来自文档：示意线 = 脚本原样 + 文档里的 handle_* override。界面上的
    「断开」把此刻的五条样式写成 override（`store/actions.detachLegendEntry`），所以
    用户看到的定格仍然成立——只是定格住的东西从此在文档里，不在进程里。
    """

    def __init__(self, leg: Legend, state: RebuildState) -> None:
        self.leg = leg
        self.state = state
        handles = [h for h in leg.legend_handles if h is not None]
        texts = list(leg.get_texts())
        n = min(len(handles), len(texts))
        self.n = n
        # 快照必须是**另一个对象**：handle_* override 改的是图例盒里那份活的
        # 示意线，而创建时那份正是它——不另存一份，「脚本原样」会被第一条
        # override 悄悄改掉，撤销就回不去了
        # 绑定用的指纹取自**创建时那份**示意线：误差棒的示意线是 LineCollection，
        # 快照只能造出 Line2D（HandlerLineCollection），拿快照比会永远对不上
        self.orig_fp: list[tuple] = [legend_handle_fingerprint(h) for h in handles[:n]]
        self.pristine: list = [self.snapshot(h) for h in handles[:n]]
        # 脚本自定义 handler 一格画了好几个 artist（色带 = 24 段矩形 + 描边）时，`pristine`
        # 只是其中第一个——重建只拿它就把整格画成一块纯色。这种格子原样定格一份（见
        # `FrozenLegendHandle`）；造不出定格的项是 None，照旧走 `pristine`
        boxes = _entry_boxes(leg)
        self.frozen: list = [
            _freeze_entry(leg, h, boxes[k][0]) if k < len(boxes) else None
            for k, h in enumerate(handles[:n])
        ]
        if any(f is not None for f in self.frozen):
            custom = dict(leg._custom_handler_map or {})  # noqa: SLF001
            custom[FrozenLegendHandle] = _FROZEN_HANDLER
            leg._custom_handler_map = custom  # noqa: SLF001
        self.orig_labels: list[str] = [t.get_text() for t in texts[:n]]
        self.texts: list = texts[:n]
        self.order: list[int] = list(range(n))
        self.hidden: set[int] = set()
        self.sources: list = [None] * n
        self.source_gids: list[str | None] = [None] * n
        self.default_binding: list[str] = ["custom"] * n
        self.binding_override: dict[int, str] = {}
        for j, t in enumerate(self.texts):
            t._mm_legend_entry = (leg, j)  # noqa: SLF001

    def snapshot(self, h):
        """一份示意线的独立副本（按 matplotlib 自己的 handler 派生，再把
        markerscale 多乘的那一次放回）。造不出来就只能用原对象。"""
        try:
            c = legend_fresh_handle(self.leg, h)
        except Exception:  # noqa: BLE001
            c = None
        if c is None or type(c) is not type(h):
            # handler 造出来的不是同一种东西（误差棒示意线 LineCollection →
            # Line2D）：那就只能用原对象本身当快照。代价：这一项的 handle_*
            # override 会直接改到它——没有源的误差棒示意线撤销到底后样式回不
            # 到原样（有源的项走源派生，不受影响）
            return h
        if isinstance(c, Line2D) and isinstance(h, Line2D):
            c.set_markersize(h.get_markersize())
        return c

    def has_style_override(self, j: int) -> bool:
        gid = self.gid_of(j)
        return any((gid, p) in self.state.applied for p in LEGEND_ENTRY_STYLE_PROPS)

    def is_frozen(self, j: int) -> bool:
        """这一项是脚本自定义 handler 画的、没有源的整格示意：重建时原样复刻，
        不从单个示意线派生，也不摆 handle_* 控件（一格里没有「那一条」线的样式可改）。
        有源的项不走这里——它们从源派生，照旧（误差棒就是这样）。"""
        return self.frozen[j] is not None and self.sources[j] is None

    def base_of(self, j: int):
        """重建 / 同步时这一项该从谁派生：跟随的从源，定格的原样复刻，其余从脚本原样快照。"""
        if self.effective_binding(j) == "follow_source":
            return self.sources[j]
        if self.is_frozen(j):
            return self.frozen[j]
        return self.pristine[j]

    # ---- 视图 ----
    def shown(self) -> list[int]:
        """当前显示的原始序号，按显示顺序。"""
        return [j for j in self.order if j not in self.hidden]

    def display_index(self, j: int) -> int | None:
        shown = self.shown()
        return shown.index(j) if j in shown else None

    def handle_of(self, j: int):
        """条目 j 此刻的示意线 artist；隐藏中的项回它的脚本原样快照。"""
        k = self.display_index(j)
        if k is None:
            return self.pristine[j]
        handles = [h for h in self.leg.legend_handles if h is not None]
        return handles[k] if k < len(handles) else self.pristine[j]

    def gid_of(self, j: int) -> str:
        return f"{self.leg.get_gid() or ''}.texts_{j}"

    def effective_binding(self, j: int) -> str | None:
        """None = 没有源；否则 follow_source / custom（见 LEGEND_BINDINGS）。"""
        if self.sources[j] is None:
            return None
        if self.has_style_override(j):
            return "custom"
        return self.binding_override.get(j) or self.default_binding[j]


def legend_entries(leg: Legend) -> LegendEntries | None:
    return getattr(leg, "_mm_entries", None)


def _entry_of(t: Text) -> tuple[LegendEntries, int]:
    leg, j = t._mm_legend_entry  # noqa: SLF001
    model = legend_entries(leg)
    if model is None:
        raise ValueError("图例项没有条目模型（instrument 没跑过）")
    return model, j


def _legend_handle_box_geometry(leg: Legend) -> tuple[float, float, float]:
    """(width, height, descent)：与 `Legend._init_legend_box` 同一套 heuristic。"""
    fontsize = leg._fontsize  # noqa: SLF001
    descent = 0.35 * fontsize * (leg.handleheight - 0.7)
    height = fontsize * leg.handleheight - descent
    return leg.handlelength * fontsize, height, descent


#: `Artist.get_figure()` 认不认 `root=` 关键字。**matplotlib 3.10 才加的**：
#: 3.10 起它默认回**根** figure，要拿 artist 自己所在的子图必须显式
#: `root=False`；3.8 / 3.9 没有这个形参，传了当场 `TypeError`。
#:
#: 直接写 `root=False` 的那一版在 pyproject 宣称的下界（`matplotlib>=3.8`）上
#: 把整个「图例项 ↔ 图中源对象」绑定**整片打掉**：`bind_legend_entries` 的
#: `except Exception: h = None` 把这个 TypeError 当成「这个候选造不出示意线」
#: 咽了，于是每一项的指纹都是 None、每一项都绑不上源。没有任何报错——只有
#: 撤销之后示意线换了个类（`LineCollection` → `Line2D`）这一个远端症状。
_GET_FIGURE_TAKES_ROOT = "root" in inspect.signature(Artist.get_figure).parameters


def _owning_figure(art: Artist):
    """artist 自己所在的那张（子）figure，跨 matplotlib 版本同义。"""
    return art.get_figure(root=False) if _GET_FIGURE_TAKES_ROOT else art.get_figure()


def legend_fresh_handle(leg: Legend, orig, box=None):
    """按 matplotlib 自己的 handler 从 `orig` 造一份图例示意线。

    这与 `Legend._init_legend_box` 每一项做的事逐字相同（同一个 handler、同样
    的 fontsize 与 handlebox 几何），所以「从源重新派生」得到的正是
    `ax.legend()` 此刻会画出来的那条。`box` 给了就画进它（替换式同步），
    不给就画进一个一次性的 DrawingArea（只为了拿指纹）。
    """
    from matplotlib.offsetbox import DrawingArea  # 只在这里用，别污染模块层

    handler = leg.get_legend_handler(leg.get_legend_handler_map(), orig)
    if handler is None:
        return None
    width, height, descent = _legend_handle_box_geometry(leg)
    if box is None:
        box = DrawingArea(width=width, height=height, xdescent=0.0, ydescent=descent)
        box.set_figure(_owning_figure(leg))
    return handler.legend_artist(leg, orig, leg._fontsize, box)  # noqa: SLF001


def _rgba(c):
    try:
        return tuple(round(float(x), 4) for x in mcolors.to_rgba(c))
    except (ValueError, TypeError):
        return str(c)


def _first(seq, default=None):
    try:
        return seq[0] if len(seq) else default
    except TypeError:
        return default


def _dash_key(h: Line2D):
    """虚线的**节奏**（未按线宽缩放的 offset + on/off 序列）。`get_linestyle()` 对任何自定义
    虚线元组都只回 `'--'`：脚本特意给图例配的短虚线 `(0, (3, 2))` 与图中那条 `(0, (5, 3))`
    指纹相同，被当成「跟随源」，第一次 apply 就换回了源的长虚线。"""
    pattern = getattr(h, "_unscaled_dash_pattern", None)
    if not pattern:
        return None
    offset, seq = pattern
    return (
        round(float(offset or 0.0), 3),
        None if seq is None else tuple(round(float(x), 3) for x in seq),
    )


def legend_handle_fingerprint(h) -> tuple:
    """示意线的**样式指纹**：两份指纹相等 = 画出来一模一样。

    按 artist 类型取各自会被 `update_from` 复制的那几条；类型名进指纹，
    Line2D 与 Rectangle 永远不相等。
    """
    kind = type(h).__name__
    if isinstance(h, Line2D):
        return (
            kind,
            _rgba(h.get_color()),
            str(h.get_linestyle()),
            _dash_key(h),
            round(float(h.get_linewidth()), 3),
            str(h.get_marker()),
            round(float(h.get_markersize()), 3),
            _rgba(h.get_markerfacecolor()),
            _rgba(h.get_markeredgecolor()),
            round(float(h.get_markeredgewidth()), 3),
            h.get_alpha(),
        )
    if isinstance(h, Patch):
        return (
            kind,
            _rgba(h.get_facecolor()),
            _rgba(h.get_edgecolor()),
            round(float(h.get_linewidth()), 3),
            str(h.get_linestyle()),
            h.get_hatch(),
            h.get_alpha(),
            bool(h.get_fill()),
        )
    if isinstance(h, Collection):
        fc = _first(h.get_facecolor())
        ec = _first(h.get_edgecolor())
        return (
            kind,
            None if fc is None else _rgba(fc),
            None if ec is None else _rgba(ec),
            round(float(_first(h.get_linewidth(), 0.0) or 0.0), 3),
            h.get_hatch(),
            h.get_alpha(),
        )
    return (kind, id(h))


def _entry_boxes(leg: Legend) -> list[tuple]:
    """按显示顺序给出每一项的 (handlebox, textbox)。

    `_legend_handle_box` 是 HPacker(列) → VPacker(项) → HPacker([示意线, 文字])。
    示意线在前还是文字在前（`markerfirst`）matplotlib 没存下来，按类型认。
    """
    from matplotlib.offsetbox import DrawingArea, TextArea

    out = []
    for column in leg._legend_handle_box.get_children():  # noqa: SLF001
        for item in column.get_children():
            hb = tb = None
            for child in item.get_children():
                if isinstance(child, DrawingArea):
                    hb = child
                elif isinstance(child, TextArea):
                    tb = child
            if hb is not None and tb is not None:
                out.append((hb, tb))
    return out


class FrozenLegendHandle:
    """脚本自定义 handler 画出来的一整格示意（多个 artist）的**定格副本**。

    matplotlib 不保存 `legend(handles, …)` 收到的原始 handle，也不保存 handler 画了几个
    artist——`legend_handles` 里只有 `legend_artist()` 回的第一个。所以重建（改内边距 /
    列数 / 顺序 / 隐藏……）拿不回脚本的自定义画法，只能原样复刻它当初画出的那一格：
    副本存在这里，`_FrozenHandler` 按新格子的尺寸把它们等比铺进去。
    """

    def __init__(self, artists: list, box) -> None:
        self.artists = [copy.copy(a) for a in artists]
        self.x0, self.y0 = -float(box.xdescent), -float(box.ydescent)
        self.width, self.height = float(box.width), float(box.height)


class _FrozenHandler(HandlerBase):
    """把 `FrozenLegendHandle` 的副本铺进新的 handlebox（旧格 → 新格的仿射）。"""

    def legend_artist(self, legend, orig_handle, fontsize, handlebox):  # noqa: ARG002
        f = orig_handle
        sx = handlebox.width / f.width if f.width else 1.0
        sy = handlebox.height / f.height if f.height else 1.0
        place = (
            Affine2D()
            .translate(-f.x0, -f.y0)
            .scale(sx, sy)
            .translate(-handlebox.xdescent, -handlebox.ydescent)
        )
        out = []
        for a in f.artists:
            c = copy.copy(a)
            c.set_transform(place + handlebox.get_transform())
            handlebox.add_artist(c)
            out.append(c)
        return out[0]


_FROZEN_HANDLER = _FrozenHandler()


def _freeze_entry(leg: Legend, h, box):
    """这一格要不要定格：脚本画出的 artist 比「从 `h` 按 matplotlib 默认 handler 重派生」
    多，就定格（重派生会丢东西）；一格只有一个 artist、或者某个 artist 不是画在这一格
    自己的坐标里（没法按新格子等比铺），就不定格。"""
    from matplotlib.offsetbox import DrawingArea  # 只在这里用，别污染模块层

    kids = list(box.get_children())
    if len(kids) <= 1:
        return None
    probe = DrawingArea(
        width=box.width, height=box.height, xdescent=box.xdescent, ydescent=box.ydescent
    )
    probe.set_figure(_owning_figure(leg))
    try:
        legend_fresh_handle(leg, h, probe)
    except Exception:  # noqa: BLE001 — 派生不出来就当它只能造出 0 个
        pass
    if len(probe.get_children()) >= len(kids):
        return None
    base = box.get_transform()
    if not all(Artist.get_transform(a) == base for a in kids):
        return None
    return FrozenLegendHandle(kids, box)


def _legend_replace_handle(leg: Legend, k: int, orig, copy_of=None) -> bool:
    """把显示位 k 的示意线换成从 `orig` 现派生的那份。

    只动 handlebox 里的子 artist 与 `legend_handles[k]`，布局盒、文字、
    定位回调一概不碰——所以它不改包围盒，只改示意线本身的样子。
    `copy_of` 给了表示 `orig` 本身已经是一份示意线（快照），派生会把
    markerscale 再乘一次，事后把 markersize 放回。
    """
    boxes = _entry_boxes(leg)
    if k >= len(boxes):
        return False
    box = boxes[k][0]
    old = list(box.get_children())
    box._children.clear()  # noqa: SLF001 — DrawingArea 没有 remove_artist
    fresh = legend_fresh_handle(leg, orig, box)
    if fresh is None:
        box._children.extend(old)  # noqa: SLF001 — handler 造不出来就保留原样
        return False
    if copy_of is not None and isinstance(fresh, Line2D) and isinstance(copy_of, Line2D):
        fresh.set_markersize(copy_of.get_markersize())
    idx = [i for i, h in enumerate(leg.legend_handles) if h is not None]
    if k < len(idx):
        leg.legend_handles[idx[k]] = fresh
    return True


def _all_legends(fig) -> list[Legend]:
    """figure 上全部图例：figure 级的 + 每个 axes 的（含插图 / 次坐标轴——
    `fig.axes` 里没有它们，遍历权威只有 `axestraversal.ordered_axes` 一处）。"""
    out = list(getattr(fig, "legends", []) or [])
    for ax in ordered_axes(fig)[0]:
        leg = ax.get_legend()
        if leg is not None:
            out.append(leg)
    return out


def sync_legends(state: RebuildState) -> None:
    """让每个 `follow_source` 的图例项与它的源对象一致（在 `apply` 尾部跑）。

    派生显示：这一步**不写文档、不进 applied**，只把图中源对象此刻的样子
    重新派生到示意线上。源没变时派生结果与现状逐字节相同，所以无条件跑
    也是幂等的；有 handle_* override 的项（custom）不在这里被碰。
    """
    for leg in _all_legends(state.fig):
        model = legend_entries(leg)
        if model is None:
            continue
        for k, j in enumerate(model.shown()):
            binding = model.effective_binding(j)
            try:
                if binding == "follow_source":
                    _legend_replace_handle(leg, k, model.sources[j])
                elif model.is_frozen(j):
                    # 定格的整格只由重建按原样复刻；这里没有「与原样不一致」可比
                    continue
                elif not model.has_style_override(j):
                    # custom 而没有 override：示意线该是脚本原样的样子（撤掉
                    # binding override 之后也退回脚本原样）。指纹相同就不动——
                    # 重派生不是免费的，也不该每一帧都换对象
                    base = model.pristine[j]
                    cur = model.handle_of(j)
                    if legend_handle_fingerprint(cur) != legend_handle_fingerprint(base):
                        _legend_replace_handle(leg, k, base, copy_of=base)
            except Exception as exc:  # noqa: BLE001 — 同步失败不拖垮渲染
                print(f"[legend] {model.gid_of(j)} 同步示意线失败: {exc}", file=sys.stderr)


def _source_label(art) -> str:
    try:
        return str(art.get_label())
    except Exception:  # noqa: BLE001 — 没有 label 的对象就当空
        return ""


def bind_legend_entries(
    leg: Legend, candidates: list[tuple[str, object]], auto_handles: list
) -> None:
    """给一个图例的每一项找**源对象**（instrument 时跑一次，override 之前）。

    `candidates` 是 (gid, 图中对象) —— 曲线 / 散点 / 填充 / 柱系列容器 / 误差棒
    容器……；`auto_handles` 是 `get_legend_handles_labels()` 此刻会给出的那份
    （不带 handles 参数的 `ax.legend()` 就是按它的顺序建的）。

    判据（每一项独立）：先拿每个候选按 matplotlib 自己的 handler 派生一份示意
    线取指纹，再与图例上现有的示意线比：

      1. 指纹 + label 都相等且唯一 → follow_source；
      2. 只有指纹相等且唯一 → follow_source（脚本把 labels 单独传了）；
      3. 只有 label 相等且唯一（类型一致）→ custom：源找到了，但示意线与源
         不一致——脚本在 `legend()` 之后改过示意线，或改过源。这种项默认
         **不跟随**：跟随等于改掉脚本此刻画出来的东西；
      4. 多个候选并列 → 只在 `auto_handles` 位置对得上时选它，否则不绑
         （**不伪造绑定**：绑错一条比不绑更坏）。
    """
    model = legend_entries(leg)
    if model is None:
        return
    fresh: list[tuple[str, object, tuple | None]] = []
    for gid, art in candidates:
        try:
            h = legend_fresh_handle(leg, art)
        except Exception:  # noqa: BLE001 — handler 造不出来的候选不参与
            h = None
        fresh.append((gid, art, None if h is None else legend_handle_fingerprint(h)))
    auto_ids = [id(a) for a in auto_handles] if len(auto_handles) == model.n else []

    def _positional(pool, j):
        if j < len(auto_ids):
            hit = [c for c in pool if id(c[1]) == auto_ids[j]]
            if len(hit) == 1:
                return hit[0]
        return None

    for j in range(model.n):
        fp = model.orig_fp[j]
        label = model.orig_labels[j]
        by_fp = [c for c in fresh if c[2] == fp]
        by_label = [
            c
            for c in fresh
            if c[2] is not None and c[2][0] == fp[0] and _source_label(c[1]) == label
        ]
        both = [c for c in by_fp if c in by_label]
        pick, binding = None, "custom"
        if len(both) == 1:
            pick, binding = both[0], "follow_source"
        elif len(both) > 1:
            pick, binding = _positional(both, j), "follow_source"
        elif len(by_fp) == 1:
            pick, binding = by_fp[0], "follow_source"
        elif len(by_fp) > 1:
            pick, binding = _positional(by_fp, j), "follow_source"
        elif len(by_label) == 1:
            pick, binding = by_label[0], "custom"
        elif len(by_label) > 1:
            pick, binding = _positional(by_label, j), "custom"
        if pick is None:
            continue
        model.sources[j] = pick[1]
        model.source_gids[j] = pick[0]
        model.default_binding[j] = binding


_LEGEND_LAYOUT_ATTRS = {
    "ncol": "_ncols",
    "borderpad": "borderpad",
    "labelspacing": "labelspacing",
    "handlelength": "handlelength",
    "handletextpad": "handletextpad",
    "columnspacing": "columnspacing",
}


def _copy_text_look(dst: Text, src: Text) -> None:
    """把一段图例文字的**样子**（不含位置 / 变换）搬到新对象上。

    刻意不用 `Text.update_from`：它连 transform 一起抄，而图例文字的
    transform 属于它所在的 TextArea——抄过去整块图例文字会画到别处。
    """
    dst.set_color(src.get_color())
    dst.set_fontproperties(src.get_fontproperties().copy())
    dst.set_alpha(src.get_alpha())
    dst.set_visible(src.get_visible())
    dst.set_path_effects(src.get_path_effects())


def rebuild_legend(leg: Legend, state: RebuildState) -> None:
    """按条目模型重排图例盒（列数 / 间距 / 顺序 / 隐藏都走这一条）。

    素材：跟随的项拿**源对象**重新派生（与 `ax.legend()` 同一条路，误差棒
    仍是误差棒、markerscale 只乘一次）；其余拿它的脚本原样快照——快照
    上 markerscale 已经乘过，重派生会再乘一次，所以事后把 markersize 放回。
    文字对象整批换新：样子从旧对象搬过去，gid / 模型 / override 由
    `_reindex_legend_children` 接上。
    """
    model = legend_entries(leg)
    if model is None:
        return
    shown = model.shown()
    handles = [model.base_of(j) for j in shown]
    labels = [model.texts[j].get_text() for j in shown]
    old_texts = {j: model.texts[j] for j in shown}
    title = leg.get_title()
    title_text = title.get_text()
    title_fp = title.get_fontproperties().copy()
    title_color, title_alpha = title.get_color(), title.get_alpha()
    leg._init_legend_box(handles, labels)  # noqa: SLF001
    # _init_legend_box 会换掉 _legend_box，定位回调必须重挂——
    # 否则图例内容画在默认偏移上（导出里整块消失）
    leg._legend_box.set_offset(leg._findoffset)  # noqa: SLF001
    # 标题：`set_title(prop=None)` 会让新标题退回默认字号，脚本的
    # title_fontsize 就丢了——把旧标题的字体属性一起带过去
    leg.set_title(title_text, prop=title_fp)
    leg.get_title().set_color(title_color)
    leg.get_title().set_alpha(title_alpha)
    new_handles = [h for h in leg.legend_handles if h is not None]
    for k, j in enumerate(shown):
        if model.effective_binding(j) != "follow_source" and k < len(new_handles):
            fresh, copy = new_handles[k], model.pristine[j]
            if isinstance(fresh, Line2D) and isinstance(copy, Line2D):
                fresh.set_markersize(copy.get_markersize())
    for k, t in enumerate(leg.get_texts()):
        if k < len(shown):
            _copy_text_look(t, old_texts[shown[k]])
    _reindex_legend_children(leg, state)


def _legend_rebuild_setter(prop: str):
    """ncol / borderpad / labelspacing / handlelength / handletextpad /
    columnspacing 是构建期参数，改动需要重排整个图例盒。"""
    attr = _LEGEND_LAYOUT_ATTRS[prop]

    def setter(leg: Legend, v, state) -> None:
        setattr(leg, attr, int(v) if prop == "ncol" else float(v))
        rebuild_legend(leg, state)

    setter._needs_state = True  # noqa: SLF001
    return setter


def _legend_entry_order(leg: Legend) -> list[int]:
    model = legend_entries(leg)
    return list(model.order) if model is not None else list(range(len(leg.get_texts())))


def _set_legend_entry_order(leg: Legend, v, state) -> None:
    """图例条目重排：value 是原始序号的排列（缺漏的按原序补尾）。"""
    model = legend_entries(leg)
    if model is None:
        return
    n = model.n
    idx = []
    for i in v or []:
        i = int(i)
        if 0 <= i < n and i not in idx:
            idx.append(i)
    idx += [i for i in range(n) if i not in idx]
    model.order = idx
    rebuild_legend(leg, state)


_set_legend_entry_order._needs_state = True  # noqa: SLF001


def _frame_rounded(leg: Legend) -> bool:
    return isinstance(leg.get_frame().get_boxstyle(), BoxStyle.Round)


def _set_frame_rounded(leg: Legend, v) -> None:
    # 与 `Legend.__init__` 的 fancybox 分支逐字相同
    if bool(v):
        leg.get_frame().set_boxstyle("round", pad=0, rounding_size=0.2)
    else:
        leg.get_frame().set_boxstyle("square", pad=0)


# ---- 条目级 handler（挂在 legend_text 上）----
def _entry_visible_get(t: Text) -> bool:
    model, j = _entry_of(t)
    return j not in model.hidden


def _entry_visible_set(t: Text, v, state) -> None:
    model, j = _entry_of(t)
    if bool(v):
        model.hidden.discard(j)
    else:
        model.hidden.add(j)
    rebuild_legend(model.leg, state)


_entry_visible_set._needs_state = True  # noqa: SLF001


def _entry_binding_get(t: Text):
    model, j = _entry_of(t)
    return model.binding_override.get(j)


def _entry_binding_set(t: Text, v) -> None:
    model, j = _entry_of(t)
    if v is None:
        model.binding_override.pop(j, None)
        return
    if v not in LEGEND_BINDINGS:
        raise ValueError(f"binding 只能是 {LEGEND_BINDINGS}，收到 {v!r}")
    if v == "custom" and model.effective_binding(j) == "follow_source":
        _detach_entry(model, j)
    model.binding_override[j] = str(v)


def _detach_entry(model: LegendEntries, j: int) -> None:
    """一个跟随中的项脱开：图例盒里那份活的示意线换成**脚本原样快照**的副本，
    随后落下的 handle_* override 写在它上面。

    不从源现派生（第一版的做法）：源此刻的样子不在文档里，重放时脱开点落在源的
    所有 override 之后，源随后变过的话热态 ≠ 重放（#414）。示意线 = 脚本原样 +
    文档里的 handle_*，两条路才是一张图；「定格此刻的样子」由前端在断开那一刻
    把五条样式写成 override 来兑现。
    """
    k = model.display_index(j)
    if k is not None:
        _legend_replace_handle(model.leg, k, model.pristine[j], copy_of=model.pristine[j])


def _entry_handle(t: Text):
    model, j = _entry_of(t)
    return model.handle_of(j)


def _handle_read(h, prop: str):
    """示意线的一条样式；类型不认这条 prop 就抛（manifest 不会发它）。"""
    if isinstance(h, Line2D):
        return {
            "handle_color": lambda: h.get_color(),
            "handle_linestyle": lambda: h.get_linestyle(),
            "handle_linewidth": lambda: float(h.get_linewidth()),
            "handle_marker": lambda: h.get_marker(),
            "handle_markersize": lambda: float(h.get_markersize()),
        }[prop]()
    if isinstance(h, Patch):
        return {"handle_color": lambda: h.get_facecolor()}[prop]()
    if isinstance(h, Collection):
        return {
            "handle_color": lambda: (
                h.get_color() if isinstance(h, LineCollection) else h.get_facecolor()
            ),
        }[prop]()
    raise KeyError(prop)


def _handle_write(h, prop: str, v) -> None:
    if isinstance(h, Line2D):
        {
            "handle_color": lambda: h.set_color(v),
            "handle_linestyle": lambda: h.set_linestyle(v),
            "handle_linewidth": lambda: h.set_linewidth(float(v)),
            "handle_marker": lambda: h.set_marker(v),
            "handle_markersize": lambda: h.set_markersize(float(v)),
        }[prop]()
        return
    if isinstance(h, Patch):
        {"handle_color": lambda: h.set_facecolor(v)}[prop]()
        return
    if isinstance(h, Collection):
        {
            "handle_color": lambda: (
                h.set_color(v) if isinstance(h, LineCollection) else h.set_facecolor(v)
            ),
        }[prop]()
        return
    raise KeyError(prop)


def _entry_handle_write(t: Text, prop: str, v) -> None:
    model, j = _entry_of(t)
    if model.effective_binding(j) == "follow_source":
        # 第一条 handle_* override 落下的这一刻它脱开跟随。`applied` 要到
        # setter 返回后才登记，所以这里还看得见「它刚才还在跟随」
        _detach_entry(model, j)
    _handle_write(model.handle_of(j), prop, v)


def _mk_entry_handle_handler(prop: str) -> tuple:
    return (
        lambda t: _handle_read(_entry_handle(t), prop),
        lambda t, v: _entry_handle_write(t, prop, v),
    )


def _reindex_legend_children(leg: Legend, state: RebuildState) -> None:
    """重建之后把新文字对象接回 gid / 条目模型 / state，并重放已应用的 override。

    gid 按**原始序号**：显示位 k 上的新 Text 属于 `model.shown()[k]` 那一项。
    隐藏中的项保留旧 Text（gid 与 override 都还挂在它上面，解除隐藏时重建
    会按它此刻的文字造新对象）。
    """
    leg_gid = leg.get_gid() or ""
    model = legend_entries(leg)
    if not leg_gid or model is None:
        return
    remap = {}
    shown = model.shown()
    for k, t in enumerate(leg.get_texts()):
        if k >= len(shown):
            break
        j = shown[k]
        t._mm_legend_entry = (leg, j)  # noqa: SLF001
        model.texts[j] = t
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
    # 重放这些 gid 上已应用的 override（旧对象被扔掉，效果要落到新对象上）。
    # 状态类 prop（隐藏 / 绑定）落在模型上，重建正是按模型做的，不重放。
    for (gid, prop), value in list(state.applied.items()):
        if gid in remap and prop not in _LEGEND_ENTRY_STATE_PROPS:
            state.reapply(remap[gid], prop, value)


#: `overrides.HANDLERS` 里图例的第一段（显隐 / 边框 / 字号 / 图幅分数位置），按原位置 `**` 展开。
HANDLERS_BASIC: dict[tuple[str, str], tuple] = {
    ("legend", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("legend", "frameon"): (lambda a: a.get_frame_on(), lambda a, v: a.set_frame_on(bool(v))),
    # getter 回**一条一个**的列表（脚本可以把某一条设成别的字号，撤销时要能
    # 逐条还原回去），所以 setter 必须同时吃标量与序列——**restore 走的正是
    # `setter(artist, originals[key])`**，两边形状不一致的话「改了图例字号
    # 之后撤销不回来」，而且只在撤销那一刻才炸（`float() argument must be a
    # string or a real number, not 'list'`）。CompatBench 的 art_legend 就是
    # 这么把它抓出来的。
    ("legend", "fontsize"): (
        lambda a: [t.get_fontsize() for t in a.get_texts()],
        lambda a, v: _set_legend_fontsize(a, v),
    ),
    ("legend", "loc_frac"): (_get_legend_loc, _mk_legend_pos_setter("loc_frac")),
}

#: 第二段：预设位置 / 外侧锚点 / 标题 / 边框样式 / 条目顺序 / 列数与间距（重建型 setter）。
HANDLERS_LAYOUT: dict[tuple[str, str], tuple] = {
    # 三条位置 prop 共用一个模型（见「图例位置模型」一节）：各写自己的槽位，
    # 再整体重建——应用顺序不影响结果。
    ("legend", "loc"): (_get_legend_loc, _mk_legend_pos_setter("loc")),
    # 锚点的 getter 回**当前可读的值**（[x, y] 或 None，界面用）；表达不出来的
    # 形状回 None + 一个 reason code，那时 manifest 根本不发这条字段。
    ("legend", "loc_anchor"): (
        lambda a: legend_anchor_state(a)[0],
        _mk_legend_pos_setter("anchor"),
    ),
    ("legend", "title"): (lambda a: a.get_title().get_text(), lambda a, v: a.set_title(str(v))),
    ("legend", "title_fontsize"): (
        lambda a: float(a.get_title().get_fontsize()),
        lambda a, v: a.get_title().set_fontsize(float(v)),
    ),
    ("legend", "facecolor"): (
        lambda a: a.get_frame().get_facecolor(),
        lambda a, v: a.get_frame().set_facecolor(v),
    ),
    ("legend", "framealpha"): (
        lambda a: a.get_frame().get_alpha(),
        lambda a, v: a.get_frame().set_alpha(None if v is None else float(v)),
    ),
    ("legend", "edgecolor"): (
        lambda a: a.get_frame().get_edgecolor(),
        lambda a, v: a.get_frame().set_edgecolor(v),
    ),
    ("legend", "entry_order"): (_legend_entry_order, _set_legend_entry_order),
    ("legend", "ncol"): (lambda a: int(getattr(a, "_ncols", 1)), _legend_rebuild_setter("ncol")),
    ("legend", "borderpad"): (lambda a: float(a.borderpad), _legend_rebuild_setter("borderpad")),
    ("legend", "labelspacing"): (
        lambda a: float(a.labelspacing),
        _legend_rebuild_setter("labelspacing"),
    ),
    ("legend", "handlelength"): (
        lambda a: float(a.handlelength),
        _legend_rebuild_setter("handlelength"),
    ),
    ("legend", "handletextpad"): (
        lambda a: float(a.handletextpad),
        _legend_rebuild_setter("handletextpad"),
    ),
    ("legend", "columnspacing"): (
        lambda a: float(a.columnspacing),
        _legend_rebuild_setter("columnspacing"),
    ),
    ("legend", "frame_linewidth"): (
        lambda a: float(a.get_frame().get_linewidth()),
        lambda a, v: a.get_frame().set_linewidth(float(v)),
    ),
    ("legend", "frame_rounded"): (_frame_rounded, _set_frame_rounded),
}

#: 撤销：位置模型的三条——槽位退回未表态 + 整体重建（脚本原样存在模型的 orig 里）。
RESTORE: dict[tuple[str, str], object] = {
    ("legend", "loc_frac"): _mk_legend_pos_restore("loc_frac"),
    ("legend", "loc"): _mk_legend_pos_restore("loc"),
    ("legend", "loc_anchor"): _mk_legend_pos_restore("anchor"),
}
