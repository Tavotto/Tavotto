"""复现 / 核对：「原图尺寸」导出被画布页面越界（out-of-page）阻断（QA flagship 节发现的缺陷）。

完整复现（真浏览器）：跑 flagship_driver.cjs 到 `P4b zero-edit export E0`——
干净配置首开一张 7 in（177.8 mm）宽的图 → 快速编辑把面板「为编辑加入本文档」，放在默认
150 × 100 mm 的画布页上（x = −13.9 mm，两侧溢出）→ 打开导出对话框，范围 = 「原图尺寸」、
检查范围写着「仅此图」→ 仍列出 1 条**阻断**「超出页面范围」，不勾「知悉……仍要导出」就不能导出；
勾了之后导出物的样式检查报告写下 `scope: original` + `acknowledged: ["out-of-page"]` + 画布的 `page_mm`。

原图尺寸导出的产物与画布页面无关（页面盒 = figure 尺寸 504 × 230.4 pt，本节 E0 已独立读出），
对话框自己的注释也写着「按原图导出只算那张图上的问题——别的图的字号、页面的比例都不是这次要出的
东西」。规范 §SCI-06：原图范围不混入画布 x/y/w/h。

本脚本只核对证据文件（确定性、离线）：
    python bug_original_scope_out_of_page.py <evidence_dir>
退出码 0 = 缺陷形状仍成立（证据齐）；1 = 证据不符。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    ev = Path(sys.argv[1])
    doc = json.loads((ev / "snap" / "s0-first-open.document.json").read_text(encoding="utf-8"))
    canvas = doc["canvases"][0] if "canvases" in doc else doc
    body = canvas.get("doc", canvas)
    page = body["page"]
    panel = next(o for o in body["objects"] if o["type"] == "panel")
    out_of_page = (
        panel["x"] < 0
        or panel["y"] < 0
        or panel["x"] + panel["w"] > page["w"]
        or panel["y"] + panel["h"] > page["h"]
    )
    report = json.loads((ev / "report.json").read_text(encoding="utf-8"))
    gate = report["exports_gate"]["E0"]
    style = json.loads(
        (ev / "exports" / "E0_style-check.restored3-run.json").read_text(encoding="utf-8")
    )
    inspect = json.loads((ev / "exports" / "E0.inspect.json").read_text(encoding="utf-8"))
    facts = {
        "page_mm": page,
        "panel_rect_mm": [panel["x"], panel["y"], panel["w"], panel["h"]],
        "panel_out_of_page": out_of_page,
        "dialog_check_text": gate["check_text"],
        "had_to_acknowledge": gate["acknowledged_blockers"],
        "style_report_scope": style["scope"],
        "style_report_acknowledged": style["acknowledged"],
        "exported_mediabox_pt": inspect["mediabox_pt"],
    }
    print(json.dumps(facts, ensure_ascii=False, indent=1))
    ok = (
        out_of_page
        and gate["acknowledged_blockers"] is True
        and "超出页面范围" in gate["check_text"]
        and "仅此图" in gate["check_text"]
        and style["scope"] == "original"
        and "out-of-page" in style["acknowledged"]
        and [round(v, 2) for v in inspect["mediabox_pt"]] == [504.0, 230.4]
    )
    print("defect shape reproduced" if ok else "evidence does not match")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
