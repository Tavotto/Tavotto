"""「图内文字复制为标注」摆竖直位置用的三个度量常数（`web/src/lib/figureTextCopy.ts`）↔ 批准字体的真值。

标注第一行基线比行盒中线低 `size × (sTypoAscender + sTypoDescender) / 2`（`rendercore/typography.layout_text`：
`基线 = 框顶 + size × ((行高 − (asc − desc)) / 2 + asc)`，desc 为负）。前端没有字体度量，批准的三族各抄一个千分之一 em
的整数；这条用例从字体文件反推，字体换了 / 常数写偏了当场红（同源对，`docs/rules/repo/same-origin-pairs.md`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fontTools")

from tests.support.tsconst import exported_number  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "web/src/lib/figureTextCopy.ts").read_text(encoding="utf-8")
FONTS = ROOT / "src/tavotto/resources/fonts/liberation"


@pytest.mark.parametrize(
    "const,file",
    [
        ("BASELINE_SANS_PER_MILLE", "LiberationSans-Regular.ttf"),
        ("BASELINE_SERIF_PER_MILLE", "LiberationSerif-Regular.ttf"),
        ("BASELINE_MONO_PER_MILLE", "LiberationMono-Regular.ttf"),
    ],
)
def test_baseline_constants_are_the_approved_faces_typo_metrics(const, file):
    from fontTools.ttLib import TTFont

    path = FONTS / file
    if not path.is_file():
        pytest.skip("批准字体未取回（python scripts/fetch_fonts.py）")
    font = TTFont(path)
    os2, upm = font["OS/2"], font["head"].unitsPerEm
    truth = (os2.sTypoAscender + os2.sTypoDescender) / 2 / upm * 1000
    assert exported_number(SRC, const) == round(truth), (const, truth)
