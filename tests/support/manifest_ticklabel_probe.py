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
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

# 与 `engine/worker.py` 同一条 sys.path 纪律：engine 目录进 path，模块平铺 import。
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.axis import Axis  # noqa: E402
from matplotlib.ticker import FixedFormatter, FixedLocator  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402
import pathgeom  # noqa: E402
import tickmodel  # noqa: E402

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


def _apply_inside_the_scope_raises() -> dict:
    """`apply()` 落进记忆表作用域时**当场炸**，不是安静地读旧刻度。

    `fig.stale` 当不了这个信号：实测每次 `build_manifest` 结束时它都是 True
    （量包围盒本身就会把 artist 标脏），拿它当「作用域里有人动过图」会恒假报警。
    能守住前提的是 `apply` 这个唯一入口。
    """
    fig, _ax = _figure(5)
    state = O.FigState(fig)
    M.instrument(state)
    outside = "no-raise"
    try:
        O.apply(state, [])  # 作用域外：照常
    except RuntimeError as e:  # noqa: BLE001
        outside = f"raised: {e}"
    inside = "no-raise"
    with tickmodel.ticklabel_memo():
        try:
            O.apply(state, [])
        except RuntimeError:
            inside = "raised"
    plt.close(fig)
    return {"outside_scope": outside, "inside_scope": inside}


def _filtered_fixture():
    fig, axes = plt.subplots(1, 2)
    for i, ax in enumerate(axes):
        ax.set(xlim=(0, 2), ylim=(0, 2))
        for which in ("x", "y"):
            axis = getattr(ax, f"{which}axis")
            axis.set_major_locator(FixedLocator([-1, 0, 1, 2, 3]))
            axis.set_major_formatter(FixedFormatter([f"{i}{which}{n}" for n in range(5)]))
            axis.set_minor_locator(FixedLocator([-0.5, 0.5, 1.5, 2.5]))
            axis.set_minor_formatter(FixedFormatter([f"{i}{which}m{n}" for n in range(4)]))
            ax.tick_params(axis=which, which="both", labeltop=True, labelright=True)
            axis.get_major_ticks()[2].set_visible(False)
    fig.canvas.draw()
    return fig, axes


def _filtered_reads() -> dict:
    """Count only repeated filtered reads after drawing; preserve raw side/index identities."""
    fig, axes = _filtered_fixture()
    rows = []
    with tickmodel.ticklabel_memo(), _Counter() as counter:
        for ax in axes:
            for which in ("x", "y"):
                for minor in (False, True):
                    axis = getattr(ax, f"{which}axis")
                    ticks = axis.get_minor_ticks() if minor else axis.get_major_ticks()
                    expected = [ticks[i].label1 for i in ((1, 2) if minor else (1, 3))]
                    expected += [ticks[i].label2 for i in ((1, 2) if minor else (1, 3))]
                    first = tickmodel.drawn_tick_label_entries(ax, which, minor=minor)
                    before = counter.n
                    again = [
                        tickmodel.drawn_tick_label_entries(ax, which, minor=minor) for _ in range(3)
                    ]
                    rows.append(
                        {
                            "minor": minor,
                            "indices": [i for i, _ in first],
                            "expected_objects": [t for _, t in first] == expected,
                            "same_objects": all(a == first for a in again),
                            "repeat_updates": counter.n - before,
                        }
                    )
                before = counter.n
                for _ in range(3):
                    tickmodel.TickSet(ax, which)._first(lambda t: t.get_text(), "")
                rows[-1]["tickset_updates"] = counter.n - before
        before = counter.n
        for ax in axes:
            tickmodel.TickSet(ax, "x").labels
            tickmodel.TickSet(ax, "y").labels
        revisit_updates = counter.n - before
    plt.close(fig)
    return {"rows": rows, "revisit_updates": revisit_updates}


def _filtered_lifetime() -> dict:
    fig, axes = _filtered_fixture()
    ax = axes[0]
    with tickmodel.ticklabel_memo():
        old = tickmodel.drawn_tick_label_entries(ax, "x")
        outer = tickmodel._ticklabel_memo.table
        try:
            with tickmodel.ticklabel_memo():
                tickmodel.drawn_tick_label_entries(ax, "x")
                inner_distinct = tickmodel._ticklabel_memo.table is not outer
                raise ValueError("scope cleanup")
        except ValueError:
            pass
        nested = inner_distinct and tickmodel._ticklabel_memo.table is outer
        with _Counter() as counter:
            tickmodel.drawn_tick_label_entries(ax, "x")
        outer_updates = counter.n
    restored = getattr(tickmodel._ticklabel_memo, "table", None) is None
    ax.xaxis.set_visible(False)
    with tickmodel.ticklabel_memo():
        hidden = tickmodel.drawn_tick_label_entries(ax, "x")
    ax.xaxis.set_visible(True)
    ax.set_xlim(0, 0.5)
    with tickmodel.ticklabel_memo():
        current = tickmodel.drawn_tick_label_entries(ax, "x")
    outside = []
    for visible in (False, True):
        ax.xaxis.set_visible(visible)
        outside.append(len(tickmodel.drawn_tick_label_entries(ax, "x")))
    # Separate figures are built/drawn serially; only independent reads overlap.
    other_fig, other_axes = _filtered_fixture()
    barrier = threading.Barrier(2)

    def read_thread(thread_ax):
        with tickmodel.ticklabel_memo():
            entries = tickmodel.drawn_tick_label_entries(thread_ax, "x")
            table = tickmodel._ticklabel_memo.table
            barrier.wait(timeout=10)
            own = tickmodel._ticklabel_memo.table is table
            barrier.wait(timeout=10)
        return table, entries, own, getattr(tickmodel._ticklabel_memo, "table", None) is None

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(read_thread, a) for a in (ax, other_axes[0])]
        a, b = [job.result(timeout=30) for job in jobs]
    plt.close(fig)
    plt.close(other_fig)
    return {
        "nested": nested,
        "outer_updates": outer_updates,
        "restored": restored,
        "old": [i for i, _ in old],
        "hidden": [i for i, _ in hidden],
        "current": [i for i, _ in current],
        "outside": outside,
        "threads": a[0] is not b[0] and all((a[2], a[3], b[2], b[3])),
        "thread_counts": [len(a[1]), len(b[1])],
    }


def _filtered_fallback() -> dict:
    fig, axes = _filtered_fixture()
    ax = axes[0]
    axis = ax.xaxis
    calls = []

    def unavailable():
        calls.append(True)
        raise RuntimeError("private API unavailable")

    with tickmodel.ticklabel_memo():
        raw = tickmodel._ticklabels(ax, "x")
        with patch.object(axis, "_update_ticks", unavailable):
            fallback = tickmodel.drawn_tick_label_entries(ax, "x")
            repeated = tickmodel.drawn_tick_label_entries(ax, "x")
        # Restore class lookup before counting; a saved bound method would shadow it.
        with _Counter() as counter:
            recovered = tickmodel.drawn_tick_label_entries(ax, "x")
            recovered_first_updates = counter.n
            tickmodel.drawn_tick_label_entries(ax, "x")
            recovered_updates = counter.n - recovered_first_updates
    get_labels = ax.get_xticklabels
    try:
        ax.get_xticklabels = lambda: get_labels()
        with tickmodel.ticklabel_memo():
            missing_minor = tickmodel.drawn_tick_label_entries(ax, "x", minor=True)
            ax.get_xticklabels = get_labels
            recovered_minor = tickmodel.drawn_tick_label_entries(ax, "x", minor=True)
    finally:
        ax.get_xticklabels = get_labels
    plt.close(fig)
    return {
        "raw_count": len(raw),
        "fallback": fallback == list(enumerate(raw)),
        "repeated": repeated == fallback,
        "calls": len(calls),
        "missing_minor": missing_minor,
        "recovered": [i for i, _ in recovered],
        "recovered_minor": [i for i, _ in recovered_minor],
        "recovered_first_updates": recovered_first_updates,
        "recovered_updates": recovered_updates,
    }


def _projection_and_draw_order() -> dict:
    fig = plt.figure()
    ax = fig.add_subplot(projection="3d")
    ax.plot([0, 1], [0, 2], [0, 3])
    fig.canvas.draw()

    def positions():
        return [
            t.label1.get_position()
            for axis in (ax.xaxis, ax.yaxis, ax.zaxis)
            for t in axis.get_major_ticks()
        ]

    before = positions()
    counts = []
    with tickmodel.ticklabel_memo():
        for which in ("x", "y", "z"):
            counts.append(len(tickmodel.drawn_tick_label_entries(ax, which)))
        with _Counter() as counter:
            for which in ("x", "y", "z"):
                tickmodel.drawn_tick_label_entries(ax, which)
    projected = before == positions()
    plt.close(fig)

    fig, ax = plt.subplots(layout="tight")
    ax.plot([0, 1], [0, 1])
    pathgeom.set_frame(fig, Bbox.from_extents(-0.2, -0.3, 6.2, 4.5))
    state = O.FigState(fig)
    M.instrument(state)
    sizes = []
    draw = fig.canvas.draw

    def observe_draw():
        table = getattr(tickmodel._ticklabel_memo, "table", None)
        sizes.append(None if table is None else len(table))
        return draw()

    fig.canvas.draw = observe_draw
    try:
        man = M.build_manifest(state, "framed")
    finally:
        fig.canvas.draw = draw
        plt.close(fig)
    return {
        "projected": projected,
        "counts": counts,
        "repeat_updates": counter.n,
        "draw_memo_sizes": sizes,
        "frame_active": man["frame"]["active"],
    }


def main() -> None:
    report = {
        "few": _case(4),
        "many": _case(24),
        "across_builds": _memo_does_not_outlive_one_build(),
        "apply_guard": _apply_inside_the_scope_raises(),
        "filtered_reads": _filtered_reads(),
        "filtered_lifetime": _filtered_lifetime(),
        "filtered_fallback": _filtered_fallback(),
        "projection_and_draw_order": _projection_and_draw_order(),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
