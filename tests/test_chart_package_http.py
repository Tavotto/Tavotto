"""v4 备份包真实 HTTP/worker 闭环；不将检视或测试消费冒充生产安装。"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from tavotto import app as m, chartbundle, security
from tavotto.engine import (
    atomicio,
    brand,
    chartpackage,
    config,
    documentassets,
    figcapture,
    packagebudget,
    patchspec,
    pool,
    runtimeasset,
    svgstore,
)
from test_chart_native_api import SOURCE, _bind, _color, _render, project as _native_project
from test_chart_native_export import _export, _original

project = _native_project
REPO = Path(__file__).resolve().parents[1]
ENDPOINT = "/api/documents/v4/package"


@pytest.fixture
def env(project):
    client, root = project
    pid = m._project_id(root)
    with m.app.test_request_context(ENDPOINT + "?pj=" + pid):
        output = m.project_export_dir()
    return client, root, output, pid


def document(*charts):
    return {
        "schema": 4,
        "project": {"id": "source_project_label", "name": "计算备份 中文"},
        "canvases": [
            {
                "id": f"canvas_{index}",
                "name": f"图 {index}",
                "page": {"w": 100, "h": 80},
                "objects": [panel(chart, edits)] if chart is not None else [],
                "guides": [],
            }
            for index, (chart, edits) in enumerate(charts or [(None, [])], 1)
        ],
        "activeCanvasId": "canvas_1",
        "createdAt": 11,
        "updatedAt": 22,
        "user_extra": {"text": "不丢未知正文"},
    }


def panel(chart=None, edits=(), *, file="chart.svg", number=1):
    return {
        "id": f"panel_{number}",
        "type": "panel",
        "fileId": file,
        "fileKind": "pdf" if runtimeasset.is_runtime_id(file) else "svg",
        "nativeW": 100 * 25.4 / 72,
        "nativeH": 100 * 25.4 / 72,
        "x": 5,
        "y": 7,
        "w": 100 * 25.4 / 72,
        "h": 100 * 25.4 / 72,
        "overrides": copy.deepcopy(list(edits)),
        **({"chart": copy.deepcopy(chart)} if chart is not None else {}),
    }


def post(env, doc=None, **kwargs):
    client, _root, _output, pid = env
    return client.post(
        ENDPOINT + "?pj=" + pid,
        json={
            "stem": "http_v4",
            "doc": document() if doc is None else doc,
            "settings": {"dpi": 600},
        },
        **kwargs,
    )


def directory_bytes(output):
    return {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}


def read_package(env, response):
    _client, _root, output, _pid = env
    body = response.get_json()
    assert response.status_code == 200, body
    expected = {"name", "url", "assets", "missing", "document_format", "chart_sources"}
    if "execution_coverage" in body:
        expected.add("execution_coverage")
    assert set(body) == expected
    assert body["document_format"] == "v4"
    assert body["name"].startswith("http_v4_") and body["name"].endswith(brand.PACKAGE_EXT)
    assert body["url"] == "/exports/" + body["name"]
    path = output / body["name"]
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        members = {name: archive.read(name) for name in names}
    budget = packagebudget.output_budget({name: len(data) for name, data in members.items()})
    with chartpackage.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        stored, manifest = ready.document, ready.manifest
        assert manifest["kind"] == brand.PACKAGE_KIND
        assert manifest["document_format"] == "v4"
        assert manifest["version"] == 1
        assert ("execution_coverage" in body) == ("execution" in manifest)
        if "execution" in manifest:
            assert body["execution_coverage"] == manifest["execution"]["coverage"]
        rows = {item.record["path"]: item.record for item in ready.resources}
        assert set(rows) == set(members) - {"layout.json", "package_manifest.json"}
        for name, row in rows.items():
            assert row["sha256"] == hashlib.sha256(members[name]).hexdigest()
            assert row["bytes"] == len(members[name])
    return body, stored, manifest, members, budget


def freeze_named(env, doc):
    client, _root, _output, pid = env
    response = client.post(
        "/api/documents/v4/layouts/PackageFixture?target=project&base_revision=absent&pj=" + pid,
        json=doc,
    )
    assert response.status_code == 200, response.get_json()
    response = client.get("/api/documents/v4/layouts/PackageFixture?pj=" + pid)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def probe_runtime(env):
    client, root, _output, pid = env
    script = (
        "def main():\n    import matplotlib.pyplot as plt\n"
        "    fig, ax = plt.subplots(figsize=(2, 1))\n"
        '    ax.plot([0, 1], [1, 0])\n    fig.savefig("F.pdf")\n'
    )
    (root / "plot.py").write_text(script, encoding="utf-8")
    response = client.post("/api/registry/probe?pj=" + pid, json={"script": "plot.py"})
    assert response.status_code == 200 and response.get_json().get("registered"), (
        response.get_json()
    )
    return figcapture.runtime_asset_id("plot.py", "F"), (root / "plot.py").read_bytes()


def test_empty_v4_package_has_explicit_format_and_zero_sources(env):
    body, stored, manifest, members, _budget = read_package(env, post(env))
    assert stored == document()
    assert body["assets"] == body["chart_sources"] == 0 and body["missing"] == []
    assert manifest["export_settings"] == {"dpi": 600}
    assert set(members) == {"layout.json", "package_manifest.json"}


def test_real_mixed_package_keeps_all_canvases_exact_outer_input_and_ordered_edits(
    env, monkeypatch
):
    client, root, _output, _pid = env
    first = _bind(client, y=0.25)
    edits = [
        _color("#00ff00"),
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5},
        _color("#ff0000"),
    ]
    produced = _export(client, original=_original(first, edits), formats=("svg",), name="input_v2")[
        "svg"
    ].read_bytes()
    embedded = chartbundle.extract_chart(produced)
    assert embedded is not None and embedded.overrides == edits
    (root / "chart.svg").write_bytes(produced)
    second = _bind(client, y=0.75)
    other_edits = [_color("#0000ff")]
    runtime_id, script = probe_runtime(env)
    (root / "ordinary.svg").write_bytes(SOURCE)
    (root / "F.svg").write_bytes(SOURCE.replace(b"Source keep", b"Ordinary F"))
    doc = document((first, edits), (second, other_edits))
    doc["canvases"][0]["objects"].append(panel(file=runtime_id, number=2))
    doc["canvases"][1]["objects"].extend(
        [
            panel(file="ordinary.svg", number=3),
            panel(file="F.svg", number=4),
            panel(file="missing.png", number=5),
        ]
    )
    frozen = freeze_named(env, doc)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    monkeypatch.setattr(
        pool, "get", lambda *a, **k: pytest.fail("package executed a cold user script")
    )
    censuses = []
    original = packagebudget.output_budget

    def budget_factory(census):
        censuses.append(dict(census))
        return original(census)

    monkeypatch.setattr(packagebudget, "output_budget", budget_factory)
    response = post(env, frozen)
    # reader helper also derives a budget; isolate the producer call count first.
    assert len(censuses) == 1
    body, stored, manifest, members, _budget = read_package(env, response)
    assert censuses[0] == {name: len(data) for name, data in members.items()}
    assert body["assets"] == 3 and body["chart_sources"] == 1
    assert body["missing"] == ["missing.png"]
    assert stored == frozen
    rows = manifest["assets"]
    assert {row["kind"] for row in rows} == {"chart_source", "file", "script", "figure_snapshot"}
    assert members["scripts/plot.py"] == script
    assert {row["id"] for row in rows if row["kind"] == "file"} == {
        "chart.svg",
        "ordinary.svg",
        "F.svg",
    }
    assert members["assets/chart.svg"] == produced
    assert members["assets/ordinary.svg"] == SOURCE
    assert members["assets/F.svg"] == (root / "F.svg").read_bytes()
    assert manifest["runtime_assets"][0]["id"] == runtime_id
    assert manifest["scripts"] == ["plot.py"]
    source_rows = [row for row in rows if row["kind"] == "chart_source"]
    assert len(source_rows) == 1 and members[source_rows[0]["path"]] == produced
    for index, (state, ordered) in enumerate(((first, edits), (second, other_edits))):
        current = stored["canvases"][index]["objects"][0]
        assert current["chart"] == state and current["overrides"] == ordered
        assert current["chartSource"]["bytesSha256"] == hashlib.sha256(produced).hexdigest()
    reference = stored["canvases"][0]["objects"][1]["figureSnapshot"]
    prefix = "assets/" + config.PROJECT_STORE_DIRNAME + "/figure_snapshots/"
    snapshot = json.loads(members[prefix + "snapshots/" + reference["snapshotId"] + ".json"])[
        "payload"
    ]
    assert snapshot["identity"]["project"] == "."
    assert snapshot["identity"]["source_id"] == runtime_id
    assert snapshot["receipt"]["receipt_id"] == reference["receiptId"]
    assert (root / "chart.svg").read_bytes() == produced
    # 同一真实 cache/receipt 的两个面板，只封存一次资源，但必须逐面板补实际引用。
    # 此处不再创建/执行另一个科学 worker，也不重新计算 chart。
    for explicit in (True, False):
        duplicate_doc = document()
        copies = [copy.deepcopy(stored["canvases"][0]["objects"][1]) for _ in range(2)]
        copies[1]["id"] = "runtime_duplicate"
        if not explicit:
            for obj in copies:
                obj.pop("figureSnapshot")
        duplicate_doc["canvases"][0]["objects"] = copies
        response = post(env, duplicate_doc)
        duplicate_body, duplicate_stored, duplicate_manifest, _members, _budget = read_package(
            env, response
        )
        refs = [obj.get("figureSnapshot") for obj in duplicate_stored["canvases"][0]["objects"]]
        assert refs == [reference, reference], {"explicit": explicit, "references": refs}
        assert duplicate_body["missing"] == [], {"explicit": explicit, "body": duplicate_body}
        assert duplicate_body["assets"] == duplicate_body["chart_sources"] == 0
        assert duplicate_manifest["figure_snapshots"] == 1
    unknown = figcapture.runtime_asset_id("unregistered.py", "Unknown")
    unknown_doc = document()
    unknown_doc["canvases"][0]["objects"].append(panel(file=unknown))
    unknown_body, _stored, unknown_manifest, _members, _budget = read_package(
        env, post(env, unknown_doc)
    )
    assert unknown in unknown_body["missing"]
    assert unknown in unknown_manifest["svg_snapshot_warnings"]
    # 已知的历史图配对仍可携带，缺掉的源脚本必须如实标记，不能偷偷重新执行。
    (root / "plot.py").unlink()
    missing_script = document()
    missing_script["canvases"][0]["objects"].append(
        copy.deepcopy(stored["canvases"][0]["objects"][1])
    )
    missing_body, _stored, missing_manifest, missing_members, _budget = read_package(
        env, post(env, missing_script)
    )
    assert "plot.py" in missing_body["missing"]
    assert "plot.py" in missing_manifest["missing_at_pack_time"]
    assert "scripts/plot.py" not in missing_members


def test_missing_plain_source_is_explicit_in_an_incomplete_backup(env):
    doc = document()
    doc["canvases"][0]["objects"].append(panel(file="missing.svg"))
    body, stored, manifest, _members, _budget = read_package(env, post(env, doc))
    assert body["assets"] == body["chart_sources"] == 0
    assert body["missing"] == manifest["missing_at_pack_time"] == ["missing.svg"]
    assert stored == doc


def test_outside_project_resource_is_rejected_before_reading_it(env, monkeypatch):
    _client, root, output, _pid = env
    outside = root.parent / "outside.svg"
    outside.write_bytes(b"must not read or package this external resource")
    original = figcapture.hash_file

    def only_project_source(path, **kwargs):
        assert Path(path).resolve() != outside.resolve(), "external resource was read"
        return original(path, **kwargs)

    monkeypatch.setattr(figcapture, "hash_file", only_project_source)
    doc = document()
    doc["canvases"][0]["objects"].append(panel(file="../outside.svg"))
    before = directory_bytes(output)
    response = post(env, doc)
    assert response.status_code == 403, response.get_json()
    assert response.get_json()["code"] == "package_source_outside_project"
    assert directory_bytes(output) == before


def test_valid_frozen_chart_input_needs_no_original_file_and_is_not_missing(env):
    client, root, _output, _pid = env
    frozen = freeze_named(env, document((_bind(client), [_color("#ff0000")])))
    (root / "chart.svg").unlink()
    body, stored, _manifest, _members, _budget = read_package(env, post(env, frozen))
    assert body["chart_sources"] == 1 and body["assets"] == 0 and body["missing"] == []
    assert stored == frozen and not (root / "chart.svg").exists()


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_explicit_missing_or_damaged_chart_attachment_never_falls_back_to_valid_original(
    env, fault
):
    client, root, output, _pid = env
    frozen = freeze_named(env, document((_bind(client), [])))
    read_package(env, post(env, frozen))
    before = directory_bytes(output)
    ref = frozen["canvases"][0]["objects"][0]["chartSource"]
    path = documentassets.source_path(root / config.PROJECT_STORE_DIRNAME, ref)
    path.unlink() if fault == "missing" else path.write_bytes(b"corrupt immutable INPUT")
    response = post(env, frozen)
    assert 400 <= response.status_code < 500, response.get_json()
    assert response.get_json()["code"] == "chart_source_mismatch"
    assert directory_bytes(output) == before
    assert (root / "chart.svg").read_bytes() == SOURCE


@pytest.mark.parametrize("schema", [2, 3, 5])
def test_v4_package_rejects_other_document_schemas_without_output(env, schema):
    _client, _root, output, _pid = env
    doc = document()
    doc["schema"] = schema
    before = directory_bytes(output)
    response = post(env, doc)
    assert response.status_code == 400, response.get_json()
    assert directory_bytes(output) == before


def test_explicit_unknown_project_never_packages_default_project(env):
    client, _root, output, _pid = env
    before = directory_bytes(output)
    response = client.post(ENDPOINT + "?pj=missing-project", json={"doc": document()})
    assert response.status_code == 409 and response.get_json()["code"] == "no_project"
    assert directory_bytes(output) == before


def test_new_v4_package_is_covered_by_real_session_guard_and_rejects_scoped_token(env, monkeypatch):
    _client, _root, output, _pid = env
    state = security.SessionState("nonce", api_secret="package-http-full-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    before = directory_bytes(output)
    for headers in ({}, {"X-Tavotto-Auth": state.status_token}):
        response = post(env, headers=headers)
        assert response.status_code == 401, response.get_json()
    assert directory_bytes(output) == before
    read_package(env, post(env, headers={"X-Tavotto-Auth": state.api_secret}))


@pytest.mark.parametrize("phase", ["add", "close", "publish"])
def test_real_zip_io_failure_preserves_every_preexisting_output(env, monkeypatch, phase):
    _client, _root, output, _pid = env
    read_package(env, post(env))
    before = directory_bytes(output)
    hits = []

    def fail():
        hits.append(phase)
        raise OSError(errno.ENOSPC, "injected v4 package volume full")

    if phase == "add":
        original = zipfile.ZipFile.open

        def open_member(archive, name, mode="r", *args, **kwargs):
            member = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if mode == "w" and member == "layout.json":
                fail()
            return original(archive, name, mode, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "open", open_member)
    elif phase == "close":
        original = zipfile.ZipFile.close

        def close(archive):
            writing = archive.mode == "w" and archive.fp is not None
            original(archive)
            if writing:
                fail()

        monkeypatch.setattr(zipfile.ZipFile, "close", close)
    else:
        original = atomicio.publish_file_if_absent

        def publish(source, target):
            if str(target).endswith(brand.PACKAGE_EXT):
                fail()
            return original(source, target)

        monkeypatch.setattr(atomicio, "publish_file_if_absent", publish)
    response = post(env)
    assert hits == [phase]
    assert response.status_code == 500, response.get_json()
    assert directory_bytes(output) == before
    assert {p.name for p in output.iterdir()} == set(before)


def test_same_name_collision_never_overwrites_an_existing_v4_package(env, monkeypatch):
    _client, _root, output, _pid = env
    original_time, original_token = time.strftime, m.secrets.token_hex
    instant = time.localtime(1_700_000_000)
    monkeypatch.setattr(time, "strftime", lambda pattern, *_args: original_time(pattern, instant))
    monkeypatch.setattr(
        m.secrets, "token_hex", lambda size: "aabbccdd" if size == 4 else original_token(size)
    )
    read_package(env, post(env))
    before = directory_bytes(output)
    changed = document()
    changed["project"]["name"] = "cannot replace prior body"
    response = post(env, changed)
    assert response.status_code == 500, response.get_json()
    assert directory_bytes(output) == before
    assert {p.name for p in output.iterdir()} == set(before)


def test_explicit_figure_snapshot_damage_is_not_silently_dropped_from_backup(env):
    _client, root, output, _pid = env
    runtime, _script = probe_runtime(env)
    doc = document()
    doc["canvases"][0]["objects"].append(panel(file=runtime))
    frozen = freeze_named(env, doc)
    read_package(env, post(env, frozen))
    before = directory_bytes(output)
    ref = frozen["canvases"][0]["objects"][0]["figureSnapshot"]
    record = svgstore.load_snapshot(
        root,
        ref,
        source_id=runtime,
        patch_hash=patchspec.patch_hash([]),
        storage_root=m._svg_storage_root(root),
    )
    assert record is not None
    record["svg_path"].write_bytes(b"corrupt referenced SVG blob")
    response = post(env, frozen)
    assert response.status_code >= 400, response.get_json()
    assert directory_bytes(output) == before


def test_package_chart_replays_through_real_http_in_new_parent_after_original_deleted(
    env, tmp_path
):
    client, root, _output, _pid = env
    state = _bind(client)
    edits = [
        _color("#00ff00"),
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 4},
        _color("#ff0000"),
    ]
    hot_without_ref = _render(client, state, edits)
    body, stored, _manifest, members, _budget = read_package(
        env, post(env, document((state, edits)))
    )
    assert body["chart_sources"] == 1
    moved = tmp_path / "新父进程项目"
    moved.mkdir()
    ref = stored["canvases"][0]["objects"][0]["chartSource"]
    frozen_panel = stored["canvases"][0]["objects"][0]
    assert frozen_panel["chart"] == state and frozen_panel["overrides"] == edits
    response = client.post(
        "/api/engine/render",
        json={
            "id": "chart.svg",
            "chart": state,
            "chart_source": ref,
            "patches": edits,
            "inline_svg": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    hot = response.get_json()
    assert hot["svg"] == hot_without_ref["svg"]
    assert hot["manifest"] == hot_without_ref["manifest"]
    # 只消费已核验的指定 chartSource；这不是新增生产安装 API 或 extractall。
    member = (
        "assets/"
        + config.PROJECT_STORE_DIRNAME
        + "/"
        + documentassets.CHART_SOURCES_DIRNAME
        + "/"
        + ref["bytesSha256"]
        + ".svg"
    )
    assert (
        documentassets.freeze_source(
            moved / config.PROJECT_STORE_DIRNAME,
            ref["sourceId"],
            members[member],
            scene_digest=ref["sceneDigest"],
        )
        == ref
    )
    (root / "chart.svg").unlink()
    code = r"""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tavotto import app as m,chartbundle
from tavotto.engine import chartprofile
req=json.load(sys.stdin)
m.app.config['TESTING']=True
try:
    opened=m.open_project(sys.argv[2],make_default=False)
    client=m.app.test_client(); suffix='?pj='+opened['id']
    saved=client.post('/api/documents/v4/layouts/Cold'+suffix+'&target=project&base_revision=absent',json=req['document'])
    assert saved.status_code==200,saved.get_json()
    restored=client.get('/api/documents/v4/layouts/Cold'+suffix).get_json()
    panel=restored['canvases'][0]['objects'][0]
    rendered=client.post('/api/engine/render'+suffix,json={'id':panel['fileId'],'chart':panel['chart'],'chart_source':panel['chartSource'],'patches':panel['overrides'],'inline_svg':True})
    assert rendered.status_code==200,rendered.get_json()
    result=rendered.get_json()
    assert result['chart_revision']==req['hot']['chart_revision']
    assert result['manifest']==req['hot']['manifest'] and result['svg']==req['hot']['svg']
    exported=client.post('/api/export'+suffix,json={'scope':'original','formats':['svg'],'filename':'cold-package','original':{'figure_id':panel['fileId'],'chart':panel['chart'],'chart_source':panel['chartSource'],'chart_render_profile':chartprofile.NATIVE_PROFILE,'overrides':panel['overrides']}})
    assert exported.status_code==200,exported.get_json()
    job=exported.get_json(); item=job['outputs'][0]
    assert item['status']=='done',job
    raw=(Path(job['export_dir'])/item['name']).read_bytes()
    bundle=chartbundle.extract_chart(raw)
    assert bundle is not None and bundle.chart==panel['chart'] and bundle.overrides==panel['overrides']
    assert '#ff0000' in raw.decode()
    assert not (Path(sys.argv[2])/panel['fileId']).exists()
    print(json.dumps({'replayed':True,'revision':result['chart_revision'],'exported':True}))
finally:
    m.reset_projects(wait=True)
"""
    process = subprocess.run(
        [sys.executable, "-I", "-c", code, str(REPO / "src"), str(moved)],
        input=json.dumps({"document": stored, "hot": hot}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={
            **os.environ,
            "TAVOTTO_DATA_DIR": str(tmp_path / "cold-data"),
            "TAVOTTO_CONFIG_DIR": str(tmp_path / "cold-config"),
            "TAVOTTO_NO_TELEMETRY": "1",
        },
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["replayed"] is True
