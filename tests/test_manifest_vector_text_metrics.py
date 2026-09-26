"""manifest 量文字用的是矢量输出的那把尺，不是文档 dpi 下的 Agg（#576）。

现象（用户 PRB 三联图）：图例锚在 `upper right`，**第一次**拖动 / 缩放松手后图例偏左
约 5.7 px、偏下约 1.5 px。manifest 在文档 dpi（100）的 Agg 上量文字——hinting 加像素
取整，6.9 pt 的小字量出来比画布上的矢量 SVG 大一圈；锚在预设位置的图例左下角取决于
自身宽高，于是 manifest 报的锚点与画布不符，拖动把「锚点 + 位移」写成 `loc_frac`
（绝对左下角）后图例就跳过去。

判据全部以**预览 SVG 本身**为尺子（画布上挂的就是它）：图例边框在 SVG 里是一条路径，
解析它的坐标得到矢量下的真实框——与 manifest 的量法无关，是独立的一侧。

另有一条在 worker 解释器里直接跑引擎模块：度量方式只在 manifest 那一段生效，出来后
同一个 Agg 渲染器量到的仍是它自己的 hinting 值（矢量值不会串进之后的位图绘制），
进去时也不会读到上一次位图绘制留下的缓存。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "src" / "tavotto" / "engine"

SCRIPT_NAME = "fig_small_legend.py"
ENTRY = "main"
STEM = "SmallLegend"
LEGEND = "axes_0.legend"

#: 与用户那张图同一类：单栏窄图、文档 dpi 100、6.9 pt 的图例锚在右上角。
#: 小字号是要点——hinting 与像素取整在小字上差得最多。
LIBRARY = f"""\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(3.4, 2.6), dpi=100)
    for k, name in enumerate(("Free space", "Theory", "Time-domain FFT", "FFT probe magnitude")):
        ax.plot([0, 1], [k, k + 1], label=name)
    ax.set_xlabel(r"Distance $h$ (mm)", fontsize=9.3)
    ax.legend(loc="upper right", bbox_to_anchor=(0.98, 0.983), fontsize=6.9,
              handlelength=1.25, borderpad=0.23, labelspacing=0.12)
    fig.savefig("{STEM}.pdf")
"""

#: 容差：SVG 路径坐标 matplotlib 按 6 位有效数字写，量纲是 pt；0.02 pt 远小于一个像素，
#: 而修复前的偏差是 1~3 pt 量级（见 test_legend_box_matches_the_vector_svg 的反证）
TOL_PT = 0.02


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("small-legend")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture
def worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


def _apply(w, patches) -> dict:
    resp = w.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _svg_legend_frame(svg: str) -> tuple[float, float, float, float]:
    """预览 SVG 里图例边框（`<g id="axes_0.legend">` 的第一条路径）的框，pt、y 向下。"""
    m = re.search(r'<g id="axes_0\.legend">(.*?)</g>', svg, re.S)
    assert m, "预览 SVG 里没有图例组"
    d = re.search(r'<path d="([^"]+)"', m.group(1))
    assert d, "图例组里没有边框路径"
    nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", d.group(1))]
    xs, ys = nums[0::2], nums[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _svg_size_pt(svg: str) -> tuple[float, float]:
    vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert vb
    return float(vb.group(1)), float(vb.group(2))


def _manifest_box_pt(man: dict, svg: str, gid: str) -> tuple[float, float, float, float]:
    """manifest 的 bbox（figure 分数、top-origin）换成与 SVG 同一坐标系的 pt。"""
    W, H = _svg_size_pt(svg)
    x, y, w, h = next(e["bbox"] for e in man["elements"] if e["gid"] == gid)
    return x * W, y * H, (x + w) * W, (y + h) * H


def _close(a, b, tol=TOL_PT) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b, strict=True))


def test_legend_box_matches_the_vector_svg(worker):
    """manifest 报的图例框 == 画布上那张矢量 SVG 里图例边框的框。"""
    man = _apply(worker, [])
    svg = worker.svg_path(STEM).read_text(encoding="utf-8")
    got, want = _manifest_box_pt(man, svg, LEGEND), _svg_legend_frame(svg)
    assert _close(got, want), (got, want)


def _svg_legend_text_origins(svg: str) -> list[tuple[float, float]]:
    """每条图例文字在预览 SVG 里的落笔点（`translate(X Y)`，基线左端，pt、y 向下）。"""
    out = []
    for j in range(4):
        m = re.search(
            rf'<g id="axes_0\.legend\.texts_{j}">.*?translate\(([-\d.e]+) ([-\d.e]+)\)', svg, re.S
        )
        assert m, f"预览 SVG 里没有第 {j} 条图例文字"
        out.append((float(m.group(1)), float(m.group(2))))
    return out


def test_legend_texts_sit_where_the_vector_svg_draws_them(worker):
    """图例里的字（偏移是 draw 时写死的）也按同一把尺：左边与行距都与画布上的 SVG 一致。

    框对上、字对不上是最容易漏的一格：图例本体的框是测量时现算的，子项偏移却是布局
    draw 写死的——不在测量阶段按矢量度量补排版，字会跟着 Agg 算出的图例宽度挪开。
    """
    man = _apply(worker, [])
    svg = worker.svg_path(STEM).read_text(encoding="utf-8")
    W, H = _svg_size_pt(svg)
    origins = _svg_legend_text_origins(svg)
    boxes = [
        next(e["bbox"] for e in man["elements"] if e["gid"] == f"{LEGEND}.texts_{j}")
        for j in range(4)
    ]
    for (ox, _), b in zip(origins, boxes, strict=True):
        assert abs(b[0] * W - ox) <= TOL_PT, (b[0] * W, ox)
    svg_steps = [origins[j + 1][1] - origins[j][1] for j in range(3)]
    man_steps = [(boxes[j + 1][1] - boxes[j][1]) * H for j in range(3)]
    assert all(abs(a - b) <= TOL_PT for a, b in zip(svg_steps, man_steps, strict=True)), (
        svg_steps,
        man_steps,
    )


def test_writing_the_reported_anchor_does_not_move_the_legend(worker):
    """用户看到的那一跳：把 manifest 报的锚点原样写成 loc_frac，图例在画布上一动不动。"""
    man0 = _apply(worker, [])
    frame0 = _svg_legend_frame(worker.svg_path(STEM).read_text(encoding="utf-8"))
    anchor = next(e["anchor"] for e in man0["elements"] if e["gid"] == LEGEND)

    _apply(worker, [{"gid": LEGEND, "prop": "loc_frac", "value": anchor}])
    frame1 = _svg_legend_frame(worker.svg_path(STEM).read_text(encoding="utf-8"))
    assert _close(frame0, frame1), (frame0, frame1)


#: 在 worker 解释器里直接量：同一个 Agg 渲染器，manifest 那一段前、中、后各量一次。
_ISOLATION = """\
import io, json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_svg import RendererSVG
import manifest

fig, ax = plt.subplots(figsize=(3.4, 2.6), dpi=100)
t = ax.text(0.1, 0.5, "Time-domain FFT", fontsize=6.9)
fig.canvas.draw()
r = fig.canvas.get_renderer()

def h():
    return t.get_window_extent(r).height

before = h()  # 先留下一份 Agg 自己的缓存值（模拟之前画过一张预览位图）
with manifest.vector_text_metrics() as arm:
    arm(fig.canvas.get_renderer())
    assert fig.canvas.get_renderer() is r  # 挂在 canvas 的那个实例上
    inside = h()
after = h()
# 独立的一侧：同一段字在矢量 SVG 渲染器上的窗口框（SVG 以 pt 计，量时 dpi 设 72 再换回像素）
fig.set_dpi(72)
svg_h = t.get_window_extent(RendererSVG(3.4, 2.6, io.StringIO())).height * 100 / 72
fig.set_dpi(100)
print(json.dumps({"before": before, "inside": inside, "after": after, "vector": svg_h}))
"""


def test_vector_metrics_apply_only_inside_the_manifest_section():
    out = subprocess.run(
        [WORKER_PY, "-c", _ISOLATION, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    # 这把尺子两侧确实不同，否则下面三条恒真
    assert abs(got["before"] - got["vector"]) > 0.05, got
    # 里面：矢量那把尺（进门时清了缓存，没读到 before 留下的 hinting 值）
    assert got["inside"] == pytest.approx(got["vector"], abs=1e-6), got
    # 出来：Agg 回到自己的度量（出门时也清了，矢量值没串进之后的位图绘制）
    assert got["after"] == pytest.approx(got["before"], abs=1e-9), got
