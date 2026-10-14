"""CompatBench V2 首次出图基线（Compatibility-first Execution 的「before」）——只观测，不改产品行为。

每个场景经真实公共入口（`python -m tavotto` + 会话认证 -> 项目扫描 -> 准备会话 -> 认领 `run`）跑一遍，记录：
首次有效出图、一次成功、**真实执行次数（脚本自写的项目外日志）**、有效 Figure、图内语义真值（标题）、分段耗时。
回退率 / 干预次数 / 端到端 p50 p95 现在拿不到，字段是 `null`（未观测），不是 0；不加遥测。

判据见 `support/compat_v2_scenarios.py` 顶部：有 `main_baseline` 的场景要求「理想未达成 **且** 形状与登记逐项相等」——
行为修好了会红（逼着升格为对照组），失败换了样子也会红；不靠 `xfail(strict)`（它不防失败原因变化）。

默认只跑冒烟子集（约 2 分钟）；`TAVOTTO_COMPAT_V2_ALL=1` 跑全部。结果 JSON 写进 `TAVOTTO_COMPAT_V2_RESULTS`
（不设则写 tmp_path）；汇总：`python tests/support/compatbench_v2.py summarize DIR --out FILE`。
"""

from __future__ import annotations

import os

import pytest

from support import compat_v2_scenarios as cs, compatbench_v2 as cb
from tavotto.engine import pool as _pool

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

_ALL = os.environ.get("TAVOTTO_COMPAT_V2_ALL", "").strip() not in ("", "0")
RUN_IDS = tuple(cs.SCENARIOS) if _ALL else cs.SMOKE


def test_every_fixture_has_a_scenario_and_vice_versa():
    on_disk = {p.name for p in cb.FIXTURES.iterdir() if p.is_dir()}
    assert on_disk == set(cs.SCENARIOS), on_disk ^ set(cs.SCENARIOS)
    assert set(cs.SMOKE) <= set(cs.SCENARIOS)
    for sid, sc in cs.SCENARIOS.items():
        assert (cb.FIXTURES / sid / "script.py").is_file()
        assert (
            sc.failure_mode
            and sc.expected_strategy
            and sc.ideal_exec_count_max >= sc.ideal_exec_count
        )
        head = (cb.FIXTURES / sid / "script.py").read_text("utf-8").split('"""')[1]
        # 每个夹具自述：原始失败模式 / 预期策略 / 理想执行次数
        for key in ("原始失败模式：", "预期策略：", "理想执行次数："):
            assert key in head, (sid, key)
        if sc.main_baseline is not None:
            assert set(sc.main_baseline) == set(cs.SHAPE_KEYS), sid


def test_unobserved_metrics_are_null_not_zero():
    summary = cb.summarize(
        [
            {
                "scenario": "x",
                "ideal_outcome": "figure",
                "ideal_met": True,
                "observed": {
                    "first_valid_figure": True,
                    "one_shot_success": True,
                    "exec_count": 1,
                    "timings_s": {"total": 1.0},
                },
            }
        ]
    )
    for key in ("fallback_rate", "intervention_count", "e2e_first_figure_ms_p50_p95"):
        assert summary[key] is None, key
        assert key in summary["unobserved"] or key == "e2e_first_figure_ms_p50_p95"
    assert set(summary["unobserved"]) >= {"fallback_rate", "intervention_count"}


@needs_worker
@pytest.mark.parametrize("scenario_id", RUN_IDS)
def test_scenario_baseline(scenario_id, tmp_path):
    sc = cs.SCENARIOS[scenario_id]
    obs = cb.observe(sc, tmp_path)
    missing = cs.IDEAL[scenario_id](obs)
    shape = cs.baseline_shape(obs)
    baseline_ok = None if sc.main_baseline is None else shape == sc.main_baseline
    out = cb.results_dir() or (tmp_path / "results")
    cb.write_record(cb.record(obs, sc, missing, baseline_ok), out)

    # 检查阶段零执行（C23）——所有场景
    assert obs.facts["exec_count_after_check"] == 0, "检查阶段执行了用户代码"
    if sc.main_baseline is None:
        assert not missing, f"对照组退步了: {missing}\n{obs.to_json()}"
        return
    assert missing, (
        f"{scenario_id} 的理想行为已达成——请删除它的 main_baseline，升格为对照组（别让它悄悄继续「已知失败」）"
    )
    assert shape == sc.main_baseline, (
        f"{scenario_id} 失败的样子变了（不是登记的那一种）:\n实测 {shape}\n登记 {sc.main_baseline}"
    )
