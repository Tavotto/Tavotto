"""native 屏障之间脚本改了状态：挂在 artist 上的「脚本原样」缓存必须跟着换成新 baseline（#1159 评审）。

屏障放行时 `release_barrier` 用 `overrides.apply(state, [])` 把图还原成脚本原样，`state.originals`
随之清空；下一个屏障 `rebase` 重新 instrument + 重放，采到的是脚本**此刻**的值。ADR 0135 新加的两份
缓存挂在 artist 上、不在 `state.originals` 里：

* 独立 `ScalarMappable` 色条的 `_mm_orig_cmap`；
* `Poly3DCollection` 的颜色快照 `_mm_poly3d_native`。

它们若活过屏障，脚本在两个屏障之间改的色图 / alpha 就会在「清掉编辑」时被换回第一次屏障的旧值——
继续跑的脚本看到（或存下）的是一张被悄悄改过的图。这里按屏障流程走一遍：套用编辑 → 还原放行 →
脚本侧改状态 → 重新 instrument + 重放 → 清掉编辑，断言回到的是**脚本的新状态**。

本进程不 import matplotlib：探针在科学栈解释器里跑。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有可用的 matplotlib 解释器")
ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"

PROBE = r"""
import json
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

import manifest
import overrides

fig = plt.figure(figsize=(4, 3))
ax = fig.add_subplot(projection="3d")
x, y = np.meshgrid(np.linspace(0, 1, 6), np.linspace(0, 1, 5))
surf = ax.plot_surface(x, y, x + y, color="tab:blue", edgecolor="k")
sm = ScalarMappable(norm=Normalize(0, 1), cmap="viridis")
fig.colorbar(sm, ax=ax)

state = overrides.FigState(fig)
manifest.instrument(state)
fig.canvas.draw()
cb = next(e["gid"] for e in state.elements if e["role"] == "colorbar")
surf_gid = next(e["gid"] for e in state.elements if e["artist"] is surf)
edits = [
    {"gid": cb, "prop": "cmap", "value": "plasma"},
    {"gid": surf_gid, "prop": "alpha", "value": 0.3},
]

# 第一个屏障：用户编辑；放行：还原成脚本原样
assert not overrides.apply(state, edits)
fig.canvas.draw()
assert not overrides.apply(state, [])
fig.canvas.draw()

# 屏障之间：脚本自己改了色图与曲面的 alpha
sm.set_cmap("magma")
surf.set_alpha(0.6)
fig.canvas.draw()
script_face = surf._facecolor3d.copy()
script_edge = surf._edgecolor3d.copy()

# 下一个屏障：rebase（重新 instrument + 重放保存的编辑），再清掉编辑
manifest.instrument(state)
assert not overrides.apply(state, edits)
fig.canvas.draw()
assert not overrides.apply(state, [])
fig.canvas.draw()

print(json.dumps({
    "cmap": sm.get_cmap().name,
    "alpha": surf.get_alpha(),
    "face_same": bool(np.allclose(surf._facecolor3d, script_face)),
    "edge_same": bool(np.allclose(surf._edgecolor3d, script_edge)),
}))
"""


def test_caches_take_the_new_baseline_after_a_barrier():
    out = subprocess.run(
        [str(WORKER_PY), "-c", PROBE, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-3000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["cmap"] == "magma", got  # 不是第一个屏障时的 viridis
    assert got["alpha"] == 0.6, got
    assert got["face_same"] and got["edge_same"], got  # 颜色数组逐位回到脚本新状态
