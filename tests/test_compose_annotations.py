"""批次 B 标注能力的矢量导出：新形状 / 端型 / 虚线 / 透明度 / 旋转 / 文字装饰。

全部用 get_drawings()/get_text() 验证真矢量——不允许位图化。
"""
import math

import pymupdf
import pytest

from magplot import app as m

MM = 72 / 25.4


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _export(client, tmp_path, objects):
    resp = client.post("/api/export", json={
        "page_w_mm": 100, "page_h_mm": 100, "formats": ["pdf"],
        "stem": "ann", "objects": objects})
    assert resp.status_code == 200, resp.get_json()
    name = resp.get_json()["files"][0]["name"]
    return pymupdf.open(tmp_path / name)


def _drawings(page):
    """过滤掉整页白底矩形，只留标注本体的矢量路径。"""
    return [d for d in page.get_drawings()
            if not (d["rect"].width >= page.rect.width - 1
                    and d["rect"].height >= page.rect.height - 1)]


def _shape(kind, **kw):
    return {"type": "shape", "shape": kind, "x_mm": 10, "y_mm": 10,
            "w_mm": 40, "h_mm": 30, "stroke_pt": 1, "color": "#000000",
            "fill": None, **kw}


def test_triangle_diamond_polygon_are_vector_paths(client, tmp_path):
    doc = _export(client, tmp_path, [
        _shape("triangle"),
        _shape("diamond", x_mm=55),
        _shape("polygon", y_mm=55, sides=6),
    ])
    drawings = _drawings(doc[0])
    assert len(drawings) == 3
    # 三角形 3 条线、菱形 4 条、六边形 6 条（closePath 的折线）
    counts = sorted(sum(1 for it in d["items"] if it[0] == "l") for d in drawings)
    assert counts == [3, 4, 6]


def test_brace_has_curves(client, tmp_path):
    doc = _export(client, tmp_path, [_shape("brace", w_mm=8, h_mm=40)])
    items = _drawings(doc[0])[0]["items"]
    assert any(it[0] == "c" for it in items)  # 贝塞尔段
    assert all(d["fill"] is None for d in _drawings(doc[0]))  # 只描边


def test_rounded_rect_and_fill_opacity(client, tmp_path):
    doc = _export(client, tmp_path, [
        _shape("rect", corner_radius_mm=3, fill="#ff0000", fill_opacity=0.5),
    ])
    d = _drawings(doc[0])[0]
    assert d["fill"] == (1.0, 0.0, 0.0)
    assert abs(d["fill_opacity"] - 0.5) < 0.01
    assert any(it[0] == "c" for it in d["items"])  # 圆角 = 曲线段


def test_dashed_stroke(client, tmp_path):
    doc = _export(client, tmp_path, [_shape("rect", dash="dashed")])
    d = _drawings(doc[0])[0]
    assert d["dashes"] and d["dashes"] != "[] 0"


def test_rotated_rect_geometry(client, tmp_path):
    """旋转 90°：矩形长边方向互换（几何级验证，非视觉目测）。"""
    doc = _export(client, tmp_path, [_shape("rect", rotation_deg=90)])
    d = _drawings(doc[0])[0]
    r = d["rect"]
    # 原始 40×30mm；转 90° 后包围盒 ≈ 30×40mm
    assert abs(r.width - 30 * MM) < 2
    assert abs(r.height - 40 * MM) < 2
    # 中心不动
    cx, cy = (10 + 20) * MM, (10 + 15) * MM
    assert abs((r.x0 + r.x1) / 2 - cx) < 1 and abs((r.y0 + r.y1) / 2 - cy) < 1


def _arrow(**kw):
    return {"type": "arrow", "x_mm": 10, "y_mm": 10, "w_mm": 50, "h_mm": 10,
            "start": {"rx": 0, "ry": 0.5}, "end": {"rx": 1, "ry": 0.5},
            "stroke_pt": 1, "color": "#000000", "head": "end", **kw}


def test_arrow_head_types(client, tmp_path):
    # bar 端型：尖端处一条垂直短线（线段 + bar 线 = 2 个 stroke path，无 fill）
    doc = _export(client, tmp_path, [_arrow(head_start="bar", head_end="open")])
    drawings = _drawings(doc[0])
    assert all(d["fill"] is None for d in drawings)  # 没有实心三角
    # open 端：两段折线经过尖端点 (60mm, 15mm)
    tip = pymupdf.Point(60 * MM, 15 * MM)
    found_tip = any(
        any(it[0] == "l" and (abs(it[1] - tip) < 1 or abs(it[2] - tip) < 1)
            for it in d["items"])
        for d in drawings)
    assert found_tip


def test_arrow_legacy_head_still_triangle(client, tmp_path):
    doc = _export(client, tmp_path, [_arrow(head="both")])
    fills = [d for d in _drawings(doc[0]) if d["fill"] is not None]
    assert len(fills) == 2  # 两端实心三角


def _text(**kw):
    return {"type": "text", "text": "Underline", "x_mm": 10, "y_mm": 10,
            "w_mm": 60, "h_mm": 10, "size_pt": 12, "bold": False,
            "italic": False, "color": "#000000", "align": "left", **kw}


def test_text_underline_and_bg(client, tmp_path):
    doc = _export(client, tmp_path, [
        _text(underline=True, bg="#ffee00", border_color="#000000",
              border_pt=1, padding_mm=2),
    ])
    page = doc[0]
    assert "Underline" in page.get_text()  # 文字仍可选择（真矢量）
    drawings = _drawings(page)
    # 背景矩形（带填充）+ 下划线（线段）
    assert any(d["fill"] == (1.0, 232 / 255, 0.0) or d["fill"] is not None
               for d in drawings)
    assert any(any(it[0] == "l" for it in d["items"]) for d in drawings)


def test_text_line_height(client, tmp_path):
    """行距 2.0 时两行文字的基线间距 = 2 × 字号。"""
    docs = {}
    for lh in (1.25, 2.0):
        doc = _export(client, tmp_path, [_text(text="A\nB", line_height=lh)])
        spans = [s for b in doc[0].get_text("dict")["blocks"]
                 for l in b.get("lines", []) for s in l.get("spans", [])]
        ys = sorted(s["origin"][1] for s in spans)
        docs[lh] = ys[1] - ys[0]
    assert abs(docs[2.0] - 24) < 0.5  # 12pt × 2.0
    assert abs(docs[1.25] - 15) < 0.5  # 12pt × 1.25


def test_rotated_text_stays_selectable(client, tmp_path):
    doc = _export(client, tmp_path, [_text(rotation_deg=45)])
    assert "Underline" in doc[0].get_text()
