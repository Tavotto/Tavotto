"""科学文本矩阵（Prompt 23 §五 I）：同一批字符走完**六个文字位置 × 四种产物**。

矩阵：`×10⁵ A m⁻²`、`μm / µm`（U+03BC 与 U+00B5 并排）、`α β γ Δ`、`25 °C`
（度号 + C 两个字符，不是 U+2103）、`Å`、`± ≤ ≥`。

四种产物各有各的尺子，**互不能当代理**：
* 预览 / 属性面板：manifest 的 `glyphs_missing`（`manifest._glyph_scan`，问真字体）；
* 原图 PDF：PyMuPDF `get_text()` 读回**同一串字符**（文本层语义一致）；
* 原图 PNG：真渲染出来且不是空图；
* 画布 PDF / PNG：`pdfbackend.missing_glyphs()` 对三个通用族都为空，且 PDF
  文本层读回同一串字符。

以前这些字符散在四五个用例里各测一角（`test_glyph_coverage_figure` 测 `×10⁵`
与中文，`test_worker_roundtrip` 测 `µm·h⁻¹ ±0.5 ℃`），`β γ Δ`、`Å`、`≤ ≥`、
`°C` 两字形式、两个 mu 并排、标注文字，从来没有一条用例把它们串起来。
本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import pool
from tavotto.pdfbackend import pymupdf_backend as pb

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: 矩阵本体。每一项都是用户会真的写进图里的那种串。
TOKENS = {
    "sci": "×10⁵ A m⁻²",
    "mu": "μm / µm",
    "greek": "α β γ Δ",
    "degc": "25 °C",
    "angstrom": "Å",
    "cmp": "± ≤ ≥",
}
ALL = " ".join(TOKENS.values())

SCRIPT_NAME = "fig_matrix.py"
ENTRY = "main"
STEM = "MatrixFig"
#: 六个文字位置：title / xlabel / ylabel / 刻度 / 图例 / 标注，每个位置至少
#: 承载矩阵里的两项，六个位置合起来把六项都盖到不止一次。
LIBRARY = f"""\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1], [0, 1], label={TOKENS["mu"]!r})
    ax.plot([0, 1], [1, 0], label={TOKENS["cmp"]!r})
    ax.set_title({TOKENS["sci"] + " " + TOKENS["greek"]!r})
    ax.set_xlabel({TOKENS["degc"] + " " + TOKENS["angstrom"]!r})
    ax.set_ylabel({TOKENS["cmp"] + " " + TOKENS["mu"]!r})
    ax.set_xticks([0, 1])
    ax.set_xticklabels([{TOKENS["sci"]!r}, {TOKENS["greek"]!r}])
    ax.annotate({TOKENS["degc"] + " " + TOKENS["angstrom"]!r}, xy=(0.5, 0.5), xytext=(0.2, 0.8))
    ax.legend()
    fig.savefig("{STEM}.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("matrix-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def rendered(library, tmp_path_factory):
    """一次 worker 会话：manifest + 原图 PDF + 原图 PNG（零 override）。"""
    out = tmp_path_factory.mktemp("matrix-out")
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, [])
        assert not resp.get("warnings"), resp["warnings"]
        pdf = out / f"{STEM}.pdf"
        png = out / f"{STEM}.png"
        w.export(STEM, [], str(pdf), "pdf")
        w.export(STEM, [], str(png), "png", dpi=150)
        return {"manifest": resp["manifest"], "pdf": pdf, "png": png}
    finally:
        pool.discard(w)


def _texts(man):
    return [e for e in man["elements"] if e.get("label") and e.get("editable")]


@needs_worker
def test_every_text_site_is_in_the_manifest_and_draws_every_character(rendered):
    """预览侧：六个位置的文字都进了元素树，而且**没有一个字符是方框**。"""
    man = rendered["manifest"]
    roles = [e["role"] for e in man["elements"]]
    # 六个位置在元素树里的角色名：title / axis_label（x 与 y 各一）/ ticklabel /
    # legend_text / text（annotate 出来的是 Text）
    for role, n in (("title", 1), ("axis_label", 2), ("ticklabel", 2), ("legend_text", 2), ("text", 1)):
        assert roles.count(role) >= n, f"缺 {role}×{n}：{sorted(set(roles))}"
    seen = " ".join(e.get("label", "") for e in man["elements"])
    for name, tok in TOKENS.items():
        assert tok in seen, f"{name} 没进任何元素的 label：{tok!r}"
    boxes = {e["gid"]: e["glyphs_missing"] for e in man["elements"] if e.get("glyphs_missing")}
    assert boxes == {}, f"这些字符会画成方框：{boxes}"


@needs_worker
def test_original_pdf_text_layer_reads_back_the_same_characters(rendered):
    """原图 PDF：矢量文字，文本层抽回来的就是写进去的那几串（语义一致）。"""
    with pymupdf.open(rendered["pdf"]) as doc:
        text = " ".join(p.get_text() for p in doc)
    norm = " ".join(text.split())
    for name, tok in TOKENS.items():
        assert tok in norm, f"原图 PDF 文本层里找不到 {name}：{tok!r}\n{norm[:400]}"


@needs_worker
def test_original_png_is_really_rendered(rendered):
    with pymupdf.open(rendered["png"]) as doc:
        pix = doc[0].get_pixmap()
    assert pix.width > 100 and pix.height > 100
    # 不是一张白纸：有相当数量的非白像素（文字、线条、刻度）
    dark = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 128)
    assert dark > 500, dark


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(m, "PROJECTS", {})
    monkeypatch.setattr(m, "DEFAULT_PROJECT", None)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "userdata"))
    m.app.config["TESTING"] = True
    return m.app.test_client()


@pytest.mark.parametrize("family", pb.CANVAS_TEXT_FAMILIES)
def test_canvas_families_all_draw_the_matrix(family):
    """画布文字：三个通用族对矩阵里每个字符都画得出（预检两侧共用的判据）。"""
    for bold in (False, True):
        for italic in (False, True):
            assert pb.missing_glyphs(ALL, family, bold, italic) == [], (family, bold, italic)


def test_canvas_pdf_and_png_carry_the_matrix(client, tmp_path):
    """画布 PDF + PNG 同一份快照：PDF 文本层读回全部字符；PNG 真渲染。"""
    objects = [
        {
            "type": "text",
            "text": f"{TOKENS['sci']} {TOKENS['mu']} {TOKENS['greek']}",
            "x_mm": 5,
            "y_mm": 5 + i * 12,
            "w_mm": 110,
            "h_mm": 10,
            "size_pt": 9,
            "font_family": fam,
        }
        for i, fam in enumerate(pb.CANVAS_TEXT_FAMILIES)
    ] + [
        {
            "type": "text",
            "text": f"{TOKENS['degc']} {TOKENS['angstrom']} {TOKENS['cmp']}",
            "x_mm": 5,
            "y_mm": 45,
            "w_mm": 110,
            "h_mm": 10,
            "size_pt": 9,
            "bold": True,
            "italic": True,
        }
    ]
    resp = client.post(
        "/api/export",
        json={
            "page_w_mm": 120,
            "page_h_mm": 60,
            "formats": ["pdf", "png"],
            "dpi": 150,
            "stem": "matrix",
            "objects": objects,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    files = {Path(f["name"]).suffix: Path(body["export_dir"]) / f["name"] for f in body["files"]}
    assert {".pdf", ".png"} <= set(files)
    with pymupdf.open(files[".pdf"]) as doc:
        text = " ".join(" ".join(p.get_text().split()) for p in doc)
    for name, tok in TOKENS.items():
        assert tok in text, f"画布 PDF 文本层里找不到 {name}：{tok!r}\n{text}"
    with pymupdf.open(files[".png"]) as doc:
        pix = doc[0].get_pixmap()
    dark = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 128)
    assert dark > 200, dark
