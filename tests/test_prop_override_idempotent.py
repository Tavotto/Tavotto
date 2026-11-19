"""属性测试：样式 / override 重跑幂等（ADR 0023 §override 全量语义；`override-application-and-replay.md`）。

主语：**热会话里的这张图**，在应用**同一份全量 override 列表 P** 之后的两个维度（`manifest` 整份 + 预览像素）：
应用一次 == 在它之上再应用一次（样式重跑 / 前端重发同一份列表）== 把 P 自己拼两遍（`P + P`，last-wins 同值）发一次。
三个读数都在「P 刚应用完」那个时刻取，取法完全对称（都是 `override` 之后 `preview_png`，没有哪一边多一次预览）。

起点不是干净的 Figure：每个例子开头不清，接着上一个例子的收尾状态走（收尾时 `override([])` 清回原样），
所以量到的是「P 应用在任意先前状态上」的幂等，而不只是「应用在原样上」。热态 == 全新 worker 的重放那一条在
`test_override_sequences.py` / `test_invariants_engine.py`，这里不重复。

生成域：`invariant_figures` 里两张图（`InvMix` / `InvCont`）manifest 宣称可编辑的字段，类型有通用取值（颜色 / 布尔 /
枚举 / 带区间的数 / 文字），去掉整块结构性的角色（`figure` / `axes3d` / `ticks` / `ticklabel`，与 `_editable_targets`
同一条口径；2D `axes` 已纳入：它们各有专用用例，随机子集跨过去会撞已登记的耦合）。
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter

import pytest
from hypothesis import given, settings, strategies as st

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from support.prop_edits import edits
from support.scriptadjust_figures import (
    ENTRY as ADJ_ENTRY,
    LIBRARY as ADJ_LIBRARY,
    SCRIPT_NAME as ADJ_SCRIPT,
)
from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

STEMS = ("InvMix", "InvCont")
#: 例子里真被应用过的 (元素 gid, prop) 计数：断言广度，也是变异反证选锚点的依据。
_APPLIED: Counter = Counter()
_STRUCTURAL_ROLES = ("figure", "axes3d", "ticks", "ticklabel")


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    figs = tmp_path_factory.mktemp("prop-idem")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    worker = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    worker.ensure_built()
    try:
        yield worker, {s: worker.override(s, [])["manifest"] for s in STEMS}
    finally:
        pool.discard(worker)


def _accept(el: dict, _f: dict) -> bool:
    return el["role"] not in _STRUCTURAL_ROLES


def _reading(worker, stem: str, patches: list[dict], tag: str) -> tuple[dict, str]:
    """应用 `patches`，回（整份 manifest，预览像素的 sha1）。"""
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    png = worker.preview_png(stem, list(patches), 380, tag)
    return resp["manifest"], hashlib.sha1(png.read_bytes()).hexdigest()


def test_applying_the_same_overrides_twice_equals_once(hot):
    worker, mans = hot
    n = int(os.environ.get("TAVOTTO_PROP_EXAMPLES", "0"))

    @settings(
        max_examples=n or 14, deadline=None, derandomize=not n, database=None, print_blob=True
    )
    @given(data=st.data())
    def prop(data):
        stem = data.draw(st.sampled_from(STEMS), label="图")
        patches = data.draw(edits(mans[stem], _accept), label="P")
        _APPLIED.update((p["gid"], p["prop"]) for p in patches)
        try:
            once = _reading(worker, stem, patches, "once")
            twice = _reading(worker, stem, patches, "twice")
            doubled = _reading(worker, stem, patches + patches, "doubled")
        finally:
            worker.override(stem, [])
        assert twice == once, ("再应用一次与第一次不同", stem, patches)
        assert doubled == once, ("P + P 与 P 不同", stem, patches)

    prop()
    assert len(_APPLIED) >= 8, _APPLIED


_GRID_EDITS = {
    "grid_linewidth": 1.9,
    "grid_color": "#cc0000",
    "grid_linestyle": "--",
    "grid_alpha": 0.3,
}


@pytest.mark.parametrize("prop", sorted(_GRID_EDITS))
@pytest.mark.parametrize("then", ["grid_y", "grid_x_off_y_on"])
def test_removing_a_grid_style_edit_restores_the_original_hot_vs_fresh(
    tmp_path_factory, prop, then
):
    """回归（原 xfail）：`axes.grid_*` 样式一次写两条轴，撤掉时必须**逐轴**还原。
    AdjGrid 只开了 x 网格——还原若只读第一条画着的线（x 的 `#99aadd` / `-.` / 0.6），
    没画的 y 向就被写成 x 的样式，之后再打开 y 网格与全新重放不同。
    最小反例：`[axes_0.grid_linewidth = 1.9]` → `[axes_0.grid_y = True]`。
    读数：热会话（走过两步）与只见过第二步的全新 worker，manifest 与预览像素。"""
    figs = tmp_path_factory.mktemp("prop-grid-style")
    (figs / ADJ_SCRIPT).write_text(ADJ_LIBRARY, encoding="utf-8")

    def _worker():
        w = pool.one_shot(ADJ_SCRIPT, str(figs), ADJ_ENTRY)
        w.ensure_built()
        return w

    first = [{"gid": "axes_0", "prop": prop, "value": _GRID_EDITS[prop]}]
    final = [{"gid": "axes_0", "prop": "grid_y", "value": True}]
    if then == "grid_x_off_y_on":
        final.append({"gid": "axes_0", "prop": "grid_x", "value": False})
    hot_w, fresh_w = _worker(), _worker()
    try:
        hot_w.override("AdjGrid", first)
        hot = _reading(hot_w, "AdjGrid", final, "hot")
        fresh = _reading(fresh_w, "AdjGrid", final, "fresh")
    finally:
        pool.discard(hot_w)
        pool.discard(fresh_w)
    assert hot == fresh


#: 脚本在 `rc_context` 里建图、画网格：上下文退出后已建刻度线带着当时解析的值（红 / 虚线 / 1.7 / 0.6），
#: 刻度 kw 里却没有 `grid_*`。撤销时若回头去读全局 rcParams，写回的是 worker 的默认而不是这些值。
_RC_GRID_SCRIPT = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    x = np.linspace(0, 6, 40)
    rc = {"grid.color": "#cc2222", "grid.linewidth": 1.7, "grid.linestyle": "--", "grid.alpha": 0.6}
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(4.4, 3.2))
        ax.plot(x, np.sin(x))
        ax.grid(True, axis="x")
    fig.savefig("RcGrid.pdf")
"""


#: 脚本先 `ax.grid(...)` 写 kw（只 x 向、major + minor 都开），再 `plt.setp` 把**已建的线**改成另一套值：
#: kw 是 A、线是 B（x 向）；y 向 kw 没表态、线却被 setp 成 B。minor 刻度线同样（`minorticks_on` 让两档都建出来）。
#: 撤销要把 kw 和线**各自**还原，不能拿 kw 的 A 去盖线的 B。
_SETP_GRID_SCRIPT = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    x = np.linspace(0, 6, 40)
    fig, ax = plt.subplots(figsize=(4.4, 3.2))
    ax.plot(x, np.sin(x))
    ax.minorticks_on()
    ax.grid(True, axis="x", which="both", color="#cc2222", linewidth=1.1, linestyle="--", alpha=0.8)
    for axis in (ax.xaxis, ax.yaxis):
        lines = [t.gridline for t in axis.get_major_ticks() + axis.get_minor_ticks()]
        plt.setp(lines, color="#2255dd", linewidth=2.4, linestyle=":", alpha=0.45)
    fig.savefig("SetpGrid.pdf")
"""

#: (夹具名, 脚本, 图名, 改完之后是否先画一帧再撤销)。画一帧让「捕获之后才建出来的刻度」有机会出现。
_GRID_SCRIPTS = {
    "rc_context": (_RC_GRID_SCRIPT, "RcGrid", False),
    "setp": (_SETP_GRID_SCRIPT, "SetpGrid", False),
    "setp_drawn": (_SETP_GRID_SCRIPT, "SetpGrid", True),
}


@pytest.mark.parametrize("prop", sorted(_GRID_EDITS))
@pytest.mark.parametrize("then", ["grid_y", "grid_x_off_y_on", "nothing"])
@pytest.mark.parametrize("variant", sorted(_GRID_SCRIPTS))
def test_removing_a_grid_edit_restores_kw_and_lines_independently_hot_vs_fresh(
    tmp_path_factory, variant, prop, then
):
    """网格「原样」的两份状态各自还原：tick kw（有值 / 没表态）与已建刻度线的实际值（可与 kw 相等或不等：
    rc_context 里建的网格 kw 没表态而线是 rc 值；`ax.grid` 之后 `plt.setp` 改线色则 kw 是 A、线是 B）。
    x 向画着、y 向没画——`grid_y` / `grid_x_off_y_on` 把没画的那一向打开，`nothing` 直接看 x 向。
    读数：热会话（改了又撤）与只见过最终列表的全新 worker，manifest 与预览像素。"""
    script, stem, drawn = _GRID_SCRIPTS[variant]
    figs = tmp_path_factory.mktemp("prop-grid-kw-lines")
    (figs / "grid_variant.py").write_text(script, encoding="utf-8")

    def _worker():
        w = pool.one_shot("grid_variant.py", str(figs), "main")
        w.ensure_built()
        return w

    first = [{"gid": "axes_0", "prop": prop, "value": _GRID_EDITS[prop]}]
    final = []
    if then != "nothing":
        final.append({"gid": "axes_0", "prop": "grid_y", "value": True})
    if then == "grid_x_off_y_on":
        final.append({"gid": "axes_0", "prop": "grid_x", "value": False})
    hot_w, fresh_w = _worker(), _worker()
    try:
        hot_w.override(stem, first)
        if drawn:
            hot_w.preview_png(stem, list(first), 380, "between")
        hot = _reading(hot_w, stem, final, "hot")
        fresh = _reading(fresh_w, stem, final, "fresh")
    finally:
        pool.discard(hot_w)
        pool.discard(fresh_w)
    assert hot == fresh
