"""Formal v4 packages carry exact real captured inputs across a cold parent.

The legacy /api/package staging test is deliberately separate. These tests use
v4 named saves, the formal v4 producer and its independent upload inspector.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from support import desktopkit, nativekit
from tavotto import app as m
from tavotto.engine import (
    brand,
    config,
    declaredinputs,
    desktopproc,
    documentpackage,
    execspec,
    figcapture,
    inputremap,
    nativesession,
    pool,
    runconfig,
    svgstore,
)
from test_chart_package_http import document, panel
from test_svg_input_save_e2e import BINARY, CSV, _contents, project as project

ENDPOINT = "/api/documents/v4/package"
INSPECT = ENDPOINT + "/inspect"
ARGV = ["--label", "中文 空格", "", "--", "-3"]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _script(counter, argv_log, *, native=False, suffix=""):
    return (
        "import csv, json, sys\nimport matplotlib.pyplot as plt\n"
        "def main():\n"
        f"    with open({str(counter)!r}, 'ab') as count: count.write(b'run\\n')\n"
        f"    with open({str(argv_log)!r}, 'w', encoding='utf-8') as log: json.dump(sys.argv[1:], log)\n"
        "    with open('data.csv', encoding='utf-8', newline='') as data: rows = list(csv.reader(data))\n"
        "    value = float(rows[1][1])\n"
        "    fig, ax = plt.subplots(figsize=(2, 1))\n"
        "    ax.plot([0, 1], [0, value], color='red')\n"
        "    ax.set_title('Captured ' + str(int(value)))\n"
        "    fig.savefig('F.pdf')\n" + ("main()\nplt.show()\n" if native else "") + suffix
    )


def _prepare(project, *, native=False, declared=True, suffix="", argv=ARGV):
    client, root, counter = project
    argv_log = root.parent / "actual-argv.json"
    (root / "plot.py").write_text(
        _script(counter, argv_log, native=native, suffix=suffix), encoding="utf-8"
    )
    run = runconfig.put(root, "plot.py", argv)
    assert (list(run.argv) if run is not None else []) == list(argv)
    if declared:
        response = client.post(
            "/api/engine/input-declarations",
            json={
                "script": "plot.py",
                "cwd_mode": "project_root",
                "run_config": run.id if run is not None else None,
                "files": ["data.csv", "raw.bin", "empty.dat"],
                "expected_revision": declaredinputs.state(root)["revision"],
            },
        )
        assert response.status_code == 200, response.get_json()
    return run, argv_log


def _save(client, root, source, *, name, edits=()):
    doc = document()
    item = panel(file=source, edits=edits)
    item.update(nativeW=50.8, nativeH=25.4, w=50.8, h=25.4)
    doc["canvases"][0]["objects"] = [item]
    pid = m._project_id(root)
    endpoint = f"/api/documents/v4/layouts/{name}"
    response = client.post(endpoint + "?target=project&base_revision=absent&pj=" + pid, json=doc)
    assert response.status_code == 200, response.get_json()
    assert response.get_json().get("svg_snapshot_warnings", []) == [], response.get_json()
    response = client.get(endpoint + "?pj=" + pid)
    assert response.status_code == 200, response.get_json()
    saved = response.get_json()
    assert saved["schema"] == 4
    reference = saved["canvases"][0]["objects"][0]["figureSnapshot"]
    assert reference["sourceId"] == source
    assert reference["executionOwner"]["script"] == "plot.py"
    return saved, reference


def _run_safe(project, run, *, name, edits=False):
    client, root, _counter = project
    response = client.post("/api/registry/probe", json={"script": "plot.py", "argv": ARGV})
    assert response.status_code == 200 and response.get_json()["registered"], response.get_json()
    source = figcapture.runtime_asset_id("plot.py", "F", run.id)
    worker = pool.peek("plot.py", root, run=runconfig.selection(root, run.id, script="plot.py"))
    assert worker is not None
    saved, reference = _save(client, root, source, name=name)
    references = [reference]
    if edits:
        ordered = [
            {"gid": "axes_0.lines_0", "prop": "color", "value": "blue"},
            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
            {"gid": "axes_0.lines_0", "prop": "color", "value": "green"},
        ]
        rendered = client.post("/api/engine/render", json={"id": source, "patches": ordered})
        assert rendered.status_code == 200, rendered.get_json()
        edited, edited_ref = _save(client, root, source, name=name + "Edited", edits=ordered)
        item = edited["canvases"][0]["objects"][0]
        assert item["overrides"] == ordered
        item["id"] = "edited"
        saved["canvases"][0]["objects"].append(item)
        references.append(edited_ref)
    return saved, references, worker


def _archive(root, reference):
    return svgstore.load_input_archive(reference, storage_root=m._svg_storage_root(root))


def _remove_source_and_cache(project, worker_base):
    _client, root, _counter = project
    pool.shutdown_all(figures_dir=str(root), wait=True)
    resolved = Path(worker_base).resolve()
    assert resolved.is_relative_to(root.parent.resolve())
    if resolved.exists():
        shutil.rmtree(resolved)
    for name in ("plot.py", "data.csv", "raw.bin", "empty.dat"):
        (root / name).write_bytes(b"changed after execution")
        (root / name).unlink()
    m.reset_projects(wait=True)
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()
    cache = config.data_dir() / "cache"
    assert cache.resolve().is_relative_to(root.parent.resolve())
    if cache.exists():
        shutil.rmtree(cache)


_COLD = r"""
import io, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from tavotto import app as m
from tavotto.engine import project_watch
from tavotto.rendercore import renderhost
request = json.loads(sys.stdin.read())
m.app.config['TESTING'] = True
m.open_project(request['root'])
assert m.current_ctx().svg_executions.owners == {}
try:
    with m.app.test_client() as client:
        pid = m._project_id(Path(request['root']))
        response = client.post('/api/documents/v4/package?pj=' + pid,
            json={'stem': 'actual_v4_inputs', 'doc': request['doc'], 'settings': {'dpi': 600}})
        assert response.status_code == 200, response.get_json()
        produced = response.get_json()
        assert produced['document_format'] == 'v4'
        path = m.project_export_dir() / produced['name']
        package_bytes = path.read_bytes()
        inspected = client.post('/api/documents/v4/package/inspect?pj=' + pid,
            data={'package': (io.BytesIO(package_bytes), 'actual.tavotto')},
            content_type='multipart/form-data')
        assert inspected.status_code == 200, inspected.get_json()
        assert m.current_ctx().svg_executions.owners == {}
        print(json.dumps({'path': str(path), 'produced': produced,
                          'inspection': inspected.get_json(), 'app': m.__file__}))
finally:
    m.reset_projects(wait=True)
    project_watch.stop()
    renderhost.shutdown_shared()
"""


def _cold_package(root, doc, tmp_path, *, fresh_settings=False):
    env = dict(
        os.environ,
        # Keep known portable argv suggestions. Captured receipts never contain
        # argv tokens; a fresh settings directory could not reconstruct them.
        TAVOTTO_DATA_DIR=str(tmp_path / "empty-settings" if fresh_settings else config.data_dir()),
        MPLCONFIGDIR=str(tmp_path / "fresh-mpl"),
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", _COLD, str(Path(__file__).resolve().parents[1])],
        input=json.dumps({"root": str(root), "doc": doc}),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert Path(report["app"]).resolve() == Path(m.__file__).resolve()
    assert report["produced"]["name"].endswith(brand.PACKAGE_EXT)
    with zipfile.ZipFile(report["path"]) as package:
        assert package.testzip() is None
        members = {name: package.read(name) for name in package.namelist()}
    manifest = json.loads(members[documentpackage.MANIFEST_MEMBER])
    stored = json.loads(members[documentpackage.LAYOUT_MEMBER])
    assert stored == doc == report["inspection"]["doc"]
    assert report["inspection"]["manifest"] == manifest
    rows = {row["path"]: row for row in manifest["assets"]}
    assert set(rows) == set(members) - {
        documentpackage.LAYOUT_MEMBER,
        documentpackage.MANIFEST_MEMBER,
    }
    for name, row in rows.items():
        assert row["sha256"] == _sha(members[name]) and row["bytes"] == len(members[name])
    return report, manifest, members


def _assert_captures(report, manifest, members, expected):
    execution = manifest["execution"]
    assert execution["version"] == 2
    captures = execution["captures"]
    assert len(captures) == len(expected)
    assert all(set(row) == {"snapshot_ref", "revision"} for row in captures)
    actual = {row["snapshot_ref"]["snapshotId"]: row for row in captures}
    assert len(actual) == len(captures)
    prefix = f"assets/{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
    for reference, graph, contents in expected:
        row = {"snapshot_ref": reference, "revision": "sha256:" + _sha(graph)}
        assert actual[reference["snapshotId"]] == row
        assert members[prefix + "inputs/graphs/" + _sha(graph) + ".json"] == graph
        for digest, data in contents.items():
            assert members[prefix + f"inputs/blobs/{digest[:2]}/{digest[2:]}"] == data
        payload = json.loads(members[prefix + "snapshots/" + reference["snapshotId"] + ".json"])[
            "payload"
        ]
        assert payload["version"] == 2 and payload["input_archive"]["revision"] == row["revision"]
    inspection = report["inspection"]
    assert "plot.py" not in report["produced"]["missing"]
    assert "plot.py" not in inspection["missing"]
    facts = inspection["captured_inputs"]
    assert {(row["snapshot_ref"]["snapshotId"], row["revision"]) for row in facts} == {
        (row["snapshot_ref"]["snapshotId"], row["revision"]) for row in captures
    }
    for row in facts:
        capture = actual[row["snapshot_ref"]["snapshotId"]]
        assert row["snapshot_ref"] == capture["snapshot_ref"]
        assert row["scope"]["script"] == "plot.py"
        assert row["scope"]["run_config"] == capture["snapshot_ref"]["executionOwner"]["run_config"]
    warning_ids = {
        row["id"]
        for row in inspection["warnings"]
        if row["reason"] == "captured_input_channels_unverified"
    }
    assert warning_ids == set(actual)
    # Known run IDs/tokens remain portable suggestions, not receipt argv proof.
    run_ids = {ref["executionOwner"]["run_config"] for ref, _graph, _contents_ in expected} - {None}
    runs = {row["id"]: row for row in execution["runs"]}
    assert run_ids <= runs.keys()
    assert all(
        runs[run_id] == {"id": run_id, "script": "plot.py", "argv": ARGV} for run_id in run_ids
    )


@pytest.mark.parametrize("profile", ["safe", "native"])
def test_formal_v4_real_capture_survives_deleted_source_and_cache_in_fresh_parent(
    project, tmp_path, profile
):
    client, root, counter = project
    expected_argv = [] if profile == "native" else ARGV
    run, argv_log = _prepare(project, native=profile == "native", argv=expected_argv)
    source_bytes = (root / "plot.py").read_bytes()
    session = None
    if profile == "safe":
        doc, references, worker = _run_safe(project, run, name="FormalSafe", edits=True)
        worker_base = worker.base
    else:
        assert nativekit.USER_PYTHON is not None, "Existing user Python is required"
        native_run = runconfig.selection(root, run.id, script="plot.py") if run else None
        selection = declaredinputs.scope_selection(
            root, script="plot.py", cwd_mode="project_root", run=native_run, execution="native"
        )
        spec = execspec.desktop_native_spec(
            str(root / "plot.py"),
            interpreter=nativekit.USER_PYTHON,
            cwd=str(root),
            project_root=str(root),
            **execspec.run_kwargs(native_run),
        )
        nativesession.REGISTRY.reopen()
        session = desktopproc.start(
            desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST, input_declaration_state=selection)
        )
        try:
            nativekit.wait_state(session, [nativesession.BARRIER])
            session.ensure_built()
            assert m._execution_receipt(session, "plot.py").captured_input_archive is not None
            m._materialize_native(session)
            source = figcapture.runtime_asset_id("plot.py", "F", run.id if run else "")
            doc, reference = _save(client, root, source, name="FormalNative")
            references = [reference]
            worker_base = session.out_dir
        finally:
            if not session.owned.finished:
                session.cancel_owned()
    assert json.loads(argv_log.read_text(encoding="utf-8")) == expected_argv
    expected = []
    for ref in references:
        archive = _archive(root, ref)
        contents = _contents(archive)
        assert set(contents.values()) == {source_bytes, CSV, BINARY, b""}
        expected.append((ref, archive.graph_bytes, contents))
    assert len({graph for _ref, graph, _data in expected}) == 1
    _remove_source_and_cache(project, worker_base)
    report, manifest, members = _cold_package(
        root, doc, tmp_path, fresh_settings=profile == "native"
    )
    _assert_captures(report, manifest, members, expected)
    assert counter.read_bytes() == b"run\n"


def test_formal_package_keeps_same_source_historical_script_and_data_by_full_reference(
    project, tmp_path
):
    client, root, counter = project
    run, _argv_log = _prepare(project)
    first_doc, first_refs, worker = _run_safe(project, run, name="HistoryFirst")
    first = _archive(root, first_refs[0])
    first_expected = (first_refs[0], first.graph_bytes, _contents(first))
    pool.shutdown_all(figures_dir=str(root), wait=True)
    (root / "data.csv").write_bytes(CSV.replace(b",3\r\n", b",8\r\n"))
    with (root / "plot.py").open("ab") as stream:
        stream.write(b"\n# a genuinely later source version\n")
    second_doc, second_refs, worker = _run_safe(project, run, name="HistorySecond")
    second = _archive(root, second_refs[0])
    second_expected = (second_refs[0], second.graph_bytes, _contents(second))
    assert first_refs[0]["sourceId"] == second_refs[0]["sourceId"]
    assert first_refs[0]["snapshotId"] != second_refs[0]["snapshotId"]
    assert first.graph_bytes != second.graph_bytes
    first_doc["canvases"].append(second_doc["canvases"][0])
    first_doc["canvases"][1]["id"] = "historical_second"
    first_doc["canvases"][1]["objects"][0]["id"] = "later_panel"
    _remove_source_and_cache(project, worker.base)
    report, manifest, members = _cold_package(root, first_doc, tmp_path)
    _assert_captures(report, manifest, members, [first_expected, second_expected])
    # Every row remains well-formed and names a real full reference/revision;
    # exchanging their association must still fail semantic inspection.
    changed = copy.deepcopy(manifest)
    left, right = changed["execution"]["captures"]
    left["revision"], right["revision"] = right["revision"], left["revision"]
    _reject_upload(
        client, _rewrite(members, {documentpackage.MANIFEST_MEMBER: json.dumps(changed).encode()})
    )
    assert counter.read_bytes() == b"run\nrun\n"


def test_formal_default_without_declaration_retains_execution_v1(project, tmp_path):
    _client, root, counter = project
    run, _argv_log = _prepare(project, declared=False)
    doc, references, worker = _run_safe(project, run, name="DefaultV1")
    assert _archive(root, references[0]) is None
    _remove_source_and_cache(project, worker.base)
    report, manifest, _members = _cold_package(root, doc, tmp_path)
    assert manifest["execution"]["version"] == 1
    assert "captures" not in manifest["execution"]
    assert "captured_inputs" not in report["inspection"]
    assert not any(
        row["reason"] == "captured_input_channels_unverified"
        for row in report["inspection"]["warnings"]
    )
    assert counter.read_bytes() == b"run\n"


def _rewrite(members, changed):
    result = dict(members)
    result.update(changed)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in result.items():
            package.writestr(name, data)
    return stream.getvalue()


def _reject_upload(client, blob):
    response = client.post(
        INSPECT,
        data={"package": (io.BytesIO(blob), "damaged.tavotto")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 422, response.get_json()
    body = response.get_json()
    assert body["code"] == "package_invalid" and body["params"]["reason"]


@pytest.mark.parametrize("damage", ["graph", "blob", "reference"])
def test_formal_inspection_rejects_outer_resigned_captured_input_damage(project, tmp_path, damage):
    client, root, counter = project
    run, _argv_log = _prepare(project)
    doc, _references, worker = _run_safe(project, run, name="DamagedInputs")
    _remove_source_and_cache(project, worker.base)
    _report, manifest, members = _cold_package(root, doc, tmp_path)
    changed = copy.deepcopy(manifest)
    replacements = {}
    if damage == "reference":
        changed["execution"]["captures"][0]["snapshot_ref"]["patchHash"] = "sha256:" + "f" * 64
    else:
        suffix = "/inputs/graphs/" if damage == "graph" else "/inputs/blobs/"
        item = next(row for row in changed["assets"] if suffix in row["path"] and row["bytes"] > 0)
        raw = members[item["path"]]
        corrupted = bytes([raw[0] ^ 1]) + raw[1:]
        replacements[item["path"]] = corrupted
        item.update(sha256=_sha(corrupted), bytes=len(corrupted))
    replacements[documentpackage.MANIFEST_MEMBER] = json.dumps(changed, ensure_ascii=False).encode()
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    _reject_upload(client, _rewrite(members, replacements))
    assert {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } == before
    assert counter.read_bytes() == b"run\n"


def test_formal_cold_package_keeps_approved_external_file_and_prefix_after_they_are_deleted(
    project, tmp_path
):
    client, root, counter = project
    run, argv_log = _prepare(project, declared=False)
    external = tmp_path / "批准的外部输入"
    external.mkdir()
    single = external / "one.txt"
    single.write_bytes(b"5\n")
    prefix = external / "prefix"
    (prefix / "nested").mkdir(parents=True)
    (prefix / "空目录" / "深空").mkdir(parents=True)
    prefix_bytes = {
        "nested/value.txt": b"7\n",
        "nested/empty.bin": b"",
        "binary.dat": BINARY,
    }
    for name, data in prefix_bytes.items():
        (prefix / name).write_bytes(data)
    file_request = "legacy/原始 single.txt"
    # An old absolute request is opaque metadata. Only builtins.open's
    # already supported remap path is exercised, not arbitrary absolute IO.
    prefix_request = (tmp_path / "previous-machine" / "原始 prefix").as_posix()
    for kind, requested, target in (
        ("file", file_request, single),
        ("prefix", prefix_request, prefix),
    ):
        inputremap.add_rule(root, {"kind": kind, "from": requested, "to": str(target)})
    program = (
        "import json, sys\nimport matplotlib.pyplot as plt\n"
        "def main():\n"
        f"    with open({str(counter)!r}, 'ab') as count: count.write(b'run\\n')\n"
        f"    with open({str(argv_log)!r}, 'w', encoding='utf-8') as log: json.dump(sys.argv[1:], log)\n"
        f"    with open({file_request!r}, encoding='utf-8') as data: first = int(data.read())\n"
        f"    with open({(prefix_request + '/nested/value.txt')!r}, encoding='utf-8') as data: second = int(data.read())\n"
        "    fig, ax = plt.subplots(figsize=(2, 1))\n"
        "    ax.plot([0, 1], [first, second])\n"
        "    ax.set_title('Actual remapped 5 and 7')\n"
        "    fig.savefig('F.pdf')\n"
    )
    (root / "plot.py").write_text(program, encoding="utf-8")
    source_bytes = (root / "plot.py").read_bytes()
    response = client.post(
        "/api/engine/input-declarations",
        json={
            "script": "plot.py",
            "cwd_mode": "project_root",
            "run_config": run.id,
            "files": [],
            "remaps": [file_request, prefix_request],
            "expected_revision": declaredinputs.state(root)["revision"],
        },
    )
    assert response.status_code == 200, response.get_json()
    doc, references, worker = _run_safe(project, run, name="ExternalPrefix")
    reference = references[0]
    captured = _archive(root, reference)
    contents = _contents(captured)
    assert set(contents.values()) == {source_bytes, b"5\n", *prefix_bytes.values()}
    graph_bytes = captured.graph_bytes
    graph = json.loads(graph_bytes)
    mappings = {row["from"]: row for row in graph["mappings"]}
    assert set(mappings) == {file_request, prefix_request}
    assert mappings[file_request]["kind"] == "file"
    assert mappings[prefix_request]["kind"] == "prefix"
    assert len(graph["files"]) == len(graph["prefixes"]) == 1
    file_id, prefix_id = graph["files"][0]["id"], graph["prefixes"][0]["id"]
    assert file_id != prefix_id and all(
        value.startswith("remap:")
        and len(value) == len("remap:") + 64
        and all(char in "0123456789abcdef" for char in value[6:])
        for value in (file_id, prefix_id)
    )
    assert mappings[file_request]["id"] == file_id
    assert mappings[prefix_request]["id"] == prefix_id
    tree = graph["prefixes"][0]
    assert set(tree["directories"]) == {"", "nested", "空目录", "空目录/深空"}
    assert {row["suffix"]: (row["sha256"], row["size_bytes"]) for row in tree["members"]} == {
        name: (_sha(data), len(data)) for name, data in prefix_bytes.items()
    }
    assert json.loads(argv_log.read_text(encoding="utf-8")) == ARGV
    assert external.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(external)
    _remove_source_and_cache(project, worker.base)
    assert not single.exists() and not prefix.exists()
    report, manifest, members = _cold_package(root, doc, tmp_path)
    _assert_captures(report, manifest, members, [(reference, graph_bytes, contents)])
    summary = report["inspection"]["captured_inputs"][0]
    assert summary["files"] == summary["prefixes"] == 1
    assert counter.read_bytes() == b"run\n"
