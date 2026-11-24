"""把一组 override 翻译成脚本里的一段代码，生成「修改后的脚本」的新字节（ADR 0171）。

导出对话框勾「修改后的 .py」时用：原脚本一个字节不动，产物是导出目录里的一份新文件。
形态是 ADR 0094 §二 的方案 A（savefig 钩子块）：顶部 import 段之后插**一段**带标记的代码，
脚本调用 `savefig` 的那一刻把调整作用到对应的图上。只用 matplotlib 公开 API 与标准库，
生成的脚本不 import Tavotto，在终端里 `python fig.py` 就能跑。

**这里只生成，不判断对不对**：等不等价一律由调用方用写回同一把尺（全量重放 + 几何 + 像素）裁决
（`app._produce_edited_script`），翻译错了的宁可不交付。不在 `WRITES` 里的 override 不猜，
进 `skipped`，由 verify 证明「新脚本 + 这些没写进去的 override == 用户看到的图」。

纯标准库：Flask 父进程 import（不许碰 matplotlib）。
"""

from __future__ import annotations

import ast
import codecs
import io
import math
import re
import tokenize
from dataclasses import dataclass, field

from . import brand

BEGIN = f"# >>> {brand.PRODUCT_NAME} adjustments >>>"
END = f"# <<< {brand.PRODUCT_NAME} adjustments <<<"


class ScriptAdjustError(ValueError):
    """这份脚本生成不了（`code` 是稳定码）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Unsupported(Exception):
    """这一条 override 不写进脚本（进 `skipped`）。"""


# -------------------------- 哪些 override 写得进脚本 --------------------------
#: manifest 的 role → `overrides._cls_key` 的键。`WRITES` / `NOT_WRITABLE` 都按后者记，
#: 这样「`HANDLERS` 的每个键都表过态」才是一句能断言的话（`tests/test_script_adjust*.py`）。
_ROLE_CLS = {
    "title": "text",
    "axis_label": "text",
    "text": "text",
    "legend_text": "legend_text",
    "line": "line",
    "legend": "legend",
    "axes": "axes",
    "axes3d": "axes",  # 3D 轴的字段在 manifest 里自成一族，写得进的只有 grid_visible
    "colorbar": "colorbar",
    "arrow_patch": "arrowpatch",  # 独立箭头与 annotate 的 arrow_patch 同一族
    "artist": "artist",  # `ax.add_artist` 放进来的（AnchoredText……）与表格
    "ticks": "ticks",
    "ticklabel": "ticklabel",
    "figure": "figure",
    "scatter": "collection",
    "fill": "collection",
    "collection": "collection",
    "linecoll": "linecoll",
    "bar_series": "bar_series",
    "bar": "bar",
    "errorbar": "errorbar",
    "stem_series": "stem_series",
    "patch": "patch",
    "image": "image",
}


_TEXT = {
    "text": "{o}.set_text({v})",
    "fontsize": "{o}.set_fontsize({v})",
    "color": "{o}.set_color({v})",
    "weight": "{o}.set_fontweight({v})",
    "style": "{o}.set_fontstyle({v})",
    "rotation": "{o}.set_rotation({v})",
    "alpha": "{o}.set_alpha({v})",
    "visible": "{o}.set_visible({v})",
    "ha": "{o}.set_ha({v})",
    "va": "{o}.set_va({v})",
    "zorder": "{o}.set_zorder({v})",
}
#: 文字的行距、描边与底框。`legend_text` 一样有（图例项的文字也是 Text），`labelpad` 只有 3D 轴标签。
#: 描边三条合成一条 `_stroke(...)`（见 `_stroke`），底框每条一句 `_bbox(...)`，都在 `_statement` 里生成。
_TEXT_LOOK = {
    "linespacing": "{o}.set_linespacing({v})",
    "stroke_enabled": None,
    "stroke_color": None,
    "stroke_width": None,
    **{f"bbox_{p}": None for p in ("visible", "facecolor", "edgecolor", "linewidth", "alpha")},
    "bbox_pad": None,
    "bbox_rounded": None,
}
_SHAPE = {
    "facecolor": "{o}.set_facecolor({v})",
    "edgecolor": "{o}.set_edgecolor({v})",
    "linewidth": "{o}.set_linewidth({v})",
    "alpha": "{o}.set_alpha({v})",
    "visible": "{o}.set_visible({v})",
    "zorder": "{o}.set_zorder({v})",
}
#: 集合与柱组的图例色块跟着对象一起变（Tavotto 里改它们时图例就是这样）。
_KEYED = {
    p: f"_with_legend({{o}}, lambda _a: _a.set_{p}({{v}}))"
    for p in ("facecolor", "edgecolor", "linewidth", "alpha")
}
_SIDES = ("top", "right", "bottom", "left")
#: 广播键（一条 override 同时写好几个对象）：引擎里「自己的设定 > 广播」（`overrides._apply` 的排序：被广播盖着的窄键
#: 排在广播之后），所以脚本里广播先落（`_rank`）；广播写不进脚本留作重放时，被它盖住的窄条目也要一起留下
#: （`_translate`），否则重放的广播会盖掉写进脚本的窄条目。`ALIAS_GROUPS` 只在引擎里，这里自己记这张表。
_BROADCAST_FIRST = ("spine_color", "spine_linewidth", "tick_fontsize", "tick_color")
#: 色条 vmin / vmax 的语句（写 norm）；图像自己的 vmin / vmax 是窄键。
_CLIM = ("vmin", "vmax")
_CB_TICK_NARROW = {"tick_fontsize": "fontsize", "tick_color": "color"}


def _narrower(r: dict, els: dict) -> set[tuple[str, str]]:
    """广播 patch `r` 盖住的窄键 `{(gid, prop)}`（引擎 `ALIAS_GROUPS` 的同一张表）。"""
    prop, gid = r["prop"], r["gid"]
    if prop in ("spine_color", "spine_linewidth"):  # 「全部」档 → 四边各自
        return {(gid, f"spine_{s}_{prop[6:]}") for s in _SIDES}
    if prop in _CB_TICK_NARROW and gid.endswith(".colorbar"):  # 色条刻度 → 色条轴上的刻度组
        ax = gid[: -len(".colorbar")]
        return {(f"{ax}.{w}ticks", _CB_TICK_NARROW[prop]) for w in "xy"}
    if prop in _CLIM and (m := (els.get(gid) or {}).get("mappable_gid")):  # 色条 → 它的 mappable
        return {(m, prop)}
    return set()


#: 柱系列的广播键：一次写系列里每一根柱，与单根柱（窄条目）同名同序（引擎 `ALIAS_GROUPS` 的 `bar_series` 一族，
#: 窄的盖广播）。`label` / `bar_width` 不在其中：窄端没有对应的键。
_BAR_BROADCAST = (
    "facecolor",
    "edgecolor",
    "linewidth",
    "hatch",
    "hatch_linewidth",
    "hatchcolor",
    "alpha",
    "visible",
)
#: 构建期参数：改了要整份重排图例（引擎 `legendmodel.rebuild_legend` 的那一组）。脚本里合成**一次**
#: `ax.legend(...)`（`_legend_rebuild`），同一个图例上几条一起改也只重建一回。
#: 条目顺序与条目显隐同是重建型，但不写（见 `NOT_WRITABLE`）。
_LEGEND_REBUILD = (
    "fontsize",
    "ncol",
    "borderpad",
    "labelspacing",
    "handlelength",
    "handletextpad",
    "columnspacing",
)
#: 图例条目的示意线样式（引擎 `legendmodel._handle_write`）。落下即脱开跟随源对象。
_HANDLE_PROPS = (
    "handle_color",
    "handle_linestyle",
    "handle_linewidth",
    "handle_marker",
    "handle_markersize",
)
#: 刻度的定位与格式（引擎 `tickmodel` 的 Locator / Formatter 模型）：「没表态 = 脚本原样」，
#: 所以同一条轴上的这几条合成**一次** `_tick_model`，只带用户改过的那几条。
#: 改了整页或坐标轴大小的几条，一律不写进脚本（`NOT_WRITABLE`），由引擎在脚本跑完之后重放——引擎里它们排在刻度之前
#: （`_apply_rank` 的 0 / 1 / 2 / 3 档）。自动刻度的个数跟轴有多长、字有多大有关，所以只要其中一条留作重放，
#: 靠「落笔那一刻的刻度」算出来的那几条（`_snapshots_ticks`）也留给重放，免得两边在不同的轴长上各算各的。
_RESHAPES_AXES = ("size_mm", "frame", "position", "orientation", "extend")
#: 值是 figure 分数、按落笔那一刻的几何换算成数据 / 轴坐标的几条（文字 / 标题 / 轴标签 / 图例拖动，箭头端点）：
#: 几何键留作重放时它们也留给重放（`_translate` 的 `places_by_geometry`），否则重放再改几何，点就离开用户放的位置
_PLACED_BY_FRACTION = ("pos_frac", "loc_frac", "endpoints_frac")
#: 引擎里和 `_PLACED_BY_FRACTION` 同在 `_RANK_REST` 档、会改变数据坐标 ↔ 屏幕换算的键（`_apply_rank` 里没有单列的）
_MOVES_COORDS = ("xlim", "ylim", "invert_x", "invert_y")
_TICK_MODEL = (
    "major_mode",
    "major_step",
    "major_values",
    "minor_visible",
    "minor_mode",
    "minor_step",
    "format",
    "minor_format",
)

#: (cls 键, prop) → 一条公开 API 语句。`{o}` 对象、`{v}` 值、`{x}` 值的元素。
#: 走专用生成（位置、图例字号）的几个 prop 也在表里占一格，模板写 `None`。
WRITES: dict[tuple[str, str], str | None] = {
    **{("text", p): t for p, t in _TEXT.items()},
    **{("legend_text", p): t for p, t in _TEXT.items() if p != "visible"},
    **{("text", p): t for p, t in _TEXT_LOOK.items()},
    **{("legend_text", p): t for p, t in _TEXT_LOOK.items()},
    # 3D 轴标签沿投影轴推远 / 拉近（2D 的轴标签 manifest 不发这条）
    ("text", "labelpad"): "{o}.axis.labelpad = {v}",
    ("text", "pos_frac"): None,
    # 图内副本（ADR 0207）：创建型语句，`_copy_statement` 生成；排在全部属性语句之前（`_rank` 的 "copy" 档）
    ("text", "copy_of"): None,
    ("text", "fontfamily"): None,
    ("legend_text", "fontfamily"): None,
    ("ticks", "fontfamily"): None,
    ("line", "color"): "_with_legend({o}, lambda _a: _a.set_color({v}))",
    ("line", "linewidth"): "_with_legend({o}, lambda _a: _a.set_linewidth({v}))",
    ("line", "linestyle"): "_with_legend({o}, lambda _a: _a.set_linestyle({v}))",
    ("line", "marker"): "_with_legend({o}, lambda _a: _a.set_marker({v}))",
    ("line", "markersize"): "_with_legend({o}, lambda _a: _a.set_markersize({v}))",
    ("line", "markerfacecolor"): "_with_legend({o}, lambda _a: _a.set_markerfacecolor({v}))",
    ("line", "markeredgecolor"): "_with_legend({o}, lambda _a: _a.set_markeredgecolor({v}))",
    ("line", "alpha"): "_with_legend({o}, lambda _a: _a.set_alpha({v}))",
    ("line", "visible"): "{o}.set_visible({v})",
    ("line", "zorder"): "{o}.set_zorder({v})",
    ("line", "label"): "{o}.set_label({v})",
    ("legend", "visible"): "{o}.set_visible({v})",
    ("legend", "frameon"): "{o}.set_frame_on({v})",
    ("legend", "loc"): "{o}.set_loc({v})",
    ("legend", "loc_frac"): None,
    ("legend", "fontsize"): None,
    ("legend", "facecolor"): "{o}.get_frame().set_facecolor({v})",
    ("legend", "edgecolor"): "{o}.get_frame().set_edgecolor({v})",
    ("legend", "framealpha"): "{o}.get_frame().set_alpha({v})",
    ("legend", "frame_linewidth"): "{o}.get_frame().set_linewidth({v})",
    ("legend", "frame_rounded"): "_frame_rounded({o}, {v})",
    ("legend", "title"): "{o}.set_title({v})",
    ("legend", "title_fontsize"): "{o}.get_title().set_fontsize({v})",
    ("legend", "loc_anchor"): "_legend_anchor({o}, {v})",
    **{("legend", p): None for p in _LEGEND_REBUILD if p != "fontsize"},
    ("legend_text", "binding"): None,
    **{("legend_text", p): None for p in _HANDLE_PROPS},
    # 图内箭头：样式与引擎同一句公开 API；端点落在**注释本身**的锚点上（`_ann_to`），独立箭头 `set_positions`
    ("arrowpatch", "color"): "{o}.set_color({v})",
    ("arrowpatch", "linewidth"): "{o}.set_linewidth({v})",
    ("arrowpatch", "mutation_scale"): "{o}.set_mutation_scale({v})",
    ("arrowpatch", "alpha"): "{o}.set_alpha({v})",
    ("arrowpatch", "visible"): "{o}.set_visible({v})",
    ("arrowpatch", "zorder"): "{o}.set_zorder({v})",
    ("arrowpatch", "arrowstyle"): "{o}.set_arrowstyle({v})",
    ("arrowpatch", "linestyle"): "_arrow_dashes({o}, {v})",
    ("arrowpatch", "endpoints_frac"): None,
    ("artist", "visible"): "{o}.set_visible({v})",
    ("artist", "zorder"): "{o}.set_zorder({v})",
    ("axes", "xlim"): "{o}.set_xlim({x})",
    ("axes", "ylim"): "{o}.set_ylim({x})",
    ("axes", "xscale"): "{o}.set_xscale({v})",
    ("axes", "yscale"): "{o}.set_yscale({v})",
    ("axes", "invert_x"): "{o}.xaxis.set_inverted({v})",
    ("axes", "invert_y"): "{o}.yaxis.set_inverted({v})",
    ("axes", "facecolor"): "{o}.set_facecolor({v})",
    ("axes", "grid_x"): "{o}.grid(visible={v}, axis='x')",
    ("axes", "grid_y"): "{o}.grid(visible={v}, axis='y')",
    # 网格样式：引擎同样是 `tick_params(axis="both", which="both", grid_*=...)`，网格没开时也先存着，
    # 之后 `grid_x` / `grid_y` 打开就是这个样子（这两条互不覆盖，先后无关）
    ("axes", "grid_color"): "{o}.tick_params(axis='both', which='both', grid_color={v})",
    ("axes", "grid_linestyle"): "{o}.tick_params(axis='both', which='both', grid_linestyle={v})",
    ("axes", "grid_linewidth"): "{o}.tick_params(axis='both', which='both', grid_linewidth={v})",
    ("axes", "grid_alpha"): "{o}.tick_params(axis='both', which='both', grid_alpha={v})",
    # 刻度线四边开关（引擎界面形态 = which="both" 两档一起）
    ("axes", "ticks_bottom"): "{o}.tick_params(axis='x', which='both', bottom={v})",
    ("axes", "ticks_top"): "{o}.tick_params(axis='x', which='both', top={v})",
    ("axes", "ticks_left"): "{o}.tick_params(axis='y', which='both', left={v})",
    ("axes", "ticks_right"): "{o}.tick_params(axis='y', which='both', right={v})",
    # 3D 轴的网格总开关（2D 轴 manifest 不发这条）
    ("axes", "grid_visible"): "{o}.grid({v})",
    # 「全部」档：作用于每一条 spine，必须排在四边各自的语句之前（`_rank`、`_narrower`）
    ("axes", "spine_color"): "[_s.set_edgecolor({v}) for _s in {o}.spines.values()]",
    ("axes", "spine_linewidth"): "[_s.set_linewidth({v}) for _s in {o}.spines.values()]",
    **{("axes", f"spine_{s}"): f"{{o}}.spines[{s!r}].set_visible({{v}})" for s in _SIDES},
    **{("axes", f"spine_{s}_color"): f"{{o}}.spines[{s!r}].set_edgecolor({{v}})" for s in _SIDES},
    **{
        ("axes", f"spine_{s}_linewidth"): f"{{o}}.spines[{s!r}].set_linewidth({{v}})"
        for s in _SIDES
    },
    ("ticks", "fontsize"): "{o}.params(labelsize={v})",
    ("ticks", "color"): "{o}.params(labelcolor={v})",
    ("ticks", "rotation"): "{o}.params(labelrotation={v})",
    ("ticks", "direction"): "{o}.params(direction={v})",
    ("ticks", "length"): "{o}.params(which='major', length={v})",
    ("ticks", "width"): "{o}.params(which='major', width={v})",
    ("ticks", "minor_length"): "{o}.params(which='minor', length={v})",
    ("ticks", "minor_width"): "{o}.params(which='minor', width={v})",
    ("ticks", "visible"): None,
    **{("ticks", p): None for p in _TICK_MODEL},
    ("ticklabel", "text"): None,
    ("figure", "facecolor"): "{o}.set_facecolor({v})",
    **{("collection", p): t for p, t in _SHAPE.items()},
    **{("collection", p): t for p, t in _KEYED.items()},
    ("collection", "label"): "{o}.set_label({v})",
    ("collection", "linestyle"): "_with_legend({o}, lambda _a: _a.set_linestyle({v}))",
    ("collection", "hatch"): "_with_legend({o}, lambda _a: _a.set_hatch({v}))",
    ("collection", "size"): "{o}.set_sizes([{v}])",
    ("collection", "marker"): "_scatter_marker({o}, {v})",
    ("collection", "cmap"): "{o}.set_cmap({v})",
    ("collection", "vmin"): "{o}.set_clim(vmin={v})",
    ("collection", "vmax"): "{o}.set_clim(vmax={v})",
    ("linecoll", "color"): "{o}.set_color({v})",
    ("linecoll", "linewidth"): "{o}.set_linewidth({v})",
    ("linecoll", "linestyle"): "{o}.set_linestyle({v})",
    ("linecoll", "alpha"): "{o}.set_alpha({v})",
    ("linecoll", "visible"): "{o}.set_visible({v})",
    ("linecoll", "zorder"): "{o}.set_zorder({v})",
    **{("bar_series", p): t for p, t in _SHAPE.items()},
    **{("bar_series", p): t for p, t in _KEYED.items()},
    ("bar_series", "hatch"): "_with_legend({o}, lambda _a: _a.set_hatch({v}))",
    ("bar_series", "hatch_linewidth"): "_with_legend({o}, lambda _a: _a.set_hatch_linewidth({v}))",
    ("bar_series", "hatchcolor"): "_with_legend({o}, lambda _a: _a.set_hatchcolor({v}))",
    ("bar_series", "bar_width"): "{o}.set_bar_width({v})",
    ("bar_series", "label"): "{o}.set_label({v})",
    # 单根柱：窄条目，盖住它所在系列的同名广播键
    **{("bar", p): _SHAPE[p] for p in ("visible",)},
    **{("bar", p): t for p, t in _KEYED.items()},
    **{
        ("bar", p): f"_with_legend({{o}}, lambda _a: _a.set_{p}({{v}}))"
        for p in ("hatch", "hatch_linewidth", "hatchcolor")
    },
    # 误差棒 / 茎叶是容器：一条 override 落到它的每个成员上（成员与引擎 `SeriesGroup.members()` 同一批）
    **{
        ("errorbar", p): f"{{o}}.set_{p}({{v}})"
        for p in ("color", "linewidth", "capsize", "cap_thickness", "alpha", "visible")
    },
    **{
        ("stem_series", p): f"{{o}}.set_{p}({{v}})"
        for p in (
            "color",
            "linewidth",
            "linestyle",
            "marker",
            "markersize",
            "alpha",
            "visible",
            "zorder",
            "label",
        )
    },
    **{("patch", p): t for p, t in _SHAPE.items()},
    ("patch", "linestyle"): "{o}.set_linestyle({v})",
    ("patch", "hatch"): "{o}.set_hatch({v})",
    ("patch", "hatch_linewidth"): "{o}.set_hatch_linewidth({v})",
    ("patch", "hatchcolor"): "{o}.set_hatchcolor({v})",
    ("patch", "fill"): "{o}.set_fill({v})",
    ("image", "cmap"): "{o}.set_cmap({v})",
    ("image", "vmin"): "{o}.norm.vmin = {v}",
    ("image", "vmax"): "{o}.norm.vmax = {v}",
    ("image", "alpha"): "{o}.set_alpha({v})",
    ("image", "visible"): "{o}.set_visible({v})",
    ("image", "interpolation"): "{o}.set_interpolation({v})",
    ("image", "zorder"): "{o}.set_zorder({v})",
    # 色条。`{o}` 是色条轴（`axes_i.colorbar` 的 i 就是它在 `fig.axes` 里的序号），一律公开 API：
    # 刻度 / 轮廓 / 显隐落在色条轴上（引擎走的 `cb.ax.tick_params`、`cb.outline`、`cb.ax.set_visible`，
    # `cb.outline` 就是 `ax.spines["outline"]`）；标签看脚本原样的方向落在长轴上（`_statement`）；
    # cmap / vmin / vmax 与 mappable 是同一份状态，写在 mappable 上（`_statement` 把 `{o}` 换成它）。
    ("colorbar", "label"): None,
    ("colorbar", "cmap"): "{o}.set_cmap({v})",
    ("colorbar", "vmin"): "{o}.set_clim(vmin={v})",
    ("colorbar", "vmax"): "{o}.set_clim(vmax={v})",
    ("colorbar", "tick_fontsize"): "{o}.tick_params(labelsize={v})",
    ("colorbar", "tick_color"): "{o}.tick_params(labelcolor={v})",
    ("colorbar", "outline_visible"): "{o}.spines['outline'].set_visible({v})",
    ("colorbar", "outline_width"): "{o}.spines['outline'].set_linewidth({v})",
    ("colorbar", "visible"): "{o}.set_visible({v})",
}

#: 这一版不写的键（类 → 空格分隔的 prop）。留在表里而不是「不在 WRITES 就算了」：`HANDLERS` 新增
#: 键忘了表态时用例会红。其中色条 `orientation` / `extend`、3D `axis_arrows` 一族只能靠私有 API，
#: 永远不写（ADR 0094 Q10）；其余是公开 API 走得通、但这一版没逐键验证等价。
NOT_WRITABLE: dict[str, str] = {
    # 条目顺序 / 条目显隐：脚本重建出来的图例按**显示顺序**编号（第几项就是 texts_几），引擎的条目
    # 模型按脚本原样的序号认、隐藏的项留在编号里。两边认的不是同一项，验证逐项比对必然对不上。
    "legend": "entry_order",
    # `visible`（条目级的隐藏整项）与 `pos_frac` 沿用 ① 的结论，不在文字描边与底框这一族里；
    # `labelpad` 只有 3D 轴标签有，图例项的文字从来不发这条，写进表里也永远采不到、验不了
    "legend_text": "visible pos_frac labelpad",
    "patch": "pos_frac size_frac",
    # artist = `ax.add_artist` 放进来的锚定框 / 插框（AnchoredText、比例尺……）与表格。显隐、层次写得进；
    # 位置要改锚框的 loc / bbox_to_anchor / xybox，各类各一套（引擎 `_place_offsetbox`），不写
    "artist": "pos_frac",
    "line": "pos_frac points_frac",
    "axes": "visible position aspect elev azim roll "
    "proj_type axis_arrows arrow_color arrow_width arrow_head axline_color "
    "axline_width pane_visible pane_color",
    # `pos_frac`（transAxes 位图的平移）与线 / 形状同一结论：落位要改 transform，不写
    "image": "gradient_color origin pos_frac",
    "figure": "size_mm frame transparent",
    # 方向 / 延伸三角只能靠私有 API（`cb._draw_all()`、`_inside`、重算落位），永远不写（ADR 0094 Q10）：
    # 留作 override，由 Tavotto 在重放时作用。
    "colorbar": "orientation extend",
    # 单根柱的 `linestyle` / `fill` / `zorder`：引擎有 handler，但界面（manifest `_bar_fields`）从不发这三个键，
    # 一条 override 里不会出现它们；没有可采样的字段就验证不了等价，不写
    "bar": "linestyle fill zorder",
    # 渐变色带的两端色：要认出「一摞逐条一色的长条」、保留深浅节奏重新配色并带着图例色块，不是一句公开 API
    "collection": "gradient pos_frac",
    "linecoll": "pos_frac",
}


# -------------------------------- gid → 表达式 --------------------------------
_AX = re.compile(r"^axes_(\d+)(?:\.(.+))?$")
#: 图内副本（ADR 0207）：`overrides.COPY_PROP` / `overrides._COPY_GID` 的镜像（对账见 `tests/test_script_adjust_copy.py`）
COPY_PROP = "copy_of"
_COPY_GID = re.compile(r"^(axes_\d+|fig)\.copy_[a-z0-9]{8}$")
#: 第一期能当副本源的 gid 形状（`overrides._copy_source_supported` 的镜像）
_COPY_SRC_AX = r"\.(?:title|title_left|title_right|xlabel|ylabel|texts_\d+)"
#: 色条的 mappable 还可能是 QuadMesh / 等值线这类通用集合（`collections_N`，N 是 `ax.collections` 的下标）；
#: 这一族自己的字段不写，所以只有认 mappable 时才用这个前缀（`_mappable`）。
_MAPPABLE_ONLY = {"collections": "{ax}.collections"}
_INDEXED = {
    "texts": "{ax}.texts",
    "images": "{ax}.images",
    "patches": "{ax}.patches",
    "scatter": "{ax}.collections",
    "fill": "{ax}.collections",
    "collection": "{ax}.collections",
    "linecoll": "{ax}.collections",
    "arrows": "{ax}.patches",  # 独立箭头（`ax.add_patch(FancyArrowPatch)`）
    "artists": "{ax}.artists",
    "tables": "{ax}.tables",
}


def _script_label(el: dict | None) -> str | None:
    """元素的脚本 label（ADR 0083 的同一条身份）：有显式 label 按 label 认，没有按位置。"""
    for f in (el or {}).get("editable", []):
        if f.get("prop") == "label":
            lab = str(f.get("value_original", f.get("value")) or "")
            return lab if lab and not lab.startswith("_") else None
    return None


def _selector(gid: str, el: dict | None) -> str:
    if gid == "figure":
        return "fig"
    if _COPY_GID.match(gid):  # 副本：创建语句（`_copy_of`）把它存在图上，后续修改按 gid 取回
        return f"_copy_ref(fig, {gid!r})"
    m = _AX.match(gid)
    if m is None:
        raise _Unsupported(gid)
    ax, rest = f"_ax(fig, {int(m.group(1))})", m.group(2)
    if rest is None:
        return ax
    if rest in ("xlabel", "ylabel", "zlabel"):
        return f"_AxisLabel({ax}, {rest[0]!r})"
    if rest == "title":
        return f"{ax}.title"
    if rest in ("xticks", "yticks"):
        return f"_TickSet({ax}, {rest[0]!r})"
    if mm := re.fullmatch(r"([xy])ticklabels_(\d+)", rest):
        return f"_TickSet({ax}, {mm.group(1)!r})"
    if rest == "legend":
        return f"_legend({ax})"
    if rest == "colorbar":
        return ax  # 色条轴本身
    if mm := re.fullmatch(r"texts_(\d+)\.arrow", rest):  # annotate 的箭头，从注释本身取
        return f"_at({ax}.texts, {int(mm.group(1))}).arrow_patch"
    if mm := re.fullmatch(r"legend\.texts_(\d+)", rest):
        return f"_at(_legend({ax}).get_texts(), {int(mm.group(1))})"
    if mm := re.fullmatch(r"lines_(\d+)", rest):
        return f"_line({ax}, {int(mm.group(1))}, {_script_label(el)!r})"
    if mm := re.fullmatch(r"barseries_(\d+)", rest):
        return f"_bars({ax}, {int(mm.group(1))})"
    if mm := re.fullmatch(r"barseries_(\d+)\.bar_(\d+)", rest):
        return f"_bar({ax}, {int(mm.group(1))}, {int(mm.group(2))})"
    if mm := re.fullmatch(r"errorbar_(\d+)", rest):
        return f"_Err({ax}, _at({ax}.containers, {int(mm.group(1))}))"
    if mm := re.fullmatch(r"stemseries_(\d+)", rest):
        return f"_Stem({ax}, _at({ax}.containers, {int(mm.group(1))}))"
    if (mm := re.fullmatch(r"([a-z]+)_(\d+)", rest)) and mm.group(1) in _INDEXED:
        return f"_at({_INDEXED[mm.group(1)].format(ax=ax)}, {int(mm.group(2))})"
    raise _Unsupported(gid)


def _mappable(els: dict, gid: str, prop: str) -> str:
    """色条的 mappable（`mappable_gid`）→ 脚本里取到它的表达式。

    没有 `mappable_gid` 的（独立 `ScalarMappable`：不画在任何地方，公开 API 够不着它），
    以及 `mappable_gid` 指的是反解位图（那条色条上色的其实是另一个对象）的，都写不了。
    判据：真在映射的图元自己也发这个 prop（cmap / vmin / vmax），反解位图是 RGB、没有这几个字段。
    """
    el = els.get(gid)
    m = (el or {}).get("mappable_gid")
    if not m or (mm := _AX.match(m)) is None or mm.group(2) is None:
        raise _Unsupported(gid)
    rest = mm.group(2)
    kind = re.fullmatch(r"([a-z]+)_(\d+)", rest)
    table = {**_INDEXED, **_MAPPABLE_ONLY}
    if kind is None or kind.group(1) not in ("images", "scatter", "fill", "collections"):
        raise _Unsupported(gid)
    target = els.get(m)
    if (
        target is None
        or _cls_of(m, target) not in ("image", "collection")
        or not any(f.get("prop") == prop for f in target.get("editable") or [])
    ):
        raise _Unsupported(gid)
    ax = f"_ax(fig, {int(mm.group(1))})"
    return f"_at({table[kind.group(1)].format(ax=ax)}, {int(kind.group(2))})"


def _literal(value, encoding: str) -> str:
    """值的源码写法：文件编码放得下就原样（中文图注还读得懂），放不下转义。"""
    text = repr(value)
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        text = ascii(value)
    return text


def _position(el: dict | None, gid: str, obj: str, value) -> str:
    fx, fy = float(value[0]), float(value[1])
    role = (el or {}).get("role")
    if _COPY_GID.match(
        gid
    ):  # 副本沿用源的角色，但它是一段独立的 Text（标题的副本不走 `set_title`）
        return f"_text_to({obj}, fig, {fx!r}, {fy!r})"
    if role == "title":
        i = _AX.match(gid)
        assert i is not None
        if not gid.endswith(".title"):
            raise _Unsupported(gid)
        return f"_title_to(_ax(fig, {int(i.group(1))}), fig, {fx!r}, {fy!r})"
    if role == "axis_label":
        return f"_label_to({obj}, fig, {fx!r}, {fy!r})"
    if role == "text":
        return f"_text_to({obj}, fig, {fx!r}, {fy!r})"
    raise _Unsupported(gid)


def _font(cls: str, obj: str, value, manifest: dict, encoding: str) -> str:
    """改字体（引擎 `_set_text_fontfamily` / `_apply_family_selection` 的公开 API 写法）。

    回退尾巴是这台机器探测出来的（`overrides.fallback_tail()`，含中日韩字体），父进程算不了，
    所以读 manifest 的 `font_fallback_tail`。值三种形状：一个名字、一条链（认第一个）、「中英两格」的选择。
    """
    if isinstance(value, dict):
        fn = "_tick_font_pick" if cls == "ticks" else "_font_pick"
        primary, fallback = value.get("primary"), value.get("fallback")
        if isinstance(primary, list):
            primary = primary[0] if primary else None
        return f"{fn}({obj}, {_literal(primary, encoding)}, {_literal(fallback, encoding)})"
    family = str(value[0]) if isinstance(value, (list, tuple)) else str(value)
    tail = manifest.get("font_fallback_tail") or ["DejaVu Sans"]
    chain = [family, *(str(f) for f in tail if f != family)]
    fn = "_tick_font" if cls == "ticks" else "_font_to"
    return f"{fn}({obj}, {_literal(chain, encoding)}, {_literal(family, encoding)})"


_LEGEND_TEXT = re.compile(r"^axes_(\d+)\.legend\.texts_(\d+)$")


def _rebuild(gid: str, patches, manifest: dict, encoding: str) -> str:
    """同一个图例上所有构建期参数合成**一条** `_legend_rebuild`（`_translate` 按语句去重）。

    素材是脚本原样的图例（这一档排在所有普通属性之前），位置用热态的 loc / 锚点：重建之后
    loc / 锚点 / 拖动这几条各自的语句照常再落一遍，与引擎的位置模型同一个结果。
    """
    m = re.match(r"^(axes_(\d+)\.legend)", gid)
    if m is None:
        raise _Unsupported(gid)
    leg_gid, i = m.group(1), int(m.group(2))
    els = {e["gid"]: e for e in manifest.get("elements", [])}
    fields = {f["prop"]: f.get("value") for f in (els.get(leg_gid) or {}).get("editable", [])}
    # 锚点表达不出来（4 元组锚框、别的参照系）时 manifest 不发这条：重建会把它弄丢，不写
    if "loc_anchor" not in fields or "loc" not in fields:
        raise _Unsupported("loc_anchor")
    edited = {p["prop"]: p["value"] for p in patches if p["gid"] == leg_gid}
    loc = fields["loc"]
    if not isinstance(loc, str) or loc == "custom":
        if "loc_frac" not in edited:  # 脚本自己给的坐标 loc：重建摆不回去
            raise _Unsupported("loc")
        loc = "upper right"  # 随后的 `_legend_to` 按拖动的点重摆
    spec = {k: edited[k] for k in _LEGEND_REBUILD if k in edited}
    # 列数读不到公开 API（`Legend._ncols`），没改时取 manifest 里的脚本原样
    spec.setdefault("ncol", int(fields.get("ncol") or 1))
    anchor = fields["loc_anchor"]
    return (
        f"_legend_rebuild(_ax(fig, {i}), {_literal(loc, encoding)}, "
        f"{_literal(anchor, encoding)}, {_literal(spec, encoding)})"
    )


def _has_axis_label_text(manifest: dict, ax: str) -> bool:
    """这根轴（色条轴）有带字的轴标签：色条整根藏起来时那一种留作重放的判据，刻度文字的隐藏同用它。"""
    return any(
        e["gid"] in (f"{ax}.xlabel", f"{ax}.ylabel")
        and any(f.get("prop") == "text" and f.get("value") for f in e.get("editable") or [])
        for e in manifest.get("elements", [])
    )


def _is_3d(manifest: dict, i: int) -> bool:
    return any(
        e["gid"] == f"axes_{i}" and e.get("role") == "axes3d" for e in manifest.get("elements", [])
    )


def _tick_statement(
    gid: str, prop: str, value, patches, manifest: dict, encoding: str, obj: str
) -> str:
    """刻度组（`axes_i.xticks` / `yticks`）上的几条：显隐、线宽按档、定位与格式合成一次。"""
    m = re.fullmatch(r"axes_(\d+)\.([xy])ticks", gid)
    if m is None:
        raise _Unsupported(gid)
    i, which = int(m.group(1)), m.group(2)
    if prop == "visible":  # 引擎只动标签的显隐（`tickmodel._set_ticks_visible`），刻度线不管
        # 与引擎同口径按侧：隐藏关两侧（色条 / twinx 的字在右边）；显示保留脚本原样那几侧
        if value:
            return f"{obj}.show_labels()"
        if any(
            p["gid"] == f"axes_{i}.colorbar" and p["prop"] == "visible" and p["value"] is False
            for p in patches
        ) and _has_axis_label_text(manifest, f"axes_{i}"):
            # 带标签的色条整根藏起来时它留作重放（见 `_statement` 的 colorbar.visible），刻度文字的
            # 隐藏跟它走：脚本里先藏字的话 savefig 按「没字」排了轴标签，热态是同一轮一起藏、
            # 标签没重排——看不见的标签几何两边对不上
            raise _Unsupported(prop)
        a, b = ("bottom", "top") if which == "x" else ("left", "right")
        return f"{obj}.params(label{a}=False, label{b}=False)"
    if prop == "width":
        # 3D 轴每次 draw 都用 `_axinfo`（私有）盖回刻度线宽，公开 API 留不住
        if _is_3d(manifest, i):
            raise _Unsupported(prop)
    if prop in _TICK_MODEL:
        spec: dict = {}
        for p in patches:
            q = p["prop"]
            if p["gid"] != gid or q not in _TICK_MODEL or p["value"] is None:
                continue
            v = p["value"]
            if q == "major_values":
                v = _number_list(v)
            elif q in ("major_step", "minor_step"):
                v = float(v)
            elif q == "minor_visible":
                v = bool(v)
            else:
                v = str(v)
            spec[q] = v
        spec = {k: spec[k] for k in _TICK_MODEL if k in spec}
        return f"_tick_model(_ax(fig, {i}), {which!r}, {_literal(spec, encoding)})"
    tmpl = WRITES[("ticks", prop)]
    assert tmpl is not None
    return tmpl.format(o=obj, v=_literal(value, encoding))


def _snapshots_ticks(patch: dict, patches: list[dict]) -> bool:
    """这条刻度 override 是不是靠「落笔那一刻的刻度」算的：单条文字、固定刻度没给值、固定间隔没给步长。"""
    prop, gid = patch["prop"], patch["gid"]
    if prop == "text":
        return bool(re.search(r"\.[xy]ticklabels_\d+$", gid))
    if prop not in _TICK_MODEL:
        return False
    given = {p["prop"]: p["value"] for p in patches if p["gid"] == gid and p["prop"] in _TICK_MODEL}
    mode = given.get("major_mode")
    if mode == "fixed":
        return not _number_list(given.get("major_values"))
    if mode == "step":
        step = given.get("major_step")
        return not (isinstance(step, (int, float)) and step > 0)
    return False


def _sized_at_draw(gid: str, els: dict) -> bool:
    """这条轴的大小是不是要画出来才定：落笔那一刻（还没画过）的刻度按「还没定的大小」算，与热态里画完之后的不同。

    实测（`InvCbar2`：`imshow` 加四条色条）：宿主轴 `aspect=1` 与底部横色条的轴，落笔时宽 167 / 223 px、
    画完 162 / 205 px，x 轴自动刻度 6 个 → 5 个，冻结出来的单条刻度文字比热态多一条、位置错开，验证不过、整份不交付。
    能判的两种：`aspect` 不是 auto 的轴（按纵横比在画的时候收缩），以及色条轴（色条 locator 画的时候才落位）。
    """
    m = re.match(r"(axes_\d+)\.", gid)
    if m is None:
        return False
    ax = m.group(1)
    if f"{ax}.colorbar" in els:
        return True
    aspect = next(
        (
            f.get("value")
            for f in (els.get(ax) or {}).get("editable", [])
            if f.get("prop") == "aspect"
        ),
        "auto",
    )
    return str(aspect) != "auto"


def _number_list(v) -> list[float]:
    """与引擎 `tickmodel._num_list` 同口径：单个数当一项，非数字的元素丢掉。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return [float(v)]
    return [float(x) for x in (v or []) if isinstance(x, (int, float)) and not isinstance(x, bool)]


#: 图例示意线跟着源对象变、而脚本里没有公开 API 能就地改的那几种：误差棒 / 茎叶一格画好几条，
#: 散点的示意线大小是按 markerscale 与 scatterpoints 现算的。引擎对 `follow_source` 的项是「从源重新派生」，
#: 脚本这边等价的写法是**整份重排图例**（`_legend_rebuild` 从源对象重新收集，与引擎同一个 handler）。
_REFRESH_PROPS = {
    "errorbar": None,  # 每个键都改示意线
    "stem_series": ("color", "linewidth", "linestyle", "marker", "markersize", "alpha", "visible"),
    "collection": ("size", "marker"),
}


def _refresh(
    el: dict | None, gid: str, cls: str, prop: str, patches, manifest: dict, encoding: str
):
    """这条修改会改到图例里跟着它走的示意线、而公开 API 不能就地改：回要追加的图例重排语句，不用追加回 None。

    重排之后别的项跟着重新派生，所以同一个图例里只要有一项脱开了跟随（custom）就不写：引擎里它停在
    脚本原样，重排会把它换成源现在的样子。重排不了（锚点读不到……）时抛 `_Unsupported`，这条修改留给重放。
    """
    if cls not in _REFRESH_PROPS or (
        _REFRESH_PROPS[cls] is not None and prop not in _REFRESH_PROPS[cls]
    ):
        return None
    entries = [e for e in manifest.get("elements", []) if e.get("role") == "legend_text"]
    mine = [e for e in entries if ((e.get("legend_entry") or {}).get("source_gid")) == gid]
    if not mine:
        return None

    def binding(e):
        return next(
            (f.get("value") for f in e.get("editable", []) if f.get("prop") == "binding"), None
        )

    legs = {e["gid"].rsplit(".texts_", 1)[0] for e in mine if binding(e) == "follow_source"}
    if not legs:
        return None
    (leg_gid,) = legs if len(legs) == 1 else (None,)
    if leg_gid is None:
        raise _Unsupported(gid)
    same = [e for e in entries if e["gid"].startswith(leg_gid + ".texts_")]
    if any(
        (e.get("legend_entry") or {}).get("source_gid") and binding(e) != "follow_source"
        for e in same
    ):
        raise _Unsupported(gid)
    return _rebuild(leg_gid, patches, manifest, encoding)


def _cls_of(gid: str, el: dict | None) -> str | None:
    """这个 gid 的 override 类（`WRITES` / `NOT_WRITABLE` 的键）：**类别只在这里推断**。

    元素在 manifest 里缺席时（刻度文字全关掉之后刻度组量不出几何，manifest 把它丢了）按 gid 形状认：
    它是坐标轴的固有部分。`_statement`、档位（`_kind`）、去重、色阶分组都问这一处，不各自从 role 推。
    """
    role = (el or {}).get("role") or ("figure" if gid == "figure" else "")
    if not role and re.fullmatch(r"axes_\d+\.[xy]ticks", gid):
        role = "ticks"
    return _ROLE_CLS.get(role)


def _stroke_statement(el: dict | None, gid: str, obj: str, patches, encoding: str) -> str:
    """描边三条（开关 / 颜色 / 线宽）合成**一条** `_stroke(...)`（`_translate` 按语句去重）。

    引擎把三者存在 artist 上、每条改动都按三元组重排一遍路径效果；脚本里读不到 `withStroke` 的参数
    （私有），所以三个值都在这里定死：改过的取改的，没改的取 manifest 里的脚本原样（脚本自带
    `withStroke` 时引擎已把它读成这三个值）。
    """
    fields = {f["prop"]: f.get("value") for f in (el or {}).get("editable", [])}
    edited = {p["prop"]: p["value"] for p in patches if p["gid"] == gid}
    got = {
        k: edited.get(k, fields.get(k, d))
        for k, d in (("stroke_enabled", False), ("stroke_color", "#FFFFFF"), ("stroke_width", 1.5))
    }
    return (
        f"_stroke({obj}, {bool(got['stroke_enabled'])!r}, "
        f"{_literal(got['stroke_color'], encoding)}, {float(got['stroke_width'])!r})"
    )


def _copy_statement(gid: str, value, manifest: dict, encoding: str) -> str:
    """图内副本的创建语句（ADR 0207）：`_copy_of(fig, gid, 宿主, 源, [x, y], 文字)`。

    条件与引擎 `_ensure_copy` 的受理一致（宿主是 2D 子图 / 整图、源是宿主上第一期认的那几类文字）；
    源的选择器写不出（`_Unsupported`）、宿主 / 源在 manifest 里没有，一律不写：整条副本与它 gid 上的
    全部修改留作重放（`_translate` 的级联），不生成半截代码。"""
    m = _COPY_GID.match(gid)
    if m is None or not isinstance(value, dict):
        raise _Unsupported(gid)
    src, text, at = value.get("src"), value.get("text"), value.get("at")
    if not isinstance(src, str) or not isinstance(text, str):
        raise _Unsupported(gid)
    if not (
        isinstance(at, (list, tuple))
        and len(at) == 2
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in at)
    ):
        raise _Unsupported(gid)
    x, y = float(at[0]), float(at[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise _Unsupported(gid)
    els = {e["gid"]: e for e in manifest.get("elements", [])}
    host_gid = m.group(1)
    if host_gid == "fig":
        host = "fig"
        sm = re.fullmatch(r"fig\.texts_(\d+)", src)
        if sm is None:
            raise _Unsupported(gid)
        src_expr = (
            f"_at(fig.texts, {int(sm.group(1))})"  # 副本在 `fig.artists`，不挤动 `fig.texts` 的序号
        )
    else:
        i = int(host_gid[len("axes_") :])
        if host_gid not in els or _is_3d(manifest, i) or f"{host_gid}.colorbar" in els:
            raise _Unsupported(gid)
        if re.fullmatch(re.escape(host_gid) + _COPY_SRC_AX, src) is None:
            raise _Unsupported(gid)
        host = f"_ax(fig, {i})"
        src_expr = _selector(src, els.get(src))
    if _cls_of(src, els.get(src)) != "text":
        raise _Unsupported(gid)
    return f"_copy_of(fig, {gid!r}, {host}, {src_expr}, [{x!r}, {y!r}], {_literal(text, encoding)})"


def _hold_copies(live: list[tuple[dict, str]], residual: list[dict], patches: list[dict]) -> bool:
    """副本是一整件事：它 gid 上有一条留作重放，整条副本（含 `copy_of`）与它全部修改一起留下。

    写进脚本的副本只认写进脚本的修改；留作重放的修改要落在副本上，引擎里得有这个副本。
    被留下的副本，它的源上的修改也一起留下：引擎的副本样式取「脚本原样的源」，源的修改要是已写进新脚本，
    重放建出来的副本就抄到改过的样子，与热态不同。返回是否挪动了东西。"""
    held = {str(r["gid"]) for r in residual if _COPY_GID.match(str(r["gid"]))}
    if not held:
        return False
    srcs = {
        p["value"]["src"]
        for p in patches
        if p["prop"] == COPY_PROP
        and p["gid"] in held
        and isinstance(p.get("value"), dict)
        and isinstance(p["value"].get("src"), str)
    }
    moved = False
    for entry in list(live):
        if entry[0]["gid"] in held or entry[0]["gid"] in srcs:
            live.remove(entry)
            residual.append(entry[0])
            moved = True
    return moved


def _statement(
    el: dict | None, gid: str, prop: str, value, patches, encoding: str, manifest: dict
) -> str:
    cls = _cls_of(gid, el)
    if cls is None or (cls, prop) not in WRITES:
        raise _Unsupported(prop)
    if (cls, prop) == ("text", COPY_PROP):
        return _copy_statement(gid, value, manifest, encoding)
    obj = _selector(gid, el)
    if prop == "hatch":  # 引擎：空串 / none 都是「不用花纹」
        value = None if value in (None, "", "none") else str(value)
    if (cls, prop) == ("collection", "marker") and value in ("original", ""):
        raise _Unsupported(prop)  # 「放回脚本原样」：脚本里没有原来那条路径可取
    if prop == "pos_frac":
        return _position(el, gid, obj, value)
    if prop == "fontfamily":
        return _font(cls, obj, value, manifest, encoding)
    if (cls, prop) == ("legend", "loc_frac"):
        return f"_legend_to({obj}, fig, {float(value[0])!r}, {float(value[1])!r})"
    if cls == "legend" and prop in _LEGEND_REBUILD:
        return _rebuild(gid, patches, manifest, encoding)
    if cls == "legend" and prop in ("title", "title_fontsize"):
        # 脚本原样没有标题时图例里没有标题这个元素，凭空加一个，验证逐元素比对就多出一项
        if not any(e["gid"] == gid + ".title" for e in manifest.get("elements", [])):
            raise _Unsupported(prop)
    if cls == "legend_text" and (prop == "binding" or prop in _HANDLE_PROPS):
        m = _LEGEND_TEXT.match(gid)
        if m is None:
            raise _Unsupported(gid)
        ax, j = f"_ax(fig, {int(m.group(1))})", int(m.group(2))
        source = str(((el or {}).get("legend_entry") or {}).get("source_gid") or "")
        if re.search(r"\.(errorbar|stemseries)_\d+$", source):
            # 误差棒 / 茎叶一格画好几条：引擎把颜色落到整格每个 artist 上，公开 API 够不着那一格
            raise _Unsupported(prop)
        if prop == "binding":
            if value != "custom":  # 改回跟随：脚本原样里没有「重新跟随」这件事可写
                raise _Unsupported(prop)
            return f"_legend_detach({ax}, {j})"
        return f"_legend_handle_set({ax}, {j}, {prop[len('handle_') :]!r}, {_literal(value, encoding)})"
    if cls == "ticks":
        return _tick_statement(gid, prop, value, patches, manifest, encoding, obj)
    if cls == "ticklabel":
        m = re.fullmatch(r"axes_(\d+)\.([xy])ticklabels_(\d+)", gid)
        # 3D 曲面图（Inv3DS）上 y 轴的刻度标签：热态 5 条、脚本重放 9 条，位置也错开，不写
        if m is None or _is_3d(manifest, int(m.group(1))):
            raise _Unsupported(gid)
        return f"_tick_text(_ax(fig, {int(m.group(1))}), {m.group(2)!r}, {int(m.group(3))}, {_literal(str(value), encoding)})"
    if (
        cls in ("image", "collection")
        and prop == "cmap"
        and (_cmap_group_reaches_beyond(el, manifest, gid) or (el or {}).get("scale_gids"))
    ):
        # 同上：图像 / 集合的 cmap 也是这一组的广播（色阶兄弟、另一个 mappable 的色条跟着换），公开 API 写不出「谁是兄弟」
        raise _Unsupported(prop)
    if cls == "colorbar" and prop == "label":
        # 长轴看脚本原样的方向：方向要是也改了，它留作重放，重放时把标签搬到新长轴
        orient = next(
            (
                f.get("value_original", f.get("value"))
                for f in (el or {}).get("editable", [])
                if f.get("prop") == "orientation"
            ),
            None,
        )
        if orient not in ("vertical", "horizontal"):  # 多宿主色条不发这个字段，方向认不准
            raise _Unsupported(prop)
        return f"{obj}.set_{'y' if orient == 'vertical' else 'x'}label({_literal(str(value), encoding)})"
    if cls == "colorbar" and prop == "visible" and value is False:
        # 带标签的色条藏起来之后，标签的几何在热态与重放里不是同一个值（热态在藏起来之前画过一次、
        # 位置已排定；重放里轴一开始就不可见，轴不画、标签就没排过位置）：画面一样，验证比几何比不过，
        # 所以这一种不写，留作重放
        if _has_axis_label_text(manifest, gid[: -len(".colorbar")]):
            raise _Unsupported(prop)
    if cls == "colorbar" and prop in ("cmap", *_CLIM):
        if prop == "cmap" and _cmap_group_reaches_beyond(el, manifest, el.get("mappable_gid")):
            # 组里还有写不到的对象（独立 `ScalarMappable` 的色条：共用 norm 不传播色图，引擎经 `scale_colorbars`
            # 一并换掉它，脚本里 `set_cmap` 只换得了 mappable 这一个）
            raise _Unsupported(prop)
        if prop == "cmap" and (el or {}).get("scale_gids"):
            # 色阶兄弟（共用 norm 的别的图元）：引擎的 setter 逐个给它们换色图，公开 API 写不出这个「谁是兄弟」
            raise _Unsupported(prop)
        obj = _mappable({e["gid"]: e for e in manifest.get("elements", [])}, gid, prop)
    if cls in ("text", "legend_text") and prop.startswith("stroke_"):
        return _stroke_statement(el, gid, obj, patches, encoding)
    if cls in ("text", "legend_text") and prop.startswith("bbox_"):
        return f"_bbox({obj}, {prop[len('bbox_') :]!r}, {_literal(value, encoding)})"
    if cls == "arrowpatch" and prop == "arrowstyle" and value == "custom":
        raise _Unsupported(prop)  # 占位项：脚本用了带参数的自定义样式，选它就是不动
    if cls == "arrowpatch" and prop == "endpoints_frac":
        f = [float(x) for x in value]
        # 端点只对「此刻是纯箭头」的注释与独立箭头生效：有字的注释上它是失效的（引擎放回脚本原样、manifest 不再发
        # `arrow_endpoints`），脚本里照落就会把还在的文字箭头拖走——留给引擎按同一个裁决重放
        if len(f) != 4 or not (el or {}).get("arrow_endpoints"):
            raise _Unsupported(prop)
        if gid.endswith(".arrow"):  # annotate 的箭头：落在注释本身的锚点上，不是 patch
            return f"_ann_to({_selector(gid.removesuffix('.arrow'), None)}, fig, {f!r})"
        return f"_arrow_to({obj}, fig, {f!r})"
    tmpl = WRITES[(cls, prop)]
    assert tmpl is not None
    return tmpl.format(
        o=obj,
        v=_literal(value, encoding),
        x=", ".join(_literal(x, encoding) for x in (value or ())) if "{x}" in tmpl else "",
    )


def _rank(prop: str, kind: str | None) -> float:
    """步骤顺序（`kind` 是生成语句时按 (cls, prop) 定的标记 `_kind`，**不看语句文本**：用户的文字写进语句里，
    标题 / 图例文字 / 刻度文字里带着 `_legend_rebuild(` 之类的子串不能影响排序）：与 `overrides._apply_rank` 同序的那几档（对账见 `tests/test_script_adjust*.py`）。

    引擎的函数要看 artist 类型、父进程 import 不了，而写得进脚本的键里只有坐标轴刻度类型这一档
    排在普通属性之前。其余几档是脚本这一侧自己的先后：

    0.5 图例里有跟着它走的示意线的误差棒 / 茎叶 / 散点大小与形状（`_refresh`，`_translate` 直接给这一档）：示意线靠整份
        重排重新派生，源要先改好。没有跟随项的（示意线已脱开、或根本没有图例）照普通属性排在重排之后：
        重排从**脚本原样**的源收集，脱开的示意线才停在原样；
    1. 重建图例：换掉图例对象，素材必须是脚本原样（引擎同样从源对象 / 原样快照重建）；
    2. 锚点、条目脱开跟随：锚点要在拖动之前（拖动清锚框，与位置模型的优先级一致）；
       脱开要在源对象改样式之前，否则 `_with_legend` 会把源的新样子带进示意线；
    3. 广播（边框总颜色 / 总线宽、色条刻度、色条 vmin / vmax、柱系列的样式）：要在被它盖住的窄条目之前，窄的盖广播；
    4. 普通属性（含坐标范围：刻度定位与刻度文字都按落下之后的视区现算）；
    5. 刻度定位与格式：引擎的第 7 档（`_RANK_TICK_MODEL`），素材是换完刻度类型之后脚本原样的 locator / formatter；
    6. 单条刻度文字：引擎的第 8 档（`_RANK_TICK_TEXT`），把整条轴冻成固定刻度，必须在定位之后；
    7. 示意线样式：写在「脚本原样 + 这几条」上；
    8. 改名（`label`）：最后落。

    图内副本（`copy_of`，ADR 0207）的创建语句排在**全部**属性语句之前（档位 -1，`kind == "copy"`）：先建出对象，
    后面的修改才有地方落，副本也才拿得到脚本原样的源。引擎的 `_RANK_COPY` 在几何组之后、其余之前；脚本侧写得进的
    几何组键只有 `xscale` / `yscale`，副本落在宿主的 transAxes / transFigure 上、与刻度类型无关，所以「副本先于
    每一条写进脚本的属性语句」两边等价（`tests/test_script_adjust_copy.py::test_copy_rank_agrees_with_the_engine`）。

    箭头端点**不单列一档**：figure 分数换成数据坐标用的是落那一刻的坐标范围，引擎也是这样（端点排在 `ylim` 前，
    箭头钉在数据位置、随后范围一改就跟着数据走；排在后面才钉在屏幕上），同档按列表顺序，两边一致
    （`test_arrow_edits_combine_like_the_editor` 的两种顺序）。
    """
    if kind == "copy":
        # 图内副本的创建语句：先于一切属性语句（含它自己的源上的修改），副本才拿到脚本原样的样式
        return -1
    if kind == "rename":
        return 8  # 曲线靠原 label 认（`_line`）、图例色块靠 label 对（`_with_legend`、重排）：改名要在所有别的语句之后
    if prop in ("xscale", "yscale"):
        return 0
    if kind == "rebuild":
        return 1
    if prop == "loc_anchor" or kind == "detach":
        return 2
    if prop in _BROADCAST_FIRST or kind == "broadcast":
        return 3  # 广播先于窄的（边框四边、色条刻度组与 vmin / vmax、柱系列），与引擎同序
    if kind == "tick_model":
        return 5
    if kind == "tick_text":
        return 6
    if prop.startswith("handle_"):
        return 7
    return 4


def _kind(cls: str, prop: str) -> str | None:
    """这条 override 的语句属于哪一档（按 (cls, prop) 定，见 `_rank`）。"""
    if cls == "text" and prop == COPY_PROP:
        return "copy"
    if prop == "label" and cls in ("line", "collection", "bar_series", "stem_series"):
        return "rename"  # 色条的标签是 set_ylabel，不在此列
    if cls == "legend" and prop in _LEGEND_REBUILD:
        return "rebuild"
    if cls == "legend_text" and prop == "binding":
        return "detach"
    if (cls == "colorbar" and prop in _CLIM) or (cls == "bar_series" and prop in _BAR_BROADCAST):
        return "broadcast"
    if cls == "ticks" and prop in _TICK_MODEL:
        return "tick_model"
    if cls == "ticklabel" and prop == "text":
        return "tick_text"
    if cls in ("text", "legend_text") and prop.startswith("stroke_"):
        return "stroke"  # 三条合成一句 `_stroke(...)`，只落一回（档位是普通属性）
    return None


# -------------------------------- 块的运行期代码 --------------------------------
#: 全部只用公开 API。`build()` 只保留这次用得到的定义（`_needed`），一条改色不带图例重建那 40 行。
#: 文本一律 ASCII：任何 PEP 263 编码的文件都放得下这一段，不用第二套模板。
_RUNTIME = """\
import os as _os
import warnings as _warnings

import matplotlib as _mpl
import matplotlib.figure as _mfig
from matplotlib.transforms import Bbox as _Bbox


def _axes_in_order(fig):
    # Same numbering as the editor: fig.axes, then child_axes, then parasite axes.
    out = list(fig.axes)
    seen = {id(a) for a in out}

    def absorb(frontier, attrs):
        while frontier:
            nxt = []
            for parent in frontier:
                for attr in attrs:
                    for kid in getattr(parent, attr, None) or []:
                        if id(kid) not in seen:
                            seen.add(id(kid))
                            out.append(kid)
                            nxt.append(kid)
            frontier = nxt

    absorb(list(out), ("child_axes",))
    absorb(list(out), ("parasites", "child_axes"))
    return out


class _Skip(Exception):
    pass


def _ax(fig, i):
    axes = _axes_in_order(fig)
    if i >= len(axes):
        raise _Skip("axes %d not found" % i)
    return axes[i]


def _at(items, j):
    items = list(items)
    if j >= len(items):
        raise _Skip("item %d not found" % j)
    return items[j]


def _line(ax, j, label):
    if label is not None:
        hits = [ln for ln in ax.lines if ln.get_label() == label]
        if len(hits) != 1:
            raise _Skip("no unique line labelled %r" % (label,))
        return hits[0]
    return _at(ax.lines, j)


def _rename(fig, pairs):
    # Find every target by its original label first, then rename: a rename must not change what the next one finds.
    found = [(find(), name) for find, name in pairs]
    for item, name in found:
        item.set_label(name)


def _with_legend(item, setter):
    # A restyled artist keeps its legend key in step.
    setter(item)
    label = item.get_label()
    ax = item.axes
    for leg in [ax.get_legend()] + list(ax.figure.legends):
        if leg is None:
            continue
        for handle, text in zip(leg.legend_handles, leg.get_texts()):
            if getattr(handle, "_tavotto_detached", False):
                continue  # this entry no longer follows its series
            if text.get_text() == label and hasattr(handle, "get_linestyle"):
                setter(handle)


def _legend(ax):
    leg = ax.get_legend()
    if leg is None:
        raise _Skip("no legend on this axes")
    return leg


class _Group:
    # Every bar of a bar series, labelled like the series.
    def __init__(self, ax, container):
        self.axes, self.container = ax, container
        self.members = list(container.patches)

    def get_label(self):
        return self.container.get_label()

    def set_label(self, label):
        # The name belongs to the series, not to each bar.
        self.container.set_label(str(label))

    def set_bar_width(self, width):
        # Same as the editor: every bar keeps its centre.
        for r in self.members:
            centre = r.get_x() + r.get_width() / 2
            r.set_x(centre - width / 2)
            r.set_width(width)

    def __getattr__(self, name):
        def call(*args, **kwargs):
            for member in self.members:
                getattr(member, name)(*args, **kwargs)

        return call


def _bars(ax, j):
    return _Group(ax, _at(ax.containers, j))


class _FirstBar:
    # The first bar of a series is what the series' legend key is drawn from.
    def __init__(self, bar, container):
        self._bar, self._container = bar, container
        self.axes = bar.axes

    def get_label(self):
        return self._container.get_label()

    def __getattr__(self, name):
        return getattr(self._bar, name)


def _bar(ax, j, k):
    container = _at(ax.containers, j)
    bar = _at(container.patches, k)
    return _FirstBar(bar, container) if k == 0 else bar


class _Err:
    # An errorbar container: the data line, the caps and the bar lines, the same
    # parts the editor restyles together.
    def __init__(self, ax, container):
        self.axes, self.container = ax, container
        self.line, caps, bars = container.lines
        self.caps, self.bars = list(caps), list(bars)
        self.stroke = ([self.line] if self.line is not None else []) + self.bars
        self.parts = ([self.line] if self.line is not None else []) + self.caps + self.bars

    def get_label(self):
        return self.container.get_label()

    def set_color(self, color):
        # A cap is drawn by its marker edge.
        for part in self.parts:
            part.set_color(color)
        for cap in self.caps:
            cap.set_markeredgecolor(color)

    def set_linewidth(self, width):
        for part in self.stroke:
            part.set_linewidth(width)

    def set_capsize(self, size):
        for cap in self.caps:
            cap.set_markersize(float(size))

    def set_cap_thickness(self, width):
        for cap in self.caps:
            cap.set_markeredgewidth(float(width))

    def set_alpha(self, alpha):
        for part in self.parts:
            part.set_alpha(alpha)

    def set_visible(self, visible):
        for part in self.parts:
            part.set_visible(bool(visible))


class _Stem:
    # A stem container: the marker line and the stems (the zero line stays a plain line).
    def __init__(self, ax, container):
        self.axes, self.container = ax, container
        self.marker = container.markerline
        stems = container.stemlines
        self.stems = list(stems) if isinstance(stems, (list, tuple)) else [stems]
        self.parts = ([self.marker] if self.marker is not None else []) + self.stems

    def get_label(self):
        return self.container.get_label()

    def set_label(self, label):
        self.container.set_label(str(label))

    def set_color(self, color):
        for part in self.parts:
            part.set_color(color)

    def set_linewidth(self, width):
        for stem in self.stems:
            stem.set_linewidth(width)

    def set_linestyle(self, style):
        for stem in self.stems:
            stem.set_linestyle(style)

    def set_marker(self, marker):
        if self.marker is not None:
            self.marker.set_marker(str(marker))

    def set_markersize(self, size):
        if self.marker is not None:
            self.marker.set_markersize(float(size))

    def set_alpha(self, alpha):
        for part in self.parts:
            part.set_alpha(alpha)

    def set_visible(self, visible):
        for part in self.parts:
            part.set_visible(bool(visible))

    def set_zorder(self, z):
        for part in self.parts:
            part.set_zorder(float(z))


def _scatter_marker(coll, name):
    # The path Axes.scatter itself builds for a marker.
    from matplotlib.markers import MarkerStyle

    style = MarkerStyle(name)
    coll.set_paths([style.get_path().transformed(style.get_transform())])


class _TickSet:
    def __init__(self, ax, which):
        self.ax, self.which = ax, which

    def params(self, which="both", **kw):
        self.ax.tick_params(axis=self.which, which=which, **kw)

    def show_labels(self):
        # Each side keeps what the axis had (a colorbar or twinx has its labels on the
        # right); only when no major side has them do they go bottom / left. A tick the
        # script hid on its own does not switch a whole side off, and is shown again.
        import matplotlib as mpl

        axis = getattr(self.ax, self.which + "axis")
        sides = ("bottom", "top") if self.which == "x" else ("left", "right")

        def side_on(minor, n):
            kw = axis._minor_tick_kw if minor else axis._major_tick_kw
            v = kw.get("label%dOn" % n)
            if v is not None:
                return bool(v)
            ticks = axis.get_minor_ticks() if minor else axis.get_major_ticks()
            if ticks:
                return any(getattr(t, "label%d" % n).get_visible() for t in ticks)
            return bool(mpl.rcParams.get("%stick.label%s" % (self.which, sides[n - 1]), n == 1))

        on = [(side_on(m, 1), side_on(m, 2)) for m in (False, True)]
        if not any(on[0]):
            on = [(True, False), (True, False)]
        for which, (s1, s2) in zip(("major", "minor"), on):
            self.params(which=which, **{"label" + sides[0]: s1, "label" + sides[1]: s2})


def _tick_formatter(name):
    from matplotlib import ticker as mt

    if name == "none":
        return mt.NullFormatter()
    if name == "sci":
        fmt = mt.ScalarFormatter(useMathText=True)
        fmt.set_powerlimits((0, 0))
        return fmt
    return mt.FormatStrFormatter(name)


def _minor_auto(axis):
    # AutoMinorLocator gives nothing on a log-type scale, so each scale gets its own.
    from matplotlib import ticker as mt

    name = axis.get_scale()
    if name == "log":
        return mt.LogLocator(base=axis.get_transform().base, subs="auto")
    if name == "symlog":
        return mt.SymmetricalLogLocator(axis.get_transform(), subs=list(range(1, 10)))
    if name == "logit":
        return mt.LogitLocator(minor=True)
    return mt.AutoMinorLocator()


def _tick_model(ax, which, spec):
    # What the editor sets as a whole: a key that was not asked for keeps what the
    # script made (the locator and formatter in place now), nothing is swapped for a default.
    from matplotlib import ticker as mt

    axis = getattr(ax, which + "axis")
    mode = spec.get("major_mode") or "auto"
    if mode in ("fixed", "step"):
        try:
            locs = [float(v) for v in axis.get_majorticklocs()]
        except Exception:
            locs = []
        if mode == "fixed":
            axis.set_major_locator(mt.FixedLocator([float(v) for v in spec.get("major_values") or ()] or locs))
        else:
            step = float(spec.get("major_step") or 0.0)
            if step <= 0 and len(locs) >= 2:
                step = abs(locs[1] - locs[0])
            if step <= 0:
                lo, hi = axis.get_view_interval()
                step = abs(float(hi) - float(lo)) / 5.0 or 1.0
            axis.set_major_locator(mt.MultipleLocator(step))
    if spec.get("format") not in (None, "auto"):
        axis.set_major_formatter(_tick_formatter(spec["format"]))
    if spec.get("minor_format") not in (None, "auto"):
        axis.set_minor_formatter(_tick_formatter(spec["minor_format"]))
    vis, minor_mode, minor_step = spec.get("minor_visible"), spec.get("minor_mode"), spec.get("minor_step")
    if vis is False:
        axis.set_minor_locator(mt.NullLocator())
    elif vis is None and minor_mode is None and minor_step is None:
        pass
    elif (minor_mode or "auto") == "step" and minor_step and float(minor_step) > 0:
        axis.set_minor_locator(mt.MultipleLocator(float(minor_step)))
    else:
        axis.set_minor_locator(_minor_auto(axis))


def _tick_text(ax, which, j, text):
    # One label asks for the whole axis to be fixed: the ticks where they are now,
    # each with the text the formatter gives it, and this one replaced.
    from matplotlib import ticker as mt

    axis = getattr(ax, which + "axis")
    locs = [float(v) for v in axis.get_majorticklocs()]
    if not locs:
        raise _Skip("no major ticks on this axis")
    texts = [str(t) for t in axis.get_major_formatter().format_ticks(locs)]
    if not 0 <= j < len(texts):
        raise _Skip("tick label %d not found" % j)
    texts[j] = text
    axis.set_major_locator(mt.FixedLocator(locs))
    axis.set_ticklabels(texts)


def _disp(fig, fx, fy):
    # Figure fraction with a top-left origin -> display coordinates. A figure saved
    # with bbox_inches="tight" is the cropped page in the editor, so its fractions
    # are of that page (inches, bottom-left origin), not of the whole figsize.
    frame = getattr(fig, "_tavotto_frame", None)
    if frame is None:
        return fx * fig.bbox.width, (1.0 - fy) * fig.bbox.height
    x0, y0, x1, y1 = frame
    return (x0 + fx * (x1 - x0)) * fig.dpi, (y0 + (1.0 - fy) * (y1 - y0)) * fig.dpi


def _text_to(t, fig, fx, fy):
    t.set_position(tuple(t.get_transform().inverted().transform(_disp(fig, fx, fy))))


class _AxisLabel:
    # An axis label's Text does not know its axes, so carry them along.
    def __init__(self, ax, which):
        self.ax, self.axis = ax, getattr(ax, which + "axis")

    def __getattr__(self, name):
        return getattr(self.axis.label, name)


def _label_to(lab, fig, fx, fy):
    x, y = lab.ax.transAxes.inverted().transform(_disp(fig, fx, fy))
    lab.axis.set_label_coords(float(x), float(y))


def _legend_to(leg, fig, fx, fy):
    parent = leg.axes.transAxes if leg.axes is not None else fig.transFigure
    leg.set_bbox_to_anchor(None)
    leg.set_loc(tuple(parent.inverted().transform(_disp(fig, fx, fy))))


def _title_to(ax, fig, fx, fy):
    # Passing y to set_title turns automatic title placement off. set_title also
    # resets the font, so the current look is passed back in.
    t = ax.title
    look = dict(
        fontproperties=t.get_fontproperties().copy(),
        color=t.get_color(),
        horizontalalignment=t.get_horizontalalignment(),
        verticalalignment=t.get_verticalalignment(),
        rotation=t.get_rotation(),
        alpha=t.get_alpha(),
    )
    ax.set_title(t.get_text(), loc="center", y=t.get_position()[1], **look)
    t.set_position(tuple(t.get_transform().inverted().transform(_disp(fig, fx, fy))))


def _copy_ref(fig, gid):
    try:
        return fig.__dict__["_tavotto_copies"][gid]
    except KeyError:
        raise _Skip("copy %s was not created" % gid)


def _copy_of(fig, gid, host, src, at, text):
    # An in-figure copy of a text: the look the script gave its source, placed in the host's axes
    # (or figure) fractions and kept out of the layout, as in the editor. Later edits find it by gid.
    from matplotlib.text import Text

    if isinstance(src, _AxisLabel):
        src = src.axis.label
    t = Text(at[0], at[1], text)
    t.update_from(src)
    t.set_rotation_mode(src.get_rotation_mode())
    t.set_zorder(src.get_zorder())
    t.set_wrap(src.get_wrap())
    t.set_parse_math(src.get_parse_math())
    t.set_label("")
    t.set_path_effects(list(src.get_path_effects()))
    box = src.get_bbox_patch()
    if box is not None:
        t.set_bbox(
            {
                "boxstyle": box.get_boxstyle(),
                "facecolor": box.get_facecolor(),
                "edgecolor": box.get_edgecolor(),
                "linewidth": box.get_linewidth(),
                "linestyle": box.get_linestyle(),
                "alpha": box.get_alpha(),
            }
        )
    t.set_transform(fig.transFigure if host is fig else host.transAxes)
    host.add_artist(t)
    t.set_clip_on(False)
    t.set_in_layout(False)
    fig.__dict__.setdefault("_tavotto_copies", {})[gid] = t


def _legend_handle(ax, j):
    return _at(_legend(ax).legend_handles, j)


def _legend_detach(ax, j):
    _legend_handle(ax, j)._tavotto_detached = True


def _legend_handle_set(ax, j, prop, value):
    # Same setters as the editor: a line key takes every style, a patch or a
    # collection key only its colour.
    from matplotlib.collections import Collection, LineCollection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    h = _legend_handle(ax, j)
    if isinstance(h, Line2D):
        getattr(h, "set_" + prop)(value)
    elif prop != "color":
        raise _Skip("this legend key has no %s" % prop)
    elif isinstance(h, LineCollection):
        h.set_color(value)
    elif isinstance(h, (Patch, Collection)):
        h.set_facecolor(value)


def _frame_rounded(leg, rounded):
    # Same box styles as Legend(fancybox=...).
    if rounded:
        leg.get_frame().set_boxstyle("round", pad=0, rounding_size=0.2)
    else:
        leg.get_frame().set_boxstyle("square", pad=0)


def _legend_parent(leg):
    parent = leg.axes if leg.axes is not None else leg.figure
    return getattr(parent, "transAxes", None) or getattr(parent, "transSubfigure", parent.transFigure)


def _legend_anchor(leg, point):
    if point is None:
        leg.set_bbox_to_anchor(None)
    else:
        leg.set_bbox_to_anchor((float(point[0]), float(point[1])), transform=_legend_parent(leg))


def _legend_rebuild(ax, loc, anchor, spec):
    # Column count, spacing and font size are build-time arguments:
    # make the legend again from the artists its entries were collected from,
    # carrying the rest of its look over.
    old = _legend(ax)
    old_texts = list(old.get_texts())
    old_handles = list(old.legend_handles)
    labels = [t.get_text() for t in old_texts]
    found = {}
    for h, lab in zip(*ax.get_legend_handles_labels()):
        found.setdefault(lab, []).append(h)
    shown = range(min(len(labels), len(old_handles)))

    fallback = set()

    def source(j):
        hits = found.get(labels[j], [])
        if len(hits) == 1:
            return hits[0]
        if old_handles[j] is None:
            raise _Skip("legend entry %d has no artist to draw it from" % j)
        fallback.add(j)
        return old_handles[j]

    title = old.get_title()
    frame = old.get_frame()
    kw = dict(
        loc=loc,
        fontsize=spec.get("fontsize", old.prop.get_size()),
        ncols=int(spec["ncol"]),
        frameon=old.get_frame_on(),
        borderpad=spec.get("borderpad", old.borderpad),
        labelspacing=spec.get("labelspacing", old.labelspacing),
        handlelength=spec.get("handlelength", old.handlelength),
        handletextpad=spec.get("handletextpad", old.handletextpad),
        columnspacing=spec.get("columnspacing", old.columnspacing),
        borderaxespad=old.borderaxespad,
        markerscale=old.markerscale,
        numpoints=old.numpoints,
        scatterpoints=old.scatterpoints,
        shadow=old.shadow,
        handler_map=old.get_legend_handler_map(),
        title=title.get_text() or None,
    )
    if anchor is not None:
        kw["bbox_to_anchor"] = tuple(anchor)
    new = ax.legend([source(j) for j in shown], [labels[j] for j in shown], **kw)
    for h, j in zip(new.legend_handles, shown):
        # A key drawn from the old legend's own key was already scaled by
        # markerscale once: keep its size instead of scaling it again.
        if j in fallback and hasattr(h, "set_markersize") and hasattr(old_handles[j], "get_markersize"):
            h.set_markersize(old_handles[j].get_markersize())
    nf = new.get_frame()
    nf.set_facecolor(frame.get_facecolor())
    nf.set_edgecolor(frame.get_edgecolor())
    nf.set_alpha(frame.get_alpha())
    nf.set_linewidth(frame.get_linewidth())
    nf.set_boxstyle(frame.get_boxstyle())
    nt = new.get_title()
    nt.set_fontproperties(title.get_fontproperties().copy())
    nt.set_color(title.get_color())
    nt.set_alpha(title.get_alpha())
    new.set_zorder(old.get_zorder())
    new.set_visible(old.get_visible())
    for n_text, j in zip(new.get_texts(), shown):
        o = old_texts[j]
        # Only the look: Artist.update_from would copy the transform too.
        n_text.set_fontproperties(o.get_fontproperties().copy())
        n_text.set_color(o.get_color())
        n_text.set_alpha(o.get_alpha())
        if "fontsize" in spec:
            n_text.set_fontsize(spec["fontsize"])


def _math_custom(family):
    # Formulas use one font set per process, so the last font chosen wins.
    name = family
    if family in ("serif", "sans-serif", "cursive", "fantasy", "monospace"):
        names = _mpl.rcParams["font." + family]
        name = names[0] if names else None
    if not name:
        return False
    try:
        # A family is a literal name, not a Fontconfig program: Matplotlib
        # escapes it, so punctuation such as "-" or ":file=" stays in the name.
        _mpl.rcParams["mathtext.rm"] = _math_pattern(name)
        _mpl.rcParams["mathtext.it"] = _math_pattern(name, style="italic")
        _mpl.rcParams["mathtext.bf"] = _math_pattern(name, weight="bold")
        _mpl.rcParams["mathtext.sf"] = _math_pattern(name)
    except (ValueError, KeyError):
        return False
    return True


def _math_pattern(name, style=None, weight=None):
    from matplotlib.font_manager import FontProperties

    props = FontProperties(family=[name])
    if style is not None:
        props.set_style(style)
    if weight is not None:
        props.set_weight(weight)
    return props.get_fontconfig_pattern()


def _font_to(t, chain, family):
    # The chain falls back glyph by glyph, so symbols and CJK text keep drawing.
    t.set_fontfamily(chain)
    if _math_custom(family):
        t.set_math_fontfamily("custom")


def _tick_labels(ts):
    return getattr(ts.ax, "get_%sticklabels" % ts.which)()


def _tick_font(ts, chain, family):
    ts.params(labelfontfamily=chain)
    if _math_custom(family):
        for t in _tick_labels(ts):
            t.set_math_fontfamily("custom")


def _has_font(name):
    from matplotlib import font_manager

    try:
        font_manager.findfont(font_manager.FontProperties(family=[name]), fallback_to_default=False)
    except ValueError:
        return False
    return True


def _picked(families, primary, fallback):
    chain = list(families) or list(_mpl.rcParams["font.family"])
    if primary is not None:
        chain[0] = primary
    if _has_font(fallback) and fallback not in chain[:1]:
        chain = [chain[0], fallback] + [f for f in chain[1:] if f != fallback]
    return chain


def _font_pick(t, primary, fallback):
    t.set_fontfamily(_picked(t.get_fontfamily(), primary, fallback))
    if primary is not None and _math_custom(primary):
        t.set_math_fontfamily("custom")


def _tick_font_pick(ts, primary, fallback):
    labels = _tick_labels(ts)
    ts.params(labelfontfamily=_picked(labels[0].get_fontfamily() if labels else [], primary, fallback))
    if primary is not None and _math_custom(primary):
        for t in labels:
            t.set_math_fontfamily("custom")


def _stroke(t, enabled, color, width):
    # Same as the editor: one withStroke in front, other path effects left alone.
    from matplotlib import patheffects as pe

    rest = [e for e in (t.get_path_effects() or []) if not isinstance(e, pe.withStroke)]
    if enabled:
        rest = [pe.withStroke(linewidth=width, foreground=color)] + rest
    t.set_path_effects(rest)


def _bbox(t, prop, value):
    # Same as the editor: a box made here starts out invisible, only "visible" shows it.
    from matplotlib.patches import BoxStyle

    patch = t.get_bbox_patch()
    if patch is None:
        if prop == "visible" and not value:
            return
        t.set_bbox(
            dict(
                visible=False,
                boxstyle="square,pad=0.3",
                facecolor="#ffffff",
                edgecolor="#000000",
                linewidth=0.0,
                alpha=1.0,
            )
        )
        patch = t.get_bbox_patch()
    if prop in ("pad", "rounded"):
        style = patch.get_boxstyle()
        pad = float(getattr(style, "pad", 0.3))
        rounded = isinstance(style, BoxStyle.Round)
        if prop == "pad":
            pad = max(0.0, float(value))
        else:
            rounded = bool(value)
        patch.set_boxstyle(("round" if rounded else "square") + ",pad=%s" % pad)
    elif prop == "visible":
        patch.set_visible(bool(value))
    elif prop == "linewidth":
        patch.set_linewidth(float(value))
    else:
        getattr(patch, "set_" + prop)(value)


class _SolidHead:
    # Renderer wrapper: from the second path of one arrow on, strokes are solid.
    def __init__(self, renderer, arrow):
        self._renderer, self._arrow, self._paths = renderer, arrow, []

    def __getattr__(self, name):
        return getattr(self._renderer, name)

    def draw_path(self, gc, path, transform, rgbFace=None):
        if not any(p is path for p in self._paths):
            self._paths.append(path)
        offset, dashes = gc.get_dashes()
        if self._paths[0] is path or dashes is None:
            return self._renderer.draw_path(gc, path, transform, rgbFace)
        gap = getattr(self._arrow, "get_edgegapcolor", lambda: None)()
        if gap is not None and tuple(gc.get_rgb())[:3] == tuple(gap)[:3]:
            return None  # the pass in the gap colour: a solid head has no gaps
        gc.set_dashes(0, None)
        try:
            return self._renderer.draw_path(gc, path, transform, rgbFace)
        finally:
            gc.set_dashes(offset, dashes)


def _arrow_dashes(a, style):
    # A dashed arrow dashes the shaft only; the head stays solid (the editor's look).
    a.set_linestyle(style)
    if getattr(a, "_tavotto_solid_head", False):
        return
    a._tavotto_solid_head = True
    native = a.draw

    def draw(renderer, *args, **kwargs):
        return native(_SolidHead(renderer, a), *args, **kwargs)

    a.draw = draw


def _arrow_to(a, fig, f):
    if a.axes is not None:
        a.axes.get_xlim()  # the view limits are only brought up to date on demand
    inv = a.get_transform().inverted()
    a.set_positions(
        tuple(inv.transform(_disp(fig, f[0], f[1]))), tuple(inv.transform(_disp(fig, f[2], f[3])))
    )


def _ann_tr(ann, coords, origin):
    # An annotation coordinate system -> display, for the systems the editor can invert.
    from matplotlib.transforms import Affine2D, blended_transform_factory

    if isinstance(coords, tuple):
        return blended_transform_factory(
            _ann_tr(ann, coords[0], origin), _ann_tr(ann, coords[1], origin)
        )
    if coords == "data":
        return ann.axes.transData
    base, unit = coords.split()
    try:
        host = ann.get_figure(root=False)
    except TypeError:
        host = ann.get_figure()
    box = {"figure": host.figbbox, "subfigure": host.bbox, "axes": ann.axes.bbox}.get(base)
    x0, y0 = (box.p0 if box is not None else origin)
    if unit == "points":
        tr = Affine2D().scale(host.dpi / 72)
    else:
        tr = Affine2D().scale(*box.size)
    return tr.translate(x0, y0)


def _ann_xy(ann, xy, coords):
    x, y = xy
    xc, yc = coords if isinstance(coords, tuple) else (coords, coords)
    if xc == "data":
        x = float(ann.convert_xunits(x))
    if yc == "data":
        y = float(ann.convert_yunits(y))
    return _ann_tr(ann, coords, None).transform((x, y))


def _ann_to(ann, fig, f):
    # The arrow of an annotation is placed from its two anchors on every draw, so the
    # anchors are what moves (head first: an offset tail is measured from it).
    ann.axes.get_xlim()  # the view limits are only brought up to date on demand
    head = _ann_tr(ann, ann.xycoords, None).inverted().transform(_disp(fig, f[2], f[3]))
    ann.xy = (float(head[0]), float(head[1]))
    origin = _ann_xy(ann, ann.xy, ann.xycoords)
    tail = _ann_tr(ann, ann.anncoords, origin).inverted().transform(_disp(fig, f[0], f[1]))
    ann.xyann = (float(tail[0]), float(tail[1]))
    ann.set_annotation_clip(False)


def _stem(fname):
    if not isinstance(fname, (str, bytes, _os.PathLike)):
        return ""
    return _os.path.splitext(_os.path.basename(_os.fsdecode(fname)))[0]


def _run(fig, stem, steps):
    for what, step in steps:
        try:
            step(fig)
        except Exception as exc:  # An adjustment must never stop the script itself.
            _warnings.warn("adjustment skipped (%s, %s): %s" % (stem, what, exc), stacklevel=3)


def _crop(kwargs, frame):
    # bbox_inches="tight" would re-crop around the edits; keep the page the editor showed.
    if frame is None or kwargs.get("bbox_inches", _mpl.rcParams["savefig.bbox"]) != "tight":
        return kwargs
    return dict(kwargs, bbox_inches=_Bbox.from_extents(*frame))


def _scripts_on_stack():
    import inspect

    out, f = set(), inspect.currentframe().f_back.f_back
    while f is not None:
        out.add(_os.path.abspath(f.f_code.co_filename))
        f = f.f_back
    return out


def _install(table, block, frames):
    # One hook per process. Each block registers its table under (script, block), so
    # a notebook or a script importing another keeps every script's adjustments, and
    # re-running a script replaces its own entry rather than stacking on it.
    script = _os.path.abspath(globals().get("__file__") or "<no-file>")
    registry = _mfig.Figure.__dict__.get("_tavotto_adjust_registry")
    if registry is None:
        registry, frame_of = {}, {}
        real = _mfig.Figure.savefig

        def savefig(self, fname, *args, **kwargs):
            stem = _stem(fname)
            if stem:
                self._tavotto_saved = True
            hits = sorted(k for k, t in registry.items() if stem in t)
            if len({k[0] for k in hits}) > 1:
                # Two scripts claim this figure name: use the one that is running.
                live = _scripts_on_stack()
                hits = [k for k in hits if k[0] in live]
            if len({k[0] for k in hits}) > 1:
                _warnings.warn("adjustments skipped (%s): claimed by two scripts" % stem, stacklevel=2)
            elif hits:
                if getattr(self, "_tavotto_adjusted", None) != stem:
                    self._tavotto_adjusted = stem
                    for key in hits:
                        self._tavotto_frame = frame_of[key].get(stem)
                        _run(self, stem, registry[key][stem])
                    self._tavotto_frame = None
                kwargs = _crop(kwargs, frame_of[hits[-1]].get(stem))
            return real(self, fname, *args, **kwargs)

        _mfig.Figure._tavotto_adjust_registry = registry
        _mfig.Figure._tavotto_adjust_frames = frame_of
        _mfig.Figure.savefig = savefig
    registry[(script, block)] = table
    _mfig.Figure._tavotto_adjust_frames[(script, block)] = frames


def _watch_show(block, shown):
    # Figures that are shown but never saved have no name of their own: they are
    # numbered in the order they first appear at plt.show().
    import matplotlib.pyplot as plt

    script = _os.path.abspath(globals().get("__file__") or "<no-file>")
    shown_of = _mfig.Figure.__dict__.get("_tavotto_adjust_shown")
    if shown_of is None:
        shown_of, order = {}, []
        real = plt.show
        registry = _mfig.Figure._tavotto_adjust_registry

        def show(*args, **kwargs):
            nums = plt.get_fignums()
            current = plt.gcf().number if nums else None
            for num in nums:
                fig = plt.figure(num)
                if getattr(fig, "_tavotto_saved", False):
                    continue
                if not any(fig is f for f in order):
                    order.append(fig)
                i = next(k for k, f in enumerate(order) if f is fig)
                hits = [k for k in sorted(shown_of) if i in shown_of[k]]
                if hits and getattr(fig, "_tavotto_adjusted", None) is None:
                    fig._tavotto_adjusted = shown_of[hits[0]][i]
                    for key in hits:
                        _run(fig, shown_of[key][i], registry[key][shown_of[key][i]])
            if current is not None:
                plt.figure(current)
            return real(*args, **kwargs)

        _mfig.Figure._tavotto_adjust_shown = shown_of
        plt.show = show
    shown_of[(script, block)] = shown
"""

#: 钩子本身：不管哪条调整都要带。
_RUNTIME_ROOTS = ("_install", "_run", "_stem", "_crop", "_scripts_on_stack")


def _needed(steps_text: str) -> str:
    """`_RUNTIME` 里这次用得到的部分（按名字引用求闭包，定义顺序不变）。"""
    tree = ast.parse(_RUNTIME)
    lines = _RUNTIME.splitlines(keepends=True)
    defs: dict[str, str] = {}
    head: list[str] = []
    for node in tree.body:
        src = "".join(lines[node.lineno - 1 : node.end_lineno])
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs[node.name] = src
        else:
            head.append(src)
    words = re.compile(r"\b_[A-Za-z_]+\b")
    keep = set(_RUNTIME_ROOTS) | {w for w in words.findall(steps_text) if w in defs}
    frontier = list(keep)
    while frontier:
        for w in words.findall(defs[frontier.pop()]):
            if w in defs and w not in keep:
                keep.add(w)
                frontier.append(w)
    return "".join(head) + "\n\n" + "\n\n".join(src for k, src in defs.items() if k in keep)


# -------------------------------- 组装与插入 --------------------------------
@dataclass
class Adjusted:
    """一次生成的结果。`data` 没写进任何调整时就是原字节。"""

    data: bytes
    #: 写进脚本的条数
    written: int = 0
    #: 没写进去的 override（原样的 patch 字典，verify 时交给一次性 worker 重放）
    residual: list[dict] = field(default_factory=list)
    #: 给人看的清单：`{gid, prop, label}`
    skipped: list[dict] = field(default_factory=list)
    #: 写进脚本的图内副本 gid（创建次序）：新脚本里它们是普通的 `ax.text`、没有副本 gid，
    #: 验证要按这个次序把热态的副本 gid 对到新脚本里的序号名（`app._alias_baked_copies`）
    copies: list[str] = field(default_factory=list)


def _effective(patches: list[dict]) -> list[dict]:
    """重复的 (gid, prop) 取最后一条（与 `effectiveOverride` 同一口径），保持首次出现的次序。"""
    last: dict[tuple, dict] = {}
    for p in patches:
        last[(p.get("gid"), p.get("prop"))] = p
    seen, out = set(), []
    for p in patches:
        key = (p.get("gid"), p.get("prop"))
        if key not in seen:
            seen.add(key)
            out.append(last[key])
    return out


def _cmap_group_reaches_beyond(el: dict | None, manifest: dict, mine: str | None) -> bool:
    """色条的 cmap 组（manifest 的 `cmap_group_gids`，引擎 `scale_colorbars` / `scale_siblings` 的结构性成员表）里，
    有没有别的 mappable（`mine` 是这条自己的 mappable gid）的色条：同一个 mappable 的几条色条跟着 `set_cmap` 一起变，别的（独立的）不会。"""
    els = {e["gid"]: e for e in manifest.get("elements", [])}
    return any(
        (o := els.get(g)) is not None
        and o.get("role") == "colorbar"
        and o.get("mappable_gid") != mine
        for g in (el or {}).get("cmap_group_gids") or ()
    )


def _cmap_group(p: dict, els: dict) -> str:
    """cmap 这一组的名字：色条取它的 mappable，图像就是自己。"""
    return (els.get(p["gid"]) or {}).get("mappable_gid") or p["gid"]


def _translate(
    patches: list[dict], manifest: dict, encoding: str
) -> tuple[list[tuple[str, str]], list[dict]]:
    """(写得进的 `[(gid.prop, 语句)]`, 写不进的 patch)。"""
    els = {e["gid"]: e for e in manifest.get("elements", [])}
    steps: list[tuple[dict, str]] = []
    residual: list[dict] = []
    refresh: dict[int, str] = {}
    tags: dict[int, str | None] = {}
    made = {str(p["gid"]) for p in patches if p["prop"] == COPY_PROP}
    for p in patches:
        try:
            el = els.get(p["gid"])
            if _COPY_GID.match(str(p["gid"])) and p["gid"] not in made:
                raise _Unsupported(
                    p["gid"]
                )  # 没有创建记录的副本修改：引擎里是「元素不存在」，留作重放照报
            stmt = _statement(el, p["gid"], p["prop"], p["value"], patches, encoding, manifest)
            cls = _cls_of(p["gid"], el) or ""
            tags[id(p)] = _kind(cls, p["prop"])
            if extra := _refresh(el, p["gid"], cls, p["prop"], patches, manifest, encoding):
                refresh[id(p)] = extra
            steps.append((p, stmt))
        except (_Unsupported, KeyError, TypeError, ValueError, IndexError):
            residual.append(p)
    # 整页 / 子图大小留给重放时，靠落笔那一刻的刻度算的几条跟着留下（`_RESHAPES_AXES`）；轴的大小要到
    # 画出来才定的（`_sized_at_draw`）也一样
    reshaped = any(r["prop"] in _RESHAPES_AXES for r in residual)

    def coords_held(gid: str) -> bool:
        """这条轴的坐标范围 / 翻转留作重放了：引擎里它们排在刻度定位之前（REST < 刻度模型），脚本里写进去的刻度
        却先于重放，按旧范围冻结。"""
        m = re.match(r"(axes_\d+)(?:\.|$)", gid)
        return m is not None and any(
            r["prop"] in _MOVES_COORDS and r["gid"] == m.group(1) for r in residual
        )

    def late(p: dict) -> bool:
        return _snapshots_ticks(p, patches) and (
            reshaped or _sized_at_draw(p["gid"], els) or coords_held(p["gid"])
        )

    def reshaped_for(gid: str) -> bool:
        """留作重放的几何键会不会动到这个 gid 所在的子图：`position` 只动它自己的子图，别的（整页大小、图幅、
        色条的方向 / 延伸会挤宿主轴）动全图。"""
        m = re.match(r"(axes_\d+)(?:\.|$)", gid)
        return any(
            r["prop"] in _RESHAPES_AXES
            and (r["prop"] != "position" or m is None or r["gid"] == m.group(1))
            for r in residual
        )

    def places_by_geometry(p: dict) -> bool:
        """figure 分数换算成别的坐标（数据坐标、轴分数）的语句：落笔那一刻的几何与热态里画完之后的几何不同时，
        换算出来的点就落错。引擎里几何先于它们（`_apply_rank` 前几档）。"""
        if p["prop"] not in _PLACED_BY_FRACTION:
            return False
        return reshaped_for(p["gid"]) or _sized_at_draw(p["gid"], els)

    # 图元自己的色阶（cmap / vmin / vmax）留作重放时，挂在它上面的色条跟着重画（`_draw_all` 重置色条轴的刻度定位、
    # 轴线与刻度样式）：写在那条色条轴上的修改（轴本身、刻度、色条自己的字段）也一起留下，重放时排在这次重画之后。
    # 色条自己的 cmap / vmin / vmax 留作重放不算（④ 的口径：色条轴上的照写）
    held_maps = {
        r["gid"]
        for r in residual
        if r["prop"] in ("cmap", *_CLIM) and _cls_of(r["gid"], els.get(r["gid"])) != "colorbar"
    }
    held_axes = {
        e["gid"][: -len(".colorbar")]
        for e in els.values()
        if e["gid"].endswith(".colorbar") and e.get("mappable_gid") in held_maps
    }

    def on_colorbar_axis(p: dict) -> bool:
        m = re.match(r"(axes_\d+)(?:\.|$)", p["gid"])
        return m is not None and m.group(1) in held_axes

    for p, stmt in list(steps):
        if on_colorbar_axis(p):
            steps.remove((p, stmt))
            residual.append(p)
    order = {id(p): k for k, p in enumerate(patches)}
    changed = True
    while (
        changed
    ):  # 留下一条会牵出另一条（坐标范围留下 -> 同轴的刻度语句留下，反之亦然）：迭代到不再变化
        n = len(residual)
        # 一条轴上的定位与格式是一整套：其中一条留下，这条轴的其余几条也留下（不然写进脚本的那一份缺了它）
        held = {p["gid"] for p, _s in steps if late(p) and p["prop"] != "text"}
        for p, stmt in list(steps):
            if late(p) or places_by_geometry(p) or (p["gid"] in held and p["prop"] in _TICK_MODEL):
                steps.remove((p, stmt))
                residual.append(p)
        # 按 figure 分数定位的语句留作重放时，同一子图上在引擎应用顺序里排在它**之后**的改坐标的修改也留下：重放永远排在
        # 脚本之后，写进脚本的会变成「先改坐标、后定位」，引擎里同档按列表序，文字 / 箭头会随反转、换范围镜像。引擎里
        # 位置 / 比例 / 刻度类型等排在 `_RANK_REST` 之前，它们写进脚本照旧排在重放之前，不受影响；
        # 排在 REST 档里的只有这几条改坐标的键，按列表序比
        placed = [
            (order[id(r)], m.group(1))
            for r in residual
            if r["prop"] in _PLACED_BY_FRACTION and id(r) in order
            if (m := re.match(r"(axes_\d+)(?:\.|$)", r["gid"]))
        ]
        for p, stmt in list(steps):
            if p["prop"] in _MOVES_COORDS and any(
                p["gid"] == ax and order[id(p)] > k for k, ax in placed
            ):
                steps.remove((p, stmt))
                residual.append(p)
        _hold_copies(steps, residual, patches)
        changed = len(residual) != n
    # 留作 override 的「广播」条目在重放时排在窄条目之前、会盖掉写进脚本的窄条目：
    # 同一 prop、gid 是它子孙的，一起留下（ADR 0094 §五.3）
    # 色条与它的 mappable 的 cmap 是互相广播的一组（ADR 0135：组里任一条改 cmap = 整组改，文档序靠后的赢）：
    # 一条留作重放，同组另一条写进脚本就会反过来被它盖掉（重放永远排在脚本之后），所以同组一起留下
    held = {_cmap_group(r, els) for r in residual if r["prop"] == "cmap"}
    kept: list[tuple[dict, str]] = []
    for p, stmt in steps:
        if p["prop"] == "cmap" and _cmap_group(p, els) in held:
            residual.append(p)
            continue
        if any(
            r["prop"] == p["prop"] and p["gid"].startswith(r["gid"] + ".") for r in residual
        ) or any((p["gid"], p["prop"]) in _narrower(r, els) for r in residual):
            residual.append(p)
        else:
            kept.append((p, stmt))
    # 上面的广播规则可能又把某条副本上的修改留了下来：副本整件事一起留下
    while _hold_copies(kept, residual, patches):
        pass
    # 改名：对象按原 label 认，所以同一个对象上还有留作重放的修改（拖动带的 `identity` 是按**改名前**的 label 算的，
    # 改名写进脚本后重放里认不出它）时，改名也留作重放；子孙 gid 上的也算
    for p, stmt in list(kept):
        if tags.get(id(p)) == "rename" and any(
            r["gid"] == p["gid"] or r["gid"].startswith(p["gid"] + ".") for r in residual
        ):
            kept.remove((p, stmt))
            residual.append(p)
    # 同一张图上的所有改名合成一条：先把每个对象都按原 label / 位置认出来，再统一改。分开写的话第一条改完，
    # 后一条按原 label 找（A→B、B→C）就会撞上刚改好的那个。语句从 patch 和选择器现拼，不从语句文本里切
    renames = [(p, stmt) for p, stmt in kept if tags.get(id(p)) == "rename"]
    if renames:
        pairs = [
            f"(lambda: {_selector(p['gid'], els.get(p['gid']))}, {_literal(str(p['value']), encoding)})"
            for p, _s in renames
        ]
        merged = {"gid": " ".join(p["gid"] for p, _s in renames), "prop": "label"}
        tags[id(merged)] = "rename"
        kept = [e for e in kept if e not in renames] + [
            (merged, "_rename(fig, [" + ", ".join(pairs) + "])")
        ]
    # 改了源对象、图例里跟着它走的示意线要重新派生：整份重排（第 1 档），源对象的修改排在它前面
    for p, _stmt in list(kept):
        if id(p) in refresh:
            syn = {"gid": p["gid"], "prop": "legend_refresh"}
            tags[id(syn)] = "rebuild"
            kept.append((syn, refresh[id(p)]))
    # 示意线样式要先脱开跟随（第 2 档）：引擎里第一条 handle_* 落下的那一刻就脱开
    for p, _stmt in list(kept):
        if p["prop"] in _HANDLE_PROPS and (m := _LEGEND_TEXT.match(p["gid"])):
            d = f"_legend_detach(_ax(fig, {int(m.group(1))}), {int(m.group(2))})"
            syn = {"gid": p["gid"], "prop": "binding"}
            tags[id(syn)] = "detach"
            kept.append((syn, d))
    kept.sort(key=lambda s: 0.5 if id(s[0]) in refresh else _rank(s[0]["prop"], tags.get(id(s[0]))))
    out: list[tuple[str, str]] = []
    first: dict[str, int] = {}
    for p, stmt in kept:
        what = f"{p['gid']}.{p['prop']}"
        dedup = tags.get(id(p)) in ("rebuild", "detach", "tick_model", "stroke")
        # 同一条重建 / 同一次脱开 / 同一轴的刻度模型 / 同一段文字的描边：只落一回，名字记在一起（别的语句不合并：色条的 cmap 与图像的 cmap 写出
        # 同一句，中间隔着另一个值时合并会让后一句落空）
        if dedup and stmt in first:
            k = first[stmt]
            out[k] = (out[k][0] + " " + what, stmt)
            continue
        first[stmt] = len(out)
        out.append((what, stmt))
    # 留作重放的 patch **一律按原始列表顺序**交出去：引擎重放同档按列表序（`overrides._apply_rank` 是稳定排序），而上面
    # 各条「跟着留下」的规则是逐轮追加的，追加次序不是用户做这些修改的次序（拖 A → 反转 → 拖 B，级联后会变成 A、B、反转）
    order = {id(p): k for k, p in enumerate(patches)}
    residual.sort(key=lambda r: order.get(id(r), len(patches)))
    return out, residual


def _frame_inches(manifest: dict) -> tuple[float, float, float, float] | None:
    """manifest 的 `frame` → 这张图的裁切框（英寸，左下为原点），没有裁切回 None。

    `bbox_inches="tight"` 的框只有格式自己的 renderer 量得准，读它要私有 API（ADR 0094 Q10 不许），
    所以把界面上这张图的那个框写进块里，而不是到时候再量一遍。
    """
    frame = manifest.get("frame")
    if not isinstance(frame, dict) or not frame.get("active", True):
        return None
    box = frame.get("savefig_in")
    if not isinstance(box, list) or len(box) != 4:
        return None
    return tuple(float(v) for v in box)


def _block(
    stem: str,
    steps: list[tuple[str, str]],
    ordinal: int,
    indent: str,
    version: str,
    frame: tuple[float, float, float, float] | None,
    shown_index: int | None,
) -> str:
    fns, entries = [], []
    for k, (what, stmt) in enumerate(steps):
        fns.append(f"def _tavotto_{k}(fig):  # {ascii(what)[1:-1]}\n    {stmt}\n")
        entries.append(f"        ({ascii(what)}, _tavotto_{k}),\n")
    watch = (
        f"_watch_show({ordinal}, {{{shown_index}: {ascii(stem)}}})\n"
        if shown_index is not None
        else ""
    )
    inner = (
        _needed("\n".join(s for _w, s in steps) + watch)
        + "\n\n"
        + "\n\n".join(fns)
        + f"\n\n_install({{\n    {ascii(stem)}: [\n{''.join(entries)}    ],\n}}, {ordinal}, "
        + (f"{{{ascii(stem)}: {frame!r}}})\n" if frame else "{})\n")
        + watch
    )
    inner = inner.replace("    ", indent)
    body = "".join((indent + ln if ln.strip() else "") + "\n" for ln in inner.splitlines())
    return (
        f"{BEGIN}\n"
        f"# Edits made in {brand.PRODUCT_NAME} {version}, applied to the figure at savefig().\n"
        "# Nothing else in this script was changed; delete this block to undo them.\n"
        f"def _tavotto_adjust():\n{body}\n\n_tavotto_adjust()\ndel _tavotto_adjust\n{END}\n"
    )


def _detect(src: bytes) -> tuple[str, str, str]:
    """(编码, 主导换行, 缩进)。解不开、或解码再编码回不到原字节 → 拒绝，不猜。"""
    try:
        encoding, _first = tokenize.detect_encoding(io.BytesIO(src).readline)
        text = src.decode(encoding)
        if text.encode(encoding) != src:
            raise ValueError("not byte-exact")
    except (SyntaxError, UnicodeError, LookupError, ValueError) as exc:
        raise ScriptAdjustError("script_export_unreadable", str(exc)) from exc
    bom = src.startswith(codecs.BOM_UTF8)
    crlf, lf = src.count(b"\r\n"), src.count(b"\n")
    newline = "\r\n" if crlf and crlf * 2 >= lf else "\n"
    indent = "    "
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text.lstrip("﻿")).readline):
            if tok.type == tokenize.INDENT:
                indent = tok.string
                break
    except (tokenize.TokenError, IndentationError):
        pass
    return ("utf-8-sig" if bom else encoding), newline, indent


def _insert_line(text: str) -> int:
    """块插在第几行之后（0 起）：已有块的末尾，否则 docstring / `__future__` / 开头连续 import 之后。

    一个都没有时也要让过 shebang 与编码声明——它们只在前两行才算数。
    """
    lines = text.split("\n")
    marks = [i + 1 for i, ln in enumerate(lines) if ln.rstrip("\r") == END]
    if marks:
        return marks[-1]
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        raise ScriptAdjustError("script_export_unreadable", str(exc)) from exc
    after = 0
    for node in tree.body:
        is_doc = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and after == 0
        )
        if not is_doc and not isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        after = node.end_lineno or after
    if after == 0:
        while after < min(2, len(lines)) and lines[after].startswith("#"):
            first = lines[after]
            if not (first.startswith("#!") or re.search(r"coding[:=]", first)):
                break
            after += 1
    return after


def _insert(src: bytes, block: str, encoding: str, newline: str) -> bytes:
    """**只动插入的那一段**：块外的字节一个不改（混合换行也原样）。"""
    text = src.decode(encoding)
    at = _insert_line(text)
    offset = 0
    for _ in range(at):
        k = text.find("\n", offset)
        if k < 0:
            offset = len(text)
            break
        offset = k + 1
    body = block.rstrip("\n").replace("\n", newline) + newline
    seg = (newline * 2 if at else "") + body + newline * 2
    if at and offset == len(text) and not text.endswith("\n"):
        seg = newline + seg  # 最后一行没有换行符
    out = text[:offset] + seg + text[offset:]
    try:
        ast.parse(out)
        data = out.encode(encoding)
    except (SyntaxError, ValueError) as exc:
        raise ScriptAdjustError("script_export_unreadable", str(exc)) from exc
    return data


def build(
    src: bytes,
    stem: str,
    patches: list[dict],
    manifest: dict,
    *,
    version: str,
    shown_index: int | None = None,
) -> Adjusted:
    """`src` 是脚本原字节，`manifest` 是这张图此刻的 manifest（要用元素的 role / label / 图例参数）。

    `shown_index`：这张图没有 savefig 时是第几张只 `plt.show()` 的图（0 起），块改挂在 `plt.show` 上。
    """
    encoding, newline, indent = _detect(src)
    patches = _effective([p for p in patches if isinstance(p, dict)])
    steps, residual = _translate(patches, manifest, encoding)
    labels = {e["gid"]: e.get("label") or "" for e in manifest.get("elements", [])}
    skipped = [
        {"gid": p["gid"], "prop": p["prop"], "label": labels.get(p["gid"], "")} for p in residual
    ]
    if not steps:
        _insert_line(src.decode(encoding))  # 读不懂的脚本一样要报，不静默交一份原样
        return Adjusted(src, 0, residual, skipped)
    ordinal = src.decode(encoding).count(END)
    block = _block(stem, steps, ordinal, indent, version, _frame_inches(manifest), shown_index)
    written = len(patches) - len(residual)
    copies = [w.removesuffix("." + COPY_PROP) for w, _s in steps if w.endswith("." + COPY_PROP)]
    return Adjusted(_insert(src, block, encoding, newline), written, residual, skipped, copies)
