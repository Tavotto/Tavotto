"""更新的 Tavotto 写下、这一版认不出的 override：不应用、不报 warning，项目照样平稳打开。

背景（2026-11-19，#2060 核兼容时实测）：旧引擎遇到不认识的 override 键 → 图照常打开，但 warnings 里出
「属性不支持: gid.prop」，写回被挡、界面报警告。每加一种新的编辑方式（新 prop），用旧版本打开新项目都
这样。从这一版起：**这一版任何一类元素都不认得的 prop 名**跳过、记进 `future_props`（不是 warning）；
认得这个名字、只是这一类元素不支持的，照旧报「属性不支持」；认得的键值不对，照旧报应用失败。

合并大队长要守的两条：只跳过不认识的键（认识的键值不对照旧报错）；跳过的键原样留在项目里——引擎只读不写
override 列表，`patchspec` 不按 prop 名筛，前端整份保存（见写回用例）。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import pytest

from tavotto.engine import patchspec, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "future_keys.py"
SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.plot([0, 1], [0, 1], color="#1f77b4")
    ax.text(0.5, 0.5, "note")
    fig.savefig("Fut.pdf")
"""

LINE = "axes_0.lines_0"
TEXT = "axes_0.texts_0"
FUTURE = {"gid": LINE, "prop": "bend_magic_from_the_future", "value": [0.1, 0.2, 0.3, 0.4]}


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    d = tmp_path_factory.mktemp("future-keys")
    (d / SCRIPT_NAME).write_text(SCRIPT, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(d), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _field(resp, gid, prop):
    el = next(e for e in resp["manifest"]["elements"] if e["gid"] == gid)
    return next(f["value"] for f in el["editable"] if f["prop"] == prop)


def test_unknown_prop_is_skipped_without_a_warning_and_the_rest_still_applies(hot):
    resp = hot.override("Fut", [FUTURE, {"gid": LINE, "prop": "color", "value": "#ff0000"}])
    assert resp["warnings"] == []
    assert resp["future_props"] == [f"{LINE}.bend_magic_from_the_future"]
    assert _field(resp, LINE, "color") == "#ff0000"  # 同一份列表里认得的照常生效
    back = hot.override("Fut", [])
    # 没有认不出的键时不带这个字段（响应形状与从前相同）
    assert back["warnings"] == [] and "future_props" not in back
    assert _field(back, LINE, "color") == "#1f77b4"


def test_known_prop_on_the_wrong_kind_of_element_still_warns(hot):
    """`markersize` 这一版认得（线有），文字没有：照旧「属性不支持」，不当成未来的键吞掉。"""
    resp = hot.override("Fut", [{"gid": TEXT, "prop": "markersize", "value": 5}])
    assert resp["warnings"] == [f"属性不支持: {TEXT}.markersize"]
    assert "future_props" not in resp
    hot.override("Fut", [])


def test_known_prop_with_a_bad_value_still_warns(hot):
    resp = hot.override("Fut", [{"gid": LINE, "prop": "color", "value": "not-a-colour"}])
    assert resp["warnings"], "认得的键值不对照旧报出来"
    assert "future_props" not in resp
    hot.override("Fut", [])


def test_missing_element_still_warns_even_for_an_unknown_prop(hot):
    """元素都不存在：照旧「元素不存在」（这一版判不出是新版本才登记的元素还是脚本改了）。"""
    resp = hot.override("Fut", [{**FUTURE, "gid": "axes_9.lines_9"}])
    assert resp["warnings"] and "axes_9.lines_9" in resp["warnings"][0]
    hot.override("Fut", [])


def test_export_reports_future_props_and_the_patch_list_keeps_them(hot, tmp_path):
    out = tmp_path / "Fut.pdf"
    resp = hot.export("Fut", [FUTURE], str(out), fmt="pdf", dpi=72)
    assert resp["warnings"] == []
    assert resp["future_props"] == [f"{LINE}.bend_magic_from_the_future"]
    assert out.exists() and out.stat().st_size > 0
    # 规范化不按 prop 名筛：项目文件里存的那条原样还在
    assert patchspec.canonicalize([FUTURE]) == [FUTURE]
