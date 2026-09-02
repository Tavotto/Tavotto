"""图例项 ↔ 图中源对象的绑定（ADR 0034）。

matplotlib 的图例示意线是创建那一刻从源对象**复制**出来的，此后源变它不变
（实测 3.10.8：`line.set_color` 之后 `leg.legend_handles[0].get_color()` 仍是
旧色）。Tavotto 把每一项建模成「跟随源 / 自定义」两档：跟随的项在每次
apply 之后从源重新派生（派生显示，不进文档）；自定义的项自己是一份状态。

本文件钉住的合同：

1. 导入时建立源引用（label + 指纹），找不到源**不伪造**；
2. 改源曲线的颜色 / 线型 / 线宽 / marker → 图例同步；
3. 在图例项上直接改样式 → 脱开（custom），源再变它不动；
4. 撤掉那条 override → 回到跟随；脚本自己改过示意线的项默认 custom，
   可以显式切回跟随；
5. 隐藏一项 = 整项从图例盒里拿掉，元素表里仍留着它（否则没法恢复）；
6. 热态 == 全新 worker 一次性重放（写回自检的前提）；
7. 布局旋钮（列数 / 示意线长 / 示意线-文字间距 / 行距 / 列距 / 边框线宽 /
   圆角）真的改得动，且 `columnspacing` 只在多列时有效。

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

SCRIPT_NAME = "fig_legend_binding.py"
ENTRY = "main"
STEM = "Bind"
LEG = "axes_0.legend"
T = [f"axes_0.legend.texts_{j}" for j in range(6)]

#: 三种线型 / 两种 marker + 散点 + 柱 + 误差棒；第 3 项（lin）的示意线被脚本
#: 自己加粗了——那是「源找到了、但示意线与源不一致」的样本；第 6 项（proxy）
#: 是一个不在图上的代理 artist——那是「没有源」的样本。
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 6.0, 30)
    ax.plot(x, np.sin(x), "r-", label="sin")
    ax.plot(x, np.cos(x), "b--o", label="cos", markersize=4)
    ax.plot(x, x / 6.0, color="g", linestyle=":", marker="s", label="lin")
    ax.scatter(x[::5], np.cos(x[::5]) * 0.5, c="m", label="pts")
    ax.bar([0.5, 1.5], [0.2, 0.3], width=0.3, color="c", label="bars")
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([], [], color="k", linestyle="-.", label="proxy"))
    labels.append("proxy")
    leg = ax.legend(handles, labels, ncol=1, title="T", title_fontsize=12, markerscale=1.5)
    leg.legend_handles[2].set_linewidth(4.0)
    fig.savefig("Bind.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-binding")
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


def _fresh(figs, patches=()):
    w = _worker(figs)
    try:
        return _man(w, patches)
    finally:
        pool.discard(w)


def _el(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return hits[0]


def _fields(man, gid):
    return {f["prop"]: f for f in _el(man, gid)["editable"]}


def _val(man, gid, prop):
    return _fields(man, gid)[prop]["value"]


def _png(worker, patches, tag):
    path = worker.preview_png(STEM, list(patches), 380, tag)
    return path.read_bytes()


# ---------------------------------------------------------------------------
# 1. 导入：源引用与默认绑定
# ---------------------------------------------------------------------------
def test_import_binds_sources_by_label_and_fingerprint(hot):
    man = _man(hot)
    info = {gid: _el(man, gid)["legend_entry"] for gid in T}
    assert info[T[0]] == {
        "index": 0,
        "source_gid": "axes_0.lines_0",
        "binding_default": "follow_source",
    }
    assert info[T[1]]["source_gid"] == "axes_0.lines_1"
    assert info[T[3]]["source_gid"] == "axes_0.scatter_0"
    assert info[T[4]]["source_gid"] == "axes_0.barseries_0"
    # 脚本在 legend() 之后把第 3 项的示意线加粗了：源找得到，默认却是 custom
    assert info[T[2]] == {"index": 2, "source_gid": "axes_0.lines_2", "binding_default": "custom"}
    assert _val(man, T[2], "binding") == "custom"
    assert _val(man, T[2], "handle_linewidth") == 4.0, "脚本改过的示意线原样保留"


def test_a_proxy_entry_has_no_binding_and_no_fake_source(hot):
    """`Line2D([], [])` 代理 artist 不在图上：没有源、没有 binding 字段——
    界面据此显示「未关联图中对象」，而不是一个假开关。"""
    man = _man(hot)
    assert _el(man, T[5])["legend_entry"] == {"index": 5}
    fields = _fields(man, T[5])
    assert "binding" not in fields
    # 示意线样式照样可编辑（它本来就是一份独立状态）
    assert fields["handle_color"]["value"] == "#000000"
    assert fields["handle_linestyle"]["value"] == "-."


def test_handle_fields_follow_the_handle_type(hot):
    """曲线的示意线五条全有；散点 / 柱的示意线只有颜色。"""
    man = _man(hot)
    line_props = {p for p in _fields(man, T[0]) if p.startswith("handle_")}
    assert line_props == {
        "handle_color",
        "handle_linestyle",
        "handle_linewidth",
        "handle_marker",
        "handle_markersize",
    }
    assert {p for p in _fields(man, T[3]) if p.startswith("handle_")} == {"handle_color"}
    assert {p for p in _fields(man, T[4]) if p.startswith("handle_")} == {"handle_color"}
    # markerscale=1.5：manifest 报的是图例上画出来的尺寸（4 × 1.5）
    assert _val(man, T[1], "handle_markersize") == 6.0


# ---------------------------------------------------------------------------
# 2. 源变 → 图例同步（派生显示，不进文档）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prop,value,handle_prop",
    [
        ("color", "#00ff00", "handle_color"),
        ("linestyle", "--", "handle_linestyle"),
        ("linewidth", 3.5, "handle_linewidth"),
        ("marker", "^", "handle_marker"),
    ],
)
def test_source_style_change_reaches_the_legend(hot, prop, value, handle_prop):
    before = _val(_man(hot), T[0], handle_prop)
    man = _man(hot, [{"gid": "axes_0.lines_0", "prop": prop, "value": value}])
    after = _val(man, T[0], handle_prop)
    assert after != before
    assert after == value
    # 图例项自己一条 override 都没有：这是派生显示
    assert _val(man, T[0], "binding") == "follow_source"
    _man(hot)


def test_source_markersize_is_scaled_by_markerscale(hot):
    man = _man(hot, [{"gid": "axes_0.lines_1", "prop": "markersize", "value": 10.0}])
    assert _val(man, T[1], "handle_markersize") == 15.0, "图例上的 marker 按 markerscale 派生"
    _man(hot)


def test_sync_changes_pixels_not_geometry(hot):
    """跟随源重新派生只换示意线本身：包围盒一个像素不动，画面要变。"""
    base = _man(hot)
    patch = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"}]
    after = _man(hot, patch)
    for gid in (LEG, *T):
        assert _el(after, gid)["bbox"] == pytest.approx(_el(base, gid)["bbox"], abs=1e-9)
    assert _png(hot, patch, "sync") != _png(hot, [], "sync-base")
    _man(hot)


# ---------------------------------------------------------------------------
# 3 / 4. 脱开与恢复
# ---------------------------------------------------------------------------
def test_editing_the_handle_detaches_and_the_source_no_longer_reaches_it(hot):
    detach = [{"gid": T[0], "prop": "handle_color", "value": "#123456"}]
    man = _man(hot, detach)
    assert _val(man, T[0], "binding") == "custom"
    assert _val(man, T[0], "handle_color") == "#123456"
    # 源再变，脱开的项不动；源自己照常变
    both = detach + [{"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"}]
    man = _man(hot, both)
    assert _val(man, T[0], "handle_color") == "#123456"
    assert _val(man, "axes_0.lines_0", "color") == "#00ff00"
    _man(hot)


def test_dropping_the_override_restores_following(hot):
    both = [
        {"gid": T[0], "prop": "handle_color", "value": "#123456"},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"},
    ]
    _man(hot, both)
    man = _man(hot, both[1:])  # 撤掉图例项那条
    assert _val(man, T[0], "binding") == "follow_source"
    assert _val(man, T[0], "handle_color") == "#00ff00", "回到跟随 = 立刻派生成源此刻的样子"
    _man(hot)


def test_detaching_keeps_the_synced_look_across_a_rebuild(hot):
    """先让源变（项跟着变绿），再在项上改线宽（脱开），再改列数重建：
    脱开的项必须还是绿的——custom_base 是脱开那一刻的样子，不是脚本原样。"""
    patches = [
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"},
        {"gid": T[0], "prop": "handle_linewidth", "value": 3.0},
    ]
    _man(hot, patches)
    man = _man(hot, patches + [{"gid": LEG, "prop": "ncol", "value": 2}])
    assert _val(man, T[0], "handle_color") == "#00ff00"
    assert _val(man, T[0], "handle_linewidth") == 3.0
    _man(hot)


def test_a_script_customised_entry_can_be_told_to_follow(hot):
    """第 3 项脚本自己加粗过（默认 custom）：显式切到 follow_source 后从源
    派生（线宽回到源的 1.5），撤掉又回到脚本原样（4.0）。"""
    follow = [{"gid": T[2], "prop": "binding", "value": "follow_source"}]
    man = _man(hot, follow)
    assert _val(man, T[2], "binding") == "follow_source"
    assert _val(man, T[2], "handle_linewidth") == 1.5
    man = _man(hot)
    assert _val(man, T[2], "binding") == "custom"
    assert _val(man, T[2], "handle_linewidth") == 4.0


def test_binding_custom_freezes_the_entry_at_its_current_look(hot):
    """显式脱开（binding=custom，不改任何样式）：示意线冻在此刻的样子，
    源再变它不动。"""
    green = {"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"}
    freeze = {"gid": T[0], "prop": "binding", "value": "custom"}
    man = _man(hot, [green, freeze])
    assert _val(man, T[0], "handle_color") == "#00ff00"
    man = _man(hot, [{**green, "value": "#0000ff"}, freeze])
    assert _val(man, T[0], "handle_color") == "#00ff00", "冻结之后源再变，它不动"
    _man(hot)


# ---------------------------------------------------------------------------
# 5. 隐藏与顺序：稳定序号
# ---------------------------------------------------------------------------
def test_hiding_an_entry_removes_the_whole_row_but_keeps_the_element(hot):
    base = _man(hot)
    man = _man(hot, [{"gid": T[1], "prop": "visible", "value": False}])
    assert _val(man, T[1], "visible") is False
    # 元素还在（否则「恢复显示」没有入口），框是图例的框
    assert _el(man, T[1])["bbox"] == pytest.approx(_el(man, LEG)["bbox"])
    # 整行没了：图例盒变矮
    assert _el(man, LEG)["bbox"][3] < _el(base, LEG)["bbox"][3]
    # 其余项的序号一个不变，顺序字段照旧六项
    assert _val(man, LEG, "entry_order") == [0, 1, 2, 3, 4, 5]
    assert _fields(man, LEG)["entry_order"]["options"] == [
        "sin",
        "cos",
        "lin",
        "pts",
        "bars",
        "proxy",
    ]
    back = _man(hot)
    assert back == base


def test_hidden_entry_keeps_its_text_override_for_when_it_returns(hot):
    patches = [
        {"gid": T[1], "prop": "text", "value": "COS"},
        {"gid": T[1], "prop": "visible", "value": False},
    ]
    _man(hot, patches)
    man = _man(hot, patches[:1])
    assert _val(man, T[1], "text") == "COS"
    assert _val(man, T[1], "visible") is True
    _man(hot)


def test_reorder_then_style_override_stays_with_its_entry(hot):
    patches = [
        {"gid": LEG, "prop": "entry_order", "value": [5, 4, 3, 2, 1, 0]},
        {"gid": T[0], "prop": "handle_color", "value": "#123456"},
    ]
    man = _man(hot, patches)
    assert _val(man, T[0], "handle_color") == "#123456", "override 跟着 sin 那一项走"
    assert _val(man, T[5], "handle_color") == "#000000", "显示在第一行的 proxy 没被误改"
    # 显示顺序：texts_5 在最上
    tops = {gid: _el(man, gid)["bbox"][1] for gid in T}
    assert sorted(tops, key=tops.get) == list(reversed(T))
    _man(hot)


# ---------------------------------------------------------------------------
# 6. 热态 == 重放；撤销到底逐位回原样
# ---------------------------------------------------------------------------
def test_hot_equals_fresh_replay(hot, library):
    steps = [
        [{"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"}],
        [
            {"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"},
            {"gid": T[1], "prop": "handle_marker", "value": "D"},
            {"gid": T[2], "prop": "binding", "value": "follow_source"},
            {"gid": T[3], "prop": "visible", "value": False},
            {"gid": LEG, "prop": "entry_order", "value": [4, 0, 1, 2, 3, 5]},
            {"gid": LEG, "prop": "ncol", "value": 2},
            {"gid": LEG, "prop": "handletextpad", "value": 1.2},
        ],
    ]
    for step in steps:
        man_hot = _man(hot, step)
    man_fresh = _fresh(library, steps[-1])
    assert man_hot == man_fresh
    _man(hot)


def test_undo_to_zero_is_pixel_identical(hot):
    base = _man(hot)
    base_png = _png(hot, [], "undo-base")
    _man(
        hot,
        [
            {"gid": "axes_0.lines_0", "prop": "color", "value": "#00ff00"},
            {"gid": T[0], "prop": "handle_linewidth", "value": 3.0},
            {"gid": T[2], "prop": "binding", "value": "follow_source"},
            {"gid": T[3], "prop": "visible", "value": False},
            {"gid": LEG, "prop": "ncol", "value": 3},
            {"gid": LEG, "prop": "frame_rounded", "value": True},
        ],
    )
    assert _man(hot) == base
    assert _png(hot, [], "undo-after") == base_png


# ---------------------------------------------------------------------------
# 7. 布局旋钮真的改得动
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prop,value,axis",
    [
        ("ncol", 3, 2),  # 三列 → 更宽
        ("handlelength", 4.0, 2),  # 示意线更长 → 更宽
        ("handletextpad", 2.0, 2),  # 示意线-文字间距 → 更宽
        ("labelspacing", 1.5, 3),  # 行距 → 更高
    ],
)
def test_layout_knobs_move_the_box(hot, prop, value, axis):
    base = _el(_man(hot), LEG)["bbox"]
    after = _el(_man(hot, [{"gid": LEG, "prop": prop, "value": value}]), LEG)["bbox"]
    assert after[axis] > base[axis]
    _man(hot)


def test_columnspacing_only_matters_with_more_than_one_column(hot):
    one = _el(_man(hot, [{"gid": LEG, "prop": "columnspacing", "value": 4.0}]), LEG)["bbox"]
    base = _el(_man(hot), LEG)["bbox"]
    assert one == pytest.approx(base), "单列图例上列距没有地方可摆"
    two = [{"gid": LEG, "prop": "ncol", "value": 2}]
    narrow = _el(_man(hot, two), LEG)["bbox"]
    wide = _el(_man(hot, two + [{"gid": LEG, "prop": "columnspacing", "value": 4.0}]), LEG)["bbox"]
    assert wide[2] > narrow[2]
    _man(hot)


def test_frame_linewidth_and_rounding_are_editable(hot):
    base = _fields(_man(hot), LEG)
    assert base["frame_rounded"]["value"] is True, "matplotlib 默认 fancybox"
    man = _man(
        hot,
        [
            {"gid": LEG, "prop": "frame_linewidth", "value": 2.5},
            {"gid": LEG, "prop": "frame_rounded", "value": False},
        ],
    )
    assert _val(man, LEG, "frame_linewidth") == 2.5
    assert _val(man, LEG, "frame_rounded") is False
    assert _png(hot, [{"gid": LEG, "prop": "frame_linewidth", "value": 2.5}], "lw") != _png(
        hot, [], "lw-base"
    )
    _man(hot)


def test_handle_override_survives_a_later_rebuild_in_a_hot_session(hot):
    """热会话里先改示意线颜色、**下一步**再改列数：重建的素材是 custom_base
    （脱开那一刻的样子），颜色 override 必须重放到新示意线上——同一批 patch
    里两条一起来时 setter 排在重建之后、天然正确，只有分两步才量得到重放。"""
    first = [{"gid": T[0], "prop": "handle_color", "value": "#123456"}]
    _man(hot, first)
    man = _man(hot, first + [{"gid": LEG, "prop": "ncol", "value": 2}])
    assert _val(man, T[0], "handle_color") == "#123456"
    _man(hot)


def test_rebuild_does_not_compound_markerscale_on_custom_entries(hot):
    """带 override 的自定义项重建：素材是快照（markerscale 已乘过），派生会再乘
    一次——`rebuild_legend` 必须把 markersize 放回。这一项有 override，所以
    `sync_legends` 不会替它兜底（有 override 的项它不碰），漏了就是 9 → 13.5。"""
    patches = [{"gid": T[2], "prop": "handle_color", "value": "#123456"}]
    assert _val(_man(hot, patches), T[2], "handle_markersize") == 9.0
    man = _man(hot, patches + [{"gid": LEG, "prop": "ncol", "value": 2}])
    assert _val(man, T[2], "handle_markersize") == 9.0
    assert _val(man, T[2], "handle_color") == "#123456"
    _man(hot)


TWINS_SCRIPT = "fig_legend_twins.py"
TWINS_LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0.0, 6.0, 30)
    ax.plot(x, np.sin(x), "r-", label="dup")
    ax.plot(x, np.cos(x), "r-", label="dup")
    ax.legend()
    fig.savefig("Twins.pdf")
"""


@pytest.fixture(scope="module")
def twins(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-twins")
    (figs / TWINS_SCRIPT).write_text(TWINS_LIBRARY, encoding="utf-8")
    w = pool.one_shot(TWINS_SCRIPT, str(figs), ENTRY)
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def test_identical_twins_bind_by_position_not_by_first_match(twins):
    """两条同色同型同名的曲线：label 与指纹都并列，只能按
    `get_legend_handles_labels()` 的位置认——第二项绑第二条，不是「第一个匹配到
    的」。绑错的表现是改第二条线的颜色、第一项的示意线变了。"""
    resp = twins.override("Twins", [])
    assert not resp["warnings"], resp["warnings"]
    man = resp["manifest"]
    assert _el(man, "axes_0.legend.texts_0")["legend_entry"]["source_gid"] == "axes_0.lines_0"
    assert _el(man, "axes_0.legend.texts_1")["legend_entry"]["source_gid"] == "axes_0.lines_1"
    resp = twins.override("Twins", [{"gid": "axes_0.lines_1", "prop": "color", "value": "#00ff00"}])
    assert not resp["warnings"], resp["warnings"]
    assert _val(resp["manifest"], "axes_0.legend.texts_1", "handle_color") == "#00ff00"
    assert _val(resp["manifest"], "axes_0.legend.texts_0", "handle_color") == "#ff0000"
    twins.override("Twins", [])


def test_rebuild_keeps_the_title_font_size(hot):
    """重建之前标题字号 12（脚本的 title_fontsize）；重建之后必须还是 12。"""
    man = _man(hot, [{"gid": LEG, "prop": "ncol", "value": 2}])
    assert _val(man, "axes_0.legend.title", "fontsize") == 12.0
    _man(hot)
