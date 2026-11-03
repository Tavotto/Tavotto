"""manifest 的 `text_block`（ADR 0163「图内文字复制为标注」）：文字块的中心 / 尺寸 / 第一行基线 / 此刻的样式。

`manifest.py` 只在 worker 子进程里（flat import），所以这里起子进程跑，主语是它打印出来的 JSON。

**期望值不调用被测函数**，由两把独立的尺子给：
* 基线：用 `va="baseline"`（转了的再加 `rotation_mode="anchor"`）摆文字，**锚点就是第一行基线的起点**（matplotlib 的定义），锚点的显示坐标由
  `transform.transform(position)` 给——与探针排版毫无关系；左对齐时 `块中心 − (w/2)·u + (asc − h/2)·v` 必须落在锚点上；
* 尺寸：元素 `bbox` 是真实 artist 转后的外接框，`bbox.w = w|cosθ| + h|sinθ|`、`bbox.h = w|sinθ| + h|cosθ|` 对任意转角成立。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src" / "tavotto" / "engine"

SCRIPT = r"""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import manifest, overrides
from matplotlib.artist import Artist

MODE = os.environ.get("TC_MODE", "")
if MODE == "oldsig":
    # matplotlib 3.8 / 3.9 的签名：get_figure(self) 不接受 root（matplotlib 自己内部的调用不受影响，只对 manifest 模块生效）
    import sys
    _new = Artist.get_figure
    def _old(self, *a, **k):
        if "root" in k and sys._getframe(1).f_globals.get("__name__") == "manifest":
            raise TypeError("get_figure() got an unexpected keyword argument 'root'")
        return _new(self, *a, **k)
    Artist.get_figure = _old
elif MODE == "inner_boom":
    def _boom(*a, **k):
        raise RuntimeError("probe failed")
    manifest.IdentityTransform = _boom
elif MODE == "outer_boom":
    def _boom(*a, **k):
        raise RuntimeError("text_block failed")
    manifest.text_block = _boom

fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([0, 1, 2], [0, 1, 0], label="a")
ax.set_title("Title here")
ax.set_ylabel("Signal (V)")
texts = {
    "flat": ax.text(0.3, 0.3, "Hxg flat", va="baseline", ha="left", fontsize=11),
    "rot30": ax.text(0.5, 0.5, "Hxg rotated", va="baseline", ha="left", rotation=30, rotation_mode="anchor", fontsize=11),
    "rot90c": ax.text(0.8, 0.4, "Hxg vertical", va="baseline", ha="left", rotation=90, rotation_mode="anchor", fontsize=11),
    "two": ax.text(0.1, 0.8, "line1\nline two", va="baseline", ha="left", fontsize=10),
    "math": ax.text(0.6, 0.15, "rate $k^{2}$", va="baseline", ha="left", fontsize=10,
                    fontweight="bold", fontstyle="italic", color="#336699"),
    "wrapped": ax.text(0.2, 0.9, "a long long long long text that wraps", wrap=True, fontsize=8),
}
ann = ax.annotate("note", xy=(1, 1), xytext=(1.3, 0.8), arrowprops=dict(arrowstyle="->"))
st = overrides.FigState(fig)
manifest.instrument(st)
m = manifest.build_manifest(st, "T")
W, H = fig.get_size_inches() * fig.dpi
anchors = {}
for k, t in texts.items():
    x, y = t.get_transform().transform(t.get_position())
    anchors[k] = [float(x), float(y)]
byid = {}
for e in m["elements"]:
    byid[e["gid"]] = {"bbox": e["bbox"], "text_block": e.get("text_block"), "role": e["role"]}
gid_of = {}
for e in m["elements"]:
    pass
# 元素 gid ↔ 对象：按 instrument 登记的顺序 gid 是 axes_0.texts_<i>
order = [*texts.keys(), "ann"]
json.dumps(m)  # 整份 manifest 必须能 json 序列化（numpy 标量漏进来会在这里炸）
print(json.dumps({"W": W, "H": H, "anchors": anchors, "elements": byid, "order": order}))
"""


def _run_script(mode: str = "") -> dict:
    env = {**os.environ, "PYTHONPATH": str(ENGINE), "TAVOTTO_NO_TELEMETRY": "1", "TC_MODE": mode}
    r = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def run() -> dict:
    return _run_script()


def test_old_matplotlib_get_figure_signature_still_yields_blocks(run):
    """matplotlib 3.8 / 3.9 的 `Artist.get_figure()` 不接受 `root`：manifest 照常生成，文字照出 text_block，
    且与新签名下量出来的一致（#1672 评审 P1）。"""
    old = _run_script("oldsig")
    for key in ("flat", "rot30", "two", "math"):
        idx = old["order"].index(key)
        tb = old["elements"][f"axes_0.texts_{idx}"]["text_block"]
        assert tb is not None, key
        assert tb == _block(run, key)[1]


@pytest.mark.parametrize("mode", ["inner_boom", "outer_boom"])
def test_text_block_failure_never_sinks_the_manifest(mode):
    """text_block 内部（探针）或整个调用抛错：manifest 照常生成，文字元素只是没有 text_block。"""
    out = _run_script(mode)
    idx = out["order"].index("flat")
    el = out["elements"][f"axes_0.texts_{idx}"]
    assert el["role"] and el["bbox"]
    assert el["text_block"] is None


def _block(run: dict, key: str) -> tuple[dict, dict]:
    idx = run["order"].index(key)
    el = run["elements"][f"axes_0.texts_{idx}"]
    assert el["text_block"] is not None, key
    return el, el["text_block"]


def _px(run: dict, tb: dict) -> tuple[float, float, float, float, float]:
    """块中心 (x, y 向上的显示 px)、w、h、asc。"""
    W, H = run["W"], run["H"]
    return tb["center"][0] * W, (1 - tb["center"][1]) * H, tb["w"] * W, tb["h"] * H, tb["asc"] * H


@pytest.mark.parametrize("key,deg", [("flat", 0), ("rot30", 30), ("rot90c", 90)])
def test_first_baseline_lands_on_the_anchor_for_any_angle(run, key, deg):
    """va=baseline 的锚点就是第一行基线起点：块中心 −(w/2)·u 再沿「向下」移 (asc − h/2)，必须回到锚点。"""
    el, tb = _block(run, key)
    cx, cy, w, h, asc = _px(run, tb)
    th = math.radians(tb["rotation"])
    assert tb["rotation"] == pytest.approx(deg)
    u = (math.cos(th), math.sin(th))  # 显示坐标（y 向上）里的阅读方向
    v = (math.sin(th), -math.cos(th))  # 「向下」：顺时针转 90°
    bx = cx - (w / 2) * u[0] + (asc - h / 2) * v[0]
    by = cy - (w / 2) * u[1] + (asc - h / 2) * v[1]
    ax_, ay_ = run["anchors"][key]
    assert bx == pytest.approx(ax_, abs=0.05)
    assert by == pytest.approx(ay_, abs=0.05)


@pytest.mark.parametrize("key", ["flat", "rot30", "rot90c", "two"])
def test_block_size_agrees_with_the_real_artist_bbox_at_any_angle(run, key):
    el, tb = _block(run, key)
    W, H = run["W"], run["H"]
    _, _, w, h, _ = _px(run, tb)
    th = math.radians(tb["rotation"])
    bw, bh = el["bbox"][2] * W, el["bbox"][3] * H
    assert bw == pytest.approx(w * abs(math.cos(th)) + h * abs(math.sin(th)), abs=0.05)
    assert bh == pytest.approx(w * abs(math.sin(th)) + h * abs(math.cos(th)), abs=0.05)


def test_the_center_is_the_center_of_the_real_artist_bbox(run):
    el, tb = _block(run, "rot30")
    x, y, w, h = el["bbox"]
    assert tb["center"][0] == pytest.approx(x + w / 2, abs=1e-9)
    assert tb["center"][1] == pytest.approx(y + h / 2, abs=1e-9)


def test_multiline_reports_the_baseline_step_and_single_line_does_not(run):
    _, one = _block(run, "flat")
    _, two = _block(run, "two")
    assert "step" not in one
    H = run["H"]
    # 两行块比单行（同字号相近）高出恰好一个基线间距，且间距为正
    assert two["step"] > 0
    assert two["h"] * H > one["h"] * H * 1.5
    assert two["text"] == "line1\nline two"


def test_style_is_what_is_drawn_now(run):
    _, tb = _block(run, "math")
    assert tb["math"] is True
    assert tb["size"] == pytest.approx(10.0)
    assert tb["color"] == "#336699"
    assert tb["weight"] in ("bold", 700)
    assert tb["style"] == "italic"
    assert tb["ha"] == "left"
    _, flat = _block(run, "flat")
    assert flat["math"] is False and flat["style"] == "normal"


def test_wrapped_text_and_tick_groups_have_no_block(run):
    idx = run["order"].index("wrapped")
    assert (
        run["elements"][f"axes_0.texts_{idx}"]["text_block"] is None
    )  # wrap：排版依赖画布宽，不报
    assert run["elements"]["axes_0.xticks"]["text_block"] is None  # 刻度组是一组标签
    # 单个刻度标签有
    tick = next(v for k, v in run["elements"].items() if "xticklabels_" in k)
    assert tick["text_block"] is not None


def test_annotation_block_is_the_text_not_the_text_plus_arrow(run):
    el, tb = _block(run, "ann")
    W, H = run["W"], run["H"]
    # Annotation.get_window_extent 并了箭头；块的尺寸只是 "note" 这几个字（比并上箭头的框小得多）
    assert tb["w"] * W < 40 and tb["h"] * H < 20
