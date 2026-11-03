"""render-bench 夹具：Hist。只依赖 numpy 与 matplotlib。"""

import sys

import matplotlib.pyplot as plt
import numpy as np

# 与 scripts/ 下其他入口同一条纪律：两条流都钉成 UTF-8（见 scripts/build_frontend.py）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.hist(np.sin(np.linspace(0, 60, 2000)) ** 3, bins=24)
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.set_title("Hist")
    fig.savefig("fig4_hist.pdf")


if __name__ == "__main__":
    main()
