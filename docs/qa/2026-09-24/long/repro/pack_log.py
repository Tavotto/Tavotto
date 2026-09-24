#!/usr/bin/env python3
"""把 scratch 里的原始日志收进仓库：超过 190 行就保留头 40 行 + 尾 150 行并注明截断；
可以追加若干「附录」文件（summary.json / 分析输出）原样拼在后面。

用法：python pack_log.py <输出 .log> <原始日志> [附录文件 ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

HEAD, TAIL = 40, 150


def pack(src: Path) -> str:
    lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= HEAD + TAIL:
        return "\n".join(lines)
    cut = len(lines) - HEAD - TAIL
    return "\n".join(
        [*lines[:HEAD], f"# …… 截断：中间 {cut} 行省略（原始共 {len(lines)} 行）……", *lines[-TAIL:]]
    )


def main() -> int:
    out, src, *extra = sys.argv[1:]
    parts = [pack(Path(src))]
    for e in extra:
        parts.append(f"\n# ===== 附录：{Path(e).name} =====\n" + pack(Path(e)))
    Path(out).write_text("\n".join(parts) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
