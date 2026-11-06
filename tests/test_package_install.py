"""A checked v4 package publishes a new resource tree without executing Python.

Readable archived pairs and real captured inputs come from the relocation
fixture. Declarative replay is injected by the caller; HTTP/document saving
and scientific execution are deliberately separate qualification subjects.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_package_relocation as relocation_fixtures
from tavotto.engine import (
    atomicio,
    chartpackage,
    config,
    inputremap,
    packagebudget,
    packageinstall,
    pool,
    registry,
    runconfig,
    svgstore,
)

package = relocation_fixtures.package
ARGV = ["--label", "中文 空格", "", "--", "-3", "--label", "second"]


def _tree(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _resource(kind, identity, data):
    return chartpackage.ResourceFile(
        {
            "kind": kind,
            "id": identity,
            "path": ("scripts/" if kind == "script" else "assets/") + identity,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        },
        data,
    )


def _rewrite(source, destination, *, execution=None, additional=()):
    with relocation_fixtures._open(source) as prepared:
        resources = [item for _target, item in chartpackage.plan_project_resources(prepared)]
        chartpackage.write_chart_package(
            destination,
            prepared.document,
            prepared.storage_root,
            resources=[*resources, *additional],
            metadata={"execution": execution or prepared.manifest["execution"]},
            budget_factory=packagebudget.portable_output_budget,
        )


@pytest.fixture
def install_case(package, tmp_path, monkeypatch):
    path, document, refs, expected = package
    original = runconfig.put(tmp_path / "source", "plot.py", ARGV)
    canary = tmp_path / "foreign-python-was-executed"
    script = (
        "from pathlib import Path\n"
        f"Path({str(canary)!r}).write_bytes(b'executed')\n"
        "def main():\n    raise RuntimeError('must not run during installation')\n"
    ).encode("utf-8")
    with relocation_fixtures._open(path) as prepared:
        execution = prepared.manifest["execution"]
    execution["runs"] = [{"id": original.id, "script": original.script, "argv": list(ARGV)}]
    source = tmp_path / "with-exact-arguments.tavotto"
    _rewrite(
        path,
        source,
        execution=execution,
        additional=(_resource("script", "plot.py", script),),
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Installing resources must not start a scientific worker")

    monkeypatch.setattr(pool, "get", forbidden)
    monkeypatch.setattr(pool, "one_shot", forbidden)
    return SimpleNamespace(
        path=source,
        document=document,
        refs=refs,
        expected=expected,
        execution=execution,
        run=original,
        script=script,
        canary=canary,
        source_tree=_tree(tmp_path / "source"),
    )


def _install(case, destination, verify=lambda _doc, _stage: None):
    with relocation_fixtures._open(case.path) as prepared:
        original = prepared.document_bytes
        result = packageinstall.install_project_package(
            prepared,
            destination=destination,
            remap_rules=relocation_fixtures._rules(destination),
            verify=verify,
        )
        assert prepared.document_bytes == original
        assert prepared.document == case.document
    assert not case.canary.exists()
    return result


def _assert_unchanged_source(case, tmp_path, original_package):
    assert case.path.read_bytes() == original_package
    assert _tree(tmp_path / "source") == case.source_tree
    assert not case.canary.exists()


def _assert_installed_pairs(case, destination, result):
    assert result["path"] == str(destination)
    assert result["document_saved"] is False
    assert result["missing"] == []
    mapping = {
        json.dumps(row["old_ref"], sort_keys=True): row["new_ref"] for row in result["references"]
    }
    assert len(mapping) == len(case.refs)
    restored = copy.deepcopy(result["doc"])
    for canvas in restored["canvases"]:
        for item in canvas["objects"]:
            item["figureSnapshot"] = next(
                row["old_ref"]
                for row in result["references"]
                if row["new_ref"] == item["figureSnapshot"]
            )
    assert restored == case.document
    store = destination / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    facts = svgstore.inspect_portable_store(storage_root=store)
    assert not facts["entries"] and not facts["sources"]
    assert len(facts["snapshots"]) == len(case.refs)
    for ref, expected in zip(case.refs, case.expected, strict=True):
        new = mapping[json.dumps(ref, sort_keys=True)]
        saved = svgstore.load_document_snapshot(
            destination,
            new,
            source_id=new["sourceId"],
            patch_hash=new["patchHash"],
            remap_fingerprint=result["remap_fingerprint"],
            storage_root=store,
        )
        assert saved is not None
        assert saved["pdf"] == expected["pdf"] and saved["svg"] == expected["svg"]
        assert saved["captured_input_archive"].graph_bytes == expected["graph"]
    assert (destination / "current.csv").read_bytes() == b"new current file\n"
    assert (destination / "plot.py").read_bytes() == case.script
    assert registry.open_registry(destination).entries() == case.execution["registry"]["scripts"]
    prefix = f"{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
    assert set(_tree(destination)) == {
        "current.csv",
        "plot.py",
        registry.REGISTRY_NAME,
        *(prefix + name for name in facts["members"] if name != ".lock"),
    }


def test_install_publishes_exact_history_then_imports_original_arguments_without_execution(
    install_case, tmp_path
):
    destination = tmp_path / "中文 新项目"
    original_package = install_case.path.read_bytes()
    observed = []

    def verify(document, stage):
        assert stage.parent == destination.parent
        assert stage != destination and stage.is_dir() and not destination.exists()
        assert (stage / "current.csv").read_bytes() == b"new current file\n"
        assert (stage / "plot.py").read_bytes() == install_case.script
        assert not install_case.canary.exists()
        observed.append(copy.deepcopy(document))

    result = _install(install_case, destination, verify)
    assert result["state"] == "resources_installed" and result["configuration"] == "ready"
    assert observed == [result["doc"]]
    _assert_installed_pairs(install_case, destination, result)
    assert result["execution"]["runs"] == install_case.execution["runs"]
    restored = runconfig.get(destination, install_case.run.id, script="plot.py")
    assert restored.id == install_case.run.id and list(restored.argv) == ARGV
    stored = json.loads(runconfig.store_path(destination).read_text(encoding="utf-8"))
    assert not stored.get("defaults") and not stored.get("launches") and not stored.get("captures")
    assert inputremap.rules_for(destination) == relocation_fixtures._rules(destination)
    assert inputremap.fingerprint(destination) == result["remap_fingerprint"]
    _assert_unchanged_source(install_case, tmp_path, original_package)


def test_install_requires_verification_before_creating_a_destination(install_case, tmp_path):
    destination = tmp_path / "missing-verifier"
    original = install_case.path.read_bytes()
    with pytest.raises(chartpackage.PackageError) as caught:
        _install(install_case, destination, None)
    assert caught.value.reason == "verification_required"
    assert not destination.exists()
    _assert_unchanged_source(install_case, tmp_path, original)


@pytest.mark.parametrize(
    "damage", ["throw", "document", "copied_bytes", "registry_bytes", "extra_file", "empty_dir"]
)
def test_prepublication_verification_failure_discards_only_its_stage(
    install_case, tmp_path, damage
):
    destination = tmp_path / "must-not-publish"
    original = install_case.path.read_bytes()
    stages = []

    def verify(document, stage):
        stages.append(stage)
        if damage == "throw":
            raise RuntimeError("declarative replay did not verify")
        if damage == "document":
            document["canvases"][0]["name"] = "verification may not edit the document"
        elif damage in {"copied_bytes", "registry_bytes"}:
            path = stage / ("current.csv" if damage == "copied_bytes" else registry.REGISTRY_NAME)
            raw = path.read_bytes()
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        elif damage == "extra_file":
            (stage / "unplanned.txt").write_bytes(b"unexpected")
        elif damage == "empty_dir":
            (stage / "unplanned-empty-directory").mkdir()

    error = RuntimeError if damage == "throw" else ValueError
    if damage in {"document", "extra_file", "empty_dir"}:
        error = chartpackage.PackageError
    with pytest.raises(error) as caught:
        _install(install_case, destination, verify)
    if damage == "document":
        assert caught.value.reason == "verification_changed_document"
    elif damage in {"extra_file", "empty_dir"}:
        assert caught.value.reason == "relocation_invalid"
    assert stages and all(not stage.exists() for stage in stages)
    assert not destination.exists()
    _assert_unchanged_source(install_case, tmp_path, original)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_existing_destination_is_never_overwritten(install_case, tmp_path, kind):
    destination = tmp_path / "already-owned"
    if kind == "directory":
        destination.mkdir()
        (destination / "keep.bin").write_bytes(b"owned directory")
        before = _tree(destination)
    else:
        destination.write_bytes(b"owned file")
        before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        _install(install_case, destination, lambda *_args: pytest.fail("verification ran"))
    assert (_tree(destination) if kind == "directory" else destination.read_bytes()) == before


def test_racing_destination_at_atomic_publication_is_not_replaced(install_case, tmp_path):
    destination = tmp_path / "racing-owner"
    stages = []

    def verify(_document, stage):
        stages.append(stage)
        destination.mkdir()
        (destination / "keep.bin").write_bytes(b"another installer won")

    with pytest.raises(FileExistsError):
        _install(install_case, destination, verify)
    assert _tree(destination) == {"keep.bin": b"another installer won"}
    assert all(not stage.exists() for stage in stages)
    assert inputremap.raw_setting(destination) is None
    assert not runconfig.store_path(destination).exists()


def test_postpublication_parent_fsync_failure_keeps_the_published_tree_and_receipt(
    install_case, tmp_path, monkeypatch
):
    destination = tmp_path / "published-not-confirmed"
    original = atomicio.fsync_dir
    reached = []

    def fsync(path):
        if Path(path) == destination.parent and destination.is_dir():
            reached.append(Path(path))
            raise OSError(errno.EIO, "parent directory durability failed after rename")
        return original(path)

    monkeypatch.setattr(atomicio, "fsync_dir", fsync)
    result = _install(install_case, destination)
    assert reached
    assert result["state"] == "publication_unconfirmed"
    assert result["configuration"] == "pending"
    _assert_installed_pairs(install_case, destination, result)
    assert not runconfig.store_path(destination).exists()
    assert inputremap.raw_setting(destination) is None


def test_original_run_config_conflict_keeps_resources_and_existing_machine_bytes(
    install_case, tmp_path
):
    destination = tmp_path / "configuration-conflict"
    runconfig.import_portable(
        destination,
        [{"id": install_case.run.id, "script": "plot.py", "argv": ["different", ""]}],
    )
    config_path = runconfig.store_path(destination)
    before = config_path.read_bytes()
    result = _install(install_case, destination)
    assert result["state"] == "resources_installed" and result["configuration"] == "pending"
    assert result["configuration_error"] == runconfig.ERROR_UNSUPPORTED
    assert config_path.read_bytes() == before
    assert runconfig.get(destination, install_case.run.id).argv == ("different", "")
    assert inputremap.raw_setting(destination) is None
    _assert_installed_pairs(install_case, destination, result)


def test_original_configuration_write_failure_is_pending_after_resource_publication(
    install_case, tmp_path, monkeypatch
):
    destination = tmp_path / "configuration-unwritable"
    real_write = atomicio.write_json

    def write(path, *args, **kwargs):
        if Path(path) == runconfig.store_path(destination):
            raise OSError(errno.EACCES, "machine configuration is not writable")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", write)
    result = _install(install_case, destination)
    assert result["state"] == "resources_installed" and result["configuration"] == "pending"
    assert result["configuration_error"] == runconfig.ERROR_UNREADABLE
    assert not runconfig.store_path(destination).exists()
    assert inputremap.raw_setting(destination) is None
    _assert_installed_pairs(install_case, destination, result)


def test_source_exit_recheck_after_publish_returns_the_published_path_without_recursive_rollback(
    install_case, tmp_path, monkeypatch
):
    destination = tmp_path / "source-changed-after-publish"
    publish = atomicio.publish_directory_if_absent
    original_package = install_case.path.read_bytes()
    with relocation_fixtures._open(install_case.path) as prepared:
        resource = next(
            item
            for _target, item in chartpackage.plan_project_resources(prepared)
            if item.record.get("id") == "current.csv"
        )

        def publish_then_change_source(stage, target):
            publish(stage, target)
            path = Path(resource.source)
            raw = path.read_bytes()
            path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])

        monkeypatch.setattr(atomicio, "publish_directory_if_absent", publish_then_change_source)
        result = packageinstall.install_project_package(
            prepared,
            destination=destination,
            remap_rules=relocation_fixtures._rules(destination),
            verify=lambda _document, _stage: None,
        )
    assert result["state"] == "installation_unconfirmed"
    assert result["path"] == str(destination)
    _assert_installed_pairs(install_case, destination, result)
    assert install_case.path.read_bytes() == original_package
    assert not install_case.canary.exists()


@pytest.mark.parametrize("compatible", [False, True])
def test_ordinary_registry_asset_is_never_silently_rewritten(install_case, tmp_path, compatible):
    expected = install_case.execution["registry"] if compatible else {"scripts": {}}
    original_registry = json.dumps(expected, ensure_ascii=False, indent=4).encode() + b"\n\n"
    replacement = tmp_path / "with-ordinary-registry.tavotto"
    _rewrite(
        install_case.path,
        replacement,
        additional=(_resource("file", registry.REGISTRY_NAME, original_registry),),
    )
    case = copy.copy(install_case)
    case.path = replacement
    original_package = replacement.read_bytes()
    destination = tmp_path / "ordinary-registry"
    if compatible:
        result = _install(case, destination)
        assert result["state"] == "resources_installed" and result["configuration"] == "ready"
        assert registry.registry_path(destination).read_bytes() == original_registry
        _assert_installed_pairs(case, destination, result)
    else:
        with pytest.raises(chartpackage.PackageError) as caught:
            _install(case, destination)
        assert caught.value.reason == "installation_metadata_conflict"
        assert not destination.exists()
    assert replacement.read_bytes() == original_package
