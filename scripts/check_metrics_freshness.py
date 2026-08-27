#!/usr/bin/env python3
"""发行量采集**有没有在跑**。注意：不是「跑得对不对」。

`telemetry-metrics.yml` 头部承诺「丢数据必须有人看见，所以采集器失败就让
这个 workflow 红」。那条保证只覆盖采集器**执行之后失败**——run 从没被创建
时不产生任何红灯，只产生沉默，而沉默和「今天没事」长得一模一样。

2026-08-27 这个洞真的被踩到了：cron 槽被 GitHub 整个丢弃（不是排队、不是
取消，是根本没有 run 记录）。同一账户同一时间窗，auto-hosts 的每小时 cron
丢掉大半、Screener 迟到 9 小时。GitHub 的 schedule 是 best-effort，会迟到
也会整槽丢弃。当时没有任何信号，是人在查别的事时偶然撞见的。

后果不止少一天：`download_count_total` 是累计计数器，区间量靠两次快照做差，
缺一个观测点会让那一段覆盖约 48 小时而不是 24 小时——日粒度趋势上表现为
一根偏高的柱子，**看起来像涨了**。

用法：
    python3 scripts/check_metrics_freshness.py            # 走 GitHub API
    python3 scripts/check_metrics_freshness.py --runs-json fixture.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "Tavotto/Tavotto")
WORKFLOW = "telemetry-metrics.yml"
#: 真正把数据送出去的那一步。它带 `if: ${{ !inputs.dry_run }}`，所以
#: **演练时它是 skipped，而整个 run 照样 success**。判据只看 run 的
#: conclusion 就会把一次 `dry_run=true` 的手动补跑当成「数据落了」，
#: 于是漏跑之后再手动演练一次，看门狗就闭嘴 30 小时——而磁盘上那份
#: 快照仍然是旧的。名字漂了要当场红，见 tests/test_metrics_freshness.py。
UPLOAD_STEP = "采集并上报"

#: 采集器名义上每 24 小时一次。阈值必须夹在「正常最坏」与「漏一次」之间。
#:
#: **这个数字是被实测改过一次的。** 起初定 30 小时，理由是「常态漂移只有
#: 约 45 分钟」。2026-08-27 实测推翻了它：那天 03:17 的槽被 GitHub 延迟到
#: 14:10 才投递，**迟了约 11 小时**（同账户 Screener 那天迟 9 小时）。于是
#: 相邻两次成功上报的间隔可以达到 24 + 11 ≈ 35 小时，30 小时会误报。
#:
#:   正常最坏（一次迟到 11 小时）   ≈ 35 小时
#:   真漏一槽                      ≈ 48 小时
#:
#: 40 小时夹在两者之间。代价是发现一次真漏跑最多晚半天——可以接受：这道
#: 门禁要防的是「连续不跑而无人知晓」，不是把每一次延迟都报成事故。
#: 误报比漏报更贵：一盏经常无故亮红的灯，很快就没人看了。
MAX_AGE_HOURS = 40

OK = "OK"
STALE = "STALE"
NO_SUCCESS = "NO_SUCCESS"
#: run 成功过，但没有一次真的上报（全是 dry_run）。**不能报 OK**：
#: 「跑过了」不等于「数据落了」。
DRY_RUN_ONLY = "DRY_RUN_ONLY"
#: **观测无效，不是「很旧」。** 一次都查不到 run 有两种成因：仓库真的从没
#: 跑过，或者我们问错了地方（改名 / 换仓库 / token 权限不足）。把它当成
#: 「年龄无穷大」会让一个坏掉的判据看起来像一条真实告警。
NO_DATA = "NO_DATA"


def _parse(ts: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def evaluate(
    runs: list[dict], now: _dt.datetime, max_age_hours: float = MAX_AGE_HOURS
) -> tuple[str, float | None, str]:
    """→ (状态, 距上次成功的小时数, 给人看的一句话)。"""
    if not runs:
        return (
            NO_DATA,
            None,
            (
                f"查不到 {WORKFLOW} 的任何 run。这是**观测无效**，不是「很旧」——"
                f"先确认仓库（{REPO}）、workflow 文件名与 token 权限。"
            ),
        )
    ok_runs = [
        r for r in runs if r.get("conclusion") == "success" and isinstance(r.get("updated_at"), str)
    ]
    if not ok_runs:
        n = len(runs)
        return (
            NO_SUCCESS,
            None,
            (f"{WORKFLOW} 最近 {n} 次 run 里没有一次成功。采集器在跑但没落数据。"),
        )
    # **成功 != 上报。** 演练跑（dry_run=true）会跳过上报那一步却整体成功。
    done = [r for r in ok_runs if r.get("uploaded")]
    if not done:
        return (
            DRY_RUN_ONLY,
            None,
            (
                f"{WORKFLOW} 最近 {len(ok_runs)} 次成功的 run 全是演练"
                f"（`{UPLOAD_STEP}` 那一步没执行），一份快照都没落。"
                f"补跑用：gh workflow run {WORKFLOW} --ref main -f dry_run=false"
            ),
        )
    newest = max(_parse(r["updated_at"]) for r in done)
    age = (now - newest).total_seconds() / 3600
    stamp = newest.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if age > max_age_hours:
        return (
            STALE,
            age,
            (
                f"上一次成功采集是 {stamp}，距今 {age:.1f} 小时，超过阈值 "
                f"{max_age_hours} 小时。GitHub 的 schedule 会整槽丢弃，补跑用："
                f"gh workflow run {WORKFLOW} --ref main -f dry_run=false"
            ),
        )
    return OK, age, f"上一次成功采集 {stamp}，距今 {age:.1f} 小时。"


def _get(url: str, token: str | None) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # **绝不打印响应体**，它可能回显我们发过去的 header
        print(f"::error::查询 GitHub API 失败: HTTP {exc.code}", file=sys.stderr)
        raise SystemExit(2) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"::error::查询 GitHub API 失败: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None


def did_upload(run_id: int, token: str | None) -> bool:
    """这次 run 里，`采集并上报` 那一步真的执行且成功了吗。

    演练跑会把它 skip 掉，而 run 整体仍是 success——只看 run 的 conclusion
    分不出这两者。
    """
    data = _get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=20", token
    )
    for job in data.get("jobs") or []:
        for step in job.get("steps") or []:
            if step.get("name") == UPLOAD_STEP:
                return step.get("conclusion") == "success"
    return False


def fetch_runs(token: str | None, per_page: int = 20) -> list[dict]:
    """run 列表，并给成功的那些标上「到底有没有上报」。

    只对成功的 run 多问一次 jobs，且**从新到旧问到第一个上报过的就停**——
    正常情况下这就是一次额外请求。
    """
    data = _get(
        f"https://api.github.com/repos/{REPO}/actions/workflows/"
        f"{WORKFLOW}/runs?per_page={per_page}",
        token,
    )
    runs = data.get("workflow_runs", [])
    settled = False
    for run in sorted(
        (r for r in runs if r.get("conclusion") == "success"),
        key=lambda r: r.get("updated_at") or "",
        reverse=True,
    ):
        if settled:
            run["uploaded"] = False
            continue
        run["uploaded"] = did_upload(run.get("id"), token)
        settled = run["uploaded"]
    return runs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--runs-json", default=None, help="离线 fixture：GitHub runs 响应")
    ap.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    ap.add_argument("--now", default=None, help="覆盖当前时刻（ISO8601），供测试用")
    args = ap.parse_args(argv)

    if args.runs_json:
        runs = json.loads(open(args.runs_json, encoding="utf-8").read()).get("workflow_runs", [])
    else:
        runs = fetch_runs(os.environ.get("GITHUB_TOKEN"))
    now = _parse(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)

    status, age, msg = evaluate(runs, now, args.max_age_hours)
    if status == OK:
        print(f"* {msg}")
        return 0
    # NO_DATA 与 NO_SUCCESS 也红：它们同样意味着「我们不知道数据还在不在落」
    print(f"::error title=发行量采集陈旧（{status}）::{msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
