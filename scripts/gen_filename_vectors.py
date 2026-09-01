#!/usr/bin/env python3
"""生成 / 校对 `tests/golden/filename_vectors.json`。

导出文件名的规则有**两个实现**（Python 的 `engine/exportreq.py` 给真正落盘的
那一侧，TypeScript 的 `web/src/lib/exportName.ts` 给输入时的就地提示）——
浏览器里跑不了 Python，所以第二份是必需的，不是重复。让它们不分叉的办法与
`preflight` ↔ `preflight.ts`、`patchspec` ↔ Rust supervisor 完全一样：
**同一份向量，两边各跑一遍**。

    python scripts/gen_filename_vectors.py            # 校对（有分歧就非零退出）
    python scripts/gen_filename_vectors.py --write    # 按 Python 侧重新生成

Python 是参考实现：`--write` 之后必须人工读一遍 diff，再让 vitest 也绿。

纯标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import exportreq  # noqa: E402

OUT = ROOT / "tests" / "golden" / "filename_vectors.json"


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8（同 `gen_preflight_vectors.py`）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


#: 覆盖每一条判据 + 每一条判据的**边界**。加规则就往这里加用例，
#: 「这条规则从没被向量碰过」= 两侧可以分叉而没人发现。
NAMES = [
    "Fig 1",
    "fig-1_final",
    "图 1 主图",
    "",
    " ",
    "  Fig 1",
    "Fig 1  ",
    "\tFig",
    "Fig\t",
    "\ufeffFig",
    "Fig\ufeff",
    "\u3000Fig",
    "Fig\x1c",
    "Fig\x1c1",
    "Fig\n1",
    "Fig\x00 1",
    "Fig\x7f1",
    "Fig<1",
    "Fig>1",
    "Fig:1",
    'Fig"1',
    "Fig/1",
    "Fig\\1",
    "Fig|1",
    "Fig?1",
    "Fig*1",
    "Fig.",
    "Fig..",
    ".",
    "..",
    "...",
    ".hidden",
    "CON",
    "con",
    "Con.pdf",
    "CONSOLE",
    "COM1",
    "COM10",
    "LPT9",
    "NUL.png",
    "aux",
    "x" * exportreq.FILENAME_MAX,
    "x" * (exportreq.FILENAME_MAX + 1),
]

#: 扩展名剥离：用户顺手打上的 `.pdf` 要被吃掉，而 `v1.2` 里的 `.2` 不许被当成
#: 扩展名。第二列是 formats。
STRIP = [
    ("Fig 1.pdf", ["pdf", "png"]),
    ("Fig 1.PDF", ["pdf"]),
    ("Fig 1.png", ["png"]),
    ("Fig 1.svg", ["pdf"]),
    ("Fig 1.pdf.pdf", ["pdf"]),
    ("v1.2", ["pdf", "png"]),
    ("data.tar.gz", ["png"]),
    ("archive.tiff", ["png"]),
    (".pdf", ["pdf"]),
    ("Fig 1", ["pdf"]),
]

#: 去重：`rename` 策略下已经被占用的名字往后编号。第二列是"已占用"集合。
DEDUPE = [
    ("Fig 1", "pdf", []),
    ("Fig 1", "pdf", ["Fig 1.pdf"]),
    ("Fig 1", "pdf", ["Fig 1.pdf", "Fig 1 (2).pdf"]),
    ("Fig 1", "png", ["Fig 1.pdf"]),
    ("图 1", "pdf", ["图 1.pdf", "图 1 (2).pdf", "图 1 (3).pdf"]),
]


def build() -> dict:
    return {
        "note": (
            "由 scripts/gen_filename_vectors.py 生成；"
            "engine/exportreq.py 与 web/src/lib/exportName.ts 两侧各跑一遍。"
        ),
        "filename_max": exportreq.FILENAME_MAX,
        "check": [{"name": n, "reason": exportreq.check_filename(n)} for n in NAMES],
        "strip": [
            {
                "name": n,
                "formats": f,
                "stripped": exportreq.strip_output_extension(n, tuple(f)),
            }
            for n, f in STRIP
        ],
        "output_name": [
            {"base": "Fig 1", "format": "pdf", "name": exportreq.output_name("Fig 1", "pdf")},
            {"base": "Fig 1", "format": "png", "name": exportreq.output_name("Fig 1", "png")},
        ],
        "dedupe": [
            {
                "base": b,
                "format": f,
                "taken": t,
                "name": exportreq.dedupe_name(b, f, lambda n, t=t: n in t),
            }
            for b, f, t in DEDUPE
        ],
    }


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="按 Python 侧重新生成向量文件")
    args = ap.parse_args()

    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT.relative_to(ROOT)}")
        return 0
    if not OUT.is_file():
        print(f"缺少 {OUT.relative_to(ROOT)}；先跑一次 --write", file=sys.stderr)
        return 1
    current = OUT.read_text(encoding="utf-8")
    if current != text:
        print("向量文件与当前实现不一致；确认改动是有意的之后跑 --write", file=sys.stderr)
        return 1
    print(f"{OUT.relative_to(ROOT)} 与当前实现一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
