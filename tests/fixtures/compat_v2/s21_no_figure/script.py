"""CompatBench V2 夹具 s21_no_figure（场景 21）。

原始失败模式：退出码 0 但没有任何可编辑 Figure
预期策略：不回退（无阻断证据）：说明「脚本没画图」
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

print("done, but nothing was drawn")
