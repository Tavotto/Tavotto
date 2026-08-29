"""原图规格的事实层（`engine/originalspec.py` + `/api/panels`，ADR 0028）。

守四件事：

1. **物理密度先量后猜**：文件写了 pHYs / JFIF / Exif 就按它，`dpi_source`
   报 `metadata`；文件没写才落到 `ASSUMED_DPI`，报 `assumed`。
2. **「没写」与「写着 96」是两个答案**。MuPDF 的 `Pixmap.xres` 把这两者压成
   同一个值（实测 PyMuPDF 1.28.2），所以判据打在本模块自己的解析上——
   一旦有人把实现换回 `xres`，`no_metadata` 那条当场红。
3. **`native_*_mm` 与 `original_spec` 同源**：`/api/panels` 里那两个老字段是
   spec 的投影，不是第二次计算。改造前它们各自算各自的（位图那档现猜一个
   ppi），两个数一旦分叉，用户看到的「原图尺寸」和导出用的不是一回事。
4. **矢量报视口、位图报像素网格**：矢量不编像素数，位图不编 viewBox；
   没测量的维度一律 `None`，不许合并进相邻取值。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import originalspec, project_watch as engine_watch


# ---------------------------------------------------------------------------
# 素材构造：PNG / JPEG 都自己拼字节，才能精确控制"写没写密度"这一维
# ---------------------------------------------------------------------------
def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(
    path: Path,
    w: int = 12,
    h: int = 8,
    *,
    dpi: float | None = None,
    phys_unit: int = 1,
    alpha: bool = False,
) -> Path:
    """一张真 PNG。`dpi=None` 就不写 pHYs；`phys_unit=0` 写「只有长宽比」。"""
    ctype, px = (6, b"\xff\x00\x00\x80") if alpha else (2, b"\xff\x00\x00")
    raw = b"".join(b"\x00" + px * w for _ in range(h))
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
    if dpi is not None:
        ppm = int(round(dpi / 0.0254))
        out += _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, phys_unit))
    out += _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return path


def write_jpeg(path: Path, w: int = 12, h: int = 8, *, dpi: int | None = None) -> Path:
    """一张真 JPEG（由 PyMuPDF 编码），JFIF 密度按 `dpi` 改写；None = 无密度。"""
    src = path.parent / "_tmp_for_jpeg.png"
    write_png(src, w, h)
    data = bytearray(pymupdf.Pixmap(str(src)).tobytes("jpeg"))
    src.unlink()
    # JFIF APP0 的载荷：`JFIF\0`(5) + 版本(2) + units(1) + Xdensity(2) + Ydensity(2)
    i = data.find(b"JFIF\x00")
    assert i > 0, "PyMuPDF 编出来的 JPEG 没有 JFIF 段，用例前提不成立"
    data[i + 7] = 0 if dpi is None else 1  # 0 = 只有长宽比；1 = 每英寸
    struct.pack_into(">HH", data, i + 8, 1 if dpi is None else dpi, 1 if dpi is None else dpi)
    path.write_bytes(bytes(data))
    return path


def add_exif(path: Path, *, x_dpi: int, y_dpi: int, unit: int = 2) -> Path:
    """在 SOI 之后插一段 APP1/Exif（IFD0 的 XResolution / YResolution / 单位）。

    单位 2 = 英寸，3 = 厘米，1 = 无单位（只有长宽比）。
    """
    data = path.read_bytes()
    tiff = bytearray(b"MM\x00\x2a\x00\x00\x00\x08")  # 大端 TIFF 头，IFD0 在 8
    entries = [
        (0x011A, 5, 1, None),  # XResolution（RATIONAL，值在偏移处）
        (0x011B, 5, 1, None),  # YResolution
        (0x0128, 3, 1, unit),  # ResolutionUnit（SHORT，值内联）
    ]
    ifd_at = 8
    values_at = ifd_at + 2 + len(entries) * 12 + 4
    tiff += b"\x00" * (2 + len(entries) * 12 + 4)
    import struct as _s

    _s.pack_into(">H", tiff, ifd_at, len(entries))
    rationals = b""
    for i, (tag, typ, cnt, inline) in enumerate(entries):
        at = ifd_at + 2 + i * 12
        _s.pack_into(">HHI", tiff, at, tag, typ, cnt)
        if inline is None:
            _s.pack_into(">I", tiff, at + 8, values_at + len(rationals))
            rationals += _s.pack(">II", x_dpi if tag == 0x011A else y_dpi, 1)
        else:
            _s.pack_into(">HH", tiff, at + 8, inline, 0)
    _s.pack_into(">I", tiff, ifd_at + 2 + len(entries) * 12, 0)  # 没有下一个 IFD
    tiff += rationals
    payload = b"Exif\x00\x00" + bytes(tiff)
    seg = b"\xff\xe1" + _s.pack(">H", len(payload) + 2) + payload
    path.write_bytes(data[:2] + seg + data[2:])
    return path


def write_pdf(path: Path, w_pt: float = 200.0, h_pt: float = 100.0) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=w_pt, height=h_pt)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()
    return path


def _spec(path: Path, kind: str) -> dict:
    from tavotto import pdfbackend

    return originalspec.asset_spec(path, kind, pdfbackend.probe_asset(path, kind))


# ---------------------------------------------------------------------------
# 一、位图：量得到就报 metadata
# ---------------------------------------------------------------------------
class TestRasterDpi:
    def test_png_phys_is_read_as_metadata(self, tmp_path):
        spec = _spec(write_png(tmp_path / "a.png", 600, 400, dpi=300), "raster")
        assert (spec["dpi"], spec["dpi_source"]) == (300.0, "metadata")
        assert (spec["px_w"], spec["px_h"]) == (600, 400)
        assert spec["logical_w_mm"] == pytest.approx(600 / 300 * 25.4, abs=1e-3)

    def test_png_without_phys_falls_back_and_says_so(self, tmp_path):
        spec = _spec(write_png(tmp_path / "a.png", 600, 400), "raster")
        assert spec["dpi_source"] == "assumed"
        assert spec["dpi"] == originalspec.ASSUMED_DPI[".png"]

    def test_ninety_six_dpi_is_metadata_not_the_absence_of_it(self, tmp_path):
        """「没写」与「写着 96」必须分得开。

        MuPDF 的 `Pixmap.xres` 对两者一律回 96 —— 把它当密度来源的话，
        本用例与上一条会给出同一个答案，而它们问的是两件不同的事。
        """
        written = _spec(write_png(tmp_path / "w.png", 96, 96, dpi=96), "raster")
        absent = _spec(write_png(tmp_path / "n.png", 96, 96), "raster")
        assert (written["dpi"], written["dpi_source"]) == (96.0, "metadata")
        assert absent["dpi_source"] == "assumed"
        assert written["logical_w_mm"] != absent["logical_w_mm"]

    def test_phys_without_unit_is_not_a_density(self, tmp_path):
        """pHYs 的单位字节为 0 时只声明长宽比，拿它当 dpi 会算出荒唐的尺寸。"""
        spec = _spec(write_png(tmp_path / "a.png", 12, 8, dpi=300, phys_unit=0), "raster")
        assert spec["dpi_source"] == "assumed"

    def test_jpeg_jfif_density(self, tmp_path):
        spec = _spec(write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=150), "raster")
        assert (spec["dpi"], spec["dpi_source"]) == (150.0, "metadata")

    def test_jpeg_without_density_uses_the_non_png_assumption(self, tmp_path):
        spec = _spec(write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=None), "raster")
        assert spec["dpi_source"] == "assumed"
        assert spec["dpi"] == originalspec.ASSUMED_DPI_DEFAULT

    def test_jpeg_exif_resolution_is_read_when_jfif_says_nothing(self, tmp_path):
        """JFIF 只给长宽比时，Exif 的分辨率才是这张图写下的物理密度。"""
        jpg = write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=None)
        add_exif(jpg, x_dpi=240, y_dpi=240, unit=2)
        spec = _spec(jpg, "raster")
        assert (spec["dpi"], spec["dpi_source"]) == (240.0, "metadata")

    def test_exif_in_centimetres_is_converted(self, tmp_path):
        jpg = write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=None)
        add_exif(jpg, x_dpi=100, y_dpi=100, unit=3)
        assert _spec(jpg, "raster")["dpi"] == pytest.approx(254.0, abs=1e-3)

    def test_exif_without_a_unit_is_only_an_aspect_ratio(self, tmp_path):
        jpg = write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=None)
        add_exif(jpg, x_dpi=72, y_dpi=72, unit=1)
        assert _spec(jpg, "raster")["dpi_source"] == "assumed"

    def test_jfif_density_wins_over_exif(self, tmp_path):
        """两个出处都写了就按 JFIF——**判据要挡得住"随便挑一个"**。"""
        jpg = write_jpeg(tmp_path / "a.jpg", 300, 200, dpi=150)
        add_exif(jpg, x_dpi=240, y_dpi=240, unit=2)
        assert _spec(jpg, "raster")["dpi"] == 150.0

    def test_unreadable_file_degrades_to_assumed_not_to_a_crash(self, tmp_path):
        missing = tmp_path / "gone.png"
        assert originalspec.raster_dpi(missing) is None

    def test_alpha_is_reported_from_the_pixmap(self, tmp_path):
        opaque = _spec(write_png(tmp_path / "o.png", 12, 8), "raster")
        clear = _spec(write_png(tmp_path / "c.png", 12, 8, alpha=True), "raster")
        assert opaque["transparent"] is False
        assert clear["transparent"] is True


# ---------------------------------------------------------------------------
# 二、矢量：报视口，不编像素
# ---------------------------------------------------------------------------
class TestVectorSpec:
    def test_pdf_reports_viewport_and_no_pixel_grid(self, tmp_path):
        spec = _spec(write_pdf(tmp_path / "a.pdf", 288.0, 144.0), "pdf")
        assert spec["source_kind"] == "vector"
        assert spec["viewport_pt"] == [288.0, 144.0]
        assert spec["logical_w_mm"] == pytest.approx(288 * 25.4 / 72, abs=1e-3)
        assert (spec["px_w"], spec["px_h"], spec["dpi"]) == (None, None, None)

    def test_pdf_dpi_and_transparency_are_unmeasured_not_zero(self, tmp_path):
        """没测量的维度报 `unknown` / `None`，不许合成一个看起来像事实的值。"""
        spec = _spec(write_pdf(tmp_path / "a.pdf"), "pdf")
        assert spec["dpi_source"] == "unknown"
        assert spec["transparent"] is None


# ---------------------------------------------------------------------------
# 三、/api/panels：老字段是 spec 的投影
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


def _open(client, figs: Path) -> None:
    client.post("/api/projects/open", json={"path": str(figs), "default": True})


class TestPanelsProjection:
    def test_native_mm_equals_the_spec_logical_size(self, client, tmp_path):
        figs = tmp_path / "figs"
        write_pdf(figs / "v.pdf", 288.0, 144.0)
        write_png(figs / "r.png", 1200, 900, dpi=300)
        _open(client, figs)

        panels = {p["id"]: p for p in client.get("/api/panels").get_json()["panels"]}
        for entry in panels.values():
            spec = entry["original_spec"]
            assert entry["native_w_mm"] == spec["logical_w_mm"]
            assert entry["native_h_mm"] == spec["logical_h_mm"]

        assert panels["r.png"]["original_spec"]["dpi_source"] == "metadata"
        assert panels["r.png"]["px_w"] == 1200
        assert panels["v.pdf"]["original_spec"]["source_kind"] == "vector"

    def test_assumed_density_keeps_the_pre_change_numbers(self, client, tmp_path):
        """没有密度元数据的 PNG，尺寸与改造前**逐位相同**（600 ppi）。

        老项目里已经摆好的面板不能因为这次改造换个大小；变的只是现在
        `dpi_source` 会说出来它是假定的。
        """
        figs = tmp_path / "figs"
        write_png(figs / "r.png", 1200, 900)
        _open(client, figs)

        entry = client.get("/api/panels").get_json()["panels"][0]
        assert entry["native_w_mm"] == pytest.approx(1200 / 600 * 25.4, abs=1e-3)
        assert entry["original_spec"]["dpi_source"] == "assumed"
