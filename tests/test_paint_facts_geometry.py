"""geometry 的两项**绘制事实**：`paint_order`（真实 draw 次序）与 `fill_alpha`（填充有效不透明度）。

命中评分（ADR 0086 修订）要回答「光标处**看得见**的墨迹是谁」：一条线只要没被画在它
上面的不透明填充盖住，就比「只落在某块填充内部」更该被选中。这需要引擎说出两件事：

* `paint_order`：该元素在 matplotlib **实际 draw 顺序**里的序号。主语是 manifest 测量那一刻
  的 `fig` 对象（与 geometry 同一进程、同一份状态），不是 zorder 字面值也不是登记序。
  zorder 相同按添加次序，不同 axes 按 axes 的 zorder（twin 轴在宿主之后）；
* `fill_alpha`：填充**此刻画出来**的不透明度。matplotlib 的 `set_alpha` 是**替换**颜色里的
  alpha 而不是相乘（实测 fc=(1,0,0,.5) + alpha=.5 画出来是 .5 不是 .25），所以直接读
  `get_facecolor()`；逐元素色取最小值（保守：宁可认为盖不住）。

本进程不 import matplotlib（worker 经 `pool.one_shot()` 起在科学栈解释器里）。
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

SCRIPT_NAME = "fig_paint_facts.py"
ENTRY = "main"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    x = np.linspace(0, 1, 5)
    # 登记序 0..：同 zorder 的三条线，按添加次序画
    ax.plot(x, x, zorder=2)
    ax.plot(x, x[::-1], zorder=2)
    # 脚本写在后面、zorder 却低：真实次序在前两条线之前
    ax.plot(x, x * 0 + 0.5, zorder=0.5)
    # 半透明填充（零件 alpha 写在 facecolor 里）与整体 alpha 两种写法
    ax.fill_between(x, 0, x, facecolor=(1, 0, 0, 0.12), edgecolor="none", zorder=1)
    ax.fill_between(x, 0, 1, color="tab:blue", alpha=0.3, zorder=1)
    # facecolor alpha 与 artist alpha 同时给：matplotlib 取替换而不是相乘
    ax.add_patch(Rectangle((0.1, 0.1), 0.2, 0.2, fc=(0, 0, 1, 0.5), alpha=0.8, zorder=3))
    # 不透明填充，画在最上面
    ax.add_patch(Rectangle((0.5, 0.5), 0.2, 0.2, fc="k", zorder=9))
    # 空心：只有描边
    ax.add_patch(Rectangle((0.7, 0.1), 0.1, 0.1, fill=False, ec="k", zorder=4))
    # 逐元素不同 alpha 的 PolyCollection：取最小
    from matplotlib.collections import PolyCollection

    pc = PolyCollection(
        [[(0.1, 0.8), (0.2, 0.8), (0.2, 0.9)], [(0.3, 0.8), (0.4, 0.8), (0.4, 0.9)]],
        facecolors=[(0, 1, 0, 0.9), (0, 1, 0, 0.2)],
        zorder=5,
    )
    ax.add_collection(pc)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # 孪生轴：整个 axes 画在宿主之后，哪怕里面的线 zorder 比宿主的线低
    ax2 = ax.twinx()
    ax2.plot(x, x, zorder=0.1)
    # 另一张图里的第二个 axes（不同 axes，第二个在后）
    fig.savefig("PaintFig.pdf")

    fig2, (a, b) = plt.subplots(1, 2, figsize=(5.0, 2.5))
    a.plot([0, 1], [0, 1], zorder=100)  # 第一个 axes 里 zorder 再高，也在第二个 axes 之前
    b.plot([0, 1], [1, 0], zorder=0)
    fig2.savefig("TwoAxFig.pdf")

    fig3, ax3 = plt.subplots(figsize=(4.0, 3.0))
    ax3.bar([0, 1, 2], [1, 2, 3], color=[(1, 0, 0, 0.9), (1, 0, 0, 0.4), (1, 0, 0, 0.7)])
    ax3.errorbar([0, 1, 2], [1, 2, 3], yerr=0.2, fmt="o")
    fig3.savefig("BarFig.pdf")

    # 成员次序不一：第 0 根柱 zorder 5（在线之上），其余在线之下
    fig4, ax4 = plt.subplots(figsize=(4.0, 3.0))
    bars = ax4.bar([0, 1, 2], [1, 2, 3])
    bars[0].set_zorder(5)
    ax4.plot([0, 2], [1, 1], zorder=4)
    fig4.savefig("MixedBarFig.pdf")

    # 一不透明一完全透明（alpha=0）的柱：透明柱仍是「填充了」，整组不能报成不透明
    fig5, ax5 = plt.subplots(figsize=(4.0, 3.0))
    ax5.plot([0, 1], [1, 1])
    ax5.bar([0, 1], [2, 2], color=[(0, 0, 1, 1.0), (0, 0, 1, 0.0)])
    # facecolor='none' 的单个 patch：根本不填充
    ax5.add_patch(Rectangle((0.2, 0.2), 0.3, 0.3, fc="none", ec="k"))
    fig5.savefig("TransparentBarFig.pdf")

    # 独立箭头（端点契约，manifest 不给 geometry）画在后加的不透明 patch 下面
    from matplotlib.patches import FancyArrowPatch

    fig6, ax6 = plt.subplots(figsize=(4.0, 3.0))
    ax6.set_xlim(0, 1)
    ax6.set_ylim(0, 1)
    ax6.add_patch(FancyArrowPatch((0.1, 0.5), (0.9, 0.5), arrowstyle="->"))
    ax6.add_patch(Rectangle((0.3, 0.3), 0.4, 0.4, fc="k"))
    fig6.savefig("ArrowFig.pdf")

    # 纯箭头注解：箭头是 Annotation.arrow_patch，不是 Axes 的 child；矩形 zorder 5 画在注解（3）之上
    fig9, ax9 = plt.subplots(figsize=(4.0, 3.0))
    ax9.set_xlim(0, 1)
    ax9.set_ylim(0, 1)
    ax9.annotate("", xy=(0.9, 0.5), xytext=(0.1, 0.5), arrowprops={"arrowstyle": "->"})
    ax9.add_patch(Rectangle((0.3, 0.3), 0.4, 0.4, fc="k", zorder=5))
    fig9.savefig("AnnotArrowFig.pdf")

    # 只有 marker 的线：一颗半填充（左半实心、右半 'none'），一颗全填充
    fig10, ax10 = plt.subplots(figsize=(4.0, 3.0))
    ax10.set_xlim(0, 1)
    ax10.set_ylim(0, 1)
    ax10.plot([0.3], [0.5], "o", ms=30, fillstyle="left", mfc="k", markerfacecoloralt="none")
    ax10.plot([0.7], [0.5], "o", ms=30, mfc="k")
    fig10.savefig("HalfMarkerFig.pdf")

    # 不透明矩形被裁成圆（任意形状裁剪）；另一块不透明矩形只有默认的矩形裁剪
    from matplotlib.patches import Circle

    fig11, ax11 = plt.subplots(figsize=(4.0, 3.0))
    ax11.set_xlim(0, 1)
    ax11.set_ylim(0, 1)
    clipped = ax11.add_patch(Rectangle((0.1, 0.1), 0.4, 0.4, fc="k", zorder=5))
    clipped.set_clip_path(Circle((0.3, 0.3), 0.1, transform=ax11.transData))
    ax11.add_patch(Rectangle((0.6, 0.6), 0.2, 0.2, fc="k", zorder=5))
    # 柱组里一根柱的矩形裁剪框比其它柱小：组几何只带第一根的裁剪，表达不了
    bars = ax11.bar([0.15, 0.35], [0.3, 0.3], width=0.1, color="k", zorder=6)
    from matplotlib.transforms import Bbox, TransformedBbox

    bars[1].set_clip_box(TransformedBbox(Bbox([[0.3, 0.0], [0.4, 0.1]]), ax11.transData))
    fig11.savefig("ClipFig.pdf")

    # 文字类（画布按 bbox 命中、无 geometry）：标题 / 轴标签 / 文字 / 图例 / 图例项
    # 一根不透明柱 + 一根空心（facecolor='none'，带描边与 hatch）：空心不是「透明填充」
    fig8, ax8 = plt.subplots(figsize=(4.0, 3.0))
    ax8.bar([0], [2], color="tab:blue")
    ax8.bar([1], [2], facecolor="none", edgecolor="k", hatch="//")
    mixed = ax8.bar([2, 3], [2, 2], color="tab:red", edgecolor="k")
    mixed[1].set_facecolor("none")  # 同组里一根不透明、一根 facecolor='none'
    nofill = ax8.bar([4, 5], [2, 2], color="tab:red", edgecolor="k")
    nofill[1].set_fill(False)  # fill=False：facecolor 仍带色、alpha 为 0
    hidden = ax8.bar([6, 7], [2, 2], color="tab:red", edgecolor="k")
    hidden[1].set_alpha(0.0)
    hidden[1].set_visible(False)  # 隐藏的 alpha=0 柱：不进几何，也不该进绘制事实
    fig8.savefig("HollowBarFig.pdf")

    fig7, ax7 = plt.subplots(figsize=(4.0, 3.0))
    ax7.set_xlim(0, 1)
    ax7.set_ylim(0, 1)
    ax7.plot([0, 1], [0, 1], label="curve")
    ax7.set_axisbelow(False)  # Axis 画在线之上（zorder 2.5）：轴标签次序随 Axis，不是随线
    ax7.set_title("T")
    ax7.set_xlabel("X")
    ax7.text(0.5, 0.5, "note")
    ax7.legend(title="LT")
    ax7.add_patch(Rectangle((0.1, 0.1), 0.2, 0.2, fc="k", zorder=20))
    fig7.savefig("InkFig.pdf")
"""


@pytest.fixture(scope="module")
def manifests(tmp_path_factory):
    figs = tmp_path_factory.mktemp("paint-facts")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    cache: dict[str, dict] = {}

    def get(stem: str) -> dict:
        if stem not in cache:
            w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
            try:
                w.ensure_built()
                resp = w.override(stem, [])
                assert not resp.get("warnings"), resp["warnings"]
                cache[stem] = resp["manifest"]
            finally:
                pool.discard(w)
        return cache[stem]

    return get


def _geom(man, gid):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    assert "geometry" in el, f"{gid} 没有 geometry"
    return el["geometry"]


def _order(man, gid):
    return _geom(man, gid)["paint_order"]


def test_same_zorder_follows_add_order_and_zorder_wins_over_registration(manifests):
    man = manifests("PaintFig")
    l0, l1, l2 = (_order(man, f"axes_0.lines_{i}") for i in range(3))
    assert l0 < l1  # zorder 相同：先添加的先画
    assert l2 < l0  # 脚本里最后添加，但 zorder 0.5 最低：真实次序最靠前


def test_mixed_line_patch_collection_are_ranked_in_one_sequence(manifests):
    man = manifests("PaintFig")
    by_z = {
        "low_fill": _order(man, "axes_0.fill_0"),
        "line": _order(man, "axes_0.lines_0"),
        "rect_z3": _order(man, "axes_0.patches_0"),
        "hollow_z4": _order(man, "axes_0.patches_2"),
        "coll_z5": _order(man, "axes_0.fill_2"),
        "opaque_z9": _order(man, "axes_0.patches_1"),
    }
    assert (
        by_z["low_fill"]
        < by_z["line"]
        < by_z["rect_z3"]
        < by_z["hollow_z4"]
        < by_z["coll_z5"]
        < by_z["opaque_z9"]
    )


def test_twin_axes_content_paints_after_host_regardless_of_zorder(manifests):
    man = manifests("PaintFig")
    host_top = max(_order(man, f"axes_0.lines_{i}") for i in range(3))
    twin = _order(man, "axes_1.lines_0")  # zorder 0.1，比宿主所有线都低
    assert twin > host_top


def test_second_axes_paints_after_first_axes(manifests):
    man = manifests("TwoAxFig")
    assert _order(man, "axes_0.lines_0") < _order(man, "axes_1.lines_0")


def test_fill_alpha_reads_drawn_color_not_a_product(manifests):
    man = manifests("PaintFig")
    assert _geom(man, "axes_0.fill_0")["fill_alpha"] == 0.12  # facecolor 自带 alpha
    assert _geom(man, "axes_0.fill_1")["fill_alpha"] == 0.3  # artist alpha
    # facecolor alpha .5 + artist alpha .8：matplotlib 替换为 .8（不是 .4）
    assert _geom(man, "axes_0.patches_0")["fill_alpha"] == 0.8
    assert _geom(man, "axes_0.patches_1")["fill_alpha"] == 1.0


def test_hollow_and_line_have_no_fill_alpha(manifests):
    man = manifests("PaintFig")
    assert "fill_alpha" not in _geom(man, "axes_0.patches_2")  # fill=False
    assert "fill_alpha" not in _geom(man, "axes_0.lines_0")


def test_per_element_alpha_takes_the_minimum(manifests):
    man = manifests("PaintFig")
    assert _geom(man, "axes_0.fill_2")["fill_alpha"] == 0.2


def test_group_elements_get_facts_too(manifests):
    man = manifests("BarFig")
    bars = next(e for e in man["elements"] if e["role"] == "bar_series")
    assert bars["geometry"]["fill_alpha"] == 0.4  # 三根柱 .9 / .4 / .7 取最小
    eb = next(e for e in man["elements"] if e["role"] == "errorbar")
    # 误差棒在柱之后添加（同 zorder 之上）：次序更靠后
    assert eb["geometry"]["paint_order"] > bars["geometry"]["paint_order"]
    # 误差线（无填充）不能把带实心 marker 的整组拉成 0
    assert eb["geometry"]["fill_alpha"] == 1.0


def test_group_split_by_other_artists_has_no_paint_order(manifests):
    man = manifests("MixedBarFig")
    bars = next(e for e in man["elements"] if e["role"] == "bar_series")
    # 第 0 根柱 zorder 5（在线之上）、其余柱在线之下：线夹在成员之间，一个数字说不清
    # 整组在谁上面——次序未知（不写），线在低柱上方不会被组降层
    assert "paint_order" not in bars["geometry"]
    # 线自己的次序照常给
    assert "paint_order" in _geom(man, "axes_0.lines_0")


def test_contiguous_group_takes_the_latest_member(manifests):
    man = manifests("BarFig")
    eb = next(e for e in man["elements"] if e["role"] == "errorbar")
    bars = next(e for e in man["elements"] if e["role"] == "bar_series")
    # 误差棒的数据线与误差线 zorder 差 0.1 但紧挨着画：同一层，照给
    assert eb["geometry"]["paint_order"] > bars["geometry"]["paint_order"]


def test_endpoint_arrow_gets_element_level_paint_order(manifests):
    man = manifests("ArrowFig")
    arrow = next(e for e in man["elements"] if e.get("arrow_endpoints"))
    assert "geometry" not in arrow
    patch = next(e for e in man["elements"] if e["role"] == "patch")
    # 箭头先加、矩形后加（同 zorder）：箭头次序更小
    assert arrow["paint_order"] < patch["geometry"]["paint_order"]


def test_fully_transparent_member_keeps_group_conservative(manifests):
    man = manifests("TransparentBarFig")
    bars = next(e for e in man["elements"] if e["role"] == "bar_series")
    # alpha=0 的柱是「填充了但全透明」，不是「没填充」：整组取 0，下面的线仍可选
    assert bars["geometry"]["fill_alpha"] == 0.0


def _by_role(man, role):
    return [e for e in man["elements"] if e["role"] == role]


def test_ink_roles_get_element_level_paint_order(manifests):
    man = manifests("InkFig")
    patch = _by_role(man, "patch")[0]["geometry"]["paint_order"]
    legend = _by_role(man, "legend")[0]
    for role in ("title", "axis_label", "text", "legend"):
        els = [e for e in _by_role(man, role) if "paint_order" in e]
        assert els, f"{role} 没有 paint_order"
        # 高 zorder 的 patch 最后画
        assert all(e["paint_order"] < patch for e in els), role
    # 图例项（含标题）画在图例里：次序就是图例的
    texts = _by_role(man, "legend_text")
    assert len(texts) >= 2  # 标题 + 一项
    assert all(e["paint_order"] == legend["paint_order"] for e in texts)


def test_axis_label_order_is_its_axis_order(manifests):
    man = manifests("InkFig")
    xl = next(e for e in _by_role(man, "axis_label") if e["gid"].endswith("xlabel"))
    # 轴标签随 Axis 画：Axis（axisbelow=False 时 zorder 2.5）在线之后、zorder 20 的 patch 之前
    assert _geom(man, "axes_0.lines_0")["paint_order"] < xl["paint_order"]
    assert xl["paint_order"] < _by_role(man, "patch")[0]["geometry"]["paint_order"]


def test_hollow_patch_member_is_not_a_transparent_fill(manifests):
    man = manifests("HollowBarFig")
    series = _by_role(man, "bar_series")
    # 柱组按添加次序：[0] 不透明、[1] 空心(hatch)、[2] 一根 set_facecolor('none')、[3] 一根 set_fill(False)
    for idx in (2, 3):
        grp = series[idx]["geometry"]
        assert grp["fill"] is True  # 前提：走得到 stamp_paint 的 fill 分支
        # 空心成员的 RGBA 是 (r,g,b,0)，不能当成 0.0 的透明填充把整组拉成透明
        assert grp["fill_alpha"] == 1.0, idx


def test_hidden_member_is_left_out_of_group_facts(manifests):
    man = manifests("HollowBarFig")
    grp = _by_role(man, "bar_series")[4]["geometry"]
    assert grp["fill"] is True  # 前提：走得到 stamp_paint 的 fill 分支
    # 几何里只有看得见的那根不透明柱；隐藏的 alpha=0 柱不能把整组拉成透明
    assert len(grp["paths"]) == 1
    assert grp["fill_alpha"] == 1.0


def test_annotation_arrow_inherits_its_annotation_order(manifests):
    man = manifests("AnnotArrowFig")
    arrow = next(e for e in man["elements"] if e.get("arrow_endpoints"))
    assert "geometry" not in arrow  # 前提：端点箭头，走元素级 paint_order 那条路
    patch = next(e for e in man["elements"] if e["role"] == "patch")
    # 箭头随所属 Annotation（zorder 3）画，在 zorder 5 的矩形之下：要能被它盖住降层
    assert arrow["paint_order"] < patch["geometry"]["paint_order"]


def test_half_filled_marker_counts_its_empty_half(manifests):
    man = manifests("HalfMarkerFig")
    half, full = _geom(man, "axes_0.lines_0"), _geom(man, "axes_0.lines_1")
    assert half["fill"] is True and full["fill"] is True  # 前提：走得到 stamp_paint 的 fill 分支
    # 右半 'none' 是空的：整颗盖不住透过它看得见的线
    assert half["fill_alpha"] == 0.0
    assert full["fill_alpha"] == 1.0


def test_custom_clipped_fill_claims_no_cover(manifests):
    man = manifests("ClipFig")
    clipped, plain = _geom(man, "axes_0.patches_0"), _geom(man, "axes_0.patches_1")
    assert clipped["fill"] is True and "clip" not in clipped  # 前提：裁剪形状表达不了
    # 担保不了画出来的范围 → 不给遮挡事实；默认矩形裁剪的照常给
    assert "fill_alpha" not in clipped
    assert plain["fill_alpha"] == 1.0


def test_group_with_mismatched_member_clips_claims_no_cover(manifests):
    man = manifests("ClipFig")
    grp = next(e for e in man["elements"] if e["role"] == "bar_series")["geometry"]
    assert grp["fill"] is True  # 前提：走得到 stamp_paint 的 fill 分支
    # 成员的裁剪框不一致：一份 clip 代表不了，不给遮挡事实
    assert "fill_alpha" not in grp
