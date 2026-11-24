"""生成 tests/golden/path_text_vectors.json：路径文字的展平 / 弧长 / 取点（`rendercore/pathtext`），前端 `lib/pathText` 对拍。

PYTHONPATH=$PWD/src python scripts/dev/gen_path_text_vectors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tavotto.rendercore import pathtext

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

OUT = Path(__file__).resolve().parents[2] / "tests" / "golden" / "path_text_vectors.json"

ARC = [{"p": [0, 1], "out": [0.3, -0.4]}, {"p": [1, 1], "in": [0.7, -0.4]}]
CASES = [
    {
        "name": "line",
        "w": 50,
        "h": 0.5,
        "subpaths": [{"closed": False, "nodes": [{"p": [0, 0.5]}, {"p": [1, 0.5]}]}],
    },
    {"name": "arc", "w": 60, "h": 20, "subpaths": [{"closed": False, "nodes": ARC}]},
    {
        "name": "arc-flip",
        "w": 60,
        "h": 20,
        "flip": True,
        "subpaths": [{"closed": False, "nodes": ARC}],
    },
    {
        "name": "polyline",
        "w": 30,
        "h": 30,
        "subpaths": [
            {"closed": False, "nodes": [{"p": [0, 0]}, {"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}]}
        ],
    },
    {
        "name": "circle-closed",
        "w": 40,
        "h": 40,
        "subpaths": [
            {
                "closed": True,
                "nodes": [
                    {"p": [0.5, 0], "in": [0.224, 0], "out": [0.776, 0]},
                    {"p": [1, 0.5], "in": [1, 0.224], "out": [1, 0.776]},
                    {"p": [0.5, 1], "in": [0.776, 1], "out": [0.224, 1]},
                    {"p": [0, 0.5], "in": [0, 0.776], "out": [0, 0.224]},
                ],
            }
        ],
    },
    {
        "name": "second-subpath-ignored",
        "w": 20,
        "h": 10,
        "subpaths": [
            {"closed": False, "nodes": [{"p": [0, 0]}]},
            {"closed": False, "nodes": [{"p": [0, 1]}, {"p": [1, 0]}]},
            {"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 1]}]},
        ],
    },
]


def main() -> None:
    out = []
    for c in CASES:
        o = {"subpaths": c["subpaths"], **({"flip": True} if c.get("flip") else {})}
        pts, cum, closed = pathtext.path_of(o, c["w"], c["h"])
        total = cum[-1]
        # 不取正好落在折线顶点上的比例：顶点两侧的切线都对，两边浮点差一点就会各取一侧
        fracs = [-0.1, 0, 0.13, 0.497, 0.77, 1, 1.25]
        samples = []
        for f in fracs:
            r = pathtext.point_at(pts, cum, f * total, closed)
            samples.append([f, None if r is None else [round(v, 9) for v in r]])
        out.append(
            {
                **c,
                "n_pts": len(pts),
                "closed": closed,
                "total": round(total, 9),
                "first": [round(v, 9) for v in pts[0]],
                "last": [round(v, 9) for v in pts[-1]],
                "samples": samples,
            }
        )
    # 画布预览的代答：后端「文字转轮廓」对一段路径文字（挪到原点、不带旋转，与 `PathTextView` 发的同一份）的结果
    from tavotto.rendercore import fonts, textoutline
    from tavotto.rendercore.hbshaper import HbFaceProvider

    provider = HbFaceProvider(fonts.FontRegistry.discover())
    preview_obj = {
        "type": "pathText",
        "id": "pt",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 60,
        "h_mm": 20,
        "text": "Tavotto",
        "size_pt": 10,
        "bold": False,
        "italic": False,
        "color": "#c00000",
        "subpaths": [{"closed": False, "nodes": ARC}],
    }
    preview = {
        "object": preview_obj,
        "paths": textoutline.outline_text(preview_obj, faces=provider),
    }
    OUT.write_text(
        json.dumps(
            {"flatten_steps": pathtext.FLATTEN_STEPS, "cases": out, "preview": preview},
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} ({len(out)} cases)")


if __name__ == "__main__":
    main()
