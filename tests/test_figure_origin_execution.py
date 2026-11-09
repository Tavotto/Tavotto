"""Real application consumers must edit the execution named by a saved origin.

Archive readability is deliberately a separate subject from permission to edit
the current Figure. The scientific program runs in the existing user worker;
the Flask test process never imports Matplotlib.
"""

from __future__ import annotations

import base64
import copy
import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

import test_export_svg as svg_cases
from support import desktopkit, nativekit
from tavotto import app as m
from tavotto.engine import (
    desktopproc,
    execspec,
    executionfact,
    figcapture,
    nativesession,
    pool,
    svgstore,
    workdir,
    workerd_client,
)

BACKENDS = ("python", "workerd", "native")
STEM = "F"


@pytest.fixture
def project(tmp_path, monkeypatch):
    worker_python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "preview")
    for client, root in svg_cases.project.__wrapped__(tmp_path, monkeypatch):
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_python)
        workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
        yield client, root, tmp_path / "actual-executions.txt", worker_python


def _script(counter, *, native=False):
    program = f"""from pathlib import Path
import matplotlib.pyplot as plt

def main():
    with open({str(counter)!r}, "ab") as count:
        count.write(b"run\\n")
    fig, ax = plt.subplots(figsize=(2, 1))
    ax.plot([0, 1], [0.5, 0.5], color="#b00000", linewidth=2)
    ax.set_title("Origin execution")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.savefig("F.pdf")
"""
    if native:
        program += "\nmain()\nplt.show()\nplt.show()\n"
    return program


def _json_ok(response):
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _field(manifest, gid, prop):
    return next(
        field["value"]
        for element in manifest["elements"]
        if element["gid"] == gid
        for field in element["editable"]
        if field["prop"] == prop
    )


def _pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        return image.size, image.convert("RGBA").tobytes()


def _render(world, patches, *, origin=True):
    body = {"id": world.source, "patches": patches, "inline_svg": True}
    if origin:
        body["figure_origin"] = world.origin
    return world.client.post("/api/engine/render", json=body)


def _preview(world, patches, *, manifest=False):
    return world.client.post(
        "/api/engine/preview_png",
        json={
            "id": world.source,
            "patches": patches,
            "figure_origin": world.origin,
            "w": 400,
            "with_manifest": manifest,
        },
    )


def _export(world, patches, *, scope="original"):
    value = svg_cases.spec(world.source, scope=scope, patches=patches, formats=("pdf", "svg"))
    value["filename"] = "origin-" + scope
    if scope == "original":
        value["original"]["figure_origin"] = world.origin
    else:
        value["canvas"]["objects"][0]["figure_origin"] = world.origin
    return world.client.post("/api/export", json=value)


@contextmanager
def _started(project, monkeypatch, backend="python"):
    client, root, counter, worker_python = project
    session = None
    if backend == "workerd":
        binary = Path(__file__).resolve().parents[1] / "workerd/target/debug/tavotto-workerd"
        if not binary.is_file():
            binary = binary.with_suffix(".exe")
        if not binary.is_file():
            pytest.skip("Workerd binary is unavailable; build workerd first")
        monkeypatch.setenv("TAVOTTO_WORKERD", str(binary))
        workerd_client.reset_client()
    else:
        monkeypatch.setenv("TAVOTTO_WORKERD", "0")
    (root / "plot.py").write_text(_script(counter, native=backend == "native"), encoding="utf-8")
    try:
        if backend == "native":
            spec = execspec.desktop_native_spec(
                str(root / "plot.py"),
                interpreter=worker_python,
                cwd=str(root),
                project_root=str(root),
            )
            nativesession.REGISTRY.reopen()
            session = desktopproc.start(desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST))
            nativekit.wait_state(session, [nativesession.BARRIER])
            built = _json_ok(client.post(f"/api/native/sessions/{session.session_id}/build"))
            assert built["ok"] and not built.get("conflicts"), built
            worker = session
        else:
            ran = _json_ok(client.post("/api/registry/probe", json={"script": "plot.py"}))
            assert ran["registered"], ran
            worker = pool.peek("plot.py", str(root))
            assert isinstance(
                worker, pool.WorkerdWorker if backend == "workerd" else pool.EngineWorker
            )
        assert worker is not None and worker.built
        source = figcapture.runtime_asset_id("plot.py", STEM)
        world = SimpleNamespace(
            client=client, root=root, counter=counter, worker=worker, source=source, backend=backend
        )
        first = _json_ok(_render(world, [], origin=False))
        world.gid = next(
            el["gid"]
            for el in first["manifest"]["elements"]
            if ".lines_" in el["gid"]
            and any(field["prop"] == "linewidth" for field in el.get("editable", []))
        )
        # The origin retains array order and duplicates, unlike its canonical hash.
        world.p = [
            {"gid": world.gid, "prop": "linewidth", "value": 1.5},
            {"gid": world.gid, "prop": "linewidth", "value": 3},
        ]
        world.q = [{"gid": world.gid, "prop": "linewidth", "value": 7}]
        world.temporary = [{"gid": world.gid, "prop": "linewidth", "value": 4}]
        assert (
            _field(
                _json_ok(_render(world, world.p, origin=False))["manifest"], world.gid, "linewidth"
            )
            == 3
        )
        saved = _json_ok(
            client.post(
                "/api/layouts/origin?target=project", json=svg_cases.document(source, world.p)
            )
        )
        assert not saved.get("svg_snapshot_warnings"), saved
        reference = saved["figure_snapshots"]["panel"]
        archived = svgstore.inspect_portable_snapshot(
            reference, storage_root=m._svg_storage_root(root)
        )
        world.origin = {"version": 1, "snapshot": reference, "patches": copy.deepcopy(world.p)}
        world.receipt = m._execution_receipt(worker, "plot.py")
        world.eid = worker.execution_id
        assert archived["receipt"]["receipt_id"] == world.receipt.receipt_id
        assert counter.read_bytes() == b"run\n"
        yield world
    finally:
        if session is not None and not session.owned.finished:
            session.cancel_owned()
        pool.shutdown_all(figures_dir=str(root), wait=True)
        if backend == "workerd":
            workerd_client.reset_client()


def _forbid_execution(world, monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("A saved origin must not build, reopen or execute another worker")

    monkeypatch.setattr(pool, "get", deny)
    monkeypatch.setattr(pool, "one_shot", deny)
    monkeypatch.setattr(world.worker, "ensure_built", deny)


def _assert_rejected(response, *, code="svg_source_unavailable", status=409):
    assert response.status_code == status, response.get_json()
    body = response.get_json()
    assert body["code"] == code, body
    assert not any(key in body for key in ("svg", "png", "manifest", "figure_origin")), body


def _assert_export_rejected(world):
    response = _export(world, world.q)
    assert response.status_code == 500, response.get_json()
    body = response.get_json()
    assert body["status"] == "failed", body
    # Admission fails before a producer returns any formats. This is the
    # original job-level failure contract, not a per-format conversion failure.
    assert body["error"]["code"] == "svg_source_unavailable", body
    assert body["outputs"] == [], body
    assert body.get("files", []) == []
    assert not any(path.is_file() for path in Path(body["export_dir"]).glob("*"))


def _check_pair(body, world, scope):
    assert body["status"] == "done", body
    outputs = {item["format"]: item for item in body["outputs"]}
    assert set(outputs) == {"pdf", "svg"}
    for item in outputs.values():
        assert item["status"] == "done", body
        assert (Path(body["export_dir"]) / item["name"]).is_file()
    svg = (Path(body["export_dir"]) / outputs["svg"]["name"]).read_bytes()
    tree = ET.fromstring(svg)
    assert tree.tag == "{http://www.w3.org/2000/svg}svg"
    if scope == "original":
        node = next(node for node in tree.iter() if node.get("id") == world.gid)
        assert any("stroke-width: 4" in node.get("style", "") for node in node.iter())
    else:
        assert any(
            "stroke-width: 4" in node.get("style", "") or node.get("stroke-width") == "4"
            for node in tree.iter()
        )
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(Path(body["export_dir"]) / outputs["pdf"]["name"]) as pdf:
        assert len(pdf) == 1
        page = pdf[0]
        try:
            assert page.get_size() == pytest.approx(
                (144, 72) if scope == "original" else (120 * 72 / 25.4, 90 * 72 / 25.4)
            )
            bitmap = page.render(scale=2)
            try:
                image = bitmap.to_pil().convert("RGB")
                assert any(r > 80 and r > g * 1.5 and r > b * 1.5 for r, g, b in image.getdata())
            finally:
                bitmap.close()
            textpage = page.get_textpage()
            try:
                assert "Origin execution" in textpage.get_text_range()
            finally:
                textpage.close()
        finally:
            page.close()


@pytest.mark.parametrize("backend", BACKENDS)
def test_current_origin_render_png_and_both_exports_share_one_real_execution(
    project, monkeypatch, backend
):
    with _started(project, monkeypatch, backend) as world:
        _forbid_execution(world, monkeypatch)
        rendered = _json_ok(_render(world, world.q))
        assert rendered["figure_origin"] == world.origin
        assert _field(rendered["manifest"], world.gid, "linewidth") == 7
        assert "svg" in rendered
        hot = _pixels(world.worker.render_png(STEM, 400).read_bytes())
        paired = _json_ok(_preview(world, world.temporary, manifest=True))
        assert paired["figure_origin"] == world.origin
        assert _field(paired["manifest"], world.gid, "linewidth") == 4
        pixels = _pixels(base64.b64decode(paired["png"], validate=True))
        plain = _preview(world, world.temporary)
        assert plain.status_code == 200 and plain.mimetype == "image/png"
        assert json.loads(plain.headers["X-Tavotto-Figure-Origin"]) == world.origin
        assert plain.headers["Cache-Control"] == "no-store"
        assert _pixels(plain.data) == pixels
        assert pixels != hot
        assert _pixels(world.worker.render_png(STEM, 400).read_bytes()) == hot
        for scope in ("original", "canvas"):
            _check_pair(_json_ok(_export(world, world.temporary, scope=scope)), world, scope)
            assert _pixels(world.worker.render_png(STEM, 400).read_bytes()) == hot
        assert world.worker.execution_id == world.eid
        assert m._execution_receipt(world.worker, "plot.py").receipt_id == world.receipt.receipt_id
        assert world.counter.read_bytes() == b"run\n"


@pytest.mark.parametrize("operation", ("render", "png", "png_manifest"))
@pytest.mark.parametrize("fault", ("p_order", "unknown_key", "null"))
def test_malformed_origin_is_not_a_request_to_reconstruct(project, monkeypatch, operation, fault):
    with _started(project, monkeypatch) as world:
        _forbid_execution(world, monkeypatch)
        if fault == "p_order":
            world.origin["patches"].reverse()
        elif fault == "unknown_key":
            world.origin["approved"] = True
        else:
            world.origin = None
        response = (
            _render(world, world.q)
            if operation == "render"
            else _preview(world, world.q, manifest=operation == "png_manifest")
        )
        _assert_rejected(response, code="bad_request", status=400)
        assert world.counter.read_bytes() == b"run\n"


@pytest.mark.parametrize("fault", ("receipt", "snapshot", "owner", "execution_id"))
def test_origin_needs_the_complete_reference_and_current_execution(project, monkeypatch, fault):
    with _started(project, monkeypatch) as world:
        _forbid_execution(world, monkeypatch)
        if fault == "receipt":
            world.origin["snapshot"]["receiptId"] = "sha256:" + "0" * 64
        elif fault == "snapshot":
            world.origin["snapshot"]["snapshotId"] = "0" * 64
        elif fault == "owner":
            world.origin["snapshot"]["executionOwner"]["script"] = "other.py"
        else:
            monkeypatch.setattr(world.worker, "execution_id", executionfact.new_id())
        _assert_rejected(_render(world, world.q))
        _assert_rejected(_preview(world, world.q, manifest=True))
        _assert_export_rejected(world)
        assert world.counter.read_bytes() == b"run\n"


def test_cold_archive_is_viewable_but_does_not_authorize_reexecution(project, monkeypatch):
    with _started(project, monkeypatch) as world:
        pool.shutdown_all(figures_dir=str(world.root), wait=True)
        m.reset_projects()
        m.open_project(str(world.root))
        _forbid_execution(world, monkeypatch)
        preview = _json_ok(
            world.client.post(
                "/api/documents/v4/figure-preview",
                json={
                    "id": world.source,
                    "patches": world.p,
                    "figure_snapshot": world.origin["snapshot"],
                },
            )
        )
        assert preview["preview_source"] == "figure_snapshot"
        _assert_rejected(_render(world, world.q))
        _assert_rejected(_preview(world, world.q))
        _assert_export_rejected(world)
        assert world.counter.read_bytes() == b"run\n"


def test_explicit_new_safe_execution_retires_the_old_origin(project, monkeypatch):
    with _started(project, monkeypatch) as world:
        rerun = _json_ok(world.client.post("/api/registry/probe", json={"script": "plot.py"}))
        assert rerun["registered"]
        assert world.counter.read_bytes() == b"run\nrun\n"
        _forbid_execution(world, monkeypatch)
        _assert_rejected(_render(world, world.q))
        _assert_rejected(_preview(world, world.q, manifest=True))
        _assert_export_rejected(world)
        assert world.counter.read_bytes() == b"run\nrun\n"


def test_native_continue_new_barrier_cannot_reuse_the_previous_origin(project, monkeypatch):
    with _started(project, monkeypatch, "native") as world:
        epoch = world.worker.snapshot_epoch
        _json_ok(world.client.post(f"/api/native/sessions/{world.worker.session_id}/continue"))
        nativekit.wait_state(world.worker, [nativesession.BARRIER])
        assert world.worker.snapshot_epoch != epoch
        assert world.worker.execution_id != world.eid
        _json_ok(world.client.post(f"/api/native/sessions/{world.worker.session_id}/build"))
        _forbid_execution(world, monkeypatch)
        _assert_rejected(_render(world, world.q))
        _assert_rejected(_preview(world, world.q, manifest=True))
        _assert_export_rejected(world)
        assert world.counter.read_bytes() == b"run\n"


def test_origin_cannot_select_another_source_in_the_same_project(project, monkeypatch):
    with _started(project, monkeypatch) as world:
        _forbid_execution(world, monkeypatch)
        world.source = figcapture.runtime_asset_id("other.py", STEM)
        _assert_rejected(_render(world, world.q), code="bad_request", status=400)
        _assert_rejected(_preview(world, world.q), code="bad_request", status=400)
        response = _export(world, world.q)
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["code"] == "bad_request"
        assert world.counter.read_bytes() == b"run\n"


def test_origin_is_project_scoped_even_when_another_project_has_the_same_id(project, monkeypatch):
    with _started(project, monkeypatch) as world:
        other = world.root.with_name("other-project")
        other.mkdir()
        # Same scientific identity and script bytes cannot transfer the live
        # execution or another project's immutable archive to this project.
        (other / "plot.py").write_bytes((world.root / "plot.py").read_bytes())
        opened = m.open_project(str(other), make_default=False)
        _forbid_execution(world, monkeypatch)
        response = world.client.post(
            "/api/engine/render",
            query_string={"pj": opened["id"]},
            json={"id": world.source, "patches": world.q, "figure_origin": world.origin},
        )
        _assert_rejected(response)
        assert world.counter.read_bytes() == b"run\n"
        assert not (other / "F.pdf").exists()
