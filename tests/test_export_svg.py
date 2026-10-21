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
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "plot.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["F"]}
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield m.app.test_client(), root
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    project_watch.stop()
    exportjob.reset_for_tests()


def spec(source, *, scope="canvas", formats=("svg",), patches=None, strict=False, reference=None):
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
    if reference is not None:
        if scope == "original":
            common["original"]["figure_snapshot"] = reference
        else:
            common["canvas"]["objects"][0]["figure_snapshot"] = reference
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
def test_first_script_run_without_edits_and_saved_cold_reopen(project, monkeypatch, scope):
    client, root = project
    source = run_script(client)
    body, outputs = output(client, spec(source, scope=scope, strict=True))
    assert_svg(body, outputs["svg"])
    saved = client.post("/api/layouts/first?target=project", json=document(source, []))
    assert saved.status_code == 200, saved.get_json()
    reference = saved.get_json()["figure_snapshots"]["panel"]
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("SVG export reran the script"))
    body, outputs = output(
        client, spec(source, scope=scope, formats=("svg", "pdf"), reference=reference)
    )
    assert body["status"] == "done", body
    assert_svg(body, outputs["svg"])
    assert outputs["pdf"]["status"] == "done"
    body, outputs = output(client, spec(source, scope=scope, strict=True, reference=reference))
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


def test_raster_original_has_no_svg_and_does_not_ask_for_a_script_rerun(project):
    import struct
    import zlib

    client, root = project

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )
    (root / "scan.png").write_bytes(png)
    value = spec("scan.png", scope="original", formats=("svg",))
    value["original"]["source_kind"] = "raster"
    rejected = client.post("/api/export", json=value)
    # 位图没有脚本可重跑：中性的 svg_source_unsupported，不是 svg_snapshot_missing
    assert rejected.status_code >= 400, rejected.get_json()
    assert rejected.get_json()["code"] == "svg_source_unsupported", rejected.get_json()


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
    edited_ref = saved.get_json()["figure_snapshots"]["panel"]
    saved_base = client.post("/api/layouts/base?target=project", json=document(source, []))
    assert saved_base.status_code == 200, saved_base.get_json()
    base_ref = saved_base.get_json()["figure_snapshots"]["panel"]
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    moved = root.with_name("moved-project")
    shutil.copytree(root, moved)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(root.parent / "fresh-data"))
    m.open_project(str(moved))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("saved SVG reran script"))
    body, outputs = output(client, spec(source, patches=patches, strict=True, reference=edited_ref))
    assert_svg(body, outputs["svg"])
    assert body["status"] == "done", body
    # Also the unedited variant is export-grade, independent of the data cache.
    body, outputs = output(client, spec(source, scope="original", strict=True, reference=base_ref))
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


SVG_ASSET = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="48" viewBox="0 0 96 48">'
    b'<path d="M0 0L96 48" stroke="red"/></svg>'
)


def _scripted_svg_asset(client, root):
    """有脚本（stem F）的 SVG 素材 `F.svg`，脚本已运行、活 Figure 在 worker 里。"""
    (root / "F.svg").write_bytes(SVG_ASSET)
    run_script(client)
    return "F.svg"


def test_svg_asset_svg_and_eps_never_come_from_two_bases(project, monkeypatch):
    """#1185：SVG = 文件字节；EPS 没有字节绑定的证明就逐项失败，且**绝不**为它再跑一次脚本。"""
    client, root = project
    figure = _scripted_svg_asset(client, root)
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec(figure, scope="original", formats=("svg", "eps")))
    assert body["status"] == "partial", body
    assert (Path(body["export_dir"]) / outputs["svg"]["name"]).read_bytes() == SVG_ASSET
    assert outputs["eps"]["status"] == "failed"
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"
    assert outputs["eps"]["error"]["params"] == {"figure": "F.svg"}


def test_svg_asset_without_script_keeps_the_no_script_reason(project):
    client, root = project
    (root / "G.svg").write_bytes(SVG_ASSET)
    body, outputs = output(client, spec("G.svg", scope="original", formats=("svg", "eps")))
    assert outputs["eps"]["error"]["code"] == "eps_needs_script"


def _bind(monkeypatch, sha):
    """绑定桩：返回给定 sha、回执 id 取此刻活 Figure 的（冷 worker 则没有）。"""
    from tavotto.rendercore import sources as rc_sources

    def load(rel_id, overrides, out_dir, *, static_source=None, reference=None):
        worker, stem = m._engine_worker(rel_id, existing_only=True)
        receipt = {}
        if worker is not None:
            script = str(m.current_registry().for_stem(stem)["script"])
            receipt = {"receipt_id": m._execution_receipt(worker, script).receipt_id}
        else:
            receipt = {"receipt_id": "cold"}
        return rc_sources.FrozenSource(
            static_source.artifact, static_source.path, receipt, None, svg_source_sha256=sha
        )

    monkeypatch.setattr(m, "_load_svg_snapshot", load)


def test_svg_asset_eps_is_delivered_only_with_a_byte_bound_live_figure(project, monkeypatch):
    import hashlib

    client, root = project
    figure = _scripted_svg_asset(client, root)
    _bind(monkeypatch, hashlib.sha256(SVG_ASSET).hexdigest())
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec(figure, scope="original", formats=("svg", "eps")))
    assert body["status"] == "done", body
    assert outputs["eps"]["status"] == "done"


def test_svg_asset_bound_eps_on_a_cold_worker_says_it_needs_the_live_figure(project, monkeypatch):
    import hashlib

    client, root = project
    figure = _scripted_svg_asset(client, root)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    _bind(monkeypatch, hashlib.sha256(SVG_ASSET).hexdigest())
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec(figure, scope="original", formats=("svg", "eps")))
    assert outputs["eps"]["error"]["code"] == "eps_needs_live_figure"


def test_job_asserts_one_basis_even_if_a_format_path_drifts(project, monkeypatch):
    """作业级断言：哪怕某条格式路径自认为有证明，基准身份与 SVG 不同也逐项失败。"""
    client, root = project
    figure = _scripted_svg_asset(client, root)
    _bind(monkeypatch, "0" * 64)
    body, outputs = output(client, spec(figure, scope="original", formats=("svg", "eps")))
    assert outputs["svg"]["status"] == "done"
    assert outputs["eps"]["status"] == "failed"
    assert outputs["eps"]["error"]["code"] == "export_basis_mismatch"


def _script_saving_svg(size):
    return SCRIPT.replace('savefig("F.pdf")', 'savefig("F.svg")').replace(
        "figsize=(3, 2)", f"figsize={size}"
    )


def _disk_svg_from_script(root, size):
    """用户自己跑脚本（非 Tavotto）落在磁盘上的 `F.svg`。"""
    import os
    import subprocess

    (root / "plot.py").write_text(_script_saving_svg(size), encoding="utf-8")
    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('plot.py')['main']()"],
        cwd=root,
        env={**os.environ, "MPLBACKEND": "agg"},
        check=True,
    )


def test_svg_asset_saved_by_the_live_figure_gets_svg_and_eps_from_one_basis(project, monkeypatch):
    """磁盘 `F.svg` 正是脚本保存出的那张图：运行后写下字节绑定，svg 与 eps 同一基准、都交付、不重跑。"""
    import hashlib

    client, root = project
    _disk_svg_from_script(root, (3, 2))
    disk = (root / "F.svg").read_bytes()
    source = run_script(client)
    assert source
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec("F.svg", scope="original", formats=("svg", "eps")))
    assert body["status"] == "done", body
    assert outputs["eps"]["status"] == "done"
    assert hashlib.sha256(disk).hexdigest()
    sha, receipt_id = m._svg_asset_binding(type("S", (), {"figure_id": "F.svg"})(), root)
    assert sha == hashlib.sha256(disk).hexdigest()
    assert receipt_id


def test_a_rerun_between_the_binding_check_and_the_worker_never_keeps_the_old_basis(
    project, monkeypatch
):
    """Codex P1（#1230）：绑定校验通过后、拿到 worker 之前跑完一次显式重跑——EPS 会从**新的**活 Figure 画出，
    不能再挂旧绑定的基准。"""
    client, root = project
    _disk_svg_from_script(root, (3, 2))
    run_script(client)
    real = m._svg_asset_binding

    def binding_then_rerun(src, tmp_dir):
        bound = real(src, tmp_dir)
        assert bound is not None
        run_script(client)  # 显式重跑：同一个脚本，新的一次 build
        return bound

    monkeypatch.setattr(m, "_svg_asset_binding", binding_then_rerun)
    body, outputs = output(client, spec("F.svg", scope="original", formats=("svg", "eps")))
    assert outputs["svg"]["status"] == "done"
    assert outputs["eps"]["status"] == "failed", body
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"


def test_svg_asset_that_differs_from_the_live_figure_is_never_bound(project, monkeypatch):
    """磁盘 `F.svg` 是脚本另一个版本存的（改过脚本没重跑 / 保存参数不同）：不写绑定，eps 如实失败。"""
    client, root = project
    _disk_svg_from_script(root, (4, 2))
    (root / "plot.py").write_text(_script_saving_svg((3, 2)), encoding="utf-8")
    run_script(client)
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec("F.svg", scope="original", formats=("svg", "eps")))
    assert outputs["svg"]["status"] == "done"
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"


def test_svg_asset_never_run_has_no_binding(project, monkeypatch):
    client, root = project
    _disk_svg_from_script(root, (3, 2))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec("F.svg", scope="original", formats=("svg", "eps")))
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"


def test_cold_worker_eps_with_an_existing_script_is_not_reported_as_no_script(project):
    """A3b：脚本存在、只是没有活 Figure（冷 worker）——原因码不能是 `eps_needs_script`。"""
    client, root = project
    source = run_script(client)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    body, outputs = output(client, spec(source, scope="original", formats=("svg", "eps")))
    assert outputs["eps"]["status"] == "failed"
    assert outputs["eps"]["error"]["code"] == "eps_needs_live_figure"
    assert outputs["eps"]["error"]["params"] == {"figure": source}


def test_project_package_contains_only_referenced_portable_snapshots(project, monkeypatch):
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
        packaged_doc = json.loads(archive.read("layout.json"))
        reference = packaged_doc["canvases"][0]["objects"][0]["figureSnapshot"]
        snapshot_name = prefix + "snapshots/" + reference["snapshotId"] + ".json"
        assert snapshot_name in names
        snapshot = json.loads(archive.read(snapshot_name))["payload"]
        assert snapshot["receipt"]["receipt_id"] == reference["receiptId"]
        assert snapshot["identity"]["source_id"] == reference["sourceId"] == source
        moved = root.parent / "unpacked-project"
        moved.mkdir()
        for name in names:
            if name.startswith("assets/") and not name.endswith("/"):
                destination = moved / name.removeprefix("assets/")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))

    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(root.parent / "package-fresh-data"))
    m.open_project(str(moved))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("package export ran script"))
    for scope in ("original", "canvas"):
        body, outputs = output(
            client, spec(source, scope=scope, formats=("pdf", "svg"), reference=reference)
        )
        assert body["status"] == "done", body
        assert_svg(body, outputs["svg"])
        assert outputs["pdf"]["status"] == "done"


def test_single_original_svg_supports_live_custom_formatter_without_pairing(project):
    client, root = project
    (root / "plot.py").write_text(
        SCRIPT.replace(
            '    ax.set_title("Source title")',
            "    from matplotlib.ticker import FuncFormatter\n"
            '    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"{x:.1f} units"))\n'
            '    ax.set_title("Source title")',
        ),
        encoding="utf-8",
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


@pytest.mark.parametrize("scope", ["original", "canvas"])
def test_failed_generation_marker_never_authorizes_an_unknown_current_snapshot(
    project, monkeypatch, scope
):
    import shutil

    from tavotto.engine import svgstore

    client, root = project
    source = run_script(client)
    saved = client.post("/api/layouts/old?target=project", json=document(source, []))
    assert saved.status_code == 200, saved.get_json()
    old_doc = json.loads((root / "tavottofile" / "old.json").read_text(encoding="utf-8"))
    reference = old_doc["canvases"][0]["objects"][0]["figureSnapshot"]
    assert reference == saved.get_json()["figure_snapshots"]["panel"]
    pool.shutdown_all(figures_dir=str(root), wait=True)
    (root / "plot.py").write_text(
        SCRIPT.replace("Source title", "New execution title"), encoding="utf-8"
    )
    monkeypatch.setattr(
        svgstore,
        "activate",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only source marker")),
    )
    monkeypatch.setattr(m.engine_runtimeasset, "materialize", lambda *a, **k: None)
    assert run_script(client) == source  # SVG storage failures never block running science.
    rejected = client.post("/api/export", json=spec(source, scope=scope, reference=reference))
    assert rejected.status_code >= 400, rejected.get_json()
    assert rejected.get_json()["status"] == "failed", rejected.get_json()
    # 执行来源缺与当前执行匹配的快照：说「先重新运行脚本」的专用码，不是共用的 svg_source_unsupported
    assert rejected.get_json()["code"] == "svg_snapshot_missing", rejected.get_json()
    # The established PDF-only path remains usable with no snapshot reference.
    body, outputs = output(client, spec(source, scope=scope, formats=("pdf",)))
    assert outputs["pdf"]["status"] == "done", body
    failed_save = client.post("/api/layouts/new?target=project", json=old_doc)
    assert failed_save.status_code == 200, failed_save.get_json()
    assert failed_save.get_json()["svg_snapshot_warnings"] == [source]
    assert failed_save.get_json()["figure_snapshots"] == {}
    new_doc = json.loads((root / "tavottofile" / "new.json").read_text(encoding="utf-8"))
    assert "figureSnapshot" not in new_doc["canvases"][0]["objects"][0]
    assert json.loads((root / "tavottofile" / "old.json").read_text(encoding="utf-8")) == old_doc
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    with monkeypatch.context() as cold:
        cold.setattr(pool, "get", lambda *a, **k: pytest.fail("same-machine cold export executed"))
        rejected = client.post("/api/export", json=spec(source, scope=scope))
        assert rejected.status_code >= 400, rejected.get_json()
    m.reset_projects()
    moved = root.with_name("moved-after-failure")
    shutil.copytree(root, moved)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(root.parent / "fresh-after-failure"))
    m.open_project(str(moved))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("cold snapshot executed code"))
    rejected = client.post("/api/export", json=spec(source, scope=scope))
    assert rejected.status_code >= 400, rejected.get_json()
    # A user explicitly reopening the OLD saved document may export its immutable
    # OLD Figure. It must not be promoted to the unknown latest execution.
    body, outputs = output(
        client, spec(source, scope=scope, formats=("pdf", "svg"), reference=reference)
    )
    assert body["status"] == "done", body
    assert_svg(body, outputs["svg"])
    # A single PDF also honors the same explicit saved identity without executing.
    body, outputs = output(client, spec(source, scope=scope, formats=("pdf",), reference=reference))
    assert outputs["pdf"]["status"] == "done", body
    rejected = client.post(
        "/api/export",
        json=spec(
            source,
            scope=scope,
            patches=[{"gid": "title", "prop": "text", "value": "changed"}],
            reference=reference,
        ),
    )
    assert rejected.status_code >= 400, rejected.get_json()


def test_native_barrier_change_during_capture_never_publishes_a_mislabeled_pair(
    project, monkeypatch
):
    from tavotto.engine import nativesession

    _, root = project
    session = nativesession.NativeSession(
        session_id="native-capture-race",
        descriptor={
            "metadata": {
                "project_root": str(root),
                "cwd": str(root),
                "interpreter": sys.executable,
                "target_kind": "script",
            },
            "out_dir": str(root.parent / "native-out"),
        },
    )
    session.built = True
    session._on_frame({nativesession.EVENT_KEY: "barrier", "stems": ["F"]})

    def changed(*args, **kwargs):
        session._on_frame({nativesession.EVENT_KEY: "released"})
        session._on_frame({nativesession.EVENT_KEY: "barrier", "stems": ["F"]})
        return {"svg": {"path": kwargs["svg_path"]}}

    monkeypatch.setattr(session, "export", changed)
    monkeypatch.setattr(
        m,
        "_save_svg_snapshot",
        lambda *a, **k: pytest.fail("published an old receipt for a new native barrier"),
    )
    desc = {
        "asset_id": figcapture.runtime_asset_id("plot.py", "F"),
        "stem": "F",
        "script": "plot.py",
    }
    m._capture_svg_snapshot_best_effort(session, desc)
    assert session.snapshot_epoch > 1


def test_native_barrier_change_during_export_is_rejected_before_receipt_publication(
    project, monkeypatch
):
    from tavotto.engine import exportreq, nativesession

    _, root = project
    source = figcapture.runtime_asset_id("plot.py", "F")
    session = nativesession.NativeSession(
        session_id="native-export-race",
        descriptor={
            "metadata": {
                "project_root": str(root),
                "cwd": str(root),
                "interpreter": sys.executable,
                "target_kind": "script",
            },
            "out_dir": str(root.parent / "native-export"),
        },
    )
    session.built = True
    session._on_frame({nativesession.EVENT_KEY: "barrier", "stems": ["F"]})

    def changed(*args, **kwargs):
        session._on_frame({nativesession.EVENT_KEY: "released"})
        session._on_frame({nativesession.EVENT_KEY: "barrier", "stems": ["F"]})
        return {}

    monkeypatch.setattr(session, "export", changed)
    monkeypatch.setattr(m, "_engine_worker", lambda *a, **k: (session, "F"))
    with pytest.raises(exportreq.ExportRequestError, match="Figure changed"):
        m._serialize_figure_with_worker(
            source, [], "svg", 600, out_dir=root.parent / "out", existing_only=True
        )


@pytest.mark.parametrize(
    "transition", ["released", "released_then_ended", "continue", "failed_continue"]
)
def test_native_execution_revokes_saved_reference_before_new_materialization(
    project, monkeypatch, transition
):
    from tavotto.engine import nativesession

    _, root = project
    source = figcapture.runtime_asset_id("plot.py", "F")
    session = nativesession.NativeSession(
        session_id="native-reference-authority",
        descriptor={
            "metadata": {
                "project_root": str(root),
                "cwd": str(root),
                "interpreter": sys.executable,
                "target_kind": "script",
            },
            "out_dir": str(root.parent / "native-reference"),
        },
    )
    session._on_frame({nativesession.EVENT_KEY: "barrier", "stems": ["F"]})
    before = m._execution_receipt(session, "plot.py")
    ctx = m._request_ctx()
    ctx.svg_snapshot_receipts[source] = before.receipt_id
    ctx.svg_snapshot_native_sources[source] = (session, "plot.py")
    record = {"receipt": before.public_facts()}
    assert m._svg_snapshot_matches_current(root, source, record, explicit=True)

    if transition == "continue":
        # Response is already successful but no released event has arrived.
        monkeypatch.setattr(session, "_request", lambda *a, **k: {"ok": True})
        session.resume()
        assert session.state == nativesession.CONTINUING
    elif transition == "failed_continue":

        def failed(*a, **k):
            raise pool.WorkerError("continue failed")

        monkeypatch.setattr(session, "_request", failed)
        with pytest.raises(pool.WorkerError, match="continue failed"):
            session.resume()
        assert session.state == nativesession.BARRIER
    else:
        session._on_frame({nativesession.EVENT_KEY: "released"})
        if transition == "released_then_ended":
            session._on_frame({nativesession.EVENT_KEY: "exit", "code": 0})

    # No new capture/materialize updated the old context receipt.
    assert ctx.svg_snapshot_receipts[source] == before.receipt_id
    assert m._svg_snapshot_matches_current(root, source, record, explicit=True) is (
        transition == "failed_continue"
    )
