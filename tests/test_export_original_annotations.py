"""QA-WIN-20：scope=original 带画布标注（`original.objects`）。

标注由整版导出那条 RenderCore 合成路径画（没有第二套绘制）；每个格式的输出里都要有图、也要有标注文字；
位图源保持源像素网格；没有标注时请求与输出与从前逐字节相同。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exportreq

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


def _inner_eps(tmp_path: Path) -> Path:
    """worker 的 matplotlib 会交出的那种原图 EPS（200 × 150 pt）。"""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib.figure import Figure

    fig = Figure(figsize=(200 / 72, 150 / 72))
    ax = fig.add_axes([0.1, 0.1, 0.5, 0.5])
    ax.plot([0, 1], [0, 1])
    out = tmp_path / "inner.eps"
    fig.savefig(out, format="eps")
    return out


@pytest.fixture
def inner_eps(env, tmp_path, monkeypatch):
    """原图 EPS 照旧由 worker 出（ADR 0046）：这里用 matplotlib 现写一份顶替 worker。"""
    path = _inner_eps(tmp_path)

    def fake(job, src, dpi, tmp_dir, **kw):
        out = tmp_dir / "inner_copy.eps"
        out.write_bytes(path.read_bytes())
        return exportjob.Produced(
            format="eps", tmp_path=out, width_mm=70.56, height_mm=52.92, vector=True
        )

    monkeypatch.setattr(m, "_produce_original_eps", fake)
    return path


def test_eps_is_an_outer_eps_embedding_the_worker_eps_verbatim(env, inner_eps):
    client, _ = env
    status, body = _post(client, _vector(["pdf", "eps"], [_label()]))
    assert status == 200, body
    assert _out(body, "eps")["status"] == "done", body
    data = _path(body, "eps").read_bytes()
    inner = inner_eps.read_bytes()
    # DSC 头与图幅
    assert data.startswith(b"%!PS-Adobe-3.0 EPSF-3.0\n")
    assert b"%%BoundingBox: 0 0 200 150" in data[:300]
    # 嵌入段完整、原样
    begin = data.index(b"%%BeginDocument:")
    end = data.index(b"%%EndDocument")
    assert inner in data[begin:end]
    assert (
        data.count(b"%%BeginDocument")
        == data.count(b"%%EndDocument")
        == 1 + inner.count(b"%%BeginDocument")
    )
    # 状态保存 / 恢复成对
    assert data.index(b"/tvt_state save def") < begin < end < data.index(b"tvt_state restore")
    # 标注叠加层在嵌入段之后：字形轮廓路径 + 填充，没有任何位图运算符
    tail = data[data.index(b"tvt_state restore") :]
    assert b" fill grestore" in tail and b"curveto" in tail
    for op in (b"colorimage", b"image\n", b" imagemask"):
        assert op not in tail
    assert data.rstrip().endswith(b"%%EOF")
    assert _out(body, "eps")["dimensions"]["mm"] == pytest.approx([70.556, 52.917], abs=0.4)
    assert "Label QA" in _pdf_text(_path(body, "pdf"))  # 其余格式照常


def test_eps_alignment_prefers_the_embedded_hires_bounding_box(env, tmp_path, monkeypatch):
    """图幅非整数 pt 时，叠加层原点 = 内层 `%%HiResBoundingBox` 的左下角（不是取整后的 BoundingBox）。"""
    inner = (
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 201 151\n"
        b"%%HiResBoundingBox: 0.5 0.25 200.5 150.25\n%%EndComments\nnewpath 0 0 moveto 1 1 lineto stroke\nshowpage\n%%EOF\n"
    )

    def fake(job, src, dpi, tmp_dir, **kw):
        out = tmp_dir / "inner_copy.eps"
        out.write_bytes(inner)
        return exportjob.Produced(format="eps", tmp_path=out, vector=True)

    monkeypatch.setattr(m, "_produce_original_eps", fake)
    client, _ = env
    _, body = _post(client, _vector(["eps"], [_label()]))
    data = _path(body, "eps").read_bytes()
    assert b"gsave -0.5 -0.25 translate newpath 0.5 0.25 200 150 rectclip" in data
    assert b"%%HiResBoundingBox: 0 0 200.000000 150.000000" in data[:300]


def test_eps_with_translucent_annotation_fails_only_that_item(env, inner_eps):
    client, _ = env
    glass = {
        "type": "shape",
        "id": "s1",
        "shape": "rect",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 20,
        "h_mm": 10,
        "fill": "#ff0000",
        "fill_opacity": 0.5,
        "stroke_pt": 1,
    }
    status, body = _post(client, _vector(["pdf", "eps"], [glass]))
    assert status == 200, body
    eps = _out(body, "eps")
    assert eps["status"] != "done"
    assert eps["error"]["code"] == "eps_annotations_unsupported"
    assert eps["error"]["params"]["operation"] == "object_alpha"
    assert _out(body, "pdf")["status"] == "done"  # 不静默丢，也不拖累其它格式


def test_eps_opaque_shapes_are_written_as_ps_paths(env, inner_eps):
    client, _ = env
    box = {
        "type": "shape",
        "id": "s1",
        "shape": "rect",
        "x_mm": 5,
        "y_mm": 5,
        "w_mm": 20,
        "h_mm": 10,
        "fill": "#ff0000",
        "stroke_pt": 1,
        "dash": "dashed",
    }
    _, body = _post(client, _vector(["eps"], [box]))
    data = _path(body, "eps").read_bytes()
    tail = data[data.index(b"tvt_state restore") :]
    assert b"1 0 0 setrgbcolor fill" in tail.replace(b"gsave ", b"") or b"1 0 0 setrgbcolor" in tail
    assert b"setdash" in tail and b"stroke" in tail


def test_eps_without_a_worker_eps_keeps_the_original_stable_code(env):
    """原图 EPS 本身给不出（这张图没有脚本）：沿用 `eps_needs_script`，不因标注改码。"""
    client, _ = env
    _, body = _post(client, _vector(["eps"], [_label()]))
    assert _out(body, "eps")["error"]["code"] == "eps_needs_script"


GS = Path("/usr/local/bin/gs")


@pytest.mark.skipif(
    not GS.is_file(),
    reason="本机没有 /usr/local/bin/gs（只在测试里用它栅格化 EPS，产品代码不调用）",
)
def test_eps_annotation_region_matches_the_png_roughly(env, inner_eps, tmp_path):
    import subprocess

    client, _ = env
    label = _label(40, 10)
    _, body = _post(client, _vector(["eps", "png"], [label], ppi=150))
    ras = tmp_path / "eps.png"
    subprocess.run(
        [
            str(GS),
            "-q",
            "-dNOPAUSE",
            "-dBATCH",
            "-dSAFER",
            "-sDEVICE=png16m",
            "-r150",
            "-dEPSCrop",
            "-dGraphicsAlphaBits=4",
            f"-sOutputFile={ras}",
            str(_path(body, "eps")),
        ],
        check=True,
        capture_output=True,
    )
    a, b = pymupdf.Pixmap(str(ras)), pymupdf.Pixmap(str(_path(body, "png")))
    s = 150 / 25.4
    box = (int(39 * s), int(9 * s), int(71 * s), int(17 * s))

    def ink(p):
        n, tot = p.n, 0
        for y in range(box[1], min(box[3], p.height)):
            for x in range(box[0], min(box[2], p.width)):
                tot += 255 - p.samples[y * p.stride + x * n]
        return tot

    ia, ib = ink(a), ink(b)
    assert ib > 0 and ia > 0, (ia, ib)  # 两边都画了字
    assert 0.6 < ia / ib < 1.6, (ia, ib)


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


# ---------------------------------------------------------------------------
# Codex 评审（#1551）：单一来源 / 图层次序 / 横纵密度不同
# ---------------------------------------------------------------------------
def _pixel(path: Path, x_mm: float, y_mm: float, ppi: float):
    pix = pymupdf.Pixmap(str(path))
    x, y = int(x_mm / 25.4 * ppi), int(y_mm / 25.4 * ppi)
    off = y * pix.stride + x * pix.n
    return tuple(pix.samples[off : off + 3])


def test_all_formats_come_from_one_source_when_eps_is_requested(
    env, inner_eps, tmp_path, monkeypatch
):
    """要 EPS 时 EPS 的内层是活 Figure 现画的；PDF 不许取磁盘旧文件——两者必须出自同一个来源（与无标注的原图导出同一判定）。"""
    import hashlib

    from tavotto.engine import figcapture
    from tavotto.rendercore import sources as rc_sources

    live = tmp_path / "live.pdf"
    doc = pymupdf.open()
    doc.new_page(width=200, height=150).insert_text((20, 120), "LivePanel", fontsize=11)
    doc.save(live)
    doc.close()
    calls = []

    def fake_execution_source(obj, dpi, sink, out_dir, **kw):
        calls.append(obj["id"])
        data = live.read_bytes()
        art = figcapture.SourceArtifact(
            source_id=obj["id"],
            origin=figcapture.ORIGIN_EXECUTION,
            kind="pdf",
            bytes_sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            receipt_id="r1",
            receipt_identity="i1",
        )
        return rc_sources.FrozenSource(art, live)

    monkeypatch.setattr(m, "_execution_source", fake_execution_source)
    monkeypatch.setattr(m, "_has_runnable_script", lambda rel: True)
    client, _ = env
    status, body = _post(client, _vector(["pdf", "eps"], [_label()]))
    assert status == 200, body
    assert _out(body, "pdf")["status"] == "done", body
    assert "LivePanel" in _pdf_text(_path(body, "pdf")) and "PanelText" not in _pdf_text(
        _path(body, "pdf")
    )
    assert calls
    # 不要 EPS：照旧取磁盘文件，不去跑脚本
    calls.clear()
    _, plain = _post(client, _vector(["pdf"], [_label()], filename="disk"))
    assert "PanelText" in _pdf_text(_path(plain, "pdf")) and not calls


def _under_red_rect():
    return {
        "type": "shape",
        "id": "s1",
        "shape": "rect",
        "x_mm": 10,
        "y_mm": 10,
        "w_mm": 10,
        "h_mm": 10,
        "fill": "#ff0000",
        "stroke_pt": 0.05,
    }


def test_annotation_below_the_figure_is_drawn_under_it(env):
    client, _ = env
    spots = {}
    for below in (0, 1):
        spec = _vector(["png"], [_under_red_rect()], ppi=100, filename=f"b{below}")
        if below:
            spec["original"]["objects_below"] = below
        _, body = _post(client, spec)
        spots[below] = _pixel(_path(body, "png"), 15, 15, 100)
    assert spots[0][0] > 200 and spots[0][2] < 80  # 在图上：红盖住蓝
    assert spots[1][2] > 150 and spots[1][0] < 80  # 在图下：蓝盖住红


def test_eps_writes_below_annotations_before_the_embedded_figure(env, inner_eps):
    client, _ = env
    under, over = _under_red_rect(), {**_under_red_rect(), "id": "s2", "fill": "#00ff00"}
    spec = _vector(["eps"], [under, over])
    spec["original"]["objects_below"] = 1
    _, body = _post(client, spec)
    data = _path(body, "eps").read_bytes()
    begin, end = data.index(b"%%BeginDocument"), data.index(b"%%EndDocument")
    assert data.index(b"1 0 0 setrgbcolor") < begin  # 红：图下
    assert data.index(b"0 1 0 setrgbcolor") > end  # 绿：图上


def test_objects_below_is_validated():
    base = {"figure_id": "p1.pdf", "w_mm": 10, "h_mm": 5, "objects": [_label()]}
    assert exportreq._original_source({**base, "objects_below": 1}).objects_below == 1
    for bad in (2, -1, True, "1"):
        with pytest.raises(exportreq.ExportRequestError):
            exportreq._original_source({**base, "objects_below": bad})


def _ink_box(path: Path):
    """深色像素的包围盒（x0, y0, x1, y1），及位图宽高。"""
    pix = pymupdf.Pixmap(str(path))
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            if pix.pixel(x, y)[0] < 128:
                xs.append(x)
                ys.append(y)
    assert xs, "没有墨迹"
    return (min(xs), min(ys), max(xs), max(ys)), (pix.width, pix.height)


def _white_png(path: Path, w: int, h: int):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    pix.save(path)


def _raster_spec(fid: str, w: int, h: int, formats):
    return {
        "scope": "original",
        "filename": fid.split(".")[0],
        "formats": formats,
        "original": {
            "figure_id": fid,
            "w_mm": 25.4,
            "h_mm": 25.4,
            "px_w": w,
            "px_h": h,
            "source_kind": "raster",
            "objects": [_label(2, 10)],
        },
    }


def test_unequal_axis_densities_draw_right_then_scale_to_source_pixels(env):
    """300 × 150 px、25.4 × 25.4 mm（横纵密度 300 / 150 dpi）：先按逻辑图幅各向同性画对（文字不被压扁、基线不错位），
    再整张缩到 300 × 150。标注墨迹的相对位置 / 相对高度与等密度（300 × 300）参照一致。"""
    client, figs = env
    _white_png(figs / "wide.png", 300, 150)
    _white_png(figs / "square.png", 300, 300)
    _, ref = _post(client, _raster_spec("square.png", 300, 300, ["png"]))
    status, body = _post(client, _raster_spec("wide.png", 300, 150, ["png", "tiff", "pdf"]))
    assert status == 200, body
    for fmt in ("png", "tiff"):
        p = pymupdf.Pixmap(str(_path(body, fmt)))
        assert (p.width, p.height) == (300, 150), fmt
        assert _out(body, fmt)["dimensions"]["px"] == [300, 150]
    (rx0, ry0, rx1, ry1), (rw, rh) = _ink_box(_path(ref, "png"))
    (x0, y0, x1, y1), (w, h) = _ink_box(_path(body, "png"))
    # 横向与参照一致；纵向相对位置 / 相对高度也与参照一致（不是被换算成别的比例）
    for got, want in (
        (x0 / w, rx0 / rw),
        (x1 / w, rx1 / rw),
        (y0 / h, ry0 / rh),
        (y1 / h, ry1 / rh),
    ):
        assert got == pytest.approx(want, abs=0.02), (got, want)
    assert (y1 - y0) / h == pytest.approx((ry1 - ry0) / rh, abs=0.02)
    assert _pdf_text(_path(body, "pdf")).strip() == "Label QA"  # 逻辑 mm 的 PDF 不受影响


def test_unequal_axis_densities_write_the_source_x_and_y_dpi(env):
    import struct

    client, figs = env
    _white_png(figs / "wide.png", 300, 150)
    _, body = _post(client, _raster_spec("wide.png", 300, 150, ["png"]))
    data = _path(body, "png").read_bytes()
    i = data.index(b"pHYs")
    ppm_x, ppm_y, unit = struct.unpack(">IIB", data[i + 4 : i + 13])
    assert unit == 1 and round(ppm_x * 0.0254) == 300 and round(ppm_y * 0.0254) == 150
