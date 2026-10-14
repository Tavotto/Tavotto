"""CompatBench V2 夹具 s17_side_effect_then_limit（场景 17）。

原始失败模式：先写外部副作用（本地 sqlite 计数），之后才遇到 Safe 文件限制
预期策略：策略已裁决：接受回退时副作用重复一次（用户 2026-10-14 裁决）；路由直达 Native 则只 1 次
理想执行次数：副作用 1（路由）/ 2（回退，已接受）
副作用库在项目目录之外：<tmp>/side.sqlite（表 effects，一行 = 一次外部写入）。
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
import sqlite3

import matplotlib.pyplot as plt

db = sqlite3.connect(str(Path(__file__).resolve().parent.parent / "side.sqlite"))
db.execute("CREATE TABLE IF NOT EXISTS effects (pid INTEGER)")
db.execute("INSERT INTO effects VALUES (?)", (os.getpid(),))
db.commit()
db.close()

fig, ax = plt.subplots()
ax.plot([0, 1, 2], [0, 1, 4])
fig.savefig("effect.png.tmp", format="png")
os.replace("effect.png.tmp", "effect.png")
ax.set_title(f"bytes>0={Path('effect.png').stat().st_size > 0}")
fig.savefig("effect_titled.png")
