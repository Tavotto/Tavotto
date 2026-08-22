#!/usr/bin/env python3
"""发布链健康检查：**谁最近一次真正跑到了结论**。

这个脚本存在的理由，是 2026-08-22 那次审计发现的一件事
（`docs/audit/2026-08-22-v1-release-process-audit.md` §5）：

    lab-ci.yml    success=0 / failure=8 / cancelled=17   ← **从来没成功过**
    release.yml   最近一次成功是 v0.8.0（两天前），v0.9.0 与 v0.9.1 都失败
    nightly.yml   schedule 腿连续四晚失败

而这些**都没有任何人被通知到**。仓库看起来是绿的，因为 PR 快线（ci.yml）
一直健康，而发布链只在打 tag 的那一天才被人看一眼。

**判据的核心是「有结论」，不是「有 run」。**
`queued` 与 `cancelled` 不是验证——一条卡在队列里三天的 workflow，
在「最近有没有跑过」这个问题上的答案是**没有**。这一条如果搞错，
这个脚本自己就会变成它要消灭的那种东西：一个报平安的门禁。

    python3 scripts/ci/check_release_health.py            # 表格 + job summary
    python3 scripts/ci/check_release_health.py --json     # 结构化报告
    python3 scripts/ci/check_release_health.py --max-age-days 7

纯标准库；GitHub 侧一律经 `gh` 子进程。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# 要盯的 workflow 与各自的新鲜度要求。
#
# **阈值按「它多久本该跑一次」定，不按「我们希望它多久跑一次」定。**
# release.yml 平时不跑（没人天天发版），所以它靠 canary 那条定期演练来
# 保持新鲜——阈值 8 天给周期性 canary 留一点余量。
WATCH = [
    {"file": "ci.yml", "label": "PR 快线", "max_age_days": 2,
     "why": "每条 PR 都跑；两天没有结论说明 runner 或触发条件出问题了"},
    {"file": "lab-ci.yml", "label": "实验室资格验证", "max_age_days": 3,
     "why": "push 到 main 就该跑。它是发行资格验证的同一份实现，"
            "长期不绿等于发布链的主要门禁处于未知状态"},
    {"file": "release.yml", "label": "发布编排", "max_age_days": 8,
     "why": "平时不跑，靠 release-health.yml 的定期 publish=false 演练保持新鲜。"
            "超期意味着下一次正式发版又会是这条链的第一次执行"},
    {"file": "nightly.yml", "label": "安装链路 nightly", "max_age_days": 3,
     "why": "每晚一次。它验的是「真装一遍」，而那条路只有真装才知道"},
]

# **`desktop-tauri.yml` 刻意不在上面这张表里。**
# 它已经改成 `workflow_call`（见 ci/release-orchestrator），被调用时**不产生
# 独立的 workflow run** —— 那些 job 挂在 caller（release.yml）的 run 上。
# `gh run list --workflow desktop-tauri.yml` 现在只回得出改动**之前**的
# push run，而且越来越旧：盯着它等于每过 8 天误报一次「桌面构建超期」，
# 而真实情况是它每次演练都在跑。
#
# 它的新鲜度由 release.yml 覆盖；「它到底有没有执行」由下面的
# `find_jobs_never_seen` 在 caller 的 job 列表里查（reusable 的 job 名
# 形如 `desktop / build (macos-latest, dmg)`）。
#
# 这条是 #66 把桌面链改成 workflow_call 时**直接引入的**对本监控的破坏——
# 改了一处、忘了另一个消费点，本轮第 N 次。（Codex 在 #67 第三轮上指出。）
REUSABLE_ONLY = ("desktop-tauri.yml", "_lab-qualification.yml")

CONCLUSIVE = {"success", "failure", "timed_out", "action_required", "neutral"}

# 排队多久算「卡住」。self-hosted 的实验室 runner 一次跑满要 3 小时，
# 所以并发槽让人等一两个小时是正常的；超过这个数就该有人看一眼。
STUCK_QUEUE_HOURS = 3.0
INCONCLUSIVE = {"cancelled", "skipped", "stale", None, ""}


class HealthError(Exception):
    pass


def _gh_json(args: list[str]) -> object:
    exe = shutil.which("gh")
    if exe is None:
        raise HealthError("找不到 gh")
    p = subprocess.run([exe, *args], capture_output=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise HealthError(f"gh {' '.join(args[:3])} 失败：{p.stderr.strip()[:300]}")
    return json.loads(p.stdout or "[]")


def fetch_runs(repo: str, wf: str, limit: int = 60) -> list[dict]:
    return _gh_json([
        "run", "list", "--repo", repo, "--workflow", wf, "--limit", str(limit),
        "--json", "databaseId,status,conclusion,createdAt,headBranch,event,url",
    ])


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def assess(spec: dict, runs: list[dict], now: datetime) -> dict:
    """一个 workflow 的健康结论。

    三个互不相同的问题，分别回答：
      * **最近一次有结论**是什么时候（`queued`/`cancelled` 不算）；
      * 最近一次**成功**是什么时候；
      * 最近这批 run 里有没有卡在 queued 的（self-hosted 没人领的信号）。
    """
    conclusive = [r for r in runs
                  if r.get("status") == "completed"
                  and r.get("conclusion") in CONCLUSIVE]
    successes = [r for r in conclusive if r.get("conclusion") == "success"]
    # **按排队多久判，不按排队几个判。** 第一版写的是「≥3 个 queued 就告警」，
    # 而实测那一刻 release.yml 有 2 个卡了一小时以上的 run——数量判据放它过去了。
    # 问题的主语是「有没有 run 卡住」，不是「有几个在排队」：正常触发的三条
    # 并发 run 完全无害，而一条卡了三小时的 run 意味着没有 runner 领得走。
    queued = [r for r in runs if r.get("status") in ("queued", "waiting", "pending")]
    stuck = [r for r in queued
             if (now - _parse(r["createdAt"])).total_seconds() > STUCK_QUEUE_HOURS * 3600]

    last_conc = conclusive[0] if conclusive else None
    last_succ = successes[0] if successes else None
    age_days = ((now - _parse(last_conc["createdAt"])).total_seconds() / 86400
                if last_conc else None)
    succ_age_days = ((now - _parse(last_succ["createdAt"])).total_seconds() / 86400
                     if last_succ else None)

    problems: list[str] = []
    level = "ok"

    if not runs:
        level, problems = "error", [f"{spec['file']}: 一次 run 都没有"]
    elif last_conc is None:
        # 有 run，但**一次都没跑到结论**。这正是 lab-ci 当时的状态：
        # 25 次 run 全是 cancelled 或还在跑。
        level = "error"
        problems.append(
            f"最近 {len(runs)} 次 run **一次都没有跑到结论**"
            f"（全是 queued / cancelled）。这不是「最近没跑」，是「跑了但等于没跑」")
    else:
        if age_days is not None and age_days > spec["max_age_days"]:
            level = "error"
            problems.append(
                f"最近一次有结论是 {age_days:.1f} 天前"
                f"（阈值 {spec['max_age_days']} 天）")
        if last_succ is None:
            level = "error"
            problems.append(
                f"最近 {len(runs)} 次 run 里**一次成功都没有**——"
                f"这条通道目前处于「从未验证通过」的状态")
        elif succ_age_days is not None and succ_age_days > spec["max_age_days"] * 2:
            level = "error" if level == "error" else "warning"
            problems.append(f"最近一次成功已经是 {succ_age_days:.1f} 天前")

    if stuck:
        level = "error" if level == "error" else "warning"
        oldest = max((now - _parse(r["createdAt"])).total_seconds() / 3600
                     for r in stuck)
        problems.append(
            f"有 {len(stuck)} 次 run 卡在 queued/waiting 超过 "
            f"{STUCK_QUEUE_HOURS} 小时（最久 {oldest:.1f} 小时）——"
            f"多半是 self-hosted runner 没人领得走，或者卡在 environment 审批上")

    # **最近一次有结论的是失败**：这不影响「新鲜度」，但它回答的是另一个
    # 同样要紧的问题——那条通道此刻是不是绿的。只告警不失败：真失败该由
    # 那条 workflow 自己红，这里重复红一遍只会让人把两个都静音。
    if last_conc is not None and last_conc.get("conclusion") != "success":
        level = "error" if level == "error" else "warning"
        problems.append(
            f"最近一次有结论的 run 是 **{last_conc['conclusion']}**"
            f"（{last_conc.get('url') or ''}）")

    return {
        "file": spec["file"], "label": spec["label"], "level": level,
        "max_age_days": spec["max_age_days"], "why": spec["why"],
        "runs_examined": len(runs),
        "last_conclusive": last_conc and {
            "conclusion": last_conc["conclusion"], "at": last_conc["createdAt"],
            "age_days": round(age_days, 1), "url": last_conc.get("url")},
        "last_success": last_succ and {
            "at": last_succ["createdAt"], "age_days": round(succ_age_days, 1),
            "url": last_succ.get("url")},
        "queued": len(queued),
        "stuck_in_queue": len(stuck),
        "counts": {k: sum(1 for r in runs if r.get("conclusion") == k)
                   for k in ("success", "failure", "cancelled")},
        "problems": problems,
    }


# ────────────────────────────────────────────────────────────────────────
# 「声明了却从没产出过结论的 job」这个扫描**从本工作流拿掉了**（issue #78）。
#
# 它想回答的问题是真的：workflow 里新加一个 job，如果它的 `if:` 永远为假、
# 或者它要的 runner 标签没人领，它会永远不出现，而 workflow 整体照样绿。
#
# 拿掉的原因是**判据不够格，而不是问题不重要**。四轮 review 逐个暴露出来：
#   · matrix 展开后 job 名与 id 不同，所以用了子串匹配；
#   · 而子串匹配让 `release.yml` 自己的 `trust` / `build` 和 `ci.yml` 的
#     `workerd` **满足了 desktop-tauri.yml 的同名声明** —— 那三个 job
#     一次没跑也会被当成「见过」。整个扫描基本是空的；
#   · 一个 job 在早先某次 run 里有过结论、却在当前 in_progress 的 run 里
#     卡了三小时，也漏。
#
# 「声明了没执行」这件事的身份判定需要 `<caller-job-id> / <callee-job-id>`
# 的**精确**结构，而不是子串。那是一次单独的实现，不该夹在这条 PR 里 ——
# 而**一个自己就是空转的空转检测器，比没有它更坏**。
#
# freshness 检查（本文件的核心）不依赖它，且已经在真实数据上验证过。
# ────────────────────────────────────────────────────────────────────────

def render_summary(results: list[dict], missing: list[dict], now: datetime) -> str:
    icon = {"ok": "✅", "warning": "⚠️", "error": "❌"}
    L = ["## 发布链健康检查", "",
         f"检查时刻：`{now.isoformat(timespec='seconds')}`", "",
         "> **判据是「有结论」，不是「有 run」。** `queued` 与 `cancelled` 不算"
         "一次验证——一条卡在队列里三天的 workflow，在「最近有没有跑过」这个"
         "问题上的答案是**没有**。", "",
         "| workflow | 结论 | 最近一次有结论 | 最近一次成功 | 排队 |",
         "|---|---|---|---|---:|"]
    for r in results:
        lc, ls = r["last_conclusive"], r["last_success"]
        lc_s = f"{lc['conclusion']} · {lc['age_days']} 天前" if lc else "**从来没有**"
        ls_s = f"{ls['age_days']} 天前" if ls else "**从来没有**"
        L.append(f"| `{r['file']}`<br><sub>{r['label']}</sub> | {icon[r['level']]} "
                 f"| {lc_s} | {ls_s} | {r['queued']} |")
    L.append("")
    for r in results:
        if r["problems"]:
            L.append(f"### {icon[r['level']]} `{r['file']}`")
            for p in r["problems"]:
                L.append(f"- {p}")
            L.append(f"- <sub>为什么盯它：{r['why']}</sub>")
            c = r["counts"]
            L.append(f"- <sub>最近 {r['runs_examined']} 次："
                     f"success={c['success']} / failure={c['failure']} / "
                     f"cancelled={c['cancelled']}</sub>")
            L.append("")
    if missing:
        L.append("### ❌ 声明了却从没产出过结论的 job")
        L.append("")
        L.append("workflow 里新加的 job，如果 `if:` 永远为假、或者它要的 runner "
                 "标签没人领，会**永远不出现**——而 workflow 整体照样绿。")
        L.append("")
        for m in missing:
            L.append(f"- `{m['workflow']}` → `{m['job']}`：{m['note']}")
        L.append("")
    if not any(r["problems"] for r in results) and not missing:
        L.append("所有被盯的 workflow 都在阈值内、且最近成功过。")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY",
                                                     "Tavotto/Tavotto"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="把结构化报告写到这个文件")
    ap.add_argument("--max-age-days", type=float,
                    help="覆盖所有 workflow 的新鲜度阈值（排查用）")
    a = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    results = []
    for spec in WATCH:
        spec = dict(spec)
        if a.max_age_days:
            spec["max_age_days"] = a.max_age_days
        try:
            runs = fetch_runs(a.repo, spec["file"])
        except HealthError as e:
            results.append({"file": spec["file"], "label": spec["label"],
                            "level": "warning", "max_age_days": spec["max_age_days"],
                            "why": spec["why"], "runs_examined": 0,
                            "last_conclusive": None, "last_success": None,
                            "queued": 0, "stuck_in_queue": 0,
                            # **键要齐。** `render_summary()` 无条件读
                            # `c['success'] / c['failure'] / c['cancelled']`，
                            # 空 dict 会让它 KeyError —— 而这条路径正是
                            # 「GitHub API 抽风」时走的，也就是最需要它把话
                            # 说出来的时候。诊断在最需要时自己挂掉，是本轮
                            # 反复出现的那个形状（#61）。
                            "counts": {"success": 0, "failure": 0, "cancelled": 0},
                            "problems": [f"拿不到数据：{e}"]})
            continue
        results.append(assess(spec, runs, now))

    missing: list[dict] = []      # 见上：job 扫描已移出本工作流（issue #78）

    payload = {"checked_at": now.isoformat(timespec="seconds"),
               "repo": a.repo, "workflows": results, "never_concluded_jobs": missing}

    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_summary(results, missing, now))

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(render_summary(results, missing, now))

    worst = "ok"
    for r in results:
        for p in r["problems"]:
            line = f"{r['file']}: {p}"
            if r["level"] == "error":
                print(f"::error::{line}")
                worst = "error"
            else:
                print(f"::warning::{line}")
                worst = "error" if worst == "error" else "warning"
    for m in missing:
        print(f"::error::{m['workflow']} 的 job `{m['job']}` 从没产出过结论")
        worst = "error"

    # **警告不让 workflow 红。** 一条天天红的健康检查会在第二周被人静音，
    # 而静音之后它连警告都不会再发出来。只有「从没跑通」「超期」「从没执行过
    # 的 job」这三种才失败——它们都意味着某道门禁此刻处于未知状态。
    return 1 if worst == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
