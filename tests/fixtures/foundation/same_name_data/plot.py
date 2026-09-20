"""U00 合成 fixture ③：同名不同值的数据文件。

`data.csv`（本目录，**正确**）x = [2, 4, 8]；`decoy/data.csv`（**干扰**）x = [200, 400, 800]。
脚本读**相对路径** `data.csv` 并画 y = 3·x + 1：

* cwd = 本目录 → y = [7, 13, 25]（真值）
* cwd = `decoy/` → y = [601, 1201, 2401]（错的那份，而且画得出来、不报错）

两份都合法、都画得出图——只有数值能分出读的是哪一份。`--dump` 把读到的 y 打到
stdout，供参考跑法与测试核对数值，不看图。
"""

import csv
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

with open("data.csv", encoding="utf-8", newline="") as fh:
    xs = [float(row["x"]) for row in csv.DictReader(fh)]
ys = [3 * x + 1 for x in xs]

if "--dump" in sys.argv[1:]:
    print(",".join(f"{y:g}" for y in ys))

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="s")
ax.set_title("y = 3x + 1")
fig.savefig("plot.pdf")
