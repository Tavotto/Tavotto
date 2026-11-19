"""横排行盒：预览（浏览器 CSS）与导出（RenderCore）逐行同一条基线（ADR 0073 修订，2026-11-16）。

文字框里上下居中的前提是两边用同一个行盒：导出 `typography.line_box`（基线在行盒中线下固定 em、公式撑开所在行），
预览靠 `lib/lineBox.horizontalBaselineFixPx` 把浏览器按字体度量摆的基线挪过去、公式按 `MathFormula` 的 `lineBox`
形态撑行。这里把同样的摆法挂进真 Chromium（批准字体的文件），逐行比基线、逐个比公式轮廓的上下边、比框高
（框高由预览量了写回文档，导出画底色 / 描边用它）。浏览器按 hhea 摆、导出不读度量：Liberation 的 hhea 本来就
离固定值不到 0.02 em（旧导出按 typo 摆，与这里差约 0.1 em，#1669）；中文脸排第一的那一例差约 0.08 em，量的是补差本身。

要真 Chromium：`web/node_modules` 里有 `@playwright/test` 且已装浏览器，node 可用；缺任一样 skip（not_run）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.rendercore import fonts, typography

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
PROBE = ROOT / "tests" / "support" / "horizontal_css_probe.mjs"

BOXES = {
    "sq": {"width": 0.62, "ascent": 0.79, "descent": 0.0},  # x²：装得下，不改行距
    "frac": {"width": 1.1, "ascent": 1.2, "descent": 0.9},  # 高公式：撑开所在行
}

# (行: [文字 | 公式名…]…, 字号 pt, 行距, 框宽 mm, 粗体, 中文脸排第一)
CASES = [
    ([["Reaction kinetics"]], 12, 1.25, 80, False, False),
    ([["弹性段刚度"], ["与屈服强度"]], 10, 1.4, 80, False, False),
    ([["k = ", "sq"], ["a ", "frac", " b"], ["c"]], 12, 1.25, 80, False, False),
    ([["Bold ", "frac"]], 9, 1.25, 80, True, False),
    # 行盒度量来自 Noto SC（hhea 1.151 / −0.286：基线比固定值低约 0.08 em）——补差必须真的生效
    ([["弹性段刚度"], ["屈服 ", "frac"]], 12, 1.25, 80, False, True),
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


def _parts(line):
    return [{"m": BOXES[p]} if p in BOXES else {"t": p} for p in line]


def _browser(tmp_path: Path):
    reg = fonts.FontRegistry.discover()
    payload = {
        "fonts": {
            "serif": reg.record_for("serif", False, False).path.as_uri(),
            "serif_bold": reg.record_for("serif", True, False).path.as_uri(),
            "cjk": reg.cjk_record("serif").path.as_uri(),
        },
        "k_per_mille": typography.BASELINE_BELOW_CENTER_PER_MILLE,
        "cases": [
            {
                "lines": [_parts(ln) for ln in lines],
                "size_pt": s,
                "line_height": lh,
                "w_mm": w,
                "bold": b,
                "cjk_first": cj,
            }
            for lines, s, lh, w, b, cj in CASES
        ],
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


def test_every_line_sits_on_the_same_baseline_in_the_browser_and_in_rendercore(tmp_path):
    reason = _skip_reason()
    if reason:
        pytest.skip(f"not_run：{reason}")
    pytest.importorskip("uharfbuzz")
    from tavotto.rendercore.hbshaper import HbFaceProvider

    got = _browser(tmp_path)
    provider = HbFaceProvider(fonts.FontRegistry.discover())
    tol = 0.1
    for (lines, size, lh, w, bold, cjk_first), have in zip(CASES, got):
        text = "\n".join("".join(f"${p}$" if p in BOXES else p for p in ln) for ln in lines)
        math = {
            typography.math_key(p, "serif"): {"ok": True, "segments": [], **BOXES[p]}
            for ln in lines
            for p in ln
            if p in BOXES
        }
        faces = typography.faces_for(provider, "serif", bold, False)
        t = {
            "text": text,
            "text_format": "latex",
            "x_mm": 0,
            "y_mm": 0,
            "w_mm": w,
            "h_mm": 10,
            "size_pt": size,
            "line_height": lh,
            "bold": bold,
        }
        block = typography.layout_text(t, faces, math)
        assert block is not None and len(block.lines) == len(lines)
        ctx = (lines, have)
        if cjk_first:
            # 这一例的补差够大（≈ 1 pt）：补差没生效、或方向反了，基线就差出容差之外
            assert abs(have["shift_pt"]) > 2 * tol, ctx
        assert have["baselines"] == pytest.approx([ln.baseline for ln in block.lines], abs=tol), ctx
        want_f = [
            edge
            for ln in block.lines
            for _x, box, _src in ln.math
            for edge in (ln.baseline - box["ascent"] * size, ln.baseline + box["descent"] * size)
        ]
        have_f = [edge for f in have["formulas"] for edge in (f["top"], f["bottom"])]
        assert have_f == pytest.approx(want_f, abs=tol), ctx
        # 框高 = 各行行盒之和（预览量了写回 `h`，导出画底色 / 描边用它）
        slots, _s, _lh, _pad, interp, fmt = typography.text_params(t, math)
        units = typography.break_lines(text, faces, size, typography.mm2pt(w), interp, fmt, slots)
        height = sum(sum(typography.line_box(u, size, lh, slots)) for u in units)
        assert have["height"] == pytest.approx(height, abs=tol), ctx
