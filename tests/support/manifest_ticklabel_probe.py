"""在 **worker 解释器**里跑的探针：一次 `build_manifest` 重算了几趟刻度模型。

`tests/` 跑在 Flask 的 .venv 里、import 不动 matplotlib，而这条问的是引擎在
matplotlib 内部踩了多少次 `Axis._update_ticks()`（locator + formatter + 视区
取舍，issue #220 里 manifest 一半的时间花在这上面），只能在这一侧回答。

用法：

    python tests/support/manifest_ticklabel_probe.py            # 打印 JSON 报告

退出码永远是 0（除非探针自己崩了）——判定归调用方
`tests/test_manifest_ticklabel_cost.py`，这里只**如实报事实**。
"""

from __future__ import annotations

import json
import os
import sys

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块平铺 import。
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axis import Axis  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_REAL_UPDATE_TICKS = Axis._update_ticks


class _Counter:
    """`Axis._update_ticks` 的调用计数器（进出成对，重入也只算它自己那次）。"""

    def __init__(self):
        self.n = 0

    def __enter__(self):
        counter = self

        def _counting(self):  # noqa: ANN001 — 顶的是 matplotlib 的未绑定方法
            counter.n += 1
            return _REAL_UPDATE_TICKS(self)

        Axis._update_ticks = _counting
        return self

    def __exit__(self, *exc):
        Axis._update_ticks = _REAL_UPDATE_TICKS
        return False


def _figure(n_ticks: int):
    """除了刻度条数以外**完全相同**的一张图。

    元素表其余部分逐位一致是这条判据的前提：两张图的 `_update_ticks` 次数只该
    差在刻度上，多一条曲线就把差值的来源搞浑了。
    """
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0.0, 1.0], [0.0, 1.0], label="line")
    ax.set_xticks([i / (n_ticks - 1) for i in range(n_ticks)])
    ax.set_yticks([i / (n_ticks - 1) for i in range(n_ticks)])
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    ax.legend()
    return fig, ax


def _case(n_ticks: int) -> dict:
    fig, _ax = _figure(n_ticks)
    state = O.FigState(fig)
    M.instrument(state)
    M.build_manifest(state, "Probe")  # 冷的那一次不算：canvas / 字体缓存还没热
    with _Counter() as c:
        man = M.build_manifest(state, "Probe")
    plt.close(fig)
    return {
        "n_ticks": n_ticks,
        "ticklabel_elements": sum(1 for e in man["elements"] if e["role"] == "ticklabel"),
        "update_ticks": c.n,
    }


def _memo_does_not_outlive_one_build() -> dict:
    """记忆表**不许跨 build 存活**：改完刻度再建一次，manifest 必须看见新刻度。

    这是缓存这类改动唯一会致命的失效形状——量出来的数字更好看，而用户改完
    刻度界面上还是旧的那排字。
    """
    fig, ax = _figure(5)
    state = O.FigState(fig)
    M.instrument(state)
    before = [
        e["editable"][0]["value"]
        for e in M.build_manifest(state, "Probe")["elements"]
        if e["role"] == "ticklabel" and e["gid"].startswith("axes_0.xticklabels_")
    ]
    ax.set_xticks([0.0, 0.5, 1.0], labels=["AA", "BB", "CC"])
    after = [
        e["editable"][0]["value"]
        for e in M.build_manifest(state, "Probe")["elements"]
        if e["role"] == "ticklabel" and e["gid"].startswith("axes_0.xticklabels_")
    ]
    plt.close(fig)
    return {"before": before, "after": after}


def main() -> None:
    report = {
        "few": _case(4),
        "many": _case(24),
        "across_builds": _memo_does_not_outlive_one_build(),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
