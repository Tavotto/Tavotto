"""导出修改后的 .py（ADR 0171）：整图扫描放不下的键，按组单独扫（刻度、边框、色条刻度）。"""

from __future__ import annotations

import pytest

from support.scriptadjust_harness import (
    HELD_OUT_SHARDS,
    held_out_replays,
    real_worker,
)

pytest_plugins = ("support.scriptadjust_harness",)


@real_worker
@pytest.mark.parametrize("group", HELD_OUT_SHARDS["a"])
def test_held_out_fields_replay_identically(tmp_path, group):
    """整图扫描里刻意不放的键（`_HELD_OUT` / `_HELD_OUT_KEYED`）：每张图上这一组一起改，照样要等价。"""
    held_out_replays(tmp_path, group)
