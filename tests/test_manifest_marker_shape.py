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
from matplotlib.path import Path


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

    # scatter_3：自定义 Path 标记 —— matplotlib 只缩放不居中（实测包围盒
    # 落在 [0, 0.5]²），是「归一化到底做没做」的样本。`H` 那种原始路径本来
    # 就正好铺满单位框的，把归一化整段拿掉都看不出差别。
    ax.scatter(
        [0.4, 1.4],
        [0.9, 1.0],
        marker=Path([(0, 0), (1, 0), (0.5, 1)], [Path.MOVETO, Path.LINETO, Path.LINETO]),
        s=[36.0, 36.0],
    )
    # scatter_4：`$\\odot$` 的 CLOSEPOLY 占位点落在 x = -0.638，比整个字形还
    # 靠左 —— 是「占位点有没有被排除在包围盒之外」的样本。
    ax.scatter([0.8, 1.8], [1.1, 1.2], marker=r"$\\odot$")

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


CLOSEPOLY = 79


def _drawn(cur):
    """真正画出来的那些顶点：CLOSEPOLY 那一个是占位，坐标没人读。"""
    codes = cur["codes"]
    if codes is None:
        return list(cur["vertices"])
    return [v for v, c in zip(cur["vertices"], codes) if c != CLOSEPOLY]


def _box(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


@pytest.mark.parametrize("gid", ["axes_0.scatter_1", "axes_0.scatter_3", "axes_0.scatter_4"])
def test_geometry_is_normalised_into_the_unit_box(worker, gid):
    """顶点落在 [-0.5, 0.5]、居中、长的那一维顶到边 —— 前端照着画 12 px 预览。

    三个样本各堵一个洞，缺一个这条判据就会在某种变异下恒真：

    * `scatter_1`（`H`）原始路径就正好铺满单位框，**归一化整段拿掉都不红**；
    * `scatter_3`（自定义 Path）matplotlib 只缩放不居中，钉住「居中」这一半；
    * `scatter_4`（`$\\odot$`）的 CLOSEPOLY 占位点在字形之外，钉住「占位点
      不参与包围盒」——把它算进去，形状会缩水而且偏一块。
    """
    cur = _field(_manifest(worker), gid)["marker_current"]
    assert cur["kind"] == "path", cur["kind"]
    lo_x, hi_x, lo_y, hi_y = _box(_drawn(cur))
    assert lo_x >= -0.5 and hi_x <= 0.5, (lo_x, hi_x)
    assert lo_y >= -0.5 and hi_y <= 0.5, (lo_y, hi_y)
    span = max(hi_x - lo_x, hi_y - lo_y)
    assert abs(span - 1.0) < 1e-9, f"没顶到单位框的边（span={span}）"
    assert abs(lo_x + hi_x) < 1e-9 and abs(lo_y + hi_y) < 1e-9, "没居中"


def test_closepoly_vertices_are_placeholders_not_coordinates(worker):
    """CLOSEPOLY 那一个顶点一律发 `[0, 0]`。

    它的坐标任何渲染器都不读（画到 CLOSEPOLY 只是闭合子路径），而
    matplotlib 往那里写的常常在形状之外。照原样发出去，「所有顶点都在
    单位框内」这句话就不成立，下游任何自己算包围盒的消费者都会被带偏。
    """
    cur = _field(_manifest(worker), "axes_0.scatter_4")["marker_current"]
    closing = [v for v, c in zip(cur["vertices"], cur["codes"]) if c == CLOSEPOLY]
    assert closing, "用例前提失效：这个标记的路径里没有 CLOSEPOLY"
    assert all(v == [0.0, 0.0] for v in closing), closing


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


# ---------------------------------------------------------------------------
# `marker_original`：override 之前那个形状（「回到脚本原始 = 回到这个形状」）
# ---------------------------------------------------------------------------
# `marker_current` 读的是图上此刻那条路径，override 之后脚本原来那条已经不在
# 图上——于是「脚本原始」那一格说不出自己会变成什么形状，用户看到的是一个空
# 的继承状态点。这一族用例钉的是补上那句话之后的四件事：
#
# * 有 override 时说得出原来那个形状（四个消费者一个都不许漏）；
# * **没有 override 时字段整个缺席**——缺席 = 与 current 相同，前端不用再判
#   一次「改没改过」；发一份重复的只会让两个事实有机会说出不同的话；
# * 原样取的是 override 系统采下的那一份（`state.originals`），不是另算一遍：
#   真的撤销回去之后 `marker_current` 必须落在同一个值上；
# * 取值本身说不出形状的那些（元组标记）原样也得说得出——那正是这个字段
#   存在的理由。


def _no_override_field(worker, gid, prop="marker"):
    return _field(_manifest(worker), gid, prop)


def test_no_override_means_the_field_is_absent(worker):
    """没改过就不发 `marker_original`。

    缺席的含义是「与 `marker_current` 相同」。发一份重复的不是更保险而是
    更坏：两个字段从此有机会说出不同的话，而前端已经按「缺席 = 相同」写了
    退回分支。四个消费者一起钉——漏掉任何一个的表现都是那一格凭空多出一个
    与当前值一模一样的「脚本原始」形状。
    """
    man = _manifest(worker)
    stem = next(e for e in man["elements"] if e["gid"].startswith("axes_0.stemseries_"))
    checked = 0
    for gid in ("axes_0.lines_0", "axes_0.scatter_0", stem["gid"]):
        f = _field(man, gid)
        assert "marker_current" in f, gid
        assert "marker_original" not in f, (gid, f.get("marker_original"))
        checked += 1
    for e in man["elements"]:
        for f in e["editable"]:
            if f["prop"] == "handle_marker":
                assert "marker_original" not in f, (e["gid"], f.get("marker_original"))
                checked += 1
    assert checked == 7, f"消费者少数了，判据可能量错了对象：{checked}"


def test_scatter_says_the_shape_it_would_go_back_to(worker):
    """本轮的原始缺陷：散点换成菱形之后，「脚本原始」那一格说不出形状。

    脚本写的是 `D`。换成 `^` 之后 `marker_current` 是 `^`（图上那个），
    `marker_original` 必须仍是 `D`——两个事实各说各的那一半，谁都不顶替谁。
    """
    gid = "axes_0.scatter_0"
    f = _field(_manifest(worker, [{"gid": gid, "prop": "marker", "value": "^"}]), gid)
    assert f["value"] == "^"
    assert f["marker_current"] == {"kind": "named", "name": "^"}
    assert f["marker_original"] == {"kind": "named", "name": "D"}


def test_line_original_survives_even_when_the_value_cannot_say_it(worker):
    """曲线：脚本原样是元组标记 `(5, 1, 0)`，**它自己说不出形状**。

    这正是这个字段存在的理由——换成 `o` 之后，那一行的取值与「回到脚本原始」
    两句话都只剩代码字样。原样必须照样发几何，且与没改过时 `marker_current`
    发的那份**逐字节相同**：两边读的是同一条路径，不是各算一遍。
    """
    gid = "axes_0.lines_2"
    before = _no_override_field(worker, gid)["marker_current"]
    assert before["kind"] == "path" and len(before["vertices"]) == 11, before

    f = _field(_manifest(worker, [{"gid": gid, "prop": "marker", "value": "o"}]), gid)
    assert f["marker_current"] == {"kind": "named", "name": "o"}
    assert f["marker_original"] == before


def test_stem_and_legend_handle_carry_the_original_too(worker):
    """同一条判据的另外两个消费者：茎叶 markerline 与图例示意标记。

    共享判据修一处不算修完——漏掉哪个，表现都是「有的面板回得去、有的面板
    只剩一个空的继承点」。
    """
    man = _manifest(worker)
    stem_gid = next(e["gid"] for e in man["elements"] if e["gid"].startswith("axes_0.stemseries_"))
    entry_gid = next(
        e["gid"]
        for e in man["elements"]
        if e["role"] == "legend_text" and any(f["prop"] == "handle_marker" for f in e["editable"])
    )

    man = _manifest(
        worker,
        [
            {"gid": stem_gid, "prop": "marker", "value": "^"},
            {"gid": entry_gid, "prop": "handle_marker", "value": "x"},
        ],
    )
    stem_f = _field(man, stem_gid)
    assert stem_f["marker_current"] == {"kind": "named", "name": "^"}
    assert stem_f["marker_original"] == {"kind": "named", "name": "s"}, "脚本写的是 markerfmt='s'"

    entry_f = _field(man, entry_gid, "handle_marker")
    assert entry_f["marker_current"] == {"kind": "named", "name": "x"}
    assert entry_f["marker_original"] == {"kind": "named", "name": "o"}


def test_going_back_to_the_script_lands_on_exactly_that_shape(worker):
    """重放一致性：撤销之后 `marker_current` **落在** `marker_original` 上。

    这才是「回到脚本原始 = 回到这个形状」那句话的兑现处。两个值来自同一份
    `state.originals`（override 系统在第一次应用之前采的那份，撤销时回灌的
    也是它），所以它们必须相等——不相等的话界面画的是一个承诺，而点下去
    得到的是另一个东西。撤销之后原样字段本身也要跟着消失。
    """
    gid = "axes_0.scatter_0"
    promised = _field(_manifest(worker, [{"gid": gid, "prop": "marker", "value": "*"}]), gid)[
        "marker_original"
    ]

    back = _field(_manifest(worker), gid)
    assert back["value"] == "original"
    assert back["marker_current"] == promised
    assert "marker_original" not in back, "没有 override 了，这个字段该整个消失"


def test_a_failed_override_is_not_an_override(worker):
    """应用失败的那一条**不算 override**，不许因此冒出一格「脚本原始」。

    这是「判据是 `state.applied` 还是 `state.originals`」那个分岔的可观测处：
    `apply()` 里原样采在 setter **之前**、`applied` 记在 setter **之后**，
    所以 setter 抛异常时 originals 留下了一条永远没人回收的记录（它没有对应
    的 applied 条目，下一轮的清理循环遍历的是 applied）。照 originals 判的话，
    一次失败的 override 会让这一行**从此**多出一格与当前值一模一样的「脚本
    原始」——图上一个像素都没变，界面却说它改过。
    """
    gid = "axes_0.lines_1"
    resp = worker.override(STEM, [{"gid": gid, "prop": "marker", "value": "@@"}])
    assert any("应用失败" in w for w in resp.get("warnings", [])), (
        f"用例前提失效：这个值居然应用成功了 {resp.get('warnings')}"
    )
    f = _field(resp["manifest"], gid)
    assert f["marker_current"] == {"kind": "none"}, "图上一个像素都不该变"
    assert "marker_original" not in f, f.get("marker_original")
    # 后面的用例还在同一个 worker 上跑：把这条失败的 patch 撤掉
    _manifest(worker)
