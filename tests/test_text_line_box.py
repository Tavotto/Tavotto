"""横排文字的行盒常数：画布预览 ↔ 导出严格同源（ADR 0073 修订，2026-11-16）。

基线落在行盒中线下固定 em，与字体度量无关：导出 `rendercore/typography.BASELINE_BELOW_CENTER_PER_MILLE`，
画布 `web/src/lib/typography.ts` 的 `TEXT_BASELINE_BELOW_CENTER_PER_MILLE`（预览按它补齐浏览器的基线、
「图内文字复制为标注」按它摆第一行基线）。一边改了另一边当场红（`docs/rules/repo/same-origin-pairs.md`）。
"""

from __future__ import annotations

from pathlib import Path

from tavotto.rendercore import typography
from tests.support.tsconst import exported_number

ROOT = Path(__file__).resolve().parent.parent
TS = (ROOT / "web/src/lib/typography.ts").read_text(encoding="utf-8")


def test_the_canvas_and_the_export_put_the_baseline_at_the_same_offset_below_the_line_center():
    ts = exported_number(TS, "TEXT_BASELINE_BELOW_CENTER_PER_MILLE")
    assert ts == typography.BASELINE_BELOW_CENTER_PER_MILLE
    assert typography.BASELINE_BELOW_CENTER == ts / 1000


def test_the_offset_centers_both_latin_caps_and_cjk_ideographs():
    """取值的依据（不是任意数）：拉丁大写字母高的一半与中文字面中心之间——两边都离行盒中线不到 0.05 em。"""
    k = typography.BASELINE_BELOW_CENTER
    latin_cap_half = (0.655 / 2, 0.688 / 2)  # Liberation Serif / Sans 的 capHeight / 2
    cjk_center = 0.38  # Noto SC 字面（typo −0.12…0.88）的中心
    for c in (*latin_cap_half, cjk_center):
        assert abs(c - k) < 0.05, (c, k)
