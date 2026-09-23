#!/usr/bin/env python3
"""性能探针报告分析器（ADR 0075）。

用户在「设置 → 诊断 → 性能分析」里录下的 ``tavotto-perf-*.json`` 只有原始数字；
「卡在哪、怎么改」在这里判——判据随代码演进，不必为改一条规则发一个新版本。

    python scripts/perf_report.py report.json                 # 终端摘要
    python scripts/perf_report.py a.json b.json --html out.html   # 多台机器对照 + 图
    python scripts/perf_report.py a.json --json findings.json     # 机器可读的结论

只用标准库：拿到报告的人不一定有开发环境。

判据的主语（ADR 0075 §量什么）：一帧 = 相邻两次 rAF 回调之间的间隔。间隔里的
主线程时间拆成三份——输入处理（pointermove 的业务处理 + React 同步重渲染）、
渲染（rAF 回调 + 样式 / 布局 / 绘制）、未归因（剩下的：GC、别的任务、没插桩的
代码、主线程之外的合成器 / GPU 等待）。掉帧 = 间隔超过刷新周期的 1.5 倍。
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

#: 插桩点 → 代码位置。分析结论要指得出该改哪一行（claims must point at code）
CODE = {
    "input.handler": "web/src/canvas/interactions.ts（trackPointer → onMove）",
    "doc.txn_update": "web/src/store/documentStore.ts txnUpdate + web/src/lib/history.ts accumulate",
    "snap.compute": "web/src/canvas/interactions.ts startMoveDrag 的 snapMove",
    "input.react_flush": "订阅了拖动中会变的 store 的组件（见组件渲染次数）",
    "raf.preview_write": "web/src/store/svgPreviewStore.ts flushPreviewFrame",
}


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


def mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def fmt(v: float | None, unit: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{nd}f}{unit}"


@dataclass
class Finding:
    severity: float
    title: str
    evidence: list[str]
    where: list[str]
    fix: list[str]
    segment: str = ""

    def to_json(self) -> dict:
        return {
            "severity": round(self.severity, 2),
            "segment": self.segment,
            "title": self.title,
            "evidence": self.evidence,
            "where": self.where,
            "fix": self.fix,
        }


@dataclass
class SegStats:
    name: str
    kind: str
    source: str
    label: str | None
    frames: int
    moves: int
    refresh: float
    fps: float
    late_pct: float
    severe: int
    dt_p50: float
    dt_p95: float
    dt_max: float
    latency_p50: float | None
    latency_p95: float | None
    moves_per_frame: float
    # 掉帧里平均每帧三份（毫秒）
    late_input: float
    late_render: float
    late_other: float
    late_dt: float
    per_move_input: float
    trend_ratio: float | None
    tail_max: float | None
    spans: dict
    counts: dict
    context: dict
    rows: list = field(repr=False, default_factory=list)


def refresh_of(report: dict) -> float:
    idle = [x for x in report.get("idle_frame_ms") or [] if x > 0]
    if len(idle) >= 10:
        r = statistics.median(idle)
        # 空闲时也掉帧的机器，中位数仍然接近刷新周期；夹一下防离谱值
        return min(max(r, 6.0), 34.0)
    return 1000 / 60


def seg_stats(seg: dict, idx: int, refresh: float) -> SegStats | None:
    rows = seg.get("frames") or []
    if len(rows) < MIN_FRAMES or (seg.get("moves") or 0) < MIN_MOVES:
        return None
    # 第一帧里有片段开始时采上下文的开销（遍历 DOM），不算进拖动本身
    rows = rows[1:]
    dts = [r[DT] for r in rows]
    late_rows = [r for r in rows if r[DT] > refresh * LATE_FACTOR]
    moves = sum(r[MOVES] for r in rows) or 1

    def parts(r: list) -> tuple[float, float, float]:
        inp = r[HANDLER] + r[FLUSH]
        ren = r[RENDER] or 0.0
        other = max(0.0, r[DT] - inp - ren)
        return inp, ren, other

    li = [parts(r) for r in late_rows]
    lat = [r[LATENCY] for r in rows if r[LATENCY] is not None and r[MOVES] > 0]

    # 越拖越慢：前三分之一与后三分之一的「每个 pointermove 的输入处理成本」之比
    trend = None
    third = len(rows) // 3
    if third >= 10:

        def per_move(rs: list) -> float | None:
            m = sum(r[MOVES] for r in rs)
            return (sum(r[HANDLER] + r[FLUSH] for r in rs) / m) if m else None

        a, b = per_move(rows[:third]), per_move(rows[-third:])
        if a and b and a > 0.05:
            trend = b / a

    tail = seg.get("tail") or []
    kind = seg.get("kind", "?")
    label = seg.get("label")
    src = seg.get("source", "user")
    name = f"#{idx + 1} {KIND_LABEL.get(kind, kind)}" + (
        f"（自动测试 {label}）" if src == "synthetic" else ""
    )
    return SegStats(
        name=name,
        kind=kind,
        source=src,
        label=label,
        frames=len(rows),
        moves=moves,
        refresh=refresh,
        fps=1000 / statistics.fmean(dts),
        late_pct=100 * len(late_rows) / len(rows),
        severe=sum(1 for d in dts if d > 50),
        dt_p50=pct(dts, 0.5) or 0,
        dt_p95=pct(dts, 0.95) or 0,
        dt_max=max(dts),
        latency_p50=pct(lat, 0.5),
        latency_p95=pct(lat, 0.95),
        moves_per_frame=moves / len(rows),
        late_input=mean([x[0] for x in li]) or 0,
        late_render=mean([x[1] for x in li]) or 0,
        late_other=mean([x[2] for x in li]) or 0,
        late_dt=mean([r[DT] for r in late_rows]) or 0,
        per_move_input=sum(r[HANDLER] + r[FLUSH] for r in rows) / moves,
        trend_ratio=trend,
        tail_max=max((r[DT] for r in tail), default=None),
        spans=seg.get("spans") or {},
        counts=seg.get("counts") or {},
        context=seg.get("context") or {},
        rows=rows,
    )


def span_mean(s: SegStats, name: str) -> float | None:
    st = s.spans.get(name)
    if not st or not st.get("count"):
        return None
    return st["total"] / st["count"]


def analyze_segment(s: SegStats) -> list[Finding]:
    out: list[Finding] = []
    if s.late_pct < 5:
        return out
    budget = s.refresh
    shares = {"input": s.late_input, "render": s.late_render, "other": s.late_other}
    total = sum(shares.values()) or 1
    base = [
        f"{s.frames} 帧里 {s.late_pct:.0f}% 超时（刷新周期 {budget:.1f}ms），"
        f"平均 {s.fps:.0f} 帧/秒，最长一帧 {s.dt_max:.0f}ms",
        f"超时帧平均 {s.late_dt:.1f}ms = 输入处理 {s.late_input:.1f} + 渲染 {s.late_render:.1f}"
        f" + 未归因 {s.late_other:.1f}",
    ]
    sev_base = s.late_pct / 100

    # ---- 输入处理
    if shares["input"] / total >= 0.35:
        ev = list(base)
        ev.append(
            f"每个 pointermove 的处理成本 {s.per_move_input:.2f}ms，"
            f"每帧 {s.moves_per_frame:.1f} 个 pointermove"
        )
        where = [CODE["input.handler"]]
        fix = []
        txn = span_mean(s, "doc.txn_update")
        if txn is not None:
            ev.append(
                f"doc.txn_update 平均 {txn:.2f}ms / 次（{s.spans['doc.txn_update']['count']} 次）"
            )
            where.append(CODE["doc.txn_update"])
        flush = span_mean(s, "input.react_flush")
        handler = span_mean(s, "input.handler")
        if flush is not None and handler is not None:
            ev.append(
                f"业务处理 {handler:.2f}ms + React 同步重渲染 {flush:.2f}ms（每次 pointermove）"
            )
        if s.moves_per_frame > 1.2:
            fix.append(
                "把 pointermove 合并到每帧一次：trackPointer 只记下最新坐标，在 rAF 里调一次 onMove"
                "（预览平面已经这么做了，画布对象的 txnUpdate 还没有）。"
            )
        heavy = top_renders(s)
        if heavy:
            ev.append("拖动中每个 pointermove 引起的组件渲染：" + "，".join(heavy))
            where.append(CODE["input.react_flush"])
            fix.append(
                "收窄这些组件的订阅：selector 只取与本组件有关的切片（配 shallow 比较），"
                "拖动中不需要跟手的面板（属性页 / 左栏）改成松手后再读。"
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
        out.append(
            Finding(
                sev_base * shares["input"] / total * 10,
                "拖动时输入处理太重",
                ev,
                where,
                fix,
                s.name,
            )
        )

    # ---- 越拖越慢
    if s.trend_ratio is not None and s.trend_ratio >= 1.6:
        patches = s.context.get("txn_patches_at_end")
        ev = [
            f"后三分之一每个 pointermove 的处理成本是前三分之一的 {s.trend_ratio:.1f} 倍",
        ]
        if patches:
            ev.append(f"松手时事务里攒了 {patches} 条补丁")
        out.append(
            Finding(
                max(1.0, s.trend_ratio) * sev_base * 3,
                "越拖越慢：每次移动的成本随拖动时长增长",
                ev,
                [CODE["doc.txn_update"]],
                [
                    "history.accumulate 每次都 `[...txn.patches, ...patches]` 整份复制，"
                    "一次拖动是 O(n²)。改成原地 push，或拖动中只保留每条路径的最后一条补丁"
                    "（compress 已有这条逻辑，可以挪到累加时做）。",
                ],
                s.name,
            )
        )

    # ---- 渲染
    if shares["render"] / total >= 0.35:
        ev = list(base)
        ctx = s.context
        big = ctx.get("largest_svg_nodes")
        if big is not None:
            ev.append(
                f"页面上 SVG 节点 {ctx.get('svg_nodes')} 个，最大一张 {big} 个；"
                f"DOM 节点 {ctx.get('dom_nodes')} 个；预览表示法 {ctx.get('preview_modes')}"
            )
        pw = span_mean(s, "raf.preview_write")
        if pw is not None:
            ev.append(f"rAF 里写预览 DOM 平均 {pw:.2f}ms")
        fix = [
            "拖动中把被拖的东西放进独立合成层：外层包装元素上用 CSS transform + will-change，"
            "而不是改 SVG 里 <g> 的 transform 属性（后者让整张 SVG 重新布局 / 重绘）。",
        ]
        if big and big > 5000:
            fix.append(
                f"最大的 SVG 有 {big} 个节点：拖动期间换成位图预览（lib/previewBudget.ts 的分档），"
                "或把与被拖元素无关的部分栅格化。"
            )
        out.append(
            Finding(
                sev_base * shares["render"] / total * 10,
                "拖动时浏览器渲染（样式 / 布局 / 绘制）太重",
                ev,
                ["web/src/store/svgPreviewStore.ts writeTransform", "web/src/canvas/PanelView.tsx"],
                fix,
                s.name,
            )
        )

    # ---- 未归因
    if shares["other"] / total >= 0.5:
        ev = list(base)
        stores = {k: v for k, v in s.counts.items() if k.startswith("store.")}
        busy = sorted(stores.items(), key=lambda kv: -kv[1])[:4]
        if busy:
            ev.append("拖动中 store 通知次数：" + "，".join(f"{k[6:]} {v}" for k, v in busy))
        fix = [
            "插桩之外的时间：用 Safari → 开发 → Tavotto → 时间线 录一次同样的拖动，"
            "看超时帧里是 JavaScript、垃圾回收还是合成（Composite）。",
        ]
        if stores.get("store.render", 0) > 5:
            fix.append("拖动中 renderStore 在变：检查是不是有面板在拖动途中被重新渲染或预取。")
        out.append(
            Finding(
                sev_base * shares["other"] / total * 6,
                "超时帧里大部分时间不在已插桩的代码里",
                ev,
                ["（未归因）"],
                fix,
                s.name,
            )
        )
    return out


def top_renders(s: SegStats) -> list[str]:
    items = []
    for k, v in s.counts.items():
        if not k.startswith("render."):
            continue
        per = v / s.moves
        if per >= 0.3:
            items.append((per, f"{k[7:]} {per:.1f} 次"))
    return [t for _, t in sorted(items, reverse=True)[:5]]


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

    # ---- 自动测试两轮对照：m3 明显更差 = 每个 pointermove 的成本在主导
    syn = {s.label: s for s in stats if s.source == "synthetic"}
    if "m1" in syn and "m3" in syn:
        a, b = syn["m1"], syn["m3"]
        if b.late_pct - a.late_pct >= 15:
            findings.append(
                Finding(
                    (b.late_pct - a.late_pct) / 10,
                    "输入频率越高越卡：每帧多个 pointermove 没有合并",
                    [
                        f"每帧 1 个 pointermove：{a.late_pct:.0f}% 超时；每帧 3 个：{b.late_pct:.0f}% 超时",
                        f"每个 pointermove 的处理成本 {b.per_move_input:.2f}ms",
                    ],
                    [CODE["input.handler"]],
                    [
                        "120Hz 触控板 / 高回报率鼠标在 60Hz 屏上每帧会来 2–3 个 pointermove。"
                        "trackPointer 改成每帧最多处理一次（rAF 合并），这一项的差距应当消失。",
                    ],
                    "自动测试 m1 / m3",
                )
            )

    # ---- 机器状态：不是代码的问题，但决定了怎么读其余数字
    sysf = report.get("system") or {}
    idle = report.get("idle_frame_ms") or []
    machine_notes: list[str] = []
    machine_fix: list[str] = []
    if len(idle) >= 20:
        idle_late = 100 * sum(1 for d in idle if d > refresh * LATE_FACTOR) / len(idle)
        if idle_late >= 5:
            machine_notes.append(f"什么都不做时也有 {idle_late:.0f}% 的帧超时——机器此刻整体很忙")
            machine_fix.append(
                "关掉其他占用 CPU 的程序（活动监视器按 CPU 排序看一眼）后重录一份对照。"
            )
    if sysf.get("low_power_mode"):
        machine_notes.append("低电量模式开着（CPU / GPU 降频）")
        machine_fix.append("关掉低电量模式后重录一份对照。")
    if sysf.get("power_source") == "battery":
        machine_notes.append("用电池供电")
        machine_fix.append("插上电源后重录一份对照。")
    if sysf.get("rosetta"):
        machine_notes.append("Tavotto 在 Rosetta 转译下运行（装错了架构的安装包）")
        machine_fix.append("改装 Apple Silicon 版安装包。")
    lim = sysf.get("cpu_speed_limit")
    if isinstance(lim, int) and lim < 100:
        machine_notes.append(f"CPU 正在降频到 {lim}%（过热）")
        machine_fix.append("等机器凉下来、垫高散热后重录一份对照。")
    if machine_notes:
        findings.append(
            Finding(
                2.0,
                "机器状态会放大卡顿（先排除再看代码）",
                machine_notes,
                ["（运行环境）"],
                machine_fix,
            )
        )

    # ---- 松手后的那一下
    tails = [s for s in stats if s.tail_max and s.tail_max > 100]
    auth = [a.get("commit_to_authority_ms") for a in report.get("authority") or []]
    auth = [a for a in auth if isinstance(a, (int, float))]
    if tails or (auth and (pct(auth, 0.5) or 0) > 500):
        ev = []
        if tails:
            worst = max(tails, key=lambda s: s.tail_max or 0)
            ev.append(
                f"{len(tails)} 次拖动在松手后 0.6 秒内出现长帧，最长 {worst.tail_max:.0f}ms（{worst.name}）"
            )
        if auth:
            ev.append(
                f"图内元素松手 → 权威 SVG 换上画布：中位 {pct(auth, 0.5):.0f}ms，P95 {pct(auth, 0.95):.0f}ms"
            )
        findings.append(
            Finding(
                1.5,
                "松手之后卡一下",
                ev,
                [
                    "web/src/store/documentStore.ts endTxn",
                    "web/src/store/svgPreviewStore.ts reattachPreview",
                ],
                [
                    "松手时的同步工作（endTxn 压缩补丁、自动保存、换上权威 SVG 后的整张重解析）"
                    "拆到下一帧 / 空闲时做；权威渲染慢的话看后端 timings（docs/perf-baseline.md 的口径）。",
                ],
            )
        )

    findings.sort(key=lambda f: -f.severity)
    return {
        "refresh": refresh,
        "stats": stats,
        "findings": findings,
        "system": sysf,
        "report": report,
    }


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
    ]
    return " · ".join(p for p in parts if p)


def text_report(path: str, a: dict) -> str:
    lines = [f"== {path}", machine_line(a), ""]
    if not a["stats"]:
        lines.append("没有足够长的拖动（每段至少 10 帧、5 个 pointermove）。")
        return "\n".join(lines)
    lines.append(
        f"{'片段':<28}{'帧/秒':>7}{'超时%':>7}{'P95ms':>8}{'最长ms':>8}{'延迟P95':>9}  主因"
    )
    for s in a["stats"]:
        dom = max(
            ("输入", s.late_input),
            ("渲染", s.late_render),
            ("未归因", s.late_other),
            key=lambda x: x[1],
        )
        cause = "—" if s.late_pct < 5 else dom[0]
        lines.append(
            f"{s.name:<28}{s.fps:>7.0f}{s.late_pct:>7.0f}{s.dt_p95:>8.1f}{s.dt_max:>8.0f}"
            f"{fmt(s.latency_p95):>9}  {cause}"
        )
    lines.append("")
    if not a["findings"]:
        lines.append("没有发现明显卡点：超时帧都在 5% 以下。")
    for i, f in enumerate(a["findings"], 1):
        head = f"[{i}] {f.title}" + (f"（{f.segment}）" if f.segment else "")
        lines.append(head)
        for e in f.evidence:
            lines.append(f"    · {e}")
        for w in f.where:
            lines.append(f"    ↳ {w}")
        for x in f.fix:
            lines.append(f"    ✎ {x}")
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
        inp = r[HANDLER] + r[FLUSH]
        ren = r[RENDER] or 0.0
        oth = max(0.0, r[DT] - inp - ren)
        y = h
        for v, cls in ((inp, "i"), (ren, "r"), (oth, "o")):
            hh = min(v, cap) * scale
            if hh <= 0:
                continue
            y -= hh
            bars.append(
                f'<rect class="{cls}" x="{x}" y="{y:.1f}" width="{w_bar - 0.5}" height="{hh:.1f}"/>'
            )
    line_y = h - s.refresh * LATE_FACTOR * scale
    width = len(rows) * w_bar
    return (
        f'<svg viewBox="0 0 {width} {h}" preserveAspectRatio="none" class="chart">'
        + "".join(bars)
        + f'<line x1="0" x2="{width}" y1="{line_y:.1f}" y2="{line_y:.1f}" class="budget"/></svg>'
    )


CSS = """
:root{--bg:#f7f7f4;--fg:#1f1f1c;--mut:#6b6b64;--card:#fff;--line:#e3e3dc;--i:#c2410c;--r:#2563eb;--o:#a3a39a}
@media (prefers-color-scheme:dark){:root{--bg:#171715;--fg:#ecece6;--mut:#9a9a92;--card:#212120;--line:#34342f;--i:#fb923c;--r:#60a5fa;--o:#5f5f58}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 -apple-system,"PingFang SC",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--mut);font-size:12px}.bad{color:var(--i);font-weight:600}
.chart{width:100%;height:140px;display:block;background:var(--bg);border-radius:6px}
.chart .i{fill:var(--i)}.chart .r{fill:var(--r)}.chart .o{fill:var(--o)}.chart .budget{stroke:var(--fg);stroke-dasharray:4 3;stroke-width:1;vector-effect:non-scaling-stroke}
.legend span{display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:12px;color:var(--mut)}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
ul{margin:6px 0 0;padding-left:20px}li{margin:2px 0}code{font-size:12px}
.wrap{overflow-x:auto}
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
        out.append(
            "<h2>机器对照</h2><div class='card wrap'><table><tr><th>报告</th><th>机器</th>"
            "<th>帧/秒（中位）</th><th>超时%（中位）</th><th>首要卡点</th></tr>"
        )
        for path, a in analyses:
            fps = [s.fps for s in a["stats"]]
            late = [s.late_pct for s in a["stats"]]
            top = a["findings"][0].title if a["findings"] else "—"
            out.append(
                f"<tr><td>{e(Path(path).name)}</td><td>{e(machine_line(a))}</td>"
                f"<td>{fmt(statistics.median(fps) if fps else None, nd=0)}</td>"
                f"<td>{fmt(statistics.median(late) if late else None, nd=0)}</td><td>{e(top)}</td></tr>"
            )
        out.append("</table></div>")
    for path, a in analyses:
        out.append(f"<h2>{e(Path(path).name)}</h2><p class='mut'>{e(machine_line(a))}</p>")
        if a["findings"]:
            for i, f in enumerate(a["findings"], 1):
                seg = f"（{e(f.segment)}）" if f.segment else ""
                out.append(f"<div class='card'><strong>{i}. {e(f.title)}</strong>{seg}<ul>")
                out += [f"<li>{e(x)}</li>" for x in f.evidence]
                out += [f"<li class='mut'>位置：<code>{e(x)}</code></li>" for x in f.where]
                out += [f"<li><strong>建议</strong>：{e(x)}</li>" for x in f.fix]
                out.append("</ul></div>")
        else:
            out.append("<div class='card'>没有发现明显卡点。</div>")
        if a["stats"]:
            out.append(
                "<div class='card wrap'><table><tr><th>片段</th><th>帧/秒</th><th>超时%</th>"
                "<th>P95 帧</th><th>最长帧</th><th>输入延迟 P95</th><th>pointermove/帧</th>"
                "<th>超时帧：输入 / 渲染 / 未归因</th></tr>"
            )
            for s in a["stats"]:
                cls = " class='bad'" if s.late_pct >= 10 else ""
                out.append(
                    f"<tr><td>{e(s.name)}</td><td>{s.fps:.0f}</td><td{cls}>{s.late_pct:.0f}</td>"
                    f"<td>{s.dt_p95:.1f}</td><td>{s.dt_max:.0f}</td><td>{fmt(s.latency_p95)}</td>"
                    f"<td>{s.moves_per_frame:.1f}</td>"
                    f"<td>{s.late_input:.1f} / {s.late_render:.1f} / {s.late_other:.1f}</td></tr>"
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
                "findings": [f.to_json() for f in a["findings"]],
                "segments": [
                    {
                        "name": s.name,
                        "fps": round(s.fps, 1),
                        "late_pct": round(s.late_pct, 1),
                        "dt_p95": s.dt_p95,
                        "late_ms": {
                            "input": round(s.late_input, 2),
                            "render": round(s.late_render, 2),
                            "other": round(s.late_other, 2),
                        },
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
