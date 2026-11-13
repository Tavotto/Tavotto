"""Checked historical captures remain independent from current execution advice."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil

import pytest

from tavotto.engine import (
    chartpackage,
    databinding,
    figcapture,
    inputdeclaration,
    inputremap,
    packageexecution,
    packageexecution_export as collector,
    registry,
    runconfig,
    svgstore,
    workdir,
)
from test_svgstore import PATCH, SOURCE, _args
from test_svgstore_input_archive import _capture, world as _input_world

world = _input_world
SCRIPT = "plot.py"
ENTRY = {"entry": "main", "cost": "light", "notes": "current advice", "stems": ["F"]}
CURRENT_SOURCE = b"# current source; collector must never execute it\nopen('current.csv')\n"


@pytest.fixture
def case(world):
    root = world[0]
    # Match the actual parent declaration's opaque remap namespace, rather than
    # the older store-only fixture's deliberately unconstrained display token.
    prefix_id = "remap:" + hashlib.sha256(b"approved test prefix").hexdigest()
    world[3]["prefixes"][0]["id"] = prefix_id
    world[3]["mappings"][0]["id"] = prefix_id
    (root / SCRIPT).write_bytes(CURRENT_SOURCE)
    (root / "current.csv").write_bytes(b"current input\n")
    registered = registry.Registry()
    registered.load_data({"scripts": {SCRIPT: ENTRY}})
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    calls = []

    def resource(kind, resource_id):
        calls.append((kind, resource_id))
        path = root / resource_id
        if not path.is_file():
            return None
        digest, size = figcapture.hash_file(path)
        return chartpackage.ResourceFile(
            {
                "kind": kind,
                "id": resource_id,
                "path": ("scripts/" if kind == "script" else "assets/") + resource_id,
                "sha256": digest,
                "bytes": size,
            },
            path.resolve(),
        )

    return world, registered, resource, calls


def _use(reference=None):
    return {"script": SCRIPT, "run_config": reference, "run_config_known": True}


def _freeze(case, *, reference=None, owner=True, patch=PATCH, source=SOURCE):
    world, *_ = case
    world[3]["scope"]["run_config"] = reference
    fact, archive = _capture(world)
    args = _args(source=source, patch=patch)
    args["receipt"]["captured_inputs"] = inputdeclaration.public_fact(fact)
    if owner:
        args["execution_owner"] = {"script": SCRIPT, "run_config": reference}
    saved = svgstore.save(
        world[0], source, patch, **args, captured_input_archive=archive, storage_root=world[1]
    )
    return saved, svgstore.inspect_portable_store(storage_root=world[1]), archive


def _collect(case, uses=(), *, facts=None, registered=None, resource=None):
    world, current_registry, callback, _calls = case
    return collector.collect_execution(
        world[0],
        current_registry if registered is None else registered,
        uses,
        resource=callback if resource is None else resource,
        capture_facts=facts,
    )


def _forbid_current(monkeypatch, *, config=True):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("A historical capture was replaced with current source/configuration")

    monkeypatch.setattr(inputremap, "rules_for", forbidden)
    monkeypatch.setattr(workdir, "resolve_mode", forbidden)
    monkeypatch.setattr(databinding, "binding_for", forbidden)
    monkeypatch.setattr(runconfig, "launch_record", forbidden)
    if config:
        monkeypatch.setattr(runconfig, "get", forbidden)
    return forbidden


def _reasons(result):
    return {row["reason"] for row in result.execution["coverage"]["issues"]}


def test_default_none_uses_exact_v1_oracle_and_never_consults_capture_helpers(case, monkeypatch):
    monkeypatch.setattr(
        packageexecution,
        "captures_from_facts",
        lambda *_a, **_k: pytest.fail("default v1 consulted captures"),
    )
    result = _collect(case, [_use()])
    assert result.execution == {
        "version": 1,
        "registry": {"scripts": {SCRIPT: ENTRY}},
        "runs": [],
        "contexts": [
            {
                "script": SCRIPT,
                "run_config": None,
                "run_config_known": True,
                "cwd_mode": "project_root",
                "profile": None,
                "basis": [],
            }
        ],
        "remaps": [],
        "coverage": {
            "state": "incomplete",
            "issues": [
                {"script": SCRIPT, "reason": "dependency_set_undeclared"},
                {"script": SCRIPT, "reason": "source_basis_missing"},
            ],
        },
    }
    assert case[3] == [("script", SCRIPT), ("file", "current.csv")]
    assert result.missing == ()
    assert [item.source.read_bytes() for item in result.resources] == [
        CURRENT_SOURCE,
        b"current input\n",
    ]


def test_actual_facts_without_input_archives_keep_v1_output_and_callback_order(case):
    world = case[0]
    svgstore.save(world[0], SOURCE, PATCH, **_args(), storage_root=world[1])
    facts = svgstore.inspect_portable_store(storage_root=world[1])
    first = _collect(case, [_use()])
    calls = list(case[3])
    case[3].clear()
    second = _collect(case, [_use()], facts=facts)
    assert first == second and second.execution["version"] == 1
    assert case[3] == calls
    assert "captures" not in second.execution


@pytest.mark.parametrize("current", ["missing", "changed", "no_registry"])
def test_capture_only_never_reads_current_script_settings_or_original_paths(
    case, monkeypatch, current
):
    cfg = (
        runconfig.put(case[0][0], SCRIPT, ["--known-but-unregistered"])
        if current == "no_registry"
        else None
    )
    saved, facts, archive = _freeze(case, reference=cfg.id if cfg is not None else None)
    root = case[0][0]
    if current == "changed":
        (root / SCRIPT).write_bytes(b"raise AssertionError('different current script')")
    else:
        (root / SCRIPT).unlink()
    shutil.rmtree(case[0][2])
    shutil.rmtree(case[0][4])
    forbidden = _forbid_current(monkeypatch)
    registered = registry.Registry() if current == "no_registry" else case[1]
    result = _collect(case, facts=facts, registered=registered, resource=forbidden)
    assert result.execution["version"] == 2
    assert result.execution["captures"] == [
        {
            "snapshot_ref": saved["snapshot_ref"],
            "revision": "sha256:" + hashlib.sha256(archive.graph_bytes).hexdigest(),
        }
    ]
    assert result.execution["contexts"] == [] and result.execution["runs"] == []
    assert result.execution["coverage"] == {"state": "incomplete", "issues": []}
    assert result.resources == () and result.missing == () and case[3] == []
    packageexecution.validate_capture_facts(result.execution, facts)


def test_ownerless_capture_does_not_infer_registry_or_config_from_opaque_source_id(
    case, monkeypatch
):
    saved, facts, _archive = _freeze(case, owner=False)
    assert "executionOwner" not in saved["snapshot_ref"]
    forbidden = _forbid_current(monkeypatch)
    result = _collect(case, facts=facts, resource=forbidden)
    assert result.execution["captures"][0]["snapshot_ref"] == saved["snapshot_ref"]
    assert result.execution["registry"] == {"scripts": {}}
    assert result.execution["runs"] == result.execution["contexts"] == []
    assert result.resources == () and result.missing == ()


def test_original_nonsecret_argv_is_preserved_as_advice_without_current_context(case, monkeypatch):
    tokens = ["--input", "different.csv", "", "--label", "中文 空格", "--", "-3"]
    cfg = runconfig.put(case[0][0], SCRIPT, tokens)
    saved, facts, _archive = _freeze(case, reference=cfg.id)
    forbidden = _forbid_current(monkeypatch, config=False)
    original_get = runconfig.get
    references = []

    def get(root, reference, **kwargs):
        references.append((reference, kwargs))
        return original_get(root, reference, **kwargs)

    monkeypatch.setattr(runconfig, "get", get)
    (case[0][0] / SCRIPT).unlink()
    result = _collect(case, facts=facts, resource=forbidden)
    assert result.execution["runs"] == [{"id": cfg.id, "script": SCRIPT, "argv": tokens}]
    assert references == [(cfg.id, {"script": SCRIPT})]
    assert result.execution["registry"] == {"scripts": {SCRIPT: ENTRY}}
    assert result.execution["contexts"] == [] and result.resources == ()
    assert result.execution["captures"][0]["snapshot_ref"] == saved["snapshot_ref"]


@pytest.mark.parametrize("forget", [False, True])
def test_sensitive_original_parameters_are_not_exposed_or_required_for_capture(
    case, monkeypatch, forget
):
    secret = "CAPTURE-SECRET-NOT-FOR-THE-PACKAGE"
    cfg = runconfig.put(case[0][0], SCRIPT, ["--secret", secret], sensitive=True)
    saved, facts, _archive = _freeze(case, reference=cfg.id)
    if forget:
        runconfig.forget_secrets()
    forbidden = _forbid_current(monkeypatch, config=False)
    result = _collect(case, facts=facts, resource=forbidden)
    assert result.execution["runs"] == []
    assert _reasons(result) == {"sensitive_run_config"}
    assert secret not in json.dumps(result.execution)
    assert result.execution["captures"][0]["snapshot_ref"] == saved["snapshot_ref"]


def test_original_missing_config_keeps_capture_without_using_current_default(case, monkeypatch):
    default = runconfig.put(case[0][0], SCRIPT, ["--current-default"])
    runconfig.set_default(case[0][0], SCRIPT, default.id)
    _saved, facts, _archive = _freeze(case, reference="rc_ffffffffffff")
    forbidden = _forbid_current(monkeypatch, config=False)
    result = _collect(case, facts=facts, resource=forbidden)
    assert result.execution["runs"] == [] and _reasons(result) == {"run_config_missing"}
    assert "--current-default" not in json.dumps(result.execution)


@pytest.mark.parametrize(
    "error",
    [
        runconfig.RunConfigUnreadable("bad store"),
        runconfig.RunConfigUnsupported("future store"),
        runconfig.RunConfigError("unavailable store"),
        OSError("unavailable filesystem"),
    ],
)
def test_unavailable_current_config_is_an_accurate_v2_gap_not_a_failed_capture(
    case, monkeypatch, error
):
    cfg = runconfig.put(case[0][0], SCRIPT, ["--original"])
    saved, facts, _archive = _freeze(case, reference=cfg.id)
    forbidden = _forbid_current(monkeypatch, config=False)

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(runconfig, "get", fail)
    result = _collect(case, facts=facts, resource=forbidden)
    assert _reasons(result) == {"run_config_unavailable"} and result.execution["runs"] == []
    assert result.execution["captures"][0]["snapshot_ref"] == saved["snapshot_ref"]
    assert result.resources == () and result.missing == ()


def test_two_historical_revisions_for_same_script_and_config_are_not_collapsed(case):
    cfg = runconfig.put(case[0][0], SCRIPT, ["--original"])
    first, _facts, _archive = _freeze(case, reference=cfg.id)
    (case[0][2] / "data.csv").write_bytes(b"second historical data")
    second, facts, _archive = _freeze(case, reference=cfg.id)
    assert first["snapshot_ref"]["snapshotId"] != second["snapshot_ref"]["snapshotId"]
    result = _collect(case, facts=facts)
    assert [row["snapshot_ref"] for row in result.execution["captures"]] == sorted(
        [first["snapshot_ref"], second["snapshot_ref"]], key=lambda row: row["snapshotId"]
    )
    assert result.execution["runs"] == [{"id": cfg.id, "script": SCRIPT, "argv": ["--original"]}]
    assert result.execution["contexts"] == [] and result.resources == ()


def test_same_graph_multiple_snapshot_refs_remains_distinct_without_duplicate_resource_copy(case):
    first, _facts, archive = _freeze(case)
    second, facts, _archive = _freeze(case, patch="sha256:" + "c" * 64)
    result = _collect(case, facts=facts)
    assert len(result.execution["captures"]) == 2
    assert {row["revision"] for row in result.execution["captures"]} == {
        "sha256:" + hashlib.sha256(archive.graph_bytes).hexdigest()
    }
    assert {row["snapshot_ref"]["snapshotId"] for row in result.execution["captures"]} == {
        first["snapshot_ref"]["snapshotId"],
        second["snapshot_ref"]["snapshotId"],
    }
    assert result.resources == ()


def test_mixed_current_and_historical_same_script_keep_current_resources_and_old_capture(case):
    saved, facts, archive = _freeze(case)
    before = copy.deepcopy(facts)
    result = _collect(case, [_use()], facts=facts)
    assert result.execution["captures"][0]["snapshot_ref"] == saved["snapshot_ref"]
    assert [row.source.read_bytes() for row in result.resources] == [
        CURRENT_SOURCE,
        b"current input\n",
    ]
    assert result.execution["contexts"][0]["script"] == SCRIPT
    assert result.execution["contexts"][0]["basis"] == []
    assert archive.graph_bytes != CURRENT_SOURCE
    assert facts == before
    assert case[3] == [("script", SCRIPT), ("file", "current.csv")]
    packageexecution.validate_capture_facts(result.execution, facts)


@pytest.mark.parametrize(
    "damage", ["revision", "owner", "graph_scope", "missing_blob", "missing_snapshot"]
)
def test_actual_capture_fact_corruption_is_rejected_without_current_resource_substitution(
    case, damage
):
    saved, facts, _archive = _freeze(case)
    snapshot = facts["snapshots"][saved["snapshot_ref"]["snapshotId"]]
    if damage == "revision":
        snapshot["payload"]["input_archive"]["revision"] = "sha256:" + "e" * 64
    elif damage == "owner":
        snapshot["reference"]["executionOwner"]["script"] = "other.py"
    elif damage == "graph_scope":
        next(iter(facts["inputs"]["graphs"].values()))["graph"]["scope"]["run_config"] = (
            "rc_ffffffffffff"
        )
    elif damage == "missing_blob":
        facts["inputs"]["blobs"].pop(next(iter(facts["inputs"]["blobs"])))
    else:
        facts["snapshots"].clear()
    if damage == "missing_snapshot":
        # No archive means there is no capture to advertise; the empty v1 path remains.
        result = _collect(case, facts=facts)
        assert result.execution["version"] == 1 and "captures" not in result.execution
    else:
        with pytest.raises(chartpackage.PackageError) as caught:
            _collect(case, facts=facts)
        assert caught.value.reason == "execution_resource_mismatch"
    assert case[3] == []
