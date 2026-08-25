#!/usr/bin/env python3
"""发布编排的 release-blocker 门禁：明知有洞不许无声发布。

背景：issue #35 早把「真实 N-1 更新」列为 1.0 退出条件——系统**知道**这条
路从未验证——却带着这个 open 的洞连发了四个 0.x 版本，没有任何机制在发版
时把它摆到眼前。这与 issue #78（「声明了却从未执行的 workflow job」）是
同一族：洞活在 issue 里而不是 YAML 里。

规则（**不是禁止发**——0.x 需要灵活；是把「明知有洞还发」从默认无声变成
显式签字）：

* open 且带 `release:blocker` label 的 issue 清单由发布编排在 trust 阶段
  现查（`gh api`），逐条列进 job summary；
* 没有 open blocker：直接放行，ack 必须为空（残留的 ack 是上一次发版
  复制来的陈词，留着会在下一个 blocker 出现时被误当成签字）；
* 有 open blocker：`workflow_dispatch` 的 `ack_open_blockers` 输入必须与
  当前 open 清单**逐条对得上**（两个方向都不许差——防止一次 ack 永久生效，
  与 CompatBench expected_false_reasons 具体到条目的纪律同源）；
* tag push 带不了输入：有 open blocker 时直接红，提示要么先关掉 blocker、
  要么改用 workflow_dispatch(ref=<tag>, publish=true, ack_open_blockers=…)
  显式签字后发布。

输入是 `gh api repos/<repo>/issues?labels=release:blocker&state=open` 的
原样 JSON——注意 GitHub 的 issues 端点会把 PR 也混进来（带 `pull_request`
键），这里要滤掉：PR 不是「已知未修的洞」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LABEL = "release:blocker"


def parse_ack(raw: str) -> set[int]:
    """解析 `ack_open_blockers` 输入：逗号分隔的 issue 编号，容忍空白与 #。"""
    out: set[int] = set()
    for token in raw.replace("，", ",").split(","):
        token = token.strip().lstrip("#")
        if not token:
            continue
        if not token.isdigit():
            raise SystemExit(f"ack_open_blockers 里读不懂的条目：{token!r}（要 issue 编号）")
        out.add(int(token))
    return out


def open_blockers(issues: list[dict]) -> dict[int, str]:
    """{编号: 标题}——只留真正 open 的 issue，滤掉 PR。"""
    found: dict[int, str] = {}
    for item in issues:
        if "pull_request" in item:
            continue
        if item.get("state") != "open":
            continue
        found[int(item["number"])] = str(item.get("title", ""))
    return found


def check(issues: list[dict], ack_raw: str, event: str) -> tuple[bool, list[str]]:
    """返回 (放行?, 给人看的行)。判定逻辑集中在这里，pytest 直接打它。"""
    blockers = open_blockers(issues)
    ack = parse_ack(ack_raw)
    lines: list[str] = []

    if not blockers:
        if ack:
            lines.append(
                f"当前没有 open 的 `{LABEL}`，但 ack_open_blockers 填了 {sorted(ack)}——"
                "陈旧的 ack 会在下一个 blocker 出现时被误当成签字，请清空后重跑。")
            return False, lines
        lines.append(f"✅ 没有 open 的 `{LABEL}` issue。")
        return True, lines

    lines.append(f"⚠️ 当前 open 的 `{LABEL}` issue：")
    for num in sorted(blockers):
        lines.append(f"  - #{num} {blockers[num]}")

    missing = sorted(set(blockers) - ack)
    stale = sorted(ack - set(blockers))
    if not missing and not stale:
        lines.append(f"已逐条显式签字（ack_open_blockers = {sorted(ack)}），放行。")
        lines.append("签字的含义：**明知这些洞存在仍决定发布**，责任在签字那次 dispatch。")
        return True, lines

    if missing:
        lines.append(f"❌ 未签字的 blocker：{['#%d' % n for n in missing]}")
    if stale:
        lines.append(
            f"❌ 签了但不在当前 open 清单里的：{['#%d' % n for n in stale]}"
            "（已关闭或编号写错——ack 必须与当前清单逐条对得上，防止一次 ack 永久生效）")
    if event == "push":
        lines.append(
            "tag 触发带不了输入。两条路：① 先处理掉上面的 blocker 再发；"
            "② 用 workflow_dispatch(ref=<本 tag>, publish=true, "
            "ack_open_blockers=\"<逐条编号>\") 显式签字后发布——tag 已经存在时"
            "trust 会核对它指向同一个 commit，发布结果与 tag 触发完全一致。")
    else:
        lines.append(
            "在 workflow_dispatch 的 ack_open_blockers 里逐条填上当前 open 的编号"
            "（如 \"35,83\"）表示明知有洞仍要发布，或先把 blocker 处理掉。")
    return False, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--issues-json", required=True, type=Path,
                    help="gh api …/issues?labels=release:blocker&state=open 的输出")
    ap.add_argument("--ack", default="", help="workflow_dispatch 的 ack_open_blockers 输入")
    ap.add_argument("--event", default="workflow_dispatch", help="github.event_name")
    args = ap.parse_args(argv)

    issues = json.loads(args.issues_json.read_text(encoding="utf-8"))
    ok, lines = check(issues, args.ack, args.event)
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Release-blocker 门禁\n\n" + text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
