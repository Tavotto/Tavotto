"""Desktop SVG pipeline: real capture, snapshot reopen, partial and safe publication."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import exportjob, figcapture, pool, project_watch

SCRIPT = """import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_title("Source title")
    fig.savefig("F.pdf")
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    m.reset_projects()
    m.app.config["TESTING"] = True
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "plot.py").write_text(SCRIPT)
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "plot.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["F"]}
                },
            }
        )
    )
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield m.app.test_client(), root
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    project_watch.stop()
    exportjob.reset_for_tests()


def spec(source, *, scope="canvas", formats=("svg",), patches=None, strict=False):
    common = {
        "scope": scope,
        "filename": "result",
        "formats": list(formats),
        "overwrite": "replace",
    }
    if strict:
        common["inspection"] = {"mode": "strict"}
    if scope == "original":
        common["original"] = {
            "figure_id": source,
            "overrides": patches or [],
            "source_kind": "vector",
        }
    else:
        common["canvas"] = {
            "page_w_mm": 120,
            "page_h_mm": 90,
            "objects": [
                {
                    "type": "panel",
                    "id": source,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 76.2,
                    "h_mm": 50.8,
                    "overrides": patches or [],
                },
                {
                    "type": "text",
                    "id": "caption",
                    "text": "标题 E=mc^{2}",
                    "x_mm": 5,
                    "y_mm": 65,
                    "w_mm": 100,
                    "h_mm": 10,
                    "size_pt": 12,
                },
                {
                    "type": "arrow",
                    "id": "arrow",
                    "x_mm": 80,
                    "y_mm": 20,
                    "w_mm": 15,
                    "h_mm": 10,
                    "start": {"rx": 0, "ry": 0},
                    "end": {"rx": 1, "ry": 1},
                },
            ],
        }
    return common


def output(client, value):
    response = client.post("/api/export", json=value)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    return body, {o["format"]: o for o in body["outputs"]}


def assert_svg(body, item):
    assert item["status"] == "done", body
    path = Path(body["export_dir"]) / item["name"]
    data = path.read_bytes()
    assert not data.startswith(b"\x89PNG")
    root = ET.fromstring(data)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert not list(root.iter("{http://www.w3.org/2000/svg}text"))
    assert list(root.iter("{http://www.w3.org/2000/svg}path"))
    return data


def run_script(client):
    response = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json().get("registered"), response.get_json()
    return figcapture.runtime_asset_id("plot.py", "F")


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_first_script_run_without_edits_and_cold_reopen(project, monkeypatch, scope):
    client, root = project
    source = run_script(client)
    # Initial run captured an export-grade pair before any export was requested.
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("SVG export reran the script"))
    body, outputs = output(client, spec(source, scope=scope, formats=("svg", "pdf")))
    assert body["status"] == "done", body
    assert_svg(body, outputs["svg"])
    assert outputs["pdf"]["status"] == "done"
    body, outputs = output(client, spec(source, scope=scope, strict=True))
    assert_svg(body, outputs["svg"])


def test_opaque_pdf_failure_is_per_format_and_names_source(project):
    import pikepdf

    client, root = project
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(root / "external.pdf")
    for scope in ("original", "canvas"):
        body, outputs = output(client, spec("external.pdf", scope=scope, formats=("pdf", "svg")))
        assert body["status"] == "partial", body
        assert outputs["pdf"]["status"] == "done"
        assert outputs["svg"]["status"] == "failed"
        assert "external.pdf" in json.dumps(outputs["svg"]["error"])


def test_svg_companion_safety_failure_preserves_pdf(project, monkeypatch):
    client, root = project
    source = run_script(client)
    rendered = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert rendered.status_code == 200, rendered.get_json()
    real = m._normalize_worker_svg

    def reject(path, expected=None):
        raise ValueError("unsafe SVG fixture")

    monkeypatch.setattr(m, "_normalize_worker_svg", reject)
    body, outputs = output(client, spec(source, formats=("pdf", "svg")))
    assert body["status"] == "partial", body
    assert outputs["pdf"]["status"] == "done"
    assert outputs["svg"]["status"] == "failed"
    monkeypatch.setattr(m, "_normalize_worker_svg", real)


def document(source, patches):
    return {
        "schema": 3,
        "project": {"id": "p", "name": "Saved figure"},
        "canvases": [
            {
                "id": "c",
                "name": "Figure 1",
                "page": {"w": 120, "h": 90},
                "objects": [
                    {"id": "panel", "type": "panel", "fileId": source, "overrides": patches}
                ],
                "guides": [],
            }
        ],
        "activeCanvasId": "c",
        "createdAt": 0,
        "updatedAt": 1,
    }


def test_edited_save_before_first_export_survives_project_move_and_cache_clear(
    project, monkeypatch
):
    import shutil

    client, root = project
    source = run_script(client)
    response = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert response.status_code == 200, response.get_json()
    manifest = response.get_json()["manifest"]
    gid = next(
        el["gid"]
        for el in manifest["elements"]
        for prop in el.get("editable", [])
        if prop["prop"] == "text" and prop.get("value") == "Source title"
    )
    patches = [{"gid": gid, "prop": "text", "value": "Saved edited title"}]
    response = client.post("/api/engine/render", json={"id": source, "patches": patches})
    assert response.status_code == 200, response.get_json()
    saved = client.post("/api/layouts/edited?target=project", json=document(source, patches))
    assert saved.status_code == 200, saved.get_json()
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    moved = root.with_name("moved-project")
    shutil.copytree(root, moved)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(root.parent / "fresh-data"))
    m.open_project(str(moved))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("saved SVG reran script"))
    body, outputs = output(client, spec(source, patches=patches, strict=True))
    assert_svg(body, outputs["svg"])
    assert body["status"] == "done", body
    # Also the unedited variant is export-grade, independent of the data cache.
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"])


def test_snapshot_staging_failure_does_not_fail_document_save(project, monkeypatch):
    client, root = project
    source = run_script(client)
    monkeypatch.setattr(
        m, "_svg_storage_root", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    response = client.post("/api/layouts/safe?target=project", json=document(source, []))
    assert response.status_code == 200, response.get_json()
    assert (root / "tavottofile" / "safe.json").is_file()
    assert response.get_json()["svg_snapshot_warnings"] == [source]


def test_matching_disk_pdf_gets_same_figure_svg_without_reexecution(project, monkeypatch):
    import os
    import subprocess

    client, root = project
    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('plot.py')['main']()"],
        cwd=root,
        env={**os.environ, "MPLBACKEND": "agg"},
        check=True,
    )
    run_script(client)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("disk SVG reran script"))
    for scope in ("original", "canvas"):
        body, outputs = output(client, spec("F.pdf", scope=scope, formats=("pdf", "svg")))
        assert body["status"] == "done", body
        assert_svg(body, outputs["svg"])
    # Nearby Python does not qualify changed bytes for the old representation.
    import pikepdf

    changed = pikepdf.new()
    changed.add_blank_page(page_size=(216, 144))
    changed.save(root / "F.pdf")
    body, outputs = output(client, spec("F.pdf", scope="original", formats=("pdf", "svg")))
    assert body["status"] == "partial", body
    assert outputs["svg"]["status"] == "failed"


def test_svg_with_eps_does_not_start_a_cold_worker(project, monkeypatch):
    client, root = project
    source = run_script(client)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("SVG+EPS reran script"))
    body, outputs = output(client, spec(source, scope="original", formats=("svg", "eps")))
    assert_svg(body, outputs["svg"])
    assert outputs["eps"]["status"] == "failed"


def test_project_package_contains_only_referenced_portable_snapshots(project):
    import zipfile

    client, root = project
    source = run_script(client)
    response = client.post("/api/package", json={"doc": document(source, []), "stem": "snapshot"})
    assert response.status_code == 200, response.get_json()
    target = root / "tavottofile" / "export" / response.get_json()["name"]
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("package_manifest.json"))
        assert manifest["figure_snapshots"] == 1
        names = archive.namelist()
        prefix = "assets/tavottofile/figure_snapshots/"
        assert any(name.startswith(prefix + "blobs/") and name.endswith(".svg") for name in names)
        records = [
            json.loads(archive.read(name))["payload"]
            for name in names
            if name.startswith(prefix + "entries/")
        ]
        assert len(records) == 1
        assert records[0]["identity"]["project"] == "."
        assert records[0]["identity"]["source_id"] == source


def test_single_original_svg_supports_live_custom_formatter_without_pairing(project):
    client, root = project
    (root / "plot.py").write_text(
        SCRIPT.replace(
            '    ax.set_title("Source title")',
            "    from matplotlib.ticker import FuncFormatter\n"
            '    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:.1f} units"))\n'
            '    ax.set_title("Source title")',
        )
    )
    source = run_script(client)
    response = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert response.status_code == 200, response.get_json()
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"])
    # Cross-backend pairing has a stronger contract and explicitly declines it.
    body, outputs = output(client, spec(source, scope="original", formats=("pdf", "svg")))
    assert body["status"] == "partial", body
    assert outputs["pdf"]["status"] == "done"
    assert outputs["svg"]["status"] == "failed"


def test_expired_live_handle_uses_verified_snapshot_without_reopening(project, monkeypatch):
    client, root = project
    source = run_script(client)
    response = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert response.status_code == 200, response.get_json()
    worker, _ = m._engine_worker(source, existing_only=True)

    def unavailable(*args, **kwargs):
        assert kwargs.get("require_built") is True
        raise pool.WorkerError("expired Figure", code="svg_source_unavailable")

    monkeypatch.setattr(worker, "export", unavailable)
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("expired SVG restarted a worker"))
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"])


def test_cancel_after_svg_written_never_publishes_it(project, monkeypatch):
    import threading
    import time

    client, root = project
    source = run_script(client)
    gate, written = threading.Event(), threading.Event()
    real = m._export_produce

    def paused(job, directory, **kwargs):
        produced = real(job, directory, **kwargs)
        assert any(item.format == "svg" and item.tmp_path.is_file() for item in produced)
        written.set()
        assert gate.wait(10)
        return produced

    monkeypatch.setattr(m, "_export_produce", paused)
    start = client.post("/api/export/start", json=spec(source, scope="original"))
    assert start.status_code == 200, start.get_json()
    job_id = start.get_json()["job_id"]
    assert written.wait(10)
    assert client.post("/api/export/cancel", json={"job_id": job_id}).get_json()["cancelling"]
    gate.set()
    for _ in range(200):
        result = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if result["status"] == "cancelled":
            break
        time.sleep(0.01)
    assert result["status"] == "cancelled", result
    target = Path(result["export_dir"])
    assert not list(target.glob("*.svg"))
    assert not list(target.glob(exportjob.TMP_PREFIX + "*"))
