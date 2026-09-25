#!/usr/bin/env python3
"""LONG-01/02/03 的夹具：两个同名脚本、同名 stem、数据不同的项目 A / B（G1 简化形态）。

确定性：内容全部写死，不依赖随机数；每次生成的字节相同（sha256 记进台账）。

用法（从 worktree 根目录）：
    python docs/qa/2026-09-24/long/repro/make_fixtures.py <输出目录>

产出：
    <out>/projA/fig.py  + <out>/projA/data/points.csv   （y = 0,9,2,10，标题 "Panel A"）
    <out>/projB/fig.py  + <out>/projB/data/points.csv   （y = 0,2,9,10，标题 "Panel B"）
两份点序列的极值 / 均值相同（与规范 D1 同一思路），真值只能靠全序列比对。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SCRIPT = '''\
"""QA LONG 夹具 G1：已知图幅、单标题 / 曲线 / 图例 / 注释 / 文字。"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
rows = (HERE / "data" / "points.csv").read_text(encoding="utf-8").split()[1:]
xs = [float(r.split(",")[0]) for r in rows]
ys = [float(r.split(",")[1]) for r in rows]

fig, ax = plt.subplots(figsize=(3.2, 2.4), dpi=100)
ax.plot(xs, ys, color="#1f77b4", label="{label}")
ax.set_title("{title}")
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.legend(loc="upper left")
ax.annotate("peak", xy=(3, ys[3]), xytext=(1.5, 11), arrowprops={{"arrowstyle": "->"}})
fig.text(0.72, 0.9, "note", fontsize=8)
fig.savefig(HERE / "{stem}.pdf")
'''

PROJECTS = {
    "projA": {"title": "Panel A", "label": "series A", "ys": (0, 9, 2, 10)},
    "projB": {"title": "Panel B", "label": "series B", "ys": (0, 2, 9, 10)},
}
STEM = "Fig1"


def build(out: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name, spec in PROJECTS.items():
        root = out / name
        (root / "data").mkdir(parents=True, exist_ok=True)
        script = SCRIPT.format(title=spec["title"], label=spec["label"], stem=STEM)
        csv = "x,y\n" + "".join(f"{i},{y}\n" for i, y in enumerate(spec["ys"]))
        (root / "fig.py").write_text(script, encoding="utf-8")
        (root / "data" / "points.csv").write_text(csv, encoding="utf-8")
        for rel in ("fig.py", "data/points.csv"):
            data = (root / rel).read_bytes()
            hashes[f"{name}/{rel}"] = hashlib.sha256(data).hexdigest()
    return hashes


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "qa-long-fixtures")
    for k, v in build(target).items():
        print(f"{v}  {k}")
