"""Desktop SVG pipeline: real capture, snapshot reopen, partial and safe publication."""

from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto import app as m
from tavotto.engine import exportjob, figcapture, patchspec, pool, project_watch, svgstore

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
    # 画布合成走 RenderCore 共享子进程；不关会漏给同一 xdist worker 里后面的用例（hang_watchdog 的「没有子进程」断言）
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()


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


def assert_svg(body, item, *, scope="original", text=None):
    assert item["status"] == "done", body
    path = Path(body["export_dir"]) / item["name"]
    data = path.read_bytes()
    assert not data.startswith(b"\x89PNG")
    root = ET.fromstring(data)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    texts = list(root.iter("{http://www.w3.org/2000/svg}text"))
    if text is None:
        text = scope == "canvas"
    if text and scope == "original":
        # 规格变更（ADR 0144，用户追加裁决）：按原图导出的 matplotlib 图也默认留 <text>（独立的 fonttype=none 一路）
        assert texts and any("Source title" in "".join(t.itertext()) for t in texts)
    elif scope == "canvas":
        # 规格变更（ADR 0144）：画布合成由 RenderCore 写 SVG，文字默认留成 <text>（可在 Office 里编辑）。
        assert texts and all((t.text or "").strip() for t in texts)
    else:
        # 已存快照 / 回退路径交的是 path 基准那份（#1185 等价证人）：文字仍是轮廓。
        assert not texts
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
    assert_svg(body, outputs["svg"], scope=scope, text=True)
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
    assert_svg(body, outputs["svg"], scope=scope)
    assert outputs["pdf"]["status"] == "done"
    body, outputs = output(client, spec(source, scope=scope, strict=True, reference=reference))
    assert_svg(body, outputs["svg"], scope=scope)


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
    assert_svg(body, outputs["svg"], scope="canvas")
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


def test_cold_render_of_missing_disk_script_does_not_block_archived_export(project, monkeypatch):
    import subprocess

    client, root = project
    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('plot.py')['main']()"],
        cwd=root,
        env={**os.environ, "MPLBACKEND": "agg"},
        check=True,
    )
    run_script(client)
    patch = {"gid": "axes_0.title", "prop": "text", "value": "Saved cold drawing"}
    response = client.post("/api/layouts/cold?target=project", json=document("F.pdf", [patch]))
    assert response.status_code == 200, response.get_json()
    reference = response.get_json()["figure_snapshots"]["panel"]
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    (root / "plot.py").rename(root / "plot.py.removed")
    m.open_project(str(root))

    # A snapshot-less timeline node can arrive before a named, archived layout.
    def forbidden_spawn(*args, **kwargs):
        pytest.fail("missing script started a worker")

    monkeypatch.setattr(pool.EngineWorker, "__init__", forbidden_spawn)
    monkeypatch.setattr(pool.WorkerdWorker, "__init__", forbidden_spawn)
    render = client.post("/api/engine/render", json={"id": "F.pdf", "patches": [patch]})
    assert render.get_json()["code"] == "script_source_missing"
    assert render.get_json()["params"] == {"script": "plot.py"}
    body, outputs = output(
        client, spec("F.pdf", patches=[patch], formats=("pdf", "svg"), reference=reference)
    )
    assert body["status"] == "done", body
    assert outputs["pdf"]["status"] == "done"
    assert_svg(body, outputs["svg"], scope="canvas")


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
        assert_svg(body, outputs["svg"], scope=scope)
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


def test_svg_asset_with_user_callbacks_is_never_bound(project, monkeypatch):
    """#1355 Codex P1：#1185 的证人是独立 savefig，配对是单次绘制——有用户回调（哪怕只算值）就不能把
    「证人与磁盘一致」当成「被 freeze 的配对那份也一致」。此处回调是确定的，证人与磁盘必然一致，所以只有阻断条件能挡住绑定。"""
    client, root = project
    script = _script_saving_svg((3, 2)).replace(
        '    ax.set_title("Source title")',
        "    from matplotlib.ticker import FuncFormatter\n"
        '    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: "x"))\n'
        '    ax.set_title("Source title")',
    )
    (root / "plot.py").write_text(script, encoding="utf-8")
    import os
    import subprocess

    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('plot.py')['main']()"],
        cwd=root,
        env={**os.environ, "MPLBACKEND": "agg"},
        check=True,
    )
    assert (root / "F.svg").is_file()
    run_script(client)
    assert m._svg_asset_binding(type("S", (), {"figure_id": "F.svg"})(), root) is None
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("EPS reran the script"))
    body, outputs = output(client, spec("F.svg", scope="original", formats=("svg", "eps")))
    assert outputs["eps"]["error"]["code"] == "eps_source_unverified"


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
        assert_svg(body, outputs["svg"], scope=scope)
        assert outputs["pdf"]["status"] == "done"


def test_single_original_svg_supports_live_custom_formatter_with_single_draw_pairing(project):
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
    assert_svg(body, outputs["svg"], text=True)
    # ADR 0150：PDF 与 SVG 出自同一次 draw，自定义刻度回调不再挡配对
    body, outputs = output(client, spec(source, scope="original", formats=("pdf", "svg")))
    assert body["status"] == "done", body
    assert outputs["pdf"]["status"] == "done"
    assert outputs["svg"]["status"] == "done"


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
    assert_svg(body, outputs["svg"], scope=scope)
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


def _original_svg_text_facts(body, outputs):
    path = Path(body["export_dir"]) / outputs["svg"]["name"]
    root = ET.fromstring(path.read_bytes())
    ns = "{http://www.w3.org/2000/svg}"
    return root, list(root.iter(ns + "text"))


def test_original_export_keeps_matplotlib_text_with_its_own_font(project):
    """ADR 0144：原样导出默认 <text>；脚本自己的字体（matplotlib 默认 DejaVu Sans）按输出保留。"""
    client, _ = project
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="original", strict=True))
    root, texts = _original_svg_text_facts(body, outputs)
    title = [t for t in texts if "".join(t.itertext()) == "Source title"]
    assert len(title) == 1
    style = title[0].get("style") or ""
    assert "DejaVu Sans" in style and "Liberation" not in style


def test_original_export_pair_serves_text_but_saved_snapshot_baseline_stays_path(project):
    client, root = project
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="original", formats=("pdf", "svg")))
    assert body["status"] == "done", body
    assert_svg(body, outputs["svg"], text=True)
    # 存下快照：#1185 等价证人那份基准字节仍是 path，没有被文字版替换
    saved = client.post("/api/layouts/first?target=project", json=document(source, []))
    assert saved.status_code == 200, saved.get_json()
    reference = saved.get_json()["figure_snapshots"]["panel"]
    body, outputs = output(
        client, spec(source, scope="original", formats=("svg", "pdf"), reference=reference)
    )
    assert_svg(body, outputs["svg"], text=False)


def test_old_worker_without_the_new_fields_falls_back_to_outlines(project, monkeypatch):
    client, _ = project
    source = run_script(client)
    monkeypatch.setattr(m, "_supported_export_kwargs", lambda worker, **wanted: {})
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"], text=False)
    body, outputs = output(client, spec(source, scope="original", formats=("pdf", "svg")))
    assert_svg(body, outputs["svg"], text=False)


def test_receipt_says_text_was_outlined_only_when_it_was(project, monkeypatch):
    """QA-WIN-08：缺文字版（旧 worker / 已存快照）退回轮廓时，回执要带 `text_outlined`；留成 <text> 的不带。"""
    client, _ = project
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"], text=True)
    assert outputs["svg"]["manifest"]["text_outlined"] is False
    monkeypatch.setattr(m, "_supported_export_kwargs", lambda worker, **wanted: {})
    body, outputs = output(client, spec(source, scope="original", strict=True))
    assert_svg(body, outputs["svg"], text=False)
    assert outputs["svg"]["manifest"]["text_outlined"] is True


def test_supported_export_kwargs_only_passes_what_the_worker_signature_has():
    class Old:
        def export(self, stem, patches, path, fmt="pdf", dpi=600, *, svg_path=None): ...

    class New:
        def export(
            self, stem, patches, path, fmt="pdf", dpi=600, *, svg_fonttype=None, **extra
        ): ...

    wanted = {"svg_fonttype": "none", "svg_text_path": "x"}
    assert m._supported_export_kwargs(Old(), **wanted) == {}
    assert m._supported_export_kwargs(New(), **wanted) == wanted


def _canvas_panel_texts(body, outputs):
    path = Path(body["export_dir"]) / outputs["svg"]["name"]
    ns = "{http://www.w3.org/2000/svg}"
    return ["".join(t.itertext()) for t in ET.fromstring(path.read_bytes()).iter(ns + "text")]


def test_canvas_live_figure_panel_keeps_matplotlib_text(project):
    """P1（Codex #1283）：画布里的 live Figure 面板也要带上 matplotlib 自己的 <text>，不只画布新增文字。"""
    client, _ = project
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="canvas"))
    assert body["status"] == "done", body
    assert "Source title" in _canvas_panel_texts(body, outputs)


def test_canvas_live_figure_panel_falls_back_to_outlines_on_old_worker(project, monkeypatch):
    client, _ = project
    source = run_script(client)
    monkeypatch.setattr(m, "_supported_export_kwargs", lambda worker, **wanted: {})
    body, outputs = output(client, spec(source, scope="canvas", formats=("svg", "pdf")))
    assert body["status"] == "done", body
    assert outputs["pdf"]["status"] == "done"
    texts = _canvas_panel_texts(body, outputs)
    assert "Source title" not in texts and texts  # 画布自己的文字仍在


FORMATTER_SCRIPT = SCRIPT.replace(
    '    ax.set_title("Source title")',
    "    from matplotlib.ticker import FuncFormatter\n"
    "    calls = [0]\n"
    "    def label(v, _p):\n"
    "        calls[0] += 1\n"
    "        return f'tick{calls[0]}'\n"
    "    ax.xaxis.set_major_formatter(FuncFormatter(label))\n"
    '    ax.set_title("Source title")',
)

#: 自定义 canvas 打印方法：单次绘制接不了（`print_pdf` 必须是 matplotlib 自己的），仍是被保留的阻断
CUSTOM_CANVAS_SCRIPT = SCRIPT.replace(
    '    ax.set_title("Source title")',
    "    import functools\n"
    "    native = fig.canvas.print_figure\n"
    "    fig.canvas.print_figure = functools.wraps(native)(lambda *a, **k: native(*a, **k))\n"
    '    ax.set_title("Source title")',
)


def test_canvas_svg_of_a_script_figure_with_counter_formatter_matches_the_pdf(project):
    """ADR 0150 的端到端：带计数器 FuncFormatter 的脚本图，画布导 SVG 成功，文字与同一次导出的 PDF 一致。"""
    import pymupdf

    client, root = project
    (root / "plot.py").write_text(FORMATTER_SCRIPT, encoding="utf-8")
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="canvas", formats=("svg", "pdf")))
    assert body["status"] == "done", body
    export = Path(body["export_dir"])
    with pymupdf.open(export / outputs["pdf"]["name"]) as pdf:
        pdf_text = pdf[0].get_text()
    ns = "{http://www.w3.org/2000/svg}text"
    svg_text = " ".join(
        "".join(t.itertext())
        for t in ET.fromstring((export / outputs["svg"]["name"]).read_bytes()).iter(ns)
    )
    import re

    # 计数器每画一遍就变：两份产物里的刻度标签必须是同一串（同一次 draw），而不是各自画一遍的 tick1.. / tick121..
    pdf_ticks = re.findall(r"tick\d+", pdf_text)
    svg_ticks = re.findall(r"tick\d+", svg_text)
    assert pdf_ticks and sorted(pdf_ticks) == sorted(svg_ticks), (pdf_ticks, svg_ticks)


def test_canvas_svg_of_a_script_figure_without_svg_pairing_says_so_in_user_words(project):
    """被保留的阻断（自定义 canvas 打印）：该项失败，错误码是面向用户的，不是 `imported_page: …` 内部串（#1348）。"""
    client, root = project
    (root / "plot.py").write_text(CUSTOM_CANVAS_SCRIPT, encoding="utf-8")
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="canvas", formats=("svg", "pdf")))
    assert body["status"] == "partial", body
    assert outputs["pdf"]["status"] == "done"
    error = outputs["svg"]["error"]
    assert error["code"] == "svg_figure_unavailable", error
    assert error["params"]["figure"] == source
    assert "custom" in error["params"]["reason"]  # 技术原因进参数 / 诊断，不进文案
    assert "imported_page" in error["params"]["detail"]  # 原文留作详情


#: 用户自己给元素起了中文 / 带空格的名字（`set_gid`）：matplotlib 原样写进 SVG 的 id，安全闸的 id 语法不认
USER_GID_SCRIPT = SCRIPT.replace(
    '    ax.set_title("Source title")',
    '    ax.set_gid("面板 A")\n    fig.patch.set_gid("背景 图")\n    ax.set_title("Source title")',
)


def test_canvas_svg_of_a_script_figure_with_user_gid_exports(project):
    """反馈包：用户脚本 set_gid 了中文名，导出级 SVG 快照被整份拒收，SVG/PDF/PNG 全挂。可信 worker 边界改名后照常导出。"""
    client, root = project
    (root / "plot.py").write_text(USER_GID_SCRIPT, encoding="utf-8")
    source = run_script(client)
    body, outputs = output(client, spec(source, scope="canvas", formats=("svg", "pdf")))
    assert body["status"] == "done", body
    assert outputs["svg"]["status"] == "done", outputs["svg"]


def _run_script_on_disk(root):
    """按脚本自己的 cwd 跑一遍，让项目里真的有一份 `F.pdf`（probe 的 worker 在别处落产物）。"""
    import os
    import subprocess

    subprocess.run(
        [sys.executable, "-c", "import runpy; runpy.run_path('plot.py')['main']()"],
        cwd=root,
        env={**os.environ, "MPLBACKEND": "agg"},
        check=True,
    )


def _disk_snapshot_reference(root, source="F.pdf"):
    """磁盘别名（`F.pdf` / `F.svg`）的独立接纳快照引用（捕获时按 static 字节绑定保存）。"""
    record = svgstore.load(
        root,
        source,
        patchspec.patch_hash([]),
        static_sha256=figcapture.hash_file(root / source)[0],
        remap_fingerprint=m.engine_inputremap.fingerprint(root),
        storage_root=m._svg_storage_root(root),
    )
    assert record is not None and record.get("snapshot_ref"), record
    return record["snapshot_ref"]


def test_original_pdf_only_uses_the_saved_snapshot_when_the_disk_file_changed(project):
    """明确引用指向保存时的 PDF：磁盘同名文件被替换后，PDF-only 也必须交存档版本（Codex #1926 P1）。"""
    import pikepdf

    client, root = project
    _run_script_on_disk(root)
    run_script(client)
    reference = _disk_snapshot_reference(root)
    changed = pikepdf.new()
    changed.add_blank_page(page_size=(300, 300))
    changed.save(root / "F.pdf")
    body, outputs = output(
        client, spec("F.pdf", scope="original", formats=("pdf",), reference=reference)
    )
    assert outputs["pdf"]["status"] == "done", body
    produced = Path(body["export_dir"]) / outputs["pdf"]["name"]
    with pikepdf.open(produced) as pdf:
        box = pdf.pages[0].MediaBox
        width_pt = float(box[2]) - float(box[0])
        height_pt = float(box[3]) - float(box[1])
    # 脚本是 figsize=(3, 2) → 216×144pt；被替换的空白页是 300×300pt。
    assert width_pt == pytest.approx(216.0)
    assert height_pt == pytest.approx(144.0)


def test_original_export_with_a_historical_input_rejects_a_cold_execution(project, monkeypatch):
    """历史输入没有唯一活执行时拒绝，绝不退回磁盘同名文件（Codex #1926 P1）。"""
    client, root = project
    _run_script_on_disk(root)
    run_script(client)
    reference = _disk_snapshot_reference(root)
    request = spec("F.pdf", scope="original", formats=("pdf",))
    request["original"]["figure_origin"] = {
        "version": 1,
        "snapshot": reference,
        "patches": [],
    }
    # 同进程、活 Figure 还在：出处准入成立，照常导出。
    body, outputs = output(client, request)
    assert outputs["pdf"]["status"] == "done", body
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("cold origin export executed code")
    )
    response = client.post("/api/export", json=request)
    assert response.status_code >= 400, response.get_json()
    payload = response.get_json()
    assert payload["status"] == "failed", payload
    assert payload["code"] == "svg_source_unavailable", payload


def test_cold_origin_replay_requests_the_archived_execution_profile(project, monkeypatch):
    """冷回放按归档回执的档位解析 worker；native 离线时拒绝，不静默降级到 safe（Codex #1926 P1）。"""
    _client, root = project
    _run_script_on_disk(root)
    seen = []

    def resolve(**kwargs):
        seen.append(kwargs["execution_profile"])
        if kwargs["execution_profile"] != m.engine_enginesession.PROFILE_NATIVE:
            raise AssertionError("cold replay fell back to a safe session")
        raise m.engine_runcodes.RunError(m.engine_runcodes.NATIVE_SESSION_OFFLINE)

    monkeypatch.setattr(m.engine_enginesession, "resolve", resolve)
    record = {
        "receipt": {"profile": m.engine_enginesession.PROFILE_NATIVE},
        "execution_owner": {"script": "plot.py", "run_config": None},
    }
    with pytest.raises(m.engine_runcodes.RunError) as excinfo:
        m._replay_figure_origin(
            "F.pdf",
            {"version": 1, "snapshot": {}, "patches": []},
            record,
            SimpleNamespace(path=root),
        )
    assert excinfo.value.code == m.engine_runcodes.NATIVE_SESSION_OFFLINE
    assert seen == [m.engine_enginesession.PROFILE_NATIVE]


def test_original_export_checks_the_origin_even_with_a_saved_snapshot(project, monkeypatch):
    """同时带快照与出处时，快照不是出处授权：冷进程没有唯一活执行就拒绝（Codex #1926 P1 残项）。"""
    client, root = project
    _run_script_on_disk(root)
    run_script(client)
    reference = _disk_snapshot_reference(root)
    request = spec("F.pdf", scope="original", formats=("pdf",), reference=reference)
    request["original"]["figure_origin"] = {
        "version": 1,
        "snapshot": reference,
        "patches": [],
    }
    body, outputs = output(client, request)
    assert outputs["pdf"]["status"] == "done", body
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("cold origin export executed code")
    )
    response = client.post("/api/export", json=request)
    assert response.status_code >= 400, response.get_json()
    payload = response.get_json()
    assert payload["status"] == "failed", payload
    assert payload["code"] == "svg_source_unavailable", payload


def test_annotated_eps_for_a_historical_svg_asset_uses_its_origin(project):
    """未编辑 `.svg` 的带标注 EPS 有出处时走出处执行，不再按当前文件绑定（Codex #1926 P1 残项）。"""
    client, root = project
    _disk_svg_from_script(root, (3, 2))
    run_script(client)
    reference = _disk_snapshot_reference(root, "F.svg")
    # 当前文件字节改掉：按当前文件绑定的那条路会 eps_source_unverified，存档出处不受影响。
    (root / "F.svg").write_bytes(SVG_ASSET)
    request = spec("F.svg", scope="original", formats=("eps",))
    request["original"].update(
        {
            "w_mm": 76.2,
            "h_mm": 50.8,
            "objects": [
                {
                    "id": "note",
                    "type": "text",
                    "text": "note",
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 40,
                    "h_mm": 8,
                    "size_pt": 10,
                }
            ],
            "figure_origin": {"version": 1, "snapshot": reference, "patches": []},
        }
    )
    body, outputs = output(client, request)
    assert outputs["eps"]["status"] == "done", body


def test_merged_pdf_page_cache_distinguishes_a_historical_origin(project):
    """多页 PDF 的页缓存键必须带出处：两页同 id/overrides/快照字段时不能复用另一页的历史渲染（Codex #1926 新 P1）。"""
    import pikepdf
    import pymupdf

    client, root = project
    _run_script_on_disk(root)
    run_script(client)
    reference = _disk_snapshot_reference(root)
    changed = pikepdf.new()
    changed.add_blank_page(page_size=(300, 300))
    changed.save(root / "F.pdf")
    request = {
        "scope": "original",
        "filename": "merged",
        "formats": ["pdf"],
        "merge_pdf": {
            "pages": [
                {
                    "figure_id": "F.pdf",
                    "source_kind": "vector",
                    "figure_origin": {
                        "version": 1,
                        "snapshot": reference,
                        "patches": [],
                    },
                },
                {"figure_id": "F.pdf", "source_kind": "vector"},
            ]
        },
    }
    body, outputs = output(client, request)
    assert outputs["pdf"]["status"] == "done", body
    produced = Path(body["export_dir"]) / outputs["pdf"]["name"]
    with pymupdf.open(produced) as pdf:
        sizes = [(round(page.rect.width), round(page.rect.height)) for page in pdf]
    assert sizes == [(216, 144), (300, 300)], sizes


def test_archived_pdf_raster_reports_the_archived_page_size(project):
    """存档 PDF 出 PNG/TIFF 时，回报尺寸必须是存档那一页，不是请求里替换文件的 w/h（Codex #1926 P2）。"""
    import pikepdf

    client, root = project
    _run_script_on_disk(root)
    run_script(client)
    reference = _disk_snapshot_reference(root)
    changed = pikepdf.new()
    changed.add_blank_page(page_size=(288, 288))
    changed.save(root / "F.pdf")
    request = spec("F.pdf", scope="original", formats=("png",), reference=reference)
    request["original"]["w_mm"] = 101.6
    request["original"]["h_mm"] = 101.6
    body, outputs = output(client, request)
    assert outputs["png"]["status"] == "done", body
    assert outputs["png"]["dimensions"]["mm"] == pytest.approx([76.2, 50.8], abs=0.01)


def test_execution_source_forwards_the_archived_profile_to_the_replay_lookup(monkeypatch):
    """冷回放的第二次取会话也要带归档档位，不能退回 safe（Codex #1926 P1 残项）。"""
    calls = []
    monkeypatch.setattr(m, "_serialize_figure_with_worker", lambda *a, **k: calls.append(k))
    monkeypatch.setattr(m, "_load_svg_snapshot", lambda *a, **k: None)
    with pytest.raises(m.engine_exportreq.ExportRequestError):
        m._execution_source(
            {"id": "F.pdf"}, 600, None, None, include_svg=True, execution_profile="native"
        )
    assert calls and calls[0]["execution_profile"] == "native"


def test_serialize_figure_with_worker_forwards_the_archived_profile(monkeypatch):
    seen = []

    def engine_worker(rel_id, **kwargs):
        seen.append(kwargs)
        return None, "F"

    monkeypatch.setattr(m, "_engine_worker", engine_worker)
    assert (
        m._serialize_figure_with_worker(
            "F.pdf", [], "pdf", 600, include_svg=True, execution_profile="native"
        )
        is None
    )
    assert seen == [{"existing_only": True, "execution_profile": "native"}]


def _title_gid(client, source):
    response = client.post("/api/engine/render", json={"id": source, "patches": []})
    assert response.status_code == 200, response.get_json()
    return next(
        el["gid"]
        for el in response.get_json()["manifest"]["elements"]
        for prop in el.get("editable", [])
        if prop["prop"] == "text" and prop.get("value") == "Source title"
    )


def _save_edit(client, source, gid, index, name="work"):
    patches = [{"gid": gid, "prop": "text", "value": f"Edited title {index}"}]
    response = client.post("/api/engine/render", json={"id": source, "patches": patches})
    assert response.status_code == 200, response.get_json()
    saved = client.post(f"/api/layouts/{name}?target=project", json=document(source, patches))
    assert saved.status_code == 200, saved.get_json()
    return patches, saved.get_json()


def _save_edit_through_reclaim(client, source, gid, index, name="work"):
    """The save that finds the store full starts a background reclaim (it holds a document
    lock, so it cannot wait for every lock); the next save of the same edit gets in."""
    patches, saved = _save_edit(client, source, gid, index, name)
    if saved.get("svg_snapshot_warnings"):
        m._wait_svg_reclaim()
        patches, saved = _save_edit(client, source, gid, index, name)
    return patches, saved


def _snapshot_store_bytes(root):
    store = root / "tavottofile" / "figure_snapshots"
    return sum(p.stat().st_size for p in store.rglob("*") if p.is_file() and p.name != ".lock")


def test_full_project_snapshot_store_reclaims_unreferenced_edits_on_save(project, monkeypatch):
    """One user's report: repeated edits filled the project store; SVG export had no way out."""
    from tavotto.engine import svgstore

    client, root = project
    monkeypatch.setattr(svgstore, "RECLAIM_GRACE_SECONDS", 0, raising=False)
    source = run_script(client)
    kept = client.post("/api/layouts/kept?target=project", json=document(source, []))
    assert kept.status_code == 200, kept.get_json()
    kept_ref = kept.get_json()["figure_snapshots"]["panel"]
    gid = _title_gid(client, source)
    first_patches, first = _save_edit(client, source, gid, 1)
    first_ref = first["figure_snapshots"]["panel"]
    for index in (2, 3):
        _save_edit(client, source, gid, index)
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", _snapshot_store_bytes(root) + 200)
    _, full = _save_edit_through_reclaim(client, source, gid, 4)
    assert not full.get("svg_snapshot_warnings"), full
    assert full["figure_snapshots"]["panel"]["snapshotId"]
    archives = {p.stem for p in (root / "tavottofile" / "figure_snapshots" / "snapshots").glob("*")}
    # A saved document still references this one; it is never reclaimed.
    assert kept_ref["snapshotId"] in archives
    # The overwritten edit is referenced by nothing any more.
    assert first_ref["snapshotId"] not in archives
    body, outputs = output(client, spec(source, scope="original", reference=kept_ref))
    assert_svg(body, outputs["svg"])
    # A reference whose variant was reclaimed asks for a rerun in one line, like any
    # missing snapshot; it is never an unexplained failure.
    stale = client.post(
        "/api/export",
        json=spec(source, scope="original", patches=first_patches, reference=first_ref),
    )
    assert stale.get_json()["code"] == "svg_snapshot_missing", stale.get_json()


def test_full_project_snapshot_store_deletes_nothing_when_documents_cannot_be_scanned(
    project, monkeypatch
):
    from tavotto.engine import svgstore

    client, root = project
    monkeypatch.setattr(svgstore, "RECLAIM_GRACE_SECONDS", 0, raising=False)
    source = run_script(client)
    gid = _title_gid(client, source)
    for index in (1, 2, 3):
        _save_edit(client, source, gid, index)
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", _snapshot_store_bytes(root) + 200)
    monkeypatch.setattr(m, "_svg_reference_corpus", lambda root: None, raising=False)
    store = root / "tavottofile" / "figure_snapshots"
    before = sorted(p.name for p in store.rglob("*") if p.is_file())
    _, full = _save_edit(client, source, gid, 4)
    assert full["svg_snapshot_warnings"] == [source], full
    m._wait_svg_reclaim()
    assert sorted(p.name for p in store.rglob("*") if p.is_file()) == before
    # A store that could not be reclaimed is not rescanned on every following edit.
    monkeypatch.setattr(
        m, "_svg_reference_corpus", lambda root: pytest.fail("rescanned inside the backoff")
    )
    _, again = _save_edit(client, source, gid, 5)
    assert again["svg_snapshot_warnings"] == [source], again


def test_reclaim_failure_still_reports_the_original_full_store(project, monkeypatch):
    from tavotto.engine import svgstore

    client, root = project
    monkeypatch.setattr(svgstore, "RECLAIM_GRACE_SECONDS", 0, raising=False)
    source = run_script(client)
    gid = _title_gid(client, source)
    for index in (1, 2, 3):
        _save_edit(client, source, gid, index)
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", _snapshot_store_bytes(root) + 200)

    def busy(*args, **kwargs):
        raise TimeoutError("Figure snapshot store is busy")

    monkeypatch.setattr(svgstore, "reclaim", busy, raising=False)
    _, full = _save_edit(client, source, gid, 4)
    assert full["svg_snapshot_warnings"] == [source], full
    m._wait_svg_reclaim()
    _, again = _save_edit(client, source, gid, 4)
    assert again["svg_snapshot_warnings"] == [source], again


def test_reclaim_keeps_references_frozen_earlier_in_the_same_save(project):
    client, root = project
    pending = {"objects": [{"figureSnapshot": {"snapshotId": "f" * 64}}]}
    token = m._SVG_PENDING_DOCUMENT.set(pending)
    try:
        corpus = m._svg_reference_corpus(root)
    finally:
        m._SVG_PENDING_DOCUMENT.reset(token)
    assert corpus is not None and ("f" * 64).encode() in corpus


def _checkpoint(client, source, patches, reference, **kind):
    body = document(source, patches)
    body["canvases"][0]["objects"][0]["figureSnapshot"] = reference
    response = client.post("/api/versions/work-doc", json={"doc": body, **kind})
    assert response.status_code == 200, response.get_json()


def test_auto_checkpoints_do_not_pin_snapshots_but_named_versions_do(project, monkeypatch):
    """Long sessions write an auto checkpoint every few minutes; they must not refill the store."""
    from tavotto.engine import svgstore

    client, root = project
    monkeypatch.setattr(svgstore, "RECLAIM_GRACE_SECONDS", 0, raising=False)
    source = run_script(client)
    gid = _title_gid(client, source)
    named_patches, named = _save_edit(client, source, gid, 0)
    named_ref = named["figure_snapshots"]["panel"]
    _checkpoint(client, source, named_patches, named_ref, name="Submitted version")
    patches, first = _save_edit(client, source, gid, 1)
    _checkpoint(client, source, patches, first["figure_snapshots"]["panel"], auto=True)
    # Room for three variants: the named one, the newest one and the incoming edit.
    monkeypatch.setattr(svgstore, "MAX_TOTAL_BYTES", _snapshot_store_bytes(root) * 3 // 2 + 200)
    auto_refs = []
    for index in range(2, 9):  # several times what the budget holds
        patches, saved = _save_edit_through_reclaim(client, source, gid, index)
        assert not saved.get("svg_snapshot_warnings"), (index, saved)
        reference = saved["figure_snapshots"]["panel"]
        auto_refs.append(reference["snapshotId"])
        _checkpoint(client, source, patches, reference, auto=True)
    archives = {p.stem for p in (root / "tavottofile" / "figure_snapshots" / "snapshots").glob("*")}
    assert named_ref["snapshotId"] in archives
    assert not set(auto_refs[:-2]) <= archives


def test_reference_scan_reads_the_legacy_file_while_a_migration_is_pending(project):
    client, root = project
    versions = root / "tavottofile" / "versions"
    store = versions / "doc.vx"
    store.mkdir(parents=True)
    (store / "migration.json").write_text(json.dumps({"state": "pending"}), encoding="utf-8")
    named = "a" * 64
    auto = "b" * 64
    legacy = {
        "versions": [
            {"id": "v1-1", "name": "Submitted", "doc": {"ref": {"snapshotId": named}}},
            {"id": "v2-2", "auto": True, "doc": {"ref": {"snapshotId": auto}}},
        ]
    }
    (versions / "doc.json").write_text(json.dumps(legacy), encoding="utf-8")
    corpus = m._svg_reference_corpus(root)
    assert corpus is not None
    assert named.encode() in corpus
    assert auto.encode() not in corpus


def test_reference_scan_fails_closed_when_version_envelopes_cannot_be_listed(project, monkeypatch):
    client, root = project
    store = root / "tavottofile" / "versions" / "doc.vx"
    (store / "s").mkdir(parents=True)
    (store / "index.json").write_text("{}", encoding="utf-8")

    def unreadable(*args, **kwargs):
        raise m.engine_versionstore.IndexUnreadable("permission denied")

    real_listdir = os.listdir

    def listdir(path):
        if Path(path) == store / "s":
            raise PermissionError("permission denied")
        return real_listdir(path)

    monkeypatch.setattr(m.engine_versionstore, "list_index", unreadable)
    monkeypatch.setattr(m.os, "listdir", listdir)
    assert m._svg_reference_corpus(root) is None


def test_reclaim_excludes_document_and_version_commits_from_scan_to_delete(project, monkeypatch):
    """A named version committed while reclaim runs waits for it, never lands between scan and delete."""
    from tavotto.engine import svgstore

    client, root = project
    seen = {}
    real = svgstore.reclaim

    def observed(*args, **kwargs):
        # Every document lock stripe is held by the reclaiming thread right now.
        seen["held"] = all(lock.locked() for lock in m._DOCUMENT_LOCKS)
        return real(*args, **kwargs)

    monkeypatch.setattr(svgstore, "reclaim", observed)
    (root / "tavottofile" / "figure_snapshots").mkdir(parents=True)
    m._reclaim_svg_snapshots_now(root, 0)
    assert seen == {"held": True}
    assert not any(lock.locked() for lock in m._DOCUMENT_LOCKS)


def test_reclaim_requested_under_a_document_lock_runs_in_the_background(project, monkeypatch):
    client, root = project
    ran = []
    monkeypatch.setattr(m, "_reclaim_svg_snapshots_now", lambda r, n: ran.append(r) or 0)
    with m._document_lock(root / "tavottofile" / "x.json"):
        assert m._reclaim_svg_snapshots(root, incoming_bytes=1) == 0
    m._wait_svg_reclaim()
    assert ran == [root]


@pytest.fixture(autouse=True)
def _legacy_layout_file_form(monkeypatch):
    """ADR 0211 第二期 PR 3 起「排版写成目录形态」默认开；这份用例钉的是改造前 `tavottofile/<名>.json` 的形态与语义，
    显式关开关来跑（与 `TAVOTTO_LAYOUT_DIR_WRITER=0` 应急回退同一档；环境变量也让子进程一并关）。目录形态的对应用例在 `tests/test_layoutdir.py`。"""
    from tavotto.engine import layoutdir

    monkeypatch.setattr(layoutdir, "WRITER_ENABLED", False)
    monkeypatch.setenv(layoutdir.WRITER_ENV, "0")


def test_canvas_svg_export_writes_names_groups_and_one_clip_and_passes_strict_inspection(project):
    """ADR 0208 §七之三 端到端：载荷 → 真实导出作业 → 产物检视（strict）。名字（中文）写进 `id`、文档的组是嵌套 `<g>`、
    剪切组的蒙版只写一次；同一份载荷的 PDF 照常交付。"""
    client, _ = project
    mask = {
        "type": "path",
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 20,
        "h_mm": 12,
        "group": "g_zoom",
        "subpaths": [
            {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}]}
        ],
    }

    def rect(i, x, name, **kw):
        return {
            "type": "shape",
            "shape": "rect",
            "id": i,
            "x_mm": x,
            "y_mm": 10,
            "w_mm": 20,
            "h_mm": 12,
            "stroke_pt": 1,
            "color": "#333333",
            "fill": "#E69F00",
            "name": name,
            **kw,
        }

    value = {
        "scope": "canvas",
        "filename": "result",
        "formats": ["svg", "pdf"],
        "overwrite": "replace",
        "inspection": {"mode": "strict"},
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 90,
            "objects": [
                rect("a", 5, "细胞膜", group_path=["g_all"]),
                rect("b", 30, "放大 细胞核", group_path=["g_all", "g_zoom"], clip=[mask]),
                rect("c", 55, "染色质", group_path=["g_all", "g_zoom"], clip=[mask]),
            ],
        },
    }
    body, outputs = output(client, value)
    assert body["status"] == "done", body
    assert outputs["pdf"]["status"] == "done"
    assert outputs["svg"]["status"] == "done", body
    data = (Path(body["export_dir"]) / outputs["svg"]["name"]).read_bytes()
    root = ET.fromstring(data)
    ns = "{http://www.w3.org/2000/svg}"
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    assert {"细胞膜", "放大_细胞核", "染色质"} <= set(ids)
    assert len(ids) == len(set(ids))
    assert data.count(b"<clipPath") == 1 and data.count(b"clip-path=") == 1
    groups = [g for g in root.iter(f"{ns}g") if set(g.attrib) <= {"id", "clip-path"}]
    assert len(groups) == 2  # g_all 与它里面的 g_zoom（页面翻转用的根 `<g>` 带变换，不算文档的组）
