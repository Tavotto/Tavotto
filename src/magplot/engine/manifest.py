"""元素清单（worker 子进程内使用）。

instrument(state)：build 后调用一次——走 Figure 的 artist 树，
按确定性树序赋 gid（axes_0.title / axes_0.texts_2 / fig.texts_0 …），
把可编辑元素登记进 FigState。

build_manifest(state)：每次渲染后调用——读取元素当前属性值与 bbox
（figure 分数坐标、top-origin），产出发给前端的 manifest dict。
"""
from __future__ import annotations

from matplotlib.axes import Axes
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.container import BarContainer, ErrorbarContainer
from matplotlib.text import Text
from matplotlib.ticker import FormatStrFormatter, ScalarFormatter

from overrides import (ColorbarProxy, FigState, HANDLERS, SeriesGroup, TickLabel,
                       TickSet, _LEGEND_LOCS, _arrow_style, _axis_arrows_on,
                       _boxstyle_info, _cb_axis, _cb_tick_color,
                       _cb_tick_fontsize, _cls_key, _grid_prop, _grid_visible,
                       _legend_entry_order, _legend_loc_name, _spines_get,
                       _stroke_state, _tick0, text_linespacing, to_hex)

CMAPS = ["viridis", "plasma", "inferno", "magma", "cividis", "Greys", "gray",
         "hot", "afmhot", "coolwarm", "RdBu_r", "seismic", "jet", "turbo"]

_SKIP_LABELS = ("_child", "_nolegend_")


def _snippet(text: str, n: int = 18) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _register(state: FigState, gid: str, artist, role: str, label: str,
              draggable: bool = False) -> None:
    artist.set_gid(gid)
    state.index[gid] = artist
    state.elements.append({"gid": gid, "artist": artist, "role": role,
                           "label": label, "draggable": draggable})


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

    # 色条反查：mappable.colorbar → 宿主轴
    cbar_of_ax = {}
    for ax in fig.axes:
        for sm in [*ax.images, *ax.collections]:
            cb = getattr(sm, "colorbar", None)
            if cb is not None:
                cbar_of_ax[cb.ax] = cb
    state.colorbar_axes = set(cbar_of_ax)

    for i, ax in enumerate(fig.axes):
        is3d = getattr(ax, "name", "") == "3d"
        _register(state, f"axes_{i}", ax, "axes3d" if is3d else "axes",
                  "色条轴" if ax in cbar_of_ax else f"子图 {i + 1}")
        if ax in cbar_of_ax:
            _register(state, f"axes_{i}.colorbar", ColorbarProxy(cbar_of_ax[ax]),
                      "colorbar", "色条")
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
            if not t.get_text():
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
        tick_axes = (("x", "X"), ("y", "Y"), ("z", "Z")) if is3d else (("x", "X"), ("y", "Y"))
        for which, cn in tick_axes:
            if getattr(ax, f"{which}axis", None) is None:
                continue
            ts = TickSet(ax, which)
            if ts.labels:
                _register(state, f"axes_{i}.{which}ticks", ts, "ticks",
                          f"{cn} 刻度文字")
            raw = getattr(ax, f"get_{which}ticklabels")()
            for j, t in enumerate(raw):
                if t.get_text():
                    _register(state, f"axes_{i}.{which}ticklabels_{j}",
                              TickLabel(ax, which, j), "ticklabel",
                              f"刻度 “{_snippet(t.get_text())}”")


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


def _image_fields(im) -> list[dict]:
    arr = im.get_array()
    mappable = arr is not None and getattr(arr, "ndim", 0) == 2
    fields = []
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


def _tick_format_name(ts: TickSet) -> str:
    fmt = getattr(ts.ax, f"{ts.which}axis").get_major_formatter()
    if isinstance(fmt, FormatStrFormatter):
        s = getattr(fmt, "fmt", "")
        return s if s in ("%.0f", "%.1f", "%.2f") else "auto"
    if isinstance(fmt, ScalarFormatter) and getattr(fmt, "_powerlimits", None) == (0, 0):
        return "sci"
    return "auto"


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
        {"prop": "format", "type": "enum", "value": _tick_format_name(ts),
         "options": ["auto", "%.0f", "%.1f", "%.2f", "sci"], "group": "刻度线"},
    ]
    if is3d:
        # mplot3d 的刻度朝向由投影决定；label 显隐的 tick_params 键也不含 z
        fields = [f for f in fields if f["prop"] not in ("direction", "visible")]
    return fields


def _axes_fields(ax) -> list[dict]:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    aspect = ax.get_aspect()
    return [
        {"prop": "position", "type": "rect",
         "value": [round(float(v), 4) for v in ax.get_position().bounds]},
        {"prop": "visible", "type": "bool", "value": bool(ax.get_visible())},

        {"prop": "xlim", "type": "pair", "value": [float(x0), float(x1)],
         "group": "数据范围"},
        {"prop": "ylim", "type": "pair", "value": [float(y0), float(y1)],
         "group": "数据范围"},
        {"prop": "xscale", "type": "enum", "value": str(ax.get_xscale()),
         "options": ["linear", "log"], "group": "数据范围"},
        {"prop": "yscale", "type": "enum", "value": str(ax.get_yscale()),
         "options": ["linear", "log"], "group": "数据范围"},
        {"prop": "invert_x", "type": "bool", "value": bool(ax.xaxis_inverted()),
         "group": "数据范围"},
        {"prop": "invert_y", "type": "bool", "value": bool(ax.yaxis_inverted()),
         "group": "数据范围"},
        {"prop": "aspect", "type": "text",
         "value": aspect if isinstance(aspect, str) else str(round(float(aspect), 3)),
         "group": "数据范围"},

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
        {"prop": "spine_color", "type": "color",
         "value": to_hex(_spines_get(ax, lambda s: s.get_edgecolor(), (0, 0, 0, 1))),
         "group": "网格与边框"},
        {"prop": "spine_linewidth", "type": "number",
         "value": round(float(_spines_get(ax, lambda s: float(s.get_linewidth()), 0.8)), 2),
         "min": 0.1, "max": 3, "step": 0.1, "unit": "pt", "group": "网格与边框"},
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
        return _axes3d_fields(artist) if role == "axes3d" else _axes_fields(artist)
    if key == "image":
        return _image_fields(artist)
    if key == "scatter":
        return _collection_fields(artist, with_size=True)
    if key == "fill":
        return _collection_fields(artist, with_size=False)
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

    elements = []
    for el in state.elements:
        artist = el["artist"]
        entry = {"gid": el["gid"], "role": el["role"], "label": el["label"],
                 "draggable": el["draggable"], "editable": _fields_for(el)}
        if el["role"] in ("axes", "axes3d"):
            entry["resizable"] = True  # 前端可拖动/缩放子图占比（override axes position）
            if artist in state.colorbar_axes:
                entry["is_colorbar"] = True
                entry["colorbar_gid"] = f"{el['gid']}.colorbar"
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
                if bb.width <= 0 and bb.height <= 0:
                    continue
                # 水平 / 垂直的扁平线（基线、参考线）单边为 0，垫成可点中的窄条
                entry["bbox"] = _padded_bbox(bb, W, H)
            except Exception:
                continue
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

    w_in, h_in = fig.get_size_inches()
    return {"stem": stem, "size_mm": [round(float(w_in) * 25.4, 2), round(float(h_in) * 25.4, 2)],
            "elements": elements}
