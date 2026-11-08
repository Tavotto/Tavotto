"""A real checked container feeds the relocation consumer, without execution.

The pair is readable PDF/safe SVG with explicit test receipt facts. Scientific
worker/new-parent qualification belongs to test_package_relocation_e2e.
"""

from __future__ import annotations

import copy
import io
import json
import shutil
import zipfile
from pathlib import Path

import pikepdf
import pytest

from tavotto.engine import (
    chartpackage,
    config,
    documentpackage,
    inputdeclaration,
    inputremap,
    packagebudget,
    packagerelocation,
    patchspec,
    svgstore,
)
from test_chart_package_http import document, panel
from test_package_execution_captures import _capture, _execution
from test_svgstore import _args


def _pair(number):
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(72, 72))
    page.Contents = pdf.make_stream(f"q 0 0 0 RG 0 {number} m 72 72 l S Q".encode())
    with io.BytesIO() as output:
        pdf.save(output)
        data = output.getvalue()
    pdf.close()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="72pt" height="72pt" '
        f'viewBox="0 0 72 72"><path d="M0 {number} L72 72" stroke="black"/></svg>'
    ).encode()
    return data, svg


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "app-data"))
    source = tmp_path / "source"
    source.mkdir()
    store = source / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    source_id = "runtime:plot.py#F"
    edits = [
        {"gid": "line", "prop": "linewidth", "value": 1.0},
        {"gid": "line", "prop": "linewidth", "value": 2.0},
    ]
    patch = patchspec.patch_hash(edits)
    refs, expected = [], []
    for number in (1, 2):
        archive, public, owner = _capture(
            tmp_path, f"capture-{number}", data=f"old-{number}\n".encode()
        )
        pdf, svg = _pair(number)
        args = _args(source=source_id, patch=patch, pdf=pdf, svg=svg, generation=number)
        args["receipt"]["captured_inputs"] = public
        ref = svgstore.save(
            source,
            source_id,
            patch,
            storage_root=store,
            archive_only=True,
            execution_owner=owner,
            captured_input_archive=archive,
            **args,
        )["snapshot_ref"]
        refs.append(ref)
        expected.append({"pdf": pdf, "svg": svg, "public": public, "graph": archive.graph_bytes})
    facts = svgstore.inspect_portable_store(storage_root=store)
    resources = []
    for relative in facts["members"]:
        if relative == ".lock":
            continue
        path = store / relative
        resources.append(
            chartpackage.ResourceFile(
                {
                    "kind": "figure_snapshot",
                    "path": f"assets/{store.relative_to(source).as_posix()}/{relative}",
                    "sha256": svgstore._digest(path.read_bytes()),
                    "bytes": path.stat().st_size,
                },
                path,
            )
        )
    current = b"new current file\n"
    resources.append(
        chartpackage.ResourceFile(
            {
                "kind": "file",
                "path": "assets/current.csv",
                "id": "current.csv",
                "sha256": svgstore._digest(current),
                "bytes": len(current),
            },
            current,
        )
    )
    doc = document()
    first = panel(file=source_id, edits=edits)
    first["figureSnapshot"] = refs[0]
    second = copy.deepcopy(first)
    second["figureSnapshot"] = refs[1]
    doc["canvases"][0]["objects"] = [first]
    inactive = copy.deepcopy(doc["canvases"][0])
    inactive.update(id="inactive", objects=[second, copy.deepcopy(first)])
    doc["canvases"].append(inactive)
    path = tmp_path / "source.tavotto"
    chartpackage.write_chart_package(
        path,
        doc,
        source / config.PROJECT_STORE_DIRNAME,
        resources=resources,
        metadata={"execution": _execution(facts)},
        budget_factory=packagebudget.portable_output_budget,
    )
    return path, doc, refs, expected


def _open(path):
    return chartpackage.inspect_chart_package(
        path.read_bytes(), budget=packagebudget.input_budget()
    )


def _rules(target):
    return [{"kind": "file", "from": "legacy/current.csv", "to": str(target / "current.csv")}]


def test_relocation_rewrites_origin_only_and_output_roles_with_their_own_baselines(
    package, tmp_path
):
    path, document, refs, expected = package
    document = copy.deepcopy(document)
    for canvas in document["canvases"]:
        for item in canvas["objects"]:
            item["figureOrigin"] = {
                "version": 1,
                "snapshot": item.pop("figureSnapshot"),
                "patches": copy.deepcopy(item["overrides"]),
            }
            item["overrides"] = [{"gid": "line", "prop": "linewidth", "value": 9}]
    # One panel retains a distinct current output, while both historical
    # baselines and the inactive canvas must remain part of the closure.
    last = document["canvases"][1]["objects"][1]
    last["figureSnapshot"] = copy.deepcopy(refs[1])
    last["overrides"] = copy.deepcopy(last["figureOrigin"]["patches"])
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read(documentpackage.MANIFEST_MEMBER))
        resources = [
            chartpackage.ResourceFile(row, archive.read(row["path"])) for row in manifest["assets"]
        ]
    edited = tmp_path / "edited.tavotto"
    chartpackage.write_chart_package(
        edited,
        document,
        tmp_path / "source" / config.PROJECT_STORE_DIRNAME,
        resources=resources,
        metadata={"execution": manifest["execution"]},
        budget_factory=packagebudget.portable_output_budget,
    )
    target = tmp_path / "迁移 编辑来源"
    with (
        _open(edited) as original,
        packagerelocation.prepare_relocation(
            original, project_root=target, remap_rules=_rules(target)
        ) as moved,
    ):
        assert original.document == document
        changed = moved.document
        mapping = {row["old_ref"]["snapshotId"]: row["new_ref"] for row in moved.references}
        assert len(mapping) == 2
        restored = copy.deepcopy(changed)
        for ci, canvas in enumerate(restored["canvases"]):
            for oi, item in enumerate(canvas["objects"]):
                old = document["canvases"][ci]["objects"][oi]
                for role, reference, patches in svgstore.panel_snapshot_inputs(old):
                    actual = item[role]["snapshot"] if role == "figureOrigin" else item[role]
                    assert actual == mapping[reference["snapshotId"]]
                    assert actual != reference
                    if role == "figureOrigin":
                        assert item[role]["patches"] == patches
                        item[role]["snapshot"] = reference
                    else:
                        item[role] = reference
        assert restored == document
        for index, ref in enumerate(refs):
            new = mapping[ref["snapshotId"]]
            record = svgstore.load_document_snapshot(
                target,
                new,
                source_id=new["sourceId"],
                patch_hash=new["patchHash"],
                remap_fingerprint=moved.remap_fingerprint,
                storage_root=moved.storage_root,
            )
            assert record["captured_input_archive"].graph_bytes == expected[index]["graph"]


def test_relocation_preserves_full_history_and_original_document_without_current_records(
    package, tmp_path
):
    path, doc, refs, expected = package
    target = tmp_path / "中文 新项目"
    with _open(path) as original:
        old_bytes = original.document_bytes
        with packagerelocation.prepare_relocation(
            original, project_root=target, remap_rules=_rules(target)
        ) as moved:
            assert not target.exists()
            assert original.document_bytes == old_bytes
            assert original.document == doc
            mapping = {row["old_ref"]["snapshotId"]: row["new_ref"] for row in moved.references}
            assert len(mapping) == 2
            assert all(
                mapping[ref["snapshotId"]]["snapshotId"] != ref["snapshotId"] for ref in refs
            )
            changed = moved.document
            restored = copy.deepcopy(changed)
            for canvas in restored["canvases"]:
                for obj in canvas["objects"]:
                    obj["figureSnapshot"] = next(
                        row["old_ref"]
                        for row in moved.references
                        if row["new_ref"] == obj["figureSnapshot"]
                    )
            assert restored == doc
            assert changed["activeCanvasId"] == doc["activeCanvasId"]
            facts = svgstore.inspect_portable_store(storage_root=moved.storage_root)
            assert len(facts["snapshots"]) == 2
            assert not facts["entries"] and not facts["sources"]
            assert {
                row["identity"]["remap_fingerprint"] for row in facts["snapshots"].values()
            } == {moved.remap_fingerprint}
            for ref, wanted in zip(refs, expected, strict=True):
                new = mapping[ref["snapshotId"]]
                record = svgstore.load_document_snapshot(
                    target,
                    new,
                    source_id=new["sourceId"],
                    patch_hash=new["patchHash"],
                    remap_fingerprint=moved.remap_fingerprint,
                    storage_root=moved.storage_root,
                )
                assert record["pdf"] == wanted["pdf"] and record["svg"] == wanted["svg"]
                assert record["captured_input_archive"].graph_bytes == wanted["graph"]
            copied_stage = moved.stage_root
            shutil.copytree(copied_stage, target)
            rules = moved.remap_rules
            inputremap.add_rule(target, rules[0])
            assert inputremap.rules_for(target) == rules
            assert inputremap.fingerprint(target) == moved.remap_fingerprint
            # Returned values cannot mutate the pending original metadata.
            changed["canvases"].clear()
            assert moved.document["canvases"]
        assert not copied_stage.exists()
        assert original.document_bytes == old_bytes
    with pytest.raises(chartpackage.PackageError, match="closed"):
        _ = moved.document
    assert (
        len(
            svgstore.inspect_portable_store(
                storage_root=target / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
            )["snapshots"]
        )
        == 2
    )


def test_selected_input_views_keep_each_history_separate_and_expire(package, tmp_path):
    path, _doc, refs, expected = package
    with (
        _open(path) as original,
        packagerelocation.prepare_relocation(
            original, project_root=tmp_path / "new", remap_rules=[]
        ) as moved,
    ):
        by_old = {row["old_ref"]["snapshotId"]: row["new_ref"] for row in moved.references}
        for old, wanted in zip(refs, expected, strict=True):
            new = by_old[old["snapshotId"]]
            with moved.input_view(new) as candidate:
                assert inputdeclaration.current_public_fact(candidate) == wanted["public"]
                path = Path(candidate["files"][0]["path"])
                assert path.read_bytes() in {b"old-1\n", b"old-2\n"}
                assert Path(candidate["script_path"]).parts[-2:] == ("script", "plot.py")
                with pytest.raises(chartpackage.PackageError, match="active input view"):
                    with moved.input_view(new):
                        pytest.fail("A second simultaneous capture-sized view was created")
            assert not path.exists()


@pytest.mark.parametrize("damage", ["bytes", "empty_directory", "extra_member"])
def test_materialized_view_changes_are_rejected_at_actual_exit(package, tmp_path, damage):
    path, _doc, _refs, _expected = package
    with (
        _open(path) as original,
        packagerelocation.prepare_relocation(
            original, project_root=tmp_path / "new", remap_rules=[]
        ) as moved,
    ):
        ref = moved.references[0]["new_ref"]
        with pytest.raises(chartpackage.PackageError, match="historical inputs changed"):
            with moved.input_view(ref) as candidate:
                if damage == "bytes":
                    selected = Path(candidate["files"][0]["path"])
                    selected.write_bytes(b"x" * selected.stat().st_size)
                elif damage == "empty_directory":
                    (Path(candidate["prefixes"][0]["path"]) / "empty dir").rmdir()
                else:
                    (Path(candidate["prefixes"][0]["path"]) / "added.csv").write_bytes(b"new")
        moved.verify()  # The immutable archive was never hard-linked to the writable view.


@pytest.mark.parametrize("damage", ["render", "input", "current_record"])
def test_relocation_stage_rechecks_its_own_bytes_before_publication(package, tmp_path, damage):
    path, _doc, _refs, _expected = package
    with _open(path) as original:
        with pytest.raises((ValueError, OSError)):
            with packagerelocation.prepare_relocation(
                original, project_root=tmp_path / "new", remap_rules=[]
            ) as moved:
                if damage == "render":
                    blob = next((moved.storage_root / "blobs").glob("*.pdf"))
                    blob.write_bytes(b"x" * blob.stat().st_size)
                elif damage == "input":
                    archive = svgstore.load_input_archive(
                        moved.references[0]["new_ref"], storage_root=moved.storage_root
                    )
                    blob = archive.resources[0].path
                    blob.write_bytes(b"x" * blob.stat().st_size)
                else:
                    # A caller cannot smuggle an unrelated current marker through
                    # the history-only consumer's final verification.
                    marker = moved.storage_root / "sources" / ("0" * 64 + ".json")
                    marker.write_text("{}", encoding="utf-8")
                stage = moved.stage_root
        assert not stage.exists()
        chartpackage.plan_project_resources(original)


def test_closed_package_and_unapproved_rule_table_never_create_target(package, tmp_path):
    path, _doc, _refs, _expected = package
    target = tmp_path / "new"
    with _open(path) as original:
        with pytest.raises(ValueError):
            with packagerelocation.prepare_relocation(
                original, project_root=target, remap_rules=[{"kind": "file", "from": "x"}]
            ):
                pytest.fail("Invalid rule table was treated as approved")
    with pytest.raises(chartpackage.PackageError, match="closed"):
        with packagerelocation.prepare_relocation(original, project_root=target, remap_rules=[]):
            pytest.fail("A closed package was consumed")
    assert not target.exists()


@pytest.mark.parametrize("directory", [False, True])
def test_publication_rejects_unplanned_files_and_empty_directories(package, tmp_path, directory):
    path, _doc, _refs, _expected = package
    with _open(path) as original:
        with pytest.raises(chartpackage.PackageError, match="unplanned member"):
            with packagerelocation.prepare_relocation(
                original, project_root=tmp_path / "new", remap_rules=[]
            ) as moved:
                injected = moved.stage_root / "unexpected"
                if directory:
                    injected.mkdir()
                else:
                    injected.write_bytes(b"must never be published")


def test_publication_file_plan_omits_locks_and_rechecks_its_streams(package, tmp_path):
    path, _doc, _refs, _expected = package
    with (
        _open(path) as original,
        packagerelocation.prepare_relocation(
            original, project_root=tmp_path / "new", remap_rules=[]
        ) as moved,
    ):
        with moved.input_view(moved.references[0]["new_ref"]):
            with pytest.raises(chartpackage.PackageError, match="active input view"):
                _ = moved.publication_files
        files = moved.publication_files
        assert files and len({name for name, _resource in files}) == len(files)
        assert all(not name.endswith("/.lock") for name, _resource in files)
        assert any(name == "current.csv" for name, _resource in files)
        assert all(resource.path.is_relative_to(moved.stage_root) for _name, resource in files)
        for _name, resource in files:
            with io.BytesIO() as sink:
                inputdeclaration.copy_resource(resource, sink)
                assert len(sink.getvalue()) == resource.size_bytes


def test_old_machine_contexts_can_converge_without_losing_reference_occurrences(package, tmp_path):
    path, doc, refs, _expected = package
    project = tmp_path / "equivalent-histories"
    store = project / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    old_refs = []
    with _open(path) as original:
        plan = chartpackage.plan_project_resources(original)
        from tavotto.engine import packageinspection

        packageinspection._figure_facts(plan, temporary_root=tmp_path / "checked-original")
        record = svgstore.inspect_portable_snapshot(
            refs[0], storage_root=tmp_path / "checked-original" / svgstore.STORE_DIRNAME
        )
        for fingerprint in ("old-machine-a", "old-machine-b"):
            old_refs.append(
                svgstore.save(
                    project,
                    refs[0]["sourceId"],
                    refs[0]["patchHash"],
                    pdf=record["pdf"],
                    svg=record["svg"],
                    artifact=record["pdf_artifact"],
                    receipt=record["receipt"],
                    execution_owner=record["execution_owner"],
                    remap_fingerprint=fingerprint,
                    storage_root=store,
                    captured_input_archive=record["captured_input_archive"],
                    archive_only=True,
                )["snapshot_ref"]
            )
    facts = svgstore.inspect_portable_store(storage_root=store)
    assert len(facts["snapshots"]) == 2
    resources = []
    for name in facts["members"]:
        if name == ".lock":
            continue
        file = store / name
        resources.append(
            chartpackage.ResourceFile(
                {
                    "kind": "figure_snapshot",
                    "path": f"assets/{store.relative_to(project).as_posix()}/{name}",
                    "sha256": svgstore._digest(file.read_bytes()),
                    "bytes": file.stat().st_size,
                },
                file,
            )
        )
    doc = copy.deepcopy(doc)
    doc["canvases"] = doc["canvases"][:1]
    first = doc["canvases"][0]["objects"][0]
    first["figureSnapshot"] = old_refs[0]
    second = copy.deepcopy(first)
    second.update(id="second", figureSnapshot=old_refs[1])
    doc["canvases"][0]["objects"].append(second)
    equivalent = tmp_path / "equivalent.tavotto"
    chartpackage.write_chart_package(
        equivalent,
        doc,
        project / config.PROJECT_STORE_DIRNAME,
        resources=resources,
        metadata={"execution": _execution(facts)},
        budget_factory=packagebudget.portable_output_budget,
    )
    with (
        _open(equivalent) as original,
        packagerelocation.prepare_relocation(
            original, project_root=tmp_path / "new", remap_rules=[]
        ) as moved,
    ):
        assert len(moved.references) == 2
        assert {row["old_ref"]["snapshotId"] for row in moved.references} == {
            ref["snapshotId"] for ref in old_refs
        }
        assert len({row["new_ref"]["snapshotId"] for row in moved.references}) == 1
        assert len(moved.execution["captures"]) == 1
        panels = moved.document["canvases"][0]["objects"]
        assert len(panels) == 2 and panels[0]["figureSnapshot"] == panels[1]["figureSnapshot"]
