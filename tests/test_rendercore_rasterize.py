"""RasterBuffer → PNG / TIFF 同源，以及经真实 ExportJob 的 PNG / TIFF 交付（统一实施包 U07，ADR 0066）。

两层：

* **纯模型**（任何机器）：`raster.encode_png` / `raster.write_tiff` 吃同一个 `RasterBuffer`（含行尾填充的），
  用**独立**解码器（`tests/support/pdfread.decode_png` 纯标准库、`tests/support/tiffcheck` 纯标准库）解回来
  逐字节相同；alpha 边缘 / 同灰度异色 / padding stride 三种 fixture；pHYs / 分辨率标签只在 dpi 已知时写。
* **rc-venv**（候选包 + 字体 + 真 child）：经 `exportjob.prepare / run` 一次导出 pdf + png + tiff，PNG 与 TIFF
  规范解码像素**逐个相同**（Pillow 第三把尺子）、尺寸 = round(pt·ppi/72)、透明背景带 alpha、DPI 进两个容器；
  child 超时 / 崩溃时该格式 `format_failed` 且 PDF 照常交付；重新栅格 PDF 与交付的 PNG 逐字节相同（RC-052）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

from tavotto.engine import exportjob
from tavotto.rendercore import fonts, raster, sources

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402
import tiffcheck  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
HAS_CANDIDATES = all(
    importlib.util.find_spec(m) is not None for m in ("pypdfium2", "pikepdf", "PIL", "uharfbuzz")
)


# ---------------------------------------------------------------- 纯模型：同一 buffer 两个容器


def _buffer(width=5, height=3, channels=4, pad=3, dpi=None) -> raster.RasterBuffer:
    """alpha 边缘（最后一列 alpha 渐变到 0）+ 同灰度异色（红 (200,50,50) 与绿 (50,200,50) 灰度相同）+ 行尾填充。"""
    rows = []
    for y in range(height):
        row = bytearray()
        for x in range(width):
            if x % 2 == 0:
                rgb = (200, 50, 50)
            else:
                rgb = (50, 200, 50)
            a = 255 if x < width - 1 else int(255 * y / max(1, height - 1))
            row += bytes(rgb + ((a,) if channels == 4 else ()))
        rows.append(bytes(row) + b"\xcc" * pad)
    stride = width * channels + pad
    return raster.RasterBuffer(width, height, channels, b"".join(rows), stride, dpi=dpi)


def _rgba_of(buf: raster.RasterBuffer) -> bytes:
    if buf.channels == 4:
        return buf.packed()
    rgb = buf.packed()
    return b"".join(rgb[i : i + 3] + b"\xff" for i in range(0, len(rgb), 3))


def test_png_and_tiff_decode_to_the_same_pixels_as_the_buffer_with_padding_stripped(tmp_path):
    buf = _buffer(dpi=None)
    png = raster.encode_png(buf)
    w, h, rgba = pdfread.decode_png(png)
    assert (w, h) == (5, 3) and rgba == _rgba_of(buf)
    assert b"\xcc" not in rgba  # 填充没有漏进产物
    facts = raster.write_tiff(buf, tmp_path / "a.tif")
    tags, samples = tiffcheck.decode_samples(tmp_path / "a.tif")
    assert (tags["width"], tags["height"], tags["samples_per_pixel"]) == (5, 3, 4)
    assert tags["extra_samples"] == 2  # 非预乘 alpha，与 PNG 色型 6 同义
    assert samples == buf.packed() and facts["channels"] == 4
    # 同一个 buffer 的两个容器：解码像素逐字节相同（RC-053）
    assert samples == rgba


def test_rgb_buffer_makes_png_color_type_2_and_tiff_with_three_samples(tmp_path):
    buf = _buffer(channels=3, pad=1)
    png = raster.encode_png(buf)
    assert png[25] == 2  # IHDR 色型：RGB
    facts = raster.write_tiff(buf, tmp_path / "rgb.tif")
    tags, samples = tiffcheck.decode_samples(tmp_path / "rgb.tif")
    assert (
        tags["samples_per_pixel"] == 3 and "extra_samples" not in tags and samples == buf.packed()
    )
    assert facts["channels"] == 3


def test_density_goes_into_both_containers_only_when_known(tmp_path):
    with_dpi = _buffer(dpi=300)
    png = raster.encode_png(with_dpi)
    assert b"pHYs" in png
    import struct

    i = png.index(b"pHYs")
    ppm_x, ppm_y, unit = struct.unpack(">IIB", png[i + 4 : i + 13])
    assert (ppm_x, ppm_y, unit) == (11811, 11811, 1)  # 300 dpi = 11811 px/m
    raster.write_tiff(with_dpi, tmp_path / "d.tif")
    tags = tiffcheck.read_tags(tmp_path / "d.tif")
    assert tags["x_resolution"] == 300 and tags["resolution_unit"] == 2
    unknown = _buffer(dpi=None)
    assert b"pHYs" not in raster.encode_png(unknown)
    raster.write_tiff(unknown, tmp_path / "u.tif")
    tags = tiffcheck.read_tags(tmp_path / "u.tif")
    assert tags["resolution_unit"] == 1  # 「没有绝对单位」，不编一个数


def test_same_luminance_colors_and_alpha_edges_survive_both_encoders(tmp_path):
    """RC-094：同灰度异色（红 / 绿）与纯 alpha 差异只在 RGBA 通道里活着——两个容器都必须原样保留。"""
    buf = _buffer()
    _, _, rgba = pdfread.decode_png(raster.encode_png(buf))
    raster.write_tiff(buf, tmp_path / "e.tif")
    _, samples = tiffcheck.decode_samples(tmp_path / "e.tif")
    px = lambda data, x, y: tuple(data[(y * 5 + x) * 4 : (y * 5 + x) * 4 + 4])  # noqa: E731
    for data in (rgba, samples):
        assert px(data, 0, 0) == (200, 50, 50, 255) and px(data, 1, 0) == (50, 200, 50, 255)
        assert px(data, 4, 0)[3] == 0 and px(data, 4, 2)[3] == 255 and px(data, 4, 1)[3] == 127


def test_encoders_refuse_a_buffer_that_is_not_the_contract():
    with pytest.raises(raster.RasterError):
        raster.RasterBuffer(2, 2, 4, b"\0" * 8, 8)  # 字节不够


# ---------------------------------------------------------------- rc-venv：经真实 ExportJob


needs = pytest.mark.skipif(
    not HAS_CANDIDATES,
    reason="候选包未装（pypdfium2 / pikepdf / Pillow / uharfbuzz）——这里是 not_run，不是绿",
)


@pytest.fixture(scope="module")
def provider():
    if not HAS_CANDIDATES:
        pytest.skip("候选包未装（not_run）")
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    return HbFaceProvider(reg)


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    if not HAS_CANDIDATES:
        pytest.skip("候选包未装（not_run）")
    from tavotto.rendercore import renderhost

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    h = renderhost.RenderHost(
        env=env, default_timeout=60, scratch_dir=tmp_path_factory.mktemp("rgba")
    )
    yield h
    h.close()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "figs").mkdir(parents=True)
    shutil.copy(FIXTURE / "page.pdf", root / "figs" / "Fig1.pdf")
    shutil.copy(FIXTURE / "original.png", root / "figs" / "Fig2.png")
    return root


@pytest.fixture(autouse=True)
def _reset_jobs():
    exportjob.reset_for_tests()
    yield
    exportjob.reset_for_tests()


def _spec(objects, formats=("pdf", "png", "tiff"), **extra) -> dict:
    return {
        "scope": "canvas",
        "formats": list(formats),
        "filename": "Fig 1",
        "overwrite": "replace",
        "ppi": 150,
        "canvas": {"page_w_mm": 120, "page_h_mm": 60, "objects": objects},
        **extra,
    }


def _panel(fid="figs/Fig1.pdf", **kw) -> dict:
    return {"type": "panel", "id": fid, "x_mm": 5, "y_mm": 5, "w_mm": 60, "h_mm": 40, **kw}


def _run(job, project, provider, host):
    from tavotto.rendercore import job as rcjob

    return exportjob.run(
        job,
        lambda j, tmp: rcjob.produce(
            j, tmp, sources=sources.StaticSourceResolver(project), provider=provider, host=host
        ),
    )


@needs
def test_pdf_png_and_tiff_come_from_one_canonical_pdf_and_one_raster(
    project, provider, host, tmp_path
):
    """120×60 mm 画布 @150 ppi → 709 × 354 px；PNG 与 TIFF 经 Pillow 解码逐像素相同；用交付的 PDF 再栅格一次
    与交付的 PNG 逐字节相同（RC-052）；DPI 进两个容器。"""
    from PIL import Image

    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [
                _panel(),
                _panel("figs/Fig2.png", x_mm=70, opacity=0.5),
                {
                    "type": "text",
                    "id": "t",
                    "text": "Hi 图",
                    "x_mm": 5,
                    "y_mm": 48,
                    "w_mm": 60,
                    "h_mm": 8,
                    "size_pt": 9,
                },
            ]
        ),
        export_dir,
    )
    payload = _run(job, project, provider, host)
    assert payload["status"] == "done", payload
    by = {o["format"]: o for o in payload["outputs"]}
    assert (
        by["pdf"]["vector"] is True
        and by["png"]["vector"] is False
        and by["tiff"]["vector"] is False
    )
    w_px, h_px = round(120 / 25.4 * 150), round(60 / 25.4 * 150)
    assert by["png"]["dimensions"]["px"] == [w_px, h_px] == by["tiff"]["dimensions"]["px"]
    png = Image.open(export_dir / "Fig 1.png")
    tif = Image.open(export_dir / "Fig 1.tiff")
    assert png.size == tif.size == (w_px, h_px)
    assert png.mode == "RGB" and tif.mode == "RGB"  # 白底：没有 alpha
    assert png.tobytes() == tif.tobytes()  # 规范解码像素逐个相同（RC-053）
    assert tuple(round(v) for v in png.info["dpi"]) == (150, 150)
    assert tuple(round(v) for v in tif.info["dpi"]) == (150, 150)
    # 交付的 PDF 再栅格：与交付的 PNG 逐字节相同（同一个 renderer、同一参数、同一份 canonical PDF）
    again = host.render(export_dir / "Fig 1.pdf", dpi=150.0)
    assert raster.encode_png(again) == (export_dir / "Fig 1.png").read_bytes()
    # 面板真的在位图里：源蓝矩形的一个点是蓝
    x, y = (5 + (60 / 270) * 45) / 25.4 * 150, (5 + 40 - (40 / 160) * 35) / 25.4 * 150
    r, g, b = png.getpixel((int(x), int(y)))
    assert b > 200 and r < 60 and g < 60, (r, g, b)


@needs
def test_transparent_background_yields_rgba_with_straight_alpha_in_both(
    project, provider, host, tmp_path
):
    from PIL import Image

    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [_panel("figs/Fig2.png", opacity=0.5)],
            formats=("png", "tiff"),
            background="transparent",
        ),
        export_dir,
    )
    payload = _run(job, project, provider, host)
    assert payload["status"] == "done", payload
    png = Image.open(export_dir / "Fig 1.png").convert("RGBA")
    tif = Image.open(export_dir / "Fig 1.tiff").convert("RGBA")
    assert png.tobytes() == tif.tobytes()
    assert png.getpixel((2, 2)) == (0, 0, 0, 0)  # 没画到的地方全 0
    # 面板左半（不透明蓝 (30,60,200)）以 0.5 合成到透明底：straight alpha → 颜色不变、alpha 128
    x, y = (5 + 15) / 25.4 * 150, (5 + 20) / 25.4 * 150
    r, g, b, a = png.getpixel((int(x), int(y)))
    assert abs(a - 128) <= 2 and abs(r - 30) <= 3 and abs(g - 60) <= 3 and abs(b - 200) <= 3, (
        r,
        g,
        b,
        a,
    )
    tags = tiffcheck.read_tags(export_dir / "Fig 1.tiff")
    assert tags["extra_samples"] == 2 and tags["samples_per_pixel"] == 4
    assert not (export_dir / "Fig 1.pdf").exists()  # 没要 PDF 就不交付（但它在临时目录里被用过）


@needs
def test_a_blank_page_still_rasterizes_and_eps_is_refused_structurally(
    project, provider, host, tmp_path
):
    """全 hidden 的透明页：PNG / TIFF 是合法的空位图（不是 U06 那时的 format_failed）；EPS 没有写入器。"""
    export_dir = tmp_path / "out"
    job = exportjob.prepare(
        _spec(
            [
                {
                    "type": "text",
                    "id": "h",
                    "text": "x",
                    "x_mm": 1,
                    "y_mm": 1,
                    "w_mm": 5,
                    "h_mm": 5,
                    "hidden": True,
                }
            ],
            formats=("pdf", "png", "tiff", "eps"),
            background="transparent",
        ),
        export_dir,
    )
    payload = _run(job, project, provider, host)
    assert payload["status"] == "partial", payload
    by = {o["format"]: o for o in payload["outputs"]}
    assert by["pdf"]["status"] == by["png"]["status"] == by["tiff"]["status"] == "done"
    assert by["eps"]["status"] == "failed" and by["eps"]["error"]["code"] == "eps_not_for_canvas"
    assert by["eps"]["error"]["params"]["unsupported"][0]["operation"] == "format"
    w, h, bpp, rgba = pdfread.decode_png_any((export_dir / "Fig 1.png").read_bytes())
    assert bpp == 4 and set(rgba) == {0}  # 透明底：RGBA，全 0
    assert "h: hidden" in payload["warnings"]


@needs
@pytest.mark.parametrize(
    ("command", "raster_code"),
    [
        ([sys.executable, "-c", "import sys; sys.exit(7)"], "render_child_died"),
        (["{tmp}/no-such-render-child"], "render_child_spawn_failed"),
    ],
    ids=["exits-at-once", "cannot-spawn"],
)
def test_a_child_failure_fails_only_the_raster_formats_and_keeps_the_pdf(
    project, provider, tmp_path, command, raster_code
):
    """render child 起不来——命令指向一个立刻退出的解释器，或指向不存在的 exe（冻结产物命令写错；Codex #471
    第三轮 P2：`Popen` 的 OSError 也要是结构化的 `render_child_spawn_failed`）：PNG / TIFF `format_failed` 带 child
    的 code，PDF 照常，作业不炸。"""
    from tavotto.rendercore import renderhost

    dead = renderhost.RenderHost(
        [c.replace("{tmp}", str(tmp_path)) for c in command], default_timeout=5
    )
    try:
        export_dir = tmp_path / "out"
        job = exportjob.prepare(_spec([_panel()]), export_dir)
        payload = _run(job, project, provider, dead)
    finally:
        dead.close()
    assert payload["status"] == "partial", payload
    by = {o["format"]: o for o in payload["outputs"]}
    assert by["pdf"]["status"] == "done"
    for fmt in ("png", "tiff"):
        assert by[fmt]["status"] == "failed" and by[fmt]["error"]["code"] == "format_failed"
        assert by[fmt]["error"]["params"]["raster_code"] == raster_code
        assert not (export_dir / f"Fig 1.{fmt}").exists()


@needs
def test_a_source_that_cannot_be_imported_fails_pdf_and_the_rasters_say_why(
    project, provider, host, tmp_path
):
    """源 PDF 加密：PDF 那一项 source_unreadable；PNG / TIFF 说「Canonical PDF 未写出」而不是画一张空图。"""
    import pikepdf

    enc = pikepdf.new()
    enc.add_blank_page(page_size=(100, 100))
    enc.save(project / "figs" / "Enc.pdf", encryption=pikepdf.Encryption(owner="o", user="u", R=6))
    export_dir = tmp_path / "out"
    job = exportjob.prepare(_spec([_panel("figs/Enc.pdf")]), export_dir)
    payload = _run(job, project, provider, host)
    assert payload["status"] == "failed", payload
    by = {o["format"]: o for o in payload["outputs"]}
    assert by["pdf"]["error"]["params"]["why"] == "encrypted"
    for fmt in ("png", "tiff"):
        assert by[fmt]["status"] == "failed" and "canonical_pdf" in by[fmt]["error"]["params"]
    assert not any(export_dir.glob("Fig 1.*"))
