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

import json
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
    try:
        obs = cb.observe(sc, tmp_path, needs_absent=cs.NEEDS_ABSENT.get(scenario_id, ()))
    except cb.PreconditionUnmet as exc:
        pytest.skip(str(exc))
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


# ---------------------------------------------------------------- Codex 评审（PR #1023）追加


def _obs(**facts):
    return cb.Observation(
        scenario="s26_import_cv2_sklearn", final_phase="awaiting_confirmation", facts=facts
    )


def test_dependency_ideal_only_expects_packages_that_are_actually_absent():
    """worker 解释器里已经装了 cv2 或 sklearn 时，依赖计划里不会、也不该出现它——理想判据只对缺的那个提要求。"""
    ideal = cs.IDEAL["s26_import_cv2_sklearn"]
    only_sklearn = _obs(
        modules_absent=["sklearn"],
        dependency_installs=["scikit-learn"],
        dependency_missing={"sklearn": "scikit-learn"},
    )
    assert ideal(only_sklearn) == []
    # 缺的两个里只给出一个 = 仍然是未达成
    both_absent = _obs(
        modules_absent=["cv2", "sklearn"],
        dependency_installs=["scikit-learn"],
        dependency_missing={"sklearn": "scikit-learn"},
    )
    assert ideal(both_absent)


def test_summarize_rejects_a_directory_that_mixes_runs(tmp_path):
    def rec(sid, sha, run_id):
        return {
            "scenario": sid,
            "source_sha": sha,
            "run_id": run_id,
            "ideal_outcome": "figure",
            "ideal_met": True,
            "observed": {
                "first_valid_figure": True,
                "one_shot_success": True,
                "exec_count": 1,
                "timings_s": {"total": 1.0},
            },
        }

    for name, row in {
        "a": rec("a", "sha1", "run1"),
        "b": rec("b", "sha2", "run1"),
    }.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(row), "utf-8")
    assert cb.main(["summarize", str(tmp_path)]) == 2  # 两个修订混在一起
    (tmp_path / "b.json").write_text(json.dumps(rec("b", "sha1", "run2")), "utf-8")
    assert (
        cb.main(["summarize", str(tmp_path)]) == 2
    )  # 同一修订但两次运行混在一起（冒烟跑盖在全量上）
    (tmp_path / "b.json").write_text(json.dumps(rec("b", "sha1", "run1")), "utf-8")
    assert cb.main(["summarize", str(tmp_path)]) == 0


def test_write_record_replaces_nothing_from_other_runs_silently(tmp_path):
    """record() 必须带 run_id，summarize 靠它识别混跑。"""
    sc = cs.SCENARIOS["s01_plain"]
    rec = cb.record(cb.Observation(scenario=sc.id), sc, [], None)
    assert rec["run_id"] and rec["source_sha"]


def test_readonly_precondition_is_verified_not_assumed(tmp_path):
    """chmod 在 Windows 上不会让目录只读（root 下的 POSIX 也不会）；前提不成立必须报 PreconditionUnmet，而不是跑出假结果。"""
    d = tmp_path / "ro"
    d.mkdir()
    cb.make_readonly(d)
    try:
        cb.assert_readonly(d)
    except cb.PreconditionUnmet:
        # Windows / POSIX root：chmod 挡不住写入，正是要识别的那种环境——正例前提不成立，只验反例
        readonly_works = False
    else:
        readonly_works = True
    finally:
        cb.restore_writable(d)
    if not readonly_works:
        assert os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), (
            "普通 POSIX 用户下 chmod(0o555) 应当让目录只读"
        )
    d2 = tmp_path / "rw"
    d2.mkdir()
    with pytest.raises(cb.PreconditionUnmet):
        cb.assert_readonly(d2)  # 根本没设只读：探针写得进去 -> 前提不成立


# ---------------------------------------------------------------- 第三轮（PR #1023 复评）


def _good_render(_asset):
    return {
        "manifest": {"elements": [{"role": "title", "editable": [{"prop": "text", "value": "T"}]}]}
    }


def test_a_captured_figure_counts_only_after_its_render_validates():
    """会话说「捕获了」不等于「有效 Figure」：渲染失败 / manifest 畸形 / 没有元素，都不能计入成功。"""
    captured = [
        {"asset_id": "ok"},
        {"asset_id": "boom"},
        {"asset_id": "bad"},
        {"asset_id": "empty"},
    ]

    def render(asset):
        if asset == "boom":
            raise RuntimeError("render 500")
        if asset == "bad":
            return {"nope": 1}
        if asset == "empty":
            return {"manifest": {"elements": []}}
        return _good_render(asset)

    valid, titles, errors = cb.validate_captured(render, captured)
    assert valid == 1 and titles == ["T"]
    assert sorted(e["asset_id"] for e in errors) == ["bad", "boom", "empty"]


def test_an_unrenderable_capture_is_not_a_first_valid_figure():
    obs = cb.Observation(scenario="x", final_phase="completed", figures_valid=0, exec_count=1)
    obs.figures_captured = 1
    obs.render_errors = [{"asset_id": "a", "error": "RuntimeError"}]
    assert obs.first_valid_figure is False and obs.one_shot_success is False


def test_probe_uses_the_interpreter_the_isolated_app_uses():
    # 显式覆盖：转发给 app，探针也探它
    assert cb.app_worker_overrides({"TAVOTTO_WORKER_PYTHON": "/x/py", "OTHER": "1"}) == {
        "TAVOTTO_WORKER_PYTHON": "/x/py"
    }
    assert cb.app_worker_overrides({"OTHER": "1"}) == {}
    assert cb.probe_interpreter({"TAVOTTO_WORKER_PYTHON": "/x/py"}, "/app/py") == "/x/py"
    # 没有覆盖：问 app 实际选了哪个
    assert cb.probe_interpreter({}, "/app/py") == "/app/py"
    assert cb.probe_interpreter({}, "") is None
