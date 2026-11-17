"""属性测试：样式 / override 重跑幂等（ADR 0023 §override 全量语义；`override-application-and-replay.md`）。

主语：**热会话里的这张图**，在应用**同一份全量 override 列表 P** 之后的两个维度（`manifest` 整份 + 预览像素）：
应用一次 == 在它之上再应用一次（样式重跑 / 前端重发同一份列表）== 把 P 自己拼两遍（`P + P`，last-wins 同值）发一次。
三个读数都在「P 刚应用完」那个时刻取，取法完全对称（都是 `override` 之后 `preview_png`，没有哪一边多一次预览）。

起点不是干净的 Figure：每个例子开头不清，接着上一个例子的收尾状态走（收尾时 `override([])` 清回原样），
所以量到的是「P 应用在任意先前状态上」的幂等，而不只是「应用在原样上」。热态 == 全新 worker 的重放那一条在
`test_override_sequences.py` / `test_invariants_engine.py`，这里不重复。

生成域：`invariant_figures` 里两张图（`InvMix` / `InvCont`）manifest 宣称可编辑的字段，类型有通用取值（颜色 / 布尔 /
枚举 / 带区间的数 / 文字），去掉整块结构性的角色（`figure` / `axes` / `axes3d` / `ticks` / `ticklabel`，与 `_editable_targets`
同一条口径：它们各有专用用例，随机子集跨过去会撞已登记的耦合）。
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
_STRUCTURAL_ROLES = ("figure", "axes", "axes3d", "ticks", "ticklabel")


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "属性测试（导出 .py 往返，常驻 worker 一版）缩出的真缺陷：`axes.grid_linewidth` 改完又撤掉之后再打开 y 向网格，热态的线比全新重放的粗（机理未查，"
        "疑似撤掉时没画的那一向线宽没还原）。manifest 逐字相等，只有像素不同（AdjGrid：1.5% 像素变、max 差 79）。"
        "待维护者定：修还原还是登记成已知分岔；修好后 strict 会红，提醒摘掉。"
    ),
)
def test_removing_a_grid_linewidth_edit_restores_the_original_hot_vs_fresh(tmp_path_factory):
    """最小反例：序列 `[axes_0.grid_linewidth = 1.9]` → `[axes_0.grid_y = True]`（第二步的全量列表里已没有线宽）。
    读数：热会话（走过两步）与只见过第二步的全新 worker，manifest 与预览像素。"""
    figs = tmp_path_factory.mktemp("prop-grid-lw")
    (figs / ADJ_SCRIPT).write_text(ADJ_LIBRARY, encoding="utf-8")

    def _worker():
        w = pool.one_shot(ADJ_SCRIPT, str(figs), ADJ_ENTRY)
        w.ensure_built()
        return w

    first = [{"gid": "axes_0", "prop": "grid_linewidth", "value": 1.9}]
    final = [{"gid": "axes_0", "prop": "grid_y", "value": True}]
    hot_w, fresh_w = _worker(), _worker()
    try:
        hot_w.override("AdjGrid", first)
        hot = _reading(hot_w, "AdjGrid", final, "hot")
        fresh = _reading(fresh_w, "AdjGrid", final, "fresh")
    finally:
        pool.discard(hot_w)
        pool.discard(fresh_w)
    assert hot == fresh
