"""最终产物验收：检查**准备交付的那个文件本身**，不是内存里的 figsize，也不是预览图。

规范化事务的最后一步（ADR 0051 §8）。每种格式按自己的精度验：

* **PDF** —— 页面物理尺寸（pt → mm，容差 `SIZE_TOL_MM`）+ 首页用到的字体名
  （`pdfbackend.pdf_fonts`）；
* **SVG** —— 根元素 `width` / `height`（pt / mm / in / px 换算）与 `viewBox` 的关系；
  文字默认被 matplotlib 转成了路径（`svg.fonttype = "path"`），字体**查不了**，
  如实回 None；
* **PNG** —— 像素尺寸 = round(英寸 × dpi)（整数取整，容差 1 px）+ `pHYs` 里的密度
  （`originalspec.raster_dpi`）；字体查不了（位图）；
* **TIFF** —— 像素尺寸；分辨率标签不解析（诚实地标「未核验」）；
* **EPS** —— `%%BoundingBox`（pt）；字体按 `%%BeginFont` / `/FontName` 扫，扫不到回 None。

三值语义（与仓库其它地方一致）：`True` 通过、`False` 不通过、`None` **查不了**。
查不了不并进通过。
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

#: PDF / SVG / EPS 页面尺寸的容差（mm）。matplotlib 写的是 `figsize × 72` pt，
#: 浮点换算误差在 1e-3 mm 量级；0.05 mm 已经是打印机分不出的量。
SIZE_TOL_MM = 0.05
#: 位图像素尺寸的容差：matplotlib 按 `int(w_in * dpi)` 取整，允许 1 px。
PX_TOL = 1
#: 位图密度元数据的容差（dpi）：PNG 的 pHYs 以「像素 / 米」写，反解回 dpi 有取整。
DPI_TOL = 0.5

_SVG_UNITS_PER_MM = {"pt": 72.0 / 25.4, "px": 96.0 / 25.4, "in": 1.0 / 25.4, "mm": 1.0, "cm": 0.1}


def _norm(name) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def font_names_match(requested: str | None, names: list[str]) -> bool | None:
    """产物里的**每一个**字体名都得对得上请求的族（子集名 `TimesNewRomanPSMT` 含
    `timesnewroman` 即算）。没有请求 → None；产物里一个字体都没有 → None（无字可验）。"""
    if not requested:
        return None
    if not names:
        return None
    want = _norm(requested)
    if not want:
        return None
    return all(want in _norm(n) for n in names)


def _mm_from_pt(v: float) -> float:
    return v / 72.0 * 25.4


def _size_ok(got: list[float] | None, want: list[float] | None) -> bool | None:
    if got is None or want is None:
        return None
    return abs(got[0] - want[0]) <= SIZE_TOL_MM and abs(got[1] - want[1]) <= SIZE_TOL_MM


def _svg_length(value: str | None) -> float | None:
    """`"425.196852pt"` → mm；没有单位按 px（SVG 的用户单位）。"""
    if not value:
        return None
    m = re.fullmatch(r"\s*([0-9.]+(?:e-?[0-9]+)?)\s*([a-z%]*)\s*", str(value))
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2) or "px"
    per_mm = _SVG_UNITS_PER_MM.get(unit)
    if per_mm is None:
        return None
    return num / per_mm


def check_pdf(path: Path, *, expect_mm: list[float] | None, font_family: str | None) -> dict:
    from tavotto import pdfbackend

    probe = pdfbackend.probe_asset(path, "pdf")
    size = [_mm_from_pt(float(probe["w_pt"])), _mm_from_pt(float(probe["h_pt"]))]
    fonts = pdfbackend.pdf_fonts(path)
    return {
        "format": "pdf",
        "size_mm": [round(size[0], 3), round(size[1], 3)],
        "size_ok": _size_ok(size, expect_mm),
        "fonts": fonts,
        "font_ok": font_names_match(font_family, fonts),
        "font_note": None if fonts else "PDF 里没有文字对象（或全部转成了路径）",
    }


def check_svg(path: Path, *, expect_mm: list[float] | None, font_family: str | None) -> dict:
    try:
        root = ElementTree.parse(path).getroot()
    except ElementTree.ParseError as exc:
        return {
            "format": "svg",
            "size_mm": None,
            "size_ok": False,
            "error": f"svg_unparseable: {exc}",
        }
    w, h = _svg_length(root.get("width")), _svg_length(root.get("height"))
    size = [w, h] if w is not None and h is not None else None
    vb = root.get("viewBox")
    viewbox_ok: bool | None = None
    if vb and size is not None:
        parts = vb.replace(",", " ").split()
        if len(parts) == 4:
            try:
                vw, vh = float(parts[2]), float(parts[3])
                # width/height 与 viewBox 必须同一个纵横比，否则渲染器会拉伸
                viewbox_ok = abs(vw / vh - size[0] / size[1]) < 1e-3 if vh and size[1] else False
            except ValueError:
                viewbox_ok = False
    # 文字有没有留成 <text>：matplotlib 默认 svg.fonttype="path"，那时字体查不了
    ns = "{http://www.w3.org/2000/svg}"
    texts = list(root.iter(f"{ns}text")) + list(root.iter("text"))
    fonts: list[str] = []
    for t in texts:
        style = t.get("style") or ""
        m = re.search(r"font-family:\s*'?([^;']+)'?", style)
        if m and m.group(1).strip() not in fonts:
            fonts.append(m.group(1).strip())
    return {
        "format": "svg",
        "size_mm": [round(size[0], 3), round(size[1], 3)] if size else None,
        "size_ok": _size_ok(size, expect_mm),
        "viewbox_ok": viewbox_ok,
        "fonts": fonts,
        "font_ok": font_names_match(font_family, fonts) if texts else None,
        "font_note": None
        if texts
        else "SVG 里文字已转成路径（svg.fonttype=path），字体无法从文件核验",
    }


def check_raster(
    path: Path,
    fmt: str,
    *,
    expect_mm: list[float] | None,
    dpi: int | None,
    font_family: str | None,
) -> dict:
    from tavotto import pdfbackend
    from tavotto.engine import originalspec

    probe = pdfbackend.probe_asset(path, "raster")
    px = [int(probe["px_w"]), int(probe["px_h"])]
    px_ok: bool | None = None
    expect_px = None
    if expect_mm is not None and dpi:
        expect_px = [int(round(expect_mm[0] / 25.4 * dpi)), int(round(expect_mm[1] / 25.4 * dpi))]
        px_ok = abs(px[0] - expect_px[0]) <= PX_TOL and abs(px[1] - expect_px[1]) <= PX_TOL
    meta = originalspec.raster_dpi(path) if fmt == "png" else None
    dpi_ok: bool | None = None
    if fmt == "png":
        dpi_ok = (
            None
            if meta is None or not dpi
            else abs(meta[0] - dpi) <= DPI_TOL and abs(meta[1] - dpi) <= DPI_TOL
        )
    return {
        "format": fmt,
        "px": px,
        "expected_px": expect_px,
        "size_ok": px_ok,
        "dpi_meta": [round(meta[0], 2), round(meta[1], 2)] if meta else None,
        "dpi_ok": dpi_ok,
        "dpi_note": "TIFF 的分辨率标签不解析（未核验）" if fmt in ("tif", "tiff") else None,
        "fonts": [],
        "font_ok": None,
        "font_note": "位图里没有字体对象，字体无法从文件核验" if font_family else None,
    }


_EPS_BBOX = re.compile(rb"^%%BoundingBox:\s*(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)", re.M)
_EPS_FONT = re.compile(rb"^%%BeginFont:\s*(\S+)|/FontName\s*/(\S+)", re.M)


def check_eps(path: Path, *, expect_mm: list[float] | None, font_family: str | None) -> dict:
    head = path.read_bytes()[:262144]
    m = _EPS_BBOX.search(head)
    size = None
    if m:
        x0, y0, x1, y1 = (int(v) for v in m.groups())
        size = [_mm_from_pt(x1 - x0), _mm_from_pt(y1 - y0)]
    fonts: list[str] = []
    for a, b in _EPS_FONT.findall(head):
        name = (a or b).decode("latin-1")
        if name and name not in fonts:
            fonts.append(name)
    # EPS 的 BoundingBox 是整数 pt：换算回 mm 的取整误差可达 0.35 mm，按 1 pt 容差判
    ok: bool | None = None
    if size is not None and expect_mm is not None:
        ok = abs(size[0] - expect_mm[0]) <= _mm_from_pt(1.0) and abs(
            size[1] - expect_mm[1]
        ) <= _mm_from_pt(1.0)
    return {
        "format": "eps",
        "size_mm": [round(size[0], 3), round(size[1], 3)] if size else None,
        "size_ok": ok,
        "fonts": fonts,
        "font_ok": font_names_match(font_family, fonts) if fonts else None,
        "font_note": None if fonts else "EPS 里没扫到字体资源声明，字体无法从文件核验",
    }


def check_file(
    path: Path,
    fmt: str,
    *,
    expect_mm: list[float] | None,
    dpi: int | None,
    font_family: str | None,
) -> dict:
    """按格式验一个文件。返回里 `ok` 只在**能验的项**全过时为 True；
    `unverified` 列出查不了的项（调用方要把它们说出口）。"""
    fmt = str(fmt).lower()
    try:
        if fmt == "pdf":
            out = check_pdf(path, expect_mm=expect_mm, font_family=font_family)
        elif fmt == "svg":
            out = check_svg(path, expect_mm=expect_mm, font_family=font_family)
        elif fmt in ("png", "tif", "tiff"):
            out = check_raster(path, fmt, expect_mm=expect_mm, dpi=dpi, font_family=font_family)
        elif fmt == "eps":
            out = check_eps(path, expect_mm=expect_mm, font_family=font_family)
        else:
            out = {"format": fmt, "error": "unknown_format"}
    except Exception as exc:  # noqa: BLE001 — 验收自己炸了也要变成一条不通过，不是异常逃逸
        out = {"format": fmt, "error": f"{type(exc).__name__}: {exc}"}
    verdicts = {k: out.get(k) for k in ("size_ok", "viewbox_ok", "dpi_ok", "font_ok") if k in out}
    failed = [k for k, v in verdicts.items() if v is False]
    unverified = [k for k, v in verdicts.items() if v is None and k in ("size_ok", "font_ok")]
    if font_family is None:
        unverified = [k for k in unverified if k != "font_ok"]
    out["failed"] = failed
    out["unverified"] = unverified
    out["ok"] = not failed and "error" not in out
    return out
