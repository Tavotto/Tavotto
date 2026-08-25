#!/usr/bin/env python3
"""稳定 Gate 的聚合判定：把一组上游 job 的结论收敛成一个明确的成功 / 失败。

    python scripts/ci/aggregate_gate.py \
        --mode fast \
        --event pull_request \
        --required invariants,backend,frontend,workerd,compat-smoke \
        --needs-json "$NEEDS_JSON"

为什么要有这个脚本（而不是在 workflow 里写几行 Bash）：

* ruleset 最终只依赖三个固定名字的 Gate（`CI fast gate` / `CI integration
  gate` / `CodeQL gate`）。Gate 是 `if: always()` 的聚合 job，它的判定逻辑
  就是合并资格本身——这段逻辑散在 Bash 里没法单测，而「Gate 判错」的代价
  是坏组合静默进 main 或者好组合被无端拦下，两头都不便宜。
* 「skipped 的必需检查算不算通过」是随 GitHub 行为变化的脆行为。这里把
  每一种上游结论（success / failure / cancelled / skipped / 缺失 / 认不出）
  都显式判掉，Gate 永远产出明确结论，绝不靠「上游被跳过所以碰巧绿了」。

判定规则（详见 `decide()`）：

* `--mode fast` / `--mode codeql`：所有 required job 必须 success，任何
  其他状态（含 skipped）都是失败。
* `--mode integration --require-heavy`：同上——merge_group、push、带
  `full-ci` 标签的 PR 走这档，重型资格必须真实通过。
* `--mode integration --allow-deferred`：普通 PR 走这档。**全部** required
  job 都 skipped 时判 deferred（Gate 结论是成功，但 summary 与 JSON 明确
  写出「完整资格验证推迟到 merge_group」）；只要有任何一个跑过，就按
  真实结果判——部分跑部分跳一律失败，不给「半套资格」留缝。
* `--allow-deferred` 在 merge_group 事件或 `--full-ci` 下直接拒绝
  （配置错误 = Gate 失败）：merge_group 与 full-ci **永远不许 deferred**。
* required 集合是闭集：needs JSON 里缺一个是失败，多一个也是失败——
  多出来的那个说明有人往 Gate 的 `needs` 加了 job 却没同步 `--required`，
  静默放过它就是在 Gate 上开洞。

退出码：0 = success 或 deferred；1 = Gate 失败；2 = 配置 / 输入错误
（同样让 Gate 红——判定器自己坏了不能算通过）。

stdout 恒输出一行机器可读 JSON；`$GITHUB_STEP_SUMMARY` 存在时另写
人类可读的 Markdown。纯标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

#: GitHub `needs` context 里合法的 job 结论。认不出的值一律按失败处理——
#: GitHub 改了枚举时 Gate 要红着提醒人来对齐，而不是猜。
KNOWN_RESULTS = ("success", "failure", "cancelled", "skipped")

DEFER_REASON = "merge_group_required"


class ConfigError(Exception):
    """输入 / 配置错误。它也让 Gate 失败：判定器自己坏了不能算通过。"""


def parse_needs(raw: str) -> dict[str, str]:
    """把 `toJSON(needs)` 的输出解析成 {job_id: result}。

    形状必须严格：不是 dict、条目缺 `result`，都按配置错误抛——一个安静地
    解析出空表的 Gate 会把「上游全没跑」判成任何东西。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"needs JSON 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"needs JSON 不是对象：{type(data).__name__}")
    out: dict[str, str] = {}
    for job, entry in data.items():
        if not isinstance(entry, dict) or "result" not in entry:
            raise ConfigError(f"needs['{job}'] 里没有 result 字段")
        out[job] = str(entry["result"])
    return out


def decide(mode: str, event: str, required: list[str], results: dict[str, str],
           *, require_heavy: bool = False, allow_deferred: bool = False,
           full_ci: bool = False) -> dict:
    """核心判定。返回 {"status": "success"|"failure"|"deferred", ...}。

    只做纯计算，不碰环境——所有 I/O 留在 main() 里，单测才测得动每一格。
    """
    # ---- 配置合法性：非法组合是 Gate 失败，不是静默取某个默认值 ----
    if not required:
        raise ConfigError("required 集合是空的——一个什么都不看的 Gate 不是 Gate")
    if mode == "integration":
        if require_heavy == allow_deferred:
            raise ConfigError(
                "--mode integration 必须且只能带 --require-heavy 或 "
                "--allow-deferred 之一——不显式选，就是把判定交给巧合")
    else:
        if require_heavy or allow_deferred:
            raise ConfigError(f"--mode {mode} 不接受 heavy/deferred 开关")
    if allow_deferred and event == "merge_group":
        raise ConfigError("merge_group 上不允许 deferred——那是完整资格的唯一执行点")
    if allow_deferred and full_ci:
        raise ConfigError("full-ci PR 不允许 deferred——标签的意义就是在 PR 上提前跑全套")

    # ---- 闭集校验：缺一个、多一个都是失败 ----
    problems: list[str] = []
    missing = [j for j in required if j not in results]
    unexpected = sorted(set(results) - set(required))
    for j in missing:
        problems.append(f"missing: required job `{j}` 不在 needs 里")
    for j in unexpected:
        problems.append(
            f"unexpected: job `{j}` 在 needs 里却不在 --required 里——"
            f"两处必须同步，否则它的失败不会被 Gate 看见")
    for j, r in sorted(results.items()):
        if r not in KNOWN_RESULTS:
            problems.append(f"unknown: job `{j}` 的结论 `{r}` 认不出")

    jobs = {j: results.get(j, "missing") for j in required}
    for j in unexpected:
        jobs[j] = results[j]

    base = {"gate": mode, "event": event, "jobs": jobs}

    if problems:
        return {**base, "status": "failure", "reason": "contract_violation",
                "problems": problems}

    req_results = [results[j] for j in required]

    if allow_deferred and all(r == "skipped" for r in req_results):
        return {**base, "status": "deferred", "reason": DEFER_REASON,
                "problems": []}

    bad = [f"{j}: {results[j]}" for j in required if results[j] != "success"]
    if bad:
        return {**base, "status": "failure", "reason": "upstream_not_success",
                "problems": bad}
    return {**base, "status": "success", "reason": "all_required_success",
            "problems": []}


def render_summary(verdict: dict, required: list[str]) -> str:
    icon = {"success": "✅", "deferred": "⏭️", "failure": "❌"}[verdict["status"]]
    lines = [f"## {icon} {verdict['gate']} gate — {verdict['status']}", ""]
    if verdict["status"] == "deferred":
        lines += ["**Full integration qualification deferred to merge_group.**",
                  "",
                  "完整合并资格（跨平台打包 / 真产物冒烟）没有在这个 PR 的",
                  "commit 上执行；它会在进入 Merge Queue 后、对最终组合提交",
                  "真实运行。这个 Gate 的绿不代表重型验证通过。", ""]
    lines += ["| job | result |", "|---|---|"]
    for j in sorted(verdict["jobs"]):
        lines.append(f"| `{j}` | {verdict['jobs'][j]} |")
    if verdict.get("problems"):
        lines += ["", "问题："] + [f"- {p}" for p in verdict["problems"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--mode", required=True, choices=("fast", "integration", "codeql"))
    ap.add_argument("--event", required=True,
                    help="github.event_name（pull_request / merge_group / push / schedule）")
    ap.add_argument("--required", required=True,
                    help="逗号分隔的上游 job id 闭集，必须与 Gate 的 needs 一致")
    ap.add_argument("--needs-json", required=True,
                    help="workflow 里 `toJSON(needs)` 的输出")
    ap.add_argument("--require-heavy", action="store_true",
                    help="integration：重型 job 必须真实 success（merge_group / full-ci / push）")
    ap.add_argument("--allow-deferred", action="store_true",
                    help="integration：普通 PR 上允许整体 skipped → deferred")
    ap.add_argument("--full-ci", action="store_true",
                    help="PR 带 full-ci 标签——与 --allow-deferred 互斥，脚本会复核")
    args = ap.parse_args(argv)

    required = [s.strip() for s in args.required.split(",") if s.strip()]
    try:
        results = parse_needs(args.needs_json)
        verdict = decide(args.mode, args.event, required, results,
                         require_heavy=args.require_heavy,
                         allow_deferred=args.allow_deferred,
                         full_ci=args.full_ci)
    except ConfigError as exc:
        verdict = {"gate": args.mode, "event": args.event, "status": "failure",
                   "reason": "config_error", "problems": [str(exc)], "jobs": {}}
        _emit(verdict, required)
        return 2

    _emit(verdict, required)
    return 0 if verdict["status"] in ("success", "deferred") else 1


def _emit(verdict: dict, required: list[str]) -> None:
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(render_summary(verdict, required))
        except OSError as exc:                        # summary 写不进不改变结论
            print(f"::warning::写不进 GITHUB_STEP_SUMMARY：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
