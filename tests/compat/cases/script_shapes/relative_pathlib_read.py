"""Path("config.json").read_text() —— pathlib 走的也是 builtins.open。"""
import json
from pathlib import Path

import matplotlib.pyplot as plt


def main():
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(cfg["points"], marker="s")
    ax.set_title(cfg["title"])
    ax.set_xlabel(cfg["xlabel"])
    ax.set_ylabel(cfg["ylabel"])
    fig.savefig("shape_relative_pathlib.pdf")
