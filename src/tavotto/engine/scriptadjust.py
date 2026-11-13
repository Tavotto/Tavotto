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
#: 这样「`HANDLERS` 的每个键都表过态」才是一句能断言的话（`tests/test_script_adjust.py`）。
_ROLE_CLS = {
    "title": "text",
    "axis_label": "text",
    "text": "text",
    "legend_text": "legend_text",
    "line": "line",
    "legend": "legend",
    "axes": "axes",
    "ticks": "ticks",
    "figure": "figure",
    "scatter": "collection",
    "fill": "collection",
    "collection": "collection",
    "linecoll": "linecoll",
    "bar_series": "bar_series",
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

#: (cls 键, prop) → 一条公开 API 语句。`{o}` 对象、`{v}` 值、`{x}` 值的元素。
#: 走专用生成（位置、图例字号）的几个 prop 也在表里占一格，模板写 `None`。
WRITES: dict[tuple[str, str], str | None] = {
    **{("text", p): t for p, t in _TEXT.items()},
    **{("legend_text", p): t for p, t in _TEXT.items() if p != "visible"},
    ("text", "pos_frac"): None,
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
    ("legend", "visible"): "{o}.set_visible({v})",
    ("legend", "frameon"): "{o}.set_frame_on({v})",
    ("legend", "loc"): "{o}.set_loc({v})",
    ("legend", "loc_frac"): None,
    ("legend", "fontsize"): None,
    ("legend", "facecolor"): "{o}.get_frame().set_facecolor({v})",
    ("legend", "edgecolor"): "{o}.get_frame().set_edgecolor({v})",
    ("legend", "framealpha"): "{o}.get_frame().set_alpha({v})",
    ("legend", "frame_linewidth"): "{o}.get_frame().set_linewidth({v})",
    ("axes", "xlim"): "{o}.set_xlim({x})",
    ("axes", "ylim"): "{o}.set_ylim({x})",
    ("axes", "xscale"): "{o}.set_xscale({v})",
    ("axes", "yscale"): "{o}.set_yscale({v})",
    ("axes", "invert_x"): "{o}.xaxis.set_inverted({v})",
    ("axes", "invert_y"): "{o}.yaxis.set_inverted({v})",
    ("axes", "facecolor"): "{o}.set_facecolor({v})",
    ("axes", "grid_x"): "{o}.grid(visible={v}, axis='x')",
    ("axes", "grid_y"): "{o}.grid(visible={v}, axis='y')",
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
    ("figure", "facecolor"): "{o}.set_facecolor({v})",
    **{("collection", p): t for p, t in _SHAPE.items()},
    **{("collection", p): t for p, t in _KEYED.items()},
    ("linecoll", "color"): "{o}.set_color({v})",
    ("linecoll", "linewidth"): "{o}.set_linewidth({v})",
    ("linecoll", "linestyle"): "{o}.set_linestyle({v})",
    ("linecoll", "alpha"): "{o}.set_alpha({v})",
    ("linecoll", "visible"): "{o}.set_visible({v})",
    ("linecoll", "zorder"): "{o}.set_zorder({v})",
    **{("bar_series", p): t for p, t in _SHAPE.items()},
    **{("bar_series", p): t for p, t in _KEYED.items()},
    ("bar_series", "hatch"): "_with_legend({o}, lambda _a: _a.set_hatch({v}))",
    **{("patch", p): t for p, t in _SHAPE.items()},
    ("patch", "linestyle"): "{o}.set_linestyle({v})",
    ("patch", "hatch"): "{o}.set_hatch({v})",
    ("image", "cmap"): "{o}.set_cmap({v})",
    ("image", "vmin"): "{o}.norm.vmin = {v}",
    ("image", "vmax"): "{o}.norm.vmax = {v}",
    ("image", "alpha"): "{o}.set_alpha({v})",
    ("image", "visible"): "{o}.set_visible({v})",
    ("image", "interpolation"): "{o}.set_interpolation({v})",
    ("image", "zorder"): "{o}.set_zorder({v})",
}

#: 这一版不写的键（类 → 空格分隔的 prop）。留在表里而不是「不在 WRITES 就算了」：`HANDLERS` 新增
#: 键忘了表态时用例会红。其中色条 `orientation` / `extend`、3D `axis_arrows` 一族只能靠私有 API，
#: 永远不写（ADR 0094 Q10）；其余是公开 API 走得通、但这一版没逐键验证等价。
NOT_WRITABLE: dict[str, str] = {
    "text": "linespacing labelpad stroke_enabled stroke_color stroke_width "
    "bbox_visible bbox_facecolor bbox_edgecolor bbox_linewidth bbox_alpha bbox_pad bbox_rounded",
    "legend_text": "visible pos_frac linespacing labelpad stroke_enabled stroke_color "
    "stroke_width bbox_visible bbox_facecolor bbox_edgecolor bbox_linewidth bbox_alpha bbox_pad "
    "bbox_rounded binding handle_color handle_linestyle handle_linewidth handle_marker "
    "handle_markersize",
    "arrowpatch": "color linewidth mutation_scale alpha visible zorder endpoints_frac arrowstyle "
    "linestyle",
    "patch": "pos_frac size_frac hatch_linewidth hatchcolor fill",
    "artist": "pos_frac visible zorder",
    "line": "label pos_frac",
    "legend": "loc_anchor title title_fontsize entry_order ncol borderpad labelspacing "
    "handlelength handletextpad columnspacing frame_rounded",
    "axes": "visible position aspect grid_color grid_linestyle grid_linewidth grid_alpha "
    "ticks_bottom ticks_top ticks_left ticks_right spine_color spine_linewidth elev azim roll "
    "grid_visible proj_type axis_arrows arrow_color arrow_width arrow_head axline_color "
    "axline_width pane_visible pane_color",
    "image": "gradient_color origin",
    "ticklabel": "text",
    "ticks": "visible width minor_length minor_width major_mode major_step major_values "
    "minor_visible minor_mode minor_step format minor_format",
    "figure": "size_mm frame transparent",
    "colorbar": "label cmap vmin vmax tick_fontsize tick_color outline_visible outline_width "
    "visible orientation extend",
    "bar_series": "hatch_linewidth hatchcolor bar_width label",
    "errorbar": "color linewidth capsize cap_thickness alpha visible",
    "stem_series": "color linewidth linestyle marker markersize alpha visible zorder label",
    "collection": "label gradient linestyle hatch size marker cmap vmin vmax",
    "bar": "facecolor edgecolor linewidth linestyle hatch hatch_linewidth hatchcolor fill alpha "
    "visible zorder",
}


# -------------------------------- gid → 表达式 --------------------------------
_AX = re.compile(r"^axes_(\d+)(?:\.(.+))?$")
_INDEXED = {
    "texts": "{ax}.texts",
    "images": "{ax}.images",
    "patches": "{ax}.patches",
    "scatter": "{ax}.collections",
    "fill": "{ax}.collections",
    "collection": "{ax}.collections",
    "linecoll": "{ax}.collections",
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
    m = _AX.match(gid)
    if m is None:
        raise _Unsupported(gid)
    ax, rest = f"_ax(fig, {int(m.group(1))})", m.group(2)
    if rest is None:
        return ax
    if rest in ("xlabel", "ylabel"):
        return f"_AxisLabel({ax}, {rest[0]!r})"
    if rest == "title":
        return f"{ax}.title"
    if rest in ("xticks", "yticks"):
        return f"_TickSet({ax}, {rest[0]!r})"
    if rest == "legend":
        return f"_legend({ax})"
    if mm := re.fullmatch(r"legend\.texts_(\d+)", rest):
        return f"_at(_legend({ax}).get_texts(), {int(mm.group(1))})"
    if mm := re.fullmatch(r"lines_(\d+)", rest):
        return f"_line({ax}, {int(mm.group(1))}, {_script_label(el)!r})"
    if mm := re.fullmatch(r"barseries_(\d+)", rest):
        return f"_bars({ax}, {int(mm.group(1))})"
    if (mm := re.fullmatch(r"([a-z]+)_(\d+)", rest)) and mm.group(1) in _INDEXED:
        return f"_at({_INDEXED[mm.group(1)].format(ax=ax)}, {int(mm.group(2))})"
    raise _Unsupported(gid)


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


#: `_legend_refont` 要的整份图例参数（来自 manifest 的 editable）。
_LEGEND_SPEC = (
    "loc",
    "loc_anchor",
    "fontsize",
    "frameon",
    "visible",
    "title",
    "title_fontsize",
    "facecolor",
    "framealpha",
    "edgecolor",
    "ncol",
    "borderpad",
    "labelspacing",
    "handlelength",
    "handletextpad",
    "columnspacing",
    "frame_linewidth",
    "frame_rounded",
)


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


def _statement(
    el: dict | None, gid: str, prop: str, value, patches, encoding: str, manifest: dict
) -> str:
    role = (el or {}).get("role") or ("figure" if gid == "figure" else "")
    cls = _ROLE_CLS.get(role)
    if cls is None or (cls, prop) not in WRITES:
        raise _Unsupported(prop)
    obj = _selector(gid, el)
    if prop == "pos_frac":
        return _position(el, gid, obj, value)
    if prop == "fontfamily":
        return _font(cls, obj, value, manifest, encoding)
    if (cls, prop) == ("legend", "loc_frac"):
        return f"_legend_to({obj}, fig, {float(value[0])!r}, {float(value[1])!r})"
    if (cls, prop) == ("legend", "fontsize"):
        m = _AX.match(gid)
        if m is None or m.group(2) != "legend":
            raise _Unsupported(gid)
        spec = {f["prop"]: f.get("value") for f in (el or {}).get("editable", [])}
        for p in patches:  # 同一个图例上别的、能放进 legend() 参数的编辑一起进去
            if p["gid"] == gid and p["prop"] in spec:
                spec[p["prop"]] = p["value"]
        spec["fontsize"] = value
        spec = {k: spec.get(k) for k in _LEGEND_SPEC}
        return f"_legend_refont(_ax(fig, {int(m.group(1))}), {_literal(spec, encoding)})"
    tmpl = WRITES[(cls, prop)]
    assert tmpl is not None
    return tmpl.format(
        o=obj,
        v=_literal(value, encoding),
        x=", ".join(_literal(x, encoding) for x in (value or ())) if "{x}" in tmpl else "",
    )


def _rank(prop: str, statement: str) -> tuple[int, int]:
    """步骤顺序：与 `overrides._apply_rank` 同序的那几档（对账见 `tests/test_script_adjust.py`）。

    引擎的函数要看 artist 类型、父进程 import 不了，而写得进脚本的键里只有坐标轴刻度类型这一档
    排在普通属性之前。重建图例会换掉图例对象，排在同一档最前。
    """
    return (0 if prop in ("xscale", "yscale") else 1, 0 if "_legend_refont(" in statement else 1)


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


def _with_legend(item, setter):
    # A restyled artist keeps its legend key in step.
    setter(item)
    label = item.get_label()
    ax = item.axes
    for leg in [ax.get_legend()] + list(ax.figure.legends):
        if leg is None:
            continue
        for handle, text in zip(leg.legend_handles, leg.get_texts()):
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

    def __getattr__(self, name):
        def call(*args, **kwargs):
            for member in self.members:
                getattr(member, name)(*args, **kwargs)

        return call


def _bars(ax, j):
    return _Group(ax, _at(ax.containers, j))


class _TickSet:
    def __init__(self, ax, which):
        self.ax, self.which = ax, which

    def params(self, which="both", **kw):
        self.ax.tick_params(axis=self.which, which=which, **kw)


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


def _legend_refont(ax, spec):
    # legend(fontsize=...) lays the whole box out again, which set_fontsize on
    # each entry does not. Rebuild it from the artists the entries were collected from.
    old = _legend(ax)
    old_texts = old.get_texts()
    labels = [t.get_text() for t in old_texts]
    found = {}
    for h, lab in zip(*ax.get_legend_handles_labels()):
        found.setdefault(lab, []).append(h)
    if any(len(found.get(lab, [])) != 1 for lab in labels):
        raise _Skip("legend entries were not collected by label")
    kw = dict(
        loc=spec["loc"],
        fontsize=spec["fontsize"],
        ncols=int(spec["ncol"]),
        frameon=spec["frameon"],
        framealpha=spec["framealpha"],
        facecolor=spec["facecolor"],
        edgecolor=spec["edgecolor"],
        fancybox=spec["frame_rounded"],
        borderpad=spec["borderpad"],
        labelspacing=spec["labelspacing"],
        handlelength=spec["handlelength"],
        handletextpad=spec["handletextpad"],
        columnspacing=spec["columnspacing"],
        title=spec["title"] or None,
        title_fontsize=spec["title_fontsize"],
        borderaxespad=old.borderaxespad,
        markerscale=old.markerscale,
        numpoints=old.numpoints,
        scatterpoints=old.scatterpoints,
        shadow=old.shadow,
    )
    if spec["loc_anchor"] is not None:
        kw["bbox_to_anchor"] = tuple(spec["loc_anchor"])
    new = ax.legend([found[lab][0] for lab in labels], labels, **kw)
    new.get_frame().set_linewidth(spec["frame_linewidth"])
    new.set_zorder(old.get_zorder())
    new.set_visible(spec["visible"])
    for n, o in zip(new.get_texts(), old_texts):
        # Only the look: Artist.update_from would copy the transform too.
        n.set_fontproperties(o.get_fontproperties().copy())
        n.set_color(o.get_color())
        n.set_alpha(o.get_alpha())
        n.set_fontsize(spec["fontsize"])


def _math_custom(family):
    # Formulas use one font set per process, so the last font chosen wins.
    name = family
    if family in ("serif", "sans-serif", "cursive", "fantasy", "monospace"):
        names = _mpl.rcParams["font." + family]
        name = names[0] if names else None
    if not name:
        return False
    try:
        _mpl.rcParams["mathtext.rm"] = name
        _mpl.rcParams["mathtext.it"] = name + ":italic"
        _mpl.rcParams["mathtext.bf"] = name + ":bold"
        _mpl.rcParams["mathtext.sf"] = name
    except (ValueError, KeyError):
        return False
    return True


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


def _translate(
    patches: list[dict], manifest: dict, encoding: str
) -> tuple[list[tuple[str, str]], list[dict]]:
    """(写得进的 `[(gid.prop, 语句)]`, 写不进的 patch)。"""
    els = {e["gid"]: e for e in manifest.get("elements", [])}
    steps: list[tuple[dict, str]] = []
    residual: list[dict] = []
    for p in patches:
        try:
            steps.append(
                (
                    p,
                    _statement(
                        els.get(p["gid"]),
                        p["gid"],
                        p["prop"],
                        p["value"],
                        patches,
                        encoding,
                        manifest,
                    ),
                )
            )
        except (_Unsupported, KeyError, TypeError, ValueError, IndexError):
            residual.append(p)
    # 留作 override 的「广播」条目在重放时排在窄条目之前、会盖掉写进脚本的窄条目：
    # 同一 prop、gid 是它子孙的，一起留下（ADR 0094 §五.3）
    kept: list[tuple[dict, str]] = []
    for p, stmt in steps:
        if any(r["prop"] == p["prop"] and p["gid"].startswith(r["gid"] + ".") for r in residual):
            residual.append(p)
        else:
            kept.append((p, stmt))
    kept.sort(key=lambda s: _rank(s[0]["prop"], s[1]))
    return [(f"{p['gid']}.{p['prop']}", stmt) for p, stmt in kept], residual


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
    return Adjusted(_insert(src, block, encoding, newline), len(steps), residual, skipped)
