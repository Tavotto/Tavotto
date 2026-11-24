#!/usr/bin/env python3
"""重出 `tests/golden/vectorscene_vectors.json`（ADR 0208）：图幅分数框 → 画布毫米。

后端 `engine/vectorscene.panel_rect_to_page` 与前端 `canvas/elementGeometry.elementRectOnPage` 必须逐数相同
（裁剪、90° 旋转、翻转都在里面）。pytest 重算比对、vitest（`web/src/lib/vectorscene.golden.test.ts`）把同一批面板喂给
前端函数比对。用法：`python scripts/gen_vectorscene_vectors.py --write`。
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import vectorscene  # noqa: E402

OUT = ROOT / "tests" / "golden" / "vectorscene_vectors.json"


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8（输出是中文；Windows 上被捕获时会退回系统区域编码）。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


BOXES = [[0, 0, 1, 1], [0.1, 0.2, 0.3, 0.25], [0.62, 0.05, 0.2, 0.6], [0.5, 0.5, 0, 0]]


def cases() -> list[dict]:
    out = []
    crops = [None, {"x": 0.1, "y": 0.2, "w": 0.6, "h": 0.5}]
    for rot, fh, fv, crop in itertools.product(
        [0, 90, 180, 270], [False, True], [False, True], crops
    ):
        panel = {"x": 12.5, "y": 30.0, "w": 80.0, "h": 50.0, "rotation": rot}
        if rot in (90, 270):
            panel["w"], panel["h"] = 50.0, 80.0
        if fh:
            panel["flipH"] = True
        if fv:
            panel["flipV"] = True
        if crop:
            panel["crop"] = crop
        out.append(
            {
                "panel": panel,
                "rects": [
                    dict(zip(("x", "y", "w", "h"), vectorscene.panel_rect_to_page(panel, b)))
                    for b in BOXES
                ],
            }
        )
    return out


def build() -> dict:
    return {
        "_doc": "由 scripts/gen_vectorscene_vectors.py 生成，勿手改",
        "boxes": BOXES,
        "cases": cases(),
    }


def main(argv=None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    text = json.dumps(build(), ensure_ascii=False, indent=1) + "\n"
    if a.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"已写入 {OUT}（{len(build()['cases'])} 条）")
        return 0
    if (OUT.read_text(encoding="utf-8") if OUT.exists() else "") != text:
        print("vectorscene_vectors.json 过期：跑 --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
