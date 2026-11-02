"""ADR 0161 §3：`original.pad_mm`——单图留白（正）/ 裁切（负）。

缺席或全 0 = 请求与输出与从前逐字节相同；非零 = 走「原图 + 标注」那条 RenderCore 合成路径（没有标注也走）：
页面 = 图幅 + 留白，面板落在 `(l, t)`；位图源的像素网格、EPS 内嵌偏移、产物验收的尺寸都按页面算。
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exportreq

PT = 25.4 / 72
W_MM, H_MM = 200 * PT, 150 * PT  # 源 PDF 页面，缩放恰为 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=150)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=None, fill=(0.1, 0.2, 0.8))
    doc.save(figs / "p1.pdf")
    doc.close()
    raster = pymupdf.open()
    rp = raster.new_page(width=120, height=80)
    rp.draw_rect(pymupdf.Rect(0, 0, 60, 80), color=None, fill=(0, 0, 0))
    rp.get_pixmap(alpha=False).save(figs / "r1.png")
    raster.close()
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs


def _vector(formats, pad=None, **over):
    original = {"figure_id": "p1.pdf", "w_mm": W_MM, "h_mm": H_MM, "source_kind": "vector"}
    if pad is not None:
        original["pad_mm"] = pad
    return {
        "scope": "original",
        "filename": "Fig",
        "formats": formats,
        "original": original,
        **over,
    }


def _raster(formats, pad=None):
    original = {
        "figure_id": "r1.png",
        "w_mm": 10.16,
        "h_mm": 6.77,
        "px_w": 120,
        "px_h": 80,
        "source_kind": "raster",
    }
    if pad is not None:
        original["pad_mm"] = pad
    return {"scope": "original", "filename": "R", "formats": formats, "original": original}


def _post(client, spec):
    resp = client.post("/api/export", json=spec)
    return resp.status_code, resp.get_json()


def _out(body, fmt):
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _path(body, fmt) -> Path:
    return Path(body["export_dir"]) / _out(body, fmt)["name"]


def _blue_rect(pdf: Path):
    with pymupdf.open(pdf) as d:
        rects = [
            x["rect"]
            for x in d[0].get_drawings()
            if x.get("fill") and abs(x["fill"][0] - 0.1) < 0.02 and x["fill"][2] > 0.7
        ]
        return d[0].rect, rects[0]


# ------------------------------- 请求解析 ----------------------------------
def test_absent_or_all_zero_pad_parses_to_the_same_request():
    base = {"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5}
    a = exportreq._original_source(base)
    assert a.pad_mm == ()
    assert exportreq._original_source({**base, "pad_mm": [0, 0, 0, 0]}) == a
    assert exportreq._original_source({**base, "pad_mm": [0.0, -0.0, 0, 0]}) == a
    assert a.page_mm == (10.0, 5.0)


def test_nonzero_pad_parses_and_gives_the_page_size():
    base = {"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5}
    src = exportreq._original_source({**base, "pad_mm": [1, 2, 3, -1]})
    assert src.pad_mm == (1.0, 2.0, 3.0, -1.0)
    assert src.page_mm == (14.0, 6.0)


@pytest.mark.parametrize(
    "pad",
    [
        [1, 2, 3],  # 长度
        "1,2,3,4",
        [1, 2, 3, "x"],
        [1, 2, 3, True],
        [1, 2, 3, float("nan")],
        [1, 2, 3, float("inf")],
        [-6, 0, -4, 0],  # 页面宽 10 - 10 = 0
        [0, -3, 0, -3],  # 页面高 5 - 6 < 0
    ],
)
def test_bad_pad_is_a_structured_request_error(pad):
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq._original_source({"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5, "pad_mm": pad})
    assert e.value.code == "bad_pad"
    assert "bad_pad" in exportreq.ERROR_CODES


def test_nonzero_pad_needs_the_figure_size():
    with pytest.raises(exportreq.ExportRequestError) as e:
        exportreq._original_source({"figure_id": "p1.pdf", "pad_mm": [1, 0, 0, 0]})
    assert e.value.code == "bad_pad"


def test_plan_identity_only_changes_with_nonzero_pad():
    base = {"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5}
    mk = lambda extra: exportreq.normalize(  # noqa: E731
        {"scope": "original", "filename": "F", "formats": ["pdf"], "original": {**base, **extra}}
    )
    ref = exportreq.render_plan_ref(mk({}), [])
    assert exportreq.render_plan_ref(mk({"pad_mm": [0, 0, 0, 0]}), []) == ref
    assert exportreq.render_plan_ref(mk({"pad_mm": [1, 0, 0, 0]}), []) != ref


# ------------------------------- 导出 --------------------------------------
def test_zero_or_absent_pad_outputs_are_byte_identical(env):
    client, _ = env
    _, a = _post(client, _vector(["pdf", "png"], None, ppi=150, filename="a"))
    _, b = _post(client, _vector(["pdf", "png"], [0, 0, 0, 0], ppi=150, filename="b"))
    for fmt in ("pdf", "png"):
        assert _path(a, fmt).read_bytes() == _path(b, fmt).read_bytes(), fmt


def test_pdf_page_is_the_figure_plus_pad_and_the_panel_sits_at_left_top(env):
    client, _ = env
    pl, t, r, b = 10.0, 5.0, 2.0, 8.0
    status, body = _post(client, _vector(["pdf"], [pl, t, r, b]))
    assert status == 200, body
    assert _out(body, "pdf")["status"] == "done", body
    page, rect = _blue_rect(_path(body, "pdf"))
    assert page.width == pytest.approx((W_MM + pl + r) / PT, abs=0.1)
    assert page.height == pytest.approx((H_MM + t + b) / PT, abs=0.1)
    # 源里蓝块在 (10, 10) pt；面板落在 (l, t) → 蓝块左上 = (10 + l/PT, 10 + t/PT)
    assert rect.x0 == pytest.approx(10 + pl / PT, abs=0.1)
    assert rect.y0 == pytest.approx(10 + t / PT, abs=0.1)
    assert (rect.width, rect.height) == pytest.approx((80, 80), abs=0.1)
    assert _out(body, "pdf")["dimensions"]["mm"] == pytest.approx(
        [W_MM + pl + r, H_MM + t + b], abs=0.05
    )


def test_png_page_is_the_figure_plus_pad_and_pad_is_background(env):
    client, _ = env
    status, body = _post(client, _vector(["png"], [10, 5, 0, 0], ppi=100))
    assert status == 200, body
    pix = pymupdf.Pixmap(str(_path(body, "png")))
    assert (pix.width, pix.height) == (
        round((W_MM + 10) / 25.4 * 100),
        round((H_MM + 5) / 25.4 * 100),
    )
    assert _out(body, "png")["dimensions"]["px"] == [pix.width, pix.height]
    # 留白区是背景（白），蓝块在偏移之后
    assert pix.pixel(5, 5)[:3] == (255, 255, 255)
    bx, by = round((10 + 10 / PT + 30) / 72 * 100), round((5 / PT + 40) / 72 * 100)
    assert pix.pixel(bx, by)[2] > pix.pixel(bx, by)[0]


def test_svg_capability_is_not_changed_by_pad(env):
    """SVG 的能力与整版一致（静态 PDF 源给不出同图 SVG）：加留白给出同一个稳定码，页面尺寸与 PDF / PNG 共用同一个 `page_w_mm`。"""
    client, _ = env
    _, plain = _post(client, _vector(["svg"]))
    status, padded = _post(client, _vector(["svg"], [4, 3, 6, 1]))
    assert status == plain_status(plain) or status == 500, padded
    assert padded["code"] == plain["code"], (plain, padded)


def plain_status(body):
    return 200 if body.get("outputs") and _out(body, "svg")["status"] == "done" else 500


def test_negative_pad_crops_the_page(env):
    client, _ = env
    pl, t, r, b = -5.0, -3.0, -2.0, -1.0
    status, body = _post(client, _vector(["pdf", "png"], [pl, t, r, b], ppi=100))
    assert status == 200, body
    page, rect = _blue_rect(_path(body, "pdf"))
    assert page.width == pytest.approx((W_MM + pl + r) / PT, abs=0.1)
    assert page.height == pytest.approx((H_MM + t + b) / PT, abs=0.1)
    # 面板仍落在 (l, t) 且 l、t < 0：蓝块左上移出页面（被页面框裁掉），尺寸不变
    assert rect.x0 == pytest.approx(10 + pl / PT, abs=0.1)
    assert rect.y0 == pytest.approx(10 + t / PT, abs=0.1)
    assert (rect.width, rect.height) == pytest.approx((80, 80), abs=0.1)
    pix = pymupdf.Pixmap(str(_path(body, "png")))
    assert (pix.width, pix.height) == (
        round((W_MM + pl + r) / 25.4 * 100),
        round((H_MM + t + b) / 25.4 * 100),
    )


def test_a_pad_that_empties_the_page_is_rejected_before_any_export(env):
    client, _ = env
    status, body = _post(client, _vector(["pdf"], [-W_MM, 0, 0, 0]))
    assert status == 400, body
    assert body["code"] == "bad_pad", body
    assert not list(Path(m.EXPORT_DIR).glob("**/*.pdf"))


def test_raster_source_keeps_its_pixel_grid_inside_a_padded_page(env):
    """300 dpi 的位图源（120 × 80 px）：左右各加 2.54 mm = 30 px → 页面 180 × 80 px，源像素一一对应、不重采样。"""
    client, _ = env
    status, body = _post(client, _raster(["png", "tiff"], [2.54, 0, 2.54, 0]))
    assert status == 200, body
    for fmt in ("png", "tiff"):
        pix = pymupdf.Pixmap(str(_path(body, fmt)))
        assert (pix.width, pix.height) == (180, 80), fmt
        assert _out(body, fmt)["dimensions"]["px"] == [180, 80]
    pix = pymupdf.Pixmap(str(_path(body, "png")))
    assert pix.pixel(10, 40)[0] > 200  # 左留白
    assert pix.pixel(35, 40)[0] < 40  # 源左半黑，整体右移 30 px
    assert pix.pixel(100, 40)[0] > 200  # 源右半白
    assert pix.pixel(170, 40)[0] > 200  # 右留白


def test_unequal_density_raster_scales_the_whole_page_to_the_grid(env):
    figs = env[1]
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 150), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    pix.save(figs / "wide.png")
    spec = {
        "scope": "original",
        "filename": "W",
        "formats": ["png"],
        "original": {
            "figure_id": "wide.png",
            "w_mm": 25.4,
            "h_mm": 25.4,
            "px_w": 300,
            "px_h": 150,
            "source_kind": "raster",
            "pad_mm": [25.4, 0, 0, 12.7],  # 页面 50.8 × 38.1 mm；密度 300 × 150 dpi
        },
    }
    client, _ = env
    status, body = _post(client, spec)
    assert status == 200, body
    out = pymupdf.Pixmap(str(_path(body, "png")))
    assert (out.width, out.height) == (600, 225)


def test_pad_with_annotations_uses_page_coordinates(env):
    """标注坐标是页面坐标（以页面左上为原点）：同一个标注，加了左留白它依然画在页面同一位置。"""
    client, _ = env
    label = {
        "type": "text",
        "id": "t1",
        "text": "Label QA",
        "x_mm": 30.0,
        "y_mm": 40.0,
        "w_mm": 30,
        "h_mm": 6,
        "size_pt": 12,
    }
    spec = _vector(["pdf"], [10, 0, 0, 0])
    spec["original"]["objects"] = [label]
    status, body = _post(client, spec)
    assert status == 200, body
    with pymupdf.open(_path(body, "pdf")) as d:
        blocks = d[0].get_text("words")
        word = next(w for w in blocks if w[4] == "Label")
        assert word[0] == pytest.approx(30.0 / PT, abs=3)  # 页面坐标，没有再被留白平移


# ------------------------------- EPS ---------------------------------------
@pytest.fixture
def inner_eps(env, tmp_path, monkeypatch):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(200 / 72, 150 / 72))
    fig.add_axes([0.1, 0.1, 0.5, 0.5]).plot([0, 1], [0, 1])
    path = tmp_path / "inner.eps"
    fig.savefig(path, format="eps")

    def fake(job, src, dpi, tmp_dir, **kw):
        out = tmp_dir / "inner_copy.eps"
        out.write_bytes(path.read_bytes())
        return exportjob.Produced(
            format="eps", tmp_path=out, width_mm=W_MM, height_mm=H_MM, vector=True
        )

    monkeypatch.setattr(m, "_produce_original_eps", fake)
    return path


def test_eps_embeds_the_figure_at_the_pad_offset_and_page_bounding_box(env, inner_eps):
    client, _ = env
    pl, t, r, b = 10.0, 5.0, 0.0, 4.0
    status, body = _post(client, _vector(["eps"], [pl, t, r, b]))
    assert status == 200, body
    assert _out(body, "eps")["status"] == "done", body
    data = _path(body, "eps").read_bytes()
    page_w, page_h = 200 + (pl + r) / PT, 150 + (t + b) / PT
    assert f"%%BoundingBox: 0 0 {int(page_w) + 1} {int(page_h) + 1}".encode() in data[:300]
    # 原图左下角落在 (l, 页高 - 上 - 图高) = (l, b)（PostScript 原点在左下）
    m_ = re.search(rb"gsave (-?[\d.]+) (-?[\d.]+) translate newpath", data)
    assert m_, data[:600]
    assert float(m_.group(1)) == pytest.approx(pl / PT, abs=1e-3)
    assert float(m_.group(2)) == pytest.approx(b / PT, abs=1e-3)
    assert f"0 0 {page_w:.6f} {page_h:.6f}".encode()[:12] in data  # 整页裁剪
    assert _out(body, "eps")["dimensions"]["mm"] == pytest.approx(
        [page_w * PT, page_h * PT], abs=0.4
    )


def test_eps_without_pad_is_byte_identical_to_the_annotated_path(env, inner_eps):
    """无留白的带标注 EPS 不受 `offset_pt` / `clip_page` 新参数影响。"""
    from tavotto.engine import epsoverlay

    inner = inner_eps.read_bytes()
    a = epsoverlay.compose(inner, "ov\n", 200.0, 150.0, underlay="un\n")
    b = epsoverlay.compose(
        inner, "ov\n", 200.0, 150.0, underlay="un\n", offset_pt=None, clip_page=False
    )
    assert a == b
    assert b"rectclip\ngsave" not in a[:400]
