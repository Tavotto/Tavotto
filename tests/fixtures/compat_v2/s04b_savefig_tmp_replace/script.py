"""CompatBench V2 夹具 s04b_savefig_tmp_replace（场景 4/6）。

原始失败模式：savefig 先写临时文件，再 os.replace 成正式图后读回（图本身经过改名；Safe 的 savefig 目标被改写）
预期策略：识别 replace→读回：直接 Native；或 Safe 被确认限制后回退 Native
理想执行次数：1（路由直达 Native）；回退路径可接受 2
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
ax.plot([0, 1, 2], [0, 2, 4])
fig.savefig("final.png.tmp", format="png")
os.replace("final.png.tmp", "final.png")
size = Path("final.png").stat().st_size
ax.set_title(f"final bytes>0={size > 0}")
fig.savefig("final_titled.png")
