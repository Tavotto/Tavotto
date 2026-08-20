#!/usr/bin/env python3
"""长时 soak：反复走真实用户路径，盯资源泄漏与状态污染。

**不是** `for 1000: GET /api/version`——那种循环唯一能证明的是 HTTP 服务器还活着。
真正会泄漏的是渲染路径：worker 子进程、常驻 figure、SVG 缓存、导出的临时文件。
所以每一轮都做用户真会做的那串动作：

    渲染 → 改参数再渲染 → 导出 → 换个面板 → 再来

启动/请求/孤儿判定全部**复用既有实现**（`scripts/smoke_app.py`），patch 靶子的
挑选复用 `scripts/bench_render.py`。刻意不另写一套 fake protocol：两份实现迟早
分叉，而分叉那天 soak 测的就已经不是产品的真实路径了。

泄漏判据分两类，严格程度不同：

* **孤儿进程 —— 硬失败**。跑完之后属于本次运行的 worker / workerd 一个都不该剩。
  归属靠**本次隔离数据目录出现在命令行里**判定，不是 `pgrep tavotto`：同一台机器上
  维护者自己开着的实例与本次运行毫无关系，误报一次，这条提示下次就被无视了。
* **FD / RSS 趋势 —— 看斜率，不看终值**。Python 的分配器有高水位，要求
  「结束 RSS == 初始 RSS」只会得到一条恒红的门禁。这里把 warmup 之后的样本
  做线性拟合，只在**持续单向增长**且幅度可观时才判定为泄漏。

用法：
    python scripts/ci/soak.py --iterations 100 --python .venv/bin/python
    python scripts/ci/soak.py --iterations 500 --json soak-metrics.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))          # scripts/ ——复用既有冒烟与基线工具

from _common import (  # noqa: E402
    CiError, ensure_layout, run_metadata, summary, summary_table, write_report,
)
# 这两个模块是本仓库既有的真实用户路径实现。复用它们而不是重写，是为了让
# soak 跑的就是用户跑的那条路——CLAUDE.md 里「别再造第二个权威」的同一条纪律。
import smoke_app as SA  # noqa: E402
import bench_render as BR  # noqa: E402

REPO = _HERE.parents[1]
DEFAULT_FIGURES = REPO / "examples" / "figures"
RENDER_TIMEOUT = 600


# ---------------------------------------------------------------- 资源采样
def _proc_children(pid: int) -> list[int]:
    """进程树（含自身）。只在 Linux 上完整；别的平台返回 [pid]。"""
    out = [pid]
    task = Path(f"/proc/{pid}/task")
    if not task.is_dir():
        return out
    frontier = [pid]
    seen = {pid}
    while frontier:
        cur = frontier.pop()
        for t in Path(f"/proc/{cur}/task").glob("*/children"):
            try:
                kids = [int(x) for x in t.read_text().split()]
            except (OSError, ValueError):
                continue
            for k in kids:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
                    frontier.append(k)
    return out


def _rss_kib(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _threads(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Threads:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _fd_count(pid: int) -> int:
    try:
        return len(list(Path(f"/proc/{pid}/fd").iterdir()))
    except OSError:
        return 0


def sample(pid: int) -> dict:
    """整棵进程树的资源快照。

    只看父进程是不够的：Tavotto 的渲染跑在 worker 子进程里，泄漏最可能出现在
    「worker 起了没回收」而不是 Flask 父进程自己涨。
    """
    tree = _proc_children(pid)
    return {
        "processes": len(tree),
        "rss_kib": sum(_rss_kib(p) for p in tree),
        "fds": sum(_fd_count(p) for p in tree),
        "threads": sum(_threads(p) for p in tree),
    }


def _supported() -> bool:
    """有没有 /proc。没有就只跑功能不判泄漏，并在报告里如实标注。"""
    return Path("/proc").is_dir()


# ---------------------------------------------------------------- 趋势判定
def _slope(xs: list[float], ys: list[float]) -> float:
    """最小二乘斜率。样本不足或全平时回 0。"""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def analyse(samples: list[dict], warmup: int) -> dict:
    """从时间序列里判断有没有泄漏。

    **看斜率不看终值**：Python 的分配器有高水位，头几轮把科学栈 import 进来
    之后 RSS 必然比初始高一大截，那不是泄漏。真正的泄漏长成「每一轮都比上
    一轮高一点」的持续单向增长，所以丢掉 warmup 之后做线性拟合。
    """
    usable = samples[warmup:]
    if len(usable) < 3:
        return {"verdict": "inconclusive", "reason": f"warmup 之后只剩 {len(usable)} 个样本，不足以判趋势"}

    xs = [float(s["iteration"]) for s in usable]
    fd_slope = _slope(xs, [float(s["fds"]) for s in usable])
    rss_slope = _slope(xs, [float(s["rss_kib"]) for s in usable])
    span = xs[-1] - xs[0] or 1.0

    fd_growth = fd_slope * span          # 整段区间里 FD 净增多少
    rss_growth_mib = rss_slope * span / 1024

    findings: list[str] = []
    # 阈值刻意宽松：第一阶段的目标是抓住「明显泄漏」，不是抓抖动。
    # 每轮稳定多占 1 个 FD，100 轮就是 +100——那是真泄漏；+5 是噪声。
    if fd_growth > 40:
        findings.append(f"FD 在 {int(span)} 轮里净增约 {fd_growth:.0f} 个（斜率 {fd_slope:.3f}/轮）")
    if rss_growth_mib > 300:
        findings.append(f"RSS 在 {int(span)} 轮里净增约 {rss_growth_mib:.0f} MiB（斜率 {rss_slope / 1024:.2f} MiB/轮）")

    proc_counts = [s["processes"] for s in usable]
    if proc_counts and max(proc_counts) - min(proc_counts) > 8:
        findings.append(f"进程数波动 {min(proc_counts)}→{max(proc_counts)}，可能有 worker 没回收")

    return {
        "verdict": "leak" if findings else "ok",
        "findings": findings,
        "fd_slope_per_iter": round(fd_slope, 4),
        "rss_slope_kib_per_iter": round(rss_slope, 2),
        "fd_growth_over_span": round(fd_growth, 1),
        "rss_growth_mib_over_span": round(rss_growth_mib, 1),
        "warmup_skipped": warmup,
        "samples_used": len(usable),
    }


# ---------------------------------------------------------------- 主流程
def run_soak(launch: list[str], figures: Path, workdir: Path, iterations: int,
             sample_every: int, warmup: int) -> dict:
    port = SA._free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = workdir / "data"
    config_dir = workdir / "config"
    for d in (data_dir, config_dir):
        d.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TAVOTTO_DATA_DIR": str(data_dir),
        "TAVOTTO_CONFIG_DIR": str(config_dir),
        "HOME": str(workdir / "home"),
        "USERPROFILE": str(workdir / "home"),
        "APPDATA": str(workdir / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(workdir / "AppData" / "Local"),
        "TAVOTTO_NO_UPDATE_CHECK": "1",
        "TAVOTTO_ALLOW_SHUTDOWN": "1",
    }
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)

    cmd = [*launch, "--port", str(port), "--no-browser", "--figures", str(figures)]
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")

    samples: list[dict] = []
    errors: list[dict] = []
    ops = 0
    started = time.time()
    try:
        SA._wait_ready(base, proc, SA.BOOT_TIMEOUT_S)
        panels = SA._get(f"{base}/api/panels")["panels"]
        scripted = [p for p in panels if p.get("script")]
        if not scripted:
            raise CiError("no_scripted_panel",
                          "示例项目里没有可参数化面板，soak 无从施力（注册表为空？）")

        # 先渲一次拿 manifest，才知道该拿哪个属性当 patch 靶子
        first = SA._post(f"{base}/api/engine/render",
                         {"id": scripted[0]["id"], "patches": []}, timeout=RENDER_TIMEOUT)
        patch = BR._pick_patch(first.get("manifest") or {})
        ops += 1
        print(f"靶子属性: {patch['prop'] if patch else '(无，走空 patch 列表)'}")

        for i in range(iterations):
            target = scripted[i % len(scripted)]
            t0 = time.time()
            try:
                # 1) 改参数渲染——这是用户拖滑块时真正发生的事
                res = SA._post(f"{base}/api/engine/render",
                               {"id": target["id"], "patches": BR._variant(patch, i)},
                               timeout=RENDER_TIMEOUT)
                if not res.get("manifest"):
                    raise CiError("render_no_manifest", f"第 {i} 轮渲染没回 manifest")
                ops += 1

                # 2) 每 5 轮导出一次：导出走 worker 全质量出图 + PyMuPDF 合成，
                #    是最容易留下临时文件与句柄的一条路径。
                if i % 5 == 0:
                    spec = {
                        "page_w_mm": 80, "page_h_mm": 40, "formats": ["pdf"],
                        "stem": f"soak-{i}",
                        "objects": [{"type": "panel", "id": target["id"],
                                     "x_mm": 5, "y_mm": 5, "w_mm": 60, "h_mm": 30}],
                    }
                    SA._post(f"{base}/api/export", spec, timeout=RENDER_TIMEOUT)
                    ops += 1
            except Exception as exc:                      # noqa: BLE001 - 逐轮记录不中断
                errors.append({"iteration": i, "error": str(exc)[:300]})
                if len(errors) > max(5, iterations // 10):
                    raise CiError("too_many_errors",
                                  f"soak 第 {i} 轮：错误累计 {len(errors)} 次，提前中止") from exc

            if i % sample_every == 0 or i == iterations - 1:
                snap = sample(proc.pid)
                snap.update({"iteration": i, "latency_s": round(time.time() - t0, 3),
                             "elapsed_s": round(time.time() - started, 1)})
                samples.append(snap)
                if i % (sample_every * 10) == 0:
                    print(f"  [{i:4d}/{iterations}] rss={snap['rss_kib'] // 1024}MiB "
                          f"fd={snap['fds']} proc={snap['processes']} "
                          f"{snap['latency_s']:.2f}s", flush=True)

        # 干净退出：验的是关得掉，不是杀得死
        try:
            SA._post(f"{base}/api/shutdown", {}, timeout=60)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            errors.append({"iteration": -1, "error": "应用没有在 120s 内干净退出，已强杀"})
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)

    # 孤儿判定放在进程真的收掉之后
    time.sleep(2)
    orphans = SA._leftover_workers(data_dir)

    analysis = analyse(samples, warmup) if _supported() else {
        "verdict": "unsupported",
        "reason": "本平台没有 /proc，只跑了功能不判资源趋势",
    }
    return {
        "ok": not orphans and not errors and analysis["verdict"] in ("ok", "unsupported", "inconclusive"),
        "iterations": iterations,
        "operations": ops,
        "errors": errors,
        "orphans": orphans,
        "samples": samples,
        "analysis": analysis,
        "elapsed_s": round(time.time() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tavotto soak / 泄漏检测")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--python", default=sys.executable, help="用 `-m tavotto` 启动")
    g.add_argument("--exe", default=None, help="打包产物")
    ap.add_argument("--figures", default=str(DEFAULT_FIGURES))
    ap.add_argument("--iterations", type=int, default=100)
    ap.add_argument("--sample-every", type=int, default=1)
    ap.add_argument("--warmup", type=int, default=5,
                    help="前 N 个样本不参与趋势判定（科学栈 import 的高水位不是泄漏）")
    ap.add_argument("--json", default=None, help="把 metrics 写到这个文件")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)

    root = ensure_layout()
    workdir = Path(tempfile.mkdtemp(prefix="artifact-soak-", dir=str(root / "tmp")))
    launch = [args.exe] if args.exe else [args.python, "-m", "tavotto"]

    try:
        result = run_soak(launch, Path(args.figures), workdir, args.iterations,
                          args.sample_every, args.warmup)
    except CiError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        summary(f"\n> **soak 失败** `{exc.code}` — {exc.message}\n")
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    result["metadata"] = run_metadata()
    write_report("soak.json", result, root)
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    a = result["analysis"]
    rows = [
        ("操作数", "✅" if not result["errors"] else "❌",
         f"{result['operations']} 次 / {result['iterations']} 轮，错误 {len(result['errors'])}"),
        ("孤儿进程", "✅" if not result["orphans"] else "❌",
         "0" if not result["orphans"] else f"{len(result['orphans'])} 个：{result['orphans'][:2]}"),
        ("FD 趋势", "✅" if a["verdict"] != "leak" else "❌",
         f"{a.get('fd_growth_over_span', '—')} 个 / 全程" if "fd_growth_over_span" in a else a.get("reason", "")),
        ("RSS 趋势", "✅" if a["verdict"] != "leak" else "❌",
         f"{a.get('rss_growth_mib_over_span', '—')} MiB / 全程" if "rss_growth_mib_over_span" in a else ""),
    ]
    summary(f"\n### Soak · {result['iterations']} 轮 / {result['elapsed_s']}s\n\n"
            + summary_table(rows))
    for line in a.get("findings", []):
        print(f"::warning::泄漏疑似 — {line}")
    for e in result["errors"][:10]:
        print(f"::error::soak 第 {e['iteration']} 轮：{e['error']}", file=sys.stderr)

    print(f"\nsoak: {'通过' if result['ok'] else '失败'} "
          f"（{result['operations']} 次操作，{result['elapsed_s']}s）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
