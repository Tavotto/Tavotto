"""U06 的 evidence 用**纯标准库**再读一遍（`docs/implementation/tavotto-foundation/evidence/u06/`）。

写入侧是 pikepdf + fontTools + HarfBuzz，生成器里的三把尺子是 pypdfium2 / pdfminer / poppler——它们
都不在主仓库 `.venv` 里。这里是每次 CI 都会量的那一把：用 `tests/support/pdfread.py` 直接读进了 git 的
`u06.pdf` / `u06_pdfium.png`，与手写的 `truth.json` 对。判据的主语是**进了 git 的那份文件此刻的字节**
（不是写入器的返回值）；像素采样点的页面坐标在 truth 里手算好了，不调 `plan.py`。

覆盖表那两份（`canvas_coverage.rendercore.json` / `coverage_diff.json`）是 D07 迁移的依据：这里钉住
它们与 report 互指的 hash，以及 diff 里必须写明的那几条事实（没有 fallback 层、Hangul 不在集合里、
科学矩阵在 auto 档没有方框）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import pdfread  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "implementation" / "tavotto-foundation" / "evidence" / "u06"
TRUTH = json.loads((EVIDENCE / "truth.json").read_text(encoding="utf-8"))
REPORT = json.loads((EVIDENCE / "report.json").read_text(encoding="utf-8"))
PDF = (EVIDENCE / "u06.pdf").read_bytes()
PNG = (EVIDENCE / "u06_pdfium.png").read_bytes()
MM = 72.0 / 25.4

OBJS = pdfread.objects(PDF)
PAGE_HEAD, CONTENT = pdfread.page(OBJS)
FONTS = pdfread.fonts(OBJS, PAGE_HEAD)
MAPS = {name: pdfread.decode_tounicode(f["tounicode"]) for name, f in FONTS.items()}
RUNS = pdfread.text_runs(CONTENT, MAPS)


def test_evidence_files_match_the_report_hashes():
    """report.json 说的就是磁盘上这几份文件；它们要一起重生成。"""
    assert REPORT["all_ok"] is True and REPORT["kind"] == "u06_ir_text_evidence"
    assert REPORT["outputs"]["u06.pdf"]["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert REPORT["outputs"]["u06_pdfium.png"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert (
        REPORT["inputs"]["truth_sha256"]
        == hashlib.sha256((EVIDENCE / "truth.json").read_bytes()).hexdigest()
    )
    assert (
        REPORT["outputs"]["canvas_coverage.rendercore.json"]["sha256"]
        == hashlib.sha256((EVIDENCE / "canvas_coverage.rendercore.json").read_bytes()).hexdigest()
    )
    allow = ROOT / "src" / "tavotto" / "rendercore" / "fonts_allowlist.json"
    assert REPORT["inputs"]["allowlist_sha256"] == hashlib.sha256(allow.read_bytes()).hexdigest()
    assert set(REPORT["inputs"]["fonts"]) == set(
        json.loads(allow.read_text(encoding="utf-8"))["faces"]
    )


def test_page_size_is_the_truth_in_pt():
    w, h = TRUTH["page_mm"]
    assert pdfread.media_box(PAGE_HEAD) == pytest.approx([0, 0, w * MM, h * MM], abs=1e-3)


def test_fonts_are_embedded_subsets_of_both_program_kinds():
    kinds = {f["kind"] for f in FONTS.values()}
    assert kinds == {"truetype", "cff-cid"}, kinds
    for name, f in FONTS.items():
        assert f["type0"] and f["identity_h"] and f["tag"] and len(f["tag"]) == 6, name
        assert f["program"] and 0 < len(f["program"]) < 40_000, name
        assert MAPS[name], f"{name} 的 ToUnicode 为空"
        assert f["w_entries"] >= 1, name
    for ff in REPORT["writer_facts"]["fonts"]:
        assert ff["glyphs_in_subset"] < ff["glyphs_in_source"] / 10, ff


@pytest.mark.parametrize("line", TRUTH["text"], ids=lambda t: t["id"])
def test_each_line_decodes_back_through_tounicode_and_actualtext(line):
    logical = [r["logical"] for r in RUNS]
    tounicode = [r["text"] for r in RUNS]
    assert line["logical"] in logical, (line["id"], logical)
    assert line.get("tounicode_only", line["logical"]) in tounicode, (line["id"], tounicode)
    assert "�" not in "".join(tounicode)


def test_hidden_text_is_not_in_the_file():
    flat = "".join(r["logical"] for r in RUNS)
    for bad in TRUTH["must_not_contain"]:
        assert bad not in flat
    assert "hidden" in REPORT["compiled"]["dropped_hidden"]


def test_every_content_code_has_a_tounicode_entry_and_truetype_codes_are_subset_gids():
    """RC-034 在 evidence 上：内容流里每个 code 都有 ToUnicode 条目；TrueType 单码位条目经嵌入子集自己的
    cmap 反查回同一个 GID。"""
    checked = 0
    cmaps = {
        n: pdfread.sfnt_cmap(f["program"]) for n, f in FONTS.items() if f["kind"] == "truetype"
    }
    for r in RUNS:
        for font, code in r["codes"]:
            assert code in MAPS[font], (font, code)
            text = MAPS[font][code]
            if font in cmaps and len(text) == 1 and code != 0:
                assert cmaps[font].get(ord(text)) == code, (font, code, text)
                checked += 1
    assert checked >= 40, checked


def test_actualtext_spans_hold_exactly_one_tj_each():
    import re

    spans = re.findall(rb"/ActualText <([0-9A-Fa-f]+)>>> BDC(.*?)EMC", CONTENT, re.S)
    texts = [bytes.fromhex(h.decode()).decode("utf-16") for h, _ in spans]
    assert "⁻²" in texts and "x̃" in texts and "é" in texts, texts
    assert all(body.count(b"] TJ") == 1 for _, body in spans)
    assert REPORT["writer_facts"]["actualtext_spans"] == len(spans)


# ---------------------------------------------------------------- 像素

PNG_W, PNG_H, RGBA = pdfread.decode_png(PNG)


def _pixel(x_pt: float, y_pt: float) -> tuple[int, int, int]:
    scale = TRUTH["raster"]["scale"]
    h_pt = TRUTH["page_mm"][1] * MM
    px, py = int(x_pt * scale), int((h_pt - y_pt) * scale)
    i = (py * PNG_W + px) * 4
    return RGBA[i], RGBA[i + 1], RGBA[i + 2]


def test_raster_is_the_page_at_scale():
    import math

    w, h = TRUTH["page_mm"]
    s = TRUTH["raster"]["scale"]
    assert (PNG_W, PNG_H) == (math.ceil(w * MM * s), math.ceil(h * MM * s))


@pytest.mark.parametrize("smp", TRUTH["raster"]["samples"], ids=lambda s: s["id"])
def test_pixel_samples_match_the_hand_computed_colors(smp):
    got = _pixel(*smp["page_pt"])
    tol = TRUTH["raster"]["rgb_tolerance"]
    assert all(abs(a - b) <= tol for a, b in zip(got, smp["rgb"])), (smp["id"], got, smp["why"])


def test_text_lines_have_ink_where_the_writer_put_them():
    """非旋转文字块：内容流里 Tm 的 (x, y) 上方 0.7 em 有墨。"""
    checked = 0
    for r in RUNS:
        if r["x"] is None or r["logical"] == "rotated":
            continue
        size = r["size"] or 10.0
        dark = 0
        scale = TRUTH["raster"]["scale"]
        h_pt = TRUTH["page_mm"][1] * MM
        for xp in range(int(r["x"] * scale), int((r["x"] + 40) * scale)):
            for yp in range(
                int((h_pt - r["y"] - size * 0.7) * scale), int((h_pt - r["y"]) * scale)
            ):
                i = (yp * PNG_W + xp) * 4
                if RGBA[i] < 100 and RGBA[i + 1] < 100 and RGBA[i + 2] < 100:
                    dark += 1
        assert dark > 20, (r["logical"], dark)
        checked += 1
    assert checked >= 4


# ---------------------------------------------------------------- 覆盖表（D07 迁移的依据）


def test_coverage_table_has_no_fallback_layer_and_records_the_known_limits():
    table = json.loads((EVIDENCE / "canvas_coverage.rendercore.json").read_text(encoding="utf-8"))
    assert table["layers"]["fallback"] == []
    assert table["backend"] == "rendercore" and table["max_codepoint"] == 0x30000
    diff = json.loads((EVIDENCE / "coverage_diff.json").read_text(encoding="utf-8"))
    var = diff["liberation_charset_variance"]
    assert var["intersection"] >= 2300 and var["union"] - var["intersection"] < 64
    assert diff["scientific_matrix"]["rendered_missing_auto"] == []
    assert diff["scientific_matrix"]["raw_missing"] == ["⁻"]
    # 集合外明示：Hangul（U+AC00 起）不在新集合里、旧后端的 fallback 脸不在了——数字必须写在 diff 里
    prim = {c for lo, hi in table["layers"]["primary"] for c in range(lo, hi + 1)}
    cjk = {c for lo, hi in table["layers"]["cjk"] for c in range(lo, hi + 1)}
    assert 0xAC00 not in cjk and 0x4E00 in cjk and 0x3042 in cjk and 0x2103 in cjk
    assert 0x03B1 in prim and 0x2075 in prim and 0x207B not in prim and 0x207B not in cjk
    assert diff["drawable"]["lost"] > 10_000 and diff["layers"]["primary"]["gained"] > 1000
