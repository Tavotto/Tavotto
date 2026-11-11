"""Actual safe/native executions install through HTTP and survive a cold parent.

The install endpoint publishes the new directory. No copytree substitutes for
installation; the cold consumer reads the already installed machine settings
without importing configuration or restoring remaps again.
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


_COLD_RUN = r"""
import base64, json, sys
from pathlib import Path
sys.path[:0] = [str(Path(sys.argv[1]) / 'src')]
from tavotto import app as m
from tavotto.engine import config, inputremap, pool, project_watch, runconfig, svgstore
from tavotto.rendercore import renderhost
request = json.loads(sys.stdin.read())
root = Path(request['root'])
work = Path(request['work']); work.mkdir(parents=True, exist_ok=True)
m.app.config['TESTING'] = True
opened = m.open_project(str(root))

def facts(data, png):
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(data) as document:
        assert len(document) == 1
        page = document[0]
        try:
            textpage = page.get_textpage()
            try: text = textpage.get_text_range()
            finally: textpage.close()
            bitmap = page.render(scale=2)
            try:
                rgb = bitmap.to_pil().convert('RGB')
                rgb.save(png)
                return {'size': list(page.get_size()), 'px': list(rgb.size), 'text': text}
            finally: bitmap.close()
        finally: page.close()

# 1. The package carries no source. A cold attempt must fail clearly and
#    execute nothing; it may never borrow a same-name file.
counter = Path(request['counter']); canary = Path(request['canary'])
before = (counter.read_bytes(), canary.read_bytes())
missing = None
try:
    pool.get('plot.py', str(root), 'main').override('F', [], None, inline_svg=True)
except Exception as exc:
    missing = {'code': getattr(exc, 'code', None), 'error': str(exc)}
finally:
    pool.shutdown_all(figures_dir=str(root), wait=True)
assert missing is not None, 'a package without its source executed something'
assert (counter.read_bytes(), canary.read_bytes()) == before, 'missing source executed the script'

# 2. The user explicitly supplies the source; the run then uses the restored
#    INPUT at its recorded roles and produces one new execution.
(root / 'plot.py').write_bytes(base64.b64decode(request['script_b64']))
for name, encoded in request['inputs_b64'].items():
    (root / name).write_bytes(base64.b64decode(encoded))
run = runconfig.selection(root, request['run_id'], script='plot.py')
worker = pool.get('plot.py', str(root), 'main', run=run)
try:
    base = worker.override('F', [], None, inline_svg=True)
    titles = sorted(f['value'] for e in base['manifest']['elements']
                    for f in e.get('editable', []) if f['prop'] == 'text')
    receipt = m._execution_receipt(worker, 'plot.py')
    patches = request['patches']
    worker.override('F', patches, None, inline_svg=True)
    out = work / 'cold.pdf'
    worker.export('F', patches, str(out), 'pdf', 600)
    cold = facts(out.read_bytes(), work / 'cold.png')
    reference = request['reference']
    record = svgstore.load_document_snapshot(
        root, reference, source_id=reference['sourceId'], patch_hash=reference['patchHash'],
        remap_fingerprint=inputremap.fingerprint(root),
        storage_root=root / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME)
    assert record is not None
    archived = facts(record['pdf'], work / 'archived.png')
    from tavotto import pdfbackend
    # The same worker must be deterministic for this exact P; the archived
    # artifact is compared separately below because it is a stored export.
    out2 = work / 'cold2.pdf'
    worker.export('F', patches, str(out2), 'pdf', 600)
    facts(out2.read_bytes(), work / 'cold2.png')
    repeat = pdfbackend.compare_png(work / 'cold.png', work / 'cold2.png')
    assert repeat['changed_pixel_ratio'] == 0, repeat
    metrics = pdfbackend.compare_png(work / 'archived.png', work / 'cold.png')
    print(json.dumps({'missing': missing, 'before': [b.decode() for b in before], 'titles': titles, 'receipt': receipt.receipt_id,
                      'execution': receipt.instance, 'size': cold['size'], 'px': cold['px'],
                      'text': cold['text'], 'archived_size': archived['size'],
                      'archived_px': archived['px'], 'archived_text': archived['text'],
                      'metrics': metrics}))
except Exception as exc:
    import traceback
    print(json.dumps({'error': str(exc), 'code': getattr(exc, 'code', None),
                      'extra': getattr(exc, 'extra', None),
                      'traceback': traceback.format_exc()}))
    raise
finally:
    pool.shutdown_all(figures_dir=str(root), wait=True)
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
    # The cold run exercises the restored project-root INPUT. The two external
    # remap targets are machine-local rules asserted above, not package data.
    script_bytes = b"\n".join(
        line
        for line in (source_root / "plot.py").read_bytes().splitlines()
        if b"old-single" not in line and b"old-series" not in line
    )
    input_bytes = {
        name: (source_root / name).read_bytes() for name in ("data.csv", "raw.bin", "empty.dat")
    }

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

    if profile != "safe":
        return
    # Cold read is not run permission. An explicit run in a new parent process
    # must use the installed INPUT at its recorded roles and produce one new
    # execution whose geometry, text and pixels match the archived baseline.
    cold_run = subprocess.run(
        [sys.executable, "-B", "-c", _COLD_RUN, str(Path(__file__).resolve().parents[1])],
        input=json.dumps(
            {
                "root": str(target),
                "work": str(tmp_path / "cold-run-output"),
                "patches": patches[refs[0]["snapshotId"]],
                "run_id": run.id,
                "reference": mapping[refs[0]["snapshotId"]],
                "script_b64": base64.b64encode(script_bytes).decode("ascii"),
                "inputs_b64": {
                    name: base64.b64encode(value).decode("ascii")
                    for name, value in input_bytes.items()
                },
                "counter": str(counter),
                "canary": str(canary),
            }
        ),
        # Same machine, new parent process: the machine-local run registry and
        # data dir are inherited (run parameters never travel with a package).
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert cold_run.returncode == 0, cold_run.stdout + cold_run.stderr
    executed = json.loads(cold_run.stdout)
    # A package without its source is refused, and executes nothing.
    assert executed["before"] == ["run\n", "loaded\n"]
    assert executed["missing"]["code"] == "script_source_missing", executed["missing"]
    # With the user-supplied source the run uses the restored INPUT exactly once.
    assert counter.read_bytes() == b"run\nrun\n"
    assert canary.read_bytes() == b"loaded\nloaded\n"
    assert any(title.startswith("Captured ") for title in executed["titles"]), executed["titles"]
    assert executed["size"] == executed["archived_size"]
    assert executed["px"] == executed["archived_px"]
    assert executed["text"] == executed["archived_text"]
    # Cross-artifact comparison (stored export vs a direct worker savefig):
    # the page, text and content extent must match exactly, and the only
    # allowed difference is localized font/edge rasterization.
    assert executed["metrics"]["ok"], json.dumps(executed["metrics"])
    for key, tol in {
        "changed_pixel_ratio": 0.02,
        "mean_abs_diff": 1.0,
        "max_abs_diff": 200,
    }.items():
        assert executed["metrics"][key] <= tol, (key, json.dumps(executed["metrics"]))
    # A cold run gets its own receipt; it never re-signs the archived one.
    assert executed["receipt"] != refs[0]["receiptId"]
    assert executed["execution"]
