"""Archived display uses one exact pair, never an editable manifest or a script."""

from __future__ import annotations

import copy
import io
import json
import secrets
from types import SimpleNamespace

import pytest
from PIL import Image

from tavotto import app as m, security
from tavotto.engine import figcapture, inputremap, patchspec, pool, svgstore
from test_svg_document_snapshot_sources import (
    COLORS,
    OWNER,
    SIZES,
    SOURCE,
    case as _history_case,
    input_world as _input_world,
)

history_case = _history_case
input_world = _input_world

PREVIEW = "/api/documents/v4/figure-preview"
PNG = "/api/engine/preview_png"
PATCHES = [
    {"gid": "axes_0.lines_0", "prop": "color", "value": "blue"},
    {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
    {"gid": "axes_0.lines_0", "prop": "color", "value": "red"},
]


@pytest.fixture
def world(history_case, monkeypatch):
    case = history_case
    references = []
    for reference in case.references:
        old = svgstore.inspect_portable_snapshot(reference, storage_root=case.store)
        artifact = {**old["pdf_artifact"], "patch_hash": patchspec.patch_hash(PATCHES)}
        saved = svgstore.save(
            case.root,
            SOURCE,
            patchspec.patch_hash(PATCHES),
            pdf=old["pdf"],
            svg=old["svg"],
            artifact=artifact,
            receipt=old["receipt"],
            execution_owner=old["execution_owner"],
            static_sha256=old["identity"]["static_sha256"],
            remap_fingerprint=case.fingerprint,
            storage_root=case.store,
            captured_input_archive=case.archive,
            activate=True,
        )
        references.append(saved["snapshot_ref"])
    case.references = references
    state = security.SessionState(secrets.token_urlsafe(32), api_secret=secrets.token_urlsafe(32))
    state.port = 25187
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    case.base_url = "http://127.0.0.1:25187"
    case.auth_headers = lambda: {"X-Tavotto-Auth": state.api_secret}
    return case


def _post(world, endpoint=PREVIEW, *, reference=None, authenticated=True, **changes):
    body = {
        "id": SOURCE,
        "patches": copy.deepcopy(PATCHES),
        "figure_snapshot": copy.deepcopy(reference or world.references[0]),
        **changes,
    }
    return world.client.post(
        endpoint + "?pj=" + world.pid,
        json=body,
        headers=world.auth_headers() if authenticated else {},
        base_url=world.base_url,
    )


def _forbid_workers(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Archived display must never resolve or execute a scientific worker")

    monkeypatch.setattr(m, "_engine_worker", forbidden)
    monkeypatch.setattr(pool, "get", forbidden)
    monkeypatch.setattr(pool, "one_shot", forbidden)


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
@pytest.mark.parametrize("remove_current", [False, True])
def test_same_source_and_ordered_patches_select_two_distinct_full_references(
    world, monkeypatch, endpoint, remove_current
):
    _forbid_workers(monkeypatch)
    if remove_current:
        (world.root / SOURCE).unlink()
    for index, reference in enumerate(world.references):
        response = _post(
            world, endpoint, reference=reference, **({"w": 200} if endpoint == PNG else {})
        )
        assert response.status_code == 200, response.get_json()
        assert response.headers["Cache-Control"] == "no-store"
        if endpoint == PREVIEW:
            body = response.get_json()
            assert set(body) == {"preview_source", "figure_snapshot", "svg", "size_mm"}
            assert body["preview_source"] == "figure_snapshot"
            assert body["figure_snapshot"] == reference
            assert body["svg"].encode("utf-8") == world.svgs[index]
            assert body["size_mm"] == pytest.approx([value * 25.4 / 72 for value in SIZES[index]])
            assert "manifest" not in body
        else:
            assert response.mimetype == "image/png"
            assert json.loads(response.headers["X-Tavotto-Figure-Snapshot"]) == reference
            with Image.open(io.BytesIO(response.data)) as image:
                assert image.width == 200
                assert (
                    image.convert("RGB").getpixel((image.width // 2, image.height // 2))
                    == COLORS[index]
                )


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
def test_opaque_runtime_reference_needs_no_registry_parse_or_disk_original(
    world, monkeypatch, endpoint
):
    source = figcapture.runtime_asset_id("script#with-hash.py", "panel#name")
    old = svgstore.inspect_portable_snapshot(world.references[0], storage_root=world.store)
    artifact = {**old["pdf_artifact"], "source_id": source}
    saved = svgstore.save(
        world.root,
        source,
        patchspec.patch_hash(PATCHES),
        pdf=old["pdf"],
        svg=old["svg"],
        artifact=artifact,
        receipt=old["receipt"],
        execution_owner=old["execution_owner"],
        remap_fingerprint=world.fingerprint,
        storage_root=world.store,
        captured_input_archive=world.archive,
        activate=True,
    )
    _forbid_workers(monkeypatch)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Explicit runtime reference may not be reverse parsed through the registry")

    monkeypatch.setattr(m, "_resolve_runtime_asset", forbidden)
    response = _post(world, endpoint, reference=saved["snapshot_ref"], id=source)
    assert response.status_code == 200
    if endpoint == PREVIEW:
        assert response.get_json()["svg"].encode() == world.svgs[0]
        assert response.get_json()["figure_snapshot"] == saved["snapshot_ref"]
    else:
        assert json.loads(response.headers["X-Tavotto-Figure-Snapshot"]) == saved["snapshot_ref"]


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
def test_invalid_patch_entries_are_not_silently_dropped_from_display_identity(
    world, monkeypatch, endpoint
):
    _forbid_workers(monkeypatch)
    response = _post(world, endpoint, patches=[*PATCHES, None])
    assert response.status_code == 400 and response.get_json()["code"] == "invalid_preview_options"


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "receipt",
        "owner",
        "patch_order",
        "snapshot",
        "remap",
        "pdf_blob",
        "svg_blob",
        "input_blob",
    ],
)
def test_invalid_reference_or_pair_never_falls_back_to_current_or_script(
    world, monkeypatch, endpoint, damage
):
    _forbid_workers(monkeypatch)
    reference = copy.deepcopy(world.references[0])
    changes = {}
    if damage == "source":
        reference["sourceId"] = "other.pdf"
    elif damage == "receipt":
        reference["receiptId"] = "sha256:" + "f" * 64
    elif damage == "owner":
        reference["executionOwner"]["script"] = "other.py"
    elif damage == "patch_order":
        changes["patches"] = list(reversed(PATCHES))
    elif damage == "snapshot":
        reference["snapshotId"] = "f" * 64
    elif damage == "remap":
        target = world.root / "other.csv"
        target.write_bytes(b"other target")
        inputremap.add_rule(world.root, {**world.rule, "to": str(target)})
    else:
        record = svgstore.inspect_portable_snapshot(reference, storage_root=world.store)
        if damage == "input_blob":
            path = next(
                resource.path
                for resource in record["captured_input_archive"].resources
                if resource.size_bytes
            )
        else:
            kind = damage.removesuffix("_blob")
            path = world.store / "blobs" / (record[kind + "_artifact"]["bytes_sha256"] + "." + kind)
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    response = _post(world, endpoint, reference=reference, **changes)
    assert response.status_code == 422
    assert response.get_json()["code"] == "figure_snapshot_unavailable"
    assert response.mimetype == "application/json" and "svg" not in response.get_json()


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
def test_auth_and_explicit_project_ownership_are_required(world, monkeypatch, endpoint):
    _forbid_workers(monkeypatch)
    assert _post(world, endpoint, authenticated=False).status_code == 401
    response = world.client.post(
        endpoint + "?pj=not-an-open-project",
        json={"id": SOURCE, "patches": PATCHES, "figure_snapshot": world.references[0]},
        headers=world.auth_headers(),
        base_url=world.base_url,
    )
    assert response.status_code == 409 and "svg" not in response.get_json()


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
def test_cross_project_reference_cannot_read_another_projects_store(world, monkeypatch, endpoint):
    _forbid_workers(monkeypatch)
    other = world.root.parent / "other-project"
    other.mkdir()
    pid = m.open_project(str(other))["id"]
    response = world.client.post(
        endpoint + "?pj=" + pid,
        json={"id": SOURCE, "patches": PATCHES, "figure_snapshot": world.references[0]},
        headers=world.auth_headers(),
        base_url=world.base_url,
    )
    assert (
        response.status_code == 422 and response.get_json()["code"] == "figure_snapshot_unavailable"
    )


@pytest.mark.parametrize(
    "endpoint,changes",
    [
        (PREVIEW, {"chart": {}}),
        (PNG, {"with_manifest": True}),
        (PNG, {"source_policy": "artifact"}),
    ],
)
def test_display_cannot_claim_editable_manifest_or_replace_artifact_context(
    world, monkeypatch, endpoint, changes
):
    _forbid_workers(monkeypatch)
    response = _post(world, endpoint, **changes)
    assert response.status_code == 400 and response.get_json()["code"] == "invalid_preview_options"


@pytest.mark.parametrize("endpoint", [PREVIEW, PNG])
def test_new_execution_revokes_current_qualification_but_not_explicit_archived_display(
    world, monkeypatch, endpoint
):
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", pool.find_worker_python())
    counter = world.root.parent / "actual-execution-counter.log"
    (world.root / OWNER["script"]).write_text(
        "import matplotlib.pyplot as plt\ndef main():\n"
        f"    with open({str(counter)!r}, 'ab') as out: out.write(b'run\\n')\n"
        "    fig, ax=plt.subplots(); ax.plot([0,1],[9,8]); fig.savefig('fresh.pdf')\n",
        encoding="utf-8",
    )
    response = world.client.post(
        "/api/registry/probe?pj=" + world.pid,
        json={"script": OWNER["script"]},
        headers=world.auth_headers(),
        base_url=world.base_url,
    )
    assert response.status_code == 200 and response.get_json()["registered"], response.get_json()
    assert counter.read_bytes() == b"run\n"
    _forbid_workers(monkeypatch)
    with m.bound_project(m.PROJECTS[world.pid]):
        for reference in world.references:
            assert (
                m._load_svg_snapshot(
                    SOURCE, PATCHES, world.root.parent / "current-check", reference=reference
                )
                is None
            )
    before = {
        path.relative_to(world.store): path.read_bytes()
        for path in world.store.rglob("*")
        if path.is_file()
    }
    for index, reference in enumerate(world.references):
        response = _post(world, endpoint, reference=reference)
        assert response.status_code == 200, response.get_json()
        if endpoint == PREVIEW:
            assert response.get_json()["svg"].encode() == world.svgs[index]
            assert response.get_json()["figure_snapshot"] == reference
            assert "manifest" not in response.get_json()
        else:
            assert json.loads(response.headers["X-Tavotto-Figure-Snapshot"]) == reference
            with Image.open(io.BytesIO(response.data)) as image:
                assert (
                    image.convert("RGB").getpixel((image.width // 2, image.height // 2))
                    == COLORS[index]
                )
        with m.bound_project(m.PROJECTS[world.pid]):
            assert (
                m._load_svg_snapshot(
                    SOURCE, PATCHES, world.root.parent / "current-check", reference=reference
                )
                is None
            )
    assert {
        path.relative_to(world.store): path.read_bytes()
        for path in world.store.rglob("*")
        if path.is_file()
    } == before
    assert counter.read_bytes() == b"run\n"


@pytest.mark.parametrize("with_manifest", [False, True])
def test_no_reference_png_keeps_original_worker_calls_and_wire(world, monkeypatch, with_manifest):
    calls = []
    path = world.root.parent / "legacy.png"
    path.write_bytes(b"original-png-wire")

    def png(stem, patches, width, tag):
        calls.append((stem, patches, width, tag))
        return path

    def snapshot(stem, patches, width):
        calls.append((stem, patches, width))
        return {"png": "original-base64", "manifest": {"elements": [], "size_mm": [10, 20]}}

    worker = SimpleNamespace(preview_png=png, preview_png_snapshot=snapshot)
    monkeypatch.setattr(m, "_engine_worker", lambda *args, **kwargs: (worker, "original-stem"))
    monkeypatch.setattr(
        m, "_engine_attempt", lambda _id, wk, stem, fn, **kwargs: (wk, stem, fn(wk, stem))
    )
    response = world.client.post(
        PNG + "?pj=" + world.pid,
        json={
            "id": SOURCE,
            "patches": [],
            "w": 201,
            **({"with_manifest": True} if with_manifest else {}),
        },
        headers=world.auth_headers(),
        base_url=world.base_url,
    )
    assert response.status_code == 200
    assert "X-Tavotto-Figure-Snapshot" not in response.headers
    if with_manifest:
        assert calls == [("original-stem", [], 400)]
        assert response.get_json() == {
            "png": "original-base64",
            "manifest": {"elements": [], "size_mm": [10, 20]},
        }
    else:
        assert calls == [
            ("original-stem", [], 400, "v" + patchspec.patch_hash([]).split(":")[-1][:12])
        ]
        assert response.data == b"original-png-wire"
