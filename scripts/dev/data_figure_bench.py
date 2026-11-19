"""「从数据到图」模型调用的固定用例集跑分（ADR 0178 修订 2026-11-19）。开发侧工具，不进产品。

对 `tests/fixtures/data_figure_cases/cases.json` 的每条用例：真的 `profile_folder` → `ai_data_model.propose_recipes`（真的协议层、真的闭集校验与排序护栏）→ 评分
（第一名命中率、任一命中率、违规率、退回规则结果的比例、丢弃的配方数、请求数、token 与花费估算）。

两种模式：

* **默认（假回答，不联网、不花钱）**：`--fake good|trap|mixed` 用用例里录好的回答（`reference` / 踩坑的 `trap`）经假传输跑通整条链，用来自测评分与报告；
  `--wire` 选假回答走哪种协议的回复体。数据目录与配置目录指到临时目录，不碰用户的任何配置。
* **`--real`（真模型，要花钱）**：用你当前连着的 API 连接（连接视图里第一个内置执行器能用的，与 AI 修复同一口径；订阅登录 Codex / Claude Code 暂不支持）。
  必须同时给 `--max-calls N`（**实际 HTTP 请求数**上限，在传输层数——换协议 / 字段降级的恢复请求也算；每条用例至少 1 次、回复不是合法 JSON 时重试一次，
  所以开跑前要求 N ≥ 用例数 × 2 作为下限核）；运行中到上限就不再发，并在报告里记「没跑完」（`stopped_early`），最后一条可能因此被截成 `over_budget`。**读你本机的连接配置，可能刷新账号登录令牌；用例的表格摘要会发给该连接的服务商。**
  开跑前自己先和维护者报预算——真实跑分花的是你的额度。

用法：
    python scripts/dev/data_figure_bench.py [--fake good|trap|mixed] [--wire anthropic|openai-chat|openai-responses] [--only DF-01,DF-02] [--out report.json]
    python scripts/dev/data_figure_bench.py --real --max-calls 28 [--only ...] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Windows 上被捕获 / 重定向时中文报告不能静默丢掉：两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
MAX_REQUESTS_PER_CASE = 2


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--suite",
        choices=("recipes", "roles"),
        default="recipes",
        help="recipes = 配方排序用例（第 0 步）；roles = 读懂数据用例（第 1 步，规则 / 规则+模型两臂）",
    )
    p.add_argument(
        "--arm",
        choices=("rules", "both"),
        default="both",
        help="仅 --suite roles：rules = 只跑规则（不连模型、不花钱）；both = 规则 + 模型两臂（默认；规则臂就是同一次请求里的第一份响应）",
    )
    p.add_argument("--real", action="store_true", help="对真模型跑（花钱）；必须同时给 --max-calls")
    p.add_argument(
        "--max-calls", type=int, default=0, help="--real 的实际请求数上限（至少 用例数 × 2）"
    )
    p.add_argument(
        "--fake", choices=("good", "trap", "mixed"), default="good", help="假回答模式（默认模式）"
    )
    p.add_argument(
        "--wire", choices=("anthropic", "openai-chat", "openai-responses"), default="anthropic"
    )
    p.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    p.add_argument("--out", default="", help="把 JSON 报告写到这个文件")
    return p.parse_args(argv)


def _load_modules(real: bool):
    """在 import 产品模块之前定隔离（假模式）：数据 / 配置目录指到临时目录。"""
    tmp = None
    if not real:
        tmp = tempfile.TemporaryDirectory(prefix="data-figure-bench-")
        os.environ["TAVOTTO_DATA_DIR"] = str(Path(tmp.name) / "data")
        os.environ["TAVOTTO_CONFIG_DIR"] = str(Path(tmp.name) / "config")
        os.environ["TAVOTTO_NO_TELEMETRY"] = "1"
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests" / "support"))
    import data_figure_cases as cases_mod
    import data_figure_roles as roles_mod

    return cases_mod, roles_mod, tmp


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    cases_mod, roles_mod, tmp_iso = _load_modules(args.real)
    D = cases_mod
    if args.suite == "roles":
        return _main_roles(args, D, roles_mod, tmp_iso)
    cases = D.load_cases()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"未知用例：{sorted(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    if args.real:
        need = len(cases) * MAX_REQUESTS_PER_CASE
        if args.max_calls < need:
            print(
                f"--real 要显式给 --max-calls，且不少于 用例数 × {MAX_REQUESTS_PER_CASE} = {need}"
                f"（当前 {args.max_calls}）。没有发出任何请求。",
                file=sys.stderr,
            )
            return 2
        from tavotto.engine import ai_structured

        conn, reason, detail = ai_structured.resolve_connection()
        if conn is None:
            print(f"没有可用的连接：{reason}（{detail}）。没有发出任何请求。", file=sys.stderr)
            return 2
        print(
            f"真实模式：{len(cases)} 条用例，请求数上限 {args.max_calls}，连接协议 {conn['wire']}、型号 {conn['model']}",
            file=sys.stderr,
        )
    runs = []
    used = 0
    stopped_early = False
    with tempfile.TemporaryDirectory(prefix="data-figure-cases-") as work:
        for i, case in enumerate(cases):
            # 上限按**实际 HTTP 请求数**硬性生效（传输层数；换协议 / 字段降级的恢复请求也算），到顶就不再发
            remaining = args.max_calls - used if args.max_calls else None
            if remaining is not None and remaining < 1:
                stopped_early = True
                break
            if args.real:
                run = D.run_case(case, Path(work), conn=conn, max_requests=remaining)
            else:
                mode = args.fake if args.fake != "mixed" else ("good", "trap")[i % 2]
                transport = D.scripted_for(case, args.wire, mode)
                run = D.run_case(
                    case,
                    Path(work),
                    conn=D.fake_conn(args.wire),
                    post=transport,
                    max_requests=remaining,
                )
            used += run.result.call.requests
            runs.append(run)
            if run.result.call.reason == "over_budget" and run.result.call.detail == "requests":
                stopped_early = True
                break
            s = run.score
            print(
                f"{case['id']}  {s.outcome:12s} top1={int(s.top1)} 违规={s.violations} "
                f"丢弃={run.result.dropped_count} 原因={run.result.call.reason or '-'}  {case['title']}"
            )
    summary = D.summarize(runs)
    report = {
        "mode": "real" if args.real else f"fake:{args.fake}",
        "wire": runs[0].result.call.wire if runs else "",
        "model": runs[0].result.call.model if runs else "",
        "requests_used": used,
        "max_calls": args.max_calls or None,
        "stopped_early": stopped_early,
        "summary": summary,
        "cases": [
            {
                "id": x.case["id"],
                "outcome": x.score.outcome,
                "top1": x.score.top1,
                "any_hit": x.score.any_hit,
                "violations": x.score.violations,
                "dropped": [{"index": d.index, "codes": list(d.codes)} for d in x.result.dropped],
                "reason": x.result.call.reason,
                "detail": x.result.call.detail,
                "attempts": x.result.call.attempts,
                "requests": x.result.call.requests,
                "input_tokens": x.result.call.input_tokens,
                "output_tokens": x.result.call.output_tokens,
                "cost_usd": x.result.call.cost_usd,
            }
            for x in runs
        ],
    }
    print(
        f"\n用例 {summary['cases']}：第一名命中 {summary['top1_rate']:.0%}，任一命中 {summary['any_hit_rate']:.0%}，"
        f"违规 {summary['violation_rate']:.0%}，退回规则结果 {summary['fallback_rate']:.0%}；"
        f"请求 {summary['requests']} 次，输入 {summary['input_tokens']} / 输出 {summary['output_tokens']} token"
        + (
            f"，花费估算 ${summary['cost_usd']:.4f}"
            if summary["cost_usd"] is not None
            else "，花费：定价表里没有这个端点 / 型号，不估算"
        )
        + (
            f"（其中 {summary['cost_unknown_calls']} 次无法估价）"
            if summary["cost_usd"] is not None and summary["cost_unknown_calls"]
            else ""
        )
        + ("；用量不是服务端报的，token 为估算" if not summary["usage_reported_all"] else "")
        + ("\n没跑完：下一条用例会超过请求数上限" if stopped_early else "")
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    if tmp_iso is not None:
        tmp_iso.cleanup()
    return 0


def _main_roles(args, D, DR, tmp_iso) -> int:
    """第 1 步「读懂数据」：每条用例一次 propose（规则臂 = 这一次的响应），有连接就同步等模型、并进建议再来一次（模型臂）。
    `--max-calls` 是**实际 HTTP 请求数**的硬上限（经 `ai_data_roles` 的 `request_budget` 覆盖，每次模型调用前核、用完扣）。"""
    cases = DR.load_role_cases()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"未知用例：{sorted(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    conn = None
    if args.real or args.arm == "both":
        if args.real:
            need = len(cases) * MAX_REQUESTS_PER_CASE
            if args.max_calls < need:
                print(
                    f"--real 要显式给 --max-calls，且不少于 用例数 × {MAX_REQUESTS_PER_CASE} = {need}"
                    f"（当前 {args.max_calls}）。没有发出任何请求。",
                    file=sys.stderr,
                )
                return 2
            from tavotto.engine import ai_structured

            conn, reason, detail = ai_structured.resolve_connection()
            if conn is None:
                print(f"没有可用的连接：{reason}（{detail}）。没有发出任何请求。", file=sys.stderr)
                return 2
            print(
                f"真实模式（roles）：{len(cases)} 条用例，请求数上限 {args.max_calls}，连接协议 {conn['wire']}、型号 {conn['model']}",
                file=sys.stderr,
            )
        else:
            conn = D.fake_conn(args.wire)
    runs = []
    budget = [args.max_calls] if args.max_calls else None
    stopped_early = False
    with tempfile.TemporaryDirectory(prefix="data-figure-roles-") as work:
        for case in cases:
            if budget is not None and budget[0] < 1:
                stopped_early = True
                break
            post = None if args.real or conn is None else DR.scripted(case, args.wire)
            run = DR.run_case(
                case, Path(work) / "proj", conn=conn, post=post, request_budget=budget
            )
            runs.append(run)
            a = DR.default_hits(case, run.rules)["all"]
            b = DR.default_hits(case, run.model)["all"] if run.model is not None else None
            print(
                f"{case['id']}  规则={'对' if a else '错'} 模型={'-' if b is None else ('对' if b else '错')} "
                f"状态={run.state} 原因={run.reason or '-'} 请求={run.usage.get('requests', 0)}  {case['title']}"
            )
    summary = DR.summarize(runs)
    used = summary["requests"]
    report = {
        "suite": "roles",
        "mode": "real" if args.real else ("fake" if conn is not None else "rules-only"),
        "wire": runs[0].usage.get("wire", "") if runs else "",
        "model": runs[0].usage.get("model", "") if runs else "",
        "requests_used": used,
        "max_calls": args.max_calls or None,
        "stopped_early": stopped_early,
        "summary": summary,
        "cases": [
            {
                "id": r.case["id"],
                "state": r.state,
                "reason": r.reason,
                "rules": r.rules,
                "model": r.model,
                "expect": r.case["expect_default"],
                "dropped": r.payload.get("dropped"),
                "usage": r.usage,
            }
            for r in runs
        ],
    }
    ra, rm = summary["arm_rules"], summary["arm_model"]
    print(
        f"\n用例 {summary['cases']}：默认 x/y/分组全对 规则 {ra['all']:.0%} → 模型 {rm['all']:.0%}"
        f"（x {ra['x']:.0%}→{rm['x']:.0%}，y {ra['y']:.0%}→{rm['y']:.0%}，分组 {ra['group']:.0%}→{rm['group']:.0%}）；"
        f"对照被改坏 {summary['control_regressions']} 条；"
        + (
            f"列角色正确率 全部 {summary['role_accuracy_all']:.0%} / 关键列 {summary['role_accuracy_critical']:.0%}；"
            if summary["role_accuracy_all"] is not None
            else ""
        )
        + f"表头 {summary['header_ok']}，处理规则 {summary['cells_ok']}；"
        f"请求 {used} 次，输入 {summary['input_tokens']} / 输出 {summary['output_tokens']} token"
        + (
            f"，花费估算 ${summary['cost_usd']:.4f}"
            if summary["cost_usd"] is not None
            else "，花费：定价表里没有这个端点 / 型号，不估算"
        )
        + ("；用量不是服务端报的，token 为估算" if not summary["usage_reported_all"] else "")
        + ("\n没跑完：请求数上限已用完" if stopped_early else "")
    )
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    if tmp_iso is not None:
        tmp_iso.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
