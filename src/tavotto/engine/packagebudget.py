"""从最终资源 plan 推导 ZIP 输出预算；没有第二套包容量或路径政策。

只适用于 documentpackage 固定的 ZIP_DEFLATED/default zlib 参数、无额外注释的
输出。caller 先持有所有临时资源，再给已规范化的实际成员名和精确字节大小；
资源选择、源 containment 和名字合法性仍由原 caller/容器负责。
"""

from __future__ import annotations

import struct
import zipfile
from collections.abc import Mapping
from dataclasses import fields

from .documentpackage import (
    ARCHIVE_CHUNK_BYTES,
    LAYOUT_MEMBER,
    MANIFEST_MEMBER,
    PackageBudget,
    PackageError,
)

# 输入是未信任的包，不能从包内声明反推其自身上限。完整项目的字节/文件
# 数沿现项目副本边界；JSON 元数据及 ZIP 目录另有限额，输出预算不受影响。
INPUT_METADATA_BYTES = 64 * 1024 * 1024
INPUT_DIRECTORY_BYTES = 64 * 1024 * 1024
INPUT_MULTIPART_BYTES = 1024 * 1024


def input_budget() -> PackageBudget:
    """显式 v4 检视/安装共用的有限输入政策；不改变旧包入口或输出容量。"""
    from . import projectcopy

    return PackageBudget(
        max_archive_bytes=projectcopy.MAX_BYTES,
        max_members=projectcopy.MAX_FILES + 2,
        max_directory_bytes=INPUT_DIRECTORY_BYTES,
        max_total_bytes=projectcopy.MAX_BYTES,
        max_layout_bytes=INPUT_METADATA_BYTES,
        max_manifest_bytes=INPUT_METADATA_BYTES,
    )


def output_budget(members: Mapping[str, int]) -> PackageBudget:
    """精确展开量 + 保守压缩/ZIP64 开销；不另设政策上限或调用 native API。

    zlib 默认窗口/内存参数的 compressBound（含 wrapper，故也包住 raw DEFLATE）：
    https://github.com/madler/zlib/blob/v1.3.1/compress.c#L65-L68
    使用 Python 整数，避免 Windows uLong 的 32 位溢出。
    """
    if not isinstance(members, Mapping) or not {LAYOUT_MEMBER, MANIFEST_MEMBER} <= members.keys():
        raise PackageError("missing_metadata", "Output plan must include both metadata members")
    total = compressed = local = central = largest_compressed = 0
    # Single-disk ZIP64: local sizes, central sizes+offset, widest data descriptor.
    local_extra = struct.calcsize("<HHQQ")
    central_extra = struct.calcsize("<HHQQQ")
    descriptor = struct.calcsize("<4sLQQ")
    for name, size in members.items():
        if not isinstance(name, str) or type(size) is not int or size < 0:
            raise PackageError("invalid_output", "Output member sizes must be nonnegative integers")
        try:
            name_bytes = len(name.encode("utf-8"))
        except UnicodeError as exc:
            raise PackageError("invalid_output", "Output member name is not UTF-8") from exc
        total += size
        bound = size + (size >> 12) + (size >> 14) + (size >> 25) + 13
        compressed += bound
        largest_compressed = max(largest_compressed, bound)
        local += zipfile.sizeFileHeader + name_bytes + local_extra + descriptor
        central += zipfile.sizeCentralDir + name_bytes + central_extra
    ending = zipfile.sizeEndCentDir + zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
    return PackageBudget(
        max_archive_bytes=local + compressed + central + ending,
        max_members=len(members),
        # The same underlying read guard also sees ZipExtFile's compressed
        # payload reads. Consume the reader's one chunk authority, not a new cap.
        max_directory_bytes=max(central + ending, min(ARCHIVE_CHUNK_BYTES, largest_compressed)),
        max_total_bytes=max(1, total),
        max_layout_bytes=max(1, members[LAYOUT_MEMBER]),
        max_manifest_bytes=max(1, members[MANIFEST_MEMBER]),
    )


def portable_output_budget(members: Mapping[str, int]) -> PackageBudget:
    """Explicit captured-input packages must also fit the existing input policy.

    Default/legacy output capacity is unchanged. Exact output bounds still come
    from output_budget; the portable variant intersects the independent input
    limits so the artifact can be inspected on another machine.
    """
    output, incoming = output_budget(members), input_budget()
    if (
        sum(members.values()) > incoming.max_total_bytes
        or len(members) > incoming.max_members
        or members[LAYOUT_MEMBER] > incoming.max_layout_bytes
        or members[MANIFEST_MEMBER] > incoming.max_manifest_bytes
    ):
        raise PackageError("output_budget", "Portable package exceeds the shared input policy")
    return PackageBudget(
        **{
            field.name: min(getattr(output, field.name), getattr(incoming, field.name))
            for field in fields(PackageBudget)
        }
    )
