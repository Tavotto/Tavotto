"""CompatBench V2 夹具 s18_readonly_dir（场景 18）。

原始失败模式：项目目录只读（运行前由测试 chmod 555）：脚本要往 cwd 写文件
预期策略：不盲目 Native：给出可读的一句原因；Native 不比 Safe 更能写只读目录
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
ax.plot([0, 1], [1, 0])
fig.savefig("readonly_out.png")
