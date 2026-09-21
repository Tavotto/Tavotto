"""U02 render_spike：按 `truth.json` 写一页真实 PDF，再用**与写入侧不同源**的读取器核对。

    PYTHONPATH=scripts:src <spike venv>/bin/python -m dev.u02_spikes.render_spike \\
        --fonts <字体目录（dev.u02_spikes.fonts 落盘的）> \\
        --out docs/implementation/tavotto-foundation/evidence/u02/render

写入侧：`dev.u02_spikes.pdfwrite`（pikepdf + fontTools + HarfBuzz）。
读取侧（三把尺子，互不同源，也不与写入侧同源）：
* **pypdfium2（PDFium）**：栅格化 → 按真值采样像素（几何 / 透明组 / clip）、页面对象普查
  （文字对象数、路径包围盒）、文字层抽取；
* **pdfminer.six**：纯 Python 的第二个文字层读取器（ToUnicode 走另一套实现）；
* **pypdf**：字体结构（Type0 / Identity-H / CIDFontType2|0 / FontFile2|3 / ToUnicode / 子集前缀）。

产物：`spike.pdf`、`spike_pdfium.png`（PDFium 栅格，RGBA）、`report.json`（版本、输入输出 hash、
写入事实、每条核对的结果）。任一核对不过 → 退出码 1。`tests/test_foundation_u02_render.py` 再用
纯标准库把 `spike.pdf` / `spike_pdfium.png` 与同一份 truth 对一遍（不依赖候选包）。
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import struct
import sys
import zlib
from pathlib import Path

from dev.u02_spikes import fonts as fontmanifest
from dev.u02_spikes.hashcheck import sha256_file
from dev.u02_spikes.pdfwrite import Document, FontFace, mat_apply, mat_mul

# Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码（cp1252 / cp936），
# 第一句中文就 UnicodeEncodeError；两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def build(truth: dict, font_dir: Path, out_pdf: Path) -> dict:
    from tavotto.richtext import parse_runs

    faces: dict[tuple[str, bool, bool], FontFace] = {}

    def face(family: str, bold: bool = False, italic: bool = False) -> FontFace:
        key = (family, bold, italic)
        if key not in faces:
            faces[key] = FontFace(fontmanifest.face_path(font_dir, family, bold, italic))
        return faces[key]

    cjk = face("cjk")
    doc = Document(*truth["page_pt"])
    src = ROOT / truth["source_page"]
    for imp in truth["imports"]:
        doc.import_page(
            src,
            tuple(imp["rect"]),
            rotate_cw_deg=imp.get("rotate_cw_deg", 0),
            crop=tuple(imp["crop"]) if imp.get("crop") else None,
            opacity=imp.get("opacity", 1.0),
        )
    for grp in truth["groups"]:
        rects = grp["rects"]
        if grp["kind"] == "transparency_group":
            xs = [r["rect"][0] for r in rects] + [r["rect"][0] + r["rect"][2] for r in rects]
            ys = [r["rect"][1] for r in rects] + [r["rect"][1] + r["rect"][3] for r in rects]

            def draw(d: Document, rects=rects) -> None:
                for r in rects:
                    d.fill_rect(*r["rect"], tuple(r["rgb"]))

            doc.group((min(xs), min(ys), max(xs), max(ys)), grp["opacity"], draw)
        elif grp["kind"] == "per_object_alpha":
            for r in rects:
                doc.fill_rect(*r["rect"], tuple(r["rgb"]), opacity=grp["opacity"])
        else:
            raise ValueError(grp["kind"])
    for t in truth["text"]:
        primary = face(t["family"], t.get("bold", False), t.get("italic", False))
        pieces = [(run.text, primary, cjk, run.script) for run in parse_runs(t["markup"])]
        doc.text(t["x"], t["y"], t["size"], pieces)
    facts = doc.save(out_pdf)
    facts["faces"] = {
        f"{k[0]}/{'bold' if k[1] else 'regular'}/{'italic' if k[2] else 'upright'}": {
            "file": v.path.name,
            "sha256": sha256_file(v.path),
            "kind": v.kind,
            "postscript_name": v.postscript_name,
        }
        for k, v in faces.items()
    }
    return facts


# ---------------------------------------------------------------------------
# 读（三把尺子）
# ---------------------------------------------------------------------------
def _png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _close(a: tuple[int, int, int], b: tuple[int, int, int], tol: int) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


def expected_matrix(imp: dict, truth: dict) -> tuple[float, ...]:
    """读取侧**自己**按 truth 算的导入矩阵（显式公式，不用 pdfwrite 的 mat_mul 组合）。

    用写入侧记录的矩阵去映射采样点会自证：旋转方向反了，采样点跟着转到错的位置，颜色照样
    对得上（变异 MD 实测就是这么漏掉的）。所以这里独立重算，写入侧的 facts 只拿来对照。"""
    bx0, by0, bx1, by1 = truth["source_visible_bbox"]
    bw, bh = bx1 - bx0, by1 - by0
    if imp.get("crop"):
        cx, cy, cw, ch = imp["crop"]
        sx0, sw, sh = bx0 + cx * bw, cw * bw, ch * bh
        sy0 = by1 - cy * bh - sh
    else:
        sx0, sy0, sw, sh = bx0, by0, bw, bh
    tx, ty, tw, th = imp["rect"]
    kx, ky = tw / sw, th / sh
    ang = -math.radians(imp.get("rotate_cw_deg", 0))
    c, s = math.cos(ang), math.sin(ang)
    cx_, cy_ = tx + tw / 2, ty + th / 2
    ex, fy = tx - kx * sx0, ty - ky * sy0
    return (
        kx * c,
        kx * s,
        -ky * s,
        ky * c,
        c * (ex - cx_) - s * (fy - cy_) + cx_,
        s * (ex - cx_) + c * (fy - cy_) + cy_,
    )


def verify_pdfium(
    pdf_path: Path, truth: dict, facts: dict, out_png: Path
) -> tuple[list[dict], dict]:
    import pypdfium2 as pdfium

    results: list[dict] = []
    tol = truth["rgb_tolerance"]
    scale = truth["raster"]["scale"]
    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[0]
    w_pt, h_pt = page.get_size()
    results.append(
        {
            "check": "pdfium.page_size",
            "ok": [round(w_pt, 3), round(h_pt, 3)] == truth["page_pt"],
            "got": [w_pt, h_pt],
        }
    )
    # 白底不透明 + 强制 RGBA（fill_color 带透明度才会给 4 通道；这里要的是「白纸上的合成结果」，
    # 所以先按 RGBA 渲染再把 alpha 通道钉成 255——像素值就是 PDFium 在白底上的合成结果）
    bitmap = page.render(
        scale=scale,
        rev_byteorder=True,
        prefer_bgrx=False,
        draw_annots=False,
        fill_color=(255, 255, 255, 255),
        force_bitmap_format=pdfium.raw.FPDFBitmap_BGRA,
    )
    width, height = bitmap.width, bitmap.height
    buf = bytes(bitmap.buffer)
    assert bitmap.n_channels == 4, (bitmap.n_channels, bitmap.mode)
    assert bitmap.mode == "RGBA", bitmap.mode
    out_png.write_bytes(_png_rgba(width, height, buf))

    def pixel(x_pt: float, y_pt: float) -> tuple[int, int, int]:
        px = int(x_pt * scale)
        py = int((h_pt - y_pt) * scale)
        i = (py * width + px) * 4
        return buf[i], buf[i + 1], buf[i + 2]

    # 导入页：真值给的是源页坐标，经**读取侧自己算的**矩阵映射到页面再采样；写入侧记的矩阵
    # 另外单独对照一次（两者不同源，差了就红）
    for imp, fact in zip(truth["imports"], facts["imports"]):
        m = expected_matrix(imp, truth)
        results.append(
            {
                "check": f"writer.import.{imp['id']}.matrix_matches_independent_recompute",
                "ok": all(abs(a - b) < 1e-6 for a, b in zip(m, fact["matrix"])),
                "expected": [round(v, 6) for v in m],
                "writer": fact["matrix"],
            }
        )
        for smp in imp["samples"]:
            x, y = mat_apply(m, *smp["src"])
            got = pixel(x, y)
            results.append(
                {
                    "check": f"pdfium.import.{imp['id']}.pixel",
                    "ok": _close(got, tuple(smp["rgb"]), tol),
                    "src": smp["src"],
                    "page": [round(x, 2), round(y, 2)],
                    "expected": smp["rgb"],
                    "got": list(got),
                    "why": smp.get("why", ""),
                }
            )
    for grp in truth["groups"]:
        for smp in grp["samples"]:
            got = pixel(*smp["page"])
            results.append(
                {
                    "check": f"pdfium.group.{grp['id']}.pixel",
                    "ok": _close(got, tuple(smp["rgb"]), tol),
                    "page": smp["page"],
                    "expected": smp["rgb"],
                    "got": list(got),
                    "why": smp.get("why", ""),
                }
            )
    # 对照：透明组 D 与逐对象 E 的重叠区必须**不同**（否则「透明组」这条什么都没证明）
    d_ov = next(s for s in truth["groups"][0]["samples"])
    e_ov = next(s for s in truth["groups"][1]["samples"])
    results.append(
        {
            "check": "pdfium.group_vs_per_object_differ",
            "ok": not _close(pixel(*d_ov["page"]), pixel(*e_ov["page"]), tol),
            "got": [list(pixel(*d_ov["page"])), list(pixel(*e_ov["page"]))],
        }
    )
    # 文字有墨：每行的基线上方 0.7 em 内至少有暗像素
    for t in truth["text"]:
        x0, y0, size = t["x"], t["y"], t["size"]
        dark = 0
        for xp in range(int(x0 * scale), int((x0 + 60) * scale)):
            for yp in range(int((h_pt - y0 - size * 0.7) * scale), int((h_pt - y0) * scale)):
                i = (yp * width + xp) * 4
                if buf[i] < 100 and buf[i + 1] < 100 and buf[i + 2] < 100:
                    dark += 1
        results.append(
            {"check": f"pdfium.text.{t['id']}.ink", "ok": dark > 20, "dark_pixels": dark}
        )
    # 对象普查：文字对象是真文字（FPDF_PAGEOBJ_TEXT），不是 path。嵌套对象的 get_bounds()
    # 是 form 局部坐标（实测），要用 PDFium 自己报的 form 矩阵逐层乘回页面空间——矩阵来自
    # 读取器，不是写入侧的事实。
    census: dict[str, int] = {}
    inner_bounds: list[list[float]] = []
    ctm_by_level: dict[int, tuple] = {-1: (1, 0, 0, 1, 0, 0)}
    for obj in page.get_objects(max_depth=3):
        name = {1: "text", 2: "path", 3: "image", 4: "shading", 5: "form"}.get(
            obj.type, str(obj.type)
        )
        census[name] = census.get(name, 0) + 1
        if obj.type == 5:
            m = obj.get_matrix()
            ctm_by_level[obj.level] = mat_mul(
                (m.a, m.b, m.c, m.d, m.e, m.f), ctm_by_level[obj.level - 1]
            )
        if obj.type == 2 and obj.level > 0:
            ctm = ctm_by_level[obj.level - 1]
            left, bottom, right, top = obj.get_bounds()
            pts = [mat_apply(ctm, x, y) for x in (left, right) for y in (bottom, top)]
            inner_bounds.append(
                [
                    round(min(p[0] for p in pts), 3),
                    round(min(p[1] for p in pts), 3),
                    round(max(p[0] for p in pts), 3),
                    round(max(p[1] for p in pts), 3),
                ]
            )
    results.append(
        {
            "check": "pdfium.objects.text_objects_present",
            "ok": census.get("text", 0) >= len(truth["text"]),
            "census": census,
        }
    )
    # 导入页里的蓝矩形：经变换后的包围盒应出现在嵌套 path 对象的包围盒里
    for imp, fact in zip(truth["imports"], facts["imports"]):
        exp = imp.get("expected_inner_rect_bounds")
        if not exp:
            continue
        x0, y0, x1, y1 = exp["src_rect"]
        pts = [mat_apply(expected_matrix(imp, truth), px, py) for px in (x0, x1) for py in (y0, y1)]
        want = [
            min(p[0] for p in pts),
            min(p[1] for p in pts),
            max(p[0] for p in pts),
            max(p[1] for p in pts),
        ]
        hit = any(all(abs(a - b) < 0.6 for a, b in zip(b_, want)) for b_ in inner_bounds)
        results.append(
            {
                "check": f"pdfium.import.{imp['id']}.inner_rect_bounds",
                "ok": hit,
                "expected": [round(v, 3) for v in want],
                "candidates": inner_bounds[:12],
            }
        )
    textpage = page.get_textpage()
    text = textpage.get_text_range()
    results += _text_checks("pdfium", text, truth)
    return results, {"text": text, "census": census, "png": {"width": width, "height": height}}


def _nospace(s: str) -> str:
    return "".join(ch for ch in s if not ch.isspace())


def _text_checks(reader: str, text: str, truth: dict) -> list[dict]:
    out = []
    flat = _nospace(text)
    for t in truth["text"]:
        for exp in t.get("expect_extract", []):
            out.append(
                {"check": f"{reader}.text.{t['id']}.contains", "ok": exp in text, "expected": exp}
            )
        for exp in t.get("expect_extract_nospace", []):
            out.append(
                {
                    "check": f"{reader}.text.{t['id']}.contains_nospace",
                    "ok": _nospace(exp) in flat,
                    "expected": exp,
                }
            )
    return out


def verify_pdfminer(pdf_path: Path, truth: dict) -> tuple[list[dict], str]:
    from pdfminer.high_level import extract_text

    text = extract_text(str(pdf_path))
    return _text_checks("pdfminer", text, truth), text


def verify_pypdf(pdf_path: Path, truth: dict) -> tuple[list[dict], dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    page = reader.pages[0]
    fonts = page["/Resources"]["/Font"]
    seen: dict[str, dict] = {}
    for name, ref in fonts.items():
        f = ref.get_object()
        desc_font = f["/DescendantFonts"][0].get_object()
        fd = desc_font["/FontDescriptor"].get_object()
        prog_key = (
            "/FontFile2" if "/FontFile2" in fd else ("/FontFile3" if "/FontFile3" in fd else None)
        )
        prog = fd[prog_key].get_object() if prog_key else None
        seen[name] = {
            "base_font": str(f["/BaseFont"]),
            "subtype": str(f["/Subtype"]),
            "encoding": str(f["/Encoding"]),
            "descendant_subtype": str(desc_font["/Subtype"]),
            "cid_to_gid": str(desc_font.get("/CIDToGIDMap", "")),
            "font_file": prog_key,
            "font_file_subtype": str(prog.get("/Subtype", "")) if prog is not None else None,
            "font_program_bytes": len(prog.get_data()) if prog is not None else 0,
            "to_unicode": "/ToUnicode" in f,
            "w_entries": len(desc_font["/W"]) // 2,
        }
    results = []
    for name, info in seen.items():
        ok = (
            info["subtype"] == "/Type0"
            and info["encoding"] == "/Identity-H"
            and info["to_unicode"]
            and info["font_file"] is not None
            and info["font_program_bytes"] > 0
            and len(info["base_font"].split("+")[0]) == 7  # "/ABCDEF"
            and (
                (
                    info["descendant_subtype"] == "/CIDFontType2"
                    and info["font_file"] == "/FontFile2"
                    and info["cid_to_gid"] == "/Identity"
                )
                or (
                    info["descendant_subtype"] == "/CIDFontType0"
                    and info["font_file_subtype"] == "/CIDFontType0C"
                )
            )
        )
        results.append({"check": f"pypdf.font.{name}.structure", "ok": ok, "font": info})
    expected_faces = {f for t in truth["text"] for f in t["expect_faces"]}
    embedded = {info["base_font"].split("+", 1)[1] for info in seen.values()}
    results.append(
        {
            "check": "pypdf.fonts.expected_faces_embedded",
            "ok": expected_faces <= embedded,
            "expected": sorted(expected_faces),
            "embedded": sorted(embedded),
        }
    )
    # 文字是文字：内容流里有 TJ/Tj，且字形数远小于源字体（真子集）
    content = page.get_contents().get_data().decode("latin-1")
    results.append(
        {"check": "pypdf.content.uses_text_operators", "ok": " TJ" in content or " Tj" in content}
    )
    return results, seen


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fonts", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--truth", type=Path, default=None)
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    truth_path = args.truth or out / "truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    font_digests = fontmanifest.verify(args.fonts)

    pdf_path = out / "spike.pdf"
    facts = build(truth, args.fonts, pdf_path)
    png_path = out / "spike_pdfium.png"
    r1, pdfium_extra = verify_pdfium(pdf_path, truth, facts, png_path)
    r2, miner_text = verify_pdfminer(pdf_path, truth)
    r3, font_structs = verify_pypdf(pdf_path, truth)
    results = r1 + r2 + r3

    import fontTools
    import pdfminer
    import pikepdf
    import pypdf
    import pypdfium2.version as pv
    import uharfbuzz

    report = {
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "versions": {
            "pypdfium2": pv.PYPDFIUM_INFO.version,
            "pdfium": pv.PDFIUM_INFO.version
            if hasattr(pv.PDFIUM_INFO, "version")
            else str(pv.PDFIUM_INFO),
            "pikepdf": pikepdf.__version__,
            "libqpdf": pikepdf.__libqpdf_version__,
            "fonttools": fontTools.version,
            "uharfbuzz": uharfbuzz.__version__,
            "harfbuzz": uharfbuzz.version_string(),
            "pdfminer.six": pdfminer.__version__,
            "pypdf": pypdf.__version__,
        },
        "inputs": {
            "truth": str(truth_path.relative_to(ROOT))
            if truth_path.is_relative_to(ROOT)
            else str(truth_path),
            "truth_sha256": sha256_file(truth_path),
            "source_page_sha256": sha256_file(ROOT / truth["source_page"]),
            "fonts": font_digests,
        },
        "outputs": {
            "spike.pdf": {"sha256": sha256_file(pdf_path), "bytes": pdf_path.stat().st_size},
            "spike_pdfium.png": {
                "sha256": sha256_file(png_path),
                "bytes": png_path.stat().st_size,
                **pdfium_extra["png"],
            },
        },
        "writer_facts": facts,
        "readers": {
            "pdfium_text": pdfium_extra["text"],
            "pdfium_object_census": pdfium_extra["census"],
            "pdfminer_text": miner_text,
            "pypdf_fonts": font_structs,
        },
        "results": results,
        "all_ok": all(r["ok"] for r in results),
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for r in results:
        print(
            ("PASS " if r["ok"] else "FAIL ")
            + r["check"]
            + (
                ""
                if r["ok"]
                else "  "
                + json.dumps(
                    {k: v for k, v in r.items() if k not in ("check", "ok")}, ensure_ascii=False
                )
            )
        )
    print(
        f"{'ALL OK' if report['all_ok'] else 'FAILED'}: {sum(r['ok'] for r in results)}/{len(results)} → {out}"
    )
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
