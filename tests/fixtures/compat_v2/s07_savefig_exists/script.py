"""CompatBench V2 夹具 s07_savefig_exists（场景 7）。

原始失败模式：savefig 后检查目标路径存在，不存在则退出（灰区：账本把 exists 当读）
预期策略：灰区——exists 不应单独触发回退；预期 Safe 直接成功
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
ax.plot([0, 1, 2], [1, 2, 4])
fig.savefig("exists_check.png")
if not os.path.exists("exists_check.png"):
    sys.exit(3)
ax.set_title("saved")
