"""Collection 改色保留源颜色的逐元素透明度（`overrides._rgb_keeping_source_alpha`）。

matplotlib 的 `set_color` / `set_facecolor` 是整体替换：`_alpha is None` 时
`to_rgba_array('#d62728', None)` 把每一段都补成不透明。科研绘图里常见的
「同一色相、逐段透明度表示强弱」的 LineCollection，改一次颜色就全部变实心
（3.11.2 实测，清空改色后才恢复）。

本文件钉住的合同（夹具是原创的固定坐标 / 颜色 / 透明度）：

1. 不带 Tavotto 钩子的原生 matplotlib 与空编辑导出逐像素相同（同一 Agg、同 dpi / figsize）；
2. 只改 RGB：逐段 alpha 原样保留（按白底合成反推 alpha）；
3. 「改色 → 改 alpha → 去掉改色只留 alpha → 清空」每一步热态 == 新 worker 冷重放，清空 == 原样；
   改 alpha 是 Artist 全局 alpha，按 matplotlib 语义覆盖颜色自带的 alpha；
4. 同色的独立 marker、独立图例 proxy、自定义 handler、重复 label 的图例项一律不跟着变——
   不靠颜色 / 坐标 / label 关联，图例绑定只认现有规则（ADR 0034）；
5. 通用 Collection（散点逐点 RGBA 面色）同一条规则；`alpha=数组` 不新增标量 alpha 控件。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里；原生对照另起一个
不经 Tavotto 的解释器子进程。
"""

import subprocess

import pytest

from tavotto.engine import pool

Image = pytest.importorskip("PIL.Image")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_collection_alpha.py"
ENTRY = "main"
STEM = "CollAlpha"
W, H, DPI = 400, 300, 100

#: 线组三段的 y（数据坐标 = 轴坐标，轴占满整张图）与各段源 alpha
BAND_Y = (0.88, 0.78, 0.68)
BAND_ALPHA = (0.25, 0.6, 1.0)
BAND_X = (0.05, 0.45)
#: 散点三点（轴坐标）与各点源 alpha
DOT_XY = ((0.12, 0.25), (0.27, 0.25), (0.42, 0.25))
DOT_ALPHA = (0.3, 0.7, 1.0)
BLUE = (26, 77, 204)  # (0.1, 0.3, 0.8) 的 8 位值

LIBRARY = f"""\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.legend_handler import HandlerBase
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle

BLUE = (0.1, 0.3, 0.8)


class SwatchHandler(HandlerBase):
    def create_artists(self, legend, orig, xd, yd, width, height, fontsize, trans):
        r = Rectangle((xd, yd), width, height, facecolor=orig.get_color(), transform=trans)
        return [r]


def main():
    plt.rcParams.update({{"font.family": "DejaVu Sans", "font.size": 8}})
    fig = plt.figure(figsize=({W / DPI}, {H / DPI}), dpi={DPI})
    ax = fig.add_axes([0, 0, 1, 1])
    segs = [[({BAND_X[0]}, y), ({BAND_X[1]}, y)] for y in {BAND_Y!r}]
    lc = LineCollection(segs, linewidths=10, label="band",
                        colors=[(*BLUE, a) for a in {BAND_ALPHA!r}])
    ax.add_collection(lc)
    # 与线组同色、紧挨着末端、同名的独立 marker：不得被当成线组的一部分
    (dot,) = ax.plot([0.5], [{BAND_Y[1]}], "o", color=BLUE, ms=9, label="band")
    xy = np.array({DOT_XY!r})
    ax.scatter(xy[:, 0], xy[:, 1], s=500, edgecolors="none", label="pts",
               facecolors=[(*BLUE, a) for a in {DOT_ALPHA!r}])
    proxy = Line2D([], [], color=BLUE, lw=4)
    swatch = Line2D([], [], color=BLUE)
    ax.legend([proxy, lc, dot, swatch], ["band", "band", "band", "swatch"],
              handler_map={{swatch: SwatchHandler()}},
              loc="upper left", bbox_to_anchor=(0.58, 0.98), frameon=False)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    fig.savefig("{STEM}.png", dpi={DPI})


if __name__ == "__main__":
    main()
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("collection-alpha")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture(scope="module")
def hot(library):
    w = _worker(library)
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, patches=()):
    resp = worker.override(STEM, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _export(worker, patches, path):
    worker.export(STEM, list(patches), str(path), "png", DPI)
    return Image.open(path).convert("RGB")


def _gid(man, role):
    hits = [e["gid"] for e in man["elements"] if e["role"] == role]
    assert len(hits) == 1, (role, hits)
    return hits[0]


def _px(img, x, y):
    """轴坐标 (x, y) 处的像素（轴占满整张图）。"""
    return img.getpixel((int(x * W), int((1 - y) * H)))


def _alpha_on_white(px, rgb):
    """白底上不透明色 `rgb` 以 alpha 合成得到 `px`：按变化最大的通道反推 alpha。"""
    ch = max(range(3), key=lambda i: 255 - rgb[i])
    return (255 - px[ch]) / (255 - rgb[ch])


def _outside_band(img):
    """线组那三段的外接框之外的像素序列。"""
    x0, x1 = int(BAND_X[0] * W) - 2, int(BAND_X[1] * W) + 2
    y0, y1 = int((1 - BAND_Y[0]) * H) - 12, int((1 - BAND_Y[-1]) * H) + 12
    return [
        img.getpixel((x, y))
        for y in range(H)
        for x in range(W)
        if not (x0 <= x <= x1 and y0 <= y <= y1)
    ]


RED_HEX, RED = "#d62728", (214, 39, 40)


def test_native_matplotlib_equals_the_empty_edit_export(hot, library, tmp_path):
    """主语：不带捕获钩子的独立解释器 savefig 的 PNG vs Tavotto worker 空编辑导出的 PNG。"""
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    subprocess.run([WORKER_PY, str(library / SCRIPT_NAME)], cwd=native_dir, check=True, timeout=120)
    native = Image.open(native_dir / f"{STEM}.png").convert("RGB")
    ours = _export(hot, [], tmp_path / "empty.png")
    assert native.size == ours.size == (W, H)
    assert native.tobytes() == ours.tobytes()


def test_the_fixture_really_has_per_segment_alpha(hot, tmp_path):
    img = _export(hot, [], tmp_path / "orig.png")
    for y, a in zip(BAND_Y, BAND_ALPHA, strict=True):
        assert _alpha_on_white(_px(img, 0.25, y), BLUE) == pytest.approx(a, abs=0.02)


def test_recolouring_the_line_collection_keeps_each_segment_alpha(hot, tmp_path):
    gid = _gid(_man(hot), "linecoll")
    img = _export(hot, [{"gid": gid, "prop": "color", "value": RED_HEX}], tmp_path / "red.png")
    for y, a in zip(BAND_Y, BAND_ALPHA, strict=True):
        px = _px(img, 0.25, y)
        assert _alpha_on_white(px, RED) == pytest.approx(a, abs=0.02), (y, px)
        assert px[0] > px[2], f"y={y} 没有变成红色：{px}"


def test_recolouring_does_not_touch_same_coloured_independent_artists(hot, tmp_path):
    """同色的独立 marker、图例 proxy、自定义 handler 的色块：改线组颜色前后，线组外接框之外
    纯 BLUE 像素一个不少——它们不随线组改色（不靠颜色 / 坐标 / label 关联）。"""
    man = _man(hot)
    gid = _gid(man, "linecoll")
    before = _outside_band(_export(hot, [], tmp_path / "b.png"))
    after = _outside_band(
        _export(hot, [{"gid": gid, "prop": "color", "value": RED_HEX}], tmp_path / "a.png")
    )
    n_blue = sum(p == BLUE for p in before)
    assert n_blue > 200, "夹具里同色的独立对象太少，这条判不出东西"
    assert sum(p == BLUE for p in after) == n_blue
    # 图例里三个同名 "band" 项：proxy 与独立 marker 不得被绑到线组上
    legend = [e for e in man["elements"] if "legend_entry" in e]
    by_index = {e["legend_entry"]["index"]: e["legend_entry"] for e in legend}
    assert by_index[0].get("source_gid") != gid, "proxy 不得按 label / 颜色绑到线组"
    assert by_index[3].get("source_gid") != gid, "自定义 handler 的项不得绑到线组"


def test_colour_alpha_sequence_hot_equals_cold_and_undo_is_original(hot, library, tmp_path):
    """改色 → 改 alpha → 去掉改色只留 alpha → 清空：每步热态与新 worker 冷重放逐像素相同。"""
    gid = _gid(_man(hot), "linecoll")
    red = {"gid": gid, "prop": "color", "value": RED_HEX}
    alpha = {"gid": gid, "prop": "alpha", "value": 0.5}
    steps = [
        ("orig", []),
        ("red", [red]),
        ("red+alpha", [red, alpha]),
        ("alpha", [alpha]),
        ("clear", []),
    ]
    hot_png = {}
    for tag, patches in steps:
        _man(hot, patches)
        hot_png[tag] = _export(hot, patches, tmp_path / f"hot-{tag}.png").tobytes()
    for tag, patches in steps:
        cold = _worker(library)
        try:
            assert _export(cold, patches, tmp_path / f"cold-{tag}.png").tobytes() == hot_png[tag], (
                tag
            )
        finally:
            pool.discard(cold)
    assert hot_png["clear"] == hot_png["orig"]
    # 全局 alpha 覆盖颜色自带的 alpha（matplotlib 语义）：三段一样淡
    img = Image.open(tmp_path / "hot-red+alpha.png").convert("RGB")
    for y in BAND_Y:
        assert _alpha_on_white(_px(img, 0.25, y), RED) == pytest.approx(0.5, abs=0.02)
    # 去掉改色只留 alpha：回到源 RGB，三段同为 0.5
    img = Image.open(tmp_path / "hot-alpha.png").convert("RGB")
    for y in BAND_Y:
        assert _alpha_on_white(_px(img, 0.25, y), BLUE) == pytest.approx(0.5, abs=0.02)


def test_explicit_alpha_in_the_colour_wins_over_the_source_alpha(hot, tmp_path):
    gid = _gid(_man(hot), "linecoll")
    img = _export(hot, [{"gid": gid, "prop": "color", "value": "#d6272880"}], tmp_path / "x.png")
    for y in BAND_Y:
        assert _alpha_on_white(_px(img, 0.25, y), RED) == pytest.approx(0x80 / 255, abs=0.02)


def test_recolouring_scatter_faces_keeps_each_point_alpha(hot, tmp_path):
    gid = _gid(_man(hot), "scatter")
    img = _export(hot, [{"gid": gid, "prop": "facecolor", "value": RED_HEX}], tmp_path / "s.png")
    for (x, y), a in zip(DOT_XY, DOT_ALPHA, strict=True):
        assert _alpha_on_white(_px(img, x, y), RED) == pytest.approx(a, abs=0.02)
    _man(hot)


ARRAY_ALPHA = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection


def main():
    fig = plt.figure(figsize=(2, 1.5), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    lc = LineCollection([[(0, y), (1, y)] for y in (0.3, 0.7)], linewidths=8,
                        colors=[(0.1, 0.3, 0.8)] * 2, alpha=np.array([0.3, 0.9]))
    ax.add_collection(lc)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.savefig("Arr.png")
"""


def test_array_alpha_gets_no_scalar_alpha_control_and_recolour_keeps_it(tmp_path):
    """源脚本 `alpha=数组`：能力声明不给标量 alpha（`manifest._alpha_field`），改色照旧按数组广播。"""
    (tmp_path / "arr.py").write_text(ARRAY_ALPHA, encoding="utf-8")
    w = pool.one_shot("arr.py", str(tmp_path), ENTRY)
    try:
        w.ensure_built()
        man = w.override("Arr", [])["manifest"]
        el = next(e for e in man["elements"] if e["role"] == "linecoll")
        assert "alpha" not in {f["prop"] for f in el["editable"]}
        w.export(
            "Arr",
            [{"gid": el["gid"], "prop": "color", "value": RED_HEX}],
            str(tmp_path / "a.png"),
            "png",
            100,
        )
        img = Image.open(tmp_path / "a.png").convert("RGB")
        for y, a in ((0.3, 0.3), (0.7, 0.9)):
            px = img.getpixel((100, int((1 - y) * 150)))
            assert _alpha_on_white(px, RED) == pytest.approx(a, abs=0.02)
    finally:
        pool.discard(w)
