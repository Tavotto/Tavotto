"""SourceResolver —— 把画布上的面板变成**冻结的**源产物（统一实施包 U06，ADR 0059）。

04_ARCHITECTURE §1 那一行：*SourceResolver（只冻结本次源图产物）*。它回答「这个面板的
字节是哪一份」，回答一次之后那份字节的身份（`SourceArtifact.bytes_sha256`）就进了
RenderPlan；写入器读字节之前再核一次 hash（`read_frozen()`），核不上就是
`source_changed`——渲染中途源文件被改写时，输出的后半段**绝不**读到新字节（RC-014）。

## 两种来源、一条硬边界

* `static`：磁盘上本来就有的原件，且这个面板**没有 override**——不跑脚本（RC-019：
  PDF-only 原图无 override 不强制重跑科学脚本），`StaticSourceResolver` 只读文件算 hash。
* `execution`：带 override 的面板、runtime 素材——必须由当次权威 worker 现画并附回执
  （`figcapture.SourceArtifact(origin="execution", receipt_id=…, receipt_identity=…)`）。
  `StaticSourceResolver` 遇到它抛 `source_needs_execution`，不去起子进程、不拿
  materialized cache 的旧文件冒充（RC-017）。**执行侧**是 `ExecutionSourceResolver`（U08，
  ADR 0067）：它自己不认识 worker，只拿一个 `execute(obj) -> FrozenSource` 回调——app 层把
  `_serialize_figure`（「谁来渲染」那扇门的唯一导出调用点）+ `receipt.from_worker`（回执）+
  `receipt.source_artifact_for`（`origin=execution` 的 SourceArtifact）接成这个回调；本模块
  仍是纯标准库，不 import pool / app。

**不复制**：解析器不把原始脚本、实验数据或整个项目目录搬进 staging；它只记路径与 hash，
中间产物由 `exportjob` 的私有临时目录承载（RC-015 / RC-016）。

纯标准库（`figcapture` 的 SourceArtifact 也是）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..engine import figcapture, runtimeasset
from .ir import FILE_KINDS

SOURCE_ERROR_CODES = (
    "source_missing",  # 面板指向的文件不存在
    "source_needs_execution",  # 带 override / runtime 素材：要 worker 现画，本解析器不做
    "source_changed",  # 冻结之后字节变了（hash 不符）
    "source_kind_unsupported",  # 扩展名不在 FILE_KINDS 里
    "source_outside_root",  # 路径逃出了项目根
)


class SourceError(RuntimeError):
    def __init__(self, code: str, message: str, params: dict | None = None) -> None:
        assert code in SOURCE_ERROR_CODES, code
        super().__init__(message)
        self.code = code
        self.params = params or {}


@dataclass(frozen=True)
class FrozenSource:
    """一份已冻结的源：语义身份 + 字节 hash（`artifact`）与冻结时的路径。路径只是「去哪读」，
    **不是身份**；读之前用 `read_frozen()` 核 hash。"""

    artifact: figcapture.SourceArtifact
    path: Path


class SourceResolver(Protocol):
    def resolve(self, obj: dict) -> FrozenSource: ...


#: `read_frozen()` 的分块大小；有界读的粒度，不是性能旋钮。
READ_CHUNK = 1 << 20


def read_frozen(fs: FrozenSource) -> bytes:
    """读冻结源的字节，并核对 `bytes_sha256`。不符 → `source_changed`（不返回新字节）。

    **有界分块读**（Codex #463 第五轮 P2）：冻结时记下了 `size_bytes`，磁盘上那份此刻若比它大，一定不是
    冻结的那份——多读一个字节就停、报 `source_changed`，不把一个几 GB 的替身整个读进内存再发现 hash 不对；
    hash 边读边算，读到的字节就是核过的那份（写入器接下来用的正是它）。缓冲只有一份（预分配 + `readinto`），
    不再 chunk 列表 + join 双份。
    """
    limit = int(fs.artifact.size_bytes)
    digest = hashlib.sha256()
    # 预分配 limit + 1 字节、`readinto` 直接填：整个过程只有一份缓冲（外加最后 `bytes()` 那一次拷贝——
    # 作业级预算 `job.SOURCE_BYTES_BUDGET` 把这份瞬时拷贝也算进去，Codex #463 第六轮 P2）
    buf = bytearray(limit + 1)
    view = memoryview(buf)
    total = 0
    try:
        with open(fs.path, "rb") as fh:
            while total < limit + 1:
                n = fh.readinto(view[total : min(total + READ_CHUNK, limit + 1)])
                if not n:
                    break
                digest.update(view[total : total + n])
                total += n
                if total > limit:
                    raise SourceError(
                        "source_changed",
                        f"{fs.artifact.source_id} 的字节在冻结之后变了（磁盘上那份比冻结时的 {limit} 字节大）",
                        {"figure": fs.artifact.source_id, "frozen_bytes": limit},
                    )
    except OSError as exc:
        raise SourceError(
            "source_missing", f"{fs.path}: {exc}", {"figure": fs.artifact.source_id}
        ) from exc
    # 核的是**读进内存的这一份**（写入器接下来用的正是它），不是再读一遍文件——两次读之间
    # 文件仍可能被改写，核文件等于核了另一个时刻
    sha = digest.hexdigest()
    if sha != fs.artifact.bytes_sha256:
        raise SourceError(
            "source_changed",
            f"{fs.artifact.source_id} 的字节在冻结之后变了"
            f"（{fs.artifact.bytes_sha256[:12]} → {sha[:12]}）",
            {"figure": fs.artifact.source_id},
        )
    return bytes(view[:total])


def needs_execution(obj: dict) -> bool:
    """这个面板要不要 worker 现画：runtime 素材永远要；带 override 的要。"""
    rel_id = str(obj.get("id", ""))
    return runtimeasset.is_runtime_id(rel_id) or bool(obj.get("overrides"))


@dataclass(frozen=True)
class StaticSourceResolver:
    """只认「磁盘原件 + 无 override」的解析器。`root` 是项目根，面板 id 是相对它的 POSIX 路径。"""

    root: Path

    def resolve(self, obj: dict) -> FrozenSource:
        rel_id = str(obj.get("id", ""))
        if needs_execution(obj):
            raise SourceError(
                "source_needs_execution",
                f"{rel_id}：带 override 或 runtime 素材要由当次 worker 现画（U06 只冻结静态源）",
                {"figure": rel_id},
            )
        root = Path(self.root).resolve()
        path = (root / rel_id).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SourceError(
                "source_outside_root", f"{rel_id} 逃出了项目根", {"figure": rel_id}
            ) from exc
        if not path.is_file():
            raise SourceError("source_missing", f"{rel_id} 不存在", {"figure": rel_id})
        kind = path.suffix.lstrip(".").lower()
        if kind not in FILE_KINDS:
            raise SourceError(
                "source_kind_unsupported", f"{rel_id}：{kind!r} 不是可放置的源", {"figure": rel_id}
            )
        artifact = figcapture.source_artifact_from_file(
            path, source_id=rel_id, origin=figcapture.ORIGIN_STATIC
        )
        return FrozenSource(artifact=artifact, path=path)


@dataclass(frozen=True)
class ExecutionSourceResolver:
    """带 override / runtime 素材的面板交给 `execute`（当次权威 worker 现画 + 回执），其余交给
    `static`（磁盘原件、无 override，RC-019：不强制重跑脚本）。

    `execute(obj) -> FrozenSource` 由 app 层提供：它必须返回 **`origin=execution`** 的产物
    （带 `receipt_id` / `receipt_identity` / `patch_hash`）——这里核一次，回来的不是执行产物就是
    调用方接错了线，不放行（RC-017 must_fail：拿 materialized cache 冒充）。
    """

    static: StaticSourceResolver
    execute: Callable[[dict], FrozenSource]

    def resolve(self, obj: dict) -> FrozenSource:
        if not needs_execution(obj):
            return self.static.resolve(obj)
        fs = self.execute(obj)
        if fs.artifact.origin != figcapture.ORIGIN_EXECUTION:
            raise SourceError(
                "source_needs_execution",
                f"{obj.get('id', '')}：execute 回调交回的不是执行产物（origin={fs.artifact.origin}）",
                {"figure": str(obj.get("id", ""))},
            )
        return fs
