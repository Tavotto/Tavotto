#!/usr/bin/env python3
"""RenderCore 候选后端的针对性 benchmark（ADR 0077）：两棵源码树（或两个后端）各起进程、ABBA 交错。

    <rc-venv>/bin/python scripts/dev/bench_rendercore.py --src ~/某个装着 PDF 的目录 \\
        --tree main=/path/to/main/src --tree new=/path/to/branch/src --rounds 4

输入是**任意一个装着 PDF 的目录**（作者用的是本机的真实 PDF，不入库）：探测扫描用全部 PDF；画布导出与预览命中
用体积最大的三份（大内容流 / 大图片是差异所在）。每一轮每棵树一个全新进程（`PYTHONPATH` 指向那棵树的 src，
并核对 import 到的确实是它——zsh 不拆词的循环曾让两轮都跑在同一棵树上）；结果是各项的中位数。
不写仓库、不碰数据目录以外的地方；需要批准字体（`TAVOTTO_FONTS_DIR` 或包内 resources/fonts）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ONE_ROUND = r"""
import json, os, statistics, sys, tempfile, time
from pathlib import Path
os.environ["TAVOTTO_RENDER_BACKEND"] = "rendercore"
src_root, expect = Path(sys.argv[1]), sys.argv[2]
import tavotto
assert tavotto.__file__.startswith(expect), (tavotto.__file__, expect)
pdfs = sorted(src_root.glob("*.pdf"))
big = sorted(pdfs, key=lambda p: -p.stat().st_size)[:3]
tmp = Path(tempfile.mkdtemp())
rec = {}
from tavotto import pdfbackend as pb
pb.warm()
time.sleep(0.5)
c = pb.compose(174, 106)
c.place({"type": "panel", "id": "a", "x_mm": 3, "y_mm": 14, "w_mm": 55, "h_mm": 41.8}, 300, lambda o, d: pdfs[0])
c.place({"type": "text", "id": "t", "text": "图 2 温度 / °C", "x_mm": 5, "y_mm": 3, "w_mm": 160, "h_mm": 8,
         "size_pt": 10, "font_family": "sans-serif", "color": "#000000"}, 300, None)
t = time.perf_counter(); c.save_pdf(tmp / "c.pdf"); c.save_png(tmp / "c.png", 300)
rec["first_export_0.5s_after_warm"] = (time.perf_counter() - t) * 1000
c.close()
def med(fn, n=7):
    fn(); xs = []
    for _ in range(n):
        t = time.perf_counter(); fn(); xs.append(time.perf_counter() - t)
    return statistics.median(xs) * 1000
t = time.perf_counter()
for p in pdfs:
    pb.probe_asset(p, "pdf")
rec[f"scan_{len(pdfs)}_first"] = (time.perf_counter() - t) * 1000
rec[f"scan_{len(pdfs)}_repeat"] = med(lambda: [pb.probe_asset(p, "pdf") for p in pdfs])
def canvas(p, png=None):
    def fn():
        c = pb.compose(130, 170)
        c.place({"type": "panel", "id": "x", "x_mm": 5, "y_mm": 5, "w_mm": 120, "h_mm": 160}, png or 300, lambda o, d: p)
        c.save_pdf(tmp / "o.pdf")
        if png:
            c.save_png(tmp / "o.png", png)
        c.close()
    return fn
from tavotto.rendercore import facade, renderhost
pc = facade.preview_cache(tmp / "pcache")
for i, p in enumerate(big):
    rec[f"canvas_pdf[{i}]"] = med(canvas(p))
    rec[f"canvas_pdf_png300[{i}]"] = med(canvas(p, 300))
    pc.get(p.name, p, 1200)
    rec[f"preview_hit[{i}]"] = med(lambda p=p: pc.get(p.name, p, 1200), n=15)
renderhost.shutdown_shared()
print(json.dumps(rec))
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, required=True, help="装着 PDF 的目录")
    ap.add_argument("--tree", action="append", required=True, help="LABEL=/path/to/src，给两次")
    ap.add_argument("--rounds", type=int, default=4)
    args = ap.parse_args()
    trees = [t.split("=", 1) for t in args.tree]
    if len(trees) != 2:
        ap.error("--tree 恰好给两次（ABBA 交错）")
    rows: dict[str, list[dict]] = {label: [] for label, _ in trees}
    script = Path(tempfile.mkdtemp()) / "one_round.py"
    script.write_text(ONE_ROUND)
    for r in range(args.rounds):
        order = trees if r % 2 == 0 else trees[::-1]
        for label, path in order:
            env = dict(os.environ, PYTHONPATH=path)
            out = (
                subprocess.run(
                    [sys.executable, str(script), str(args.src), path],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                .stdout.strip()
                .splitlines()[-1]
            )
            rows[label].append(json.loads(out))
            print(f"round {r + 1}: {label} ok", file=sys.stderr)
    (a, _), (b, _) = trees
    print(f"{'':<34}{a:>12}{b:>12}{'':>10}")
    for key in rows[a][0]:
        va = statistics.median(x[key] for x in rows[a])
        vb = statistics.median(x[key] for x in rows[b])
        print(f"{key:<34}{va:>10.2f}ms{vb:>10.2f}ms   x{va / vb:6.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
