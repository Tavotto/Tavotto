"""CompatBench V2 夹具 s05_dynamic_rename（场景 5）。

原始失败模式：运行时算出的路径做 os.rename 后读回（静态扫描看不到最终文件名）
预期策略：运行证据驱动：Safe 被阻断后回退 Native
理想执行次数：2（静态不可判，需运行证据）
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

n = len(sys.argv) + 3
src = f"chart_{n}.tmp.png"
dst = f"chart_{n * 2}.png"
fig, ax = plt.subplots()
ax.plot([0, 1], [0, n])
fig.savefig(src, format="png")
os.rename(src, dst)
size = Path(dst).stat().st_size
ax.set_title(f"bytes>0={size > 0}")
fig.savefig("renamed_check.png")
