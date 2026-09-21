#!/usr/bin/env python3
"""U06 的证据生成器：真字体 → RenderPlan → 可检索 PDF → 独立读取器 → `evidence/u06/`（统一实施包 U06）。

    PYTHONPATH=src <rc-venv>/bin/python scripts/dev/u06_evidence.py \\
        --out docs/implementation/tavotto-foundation/evidence/u06

产出（进 git）：
* `truth.json`      手写规格（页面、对象、每行文字各把尺子该抽出什么、像素采样点）——本脚本只读它；
* `u06.pdf`         写入器的产物（同一输入两次运行字节相同）；
* `u06_pdfium.png`  PDFium 栅格（scale 2；**跨平台像素不同**，只做同平台对照，见 ADR 0055 §7）；
* `report.json`     版本 / hash / 写入事实 / 各把尺子的读数 / 覆盖表 diff 摘要 / 逐条核对；
* `canvas_coverage.rendercore.json`  批准字体集合下的三层覆盖表（与 `pdfbackend/canvas_coverage.json`
  同形；**不替换**那份——U06 不切默认，前端的方框判据仍跟旧后端走，这份是 D07 迁移的 diff 依据）；
* `coverage_diff.json`  旧表 → 新表逐层的码位差（哪些从 fallback 变成 missing、哪些新救回来）。

需要 `tavotto[rendercore]` 与批准字体（`scripts/fetch_fonts.py`）。任一条核对不过退出 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "support"))

import pdfread  # noqa: E402

from tavotto import glyphplan  # noqa: E402
from tavotto.rendercore import (  # noqa: E402
    fonts,
    hbshaper,
    pdfwriter,
    plan,
    sources,
    typography,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_lf(path: Path, text: str) -> None:
    """文本产物一律写 LF：Windows 上默认的换行翻译会把 `\n` 换成 CRLF，而这些文件按 sha256 与 git 里的比
    （U02 在 Windows 腿上踩过一次）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _dump_coverage(table: dict) -> str:
    """与 scripts/gen_canvas_coverage.py 同一种排版（每层一行）。"""
    lines = ["{"]
    for key, value in table.items():
        if key != "layers":
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append(' "layers": {')
    layers = table["layers"]
    for i, (name, ranges) in enumerate(layers.items()):
        tail = "" if i == len(layers) - 1 else ","
        lines.append(f"  {json.dumps(name)}: {json.dumps(ranges, separators=(',', ':'))}{tail}")
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _expand(ranges: list[list[int]]) -> set[int]:
    return {c for lo, hi in ranges for c in range(lo, hi + 1)}


def coverage_evidence(provider: hbshaper.HbFaceProvider, out: Path, checks: list) -> dict:
    faces = typography.faces_for(provider, "serif", False, False)
    # 12 张 Liberation 脸的 cmap **不完全相同**（Mono 缺 U+2000–200B 一段空格，Serif 缺几个占星符号，
    # PUA 各不同）：旧后端「所有 base-14 脸共用一张覆盖表」的假设在新集合上不成立。表里的 primary
    # 取**交集**——前端摆得出的族 × 样式，后端每一张都画得出，才叫「画得出」。
    lib = {
        fid: frozenset(rec.info.cmap)
        for fid, rec in provider.registry.faces.items()
        if rec.approved.family != "cjk"
    }
    inter = frozenset.intersection(*lib.values())
    union = frozenset.union(*lib.values())
    variance = {fid: len(union - s) for fid, s in sorted(lib.items())}
    checks.append(
        (
            "coverage.liberation_intersection_recorded",
            len(inter) >= 2300 and len(union) - len(inter) < 64,
            {"intersection": len(inter), "union": len(union), "missing_vs_union": variance},
        )
    )
    cov_inter = glyphplan.Coverage(
        primary=lambda cp: cp in inter,
        cjk=typography.coverage(faces).cjk,
        fallback=lambda cp: False,
    )
    lo, hi = 0x20, typography.COVERAGE_MAX_CP
    table = {
        "schema": 1,
        "comment": (
            "U06 evidence：批准字体集合（Liberation 2.1.5 + Noto Sans SC）下画布文字三层覆盖的区间表，"
            "与 pdfbackend/canvas_coverage.json 同形。fallback 恒空（没有隐式回退脸）。"
            "不是产品用的那份：U06 不切默认，前端仍读旧表；这里是 D07 迁移 diff 的依据。"
        ),
        "backend": "rendercore",
        "backend_version": "0.1",
        "max_codepoint": typography.COVERAGE_MAX_CP,
        "primary_is": "12 张 Liberation 脸 cmap 的交集",
        "layers": {
            "primary": glyphplan.ranges_of(cov_inter.primary, lo, hi),
            "cjk": glyphplan.ranges_of(cov_inter.cjk, lo, hi),
            "fallback": [],
        },
    }
    write_lf(out / "canvas_coverage.rendercore.json", _dump_coverage(table))
    old = json.loads(
        (ROOT / "src" / "tavotto" / "pdfbackend" / "canvas_coverage.json").read_text(
            encoding="utf-8"
        )
    )
    diff: dict = {
        "old_backend": f"{old['backend']} {old['backend_version']}",
        "liberation_charset_variance": {
            "intersection": len(inter),
            "union": len(union),
            "missing_vs_union": variance,
        },
        "layers": {},
    }
    old_layers = {k: _expand(v) for k, v in old["layers"].items()}
    new_layers = {k: _expand(v) for k, v in table["layers"].items()}
    for layer in ("primary", "cjk", "fallback"):
        a, b = old_layers[layer], new_layers[layer]
        diff["layers"][layer] = {
            "old": len(a),
            "new": len(b),
            "gained": len(b - a),
            "lost": len(a - b),
            "gained_sample": [f"U+{c:04X}" for c in sorted(b - a)[:40]],
            "lost_sample": [f"U+{c:04X}" for c in sorted(a - b)[:40]],
        }
    # 关键的一档：旧表 fallback 里、新集合谁都画不出的（这些字从「换脸画出」变成「方框 + 问题」）
    drawable_old = old_layers["primary"] | old_layers["cjk"] | old_layers["fallback"]
    drawable_new = new_layers["primary"] | new_layers["cjk"]
    lost = sorted(drawable_old - drawable_new)
    gained = sorted(drawable_new - drawable_old)
    diff["drawable"] = {
        "old": len(drawable_old),
        "new": len(drawable_new),
        "lost": len(lost),
        "gained": len(gained),
        "lost_sample": [f"U+{c:04X} {chr(c)}" for c in lost[:80]],
        "gained_sample": [f"U+{c:04X} {chr(c)}" for c in gained[:40]],
    }
    # 科学文本矩阵（tests/test_scientific_text_matrix.py 的 TOKENS）在新集合下的分层
    matrix = "×10⁵ A m⁻² μm / µm α β γ Δ 25 °C Å ± ≤ ≥"
    cov = cov_inter
    layers_of = {ch: glyphplan.layer_of(ord(ch), cov) for ch in matrix if not ch.isspace()}
    missing_raw = [ch for ch, layer in layers_of.items() if layer == "missing"]
    rendered_missing, _ = glyphplan.text_diagnostics(matrix, cov, "auto")
    diff["scientific_matrix"] = {
        "raw_missing": missing_raw,
        "rendered_missing_auto": list(rendered_missing),
        "layers": {ch: layer for ch, layer in layers_of.items() if layer != "primary"},
    }
    checks.append(
        (
            "coverage.scientific_matrix_renders_without_boxes",
            rendered_missing == [],
            diff["scientific_matrix"],
        )
    )
    write_lf(out / "coverage_diff.json", json.dumps(diff, ensure_ascii=False, indent=1) + "\n")
    return diff


def render_evidence(
    provider: hbshaper.HbFaceProvider, out: Path, truth: dict, checks: list
) -> dict:
    compiled = plan.compile_page(
        truth["page_mm"][0],
        truth["page_mm"][1],
        truth["objects"],
        sources=sources.StaticSourceResolver(ROOT),
        faces=provider,
    )
    pdf_path = out / "u06.pdf"
    facts = pdfwriter.write_pdf(compiled.page, pdf_path, provider)
    again = out / "_again.pdf"
    pdfwriter.write_pdf(compiled.page, again, provider)
    checks.append(("writer.deterministic_bytes", again.read_bytes() == pdf_path.read_bytes(), {}))
    again.unlink()
    report: dict = {
        "compiled": {
            "problems": list(compiled.problems),
            "dropped_hidden": list(compiled.dropped_hidden),
            "resources": sorted(compiled.page.resources),
        },
        "writer_facts": json.loads(json.dumps(facts.__dict__, default=str)),
    }

    # 尺子 1：纯标准库读取器
    data = pdf_path.read_bytes()
    objs = pdfread.objects(data)
    page_head, content = pdfread.page(objs)
    fnt = pdfread.fonts(objs, page_head)
    maps = {name: pdfread.decode_tounicode(f["tounicode"]) for name, f in fnt.items()}
    runs = pdfread.text_runs(content, maps)
    logical = [r["logical"] for r in runs]
    tounicode = [r["text"] for r in runs]
    report["reader_stdlib"] = {
        "media_box": pdfread.media_box(page_head),
        "fonts": {
            n: {k: v for k, v in f.items() if k not in ("program", "tounicode", "widths")}
            | {"program_bytes": len(f["program"] or b""), "tounicode_entries": len(maps[n])}
            for n, f in fnt.items()
        },
        "lines_logical": logical,
        "lines_tounicode": tounicode,
    }
    mm = 72.0 / 25.4
    checks.append(
        (
            "stdlib.media_box",
            [round(v, 3) for v in report["reader_stdlib"]["media_box"]]
            == [0, 0, round(truth["page_mm"][0] * mm, 3), round(truth["page_mm"][1] * mm, 3)],
            {},
        )
    )
    for line in truth["text"]:
        checks.append(
            (f"stdlib.text.{line['id']}.logical", line["logical"] in logical, {"got": logical})
        )
        checks.append(
            (
                f"stdlib.text.{line['id']}.tounicode",
                line.get("tounicode_only", line["logical"]) in tounicode,
                {"got": tounicode},
            )
        )
    checks.append(("stdlib.no_unmapped_code", "�" not in "".join(tounicode), {}))
    for name, f in fnt.items():
        checks.append(
            (
                f"stdlib.font.{name}.embedded_subset_with_tounicode",
                bool(f["program"]) and bool(maps[name]),
                {},
            )
        )
    orphans = [(f, c) for r in runs for f, c in r["codes"] if c not in maps[f]]
    checks.append(
        ("stdlib.every_content_code_has_tounicode", not orphans, {"orphans": orphans[:10]})
    )

    # 尺子 2：PDFium（文字 + 栅格 + 对象普查）
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    page = doc[0]
    text = page.get_textpage().get_text_range()
    flat = "".join(ch for ch in text if not ch.isspace())
    report["reader_pdfium"] = {"version": pdfium.PDFIUM_INFO.version, "text": text}
    for line in truth["text"]:
        want = "".join(ch for ch in line["logical"] if not ch.isspace())
        checks.append((f"pdfium.text.{line['id']}", want in flat, {}))
    kinds = [o.type for o in page.get_objects()]
    report["reader_pdfium"]["objects"] = {"text": kinds.count(1), "path": kinds.count(2)}
    checks.append(
        (
            "pdfium.text_objects_not_outlines",
            kinds.count(1) >= truth["min_text_objects"],
            {"got": kinds.count(1)},
        )
    )
    scale = truth["raster"]["scale"]
    img = page.render(scale=scale).to_pil().convert("RGBA")
    img.save(out / "u06_pdfium.png")
    h_pt = truth["page_mm"][1] * mm
    for smp in truth["raster"]["samples"]:
        x, y = smp["page_pt"]
        px, py = int(x * scale), int((h_pt - y) * scale)
        got = img.getpixel((px, py))[:3]
        ok = all(abs(a - b) <= truth["raster"]["rgb_tolerance"] for a, b in zip(got, smp["rgb"]))
        checks.append(
            (
                f"pdfium.pixel.{smp['id']}",
                ok,
                {"got": list(got), "want": smp["rgb"], "why": smp.get("why")},
            )
        )

    # 尺子 3：pdfminer（只看 ToUnicode）
    try:
        from pdfminer.high_level import extract_text

        pm = extract_text(str(pdf_path))
        report["reader_pdfminer"] = {"text": pm}
        pm_flat = "".join(ch for ch in pm if not ch.isspace())
        for line in truth["text"]:
            want = "".join(
                ch for ch in line.get("tounicode_only", line["logical"]) if not ch.isspace()
            )
            checks.append((f"pdfminer.text.{line['id']}", want in pm_flat, {}))
    except ImportError:
        report["reader_pdfminer"] = {"skipped": "pdfminer.six 未装"}

    # 尺子 4：poppler pdftotext（系统里有就用）
    exe = shutil.which("pdftotext")
    if exe:
        proc = subprocess.run(
            [exe, str(pdf_path), "-"], capture_output=True, text=True, encoding="utf-8"
        )
        report["reader_poppler"] = {"text": proc.stdout, "stderr": proc.stderr}
        checks.append(
            (
                "poppler.no_syntax_error",
                proc.returncode == 0 and "Syntax Error" not in proc.stderr,
                {},
            )
        )
        pp_flat = "".join(ch for ch in proc.stdout if not ch.isspace())
        for line in truth["text"]:
            want = "".join(ch for ch in line["logical"] if not ch.isspace())
            checks.append((f"poppler.text.{line['id']}", want in pp_flat, {}))
    else:
        report["reader_poppler"] = {"skipped": "系统里没有 pdftotext"}
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    registry = fonts.FontRegistry.discover()
    if registry.missing:
        print(
            f"批准字体不全：缺 {registry.missing}（先跑 scripts/fetch_fonts.py）", file=sys.stderr
        )
        return 2
    provider = hbshaper.HbFaceProvider(registry)
    checks: list = []
    cov = coverage_evidence(provider, out, checks)
    rep = render_evidence(provider, out, truth, checks)
    report = {
        "kind": "u06_ir_text_evidence",
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "versions": hbshaper.versions(),
        "inputs": {
            "truth_sha256": sha256((out / "truth.json").read_bytes()),
            "fonts": {fid: rec.approved.sha256 for fid, rec in sorted(registry.faces.items())},
            "allowlist_sha256": sha256(fonts.allowlist_path().read_bytes()),
        },
        "outputs": {
            "u06.pdf": {
                "sha256": sha256((out / "u06.pdf").read_bytes()),
                "bytes": (out / "u06.pdf").stat().st_size,
            },
            "u06_pdfium.png": {"sha256": sha256((out / "u06_pdfium.png").read_bytes())},
            "canvas_coverage.rendercore.json": {
                "sha256": sha256((out / "canvas_coverage.rendercore.json").read_bytes())
            },
        },
        "coverage_diff": {
            "drawable": cov["drawable"] | {"lost_sample": cov["drawable"]["lost_sample"][:20]},
            "scientific_matrix": cov["scientific_matrix"],
        },
        **rep,
        "checks": [{"check": c, "ok": ok, **({"detail": d} if d else {})} for c, ok, d in checks],
        "all_ok": all(ok for _, ok, _ in checks),
    }
    write_lf(out / "report.json", json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    for c, ok, d in checks:
        print(("PASS" if ok else "FAIL"), c, "" if ok else json.dumps(d, ensure_ascii=False)[:300])
    n_ok = sum(1 for _, ok, _ in checks if ok)
    print(f"{'ALL OK' if report['all_ok'] else 'FAILED'}: {n_ok}/{len(checks)} → {out}")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
