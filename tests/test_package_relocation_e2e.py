"""Real safe/native Figures survive archive relocation in a cold parent.

This validates the archive consumer, not an installation endpoint or UI. Only
self-owned test projects are copied/deleted; no source script is rerun by a
package read, relocation, input-view comparison or cold archive load.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from support import desktopkit, nativekit
from tavotto import app as m
from tavotto.engine import (
    chartpackage,
    config,
    declaredinputs,
    desktopproc,
    execspec,
    figcapture,
    inputdeclaration,
    inputremap,
    nativesession,
    packagebudget,
    packagerelocation,
    runconfig,
    svgstore,
)
from test_svg_input_package_e2e import (
    ARGV,
    _archive,
    _cold_package,
    _prepare,
    _remove_source_and_cache,
    _run_safe,
    _save,
    project as project,
)
from test_svg_input_save_e2e import _contents

ORDERED = [
    {"gid": "axes_0.lines_0", "prop": "color", "value": "blue"},
    {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
    {"gid": "axes_0.lines_0", "prop": "color", "value": "green"},
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _selected_prefix(project, run, tmp_path):
    client, root, _counter = project
    prefix = tmp_path / "明确批准的外部目录"
    (prefix / "nested" / "empty").mkdir(parents=True)
    (prefix / "nested" / "values.csv").write_bytes(b"1,2\r\n")
    (prefix / "zero.dat").write_bytes(b"")
    inputremap.add_rule(root, {"kind": "prefix", "from": "series", "to": str(prefix)})
    response = client.post(
        "/api/engine/input-declarations",
        json={
            "script": "plot.py",
            "cwd_mode": "project_root",
            "run_config": run.id,
            "files": ["data.csv", "raw.bin", "empty.dat"],
            "remaps": ["series"],
            "expected_revision": declaredinputs.state(root)["revision"],
        },
    )
    assert response.status_code == 200, response.get_json()


def _run_native(project, run):
    client, root, _counter = project
    assert nativekit.USER_PYTHON is not None, "Existing user Python is required"
    selection = runconfig.selection(root, run.id, script="plot.py")
    declared = declaredinputs.scope_selection(
        root, script="plot.py", cwd_mode="project_root", run=selection, execution="native"
    )
    spec = execspec.desktop_native_spec(
        str(root / "plot.py"),
        interpreter=nativekit.USER_PYTHON,
        cwd=str(root),
        project_root=str(root),
        **execspec.run_kwargs(selection),
    )
    nativesession.REGISTRY.reopen()
    session = desktopproc.start(
        desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST, input_declaration_state=declared)
    )
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        built = client.post(f"/api/native/sessions/{session.session_id}/build")
        assert built.status_code == 200, built.get_json()
        assert m._execution_receipt(session, "plot.py").captured_input_archive is not None
        source = figcapture.runtime_asset_id("plot.py", "F", run.id)
        document, reference = _save(client, root, source, name="RelocateNative")
        rendered = client.post("/api/engine/render", json={"id": source, "patches": ORDERED})
        assert rendered.status_code == 200, rendered.get_json()
        edited, edited_ref = _save(client, root, source, name="RelocateNativeEdited", edits=ORDERED)
        edited["canvases"][0]["id"] = "nonactive"
        edited["canvases"][0]["objects"][0]["id"] = "edited-native"
        document["canvases"].append(edited["canvases"][0])
        return document, [reference, edited_ref], session.out_dir
    finally:
        if not session.owned.finished:
            session.cancel_owned()


def _expected(root, references):
    records = {}
    for reference in references:
        record = svgstore.inspect_portable_snapshot(
            reference, storage_root=m._svg_storage_root(root)
        )
        archive = record["captured_input_archive"]
        records[reference["snapshotId"]] = {
            "receipt": record["receipt"],
            "pdf_sha256": _sha(record["pdf"]),
            "svg_sha256": _sha(record["svg"]),
            "graph": base64.b64encode(archive.graph_bytes).decode("ascii"),
            "inputs": {
                digest: base64.b64encode(data).decode("ascii")
                for digest, data in _contents(archive).items()
            },
        }
    return records


_COLD = r"""
import base64, hashlib, json, sys
from pathlib import Path
sys.path[:0] = [str(Path(sys.argv[1]) / 'src')]
from tavotto.engine import config, inputremap, runconfig, svgstore
request = json.loads(sys.stdin.read())
root = Path(request['root'])
for rule in request['rules']:
    inputremap.add_rule(root, rule)
actual = inputremap.fingerprint(root)
assert inputremap.rules_for(root) == request['rules']
runs = runconfig.import_portable(root, request['execution']['runs'])
assert [{'id':run.id,'script':run.script,'argv':list(run.argv)} for run in runs] == request['execution']['runs']
results = []
for reference in request['refs']:
    record = svgstore.load_document_snapshot(
        root, reference, source_id=reference['sourceId'], patch_hash=reference['patchHash'],
        remap_fingerprint=actual,
        storage_root=root / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME)
    if request.get('reject'):
        assert record is None, reference
        continue
    assert actual == request['fingerprint']
    assert record is not None, reference
    archive = record['captured_input_archive']
    inputs = {}
    for resource in archive.resources:
        data = resource.path.read_bytes()
        assert len(data) == resource.size_bytes
        assert hashlib.sha256(data).hexdigest() == resource.sha256
        inputs[resource.sha256] = base64.b64encode(data).decode('ascii')
    results.append({
        'reference':record['snapshot_ref'],
        'receipt':record['receipt'],
        'pdf_sha256':hashlib.sha256(record['pdf']).hexdigest(),
        'svg_sha256':hashlib.sha256(record['svg']).hexdigest(),
        'graph':base64.b64encode(archive.graph_bytes).decode('ascii'),
        'inputs':inputs})
print(json.dumps({'svgstore':svgstore.__file__,'fingerprint':actual,'results':results}))
"""


def _cold_read(tmp_path, root, references, execution, rules, fingerprint, *, label, reject=False):
    result = subprocess.run(
        [sys.executable, "-B", "-c", _COLD, str(Path(__file__).resolve().parents[1])],
        input=json.dumps(
            {
                "root": str(root),
                "refs": references,
                "execution": execution,
                "rules": rules,
                "fingerprint": fingerprint,
                "reject": reject,
            }
        ),
        env={
            **os.environ,
            "TAVOTTO_DATA_DIR": str(tmp_path / ("cold-data-" + label)),
            "TAVOTTO_CONFIG_DIR": str(tmp_path / ("cold-config-" + label)),
            "TAVOTTO_NO_TELEMETRY": "1",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert Path(body["svgstore"]).resolve() == Path(svgstore.__file__).resolve()
    return body


@pytest.mark.parametrize("profile", ["safe", "native"])
def test_actual_figure_relocation_keeps_refs_inputs_and_one_real_remap_identity(
    project, tmp_path, profile
):
    client, source_root, counter = project
    run, argv_log = _prepare(project, native=profile == "native", argv=ARGV)
    assert run is not None
    _selected_prefix(project, run, tmp_path)
    if profile == "safe":
        document, references, worker = _run_safe(project, run, name="RelocateSafe", edits=True)
        worker_base = worker.base
        # The second exact edit variant is also present on a non-active canvas.
        second = copy.deepcopy(document["canvases"][0])
        second["id"] = "nonactive"
        second["objects"] = [second["objects"].pop()]
        second["objects"][0]["id"] = "edited-safe-nonactive"
        document["canvases"].append(second)
    else:
        document, references, worker_base = _run_native(project, run)
    assert json.loads(argv_log.read_text(encoding="utf-8")) == ARGV
    assert counter.read_bytes() == b"run\n"
    assert any(
        panel["overrides"] == ORDERED
        for canvas in document["canvases"]
        for panel in canvas["objects"]
    )
    expected = _expected(source_root, references)
    prefix_graph = json.loads(_archive(source_root, references[0]).graph_bytes)["prefixes"]
    assert len(prefix_graph) == 1
    assert "nested/empty" in prefix_graph[0]["directories"]

    packed, manifest, _members = _cold_package(source_root, document, tmp_path)
    blob = Path(packed["path"]).read_bytes()
    assert manifest["execution"]["runs"] == [{"id": run.id, "script": "plot.py", "argv": ARGV}]
    assert counter.read_bytes() == b"run\n"

    target = tmp_path / "中文迁移新项目"
    occupied = tmp_path / "不可覆盖的既有项目"
    occupied.mkdir()
    sentinel = occupied / "original.txt"
    sentinel.write_bytes(b"existing user content")
    first_archive = _archive(source_root, references[0])
    graph = json.loads(first_archive.graph_bytes)
    data_digest = next(row["sha256"] for row in graph["files"] if row["id"] == "data.csv")
    data_resource = next(row for row in first_archive.resources if row.sha256 == data_digest)
    relative_blob = data_resource.path.relative_to(m._svg_storage_root(source_root))
    rules = [
        {
            "kind": "file",
            "from": "data.csv",
            "to": str(
                target / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME / relative_blob
            ),
        }
    ]
    with chartpackage.inspect_chart_package(
        io.BytesIO(blob), budget=packagebudget.input_budget()
    ) as prepared:
        with packagerelocation.prepare_relocation(
            prepared, project_root=target, remap_rules=rules
        ) as moved:
            moved.verify()
            assert not target.exists()
            assert sentinel.read_bytes() == b"existing user content"
            with pytest.raises(FileExistsError):
                shutil.copytree(moved.stage_root, occupied)
            assert sentinel.read_bytes() == b"existing user content"
            assert moved.remap_fingerprint == inputremap.fingerprint_rules(rules)
            rows = moved.references
            assert {row["old_ref"]["snapshotId"] for row in rows} == set(expected)
            changed = {row["old_ref"]["snapshotId"]: row["new_ref"] for row in rows}
            for old in references:
                new = changed[old["snapshotId"]]
                assert {key: value for key, value in new.items() if key != "snapshotId"} == {
                    key: value for key, value in old.items() if key != "snapshotId"
                }
                assert new["snapshotId"] != old["snapshotId"]
                with moved.input_view(new) as candidate:
                    assert (
                        inputdeclaration.current_public_fact(candidate)
                        == expected[old["snapshotId"]]["receipt"]["captured_inputs"]
                    )
                    assert candidate["scope"] == graph["scope"]
                    assert len(candidate["prefixes"]) == 1
                    assert (Path(candidate["prefixes"][0]["path"]) / "nested" / "empty").is_dir()
            modified = moved.document
            for before, after in zip(document["canvases"], modified["canvases"], strict=True):
                for old, new in zip(before["objects"], after["objects"], strict=True):
                    old_copy = copy.deepcopy(old)
                    old_copy["figureSnapshot"] = changed[old["figureSnapshot"]["snapshotId"]]
                    assert new == old_copy
            relocated_execution = moved.execution
            assert relocated_execution["runs"] == manifest["execution"]["runs"]
            assert {
                row["snapshot_ref"]["snapshotId"] for row in relocated_execution["captures"]
            } == {reference["snapshotId"] for reference in changed.values()}
            facts = svgstore.inspect_portable_store(storage_root=moved.storage_root)
            assert facts["entries"] == {} and facts["sources"] == {}
            shutil.copytree(moved.stage_root, target)
            new_refs = [changed[ref["snapshotId"]] for ref in references]
            fingerprint = moved.remap_fingerprint

    for label in ("before-delete", "after-delete"):
        if label == "after-delete":
            _remove_source_and_cache(project, worker_base)
            resolved = source_root.resolve()
            assert resolved.is_relative_to(tmp_path.resolve())
            assert resolved != tmp_path.resolve() and resolved != target.resolve()
            shutil.rmtree(resolved)
            assert not source_root.exists()
        read = _cold_read(
            tmp_path, target, new_refs, relocated_execution, rules, fingerprint, label=label
        )
        assert read["fingerprint"] == fingerprint
        assert len(read["results"]) == len(references)
        for old, actual in zip(references, read["results"], strict=True):
            assert actual.pop("reference") == changed[old["snapshotId"]]
            assert actual == expected[old["snapshotId"]]
        assert counter.read_bytes() == b"run\n"
        assert sentinel.read_bytes() == b"existing user content"

    restored_data = target / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME / relative_blob
    original = restored_data.read_bytes()
    restored_data.write_bytes(b"x" * len(original))
    rejected = _cold_read(
        tmp_path,
        target,
        new_refs,
        relocated_execution,
        rules,
        fingerprint,
        label="tampered-input",
        reject=True,
    )
    assert rejected["results"] == []
    restored_data.write_bytes(original)
    assert counter.read_bytes() == b"run\n"
