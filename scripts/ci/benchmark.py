#!/usr/bin/env python3
"""性能回归门禁：在固定机器上跑基线，与历史比较。

**测量本身复用 `scripts/bench_render.py`**——那份脚本已经把「怎么量才有意义」
解决过一遍了（真实 HTTP 链路而不是直接 import 引擎、冷热分开、取中位数而不是
均值、每次数据目录全新）。这里只补它没有的那一半：**把数字存下来，并和上一次比**。

固定机器是这条门禁成立的前提，也是它最脆弱的地方：

* **先查污染再测量**。别人的构建正占着 16 个核时量出来的数字毫无意义。
  把「机器忙」误报成「Tavotto 变慢了」，比不量还糟——它会让人去优化一个
  根本不存在的回归。查出来就明确报 `environment_contaminated`，
  **不静默接受一份没意义的 benchmark**。
* **候选版永不覆盖基线**。release 只比不写；只有 main 模式跑绿之后才滚动更新。
  否则「和基线比」会退化成「和自己比」。
* **基线带完整元数据**（SHA / CPU / Python / 时间戳）。换过机器或换过解释器
  之后的数字与上一版不可比，没有元数据就无法事后判断那次回归是真是假。

阈值第一阶段刻意宽松（中位数劣化 > 25%）。目标是抓住「明显变慢了」，
不是抓抖动——等积累几周数据之后再收紧。

用法：
    python scripts/ci/benchmark.py --python .venv/bin/python --mode main
    python scripts/ci/benchmark.py --python .venv/bin/python --mode release --no-update
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from _common import (  # noqa: E402
    CiError, ensure_layout, run_metadata, state_root, summary, summary_table,
    write_report,
)

REPO = _HERE.parents[1]
DEFAULT_FIGURES = REPO / "examples" / "figures"
# 第一阶段的门槛。宽松是有意的：先证明这条链路能稳定产出可比数字，
# 再谈收紧。收紧之前请先看 baselines/perf/ 里累积的历史波动。
REGRESSION_PCT = 25.0
# 环境污染判据。load average 用「每核负载」而不是绝对值，16 核机器上
# load=4 是空闲，4 核机器上 load=4 已经满了。
MAX_LOAD_PER_CPU = 0.35
MIN_FREE_RAM_GIB = 4.0


# ---------------------------------------------------------------- 环境体检
def _load_avg() -> float:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):
        return 0.0


def _free_ram_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return -1.0


def check_environment() -> tuple[bool, dict]:
    """机器现在适不适合量性能。"""
    cpus = os.cpu_count() or 1
    load = _load_avg()
    free = _free_ram_gib()
    per_cpu = load / cpus
    facts = {"load_avg_1m": round(load, 2), "cpu_count": cpus,
             "load_per_cpu": round(per_cpu, 3), "free_ram_gib": free}
    reasons = []
    if per_cpu > MAX_LOAD_PER_CPU:
        reasons.append(f"每核负载 {per_cpu:.2f} > {MAX_LOAD_PER_CPU}（load {load:.1f} / {cpus} 核）")
    if 0 <= free < MIN_FREE_RAM_GIB:
        reasons.append(f"可用内存仅 {free} GiB < {MIN_FREE_RAM_GIB}")
    facts["reasons"] = reasons
    return (not reasons), facts


# ---------------------------------------------------------------- 测量
def measure(python: str, figures: Path, repeat: int, out_json: Path) -> dict:
    """跑 bench_render.py，拿它的原始 JSON。

    只跑 **Python 池** 一条控制面（`--plane python`）：workerd 那条也值得看，
    但两条一起跑会让墙钟翻倍，而回归判定只需要一条稳定的参照系。
    要对照两条控制面时用 bench_render 自己跑，那是它本来的用途。
    """
    cmd = [python, str(REPO / "scripts" / "bench_render.py"),
           "--python", python, "--figures", str(figures),
           "--repeat", str(repeat), "--plane", "python",
           "--json", str(out_json)]
    print(f"$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5400)
    if r.returncode != 0:
        raise CiError("bench_failed",
                      f"bench_render.py 退出码 {r.returncode}：\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    try:
        return json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CiError("bench_no_json", f"读不到 bench_render 的 JSON：{exc}") from exc


def extract_metrics(raw: dict) -> dict[str, float]:
    """把 bench_render 的原始行压成「指标名 → 毫秒」。

    只取**总时长**这一层：分项（script_build / patch_apply / canvas_draw）
    在排查时有用，但拿它们做门禁会让噪声源翻好几倍。
    """
    out: dict[str, float] = {}
    for row in raw.get("rows", []):
        pid = row.get("id", "?")
        cold = row.get("cold") or {}
        hot = row.get("hot") or {}
        if row.get("really_cold") and isinstance(cold.get("total_ms"), (int, float)):
            out[f"{pid}::cold_total_ms"] = float(cold["total_ms"])
        if isinstance(hot.get("total_ms"), (int, float)):
            out[f"{pid}::hot_total_ms"] = float(hot["total_ms"])
        if isinstance(row.get("export_wall_ms"), (int, float)) and row.get("export_ok"):
            out[f"{pid}::export_ms"] = float(row["export_wall_ms"])
    return out


# ---------------------------------------------------------------- 基线
def baseline_path(root: Path) -> Path:
    return root / "baselines" / "perf" / "rolling.json"


def load_baseline(root: Path) -> dict | None:
    p = baseline_path(root)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_baseline(root: Path, payload: dict) -> Path:
    """原子写，并留一份上一代。

    留旧的那份不是洁癖：基线一旦被一次异常的测量污染，没有上一代就只能
    靠人回忆「上周大概是多少」。
    """
    dest = baseline_path(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        shutil.copy2(dest, dest.with_suffix(".previous.json"))
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def compare(current: dict[str, float], baseline: dict | None) -> tuple[bool, list[dict]]:
    """逐指标比较。返回 (有没有回归, 明细)。"""
    if not baseline or not baseline.get("metrics"):
        return True, []
    base = baseline["metrics"]
    findings: list[dict] = []
    regressed = False
    for key, now in sorted(current.items()):
        was = base.get(key)
        if not isinstance(was, (int, float)) or was <= 0:
            findings.append({"metric": key, "now_ms": round(now, 1), "baseline_ms": None,
                             "delta_pct": None, "verdict": "new"})
            continue
        delta = (now - was) / was * 100.0
        bad = delta > REGRESSION_PCT
        regressed = regressed or bad
        findings.append({"metric": key, "now_ms": round(now, 1), "baseline_ms": round(was, 1),
                         "delta_pct": round(delta, 1),
                         "verdict": "regression" if bad else ("faster" if delta < -10 else "ok")})
    return (not regressed), findings


# ---------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="性能回归门禁")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--repeat", type=int, default=7, help="热渲染采样次数（含 warmup）")
    ap.add_argument("--mode", default="main",
                    choices=["main", "nightly", "release", "weekly"])
    ap.add_argument("--no-update", action="store_true",
                    help="只比不写。release 模式下强制生效")
    ap.add_argument("--gate", default=None,
                    help="true/false 覆盖 LAB_PERF_GATE；不传时读环境变量，默认不阻断")
    ap.add_argument("--allow-contaminated", action="store_true",
                    help="机器忙时仍然测量（数字仅供参考，不参与门禁）")
    args = ap.parse_args(argv)

    root = ensure_layout()
    gate_raw = args.gate if args.gate is not None else os.environ.get("LAB_PERF_GATE", "false")
    gate = str(gate_raw).lower() == "true"

    clean, env_facts = check_environment()
    if not clean and not args.allow_contaminated:
        msg = "；".join(env_facts["reasons"])
        print(f"::error::benchmark 环境被污染：{msg}", file=sys.stderr)
        summary(f"\n> **benchmark 环境被污染** — {msg}\n>\n"
                f"> 这台机器现在的数字没有参考价值。**不接受一份无意义的 benchmark**——"
                f"把「机器忙」当成「Tavotto 变慢了」会让人去优化一个不存在的回归。\n")
        write_report("benchmark.json",
                     {"ok": False, "code": "environment_contaminated",
                      "environment": env_facts, "metadata": run_metadata(args.mode)}, root)
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="artifact-bench-", dir=str(root / "tmp")))
    try:
        raw = measure(args.python, Path(args.figures), args.repeat, tmp / "raw.json")
        metrics = extract_metrics(raw)
        if not metrics:
            raise CiError("no_metrics",
                          "bench_render 没产出任何可比指标（示例项目里没有可参数化面板？）")
    except CiError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        summary(f"\n> **benchmark 失败** `{exc.code}` — {exc.message}\n")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    baseline = load_baseline(root)
    ok, findings = compare(metrics, baseline)

    # 候选版永不写基线。写了的话「和基线比」就变成「和自己比」，
    # 这条门禁从此再也不会红。
    may_update = (args.mode == "main") and not args.no_update and ok
    updated = False
    if may_update:
        save_baseline(root, {
            "metrics": metrics,
            "metadata": run_metadata(args.mode),
            "environment": env_facts,
            "regression_pct_threshold": REGRESSION_PCT,
        })
        updated = True

    payload = {
        "ok": ok,
        "gate_enforced": gate,
        "mode": args.mode,
        "environment": env_facts,
        "environment_clean": clean,
        "baseline_sha": (baseline or {}).get("metadata", {}).get("sha", ""),
        "baseline_timestamp": (baseline or {}).get("metadata", {}).get("timestamp", ""),
        "baseline_updated": updated,
        "metrics": metrics,
        "findings": findings,
        "metadata": run_metadata(args.mode),
    }
    write_report("benchmark.json", payload, root)

    if baseline is None:
        summary("\n### 性能基线\n\n> 尚无历史基线，本次只记录不比较。"
                "（基线在 main 模式跑绿后滚动建立）\n")
        print("尚无历史基线，已记录本次结果" + ("并写入基线" if updated else ""))
        if args.mode == "main" and not args.no_update:
            save_baseline(root, {"metrics": metrics, "metadata": run_metadata(args.mode),
                                 "environment": env_facts,
                                 "regression_pct_threshold": REGRESSION_PCT})
            print("已建立首个基线")
        return 0

    rows = []
    for f in findings:
        mark = {"regression": "❌", "faster": "🚀", "ok": "✅", "new": "🆕"}[f["verdict"]]
        delta = "—" if f["delta_pct"] is None else f"{f['delta_pct']:+.1f}%"
        rows.append((f["metric"], mark,
                     f"{f['now_ms']} ms（基线 {f['baseline_ms']} ms，{delta}）"))
    summary(f"\n### 性能对比 · 基线 `{payload['baseline_sha'][:8] or '?'}`"
            f"{'（本次已更新基线）' if updated else ''}\n\n" + summary_table(rows))

    regressions = [f for f in findings if f["verdict"] == "regression"]
    for f in regressions:
        print(f"::warning::性能回归 {f['metric']}: {f['now_ms']} ms vs 基线 {f['baseline_ms']} ms "
              f"（{f['delta_pct']:+.1f}%）")

    if regressions and not gate:
        # 报告但不阻断：初期先收集数据，`LAB_PERF_GATE=true` 之后再变硬门禁。
        summary(f"\n> 检测到 {len(regressions)} 项性能回归，但 `LAB_PERF_GATE` 未开启，"
                f"本次不阻断。\n")
        print(f"\n性能：{len(regressions)} 项回归（未开启门禁，不阻断）")
        return 0
    print(f"\n性能：{'通过' if ok else f'{len(regressions)} 项回归'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
