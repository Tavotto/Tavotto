"""CompatBench V2 夹具 s25_partial_capture（场景 25）。

原始失败模式：两张图，其中一张含只能在 Agg 下画的 Artist，捕获失败（partial）
预期策略：保留成功那张；明确告知另一张未捕获；不因 partial 回退
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
from matplotlib.artist import Artist


class AggOnly(Artist):
    def draw(self, renderer):
        renderer.buffer_rgba()


good, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
ax.set_title("good")
good.savefig("good.png")
bad, ax2 = plt.subplots()
ax2.add_artist(AggOnly())
bad.savefig("bad.png")
