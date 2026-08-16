"""/api/export 端到端：页面尺寸、矢量文字保真、hidden 过滤、旧契约兼容。

按 CLAUDE.md 的验证约定，导出 PDF 用 pymupdf get_text() 验证矢量文字。
"""
import pymupdf
import pytest

from magplot import app as m
from magplot.pdfbackend import pymupdf_backend as pb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    m.app.config["TESTING"] = True
    return m.app.test_client()


def _text_obj(text, **kw):
    return {"type": "text", "text": text, "x_mm": 10, "y_mm": 10,
            "w_mm": 80, "h_mm": 10, "size_pt": 9, **kw}


def _export(client, tmp_path, spec):
    resp = client.post("/api/export", json={"page_w_mm": 100, "page_h_mm": 50,
                                            "formats": ["pdf"], **spec})
    assert resp.status_code == 200, resp.get_json()
    files = resp.get_json()["files"]
    assert len(files) == 1
    return pymupdf.open(tmp_path / files[0]["name"])


def _asym_panel_dir(tmp_path):
    """左半有内容、右半空白的不对称面板，翻转与否肉眼（像素）可辨。"""
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=100, height=50)
    page.draw_rect(pymupdf.Rect(5, 5, 45, 45), color=None, fill=(0, 0, 0))
    doc.save(figs / "asym.pdf")
    doc.close()
    return figs


def _ink_halves(doc):
    """导出 PDF 渲染后左右两半的墨量（像素值越小越黑）。"""
    pix = doc[0].get_pixmap()
    w, h, n = pix.width, pix.height, pix.n
    s = pix.samples
    left = right = 0
    for y in range(h):
        row = y * pix.stride
        for x in range(w):
            v = s[row + x * n]
            if x < w // 2:
                left += 255 - v
            else:
                right += 255 - v
    return left, right


def test_export_panel_flip_h(client, tmp_path, monkeypatch):
    """水平翻转：墨量从左半移到右半；不翻转的对照在左半。"""
    monkeypatch.setattr(m, "FIGURES_DIR", _asym_panel_dir(tmp_path))
    panel = {"type": "panel", "id": "asym.pdf",
             "x_mm": 0, "y_mm": 0, "w_mm": 100, "h_mm": 50}
    plain = _export(client, tmp_path, {"stem": "plain", "objects": [panel]})
    flipped = _export(client, tmp_path,
                      {"stem": "flip", "objects": [{**panel, "flip_h": True}]})
    l0, r0 = _ink_halves(plain)
    l1, r1 = _ink_halves(flipped)
    assert l0 > r0 * 3, (l0, r0)   # 原图墨在左
    assert r1 > l1 * 3, (l1, r1)   # 翻转后墨在右


def test_export_panel_flip_v_keeps_horizontal(client, tmp_path, monkeypatch):
    """垂直翻转不影响左右分布（内容仍在左半）。"""
    monkeypatch.setattr(m, "FIGURES_DIR", _asym_panel_dir(tmp_path))
    panel = {"type": "panel", "id": "asym.pdf",
             "x_mm": 0, "y_mm": 0, "w_mm": 100, "h_mm": 50, "flip_v": True}
    doc = _export(client, tmp_path, {"stem": "flipv", "objects": [panel]})
    left, right = _ink_halves(doc)
    assert left > right * 3, (left, right)


def test_export_vector_text_and_page_size(client, tmp_path):
    doc = _export(client, tmp_path, {
        "stem": "t", "objects": [_text_obj("Hello 等离子体")]})
    page = doc[0]
    assert page.rect.width == pytest.approx(pb.mm2pt(100), abs=0.1)
    assert page.rect.height == pytest.approx(pb.mm2pt(50), abs=0.1)
    text = page.get_text()
    assert "Hello" in text and "等离子体" in text  # 真矢量文字，非位图


def test_export_italic_text_embeds_italic_font(client, tmp_path):
    doc = _export(client, tmp_path, {
        "stem": "t", "objects": [_text_obj("slanted", italic=True)]})
    fonts = {span["font"]
             for block in doc[0].get_text("rawdict")["blocks"]
             for line in block.get("lines", [])
             for span in line.get("spans", [])}
    assert any("Italic" in f for f in fonts)


def test_export_skips_hidden_objects(client, tmp_path):
    doc = _export(client, tmp_path, {"stem": "t", "objects": [
        _text_obj("visible"),
        {**_text_obj("SECRET"), "hidden": True, "y_mm": 30}]})
    text = doc[0].get_text()
    assert "visible" in text and "SECRET" not in text


def test_export_legacy_items_texts_contract(client, tmp_path):
    """旧 bundle 标签页仍发 items[]+texts[]，必须继续可用。"""
    doc = _export(client, tmp_path, {
        "stem": "t", "objects": None,
        "items": [], "texts": [_text_obj("legacy")]})
    assert "legacy" in doc[0].get_text()


def test_export_shape_and_arrow_draw_vector_paths(client, tmp_path):
    doc = _export(client, tmp_path, {"stem": "t", "objects": [
        {"type": "shape", "shape": "rect", "x_mm": 5, "y_mm": 5,
         "w_mm": 20, "h_mm": 10, "stroke_pt": 1, "color": "#ff0000", "fill": None},
        {"type": "arrow", "x_mm": 30, "y_mm": 5, "w_mm": 20, "h_mm": 10,
         "start": {"rx": 0, "ry": 0}, "end": {"rx": 1, "ry": 1},
         "stroke_pt": 1, "color": "#000000", "head": "end"}]})
    # 白底矩形 + rect 描边 + 箭头干线 + 箭头帽
    assert len(doc[0].get_drawings()) >= 4


def test_export_stem_sanitized(client, tmp_path):
    resp = client.post("/api/export", json={
        "page_w_mm": 50, "page_h_mm": 50, "formats": ["pdf"],
        "stem": "图9/../x", "objects": []})
    name = resp.get_json()["files"][0]["name"]
    assert "/" not in name and ".." not in name
    assert name.startswith("图9_")
