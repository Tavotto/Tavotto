"""相对路径读同目录的 CSV——`python figure.py` 下最普通不过的写法。"""
import csv

import matplotlib.pyplot as plt


def main():
    with open("data.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    xs = [float(r["x"]) for r in rows]
    ys = [float(r["y"]) for r in rows]
    fig, ax = plt.subplots(figsize=(3.6, 2.4))
    ax.plot(xs, ys, marker="o")
    ax.set_title("Relative CSV")
    fig.savefig("shape_relative_csv.pdf")
