"""汇总 results/*.jsonl：题 × 组成功率表 + 失败原因归类计数 + 失败样例。

用法：`python scripts/eval/vector_edit/summarize.py [--results DIR] [--examples N]`（或 `harness.py report`）。
A 组的加对象题（0208 类）按设计不能新增对象，期望是进 `unhandled`，不进 A 的合计；对照题单独列。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ORDER = ("A", "Aref", "B", "B2")


def load(results: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for f in sorted(results.glob("*.jsonl")):
        out[f.stem] = [
            json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
    return out


def _cell(rs: list[dict] | None, rec_kind: str, cond: str) -> str:
    if not rs:
        return "-"
    mark = ""
    if cond in ("A", "Aref") and rec_kind != "edit":
        mark = "*"  # 期望是进 unhandled、画布不动
    return f"{sum(bool(r['success']) for r in rs)}/{len(rs)}{mark}"


def table(data: dict[str, list[dict]]) -> str:
    conds = [c for c in ORDER if c in data]
    kinds = {r["id"]: (r["kind"], r["family"]) for rs in data.values() for r in rs}
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c, rs in data.items():
        for r in rs:
            by[(r["id"], c)].append(r)
    lines = [
        "| 题 | 类 | 家族 | " + " | ".join(conds) + " |",
        "|---|---|---|" + "---|" * len(conds),
    ]
    for tid, (kind, fam) in kinds.items():
        lines.append(
            f"| {tid} | {kind} | {fam} | "
            + " | ".join(_cell(by.get((tid, c)), kind, c) for c in conds)
            + " |"
        )
    lines.append("")
    lines.append("`*` = A 组按设计不能新增对象，期望是进 `unhandled` 且画布不动；不进 A 的合计。")
    lines.append("")
    lines.append("| 合计 | " + " | ".join(conds) + " |")
    lines.append("|---|" + "---|" * len(conds))
    for label, pred in (
        ("编辑题 edit", lambda r: r["kind"] == "edit"),
        ("加对象题 add", lambda r: r["kind"] == "add"),
        ("对照题 control", lambda r: r["kind"] == "control"),
    ):
        cells = []
        for c in conds:
            rs = [r for r in data[c] if pred(r)]
            cells.append(f"{sum(bool(r['success']) for r in rs)}/{len(rs)}" if rs else "-")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def stuck_counts(data: dict[str, list[dict]]) -> str:
    out = []
    for c in [c for c in ORDER if c in data]:
        cnt = Counter(r.get("stuck", "?") for r in data[c] if not r["success"])
        out.append(f"\n**{c} 失败原因**（共 {sum(cnt.values())} 次）")
        out.extend(f"- {k}：{v}" for k, v in cnt.most_common())
    return "\n".join(out)


def examples(data: dict[str, list[dict]], n: int) -> str:
    out = []
    for c in [c for c in ORDER if c in data]:
        for r in [r for r in data[c] if not r["success"]][:n]:
            bad = [d for d in r.get("predicate_detail") or [] if not d["ok"]]
            out.append(f"\n### {c} · {r['id']} · {r.get('stuck')}")
            if c.startswith("B"):
                calls = [t for t in r.get("trajectory") or [] if "layout" in t["tool"]]
                out.append(f"- 排版工具调用 {len(calls)} 次；末句：{r.get('final_text', '')[:160]}")
                for t in calls[-2:]:
                    out.append(f"  - {t['tool']} {t['args'][:300]} → {str(t.get('result'))[:160]}")
            else:
                out.append(f"- ops：{json.dumps(r.get('ops'), ensure_ascii=False)[:400]}")
                out.append(
                    f"- unhandled：{json.dumps(r.get('unhandled'), ensure_ascii=False)[:300]}"
                )
            out.extend(f"- 没过：{d['t']}：{d['detail']}" for d in bad)
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.environ.get("EVAL_RESULTS") or str(HERE / "results"))
    ap.add_argument("--examples", type=int, default=4)
    a = ap.parse_args()
    data = load(Path(a.results))
    if not data:
        sys.exit("没有结果")
    print(table(data))
    print(stuck_counts(data))
    print(examples(data, a.examples))
    for c in ("B", "B2"):
        rs = data.get(c) or []
        if rs:
            tc = [r.get("tool_calls", 0) for r in rs]
            print(
                f"\n{c} 平均 {sum(tc) / len(tc):.1f} 次工具调用、{sum(r['wall_s'] for r in rs) / len(rs):.0f} s、输入 token {sum(r.get('tokens_in', 0) for r in rs) / len(rs):.0f}"
            )


if __name__ == "__main__":
    main()
