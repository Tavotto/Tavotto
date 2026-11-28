"""闭式执行事实和已携资源的引用校验；不运行脚本、不写本机执行配置。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import documentpackage, packageexecution as execution, runconfig

SCRIPT = "scripts/figure.py"
OTHER = "scripts/other.py"
RUN = "rc_0123456789ab"
OTHER_RUN = "rc_abcdef012345"
SHA = "a" * 64
HELPER_SHA = "b" * 64
DATA_SHA = "c" * 64


def block():
    return {
        "version": 1,
        "registry": {
            "scripts": {
                SCRIPT: {
                    "entry": "render",
                    "cost": "light",
                    "notes": "中文备注",
                    "stems": ["F"],
                    "artifacts": {"out/F.svg": "F"},
                    "stem_aliases": {"original": "F"},
                }
            }
        },
        "runs": [
            {
                "id": RUN,
                "script": SCRIPT,
                "argv": ["--series", "data/value.csv", "", "--label", "中文", "space token"],
            }
        ],
        "contexts": [
            {
                "script": SCRIPT,
                "run_config": RUN,
                "run_config_known": True,
                "cwd_mode": "project_root",
                "profile": "safe",
                "basis": [
                    {"id": "data/value.csv", "sha256": DATA_SHA},
                    {"id": "scripts/helper.py", "sha256": HELPER_SHA},
                ],
            }
        ],
        "remaps": [{"kind": "file", "from": "../old\\value.csv", "to_resource": "data/value.csv"}],
        "coverage": {
            "state": "incomplete",
            "issues": [{"script": SCRIPT, "reason": "dependency_set_undeclared"}],
        },
    }


def resource(kind, name, digest):
    return {
        "kind": kind,
        "id": name,
        "path": ("scripts/" if kind == "script" else "assets/") + name,
        "sha256": digest,
        "bytes": 11,
    }


def records():
    return [
        resource("script", SCRIPT, SHA),
        resource("script", "scripts/helper.py", HELPER_SHA),
        resource("file", "data/value.csv", DATA_SHA),
    ]


def changed(raw, path, value):
    current = raw
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    return raw


def issue(raw, reason, *, script=SCRIPT):
    raw["coverage"]["state"] = "incomplete"
    raw["coverage"]["issues"].append({"script": script, "reason": reason})
    return raw


def assert_invalid(raw):
    with pytest.raises(documentpackage.PackageError) as caught:
        execution.validate_execution(raw)
    assert caught.value.reason == "invalid_execution"


def assert_mismatch(raw, resources):
    with pytest.raises(documentpackage.PackageError) as caught:
        execution.validate_resources(raw, resources)
    assert caught.value.reason == "execution_resource_mismatch"


def add_other(raw):
    raw["registry"]["scripts"][OTHER] = {
        "entry": "main",
        "cost": "medium",
        "notes": "",
        "stems": ["G"],
    }
    return raw


def test_valid_execution_is_deep_copied_canonical_data_with_original_array_order():
    raw = block()
    before = copy.deepcopy(raw)
    result = execution.validate_execution(raw)
    assert result == raw and raw == before
    assert list(result) == sorted(result)
    assert result["runs"][0]["argv"] == [
        "--series",
        "data/value.csv",
        "",
        "--label",
        "中文",
        "space token",
    ]
    assert result["contexts"][0]["basis"] == raw["contexts"][0]["basis"]
    assert result["remaps"][0]["from"] == "../old\\value.csv"
    result["runs"][0]["argv"].append("changed")
    result["registry"]["scripts"][SCRIPT]["stems"].append("new")
    result["contexts"][0]["basis"][0]["id"] = "changed"
    assert raw == before
    assert execution.validate_resources(raw, records()) is None


def test_registry_defaults_and_explicit_empty_optional_maps_preserve_declarations():
    raw = block()
    raw["registry"]["scripts"][SCRIPT] = {"artifacts": {}, "stem_aliases": {}}
    result = execution.validate_execution(raw)
    assert result["registry"]["scripts"][SCRIPT] == {
        "entry": "main",
        "cost": "medium",
        "notes": "",
        "stems": [],
        "artifacts": {},
        "stem_aliases": {},
    }


@pytest.mark.parametrize(
    "path,value",
    [
        (("version",), True),
        (("version",), 2),
        (("version",), 1.0),
        (("registry",), {"scripts": {}, "version": 1}),
        (("registry", "scripts", SCRIPT, "notes"), 42),
        (("registry", "scripts", SCRIPT, "entry"), "not-valid-entry"),
        (("registry", "scripts", SCRIPT, "cost"), "unlimited"),
        (("registry", "scripts", SCRIPT, "stems"), None),
        (("registry", "scripts", SCRIPT, "stems"), ["F", "F"]),
        (("registry", "scripts", SCRIPT, "artifacts"), {"out/F.svg": "not-registered"}),
        (("registry", "scripts", SCRIPT, "artifacts"), {"out\\F.svg": "F"}),
        (("registry", "scripts", SCRIPT, "stem_aliases"), {"F": "F"}),
        (("registry", "scripts", SCRIPT, "stem_aliases"), {"one": "F", "two": "F"}),
        (("runs", 0, "id"), "rc_unknown"),
        (("runs", 0, "script"), OTHER),
        (("runs", 0, "argv"), None),
        (("runs", 0, "argv"), []),
        (("runs", 0, "argv"), ("--x",)),
        (("runs", 0, "argv"), [True]),
        (("runs", 0, "argv"), ["a\0b"]),
        (("contexts", 0, "script"), OTHER),
        (("contexts", 0, "run_config"), "not-a-run"),
        (("contexts", 0, "run_config_known"), 1),
        (("contexts", 0, "cwd_mode"), None),
        (("contexts", 0, "cwd_mode"), "/tmp"),
        (("contexts", 0, "profile"), "trusted"),
        (("contexts", 0, "basis", 0, "sha256"), "sha256:" + DATA_SHA),
        (("contexts", 0, "basis", 0, "sha256"), "A" * 64),
        (("coverage", "state"), "unknown"),
        (("coverage", "issues", 0, "script"), OTHER),
        (("coverage", "issues", 0, "reason"), "allow_all"),
        (("remaps", 0, "kind"), "prefix"),
        (("remaps", 0, "from"), "/absolute.csv"),
        (("remaps", 0, "from"), "C:/absolute.csv"),
        (("remaps", 0, "from"), "C:drive-relative.csv"),
        (("remaps", 0, "from"), "\\\\server\\share\\x.csv"),
        (("remaps", 0, "from"), "~/.secrets"),
        (("remaps", 0, "from"), "https://example.invalid/data"),
        (("remaps", 0, "from"), "bad\0request"),
        (("registry", "scripts", SCRIPT, "notes"), "bad\ud800unicode"),
    ],
)
def test_rejects_invalid_closed_metadata(path, value):
    assert_invalid(changed(block(), path, value))


@pytest.mark.parametrize(
    "path,extra",
    [
        ((), "interpreter"),
        (("registry", "scripts", SCRIPT), "granted_at"),
        (("runs", 0), "sensitive"),
        (("contexts", 0), "environment"),
        (("contexts", 0, "basis", 0), "mtime"),
        (("remaps", 0), "to"),
        (("coverage",), "permission"),
        (("coverage", "issues", 0), "path"),
    ],
)
def test_every_layer_rejects_unknown_or_machine_authority_fields(path, extra):
    raw = block()
    current = raw
    for key in path:
        current = current[key]
    current[extra] = "machine-dependent"
    assert_invalid(raw)


@pytest.mark.parametrize(
    "bad",
    ["../escape", "/absolute", "C:/absolute", "a\\b", "a//b", "a/./b", "CON.txt", "a.", "a\0b", ""],
)
def test_resource_ids_use_the_existing_portable_member_authority(bad):
    assert_invalid(changed(block(), ("remaps", 0, "to_resource"), bad))


@pytest.mark.parametrize("collection", ["runs", "contexts", "remaps", "issues", "basis"])
def test_duplicate_identities_are_rejected(collection):
    raw = block()
    target = (
        raw["coverage"]["issues"]
        if collection == "issues"
        else raw["contexts"][0]["basis"]
        if collection == "basis"
        else raw[collection]
    )
    target.append(copy.deepcopy(target[0]))
    assert_invalid(raw)


def test_registry_cannot_silently_discard_contested_artifacts_or_portable_aliases():
    raw = add_other(block())
    raw["registry"]["scripts"][OTHER]["artifacts"] = {"out/F.svg": "G"}
    assert_invalid(raw)
    raw = block()
    raw["registry"]["scripts"]["scripts/FIGURE.py"] = {"stems": ["G"]}
    assert_invalid(raw)
    raw = block()
    raw["registry"]["scripts"][SCRIPT]["artifacts"]["OUT/f.svg"] = "F"
    assert_invalid(raw)


def test_relative_remap_spellings_are_preserved_but_equivalent_requests_cannot_conflict():
    raw = block()
    raw["remaps"].append(
        {"kind": "file", "from": "../old/value.csv", "to_resource": "different.csv"}
    )
    assert_invalid(raw)


@pytest.mark.parametrize("cwd", ["sandbox", "project", "project_root"])
@pytest.mark.parametrize("profile", ["safe", "native", None])
def test_context_modes_are_original_advisory_enums_only(cwd, profile):
    raw = block()
    raw["contexts"][0].update(cwd_mode=cwd, profile=profile)
    result = execution.validate_execution(raw)
    assert result["contexts"][0]["cwd_mode"] == cwd
    assert result["contexts"][0]["profile"] == profile


def test_unknown_run_config_is_distinct_from_verified_empty_arguments():
    raw = block()
    raw["runs"] = []
    raw["contexts"][0].update(run_config=None, run_config_known=False)
    assert_invalid(raw)
    issue(raw, "run_config_unresolved")
    assert execution.validate_execution(raw)["contexts"][0]["run_config_known"] is False
    raw["contexts"][0]["run_config"] = RUN
    assert_invalid(raw)
    raw = block()
    raw["runs"] = []
    raw["contexts"][0]["run_config"] = None
    assert execution.validate_execution(raw)["contexts"][0]["run_config_known"] is True


def test_same_script_paramfree_runtime_and_unknown_disk_context_both_keep_their_evidence():
    raw = block()
    raw["runs"] = []
    raw["contexts"][0]["run_config"] = None
    unknown = copy.deepcopy(raw["contexts"][0])
    unknown["run_config_known"] = False
    raw["contexts"].append(unknown)
    issue(raw, "run_config_unresolved")
    result = execution.validate_execution(raw)
    assert [(row["run_config"], row["run_config_known"]) for row in result["contexts"]] == [
        (None, True),
        (None, False),
    ]
    execution.validate_resources(result, records())
    raw["contexts"].append(copy.deepcopy(unknown))
    assert_invalid(raw)


@pytest.mark.parametrize("reason", ["run_config_missing", "sensitive_run_config"])
def test_unavailable_run_requires_its_exact_script_coverage_issue(reason):
    raw = add_other(block())
    raw["runs"] = []
    assert_invalid(raw)
    issue(raw, reason, script=OTHER)
    assert_invalid(raw)
    issue(raw, reason)
    assert execution.validate_execution(raw)["contexts"][0]["run_config"] == RUN


def test_run_reference_cannot_borrow_another_scripts_arguments_even_with_missing_issue():
    raw = add_other(block())
    raw["runs"][0]["script"] = OTHER
    issue(raw, "run_config_missing")
    assert_invalid(raw)


def test_argv_budget_comes_from_original_runconfig_validator():
    raw = block()
    raw["runs"][0]["argv"] = [""] * (runconfig.execspec.MAX_ARGV_TOKENS + 1)
    assert_invalid(raw)


def test_complete_is_only_a_shape_claim_with_no_issues_not_an_io_completeness_proof():
    raw = block()
    raw["coverage"]["state"] = "complete"
    assert_invalid(raw)
    raw["coverage"]["issues"] = []
    assert execution.validate_execution(raw)["coverage"] == {"state": "complete", "issues": []}
    empty = {
        "version": 1,
        "registry": {"scripts": {}},
        "runs": [],
        "contexts": [],
        "remaps": [],
        "coverage": {"state": "incomplete", "issues": []},
    }
    assert execution.validate_execution(empty) == empty
    execution.validate_resources(empty, [])


def test_resource_check_accepts_matching_file_script_and_ignores_nonexecution_blob_rows():
    rows = records()
    rows.append(
        {
            "kind": "chart_source",
            "path": "assets/tavottofile/chart_sources/opaque.svg",
            "sha256": "d" * 64,
            "bytes": 3,
        }
    )
    before = copy.deepcopy(rows)
    execution.validate_resources(block(), rows)
    assert rows == before
    rows.append(resource("file", SCRIPT, SHA))
    execution.validate_resources(block(), rows)


def test_missing_registered_script_requires_that_scripts_resource_missing_issue():
    rows = records()[1:]
    assert_mismatch(block(), rows)
    raw = issue(add_other(block()), "resource_missing", script=OTHER)
    rows.append(resource("script", OTHER, SHA))
    assert_mismatch(raw, rows)
    issue(raw, "resource_missing")
    execution.validate_resources(raw, rows)


def test_missing_basis_is_scoped_to_its_context_not_an_unrelated_issue():
    rows = [row for row in records() if row["id"] != "scripts/helper.py"]
    assert_mismatch(block(), rows)
    raw = add_other(block())
    rows.append(resource("script", OTHER, SHA))
    issue(raw, "resource_missing", script=OTHER)
    assert_mismatch(raw, rows)
    issue(raw, "resource_missing")
    execution.validate_resources(raw, rows)


@pytest.mark.parametrize(
    "reason", ["source_basis_stale", "resource_missing", "dependency_set_undeclared"]
)
def test_a_coverage_issue_never_authorizes_different_basis_bytes(reason):
    raw = block()
    if reason != "dependency_set_undeclared":
        issue(raw, reason)
    rows = records()
    rows[1]["sha256"] = "d" * 64
    assert_mismatch(raw, rows)


def test_global_file_remap_missing_target_cannot_borrow_any_scripts_issue():
    raw = block()
    raw["contexts"][0]["basis"] = []
    issue(raw, "resource_missing")
    assert_mismatch(raw, records()[:2])


@pytest.mark.parametrize("fault", ["duplicate", "different_bytes", "alias", "bad_digest"])
def test_resources_cannot_hide_duplicate_alias_or_conflicting_content(fault):
    rows = records()
    if fault == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif fault == "different_bytes":
        rows.append(resource("file", SCRIPT, "d" * 64))
    elif fault == "alias":
        rows.append(resource("file", "SCRIPTS/figure.py", SHA))
    else:
        rows[0]["sha256"] = "not-a-hash"
    assert_mismatch(block(), rows)


def test_import_and_validation_are_stdlib_only_and_do_not_create_runtime_state(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    code = """
import json,sys
sys.path.insert(0,sys.argv[1])
from tavotto.engine import packageexecution
raw=json.load(sys.stdin)
packageexecution.validate_execution(raw)
assert not any(name in sys.modules for name in ('matplotlib','numpy','PIL','pikepdf','pypdfium2','tavotto.engine.pool'))
print('stdlib-only')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(repo / "src")],
        input=json.dumps(block()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        cwd=tmp_path,
        env={
            **os.environ,
            "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
            "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "stdlib-only"
    assert list(tmp_path.iterdir()) == []
