#!/usr/bin/env python3
"""从 enrollment.json（真值）派生 ENROLLMENT.md（人读）。与 generate_facade_ledger 同一条纪律：
md 不手改；`--check` 用于门禁（tests/test_foundation_harness.py）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LEDGER = HERE / "enrollment.json"
OUT = HERE / "ENROLLMENT.md"


def render(ledger: dict) -> str:
    counts: dict[str, int] = {}
    for c in ledger["cases"]:
        counts[c["enrollment"]] = counts.get(c["enrollment"], 0) + 1
    lines = [
        "# case enrollment 台账（派生视图）",
        "",
        "真值是 [`enrollment.json`](enrollment.json)，本文件由 `tools/generate_enrollment.py` 生成，不手改。",
        "状态含义见 [`03_CI_POLICY.md`](03_CI_POLICY.md) §3 与 ADR 0053 §五：**只有 enforced 且结果目录里有有效通过记录的实例才算通过**；",
        "planned / observing / later 是登记，不是成绩。",
        "",
        f"能力版本：`{ledger['capability_version']}` · 计数："
        + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "| case | 标题 | enrollment | lane | 阶段 | 用例 | fixture | 场景 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in ledger["cases"]:
        lines.append(
            f"| {c['case_id']} | {c['title']} | {c['enrollment']} | {c['lane']} | {c['stage']} | "
            f"{('`' + c['test'] + '`') if c.get('test') else '—'} | {('`' + c['fixture'] + '`') if c.get('fixture') else '—'} | "
            f"{', '.join(c.get('scenario_refs') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只比对，不写")
    args = ap.parse_args(argv)
    text = render(json.loads(LEDGER.read_text(encoding="utf-8")))
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print("ENROLLMENT.md 与 enrollment.json 不一致，请重新生成", file=sys.stderr)
            return 1
        print("ENROLLMENT.md 是最新的")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
