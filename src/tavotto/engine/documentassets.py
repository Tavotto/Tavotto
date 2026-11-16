"""项目文档引用的不可变 SVG 输入附件（纯标准库，不含计算或执行授权）。

调用方传入 ``project_layout_dir()`` 的权威项目收纳目录，并持有现有文档锁。
附件先落盘，正文仍由调用方经 atomicio 单次提交；这里没有索引、锁、GC 或
第二个提交点。自包含输入保留整份精确字节，原场景身份沿用 chartbundle/svgscene。
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from tavotto import chartbundle, svgscene
from tavotto.rendercore.svgcheck import SVGBudget

from . import atomicio, documents

CHART_SOURCES_DIRNAME = "chart_sources"
MAX_SOURCE_BYTES = SVGBudget().max_bytes


class DocumentAssetError(ValueError):
    """来源不可用或身份不符；稳定错误码供现有接口转换。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ChartDocumentSource:
    path: Path
    input_svg: bytes
    source_svg: bytes
    reference: dict


def _invalid(detail: str) -> None:
    raise DocumentAssetError("chart_source_mismatch", detail)


def _reference(reference, *, source_id=None, scene_digest=None) -> dict:
    try:
        return documents.validate_chart_source(
            reference, source_id=source_id, scene_digest=scene_digest
        )
    except documents.DocumentError as exc:
        raise DocumentAssetError(exc.code, exc.message) from exc


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
        _invalid("unsafe chart source directory")


def _root(storage_root: Path, *, create: bool) -> Path:
    requested = Path(storage_root).expanduser().absolute()
    if ".." in requested.parts:
        _invalid("unsafe chart source storage root")
    # The caller owns the project parent. OS aliases above it may be resolved;
    # neither the supplied store nor any child owned here may be a symlink.
    root = requested.parent.resolve() / requested.name
    _directory(root, create=create)
    sources = root / CHART_SOURCES_DIRNAME
    _directory(sources, create=create)
    if not sources.resolve().is_relative_to(root.resolve()):
        _invalid("chart source directory escaped its storage root")
    return sources


def source_path(storage_root: Path, reference: dict) -> Path:
    """派生唯一摘要路径；不创建目录，也不以 sourceId 当文件路径。"""
    ref = _reference(reference)
    try:
        return _root(storage_root, create=False) / (ref["bytesSha256"] + ".svg")
    except OSError as exc:
        raise DocumentAssetError(
            "chart_source_mismatch", "chart source storage unavailable"
        ) from exc


def _read(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        _invalid("unsafe chart source file")
    if not 0 < before.st_size <= MAX_SOURCE_BYTES:
        raise DocumentAssetError("svg_resource_limit", "chart source byte budget exceeded")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            _invalid("chart source changed during open")
        data = stream.read(MAX_SOURCE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (
        len(data) != before.st_size
        or len(data) > MAX_SOURCE_BYTES
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or not os.path.samestat(before, path.lstat())
    ):
        _invalid("chart source changed during read")
    return data


def _source(input_svg: bytes) -> tuple[bytes, str]:
    if not isinstance(input_svg, bytes):
        raise DocumentAssetError("chart_model_invalid", "chart input must be immutable bytes")
    if not 0 < len(input_svg) <= MAX_SOURCE_BYTES:
        raise DocumentAssetError("svg_resource_limit", "chart source byte budget exceeded")
    try:
        bundle = chartbundle.extract_chart(input_svg)
        source = bundle.source_svg if bundle is not None else input_svg
        scene = svgscene.scene_inventory(source)
    except (chartbundle.ChartBundleError, svgscene.SceneError) as exc:
        raise DocumentAssetError(exc.code, exc.detail) from exc
    return source, scene["scene_digest"]


def load_source(
    storage_root: Path,
    reference: dict,
    *,
    source_id: str | None = None,
    scene_digest: str | None = None,
) -> ChartDocumentSource:
    """有界读取并核对 INPUT 字节与真实原场景；不回退同名原件或包内模型。"""
    ref = _reference(reference, source_id=source_id, scene_digest=scene_digest)
    path = source_path(storage_root, ref)
    try:
        data = _read(path)
    except OSError as exc:
        raise DocumentAssetError("chart_source_mismatch", "chart source unavailable") from exc
    if len(data) != ref["sizeBytes"] or hashlib.sha256(data).hexdigest() != ref["bytesSha256"]:
        _invalid("chart input bytes changed")
    source, actual_scene = _source(data)
    if actual_scene != ref["sceneDigest"]:
        _invalid("chart original scene changed")
    return ChartDocumentSource(path, data, source, ref)


def freeze_source(
    storage_root: Path, source_id: str, input_svg: bytes, *, scene_digest: str
) -> dict:
    """先验证来源再持久化不可变输入；返回将与正文一同提交的闭集引用。"""
    _source_svg, actual_scene = _source(input_svg)
    ref = _reference(
        {
            "schema": documents.CHART_SOURCE_SCHEMA,
            "sourceId": source_id,
            "bytesSha256": hashlib.sha256(input_svg).hexdigest(),
            "sizeBytes": len(input_svg),
            "sceneDigest": actual_scene,
        },
        source_id=source_id,
        scene_digest=scene_digest,
    )
    try:
        path = _root(storage_root, create=True) / (ref["bytesSha256"] + ".svg")
        atomicio.write_bytes_if_absent(path, input_svg)
    except FileExistsError:
        # An identical writer may have won; an already corrupt blob is never repaired
        # over the user's bytes. All references must continue to name exact input.
        pass
    load_source(storage_root, ref, source_id=source_id, scene_digest=scene_digest)
    return ref
