"""Actual safe/native executions install through HTTP and survive a cold parent.

The install endpoint publishes the new directory. No copytree substitutes for
installation; the cold consumer reads the already installed machine settings
without importing configuration or restoring remaps again.
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
from pathlib import Path

import pytest

import test_package_relocation_e2e as relocation_fixtures
import test_svg_input_package_e2e as package_fixtures
from tavotto import app as m
from tavotto.engine import (
    config,
    declaredinputs,
    desktopproc,
    inputremap,
    pool,
    registry,
    runconfig,
    svgstore,
    workdir,
)
from test_chart_package_http import panel
from test_svg_input_save_e2e import BINARY

project = package_fixtures.project
INSTALL = "/api/documents/v4/package/install"
ORDINARY = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="orange"/></svg>'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _prepare_actual_inputs(project, tmp_path, profile):
    client, root, _counter = project
    run, argv_log = package_fixtures._prepare(
        project, native=profile == "native", argv=package_fixtures.ARGV
    )
    assert run is not None
    external = tmp_path / "自有批准外部输入"
    external.mkdir()
    single = external / "one.txt"
    single.write_bytes(b"5\n")
    prefix = external / "prefix"
    (prefix / "nested" / "空目录").mkdir(parents=True)
    (prefix / "nested" / "values.csv").write_bytes(b"1,2\r\n")
    (prefix / "zero.dat").write_bytes(b"")
    for kind, requested, target in (
        ("file", "old-single", single),
        ("prefix", "old-series", prefix),
    ):
        inputremap.add_rule(root, {"kind": kind, "from": requested, "to": str(target)})
    first = "old-single" if profile == "safe" else str(single)
    second = (
        "old-series/nested/values.csv" if profile == "safe" else str(prefix / "nested/values.csv")
    )
    canary = tmp_path / "actual-module-executions.log"
    source = root / "plot.py"
    text = source.read_text(encoding="utf-8")
    text = f"with open({str(canary)!r}, 'ab') as loaded: loaded.write(b'loaded\\n')\n" + text
    text = text.replace(
        "    fig, ax = plt.subplots",
        f"    with open({first!r}, 'rb') as data: assert data.read() == b'5\\n'\n"
        f"    with open({second!r}, 'rb') as data: assert data.read() == b'1,2\\r\\n'\n"
        f"    with open('raw.bin', 'rb') as data: assert data.read() == {BINARY!r}\n"
        "    with open('empty.dat', 'rb') as data: assert data.read() == b''\n"
        "    fig, ax = plt.subplots",
    )
    source.write_text(text, encoding="utf-8")
    response = client.post(
        "/api/engine/input-declarations",
        json={
            "script": "plot.py",
            "cwd_mode": "project_root",
            "run_config": run.id,
            "files": ["data.csv", "raw.bin", "empty.dat"],
            "remaps": ["old-single", "old-series"],
            "expected_revision": declaredinputs.state(root)["revision"],
        },
    )
    assert response.status_code == 200, response.get_json()
    return run, argv_log, external, canary


def _remove_owned_directory(path, owner, target):
    resolved, boundary = path.resolve(), owner.resolve()
    assert resolved.is_relative_to(boundary)
    assert resolved not in {boundary, target.resolve()}
    shutil.rmtree(resolved)


_COLD = r"""
import base64, hashlib, json, sys
from pathlib import Path
sys.path[:0] = [str(Path(sys.argv[1]) / 'src')]
from tavotto import app as m
from tavotto.engine import config, inputremap, pool, project_watch, registry, runconfig, svgstore, workdir
from tavotto.rendercore import renderhost
from tavotto.rendercore.sources import read_frozen
request = json.loads(sys.stdin.read())
root = Path(request['root'])
assert not Path(request['old_root']).exists()
assert not Path(request['external']).exists()
actual = inputremap.fingerprint(root)
assert actual == request['fingerprint']
assert inputremap.rules_for(root) == request['rules']
run = runconfig.get(root, request['run']['id'], script='plot.py')
assert {'id':run.id,'script':run.script,'argv':list(run.argv)} == request['run']
assert runconfig.default_selection(root, 'plot.py') is None
assert not workdir.decided(root)
assert workdir.SETTINGS_KEY not in config.project_settings(str(root))
registry_bytes = registry.registry_path(root).read_bytes()
assert hashlib.sha256(registry_bytes).hexdigest() == request['registry_sha256']
assert (root / 'ordinary.svg').read_bytes().hex() == request['ordinary_hex']
def forbidden(*a,**kw): raise AssertionError('cold read attempted scientific execution')
pool.get = forbidden
pool.one_shot = forbidden
m.app.config['TESTING'] = True
opened = m.open_project(str(root))
assert m.current_ctx().svg_executions.owners == {}
results = []
def pdf_pixels(data):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(data) as document:
        assert len(document) == 1
        page = document[0]
        bitmap = page.render(scale=1)
        try:
            result = [list(page.get_size()), hashlib.sha256(bitmap.to_pil().convert('RGB').tobytes()).hexdigest()]
        finally:
            bitmap.close(); page.close()
    return result
try:
    for index, row in enumerate(request['rows']):
        reference, patches = row['new_ref'], row['patches']
        with m.app.test_request_context('/?pj=' + opened['id']):
            frozen = m._load_svg_snapshot(reference['sourceId'], patches,
                Path(request['work']) / ('pair-' + str(index)), reference=reference)
            assert frozen is not None, reference
            pdf = read_frozen(frozen)
            svg = read_frozen(frozen.svg_representation())
        record = svgstore.load_document_snapshot(root, reference,
            source_id=reference['sourceId'], patch_hash=reference['patchHash'],
            remap_fingerprint=actual,
            storage_root=root / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME)
        assert record is not None, reference
        archive = record['captured_input_archive']
        inputs = {}
        for resource in archive.resources:
            data = resource.path.read_bytes()
            assert len(data) == resource.size_bytes
            assert hashlib.sha256(data).hexdigest() == resource.sha256
            inputs[resource.sha256] = base64.b64encode(data).decode('ascii')
        response = m.app.test_client().post('/api/export?pj=' + opened['id'], json={
            'scope':'original','formats':['pdf','svg'],'filename':'cold-history-' + str(index),
            'overwrite':'replace','original':{'figure_id':reference['sourceId'],
                'source_kind':'vector','overrides':patches,'figure_snapshot':reference}})
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        assert body['status'] == 'done', body
        outputs = {item['format']:Path(body['export_dir']) / item['name'] for item in body['outputs'] if item['status']=='done'}
        assert set(outputs) == {'pdf','svg'}, body
        assert outputs['svg'].read_bytes() == svg
        assert pdf_pixels(outputs['pdf'].read_bytes()) == pdf_pixels(pdf)
        results.append({'old_ref':row['old_ref'],'new_ref':record['snapshot_ref'],
            'receipt':record['receipt'],'pdf_sha256':hashlib.sha256(pdf).hexdigest(),
            'svg_sha256':hashlib.sha256(svg).hexdigest(),
            'graph':base64.b64encode(archive.graph_bytes).decode('ascii'),'inputs':inputs})
    assert m.current_ctx().svg_executions.owners == {}
    assert not any(name in sys.modules for name in ('matplotlib','numpy'))
    assert Path(request['counter']).read_bytes() == b'run\n'
    assert Path(request['canary']).read_bytes() == b'loaded\n'
    print(json.dumps({'results':results,'fingerprint':actual,'app':m.__file__,'store':svgstore.__file__}))
finally:
    m.reset_projects(wait=True); project_watch.stop(); renderhost.shutdown_shared()
"""


@pytest.mark.parametrize("profile", ["safe", "native"])
def test_actual_install_api_publishes_history_and_cold_reads_without_old_sources_or_reexecution(
    project, tmp_path, monkeypatch, profile
):
    client, source_root, counter = project
    run, argv_log, external, canary = _prepare_actual_inputs(project, tmp_path, profile)
    if profile == "safe":
        document, refs, worker = package_fixtures._run_safe(
            project, run, name="InstallSafe", edits=True
        )
        worker_base = worker.base
        inactive = copy.deepcopy(document["canvases"][0])
        inactive["id"] = "inactive"
        inactive["objects"] = [inactive["objects"].pop()]
        inactive["objects"][0]["id"] = "edited-inactive"
        document["canvases"].append(inactive)
    else:
        document, refs, worker_base = relocation_fixtures._run_native(project, run)
    assert len(refs) == 2 and refs[0] != refs[1]
    assert counter.read_bytes() == b"run\n"
    assert canary.read_bytes() == b"loaded\n"
    assert json.loads(argv_log.read_text(encoding="utf-8")) == package_fixtures.ARGV
    expected = relocation_fixtures._expected(source_root, refs)
    graph = json.loads(package_fixtures._archive(source_root, refs[0]).graph_bytes)
    assert len(graph["prefixes"]) == 1
    assert "nested/空目录" in graph["prefixes"][0]["directories"]
    assert {member["suffix"] for member in graph["prefixes"][0]["members"]} == {
        "nested/values.csv",
        "zero.dat",
    }
    assert graph["scope"]["run_config"] == run.id
    files = {row["id"]: row for row in graph["files"]}
    assert {"data.csv", "raw.bin", "empty.dat"} < set(files)
    assert files["raw.bin"]["sha256"] == _sha(BINARY)
    assert files["empty.dat"]["size_bytes"] == 0
    external_file = next(row for name, row in files.items() if name.startswith("remap:"))
    assert external_file["sha256"] == _sha(b"5\n") and external_file["size_bytes"] == 2
    prefix_files = {row["suffix"]: row for row in graph["prefixes"][0]["members"]}
    assert prefix_files["nested/values.csv"]["sha256"] == _sha(b"1,2\r\n")
    assert prefix_files["zero.dat"]["size_bytes"] == 0

    (source_root / "ordinary.svg").write_bytes(ORDINARY)
    ordinary = panel(file="ordinary.svg", number=99)
    ordinary.update(id="ordinary-static-resource", fileKind="svg")
    document["canvases"][0]["objects"].append(ordinary)
    packed, manifest, members = package_fixtures._cold_package(source_root, document, tmp_path)
    package_bytes = Path(packed["path"]).read_bytes()
    assert manifest["execution"]["runs"] == [
        {"id": run.id, "script": "plot.py", "argv": package_fixtures.ARGV}
    ]
    ordinary_record = next(row for row in manifest["assets"] if row.get("id") == "ordinary.svg")
    assert members[ordinary_record["path"]] == ORDINARY

    target = tmp_path / "中文 安装新项目"
    data = next(row for row in graph["files"] if row["id"] == "data.csv")
    rules = [
        {
            "kind": "file",
            "from": "data.csv",
            "to": str(
                target
                / config.PROJECT_STORE_DIRNAME
                / svgstore.STORE_DIRNAME
                / "inputs/blobs"
                / data["sha256"][:2]
                / data["sha256"][2:]
            ),
        }
    ]
    grant_before = workdir.grant_for(target)
    projects_before, default_before = set(m.PROJECTS), m.DEFAULT_PROJECT
    body_before = {
        path: path.read_bytes()
        for path in (source_root / config.PROJECT_STORE_DIRNAME).rglob("*.json")
        if svgstore.STORE_DIRNAME not in path.parts
    }

    def forbidden(*_args, **_kwargs):
        pytest.fail("Installing a package may not open a project or execute its Python")

    with monkeypatch.context() as guard:
        guard.setattr(m, "open_project", forbidden)
        guard.setattr(pool, "get", forbidden)
        guard.setattr(pool, "one_shot", forbidden)
        guard.setattr(desktopproc, "start", forbidden)
        response = client.post(
            INSTALL + "?pj=" + m._project_id(source_root),
            data={
                "package": (io.BytesIO(package_bytes), "actual-history.tavotto"),
                "options": json.dumps({"destination": str(target), "remap_rules": rules}),
            },
            content_type="multipart/form-data",
        )
    assert response.status_code == 201, response.get_json()
    result = response.get_json()
    assert result["state"] == "resources_installed" and result["configuration"] == "ready"
    assert result["path"] == str(target) and result["document_saved"] is False
    assert result["configuration_review"]["existing_local_metadata"] is False
    assert set(m.PROJECTS) == projects_before and m.DEFAULT_PROJECT == default_before
    assert {path: path.read_bytes() for path in body_before} == body_before
    assert workdir.grant_for(target) == grant_before and not workdir.decided(target)
    assert workdir.SETTINGS_KEY not in config.project_settings(str(target))
    assert runconfig.default_selection(target, "plot.py") is None
    selected = runconfig.get(target, run.id, script="plot.py")
    assert list(selected.argv) == package_fixtures.ARGV
    assert result["execution"]["runs"] == manifest["execution"]["runs"]
    assert inputremap.rules_for(target) == rules
    assert inputremap.fingerprint(target) == result["remap_fingerprint"]
    assert (target / "ordinary.svg").read_bytes() == ORDINARY
    assert registry.open_registry(target).entries() == manifest["execution"]["registry"]["scripts"]
    store = target / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    facts = svgstore.inspect_portable_store(storage_root=store)
    assert not facts["entries"] and not facts["sources"]
    assert len(facts["snapshots"]) == 2
    assert all(
        path.relative_to(target).parts[1] == svgstore.STORE_DIRNAME
        for path in (target / config.PROJECT_STORE_DIRNAME).rglob("*")
        if path.is_file()
    )
    mapping = {row["old_ref"]["snapshotId"]: row["new_ref"] for row in result["references"]}
    assert set(mapping) == set(expected)
    restored = copy.deepcopy(result["doc"])
    patches = {}
    for canvas in restored["canvases"]:
        for item in canvas["objects"]:
            new = item.get("figureSnapshot")
            if new is not None:
                old = next(row["old_ref"] for row in result["references"] if row["new_ref"] == new)
                patches[old["snapshotId"]] = item["overrides"]
                item["figureSnapshot"] = old
    assert restored == document
    assert counter.read_bytes() == b"run\n" and canary.read_bytes() == b"loaded\n"

    package_fixtures._remove_source_and_cache(project, worker_base)
    _remove_owned_directory(source_root, tmp_path, target)
    _remove_owned_directory(external, tmp_path, target)
    assert not source_root.exists() and not external.exists()
    request = {
        "root": str(target),
        "old_root": str(source_root),
        "external": str(external),
        "work": str(tmp_path / "cold-output"),
        "counter": str(counter),
        "canary": str(canary),
        "fingerprint": result["remap_fingerprint"],
        "rules": rules,
        "run": manifest["execution"]["runs"][0],
        "registry_sha256": _sha(registry.registry_path(target).read_bytes()),
        "ordinary_hex": ORDINARY.hex(),
        "rows": [
            {
                "old_ref": ref,
                "new_ref": mapping[ref["snapshotId"]],
                "patches": patches[ref["snapshotId"]],
            }
            for ref in refs
        ],
    }
    cold = subprocess.run(
        [sys.executable, "-B", "-c", _COLD, str(Path(__file__).resolve().parents[1])],
        input=json.dumps(request),
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr
    actual = json.loads(cold.stdout)
    assert Path(actual["app"]).resolve() == Path(m.__file__).resolve()
    assert Path(actual["store"]).resolve() == Path(svgstore.__file__).resolve()
    assert actual["fingerprint"] == result["remap_fingerprint"]
    for row in actual["results"]:
        old = row.pop("old_ref")
        assert row.pop("new_ref") == mapping[old["snapshotId"]]
        assert row == expected[old["snapshotId"]]
    assert len(actual["results"]) == 2
    assert counter.read_bytes() == b"run\n" and canary.read_bytes() == b"loaded\n"
