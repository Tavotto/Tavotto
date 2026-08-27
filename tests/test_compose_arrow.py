"""_draw_arrow 的几何测试：帽长 4×线宽、帽半宽 1.7×线宽、带帽端回缩 0.75×帽长。

前端 ArrowView 逐点复刻同一几何；这里从 PDF 矢量指令（get_drawings）提取
实际坐标钉死后端行为。
"""
import pymupdf
import pytest

from tavotto.pdfbackend import pymupdf_backend as pb

SW = 2.0  # stroke_pt


def _draw(o: dict):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    pb._draw_arrow(page, {"stroke_pt": SW, "color": "#000000", **o})
    strokes = [d for d in page.get_drawings() if d["fill"] is None]
    fills = [d for d in page.get_drawings() if d["fill"] is not None]
    return strokes, fills


def _points(drawing) -> set[tuple[float, float]]:
    pts = set()
    for item in drawing["items"]:
        for p in item[1:]:
            if isinstance(p, pymupdf.Point):
                pts.add((round(p.x, 2), round(p.y, 2)))
    return pts


HORIZ = {"x_mm": 10, "y_mm": 10, "w_mm": 40, "h_mm": 0,
         "start": {"rx": 0, "ry": 0}, "end": {"rx": 1, "ry": 0}}
AX, AY = pb.mm2pt(10), pb.mm2pt(10)
BX = pb.mm2pt(50)
HEAD_LEN, HEAD_HALF, TRIM = SW * 4.0, SW * 1.7, SW * 4.0 * 0.75


def test_head_end_geometry():
    strokes, fills = _draw({**HORIZ, "head": "end"})
    assert len(strokes) == 1 and len(fills) == 1
    # 干线：起点不回缩，带帽端回缩 0.75×帽长
    line_pts = _points(strokes[0])
    assert (round(AX, 2), round(AY, 2)) in line_pts
    assert (round(BX - TRIM, 2), round(AY, 2)) in line_pts
    assert strokes[0]["width"] == pytest.approx(SW)
    # 箭头帽：tip 在终点，底边 ±1.7×线宽
    expected = {(round(BX, 2), round(AY, 2)),
                (round(BX - HEAD_LEN, 2), round(AY + HEAD_HALF, 2)),
                (round(BX - HEAD_LEN, 2), round(AY - HEAD_HALF, 2))}
    assert expected <= _points(fills[0])


def test_head_none_is_plain_line():
    strokes, fills = _draw({**HORIZ, "head": "none"})
    assert len(fills) == 0
    pts = _points(strokes[0])
    assert (round(AX, 2), round(AY, 2)) in pts
    assert (round(BX, 2), round(AY, 2)) in pts  # 无帽不回缩


def test_head_both_trims_both_ends():
    strokes, fills = _draw({**HORIZ, "head": "both"})
    assert len(fills) == 2
    pts = _points(strokes[0])
    assert (round(AX + TRIM, 2), round(AY, 2)) in pts
    assert (round(BX - TRIM, 2), round(AY, 2)) in pts


def test_diagonal_head_on_unit_vector():
    """斜箭头：帽底点沿单位向量回退，法向偏移 1.7×线宽。"""
    o = {"x_mm": 0, "y_mm": 0, "w_mm": 30, "h_mm": 40,
         "start": {"rx": 0, "ry": 0}, "end": {"rx": 1, "ry": 1}, "head": "end"}
    _, fills = _draw(o)
    bx, by = pb.mm2pt(30), pb.mm2pt(40)
    ux, uy = 0.6, 0.8  # (30,40) 的单位向量
    nx, ny = -uy, ux
    base = (bx - ux * HEAD_LEN, by - uy * HEAD_LEN)
    expected = {(round(bx, 2), round(by, 2)),
                (round(base[0] + nx * HEAD_HALF, 2), round(base[1] + ny * HEAD_HALF, 2)),
                (round(base[0] - nx * HEAD_HALF, 2), round(base[1] - ny * HEAD_HALF, 2))}
    assert expected <= _points(fills[0])
