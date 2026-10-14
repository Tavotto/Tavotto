"""实际执行历史比较的 Bash step，用本机 stub 代替 GitHub 和测量子进程。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from test_perf_agent import A, report

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/performance-agent.yml"


def _comparison_step() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    start = source.index("      - name: Compare to the latest successful main run (advisory)")
    end = source.index("      - name: Upload numeric results and failure traces", start)
    return textwrap.dedent(source[start:end].split("        run: |\n", 1)[1])


@pytest.mark.parametrize("later_verdict", ["incomparable", "pass", "regression", "no_main_runs"])
def test_historical_baseline_shell_keeps_only_a_compatible_comparison(tmp_path, later_verdict):
    bash = shutil.which("bash")
    if bash is None or sys.platform == "win32":
        pytest.skip("This workflow step runs in Bash on hosted Ubuntu.")
    job = tmp_path / "job"
    results = job / "web/test-results/perf-agent"
    results.mkdir(parents=True)
    (results / "run.json").write_text(json.dumps(report()))
    summary = results / "summary.json"
    original = A.compose_run_summary(A.summarize(report()), "ok")
    summary.write_text(json.dumps(original))
    comparison = results / "comparison.json"
    comparison.write_text(json.dumps({"status": "regression", "stale": True}))
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    common = f"#!{sys.executable}\n"
    gh = bin_dir / "gh"
    gh.write_text(
        common
        + textwrap.dedent("""\
        import json, os, sys
        from pathlib import Path
        if sys.argv[1:3] == ["run", "list"]:
            print(os.environ.get("TEST_RUN_LIST", "101\\n102"))
        elif sys.argv[1:3] == ["run", "download"]:
            run_id = sys.argv[3]
            target = Path(sys.argv[sys.argv.index("--dir") + 1]) / "test-results/perf-agent/run.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"run_id": run_id, "status": json.loads(os.environ["TEST_VERDICTS"])[run_id]}))
        else:
            raise SystemExit("unexpected gh operation")
    """)
    )
    python = bin_dir / "python"
    python.write_text(
        common
        + textwrap.dedent("""\
        import json, os, sys
        from pathlib import Path
        if sys.argv[1] == "-c":
            os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
        if sys.argv[1:3] != ["scripts/perf_agent.py", "compare"]:
            raise SystemExit("unexpected python operation")
        candidate = json.loads(Path(sys.argv[4]).read_text())
        verdict = candidate["status"]
        outcome = {"schema": "tavotto-perf-comparison/1", "status": verdict}
        Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(outcome))
        with Path(os.environ["TEST_COMPARE_LOG"]).open("a") as out:
            out.write(json.dumps({"run_id": candidate["run_id"], "summary": "--summary" in sys.argv}) + "\\n")
        if "--summary" in sys.argv:
            target = Path(sys.argv[sys.argv.index("--summary") + 1])
            summary = json.loads(target.read_text())
            summary.update(status="ok" if verdict == "pass" else verdict, regression_status=verdict, comparison=outcome)
            target.write_text(json.dumps(summary))
        raise SystemExit(0 if verdict == "pass" else 3)
    """)
    )
    for executable in (gh, python):
        executable.chmod(0o755)
    calls_path = tmp_path / "compare-calls.jsonl"
    step_summary = tmp_path / "step-summary.md"
    executed = subprocess.run(
        [bash, "-c", _comparison_step()],
        cwd=job,
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "GITHUB_STEP_SUMMARY": str(step_summary),
            "GH_REPO": "Tavotto/Tavotto",
            "TEST_VERDICTS": json.dumps({"101": "incomparable", "102": later_verdict}),
            "TEST_RUN_LIST": "" if later_verdict == "no_main_runs" else "101 102",
            "TEST_COMPARE_LOG": str(calls_path),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert executed.returncode == 0, executed.stdout + executed.stderr
    if later_verdict == "no_main_runs":
        assert not calls_path.exists(), "No baseline: a comparison must not have been attempted"
        assert not comparison.exists(), "Even without main runs, stale comparison must be removed"
        assert json.loads(summary.read_text()) == original
        assert "no successful main artifact yet" in step_summary.read_text()
        return
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert calls[:2] == [{"run_id": "101", "summary": False}, {"run_id": "102", "summary": False}]
    saved = json.loads(summary.read_text())
    if later_verdict == "incomparable":
        assert not comparison.exists()
        assert saved == original
        assert len(calls) == 2
        assert "NOT regression PASS" in step_summary.read_text()
    else:
        assert calls[2:] == [{"run_id": "102", "summary": True}]
        assert json.loads(comparison.read_text())["status"] == later_verdict
        assert saved["regression_status"] == later_verdict
        assert saved["status"] == ("ok" if later_verdict == "pass" else "regression")
        assert saved["suites"] == original["suites"]
