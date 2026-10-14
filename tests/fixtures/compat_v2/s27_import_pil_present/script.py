"""CompatBench V2 夹具 s27_import_pil_present（场景 另（import 名≠发行包名））。

原始失败模式：PIL 已随 matplotlib 装好；import 名 PIL 与发行包名 pillow 不同，不得被当成缺包
预期策略：不报缺包，Safe 直接成功
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
import PIL  # noqa: F401
from PIL import Image  # noqa: F401

fig, ax = plt.subplots()
ax.plot([0, 1], [1, 0])
ax.set_title("pil present")
fig.savefig("pil_present.png")
