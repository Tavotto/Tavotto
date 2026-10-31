"""QA-WIN-20：scope=original 带画布标注（`original.objects`）。

标注由整版导出那条 RenderCore 合成路径画（没有第二套绘制）；每个格式的输出里都要有图、也要有标注文字；
位图源保持源像素网格；没有标注时请求与输出与从前逐字节相同。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exportreq, pdfeps

W_MM, H_MM = 200 * 25.4 / 72, 150 * 25.4 / 72  # = 源 PDF 页面，缩放恰为 1
GS = Path("/usr/local/bin/gs")


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=None, fill=(0.1, 0.2, 0.8))
    page.insert_text((20, 120), "PanelText", fontsize=11)
    doc.save(figs / "p1.pdf")
    doc.close()
    raster = pymupdf.open()
    rp = raster.new_page(width=120, height=80)
    rp.draw_rect(pymupdf.Rect(0, 0, 60, 80), color=None, fill=(0, 0, 0))
    rp.get_pixmap(alpha=False).save(figs / "r1.png")
    raster.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.delenv("TAVOTTO_GHOSTSCRIPT", raising=False)
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs


def _label(x=30.0, y=40.0):
    return {
        "type": "text",
        "id": "t1",
        "text": "Label QA",
        "x_mm": x,
        "y_mm": y,
        "w_mm": 30,
        "h_mm": 6,
        "size_pt": 12,
    }


def _vector(formats, objects=None, **over):
    original = {"figure_id": "p1.pdf", "w_mm": W_MM, "h_mm": H_MM, "source_kind": "vector"}
    if objects is not None:
        original["objects"] = objects
    return {
        "scope": "original",
        "filename": "Fig",
        "formats": formats,
        "original": original,
        **over,
    }


def _raster(formats, objects=None, **over):
    original = {
        "figure_id": "r1.png",
        "w_mm": 10.16,
        "h_mm": 6.77,
        "px_w": 120,
        "px_h": 80,
        "source_kind": "raster",
    }
    if objects is not None:
        original["objects"] = objects
    return {"scope": "original", "filename": "R", "formats": formats, "original": original, **over}


def _post(client, spec):
    resp = client.post("/api/export", json=spec)
    return resp.status_code, resp.get_json()


def _out(body, fmt):
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _path(body, fmt) -> Path:
    return Path(body["export_dir"]) / _out(body, fmt)["name"]


def _pdf_text(path: Path) -> str:
    with pymupdf.open(path) as d:
        return d[0].get_text()


def _diff_region(a: Path, b: Path):
    """两张位图差异像素的包围盒（None = 完全相同）。"""
    pa, pb = pymupdf.Pixmap(str(a)), pymupdf.Pixmap(str(b))
    assert (pa.width, pa.height) == (pb.width, pb.height)
    xs, ys = [], []
    n = pa.n
    for y in range(pa.height):
        ra, rb = y * pa.stride, y * pb.stride
        for x in range(pa.width):
            if pa.samples[ra + x * n : ra + x * n + n] != pb.samples[rb + x * n : rb + x * n + n]:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def test_pdf_has_figure_and_annotation_at_figure_size(env):
    client, _ = env
    status, body = _post(client, _vector(["pdf"], [_label()]))
    assert status == 200, body
    out = _path(body, "pdf")
    text = _pdf_text(out)
    assert "Label QA" in text and "PanelText" in text
    with pymupdf.open(out) as d:
        r = d[0].rect
    assert (r.width, r.height) == pytest.approx((200, 150), abs=0.1)


def test_png_vector_source_has_figure_and_annotation_ink_only_where_placed(env):
    client, _ = env
    _, plain = _post(client, _vector(["png"], None, ppi=150, filename="plain"))
    _, annot = _post(client, _vector(["png"], [_label()], ppi=150, filename="annot"))
    a, b = _path(plain, "png"), _path(annot, "png")
    pa = pymupdf.Pixmap(str(a))
    assert _out(annot, "png")["dimensions"]["px"] == [pa.width, pa.height]
    box = _diff_region(a, b)
    assert box is not None, "标注没画进 PNG"
    scale = 150 / 25.4
    # 差异只在标注框（x 30–60 mm、y 40–46 mm）附近，图其余部分与无标注时一致
    assert box[0] >= 29 * scale and box[2] <= 61 * scale
    assert box[1] >= 39 * scale and box[3] <= 47 * scale


def test_png_and_tiff_raster_source_keep_source_pixel_grid(env):
    client, _ = env
    _, body = _post(client, _raster(["png", "tiff"], [_label(1.0, 2.0)]))
    for fmt in ("png", "tiff"):
        path = _path(body, fmt)
        pix = pymupdf.Pixmap(str(path))
        assert (pix.width, pix.height) == (120, 80), fmt
        assert _out(body, fmt)["dimensions"]["px"] == [120, 80]
    _, plain = _post(client, _raster(["png"], None, filename="plain"))
    assert _diff_region(_path(plain, "png"), _path(body, "png")) is not None


def test_svg_capability_matches_the_canvas_path(env):
    """SVG 能力与整版一致：同一个源，整版给不出就给出同一个稳定码；给得出就带标注文字。"""
    client, _ = env
    canvas = {
        "scope": "canvas",
        "filename": "C",
        "formats": ["svg"],
        "canvas": {
            "page_w_mm": W_MM,
            "page_h_mm": H_MM,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 0, "y_mm": 0, "w_mm": W_MM, "h_mm": H_MM},
                _label(),
            ],
        },
    }
    _, ref = _post(client, canvas)
    _, got = _post(client, _vector(["svg"], [_label()]))
    ref_svg, got_svg = _out(ref, "svg"), _out(got, "svg")
    assert got_svg["status"] == ref_svg["status"]
    if ref_svg["status"] == "done":
        assert "Label QA" in _path(got, "svg").read_text(encoding="utf-8")
    else:
        assert got_svg["error"]["code"] == ref_svg["error"]["code"]


def test_eps_without_converter_fails_only_that_item_and_never_drops_annotations(env):
    client, _ = env
    status, body = _post(client, _vector(["pdf", "eps"], [_label()]))
    assert status == 200, body
    assert _out(body, "eps")["status"] != "done"
    assert _out(body, "eps")["error"]["code"] == "eps_annotations_need_converter"
    assert "Label QA" in _pdf_text(_path(body, "pdf"))  # 其余格式照常交付


def test_eps_converter_is_fed_the_same_annotated_pdf(env, monkeypatch):
    client, _ = env
    seen = {}

    def fake(pdf: Path, out: Path) -> bool:
        seen["text"] = _pdf_text(pdf)
        out.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 200 150\n%%EOF\n")
        return True

    monkeypatch.setattr(pdfeps, "available", lambda: True)
    monkeypatch.setattr(pdfeps, "pdf_to_eps", fake)
    _, body = _post(client, _vector(["eps"], [_label()]))
    assert _out(body, "eps")["status"] == "done", body
    assert "Label QA" in seen["text"] and "PanelText" in seen["text"]


@pytest.mark.skipif(not GS.is_file(), reason="本机没有 /usr/local/bin/gs")
def test_eps_real_ghostscript_conversion(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("TAVOTTO_GHOSTSCRIPT", str(GS))
    _, body = _post(client, _vector(["eps"], [_label()]))
    assert _out(body, "eps")["status"] == "done", body
    data = _path(body, "eps").read_bytes()
    assert data.startswith(b"%!PS-Adobe-") and b"EPSF" in data[:64]
    w, h = _out(body, "eps")["dimensions"]["mm"]
    assert (round(w, 0), round(h, 0)) == (round(W_MM, 0), round(H_MM, 0))
    # 栅格化后裹一层 PS 的冒牌矢量不行：真矢量里不会有 image 操作符
    assert b"colorimage" not in data and b"%%BeginBinary" not in data


def test_no_objects_requests_are_byte_identical(env):
    """没有标注（缺键 / 空列表）：走旧分支，请求语义与输出逐字节相同。"""
    client, figs = env
    _, a = _post(client, _vector(["png"], None, ppi=150, filename="a"))
    _, b = _post(client, _vector(["png"], [], ppi=150, filename="b"))
    assert _path(a, "png").read_bytes() == _path(b, "png").read_bytes()
    _, c = _post(client, _raster(["png"], []))
    assert _path(c, "png").read_bytes() == (figs / "r1.png").read_bytes()


def test_request_parsing_rules():
    base = {"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5}
    src = exportreq._original_source(base)
    assert src.objects == ()
    assert exportreq._original_source({**base, "objects": []}).objects == ()
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq._original_source({**base, "objects": [{"type": "panel", "id": "x"}]})
    assert e.value.code == "bad_objects"
    with pytest.raises(exportreq.ExportRequestError):
        exportreq._original_source({"figure_id": "p1.pdf", "objects": [_label()]})  # 缺图幅
