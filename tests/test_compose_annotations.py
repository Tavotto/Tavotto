"""批次 B 标注能力的矢量导出：新形状 / 端型 / 虚线 / 透明度 / 旋转 / 文字装饰。

全部用 get_drawings()/get_text() 验证真矢量——不允许位图化。
"""

import math

import pymupdf
import pytest

from tavotto import app as m

MM = 72 / 25.4


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    # 纯标注导出不依赖项目；清掉别的测试模块残留的已打开项目，
    # 否则导出会落到那个项目的 tavottofile/export/（project_export_dir 的默认）
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _export(client, tmp_path, objects):
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 100,
            "page_h_mm": 100,
            "formats": ["pdf"],
            "stem": "ann",
            "objects": objects,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    name = resp.get_json()["files"][0]["name"]
    # 从内存开：Windows 上进程持着 PDF 的文件句柄时，下一次导出想覆盖同名
    # 文件会直接 Permission denied（POSIX 无此限制，此前只在 Windows CI 暴露）。
    return pymupdf.open(stream=(tmp_path / name).read_bytes(), filetype="pdf")


def _drawings(page):
    """过滤掉整页白底矩形，只留标注本体的矢量路径。"""
    return [
        d
        for d in page.get_drawings()
        if not (d["rect"].width >= page.rect.width - 1 and d["rect"].height >= page.rect.height - 1)
    ]


def _shape(kind, **kw):
    return {
        "type": "shape",
        "shape": kind,
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 40,
        "h_mm": 30,
        "stroke_pt": 1,
        "color": "#000000",
        "fill": None,
        **kw,
    }


def test_triangle_diamond_polygon_are_vector_paths(client, tmp_path):
    doc = _export(
        client,
        tmp_path,
        [
            _shape("triangle"),
            _shape("diamond", x_mm=55),
            _shape("polygon", y_mm=55, sides=6),
        ],
    )
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
    doc = _export(
        client,
        tmp_path,
        [
            _shape("rect", corner_radius_mm=3, fill="#ff0000", fill_opacity=0.5),
        ],
    )
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


def _line_points(page):
    """直线导出后那条线段的两端（pt，按绘制方向）。

    get_drawings 把一条描边线段来回各记一次（去程 + 回程），取第一条即绘制方向。
    """
    items = [it for d in _drawings(page) for it in d["items"] if it[0] == "l"]
    assert items, "没画出线段"
    return items[0][1], items[0][2]


def test_line_draws_between_endpoints(client, tmp_path):
    """直线端点：比例坐标 → 页面 pt 的换算与前端 ShapeView 逐点同源。

    包围盒 (10,10)+40×30mm，start(0,0)→end(1,1) 即左上到右下的对角线。
    """
    doc = _export(
        client,
        tmp_path,
        [
            _shape("line", start={"rx": 0, "ry": 0}, end={"rx": 1, "ry": 1}),
        ],
    )
    a, b = _line_points(doc[0])
    assert abs(a - pymupdf.Point(10 * MM, 10 * MM)) < 0.01
    assert abs(b - pymupdf.Point(50 * MM, 40 * MM)) < 0.01


def test_line_endpoints_follow_drag_direction(client, tmp_path):
    """从右下往左上拖出来的线：端点顺序不同，线段本身是同一条对角线。"""
    doc = _export(
        client,
        tmp_path,
        [
            _shape("line", start={"rx": 1, "ry": 1}, end={"rx": 0, "ry": 0}),
        ],
    )
    a, b = _line_points(doc[0])
    assert abs(a - pymupdf.Point(50 * MM, 40 * MM)) < 0.01
    assert abs(b - pymupdf.Point(10 * MM, 10 * MM)) < 0.01


def test_line_without_endpoints_is_bbox_midline(client, tmp_path):
    """旧布局文件没有 start/end：兜底成包围盒水平中线，与改动前逐点一致
    （前端同一缺省在 types/document.lineEndpoints）。"""
    doc = _export(client, tmp_path, [_shape("line")])
    a, b = _line_points(doc[0])
    assert abs(a - pymupdf.Point(10 * MM, 25 * MM)) < 0.01
    assert abs(b - pymupdf.Point(50 * MM, 25 * MM)) < 0.01


def _arrow(**kw):
    return {
        "type": "arrow",
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 50,
        "h_mm": 10,
        "start": {"rx": 0, "ry": 0.5},
        "end": {"rx": 1, "ry": 0.5},
        "stroke_pt": 1,
        "color": "#000000",
        "head": "end",
        **kw,
    }


def test_arrow_head_types(client, tmp_path):
    # bar 端型：尖端处一条垂直短线（线段 + bar 线 = 2 个 stroke path，无 fill）
    doc = _export(client, tmp_path, [_arrow(head_start="bar", head_end="open")])
    drawings = _drawings(doc[0])
    assert all(d["fill"] is None for d in drawings)  # 没有实心三角
    # open 端：两段折线经过尖端点 (60mm, 15mm)
    tip = pymupdf.Point(60 * MM, 15 * MM)
    found_tip = any(
        any(it[0] == "l" and (abs(it[1] - tip) < 1 or abs(it[2] - tip) < 1) for it in d["items"])
        for d in drawings
    )
    assert found_tip


def test_arrow_legacy_head_still_triangle(client, tmp_path):
    doc = _export(client, tmp_path, [_arrow(head="both")])
    fills = [d for d in _drawings(doc[0]) if d["fill"] is not None]
    assert len(fills) == 2  # 两端实心三角


def _text(**kw):
    return {
        "type": "text",
        "text": "Underline",
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 60,
        "h_mm": 10,
        "size_pt": 12,
        "bold": False,
        "italic": False,
        "color": "#000000",
        "align": "left",
        **kw,
    }


def test_text_underline_and_bg(client, tmp_path):
    doc = _export(
        client,
        tmp_path,
        [
            _text(underline=True, bg="#ffee00", border_color="#000000", border_pt=1, padding_mm=2),
        ],
    )
    page = doc[0]
    assert "Underline" in page.get_text()  # 文字仍可选择（真矢量）
    drawings = _drawings(page)
    # 背景矩形（带填充）+ 下划线（线段）
    assert any(d["fill"] == (1.0, 232 / 255, 0.0) or d["fill"] is not None for d in drawings)
    assert any(any(it[0] == "l" for it in d["items"]) for d in drawings)


def test_text_line_height(client, tmp_path):
    """行距 2.0 时两行文字的基线间距 = 2 × 字号。"""
    docs = {}
    for lh in (1.25, 2.0):
        doc = _export(client, tmp_path, [_text(text="A\nB", line_height=lh)])
        spans = [
            s
            for b in doc[0].get_text("dict")["blocks"]
            for ln in b.get("lines", [])
            for s in ln.get("spans", [])
        ]
        ys = sorted(s["origin"][1] for s in spans)
        docs[lh] = ys[1] - ys[0]
    assert abs(docs[2.0] - 24) < 0.5  # 12pt × 2.0
    assert abs(docs[1.25] - 15) < 0.5  # 12pt × 1.25


def test_rotated_text_stays_selectable(client, tmp_path):
    # 框放页面中部：贴顶边的框顺时针转 45° 后前几个字符在页外，会被裁掉
    doc = _export(client, tmp_path, [_text(rotation_deg=45, y_mm=45)])
    assert "Underline" in doc[0].get_text()


# ---------------------------------------------------------------------------
# 旋转方向：CSS rotate() 顺时针（y 向下）是权威语义（缺陷：设 90° 导出成 270°）。
# ±90° 的包围盒完全相同——上面 test_rotated_rect_geometry 那把尺子看不见方向，
# 这一组用非对称几何把方向本身钉住，且 90/270（或 45/315）两侧都钉。
# ---------------------------------------------------------------------------


def _rot_cw(px, py, cx, cy, deg):
    """页面坐标（y 向下）绕 (cx,cy) 顺时针转 deg——CSS rotate() 语义的预期值发生器。"""
    a = math.radians(deg)
    dx, dy = px - cx, py - cy
    return (
        cx + dx * math.cos(a) - dy * math.sin(a),
        cy + dx * math.sin(a) + dy * math.cos(a),
    )


def _single_line_chars(page):
    """唯一一行文字的 (dir, chars)。dir 是 get_text 报的阅读方向（页面坐标）。"""
    raw = page.get_text("rawdict")
    lines = [ln for b in raw["blocks"] for ln in b["lines"]]
    assert len(lines) == 1, [ln["dir"] for ln in lines]
    return lines[0]["dir"], [c for s in lines[0]["spans"] for c in s["chars"]]


def test_rotated_text_direction_is_css_clockwise(client, tmp_path):
    """文字 90°：首字符转到框中心正上方、阅读方向朝下（dir=(0,1)）；270° 全反。"""
    for deg, want_dir, head_on_top in ((90, (0.0, 1.0), True), (270, (0.0, -1.0), False)):
        doc = _export(
            client,
            tmp_path,
            [
                _text(
                    text="HEADxxxxxxxxxxxxtail",
                    x_mm=10,
                    y_mm=45,
                    w_mm=80,
                    h_mm=10,
                    rotation_deg=deg,
                )
            ],
        )
        d, chars = _single_line_chars(doc[0])
        assert abs(d[0] - want_dir[0]) < 1e-6 and abs(d[1] - want_dir[1]) < 1e-6, (deg, d)
        first_y = (chars[0]["bbox"][1] + chars[0]["bbox"][3]) / 2
        last_y = (chars[-1]["bbox"][1] + chars[-1]["bbox"][3]) / 2
        cy = 50 * MM
        if head_on_top:
            assert first_y < cy < last_y, (deg, first_y, last_y)
        else:
            assert last_y < cy < first_y, (deg, first_y, last_y)


def test_shape_and_text_co_rotate_clockwise(client, tmp_path):
    """三角形与文字同角度共转：90° 顶点到包围盒右侧、270° 到左侧，文字方向一致。

    文字（TextWriter）与形状（Shape）是 morph 的两条消费路径，只翻一条的半修
    会在这里分叉。"""
    for deg, want_dy in ((90, 1.0), (270, -1.0)):
        doc = _export(
            client,
            tmp_path,
            [
                _shape("triangle", x_mm=30, y_mm=30, w_mm=40, h_mm=40, rotation_deg=deg),
                _text(
                    text="HEADxxxxxxxxxxxxtail",
                    x_mm=10,
                    y_mm=45,
                    w_mm=80,
                    h_mm=10,
                    rotation_deg=deg,
                ),
            ],
        )
        page = doc[0]
        cx, cy = 50 * MM, 50 * MM
        apex_want = _rot_cw(50 * MM, 30 * MM, cx, cy, deg)
        apex_wrong = _rot_cw(50 * MM, 30 * MM, cx, cy, -deg)
        pts = [
            pt
            for dr in _drawings(page)
            for it in dr["items"]
            for pt in it[1:]
            if isinstance(pt, pymupdf.Point)
        ]
        d_want = min(math.hypot(p.x - apex_want[0], p.y - apex_want[1]) for p in pts)
        d_wrong = min(math.hypot(p.x - apex_wrong[0], p.y - apex_wrong[1]) for p in pts)
        assert d_want < 2, (deg, d_want)
        assert d_wrong > 30, (deg, d_wrong)  # 尺子活性：反向预期必须远离，防恒真
        d, _ = _single_line_chars(page)
        assert abs(d[1] - want_dy) < 1e-6, (deg, d)


def test_rotated_arrow_head_is_css_clockwise(client, tmp_path):
    """箭头 90°：三角帽随整体转到包围盒正下方；270° 正上方。

    帽是唯一非对称件——±90° 时杆的两端互为镜像，量杆分不出方向。"""
    for deg in (90, 270):
        doc = _export(
            client,
            tmp_path,
            [
                {
                    "type": "arrow",
                    "x_mm": 30,
                    "y_mm": 45,
                    "w_mm": 40,
                    "h_mm": 10,
                    "start": {"rx": 0, "ry": 0.5},
                    "end": {"rx": 1, "ry": 0.5},
                    "head_start": "none",
                    "head_end": "triangle",
                    "stroke_pt": 1,
                    "color": "#000000",
                    "rotation_deg": deg,
                }
            ],
        )
        heads = [
            pt
            for dr in _drawings(doc[0])
            if dr.get("fill")
            for it in dr["items"]
            for pt in it[1:]
            if isinstance(pt, pymupdf.Point)
        ]
        assert heads, "没画出三角帽"
        hx = sum(p.x for p in heads) / len(heads)
        hy = sum(p.y for p in heads) / len(heads)
        cx, cy = 50 * MM, 50 * MM
        want = _rot_cw(70 * MM, 50 * MM, cx, cy, deg)
        wrong = _rot_cw(70 * MM, 50 * MM, cx, cy, -deg)
        assert math.hypot(hx - want[0], hy - want[1]) < 12, (deg, hx, hy)
        assert math.hypot(hx - wrong[0], hy - wrong[1]) > 60, (deg, hx, hy)


def test_rotated_underline_follows_text_clockwise(client, tmp_path):
    """下划线随 90° 文字变竖直，且 HEAD 端在上；270° HEAD 端在下。"""
    for deg, head_top in ((90, True), (270, False)):
        doc = _export(
            client,
            tmp_path,
            [
                _text(
                    text="HEADtail",
                    x_mm=10,
                    y_mm=45,
                    w_mm=80,
                    h_mm=10,
                    underline=True,
                    rotation_deg=deg,
                )
            ],
        )
        segs = [it for dr in _drawings(doc[0]) for it in dr["items"] if it[0] == "l"]
        assert segs, "没画出下划线"
        a, b = segs[0][1], segs[0][2]
        assert abs(a.x - b.x) < 1, (deg, a, b)  # 竖直
        assert (a.y < b.y) == head_top, (deg, a, b)


def test_rotated_text_background_co_rotates_with_glyphs(client, tmp_path):
    """背景矩形与正文同角度共转（45°/315°，矩形 ±45° 的角点集不同、可辨向）。"""
    corners = [(10, 45), (90, 45), (90, 55), (10, 55)]
    for deg in (45, 315):
        doc = _export(
            client,
            tmp_path,
            [
                _text(
                    text="HEADxxxxxxxxxxxxtail",
                    x_mm=10,
                    y_mm=45,
                    w_mm=80,
                    h_mm=10,
                    bg="#ffee00",
                    rotation_deg=deg,
                )
            ],
        )
        dr = _drawings(doc[0])[0]
        pts = [pt for it in dr["items"] for pt in it[1:] if isinstance(pt, pymupdf.Point)]
        assert pts
        top = min(pts, key=lambda p: p.y)
        cx, cy = 50 * MM, 50 * MM
        want = min((_rot_cw(x * MM, y * MM, cx, cy, deg) for x, y in corners), key=lambda q: q[1])
        wrong = min((_rot_cw(x * MM, y * MM, cx, cy, -deg) for x, y in corners), key=lambda q: q[1])
        assert math.hypot(top.x - want[0], top.y - want[1]) < 3, (deg, top)
        assert math.hypot(top.x - wrong[0], top.y - wrong[1]) > 30, (deg, top)
