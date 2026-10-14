"""CompatBench V2 夹具 s14_missing_pkg_then_limit（场景 14）。

原始失败模式：先缺包（sklearn，发行包名 scikit-learn）；修好包之后才触发 Safe 文件限制（savefig 先写临时文件再 os.replace）
预期策略：一次依赖确认 -> 同一意图里一次执行；Safe 被确认限制后回退 Native（共享预算与历史，不再要第二次装包确认）
理想执行次数：装包前 0；装包后 1（路由直达 Native）/ 2（回退）
覆盖范围：本夹具在 main 上只能观测到第一段（依赖计划，零执行）；装包后的第二段需要离线 wheelhouse（T11-S2 的
`dep_house` 装置），未纳入本基线——记为 not_run，见 compat_v2_scenarios.NOT_RUN。
"""

import json
import os
import sys
from pathlib import Path

# 脚本自写的执行日志（项目目录之外）：每次**开始执行**记一行。回执 / 回显不算执行次数的证据（05_TEST_STRATEGY §4）。
with open(Path(__file__).resolve().parent.parent / "exec_log.jsonl", "a", encoding="utf-8") as _log:
    _log.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")

import matplotlib
from sklearn.cluster import KMeans  # noqa: F401  发行包名 scikit-learn

matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1, 2], [0, 2, 4])
fig.savefig("final.png.tmp", format="png")
os.replace("final.png.tmp", "final.png")
size = Path("final.png").stat().st_size
ax.set_title(f"final bytes>0={size > 0}")
fig.savefig("final_titled.png")
