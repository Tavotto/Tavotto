"""坐标轴边框线几何与主 / 次刻度分档（Prompt 16）。

两件事在这里被钉住：

1. **manifest 给的边框线是画出来的那条线**，不是 axes 的框：偏出去的边框
   （`set_position(("outward", n))`）命中区要跟着线走；极坐标 / 3D 不给，
   twinx 的第二个 axes 不给它关掉的那条轴；对数 / 反转不改几何。
2. **`length` / `width` 只动主刻度**，次刻度另有 `minor_length` / `minor_width`
   ——两档写的是轴上不同的 kw，应用顺序不影响结果；撤销逐字回原样。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import math

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ENTRY = "main"

LIBRARY = {
    "fig_plain.py": """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1, 2], [0, 1, 0.5])
    ax.minorticks_on()
    fig.savefig("Plain.pdf")
""",
    "fig_shapes.py": """\
import matplotlib.pyplot as plt


def main():
    fig = plt.figure(figsize=(6.0, 5.0))
    ax = fig.add_subplot(2, 2, 1)
    ax.plot([0, 1], [0, 1])
    ax.spines["left"].set_position(("outward", 10))
    ax.spines["top"].set_visible(False)
    ax2 = ax.twinx()
    ax2.plot([0, 1], [1, 0], "r")
    p = fig.add_subplot(2, 2, 2, projection="polar")
    p.plot([0, 1, 2], [1, 2, 3])
    a3 = fig.add_subplot(2, 2, 3, projection="3d")
    a3.plot([0, 1], [0, 1], [0, 1])
    b = fig.add_subplot(2, 2, 4)
    b.semilogx([1, 10, 100], [1, 2, 3])
    b.invert_yaxis()
    b.secondary_xaxis("top")
    fig.savefig("Shapes.pdf")
""",
    "fig_cbar.py": """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    im = ax.imshow(np.arange(16).reshape(4, 4))
    fig.colorbar(im, ax=ax)
    fig.savefig("Cbar.pdf")
""",
}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("tick-sides-geometry")
    for name, src in LIBRARY.items():
        (figs / name).write_text(src, encoding="utf-8")
    return figs


def _worker(figs, script):
    w = pool.one_shot(script, str(figs), ENTRY)
    w.ensure_built()
    return w


def _render(figs, script, stem, patches=()):
    w = _worker(figs, script)
    try:
        resp = w.override(stem, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _field(man, gid, prop):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == prop)


def _props(man, gid):
    return {f["prop"] for f in _el(man, gid)["editable"]}


SIDES = ("bottom", "top", "left", "right")


# ---------------------------------------------------------------------------
# 边框线几何
# ---------------------------------------------------------------------------
def test_plain_axes_report_four_spines_on_the_box_edges(library):
    man = _render(library, "fig_plain.py", "Plain")
    ax = _el(man, "axes_0")
    x, y, w, h = ax["bbox"]
    spines = ax["spines"]
    assert set(spines) == set(SIDES)
    tol = 1e-3
    # 上下两条横在框的上下沿、左右两条竖在左右沿；端点就是四个角
    assert math.isclose(spines["bottom"]["from"][1], y + h, abs_tol=tol)
    assert math.isclose(spines["bottom"]["to"][1], y + h, abs_tol=tol)
    assert math.isclose(spines["top"]["from"][1], y, abs_tol=tol)
    assert math.isclose(spines["left"]["from"][0], x, abs_tol=tol)
    assert math.isclose(spines["right"]["from"][0], x + w, abs_tol=tol)
    xs = sorted([spines["bottom"]["from"][0], spines["bottom"]["to"][0]])
    assert math.isclose(xs[0], x, abs_tol=tol) and math.isclose(xs[1], x + w, abs_tol=tol)
    ys = sorted([spines["left"]["from"][1], spines["left"]["to"][1]])
    assert math.isclose(ys[0], y, abs_tol=tol) and math.isclose(ys[1], y + h, abs_tol=tol)
    # 每条边的 visible / ticks 与同一份 manifest 的四边字段同一口径
    for side in SIDES:
        assert spines[side]["visible"] == _field(man, "axes_0", f"spine_{side}")
        assert spines[side]["ticks"] == _field(man, "axes_0", f"ticks_{side}")


def test_spine_geometry_follows_overrides_not_the_script_snapshot(library):
    """关掉一条边 / 开一侧刻度之后，`spines` 里报的是改完的状态。"""
    man = _render(
        library,
        "fig_plain.py",
        "Plain",
        [
            {"gid": "axes_0", "prop": "spine_top", "value": False},
            {"gid": "axes_0", "prop": "ticks_right", "value": True},
        ],
    )
    spines = _el(man, "axes_0")["spines"]
    assert spines["top"]["visible"] is False
    assert spines["top"]["ticks"] is False  # 刻度线没动：边框显隐与刻度显隐是两件事
    assert spines["right"]["ticks"] is True
    assert spines["right"]["visible"] is True


def test_outward_offset_and_hidden_spines_and_twins(library):
    man = _render(library, "fig_shapes.py", "Shapes")
    ax = _el(man, "axes_0")
    x, _y, _w, _h = ax["bbox"]
    spines = ax["spines"]
    # 偏出去 10pt 的左边框：线在框的左沿之外，不在框上
    assert spines["left"]["from"][0] < x - 1e-3
    assert math.isclose(spines["left"]["from"][0], spines["left"]["to"][0], abs_tol=1e-6)
    # 隐藏的上边框：几何照给，visible=False（用户开回来要点得到）
    assert spines["top"]["visible"] is False
    assert "from" in spines["top"]
    # twinx 出来的第二个 axes：x 轴被它自己关了，上下两条不出；左右各按真值
    twin = _el(man, "axes_1")["spines"]
    assert set(twin) == {"left", "right"}
    assert twin["left"]["ticks"] is False
    assert twin["right"]["ticks"] is True


def test_polar_and_3d_axes_do_not_offer_spine_geometry(library):
    man = _render(library, "fig_shapes.py", "Shapes")
    assert "spines" not in _el(man, "axes_2")  # polar
    assert "spines" not in _el(man, "axes_3")  # 3d


def test_log_and_inverted_axes_keep_the_box_geometry(library):
    man = _render(library, "fig_shapes.py", "Shapes")
    ax = _el(man, "axes_4")
    x, y, w, h = ax["bbox"]
    spines = ax["spines"]
    assert set(spines) == set(SIDES)
    assert math.isclose(spines["bottom"]["from"][1], y + h, abs_tol=1e-3)
    assert math.isclose(spines["left"]["from"][0], x, abs_tol=1e-3)
    assert math.isclose(spines["right"]["from"][0], x + w, abs_tol=1e-3)
    # secondary_xaxis("top")：只有上下两条（左右退化成一点），下边既不显示也没刻度
    sec = _el(man, "axes_5")["spines"]
    assert set(sec) == {"bottom", "top"}
    assert sec["bottom"]["visible"] is False and sec["bottom"]["ticks"] is False
    assert sec["top"]["visible"] is True and sec["top"]["ticks"] is True


def test_colorbar_axes_do_not_offer_spine_geometry(library):
    man = _render(library, "fig_cbar.py", "Cbar")
    hosts = [e for e in man["elements"] if e["role"] == "axes" and not e.get("is_colorbar")]
    cbars = [e for e in man["elements"] if e["role"] == "axes" and e.get("is_colorbar")]
    assert hosts and cbars
    assert all("spines" in e for e in hosts)
    assert all("spines" not in e for e in cbars)


# ---------------------------------------------------------------------------
# 主 / 次刻度分档
# ---------------------------------------------------------------------------
def test_major_length_leaves_minor_ticks_alone(library):
    base = _render(library, "fig_plain.py", "Plain")
    assert _field(base, "axes_0.xticks", "length") == 3.5
    assert _field(base, "axes_0.xticks", "minor_length") == 2.0
    man = _render(
        library,
        "fig_plain.py",
        "Plain",
        [{"gid": "axes_0.xticks", "prop": "length", "value": 7}],
    )
    assert _field(man, "axes_0.xticks", "length") == 7
    assert _field(man, "axes_0.xticks", "minor_length") == 2.0  # 次刻度没被拉长
    assert _field(man, "axes_0.yticks", "length") == 3.5  # 另一条轴没动


def test_minor_length_is_its_own_knob_in_either_order(library):
    for patches in (
        [
            {"gid": "axes_0.xticks", "prop": "length", "value": 7},
            {"gid": "axes_0.xticks", "prop": "minor_length", "value": 1},
        ],
        [
            {"gid": "axes_0.xticks", "prop": "minor_length", "value": 1},
            {"gid": "axes_0.xticks", "prop": "length", "value": 7},
        ],
    ):
        man = _render(library, "fig_plain.py", "Plain", patches)
        assert _field(man, "axes_0.xticks", "length") == 7
        assert _field(man, "axes_0.xticks", "minor_length") == 1


def test_minor_width_is_split_the_same_way(library):
    man = _render(
        library,
        "fig_plain.py",
        "Plain",
        [
            {"gid": "axes_0.yticks", "prop": "width", "value": 2},
            {"gid": "axes_0.yticks", "prop": "minor_width", "value": 0.3},
        ],
    )
    assert _field(man, "axes_0.yticks", "width") == 2
    assert _field(man, "axes_0.yticks", "minor_width") == 0.3


def test_minor_length_set_before_minor_ticks_exist_survives_turning_them_on(library):
    """次刻度还没开时设长度：值落在轴的 kw 上，开了之后新建的次刻度继承。"""
    man = _render(
        library,
        "fig_shapes.py",
        "Shapes",
        [
            {"gid": "axes_4.xticks", "prop": "minor_length", "value": 5},
            {"gid": "axes_4.xticks", "prop": "minor_visible", "value": True},
        ],
    )
    assert _field(man, "axes_4.xticks", "minor_visible") is True
    assert _field(man, "axes_4.xticks", "minor_length") == 5


def test_minor_length_really_changes_pixels_and_undoes(library):
    w = _worker(library, "fig_plain.py")
    try:
        base = w.preview_png("Plain", [], 380, "minor-base").read_bytes()
        longer = w.preview_png(
            "Plain",
            [{"gid": "axes_0.xticks", "prop": "minor_length", "value": 9}],
            380,
            "minor-long",
        ).read_bytes()
        resp = w.override("Plain", [{"gid": "axes_0.xticks", "prop": "minor_length", "value": 9}])
        assert not resp["warnings"], resp["warnings"]
        back = w.override("Plain", [])
        assert not back["warnings"], back["warnings"]
        undone = w.preview_png("Plain", [], 380, "minor-undone").read_bytes()
    finally:
        pool.discard(w)
    assert longer != base
    assert undone == base
    assert _field(back["manifest"], "axes_0.xticks", "minor_length") == 2.0


def test_3d_axes_do_not_offer_minor_length_or_width(library):
    man = _render(library, "fig_shapes.py", "Shapes")
    props = _props(man, "axes_3.xticks")
    assert "length" in props
    assert "minor_length" not in props and "minor_width" not in props
