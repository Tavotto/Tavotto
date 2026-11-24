"""公式与中文混排的分段排（`engine/mixedtext.py`，ADR 0045 §7，摸底 T-002）。

尺子与被测函数分开：

* 「切了没有、切得对不对」看 `plan()` 的输出与**导出物**（PDF 文本层读回汉字、位图的墨迹）；
* 「不该变的没变」看**字节**：没有混排的图，武装前后 PNG / PDF / SVG（路径与文字两种）逐字节相同；
* 「量的 == 画的」看 Agg 位图里的墨迹落在 `get_window_extent` 里（0°、90°、30°，三种水平对齐）。

全部跑在 worker 解释器里（与 `test_cjk_figure_text.py` 同一套驱动方式），所以 CI 的 matplotlib
兼容矩阵（3.8 / 3.10 / 3.11）各验一遍。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import pool

WORKER_PY = pool.find_worker_python()

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"

_PRELUDE = """\
import io, json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import mixedtext, overrides

CJK = list(overrides.cjk_fallback_tail())
LATIN = "DejaVu Sans"
plt.rcParams["font.family"] = [LATIN, *CJK]
plt.rcParams["svg.hashsalt"] = "tavotto"
"""


def _run(body: str, *args: str) -> dict:
    out = subprocess.run(
        [WORKER_PY, "-c", _PRELUDE + body, str(ENGINE_DIR), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def has_cjk() -> bool:
    return bool(_run("print(json.dumps({'cjk': CJK}))")["cjk"])


_PLAN = r"""
prop = font_manager.FontProperties(family=[LATIN, *CJK])
mixedtext.arm()
cases = {
    "unit": "浓度 ($\\mu$mol/L)",
    "several": "$\\Delta G$ 随 $T$ 变化 (kJ·mol$^{-1}$)",
    "escaped": "价格 \\$5 ($x$)",
    "inside_math": "$\\mathrm{浓度}$ (mol)",
    "no_math": "浓度 (mol)",
    "latin": "Conc. ($\\mu$mol/L)",
}
out = {k: mixedtext.plan(v, prop, True) for k, v in cases.items()}
out["tex"] = mixedtext.plan(cases["unit"], prop, "TeX")
out["plain_call"] = mixedtext.plan(cases["unit"], prop, False)
# 正文脸自己画得出汉字：不切（mathtext 原样就对）
out["cjk_primary"] = mixedtext.plan(cases["unit"], font_manager.FontProperties(family=[*CJK, LATIN]), True)
# 回退链里一张中文脸都没有：切了也是方框，不切
out["no_chain"] = mixedtext.plan(cases["unit"], font_manager.FontProperties(family=[LATIN]), True)
mixedtext.disarm()
out["disarmed"] = mixedtext.plan(cases["unit"], prop, True)
with mixedtext.suspended():
    out["suspended_inside"] = mixedtext.armed()
mixedtext.arm()
with mixedtext.suspended():
    out["suspended_armed_inside"] = mixedtext.armed()
out["suspended_after"] = mixedtext.armed()
print(json.dumps(out, ensure_ascii=False))
"""


def test_plan_splits_only_the_characters_the_math_face_cannot_draw(has_cjk):
    if not has_cjk:
        pytest.skip("worker 的解释器找不到任何中日韩候选字体")
    out = _run(_PLAN)
    seg = lambda rows: [tuple(r) for r in rows]  # noqa: E731
    assert seg(out["unit"]) == [("浓度 ", False), ("($\\mu$mol/L)", True)]
    assert seg(out["several"]) == [
        ("$\\Delta G$", True),
        (" 随 ", False),
        ("$T$", True),
        (" 变化 ", False),
        ("(kJ·mol$^{-1}$)", True),
    ]
    # 公式外的 `\$` 是转义的美元号：落到普通文字段时要还原成 `$`
    assert seg(out["escaped"]) == [("价格 ", False), ("\\$5 ($x$)", True)]
    # 公式里面的汉字归 mathtext 的字体集管，不切；没有 `$` 的行本来就走回退链
    assert out["inside_math"] is None
    assert out["no_math"] is None
    assert out["latin"] is None
    assert out["tex"] is None and out["plain_call"] is None
    assert out["cjk_primary"] is None
    assert out["no_chain"] is None
    assert out["disarmed"] is None
    assert out["suspended_inside"] is False and out["suspended_armed_inside"] is False
    assert out["suspended_after"] is True


_BYTES = r"""
import hashlib
def figure():
    fig, ax = plt.subplots(figsize=(3, 2), dpi=100)
    ax.plot([0, 1], [0, 1])
    ax.set_ylabel("Conc. ($\\mu$mol/L)")
    ax.set_title("中文标题")
    ax.set_xlabel("$x$ (m)")
    ax.text(0.5, 0.5, "温度 (°C)", rotation=30)
    return fig
META = {"png": {"Software": None}, "pdf": {"Creator": None, "Producer": None, "CreationDate": None},
        "svg": {"Date": None}}
def digests():
    out = {}
    for fmt in ("png", "pdf", "svg"):
        b = io.BytesIO()
        figure().savefig(b, format=fmt, metadata=META[fmt])
        out[fmt] = hashlib.sha256(b.getvalue()).hexdigest()
    plt.rcParams["svg.fonttype"] = "none"
    b = io.BytesIO()
    figure().savefig(b, format="svg", metadata=META["svg"])
    out["svg-text"] = hashlib.sha256(b.getvalue()).hexdigest()
    plt.rcParams["svg.fonttype"] = "path"
    return out
before = digests()
mixedtext.arm()
after = digests()
print(json.dumps({"before": before, "after": after}))
"""


def test_figures_without_mixed_lines_are_byte_identical_when_armed():
    """绝大多数图没有「公式 + 正文脸画不出的汉字」：武装与否，四种产物逐字节相同。"""
    out = _run(_BYTES)
    assert out["before"] == out["after"]


_EXPORT = r"""
out_dir = sys.argv[2]
mixedtext.arm() if sys.argv[3] == "on" else mixedtext.disarm()
# Type 42 才带 ToUnicode，文本层读得回汉字（Type 3 的非 ASCII 字形读不回，尺子就不灵了）
plt.rcParams["pdf.fonttype"] = 42
fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([0, 1], [0, 1])
ax.set_ylabel("浓度 ($\\mu$mol/L)")
ax.set_xlabel("$\\Delta G$ 随 $T$ 变化 (kJ·mol$^{-1}$)")
fig.savefig(out_dir + "/mix.pdf")
print(json.dumps({"ok": True}))
"""


@pytest.mark.parametrize("state", ["on", "off"])
def test_exported_pdf_reads_back_the_chinese_only_when_armed(tmp_path, has_cjk, state):
    if not has_cjk:
        pytest.skip("worker 的解释器找不到任何中日韩候选字体")
    _run(_EXPORT, str(tmp_path), state)
    with pymupdf.open(tmp_path / "mix.pdf") as doc:
        text = "".join(p.get_text() for p in doc)
    has = [w for w in ("浓度", "随", "变化") if w in text]
    if state == "on":
        assert has == ["浓度", "随", "变化"], text
    else:
        # 尺子先验活：不分段时就是方框（matplotlib 自己的行为）
        assert has == [], text
    assert "mol/L" in text, text


_INK = r"""
import numpy as np
mixedtext.arm()
rows = []
for rot, ha in [(0, "left"), (0, "center"), (0, "right"), (90, "center"), (30, "right")]:
    fig = plt.figure(figsize=(4, 4), dpi=100)
    t = fig.text(0.5, 0.5, "浓度 ($\\mu$mol/L) 随 $T$", rotation=rot, ha=ha, va="center", fontsize=14)
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    bb = t.get_window_extent(r)
    img = np.asarray(fig.canvas.buffer_rgba())[:, :, :3]
    ys, xs = np.nonzero(img.min(axis=2) < 200)
    H = img.shape[0]
    ink = (float(xs.min()), float(H - 1 - ys.max()), float(xs.max() + 1), float(H - ys.min()))
    w_sum = sum(
        r.get_text_width_height_descent(s, t.get_fontproperties(), m)[0]
        for s, m in mixedtext.plan(t.get_text(), t.get_fontproperties(), True)
    )
    w_line = r.get_text_width_height_descent(t.get_text(), t.get_fontproperties(), True)[0]
    rows.append({"rot": rot, "ha": ha, "bb": [bb.x0, bb.y0, bb.x1, bb.y1], "ink": ink,
                 "w_sum": w_sum, "w_line": w_line})
    plt.close(fig)
print(json.dumps(rows))
"""


def test_drawn_ink_stays_inside_the_measured_box(has_cjk):
    """量的 == 画的：布局、tight、命中框都靠 `get_window_extent`，墨迹不许跑出去，也不许缩在一角。"""
    if not has_cjk:
        pytest.skip("worker 的解释器找不到任何中日韩候选字体")
    for row in _run(_INK):
        x0, y0, x1, y1 = row["bb"]
        ix0, iy0, ix1, iy1 = row["ink"]
        tol = 2.0
        assert ix0 >= x0 - tol and iy0 >= y0 - tol and ix1 <= x1 + tol and iy1 <= y1 + tol, row
        # 盒子沿阅读方向的长度 ≈ 墨迹（各段宽度相加，首尾只差字的侧边距）
        assert abs(row["w_line"] - row["w_sum"]) < 1e-6, row
        span_box = max(x1 - x0, y1 - y0) if row["rot"] in (0, 90) else None
        if span_box is not None:
            span_ink = max(ix1 - ix0, iy1 - iy0)
            assert span_box - span_ink < 6.0, row
