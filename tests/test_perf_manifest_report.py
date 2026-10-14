"""Agent manifest 阶段报告：样本完整性、时间守恒、脱敏与失败关闭。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bench_render as BR  # noqa: E402
import perf_manifest_report as R  # noqa: E402


def sample():
    return {
        "manifest_ms": 120,
        "manifest_prepare_ms": 100,
        "manifest_layout_ms": 40,
        "manifest_measure_ms": 60,
        "manifest_final_geometry_ms": 20,
        "canvas_draw_ms": 30,
        "wall_ms": 180,
    }


def report(n=9):
    return {
        "meta": {"figures": "/home/secret-user/Project", "python": "/Users/A/.venv/bin/python"},
        "rows": [
            {
                "id": "private_study_α.pdf",
                "plane": "Python",
                "hot_n": n,
                "hot_samples": [sample() for _ in range(n)],
            }
        ],
    }


def test_complete_anonymous_report_has_real_sample_p95_not_a_false_comparison():
    result = R.summarize(report())
    assert result["status"] == "recorded"
    assert result["comparison_verdict"] == "not_checked_without_pinned_same_machine_baseline"
    assert result["scenarios"][0]["phases"]["manifest_ms"]["p95_ms"] == 120
    assert result["scenarios"][0]["sample_count"] == 9
    serialized = json.dumps(result)
    for hidden in ["private_study", "secret-user", "/Users", "Project"]:
        assert hidden not in serialized


def test_missing_measurements_or_lost_samples_cannot_be_labeled_recorded():
    source = report()
    source["rows"][0]["hot_samples"][0].pop("manifest_final_geometry_ms")
    assert R.summarize(source)["status"] == "incomplete"
    source = report(5)
    assert R.summarize(source)["status"] == "incomplete"
    source = report()
    source["rows"][0]["hot_n"] = 10
    assert R.summarize(source)["status"] == "incomplete"


def test_malformed_stage_sum_negative_or_nan_fails_closed():
    for field, value in [
        ("manifest_ms", 42),
        ("manifest_ms", -1),
        ("manifest_measure_ms", float("nan")),
    ]:
        source = report()
        source["rows"][0]["hot_samples"][0][field] = value
        assert R.summarize(source)["status"] == "incomplete"


def test_bench_numeric_whitelist_rejects_user_strings_boolean_and_infinity():
    row = {
        **sample(),
        "script": "secrets.py",
        "manifest_layout_ms": True,
        "canvas_draw_ms": float("inf"),
    }
    filtered = BR.numeric_hot_sample(row)
    assert "script" not in filtered
    assert "manifest_layout_ms" not in filtered
    assert "canvas_draw_ms" not in filtered
    assert filtered["manifest_ms"] == 120


def test_foreign_or_empty_report_is_not_pass():
    assert R.summarize({"rows": []})["status"] == "incomplete"
    try:
        R.summarize({"path": "secrets.py"})
    except ValueError:
        pass
    else:
        raise AssertionError("foreign JSON should be rejected")


def test_manifest_stage_coverage_fails_when_timing_is_mostly_unaccounted_for():
    """即使 prepare + final 等于总量，也不允许 layout/measure 都是假零。"""
    source = report()
    for sample_row in source["rows"][0]["hot_samples"]:
        sample_row["manifest_layout_ms"] = 0
        sample_row["manifest_measure_ms"] = 0
    assert R.summarize(source)["status"] == "incomplete"
    source = report()
    for sample_row in source["rows"][0]["hot_samples"]:
        sample_row["manifest_layout_ms"] = 105
        sample_row["manifest_measure_ms"] = 30
    assert R.summarize(source)["status"] == "incomplete"


def test_absurdly_large_integer_timings_fail_closed_without_crashing(tmp_path):
    """400 位合法 JSON 整数不能触发 math.isfinite 的 OverflowError。"""
    data = report()
    data["rows"][0]["hot_samples"][0]["manifest_ms"] = 10**400
    assert R.summarize(data)["status"] == "incomplete"

    raw = tmp_path / "input.json"
    result = tmp_path / "summary.json"
    raw.write_text(json.dumps(data), encoding="utf-8")
    assert R.main([str(raw), "--json", str(result)]) == 2
    assert json.loads(result.read_text(encoding="utf-8"))["status"] == "incomplete"
