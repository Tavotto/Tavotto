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

**`--real --key-file PATH --model NAME [--base-url URL] [--effort minimal|low|medium|high]`**：不用应用里配置的连接，从文件读一把单独的 API key（默认 OpenAI 官方、`openai-responses` 协议）
组一个**临时**连接——不读、不写应用配置与 provider 存储（数据 / 配置目录指到临时目录），不落盘；key 文件权限必须是仅本人可读写（600，比这宽就拒绝），
key 不出现在 stdout / stderr / 报告 / 错误文字里（输出流与报告统一做一遍替换）。`--key-file` 只能和 `--real` 同用，仍然强制 `--max-calls`。

**`--suite roles` 的统计口径**（`--repeats N` 每条用例跑 N 次；每次都用独立的空缓存目录并清负缓存，所以重复跑每次都真的再问一次模型，不读不写应用缓存）：
* **完成** = 模型任务 `state == ready`，且并进建议后的默认横轴 / 纵轴 / 分组与标准答案（或用例的 `also_ok`）三项全部一致。没起任务 / 失败都不算。
* 报：整体完成率、每条用例 N 次里对几次、「N 次全对的用例占比」、不比规则差的比例（没有出现「规则本来对、模型改错」）与「改坏规则本来对的」次数 / 用例（任何用例集都算）。
* 每次调用记耗时（p50 / p95 / 最大值；另给每张表的合计）、请求数、输入 / 输出 / 命中缓存的输入 token、估价、**每张表平均花费**（一次运行 = 一张表）。
* `--cases main|holdout|all`：用例集。`main` 是调提示词时用过的 22 条（已被调优污染，只当回归）；`holdout` 是留出的 24 条（标准答案先写定，不许据它改提示词）；`all` 两者合并。
* `--arm rules` 另输出「规则已经很确定就不问模型」的门（严 / 中 / 松三个版本）：各跳过几条、被跳过的里规则实际错了几条（硬指标：必须 0）。**只测量，没有接进产品。**
* `--real` 的请求数上限不得少于 用例数 × 2 × N。

用法：
    python scripts/dev/data_figure_bench.py [--fake good|trap|mixed] [--wire anthropic|openai-chat|openai-responses] [--only DF-01,DF-02] [--out report.json]
    python scripts/dev/data_figure_bench.py --real --max-calls 28 [--only ...] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

# Windows 上被捕获 / 重定向时中文报告不能静默丢掉：两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
MAX_REQUESTS_PER_CASE = 2
OPENAI_BASE = "https://api.openai.com/v1"
KEY_RE = re.compile(r"[\x21-\x7e]{1,4096}")


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
        "--key-file", default="", help="仅 --real：从这个文件读 API key（600 权限，单行）组临时连接"
    )
    p.add_argument("--model", default="", help="--key-file 的型号（必填）")
    p.add_argument("--base-url", default=OPENAI_BASE, help="--key-file 的地址（默认 OpenAI 官方）")
    p.add_argument(
        "--effort",
        choices=("none", "minimal", "low", "medium", "high"),
        default="",
        help="--key-file 的思考强度（默认 low；不认的型号 / 档位服务端会 400）",
    )
    p.add_argument(
        "--auto-small",
        action="store_true",
        help="仅 --suite roles 且 --real：开「小型号 + 最低档」自动策略（默认关：显式给什么型号 / 强度就用什么）。"
        "与 --effort 互斥（临时连接不带 effort，型号当成「用户选的型号」）",
    )
    p.add_argument(
        "--max-calls", type=int, default=0, help="--real 的实际请求数上限（至少 用例数 × 2）"
    )
    p.add_argument(
        "--fake", choices=("good", "trap", "mixed"), default="good", help="假回答模式（默认模式）"
    )
    p.add_argument("--wire", choices=("anthropic", "openai-chat", "openai-responses"), default=None)
    p.add_argument(
        "--cases",
        choices=("main", "holdout", "all"),
        default="main",
        help="仅 --suite roles：用例集（main 22 条已被调优污染只当回归；holdout 24 条留出集；all 两者合并）",
    )
    p.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="仅 --suite roles：每条用例跑 N 次（每次独立空缓存）；--real 的 --max-calls 至少 用例数 × 2 × N",
    )
    p.add_argument("--only", default="", help="只跑这些用例 id，逗号分隔")
    p.add_argument("--out", default="", help="把 JSON 报告写到这个文件")
    args = p.parse_args(argv)
    if args.repeats < 1 or args.repeats > 50:
        p.error("--repeats 要在 1 到 50 之间")
    if args.suite != "roles" and (args.repeats != 1 or args.cases != "main"):
        p.error("--repeats / --cases 只用于 --suite roles")
    if args.arm == "rules" and (args.real or args.key_file):
        # 在解析任何连接 / 读任何 key 文件之前就拒绝：`--arm rules` 的意思是「不连模型、不花钱」
        p.error("--arm rules 不连模型，不能和 --real / --key-file 同用")
    if args.key_file:
        if not args.real:
            p.error("--key-file 只能和 --real 同用")
        if not args.model:
            p.error("--key-file 要同时给 --model")
        if args.auto_small and args.effort:
            p.error("--auto-small 与 --effort 互斥：自动策略自己定型号与强度")
        args.effort = "" if args.auto_small else (args.effort or "low")
    elif args.model or args.effort or args.base_url != OPENAI_BASE:
        p.error("--model / --base-url / --effort 只在 --key-file 下有意义")
    if args.auto_small and (not args.real or args.suite != "roles"):
        p.error("--auto-small 只用于 --suite roles --real")
    if args.wire is None:
        args.wire = "openai-responses" if args.key_file else "anthropic"
    return args


def read_key_file(path: str) -> tuple[str | None, str]:
    """→ `(key, "")` 或 `(None, 原因)`。原因里没有 key 的任何内容。权限必须是仅本人可读写（POSIX 上 group / other 任何位都拒绝）。"""
    p = Path(path).expanduser()
    try:
        st = p.stat()
    except OSError:
        return None, "读不到 key 文件"
    if not stat.S_ISREG(st.st_mode):
        return None, "key 文件不是普通文件"
    if os.name != "nt" and st.st_mode & 0o077:
        return None, f"key 文件权限太宽（{oct(st.st_mode & 0o777)}），要求 600：先 chmod 600 再来"
    if st.st_size > 4200:
        return None, "key 文件太大，不像单行 key"
    try:
        raw = p.read_bytes().decode("ascii")
    except (OSError, UnicodeDecodeError):
        return None, "key 文件读不了或含非 ASCII 字符"
    key = raw.rstrip("\r\n")
    if not KEY_RE.fullmatch(key):
        return None, "key 文件内容不是单行无空白的 key"
    return key, ""


class _Scrub:
    """包一层输出流：任何写出去的文字里的 key 换成 `***`（即便哪里不小心带出来）。"""

    def __init__(self, stream, secret: str):
        self._stream, self._secret = stream, secret

    def write(self, text):
        return self._stream.write(
            text.replace(self._secret, "***") if isinstance(text, str) else text
        )

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _install_scrub(secret: str) -> None:
    sys.stdout = _Scrub(sys.stdout, secret)
    sys.stderr = _Scrub(sys.stderr, secret)


def _real_connection(args, n_cases: int, label: str, repeats: int = 1):
    """`--real` 的连接：`--key-file` 的临时连接，或应用里第一条 Key 连接。→ `(conn, 退出码)`；退出码非 0 = 已说明原因、没发任何请求。"""
    need = n_cases * MAX_REQUESTS_PER_CASE * repeats
    if args.max_calls < need:
        times = f" × {repeats}" if repeats > 1 else ""
        print(
            f"--real 要显式给 --max-calls，且不少于 用例数 × {MAX_REQUESTS_PER_CASE}{times} = {need}"
            f"（当前 {args.max_calls}）。没有发出任何请求。",
            file=sys.stderr,
        )
        return None, 2
    if args.key_file:
        key, why = read_key_file(args.key_file)
        if key is None:
            print(f"{why}。没有发出任何请求。", file=sys.stderr)
            return None, 2
        from tavotto.engine.agent_runner import wires

        if wires.check_base_url(args.base_url) is not None:
            print("--base-url 不合规（要 https 或本机回环）。没有发出任何请求。", file=sys.stderr)
            return None, 2
        _install_scrub(key)
        conn = {
            "wire": args.wire,
            "base_url": args.base_url,
            "api_key": key,
            "model": args.model,
            "auth": "key",
            "provider_id": "bench-temp",  # 临时连接：不在 provider 存储里
        }
        if args.effort:  # 只有这条临时连接带（且显式给了才带）；`ai_structured._effort_kwargs` 认它，也让自动策略不插手
            conn["effort"] = args.effort
    else:
        from tavotto.engine import ai_structured

        conn, reason, detail = ai_structured.resolve_connection()
        if conn is None:
            print(f"没有可用的连接：{reason}（{detail}）。没有发出任何请求。", file=sys.stderr)
            return None, 2
    print(
        f"真实模式（{label}）：{n_cases} 条用例"
        + (f" × {repeats} 次" if repeats > 1 else "")
        + f"，请求数上限 {args.max_calls}，连接协议 {conn['wire']}、型号 {conn['model']}"
        + (f"、思考强度 {conn['effort']}" if conn.get("effort") else ""),
        file=sys.stderr,
    )
    return conn, 0


def _connection_info(args, conn) -> dict | None:
    if not args.real or conn is None:
        return None
    from urllib.parse import urlsplit

    return {
        "source": "key-file" if args.key_file else "app",
        "wire": conn["wire"],
        "model": conn["model"],
        "effort": conn.get("effort") or None,
        "base_url_host": urlsplit(conn["base_url"]).hostname,
    }


def _write_report(args, report: dict) -> None:
    if not args.out:
        return
    text = json.dumps(report, ensure_ascii=False, indent=1) + "\n"
    if args.key_file:
        key, _ = read_key_file(args.key_file)
        if key:
            text = text.replace(key, "***")
    Path(args.out).write_text(text, encoding="utf-8")


def _load_modules(real: bool, isolate: bool = False):
    """在 import 产品模块之前定隔离：假模式与 `--key-file` 模式把数据 / 配置目录指到临时目录（后者不读用户的应用配置）。"""
    tmp = None
    if not real or isolate:
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
    cases_mod, roles_mod, tmp_iso = _load_modules(args.real, bool(args.key_file))
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
    conn = None
    if args.real:
        conn, rc = _real_connection(args, len(cases), "recipes")
        if rc:
            return rc
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
        "connection": _connection_info(args, conn),
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
        f"请求 {summary['requests']} 次，输入 {summary['input_tokens']} / 输出（含推理）{summary['output_tokens']} token"
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
    _write_report(args, report)
    if tmp_iso is not None:
        tmp_iso.cleanup()
    return 0


def _main_roles(args, D, DR, tmp_iso) -> int:
    """第 1 步「读懂数据」：每条用例（× `--repeats`）一次 propose（规则臂 = 这一次的响应），有连接就同步等模型、并进建议再来一次（模型臂）。
    `--max-calls` 是**实际 HTTP 请求数**的硬上限（经 `ai_data_roles` 的 `request_budget` 覆盖，每次模型调用前核、用完扣）。
    每次运行都用独立的空缓存（`DR.run_case(fresh=True)`）：重复跑不会读到上一次的结果。"""
    cases = DR.load_case_set(args.cases)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"未知用例：{sorted(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    repeats = 1 if args.arm == "rules" else args.repeats  # 规则是确定的，重复没有意义
    conn = None
    if args.real or args.arm == "both":
        if args.real:
            conn, rc = _real_connection(args, len(cases), "roles", repeats)
            if rc:
                return rc
        else:
            conn = D.fake_conn(args.wire)
    runs = []
    budget = [args.max_calls] if args.max_calls else None
    stopped_early = False
    with tempfile.TemporaryDirectory(prefix="data-figure-roles-") as work:
        for case in cases:
            for k in range(repeats):
                if budget is not None and budget[0] < 1:
                    stopped_early = True
                    break
                post = (
                    None
                    if args.real or conn is None
                    else DR.scripted(case, args.wire, answer=DR.fake_answer(case, args.fake, k))
                )
                run = DR.run_case(
                    case,
                    Path(work) / "proj",
                    conn=conn,
                    post=post,
                    request_budget=budget,
                    fresh=True,
                    auto_small=args.auto_small,  # 默认 False：跑分显式给什么型号 / 强度就用什么
                )
                runs.append((k, run))
                a = DR.default_hits(case, run.rules)["all"]
                b = DR.default_hits(case, run.model)["all"] if run.model is not None else None
                tag = f"#{k + 1}" if repeats > 1 else ""
                print(
                    f"{case['id']}{tag}  规则={'对' if a else '错'} 模型={'-' if b is None else ('对' if b else '错')} "
                    f"状态={run.state} 原因={run.reason or '-'} 请求={run.usage.get('requests', 0)}  {case['title']}"
                )
            if stopped_early:
                break
        gate = DR.gate_measure(cases, Path(work)) if args.arm == "rules" else None
    only_runs = [r for _k, r in runs]
    if args.arm == "rules" and args.repeats > 1:
        print("规则臂是确定的：--repeats 在 --arm rules 下只跑一次", file=sys.stderr)
    summary = DR.summarize(only_runs)
    stats = DR.summarize_repeats(only_runs)
    used = summary["requests"]
    report = {
        "suite": "roles",
        "mode": "real" if args.real else ("fake" if conn is not None else "rules-only"),
        "case_set": args.cases,
        "repeats": repeats,
        "connection": _connection_info(args, conn),
        "wire": only_runs[0].usage.get("wire", "") if only_runs else "",
        "model": only_runs[0].usage.get("model", "") if only_runs else "",
        "requests_used": used,
        "max_calls": args.max_calls or None,
        "stopped_early": stopped_early,
        "summary": summary,
        "stats": stats,
        "gate": gate,
        "cases": [
            {
                "id": r.case["id"],
                "attempt": k + 1,
                "state": r.state,
                "reason": r.reason,
                "rules": r.rules,
                "model": r.model,
                "expect": r.case["expect_default"],
                "dropped": r.payload.get("dropped"),
                "usage": r.usage,
                "calls": [
                    {
                        "elapsed_ms": c.elapsed_ms,
                        "requests": c.requests,
                        "input_tokens": c.input_tokens,
                        "output_tokens": c.output_tokens,
                        "cached_input_tokens": c.cached_input_tokens,
                        "ok": c.ok,
                        "reason": c.reason,
                    }
                    for c in r.calls
                ],
            }
            for k, r in runs
        ],
    }
    _print_summary_line(summary, args.arm, used, stopped_early)
    if gate is None:
        _print_stats(stats, args)
    else:
        _print_gate(gate)
    _write_report(args, report)
    if tmp_iso is not None:
        tmp_iso.cleanup()
    return 0


def _print_summary_line(summary: dict, arm: str, used: int, stopped_early: bool) -> None:
    ra, rm = summary["arm_rules"], summary["arm_model"]
    if arm == "rules":
        print(
            f"\n规则单臂（不连模型）：{summary['cases']} 条用例，默认 x/y/分组全对 {ra['all']:.0%}"
            f"（x {ra['x']:.0%}，y {ra['y']:.0%}，分组 {ra['group']:.0%}）"
        )
        return
    print(
        f"\n运行 {summary['cases']} 次：默认 x/y/分组全对 规则 {ra['all']:.0%} → 模型 {rm['all']:.0%}"
        f"（x {ra['x']:.0%}→{rm['x']:.0%}，y {ra['y']:.0%}→{rm['y']:.0%}，分组 {ra['group']:.0%}→{rm['group']:.0%}）；"
        f"对照被改坏 {summary['control_regressions']} 条；"
        + (
            f"列角色正确率 全部 {summary['role_accuracy_all']:.0%} / 关键列 {summary['role_accuracy_critical']:.0%}；"
            if summary["role_accuracy_all"] is not None
            else ""
        )
        + f"表头 {summary['header_ok']}，处理规则 {summary['cells_ok']}；"
        f"请求 {used} 次，输入 {summary['input_tokens']} / 输出（含推理）{summary['output_tokens']} token"
        + (
            f"，花费估算 ${summary['cost_usd']:.4f}"
            if summary["cost_usd"] is not None
            else "，花费：定价表里没有这个端点 / 型号，不估算"
        )
        + ("；用量不是服务端报的，token 为估算" if not summary["usage_reported_all"] else "")
        + ("\n没跑完：请求数上限已用完" if stopped_early else "")
    )


def _fmt_ms(v) -> str:
    return "-" if v is None else f"{v:.0f}"


def _print_stats(st: dict, args) -> None:
    if not st["runs"]:
        return
    lat = st["latency_ms"]
    print(f"\n完成（{st['definition']}）")
    print(
        f"用例集 {args.cases}，{st['cases']} 条 × {st['repeats']} 次 = {st['runs']} 次运行；{st['cache']}"
    )
    print(
        f"完成率 {st['completion_rate']:.1%}（规则单臂 {st['rules_completion_rate']:.1%}）；"
        f"{st['repeats']} 次全对的用例占比 {st['all_runs_correct_share']:.1%}；"
        f"不比规则差 {st['not_worse_than_rules_rate']:.1%}，改坏规则本来对的 {st['rules_broken_runs']} 次"
        + (f"（{', '.join(st['rules_broken_cases'])}）" if st["rules_broken_cases"] else "")
        + f"；没就绪 {st['not_ready_runs']} 次"
    )
    for tier, t in sorted(st["tiers"].items()):
        print(
            f"  {tier}：{t['cases']} 条，完成率 {t['completion_rate']:.1%}（规则本来就对的 {t['rules_ok_cases']} 条）"
        )
    wrong = [
        f"{cid} {r['done']}/{r['runs']}"
        for cid, r in st["per_case"].items()
        if r["done"] < r["runs"]
    ]
    print("没有全对的用例（对几次/共几次）：" + ("、".join(wrong) if wrong else "无"))
    pc, pt = lat["per_call"], lat["per_table"]
    print(
        f"耗时 ms：每次调用 p50 {_fmt_ms(pc['p50'])} / p95 {_fmt_ms(pc['p95'])} / 最大 {_fmt_ms(pc['max'])}（{pc['n']} 次）；"
        f"每张表合计 p50 {_fmt_ms(pt['p50'])} / p95 {_fmt_ms(pt['p95'])} / 最大 {_fmt_ms(pt['max'])}"
    )
    cost = st["cost_per_table_usd"]
    print(
        f"每张表平均 {st['requests_per_table']:.2f} 个请求；输入 {st['input_tokens']}（其中命中缓存 {st['cached_input_tokens']}）/ 输出 {st['output_tokens']} token；"
        + (f"每张表平均花费 ${cost:.5f}" if cost is not None else "每张表平均花费：无法估价")
        + (f"（{st['cost_unknown_tables']} 张表无法估价）" if st["cost_unknown_tables"] else "")
    )


def _print_gate(gate: dict) -> None:
    """一张表：行 = 范围，列 = 版本；每格「跳过 n（占比）/ 其中规则错 m」。m 必须是 0。"""
    vers = list(gate["versions"])
    print(
        "\n「规则已经很确定就不问模型」的门（只测量，未接进产品；规则臂，不连模型）。格内 = 跳过条数（占比）/ 被跳过却规则错的条数（必须 0）"
    )
    print(f"{'范围':16s}{'用例':>5s}{'规则本来对':>11s}" + "".join(f"{v:>18s}" for v in vers))
    for scope, rr in gate["rules_right"].items():
        cells = []
        for v in vers:
            g = gate["versions"][v][scope]
            cells.append(
                f"{g['skipped']}（{g['skipped_share']:.0%}）/ {g['skipped_but_rules_wrong']}".rjust(
                    18
                )
            )
        print(f"{scope:16s}{rr['cases']:>5d}{rr['rules_right']:>11d}" + "".join(cells))
    for v in vers:
        g = gate["versions"][v]["all"]
        print(
            f"  {v} 跳过：{', '.join(g['skipped_ids']) or '无'}"
            + (f"；其中规则错：{', '.join(g['wrong_ids'])}" if g["wrong_ids"] else "")
        )


if __name__ == "__main__":
    sys.exit(main())
