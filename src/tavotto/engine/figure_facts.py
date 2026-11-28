"""改图助手的「图上的事实」：Tavotto 按渲染几何算好的、确定性的版面与数据事实（给修改方案模型看）。

模型只看得到元素表里的 `bbox`（整张图分数坐标），读不出「图例压没压住曲线」「右下角空不空」「收窄坐标范围会不会把数据藏掉」；
真机 2026-11-27 它把 `loc: lower right` 的图例说成压在曲线右上方，还建议把 0–60 收窄到 0–45。这里把这些事实先算好：

* 图幅；
* 每个顶层子图里图例 / 标题 / 轴标题 / 文字注释落在哪（坐标区右下角、坐标区外上方居中……）；
* 数据把子图 3×3 九宫格占了哪几格、哪几格是空白，每条曲线经过哪几格；
* 干涉检测（`interference.detect`）的结论，没问题时明说「图例没有压住数据」；
* 坐标范围与数据范围的关系（数据是否全在范围内）。
* 图例项与它代表的曲线 / 柱样式对不上的地方（`legend_style_mismatch`，与问题面板的 `legend-style-mismatch` 同一份事实），
  连同改得掉它的那条图例项属性（排在最前，多系列图上不被行数上限截掉）。只有来源确证（ADR 0219：创建图例时
  实际传入的就是那条已画系列）的项才有这一行；没确证的图例项一个字不提——作者单独设计的图例不是错。

预检（`preflight.run`）要规范 spec 与 profile，这里拿不到，所以不并进来；干涉检测只要 manifest。
只读 manifest，纯标准库；有界（`MAX_LINES` / `MAX_CHARS`）。数据坐标只对线性轴反推（对数 / 未知刻度不说范围）。
"""

from __future__ import annotations

import math

from . import interference as itf, preflight

MAX_LINES = 40
MAX_CHARS = 4000
MAX_ANNOTATIONS = 6
#: 每条折线最多采样这么多点（线段按格子大小插值，防几何很大时变慢）
_MAX_SAMPLES = 6000

_COLS = ("左", "中", "右")
_ROWS = ("上", "中", "下")
_CELL_NAMES = tuple(
    ("中心" if (c, r) == ("中", "中") else f"{c}{r}") for r in _ROWS for c in _COLS
)  # 行优先：上行三格、中行、下行


def _axes_rect(el: dict) -> tuple[float, float, float, float] | None:
    return itf._rect(el.get("bbox"))


def position_word(box: tuple, ax: tuple) -> str:
    """元素框相对子图绘图区的位置词（y 向下）。中心落在框外 = 「坐标区外…」，否则 3×3 里的格子名。"""
    aw, ah = ax[2] - ax[0], ax[3] - ax[1]
    if aw <= 0 or ah <= 0:
        return ""
    u = ((box[0] + box[2]) / 2 - ax[0]) / aw
    v = ((box[1] + box[3]) / 2 - ax[1]) / ah
    if u < 0 or u > 1 or v < 0 or v > 1:
        side = (
            ("左侧" if u < 0 else "右侧")
            if abs(u - 0.5) > abs(v - 0.5)
            else ("上方" if v < 0 else "下方")
        )
        mid = ""
        if side in ("上方", "下方"):
            mid = "居中" if 1 / 3 <= u <= 2 / 3 else ("偏左" if u < 1 / 3 else "偏右")
        else:
            mid = "居中" if 1 / 3 <= v <= 2 / 3 else ("偏上" if v < 1 / 3 else "偏下")
        return f"坐标区外{side}{mid}"
    col = 0 if u < 1 / 3 else (2 if u > 2 / 3 else 1)
    row = 0 if v < 1 / 3 else (2 if v > 2 / 3 else 1)
    names = {
        (0, 0): "左上角",
        (1, 0): "上方居中",
        (2, 0): "右上角",
        (0, 1): "左侧居中",
        (1, 1): "正中",
        (2, 1): "右侧居中",
        (0, 2): "左下角",
        (1, 2): "下方居中",
        (2, 2): "右下角",
    }
    return "坐标区" + names[(col, row)]


def _cells_of_points(pts: list[tuple[float, float]], ax: tuple) -> set[int]:
    aw, ah = ax[2] - ax[0], ax[3] - ax[1]
    out: set[int] = set()
    for x, y in pts:
        u, v = (x - ax[0]) / aw, (y - ax[1]) / ah
        if u < -1e-9 or u > 1 + 1e-9 or v < -1e-9 or v > 1 + 1e-9:
            continue
        out.add(min(2, max(0, int(v * 3))) * 3 + min(2, max(0, int(u * 3))))
    return out


def _paths_of(el: dict) -> list[dict]:
    geom = el.get("geometry")
    paths = geom.get("paths") if isinstance(geom, dict) else None
    return [p for p in paths if isinstance(p, dict)] if isinstance(paths, list) else []


def _clean_points(path: dict) -> list[tuple[float, float]]:
    out = []
    for p in path.get("points") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            x, y = itf._num(p[0]), itf._num(p[1])
            if x is not None and y is not None:
                out.append((x, y))
    return out


def _series_cells(el: dict, ax: tuple) -> set[int]:
    """一个数据元素经过绘图区的哪几格：折线按线段插值取样；闭合填充的（柱 / 面）按路径包围盒盖住的格子算。"""
    aw, ah = ax[2] - ax[0], ax[3] - ax[1]
    step = min(aw, ah) / 12  # 取样间距：格子边长的 1/4
    cells: set[int] = set()
    budget = _MAX_SAMPLES
    for path in _paths_of(el):
        pts = _clean_points(path)
        if not pts:
            continue
        if path.get("closed") and el.get("geometry", {}).get("fill") and len(pts) > 2:
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
            clipped = itf._intersect((x0, y0, x1, y1), ax)
            if clipped is not None:
                grid = [
                    (
                        clipped[0] + (clipped[2] - clipped[0]) * i / 4,
                        clipped[1] + (clipped[3] - clipped[1]) * j / 4,
                    )
                    for i in range(5)
                    for j in range(5)
                ]
                cells |= _cells_of_points(grid, ax)
            continue
        samples = list(pts)
        for (ax_, ay_), (bx_, by_) in zip(pts, pts[1:], strict=False):
            n = int(math.hypot(bx_ - ax_, by_ - ay_) / step) if step > 0 else 0
            n = min(n, 200)
            samples.extend(
                (ax_ + (bx_ - ax_) * k / (n + 1), ay_ + (by_ - ay_) * k / (n + 1))
                for k in range(1, n + 1)
            )
            if len(samples) > budget:
                break
        cells |= _cells_of_points(samples[:budget], ax)
    return cells


def _cell_list(cells: set[int]) -> str:
    return "、".join(_CELL_NAMES[i] for i in sorted(cells))


def _field(el: dict, prop: str):
    return itf._field(el, prop)


def _lim(el: dict, prop: str) -> tuple[float, float] | None:
    v = _field(el, prop)
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = itf._num(v[0]), itf._num(v[1])
        if a is not None and b is not None and a != b:
            return (a, b)
    return None


def linear_axes(el: dict) -> bool:
    """x、y 两轴都是已知的线性刻度（才能把画面分数坐标反推成数据坐标）。"""
    return _field(el, "xscale") == "linear" and _field(el, "yscale") == "linear"


def to_data(pt: tuple[float, float], ax: tuple, xlim: tuple, ylim: tuple) -> tuple[float, float]:
    """画面分数坐标（y 向下）→ 数据坐标（线性轴；`xlim` / `ylim` 的先后顺序即轴方向，反转轴也成立）。"""
    u = (pt[0] - ax[0]) / (ax[2] - ax[0])
    v = (ax[3] - pt[1]) / (ax[3] - ax[1])
    return (xlim[0] + u * (xlim[1] - xlim[0]), ylim[0] + v * (ylim[1] - ylim[0]))


def data_elements(manifest: dict, axes_gid: str) -> list[dict]:
    """某个顶层子图里、拿得到真实几何的数据元素（图像不算：它的「几何」是整块矩形）。"""
    return [
        e
        for e in manifest.get("elements") or []
        if isinstance(e, dict)
        and e.get("role") in itf.DATA_ROLES
        and e.get("role") != "image"
        # 画上去的装饰形状（框 / 矩形，role 也是 patch）不是数据：真数据的 patch（柱子）带数据指纹，装饰形状没有。
        # 真机 2026-11-27：绘图区外的一个矩形被算进数据范围，「数据到 86.8 min」，模型据此建议把横轴放宽到 90
        and (e.get("role") != "patch" or isinstance(e.get("data_fingerprint"), dict))
        and itf.owner_axes(str(e.get("gid", ""))) == axes_gid
        and itf.is_visible(e)
        and _paths_of(e)
    ]


def data_points(manifest: dict, axes_el: dict) -> list[tuple[float, float]]:
    """该子图全部数据点的数据坐标（含画在范围外、被裁掉的）；非线性轴 / 没有范围 / 没有几何给空表。"""
    ax = _axes_rect(axes_el)
    xlim, ylim = _lim(axes_el, "xlim"), _lim(axes_el, "ylim")
    if ax is None or xlim is None or ylim is None or not linear_axes(axes_el):
        return []
    if ax[2] <= ax[0] or ax[3] <= ax[1]:
        return []
    pts: list[tuple[float, float]] = []
    for d in data_elements(manifest, str(axes_el.get("gid", ""))):
        for path in _paths_of(d):
            pts.extend(to_data(p, ax, xlim, ylim) for p in _clean_points(path))
            if len(pts) > 200_000:
                return pts
    return pts


def _fmt(v: float, span: float = 1.0) -> str:
    """3 位有效数字；贴近 0 的浮点噪声（-1e-15）按 `span` 的 1e-6 归零。"""
    return "0" if abs(v) < abs(span) * 1e-6 else f"{v:.3g}"


def _name(el: dict, fallback: str) -> str:
    v = _field(el, "label")
    text = v if isinstance(v, str) and v.strip() else str(el.get("label") or fallback)
    return text.replace("\n", " ").strip()[:30]


MAX_LEGEND_MISMATCH = 6

_STYLE_WORD = {
    "color": "线色",
    "linestyle": "线型",
    "linewidth": "线宽",
    "marker": "标记形状",
    "markerfacecolor": "标记填充色",
    "markeredgecolor": "标记描边色",
    "facecolor": "填充色",
    "edgecolor": "边框色",
}


def legend_mismatch_facts(manifest: dict) -> list[str]:
    """图例项样式与曲线不一致的事实行（每项一行，至多 `MAX_LEGEND_MISMATCH` 行）：哪一项、哪个属性图例是什么 / 曲线是什么、怎么改。"""
    elements = [e for e in manifest.get("elements") or [] if isinstance(e, dict)]
    hidden = itf.hidden_legends(elements)
    out: list[str] = []
    for el in elements:
        if el.get("role") != "legend_text" or not itf.is_visible(el):
            continue
        gid = str(el.get("gid", ""))
        if itf.legend_of(gid) in hidden:
            continue
        # 只认 worker 确证的来源（ADR 0219）：0034 的 `legend_entry.source_gid` 可能是按样式 / 标签猜的
        source = preflight.legend_style_source(el)
        diffs = preflight.legend_style_diffs(el)
        if source is None or not diffs:
            continue
        parts = [
            f"{_STYLE_WORD.get(d['prop'], d['prop'])} 图例 {d.get('legend')} / 曲线 {d.get('source')}"
            for d in diffs
        ]
        fixes: list[str] = []
        stuck: list[str] = []
        for d in diffs:
            fix = d.get("fix")
            if isinstance(fix, dict) and isinstance(fix.get("prop"), str) and "value" in fix:
                text = f"{gid} 的 {fix['prop']} 设为 {fix['value']}"
                if text not in fixes:
                    fixes.append(text)
            else:
                stuck.append(_STYLE_WORD.get(d["prop"], d["prop"]))
        how = f"改法：{'；'.join(fixes)}" if fixes else ""
        if stuck:
            how += ("，" if how else "") + f"{'、'.join(stuck)}在图内改不了（要回脚本改句柄）"
        out.append(
            f"图例项「{preflight.legend_entry_text(el)}」（{gid}）与它代表的 {source} 样式不一致："
            + "；".join(parts)
            + (f"。{how}" if how else "")
        )
        if len(out) >= MAX_LEGEND_MISMATCH:
            break
    return out


#: 一条线的数据点多于这个数 = 连续曲线（拟合 / 模型 / 密采样），逐点加标记会糊成一串珠子（真机 2026-11-27）
DENSE_POINTS = 40


def point_count(el: dict) -> int | None:
    """这条数据线的原始点数（`data_fingerprint.parts.xy.shape[0]`；折线几何是抽稀过的，不能拿来数）。"""
    try:
        shape = el["data_fingerprint"]["parts"]["xy"]["shape"]
        return int(shape[0])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def is_dense_line(el: dict) -> bool:
    n = point_count(el)
    return el.get("role") == "line" and n is not None and n > DENSE_POINTS


def _density_note(el: dict) -> str:
    n = point_count(el)
    if n is None or el.get("role") != "line":
        return ""
    if n > DENSE_POINTS:
        return f"；{n} 个点，是连续曲线（拟合 / 模型 / 密采样），不是一个个实测点——不要逐点加标记"
    return f"；{n} 个点"


def figure_facts(manifest: object) -> list[str]:
    """→ 一行一条的中文事实（至多 `MAX_LINES` 行、`MAX_CHARS` 字）。manifest 读不懂 / 没有子图给空表。"""
    if not isinstance(manifest, dict):
        return []
    elements = [e for e in manifest.get("elements") or [] if isinstance(e, dict)]
    lines: list[str] = []
    fig = next((e for e in elements if e.get("role") == "figure"), None)
    size = manifest.get("size_mm")
    if size is None and fig is not None:
        size = _field(fig, "size_mm")
    if isinstance(size, (list, tuple)) and len(size) == 2 and all(itf._num(s) for s in size):
        lines.append(
            f"图幅 {_fmt(size[0])} × {_fmt(size[1])} mm（宽 × 高；受期刊栏宽约束，用户没明说就不要改）"
        )
    try:
        lines.extend(legend_mismatch_facts(manifest))
    except Exception:  # noqa: BLE001  事实只是辅助
        pass
    try:
        findings = itf.detect(manifest)
    except Exception:  # noqa: BLE001  事实只是辅助，几何读不懂就少说，不拖垮出方案
        findings = []
    axes_list = [e for e in elements if itf.is_top_level_axes(e) and _axes_rect(e) is not None]
    multi = len(axes_list) > 1
    for axes in axes_list:
        gid = str(axes.get("gid", ""))
        ax = _axes_rect(axes)
        assert ax is not None
        tag = f"[{_name(axes, gid)}] " if multi else ""
        owned = [
            e
            for e in elements
            if itf.owner_axes(str(e.get("gid", ""))) == gid and itf.is_visible(e)
        ]
        # ---- 位置 ----
        for e in owned:
            role = e.get("role")
            if role not in ("legend", "title", "axis_label", "text"):
                continue
            box = itf.visible_rect(e) if role == "legend" else itf._rect(e.get("bbox"))
            if box is None:
                continue
            word = position_word(box, ax)
            if not word:
                continue
            if role == "legend":
                loc = _field(e, "loc")
                lines.append(f"{tag}图例在{word}" + (f"（loc: {loc}）" if loc else ""))
            elif role == "title":
                lines.append(f"{tag}标题在{word}")
            elif role == "axis_label":
                end = str(e.get("gid", "")).rsplit(".", 1)[-1]
                title = {"xlabel": "X 轴标题", "ylabel": "Y 轴标题"}.get(end, "轴标题")
                lines.append(f"{tag}{title}在{word}")
        notes = [e for e in owned if e.get("role") == "text"]
        for e in notes[:MAX_ANNOTATIONS]:
            box = itf._rect(e.get("bbox"))
            if box is not None:
                lines.append(f"{tag}文字「{_name(e, '文字')}」在{position_word(box, ax)}")
        # ---- 数据占格 ----
        series = [(d, _series_cells(d, ax)) for d in data_elements(manifest, gid)]
        if series:
            used: set[int] = set()
            for _d, cells in series:
                used |= cells
            empty = set(range(9)) - used
            lines.append(
                f"{tag}数据占据：{_cell_list(used) or '无'}；空白：{_cell_list(empty) if empty else '没有'}"
                "（把绘图区分成 3×3 格，左上为起点）"
            )
            for d, cells in series[:8]:
                lines.append(
                    f"{tag}「{_name(d, str(d.get('gid')))}」经过：{_cell_list(cells) or '（在绘图区外）'}"
                    + _density_note(d)
                )
        # ---- 坐标范围 vs 数据范围 ----
        pts = data_points(manifest, axes)
        xlim, ylim = _lim(axes, "xlim"), _lim(axes, "ylim")
        if pts and xlim and ylim:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            ex, ey = (max(xlim) - min(xlim)) * 1e-3, (max(ylim) - min(ylim)) * 1e-3
            sx, sy = max(max(xs) - min(xs), ex), max(max(ys) - min(ys), ey)
            xr = f"x {_fmt(min(xs), sx)}–{_fmt(max(xs), sx)}"
            yr = f"y {_fmt(min(ys), sy)}–{_fmt(max(ys), sy)}"
            cur = f"x {_fmt(min(xlim), sx)}–{_fmt(max(xlim), sx)}、y {_fmt(min(ylim), sy)}–{_fmt(max(ylim), sy)}"
            out_x = any(x < min(xlim) - ex or x > max(xlim) + ex for x in xs)
            out_y = any(y < min(ylim) - ey or y > max(ylim) + ey for y in ys)
            if out_x or out_y:
                which = "、".join(n for n, o in (("x", out_x), ("y", out_y)) if o)
                lines.append(
                    f"{tag}数据范围 {xr}、{yr}；当前坐标范围 {cur}：{which} 方向有数据在范围外，没显示出来"
                )
            else:
                lines.append(
                    f"{tag}数据范围 {xr}、{yr}；当前坐标范围 {cur}：数据全部在范围内（再收窄会把数据藏起来）"
                )
        # ---- 干涉结论 ----
        for leg in (e for e in owned if e.get("role") == "legend"):
            hit = [
                f
                for f in findings
                if f["id"] == itf.CHECK_LEGEND_OVER_DATA
                and f["gids"]
                and f["gids"][0] == leg.get("gid")
            ]
            if not hit:
                lines.append(f"{tag}图例没有压住数据（已按真实几何核对）")
    for f in findings[:8]:
        lines.append(f"检测到：{f['text']}")
    if (
        not any(f["id"] in (itf.CHECK_TEXT_OVERLAP, itf.CHECK_TEXT_OVER_AXES) for f in findings)
        and axes_list
    ):
        lines.append("文字之间没有互相重叠，也没有文字压进别的子图的绘图区")
    out: list[str] = []
    used_chars = 0
    for line in lines[:MAX_LINES]:
        line = line.replace("\n", " ")
        if used_chars + len(line) + 1 > MAX_CHARS:
            break
        used_chars += len(line) + 1
        out.append(line)
    return out
