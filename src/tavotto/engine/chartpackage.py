"""v4 包的来源附件准备与显式发布；ZIP、来源字节和正文仍各有原权威。

检视只在 data_dir 自有暂存目录内核验，不安装素材或执行脚本。调用方必须先
全量重放 verify，再安装不可变 chartSource，最后沿现有文档事务提交正文。
普通素材/脚本/配对快照保留为已核验资源，安装由调用方另行完成。
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from . import atomicio, brand, config, documentassets, documentformat, documentpackage, documents

PackageError = documentpackage.PackageError
PackageBudget = documentpackage.PackageBudget
_CHART_PREFIX = f"assets/{config.PROJECT_STORE_DIRNAME}/{documentassets.CHART_SOURCES_DIRNAME}/"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ResourceFile:
    """调用方批准的资源及其闭式清单行；Path 的项目 containment 由调用方负责。"""

    record: dict
    source: bytes | Path


def _copy(value):
    return documents.loads_document(atomicio.dumps_json(value))


def _chart_refs(document: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for canvas in document["canvases"]:
        for obj in canvas.get("objects", []):
            if isinstance(obj, dict) and "chartSource" in obj:
                ref = obj["chartSource"]
                path = _CHART_PREFIX + ref["bytesSha256"] + ".svg"
                result.setdefault(path, []).append(ref)
    return result


def _chart_record(path: str, ref: dict) -> dict:
    return {
        "kind": "chart_source",
        "path": path,
        "sha256": ref["bytesSha256"],
        "bytes": ref["sizeBytes"],
    }


def _record(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise PackageError("invalid_resource", "Resource row must be an object")
    kind = raw.get("kind")
    if not isinstance(kind, str):
        raise PackageError("invalid_resource", "Unknown resource kind")
    fields = {"kind", "path", "sha256", "bytes"}
    if kind in {"file", "script"}:
        fields.add("id")
    elif kind not in {"chart_source", "figure_snapshot"}:
        raise PackageError("invalid_resource", "Unknown resource kind")
    if (
        set(raw) != fields
        or not isinstance(raw["path"], str)
        or not isinstance(raw["sha256"], str)
        or not _HASH.fullmatch(raw["sha256"])
        or type(raw["bytes"]) is not int
        or raw["bytes"] < 0
    ):
        raise PackageError("invalid_resource", "Invalid resource fields")
    path = raw["path"]
    if kind in {"file", "script"}:
        resource_id = raw["id"]
        prefix = "scripts/" if kind == "script" else "assets/"
        if (
            not isinstance(resource_id, str)
            or not resource_id
            or "\\" in resource_id
            or PurePosixPath(resource_id).is_absolute()
            or PurePosixPath(resource_id).as_posix() != resource_id
            or ".." in resource_id.split("/")
            or path != prefix + resource_id
        ):
            raise PackageError("invalid_resource", "Resource ID and member path disagree")
    elif not path.startswith("assets/"):
        raise PackageError("invalid_resource", "Snapshot/source must be in assets")
    if path.casefold().startswith(_CHART_PREFIX.casefold()) != (kind == "chart_source"):
        raise PackageError("invalid_resource", "Chart source namespace is reserved")
    if kind == "chart_source" and not 0 < raw["bytes"] <= documentassets.MAX_SOURCE_BYTES:
        raise PackageError("invalid_resource", "Invalid chart source byte budget")
    return _copy(raw)


def _records(manifest: dict, refs: dict[str, list[dict]]) -> tuple[dict, ...]:
    raw = manifest.get("assets")
    if not isinstance(raw, list):
        raise PackageError("invalid_resource", "Resource index is required")
    records = tuple(_record(row) for row in raw)
    seen = set()
    chart_rows = {}
    for row in records:
        if row["path"] in seen:
            raise PackageError("invalid_resource", "Duplicate resource row")
        seen.add(row["path"])
        if row["kind"] == "chart_source":
            chart_rows[row["path"]] = row
    expected = {path: _chart_record(path, group[0]) for path, group in refs.items()}
    if chart_rows != expected:
        raise PackageError("chart_source_mismatch", "Resource index and chart references disagree")
    return records


def _check_bytes(row: dict, data: bytes) -> None:
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise PackageError("resource_mismatch", "Resource bytes and digest disagree")


def _read_staged(path: Path, row: dict) -> bytes:
    """仅读本上下文的已知数字暂存路径；回调后仍核 regular-file/identity/预算。"""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise PackageError("resource_mismatch", "Staged resource is not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise PackageError("resource_mismatch", "Staged resource changed")
        data = stream.read(row["bytes"] + 1)
    after = path.lstat()
    if not os.path.samestat(before, after) or (
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_size, after.st_mtime_ns):
        raise PackageError("resource_mismatch", "Staged resource changed")
    _check_bytes(row, data)
    return data


class PreparedChartPackage:
    """仅在 inspect 上下文内存活；正文/清单副本不改变已核验的冻结字节。"""

    def __init__(self, document: dict, manifest: dict, storage_root: Path, resources: tuple):
        self._document = atomicio.dumps_json(document)
        self._manifest = atomicio.dumps_json(manifest)
        self._root = storage_root
        self._resources = resources
        self._active = True

    def _require_active(self) -> None:
        if not self._active:
            raise PackageError("prepared_closed", "Prepared package context has closed")

    @property
    def document_bytes(self) -> bytes:
        self._require_active()
        return self._document

    @property
    def document(self) -> dict:
        return documents.loads_document(self.document_bytes)

    @property
    def manifest(self) -> dict:
        self._require_active()
        return documents.loads_document(self._manifest)

    @property
    def storage_root(self) -> Path:
        self._require_active()
        return self._root

    @property
    def resources(self) -> tuple[ResourceFile, ...]:
        self._require_active()
        return tuple(ResourceFile(_copy(row), path) for row, path in self._resources)

    def _verify(self) -> None:
        self._require_active()
        for group in _chart_refs(self.document).values():
            for ref in group:
                documentassets.load_source(self._root, ref)
        for row, path in self._resources:
            if row["kind"] != "chart_source":
                _read_staged(path, row)


@contextmanager
def inspect_chart_package(
    source: bytes | BinaryIO, *, budget: PackageBudget
) -> Iterator[PreparedChartPackage]:
    """严格检视并核验完整 INPUT；不写目标项目，不执行脚本，不自动安装。"""
    prepared = None
    data_root = config.data_dir()
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chart-package-", dir=data_root) as temporary:
        stage = Path(temporary)
        storage_root = stage / "storage"
        storage_root.mkdir()
        staged_resources = []
        try:
            with documentpackage.open_package(source, budget=budget) as package:
                document, manifest = _copy(package.document), _copy(package.manifest)
                refs = _chart_refs(document)
                records = _records(manifest, refs)
                rows = {row["path"]: row for row in records}
                needed = {name: max(1, row["bytes"]) for name, row in rows.items()}
                for index, (name, data) in enumerate(package.read_required(needed, exact=True)):
                    row = rows[name]
                    _check_bytes(row, data)
                    if row["kind"] == "chart_source":
                        for ref in refs[name]:
                            actual = documentassets.freeze_source(
                                storage_root,
                                ref["sourceId"],
                                data,
                                scene_digest=ref["sceneDigest"],
                            )
                            if actual != ref:
                                raise PackageError(
                                    "chart_source_mismatch", "Input reference changed"
                                )
                        path = documentassets.source_path(storage_root, refs[name][0])
                    else:
                        path = stage / "resources" / str(index)
                        atomicio.write_bytes_if_absent(path, data)
                    staged_resources.append((row, path))
            prepared = PreparedChartPackage(
                document, manifest, storage_root, tuple(staged_resources)
            )
            prepared._verify()
            yield prepared
        finally:
            if prepared is not None:
                prepared._active = False


def write_chart_package(
    destination: Path,
    document: dict,
    storage_root: Path,
    *,
    budget: PackageBudget,
    overwrite: bool = False,
    resources: Sequence[ResourceFile] = (),
    metadata: Mapping | None = None,
) -> dict:
    """先封口并严格检视，再原子发布最终名；不重新执行任何来源。"""
    if type(overwrite) is not bool:
        raise PackageError("invalid_output", "Invalid package output request")
    document = documents.validate_chart_document(_copy(document))
    refs = _chart_refs(document)
    records = [_chart_record(path, group[0]) for path, group in refs.items()]
    extras = []
    for item in resources:
        if not isinstance(item, ResourceFile) or not isinstance(item.source, (bytes, Path)):
            raise PackageError(
                "invalid_resource", "ResourceFile requires bytes or an approved Path"
            )
        row = _record(item.record)
        if row["kind"] == "chart_source":
            raise PackageError("invalid_resource", "Chart rows are derived only from the document")
        records.append(row)
        extras.append(ResourceFile(row, item.source))
    if metadata is not None and not isinstance(metadata, Mapping):
        raise PackageError("invalid_manifest", "Package metadata must be a mapping")
    manifest = _copy(dict(metadata or {}))
    if manifest.keys() & {"kind", "version", "document_format", "assets"}:
        raise PackageError("invalid_manifest", "Metadata cannot override owned manifest fields")
    manifest.update(
        kind=brand.PACKAGE_KIND,
        version=documentpackage.PACKAGE_VERSION,
        document_format=documentformat.CHART4,
        assets=records,
    )
    _records(manifest, refs)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".prepared", dir=destination.parent
    )
    os.close(fd)
    sealed = Path(name)
    try:
        with documentpackage.staged_package(sealed, budget=budget) as archive:
            archive.writestr(documentpackage.LAYOUT_MEMBER, atomicio.dumps_json(document))
            archive.writestr(documentpackage.MANIFEST_MEMBER, atomicio.dumps_json(manifest))
            for path, group in refs.items():
                loaded = documentassets.load_source(storage_root, group[0])
                for ref in group[1:]:
                    documentassets.load_source(storage_root, ref)
                archive.writestr(path, loaded.input_svg)
            for item in extras:
                if isinstance(item.source, bytes):
                    archive.writestr(item.record["path"], item.source)
                else:
                    archive.write(item.source, item.record["path"])
        with sealed.open("rb") as stream, inspect_chart_package(stream, budget=budget) as checked:
            result = {"manifest": checked.manifest, "chart_sources": len(refs)}
        publish = atomicio.publish_file if overwrite else atomicio.publish_file_if_absent
        publish(sealed, destination)
        return result
    finally:
        sealed.unlink(missing_ok=True)


def install_chart_sources(
    prepared: PreparedChartPackage,
    storage_root: Path,
    *,
    verify: Callable[[dict, Path], None],
) -> dict:
    """调用方全量重放通过后仅发布不可变 INPUT；正文和其他资源不在此提交。"""
    if not callable(verify):
        raise PackageError("verification_required", "A replay verification callback is required")
    prepared._verify()
    verified_document = prepared.document
    verify(verified_document, prepared.storage_root)
    if atomicio.dumps_json(verified_document) != prepared.document_bytes:
        raise PackageError("verification_changed_document", "Verification changed the document")
    prepared._verify()
    installed = []
    for group in _chart_refs(prepared.document).values():
        for ref in group:
            source = documentassets.load_source(prepared.storage_root, ref)
            actual = documentassets.freeze_source(
                storage_root, ref["sourceId"], source.input_svg, scene_digest=ref["sceneDigest"]
            )
            if actual != ref:
                raise PackageError("chart_source_mismatch", "Installed reference changed")
            installed.append(actual)
    return {
        "document": prepared.document,
        "chart_sources": installed,
        "remaining_resources": [
            item.record for item in prepared.resources if item.record["kind"] != "chart_source"
        ],
    }
