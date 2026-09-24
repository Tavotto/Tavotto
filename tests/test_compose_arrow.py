"""箭头的几何合同：帽长 4×线宽、帽半宽 1.7×线宽、带帽端回缩 0.75×帽长（用户合同，前端 ArrowView 逐点复刻）。

经契约层 `pdfbackend.compose()` 合成进真实 PDF，再从内容流的矢量指令里把实际坐标抽回来
（`q … cm` 的组变换手动套回页面、顶原点——与旧用例 PyMuPDF `get_drawings()` 报的同一口径），
钉死后端行为。U10 之前这里直接调旧后端私有的 `_draw_arrow`（ADR 0072）；合同一字未变。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
from pathlib import Path

import pytest

from tavotto import pdfbackend

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")

SW = 2.0  # stroke_pt
PAGE_PT = (400.0, 300.0)
_NUM = r"-?\d+(?:\.\d+)?"


@pytest.fixture(scope="module", autouse=True)
def _fonts_ready():
    if not HAS:
        pytest.skip("RenderCore 依赖未装（not_run）")
    from tavotto.rendercore import fonts

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")


def _drawings(content: bytes) -> list[dict]:
    """内容流 → [{'points': {(x, y)…页面顶原点 pt}, 'fill': bool, 'width': float}]。
    只认写入器会写的那几种指令（q / Q / cm / w / m / l / h / S / f / re）。"""
    tokens = re.findall(rb"[^\s]+", content)
    stack: list[tuple[float, ...]] = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    width = 1.0
    pts: set[tuple[float, float]] = set()
    out: list[dict] = []
    stackw: list[float] = []

    def apply(x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = ctm
        px, py = a * x + c * y + e, b * x + d * y + f
        return (round(px, 2), round(PAGE_PT[1] - py, 2))

    i = 0
    nums: list[float] = []
    while i < len(tokens):
        t = tokens[i]
        i += 1
        if re.fullmatch(_NUM.encode(), t):
            nums.append(float(t))
            continue
        op = t.decode("latin-1")
        if op == "q":
            stack.append(ctm)
            stackw.append(width)
        elif op == "Q":
            ctm = stack.pop()
            width = stackw.pop()
        elif op == "cm":
            a, b, c, d, e, f = nums[-6:]
            A, B, C, D, E, F = ctm
            ctm = (
                a * A + b * C,
                a * B + b * D,
                c * A + d * C,
                c * B + d * D,
                e * A + f * C + E,
                e * B + f * D + F,
            )
        elif op == "w":
            width = nums[-1]
        elif op in ("m", "l"):
            pts.add(apply(*nums[-2:]))
        elif op == "re":
            x, y, w, h = nums[-4:]
            for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                pts.add(apply(px, py))
        elif op in ("S", "f", "B", "f*", "B*", "s"):
            out.append({"points": pts, "fill": op != "S", "width": width})
            pts = set()
        nums = []
    return out


def _draw(o: dict):
    out = Path(tempfile.mkdtemp(prefix="compose-arrow-")) / "a.pdf"
    canvas = pdfbackend.compose(PAGE_PT[0] * 25.4 / 72, PAGE_PT[1] * 25.4 / 72)
    try:
        canvas.place(
            {"type": "arrow", "id": "a", "stroke_pt": SW, "color": "#000000", **o},
            150,
            lambda x, d: None,
        )
        canvas.save_pdf(out)
    finally:
        canvas.close()
    objs = pdfread.objects(out.read_bytes())
    _head, content = pdfread.page(objs)
    drawings = _drawings(content)[1:]  # 第一个是整页白底
    strokes = [d for d in drawings if not d["fill"]]
    fills = [d for d in drawings if d["fill"]]
    return strokes, fills


def _points(drawing) -> set[tuple[float, float]]:
    return drawing["points"]


HORIZ = {
    "x_mm": 10,
    "y_mm": 10,
    "w_mm": 40,
    "h_mm": 0,
    "start": {"rx": 0, "ry": 0},
    "end": {"rx": 1, "ry": 0},
}
AX, AY = pdfbackend.mm2pt(10), pdfbackend.mm2pt(10)
BX = pdfbackend.mm2pt(50)
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
    expected = {
        (round(BX, 2), round(AY, 2)),
        (round(BX - HEAD_LEN, 2), round(AY + HEAD_HALF, 2)),
        (round(BX - HEAD_LEN, 2), round(AY - HEAD_HALF, 2)),
    }
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
    o = {
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 30,
        "h_mm": 40,
        "start": {"rx": 0, "ry": 0},
        "end": {"rx": 1, "ry": 1},
        "head": "end",
    }
    _, fills = _draw(o)
    bx, by = pdfbackend.mm2pt(30), pdfbackend.mm2pt(40)
    ux, uy = 0.6, 0.8  # (30,40) 的单位向量
    nx, ny = -uy, ux
    base = (bx - ux * HEAD_LEN, by - uy * HEAD_LEN)
    expected = {
        (round(bx, 2), round(by, 2)),
        (round(base[0] + nx * HEAD_HALF, 2), round(base[1] + ny * HEAD_HALF, 2)),
        (round(base[0] - nx * HEAD_HALF, 2), round(base[1] - ny * HEAD_HALF, 2)),
    }
    assert expected <= _points(fills[0])
