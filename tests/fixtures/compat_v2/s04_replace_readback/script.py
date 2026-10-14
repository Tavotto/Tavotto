"""CompatBench V2 夹具 s04_replace_readback（场景 4）。

原始失败模式：临时文件写好后 os.replace 成正式文件再读回；Safe 的文件账本阻断
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

tmp = Path("value.txt.tmp")
tmp.write_text("2", encoding="utf-8")
os.replace(tmp, "value.txt")
value = float(Path("value.txt").read_text(encoding="utf-8"))
fig, ax = plt.subplots()
ax.plot([0, 1, 2], [0, value, value * 2])
ax.set_title(f"value={value:g}")
fig.savefig("replace.png")
