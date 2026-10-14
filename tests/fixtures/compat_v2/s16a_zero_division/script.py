"""CompatBench V2 夹具 s16a_zero_division（场景 16）。

原始失败模式：真实脚本 bug：ZeroDivisionError
预期策略：不回退、不 Native：停下并说明原因
理想执行次数：1
离线、可重复；不联网、不读项目外数据。
"""

import json
import os
import sys
from pathlib import Path

# 脚本自写的执行日志（项目目录之外）：每次**开始执行**记一行。回执 / 回显不算执行次数的证据（05_TEST_STRATEGY §4）。
with open(Path(__file__).resolve().parent.parent / "exec_log.jsonl", "a", encoding="utf-8") as _log:
    _log.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
ratio = 1 / (len(sys.argv) - 1)
ax.set_title(str(ratio))
fig.savefig("never.png")
