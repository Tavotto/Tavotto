#!/usr/bin/env python3
"""独立复核 long_probe.py 的导出产物（不经产品的读取代码）。

对 run 目录的 events.jsonl 里每一条成功的 `export`：
  * PDF：用仓库的**纯标准库独立读取器** `tests/support/pdfread.py`（不 import 产品代码，与
    RenderCore 的 pikepdf 写入器不同源）读页面 MediaBox，必须等于请求的 100 × 80 mm
    （283.465 × 226.772 pt，容差 0.01 pt）；页面对象恰好 1 个；
  * PNG：直接解析 IHDR，宽高必须等于 100 × 80 mm @ 150 dpi 的像素数（591 × 472，±1）；
  * 确定性：把导出前最后一次 render 的历史状态当作这次导出的状态，同一状态的 PNG sha256
    应当相同（PDF 含时间戳等元数据，只报数不判）。
输出 JSON；退出码 0 = 全部通过，1 = 有不符。

用法：python check_exports.py <run 目录> <导出目录>
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "tests"))
from support import pdfread  # noqa: E402

MM = 72.0 / 25.4
WANT_PT = (100 * MM, 80 * MM)
WANT_PX = (round(100 / 25.4 * 150), round(80 / 25.4 * 150))


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def main() -> int:
    run, exp = Path(sys.argv[1]), Path(sys.argv[2])
    events = [json.loads(x) for x in (run / "events.jsonl").read_text().splitlines() if x.strip()]
    last_state = None
    by_state: dict[str, set] = defaultdict(set)
    checked = {"pdf": 0, "png": 0}
    problems: list[str] = []
    missing = 0
    for ev in events:
        if ev.get("kind") == "render":
            last_state = json.dumps(ev.get("state"))
        if ev.get("kind") != "export" or ev.get("status") != 200:
            continue
        for f in ev.get("files", []):
            p = exp / f["name"]
            if not p.exists():
                missing += 1
                continue
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() != f["sha256"]:
                problems.append(f"{f['name']}: 磁盘上的字节与导出当时记下的 sha256 不同")
            if f["name"].endswith(".pdf"):
                objs = pdfread.objects(data)
                pages = [h for h, _ in objs.values() if b"/Type /Page" in h and b"/Pages" not in h]
                if len(pages) != 1:
                    problems.append(f"{f['name']}: 页面对象 {len(pages)} 个")
                mb = pdfread.media_box(pdfread.page(objs)[0])
                w, h = mb[2] - mb[0], mb[3] - mb[1]
                if abs(w - WANT_PT[0]) > 0.01 or abs(h - WANT_PT[1]) > 0.01:
                    problems.append(f"{f['name']}: MediaBox {w:.3f}×{h:.3f} pt ≠ {WANT_PT}")
                checked["pdf"] += 1
            else:
                w, h = png_size(data)
                if abs(w - WANT_PX[0]) > 1 or abs(h - WANT_PX[1]) > 1:
                    problems.append(f"{f['name']}: PNG {w}×{h} ≠ {WANT_PX}")
                by_state[last_state].add(hashlib.sha256(data).hexdigest())
                checked["png"] += 1
    nondet = {s: len(h) for s, h in by_state.items() if len(h) > 1}
    out = {
        "checked": checked,
        "missing_files": missing,
        "states_exported": len(by_state),
        "png_nondeterministic_states": nondet,
        "problems": problems[:20],
        "problem_count": len(problems),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if problems or nondet else 0


if __name__ == "__main__":
    raise SystemExit(main())
