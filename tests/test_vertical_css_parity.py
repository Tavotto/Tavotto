"""竖排折行：预览（浏览器 CSS）与导出（RenderCore）逐列相同（ADR 0163「预览与导出同源」）。

横排的折行只有一份「语义约定」（前端 CSS 与 `typography.break_lines` 各写各的，靠 `textFit` 的余量兜底）；
竖排沿用同一机制，但这里多加一道**真浏览器**的对账：同样的字体文件（批准字体）、同样的样式
（`TextView` 的竖排样式），把几段文字挂进真 Chromium，量每个字落在第几列，与 `vertical.layout` 排出来的
每一列逐列比。比的是**折行规则**（CJK 逐字、拉丁按词、列长 = 框高），不是字体度量。

两处刻意避开的已知差异（横排同样存在、不是竖排引入的）：行首 / 行尾禁则（kinsoku：浏览器不让「，。」出现在列首，
RenderCore 不做），所以用例文字里没有让标点落在折点上的写法。

要真 Chromium：`web/node_modules` 里有 `@playwright/test` 且已装浏览器，node 可用；缺任一样 skip（not_run）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, vertical

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PROBE = ROOT / "tests" / "support" / "vertical_css_probe.mjs"

CASES = [
    # (文字, 字号 pt, 行距, 框高 mm)
    ("弹性段刚度与塑性段屈服强度的比较测试结果", 12, 1.25, 30),
    ("Elastic modulus of the steel specimen was measured at room temperature", 10, 1.25, 40),
    ("弹性段 E = 210 GPa 与塑性段 σy = 355 MPa 的拉伸曲线对比", 12, 1.25, 35),
    ("样品 A1 与 B2 在 300 K 下的结果", 11, 1.4, 25),
    ("Stress 应力 and strain 应变 curves of the specimens", 9, 1.25, 20),
]


def _skip_reason() -> str | None:
    if shutil.which("node") is None:
        return "没有 node"
    if not (WEB / "node_modules" / "@playwright" / "test").exists():
        return "web/node_modules 里没有 @playwright/test（没装前端依赖）"
    reg = fonts.FontRegistry.discover()
    if reg.missing:
        return f"批准字体不全：缺 {reg.missing}"
    return None


def _browser_columns(tmp_path: Path):
    reg = fonts.FontRegistry.discover()
    serif = reg.record_for("serif", False, False).path
    cjk = reg.cjk_record("serif").path
    payload = {
        "fonts": {"serif": serif.as_uri(), "cjk": cjk.as_uri()},
        "cases": [{"text": t, "size_pt": s, "line_height": lh, "h_mm": h} for t, s, lh, h in CASES],
        "formula_cases": _formula_payload(),
    }
    inp = tmp_path / "cases.json"
    inp.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(PROBE), str(WEB), str(inp)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if proc.returncode != 0:
        if "Executable doesn't exist" in proc.stderr or "browserType.launch" in proc.stderr:
            pytest.skip(f"没有可用的 Chromium（not_run）：{proc.stderr.strip()[:200]}")
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


# (各段: 文字 | 公式盒, 字号 pt, 行距, 框高 mm)：公式 em 尺寸取 matplotlib mathtext 的典型量级
FORMULA_BOXES = {
    "frac": {"width": 1.1, "ascent": 0.95, "descent": 0.55},
    "sq": {"width": 0.62, "ascent": 0.72, "descent": 0.06},
}
FORMULA_CASES = [
    # 公式在列中、不折行
    (["弹性段 ", "frac", " 与塑性段"], 12, 1.25, 80),
    # 公式靠近折点：前面的字把列填到只剩放不下公式 → 整个公式换到下一列
    (["材料屈服强度比较试验的结果为 ", "frac", " 数值", "sq", " 之差"], 12, 1.25, 30),
    # 拉丁文字混排、行距不同
    (["Stress ", "sq", " and ", "frac", " ratio"], 10, 1.4, 35),
]


def _metric_gap_em(provider) -> float:
    """侧躺基线的两种取法之差（em）：hhea（浏览器）vs OS/2 sTypo（导出）的 (上升 + 下降) / 2。"""
    from fontTools.ttLib import TTFont

    rec = fonts.FontRegistry.discover().record_for("serif", False, False)
    t = TTFont(rec.path)
    upem = t["head"].unitsPerEm
    hhea = (t["hhea"].ascent + t["hhea"].descent) / 2 / upem
    typo = (t["OS/2"].sTypoAscender + t["OS/2"].sTypoDescender) / 2 / upem
    return abs(hhea - typo)


def _formula_payload():
    cases = []
    for parts, size, lh, h in FORMULA_CASES:
        cases.append(
            {
                "parts": [
                    {"m": FORMULA_BOXES[p]} if p in FORMULA_BOXES else {"t": p} for p in parts
                ],
                "size_pt": size,
                "line_height": lh,
                "h_mm": h,
            }
        )
    return cases


def test_columns_fold_the_same_in_the_browser_and_in_rendercore(tmp_path):
    reason = _skip_reason()
    if reason:
        pytest.skip(f"not_run：{reason}")
    pytest.importorskip("uharfbuzz")
    from tavotto.rendercore.hbshaper import HbFaceProvider
    from tavotto.rendercore.typography import faces_for

    browser = _browser_columns(tmp_path)["cols"]
    provider = HbFaceProvider(fonts.FontRegistry.discover())
    assert vertical  # 被测对象
    for (text, size, lh, h), cols in zip(CASES, browser):
        faces = faces_for(provider, "serif", False, False)
        block = vertical.layout_text(
            {
                "text": text,
                "x_mm": 0,
                "y_mm": 0,
                "w_mm": 80,
                "h_mm": h,
                "size_pt": size,
                "line_height": lh,
                "writing_mode": "vertical",
            },
            faces,
        )
        mine = [c.logical for c in block.columns]
        # 浏览器：列文字的行尾空格留在原列里（white-space: pre-wrap 的悬挂空格），RenderCore 的折行已 rstrip
        assert [c.rstrip() for c in cols] == mine, (text, cols, mine)


def test_formulas_lie_sideways_in_the_column_the_same_in_the_browser_and_in_rendercore(tmp_path):
    """公式（ADR 0163）：预览里整块侧躺，所在列、沿列的位置、占的列长、横向落点都与导出 `_vertical_nodes` 逐项对账。

    导出一侧：`vertical.layout` 给出每个公式的列、`pos`、`length`，侧躺基线 `side_x`，公式盒（上升 / 下降）→ 墨迹横向范围
    `side_x − 下降…side_x + 上升`（顺时针转 90°，上升朝右），相对列中线；公式后第一个字的位置 = 公式的 `pos + length`。
    浏览器一侧：`MathFormula` 的竖排摆法（同一份源码 `verticalFormula.ts`）挂进真 Chromium，量 SVG 轮廓的包围盒。
    比的是**位置与占位**：沿列方向（列、pos、len、公式后第一个字）容差 0.6 pt；横向（相对列中线）另加一条已知差——
    浏览器用 hhea 度量定侧躺字的基线，导出用 OS/2 sTypo 度量（Liberation 的两组不同：见 `_metric_gap_em`），
    侧躺的拉丁字本来就带这一差（横向 ≈ 1 pt @12 pt）；公式跟同列的侧躺字走浏览器那条基线，所以横向容差 = 这一差 + 0.4 pt。"""
    reason = _skip_reason()
    if reason:
        pytest.skip(f"not_run：{reason}")
    pytest.importorskip("uharfbuzz")
    from tavotto.rendercore.hbshaper import HbFaceProvider
    from tavotto.rendercore.typography import faces_for, math_key

    browser = _browser_columns(tmp_path)["formulas"]
    provider = HbFaceProvider(fonts.FontRegistry.discover())
    tol = 0.6
    gap_pt = _metric_gap_em(provider)
    for (parts, size, lh, h), got in zip(FORMULA_CASES, browser):
        text = "".join(f"${p}$" if p in FORMULA_BOXES else p for p in parts)
        math = {}
        for p in parts:
            if p in FORMULA_BOXES:
                b = FORMULA_BOXES[p]
                math[math_key(p, "serif")] = {
                    "ok": True,
                    "width": b["width"],
                    "ascent": b["ascent"],
                    "descent": b["descent"],
                    "segments": [
                        ["M", 0.0, 0.0],
                        ["L", b["width"], 0.0],
                        ["L", b["width"], b["ascent"]],
                        ["Z"],
                    ],
                }
        faces = faces_for(provider, "serif", False, False)
        block = vertical.layout_text(
            {
                "text": text,
                "text_format": "latex",
                "x_mm": 0,
                "y_mm": 0,
                "w_mm": 80,
                "h_mm": h,
                "size_pt": size,
                "line_height": lh,
                "writing_mode": "vertical",
            },
            faces,
            math,
        )
        mine = []
        size = float(size)
        for ci, col in enumerate(block.columns):
            for k, item in enumerate(col.items):
                if item.kind != "math":
                    continue
                box = item.math[0]
                nxt = col.items[k + 1].pos if k + 1 < len(col.items) else None
                mine.append(
                    {
                        "col": ci,
                        "pos": item.pos,
                        "len": item.length,
                        "left": col.side_x - col.cx - (box["descent"]) * block.size,
                        "right": col.side_x - col.cx + (box["ascent"]) * block.size,
                        "next": nxt,
                    }
                )
        assert len(mine) == len(got) == sum(p in FORMULA_BOXES for p in parts), (parts, mine, got)
        for want, have in zip(mine, got):
            ctx = (parts, want, have)
            assert have["col"] == want["col"], ctx
            for key in ("pos", "len"):
                assert have[key] == pytest.approx(want[key], abs=tol), (key, ctx)
            for key in ("left", "right"):
                assert have[key] == pytest.approx(want[key], abs=gap_pt * size + 0.4), (key, ctx)
            # 宽度（右 − 左）不受基线差影响：必须逐点相同——侧躺的整块公式，上升 + 下降 = 横向厚度
            assert have["right"] - have["left"] == pytest.approx(
                want["right"] - want["left"], abs=0.3
            ), ctx
            if want["next"] is not None and have["next"] is not None:
                assert have["next"] == pytest.approx(want["next"], abs=tol), ("next", ctx)
