"""位图方向标记（EXIF / TIFF Orientation）一律照办（TVM-22EE / TVD-1KN2）。

画布上 JPEG 由浏览器 `<img>` 直接显示、浏览器按方向标记摆正；导出 / 预览走 `rendercore`。两边必须是同一张图：
手机照片「横着存像素 + 标记转 90°」，以前导出出来转了 90° 还被塞进竖框变形。

期望值一律由 **Pillow 自己的 `ImageOps.exif_transpose`** 给出（与 `rasterio` 的方向表、PDF 摆正矩阵不共享一行代码），
PDF 端到端由 PDFium 栅格化后按象限采样。
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

pytest.importorskip("PIL", reason="RenderCore 依赖没装（pip install -r requirements.txt）")

from PIL import Image, ImageOps  # noqa: E402

from tavotto.rendercore import fonts, ir, pdfwriter, preview, rasterio  # noqa: E402

#: 存储像素 80×40（不是正方形：宽高互换看得出来），四个象限四种颜色
STORED_W, STORED_H = 80, 40
QUADS = {"TL": (255, 0, 0), "TR": (0, 160, 0), "BL": (0, 0, 255), "BR": (255, 200, 0)}
CENTERS = {"TL": (0.25, 0.25), "TR": (0.75, 0.25), "BL": (0.25, 0.75), "BR": (0.75, 0.75)}
ORIENTATIONS = range(1, 9)


def _stored() -> Image.Image:
    im = Image.new("RGB", (STORED_W, STORED_H))
    hw, hh = STORED_W // 2, STORED_H // 2
    for name, box in {
        "TL": (0, 0, hw, hh),
        "TR": (hw, 0, STORED_W, hh),
        "BL": (0, hh, hw, STORED_H),
        "BR": (hw, hh, STORED_W, STORED_H),
    }.items():
        im.paste(QUADS[name], box)
    return im


def _jpeg(orientation: int | None, *, big_endian: bool = False) -> bytes:
    out = io.BytesIO()
    kw = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[0x0112] = orientation
        if big_endian:
            exif.endian = ">"
        kw["exif"] = exif.tobytes()
    _stored().save(out, "JPEG", quality=95, subsampling=0, **kw)
    return out.getvalue()


def _tiff(orientation: int) -> bytes:
    out = io.BytesIO()
    _stored().save(out, "TIFF", tiffinfo={274: orientation})
    return out.getvalue()


def _truth(data: bytes) -> Image.Image:
    """独立实现：Pillow 自己按方向标记摆正。"""
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def _close(a, b, tol=40) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _at(im: Image.Image, u: float, v: float):
    return im.getpixel(
        (min(im.width - 1, int(u * im.width)), min(im.height - 1, int(v * im.height)))
    )


# ================================================================ 解析与尺寸


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("fmt", ["jpg", "tif"])
def test_reported_size_and_decoded_pixels_are_upright(fmt, orientation):
    data = _jpeg(orientation) if fmt == "jpg" else _tiff(orientation)
    truth = _truth(data)
    assert rasterio.orientation(data, fmt) == orientation
    assert rasterio.header_size(data, fmt) == truth.size
    info = rasterio.header_info(data, fmt)
    assert (info["width"], info["height"]) == truth.size
    buf = rasterio.decode(data, fmt)
    assert (buf.width, buf.height) == truth.size
    got = Image.frombytes("RGB", (buf.width, buf.height), buf.packed())
    for name, (u, v) in CENTERS.items():
        assert _close(_at(got, u, v), _at(truth, u, v)), (fmt, orientation, name)
    # 预览（/api/render 的位图路）与解码同一张图
    pv = rasterio.preview(data, fmt, truth.width * 2)
    assert (pv.width, pv.height) == (truth.width * 2, truth.height * 2)


def test_big_endian_exif_is_read():
    assert rasterio.orientation(_jpeg(6, big_endian=True), "jpg") == 6


@pytest.mark.parametrize(
    "data",
    [
        _jpeg(None),  # 没有 Exif
        _jpeg(1),
        # 坏的 Exif 载荷：不是 TIFF 头
        _jpeg(None)[:2] + b"\xff\xe1\x00\x0cExif\x00\x00XXXX" + _jpeg(None)[2:],
        # 方向值越界
        _jpeg(None)[:2]
        + b"\xff\xe1\x00\x22Exif\x00\x00II*\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x09\x00\x00\x00\x00\x00\x00\x00"
        + _jpeg(None)[2:],
    ],
    ids=["no-exif", "orientation-1", "garbage-exif", "out-of-range"],
)
def test_absent_or_malformed_orientation_is_upright_as_stored(data):
    assert rasterio.orientation(data, "jpg") == 1
    assert rasterio.header_size(data, "jpg") == (STORED_W, STORED_H)
    jp = rasterio.jpeg_passthrough(data, "jpg")
    assert jp is not None and jp["orientation"] == 1


def test_png_orientation_is_not_applied():
    """浏览器对 PNG eXIf 的方向并不一致地照办：画布上不转的，这里也不转。"""
    out = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6
    _stored().save(out, "PNG", exif=exif.tobytes())
    data = out.getvalue()
    assert rasterio.orientation(data, "png") == 1
    assert rasterio.header_size(data, "png") == (STORED_W, STORED_H)


def test_passthrough_keeps_stored_size_and_reports_orientation():
    """直通不改字节：`/Width` `/Height` 要的是 SOF 里存储的尺寸，摆正交给调用方。"""
    jp = rasterio.jpeg_passthrough(_jpeg(6), "jpg")
    assert jp is not None
    assert (jp["width"], jp["height"], jp["orientation"]) == (STORED_W, STORED_H, 6)


def test_preview_cache_key_carries_the_raster_decode_revision():
    """旧版本按存储方向缓存的预览不能在升级后继续交出去。"""
    kw = dict(transparent=False, renderer_version="r", fonts_version="f")
    now = preview.cache_key("a.jpg", "0" * 64, 200, **kw)
    assert preview.RASTER_DECODE_REVISION >= 2
    old = preview.RASTER_DECODE_REVISION
    try:
        preview.RASTER_DECODE_REVISION = 1
        assert preview.cache_key("a.jpg", "0" * 64, 200, **kw) != now
    finally:
        preview.RASTER_DECODE_REVISION = old


# ================================================================ 写入器端到端


def _candidates() -> None:
    from tavotto.rendercore.hbshaper import CandidatePackagesMissing, require

    try:
        require("pikepdf", "fontTools", "uharfbuzz", "pypdfium2", "PIL")
    except CandidatePackagesMissing as exc:
        pytest.skip(f"候选包未装（not_run）：{exc}")


@pytest.fixture(scope="module")
def provider():
    _candidates()
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return HbFaceProvider(reg)


PAGE_W, PAGE_H = 300.0, 300.0
SCALE = 2


def _page(data: bytes, rect, crop=None) -> ir.Page:
    res = ir.FileResource(
        "photo.jpg", "jpg", hashlib.sha256(data).hexdigest(), "static", "sha256:" + "0" * 64
    )
    node = ir.Image(resource="im", rect=rect, crop=crop, object_id="photo")
    return ir.Page(PAGE_W, PAGE_H, (node,), {"im": res}, (1.0, 1.0, 1.0))


def _rect_for(truth: Image.Image):
    # 画布上的框按摆正后的宽高比（浏览器显示的那张）
    k = 2.0
    return (40.0, 40.0, truth.width * k, truth.height * k)


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("crop", [None, (0.5, 0.0, 0.5, 0.5)], ids=["full", "crop-TR"])
def test_pdf_draws_the_upright_photo_without_reencoding_the_jpeg(
    provider, tmp_path: Path, orientation, crop
):
    import pikepdf
    import pypdfium2

    data = _jpeg(orientation)
    truth = _truth(data)
    rect = _rect_for(truth)
    out = tmp_path / f"o{orientation}.pdf"
    pdfwriter.write_pdf(_page(data, rect, crop), out, provider, {"im": data})

    with pikepdf.open(out) as pdf:
        streams = [
            o
            for o in pdf.objects
            if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"
        ]
        assert len(streams) == 1
        assert streams[0].get("/Filter") == "/DCTDecode"
        assert streams[0].read_raw_bytes() == data  # 原字节直通，不重编码

    img = pypdfium2.PdfDocument(str(out))[0].render(scale=SCALE).to_pil().convert("RGB")
    tx, ty, tw, th = rect
    cx, cy, cw, ch = crop or (0.0, 0.0, 1.0, 1.0)
    for name, (u, v) in CENTERS.items():
        # 框里的 (u, v) 对应摆正后原图里的 (cx + u·cw, cy + v·ch)
        want = _at(truth, cx + u * cw, cy + v * ch)
        x, y_up = tx + u * tw, ty + th - v * th
        got = img.getpixel((int(x * SCALE), int((PAGE_H - y_up) * SCALE)))
        assert _close(got, want), (orientation, crop, name, got, want)


@pytest.mark.parametrize("orientation", [1, 6])
def test_svg_embeds_an_upright_image(provider, tmp_path: Path, orientation):
    import base64
    import re

    from tavotto.rendercore import svgwriter

    data = _jpeg(orientation)
    truth = _truth(data)
    out = tmp_path / f"o{orientation}.svg"
    svgwriter.write_svg(_page(data, _rect_for(truth)), out, provider, {"im": data})
    text = out.read_text(encoding="utf-8")
    m = re.search(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/=]+)", text)
    assert m is not None
    if orientation == 1:
        assert m.group(1) == "image/jpeg"  # 没有方向标记照旧直通
        return
    # 带方向标记：各家 SVG 阅读器对 `<image>` 里的 Exif 照不照办不一致，嵌摆正后的 PNG
    assert m.group(1) == "image/png"
    embedded = Image.open(io.BytesIO(base64.b64decode(m.group(2)))).convert("RGB")
    assert embedded.size == truth.size
    for name, (u, v) in CENTERS.items():
        assert _close(_at(embedded, u, v), _at(truth, u, v)), name


# ================================================================ 物理尺寸：密度轴跟着摆正


@pytest.mark.parametrize("orientation", [1, 6])
def test_unequal_density_axes_follow_the_upright_pixels(tmp_path: Path, orientation):
    """x≠y 密度的照片：宽高互换时密度的 x / y 也要换，否则原图毫米尺寸错（Codex #2015 P1）。
    存储 1200×600、密度 (300, 150) → 物理上 101.6×101.6 mm，摆不摆正都是这个正方形。"""
    from tavotto.engine import originalspec
    from tavotto.rendercore import facade

    exif = Image.Exif()
    exif[0x0112] = orientation
    path = tmp_path / "photo.jpg"
    Image.new("RGB", (1200, 600), (120, 120, 120)).save(
        path, "JPEG", dpi=(300, 150), exif=exif.tobytes()
    )
    probe = facade.probe_asset(path, "raster")
    spec = originalspec.asset_spec(path, "raster", probe)
    assert (spec["logical_w_mm"], spec["logical_h_mm"]) == (101.6, 101.6)
    want_axes = (300.0, 150.0) if orientation == 1 else (150.0, 300.0)
    assert originalspec.raster_dpi(path) == want_axes
