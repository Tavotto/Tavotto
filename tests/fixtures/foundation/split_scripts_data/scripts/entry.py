"""U00 合成 fixture ②：`scripts/entry.py` 与 `data/` 分离 + `__file__` + 本地包。

同一个脚本有两种读数据的形态，由 `--data-via` 选（默认 `file`）：

* `file`   —— **原 cwd 明确**：按 `__file__` 定位 `../data/points.csv`，
              从任何 cwd 启动都读到同一份；
* `cwd`    —— **原 cwd 歧义**：读相对路径 `data/points.csv`，只有
              cwd = 项目根（`python scripts/entry.py`）时成立；
              cwd = `scripts/` 时 FileNotFoundError。

本地包 `labhelpers` 靠 `python scripts/entry.py` 把 `scripts/` 放进 `sys.path[0]`
才 import 得到；它不在 PyPI 上。

真值见 `../truth.json`：v = 2·t + 1，[1, 3, 5, 7]。
"""

import argparse
import csv
import os
import sys

import labhelpers
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)


def data_path(mode: str) -> str:
    if mode == "file":
        return os.path.join(PROJECT, "data", "points.csv")
    return os.path.join("data", "points.csv")


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-via", choices=("file", "cwd"), default="file")
    ap.add_argument("--out", default="entry.pdf")
    args = ap.parse_args(argv)

    ts: list[float] = []
    vs: list[float] = []
    with open(data_path(args.data_via), encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ts.append(float(row["t"]))
            vs.append(float(row["v"]))
    predicted = [labhelpers.predict(t) for t in ts]

    fig, ax = plt.subplots(figsize=(3.2, 2.4))
    ax.plot(ts, vs, "o", label="data")
    ax.plot(ts, predicted, "-", label="2t + 1")
    ax.set_xlabel("t")
    ax.set_ylabel("v")
    ax.legend()
    fig.savefig(args.out)


if __name__ == "__main__":
    main(sys.argv[1:])
