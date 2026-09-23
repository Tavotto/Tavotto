"""有界、确定性的并行 Deflate（ADR 0077）——PNG 的 IDAT、TIFF 的条带、Canonical PDF 的大流共用这一份。

zlib 的压缩在 C 里放 GIL，线程就能占满多核；难点只在两件事上，这里各给一条规则：

* **确定性**：切块只由输入决定（字节数或行数），与线程数、调度无关——线程池 1 个线程与 8 个线程给出同一份
  输出。`zlib_stream` 的每块以前一块末尾 32 KiB 为预置字典（跨块回溯引用照样合法：解码器的窗口里本来就是
  前一块的明文），非末块 `Z_SYNC_FLUSH` 收在字节边界上，拼成**一条**合法 zlib 流；一块以内逐字节等于
  `zlib.compress`。`compress_each` 的每一件各自独立压缩（TIFF 条带），逐字节等于逐个 `zlib.compress`。
* **有界内存**：输入是可迭代的块，在飞的块数封顶（`inflight`），整幅像素从不在这里被再复制一份；线程池
  每次调用临时起、用完 join——不留常驻线程（`tests/conftest.py` 会话结束时点名活着的非 daemon 线程）。

纯标准库；不 import 任何产品模块（Flask 侧的 `tiffwrite` 与 RenderCore 的 `raster` 都用它）。
"""

from __future__ import annotations

import os
import struct
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: 一块的未压缩字节数（按字节切的输入用它；按行切的调用方自己按行数凑到约这么大）。
BLOCK = 1 << 20
#: deflate 的窗口：每块带前一块末尾这么多字节当预置字典。
_WINDOW = 32 * 1024


def workers() -> int:
    """线程数：CPU 数，封顶 8（再多 zlib 也吃不满内存带宽）。"""
    return max(1, min(8, os.cpu_count() or 1))


def ordered_map(
    fn: Callable[[T], R], items: Iterable[T], *, inflight: int | None = None
) -> Iterator[R]:
    """线程池里跑 `fn`，**按输入顺序**产出结果；任何时刻最多 `inflight` 件在飞（默认 2 × 线程数）——
    输入是惰性生成的大块时，内存只和窗口一样大。"""
    n = workers()
    limit = max(1, inflight or 2 * n)
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="deflate") as pool:
        pending: deque = deque()
        for item in items:
            pending.append(pool.submit(fn, item))
            if len(pending) >= limit:
                yield pending.popleft().result()
        while pending:
            yield pending.popleft().result()


def compress_each(chunks: Iterable[bytes], level: int) -> Iterator[bytes]:
    """每件独立 `zlib.compress`（TIFF 条带）：逐字节等于串行逐个压，只是并行。"""
    return ordered_map(lambda c: zlib.compress(c, level), chunks)


def _header(level: int) -> bytes:
    flevel = 0 if level < 2 else 1 if level < 6 else 2 if level == 6 else 3
    flg = flevel << 6
    flg += (31 - (0x78 * 256 + flg) % 31) % 31
    return bytes((0x78, flg))


def _deflate(task: tuple[bytes, bytes | None, bool, int]) -> bytes:
    data, zdict, last, level = task
    c = (
        zlib.compressobj(level, zlib.DEFLATED, -15, zdict=zdict)
        if zdict
        else zlib.compressobj(level, zlib.DEFLATED, -15)
    )
    return c.compress(data) + c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


def zlib_stream(blocks: Iterable[bytes], level: int = 6) -> bytes:
    """一串明文块 → **一条** zlib 流（任何解码器解出的就是这些块首尾相接）。块边界由调用方决定、必须确定；
    空块跳过。只有一块时逐字节等于 `zlib.compress(块, level)`。"""
    adler = 1

    def tasks() -> Iterator[tuple[bytes, bytes | None, bool, int]]:
        nonlocal adler
        prev_tail: bytes | None = None
        held: bytes | None = None
        for block in blocks:
            block = bytes(block)
            if not block:
                continue
            if held is not None:
                yield (held, prev_tail, False, level)
                prev_tail = held[-_WINDOW:]
            adler = zlib.adler32(block, adler)
            held = block
        yield (held if held is not None else b"", prev_tail, True, level)

    body = b"".join(ordered_map(_deflate, tasks()))
    return _header(level) + body + struct.pack(">I", adler & 0xFFFFFFFF)


def zlib_compress(data, level: int = 6) -> bytes:
    """`zlib.compress` 的并行版：按 `BLOCK` 字节切块走 `zlib_stream`；一块以内直接 `zlib.compress`（同一份字节）。"""
    view = memoryview(data).cast("B")
    if len(view) <= BLOCK:
        return zlib.compress(view, level)
    return zlib_stream((view[s : s + BLOCK] for s in range(0, len(view), BLOCK)), level)
