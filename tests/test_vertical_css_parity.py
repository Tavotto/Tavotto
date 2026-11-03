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
    cjk = reg.cjk_record().path
    payload = {
        "fonts": {"serif": serif.as_uri(), "cjk": cjk.as_uri()},
        "cases": [{"text": t, "size_pt": s, "line_height": lh, "h_mm": h} for t, s, lh, h in CASES],
    }
    inp = tmp_path / "cases.json"
    inp.write_text(json.dumps(payload), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(PROBE), str(WEB), str(inp)], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        if "Executable doesn't exist" in proc.stderr or "browserType.launch" in proc.stderr:
            pytest.skip(f"没有可用的 Chromium（not_run）：{proc.stderr.strip()[:200]}")
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def test_columns_fold_the_same_in_the_browser_and_in_rendercore(tmp_path):
    reason = _skip_reason()
    if reason:
        pytest.skip(f"not_run：{reason}")
    pytest.importorskip("uharfbuzz")
    from tavotto.rendercore.hbshaper import HbFaceProvider
    from tavotto.rendercore.typography import faces_for, layout_text

    browser = _browser_columns(tmp_path)
    provider = HbFaceProvider(fonts.FontRegistry.discover())
    assert vertical  # 被测对象
    for (text, size, lh, h), cols in zip(CASES, browser):
        faces = faces_for(provider, "serif", False, False)
        block = layout_text(
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
