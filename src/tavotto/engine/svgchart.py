"""SVG 计算图表的 worker 侧脚手架（ADR 0166，SG07）。

**只在 worker 进程跑**（import matplotlib——父进程闭包里没有它）。输入是
经 `chartmodel.validate_model` 的模型 + 场景框事实（viewBox/物理 pt/
preserveAspectRatio），输出是**与场景用户坐标 1:1 对齐的 SVG 片段**和
刻度/数据点事实。纪律：

- 纯函数：不读写文件、不碰会话、不执行用户代码；matplotlib 只在
  `rc_context` 的固定 salt 下渲染（同输入 → 同字节）。
- 刻度/定位/格式全部来自 matplotlib 的 locator/formatter——绝不自写刻度规则
  （计划 SG07 禁令；SG01 实验二证明分数与独立公式一致到 1e-9）。
"""

from __future__ import annotations

import io
import re

from tavotto.engine import chartmodel
from tavotto.svgcoord import apply, viewbox_to_pt

#: 片段里允许出现的刻度标签字体大小上界（防御性；matplotlib 默认 10pt）。
_MAX_LABEL_PT = 72.0


def scaffold_fragment(model: dict, frame: dict, *, salt: str = "tavotto") -> dict:
    """模型 + 场景框 → ``{"fragment": bytes, "facts": {...}}``。

    ``frame``：``{"view_box": [x,y,w,h], "w_pt": float, "h_pt": float,
    "preserve": str|None}``——场景的物理映射；片段用同一映射渲染，坐标与
    场景 1:1 对齐（SG01 实验三的嫁接前提）。
    """
    chartmodel.validate_model(model)
    view_box = frame.get("view_box")
    if not isinstance(view_box, list) or len(view_box) != 4:
        raise ValueError("frame.view_box must be [x,y,w,h]")
    w_pt, h_pt = float(frame["w_pt"]), float(frame["h_pt"])
    u2pt = viewbox_to_pt(view_box, w_pt, h_pt, frame.get("preserve"))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.figure

    ax_cfg = model["axes"][0]
    ax_x, ax_y, ax_w, ax_h = (float(v) for v in ax_cfg["rect"])
    pt0 = apply(u2pt, ax_x, ax_y)
    pt1 = apply(u2pt, ax_x + ax_w, ax_y + ax_h)
    # viewBox 若有非零原点，映射后的 rect 以 pt0 为左上、pt1 为右下
    left, right = sorted((pt0[0], pt1[0]))
    top, bottom = sorted((pt0[1], pt1[1]))
    fig = matplotlib.figure.Figure(figsize=(w_pt / 72.0, h_pt / 72.0), dpi=72)
    ax = fig.add_axes(
        [
            left / w_pt,
            1.0 - bottom / h_pt,  # matplotlib 是 y-up 的图分数
            (right - left) / w_pt,
            (bottom - top) / h_pt,
        ]
    )
    ax.set_xlim(*ax_cfg["xlim"])
    ax.set_ylim(*ax_cfg["ylim"])
    if ax_cfg["xscale"] != "linear":
        ax.set_xscale(ax_cfg["xscale"])
    if ax_cfg["yscale"] != "linear":
        ax.set_yscale(ax_cfg["yscale"])
    if ax_cfg.get("invert_x"):
        ax.invert_xaxis()
    if ax_cfg.get("invert_y"):
        ax.invert_yaxis()
    if ax_cfg.get("xlabel"):
        ax.set_xlabel(str(ax_cfg["xlabel"]))
    if ax_cfg.get("ylabel"):
        ax.set_ylabel(str(ax_cfg["ylabel"]))

    points = {}
    categorical = ax_cfg.get("kind") == "categorical"
    cats = ax_cfg.get("categories") or []
    for sr in model["series"]:
        kind = sr["kind"]
        style = dict(sr.get("style") or {})
        if kind == "image":
            grid = model["dataset"].get("grids", {}).get(sr.get("grid", ""))
            if grid is None:
                raise ValueError(f"series {sr['id']} references unknown grid")
            imshow_kwargs = _safe_style(
                {k: v for k, v in style.items() if k in ("cmap", "vmin", "vmax", "alpha")}
            )
            mappable = ax.imshow(grid["values"], origin="upper", **imshow_kwargs)
            fig.colorbar(mappable, ax=ax)
            continue
        xs = model["dataset"]["columns"][sr["x"]]
        ys = model["dataset"]["columns"][sr["y"]]
        yerr_col = sr.get("yerr")
        yerr = model["dataset"]["columns"].get(yerr_col) if yerr_col else None
        if categorical:
            # 类别位置 = 序号；x 列的文本值必须与轴类目逐位一致（不猜顺序）
            if xs != cats:
                raise ValueError("series categories do not match the axis categories")
            xs = list(range(len(cats)))
        label = str(style.pop("label", "") or "")
        if kind == "bar":
            kwargs = _safe_style(
                {
                    k: v
                    for k, v in style.items()
                    if k in ("color", "facecolor", "edgecolor", "alpha", "bar_width")
                }
            )
            if "bar_width" in kwargs:
                kwargs["width"] = kwargs.pop("bar_width")
            if "color" in kwargs and "facecolor" not in kwargs:
                kwargs["facecolor"] = kwargs.pop("color")
            ax.bar(xs, ys, yerr=yerr, capsize=4, label=label or None, **kwargs)
        elif kind == "errorbar":
            kwargs = _safe_style(
                {
                    k: v
                    for k, v in style.items()
                    if k in ("color", "linewidth", "linestyle", "alpha")
                }
            )
            ax.errorbar(xs, ys, yerr=yerr, capsize=4, label=label or None, **kwargs)
        else:
            plot_style = _safe_style(style)
            if kind == "scatter":
                plot_style.setdefault("marker", "o")
                plot_style["linestyle"] = "none"
            ax.plot(
                xs,
                ys,
                linewidth=float(plot_style.pop("linewidth", 2.0) or 2.0),
                label=label or None,
                **plot_style,
            )
    if categorical:
        ax.set_xticks(list(range(len(cats))))
        ax.set_xticklabels(cats)
    fig.canvas.draw()  # realize transforms & locators（exp2 口径）
    # 轴分数（matplotlib 原生、y-up）：display(72dpi=pt) 减轴原点、除轴尺寸。
    # 轴在 display 空间的左/下：left(pt) 与 h_pt - bottom（SVG y-down 的 rect）。
    ax_disp_left = left
    ax_disp_bottom = h_pt - bottom
    ax_w_px, ax_h_px = right - left, bottom - top
    for sr in model["series"]:
        if sr["kind"] == "image":
            continue  # 网格系列的命中事实由图像矩形承担，不做逐点
        xs = model["dataset"]["columns"][sr["x"]]
        ys = model["dataset"]["columns"][sr["y"]]
        if categorical:
            xs = list(range(len(cats)))  # 类别位置 = 序号（与绘制同一映射）
        disp = ax.transData.transform(list(zip(xs, ys)))
        points[sr["id"]] = [
            {"x": (p[0] - ax_disp_left) / ax_w_px, "y": (p[1] - ax_disp_bottom) / ax_h_px}
            for p in disp
        ]

    with matplotlib.rc_context({"svg.fonttype": "none", "svg.hashsalt": salt}):
        buf = io.BytesIO()
        fig.savefig(buf, format="svg")
    text = buf.getvalue().decode("utf-8")
    text = re.sub(r"<dc:date>[^<]*</dc:date>\s*", "", text)
    text = re.sub(r"<dc:creator>.*?</dc:creator>\s*", "", text, flags=re.S)

    facts = {
        "series_points": points,
        "ticks": _tick_facts(ax),
        "axes_rect_user": [ax_x, ax_y, ax_w, ax_h],
    }
    return {"fragment": text.encode("utf-8"), "facts": facts}


def _safe_style(style: dict) -> dict:
    """系列样式 → matplotlib kwargs（闭集已在 chartmodel 校验；这里只做类型钉）。"""
    out = {}
    allowed = {
        "color": str,
        "linestyle": str,
        "marker": str,
        "markersize": float,
        "alpha": float,
        "facecolor": str,
        "edgecolor": str,
        "bar_width": float,
        "cmap": str,
        "vmin": float,
        "vmax": float,
    }
    for key, kind in allowed.items():
        if key in style and style[key] is not None:
            out[key] = kind(style[key])
    return out


def _tick_facts(ax) -> dict:
    """视区内刻度事实（tickmodel 同款口径：`_update_ticks` 的取舍）。"""

    def one(axis, horizontal: bool) -> dict:
        drawn = axis._update_ticks()  # noqa: SLF001 — 渲染器自己的视区取舍
        ticks = []
        for t in drawn:
            loc = float(t.get_loc())
            label = t.label1.get_text()
            ticks.append({"value": loc, "label": label, "mathtext": label.startswith("$")})
        lo, hi = sorted(axis.get_view_interval())
        eps = (hi - lo) * 1e-9
        minor = [float(v) for v in axis.get_minorticklocs() if lo - eps <= v <= hi + eps]
        return {"ticks": ticks, "minor": minor, "offset_text": axis.get_offset_text().get_text()}

    return {"x": one(ax.xaxis, True), "y": one(ax.yaxis, False)}
