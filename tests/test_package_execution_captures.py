"""Versioned package capture metadata consumes actual, checked portable stores.

Fixtures use real Capture/cache/store IO and opaque admitted render bytes, never
execute a script. Negative graph cases explicitly re-sign test-owned metadata;
they do not claim to be observations of another scientific execution.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import (
    config,
    executionfact,
    figcapture,
    inputdeclaration,
    packageexecution as execution,
    packagemembers,
    svgstore,
    svgstore_inputs,
)
from test_package_execution import block as legacy_block, records as legacy_records
from test_svgstore import _args

SCRIPT = "plot.py"
RUN = "rc_0123456789ab"
OTHER_RUN = "rc_abcdef012345"
LIMITS = {"max_files": 100, "max_bytes": 1_000_000}


def _capture(
    tmp_path,
    label="capture",
    *,
    mode="project_root",
    run=None,
    ids=("data.csv", "empty.csv"),
    prefix=True,
    graph_edit=None,
    data=b'1,"quoted"\r\n',
):
    root = tmp_path / label
    root.mkdir()
    source = root / "actual-source.py"
    source.write_bytes(b"# test-owned source; never executed\n")
    files = []
    for index, identifier in enumerate(ids):
        path = root / f"input-{index}.bin"
        path.write_bytes(data if index == 0 else b"")
        files.append({"id": identifier, "path": str(path)})
    prefixes = []
    if prefix:
        directory = root / "selected"
        (directory / "empty dir").mkdir(parents=True)
        (directory / "nested").mkdir()
        (directory / "nested" / "value.csv").write_bytes(b"2,3\n")
        prefixes.append({"id": "selected", "path": str(directory)})
    candidate = {
        "version": 1,
        "scope": {"script": SCRIPT, "cwd_mode": mode, "run_config": run},
        "script_path": str(source),
        "files": files,
        "prefixes": prefixes,
        "mappings": [],
        "rules": [],
        "limits": LIMITS,
    }
    capture = inputdeclaration.begin(
        candidate,
        script=SCRIPT,
        script_path=str(source),
        cwd_mode=mode,
        run_config=run,
        actual_cwd=str(root),
        project_root=str(root),
    )
    assert capture is not None
    capture.entered(executionfact.new_id())
    cache = root / inputdeclaration.ARCHIVE_DIRNAME
    fact = capture.finish(hashlib.sha256(source.read_bytes()).hexdigest(), archive_root=cache)
    assert fact is not None
    archive = inputdeclaration.verify_archive(
        capture.archive_descriptor(), fact, archive_root=cache, limits=LIMITS
    )
    assert archive is not None
    if graph_edit is not None:
        graph = json.loads(archive.graph_bytes)
        graph_edit(graph)
        archive = dataclasses.replace(
            archive, graph_bytes=inputdeclaration.canonical_graph_bytes(graph)
        )
    public = {"version": 1, "revision": "sha256:" + hashlib.sha256(archive.graph_bytes).hexdigest()}
    return archive, public, {"script": SCRIPT, "run_config": run}


def _store(tmp_path, *, captures=None, **capture_options):
    project = tmp_path / "project"
    project.mkdir()
    root = project / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    captures = captures or [_capture(tmp_path, **capture_options)]
    refs = []
    for index, (archive, public, owner) in enumerate(captures):
        source = figcapture.runtime_asset_id(owner["script"], "F", owner["run_config"] or "")
        patch = "sha256:" + hashlib.sha256(str(index).encode()).hexdigest()
        args = _args(source=source, patch=patch, generation=index + 1)
        args["receipt"]["captured_inputs"] = public
        saved = svgstore.save(
            project,
            source,
            patch,
            storage_root=root,
            activate=True,
            execution_owner=owner,
            captured_input_archive=archive,
            **args,
        )
        refs.append(saved["snapshot_ref"])
    facts = svgstore.inspect_portable_store(storage_root=root)
    prefix = f"assets/{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
    records = []
    for name in facts["members"]:
        if name == ".lock":
            continue
        data = (root / name).read_bytes()
        records.append(
            {
                "kind": "figure_snapshot",
                "path": prefix + name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return SimpleNamespace(root=root, facts=facts, records=records, refs=refs)


def _execution(facts, *, profile="safe", current_contexts=False):
    captures = execution.captures_from_facts(facts)
    owners = {
        (
            row["snapshot_ref"]["executionOwner"]["script"],
            row["snapshot_ref"]["executionOwner"]["run_config"],
        )
        for row in captures
    }
    scripts = sorted({script for script, _ in owners})
    return {
        "version": 2,
        "registry": {
            "scripts": {
                script: {"entry": "main", "cost": "light", "notes": "", "stems": [f"F{index}"]}
                for index, script in enumerate(scripts)
            }
        },
        "runs": [
            {"id": run, "script": script, "argv": ["--value", "1"]}
            for script, run in sorted(owners, key=str)
            if run is not None
        ],
        "contexts": [
            {
                "script": script,
                "run_config": run,
                "run_config_known": True,
                "cwd_mode": "sandbox" if profile == "native" else "project_root",
                "profile": profile,
                "basis": [],
            }
            for script, run in sorted(owners, key=str)
            if current_contexts
        ],
        "remaps": [],
        "coverage": {
            "state": "incomplete",
            "issues": [
                {"script": script, "reason": "dependency_set_undeclared"} for script in scripts
            ],
        },
        "captures": captures,
    }


def _mismatch(call):
    with pytest.raises(packagemembers.PackageError) as caught:
        call()
    assert caught.value.reason == "execution_resource_mismatch"


@pytest.fixture
def packaged(tmp_path):
    return _store(tmp_path)


def test_v1_default_canonical_json_and_resources_are_unchanged():
    original = legacy_block()
    expected = json.dumps(original, ensure_ascii=False, sort_keys=True)
    actual = execution.validate_execution(original)
    assert execution.VERSION == 1 and execution.CAPTURE_VERSION == 2
    assert json.dumps(actual, ensure_ascii=False, sort_keys=True) == expected
    assert "captures" not in actual
    assert execution.validate_resources(actual, legacy_records()) is None


def test_checked_store_yields_detached_full_capture_refs_and_summaries(packaged):
    facts = packaged.facts
    before = copy.deepcopy(facts)
    rows = execution.captures_from_facts(facts)
    expected = [
        {
            "snapshot_ref": item["reference"],
            "revision": item["payload"]["input_archive"]["revision"],
        }
        for item in facts["snapshots"].values()
    ]
    assert rows == expected
    value = _execution(facts)
    canonical = execution.validate_execution(value)
    assert canonical == value
    assert execution.validate_resources(canonical, packaged.records) is None
    summaries = execution.validate_capture_facts(canonical, facts)
    assert len(summaries) == 1
    graph = next(iter(facts["inputs"]["graphs"].values()))["graph"]
    assert summaries[0] == {
        **expected[0],
        "scope": graph["scope"],
        "files": len(graph["files"]),
        "prefixes": len(graph["prefixes"]),
    }
    rows[0]["snapshot_ref"]["sourceId"] = "changed"
    summaries[0]["scope"]["script"] = "changed"
    assert facts == before


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "empty",
        "duplicate",
        "extra",
        "bad_revision",
        "bad_ref",
        "boolean_version",
        "future",
        "complete",
        "v1",
    ],
)
def test_capture_wire_is_closed_versioned_and_nonempty(packaged, change):
    raw = _execution(packaged.facts)
    if change == "missing":
        raw.pop("captures")
    elif change == "empty":
        raw["captures"] = []
    elif change == "duplicate":
        raw["captures"].append(copy.deepcopy(raw["captures"][0]))
    elif change == "extra":
        raw["captures"][0]["graph"] = {}
    elif change == "bad_revision":
        raw["captures"][0]["revision"] = "sha256:" + "A" * 64
    elif change == "bad_ref":
        raw["captures"][0]["snapshot_ref"]["extra"] = True
    elif change == "boolean_version":
        raw["version"] = True
    elif change == "future":
        raw["version"] = 3
    elif change == "complete":
        raw["coverage"] = {"state": "complete", "issues": []}
    else:
        raw["version"] = 1
    with pytest.raises(packagemembers.PackageError) as caught:
        execution.validate_execution(raw)
    assert caught.value.reason == "invalid_execution"


@pytest.mark.parametrize(
    "target", ["snapshot_missing", "snapshot_digest", "graph_missing", "graph_digest"]
)
def test_pure_resources_require_the_exact_capture_metadata_and_graph_rows(packaged, target):
    raw = _execution(packaged.facts)
    records = copy.deepcopy(packaged.records)
    suffix = (
        "snapshots/" + packaged.refs[0]["snapshotId"] + ".json"
        if target.startswith("snapshot")
        else "inputs/graphs/" + raw["captures"][0]["revision"][7:] + ".json"
    )
    index = next(i for i, row in enumerate(records) if row["path"].endswith(suffix))
    if target.endswith("missing"):
        records.pop(index)
    else:
        records[index]["sha256"] = "bad-sha" if target == "snapshot_digest" else "0" * 64
    _mismatch(lambda: execution.validate_resources(raw, records))


def test_capture_ownership_alone_exempts_only_its_registered_script(packaged):
    raw = _execution(packaged.facts)
    assert execution.validate_resources(raw, packaged.records) is None
    raw["registry"]["scripts"]["uncaptured.py"] = {
        "entry": "main",
        "cost": "light",
        "notes": "",
        "stems": ["G"],
    }
    _mismatch(lambda: execution.validate_resources(raw, packaged.records))


def test_current_context_cannot_borrow_historical_capture_to_hide_its_missing_script(packaged):
    raw = _execution(packaged.facts, current_contexts=True)
    _mismatch(lambda: execution.validate_resources(raw, packaged.records))


@pytest.mark.parametrize(
    "change",
    [
        "missing_capture",
        "extra_capture",
        "ref_owner",
        "ref_receipt",
        "revision",
        "missing_graph",
        "missing_blob",
        "extra_blob",
    ],
)
def test_capture_facts_must_match_all_historical_archives_and_exact_input_closure(packaged, change):
    raw = _execution(packaged.facts)
    facts = copy.deepcopy(packaged.facts)
    if change == "missing_capture":
        facts["snapshots"] = {}
    elif change == "extra_capture":
        extra = copy.deepcopy(raw["captures"][0])
        extra["snapshot_ref"]["snapshotId"] = "0" * 64
        raw["captures"].append(extra)
    elif change == "ref_owner":
        raw["captures"][0]["snapshot_ref"]["executionOwner"]["run_config"] = RUN
    elif change == "ref_receipt":
        raw["captures"][0]["snapshot_ref"]["receiptId"] = "sha256:" + "0" * 64
    elif change == "revision":
        raw["captures"][0]["revision"] = "sha256:" + "0" * 64
    elif change == "missing_graph":
        facts["inputs"]["graphs"] = {}
    elif change == "missing_blob":
        facts["inputs"]["blobs"].pop(next(iter(facts["inputs"]["blobs"])))
    else:
        facts["inputs"]["blobs"]["0" * 64] = {"size_bytes": 0}
    _mismatch(lambda: execution.validate_capture_facts(raw, facts))


@pytest.mark.parametrize(
    "change", ["ref_owner", "ref_receipt", "ref_source", "row_owner", "row_receipt"]
)
def test_checked_reference_and_mirrors_cannot_self_authorize_poisoned_capture(packaged, change):
    raw = _execution(packaged.facts)
    facts = copy.deepcopy(packaged.facts)
    row = next(iter(facts["snapshots"].values()))
    if change == "ref_owner":
        row["reference"]["executionOwner"]["script"] = "other.py"
    elif change == "ref_receipt":
        row["reference"]["receiptId"] = "sha256:" + "0" * 64
    elif change == "ref_source":
        row["reference"]["sourceId"] = figcapture.runtime_asset_id("other.py", "F")
    elif change == "row_owner":
        row["execution_owner"] = {"script": "other.py", "run_config": None}
    else:
        row["receipt"] = {**row["receipt"], "source_revision": "unrelated-source"}
    # An attacker copies the poisoned summary into execution too. Comparing
    # these two projections to one another would accept the original defect.
    raw["captures"][0]["snapshot_ref"] = copy.deepcopy(row["reference"])
    _mismatch(lambda: execution.captures_from_facts(facts))
    _mismatch(lambda: execution.validate_capture_facts(raw, facts))


@pytest.mark.parametrize("change", ["owner", "receipt_summary", "script", "run", "safe_cwd"])
def test_actual_graph_scope_is_checked_against_its_execution_metadata(packaged, change):
    raw = _execution(packaged.facts)
    facts = copy.deepcopy(packaged.facts)
    row = next(iter(facts["snapshots"].values()))
    if change == "owner":
        row["execution_owner"] = {"script": SCRIPT, "run_config": OTHER_RUN}
    elif change == "receipt_summary":
        row["receipt"]["captured_inputs"]["revision"] = "sha256:" + "0" * 64
    else:
        row["receipt"]["profile"] = "safe"
        row["receipt"]["spec"] = {"target": SCRIPT, "run_config": None, "cwd_mode": "project_root"}
        field, value = {
            "script": ("target", "other.py"),
            "run": ("run_config", OTHER_RUN),
            "safe_cwd": ("cwd_mode", "sandbox"),
        }[change]
        row["receipt"]["spec"][field] = value
    _mismatch(lambda: execution.validate_capture_facts(raw, facts))


@pytest.mark.parametrize("kind", ["render", "input"])
def test_checked_render_orphans_remain_legal_but_unreferenced_inputs_are_not_complete_captures(
    packaged, kind
):
    data = b"self-owned unreferenced bytes"
    digest = hashlib.sha256(data).hexdigest()
    path = (
        packaged.root / "blobs" / (digest + ".pdf")
        if kind == "render"
        else packaged.root / svgstore_inputs._blob_name(digest)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    facts = svgstore.inspect_portable_store(storage_root=packaged.root)
    raw = _execution(facts)
    if kind == "render":
        assert execution.validate_capture_facts(raw, facts)
    else:
        _mismatch(lambda: execution.validate_capture_facts(raw, facts))


def test_same_graph_different_patch_and_owner_run_histories_are_not_deduplicated(tmp_path):
    first = _capture(tmp_path)
    second = _capture(tmp_path, "second", run=RUN)
    packaged = _store(tmp_path, captures=[first, first, second])
    raw = _execution(packaged.facts)
    assert len(raw["captures"]) == 3
    assert len({row["revision"] for row in raw["captures"]}) == 2
    assert len({row["snapshot_ref"]["snapshotId"] for row in raw["captures"]}) == 3
    assert execution.validate_resources(raw, packaged.records) is None
    assert len(execution.validate_capture_facts(raw, packaged.facts)) == 3


def test_historical_captures_keep_distinct_bytes_for_the_same_logical_input(tmp_path):
    first = _capture(tmp_path)
    second = _capture(tmp_path, "second", data=b"new historical data\n")
    packaged = _store(tmp_path, captures=[first, second])
    raw = _execution(packaged.facts)
    graphs = [row["graph"] for row in packaged.facts["inputs"]["graphs"].values()]
    assert {graph["files"][0]["id"] for graph in graphs} == {"data.csv"}
    assert len({graph["files"][0]["sha256"] for graph in graphs}) == 2
    assert len(execution.validate_capture_facts(raw, packaged.facts)) == 2


@pytest.mark.parametrize("mode", ["project", "project_root"])
def test_native_real_graph_cwd_does_not_need_to_equal_advisory_spec_sandbox(tmp_path, mode):
    packaged = _store(tmp_path, mode=mode)
    raw = _execution(packaged.facts, profile="native", current_contexts=True)
    assert raw["contexts"][0]["cwd_mode"] == "sandbox"
    facts = copy.deepcopy(packaged.facts)
    row = next(iter(facts["snapshots"].values()))
    row["receipt"]["profile"] = "native"
    row["receipt"]["spec"] = {"target": SCRIPT, "run_config": None, "cwd_mode": "sandbox"}
    summaries = execution.validate_capture_facts(raw, facts)
    assert summaries[0]["scope"]["cwd_mode"] == mode


@pytest.mark.parametrize("ids", [("remap:" + "a" * 64,), (SCRIPT,)])
def test_opaque_remap_and_shadow_script_source_are_distinct_legal_roles(tmp_path, ids):
    packaged = _store(tmp_path, ids=ids)
    graph = next(iter(packaged.facts["inputs"]["graphs"].values()))["graph"]
    if ids == (SCRIPT,):
        assert graph["script"]["sha256"] != graph["files"][0]["sha256"]
    assert "" in graph["prefixes"][0]["directories"]
    assert execution.validate_capture_facts(_execution(packaged.facts), packaged.facts)


@pytest.mark.parametrize(
    "ids", [("../escape.csv",), ("A.csv", "a.csv"), ("é.csv", "e\u0301.csv"), ("selected/nested",)]
)
def test_actual_opaque_graph_ids_cannot_hide_nonportable_or_aliased_input_targets(tmp_path, ids):
    packaged = _store(tmp_path, ids=ids)
    _mismatch(lambda: execution.validate_capture_facts(_execution(packaged.facts), packaged.facts))


@pytest.mark.parametrize(
    "change", ["suffix_escape", "suffix_backslash", "missing_parent", "file_dir"]
)
def test_resigned_prefix_graph_must_still_have_a_safe_closed_logical_tree(tmp_path, change):
    def edit(graph):
        prefix = graph["prefixes"][0]
        if change == "suffix_escape":
            prefix["members"][0]["suffix"] = "../escape.csv"
        elif change == "suffix_backslash":
            prefix["members"][0]["suffix"] = "nested\\value.csv"
        elif change == "missing_parent":
            prefix["directories"].remove("nested")
        else:
            prefix["directories"].append(prefix["members"][0]["suffix"])

    packaged = _store(tmp_path, graph_edit=edit)
    _mismatch(lambda: execution.validate_capture_facts(_execution(packaged.facts), packaged.facts))


def test_capture_fact_validation_performs_no_resource_io_or_source_execution(packaged, monkeypatch):
    raw = _execution(packaged.facts)
    before = copy.deepcopy(packaged.facts)

    def forbidden(*args, **kwargs):
        pytest.fail("pure package capture validation attempted filesystem IO")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(inputdeclaration, "_OPEN", forbidden)
    assert execution.validate_resources(raw, packaged.records) is None
    assert execution.validate_capture_facts(raw, packaged.facts)
    assert packaged.facts == before
