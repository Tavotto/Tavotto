#!/usr/bin/env python3
"""把各环节的报告汇成一张表，写进 GitHub Step Summary。**纯标准库**。

存在的理由只有一个：**不让人为了知道「这次跑成什么样」去翻五千行日志。**
每个脚本自己也写 summary，但那是分散的；这里给一张总表，并且刻意把
**正确性与性能分开判**——「渲染成功但慢了 40%」应该读作

    correctness PASS / performance FAIL

而不是一句含糊的「lab failed」。两者的处置完全不同：前者要回滚，后者要
先确认机器状态再看是不是真回归。

用法：
    python scripts/ci/summarize.py --mode nightly
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import reports_dir, run_metadata, state_root, summary  # noqa: E402

# (报告文件, 显示名, 归到哪一类)
SECTIONS = [
    ("preflight.json", "开跑前体检", "correctness"),
    ("acceptance.json", "候选包验收", "correctness"),
    ("upgrade.json", "升级 N-1 → N", "correctness"),
    ("visual.json", "Golden 视觉回归", "correctness"),
    ("compat.json", "Matplotlib 兼容性", "correctness"),
    ("soak.json", "Soak 与泄漏", "correctness"),
    ("benchmark.json", "性能回归", "performance"),
    ("mutation.json", "Mutation", "advisory"),
]


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _detail(name: str, data: dict) -> str:
    """每一项给一句**具体**的话。只说 PASS/FAIL 等于什么都没说。"""
    if name == "preflight.json":
        bad = [c["name"] for c in data.get("checks", []) if not c["ok"] and not c.get("warn")]
        return "全部通过" if not bad else "、".join(bad)
    if name == "acceptance.json":
        failed = [c["name"] for c in data.get("checks", []) if not c["ok"]]
        return (f"{data.get('wheel', '?')} sha256 {str(data.get('sha256', ''))[:12]}…"
                if not failed else "、".join(failed))
    if name == "upgrade.json":
        # 跳过必须显示成跳过。把它渲染成 PASS 会让人以为升级路径验过了，
        # 而实际上一次都没跑——这正是「假绿」最典型的形态。
        if data.get("skipped"):
            return f"⏭️ 跳过（{data.get('reason', '?')}）：{str(data.get('detail', ''))[:90]}"
        checks = data.get("checks", [])
        bad = [c["name"] for c in checks if not c["ok"]]
        return (f"{data.get('baseline_tag', '?')} → 候选，{len(checks)} 项全过"
                if not bad else "、".join(bad[:3]))
    if name == "visual.json":
        cases = data.get("cases", {})
        total = len(cases)
        skipped = sum(1 for c in cases.values() if c.get("skipped"))
        bad = [k for k, c in cases.items() if c.get("ok") is False]
        passed = total - skipped - len(bad)
        base = f"{passed}/{total - skipped} 张一致"
        if skipped:
            base += f"（{skipped} 张按 manifest 跳过像素比对）"
        return base if not bad else base + " — 变化：" + "、".join(bad[:3])
    if name == "compat.json":
        s2 = data.get("summary", {})
        cls = s2.get("classification", {})
        funnel = {r["stage"]: r for r in s2.get("funnel", [])}
        cap = funnel.get("capture", {})
        parts = [f"{s2.get('cases', 0)} case",
                 f"目标 {data.get('target', '?')}",
                 f"捕获 {cap.get('passed', 0)}/{cap.get('total', 0)}",
                 f"完全支持 {cls.get('full_support', 0)}"]
        # **product_bug 必须出现在这一行**：把它折进「部分支持」的数字里，
        # 扫读的人就永远看不到「有几个是我们自己的缺陷」。
        bugs = s2.get("product_bugs", [])
        if bugs:
            parts.append("产品缺陷 " + "、".join(
                f"{b['id']}:{b['stage'] or '?'}" for b in bugs[:3]))
        else:
            parts.append("产品缺陷 0")
        return "，".join(parts)
    if name == "soak.json":
        a = data.get("analysis", {})
        parts = [f"{data.get('operations', 0)} 次操作 / {len(data.get('errors', []))} 次错误",
                 f"孤儿进程 {len(data.get('orphans', []))}"]
        if "fd_growth_over_span" in a:
            parts.append(f"FD {a['fd_growth_over_span']:+.0f}")
            parts.append(f"RSS {a['rss_growth_mib_over_span']:+.0f} MiB")
        elif a.get("verdict") == "unsupported":
            parts.append("资源趋势未判定（本平台无 /proc）")
        return "，".join(parts)
    if name == "benchmark.json":
        if data.get("code") == "environment_contaminated":
            return "机器被占用，本次数字无参考价值"
        findings = data.get("findings", [])
        regs = [f for f in findings if f["verdict"] == "regression"]
        if not findings:
            return "尚无历史基线，仅记录"
        worst = max((f["delta_pct"] for f in findings if f["delta_pct"] is not None), default=0.0)
        s = f"{len(findings)} 项指标，最大变化 {worst:+.1f}%"
        if regs:
            s += f" — 回归 {len(regs)} 项：" + "、".join(f["metric"].split("::")[-1] for f in regs[:3])
        if not data.get("gate_enforced"):
            s += "（LAB_PERF_GATE 未开，不阻断）"
        return s
    if name == "mutation.json":
        c = data.get("counts")
        if not c:
            return data.get("error", "未运行")
        return (f"killed {c['killed']} / survived {c['survived']} "
                f"（{data.get('survived_ratio', 0):.0%}）"
                + ("" if data.get("gate_enforced") else "，report-only"))
    return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="汇总实验室 CI 的各环节报告")
    ap.add_argument("--mode", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    d = reports_dir()
    meta = run_metadata(args.mode)
    rows: list[str] = []
    verdicts = {"correctness": True, "performance": True}
    present = 0

    for fname, label, kind in SECTIONS:
        data = _read(d / fname)
        if data is None:
            rows.append(f"| {label} | — | 未运行 |")
            continue
        present += 1
        ok = bool(data.get("ok", True))
        if kind in verdicts:
            verdicts[kind] = verdicts[kind] and ok
        # 跳过必须在**结果列**就看得出来。只在细节里写「跳过」是不够的：
        # 扫读的人先看结果列，一个 ✅ PASS 会让他以为这一项验过了，
        # 而实际上一次都没跑。
        if data.get("skipped"):
            mark = "⏭️ 跳过"
        elif kind == "advisory" and not ok:
            mark = "⚠️ 需关注"
        else:
            mark = "✅ PASS" if ok else "❌ FAIL"
        rows.append(f"| {label} | {mark} | {_detail(fname, data)} |")

    head = [
        "## Tavotto Lab Qualification",
        "",
        f"**Commit** `{meta['sha'][:12] or '?'}` · **档位** `{meta['mode'] or '?'}` · "
        f"**版本** {meta['tavotto_version'] or '?'}",
        f"<sub>{meta['os']} · {meta['cpu_count']} 核 · {meta['ram_gib']} GiB · "
        f"Python {meta['python']} · {meta['timestamp']}</sub>",
        "",
        # 分开报，理由见模块开头
        f"**正确性 {'✅ PASS' if verdicts['correctness'] else '❌ FAIL'}**　·　"
        f"**性能 {'✅ PASS' if verdicts['performance'] else '❌ FAIL'}**",
        "",
        "| 环节 | 结果 | 细节 |",
        "| --- | --- | --- |",
    ]
    text = "\n".join(head + rows)
    summary("\n" + text + "\n")
    print(text)

    if present == 0:
        # 一份报告都没有，说明前面的步骤全都没跑起来。这种情况下给一张
        # 全是「未运行」的漂亮表格，比不给更误导。
        print("::warning::没有找到任何报告——前面的步骤可能全部未执行", file=sys.stderr)

    if args.json:
        print(json.dumps({"correctness": verdicts["correctness"],
                          "performance": verdicts["performance"],
                          "reports_found": present}, ensure_ascii=False))
    # 汇总本身不改变成败：各步骤已经各自决定过退出码。这里再判一次只会
    # 让「哪一步失败的」更难定位。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
