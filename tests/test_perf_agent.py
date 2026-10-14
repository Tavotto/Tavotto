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


@pytest.fixture(autouse=True)
def _synthetic_pnpm_for_mocked_runner(monkeypatch):
    """These are unit tests with a mocked subprocess, not real Playwright runs.

    Backend-only CI images do not install pnpm. Patch just executable discovery,
    so synthetic subprocess tests reach their intended failure/baseline paths.
    The explicit unavailable-pnpm test overrides this fixture to assert rc=2.
    """
    real_which = A.shutil.which
    monkeypatch.setattr(
        A.shutil,
        "which",
        lambda name: "pnpm" if name == "pnpm" else real_which(name),
    )


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
            # 反证：删除生产代码里“转 pending 后保存摘要”这一行，测试必须红。
            # 主语是子进程真正启动那一刻持久化的 summary.json，而非内存状态。
            in_flight = json.loads(summary.read_text(encoding="utf-8"))
            assert in_flight["status"] == "pending"
            assert in_flight["suites"] == {"symbol_ui": "ok", "canvas_drag": "pending"}
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


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--repeat", "10.5"],
        ["--repeat", "oops"],
        ["--regression-pct", "not-a-number"],
        ["--unsupported-opt"],
    ],
)
def test_invalid_run_cli_args_invalidate_previous_successful_summary(tmp_path, bad_args):
    """argparse 提前错误退出前，旧成功摘要必须被替换为本次 failed。"""
    old = tmp_path / "summary.json"
    raw = tmp_path / "run.json"
    old.write_text(json.dumps(A.compose_run_summary(A.summarize(report()), "ok")), encoding="utf-8")
    raw.write_text(json.dumps(report()), encoding="utf-8")
    args = ["run", "--summary", str(old), "--out", str(raw), *bad_args]
    assert A.main(args) == 2
    assert json.loads(old.read_text(encoding="utf-8"))["status"] == "failed"
    assert not raw.exists()


def test_invalid_compare_cli_args_invalidate_stale_verdict_and_summary(tmp_path):
    """--regression-pct 无法解析，也不能留下上次 comparison PASS。"""
    outcome = tmp_path / "comparison.json"
    summary = tmp_path / "summary.json"
    outcome.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    summary.write_text(
        json.dumps(A.compose_run_summary(A.summarize(report()), "not_requested")), encoding="utf-8"
    )
    rc = A.main(
        [
            "compare",
            "current.json",
            "base.json",
            "--regression-pct",
            "broken",
            "--json",
            str(outcome),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 2
    assert json.loads(outcome.read_text(encoding="utf-8"))["status"] == "failed"
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["comparison"]["status"] == "failed"
    assert saved["suites"] == {"symbol_ui": "ok", "canvas_drag": "not_requested"}


def test_help_does_not_destroy_existing_successful_report(tmp_path):
    summary = tmp_path / "summary.json"
    good = A.compose_run_summary(A.summarize(report()), "ok")
    summary.write_text(json.dumps(good), encoding="utf-8")
    assert A.main(["run", "--help", "--summary", str(summary)]) == 0
    assert json.loads(summary.read_text(encoding="utf-8")) == good


def test_range_invalid_run_removes_stale_raw_report_before_failing(tmp_path, monkeypatch):
    """数字解析成功但超界，也不能让 Agent 继续读取旧成功 run.json。"""
    out = tmp_path / "run.json"
    summary = tmp_path / "summary.json"
    out.write_text(json.dumps(report()), encoding="utf-8")
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("Must not start browser"))
    rc = A.main(["run", "--repeat", "2", "--out", str(out), "--summary", str(summary)])
    assert rc == 2
    assert not out.exists()
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


def test_perf_playwright_cleanup_cannot_erase_agent_pending_summary():
    """独立 outputDir 的主语是 Playwright 真正会清理的目录，而非报告文件名。"""
    root = Path(__file__).resolve().parents[1]
    config = (root / "web/playwright.perf.config.ts").read_text(encoding="utf-8")
    assert "outputDir: './test-results-perf-agent-playwright'" in config
    assert "outputDir: './test-results'" not in config
    workflow = (root / ".github/workflows/performance-agent.yml").read_text(encoding="utf-8")
    assert "web/test-results/perf-agent/**" in workflow
    assert "web/test-results-perf-agent-playwright/**" in workflow


@pytest.mark.parametrize(
    "command,flag",
    [
        ("run", "--out"),
        ("run", "--summary"),
        ("compare", "--json"),
        ("compare", "--summary"),
        ("summarize", "--json"),
    ],
)
def test_cli_option_terminator_does_not_erase_positional_files(tmp_path, command, flag):
    """恶意/错误调用在 -- 之后给出看似输出 flag，不得覆盖或删除该文件。"""
    victim = tmp_path / "important-user-file.json"
    victim.write_text("must-not-touch", encoding="utf-8")
    assert A.main([command, "--", flag, str(victim)]) == 2
    assert victim.read_text(encoding="utf-8") == "must-not-touch"


def test_long_option_abbreviation_is_not_accepted_or_treated_as_output(tmp_path, monkeypatch):
    """--summ 不再由 argparse 自动认作 --summary，清理器不会误认另一套语法。"""
    important = tmp_path / "non-output.json"
    important.write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    # 反证：打开 allow_abbrev 后会实际走到 pnpm 探测，不能仅靠 rc=2/文件没变。
    monkeypatch.setattr(A.shutil, "which", lambda _: pytest.fail("缩写参数不能启动任何性能子进程"))
    assert A.main(["run", "--summ", str(important), "--repeat", "3"]) == 2
    assert json.loads(important.read_text(encoding="utf-8"))["status"] == "ok"
    assert A._option_path(["--", "--out", str(important)], "--out") is None


def test_run_rejects_same_baseline_and_output_without_deleting_baseline(tmp_path, monkeypatch):
    """baseline 绝不能被 unlink；否则 current 与 baseline 指向同一份会伪 PASS。"""
    path = tmp_path / "historical-baseline.json"
    original = json.dumps(report(value=22))
    path.write_text(original, encoding="utf-8")
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        A.subprocess, "run", lambda *a, **k: pytest.fail("不得在基线别名下启动浏览器")
    )
    rc = A.main(
        [
            "run",
            "--repeat",
            "16",
            "--out",
            str(path),
            "--baseline",
            str(path),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 2
    assert path.read_text(encoding="utf-8") == original
    saved = json.loads(summary.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["regression_status"] == "failed"


def test_baseline_same_inode_through_another_path_is_protected(tmp_path, monkeypatch):
    """目录 .. 或硬链接指向同一历史数据，也不许清除/复写。"""
    base = tmp_path / "baseline.json"
    raw = json.dumps(report(value=25))
    base.write_text(raw, encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    disguised = nested / ".." / "baseline.json"
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("不允许覆盖基线"))
    rc = A.main(
        [
            "run",
            "--out",
            str(disguised),
            "--baseline",
            str(base),
            "--summary",
            str(tmp_path / "summary.json"),
        ]
    )
    assert rc == 2
    assert base.read_text(encoding="utf-8") == raw


def test_parse_error_with_aliased_output_does_not_delete_baseline(tmp_path):
    baseline = tmp_path / "baseline.json"
    original = json.dumps(report(value=31))
    baseline.write_text(original, encoding="utf-8")
    summary = tmp_path / "summary.json"
    rc = A.main(
        [
            "run",
            "--repeat",
            "10.5",
            "--baseline",
            str(baseline),
            "--out",
            str(baseline),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 2
    assert baseline.read_text(encoding="utf-8") == original
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


@pytest.mark.parametrize("shared", ["baseline", "output"])
def test_run_rejects_summary_path_alias_before_any_file_is_changed(tmp_path, shared):
    """summary 若直接指向历史基线或当前输出，绝不允许写 pending 覆盖文件。"""
    baseline = tmp_path / "baseline.json"
    current = tmp_path / "run.json"
    baseline.write_text(json.dumps(report(value=20)), encoding="utf-8")
    current.write_text(json.dumps(report(value=30)), encoding="utf-8")
    summary = baseline if shared == "baseline" else current
    before = summary.read_text(encoding="utf-8")
    rc = A.main(
        [
            "run",
            "--out",
            str(current),
            "--baseline",
            str(baseline),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 2
    assert summary.read_text(encoding="utf-8") == before
    assert baseline.exists() and current.exists()


def test_compare_cannot_overwrite_historical_input_via_output_alias(tmp_path):
    """比较结果路径不能与 current 或 baseline 同名/硬链接。"""
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    current.write_text(json.dumps(report(value=21)), encoding="utf-8")
    baseline.write_text(json.dumps(report(value=19)), encoding="utf-8")
    for target in (current, baseline):
        before = target.read_text(encoding="utf-8")
        assert A.main(["compare", str(current), str(baseline), "--json", str(target)]) == 2
        assert target.read_text(encoding="utf-8") == before
    hardlink = tmp_path / "baseline-hardlink.json"
    hardlink.hardlink_to(baseline)
    before = baseline.read_text(encoding="utf-8")
    assert A.main(["compare", str(current), str(baseline), "--json", str(hardlink)]) == 2
    assert baseline.read_text(encoding="utf-8") == before


def test_agent_workflow_tracks_drag_probe_instrumentation():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/performance-agent.yml"
    ).read_text(encoding="utf-8")
    assert "'web/e2e/perf-probe.spec.ts'" in workflow
    assert "'web/src/perf/**'" in workflow


def test_malformed_compare_does_not_replace_input_with_failure_json(tmp_path):
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    current.write_text(json.dumps(report(value=21)), encoding="utf-8")
    baseline.write_text(json.dumps(report(value=19)), encoding="utf-8")
    saved = baseline.read_text(encoding="utf-8")
    rc = A.main(
        [
            "compare",
            str(current),
            str(baseline),
            "--json",
            str(baseline),
            "--regression-pct",
            "invalid",
        ]
    )
    assert rc == 2
    assert baseline.read_text(encoding="utf-8") == saved


def test_malformed_summarize_does_not_replace_source_with_failure_json(tmp_path):
    raw = tmp_path / "source.json"
    raw.write_text(json.dumps(report()), encoding="utf-8")
    original = raw.read_text(encoding="utf-8")
    assert A.main(["summarize", str(raw), "--json", str(raw), "--unknown"]) == 2
    assert raw.read_text(encoding="utf-8") == original


def test_parse_error_with_interleaved_options_never_overwrites_readonly_baseline(tmp_path):
    """Codex P1：baseline 在非法 --regression-pct 后面时仍是只读输入。"""
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    current.write_text(json.dumps(report(value=21)), encoding="utf-8")
    baseline.write_text(json.dumps(report(value=19)), encoding="utf-8")
    before = baseline.read_bytes()
    rc = A.main(
        [
            "compare",
            str(current),
            "--regression-pct",
            "invalid",
            str(baseline),
            "--json",
            str(baseline),
        ]
    )
    assert rc == 2
    assert baseline.read_bytes() == before


def test_summarize_parse_error_with_options_first_keeps_source(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps(report()), encoding="utf-8")
    before = source.read_bytes()
    assert A.main(["summarize", "--json", str(source), str(source), "--unknown"]) == 2
    assert source.read_bytes() == before


@pytest.mark.parametrize("link_type", ["ordinary", "hardlink", "symlink"])
def test_report_atomic_temp_cannot_clobber_baseline_named_output_tmp(tmp_path, link_type):
    """输出是 comparison.json，历史基线刚好叫 comparison.json.tmp。"""
    current = tmp_path / "current.json"
    original = tmp_path / "historical.json"
    baseline = tmp_path / "comparison.json.tmp"
    output = tmp_path / "comparison.json"
    current.write_text(json.dumps(report(value=22)), encoding="utf-8")
    original.write_text(json.dumps(report(value=20)), encoding="utf-8")
    if link_type == "ordinary":
        baseline.write_bytes(original.read_bytes())
    elif link_type == "hardlink":
        baseline.hardlink_to(original)
    else:
        try:
            baseline.symlink_to(original)
        except (OSError, NotImplementedError):
            pytest.skip("This Windows runner cannot create symlinks without elevated privileges")
    before = original.read_bytes()
    rc = A.main(["compare", str(current), str(baseline), "--json", str(output)])
    assert rc == 0
    assert original.read_bytes() == before
    assert baseline.read_bytes() == before
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "pass"


def test_parse_error_does_not_clobber_baseline_via_derived_temp_alias(tmp_path):
    """即使输出报告本身不与输入同名，旧固定 .tmp 也不许截断历史基线。"""
    current = tmp_path / "current.json"
    baseline = tmp_path / "verdict.json.tmp"
    verdict = tmp_path / "verdict.json"
    current.write_text(json.dumps(report()), encoding="utf-8")
    baseline.write_text(json.dumps(report()), encoding="utf-8")
    before = baseline.read_bytes()
    rc = A.main(
        [
            "compare",
            str(current),
            str(baseline),
            "--json",
            str(verdict),
            "--regression-pct",
            "not-a-number",
        ]
    )
    assert rc == 2
    assert baseline.read_bytes() == before
    assert json.loads(verdict.read_text(encoding="utf-8"))["status"] == "failed"


@pytest.mark.parametrize("command", ["summarize", "compare"])
def test_all_report_subparsers_reject_abbreviated_json_flag(tmp_path, command):
    """Codex P1 反证：不能只看护 run 子命令的 allow_abbrev=False。"""
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    target = tmp_path / "prior-verdict.json"
    current.write_text(json.dumps(report()), encoding="utf-8")
    baseline.write_text(json.dumps(report()), encoding="utf-8")
    target.write_text(json.dumps({"status": "pass", "canary": "do-not-touch"}), encoding="utf-8")
    before = target.read_bytes()
    args = (
        ["summarize", str(current), "--j", str(target)]
        if command == "summarize"
        else ["compare", str(current), str(baseline), "--j", str(target)]
    )
    # 若该子解析器重新启用 unique-prefix 识别，就会成功执行并改写结果。
    assert A.main(args) == 2
    assert target.read_bytes() == before


@pytest.mark.parametrize("output_name", ["drag.json", "drag-findings.json"])
def test_run_rejects_symbol_output_colliding_with_drag_sidecars(tmp_path, monkeypatch, output_name):
    """真实 Agent 不可先报符号通过再让 sidecar 覆写 --out。"""
    output = tmp_path / output_name
    output.write_text(json.dumps(report()), encoding="utf-8")
    prior = output.read_bytes()
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        A.subprocess, "run", lambda *a, **k: pytest.fail("Preflight must reject collision")
    )
    rc = A.main(["run", "--include-drag", "--out", str(output), "--summary", str(summary)])
    assert rc == 2
    assert output.read_bytes() == prior
    assert json.loads(summary.read_text(encoding="utf-8"))["status"] == "failed"


@pytest.mark.parametrize("name", ["drag.json", "drag-findings.json"])
def test_drag_sidecar_cannot_unlink_the_readonly_baseline(tmp_path, monkeypatch, name):
    base = tmp_path / name
    base.write_text(json.dumps(report()), encoding="utf-8")
    before = base.read_bytes()
    monkeypatch.setattr(
        A.subprocess, "run", lambda *a, **k: pytest.fail("No benchmark may be launched")
    )
    rc = A.main(
        [
            "run",
            "--include-drag",
            "--out",
            str(tmp_path / "run.json"),
            "--baseline",
            str(base),
            "--summary",
            str(tmp_path / "summary.json"),
        ]
    )
    assert rc == 2
    assert base.read_bytes() == before
    result = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["regression_status"] == "failed"


def test_summarize_rejects_in_place_or_hardlink_report_output(tmp_path):
    """既保住同路径输入，也不能通过硬链接用同名 schema 覆盖原样本。"""
    original = tmp_path / "report.json"
    alias = tmp_path / "alias.json"
    original.write_text(json.dumps(report()), encoding="utf-8")
    alias.hardlink_to(original)
    before = original.read_bytes()
    for target in (original, alias):
        assert A.main(["summarize", str(original), "--json", str(target)]) == 2
        assert original.read_bytes() == before
        assert alias.read_bytes() == before


def test_incomplete_symbol_with_requested_baseline_is_terminal_not_pending(tmp_path, monkeypatch):
    raw = tmp_path / "run.json"
    baseline = tmp_path / "baseline.json"
    summary = tmp_path / "summary.json"
    baseline.write_text(json.dumps(report()), encoding="utf-8")
    incomplete = report()
    incomplete["scenarios"].pop()
    monkeypatch.setattr(A.shutil, "which", lambda _: "pnpm")

    def write_incomplete(argv, **kwargs):
        assert "playwright.perf.config.ts" in argv
        raw.write_text(json.dumps(incomplete), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(A.subprocess, "run", write_incomplete)
    assert (
        A.main(
            [
                "run",
                "--out",
                str(raw),
                "--baseline",
                str(baseline),
                "--summary",
                str(summary),
            ]
        )
        == 2
    )
    result = json.loads(summary.read_text(encoding="utf-8"))
    assert result["suites"]["symbol_ui"] == "incomplete"
    assert result["status"] == "incomplete"
    assert result["regression_status"] == "failed"


@pytest.mark.parametrize("alias_kind", ["direct", "hardlink", "symlink"])
def test_run_rejects_derived_comparison_output_aliasing_historical_baseline(
    tmp_path, monkeypatch, alias_kind
):
    """Codex P1：out=run.json 的附属 run.comparison.json 不得成为历史 baseline。"""
    current = tmp_path / "run.json"
    derived = tmp_path / "run.comparison.json"
    historical = tmp_path / "history.json"
    historical.write_text(json.dumps(report(value=20)), encoding="utf-8")
    if alias_kind == "direct":
        derived.write_bytes(historical.read_bytes())
    elif alias_kind == "hardlink":
        derived.hardlink_to(historical)
    else:
        try:
            derived.symlink_to(historical)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation is unavailable on this Windows runner")
    before = historical.read_bytes()
    summary = tmp_path / "summary.json"
    monkeypatch.setattr(
        A.subprocess, "run", lambda *a, **k: pytest.fail("Baseline collision must reject first")
    )
    assert (
        A.main(
            [
                "run",
                "--out",
                str(current),
                "--baseline",
                str(derived),
                "--summary",
                str(summary),
            ]
        )
        == 2
    )
    assert derived.read_bytes() == historical.read_bytes() == before
    assert json.loads(summary.read_text(encoding="utf-8"))["regression_status"] == "failed"


def test_failed_run_argparse_does_not_erase_derived_comparison_baseline(tmp_path):
    raw = tmp_path / "run.json"
    baseline = tmp_path / "run.comparison.json"
    baseline.write_text(json.dumps(report(value=20)), encoding="utf-8")
    before = baseline.read_bytes()
    summary = tmp_path / "summary.json"
    assert (
        A.main(
            [
                "run",
                "--repeat",
                "not-a-number",
                "--out",
                str(raw),
                "--baseline",
                str(baseline),
                "--summary",
                str(summary),
            ]
        )
        == 2
    )
    assert baseline.read_bytes() == before
    assert json.loads(summary.read_text(encoding="utf-8"))["regression_status"] == "failed"
