#!/usr/bin/env python3
"""资源趋势分析：读 long_probe.py 的 samples.jsonl，丢掉预热段后对每个指标做最小二乘斜率。

判据（与 scripts/ci/soak.py 的口径一致——看斜率不看终值，分配器高水位不是泄漏）：
  * fds / threads / processes / workers：预热后 max − min ≤ 2 且斜率 × 跨度 ≤ 2 → 平稳；
  * rss：斜率 × 跨度 ≤ 50 MiB 且后三分之一的中位数不高于前三分之一中位数的 1.25 倍 → 平稳；
  * cache_bytes：只报数（app.log 轮转有上限；engine 目录另看 summary）；
  * tmp_files：预热后恒为 0 → 平稳。
不是门禁，只给台账报数；阈值写在这里是为了让「平稳」这个词有可复核的定义。

用法：python analyze_samples.py <run 目录> [预热样本数，默认 5]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def slope(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def main() -> int:
    run = Path(sys.argv[1])
    warm = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    rows = [json.loads(x) for x in (run / "samples.jsonl").read_text().splitlines() if x.strip()]
    use = rows[warm:]
    out = {"run": run.name, "samples_total": len(rows), "samples_used": len(use), "metrics": {}}
    if len(use) < 3:
        out["verdict"] = "inconclusive"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    t0 = use[0]["t"]
    xs = [(r["t"] - t0) / 3600.0 for r in use]  # 小时
    span_h = xs[-1] - xs[0] or 1e-9
    verdicts = {}
    for k in (
        "rss_kib",
        "rss_server_kib",
        "fds",
        "fds_server",
        "threads",
        "threads_server",
        "processes",
        "workers",
        "tmp_files",
        "cache_bytes",
        "export_bytes",
    ):
        ys = [float(r.get(k, 0)) for r in use]
        s = slope(xs, ys)
        third = max(1, len(ys) // 3)
        m = {
            "first": ys[0],
            "last": ys[-1],
            "min": min(ys),
            "max": max(ys),
            "slope_per_hour": round(s, 2),
            "growth_over_span": round(s * span_h, 1),
            "median_first_third": statistics.median(ys[:third]),
            "median_last_third": statistics.median(ys[-third:]),
        }
        out["metrics"][k] = m
        if k in ("fds", "threads", "processes", "workers", "fds_server", "threads_server"):
            verdicts[k] = (
                "stable"
                if (m["max"] - m["min"] <= 2 and abs(m["growth_over_span"]) <= 2)
                else "drift"
            )
        elif k in ("rss_kib", "rss_server_kib"):
            ok = (
                m["growth_over_span"] <= 50 * 1024
                and m["median_last_third"] <= 1.25 * m["median_first_third"]
            )
            verdicts[k] = "stable" if ok else "drift"
        elif k == "tmp_files":
            verdicts[k] = "stable" if m["max"] == 0 else "drift"
    out["span_hours"] = round(span_h, 3)
    out["verdicts"] = verdicts
    out["verdict"] = "stable" if all(v == "stable" for v in verdicts.values()) else "drift"
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
