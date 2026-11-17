"""渐变填充与描边（ADR 0186）的后端一侧。

1. 共享向量 `tests/golden/gradient_vectors.json`：`geometry.read_gradient` / `gradient_paint` 与前端 `lib/gradient` 出同一份数；
2. IR：`validate()` 收得下合法渐变、挡住坏的；`operations_of` 报 gradient_fill / gradient_stroke，色标带 alpha 再报 object_alpha；
3. 三种写入器读回：PDF 的 `/ShadingType 2|3`、`/Coords`、`/Extend`（色标带 alpha 有 luminosity 软蒙版）；SVG 的
   `<linearGradient>` / `<radialGradient>` 与 `url(#…)`；EPS 的 `shfill`（色标带 alpha 照透明拒绝，按不透明导出能写）；
4. PNG 逐像素（PDFium 栅格化 Canonical PDF）：两端等于首尾色、中点是插值；旋转对象、透明组里的描边渐变照样对；
5. 旧文档逐字不变：没有渐变字段的对象出的 IR 与加功能之前同一份 `Paint`。
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from tavotto.rendercore import geometry, ir, plan

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

VECTORS = json.loads((ROOT / "golden" / "gradient_vectors.json").read_text(encoding="utf-8"))
HAS = all(importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL"))
needs_rc = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")

LINEAR = {
    "kind": "linear",
    "stops": [{"offset": 0, "color": "#ff0000"}, {"offset": 1, "color": "#0000ff"}],
    "from": [0, 0.5],
    "to": [1, 0.5],
}
RADIAL = {
    "kind": "radial",
    "stops": [{"offset": 0, "color": "#ffffff"}, {"offset": 1, "color": "#000000"}],
    "center": [0.5, 0.5],
    "radius": 0.5,
}
TRANSLUCENT = {
    **LINEAR,
    "stops": [{"offset": 0, "color": "#ff0000"}, {"offset": 1, "color": "#0000ff", "opacity": 0.4}],
}


# ---------------------------------------------------------------------------
# 1. 共享向量
# ---------------------------------------------------------------------------
def _ir(p):
    if p is None:
        return None
    stops = [[s.offset, *s.rgb, s.alpha] for s in p.stops]
    if isinstance(p, ir.LinearGradient):
        return {"kind": "linear", "stops": stops, "p0": list(p.p0), "p1": list(p.p1)}
    return {"kind": "radial", "stops": stops, "center": list(p.center), "radius": p.radius}


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: json.dumps(c["in"])[:60])
def test_read_and_paint_match_the_shared_vectors(case):
    r = geometry.read_gradient(case["in"])
    if r is not None:
        r = {k: (list(v) if isinstance(v, tuple) else v) for k, v in r.items()}
    assert r == case["read"]
    for p in case["paint"]:
        got = _ir(geometry.gradient_paint(case["in"], p["w"], p["h"], p["alpha"]))
        assert got == pytest.approx(p["out"]) if got is not None else p["out"] is None


def test_the_vector_set_covers_both_kinds_and_rejects():
    reads = [c["read"] for c in VECTORS["cases"]]
    assert {r["kind"] for r in reads if r} == {"linear", "radial"}
    assert sum(r is None for r in reads) >= 5


# ---------------------------------------------------------------------------
# 2. IR
# ---------------------------------------------------------------------------
RECT = (("M", 0, 0), ("L", 10, 0), ("L", 10, 10), ("L", 0, 10), ("Z",))


def _page(*nodes):
    return ir.Page(20, 20, tuple(nodes), {}, None)


def test_validate_accepts_gradients_and_rejects_bad_ones():
    good = geometry.gradient_paint(LINEAR, 10, 10)
    ir.validate(_page(ir.Path(RECT, fill=good)))
    stop = ir.GradientStop(0, (0, 0, 0))
    for bad in (
        ir.LinearGradient((stop,), (0, 0), (1, 0)),  # 一个色标
        ir.LinearGradient(
            (ir.GradientStop(0.6, (0, 0, 0)), ir.GradientStop(0.2, (0, 0, 0))), (0, 0), (1, 0)
        ),
        ir.LinearGradient((stop, ir.GradientStop(1.5, (0, 0, 0))), (0, 0), (1, 0)),
        ir.LinearGradient((stop, ir.GradientStop(1, (2, 0, 0))), (0, 0), (1, 0)),
        ir.LinearGradient((stop, stop), (0, 0), (float("nan"), 0)),
        ir.RadialGradient((stop, stop), (0, 0), 0),
        ir.LinearGradient((stop, stop), (0, 0), (1, 0), spread="repeat"),
    ):
        with pytest.raises(ir.IRError):
            ir.validate(_page(ir.Path(RECT, fill=bad)))


def test_operations_name_gradients_and_their_alpha():
    g = geometry.gradient_paint(LINEAR, 10, 10)
    t = geometry.gradient_paint(TRANSLUCENT, 10, 10)
    assert ir.operations_of(ir.Path(RECT, fill=g)) == ("path_fill", "gradient_fill")
    assert "object_alpha" in ir.operations_of(ir.Path(RECT, fill=t))
    ops = ir.operations_of(ir.Path(RECT, stroke=ir.Stroke(g, 1)))
    assert ops == ("path_stroke", "gradient_stroke")


def test_gradient_follows_fill_and_stroke_switches_and_opacity():
    base = {"shape": "rect", "stroke_pt": 1, "color": "#000000", "fill": "#ff0000"}
    [p] = geometry.shape_paths({**base, "fill_gradient": LINEAR, "fill_opacity": 0.5}, 100, 50)
    assert isinstance(p.fill, ir.LinearGradient)
    assert [s.alpha for s in p.fill.stops] == [0.5, 0.5]
    # 没有填充：渐变不生效（什么都不填）；没有描边：描边渐变不生效
    assert (
        geometry.shape_paths(
            {**base, "fill": None, "stroke": False, "fill_gradient": LINEAR}, 10, 10
        )
        == []
    )
    [p] = geometry.shape_paths({**base, "stroke_gradient": LINEAR, "stroke_opacity": 0.5}, 100, 50)
    assert isinstance(p.stroke.paint, ir.LinearGradient) and p.stroke.paint.stops[0].alpha == 0.5
    # 读不成的渐变：照用纯色
    [p] = geometry.shape_paths({**base, "fill_gradient": {"kind": "linear"}}, 10, 10)
    assert p.fill == ir.Paint((1.0, 0.0, 0.0))


def test_objects_without_gradients_are_unchanged():
    """旧文档逐字不变：没有新字段时出的仍是 `Paint`（对象相等）。"""
    o = {
        "shape": "ellipse",
        "stroke_pt": 2,
        "color": "#224466",
        "fill": "#ffcc00",
        "fill_opacity": 0.5,
    }
    [p] = geometry.shape_paths(o, 40, 30)
    assert p.fill == ir.Paint(ir.hex2rgb("#ffcc00"), 0.5)
    assert p.stroke.paint == ir.Paint(ir.hex2rgb("#224466"))


# ---------------------------------------------------------------------------
# 3. 写入器读回
# ---------------------------------------------------------------------------
def _obj(**over):
    o = {
        "type": "shape",
        "shape": "rect",
        "id": "g",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 50,
        "h_mm": 20,
        "stroke_pt": 1,
        "color": "#000000",
        "fill": "#ff0000",
        "stroke": False,
        "fill_gradient": LINEAR,
    }
    o.update(over)
    return o


def _compiled(*objs):
    return plan.compile_page(70.0, 70.0, list(objs), sources=None, faces=None).page  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def provider():
    if not HAS:
        pytest.skip("RenderCore 依赖未装")
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(fonts.FontRegistry.discover())


def _pdf(page, provider, tmp_path) -> bytes:
    from tavotto.rendercore import pdfwriter

    out = tmp_path / "g.pdf"
    facts = pdfwriter.write_pdf(page, out, provider)
    assert facts.gradients >= 1
    return out.read_bytes()


@needs_rc
def test_pdf_writes_axial_and_radial_shading_patterns(provider, tmp_path):
    raw = _pdf(_compiled(_obj(), _obj(id="r", y_mm=30, fill_gradient=RADIAL)), provider, tmp_path)
    objs = pdfread.objects(raw)
    blob = b"".join(h + (d or b"") for h, d in objs.values())
    assert b"/ShadingType 2" in blob and b"/ShadingType 3" in blob
    assert b"/PatternType 2" in blob and re.search(rb"/Extend \[ ?true true ?\]", blob)
    _head, content = pdfread.page(objs)
    assert re.search(rb"/Pattern cs /P\d+ scn", content)
    assert b"/SMask" not in blob  # 全不透明不建软蒙版


@needs_rc
def test_pdf_stroke_gradient_and_alpha_soft_mask(provider, tmp_path):
    page = _compiled(
        _obj(fill=None, stroke=None, stroke_pt=4, stroke_gradient=TRANSLUCENT, fill_gradient=None)
    )
    raw = _pdf(page, provider, tmp_path)
    objs = pdfread.objects(raw)
    blob = b"".join(h + (d or b"") for h, d in objs.values())
    _head, content = pdfread.page(objs)
    assert re.search(rb"/Pattern CS /P\d+ SCN", content)
    assert b"/Luminosity" in blob and b"/SMask" in blob


@needs_rc
def test_svg_writes_gradient_elements(provider, tmp_path):
    from tavotto.rendercore import svgwriter

    out = tmp_path / "g.svg"
    page = _compiled(
        _obj(),
        _obj(id="r", y_mm=30, fill_gradient=RADIAL, stroke=None, stroke_gradient=TRANSLUCENT),
    )
    svgwriter.write_svg(page, out, provider)
    text = out.read_text(encoding="utf-8")
    assert "<linearGradient" in text and "<radialGradient" in text
    assert 'gradientUnits="userSpaceOnUse"' in text
    assert re.search(r'fill="url\(#tavotto-grad-\d+\)"', text)
    assert re.search(r'stroke="url\(#tavotto-grad-\d+\)"', text)
    assert 'stop-opacity="0.4"' in text


@needs_rc
def test_eps_uses_shfill_and_refuses_translucent_stops_until_flattened(provider):
    from tavotto.engine import epsoverlay
    from tavotto.rendercore import epswriter

    ps, _facts = epswriter.overlay_ps(
        _compiled(_obj(), _obj(id="s", fill=None, stroke=None, stroke_gradient=RADIAL)), provider
    )
    assert ps.count(" shfill") == 2 and "strokepath clip" in ps and "/ShadingType 3" in ps
    page = _compiled(_obj(fill_gradient=TRANSLUCENT))
    with pytest.raises(epswriter.UnsupportedCapability) as ei:
        epswriter.overlay_ps(page, provider)
    assert ei.value.operation == "object_alpha"
    flat, _ = epswriter.overlay_ps(epswriter.flatten_alpha(page), provider)
    assert " shfill" in flat
    # 外层 EPS：叠加层里有 shfill 才声明 Level 3，没有时与从前逐字节相同
    inner = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%EndComments\nshowpage\n"
    with_grad = epsoverlay.compose(inner, flat, 10, 10)
    without = epsoverlay.compose(inner, "newpath", 10, 10)
    assert b"%%LanguageLevel: 3" in with_grad and b"%%LanguageLevel: 2" in without


# ---------------------------------------------------------------------------
# 4. PNG 逐像素
# ---------------------------------------------------------------------------
def _render(page, provider, tmp_path, scale=4):
    pdfium = pytest.importorskip("pypdfium2")
    raw = _pdf(page, provider, tmp_path)
    path = tmp_path / "r.pdf"
    path.write_bytes(raw)
    img = pdfium.PdfDocument(str(path))[0].render(scale=scale).to_pil().convert("RGB")
    k = scale * 72 / 25.4
    return lambda x_mm, y_mm: img.getpixel((int(x_mm * k), int(y_mm * k)))


def _near(got, want, tol=3):
    assert all(abs(a - b) <= tol for a, b in zip(got, want)), (got, want)


@needs_rc
def test_png_pixels_follow_the_stops(provider, tmp_path):
    px = _render(
        _compiled(_obj(), _obj(id="r", x_mm=5, y_mm=35, w_mm=30, h_mm=30, fill_gradient=RADIAL)),
        provider,
        tmp_path,
    )
    _near(px(5.2, 15), (255, 0, 0), 6)
    _near(px(54.8, 15), (0, 0, 255), 6)
    _near(px(30, 15), (128, 0, 128), 4)
    _near(px(20, 50), (255, 255, 255), 6)  # 径向圆心 = 首色
    _near(px(20, 35.3), (0, 0, 0), 12)  # 半径处 = 尾色


@needs_rc
def test_png_rotated_object_and_stroke_gradient_in_a_transparency_group(provider, tmp_path):
    """旋转 90°：渐变跟着对象转（框空间里从左到右 = 页面上从上到下）。带端型的路径描边进透明组（Form XObject），
    Pattern 的矩阵要相对 form 的空间——算错的话颜色会整体错位。"""
    rot = _obj(x_mm=10, y_mm=10, w_mm=40, h_mm=40, rotation_deg=90)
    path = {
        "type": "path",
        "id": "p",
        "x_mm": 5,
        "y_mm": 60,
        "w_mm": 60,
        "h_mm": 0.01,
        "stroke_pt": 8,
        "color": "#000000",
        "fill": None,
        "stroke_opacity": 0.999,
        "head_end": "triangle",
        "stroke_gradient": LINEAR,
        "subpaths": [{"closed": False, "nodes": [{"p": [0, 0.5]}, {"p": [1, 0.5]}]}],
    }
    px = _render(_compiled(rot, path), provider, tmp_path)
    top, bottom = px(30, 10.5), px(30, 49.5)
    assert top[0] > 200 and top[2] < 60, top
    assert bottom[2] > 200 and bottom[0] < 60, bottom
    left, mid = px(6, 60), px(30, 60)
    assert left[0] > 200 and left[2] < 60, left
    assert abs(mid[0] - mid[2]) < 60, mid


@needs_rc
def test_png_gradient_inside_a_clip_union_group(provider, tmp_path):
    """剪切并集（ClipUnion，SVG 的多成员 clipPath）的内容画进单独的 Form：里面的渐变 Pattern 矩阵也要相对
    form 的空间（Do 那一刻的 CTM）——算错的话颜色整体错位。外面再套一层平移，量的是同一个矩形的两端与中点。"""
    page = _compiled(_obj())

    def rect(x: float, w: float) -> ir.Path:
        return ir.Path((("M", x, 0), ("L", x + w, 0), ("L", x + w, 200), ("L", x, 200), ("Z",)))

    union = ir.Group(page.children, clip=ir.ClipUnion((rect(0, 80), rect(80, 200))))
    shifted = ir.Group((union,), transform=ir.translate(10 * 72 / 25.4, 0))  # 整体右移 10 mm
    px = _render(dataclasses.replace(page, children=(shifted,)), provider, tmp_path)
    _near(px(15.2, 15), (255, 0, 0), 6)
    _near(px(64.8, 15), (0, 0, 255), 6)
    _near(px(40, 15), (128, 0, 128), 4)
