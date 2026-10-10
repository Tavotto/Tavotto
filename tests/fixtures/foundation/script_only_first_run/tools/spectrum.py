"""T11（C23）夹具：只有脚本 / 数据 / 依赖声明的项目——首跑必须经产品的正常路径才会有图。

- 数据在项目根的 `data/values.csv`，按 **cwd** 读：脚本在 `tools/` 下，只有运行目录 = 项目根才读得到；
- 必填 `--scale` / `--label`（可带中文与空格），可选 `--offset`（负数）/ `--tag`（可以是空串）/ `--seed`；
- 运行中问一次菜单（`input()`），菜单内容随 `--scale` 变：`< 2` 只有 raw / smooth，否则多一个 log；
- 不同参数存同一个图名 `spectrum.pdf`；
- 每次执行往**项目目录之外**（项目目录的父目录）追加一行 `t11_exec_log.jsonl`：进程实际看到的解释器 / prefix /
  argv / cwd / 菜单 / 选项 / 画出的 y。参考运行与被测运行各在自己的父目录里记，互不相干；不往项目里写日志。
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

parser = argparse.ArgumentParser(description="spectrum")
parser.add_argument("--scale", type=float, required=True)
parser.add_argument("--label", required=True)
parser.add_argument("--offset", type=float, default=0.0)
parser.add_argument("--tag", default="")
parser.add_argument("--seed", type=int, default=7)
args = parser.parse_args()

with open("data/values.csv", encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh))
x = np.array([float(r["x"]) for r in rows])
base = np.array([float(r["y"]) for r in rows])

menu = ["raw", "smooth"] + (["log"] if args.scale >= 2 else [])
for i, name in enumerate(menu):
    print(f"{i}) {name}")
mode = menu[int(input("mode? ").strip())]

rng = np.random.default_rng(args.seed)
y = base * args.scale + args.offset + rng.normal(0.0, 0.01, size=base.size)
if mode == "smooth":
    y = np.convolve(y, np.ones(3) / 3.0, mode="same")
elif mode == "log":
    y = np.log1p(np.abs(y))

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot(x, y, label=args.label)
ax.set_title(f"{args.label} [{mode}]" + (f" {args.tag}" if args.tag else ""))
ax.legend()
fig.savefig("spectrum.pdf")

log = Path(__file__).resolve().parent.parent.parent / "t11_exec_log.jsonl"
with open(log, "a", encoding="utf-8") as out:
    record = {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "menu": menu,
        "mode": mode,
        "y": [round(float(v), 12) for v in y],
    }
    out.write(json.dumps(record, ensure_ascii=False) + "\n")
