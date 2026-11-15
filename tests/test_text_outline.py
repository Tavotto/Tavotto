"""文字转轮廓（ADR 0189，`rendercore/textoutline.py`）。

同源的判据是**像素**：同一个文字对象，按文字导出（PDF 里嵌字体）与转成路径后按路径导出，PDFium 栅格化出来逐像素几乎一样
（只差抗锯齿）——字形、字距、旋转、模拟斜体 / 粗体、下划线、背景框都与导出同一份。另核：颜色 / 模拟粗体分组、空文字、接口。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tavotto.rendercore import plan, textoutline

HAS = all(
    importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL", "pypdfium2")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(fonts.FontRegistry.discover())


def _text(**over):
    o = {
        "type": "text",
        "id": "t",
        "x_mm": 6,
        "y_mm": 8,
        "w_mm": 50,
        "h_mm": 10,
        "text": "Hag Ωμ 中文",
        "size_pt": 14,
        "bold": False,
        "italic": False,
        "color": "#1f4e9a",
        "align": "left",
    }
    o.update(over)
    return o


def _as_export(p: dict) -> dict:
    """路径对象（画布字段）→ 导出载荷（与前端 `exportPayload` 同名同义）。"""
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
    if p.get("lineJoin"):
        out["line_join"] = p["lineJoin"]
    if p.get("lineCap"):
        out["line_cap"] = p["lineCap"]
    return out


def _raster(objs, provider, tmp_path, name, scale=6):
    import pypdfium2 as pdfium

    from tavotto.rendercore import pdfwriter

    page = plan.compile_page(70, 40, objs, sources=None, faces=provider).page  # type: ignore[arg-type]
    out = tmp_path / f"{name}.pdf"
    pdfwriter.write_pdf(page, out, provider)
    return pdfium.PdfDocument(str(out))[0].render(scale=scale).to_pil().convert("L")


def _diff(a, b) -> tuple[float, int]:
    from PIL import ImageChops

    d = ImageChops.difference(a, b)
    hist = d.histogram()
    total = sum(hist)
    mean = sum(i * n for i, n in enumerate(hist)) / total
    big = sum(n for i, n in enumerate(hist) if i > 96)
    return mean, big


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"rotation_deg": 25},
        {"italic": True, "bold": True},
        {"underline": True, "bg": "#ffeeaa", "border_color": "#333333", "border_pt": 1},
        {"align": "center", "text": "x² + y² = r²"},
        # 竖排里横躺的拉丁字是对象组里再套一层旋转组：两层变换的先后不能反
        {"writing_mode": "vertical", "text": "中文AB", "rotation_deg": 20, "w_mm": 10, "h_mm": 28},
    ],
    ids=["plain", "rotated", "bold-italic", "underline-box", "superscript", "vertical-rotated"],
)
def test_outline_renders_like_the_exported_text(provider, tmp_path, over):
    t = _text(**over)
    paths = textoutline.outline_text(t, faces=provider)
    assert paths and all(p["type"] == "path" for p in paths)
    a = _raster([t], provider, tmp_path, "text")
    b = _raster([_as_export(p) for p in paths], provider, tmp_path, "paths")
    mean, big = _diff(a, b)
    # 只差抗锯齿：平均差 < 1 / 255，差得多的像素是边缘上的零星几个
    assert mean < 1.0, mean
    assert big < a.size[0] * a.size[1] * 0.0005, big


def test_glyphs_merge_into_one_compound_path_per_paint(provider):
    paths = textoutline.outline_text(_text(text="ABC"), faces=provider)
    assert len(paths) == 1
    p = paths[0]
    assert p["fill"] == "#1f4e9a" and p["stroke"] is False
    assert len(p["subpaths"]) >= 4  # A 有内孔、B 两个内孔


def test_synthetic_bold_is_a_same_colour_stroke_and_renders_like_export(
    provider, tmp_path, monkeypatch
):
    # 内置字体都有真粗体：把「这张脸要模拟粗体」强行打开，看转出来的路径与导出的模拟粗体同一画法
    monkeypatch.setattr(plan, "_synth_of", lambda face: {"synth_bold": True, "synth_italic": False})
    t = _text(text="Hag", bold=True)
    paths = textoutline.outline_text(t, faces=provider)
    (p,) = paths
    assert "stroke" not in p  # 描边开着（路径缺省是圆角拐角，与模拟粗体同一种）
    assert p["color"] == p["fill"] == "#1f4e9a"
    assert p["strokePt"] > 0 and "lineJoin" not in p
    a = _raster([t], provider, tmp_path, "text")
    b = _raster([_as_export(p)], provider, tmp_path, "paths")
    mean, _big = _diff(a, b)
    assert mean < 1.0, mean


def test_empty_text_gives_nothing(provider):
    assert textoutline.outline_text(_text(text=""), faces=provider) == []


def test_non_text_is_refused(provider):
    with pytest.raises(plan.PlanError):
        textoutline.outline_text({"type": "shape"}, faces=provider)


def test_endpoint():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    r = client.post("/api/text/outline", json={"objects": [_text(text="Hi")]})
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["results"][0]["paths"][0]["type"] == "path"
    assert (
        client.post("/api/text/outline", json={"objects": [{"type": "shape"}]}).status_code == 400
    )
    assert client.post("/api/text/outline", json={}).status_code == 400


def test_copy_as_svg_endpoint_round_trips_through_the_importer():
    """复制为 SVG 写出来的 SVG 再粘贴回来（`svgimport`）：没有做不准的东西，形状与文字都还在。"""
    from tavotto import app as m
    from tavotto.rendercore import svgimport

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    rect = {
        "type": "shape",
        "shape": "rect",
        "id": "r",
        "x_mm": 1,
        "y_mm": 1,
        "w_mm": 20,
        "h_mm": 10,
        "stroke_pt": 1,
        "color": "#000000",
        "fill": "#ff0000",
    }
    r = client.post(
        "/api/svg/copy",
        json={"objects": [rect, _text(x_mm=2, y_mm=14, text="Hi")], "w_mm": 60, "h_mm": 30},
    )
    body = r.get_json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["svg"].lstrip().startswith(("<?xml", "<svg"))
    back = svgimport.import_svg(body["svg"])
    assert back["size_mm"] == pytest.approx([60, 30], abs=0.01)
    assert back["unsupported"] == {} or set(back["unsupported"]) <= {"external_ref"}
    assert any(o["type"] == "path" and o.get("fill") == "#ff0000" for o in back["objects"])
    # 面板不收
    bad = client.post(
        "/api/svg/copy", json={"objects": [{"type": "panel"}], "w_mm": 10, "h_mm": 10}
    )
    assert bad.status_code == 400


GOLDEN = json.loads(
    (Path(__file__).resolve().parent / "golden" / "text_outline.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=[c["name"] for c in GOLDEN["cases"]])
def test_matches_the_shared_snapshot(provider, case):
    got = textoutline.outline_text(case["text"], faces=provider)
    assert _close(json.loads(json.dumps(got)), case["paths"])


def _close(a, b) -> bool:
    if isinstance(a, dict):
        assert set(a) == set(b), set(a) ^ set(b)
        return all(_close(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        assert len(a) == len(b)
        return all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, float):
        assert a == pytest.approx(b, abs=1e-6)
        return True
    assert a == b
    return True
