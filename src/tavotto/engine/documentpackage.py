"""v4 包的有界 ZIP 读取与原子封口；不安装文件、不计算、不管理会话。

目录/字节预算和读哪些资源由调用方给定。模型、来源引用与实际重放分别仍由
documents/documentassets/共享计算入口负责。write_package 也供旧包 writer 使用，
只封口与发布，不改动调用方已序列化的正文/清单字节。
"""

from __future__ import annotations

import io
import json
import ntpath
import os
import stat
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from . import atomicio, brand, documentformat, documents

LAYOUT_MEMBER = "layout.json"
MANIFEST_MEMBER = "package_manifest.json"
PACKAGE_VERSION = 1
ARCHIVE_CHUNK_BYTES = 64 * 1024
_METADATA = frozenset({LAYOUT_MEMBER, MANIFEST_MEMBER})
_is_reserved = getattr(ntpath, "isreserved", lambda name: PureWindowsPath(name).is_reserved())
_MANIFEST_REQUIRED = frozenset({"kind", "version", "document_format"})
_MANIFEST_OPTIONAL = frozenset(
    {
        "created_at",
        "figures_dir",
        "page",
        "export_settings",
        "assets",
        "missing_at_pack_time",
        "scripts",
        "runtime_assets",
        "figure_snapshots",
        "svg_snapshot_warnings",
        "svg_snapshot_warning",
    }
)


class PackageError(ValueError):
    """包结构/预算不合法；既有 package_invalid 错误码，reason 是诊断而非权限。"""

    code = "package_invalid"

    def __init__(self, reason: str, detail: str = "Invalid project package") -> None:
        self.reason, self.detail = reason, detail
        self.params = {"reason": reason}
        super().__init__(detail)


@dataclass(frozen=True)
class PackageBudget:
    """所有数值必传；不在 ZIP 消费者复制文档、SVG 或项目体量的政策数字。"""

    max_archive_bytes: int
    max_members: int
    max_directory_bytes: int
    max_total_bytes: int
    max_layout_bytes: int
    max_manifest_bytes: int

    def __post_init__(self) -> None:
        for item in fields(self):
            _positive(getattr(self, item.name))


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise PackageError("invalid_budget", "Package budgets must be positive integers")
    return value


def _member_key(name: object, *, directory: bool = False, legacy_names: bool = False) -> str:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or (not legacy_names and "\\" in name)
    ):
        raise PackageError("unsafe_member", "Invalid ZIP member path")
    if legacy_names:
        # Same actual member spelling as the stdlib writer; never normalize IDs
        # or grant an extraction path. The caller supplies its approved plan.
        name = zipfile.ZipInfo(name).filename
    path = name[:-1] if directory and name.endswith("/") else name
    parts = path.split("/")
    if any(
        not part
        or part in {".", ".."}
        or (
            not legacy_names
            and (
                part.endswith((".", " "))
                or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)
                or _is_reserved(part)
            )
        )
        for part in parts
    ):
        raise PackageError("unsafe_member", "Unsafe or nonportable ZIP member path")
    if (directory and parts[0] not in {"assets", "scripts"}) or (
        not directory
        and name not in _METADATA
        and (parts[0] not in {"assets", "scripts"} or len(parts) < 2)
    ):
        raise PackageError("unknown_member", "Unknown package member namespace")
    return path if legacy_names else unicodedata.normalize("NFC", path).casefold()


class _BoundedArchive:
    """ZipFile 仍是唯一 ZIP 解析器；先限制它一次读取目录的内存分配。

    ZipFile 构造时一次读取 central directory；仅在 infolist 之后数成员会太晚。
    资源解压始终按小块读取，因此同一上限也可用于底层读取，无需另写 EOCD 解析器。
    """

    def __init__(self, source: BinaryIO, size: int, max_read: int) -> None:
        self.source, self.size, self.max_read = source, size, max_read

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.source.seek(offset, whence)

    def tell(self) -> int:
        return self.source.tell()

    def seekable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = max(0, self.size - self.tell())
        if size > self.max_read:
            raise PackageError("directory_budget", "ZIP directory read exceeds its budget")
        data = self.source.read(size)
        if not isinstance(data, bytes) or len(data) > size:
            raise PackageError("invalid_archive_stream", "Invalid package input stream")
        return data


class _Archive:
    def __init__(
        self, source: BinaryIO, budget: PackageBudget, *, legacy_names: bool = False
    ) -> None:
        try:
            initial = source.tell()
            size = source.seek(0, os.SEEK_END)
            source.seek(initial)
        except (OSError, ValueError, AttributeError) as exc:
            raise PackageError("invalid_archive_stream", "Package input must be seekable") from exc
        if not isinstance(size, int) or not 0 < size <= budget.max_archive_bytes:
            raise PackageError("archive_budget", "Package archive byte budget exceeded")
        self.budget, self.expanded, self.legacy_names = budget, 0, legacy_names
        self.archive = zipfile.ZipFile(_BoundedArchive(source, size, budget.max_directory_bytes))
        try:
            entries = self.archive.infolist()
            if len(entries) > budget.max_members:
                raise PackageError("member_budget", "Package member count budget exceeded")
            self.members: dict[str, zipfile.ZipInfo] = {}
            keys, paths = set(), {}
            total = 0
            for entry in entries:
                directory = entry.is_dir()
                if entry.filename != entry.orig_filename:
                    raise PackageError("unsafe_member", "ZIP member name was truncated")
                key = _member_key(entry.filename, directory=directory, legacy_names=legacy_names)
                if key in keys:
                    raise PackageError("duplicate_member", "Duplicate or aliased ZIP member")
                keys.add(key)
                paths[key] = directory
                mode = entry.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFDIR if directory else stat.S_IFREG) or (
                    entry.external_attr & 0x10 and not directory
                ):
                    raise PackageError(
                        "special_member", "ZIP links and special files are forbidden"
                    )
                if entry.flag_bits & 1 or entry.compress_type not in (
                    zipfile.ZIP_STORED,
                    zipfile.ZIP_DEFLATED,
                ):
                    raise PackageError("unsupported_member", "Unsupported ZIP encoding")
                if directory:
                    if entry.file_size:
                        raise PackageError("special_member", "ZIP directory has file contents")
                    continue
                total += entry.file_size
                if total > budget.max_total_bytes:
                    raise PackageError("expanded_budget", "Package expanded byte budget exceeded")
                self.members[entry.filename] = entry
            for key in paths:
                parts = key.split("/")
                if any(paths.get("/".join(parts[:i])) is False for i in range(1, len(parts))):
                    raise PackageError("ambiguous_member", "ZIP file/directory collision")
            if not _METADATA <= self.members.keys():
                raise PackageError("missing_metadata", "Package metadata members are missing")
        except BaseException:
            self.archive.close()
            raise

    def close(self) -> None:
        self.archive.close()

    def read(self, name: str, limit: int, *, collect: bool = True) -> bytes:
        _member_key(name, legacy_names=self.legacy_names)
        _positive(limit)
        entry = self.members.get(name)
        if entry is None:
            raise PackageError("missing_member", f"Missing package member: {name}")
        if entry.file_size > limit:
            raise PackageError("member_byte_budget", f"Package member byte budget exceeded: {name}")
        count, chunks = 0, []
        try:
            with self.archive.open(entry) as stream:
                while data := stream.read(min(ARCHIVE_CHUNK_BYTES, limit - count + 1)):
                    count += len(data)
                    self.expanded += len(data)
                    if count > limit or self.expanded > self.budget.max_total_bytes:
                        raise PackageError(
                            "expanded_budget", "Actual ZIP expansion exceeds its budget"
                        )
                    if collect:
                        chunks.append(data)
        except (OSError, RuntimeError, ValueError, EOFError, zipfile.BadZipFile) as exc:
            if isinstance(exc, PackageError):
                raise
            raise PackageError("invalid_member", f"Invalid package member: {name}") from exc
        if count != entry.file_size:
            raise PackageError("invalid_member", "ZIP member size mismatch")
        return b"".join(chunks)


def _json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PackageError("duplicate_json_key", "Duplicate package JSON object key")
            result[key] = value
        return result

    try:
        documents.loads_document(raw)  # 非有限数的已有唯一读侧闸。
        parsed = json.loads(raw, object_pairs_hook=unique)
        # JSON 的 1e400 不走 parse_constant；沿用现有有限数写侧权威核对解析值。
        atomicio.dumps_json(parsed)
        return parsed
    except (ValueError, UnicodeError, RecursionError, atomicio.AtomicWriteError) as exc:
        if isinstance(exc, PackageError):
            raise
        raise PackageError("invalid_json", "Invalid package JSON") from exc


def _manifest(raw: object) -> dict:
    if (
        not isinstance(raw, dict)
        or not _MANIFEST_REQUIRED <= raw.keys()
        or not raw.keys() <= (_MANIFEST_REQUIRED | _MANIFEST_OPTIONAL)
    ):
        raise PackageError("invalid_manifest", "Invalid package manifest fields")
    if (
        raw["kind"] != brand.PACKAGE_KIND
        or type(raw["version"]) is not int
        or raw["version"] != PACKAGE_VERSION
        or raw["document_format"] != documentformat.CHART4
    ):
        raise PackageError("unsupported_envelope", "Unsupported package envelope")
    for name, expected in (
        ("created_at", str),
        ("figures_dir", str),
        ("export_settings", dict),
        ("assets", list),
        ("missing_at_pack_time", list),
        ("scripts", list),
        ("runtime_assets", list),
        ("svg_snapshot_warnings", list),
        ("svg_snapshot_warning", bool),
    ):
        if name in raw and not isinstance(raw[name], expected):
            raise PackageError("invalid_manifest", f"Invalid manifest field: {name}")
    for name, expected in (
        ("assets", dict),
        ("runtime_assets", dict),
        ("scripts", str),
        ("missing_at_pack_time", str),
        ("svg_snapshot_warnings", str),
    ):
        if any(not isinstance(item, expected) for item in raw.get(name, [])):
            raise PackageError("invalid_manifest", f"Invalid manifest list: {name}")
    if raw.get("page") is not None and not isinstance(raw["page"], dict):
        raise PackageError("invalid_manifest", "Invalid manifest page")
    if "figure_snapshots" in raw and (
        type(raw["figure_snapshots"]) is not int or raw["figure_snapshots"] < 0
    ):
        raise PackageError("invalid_manifest", "Invalid Figure snapshot count")
    return raw


class DocumentPackage:
    """仅供 open_package 上下文使用；不持有/安装任何目标项目文件。"""

    def __init__(self, archive: _Archive) -> None:
        self._archive = archive
        budget = archive.budget
        self.manifest = _manifest(_json(archive.read(MANIFEST_MEMBER, budget.max_manifest_bytes)))
        raw = _json(archive.read(LAYOUT_MEMBER, budget.max_layout_bytes))
        try:
            self.document = documents.validate_chart_document(raw)
        except documents.DocumentError as exc:
            raise PackageError(exc.code, exc.message) from exc
        self.member_names = tuple(archive.members)

    def read_required(
        self, needed: Mapping[str, int], *, exact: bool = True
    ) -> Iterator[tuple[str, bytes]]:
        """只读取 caller 指定资源；先核所有名字/预算，再逐成员返回，不提取文件。"""
        if not isinstance(needed, Mapping):
            raise PackageError("invalid_required_members", "Required members must be a mapping")
        members = tuple(needed.items())
        for name, limit in members:
            _member_key(name)
            _positive(limit)
            if name in _METADATA or name not in self._archive.members:
                raise PackageError("missing_member", "Invalid required resource member")
        if exact and set(self._archive.members) != _METADATA | needed.keys():
            raise PackageError("unexpected_member", "Package contains unrequested resource members")
        for name, limit in members:
            yield name, self._archive.read(name, limit)


@contextmanager
def open_package(source: BinaryIO | bytes, *, budget: PackageBudget) -> Iterator[DocumentPackage]:
    """读取上传流/不可变字节；不拥有 caller 的流，不创建目录或写入项目。"""
    owned = io.BytesIO(source) if isinstance(source, bytes) else None
    archive = None
    try:
        try:
            archive = _Archive(owned if owned is not None else source, budget)
            package = DocumentPackage(archive)
        except (OSError, RuntimeError, ValueError, EOFError, zipfile.BadZipFile) as exc:
            if isinstance(exc, PackageError):
                raise
            raise PackageError("invalid_archive", "Invalid project package ZIP") from exc
        yield package
    finally:
        if archive is not None:
            archive.close()
        if owned is not None:
            owned.close()


def _copy_member(archive, name, source, limit, remaining):
    total = 0
    if isinstance(source, bytes):
        stream = io.BytesIO(source)
    else:
        before = Path(source).lstat()
        if not stat.S_ISREG(before.st_mode):
            raise PackageError("unsafe_source", "Package source is not a regular file")
        fd = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
        stream = os.fdopen(fd, "rb")
    try:
        if not isinstance(source, bytes):
            if not os.path.samestat(before, os.fstat(stream.fileno())):
                raise PackageError("unsafe_source", "Package source is not a regular file")
        info = zipfile.ZipInfo(name)
        info.file_size = len(source) if isinstance(source, bytes) else before.st_size
        info.compress_type = archive.compression
        # Python 3.10 的 ZipInfo 仅有此字段；与 ZipFile.open(str) 的原值相同。
        info._compresslevel = archive.compresslevel
        with archive.open(info, "w") as output:
            while data := stream.read(min(ARCHIVE_CHUNK_BYTES, min(limit, remaining) - total + 1)):
                total += len(data)
                if total > limit or total > remaining:
                    raise PackageError("expanded_budget", "Package output byte budget exceeded")
                output.write(data)
        if not isinstance(source, bytes):
            after = Path(source).lstat()
            if (
                not os.path.samestat(before, after)
                or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or total != before.st_size
            ):
                raise PackageError("source_changed", "Package source changed while reading")
        return total
    finally:
        stream.close()


class _BoundedOutput:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def tell(self):
        return self.stream.tell()

    def seek(self, *args):
        return self.stream.seek(*args)

    def flush(self):
        return self.stream.flush()

    def write(self, data):
        if self.stream.tell() + len(data) > self.limit:
            raise PackageError("archive_budget", "Package output archive byte budget exceeded")
        return self.stream.write(data)


class _OutputNames:
    def __init__(self, maximum: int, *, legacy_names: bool = False) -> None:
        self.maximum, self.legacy_names = maximum, legacy_names
        self.files: set[str] = set()
        self.directories: set[str] = set()

    def add(self, name: str) -> None:
        key = _member_key(name, legacy_names=self.legacy_names)
        if key in self.files:
            raise PackageError("duplicate_member", "Duplicate or aliased package member")
        parents = {key.rsplit("/", i)[0] for i in range(1, key.count("/") + 1)}
        if key in self.directories or parents & self.files:
            raise PackageError("ambiguous_member", "Package file/directory collision")
        if len(self.files) >= self.maximum:
            raise PackageError("member_budget", "Package member count budget exceeded")
        self.files.add(key)
        self.directories.update(parents)


class _StagedPackage:
    """只提供旧 helper 需要的两种添加操作；任何失败均阻止整个 ZIP 发布。"""

    def __init__(
        self, archive: zipfile.ZipFile, budget: PackageBudget, *, legacy_names: bool = False
    ) -> None:
        self.archive, self.budget = archive, budget
        self.names = _OutputNames(budget.max_members, legacy_names=legacy_names)
        self.total = 0
        self.closed = False
        self.failure: BaseException | None = None

    def _ready(self) -> None:
        if self.closed:
            raise PackageError("closed_output", "Package staging context is closed")
        if self.failure is not None:
            raise self.failure

    def _add(self, name: str, source: bytes | Path) -> None:
        self.names.add(name)
        limit = (
            self.budget.max_layout_bytes
            if name == LAYOUT_MEMBER
            else self.budget.max_manifest_bytes
            if name == MANIFEST_MEMBER
            else self.budget.max_total_bytes
        )
        self.total += _copy_member(
            self.archive, name, source, limit, self.budget.max_total_bytes - self.total
        )

    def write(self, filename: str | Path, arcname: str) -> None:
        """在调用当下读取已授权普通文件，允许 caller 随后删除自己的暂存目录。"""
        self._ready()
        try:
            if not isinstance(filename, (str, Path)):
                raise PackageError("unsafe_source", "Package source must be an approved path")
            self._add(arcname, Path(filename))
        except BaseException as exc:
            self.failure = exc
            raise

    def writestr(self, name: str, data: str | bytes) -> None:
        """沿用 ZipFile.writestr 的 UTF-8 字符串语义；字节输入原样写入。"""
        self._ready()
        try:
            if not isinstance(data, (str, bytes)):
                raise PackageError("unsafe_source", "Package string source must be text or bytes")
            if isinstance(data, str):
                # UTF-8 bytes are never fewer than code points: reject oversized text before copying.
                if len(data) > self.budget.max_total_bytes - self.total:
                    raise PackageError("expanded_budget", "Package output byte budget exceeded")
                data = data.encode("utf-8")
            self._add(name, data)
        except BaseException as exc:
            self.failure = exc
            raise


def _publish_package(
    temporary: Path,
    destination: Path,
    budget: PackageBudget,
    overwrite: bool,
    *,
    legacy_names: bool = False,
):
    with temporary.open("rb") as stream:
        checked = _Archive(stream, budget, legacy_names=legacy_names)
        try:
            for name in checked.members:
                checked.read(name, budget.max_total_bytes, collect=False)
        finally:
            checked.close()
    if overwrite:
        atomicio.publish_file(temporary, destination)
    else:
        atomicio.publish_file_if_absent(temporary, destination)


@contextmanager
def staged_package(
    destination: Path, *, budget: PackageBudget, overwrite: bool = True, legacy_names: bool = False
) -> Iterator[_StagedPackage]:
    """增量写暂存 ZIP；封口/逐项 CRC 后经 atomicio 一次发布。

    write/writestr 在调用时完成有界读取，不保留 caller 临时资源的路径。
    添加失败即使被 helper 捕获仍拒发布；不安装成员，不提供任意 ZIP 操作。
    destination 与输入文件的授权/containment 仍由 caller 负责。
    legacy_names 仅供旧只读包的显式 writer 使用；公开 v4 reader 始终 portable。
    """
    if type(overwrite) is not bool or type(legacy_names) is not bool:
        raise PackageError("invalid_output", "Invalid package output request")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(name)
    try:
        with (
            temporary.open("wb") as stream,
            zipfile.ZipFile(
                _BoundedOutput(stream, budget.max_archive_bytes), "w", zipfile.ZIP_DEFLATED
            ) as archive,
        ):
            writer = _StagedPackage(archive, budget, legacy_names=legacy_names)
            try:
                yield writer
                writer._ready()
            finally:
                writer.closed = True
        _publish_package(temporary, destination, budget, overwrite, legacy_names=legacy_names)
    finally:
        temporary.unlink(missing_ok=True)


def write_package(
    destination: Path,
    members: Mapping[str, bytes | Path],
    *,
    budget: PackageBudget,
    overwrite: bool = True,
) -> None:
    """固定 mapping 的薄适配；与增量 helper 共用唯一封口、校验和发布流程。"""
    if not isinstance(members, Mapping) or type(overwrite) is not bool:
        raise PackageError("invalid_output", "Invalid package output request")
    if len(members) > budget.max_members or not _METADATA <= members.keys():
        raise PackageError("member_budget", "Invalid package output members")
    names = _OutputNames(budget.max_members)
    for name, source in members.items():
        names.add(name)
        if not isinstance(source, (bytes, Path)):
            raise PackageError("unsafe_source", "Package sources must be bytes or approved paths")
    with staged_package(destination, budget=budget, overwrite=overwrite) as archive:
        for name, source in members.items():
            if isinstance(source, bytes):
                archive.writestr(name, source)
            else:
                archive.write(source, name)
