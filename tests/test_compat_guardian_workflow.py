"""Static contract for .github/workflows/compat-guardian.yml.

Same discipline as tests/test_merge_queue_workflows.py: no PyYAML, string criteria that
only know this repository's indentation, and a failure to find the expected shape raises
instead of passing. The subject is the checked-out workflow file, not what GitHub
executes for a PR (a PR runs its own copy of the yml).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
GUARDIAN = WF / "compat-guardian.yml"

if not WF.is_dir():
    pytest.skip(
        "repository-level workflow contract; only meaningful in a source checkout",
        allow_module_level=True,
    )

sys.path.insert(0, str(ROOT / "scripts/ci"))
import compat_guardian as cg  # noqa: E402
import merge_queue_ruleset as mq  # noqa: E402

HOSTED = {"ubuntu-24.04", "ubuntu-latest"}
FILES_THE_PR_FILTER_MAY_NAME = (
    ".github/workflows/compat-guardian.yml",
    "scripts/ci/compat_guardian",
    "scripts/ci/compat_explore",
    "scripts/ci/compat_triage",
    "scripts/ci/compat_minimize",
    "scripts/ci/guardian_exec_observer.py",
    "scripts/ci/compat_driver.py",
    "scripts/ci/compat_corpus.py",
    "scripts/ci/compat_matrix.py",
    "scripts/ci/package_smoke.py",
    "tests/test_package_smoke.py",
    "tests/compat/",
    "tests/fixtures/compat_v2/",
    "tests/support/compat_v2_scenarios.py",
    "tests/support/compatbench_v2.py",
    "tests/test_compat_guardian",
    "tests/test_compat_explore",
    "tests/test_compat_triage",
    "tests/test_compat_minimize",
    "tests/compat_guardian/",
    "docs/ci/compat-guardian.md",
)


def text() -> str:
    return GUARDIAN.read_text(encoding="utf-8")


def code() -> str:
    return "\n".join(ln for ln in text().splitlines() if not ln.lstrip().startswith("#"))


def top_block(name: str) -> str:
    m = re.search(rf"(?ms)^{name}:\n(.*?)(?=^\S|\Z)", code())
    assert m, f"no top-level `{name}:` block"
    return m.group(1)


def steps() -> list[str]:
    body = code().split("    steps:\n", 1)[1]
    parts = re.split(r"(?m)^      - ", body)
    return [p for p in parts if p.strip()]


def test_runs_only_on_github_hosted_runners_never_self_hosted_or_lab():
    labels = re.findall(r"(?m)^\s+runs-on:\s*(.+?)\s*$", code())
    assert labels, "no runs-on found"
    assert set(labels) <= HOSTED, labels
    for forbidden in ("self-hosted", "tavotto-lab", "runs-on: {", "runs-on: ["):
        assert forbidden not in code()


def test_events_are_the_trusted_closed_set_without_merge_queue_or_target_events():
    events = set(re.findall(r"(?m)^  ([a-z_]+):", top_block("on")))
    assert events == {"pull_request", "workflow_dispatch", "schedule"}


def test_pull_request_trigger_is_limited_to_main_and_guardians_own_files():
    block = top_block("on")
    assert re.search(r"(?m)^    branches: \[main\]$", block)
    paths = re.findall(r"(?m)^      - '([^']+)'$", block.split("paths:", 1)[1])
    assert paths
    for path in paths:
        assert path.startswith(FILES_THE_PR_FILTER_MAY_NAME), (
            f"filter names a non-Guardian path: {path}"
        )


def test_pull_request_filter_covers_every_input_the_workflow_executes():
    """Every committed input the run reads (registered corpus + V2 scenarios/fixtures) re-runs it."""
    block = top_block("on")
    paths = re.findall(r"(?m)^      - '([^']+)'$", block.split("paths:", 1)[1])
    for needed in (
        "tests/compat/**",
        "tests/fixtures/compat_v2/**",
        "tests/support/compat_v2_scenarios.py",
        "tests/support/compatbench_v2.py",
        "scripts/ci/compat_corpus.py",
        "scripts/ci/compat_matrix.py",
        "scripts/ci/package_smoke.py",
    ):
        assert needed in paths, f"PR filter misses an executed input: {needed}"


def test_token_is_read_only_and_credentials_are_not_persisted():
    assert re.fullmatch(r"\s*contents: read\s*", top_block("permissions"))
    assert "permissions:" not in code().split("jobs:", 1)[1], "no per-job escalation"
    checkouts = [s for s in steps() if s.startswith("uses: actions/checkout@")]
    assert checkouts and all("persist-credentials: false" in s for s in checkouts)


def test_no_secrets_and_no_token_use():
    for forbidden in (
        "secrets.",
        "github.token",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "pull_request_target",
    ):
        assert forbidden not in code(), forbidden


def test_no_failure_is_swallowed():
    assert "continue-on-error" not in code()
    for line in code().splitlines():
        if "|| true" in line:
            assert line.strip().startswith("grep "), (
                f"only the log-filter grep may be `|| true`: {line}"
            )
    for step in steps():
        if "set +e" in step:
            assert 'exit "$code"' in step, "a step that relaxes -e must re-raise the exit code"
            assert "*)" in step, "unknown exit codes must fall to a failing branch"
    # exit 4 (partial_coverage) is the only code allowed through, and it warns.
    assert code().count('4) echo "::warning::') == 2
    assert 'case "$code" in\n            0) ;;' in code()


def test_failures_and_partial_results_are_written_to_the_step_summary():
    last = [s for s in steps() if "Validate and summarize" in s][0]
    assert "GITHUB_STEP_SUMMARY" in last
    assert "if: ${{ !cancelled() }}" in last, "summary and validation must run after a failed suite"
    assert 'compat_guardian.py validate "$report"' in last
    assert "exit 1" in last, "no report at all is a failure, not an empty summary"


def test_artifact_upload_runs_on_failure_too_and_is_an_allowlist():
    upload = [s for s in steps() if "actions/upload-artifact@" in s]
    assert len(upload) == 1
    block = upload[0]
    assert "if: ${{ !cancelled() }}" in block
    listed = re.findall(r"(?m)^ {12}(\$\{\{ runner\.temp \}\}/\S+)$", block)
    assert listed
    allowed_suffixes = (
        "/reports/guardian.json",
        "/reports/summary.md",
        "/reports/guardian.unvalidated.json",
        "guardian-summary.json",
        "guardian-summary.md",
        "/evidence/**/*.png",
    )
    for path in listed:
        assert path.endswith(allowed_suffixes), f"not on the allowlist: {path}"
    for raw in ("stdout", "stderr", "process.json", "execs", ".txt", "compat-guardian/**"):
        assert raw not in "\n".join(listed), f"raw output pattern in upload paths: {raw}"


def test_public_log_only_gets_status_lines_not_raw_output():
    for step in steps():
        if "compat_guardian.py run" in step:
            assert '> "$RUNNER_TEMP/' in step, "run output must be captured, then filtered"
            assert "grep -E '^[A-Za-z0-9_]+: [a-z_]+$'" in step
            assert "tail -n 30" in step, "a failing exit shows a bounded tail only"


def test_listed_cases_are_registered_and_the_v2_run_is_the_whole_suite():
    engine = re.search(r"--suite compat_engine \\\n\s+--case (\S+) \\", code())
    assert engine, "engine run must pin its case list"
    registry = cg.registered("compat_engine")
    for case in engine.group(1).split(","):
        assert case in registry, f"compat_engine: {case} is not a registered case"
    v2 = re.search(r"(?s)--suite compat_v2(.*?)--out", code()).group(1)
    assert "--case" not in v2, "the V2 run must cover every scenario, not a hand-picked subset"
    assert len(re.findall(r"--suite (\w+)", code())) == 2


def test_runs_have_timeouts():
    assert re.search(r"(?m)^    timeout-minutes: \d+$", code())
    for step in steps():
        if "compat_guardian.py run" in step:
            assert re.search(r"timeout-minutes: \d+", step)
            assert "--timeout" in step


def test_guardian_is_not_part_of_the_merge_gate():
    ci = (WF / "ci.yml").read_text(encoding="utf-8")
    assert "compat_guardian" not in ci and "compat-guardian" not in ci
    assert not [n for n in re.findall(r"(?m)^\s+needs:.*$", ci) if "guardian" in n.lower()]
    workflow_name = re.search(r"(?m)^    name: (.+)$", code()).group(1)
    for context in mq.GATE_CONTEXTS:
        assert "guardian" not in context.lower()
    assert workflow_name not in mq.GATE_CONTEXTS
    assert "merge_group" not in code()


def test_guardian_infrastructure_changes_have_code_owners():
    owners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    for pattern in (
        "/tests/compat/**",
        "/tests/fixtures/compat_v2/**",
        "/scripts/ci/compat_guardian*",
        "/scripts/ci/compat_explore*",
        "/scripts/ci/compat_triage*",
        "/scripts/ci/compat_minimize*",
        "/scripts/ci/guardian_exec_observer.py",
        "/tests/test_compat_guardian*",
        "/tests/test_compat_explore*",
        "/tests/test_compat_triage*",
        "/tests/test_compat_minimize*",
        "/tests/compat_guardian/**",
        "/.github/workflows/compat-guardian.yml",
        "/docs/ci/compat-guardian.md",
    ):
        assert re.search(rf"(?m)^{re.escape(pattern)}\s+@erwanjun$", owners), pattern


def test_any_workflow_that_references_guardian_code_is_hosted_only_and_not_lab_reachable():
    """Runner-trust rule for Guardian: candidate/exploration code never meets a persistent runner.

    Subject: every workflow file whose executable text mentions a Guardian script, not a
    hand-kept list, so a future workflow that adds Guardian is judged the same way.
    """
    referencing = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WF.glob("*.y*ml"))
        if re.search(
            r"compat_guardian|compat_explore|compat_triage|compat_minimize|guardian_exec_observer",
            path.read_text(encoding="utf-8"),
        )
    }
    assert "compat-guardian.yml" in referencing, (
        "wrong subject: the Guardian workflow must be in the set"
    )
    for name, body in referencing.items():
        executable = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        labels = set(re.findall(r"(?m)^\s+runs-on:\s*(.+?)\s*$", executable))
        assert labels <= HOSTED, f"{name} runs Guardian on {sorted(labels - HOSTED)}"
        assert "tavotto-lab" not in executable and "self-hosted" not in executable, name
        assert "_lab-qualification.yml" not in executable, name


def test_fork_pull_requests_do_not_run_guardian():
    job = code().split("jobs:", 1)[1].split("    steps:", 1)[0]
    assert (
        "if: ${{ github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.full_name == github.repository }}"
    ) in job
    assert "pull_request_target" not in code()
