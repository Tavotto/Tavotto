"""Historical edit input survives saving and packaging without current execution."""

from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

from tavotto import app as m
from tavotto.engine import documentpackage, patchspec, svgstore
from test_svg_document_snapshot_sources import SOURCE, _document, case, input_world

# Imported fixtures build real checked PDF/SVG archives with captured INPUT.
__all__ = ["case", "input_world"]


def _edited_document(case):
    document = _document(case)
    for index, panel in enumerate(document["canvases"][0]["objects"]):
        panel["figureOrigin"] = {
            "version": 1,
            "snapshot": panel.pop("figureSnapshot"),
            "patches": copy.deepcopy(panel["overrides"]),
        }
        panel["overrides"] = [{"gid": "line", "prop": "linewidth", "value": 4 + index}]
    return document


def test_origin_validation_keeps_order_and_duplicates_without_aliasing():
    patches = [
        {"gid": "z", "prop": "linewidth", "value": 1},
        {"gid": "a", "prop": "color", "value": [1, 0, 0]},
        {"gid": "z", "prop": "linewidth", "value": 2},
    ]
    reference = {
        "version": 1,
        "sourceId": "plot.pdf",
        "patchHash": patchspec.patch_hash(patches),
        "snapshotId": "a" * 64,
        "receiptId": "sha256:" + "b" * 64,
    }
    origin = {"version": 1, "snapshot": reference, "patches": patches}
    result = svgstore.validate_origin(origin, source_id="plot.pdf")
    assert result == origin and len(result["patches"]) == 3
    result["patches"][1]["value"][0] = 0
    result["snapshot"]["sourceId"] = "other.pdf"
    assert patches[1]["value"] == [1, 0, 0] and reference["sourceId"] == "plot.pdf"
    for bad in (
        {**origin, "version": True},
        {**origin, "patches": []},
        {**origin, "patches": patches + [{"gid": "", "prop": "color", "value": 3}]},
        {**origin, "approved": True},
    ):
        with pytest.raises(ValueError):
            svgstore.validate_origin(bad, source_id="plot.pdf")
    with pytest.raises(ValueError):
        svgstore.validate_origin(origin, source_id="other.pdf")


@pytest.mark.parametrize("schema", [3, 4])
def test_save_keeps_origin_and_never_borrows_current_output(case, monkeypatch, schema):
    document = _edited_document(case)
    document["schema"] = schema

    def forbidden(*_args, **_kwargs):
        pytest.fail("Saving an origin-only edit must not choose a current execution")

    monkeypatch.setattr(m, "_execution_source", forbidden)
    # A marker for the same logical file must not authorize a different Figure.
    monkeypatch.setattr(svgstore, "load", forbidden)
    prefix = "/api/documents/v4" if schema == 4 else "/api"
    url = f"{prefix}/layouts/edited?pj={case.pid}"
    saved = case.client.post(url + "&target=project&base_revision=absent", json=document)
    assert saved.status_code == 200, saved.get_json()
    restored = case.client.get(url)
    assert restored.status_code == 200, restored.get_json()
    panels = restored.get_json()["canvases"][0]["objects"]
    assert panels == document["canvases"][0]["objects"]
    assert all("figureSnapshot" not in panel for panel in panels)


def test_origin_only_package_carries_each_pair_and_input_without_claiming_current_output(case):
    document = _edited_document(case)
    response = case.client.post(
        "/api/documents/v4/package?pj=" + case.pid, json={"stem": "edited", "doc": document}
    )
    assert response.status_code == 200, response.get_json()
    path = case.export_dir / response.get_json()["name"]
    with zipfile.ZipFile(path) as package:
        members = {name: package.read(name) for name in package.namelist()}
    manifest = json.loads(members[documentpackage.MANIFEST_MEMBER])
    captures = manifest["execution"]["captures"]
    assert {row["snapshot_ref"]["snapshotId"] for row in captures} == {
        reference["snapshotId"] for reference in case.references
    }
    assert {row["revision"] for row in captures} == {case.input_revision}
    for index, reference in enumerate(case.references):
        prefix = "assets/" + case.store.relative_to(case.root).as_posix() + "/"
        payload = json.loads(members[prefix + "snapshots/" + reference["snapshotId"] + ".json"])[
            "payload"
        ]
        for kind, expected in (("pdf", case.pdfs[index]), ("svg", case.svgs[index])):
            sha = payload[kind + "_artifact"]["bytes_sha256"]
            assert members[prefix + "blobs/" + sha + "." + kind] == expected
    inspected = case.client.post(
        "/api/documents/v4/package/inspect?pj=" + case.pid,
        data={"package": (io.BytesIO(path.read_bytes()), "edited.tavotto")},
    )
    assert inspected.status_code == 200, inspected.get_json()
    result = inspected.get_json()
    assert result["doc"]["canvases"][0]["objects"] == document["canvases"][0]["objects"]
    assert len(result["captured_inputs"]) == 2
    assert {"reason": "figure_snapshot_missing", "id": SOURCE} in result["warnings"]


def test_package_counts_same_archive_once_when_output_and_origin_share_it(case):
    document = _document(case)
    for panel in document["canvases"][0]["objects"]:
        panel["figureOrigin"] = {
            "version": 1,
            "snapshot": copy.deepcopy(panel["figureSnapshot"]),
            "patches": copy.deepcopy(panel["overrides"]),
        }
    response = case.client.post(
        "/api/documents/v4/package?pj=" + case.pid, json={"stem": "both-roles", "doc": document}
    )
    assert response.status_code == 200, response.get_json()
    path = case.export_dir / response.get_json()["name"]
    with zipfile.ZipFile(path) as package:
        manifest = json.loads(package.read(documentpackage.MANIFEST_MEMBER))
    assert manifest["figure_snapshots"] == len(case.references)
    assert len(manifest["execution"]["captures"]) == len(case.references)


@pytest.mark.parametrize("field", ["receiptId", "executionOwner"])
def test_origin_package_revalidates_colliding_full_reference(case, field):
    document = _edited_document(case)
    panels = document["canvases"][0]["objects"]
    panels[1]["figureOrigin"] = copy.deepcopy(panels[0]["figureOrigin"])
    reference = panels[1]["figureOrigin"]["snapshot"]
    reference[field] = (
        "sha256:" + "e" * 64 if field == "receiptId" else {"script": "other.py", "run_config": None}
    )
    before = set(case.export_dir.rglob("*.tavotto"))
    response = case.client.post(
        "/api/documents/v4/package?pj=" + case.pid, json={"stem": "bad-origin", "doc": document}
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "figure_snapshot_unavailable"
    assert set(case.export_dir.rglob("*.tavotto")) == before
