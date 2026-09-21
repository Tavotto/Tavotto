#!/usr/bin/env python3
"""U07 的证据生成器：合成（外来页 / 位图 / 文字 / 透明组）→ Canonical PDF → render child 栅格 → PNG + TIFF →
独立读取器 → `evidence/u07/`（统一实施包 U07，ADR 0065 / 0066）。

    PYTHONPATH=src <rc-venv>/bin/python scripts/dev/u07_evidence.py \\
        --out docs/implementation/tavotto-foundation/evidence/u07

产出（进 git）：
* `truth.json`        手写规格（页面、对象、每个像素采样点该是什么颜色、对象普查下界）——本脚本只读它；
* `u07.pdf`           写入器的产物（同一输入两次运行字节相同；三平台应逐字节相同——写入侧不含平台维度）；
* `u07_pdfium.png`    render child 的栅格（150 ppi；**跨平台像素不同、同平台可复现**，ADR 0055 §7——只做同平台
                      对照；其它平台的 sha256 由 `foundation-u06-rendercore.yml` 的腿记进工件，不判）；
* `report.json`       版本 / hash / 写入事实 / 栅格事实 / 各把尺子的读数 / 逐条核对。
  TIFF 不进 git：它与 PNG 出自同一个 RasterBuffer，报告里记它的解码像素 sha256 == PNG 的（RC-053）。

需要 `tavotto[rendercore]` 与批准字体（`scripts/fetch_fonts.py`）。任一条核对不过退出 1。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

import pdfread  # noqa: E402
import tiffcheck  # noqa: E402

from tavotto.rendercore import (  # noqa: E402
    BACKEND_NAME,
    BACKEND_VERSION,
    fonts,
    hbshaper,
    pdfwriter,
    plan,
    raster,
    renderhost,
    sources,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _same_name_pdf(text: str, font: str, rgb: tuple[float, float, float]) -> bytes:
    """/F1 与 /X1 同名、内容不同的合成源页（RC-043）。"""
    import pikepdf

    pdf = pikepdf.new()
    img = pikepdf.Stream(pdf, bytes(int(c * 255) for c in rgb))
    img["/Type"] = pikepdf.Name.XObject
    img["/Subtype"] = pikepdf.Name.Image
    img["/Width"] = 1
    img["/Height"] = 1
    img["/ColorSpace"] = pikepdf.Name.DeviceRGB
    img["/BitsPerComponent"] = 8
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 200, 100],
        Resources=pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name("/" + font),
                )
            ),
            XObject=pikepdf.Dictionary(X1=pdf.make_indirect(img)),
        ),
        Contents=pdf.make_stream(
            f"q 100 0 0 50 50 25 cm /X1 Do Q BT /F1 14 Tf 60 80 Td ({text}) Tj ET".encode()
        ),
    )
    pdf.pages.append(pikepdf.Page(page))
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


def _overlap_pdf() -> bytes:
    import pikepdf

    pdf = pikepdf.new()
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=[0, 0, 200, 100],
        Contents=pdf.make_stream(b"0 0 1 rg 20 20 100 60 re f 1 0 0 rg 60 40 100 40 re f"),
    )
    pdf.pages.append(pikepdf.Page(page))
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


class _Resolver:
    """把 truth 里点名的合成源当静态冻结源（不落盘到项目：字节在内存里）。"""

    def __init__(self, files: dict[str, bytes]) -> None:
        from tavotto.engine import figcapture

        self.files = files
        self.frozen: dict[str, sources.FrozenSource] = {}
        self.figcapture = figcapture

    def resolve(self, obj: dict) -> sources.FrozenSource:
        rel = str(obj["id"])
        data = self.files[rel]
        kind = rel.rsplit(".", 1)[-1].lower()
        art = self.figcapture.SourceArtifact(
            source_id=rel,
            origin=self.figcapture.ORIGIN_STATIC,
            kind=kind,
            bytes_sha256=sha256(data),
            size_bytes=len(data),
        )
        fs = sources.FrozenSource(artifact=art, path=Path("<memory>") / rel)
        self.frozen[rel] = fs
        return fs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--ppi", type=int, default=150)
    args = ap.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        print(f"字体不全：{reg.missing}", file=sys.stderr)
        return 2
    provider = hbshaper.HbFaceProvider(reg)

    files = {
        "figs/page.pdf": (FIXTURE / "page.pdf").read_bytes(),
        "figs/original.png": (FIXTURE / "original.png").read_bytes(),
        "figs/a.pdf": _same_name_pdf("alpha-text", "Helvetica", (1, 0, 0)),
        "figs/b.pdf": _same_name_pdf("bravo-text", "Courier", (0, 1, 0)),
        "figs/overlap.pdf": _overlap_pdf(),
    }
    resolver = _Resolver(files)
    checks: list[tuple[str, bool, dict]] = []

    def check(name: str, ok: bool, **facts) -> None:
        checks.append((name, bool(ok), facts))

    # ---- 编译 + 写 Canonical PDF ------------------------------------------------------
    compiled = plan.compile_page(
        truth["page"]["w_mm"],
        truth["page"]["h_mm"],
        truth["objects"],
        sources=resolver,
        faces=provider,
        background=None if truth["page"].get("transparent") else (1.0, 1.0, 1.0),
    )
    file_bytes = {key: files[fs.artifact.source_id] for key, fs in compiled.sources.items()}
    pdf_path = out / "u07.pdf"
    facts = pdfwriter.write_pdf(compiled.page, pdf_path, provider, file_bytes)
    again = out / "u07.again.pdf"
    facts2 = pdfwriter.write_pdf(compiled.page, again, provider, file_bytes)
    check("pdf.deterministic", facts.sha256 == facts2.sha256, sha256=facts.sha256)
    again.unlink()
    check(
        "pdf.imported_pages",
        len(facts.imported_pages) == truth["expect"]["imported_pages"],
        n=len(facts.imported_pages),
    )
    check("pdf.images", len(facts.images) == truth["expect"]["images"], n=len(facts.images))
    check(
        "pdf.transparency_groups",
        facts.transparency_groups == truth["expect"]["transparency_groups"],
        n=facts.transparency_groups,
    )
    objs = pdfread.objects(pdf_path.read_bytes())
    raw = pdf_path.read_bytes()
    for needle in ("/JavaScript", "/OpenAction", "/Annots"):
        check(f"pdf.no_{needle[1:].lower()}", needle.encode() not in raw)
    forms = [h for h, _ in objs.values() if b"/Subtype /Form" in h and b"/Group" not in h]
    check("pdf.one_form_per_source_page", len(forms) == truth["expect"]["forms"], n=len(forms))

    # ---- render child：一次栅格 → PNG + TIFF ------------------------------------------
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    host = renderhost.RenderHost(env=env, default_timeout=120)
    try:
        info = host.ping()
        buf = host.render(
            pdf_path,
            dpi=float(args.ppi),
            transparent=compiled.page.background is None,
            page_size_pt=(compiled.page.width_pt, compiled.page.height_pt),
        )
        inspect = host.inspect(pdf_path)
        probe = host.probe(pdf_path)
    finally:
        host.close()
    png = raster.encode_png(buf)
    (out / "u07_pdfium.png").write_bytes(png)
    tiff_path = out / "u07.tiff"
    tiff_facts = raster.write_tiff(buf, tiff_path)
    w_px = round(compiled.page.width_pt * args.ppi / 72)
    h_px = round(compiled.page.height_pt * args.ppi / 72)
    check(
        "raster.size", (buf.width, buf.height) == (w_px, h_px), width=buf.width, height=buf.height
    )
    want_channels = 4 if compiled.page.background is None else 3
    check(
        "raster.channels",
        buf.channels == want_channels and not buf.premultiplied,
        channels=buf.channels,
    )
    _, _, bpp, png_rgba = pdfread.decode_png_any(png)
    tags, tiff_samples = tiffcheck.decode_samples(tiff_path)
    check(
        "png_tiff.same_pixels",
        bpp == buf.channels and png_rgba == tiff_samples,
        sha256=sha256(png_rgba),
    )
    check(
        "tiff.tags",
        tags["samples_per_pixel"] == buf.channels
        and (tags.get("extra_samples") == 2) == (buf.channels == 4)
        and tags["x_resolution"] == args.ppi
        and tags["resolution_unit"] == 2,
    )
    tiff_path.unlink()  # 不进 git：像素 hash 已记

    scale = args.ppi / 72.0
    H = compiled.page.height_pt

    def px(x_pt: float, y_pt: float) -> tuple[int, ...]:
        return buf.pixel(int(x_pt * scale), int((H - y_pt) * scale))

    for smp in truth["samples"]:
        got = px(smp["x_pt"], smp["y_pt"])
        want = tuple(smp["rgba"])
        tol = smp.get("tol", 6)
        check(
            f"pixel.{smp['id']}",
            all(abs(a - b) <= tol for a, b in zip(got, want)),
            got=list(got),
            want=list(want),
        )
    census = inspect["objects"]
    for kind, low in truth["expect"]["census_min"].items():
        check(f"census.{kind}", census.get(kind, 0) >= low, got=census.get(kind, 0), min=low)
    check(
        "census.image_objects_are_only_the_placed_bitmaps",
        census.get("image", 0) == truth["expect"]["image_objects_total"],
        got=census.get("image", 0),
    )
    for t in truth["expect"]["texts"]:
        check(f"text.{t}", t in inspect["text"])
    check(
        "probe.size",
        abs(probe["width_pt"] - compiled.page.width_pt) < 0.01
        and abs(probe["height_pt"] - compiled.page.height_pt) < 0.01,
        probe=[probe["width_pt"], probe["height_pt"]],
    )

    # ---- 报告 --------------------------------------------------------------------------
    report = {
        "kind": "u07_compose_raster_evidence",
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "versions": {
            **hbshaper.versions(),
            "pdfium": info["pdfium"],
            "rendercore": f"{BACKEND_NAME}-{BACKEND_VERSION}",
        },
        "inputs": {
            "truth_sha256": sha256((out / "truth.json").read_bytes()),
            "sources": {k: sha256(v) for k, v in files.items()},
            "allowlist_sha256": sha256(fonts.allowlist_path().read_bytes()),
        },
        "outputs": {
            "u07.pdf": {"sha256": facts.sha256, "bytes": facts.bytes},
            "u07_pdfium.png": {"sha256": sha256(png), "bytes": len(png), "platform_specific": True},
            "u07.tiff(not committed)": {"pixel_sha256": sha256(tiff_samples), **tiff_facts},
        },
        "writer_facts": {
            "imported_pages": facts.imported_pages,
            "images": facts.images,
            "transparency_groups": facts.transparency_groups,
            "text_objects": facts.text_objects,
            "fonts": facts.fonts,
        },
        "raster_facts": {
            "width": buf.width,
            "height": buf.height,
            "stride": buf.stride,
            "channels": buf.channels,
            "dpi": buf.dpi,
            "ms_child": None,
        },
        "reader_pdfium": {"objects": census, "text": inspect["text"], "probe": probe},
        "checks": [{"name": n, "ok": ok, **f} for n, ok, f in checks],
        "all_ok": all(ok for _, ok, _ in checks),
    }
    write_lf(out / "report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for n, ok, f in checks:
        print(("OK  " if ok else "FAIL") + f" {n} {f if not ok else ''}")
    print(
        f"{'ALL OK' if report['all_ok'] else 'FAILED'} {sum(ok for _, ok, _ in checks)}/{len(checks)}"
    )
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
