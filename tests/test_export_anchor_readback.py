"""导出产物回读：面板在画布上的锚点 / crop / 页面盒 / 位图密度，从**产物字节**量，不信回执。

QA 2026-09-24 §5 SCI-06。`tests/test_export_pipeline.py` 已钉住页面尺寸、原图不套画布缩放、TIFF ≡ PNG、
透明背景；那里的像素数多半取自回执的 `dimensions`（服务端自报）。这里补的是「对象落在哪」：
一个已知位置的黑色方块，经画布摆放（含 crop）之后，在导出的 PDF / PNG / TIFF 里是不是落在按
**测试侧独立换算**（mm → px，不调生产坐标函数）得出的矩形上。

独立性：PDF 用 PyMuPDF 栅格（与 RenderCore 的 PDFium render child 不同源）、PNG / TIFF 用 Pillow 解码
（与 `tiffwrite` / RenderCore 编码器不同源）、页面盒用纯标准库的 `tests/support/pdfread.py`。

几何真值（源 200×100 pt，方块在顶原点 (20,20)–(80,80) pt）：
  * 整页摆进 60×30 mm、左上角 (30,20) mm → 方块 x∈[36,54] mm、y∈[26,44] mm；
  * crop = 左半 (0,0,0.5,1) 摆进 30×30 mm、左上角 (30,20) mm → 同一个矩形。
容差 ±2 px（300 ppi 下 ≈0.17 mm），抗锯齿边只落在一个像素内。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from support import pdfread
from tavotto import app as m
from tavotto.engine import exportjob

PPI = 300
PAGE_MM = (150.0, 100.0)
BOX_MM = (36.0, 26.0, 54.0, 44.0)  # x0, y0, x1, y1（顶原点）
TOL_PX = 2


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    page.draw_rect(pymupdf.Rect(20, 20, 80, 80), color=None, fill=(0, 0, 0))
    doc.save(figs / "src.pdf")
    doc.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    yield m.app.test_client()
    m.reset_projects()


def _panel(**over):
    return {
        "type": "panel",
        "id": "src.pdf",
        "x_mm": 30,
        "y_mm": 20,
        "w_mm": 60,
        "h_mm": 30,
        **over,
    }


def _export(client, name, panel, formats, background="white"):
    resp = client.post(
        "/api/export",
        json={
            "scope": "canvas",
            "filename": name,
            "formats": formats,
            "ppi": PPI,
            "background": background,
            "canvas": {"page_w_mm": PAGE_MM[0], "page_h_mm": PAGE_MM[1], "objects": [panel]},
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["status"] == "done", body
    out = Path(body["export_dir"])
    return {o["format"]: out / o["name"] for o in body["outputs"]}


def _px(mm: float) -> float:
    return mm / 25.4 * PPI


def _ink_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """深色像素（亮度 < 128 且不透明）的外接框，PIL 的 (x0, y0, x1, y1) 半开区间。"""
    rgba = img.convert("RGBA")
    mask = Image.eval(rgba.convert("L"), lambda v: 255 if v < 128 else 0)
    alpha = rgba.getchannel("A").point(lambda a: 255 if a > 128 else 0)
    box = Image.composite(mask, Image.new("L", rgba.size, 0), alpha).getbbox()
    assert box is not None, "产物里一个深色像素都没有——方块没画出来"
    return box


def _assert_box(box, what: str) -> None:
    want = tuple(_px(v) for v in BOX_MM)
    for got, exp, edge in zip(box, want, ("x0", "y0", "x1", "y1")):
        assert abs(got - exp) <= TOL_PX, f"{what} 的 {edge}：产物 {got}px，独立换算 {exp:.1f}px"


@pytest.mark.parametrize(
    "panel",
    [_panel(), _panel(w_mm=30, crop={"x": 0, "y": 0, "w": 0.5, "h": 1})],
    ids=["whole-page", "left-half-crop"],
)
def test_panel_anchor_lands_where_the_canvas_put_it_in_every_format(env, panel):
    files = _export(env, "Anchor", panel, ["pdf", "png", "tiff"])

    # PDF：页面盒（纯标准库读取）+ PyMuPDF 独立栅格化后的方块位置
    raw = files["pdf"].read_bytes()
    with pymupdf.open(stream=raw, filetype="pdf") as doc:
        assert doc.page_count == 1
        rect = doc[0].rect
        assert rect.width == pytest.approx(PAGE_MM[0] / 25.4 * 72, abs=0.05)
        assert rect.height == pytest.approx(PAGE_MM[1] / 25.4 * 72, abs=0.05)
        pix = doc[0].get_pixmap(dpi=PPI, alpha=False)
        pdf_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    _assert_box(_ink_bbox(pdf_img), "PDF（PyMuPDF 栅格）")

    # PNG / TIFF：Pillow 解码，尺寸 = 页面 mm × ppi，密度标签 = ppi，方块位置一致
    want_size = (round(_px(PAGE_MM[0])), round(_px(PAGE_MM[1])))
    for fmt in ("png", "tiff"):
        with Image.open(files[fmt]) as img:
            img.load()
            assert img.size == want_size, (fmt, img.size, want_size)
            dpi = img.info.get("dpi")
            assert dpi is not None and [round(float(d)) for d in dpi] == [PPI, PPI], (fmt, dpi)
            _assert_box(_ink_bbox(img), fmt.upper())


def test_transparent_background_is_transparent_outside_the_box_and_opaque_inside(env):
    files = _export(env, "Clear", _panel(), ["png", "tiff"], background="transparent")
    cx, cy = round(_px(45)), round(_px(35))  # 方块中心
    for fmt in ("png", "tiff"):
        with Image.open(files[fmt]) as img:
            rgba = img.convert("RGBA")
            assert img.mode in ("RGBA", "LA"), (fmt, img.mode)
            assert rgba.getpixel((2, 2))[3] == 0, f"{fmt} 页面空白处不透明"
            r, g, b, a = rgba.getpixel((cx, cy))
            assert a == 255 and max(r, g, b) < 40, (fmt, (r, g, b, a))


def test_page_box_is_read_by_a_reader_that_shares_no_code_with_the_writer(env):
    """pdfread（纯标准库）量页面盒：与 PyMuPDF 各量一次，两把尺子同一个答案。"""
    files = _export(env, "Box", _panel(), ["pdf"])
    head, _content = pdfread.page(pdfread.objects(files["pdf"].read_bytes()))
    x0, y0, x1, y1 = pdfread.media_box(head)
    assert (x1 - x0) == pytest.approx(PAGE_MM[0] / 25.4 * 72, abs=0.05)
    assert (y1 - y0) == pytest.approx(PAGE_MM[1] / 25.4 * 72, abs=0.05)
