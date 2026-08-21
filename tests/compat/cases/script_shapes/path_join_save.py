"""输出路径由 pathlib 拼出来：Path("out") / "figure.pdf"。"""
from pathlib import Path

import matplotlib.pyplot as plt

OUT = Path("out")


def main():
    OUT.mkdir(exist_ok=True)
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot([2, 4, 6, 8])
    ax.set_title("Path join")
    fig.savefig(OUT / "shape_path_join.pdf")
