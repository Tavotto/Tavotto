"""PDF 内容流里读「纯红填充的那条路径」的中心：数据点对照用的独立量法（不经过任何换算代码）。"""

from __future__ import annotations

from pathlib import Path


def red_marker_center_mm(pdf: Path) -> tuple[float, float]:
    """导出的 PDF 里纯红填充的那条路径（红点）的外接框中心，换成 top-origin 的毫米。"""
    import pikepdf

    with pikepdf.open(pdf) as doc:
        page = doc.pages[0]
        height = float(page.MediaBox[3]) - float(page.MediaBox[1])
        red, pts = False, []
        for operands, op in pikepdf.parse_content_stream(page):
            name = str(op)
            if name == "rg":
                red = [float(v) for v in operands] == [1.0, 0.0, 0.0]
            elif red and name in ("m", "l"):
                pts.append((float(operands[0]), float(operands[1])))
            elif red and name == "c":
                pts.append((float(operands[4]), float(operands[5])))
            elif red and pts and name in ("f", "f*", "B", "b"):
                break
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return (cx * 25.4 / 72, (height - cy) * 25.4 / 72)
