"""属性测试：导出「修改后的 .py」往返（ADR 0171）。

主语：**用户在编辑器里的这组编辑值**（热会话的图）→ `scriptadjust.build` 生成的新脚本 → **另一条全新 worker
执行新脚本（+ 没写进去的残余 override）得到的图**，二者的 manifest（几何 + 每个字段的值）与像素必须一致。
判据就是产品自己的那把尺 `app._verify_edited_script`（与写回同一把），这里只把输入从「整图扫描：每个键各采一个值」
换成 Hypothesis 随机抽的「键子集 × 值」。

**生成域 = 产品声称支持的输入**：manifest 里每个字段自己宣称的类型 / 选项 / 区间（`options`、`min` / `max` / `step`），
键必须在 `scriptadjust.WRITES` 里，并且不在整图扫描刻意排除的那几组（`_NOT_IN_THE_SWEEP` / `_HELD_OUT_*`——它们是已登记的
「一起改会触发 `script_export_diverged`」的按组单扫项，随机子集跨过去会得到假红）。结构化字段（pair / order / number_list）
没有可抽的通用值，各有专用用例。

不碰整图扫描 / 覆盖断言（`test_script_adjust_sweep.py`）：那里是确定性的、一键一值；这里补的是**组合与取值**这两个维度。
本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from support.prop_edits import edits
from support.scriptadjust_harness import (
    _HELD_OUT_KEYS,
    _HELD_OUT_PROPS,
    _NOT_IN_THE_SWEEP,
    WORKER_PY,
    _library_worker,
    real_worker,
)
from tavotto.engine import pool, scriptadjust

pytestmark = real_worker

#: 夹具图（`tests/support/scriptadjust_figures.py`）：多列图例 / 带框文字与箭头 / 网格与边框。
STEMS = ("AdjLegend", "AdjText", "AdjGrid")

#: 本进程已验过的例子里：这张图上真有键写进了脚本的次数 / 总次数（防「全被留作重放而空转」）。
_STATS = {"examples": 0, "written": 0}
#: 真写进了脚本并验过的 (类, prop) 键的累计次数：断言广度（只写同一两个键的「通过」说明不了什么），也是变异反证选锚点的依据。
_WRITTEN_KEYS: Counter = Counter()


@pytest.fixture(scope="module")
def adj():
    """一条常驻 worker + 每张图的基准 manifest。每个例子之后清空 override（`finally`），所以例子之间互不留痕。"""
    with tempfile.TemporaryDirectory(prefix="tavotto-prop-export-") as d:
        tmp = Path(d)
        worker, name = _library_worker(tmp, "adj")
        try:
            mans = {s: worker.override(s, [])["manifest"] for s in STEMS}
            yield worker, name, tmp, mans
        finally:
            pool.discard(worker)


def _accept(el: dict, f: dict) -> bool:
    """键写得进脚本、不在整图扫描排除的几组里。"""
    key = (scriptadjust._cls_of(el["gid"], el), f["prop"])
    return (
        key in scriptadjust.WRITES
        and f["prop"] not in (_NOT_IN_THE_SWEEP | _HELD_OUT_PROPS)
        and key not in _HELD_OUT_KEYS
    )


def _roundtrip(
    worker, name: str, tmp: Path, stem: str, patches: list[dict], els: dict
) -> set[tuple[str, str]]:
    """导出脚本重放 == 热态；回这组里**真写进了脚本**的 (类, prop) 键（空 = 全被留作重放，没验到脚本）。"""
    from tavotto import app

    with tempfile.TemporaryDirectory(dir=tmp, prefix="ex-") as d:
        try:
            resp = worker.override(stem, patches)
            assert not (resp.get("warnings") or []), resp["warnings"]
            hot = app._hot_manifest(worker, stem, patches)
            assert hot is not None
            src = (tmp / name).read_bytes()
            adjusted = scriptadjust.build(src, stem, patches, hot, version="9.9")
            if not adjusted.written:
                return set()
            stage = Path(d)
            target = stage / "edited.py"
            target.write_bytes(adjusted.data)
            diffs = app._verify_edited_script(worker, stem, target, adjusted, hot, stage)
            assert diffs == [], (stem, patches, diffs)
            residual = {(r["gid"], r["prop"]) for r in adjusted.residual}
            return {
                (scriptadjust._cls_of(p["gid"], els.get(p["gid"])), p["prop"])
                for p in patches
                if (p["gid"], p["prop"]) not in residual
            }
        finally:
            worker.override(stem, [])


def test_exported_script_replays_to_the_edited_figure(adj):
    worker, name, tmp, mans = adj
    assert WORKER_PY is not None

    # CI 与默认：固定例子集（derandomize，flaky 零容忍）；本地找 bug 时 `TAVOTTO_PROP_EXAMPLES=80` 放大并改为真随机
    n = int(os.environ.get("TAVOTTO_PROP_EXAMPLES", "0"))

    @settings(
        max_examples=n or 14, deadline=None, derandomize=not n, database=None, print_blob=True
    )
    @given(data=st.data())
    def prop(data):
        stem = data.draw(st.sampled_from(STEMS), label="图")
        patches = data.draw(edits(mans[stem], _accept), label="编辑")
        _STATS["examples"] += 1
        els = {e["gid"]: e for e in mans[stem]["elements"]}
        keys = _roundtrip(worker, name, tmp, stem, patches, els)
        if keys:
            _STATS["written"] += 1
            _WRITTEN_KEYS.update(keys)

    prop()
    # 不空转：一半以上的例子里真有键写进了脚本并过了验证
    assert _STATS["written"] * 2 >= _STATS["examples"], _STATS
    # 广度：这一轮写进脚本并验过的键不能只有一两个
    assert len(_WRITTEN_KEYS) >= 8, _WRITTEN_KEYS
