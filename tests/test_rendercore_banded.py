"""条带栅格（ADR 0077 P2）：整页像素超过 render child 的单张预算时按行带渲染、边收边写 PNG / TIFF。

主语：**写出来的文件**（测试侧纯标准库读取器解码、产品检查器 `inspector.observe_*` 核完整性）、**child 渲染了几次**
（`RenderHost.requests`）、以及**预算以内的路径一字不变**。真 child（pypdfium2）只在 rc-venv 里有；缺包 skip 并写明。
用例把 `max_pixels` / `BAND_PIXELS` 调小，让一页小图也切成好几带——真实尺寸（A4 @ 1200 ppi）由 perf-baseline 的数字说话。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from tavotto.rendercore import banded, renderchild, renderhost

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import pdfread  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
U06_PDF = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u06" / "u06.pdf"

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pypdfium2") is None or importlib.util.find_spec("pikepdf") is None,
    reason="本解释器里没有 pypdfium2 / pikepdf（候选包只装在 rc-venv）——这里是 not_run，不是绿",
)

DPI = 150.0


@pytest.fixture
def host(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    h = renderhost.RenderHost(env=env, default_timeout=60, scratch_dir=tmp_path)
    yield h
    h.close()


def _size(pdf: Path, host) -> tuple[tuple[int, int], tuple[float, float]]:
    s = host.size(pdf)
    w, h = float(s["width_pt"]), float(s["height_pt"])
    return (round(w * DPI / 72), round(h * DPI / 72)), (w, h)


def _small_bands(monkeypatch, host, size_px):
    """让这一页必须切带：单张预算压到整页的一半以下，每带只有 ~1/5 页。"""
    w, h = size_px
    host.max_pixels = w * h // 2
    monkeypatch.setattr(renderchild, "BAND_PIXELS", max(w, w * h // 5))


def test_banded_pixels_match_a_whole_page_render_within_antialiasing_noise(
    host, tmp_path, monkeypatch
):
    """条带的像素与整页一次渲染只差抗锯齿噪声（PDFium 随位图原点差 1–3 级、落在极少数像素上）——没有错位、没有接缝
    处整行丢失。PNG 与 TIFF 出自同一次条带渲染：两者像素逐个相同，child 每带只渲染一次。"""
    size, page_pt = _size(U06_PDF, host)
    whole = host.render(U06_PDF, dpi=DPI, page_size_pt=page_pt)
    _small_bands(monkeypatch, host, size)
    assert banded.needs_bands(host, size)
    before = host.requests
    facts = banded.write_banded(
        host,
        U06_PDF,
        dpi=DPI,
        size_px=size,
        page_size_pt=page_pt,
        transparent=False,
        png=tmp_path / "b.png",
        tiff=tmp_path / "b.tiff",
    )
    assert facts["bands"] >= 3 and host.requests - before == facts["bands"]
    w, h, ch, pixels = pdfread.decode_png_any((tmp_path / "b.png").read_bytes())
    assert (w, h, ch) == (whole.width, whole.height, 3)
    ref = whole.packed()
    diffs = [abs(a - b) for a, b in zip(pixels, ref) if a != b]
    assert max(diffs, default=0) <= 8 and len(diffs) <= len(ref) // 100, (
        max(diffs, default=0),
        len(diffs),
    )
    from tavotto.rendercore import inspector

    tiff_obs = inspector.observe_tiff(tmp_path / "b.tiff")
    png_obs = inspector.observe_png(tmp_path / "b.png")
    assert tiff_obs["integrity"] == png_obs["integrity"] == "verified"
    assert tiff_obs["px"] == png_obs["px"] == [w, h] and tiff_obs["dpi"] == DPI
    assert (
        abs(png_obs["dpi"] - DPI) < 0.05
    )  # PNG 的 pHYs 记的是像素 / 米的整数：150 ppi → 5906 → 150.01
    assert _tiff_pixels(tmp_path / "b.tiff") == pixels


def _tiff_pixels(path: Path) -> bytes:
    import struct
    import zlib

    data = path.read_bytes()
    (ifd,) = struct.unpack_from("<I", data, 4)
    (n,) = struct.unpack_from("<H", data, ifd)
    tags = {}
    for i in range(n):
        tag, _typ, count, val = struct.unpack_from("<HHII", data, ifd + 2 + 12 * i)
        tags[tag] = (count, val)

    def longs(tag):
        count, val = tags[tag]
        return [val] if count == 1 else list(struct.unpack_from(f"<{count}I", data, val))

    return b"".join(zlib.decompress(data[o : o + c]) for o, c in zip(longs(273), longs(279)))


def test_the_band_layout_is_fixed_so_the_same_input_gives_the_same_files(
    host, tmp_path, monkeypatch
):
    size, page_pt = _size(U06_PDF, host)
    _small_bands(monkeypatch, host, size)
    for name in ("a", "b"):
        banded.write_banded(
            host,
            U06_PDF,
            dpi=DPI,
            size_px=size,
            page_size_pt=page_pt,
            transparent=True,
            png=tmp_path / f"{name}.png",
            tiff=tmp_path / f"{name}.tiff",
        )
    assert (tmp_path / "a.png").read_bytes() == (tmp_path / "b.png").read_bytes()
    assert (tmp_path / "a.tiff").read_bytes() == (tmp_path / "b.tiff").read_bytes()
    assert pdfread.decode_png_any((tmp_path / "a.png").read_bytes())[2] == 4  # 透明底：RGBA


def test_the_page_total_cap_refuses_before_any_band_is_rendered(host, tmp_path, monkeypatch):
    size, page_pt = _size(U06_PDF, host)
    _small_bands(monkeypatch, host, size)
    host.max_total_pixels = size[0] * size[1] - 1
    before = host.requests
    with pytest.raises(renderchild.RenderChildError) as exc:
        banded.write_banded(
            host,
            U06_PDF,
            dpi=DPI,
            size_px=size,
            page_size_pt=page_pt,
            transparent=False,
            png=tmp_path / "x.png",
        )
    assert exc.value.code == "pixel_budget_exceeded" and host.requests == before


def test_the_child_checks_band_bounds_and_both_budgets_itself(host, tmp_path):
    """父侧判得了的，child 打开页面之后再判一次（ADR 0066 的两侧都判）：越界的带、超单张预算的带、超整页上限的页。"""
    size, page_pt = _size(U06_PDF, host)
    w, h = size
    with pytest.raises(renderchild.RenderChildError) as exc:
        host.render(U06_PDF, dpi=DPI, band=(h - 1, 5))  # 不给 page_size_pt：父侧判不了，child 判
    assert exc.value.code == "bad_request"
    host.max_pixels = w * 3
    with pytest.raises(renderchild.RenderChildError) as exc:
        host.render(U06_PDF, dpi=DPI, band=(0, 4))
    assert exc.value.code == "pixel_budget_exceeded"
    host.max_pixels, host.max_total_pixels = w * 10, w * h - 1
    with pytest.raises(renderchild.RenderChildError) as exc:
        host.render(U06_PDF, dpi=DPI, band=(0, 4))
    assert exc.value.code == "pixel_budget_exceeded" and host.pid is not None, "坏请求不该杀 child"
    host.max_total_pixels = renderchild.DEFAULT_MAX_TOTAL_PIXELS
    band = host.render(U06_PDF, dpi=DPI, band=(h - 4, 4))  # 最后四行：合法的带照常回来
    assert (band.width, band.height) == (w, 4)


def test_within_the_budget_nothing_changes(host):
    """预算以内不切带：整页一次，与从前逐字节相同（条带像素与整页不逐字节相同，所以这条边界是合同的一部分）。"""
    size, _ = _size(U06_PDF, host)
    assert not banded.needs_bands(host, size) and host.max_pixels == renderchild.DEFAULT_MAX_PIXELS


def test_an_export_job_over_the_budget_writes_both_formats_from_one_banded_pass(
    tmp_path, monkeypatch
):
    """产品导出路（`job.produce`）：超预算时 PNG + TIFF 由同一次条带渲染写出，尺寸按计划、检查器核得过；
    PDF 照常是矢量。"""
    import shutil

    from tavotto.engine import exportjob
    from tavotto.rendercore import fonts, job as rcjob, sources
    from tavotto.rendercore.hbshaper import HbFaceProvider

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}")
    project = tmp_path / "proj"
    (project / "figs").mkdir(parents=True)
    shutil.copy(FIXTURE / "page.pdf", project / "figs" / "Fig1.pdf")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    h = renderhost.RenderHost(env=env, default_timeout=60, scratch_dir=tmp_path)
    try:
        # 120 × 60 mm @ 300 ppi = 1417 × 709：压到一半以下的单张预算、每带 ~1/6 页
        h.max_pixels = 1417 * 709 // 2
        monkeypatch.setattr(renderchild, "BAND_PIXELS", 1417 * 120)
        exportjob.reset_for_tests()
        job = exportjob.prepare(
            {
                "scope": "canvas",
                "formats": ["pdf", "png", "tiff"],
                "filename": "Big",
                "overwrite": "replace",
                "ppi": 300,
                "canvas": {
                    "page_w_mm": 120,
                    "page_h_mm": 60,
                    "objects": [
                        {
                            "type": "panel",
                            "id": "figs/Fig1.pdf",
                            "x_mm": 5,
                            "y_mm": 5,
                            "w_mm": 110,
                            "h_mm": 50,
                        }
                    ],
                },
            },
            tmp_path / "out",
        )
        before = h.requests
        payload = exportjob.run(
            job,
            lambda j, d: rcjob.produce(
                j,
                d,
                sources=sources.StaticSourceResolver(project),
                provider=HbFaceProvider(reg),
                host=h,
            ),
        )
        rendered = h.requests - before
    finally:
        h.close()
        exportjob.reset_for_tests()
    assert payload["status"] == "done", payload
    by = {o["format"]: o for o in payload["outputs"]}
    assert by["pdf"]["vector"] is True
    assert by["png"]["dimensions"]["px"] == by["tiff"]["dimensions"]["px"] == [1417, 709]
    from tavotto.rendercore import inspector

    png, tiff = tmp_path / "out" / "Big.png", tmp_path / "out" / "Big.tiff"
    assert (
        inspector.observe_png(png)["integrity"]
        == inspector.observe_tiff(tiff)["integrity"]
        == "verified"
    )
    assert pdfread.decode_png_any(png.read_bytes())[3] == _tiff_pixels(tiff)
    bands = -(-709 // renderchild.band_rows_for(1417))
    assert bands >= 3 and rendered == bands + 1, (rendered, bands)  # +1 = 首次算键的 ping


def test_original_png_of_an_oversized_source_is_banded(tmp_path, monkeypatch):
    """按原图导出（`facade.original_png`）同样：超预算走条带、尺寸按 `size`（含 /UserUnit）× ppi 算。"""
    from tavotto.rendercore import facade

    h = facade.host()
    size, _ = _size(U06_PDF, h)
    monkeypatch.setattr(h, "max_pixels", size[0] * size[1] // 2)
    monkeypatch.setattr(renderchild, "BAND_PIXELS", max(size[0], size[0] * size[1] // 4))
    facts = facade.original_png(U06_PDF, tmp_path / "o.png", int(DPI))
    w, hgt, _ch, _px = pdfread.decode_png_any((tmp_path / "o.png").read_bytes())
    assert (facts["px_w"], facts["px_h"]) == (w, hgt) == size
