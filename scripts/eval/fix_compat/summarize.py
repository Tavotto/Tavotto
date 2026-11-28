"""汇总 results/*.jsonl：按题 × 组的成功率表 + 「卡在哪」的分类计数 + 工具使用统计。

用法：`python scripts/eval/fix_compat/summarize.py [--results DIR]`（或 `harness.py report`）。
一行一次运行；同一 (题, 组) 多次运行时单元格写 `成功数/运行数`。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent


def load(results: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in sorted(results.glob("*.jsonl")):
        out[f.stem] = [
            json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
    return out


def is_ok_before(r: dict) -> bool:
    """未修前就出图（已修家族）：A / B 不叫模型（`already_ok`），baseline 里靠 before 判。"""
    b = r.get("before") or {}
    return bool(r.get("already_ok")) or (b.get("phase") == "completed" and b.get("figure_captured"))


def table(data: dict[str, list[dict]]) -> str:
    conds = [c for c in ("baseline", "A", "M", "B", "C") if c in data]
    ids = sorted({r["id"] for rs in data.values() for r in rs})
    cell: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for c, rs in data.items():
        for r in rs:
            cell[(r["id"], c)].append(bool(r["success"]))
    lines = ["| 题 | 家族 | " + " | ".join(conds) + " |", "|---|---|" + "---|" * len(conds)]
    fam = {r["id"]: r["family"] for rs in data.values() for r in rs}
    for i in ids:
        cs = []
        for c in conds:
            v = cell.get((i, c))
            cs.append(f"{sum(v)}/{len(v)}" if v else "-")
        lines.append(f"| {i} | {fam[i]} | " + " | ".join(cs) + " |")
    tot = []
    for c in conds:
        # 分母：有区分度的题（本来就出图的 already_ok 和 solvable=false 的对照题都不算）
        runs = [r for r in data[c] if r.get("solvable", True) and not is_ok_before(r)]
        ok = sum(1 for r in runs if r["success"])
        tot.append(f"{ok}/{len(runs)}" if runs else "-")
    lines.append(
        "| **合计（不含 already_ok 与 solvable=false 对照题）** | | " + " | ".join(tot) + " |"
    )
    ctrl = []
    for c in conds:
        runs = [r for r in data[c] if not r.get("solvable", True)]
        ctrl.append(f"{sum(1 for r in runs if r['success'])}/{len(runs)}" if runs else "-")
    lines.append("| 对照题（solvable=false）单列 | | " + " | ".join(ctrl) + " |")
    return "\n".join(lines)


def stuck_counts(data: dict[str, list[dict]]) -> str:
    lines = []
    for c in ("A", "M", "B", "C"):
        if c not in data:
            continue
        cnt = Counter(r.get("stuck", "?") for r in data[c] if not r["success"])
        lines.append(
            f"{c} 失败的首个卡点：" + (", ".join(f"{k}×{v}" for k, v in cnt.most_common()) or "无")
        )
    return "\n".join(lines)


def tool_stats(data: dict[str, list[dict]]) -> str:
    lines = []
    for c in ("A", "M", "B", "C"):
        rs = [r for r in data.get(c, []) if not r.get("already_ok")]
        if not rs:
            continue
        calls: Counter = Counter()
        for r in rs:
            for t in r.get("trajectory") or []:
                calls[t.get("tool")] += 1
        n = len(rs)
        avg = lambda k: round(sum((r.get(k) or 0) for r in rs) / n, 1)  # noqa: E731
        lines.append(
            f"{c}（n={n}）：平均步数 {avg('steps')}，平均工具调用 "
            f"{round(sum(len(r.get('trajectory') or []) for r in rs) / n, 1)}，"
            f"平均用时 {avg('wall_s_total')} s，token in/out 平均 {avg('tokens_in')}/{avg('tokens_out')}"
        )
        lines.append("  工具：" + ", ".join(f"{k}×{v}" for k, v in calls.most_common(14)))
    return "\n".join(lines)


def hint_follow(data: dict[str, list[dict]]) -> str:
    """A 组每次应用试运行（审计 `attempt`）：给了哪条提示、模型接下来动了哪个会改东西的工具、下一次失败形状变没变。
    离线提炼新提示（ADR 0197 第 3 层）看的就是这张表：给了提示却没照做、照做了形状没动，都是要改提示或加提示的地方。"""
    rows = []
    for r in data.get("A", []):
        tr = r.get("trajectory") or []
        idx = [i for i, t in enumerate(tr) if t.get("attempt")]
        for j, i in enumerate(idx):
            att = tr[i]["attempt"]
            end = idx[j + 1] if j + 1 < len(idx) else len(tr)
            nxt = next(
                (t.get("tool") for t in tr[i + 1 : end] if t.get("tier") not in ("observe", None)),
                "-",
            )
            moved = "-"
            if j + 1 < len(idx):
                later = tr[idx[j + 1]]["attempt"]
                moved = (
                    "same" if later.get("after") == att.get("after") else str(later.get("after"))
                )
            rows.append(
                f"| {r['id']} | {att.get('n')} | {att.get('after') or '?'} | {att.get('hint') or '-'} | {nxt} | {moved} |"
            )
    if not rows:
        return "（没有带试运行记录的 A 组运行：审计里还没有 `attempt` 字段的旧结果）"
    head = "| 题 | 第几次 | 失败形状 | 给的提示 | 接着动的工具 | 下一次的形状 |\n|---|---|---|---|---|---|"
    return head + "\n" + "\n".join(rows)


def memory_pairs(data: dict[str, list[dict]]) -> str:
    """「乙」题 A 组（全新数据目录）对 M 组（同一个数据目录里先修过「甲」，ADR 0197 第 3 层·在线）：成功数、平均步数、平均用时。
    M 组里「甲」没修好的那几次单独数：那时本机没记下做法，「乙」等于没得参考。"""
    m_rows = data.get("M") or []
    if not m_rows:
        return "（没有 M 组结果）"
    lines = [
        "| 乙题 | A 成功 | A 平均步数 | A 修复用时 s | M 成功 | M 平均步数 | M 修复用时 s | M 里甲修好 / 记下做法 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def agg(rs: list[dict]) -> tuple[str, str, str]:
        if not rs:
            return "-", "-", "-"
        ok = sum(1 for r in rs if r.get("success"))
        steps = [r.get("steps") or 0 for r in rs]
        wall = [
            r.get("wall_s") or 0 for r in rs
        ]  # 只算修这一题的那段（M 组的 wall_s_total 含修「甲」）
        return (
            f"{ok}/{len(rs)}",
            str(round(sum(steps) / len(rs), 1)),
            str(round(sum(wall) / len(rs), 1)),
        )

    for pid in sorted({r["id"] for r in m_rows}):
        a = [r for r in data.get("A") or [] if r["id"] == pid]
        m = [r for r in m_rows if r["id"] == pid]
        warm_ok = sum(1 for r in m if (r.get("warmup") or {}).get("success"))
        remembered = sum(1 for r in m if r.get("memory_before"))
        lines.append(
            f"| {pid} | {' | '.join(agg(a))} | {' | '.join(agg(m))} | {warm_ok}/{len(m)} · {remembered}/{len(m)} |"
        )
    return "\n".join(lines)


def cited_known_issue(rec: dict) -> bool:
    """修复会话是否引用了已知问题（ADR 0221 §二）。

    规则（结构化信号，不读自由文本）：轨迹里任何一行带非空 `known_issue`（harness 从审计结果的 `known_issue` 键搬来），
    或结果码字段里出现 `known_issue` 字样。现在的产品还没有这个键（第 2 期才加），所以全是 False，不报错。"""
    for t in rec.get("trajectory") or []:
        if t.get("known_issue") or "known_issue" in str(t.get("result") or ""):
            return True
    return False


def misjudged_user_fault(rec: dict) -> bool:
    """是否把产品缺陷当成用户环境问题（近似规则，审计里拿不到模型的结论原文，所以只用结构化信号）：

    1. 报产品问题却被工具退回：`prepare_diagnostics` 带 `issue`（审计里是 `<dict>`），结果码 `bad_arguments`
       （缺字段，`product_reason` 为空是其中一种；审计只留长度不留内容，分不出是哪个字段）或 `not_a_product_defect`
       （工具判这是用户侧能补的失败）；
    2. 题库标了 `fault.product_defect`（产品缺陷形，如自检被守卫误拦）、这次又没修好、整条轨迹里没有一次成功的
       `prepare_diagnostics`——模型按「用户环境问题」收尾了，产品缺陷没有报出去。
    只对 A / M 组有意义（B 组没有审计行，恒 False）。"""
    rows = [t for t in rec.get("trajectory") or [] if t.get("tool") == "prepare_diagnostics"]
    for t in rows:
        if "issue" in str(t.get("args") or "") and t.get("result") in (
            "bad_arguments",
            "not_a_product_defect",
        ):
            return True
    if rec.get("product_defect") and not rec.get("success") and rec.get("cond") in ("A", "M"):
        return not any(t.get("result") is True for t in rows)
    return False


def environment_family(data: dict[str, list[dict]]) -> str:
    """环境家族（ADR 0221 §六）的两列：每题每组 `cited_known_issue` / `misjudged_user_fault` 的次数 / 运行数。"""
    lines = [
        "| 题 | 组 | 运行数 | 成功 | cited_known_issue | misjudged_user_fault |",
        "|---|---|---|---|---|---|",
    ]
    n_rows = 0
    for c in ("baseline", "A", "M", "B", "C"):
        by_id: dict[str, list[dict]] = defaultdict(list)
        for r in data.get(c, []):
            if r.get("family") == "environment":
                by_id[r["id"]].append(r)
        for i, rs in sorted(by_id.items()):
            n_rows += 1
            lines.append(
                f"| {i} | {c} | {len(rs)} | {sum(1 for r in rs if r['success'])} "
                f"| {sum(cited_known_issue(r) for r in rs)}/{len(rs)} "
                f"| {sum(misjudged_user_fault(r) for r in rs)}/{len(rs)} |"
            )
    return "\n".join(lines) if n_rows else "（没有环境家族的运行）"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(HERE / "results"))
    a = ap.parse_args()
    data = load(Path(a.results))
    if not data:
        print("没有结果")
        return
    print(table(data))
    print()
    print(stuck_counts(data))
    print()
    print(tool_stats(data))
    print()
    print(hint_follow(data))
    print()
    print(memory_pairs(data))
    print()
    print(environment_family(data))


if __name__ == "__main__":
    main()
