"""RenderCore 预览：位图素材不能交给 PDFium（2026-09-23 beta 实测：素材库里所有 PNG 缩略图都是 500，
`PDFium: Data format error`）。预览副本没有扩展名，类型必须从源路径带进 `_write`。

反证：把 `PreviewCache._write` 的位图分支拿掉 → 本文件第一条红（PdfiumError / render_child_*）。
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL", reason="RenderCore 依赖没装（pip install -r requirements.txt）")
pytest.importorskip("pypdfium2", reason="RenderCore 候选包没装")

from PIL import Image  # noqa: E402

from tavotto.rendercore import facade, rasterio  # noqa: E402


def _png(tmp_path, *, alpha: bool):
    im = Image.new(
        "RGBA" if alpha else "RGB", (300, 200), (255, 0, 0, 0) if alpha else (0, 128, 255)
    )
    p = tmp_path / ("a.png" if alpha else "b.png")
    im.save(p)
    return p


def test_png_asset_preview_goes_through_the_raster_path(tmp_path):
    src = _png(tmp_path, alpha=False)
    cache = facade.preview_cache(tmp_path / "cache")
    out = cache.get("b.png", src, 150)
    im = Image.open(io.BytesIO(out.read_bytes()))
    assert im.format == "PNG" and im.size == (150, 100)
    assert im.getpixel((10, 10))[:3] == (0, 128, 255)


def test_transparent_png_preview_is_on_white_by_default(tmp_path):
    buf = rasterio.preview(_png(tmp_path, alpha=True).read_bytes(), "png", 60)
    assert buf.channels == 3
    assert buf.samples[:3] == b"\xff\xff\xff"
    keep = rasterio.preview(_png(tmp_path, alpha=True).read_bytes(), "png", 60, transparent=True)
    assert keep.channels == 4


def test_unreadable_raster_is_a_preview_error_with_a_stable_code(tmp_path):
    bad = tmp_path / "c.png"
    bad.write_bytes(b"not a png at all")
    cache = facade.preview_cache(tmp_path / "cache2")
    from tavotto.rendercore.preview import PreviewError

    with pytest.raises(PreviewError) as exc:
        cache.get("c.png", bad, 100)
    assert exc.value.code == "raster_unreadable"


def test_raster_preview_is_charged_against_the_source_pixel_budget(tmp_path, monkeypatch):
    # 预览在父进程解码：不记账的话，一张压缩得很小的超大 PNG 会在这里被整张解开（ADR 0066 的源像素预算）
    from tavotto.rendercore import preview as preview_mod
    from tavotto.rendercore.preview import PreviewError

    monkeypatch.setattr(preview_mod, "SOURCE_MAX_PIXELS", 300 * 200 - 1)
    cache = facade.preview_cache(tmp_path / "cache3")
    with pytest.raises(PreviewError) as exc:
        cache.get("b.png", _png(tmp_path, alpha=False), 150)
    assert exc.value.code == "raster_too_large"
