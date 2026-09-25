"""TIFF 素材（issue #534）：进得了素材库、渲染后端画得对、范围之外如实拒绝。

主语分三层：

1. **支持范围的判据**（`tavotto/tiffprobe.py`）——只读头，纯标准库。夹具用本文件里的
   `_tiff()` 手写（未压缩单条带），与被测解析器没有一行共享代码；
2. **渲染后端（RenderCore，U10 起唯一）**经**真的 Flask 端点**：`/api/panels` →
   `/api/render` 缩略图 → `/api/file` → 画布合成导出 → 原图导出。缺 RenderCore 的包或批准字体时
   skip（skip 不是绿：在装了 `.[rendercore]` 的 venv 里真跑过才算）；
3. **范围之外**：不进面板表、进 `unsupported`；缩略图 / 原文件 / 导出都报同一个 code，不出错图。

像素判据一律取「左红右蓝」两块纯色的中心点：缩放滤波会让边缘像素漂，中心不会。
"""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m, pdfbackend, tiffprobe
from tavotto.engine import exportjob, originalspec, project_watch as engine_watch

HAS_CANDIDATE = all(
    importlib.util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
HAS_PIL = importlib.util.find_spec("PIL") is not None

W, H = 64, 40
RED, BLUE = (200, 30, 30), (30, 30, 200)


# ---------------------------------------------------------------------------
# 夹具：手写 TIFF（未压缩、单条带；判据只读头，像素给得真实，后端能真解）
# ---------------------------------------------------------------------------
def _tiff(
    path: Path,
    pixels: bytes,
    *,
    spp: int,
    bits: int = 8,
    photometric: int = 2,
    compression: int = 1,
    sample_format: int | None = None,
    planar: int | None = None,
    extra: int | None = None,
    resolution: tuple[float, int] | None = None,
    big_endian: bool = False,
    bigtiff: bool = False,
) -> Path:
    e = ">" if big_endian else "<"
    if bigtiff:
        # 头部就够判据用：魔数 43 之后不往下解析
        path.write_bytes((b"MM\x00+" if big_endian else b"II+\x00") + b"\x00" * 12)
        return path
    tags: list[tuple[int, int, list]] = [
        (256, 4, [W]),
        (257, 4, [H]),
        (258, 3, [bits] * spp),
        (259, 3, [compression]),
        (262, 3, [photometric]),
        (273, 4, [0]),  # 条带偏移，下面回填
        (277, 3, [spp]),
        (278, 4, [H]),
        (279, 4, [len(pixels)]),
    ]
    if resolution is not None:
        tags += [(282, 5, [resolution[0]]), (283, 5, [resolution[0]]), (296, 3, [resolution[1]])]
    if planar is not None:
        tags.append((284, 3, [planar]))
    if extra is not None:
        tags.append((338, 3, [extra]))
    if sample_format is not None:
        tags.append((339, 3, [sample_format] * spp))
    tags.sort()
    ifd_off = 8
    ifd_len = 2 + 12 * len(tags) + 4
    ext_off = ifd_off + ifd_len
    ext = b""
    entries = b""
    for tag, typ, vals in tags:
        if typ == 5:
            data = b"".join(struct.pack(e + "II", int(round(v * 1000)), 1000) for v in vals)
        else:
            data = struct.pack(e + ("H" if typ == 3 else "I") * len(vals), *vals)
        if len(data) <= 4:
            field = data.ljust(4, b"\x00")
        else:
            field = struct.pack(e + "I", ext_off + len(ext))
            ext += data
        entries += struct.pack(e + "HHI", tag, typ, len(vals)) + field
    pix_off = ext_off + len(ext)
    # 回填条带偏移
    i = [t for t, _, _ in tags].index(273)
    start = i * 12
    entries = entries[: start + 8] + struct.pack(e + "I", pix_off) + entries[start + 12 :]
    head = (b"MM\x00*" if big_endian else b"II*\x00") + struct.pack(e + "I", ifd_off)
    ifd = struct.pack(e + "H", len(tags)) + entries + struct.pack(e + "I", 0)
    path.write_bytes(head + ifd + ext + pixels)
    return path


def _halves(left: tuple, right: tuple, *, fmt: str = "B") -> bytes:
    """左半 `left`、右半 `right` 的逐像素样本（交错存放）。"""
    row = b"".join(struct.pack(f"<{len(left)}{fmt}", *left) for _ in range(W // 2))
    row += b"".join(struct.pack(f"<{len(right)}{fmt}", *right) for _ in range(W // 2))
    return row * H


def _rgb(path: Path, **kw) -> Path:
    return _tiff(path, _halves(RED, BLUE), spp=3, **kw)


# ---------------------------------------------------------------------------
# 1. 判据：支持范围
# ---------------------------------------------------------------------------
SUPPORTED = {
    "rgb8": lambda p: _rgb(p),
    "rgb8_big_endian": lambda p: _tiff(p, _halves(RED, BLUE), spp=3, big_endian=True),
    "rgba8": lambda p: _tiff(p, _halves(RED + (255,), BLUE + (128,)), spp=4, extra=2),
    "gray8": lambda p: _tiff(p, _halves((81,), (49,)), spp=1, photometric=1),
    "gray_alpha8": lambda p: _tiff(p, _halves((81, 255), (49, 255)), spp=2, photometric=1, extra=2),
    "gray16": lambda p: _tiff(
        p, _halves((200 * 256 + 7,), (30 * 256 + 7,), fmt="H"), spp=1, bits=16, photometric=1
    ),
    "rgb16": lambda p: _tiff(
        p,
        _halves(tuple(v * 256 + 7 for v in RED), tuple(v * 256 + 7 for v in BLUE), fmt="H"),
        spp=3,
        bits=16,
    ),
    "unsigned_sample_format_explicit": lambda p: _rgb(p, sample_format=1),
}

UNSUPPORTED = {
    "float32": (
        lambda p: _tiff(
            p, _halves((0.8,), (0.1,), fmt="f"), spp=1, bits=32, photometric=1, sample_format=3
        ),
        "tiff_sample_format",
    ),
    "signed16": (
        lambda p: _tiff(
            p, _halves((100,), (-100,), fmt="h"), spp=1, bits=16, photometric=1, sample_format=2
        ),
        "tiff_sample_format",
    ),
    "cmyk8": (
        lambda p: _tiff(p, _halves((0, 200, 200, 55), (200, 200, 0, 55)), spp=4, photometric=5),
        "tiff_color_space",
    ),
    "lab8": (
        lambda p: _tiff(p, _halves((50, 0, 0), (50, 0, 0)), spp=3, photometric=8),
        "tiff_color_space",
    ),
    "ycbcr_without_jpeg": (lambda p: _rgb(p, photometric=6), "tiff_color_space"),
    "rgb32u": (
        lambda p: _tiff(
            p,
            _halves(tuple(v << 24 for v in RED), tuple(v << 24 for v in BLUE), fmt="I"),
            spp=3,
            bits=32,
        ),
        "tiff_bit_depth",
    ),
    "rgb1": (lambda p: _tiff(p, b"\x00" * (W * H * 3 // 8), spp=3, bits=1), "tiff_bit_depth"),
    "zstd": (lambda p: _rgb(p, compression=50000), "tiff_compression"),
    "planar": (lambda p: _rgb(p, planar=2), "tiff_planar"),
    "bigtiff": (lambda p: _tiff(p, b"", spp=3, bigtiff=True), "tiff_bigtiff"),
    "truncated": (lambda p: (p.write_bytes(_rgb(p).read_bytes()[:20]), p)[1], "tiff_unreadable"),
    "not_a_tiff": (
        lambda p: (p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64), p)[1],
        "tiff_unreadable",
    ),
}


@pytest.mark.parametrize("name", sorted(SUPPORTED))
def test_supported_tiffs_pass_the_gate(tmp_path, name):
    header = tiffprobe.check(SUPPORTED[name](tmp_path / f"{name}.tif"))
    assert (header.width, header.height) == (W, H)


@pytest.mark.parametrize("name", sorted(UNSUPPORTED))
def test_out_of_range_tiffs_are_refused_with_their_code(tmp_path, name):
    make, code = UNSUPPORTED[name]
    with pytest.raises(tiffprobe.UnsupportedTiff) as info:
        tiffprobe.check(make(tmp_path / f"{name}.tif"))
    assert info.value.code == code
    assert info.value.params == {"file": f"{name}.tif"}


def test_every_refusal_code_has_a_case():
    """闭集里每个 code 都有一条用例真的走到——少一条就是一个没人量过的拒绝。"""
    assert {code for _make, code in UNSUPPORTED.values()} == set(tiffprobe.ERROR_CODES)


# ---------------------------------------------------------------------------
# 2. 物理密度：TIFF 的分辨率标签进 `original_spec`（`engine/originalspec` 是唯一出处）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("resolution", "dpi", "source"),
    [
        ((300.0, 2), 300.0, "metadata"),  # 每英寸
        ((100.0, 3), 254.0, "metadata"),  # 每厘米
        ((72.0, 1), 300.0, "assumed"),  # 单位 1 = 只有纵横比，不是密度
        (None, 300.0, "assumed"),  # 没写 → 按「其余 300」假定，并且说出来
    ],
)
def test_tiff_density_comes_from_its_resolution_tags(tmp_path, resolution, dpi, source):
    p = _rgb(tmp_path / "d.tif", resolution=resolution)
    spec = originalspec.asset_spec(p, "raster", {"px_w": W, "px_h": H, "alpha": False})
    assert spec["dpi_source"] == source
    assert spec["dpi"] == pytest.approx(dpi, abs=0.01)
    assert spec["logical_w_mm"] == pytest.approx(W / dpi * 25.4, abs=0.01)


# ---------------------------------------------------------------------------
# 3. 真端点
# ---------------------------------------------------------------------------
@pytest.fixture(params=[pdfbackend.BACKEND_RENDERCORE])
def backend(request, monkeypatch):
    name = request.param
    if name == pdfbackend.BACKEND_RENDERCORE:
        if not HAS_CANDIDATE:
            pytest.skip("候选包未装（not_run，不是绿）")
        from tavotto.rendercore import fonts

        reg = fonts.FontRegistry.discover()
        if reg.missing:
            pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, name)
    yield name
    if name == pdfbackend.BACKEND_RENDERCORE:
        from tavotto.rendercore import renderhost

        renderhost.shutdown_shared()


@pytest.fixture
def client(tmp_path, monkeypatch, backend):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    exportjob.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()
    exportjob.reset_for_tests()


def _project(tmp_path: Path, **files) -> Path:
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    for name, make in files.items():
        make(figs / name.replace("__", "."))
    m.open_project(str(figs))
    return figs


def _px(png: bytes, fx: float, fy: float = 0.5) -> tuple[int, int, int]:
    pix = pymupdf.Pixmap(png)
    if pix.alpha or pix.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix, 0)
    return tuple(pix.pixel(int(pix.width * fx), int(pix.height * fy)))[:3]


def _near(got, want, tol=6):
    return all(abs(a - b) <= tol for a, b in zip(got, want, strict=True))


def _pdf_px(path: Path) -> tuple[tuple, tuple]:
    with pymupdf.open(path) as doc:
        png = doc[0].get_pixmap(dpi=72, alpha=False).tobytes("png")
    return _px(png, 0.25), _px(png, 0.75)


def _canvas(obj_id: str, **over) -> dict:
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 64,
            "page_h_mm": 40,
            "objects": [
                {"type": "panel", "id": obj_id, "x_mm": 0, "y_mm": 0, "w_mm": 64, "h_mm": 40}
            ],
        },
    }
    spec.update(over)
    return spec


def _out(body: dict, fmt: str) -> dict:
    return next(o for o in body["outputs"] if o["format"] == fmt)


def test_a_tiff_goes_all_the_way_from_the_folder_to_an_exported_pdf(client, tmp_path):
    """端到端（issue #534 的完成定义）：项目里放一张 TIFF → 出现在 `/api/panels` → 缩略图 200 且是
    PNG → 原文件 200 → 放上画布导出 PDF / PNG 成功，颜色对。"""
    _project(tmp_path, fig__tif=lambda p: _rgb(p, resolution=(300.0, 2)))

    panels = client.get("/api/panels").get_json()
    entry = next(p for p in panels["panels"] if p["id"] == "fig.tif")
    assert entry["kind"] == "raster"
    assert (entry["px_w"], entry["px_h"]) == (W, H)
    assert entry["original_spec"]["dpi_source"] == "metadata"
    assert panels["unsupported"] == []

    thumb = client.get("/api/render?id=fig.tif&w=200")
    assert thumb.status_code == 200
    assert thumb.mimetype == "image/png"
    assert thumb.data[:8] == b"\x89PNG\r\n\x1a\n"
    assert _near(_px(thumb.data, 0.25), RED) and _near(_px(thumb.data, 0.75), BLUE)

    raw = client.get("/api/file?id=fig.tif")
    assert raw.status_code == 200
    assert raw.mimetype == "image/tiff"

    resp = client.post("/api/export", json=_canvas("fig.tif"))
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["status"] == "done", body
    left, right = _pdf_px(Path(body["export_dir"]) / _out(body, "pdf")["name"])
    assert _near(left, RED) and _near(right, BLUE), (left, right)
    png = (Path(body["export_dir"]) / _out(body, "png")["name"]).read_bytes()
    assert _near(_px(png, 0.25), RED) and _near(_px(png, 0.75), BLUE)


@pytest.mark.parametrize("fmt", ["pdf", "png", "tiff"])
def test_original_export_of_a_tiff_keeps_its_pixel_grid(client, tmp_path, fmt):
    """原图导出：TIFF 源保持自己的像素网格（不按导出 ppi 重采样），三个容器都出得来。"""
    _project(tmp_path, fig__tif=lambda p: _rgb(p, resolution=(300.0, 2)))
    spec = {
        "scope": "original",
        "filename": "orig",
        "formats": [fmt],
        "ppi": 600,
        "overwrite": "replace",
        "original": {"figure_id": "fig.tif", "source_kind": "raster"},
    }
    resp = client.post("/api/export", json=spec)
    body = resp.get_json()
    assert resp.status_code == 200, body
    out = _out(body, fmt)
    assert out["status"] == "done", out
    path = Path(body["export_dir"]) / out["name"]
    if fmt == "pdf":
        left, right = _pdf_px(path)
        with pymupdf.open(path) as doc:
            # 300 dpi 的 64 × 40 像素 = 15.36 × 9.6 pt
            assert doc[0].rect.width == pytest.approx(W / 300 * 72, abs=0.05)
    else:
        pix = pymupdf.Pixmap(str(path))  # 按像素读，不经「页面」（页面尺寸会按密度换算）
        assert (pix.width, pix.height) == (W, H)
        png = (
            pymupdf.Pixmap(pymupdf.csRGB, pix, 0).tobytes("png")
            if pix.alpha
            else pix.tobytes("png")
        )
        left, right = _px(png, 0.25), _px(png, 0.75)
    assert _near(left, RED) and _near(right, BLUE), (left, right)


@pytest.mark.parametrize("name", sorted(SUPPORTED))
def test_every_supported_layout_renders_the_same_picture_on_both_backends(client, tmp_path, name):
    """支持范围里的每一格，缩略图与画布合成都画出同一张图（取两块纯色的中心）。"""
    _project(tmp_path, v__tif=SUPPORTED[name])
    if name.startswith("gray"):
        want = ((200, 200, 200), (30, 30, 30)) if name == "gray16" else ((81,) * 3, (49,) * 3)
    elif name == "rgba8":
        want = (RED, (142, 142, 227))  # 右半 alpha 128：缩略图与导出都压在白底上，alpha 没被丢掉
    else:
        want = (RED, BLUE)
    thumb = client.get("/api/render?id=v.tif&w=200")
    assert thumb.status_code == 200, thumb.get_json()
    got = (_px(thumb.data, 0.25), _px(thumb.data, 0.75))
    assert _near(got[0], want[0]) and _near(got[1], want[1]), got
    body = client.post("/api/export", json=_canvas("v.tif", formats=["pdf"])).get_json()
    assert body["status"] == "done", body
    got = _pdf_px(Path(body["export_dir"]) / _out(body, "pdf")["name"])
    assert _near(got[0], want[0]) and _near(got[1], want[1]), got


@pytest.mark.skipif(not HAS_PIL, reason="造压缩 / 多页 TIFF 要 Pillow（not_run，不是绿）")
@pytest.mark.parametrize(
    "variant",
    ["tiff_lzw", "tiff_adobe_deflate", "packbits", "jpeg", "multipage", "palette"],
)
def test_compressed_and_multipage_tiffs_render_on_both_backends(client, tmp_path, variant):
    """常见压缩（Pillow 写，libtiff 口径）与多页（只取首页）都画得对。"""
    from PIL import Image

    im = Image.frombytes("RGB", (W, H), _halves(RED, BLUE))

    def make(p: Path) -> None:
        if variant == "multipage":
            green = Image.new("RGB", (W, H), (30, 200, 30))
            im.save(p, save_all=True, append_images=[green])
        elif variant == "palette":
            im.convert("P", palette=Image.Palette.ADAPTIVE, colors=4).save(p)
        else:
            im.save(p, compression=variant)

    _project(tmp_path, v__tif=make)
    thumb = client.get("/api/render?id=v.tif&w=200")
    assert thumb.status_code == 200
    tol = 12 if variant in ("jpeg", "palette") else 6
    assert _near(_px(thumb.data, 0.25), RED, tol) and _near(_px(thumb.data, 0.75), BLUE, tol)
    body = client.post("/api/export", json=_canvas("v.tif", formats=["pdf"])).get_json()
    assert body["status"] == "done", body
    left, right = _pdf_px(Path(body["export_dir"]) / _out(body, "pdf")["name"])
    assert _near(left, RED, tol) and _near(right, BLUE, tol), (left, right)


# ---------------------------------------------------------------------------
# 4. 范围之外：列出来、说清楚、谁都不画
# ---------------------------------------------------------------------------
def test_an_out_of_range_tiff_is_listed_as_unsupported_not_as_a_panel(client, tmp_path):
    make, _code = UNSUPPORTED["float32"]
    _project(tmp_path, ok__tif=_rgb, bad__tif=make)
    body = client.get("/api/panels").get_json()
    assert [p["id"] for p in body["panels"]] == ["ok.tif"]
    assert body["unsupported"] == [
        {"id": "bad.tif", "name": "bad.tif", "folder": ".", "code": "tiff_sample_format"}
    ]


@pytest.mark.parametrize("path", ["/api/render?id=bad.tif&w=200", "/api/file?id=bad.tif"])
def test_reading_an_out_of_range_tiff_is_a_422_with_its_code(client, tmp_path, path):
    make, code = UNSUPPORTED["cmyk8"]
    _project(tmp_path, bad__tif=make)
    resp = client.get(path)
    assert resp.status_code == 422
    assert resp.get_json()["code"] == code
    assert resp.get_json()["params"] == {"file": "bad.tif"}


@pytest.mark.parametrize("scope", ["canvas", "original"])
def test_exporting_an_out_of_range_tiff_fails_with_its_code(client, tmp_path, scope):
    """导出作业里也是同一个 code——不是 `export_failed` 加一串异常，更不是一张错图。"""
    make, code = UNSUPPORTED["float32"]
    _project(tmp_path, bad__tif=make)
    if scope == "canvas":
        spec = _canvas("bad.tif", formats=["pdf"])
    else:
        spec = {
            "scope": "original",
            "filename": "orig",
            "formats": ["png"],
            "overwrite": "replace",
            "original": {"figure_id": "bad.tif", "source_kind": "raster"},
        }
    body = client.post("/api/export", json=spec).get_json()
    assert body["status"] == "failed", body
    assert body["code"] == code
    assert body["params"] == {"file": "bad.tif"}
    assert not list((tmp_path / "figs" / "tavottofile").rglob("orig*"))


def _scripted_tiff_project(tmp_path: Path) -> Path:
    """一张 TIFF + 注册表里认领它 stem 的脚本（写回 / 版本恢复够得着它）。"""
    figs = _project(tmp_path, Fig1__tif=_rgb)
    (figs / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "fig1.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["Fig1"]}
                },
            }
        ),
        encoding="utf-8",
    )
    (figs / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    m.reset_projects()
    m.open_project(str(figs))
    return figs


def _no_worker(*_a, **_k):
    raise AssertionError("拒绝必须在起 worker 之前")


def test_write_back_refuses_a_panel_it_would_not_rewrite(client, tmp_path, monkeypatch):
    """写回只重写同名 .pdf / .png。画布上这张是 TIFF 时它自己一个字节都不会变——以前会报成功、
    记下基线；现在在起 worker 之前如实拒绝。"""
    figs = _scripted_tiff_project(tmp_path)
    before = (figs / "Fig1.tif").read_bytes()
    monkeypatch.setattr(m, "_safe_worker", _no_worker)
    resp = client.post("/api/engine/update_source", json={"id": "Fig1.tif", "patches": []})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "write_back_format_unsupported"
    assert resp.get_json()["params"] == {"format": "TIF"}
    assert (figs / "Fig1.tif").read_bytes() == before


def test_history_restore_refuses_a_panel_it_would_not_rewrite(client, tmp_path, monkeypatch):
    """版本恢复是第二条写回路（Codex #561）：历史来自这张图还是 PDF / PNG 的时候，现在它是 TIFF。
    以前会重放、建备份目录、回 `updated: []` 的成功，再追加一条基线——一个字节都没写。现在与
    update_source 同一个 code，起 worker 之前拒，基线一条不加。"""
    figs = _scripted_tiff_project(tmp_path)
    m.append_baked("Fig1", [{"gid": "axes_0.title", "prop": "text", "value": "A"}])
    versions_before = len(m.load_baked()["Fig1"]["versions"])
    before = (figs / "Fig1.tif").read_bytes()
    monkeypatch.setattr(m, "_engine_worker", _no_worker)
    monkeypatch.setattr(m, "_safe_worker", _no_worker)
    resp = client.post("/api/engine/history/restore", json={"id": "Fig1.tif", "n": 0})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "write_back_format_unsupported"
    assert resp.get_json()["params"] == {"format": "TIF"}
    assert (figs / "Fig1.tif").read_bytes() == before
    assert len(m.load_baked()["Fig1"]["versions"]) == versions_before


def test_write_source_files_is_the_choke_point(client, tmp_path, monkeypatch):
    """两条写回路的早检之外，事务本身也拒：将来多一个调用方忘了早检，也绝不「零个目标、报成功」。
    拒绝发生在 prepare（校验 / 备份）之前。"""
    figs = _scripted_tiff_project(tmp_path)

    def no_prepare(*_a, **_k):
        raise AssertionError("拒绝必须在 prepare 之前")

    monkeypatch.setattr(m, "_write_back_prepare", no_prepare)
    with pytest.raises(m.WriteBackFormatError) as info:
        m._write_source_files(figs / "Fig1.tif", [], object())
    assert info.value.format == "TIF"


# ---------------------------------------------------------------------------
# 5. 坏头：文件里读出来的数不许驱动巨大的分配 / 读长度（Codex #561）
# ---------------------------------------------------------------------------
def _raw(path: Path, entries: list[tuple[int, int, int, bytes]], *, count=None, tail=b"") -> Path:
    """手写一个 IFD：`entries` 是 `(tag, type, count, 4 字节值域)`；`count` 可以谎报条目数。"""
    body = b"".join(struct.pack("<HHI", t, ty, n) + v.ljust(4, b"\x00") for t, ty, n, v in entries)
    n = len(entries) if count is None else count
    path.write_bytes(
        b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", n) + body + b"\x00" * 4 + tail
    )
    return path


def _base(over: dict | None = None) -> list[tuple[int, int, int, bytes]]:
    tags = {
        256: (3, 1, struct.pack("<H", W)),
        257: (3, 1, struct.pack("<H", H)),
        258: (3, 1, struct.pack("<H", 8)),
        262: (3, 1, struct.pack("<H", 1)),
        277: (3, 1, struct.pack("<H", 1)),
    }
    tags.update(over or {})
    return [(t, *v) for t, v in sorted(tags.items())]


def _bounded(path: Path, code: str | None = "tiff_unreadable", *, cap: int = 1 << 20) -> None:
    """判据给出 `code`，且整个过程的 Python 分配峰值在 `cap` 字节以内。"""
    import tracemalloc

    tracemalloc.start()
    try:
        if code is None:
            tiffprobe.check(path)
        else:
            with pytest.raises(tiffprobe.UnsupportedTiff) as info:
                tiffprobe.check(path)
            assert info.value.code == code
    finally:
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert peak < cap, f"分配峰值 {peak} 字节"


def test_samples_per_pixel_bomb_is_refused_before_any_allocation(tmp_path):
    """`SamplesPerPixel` = 5000 万（LONG）：没有闸就先建 5000 万个元素的默认元组（~400 MB）。"""
    _bounded(_raw(tmp_path / "spp.tif", _base({277: (4, 1, struct.pack("<I", 50_000_000))})))


def test_samples_per_pixel_0xffffffff_is_unreadable(tmp_path):
    """Codex 的原例：LONG 0xffffffff。"""
    _bounded(_raw(tmp_path / "spp.tif", _base({277: (4, 1, struct.pack("<I", 0xFFFFFFFF))})))


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ((3, 1, struct.pack("<H", 0)), "tiff_unreadable"),  # 0 个样本
        ((3, 1, struct.pack("<H", 17)), "tiff_unreadable"),  # 超过解析上限
        ((2, 4, b"abc\x00"), "tiff_unreadable"),  # 类型不是整数
        ((3, 1, struct.pack("<H", 5)), "tiff_color_space"),  # 读得懂，但不在支持范围
    ],
)
def test_samples_per_pixel_outside_the_parse_range(tmp_path, value, code):
    _bounded(_raw(tmp_path / "spp.tif", _base({277: value})), code)


def test_ifd_entry_count_that_does_not_fit_the_file_is_unreadable(tmp_path):
    """条目数谎报成 65535：`2 + 12 × 65535` 字节不在文件里，先核再读（不核的话先分配 768 KiB 的读缓冲）。"""
    _bounded(_raw(tmp_path / "n.tif", _base(), count=0xFFFF), cap=256 << 10)


def test_ifd_offset_past_the_end_is_unreadable(tmp_path):
    p = tmp_path / "off.tif"
    p.write_bytes(b"II*\x00" + struct.pack("<I", 0x7FFFFFFF))
    _bounded(p)


@pytest.mark.parametrize(
    ("tag", "typ"),
    [(273, 4), (279, 4), (324, 4), (325, 4), (320, 3), (258, 3)],
    ids=[
        "StripOffsets",
        "StripByteCounts",
        "TileOffsets",
        "TileByteCounts",
        "ColorMap",
        "BitsPerSample",
    ],
)
def test_a_value_area_past_the_end_is_unreadable(tmp_path, tag, typ):
    """个数写成 2^30 的条带 / 瓦片表、ColorMap、BitsPerSample：值区越过文件尾，在本模块拦下，
    不留给解码器去分配。"""
    _bounded(_raw(tmp_path / "v.tif", _base({tag: (typ, 1 << 30, struct.pack("<I", 64))})))


def test_a_bomb_in_the_project_is_listed_not_fatal(client, tmp_path):
    """扫项目的那一刻：坏头进 `unsupported`（tiff_unreadable），好图照列，服务不倒。"""
    _project(
        tmp_path,
        ok__tif=_rgb,
        bomb__tif=lambda p: _raw(p, _base({277: (4, 1, struct.pack("<I", 0xFFFFFFFF))})),
    )
    body = client.get("/api/panels").get_json()
    assert [p["id"] for p in body["panels"]] == ["ok.tif"]
    assert [(u["id"], u["code"]) for u in body["unsupported"]] == [("bomb.tif", "tiff_unreadable")]
