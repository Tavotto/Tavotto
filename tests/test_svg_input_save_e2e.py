"""Real execution -> application snapshots -> cold application package staging.

Scientific bytes are produced by the existing user Python, never a parent-side
plotting import or a fabricated pair. Archive inspection is not run permission.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from support import desktopkit, nativekit
from tavotto import app as m
from tavotto.engine import (
    declaredinputs,
    desktopproc,
    execspec,
    figcapture,
    inputdeclaration,
    nativesession,
    patchspec,
    pool,
    receipt,
    svgstore,
    workdir,
)
from tavotto.rendercore import sources
from test_export_svg import document, project as base_project

CSV = b'label,value\r\n"comma, tab\t quote"" and\nnewline",3\r\n'
BINARY = b'\0\xff\r\n"raw,bytes"'
TITLE = "Captured value 3"


def _script(counter, *, changed=False, failed=False, native=False):
    text = (
        "from pathlib import Path\nimport csv\nimport matplotlib.pyplot as plt\n"
        "def main():\n"
        f"    with open({str(counter)!r}, 'ab') as count: count.write(b'run\\n')\n"
        "    with open('data.csv', newline='', encoding='utf-8') as data:\n"
        "        rows = list(csv.reader(data))\n"
        "    value = float(rows[1][1])\n"
        "    fig, ax = plt.subplots(figsize=(2, 1))\n"
        "    ax.plot([0, 1], [0, value], color='red')\n"
        "    ax.set_title('Captured value ' + str(int(value)))\n"
        "    fig.savefig('F.pdf')\n"
    )
    if changed:
        text += "    with open('data.csv', 'w', encoding='utf-8') as data: data.write('changed')\n"
    if failed:
        text += "    raise RuntimeError('after real Figure creation')\n"
    if native:
        text += "main()\nplt.show()\n"
    return text


@pytest.fixture
def project(tmp_path, monkeypatch):
    from tavotto.rendercore import fonts

    assert not fonts.FontRegistry.discover().missing, "Run scripts/fetch_fonts.py first"
    worker_python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "preview")
    for client, root in base_project.__wrapped__(tmp_path, monkeypatch):
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_python)
        workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
        counter = tmp_path / "actual-executions.txt"
        (root / "plot.py").write_text(_script(counter), encoding="utf-8")
        (root / "data.csv").write_bytes(CSV)
        (root / "raw.bin").write_bytes(BINARY)
        (root / "empty.dat").write_bytes(b"")
        yield client, root, counter


def _declare(client, *, mode="project_root"):
    response = client.post(
        "/api/engine/input-declarations",
        json={
            "script": "plot.py",
            "cwd_mode": mode,
            "files": ["data.csv", "raw.bin", "empty.dat"],
            "expected_revision": None,
        },
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _run(client):
    response = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _save(client, source, *, patches=(), name="saved"):
    doc = document(source, list(patches))
    response = client.post(f"/api/layouts/{name}?target=project", json=doc)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body.get("svg_snapshot_warnings", []) == [], body
    reference = body["figure_snapshots"]["panel"]
    doc["canvases"][0]["objects"][0]["figureSnapshot"] = reference
    return doc, reference


def _contents(archive):
    assert isinstance(archive, inputdeclaration.InputArchive)
    result = {}
    for row in archive.resources:
        data = row.path.read_bytes()
        assert len(data) == row.size_bytes and hashlib.sha256(data).hexdigest() == row.sha256
        result[row.sha256] = data
    return result


def _pair(reference, root):
    record = svgstore.inspect_portable_snapshot(reference, storage_root=m._svg_storage_root(root))
    assert record["pdf"].startswith(b"%PDF-")
    tree = ET.fromstring(record["svg"])
    assert tree.tag == "{http://www.w3.org/2000/svg}svg"
    assert list(tree.iter("{http://www.w3.org/2000/svg}path"))
    return record


_COLD_PACKAGE = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'src'))
from tavotto import app as m
request = json.loads(sys.stdin.read())
m.app.config['TESTING'] = True
m.open_project(request['root'])
try:
    with m.app.test_client() as client:
        response = client.post('/api/package', json={'doc': request['doc'], 'stem': 'cold-inputs'})
        assert response.status_code == 200, response.get_json()
        body = response.get_json()
        path = m.project_export_dir() / body['name']
        assert path.is_file()
        print(json.dumps({'path': str(path), 'response': body, 'app': m.__file__}))
finally:
    m.reset_projects()
    from tavotto.engine import project_watch
    project_watch.stop()
"""


def test_actual_safe_auto_capture_edits_save_and_cold_package_keep_exact_input_bytes(
    project, tmp_path
):
    client, root, counter = project
    original_source = (root / "plot.py").read_bytes()
    _declare(client)
    ran = _run(client)
    assert ran["registered"], ran
    source = figcapture.runtime_asset_id("plot.py", "F")
    worker = pool.peek("plot.py", root)
    assert worker is not None and worker.last_captured_input_archive is not None
    receipt_now = m._execution_receipt(worker, "plot.py")
    assert receipt_now.captured_input_archive is not None
    # Probe's real materialization already persisted the original, before a save.
    original = svgstore.load(
        root, source, patchspec.patch_hash([]), storage_root=m._svg_storage_root(root)
    )
    assert (
        original["captured_input_archive"].graph_bytes
        == receipt_now.captured_input_archive.graph_bytes
    )
    doc, reference = _save(client, source)
    base = _pair(reference, root)
    expected = _contents(base["captured_input_archive"])
    assert set(expected.values()) == {original_source, CSV, BINARY, b""}
    graph = base["captured_input_archive"].graph_bytes
    assert (
        base["receipt"]["captured_inputs"]["revision"]
        == "sha256:" + hashlib.sha256(graph).hexdigest()
    )

    rendered = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert rendered.status_code == 200, rendered.get_json()
    manifest = rendered.get_json()["manifest"]
    gid = next(
        element["gid"]
        for element in manifest["elements"]
        for prop in element.get("editable", [])
        if prop["prop"] == "text" and prop.get("value") == TITLE
    )
    edits = [{"gid": gid, "prop": "text", "value": "Saved exact input figure"}]
    rendered = client.post("/api/engine/render", json={"id": source, "patches": edits})
    assert rendered.status_code == 200, rendered.get_json()
    edited_doc, edited_ref = _save(client, source, patches=edits, name="edited")
    edited = _pair(edited_ref, root)
    assert edited_ref["snapshotId"] != reference["snapshotId"]
    assert edited["captured_input_archive"].graph_bytes == graph
    assert _contents(edited["captured_input_archive"]) == expected
    second = edited_doc["canvases"][0]["objects"][0]
    second["id"] = "edited"
    doc["canvases"][0]["objects"].append(second)
    assert counter.read_bytes() == b"run\n"

    worker_base = worker.base.resolve()
    pool.shutdown_all(figures_dir=str(root), wait=True)
    assert worker_base.is_relative_to(tmp_path.resolve())
    if worker_base.exists():
        shutil.rmtree(worker_base)
    for name in ("plot.py", "data.csv", "raw.bin", "empty.dat"):
        (root / name).write_bytes(b"changed after capture")
        (root / name).unlink()
    m.reset_projects()
    env = dict(
        os.environ,
        TAVOTTO_DATA_DIR=str(tmp_path / "cold-data"),
        MPLCONFIGDIR=str(tmp_path / "cold-mpl"),
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", _COLD_PACKAGE, str(Path(__file__).resolve().parents[1])],
        input=json.dumps({"root": str(root), "doc": doc}),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert Path(report["app"]).resolve() == Path(m.__file__).resolve()
    with zipfile.ZipFile(report["path"]) as package:
        prefix = "assets/tavottofile/" + svgstore.STORE_DIRNAME + "/"
        assert (
            package.read(prefix + "inputs/graphs/" + hashlib.sha256(graph).hexdigest() + ".json")
            == graph
        )
        for digest, data in expected.items():
            assert package.read(prefix + f"inputs/blobs/{digest[:2]}/{digest[2:]}") == data
        for ref in (reference, edited_ref):
            payload = json.loads(package.read(prefix + "snapshots/" + ref["snapshotId"] + ".json"))[
                "payload"
            ]
            assert (
                payload["version"] == 2
                and payload["input_archive"]["revision"]
                == "sha256:" + hashlib.sha256(graph).hexdigest()
            )
    assert counter.read_bytes() == b"run\n"


def test_same_real_receipt_upgrades_legacy_v1_cache_without_overwriting_old_archive(
    project, tmp_path
):
    client, root, counter = project
    _declare(client)
    worker, built = pool.build("plot.py", str(root), "main", allow_project_env=False)
    rcpt = m._execution_receipt(worker, "plot.py")
    assert rcpt.captured_input_archive is not None
    source = figcapture.runtime_asset_id("plot.py", "F")
    pdf, svg = tmp_path / "actual.pdf", tmp_path / "actual.svg"
    response = worker.export(
        "F", [], str(pdf), "pdf", svg_path=str(svg), require_built=True, snapshot_only=True
    )
    assert response["svg"]["path"] == str(svg)
    patch = patchspec.patch_hash([])
    frozen = sources.FrozenSource(
        receipt.source_artifact_for(rcpt, pdf, source_id=source, patch_hash=patch),
        pdf,
        rcpt.public_facts(),
        sources.FrozenSource(
            receipt.source_artifact_for(rcpt, svg, source_id=source, patch_hash=patch),
            svg,
            rcpt.public_facts(),
        ),
    )
    old = m._save_svg_snapshot(frozen, execution_owner={"script": "plot.py", "run_config": None})
    old_ref = old["snapshot_ref"]
    old_file = m._svg_storage_root(root) / "snapshots" / (old_ref["snapshotId"] + ".json")
    old_bytes = old_file.read_bytes()
    assert json.loads(old_bytes)["payload"]["version"] == 1
    desc = next(row for row in built["descriptors"] if row["stem"] == "F")
    m._capture_svg_snapshot_best_effort(worker, desc)
    upgraded = svgstore.load(root, source, patch, storage_root=m._svg_storage_root(root))
    assert upgraded["snapshot_ref"]["snapshotId"] != old_ref["snapshotId"]
    assert upgraded["captured_input_archive"].graph_bytes == rcpt.captured_input_archive.graph_bytes
    assert old_file.read_bytes() == old_bytes
    assert "captured_input_archive" not in svgstore.inspect_portable_snapshot(
        old_ref, storage_root=m._svg_storage_root(root)
    )
    assert counter.read_bytes() == b"run\n"


@pytest.mark.parametrize("case", ["absent", "late", "scope", "changed", "failed"])
def test_real_safe_runs_do_not_invent_input_attachments(project, case):
    client, root, counter = project
    if case not in {"absent", "late"}:
        _declare(client, mode="project" if case == "scope" else "project_root")
    (root / "plot.py").write_text(
        _script(counter, changed=case == "changed", failed=case == "failed"), encoding="utf-8"
    )
    ran = _run(client)
    assert counter.read_bytes() == b"run\n"
    if case == "failed":
        assert not ran["registered"], ran
    else:
        assert ran["registered"], ran
        source = figcapture.runtime_asset_id("plot.py", "F")
        worker = pool.peek("plot.py", root)
        assert worker is not None
        assert m._execution_receipt(worker, "plot.py").captured_input_archive is None
        if case == "late":
            _declare(client)
            desc = next(row for row in ran["descriptors"] if row["stem"] == "F")
            m._capture_svg_snapshot_best_effort(worker, desc)
        _, reference = _save(client, source, name=case)
        record = _pair(reference, root)
        assert "captured_inputs" not in record["receipt"]
        assert "captured_input_archive" not in record
    assert not (m._svg_storage_root(root) / "inputs").exists()


@nativekit.needs_user_python
def test_real_owned_native_barrier_application_capture_persists_the_same_input_archive(project):
    client, root, counter = project
    (root / "plot.py").write_text(_script(counter, native=True), encoding="utf-8")
    _declare(client)
    selection = declaredinputs.scope_selection(root, script="plot.py", cwd_mode="project_root")
    spec = execspec.desktop_native_spec(
        str(root / "plot.py"),
        interpreter=nativekit.USER_PYTHON,
        cwd=str(root),
        project_root=str(root),
    )
    # The shared application fixture simulates sidecar shutdown between tests.
    # Use the registry's actual same-process restart entry, not a fake session.
    nativesession.REGISTRY.reopen()
    session = desktopproc.start(
        desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST, input_declaration_state=selection)
    )
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        built = session.ensure_built()
        rcpt = m._execution_receipt(session, "plot.py")
        assert rcpt.captured_input_archive is not None
        assert built["execution_id"] == session.execution_id
        desc = next(row for row in session.descriptors if row["stem"] == "F")
        m._capture_svg_snapshot_best_effort(session, desc)
        source = desc["asset_id"]
        record = svgstore.load(
            root, source, patchspec.patch_hash([]), storage_root=m._svg_storage_root(root)
        )
        assert (
            record["captured_input_archive"].graph_bytes == rcpt.captured_input_archive.graph_bytes
        )
        assert _contents(record["captured_input_archive"]) == _contents(rcpt.captured_input_archive)
        _, reference = _save(client, source, name="native")
        assert (
            _pair(reference, root)["captured_input_archive"].graph_bytes
            == rcpt.captured_input_archive.graph_bytes
        )
        assert counter.read_bytes() == b"run\n"
    finally:
        if not session.owned.finished:
            session.cancel_owned()
