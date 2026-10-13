"""#951：≤3.10 的 fill 撤销写回花纹色，要识别「创建时的记录」是否已被屏障之间的用户改动作废。

matplotlib ≤3.10 的 `Patch.set_fill(False)` 把 `_hatch_color` 改成 `patch.edgecolor`，撤销时
`_set_patch_fill` 把记着的创建时的值写回（#874）。那条记录原先永久有效：用户在 native 屏障之间
改过花纹色，撤销 fill 仍写回旧值，盖掉用户的新颜色。现在每次 `apply` 收尾盖上「写完的样子」，
下一次 fill 编辑发现对不上就按实况重记——与 ≥3.11 的 `_HATCH_BASE` 同一套语义。

探针 `tests/support/hatch_fill_legacy_probe.py` 在 worker 解释器里直接驱动 `overrides.apply`；
≥3.11 有 `set_hatchcolor`，走另一条路径（`test_hatch_mode_restored.py`），本文件 skip。
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
PROBE = os.path.join(os.path.dirname(__file__), "support", "hatch_fill_legacy_probe.py")


@pytest.fixture(scope="module")
def report():
    out = subprocess.run(  # noqa: S603 — 测试里起 worker 解释器跑探针
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    rep = json.loads(out.strip().splitlines()[-1])
    if rep["has_set_hatchcolor"]:
        pytest.skip(f"matplotlib {rep['matplotlib']} 有 set_hatchcolor：不走 ≤3.10 的写回")
    return rep


def test_without_external_change_the_creation_value_is_written_back(report):
    c = report["untouched"]
    assert not c["warnings"], c["warnings"]
    assert c["fill_after_clear"], "撤销后 fill 没回到 True"
    assert c["during"] != c["created"], "前提不成立：set_fill(False) 没改花纹色"
    assert c["after_clear"] == c["created"], "没人动过花纹色：撤销要写回创建时的值（#950 行为）"


def test_user_hatch_color_set_between_barriers_survives_the_fill_undo(report):
    c = report["user_edit"]
    assert not c["warnings"], c["warnings"]
    assert c["fill_after_clear"]
    assert c["after_clear"] == report["user_color"], "用户在屏障之间改的花纹色被写回的旧值盖掉了"
    assert c["after_clear"] != c["created"]


def test_user_color_adopted_even_when_the_next_apply_has_no_fill_edit(report):
    """Codex #993 r4259898020：撤销 fill 后无关 override 留着，屏障间改色，下一批 apply 不含 fill 编辑。"""
    c = report["unrelated_between"]
    assert not c["warnings"], c["warnings"]
    assert c["after_clear"] == report["user_color"], (
        "收尾盖章把用户的值盖成了「写完的样子」，之后写回旧色"
    )
