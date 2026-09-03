"""寄生轴（`axes_grid1` 的 `host_subplot` + `twinx()`）—— issue #217。

`mpl_toolkits.axes_grid1`（与 `axisartist`）的 `host_subplot(...).twinx()` 造出
来的轴挂在 `host.parasites` 上，**既不在 `fig.axes` 也不在 `ax.child_axes`**
——宿主在自己的 `draw()` 里把 `ax.get_children()` 临时接到孩子列表上代画，所以
它画得出来、却不在任何一条我们遍历过的表里。表现是**整条第二组数据连同它的
右轴一起不进 manifest**：在 Tavotto 里既列不出也改不了，而且一句警告都没有。
静默错比报错难得多：用户以为这张图只有一组数据。

三件事各自钉一条：

1. **在不在**——寄生轴、它上面的曲线、它的轴标签都得进元素表；
2. **gid 稳不稳**——`axes_i` 会进用户文档，存量文档里的编号一个字节不能变，
   所以寄生轴只能排在**全部** `fig.axes` 与全部子 axes 之后；且热态与全新
   worker 全量重放必须逐位相同（写回事务的「所见 == 所写 == 重放出来的」）；
3. **宣称的能力兑不兑现**——寄生轴的 `position` 与 `visible` 是两个死开关
   （宿主每帧把落位按自己的 rect 顶回去、代画时从不看它的 visible），所以
   **不出这两个字段**，并且各自说得出为什么。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import hashlib

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_parasite.py"
ENTRY = "main"
STEM = "ParFig"

#: 同一张图上**两族**非 `fig.axes` 的轴都在：`inset_axes()` 走 `child_axes`、
#: `twinx()` 走 `parasites`。少了插图这一半，「谁排在谁前面」就没有东西可钉，
#: 而那正是存量文档里的 `axes_i` 会不会错位的唯一判据。
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import host_subplot


def main():
    x = np.linspace(0.0, 6.0, 40)

    fig = plt.figure(figsize=(4.2, 3.2))
    host = host_subplot(111, figure=fig)
    par = host.twinx()
    ins = host.inset_axes([0.60, 0.62, 0.34, 0.30])
    host.plot(x, np.sin(x), color="#1f77b4", label="host")
    par.plot(x, np.cos(x) * 40.0 + 60.0, color="#B34700", linestyle="--", label="parasite")
    ins.plot(x, x, color="#2A6F3C")
    host.set_ylabel("host y")
    par.set_ylabel("parasite y")
    fig.savefig("ParFig.pdf")
"""

#: 遍历序：`fig.axes`（宿主）→ 子 axes（插图）→ 寄生轴。**这三个数字是契约**，
#: 不是实现细节：它们会以 override 的 gid 形式进用户文档。
HOST_GID, INSET_GID, PARASITE_GID = "axes_0", "axes_1", "axes_2"


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("parasite-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _png(worker, patches, tag):
    """这一组 patch 画出来的样子（sha1）。`preview_png` 是状态中立的。"""
    return hashlib.sha1(worker.preview_png(STEM, list(patches), 380, tag).read_bytes()).hexdigest()


def _manifest(figs, patches=()):
    w = _worker(figs)
    try:
        resp = w.override(STEM, list(patches))
        assert not (resp.get("warnings") or []), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _by_gid(man):
    return {e["gid"]: e for e in man["elements"]}


# ---------------------------------------------------------------------------
# ① 在不在
# ---------------------------------------------------------------------------
def test_the_parasite_axes_and_everything_on_it_reach_the_manifest(library):
    """寄生轴、它的曲线、它的轴标签、它的刻度组——一个都不许少。

    修之前这四样**全部**缺席，而 `warnings` 是空的、`unsupported` 是 `null`：
    用户拿到的是一张「只有一组数据」的图，没有任何东西提示他第二组去哪了。
    """
    els = _by_gid(_manifest(library))
    missing = [
        g
        for g in (PARASITE_GID, f"{PARASITE_GID}.lines_0", f"{PARASITE_GID}.ylabel")
        if g not in els
    ]
    assert not missing, f"寄生轴上的这些元素没进 manifest：{missing}（issue #217 的原貌）"
    assert els[f"{PARASITE_GID}.lines_0"]["label"] == "曲线 “parasite”"
    assert els[f"{PARASITE_GID}.ylabel"]["label"] == "Y 轴 “parasite y”"


def test_the_parasite_is_named_as_a_twin_not_as_an_inset(library):
    """元素树里叫「子图 1（右轴）」，不是「插图 2」也不是「子图 3」。

    寄生轴在用户眼里就是 `twinx()`：它与宿主逐像素重叠，按遍历序拿一个不相干
    的名字，元素树里根本猜不到它是谁。判据走的是既有的那一份孪生轴判据
    （`coincident_shared_axes_pairs`：共享 x/y + 落位重合），**没有第二份**。
    """
    els = _by_gid(_manifest(library))
    assert els[PARASITE_GID]["label"] == "子图 1（右轴）"
    assert els[INSET_GID]["label"] == "插图 1"


# ---------------------------------------------------------------------------
# ② gid 稳不稳
# ---------------------------------------------------------------------------
def test_the_parasite_is_numbered_after_every_child_axes(library):
    """插图仍是 `axes_1`，寄生轴排在它后面。

    这条是**数据级**的：`axes_i` 以 override 的 gid 形式存进用户文档。把寄生轴
    与 `child_axes` 合成一趟遍历，按属性先后它会插到插图前面，于是存量文档里
    写着 `axes_1.lines_0` 的那条 override 升级后指向另一个 axes——用户重开项目，
    改动落在了别的东西上。所以遍历必须**两趟**：改动前的那份序列是新序列的
    严格前缀。
    """
    els = _by_gid(_manifest(library))
    assert els[HOST_GID]["role"] == "axes" and els[HOST_GID]["label"] == "子图 1"
    assert els[INSET_GID]["label"] == "插图 1", (
        "插图的编号被寄生轴顶掉了一位——存量文档里的 axes_1 从此指向另一个 axes"
    )
    assert PARASITE_GID in els
    axes_gids = [e["gid"] for e in _manifest(library)["elements"] if e["role"] == "axes"]
    assert axes_gids == [HOST_GID, INSET_GID, PARASITE_GID], axes_gids


#: 一串「这一步的全量 patch 列表」。最后一步是**减**出来的：override 是全量列表
#: 语义，撤销走的是完全不同的代码路径（还原 → 组内其他成员要不要重放），历史上
#: 热态与重放就在这里分岔。
_STEPS = [
    [{"gid": f"{PARASITE_GID}.lines_0", "prop": "color", "value": "#123456"}],
    [
        {"gid": f"{PARASITE_GID}.lines_0", "prop": "color", "value": "#123456"},
        {"gid": f"{PARASITE_GID}.lines_0", "prop": "linewidth", "value": 3.5},
        {"gid": f"{PARASITE_GID}.ylabel", "prop": "text", "value": "second axis"},
        {"gid": PARASITE_GID, "prop": "ylim", "value": [0.0, 150.0]},
    ],
    [
        {"gid": f"{PARASITE_GID}.lines_0", "prop": "linewidth", "value": 3.5},
        {"gid": PARASITE_GID, "prop": "ylim", "value": [0.0, 150.0]},
    ],
]


def test_hot_equals_a_fresh_replay_for_parasite_overrides(library):
    """寄生轴上的编辑：热会话的样子 == 全新 worker 全量重放的样子。

    这是写回事务不变式在这条新登记面上的那一半（「所见 == 所写 == 重开后重放
    出来的」）。**两把尺子一起上**：manifest 抓显示层的漂移，像素抓画面层的
    ——写回自检的几何比对两者都量不到（颜色、线宽都不动包围盒）。
    """
    final = _STEPS[-1]
    hot = _worker(library)
    try:
        for step in _STEPS:
            resp = hot.override(STEM, list(step))
            assert not (resp.get("warnings") or []), resp["warnings"]
        hot_man = hot.override(STEM, list(final))["manifest"]
        hot_png = _png(hot, final, "hot")
    finally:
        pool.discard(hot)

    fresh = _worker(library)
    try:
        fresh_resp = fresh.override(STEM, list(final))
        assert not (fresh_resp.get("warnings") or []), fresh_resp["warnings"]
        fresh_man = fresh_resp["manifest"]
        fresh_png = _png(fresh, final, "fresh")
    finally:
        pool.discard(fresh)

    assert [e["gid"] for e in hot_man["elements"]] == [e["gid"] for e in fresh_man["elements"]], (
        "改过寄生轴之后热态与重放的 gid 串不一样——写回会因为「元素不存在」被阻断"
    )
    assert hot_man == fresh_man, "热态与全新 worker 重放的 manifest 不一致"
    assert hot_png == fresh_png, "manifest 一样但**画出来**不一样"


# ---------------------------------------------------------------------------
# ③ 宣称的能力兑不兑现
# ---------------------------------------------------------------------------
def test_the_parasite_hides_position_and_visible_and_says_why(library):
    """两个死开关不出字段，但**各自说得出为什么**。

    少一个控件而不给理由，用户只会以为是漏了或是坏了（#76 的教训）。reason 是
    稳定 code，措辞归界面（`inspector/UnsupportedProps.tsx`）。
    """
    els = _by_gid(_manifest(library))
    par = els[PARASITE_GID]
    props = {f["prop"] for f in par["editable"]}
    assert "position" not in props and "visible" not in props, (
        f"寄生轴仍在宣称改不动的东西：{sorted(props & {'position', 'visible'})}"
    )
    assert par.get("resizable") is False
    assert {u["prop"]: u["reason"] for u in par.get("unsupported_props", [])} == {
        "position": "parasite_host_rect",
        "visible": "parasite_host_draw",
    }
    # **不是一刀切**：同一张图上宿主的这两条照常给，寄生轴上其余的照常给
    assert {"position", "visible"} <= {f["prop"] for f in els[HOST_GID]["editable"]}
    assert {"ylim", "yscale", "grid_y"} <= props


def test_those_two_props_really_are_dead_on_a_parasite(library):
    """不给控件的理由必须**当场兑现**：设进去，像素一个都不动。

    只断言「字段不在」是不够的——那样的判据在「其实改得动、我们却藏了起来」
    时照样绿（`Arc` 那次就是把使能项没开当成了能力不存在）。所以正反两侧各量
    一次：寄生轴上这两条设了没反应，**宿主上同样两条必须有反应**。少了后面那
    半，夹具哪天不画寄生轴了，前半段会一路恒真。

    这也是 matplotlib 升版的看护点：哪天 `HostAxesBase.draw` 开始尊重寄生轴
    自己的 visible / position，这条会当场红，提醒把控件放回来。

    **开头那条「寄生轴在不在」不是冗余**：反证时实测过，遍历不认寄生轴的那一版
    上，`axes_2` 根本不存在，两条 patch 打在一个没有的 gid 上什么也不发生，
    `moved == base` 与 `hidden == base` 双双恒真——这条用例是这个文件里唯一
    在 #217 原貌下**仍然绿**的一条。宿主那侧的对照组挡不住它：宿主一直都在。
    """
    w = _worker(library)
    try:
        assert PARASITE_GID in _by_gid(w.override(STEM, [])["manifest"]), (
            f"{PARASITE_GID} 不在 manifest 里——下面两条 patch 会打空，判据恒真"
        )
        base = _png(w, [], "base")
        moved = _png(
            w, [{"gid": PARASITE_GID, "prop": "position", "value": [0.2, 0.2, 0.4, 0.4]}], "par-pos"
        )
        hidden = _png(w, [{"gid": PARASITE_GID, "prop": "visible", "value": False}], "par-vis")
        host_moved = _png(
            w, [{"gid": HOST_GID, "prop": "position", "value": [0.2, 0.2, 0.4, 0.4]}], "host-pos"
        )
        host_hidden = _png(w, [{"gid": HOST_GID, "prop": "visible", "value": False}], "host-vis")
    finally:
        pool.discard(w)

    assert host_moved != base, "对照组失效：宿主的落位都改不动，这张图量不出任何位置差"
    assert host_hidden != base, "对照组失效：宿主的显示开关都没反应"
    assert moved == base, "寄生轴的 position 其实改得动像素了——把控件放回来（去掉 position_locked）"
    assert hidden == base, "寄生轴的 visible 其实生效了——把控件放回来（去掉 visible_locked）"
