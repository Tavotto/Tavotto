"""图例字号 override = matplotlib 原生 `legend(fontsize=…)`（2026-09-25 用户拍板）。

matplotlib 图例盒里的边距 / 行距 / 示意线长 / 线字间距 / 列距 / 行高下限都以
`Legend._fontsize` 为单位。改之前 override 只改每条文字的字号，框的其余部分原样，
拖角整体缩放图例时成图倍数与预览对不上（先缩 ×0.7 成图 ×0.883）、松手回弹（#575）。

对拍的另一侧是**独立的**：同一张图由脚本自己 `ax.legend(fontsize=…)` 建出来，
不经过 Tavotto 的任何 setter——override 一侧坏了，这一侧不会跟着坏。

本文件钉住的合同：

1. 标量字号 override 后图例框 == 原生 `legend(fontsize=v)` 的框（缩小、放大各一格）；
2. 以字号为单位的间距仍能单独调，且叠在新字号上（== 原生同时给两个参数）；
3. 标题字号不跟（原生也不跟）；
4. 撤销 = 框与每条字号都回到脚本原样，脚本逐条设过不同字号也一样；
5. 热态 == 全新 worker 一次性重放（写回自检的前提），两种列表序各一次。

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

SCRIPT_NAME = "fig_legend_fontsize.py"
STEM = "Leg"
LEG = "axes_0.legend"
TITLE = "axes_0.legend.title"
T0, T1 = "axes_0.legend.texts_0", "axes_0.legend.texts_1"

#: 同一张图，图例只差构建参数。`main` 是被 override 的那张（默认 10 pt）；
#: 其余入口是对拍用的原生版本。`nonuniform` 是「脚本把某一条单独设了字号」的样本。
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt


def _fig(**legend_kw):
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 6.0, 30)
    ax.plot(x, np.sin(x), "r-o", label="Sample A", markersize=4)
    ax.plot(x, np.cos(x), "b--", label="Sample B")
    leg = ax.legend(**{"loc": "upper left", "title": "Group", "fontsize": 10, **legend_kw})
    return fig, leg


def main():
    fig, _ = _fig()
    fig.savefig("Leg.pdf")


def native7():
    fig, _ = _fig(fontsize=7)
    fig.savefig("Leg.pdf")


def native14():
    fig, _ = _fig(fontsize=14)
    fig.savefig("Leg.pdf")


def native7pad():
    fig, _ = _fig(fontsize=7, borderpad=1.2)
    fig.savefig("Leg.pdf")


def nonuniform():
    fig, leg = _fig()
    leg.get_texts()[0].set_fontsize(13)
    fig.savefig("Leg.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-fontsize")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs, entry="main"):
    w = pool.one_shot(SCRIPT_NAME, str(figs), entry)
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


def _fresh(figs, patches=(), entry="main"):
    w = _worker(figs, entry)
    try:
        return _man(w, patches)
    finally:
        pool.discard(w)


def _el(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return hits[0]


def _val(man, gid, prop):
    return {f["prop"]: f for f in _el(man, gid)["editable"]}[prop]["value"]


def _box(man):
    return [round(v, 6) for v in _el(man, LEG)["bbox"]]


def _size(man):
    """框的宽高（figure 分数）。loc='upper left' 钉左上角，宽高相等 ⇒ 整个框相等。"""
    _, _, w, h = _box(man)
    return (w, h)


@pytest.mark.parametrize(("size", "entry"), [(7, "native7"), (14, "native14")])
def test_scalar_fontsize_matches_native_legend(library, hot, size, entry):
    base = _size(_man(hot))
    got = _man(hot, [{"gid": LEG, "prop": "fontsize", "value": size}])
    want = _fresh(library, entry=entry)
    assert _box(got) == _box(want)
    # 判据活着：框确实变了（10 pt → 7 / 14 pt，不是两侧都停在原样）
    assert _size(got) != base
    assert _val(got, T0, "fontsize") == _val(got, T1, "fontsize") == size
    _man(hot)


def test_spacing_still_adjustable_on_top_of_the_new_size(library, hot):
    got = _man(
        hot,
        [
            {"gid": LEG, "prop": "fontsize", "value": 7},
            {"gid": LEG, "prop": "borderpad", "value": 1.2},
        ],
    )
    assert _box(got) == _box(_fresh(library, entry="native7pad"))
    assert _box(got) != _box(_fresh(library, entry="native7"))
    _man(hot)


def test_title_size_does_not_follow(hot):
    before = _val(_man(hot), TITLE, "fontsize")
    got = _man(hot, [{"gid": LEG, "prop": "fontsize", "value": 7}])
    assert _val(got, TITLE, "fontsize") == before
    _man(hot)


def test_undo_restores_box_and_sizes(library, hot):
    # 基线取全新 worker 的脚本原样，不取热态：前面的用例改过又撤过这个热 worker，
    # 撤销坏了的话热态本身就是坏的，拿它当基线会恒真
    base = _fresh(library)
    _man(hot, [{"gid": LEG, "prop": "fontsize", "value": 7}])
    back = _man(hot)
    assert _box(back) == _box(base)
    assert _val(back, T0, "fontsize") == _val(base, T0, "fontsize") == 10


def test_undo_restores_per_entry_script_sizes(library):
    w = _worker(library, "nonuniform")
    try:
        base = _man(w)
        assert (_val(base, T0, "fontsize"), _val(base, T1, "fontsize")) == (13, 10)
        changed = _man(w, [{"gid": LEG, "prop": "fontsize", "value": 7}])
        assert _box(changed) != _box(base)
        back = _man(w)
        assert (_val(back, T0, "fontsize"), _val(back, T1, "fontsize")) == (13, 10)
        assert _box(back) == _box(base)
    finally:
        pool.discard(w)


@pytest.mark.parametrize(
    "patches",
    [
        [
            {"gid": LEG, "prop": "fontsize", "value": 7},
            {"gid": T1, "prop": "fontsize", "value": 12},
            {"gid": LEG, "prop": "ncol", "value": 2},
        ],
        [
            {"gid": T1, "prop": "fontsize", "value": 12},
            {"gid": LEG, "prop": "ncol", "value": 2},
            {"gid": LEG, "prop": "fontsize", "value": 7},
        ],
    ],
    ids=["legend-first", "legend-last"],
)
def test_hot_equals_fresh_replay(library, hot, patches):
    # 热态一步步走到这组 patch（中间态与重放不同），再与全新重放比
    for k in range(1, len(patches) + 1):
        got = _man(hot, patches[:k])
    fresh = _fresh(library, patches)
    assert _box(got) == _box(fresh)
    for gid in (T0, T1):
        assert _val(got, gid, "fontsize") == _val(fresh, gid, "fontsize")
    # 广播先于窄的（与列表序无关，见 overrides.ALIAS_GROUPS）：单条 12 盖住整组 7；
    # 重建换掉了文字对象，单条的值要被接回新对象（_reindex_legend_children）
    assert (_val(got, T0, "fontsize"), _val(got, T1, "fontsize")) == (7, 12)
    _man(hot)


def test_legend_level_field_reports_the_native_base(library, hot):
    """图例级 `fontsize` 报 `_fontsize`（盒的基准），不报第一条文字的字号。

    前端拖角缩放拿它当基准乘倍数：首条单独设过字号时报首条，就会从错的值乘
    （10 pt 的图例首条 13 pt，×1.5 写成 19.5 而不是 15）。
    """
    # 会话里单独改了首条
    got = _man(hot, [{"gid": T0, "prop": "fontsize", "value": 13}])
    assert _val(got, T0, "fontsize") == 13
    assert _val(got, LEG, "fontsize") == 10
    _man(hot)
    # 脚本自己把首条设成 13
    w = _worker(library, "nonuniform")
    try:
        base = _man(w)
        assert (_val(base, T0, "fontsize"), _val(base, LEG, "fontsize")) == (13, 10)
        assert _val(_man(w, [{"gid": LEG, "prop": "fontsize", "value": 7}]), LEG, "fontsize") == 7
    finally:
        pool.discard(w)


def test_hidden_entry_takes_the_legend_size_when_shown_again(library, hot):
    """先藏一项、再改整组字号、再把它放出来：它要是整组的字号，热态 == 全新重放。

    `leg.get_texts()` 只含显示着的项；setter 只改它们的话，藏着的那条 Text 留着脚本
    字号，放出来时重建从它抄样子，而整组那条 override 值没变、不重放——热态混着两种
    字号，全新重放却全是新字号（Codex #579 第 3 轮 P1）。
    """
    hide = {"gid": T1, "prop": "visible", "value": False}
    size = {"gid": LEG, "prop": "fontsize", "value": 7}
    _man(hot, [hide])
    _man(hot, [hide, size])
    got = _man(hot, [size])
    fresh = _fresh(library, [size])
    assert (_val(got, T0, "fontsize"), _val(got, T1, "fontsize")) == (7, 7)
    assert _box(got) == _box(fresh)
    _man(hot)


def test_hidden_entry_keeps_its_own_size_through_a_legend_change(library, hot):
    """隐藏项上有单条字号：整组字号改动写到它身上之后，单条那条要重放回来（组员含隐藏项）。"""
    hide = {"gid": T1, "prop": "visible", "value": False}
    own = {"gid": T1, "prop": "fontsize", "value": 12}
    size = {"gid": LEG, "prop": "fontsize", "value": 7}
    _man(hot, [own, hide])
    # 还藏着的那一刻：隐藏项的 Text 仍在元素表里、报字号——它也得是 12，与全新重放一致
    # （放出来时重建会把单条字号重放回来，只看放出来之后的话，组员漏了隐藏项也看不出）
    hidden = _man(hot, [own, hide, size])
    assert (
        _val(hidden, T1, "fontsize")
        == _val(_fresh(library, [own, hide, size]), T1, "fontsize")
        == 12
    )
    got = _man(hot, [own, size])
    fresh = _fresh(library, [own, size])
    assert (_val(got, T0, "fontsize"), _val(got, T1, "fontsize")) == (7, 12)
    assert _box(got) == _box(fresh)
    _man(hot)
