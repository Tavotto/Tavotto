"""Agent 性能对照工具：不把缺失基线/错机器/少样本误报成 pass。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_agent as A  # noqa: E402


def report(n=16, value=20, browser="chromium"):
    return {
        "schema": A.SCHEMA,
        "iterations": n,
        "environment": {
            "platform": "linux",
            "arch": "x64",
            "browser": browser,
            "browser_major": 125,
            "browser_version": "125.0.0.0",
            "viewport_w": 1280,
            "viewport_h": 720,
            "dpr": 1,
            "headless": True,
            "os_release": "test-kernel",
            "cpu_model": "test-cpu",
            "cpu_count": 4,
            "total_mem_gib": 16,
        },
        "scenarios": [
            {
                "id": key,
                "two_raf_ms": [
                    value for _ in range(n * (4 if key == "dialog.presets_open" else 1))
                ],
            }
            for key in sorted(A.ALLOWED_SCENARIOS)
        ],
    }


def test_valid_complete_report():
    a = A.summarize(report())
    assert a["status"] == "ok"
    assert a["coverage"] == "browser_webview_proxy_only"
    assert a["scenarios"]["symbol.alpha"]["count"] == 16


def test_missing_scenario_or_browser_is_not_pass():
    x = report()
    x["scenarios"].pop()
    assert A.summarize(x)["status"] == "incomplete"
    y = report(browser="webkit")
    assert A.compare(y, report())["status"] == "incomparable"


def test_regression_requires_relative_and_absolute_delta():
    assert A.compare(report(value=51), report(value=20))["status"] == "regression"
    assert A.compare(report(value=23), report(value=20))["status"] == "pass"
    assert A.compare(report(value=11.0), report(value=8.0))["status"] == "pass"


def test_too_few_samples_not_comparable():
    assert A.compare(report(n=3), report(n=3))["status"] == "incomparable"


def test_summary_rejects_untrusted_names_and_boolean_times():
    data = report()
    data["scenarios"].append({"id": "private.py", "two_raf_ms": [100] * 16})
    data["scenarios"][1]["two_raf_ms"][0] = True
    s = A.summarize(data)
    assert "private.py" not in json.dumps(s)
    assert s["status"] == "incomplete"


def test_load_checks_schema_and_file_size(tmp_path):
    p = tmp_path / "measure.json"
    p.write_text(json.dumps(report()), encoding="utf-8")
    assert A.load(p)["schema"] == A.SCHEMA
    p.write_text(json.dumps({"schema": "alien"}), encoding="utf-8")
    try:
        A.load(p)
    except ValueError:
        pass
    else:
        raise AssertionError("untrusted schema accepted")


def test_dialog_sample_multiplier_is_four_per_iteration():
    r = report(n=12)
    summary = A.summarize(r)
    assert summary["status"] == "ok"
    assert summary["scenarios"]["dialog.presets_open"]["expected_count"] == 48
    assert summary["scenarios"]["dialog.presets_open"]["count"] == 48
    r["scenarios"][0]["two_raf_ms"].pop()
    assert A.summarize(r)["status"] == "incomplete"


def test_environment_missing_does_not_compare_as_equal():
    """两份报告都缺浏览器版本/CPU 不等于环境真实一致。"""
    a = report()
    b = report()
    del a["environment"]["cpu_model"]
    del b["environment"]["cpu_model"]
    assert A.summarize(a)["status"] == "incomplete"
    assert A.compare(a, b)["status"] == "incomparable"


def test_browser_patch_version_is_part_of_comparison_identity():
    a, b = report(), report()
    b["environment"]["browser_version"] = "125.0.1.0"
    assert A.compare(a, b)["status"] == "incomparable"


def test_duplicate_or_unknown_scenario_cannot_be_ignored():
    a = report()
    a["scenarios"].append(dict(a["scenarios"][0]))
    assert A.summarize(a)["status"] == "incomplete"
    b = report()
    b["scenarios"].append({"id": "secret_work.py", "two_raf_ms": [0] * 16})
    assert A.summarize(b)["status"] == "incomplete"
    assert "secret_work.py" not in json.dumps(A.summarize(b))


def test_run_summary_explicitly_says_no_baseline_was_checked():
    assert A.summarize(report())["regression_status"] == "not_checked_without_baseline"


def test_agent_drag_cannot_be_confused_with_symbol_baseline():
    """默认基线比较只评估符号场景；不能拿普通 UI 指标宣布拖动通过。"""
    a = A.summarize(report())
    assert "native_window_drag" in a["not_covered"]
    assert a["coverage"] == "browser_webview_proxy_only"


def test_two_playwright_runs_do_not_share_cleanup_directory():
    """普通 UI 产物落在 test-results/perf-agent，拖动测量必须另用输出目录。"""
    assert A.DRAG_E2E_OUTPUT != "test-results"
    assert not A.DRAG_E2E_OUTPUT.startswith("test-results/")


def test_composed_summary_does_not_hide_later_drag_failure():
    """符号先绿不等于完整任务绿：这是 Agent 自动接收报告的闸。"""
    symbols = A.summarize(report())
    assert A.compose_run_summary(symbols, "pending")["status"] == "pending"
    result = A.compose_run_summary(symbols, "failed")
    assert result["status"] == "failed"
    assert result["suites"] == {"symbol_ui": "ok", "canvas_drag": "failed"}
    assert A.compose_run_summary(symbols, "ok")["status"] == "ok"
    assert A.compose_run_summary(symbols, "not_requested")["status"] == "ok"
    bad = {**symbols, "status": "incomplete"}
    assert A.compose_run_summary(bad, "pending")["status"] == "incomplete"


def test_actions_baseline_artifact_path_matches_actual_upload_root():
    """Actions 产物上传公共根是 web/；下载后包含 test-results/ 层级。"""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/performance-agent.yml"
    ).read_text(encoding="utf-8")
    assert "web/test-results/perf-agent/**" in workflow
    assert '"$target/test-results/perf-agent/run.json"' in workflow
    assert '"$target/perf-agent/run.json"' not in workflow



def test_browser_suite_failure_has_an_unambiguous_machine_readable_summary():
    failure = A.failed_run_summary(True)
    assert failure["status"] == "failed"
    assert failure["suites"] == {"symbol_ui": "failed", "canvas_drag": "not_started"}
    assert failure["comparison_verdict"] == "not_checked"
    assert failure["scenarios"] == {}
    assert A.failed_run_summary(False)["suites"]["canvas_drag"] == "not_requested"


def test_agent_drag_timeout_preserves_passed_symbol_suite(tmp_path, monkeypatch):
    """浏览器符号通过而拖动超时：Agent 的 summary 不得归咎于已成功的符号。"""
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def fake_run(argv, **kwargs):
        if "playwright.perf.config.ts" in argv:
            output.write_text(json.dumps(report()), encoding="utf-8")
            return SimpleNamespace(returncode=0)
        if "playwright.config.ts" in argv:
            raise subprocess.TimeoutExpired(argv, 900)
        raise AssertionError("unexpected subprocess")

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    rc = A.main(
        ["run", "--include-drag", "--out", str(output), "--summary", str(summary)]
    )
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert rc == 2
    assert saved["status"] == "failed"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "failed"}


def test_requested_baseline_regression_updates_the_same_machine_readable_summary(
    tmp_path, monkeypatch
):
    """有基线时真实回归检测红了，summary 必须一起红。"""
    output = tmp_path / "run.json"
    baseline = tmp_path / "baseline.json"
    summary = tmp_path / "summary.json"
    baseline.write_text(json.dumps(report(value=20)), encoding="utf-8")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    def fake_run(argv, **kwargs):
        assert "playwright.perf.config.ts" in argv
        output.write_text(json.dumps(report(value=55)), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    rc = A.main(
        [
            "run",
            "--out",
            str(output),
            "--baseline",
            str(baseline),
            "--summary",
            str(summary),
        ]
    )
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert rc == 3
    assert saved["status"] == "regression"
    assert saved["regression_status"] == "regression"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "not_requested"}
    assert saved["comparison"]["status"] == "regression"


def test_workflow_checks_each_recent_main_artifact_until_compatible():
    """不能在第一份 Chromium / 不同 CPU 基线就结束 WebKit 的历史搜索。"""
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/performance-agent.yml"
    ).read_text(encoding="utf-8")
    assert 'candidate="$target/test-results/perf-agent/run.json"' in workflow
    assert 'if [[ "$verdict" == "incomparable" ]]; then' in workflow
    assert 'if [[ "$verdict" != "pass" && "$verdict" != "regression" ]]; then' in workflow
