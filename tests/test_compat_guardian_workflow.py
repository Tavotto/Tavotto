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


def jobs() -> dict[str, str]:
    parts = re.split(r"(?m)^  ([A-Za-z_][\w-]*):\s*$", top_block("jobs"))
    found = {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}
    assert found, "no jobs found"
    return found


def steps(job: str | None = None) -> list[str]:
    """Steps of one job, or of every job when `job` is None."""
    blocks = [jobs()[job]] if job else list(jobs().values())
    out = []
    for block in blocks:
        body = block.split("    steps:\n", 1)[1]
        out += [p for p in re.split(r"(?m)^      - ", body) if p.strip()]
    return out


def test_runs_only_on_github_hosted_runners_never_self_hosted_or_lab():
    labels = re.findall(r"(?m)^\s+runs-on:\s*(.+?)\s*$", code())
    assert labels, "no runs-on found"
    assert set(labels) <= HOSTED, labels
    for forbidden in ("self-hosted", "tavotto-lab", "runs-on: {", "runs-on: ["):
        assert forbidden not in code()


def test_events_are_the_trusted_closed_set_without_merge_queue_or_target_events():
    events = set(re.findall(r"(?m)^  ([a-z_]+):", top_block("on")))
    assert events == {"pull_request", "workflow_dispatch", "schedule"}
    assert "pull_request_target" not in code() and "merge_group" not in code()


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
    per_job = {
        name: re.search(r"(?m)^    permissions:\n((?:      .+\n)+)", block)
        for name, block in jobs().items()
    }
    escalated = {name: m.group(1) for name, m in per_job.items() if m}
    # the only job allowed to widen anything reads run metadata (actions: read) to find the
    # previous trend artifact; nothing in this workflow can write
    assert set(escalated) == {"trend"}
    assert re.fullmatch(r"\s*contents: read\s+actions: read\s*", escalated["trend"])
    assert ": write" not in code() and "write-all" not in code()
    checkouts = [s for s in steps() if s.startswith("uses: actions/checkout@")]
    assert len(checkouts) == 3 and all("persist-credentials: false" in s for s in checkouts)


def test_workflow_can_only_annotate_it_never_opens_issues_or_prs_or_posts():
    for forbidden in (
        "gh issue",
        "gh pr ",
        "gh api",
        "github-script",
        "create-pull-request",
        "issues:",
        "pull-requests:",
        "curl ",
    ):
        assert forbidden not in code(), forbidden


def test_no_secrets_and_the_token_is_confined_to_one_read_only_step():
    for forbidden in ("secrets.", "GITHUB_TOKEN", "pull_request_target"):
        assert forbidden not in code(), forbidden
    users = [
        name for name, block in jobs().items() if "github.token" in block or "GH_TOKEN" in block
    ]
    assert users == ["trend"]
    step = [s for s in steps("trend") if "GH_TOKEN" in s]
    assert len(step) == 1 and "gh run list" in step[0] and "gh run download" in step[0]
    for other in ("--method", "-X ", "POST", "PATCH", "DELETE"):
        assert other not in step[0]


def test_previous_trend_comes_from_the_latest_completed_run_that_has_the_artifact():
    """The scheduled V2 step may exit 1 on purpose, so a failed run still carries a valid trend."""
    step = [s for s in steps("trend") if "GH_TOKEN" in s][0]
    assert "--status completed" in step and "--status success" not in step
    assert "--event schedule" in step
    # cancelled/skipped runs are filtered by conclusion; success and failure are both usable
    assert '.conclusion == "success" or .conclusion == "failure"' in step
    assert "cancelled" not in step.replace("cancelled/skipped", "")
    # selection is by downloadable artifact, searching back a bounded number of runs
    assert "[:5]" in step and "for prev in" in step and "break" in step
    assert "compat-guardian-trend.json" in step and "gh run download" in step
    # both extraction layouts: directly under --dir, or one subdirectory per artifact
    assert '"$RUNNER_TEMP/prev/compat-guardian-trend.json"' in step
    assert '"$RUNNER_TEMP/prev/*/compat-guardian-trend.json"' in step
    assert "not_computed" in step


def test_trend_requires_a_report_for_every_expected_suite():
    step = [s for s in steps("trend") if "compat_guardian.py summarize" in s][0]
    assert "--require-suites compat_engine,compat_v2" in step


def test_trend_job_is_structurally_read_only():
    """Proves, from the workflow text's structure: the trend job's permissions are exactly
    contents: read + actions: read, nothing in it references secrets, and every checkout drops
    credentials. It does not prove what the downloaded artifacts contain."""
    block = jobs()["trend"]
    perms = re.search(r"(?m)^    permissions:\n((?:      .+\n)+)", block)
    assert perms
    entries = dict(
        line.strip().split(": ", 1) for line in perms.group(1).splitlines() if line.strip()
    )
    assert entries == {"contents": "read", "actions": "read"}
    assert "secrets" not in block.lower()
    assert "id-token" not in block and "write" not in block.replace("write_", "")
    checkout = [s for s in steps("trend") if s.startswith("uses: actions/checkout@")]
    assert checkout and all("persist-credentials: false" in s for s in checkout)


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
    last = [s for s in steps("guardian") if "Validate and summarize" in s][0]
    assert "GITHUB_STEP_SUMMARY" in last
    assert "if: ${{ !cancelled() }}" in last, "summary and validation must run after a failed suite"
    assert 'compat_guardian.py validate "$report"' in last
    assert "exit 1" in last, "no report at all is a failure, not an empty summary"
    triage = [s for s in steps("explore") if "Triage and maintainer alert" in s][0]
    assert (
        "if: ${{ !cancelled() }}" in triage
        and "GITHUB_STEP_SUMMARY" in triage
        and "exit 1" in triage
    )
    assert "triage" in triage and "--markdown-out" in triage
    summarize = [s for s in steps("trend") if s.startswith("name: Summarize")][0]
    assert "GITHUB_STEP_SUMMARY" in summarize and "exit 1" in summarize


UPLOAD_ALLOWED = (
    "/reports/guardian.json",
    "/reports/summary.md",
    "/reports/guardian.unvalidated.json",
    "guardian-summary.json",
    "guardian-summary.md",
    "/evidence/**/*.png",
    "/reports/explore-report.json",
    "/reports/explore-summary.md",
    "/reports/triage.json",
    "/reports/triage.md",
    "/candidates.json",
    "/trend/compat-guardian-trend.json",
    "/trend/trend.md",
)


def test_every_artifact_upload_runs_on_failure_too_and_is_an_allowlist():
    uploads = [s for s in steps() if "actions/upload-artifact@" in s]
    assert len(uploads) == 3
    for block in uploads:
        assert "if: ${{ !cancelled() }}" in block
        listed = re.findall(r"(?m)^ {12}(\$\{\{ runner\.temp \}\}/\S+)$", block)
        assert listed
        for path in listed:
            assert path.endswith(UPLOAD_ALLOWED), f"not on the allowlist: {path}"
        for raw in (
            "stdout",
            "stderr",
            "process.json",
            "execs",
            ".txt",
            "compat-guardian/**",
            "compat-explore/**",
            "/candidates/",
        ):
            assert raw not in "\n".join(listed), f"raw output pattern in upload paths: {raw}"


def test_public_log_only_gets_status_lines_not_raw_output():
    for step in steps():
        if "compat_guardian.py run" in step or "compat_guardian.py explore" in step:
            assert '> "$RUNNER_TEMP/' in step, "output must be captured, then filtered"
            assert "grep -E '^" in step
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


def test_nightly_explore_is_hosted_fixed_seed_bounded_and_never_on_pull_requests():
    for name in ("explore", "trend"):
        assert "github.event_name != 'pull_request'" in jobs()[name].split("steps:")[0], name
    block = jobs()["explore"]
    assert re.search(r"EXPLORE_SEED: \$\{\{ inputs\.seed \|\| '42' \}\}", block), (
        "fixed default seed"
    )
    limit = int(re.search(r"EXPLORE_LIMIT: \$\{\{ inputs\.limit \|\| '(\d+)' \}\}", block).group(1))
    assert 1 <= limit <= 40, "nightly stays far inside the job limit"
    job_minutes = int(re.search(r"(?m)^    timeout-minutes: (\d+)$", block).group(1))
    step_minutes = int(
        re.search(r"Explore derived candidates\n\s+timeout-minutes: (\d+)", block).group(1)
    )
    assert step_minutes < job_minutes
    assert "--confirm-differences" in block and "--limit" in block and "--seed" in block


def test_candidate_source_is_restricted_to_reviewed_parents_by_construction():
    inputs = top_block("on").split("workflow_dispatch:", 1)[1].split("schedule:", 1)[0]
    names = re.findall(r"(?m)^      ([a-z_]+):$", inputs)
    assert names == ["seed", "limit", "families"], names
    for forbidden in ("script", "path", "url", "command", "source", "file", "repo"):
        assert not any(forbidden in n for n in names)
    # inputs reach the shell only through env, never interpolated into command text
    for step in steps():
        run = step.split("run: ", 1)[1] if "run: " in step else ""
        assert "${{ inputs." not in run and "${{ github.event." not in run
    assert (
        "--parent" not in code() and "--candidates" not in code() and "--static-only" not in code()
    )
    assert code().count("compat_guardian.py explore") == 1


def test_explore_cannot_be_given_an_unvalidated_seed_limit_or_family():
    run = [s for s in steps("explore") if "Explore derived candidates" in s][0]
    assert "=~ ^[0-9]+$" in run and "=~ ^[a-z_,]*$" in run
    # limit is a positive integer: 0 must not be accepted (select() would otherwise run everything)
    assert '"$EXPLORE_LIMIT" =~ ^[1-9][0-9]*$' in run
    assert '"$EXPLORE_LIMIT" =~ ^[0-9]+$' not in run
    assert run.index("=~") < run.index("compat_guardian.py explore")


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


def test_trend_downloads_exactly_this_runs_two_reports_by_name():
    """trend 不用 pattern 下载：按名字取 guardian 与 explore 两份，不会匹配到自己的 trend 上传。"""
    text = GUARDIAN.read_text(encoding="utf-8")
    trend = text[text.index("\n  trend:") :]
    assert "pattern:" not in trend
    for name in (
        "name: compat-guardian-${{ github.run_id }}-${{ github.run_attempt }}",
        "name: compat-guardian-explore-${{ github.run_id }}-${{ github.run_attempt }}",
    ):
        assert name in trend, name
