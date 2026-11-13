"""Explicit document versions select one complete archived PDF/SVG pair.

The pair is independently readable real vector artwork, not a scientific
execution fixture. Real Capture produces its input closure; the last test
performs a genuine new worker execution to exercise application revocation.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pikepdf
import pytest

import test_svgstore_input_archive as input_archive_fixtures
from tavotto import app as m
from tavotto.engine import (
    declaredinputs,
    documentpackage,
    inputremap,
    patchspec,
    pool,
    project_watch,
    svgstore,
)
from tavotto.rendercore import renderhost
from test_svgstore import _args
from test_svgstore_input_archive import _capture

input_world = input_archive_fixtures.world

SOURCE = "历史图.pdf"
PATCH = patchspec.patch_hash([])
OWNER = {"script": "plot.py", "run_config": None}
SIZES = ((120, 80), (240, 100))
COLORS = ((255, 0, 0), (0, 0, 255))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pdf(size, color):
    with pikepdf.new() as document:
        page = document.add_blank_page(page_size=size)
        rgb = " ".join(str(value / 255) for value in color)
        page.Contents = document.make_stream(
            f"{rgb} rg 0 0 {size[0]} {size[1]} re f\n".encode("ascii")
        )
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()


def _svg(size, color):
    fill = "#" + "".join(f"{value:02x}" for value in color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size[0]}pt" '
        f'height="{size[1]}pt" viewBox="0 0 {size[0]} {size[1]}">'
        f'<rect width="{size[0]}" height="{size[1]}" fill="{fill}"/></svg>'
    ).encode("ascii")


@pytest.fixture
def case(input_world, monkeypatch):
    root, _store, original, candidate, _cache = input_world
    monkeypatch.setitem(m.app.config, "TESTING", True)
    monkeypatch.setenv("MPLCONFIGDIR", str(root.parent / "mpl"))
    monkeypatch.setattr(pool, "ENGINE_CACHE", root.parent / "engine")
    monkeypatch.setattr(m, "CACHE_DIR", root.parent / "preview")
    m.reset_projects(wait=True)
    # The lower-level Capture fixture permits opaque IDs; formal packages use
    # the parent declaration authority's portable remap identity.
    prefix = candidate["prefixes"][0]
    identity = declaredinputs._remap_identity(root, prefix["path"], candidate["rules"][0])
    prefix["id"] = candidate["mappings"][0]["id"] = identity
    fact, archive = _capture(input_world)
    target = root / "selected.csv"
    target.write_bytes(b"owned target\n")
    rule = {"kind": "file", "from": "legacy/value.csv", "to": str(target)}
    inputremap.add_rule(root, rule)
    fingerprint = inputremap.fingerprint(root)
    assert fingerprint
    store = m._svg_storage_root(root)
    store.parent.mkdir(parents=True, exist_ok=True)
    pdfs, svgs, references = [], [], []
    for index, (size, color) in enumerate(zip(SIZES, COLORS, strict=True)):
        pdf, svg = _pdf(size, color), _svg(size, color)
        arguments = _args(source=SOURCE, patch=PATCH, pdf=pdf, svg=svg, generation=index + 1)
        for field in ("receipt_id", "receipt_identity"):
            value = "sha256:" + _sha(f"historical-{field}-{index}".encode())
            arguments["receipt"][field] = arguments["artifact"][field] = value
        arguments["receipt"]["captured_inputs"] = {"version": 1, "revision": fact["revision"]}
        saved = svgstore.save(
            root,
            SOURCE,
            PATCH,
            **arguments,
            execution_owner=OWNER,
            static_sha256=_sha(pdf),
            remap_fingerprint=fingerprint,
            storage_root=store,
            captured_input_archive=archive,
            activate=True,
        )
        references.append(saved["snapshot_ref"])
        pdfs.append(pdf)
        svgs.append(svg)
    (root / SOURCE).write_bytes(pdfs[1])
    opened = m.open_project(str(root))
    client = m.app.test_client()
    value = SimpleNamespace(
        root=root,
        store=store,
        original=original,
        client=client,
        pid=opened["id"],
        export_dir=Path(opened["export_dir"]),
        references=references,
        pdfs=pdfs,
        svgs=svgs,
        archive=archive,
        input_revision=fact["revision"],
        fingerprint=fingerprint,
        rule=rule,
    )
    try:
        yield value
    finally:
        pool.shutdown_all(figures_dir=str(root), wait=True)
        m.reset_projects(wait=True)
        project_watch.stop()
        renderhost.shutdown_shared()


def _panel(reference, *, index=0):
    return {
        "id": f"panel-{index}",
        "type": "panel",
        "fileId": SOURCE,
        "fileKind": "pdf",
        "nativeW": SIZES[index][0] * 25.4 / 72,
        "nativeH": SIZES[index][1] * 25.4 / 72,
        "x": 5 + index * 60,
        "y": 5,
        "w": 40,
        "h": 30,
        "overrides": [],
        "figureSnapshot": copy.deepcopy(reference),
    }


def _document(case):
    return {
        "schema": 4,
        "project": {"id": "historical", "name": "同名历史版本"},
        "canvases": [
            {
                "id": "c1",
                "name": "History",
                "page": {"w": 120, "h": 90},
                "objects": [_panel(ref, index=index) for index, ref in enumerate(case.references)],
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 1,
        "updatedAt": 2,
    }


def _request(scope, *, reference=None, source=SOURCE, edits=()):
    result = {
        "scope": scope,
        "filename": "history",
        "formats": ["pdf", "svg"],
        "overwrite": "replace",
    }
    member = {"overrides": list(edits)}
    if reference is not None:
        member["figure_snapshot"] = copy.deepcopy(reference)
    if scope == "original":
        result["original"] = {"figure_id": source, "source_kind": "vector", **member}
    else:
        result["canvas"] = {
            "page_w_mm": 120,
            "page_h_mm": 90,
            "objects": [
                {
                    "type": "panel",
                    "id": source,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 30,
                    **member,
                }
            ],
        }
    return result


def _export(case, request):
    response = case.client.post("/api/export?pj=" + case.pid, json=request)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    outputs = {item["format"]: item for item in body["outputs"]}
    paths = {
        kind: Path(body["export_dir"]) / item["name"]
        for kind, item in outputs.items()
        if item["status"] == "done"
    }
    return body, outputs, paths


def _assert_snapshot_rejected(case, request):
    before = {
        path.relative_to(case.export_dir): _sha(path.read_bytes())
        for path in case.export_dir.rglob("*")
        if path.is_file()
    }
    response = case.client.post("/api/export?pj=" + case.pid, json=request)
    body = response.get_json()
    assert response.status_code >= 400, body
    assert body["status"] == "failed", body
    assert body["code"] == "svg_snapshot_missing", body
    assert not any(item["status"] == "done" for item in body.get("outputs", [])), body
    assert {
        path.relative_to(case.export_dir): _sha(path.read_bytes())
        for path in case.export_dir.rglob("*")
        if path.is_file()
    } == before


def _pdf_pixels(path):
    # An independent reader checks the product's actual HTTP output, not its
    # own manifest or the renderer that produced it.
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(str(path)) as document:
        assert len(document) == 1
        page = document[0]
        size = page.get_size()
        bitmap = page.render(scale=1)
        try:
            pixels = bitmap.to_pil().convert("RGB").copy()
        finally:
            bitmap.close()
            page.close()
    return size, pixels


def _pixel(pixels, x_pt, y_pt, expected):
    actual = pixels.getpixel((int(x_pt), int(y_pt)))
    assert actual == pytest.approx(expected, abs=2)


def _assert_pair(case, scope, variant, request=None):
    body, outputs, paths = _export(
        case, request or _request(scope, reference=case.references[variant])
    )
    assert body["status"] == "done", body
    assert set(paths) == {"pdf", "svg"}, outputs
    size, pixels = _pdf_pixels(paths["pdf"])
    if scope == "original":
        assert size == pytest.approx(SIZES[variant], abs=0.01)
        _pixel(pixels, size[0] / 2, size[1] / 2, COLORS[variant])
        assert paths["svg"].read_bytes() == case.svgs[variant]
    else:
        assert size == pytest.approx((120 * 72 / 25.4, 90 * 72 / 25.4), abs=0.01)
        _pixel(pixels, 25 * 72 / 25.4, 20 * 72 / 25.4, COLORS[variant])
        svg = ET.fromstring(paths["svg"].read_bytes())
        fills = {node.get("fill") for node in svg.iter()}
        assert ("#ff0000" if variant == 0 else "#0000ff") in fills
        assert ("#0000ff" if variant == 0 else "#ff0000") not in fills
    return body, outputs, paths


@pytest.mark.parametrize("scope", ["original", "canvas"])
@pytest.mark.parametrize("variant", [0, 1])
def test_explicit_static_history_exports_its_archived_pdf_and_svg_not_current_file(
    case, scope, variant
):
    assert (case.root / SOURCE).read_bytes() == case.pdfs[1]
    _assert_pair(case, scope, variant)


def test_same_canvas_keeps_two_versions_of_one_static_source(case):
    request = _request("canvas")
    objects = request["canvas"]["objects"]
    objects[0]["figure_snapshot"] = case.references[0]
    other = copy.deepcopy(objects[0])
    other.update(x_mm=65, figure_snapshot=case.references[1])
    objects.append(other)
    body, _outputs, paths = _export(case, request)
    assert body["status"] == "done", body
    size, pixels = _pdf_pixels(paths["pdf"])
    assert size == pytest.approx((120 * 72 / 25.4, 90 * 72 / 25.4), abs=0.01)
    _pixel(pixels, 25 * 72 / 25.4, 20 * 72 / 25.4, COLORS[0])
    _pixel(pixels, 85 * 72 / 25.4, 20 * 72 / 25.4, COLORS[1])
    fills = {node.get("fill") for node in ET.fromstring(paths["svg"].read_bytes()).iter()}
    assert {"#ff0000", "#0000ff"} <= fills


@pytest.mark.parametrize("format", ["pdf", "svg"])
def test_original_single_format_still_selects_the_explicit_history(case, format):
    request = _request("original", reference=case.references[0])
    request["formats"] = [format]
    body, _outputs, paths = _export(case, request)
    assert body["status"] == "done", body
    assert set(paths) == {format}
    if format == "pdf":
        size, pixels = _pdf_pixels(paths["pdf"])
        assert size == pytest.approx(SIZES[0], abs=0.01)
        _pixel(pixels, size[0] / 2, size[1] / 2, COLORS[0])
    else:
        assert paths["svg"].read_bytes() == case.svgs[0]


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_explicit_static_history_survives_deleted_current_original(case, scope):
    (case.root / SOURCE).unlink()
    _assert_pair(case, scope, 0)


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_no_reference_keeps_current_static_digest_gate(case, scope):
    _assert_pair(case, scope, 1, _request(scope))
    (case.root / SOURCE).write_bytes(_pdf((150, 110), (0, 255, 0)))
    body, outputs, paths = _export(case, _request(scope))
    assert outputs["svg"]["status"] == "failed", body
    if scope == "original":
        assert body["status"] == "partial", body
        size, pixels = _pdf_pixels(paths["pdf"])
        assert size == pytest.approx((150, 110), abs=0.01)
        _pixel(pixels, 75, 55, (0, 255, 0))


@pytest.mark.parametrize(
    "damage", ["source", "patch", "snapshot", "owner", "remap", "svg_blob", "input_blob"]
)
def test_explicit_history_rejects_wrong_binding_or_corrupted_bytes(case, damage):
    reference = copy.deepcopy(case.references[0])
    edits = []
    if damage == "source":
        reference["sourceId"] = "different.pdf"
    elif damage == "patch":
        edits = [{"gid": "axes_0.lines_0", "prop": "color", "value": "red"}]
    elif damage == "snapshot":
        reference["snapshotId"] = "f" * 64
    elif damage == "owner":
        reference["executionOwner"] = {"script": "other.py", "run_config": None}
    elif damage == "remap":
        target = case.root / "different.csv"
        target.write_bytes(b"different\n")
        inputremap.add_rule(case.root, {**case.rule, "to": str(target)})
        assert inputremap.fingerprint(case.root) != case.fingerprint
    else:
        if damage == "svg_blob":
            path = next(
                path
                for path in (case.store / "blobs").iterdir()
                if path.read_bytes() == case.svgs[0]
            )
        else:
            resource = next(row for row in case.archive.resources if row.size_bytes > 0)
            digest = resource.sha256
            path = case.store / "inputs/blobs" / digest[:2] / digest[2:]
        old = path.read_bytes()
        path.write_bytes(bytes([old[0] ^ 1]) + old[1:])
        assert len(path.read_bytes()) == len(old)
    _assert_snapshot_rejected(case, _request("original", reference=reference, edits=edits))


def test_actual_new_worker_execution_revokes_all_old_static_document_versions(case, monkeypatch):
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", pool.find_worker_python())
    counter = case.root.parent / "actual-new-run.log"
    (case.root / OWNER["script"]).write_text(
        "import matplotlib.pyplot as plt\ndef main():\n"
        f"    with open({str(counter)!r}, 'ab') as f: f.write(b'run\\n')\n"
        "    fig, ax=plt.subplots(); ax.plot([0,1],[9,8]); fig.savefig('fresh.pdf')\n",
        encoding="utf-8",
    )
    response = case.client.post(
        "/api/registry/probe?pj=" + case.pid, json={"script": OWNER["script"]}
    )
    assert response.status_code == 200 and response.get_json().get("registered"), (
        response.get_json()
    )
    assert counter.read_bytes() == b"run\n"
    for reference in case.references:
        _assert_snapshot_rejected(case, _request("original", reference=reference))
    assert counter.read_bytes() == b"run\n"


def test_eps_only_explicit_history_never_starts_a_worker_or_executes_source(case, monkeypatch):
    counter = case.root.parent / "must-not-run-for-eps"
    (case.root / OWNER["script"]).write_text(
        f"def main():\n    open({str(counter)!r}, 'wb').write(b'executed')\n",
        encoding="utf-8",
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("An explicit historical EPS request must not start a worker")

    monkeypatch.setattr(pool, "get", forbidden)
    monkeypatch.setattr(pool, "one_shot", forbidden)
    request = _request("original", reference=case.references[0])
    request["formats"] = ["eps"]
    before = set(case.export_dir.rglob("*"))
    response = case.client.post("/api/export?pj=" + case.pid, json=request)
    body = response.get_json()
    assert response.status_code == 500, body  # Existing all-formats-failed contract.
    assert body["status"] == "failed", body
    assert body["code"] == "eps_needs_script", body
    outputs = {item["format"]: item for item in body["outputs"]}
    assert set(outputs) == {"eps"}, body
    assert outputs["eps"]["status"] == "failed", body
    assert outputs["eps"]["error"]["code"] == "eps_needs_script", body
    assert set(case.export_dir.rglob("*")) == before
    assert not counter.exists()


@pytest.mark.parametrize("field", ["receiptId", "executionOwner"])
def test_formal_package_does_not_reuse_first_reference_to_authorize_a_corrupted_second_ref(
    case, field
):
    document = _document(case)
    objects = document["canvases"][0]["objects"]
    objects[1]["figureSnapshot"] = copy.deepcopy(case.references[0])
    second = objects[1]["figureSnapshot"]
    second[field] = (
        "sha256:" + "e" * 64 if field == "receiptId" else {"script": "other.py", "run_config": None}
    )
    assert second["snapshotId"] == objects[0]["figureSnapshot"]["snapshotId"]
    before = set(case.export_dir.rglob("*.tavotto"))
    response = case.client.post(
        "/api/documents/v4/package?pj=" + case.pid,
        json={"stem": "invalid-second-ref", "doc": document},
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "figure_snapshot_unavailable", response.get_json()
    assert set(case.export_dir.rglob("*.tavotto")) == before


def test_formal_v4_save_and_package_keep_history_and_current_file_as_separate_resources(case):
    document = _document(case)
    saved = case.client.post(
        "/api/documents/v4/layouts/histories?target=project&base_revision=absent&pj=" + case.pid,
        json=document,
    )
    assert saved.status_code == 200, saved.get_json()
    assert not saved.get_json().get("svg_snapshot_warnings"), saved.get_json()
    stored = case.client.get("/api/documents/v4/layouts/histories?pj=" + case.pid)
    assert stored.status_code == 200, stored.get_json()
    document = stored.get_json()
    assert [
        item["figureSnapshot"] for item in document["canvases"][0]["objects"]
    ] == case.references
    response = case.client.post(
        "/api/documents/v4/package?pj=" + case.pid, json={"stem": "two-history", "doc": document}
    )
    assert response.status_code == 200, response.get_json()
    path = case.export_dir / response.get_json()["name"]
    with zipfile.ZipFile(path) as package:
        assert package.testzip() is None
        members = {name: package.read(name) for name in package.namelist()}
    manifest = json.loads(members[documentpackage.MANIFEST_MEMBER])
    ordinary = next(
        row for row in manifest["assets"] if row["kind"] == "file" and row["id"] == SOURCE
    )
    assert members[ordinary["path"]] == case.pdfs[1]
    assert ordinary["sha256"] == _sha(case.pdfs[1])
    execution = manifest["execution"]
    assert execution["version"] == 2
    assert {json.dumps(row["snapshot_ref"], sort_keys=True) for row in execution["captures"]} == {
        json.dumps(ref, sort_keys=True) for ref in case.references
    }
    assert {row["revision"] for row in execution["captures"]} == {case.input_revision}
    prefix = "assets/" + case.store.relative_to(case.root).as_posix() + "/"
    for index, ref in enumerate(case.references):
        payload = json.loads(members[prefix + "snapshots/" + ref["snapshotId"] + ".json"])[
            "payload"
        ]
        assert payload["identity"]["static_sha256"] == _sha(case.pdfs[index])
        for kind, expected in (("pdf", case.pdfs[index]), ("svg", case.svgs[index])):
            artifact = payload[kind + "_artifact"]
            assert members[prefix + "blobs/" + artifact["bytes_sha256"] + "." + kind] == expected
    endpoint = "/api/documents/v4/package/inspect?pj=" + case.pid
    inspection = case.client.post(
        endpoint, data={"package": (io.BytesIO(path.read_bytes()), "histories.tavotto")}
    )
    assert inspection.status_code == 200, inspection.get_json()
    assert inspection.get_json()["missing"] == []
    assert len(inspection.get_json()["captured_inputs"]) == 2
    changed = dict(members)
    original = changed[ordinary["path"]]
    changed[ordinary["path"]] = bytes([original[0] ^ 1]) + original[1:]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, raw in changed.items():
            package.writestr(name, raw)
    rejected = case.client.post(
        endpoint, data={"package": (io.BytesIO(bad.getvalue()), "tampered.tavotto")}
    )
    assert rejected.status_code == 422, rejected.get_json()
    assert rejected.get_json()["code"] == "package_invalid"
    assert rejected.get_json()["params"]["reason"] == "resource_mismatch"
