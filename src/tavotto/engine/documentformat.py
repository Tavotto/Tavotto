"""文档格式的显式目录适配；不改变项目资产根、文档模型或写入事务。

调用方传现有权威目录。legacy 原样派生旧路径，v4 的每个自有目录组件
拒绝符号链接/目录联接，包括仍指向同一项目内部的重定向。
"""

from __future__ import annotations

import stat
from pathlib import Path

from . import documents

LEGACY = "legacy"
CHART4 = "v4"
FORMATS = frozenset({LEGACY, CHART4})
_NAMED_PARTS = ("documents", CHART4)


def validate_format(value: object = LEGACY) -> str:
    if not isinstance(value, str) or value not in FORMATS:
        raise documents.DocumentError("invalid_document", "未知的文档存储格式")
    return value


def session_namespace(document_format: object = LEGACY) -> str | None:
    return None if validate_format(document_format) == LEGACY else CHART4


def validate_document(
    raw: object, document_format: object = LEGACY, *, require_sources: bool = True
) -> dict:
    if validate_format(document_format) == LEGACY:
        return documents.validate_document(raw)
    return documents.validate_chart_document(raw, require_sources=require_sources)


def _directory(path: Path, *, create: bool) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        if not create:
            return
        path.mkdir(parents=True, exist_ok=True)
        mode = path.lstat().st_mode
    if (
        not stat.S_ISDIR(mode)
        or path.is_symlink()
        or path.resolve() != path.parent.resolve() / path.name
    ):
        raise documents.DocumentError("layout_outside_project", "文档目录不能通过链接重定向")


def _v4_directory(base: Path, parts: tuple[str, ...], *, create: bool) -> Path:
    requested = Path(base).expanduser().absolute()
    if ".." in requested.parts:
        raise documents.DocumentError("layout_outside_project", "无效的文档目录")
    # 上级是调用方的权威目录；允许系统级路径别名，根本身及其下每层均核对。
    path = requested.parent.resolve() / requested.name
    _directory(path, create=create)
    for part in parts:
        path /= part
        _directory(path, create=create)
    return path


def named_dir(
    project_layout_dir: Path, document_format: object = LEGACY, *, create: bool = False
) -> Path:
    if validate_format(document_format) == LEGACY:
        return Path(project_layout_dir)
    return _v4_directory(project_layout_dir, _NAMED_PARTS, create=create)


def autosave_dir(
    layout_dir: Path, document_format: object = LEGACY, *, create: bool = False
) -> Path:
    if validate_format(document_format) == LEGACY:
        return Path(layout_dir) / documents.AUTOSAVE_DIRNAME
    return _v4_directory(layout_dir, (documents.AUTOSAVE_DIRNAME, CHART4), create=create)


def versions_dir(
    project_store: Path | None,
    legacy_versions_dir: Path,
    document_format: object = LEGACY,
    *,
    create: bool = False,
) -> Path:
    if validate_format(document_format) == LEGACY:
        return (
            Path(project_store) / "versions"
            if project_store is not None
            else Path(legacy_versions_dir)
        )
    base = Path(project_store) if project_store is not None else Path(legacy_versions_dir).parent
    return _v4_directory(base, (*_NAMED_PARTS, "versions"), create=create)
