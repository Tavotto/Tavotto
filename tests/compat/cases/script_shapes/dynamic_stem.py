"""stem 只有运行期才知道（读同目录的数据文件决定文件名）。

静态扫描解不出来，必须靠试运行探测（engine/probe.py）登记——CompatBench
把这一条记成 `requires_probe`，它验的正是「用户不必手写注册表」。
"""
import csv

import matplotlib.pyplot as plt


def main():
    with open("data.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    groups = sorted({r["group"] for r in rows})
    for g in groups:
        pts = [(float(r["x"]), float(r["y"])) for r in rows if r["group"] == g]
        fig, ax = plt.subplots(figsize=(3.0, 2.0))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o")
        ax.set_title(f"group {g}")
        fig.savefig(f"shape_dynamic_{g}.pdf")
