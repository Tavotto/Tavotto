"""导出物独立读取（不 import 任何 Tavotto 代码）：pdfminer.six 读页面盒、文字行、路径；poppler 栅格化。

用法：python inspect_export.py <pdf> <out.json> [--raster <out.png>]

输出（JSON）：
* ``mediabox_pt``：[w, h]
* ``texts``：[{text, bbox_pt: [x0, y0, x1, y1]（bottom-origin）, center_frac_top: [cx, cy]}]
* ``curves4``：四个顶点的折线（点序列那条线就在里面），每条 [[x, y], ...]
* ``raster_sha256``：pdftoppm 100 dpi PNG 的 sha256（如请求）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTCurve, LTTextContainer, LTTextLine


def walk(obj):
    yield obj
    if hasattr(obj, "__iter__"):
        for child in obj:
            yield from walk(child)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--raster")
    a = ap.parse_args()
    pages = list(extract_pages(a.pdf, laparams=LAParams(line_margin=0.01, char_margin=1.0)))
    page = pages[0]
    w, h = page.width, page.height
    texts = []
    curves = []
    for o in walk(page):
        if isinstance(o, LTTextLine):
            chars = [c for c in o if isinstance(c, LTChar)]
            if not chars:
                continue
            x0, y0, x1, y1 = o.bbox
            texts.append(
                {
                    "text": o.get_text().strip(),
                    "bbox_pt": [x0, y0, x1, y1],
                    "center_frac_top": [(x0 + x1) / 2 / w, 1 - (y0 + y1) / 2 / h],
                    "fontsize": round(chars[0].size, 3),
                }
            )
        elif isinstance(o, LTCurve) and not isinstance(o, LTTextContainer):
            pts = getattr(o, "pts", None)
            if pts and len(pts) == 4:
                curves.append([[float(x), float(y)] for x, y in pts])
    out = {
        "pages": len(pages),
        "mediabox_pt": [w, h],
        "texts": texts,
        "curves4": curves,
        "pdf_sha256": hashlib.sha256(Path(a.pdf).read_bytes()).hexdigest(),
    }
    if a.raster:
        stem = str(Path(a.raster).with_suffix(""))
        subprocess.run(["pdftoppm", "-r", "100", "-png", "-singlefile", a.pdf, stem], check=True)
        out["raster_png"] = stem + ".png"
        out["raster_sha256"] = hashlib.sha256(Path(stem + ".png").read_bytes()).hexdigest()
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "pages": out["pages"],
                "mediabox_pt": out["mediabox_pt"],
                "texts": len(texts),
                "curves4": len(curves),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
