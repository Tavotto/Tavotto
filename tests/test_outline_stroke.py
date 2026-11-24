"""轮廓化描边的完整外观（ADR 0185 M7）：虚线、端型（箭头头）、描边内外对齐转成面积后，与原描边按导出画出来逐像素一样。

样本由前端生成（`web/src/lib/outlineStroke.golden.test.ts`：真 WASM 内核 + `lib/outlineStroke` 摆几何），写在
`tests/golden/outline_stroke_vectors.json`；那边核「现在算出来的 = 金样」，这里核「金样画出来 = 原描边画出来」。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tavotto.rendercore import plan

HAS = all(
    importlib.util.find_spec(m) is not None for m in ("pikepdf", "uharfbuzz", "PIL", "pypdfium2")
)
pytestmark = pytest.mark.skipif(not HAS, reason="RenderCore 依赖未装（not_run，不是绿）")

GOLDEN = Path(__file__).parent / "golden" / "outline_stroke_vectors.json"
CASES = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def provider():
    from tavotto.rendercore import fonts
    from tavotto.rendercore.hbshaper import HbFaceProvider

    return HbFaceProvider(fonts.FontRegistry.discover())


def _raster(objs, provider, tmp_path, name, scale=8):
    import pypdfium2 as pdfium

    from tavotto.rendercore import pdfwriter

    page = plan.compile_page(70, 45, objs, sources=None, faces=provider).page  # type: ignore[arg-type]
    out = tmp_path / f"{name}.pdf"
    pdfwriter.write_pdf(page, out, provider)
    return pdfium.PdfDocument(str(out))[0].render(scale=scale).to_pil().convert("L")


def _diff(a, b):
    from PIL import ImageChops

    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / total, sum(
        n for i, n in enumerate(hist) if i > 96
    )


def test_golden_has_every_kind():
    names = {c["name"] for c in CASES}
    assert {
        "dashed-curve-butt",
        "dotted",
        "heads-triangle-open",
        "align-inside",
        "align-outside-miter",
        "arrow-stealth",
    } <= names


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_outlined_area_renders_like_the_stroke(provider, tmp_path, case):
    a = _raster(case["original"], provider, tmp_path, "orig")
    b = _raster(case["outlined"], provider, tmp_path, "outl")
    mean, big = _diff(a, b)
    # 只差抗锯齿（曲线拍平与 PDF 自己描边的细微差别）：平均差 < 1 / 255，差得多的像素是边缘上零星几个
    assert a.getextrema()[0] < 128  # 真的画出了东西
    assert mean < 1.0, mean
    assert big < a.size[0] * a.size[1] * 0.001, big
    # 转出来的没有描边
    assert all(
        o.get("stroke") is False
        for o in case["outlined"]
        if o.get("fill") == case["original"][0]["color"]
    )
