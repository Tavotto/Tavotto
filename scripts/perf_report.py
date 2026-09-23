#!/usr/bin/env python3
"""性能探针报告分析器（ADR 0075）。

用户在「设置 → 诊断 → 性能分析」里录下的 ``tavotto-perf-*.json`` 只有原始数字；
「卡在哪、怎么改」在这里判——判据随代码演进，不必为改一条规则发一个新版本。

    python scripts/perf_report.py report.json                     # 终端摘要
    python scripts/perf_report.py a.json b.json --html out.html   # 多台机器对照 + 图
    python scripts/perf_report.py a.json --json findings.json     # 机器可读的结论

只用标准库：拿到报告的人不一定有开发环境。

## 判据的主语

一帧 = 相邻两次 rAF 回调之间的间隔。间隔里的主线程时间拆成：

- **输入处理**：pointermove 的业务处理（`input.handler`）+ React 紧随其后的同步
  重渲染（`input.react_flush`）；
- **渲染**：rAF 回调开始 → 渲染后第一个任务，含 rAF 脚本（预览写 DOM）与浏览器的
  样式 / 布局 / 绘制；
- **未归因**：剩下的（GC、别的任务、没插桩的代码、合成器 / GPU 等待，或者空闲）。

「超时」= 间隔超过刷新周期的 1.5 倍。刷新周期取录制开始、还没拖动时空转帧的中位数。

## 评价维度

一份报告先给**体检表**（每一维 A–D + 一句理由），再给**按严重程度排好的卡点**
（严重 / 问题 / 提示），每条都带证据、代码位置与改法。维度：

1. 流畅度：超时比例、>50ms 的帧、最长连续卡顿、帧间隔抖动；
2. 响应：输入 → 画面延迟、起拖那一下；
3. 余量：主线程每帧实际忙多久 → 在慢几倍的机器上会开始掉帧（快机器的报告也有用）；
4. 归因：超时帧里时间花在哪 + 帧间隔与哪一项同涨同落（相关系数）；
5. 稳定性：每个 pointermove 的成本随拖动时长的斜率（越拖越慢）；
6. 渲染放大：拖动中每个 pointermove 引起几次组件渲染 / store 通知；
7. 松手：松手后 600ms 的长帧、图内元素 commit → 权威 SVG；
8. 场景：SVG / DOM 规模，跨片段看复杂度与渲染耗时的相关；
9. 输入频率：自动测试 m1（每帧 1 个 move）与 m3（每帧 3 个）对照；
10. 机器：空转帧、低电量、电池、Rosetta、降频、高分辨率大窗口；
11. 覆盖：报告本身够不够下结论（有没有自动测试、图是不是太简单）。

WebKit 把 ``performance.now()`` 粗化到 1ms：单个 span 只有 0/1，**平均**仍然无偏，
所以这里只用总和 / 次数与逐帧量级，不拿单个小值下结论。
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "tavotto-perf-probe/1"

# 帧行的列（web/src/perf/core.ts 的 FrameRow）
DT, RENDER, HANDLER, FLUSH, RAF, MOVES, LATENCY = range(7)

LATE_FACTOR = 1.5
#: 少于这么多帧 / pointermove 的片段是一次点击，不是拖动
MIN_FRAMES = 10
MIN_MOVES = 5
#: WebKit 的计时分辨率（ms）：小于它的 p95 按它算，余量只说「至少」
TIMER_RES = 1.0

KIND_LABEL = {
    "move": "画布对象移动",
    "resize": "画布对象缩放",
    "element": "图内元素拖动",
    "endpoint": "端点拖动",
    "crop": "裁剪",
    "marquee": "框选",
    "pan": "平移画布",
    "guide": "参考线",
    "draw": "绘制",
}

#: 插桩点 → 代码位置。结论要指得出该改哪一行
CODE = {
    "input.handler": "web/src/canvas/interactions.ts（trackPointer → onMove）",
    "doc.txn_update": "web/src/store/documentStore.ts txnUpdate + web/src/lib/history.ts accumulate",
    "snap.compute": "web/src/canvas/interactions.ts startMoveDrag 的 snapMove",
    "input.react_flush": "订阅了拖动中会变的 store 的组件（见组件渲染次数）",
    "raf.preview_write": "web/src/store/svgPreviewStore.ts flushPreviewFrame",
    "autosave.flush": "web/src/store/documentStore.ts flushAutosave（最后一次编辑后 DEBOUNCE_MS = 1000 触发）",
}

#: 组件 → 文件。「拖动中该不该每帧重渲染」：跟手的读数 / 选框是合理的，属性页和左栏不是
COMPONENTS = {
    "CanvasStage": (
        "web/src/canvas/CanvasStage.tsx",
        "画布根组件，拖动中不该跟着 pointermove 重渲染",
    ),
    "PanelView": ("web/src/canvas/PanelView.tsx", "面板本体，图内拖动走预览平面、不该重渲染"),
    "ElementHitLayer": ("web/src/canvas/PanelView.tsx", "命中层，拖动中不该重渲染"),
    "ObjectView": ("web/src/canvas/ObjectView.tsx", "画布对象，移动对象时每帧一次是预期"),
    "OverlaySvg": ("web/src/canvas/OverlaySvg.tsx", "选框 / 吸附线跟手，每帧一次是预期"),
    "Rulers": ("web/src/canvas/Rulers.tsx", "标尺：只该让光标标记跟手，不该整把尺重算刻度"),
    "CanvasHud": ("web/src/components/StatusBar.tsx", "坐标读数跟手，每帧一次是预期"),
    "PageSheet": ("web/src/canvas/PageSheet.tsx", "页面底板，拖动中不该重渲染"),
    "Inspector": ("web/src/components/inspector/Inspector.tsx", "属性页，拖动中不该重渲染"),
    "LeftPanel": ("web/src/components/left/LeftPanel.tsx", "左栏，拖动中不该重渲染"),
}
#: 拖动中每帧跟着变是合理的组件（只要不是一个 pointermove 好几次）
EXPECTED_FOLLOWERS = {"OverlaySvg", "CanvasHud", "ObjectView", "Rulers"}

LEVELS = ("严重", "问题", "提示")
LEVEL_WEIGHT = {"严重": 100.0, "问题": 10.0, "提示": 1.0}


# ---------------------------------------------------------------- 小工具


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


def mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def corr(a: list[float], b: list[float]) -> float | None:
    """皮尔逊相关；样本太少或一侧恒定时说不出来，回 None（不是 0）。"""
    if len(a) < 8 or len(a) != len(b):
        return None
    try:
        return statistics.correlation(a, b)
    except statistics.StatisticsError:
        return None


def slope(ys: list[float]) -> float | None:
    """ys 对下标的最小二乘斜率。"""
    n = len(ys)
    if n < 10:
        return None
    try:
        return statistics.linear_regression(list(range(n)), ys).slope
    except statistics.StatisticsError:
        return None


def fmt(v: float | None, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}"


def longest_run(flags: list[bool]) -> tuple[int, int]:
    """最长连续 True 的长度与 True 段数。"""
    best = cur = runs = 0
    prev = False
    for f in flags:
        if f:
            cur += 1
            if not prev:
                runs += 1
        else:
            cur = 0
        best = max(best, cur)
        prev = f
    return best, runs


@dataclass
class Finding:
    level: str
    dimension: str
    title: str
    evidence: list[str]
    where: list[str]
    fix: list[str]
    segment: str = ""
    weight: float = 1.0

    @property
    def severity(self) -> float:
        return LEVEL_WEIGHT[self.level] * self.weight

    def to_json(self) -> dict:
        return {
            "level": self.level,
            "dimension": self.dimension,
            "severity": round(self.severity, 2),
            "segment": self.segment,
            "title": self.title,
            "evidence": self.evidence,
            "where": self.where,
            "fix": self.fix,
        }


def level_by(value: float, minor: float, major: float, critical: float) -> str | None:
    if value >= critical:
        return "严重"
    if value >= major:
        return "问题"
    if value >= minor:
        return "提示"
    return None


# ---------------------------------------------------------------- 每段统计


@dataclass
class Parts:
    handler: float
    flush: float
    raf: float
    render: float  # 含 raf
    other: float

    @property
    def input(self) -> float:
        return self.handler + self.flush

    @property
    def busy(self) -> float:
        return self.input + self.render


def parts_of(r: list) -> Parts:
    ren = r[RENDER] or 0.0
    other = max(0.0, r[DT] - r[HANDLER] - r[FLUSH] - ren)
    return Parts(r[HANDLER], r[FLUSH], r[RAF], ren, other)


@dataclass
class SegStats:
    index: int
    name: str
    kind: str
    source: str
    label: str | None
    refresh: float
    rows: list = field(repr=False)
    moves: int = 0
    frames: int = 0
    fps: float = 0.0
    late_pct: float = 0.0
    severe: int = 0
    dt_p50: float = 0.0
    dt_p95: float = 0.0
    dt_p99: float = 0.0
    dt_max: float = 0.0
    jitter: float = 0.0
    streak: int = 0
    bursts: int = 0
    latency_p50: float | None = None
    latency_p95: float | None = None
    moves_per_frame: float = 0.0
    start_max: float | None = None
    late_mean: Parts | None = None
    late_dt: float = 0.0
    all_mean: Parts | None = None
    busy_p95: float = 0.0
    per_move_input: float = 0.0
    trend_ratio: float | None = None
    #: 前后三分之一每帧输入成本的绝对增量（ms）：比值在小数值上没意义（WebKit 1ms 分辨率）
    trend_growth_ms: float = 0.0
    trend_slope_per_s: float | None = None
    r_dt: dict = field(default_factory=dict)
    tail_max: float | None = None
    tail_late: int = 0
    tail_worst: Parts | None = None
    renders_per_move: dict = field(default_factory=dict)
    stores_per_move: dict = field(default_factory=dict)
    tail_renders: dict = field(default_factory=dict)
    spans: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    worst: list = field(default_factory=list)
    #: 旧版报告没把松手后的计数分开：拖动中的「每个 move 几次渲染」算不准，不判
    counts_mixed: bool = False

    @property
    def budget(self) -> float:
        return self.refresh

    @property
    def late_limit(self) -> float:
        return self.refresh * LATE_FACTOR


def refresh_of(report: dict) -> float:
    idle = [x for x in report.get("idle_frame_ms") or [] if x > 0]
    if len(idle) >= 10:
        return min(max(statistics.median(idle), 6.0), 34.0)
    return 1000 / 60


def seg_stats(seg: dict, idx: int, refresh: float) -> SegStats | None:
    rows_all = seg.get("frames") or []
    if len(rows_all) < MIN_FRAMES or (seg.get("moves") or 0) < MIN_MOVES:
        return None
    ctx = seg.get("context") or {}
    # 第一帧里有片段开始时采上下文的开销（遍历 DOM）：统计里去掉它，起拖顿挫单独看
    rows = rows_all[1:]
    kind = seg.get("kind", "?")
    src = seg.get("source", "user")
    label = seg.get("label")
    name = f"#{idx + 1} {KIND_LABEL.get(kind, kind)}" + (
        f"（自动测试 {label}）" if src == "synthetic" else ""
    )
    s = SegStats(idx, name, kind, src, label, refresh, rows)
    dts = [r[DT] for r in rows]
    late_flags = [d > s.late_limit for d in dts]
    late_rows = [r for r, f in zip(rows, late_flags, strict=True) if f]
    s.frames = len(rows)
    s.moves = sum(r[MOVES] for r in rows) or 1
    s.fps = 1000 / statistics.fmean(dts)
    s.late_pct = 100 * len(late_rows) / len(rows)
    s.severe = sum(1 for d in dts if d > 50)
    s.dt_p50 = pct(dts, 0.5) or 0
    s.dt_p95 = pct(dts, 0.95) or 0
    s.dt_p99 = pct(dts, 0.99) or 0
    s.dt_max = max(dts)
    s.jitter = statistics.pstdev(dts)
    s.streak, s.bursts = longest_run(late_flags)
    lat = [r[LATENCY] for r in rows if r[LATENCY] is not None and r[MOVES] > 0]
    s.latency_p50, s.latency_p95 = pct(lat, 0.5), pct(lat, 0.95)
    s.moves_per_frame = s.moves / len(rows)

    # 起拖：第一帧扣掉探针自己的开销，连同接下来两帧
    head = rows_all[:3]
    if head:
        first = head[0][DT] - float(ctx.get("probe_overhead_ms") or 0)
        s.start_max = max([first] + [r[DT] for r in head[1:]])

    all_parts = [parts_of(r) for r in rows]
    s.all_mean = avg_parts(all_parts)
    if late_rows:
        s.late_mean = avg_parts([parts_of(r) for r in late_rows])
        s.late_dt = statistics.fmean(r[DT] for r in late_rows)
    busy = [p.busy for p, r in zip(all_parts, rows, strict=True) if r[MOVES] > 0]
    s.busy_p95 = pct(busy, 0.95) or 0.0
    s.per_move_input = sum(p.input for p in all_parts) / s.moves

    # 帧间隔与哪一项同涨同落
    for key, series in (
        ("输入处理", [p.input for p in all_parts]),
        ("渲染", [p.render for p in all_parts]),
        ("pointermove 数", [float(r[MOVES]) for r in rows]),
    ):
        c = corr(dts, series)
        if c is not None:
            s.r_dt[key] = c

    # 越拖越慢：每帧「每个 move 的输入成本」对时间的斜率，外加前后三分之一之比
    per_frame = [p.input / r[MOVES] for p, r in zip(all_parts, rows, strict=True) if r[MOVES] > 0]
    third = len(per_frame) // 3
    if third >= 10:
        a = statistics.fmean(per_frame[:third])
        b = statistics.fmean(per_frame[-third:])
        if a > 0.05:
            s.trend_ratio = b / a
        s.trend_growth_ms = (b - a) * s.moves_per_frame
        sl = slope(per_frame)
        if sl is not None:
            frames_per_s = 1000 / max(statistics.fmean(dts), 1e-6)
            s.trend_slope_per_s = sl * frames_per_s

    tail = seg.get("tail") or []
    if tail:
        s.tail_max = max(r[DT] for r in tail)
        s.tail_late = sum(1 for r in tail if r[DT] > s.late_limit)
        s.tail_worst = parts_of(max(tail, key=lambda r: r[DT]))
    counts = seg.get("counts") or {}
    s.counts_mixed = "tailCounts" not in seg
    seg_moves = seg.get("moves") or s.moves
    for k, v in counts.items():
        if k.startswith("render."):
            s.renders_per_move[k[7:]] = v / seg_moves
        elif k.startswith("store."):
            s.stores_per_move[k[6:]] = v / seg_moves
    s.tail_renders = {
        k[7:]: v for k, v in (seg.get("tailCounts") or {}).items() if k.startswith("render.")
    }
    s.spans = seg.get("spans") or {}
    s.context = ctx
    # 最慢的五帧：什么时候、各段多少
    order = sorted(range(len(rows)), key=lambda i: -rows[i][DT])[:5]
    t = 0.0
    starts = []
    for r in rows:
        starts.append(t)
        t += r[DT]
    s.worst = [(starts[i], rows[i]) for i in sorted(order) if rows[i][DT] > s.late_limit]
    return s


def avg_parts(ps: list[Parts]) -> Parts:
    return Parts(
        statistics.fmean(p.handler for p in ps),
        statistics.fmean(p.flush for p in ps),
        statistics.fmean(p.raf for p in ps),
        statistics.fmean(p.render for p in ps),
        statistics.fmean(p.other for p in ps),
    )


def span_mean(s: SegStats, name: str) -> float | None:
    st = s.spans.get(name)
    if not st or not st.get("count"):
        return None
    return st["total"] / st["count"]


# ---------------------------------------------------------------- 每段规则


def analyze_segment(s: SegStats) -> list[Finding]:
    out: list[Finding] = []
    B = s.budget
    base = [
        f"{s.frames} 帧里 {s.late_pct:.0f}% 超时（刷新周期 {B:.1f}ms），平均 {s.fps:.0f} 帧/秒，"
        f"P95 {s.dt_p95:.0f}ms，最长一帧 {s.dt_max:.0f}ms",
    ]
    if s.late_mean:
        lm = s.late_mean
        base.append(
            f"超时帧平均 {s.late_dt:.1f}ms = 输入处理 {lm.input:.1f}（业务 {lm.handler:.1f} + React {lm.flush:.1f}）"
            f" + 渲染 {lm.render:.1f} + 未归因 {lm.other:.1f}"
        )
    if s.r_dt:
        strong = sorted(s.r_dt.items(), key=lambda kv: -kv[1])
        base.append("帧间隔与各项的相关：" + "，".join(f"{k} r={v:+.2f}" for k, v in strong))

    # ---- 1. 流畅度（总览一条，归因在后面几条里）
    lvl = level_by(s.late_pct, 5, 10, 25)
    if s.streak >= 4 and lvl in (None, "提示"):
        lvl = "问题"
    if s.severe >= 3 and lvl in (None, "提示"):
        lvl = "问题"
    elif s.severe >= 1 and lvl is None:
        lvl = "提示"
    if lvl:
        ev = list(base)
        ev.append(
            f"最长连续超时 {s.streak} 帧（约 {s.streak * B:.0f}ms 画面不动），共 {s.bursts} 次卡顿；"
            f">50ms 的帧 {s.severe} 个；帧间隔标准差 {s.jitter:.1f}ms"
        )
        for t0, r in s.worst[:3]:
            p = parts_of(r)
            ev.append(
                f"第 {t0 / 1000:.2f}s 那帧 {r[DT]:.0f}ms：输入 {p.input:.1f} / 渲染 {p.render:.1f}"
                f" / 未归因 {p.other:.1f} / {r[MOVES]} 个 move"
            )
        out.append(
            Finding(
                lvl,
                "流畅度",
                "拖动掉帧",
                ev,
                ["（见下面按成因拆开的几条）"],
                ["先看同一片段下面「输入处理 / 渲染 / 未归因」哪一条被点名，改那一处。"],
                s.name,
                s.late_pct / 10,
            )
        )

    lm = s.late_mean
    shares = None
    if lm and s.late_pct >= 3:
        total = (lm.input + lm.render + lm.other) or 1
        shares = {"input": lm.input / total, "render": lm.render / total, "other": lm.other / total}

    # ---- 2. 输入处理
    input_budget = s.per_move_input * max(s.moves_per_frame, 1)
    heavy_input = shares and shares["input"] >= 0.35
    tight_input = input_budget >= 0.35 * B
    if heavy_input or tight_input:
        ev = [*base] if heavy_input else []
        ev.append(
            f"每个 pointermove 的处理成本 {s.per_move_input:.2f}ms × 每帧 {s.moves_per_frame:.1f} 个"
            f" ≈ 每帧 {input_budget:.1f}ms（占刷新周期 {100 * input_budget / B:.0f}%）"
        )
        where = [CODE["input.handler"]]
        fix: list[str] = []
        txn = span_mean(s, "doc.txn_update")
        handler = span_mean(s, "input.handler")
        flush = span_mean(s, "input.react_flush")
        if txn is not None:
            ev.append(
                f"doc.txn_update 平均 {txn:.2f}ms / 次（{s.spans['doc.txn_update']['count']} 次）"
            )
            where.append(CODE["doc.txn_update"])
        snap = span_mean(s, "snap.compute")
        if snap is not None and snap >= 0.3:
            ev.append(f"吸附计算平均 {snap:.2f}ms / 次")
            where.append(CODE["snap.compute"])
        if handler is not None and flush is not None:
            ev.append(
                f"业务处理 {handler:.2f}ms + React 同步重渲染 {flush:.2f}ms（每次 pointermove）"
            )
        if s.moves_per_frame > 1.2:
            fix.append(
                "把 pointermove 合并到每帧一次：trackPointer 只记下最新坐标，在 rAF 里调一次 onMove"
                "（预览平面已经这么做了，画布对象的 txnUpdate 还没有）。"
            )
        if flush is not None and handler is not None and flush > handler:
            fix.append(
                "React 重渲染比业务代码还贵：收窄拖动中会变的订阅（见「渲染放大」一条），"
                "selector 只取与本组件有关的切片。"
            )
        if txn is not None and handler and txn / handler > 0.4:
            fix.append(
                "拖动中不要每个 pointermove 都 produceWithPatches 整份文档：先只改预览"
                "（CSS transform / 临时位移），松手时一次 commit。"
            )
        if not fix:
            fix.append(
                "用 Safari Web Inspector 的 Timeline 录一次同样的拖动，看 pointermove 里哪段 JS 最长。"
            )
        lvl = level_by(s.late_pct, 3, 10, 25) if heavy_input else "提示"
        title = (
            "拖动时输入处理太重"
            if heavy_input
            else "输入处理吃掉了不少帧预算（慢机器上会先卡在这）"
        )
        out.append(
            Finding(lvl or "提示", "归因", title, ev, where, fix, s.name, 1 + s.late_pct / 10)
        )

    # ---- 3. 渲染
    render_p95 = pct([parts_of(r).render for r in s.rows], 0.95) or 0
    heavy_render = shares and shares["render"] >= 0.35
    tight_render = render_p95 >= 0.4 * B
    if heavy_render or tight_render:
        ctx = s.context
        big = ctx.get("largest_svg_nodes")
        ev = [*base] if heavy_render else []
        ev.append(
            f"每帧主线程渲染 P95 {render_p95:.1f}ms（占刷新周期 {100 * render_p95 / B:.0f}%）"
        )
        if big is not None:
            ev.append(
                f"页面上 SVG 节点 {ctx.get('svg_nodes')} 个，最大一张 {big} 个；"
                f"DOM 节点 {ctx.get('dom_nodes')} 个；预览表示法 {ctx.get('preview_modes')}"
            )
        pw = span_mean(s, "raf.preview_write")
        if pw is not None:
            ev.append(f"rAF 里写预览 DOM 平均 {pw:.2f}ms（其余是浏览器样式 / 布局 / 绘制）")
        fix = [
            "拖动中把被拖的东西放进独立合成层：外层包装元素上用 CSS transform + will-change，"
            "而不是改 SVG 里 <g> 的 transform 属性（后者让整张 SVG 重新布局 / 重绘）。",
        ]
        if big and big > 5000:
            fix.append(
                f"最大的 SVG 有 {big} 个节点：拖动期间换成位图预览（lib/previewBudget.ts 的分档），"
                "或把与被拖元素无关的部分栅格化。"
            )
        lvl = level_by(s.late_pct, 3, 10, 25) if heavy_render else "提示"
        title = (
            "拖动时浏览器渲染（样式 / 布局 / 绘制）太重"
            if heavy_render
            else "浏览器渲染吃掉了不少帧预算（慢机器上会先卡在这）"
        )
        out.append(
            Finding(
                lvl or "提示",
                "归因",
                title,
                ev,
                ["web/src/store/svgPreviewStore.ts writeTransform", "web/src/canvas/PanelView.tsx"],
                fix,
                s.name,
                1 + s.late_pct / 10,
            )
        )

    # ---- 4. 未归因
    if shares and shares["other"] >= 0.5 and s.late_pct >= 5:
        ev = list(base)
        busy = sorted(s.stores_per_move.items(), key=lambda kv: -kv[1])[:4]
        if busy:
            ev.append(
                "拖动中每个 pointermove 的 store 通知："
                + "，".join(f"{k} {v:.1f}" for k, v in busy)
            )
        idle_like = s.r_dt.get("pointermove 数")
        fix = [
            "插桩之外的时间：用 Safari → 开发 → Tavotto → 时间线 录一次同样的拖动，"
            "看超时帧里是 JavaScript、垃圾回收还是合成（Composite）。",
        ]
        if s.stores_per_move.get("render", 0) > 0.1:
            fix.append("拖动中 renderStore 在变：检查是不是有面板在拖动途中被重新渲染或预取。")
        if idle_like is not None and idle_like < -0.3:
            ev.append(
                "move 越少的帧越长：更像是输入事件本身来得稀疏（系统繁忙 / 输入被节流），不是渲染慢"
            )
        out.append(
            Finding(
                level_by(s.late_pct, 5, 10, 25) or "提示",
                "归因",
                "超时帧里大部分时间不在已插桩的代码里",
                ev,
                ["（未归因）"],
                fix,
                s.name,
                s.late_pct / 10,
            )
        )

    # ---- 5. 越拖越慢
    # 比值与绝对增量都要过：小数值上的比值是量化噪声，不是趋势
    grow = (s.trend_ratio or 0) >= 1.6 and s.trend_growth_ms >= 0.1 * B
    slope_big = (s.trend_slope_per_s or 0) * (s.frames * s.dt_p50 / 1000) >= 0.3 * B
    if grow or slope_big:
        patches = s.context.get("txn_patches_at_end")
        ev = []
        if s.trend_ratio:
            ev.append(
                f"后三分之一每个 pointermove 的处理成本是前三分之一的 {s.trend_ratio:.1f} 倍"
                f"（每帧多出 {s.trend_growth_ms:.1f}ms）"
            )
        if s.trend_slope_per_s is not None:
            ev.append(f"每个 move 的成本每秒增加 {s.trend_slope_per_s:.2f}ms")
        if patches:
            ev.append(f"松手时事务里攒了 {patches} 条补丁")
        out.append(
            Finding(
                "问题" if grow and slope_big else "提示",
                "稳定性",
                "越拖越慢：每次移动的成本随拖动时长增长",
                ev,
                [CODE["doc.txn_update"]],
                [
                    "history.accumulate 每次都 `[...txn.patches, ...patches]` 整份复制，"
                    "一次拖动是 O(n²)。改成原地 push，或拖动中只保留每条路径的最后一条补丁"
                    "（compress 已有这条逻辑，可以挪到累加时做）。",
                ],
                s.name,
                max(1.0, s.trend_ratio or 1.0),
            )
        )

    # ---- 6. 响应
    if s.latency_p95 is not None:
        lvl = level_by(s.latency_p95 / B, 2.5, 3.5, 6)
        if lvl:
            out.append(
                Finding(
                    lvl,
                    "响应",
                    "拖动跟手性差：鼠标动了，画面晚几帧才跟上",
                    [
                        f"输入 → 画面延迟中位 {fmt(s.latency_p50, 'ms', 0)}，P95 {s.latency_p95:.0f}ms"
                        f"（{s.latency_p95 / B:.1f} 个刷新周期；1.5–2 个是正常下限）",
                    ],
                    [CODE["input.handler"], CODE["raf.preview_write"]],
                    [
                        "延迟是掉帧的直接后果：先解决同一片段的掉帧那几条，延迟会跟着下来。"
                        if s.late_pct >= 5
                        else "帧率正常而延迟高：每次处理跨了一帧——把 DOM 写入集中到同一个 rAF，别在 move 里触发布局读。"
                    ],
                    s.name,
                    min(s.latency_p95 / B, 10) / 2,
                )
            )
    if s.start_max is not None:
        lvl = level_by(s.start_max / B, 3, 6, 15)
        if lvl:
            out.append(
                Finding(
                    lvl,
                    "响应",
                    "起拖那一下顿住",
                    [f"按下后前三帧里最长一帧 {s.start_max:.0f}ms（已扣掉探针采上下文的开销）"],
                    [
                        "web/src/canvas/interactions.ts startXDrag（beginTxn / beginElementPreview）",
                        "选区变化引起的属性页重渲染",
                    ],
                    [
                        "按下时的同步工作（开事务、选中、属性页换内容）推迟到第一帧之后，或只在真的开始移动后再做。"
                    ],
                    s.name,
                    # 单帧顿挫排在持续掉帧之后：同一档里权重封顶
                    min(s.start_max / B, 10) / 2,
                )
            )

    # ---- 7. 渲染放大（快机器上也看得出来）。旧版报告的计数混着松手后那段，判不出就不判
    if not s.counts_mixed:
        out += render_amplification(s)

    # ---- 8. 自动保存落在拖动途中（松手 1 秒后触发，常常赶上下一次拖动）
    af = s.spans.get("autosave.flush")
    if af and af.get("count"):
        mx = float(af.get("max") or 0)
        avg = af["total"] / af["count"]
        out.append(
            Finding(
                "问题" if mx >= B else "提示",
                "松手",
                "上一次松手的自动保存落在了这次拖动途中",
                [
                    f"拖动途中跑了 {af['count']} 次自动保存的同步段（整份文档 buildProject + JSON.stringify"
                    f" + 写本机副本），平均 {avg:.1f}ms，最长 {mx:.1f}ms（刷新周期 {B:.1f}ms）",
                    "文档越大（对象 / override 越多）这一段越长；快机器上不显，慢机器上就是拖到一半顿一下",
                ],
                [CODE["autosave.flush"]],
                [
                    "拖动进行中推迟自动保存（interaction.kind != none 时顺延到松手后），"
                    "或把序列化放进 requestIdleCallback / Worker，别和下一次拖动抢主线程。",
                ],
                s.name,
                max(1.0, mx / B),
            )
        )

    # ---- 9. 松手
    if s.tail_max is not None:
        lvl = level_by(s.tail_max / B, 2.8, 6, 15)
        if lvl:
            tw = s.tail_worst
            ev = [f"松手后 0.6 秒内最长一帧 {s.tail_max:.0f}ms（超时 {s.tail_late} 帧）"]
            if tw:
                ev.append(
                    f"那一帧：输入 {tw.input:.1f} / 渲染 {tw.render:.1f} / 未归因 {tw.other:.1f}"
                )
            top = sorted(s.tail_renders.items(), key=lambda kv: -kv[1])[:4]
            if top:
                ev.append("松手后组件渲染：" + "，".join(f"{k} {v} 次" for k, v in top))
            out.append(
                Finding(
                    lvl,
                    "松手",
                    "松手之后卡一下",
                    ev,
                    [
                        "web/src/store/documentStore.ts endTxn",
                        "web/src/store/svgPreviewStore.ts reattachPreview",
                    ],
                    [
                        "松手时的同步工作（endTxn 压缩补丁、自动保存、换上权威 SVG 后的整张重解析与重排）"
                        "拆到下一帧 / 空闲时做；换 SVG 时复用未变的节点而不是整棵替换。",
                    ],
                    s.name,
                    min(s.tail_max / B, 10) / 2,
                )
            )
    return out


def render_amplification(s: SegStats) -> list[Finding]:
    """拖动中每个 pointermove 引起几次渲染。合理的跟手组件每帧一次；不该动的动了就是浪费。"""
    per_frame = 1 / max(s.moves_per_frame, 1e-6)  # 每帧一次折算成每个 move 几次
    wasted, noisy = [], []
    for comp, per_move in sorted(s.renders_per_move.items(), key=lambda kv: -kv[1]):
        if per_move < 0.3:
            continue
        if comp in EXPECTED_FOLLOWERS:
            if per_move > max(per_frame, 1.0) * 1.5:
                noisy.append((comp, per_move))
        else:
            wasted.append((comp, per_move))
    out: list[Finding] = []
    budget_hint = s.all_mean.input if s.all_mean else 0.0
    if wasted:
        ev = [
            "每个 pointermove 引起的渲染：" + "，".join(f"{c} {v:.1f} 次" for c, v in wasted),
            f"这段拖动平均每帧输入处理 {budget_hint:.2f}ms（快机器上看不出，慢 3–5 倍就是它）",
        ]
        where = [
            f"{COMPONENTS[c][0]}（{c}：{COMPONENTS[c][1]}）" for c, _ in wasted if c in COMPONENTS
        ]
        out.append(
            Finding(
                "问题" if any(v >= 0.8 for _, v in wasted) else "提示",
                "渲染放大",
                "拖动中不该动的组件在跟着重渲染",
                ev,
                where or ["（组件未登记）"],
                [
                    "查这些组件订阅了哪个拖动中会变的字段（interactionStore 的 cursor / gidDrag / snap，"
                    "或 documentStore 的整份 doc），换成只取自己需要的切片；拖动中不需要跟手的部分改成松手后再读。",
                ],
                s.name,
                sum(v for _, v in wasted),
            )
        )
    if noisy:
        out.append(
            Finding(
                "提示",
                "渲染放大",
                "跟手组件一个 pointermove 渲染了不止一次",
                [
                    "，".join(f"{c} {v:.1f} 次 / move" for c, v in noisy)
                    + f"（每帧一次折合 {per_frame:.1f} 次 / move）"
                ],
                [f"{COMPONENTS[c][0]}（{c}）" for c, _ in noisy if c in COMPONENTS],
                [
                    "同一个 move 里被通知了多次（多个 store 字段分开 set）：合成一次 set，或让组件只订阅一个合并后的派生值。"
                    " Rulers 这类重组件只让光标标记跟手，刻度与底板 memo 住。",
                ],
                s.name,
                1.0,
            )
        )
    # 只认「文档本体变了」这一类：documentStore 里还住着保存状态，上一次松手 1 秒后的
    # 自动保存会在下一段拖动途中改它——那不是写文档（见「自动保存落在拖动途中」）。
    # 旧版报告根本没有 document.doc 这个计数，这里自然读成 0、不判；它们在「覆盖」里披露
    doc_per_move = s.stores_per_move.get("document.doc", 0)
    if s.kind == "element" and doc_per_move >= 0.3:
        out.append(
            Finding(
                "提示",
                "渲染放大",
                "图内元素拖动途中在写文档",
                [
                    f"documentStore 的文档本体每个 pointermove 变 {doc_per_move:.1f} 次——预览平面按设计不该碰文档"
                ],
                [
                    "web/src/canvas/interactions.ts startElementDrag",
                    "web/src/store/svgPreviewStore.ts",
                ],
                [
                    "查拖动途中是谁在 commit / txnUpdate（fake-realtime-preview 细则：预览只改 DOM）。"
                ],
                s.name,
                doc_per_move,
            )
        )
    return out


# ---------------------------------------------------------------- 整份报告


def machine_notes(report: dict, refresh: float) -> tuple[list[str], list[str], float | None]:
    sysf = report.get("system") or {}
    client = report.get("client") or {}
    idle = report.get("idle_frame_ms") or []
    notes: list[str] = []
    fixes: list[str] = []
    idle_late = None
    if len(idle) >= 20:
        idle_late = 100 * sum(1 for d in idle if d > refresh * LATE_FACTOR) / len(idle)
        if idle_late >= 5:
            notes.append(f"什么都不做时也有 {idle_late:.0f}% 的帧超时——机器此刻整体很忙")
            fixes.append("关掉其他占用 CPU 的程序（活动监视器按 CPU 排序看一眼）后重录一份对照。")
    if sysf.get("low_power_mode"):
        notes.append("低电量模式开着（CPU / GPU 降频）")
        fixes.append("关掉低电量模式后重录一份对照。")
    if sysf.get("power_source") == "battery":
        notes.append("用电池供电")
        fixes.append("插上电源后重录一份对照。")
    if sysf.get("rosetta"):
        notes.append("Tavotto 在 Rosetta 转译下运行（装错了架构的安装包）")
        fixes.append("改装 Apple Silicon 版安装包。")
    lim = sysf.get("cpu_speed_limit")
    if isinstance(lim, int) and lim < 100:
        notes.append(f"CPU 正在降频到 {lim}%（过热）")
        fixes.append("等机器凉下来、垫高散热后重录一份对照。")
    tw = sysf.get("thermal_warning_level")
    if isinstance(tw, int) and tw > 0:
        notes.append(f"系统报了热警告（级别 {tw}）")
    vp = client.get("viewport") or {}
    dpr = client.get("dpr") or 1
    if vp.get("w") and vp.get("h"):
        px = vp["w"] * vp["h"] * dpr * dpr
        if px >= 12e6:
            notes.append(
                f"窗口很大且是高分辨率（{vp['w']}×{vp['h']} × DPR {dpr} ≈ {px / 1e6:.0f}M 像素），每帧要画的像素多"
            )
            fixes.append("缩小窗口或换到 1x 的外接屏录一份对照，能区分「像素多」与「代码慢」。")
    return notes, fixes, idle_late


def analyze_report(report: dict) -> dict:
    refresh = refresh_of(report)
    stats: list[SegStats] = []
    for i, seg in enumerate(report.get("segments") or []):
        st = seg_stats(seg, i, refresh)
        if st:
            stats.append(st)
    findings: list[Finding] = []
    for st in stats:
        findings += analyze_segment(st)

    # ---- 余量：主线程每帧实际忙多久 → 慢几倍会开始掉帧
    headroom = None
    busy = [s.busy_p95 for s in stats]
    if busy:
        worst_busy = max(max(busy), TIMER_RES)
        headroom = refresh / worst_busy
        if headroom < 2:
            saturated = headroom < 1
            findings.append(
                Finding(
                    "问题" if headroom < 1.3 else "提示",
                    "余量",
                    "主线程已经满载：每帧的活比一个刷新周期还多"
                    if saturated
                    else "帧预算快用完了：稍慢一点的机器就会掉帧",
                    [
                        f"有 move 的帧里主线程忙碌（输入处理 + 渲染）P95 {worst_busy:.1f}ms，刷新周期 {refresh:.1f}ms",
                        "这台机器上已经没有余量"
                        if saturated
                        else f"估计 CPU 慢 {headroom:.1f} 倍的机器上开始掉帧",
                    ],
                    ["（看同一报告里「输入处理 / 渲染」的提示条）"],
                    ["先处理被点名为「吃掉了不少帧预算」的那一项。"],
                    weight=2 / headroom,
                )
            )

    # ---- 场景复杂度与渲染耗时的相关（跨片段）
    pairs = [
        (float(s.context.get("largest_svg_nodes") or 0), s.all_mean.render if s.all_mean else 0.0)
        for s in stats
    ]
    if len(pairs) >= 4 and len({p[0] for p in pairs}) >= 3:
        try:
            r = statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])
        except statistics.StatisticsError:
            r = None
        if r is not None and r >= 0.7:
            findings.append(
                Finding(
                    "提示",
                    "场景",
                    "图越复杂，拖动时渲染越慢",
                    [
                        f"{len(pairs)} 段拖动里「最大 SVG 节点数」与每帧渲染耗时相关 r={r:+.2f}",
                        "节点数 / 每帧渲染："
                        + "，".join(f"{int(n)}→{ms:.1f}ms" for n, ms in sorted(pairs)),
                    ],
                    [
                        "web/src/lib/previewBudget.ts（预览表示法分档）",
                        "web/src/store/svgPreviewStore.ts writeTransform",
                    ],
                    ["节点多的图在拖动期间降成位图预览，或把被拖元素放进独立合成层。"],
                    weight=r,
                )
            )

    # ---- 自动测试两轮对照
    syn = {s.label: s for s in stats if s.source == "synthetic"}
    if "m1" in syn and "m3" in syn:
        a, b = syn["m1"], syn["m3"]
        gap = b.late_pct - a.late_pct
        cost_gap = b.per_move_input * b.moves_per_frame - a.per_move_input * a.moves_per_frame
        if gap >= 15 or (gap >= 5 and cost_gap >= 0.3 * refresh):
            findings.append(
                Finding(
                    "问题" if gap >= 15 else "提示",
                    "输入频率",
                    "输入频率越高越卡：每帧多个 pointermove 没有合并",
                    [
                        f"每帧 1 个 pointermove：{a.late_pct:.0f}% 超时；每帧 3 个：{b.late_pct:.0f}% 超时",
                        f"每帧输入处理 {a.per_move_input * a.moves_per_frame:.1f}ms → "
                        f"{b.per_move_input * b.moves_per_frame:.1f}ms",
                    ],
                    [CODE["input.handler"]],
                    [
                        "120Hz 触控板 / 高回报率鼠标在 60Hz 屏上每帧会来 2–3 个 pointermove。"
                        "trackPointer 改成每帧最多处理一次（rAF 合并），这一项的差距应当消失。",
                    ],
                    "自动测试 m1 / m3",
                    max(1.0, gap / 10),
                )
            )

    # ---- 机器状态
    notes, fixes, idle_late = machine_notes(report, refresh)
    if notes:
        findings.append(
            Finding(
                "提示",
                "机器",
                "机器状态会放大卡顿（先排除再看代码）",
                notes,
                ["（运行环境）"],
                fixes,
                weight=2.0,
            )
        )

    # ---- 图内元素 commit → 权威 SVG（后端往返 + 换图）
    auth = [a.get("commit_to_authority_ms") for a in report.get("authority") or []]
    auth = [a for a in auth if isinstance(a, (int, float))]
    if auth:
        p50 = pct(auth, 0.5) or 0
        lvl = level_by(p50, 150, 500, 1500)
        if lvl:
            findings.append(
                Finding(
                    lvl,
                    "松手",
                    "松手后要等后端重画一遍才落定",
                    [
                        f"图内元素松手 → 权威 SVG 换上画布：中位 {p50:.0f}ms，P95 {pct(auth, 0.95):.0f}ms（{len(auth)} 次）"
                    ],
                    [
                        "后端 /api/engine/render（docs/perf-baseline.md 的口径）",
                        "web/src/store/svgPreviewStore.ts reattachPreview",
                    ],
                    [
                        "这段时间画面停在预览上不算卡；若用户觉得「松手后跳一下」，看后端 timings 与换图时的整张重解析。"
                    ],
                    weight=min(p50 / 150, 10) / 2,
                )
            )

    # ---- 覆盖：这份报告够不够下结论
    coverage: list[str] = []
    if not stats:
        coverage.append("没有足够长的拖动（每段至少 10 帧、5 个 pointermove）")
    if not syn:
        coverage.append("没跑自动测试：缺少「输入频率」这一维的对照")
    big = max((float(s.context.get("largest_svg_nodes") or 0) for s in stats), default=0)
    if stats and big < 1000:
        coverage.append(f"拖的图都很简单（最大 SVG 只有 {int(big)} 个节点），代表性有限")
    unsplit = [
        s
        for s in stats
        if s.kind == "element"
        and not s.context.get("doc_split")
        and s.stores_per_move.get("document", 0) > 0
    ]
    if unsplit:
        coverage.append(
            f"{len(unsplit)} 段图内拖动途中有 documentStore 通知，这版探针分不清是改了文档还是自动保存在改保存状态"
            "（新版分开记，重录即可判）"
        )
    if len(report.get("idle_frame_ms") or []) < 20:
        coverage.append("空转基线太短（开始后马上就拖了），刷新周期是估的")
    if coverage:
        findings.append(
            Finding(
                "提示",
                "覆盖",
                "这份报告能下的结论有限",
                coverage,
                ["（录制方式）"],
                ["补录：在最复杂的那张图上照常拖几次，再点「自动测试」选最卡的对象。"],
                weight=0.5,
            )
        )

    if any(s.counts_mixed for s in stats):
        findings.append(
            Finding(
                "提示",
                "覆盖",
                "旧版探针录的报告：「渲染放大」一维不判",
                [
                    "这份报告没把松手后的组件渲染 / store 通知与拖动中分开，每个 move 引起几次渲染算不准"
                ],
                ["（录制方式）"],
                ["用新版（松手后单独计数）重录一份，这一维才有结论。"],
                weight=0.4,
            )
        )
    findings = merge_findings(findings)
    findings.sort(key=lambda f: -f.severity)
    grades = grade(stats, findings, refresh, headroom, idle_late, auth)
    return {
        "refresh": refresh,
        "stats": stats,
        "findings": findings,
        "grades": grades,
        "headroom": headroom,
        "system": report.get("system") or {},
        "report": report,
    }


def merge_findings(findings: list[Finding]) -> list[Finding]:
    """同一现象在好几段拖动里各报一条，合并成一条：留最严重那段的证据，注明出现在哪几段。"""
    groups: dict[tuple[str, str], list[Finding]] = {}
    order: list[tuple[str, str]] = []
    for f in findings:
        key = (f.dimension, f.title)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(f)
    out: list[Finding] = []
    for key in order:
        fs = groups[key]
        if len(fs) == 1:
            out.append(fs[0])
            continue
        fs.sort(key=lambda f: -f.severity)
        top = fs[0]
        segs = [f.segment for f in fs if f.segment]
        where = list(dict.fromkeys(w for f in fs for w in f.where))
        out.append(
            Finding(
                top.level,
                top.dimension,
                top.title,
                [
                    f"出现在 {len(fs)} 段：{'、'.join(segs)}；下面是最严重的那段（{top.segment}）",
                    *top.evidence,
                ],
                where,
                top.fix,
                f"{len(fs)} 段",
                top.weight * (1 + 0.15 * (len(fs) - 1)),
            )
        )
    return out


# ---------------------------------------------------------------- 体检表


def _g(v: float | None, a: float, b: float, c: float, higher_is_better: bool = False) -> str:
    if v is None:
        return "—"
    if higher_is_better:
        return "A" if v >= a else "B" if v >= b else "C" if v >= c else "D"
    return "A" if v <= a else "B" if v <= b else "C" if v <= c else "D"


def grade(
    stats: list[SegStats], findings: list[Finding], refresh: float, headroom, idle_late, auth
) -> list[tuple[str, str, str]]:
    B = refresh
    rows: list[tuple[str, str, str]] = []
    if not stats:
        rows += [
            (d, "—", "没有足够长的拖动")
            for d in ("流畅度", "响应", "余量", "松手", "稳定性", "渲染放大")
        ]
    if stats:
        worst = max(stats, key=lambda s: s.late_pct)
        rows.append(
            (
                "流畅度",
                _g(worst.late_pct, 2, 5, 15),
                f"最差一段 {worst.late_pct:.0f}% 超时、最长连续 {worst.streak} 帧（{worst.name}）",
            )
        )
        lat = [s.latency_p95 for s in stats if s.latency_p95 is not None]
        if lat:
            m = max(lat)
            rows.append(
                (
                    "响应",
                    _g(m / B, 1.8, 2.5, 4),
                    f"输入 → 画面延迟 P95 最差 {m:.0f}ms（{m / B:.1f} 个刷新周期）",
                )
            )
        else:
            rows.append(("响应", "—", "没有量到输入延迟"))
        rows.append(
            (
                "余量",
                _g(headroom, 4, 2, 1.2, higher_is_better=True),
                "—"
                if headroom is None
                else "主线程已满载"
                if headroom < 1
                else f"估计慢 {headroom:.1f} 倍的机器开始掉帧",
            )
        )
        tails = [s.tail_max for s in stats if s.tail_max is not None]
        if tails:
            m = max(tails)
            rows.append(
                (
                    "松手",
                    _g(m / B, 2.8, 6, 15),
                    f"松手后最长一帧 {m:.0f}ms"
                    + (f"，commit→权威中位 {pct(auth, 0.5):.0f}ms" if auth else ""),
                )
            )
        else:
            rows.append(("松手", "—", "没有松手后的帧数据"))
        # 与「越拖越慢」同一道闸：绝对增量不到刷新周期 10% 的比值是量化噪声
        trend = max(((s.trend_ratio or 1) if s.trend_growth_ms >= 0.1 * B else 1.0) for s in stats)
        rows.append(
            (
                "稳定性",
                _g(trend, 1.3, 1.6, 2.5),
                f"每个 move 的成本后段 / 前段最多 {trend:.1f} 倍（计入绝对增量门槛）",
            )
        )
        clean = [s for s in stats if not s.counts_mixed]
        if clean:
            amp = max(
                max(
                    (v for c, v in s.renders_per_move.items() if c not in EXPECTED_FOLLOWERS),
                    default=0,
                )
                for s in clean
            )
            rows.append(
                ("渲染放大", _g(amp, 0.1, 0.3, 0.8), f"不该动的组件每个 move 最多渲染 {amp:.1f} 次")
            )
        else:
            rows.append(("渲染放大", "—", "旧版报告：拖动中与松手后的计数没分开，不判"))
    machine = [f for f in findings if f.dimension == "机器"]
    rows.append(
        (
            "机器",
            "C" if machine else "A",
            "；".join(machine[0].evidence) if machine else "插电、未降频、空转正常",
        )
    )
    cov = [e for f in findings if f.dimension == "覆盖" for e in f.evidence]
    rows.append(
        (
            "覆盖",
            "C" if cov else "A",
            "；".join(cov) if cov else "有真实拖动、有自动测试、有复杂的图",
        )
    )
    return rows


# ---------------------------------------------------------------- 输出


def machine_line(a: dict) -> str:
    s = a["system"] or {}
    client = a["report"].get("client") or {}
    parts = [
        s.get("model") or "?",
        s.get("cpu") or "",
        f"{s['memory_gb']:.0f}GB" if s.get("memory_gb") else "",
        f"macOS {s['os_version']}" if s.get("os_version") else "",
        f"Tavotto {s['tavotto_version']}" if s.get("tavotto_version") else "",
        f"DPR {client.get('dpr')}" if client.get("dpr") else "",
        f"刷新 {1000 / a['refresh']:.0f}Hz",
        "电池" if s.get("power_source") == "battery" else "",
        "低电量模式" if s.get("low_power_mode") else "",
    ]
    return " · ".join(p for p in parts if p)


def text_report(path: str, a: dict) -> str:
    lines = [f"== {path}", machine_line(a), "", "体检表"]
    for dim, g, why in a["grades"]:
        lines.append(f"  {g}  {dim:<5} {why}")
    lines.append("")
    if a["stats"]:
        lines.append(
            f"{'片段':<30}{'帧/秒':>6}{'超时%':>6}{'P95':>6}{'最长':>6}{'连续':>5}{'延迟P95':>8}{'松手':>6}  主因"
        )
        for s in a["stats"]:
            cause = "—"
            if s.late_mean and s.late_pct >= 3:
                lm = s.late_mean
                cause = max(
                    (("输入", lm.input), ("渲染", lm.render), ("未归因", lm.other)),
                    key=lambda x: x[1],
                )[0]
            lines.append(
                f"{s.name:<30}{s.fps:>6.0f}{s.late_pct:>6.0f}{s.dt_p95:>6.0f}{s.dt_max:>6.0f}{s.streak:>5}"
                f"{fmt(s.latency_p95, nd=0):>8}{fmt(s.tail_max, nd=0):>6}  {cause}"
            )
        lines.append("")
    by_level = {lv: [f for f in a["findings"] if f.level == lv] for lv in LEVELS}
    if not by_level["严重"] and not by_level["问题"]:
        lines.append(
            "没有达到「问题」级别的卡点。" + ("下面是值得留意的提示：" if by_level["提示"] else "")
        )
        lines.append("")
    n = 0
    for lv in LEVELS:
        for f in by_level[lv]:
            n += 1
            lines.append(
                f"[{n}] 【{f.level}·{f.dimension}】{f.title}"
                + (f"（{f.segment}）" if f.segment else "")
            )
            lines += [f"    · {e}" for e in f.evidence]
            lines += [f"    ↳ {w}" for w in f.where]
            lines += [f"    ✎ {x}" for x in f.fix]
            lines.append("")
    return "\n".join(lines)


def frame_chart(s: SegStats) -> str:
    """每帧一根柱：输入 / 渲染 / 未归因三段叠起来，虚线是 1.5 倍刷新周期。"""
    rows = s.rows[:600]
    if not rows:
        return ""
    w_bar = 3
    h = 140
    cap = max(50.0, s.refresh * 4)
    scale = h / cap
    bars = []
    for i, r in enumerate(rows):
        x = i * w_bar
        p = parts_of(r)
        y = h
        for v, cls in ((p.input, "i"), (p.render, "r"), (p.other, "o")):
            hh = min(v, cap) * scale
            if hh <= 0:
                continue
            y -= hh
            bars.append(
                f'<rect class="{cls}" x="{x}" y="{y:.1f}" width="{w_bar - 0.5}" height="{hh:.1f}"/>'
            )
    line_y = h - s.late_limit * scale
    width = len(rows) * w_bar
    return (
        f'<svg viewBox="0 0 {width} {h}" preserveAspectRatio="none" class="chart">'
        + "".join(bars)
        + f'<line x1="0" x2="{width}" y1="{line_y:.1f}" y2="{line_y:.1f}" class="budget"/></svg>'
    )


CSS = """
:root{--bg:#f7f7f4;--fg:#1f1f1c;--mut:#6b6b64;--card:#fff;--line:#e3e3dc;--i:#c2410c;--r:#2563eb;--o:#a3a39a;
--crit:#b91c1c;--maj:#c2410c;--min:#6b6b64;--a:#15803d;--b:#4d7c0f;--c:#b45309;--d:#b91c1c}
@media (prefers-color-scheme:dark){:root{--bg:#171715;--fg:#ecece6;--mut:#9a9a92;--card:#212120;--line:#34342f;
--i:#fb923c;--r:#60a5fa;--o:#5f5f58;--crit:#f87171;--maj:#fb923c;--min:#9a9a92;--a:#4ade80;--b:#a3e635;--c:#fbbf24;--d:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"PingFang SC",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
h3{font-size:14px;margin:18px 0 6px;color:var(--mut)}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px;white-space:nowrap}td.g{white-space:nowrap}.bad{color:var(--i);font-weight:600}
.chart{width:100%;height:140px;display:block;background:var(--bg);border-radius:6px}
.chart .i{fill:var(--i)}.chart .r{fill:var(--r)}.chart .o{fill:var(--o)}.chart .budget{stroke:var(--fg);stroke-dasharray:4 3;stroke-width:1;vector-effect:non-scaling-stroke}
.legend span{display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:12px;color:var(--mut)}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
ul{margin:6px 0 0;padding-left:20px}li{margin:2px 0}code{font-size:12px}
.wrap{overflow-x:auto}
.lv{display:inline-block;font-size:12px;font-weight:600;border-radius:4px;padding:0 6px;margin-right:6px;color:#fff}
.lv-严重{background:var(--crit)}.lv-问题{background:var(--maj)}.lv-提示{background:var(--min)}
.g{font-weight:700;width:2em}.g-A{color:var(--a)}.g-B{color:var(--b)}.g-C{color:var(--c)}.g-D{color:var(--d)}
"""


def html_report(analyses: list[tuple[str, dict]]) -> str:
    e = html.escape
    out = [
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>Tavotto 性能报告</title><style>{CSS}</style><main>",
        "<h1>Tavotto 拖动性能报告</h1>",
        f"<p class='mut'>{len(analyses)} 份报告 · 由 scripts/perf_report.py 生成</p>",
    ]
    if len(analyses) > 1:
        dims = [d for d, _, _ in analyses[0][1]["grades"]]
        out.append("<h2>机器对照</h2><div class='card wrap'><table><tr><th>报告</th><th>机器</th>")
        out += [f"<th>{e(d)}</th>" for d in dims]
        out.append("<th>首要卡点</th></tr>")
        for path, a in analyses:
            g = {d: v for d, v, _ in a["grades"]}
            top = next((f for f in a["findings"] if f.level != "提示"), None)
            out.append(f"<tr><td>{e(Path(path).name)}</td><td>{e(machine_line(a))}</td>")
            out += [f"<td class='g g-{g.get(d, '—')}'>{e(g.get(d, '—'))}</td>" for d in dims]
            out.append(f"<td>{e(top.title) if top else '—'}</td></tr>")
        out.append("</table></div>")
    for path, a in analyses:
        out.append(f"<h2>{e(Path(path).name)}</h2><p class='mut'>{e(machine_line(a))}</p>")
        out.append("<div class='card'><table><tr><th>评分</th><th>维度</th><th>依据</th></tr>")
        for dim, g, why in a["grades"]:
            out.append(
                f"<tr><td class='g g-{g}'>{e(g)}</td><td>{e(dim)}</td><td>{e(why)}</td></tr>"
            )
        out.append("</table></div>")
        if not a["findings"]:
            out.append("<div class='card'>没有发现任何值得留意的地方。</div>")
        for lv in LEVELS:
            fs = [f for f in a["findings"] if f.level == lv]
            if not fs:
                continue
            out.append(f"<h3>{e(lv)}（{len(fs)}）</h3>")
            for f in fs:
                seg = f"<span class='mut'>（{e(f.segment)}）</span>" if f.segment else ""
                out.append(
                    f"<div class='card'><span class='lv lv-{e(f.level)}'>{e(f.level)}</span>"
                    f"<span class='mut'>{e(f.dimension)}</span> · <strong>{e(f.title)}</strong>{seg}<ul>"
                )
                out += [f"<li>{e(x)}</li>" for x in f.evidence]
                out += [f"<li class='mut'>位置：<code>{e(x)}</code></li>" for x in f.where]
                out += [f"<li><strong>建议</strong>：{e(x)}</li>" for x in f.fix]
                out.append("</ul></div>")
        if a["stats"]:
            out.append(
                "<h3>逐段数据</h3><div class='card wrap'><table><tr><th>片段</th><th>帧/秒</th><th>超时%</th>"
                "<th>P95</th><th>最长</th><th>连续</th><th>延迟 P95</th><th>move/帧</th>"
                "<th>超时帧：输入 / 渲染 / 未归因</th><th>松手最长</th></tr>"
            )
            for s in a["stats"]:
                cls = " class='bad'" if s.late_pct >= 10 else ""
                lm = s.late_mean
                split = f"{lm.input:.1f} / {lm.render:.1f} / {lm.other:.1f}" if lm else "—"
                out.append(
                    f"<tr><td>{e(s.name)}</td><td>{s.fps:.0f}</td><td{cls}>{s.late_pct:.0f}</td>"
                    f"<td>{s.dt_p95:.0f}</td><td>{s.dt_max:.0f}</td><td>{s.streak}</td>"
                    f"<td>{fmt(s.latency_p95, nd=0)}</td><td>{s.moves_per_frame:.1f}</td>"
                    f"<td>{split}</td><td>{fmt(s.tail_max, nd=0)}</td></tr>"
                )
            out.append("</table></div>")
            out.append(
                "<p class='legend'><span><i style='background:var(--i)'></i>输入处理</span>"
                "<span><i style='background:var(--r)'></i>渲染</span>"
                "<span><i style='background:var(--o)'></i>未归因 / 空闲</span>"
                "<span>虚线 = 1.5 倍刷新周期</span></p>"
            )
            for s in a["stats"]:
                out.append(
                    f"<div class='card'><div class='mut'>{e(s.name)} · 每帧耗时（ms）</div>{frame_chart(s)}</div>"
                )
    out.append("</main></html>")
    return "\n".join(out)


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SystemExit(f"{path}: 不是 {SCHEMA} 报告")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分析 Tavotto 性能探针报告")
    ap.add_argument("reports", nargs="+", help="tavotto-perf-*.json")
    ap.add_argument("--html", help="另存一份 HTML 报告（多份报告时含机器对照）")
    ap.add_argument("--json", help="另存机器可读的结论")
    args = ap.parse_args(argv)

    analyses = [(p, analyze_report(load(p))) for p in args.reports]
    for p, a in analyses:
        print(text_report(p, a))
    if args.html:
        Path(args.html).write_text(html_report(analyses), encoding="utf-8")
        print(f"HTML 报告：{args.html}")
    if args.json:
        payload = [
            {
                "report": p,
                "machine": machine_line(a),
                "grades": [{"dimension": d, "grade": g, "why": w} for d, g, w in a["grades"]],
                "headroom": a["headroom"],
                "findings": [f.to_json() for f in a["findings"]],
                "segments": [
                    {
                        "name": s.name,
                        "fps": round(s.fps, 1),
                        "late_pct": round(s.late_pct, 1),
                        "dt_p95": s.dt_p95,
                        "streak": s.streak,
                        "latency_p95": s.latency_p95,
                        "tail_max": s.tail_max,
                        "late_ms": (
                            {
                                "input": round(s.late_mean.input, 2),
                                "render": round(s.late_mean.render, 2),
                                "other": round(s.late_mean.other, 2),
                            }
                            if s.late_mean
                            else None
                        ),
                        "renders_per_move": {k: round(v, 2) for k, v in s.renders_per_move.items()},
                    }
                    for s in a["stats"]
                ],
            }
            for p, a in analyses
        ]
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
