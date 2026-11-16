"""路径文字（ADR 0190，`rendercore/pathtext.py` + `plan._path_text_node`）。

判据：
- 落字：一行字按弧长逐字落到路径上——直路径上每个字的原点就是起点 + 前面字宽之和（与普通文字同一次排字的 advance），
  居中 / 末尾按字串宽往回退，翻转 = 反着走（字头朝另一侧），开放路径越界的字不画、闭合路径绕回来；
- 同源：画布照「文字转轮廓」的结果画（`/api/text/outline`），它与导出的字**按像素**比过（与 `test_text_outline` 同一判据）；
- 三个写入器都写得出；导出载荷、接口认这个类型；`break_lines` 不经过（一行）；
- 弧长几何与前端对拍：`tests/golden/path_text_vectors.json`。
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

from tavotto.rendercore import ir, pathtext, plan, textoutline, typography

HAS = all(
    importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL", "pypdfium2")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")

GOLDEN = Path(__file__).parent / "golden" / "path_text_vectors.json"
PT = 72 / 25.4
ARC = [{"p": [0, 1], "out": [0.3, -0.4]}, {"p": [1, 1], "in": [0.7, -0.4]}]
LINE = [{"p": [0, 0.5]}, {"p": [1, 0.5]}]
CIRCLE = [
    {"p": [0.5, 0], "in": [0.224, 0], "out": [0.776, 0]},
    {"p": [1, 0.5], "in": [1, 0.224], "out": [1, 0.776]},
    {"p": [0.5, 1], "in": [0.776, 1], "out": [0.224, 1]},
    {"p": [0, 0.5], "in": [0, 0.776], "out": [0, 0.224]},
]


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(fonts.FontRegistry.discover())


def _pt(**over):
    o = {
        "type": "pathText",
        "id": "pt",
        "x_mm": 8,
        "y_mm": 10,
        "w_mm": 50,
        "h_mm": 16,
        "text": "Hag Ωμ 中文",
        "size_pt": 12,
        "bold": False,
        "italic": False,
        "color": "#1f4e9a",
        "subpaths": [{"closed": False, "nodes": ARC}],
    }
    o.update(over)
    return o


def _glyph_groups(o, provider):
    page = plan.compile_page(80, 50, [o], sources=None, faces=provider).page  # type: ignore[arg-type]
    (obj,) = page.children
    assert isinstance(obj, ir.Group)
    return page, list(obj.children)


def _origins(groups):
    """每个字的原点（页面 pt，PDF y 向上）与 x 轴方向。字不许成反字：变换的行列式 > 0（翻转是转 180°，不是镜像）。"""
    out = []
    for g in groups:
        a, b, c, d, e, f = g.transform
        assert a * d - b * c > 0, g.transform
        out.append((e, f, math.atan2(b, a)))
    return out


def _line(o, provider):
    """与 `_path_text_node` 同一次排字：每个字的 advance（pt）。"""
    fs = plan.faces_for(
        provider, o.get("font_family"), o.get("bold", False), o.get("italic", False)
    )
    block = typography.layout_text(pathtext.line_text(o), fs, None)
    (line,) = block.lines
    adv = []
    for p in line.pieces:
        scale = p.size / p.face.resource.units_per_em
        adv += [g.x_advance * scale for g in p.glyphs]
    return adv


# --------------------------------------------------------------- 弧长几何（与前端对拍）
def test_golden_vectors_match_the_backend():
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert g["flatten_steps"] == pathtext.FLATTEN_STEPS
    for c in g["cases"]:
        o = {"subpaths": c["subpaths"], **({"flip": True} if c.get("flip") else {})}
        pts, cum, closed = pathtext.path_of(o, c["w"], c["h"])
        assert len(pts) == c["n_pts"] and closed == c["closed"]
        assert cum[-1] == pytest.approx(c["total"], abs=1e-9)
        for f, want in c["samples"]:
            got = pathtext.point_at(pts, cum, f * cum[-1], closed)
            if want is None:
                assert got is None, (c["name"], f)
            else:
                assert got == pytest.approx(want, abs=1e-7), (c["name"], f)


def test_arc_length_of_a_quarter_circle_is_close_to_exact():
    # 一段 90° 圆弧的标准贝塞尔近似：弧长 ≈ πr/2（展平 32 段的误差远小于 0.1%）
    k = 0.5523
    o = {
        "subpaths": [
            {
                "closed": False,
                "nodes": [{"p": [0, 1], "out": [0, 1 - k]}, {"p": [1, 0], "in": [1 - k, 0]}],
            }
        ]
    }
    _pts, cum, _closed = pathtext.path_of(o, 100, 100)
    assert cum[-1] == pytest.approx(math.pi * 100 / 2, rel=1e-3)


# --------------------------------------------------------------- 落字
def test_straight_path_places_glyphs_at_cumulative_advances(provider):
    o = _pt(
        subpaths=[{"closed": False, "nodes": LINE}],
        text="Hello wd",
        h_mm=0.5,
        start_offset=0.1,
        baseline_offset_pt=0,
    )
    _page, groups = _glyph_groups(o, provider)
    adv = _line(o, provider)
    assert len(groups) == len(adv)
    org = _origins(groups)
    x0 = o["x_mm"] * PT + 0.1 * o["w_mm"] * PT
    run = 0.0
    for (ex, _ey, ang), a in zip(org, adv):
        assert ex == pytest.approx(x0 + run, abs=1e-6)
        assert ang == pytest.approx(0, abs=1e-9)
        run += a
    # 离线 0 = 基线在路径上：所有字同一个 y，等于路径的 y（页面 50 mm 高，PDF y 向上）
    ys = {round(ey, 6) for _ex, ey, _a in org}
    assert ys == {round((50 - (o["y_mm"] + o["h_mm"] / 2)) * PT, 6)}


@pytest.mark.parametrize("align,shift", [("center", 0.5), ("end", 1.0)])
def test_alignment_pulls_the_run_back_from_the_start_point(provider, align, shift):
    base = _pt(subpaths=[{"closed": False, "nodes": LINE}], text="Abc", h_mm=0.5, start_offset=0.5)
    _p, g0 = _glyph_groups(base, provider)
    _p, g1 = _glyph_groups({**base, "text_align": align}, provider)
    run = sum(_line(base, provider))
    assert _origins(g1)[0][0] == pytest.approx(_origins(g0)[0][0] - shift * run, abs=1e-6)


def test_flip_walks_the_path_backwards_with_glyphs_upside_down(provider):
    o = _pt(
        subpaths=[{"closed": False, "nodes": LINE}],
        text="Ab",
        h_mm=0.5,
        flip=True,
        baseline_offset_pt=0,
    )
    _p, groups = _glyph_groups(o, provider)
    (x1, _y1, a1), (x2, _y2, _a2) = _origins(groups)
    right = (o["x_mm"] + o["w_mm"]) * PT
    assert x1 == pytest.approx(right, abs=1e-6)  # 第一个字从路径的终点起
    assert x2 < x1  # 往左走
    assert abs(a1) == pytest.approx(math.pi, abs=1e-9)  # 字头朝下（翻到路径另一侧）


# --------------------------------------------------------------- 离线（字不碰线）
def _line_y_pdf(o):
    return (50 - (o["y_mm"] + o["h_mm"] / 2)) * PT


def _descender(o, provider):
    return plan.faces_for(provider, o.get("font_family"), False, False).primary.descender


@pytest.mark.parametrize("flip", [False, True])
def test_auto_offset_lifts_the_baseline_off_the_line(provider, flip):
    line = [{"closed": False, "nodes": LINE}]
    o = _pt(
        subpaths=line, text="Hag", h_mm=0.5, line_width_pt=1.5, **({"flip": True} if flip else {})
    )
    want = 1.5 / 2 + 0.2 * 12 + -_descender(o, provider) * 12
    assert pathtext.offset_pt(o, _descender(o, provider)) == pytest.approx(want)
    assert plan.path_text_auto_offset(o, provider) == pytest.approx(want)
    _p, groups = _glyph_groups(o, provider)
    ys = {round(f, 6) for _e, f, _a in _origins(groups)}
    # 没翻面：字在线上方（PDF y 向上 = 大）；翻面：字在线下方、头朝下
    sign = -1 if flip else 1
    assert ys == {round(_line_y_pdf(o) + sign * want, 6)}


def test_manual_offset_wins_and_may_be_negative(provider):
    o = _pt(
        subpaths=[{"closed": False, "nodes": LINE}], text="Hag", h_mm=0.5, baseline_offset_pt=-2
    )
    _p, groups = _glyph_groups(o, provider)
    assert {round(f, 6) for _e, f, _a in _origins(groups)} == {round(_line_y_pdf(o) - 2, 6)}
    assert pathtext.offset_pt({**o, "baseline_offset_pt": float("nan")}, -0.2) == pytest.approx(
        pathtext.auto_offset_pt(o, -0.2)
    )


@pytest.mark.parametrize("flip", [False, True])
def test_auto_offset_keeps_every_glyph_clear_of_the_stroke(provider, flip):
    """字形轮廓（与导出同一份）离线的描边外缘至少还有一点空：降部也不穿线。"""
    lw = 2.0
    o = _pt(
        subpaths=[{"closed": False, "nodes": LINE}],
        text="Hagypj (q)",
        h_mm=0.5,
        line_width_pt=lw,
        **({"flip": True} if flip else {}),
    )
    paths = textoutline.outline_text(o, faces=provider)
    line_y = o["y_mm"] + o["h_mm"] / 2
    edge = lw / 2 / PT
    near = []
    for p in paths:
        top, bottom = p["y"], p["y"] + p["h"]
        near.append((line_y - edge) - bottom if not flip else top - (line_y + edge))
    assert min(near) > 0.05 * 12 / PT, near


# --------------------------------------------------------------- 沿平行线排（离线多大字距都不变）
def _mids(o, provider):
    """每个字的中点（框空间 pt）与 advance：从落字组的变换反推（原点 + 半个字宽沿 x 轴）。"""
    _p, groups = _glyph_groups(o, provider)
    adv = _line(o, provider)
    to_page = plan._object_transform(o, 50 * PT)
    inv = _invert(to_page)
    out = []
    for g, a in zip(groups, adv):
        m = ir.compose(g.transform, inv)  # 字局部 → 框空间
        x, y = ir.apply(m, a / 2, 0.0)
        out.append((x, y))
    return out, adv


def _invert(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


@pytest.mark.parametrize("off,flip", [(17, False), (17, True), (8, False)])
def test_glyph_spacing_does_not_grow_with_offset(provider, off, flip):
    o = _pt(
        subpaths=[{"closed": True, "nodes": CIRCLE}],
        w_mm=40,
        h_mm=40,
        text="ABCDEFGH",
        baseline_offset_pt=off,
        **({"flip": True} if flip else {}),
    )
    mids, adv = _mids(o, provider)
    for (p, q), a, b in zip(zip(mids, mids[1:]), adv, adv[1:]):
        # 相邻两字中点的弦长 ≈ 两个半字宽之和（平行线上按弧长排；按原线排时圆外会大出 off / r 那么多）
        assert math.hypot(q[0] - p[0], q[1] - p[1]) == pytest.approx((a + b) / 2, rel=0.03)


def _arc_param(pts, cum, q):
    best = (math.inf, 0.0)
    for i in range(1, len(pts)):
        (ax, ay), (bx, by) = pts[i - 1], pts[i]
        dx, dy = bx - ax, by - ay
        ln2 = dx * dx + dy * dy
        u = 0.0 if ln2 == 0 else max(0.0, min(1.0, ((q[0] - ax) * dx + (q[1] - ay) * dy) / ln2))
        d = math.hypot(ax + dx * u - q[0], ay + dy * u - q[1])
        if d < best[0]:
            best = (d, cum[i - 1] + (cum[i] - cum[i - 1]) * u)
    return best[1]


@pytest.mark.parametrize(
    "nodes,off",
    [
        # 直角拐弯，内侧离得比拐角还远：平行线会打结
        ([{"p": [0, 1]}, {"p": [0.5, 0]}, {"p": [1, 1]}], -14),
        ([{"p": [0, 0]}, {"p": [0.5, 1]}, {"p": [1, 0]}], 14),
        # 小弧内侧，离线大于曲率半径
        ([{"p": [0, 1], "out": [0, 0]}, {"p": [1, 1], "in": [1, 0]}], -40),
    ],
    ids=["corner-inside-neg", "corner-inside", "tight-arc"],
)
def test_sharp_corners_and_cusps_keep_glyphs_in_order(provider, nodes, off):
    """尖角 / 平行线自交：字仍按路径的先后排开，不来回跳；落到哪就落到哪，不抛错。"""
    o = _pt(
        subpaths=[{"closed": False, "nodes": nodes}],
        w_mm=20,
        h_mm=10,
        text="abcdefghij",
        baseline_offset_pt=off,
    )
    mids, _adv = _mids(o, provider)
    assert mids
    _x, _y, w, h = plan._box_pt(o, allow_zero=True)
    pts, cum, _closed = pathtext.path_of(o, w, h)
    # 不来回跳：相邻两字中点隔得不比两个字宽还远
    step = max(_adv) * 2
    assert all(math.hypot(q[0] - p[0], q[1] - p[1]) <= step for p, q in zip(mids, mids[1:]))
    # 平行线没整个塌掉时，字沿原路径的先后顺序排（离线比曲率半径还大、整条塌掉时退回「原线 + 法向平移」，只核不跳）
    if len(pathtext.parallel(pts, False, off)) > 1:
        ts = [_arc_param(pts, cum, m) for m in mids]
        assert all(b >= a - 1e-6 for a, b in zip(ts, ts[1:])), ts


def test_parallel_of_zero_offset_is_the_path_itself():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert pathtext.parallel(pts, False, 0) == pts
    # 直线向上（y 向下空间里是 −y）平移
    assert pathtext.parallel([(0.0, 0.0), (10.0, 0.0)], False, 3) == [(0.0, -3.0), (10.0, -3.0)]


# --------------------------------------------------------------- 放不下的字
def test_overflow_flag_only_when_glyphs_fall_off_an_open_path(provider):
    short = _pt(subpaths=[{"closed": False, "nodes": LINE}], w_mm=6, h_mm=0.5, text="ABCDEFGHIJ")
    long = {**short, "w_mm": 80}
    ring = _pt(subpaths=[{"closed": True, "nodes": CIRCLE}], w_mm=6, h_mm=6, text="ABCDEFGHIJ")
    assert plan.path_text_overflow(short, provider) is True
    assert plan.path_text_overflow(long, provider) is False
    assert plan.path_text_overflow(ring, provider) is False


def test_open_path_drops_glyphs_past_its_end(provider):
    o = _pt(subpaths=[{"closed": False, "nodes": LINE}], w_mm=6, h_mm=0.5, text="ABCDEFGHIJ")
    _p, groups = _glyph_groups(o, provider)
    adv = _line(o, provider)
    fits = 0
    s = 0.0
    for a in adv:
        if s + a / 2 <= 6 * PT:
            fits += 1
        s += a
    assert 0 < len(groups) == fits < len(adv)


def test_closed_path_wraps_round(provider):
    o = _pt(
        subpaths=[{"closed": True, "nodes": CIRCLE}],
        w_mm=30,
        h_mm=30,
        text="Around the ring",
        start_offset=0.9,
    )
    _p, groups = _glyph_groups(o, provider)
    assert len(groups) == len(_line(o, provider))


def test_glyphs_follow_the_tangent_on_a_curve(provider):
    o = _pt(text="Wave")
    _p, groups = _glyph_groups(o, provider)
    angs = [a for _x, _y, a in _origins(groups)]
    # 上拱的弧（屏幕 y 向下、PDF y 向上）：起头往上爬（PDF 里角度为正），越往后越平
    assert angs[0] > 0.3 and angs[0] > angs[-1]


def test_newlines_fold_into_one_line(provider):
    o = _pt(text="A\nB")
    assert pathtext.line_text(o)["text"] == "A B"
    _p, groups = _glyph_groups(o, provider)
    assert len(groups) == 3


def test_empty_or_degenerate_draws_nothing(provider):
    page = plan.compile_page(80, 50, [_pt(text="  ")], sources=None, faces=provider).page  # type: ignore[arg-type]
    assert page.children == ()
    page = plan.compile_page(
        80,
        50,
        [_pt(subpaths=[{"closed": False, "nodes": [{"p": [0, 0]}]}])],
        sources=None,
        faces=provider,
    ).page  # type: ignore[arg-type]
    assert page.children == ()


def test_one_line_through_the_unchanged_break_lines(provider, monkeypatch):
    calls = []
    real = typography.break_lines

    def spy(*a, **k):
        calls.append(a[0])
        return real(*a, **k)

    monkeypatch.setattr(typography, "break_lines", spy)
    _glyph_groups(_pt(text="a b c d e f g h i j k l m n o p"), provider)
    # 排一行走的是 layout_text → break_lines，但框宽给够了，结果只有一行（规则一个字不改）
    assert calls and all("\n" not in c for c in calls)


# --------------------------------------------------------------- 同源：画布 = 文字转轮廓 = 导出
def _as_export(p: dict) -> dict:
    out = {
        "type": "path",
        "x_mm": p["x"],
        "y_mm": p["y"],
        "w_mm": p["w"],
        "h_mm": p["h"],
        "subpaths": p["subpaths"],
        "stroke_pt": p["strokePt"],
        "color": p["color"],
        "fill": p["fill"],
    }
    if p.get("fillRule") == "evenodd":
        out["fill_rule"] = "evenodd"
    if p.get("stroke") is False:
        out["stroke"] = False
    return out


def _raster(objs, provider, tmp_path, name, scale=6):
    import pypdfium2 as pdfium

    from tavotto.rendercore import pdfwriter

    page = plan.compile_page(80, 50, objs, sources=None, faces=provider).page  # type: ignore[arg-type]
    out = tmp_path / f"{name}.pdf"
    pdfwriter.write_pdf(page, out, provider)
    return pdfium.PdfDocument(str(out))[0].render(scale=scale).to_pil().convert("L")


def _diff(a, b):
    from PIL import ImageChops

    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / total, sum(
        n for i, n in enumerate(hist) if i > 96
    )


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"rotation_deg": 30},
        {"flip": True, "text_align": "center", "start_offset": 0.5},
        {"bold": True, "italic": True, "font_family": "sans-serif"},
        {
            "subpaths": [{"closed": True, "nodes": CIRCLE}],
            "w_mm": 34,
            "h_mm": 34,
            "start_offset": 0.6,
        },
        {"text": "x² + y² = r²"},
        {"line_width_pt": 2.5},
        {"baseline_offset_pt": -3, "flip": True},
        {"baseline_offset_pt": 17},
    ],
    ids=[
        "arc",
        "rotated",
        "flip-center",
        "bold-italic-sans",
        "circle",
        "superscript",
        "auto-offset-thick-line",
        "manual-negative-flip",
        "offset-17pt-parallel",
    ],
)
def test_outline_preview_renders_like_the_export(provider, tmp_path, over):
    o = _pt(**over)
    paths = textoutline.outline_text(o, faces=provider)
    assert paths and all(p["type"] == "path" for p in paths)
    a = _raster([o], provider, tmp_path, "pt")
    b = _raster([_as_export(p) for p in paths], provider, tmp_path, "paths")
    mean, big = _diff(a, b)
    assert mean < 1.0, mean
    assert big < a.size[0] * a.size[1] * 0.0005, big
    # 有字画出来（不是两张空白相等）
    assert a.getextrema()[0] < 128


def test_canvas_preview_payload_moves_to_origin_and_back(provider):
    """画布把对象挪到原点、去掉旋转发给接口（`PathTextView.localPayload`）：拿回来的轮廓平移回去就是原位的轮廓。"""
    o = _pt()
    here = textoutline.outline_text(o, faces=provider)
    there = textoutline.outline_text({**o, "x_mm": 0, "y_mm": 0}, faces=provider)
    assert len(here) == len(there)
    for p, q in zip(here, there):
        assert p["x"] == pytest.approx(q["x"] + o["x_mm"], abs=1e-4)
        assert p["y"] == pytest.approx(q["y"] + o["y_mm"], abs=1e-4)
        assert p["w"] == pytest.approx(q["w"], abs=1e-4)


# --------------------------------------------------------------- 写入器 / 载荷 / 接口
def test_all_three_writers(provider, tmp_path):
    from tavotto.rendercore import epswriter, pdfwriter, svgwriter

    page = plan.compile_page(80, 50, [_pt()], sources=None, faces=provider).page  # type: ignore[arg-type]
    pdfwriter.write_pdf(page, tmp_path / "a.pdf", provider)
    assert (tmp_path / "a.pdf").stat().st_size > 1000
    svgwriter.write_svg(page, tmp_path / "a.svg", provider)
    svg = (tmp_path / "a.svg").read_text(encoding="utf-8")
    # 每个字一个带变换的组：SVG 里字是转着的
    assert svg.count("rotate(") + svg.count("matrix(") >= len("Hag Ωμ 中文".replace(" ", ""))
    ps, _facts = epswriter.overlay_ps(page, provider)
    # EPS 覆盖层：每个字（9 个画得出的字）各自一次 concat
    assert ps.count(" concat") >= 9


def test_export_request_accepts_path_text():
    from tavotto.engine import exportreq

    assert "pathText" in exportreq.ORIGINAL_OBJECT_TYPES


def test_outline_endpoint_accepts_path_text():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    r = client.post("/api/text/outline", json={"objects": [_pt(text="Hi")]})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["results"][0]["paths"][0]["type"] == "path"
    # 自动离线算出来是多少：属性栏「离线」框的占位数，与落字同一个式子
    o = _pt(text="Hi", line_width_pt=1)
    r = client.post("/api/text/outline", json={"objects": [o]}).get_json()
    from tavotto.rendercore import facade

    assert r["results"][0]["auto_offset_pt"] == pytest.approx(
        plan.path_text_auto_offset(o, facade.provider()), abs=1e-4
    )
    assert r["results"][0]["overflow"] is False
    tight = _pt(subpaths=[{"closed": False, "nodes": LINE}], w_mm=6, h_mm=0.5, text="ABCDEFGHIJ")
    r2 = client.post("/api/text/outline", json={"objects": [tight]}).get_json()
    assert r2["results"][0]["overflow"] is True
    t = client.post("/api/text/outline", json={"objects": [{**o, "type": "text"}]}).get_json()
    assert "auto_offset_pt" not in t["results"][0] and "overflow" not in t["results"][0]


def test_copy_as_svg_endpoint_takes_path_text():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    r = client.post(
        "/api/svg/copy", json={"objects": [_pt(x_mm=2, y_mm=2)], "w_mm": 70, "h_mm": 40}
    )
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True and "<svg" in body["svg"]


def test_preview_golden_is_what_the_backend_gives_now(provider):
    """前端画布测试的代答（golden 里的 preview）就是后端此刻的结果：改了落字规则不重新生成，这里先红。"""
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))["preview"]
    now = textoutline.outline_text(g["object"], faces=provider)
    assert json.loads(json.dumps(now)) == g["paths"]
