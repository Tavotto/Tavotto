"""U00 合成 fixture ④ 的项目脚本：与应用不同 Python 的项目 `.venv` 应当能跑它。

脚本本身只要 matplotlib（由 venv 的基础解释器或用户自己装的那份提供）。它把
**真正跑它的解释器**写进图标题并打到 stdout（`sys.version_info[:2]` 与
`sys.prefix`），这样「用的是哪个 Python」不用猜、直接从产物与输出读。
真值见 `truth.json`：n = 10·k。
"""

import csv
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 —— backend 选定之后才能 import pyplot

with open("data.csv", encoding="utf-8", newline="") as fh:
    rows = list(csv.DictReader(fh))
ks = [float(r["k"]) for r in rows]
ns = [float(r["n"]) for r in rows]

identity = {
    "python": list(sys.version_info[:3]),
    "prefix": sys.prefix,
    "executable": sys.executable,
    "matplotlib": matplotlib.__version__,
}
print(json.dumps(identity))

fig, ax = plt.subplots(figsize=(3.2, 2.4))
ax.plot(ks, ns, marker="^")
ax.set_title(f"Python {sys.version_info[0]}.{sys.version_info[1]}")
fig.savefig("figure.pdf")
