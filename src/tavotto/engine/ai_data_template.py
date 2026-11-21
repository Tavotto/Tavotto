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
同一图元以不同 role 叠两层时 label 写成 `图元@role`（`_layer_tags`），保证整张图里每个可核层的 label 唯一。
"""

from __future__ import annotations

import ast
import bisect
import datetime
import functools
import hashlib
import inspect
import io
import json
import math
import re
import threading
import tokenize
from dataclasses import dataclass, replace

from . import ai_data_figure as adf, ai_data_recipe as recipe_mod, xlsxread
from .ai_data_figure import (
    NumCat,
    SemicolonCell,
    apply_cells,
    apply_numcat,
    apply_stack,
    date_number,
    delimited_records,
    iter_records,
    iter_table,
    parse_cell,
    parse_date,
    resolve_header,
    table_from_text,
    xvg_label,
    xvg_text,
)

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


class LargeRuleError(ValueError):
    """大表上数据相关的资格判据没过（折线的 x 有重复 / 没排序、系列不够多个 x……）：`code` 是 `ai_data_recipe.REJECTIONS` 里的稳定码
    （与小表上 `validate_recipe` 对同一条规则报的码一致，前端已有文案）。小表的这些规则读行数据判，大表要把整表流过一遍才知道，所以在缩减那一遍里判。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# =========================================================================== 内嵌运行时（这一段的函数源码会原样进脚本）
# 约束：只用 math / csv / re / datetime 与彼此，不引用本模块的任何别的名字；Python 3.10 可跑。
_ISO_DATE_PREAMBLE = 'import datetime as _dt\n_ISO_DATE = re.compile(r"\\d{4}-\\d{2}-\\d{2}")\n_EPOCH = _dt.date(1970, 1, 1)\n'


def read_delimited(path, delimiter, header_row=None, units_row=False):
    """文本表格 → `(表头, 数据行)`：UTF-8（可带 BOM）；全空行不算数据行；表头去首尾空白。`delimiter` 是 `,` / `\t` / `;` / `ws`（连续空白）；
    `header_row`（1 起的物理行号）不给 = 第一条非空记录是表头，给了 = 跳过它之前的说明行；`units_row` 真 = 表头后紧跟的单位行不是数据、切掉
    （读法全文在 `table_from_text`，Tavotto 读出来的是同一张表）。"""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        text = fh.read()
    header, rows, _lines = table_from_text(text, delimiter, header_row, units_row)
    return header, rows


def read_xlsx(path, sheet):
    """XLSX 一张表 → `(表头, 数据行)`：读法就是内嵌的 `XlsxBook`（与 Tavotto 的 `xlsxread` 同一份源码）。"""
    with XlsxBook(path) as book:  # noqa: F821 - 由内嵌的 xlsxread 源码提供
        table = [r for r in book.read(sheet) if any(c.strip() for c in r)]
    return [h.strip() for h in table[0]], table[1:]


def group_key(label):
    """一个分组列的单元格在分组时的键：去首尾空白、区分大小写、不做 Unicode 规范化——`comparison_data` / `trend_data` / `relationship_data` 与
    `ai_data_figure._summarize` 的取值集合都是这个写法（`cell.strip()`）。合画的文件标签是否「分得开」就按它判（`merge_tables` 与父进程同一个函数）。"""
    return label.strip()


def merge_tables(tables, labels, column):
    """几份列名与顺序都相同的表 → 一张表（合画）：按给定顺序逐份拼行，表头末尾多一列 `column`，值是该份的 `labels[i]`。
    `tables` 是 `[(表头, 数据行), …]`（各份读法与单份一样，由调用方先读好）；某份表头与第一份不同直接 `ValueError`——数据在生成之后变了，
    不悄悄错位拼接；标签按 `group_key` 有重复也直接 `ValueError("tables_labels_collide")`。只拼内存里的副本，数据文件不动。"""
    header = list(tables[0][0])
    if len({group_key(lab) for lab in labels}) != len(labels):
        raise ValueError(
            "tables_labels_collide"
        )  # 两个文件的标签在分组时会被并成一组：绝不静默并组
    out = []
    for (head, rows), label in zip(tables, labels):
        if list(head) != header:
            raise ValueError("tables_columns_differ")
        out.extend([*r, label] for r in rows)
    return [*header, column], out


def distinct(values):
    """按首次出现的顺序去重；整列都是整数编码的类别（`NumCat`，见 `apply_numcat`）时改按数值升序（类别顺序按编码大小，不看谁先出现）。"""
    out = list(dict.fromkeys(values))
    if out and all(isinstance(v, NumCat) for v in out):
        out.sort(key=lambda v: v.number)
    return out


def make_label(mark, *parts):
    """artist 的 label：`图元 | 分组 | 分面 | …`（空的部分略去）。每个数据 artist 的 label 在整张图里唯一——
    Tavotto 按 label 找「画的是哪份数据」。`mark` 由模板给（同一图元在两层出现时带层的 role，见 `_layer_tags`）；数据里的文字
    先把反斜杠与竖线转义，不会靠自带的「 | 」把两个不同的格拼成同一个名字。"""
    return " | ".join([mark] + [p.replace("\\", "\\\\").replace("|", "\\|") for p in parts if p])


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


def scott_bandwidth(vals):
    """核密度的带宽规则（全产品只有这一条）：Scott 规则 `带宽 = 样本标准差 × n^(-1/5)`；标准差为 0（或 n < 2）时退到
    `|均值| × 0.05`，均值也是 0 就取 1。"""
    n = len(vals)
    sd = sd_of(vals)
    return sd * n ** (-0.2) if sd == sd and sd > 0 else (abs(mean_of(vals)) * 0.05 or 1.0)


def kde_curve(vals, points=64):
    """高斯核密度（Scott 带宽），在数据范围内取 `points` 个点，密度按最大值归一到 1：`(网格, 密度)`（小提琴的形状）。"""
    lo, hi = min(vals), max(vals)
    bw = scott_bandwidth(vals)
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
    """每个（分面, 类别, 分组）格子里的有效 y 值（行序）。没有 x（`x` 为 None，distribution 的一种形态）时只有一个类别 `""`。
    给了 x / 分组 / 分面但格子为空的行不画；y 不是数（空、NaN、文字）的行不画。
    类别顺序：`as_given` = 首次出现；`value_desc` / `value_asc` = 按该类别全部有效值的均值排。"""
    xi, yi = (header.index(x) if x else None), header.index(y)
    gi = header.index(group) if group else None
    fi = header.index(facet) if facet else None
    cats, groups, panels, cells = [], [], [], {}
    for r in rows:
        c = r[xi].strip() if xi is not None else ""
        g = r[gi].strip() if gi is not None else ""
        p = r[fi].strip() if fi is not None else ""
        if (xi is not None and not c) or (gi is not None and not g) or (fi is not None and not p):
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


def isolated_idx(vals):
    """孤立的有效位置的下标：自己有值、前后都没有（缺失或到头）。折线在这里画不出线段，要靠标记让它可见；
    给 `markevery` 用（只标这几个点，别给整条线加标记）。"""
    n = len(vals)
    return [
        i
        for i, v in enumerate(vals)
        if v == v
        and (i == 0 or vals[i - 1] != vals[i - 1])
        and (i == n - 1 or vals[i + 1] != vals[i + 1])
    ]


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
    """误差带的数据：**保留缺失位置**（上下界任一为 NaN → 两个都置 NaN），`fill_between` 在 NaN 处把带断开成几段，与汇总线在
    同样的位置断开——不许把没有观测的区间两侧连起来；没有一个位置能画 → None。"""
    ok = [lo == lo and hi == hi for lo, hi in zip(lower, upper)]
    if not any(ok):
        return None
    nan = math.nan
    return (
        list(xs),
        [lo if k else nan for lo, k in zip(lower, ok)],
        [hi if k else nan for hi, k in zip(upper, ok)],
    )


def error_xy(xs, centers, lower, upper):
    keep = [
        (a, c, c - lo, hi - c)
        for a, c, lo, hi in zip(xs, centers, lower, upper)
        if lo == lo and hi == hi
    ]
    return [k[0] for k in keep], [k[1] for k in keep], [k[2] for k in keep], [k[3] for k in keep]


def fit_segment(pool, logx=False, logy=False):
    """最小二乘直线（描述性）：点不够或 x 全相同 → None。在**画出来的坐标**里拟合：对数轴那一维先取 log10 再拟合，
    再换回数据值——两轴同为线性或同为对数时直线在图上仍是直线，取数据 x 范围两端两个点；只有一维是对数时它在图上是曲线，
    沿 x 取 50 个点。对数轴上有 0 或负数 → None。"""
    xs, ys = finite_pairs(*pool)
    if (logx and any(x <= 0 for x in xs)) or (logy and any(y <= 0 for y in ys)):
        return None
    tx = [math.log10(x) for x in xs] if logx else xs
    ty = [math.log10(y) for y in ys] if logy else ys
    fit = linear_fit(tx, ty)
    if fit is None:
        return None
    a, b = fit
    t0, t1 = min(tx), max(tx)
    n = 2 if logx == logy else 50
    ts = [t0 + (t1 - t0) * i / (n - 1) for i in range(n)]
    fx = [10.0**t if logx else t for t in ts]
    fy = [a + b * t for t in ts]
    return fx, [10.0**v if logy else v for v in fy]


def hline_segment(pool, summary):
    """整条序列一个汇总值的水平线，横跨数据的 x 范围；没有有效值 → None。"""
    xs, ys = finite_pairs(*pool)
    if not ys:
        return None
    c = summarize(ys, summary)[0]
    return [min(xs), max(xs)], [c, c]


# ---- 看两个量的关系（relationship）
def relationship_data(header, rows, x, y, group, facet):
    """每个（分面, 分组）里 x、y 都是有限数的点（行序）；分组 / 分面为空的行不画；x 或 y 不是数（空、NaN、文字）的行不画。"""
    xi, yi = header.index(x), header.index(y)
    gi = header.index(group) if group else None
    fi = header.index(facet) if facet else None
    panels, groups, pairs = [], [], {}
    for r in rows:
        g = r[gi].strip() if gi is not None else ""
        p = r[fi].strip() if fi is not None else ""
        if (gi is not None and not g) or (fi is not None and not p):
            continue
        kx, vx = parse_cell(r[xi])
        ky, vy = parse_cell(r[yi])
        if kx != "numeric" or ky != "numeric":
            continue
        panels.append(p)
        groups.append(g)
        xs, ys = pairs.setdefault((p, g), ([], []))
        xs.append(vx)
        ys.append(vy)
    return {"panels": distinct(panels) or [""], "groups": distinct(groups) or [""], "pairs": pairs}


def relationship_pairs(d, p, g):
    return d["pairs"].get((p, g), ([], []))


def all_values(d):
    """整张图里的全部有效值（各分面、各分组并起来）：直方图的分箱按它定，各组共用同一组分箱才可比。"""
    return [v for vs in d["cells"].values() for v in vs]


def pooled_values(d, p, g):
    """这一格（分面 p、分组 g）里的全部值（并起各类别）。"""
    return [v for (pp, c, gg), vs in d["cells"].items() if pp == p and gg == g for v in vs]


def hist_edges(vals):
    """直方图的分箱规则（全产品只有这一条）：Freedman–Diaconis——箱宽 `2 × IQR × n^(-1/3)`，箱数 = 向上取整(范围 / 箱宽)，
    限制在 1–50；IQR 为 0 时退到 Sturges（箱数 = ceil(log2 n) + 1）；全部值相同则取以该值为中心、宽 1 的一个箱。
    箱边在数据范围内等分，返回 `箱数 + 1` 个边。"""
    s = sorted(vals)
    n = len(s)
    lo, hi = s[0], s[-1]
    if hi == lo:
        return [lo - 0.5, lo + 0.5]
    iqr = quantile(s, 0.75) - quantile(s, 0.25)
    width = 2 * iqr * n ** (-1 / 3)
    bins = math.ceil((hi - lo) / width) if width > 0 else math.ceil(math.log2(n)) + 1
    bins = max(1, min(50, bins))
    return [lo + (hi - lo) * i / bins for i in range(bins + 1)]


def hist_counts(vals, edges):
    """每个箱里的个数：箱左闭右开，最后一个箱右端也算在内（与 numpy 同一约定）。"""
    counts = [0] * (len(edges) - 1)
    for v in vals:
        counts[min(max(bisect.bisect_right(edges, v) - 1, 0), len(counts) - 1)] += 1
    return counts


def hist_heights(counts, n, width, density):
    """柱高：个数；`density` 为真时换成概率密度 `个数 / (n × 箱宽)`（与密度曲线叠画时用，面积为 1）。"""
    return [c / (n * width) for c in counts] if density else [float(c) for c in counts]


def density_curve(vals, points=100):
    """高斯核密度估计（带宽 `scott_bandwidth`），在 `[最小值 - 3 × 带宽, 最大值 + 3 × 带宽]` 上取 `points` 个点：
    `(网格, 概率密度)`，面积约为 1。"""
    n = len(vals)
    bw = scott_bandwidth(vals)
    lo, hi = min(vals) - 3 * bw, max(vals) + 3 * bw
    grid = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    norm = n * bw * math.sqrt(2 * math.pi)
    return grid, [
        math.fsum(math.exp(-0.5 * ((g - v) / bw) ** 2) for v in vals) / norm for g in grid
    ]


def ecdf_xy(vals):
    """经验累积分布的阶梯：从 `(最小值, 0)` 起，把值排序后第 i 个值处升到 `i / n`（逐点，不分箱、不合并重复）。"""
    s = sorted(vals)
    n = len(s)
    return [s[0]] + s, [0.0] + [(i + 1) / n for i in range(n)]


# ---- 大表缩减（ADR 0178 修订·大表）：流式一遍，内存只与「箱数 × 系列数」有关
def bin_update(b, yv, row):
    """把一行（`row = (行序号, 单元格)`，`yv` = 各 y 列的有限数值或 None）并进箱 `b` 的 y 角色：每个 y 列记最小 / 最大值所在行。
    严格比较、行序号递增，所以并列时先出现的赢——与 `bin_merge` 的并列规则（行序号小的赢）一致。"""
    for j in range(len(yv)):
        v = yv[j]
        if v is None:
            continue
        o = 4 + 4 * j
        if b[o] is None or v < b[o]:
            b[o], b[o + 1] = v, row
        if b[o + 2] is None or v > b[o + 2]:
            b[o + 2], b[o + 3] = v, row


def bin_merge(a, b, ny):
    """两个相邻箱并成一个（箱宽翻倍时）：每个角色取两边更极端的；并列取行序号小的。结果只取决于两个箱里的行，与并入的先后无关。"""
    out = list(a)
    if b[0] < a[0] or (b[0] == a[0] and b[1][0] < a[1][0]):
        out[0], out[1] = b[0], b[1]
    if b[2] > a[2] or (b[2] == a[2] and b[3][0] < a[3][0]):
        out[2], out[3] = b[2], b[3]
    for j in range(ny):
        o = 4 + 4 * j
        if b[o] is not None and (
            a[o] is None or b[o] < a[o] or (b[o] == a[o] and b[o + 1][0] < a[o + 1][0])
        ):
            out[o], out[o + 1] = b[o], b[o + 1]
        if b[o + 2] is not None and (
            a[o + 2] is None
            or b[o + 2] > a[o + 2]
            or (b[o + 2] == a[o + 2] and b[o + 3][0] < a[o + 3][0])
        ):
            out[o + 2], out[o + 3] = b[o + 2], b[o + 3]
    return out


def bin_rows(b, ny):
    """一个箱要留下的行：最左 / 最右（按 x）、每个 y 列的最小 / 最大。"""
    out = [b[1], b[3]]
    for j in range(ny):
        o = 4 + 4 * j
        out += [s for s in (b[o + 1], b[o + 3]) if s is not None]
    return out


def reduce_trend(header, body, spec):
    """流式缩减一张大表给「趋势」图（折线 / 阶梯 / 点）用：`(留下的行, 信息)`。行是原文单元格（与读表读出来的行同形），按文件顺序；
    下游的 `trend_data` 与小表一字不差地吃这些行。

    **做法**：行按（分面, 分组）分系列，与 `trend_data` 同口径（分组 / 分面为空的行不画；x 不是值的行不画）；每个系列在 **x 值**上分箱
    （不是文件里的位置：未排序、多条交错的序列照样对）：箱宽是 2 的幂、起点是该系列第一行的 x，箱数超过 `spec["bins"]` 时箱宽翻倍并箱
    （一遍扫完，不要先知道 x 的范围）。每个箱留下 最左 / 最右 的行与每个 y 列取最小 / 最大值的行（上下包络），所以
    **每个 y 的全局最小 / 最大、x 的两端都原样在图上**。
    **断开**（`spec["lines"]`，有折线 / 阶梯层时）：折线在 y 不是数的地方断开，所以每个系列每个 y 列里**每一段**缺失（夹在两个有限值之间的连续非数行）
    各留下它的第一行——这样留下的任意两个相邻点之间，只要原数据在它们之间断开过，就一定有一条断开标记；每个系列每个 y 列至多留 `spec["gap_max"]`
    段，更多就不再留并标 `gap_over`（调用方拒绝这张折线，不画一条会把断开连起来的线）。系列数超过 `spec["series_max"]` →
    `ValueError("reduce_series_overflow")`（数据与出备选时不一样了，不悄悄多画）。
    `信息`：读到的行数、x 不是值的行数与前 100 个行号、每个 y 列的（有限数个数, 最小, 最大）——整表上的独立累计，父进程拿它与概览扫描的
    统计对账；`mono`：每个系列的 x 是否按文件顺序不减；`dup`：某个 y 列在同一系列里是否有两个有限观测的 x 相同（只数 x 与该 y 都有限的行，
    与小表的规则同口径；只在 `mono` 时可信）；`x_counts`：每个有（x 与第一个 y 都有限的）观测的系列的不同 x 个数（封顶 4）；
    `ords`：留下的行的行序号；`gap_runs` / `gap_over`：断开的段数 / 是否超过上限；`grid`：每个系列最终的箱（起点, 箱宽）。"""
    xi = header.index(spec["x"])
    yis = [header.index(y) for y in spec["ys"]]
    kis = [header.index(k) for k in spec["keys"]]
    x_is_date = spec["date"]
    nb = spec["bins"]
    smax = spec["series_max"]
    lines = spec.get("lines", False)
    gap_max = spec.get("gap_max", 0)
    ny = len(yis)
    series = {}
    gap_rows = {}
    gap_runs = 0
    gap_over = False
    n_rows = 0
    x_invalid = 0
    examples = []
    ystat = [[0, None, None] for _ in yis]
    for ln, r in body:
        n_rows += 1
        yv = [None] * ny
        for j in range(ny):
            kind, v = parse_cell(r[yis[j]])
            if kind == "numeric":
                yv[j] = v
                s = ystat[j]
                s[0] += 1
                if s[1] is None:
                    s[1] = s[2] = v
                elif v < s[1]:
                    s[1] = v
                elif v > s[2]:
                    s[2] = v
        if x_is_date:
            dv = parse_date(r[xi])
            xv = date_number(dv) if dv is not None else None
        else:
            kind, v = parse_cell(r[xi])
            xv = v if kind == "numeric" else None
        if xv is None:
            x_invalid += 1
            if len(examples) < 100:
                examples.append(ln)
            continue
        key = tuple(r[k].strip() for k in kis)
        if "" in key:
            continue
        st = series.get(key)
        if st is None:
            if len(series) >= smax:
                raise ValueError("reduce_series_overflow")
            e = math.frexp(abs(xv))[1] if xv else 0
            st = series[key] = {
                "w": 2.0 ** (e - 52),
                "o": xv,
                "bins": {},
                "prev": None,
                "mono": True,
                "pv": [None] * ny,
                "dup": False,
                "xs": set(),
                "seen": [False] * ny,
                "ingap": [False] * ny,
                "gaps": [0] * ny,
            }
        prev = st["prev"]
        if prev is not None and xv < prev:
            st["mono"] = False
        st["prev"] = xv
        pv = st["pv"]
        for j in range(ny):
            if yv[j] is not None:
                if pv[j] is not None and xv == pv[j]:
                    st["dup"] = True
                pv[j] = xv
        if yv[0] is not None and len(st["xs"]) < 4:
            st["xs"].add(xv)
        row = (n_rows, r)
        if lines:
            for j in range(ny):
                if yv[j] is None:
                    if st["seen"][j] and not st["ingap"][j]:
                        st["ingap"][j] = True
                        gap_runs += 1
                        if st["gaps"][j] < gap_max:
                            st["gaps"][j] += 1
                            gap_rows[n_rows] = r
                        else:
                            gap_over = True
                else:
                    st["seen"][j] = True
                    st["ingap"][j] = False
        bins = st["bins"]
        k = math.floor((xv - st["o"]) / st["w"])
        b = bins.get(k)
        if b is None:
            b = bins[k] = [xv, row, xv, row] + [None] * (4 * ny)
            bin_update(b, yv, row)
            while len(bins) > nb:
                st["w"] *= 2.0
                merged = {}
                for kk, bb in bins.items():
                    nk = kk // 2
                    cur = merged.get(nk)
                    merged[nk] = bb if cur is None else bin_merge(cur, bb, ny)
                bins = st["bins"] = merged
            continue
        if xv < b[0]:
            b[0], b[1] = xv, row
        if xv > b[2]:
            b[2], b[3] = xv, row
        bin_update(b, yv, row)
    kept = dict(gap_rows)
    for st in series.values():
        for b in st["bins"].values():
            for slot in bin_rows(b, ny):
                kept[slot[0]] = slot[1]
    ords = sorted(kept)
    rows = [kept[i] for i in ords]
    counts = [len(s["xs"]) for s in series.values() if s["xs"]]
    info = {
        "rows": n_rows,
        "kept": len(rows),
        "ords": ords,
        "x_invalid": x_invalid,
        "examples": examples,
        "series": len(series),
        "y": [tuple(s) for s in ystat],
        "mono": all(s["mono"] for s in series.values()),
        "dup": any(s["dup"] for s in series.values()),
        "x_counts": counts,
        "bins": max((len(s["bins"]) for s in series.values()), default=0),
        "gap_runs": gap_runs,
        "gap_over": gap_over,
        "grid": {k: (s["o"], s["w"]) for k, s in series.items()},
    }
    return rows, info


def reduce_relationship(header, body, spec):
    """流式缩减一张大表给「两个量的关系」图（散点）用：`(留下的行, 信息)`。系列 = （分面, 分组），与 `relationship_data` 同口径（分组 / 分面为空、
    x 或 y 不是有限数的行不画）。每个系列留下 **等距抽样 + 四个极值点**：等距抽样 = 该系列第 0, K, 2K… 个有效点（K 是 2 的幂，留下的超过
    `spec["points"]` 就翻倍并丢掉不再满足的，一遍扫完、不要先知道行数）；极值点 = x 与 y 各自的最小 / 最大所在行（并列取先出现的），
    所以坐标范围与离群点照实在图上。系列数超过 `spec["series_max"]` → `ValueError("reduce_series_overflow")`。`信息`：读到的行数、x 不是有限数的行数与前
    100 个行号、x 与 y 在**整表**上的（有限数个数, 最小, 最大）（独立累计，父进程对账用）。"""
    xi = header.index(spec["x"])
    yi = header.index(spec["ys"][0])
    kis = [header.index(k) for k in spec["keys"]]
    cap = spec["points"]
    smax = spec["series_max"]
    series = {}
    n_rows = 0
    x_invalid = 0
    examples = []
    xstat = [0, None, None]
    ystat = [0, None, None]
    for ln, r in body:
        n_rows += 1
        kx, vx = parse_cell(r[xi])
        ky, vy = parse_cell(r[yi])
        if kx == "numeric":
            xstat[0] += 1
            if xstat[1] is None:
                xstat[1] = xstat[2] = vx
            elif vx < xstat[1]:
                xstat[1] = vx
            elif vx > xstat[2]:
                xstat[2] = vx
        else:
            x_invalid += 1
            if len(examples) < 100:
                examples.append(ln)
        if ky == "numeric":
            ystat[0] += 1
            if ystat[1] is None:
                ystat[1] = ystat[2] = vy
            elif vy < ystat[1]:
                ystat[1] = vy
            elif vy > ystat[2]:
                ystat[2] = vy
        if kx != "numeric" or ky != "numeric":
            continue
        key = tuple(r[k].strip() for k in kis)
        if "" in key:
            continue
        st = series.get(key)
        if st is None:
            if len(series) >= smax:
                raise ValueError("reduce_series_overflow")
            st = series[key] = {"n": 0, "k": 1, "kept": [], "ext": [None] * 4}
        i = st["n"]
        st["n"] = i + 1
        row = (n_rows, r)
        if i % st["k"] == 0:
            st["kept"].append((i, row))
            while len(st["kept"]) > cap:
                st["k"] *= 2
                st["kept"] = [t for t in st["kept"] if t[0] % st["k"] == 0]
        ext = st["ext"]
        if ext[0] is None or vx < ext[0][0]:
            ext[0] = (vx, row)
        if ext[1] is None or vx > ext[1][0]:
            ext[1] = (vx, row)
        if ext[2] is None or vy < ext[2][0]:
            ext[2] = (vy, row)
        if ext[3] is None or vy > ext[3][0]:
            ext[3] = (vy, row)
    kept = {}
    for st in series.values():
        for _i, slot in st["kept"]:
            kept[slot[0]] = slot[1]
        for e in st["ext"]:
            if e is not None:
                kept[e[1][0]] = e[1][1]
    ords = sorted(kept)
    rows = [kept[i] for i in ords]
    info = {
        "rows": n_rows,
        "kept": len(rows),
        "ords": ords,
        "x_invalid": x_invalid,
        "examples": examples,
        "series": len(series),
        "x": tuple(xstat),
        "y": [tuple(ystat)],
        "valid": min((s["n"] for s in series.values()), default=0),
        "strides": {k: s["k"] for k, s in series.items()},
    }
    return rows, info


def tick_rows(body, raw, progress):
    """透传 `body`，每 65536 行调一次 `progress(已读字节)`（父进程的进度 / 取消检查点；脚本里不传，不经过这里）。"""
    n = 0
    for item in body:
        n += 1
        if not n & 0xFFFF:
            progress(raw.tell())
        yield item


def reduce_file(path, delimiter, header_row, units_row, codec, spec, progress=None):
    """大表文件 → `(表头, 缩减后的行, 信息)`：流式读（`iter_table`，读法与小表同一份）+ 按 `spec["kind"]` 缩减。`codec` 是 Python 的 codec 名
    （由父进程按文件定下、以字面量写进脚本，脚本不探测）。`progress`：可选的 `f(已读字节)`，父进程用它报进度并在取消时抛异常。"""
    with open(path, "rb") as raw, io.TextIOWrapper(raw, encoding=codec, newline="") as fh:
        header, body = iter_table(fh, delimiter, header_row, units_row)
        if progress is not None:
            body = tick_rows(body, raw, progress)
        if spec["kind"] == "trend":
            rows, info = reduce_trend(header, body, spec)
        elif spec["kind"] == "relationship":
            rows, info = reduce_relationship(header, body, spec)
        else:
            raise ValueError("reduce_kind_unsupported")
    return header, rows, info


def read_reduced(path, delimiter, header_row, units_row, codec, spec):
    """生成的脚本在大表上读表的那一句：`(表头, 缩减后的行)`；之后与小表同一份画图代码。"""
    return reduce_file(path, delimiter, header_row, units_row, codec, spec)[:2]


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
    SemicolonCell,
    NumCat,
    parse_cell,
    parse_date,
    date_number,
    iter_records,
    delimited_records,
    resolve_header,
    xvg_label,
    xvg_text,
    iter_table,
    table_from_text,
    read_delimited,
    read_xlsx,
    apply_cells,
    apply_numcat,
    apply_stack,
    group_key,
    merge_tables,
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
    isolated_idx,
    trend_centers,
    band_xy,
    error_xy,
    fit_segment,
    hline_segment,
    scott_bandwidth,
    relationship_data,
    relationship_pairs,
    all_values,
    pooled_values,
    hist_edges,
    hist_counts,
    hist_heights,
    density_curve,
    ecdf_xy,
    bin_update,
    bin_merge,
    bin_rows,
    reduce_trend,
    reduce_relationship,
    tick_rows,
    reduce_file,
    read_reduced,
    pick_fonts,
)
#: 只有 XLSX 数据源的脚本才内嵌 `read_xlsx` 与 xlsxread；CSV / TSV 的脚本里没有它们

# =========================================================================== 样式（出处：出版规范 profile）
#: Okabe & Ito（2008）的色盲安全分类色（公开发表的调色板，无许可证限制）；去掉对比弱的黄色。仓库里没有批准的分类色表
#: （profile 推荐的 Crameri batlowS 不随 matplotlib 提供，装了才有）——要换由用户拍板。
PALETTE = ("#0072B2", "#D55E00", "#009E73", "#E69F00", "#56B4E9", "#CC79A7", "#000000")
LINESTYLES = ("-", "--", ":")  # 多个 y 列（分组时）的线型表
#: 拟合线的专用线型：不在 `LINESTYLES` 里，保证「拟合线」与「第二个 y 的线」永不同形（两者同图出现时各有说明）
FIT_LINESTYLE = "-."
assert FIT_LINESTYLE not in LINESTYLES
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
    if entry.summary.large is not None and (
        recipe.family not in ("trend", "relationship") or recipe.unit is not None or recipe.cells
    ):
        raise TemplateError(
            "template_option_unsupported", "large"
        )  # 大表只有趋势的缩减实现（见 `recipe_mod.LARGE_MARKS`）
    cols = entry.summary.per_column
    if recipe.stack:  # 把列当分组：被转的列在原表里，作图用的列在转换后的长表里（`stack_view`）
        if (
            recipe.family not in ("comparison", "distribution")
            or recipe.cells
            or recipe.numcat
            or recipe.unit is not None
            or entry.summary.large is not None
            or _merge_of(entry) is not None
            or any(c not in cols for c in recipe.stack)
        ):
            raise TemplateError("template_option_unsupported", "stack")
        cols = recipe_mod.stack_view(entry.summary, recipe.stack).per_column
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
    marks = {layer.mark for layer in recipe.layers}
    if recipe.family == "comparison" or (
        recipe.family == "distribution" and not marks & VALUE_ON_X
    ):
        if recipe.scale not in ("linear", "log_y"):
            raise TemplateError("template_option_unsupported", "scale")
    if recipe.family == "distribution":
        if marks & VALUE_ON_X and marks - VALUE_ON_X:
            raise TemplateError(
                "template_option_unsupported", "orientation"
            )  # 值在横轴的图元不与值在纵轴的叠
        if "ecdf" in marks and marks & {"hist", "density"}:
            raise TemplateError("template_option_unsupported", "ecdf_with_hist")  # 纵轴含义不同
        if marks & VALUE_ON_X and (recipe.scale != "linear" or recipe.x is not None):
            raise TemplateError("template_option_unsupported", "scale_or_x")
    if (
        recipe.family == "trend"
        and recipe.scale in ("log_x", "log_xy")
        and entry.summary.per_column[recipe.x].kind == "date"
    ):
        raise TemplateError("template_option_unsupported", "scale")


def _layer_tags(recipe: recipe_mod.FigureRecipe) -> list[str]:
    """每层进 label 的图元名：同一图元只出现一次就是图元名本身；以不同 role 出现多次（`validate_recipe` 允许）就写成
    `图元@role`——（图元, role）在配方里唯一，所以每个可核层的 label 在整张图里唯一。图例显示的文字另行构造，不受影响。"""
    count: dict[str, int] = {}
    for layer in recipe.layers:
        count[layer.mark] = count.get(layer.mark, 0) + 1
    return [
        f"{layer.mark}@{layer.role}" if count[layer.mark] > 1 else layer.mark
        for layer in recipe.layers
    ]


#: 逐位置汇总（每个 x 位置一个汇总值）的图元；其余带汇总的（`hline_summary`）汇总整条序列
_PER_POINT_MARKS = ("line", "step", "band", "errorbar")


def _summary_layers(recipe: recipe_mod.FigureRecipe) -> list[str]:
    return [layer.summary for layer in recipe.layers if layer.summary != "none"]


def _footer(recipe, style) -> list[str]:
    """图的收尾排版。**图里不写任何自动加的脚注**（2026-11-17 用户决定：没画的行、汇总 / 拟合的定义、n 都不进图；这些在界面右栏与响应字段里给）。
    只做 `tight_layout()`，不为脚注留白。"""
    return ["    fig.tight_layout()"]


#: 脚本头的来源注释：生产（`_source_comment`、`_header`）与解析（`parse_source_comment`）共用这两个常量
SOURCE_COMMENT_PREFIX = "# tavotto-source: "
SOURCE_GENERATOR = "data_to_figure/v2"
#: 合画（几份数据拼成一张图）的脚本用另一个 `generator`：`parse_source_comment` 只认 `SOURCE_GENERATOR`，所以合画脚本不会被当成「来自某一份数据」
SOURCE_GENERATOR_MERGE = "data_to_figure/v2-merge"
#: 解析来源注释只看脚本开头这么多行 / 字符，注释本身最长这么多字符（脚本是不可信输入）
SOURCE_SCAN_LINES = 64
SOURCE_SCAN_CHARS = 16 * 1024
SOURCE_COMMENT_MAX = 4096
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _source_comment(entry: adf.TableEntry, recipe: recipe_mod.FigureRecipe, data_rel: str) -> str:
    body = {
        k: v for k, v in recipe.to_dict().items() if k != "why"
    }  # why 只显示给用户，不进脚本（ADR 0178）
    merge = _merge_of(entry)
    if merge is not None:
        body["tables"] = [s.data_rel for s in merge.sources]
    return json.dumps(
        {
            "data": data_rel,
            "sha256": entry.summary.sha256,
            "recipe": body,
            "generator": SOURCE_GENERATOR if merge is None else SOURCE_GENERATOR_MERGE,
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def parse_source_comment(head: str) -> dict | None:
    """从一个脚本的开头文字里读出来源注释：`{"data": <脚本里写的数据路径，相对脚本所在目录>, "sheet": <工作表名 | None>}`；**格式不对一律回 None**。

    注释内容是不可信输入（任何脚本都能写一行一样的），所以严格：只看开头 `SOURCE_SCAN_LINES` 行；必须是 tokenize 认得的**真注释**
    （不是字符串 / docstring 里的一行字）、从第 0 列起、以 `SOURCE_COMMENT_PREFIX` 开头；整行纯 ASCII 且不超过 `SOURCE_COMMENT_MAX`；
    只认**第一条**（它不合格就是没有，不往后找）；JSON 对象的键恰好是 `data` / `sha256` / `recipe` / `generator`，`generator` 等于
    `SOURCE_GENERATOR`，`sha256` 是 64 位小写十六进制，`recipe` 是对象且其 `sheet` 缺省 / null / 1–255 字符不含控制字符的字符串，
    `data` 是 1–1024 字符、不含 NUL 与反斜杠的字符串。只返回路径与工作表名：配方、哈希都不外传。**不碰文件系统**。"""
    lines = head.splitlines(keepends=True)[:SOURCE_SCAN_LINES]
    comment = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO("".join(lines)).readline):
            if (
                tok.type == tokenize.COMMENT
                and tok.start[1] == 0
                and tok.string.startswith(SOURCE_COMMENT_PREFIX.rstrip())
            ):
                comment = tok.string
                break
    except (tokenize.TokenError, SyntaxError):
        pass  # 开头之后读不下去了：之前已经找到的照用，没找到就是没有
    if (
        comment is None
        or not comment.startswith(SOURCE_COMMENT_PREFIX)
        or len(comment) > SOURCE_COMMENT_MAX
        or not comment.isascii()
    ):
        return None
    try:
        meta = json.loads(comment[len(SOURCE_COMMENT_PREFIX) :])
    except (ValueError, RecursionError):
        return None
    if not isinstance(meta, dict) or set(meta) != {"data", "sha256", "recipe", "generator"}:
        return None
    data, sha, rec = meta["data"], meta["sha256"], meta["recipe"]
    if (
        meta["generator"] != SOURCE_GENERATOR
        or not isinstance(sha, str)
        or not _SHA256_HEX.fullmatch(sha)
        or not isinstance(rec, dict)
        or not isinstance(data, str)
        or not 0 < len(data) <= 1024
        or "\x00" in data
        or "\\" in data
    ):
        return None
    sheet = rec.get("sheet")
    if sheet is not None and (
        not isinstance(sheet, str)
        or not 0 < len(sheet) <= 255
        or any(ord(c) < 32 or ord(c) == 127 for c in sheet)
    ):
        return None
    return {"data": data, "sheet": sheet}


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
        "import bisect",
        "import csv",
        "import io",
        "import itertools",
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


def _read_lines(recipe, reader: str) -> list[str]:
    """`main()` 里读表那几行。配方带了「夹着文字格的数值列」的处理规则时，读完紧接着套规则（内存里的副本；文件不改）；图里不写说明（2026-11-17）。"""
    out = [f"    header, rows = {reader}"]
    if recipe.cells:
        out.append("    rows, _cell_notes, _cell_stats = apply_cells(header, rows, CELLS)")
    if recipe.numcat:
        out.append("    rows = apply_numcat(header, rows, NUMCATS)")
    if recipe.stack:  # 把列当分组：宽表转长表（只剩 值 / 类别 两列），之后与别的长表一样画
        out.append("    header, rows = apply_stack(header, rows, STACK, *STACK_NAMES)")
    return out


def _merge_of(entry):
    """合画的条目（`ai_data_merge.MergedEntry`）带 `merge`；单份条目没有这个属性。"""
    return getattr(entry, "merge", None)


def _is_xlsx(entry) -> bool:
    return _merge_of(entry) is None and entry.path.lower().endswith(".xlsx")


def _data_lines(entry, data_rel) -> list[str]:
    merge = _merge_of(entry)
    if merge is None:
        return [f"DATA = Path(__file__).resolve().parent / {data_rel!r}"]
    return [
        "DATAS = [",
        *[f"    Path(__file__).resolve().parent / {s.data_rel!r}," for s in merge.sources],
        "]",
        f"SHEETS = {[s.entry.sheet for s in merge.sources]!r}",
        f"LABELS = {[s.label for s in merge.sources]!r}",
        f"FILE_COLUMN = {merge.column!r}",
    ]


def _header(entry, recipe, data_rel, title, is_xlsx):
    name = runtime_module()[0][: -len(".py")]
    cells = [f"CELLS = {[c.args() for c in recipe.cells]!r}"] if recipe.cells else []
    if recipe.numcat:  # 整数编码的类别列（读表后规范成整数写法，类别按数值升序）
        cells.append(f"NUMCATS = {list(recipe.numcat)!r}")
    if (
        recipe.stack
    ):  # 把这几列当分组：被转成长表的列（顺序 = 类别顺序）与转换后「值 / 类别」两列的名字
        cells.append(f"STACK = {list(recipe.stack)!r}")
        cells.append(f"STACK_NAMES = {list(recipe_mod.stack_names(recipe.stack))!r}")
    return [
        f'"""由 Tavotto 根据数据生成的图（data_to_figure v2：{title}）。之后就是一份普通的绘图脚本，可以随意修改。\n\n'
        f'读表、汇总、布局的函数在同目录的 {name}.py 里（Tavotto 生成，别改它；它不含数据）。"""',
        f"{SOURCE_COMMENT_PREFIX}{_source_comment(entry, recipe, data_rel)}",
        "from __future__ import annotations",
        "",
        "from pathlib import Path",
        "",
        "import matplotlib.pyplot as plt",
        "from matplotlib.patches import Patch",
        "",
        f"import {name} as {RUNTIME_ALIAS}",
        "",
        *_data_lines(entry, data_rel),
        *cells,
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
MARKS_RELATIONSHIP = ("points", "fit_line")
MARKS_DISTRIBUTION = ("hist", "ecdf", "density", "box", "violin", "jitter", "points")
#: distribution 里「值在横轴」的图元（不带类别 x，按颜色分组）；其余图元值在纵轴、类别在横轴
VALUE_ON_X = frozenset({"hist", "ecdf", "density"})
MARKS_RENDERED = {
    "comparison": MARKS_COMPARISON,
    "trend": MARKS_TREND,
    "relationship": MARKS_RELATIONSHIP,
    "distribution": MARKS_DISTRIBUTION,
}


def _comparison_layer_lines(layer, tag: str, lw: list[float]) -> list[str]:
    m, s = layer.mark, layer.summary
    thin, mid, thick = lw[0], lw[min(1, len(lw) - 1)], lw[-1]
    lab = f"make_label({tag!r}, g, p)"
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
            f"        facecolor=color, alpha=0.35, edgecolor=color, linewidth={mid!r}, label=make_label({tag!r}, g, p, d['cats'][ci]),",
            "    )",
        ]
    raise TemplateError("template_mark_unsupported", m)


def _render_comparison(entry, recipe, data_rel, stem, style) -> str:
    is_xlsx = _is_xlsx(entry)
    lw = style["line_widths_pt"]
    with_n = (
        any(layer.summary != "none" for layer in recipe.layers) or recipe.family == "distribution"
    )
    L = _header(
        entry, recipe, data_rel, "比较各组" if recipe.family == "comparison" else "看分布", is_xlsx
    )
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    L += [
        f"X = {recipe.x!r}",
        f"Y = {recipe.y[0]!r}",
        f"GROUP = {recipe.group!r}",
        f"FACET = {recipe.facet!r}",
        f"ORDER = {recipe.order!r}",
        f"XLABEL = {adf.axis_label(recipe.x) if recipe.x else ''!r}",
        f"YLABEL = {adf.axis_label(recipe.y[0])!r}",
        f"PALETTE = {list(PALETTE)!r}",
    ]
    if is_xlsx:
        reader = "read_xlsx(DATA, SHEET)"
    else:
        reader = _reader_expr(entry, False)
    L += ["", "", "def main():"]
    main_at = len(L)
    L += _read_lines(recipe, reader)
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
    for layer, tag in zip(recipe.layers, _layer_tags(recipe)):
        for line in _comparison_layer_lines(layer, tag, lw):
            L.append("            " + line)
    if recipe.x is None:
        L.append("        ax.set_xticks([])  # 没有类别轴：只有一个位置，组在其中并排")
    else:
        L += [
            "        ax.set_xticks(range(len(cats)))",
            f"        ax.set_xticklabels(category_labels(d, p, uneven and {with_n!r}))",
        ]
    L += [
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
    L += _footer(recipe, style)
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
def _trend_layer_lines(layer, tag_m: str, lw: list[float], logs=(False, False)) -> list[str]:
    m, s = layer.mark, layer.summary
    mid, thick = lw[min(1, len(lw) - 1)], lw[-1]
    tag = "ytag"
    lab = f"make_label({tag_m!r}, g, p, {tag})"
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
                "        iso = isolated_idx(ln[1])",
                f"        {draw}(*ln, color=color, linestyle=ls, linewidth={thick!r}{extra}, marker='o' if iso else 'None', markersize=2.5, markevery=iso or None, label=make_label({tag_m!r}, g, p, {tag}, u))",
            ]
        return [
            f"cx, cc, _lo, _hi = trend_centers(pool, {s!r})",
            "ln = line_xy(cx, cc)",
            "if ln:",
            "    iso = isolated_idx(ln[1])",
            f"    {draw}(*ln, color=color, linestyle=ls, linewidth={thick!r}{extra}, marker='o' if iso else 'None', markersize=2.5, markevery=iso or None, label={lab})",
        ]
    if m == "band":
        return [
            f"cx, cc, lo, hi = trend_centers(pool, {s!r})",
            "band = band_xy(cx, lo, hi)",
            "if band:",
            f"    ax.fill_between(*band, facecolor=color, alpha=0.25, linewidth=0, label={lab})",
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
            f"seg = fit_segment(pool, {logs[0]!r}, {logs[1]!r})",
            "if seg:",
            f"    ax.plot(*seg, color=color, linestyle={FIT_LINESTYLE!r}, linewidth={mid!r}, label={lab})",
        ]
    if m == "hline_summary":
        return [
            f"seg = hline_segment(pool, {s!r})",
            "if seg:",
            f"    ax.plot(*seg, color=color, linestyle=':', linewidth={thick!r}, label={lab})",
        ]
    raise TemplateError("template_mark_unsupported", m)


def _render_trend(entry, recipe, data_rel, stem, style) -> str:
    is_xlsx = _is_xlsx(entry)
    x_is_date = entry.summary.per_column[recipe.x].kind == "date"
    lw = style["line_widths_pt"]
    L = _header(entry, recipe, data_rel, "变化趋势", is_xlsx)
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    large = entry.summary.large is not None
    if large:
        L.append(f"REDUCE = {large_spec(entry.summary, recipe)!r}")
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
        f"YLABELS = {[adf.axis_label(y) for y in recipe.y]!r}",
        f"PALETTE = {list(PALETTE)!r}",
        f"LINESTYLES = {list(LINESTYLES)!r}",
    ]
    if is_xlsx:
        reader = "read_xlsx(DATA, SHEET)"
    elif large:
        reader = _large_reader_expr(entry)
    else:
        reader = _reader_expr(entry, False)
    L += ["", "", "def main():"]
    main_at = len(L)
    L += _read_lines(recipe, reader)
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
    logs = (recipe.scale in ("log_x", "log_xy"), recipe.scale in ("log_y", "log_xy"))
    for layer, tag in zip(recipe.layers, _layer_tags(recipe)):
        for line in _trend_layer_lines(layer, tag, lw, logs):
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
        "    keys = groups if GROUP else (YLABELS if len(YS) > 1 else [])",
        "    handles = [Patch(facecolor=PALETTE[i % len(PALETTE)], label=name) for i, name in enumerate(keys)]",
        "    if GROUP and len(YS) > 1:  # 线型按 y 列区分：图例里补「线型 ↔ y 列」",
        "        handles += [",
        "            plt.Line2D([], [], color=PALETTE[-1], linestyle=LINESTYLES[i % len(LINESTYLES)], label=name)",
        "            for i, name in enumerate(YLABELS)",
        "        ]",
        "    if handles:",
        f"        axes[0][0].legend(handles=handles, frameon={bool(style['legend_frame'])!r})",
    ]
    L += _footer(recipe, style)
    L += [
        f"    fig.savefig(Path(__file__).resolve().parent / {stem + '.pdf'!r})",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]
    return "\n".join(L[:main_at] + _via_runtime(L[main_at:]))


# --------------------------------------------------------------------------- relationship
def _render_relationship(entry, recipe, data_rel, stem, style) -> str:
    is_xlsx = _is_xlsx(entry)
    lw = style["line_widths_pt"]
    mid = lw[min(1, len(lw) - 1)]
    logx, logy = recipe.scale in ("log_x", "log_xy"), recipe.scale in ("log_y", "log_xy")
    L = _header(entry, recipe, data_rel, "看两个量的关系", is_xlsx)
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    large = entry.summary.large is not None
    if large:
        L.append(f"REDUCE = {large_spec(entry.summary, recipe)!r}")
    L += [
        f"X = {recipe.x!r}",
        f"Y = {recipe.y[0]!r}",
        f"GROUP = {recipe.group!r}",
        f"FACET = {recipe.facet!r}",
        f"XLABEL = {adf.axis_label(recipe.x)!r}",
        f"YLABEL = {adf.axis_label(recipe.y[0])!r}",
        f"PALETTE = {list(PALETTE)!r}",
    ]
    reader = _large_reader_expr(entry) if large else _reader_expr(entry, is_xlsx)
    L += ["", "", "def main():"]
    main_at = len(L)
    L += _read_lines(recipe, reader)
    L += [
        "    d = relationship_data(header, rows, X, Y, GROUP, FACET)",
        "    groups, panels = d['groups'], d['panels']",
    ]
    L += _rc_lines(style, True)
    L += [
        f"    fig, axes = plt.subplots(1, len(panels), sharex=True, sharey=True, figsize={_figsize(style, 'len(panels)')}, squeeze=False)",
        "    for pi, (p, ax) in enumerate(zip(panels, axes[0])):",
        "        for gi, g in enumerate(groups):",
        "            color = PALETTE[gi % len(PALETTE)]",
        "            xs, ys = relationship_pairs(d, p, g)",
    ]
    for layer, tag in zip(recipe.layers, _layer_tags(recipe)):
        if layer.mark == "points":
            lines = [
                "if ys:",
                f"    ax.scatter(xs, ys, s=9, color=color, linewidths=0, label=make_label({tag!r}, g, p))",
            ]
        else:
            lines = [
                f"seg = fit_segment((xs, ys), {logx!r}, {logy!r})",
                "if seg:",
                f"    ax.plot(*seg, color=color, linestyle={FIT_LINESTYLE!r}, linewidth={mid!r}, label=make_label({tag!r}, g, p))",
            ]
        L += ["            " + ln for ln in lines]
    L += [
        "        ax.set_xlabel(XLABEL)",
        "        if FACET:",
        "            ax.set_title(p)",
        "        if pi == 0:",
        "            ax.set_ylabel(YLABEL)",
    ]
    if logx:
        L.append('        ax.set_xscale("log")')
    if logy:
        L.append('        ax.set_yscale("log")')
    L += _group_legend(style)
    L += _footer(recipe, style)
    L += _tail(stem)
    return "\n".join(L[:main_at] + _via_runtime(L[main_at:]))


# --------------------------------------------------------------------------- distribution
def _render_distribution(entry, recipe, data_rel, stem, style) -> str:
    marks = {layer.mark for layer in recipe.layers}
    if not marks & VALUE_ON_X:  # 值在纵轴：与 comparison 同一套布局（类别 x 可选）
        return _render_comparison(entry, recipe, data_rel, stem, style)
    is_xlsx = _is_xlsx(entry)
    lw = style["line_widths_pt"]
    thin, thick = lw[0], lw[-1]
    density_units = "density" in marks
    ylabel = (
        "cumulative proportion"
        if "ecdf" in marks
        else ("density" if "density" in marks else "count")
    )
    L = _header(entry, recipe, data_rel, "看分布", is_xlsx)
    if is_xlsx:
        L.append(f"SHEET = {recipe.sheet!r}")
    L += [
        f"Y = {recipe.y[0]!r}",
        f"GROUP = {recipe.group!r}",
        f"FACET = {recipe.facet!r}",
        f"XLABEL = {adf.axis_label(recipe.y[0])!r}",
        f"YLABEL = {ylabel!r}",
        f"PALETTE = {list(PALETTE)!r}",
    ]
    reader = _reader_expr(entry, is_xlsx)
    L += ["", "", "def main():"]
    main_at = len(L)
    L += _read_lines(recipe, reader)
    L += [
        "    d = comparison_data(header, rows, None, Y, GROUP, FACET, 'as_given')",
        "    groups, panels = d['groups'], d['panels']",
    ]
    if "hist" in marks:
        L += ["    edges = hist_edges(all_values(d))", "    width = edges[1] - edges[0]"]
    L += _rc_lines(style, True)
    L += [
        f"    fig, axes = plt.subplots(1, len(panels), sharex=True, sharey=True, figsize={_figsize(style, 'len(panels)')}, squeeze=False)",
        "    for pi, (p, ax) in enumerate(zip(panels, axes[0])):",
        "        for gi, g in enumerate(groups):",
        "            color = PALETTE[gi % len(PALETTE)]",
        "            vals = pooled_values(d, p, g)",
        "            if not vals:",
        "                continue",
    ]
    for layer, tag in zip(recipe.layers, _layer_tags(recipe)):
        m = layer.mark
        lab = f"make_label({tag!r}, g, p)"
        if m == "hist":
            lines = [
                "counts = hist_counts(vals, edges)",
                f"heights = hist_heights(counts, len(vals), width, {density_units!r})",
                "ax.bar(",
                "    edges[:-1], heights, width=width, align='edge', color=color, alpha=0.45,",
                f"    edgecolor=color, linewidth={thin!r}, label={lab},",
                ")",
            ]
        elif m == "density":
            lines = [
                "grid, dens = density_curve(vals)",
                f"ax.plot(grid, dens, color=color, linewidth={thick!r}, label={lab})",
            ]
        else:
            lines = [
                "xs, ys = ecdf_xy(vals)",
                f"ax.step(xs, ys, where='post', color=color, linewidth={thick!r}, label={lab})",
            ]
        L += ["            " + ln for ln in lines]
    L += [
        "        ax.set_xlabel(XLABEL)",
        "        if FACET:",
        "            ax.set_title(p)",
        "        if pi == 0:",
        "            ax.set_ylabel(YLABEL)",
    ]
    L += _group_legend(style)
    L += _footer(recipe, style)
    L += _tail(stem)
    return "\n".join(L[:main_at] + _via_runtime(L[main_at:]))


def _merged_reader_expr(merge) -> str:
    """合画：每一份各按自己的读法（分隔符 / 表头行 / 单位行 / 编码 / 工作表）读，再经公共模块的 `merge_tables` 拼成一张表。"""
    parts = [
        "            "
        + _reader_expr(
            s.entry, s.entry.path.lower().endswith(".xlsx"), f"DATAS[{i}]", f"SHEETS[{i}]"
        )
        + ","
        for i, s in enumerate(merge.sources)
    ]
    return (
        "merge_tables(\n        [\n"
        + "\n".join(parts)
        + "\n        ],\n        LABELS,\n        FILE_COLUMN,\n    )"
    )


def _reader_expr(entry, is_xlsx: bool, data: str = "DATA", sheet: str = "SHEET") -> str:
    """脚本里读表那一句。文本表格的读法（编码 + 分隔符 + 显式表头行）取自摘要（`read_text_table` 嗅探 / 调用方指定的结果），以字面量写进脚本——
    脚本不嗅探；Tavotto 算期望值读表用的是同一组参数（同一个 `table_from_text`），所以脚本读出来的和预览 / 资格判定读出来的是同一张表。
    老读法（首行表头、按后缀分隔）写成 `read_delimited(DATA, ',')`，与此前生成的脚本逐字相同。"""
    merge = _merge_of(entry)
    if merge is not None:
        return _merged_reader_expr(merge)
    if is_xlsx:
        return f"read_xlsx({data}, {sheet})"
    s = entry.summary
    delim = s.delimiter or ("\t" if entry.path.lower().endswith((".tsv", ".tab")) else ",")
    args = [repr(delim)] + ([repr(s.header_row)] if s.header_row is not None else [])
    if s.units_row:  # 只在嗅探到单位行时多写这一项：其余脚本逐字不变
        args = [*args[:1], repr(s.header_row), "True"]
    codec = adf.script_codec(s.encoding)
    if entry.path.lower().endswith(adf.XVG_SUFFIXES):
        # `.xvg`：列名在 `@` 指令里，先经运行时函数 `xvg_text` 改写成带表头行的制表符文本（父进程读表是同一个函数），再按嗅探出的读法读
        text = f"{data}.read_bytes().decode({(codec or 'utf-8-sig')!r})"
        return f"table_from_text(xvg_text({text}), {', '.join(args)})[:2]"
    if codec is not None:
        # 非 UTF-8（GB18030 / 有 BOM 的 UTF-16）：编码由父进程定下、以字面量写进脚本，脚本不探测。字节原样 `decode`（不经平台换行转换），
        # 再交给公共模块里同一个 `table_from_text`——与父进程读出的是同一张表；公共模块不变（哈希不换）。
        return f"table_from_text({data}.read_bytes().decode({codec!r}), {', '.join(args)})[:2]"
    return f"read_delimited({data}, {', '.join(args)})"


def _group_legend(style) -> list[str]:
    return [
        "    if GROUP:",
        "        axes[0][0].legend(",
        "            handles=[Patch(facecolor=PALETTE[i % len(PALETTE)], label=name) for i, name in enumerate(groups)],",
        f"            frameon={bool(style['legend_frame'])!r},",
        "        )",
    ]


def _tail(stem: str) -> list[str]:
    return [
        f"    fig.savefig(Path(__file__).resolve().parent / {stem + '.pdf'!r})",
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
        "",
    ]


RENDERERS = {
    "comparison": _render_comparison,
    "trend": _render_trend,
    "relationship": _render_relationship,
    "distribution": _render_distribution,
}
if set(RENDERERS) != set(
    recipe_mod.RENDERABLE
):  # 两张表必须逐项相同：声明能画的就得真有模板，反之亦然
    raise RuntimeError("ai_data_recipe.RENDERABLE 与 ai_data_template.RENDERERS 不一致")


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
#: 公开别名：图表桥接层按图元查不可核原因；行为与 `_UNCHECKED` 完全相同。
UNCHECKED_MARKS = _UNCHECKED
_CHECKED_RAW = frozenset({"points", "jitter", "ecdf"})  # ecdf 的阶梯点就是排序后的全部原始值


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


# --------------------------------------------------------------------------- 大表：缩减的参数、对账与判据（父进程）
#: 缩减后画出的数值总数的预算（两个坐标一起算）：manifest 的 `FINGERPRINT_BUDGET` 的一半，另一半留给图上别的元素与余量
LARGE_POINT_BUDGET = 1_000_000
#: 每个系列的箱数上限（再多就是一个像素里画几个箱，没有意义）
LARGE_BINS_MAX = 2000
LARGE_BINS_MIN = 20
#: 折线每个系列每个 y 列最多保留多少段断开（见 `reduce_trend`）；更多的断开就不画折线（散点照常）
LARGE_GAP_RUNS_MAX = 200
#: 关系图每个系列留下的点数上限 / 下限（等距抽样的点，另加 4 个极值点）
LARGE_POINTS_MAX = 20_000
LARGE_POINTS_MIN = 50


def large_spec(summary: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> dict:
    """缩减的参数（字面量写进脚本的 `REDUCE`）：只由配方与摘要里的**结构**事实定（系列数上界 = 分组 × 分面的取值个数），不含数据值。
    点数按预算反推：系列数 × 每系列最多留的点 × y 列数 × 层数 × 2 个坐标 ≤ `LARGE_POINT_BUDGET`——所以任何大表画出的数据总量都在
    manifest 的指纹预算以内，核对不会因超预算变成 unknown。趋势：按 x 分箱留包络（每箱最多 `2 + 3 × y 列数` 行）；关系：每系列等距抽样 + 4 个极值点。"""
    keys = [c for c in (recipe.group, recipe.facet) if c is not None]
    series = 1
    for c in keys:
        series *= max(1, summary.groups.get(c, 1))
    ny = len(recipe.y)
    layers = max(1, len(recipe.layers))
    if recipe.family == "relationship":
        points = LARGE_POINTS_MAX
        points = min(points, LARGE_POINT_BUDGET // (series * layers * 2) - 4)
        return {
            "kind": "relationship",
            "x": recipe.x,
            "ys": list(recipe.y),
            "keys": keys,
            "points": max(LARGE_POINTS_MIN, points),
            "series_max": series,
        }
    lines = any(
        ly.mark in recipe_mod.POINTWISE_LINES and ly.summary == recipe_mod.SUMMARY_NONE
        for ly in recipe.layers
    )
    gap_max = LARGE_GAP_RUNS_MAX if lines else 0
    per_bin_rows = 2 + 2 * ny
    # 每系列最多 `bins × 每箱行数 + y 列数 × 断开段数` 行，每行在每个 y 列、每层各画一个点（x、y 两个坐标）
    rows_budget = LARGE_POINT_BUDGET // (series * ny * layers * 2) - ny * gap_max
    bins = rows_budget // per_bin_rows
    return {
        "kind": "trend",
        "x": recipe.x,
        "ys": list(recipe.y),
        "keys": keys,
        "date": summary.per_column[recipe.x].kind == "date",
        "bins": max(LARGE_BINS_MIN, min(LARGE_BINS_MAX, bins)),
        "series_max": series,
        "lines": lines,
        "gap_max": gap_max,
    }


def _large_reader_expr(entry) -> str:
    s = entry.summary
    delim = s.delimiter or ("\t" if entry.path.lower().endswith((".tsv", ".tab")) else ",")
    codec = adf.large_codec(s.encoding)
    return (
        f"read_reduced(DATA, {delim!r}, {s.header_row!r}, {bool(s.units_row)!r}, {codec!r}, REDUCE)"
    )


_REDUCE_CACHE: dict = {}
_REDUCE_CACHE_MAX = 6
_REDUCE_LOCK = threading.Lock()


def gap_audit(values: adf.DataSummary, spec: dict, ords) -> int:
    """**独立于缩减**的断开对账（只在父进程、只在折线 + 有断开时多读一遍文件）：数「某个系列某个 y 列里，夹在两个有限值之间的连续非数行（一段断开）
    里一行都没被留下」的段数。缩减把断开弄丢（留下的点之间会被连成线）时它大于 0。不复用缩减里的任何状态，只用留下的行序号。"""
    keep = set(ords)
    xi = values.columns.index(spec["x"])
    yis = [values.columns.index(y) for y in spec["ys"]]
    kis = [values.columns.index(k) for k in spec["keys"]]
    state: dict = {}
    lost = 0
    with (
        open(values.large.path, "rb") as raw,
        io.TextIOWrapper(raw, encoding=adf.large_codec(values.encoding), newline="") as fh,
    ):
        _h, body = adf.iter_table(fh, values.delimiter or ",", values.header_row, values.units_row)
        for n, (_ln, r) in enumerate(body, start=1):
            if spec["date"]:
                good = adf.parse_date(r[xi]) is not None
            else:
                good = adf.parse_cell(r[xi])[0] == "numeric"
            key = tuple(r[k].strip() for k in kis)
            if not good or "" in key:
                continue
            sts = state.setdefault(key, [None] * len(yis))
            for j, yi in enumerate(yis):
                finite = adf.parse_cell(r[yi])[0] == "numeric"
                cur = sts[j]
                if finite:
                    if isinstance(cur, list) and not cur[0]:
                        lost += 1  # 这一段断开里没有任何一行被留下
                    sts[j] = True
                elif cur is True:
                    sts[j] = [n in keep]
                elif isinstance(cur, list) and n in keep:
                    cur[0] = True
    return lost


def large_reduce(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> tuple[list, dict]:
    """大表对这份配方的缩减结果 `(行, 信息)`（同一文件内容 + 同一参数的结果缓存，出备选的几个候选共用一遍）：父进程这一遍与脚本里的 `read_reduced`
    是同一份源码。**对账**：缩减那一遍读到的行数与每个 y 列的（有限数个数, 最小, 最大）必须等于概览扫描的整表统计（两遍是独立的累计）——
    对不上说明文件在两遍之间变了或缩减丢了行，抛 `TemplateError("template_recipe_mismatch")`。再判数据相关的资格（见 `LargeRuleError`）。"""
    info_s = values.large
    if info_s is None or not info_s.path:
        raise TemplateError("template_recipe_mismatch", "large")
    spec = large_spec(values, recipe)
    key = (values.sha256, json.dumps(spec, sort_keys=True))
    with _REDUCE_LOCK:
        hit = _REDUCE_CACHE.get(key)
        if hit is not None:
            _REDUCE_CACHE[key] = _REDUCE_CACHE.pop(key)
    if hit is None:
        total = values.size
        _header_cells, rows, info = reduce_file(
            info_s.path,
            values.delimiter or ",",
            values.header_row,
            values.units_row,
            adf.large_codec(values.encoding),
            spec,
            (lambda pos: adf.progress.tick("reduce", pos, total))
            if adf.progress.active()
            else None,
        )
        hit = (rows, info)
        with _REDUCE_LOCK:
            _REDUCE_CACHE[key] = hit
            while len(_REDUCE_CACHE) > _REDUCE_CACHE_MAX:
                _REDUCE_CACHE.pop(next(iter(_REDUCE_CACHE)))
    rows, info = hit
    if info["rows"] != info_s.rows:
        raise TemplateError("template_recipe_mismatch", "large_rows")
    stats = [(y, got) for y, got in zip(recipe.y, info["y"], strict=True)]
    if spec["kind"] == "relationship":
        stats.append((recipe.x, info["x"]))
    for col, (n, lo, hi) in stats:
        st = info_s.stats[col]
        if (n, lo, hi) != (st.numeric, st.lo, st.hi):
            raise TemplateError("template_recipe_mismatch", "large_stats")
    if spec["kind"] == "relationship":
        return rows, info
    if info["series"] == 0 or recipe_mod.trend_too_few_x(info["x_counts"]):
        raise LargeRuleError("trend_too_few_x")
    if spec["lines"]:
        if not (info["mono"] and not info["dup"]):
            raise LargeRuleError("line_duplicate_x_unconfirmed")
        if info["gap_over"]:
            raise TemplateError("template_option_unsupported", "large_gaps")
        if info["gap_runs"] and gap_audit(values, spec, info["ords"]):
            raise TemplateError("template_recipe_mismatch", "large_gaps")
    return rows, info


def large_reduce_pending(values: adf.DataSummary, recipes) -> int:
    """这批配方的缩减还要读几遍文件（同一参数的共用一遍，缓存里已有的不读）：给进度摊遍数用。"""
    todo = set()
    with _REDUCE_LOCK:
        for r in recipes:
            try:
                key = (values.sha256, json.dumps(large_spec(values, r), sort_keys=True))
            except (KeyError, ValueError):
                continue
            if key not in _REDUCE_CACHE:
                todo.add(key)
    return len(todo)


def large_unplotted(
    values: adf.DataSummary, recipe: recipe_mod.FigureRecipe
) -> tuple[int, list[int]]:
    """大表上画不出的行：`(总数, 前 100 个文件行号)`。横轴不是值（空 / NaN / 不是数或日期）的行——总数取自概览扫描（精确），行号取自缩减那一遍。"""
    s = values.per_column[recipe.x]
    total = s.empty + s.nan + (s.text_count if s.kind != "date" else 0)
    if total == 0:
        return 0, []
    _rows, info = large_reduce(values, recipe)
    return total, list(info["examples"])


def expected_fingerprints(values: adf.DataSummary, recipe: recipe_mod.FigureRecipe) -> list[dict]:
    """`[{label, part, digest}]`：按配方「这份脚本画出来应当是什么数据」，只含能逐字节算准的层（见模块文档与
    `fingerprint_coverage`）。`values` 是带行数据的摘要（对配方指的那张表 `inspect_data_source`）；与脚本调用同一批函数。"""
    if recipe.family not in RENDERERS:
        raise TemplateError("template_family_unsupported", recipe.family)
    if values.large is not None:
        # 大表：期望值按「缩减后的那张表」算——缩减用的就是脚本里的 `reduce_file`（同一份源码），之后与小表同一批函数
        rows, _info = large_reduce(values, recipe)
        values = replace(values, rows=rows, row_numbers=[], row_count=None, large=None)
    values = recipe_mod.with_cells_applied(
        values, recipe
    )  # 与脚本读表后套的是同一份规则、同一份源码
    if recipe.family in ("comparison", "distribution"):
        marks = {layer.mark for layer in recipe.layers}
        if marks & VALUE_ON_X:
            return _expected_distribution_x(values, recipe)
        return _expected_comparison(values, recipe)
    if recipe.family == "relationship":
        return _expected_relationship(values, recipe)
    return _expected_trend(values, recipe)


def _expected_comparison(values, recipe) -> list[dict]:
    d = comparison_data(
        values.columns, values.rows, recipe.x, recipe.y[0], recipe.group, recipe.facet, recipe.order
    )
    ng = len(d["groups"])
    tags = _layer_tags(recipe)
    out = []
    for p in d["panels"]:
        for gi, g in enumerate(d["groups"]):
            cells = comparison_cells(d, p, g)
            for layer, tag in zip(recipe.layers, tags):
                m, s = layer.mark, layer.summary
                lab = make_label(tag, g, p)
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


def _expected_relationship(values, recipe) -> list[dict]:
    d = relationship_data(
        values.columns, values.rows, recipe.x, recipe.y[0], recipe.group, recipe.facet
    )
    logx, logy = recipe.scale in ("log_x", "log_xy"), recipe.scale in ("log_y", "log_xy")
    tags = _layer_tags(recipe)
    out = []
    for p in d["panels"]:
        for g in d["groups"]:
            xs, ys = relationship_pairs(d, p, g)
            for layer, tag in zip(recipe.layers, tags):
                lab = make_label(tag, g, p)
                if layer.mark == "points":
                    if ys:
                        out.append(_entry(lab, "offsets", _interleave(xs, ys), (len(ys), 2)))
                else:
                    seg = fit_segment((xs, ys), logx, logy)
                    if seg:
                        out.append(_entry(lab, "xy", _interleave(*seg), (len(seg[0]), 2)))
    return out


def _expected_distribution_x(values, recipe) -> list[dict]:
    d = comparison_data(
        values.columns, values.rows, None, recipe.y[0], recipe.group, recipe.facet, "as_given"
    )
    marks = {layer.mark for layer in recipe.layers}
    edges = hist_edges(all_values(d)) if "hist" in marks and all_values(d) else None
    tags = _layer_tags(recipe)
    out = []
    for p in d["panels"]:
        for g in d["groups"]:
            vals = pooled_values(d, p, g)
            if not vals:
                continue
            for layer, tag in zip(recipe.layers, tags):
                m = layer.mark
                lab = make_label(tag, g, p)
                if m == "hist":
                    counts = hist_counts(vals, edges)
                    heights = hist_heights(
                        counts, len(vals), edges[1] - edges[0], "density" in marks
                    )
                    out.append(
                        _entry(
                            lab,
                            "bars",
                            _interleave([0.0] * len(heights), heights),
                            (len(heights), 2),
                        )
                    )
                elif m == "density":
                    grid, dens = density_curve(vals)
                    out.append(_entry(lab, "xy", _interleave(grid, dens), (len(grid), 2)))
                else:
                    xs, ys = ecdf_xy(vals)
                    out.append(_entry(lab, "xy", _interleave(xs, ys), (len(xs), 2)))
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
    tags = _layer_tags(recipe)
    out = []
    for p in d["panels"]:
        for g in d["groups"]:
            for y in recipe.y:
                ytag = y if len(recipe.y) > 1 else ""
                pool = trend_pool(d, p, g, y)
                for layer, tag in zip(recipe.layers, tags):
                    m, s = layer.mark, layer.summary
                    lab = make_label(tag, g, p, ytag)
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
                                            make_label(tag, g, p, ytag, u),
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
                        seg = fit_segment(
                            pool,
                            recipe.scale in ("log_x", "log_xy"),
                            recipe.scale in ("log_y", "log_xy"),
                        )
                        if seg:
                            out.append(_entry(lab, "xy", _interleave(*seg), (len(seg[0]), 2)))
                    elif m == "hline_summary":
                        seg = hline_segment(pool, s)
                        if seg:
                            out.append(_entry(lab, "xy", _interleave(*seg), (len(seg[0]), 2)))
    return out


# 让 ruff 知道这些只为被内嵌 / 被测试用而导入
_ = (datetime, re)
