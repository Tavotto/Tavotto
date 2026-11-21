"""Publish a checked package into one new project, without executing its code.

The original Prepared/stream, registry and configuration authorities remain
responsible for their facts. Resource publication, machine configuration and the
later document writer are separate commits with separate receipts.

Relocation / adoption is not part of this layer: the package's own project
projection is published as-is, with the document unchanged. A snapshot that was
sealed under another machine's remap fingerprint is reported by the read-only
inspection (`snapshot_remap_requires_relink`), not silently rebound here.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from . import (
    atomicio,
    brand,
    chartpackage,
    config,
    discover,
    figcapture,
    inputdeclaration,
    packageinspection,
    registry,
    runconfig,
    workdir,
)


def _invalid(reason, detail):
    raise chartpackage.PackageError(reason, detail)


def _directory_identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
    ):
        _invalid("installation_invalid", "Installation directory is not a real directory")
    return info


def _same_directory(path, identity):
    try:
        return os.path.samestat(_directory_identity(path), identity)
    except (FileNotFoundError, chartpackage.PackageError):
        return False


def _stage_path(root, relative):
    """Resolve one planned member inside the publication stage."""
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        _invalid("installation_invalid", "Installation stage path escaped")
    return path


def _verify_stage(stage, plan, extra):
    """Check the publication tree against the same closed file plan.

    `plan` rows are the package's own project projection (relative path plus
    `ResourceFile`); `extra` rows are caller-generated metadata. A generated
    row may never replace a packaged member, and no unplanned file may appear.
    """
    stage = Path(stage)
    names = {relative for relative, _resource in plan}
    rows = [(relative, resource.record) for relative, resource in plan]
    for relative, resource in extra:
        if relative in names:
            _invalid(
                "installation_metadata_conflict",
                "Generated metadata would replace a packaged resource",
            )
        names.add(relative)
        rows.append((relative, {"sha256": resource.sha256, "bytes": resource.size_bytes}))
    for relative, record in rows:
        inputdeclaration.verify_resource(
            inputdeclaration.InputResource(
                record["sha256"], record["bytes"], _stage_path(stage, relative)
            )
        )
    for path in stage.rglob("*"):
        if path.is_dir():
            continue
        if path.relative_to(stage).as_posix() not in names:
            _invalid("installation_invalid", "Installation stage has unexpected files")


def _metadata(stage, execution):
    expected = execution["registry"] if execution is not None else {"scripts": {}}
    checked = registry.Registry()
    checked.load_data(expected, source="<project-package>")
    path = registry.registry_path(stage)
    if path.exists() or path.is_symlink():
        # An ordinary packaged file can occupy the registry name. Preserve its
        # exact bytes only when the original registry authority agrees with the
        # execution table; semantic equality is no license to rewrite an input.
        try:
            actual = registry.open_registry(stage)
        except (OSError, ValueError, RuntimeError) as exc:
            raise chartpackage.PackageError(
                "installation_metadata_conflict", "Packaged registry is unreadable"
            ) from exc
        if actual.entries() != checked.entries():
            _invalid("installation_metadata_conflict", "Packaged registry and execution disagree")
        return ()
    discover.write_config(stage, expected, static_discovery=False)
    digest, size = figcapture.hash_file(path)
    return (
        (path.relative_to(stage).as_posix(), inputdeclaration.InputResource(digest, size, path)),
    )


def _local_configuration_review(destination, execution):
    """Read existing machine choices before importing anything or taking its project lock."""
    existing = (
        bool(config.project_settings(str(destination)))
        or runconfig.store_path(destination).exists()
    )
    result = {
        "version": 1,
        "existing_local_metadata": existing,
        "workdir": None,
        "defaults": [],
        "issues": [],
    }
    if not existing:
        return result
    # These original read authorities retain their distinction between unset,
    # known empty arguments, unavailable secrets and an actual cwd grant. In
    # particular, no raw argv is added to this public projection.
    result["workdir"] = workdir.state(destination)
    result["issues"].append({"reason": "existing_local_metadata", "script": None})
    execution = execution or {}
    contexts = execution.get("contexts", [])
    for script in execution.get("registry", {}).get("scripts", {}):
        selection, unavailable = None, None
        try:
            selected = runconfig.default_selection(destination, script)
            if selected is not None:
                selection = runconfig.get(destination, selected.config_id, script=script).public()
        except runconfig.RunConfigError as exc:
            unavailable = exc.code
        result["defaults"].append(
            {"script": script, "selection": selection, "unavailable_code": unavailable}
        )
        known = [row for row in contexts if row["script"] == script and row["run_config_known"]]
        reasons = []
        if unavailable:
            reasons.append("local_default_unavailable")
        if not known:
            reasons.append("execution_context_unknown")
        else:
            if selection is not None and selection["id"] not in {
                row["run_config"] for row in known
            }:
                reasons.append("local_default_differs")
            if result["workdir"]["mode"] not in {row["cwd_mode"] for row in known}:
                reasons.append("local_cwd_differs")
        result["issues"].extend({"reason": reason, "script": script} for reason in reasons)
    return result


def install_project_package(prepared, *, destination, verify):
    """Install resources, then import suggestions; never open or save a document.

    verify(document, project_stage) must replay declarative charts through the
    original renderer without running packaged Python. A missing known resource
    prevents installation; incomplete observation is reported as such.

    Once the directory has been published it is never recursively rolled back.
    A later durability/configuration failure returns the published path and the
    unconfirmed phase, so the caller can preserve its old working document.
    """
    outcome = {}
    try:
        return _install_project_package(
            prepared,
            destination=destination,
            verify=verify,
            outcome=outcome,
        )
    except (OSError, ValueError, chartpackage.PackageError, runconfig.RunConfigError) as exc:
        # If the package's own exit checks fail after publication, retain the
        # new tree and identify the unconfirmed installation instead of losing
        # its receipt or deleting it.
        if outcome.get("published"):
            return {
                **outcome["receipt"],
                "state": "installation_unconfirmed",
                "configuration_error": getattr(exc, "code", "installation_unavailable"),
            }
        raise


def _install_project_package(prepared, *, destination, verify, outcome):
    if not callable(verify):
        _invalid("verification_required", "A chart replay verification callback is required")
    destination = Path(destination)
    if not destination.is_absolute() or not destination.name:
        _invalid("installation_invalid", "An absolute new project directory is required")
    parent = destination.parent.resolve(strict=True)
    _directory_identity(parent)
    destination = parent / destination.name
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(destination)
    report = packageinspection.inspect_project_package(prepared)
    if report["missing"]:
        _invalid("installation_resources_missing", "Known project resources are missing")
    execution = prepared.manifest.get("execution")
    runs = execution["runs"] if execution is not None else []
    if runs:
        runconfig.validate_portable(runs)
    review = _local_configuration_review(destination, execution)
    plan = prepared.plan_project_resources()

    stage = Path(tempfile.mkdtemp(prefix=f".{brand.DIST_NAME}-install-", dir=parent))
    identity = _directory_identity(stage)
    published = False
    try:
        for relative, resource in plan:
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            source = inputdeclaration.InputResource(
                resource.record["sha256"], resource.record["bytes"], Path(resource.source)
            )
            with path.open("xb") as stream:
                inputdeclaration.copy_resource(source, stream)
                stream.flush()
                os.fsync(stream.fileno())
        extra = _metadata(stage, execution)
        document = prepared.document
        expected_document = atomicio.dumps_json(document)
        verify(document, stage)
        if atomicio.dumps_json(document) != expected_document:
            _invalid("verification_changed_document", "Verification changed the document")
        _verify_stage(stage, plan, extra)
        # Every nested directory entry, including generated metadata, is
        # durable before the single directory publication point.
        for root, directories, _files in os.walk(stage, topdown=False, followlinks=False):
            for name in directories:
                atomicio.fsync_dir(Path(root) / name)
        if not _same_directory(stage, identity):
            _invalid("installation_invalid", "Installation stage identity changed")
        _verify_stage(stage, plan, extra)
        result = {
            "state": "resources_installed",
            "path": str(destination),
            "document_format": report["document_format"],
            "doc": document,
            "execution": execution,
            "missing": report["missing"],
            "warnings": report["warnings"],
            "execution_coverage": report["execution_coverage"],
            "configuration": "pending",
            "configuration_review": review,
            "document_saved": False,
        }
        outcome["receipt"] = result
        try:
            atomicio.publish_directory_if_absent(stage, destination)
        except OSError:
            if _same_directory(destination, identity):
                published = True
                outcome["published"] = True
                return {**result, "state": "publication_unconfirmed"}
            raise
        published = True
        outcome["published"] = True
        try:
            if runs:
                _imported, result["runconfig_skipped"] = runconfig.import_portable(
                    destination, runs
                )
        except (
            OSError,
            ValueError,
            runconfig.RunConfigError,
            chartpackage.PackageError,
        ) as exc:
            return {
                **result,
                "configuration_error": getattr(exc, "code", "installation_unavailable"),
            }
        result["configuration"] = "needs_review" if review["existing_local_metadata"] else "ready"
        return result
    finally:
        if not published and _same_directory(stage, identity):
            # Only this attempt's unchanged directory is ours to remove.
            shutil.rmtree(stage)
