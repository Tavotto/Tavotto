"""Prepare relocated immutable Figure references in a context-owned project.

This consumes the original package/store authorities. It neither publishes a
project nor writes document settings, current execution markers or grants.
Historical input views are materialized one at a time and checked by the
capture freezer; a matching view is not permission to execute its script.
"""

from __future__ import annotations

import copy
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from . import (
    atomicio,
    chartpackage,
    config,
    documents,
    figcapture,
    inputdeclaration,
    inputremap,
    packageexecution,
    packageinspection,
    projectenv,
    svgstore,
)

INPUT_VIEWS_DIRNAME = "input-views"


def _invalid(detail):
    raise chartpackage.PackageError("relocation_invalid", detail)


def _path(root, relative):
    # IDs have already passed the original package member/tree validator. Keep
    # containment at the actual filesystem boundary as well.
    result = projectenv.contained_path(root, relative)
    if result is None:
        _invalid("Relocated resource escapes its owned root")
    return Path(result)


def _copy_resource(resource, destination):
    if destination.exists() or destination.is_symlink():
        inputdeclaration.verify_resource(
            inputdeclaration.InputResource(resource.sha256, resource.size_bytes, destination)
        )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        inputdeclaration.copy_resource(resource, output)


def _limits():
    policy = svgstore.input_store_budget()
    return {"max_files": policy.capture_max_files, "max_bytes": policy.capture_max_bytes}


def _materialize_view(archive, root):
    graph = inputdeclaration.validate_input_archive(archive, limits=_limits())
    # Reuse the existing portable role/tree authority, including aliases,
    # prefix directory parents and the shadow-script/data distinction.
    packageexecution._portable_graph(graph)
    resources = {resource.sha256: resource for resource in archive.resources}

    def write(row, path):
        _copy_resource(resources[row["sha256"]], path)
        return str(path)

    script = _path(root / "script", graph["scope"]["script"])
    write(graph["script"], script)
    paths, files, prefixes = {}, [], []

    def target(identifier):
        if identifier.startswith("remap:"):
            return root / "remaps" / identifier.removeprefix("remap:")
        return _path(root / "project", identifier)

    for row in graph["files"]:
        path = target(row["id"])
        files.append({"id": row["id"], "path": write(row, path)})
        paths[row["id"]] = path
    for row in graph["prefixes"]:
        path = target(row["id"])
        for relative in row["directories"]:
            _path(path, relative).mkdir(parents=True, exist_ok=True)
        for member in row["members"]:
            write(member, _path(path, member["suffix"]))
        prefixes.append({"id": row["id"], "path": str(path)})
        paths[row["id"]] = path
    return inputdeclaration.validate_candidate(
        {
            "version": 1,
            "scope": graph["scope"],
            "script_path": str(script),
            "files": files,
            "prefixes": prefixes,
            "mappings": graph["mappings"],
            "rules": [
                {"kind": row["kind"], "from": row["from"], "to": str(paths[row["id"]])}
                for row in graph["mappings"]
            ],
            "limits": _limits(),
        }
    )


class PreparedRelocation:
    """Detached metadata and owned stages, valid only inside prepare_relocation."""

    def __init__(self, project_root, stage, document, references, execution, rules, resources):
        self._project_root = project_root
        self._stage = stage
        self._document = atomicio.dumps_json(document)
        self._references = copy.deepcopy(references)
        self._execution = copy.deepcopy(execution)
        self._rules = copy.deepcopy(rules)
        self._fingerprint = inputremap.fingerprint_rules(rules)
        self._resources = resources
        self._active = True
        self._view_lock = threading.Lock()

    def _require_active(self):
        if not self._active:
            raise chartpackage.PackageError("prepared_closed", "Relocation context has closed")

    @property
    def stage_root(self):
        self._require_active()
        return self._stage

    @property
    def storage_root(self):
        return self.stage_root / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME

    @property
    def document(self):
        self._require_active()
        return documents.loads_document(self._document)

    @property
    def references(self):
        self._require_active()
        return copy.deepcopy(self._references)

    @property
    def execution(self):
        self._require_active()
        return copy.deepcopy(self._execution)

    @property
    def remap_rules(self):
        self._require_active()
        return copy.deepcopy(self._rules)

    @property
    def remap_fingerprint(self):
        self._require_active()
        return self._fingerprint

    def _record(self, reference):
        self._require_active()
        if reference not in [row["new_ref"] for row in self._references]:
            _invalid("Figure reference is not part of this relocation")
        record = svgstore.load_document_snapshot(
            self._project_root,
            reference,
            source_id=reference["sourceId"],
            patch_hash=reference["patchHash"],
            remap_fingerprint=self._fingerprint,
            storage_root=self.storage_root,
        )
        if record is None:
            _invalid("Relocated Figure no longer matches its complete reference")
        return record

    def verify(self):
        """Recheck staged bytes, without consulting or authorizing the new machine."""
        self._require_active()
        if self._view_lock.locked():
            _invalid("Finish the active input view before verifying relocation")
        for resource in self._resources:
            inputdeclaration.verify_resource(resource)
        facts = None
        if self._references:
            facts = svgstore.inspect_portable_store(storage_root=self.storage_root)
            if set(facts["snapshots"]) != {
                row["new_ref"]["snapshotId"] for row in self._references
            }:
                _invalid("Relocated Figure inventory changed")
            if facts["entries"] or facts["sources"]:
                _invalid("Historical relocation created current execution records")
            if self._execution is not None:
                packageexecution.validate_capture_facts(self._execution, facts)
            for row in self._references:
                checked = facts["snapshots"][row["new_ref"]["snapshotId"]]
                if (
                    checked["reference"] != row["new_ref"]
                    or checked["identity"]["remap_fingerprint"] != self._fingerprint
                ):
                    _invalid("Relocated Figure identity changed")
        return self._members(facts)

    def _members(self, facts):
        root_info = self._stage.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or getattr(
            root_info, "st_file_attributes", 0
        ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            _invalid("Relocation stage root is not its owned directory")
        expected = {
            resource.path.relative_to(self._stage).as_posix() for resource in self._resources
        }
        directories = set()
        if facts is not None:
            prefix = self.storage_root.relative_to(self._stage).as_posix()
            expected.update(prefix + "/" + name for name in facts["members"])
            directories.update(prefix + "/" + name for name in ("entries", "sources"))
        for relative in (*expected, *tuple(directories)):
            directories.update(
                parent.as_posix() for parent in Path(relative).parents if parent.as_posix() != "."
            )
        stack = [self._stage]
        while stack:
            parent = stack.pop()
            with os.scandir(parent) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relative = path.relative_to(self._stage).as_posix()
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(
                        info, "st_file_attributes", 0
                    ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                        _invalid("Relocation stage contains a link or reparse point")
                    if stat.S_ISDIR(info.st_mode) and relative in directories:
                        stack.append(path)
                    elif not (stat.S_ISREG(info.st_mode) and relative in expected):
                        _invalid("Relocation stage contains an unplanned member")
        return sorted(expected)

    @property
    def publication_files(self):
        """The closed file plan for the original verified-stream publisher.

        The caller must use copy_resource again: these paths are not leases.
        Lock files and comparison-only temporary views are never published.
        """
        result = []
        for relative in self.verify():
            path = self._stage / relative
            if self._references and path == self.storage_root / ".lock":
                continue
            digest, size = figcapture.hash_file(path)
            result.append((relative, inputdeclaration.InputResource(digest, size, path)))
        return tuple(result)

    @contextmanager
    def input_view(self, reference):
        """Materialize one bounded historical view for comparison, never execution.

        The yielded private candidate contains paths leased by this context.
        Its bytes are checked again before normal exit. All historical versions
        remain in the immutable store; only the requested view consumes another
        capture-sized physical copy at a time.
        """
        record = self._record(reference)
        archive = record.get("captured_input_archive")
        if archive is None:
            _invalid("This historical Figure has no captured input attachment")
        if not self._view_lock.acquire(blocking=False):
            _invalid("Finish the active input view before materializing another")
        views = self.stage_root.parent / INPUT_VIEWS_DIRNAME
        try:
            views.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="view-", dir=views) as directory:
                candidate = _materialize_view(archive, Path(directory))
                expected = record["receipt"]["captured_inputs"]
                if inputdeclaration.current_public_fact(candidate) != expected:
                    _invalid("Materialized historical inputs do not match their capture")
                yield copy.deepcopy(candidate)
                if inputdeclaration.current_public_fact(candidate) != expected:
                    _invalid("Materialized historical inputs changed")
        finally:
            self._view_lock.release()


@contextmanager
def prepare_relocation(prepared, *, project_root, remap_rules):
    """Retarget every archived full reference and its document occurrences.

    project_root and rules describe the caller-approved final destination. Rule
    targets are only fingerprinted, never opened or treated as machine grants.
    The caller must compare that fingerprint with the actual target settings at
    installation. All writes here are confined to data_dir-owned stages, kept
    alive until the caller finishes verification and publication.
    """
    project_root = Path(project_root)
    if not project_root.is_absolute():
        _invalid("Relocation requires an absolute destination project identity")
    remap_rules = copy.deepcopy(remap_rules)
    fingerprint = inputremap.fingerprint_rules(remap_rules)
    plan = chartpackage.plan_project_resources(prepared)
    packageinspection.inspect_project_package(prepared)
    data_root = config.data_dir()
    data_root.mkdir(parents=True, exist_ok=True)
    result = None
    with tempfile.TemporaryDirectory(prefix="package-relocation-", dir=data_root) as directory:
        work = Path(directory)
        facts = packageinspection._figure_facts(plan, temporary_root=work / "original")
        original_store = work / "original" / svgstore.STORE_DIRNAME
        stage = work / "project"
        stage.mkdir()
        resources = []
        for relative, item in plan:
            if item.record["kind"] == "figure_snapshot":
                continue
            destination = _path(stage, relative)
            resource = inputdeclaration.InputResource(
                item.record["sha256"], item.record["bytes"], item.source
            )
            _copy_resource(resource, destination)
            resources.append(
                inputdeclaration.InputResource(resource.sha256, resource.size_bytes, destination)
            )
        references = []
        storage = stage / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
        for row in facts["snapshots"].values():
            old_ref = row["reference"]
            record = svgstore.inspect_portable_snapshot(old_ref, storage_root=original_store)
            saved = svgstore.save(
                project_root,
                old_ref["sourceId"],
                old_ref["patchHash"],
                pdf=record["pdf"],
                svg=record["svg"],
                artifact=record["pdf_artifact"],
                receipt=record["receipt"],
                execution_owner=record["execution_owner"],
                static_sha256=record["identity"]["static_sha256"],
                remap_fingerprint=fingerprint,
                storage_root=storage,
                archive_only=True,
                **(
                    {"captured_input_archive": record["captured_input_archive"]}
                    if "captured_input_archive" in record
                    else {}
                ),
            )
            references.append({"old_ref": old_ref, "new_ref": saved["snapshot_ref"]})
        mapping = {row["old_ref"]["snapshotId"]: row for row in references}

        def replacement(reference):
            row = mapping.get(reference.get("snapshotId"))
            if row is None or row["old_ref"] != reference:
                _invalid("Document reference has no exact archived source")
            return copy.deepcopy(row["new_ref"])

        document = prepared.document
        for canvas in document["canvases"]:
            for obj in canvas.get("objects", []):
                if (
                    isinstance(obj, dict)
                    and obj.get("type") == "panel"
                    and obj.get("figureSnapshot") is not None
                ):
                    obj["figureSnapshot"] = replacement(obj["figureSnapshot"])
        execution = prepared.manifest.get("execution")
        if execution is not None and execution["version"] == packageexecution.CAPTURE_VERSION:
            # Distinct old machine contexts can legitimately converge on the
            # same immutable new identity. Keep every old->new mapping, while
            # deriving the capture set from the original checked-store authority.
            execution["captures"] = packageexecution.captures_from_facts(
                svgstore.inspect_portable_store(storage_root=storage)
            )
        result = PreparedRelocation(
            project_root, stage, document, references, execution, remap_rules, tuple(resources)
        )
        try:
            result.verify()
            chartpackage.plan_project_resources(prepared)
            yield result
            result.verify()
            chartpackage.plan_project_resources(prepared)
        finally:
            result._active = False
