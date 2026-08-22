#!/usr/bin/env python3
"""Codex Review 门禁：按 **disposition**判，不按「有没有发现」判。

这道门禁要解决的是一个具体的、量得出来的问题（见
`docs/audit/2026-08-22-v1-release-process-audit.md` §7）：Codex 在**每一次
push** 上都跑一轮，于是 PR #48 被 review 了 18 轮、#53 被 review 了 15 轮，
而 188 条发现里 **80% 是 P2、P0 是 0**。修一条、触发一轮、再修一条的循环，
既不收敛，也不是这个工具最值钱的用法。

**判据（`docs/engineering/codex-review-policy.md` 是全文）：**

* unresolved **P0 / P1** → check **failure**。它们本来就该挡住合并。
* unresolved **P2** → **warning，不失败**。P2 的正确出口是「有 disposition」，
  而 disposition 可以是「转 issue，本 PR 不修」——把它做成硬失败，等于逼着
  每条 PR 无限扩张，那正是我们要停的循环。
* 轮次 **> 2** → warning（附每一轮的 commit），提醒 scope 没冻住。
* **severity 认不出来** → warning，**并且照常计入 unresolved**。认不出来
  不等于没问题；静默当成 P3 是这道门禁最容易长出来的空转形态。
* **Codex 没跑（usage limit / App 掉线 / 纯文档 PR）** → **neutral，不失败**。
  一道会因为外部服务不在线而永久卡住 PR 的门禁，会在第一次卡住时就被摘掉。

**这个脚本不做的事**（每一条都是有意的）：

* 不自动请求 review（那会重新造出它要消灭的循环）；
* 不 resolve 任何 thread（disposition 是人的判断，不是脚本的）；
* 不按评论条数数轮次——**按 review submission 的 commit 数**。一轮 review
  会产出十几条 thread comment，按条数数会把一轮报成十几轮。

单独跑（本地）::

    python3 scripts/ci/codex_review_gate.py --pr 61
    python3 scripts/ci/codex_review_gate.py --pr 61 --json      # 结构化输出

纯标准库；GitHub 侧一律经 `gh api graphql` 子进程，不引 requests。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# Codex Review 的 GitHub App 登录名。**一个名字变了就整条门禁静默空转**，
# 所以 `--require-bot` 下认不出任何一条 Codex review 会被当成配置错误报出来
# （而不是当成「这轮很干净」）。
CODEX_LOGINS = ("chatgpt-codex-connector", "chatgpt-codex-connector[bot]")

# Codex 在正文里用 shields.io 的 badge 图片标严重度，形如
#   ![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)
# 兼容两种写法：图片 alt，以及退化成纯文本时的 `**P1**` / `[P1]`。
_BADGE = re.compile(r"!\[\s*(P[0-3])\s*Badge\s*\]", re.I)
_PLAIN = re.compile(r"(?:^|[\s\*\[(])(P[0-3])(?:[\s\*\])：:]|$)")

QUERY = """
query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) {
    pullRequest(number:$number) {
      number title isDraft
      headRefOid
      reviews(first:100) {
        nodes { author { login } state submittedAt commit { oid } }
      }
      reviewThreads(first:100) {
        nodes {
          isResolved isOutdated
          comments(first:20) { nodes { author { login } body createdAt url } }
        }
      }
    }
  }
}
"""


class GateError(Exception):
    pass


def _gh_graphql(owner: str, name: str, number: int) -> dict:
    exe = shutil.which("gh")
    if exe is None:
        raise GateError("找不到 gh")
    proc = subprocess.run(
        [exe, "api", "graphql",
         "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={number}",
         "-f", f"query={QUERY}"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise GateError(f"gh api graphql 失败：{proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def severity_of(body: str) -> str | None:
    """从一条 thread 的首条评论里读出严重度。读不出来回 None。

    **读不出来 ≠ 不严重。** 调用方必须把 None 当成「需要人看」，
    不能当成 P3 悄悄放过去——那是这道门禁最容易长出来的空转形态。
    """
    m = _BADGE.search(body or "")
    if m:
        return m.group(1).upper()
    # 退而求其次：只在**开头 200 字**里找裸 P0-P3，避免把正文里
    # 随口提到的 "P2" 当成这条 thread 自己的分级。
    m = _PLAIN.search((body or "")[:200])
    return m.group(1).upper() if m else None


def analyse(pr: dict) -> dict:
    reviews = pr["reviews"]["nodes"]
    codex_reviews = [r for r in reviews
                     if r.get("author") and r["author"]["login"] in CODEX_LOGINS]

    # **轮次按 reviewed commit 数，不按评论条数。** 一轮 review 会产出十几条
    # thread comment；按条数数会把一轮报成十几轮，而那个数字会被人拿去
    # 判「超没超两轮」。
    round_commits: list[str] = []
    for r in codex_reviews:
        oid = (r.get("commit") or {}).get("oid")
        if oid and oid not in round_commits:
            round_commits.append(oid)
    # 极端情况：review 没带 commit（GraphQL 对某些 PENDING review 会这样）。
    # 那时退回按 submission 计数，并如实标记——不能假装它不存在。
    rounds = len(round_commits) if round_commits else len(codex_reviews)

    buckets: dict[str, list[dict]] = {"P0": [], "P1": [], "P2": [], "P3": [], "unknown": []}
    resolved_count = 0
    for t in pr["reviewThreads"]["nodes"]:
        comments = t["comments"]["nodes"]
        if not comments:
            continue
        first = comments[0]
        if not (first.get("author") and first["author"]["login"] in CODEX_LOGINS):
            continue          # 人开的 thread 不归这道门禁管
        if t["isResolved"]:
            resolved_count += 1
            continue
        sev = severity_of(first.get("body") or "")
        title = _title_of(first.get("body") or "")
        item = {"severity": sev or "unknown", "title": title,
                "url": first.get("url"), "outdated": t.get("isOutdated", False),
                "replies": max(0, len(comments) - 1)}
        buckets[sev or "unknown"].append(item)

    return {
        "pr": pr["number"],
        "title": pr["title"],
        "is_draft": pr["isDraft"],
        "head": pr.get("headRefOid"),
        "codex_ran": bool(codex_reviews),
        "rounds": rounds,
        "round_commits": round_commits,
        "resolved": resolved_count,
        "unresolved": {k: v for k, v in buckets.items() if v},
        "counts": {k: len(v) for k, v in buckets.items()},
    }


def _title_of(body: str) -> str:
    """取 Codex 那条发现的小标题（badge 之后的第一段粗体）。"""
    b = _BADGE.sub("", body or "")
    m = re.search(r"\*\*(.+?)\*\*", b, re.S)
    line = (m.group(1) if m else b).strip().splitlines()
    return (line[0].strip() if line else "(无标题)")[:120]


def verdict(a: dict, max_rounds: int) -> dict:
    """把分析结果翻译成 check 结论 + 给人看的理由。"""
    blocking = a["counts"].get("P0", 0) + a["counts"].get("P1", 0)
    warnings: list[str] = []
    errors: list[str] = []

    if not a["codex_ran"]:
        # **不能因为 Codex 没来就永久红。** usage limit、App 掉线、纯文档 PR
        # 都会走到这里；一道会被外部服务卡死的门禁活不过第一次卡死。
        return {"conclusion": "neutral", "errors": [], "warnings": [
            "Codex 没有在这条 PR 上提交过 review。可能是还没 Ready for review、"
            "usage limit、或这类改动不需要（纯文档 / 版本同步 / generated artifact）。"
            "**这道门禁不因此失败**——但合并前请人工确认这是有意的。"]}

    if blocking:
        for sev in ("P0", "P1"):
            for it in a["unresolved"].get(sev, []):
                errors.append(f"未处置的 {sev}：{it['title']}  {it['url'] or ''}")

    p2 = a["counts"].get("P2", 0) + a["counts"].get("P3", 0)
    if p2:
        warnings.append(
            f"有 {p2} 条未处置的 P2/P3。**这不会让这道门禁失败**——"
            f"P2 的正确出口是有 disposition（可以是「转 issue，本 PR 不修」）。"
            f"合并前每条都要在 thread 里给出：Fixed in … / Deferred to #… / "
            f"Guarded in …, long-term fix #… / False positive + 复现证据。")

    unknown = a["counts"].get("unknown", 0)
    if unknown:
        warnings.append(
            f"有 {unknown} 条读不出严重度的未处置 thread。**读不出来不等于不严重**——"
            f"请人工分级后再合并。")

    if a["rounds"] > max_rounds:
        warnings.append(
            f"Codex 已经跑了 {a['rounds']} 轮（策略上限 {max_rounds} 轮）。"
            f"轮次 == push 次数说明 scope 还没冻住；"
            f"见 docs/engineering/codex-review-policy.md。")

    return {"conclusion": "failure" if errors else "success",
            "errors": errors, "warnings": warnings}


def render_summary(a: dict, v: dict, max_rounds: int) -> str:
    L = [f"## Codex Review 门禁 · PR #{a['pr']}", ""]
    icon = {"success": "✅", "failure": "❌", "neutral": "➖"}[v["conclusion"]]
    L.append(f"{icon} **{v['conclusion']}**")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| Codex 轮次 | {a['rounds']}（策略上限 {max_rounds}） |")
    L.append(f"| 已处置 thread | {a['resolved']} |")
    for sev in ("P0", "P1", "P2", "P3", "unknown"):
        n = a["counts"].get(sev, 0)
        if n:
            L.append(f"| 未处置 {sev} | **{n}** |")
    if not any(a["counts"].get(s) for s in ("P0", "P1", "P2", "P3", "unknown")):
        L.append("| 未处置 | 0 |")
    L.append("")
    if a["round_commits"]:
        L.append("每一轮 review 对应的 commit：")
        L.append("")
        for i, oid in enumerate(a["round_commits"], 1):
            L.append(f"{i}. `{oid[:12]}`")
        L.append("")
    for e in v["errors"]:
        L.append(f"- ❌ {e}")
    for w in v["warnings"]:
        L.append(f"- ⚠️ {w}")
    if not v["errors"] and not v["warnings"]:
        L.append("没有需要处置的发现。")
    L.append("")
    L.append("---")
    L.append("策略：`docs/engineering/codex-review-policy.md`　"
             "P2 生命周期：`docs/engineering/p2-lifecycle.md`")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Tavotto/Tavotto"))
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--max-rounds", type=int, default=2)
    ap.add_argument("--json", action="store_true", help="把分析结果打成一行 JSON")
    ap.add_argument("--require-bot", action="store_true",
                    help="Codex 一条 review 都没有时也判失败（默认不判，见模块 docstring）")
    a = ap.parse_args(argv)

    owner, _, name = a.repo.partition("/")
    try:
        raw = _gh_graphql(owner, name, a.pr)
    except GateError as e:
        # **拿不到数据不等于没问题，但也不该让 PR 永久红。** 报 neutral 并说清。
        print(f"::warning::Codex 门禁拿不到数据：{e}", file=sys.stderr)
        return 0

    pr = raw.get("data", {}).get("repository", {}).get("pullRequest")
    if pr is None:
        print(f"::warning::读不到 PR #{a.pr}", file=sys.stderr)
        return 0

    an = analyse(pr)
    v = verdict(an, a.max_rounds)
    if a.require_bot and not an["codex_ran"]:
        v = {"conclusion": "failure",
             "errors": ["--require-bot：这条 PR 上没有任何 Codex review。"
                        "要么 App 没装/掉线，要么登录名变了（门禁会因此静默空转）。"],
             "warnings": v["warnings"]}

    if a.json:
        print(json.dumps({"analysis": an, "verdict": v}, ensure_ascii=False))
    else:
        print(render_summary(an, v, a.max_rounds))

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(render_summary(an, v, a.max_rounds))
    # `--json` 时注解走 stderr：stdout 必须是**一行可解析的 JSON**，
    # 混进 `::warning::` 会让调用方的 json.load 当场炸掉（本地实测）。
    # Actions 两条流都会扫工作流命令，所以注解不会因此丢。
    ann = sys.stderr if a.json else sys.stdout
    for e in v["errors"]:
        print(f"::error::{e}", file=ann)
    for w in v["warnings"]:
        print(f"::warning::{w}", file=ann)

    return 1 if v["conclusion"] == "failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())
