"""EPS 文件头的只读解析 —— 导出回执里「这张 EPS 多大」的唯一出处（ADR 0046）。

EPS 由 worker 侧的 matplotlib 直接序列化（父进程没有 matplotlib，也没有任何
PostScript 写入器），父进程拿到文件之后只需要两件事实：它**是不是** EPS、
**多大**。两件都写在 DSC 注释里（`%!PS-Adobe-3.0 EPSF-3.0`、`%%BoundingBox`、
`%%HiResBoundingBox`），纯文本、纯标准库就读得出，不需要 Ghostscript。

纯标准库；Flask 父进程与测试都 import。
"""

from __future__ import annotations

import re
from pathlib import Path

#: EPS / PostScript 文件的签名。matplotlib 写 `%!PS-Adobe-3.0 EPSF-3.0`。
EPS_MAGIC = b"%!PS-Adobe"

#: DSC 头与尾各读多少字节。`%%BoundingBox: (atend)` 是合法写法——那时真值在
#: 文件末尾的 trailer 里，所以两头都看。
_PROBE_BYTES = 8192

_BBOX = re.compile(
    rb"^%%(HiRes)?BoundingBox:\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)",
    re.MULTILINE,
)


def is_eps(path: Path | str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(len(EPS_MAGIC)) == EPS_MAGIC
    except OSError:
        return False


def bounding_box_pt(path: Path | str) -> tuple[float, float] | None:
    """`%%HiResBoundingBox`（优先）或 `%%BoundingBox` 给出的宽高（pt）。

    读不到、不是 EPS、或框是退化的（宽或高 ≤ 0）一律回 `None`——回执里这两个
    维度于是缺席，而不是编一个数（ADR 0028 的同一条纪律）。
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(_PROBE_BYTES)
            fh.seek(0, 2)
            size = fh.tell()
            tail = b""
            if size > _PROBE_BYTES:
                fh.seek(max(_PROBE_BYTES, size - _PROBE_BYTES))
                tail = fh.read(_PROBE_BYTES)
    except OSError:
        return None
    if not head.startswith(EPS_MAGIC):
        return None
    best: tuple[float, float] | None = None
    for blob in (head, tail):
        for m in _BBOX.finditer(blob):
            try:
                x1, y1, x2, y2 = (float(v) for v in m.groups()[1:])
            except ValueError:
                continue
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0:
                continue
            if m.group(1):  # HiRes 优先，找到就定
                return (w, h)
            if best is None:
                best = (w, h)
    return best
