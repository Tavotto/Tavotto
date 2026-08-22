#!/usr/bin/env python3
"""统计整个仓库的 Codex Review 用量：轮次、thread 数、严重度分布。

`docs/audit/2026-08-22-v1-release-process-audit.md` §7 的三张表由它生成。
写成脚本而不是把数字抄进文档，是为了让那几个结论**可以复算**——
「Codex 提了 113 次 review」这种数字，读的人应该能自己验一遍。

    python3 scripts/admin/codex_review_stats.py                 # 表格
    python3 scripts/admin/codex_review_stats.py --json          # 结构化

轮次判据与 `scripts/ci/codex_review_gate.py` **共用同一份实现**
（`severity_of` / 按 reviewed commit 去重）——两份会漂开，而漂开的表现
正是「门禁说两轮、报告说十五轮」。
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
from codex_review_gate import CODEX_LOGINS, severity_of   # noqa: E402

QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    pullRequests(first:50, states:[OPEN,MERGED,CLOSED],
                 orderBy:{field:CREATED_AT, direction:DESC}, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title state
        # **两个嵌套 connection 都只回第一页。** 超过 100 条 review 或
        # thread 的 PR 会被少数 —— 而这个脚本产出的正是审计报告里那几个
        # 「113 次 review、188 条 thread」的数字。少数了不会报错，
        # 只会让结论偏保守，而读的人不知道。
        # 本仓库最多的一条 PR 有 18 轮 / 29 条 thread，离 100 还有距离，
        # 所以这里如实**报出被截断**而不是翻页：翻页要为每条 PR 各再发
        # 一串请求，而这个脚本是一次性诊断工具，不值得那个复杂度。
        reviews(first:100) {
          totalCount
          nodes { author { login } commit { oid } }
        }
        reviewThreads(first:100) {
          totalCount
          nodes { isResolved comments(first:1) { nodes { author { login } body } } }
        }
      }
    }
  }
}
"""


def fetch(owner: str, name: str) -> list[dict]:
    exe = shutil.which("gh")
    if exe is None:
        raise SystemExit("找不到 gh")
    out, cursor = [], None
    while True:
        args = [exe, "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}",
                "-f", f"query={QUERY}"]
        if cursor:
            args += ["-F", f"cursor={cursor}"]
        p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise SystemExit(f"gh api graphql 失败：{p.stderr.strip()[:400]}")
        page = json.loads(p.stdout)["data"]["repository"]["pullRequests"]
        out.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            return out
        cursor = page["pageInfo"]["endCursor"]


def summarise(nodes: list[dict]) -> dict:
    rows, sev_all = [], collections.Counter()
    for n in sorted(nodes, key=lambda x: -x["number"]):
        revs = [r for r in n["reviews"]["nodes"]
                if r.get("author") and r["author"]["login"] in CODEX_LOGINS]
        commits, seen = [], set()
        for r in revs:
            oid = (r.get("commit") or {}).get("oid")
            if oid and oid not in seen:
                seen.add(oid)
                commits.append(oid)
        threads = [t for t in n["reviewThreads"]["nodes"]
                   if t["comments"]["nodes"]
                   and t["comments"]["nodes"][0].get("author")
                   and t["comments"]["nodes"][0]["author"]["login"] in CODEX_LOGINS]
        if not revs and not threads:
            continue
        sev = collections.Counter()
        for t in threads:
            sev[severity_of(t["comments"]["nodes"][0]["body"]) or "unknown"] += 1
        sev_all.update(sev)
        # 被截断就如实说 —— 一个悄悄少数的统计比没有统计更坏
        truncated = (n["reviews"].get("totalCount", 0) > len(n["reviews"]["nodes"])
                     or n["reviewThreads"].get("totalCount", 0)
                     > len(n["reviewThreads"]["nodes"]))
        if truncated:
            print(f"::warning::PR #{n['number']} 的 review 或 thread 超过 100 条，"
                  f"本次统计只覆盖前 100 条", file=sys.stderr)
        rows.append({
            "truncated": truncated,
            "pr": n["number"], "state": n["state"], "title": n["title"],
            "rounds": len(commits) or len(revs),
            "submissions": len(revs),
            "threads": len(threads),
            "unresolved": sum(1 for t in threads if not t["isResolved"]),
            "severity": dict(sev),
        })
    return {
        "any_truncated": any(r["truncated"] for r in rows),
        "rows": rows,
        "total_submissions": sum(r["submissions"] for r in rows),
        "total_threads": sum(r["threads"] for r in rows),
        "total_unresolved": sum(r["unresolved"] for r in rows),
        "severity": dict(sev_all),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default="Tavotto")
    ap.add_argument("--name", default="Tavotto")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    s = summarise(fetch(a.owner, a.name))
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    print(f"{'PR':>5} {'state':7} {'轮次':>5} {'thread':>6} {'未处置':>6}  "
          f"{'P0':>2} {'P1':>2} {'P2':>3} {'P3':>2} {'??':>2}  标题")
    for r in s["rows"]:
        v = r["severity"]
        print(f"{r['pr']:>5} {r['state']:7} {r['rounds']:>5} {r['threads']:>6} "
              f"{r['unresolved']:>6}  {v.get('P0',0):>2} {v.get('P1',0):>2} "
              f"{v.get('P2',0):>3} {v.get('P3',0):>2} {v.get('unknown',0):>2}  "
              f"{r['title'][:44]}")
    print()
    print(f"合计：{s['total_submissions']} 次 review 提交，{s['total_threads']} 条 thread，"
          f"其中 {s['total_unresolved']} 条未处置")
    tot = sum(s["severity"].values()) or 1
    for k in ("P0", "P1", "P2", "P3", "unknown"):
        n = s["severity"].get(k, 0)
        print(f"  {k:<8} {n:>4}  {n * 100 / tot:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
