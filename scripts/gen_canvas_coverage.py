#!/usr/bin/env python3
"""生成 / 校对 `src/tavotto/pdfbackend/canvas_coverage.json`。

画布文字的字形归属计划（`tavotto/glyphplan.py`）有**两个求值器**：Python 侧
问真字体，浏览器侧没有字体引擎、只能读这份生成物。两者的算法同源、oracle
不同源——**这份生成物就是那条差异的看护点**。

    python scripts/gen_canvas_coverage.py            # 校对（有分歧就非零退出）
    python scripts/gen_canvas_coverage.py --write    # 按当前后端重新生成

PyMuPDF 换版本、换平台导致字体覆盖漂移时，红的是这一格；不看护它的话，
漂移的表现是「预览说画得出、导出上是一个方框」，而且只在某些字符上发作。

只依赖 `pdfbackend` 边界层（不直接 import pymupdf）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto import pdfbackend  # noqa: E402

OUT = ROOT / "src" / "tavotto" / "pdfbackend" / "canvas_coverage.json"


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def build() -> dict:
    return {
        "schema": 1,
        "comment": (
            "生成物：画布文字三层字形覆盖的区间表（闭区间，[start, end]）。"
            "唯一产生者 scripts/gen_canvas_coverage.py；手改无效。"
            "primary=请求族的 base-14 脸；cjk=中日韩脸；"
            "fallback=前两层轮不到、而 PyMuPDF 自己挑得出的那一段。"
        ),
        "backend": pdfbackend.BACKEND_NAME,
        "backend_version": pdfbackend.BACKEND_VERSION,
        "max_codepoint": pdfbackend.COVERAGE_MAX_CP,
        "layers": pdfbackend.coverage_ranges(),
    }


def _dump(table: dict) -> str:
    """顶层一行一个键、每层的区间表压成**一行**。

    整份 indent 展开会有三万五千行，`git diff` 里没人读得完；压成一行之后
    一次漂移就是一行变更，而「变了什么」由 `--check` 打出的码位差回答——
    那才是能读的那份 diff。
    """
    lines = ["{"]
    for key, value in table.items():
        if key != "layers":
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append(' "layers": {')
    layers = table["layers"]
    for i, (name, ranges) in enumerate(layers.items()):
        tail = "" if i == len(layers) - 1 else ","
        lines.append(f"  {json.dumps(name)}: {json.dumps(ranges, separators=(',', ':'))}{tail}")
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="按当前后端重新生成")
    args = ap.parse_args()

    fresh = build()
    text = _dump(fresh)
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT.relative_to(ROOT)}")
        return 0

    if not OUT.exists():
        print(f"缺少 {OUT.relative_to(ROOT)}——先跑一次 --write", file=sys.stderr)
        return 1
    stored = json.loads(OUT.read_text(encoding="utf-8"))
    if stored == fresh:
        n = sum(len(v) for v in fresh["layers"].values())
        print(f"覆盖表与当前后端一致（{pdfbackend.BACKEND_VERSION}，{n} 个区间）")
        return 0

    print("覆盖表与当前后端不一致：", file=sys.stderr)
    for key in ("backend", "backend_version", "max_codepoint"):
        if stored.get(key) != fresh.get(key):
            print(f"  {key}: 表里 {stored.get(key)!r} ≠ 现在 {fresh.get(key)!r}", file=sys.stderr)
    for layer, ranges in fresh["layers"].items():
        was = (stored.get("layers") or {}).get(layer)
        if was != ranges:
            a = {c for lo, hi in (was or []) for c in range(lo, hi + 1)}
            b = {c for lo, hi in ranges for c in range(lo, hi + 1)}
            print(
                f"  {layer}: 区间 {len(was or [])} → {len(ranges)}；"
                f"多了 {len(b - a)} 个码位、少了 {len(a - b)} 个",
                file=sys.stderr,
            )
    print("确认过 diff 之后跑 --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
