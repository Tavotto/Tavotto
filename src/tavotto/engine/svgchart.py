"""SVG 计算图表的 worker 侧脚手架（ADR-XXXX，SG07）。

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

from tavotto.engine import chartmodel, chartprofile
from tavotto.engine.chartprofile import LEGACY_PROFILE
from tavotto.svgcoord import apply, viewbox_to_pt

#: 片段里允许出现的刻度标签字体大小上界（防御性；matplotlib 默认 10pt）。
_MAX_LABEL_PT = 72.0


def scaffold_fragment(
    model: dict,
    frame: dict,
    *,
    salt: str = "tavotto",
    render_profile: str = LEGACY_PROFILE,
    patches=(),
    legend=(),
) -> dict:
    """保持旧渲染协议；native 的 Figure、属性与 manifest 都在场景物理 pt 中。

    ``legend``（N4）：绑定合同的 legend 块（非空列表）。legacy 组合器没有
    真图例能力，非空 legend 在这里直接拒绝（防御层；app 层主闸报
    ``chart_render_unsupported``）。
    """
    profile = chartprofile.validate_profile(render_profile)
    edits = chartprofile.validate_overrides(patches, render_profile=profile)
    if profile == LEGACY_PROFILE:
        if legend:
            raise ValueError("legend contract requires a native render profile")
        return _scaffold_fragment(model, frame, salt=salt)
    import matplotlib

    # Text 在构造时采 rcParams；不能等 savefig 时才关闭 TeX。
    with matplotlib.rc_context({"text.usetex": False, "savefig.bbox": None}):
        return _scaffold_fragment(
            model,
            frame,
            salt=salt,
            native=True,
            patches=edits,
            **({"complete_frame": True} if profile == chartprofile.COMPLETE_PROFILE else {}),
            **({"legend": list(legend)} if legend else {}),
        )


def _scaffold_fragment(
    model: dict,
    frame: dict,
    *,
    salt: str,
    native: bool = False,
    patches=(),
    complete_frame: bool = False,
    legend=(),
) -> dict:
    """模型 + 场景框 → ``{"fragment": bytes, "facts": {...}}``。

    ``frame``：``{"view_box": [x,y,w,h], "w_pt": float, "h_pt": float,
    "preserve": str|None}``——场景的物理映射。片段在**场景用户坐标系**渲染
    （figure 1 单位 = 1 viewBox 用户单位），组合时与源坐标 1:1 对齐；
    pt 类量（字号/线宽/marker/capsize）按 viewBox→pt 比例**换算成用户单位**，
    使场景映射后视觉尺寸正确。每个语义对象带 ``data-tavotto`` 角色标记
    （matplotlib gid → SVG id），组合按角色取完整产物。

    ``legend``（N4）：非空时按合同创建真 Legend（follow 条目 handle = 系列
    实际 artist、custom 条目 = 代理示意线），位置写进 legendmodel 位置模型。

    非 uniform（preserveAspectRatio="none"）时 pt 换算取两轴比例的几何平均
    并在 facts 里如实声明——位置的映射仍逐轴精确。
    """
    chartmodel.validate_model(model)
    if legend:
        # worker 不信任父进程：合同判据在这里再过一遍（与 validate_binding
        # 消费的是同一个 validate_legend_spec，不存在第二套判据）。
        chartmodel.validate_legend_spec(legend, model)
    view_box = frame.get("view_box")
    if not isinstance(view_box, list) or len(view_box) != 4:
        raise ValueError("frame.view_box must be [x,y,w,h]")
    vb_x, vb_y, vb_w, vb_h = (float(v) for v in view_box)
    w_pt, h_pt = float(frame["w_pt"]), float(frame["h_pt"])
    u2pt = viewbox_to_pt(view_box, w_pt, h_pt, frame.get("preserve"))
    scale_x, scale_y = u2pt[0], u2pt[3]
    render_scale = 1.0 if native else (scale_x * scale_y) ** 0.5

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.figure

    ax_cfg = model["axes"][0]
    ax_x, ax_y, ax_w, ax_h = (float(v) for v in ax_cfg["rect"])
    # figure 空间 = **viewBox 局部**坐标 [0,vb_w]×[0,vb_h]：轴矩形先平移进局部
    # （场景用户坐标 − viewBox 原点）；组合端再以 translate(origin) 映射回场景
    # 用户空间（N1）。绘制尺寸按 vb 尺寸 → 1 单位 = 1 用户单位。
    left = ax_x - vb_x
    bottom = ax_y + ax_h - vb_y
    figure_w, figure_h = vb_w, vb_h
    width, height = ax_w, ax_h
    if native:
        left, top = apply(u2pt, ax_x, ax_y)
        right, bottom = apply(u2pt, ax_x + ax_w, ax_y + ax_h)
        width, height = right - left, bottom - top
        figure_w, figure_h = w_pt, h_pt
    fig = matplotlib.figure.Figure(figsize=(figure_w / 72.0, figure_h / 72.0), dpi=72)
    ax = fig.add_axes(
        [
            left / figure_w,
            1.0 - bottom / figure_h,  # matplotlib 是 y-up 的图分数
            width / figure_w,
            height / figure_h,
        ]
    )
    ax.set_gid(f"tv-axes-{ax_cfg['id']}")
    # 背景属于保留的原场景，不是刻度/网格等要嫁接的轴家具。
    # 显式角色让组合端无需按 patch 序号或白色填充猜测。
    ax.patch.set_gid(f"tv-axesbg-{ax_cfg['id']}")
    if native:
        # Start transparent; an explicit native facecolor edit then paints the
        # same real patch without replacing an unowned source background.
        ax.patch.set_facecolor("none")
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

    points, ownership = {}, {f"axes:{ax_cfg['id']}": ax}
    categorical = ax_cfg.get("kind") == "categorical"
    cats = ax_cfg.get("categories") or []
    for sr in model["series"]:
        kind = sr["kind"]
        style = dict(sr.get("style") or {})
        role = f"tv-series-{sr['id']}"
        if kind == "image":
            grid = model["dataset"].get("grids", {}).get(sr.get("grid", ""))
            if grid is None:
                raise ValueError(f"series {sr['id']} references unknown grid")
            imshow_kwargs = _safe_style(
                {k: v for k, v in style.items() if k in ("cmap", "vmin", "vmax", "alpha")}
            )
            mappable = ax.imshow(grid["values"], origin="upper", **imshow_kwargs)
            mappable.set_gid(role)
            cbar = fig.colorbar(mappable, ax=ax)
            cbar.ax.set_gid(f"tv-colorbar-{sr['id']}")
            ownership[f"series:{sr['id']}"] = mappable
            ownership[f"axes:colorbar_{sr['id']}"] = cbar.ax
            ownership[f"colorbar:{sr['id']}"] = cbar
            # 色条刻度字号同样按用户单位换算
            cbar.ax.tick_params(labelsize=10.0 / render_scale)
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
        artists = []
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
            artists = ax.bar(
                xs, ys, yerr=yerr, capsize=4.0 / render_scale, label=label or None, **kwargs
            )
        elif kind == "errorbar":
            kwargs = _safe_style(
                {
                    k: v
                    for k, v in style.items()
                    if k in ("color", "linewidth", "linestyle", "alpha")
                }
            )
            if "linewidth" in kwargs:
                kwargs["linewidth"] = kwargs["linewidth"] / render_scale
            artists = ax.errorbar(
                xs, ys, yerr=yerr, capsize=4.0 / render_scale, label=label or None, **kwargs
            )
        else:
            plot_style = _safe_style(style)
            if native and "linewidth" in style:
                plot_style["linewidth"] = float(style["linewidth"])
            if kind == "scatter":
                plot_style.setdefault("marker", "o")
                plot_style["linestyle"] = "none"
            if "markersize" in plot_style:
                plot_style["markersize"] = plot_style["markersize"] / render_scale
            ax.plot(
                xs,
                ys,
                linewidth=(
                    float(plot_style.pop("linewidth", 2.0))
                    if native
                    else float(plot_style.pop("linewidth", 2.0) or 2.0)
                )
                / render_scale,
                label=label or None,
                **plot_style,
            )
            artists = [ax.lines[-1]]
        # 语义角色标记：完整产物（线/柱/marker/误差成员）都带角色 id（matplotlib gid → SVG id）
        ownership[f"series:{sr['id']}"] = artists
        for j, artist in enumerate(_iter_artists(artists)):
            artist.set_gid(f"{role}-{j + 1}" if j else role)
    if categorical:
        ax.set_xticks(list(range(len(cats))))
        ax.set_xticklabels(cats)
    # 轴家具的 pt 量按用户单位换算（刻度字号/线长/线宽）
    ax.tick_params(
        labelsize=10.0 / render_scale, length=3.5 / render_scale, width=0.8 / render_scale
    )
    for spine in ax.spines.values():
        spine.set_linewidth(0.8 / render_scale)
    if ax_cfg.get("xlabel"):
        ax.set_xlabel(str(ax_cfg["xlabel"]), fontsize=10.0 / render_scale)
    if ax_cfg.get("ylabel"):
        ax.set_ylabel(str(ax_cfg["ylabel"]), fontsize=10.0 / render_scale)
    paired = None
    if native:
        from pathlib import Path

        from tavotto.engine import bridgeboot
        from tavotto.engine.chartmanifest import render_scaffold

        runtime = bridgeboot.load_engine_modules(
            str(Path(__file__).resolve().parent), bridgeboot.ENGINE_SIBLINGS
        )
        legend_follow = (
            _scaffold_legends(
                fig,
                ax,
                ownership,
                legend,
                u2pt=u2pt,
                w_pt=w_pt,
                h_pt=h_pt,
                render_scale=render_scale,
                runtime=runtime,
            )
            if legend
            else None
        )
        paired = render_scaffold(
            fig,
            ownership,
            frame,
            list(patches),
            salt=salt,
            coordinate_space="scene-viewport-pt",
            legend_follow=legend_follow,
            **({"complete_frame": True} if complete_frame else {}),
        )
    else:
        fig.canvas.draw()  # realize transforms & locators（exp2 口径）
    # 轴分数（matplotlib 原生、y-up）：display(72dpi) 减轴原点、除轴尺寸。
    # figure 1 display px = 1 场景用户单位；display y-up 的轴底 = vb_h - (rect 底偏移)。
    ax_disp_left = left
    ax_disp_bottom = vb_h - bottom
    ax_w_px, ax_h_px = ax_w, ax_h
    if native:
        ax_disp_left, ax_disp_bottom, ax_w_px, ax_h_px = ax.bbox.bounds
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

    if paired is not None:
        paired["facts"] = {
            "series_points": points,
            "ticks": _tick_facts(ax),
            "axes_rect_user": [ax_x, ax_y, ax_w, ax_h],
        }
        return paired
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


def _scaffold_legends(fig, ax, ownership, legend, *, u2pt, w_pt, h_pt, render_scale, runtime):
    """按 legend 合同在 scaffold 里创建真 Legend（N4），返回条目 → 系列映射。

    follow 条目的 handle 取**系列的实际 artist/容器**（matplotlib 自己的
    handler 从它派生示意线，与真实 Figure 的 ``ax.legend()`` 逐字同一条路；
    ``chartmanifest._claims`` 已把误差成员归进同一角色）；custom 条目造代理
    Line2D——样式取缺省、文字取合同 label（不从旧 SVG 色值捕获初始样式，
    那是按色猜；示意线 = pristine 快照，后续可用既有 handle_* override 编辑）。

    位置按合同 rect（场景用户单位）经 u2pt 换算写进 ``legendmodel`` 位置模型的
    loc_frac 槽：**区域外接矩形左上角对齐 legend 盒左上角**（N4 冻结决策 A）。
    tuple loc 的 matplotlib 语义锚的是盒的**左下**角（``Legend._findoffset``），
    所以量一次盒高把锚点从左上角下移到左下角。复用现有位置模型，不新发明
    位置表达；字号与刻度标签同一换算（``10.0 / render_scale``）。

    返回 ``{"legend:<id>": ["series:<sid>" | None, …]}``（按新图例条目位置），
    交给 ``render_scaffold`` 在 instrument 后落进条目模型——判据主语是用户
    指认，不是指纹猜测。
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.lines import Line2D

    legendmodel = runtime.legendmodel
    renderer = FigureCanvasAgg(fig).get_renderer()
    legend_follow: dict[str, list[str | None]] = {}
    for order, item in enumerate(legend):
        entries = sorted(item["entries"], key=lambda e: e["index"])
        handles, labels, series_roles = [], [], []
        for entry in entries:
            labels.append(str(entry["label"]))
            sid = entry.get("series")
            if sid is None:
                handles.append(Line2D([], [], color="black"))
                series_roles.append(None)
            else:
                source = ownership[f"series:{sid}"]
                handles.append(
                    source[0] if isinstance(source, list) and len(source) == 1 else source
                )
                series_roles.append(f"series:{sid}")
        # 第一条挂 axes（instrument 登记 axes_0.legend）；同 axes 的第二条会被
        # matplotlib 顶掉，所以后续挂 figure 级（fig.legends，登记 fig.legend_i）
        kwargs = {"fontsize": 10.0 / render_scale}
        leg = (
            ax.legend(handles, labels, **kwargs)
            if order == 0
            else fig.legend(handles, labels, **kwargs)
        )
        # instrument 会把 gid 重登记为 axes_0.legend / fig.legend_i；这里先挂
        # 合同 id，让 roles 的 input_gids 留下合同 id 的痕迹（可追溯）。
        leg.set_gid(f"tv-legend-{item['id']}")
        ownership[f"legend:{item['id']}"] = leg
        x_pt, y_pt = apply(u2pt, float(item["rect"][0]), float(item["rect"][1]))
        box = leg.get_window_extent(renderer)
        cfg = legendmodel.legend_pos_cfg(leg)
        cfg["loc_frac"] = [x_pt / w_pt, (y_pt + box.height) / h_pt]
        legendmodel.apply_legend_pos_model(leg)
        legend_follow[f"legend:{item['id']}"] = series_roles
    return legend_follow


def _iter_artists(item):
    """bar/errorbar 容器或单个 artist → 全部**可绘制**成员。

    完整产物（N4）：BarContainer 的 tuple 内容只有柱矩形——误差成员挂在
    ``.errorbar`` 属性（ErrorbarContainer），其 get_children 才是
    数据线/端帽/误差线集合。规则：`.errorbar` 属性先收；`get_children()` 非空
    就递归子成员（Line2D/LineCollection/Rectangle 返回空 → 叶子，gid 落在
    会真实出现的元素上）；裸 list/tuple 递归。
    """
    if item is None:
        return []
    if isinstance(item, (list, tuple)):
        # BarContainer/ErrorbarContainer 都是 tuple 子类；tuple 内容之外，
        # BarContainer 的误差成员挂在 .errorbar 属性（N4）。
        out = []
        for sub in item:
            out.extend(_iter_artists(sub))
        errorbar = getattr(item, "errorbar", None)
        if errorbar is not None:
            out.extend(_iter_artists(errorbar))
        return out
    out = []
    children = item.get_children() if hasattr(item, "get_children") else []
    if children:
        for child in children:
            out.extend(_iter_artists(child))
        return out
    out.append(item)
    return out


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


def main() -> None:
    """独立子进程入口：stdin 单个 JSON 请求 → stdout 单个 JSON 响应。

    父进程用项目 worker 解释器 spawn（``python -m tavotto.engine.svgchart``），
    不经 worker 会话、不 import 用户脚本。stderr 只写日志，协议走 stdout。
    """
    import base64
    import json
    import sys

    raw = sys.stdin.buffer.read()
    try:
        import matplotlib
        import numpy

        from tavotto import __version__

        engine = {
            "matplotlib": matplotlib.__version__,
            "numpy": numpy.__version__,
            "tavotto": __version__,
        }
        req = json.loads(raw.decode("utf-8"))
        if req.get("action") is not None:
            from tavotto.rendercore.epscontainer import SceneEPSError, pack_prepared_eps

            if set(req) != {"action", "prepared"} or req["action"] != "pack_eps":
                raise SceneEPSError("native_eps_invalid_payload", "Unknown fixed chart action")
            prepared = req["prepared"]
            eps = pack_prepared_eps(prepared)
            out = {
                "ok": True,
                "engine": engine,
                "eps_b64": base64.b64encode(eps).decode("ascii"),
                **{
                    key: prepared[key]
                    for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt")
                },
            }
        elif req.get("probe"):
            out = {"ok": True, "engine": engine}
        else:
            result = scaffold_fragment(
                req.get("model", {}),
                req.get("frame", {}),
                salt=str(req.get("salt", "tavotto")),
                render_profile=req.get("render_profile", LEGACY_PROFILE),
                patches=req.get("patches", ()),
                legend=req.get("legend", ()),
            )
            out = {
                "ok": True,
                "fragment_b64": base64.b64encode(result["fragment"]).decode("ascii"),
                **{key: value for key, value in result.items() if key != "fragment"},
                "engine": engine,
            }
    except Exception as exc:  # noqa: BLE001 — 协议层兜底，错误以稳定 JSON 表达
        out = {
            "ok": False,
            "code": getattr(exc, "code", None) or "svg_scaffold_invalid",
            "error": str(exc),
        }
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
