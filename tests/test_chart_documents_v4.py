"""schema 4 的真实 HTTP 保存/恢复闭环；不伪造来源、计算结果或生产导出。

新父进程只收到项目目录和文档名，通过产品入口读取正文、消费附件、编辑并导出。
故障只注入文档原子提交的 os.replace 边界，不修改计算生产者。
"""

from __future__ import annotations

import base64
import errno
import hashlib
import io
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

from tavotto import app as m, chartbundle
from tavotto.engine import atomicio, chartprofile, documentassets, documents, layoutsession, pool
from test_chart_native_api import SOURCE, _bind, _color, project as _native_project
from test_chart_native_export import _assert_svg_color

V4 = "/api/documents/v4"
PT_MM = 25.4 / 72
project = _native_project


@pytest.fixture
def env(project, tmp_path, monkeypatch):
    client, root = project
    data = tmp_path / "data"
    monkeypatch.setattr(m, "LAYOUT_DIR", data / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", data / "layouts/_autosave")
    monkeypatch.setattr(m, "VERSIONS_DIR", data / "layouts/_versions")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    return client, root, data


def _document(chart, *, canvases=1):
    return {
        "schema": 4,
        "project": {"id": "p_chart", "name": "计算图表项目"},
        "canvases": [
            {
                "id": f"c{i + 1}",
                "name": f"Figure {i + 1}",
                "page": {"w": 100, "h": 80},
                "objects": [
                    {
                        # 跨画布同名合法；保存回执必须靠 canvas/object 双定位。
                        "id": "panel_repeat",
                        "type": "panel",
                        "fileId": "chart.svg",
                        "fileKind": "svg",
                        "nativeW": 100 * PT_MM,
                        "nativeH": 100 * PT_MM,
                        "x": 5,
                        "y": 7,
                        "w": 100 * PT_MM,
                        "h": 100 * PT_MM,
                        "overrides": [
                            _color("#00ff00"),
                            {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4},
                            _color("#ff0000" if i == 0 else "#0000ff"),
                        ],
                        "chart": deepcopy(chart),
                    }
                ],
                "guides": [],
            }
            for i in range(canvases)
        ],
        "activeCanvasId": "c1",
        "createdAt": 1,
        "updatedAt": 2,
    }


def _legacy_document(schema=3):
    if schema == 2:
        return {"schema": 2, "name": "legacy", "page": {"w": 100, "h": 80}, "objects": []}
    return {
        "schema": 3,
        "project": {"id": "p_legacy", "name": "旧排版"},
        "canvases": [{"id": "c_old", "name": "Old", "page": {"w": 100, "h": 80}, "objects": []}],
        "activeCanvasId": "c_old",
        "createdAt": 1,
        "updatedAt": 2,
    }


def _save_named(client, doc, name="Chart", *, base="absent"):
    response = client.post(
        f"{V4}/layouts/{quote(name)}?target=project&base_revision={quote(base)}", json=doc
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _get_named(client, name="Chart"):
    response = client.get(f"{V4}/layouts/{quote(name)}")
    assert response.status_code == 200, response.get_json()
    assert response.headers["X-Tavotto-Document-Format"] == "v4"
    assert response.headers["X-Tavotto-Revision"] == atomicio.revision_of(response.data)
    return response, response.get_json()


def _assert_receipt(receipt, stored, root, *, input_svg=SOURCE):
    assert receipt["document_format"] == "v4"
    expected = []
    for ci, canvas in enumerate(stored["canvases"]):
        for oi, panel in enumerate(canvas["objects"]):
            if "chart" not in panel:
                continue
            reference = panel["chartSource"]
            assert (
                documents.validate_chart_source(
                    reference,
                    source_id=panel["fileId"],
                    scene_digest=panel["chart"]["model"]["scene_digest"],
                )
                == reference
            )
            saved = documentassets.load_source(root / "tavottofile", reference)
            assert saved.input_svg == input_svg and saved.source_svg == SOURCE
            assert reference["bytesSha256"] == hashlib.sha256(input_svg).hexdigest()
            assert reference["sizeBytes"] == len(input_svg)
            assert "figureSnapshot" not in panel
            expected.append(
                {
                    "canvas_index": ci,
                    "object_index": oi,
                    "canvas_id": canvas["id"],
                    "panel_id": panel["id"],
                    "chart_source": reference,
                }
            )
    assert receipt["chart_sources"] == expected


def _render_request(panel, *, color=None):
    edits = deepcopy(panel["overrides"])
    if color is not None:
        edits.append(_color(color))
    return {
        "id": panel["fileId"],
        "chart": panel["chart"],
        "chart_source": panel["chartSource"],
        "patches": edits,
        "inline_svg": True,
    }


def _export_request(panel, *, name="restored"):
    return {
        "scope": "original",
        "formats": ["svg"],
        "filename": name,
        "overwrite": "replace",
        "original": {
            "figure_id": panel["fileId"],
            "chart": panel["chart"],
            "chart_source": panel["chartSource"],
            "chart_render_profile": chartprofile.NATIVE_PROFILE,
            "overrides": panel["overrides"],
        },
    }


def test_named_v4_save_freezes_exact_input_and_returns_unambiguous_receipts(env):
    client, root, _data = env
    original = _document(_bind(client), canvases=2)
    receipt = _save_named(client, original, "计算图表")
    response, stored = _get_named(client, "计算图表")
    _assert_receipt(receipt, stored, root)
    assert receipt["revision"] == response.headers["X-Tavotto-Revision"]
    assert receipt["file"] == "tavottofile/documents/v4/计算图表.json"
    assert unquote(response.headers["X-Tavotto-Layout-File"]) == receipt["file"]
    assert (root / receipt["file"]).read_bytes() == response.data
    recovered_input = deepcopy(stored)
    for canvas in recovered_input["canvases"]:
        canvas["objects"][0].pop("chartSource")
    assert recovered_input == original
    assert (root / "chart.svg").read_bytes() == SOURCE
    listing = client.get(f"{V4}/layouts").get_json()
    assert listing["document_format"] == "v4"
    assert listing["layouts"] == ["计算图表"]
    assert set(listing["modified"]) == {"计算图表"}
    assert "计算图表" not in client.get("/api/layouts").get_json()["layouts"]
    assert client.get("/api/layouts/计算图表").status_code == 404
    before = (root / receipt["file"]).read_bytes()
    rejected = client.post("/api/layouts/计算图表?target=project", json=stored)
    assert rejected.status_code == 400 and rejected.get_json()["code"] == "schema_too_new"
    assert (root / receipt["file"]).read_bytes() == before
    assert not (root / "tavottofile/计算图表.json").exists()


def test_v4_autosave_session_owner_candidates_and_summary_are_separate_from_legacy(env):
    client, root, data = env
    legacy = _legacy_document()
    old = client.put("/api/autosave/d_same?base_revision=absent", json=legacy)
    assert old.status_code == 200, old.get_json()
    response = client.put("/api/layout-session/last", json={"doc_id": "d_same", "name": "旧排版"})
    assert response.status_code == 200, response.get_json()
    legacy_session = client.get("/api/layout-session").get_json()
    response = client.put(f"{V4}/layout-session/last", json={"doc_id": "d_same", "name": "图表"})
    assert response.status_code == 200 and response.get_json()["state"] == "pending"
    response = client.put(
        f"{V4}/autosave/d_same?base_revision=absent", json=_document(_bind(client))
    )
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    saved = client.get(f"{V4}/autosave/d_same")
    assert saved.status_code == 200, saved.get_json()
    stored = saved.get_json()
    _assert_receipt(receipt, stored, root)
    assert saved.headers["X-Tavotto-Document-Format"] == "v4"
    assert saved.headers["X-Tavotto-Revision"] == receipt["revision"]
    assert (data / "layouts/_autosave/v4/d_same.json").read_bytes() == saved.data
    assert client.get("/api/autosave/d_same").get_json() == legacy
    assert client.get("/api/layout-session").get_json() == legacy_session
    for prefix, name, revision in (
        ("/api", "旧排版", old.get_json()["revision"]),
        (V4, "图表", receipt["revision"]),
    ):
        session = client.get(f"{prefix}/layout-session").get_json()
        assert session["last"]["doc_id"] == "d_same" and session["last"]["name"] == name
        assert session["pending"] is None
        owner = client.get(f"{prefix}/layout-session/owner?doc_id=d_same").get_json()
        assert owner["owner"] == "this" and owner["verified"] is True
        assert owner["revision"] == revision
        candidates = client.get(f"{prefix}/layout-session/candidates?scope=project").get_json()
        assert candidates["truncated"] is False
        item = next(item for item in candidates["items"] if item["doc_id"] == "d_same")
        assert item["status"] == "ok" and item["revision"] == revision
        assert item["schema"] == (4 if prefix == V4 else 3)
    summary = client.get(f"{V4}/autosave/d_same/summary").get_json()
    assert summary["schema"] == 4 and summary["objects"] == 1 and summary["canvases"] == 1
    assert summary["revision"] == receipt["revision"]
    assert client.delete("/api/autosave/d_same").status_code == 200
    assert client.get(f"{V4}/autosave/d_same").get_json() == stored
    assert client.get(f"{V4}/layout-session").get_json()["last"]["doc_id"] == "d_same"


def test_v4_versions_capture_and_restore_chart_sources_without_exposing_legacy_history(env):
    client, root, _data = env
    old = client.post("/api/versions/d_shared", json={"doc": _legacy_document(), "name": "Old"})
    assert old.status_code == 200, old.get_json()
    response = client.post(
        f"{V4}/versions/d_shared", json={"doc": _document(_bind(client)), "name": "Chart"}
    )
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    vid = receipt["version"]["id"]
    restored = client.get(f"{V4}/versions/d_shared/{vid}")
    assert restored.status_code == 200, restored.get_json()
    stored = restored.get_json()["doc"]
    _assert_receipt(receipt, stored, root)
    assert receipt["version"]["objects"] == 1
    assert [v["name"] for v in client.get(f"{V4}/versions/d_shared").get_json()["versions"]] == [
        "Chart"
    ]
    assert [v["name"] for v in client.get("/api/versions/d_shared").get_json()["versions"]] == [
        "Old"
    ]
    assert client.get(f"/api/versions/d_shared/{vid}").status_code == 404
    assert (root / "tavottofile/documents/v4/versions/d_shared.vx/index.json").is_file()
    (root / "chart.svg").unlink()
    saved = _save_named(client, stored, "FromVersion")
    _response, from_version = _get_named(client, "FromVersion")
    _assert_receipt(saved, from_version, root)
    assert from_version == stored


def test_v4_owner_evidence_uses_verified_attachments_after_source_deletion(env):
    client, root, _data = env
    saved = client.put(f"{V4}/autosave/d_detached", json=_document(_bind(client)))
    assert saved.status_code == 200, saved.get_json()
    panel = client.get(f"{V4}/autosave/d_detached").get_json()["canvases"][0]["objects"][0]
    (root / "chart.svg").unlink()
    with layoutsession.using_namespace("v4"):
        layoutsession.forget_documents(["d_detached"])
    owner = client.get(f"{V4}/layout-session/owner?doc_id=d_detached").get_json()
    assert owner["owner"] == "this" and owner["evidence"] == "assets"
    assert owner["verified"] is False  # 素材旁证不是一次已记录的owner提交。
    candidates = client.get(f"{V4}/layout-session/candidates?scope=project").get_json()
    assert any(item["doc_id"] == "d_detached" for item in candidates["items"])
    asset = documentassets.source_path(root / "tavottofile", panel["chartSource"])
    asset.write_bytes(asset.read_bytes() + b" ")
    damaged = client.get(f"{V4}/layout-session/owner?doc_id=d_detached").get_json()
    assert damaged["owner"] == "unknown" and damaged["evidence"] is None
    candidates = client.get(f"{V4}/layout-session/candidates?scope=project").get_json()
    assert not any(item["doc_id"] == "d_detached" for item in candidates["items"])


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/layouts"),
        ("GET", "/autosave/d_guard"),
        ("GET", "/layout-session"),
        ("GET", "/versions/d_guard"),
        ("POST", "/layouts/Guarded"),
        ("PUT", "/autosave/d_guard"),
        ("POST", "/versions/d_guard"),
    ],
)
def test_v4_aliases_still_require_the_existing_session_auth(env, monkeypatch, method, path):
    client, root, _data = env
    state = m.security.SessionState("fixture-nonce", api_secret="fixture-secret")
    monkeypatch.setitem(m.app.config, m.security.STATE_KEY, state)
    response = client.open(f"{V4}{path}", method=method, json={}, base_url="http://127.0.0.1:5089")
    assert response.status_code == 401
    assert not (root / "tavottofile/documents").exists()
    authorized = client.get(
        f"{V4}/layouts",
        base_url="http://127.0.0.1:5089",
        headers={"X-Tavotto-Auth": "fixture-secret"},
    )
    assert authorized.status_code == 200
    assert authorized.get_json()["document_format"] == "v4"


FRESH_PARENT = r"""
import base64,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tavotto import app as m
from tavotto.engine import exportjob,pool,project_watch
from tavotto.rendercore import renderhost
m.app.config['TESTING']=True
root=Path(sys.argv[2]);m.open_project(str(root));client=m.app.test_client()
formats=json.loads(sys.argv[3]) if len(sys.argv)>3 else ['svg']
try:
    response=client.get('/api/documents/v4/layouts/Portable')
    assert response.status_code==200,response.get_json()
    panel=response.get_json()['canvases'][0]['objects'][0]
    edits=panel['overrides']+[{'gid':'axes_0.lines_0','prop':'color','value':'#00ff00'}]
    rendered=client.post('/api/engine/render',json={
        'id':panel['fileId'],'chart':panel['chart'],'chart_source':panel['chartSource'],
        'patches':edits,'inline_svg':True})
    assert rendered.status_code==200,rendered.get_json()
    preview=None
    if 'png' in formats:
        preview_response=client.post('/api/engine/preview_png',json={
            'id':panel['fileId'],'chart':panel['chart'],'chart_source':panel['chartSource'],
            'patches':edits,'w':230,'with_manifest':True})
        assert preview_response.status_code==200,preview_response.get_json()
        preview=preview_response.get_json()
    produced=client.post('/api/export',json={
        'scope':'original','formats':formats,'ppi':144,'filename':'fresh-parent','overwrite':'replace',
        'original':{'figure_id':panel['fileId'],'chart':panel['chart'],
        'chart_source':panel['chartSource'],'chart_render_profile':'native-manifest/1','overrides':edits}})
    assert produced.status_code==200,produced.get_json()
    job=produced.get_json();assert job['status']=='done',job
    output_bytes={}
    for item in job['outputs']:
        assert item['status']=='done',job
        raw=(Path(job['export_dir'])/item['name']).read_bytes()
        output_bytes[item['format']+'_b64']=base64.b64encode(raw).decode()
    print('RESULT='+json.dumps({'rendered':rendered.get_json(),'preview':preview,'edits':edits,**output_bytes}))
finally:
    pool.shutdown_all(figures_dir=str(root),wait=True)
    m.reset_projects();project_watch.stop();exportjob.reset_for_tests();renderhost.shutdown_shared()
"""


def _fresh_parent(root, *, formats=("svg",)):
    pool.shutdown_all(figures_dir=str(root), wait=True)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            FRESH_PARENT,
            str(Path(__file__).resolve().parents[1] / "src"),
            str(root),
            json.dumps(list(formats)),
        ],
        env=dict(os.environ),
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode(
        "utf-8", errors="replace"
    ) + result.stdout.decode("utf-8", errors="replace")
    lines = [
        line[7:]
        for line in result.stdout.decode("utf-8").splitlines()
        if line.startswith("RESULT=")
    ]
    assert len(lines) == 1, result.stdout
    return json.loads(lines[0])


def test_saved_attachment_reopens_in_a_fresh_parent_after_source_deletion_and_exports_edits(env):
    client, root, _data = env
    receipt = _save_named(client, _document(_bind(client)), "Portable")
    document_path = root / receipt["file"]
    before = document_path.read_bytes()
    (root / "chart.svg").unlink()
    evidence = _fresh_parent(root)
    rendered = evidence["rendered"]
    _assert_svg_color(rendered["svg"], "#00ff00")
    line = next(e for e in rendered["manifest"]["elements"] if e["gid"] == "axes_0.lines_0")
    for actual, expected in zip(
        line["geometry"]["paths"][0]["points"], [[0.1, 0.7], [0.9, 0.7]], strict=True
    ):
        assert actual == pytest.approx(expected, abs=1e-7)
    exported = base64.b64decode(evidence["svg_b64"], validate=True)
    _assert_svg_color(exported, "#00ff00")
    bundle = chartbundle.extract_chart(exported)
    assert bundle is not None and bundle.source_svg == SOURCE
    assert bundle.render_profile == chartprofile.NATIVE_PROFILE
    assert bundle.overrides == evidence["edits"]
    assert document_path.read_bytes() == before and not (root / "chart.svg").exists()


def test_production_v2_outer_input_is_saved_exactly_and_replayed_in_fresh_parent(env):
    from PIL import Image

    client, root, _data = env
    chart = _bind(client)
    doc = _document(chart)
    panel = doc["canvases"][0]["objects"][0]
    outer_edits = deepcopy(panel["overrides"])
    exported = client.post(
        "/api/export",
        json={
            "scope": "original",
            "formats": ["svg"],
            "filename": "outer-input",
            "overwrite": "replace",
            "original": {
                "figure_id": "chart.svg",
                "chart": chart,
                "chart_render_profile": chartprofile.NATIVE_PROFILE,
                "overrides": outer_edits,
            },
        },
    )
    assert exported.status_code == 200, exported.get_json()
    job = exported.get_json()
    assert job["status"] == "done", job
    item = next(item for item in job["outputs"] if item["format"] == "svg")
    assert item["status"] == "done", job
    outer = (Path(job["export_dir"]) / item["name"]).read_bytes()
    produced_bundle = chartbundle.extract_chart(outer)
    assert produced_bundle is not None and produced_bundle.source_svg == SOURCE
    assert produced_bundle.chart == chart and produced_bundle.overrides == outer_edits
    assert produced_bundle.render_profile == chartprofile.NATIVE_PROFILE
    assert outer != SOURCE
    _assert_svg_color(outer, "#ff0000")
    (root / "wrapped.svg").write_bytes(outer)
    panel["fileId"] = "wrapped.svg"
    panel["overrides"].append({"gid": "axes_0", "prop": "xlim", "value": [0, 2]})
    expected = deepcopy(panel)
    receipt = _save_named(client, doc, "Portable")
    response, stored = _get_named(client, "Portable")
    _assert_receipt(receipt, stored, root, input_svg=outer)
    restored_panel = stored["canvases"][0]["objects"][0]
    reference = restored_panel["chartSource"]
    assert reference["sceneDigest"] == chart["model"]["scene_digest"]
    assert reference["bytesSha256"] == hashlib.sha256(outer).hexdigest()
    assert reference["bytesSha256"] != hashlib.sha256(SOURCE).hexdigest()
    assert {k: v for k, v in restored_panel.items() if k != "chartSource"} == expected
    document_path = root / receipt["file"]
    before = document_path.read_bytes()
    assert before == response.data
    (root / "chart.svg").unlink()
    (root / "wrapped.svg").unlink()
    evidence = _fresh_parent(root, formats=("svg", "png"))
    rendered, preview = evidence["rendered"], evidence["preview"]
    assert preview["chart_revision"] == rendered["chart_revision"]
    assert preview["manifest"] == rendered["manifest"]
    line = next(e for e in rendered["manifest"]["elements"] if e["gid"] == "axes_0.lines_0")
    for actual, expected_point in zip(
        line["geometry"]["paths"][0]["points"], [[0.1, 0.7], [0.5, 0.7]], strict=True
    ):
        assert actual == pytest.approx(expected_point, abs=1e-7)
    final_svg = base64.b64decode(evidence["svg_b64"], validate=True)
    _assert_svg_color(final_svg, "#00ff00")
    final_bundle = chartbundle.extract_chart(final_svg)
    assert final_bundle is not None and final_bundle.source_svg == SOURCE
    assert final_bundle.chart == chart
    assert final_bundle.overrides == expected["overrides"] + [_color("#00ff00")]
    for raw, size, scale in (
        (base64.b64decode(evidence["png_b64"], validate=True), 200, 2),
        (base64.b64decode(preview["png"], validate=True), 400, 4),
    ):
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == (size, size)
            pixels = image.convert("RGB")
        red, green, blue = pixels.getpixel((30 * scale, 70 * scale))
        assert green > 180 and red < 100 and blue < 100
        assert min(pixels.getpixel((70 * scale, 70 * scale))) > 245
    assert document_path.read_bytes() == before
    assert not (root / "chart.svg").exists() and not (root / "wrapped.svg").exists()


def test_document_replace_failure_preserves_previous_body_revision_and_attachment(env, monkeypatch):
    client, root, _data = env
    receipt = _save_named(client, _document(_bind(client)))
    response, stored = _get_named(client)
    path = root / receipt["file"]
    before = path.read_bytes()
    reference = stored["canvases"][0]["objects"][0]["chartSource"]
    asset = documentassets.source_path(root / "tavottofile", reference)
    asset_before = asset.read_bytes()
    original_replace = os.replace
    intercepted = []

    def fail_publish(src, dst):
        if Path(dst).resolve() == path.resolve():
            intercepted.append(Path(src).read_bytes())
            raise OSError(errno.ENOSPC, "injected full disk at document publish")
        return original_replace(src, dst)

    changed = deepcopy(stored)
    changed["updatedAt"] = 3
    monkeypatch.setattr(os, "replace", fail_publish)
    failed = client.post(
        f"{V4}/layouts/Chart?target=project&base_revision={receipt['revision']}", json=changed
    )
    assert failed.status_code == 500 and failed.get_json()["code"] == "replace_failed"
    assert len(intercepted) == 1 and json.loads(intercepted[0])["updatedAt"] == 3
    assert path.read_bytes() == before == response.data
    assert atomicio.content_revision(path) == receipt["revision"]
    assert asset.read_bytes() == asset_before
    assert not list(path.parent.glob(f"{path.name}.*.tmp"))


@pytest.mark.parametrize("fault", ["missing", "tampered"])
def test_missing_or_tampered_attachment_refuses_save_render_and_export_without_fallback(env, fault):
    client, root, _data = env
    receipt = _save_named(client, _document(_bind(client)))
    _response, stored = _get_named(client)
    path = root / receipt["file"]
    before = path.read_bytes()
    panel = stored["canvases"][0]["objects"][0]
    asset = documentassets.source_path(root / "tavottofile", panel["chartSource"])
    if fault == "missing":
        asset.unlink()
    else:
        asset.write_bytes(asset.read_bytes() + b" ")
    damaged = asset.read_bytes() if asset.exists() else None
    changed = deepcopy(stored)
    changed["updatedAt"] = 3
    refused = client.post(
        f"{V4}/layouts/Chart?target=project&base_revision={receipt['revision']}", json=changed
    )
    assert 400 <= refused.status_code < 500, refused.get_json()
    assert refused.get_json()["code"] == "chart_source_mismatch"
    rendered = client.post("/api/engine/render", json=_render_request(panel, color="#00ff00"))
    assert rendered.status_code == 422, rendered.get_json()
    assert rendered.get_json()["code"] == "chart_source_mismatch"
    exported = client.post("/api/export", json=_export_request(panel, name="must-not-exist"))
    assert exported.status_code == 500, exported.get_json()
    job = exported.get_json()
    assert job["status"] == "failed", job
    assert job["outputs"][0]["error"]["code"] == "chart_source_mismatch", job
    assert job["outputs"][0]["name"] is None
    assert not (Path(job["export_dir"]) / "must-not-exist.svg").exists()
    assert path.read_bytes() == before and atomicio.content_revision(path) == receipt["revision"]
    assert (asset.read_bytes() if asset.exists() else None) == damaged
    assert (root / "chart.svg").read_bytes() == SOURCE


@pytest.mark.parametrize("schema", [2, 3])
def test_legacy_schema_round_trip_stays_unchanged_and_v4_never_overwrites_it(env, schema):
    client, root, _data = env
    legacy = _legacy_document(schema)
    old = client.post("/api/layouts/Legacy?target=project", json=legacy)
    assert old.status_code == 200, old.get_json()
    path = root / "tavottofile/Legacy.json"
    before = path.read_bytes()
    got = client.get("/api/layouts/Legacy")
    assert got.status_code == 200 and got.get_json() == legacy
    assert "X-Tavotto-Document-Format" not in got.headers
    assert "document_format" not in old.get_json() and "chart_sources" not in old.get_json()
    response = client.post(f"{V4}/layouts/Legacy?target=project", json=legacy)
    assert response.status_code == 400, response.get_json()
    assert path.read_bytes() == before
    assert client.get(f"{V4}/layouts/Legacy").status_code == 404
    assert "Legacy" not in client.get(f"{V4}/layouts").get_json()["layouts"]


@pytest.mark.parametrize("schema", [2, 3])
def test_legacy_unknown_chart_field_keeps_the_existing_figure_reference_validation(env, schema):
    client, _root, _data = env
    legacy = _legacy_document(schema)
    objects = legacy["objects"] if schema == 2 else legacy["canvases"][0]["objects"]
    objects.append(
        {
            "id": "p_unknown_extension",
            "type": "panel",
            "fileId": "chart.svg",
            "fileKind": "svg",
            "chart": {},  # 旧格式允许未知扩展字段，不能因此绕过既有快照核验。
            "overrides": [],
            "figureSnapshot": {
                "version": 1,
                "sourceId": "chart.svg",
                "patchHash": "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
                "receiptId": "sha256:" + "1" * 64,
                "snapshotId": "2" * 64,
            },
        }
    )
    response = client.post("/api/layouts/LegacyExtension?target=project", json=legacy)
    assert response.status_code == 200, response.get_json()
    stored = client.get("/api/layouts/LegacyExtension").get_json()
    objects = stored["objects"] if schema == 2 else stored["canvases"][0]["objects"]
    assert objects[0]["chart"] == {}
    assert "figureSnapshot" not in objects[0]
    assert response.get_json()["figure_snapshots"] == {}
