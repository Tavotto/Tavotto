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
import os
import threading
import time
from collections import OrderedDict
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
    "source_unreadable",  # 文件在但不是可用的产物（0 字节，why=empty）——与写入器同一个码、同一套 why（#517）
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
    **不是身份**；读之前用 `read_frozen()` 核 hash。

    `receipt`（U09，ADR 0070）：执行侧源随附的回执**公开事实**（`receipt.public_facts()`：身份 / 完备性 /
    generation / source revision / 解释器版本 / 关键包 / 数据绑定核对 / 输入观察的完备性——没有路径），
    进 manifest 的 `receipts`；静态源没有回执，None。
    """

    artifact: figcapture.SourceArtifact
    path: Path
    receipt: dict | None = None


class SourceResolver(Protocol):
    def resolve(self, obj: dict) -> FrozenSource: ...


#: `read_frozen()` 的分块大小；有界读的粒度，不是性能旋钮。
READ_CHUNK = 1 << 20


#: 这个平台的 stat 能不能看见「文件被改写过」。POSIX 的 ctime 由内核在任何写入 / 改元数据时推进、用户工具设不
#: 回去；**Windows 的 `st_ctime` 是创建时间**（Python 3.12 起明说），NTFS 的 ChangeTime `os.stat` 不给——同长原地
#: 改写、再由工具把 mtime 设回原值，五元组一个字段都不变（Codex #506 P2）。看不见就不复用：那里指纹恒为 None，
#: `FingerprintMemo` 每次都重算，行为与没有这层复用时相同。
FINGERPRINT_TRUSTED = os.name != "nt"


def file_fingerprint(path: Path) -> tuple[int, int, int, int, int] | None:
    """「这个文件自上次看过之后动过没有」的判据：(设备, inode, 字节数, mtime_ns, ctime_ns)；stat 不了、或这个平台
    的元数据看不见改写（`FINGERPRINT_TRUSTED` 为假，Windows）时回 None——None 的意思是「别复用」。

    **只用来决定能不能复用一个派生值**（尺寸探测 / 预览缓存键），从不当身份：身份永远是字节 hash，
    冻结与渲染仍按 hash 核（`read_frozen` / `PreviewCache._stage`）。「换了内容又把 mtime 与大小都改回原样」
    躲不过 ctime；时间戳粒度内的等长改写由 `FingerprintMemo` 的「刚改过不记」挡住。"""
    if not FINGERPRINT_TRUSTED:
        return None
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


#: 「刚改过」的窗口：文件最后一次改动离记录时刻不到这么久，指纹不可信，不记。文件系统的时间戳是**粗粒度**的
#: （Linux 按内核 tick ~1–4 ms、Windows ~16 ms、FAT 2 s）——窗口之内两次等长改写可以得到同一个指纹（git 的
#: 「racy clean」）。代价只是刚写出的文件前 2 秒不走复用。
RACY_WINDOW_NS = 2_000_000_000


def _is_racy(fp: tuple) -> bool:
    return time.time_ns() - max(fp[3], fp[4]) < RACY_WINDOW_NS


class FingerprintMemo:
    """按 `file_fingerprint` 复用派生值的有界表（线程安全，满了丢最久没用的）。

    * `lookup(path, key=)` → `(此刻的指纹, 值或 None)`：指纹与表里那次相同才回值。
    * `store(path, before, value, key=)`：把值挂在 `before`（算值**之前**取的指纹）上；文件最后一次改动离现在不到
      `RACY_WINDOW_NS` 的不记——时间戳粒度还分辨不出下一次等长改写。算的过程中被改写的，指纹已经前进，挂在旧
      指纹上的这一条自然查不到，下一次重算。
    * `get_or_compute(path, compute, key=)`：上面两步的组合；`compute` 抛的异常原样上抛、什么都不记。

    值不能是 None（None 就是「表里没有」）。"""

    def __init__(self, maxsize: int = 4096) -> None:
        self.maxsize = int(maxsize)
        self._table: OrderedDict[tuple[str, object], tuple[tuple, object]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _slot(path: Path, key: object) -> tuple[str, object]:
        return (os.path.realpath(path), key)

    def lookup(self, path: Path, *, key: object = None) -> tuple[tuple | None, object]:
        now = file_fingerprint(path)
        if now is not None:
            slot = self._slot(path, key)
            with self._lock:
                entry = self._table.get(slot)
                if entry is not None and entry[0] == now:
                    self._table.move_to_end(slot)
                    self.hits += 1
                    return now, entry[1]
        with self._lock:
            self.misses += 1
        return now, None

    def store(self, path: Path, before: tuple | None, value: object, *, key: object = None) -> None:
        if value is None:
            raise ValueError("FingerprintMemo 的值不能是 None（None 表示表里没有）")
        slot = self._slot(path, key)
        with self._lock:
            # 算值的过程中文件若被改写，指纹已前进（ctime 只增不减），挂在 `before` 上的这一条再也不会被查到——
            # 不必另判「前后相同」；要挡的只有时间戳粒度分辨不出的那一种（刚改过不记）
            if before is not None and not _is_racy(before):
                self._table[slot] = (before, value)
                self._table.move_to_end(slot)
                while len(self._table) > self.maxsize:
                    self._table.popitem(last=False)
            else:
                self._table.pop(slot, None)

    def get_or_compute(self, path: Path, compute: Callable[[], object], *, key: object = None):
        before, value = self.lookup(path, key=key)
        if value is not None:
            return value
        value = compute()
        self.store(path, before, value, key=key)
        return value

    def clear(self) -> None:
        with self._lock:
            self._table.clear()


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


def static_artifact(path: Path, source_id: str) -> figcapture.SourceArtifact:
    """磁盘原件 → `origin=static` 的 SourceArtifact；两个静态解析器（这里的 `StaticSourceResolver` 与
    `facade._PathResolver`）共用这一处。0 字节的文件（写到一半被打断、同步工具的占位）不是产物：
    报结构化的 `source_unreadable`（`why=empty`，与写入器坏源同一套词），不让 `SourceArtifact` 构造时的
    `ValueError` 原文把整个作业打成 `export_failed`（#517）。"""
    try:
        empty = Path(path).stat().st_size == 0
    except OSError as exc:
        raise SourceError("source_missing", f"{source_id}: {exc}", {"figure": source_id}) from exc
    if empty:
        raise SourceError(
            "source_unreadable",
            f"{source_id}：文件是空的（0 字节）",
            {"figure": source_id, "why": "empty"},
        )
    return figcapture.source_artifact_from_file(
        path, source_id=source_id, origin=figcapture.ORIGIN_STATIC
    )


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
        return FrozenSource(artifact=static_artifact(path, rel_id), path=path)


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
