"""色条在画布上能改大小和长度（2026-09-13 用户反馈：图 E 下方的色条拖不动）。

两件事：

1. **manifest**：色条伪元素（`axes_i.colorbar`）宣称 `resizable` 并把几何代理到
   它的轴（`geom_gid = axes_i`，与位图代理到宿主子图同一套机制）。从前色条元素
   与色条轴 bbox 逐位相同，命中测试里色条元素恒胜（容器有降权），于是点色条
   选中的是一个既没有手柄也拖不动的伪元素。色条轴落位不归 Tavotto 管时
   （插图里的色条）不宣称。
2. **引擎**：`fig.colorbar(im, ax=ax)` 造的色条轴带着 `box_aspect=20`，
   `set_position` 给多宽都会被 `apply_aspect` 按回高度的 1/20——用户把色条拖粗，
   下一帧弹回去。落 position override 那一刻把长宽比解开（与方向翻转同一处置），
   撤销时放回；热会话与全新 worker 全量重放逐位相同。

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

SCRIPT_NAME = "fig_cbar_resize.py"
ENTRY = "main"
#: `fig.colorbar(im, ax=ax)`：gridspec 分出来的色条轴，带 box_aspect=20
STEM_GS = "CbarGrid"
#: `fig.colorbar(im, cax=fig.add_axes([...]))`：用户自己摆的轴，没有 box_aspect
STEM_CAX = "CbarCax"
#: 挂在插图上的色条：色条轴是子 axes、落位归 locator，不宣称 resizable
STEM_INSET = "CbarInset"
#: 带延伸三角的 gridspec 色条：locator 每帧按 extend 收缩，落位仍要归用户
STEM_EXT = "CbarExtend"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt


def _grid(stem, **kw):
    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    im = ax.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    fig.colorbar(im, ax=ax, **kw)
    fig.savefig(stem + ".pdf")


def _cax(stem):
    fig = plt.figure(figsize=(6.0, 4.0))
    ax = fig.add_subplot(111)
    im = ax.imshow(np.arange(64).reshape(8, 8), cmap="magma")
    cax = fig.add_axes([0.128, 0.05, 0.435, 0.012])
    fig.colorbar(im, cax=cax, orientation="horizontal")
    fig.savefig(stem + ".pdf")


def _inset(stem):
    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    im = ax.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    cax = ax.inset_axes([1.05, 0.1, 0.05, 0.8])
    fig.colorbar(im, cax=cax)
    fig.savefig(stem + ".pdf")


def main():
    _grid("CbarGrid")
    _grid("CbarExtend", extend="both")
    _cax("CbarCax")
    _inset("CbarInset")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cbar-resize-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _render(figs, stem, patches=()):
    w = _worker(figs)
    try:
        resp = w.override(stem, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _colorbar(man):
    return next(e for e in man["elements"] if e["role"] == "colorbar")


def _position(man, gid):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == "position")


@pytest.mark.parametrize("stem", [STEM_GS, STEM_CAX, STEM_EXT])
def test_colorbar_element_proxies_its_geometry_to_the_colorbar_axes(library, stem):
    """色条元素：`resizable` + `geom_gid` 指向它的轴，那条轴自己也 resizable、
    有 position 字段；两者 bbox 逐位相同（前端拿轴的 position 画手柄、写 override）。"""
    man = _render(library, stem)
    cb = _colorbar(man)
    assert cb["resizable"] is True
    cbax = _el(man, cb["geom_gid"])
    assert cbax["role"] == "axes" and cbax.get("is_colorbar") is True
    assert cbax["colorbar_gid"] == cb["gid"]
    assert cbax["resizable"] is True
    assert cb["bbox"] == pytest.approx(cbax["bbox"], abs=1e-6)
    assert len(_position(man, cbax["gid"])) == 4


def test_inset_colorbar_does_not_claim_a_geometry_it_cannot_keep(library):
    """插图里的色条：色条轴是子 axes、落位由父级 locator 每帧重算，`position`
    是个死开关——色条元素也不许宣称 resizable（两处判据同源）。"""
    man = _render(library, STEM_INSET)
    cb = _colorbar(man)
    assert "resizable" not in cb and "geom_gid" not in cb
    cbax = next(e for e in man["elements"] if e.get("colorbar_gid") == cb["gid"])
    assert cbax["resizable"] is False


def test_user_placed_colorbar_axes_takes_any_rectangle(library):
    """`cax=` 的色条：position override 给什么就画成什么——长度与厚度都改得动。"""
    man = _render(library, STEM_CAX)
    cbax_gid = _colorbar(man)["geom_gid"]
    want = [0.128, 0.05, 0.6, 0.03]
    after = _render(library, STEM_CAX, [{"gid": cbax_gid, "prop": "position", "value": want}])
    assert _position(after, cbax_gid) == pytest.approx(want, abs=1e-4)
    # bbox（top-origin）跟着走：宽 0.6、高 0.03
    _, _, bw, bh = _colorbar(after)["bbox"]
    assert (bw, bh) == pytest.approx((0.6, 0.03), abs=2e-3)


@pytest.mark.parametrize("stem", [STEM_GS, STEM_EXT])
def test_gridspec_colorbar_honours_the_requested_thickness(library, stem):
    """`fig.colorbar(im, ax=ax)` 的色条轴带 box_aspect=20：不解开的话请求宽 0.10
    画出来 0.02（实测 3.10.8）。落位归用户之后宽度就是请求的宽度。

    extend="both" 那张的**长度**由 locator 按延伸三角收缩（那是 matplotlib 给
    三角让地方，不是弹回去），所以只钉厚度与左沿。"""
    man = _render(library, stem)
    cbax_gid = _colorbar(man)["geom_gid"]
    x0, y0, w0, h0 = _position(man, cbax_gid)
    assert w0 < 0.04, f"对照：没改之前是细高的（宽 {w0}）"
    want = [x0, y0, 0.10, h0]
    after = _render(library, stem, [{"gid": cbax_gid, "prop": "position", "value": want}])
    got = _position(after, cbax_gid)
    assert got[2] == pytest.approx(0.10, abs=1e-3), f"厚度被长宽比按回去了：{got}"
    assert got[0] == pytest.approx(x0, abs=1e-3)
    if stem == STEM_GS:
        assert got == pytest.approx(want, abs=1e-3)


def test_undo_puts_the_aspect_back(library):
    """撤销 position：色条回到细高的样子，manifest 与从没改过时逐位相同——
    解开的长宽比不还回去的话，撤销之后色条停在解开的样子，比原来粗四倍。"""
    base = _render(library, STEM_GS)
    cbax_gid = _colorbar(base)["geom_gid"]
    x0, y0, _, h0 = _position(base, cbax_gid)
    patch = {"gid": cbax_gid, "prop": "position", "value": [x0, y0, 0.10, h0]}
    hot = _worker(library)
    try:
        wide = hot.override(STEM_GS, [patch])["manifest"]
        assert _position(wide, cbax_gid)[2] == pytest.approx(0.10, abs=1e-3)
        undone = hot.override(STEM_GS, [])["manifest"]
    finally:
        pool.discard(hot)
    assert _position(undone, cbax_gid) == pytest.approx(_position(base, cbax_gid), abs=1e-6)
    assert _colorbar(undone)["bbox"] == pytest.approx(_colorbar(base)["bbox"], abs=1e-6)


def test_undo_hands_the_thickness_back_to_the_aspect(library):
    """撤销之后长宽比要**真的**回来，不只是此刻的矩形碰巧一样。

    上一条用例量的那一刻分不出「放回了 box_aspect」与「没放回」：position 的
    脚本原样记的是画出来的细矩形，回灌它之后两种实现画出来的都是那个细矩形。
    分得出的时刻在**之后**：改图幅把长宽比换掉，box_aspect 在的话厚度按新高度
    重新算成 1/20，不在的话就按分数跟着图幅拉伸——与从没改过 position 的全新
    重放比，后者会分岔。
    """
    base = _render(library, STEM_GS)
    cbax_gid = _colorbar(base)["geom_gid"]
    x0, y0, _, h0 = _position(base, cbax_gid)
    position = {"gid": cbax_gid, "prop": "position", "value": [x0, y0, 0.10, h0]}
    # 图幅从 6×4 英寸改成 4×6：高度翻倍相对宽度
    resize = {"gid": "figure", "prop": "size_mm", "value": [101.6, 152.4]}
    hot = _worker(library)
    try:
        hot.override(STEM_GS, [position])
        hot.override(STEM_GS, [])
        man_hot = hot.override(STEM_GS, [resize])["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _render(library, STEM_GS, [resize])
    assert _position(man_hot, cbax_gid) == pytest.approx(_position(man_fresh, cbax_gid), abs=1e-6)
    assert _colorbar(man_hot)["bbox"] == pytest.approx(_colorbar(man_fresh)["bbox"], abs=1e-6)


@pytest.mark.parametrize(
    "stem,extra",
    [
        (STEM_GS, []),
        (STEM_EXT, []),
        # 先落位、再开延伸三角：热会话里 extend 的 setter 在 position 之后跑，
        # 全量重放里它排在 position 之前（规范档位 3 < 4）——两条路都得落到
        # 「长宽比解开」这一个状态
        (STEM_GS, [{"gid": "__CB__", "prop": "extend", "value": "both"}]),
    ],
    ids=["grid", "extend", "position-then-extend"],
)
def test_hot_session_matches_a_fresh_replay(library, stem, extra):
    """热会话一步步改出来的落位与全新 worker 一次性重放**逐位相同**（写回事务
    的「热态所见 == 重开后重放」）。"""
    base = _render(library, stem)
    cb = _colorbar(base)
    cbax_gid = cb["geom_gid"]
    x0, y0, _, h0 = _position(base, cbax_gid)
    patches = [{"gid": cbax_gid, "prop": "position", "value": [x0, y0, 0.10, h0]}]
    patches += [{**p, "gid": cb["gid"]} for p in extra]
    hot = _worker(library)
    try:
        for i in range(len(patches)):
            hot.override(stem, patches[: i + 1])
        man_hot = hot.override(stem, patches)["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _render(library, stem, patches)
    for gid in (cbax_gid, cb["gid"]):
        assert _el(man_hot, gid)["bbox"] == pytest.approx(_el(man_fresh, gid)["bbox"], abs=1e-6)
    assert _position(man_hot, cbax_gid) == pytest.approx(_position(man_fresh, cbax_gid), abs=1e-6)
