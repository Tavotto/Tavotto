"""S0 accounting contracts; synthetic records never claim scientific execution.

The subject is the frozen sixteen-project development cohort at aggregation time:
missing/native-invalid cases stay visible, and the eligible denominator does not
shrink separately at later stages. No external research source is imported.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))

import compat_guardian as cg  # noqa: E402
import compat_guardian_research as research  # noqa: E402

SHA = "a" * 40


def cohort_record() -> dict:
    return {
        "schema": "tavotto-research-cohort/1",
        "cohort_id": "synthetic-contract-only",
        "product_baseline_sha": "b" * 40,
        "development": [
            {
                "project_id": f"project_{i:02}",
                "case_id": f"research_contract_{i:02}",
                "domain": "contract_test",
                "sample_kind": "research_artifact",
                "upstream": {"repository": f"independent_{i}/project", "commit": SHA},
                "entry": "plot.py",
                "launch": {"cwd": ".", "argv": []},
                "project_directories": [],
                "requires_tex": False,
                "expected_figures_static": 1,
                "source_package_files": {},
                "tasks": [
                    {
                        "target_stem": "plot",
                        "target_figure_index": 0,
                        "mutation": {
                            "gid": "axes_0.lines_0",
                            "prop": "color",
                            "value": "#112233",
                        },
                        "required_stages": list(research.cc.STAGES),
                        "exports": ["pdf", "png", "svg"],
                        "document_persistence": "not_covered",
                        "original_file_writeback": "not_requested",
                    }
                ],
            }
            for i in range(16)
        ],
        "holdout": [
            {
                "project_id": f"sealed_{i:02}",
                "domain": "contract_test",
                "sample_kind": "research_artifact",
                "source_sha256": "c" * 64,
                "task_sha256": "d" * 64,
                "package_sha256": "e" * 64,
                "license_verified": True,
                "inputs_available": True,
                "status": "sealed_not_run",
            }
            for i in range(8)
        ],
    }


def report_row(case_id: str) -> dict:
    """Only projection fields; full Guardian proof validation has its own suite."""
    return {
        "id": case_id,
        "status": "passed",
        "native": {"status": "passed"},
        "figures": {"native": 1, "tavotto_captured": 1, "tavotto_valid": 1},
        "executions": {"native": 1, "tavotto": 1},
        "comparable": True,
        "stages": {stage: {"status": "passed"} for stage in research.cc.STAGES},
        "failure_category": {"category": None, "source_code": None},
        "environment_before": {"fingerprint": "f" * 64},
        "unverified": ["Synthetic accounting contract, not scientific execution"],
    }


def report_for(rows: list[dict]) -> dict:
    return {
        "source": {"commit": SHA, "dirty": False},
        "suite": "compat_engine",
        "run_id": "synthetic-accounting-report",
        "cases": rows,
    }


@pytest.fixture
def cohort():
    return cohort_record()


@pytest.fixture
def frozen_inputs(tmp_path, monkeypatch):
    """Freeze real input bytes, while leaving manifest-schema tests to cc."""
    cohort = cohort_record()
    runtime_lock = tmp_path / "packaging/runtime-lock.json"
    runtime_lock.parent.mkdir()
    runtime_lock.write_bytes(b'{"synthetic_contract_only": true, "packages": []}\n')
    cohort["runtime_lock_sha256"] = hashlib.sha256(runtime_lock.read_bytes()).hexdigest()
    manifest = {"cases": []}
    for project in cohort["development"]:
        script = f"cases/research/{project['project_id']}/plot.py"
        license_file = f"cases/research/{project['project_id']}/LICENSE"
        files = {}
        for name, contents in (
            (script, b"# frozen fixture bytes; not executed\n"),
            (license_file, b"Synthetic license evidence for a contract test only\n"),
        ):
            relative = "tests/compat/" + name
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
            files[relative] = hashlib.sha256(contents).hexdigest()
        project["files"] = files
        project["staged_files"] = list(files)
        project["license_files"] = ["tests/compat/" + license_file]
        project["upstream_path_map"] = {
            "plot.py": "tests/compat/" + script,
            "LICENSE": "tests/compat/" + license_file,
        }
        recipe = f"tests/compat/research-environments/{project['case_id']}.txt"
        recipe_path = tmp_path / recipe
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_bytes(b"# synthetic environment recipe; never installed\n")
        project["environment_recipe"] = {
            "path": recipe,
            "sha256": hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
        }
        manifest["cases"].append(
            {
                "id": project["case_id"],
                "research_project_id": project["project_id"],
                "script": script,
                "script_dest": "plot.py",
                "entry": "__main__",
                "extra_files": {"LICENSE": license_file},
                "reference_invocation": copy.deepcopy(project["launch"]),
                "project_directories": [],
                "stem": "plot",
                "expected_figures": 1,
                "expected": {},
                "mutations": [copy.deepcopy(project["tasks"][0]["mutation"])],
                "semantic_expectations": {"editable": [["axes_0.lines_0", "color"]]},
                "export_formats": ["pdf", "png", "svg"],
            }
        )
    path = tmp_path / "cohort.json"
    monkeypatch.setattr(research, "load_cases", lambda _root: manifest["cases"])
    monkeypatch.setattr(research.sources, "source_file", lambda _case, name, *, root: root / name)

    def load():
        path.write_text(json.dumps(cohort), encoding="utf-8")
        return research.load_cohort(path=path, root=tmp_path, verify_files=True)

    return cohort, manifest, tmp_path, load


@pytest.fixture
def snapshot_inputs(frozen_inputs):
    """Use the existing full Guardian protocol fixture; never execute its script."""
    from test_compat_guardian import GuardianBase

    cohort, _, root, load = frozen_inputs
    load()
    (root / "tests/compat/research-cohort.json").write_text(json.dumps(cohort))
    evidence_root = root / research.SNAPSHOT_EVIDENCE
    evidence_root.mkdir(parents=True)
    harness = GuardianBase()
    harness.cid = cohort["development"][0]["case_id"]
    harness.files = {"plot.py": "f" * 64}
    harness.fixture = {
        "files": harness.files,
        "source_sha256": cg.canonical(harness.files),
        "data_sha256": cg.canonical({}),
    }
    harness.setUp()
    try:
        report = copy.deepcopy(harness.report)
        report["cases"] = []
        for project in cohort["development"]:
            row = copy.deepcopy(harness.row)
            row["id"] = project["case_id"]
            report["cases"].append(row)
        report["expected_cases"] = [row["id"] for row in report["cases"]]
        report["corpus_sha256"] = cg.canonical(
            {row["id"]: row["fixture"]["files"] for row in report["cases"]}
        )
        report["coverage"] = cg.coverage_table(report["cases"])
        report_path = evidence_root / "guardian.json"
        report_path.write_text(json.dumps(report))
        receipts = setup_receipts(cohort, evidence_root)
        snapshot = research.project_reports(
            cohort, [research.summary.load_report(report_path)], SHA
        )
        research.check_setup_receipts(snapshot, receipts)
        paths = [report_path, *receipts]
        snapshot["evidence"] = {
            "reports": [report_path.relative_to(root).as_posix()],
            "setup_receipts": [path.relative_to(root).as_posix() for path in receipts],
            "sha256": {
                path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths
            },
        }
        snapshot_path = root / research.SNAPSHOT

        def save():
            snapshot_path.write_text(json.dumps(snapshot))

        save()
        yield root, snapshot_path, snapshot, report_path, save
    finally:
        harness.doCleanups()


def test_all_sixteen_projects_have_one_fixed_stage_denominator(cohort):
    rows = [report_row(p["case_id"]) for p in cohort["development"]]
    rows[-1]["status"] = "failed"
    rows[-1]["failure_category"] = {"category": "editing_capability", "source_code": "stage:edit"}
    rows[-1]["stages"]["edit"]["status"] = "failed"
    for stage in ("replay", "export", "fidelity"):
        rows[-1]["stages"][stage]["status"] = "not_run"

    out = research.project_reports(cohort, [report_for(rows)], SHA)

    assert out["n_raw"] == out["n_eligible"] == 16
    assert len(out["projects"]) == 16
    assert out["holdout_sealed_not_run"] == 8
    assert out["status_counts"] == {"passed": 15, "failed": 1}
    assert {value["denominator"] for value in out["stages_fixed_denominator"].values()} == {16}
    assert out["stages_fixed_denominator"]["capture"]["passed"] == 16
    assert out["stages_fixed_denominator"]["edit"]["passed"] == 15
    assert out["stages_fixed_denominator"]["export"]["passed"] == 15


def test_missing_reports_stay_in_raw_cohort_with_explicit_not_run_rows(cohort):
    one = report_row(cohort["development"][0]["case_id"])
    out = research.project_reports(cohort, [report_for([one])], SHA)

    assert out["n_raw"] == len(out["projects"]) == 16
    assert out["n_eligible"] == 1
    assert out["status_counts"] == {"passed": 1, "not_run": 15}
    assert len(out["missing_cases"]) == 15
    assert len(out["clusters"]["not_run:report_missing"]) == 15
    missing = [row for row in out["projects"] if row["case_id"] in out["missing_cases"]]
    assert all(not row["eligible"] and row["source_code"] == "report_missing" for row in missing)
    assert all(row["stages"] == {} for row in missing)
    assert {value["denominator"] for value in out["stages_fixed_denominator"].values()} == {1}


def test_no_reports_is_not_a_zero_case_success(cohort, monkeypatch, tmp_path):
    monkeypatch.setattr(research, "load_cohort", lambda: cohort)
    destination = tmp_path / "summary.json"
    receipts = setup_receipts(cohort, tmp_path)
    code = research.main(
        [
            "summarize",
            "--expected-sha",
            SHA,
            "--json-out",
            str(destination),
            "--setup-receipts",
            *map(str, receipts),
        ]
    )
    out = json.loads(destination.read_text())

    assert code == 3
    assert out["n_raw"] == len(out["missing_cases"]) == 16
    assert out["status_counts"] == {"not_run": 16}
    assert out["n_eligible"] == 0
    assert all(
        value == {"passed": 0, "denominator": 0}
        for value in out["stages_fixed_denominator"].values()
    )


@pytest.mark.parametrize("duplicate_in_same_report", [False, True])
def test_duplicate_case_cannot_inflate_numerator(cohort, duplicate_in_same_report):
    row = report_row(cohort["development"][0]["case_id"])
    reports = (
        [report_for([row, copy.deepcopy(row)])]
        if duplicate_in_same_report
        else [report_for([row]), report_for([copy.deepcopy(row)])]
    )
    with pytest.raises(cg.EvidenceError, match="duplicate"):
        research.project_reports(cohort, reports, SHA)


@pytest.mark.parametrize(
    "source", [{"commit": "0" * 40, "dirty": False}, {"commit": SHA, "dirty": True}]
)
def test_wrong_sha_or_dirty_checkout_cannot_supply_baseline_evidence(cohort, source):
    report = report_for([report_row(cohort["development"][0]["case_id"])])
    report["source"] = source
    with pytest.raises(cg.EvidenceError, match="clean enrollment commit"):
        research.project_reports(cohort, [report], SHA)


@pytest.mark.parametrize(
    "native_status", ["invalid_fixture", "environment_unavailable", "not_run", "unknown"]
)
def test_native_failure_is_visible_but_never_eligible_or_product_failure(cohort, native_status):
    row = report_row(cohort["development"][0]["case_id"])
    row["native"]["status"] = row["status"] = native_status
    row["failure_category"] = {"category": "invalid_fixture", "source_code": "native_script_error"}
    out = research.project_reports(cohort, [report_for([row])], SHA)

    assert out["n_raw"] == 16
    assert out["n_eligible"] == 0
    assert out["projects"][0]["native_status"] == native_status
    assert not any(key.startswith("product:") for key in out["clusters"])
    assert all(value["passed"] == 0 for value in out["stages_fixed_denominator"].values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("comparable", False),
        ("native_figures", 0),
        ("native_executions", 0),
        ("native_executions", None),
    ],
)
def test_missing_comparability_figure_or_execution_proof_cannot_enter_eligible(
    cohort, field, value
):
    row = report_row(cohort["development"][0]["case_id"])
    row["status"] = "unknown"
    row["failure_category"] = {"category": "unknown", "source_code": "missing_proof"}
    if field == "native_figures":
        row["figures"]["native"] = value
    elif field == "native_executions":
        row["executions"]["native"] = value
    else:
        row[field] = value
    out = research.project_reports(cohort, [report_for([row])], SHA)
    assert out["n_raw"] == 16
    assert out["n_eligible"] == 0


def test_runner_crash_keeps_native_eligible_denominator_without_product_attribution(cohort):
    row = report_row(cohort["development"][0]["case_id"])
    row["status"] = "unknown"
    row["failure_category"] = {"category": "unknown", "source_code": "runner_crash"}
    row["stages"]["export"]["status"] = "not_run"
    out = research.project_reports(cohort, [report_for([row])], SHA)
    assert out["n_eligible"] == 1
    assert out["stages_fixed_denominator"]["export"] == {"passed": 0, "denominator": 1}
    assert not any(key.startswith("product:") for key in out["clusters"])
    assert out["clusters"]["prerequisite_or_evidence:unknown:runner_crash"] == ["project_00"]


def test_holdout_report_case_is_rejected_not_aggregated(cohort):
    row = report_row(cohort["holdout"][0]["project_id"])
    with pytest.raises(cg.EvidenceError, match="unexpected"):
        research.project_reports(cohort, [report_for([row])], SHA)


def setup_receipts(cohort, root):
    paths = []
    for project in cohort["development"]:
        path = root / f"{project['case_id']}.json"
        path.write_text(
            json.dumps(
                {
                    "case_id": project["case_id"],
                    "source_sha": SHA,
                    "steps": {
                        "core": "success",
                        "fonts": "success",
                        "scientific": "success",
                        "tex": "skipped",
                    },
                }
            )
        )
        paths.append(path)
    return paths


def test_failed_setup_removes_product_attribution_and_eligible_numerator(cohort, tmp_path):
    rows = [report_row(p["case_id"]) for p in cohort["development"]]
    rows[0]["status"] = "failed"
    rows[0]["stages"]["edit"]["status"] = "failed"
    rows[0]["failure_category"] = {"category": "editing_capability", "source_code": "stage:edit"}
    out = research.project_reports(cohort, [report_for(rows)], SHA)
    receipts = setup_receipts(cohort, tmp_path)
    failed = json.loads(receipts[0].read_text())
    failed["steps"]["scientific"] = "failure"
    receipts[0].write_text(json.dumps(failed))

    research.check_setup_receipts(out, receipts)

    assert out["n_raw"] == 16
    assert out["n_eligible"] == 15
    assert out["status_counts"] == {"not_run": 1, "passed": 15}
    assert out["projects"][0]["source_code"] == "environment_setup_failed"
    assert all(
        value == {"passed": 15, "denominator": 15}
        for value in out["stages_fixed_denominator"].values()
    )
    assert not any(key.startswith("product:") for key in out["clusters"])
    key = "prerequisite_or_evidence:environment_resolution:environment_setup_failed"
    assert out["clusters"][key] == ["project_00"]


@pytest.mark.parametrize("defect", ["external_case", "duplicate", "wrong_sha"])
def test_setup_receipts_cannot_mix_external_duplicate_or_wrong_commit_evidence(
    cohort, tmp_path, defect
):
    out = research.project_reports(cohort, [], SHA)
    receipts = setup_receipts(cohort, tmp_path)
    if defect == "duplicate":
        receipts.append(receipts[0])
    else:
        wrong = json.loads(receipts[0].read_text())
        if defect == "external_case":
            wrong["case_id"] = "outside_development"
        else:
            wrong["source_sha"] = "0" * 40
        receipts[0].write_text(json.dumps(wrong))
    with pytest.raises(cg.EvidenceError, match="setup receipt"):
        research.check_setup_receipts(out, receipts)


def test_missing_setup_receipt_keeps_sixteen_rows_and_cannot_qualify_a_passing_report(
    cohort, tmp_path
):
    rows = [report_row(p["case_id"]) for p in cohort["development"]]
    out = research.project_reports(cohort, [report_for(rows)], SHA)
    receipts = setup_receipts(cohort, tmp_path)
    research.check_setup_receipts(out, receipts[1:])
    assert out["n_raw"] == len(out["projects"]) == 16
    assert out["n_eligible"] == 15
    assert out["missing_setup_receipts"] == ["research_contract_00"]
    missing = out["projects"][0]
    assert missing["status"] == "not_run"
    assert missing["source_code"] == "setup_receipt_missing"
    assert missing["setup"] is None
    assert out["status_counts"] == {"not_run": 1, "passed": 15}
    assert all(value["denominator"] == 15 for value in out["stages_fixed_denominator"].values())
    assert not any(key.startswith("product:") for key in out["clusters"])


def test_no_reports_or_receipts_still_writes_a_fixed_cohort_summary(cohort, tmp_path, monkeypatch):
    monkeypatch.setattr(research, "load_cohort", lambda: cohort)
    path = tmp_path / "summary.json"
    code = research.main(["summarize", "--expected-sha", SHA, "--json-out", str(path)])
    out = json.loads(path.read_text())
    assert code == 3
    assert out["n_raw"] == len(out["projects"]) == len(out["missing_setup_receipts"]) == 16
    assert out["n_eligible"] == 0
    assert out["status_counts"] == {"not_run": 16}
    assert all(p["source_code"] == "setup_receipt_missing" for p in out["projects"])


def test_required_tex_cannot_be_skipped_even_with_a_passing_observation(cohort, tmp_path):
    cohort["development"][0]["requires_tex"] = True
    rows = [report_row(p["case_id"]) for p in cohort["development"]]
    out = research.project_reports(cohort, [report_for(rows)], SHA)
    research.check_setup_receipts(out, setup_receipts(cohort, tmp_path))
    assert out["n_eligible"] == 15
    assert out["projects"][0]["source_code"] == "environment_setup_failed"
    assert out["projects"][0]["status"] == "not_run"


def test_frozen_cohort_accepts_exact_sources_and_opaque_holdout(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    assert load() == cohort


@pytest.mark.parametrize(
    "defect",
    ["changed_bytes", "wrong_hash", "missing_commitment", "malformed_commitment", "missing_file"],
)
def test_runtime_lock_must_match_the_frozen_byte_commitment(frozen_inputs, defect):
    cohort, _, root, load = frozen_inputs
    runtime_lock = root / "packaging/runtime-lock.json"
    if defect == "changed_bytes":
        # Equivalent JSON with different bytes still violates the preregistered lock.
        runtime_lock.write_bytes(runtime_lock.read_bytes() + b"\n")
    elif defect == "wrong_hash":
        cohort["runtime_lock_sha256"] = "0" * 64
    elif defect == "missing_commitment":
        del cohort["runtime_lock_sha256"]
    elif defect == "malformed_commitment":
        cohort["runtime_lock_sha256"] = "not-a-sha256"
    else:
        runtime_lock.unlink()
    with pytest.raises(cg.EvidenceError, match="runtime lock"):
        load()


def test_provenance_only_archive_is_verified_without_becoming_runtime_input(frozen_inputs):
    cohort, _, root, load = frozen_inputs
    project = cohort["development"][0]
    relative = "tests/compat/research-provenance/project_00/package/__init__.py"
    archive = root / relative
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"# provenance-only partial package; never stage or import\n")
    project["files"][relative] = hashlib.sha256(archive.read_bytes()).hexdigest()
    project["upstream_path_map"]["package/__init__.py"] = relative
    assert relative not in project["staged_files"]
    assert load() == cohort
    archive.write_bytes(b"modified archive\n")
    with pytest.raises(cg.EvidenceError, match="source bytes changed"):
        load()


@pytest.mark.parametrize("group", ["development", "holdout"])
def test_frozen_cohort_rejects_a_missing_project(frozen_inputs, group):
    cohort, _, _, load = frozen_inputs
    cohort[group].pop()
    with pytest.raises(cg.EvidenceError, match="16 development and 8 sealed"):
        load()


def test_two_projects_from_one_upstream_are_not_independent(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    cohort["development"][1]["upstream"]["repository"] = cohort["development"][0]["upstream"][
        "repository"
    ].upper()
    with pytest.raises(cg.EvidenceError, match="independent"):
        load()


def test_duplicate_project_identity_is_rejected(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    cohort["holdout"][0]["project_id"] = cohort["development"][0]["project_id"]
    with pytest.raises(cg.EvidenceError, match="duplicate project"):
        load()


@pytest.mark.parametrize("revision", ["main", "a" * 39, "g" * 40])
def test_upstream_revision_must_be_an_immutable_commit(frozen_inputs, revision):
    cohort, _, _, load = frozen_inputs
    cohort["development"][0]["upstream"]["commit"] = revision
    with pytest.raises(cg.EvidenceError, match="immutable"):
        load()


@pytest.mark.parametrize("enrolled", [True, False])
def test_holdout_cannot_be_added_to_executable_manifest(frozen_inputs, enrolled):
    cohort, manifest, _, load = frozen_inputs
    project_id = cohort["holdout"][0]["project_id"]
    case = {"id": project_id, "script": "never_execute.py"}
    if enrolled:
        case["research_project_id"] = project_id
    manifest["cases"].append(case)
    with pytest.raises(cg.EvidenceError, match="manifest differs|holdout leaked"):
        load()


def test_holdout_identity_details_are_rejected(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    cohort["holdout"][0]["upstream"] = {"repository": "do-not-expose/holdout"}
    with pytest.raises(cg.EvidenceError, match="non-opaque"):
        load()


@pytest.mark.parametrize("mutate_license", [False, True])
def test_source_or_license_hash_changes_are_rejected(frozen_inputs, mutate_license):
    cohort, _, root, load = frozen_inputs
    project = cohort["development"][0]
    name = project["license_files"][0] if mutate_license else next(iter(project["files"]))
    with (root / name).open("ab") as handle:
        handle.write(b"changed after freeze\n")
    with pytest.raises(cg.EvidenceError, match="source bytes changed"):
        load()


def test_uninventoried_staged_helper_is_rejected(frozen_inputs):
    _, manifest, _, load = frozen_inputs
    manifest["cases"][0]["extra_files"]["unfrozen.py"] = "cases/unfrozen.py"
    with pytest.raises(cg.EvidenceError, match="staged inputs differ"):
        load()


@pytest.mark.parametrize("key", ["tasks", "license_files"])
def test_empty_preregistered_task_or_license_evidence_is_rejected(frozen_inputs, key):
    cohort, _, _, load = frozen_inputs
    cohort["development"][0][key] = []
    with pytest.raises(cg.EvidenceError, match="tasks and license evidence|license files"):
        load()


@pytest.mark.parametrize(
    "field,value",
    [
        ("script_dest", "other.py"),
        ("entry", "build"),
        ("reference_invocation", {"cwd": "other", "argv": []}),
        ("reference_invocation", {"cwd": ".", "argv": ["different_analysis"]}),
        ("project_directories", ["different_output"]),
        ("stem", "different_figure"),
        ("expected_figures", 2),
        ("mutations", [{"gid": "axes_0.title", "prop": "fontsize", "value": 16}]),
        ("semantic_expectations", {"editable": [["axes_0.title", "fontsize"]]}),
        ("export_formats", ["png"]),
        ("expected", {"edit": False}),
    ],
)
def test_manifest_cannot_change_the_frozen_launch_figure_or_task(frozen_inputs, field, value):
    _, manifest, _, load = frozen_inputs
    manifest["cases"][0][field] = value
    with pytest.raises(cg.EvidenceError, match="frozen"):
        load()


def test_runtime_destination_cannot_be_renamed_while_preserving_source_set(frozen_inputs):
    _, manifest, _, load = frozen_inputs
    files = manifest["cases"][0]["extra_files"]
    files["renamed_LICENSE"] = files.pop("LICENSE")
    with pytest.raises(cg.EvidenceError, match="runtime destination"):
        load()


@pytest.mark.parametrize("defect", ["changed_bytes", "wrong_path", "wrong_hash"])
def test_scientific_environment_recipe_is_frozen_independently_of_source_inventory(
    frozen_inputs, defect
):
    cohort, _, root, load = frozen_inputs
    recipe = cohort["development"][0]["environment_recipe"]
    assert recipe["path"] not in cohort["development"][0]["files"]
    if defect == "changed_bytes":
        (root / recipe["path"]).write_bytes(b"unfrozen-package==99\n")
    elif defect == "wrong_path":
        recipe["path"] = cohort["development"][1]["environment_recipe"]["path"]
    else:
        recipe["sha256"] = "0" * 64
    with pytest.raises(cg.EvidenceError, match="environment recipe"):
        load()


@pytest.mark.parametrize("destination", ["renamed.py", "../plot.py", "/plot.py", "C:\\plot.py"])
def test_source_package_cannot_remap_frozen_bytes_or_escape_staging(frozen_inputs, destination):
    cohort, _, _, load = frozen_inputs
    project = cohort["development"][0]
    project["source_package_files"] = {destination: project["upstream_path_map"]["plot.py"]}
    with pytest.raises(cg.EvidenceError, match="source package mapping|project-relative"):
        load()


def test_source_package_cannot_install_a_source_outside_its_frozen_inventory(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    project = cohort["development"][0]
    project["source_package_files"] = {"plot.py": "tests/compat/unfrozen.py"}
    with pytest.raises(cg.EvidenceError, match="source package mapping"):
        load()


def test_matching_source_package_mapping_is_accepted_without_execution(frozen_inputs):
    cohort, _, _, load = frozen_inputs
    project = cohort["development"][0]
    project["source_package_files"] = {"plot.py": project["upstream_path_map"]["plot.py"]}
    assert load() == cohort


@pytest.mark.parametrize("defect", ["wrong_hash", "wrong_module", "missing_archive"])
def test_installed_helper_identity_must_come_from_archived_upstream_bytes(frozen_inputs, defect):
    cohort, _, _, load = frozen_inputs
    project = cohort["development"][0]
    project["dependency_helper_integrity"] = {
        "module": "plot",
        "archived_path": "plot.py",
        "sha256": project["files"][project["upstream_path_map"]["plot.py"]],
    }
    assert load() == cohort
    helper = project["dependency_helper_integrity"]
    if defect == "wrong_hash":
        helper["sha256"] = "0" * 64
    elif defect == "wrong_module":
        helper["module"] = "other.module"
    else:
        helper["archived_path"] = "missing.py"
    with pytest.raises(cg.EvidenceError, match="helper identity"):
        load()


def test_snapshot_reproduces_full_guardian_records_without_writing(snapshot_inputs):
    root, _, snapshot, _, _ = snapshot_inputs
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert research.verify_snapshot(root=root) == snapshot
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_missing_snapshot_is_a_failure_not_an_empty_baseline(tmp_path):
    with pytest.raises(cg.EvidenceError, match="snapshot is missing"):
        research.verify_snapshot(root=tmp_path)


@pytest.mark.parametrize("kind", ["reports", "setup_receipts"])
def test_snapshot_rejects_changed_evidence_bytes(snapshot_inputs, kind):
    root, _, snapshot, _, _ = snapshot_inputs
    evidence = root / snapshot["evidence"][kind][0]
    evidence.write_bytes(evidence.read_bytes() + b"\n")
    with pytest.raises(cg.EvidenceError, match="evidence hash differs"):
        research.verify_snapshot(root=root)


def test_snapshot_rejects_a_missing_referenced_artifact(snapshot_inputs):
    root, _, _, report_path, _ = snapshot_inputs
    report_path.unlink()
    with pytest.raises(cg.EvidenceError, match="evidence must be a file"):
        research.verify_snapshot(root=root)


@pytest.mark.parametrize(
    "defect", ["missing_hash", "extra_hash", "duplicate", "cross_list_duplicate"]
)
def test_snapshot_evidence_inventory_cannot_omit_or_duplicate_references(snapshot_inputs, defect):
    root, _, snapshot, _, save = snapshot_inputs
    evidence = snapshot["evidence"]
    report = evidence["reports"][0]
    if defect == "missing_hash":
        del evidence["sha256"][report]
    elif defect == "extra_hash":
        evidence["sha256"]["unreferenced.json"] = "0" * 64
    elif defect == "duplicate":
        evidence["reports"].append(report)
    else:
        evidence["setup_receipts"].append(report)
    save()
    with pytest.raises(cg.EvidenceError, match="unique references and exact hash coverage"):
        research.verify_snapshot(root=root)


def test_snapshot_evidence_cannot_reference_another_repository_directory(snapshot_inputs):
    root, _, snapshot, report_path, save = snapshot_inputs
    outside = root / "other-evidence.json"
    outside.write_bytes(report_path.read_bytes())
    evidence = snapshot["evidence"]
    old = evidence["reports"][0]
    evidence["reports"] = [outside.name]
    evidence["sha256"][outside.name] = evidence["sha256"].pop(old)
    save()
    with pytest.raises(cg.EvidenceError, match="baseline evidence directory"):
        research.verify_snapshot(root=root)


def test_snapshot_evidence_cannot_escape_its_directory_through_a_parent_symlink(snapshot_inputs):
    root, _, snapshot, report_path, save = snapshot_inputs
    outside = root / "other-evidence"
    outside.mkdir()
    (outside / "guardian.json").write_bytes(report_path.read_bytes())
    link = report_path.parent / "linked"
    link.symlink_to(outside, target_is_directory=True)
    evidence = snapshot["evidence"]
    old = evidence["reports"][0]
    relative = (link / "guardian.json").relative_to(root).as_posix()
    evidence["reports"] = [relative]
    evidence["sha256"][relative] = evidence["sha256"].pop(old)
    save()
    with pytest.raises(cg.EvidenceError, match="baseline evidence directory"):
        research.verify_snapshot(root=root)


@pytest.mark.parametrize(
    "defect",
    ["drop_project", "duplicate_project", "numerator", "bool_count", "stage", "extra_field"],
)
def test_snapshot_cannot_forge_project_membership_or_any_recomputed_field(snapshot_inputs, defect):
    root, _, snapshot, _, save = snapshot_inputs
    if defect == "drop_project":
        snapshot["projects"].pop()
    elif defect == "duplicate_project":
        snapshot["projects"][-1] = copy.deepcopy(snapshot["projects"][0])
    elif defect == "numerator":
        snapshot["n_eligible"] = 15
    elif defect == "bool_count":
        snapshot["projects"][0]["executions"]["native"] = True
    elif defect == "stage":
        snapshot["projects"][0]["stages"]["edit"] = "not_run"
    else:
        snapshot["invented_claim"] = "all desktop workflows passed"
    save()
    with pytest.raises(cg.EvidenceError, match="differs from recomputed evidence"):
        research.verify_snapshot(root=root)


def test_snapshot_loads_guardian_proof_even_when_hash_and_projection_are_consistent(
    snapshot_inputs,
):
    root, _, snapshot, report_path, save = snapshot_inputs
    report = json.loads(report_path.read_text())
    report["cases"][0]["executions"]["tavotto"] = 0
    snapshot["projects"][0]["executions"]["tavotto"] = 0
    report_path.write_text(json.dumps(report))
    name = report_path.relative_to(root).as_posix()
    snapshot["evidence"]["sha256"][name] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    save()
    with pytest.raises(cg.EvidenceError, match="proof invariants"):
        research.verify_snapshot(root=root)


def test_snapshot_rejects_duplicate_json_keys(snapshot_inputs):
    root, path, snapshot, _, _ = snapshot_inputs
    contents = json.dumps(snapshot)
    path.write_text('{"evidence": {},' + contents[1:])
    with pytest.raises(cg.EvidenceError, match="duplicate research snapshot key"):
        research.verify_snapshot(root=root)


def test_snapshot_preserves_setup_only_failure_without_inventing_a_product_result(snapshot_inputs):
    root, _, snapshot, report_path, save = snapshot_inputs
    report = json.loads(report_path.read_text())
    missing_id = report["cases"].pop(0)["id"]
    report["expected_cases"].remove(missing_id)
    report["coverage"] = cg.coverage_table(report["cases"])
    report["corpus_sha256"] = cg.canonical(
        {row["id"]: row["fixture"]["files"] for row in report["cases"]}
    )
    report_path.write_text(json.dumps(report))
    evidence = snapshot["evidence"]
    receipts = [root / name for name in evidence["setup_receipts"]]
    receipt = json.loads(receipts[0].read_text())
    assert receipt["case_id"] == missing_id
    receipt["steps"]["scientific"] = "failure"
    receipts[0].write_text(json.dumps(receipt))
    for changed in (report_path, receipts[0]):
        evidence["sha256"][changed.relative_to(root).as_posix()] = hashlib.sha256(
            changed.read_bytes()
        ).hexdigest()
    cohort = research.load_cohort(root / "tests/compat/research-cohort.json", root=root)
    observed = research.project_reports(cohort, [research.summary.load_report(report_path)], SHA)
    research.check_setup_receipts(observed, receipts)
    observed["evidence"] = evidence
    snapshot.clear()
    snapshot.update(observed)
    save()

    verified = research.verify_snapshot(root=root)

    assert verified["n_raw"] == 16
    assert verified["n_eligible"] == 15
    assert verified["missing_cases"] == [missing_id]
    assert verified["projects"][0]["status"] == "not_run"
    assert verified["projects"][0]["source_code"] == "environment_setup_failed"
    assert not any(key.startswith("product:") for key in verified["clusters"])


def test_verify_snapshot_cli_only_verifies_the_selected_snapshot(monkeypatch, tmp_path):
    selected = tmp_path / "observation.json"
    calls = []
    monkeypatch.setattr(research, "verify_snapshot", lambda path: calls.append(path))
    assert research.main(["verify-snapshot", "--snapshot", str(selected)]) == 0
    assert calls == [selected]
    assert not selected.exists()


@pytest.fixture
def snapshot_with_inventories(snapshot_inputs):
    """Every receipt in the two-venv format, with both package lists archived and hash-covered."""
    root, _, snapshot, report_path, save = snapshot_inputs
    evidence = snapshot["evidence"]
    receipts = [root / name for name in evidence["setup_receipts"]]
    evidence["inventories"] = {}
    for receipt_path in receipts:
        stem = receipt_path.stem
        lists = {
            "research": receipt_path.with_name(f"{stem}-research-installed.txt"),
            "tavotto": receipt_path.with_name(f"{stem}-tavotto-installed.txt"),
        }
        lists["research"].write_text("numpy==2.5.2\n")
        lists["tavotto"].write_text("uharfbuzz==0.56.1\n")
        receipt = json.loads(receipt_path.read_text())
        receipt["installed_sha256"] = hashlib.sha256(lists["research"].read_bytes()).hexdigest()
        receipt["tavotto_installed_sha256"] = hashlib.sha256(
            lists["tavotto"].read_bytes()
        ).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        evidence["inventories"][receipt_path.relative_to(root).as_posix()] = {
            role: path.relative_to(root).as_posix() for role, path in lists.items()
        }
    for path in [report_path, *receipts]:
        evidence["sha256"][path.relative_to(root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    for entry in evidence["inventories"].values():
        for name in entry.values():
            evidence["sha256"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    cohort = research.load_cohort(root / "tests/compat/research-cohort.json", root=root)
    observed = research.project_reports(cohort, [research.summary.load_report(report_path)], SHA)
    research.check_setup_receipts(observed, receipts)
    observed["evidence"] = evidence
    snapshot.clear()
    snapshot.update(observed)
    save()
    return root, snapshot, save


def test_snapshot_with_archived_package_lists_verifies(snapshot_with_inventories):
    root, snapshot, _ = snapshot_with_inventories
    assert research.verify_snapshot(root=root) == snapshot


@pytest.mark.parametrize("role", ["research", "tavotto"])
def test_snapshot_rejects_a_tampered_package_list(snapshot_with_inventories, role):
    root, snapshot, save = snapshot_with_inventories
    entry = next(iter(snapshot["evidence"]["inventories"].values()))
    path = root / entry[role]
    path.write_text(path.read_text() + "evil==1\n")
    # Keep the snapshot's own file hash consistent so only the receipt's recorded hash can catch it.
    snapshot["evidence"]["sha256"][entry[role]] = hashlib.sha256(path.read_bytes()).hexdigest()
    save()
    with pytest.raises(cg.EvidenceError, match="package list differs"):
        research.verify_snapshot(root=root)


@pytest.mark.parametrize("role", ["research", "tavotto"])
def test_snapshot_rejects_a_deleted_package_list(snapshot_with_inventories, role):
    root, snapshot, _ = snapshot_with_inventories
    entry = next(iter(snapshot["evidence"]["inventories"].values()))
    (root / entry[role]).unlink()
    with pytest.raises(cg.EvidenceError, match="evidence must be a file"):
        research.verify_snapshot(root=root)


def test_snapshot_rejects_a_two_venv_receipt_whose_lists_are_not_archived(
    snapshot_with_inventories,
):
    root, snapshot, save = snapshot_with_inventories
    receipt_name = next(iter(snapshot["evidence"]["inventories"]))
    for name in snapshot["evidence"]["inventories"].pop(receipt_name).values():
        del snapshot["evidence"]["sha256"][name]
    save()
    with pytest.raises(cg.EvidenceError, match="package lists are not archived"):
        research.verify_snapshot(root=root)


def test_archived_sources_are_source_not_data_and_distinguish_cases():
    a = {"tests/compat/cases/research/a/upstream/plot.py.source": "a" * 64, "data/x.csv": "d" * 64}
    b = {"tests/compat/cases/research/b/upstream/plot.py.source": "b" * 64, "data/x.csv": "d" * 64}
    fa, fb = cg.fixture_digests(a), cg.fixture_digests(b)
    assert fa["source_sha256"] != fb["source_sha256"]
    assert fa["data_sha256"] == fb["data_sha256"]
    assert fa["source_sha256"] != cg.canonical({})
    plain = cg.fixture_digests({"plot.py": "a" * 64, "data/x.csv": "d" * 64})
    assert plain["source_sha256"] == cg.canonical({"plot.py": "a" * 64})


def _refused_row():
    path = Path(__file__).parent / "compat" / "launch-context-unsupported-row.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["comparable"] = False
    cg.finalize_row(row)
    return row


def test_pre_launch_refusal_stays_in_the_native_qualified_denominator_as_a_product_failure():
    refused = _refused_row()
    assert refused["status"] == "failed"
    assert cg.native_eligible(refused)
    passed_stages = {s: "passed" for s in research.cc.STAGES}
    healthy = {
        "project_id": "healthy",
        "status": "passed",
        "eligible": True,
        "stages": passed_stages,
        "failure_category": None,
        "source_code": None,
    }
    item = {
        "project_id": "cantera",
        "status": refused["status"],
        "eligible": cg.native_eligible(refused),
        "stages": {s: c["status"] for s, c in refused["stages"].items()},
        "failure_category": refused["failure_category"]["category"],
        "source_code": refused["failure_category"]["source_code"],
    }
    data = {"projects": [healthy, item]}
    research.refresh_accounting(data)
    assert data["n_eligible"] == 2
    assert data["stages_fixed_denominator"]["discover"] == {"passed": 1, "denominator": 2}
    assert data["stages_fixed_denominator"]["fidelity"] == {"passed": 1, "denominator": 2}
    assert data["clusters"] == {"product:cwd_or_data_path:launch_context_unsupported": ["cantera"]}


def test_unlaunched_rows_without_a_refusal_code_are_not_eligible():
    row = _refused_row()
    row["raw"]["detail"]["discover"]["code"] = "something_else"
    row["comparable"] = False
    assert not cg.native_eligible(row)


# ---- #1363：receipt 与报告按工作流出处绑定 ----------------------------------------------------


def wf(run_id="100", attempt="1", job="baseline", case_id=None):
    return {"run_id": run_id, "run_attempt": attempt, "job": job, "case_id": case_id}


def provenance_pair(cohort, tmp_path, report_wf, receipt_wf):
    """16 个项目的投影；报告与每份 receipt 的出处由参数给定（`research.pv.ABSENT` = 不写该字段）。"""
    report = report_for([report_row(p["case_id"]) for p in cohort["development"]])
    if report_wf is not research.pv.ABSENT:
        report["workflow"] = report_wf
    out = research.project_reports(cohort, [report], SHA)
    receipts = setup_receipts(cohort, tmp_path)
    if receipt_wf is not research.pv.ABSENT:
        for path in receipts:
            data = json.loads(path.read_text())
            data["workflow"] = dict(receipt_wf, case_id=data["case_id"])
            path.write_text(json.dumps(data))
    return out, receipts


def test_legacy_report_and_receipt_without_workflow_use_the_old_rule(cohort, tmp_path):
    out, receipts = provenance_pair(cohort, tmp_path, research.pv.ABSENT, research.pv.ABSENT)
    research.check_setup_receipts(out, receipts)
    assert out["n_eligible"] == 16
    assert all("workflow" not in p for p in out["projects"])
    assert all("workflow" not in r for r in out["reports"])


def test_same_run_report_and_receipts_pair(cohort, tmp_path):
    out, receipts = provenance_pair(cohort, tmp_path, wf(), wf())
    research.check_setup_receipts(out, receipts)
    assert out["n_eligible"] == 16
    assert out["reports"][0]["workflow"] == wf()


def test_local_runs_with_null_provenance_on_both_sides_use_the_old_rule(cohort, tmp_path):
    out, receipts = provenance_pair(cohort, tmp_path, None, research.pv.ABSENT)
    for path in receipts:
        data = json.loads(path.read_text())
        data["workflow"] = None
        path.write_text(json.dumps(data))
    research.check_setup_receipts(out, receipts)
    assert out["n_eligible"] == 16


@pytest.mark.parametrize(
    "report_wf, receipt_wf",
    [
        (wf("100"), wf("101")),  # 两次运行交叉配对
        (wf(attempt="1"), wf(attempt="2")),  # 同一次运行的两次 attempt
        (wf(job="baseline"), wf(job="other")),
        (wf(), None),  # receipt 无出处（null）
        (wf(), research.pv.ABSENT),  # receipt 来自旧版 workflow
        (None, wf()),
        (research.pv.ABSENT, wf()),  # 报告来自旧版
    ],
)
def test_receipt_and_report_from_different_or_one_sided_provenance_fail(
    cohort, tmp_path, report_wf, receipt_wf
):
    out, receipts = provenance_pair(cohort, tmp_path, report_wf, research.pv.ABSENT)
    for path in receipts:
        data = json.loads(path.read_text())
        if receipt_wf is research.pv.ABSENT:
            pass
        elif receipt_wf is None:
            data["workflow"] = None
        else:
            data["workflow"] = dict(receipt_wf, case_id=data["case_id"])
        path.write_text(json.dumps(data))
    with pytest.raises(cg.EvidenceError, match="workflow"):
        research.check_setup_receipts(out, receipts)


def test_receipt_case_in_provenance_must_match_its_case(cohort, tmp_path):
    out, receipts = provenance_pair(cohort, tmp_path, wf(), wf())
    data = json.loads(receipts[0].read_text())
    data["workflow"]["case_id"] = "research_contract_09"
    receipts[0].write_text(json.dumps(data))
    with pytest.raises(cg.EvidenceError, match="case"):
        research.check_setup_receipts(out, receipts)


def test_one_cross_paired_receipt_among_sixteen_fails(cohort, tmp_path):
    out, receipts = provenance_pair(cohort, tmp_path, wf(), wf())
    data = json.loads(receipts[5].read_text())
    data["workflow"]["run_id"] = "999"
    receipts[5].write_text(json.dumps(data))
    with pytest.raises(cg.EvidenceError, match="run_id"):
        research.check_setup_receipts(out, receipts)


def test_snapshot_verification_rejects_cross_paired_runs_and_accepts_matching(snapshot_inputs):
    root, _, snapshot, report_path, save = snapshot_inputs
    evidence = snapshot["evidence"]
    receipts = [root / name for name in evidence["setup_receipts"]]
    report = json.loads(report_path.read_text())
    report["workflow"] = wf()
    report_path.write_text(json.dumps(report))
    for path in receipts:
        data = json.loads(path.read_text())
        data["workflow"] = wf(case_id=data["case_id"])
        path.write_text(json.dumps(data))

    def rebuild():
        for changed in (report_path, *receipts):
            evidence["sha256"][changed.relative_to(root).as_posix()] = hashlib.sha256(
                changed.read_bytes()
            ).hexdigest()
        cohort_ = research.load_cohort(root / "tests/compat/research-cohort.json", root=root)
        observed = research.project_reports(
            cohort_, [research.summary.load_report(report_path)], SHA
        )
        research.check_setup_receipts(observed, receipts)
        observed["evidence"] = evidence
        snapshot.clear()
        snapshot.update(observed)
        save()

    rebuild()
    assert research.verify_snapshot(root=root) == snapshot

    # 把一份 receipt 换成另一次运行的（哈希同步更新，快照其余部分不变）：验证必须失败
    data = json.loads(receipts[0].read_text())
    data["workflow"]["run_attempt"] = "2"
    receipts[0].write_text(json.dumps(data))
    evidence["sha256"][receipts[0].relative_to(root).as_posix()] = hashlib.sha256(
        receipts[0].read_bytes()
    ).hexdigest()
    save()
    with pytest.raises(cg.EvidenceError, match="workflow"):
        research.verify_snapshot(root=root)


def test_committed_first_round_snapshot_is_legacy_and_carries_no_provenance():
    """历史快照永不改写：它的报告与 receipt 都没有 `workflow`，所以走旧规则（见 verify_snapshot 的旧快照用例）。"""
    committed = json.loads(
        (Path(__file__).resolve().parents[1] / research.SNAPSHOT).read_text(encoding="utf-8")
    )
    assert all("workflow" not in report for report in committed["reports"])
    assert all("workflow" not in project for project in committed["projects"])
    assert all("workflow" not in (project["setup"] or {}) for project in committed["projects"])
    for project in committed["projects"]:
        research.pv.check_pair(research.pv.ABSENT, research.pv.ABSENT, project["case_id"])
