"""Override 应用层（worker 子进程内使用）。

设计：文档每次发来**全量** override 列表（[{gid, prop, value}...]），
worker 维护每张图的 applied / originals 两张表：
  - 新列表缺少上次已应用的 (gid,prop) → 用 originals 恢复原值（支持 undo）
  - 首次修改某 (gid,prop) 时先记录原值
坐标约定：前端发来的位置一律是 figure 分数坐标、y 轴向下（top-origin），
worker 在此转换为各 artist 自己的坐标系。
"""

from __future__ import annotations

import contextlib
import importlib
import re
import sys
import threading
import weakref

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.patheffects as mpatheffects
import matplotlib.ticker as mticker
import numpy as np
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.axes._base import _AxesBase
from matplotlib.collections import Collection, LineCollection, PathCollection, QuadMesh, TriMesh
from matplotlib.figure import Figure
from matplotlib.image import AxesImage
from matplotlib.layout_engine import TightLayoutEngine
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.patches import BoxStyle, FancyArrowPatch, Patch, Rectangle
from matplotlib.text import Text
from matplotlib.ticker import FormatStrFormatter, ScalarFormatter
from mpl_toolkits.mplot3d import proj3d

#: 刻度标签的 gid 形状（`FigState.resolve` 按需现解时用）
_TICKLABEL_GID = re.compile(r"^axes_(\d+)\.([xyz])ticklabels_(\d+)$")


def _sibling(name: str):
    """按**本模块自己的加载位置**解析兄弟模块（唯一用途：`manifest`）。

    `manifest` 在模块层 import 本模块，反过来在模块层 import 会成环，所以
    那两处只能延后到调用时——问题是**延后到什么时候执行、在谁的命名空间里
    执行**，两条入口的答案不一样：

    * safe worker 把 engine 目录插进 `sys.path` 后平铺 import
      （`__name__ == "overrides"`），裸 `import manifest` 命中的是我们自己的；
    * native bridge（ADR 0020）在**用户自己的进程**里跑用户的代码，engine
      目录**必须**在 import 完就从 `sys.path` 收回——否则用户项目里那份
      `manifest.py` / `overrides.py` / `config.py` 会被我们顶掉。此时
      `__name__ == "tavotto_bridge_runtime.overrides"`，而这两处 late import
      是在**用户代码跑起来之后**才执行的：裸 `import manifest` 会去命中
      用户项目里的 `manifest.py`，然后报一个指向完全错误方向的
      AttributeError。

    按 `__name__` 的包前缀解析对两条入口都成立，且平铺那条**一个字节都没变**
    （前缀为空 → 仍然是裸名 `manifest`）。看护：
    `tests/bridge/test_bridge_namespace.py::test_user_module_wins_over_engine_sibling`。
    """
    pkg = __name__.rpartition(".")[0]
    full = f"{pkg}.{name}" if pkg else name
    mod = sys.modules.get(full)
    return mod if mod is not None else importlib.import_module(full)


class FigState:
    """一张常驻内存 Figure 的可变状态。"""

    def __init__(self, fig: Figure):
        self.fig = fig
        self.elements: list[dict] = []  # manifest.instrument 填充（含 artist 引用）
        self.index: dict[str, object] = {}  # gid -> artist（"figure" -> Figure）
        self.applied: dict[tuple, object] = {}  # (gid,prop) -> 请求值
        self.originals: dict[tuple, object] = {}  # (gid,prop) -> 原生值
        # 由**广播型 prop** 代为采下的「脚本原样」（见 ALIAS_GROUPS）。它们
        # 是 originals 里没有对应 applied 条目的那些，单独记一笔才能在广播
        # 撤销之后跟着清掉——否则 originals 里会留下永远没人回收的条目。
        self.alias_seeded: set[tuple] = set()
        self.colorbar_axes: set = set()  # 承载色条的轴（manifest 标记用）
        # 宿主 axes gid -> 拖动它时应当一起走的其他 axes gid（色条轴 / 孪生轴）
        self.axes_follow: dict[str, list[str]] = {}
        # 画在图上、却没进元素表的 artist（manifest.census 填充，诊断用）
        self.unregistered: list[dict] = []
        # 本次 apply 的**全量** patch 表 {(gid, prop): value}，只在 apply() 期间有值。
        # 结构性 setter（色条方向）要按「这一次改完之后」的落位算几何，而不是
        # 按此刻的实况——热会话里 position 可能已经先改过，全量重放里它还没轮到，
        # 只看实况两条路就会算出不同的位置。
        self.pending: dict[tuple, object] = {}

    def index_ids(self) -> set[int]:
        """已登记 artist 的 `id()` 集合（伪元素也在，它们不是真 artist 但不碍事）。"""
        return {id(a) for a in self.index.values()}

    def resolve(self, gid: str):
        """gid → artist / 伪元素。查不到就试着**按需现解**刻度标签。

        刻度标签不是常驻 artist：改 xlim、换 locator、翻转色条方向都会让整组
        重来，`index` 里那一份只是上一次渲染留下的快照。全量重放时
        「先把主刻度改成 0.05 间隔，再改第 22 条刻度的文字」这种组合里，第 22
        条在登记表建立的那一刻还不存在——只查表就会报「元素不存在」，而热
        会话里它明明改得动，两条路当场分岔（而分岔的表现是写回被 409 拦住，
        用户完全看不出为什么）。

        现解不出来（轴没了 / 序号越界 / 那条刻度没有文字）才是真的不存在，
        照旧回 None → warning → 界面上的孤儿 override。
        """
        artist = self.index.get(gid)
        if artist is not None:
            return artist
        m = _TICKLABEL_GID.match(gid)
        if m is None:
            return None
        i, which, j = int(m.group(1)), m.group(2), int(m.group(3))
        # **序号是 `_ordered_axes` 编的**，它在 `len(fig.axes)` 之后继续给子
        # axes 编号。拿 `fig.axes` 去索引，插图的刻度文字 gid 会越界 → 回 None
        # → apply 报「元素不存在」，而**一条 warning 就阻断写回**。
        # 这条只在索引里还没有它时才走到（CLAUDE.md 记的「先改刻度定位、再改
        # 新出现的那条刻度」在全量重放里的情形），但那正是写回那条路。
        # late import：manifest 在模块层 import 本模块，反过来会成环。
        # **不能写成裸 `import manifest`**——native bridge 里那会命中用户项目
        # 自己的 manifest.py，理由见 `_sibling`。
        _ordered_axes = _sibling("manifest")._ordered_axes

        axes = _ordered_axes(self.fig)[0]
        if not 0 <= i < len(axes):
            return None
        ax = axes[i]
        if getattr(ax, f"{which}axis", None) is None:
            return None
        try:
            labels = getattr(ax, f"get_{which}ticklabels")()
        except Exception:  # noqa: BLE001 — 取不到就当它不存在
            return None
        if j >= len(labels) or not labels[j].get_text():
            return None
        return TickLabel(ax, which, j)


class SeriesGroup:
    """一组同质 artist 的伪元素（柱形系列 / 误差棒），属性统一应用、按成员还原。

    kind="bar_series":  artists = [Rectangle...]
    kind="errorbar":    artists = {"line": Line2D|None, "caps": [Line2D], "bars": [LineCollection]}
    kind="stem_series": artists = {"marker": Line2D|None, "stems": [LineCollection]}
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
            return (
                ([line] if line is not None else []) + self.artists["caps"] + self.artists["bars"]
            )
        if self.kind == "stem_series":
            # baseline **不进成员**：它是零线，不是这条系列的一部分，仍以普通
            # 曲线（axes_i.lines_j）的身份单独可编辑
            m = self.artists.get("marker")
            return ([m] if m is not None else []) + self.artists["stems"]
        return list(self.artists)


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


#: 一次 `build_manifest` 之内的 `get_[xyz]ticklabels()` 记忆表。**线程局部**：
#: Figure 归线程所有（`LiveFigureSession._own`），两条线程各建各的 manifest 时
#: 共用一张表就成了跨图串味。
_ticklabel_memo = threading.local()


@contextlib.contextmanager
def ticklabel_memo():
    """在这个作用域里，同一条轴的 `get_[xyz]ticklabels()` 只算一次。

    **前提（失效就不能再用）**：作用域里没有任何东西会改刻度。唯一的开启点是
    `manifest.build_manifest`——它开头先 `fig.canvas.draw()`（locator/formatter
    在那一刻就把这一帧的刻度定死了），之后整趟只读几何、不动 artist、不动
    xlim/ylim、不换 locator。override 的应用发生在 `apply()` 里，在这个作用域
    **之外**（`figsession.do_render` 先 apply 再 render）。

    为什么值得记：matplotlib 每次 `get_[xyz]ticklabels()` 都要跑一趟
    `Axis._update_ticks()`（locator + formatter + 视区取舍），实测
    `Fig1_kinetics` 上单次约 0.4 ms；而 `build_manifest` 对**每个**刻度伪元素
    要问三次（`_fields_for` 的 text 字段、几何分支、缺字形扫描），13 个刻度
    就是 39 次同样的重算——manifest 步骤一半的时间花在这里（issue #220）。
    """
    outer = getattr(_ticklabel_memo, "table", None)
    _ticklabel_memo.table = {}
    try:
        yield
    finally:
        _ticklabel_memo.table = outer


def _ticklabels(ax: Axes, which: str, *, minor: bool = False) -> list:
    """`ax.get_[xyz]ticklabels()`，在 `ticklabel_memo()` 作用域里只算一次。

    键里带 `id(ax)`，值里**连 ax 一起存**：只有 id 的话，作用域内某个 Axes 被
    回收后新对象拿到同一个 id，就会安静地读到别人的刻度。存一份引用既让 id
    不可能被复用，取的时候还能再核一次身份。
    """
    get = getattr(ax, f"get_{which}ticklabels")
    table = getattr(_ticklabel_memo, "table", None)
    if table is None:  # 不在作用域里（restore / 手工调用）：照旧现算
        return list(get(minor=True)) if minor else list(get())
    key = (id(ax), which, minor)
    hit = table.get(key)
    if hit is not None and hit[0] is ax:
        return hit[1]
    labels = list(get(minor=True)) if minor else list(get())
    table[key] = (ax, labels)
    return labels


def drawn_tick_label_entries(ax: Axes, which: str, *, minor: bool = False) -> list[tuple]:
    """**真的画在图上**的刻度标签 → [(它在 `get_[which]ticklabels()` 里的下标, Text)]。

    这是刻度伪元素几何与登记的唯一判据。`get_[xy]ticklabels()` 回的是 locator
    产出的**全部**刻度的标签——matplotlib 在 `_update_ticks` 里给每一条都填了
    文字与位置，却只画视区之内的那些。对数轴上这两者差得最远：LogLocator 按
    整十年铺位（floor(log vmin) 到 ceil(log vmax) 再加余量），数据跨一两个量级
    时**大半标签落在子图外**。不过滤就登记，表现是「Y 刻度文字」的包围盒比
    图还高 1.8 倍、点着一条画着的刻度命中的却是图外的幽灵——用户报的
    「log 之后刻度线与刻度数字不对齐」就是它。线性轴同病只是量轻（AutoLocator
    只在两端各多出一条）。

    取舍全部**跟渲染器走**（`Axis._update_ticks` 是 `Axis.draw` / mplot3d
    `axis3d.Axis.draw` 共同的那一步），不自算第二份视区判据：
      * 整条轴不可见（twinx 的隐形 x 轴）→ 一条都不在图上；
      * 单条 tick 被 tick_params 关掉（`tick.get_visible()`）→ 不画；
      * 位置在视区外（`_update_ticks` 的变换 + 容差取舍）→ 不画。

    下标身份**保持原口径**（labels1+labels2 拼接序，见 TickLabel）：过滤只决定
    「登不登记 / 量不量几何」，第 j 条指的还是同一条——冻结整条轴时
    `_freeze_tick_texts` 按同一个 j 对位。逐位重建对不上、或私有 API 缺席时
    **放弃过滤退回全量**（宁多勿错删；`test_manifest_geometry` 有版本金丝雀）。
    """
    axis = _axis_of(ax, which)
    try:
        raw = _ticklabels(ax, which, minor=minor)
    except (TypeError, AttributeError):  # 该轴不支持 minor 参数
        return []
    entries = list(enumerate(raw))
    if not entries:
        return []
    try:
        if not axis.get_visible() or not ax.get_visible():
            return []
        to_draw = {id(t) for t in axis._update_ticks()}  # noqa: SLF001 — 渲染器自己的取舍
        ticks = axis.get_minor_ticks() if minor else axis.get_major_ticks()
        # `get_ticklabels()` 的口径：label1 可见的在前、label2 可见的接后。
        # 逐位按身份对拍，拼不回同一个列表就说明口径变了——放弃过滤。
        side1 = [t for t in ticks if t.label1.get_visible()]
        side2 = [t for t in ticks if t.label2.get_visible()]
        rebuilt = [t.label1 for t in side1] + [t.label2 for t in side2]
        if len(rebuilt) != len(raw) or any(a is not b for a, b in zip(rebuilt, raw)):
            return entries
        flags = [t.get_visible() and id(t) in to_draw for t in (*side1, *side2)]
        return [e for e, ok in zip(entries, flags) if ok]
    except Exception:  # noqa: BLE001 — matplotlib 内部形状变了：退回全量，别丢刻度
        return entries


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
        """当前这条轴上**画着字**的刻度标签：主刻度 + 开了数字的次刻度。

        次刻度也算进来，是因为它们在用户眼里就是「X 刻度文字」的一部分——
        开了 `minor_format` 之后不算的话，刻度组的包围盒会漏掉下面那一排，
        点它选不中、对齐也对不准。单条编辑仍然只对主刻度开放（冻结整条轴
        是主刻度的机制，见 TickLabel）。「画着」的判据只有
        `drawn_tick_label_entries` 一份——包围盒圈的必须是真的画出来的那排字。
        """
        out = [t for _, t in drawn_tick_label_entries(self.ax, self.which) if t.get_text()]
        out += [
            t for _, t in drawn_tick_label_entries(self.ax, self.which, minor=True) if t.get_text()
        ]
        return out

    def _first(self, getter, default):
        labs = self.labels
        return getter(labs[0]) if labs else default

    def tick_params(self, which: str = "both", **kw) -> None:
        """`which` 默认 "both"——方向 / 颜色 / 字号那一档主次同改；长度与线宽
        **分档写**（主 `length` / `width`，次 `minor_length` / `minor_width`），
        matplotlib 的次刻度默认就比主刻度短（2 pt vs 3.5 pt），which="both"
        会把这层区分抹平。"""
        self.ax.tick_params(axis=self.which, which=which, **kw)


class TickLabel:
    """单个刻度标签的伪元素（按主刻度序号定位）。3D 轴含 "z"。

    **生命周期**（改动前先读）：刻度标签不是常驻 artist，每次 draw 由 Locator +
    Formatter 重新生成。想让「把 0.5 改成 ½」留得住，只有把整条轴冻成
    `set_ticks(locs, labels)`（FixedLocator + FixedFormatter）这一条路。因此：

    * 身份是**序号**（第 j 个主刻度），不是数值。改 xlim / 换 locator 之后
      第 j 个刻度可能已经是另一个数——这是索引身份的固有代价，`manifest`
      每次渲染都按当前刻度重新登记这批伪元素，消失的那个会变成孤儿 override
      （界面里可见、可清理），**不会**静默吞掉。
    * 冻结前先 `apply_tick_model` 回到「模型态」（脚本原样或用户配置的
      locator），再把该轴上**全部**仍在生效的单条文字一起盖上去——否则
      两条编辑会互相顶掉，热会话与全量重放也会分岔。
    * 索引超出当前刻度数时**抛异常**（→ warning → 写回阻断），绝不静默返回：
      静默返回的表现是「改了字，下一帧自己变回去，没有任何提示」。
    """

    def __init__(self, ax: Axes, which: str, index: int):
        self.ax = ax
        self.which = which
        self.index = index

    def set_gid(self, gid) -> None:
        """伪元素不进 SVG；前端命中靠 manifest bbox。"""

    def live(self):
        labels = _ticklabels(self.ax, self.which)
        return labels[self.index] if self.index < len(labels) else None

    def get_text(self) -> str:
        t = self.live()
        return t.get_text() if t is not None else ""

    def set_text(self, value) -> None:
        """只改自己这一条（无 state 时的退化路径，供 restore/手工调用）。"""
        _freeze_tick_texts(self.ax, self.which, {self.index: str(value)})


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


#: matplotlib 的**通用族别名**。`font.<别名>` 是一张候选字体名清单。
_GENERIC_FONT_FAMILIES = ("serif", "sans-serif", "cursive", "fantasy", "monospace")


def _mathtext_font_name(fam: str) -> str | None:
    """把字体族名换成 **mathtext 认得的具体字体名**；换不出来返回 None。

    `mathtext.rm` 这几个 rcParam 走的是 FontconfigPattern 语法，只吃**具体
    字体名**——喂 `"sans-serif"` 会当场抛：

        Key mathtext.rm: sans-serif
                             ^
        ParseException: Expected end of text, found '-' (at char 4)

    而 `sans-serif` 正是我们在检查器里列出来的选项之一。于是用户点一下
    「无衬线」→ `apply` 报「应用失败」→ **一条 warning 就阻断写回**。
    通用族别名要先经 `font.<别名>` 那张候选清单解析成真实字体名，
    matplotlib 自己也是这么做的。
    """
    if fam in _GENERIC_FONT_FAMILIES:
        names = list(mpl.rcParams.get(f"font.{fam}") or [])
        return str(names[0]) if names else None
    return fam


#: 字体回退尾巴：正文那张脸缺字形时，逐字形退到这里。
#:
#: **只有 DejaVu Sans 一个**，理由是三条同时成立：matplotlib 自己就带着它
#: （我们不新增、不捆绑任何字体，见 `00_SHARED_RULES` §10），它在每个平台上
#: 都在（回退结果因此是确定的，不会「这台机器有那台没有」），而且实测它盖住
#: 了科学文本里那一批 base-14 缺的字符（`⁵` `⁻` `₂` `μ` `≤` `Å` …）。
#:
#: **中日韩不在它的覆盖里**，本尾巴治不了中文方框——那要用户选一个装了中文
#: 的字体（选项由 `manifest._family_options()` 按运行时探测给出）。往这里塞
#: 一个平台相关的中文字体会让同一份文档在两台机器上画出不同的字，比一条
#: 说得清楚的问题项更坏。
FONT_FALLBACK_TAIL = ("DejaVu Sans",)


def _family_chain(fam: str) -> list[str]:
    """正文族 + 回退尾巴。已经点了名的不重复加。"""
    return [fam, *(f for f in FONT_FALLBACK_TAIL if f != fam)]


def _set_text_fontfamily(t: Text, v) -> None:
    """改字体连同 mathtext 一起改。set_fontfamily 只影响正文，$…$ 里的上下标
    仍按 mathtext 字体集渲染——同一个文字框里两种字体。把该 artist 的
    math_fontfamily 切到 custom 字体集，再让 rcParams 的 mathtext.* 指向同一
    字体，正文与上下标才一致。rcParams 是进程级：多个文字分别改成**不同**
    字体时 custom 集只能指向最后一次的选择（明示的边界）；未改字体的文字
    不在 custom 集上，不受影响。

    **正文字体按回退链设**（`_family_chain`）：只设一个名字时，那个字体缺的
    字形会画成方框；带上尾巴之后 matplotlib 逐字形退到 DejaVu Sans。用户选的
    那个族仍然是 `get_fontfamily()[0]`，manifest 与预检报的都是它。

    **正文字体优先落地**：mathtext 那一步是「让上下标跟着一起换」的加分项，
    换不成也不该把整条编辑拖失败——失败的表现是 warning，而一条 warning 就
    阻断写回。换不成时 `$…$` 留在默认字体集里，用户看得见（正文变了、公式
    没变），不是静默的。
    """
    fam = str(v[0]) if isinstance(v, (list, tuple)) else str(v)
    # **按回退链设，不按单个名字设**：matplotlib 3.6 起 family 是一条逐字形
    # 回退链，只给一个名字时缺的字形画成 .notdef 方框（实测 Times New Roman
    # 画 `×10⁵` 的 `⁵` `⁻` 是三个一模一样的空心框）。`get_fontfamily()[0]`
    # 仍然是用户选的那个，manifest / 预检报的都是它。
    t.set_fontfamily(_family_chain(fam))
    math_name = _mathtext_font_name(fam)
    if not math_name:
        return
    try:
        mpl.rcParams["mathtext.rm"] = math_name
        mpl.rcParams["mathtext.it"] = f"{math_name}:italic"
        mpl.rcParams["mathtext.bf"] = f"{math_name}:bold"
        mpl.rcParams["mathtext.sf"] = math_name
    except (ValueError, KeyError):
        return  # 正文已经改好；上下标留在默认字体集
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
# 图内独立箭头（FancyArrowPatch）：端点拖动 + 箭头样式
# ---------------------------------------------------------------------------
_ARROWSTYLES = [
    "-",
    "->",
    "-|>",
    "<-",
    "<|-",
    "<->",
    "<|-|>",
    "|-|",
    "]-[",
    "simple",
    "fancy",
    "wedge",
]


def _arrowstyle_name(a) -> str:
    """当前 ArrowStyle 的注册名；识别不出（带参数的自定义写法）报 'custom'，
    前端把它放进选项里，选它 = 保持脚本原样。"""
    from matplotlib.patches import ArrowStyle

    st = a.get_arrowstyle()
    for name, cls in ArrowStyle._style_list.items():  # noqa: SLF001
        if type(st) is cls:
            return name
    return "custom"


def _set_arrowstyle(a, v) -> None:
    if str(v) == "custom":
        return  # 占位项：脚本用了带参数的自定义样式，选它就是不动
    a.set_arrowstyle(str(v))


def _linestyle_name(a) -> str:
    ls = a.get_linestyle()
    named = {"solid": "-", "dashed": "--", "dashdot": "-.", "dotted": ":"}
    if isinstance(ls, str):
        return named.get(ls, ls)
    return "-"  # (offset, seq) 自定义虚线：显示成实线占位，用户改了才覆盖


def _set_arrow_endpoints(a, value) -> None:
    """拖动图内独立箭头。值为 figure 分数（top-origin）的 [ax, ay, bx, by]，
    换算回箭头自己的 transform 坐标后 set_positions——数据坐标里落点跟着
    数据范围走，figure 分数才是「屏幕上挪到哪就是哪」。annotate 的箭头每次
    draw 会被注释机制重定位，manifest 不给它出端点，这里只会收到独立箭头。"""
    fig = a.get_figure()
    da = _frac_to_display(fig, float(value[0]), float(value[1]))
    db = _frac_to_display(fig, float(value[2]), float(value[3]))
    inv = a.get_transform().inverted()
    a.set_positions(tuple(inv.transform(da)), tuple(inv.transform(db)))


def _get_arrow_endpoints(a):
    pts = getattr(a, "_posA_posB", None)
    return None if pts is None else (tuple(pts[0]), tuple(pts[1]))


def _restore_arrow_endpoints(a, orig) -> None:
    if orig is not None:
        a.set_positions(orig[0], orig[1])


# ---------------------------------------------------------------------------
# 文字背景框（Text.set_bbox 的 FancyBboxPatch）与描边（path_effects.withStroke）
# ---------------------------------------------------------------------------
#: 文字背景框的默认值 —— **全仓库唯一一处**。三处消费它，少一处对齐就出问题：
#:
#:   1. `_BBOX_CREATE`：首次改任何背景属性时现建的那个 patch 长什么样；
#:   2. `_bbox_handler` 的 default：还原时写回去的值；
#:   3. `manifest._text_fields`：**还没有框**时检查器显示什么。
#:
#: 三处曾经各写各的，代价是「开一次框再关掉」之后 manifest 的值漂一格
#: （手写的 `#FFFFFF` vs `to_hex` 的 `#ffffff`）——画面一个像素没变，热态却
#: 已经 ≠ 全量重放。颜色一律经 `mcolors.to_hex`，别手写十六进制字面量。
BBOX_DEFAULTS = {
    "bbox_visible": False,
    "bbox_facecolor": mcolors.to_hex("white"),
    "bbox_edgecolor": mcolors.to_hex("black"),
    "bbox_linewidth": 0.0,
    "bbox_alpha": 1.0,
    "bbox_pad": 0.3,
    "bbox_rounded": False,
}

_BBOX_CREATE = dict(
    boxstyle=f"square,pad={BBOX_DEFAULTS['bbox_pad']}",
    facecolor=BBOX_DEFAULTS["bbox_facecolor"],
    edgecolor=BBOX_DEFAULTS["bbox_edgecolor"],
    linewidth=BBOX_DEFAULTS["bbox_linewidth"],
    alpha=BBOX_DEFAULTS["bbox_alpha"],
)


def _bbox_ensure(t: Text):
    """拿到（必要时现建）这个 Text 的背景框 patch。

    现建时在 artist 上留一个记号 `_mm_bbox_created`——**还原要靠它，靠
    `originals` 靠不住**：bbox_* 是六条 prop 写**同一个 patch**，谁先被应用
    谁就把框建出来了，于是后一条 prop 的「脚本原样」是在**框已经存在之后**
    采的（读到 `bool(patch.get_visible())` 而不是「本来没有框」）。这与
    ALIAS_GROUPS 那条「广播端要在动手之前替组员采原样」是同一个坑，只是这里
    的组小到不必上那套机制：记一个「这框是我们建的」就够了。
    """
    patch = t.get_bbox_patch()
    if patch is None:
        t._mm_bbox_created = True  # noqa: SLF001 — 见上
        t.set_bbox(dict(_BBOX_CREATE))
        patch = t.get_bbox_patch()
    return patch


class _NoBbox:
    """哨兵：这个 Text **原本没有背景框**。

    `originals` 只活在 worker 进程里、不落盘也不过 JSON，所以可以用对象身份
    表示「没有」。用一个普通默认值表示不行——那个值与「有一个 patch，而它的
    facecolor 恰好是白色」在数值上完全一样，还原时分不出该不该把框摘掉。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # 诊断里要看得懂
        return "<no bbox>"


_NO_BBOX = _NoBbox()


def _text_has_bbox_left(t: Text, state: "FigState") -> bool:
    """这个 Text 上**还剩**几条 bbox_* override 生效（含正在还原的那一条）。

    `apply` 的还原循环是先调 restore、再把 key 从 `state.applied` 里弹出去，
    所以正在还原的这条此刻仍在表里：只剩它一条（≤1）时才轮到摘框。
    多条一起还原时，前几条数出 >1 不动手，最后一条数出 1 才摘——顺序无关。
    """
    n = 0
    for gid, prop in state.applied:
        if not prop.startswith("bbox_"):
            continue
        try:
            if state.resolve(gid) is t:
                n += 1
        except Exception:  # noqa: BLE001 — 解析不出就不算
            continue
    return n > 1


def _bbox_handler(read, write, default) -> tuple:
    """背景框子属性：setter 按需建 patch（首次改任何背景属性即出现背景框）。

    ## 还原必须能把「本来就没有框」这个状态还回去

    老实现的 getter 在没有 patch 时回 `default`，还原时又把 `default` 写进一个
    **`_bbox_ensure` 现建出来的** patch——而新建的 patch 是可见的。于是：

        文字本来没有背景框 → 用户只改了「背景色」 → 撤销 →
        底色是还原了，**框还在**（`bbox_visible` 从 False 变成 True，
        而且再也回不去）

    这条比看上去严重：它让**热态 ≠ 全量重放**（全新 worker 重放同一组 patch
    时那个 Text 上根本没有 patch），而 manifest 里 `bbox_visible` 真的变了值
    ——写回自检只比几何，看不见。之所以一直没被用例逮到，是因为扫描顺序
    恰好先把 `bbox_visible` 设成 True 建了框，后面每一条都落在「框已存在」
    的分支上——**另一道防线恰好挡住了它**，测试全绿。换个顺序就现形。

    修法：没有 patch 时 getter 回哨兵 `_NO_BBOX`，还原看到哨兵就
    `set_bbox(None)` 把框整个摘掉。**但要先确认这个 Text 上没有别的 bbox_*
    还生效**——那几条 prop 写的是同一个 patch，摘早了会把仍然生效的背景色
    一起摘掉（与 ALIAS_GROUPS 处理的是同一类重叠，只是这里的「组」小到可以
    就地数清楚）。
    """

    def g(t):
        p = t.get_bbox_patch()
        return read(p) if p is not None else _NO_BBOX

    def s(t, v):
        if v is _NO_BBOX:
            return  # 还原路径专用，见下面的 restore
        write(_bbox_ensure(t), v)

    def r(t, orig, state):
        if _text_has_bbox_left(t, state):
            # 同一个 patch 上还有别的 bbox_* 生效，框得留着——但**这一条**必须
            # 写回去。少了这一句，「只撤掉背景色」会把颜色留在 patch 上，而
            # 调用方看到的是「撤了却没变」。原样采晚了（`_NO_BBOX`）就写默认。
            write(_bbox_ensure(t), default if orig is _NO_BBOX else orig)
            return
        if getattr(t, "_mm_bbox_created", False):
            # 这一族的最后一条也撤了，而这个框**是我们建的** → 整个摘掉。
            # 判据用记号而不是 `orig is _NO_BBOX`：后者会因为采样时机而失真
            # （见 `_bbox_ensure`）。
            t.set_bbox(None)
            t._mm_bbox_created = False  # noqa: SLF001
            return
        if orig is not _NO_BBOX:
            write(_bbox_ensure(t), orig)

    r._needs_state = True  # noqa: SLF001
    return (g, s), r


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
    """行距的**显示**值：命名值（'normal'）按传统默认 1.2 计。

    只给 manifest 用。**还原不能用它**——见 `_get_text_linespacing`。
    """
    v = getattr(t, "_linespacing", 1.2)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 1.2


class _Autoscale:
    """哨兵：这条轴的「脚本原样」是**自动缩放**，不是某一对具体的上下限。

    只活在 `state.originals` 里（worker 进程内），永远不进 patch、不过 JSON、
    不到 patchspec —— 与 `_NO_BBOX` 同一条纪律。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<autoscale>"


_AUTOSCALE = _Autoscale()

#: 「没有值」——不能用 None，None 本身可以是一个合法的原样。
_NOTHING = object()


#: 脚本原样的轴方向（x, y），instrument 时采一次——**那一刻才是脚本原样**。
#:
#: 为什么不能从 originals 里的 lim 反推：`ax.invert_yaxis()` **不关自动缩放**
#: （三个 matplotlib 版本实测一致），于是 getter 回 `_AUTOSCALE`，而这个哨兵
#: 只记了「范围是自动的」那一半——方向是与它正交的另一半事实，端点序里一点
#: 信息都没有。这与 §3.5 那一类是同一个形状：原样是个**模式**，而这个模式
#: 又不止一个维度。
_ORIG_DIR = "_mm_orig_inverted"


def remember_axis_directions(ax) -> None:
    """记下脚本原样的两条轴方向。instrument 调用，重复调用不覆盖。"""
    if not hasattr(ax, _ORIG_DIR):
        setattr(ax, _ORIG_DIR, (bool(ax.xaxis_inverted()), bool(ax.yaxis_inverted())))


def _orig_inverted(ax, axis: str) -> bool:
    d = getattr(ax, _ORIG_DIR, None)
    if d is None:  # 没经过 instrument（手工构造）→ 退回实况
        return bool(getattr(ax, f"{axis}axis_inverted")())
    return bool(d[0 if axis == "x" else 1])


def _pending_inverted(ax, axis: str, state):
    """这一次的 patch 表里对这条轴的方向**有没有明确表态**。没表态回 `None`。

    「明确表态」与「没表态」必须分得开，不能压成一个布尔：

      * 没表态（`None`）→ 端点顺序自己说话，只是要保住**脚本原样**的方向
        （升序输入 + 脚本本来就翻转 → 仍然翻转）；
      * 明确表态 → **两个方向都归一化**。`invert_y=False` 配上降序端点时，
        不把端点扶正的话 `set_ylim(60, 2)` 当场又把轴翻回去，manifest 报
        `invert_y=True`，而幸存的 patch 明明写着 False——用户去掉勾、勾自己
        弹回来。

    绝不读轴的实况：实况带着上一次 patch 的残留，而全量重放是从脚本原样起步的。
    """
    if state is None:
        return None
    want = f"invert_{axis}"
    for (gid, prop), val in state.pending.items():
        if prop == want and state.index.get(gid) is ax:
            return bool(val)
    return None


def _get_axes_lim(axis: str):
    """坐标范围的**可回灌**表示。脚本没设过范围时回 `_AUTOSCALE` 哨兵。

    这是「真正的原样是一个**模式**，不是一个值」的第三个入口（前两个是
    `_NO_BBOX` 与 marker 颜色的 `'auto'`），而这一个的后果最重：**它会让写回
    被拦下来**。

    `ax.set_ylim(...)` 有个副作用——把 `autoscaley_on` 关掉。撤销时我们把
    `get_ylim()` 当原样回灌，数字是对了，**自动缩放却再也回不来**。于是后面
    任何一个会触发重新缩放的 prop（`set_yscale("log")` 就是）在热会话里不再
    缩放，而全新 worker 重放同一串 patch 时会缩放：

        热态   ylim → 撤销 → yscale=log   幸存 patch 列表 = [yscale=log]
        重放   全新 worker，[yscale=log]
        实测   ylim 热态 [2.0, 104.95] vs 重放 [0.794, 125.893]，像素不同

    **`HOT(P) == REPLAY(P)` 破了**，而幸存的那串 patch 与「只设过 yscale」
    的那串**逐字节相同**。这条与本轮那四条颜色缺陷不同：写回自检**看得见**
    它（几何真的变了，实测 8 处分歧），所以它不会静默写坏文件——它的代价是
    把一次完全正当的编辑序列**拦下来**，而用户屏幕上那张图确实不等于
    「脚本 + 这串 patch」应有的样子。

    判据 `get_autoscale[xy]_on()` 是运行时实况，不是类名。manifest 那边照旧
    读 `ax.get_[xy]lim()` 报具体数字（`_axes_fields`），检查器里仍然是两个能
    改的数——显示与回灌本来就是两个口径。
    """

    def get(ax):
        on = getattr(ax, f"get_autoscale{axis}_on", None)
        if on is not None and on():
            return _AUTOSCALE
        return ax.get_xlim() if axis == "x" else ax.get_ylim()

    return get


def _set_axes_lim(axis: str):
    """坐标范围：吃一对数字（用户改的）或 `_AUTOSCALE`（还原时喂回来的）。

    ## 范围与方向是两条**正交**的 prop，只是共用了 `set_[xy]lim` 这一个入口

    matplotlib 用**端点顺序**表达翻转：`set_ylim(2, 60)` 升序 = 不翻转，
    而这会把之前 `invert_yaxis()` 的效果**当场抹掉**。于是「同时设了范围和
    翻转」这个组合坏在一处谁都想不到的地方：

        只 invert_y      ylim [104.95, -3.95]   invert_y True
        ylim + invert_y  ylim [2.0, 60.0]       invert_y **False**   ← 被吃掉

    用户勾了「翻转 Y 轴」、又把范围设成 2..60，界面显示没翻、画面也没翻，
    而 patch 列表里 `invert_y=true` 明明还在。**两个列表序都坏**——
    `_alias_same_element` 把 `ylim` 声明成 `invert_y` 的窄端，`_rank` 于是
    保证 invert 先、lim 后，把「可能被抹掉」变成了「必然被抹掉」。

    所以 lim 只管**范围大小**，方向交给 `invert_*`：写之前先问这一次的 patch
    表（`state.pending`）里有没有对这条轴的 `invert_<axis>`，有就按它排端点；
    没有就沿用轴当前的方向（脚本原样，或已经应用过的 override）。
    看 `pending` 而不是只看轴的实况，是因为**这一次改完之后**才是要落的状态
    ——与色条方向那条结构性 setter 是同一条纪律（见 `FigState.pending`）。

    用户直接把范围写成降序（`[60, 2]`）仍然表达翻转：那时 `pending` 里没有
    `invert_*`，端点顺序照旧说了算。
    """

    def put(ax, v, state=None):
        if v is _AUTOSCALE:
            ax.autoscale(enable=True, axis=axis)
            ax.autoscale_view()
            return
        lo, hi = float(v[0]), float(v[1])
        # **方向由「这一次该是什么方向」说了算，端点顺序只表达「范围是这两个数」。**
        #
        # 判据是 `_requested_inverted`：这一次的 patch 表里有 `invert_<axis>`
        # 就按它，没有就按**脚本原样**——**绝不读轴的实况**。
        #
        # 曾经这里读的就是实况（`ax.<axis>axis_inverted()`），理由写的是
        # 「不依赖 invert 与 lim 谁先应用，两条路殊途同归」。那句话对，但它
        # 只覆盖了「同一次 apply 里两条 patch 的先后」，漏掉了**跨两次 apply**：
        # 热会话里轴还带着上一次 patch 留下的翻转，而全量重放是从脚本原样起步的。
        # 把降序改成升序、这一次又没有 `invert_*` 时，热态停在 `(10, 0)`、
        # 重放是 `(0, 10)`——写回自检会当 divergence 拦下来，用户看到的是
        # 「改了没反应」。当初那版查 `pending` 的实现被删掉，正是因为夹具里
        # 只有一次 apply，抽掉它一条用例都不红。**空门禁的另一种长法：不是
        # 用例没写，是场景少了一维。**
        #
        # 用户直接把范围写成降序（`[60, 2]`）仍然表达翻转：那时 `lo < hi` 为
        # 假，这里不动手，端点顺序自己说话。
        inv = _pending_inverted(ax, axis, state)
        if inv is None:
            # 没表态：端点顺序说了算，只把**脚本原样**的翻转保住。
            # 用户直接写降序（`[60, 2]`）仍然表达翻转——那时 `lo < hi` 为假。
            if lo < hi and _orig_inverted(ax, axis):
                lo, hi = hi, lo
        elif inv:
            if lo < hi:
                lo, hi = hi, lo
        elif lo > hi:
            # **明确要求不翻转**：降序端点也要扶正，否则 set_[xy]lim 当场
            # 又把轴翻回去，而幸存的 patch 写着 False。
            lo, hi = hi, lo
        (ax.set_xlim if axis == "x" else ax.set_ylim)(lo, hi)

    put._needs_state = True  # noqa: SLF001
    return put


def _get_marker_color(attr: str, getter_name: str):
    """marker 颜色的**可回灌**表示：`_marker*color` 原样（多半是 `'auto'`）。

    与 `_get_linecoll_ls` / `_get_coll_edgecolor` / `_get_text_linespacing`
    是同一个坑的第五、六个入口，而这一次坏的东西多一样：**联动关系**。

    Line2D 的 `_markerfacecolor` / `_markeredgecolor` 默认是字符串
    `'auto'`，`get_marker*color()` 会把它**解析成当前的 `color`**。于是

        原始：color=#1f77b4, _markerfacecolor='auto'（marker 跟着线走）
        改 color=#ff0000 → 再改 markerfacecolor  →  撤销

    时，`markerfacecolor` 的「脚本原样」是在 color 已经改过之后采的，采到的是
    **解析值 `#ff0000`**。撤销之后 marker 永久停在红色（实测 386 px 与原样
    不同，而这次编辑本身只动了 1008 px）。`marker='x'` 这类不填充的 marker 在
    `markeredgecolor` 上同样成立（189 px）。

    **修法不是把它加进 ALIAS_GROUPS**。试过：那样像素能还原，但还原写进去的是
    解析后的具体颜色，`'auto'` 这个**模式**丢了——之后用户再单独改线的颜色，
    marker 不再跟着走。与 `_NO_BBOX` 那条是同一个道理：**真正的原样是一个模式，
    不是一个值**，而模式只有原样回灌才留得住。

    manifest 那边照旧显示 `to_hex(get_marker*color())`（解析后的具体色），
    检查器里仍然是一个能点的色块——显示与回灌本来就该是两个口径。
    """

    def get(a):
        raw = getattr(a, attr, None)
        return raw if raw is not None else getattr(a, getter_name)()

    return get


def _get_text_linespacing(t):
    """行距的**可回灌**表示：`_linespacing` 原样（可能是字符串 `'normal'`）。

    与 `_get_linecoll_ls`、`_get_coll_edgecolor` 是同一个坑的第四个入口——
    **getter 回的形状 ≠ setter 吃的形状**，而这一次的代价是几何漂移。

    matplotlib **3.11 起** Text 的默认 `_linespacing` 是字符串 `'normal'`，
    而 `'normal'` 与数值 `1.2` **不是同一个排版**（实测 3.11.1，一个标题的
    `get_window_extent().y0`）：

        默认（'normal'）      268.3294
        set_linespacing(1.2)  268.8333   ← 差 0.5px，而且回不去
        set_linespacing('normal') 268.3294  ← 只有原样回灌才回得去

    把它读成 1.2 再回灌，撤销之后整块文字挪半个像素。**这不只是难看**：
    多行文字所在的图例整块跟着重排，实测 legend 与三条图例项的 bbox 一起
    偏移最多 0.73% figure 分数——而写回自检 `_compare_manifests` 的容差是
    0.5%，也就是说它足以在 3.11 上把一次正常的写回**误判成 replay 分歧**
    而阻断。3.10 / 3.8 上默认是数值，不受影响，所以这条只在 CI 钉着的那个
    版本上现形（本地跑 3.10 全绿）。

    `set_linespacing` 在 3.11 上认 `'normal'`，在 3.10 / 3.8 上不认——但那两版
    的原样本来就是数值，回灌的是数值，碰不到这一支。
    """
    return getattr(t, "_linespacing", 1.2)


def _set_text_linespacing(t: Text, v) -> None:
    """行距：吃数值（用户改的）或命名值（还原时喂回来的 `'normal'`）。

    两种形状都要认——`originals` 里存的正是 `_get_text_linespacing` 回的那份。
    """
    if isinstance(v, str):
        t.set_linespacing(v)
        return
    t.set_linespacing(float(v))


def _stroke_state(t: Text) -> dict:
    """当前描边三元组，缓存在 artist 上；脚本自带 withStroke 时读入其参数。"""
    st = getattr(t, "_mm_stroke", None)
    if st is None:
        st = {"enabled": False, "color": "#FFFFFF", "width": 1.5}
        for eff in t.get_path_effects() or []:
            if isinstance(eff, mpatheffects.withStroke):
                kw = getattr(eff, "_gc", {})
                st = {
                    "enabled": True,
                    "color": to_hex(kw.get("foreground", "#FFFFFF")),
                    "width": float(kw.get("linewidth", 1.5)),
                }
                break
        t._mm_stroke = st  # noqa: SLF001
    return st


def _stroke_set(t: Text, key: str, v) -> None:
    _stroke_state(t)[key] = v
    st = t._mm_stroke  # noqa: SLF001
    rest = [e for e in (t.get_path_effects() or []) if not isinstance(e, mpatheffects.withStroke)]
    if st["enabled"]:
        rest = [
            mpatheffects.withStroke(linewidth=float(st["width"]), foreground=st["color"])
        ] + rest
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


# ---------------------------------------------------------------------------
# 边框（spine）模型：一档「全部」+ 四条可各自覆盖
#
# 与刻度模型同一套路数（写进 cfg 再**整体重建**），原因也一样：「全部边框改成
# 灰色」与「只把上边框改成红色」是两条会互相盖写的 setter，谁先谁后就会得到
# 两张不同的图。改成一次重建之后，两者的应用顺序不影响结果——热会话与全量
# 重放才收敛。
#
# 优先级：某一条自己的设定 > 「全部」的设定 > 脚本原样。
# 「全部」这一档故意作用于 `ax.spines` 的**每一条**（含色条轴的 'outline'）——
# 那是它原本的口径，收窄成四条会让色条的外框突然改不动了。
# ---------------------------------------------------------------------------
_SPINE_SIDES = ("top", "right", "bottom", "left")
_SPINE_CFG_KEYS = (
    "all_color",
    "all_width",
    *(f"{s}_{k}" for s in _SPINE_SIDES for k in ("color", "width")),
)


def spine_cfg(ax: Axes) -> dict:
    """取（必要时新建）一条 axes 的边框模型缓存。`instrument` 在 build 之后
    对每个 2D axes 调一次，保证 `orig` 采的是**脚本原样**。"""
    cfg = getattr(ax, "_mm_spine_cfg", None)
    if cfg is None:
        cfg = {k: None for k in _SPINE_CFG_KEYS}
        cfg["orig"] = {
            name: (sp.get_edgecolor(), float(sp.get_linewidth())) for name, sp in ax.spines.items()
        }
        ax._mm_spine_cfg = cfg  # noqa: SLF001
    return cfg


def apply_spine_model(ax: Axes) -> None:
    """按 cfg **整体重建**每一条边框的颜色与线宽。"""
    cfg = spine_cfg(ax)
    for name, sp in ax.spines.items():
        orig = cfg["orig"].get(name)
        if orig is None:
            continue
        color = cfg.get(f"{name}_color")
        if color is None:
            color = cfg["all_color"]
        width = cfg.get(f"{name}_width")
        if width is None:
            width = cfg["all_width"]
        sp.set_edgecolor(orig[0] if color is None else color)
        sp.set_linewidth(orig[1] if width is None else float(width))
    ax.stale = True


def spine_all_color(ax: Axes):
    cfg = spine_cfg(ax)
    if cfg["all_color"] is not None:
        return cfg["all_color"]
    return _spines_get(ax, lambda s: s.get_edgecolor(), (0, 0, 0, 1))


def spine_all_width(ax: Axes) -> float:
    cfg = spine_cfg(ax)
    if cfg["all_width"] is not None:
        return float(cfg["all_width"])
    return float(_spines_get(ax, lambda s: float(s.get_linewidth()), 0.8))


def spine_side_color(ax: Axes, side: str):
    cfg = spine_cfg(ax)
    if cfg[f"{side}_color"] is not None:
        return cfg[f"{side}_color"]
    sp = ax.spines.get(side)
    return sp.get_edgecolor() if sp is not None else spine_all_color(ax)


def spine_side_width(ax: Axes, side: str) -> float:
    cfg = spine_cfg(ax)
    if cfg[f"{side}_width"] is not None:
        return float(cfg[f"{side}_width"])
    sp = ax.spines.get(side)
    return float(sp.get_linewidth()) if sp is not None else spine_all_width(ax)


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


def _mk_spine_handler(key: str, read):
    def g(ax: Axes):
        return read(ax)

    def s(ax: Axes, v) -> None:
        spine_cfg(ax)[key] = v
        apply_spine_model(ax)

    return (g, s)


def _mk_spine_restore(key: str):
    """撤销一条边框设定 = **退回未表态**（落回「全部」那一档，或脚本原样），
    不是把当前推断出来的值钉死成一条显式配置。"""

    def r(ax: Axes, _orig) -> None:
        spine_cfg(ax)[key] = None
        apply_spine_model(ax)

    return r


def _tick_axis(ts: "TickSet"):
    return getattr(ts.ax, f"{ts.which}axis")


def _tick0(ts: "TickSet"):
    ticks = _tick_axis(ts).get_major_ticks()
    return ticks[0] if ticks else None


def _minor_tick0(ts: "TickSet"):
    ticks = _tick_axis(ts).get_minor_ticks()
    return ticks[0] if ticks else None


def _minor_tick_prop(ts: "TickSet", key: str, rc: str, default: float) -> float:
    """次刻度的长度 / 线宽（三级真值链，与 `_tick_side_state` 同一套路数）：
    有 Tick 对象读它；没有（次刻度没开）读轴的 `_minor_tick_kw`——
    `tick_params(which="minor")` 写的就是它、之后新建的次刻度从它继承；
    kw 里也没有才按 rcParams 落回 matplotlib 会种的那个初值。"""
    t = _minor_tick0(ts)
    if t is not None:
        if key == "size":
            return float(t._size)  # noqa: SLF001
        return float(t.tick1line.get_markeredgewidth())
    kw = _tick_axis(ts)._minor_tick_kw  # noqa: SLF001
    v = kw.get(key)
    if v is not None:
        return float(v)
    try:
        return float(mpl.rcParams[f"{ts.which}tick.minor.{rc}"])
    except KeyError:
        return default


#: (轴, line) → 边名。line 1 = 下/左（tick1line），line 2 = 上/右（tick2line）
_TICK_SIDES = {("x", 1): "bottom", ("x", 2): "top", ("y", 1): "left", ("y", 2): "right"}


def _tick_side_state(axis, which: str, line: int, minor: bool) -> bool:
    """一档（主或次）刻度在某一侧的可见性，三级真值链（issue #96）。

    首选仍是 Tick 对象自己（`tick1line` / `tick2line`）——rcParams 只决定
    初值，脚本自己 `tick_params(top=True)` 之后就不作数了。没有 Tick 对象时
    （`set_xticks([])` 的 NullLocator、没开次刻度的轴）读轴的
    `_major/_minor_tick_kw`：`tick_params` 写的就是它、新建刻度从它继承，
    而且 `Axes.__init__` 会用 rcParams 把两档的 side 键都种进去
    （matplotlib 3.10.8 的 axes/_base.py 实测），所以普通 2D 轴上这一级永远
    有答案——写死「下/左 True」的旧退路会把脚本配过的 `tick_params(top=True)`
    直接无视掉。kw 里也没有（自定义 Axis）才按 matplotlib 种初值的同一条
    公式落回 rcParams，写死默认只剩最后的兜底。
    """
    ticks = axis.get_minor_ticks() if minor else axis.get_major_ticks()
    if ticks:
        return bool(getattr(ticks[0], f"tick{line}line").get_visible())
    kw = axis._minor_tick_kw if minor else axis._major_tick_kw  # noqa: SLF001
    v = kw.get(f"tick{line}On")
    if v is not None:
        return bool(v)
    side = _TICK_SIDES[(which, line)]
    try:
        grp = "minor" if minor else "major"
        return bool(
            mpl.rcParams[f"{which}tick.{side}"] and mpl.rcParams[f"{which}tick.{grp}.{side}"]
        )
    except KeyError:
        return line == 1  # 最后的兜底：matplotlib 默认下/左有、上/右无


def tick_side_visible(ax, which: str, line: int) -> bool:
    """某条轴某一侧**主刻度**的刻度线可见性（line 1 = 下/左，line 2 = 上/右）。

    manifest 的 `_axes_fields` 显示用（issue #92）：界面上是一个开关，显示
    口径取主刻度。handler 的 getter **不再**共用这一份——它要的是 (主, 次)
    二元组那种可还原的原样，见 `_mk_tick_side`（issue #96）。
    """
    axis = getattr(ax, f"{which}axis", None)
    if axis is None:
        return line == 1
    return _tick_side_state(axis, which, line, minor=False)


def _mk_tick_side(which: str, side: str, line: int):
    """axes 的刻度线四边开关：`ticks_top` 落在 x 轴的 tick2、`ticks_left`
    落在 y 轴的 tick1……开关是**边**的语义（与 spine_top 同构），方向
    （in/out）仍在刻度组元素上——两个旋钮写同一状态会互相盖写，不重复。

    getter 回 `(主, 次)` 二元组，不是一个 bool：脚本可以把同一侧配成主开
    次关（`tick_params(which="minor", bottom=False)`），一个 bool 装不下
    这份原样——按主刻度的值用 which="both" 还原，次刻度会被静默盖成一致
    （issue #96）。setter 因此双形态：bool 是界面的开关（which="both" 两档
    一起写），二元组是还原（两档分别写回）——restore 走的正是
    `setter(artist, originals[key])`，与 legend.fontsize 同一条纪律。
    manifest 显示仍是一个 bool（`tick_side_visible`），显示与回灌本来就该
    是两个口径。
    """

    def get(a):
        axis = getattr(a, f"{which}axis", None)
        if axis is None:
            return (line == 1, line == 1)
        return (
            _tick_side_state(axis, which, line, minor=False),
            _tick_side_state(axis, which, line, minor=True),
        )

    def put(a, v):
        if isinstance(v, (tuple, list)):
            major, minor = v
            a.tick_params(axis=which, which="major", **{side: bool(major)})
            a.tick_params(axis=which, which="minor", **{side: bool(minor)})
        else:
            a.tick_params(axis=which, which="both", **{side: bool(v)})

    return get, put


def _set_tick_width(ts: "TickSet", v) -> None:
    ts.tick_params(which="major", width=float(v))
    # mplot3d 的 axis3d.draw 每次都会用 _axinfo 覆盖刻度线宽，
    # 只走 tick_params 会在下一次 draw 被打回去
    info = getattr(_tick_axis(ts), "_axinfo", None)
    if info and "tick" in info:
        lw = info["tick"].get("linewidth")
        if isinstance(lw, dict):
            lw[True] = float(v)  # 只动主刻度
        else:
            info["tick"]["linewidth"] = float(v)


# ---------------------------------------------------------------------------
# 刻度模型：Locator / Formatter（不是「改已经生成出来的 Text」）
#
# 为什么必须走 Locator/Formatter：刻度标签每次 draw 由 locator 现算、Text 对象
# 现建，改 Text 的属性只能靠 tick_params 持久（字号/颜色/朝向那一档），而
# 「几个刻度、落在哪、写成什么」只有 locator 与 formatter 说了算。
#
# 模型存在**轴对象**上（`axis._mm_tick_cfg`），四个字段是「用户表态过什么」，
# 三个 `orig_*` 是「脚本原样」。规则：
#   * 没表态（None）= 用脚本原样，而不是我们另挑一个 AutoLocator——对数轴的
#     LogLocator、脚本自己 set_xticks 冻出来的 FixedLocator，换成 AutoLocator
#     就是把用户的图改了。
#   * setter 一律写进 cfg 再**整体重建**（`apply_tick_model`），不做增量：
#     两条 prop 的应用顺序因此不影响结果，热会话与全量重放才收敛。
#   * `set_[xy]scale` 会把 locator/formatter 整套换成该 scale 的默认值，所以
#     换 scale 之后必须**重新采集** orig（`invalidate_tick_cfg`），否则
#     「自动」会把线性轴的 AutoLocator 按到对数轴上。
# ---------------------------------------------------------------------------
_TICK_MODEL_PROPS = (
    "major_mode",
    "major_step",
    "major_values",
    "minor_visible",
    "minor_mode",
    "minor_step",
    "format",
    "minor_format",
)

_TICK_FORMATS = ["auto", "%.0f", "%.1f", "%.2f", "%.3f", "%g", "sci"]
#: 次刻度的格式多一档 "none"（不标数字）——**那才是默认**，所以它得排在最前。
#: 「auto」在这里同样是「脚本原样」：对数轴的 LogFormatterSciNotation 会挑几条
#: 次刻度标上 10^n，换成 ScalarFormatter 就把它标成一串整数了。
_TICK_MINOR_FORMATS = ["none", *_TICK_FORMATS]


def _axis_of(ax: Axes, which: str):
    return getattr(ax, f"{which}axis")


def tick_cfg(ax: Axes, which: str) -> dict:
    """取（必要时新建）一条轴的刻度模型缓存。`instrument` 会在 build 之后
    对每条轴调用一次，保证 `orig_*` 采的是**脚本原样**而不是改到一半的状态。"""
    axis = _axis_of(ax, which)
    cfg = getattr(axis, "_mm_tick_cfg", None)
    if cfg is None:
        cfg = {k: None for k in _TICK_MODEL_PROPS}
        axis._mm_tick_cfg = cfg  # noqa: SLF001
        invalidate_tick_cfg(ax, which)
    return cfg


def invalidate_tick_cfg(ax: Axes, which: str) -> None:
    """重新采集「脚本原样」的 locator/formatter（换 scale / 重建色条后调用）。"""
    axis = _axis_of(ax, which)
    cfg = getattr(axis, "_mm_tick_cfg", None)
    if cfg is None:
        cfg = {k: None for k in _TICK_MODEL_PROPS}
        axis._mm_tick_cfg = cfg  # noqa: SLF001
    cfg["orig_major_locator"] = axis.get_major_locator()
    cfg["orig_major_formatter"] = axis.get_major_formatter()
    cfg["orig_minor_locator"] = axis.get_minor_locator()
    cfg["orig_minor_formatter"] = axis.get_minor_formatter()


def _minor_auto_locator(axis):
    """「自动次刻度」按当前 scale 选 locator。

    `AutoMinorLocator` 在对数类刻度上直接罢工（matplotlib 会 warn 并给空
    列表），所以 log / symlog / logit 各用它们自己的那一款——列一个点不出
    刻度的选项，等于给了个坏掉的开关。
    """
    name = axis.get_scale()
    if name == "log":
        base = getattr(getattr(axis, "_scale", None), "base", 10)
        return mticker.LogLocator(base=base, subs="auto")
    if name == "symlog":
        return mticker.SymmetricalLogLocator(axis.get_transform(), subs=list(range(1, 10)))
    if name == "logit":
        return mticker.LogitLocator(minor=True)
    return mticker.AutoMinorLocator()


def _major_locs(axis) -> list[float]:
    try:
        return [float(v) for v in axis.get_majorticklocs()]
    except Exception:  # noqa: BLE001 — 取不到就当没有
        return []


def _baseline_major_locs(axis, cfg: dict) -> list[float]:
    """**脚本原样**那份 locator 算出来的主刻度位置。

    刻度模型里凡是「没给具体值就沿用当前刻度」的档位，都必须锚到这一份，
    不能读 `axis.get_majorticklocs()`。后者回答的是「此刻 locator 是什么」，
    而这在两条路径上根本不是同一件事：

    * 热会话里，它可能是上一次 `FixedLocator([5,10,15])` 留下的痕迹；
    * 全量重放里（重开工程、会话空闲被杀后重建、写回自检的一次性 worker），
      同一份 patch 列表落到一张全新的 figure 上，它就是脚本自己的刻度。

    于是同一组 override 画出两张不同的图，而 applied 表里一个字节都没变——
    正是 CLAUDE.md 那条「热态所见 == 全量重放 == 写回 == 重开」要挡的东西。
    锚到 `orig_major_locator` 之后，这一档的取值只跟 patch 列表有关。
    """
    orig = cfg.get("orig_major_locator")
    if orig is None:
        return _major_locs(axis)
    keep = axis.get_major_locator()
    try:
        axis.set_major_locator(orig)  # 绑定到本轴，取值才用对 view interval
        return _major_locs(axis)
    except Exception:  # noqa: BLE001 — 取不到就退回当前值，总好过抛
        return _major_locs(axis)
    finally:
        axis.set_major_locator(keep)


def _step_of(axis, locs: list[float]) -> float:
    if len(locs) >= 2:
        step = abs(locs[1] - locs[0])
        if step > 0:
            return float(step)
    lo, hi = axis.get_view_interval()
    span = abs(float(hi) - float(lo))
    return float(span / 5.0) if span > 0 else 1.0


def _guess_step(axis) -> float:
    """**当前**刻度的间距。给 getter 用——manifest 里的字段是实况回读。"""
    return _step_of(axis, _major_locs(axis))


def _baseline_step(axis, cfg: dict) -> float:
    """脚本原样刻度的间距（切到「固定间隔」时的缺省值，避免视觉上突然跳一下）。

    给 `apply_tick_model` 用，理由同 `_baseline_major_locs`：读实时刻度会让
    「只给了 major_mode、没给 major_step」的那条 patch 在热态与重放里猜出
    两个步长。apply 完之后实况就等于这里算出来的值，getter 照旧回读实况。
    """
    return _step_of(axis, _baseline_major_locs(axis, cfg))


def _formatter_for(name, orig):
    """格式名 → Formatter。`None`/"auto" = 脚本原样，"none" = 不标数字。"""
    name = name or "auto"
    if name == "auto":
        return orig
    if name == "none":
        return mticker.NullFormatter()
    if name == "sci":
        f = ScalarFormatter(useMathText=True)
        f.set_powerlimits((0, 0))
        return f
    return FormatStrFormatter(str(name))


def _formatter_name(fmt, *, allow_none: bool) -> str:
    """Formatter → 格式名（用户没表态时按实际对象反推）。"""
    if allow_none and isinstance(fmt, mticker.NullFormatter):
        return "none"
    if isinstance(fmt, FormatStrFormatter):
        s = getattr(fmt, "fmt", "")
        return s if s in _TICK_FORMATS else "auto"
    if isinstance(fmt, ScalarFormatter) and getattr(fmt, "_powerlimits", None) == (0, 0):
        return "sci"
    return "auto"


def apply_tick_model(ax: Axes, which: str) -> None:
    """按 cfg **整体重建** major/minor locator 与 major formatter。"""
    axis = _axis_of(ax, which)
    cfg = tick_cfg(ax, which)

    mode = cfg["major_mode"] or "auto"
    if mode == "fixed":
        vals = cfg["major_values"]
        # 界面承诺「留空 = 用当前刻度」，而这里的「当前」必须取脚本原样那一份
        vals = [float(v) for v in vals] if vals else _baseline_major_locs(axis, cfg)
        axis.set_major_locator(mticker.FixedLocator(vals))
    elif mode == "step":
        step = float(cfg["major_step"] or 0.0)
        if step <= 0:
            step = _baseline_step(axis, cfg)
        axis.set_major_locator(mticker.MultipleLocator(step))
    else:
        axis.set_major_locator(cfg["orig_major_locator"])

    axis.set_major_formatter(_formatter_for(cfg["format"], cfg["orig_major_formatter"]))

    axis.set_minor_formatter(_formatter_for(cfg["minor_format"], cfg["orig_minor_formatter"]))

    vis, mmode, mstep = cfg["minor_visible"], cfg["minor_mode"], cfg["minor_step"]
    if vis is False:
        axis.set_minor_locator(mticker.NullLocator())
    elif vis is None and mmode is None and mstep is None:
        axis.set_minor_locator(cfg["orig_minor_locator"])  # 没人表态 → 脚本原样
    elif (mmode or "auto") == "step" and mstep and float(mstep) > 0:
        axis.set_minor_locator(mticker.MultipleLocator(float(mstep)))
    else:
        axis.set_minor_locator(_minor_auto_locator(axis))
    ax.stale = True


# ---- manifest 侧的「当前值」读数（用户没表态时按实况推断）----
def tick_major_mode(ax: Axes, which: str) -> str:
    cfg = tick_cfg(ax, which)
    if cfg["major_mode"]:
        return str(cfg["major_mode"])
    loc = _axis_of(ax, which).get_major_locator()
    if isinstance(loc, mticker.FixedLocator):
        return "fixed"
    if isinstance(loc, mticker.MultipleLocator):
        return "step"
    return "auto"


def tick_major_step(ax: Axes, which: str) -> float:
    cfg = tick_cfg(ax, which)
    if cfg["major_step"]:
        return round(float(cfg["major_step"]), 6)
    return round(_guess_step(_axis_of(ax, which)), 6)


def tick_major_values(ax: Axes, which: str) -> list[float]:
    # manifest 的这个字段是**实况回读**（step 模式下回的就是等间隔那组），
    # 不是编辑器里那行输入。apply 之后实况即 apply 算出来的值，两者自洽。
    cfg = tick_cfg(ax, which)
    vals = cfg["major_values"] if cfg["major_values"] else _major_locs(_axis_of(ax, which))
    return [round(float(v), 6) for v in vals]


def tick_minor_visible(ax: Axes, which: str) -> bool:
    cfg = tick_cfg(ax, which)
    if cfg["minor_visible"] is not None:
        return bool(cfg["minor_visible"])
    return not isinstance(_axis_of(ax, which).get_minor_locator(), mticker.NullLocator)


def tick_minor_mode(ax: Axes, which: str) -> str:
    return str(tick_cfg(ax, which)["minor_mode"] or "auto")


def tick_minor_step(ax: Axes, which: str) -> float:
    return round(float(tick_cfg(ax, which)["minor_step"] or 0.0), 6)


def tick_format_name(ax: Axes, which: str) -> str:
    """当前主刻度数值格式。用户表态过就报表态值，否则按实际 formatter 推断。"""
    cfg = tick_cfg(ax, which)
    if cfg["format"]:
        return str(cfg["format"])
    return _formatter_name(_axis_of(ax, which).get_major_formatter(), allow_none=False)


def tick_minor_format(ax: Axes, which: str) -> str:
    """当前**次**刻度数值格式。默认是 "none"（次刻度不标数字）。"""
    cfg = tick_cfg(ax, which)
    if cfg["minor_format"]:
        return str(cfg["minor_format"])
    return _formatter_name(_axis_of(ax, which).get_minor_formatter(), allow_none=True)


def _mk_tick_model_handler(key: str, cast=None):
    """刻度模型 prop 的 (getter, setter)：写进 cfg 再整体重建。"""
    readers = {
        "major_mode": tick_major_mode,
        "major_step": tick_major_step,
        "major_values": tick_major_values,
        "minor_visible": tick_minor_visible,
        "minor_mode": tick_minor_mode,
        "minor_step": tick_minor_step,
        "format": tick_format_name,
        "minor_format": tick_minor_format,
    }

    def g(ts: "TickSet"):
        return readers[key](ts.ax, ts.which)

    def s(ts: "TickSet", v) -> None:
        tick_cfg(ts.ax, ts.which)[key] = None if v is None else cast(v)
        apply_tick_model(ts.ax, ts.which)

    return (g, s)


def _mk_tick_model_restore(key: str):
    """撤销一条刻度模型 prop = **把它退回未表态**（脚本原样），而不是把
    「当前推断出来的值」钉死成一条显式配置——后者会让 undo 之后的图与
    从没改过的图不是同一张。"""

    def r(ts: "TickSet", _orig) -> None:
        tick_cfg(ts.ax, ts.which)[key] = None
        apply_tick_model(ts.ax, ts.which)

    return r


def _num_list(v) -> list[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [float(v)]
    return [float(x) for x in (v or []) if isinstance(x, (int, float)) and not isinstance(x, bool)]


def _freeze_tick_texts(ax: Axes, which: str, edits: dict) -> None:
    """把一条轴冻成 FixedLocator + FixedFormatter，并盖上 `edits` 里的文字。

    `edits` 是 {主刻度序号: 文字}。基础文字取当前 formatter 对当前刻度位置的
    输出（`format_ticks`）——它与 matplotlib 自己 draw 时用的是同一条路，
    所以「只改一条、其余原样」是逐字节原样。
    """
    axis = _axis_of(ax, which)
    locs = _major_locs(axis)
    if not locs:
        raise ValueError(f"{which} 轴当前没有主刻度，无法改刻度文字")
    texts = [str(t) for t in axis.get_major_formatter().format_ticks(locs)]
    for idx, val in edits.items():
        if not (0 <= int(idx) < len(texts)):
            raise ValueError(f"刻度 #{int(idx)} 已不存在（当前只有 {len(texts)} 个主刻度）")
        texts[int(idx)] = str(val)
    # `set_ticks` 有个副作用：把**整组共享轴**的视区扩到 min/max(locs)
    # （`_set_tick_locations` 逐个 `set_view_interval`），而 locs 里常带着
    # 视区外的刻度位（AutoLocator 两端各多一条、LogLocator 多出整套十年）。
    # 冻结改的是**文字**，不该动数据范围——不还原的话视区留在扩过的状态，
    # 撤销之后热会话比全量重放多画两条端头刻度（HOT([]) ≠ REPLAY([])）。
    # 还原按 matplotlib 自己扩的那个范围逐轴还回去（含共享组），
    # `ignore=True` 精确回写、保持反向轴的方向。
    try:
        shared = list(axis._get_shared_axis())  # noqa: SLF001 — 与 _set_tick_locations 同一份名单
    except Exception:  # noqa: BLE001 — 私有 API 缺席时至少还原本轴
        shared = [axis]
    views = [(a, tuple(a.get_view_interval())) for a in shared]
    getattr(ax, f"set_{which}ticks")(locs, texts)
    for a, (lo, hi) in views:
        a.set_view_interval(lo, hi, ignore=True)


def _set_ticklabel_text(tl: "TickLabel", value, state: "FigState") -> None:
    """改单条刻度文字：先回到模型态，再把该轴上**全部**仍在生效的编辑一起冻上。

    只冻自己那一条的话，同轴上的第二条编辑会把第一条顶掉（冻结是整条轴的
    动作）；而不先回模型态的话，反复冻结会把旧刻度位置带进新一轮——改完
    xlim 之后热会话与全量重放就分岔了。
    """
    apply_tick_model(tl.ax, tl.which)
    edits: dict[int, str] = {}
    for (gid, prop), v in (state.applied or {}).items():
        if prop != "text":
            continue
        other = state.resolve(gid)
        if (
            isinstance(other, TickLabel)
            and other.ax is tl.ax
            and other.which == tl.which
            and other.index != tl.index
        ):
            edits[other.index] = str(v)
    edits[tl.index] = str(value)
    _freeze_tick_texts(tl.ax, tl.which, edits)


_set_ticklabel_text._needs_state = True  # noqa: SLF001


def _restore_ticklabel_text(tl: "TickLabel", _orig, state: "FigState") -> None:
    """撤销一条刻度文字 = 让该轴回到模型态。仍在生效的其它编辑由 apply()
    的最后一档（`_ALWAYS_REPLAY`）重新冻上，所以这里不必也不该逐条补。"""
    apply_tick_model(tl.ax, tl.which)


_restore_ticklabel_text._needs_state = True  # noqa: SLF001


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
        super().__init__((0, 0), (0, 0), shrinkA=0, shrinkB=0, clip_on=False, **kw)
        self._mm_axis3d = axis
        self._mm_index = index  # 0/1/2 = x/y/z
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
            (p1[0], p2[0]), (p1[1], p2[1]), (p1[2], p2[2]), self.axes.M
        )
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
            arr = _AxisArrow3D(
                axis,
                i,
                arrowstyle="-|>",
                mutation_scale=float(st["head"]),
                lw=float(st["width"]),
                color=st["color"],
                zorder=60,
            )
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

    def g(a):
        return [get1(ax) for ax in _axes3d_axes(a)]

    def s(a, v):
        [set1(ax, v) for ax in _axes3d_axes(a)]

    def r(a, orig):
        [set1(ax, o) for ax, o in zip(_axes3d_axes(a), orig)]

    return (g, s), r


# ---------------------------------------------------------------------------
# 系列伪元素：统一应用、按成员列表还原
# ---------------------------------------------------------------------------
def _bar_handler(get1, set1):
    """柱形系列：getter 收集每根柱的原值列表，setter 统一应用，restore 逐柱还原。"""

    def g(grp):
        return [get1(r) for r in grp.artists]

    def s(grp, v):
        [set1(r, v) for r in grp.artists]

    def r(grp, orig):
        [set1(rct, o) for rct, o in zip(grp.artists, orig)]

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

    def g(grp):
        return [getter_each(a) for a in pick(grp)]

    def s(grp, v):
        [setter_each(a, v) for a in pick(grp)]

    def r(grp, orig):
        [setter_each(a, o) for a, o in zip(pick(grp), orig)]

    return (g, s), r


def _eb_caps(grp):
    return grp.artists["caps"]


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


def _set_linestyle(a, v) -> None:
    """线型 setter，**两种输入都要吃**。

    用户发来的是名字（"--"），还原路径放回来的却是 matplotlib 自己的 dash
    规格——Collection 的 `get_linestyle()` 回的是 `[(0.0, None)]`，Patch 上
    也可能是 `(offset, seq)`。无脑 `str(v)` 把它 stringify 成
    `"[(np.float64(0.0), None)]"`，`set_linestyle` 当场抛 ValueError：
    **用户按了撤销，线型回不去，而且只在改过线型的元素上发作**。
    """
    a.set_linestyle(v if isinstance(v, (list, tuple)) else str(v))


#: 四种命名线型的**未缩放** dash 规格（`Collection._us_linestyles` 的形状）。
#: 用来把当前线型反查成界面上那个枚举名。数值取自 matplotlib 自己的
#: `_get_dash_pattern`，两个版本（3.8.4 / 3.11.1）实测一致。
_LC_DASHES = {
    (): "-",
    (3.7, 1.6): "--",
    (6.4, 1.6, 1.0, 1.6): "-.",
    (1.0, 1.65): ":",
}


def _linecoll_linestyle_name(coll) -> str:
    """LineCollection 当前线型 → 枚举名；认不出的按实线占位。

    与 `_linestyle_name`（Line2D 那条）同一个产品约定：自定义 dash 显示成
    实线占位，用户改了才覆盖。反查用的是**未缩放**规格——`get_linestyle()`
    回的是按线宽缩放过的那份，同一个 "--" 在 lw=1.5 与 lw=3 下数值不同，
    拿它当键必然认不出来。
    """
    us = getattr(coll, "_us_linestyles", None)  # noqa: SLF001 — 见 _get_linecoll_ls
    if not us:
        return "-"
    _offset, seq = us[0]
    return _LC_DASHES.get(tuple(seq) if seq else (), "-")


def _get_linecoll_ls(coll):
    """线型的**可回灌**表示：未缩放规格 `_us_linestyles`。

    **不能用 `get_linestyle()`**：它回的是按线宽缩放之后的 dash 序列，
    而 `set_linestyle()` 会把喂进去的值再缩放一遍。实测（两个 matplotlib
    版本都一样）：`--` 在 lw=1.5 下是 `[(0.0, [5.55, 2.4])]`，把这个值原样
    回灌得到 `[(0.0, [8.325, 3.6])]`——**撤销之后线型不是原来那条**，而且
    每撤销一次就再放大一次。`_us_linestyles` 是 matplotlib 自己为这件事保留
    的未缩放副本，回灌它得到逐位相同的结果（有实测用例看护）。

    私有属性不在时退回 `get_linestyle()`：那时至少不会崩，代价是自定义
    dash 的还原会有缩放偏差——总比整个属性不可用好。

    ## 这是正确性，不是显示精度——别把它当优化删掉

    `overrides` 是**全量列表**语义：撤销 = 用空列表全量重放，而重放必须落回
    脚本原样。这里回不去的话，**没有任何下游门禁拦得住**——实测过，不是推的：

    * 写回事务的 `app._compare_manifests` **只比几何**（bbox / anchor /
      size_mm，它自己的 docstring 写着）。dash 变了不动任何包围盒：同一张图
      上人为制造这个偏差之后，它比过 19 个元素、报 **0 处分歧**。所以写回
      **不会**回 409 `replay_divergence`，坏状态直接写进用户的原件。
    * 四路等价性矩阵那三条腿用的是同一个比较器，同样看不见。
    * `apply` 也不会报 warning：setter 没抛，它就是成功。

    更难查的是它**同时把界面也带偏**：双重缩放之后的 dash 在
    `_LC_DASHES` 里查不到，`_linecoll_linestyle_name` 退回实线占位——检查器
    显示「实线」，画面上却是一条比原来更疏的虚线。**界面说的和画出来的不是
    一回事**，而这正是这套东西最不能接受的一种失败。

    而且它是复利的：每撤销一次再放大一次，每次 ×1.5
    （实测 5.55 → 8.325 → 12.488 → 18.731）。

    浏览器 playground 走的是**同一份 `overrides.apply`**（`browser.py` 平铺
    import 的就是这个模块），所以 /try 里一模一样地发作。
    """
    us = getattr(coll, "_us_linestyles", None)  # noqa: SLF001
    return list(us) if us else coll.get_linestyle()


def _set_linecoll_ls(coll, v) -> None:
    """线型：吃枚举名（用户改的）或未缩放规格（还原时喂回来的那份）。

    两种形状都要认——`originals` 里存的正是 `_get_linecoll_ls` 回的那份。
    """
    coll.set_linestyle(v)


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


def _cb_target_rect(p: "ColorbarProxy", to: str, state: "FigState"):
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


def _cb_reorient(p: "ColorbarProxy", to: str, state: "FigState") -> None:
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
        invalidate_tick_cfg(cb.ax, which)
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


class PinnedTightLayoutEngine(TightLayoutEngine):
    """持久 tight 布局下把「用户摆过的子图」钉住，其余照旧自动排版（issue #162）。

    ## 为什么要有它

    Tavotto 落 `axes.position` override 的方式是 `ax.set_position(v)`。图上挂着
    **持久的** `TightLayoutEngine` 时（`plt.subplots(layout="tight")` /
    `tight_layout=True`），它会在紧随其后的那次绘制里把位置整个算回去——文档里
    记着 override、画面上什么都没发生。#140 的处理是**不宣称这条能力**（界面
    置灰 + reason），silent wrong 是没了，但这么写图的用户从此拖不动子图、
    不能多选对齐、不能改 mm 宽高、不能成组缩放。这个类是把能力拿回来的那一步。

    ## 三条路各自的实测结论（3.9.4 / 3.10.8 / 3.11.1，三版一致）

    | 做法 | 结论 |
    |---|---|
    | `ax.set_in_layout(False)` | **无效**，挡不住 TightLayoutEngine |
    | 应用前 `fig.set_layout_engine("none")` | 能 work，但同时关掉这张图对**其它**元素的自动排版，副作用面比现状更糟 |
    | 自定义引擎（本类） | 成立，见下面的量法 |

    ## 关键在于「什么时候盖回去」，不只是「盖不盖」

    最直觉的写法——`super().execute(fig)` 之后把 pin 过的位置盖回去——**是错
    的**，而且错得不显眼：它算得出正确的画面，却让「热态所见 == 重开后重放
    出来的」当场破掉。原因在 `matplotlib._tight_layout.get_tight_layout_figure`
    里：它拿 `ss.get_position(fig)`（**gridspec 该给这个格子的位置**）当 ax_bbox，
    却拿 axes **当前**的 tight bbox 当 tight_bbox，两者相减得到边距。被 pin 的
    轴一旦离开自己的格子，这个差就不再是「装饰物探出去多少」，于是每次 draw
    都算出一组新的边距——实测 10 次 draw 都没收敛，而且「先画两次再 pin」与
    「一次性 pin 再画」收敛到**不同**的结果（三版一致）。写回自检只比几何，
    这种分歧正好落在它量得到的那一维上：409，或者更坏——用户所见与写进文件的
    不是一张图。

    所以这里的顺序是：**先把被 pin 的轴放回 gridspec 该给它的格子 → 让 tight
    照常算 → 再盖回 pin**。tight 的输入于是与「一条 override 都没有」时逐位
    相同，实测结果也逐位相同（`others_match_native` 在 10 次 draw 上全 True，
    三版一致）：

    * 被 pin 的轴每一次 draw 都精确落在请求的位置上（与 draw 次数无关）；
    * **没被 pin 的轴一动不动**——「我拖了 A，B 不该跟着跳」；
    * 热态（逐步 pin）与重放（一次性 pin）在同一 draw 序号上逐位相同；
    * pin 表为空时，像素与位置与原生 `TightLayoutEngine` **逐字节相同**——
      所以在 `instrument()` 里无条件换上它不改变任何没被编辑过的图。

    ## 为什么是 TightLayoutEngine 的子类

    `_adjust_compatible` / `_colorbar_gridspec` 直接继承（`fig.colorbar` 靠
    后者决定怎么抠空间，`fig.subplots_adjust` 靠前者决定要不要执行），
    `set()` / `get()` 的 pad/h_pad/w_pad/rect 也照旧。代价是 `isinstance(...,
    TightLayoutEngine)` 对它为真——**判据必须自己排除掉它**，见
    `figure_layout_engine_eats_position`。
    """

    #: 与 `TightLayoutEngine` 相同；写出来是因为它们决定 `fig.colorbar` 与
    #: `fig.subplots_adjust` 的行为，继承来的默认值不该靠读父类才知道。
    _adjust_compatible = True
    _colorbar_gridspec = True

    #: 在 `super().__init__()` 跑完之前 `set()` 就会被调用一次，那时还没有 inner。
    _inner = None

    def __init__(self, inner):
        """`inner` 是被接管的那个引擎**实例本身**，不是它的参数。

        **不用 `PinnedTightLayoutEngine(**inner.get())` 重建**：用户脚本可以挂一个
        自己的 `TightLayoutEngine` 子类（`isinstance` 判据同样选中它），重建会把它
        重写过的 `execute()` 与全部子类状态**静默丢掉**，把每一个没被 pin 的轴的
        落位一起改掉；而子类的 `get()` 多回一个键时，重建会当场 TypeError、这条编辑
        直接失败。包住原件再委派，两种都不会发生（Codex 在 PR #262 上指出）。
        """
        super().__init__()
        self._inner = inner
        # 这两个决定 `fig.colorbar` 怎么抠空间、`fig.subplots_adjust` 要不要执行。
        # **跟着被接管的那个走**，不是照抄 TightLayoutEngine 的类属性——自定义子类
        # 可以改它们。
        self._adjust_compatible = inner.adjust_compatible
        self._colorbar_gridspec = inner.colorbar_gridspec
        #: axes → figure 分数坐标 (x0, y0, w, h)。弱引用：被 pin 的轴
        #: `ax.remove()` 掉之后不该被这张表续命。
        self._pinned: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()

    def set(self, *, pad=None, w_pad=None, h_pad=None, rect=None) -> None:
        """pad / h_pad / w_pad / rect 归被接管的那个引擎管。

        **签名必须与 `TightLayoutEngine.set` 逐字相同**：上游那份的实现是
        `for td in self.set.__kwdefaults__`，而 `self.set` 解析到的是**这里这个
        override**。写成 `**kwargs` 的话 `__kwdefaults__` 是 None，第一次建引擎就
        `TypeError: 'NoneType' object is not iterable`（实测）。
        """
        if self._inner is None:  # super().__init__() 期间：还没有 inner
            super().set(pad=pad, w_pad=w_pad, h_pad=h_pad, rect=rect)
        else:
            self._inner.set(pad=pad, w_pad=w_pad, h_pad=h_pad, rect=rect)

    def get(self) -> dict:
        return self._inner.get() if self._inner is not None else super().get()

    def pin(self, ax, bounds) -> None:
        """这个 axes 的位置由用户说了算，tight 不许再算它。"""
        self._pinned[ax] = tuple(float(v) for v in bounds)

    def unpin(self, ax) -> None:
        """撤销一条 position override：把这个 axes 还给 tight。"""
        self._pinned.pop(ax, None)

    def is_pinned(self, ax) -> bool:
        return ax in self._pinned

    def _live_pins(self) -> list:
        """还挂在图上的 pin。`ax.remove()` 过的轴留在弱引用表里也没用了。"""
        return [(ax, b) for ax, b in self._pinned.items() if ax.get_figure() is not None]

    def execute(self, fig) -> None:
        live = self._live_pins()
        # 1) 放回格子：tight 的输入必须与「没有任何 override」时一样，否则它
        #    算出来的边距会随 pin 的位置漂（见类注释）。没有 SubplotSpec 的轴
        #    （`fig.add_axes`）本来就不参与 tight 计算，跳过。
        for ax, _bounds in live:
            get_ss = getattr(ax, "get_subplotspec", None)
            ss = get_ss() if get_ss is not None else None
            if ss is not None:
                ax.set_position(ss.get_position(fig))
        # 2) 让被接管的那个引擎照常算它自己那份。**调 `inner.execute` 而不是
        #    `super().execute`**：原件可能是用户自己的 `TightLayoutEngine` 子类，
        #    它重写过的排版必须原样跑（见 `__init__`）。
        self._inner.execute(fig)
        # 3) 再把用户摆过的位置盖回去——这一步必须在最后：`subplots_adjust`
        #    会把每一个有 SubplotSpec 的轴按格子重新落位。
        for ax, bounds in live:
            ax.set_position(list(bounds))


def pinnable_layout_engine(fig):
    """这张图上装着的 Tavotto 可钉引擎（没有就 None）。"""
    try:
        engine = fig.get_layout_engine()
    except Exception:
        return None
    return engine if isinstance(engine, PinnedTightLayoutEngine) else None


def ensure_pinnable_layout_engine(fig):
    """持久 tight 引擎 → 换成可钉的那一版。已经换过 / 用不上则原样返回。

    **热态与重放必须在同一时刻做这一步**，否则「所见 == 所写 == 重开后重放出来
    的」当场破掉：换引擎会改变没被 pin 的轴的落位收敛过程，只在一侧做就等于两侧
    跑的是两套布局。做到这一点的办法不是「两边各调一次」，而是**只有一个调用点**
    ——`_set_axes_position`。它是热态与重放**共用的同一条代码路径**：两侧都在
    `overrides.apply()` 里、在同一个规范顺序档位上、在这张图的第一条 position
    override 落下的那一刻走到它。

    曾经在 `manifest.instrument()` 里也调过一次（想让 manifest 建好之前就换掉）。
    变异反证证明那一次是**杀不死的**：把它删掉，整套用例全绿——因为 setter 这条
    路已经覆盖了同一件事。同一条保证实现两遍，坏掉一份另一份会替它兜住，于是两条
    变异一起存活。删掉之后 setter 那条变异当场变红。

    换上去是无条件的：pin 表为空时它与原生 `TightLayoutEngine` 逐字节相同
    （实测），所以没被编辑过的图不受任何影响。
    """
    if fig is None:
        return None
    if not figure_layout_engine_eats_position(fig):
        # 「换不换」只有 `figure_layout_engine_eats_position` 一份判据——这里**不**
        # 再写一次「是不是已经换过了」的早退。写两遍的话，其中一份坏掉时另一份
        # 会替它兜住，变异反证于是两条一起存活（实测：早退在时，把判据里排除自己
        # 子类那半段删掉，整套用例全绿）。
        return pinnable_layout_engine(fig)
    engine = PinnedTightLayoutEngine(fig.get_layout_engine())
    fig.set_layout_engine(engine)
    return engine


def figure_layout_engine_eats_position(fig) -> bool:
    """这张图的布局引擎会不会把 `set_position` 整个算回去？

    问的是「**还没被 Tavotto 接管**的持久 tight 引擎」——它现在只有一个消费者
    `ensure_pinnable_layout_engine`，回答的是「要不要换成可钉的那一版」。
    #140 时代它还兼着「要不要把 position 能力藏起来」，那一层已经由
    `PinnedTightLayoutEngine` 取代（issue #162）。

    三个版本上逐个量过（3.9.4 / 3.10.8 / 3.11.1，结果完全一致）：

    | 建图方式 | layout engine | set_position 之后再 draw |
    |---|---|---|
    | `plt.subplots()` | None | 保住 ✅ |
    | `fig.tight_layout()` 调一次 | PlaceHolderLayoutEngine | 保住 ✅ |
    | `plt.subplots(layout='tight')` | TightLayoutEngine | **被吃掉** ❌ |
    | `plt.subplots(tight_layout=True)` | TightLayoutEngine | **被吃掉** ❌ |
    | `layout='constrained'` / `constrained_layout=True` | ConstrainedLayoutEngine | 保住 ✅ |
    | `layout='compressed'` | ConstrainedLayoutEngine | 保住 ✅ |

    两条实测结论值得记下来：**constrained 不受影响**（曾被与 tight 并列写进
    #137 的剩余风险，那一半是错的）；**最常见的 `fig.tight_layout()` 调用也不
    受影响**——它执行完会把引擎换成 `PlaceHolderLayoutEngine`。中招的只有
    「把 tight 设成常驻引擎」这一种写法。

    还量了一条否定结论：`ax.set_in_layout(False)` **挡不住** TightLayoutEngine
    （三个版本上都不行），所以救不回来的不是它——救回来的是自定义引擎。

    判据用 `isinstance` 而不是类名字符串：`PlaceHolderLayoutEngine` 与
    `ConstrainedLayoutEngine` 都**不是** `TightLayoutEngine` 的子类（实测
    `issubclass` 为 False），所以 isinstance 不会误伤它们。**但
    `PinnedTightLayoutEngine` 是**，所以它必须被显式排除——不排除的话
    `ensure_pinnable_layout_engine` 会在每次调用时再换一次引擎，把上一版的
    pin 表连同用户摆过的每一个位置一起丢掉。
    """
    try:
        engine = fig.get_layout_engine()
        return isinstance(engine, TightLayoutEngine) and not isinstance(
            engine, PinnedTightLayoutEngine
        )
    except Exception:
        return False


def _set_axes_position(a, v) -> None:
    """落 `axes.position`——持久 tight 布局下顺手把这个 axes 钉住。

    这是 `ensure_pinnable_layout_engine` 的**第二个消费点**。`instrument()`
    那边已经在建 manifest 之前把引擎换成可钉的那一版了，但「instrument 换过」
    挡不住两种来路：一份 1.0 之前存下的旧文档，以及一个直接调 API / MCP 的
    调用——它们可能落在一个没走过 instrument 的 FigState 上。共用同一份实现
    是有意的（见 CLAUDE.md「共享判据修一处不算修完」）。

    引擎装不上（不是持久 tight 的图）时 `engine` 为 None，那正是**绝大多数**
    图的情形：位置照旧只靠 `set_position`，没有任何东西会算回去。

    **撤销那条路不走这里**（`_RESTORE` 里另有一条）：还原不只是把数字写回去,
    还要把这个 axes 还给 tight，否则撤销之后它会被钉在「脚本原样」那个数上，
    再也不跟着字号 / 标签变化重排——那是一个不声不响的语义降级。
    """
    bounds = [float(x) for x in v]
    # **顺序是这条函数的不变式**：可能失败的那一步（`set_position` 会对长度不是 4 的
    # bounds 抛 TypeError）必须排在**不可逆**的两步（换引擎、落 pin）之前。
    #
    # 反过来写会烧掉一张图：pin 已经落下而 setter 抛了异常，于是 `apply` 把它收成一条
    # warning、**不记进 `state.applied`**；后续任何一次全量列表里都没有这个 key，
    # 还原那条路（`_RESTORE`）就永远不会跑，也就永远不会 `unpin`。坏 bounds 从此留在
    # 引擎里，而 `Figure.draw` 只吞 `ValueError`——`Bbox.from_bounds()` 抛的是
    # **TypeError**，它会一路冒出去：**这张图再也画不出来，且撤销不回来**
    # （三个版本实测一致）。
    #
    # 这与 #190 那一族是同一句话：不可逆的那一步排在了可能失败的那一步之前。
    # 校验长度只挡得住这一种坏输入，换顺序挡得住 `set_position` 的**每一种**失败。
    a.set_position(bounds)
    engine = ensure_pinnable_layout_engine(getattr(a, "get_figure", lambda: None)())
    if engine is not None:
        engine.pin(a, bounds)


def _restore_axes_position(a, orig) -> None:
    """撤销一条 position override：先把 axes 还给布局引擎，再写回脚本原样。

    `unpin` 不能省。position 的「脚本原样」在持久 tight 图上是一组**算出来的**
    数字，不是用户或脚本表过的态——留着 pin 就等于把那次计算的结果冻成了永久
    设置（见 CLAUDE.md「getter 必须回可回灌的形式」的同一族问题）。unpin 之后
    tight 会在下一次 draw 里重新算它，实测逐位回到「从没被 override 过」的位置。
    """
    engine = pinnable_layout_engine(getattr(a, "get_figure", lambda: None)())
    if engine is not None:
        engine.unpin(a)
    a.set_position([float(x) for x in orig])


def _set_cb_orientation(p: "ColorbarProxy", v, state: "FigState") -> None:
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


def _restore_cb_orientation(p: "ColorbarProxy", orig, state: "FigState") -> None:
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
        invalidate_tick_cfg(ax, which)
    _refresh_axes_follow(state)


_restore_cb_orientation._needs_state = True  # noqa: SLF001


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

    `axes` **要传 `manifest._ordered_axes(fig)[0]`**，别让它退回 `fig.axes`：
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
        return parents[0] if parents else None

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
    # **编号与遍历都必须用 `_ordered_axes`**（由调用方传进来）。用 `fig.axes`
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

    对 (ax, other) 双向各出现一次；按 `ordered`（`_ordered_axes` 的遍历序）
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


def _refresh_axes_follow(state: "FigState") -> None:
    """结构改造之后重算随行关系（色条方向翻转会改变谁和谁挨着）。"""
    try:
        # 与 `instrument` 同一条遍历（插图里的宿主不在 `fig.axes` 里）。
        # 这里靠 late import 拿 `_ordered_axes`：manifest 在模块层 import
        # overrides，反过来在模块层 import 会成环。**不能写成裸 import**，
        # 理由见 `_sibling`。
        _ordered_axes = _sibling("manifest")._ordered_axes

        _ordered = _ordered_axes(state.fig)[0]
        cbar_of_ax, host_of_cbax = colorbar_maps(state.fig, _ordered)
        state.colorbar_axes = set(cbar_of_ax)
        state.axes_follow = follow_map(state.fig, cbar_of_ax, host_of_cbax, _ordered)
    except Exception:  # noqa: BLE001 — 少一条联动不该拦渲染
        pass


# ---------------------------------------------------------------------------
# 坐标轴 scale：选项必须与真实 handler 完全一致
# ---------------------------------------------------------------------------
#: 我们支持的刻度类型（按界面里的排列顺序）。真正出不出某一项，看当前
#: matplotlib 有没有注册它——列一个 `set_[xy]scale` 吃不下的假选项，
#: 用户点了只会得到一次渲染失败。
_SCALE_CHOICES = ("linear", "log", "symlog", "logit")


def scale_options(current: str) -> list[str]:
    import matplotlib.scale as mscale  # noqa: PLC0415 — worker 侧才有科学栈

    have = set(mscale.get_scale_names())
    opts = [s for s in _SCALE_CHOICES if s in have]
    return ([str(current)] if str(current) not in opts else []) + opts


def _mk_set_scale(which: str):
    """换 scale。matplotlib 会把该轴的 locator/formatter 整套换成新 scale 的
    默认值，所以刻度模型的「脚本原样」必须当场重采——不重采的话「自动刻度」
    会把线性轴的 AutoLocator 按到对数轴上（一个刻度都出不来）。"""

    def s(a: Axes, v) -> None:
        getattr(a, f"set_{which}scale")(str(v))
        invalidate_tick_cfg(a, which)

    return s


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


def _set_legend_loc_preset(leg: Legend, v) -> None:
    leg.set_bbox_to_anchor(None)
    leg.set_loc(str(v))


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
    gid 与 override 都挂在它上面）、示意线的**脚本原样快照**（`pristine`，
    没有源的项重建时的唯一素材，也是「撤销到底」时的样子）、以及
    `custom_base`——一个 custom 项「不带任何 handle_* override 时长什么样」：
    脚本原样就是 custom 的项等于 pristine；从跟随状态脱开的项等于脱开那一刻
    从源派生出来的样子（否则改列数重排之后它会退回脚本原样，而脱开之前它
    明明已经跟着源变过了）。
    """

    def __init__(self, leg: Legend, state: "FigState") -> None:
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
        self.custom_base: list = list(self.pristine)
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

    def base_of(self, j: int):
        """重建 / 同步时这一项该从谁派生：跟随的从源，其余从 custom_base。"""
        if self.effective_binding(j) == "follow_source":
            return self.sources[j]
        return self.custom_base[j]

    # ---- 视图 ----
    def shown(self) -> list[int]:
        """当前显示的原始序号，按显示顺序。"""
        return [j for j in self.order if j not in self.hidden]

    def display_index(self, j: int) -> int | None:
        shown = self.shown()
        return shown.index(j) if j in shown else None

    def handle_of(self, j: int):
        """条目 j 此刻的示意线 artist；隐藏中的项回它的 custom_base。"""
        k = self.display_index(j)
        if k is None:
            return self.custom_base[j]
        handles = [h for h in self.leg.legend_handles if h is not None]
        return handles[k] if k < len(handles) else self.custom_base[j]

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
        box.set_figure(leg.get_figure(root=False))
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
    `fig.axes` 里没有它们，遍历权威只有 `manifest._ordered_axes` 一处）。"""
    out = list(getattr(fig, "legends", []) or [])
    for ax in _sibling("manifest")._ordered_axes(fig)[0]:  # noqa: SLF001
        leg = ax.get_legend()
        if leg is not None:
            out.append(leg)
    return out


def sync_legends(state: "FigState") -> None:
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
                    # 回到跟随，「脱开时的样子」就作废了
                    model.custom_base[j] = model.pristine[j]
                elif not model.has_style_override(j):
                    # custom 而没有 override：示意线该是 custom_base 的样子
                    # （撤掉 binding override 之后要退回脚本原样）。指纹相同就
                    # 不动——重派生不是免费的，也不该每一帧都换对象
                    base = model.custom_base[j]
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


def rebuild_legend(leg: Legend, state: "FigState") -> None:
    """按条目模型重排图例盒（列数 / 间距 / 顺序 / 隐藏都走这一条）。

    素材：跟随的项拿**源对象**重新派生（与 `ax.legend()` 同一条路，误差棒
    仍是误差棒、markerscale 只乘一次）；其余拿它的 custom_base 快照——快照
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
            fresh, copy = new_handles[k], model.custom_base[j]
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
    """一个跟随中的项脱开：custom_base 记成**源此刻**派生出来的样子，图例盒里
    那份活的示意线也换成它。

    必须从源现派生，不能拿盒里那份：同一批 patch 里源的改动可能排在前面、
    而跟随同步要到整轮结束才跑——盒里那份此刻还是上一轮的样子。拿它当
    脱开点，会把「先把线改绿、再在图例项上改线宽」做成一条红线。
    """
    src = model.sources[j]
    fresh = legend_fresh_handle(model.leg, src)
    if fresh is None:
        model.custom_base[j] = model.snapshot(model.handle_of(j))
        return
    model.custom_base[j] = fresh
    k = model.display_index(j)
    if k is not None:
        _legend_replace_handle(model.leg, k, src)


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


def legend_handle_props(h) -> tuple[str, ...]:
    """这一种示意线支持哪几条 handle_* prop（manifest 与 handler 共用）。

    色图**正在**决定颜色的 Collection 示意线（映射散点的图例）不给 `handle_color`
    ——`set_facecolor` 设得进去，下一次 draw `update_scalarmappable()` 原样
    覆盖回来，那是一个改了没反应的控件（判据与散点本体同一个：
    `color_mapping_is_live`）。
    """
    if isinstance(h, Line2D):
        return LEGEND_ENTRY_STYLE_PROPS
    if isinstance(h, Patch):
        return ("handle_color",)
    if isinstance(h, Collection):
        # 面 / 散点 / 线组的示意线只给颜色：它们的「线宽」是边宽，而边色
        # 默认与面同色（fill_between 的示意矩形），改了 0.5 pt 在像素上
        # 量不出——一个改了看不见的控件不如不摆
        return () if color_mapping_is_live(h) else ("handle_color",)
    return ()


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


def _reindex_legend_children(leg: Legend, state: "FigState") -> None:
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
        # 图例项的文字带条目模型标记：它既是一段文字（全部 text handler 照用），
        # 又是一个「条目」（示意线样式 / 绑定 / 隐藏挂在它上面）。图例标题
        # 没有这个标记，仍是普通文字。
        return "legend_text" if getattr(artist, "_mm_legend_entry", None) else "text"
    if isinstance(artist, FancyArrowPatch):
        return "arrowpatch"
    if isinstance(artist, Line2D):
        return "line"
    if isinstance(artist, Legend):
        return "legend"
    # **`_AxesBase` 而不是 `Axes`**：`secondary_[xy]axis` 建出来的
    # `SecondaryAxis` 直接从 `_AxesBase` 派生，**不是 `Axes` 的子类**
    # （实测 mro = [SecondaryAxis, _AxesBase, Artist]）。只判 `Axes` 的话它
    # 拿不到任何容器级字段——visible / grid / spines 一个都出不来，而它的
    # 轴标签与刻度是独立元素、照常可编辑，于是界面上出现「这条轴的字能改、
    # 轴本身点了没反应」这种说不通的半吊子。
    # 私有基类是有意为之：matplotlib 没有公开的「Axes 类容器」抽象。
    # `test_secondary_axis_detection_still_works` 看着这条依赖。
    if isinstance(artist, _AxesBase):
        return "axes"
    if isinstance(artist, AxesImage):
        return "image"
    if isinstance(artist, Rectangle) and getattr(artist, "_mm_bar", False):
        return "bar"
    # Patch family：`ax.fill()` 的 Polygon、手搓的 PathPatch，以及 pie 的
    # Wedge、axhspan 的 Rectangle、Circle / Ellipse / Arc / FancyBboxPatch /
    # StepPatch，还有用户自己继承出来的子类——**按 family 认，不逐个列类名**。
    # 必须排在 FancyArrowPatch / bar 之后：它们也是 Patch，各有各的契约。
    if isinstance(artist, Patch):
        return "patch"
    # 线组：hlines / vlines 的参考线、stem 的竖线、eventplot 的事件线、
    # streamplot 的流线、violinplot 的极值线——全是 LineCollection（含它的
    # 子类 EventCollection）。**刻意不并进 `line`**：Line2D 的 getter 回标量、
    # LineCollection 回数组，setter 也各吃各的，硬合成一族迟早分叉。
    # 也**刻意不并进下面的 `collection`**：它对外的 prop 是 `color`，而
    # Collection 族给的是 facecolor / edgecolor——两套命名都已经发出去了。
    if is_linecoll_family(artist):
        return "linecoll"
    # Collection family：散点（PathCollection）与填充（PolyCollection）之外
    # 还有 QuadMesh / ContourSet / Quiver / Barbs…
    # **能改什么由 `collection_caps()` 按真实 getter 实况决定**，不按类名——
    # 所以这里一个 key 就够，manifest 那边再决定 advertise 哪几条。
    if isinstance(artist, Collection):
        return "collection"
    # 认不出来的 Artist：不是「不支持」，是「只支持得起两条」。
    # `_GENERIC_CAPS` 只给 visible / zorder——它们由 draw 的公共机制兑现，
    # 任何子类都逃不掉。别在这里加 alpha：那要靠每个 artist 自己在 draw 里读。
    if isinstance(artist, Artist):
        return "artist"
    return None


def _get_coll_edgecolor(coll):
    """边色的**可回灌**表示：`_original_edgecolor`，不是解析出来的 RGBA 数组。

    与 `_get_linecoll_ls` 是同一个坑的第三个入口——**getter 回的形状 ≠ setter
    吃的形状**，而这一次的代价不是「值不一样」，是**能力被永久杀掉**。

    matplotlib 判「边归不归 colormap 管」用的是 `_set_mappable_flags()`，
    而它只看两个原始值（实测读的就是这段源码）：

        if self._A is not None:
            if not _str_equal(self._original_facecolor, 'none'):
                self._face_is_mapped = True
            else:
                if self._original_edgecolor is None:      # <── 就是这一句
                    self._edge_is_mapped = True

    `set_edgecolor(c)` 把 `_original_edgecolor` 从 `None` 换成 `c`。**没有面的
    映射集合**（LineCollection、`contour`）的颜色正是走边这条通道，于是：

        改一次边色 → 撤销（回灌解析出来的 RGBA 数组）→ 像素**看着回去了**，
        `_original_edgecolor` 却再也不是 None → 这条元素的 colormap **永久失效**，
        之后改 cmap / vmin / vmax 一个像素都不动，而且不报错。

    实测（3.10.8，映射的 LineCollection）：改 cmap 单独跑动 3278 px；走一遍
    「改边色再撤销」之后，同一句 `set_cmap` 变成 **0 px**。撤销把像素还了，
    把能力吞了——界面上那三个色图控件从此是死的。

    回灌 `_original_edgecolor`（映射态下就是 `None`）能让 matplotlib 自己把
    标志位重新算对。有面的那些（pcolormesh / 映射散点）不受影响：它们的
    mapping 走 face 通道，边色本来就是用户的。
    """
    return getattr(coll, "_original_edgecolor", coll.get_edgecolor())


def _get_coll_facecolor(coll):
    """面色的可回灌表示。理由同 `_get_coll_edgecolor`：`_set_mappable_flags()`
    读的是 `_original_facecolor`，回灌解析后的数组会让 `'none'` 这个**字符串
    语义**丢失（数组不等于 'none'），于是本来没有面的集合被判成有面。"""
    return getattr(coll, "_original_facecolor", coll.get_facecolor())


# ---------------------------------------------------------------------------
# Artist family 能力层（2026-08-21）
#
# 一条 prop 只写一次、注册给整个 family。`("patch","alpha")` 与
# `("bar","alpha")` 曾经是两份逐字相同的 lambda，`("scatter","facecolor")`
# 与 `("fill","facecolor")` 也是。重复本身不致命，**分叉**才是：改了一处忘了
# 另一处，同一个属性在两种元素上行为不同，而没有任何东西会报出来。
#
# 能力**按真实 getter 实况判，不按类名**。`pcolor()` 出的 PolyQuadMesh 是
# PolyCollection 的子类，却永远按 cmap 上色——给它开 facecolor，用户点了颜色、
# `Collection.update_scalarmappable()` 在下一次 draw 里原样覆盖回去，屏幕上
# 一个像素都不变（mpl 3.10.8 / 3.11.1 实测一致）。「界面说改了、画面没动」
# 是最坏的一种假支持，所以 fill 能力的判据是「此刻真的有 facecolors **且**
# 没在做颜色映射」，不是「它是不是 PolyCollection」。
#
# 反过来 stroke（edgecolor / linewidth / linestyle）对任何 Collection 都成立
# ——此刻没有边不代表加不上边，`pcolormesh` 加网格线正是常见需求。
# ---------------------------------------------------------------------------
#: Collection / Patch 通用的「安全 setter」——都是 Artist 基类或 family 基类
#: 上的公开 API，子类没有一个重定义成别的语义。
_CAP_ALPHA = (lambda a: a.get_alpha(), lambda a, v: a.set_alpha(None if v is None else float(v)))
_CAP_VISIBLE = (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v)))
_CAP_ZORDER = (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v)))
_CAP_LABEL = (lambda a: str(a.get_label()), lambda a, v: a.set_label(str(v)))
_CAP_HATCH = (
    lambda a: a.get_hatch(),
    lambda a, v: a.set_hatch(None if v in (None, "", "none") else str(v)),
)
#: 颜色映射（ScalarMappable / ColorizingArtist）：Collection 与 AxesImage 共享。
#: 原生值存 Colormap 对象本身，`set_cmap` 两种都吃。
_CAP_CMAP = (lambda a: a.get_cmap(), lambda a, v: a.set_cmap(v))
_CAP_VMIN = (
    lambda a: a.get_clim()[0],
    lambda a, v: a.set_clim(vmin=(None if v is None else float(v))),
)
_CAP_VMAX = (
    lambda a: a.get_clim()[1],
    lambda a, v: a.set_clim(vmax=(None if v is None else float(v))),
)

#: 花纹的可选项。`""` = 不用花纹（黑白印刷时区分同色区块的标准手段）。
HATCHES = ["", "/", "\\", "|", "-", "+", "x", "o", "O", ".", "*", "//", "\\\\", "xx", "..", "++"]

#: Collection family（PathCollection / PolyCollection / LineCollection /
#: QuadMesh / ContourSet / EventCollection / Quiver …）。颜色与线宽都是
#: **逐元素数组**，原生值必须 `.copy()`——不拷贝的话 restore 拿到的是同一个
#: 数组对象，setter 就地改完原值也跟着变，还原等于什么都没做。
_COLLECTION_CAPS: dict[str, tuple] = {
    "label": _CAP_LABEL,
    # face / edge 的 getter 回的是**原始设定**（`_original_*`），不是解析出来
    # 的 RGBA 数组：matplotlib 判「这条通道归不归 colormap 管」只看那两个值，
    # 回灌数组会把映射永久关掉（详见 `_get_coll_edgecolor`）。
    "facecolor": (_get_coll_facecolor, lambda a, v: a.set_facecolor(v)),
    "edgecolor": (_get_coll_edgecolor, lambda a, v: a.set_edgecolor(v)),
    "linewidth": (lambda a: a.get_linewidths().copy(), _set_collection_lw),
    # 线型的 getter 走**未缩放**规格（`_us_linestyles`）而不是
    # `get_linestyle()`：后者回的是按线宽缩放过的 dash，`set_linestyle()` 会
    # 把喂进去的值再缩放一遍，撤销一次线型就疏一档、复利放大。整个
    # Collection 族都是这个毛病，不只线组——判据与理由见 `_get_linecoll_ls`。
    "linestyle": (_get_linecoll_ls, _set_linecoll_ls),
    "hatch": _CAP_HATCH,
    "size": (lambda a: a.get_sizes().copy(), _set_collection_sizes),
    "marker": (lambda a: list(a.get_paths()), _set_scatter_marker),
    "cmap": _CAP_CMAP,
    "vmin": _CAP_VMIN,
    "vmax": _CAP_VMAX,
    "alpha": _CAP_ALPHA,
    "visible": _CAP_VISIBLE,
    "zorder": _CAP_ZORDER,
}

#: Patch family（Rectangle / Polygon / PathPatch / Wedge / Circle / Ellipse /
#: Arc / FancyBboxPatch / StepPatch / Annulus …，以及用户自己继承的子类）。
#: 这些 getter/setter 全在 `Patch` 基类上，子类一个都没改语义。
_PATCH_CAPS: dict[str, tuple] = {
    "facecolor": (lambda a: a.get_facecolor(), lambda a, v: a.set_facecolor(v)),
    "edgecolor": (lambda a: a.get_edgecolor(), lambda a, v: a.set_edgecolor(v)),
    "linewidth": (lambda a: float(a.get_linewidth()), lambda a, v: a.set_linewidth(float(v))),
    "linestyle": (lambda a: a.get_linestyle(), _set_linestyle),
    "hatch": _CAP_HATCH,
    "fill": (lambda a: bool(a.get_fill()), lambda a, v: a.set_fill(bool(v))),
    "alpha": _CAP_ALPHA,
    "visible": _CAP_VISIBLE,
    "zorder": _CAP_ZORDER,
}

#: 认不出来的 Artist 只开这两条。两者都由 `Axes.draw` / `Artist.draw` 的
#: 公共机制兑现，任何 Artist 子类都逃不掉；`alpha` **刻意不给**——它要靠每个
#: artist 自己在 draw 里读，基类不保证，给了就是又一个「点了没反应」的开关。
_GENERIC_CAPS: dict[str, tuple] = {
    "visible": _CAP_VISIBLE,
    "zorder": _CAP_ZORDER,
}


def _len0(seq) -> int:
    """长度；拿不到长度的（None / 标量）算 0。"""
    try:
        return len(seq)
    except TypeError:
        return 0


def is_color_mapped(artist) -> bool:
    """这个 artist **带不带**数值→颜色的映射（`get_array()` 非空）。

    判据是数组在不在，不是类名：同一个 PathCollection，`scatter(x, y)` 不带、
    `scatter(x, y, c=z)` 带；而 `pcolor()` 出的 PolyQuadMesh 是 PolyCollection
    的子类却**永远**带。

    ## 这条问的是「身份」，不是「此刻生不生效」

    它决定 **family**（`is_linecoll_family` → gid 前缀 → handler 家族），
    所以**必须在一次会话里恒定**。数组是脚本给的，用户的 override 动不了它。

    「此刻那套色图控件生不生效」是另一个问题，问 `color_mapping_is_live()`
    ——那个会随用户改边色而变。两者**不能合并**：合了的话，用户给一条映射的
    线组设了边色之后 `_cls_key` 会当场从 `collection` 翻成 `linecoll`，
    于是下一次 apply 按线组去查 handler、`HANDLERS[("linecoll","cmap")]` 不
    存在，好端端的元素开始报「不支持的属性」。gid 与 family 的稳定性优先。
    """
    get = getattr(artist, "get_array", None)
    if get is None:
        return False
    try:
        return get() is not None
    except Exception:  # noqa: BLE001 — 探针失败一律当「没在映射」
        return False


def _is_none_color(v) -> bool:
    return isinstance(v, str) and v.lower() == "none"


def color_mapping_is_live(artist) -> bool:
    """色图**此刻真的在决定颜色**吗——决定要不要给 cmap / vmin / vmax 控件。

    「有数组」不等于「在映射」。matplotlib 的 `Collection._set_mappable_flags()`
    是这么判的（照抄它的规则，这里不重新发明）：

        if self._A is not None:
            if not _str_equal(self._original_facecolor, 'none'):
                self._face_is_mapped = True         # 面在映射
            else:
                if self._original_edgecolor is None:
                    self._edge_is_mapped = True     # 面是 none 时才轮到边

    两种情况会让数组在、映射却不在（**实测两条都让 cmap 一个像素都改不动**）：

      * 脚本自己写死了颜色 —— `LineCollection(..., colors="red", array=z)`：
        面是 `'none'`、边被显式设过，两个标志都是 False；
      * **用户设过我们自己开放的 `edgecolor`** —— 映射的线组被设了边色之后
        进入同一个状态。那时还把 cmap / vmin / vmax 摆在界面上，就是三个
        设得进状态、画面纹丝不动的控件。撤掉边色 override 之后它们自然回来
        （`_get_coll_edgecolor` 回灌的是 `_original_edgecolor`）。

    **不读 `_face_is_mapped` / `_edge_is_mapped` 那两个标志**：它们要等
    `update_scalarmappable()`（即一次 draw）之后才有值，而 `instrument()` 跑在
    第一次 draw 之前——那时读到的是 `None`，判据会随「问得早还是问得晚」变。
    照抄规则是纯函数，什么时候问都一样。
    """
    if not is_color_mapped(artist):
        return False
    if not isinstance(artist, Collection):
        # AxesImage 这类：**只有二维数组才走色图**。`imshow` 吃的
        # `(M, N, 3)` / `(M, N, 4)` 是已经成色的 RGB(A) 位图，`get_array()`
        # 照样非空，但 `set_cmap` 一个像素都不动（实测：灰度 23409，
        # RGB 与 RGBA 都是 0）。今天没有用户可见后果——`manifest._image_fields`
        # 另有一道 `arr.ndim == 2` 的闸，色图字段本来就不出——但这条谓词写着
        # 「有数组就是按它上色」是**假的**，下一个拿它当判据的人会踩空。
        arr = getattr(artist, "get_array", lambda: None)()
        return getattr(arr, "ndim", 0) == 2
    orig_fc = getattr(artist, "_original_facecolor", None)
    if not _is_none_color(orig_fc):
        return True  # 面在映射（facecolor 没被写死成 'none'）
    return getattr(artist, "_original_edgecolor", None) is None


def is_linecoll_family(artist) -> bool:
    """归不归**线组**那一族。`manifest.instrument` 与 `_cls_key` 的**唯一判据**。

    两处必须问同一个函数：登记时按它挑 gid 前缀与 role，dispatch 时按它挑
    handler 家族与字段表。分开写必然漂开，而漂开的表现是「manifest 说这是个
    映射色的通用 collection、检查器却按线组给了 `color`」——界面上那个控件
    改不动任何东西，因为 `HANDLERS[("linecoll", …)]` 根本不在这个元素上。

    **标量映射的不算线组**：那时颜色由 colormap 每次 draw 重算，线组对外的
    `color` 是个单值，表达不了逐条颜色。它们走通用 Collection 族，由
    `collection_caps()` 按实况给出 cmap / vmin / vmax 与描边。
    """
    return isinstance(artist, LineCollection) and not is_color_mapped(artist)


def colorbar_mapping_is_live(cb) -> bool:
    """**色条**那一侧该不该给 cmap / vmin / vmax。

    不能直接用 `color_mapping_is_live(cb.mappable)`：那条判据问的是「映射此刻
    还在不在决定**这个 artist 的颜色**」，而 `fig.colorbar(ScalarMappable(
    norm=…, cmap=…), ax=ax)` 里那个 mappable **根本没有数据数组**（三个版本
    实测 `get_array()` 都是 None），于是被判成「映射不在」，色条的三个控件
    全被摘掉——可 `set_cmap` 明明改得动色条本身。又一次「能改却不宣称」。

    真正的区分点不是「有没有数组」，是**图上有没有一个会与色标对不上的
    artist**：

      * 独立 mappable **不是 Artist**（3.8.4 / 3.10.8 / 3.11.1 实测一致，
        `_ScalarMappable` 不继承 Artist）——图上没有对应的图元，色条就是它
        唯一的呈现，改 cmap 不存在「色标变了、数据没变」的风险 → **给**。
      * 真正的图元（AxesImage / Collection）→ 仍然按
        `color_mapping_is_live` 判。映射的线组被设过 edgecolor 之后，
        映射不再决定线的颜色，那时给 cmap 就是让色标与数据脱节 → **不给**。
        （那条闸是上一轮修的，必须原样留着。）
    """
    m = getattr(cb, "mappable", None)
    if m is None:
        return False
    if not isinstance(m, Artist):
        return True
    return color_mapping_is_live(m)


def honours_faces(coll) -> bool:
    """这个 Collection 上 `set_facecolor` 到底**能不能把面涂成你要的颜色**。

    判据**不是「此刻有没有面色」**。`scatter(facecolors="none")` 的
    `get_facecolor()` 长度为 0，而 marker 路径是闭合可填的：实测
    `set_facecolor("#FF00FF")` 改 1197 像素，且 draw 之后 `get_facecolor()[0]`
    **精确等于品红**。那是「能改却不宣称」——与「宣称却改不动」同样是能力
    不真实，只是方向相反，而这一条还是本次 family 重构**引入的回归**
    （旧的散点契约是无条件给 facecolor 的）。

    实测表（三个 matplotlib 版本逐格一致，`set_facecolor("#FF00FF")`）：

        scatter facecolors="none"  长度 0   1197 px   draw 后**是品红**   → 给
        scatter 默认                长度 1   1120 px   draw 后**是品红**   → 给
        scatter marker="x"         长度 1    569 px   draw 后**是品红**   → 给
        LineCollection             长度 0      0 px                      → 不给
        contour（映射）             长度 0  48654 px   draw 后**是 viridis** → 不给
        fill_between               长度 1  30061 px   draw 后**是品红**   → 给

    **contour 那一格是这条判据最容易踩的坑**：它像素变了整整 48654 个，
    只看「像素变没变」会判成「能改」。但那不是你设的颜色——`set_facecolor`
    只是把原本 `none` 的面打开，随后 `update_scalarmappable()` 用映射色重画。
    像素变了 ≠ 变成了你要的。（映射中的那些由 `color_mapping_is_live` 挡在
    更上游，这里只是把理由记清楚。）
    """
    if isinstance(coll, PathCollection):
        # marker 路径天然可填，与此刻是不是空心无关。
        return True
    try:
        return bool(_len0(coll.get_facecolor()))
    except Exception:  # noqa: BLE001
        return False


def honours_stroke(coll) -> bool:
    """这个 Collection 的 draw 认不认**描边本身**（边色 / 线宽）。

    `TriMesh`（`tripcolor(..., shading="gouraud")`）不认。它整块交给
    `renderer.draw_gouraud_triangles`——那个渲染原语只接**顶点颜色**，
    连边都不画。实测（同一张图，先设 `edgecolor="#ff00ff"` 再加 `linewidth=3`，
    数变化的像素）：

        TriMesh(gouraud)      edgecolor     0   +linewidth     0
        QuadMesh(pcolormesh)  edgecolor  4086   +linewidth  8010
        PolyCollection        edgecolor  1834   +linewidth  3175

    注意 `QuadMesh` 与 `TriMesh` 在这里**分家**：网格类不认花纹与线型
    （见 `honours_stroke_style`），但 `QuadMesh` 是认边色与线宽的（给
    pcolormesh 加网格线是常见需求）。所以这是**两条**判据，不是一条。

    ## 这条是怎么漏掉的

    `honours_stroke_style` 那张实测表**把描边当成了基线**：它先设
    `edgecolor` + `linewidth`，再量加上 hatch / linestyle 之后的增量。基线
    本身有没有效果，那张表从来没问过——于是 TriMesh 的 `edgecolor` 与
    `linewidth` 一路是「宣称了、设得进去、画面纹丝不动」。
    现在探针把描边的像素数也一并报出来，用例两头都断言（认的必须 >0，
    不认的必须 ==0），基线不再是没人验的那一半。
    """
    return not isinstance(coll, TriMesh)


def honours_stroke_style(coll) -> bool:
    """这个 Collection 的 draw 认不认 `hatch` / `linestyle`。

    **网格类不认**。`QuadMesh`（`pcolormesh`）与 `TriMesh`（`tripcolor(...,
    shading="gouraud")`）不走 Collection 的通用绘制路径，而是把整块网格交给
    `renderer.draw_quad_mesh` / `draw_gouraud_triangles`——那两个渲染原语只接
    边色与线宽，**花纹和虚线在参数里根本不存在**。setter 照收、getter 照回、
    manifest 照报，画面一个像素都不动。

    实测（3.10.8，同一张图、都先设了 `edgecolor="#ff00ff"` + `linewidth=2`，
    数的是变化的像素数）：

        QuadMesh(pcolormesh)          hatch      0   linestyle      0
        TriMesh(tripcolor gouraud)    hatch      0   linestyle      0
        PolyQuadMesh(pcolor)          hatch  10692   linestyle   1100
        PolyCollection(fill_between)  hatch   8304   linestyle   2616
        PathCollection(scatter)       hatch   5036   linestyle   3560
        EllipseCollection             hatch   1202   linestyle    771
        RegularPolyCollection         hatch   1952   linestyle   1063
        CircleCollection              hatch   2787   linestyle   1290
        PatchCollection               hatch   4585   linestyle   1589
        hexbin(PolyCollection)        hatch  11017   linestyle   2088

    注意 `pcolor` 与 `pcolormesh` 落在**两侧**：前者出 `PolyQuadMesh`，走通用
    路径，两条都认。所以这不是「网格图不支持」，是「那两个渲染原语不支持」。

    ## 为什么这里仍然是 isinstance

    matplotlib 没有公开的「你的 draw 认不认 hatch」谓词，而 `draw` 被
    `@allow_rasterization` 包过，按字节码反查用的哪个渲染原语拿到的是包装器的
    code——试过，`co_names` 里什么都没有。

    但**这条例外与 `Arc` 那条不是一回事**：Arc 那次是照着注释推理、没做同类
    对比（漏了 `fill` 默认为 False），而这张表是同条件量出来的，并且由
    `tests/test_invariants_engine.py::test_the_mesh_stroke_style_table_still_holds`
    **每次跑都重新渲染一遍**。哪天 matplotlib 给 `draw_quad_mesh` 补上花纹，
    那条用例会红，我们跟着放开——例外不会悄悄过期。
    """
    return not isinstance(coll, (QuadMesh, TriMesh))


def collection_caps(coll) -> frozenset[str]:
    """这个 Collection 上**真正改得动**的能力集。manifest 与 handler 共用它。

    * ``stroke``  边线：多数 Collection 都能加/改边（现在没有边 ≠ 加不上），
                  但 `TriMesh` 走 `draw_gouraud_triangles`、**连边都不画**
                  （实测 edgecolor / linewidth 各 0 像素）。判据
                  `honours_stroke`
    * ``stroke_style`` 花纹与线型：**网格类不认**（`QuadMesh` / `TriMesh` 交给
                  `draw_quad_mesh` / `draw_gouraud_triangles`，那两个渲染原语
                  只接边色与线宽）。判据见 `honours_stroke_style` 的实测表
    * ``faces``   **有面可画**：`get_facecolor()` 非空。这与 `fill` 是两件事
                  ——映射的 QuadMesh / contourf / hexbin 有面（花纹画得上）
                  但 facecolor 不归用户改；而 LineCollection 与 `contour`
                  的 facecolor 是 `'none'`，**连面都没有**，给花纹就是给一个
                  设得进状态、画面上一个像素都不变的开关（实测：面向的
                  `get_facecolor()` 长度 pcolormesh 36 / contourf 7 /
                  fill_between 1，而 contour 与 LineCollection 都是 0）
    * ``fill``    填充：有面 **且**没在做颜色映射（见本节抬头）
    * ``mapped``  颜色映射：cmap / vmin / vmax
    * ``sizes``   标记大小：`_CollectionWithSizes` 且此刻真的有 sizes
    * ``marker``  标记形状整体替换：**只有 PathCollection**。`set_paths` 对
                  散点是换 marker，对 PolyCollection 是把用户的多边形几何整个
                  换掉——那是改数据，不是改样式。
    """
    caps = {"base"}
    if honours_stroke(coll):
        caps.add("stroke")
    if honours_stroke_style(coll):
        # `stroke_style` = 花纹与线型。与 `stroke`（边色 / 线宽）分开，因为
        # 网格类认后者不认前者——见 `honours_stroke_style` 的实测表。
        caps.add("stroke_style")
    if honours_faces(coll):
        caps.add("faces")
    # **判据是「此刻在不在映射」，不是「带不带数组」**：脚本写死了颜色、或者
    # 用户设过我们开放的 edgecolor 之后，数组还在、映射已经不在了——那时
    # cmap/vmin/vmax 是三个设得进状态、画面纹丝不动的控件（实测 0 像素）。
    # 反过来，映射不在了 facecolor 就重新归用户管，`fill` 该给就给。
    if color_mapping_is_live(coll):
        caps.add("mapped")
    elif "faces" in caps:
        caps.add("fill")
    get_sizes = getattr(coll, "get_sizes", None)
    if get_sizes is not None:
        try:
            if _len0(get_sizes()):
                caps.add("sizes")
                if isinstance(coll, PathCollection):
                    caps.add("marker")
        except Exception:  # noqa: BLE001
            pass
    return frozenset(caps)


def _install_caps(key: str, caps: dict[str, tuple]) -> None:
    """把一族能力注册到 HANDLERS[(key, prop)]。

    `setdefault` 是有意的：族里已经有的**专用**实现永远优先（色条的 label、
    柱的 bar_width…）。能力层是补齐重复的那一层，不是推翻既有裁决的那一层。
    """
    for prop, handler in caps.items():
        HANDLERS.setdefault((key, prop), handler)


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
    if float(dist.max()) < 1e-3:  # 全白：没有可辨识的基色
        return None
    base = flat[int(np.argmax(dist))]
    ok = np.abs(1.0 - base) > 1e-3  # 基色为 1 的通道恒等于 1，解不出 s
    if not ok.any():
        return None
    s = (flat[:, ok] - base[ok]) / (1.0 - base[ok])
    if s.shape[1] > 1:
        spread = s.max(axis=1) - s.min(axis=1)
        if float(np.mean(spread > 0.04)) > 0.02:
            return None  # 通道间不一致 → 不是单色渐变
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
    sub = a[:: max(1, a.shape[0] // 64), :: max(1, a.shape[1] // 64)]
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
    ("text", "text"): (lambda a: a.get_text(), lambda a, v: a.set_text(str(v))),
    ("text", "fontsize"): (lambda a: a.get_fontsize(), lambda a, v: a.set_fontsize(float(v))),
    ("text", "color"): (lambda a: a.get_color(), lambda a, v: a.set_color(v)),
    ("text", "weight"): (lambda a: a.get_fontweight(), lambda a, v: a.set_fontweight(v)),
    ("text", "style"): (lambda a: a.get_fontstyle(), lambda a, v: a.set_fontstyle(v)),
    ("text", "rotation"): (lambda a: a.get_rotation(), lambda a, v: a.set_rotation(float(v))),
    ("text", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("text", "pos_frac"): (_get_text_pos, _set_text_pos_frac),
    ("text", "alpha"): (
        lambda a: a.get_alpha(),
        lambda a, v: a.set_alpha(None if v is None else float(v)),
    ),
    ("text", "fontfamily"): (_get_text_fontfamily, _set_text_fontfamily),
    ("text", "ha"): (lambda a: a.get_ha(), lambda a, v: a.set_ha(v)),
    ("text", "va"): (lambda a: a.get_va(), lambda a, v: a.set_va(v)),
    # getter 回**可回灌**的原样（可能是 `'normal'`），不是显示用的 1.2
    ("text", "linespacing"): (_get_text_linespacing, _set_text_linespacing),
    # 仅 3D 轴标签（manifest 打了 _mm_axis 标记）：沿投影轴推远/拉近
    ("text", "labelpad"): (
        lambda a: float(a._mm_axis.labelpad),
        lambda a, v: setattr(a._mm_axis, "labelpad", float(v)),
    ),
    ("text", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    # 背景框那一族的注册在下面（`_BBOX_PROPS`）——它们共用一个 patch，
    # 还原要能把「本来就没有框」整个还回去，所以 handler 与 restore 成对登记。
    ("text", "stroke_enabled"): (
        lambda a: bool(_stroke_state(a)["enabled"]),
        lambda a, v: _stroke_set(a, "enabled", bool(v)),
    ),
    ("text", "stroke_color"): (
        lambda a: _stroke_state(a)["color"],
        lambda a, v: _stroke_set(a, "color", v),
    ),
    ("text", "stroke_width"): (
        lambda a: float(_stroke_state(a)["width"]),
        lambda a, v: _stroke_set(a, "width", float(v)),
    ),
    # 图内独立箭头（FancyArrowPatch：脚本 add_patch 的与 annotate 的 arrow_patch
    # 同一个类）。set_color 同时写 edge+face——"-|>" 这类实心帽两者必须一致，
    # 分开暴露只会做出「帽黑杆红」的半成品
    ("arrowpatch", "color"): (lambda a: a.get_edgecolor(), lambda a, v: a.set_color(v)),
    ("arrowpatch", "linewidth"): (
        lambda a: a.get_linewidth(),
        lambda a, v: a.set_linewidth(float(v)),
    ),
    ("arrowpatch", "mutation_scale"): (
        lambda a: a.get_mutation_scale(),
        lambda a, v: a.set_mutation_scale(float(v)),
    ),
    ("arrowpatch", "alpha"): (
        lambda a: a.get_alpha(),
        lambda a, v: a.set_alpha(None if v is None else float(v)),
    ),
    ("arrowpatch", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("arrowpatch", "zorder"): (
        lambda a: float(a.get_zorder()),
        lambda a, v: a.set_zorder(float(v)),
    ),
    # 端点与样式：位置只对独立箭头开放（manifest 侧把关），样式两类都能改。
    # 原生值分别是 transform 坐标的端点对 / ArrowStyle 对象 / linestyle 原值，
    # 恢复走 _RESTORE 里的专用函数
    ("arrowpatch", "endpoints_frac"): (_get_arrow_endpoints, _set_arrow_endpoints),
    ("arrowpatch", "arrowstyle"): (lambda a: a.get_arrowstyle(), _set_arrowstyle),
    ("arrowpatch", "linestyle"): (
        lambda a: a.get_linestyle(),
        lambda a, v: a.set_linestyle(str(v)),
    ),
    ("line", "color"): (lambda a: a.get_color(), lambda a, v: a.set_color(v)),
    ("line", "linewidth"): (lambda a: a.get_linewidth(), lambda a, v: a.set_linewidth(float(v))),
    ("line", "linestyle"): (lambda a: a.get_linestyle(), lambda a, v: a.set_linestyle(v)),
    ("line", "marker"): (lambda a: a.get_marker(), lambda a, v: a.set_marker(v)),
    ("line", "markersize"): (lambda a: a.get_markersize(), lambda a, v: a.set_markersize(float(v))),
    ("line", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
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
    ("legend", "loc_frac"): (_get_legend_loc, _set_legend_loc_frac),
    # 坐标范围的 getter 回**可回灌**的表示：脚本没有显式设过范围时，那个
    # 「原样」不是一对数字，而是「自动缩放」这个**模式**。见 `_get_axes_lim`。
    ("axes", "xlim"): (_get_axes_lim("x"), _set_axes_lim("x")),
    ("axes", "ylim"): (_get_axes_lim("y"), _set_axes_lim("y")),
    ("axes", "position"): (
        lambda a: list(a.get_position().bounds),
        _set_axes_position,
    ),
    ("axes", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    ("image", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    # 原生值 = 整个像素数组（恢复走 set_data，见 _restore_image_gradient）
    ("image", "gradient_color"): (
        lambda a: np.array(a.get_array(), copy=True),
        _set_image_gradient,
    ),
    # 单条刻度文字：冻结整条轴（FixedLocator + FixedFormatter）才留得住，
    # 生命周期与索引身份见 TickLabel 的类注释
    ("ticklabel", "text"): (
        lambda a: a.get_text(),
        _set_ticklabel_text,
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
            **({"labelbottom": bool(v)} if a.which == "x" else {"labelleft": bool(v)})
        ),
    ),
    ("figure", "size_mm"): (
        lambda f: [x * 25.4 for x in f.get_size_inches()],
        lambda f, v: f.set_size_inches(float(v[0]) / 25.4, float(v[1]) / 25.4, forward=False),
    ),
    ("figure", "facecolor"): (
        lambda f: f.patch.get_facecolor(),
        lambda f, v: f.patch.set_facecolor(v),
    ),
    ("figure", "transparent"): (
        lambda f: not f.patch.get_visible(),
        lambda f, v: f.patch.set_visible(not bool(v)),
    ),
    # ---- axes: 比例 / 反转 / 缩放 / 网格 / spine / 底色 ----
    ("axes", "xscale"): (lambda a: a.get_xscale(), _mk_set_scale("x")),
    ("axes", "yscale"): (lambda a: a.get_yscale(), _mk_set_scale("y")),
    ("axes", "invert_x"): (lambda a: bool(a.xaxis_inverted()), _mk_set_invert("x")),
    ("axes", "invert_y"): (lambda a: bool(a.yaxis_inverted()), _mk_set_invert("y")),
    ("axes", "aspect"): (lambda a: a.get_aspect(), _set_aspect),
    ("axes", "spine_top_color"): _mk_spine_handler(
        "top_color", lambda a, _s="top": spine_side_color(a, _s)
    ),
    ("axes", "spine_top_linewidth"): _mk_spine_handler(
        "top_width", lambda a, _s="top": spine_side_width(a, _s)
    ),
    ("axes", "spine_right_color"): _mk_spine_handler(
        "right_color", lambda a, _s="right": spine_side_color(a, _s)
    ),
    ("axes", "spine_right_linewidth"): _mk_spine_handler(
        "right_width", lambda a, _s="right": spine_side_width(a, _s)
    ),
    ("axes", "spine_bottom_color"): _mk_spine_handler(
        "bottom_color", lambda a, _s="bottom": spine_side_color(a, _s)
    ),
    ("axes", "spine_bottom_linewidth"): _mk_spine_handler(
        "bottom_width", lambda a, _s="bottom": spine_side_width(a, _s)
    ),
    ("axes", "spine_left_color"): _mk_spine_handler(
        "left_color", lambda a, _s="left": spine_side_color(a, _s)
    ),
    ("axes", "spine_left_linewidth"): _mk_spine_handler(
        "left_width", lambda a, _s="left": spine_side_width(a, _s)
    ),
    ("axes", "facecolor"): (lambda a: a.get_facecolor(), lambda a, v: a.set_facecolor(v)),
    ("axes", "grid_x"): (
        lambda a: _grid_visible(a, "x"),
        lambda a, v: a.grid(visible=bool(v), axis="x"),
    ),
    ("axes", "grid_y"): (
        lambda a: _grid_visible(a, "y"),
        lambda a, v: a.grid(visible=bool(v), axis="y"),
    ),
    ("axes", "grid_color"): (
        _grid_prop(lambda g: g.get_color(), "#b0b0b0"),
        lambda a, v: a.tick_params(axis="both", which="both", grid_color=v),
    ),
    ("axes", "grid_linestyle"): (
        _grid_prop(lambda g: g.get_linestyle(), ":"),
        lambda a, v: a.tick_params(axis="both", which="both", grid_linestyle=str(v)),
    ),
    ("axes", "grid_linewidth"): (
        _grid_prop(lambda g: float(g.get_linewidth()), 0.5),
        lambda a, v: a.tick_params(axis="both", which="both", grid_linewidth=float(v)),
    ),
    ("axes", "grid_alpha"): (
        _grid_prop(lambda g: g.get_alpha(), None),
        lambda a, v: a.tick_params(
            axis="both", which="both", grid_alpha=(None if v is None else float(v))
        ),
    ),
    # ---- axes: 刻度线四边开关（issue #92）----
    ("axes", "ticks_bottom"): _mk_tick_side("x", "bottom", 1),
    ("axes", "ticks_top"): _mk_tick_side("x", "top", 2),
    ("axes", "ticks_left"): _mk_tick_side("y", "left", 1),
    ("axes", "ticks_right"): _mk_tick_side("y", "right", 2),
    ("axes", "spine_top"): (_mk_spine_get("top"), _mk_spine_set("top")),
    ("axes", "spine_right"): (_mk_spine_get("right"), _mk_spine_set("right")),
    ("axes", "spine_bottom"): (_mk_spine_get("bottom"), _mk_spine_set("bottom")),
    ("axes", "spine_left"): (_mk_spine_get("left"), _mk_spine_set("left")),
    # 边框颜色 / 线宽走「模型」：一档「全部」+ 四条各自可覆盖，
    # 应用顺序不影响结果（见上方 apply_spine_model 的注释）
    ("axes", "spine_color"): _mk_spine_handler("all_color", spine_all_color),
    ("axes", "spine_linewidth"): _mk_spine_handler("all_width", spine_all_width),
    # ---- axes3d: 视角 / 网格（manifest 只对 3D 轴放出这些字段）----
    ("axes", "elev"): (_view3d_get("elev"), _view3d_set("elev")),
    ("axes", "azim"): (_view3d_get("azim"), _view3d_set("azim")),
    ("axes", "roll"): (_view3d_get("roll"), _view3d_set("roll")),
    ("axes", "grid_visible"): (
        lambda a: bool(getattr(a, "_draw_grid", True)),
        lambda a, v: a.grid(bool(v)),
    ),
    ("axes", "proj_type"): (
        lambda a: str(getattr(a, "_proj_type", "persp")),
        lambda a, v: a.set_proj_type(str(v)),
    ),
    # ---- axes3d: 轴箭头（隐藏原生轴线，按当前投影的盒边画带箭头的轴）----
    ("axes", "axis_arrows"): (_axis_arrows_on, _set_axis_arrows),
    ("axes", "arrow_color"): _mk_arrow_style_handler("color", lambda p, v: p.set_color(v)),
    ("axes", "arrow_width"): _mk_arrow_style_handler(
        "width", lambda p, v: p.set_linewidth(float(v))
    ),
    ("axes", "arrow_head"): _mk_arrow_style_handler(
        "head", lambda p, v: p.set_mutation_scale(float(v))
    ),
    # ---- image: 颜色映射 / 显示 ----
    ("image", "cmap"): (lambda a: a.get_cmap(), lambda a, v: a.set_cmap(v)),
    ("image", "vmin"): (
        lambda a: a.get_clim()[0],
        lambda a, v: a.set_clim(vmin=(None if v is None else float(v))),
    ),
    ("image", "vmax"): (
        lambda a: a.get_clim()[1],
        lambda a, v: a.set_clim(vmax=(None if v is None else float(v))),
    ),
    ("image", "interpolation"): (
        lambda a: a.get_interpolation(),
        lambda a, v: a.set_interpolation(str(v)),
    ),
    ("image", "alpha"): (
        lambda a: a.get_alpha(),
        lambda a, v: a.set_alpha(None if v is None else float(v)),
    ),
    ("image", "origin"): (lambda a: a.origin, _set_image_origin),
    ("image", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    # ---- line: 标签 / 透明度 / 层级 / marker 颜色 ----
    ("line", "label"): (lambda a: str(a.get_label()), lambda a, v: a.set_label(str(v))),
    ("line", "alpha"): (
        lambda a: a.get_alpha(),
        lambda a, v: a.set_alpha(None if v is None else float(v)),
    ),
    ("line", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    # marker 的两个颜色：getter 回**原始设定**（可能是 `'auto'`），不是
    # `get_marker*color()` 解析出来的那个具体色，见 `_get_marker_color`。
    ("line", "markerfacecolor"): (
        _get_marker_color("_markerfacecolor", "get_markerfacecolor"),
        lambda a, v: a.set_markerfacecolor(v),
    ),
    ("line", "markeredgecolor"): (
        _get_marker_color("_markeredgecolor", "get_markeredgecolor"),
        lambda a, v: a.set_markeredgecolor(v),
    ),
    # ---- collection / patch / bar 的通用属性走能力层（见 _install_caps 那一节）；
    # 这里只留族里的**专用**契约 ----
    # ---- 线组 LineCollection（hlines/vlines、stem、eventplot、streamplot）----
    # 它**不**走能力层：对外那套 prop 名（`color` 而不是 edgecolor）是已经
    # 发出去的契约，与 Collection 族并不同名。
    #
    # **只开样式，不开数据**：几条线、落在哪，是脚本的数据，改它该回代码
    # （与 3D 盒内属性、散点数据同一条产品边界）。
    #
    # getter 一律 `.copy()`：`get_color()` / `get_linewidths()` **把内部对象
    # 本身交出来**（实测 `get_color() is get_color()` 为 True，两个 matplotlib
    # 版本一致），存进 `originals` 就是存了一个活引用。**今天还咬不到人**
    # ——`set_color` / `set_linewidth` 是整个替换那个数组而不是原地改，所以
    # 拿未拷贝的旧引用还原，结果目前是对的（也实测过）。但那是 matplotlib 的
    # 实现细节、不是它承诺的契约（那个数组 `flags.writeable` 是 True），而
    # `originals` 存错一次的后果是撤销回不到原样。拷一份的代价是几个浮点数。
    # 与 `_COLLECTION_CAPS` 那一族的 `.copy()` 同一个理由。
    ("linecoll", "color"): (lambda a: a.get_color().copy(), lambda a, v: a.set_color(v)),
    ("linecoll", "linewidth"): (lambda a: a.get_linewidths().copy(), _set_collection_lw),
    ("linecoll", "linestyle"): (_get_linecoll_ls, _set_linecoll_ls),
    ("linecoll", "alpha"): (
        lambda a: a.get_alpha(),
        lambda a, v: a.set_alpha(None if v is None else float(v)),
    ),
    ("linecoll", "zorder"): (lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v))),
    ("linecoll", "visible"): (lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v))),
    # ---- legend: 预设位置 / 标题 / 边框样式 ----
    ("legend", "loc"): (_get_legend_loc, _set_legend_loc_preset),
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
    # ---- ticks: 方向 / 长度 / 线宽 / 数字格式 ----
    ("ticks", "direction"): (
        lambda a: str(getattr(_tick0(a), "_tickdir", "out")),
        lambda a, v: a.tick_params(direction=str(v)),
    ),
    # 长度 / 线宽**按档写**：`length` / `width` 只动主刻度（与 matplotlib
    # `tick_params` 的默认 which="major" 同口径），次刻度另有 `minor_length` /
    # `minor_width`。两档写的是轴上不同的 kw，应用顺序因此不影响结果。
    # 曾经 which="both"：改主刻度长度会把次刻度一起拉长，「主 / 次」的区分
    # 在界面上就此消失（Prompt 16）。
    ("ticks", "length"): (
        lambda a: float(getattr(_tick0(a), "_size", 3.5)),
        lambda a, v: a.tick_params(which="major", length=float(v)),
    ),
    # 刻度是 marker：线宽在 markeredgewidth 上，get_linewidth 是错误口径
    ("ticks", "width"): (
        lambda a: float(_tick0(a).tick1line.get_markeredgewidth()) if _tick0(a) else 0.8,
        _set_tick_width,
    ),
    ("ticks", "minor_length"): (
        lambda a: _minor_tick_prop(a, "size", "size", 2.0),
        lambda a, v: a.tick_params(which="minor", length=float(v)),
    ),
    ("ticks", "minor_width"): (
        lambda a: _minor_tick_prop(a, "width", "width", 0.6),
        lambda a, v: a.tick_params(which="minor", width=float(v)),
    ),
    # ---- ticks: 刻度定位模型（Locator / Formatter）----
    ("ticks", "major_mode"): _mk_tick_model_handler("major_mode", str),
    ("ticks", "major_step"): _mk_tick_model_handler("major_step", float),
    ("ticks", "major_values"): _mk_tick_model_handler("major_values", _num_list),
    ("ticks", "minor_visible"): _mk_tick_model_handler("minor_visible", bool),
    ("ticks", "minor_mode"): _mk_tick_model_handler("minor_mode", str),
    ("ticks", "minor_step"): _mk_tick_model_handler("minor_step", float),
    ("ticks", "format"): _mk_tick_model_handler("format", str),
    ("ticks", "minor_format"): _mk_tick_model_handler("minor_format", str),
    # ---- colorbar（ColorbarProxy 伪元素）----
    ("colorbar", "label"): (
        lambda p: _cb_axis(p).label.get_text(),
        lambda p, v: p.cb.set_label(str(v)),
    ),
    ("colorbar", "cmap"): (
        lambda p: p.cb.mappable.get_cmap(),
        lambda p, v: p.cb.mappable.set_cmap(v),
    ),
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
    lambda g, v: g.container.set_label(str(v)) if g.container is not None else None,
)

for _prop, _pair in [
    ("color", _eb_handler(lambda a: a.get_color(), lambda a, v: a.set_color(v))),
    (
        "linewidth",
        _eb_handler(
            lambda a: a.get_linewidth(), lambda a, v: a.set_linewidth(v), _eb_linewidth_members
        ),
    ),
    (
        "capsize",
        _eb_handler(
            lambda a: float(a.get_markersize()), lambda a, v: a.set_markersize(float(v)), _eb_caps
        ),
    ),
    (
        "cap_thickness",
        _eb_handler(
            lambda a: float(a.get_markeredgewidth()),
            lambda a, v: a.set_markeredgewidth(float(v)),
            _eb_caps,
        ),
    ),
    (
        "alpha",
        _eb_handler(
            lambda a: a.get_alpha(), lambda a, v: a.set_alpha(None if v is None else float(v))
        ),
    ),
    ("visible", _eb_handler(lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v)))),
]:
    HANDLERS[("errorbar", _prop)] = _pair[0]
    _PENDING_RESTORES[("errorbar", _prop)] = _pair[1]


# ---------------------------------------------------------------------------
# stem 系列（StemContainer）：markerline(Line2D) + stemlines(LineCollection)
#   + baseline(Line2D)。三个成员两种类型，但用到的 setter 全是 Artist 或
# 两族的公共 API（set_color / set_linewidth / set_alpha / set_visible），所以
# 复用误差棒那套「统一应用、按成员列表还原」的组合器就够——不需要为它再写
# 一个 handler 家族。
#
# 为什么必须做成容器而不是让成员各自登记：`ax.stem()` 在用户眼里是**一个**
# 数据系列。不消费成员的话 markerline 与 baseline 会变成两条无名「曲线」、
# 而 stemlines 那条 LineCollection 是茎本身——三样东西各改各的，改完还对不齐。
# ---------------------------------------------------------------------------
def _stem_stems(grp):
    return grp.artists["stems"]


def _stem_markers(grp):
    m = grp.artists.get("marker")
    return [m] if m is not None else []


def _stem_marker_get(a):
    return str(a.get_marker())


for _prop, _pair in [
    ("color", _eb_handler(lambda a: a.get_color(), lambda a, v: a.set_color(v))),
    (
        "linewidth",
        _eb_handler(lambda a: a.get_linewidth(), lambda a, v: a.set_linewidth(v), _stem_stems),
    ),
    # 茎是 LineCollection，所以线型必须走**未缩放**规格：`get_linestyle()` 回的
    # 是按线宽缩放过的 dash，`set_linestyle()` 会再缩一遍，每撤销一次疏一档
    # （实测 `ax.stem(..., linefmt="--")` 在默认 lw=1.5 下 5.55 → 8.325 → 12.49）。
    # 判据与理由在 `_get_linecoll_ls`——那一族已经修过，这里是同一个坑的第二个入口。
    ("linestyle", _eb_handler(_get_linecoll_ls, _set_linecoll_ls, _stem_stems)),
    ("marker", _eb_handler(_stem_marker_get, lambda a, v: a.set_marker(str(v)), _stem_markers)),
    (
        "markersize",
        _eb_handler(
            lambda a: float(a.get_markersize()),
            lambda a, v: a.set_markersize(float(v)),
            _stem_markers,
        ),
    ),
    (
        "alpha",
        _eb_handler(
            lambda a: a.get_alpha(), lambda a, v: a.set_alpha(None if v is None else float(v))
        ),
    ),
    ("visible", _eb_handler(lambda a: a.get_visible(), lambda a, v: a.set_visible(bool(v)))),
    ("zorder", _eb_handler(lambda a: float(a.get_zorder()), lambda a, v: a.set_zorder(float(v)))),
]:
    HANDLERS[("stem_series", _prop)] = _pair[0]
    _PENDING_RESTORES[("stem_series", _prop)] = _pair[1]

HANDLERS[("stem_series", "label")] = (
    lambda g: str(g.container.get_label()) if g.container is not None else "",
    lambda g, v: g.container.set_label(str(v)) if g.container is not None else None,
)

# ---- 能力层落地：一族一份实现，注册给对应的 family key ----
# `_install_caps` 用 setdefault，所以上面所有**专用**契约（色条的 label、
# 柱的 bar_width、箭头的端点…）都优先，这一步只补齐族里通用的那些。
_install_caps("collection", _COLLECTION_CAPS)
for _k in ("patch", "bar"):
    _install_caps(_k, _PATCH_CAPS)
_install_caps("artist", _GENERIC_CAPS)

# 3D 轴线 / 背景面板：作用于 x/y/z 三条轴，原值按轴列表还原
for _prop, _g3, _s3 in [
    ("axline_color", lambda ax: ax.line.get_color(), lambda ax, v: ax.line.set_color(v)),
    (
        "axline_width",
        lambda ax: float(ax.line.get_linewidth()),
        lambda ax, v: ax.line.set_linewidth(float(v)),
    ),
    (
        "pane_visible",
        lambda ax: bool(ax.pane.get_visible()),
        lambda ax, v: ax.pane.set_visible(bool(v)),
    ),
    ("pane_color", lambda ax: ax.pane.get_facecolor(), lambda ax, v: ax.pane.set_facecolor(v)),
]:
    _h3, _r3 = _tri_handler(_g3, _s3)
    HANDLERS[("axes", _prop)] = _h3
    _PENDING_RESTORES[("axes", _prop)] = _r3

# 恢复原值时 pos_frac / loc_frac 的原生值需要走原生 setter。
# 标了 `_needs_state` 的 restore 函数额外收一个 state（与 setter 同一约定）。
_RESTORE: dict[tuple[str, str], object] = {
    ("collection", "marker"): _restore_scatter_marker,
    ("arrowpatch", "endpoints_frac"): _restore_arrow_endpoints,
    ("arrowpatch", "arrowstyle"): lambda a, orig: a.set_arrowstyle(orig),
    ("arrowpatch", "linestyle"): lambda a, orig: a.set_linestyle(orig),
    ("text", "pos_frac"): _restore_text_pos,
    ("text", "fontfamily"): _restore_text_fontfamily,
    ("image", "gradient_color"): _restore_image_gradient,
    ("legend", "loc_frac"): _restore_legend_loc,
    ("legend", "loc"): _restore_legend_loc,  # loc 预设的原生值同为 (loc, 锚框)
    ("ticklabel", "text"): _restore_ticklabel_text,
    ("colorbar", "orientation"): _restore_cb_orientation,
    ("colorbar", "extend"): _restore_cb_extend,
    # 还原脚本原样：不过 `_set_axes_position` 的 guard——tight 引擎下还原是无害
    # 的（它本来就要把位置算回去），在 undo 路上抛只会平白多一条 warning
    ("axes", "position"): _restore_axes_position,
    ("figure", "size_mm"): lambda f, v: f.set_size_inches(v[0] / 25.4, v[1] / 25.4, forward=False),
}
for _p in _TICK_MODEL_PROPS:
    _RESTORE[("ticks", _p)] = _mk_tick_model_restore(_p)
for _prop, _key in [
    ("spine_color", "all_color"),
    ("spine_linewidth", "all_width"),
    *[
        (f"spine_{_s}_{_n}", f"{_s}_{_k}")
        for _s in _SPINE_SIDES
        for _n, _k in (("color", "color"), ("linewidth", "width"))
    ],
]:
    _RESTORE[("axes", _prop)] = _mk_spine_restore(_key)
# ---------------------------------------------------------------------------
# 背景框（bbox_*）：六条 prop 写的是**同一个 patch**，而那个 patch 可能是被
# 第一条 override 现建出来的。所以 handler 与 restore 必须成对登记——
# 详见 `_bbox_handler` 的抬头。
# ---------------------------------------------------------------------------
#: prop → (读, 写)。**默认值不在这儿**，在 `BBOX_DEFAULTS`（唯一出处）。
_BBOX_PROPS = {
    "bbox_visible": (lambda p: bool(p.get_visible()), lambda p, v: p.set_visible(bool(v))),
    "bbox_facecolor": (lambda p: p.get_facecolor(), lambda p, v: p.set_facecolor(v)),
    "bbox_edgecolor": (lambda p: p.get_edgecolor(), lambda p, v: p.set_edgecolor(v)),
    "bbox_linewidth": (lambda p: float(p.get_linewidth()), lambda p, v: p.set_linewidth(float(v))),
    "bbox_alpha": (
        lambda p: p.get_alpha(),
        lambda p, v: p.set_alpha(None if v is None else float(v)),
    ),
    "bbox_pad": (lambda p: _boxstyle_info(p)[0], lambda p, v: _boxstyle_set(p, pad=v)),
    "bbox_rounded": (lambda p: _boxstyle_info(p)[1], lambda p, v: _boxstyle_set(p, rounded=v)),
}
for _bp, (_bread, _bwrite) in _BBOX_PROPS.items():
    _bpair, _brestore = _bbox_handler(_bread, _bwrite, BBOX_DEFAULTS[_bp])
    HANDLERS[("text", _bp)] = _bpair
    _RESTORE[("text", _bp)] = _brestore
# `bbox_visible=False` 不该为了「关」而现建一个 patch（本来就没有框时它是
# no-op）。其余五条照旧「首次改任何背景属性即出现背景框」。
HANDLERS[("text", "bbox_visible")] = (HANDLERS[("text", "bbox_visible")][0], _set_bbox_visible)

# ---------------------------------------------------------------------------
# 图例项（legend_text）= 一段文字 + 一个条目。文字那半**逐条镜像** text 的
# handler / restore（同一份实现，不抄第二遍）；条目那半是自己的：示意线
# 样式、绑定、隐藏。`visible` 被条目级的实现盖掉——隐藏一个图例项是把整项
# 从图例盒里拿掉（示意线与文字一起），不是只让文字消失、留一个空行。
# ---------------------------------------------------------------------------
for (_k, _p), _h in list(HANDLERS.items()):
    if _k == "text":
        HANDLERS.setdefault(("legend_text", _p), _h)
for (_k, _p), _r in list(_RESTORE.items()):
    if _k == "text":
        _RESTORE.setdefault(("legend_text", _p), _r)
HANDLERS[("legend_text", "visible")] = (_entry_visible_get, _entry_visible_set)
HANDLERS[("legend_text", "binding")] = (_entry_binding_get, _entry_binding_set)
for _p in LEGEND_ENTRY_STYLE_PROPS:
    HANDLERS[("legend_text", _p)] = _mk_entry_handle_handler(_p)

_RESTORE.update(_PENDING_RESTORES)


def to_hex(color) -> str:
    try:
        return mcolors.to_hex(color)
    except (ValueError, TypeError):
        return "#000000"


# figure 锚定的 prop：setter 在应用那一刻把 figure 分数换算进 artist 的本地
# 坐标（transData / transAxes / legend loc）。几何（图幅、子图 position）随后
# 再变，artist 会跟着几何一起漂走，声明的 figure 位置就不再成立——所以几何
# 一变必须重放它们，否则「写回时看到的」与「重开后重放出来的」不是同一张图
# （FigS3 文字全体错位就是这么来的）。
_FRAC_ANCHORED = {"pos_frac", "loc_frac", "endpoints_frac"}


def _is_geometry_key(prop: str, artist) -> bool:
    """会改变 figure 分数 ↔ 本地坐标换算关系的 prop。"""
    if prop == "size_mm":
        return isinstance(artist, Figure)
    if isinstance(artist, ColorbarProxy) and prop in ("orientation", "extend"):
        # 长短边互换 / 给延伸三角让地方，两者都让色条轴换了一块地方
        return True
    return prop == "position" and isinstance(artist, Axes)


def _must_replay(prop: str, artist) -> bool:
    """每次 apply 都必须重放的 prop（「值没变就跳过」那条捷径对它们是错的）。

    刻度定位与单条刻度文字都是「按当前状态重算」的：改 xlim、换 scale、
    翻转色条方向都会让 matplotlib 把 locator/formatter 或整组刻度换掉，而
    `applied` 表里的值一个字节没变。跳过它们，热会话就停在旧刻度上，与
    全量重放当场分岔（分岔的表现是写回自检 409，或者更糟——没被发现）。
    重放的代价是每次渲染多几次 locator 赋值，与 draw 相比可以忽略。
    """
    if isinstance(artist, TickSet):
        return prop in _TICK_MODEL_PROPS
    return isinstance(artist, TickLabel) and prop == "text"


# ---------------------------------------------------------------------------
# 别名组：广播型 prop 与它会盖掉的窄 prop
#
# 「广播型」= setter 一次写**一组** artist，而那组 artist **本身也是登记元素**
# （各有自己的 gid 和同名 prop）。两者叠加时 `originals` 的快照是顺序相关的：
# 先改整体、再改单条，单条那次记下的「原样」已经是被整体改过的值，撤销就回
# 不到脚本原样。实测五组全中（下表），所以这不是图例特有的毛病，必须有表。
#
# 表要**显式**、单一出处：靠 setter 里顺手记一笔那种隐式做法，下一个人加
# handler 时必然漏掉，而漏掉的症状是「撤销少还原一个」——没有报错。
# ---------------------------------------------------------------------------
def _alias_by_artists(pick, narrow_prop: str):
    """广播端直接写的那批 artist —— 按**对象身份**反查它们的 gid。

    刻意不拼 gid 字符串：`axes_0.legend.texts_j` 这类命名规则一旦变，或者
    某个成员根本没被登记（空文字的图例项就不登记），拼出来的 gid 会指向不
    存在的元素，而症状是撤销时静默少还原一个。身份反查天然只命中真的登记
    过的那些。
    """

    def resolve(state: "FigState", rev: dict, artist) -> list[tuple]:
        try:
            members = pick(artist)
        except Exception:  # noqa: BLE001 — 结构不符就当没有
            return []
        out = []
        for m in members:
            gid = rev.get(id(m))
            if gid is not None:
                out.append((gid, narrow_prop))
        return out

    return resolve
    # 这里**刻意不再筛一遍**「窄端那一族有没有这条 prop」。组里确实会混进解析
    # 不出 handler 的键（`("stem_series","marker")` 的成员含 stemlines，而
    # `("linecoll","marker")` 不存在），但下游两条路径都已经挡住了：采「脚本
    # 原样」那段查不到 handler 就 `continue`，还原那段只走 `state.applied` 里
    # 真的应用过的键——幽灵键一个都不在里面。**试过在这儿加一道过滤，拿掉它
    # 之后没有任何用例变红**（regression proof 跑过），那就是一道空门禁：
    # 读的人会以为它在挡什么，而它什么都没挡。表本身的自洽由
    # `tests/test_invariants_engine.py` 静态核对。


def _alias_colorbar_mappable(narrow_prop: str):
    """色条的 `cmap` / `vmin` / `vmax` 写的是 **`cb.mappable`**——而那个
    mappable 自己也是元素表里的一条（imshow 的 AxesImage、pcolormesh 的
    QuadMesh、scatter(c=z) 的 PathCollection…）。两个 gid 指着同一份状态。

    不把它们连成一组的话（实测，`imshow` + colorbar）：

    * 两条都设过、只撤掉 mappable 那条 → 还原把色图写回脚本原样，色条那条
      「值没变」于是被跳过，**热态回到 viridis、全量重放却是 magma**；
    * 两条**全撤** → 后采的那份 originals 记的是「已经被另一条改过之后」的
      值，于是撤销回到的是**中间态**（实测停在 plasma，回不到 viridis）。
      用户按了撤销、图还是花的，而且再也回不去。

    第二条比第一条更要命，也正是广播端「动手之前先把组员的脚本原样采下来」
    那段逻辑存在的理由。
    """

    def resolve(state: "FigState", rev: dict, artist) -> list[tuple]:
        m = getattr(getattr(artist, "cb", None), "mappable", None)
        if m is None:
            return []
        gid = rev.get(id(m))
        if gid is None:
            # **独立 mappable**（`fig.colorbar(ScalarMappable(...), ax=ax)`）：
            # 它不属于任何 axes，不在元素表里，也**不该**被塞进 `state.index`
            # ——它不是可编辑元素，塞进去会被「不许静默消失」那条正确地抓成
            # 孤儿（试过，用例当场红）。
            #
            # 这里要的只是一个**分组令牌**：同一个 mappable 的两条色条算出同一
            # 个字符串，它们就落进同一个别名组。令牌只活在本次会话的
            # `owner` / `originals` / `alias_seeded` 里，**不进 manifest、不进
            # patch、不跨进程**，所以拿对象身份当键是安全的。
            #
            # 顺带一提：这个窄成员**采不到原样**——`ScalarMappable` 不是
            # Artist，`HANDLERS` 里没有它的 cmap，`state.resolve` 也回 None。
            # 共享原样因此走「对等广播端」那条回退（见 `apply` 里采 originals
            # 那一段）。分组令牌照样是需要的：没有它连组都不成立。
            return [(f"mappable#{id(m):x}", narrow_prop)]
        return [(gid, narrow_prop)]

    return resolve


def _alias_colorbar_ticks(narrow_prop: str):
    """色条的 `tick_*` 写的是 `cb.ax.tick_params(...)`（默认 axis="both"），
    盖掉的是**色条轴自己那两组刻度**的同名 prop。

    刻度组是伪元素（`TickSet`），不在广播端写的 artist 列表里，只能按结构找
    ——这也是别名解析要做成函数而不是静态映射的原因。
    """

    def resolve(state: "FigState", rev: dict, artist) -> list[tuple]:
        cb_ax = getattr(getattr(artist, "cb", None), "ax", None)
        if cb_ax is None:
            return []
        return [
            (el["gid"], narrow_prop)
            for el in state.elements
            if isinstance(el["artist"], TickSet) and el["artist"].ax is cb_ax
        ]

    return resolve


def _alias_same_element(narrow_prop: str):
    """广播端与窄端**在同一个元素上**（同一个 gid，两条 prop）。

    既有的别名组都是「一个 prop 写一批**别的** artist」（图例字号 → 每条图例
    项、色条 cmap → 它的 mappable）。这一类不是——它是「同一个 artist 上，
    A 的 setter 顺手改变了 B 的可读状态」，而 `originals` 又是**按需、在应用
    那一刻**采的：先应用 A 再应用 B，B 采到的「脚本原样」已经被 A 污染了。

    `bbox_*` 那一组是同一个形状（当时用记号 `_mm_bbox_created` 单独解决的）。
    机制其实早就在——广播端「动手之前先把组员的脚本原样采下来」那段逻辑不关心
    组员是不是同一个 artist，反查表 `rev[id(artist)]` 回的正是它自己的 gid。
    缺的只是这张表里的条目。
    """

    def resolve(state: "FigState", rev: dict, artist) -> list[tuple]:
        gid = rev.get(id(artist))
        return [(gid, narrow_prop)] if gid is not None else []

    return resolve


#: 广播端 `(cls_key, prop)` → 解析出「它会盖掉哪些 (窄 gid, 窄 prop)」的函数。
#:
#: **不在表里的，各有各的理由**（往里加之前先确认narrow 端真的是登记元素）：
#:   * `spine_*` 与 `ticks` 的模型类 prop —— 它们是「写进 cfg 再整体重建」的
#:     路数，撤销一条 = 退回未表态，天然没有快照问题；
#:   * `errorbar.*` —— 它写的成员（line / caps / bars）**没有单独登记**，
#:     不存在能和它打架的窄 gid；
#:   * 3D 的 `axline_* / pane_*` —— 同上，x/y/z 轴对象不是登记元素；
#:   * `ticks.*` → `ticklabel.text` —— 刻度文字只暴露 `text` 一个 prop，与
#:     刻度模型不同名，而且它俩的先后已经由 `_RANK_TICK_TEXT` + `_must_replay`
#:     管住了（刻度文字永远最后、且每次重放）。
ALIAS_GROUPS: dict[tuple[str, str], object] = {
    # 图例整体字号 → 每一条图例项的字号
    ("legend", "fontsize"): _alias_by_artists(lambda leg: list(leg.get_texts()), "fontsize"),
    # 图例标题字号 → 图例标题那个 Text（它不在 get_texts() 里，单独一条）
    ("legend", "title_fontsize"): _alias_by_artists(lambda leg: [leg.get_title()], "fontsize"),
    # 色条刻度 → 色条轴上的刻度组（tick_params 默认写 x/y 两条）
    ("colorbar", "tick_fontsize"): _alias_colorbar_ticks("fontsize"),
    ("colorbar", "tick_color"): _alias_colorbar_ticks("color"),
}
# 柱形系列的样式 prop → 每一根柱的同名 prop。`bar_width` 与 `label` 不在此列：
# 前者窄端没有对应 prop（`bar` 不暴露宽度），后者写的是 container 不是柱。
for _bprop in ("facecolor", "edgecolor", "linewidth", "alpha", "visible"):
    ALIAS_GROUPS[("bar_series", _bprop)] = _alias_by_artists(lambda g: list(g.artists), _bprop)
# stem 系列 → 被它消费掉的成员。这里的「窄端」不是界面上的另一个条目，而是
# 那些成员的**旧 gid 别名**（`manifest._alias_consumed_member`）：容器化之前
# markerline 是一条普通曲线（`axes_i.lines_k`）、stemlines 是一条线组
# （`axes_i.linecoll_j`），历史文档里可能有针对它们的 override，两个 gid 落在
# 同一个 artist 上。走别名组的机制，撤销任一侧都会让另一侧重放。
#
for _sprop in ("color", "alpha", "visible", "zorder", "marker", "markersize"):
    ALIAS_GROUPS[("stem_series", _sprop)] = _alias_by_artists(lambda g: g.members(), _sprop)
# `linewidth` / `linestyle` **也是别名组，但窄端只有茎**：它们经 `_stem_stems`
# 只写 stemlines，markerline 一个字节都不碰。而茎自己也有旧 gid 别名
# （`axes_i.linecoll_j`，容器化之前的登记名，见 `manifest._alias_consumed_member`）
# ——所以照样是「两个 gid 一份状态」，别名一加重叠就成立了。
#
# **组员必须与 setter 真正写的那批 artist 逐一对上**，不能图省事写
# `g.members()`：那会把 markerline 也声明成组员，而广播端根本不写它。声明一个
# 不存在的重叠不会当场出错，却会让 `alias_seeded` 替 markerline 采一份没人用
# 的「脚本原样」、并在撤销时把它算进 `dirty_groups` 白重放一轮——别名表是
# 「谁会盖掉谁」的事实表，不是「谁跟谁沾边」。
for _sprop in ("linewidth", "linestyle"):
    ALIAS_GROUPS[("stem_series", _sprop)] = _alias_by_artists(_stem_stems, _sprop)
# 色条 ↔ 它的 mappable：同一份色图与 clim，两个 gid。**这条不是本次新开的
# 重叠**——`("image", "cmap")` 与 `("colorbar", "cmap")` 一直都在同一个
# AxesImage 上；Collection 族开放 cmap/vmin/vmax 只是把它扩到了
# pcolormesh / contour / scatter(c=z)。既然机制在这儿，一起收了。
# 谁在前：mappable 那条排在色条之后（`_rank` 的组内次序），所以两条都设过时
# 图元自己那条说了算——色条是 mappable 的一个视图，不是反过来。
for _cprop in ("cmap", "vmin", "vmax"):
    ALIAS_GROUPS[("colorbar", _cprop)] = _alias_colorbar_mappable(_cprop)

# ---------------------------------------------------------------------------
# **同一个元素上的重叠**（广播端与窄端同 gid）。全部实测过，三条各有各的机制：
#
#   * `fill` → `facecolor`：`Patch.set_fill(False)` 把 `_facecolor` 的 **alpha
#     清零、RGB 留着**。于是 `get_facecolor()` 之后回的是一个 alpha=0 的四元组
#     ——`facecolor` 采到它当原样，撤销之后那个面**永久透明**。
#     **manifest 看不见**：`_fields_for` 经 `to_hex()` 报颜色，而 to_hex 丢掉
#     alpha，前后都读成 `#3366cc`。实测走真 worker：manifest 逐字节相同，
#     画面差 16236 像素（占整帧 5.64%），warnings 为空。这是本轮最安静的一条。
#     邻居都干净（fill→edgecolor / linewidth / alpha 实测均可还原），所以组就
#     是 `{fill → facecolor}` 这一对。
#
#   * `[xy]scale` → `[xy]lim`：`set_yscale("log")` 会重新自动缩放，于是 `ylim`
#     的原样是在坐标范围已经变过之后采的。**两个列表序都坏**——`_apply_rank`
#     把 scale（第 4 档）钉死在 lim（第 6 档）之前，规范顺序把「可能被污染」
#     变成了「必然被污染」。实测 yscale+ylim 撤销后 20 处 manifest 字段不同
#     （含 ylim、刻度值、刻度文字与一串 bbox），xscale+xlim 12 处；**各自单独
#     应用则完全干净**。
#
#   * `invert_[xy]` → `[xy]lim`：翻转把上下限对调，`get_[xy]lim()` 回的是调过
#     的那个元组，回灌它等于**再翻一次**。实测撤销后 `ylim` 从 [-0.2, 2.4]
#     变成 [2.4, -0.2]、`invert_y` 从 False 变成 True，15 处字段不同。
#
# 这三条**写回自检都拦不住**：`app._compare_manifests` 只比几何，颜色差它看不见；
# 而 scale / invert 那两条虽然动几何，热态与重放是**一起**跑偏的，比不出分歧。
# ---------------------------------------------------------------------------
for _fkey in ("patch", "bar"):
    ALIAS_GROUPS[(_fkey, "fill")] = _alias_same_element("facecolor")
for _axis in ("x", "y"):
    ALIAS_GROUPS[("axes", f"{_axis}scale")] = _alias_same_element(f"{_axis}lim")
    ALIAS_GROUPS[("axes", f"invert_{_axis}")] = _alias_same_element(f"{_axis}lim")

#: 广播端 prop 名的集合。`apply` 拿它做**廉价预筛**：不在表里的 patch 连
#: `state.resolve()` 都不用付。随着别名组覆盖到线组、色条 ↔ mappable，这张表
#: 已经收进了 color / alpha / visible / zorder / cmap 这些**很常见**的名字，
#: 预筛能挡掉的比当初少了不少——它挡的是 prop 名，不是元素。真正的花销仍然
#: 由 `_alias_members` 里那句 `ALIAS_GROUPS.get((_cls_key(artist), prop))` 兜住
#: （查不到就立刻回空，反查表也不会被建起来）。
_BROADCAST_PROPS = frozenset(prop for _cls, prop in ALIAS_GROUPS)


#: 应用顺序的**规范档位**。同档内保持列表序（sorted 稳定），跨档必须按这里
#: 的先后，否则同一组 patch 在热会话与全量重放里会落成两张不同的图。
#:
#:   0 图幅       size_mm 一变，所有分数坐标的物理落点全变
#:   1 色条方向   长短轴互换、整块地方换位置，还要重设 box_aspect 基线
#:   2 色条 extend 延伸三角要占地方；必须**在方向之后**，因为方向要拿色条
#:                当前的矩形反解厚度与间距
#:   3 子图落位   axes position
#:   4 刻度类型   set_[xy]scale 会把 locator/formatter 整套换掉，必须先于 6
#:   5 其余       列表序
#:   6 刻度定位   locator / formatter 模型（依赖 4 与 5 的 xlim）
#:   7 刻度文字   冻结整条轴，必须最后（先冻的会被后来的 locator 换掉）
_RANK_FIGURE_SIZE, _RANK_CB_ORIENT, _RANK_CB_EXTEND = 0, 1, 2
_RANK_POSITION, _RANK_SCALE = 3, 4
_RANK_REST, _RANK_TICK_MODEL, _RANK_TICK_TEXT = 5, 6, 7


def _apply_rank(prop: str, artist, gid: str = "") -> int:
    # 刻度文字按 **gid 形状** 归档，不按解出来的对象：排序发生在整轮 apply
    # 之前，而「先改刻度定位、再改新出现的那条刻度」里那条刻度**此刻还不存在**
    # ——按对象归档会把它错排到刻度定位前面，于是永远解不出来
    if prop == "text" and _TICKLABEL_GID.match(gid or ""):
        return _RANK_TICK_TEXT
    if prop == "size_mm" and isinstance(artist, Figure):
        return _RANK_FIGURE_SIZE
    if prop == "orientation" and isinstance(artist, ColorbarProxy):
        return _RANK_CB_ORIENT
    if prop == "extend" and isinstance(artist, ColorbarProxy):
        return _RANK_CB_EXTEND
    if prop == "position" and isinstance(artist, Axes):
        return _RANK_POSITION
    if prop in ("xscale", "yscale") and isinstance(artist, Axes):
        return _RANK_SCALE
    if isinstance(artist, TickSet) and prop in _TICK_MODEL_PROPS:
        return _RANK_TICK_MODEL
    if isinstance(artist, TickLabel) and prop == "text":
        return _RANK_TICK_TEXT
    return _RANK_REST


def apply(state: FigState, patches: list[dict]) -> list[str]:
    """把全量 patch 列表同步到 Figure。返回 warning 列表（孤儿 gid 等）。

    应用顺序是**规范化**的，七个档位见 `_apply_rank` 的表：图幅 → 结构改造
    （色条方向）→ 子图 position → 刻度类型 → 其余（列表序）→ 刻度定位 →
    刻度文字。figure 锚定的 prop 依赖应用那一刻的几何，只有先把几何放到位、
    且几何变过就重放它们，热会话的增量应用才与冷启动的全量重放收敛到同一
    状态——「所见 == 文档重放 == 写回 == 重开」这条链靠它成立。

    **入口守卫**：`ticklabel_memo()` 的前提是「作用域里没有任何东西会改刻度」，
    而那条前提只写在 docstring 里——前提失效时不会有任何信号，表现是记忆表回旧
    的刻度文字、manifest 描述一个已经不存在的状态：不报错、不变红，**只是所见
    不等于所写**。所以这里当场炸：`apply` 落进 `build_manifest` 的作用域是**唯一**
    能让前提失效的改法（改刻度的路全从这里走），把它钉成断言，前提就有人守着，
    而不是靠下一个人记得读那段 docstring。
    """
    if getattr(_ticklabel_memo, "table", None) is not None:
        raise RuntimeError(
            "apply() 跑在 ticklabel_memo() 作用域里——记忆表的前提（作用域内不改刻度）"
            "已经不成立。别放宽这条断言：要在 build_manifest 里改图，先把记忆表关掉。"
        )
    warnings: list[str] = []
    new: dict[tuple, object] = {}
    for p in patches:
        key = (str(p["gid"]), str(p["prop"]))
        new[key] = p["value"]

    geometry_moved = False
    # 结构性 setter 要按「这一次改完之后」的落位算几何，见 FigState.pending。
    # 出口处一定要清掉（下面 try/finally），不然下一次 apply 会拿着上一批的
    # patch 表去算落位
    state.pending = new

    # ---------------- 别名组（见 ALIAS_GROUPS）----------------
    # 反查表按需建：它是 O(元素数) 的，而绝大多数 apply 一个广播型 prop 都
    # 没碰到，不该为它们付这笔钱。
    _rev: dict[int, str] = {}
    _rev_built = False

    def _reverse_index() -> dict:
        nonlocal _rev, _rev_built
        if not _rev_built:
            _rev = {id(el["artist"]): el["gid"] for el in state.elements}
            # **别名 gid 也算组员**。容器消费掉的成员（stem 的 markerline）只在
            # `state.index` 里留了一条旧 gid 别名，元素表里没有它——而别名与
            # 系列指着**同一个 artist**。不算进来的话：历史文档里那条
            # `axes_i.lines_k` 被撤掉时，成员被还原成脚本原样，系列那条值没变
            # 于是走了「跳过」的捷径，结果是**茎还是新颜色、marker 却退回原色**，
            # 而全量重放两者都是新颜色——热态 ≠ 重放，写回自检又只比几何、
            # 看不见颜色，坏状态会直接写进用户的原件。
            # 元素表里已有的 gid 优先（setdefault）：别名是补充，不是改名。
            for _gid, _artist in state.index.items():
                _rev.setdefault(id(_artist), _gid)
            _rev_built = True
        return _rev

    def _alias_members(key: tuple, artist) -> list[tuple]:
        """这个 key 如果是广播型的，它会盖掉哪些窄 key。"""
        if key[1] not in _BROADCAST_PROPS or artist is None:
            return []
        resolver = ALIAS_GROUPS.get((_cls_key(artist), key[1]))
        return resolver(state, _reverse_index(), artist) if resolver else []

    # 窄 key → 盖着它的广播 key。只对这一轮真的会碰到的广播 prop 展开。
    #
    # **值是一个列表，不是一个 key**：同一个窄 key 可以有**多个**广播端。
    # 一个 mappable 交给 `fig.colorbar()` 两次时，两条色条的 cmap 都解析到
    # 同一个 `(mappable, "cmap")`——只存得下最后一个的话，撤掉第一条时另一条
    # 不进 dirty_groups、不重放，热态于是回到脚本原样，而全新重放里第二条
    # 还在生效（实测热态 viridis vs 重放 cividis）。
    owner: dict[tuple, list[tuple]] = {}
    for _bkey in dict.fromkeys(list(new) + list(state.applied)):
        if _bkey[1] not in _BROADCAST_PROPS:
            continue
        for _nkey in _alias_members(_bkey, state.resolve(_bkey[0])):
            owner.setdefault(_nkey, []).append(_bkey)
    #: 这一轮被动过的组（组 id = 广播 key）。组里任何一个成员被应用或还原，
    #: 都会把同组其他成员盖掉，所以整组都要重放——「值没变就跳过」那条捷径
    #: 对它们是错的，与 `_must_replay` 同一个道理。
    dirty_groups: set[tuple] = set()

    # 上次应用、这次不在 → 恢复原值（originals 存的是本地坐标，与几何无关）
    for key in list(state.applied):
        if key in new:
            continue
        artist = state.resolve(key[0])
        orig = state.originals.get(key)
        if artist is not None and key in state.originals:
            ck = _cls_key(artist)
            try:
                restore = _RESTORE.get((ck, key[1]))
                if restore is not None:
                    if getattr(restore, "_needs_state", False):
                        restore(artist, orig, state)
                    else:
                        restore(artist, orig)
                else:
                    setter = HANDLERS[(ck, key[1])][1]
                    if getattr(setter, "_needs_state", False):
                        setter(artist, orig, state)
                    else:
                        setter(artist, orig)
                if _is_geometry_key(key[1], artist):
                    geometry_moved = True
                # 还原一个组员会把同组其他成员一起盖掉（广播还原写的是整组，
                # 窄的还原写的是广播本该管着的那一个）——整组标脏，下面重放。
                if key in owner:
                    dirty_groups.update(owner[key])
                else:
                    _members = _alias_members(key, artist)
                    if _members:
                        dirty_groups.add(key)
                        # **对等的广播端也要标脏**。撤掉的这条把共享的那份状态
                        # 还原成了脚本原样，而另一条色条的 override 还在生效——
                        # 它的值一个字节没变，走「值没变就跳过」的捷径就永远
                        # 不会被重放，热态于是停在脚本原样，而全新重放里它还在
                        # （实测热态 viridis vs 重放 cividis）。
                        for _nk in _members:
                            dirty_groups.update(owner.get(_nk, ()))
            except Exception as exc:  # noqa: BLE001 — 单条失败不拖垮整次渲染
                warnings.append(f"还原失败 {key[0]}.{key[1]}: {exc}")
        state.applied.pop(key)
        state.originals.pop(key, None)
        state.alias_seeded.discard(key)

    # 广播 prop 代采的「脚本原样」：广播自己也退场了就跟着清掉，否则
    # `originals` 会攒下一堆没有 applied 条目、永远没人回收的记录。
    # 广播还在的（只撤了窄的那一条）要留着——用户再点回来时还要用它。
    for _nkey in list(state.alias_seeded):
        if _nkey in state.applied:
            continue
        if any(_b in new for _b in owner.get(_nkey, ())):
            continue
        state.originals.pop(_nkey, None)
        state.alias_seeded.discard(_nkey)

    # 应用新值：七档规范顺序（组内保持列表序，sorted 稳定）
    def _rank(item):
        (gid, prop), _value = item
        # 次序位：组内成员必须排在**它的广播 prop 之后**。同一组 patch 无论
        # 列表序怎么排都得落成同一张图——热会话与全量重放同序是写回自检的
        # 前提。默认 0，不影响任何非别名 prop 的既有相对顺序。
        return (_apply_rank(prop, state.resolve(gid), gid), 1 if (gid, prop) in owner else 0)

    drawn_after_geometry = False
    try:
        for key, value in sorted(new.items(), key=_rank):
            gid, prop = key
            artist = state.resolve(gid)
            if artist is None:
                warnings.append(f"元素不存在（脚本可能已改动）: {gid}")
                continue
            handler = HANDLERS.get((_cls_key(artist), prop))
            if handler is None:
                warnings.append(f"属性不支持: {gid}.{prop}")
                continue
            # 几何组应用完、进入其余 prop 之前强制一次布局：aspect="equal" 的
            # 子图只有 draw 才 apply_aspect，不刷新的话 figure 锚定的换算会拿着
            # 旧 transform 落错位置（AFM 方图上尤其明显）
            if geometry_moved and not drawn_after_geometry and not _is_geometry_key(prop, artist):
                drawn_after_geometry = True
                try:
                    state.fig.draw_without_rendering()
                except Exception:  # noqa: BLE001 — 布局刷新失败不拦渲染
                    pass
            if state.applied.get(key) == value:
                # 值没变也要重放：① figure 锚定的位置（几何动过，本地坐标已失效）；
                # ② 刻度定位与刻度文字（它们按当前状态重算，见 `_must_replay`）；
                # ③ 别名组里被同组其他成员盖掉的（见 ALIAS_GROUPS / dirty_groups）
                if (
                    not (geometry_moved and prop in _FRAC_ANCHORED)
                    and not _must_replay(prop, artist)
                    and key not in dirty_groups
                    and not dirty_groups.intersection(owner.get(key, ()))
                ):
                    continue
            elif _is_geometry_key(prop, artist):
                geometry_moved = True
            getter, setter = handler
            try:
                if key not in state.originals:
                    # **同名的对等广播共用一份「原样」**。两条色条指着同一个
                    # mappable 时，第二条的 cmap 原样是在第一条已经改过之后
                    # 采的——采到的是 plasma 而不是脚本的 viridis，于是「两条
                    # 都撤掉」之后热态停在中间态（实测 plasma vs 重放 viridis），
                    # 而 `_compare_manifests` 只比几何、看不见颜色，写回会
                    # 静默写出与用户所见不同的色图。
                    #
                    # 判据限得很窄：**窄成员的 prop 名与广播自己同名**——那才
                    # 是「两个 gid 指着同一个值」。`fill` → `facecolor` 这类
                    # 改名换型的别名不适用，照旧读实况。
                    _seeded = _NOTHING
                    # 两条路都指向同一件事——「这个值的脚本原样已经有人采过了」：
                    #   ① 窄成员自己被采过（mappable 在元素表里的常规情形）；
                    #   ② 只有**对等的广播端**采过。独立 mappable
                    #      （`fig.colorbar(ScalarMappable(...), ax=ax)`）走的是
                    #      这一条：那个 mappable **不是 Artist**，`HANDLERS` 里
                    #      没有它的 cmap，窄成员根本采不了原样（`alias_seeded`
                    #      为空）。对等广播端的 getter 与自己是同一个，类型天然
                    #      一致。
                    for _nk in _alias_members(key, artist):
                        # **判据不是「同名」**，是「这个窄成员上真的还站着
                        # 另一个对等广播端」。柱系列的 `facecolor` 广播到每根
                        # 柱子也是同名，但那是**容器 → 成员**：每根柱子有自己
                        # 的一份值，共用原样会拿错形状（实测：还原时报
                        # `Invalid RGBA argument: 0.1215…`，等价矩阵的
                        # s8-alias-mixed-reversed 当场红）。
                        # 只有「两个 gid 指着同一个值」才该共用，而那一定表现为
                        # 同一个窄 key 上挂着两个以上同名广播端。
                        if _nk[1] != key[1] or _nk not in state.originals:
                            continue
                        if any(_b != key and _b[1] == key[1] for _b in owner.get(_nk, ())):
                            _seeded = state.originals[_nk]
                            break
                    if _seeded is _NOTHING:
                        for _nk in _alias_members(key, artist):
                            for _b in owner.get(_nk, ()):
                                if _b != key and _b[1] == key[1] and _b in state.originals:
                                    _seeded = state.originals[_b]
                                    break
                            if _seeded is not _NOTHING:
                                break
                    state.originals[key] = getter(artist) if _seeded is _NOTHING else _seeded
                # 广播型 prop：**在自己动手之前**把组内窄 prop 的「脚本原样」
                # 一起采下来。等窄 prop 自己被应用时再采就晚了——那时读到的
                # 已经是被广播改过的值，撤销就回不到原样（这正是本 bug）。
                members = _alias_members(key, artist)
                if members:
                    dirty_groups.add(key)
                    for nkey in members:
                        if nkey in state.originals:
                            continue
                        nart = state.resolve(nkey[0])
                        nh = HANDLERS.get((_cls_key(nart), nkey[1])) if nart is not None else None
                        if nh is None:
                            continue
                        try:
                            state.originals[nkey] = nh[0](nart)
                            state.alias_seeded.add(nkey)
                        except Exception:  # noqa: BLE001 — 采不到就退回旧行为
                            pass
                if getattr(setter, "_needs_state", False):
                    setter(artist, value, state)
                else:
                    setter(artist, value)
                state.applied[key] = value
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"应用失败 {gid}.{prop}: {exc}")
    finally:
        # 下一次 apply 绝不能拿着上一批的 patch 表去算落位
        state.pending = {}

    # 图例项跟随源对象（派生显示，不进 applied）。放在整轮之后：源对象的
    # 改动与还原都已落定，此刻派生出来的才是「这一次改完之后」的样子。
    sync_legends(state)

    return warnings
