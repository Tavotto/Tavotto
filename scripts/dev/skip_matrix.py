"""快线 skip 的补验矩阵：一条 skip 在别的 pytest lane 上有没有真的 pass 过。

「55 个 skip 不等于 55 个缺失测试」——但也不等于 55 个别处补验过的测试。这个脚本把一个
CI run 的全部 pytest junit 报告读进来，按 skip 理由分组，逐条回答：**同一提交**上有没有
哪个 lane 让它跑过并 pass。回答不了的（没有任何 lane 跑过）如实标 `—`，不猜「预期某个
job 会跑」。那一半（哪个 job 提供前提、发布要不要求）是人工登记的，在
`docs/ci/skip-evidence-matrix.md`。

用法：
    gh run download <run-id> -p "pytest-*" -D <dir>
    python scripts/dev/skip_matrix.py <dir> [--lane fast/ubuntu/3.13] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

LANE_RE = re.compile(
    r"pytest-backend-(fast|platforms)-(ubuntu|macos|windows)-latest-(3\.\d+)-shard(\d)"
)


def load_lanes(root: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """lane → {nodeid: (outcome, skip_reason)}；同一 lane 的分片合并。"""
    lanes: dict[str, dict[str, tuple[str, str]]] = {}
    for junit in sorted(root.rglob("junit.xml")):
        m = LANE_RE.search(junit.parent.name)
        if not m:
            continue
        lane = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
        tree = ET.parse(junit).getroot()
        for tc in tree.iter("testcase"):
            nodeid = f"{tc.get('classname')}::{tc.get('name')}"
            if tc.find("skipped") is not None:
                out = ("skipped", (tc.find("skipped").get("message") or "").strip())
            elif tc.find("failure") is not None or tc.find("error") is not None:
                out = ("failed", "")
            else:
                out = ("passed", "")
            lanes.setdefault(lane, {})[nodeid] = out
    return lanes


def reason_key(msg: str) -> str:
    first = msg.split("\n")[0]
    return re.sub(r"\d+", "N", first)[:100]


def matrix(lanes: dict, lane: str) -> list[dict]:
    base = lanes[lane]
    rows = []
    for nodeid, (outcome, msg) in sorted(base.items()):
        if outcome != "skipped":
            continue
        passed_in = sorted(
            other
            for other, res in lanes.items()
            if other != lane and res.get(nodeid, ("",))[0] == "passed"
        )
        rows.append(
            {"nodeid": nodeid, "reason": msg, "reason_key": reason_key(msg), "passed_in": passed_in}
        )
    return rows


def render(lanes: dict, lane: str, rows: list[dict]) -> str:
    out = []
    for name, res in sorted(lanes.items()):
        counts = defaultdict(int)
        for outcome, _ in res.values():
            counts[outcome] += 1
        out.append(
            f"{name:<24} total {len(res):>5}  passed {counts['passed']:>5}  "
            f"skipped {counts['skipped']:>3}  failed {counts['failed']}"
        )
    out.append(
        f"\n{lane} 的 skip：{len(rows)} 条，别处 pass {sum(1 for r in rows if r['passed_in'])} 条"
    )
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["reason_key"]].append(r)
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        covered = sum(1 for r in items if r["passed_in"])
        out.append(f"\n[{len(items):>2} 条，别处 pass {covered}] {key}")
        for r in items:
            short = r["nodeid"].split("::")[0].split(".")[-1] + "::" + r["nodeid"].split("::")[-1]
            out.append(f"    {short[:96]}  ← {', '.join(r['passed_in']) or '—'}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("dir", type=Path, help="gh run download 下来的目录（含 pytest-*/junit.xml）")
    ap.add_argument("--lane", default="fast/ubuntu/3.13", help="以哪条 lane 的 skip 为基准")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    lanes = load_lanes(args.dir)
    if args.lane not in lanes:
        print(f"没有 {args.lane} 这条 lane；有的：{sorted(lanes)}", file=sys.stderr)
        return 2
    rows = matrix(lanes, args.lane)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(render(lanes, args.lane, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
