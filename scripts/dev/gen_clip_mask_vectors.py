"""生成 tests/golden/clip_mask_vectors.json：剪切蒙版轮廓的前后端同源对（ADR 0191）。

每个用例给出画布上的蒙版路径（前端 `PathObject` 形状）、它在导出载荷里的样子（`exportPayload.toExportClip`），以及
后端 `plan.clip_outline` 算出的页面轮廓（换回页面 mm、顶原点 y 向下）。前端 `lib/clipMask.golden.test.ts` 断言
`toExportObjects` 发出的 `clip` 与 `export` 逐字段相同、`maskOutline` 与 `outline_mm` 逐点相同。

    PYTHONPATH=$PWD/src python scripts/dev/gen_clip_mask_vectors.py
"""

from __future__ import annotations

import json
from pathlib import Path

from tavotto.rendercore import plan

PAGE_H_MM = 120.0
MM_PER_PT = 25.4 / 72.0


def _node(p, i=None, o=None):
    out = {"p": p}
    if i is not None:
        out["in"] = i
    if o is not None:
        out["out"] = o
    return out


CASES = [
    {
        "name": "三角形",
        "canvas": {
            "x": 10,
            "y": 20,
            "w": 40,
            "h": 30,
            "subpaths": [
                {"closed": True, "nodes": [_node([0.5, 0]), _node([1, 1]), _node([0, 1])]}
            ],
        },
    },
    {
        "name": "旋转 30° 的曲线闭合路径",
        "canvas": {
            "x": 30,
            "y": 15,
            "w": 50,
            "h": 40,
            "rotationDeg": 30,
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [
                        _node([0, 0.5], [0, 0.8], [0, 0.2]),
                        _node([0.5, 0], [0.2, 0], [0.8, 0]),
                        _node([1, 0.5], [1, 0.2], [1, 0.8]),
                        _node([0.5, 1], [0.8, 1], [0.2, 1]),
                    ],
                }
            ],
        },
    },
    {
        "name": "奇偶规则的环（两条子路径）",
        "canvas": {
            "x": 5,
            "y": 5,
            "w": 60,
            "h": 60,
            "fillRule": "evenodd",
            "subpaths": [
                {
                    "closed": True,
                    "nodes": [_node([0, 0]), _node([1, 0]), _node([1, 1]), _node([0, 1])],
                },
                {
                    "closed": True,
                    "nodes": [
                        _node([0.25, 0.25]),
                        _node([0.75, 0.25]),
                        _node([0.75, 0.75]),
                        _node([0.25, 0.75]),
                    ],
                },
            ],
        },
    },
    {
        "name": "开放路径（按闭合的填充几何裁）",
        "canvas": {
            "x": 0,
            "y": 40,
            "w": 80,
            "h": 20,
            "rotationDeg": 270,
            "subpaths": [
                {"closed": False, "nodes": [_node([0, 1]), _node([0.5, 0]), _node([1, 1])]}
            ],
        },
    },
]


def export_of(c: dict) -> dict:
    """与前端 `exportPayload.toExportClip` 同一个形状：几何字段，缺省不发。"""
    out = {
        "type": "path",
        "x_mm": c["x"],
        "y_mm": c["y"],
        "w_mm": c["w"],
        "h_mm": c["h"],
        "subpaths": c["subpaths"],
    }
    if c.get("fillRule") == "evenodd":
        out["fill_rule"] = "evenodd"
    if c.get("rotationDeg"):
        out["rotation_deg"] = c["rotationDeg"]
    return out


def main() -> None:
    page_h = PAGE_H_MM / MM_PER_PT
    out = []
    for case in CASES:
        exp = export_of(case["canvas"])
        segs, rule = plan.clip_outline(exp, page_h)
        mm = []
        for s in segs:
            if s[0] == "Z":
                mm.append(["Z"])
                continue
            vals = []
            for i in range(1, len(s), 2):
                vals += [round(s[i] * MM_PER_PT, 9), round(PAGE_H_MM - s[i + 1] * MM_PER_PT, 9)]
            mm.append([s[0], *vals])
        out.append({**case, "export": exp, "fill_rule": rule, "outline_mm": mm})
    dest = Path(__file__).resolve().parents[2] / "tests" / "golden" / "clip_mask_vectors.json"
    dest.write_text(
        json.dumps({"page_h_mm": PAGE_H_MM, "cases": out}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {dest} ({len(out)} cases)")


if __name__ == "__main__":
    main()
