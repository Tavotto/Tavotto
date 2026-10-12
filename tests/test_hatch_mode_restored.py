"""#874 / Codex #950 r4245786615：alpha / fill 编辑与撤空之后，花纹色的**模式**回到「未设」。

钉住（`overrides._with_hatch_pinned`）若把 `_original_hatchcolor` 永久改成显式值，native 会话释放屏障后
用户的 Python 改了 `rcParams['hatch.color']` 再 `set_alpha`，这个 patch 会保留 Tavotto 钉的颜色而不跟随
新的 rc，悄悄改变后续脚本的输出。判据的主语：**用户拿回手的那个 artist**（`apply([])` 之后）的模式与花纹色，
对照一张从没被 Tavotto 碰过的同构图。

探针 `tests/support/hatch_mode_probe.py` 在 worker 解释器里直接驱动 `overrides.apply`（native 与 safe
共用的那一个入口）；≤3.10 没有 `set_hatchcolor`，不适用，用例 skip。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
PROBE = os.path.join(os.path.dirname(__file__), "support", "hatch_mode_probe.py")


@pytest.fixture(scope="module")
def report():
    out = subprocess.run(  # noqa: S603 — 测试里起 worker 解释器跑探针
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    rep = json.loads(out.strip().splitlines()[-1])
    if not rep["has_set_hatchcolor"]:
        pytest.skip(f"matplotlib {rep['matplotlib']} 没有 set_hatchcolor：不存在钉住")
    return rep


CASES = [
    "bar:alpha",
    "series:alpha",
    "rect:alpha",
    "rect:fill",
    "rect:fill+alpha",
    "fill_between:alpha",
    "scatter:alpha",
    # #956：文本背景框 / 图例框 / 线集合
    "text_bbox:alpha",
    "legend_frame:alpha",
    "linecoll:alpha",
    "bar:stale-base",
    "scatter:stale-base",
    # #874 第二步：花纹色可编辑——显式模式只活在编辑期间
    "bar:hatchcolor",
    "series:hatchcolor",
    "rect:hatchcolor",
    "bar:hatchcolor+alpha",
    "series:hatchcolor+alpha",
    "rect:hatchcolor+alpha",
    "bar:hatchcolor-edge",
]


@pytest.mark.parametrize("case", CASES)
def test_edit_then_clear_leaves_the_hatch_mode_unset_and_following_the_rc(report, case):
    c = report["cases"][case]
    assert not c["warnings"], c["warnings"]
    assert c.get("mode_none_during_edit", True), f"{case}：编辑期间模式就不是「未设」了"
    assert c["mode_none_after_clear"], f"{case}：apply([]) 之后 _original_hatchcolor 没回到 None"
    assert c["hatch_after_clear_equals_pristine"], f"{case}：撤空后花纹色不是创建时的值"
    assert c["follows_new_rc_like_pristine"], (
        f"{case}：改 rc 再 set_alpha 没有像从未编辑过那样跟随新 rc"
    )


@pytest.mark.parametrize("case", ["AlphaRect:alpha", "FillRect:fill", "AlphaColl:alpha"])
def test_a_user_subclass_that_installs_its_own_hatch_mode_keeps_it(report, case):
    """子类在 `set_alpha` / `set_fill` 里自己 `set_hatchcolor("blue")`：那是它有意装的模式，
    钉住的还原不许抹掉（Codex #950 r4246080546）。参照物是用户自己直接调同一个子类 setter 往返。"""
    c = report["subclass_cases"][case]
    assert not c["warnings"], c["warnings"]
    assert c["mode_during_edit"] == "blue", (
        f"{case}：编辑期间子类装的模式丢了：{c['mode_during_edit']!r}"
    )
    assert c["mode_after_clear"] == "blue", (
        f"{case}：apply([]) 后子类装的模式被抹成 {c['mode_after_clear']!r}"
    )
    assert c["mode_after_clear_equals_user_calls"], case
    assert c["hatch_follows_like_user_calls"], f"{case}：改 rc 后的花纹色与用户自己调用的结果不同"


def test_a_subclass_setter_that_installs_a_mode_then_raises_keeps_it(report):
    c = report["subclass_cases"]["RaisingRect:alpha"]
    assert c["warnings"], "子类 set_alpha 抛了，apply 应当报 warning"
    assert c["mode_after_edit"] == "blue", f"异常路径抹掉了子类装的模式：{c['mode_after_edit']!r}"


def test_a_subclass_set_hatchcolor_that_copies_the_color_still_gets_the_mode_restored(report):
    """子类重写 `set_hatchcolor` 先复制颜色再存：存进去的对象与钉入的不是同一个，但 alpha 没装模式，
    钉住仍要还原成「未设」（Codex #950 r4246402299：比较的是钉完后实际存进去的那个对象）。"""
    c = report["subclass_cases"]["CopyHatchRect:alpha"]
    assert not c["warnings"], c["warnings"]
    assert c["mode_after_clear"] is None, (
        f"apply([]) 后模式没回到未设：{c['mode_after_clear']!r}——钉住被永久留下"
    )
    assert c["hatch_follows_like_pristine"], "改 rc 后花纹色与从未编辑过的同构图不同"
