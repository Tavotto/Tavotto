"""U00 合成 fixture ①：单文件 matplotlib 脚本 + 同目录 CSV。

真值（`truth.json`）：`data.csv` 的 y 列是 x 的线性函数 y = 1.5·x + 1，
四个点 [2.5, 4.0, 5.5, 7.0]。脚本用**相对路径**读同目录的 CSV——在
`python figure.py`（cwd = 脚本目录）下天经地义；cwd 换到别处时 `open("data.csv")`
当场 FileNotFoundError，而不是静默画出别的东西。

原生参考：把整个目录复制到一个独立的临时目录里，`cd` 进去跑
`python figure.py`，产物 `figure.pdf` 落在那个副本里；本目录不该出现任何新文件。
"""

import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

xs: list[float] = []
ys: list[float] = []
with open("data.csv", encoding="utf-8", newline="") as fh:
    for row in csv.DictReader(fh):
        xs.append(float(row["x"]))
        ys.append(float(row["y"]))

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="o", label="y = 1.5x + 1")
ax.set_xlabel("x")
ax.set_ylabel("y")
ax.legend()
fig.savefig("figure.pdf")
