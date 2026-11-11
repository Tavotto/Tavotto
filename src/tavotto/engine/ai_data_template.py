"""TAF 数据出图 v2 的「配方 → 脚本」模板（ADR 0178，批次 1 的前两族：comparison、trend）与期望数据指纹。

纯标准库，Flask 父进程 import；**不执行**任何东西、不写任何文件、不渲染。被执行的脚本完全由这里的模板生成：
配方（`ai_data_recipe.FigureRecipe`，闭集）里的列名 / 工作表名 / 数据文件名只以 `repr()` 字符串字面量进入脚本，
不会成为标识符或代码，也不进注释正文（来源注释是 ASCII JSON）。调用方先 `ai_data_recipe.validate_recipe`；这里对不认识的
组合直接抛 `TemplateError`，**不静默画个近似的**。

**一份实现，两处使用；脚本形态 = 短脚本 + 旁边一个公共模块**（用户 2026-11-11 拍板）。读表、单元格解析、汇总、确定性抖动、
箱线 / 核密度 / 拟合的数值全是本模块里的普通函数（`RUNTIME_FUNCTIONS`）；它们的**源码**（`inspect.getsource`）连同 `xlsxread` 的
源码原文被收进一个公共模块（`runtime_module()`：`tavotto_plot_<内容 sha256 前 8 位>.py`，对所有图逐字节相同、不含任何数据、
一个模块覆盖 CSV / TSV / XLSX 三种数据源），生成的脚本只剩 docstring、来源注释、`import tavotto_plot_xxxxxxxx as tp`、常量和
`main()`；父进程算期望指纹调用的就是同一批函数，不是抄第二份。**落盘规则**（下一个 PR 实现；本模块不写任何文件）：脚本与公共模块
都只新建，永不覆盖；公共模块已存在且字节相同就复用，存在但字节不同按损坏报错、不覆盖；内容变了名字就变，老脚本继续 import 它们当时的
那一版。脚本不自己改 `sys.path`：safe worker 与 native bridge 都把脚本所在目录放在 `sys.path[0]`（与 `python 脚本.py` 相同），
真 worker 与真 native bridge 两条入口各有用例钉住（cwd 与脚本目录不同的情形也钉了）。
`tests/test_ai_data_template.py` 用真 safe worker 对拍：manifest 的数据指纹与这里算的期望逐字节相等。

**哪些层能核**（`engine/manifest.py::data_fingerprint` 的覆盖面 × `figureverify.data_matches_expected` 按 label 匹配）：
manifest 给指纹的是 Line2D（`xy`）、Collection（`offsets` / `paths`）、柱组（`bars` = 底 + 高）、误差棒（`points` / `bars`），
但验证器按**元素的 label 唯一匹配**，而 matplotlib 的 `fill_between`（PolyCollection）与误差棒容器在 manifest 里没有 label 字段。
所以可核 = points / jitter（散点 offsets）、line / step（Line2D）、hline_summary、fit_line（Line2D）、bar（柱组）；
不核 = errorbar、band、box、violin（见 `fingerprint_coverage`）——不折算成 verified，调用方如实报告。
"""

from __future__ import annotations

import ast
import csv
import datetime
import functools
import hashlib
import inspect
import json
import math
import re
from dataclasses import dataclass

from . import ai_data_figure as adf, ai_data_recipe as recipe_mod, xlsxread
from .ai_data_figure import date_number, parse_cell, parse_date

# --------------------------------------------------------------------------- 错误
TEMPLATE_ERROR_CODES = (
    "template_family_unsupported",  # 这一族还没有模板
    "template_mark_unsupported",  # 这一族允许、但本模板没有的图元组合
    "template_recipe_mismatch",  # 配方指的表 / 列与传进来的条目对不上
    "template_option_unsupported",  # 坐标 / 轴刻度等选项在这一族没有实现
)


class TemplateError(ValueError):
    """配方不能被渲染成脚本。`code` 在 `TEMPLATE_ERROR_CODES` 里（内部错误：调用方应先 `validate_recipe`，不是用户可见码）。"""

    def __init__(self, code: str, detail: str = ""):
        if code not in TEMPLATE_ERROR_CODES:
            raise ValueError(f"未登记的 TemplateError 码：{code}")
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


# =========================================================================== 内嵌运行时（这一段的函数源码会原样进脚本）
# 约束：只用 math / csv / re / datetime 与彼此，不引用本模块的任何别的名字；Python 3.10 可跑。
_ISO_DATE_PREAMBLE = 'import datetime as _dt\n_ISO_DATE = re.compile(r"\\d{4}-\\d{2}-\\d{2}")\n_EPOCH = _dt.date(1970, 1, 1)\n'


def read_delimited(path, delimiter):
    """CSV / TSV → `(表头, 数据行)`：UTF-8（可带 BOM）；全空行不算数据行；表头去首尾空白。"""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        table = [r for r in csv.reader(fh, delimiter=delimiter) if any(c.strip() for c in r)]
    return [h.strip() for h in table[0]], table[1:]


def read_xlsx(path, sheet):
    """XLSX 一张表 → `(表头, 数据行)`：读法就是内嵌的 `XlsxBook`（与 Tavotto 的 `xlsxread` 同一份源码）。"""
    with XlsxBook(path) as book:  # noqa: F821 - 由内嵌的 xlsxread 源码提供
        table = [r for r in book.read(sheet) if any(c.strip() for c in r)]
    return [h.strip() for h in table[0]], table[1:]


def distinct(values):
    """按首次出现的顺序去重。"""
    return list(dict.fromkeys(values))


def make_label(mark, *parts):
    """artist 的 label：`图元 | 分组 | 分面 | …`（空的部分略去）。每个数据 artist 的 label 在整张图里唯一——
    Tavotto 按 label 找「画的是哪份数据」。"""
    return " | ".join([mark] + [p for p in parts if p])


def mean_of(vals):
    return math.fsum(vals) / len(vals)


def sd_of(vals):
    """样本标准差（n - 1）；n < 2 → NaN。"""
    n = len(vals)
    if n < 2:
        return math.nan
    m = mean_of(vals)
    return math.sqrt(math.fsum((v - m) ** 2 for v in vals) / (n - 1))


def quantile(sorted_vals, p):
    """线性插值分位数（与 numpy 默认、Excel QUARTILE.INC 同一个定义）。"""
    h = (len(sorted_vals) - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (h - lo)


def t_crit95(df):
    """双侧 95% 的 t 分布临界值（df ≥ 1）：1–30 查表，之后在 40 / 60 / 120 / ∞ 之间按 1/df 线性插值。"""
    table = (
        12.7062, 4.3027, 3.1824, 2.7764, 2.5706, 2.4469, 2.3646, 2.3060, 2.2622, 2.2281,
        2.2010, 2.1788, 2.1604, 2.1448, 2.1314, 2.1199, 2.1098, 2.1009, 2.0930, 2.0860,
        2.0796, 2.0739, 2.0687, 2.0639, 2.0595, 2.0555, 2.0518, 2.0484, 2.0452, 2.0423,
    )  # fmt: skip
    if df < 1:
        return math.nan
    if df <= 30:
        return table[df - 1]
    anchors = ((30, 2.0423), (40, 2.0211), (60, 2.0003), (120, 1.9799), (math.inf, 1.9600))
    for (d0, t0), (d1, t1) in zip(anchors, anchors[1:]):
        if df <= d1:
            w0 = 1.0 / d0
            w1 = 0.0 if d1 == math.inf else 1.0 / d1
            return t1 + (t0 - t1) * ((1.0 / df) - w1) / (w0 - w1)
    return 1.96


def summarize(vals, summary):
    """`(中心, 下限, 上限)`。`mean_sd` / `mean_sem` / `mean_ci95` 的中心是均值，`median_iqr` 是中位数与四分位数；
    样本不够（n < 2 的 SD 类）下限 / 上限为 NaN；没有值 → 全 NaN；`none` 只许恰好一个值（原值）。"""
    nan = math.nan
    n = len(vals)
    if summary == "none":
        if n != 1:
            raise ValueError("summary=none 要求每个位置恰好一个值")
        return vals[0], nan, nan
    if n == 0:
        return nan, nan, nan
    if summary == "median_iqr":
        s = sorted(vals)
        return quantile(s, 0.5), quantile(s, 0.25), quantile(s, 0.75)
    m = mean_of(vals)
    if n < 2:
        return m, nan, nan
    sd = sd_of(vals)
    if summary == "mean_sd":
        half = sd
    elif summary == "mean_sem":
        half = sd / math.sqrt(n)
    elif summary == "mean_ci95":
        half = t_crit95(n - 1) * sd / math.sqrt(n)
    else:
        raise ValueError(summary)
    return m, m - half, m + half


def jitter(k):
    """确定性抖动：第 k 个点的偏移，落在 [-0.5, 0.5)（黄金比例序列，不用随机数——同一数据永远同一张图）。"""
    return ((k * 0.6180339887498949) % 1.0) - 0.5


def cell_pos(ci, gi, ng):
    """第 ci 个类别、第 gi 组（共 ng 组）在横轴上的位置：类别居中，组在 0.8 宽内并排。"""
    return ci + (gi - (ng - 1) / 2) * (0.8 / ng)


def box_stats(vals):
    """箱线统计（Tukey：须 = 1.5 IQR 内最远的数据点；之外的全是离群点，**原样画出、不删**）。"""
    s = sorted(vals)
    q1, med, q3 = quantile(s, 0.25), quantile(s, 0.5), quantile(s, 0.75)
    iqr = q3 - q1
    inside = [v for v in s if q1 - 1.5 * iqr <= v <= q3 + 1.5 * iqr]
    return {
        "med": med,
        "q1": q1,
        "q3": q3,
        "whislo": inside[0],
        "whishi": inside[-1],
        "fliers": [v for v in s if v < inside[0] or v > inside[-1]],
    }


def kde_curve(vals, points=64):
    """高斯核密度（Scott 带宽），在数据范围内取 `points` 个点，密度按最大值归一到 1：`(网格, 密度)`。"""
    n = len(vals)
    lo, hi = min(vals), max(vals)
    sd = sd_of(vals)
    bw = sd * n ** (-0.2) if sd == sd and sd > 0 else (abs(mean_of(vals)) * 0.05 or 1.0)
    grid = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    dens = [math.fsum(math.exp(-0.5 * ((g - v) / bw) ** 2) for v in vals) for g in grid]
    top = max(dens)
    return grid, [d / top for d in dens]


def linear_fit(xs, ys):
    """最小二乘直线 `(截距, 斜率)`；少于 2 个点或 x 全相同 → None。"""
    n = len(xs)
    if n < 2:
        return None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


# ---- 比较各组（comparison）
def comparison_data(header, rows, x, y, group, facet, order):
    """每个（分面, 类别, 分组）格子里的有效 y 值（行序）。x / 分组 / 分面为空的行不画；y 不是数（空、NaN、文字）的行不画。
    类别顺序：`as_given` = 首次出现；`value_desc` / `value_asc` = 按该类别全部有效值的均值排。"""
    xi, yi = header.index(x), header.index(y)
    gi = header.index(group) if group else None
    fi = header.index(facet) if facet else None
    cats, groups, panels, cells = [], [], [], {}
    for r in rows:
        c = r[xi].strip()
        g = r[gi].strip() if gi is not None else ""
        p = r[fi].strip() if fi is not None else ""
        if not c or (gi is not None and not g) or (fi is not None and not p):
            continue
        kind, v = parse_cell(r[yi])
        if kind != "numeric":
            continue
        cats.append(c)
        groups.append(g)
        panels.append(p)
        cells.setdefault((p, c, g), []).append(v)
    cats, groups, panels = distinct(cats), distinct(groups) or [""], distinct(panels) or [""]
    if order != "as_given":
        pooled = {c: [v for (p, cc, g), vs in cells.items() if cc == c for v in vs] for c in cats}
        means = {c: mean_of(vs) for c, vs in pooled.items()}
        cats = sorted(cats, key=lambda c: means[c], reverse=(order == "value_desc"))
    return {"cats": cats, "groups": groups, "panels": panels, "cells": cells}


def comparison_cells(d, p, g):
    """这一格（分面 p、分组 g）里有数据的类别：`[(类别序号, [y…])]`。"""
    return [
        (ci, d["cells"][(p, c, g)]) for ci, c in enumerate(d["cats"]) if (p, c, g) in d["cells"]
    ]


def comparison_points(cells, gi, ng, jittered):
    xs, ys = [], []
    for ci, vals in cells:
        for k, v in enumerate(vals):
            x = cell_pos(ci, gi, ng)
            if jittered:
                x = x + jitter(k) * (0.8 / ng) * 0.6
            xs.append(x)
            ys.append(v)
    return xs, ys


def comparison_hlines(cells, gi, ng, summary):
    """每个类别一条水平短线（中心值），用 NaN 隔开成一个 artist。"""
    half = (0.8 / ng) * 0.4
    xs, ys = [], []
    for ci, vals in cells:
        c = summarize(vals, summary)[0]
        if c == c:
            x = cell_pos(ci, gi, ng)
            xs += [x - half, x + half, math.nan]
            ys += [c, c, math.nan]
    return xs, ys


def comparison_bars(cells, gi, ng, summary):
    pos, heights = [], []
    for ci, vals in cells:
        c = summarize(vals, summary)[0]
        if c == c:
            pos.append(cell_pos(ci, gi, ng))
            heights.append(c)
    return pos, heights


def comparison_errors(cells, gi, ng, summary):
    pos, centers, lower, upper = [], [], [], []
    for ci, vals in cells:
        c, lo, hi = summarize(vals, summary)
        if lo == lo and hi == hi:
            pos.append(cell_pos(ci, gi, ng))
            centers.append(c)
            lower.append(c - lo)
            upper.append(hi - c)
    return pos, centers, lower, upper


def comparison_boxes(cells, gi, ng):
    return [cell_pos(ci, gi, ng) for ci, _ in cells], [box_stats(v) for _, v in cells]


def comparison_violins(cells, gi, ng):
    return [(ci, cell_pos(ci, gi, ng), *kde_curve(v)) for ci, v in cells]


def cell_counts(d):
    return sorted({len(v) for v in d["cells"].values()})


def category_labels(d, p, with_n):
    """横轴刻度文字：类别名；各格 n 不一样时再加一行 n（有分组时按组的顺序用 / 隔开）。"""
    out = []
    for c in d["cats"]:
        if with_n:
            n = "/".join(str(len(d["cells"].get((p, c, g), []))) for g in d["groups"])
            out.append(f"{c}\n(n = {n})")
        else:
            out.append(c)
    return out


# ---- 变化趋势（trend）
def trend_data(header, rows, x, ys, group, facet, unit, x_is_date):
    """每个（分面, 分组, y 列, 个体）一条序列 `(xs, vals)`，按 x 稳定排序（同 x 保持行序）。x 不是值（日期格式不对 / 非数）、
    分组 / 分面 / 个体为空的行不画；y 不是数 → NaN（折线在这里断开，不插值不填 0）。"""
    xi = header.index(x)
    yis = [header.index(y) for y in ys]
    gi = header.index(group) if group else None
    fi = header.index(facet) if facet else None
    ui = header.index(unit) if unit else None
    panels, groups, series = [], [], {}
    for r in rows:
        if x_is_date:
            dv = parse_date(r[xi])
            xv = date_number(dv) if dv is not None else None
        else:
            kind, v = parse_cell(r[xi])
            xv = v if kind == "numeric" else None
        g = r[gi].strip() if gi is not None else ""
        p = r[fi].strip() if fi is not None else ""
        u = r[ui].strip() if ui is not None else ""
        if (
            xv is None
            or (gi is not None and not g)
            or (fi is not None and not p)
            or (ui is not None and not u)
        ):
            continue
        panels.append(p)
        groups.append(g)
        for y, yi in zip(ys, yis):
            kind, v = parse_cell(r[yi])
            series.setdefault((p, g, y, u), []).append((xv, v if kind == "numeric" else math.nan))
    out = {}
    for key, pairs in series.items():
        pairs = sorted(pairs, key=lambda t: t[0])
        out[key] = ([a for a, _ in pairs], [b for _, b in pairs])
    return {"panels": distinct(panels) or [""], "groups": distinct(groups) or [""], "series": out}


def trend_units(d, p, g, y):
    """这一条（分面, 分组, y 列）下每个个体一条序列：`[(个体, xs, vals)]`（没有个体列时只有一条，个体为空串）。"""
    return [(k[3], xs, vals) for k, (xs, vals) in d["series"].items() if k[:3] == (p, g, y)]


def trend_pool(d, p, g, y):
    """把各个体的序列并起来（按 x 稳定排序）：汇总、散点、拟合、水平线都用它。"""
    pairs = []
    for _, xs, vals in trend_units(d, p, g, y):
        pairs += list(zip(xs, vals))
    pairs.sort(key=lambda t: t[0])
    return [a for a, _ in pairs], [b for _, b in pairs]


def finite_pairs(xs, vals):
    keep = [(a, b) for a, b in zip(xs, vals) if b == b]
    return [a for a, _ in keep], [b for _, b in keep]


def line_xy(xs, vals):
    """逐点折线的数据：全是 NaN（没有一个点可画）→ None。"""
    return (xs, vals) if any(v == v for v in vals) else None


def trend_centers(pool, summary):
    """每个不同的 x 一个汇总值：`(xs, 中心, 下限, 上限)`；这个 x 上没有有效值 → 中心 NaN（线在这里断开）。"""
    xs, vals = pool
    by_x = {}
    for a, b in zip(xs, vals):
        by_x.setdefault(a, [])
        if b == b:
            by_x[a].append(b)
    ux = sorted(by_x)
    cols = [summarize(by_x[a], summary) for a in ux]
    return ux, [c[0] for c in cols], [c[1] for c in cols], [c[2] for c in cols]


def band_xy(xs, lower, upper):
    keep = [(a, lo, hi) for a, lo, hi in zip(xs, lower, upper) if lo == lo and hi == hi]
    return [k[0] for k in keep], [k[1] for k in keep], [k[2] for k in keep]


def error_xy(xs, centers, lower, upper):
    keep = [
        (a, c, c - lo, hi - c)
        for a, c, lo, hi in zip(xs, centers, lower, upper)
        if lo == lo and hi == hi
    ]
    return [k[0] for k in keep], [k[1] for k in keep], [k[2] for k in keep], [k[3] for k in keep]


def fit_segment(pool):
    """拟合直线在数据 x 范围两端的两个点；点不够 → None。"""
    xs, ys = finite_pairs(*pool)
    fit = linear_fit(xs, ys)
    if fit is None:
        return None
    a, b = fit
    x0, x1 = min(xs), max(xs)
    return [x0, x1], [a + b * x0, a + b * x1]


def hline_segment(pool, summary):
    """整条序列一个汇总值的水平线，横跨数据的 x 范围；没有有效值 → None。"""
    xs, ys = finite_pairs(*pool)
    if not ys:
        return None
    c = summarize(ys, summary)[0]
    return [min(xs), max(xs)], [c, c]


def trend_counts(d, summary_layers_pool):
    """每个汇总点背后的样本个数（去重后的集合），给图下方的 n 说明。"""
    out = set()
    for xs, vals in summary_layers_pool:
        by_x = {}
        for a, b in zip(xs, vals):
            if b == b:
                by_x[a] = by_x.get(a, 0) + 1
        out |= set(by_x.values())
    return sorted(out)


def pick_fonts(latin, cjk):
    """从候选里各挑**第一个本机真有的**（常规与粗体字重都在）字体（拉丁文一个、中文回退一个）：整张候选清单都塞进 `font.family` 会让
    matplotlib 为每个缺字重 / 缺字体的名字各报一条 findfont 警告。"""
    from matplotlib import font_manager

    weights = {}
    for f in font_manager.fontManager.ttflist:
        weights.setdefault(f.name, set()).add(font_manager.weight_dict.get(f.weight, f.weight))
    have = {
        n for n, w in weights.items() if 400 in w and 700 in w
    }  # 常规与粗体都有的才用（轴标题是粗体）
    return [
        next(n for n in names if n in have)
        for names in (latin, cjk)
        if any(n in have for n in names)
    ]


RUNTIME_FUNCTIONS = (
    parse_cell,
    parse_date,
    date_number,
    read_delimited,
    read_xlsx,
    distinct,
    make_label,
    mean_of,
    sd_of,
    quantile,
    t_crit95,
    summarize,
    jitter,
    cell_pos,
    box_stats,
    kde_curve,
    linear_fit,
    comparison_data,
    comparison_cells,
    comparison_points,
    comparison_hlines,
    comparison_bars,
    comparison_errors,
    comparison_boxes,
    comparison_violins,
    cell_counts,
    category_labels,
    trend_data,
    trend_units,
    trend_pool,
    finite_pairs,
    line_xy,
    trend_centers,
    band_xy,
    error_xy,
    fit_segment,
    hline_segment,
    trend_counts,
    pick_fonts,
)
#: 只有 XLSX 数据源的脚本才内嵌 `read_xlsx` 与 xlsxread；CSV / TSV 的脚本里没有它们

# =========================================================================== 样式（出处：出版规范 profile）
#: Okabe & Ito（2008）的色盲安全分类色（公开发表的调色板，无许可证限制）；去掉对比弱的黄色。仓库里没有批准的分类色表
#: （profile 推荐的 Crameri batlowS 不随 matplotlib 提供，装了才有）——要换由用户拍板。
PALETTE = ("#0072B2", "#D55E00", "#009E73", "#E69F00", "#56B4E9", "#CC79A7", "#000000")
LINESTYLES = ("-", "--", ":")
SUMMARY_TEXT = {
    "mean_sd": "mean ± SD",
    "mean_sem": "mean ± SEM",
    "mean_ci95": "mean with 95% CI",
    "median_iqr": "median with IQR",
}


def style_from_profile(profile: dict | None = None) -> dict:
    """出版规范 profile → 模板要的数字。取不到的项才用具名默认（`_STYLE_DEFAULTS`）。"""
    if profile is None:
        from . import profiles

        profile = profiles.load()
    d = dict(_STYLE_DEFAULTS)

    def pick(key, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            d[key] = float(value)

    w = profile.get("widths_mm") or {}
    pick("width_single_mm", w.get("single"))
    pick("width_double_mm", w.get("double"))
    pick("font_pt", profile.get("default_font_size_pt"))
    pick("min_font_pt", profile.get("min_effective_font_size_pt"))
    lws = [v for v in (profile.get("line_widths_pt") or []) if isinstance(v, (int, float))]
    if lws:
        d["line_widths_pt"] = [float(v) for v in sorted(lws)]
    axis = profile.get("axis_policy") or {}
    frame = axis.get("frame_linewidth_pt")
    if isinstance(frame, list) and frame and isinstance(frame[0], (int, float)):
        d["frame_lw_pt"] = float(frame[0])
    if axis.get("tick_direction") in ("in", "out", "inout"):
        d["tick_direction"] = axis["tick_direction"]
    if isinstance(axis.get("enclosed_spines"), bool):
        d["enclosed_spines"] = axis["enclosed_spines"]
    legend = profile.get("legend_policy") or {}
    if isinstance(legend.get("frame"), bool):
        d["legend_frame"] = legend["frame"]
    ratios = {
        r.get("id"): r for r in profile.get("allowed_aspect_ratios") or [] if isinstance(r, dict)
    }
    for key, rid in (("ratio_single", "4:3"), ("ratio_multi", "16:9")):
        r = ratios.get(rid) or next(iter(ratios.values()), None)
        if (
            r
            and isinstance(r.get("w"), (int, float))
            and isinstance(r.get("h"), (int, float))
            and r["w"] > 0
        ):
            d[key] = float(r["h"]) / float(r["w"])
    fam = profile.get("font_family") or {}
    latin = [n for n in (fam.get("latin_accepted") or []) if isinstance(n, str)]
    if latin:
        d["latin_fonts"] = latin
    cjk = (profile.get("cjk_fallback") or {}).get("accepted")
    if isinstance(cjk, list) and cjk:
        d["cjk_fonts"] = [n for n in cjk if isinstance(n, str)]
    weights = profile.get("text_weight_policy") or {}
    if weights.get("axis_label") in ("normal", "bold"):
        d["axis_label_weight"] = weights["axis_label"]
    return d


#: profile 里取不到时的具名默认（与 `lab-publication-v1` 同值，仅作后备）
_STYLE_DEFAULTS = {
    "width_single_mm": 80.0,
    "width_double_mm": 150.0,
    "font_pt": 9.0,
    "min_font_pt": 8.0,
    "line_widths_pt": [0.5, 0.75, 1.0, 1.5],
    "frame_lw_pt": 0.75,
    "tick_direction": "in",
    "enclosed_spines": True,
    "legend_frame": False,
    "ratio_single": 0.75,
    "ratio_multi": 9 / 16,
    "latin_fonts": ["Times New Roman", "Liberation Serif", "Tinos", "STIXGeneral"],
    "cjk_fonts": ["Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "SimHei"],
    "axis_label_weight": "bold",
}

_MM = 25.4


# =========================================================================== 渲染
def _check(entry: adf.TableEntry, recipe: recipe_mod.FigureRecipe) -> None:
    if recipe.family not in RENDERERS:
        raise TemplateError("template_family_unsupported", recipe.family)
    if recipe.table != entry.path or recipe.sheet != entry.sheet:
        raise TemplateError("template_recipe_mismatch", "table")
    cols = entry.summary.per_column
    named = [recipe.x, *recipe.y, recipe.group, recipe.facet, recipe.unit]
    if any(c is not None and c not in cols for c in named):
        raise TemplateError("template_recipe_mismatch", "column")
    allowed = MARKS_RENDERED[recipe.family]
    for layer in recipe.layers:
        if layer.mark not in allowed:
            raise TemplateError("template_mark_unsupported", layer.mark)
    if recipe.coord != "cartesian":
        raise TemplateError("template_option_unsupported", "coord")
    if recipe.paired:
        raise TemplateError("template_option_unsupported", "paired")
    if recipe.family == "comparison":
        if recipe.scale not in ("linear", "log_y"):
            raise TemplateError("template_option_unsupported", "scale")
    if (
        recipe.family == "trend"
        and recipe.scale in ("log_x", "log_xy")
        and entry.summary.per_column[recipe.x].kind == "date"
    ):
        raise TemplateError("template_option_unsupported", "scale")


def _summary_layers(recipe: recipe_mod.FigureRecipe) -> list[str]:
    return [layer.summary for layer in recipe.layers if layer.summary != "none"]


def _summary_text(recipe: recipe_mod.FigureRecipe) -> str:
    return " / ".join(dict.fromkeys(SUMMARY_TEXT[s] for s in _summary_layers(recipe)))


def _source_comment(entry: adf.TableEntry, recipe: recipe_mod.FigureRecipe, data_rel: str) -> str:
    body = {
        k: v for k, v in recipe.to_dict().items() if k != "why"
    }  # why 只显示给用户，不进脚本（ADR 0178）
    return json.dumps(
        {
            "data": data_rel,
            "sha256": entry.summary.sha256,
            "recipe": body,
            "generator": "data_to_figure/v2",
        },
        ensure_ascii=True,
        sort_keys=True,
    )


XLSX_BEGIN = "# ---- begin xlsxread（Tavotto 的 XLSX 读取，原文内嵌）"
XLSX_END = "# ---- end xlsxread"


def _xlsx_source() -> str:
    """`xlsxread` 的源码原文，去掉模块 docstring 与 `from __future__` 行（脚本自己在顶部写 `__future__`）。"""
    src = inspect.getsource(xlsxread)
    tree = ast.parse(src)
    lines = src.splitlines()
    drop = set()
    first = tree.body[0]
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
        drop |= set(range(first.lineno, first.end_lineno + 1))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            drop |= set(range(node.lineno, node.end_lineno + 1))
    return "\n".join(ln for i, ln in enumerate(lines, start=1) if i not in drop).strip("\n") + "\n"


RUNTIME_PREFIX = "tavotto_plot_"
RUNTIME_ALIAS = "tp"
_RUNTIME_DOC = (
    '"""Tavotto 生成的绘图公共模块：读表（CSV / TSV / XLSX）、单元格解析、汇总、布局、抖动、箱线 / 核密度 / 拟合的数值函数。\n\n'
    "由 Tavotto 生成，被**同目录里 Tavotto 生成的绘图脚本**共用；请不要修改它——文件名里的 8 位十六进制是本文件全部字节的\n"
    "sha256 前缀，内容变了名字就对不上。Tavotto 只新建、不覆盖：以后版本换了实现会生成另一个名字的文件，现有脚本继续用\n"
    "它们当时 import 的这一份。模块里不含任何数据。\n"
    '"""\n'
)


def _runtime_source() -> str:
    """公共模块的全文：说明 + 全部运行时函数的源码（`inspect.getsource`，与父进程算期望值调用的是同一批）+ `xlsxread` 的源码原文。
    对所有图逐字节相同，不含任何数据。"""
    parts = [
        _RUNTIME_DOC.rstrip("\n"),
        "from __future__ import annotations",
        "",
        "import csv",
        "import math",
        "import re",
        "",
        _ISO_DATE_PREAMBLE.rstrip("\n"),
        "",
        "",
    ]
    for f in RUNTIME_FUNCTIONS:
        parts.append(inspect.getsource(f).rstrip("\n"))
        parts.append("")
        parts.append("")
    parts += [XLSX_BEGIN, _xlsx_source().rstrip("\n"), XLSX_END, ""]
    return "\n".join(parts)


@functools.lru_cache(maxsize=1)
def runtime_module() -> tuple[str, bytes]:
    """`(文件名, 字节)`：与生成的脚本放在同一目录的公共模块。文件名按内容寻址 `tavotto_plot_<sha256 前 8 位>.py`
    （合法的模块名）。**落盘规则**（下一个 PR 实现，本模块不写任何文件）：脚本与公共模块都只新建；公共模块已存在且字节相同
    → 复用；存在但字节不同 → 按损坏报错，**不覆盖**；内容变了名字就变，老脚本继续 import 它当时的那一版。"""
    data = _runtime_source().encode("utf-8")
    return f"{RUNTIME_PREFIX}{hashlib.sha256(data).hexdigest()[:8]}.py", data


def _header(entry, recipe, data_rel, title, is_xlsx):
    name = runtime_module()[0][: -len(".py")]
    return [
        f'"""由 Tavotto 根据数据生成的图（data_to_figure v2：{title}）。之后就是一份普通的绘图脚本，可以随意修改。\n\n'
        f'读表、汇总、布局的函数在同目录的 {name}.py 里（Tavotto 生成，别改它；它不含数据）。"""',
        f"# tavotto-source: {_source_comment(entry, recipe, data_rel)}",
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "",
        "import matplotlib.pyplot as plt",
        "from matplotlib.patches import Patch",
        "",
        f"import {name} as {RUNTIME_ALIAS}",
        "",
        f"DATA = Path(__file__).resolve().parent / {data_rel!r}",
    ]


_RUNTIME_CALL = re.compile(r"\b(" + "|".join(f.__name__ for f in RUNTIME_FUNCTIONS) + r")\(")


def _via_runtime(lines: list[str]) -> list[str]:
    """脚本 `main()` 里对运行时函数的调用都经公共模块别名 `tp.` 走。只处理生成的代码行（数据里的文字只在顶部常量里，
    且是 `repr()` 字面量，不经过这里）。"""
    return [_RUNTIME_CALL.sub(lambda m: f"{RUNTIME_ALIAS}.{m.group(1)}(", ln) for ln in lines]


def _rc_lines(style: dict, many_panels: bool) -> list[str]:
    lw = style["line_widths_pt"]
    return [
        f"    fonts = pick_fonts({list(style['latin_fonts'])!r}, {list(style['cjk_fonts'])!r})",
        "    plt.rcParams.update(",
        "        {",
        '            "font.family": fonts or ["serif"],',
        '            "mathtext.fontset": "stix",',
        f'            "font.size": {style["font_pt"]!r},',
        f'            "axes.labelsize": {style["font_pt"]!r},',
        f'            "axes.labelweight": {style["axis_label_weight"]!r},',
        f'            "axes.titlesize": {style["font_pt"]!r},',
        f'            "xtick.labelsize": {style["font_pt"]!r},',
        f'            "ytick.labelsize": {style["font_pt"]!r},',
        f'            "legend.fontsize": {style["min_font_pt"]!r},',
        f'            "axes.linewidth": {style["frame_lw_pt"]!r},',
        f'            "xtick.direction": {style["tick_direction"]!r},',
        f'            "ytick.direction": {style["tick_direction"]!r},',
        f'            "axes.spines.top": {bool(style["enclosed_spines"])!r},',
        f'            "axes.spines.right": {bool(style["enclosed_spines"])!r},',
        f'            "legend.frameon": {bool(style["legend_frame"])!r},',
        f'            "lines.linewidth": {lw[min(2, len(lw) - 1)]!r},',
        '            "svg.fonttype": "none",',
        '            "pdf.fonttype": 42,',
        "        }",
        "    )",
    ]


def _figsize(style: dict, panels_expr: str) -> str:
    ws, wd = style["width_single_mm"] / _MM, style["width_double_mm"] / _MM
    return f"(({ws!r}, {ws * style['ratio_single']!r}) if {panels_expr} == 1 else ({wd!r}, {wd * style['ratio_multi']!r}))"


# --------------------------------------------------------------------------- comparison
MARKS_COMPARISON = ("points", "jitter", "box", "violin", "bar", "errorbar", "hline_summary")
MARKS_TREND = ("line", "step", "band", "points", "errorbar", "fit_line", "hline_summary")
MARKS_RENDERED = {"comparison": MARKS_COMPARISON, "trend": MARKS_TREND}


def _comparison_layer_lines(layer, ci_tag: str, lw: list[float]) -> list[str]:
    m, s = layer.mark, layer.summary
    thin, mid, thick = lw[0], lw[min(1, len(lw) - 1)], lw[-1]
    lab = f"make_label({m!r}, g, p)"
    if m in ("points", "jitter"):
        return [
            f"xs, ys = comparison_points(cells, gi, ng, {m == 'jitter'})",
            "if ys:",
            f"    ax.scatter(xs, ys, s=9, color=color, linewidths=0, label={lab})",
        ]
    if m == "hline_summary":
        return [
            f"xs, ys = comparison_hlines(cells, gi, ng, {s!r})",
            "if ys:",
            f"    ax.plot(xs, ys, color=color, linewidth={thick!r}, label={lab})",
        ]
    if m == "bar":
        return [
            f"pos, heights = comparison_bars(cells, gi, ng, {s!r})",
            "if heights:",
            f"    ax.bar(pos, heights, width=(0.8 / ng) * 0.85, color=color, alpha=0.85, label={lab})",
        ]
    if m == "errorbar":
        return [
            f"pos, centers, lower, upper = comparison_errors(cells, gi, ng, {s!r})",
            "if pos:",
            "    ax.errorbar(",
            f"        pos, centers, yerr=[lower, upper], fmt='none', ecolor=color, elinewidth={mid!r}, capsize=2, capthick={mid!r}, label={lab}",
            "    )",
        ]
    if m == "box":
        return [
            "pos, stats = comparison_boxes(cells, gi, ng)",
            "if stats:",
            "    ax.bxp(",
            "        stats, positions=pos, widths=(0.8 / ng) * 0.6, manage_ticks=False,",
            f"        boxprops={{'color': color, 'linewidth': {mid!r}}}, medianprops={{'color': color, 'linewidth': {thick!r}}},",
            f"        whiskerprops={{'color': color, 'linewidth': {mid!r}}}, capprops={{'color': color, 'linewidth': {mid!r}}},",
            f"        flierprops={{'marker': 'o', 'markersize': 2.5, 'markerfacecolor': 'none', 'markeredgecolor': color, 'markeredgewidth': {thin!r}}},",
            "    )",
        ]
    if m == "violin":
        return [
            "for ci, pos, grid, dens in comparison_violins(cells, gi, ng):",
            "    half = (0.8 / ng) * 0.45",
            "    ax.fill_betweenx(",
            "        grid, [pos - half * v for v in dens], [pos + half * v for v in dens],",
            f"        facecolor=color, alpha=0.35, edgecolor=color, linewidth={mid!r}, label=make_label('violin', g, p, d['cats'][ci]),",
            "    )",
        ]
    raise TemplateError("template_mark_unsupported", m)


def _render_comparison(entry, recipe, data_rel, stem, style) -> str:
    is_xlsx = entry.path.lower().endswith(".xlsx")
    summ = _summary_text(recipe)
    lw = style["line_widths_pt"]
    L = _header(entry, recipe, data_rel, "比较各组", is_xlsx)
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    L += [
        f"X = {recipe.x!r}",
        f"Y = {recipe.y[0]!r}",
        f"GROUP = {recipe.group!r}",
        f"FACET = {recipe.facet!r}",
        f"ORDER = {recipe.order!r}",
        f"XLABEL = {adf.axis_label(recipe.x)!r}",
        f"YLABEL = {adf.axis_label(recipe.y[0])!r}",
        f"PALETTE = {list(PALETTE)!r}",
    ]
    if is_xlsx:
        reader = "read_xlsx(DATA, SHEET)"
    else:
        delim = "'\\t'" if entry.path.lower().endswith((".tsv", ".tab")) else "','"
        reader = f"read_delimited(DATA, {delim})"
    L += ["", "", "def main():"]
    main_at = len(L)
    L.append(f"    header, rows = {reader}")
    L += [
        "    d = comparison_data(header, rows, X, Y, GROUP, FACET, ORDER)",
        "    cats, groups, panels = d['cats'], d['groups'], d['panels']",
        "    ng = len(groups)",
    ]
    L += _rc_lines(style, True)
    L += [
        f"    fig, axes = plt.subplots(1, len(panels), sharey=True, figsize={_figsize(style, 'len(panels)')}, squeeze=False)",
        "    uneven = len(cell_counts(d)) > 1",
        "    for pi, (p, ax) in enumerate(zip(panels, axes[0])):",
        "        for gi, g in enumerate(groups):",
        "            color = PALETTE[gi % len(PALETTE)]",
        "            cells = comparison_cells(d, p, g)",
    ]
    for layer in recipe.layers:
        for line in _comparison_layer_lines(layer, "", lw):
            L.append("            " + line)
    L += [
        "        ax.set_xticks(range(len(cats)))",
        f"        ax.set_xticklabels(category_labels(d, p, uneven and {bool(summ)!r}))",
        "        ax.set_xlim(-0.6, len(cats) - 0.4)",
        "        ax.set_xlabel(XLABEL)",
        "        if FACET:",
        "            ax.set_title(p)",
        "        if pi == 0:",
        "            ax.set_ylabel(YLABEL)",
    ]
    if recipe.scale == "log_y":
        L.append('        ax.set_yscale("log")')
    if any(layer.mark == "bar" for layer in recipe.layers):
        L.append("        ax.set_ylim(bottom=0)")
    L += [
        "    if GROUP:",
        "        axes[0][0].legend(",
        "            handles=[Patch(facecolor=PALETTE[i % len(PALETTE)], label=name) for i, name in enumerate(groups)],",
        f"            frameon={bool(style['legend_frame'])!r},",
        "        )",
    ]
    if summ:
        L += [
            "    counts = cell_counts(d)",
            f"    note = {summ!r} + ('; n = %d' % counts[0] if len(counts) == 1 else '; n per group in tick labels')",
            f"    fig.text(0.01, 0.01, note, fontsize={style['min_font_pt']!r}, ha='left', va='bottom')",
            "    fig.tight_layout(rect=(0, 0.06, 1, 1))",
        ]
    else:
        L.append("    fig.tight_layout()")
    L += [
        f"    fig.savefig(Path(__file__).resolve().parent / {stem + '.pdf'!r})",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    return "\n".join(L[:main_at] + _via_runtime(L[main_at:]))


# --------------------------------------------------------------------------- trend
def _trend_layer_lines(layer, lw: list[float], unit: bool, multi_y: bool) -> list[str]:
    m, s = layer.mark, layer.summary
    mid, thick = lw[min(1, len(lw) - 1)], lw[-1]
    tag = "ytag"
    lab = f"make_label({m!r}, g, p, {tag})"
    if m in ("points",):
        return [
            "xs, ys = finite_pairs(*pool)",
            "if ys:",
            f"    ax.scatter(xs, ys, s=9, color=color, linewidths=0, label={lab})",
        ]
    if m in ("line", "step"):
        draw = "ax.plot" if m == "line" else "ax.step"
        extra = "" if m == "line" else ', where="post"'
        if s == "none":
            return [
                "for u, uxs, uvals in trend_units(d, p, g, y):",
                "    ln = line_xy(uxs, uvals)",
                "    if ln:",
                f"        {draw}(*ln, color=color, linestyle=ls, linewidth={thick!r}{extra}, label=make_label({m!r}, g, p, {tag}, u))",
            ]
        return [
            f"cx, cc, _lo, _hi = trend_centers(pool, {s!r})",
            "ln = line_xy(cx, cc)",
            "if ln:",
            f"    {draw}(*ln, color=color, linestyle=ls, linewidth={thick!r}{extra}, label={lab})",
        ]
    if m == "band":
        return [
            f"cx, cc, lo, hi = trend_centers(pool, {s!r})",
            "bx, blo, bhi = band_xy(cx, lo, hi)",
            "if bx:",
            f"    ax.fill_between(bx, blo, bhi, facecolor=color, alpha=0.25, linewidth=0, label={lab})",
        ]
    if m == "errorbar":
        return [
            f"cx, cc, lo, hi = trend_centers(pool, {s!r})",
            "ex, ec, elow, ehigh = error_xy(cx, cc, lo, hi)",
            "if ex:",
            f"    ax.errorbar(ex, ec, yerr=[elow, ehigh], fmt='none', ecolor=color, elinewidth={mid!r}, capsize=2, capthick={mid!r}, label={lab})",
        ]
    if m == "fit_line":
        return [
            "seg = fit_segment(pool)",
            "if seg:",
            f"    ax.plot(*seg, color=color, linestyle='--', linewidth={mid!r}, label={lab})",
        ]
    if m == "hline_summary":
        return [
            f"seg = hline_segment(pool, {s!r})",
            "if seg:",
            f"    ax.plot(*seg, color=color, linestyle=':', linewidth={thick!r}, label={lab})",
        ]
    raise TemplateError("template_mark_unsupported", m)


def _render_trend(entry, recipe, data_rel, stem, style) -> str:
    is_xlsx = entry.path.lower().endswith(".xlsx")
    x_is_date = entry.summary.per_column[recipe.x].kind == "date"
    summ = _summary_text(recipe)
    lw = style["line_widths_pt"]
    multi_y = len(recipe.y) > 1
    L = _header(entry, recipe, data_rel, "变化趋势", is_xlsx)
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    ylabel = ", ".join(adf.axis_label(y) for y in recipe.y)
    L += [
        f"X = {recipe.x!r}",
        f"YS = {list(recipe.y)!r}",
        f"GROUP = {recipe.group!r}",
        f"FACET = {recipe.facet!r}",
        f"UNIT = {recipe.unit!r}",
        f"X_IS_DATE = {x_is_date!r}",
        f"XLABEL = {adf.axis_label(recipe.x)!r}",
        f"YLABEL = {ylabel!r}",
        f"PALETTE = {list(PALETTE)!r}",
        f"LINESTYLES = {list(LINESTYLES)!r}",
    ]
    if is_xlsx:
        reader = "read_xlsx(DATA, SHEET)"
    else:
        delim = "'\\t'" if entry.path.lower().endswith((".tsv", ".tab")) else "','"
        reader = f"read_delimited(DATA, {delim})"
    L += ["", "", "def main():"]
    main_at = len(L)
    L.append(f"    header, rows = {reader}")
    if x_is_date:
        L += [
            "    import matplotlib.dates",
            "",
            "    try:  # 日期数值按 1970 纪元算（与 Tavotto 的期望值一致）；本进程已用过别的纪元时 matplotlib 会拒绝，保持原状",
            '        matplotlib.dates.set_epoch("1970-01-01T00:00:00")',
            "    except RuntimeError:",
            "        pass",
        ]
    L += [
        "    d = trend_data(header, rows, X, YS, GROUP, FACET, UNIT, X_IS_DATE)",
        "    groups, panels = d['groups'], d['panels']",
    ]
    L += _rc_lines(style, True)
    L += [
        f"    fig, axes = plt.subplots(1, len(panels), sharey=True, figsize={_figsize(style, 'len(panels)')}, squeeze=False)",
        "    pools = []",
        "    for pi, (p, ax) in enumerate(zip(panels, axes[0])):",
        "        for gi, g in enumerate(groups):",
        "            for yi, y in enumerate(YS):",
        "                color = PALETTE[(gi if GROUP else yi) % len(PALETTE)]",
        "                ls = LINESTYLES[yi % len(LINESTYLES)] if GROUP else '-'",
        "                ytag = y if len(YS) > 1 else ''",
        "                pool = trend_pool(d, p, g, y)",
        "                pools.append(pool)",
    ]
    for layer in recipe.layers:
        for line in _trend_layer_lines(layer, lw, recipe.unit is not None, multi_y):
            L.append("                " + line)
    L += [
        "        ax.set_xlabel(XLABEL)",
        "        if FACET:",
        "            ax.set_title(p)",
        "        if pi == 0:",
        "            ax.set_ylabel(YLABEL)",
    ]
    if x_is_date:
        L.append("        ax.xaxis_date()")
    if recipe.scale in ("log_y", "log_xy"):
        L.append('        ax.set_yscale("log")')
    if recipe.scale in ("log_x", "log_xy"):
        L.append('        ax.set_xscale("log")')
    L += [
        "    keys = groups if GROUP else (YS if len(YS) > 1 else [])",
        "    if keys:",
        "        axes[0][0].legend(",
        "            handles=[Patch(facecolor=PALETTE[i % len(PALETTE)], label=name) for i, name in enumerate(keys)],",
        f"            frameon={bool(style['legend_frame'])!r},",
        "        )",
    ]
    if summ:
        L += [
            "    counts = trend_counts(d, pools)",
            "    n_text = '' if not counts else ('n = %d' % counts[0] if len(counts) == 1 else 'n = %d–%d per point' % (counts[0], counts[-1]))",
            f"    fig.text(0.01, 0.01, {summ!r} + ('; ' + n_text if n_text else ''), fontsize={style['min_font_pt']!r}, ha='left', va='bottom')",
            "    fig.tight_layout(rect=(0, 0.06, 1, 1))",
        ]
    else:
        L.append("    fig.tight_layout()")
    L += [
        f"    fig.savefig(Path(__file__).resolve().parent / {stem + '.pdf'!r})",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    return "\n".join(L[:main_at] + _via_runtime(L[main_at:]))


RENDERERS = {"comparison": _render_comparison, "trend": _render_trend}


@dataclass(frozen=True)
class Rendered:
    """一次渲染的产物：脚本文本 + 它 import 的公共模块（文件名与字节，对所有图逐字节相同）。落盘是调用方的事（下一个 PR）：
    两个文件都只新建；公共模块已存在且字节相同就复用，存在但字节不同按损坏报错、不覆盖。"""

    script: str
    runtime_name: str
    runtime: bytes


def render_recipe_script(
    entry: adf.TableEntry,
    recipe: recipe_mod.FigureRecipe,
    *,
    data_rel: str,
    stem: str,
    style: dict | None = None,
) -> Rendered:
    """配方 → 短脚本 + 公共模块。`entry` 是配方指的那张表（来自 `profile_folder`，不带行数据：脚本运行时自己读文件）；
    `data_rel` 是数据文件相对**脚本所在目录**的 POSIX 路径（调用方保证不出数据文件夹）；`stem` 决定输出 `<stem>.pdf`；
    `style` 缺省取 `profiles.load()` 的默认出版规范。调用方须先 `validate_recipe`；对不认识的组合抛 `TemplateError`。
    脚本里数据文字只以 `repr()` 字面量出现；公共模块不含任何数据。"""
    _check(entry, recipe)
    if style is None:
        style = style_from_profile()
    name, data = runtime_module()
    return Rendered(RENDERERS[recipe.family](entry, recipe, data_rel, stem, style), name, data)


# =========================================================================== 期望数据指纹（父进程；与脚本同一批函数）
@dataclass(frozen=True)
class LayerCoverage:
    index: int
    mark: str
    checked: bool
    reason: str  # checked=False 时：为什么不核


@dataclass(frozen=True)
class Coverage:
    """这份配方哪些层能核、哪些不核。`raw_checked`：至少有一层带**原始数据**的可核层；`any_checked`：至少有一层可核。
    `any_checked == False` 时调用方要如实报告「这张图的数据没法核」，不能当成 verified。"""

    layers: tuple[LayerCoverage, ...]
    raw_checked: bool
    any_checked: bool

    def to_dict(self) -> dict:
        return {
            "layers": [
                {"index": c.index, "mark": c.mark, "checked": c.checked, "reason": c.reason}
                for c in self.layers
            ],
            "raw_checked": self.raw_checked,
            "any_checked": self.any_checked,
        }


_UNCHECKED = {
    "errorbar": "errorbar_has_no_label",  # 误差棒容器在 manifest 里没有 label，验证器按 label 匹配
    "band": "fill_has_no_label",  # fill_between 的 PolyCollection 同上
    "box": "matplotlib_derived",  # 箱线由 matplotlib 的 bxp 拼成多条无 label 的线
    "violin": "fill_has_no_label",
}
_CHECKED_RAW = frozenset({"points", "jitter"})


def fingerprint_coverage(recipe: recipe_mod.FigureRecipe) -> Coverage:
    out = []
    raw = False
    for i, layer in enumerate(recipe.layers):
        m = layer.mark
        if m in _UNCHECKED:
            out.append(LayerCoverage(i, m, False, _UNCHECKED[m]))
            continue
        out.append(LayerCoverage(i, m, True, ""))
        if m in _CHECKED_RAW or (m in ("line", "step") and layer.summary == "none"):
            raw = True
    return Coverage(tuple(out), raw, any(c.checked for c in out))


def _entry(label: str, part: str, flat: list[float], shape: tuple[int, ...]) -> dict:
    return {"label": label, "part": part, "digest": adf.digest(flat, shape)}


def _interleave(xs, ys) -> list[float]:
    return [v for pair in zip(xs, ys) for v in pair]


def expected_fingerprints(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> list[dict]:
    """`[{label, part, digest}]`：按配方「这份脚本画出来应当是什么数据」，只含能逐字节算准的层（见模块文档与
    `fingerprint_coverage`）。`values` 是带行数据的摘要（对配方指的那张表 `inspect_data_source`）；与脚本调用同一批函数。"""
    if recipe.family not in RENDERERS:
        raise TemplateError("template_family_unsupported", recipe.family)
    if recipe.family == "comparison":
        return _expected_comparison(values, recipe)
    return _expected_trend(values, recipe)


def _expected_comparison(values, recipe) -> list[dict]:
    d = comparison_data(
        values.columns, values.rows, recipe.x, recipe.y[0], recipe.group, recipe.facet, recipe.order
    )
    ng = len(d["groups"])
    out = []
    for p in d["panels"]:
        for gi, g in enumerate(d["groups"]):
            cells = comparison_cells(d, p, g)
            for layer in recipe.layers:
                m, s = layer.mark, layer.summary
                lab = make_label(m, g, p)
                if m in ("points", "jitter"):
                    xs, ys = comparison_points(cells, gi, ng, m == "jitter")
                    if ys:
                        out.append(_entry(lab, "offsets", _interleave(xs, ys), (len(ys), 2)))
                elif m == "hline_summary":
                    xs, ys = comparison_hlines(cells, gi, ng, s)
                    if ys:
                        out.append(_entry(lab, "xy", _interleave(xs, ys), (len(ys), 2)))
                elif m == "bar":
                    _, heights = comparison_bars(cells, gi, ng, s)
                    if heights:
                        out.append(
                            _entry(
                                lab,
                                "bars",
                                _interleave([0.0] * len(heights), heights),
                                (len(heights), 2),
                            )
                        )
    return out


def _expected_trend(values, recipe) -> list[dict]:
    x_is_date = values.per_column[recipe.x].kind == "date"
    d = trend_data(
        values.columns,
        values.rows,
        recipe.x,
        list(recipe.y),
        recipe.group,
        recipe.facet,
        recipe.unit,
        x_is_date,
    )
    out = []
    for p in d["panels"]:
        for g in d["groups"]:
            for y in recipe.y:
                ytag = y if len(recipe.y) > 1 else ""
                pool = trend_pool(d, p, g, y)
                for layer in recipe.layers:
                    m, s = layer.mark, layer.summary
                    lab = make_label(m, g, p, ytag)
                    if m == "points":
                        xs, ys = finite_pairs(*pool)
                        if ys:
                            out.append(_entry(lab, "offsets", _interleave(xs, ys), (len(ys), 2)))
                    elif m in ("line", "step"):
                        if s == "none":
                            for u, uxs, uvals in trend_units(d, p, g, y):
                                ln = line_xy(uxs, uvals)
                                if ln:
                                    out.append(
                                        _entry(
                                            make_label(m, g, p, ytag, u),
                                            "xy",
                                            _interleave(*ln),
                                            (len(ln[0]), 2),
                                        )
                                    )
                        else:
                            cx, cc, _lo, _hi = trend_centers(pool, s)
                            ln = line_xy(cx, cc)
                            if ln:
                                out.append(_entry(lab, "xy", _interleave(*ln), (len(ln[0]), 2)))
                    elif m == "fit_line":
                        seg = fit_segment(pool)
                        if seg:
                            out.append(_entry(lab, "xy", _interleave(*seg), (2, 2)))
                    elif m == "hline_summary":
                        seg = hline_segment(pool, s)
                        if seg:
                            out.append(_entry(lab, "xy", _interleave(*seg), (2, 2)))
    return out


# 让 ruff 知道这些只为被内嵌 / 被测试用而导入
_ = (datetime, re)
