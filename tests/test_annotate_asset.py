"""写回原图携带画布标注：pdfbackend.annotate_asset 的矢量与栅格保真。

标注坐标是**该图自身的 mm**；PDF 上必须是真矢量（get_text/get_drawings 可见），
PNG 由注好的 PDF 重新栅格化——两种载体同源。绘制函数与导出合成同一套
（_draw_text/_draw_arrow/_draw_shape），这里只看护 annotate_asset 的编排。
"""
import pymupdf

from tavotto import pdfbackend

MM = 72 / 25.4


def _make_pdf(path, w_mm=100, h_mm=80):
    doc = pymupdf.open()
    page = doc.new_page(width=w_mm * MM, height=h_mm * MM)
    page.draw_rect(pymupdf.Rect(10, 10, 40, 40), color=(0, 0, 0))
    doc.save(path)
    doc.close()


def test_annotate_pdf_draws_vector_annotations(tmp_path):
    pdf = tmp_path / "fig.pdf"
    _make_pdf(pdf)
    pdfbackend.annotate_asset(pdf, None, [
        {"type": "text", "x_mm": 10, "y_mm": 10, "w_mm": 60, "h_mm": 8,
         "text": "baked note", "size_pt": 9, "bold": False,
         "color": "#000000", "align": "left"},
        {"type": "shape", "shape": "rect", "x_mm": 20, "y_mm": 30,
         "w_mm": 30, "h_mm": 20, "stroke_pt": 1, "color": "#000000",
         "fill": None},
        {"type": "arrow", "x_mm": 5, "y_mm": 60, "w_mm": 40, "h_mm": 10,
         "start": {"rx": 0, "ry": 0.5}, "end": {"rx": 1, "ry": 0.5},
         "stroke_pt": 1, "color": "#000000", "head": "end"},
    ])
    with pymupdf.open(pdf) as doc:
        assert "baked note" in doc[0].get_text()   # 矢量文字，不是位图
        # 原有内容 + 矩形 + 箭头（线 + 箭头帽）都在矢量层
        assert len(doc[0].get_drawings()) >= 3


def test_annotate_regenerates_png_from_annotated_pdf(tmp_path):
    pdf = tmp_path / "fig.pdf"
    png = tmp_path / "fig.png"
    _make_pdf(pdf)
    # 一张空白 PNG 占位（写回流程里是 worker 导出的旧图）
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False).save(png)
    pdfbackend.annotate_asset(pdf, png, [
        {"type": "shape", "shape": "rect", "x_mm": 20, "y_mm": 30,
         "w_mm": 30, "h_mm": 20, "stroke_pt": 2, "color": "#000000",
         "fill": "#000000"},
    ], dpi=150)
    pix = pymupdf.Pixmap(str(png))
    # PNG 已换成注好的 PDF 的栅格：尺寸按 150dpi、且不再是 10×10 的占位图
    assert pix.width == round(100 * MM * 150 / 72)
    # 填充矩形处确实有墨（中心点取样）
    cx = round((20 + 15) / 100 * pix.width)
    cy = round((30 + 10) / 80 * pix.height)
    r, g, b = pix.pixel(cx, cy)[:3]
    assert r + g + b < 200, (r, g, b)
