"""U09 合成 fixture ⑩（FO32 的核心联合实例）：真实 h5py 读 HDF5、同名干扰、项目 Python 与应用不同。

`scripts/figure_h5.py` 站在**项目根**读相对路径 `data/measure.h5`（用户在终端里是 `cd 项目根 &&
python scripts/figure_h5.py`）：数据集 `x` = [2, 4, 8]，画 y = 3·x + 1 = [7, 13, 25]（真值）。脚本目录里
**另有一份**同名 `scripts/data/measure.h5`（干扰）：x = [200, 400, 800] → [601, 1201, 2401]，一样画得出图、
不报错——只有数值能分出读的是哪一份。h5py 经 C 库打开文件，**不走** Python 的 `open`：产品的只读回退救不了它、
输入观察也看不见它（回执如实 `partial`），要靠工作目录决定 + 静态证据。

`--dump` 把读到的 y 打到 stdout（参考跑法 / 测试核数值用）；标题里带解释器版本（图内可见的解释器身份）。
"""

import sys

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

with h5py.File("data/measure.h5", "r") as fh:
    xs = [float(v) for v in fh["x"][()]]
ys = [3 * x + 1 for x in xs]

if "--dump" in sys.argv[1:]:
    print(",".join(f"{y:g}" for y in ys))

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(xs, ys, marker="o")
ax.set_title(f"join h5 · Python {sys.version_info[0]}.{sys.version_info[1]}")
ax.set_xlabel("x")
ax.set_ylabel("y = 3x + 1")
fig.savefig("figure_h5.pdf")
