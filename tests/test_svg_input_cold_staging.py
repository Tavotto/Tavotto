"""Application cold staging preserves actual captured input bytes.

Most checks use the admitted opaque storage pair; the formal v4 reader uses a
readable vector pair. These checks qualify staging/ZIP transport, not scientific
execution or project installation.
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
import tempfile
import zipfile
from pathlib import Path

import pytest

from tavotto import app as m
from tavotto.engine import (
    brand,
    config,
    declaredinputs,
    documentpackage,
    figcapture,
    packagebudget,
    pool,
    svgstore,
)
from test_svgstore import PATCH, PDF, _args
from test_svgstore_input_archive import _capture, _contents, world as _input_world

input_world = _input_world
SOURCE = figcapture.runtime_asset_id("plot.py", "F")
OWNER = {"script": "plot.py", "run_config": None}


@pytest.fixture
def env(input_world, monkeypatch):
    root, _store, original, candidate, cache = input_world
    m.reset_projects()
    monkeypatch.setitem(m.app.config, "TESTING", True)
    monkeypatch.setattr(m, "EXPORT_DIR", root.parent / "fallback-exports")
    m.open_project(str(root))
    world = root, m._svg_storage_root(root), original, candidate, cache

    def forbidden(*_args, **_kwargs):
        pytest.fail("Cold packaging may not start a worker or reload input declarations")

    monkeypatch.setattr(pool, "get", forbidden)
    monkeypatch.setattr(declaredinputs, "state", forbidden)
    monkeypatch.setattr(declaredinputs, "payload_for", forbidden)
    yield world
    m.reset_projects()
    from tavotto.engine import project_watch
    from tavotto.rendercore import renderhost

    project_watch.stop()
    renderhost.shutdown_shared()


def _publish(env, archive=None, *, source=SOURCE, patch=PATCH, static=False, pair=None):
    args = _args(source=source, patch=patch, **(pair or {}))
    if archive is not None:
        args["receipt"]["captured_inputs"] = {
            "version": 1,
            "revision": "sha256:" + hashlib.sha256(archive.graph_bytes).hexdigest(),
        }
        args["captured_input_archive"] = archive
    if static:
        (env[0] / source).write_bytes(PDF)
        args["static_sha256"] = hashlib.sha256(PDF).hexdigest()
    return svgstore.save(env[0], source, patch, **args, execution_owner=OWNER, storage_root=env[1])


def _formal_package_fixture(env):
    import pikepdf

    # Core storage accepts arbitrary opaque IDs. A formal portable package
    # must use the parent's approved remap identity without changing that
    # lower-level shared fixture or the legacy ZIP byte oracle.
    candidate = copy.deepcopy(env[3])
    prefix = candidate["prefixes"][0]
    identity = declaredinputs._remap_identity(env[0], prefix["path"], candidate["rules"][0])
    prefix["id"] = candidate["mappings"][0]["id"] = identity
    _, archive = _capture((*env[:3], candidate, env[4]))

    stream = io.BytesIO()
    with pikepdf.new() as document:
        page = document.add_blank_page(page_size=(120, 80))
        page.Contents = document.make_stream(b"1 0 0 rg 0 0 120 80 re f\n")
        document.save(stream)
    pair = {
        "pdf": stream.getvalue(),
        "svg": (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="120pt" height="80pt" '
            b'viewBox="0 0 120 80"><rect width="120" height="80" fill="red"/></svg>'
        ),
    }
    return archive, _publish(env, archive, pair=pair)


def _panel(reference, *, source=SOURCE, number=1, edits=()):
    return {
        "id": "p" + str(number),
        "type": "panel",
        "fileId": source,
        "fileKind": "pdf",
        "nativeW": 30,
        "nativeH": 20,
        "x": 1,
        "y": 2,
        "w": 30,
        "h": 20,
        "overrides": copy.deepcopy(list(edits)),
        **({"figureSnapshot": copy.deepcopy(reference)} if reference is not None else {}),
    }


def _document(panels=(), *, schema=4):
    return {
        "schema": schema,
        "project": {"id": "cold-staging", "name": "冷归档"},
        "canvases": [
            {
                "id": "c1",
                "name": "第一画布",
                "page": {"w": 100, "h": 80},
                "objects": list(panels),
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 11,
        "updatedAt": 22,
    }


def _helper_zip(env, doc, destination):
    """Use the production collector and atomic ZIP writer, keeping staging alive."""
    config.data_dir().mkdir(parents=True, exist_ok=True)
    with m.app.test_request_context("/api/documents/v4/package?pj=" + m._project_id(env[0])):
        with tempfile.TemporaryDirectory(
            prefix="cold-staging-", dir=config.data_dir()
        ) as temporary:
            plan = m._PackageFilePlan()
            count = m._package_svg_assets(
                doc, env[0], plan, temporary_root=Path(temporary), strict_references=True
            )
            plan.members[documentpackage.LAYOUT_MEMBER] = json.dumps(
                doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            plan.members[documentpackage.MANIFEST_MEMBER] = json.dumps(
                {"kind": brand.PACKAGE_KIND, "version": 1, "figure_snapshots": count},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            budget = packagebudget.output_budget(
                {
                    name: len(source) if isinstance(source, bytes) else source.stat().st_size
                    for name, source in plan.members.items()
                }
            )
            with documentpackage.staged_package(
                destination, budget=budget, overwrite=False
            ) as archive:
                for name, source in plan.members.items():
                    if isinstance(source, bytes):
                        archive.writestr(name, source)
                    else:
                        archive.write(source, name)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        return count, {name: archive.read(name) for name in archive.namelist()}


def _prefix(env):
    return "assets/" + env[1].relative_to(env[0]).as_posix() + "/"


def _assert_input_members(env, members, archive):
    prefix = _prefix(env)
    graph_name = (
        prefix + "inputs/graphs/" + hashlib.sha256(archive.graph_bytes).hexdigest() + ".json"
    )
    assert graph_name in members, "captured input graph missing from the real staged ZIP"
    assert members[graph_name] == archive.graph_bytes
    for resource in archive.resources:
        name = prefix + "inputs/blobs/" + resource.sha256[:2] + "/" + resource.sha256[2:]
        assert members[name] == resource.path.read_bytes()
    return graph_name


@pytest.mark.parametrize("lookup", ["explicit", "current_static"])
def test_actual_helper_preserves_inputs_from_both_checked_store_load_paths(
    env, tmp_path, monkeypatch, lookup
):
    _, archive = _capture(env)
    source = SOURCE if lookup == "explicit" else "F.pdf"
    saved = _publish(env, archive, source=source, static=lookup == "current_static")
    document = _document(
        [_panel(saved["snapshot_ref"] if lookup == "explicit" else None, source=source)]
    )
    observed = []
    original = svgstore.save

    def save(*args, **kwargs):
        if Path(kwargs.get("storage_root", "")) != env[1]:
            observed.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(svgstore, "save", save)
    count, members = _helper_zip(env, document, tmp_path / (lookup + brand.PACKAGE_EXT))
    _assert_input_members(env, members, archive)
    assert count == 1 and len(observed) == 1
    assert observed[0]["captured_input_archive"].graph_bytes == archive.graph_bytes
    assert _contents(observed[0]["captured_input_archive"]) == _contents(archive)
    assert document["canvases"][0]["objects"][0]["figureSnapshot"] == saved["snapshot_ref"]
    assert json.loads(members["layout.json"]) == document


def test_same_graph_multiple_panels_and_variants_keep_every_reference_and_deduplicate(
    env, tmp_path
):
    _, archive = _capture(env)
    first = _publish(env, archive)
    edits = [{"gid": "axes:0", "prop": "visible", "value": False}]
    patch = m.engine_patchspec.patch_hash(edits)
    second = _publish(env, archive, patch=patch)
    document = _document(
        [
            _panel(first["snapshot_ref"], number=1),
            _panel(first["snapshot_ref"], number=2),
            _panel(second["snapshot_ref"], number=3, edits=edits),
        ]
    )
    document["canvases"].append(
        {
            "id": "inactive",
            "name": "非活动",
            "page": {"w": 100, "h": 80},
            "objects": [_panel(second["snapshot_ref"], number=4, edits=edits)],
            "guides": [],
        }
    )
    count, members = _helper_zip(env, document, tmp_path / ("deduplicated" + brand.PACKAGE_EXT))
    _assert_input_members(env, members, archive)
    assert count == 2
    assert len([name for name in members if "/inputs/graphs/" in name]) == 1
    assert len([name for name in members if "/inputs/blobs/" in name]) == len(archive.resources)
    assert len([name for name in members if "/snapshots/" in name]) == 2
    assert [obj["figureSnapshot"] for obj in m._doc_objects(document)] == [
        first["snapshot_ref"],
        first["snapshot_ref"],
        second["snapshot_ref"],
        second["snapshot_ref"],
    ]


def test_fresh_application_parent_packages_original_input_after_source_and_capture_cache_deleted(
    env, tmp_path
):
    archive, saved = _formal_package_fixture(env)
    expected = {key: value.hex() for key, value in _contents(archive).items()}
    document = _document([_panel(saved["snapshot_ref"])])
    exchange = tmp_path / "document.json"
    exchange.write_text(json.dumps(document), encoding="utf-8")
    shutil.rmtree(env[2])
    shutil.rmtree(env[4])
    code = """
import json,sys,zipfile
from pathlib import Path
from tavotto import app as m
from tavotto.engine import pool,declaredinputs,svgstore,project_watch
def forbidden(*a,**kw): raise AssertionError('cold packaging attempted execution/declaration lookup')
pool.get=forbidden
declaredinputs.state=forbidden
declaredinputs.payload_for=forbidden
root=Path(sys.argv[1]); m.open_project(str(root)); m.app.config['TESTING']=True
doc=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
pid=m._project_id(root)
response=m.app.test_client().post('/api/documents/v4/package?pj='+pid,json={'stem':'cold_parent','doc':doc,'settings':{}})
assert response.status_code==200, (response.status_code,response.get_json())
with m.app.test_request_context('/api/documents/v4/package?pj='+pid):
    target=m.project_export_dir()/response.get_json()['name']
with zipfile.ZipFile(target) as package:
    assert package.testzip() is None
    members={name:package.read(name) for name in package.namelist()}
prefix='assets/'+m._svg_storage_root(root).relative_to(root).as_posix()+'/'
graphs=[data.hex() for name,data in members.items() if name.startswith(prefix+'inputs/graphs/')]
blobs={name.rsplit('/',2)[1]+name.rsplit('/',1)[1]:data.hex() for name,data in members.items() if name.startswith(prefix+'inputs/blobs/')}
assert not any(k in sys.modules for k in ('matplotlib','numpy'))
m.reset_projects(); project_watch.stop()
from tavotto.rendercore import renderhost
renderhost.shutdown_shared()
print(json.dumps({'status':response.status_code,'graphs':graphs,'blobs':blobs,'doc':json.loads(members['layout.json'])}))
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-c", code, str(env[0]), str(exchange)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "status": 200,
        "graphs": [archive.graph_bytes.hex()],
        "blobs": expected,
        "doc": document,
    }


def _fixed_zip_clock(monkeypatch):
    monkeypatch.setattr(svgstore.time, "time", lambda: 1700000000.125)
    original = zipfile.ZipInfo.from_file
    original_init = zipfile.ZipInfo.__init__

    def init(info, *args, **kwargs):
        original_init(info, *args, **kwargs)
        info.create_system = 3
        info.external_attr = 0o100600 << 16

    def from_file(*args, **kwargs):
        info = original(*args, **kwargs)
        info.date_time = (2020, 1, 2, 3, 4, 6)
        info.create_system = 3
        info.external_attr = 0o100600 << 16
        return info

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", init)
    monkeypatch.setattr(zipfile.ZipInfo, "from_file", from_file)
    monkeypatch.setattr(zipfile.time, "localtime", lambda *args: (2020, 1, 2, 3, 4, 6, 3, 2, -1))


def test_legacy_v1_staging_adds_no_new_save_kwargs_and_preserves_complete_zip_oracle(
    env, tmp_path, monkeypatch
):
    _fixed_zip_clock(monkeypatch)
    saved = _publish(env)
    document = _document([_panel(saved["snapshot_ref"])], schema=3)
    observed = []
    original = svgstore.save

    def save(*args, **kwargs):
        if Path(kwargs.get("storage_root", "")) != env[1]:
            observed.append(dict(kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(svgstore, "save", save)
    target = tmp_path / ("legacy" + brand.PACKAGE_EXT)
    count, members = _helper_zip(env, document, target)
    assert count == 1 and len(observed) == 1
    assert "captured_input_archive" not in observed[0] and "input_budget" not in observed[0]
    assert not any("/inputs/" in name for name in members)
    payload = json.loads(
        members[_prefix(env) + "snapshots/" + saved["snapshot_ref"]["snapshotId"] + ".json"]
    )["payload"]
    assert payload["version"] == 1 and "input_archive" not in payload
    # Actual pre-change helper oracle: fixed clock and platform ZIP permission fields.
    assert (
        hashlib.sha256(target.read_bytes()).hexdigest()
        == "e6649838e2bfec274d087a682ec90fa6f374df4c6a8f91bf19fd9cdd29e01d66"
    )


@pytest.mark.parametrize(
    "damage", ["missing_graph", "changed_graph", "missing_blob", "changed_blob"]
)
def test_strict_http_rejects_bad_cold_input_reference_without_publishing_a_package(env, damage):
    _, archive = _capture(env)
    saved = _publish(env, archive)
    client = m.app.test_client()
    url = "/api/documents/v4/package?pj=" + m._project_id(env[0])
    response = client.post(url, json={"stem": "existing", "doc": _document(), "settings": {}})
    assert response.status_code == 200, response.get_json()
    with m.app.test_request_context(url):
        output = m.project_export_dir()
    before = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
    if damage.endswith("graph"):
        target = (
            env[1] / "inputs/graphs" / (hashlib.sha256(archive.graph_bytes).hexdigest() + ".json")
        )
    else:
        resource = next(row for row in archive.resources if row.size_bytes)
        target = env[1] / "inputs/blobs" / resource.sha256[:2] / resource.sha256[2:]
    if damage.startswith("missing"):
        target.unlink()
    else:
        target.write_bytes(b"!" * target.stat().st_size)
    response = client.post(
        url,
        json={"stem": "damaged", "doc": _document([_panel(saved["snapshot_ref"])]), "settings": {}},
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "figure_snapshot_unavailable"
    assert {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()} == before
    assert set(p.name for p in output.iterdir()) == set(before)
