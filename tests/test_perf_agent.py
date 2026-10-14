"""Agent 性能对照工具：不把缺失基线/错机器/少样本误报成 pass。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    rc = A.main(["run", "--include-drag", "--out", str(output), "--summary", str(summary)])
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


def test_external_comparison_writes_back_the_run_summary(tmp_path):
    """Actions 单独 compare 一次，原 summary 也必须反映回归，不能继续 not_checked。"""
    current = tmp_path / "run.json"
    baseline = tmp_path / "baseline.json"
    summary = tmp_path / "summary.json"
    outcome = tmp_path / "comparison.json"
    current.write_text(json.dumps(report(value=55)), encoding="utf-8")
    baseline.write_text(json.dumps(report(value=20)), encoding="utf-8")
    summary.write_text(
        json.dumps(A.compose_run_summary(A.summarize(report(value=55)), "ok")),
        encoding="utf-8",
    )
    rc = A.main(
        ["compare", str(current), str(baseline), "--json", str(outcome), "--summary", str(summary)]
    )
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert rc == 3
    assert saved["status"] == "regression"
    assert saved["regression_status"] == "regression"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "ok"}


def test_different_repeat_counts_are_different_canvas_workloads():
    """每轮新增对象且不撤销；10 与 60 轮的 P95 不是同一场景规模。"""
    assert A.compare(report(n=10), report(n=60))["status"] == "incomparable"


@pytest.mark.parametrize("phase", ["symbol", "drag", "analysis"])
@pytest.mark.parametrize("failure", ["nonzero", "missing", "timeout", "oserror"])
def test_run_replaces_previous_success_with_current_failure(tmp_path, monkeypatch, phase, failure):
    """主语是本次命令最终交出的 summary，覆盖每个子进程的三类失败。"""
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(A.compose_run_summary(A.summarize(report()), "ok")))
    output.write_text(json.dumps(report()))
    calls = []

    def execute(argv, **kwargs):
        stage = ("symbol", "drag", "analysis")[len(calls)]
        calls.append(stage)
        assert json.loads(summary.read_text())["status"] == "pending"
        if stage == phase:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            if failure == "oserror":
                raise FileNotFoundError("synthetic_unavailable_tool")
            if failure == "nonzero":
                # 即使失败子进程留下完整形状的产物，退出码也必须阻断成功。
                if stage == "symbol":
                    output.write_text(json.dumps(report()))
                elif stage == "drag":
                    Path(kwargs["env"]["TAVOTTO_PERF_REPORT_OUT"]).write_text("{}")
                else:
                    Path(argv[argv.index("--json") + 1]).write_text("[]")
            return subprocess.CompletedProcess(argv, 1 if failure == "nonzero" else 0)
        if stage == "symbol":
            Path(kwargs["env"]["TAVOTTO_PERF_AGENT_OUTPUT"]).write_text(json.dumps(report()))
        elif stage == "drag":
            Path(kwargs["env"]["TAVOTTO_PERF_REPORT_OUT"]).write_text("{}")
        else:
            raise AssertionError("analysis should be the failing stage")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(A.subprocess, "run", execute)
    assert A.main(["run", "--out", str(output), "--summary", str(summary), "--include-drag"]) == 2
    result = json.loads(summary.read_text())
    assert result["status"] == "failed"
    assert result["suites"]["symbol_ui" if phase == "symbol" else "canvas_drag"] == "failed"
    assert len(calls) == ("symbol", "drag", "analysis").index(phase) + 1


def test_invalid_run_parameters_cannot_leave_previous_success(tmp_path, monkeypatch):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"status": "ok"}))
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("invalid run launched"))
    assert A.main(["run", "--repeat", "2", "--summary", str(summary)]) == 2
    assert json.loads(summary.read_text())["status"] == "failed"


@pytest.mark.parametrize(
    "baseline_ms, baseline_browser, expected, code",
    [
        (20, "chromium", "pass", 0),
        (1, "chromium", "regression", 3),
        (20, "webkit", "incomparable", 3),
    ],
)
def test_run_summary_records_the_baseline_verdict(
    tmp_path, monkeypatch, baseline_ms, baseline_browser, expected, code
):
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report(value=baseline_ms, browser=baseline_browser)))

    def execute(argv, **kwargs):
        Path(kwargs["env"]["TAVOTTO_PERF_AGENT_OUTPUT"]).write_text(json.dumps(report()))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(A.subprocess, "run", execute)
    assert (
        A.main(
            ["run", "--out", str(output), "--summary", str(summary), "--baseline", str(baseline)]
        )
        == code
    )
    saved = json.loads(summary.read_text())
    assert saved["regression_status"] == expected
    assert saved["status"] == ("ok" if expected == "pass" else expected)
    assert saved["comparison"]["status"] == expected


@pytest.mark.parametrize("command", ["summarize", "compare"])
@pytest.mark.parametrize("input_kind", ["missing", "malformed"])
def test_failed_subcommand_replaces_previous_machine_verdict(tmp_path, command, input_kind):
    """Agent 重用 --json 路径时，输入失败不能交出上一轮的 pass。"""
    bad = tmp_path / "input.json"
    if input_kind == "malformed":
        bad.write_text("{")
    output = tmp_path / "verdict.json"
    output.write_text(json.dumps({"status": "pass"}))
    argv = [command, str(bad)]
    if command == "compare":
        argv.append(str(bad))
    assert A.main([*argv, "--json", str(output)]) == 2
    assert json.loads(output.read_text())["status"] == "failed"


def test_unavailable_pnpm_never_launches_a_subprocess(tmp_path, monkeypatch):
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(A.shutil, "which", lambda _: None)
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("pnpm was not found"))
    assert A.main(["run", "--out", str(tmp_path / "run.json"), "--summary", str(summary)]) == 2
    assert json.loads(summary.read_text())["status"] == "failed"


def test_invalid_baseline_fails_the_current_run_summary(tmp_path, monkeypatch):
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"

    def execute(argv, **kwargs):
        Path(kwargs["env"]["TAVOTTO_PERF_AGENT_OUTPUT"]).write_text(json.dumps(report()))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(A.subprocess, "run", execute)
    assert (
        A.main(
            [
                "run",
                "--out",
                str(output),
                "--summary",
                str(summary),
                "--baseline",
                str(tmp_path / "missing.json"),
            ]
        )
        == 2
    )
    result = json.loads(summary.read_text())
    assert result["status"] == "failed"
    assert result["suites"]["symbol_ui"] == "ok"
    assert result["regression_status"] == "failed"


@pytest.mark.parametrize("input_kind", ["missing", "malformed"])
@pytest.mark.parametrize("with_json", [False, True])
def test_external_comparison_input_failure_invalidates_completed_summary(
    tmp_path, input_kind, with_json
):
    current = tmp_path / "run.json"
    if input_kind == "malformed":
        current.write_text("{")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report()))
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(A.compose_run_summary(A.summarize(report()), "ok")))
    argv = ["compare", str(current), str(baseline), "--summary", str(summary)]
    if with_json:
        argv.extend(["--json", str(tmp_path / "comparison.json")])
    assert A.main(argv) == 2
    saved = json.loads(summary.read_text())
    assert saved["status"] == "failed"
    assert saved["regression_status"] == "failed"
    assert saved["comparison"]["status"] == "failed"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "ok"}


def test_external_comparison_rejects_pending_summary_without_overwriting_it(tmp_path):
    current = tmp_path / "run.json"
    current.write_text(json.dumps(report()))
    summary = tmp_path / "summary.json"
    pending = A.compose_run_summary(A.summarize(report()), "pending")
    summary.write_text(json.dumps(pending))
    verdict = tmp_path / "comparison.json"
    assert (
        A.main(
            [
                "compare",
                str(current),
                str(current),
                "--summary",
                str(summary),
                "--json",
                str(verdict),
            ]
        )
        == 2
    )
    assert json.loads(summary.read_text()) == pending
    assert json.loads(verdict.read_text())["status"] == "failed"


@pytest.mark.parametrize("regression_pct", ["0", "201", "nan"])
def test_external_comparison_invalid_threshold_invalidates_completed_summary(
    tmp_path, regression_pct
):
    current = tmp_path / "run.json"
    current.write_text(json.dumps(report()))
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(A.compose_run_summary(A.summarize(report()), "ok")))
    verdict = tmp_path / "comparison.json"
    assert (
        A.main(
            [
                "compare",
                str(current),
                str(current),
                "--summary",
                str(summary),
                "--json",
                str(verdict),
                "--regression-pct",
                regression_pct,
            ]
        )
        == 2
    )
    saved = json.loads(summary.read_text())
    assert saved["status"] == "failed"
    assert saved["regression_status"] == "failed"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "ok"}
    assert saved["comparison"]["status"] == "failed"
    assert json.loads(verdict.read_text())["status"] == "failed"


@pytest.mark.parametrize(
    "bad_sample",
    [
        None,
        True,
        False,
        float("nan"),
        float("inf"),
        float("-inf"),
        -1,
        "20",
        120_000,
    ],
    ids=["null", "true", "false", "nan", "inf", "negative_inf", "negative", "string", "too_large"],
)
def test_any_extra_invalid_sample_rejects_an_otherwise_complete_report(tmp_path, bad_sample):
    """合法数已足量也不许吞掉额外的坏样本，导入路径同样不能洗成 pass。"""
    malformed = report()
    next(row for row in malformed["scenarios"] if row["id"] == "symbol.alpha")["two_raf_ms"].append(
        bad_sample
    )
    path = tmp_path / "imported.json"
    path.write_text(json.dumps(malformed))
    loaded = A.load(path)
    assert A.summarize(loaded)["status"] == "incomplete"
    assert A.compare(loaded, report())["status"] == "incomparable"
    assert A.compare(report(), loaded)["status"] == "incomparable"


def test_symbol_failure_leaves_unstarted_drag_terminal_not_pending(tmp_path, monkeypatch):
    """符号 Playwright 失败就不会进入拖动；summary 必须是明确失败终态。"""
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(A.shutil, "which", lambda _: "pnpm")

    def fail_symbol(argv, **_kwargs):
        assert "playwright.perf.config.ts" in argv
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(A.subprocess, "run", fail_symbol)
    rc = A.main(["run", "--include-drag", "--out", str(output), "--summary", str(summary)])
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert rc == 2
    assert saved["status"] == "failed"
    assert saved["suites"] == {"symbol_ui": "failed", "canvas_drag": "not_started"}


def test_incomplete_symbol_samples_leave_drag_not_started(tmp_path, monkeypatch):
    """符号成功启动但样本不完整，后继拖动不能标为 pending。"""
    output = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(A.shutil, "which", lambda _: "pnpm")
    incomplete = report()
    incomplete["scenarios"].pop()

    def write_bad_samples(argv, **_kwargs):
        assert "playwright.perf.config.ts" in argv
        output.write_text(json.dumps(incomplete), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(A.subprocess, "run", write_bad_samples)
    rc = A.main(["run", "--include-drag", "--out", str(output), "--summary", str(summary)])
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert rc == 2
    assert saved["status"] == "incomplete"
    assert saved["suites"] == {"symbol_ui": "incomplete", "canvas_drag": "not_started"}


def test_local_runner_disables_background_update_checks(tmp_path, monkeypatch):
    """CI 之外的本地 agent 启动也必须关 updater，测量时不能偷偷联网。"""
    output = tmp_path / "run.json"
    monkeypatch.delenv("TAVOTTO_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(A.shutil, "which", lambda _: "pnpm")
    observed = []

    def capture_environment(argv, **kwargs):
        assert "playwright.perf.config.ts" in argv
        observed.append(kwargs["env"]["TAVOTTO_NO_UPDATE_CHECK"])
        output.write_text(json.dumps(report()), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(A.subprocess, "run", capture_environment)
    assert A.main(["run", "--out", str(output)]) == 0
    assert observed == ["1"]
