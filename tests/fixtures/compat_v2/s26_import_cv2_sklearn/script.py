"""CompatBench V2 夹具 s26_import_cv2_sklearn（场景 另（import 名≠发行包名））。

原始失败模式：import cv2 / sklearn 缺失：发行包名是 opencv-python / scikit-learn
预期策略：依赖计划按 import→发行包映射；不盲目 Native；一次确认
理想执行次数：检查阶段 0 次执行；装好后 1
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
import cv2  # noqa: F401  发行包名 opencv-python
import matplotlib.pyplot as plt
from sklearn.cluster import KMeans  # noqa: F401  发行包名 scikit-learn

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
fig.savefig("dep.png")
