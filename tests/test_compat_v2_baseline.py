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
    cb.assert_no_execution_before_run(obs)
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
    facts.setdefault("actions_offered_final", ["recheck", "prepare_dependencies"])
    return cb.Observation(
        scenario="s26_import_cv2_sklearn",
        final_phase="awaiting_confirmation",
        outcome_kind="pending",
        problem_codes=["dependency_preparation_required"],
        facts=facts,
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


# ---------------------------------------------------------------- 第四轮：判据收紧（PR #1023）


def _o(**kw):
    base = dict(
        scenario="x",
        final_phase="action_required",
        outcome_kind="failed",
        problem_codes=["script_error"],
        error_type="ZeroDivisionError",
        exec_count=1,
    )
    base.update(kw)
    return cb.Observation(**base)


def test_stop_scenarios_reject_a_success_projection():
    """错误码还在、但会话把它投影成 completed / succeeded：UI 说成功而不是停下解释，不能算对照组达标。"""
    ideal = cs.IDEAL["s16a_zero_division"]
    assert ideal(_o()) == []
    assert ideal(_o(final_phase="completed"))
    assert ideal(_o(outcome_kind="succeeded"))
    no_fig = cs.IDEAL["s21_no_figure"]
    ok = _o(
        final_phase="partial",
        outcome_kind="execution_finished_no_figure",
        problem_codes=["no_figures_captured"],
        error_type=None,
    )
    assert no_fig(ok) == []
    assert no_fig(_o(**{**ok.__dict__, "final_phase": "completed"}))
    assert cs.IDEAL["s18_readonly_dir"](_o(final_phase="completed", outcome_kind="succeeded"))


def test_side_effect_scenario_requires_the_effect_to_actually_happen():
    ideal = cs.IDEAL["s17_side_effect_then_limit"]
    fig = dict(
        final_phase="completed",
        outcome_kind="succeeded",
        problem_codes=[],
        error_type=None,
        figures_valid=1,
        figures_captured=1,
        titles=["bytes>0=True"],
    )
    assert ideal(_o(**fig, side_effect_count=1)) == []
    assert ideal(_o(**fig, exec_count=2, side_effect_count=2)) == []
    assert ideal(_o(**fig, side_effect_count=None))  # 副作用库坏了 / 没写
    assert ideal(_o(**fig, side_effect_count=0))
    assert ideal(_o(**fig, exec_count=2, side_effect_count=1))  # 次数对不上 = 日志或夹具坏了


def test_figure_scenarios_reject_a_failed_outcome_with_a_figure():
    ideal = cs.IDEAL["s01_plain"]
    ok = dict(final_phase="completed", outcome_kind="succeeded", problem_codes=[], figures_valid=1)
    assert ideal(_o(**ok, error_type=None)) == []
    assert ideal(_o(**{**ok, "outcome_kind": "failed"}, error_type=None))


def test_dependency_scenarios_require_the_prepare_action_and_the_code():
    ideal = cs.IDEAL["s26_import_cv2_sklearn"]
    facts = {
        "dependency_installs": ["opencv-python", "scikit-learn"],
        "dependency_missing": {"cv2": "opencv-python", "sklearn": "scikit-learn"},
        "actions_offered_final": ["recheck", "prepare_dependencies", "run_when_ready"],
    }
    ok = dict(
        final_phase="awaiting_confirmation",
        outcome_kind="pending",
        problem_codes=["dependency_preparation_required"],
        error_type=None,
        exec_count=0,
        facts=facts,
    )
    assert ideal(_o(**ok)) == []
    assert ideal(_o(**{**ok, "facts": {**facts, "actions_offered_final": ["recheck"]}}))
    assert ideal(_o(**{**ok, "problem_codes": []}))


def test_partial_scenario_requires_a_clean_partial_projection():
    ideal = cs.IDEAL["s25_partial_capture"]
    ok = dict(
        final_phase="partial",
        outcome_kind="succeeded",
        problem_codes=[],
        error_type=None,
        figures_valid=1,
        figures_captured=1,
        failed_figures=["bad"],
    )
    assert ideal(_o(**ok)) == []
    assert ideal(_o(**{**ok, "render_errors": [{"asset_id": "a", "error": "X"}]}))


def test_baseline_shape_pins_the_outcome_kind_and_failed_figures():
    assert {"outcome_kind", "failed_figures"} <= set(cs.SHAPE_KEYS)


def test_zero_execution_is_asserted_after_every_check_not_only_the_first():
    obs = cb.Observation(scenario="x")
    obs.facts["exec_counts_at_checks"] = [0, 0, 0]
    cb.assert_no_execution_before_run(obs)
    obs.facts["exec_counts_at_checks"] = [0, 1, 1]  # recheck 之后开始执行用户代码
    with pytest.raises(AssertionError, match="执行了用户代码"):
        cb.assert_no_execution_before_run(obs)
    obs.facts["exec_counts_at_checks"] = []  # 没记录 = 没验证，同样不通过
    with pytest.raises(AssertionError, match="没有记录"):
        cb.assert_no_execution_before_run(obs)


# ---------------------------------------------------------------- 第五轮（PR #1023）


def test_partial_scenario_requires_success_outcome_and_clean_codes():
    ideal = cs.IDEAL["s25_partial_capture"]
    ok = dict(
        final_phase="partial",
        outcome_kind="succeeded",
        problem_codes=[],
        error_type=None,
        figures_valid=1,
        figures_captured=1,
        failed_figures=["bad"],
    )
    assert ideal(_o(**ok)) == []
    # 指认了 bad，但把会话投影成失败 / 无图 / 带错误码：坏的 UI 结果不能算理想
    assert ideal(_o(**{**ok, "outcome_kind": "failed"}))
    assert ideal(_o(**{**ok, "outcome_kind": "execution_finished_no_figure"}))
    assert ideal(_o(**{**ok, "problem_codes": ["script_error"]}))
    assert ideal(_o(**{**ok, "failed_figures": ["bad", "good"]}))  # 把成功那张也说成失败


def test_dependency_plan_must_match_exactly_the_probed_absent_set():
    ideal = cs.IDEAL["s26_import_cv2_sklearn"]
    base = dict(
        final_phase="awaiting_confirmation",
        outcome_kind="pending",
        problem_codes=["dependency_preparation_required"],
        error_type=None,
        exec_count=0,
    )

    def facts(absent, installs, missing):
        return {
            "modules_absent": absent,
            "dependency_installs": installs,
            "dependency_missing": missing,
            "actions_offered_final": ["recheck", "prepare_dependencies"],
        }

    # cv2 已装、sklearn 缺：只该装 scikit-learn
    good = facts(["sklearn"], ["scikit-learn"], {"sklearn": "scikit-learn"})
    assert ideal(_o(**base, facts=good)) == []
    # 退化的规划器把已装的 cv2 也拉进来：不能放过
    regress_installs = facts(
        ["sklearn"], ["opencv-python", "scikit-learn"], {"sklearn": "scikit-learn"}
    )
    assert ideal(_o(**base, facts=regress_installs))
    regress_missing = facts(
        ["sklearn"],
        ["scikit-learn"],
        {"sklearn": "scikit-learn", "cv2": "opencv-python"},
    )
    assert ideal(_o(**base, facts=regress_missing))


# ---------------------------------------------------------------- 第六轮（PR #1023）


def _fields_read_by_the_ideal_predicates() -> set[str]:
    """静态扫 compat_v2_scenarios：理想判据（及其辅助函数）读了 Observation 的哪些字段 / facts 键。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(cs))
    params = {"obs", "o"}
    fields: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in params
        ):
            fields.add(node.attr)
        # o.facts.get("key") / obs.facts["key"]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "facts"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            fields.add(f"facts.{node.args[0].value}")
    # `facts` 是容器（各键已展开成 facts.<k>）；to_json 是方法，不是字段
    return fields - {"facts", "to_json"}


# 派生属性 -> 它由哪些形状字段决定（派生量本身不必重复登记）
_DERIVED = {"first_valid_figure": {"final_phase", "figures_valid"}}


def test_every_field_the_ideal_predicates_read_is_pinned_in_the_baseline_shape():
    """理想判据检查的字段，必须全部出现在登记的基线形状里，没有例外：
    否则「失败的样子」变了（例如从无图变成有图但渲染不出）基线不会红。"""
    read = _fields_read_by_the_ideal_predicates()
    expanded = set()
    for f in read:
        expanded |= _DERIVED.get(f, {f})
    missing = expanded - set(cs.SHAPE_KEYS)
    assert not missing, f"理想判据读了但基线形状没钉的字段: {sorted(missing)}"
    # 扫描器自检：确实扫到了东西
    assert {"final_phase", "render_errors", "facts.dependency_installs"} <= read


def test_baseline_shape_pins_render_failures_and_every_registered_baseline_has_all_keys():
    assert {"render_errors", "figures_captured", "titles"} <= set(cs.SHAPE_KEYS)
    for sid, sc in cs.SCENARIOS.items():
        if sc.main_baseline is not None:
            assert set(sc.main_baseline) == set(cs.SHAPE_KEYS), sid
    # 「有图但渲染不出」与「无图」是两种不同的失败形状
    a = _o(figures_captured=0, render_errors=[])
    b = _o(figures_captured=1, render_errors=[{"asset_id": "a", "error": "X"}])
    assert cs.baseline_shape(a) != cs.baseline_shape(b)


def test_baseline_shape_has_exactly_the_pinned_keys_including_facts():
    shape = cs.baseline_shape(_o(facts={"dependency_installs": ["x"]}))
    assert set(shape) == set(cs.SHAPE_KEYS)
    assert shape["facts.dependency_installs"] == ["x"]
    assert shape["facts.modules_absent"] is None
