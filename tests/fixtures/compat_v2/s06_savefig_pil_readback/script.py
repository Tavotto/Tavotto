"""CompatBench V2 夹具 s06_savefig_pil_readback（场景 6）。

原始失败模式：savefig 后用 PIL Image.open 读回（Safe 内 savefig 目标被改写）
预期策略：直接 Native（明确 savefig→读回）；或 Safe 阻断后回退
理想执行次数：1（路由）/ 2（回退）
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
from PIL import Image

fig, ax = plt.subplots()
ax.plot([0, 1, 2], [2, 1, 3])
fig.savefig("shot.png")
with Image.open("shot.png") as im:
    width, height = im.size
ax.set_title(f"{width}x{height}")
fig.savefig("shot_titled.png")
