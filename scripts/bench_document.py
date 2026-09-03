#!/usr/bin/env python3
"""文档层性能基线：大对象数量的校验 / 原子写 / 读回，与自动保存端点的整次往返。

**先测量，后优化**（与 `bench_render.py` 同一纪律）。这里量的是 Prompt 23 §七
点名的四件事里后端能量到的三件：大文档 load/save、自动保存的写入大小与往返、
版本时间线裁剪。迁移（schema 2 → 3）在前端（`migrateToProject`），不在这里。

每个规模跑 `--repeat` 次取中位，数据每次现造（对象里带 override 与文字，
接近真实文档的形状而不是空壳）。结论写进 `docs/perf-baseline.md`。

用法：
    python scripts/bench_document.py
    python scripts/bench_document.py --sizes 100,1000,5000 --repeat 5 --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("TAVOTTO_NO_TELEMETRY", "1")


def make_doc(n: int) -> dict:
    objects = []
    for i in range(n):
        if i % 3 == 0:
            objects.append(
                {
                    "id": f"p{i}",
                    "type": "panel",
                    "x": (i % 10) * 20.0,
                    "y": (i // 10) * 15.0,
                    "w": 60.0,
                    "h": 40.0,
                    "fileId": f"Fig{i % 7}.pdf",
                    "fileKind": "pdf",
                    "nativeW": 101.6,
                    "nativeH": 76.2,
                    "script": "fig.py",
                    "overrides": [
                        {"gid": "axes_0.title", "prop": "fontsize", "value": 8.5 + (i % 5)},
                        {"gid": "axes_0.xlabel", "prop": "text", "value": f"Flux ×10⁵ ({i})"},
                    ],
                }
            )
        else:
            objects.append(
                {
                    "id": f"t{i}",
                    "type": "text",
                    "text": f"Panel {i} · 25 °C · α β γ",
                    "sizePt": 9,
                    "bold": i % 2 == 0,
                    "color": "#1b1b18",
                    "align": "left",
                    "x": (i % 10) * 20.0,
                    "y": (i // 10) * 15.0 + 41,
                    "w": 60.0,
                    "h": 8.0,
                }
            )
    return {
        "schema": 3,
        "project": {"id": "bench", "name": "bench"},
        "canvases": [
            {
                "id": "c1",
                "name": "Fig 1",
                "page": {"w": 210, "h": 297},
                "objects": objects,
                "guides": [],
            }
        ],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": 1,
    }


def med(fn, repeat: int) -> float:
    ts = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000.0)
    return round(statistics.median(ts), 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="100,1000,5000")
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    from tavotto import app as m
    from tavotto.engine import atomicio, documents

    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        os.environ["TAVOTTO_DATA_DIR"] = str(tmp / "userdata")
        m.LAYOUT_DIR = tmp
        m.AUTOSAVE_DIR = tmp / documents.AUTOSAVE_DIRNAME
        m.VERSIONS_DIR = tmp / documents.VERSIONS_DIRNAME
        m.app.config["TESTING"] = True
        client = m.app.test_client()
        for n in [int(s) for s in args.sizes.split(",") if s]:
            doc = make_doc(n)
            raw = atomicio.dumps_json(doc)
            path = tmp / f"doc{n}.json"
            row = {
                "objects": n,
                "bytes": len(raw),
                "validate_ms": med(lambda: documents.validate_document(doc), args.repeat),
                "write_json_ms": med(lambda: atomicio.write_json(path, doc), args.repeat),
                "read_ms": med(lambda: json.loads(path.read_bytes()), args.repeat),
                "revision_ms": med(lambda: atomicio.content_revision(path), args.repeat),
                "autosave_put_ms": med(
                    lambda: client.put(f"/api/autosave/bench{n}", json=doc), args.repeat
                ),
                "autosave_get_ms": med(lambda: client.get(f"/api/autosave/bench{n}"), args.repeat),
            }
            # 版本时间线：塞满上限再追加一次，量的是「读 → 追加 → 裁剪 → 写」整段
            for i in range(m.VERSION_KEEP_TOTAL):
                client.post(
                    f"/api/versions/bench{n}",
                    json={"doc": doc, "label": f"v{i}", "auto": i % 2 == 0},
                )
            row["version_append_at_cap_ms"] = med(
                lambda: client.post(
                    f"/api/versions/bench{n}", json={"doc": doc, "label": "x", "auto": True}
                ),
                args.repeat,
            )
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
