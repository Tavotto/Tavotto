"""Publish a checked package into one new project, without executing its code.

The original Prepared/relocation, stream, registry and configuration authorities
remain responsible for their facts. Resource publication, machine configuration
and the later document writer are separate commits with separate receipts.
"""

from __future__ import annotations

import copy
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
    inputremap,
    packageinspection,
    packagerelocation,
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


def install_project_package(prepared, *, destination, remap_rules, verify):
    """Install resources, then import suggestions; never open or save a document.

    remap_rules are caller-approved local rules, not sender machine settings.
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
            remap_rules=remap_rules,
            verify=verify,
            outcome=outcome,
        )
    except (OSError, ValueError, chartpackage.PackageError, runconfig.RunConfigError) as exc:
        # The original Prepared/relocation exit checks remain mandatory. If
        # they fail after publication, retain the new tree and identify the
        # unconfirmed installation instead of losing its receipt or deleting it.
        if outcome.get("published"):
            return {
                **outcome["receipt"],
                "state": "installation_unconfirmed",
                "configuration_error": getattr(exc, "code", "installation_unavailable"),
            }
        raise


def _install_project_package(prepared, *, destination, remap_rules, verify, outcome):
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
    remap_rules = copy.deepcopy(remap_rules)
    fingerprint = inputremap.fingerprint_rules(remap_rules)
    report = packageinspection.inspect_project_package(prepared)
    if report["missing"]:
        _invalid("installation_resources_missing", "Known project resources are missing")
    review = _local_configuration_review(destination, prepared.manifest.get("execution"))

    with inputremap.project_mutex(destination):
        previous = inputremap.raw_setting(destination)
        if previous is not None and inputremap.fingerprint(destination) != fingerprint:
            _invalid(
                "installation_metadata_conflict", "Destination already has different local remaps"
            )
        with packagerelocation.prepare_relocation(
            prepared, project_root=destination, remap_rules=remap_rules
        ) as moved:
            stage = Path(tempfile.mkdtemp(prefix=f".{brand.DIST_NAME}-install-", dir=parent))
            identity = _directory_identity(stage)
            published = False
            try:
                for relative, resource in moved.publication_files:
                    path = stage / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        inputdeclaration.copy_resource(resource, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                extra = _metadata(stage, moved.execution)
                document = moved.document
                expected_document = atomicio.dumps_json(document)
                verify(document, stage)
                if atomicio.dumps_json(document) != expected_document:
                    _invalid("verification_changed_document", "Verification changed the document")
                moved.verify_publication_copy(stage, additional=extra)
                # Every nested directory entry, including generated metadata,
                # is durable before the single directory publication point.
                for root, directories, _files in os.walk(stage, topdown=False, followlinks=False):
                    for name in directories:
                        atomicio.fsync_dir(Path(root) / name)
                if not _same_directory(stage, identity):
                    _invalid("installation_invalid", "Installation stage identity changed")
                moved.verify_publication_copy(stage, additional=extra)
                result = {
                    "state": "resources_installed",
                    "path": str(destination),
                    "document_format": report["document_format"],
                    "doc": moved.document,
                    "references": moved.references,
                    "execution": moved.execution,
                    "remap_fingerprint": fingerprint,
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
                    execution = moved.execution
                    if execution is not None:
                        runconfig.import_portable(destination, execution["runs"])
                    if previous is None and remap_rules:
                        inputremap.restore_raw(destination, {"rules": moved.remap_rules})
                    if inputremap.fingerprint(destination) != fingerprint:
                        _invalid(
                            "installation_metadata_conflict", "Installed remap identity changed"
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
                result["configuration"] = (
                    "needs_review" if review["existing_local_metadata"] else "ready"
                )
                return result
            finally:
                if not published and _same_directory(stage, identity):
                    # Only this attempt's unchanged directory is ours to remove.
                    shutil.rmtree(stage)
