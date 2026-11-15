"""生成 tests/golden/text_outline.json：文字转轮廓（ADR 0185 M5）的后端结果快照。

字体是 allowlist 按 sha256 钉死的批准字体（`scripts/fetch_fonts.py`），所以快照与机器无关。后端 `tests/test_text_outline.py`
核 `outline_text` 与快照一致；前端 `store/textOutlineActions.test.tsx` 拿同一份结果当后端的回答，核落到画布的规则。

    PYTHONPATH=$PWD/src python scripts/dev/gen_text_outline_golden.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]

CASES = [
    {
        "name": "单色一行",
        "text": {
            "type": "text", "id": "t1", "x_mm": 10, "y_mm": 20, "w_mm": 30, "h_mm": 8, "text": "Ab",
            "size_pt": 12, "bold": False, "italic": False, "color": "#1f4e9a", "align": "left",
        },
    },
    {
        "name": "带底框和下划线（三条路径）",
        "text": {
            "type": "text", "id": "t2", "x_mm": 5, "y_mm": 5, "w_mm": 20, "h_mm": 7, "text": "x",
            "size_pt": 10, "bold": True, "italic": False, "color": "#000000", "align": "center",
            "underline": True, "bg": "#ffeeaa", "rotation_deg": 15,
        },
    },
]  # fmt: skip


def main() -> None:
    from tavotto.rendercore import fonts, textoutline
    from tavotto.rendercore.hbshaper import HbFaceProvider

    provider = HbFaceProvider(fonts.FontRegistry.discover())
    out = [{**c, "paths": textoutline.outline_text(c["text"], faces=provider)} for c in CASES]
    dest = ROOT / "tests" / "golden" / "text_outline.json"
    dest.write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {dest.relative_to(ROOT)} ({[len(c['paths']) for c in out]} paths)")


if __name__ == "__main__":
    main()
