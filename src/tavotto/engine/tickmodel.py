"""刻度（tick）模型：伪元素 `TickSet` / `TickLabel`、刻度线四边开关、Locator / Formatter 模型、
单条刻度文字的冻结，以及 `build_manifest` 那张刻度标签记忆表。

2026-09-18 审计任务书 PR D 第二步的第二刀（`docs/architecture/figstate-dependencies.md` 的顺序：
spine → **tick** → colorbar → legend），从 `overrides.py` 按 artist family 切出来。只依赖标准库 +
matplotlib：不 import `overrides`，也不 import `manifest`——`overrides.HANDLERS` 只登记这里导出的
getter / setter（`HANDLERS_*` 三张表按原位置展开进去，顺序一个字节不变），撤销登记走 `RESTORE`，
`manifest` 直接从这里取只读判据与记忆表。正文逐字未改。

与 `overrides.FigState` 的关系是**协议**而不是 import：`_set_ticklabel_text` 收到的 `state` 只用
`.applied`（(gid, prop) → 值）与 `.resolve(gid)` 两样（`EditState`）；`FigState.resolve` 反过来按
`TICKLABEL_GID` 的形状现解出 `TickLabel`。刻度文字的 gid 形状因此只有这一份。
"""

from __future__ import annotations

import contextlib
import re
import threading
from typing import Protocol

import matplotlib as mpl
import matplotlib.ticker as mticker
from matplotlib.axes import Axes
from matplotlib.ticker import FormatStrFormatter, ScalarFormatter

#: 刻度标签的 gid 形状（`overrides.FigState.resolve` 按需现解、`_apply_rank` 按形状归档时用）
TICKLABEL_GID = re.compile(r"^axes_(\d+)\.([xyz])ticklabels_(\d+)$")


class EditState(Protocol):
    """`_set_ticklabel_text` 对 `overrides.FigState` 的全部要求——只这两样，族模块不 import 它。"""

    applied: dict[tuple, object]

    def resolve(self, gid: str): ...


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
        with _keep_projected_ticks(ax, which):
            return list(get(minor=True)) if minor else list(get())
    key = (id(ax), which, minor)
    hit = table.get(key)
    if hit is not None and hit[0] is ax:
        return hit[1]
    with _keep_projected_ticks(ax, which):
        labels = list(get(minor=True)) if minor else list(get())
    table[key] = (ax, labels)
    return labels


def _is_axes3d(ax) -> bool:
    """mplot3d 的 Axes3D——与 `manifest` 同一条判据：有 `zaxis` 的就是。"""
    return getattr(ax, "zaxis", None) is not None


@contextlib.contextmanager
def _keep_projected_ticks(ax: Axes, which: str):
    """3D 轴上跑 `Axis._update_ticks()` 的前后，把刻度的**投影位置**存回去。

    `_update_ticks` 是 2D 轴的常规步骤：`XTick.update_position(loc)` 把刻度线
    与标签写到 x = loc（数据坐标，配 blended transform）。mplot3d 的三条轴都
    继承自 `XAxis`，刻度对象的 transform 却被 `axis3d.get_major_ticks` 换成了
    投影平面的 `transData`，真正的位置由 `axis3d.Axis._draw_ticks` 在 draw 里
    按投影现算再写回。于是 draw 之后任何一次 `_update_ticks`——包括
    `get_[xyz]ticklabels()` 内部那一次——都会把标签拽回 x = loc 这个在投影
    平面上毫无意义的点：z 刻度 −1…1 落成一条横贯整张图的带子，
    `get_window_extent` 量出来的刻度组包围盒起点在图外 2.6 个图幅、宽达
    图幅的 6 倍（审计 T25：选中 Z 刻度，蓝色选区横贯工作区）。

    2D 轴不需要：draw 走的就是同一步，重算是幂等的。这里只存 draw 留下的
    几何（线段端点、标签位置），不碰 `_loc`——那正是 `_update_ticks` 该算的。
    """
    if not _is_axes3d(ax):
        yield
        return
    axis = _axis_of(ax, which)
    ticks = [*axis.get_major_ticks(), *axis.get_minor_ticks()]
    saved = [
        (
            t,
            t.tick1line.get_data(),
            t.tick2line.get_data(),
            t.gridline.get_data(),
            t.label1.get_position(),
            t.label2.get_position(),
        )
        for t in ticks
    ]
    try:
        yield
    finally:
        for t, l1, l2, g, p1, p2 in saved:
            t.tick1line.set_data(*l1)
            t.tick2line.set_data(*l2)
            t.gridline.set_data(*g)
            t.label1.set_position(p1)
            t.label2.set_position(p2)


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
        with _keep_projected_ticks(ax, which):
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


def _set_ticklabel_text(tl: "TickLabel", value, state: EditState) -> None:
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


def _restore_ticklabel_text(tl: "TickLabel", _orig, state: EditState) -> None:
    """撤销一条刻度文字 = 让该轴回到模型态。仍在生效的其它编辑由 apply()
    的最后一档（`_ALWAYS_REPLAY`）重新冻上，所以这里不必也不该逐条补。"""
    apply_tick_model(tl.ax, tl.which)


_restore_ticklabel_text._needs_state = True  # noqa: SLF001


#: `overrides.HANDLERS` 里刻度文字那一段：单条刻度文字 + 刻度组的字号 / 颜色 / 朝向 / 显隐。
#: 按原位置 `**` 展开（`("ticks", "fontfamily")` 属于字体族，仍留在 overrides 里紧随其后）。
HANDLERS_TEXT: dict[tuple[str, str], tuple] = {
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
}

#: axes 的刻度线四边开关（issue #92）——key 在 "axes" 族名下，机制在这里。
HANDLERS_SIDES: dict[tuple[str, str], tuple] = {
    ("axes", "ticks_bottom"): _mk_tick_side("x", "bottom", 1),
    ("axes", "ticks_top"): _mk_tick_side("x", "top", 2),
    ("axes", "ticks_left"): _mk_tick_side("y", "left", 1),
    ("axes", "ticks_right"): _mk_tick_side("y", "right", 2),
}

#: 刻度线的方向 / 长度 / 线宽（主次分档）+ Locator / Formatter 模型那 8 条。
HANDLERS_MARKS: dict[tuple[str, str], tuple] = {
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
}

#: 撤销：单条刻度文字回到模型态；模型 prop 退回未表态（`_mk_tick_model_restore`）。
RESTORE: dict[tuple[str, str], object] = {
    ("ticklabel", "text"): _restore_ticklabel_text,
    **{("ticks", _p): _mk_tick_model_restore(_p) for _p in _TICK_MODEL_PROPS},
}
