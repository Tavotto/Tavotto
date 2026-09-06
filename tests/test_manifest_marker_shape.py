"""manifest 的 `marker_current`：面板上那一行**此刻画的是什么形状**。

`marker` 字段的 `value` 回答的是「用户选中的是哪个取值」，那不等于形状——
散点没被整体换过标记时它是 `"original"`（继承脚本），曲线的它可能是
`(5, 1, 0)` / `$\\alpha$` / 一个 Path 的 repr。两种情形下界面上都只剩一行字。
这个只读事实是补上「那到底是圆是方」这一句的唯一出口。

钉的是「坏掉之后会怎样」：

* 认名字认错 → 界面画一个图上没有的形状，而它看起来言之凿凿；
* 认不出时不发几何 → 用户又回到只有一行代码字样的状态（本轮的原始缺陷）；
* 「不知道」与「没有标记」压成一档 → 老引擎发来的清单被读成「图上没标记」；
* 一个 collection 里混着两种形状却挑第一条冒充全体 → 判据量错了对象；
* 顶点跑出单位框 / 精度不截断 → 前端画歪，或 manifest 体积失控。

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

SCRIPT_NAME = "fig_marker.py"
ENTRY = "main"
STEM = "MarkerFig"

#: 16 个拉丁字母的 mathtext 标记，实测 338 个顶点（上限 256）——用它把
#: 「太复杂就不发几何」这条闸真的打开。8 个字母是 204 个顶点，还在闸内。
HUGE_MARKER = r"$\mathrm{ABCDEFGHIJKLMNOP}$"

LIBRARY = (
    """\
import matplotlib.pyplot as plt
from matplotlib.collections import PathCollection
from matplotlib.markers import MarkerStyle


def _unit(name):
    ms = MarkerStyle(name)
    return ms.get_path().transformed(ms.get_transform())


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))

    # lines_0：具名标记（图形网格里画得出的那 13 个之一）
    ax.plot([0, 1, 2], [0, 1, 2], marker="o", label="named")
    # lines_1：没有标记 —— `none`，不是「不知道」
    ax.plot([0, 1, 2], [1, 1, 1], label="bare")
    # lines_2：元组标记，前端认不出这个字面量 → 必须发几何
    ax.plot([0, 1, 2], [2, 1, 0], marker=(5, 1, 0), label="tuple")
    # lines_3：顶点数超过上限 → 只说「有一个叫不出名字的形状」
    ax.plot([0, 1], [0.5, 0.5], marker="%s", label="huge")

    # scatter_0：脚本写了具名标记，而 `value` 仍是 "original"
    ax.scatter([0, 1, 2], [0.2, 0.4, 0.6], marker="D", label="named-scatter")
    # scatter_1：`H` 不在图形网格里 → 发几何
    ax.scatter([0, 1, 2], [0.3, 0.5, 0.7], marker="H")
    # scatter_2：一个 collection 里两种形状 → 如实说「多个」
    # `sizes` 不能省：`marker` 这条能力的判据是「此刻真的有 sizes」
    # （`collection_caps`），没有 sizes 的 PathCollection 连 marker 字段都不出。
    pc = PathCollection(
        [_unit("o"), _unit("s")],
        sizes=[36.0, 36.0],
        offsets=[(0.5, 1.5), (1.5, 1.5)],
        offset_transform=ax.transData,
    )
    ax.add_collection(pc)

    ax.stem([0.2, 0.6], [1.2, 1.4], markerfmt="s")
    ax.legend()
    fig.savefig("MarkerFig.pdf")
"""
    % HUGE_MARKER
)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("marker-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


def _manifest(worker, patches=()):
    resp = worker.override(STEM, list(patches))
    assert not resp.get("warnings"), resp["warnings"]
    return resp["manifest"]


def _field(man, gid, prop="marker"):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next(f for f in el["editable"] if f["prop"] == prop)


# ---------------------------------------------------------------------------
# 认得出名字的
# ---------------------------------------------------------------------------
def test_named_marker_on_a_line(worker):
    f = _field(_manifest(worker), "axes_0.lines_0")
    assert f["value"] == "o"
    assert f["marker_current"] == {"kind": "named", "name": "o"}


def test_scatter_says_the_shape_its_value_cannot(worker):
    """本轮的原始缺陷：散点的 `value` 是 `"original"`（继承），说不出形状。

    事实字段必须把脚本真正用的那个 `D` 说出来——**而 `value` 一个字不变**，
    四档值语义（uniform / mixed / inherit / unsupported）一档都不许压扁。
    """
    f = _field(_manifest(worker), "axes_0.scatter_0")
    assert f["value"] == "original", "继承这一档不许被形状挤掉"
    assert f["marker_current"] == {"kind": "named", "name": "D"}


def test_every_option_in_the_grid_is_recognised_by_name(worker):
    """选项表里的每一个图形项，换上去之后都必须被认成**它自己**。

    这条同时钉住两件事：认名字的对照表没有漏项，也没有两个名字撞在一起
    （撞了的话某个选项会被认成另一个名字，界面画出别人的形状）。
    期望值取自 manifest 自己发的 `options`，不手抄第二份清单。
    """
    gid = "axes_0.scatter_0"
    options = [o for o in _field(_manifest(worker), gid)["options"] if o != "original"]
    assert len(options) >= 13, f"选项表比预期短，判据可能量错了对象：{options}"
    for opt in options:
        f = _field(_manifest(worker, [{"gid": gid, "prop": "marker", "value": opt}]), gid)
        assert f["value"] == opt
        assert f["marker_current"] == {"kind": "named", "name": opt}, opt


def test_override_wins_because_the_manifest_is_the_rendered_state(worker):
    """有 override 时发的是 **override 之后**的形状（脚本写的是 `D`）。"""
    gid = "axes_0.scatter_0"
    f = _field(_manifest(worker, [{"gid": gid, "prop": "marker", "value": "^"}]), gid)
    assert f["marker_current"] == {"kind": "named", "name": "^"}
    # 清空 override 之后回到脚本原始形状（全量列表语义）
    assert _field(_manifest(worker), gid)["marker_current"] == {"kind": "named", "name": "D"}


def test_stem_and_legend_handle_carry_the_fact_too(worker):
    """同一条判据的四个消费者：曲线 / 散点 / 茎叶 / 图例示意标记。

    漏掉任何一个的表现都是「有的面板看得见形状、有的看不见」——
    共享判据修一处不算修完。
    """
    man = _manifest(worker)
    stem = next(e for e in man["elements"] if e["gid"].startswith("axes_0.stemseries_"))
    assert _field(man, stem["gid"])["marker_current"] == {"kind": "named", "name": "s"}
    # 图例条目里带示意标记的那几个，一一对应上面四条曲线，四种答案各出一次
    # （散点那条条目的 handle 是 PathCollection，本来就没有 `handle_marker`）
    kinds = [
        f["marker_current"]
        for e in man["elements"]
        if e["role"] == "legend_text"
        for f in e["editable"]
        if f["prop"] == "handle_marker"
    ]
    assert len(kinds) == 4, kinds
    assert kinds[0] == {"kind": "named", "name": "o"}
    assert kinds[1] == {"kind": "none"}
    assert kinds[2]["kind"] == "path" and len(kinds[2]["vertices"]) == 11
    assert kinds[3] == {"kind": "too_complex"}


# ---------------------------------------------------------------------------
# 认不出名字的：发几何
# ---------------------------------------------------------------------------
def test_unnamed_marker_ships_its_geometry(worker):
    """元组标记 `(5, 1, 0)`：前端认不出这个字面量，必须拿到顶点自己画。"""
    f = _field(_manifest(worker), "axes_0.lines_2")
    cur = f["marker_current"]
    assert cur["kind"] == "path"
    assert len(cur["vertices"]) == 11
    assert cur["codes"] is not None and len(cur["codes"]) == len(cur["vertices"])
    assert cur["codes"][0] == 1, "第一个点是 MOVETO"


def test_geometry_is_normalised_into_the_unit_box(worker):
    """顶点落在 [-0.5, 0.5]，长的那一维顶到边 —— 前端照着画 12 px 预览。"""
    cur = _field(_manifest(worker), "axes_0.scatter_1")["marker_current"]
    assert cur["kind"] == "path"
    xs = [v[0] for v in cur["vertices"]]
    ys = [v[1] for v in cur["vertices"]]
    assert min(xs) >= -0.5 and max(xs) <= 0.5, (min(xs), max(xs))
    assert min(ys) >= -0.5 and max(ys) <= 0.5, (min(ys), max(ys))
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    assert abs(span - 1.0) < 1e-9, f"没顶到单位框的边（span={span}）"


def test_geometry_precision_is_truncated(worker):
    """精度截断到 4 位：一条标记路径的 JSON 体积因此有上界。

    不截断的话每个坐标是 17 位有效数字，同一条路径要多花三倍字节，
    而预览是 12 px —— 第 5 位小数在那里是 1.2e-3 个像素。
    """
    cur = _field(_manifest(worker), "axes_0.scatter_1")["marker_current"]
    for x, y in cur["vertices"]:
        assert round(x, 4) == x and round(y, 4) == y, (x, y)


def test_too_many_vertices_says_so_instead_of_shipping_them(worker):
    """超过顶点上限：只说「有个叫不出名字的形状」，不把几何搬进 manifest。"""
    cur = _field(_manifest(worker), "axes_0.lines_3")["marker_current"]
    assert cur == {"kind": "too_complex"}


# ---------------------------------------------------------------------------
# 「没有」与「多个」各是独立一档
# ---------------------------------------------------------------------------
def test_no_marker_is_none_not_a_missing_field(worker):
    """没有标记的曲线发 `none`。

    **和字段缺席不是一回事**：缺席的含义是「引擎说不出」（老引擎、
    或者构造 MarkerStyle 时出了岔子），把两者合并会让老清单被读成
    「图上没有标记」。
    """
    f = _field(_manifest(worker), "axes_0.lines_1")
    assert f["value"] == "None"
    assert f["marker_current"] == {"kind": "none"}


def test_two_shapes_in_one_collection_report_multiple(worker):
    """一个 collection 里混着圆和方：拿第一条冒充全体就是量错了对象。"""
    cur = _field(_manifest(worker), "axes_0.scatter_2")["marker_current"]
    assert cur == {"kind": "multiple"}
