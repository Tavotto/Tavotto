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

* `--classifier JOB --docs-only-skippable A,B,...`（docs-only 快车道，
  `scripts/ci/docs_scope.py`）：**只有**分类 job 自己 `success` **且**它的
  输出 `docs_only == "true"`（取自 `needs[JOB].outputs`，即 `toJSON(needs)`
  里本来就带的证据，不是调用方另外声明的布尔值）时，`A,B,...` 这些 required
  job 才允许 `skipped`；它们不是 skipped 就仍须 success。分类 job 失败 / 被跳过
  / 没输出 / 输出不是字面 `true`，一律回到上面的严格规则。`A,B,...` 必须是
  required 的子集且不含分类 job 自己；其余 required（lint / CLA / 发行说明闸）
  照常必须 success。docs-only 通过时 status 仍是 `success`，reason 是
  `docs_only_fast_path`，summary 会列出被豁免跳过的 job。

* `--e2e-classifier JOB --e2e-legs A,B,...`（PR 改到 e2e 用例，`scripts/ci/docs_scope.py` 的
  `e2e_specs_changed`）：只在 `--allow-deferred`（普通 PR）下有意义。分类 job `success` **且**
  `outputs.e2e_specs_changed == "true"` 时，`A,B,...`（须 ⊆ required）这些 e2e 腿**必须真实 success**
  （它们在这个 PR 上本该跑；skipped 就是失败，等于「该验的没验」），其余 required 则要么全
  skipped（照旧推迟到 merge_group，status `success`、reason `e2e_legs_only`）、要么全 success；
  半跑半跳一律失败。证据不成立（分类失败 / 输出不是字面 `true`）：参数形同没传，走上面的普通
  deferred 规则——此时 e2e 腿若仍 success、其余 skipped 就是「部分跑部分跳」，判失败，不给
  「ci.yml 以为该跑、Gate 以为不该」的缝。与 `--require-heavy` 同用是配置错误（那档本就全必需）。

* merge_group 快速失败（`fail_fast_attribution()`）：重腿红后 workflow 取消其余 job，
  结论里多出 `cancelled`。判红规则不变（非 success 即红），verdict 另给 `root_failures`
  与 `cancelled_by_fail_fast` 供人先看真红的那条；没有 failure 的 cancelled 不归因。

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

# Windows 上 stdout 一旦不是真控制台（被 CI 捕获 / 管道 / 重定向）就退回系统区域
# 编码（cp1252/cp936），第一句中文或 ✓ 的输出就 UnicodeEncodeError——脚本明明
# 做完了却以非零退出，而父进程只看得见「它挂了」。写法与 build_frontend.py 同源。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: GitHub `needs` context 里合法的 job 结论。认不出的值一律按失败处理——
#: GitHub 改了枚举时 Gate 要红着提醒人来对齐，而不是猜。
KNOWN_RESULTS = ("success", "failure", "cancelled", "skipped")

DEFER_REASON = "merge_group_required"


class ConfigError(Exception):
    """输入 / 配置错误。它也让 Gate 失败：判定器自己坏了不能算通过。"""


def parse_needs(raw: str) -> dict[str, str]:
    return parse_needs_full(raw)[0]


def parse_needs_full(raw: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
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
    outputs: dict[str, dict[str, str]] = {}
    for job, entry in data.items():
        if not isinstance(entry, dict) or "result" not in entry:
            raise ConfigError(f"needs['{job}'] 里没有 result 字段")
        out[job] = str(entry["result"])
        raw_outputs = entry.get("outputs")
        if isinstance(raw_outputs, dict):
            outputs[job] = {str(k): str(v) for k, v in raw_outputs.items()}
    return out, outputs


def root_failures_from_run_jobs(run_jobs: list[dict]) -> list[str]:
    """从本 run 各 job 的步骤结论里找真红的腿（纯函数）。

    快速失败步骤取消的是**整个** run，红腿自己也被取消，`needs.<id>.result` 于是是
    `cancelled` 而不是 `failure`——只看 needs 找不到真红的那条（#1655 合入前第一个红组
    实测）。步骤结论不受取消影响：红腿里那一步仍是 `failure`。输入是 Actions API
    `runs/{id}/attempts/{n}/jobs` 每个 job 的 `{name, failed_steps}`；Gate 自己不算。
    返回「job 显示名 · 失败步骤」，排序去重。
    """
    out = set()
    for job in run_jobs:
        name = str(job.get("name") or "")
        if not name or (name.startswith("CI ") and name.endswith(" gate")):
            continue
        for step in job.get("failed_steps") or []:
            out.add(f"{name} · {step}")
    return sorted(out)


def fail_fast_attribution(
    event: str, jobs: dict[str, str], run_jobs: list[dict] | None = None
) -> dict:
    """merge_group 快速失败的归因（纯函数）：谁是真红、谁是被收尾取消的。

    重腿红了之后 workflow 会取消本 run 的其余 job（ci.yml 各重腿最后一步），它们的结论
    于是是 `cancelled`——**红腿自己也是**（取消的是整个 run）。所以 merge_group 上优先用
    `run_jobs`（各 job 的失败步骤，见 `root_failures_from_run_jobs`）找真红；取不到时退回
    needs 里的 failure。判红本身不依赖这个归因——任何非 success 都已经是红；这里只回答
    「该先看哪条腿」。**找不到任何真红时不归因给快速失败**（那是外部取消，保持裸
    cancelled，L-114 的核对口径照旧）。
    """
    failed = sorted(j for j, r in jobs.items() if r == "failure")
    if event == "merge_group" and run_jobs:
        failed = root_failures_from_run_jobs(run_jobs) or failed
    cancelled = sorted(j for j, r in jobs.items() if r == "cancelled")
    fast = event == "merge_group" and bool(failed)
    return {
        "root_failures": failed,
        "cancelled_by_fail_fast": cancelled if fast else [],
    }


def decide(
    mode: str,
    event: str,
    required: list[str],
    results: dict[str, str],
    *,
    require_heavy: bool = False,
    allow_deferred: bool = False,
    full_ci: bool = False,
    outputs: dict[str, dict[str, str]] | None = None,
    classifier: str | None = None,
    docs_only_skippable: list[str] | None = None,
    run_jobs: list[dict] | None = None,
    e2e_classifier: str | None = None,
    e2e_legs: list[str] | None = None,
) -> dict:
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
                "--allow-deferred 之一——不显式选，就是把判定交给巧合"
            )
    else:
        if require_heavy or allow_deferred:
            raise ConfigError(f"--mode {mode} 不接受 heavy/deferred 开关")
    if allow_deferred and event == "merge_group":
        raise ConfigError("merge_group 上不允许 deferred——那是完整资格的唯一执行点")
    if allow_deferred and full_ci:
        raise ConfigError("full-ci PR 不允许 deferred——标签的意义就是在 PR 上提前跑全套")

    skippable = list(docs_only_skippable or [])
    if skippable and not classifier:
        raise ConfigError("--docs-only-skippable 必须同时给 --classifier")
    if classifier and not skippable:
        raise ConfigError("--classifier 必须同时给 --docs-only-skippable")
    if classifier and classifier in skippable:
        raise ConfigError("分类 job 自己不能出现在可跳过集合里——它是这条豁免的证据")
    not_required = [j for j in skippable if j not in required]
    if not_required:
        raise ConfigError(f"--docs-only-skippable 里有不在 required 的 job：{not_required}")

    e2e = list(e2e_legs or [])
    if e2e and not e2e_classifier:
        raise ConfigError("--e2e-legs 必须同时给 --e2e-classifier")
    if e2e_classifier and not e2e:
        raise ConfigError("--e2e-classifier 必须同时给 --e2e-legs")
    if e2e:
        if mode != "integration" or not allow_deferred:
            raise ConfigError("--e2e-legs 只用于 integration 的 --allow-deferred（普通 PR）")
        if e2e_classifier in e2e:
            raise ConfigError("e2e 分类 job 自己不能出现在 e2e 腿集合里——它是证据")
        not_required = [j for j in e2e if j not in required]
        if not_required:
            raise ConfigError(f"--e2e-legs 里有不在 required 的 job：{not_required}")

    # ---- 闭集校验：缺一个、多一个都是失败 ----
    problems: list[str] = []
    missing = [j for j in required if j not in results]
    #: 分类 job 本身可以出现在 needs 里而不在 required 里（integration gate 就是这样），
    #: 但出现了就必须 success——它是豁免的证据，证据自己坏了不能当没看见。
    allowed_extra = {c for c in (classifier, e2e_classifier) if c and c not in required}
    unexpected = sorted(set(results) - set(required) - allowed_extra)
    for j in sorted(allowed_extra & set(results)):
        if results[j] != "success":
            problems.append(f"classifier: `{j}` 的结论是 `{results[j]}`，不是 success")
    for j in missing:
        problems.append(f"missing: required job `{j}` 不在 needs 里")
    for j in unexpected:
        problems.append(
            f"unexpected: job `{j}` 在 needs 里却不在 --required 里——"
            f"两处必须同步，否则它的失败不会被 Gate 看见"
        )
    for j, r in sorted(results.items()):
        if r not in KNOWN_RESULTS:
            problems.append(f"unknown: job `{j}` 的结论 `{r}` 认不出")

    jobs = {j: results.get(j, "missing") for j in required}
    for j in unexpected:
        jobs[j] = results[j]

    base = {"gate": mode, "event": event, "jobs": jobs}

    if problems:
        return {**base, "status": "failure", "reason": "contract_violation", "problems": problems}

    docs_only = bool(
        classifier
        and results.get(classifier) == "success"
        and (outputs or {}).get(classifier, {}).get("docs_only") == "true"
    )
    if docs_only:
        bad = [
            f"{j}: {results[j]}"
            for j in required
            if results[j] != "success" and not (results[j] == "skipped" and j in skippable)
        ]
        if bad:
            return {**base, "status": "failure", "reason": "upstream_not_success", "problems": bad}
        skipped = [j for j in skippable if results[j] == "skipped"]
        return {
            **base,
            "status": "success",
            "reason": "docs_only_fast_path",
            "docs_only_skipped": skipped,
            "problems": [],
        }

    e2e_evidence = bool(
        e2e
        and results.get(e2e_classifier) == "success"
        and (outputs or {}).get(e2e_classifier, {}).get("e2e_specs_changed") == "true"
    )
    if e2e_evidence:
        bad = [f"{j}: {results[j]}" for j in e2e if results[j] != "success"]
        rest = [j for j in required if j not in e2e]
        rest_results = {results[j] for j in rest}
        if rest_results not in (set(), {"skipped"}, {"success"}):
            bad += [f"{j}: {results[j]}（其余重型须全 skipped 或全 success）" for j in rest]
        if bad:
            return {
                **base,
                "status": "failure",
                "reason": "upstream_not_success",
                "problems": bad,
                **fail_fast_attribution(event, jobs, run_jobs),
            }
        if rest_results == {"skipped"} or not rest:
            return {
                **base,
                "status": "success",
                "reason": "e2e_legs_only",
                "e2e_legs": e2e,
                "problems": [],
            }
        return {**base, "status": "success", "reason": "all_required_success", "problems": []}

    req_results = [results[j] for j in required]

    if allow_deferred and all(r == "skipped" for r in req_results):
        return {**base, "status": "deferred", "reason": DEFER_REASON, "problems": []}

    bad = [f"{j}: {results[j]}" for j in required if results[j] != "success"]
    if bad:
        return {
            **base,
            "status": "failure",
            "reason": "upstream_not_success",
            "problems": bad,
            **fail_fast_attribution(event, jobs, run_jobs),
        }
    return {**base, "status": "success", "reason": "all_required_success", "problems": []}


def render_summary(verdict: dict, required: list[str]) -> str:
    icon = {"success": "✅", "deferred": "⏭️", "failure": "❌"}[verdict["status"]]
    lines = [f"## {icon} {verdict['gate']} gate — {verdict['status']}", ""]
    if verdict["status"] == "deferred":
        lines += [
            "**Full integration qualification deferred to merge_group.**",
            "",
            "完整合并资格（跨平台打包 / 真产物冒烟）没有在这个 PR 的",
            "commit 上执行；它会在进入 Merge Queue 后、对最终组合提交",
            "真实运行。这个 Gate 的绿不代表重型验证通过。",
            "",
        ]
    lines += ["| job | result |", "|---|---|"]
    for j in sorted(verdict["jobs"]):
        lines.append(f"| `{j}` | {verdict['jobs'][j]} |")
    if verdict.get("docs_only_skipped"):
        lines += [
            "",
            "docs-only 快车道：本次只改了 `docs/release-notes/`（分类 job 已核对），",
            "下列重型 job 按设计被跳过："
            + "、".join(f"`{j}`" for j in verdict["docs_only_skipped"]),
        ]
    if verdict.get("e2e_legs"):
        lines += [
            "",
            "本 PR 改到了 e2e 用例 / 配置（分类 job 已核对）：下列 e2e 腿已在 PR 上真实通过："
            + "、".join(f"`{j}`" for j in verdict["e2e_legs"]),
            "其余重型资格仍推迟到 merge_group。",
        ]
    if verdict.get("cancelled_by_fail_fast"):
        lines += [
            "",
            "merge_group 快速失败：先看真红的 "
            + "、".join(f"`{j}`" for j in verdict.get("root_failures", []))
            + "；下列 job 是被收尾取消的（不是各自的问题）："
            + "、".join(f"`{j}`" for j in verdict["cancelled_by_fail_fast"]),
        ]
    if verdict.get("problems"):
        lines += ["", "问题："] + [f"- {p}" for p in verdict["problems"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--mode", required=True, choices=("fast", "integration", "codeql"))
    ap.add_argument(
        "--event",
        required=True,
        help="github.event_name（pull_request / merge_group / push / schedule）",
    )
    ap.add_argument(
        "--required", required=True, help="逗号分隔的上游 job id 闭集，必须与 Gate 的 needs 一致"
    )
    ap.add_argument("--needs-json", required=True, help="workflow 里 `toJSON(needs)` 的输出")
    ap.add_argument(
        "--require-heavy",
        action="store_true",
        help="integration：重型 job 必须真实 success（merge_group / full-ci / push）",
    )
    ap.add_argument(
        "--allow-deferred",
        action="store_true",
        help="integration：普通 PR 上允许整体 skipped → deferred",
    )
    ap.add_argument(
        "--full-ci",
        action="store_true",
        help="PR 带 full-ci 标签——与 --allow-deferred 互斥，脚本会复核",
    )
    ap.add_argument(
        "--classifier",
        help="docs-only 分类 job 的 id；其 success 且 outputs.docs_only == 'true' 是跳过豁免的唯一证据",
    )
    ap.add_argument(
        "--docs-only-skippable",
        default="",
        help="逗号分隔：docs-only 证据成立时允许 skipped 的 required job（须 ⊆ required）",
    )
    ap.add_argument(
        "--e2e-classifier",
        help="分类 job 的 id；其 success 且 outputs.e2e_specs_changed == 'true' 是 e2e 腿必须在 PR 上跑的证据",
    )
    ap.add_argument(
        "--e2e-legs",
        default="",
        help="逗号分隔：e2e 证据成立时必须真实 success 的 required job（须 ⊆ required；仅普通 PR）",
    )
    args = ap.parse_args(argv)

    required = [s.strip() for s in args.required.split(",") if s.strip()]
    try:
        results, outputs = parse_needs_full(args.needs_json)
        run_jobs = _read_run_jobs(os.environ.get("RUN_JOBS_FILE"))
        verdict = decide(
            args.mode,
            args.event,
            required,
            results,
            require_heavy=args.require_heavy,
            allow_deferred=args.allow_deferred,
            full_ci=args.full_ci,
            outputs=outputs,
            classifier=args.classifier,
            docs_only_skippable=[
                s.strip() for s in args.docs_only_skippable.split(",") if s.strip()
            ],
            run_jobs=run_jobs,
            e2e_classifier=args.e2e_classifier,
            e2e_legs=[s.strip() for s in args.e2e_legs.split(",") if s.strip()],
        )
    except ConfigError as exc:
        verdict = {
            "gate": args.mode,
            "event": args.event,
            "status": "failure",
            "reason": "config_error",
            "problems": [str(exc)],
            "jobs": {},
        }
        _emit(verdict, required)
        return 2

    _emit(verdict, required)
    return 0 if verdict["status"] in ("success", "deferred") else 1


def _read_run_jobs(path: str | None) -> list[dict] | None:
    """读 `RUN_JOBS_FILE`（每行一个 `{name, failed_steps}` 的 JSON）。只用于归因：
    文件不在、读不了、某行坏了都退回 None / 跳过那一行，**不改变结论**。

    经环境变量而不是命令行参数传入：判定器取自默认分支，新参数在它合入前会让旧判定器
    argparse 报错（bootstrap），环境变量旧判定器只会忽略。
    """
    if not path:
        return None
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out or None


def _emit(verdict: dict, required: list[str]) -> None:
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(render_summary(verdict, required))
        except OSError as exc:  # summary 写不进不改变结论
            print(f"::warning::写不进 GITHUB_STEP_SUMMARY：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
