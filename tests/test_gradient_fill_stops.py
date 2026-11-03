"""渐变色带填充的「两端取色」（`overrides.gradient_detect` / `gradient` 字段，2026-11-03）。

科研图里的渐变峰常见写法是一个 `PolyCollection`：几百条轴对齐长条逐条一色、再 `set_clip_path` 成峰形
（用户同门的 XPS 分峰图）。从前检查器只给一个「填充颜色」、改它就把整个渐变压成纯色。

钉这几件事：

1. **认得出、只认它**——横条叠成的竖向渐变 / 竖条排成的横向渐变出 `gradient` 字段、不出 facecolor；
   `fill_between`、少于 8 条、颜色都一样、两列并排的图例色块照旧出 facecolor。
2. **两端按屏幕说**——起端是上（竖向）/ 左（横向），y 轴反转的图照样是屏幕上的那一端。
3. **改得动、改得准**——写 `{stops: [起端, 止端]}` 后两端就是这两个颜色。
4. **还得回去、重放一致**——撤掉 override 回到脚本原样；热会话与全新 worker 全量重放给出同一份两端颜色。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_gradstops.py"
ENTRY = "main"
STEM = "GradStops"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path


def ramp(n, lo, hi, alpha=1.0):
    t = np.linspace(0.0, 1.0, n)[:, None]
    rgb = np.asarray(lo) + t * (np.asarray(hi) - np.asarray(lo))
    return np.column_stack([rgb, np.full(n, alpha)])


def vband(ax, x0, x1, y0, y1, n, lo, hi, alpha=1.0):
    # 横条自下而上：第 0 条（最下）是 hi、最上一条是 lo —— 与同门脚本同一方向
    edges = np.linspace(y0, y1, n + 1)
    verts = [[(x0, a), (x1, a), (x1, b), (x0, b)] for a, b in zip(edges[:-1], edges[1:])]
    coll = PolyCollection(verts, facecolors=ramp(n, hi, lo, alpha), edgecolors="none", linewidths=0)
    ax.add_collection(coll)
    return coll


def main():
    fig, axs = plt.subplots(1, 4, figsize=(10.0, 3.0))
    ax, ax2, ax3, ax4 = axs
    # axes_0.fill_0：竖向渐变（上浅蓝、下深蓝），裁成峰形；半透明，验 alpha 原样
    x = np.linspace(0.0, 10.0, 200)
    y = 0.1 + np.exp(-((x - 5.0) / 1.5) ** 2)
    band = vband(ax, 0.0, 10.0, 0.1, float(y.max()), 64, (0.8, 0.9, 1.0), (0.0, 0.2, 0.6), alpha=0.7)
    poly = np.vstack([np.column_stack([x, y]), [[10.0, 0.1], [0.0, 0.1], [0.0, y[0]]]])
    band.set_clip_path(PathPatch(Path(poly), transform=ax.transData))
    # axes_0.fill_1：普通 fill_between —— 照旧是「填充颜色」
    ax.fill_between(x, 0.1, 0.3, color="tab:orange")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.3)

    # axes_1.fill_0：横向渐变（竖条从左到右：左红、右黄）
    edges = np.linspace(0.0, 1.0, 33)
    verts = [[(a, 0.0), (b, 0.0), (b, 1.0), (a, 1.0)] for a, b in zip(edges[:-1], edges[1:])]
    ax2.add_collection(PolyCollection(verts, facecolors=ramp(32, (0.9, 0.1, 0.1), (1.0, 0.9, 0.1))))
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)

    # axes_2.fill_0：y 轴反转的竖向渐变 —— 屏幕上的「上端」是数据里 y 小的那一头
    vband(ax3, 0.0, 1.0, 0.0, 1.0, 32, (1.0, 1.0, 1.0), (0.0, 0.5, 0.0))
    ax3.set_xlim(0, 1)
    ax3.set_ylim(1, 0)

    # axes_3.fill_0：只有 4 条（不算渐变）；fill_1：64 条同一个颜色（纯色）；
    # fill_2：两列并排的小色块（图例样式，不是一个方向的色带）
    vband(ax4, 0.0, 0.2, 0.0, 0.2, 4, (1, 0, 0), (0, 0, 1))
    edges = np.linspace(0.3, 0.6, 65)
    ax4.add_collection(PolyCollection(
        [[(0.0, a), (0.2, a), (0.2, b), (0.0, b)] for a, b in zip(edges[:-1], edges[1:])],
        facecolors="tab:green"))
    sw = []
    for cx in (0.5, 0.8):
        e = np.linspace(0.7, 0.9, 17)
        sw += [[(cx, a), (cx + 0.1, a), (cx + 0.1, b), (cx, b)] for a, b in zip(e[:-1], e[1:])]
    ax4.add_collection(PolyCollection(sw, facecolors=np.vstack([ramp(16, (1, .8, .6), (.8, .3, 0))] * 2)))
    ax4.set_xlim(0, 1)
    ax4.set_ylim(0, 1)
    fig.savefig("GradStops.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("gradstops-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _render(figs, patches=()):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(STEM, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


@pytest.fixture(scope="module")
def manifest(library):
    return _render(library)


def _field(man, gid, prop):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next((f for f in el["editable"] if f["prop"] == prop), None)


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _close(a, b, tol=3):
    return all(abs(x - y) <= tol for x, y in zip(_rgb(a), _rgb(b)))


def test_vertical_band_gets_one_gradient_field_instead_of_facecolor(manifest):
    f = _field(manifest, "axes_0.fill_0", "gradient")
    assert f is not None and f["type"] == "gradient"
    assert _field(manifest, "axes_0.fill_0", "facecolor") is None, (
        "渐变出了 gradient 就不该再出纯色字段"
    )
    top, bottom = f["value"]["stops"]
    assert f["value"]["direction"] == "vertical"
    assert _close(top, "#cce6ff") and _close(bottom, "#003399"), f["value"]


def test_horizontal_band_starts_at_the_left(manifest):
    f = _field(manifest, "axes_1.fill_0", "gradient")
    assert f["value"]["direction"] == "horizontal"
    left, right = f["value"]["stops"]
    assert _close(left, "#e61a1a") and _close(right, "#ffe61a"), f["value"]


def test_inverted_axis_names_the_screen_top(manifest):
    """y 轴反转：数据里 y=0 那条（深绿）画在屏幕上方——起端是它，不是数据里 y 最大的白色那条。"""
    top, bottom = _field(manifest, "axes_2.fill_0", "gradient")["value"]["stops"]
    assert _close(top, "#008000") and _close(bottom, "#ffffff"), (top, bottom)


@pytest.mark.parametrize(
    "gid",
    ["axes_0.fill_1", "axes_3.fill_0", "axes_3.fill_1", "axes_3.fill_2"],
    ids=["fill_between", "too-few-stripes", "solid-colour", "two-column-swatches"],
)
def test_ordinary_fills_keep_their_facecolor(manifest, gid):
    assert _field(manifest, gid, "gradient") is None
    assert _field(manifest, gid, "facecolor") is not None


@pytest.fixture(scope="module")
def recoloured(library):
    patch = {"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": ["#ffd9de", "#b00020"]}}
    return _render(library, [patch])


def test_writing_two_stops_recolours_both_ends(recoloured):
    f = _field(recoloured, "axes_0.fill_0", "gradient")
    top, bottom = f["value"]["stops"]
    assert _close(top, "#ffd9de") and _close(bottom, "#b00020"), f["value"]
    # 原样随字段一起发（检查器的恢复钮与「脚本原始」靠它）
    orig = f.get("value_original")
    assert orig is not None and _close(orig["stops"][1], "#003399"), orig


def test_undo_returns_to_the_script_colours(library):
    patch = {"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": ["#000000", "#ffffff"]}}
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        w.override(STEM, [patch])
        back = w.override(STEM, [])["manifest"]
    finally:
        pool.discard(w)
    top, bottom = _field(back, "axes_0.fill_0", "gradient")["value"]["stops"]
    assert _close(top, "#cce6ff") and _close(bottom, "#003399"), (top, bottom)


def test_hot_session_matches_a_fresh_replay(library):
    """连续改两次（热会话在已改过的颜色上再改）与全新 worker 只重放最后那组 patch 结果相同：
    每次都从脚本原始逐条颜色算，不在改过的颜色上叠加。"""
    first = {"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": ["#00ff00", "#000000"]}}
    final = {"gid": "axes_0.fill_0", "prop": "gradient", "value": {"stops": ["#ffd9de", "#b00020"]}}
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        w.override(STEM, [first])
        hot = w.override(STEM, [final])["manifest"]
    finally:
        pool.discard(w)
    fresh = _render(library, [final])
    assert (
        _field(hot, "axes_0.fill_0", "gradient")["value"]
        == _field(fresh, "axes_0.fill_0", "gradient")["value"]
    )


def test_invert_y_between_two_gradient_edits_does_not_flip_the_colours(library):
    """Codex P1：编辑渐变 → 切 y 轴反转 → 再编辑渐变。override 列表里 gradient 排在 invert_y 前面
    （同一个 `_apply_rank`，原位替换），热会话第三步的 setter 看到的是**已经反转**的轴，全新 worker
    重放时却是反转之前——按当时坐标变换算起端 / 止端会得到相反方向。override 值现在带 `screen_start`，
    引擎按数据坐标顺序重算，与重放时的变换无关。"""
    gid = "axes_0.fill_0"

    def grad(stops, screen_start):
        return {
            "gid": gid,
            "prop": "gradient",
            "value": {"stops": stops, "screen_start": screen_start},
        }

    inv = {"gid": "axes_0", "prop": "invert_y", "value": True}
    first = grad(["#00ff00", "#000000"], "high")  # 正常 y 轴：屏幕上端 = 数据大的一头
    # 反转之后用户在界面上看到的上端 = 数据小的一头，检查器写回 screen_start="low"
    final = grad(["#ffd9de", "#b00020"], "low")
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        w.override(STEM, [first])
        w.override(STEM, [first, inv])
        hot = w.override(STEM, [final, inv])["manifest"]
    finally:
        pool.discard(w)
    fresh = _render(library, [final, inv])
    hv = _field(hot, gid, "gradient")["value"]
    assert hv == _field(fresh, gid, "gradient")["value"]
    top, bottom = hv["stops"]
    assert _close(top, "#ffd9de") and _close(bottom, "#b00020"), hv
