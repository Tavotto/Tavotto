"""Artist family 能力层的契约（2026-08-21）。

Tavotto 对 matplotlib 的理解从「一条越来越长的 `isinstance` 表」改成「按
family 建模」之后，这里钉的是那套模型必须成立的几件事：

1. **族里的新类不用改代码就认得出**——`Patch` / `Collection` 的子类（含用户
   自己继承的）自动落进对应 family；
2. **能力按真实 getter 实况判，不按类名**——颜色映射中的 Collection 不给
   facecolor（`update_scalarmappable()` 会在下一次 draw 里原样覆盖回去，
   给了就是「界面说改了、画面没动」）；
3. **改了能还原**——override 是全量列表语义，撤销 = 把 (gid,prop) 从列表里
   拿掉，所以每一条新开放的属性都必须**逐字还原**，不是「setter 能跑」；
4. **旧 gid 一个都不能变**——`axes_i.scatter_j` / `axes_i.fill_j` /
   `axes_i.lines_j` 是已经发出去的名字，历史文档里有针对它们的 override；
5. **认不出来的 artist 不许静默消失**——要么进元素表（只开 visible/zorder），
   要么进 manifest 的 `unsupported` 诊断清单。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""
import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

SCRIPT_NAME = "fig_families.py"
ENTRY = "main"

#: 一个脚本出五张图，一次 build 全捕获（build 是这套用例里唯一慢的一步）。
#:   FamColl   Collection family：散点 / 映射散点 / fill_between / pcolormesh /
#:             contour / eventplot / hexbin
#:   FamPatch  Patch family：pie 的 Wedge、axhspan 的 Rectangle、Circle、
#:             stairs 的 StepPatch、ax.fill() 的 Polygon
#:   FamCont   容器：stem / bar / errorbar
#:   FamCustom 用户自定义子类 + 完全认不出来的 Artist
LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.lines import Line2D
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle, Rectangle


class MyLine(Line2D):
    """用户继承出来的曲线——family 抽象的意义就是它不用我们改一行代码。"""


class MyPatch(Rectangle):
    pass


class Doodad(Artist):
    """完全不在 matplotlib 体系里的自定义 Artist：不许让 instrument 崩，
    也不许让它从元素表里凭空消失。"""

    def draw(self, renderer):
        return None

    def get_window_extent(self, renderer=None):
        from matplotlib.transforms import Bbox
        return Bbox([[0.0, 0.0], [1.0, 1.0]])


def main():
    rng = np.random.RandomState(0)
    Z = rng.rand(8, 8)
    x = np.linspace(0.5, 6.0, 24)

    # ---- FamColl ----
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.scatter(x, np.sin(x), label="pts")                 # scatter_0 未映射
    ax.scatter(x, np.cos(x) + 3, c=x, label="mapped")     # scatter_1 映射
    ax.fill_between(x, -1.2, np.sin(x) - 1.0, alpha=0.3)  # fill_2
    ax.pcolormesh(np.linspace(7, 9, 9), np.linspace(0, 2, 9), Z)   # collections_3
    ax.contour(np.linspace(7, 9, 8), np.linspace(3, 5, 8), Z)      # collections_4
    ax.eventplot([[1.0, 2.0, 3.0]], lineoffsets=5.0, linelengths=0.6)  # linecoll_5
    # 标量映射的线组：**不算线组那一族**，走通用 collection（collections_6）
    mapped_lc = LineCollection([[(0.5, -2.0), (6.0, -2.0)], [(0.5, -1.6), (6.0, -1.6)]],
                               array=np.array([0.2, 0.8]), cmap="viridis")
    ax.add_collection(mapped_lc)
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.5, 6)
    ax.legend()
    fig.savefig("FamColl.pdf")

    # ---- FamPatch ----
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.pie([3, 4, 5])                                     # patches_0..2 Wedge
    ax.add_patch(Circle((0.6, 0.6), 0.15, facecolor="#B34700"))   # patches_3
    ax.add_patch(MyPatch((-0.9, -0.9), 0.3, 0.2, facecolor="#2A6F3C"))  # patches_4
    fig.savefig("FamPatch.pdf")

    # ---- FamStairs：StepPatch 是 PathPatch 的子类 ----
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.stairs(np.arange(1, 6), np.arange(6))              # patches_0
    ax.axhspan(0.5, 1.2, alpha=0.2)                       # patches_1 Rectangle
    fig.savefig("FamStairs.pdf")

    # ---- FamCont：容器 ----
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.stem([1.0, 2.0, 3.0], [1.0, 2.0, 1.5], label="stems")
    ax.bar([5.0, 6.0], [1.0, 2.0], label="bars")
    ax.errorbar([8.0, 9.0], [1.0, 1.5], yerr=0.2, label="err", capsize=3)
    ax.legend()
    fig.savefig("FamCont.pdf")

    # ---- FamCbar：色条轴的内部件一个都不许登记 ----
    fig, ax = plt.subplots(figsize=(3.4, 2.8))
    im = ax.imshow(rng.rand(8, 8), cmap="magma")
    cb = fig.colorbar(im, ax=ax, extend="both")
    cb.set_label("signal")
    fig.savefig("FamCbar.pdf")

    # ---- FamCustom ----
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.add_line(MyLine([0.0, 1.0], [0.0, 1.0], color="#123456"))
    ax.add_artist(Doodad())
    fig.savefig("FamCustom.pdf")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("family-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def hot(library):
    """一个常驻 worker：契约用例全部在它上面跑（apply → 读 manifest → 撤销）。"""
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    yield w
    pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not resp.get("warnings"), resp["warnings"]
    return resp["manifest"]


def _gids(man):
    return {e["gid"] for e in man["elements"]}


def _el(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里：{sorted(_gids(man))}"
    return hits[0]


def _fields(man, gid):
    return {f["prop"]: f for f in _el(man, gid)["editable"]}


# ---------------------------------------------------------------------------
# 1. 族覆盖：这些 API 的产物必须都进元素表
# ---------------------------------------------------------------------------
def test_collection_family_is_registered(hot):
    """散点之外的 Collection 从前整族看不见：pcolormesh 的 QuadMesh、contour 的
    ContourSet、eventplot 的 EventCollection 一个都进不了元素表。"""
    gids = _gids(_man(hot, "FamColl"))
    assert {"axes_0.scatter_0", "axes_0.scatter_1", "axes_0.fill_2"} <= gids, "旧 gid 必须原样还在"
    assert {"axes_0.collections_3", "axes_0.collections_4"} <= gids
    # EventCollection 是 LineCollection 的子类，走**线组**那一族（`color` 口径），
    # 不是通用 collection——两套 prop 名都已经发出去了，不能合并
    assert "axes_0.linecoll_5" in gids


def test_patch_family_is_registered(hot):
    """从前只认 Polygon / PathPatch，于是 pie 的扇形、axhspan 的色带、
    add_patch 的圆一个都选不中。"""
    gids = _gids(_man(hot, "FamPatch"))
    assert {f"axes_0.patches_{i}" for i in range(5)} <= gids
    gids = _gids(_man(hot, "FamStairs"))
    assert {"axes_0.patches_0", "axes_0.patches_1"} <= gids


def test_stem_is_one_series_not_three_loose_artists(hot):
    """`ax.stem()` 在用户眼里是一条系列。不做成容器的话 markerline 与 baseline
    是两条无名曲线、茎（LineCollection）干脆不出现。"""
    man = _man(hot, "FamCont")
    gids = _gids(man)
    assert "axes_0.stemseries_0" in gids
    props = set(_fields(man, "axes_0.stemseries_0"))
    assert {"color", "linewidth", "marker", "markersize", "visible"} <= props
    # baseline 仍以普通曲线的身份单独可编辑（它是零线，不属于这条系列）
    assert any(g.startswith("axes_0.lines_") for g in gids)


def test_bar_and_errorbar_containers_are_unchanged(hot):
    gids = _gids(_man(hot, "FamCont"))
    assert "axes_0.barseries_1" in gids
    assert "axes_0.errorbar_2" in gids


# ---------------------------------------------------------------------------
# 2. 能力按实况判，不按类名
# ---------------------------------------------------------------------------
def test_color_mapped_collections_do_not_advertise_facecolor(hot):
    """**这条是整个能力层的理由**。

    `scatter(x, y, c=z)` 与 `pcolormesh` 的 facecolors 每次 draw 由
    `Collection.update_scalarmappable()` 从数组重算——`set_facecolor` 在屏幕上
    一个像素都不会变（mpl 3.10.8 / 3.11.1 实测一致）。给它一个填充色控件，
    用户点了、界面显示改了、图纹丝不动，这比不给控件坏得多。
    """
    man = _man(hot, "FamColl")
    plain = _fields(man, "axes_0.scatter_0")
    mapped = _fields(man, "axes_0.scatter_1")
    mesh = _fields(man, "axes_0.collections_3")

    assert "facecolor" in plain and "cmap" not in plain
    assert "facecolor" not in mapped and {"cmap", "vmin", "vmax"} <= set(mapped)
    assert "facecolor" not in mesh and {"cmap", "vmin", "vmax"} <= set(mesh)


def test_every_collection_can_be_stroked(hot):
    """现在没有边 ≠ 加不上边：给 pcolormesh 加网格线是常见需求，
    等值线的线宽/颜色更是。"""
    man = _man(hot, "FamColl")
    for gid in ("axes_0.fill_2", "axes_0.collections_3", "axes_0.collections_4"):
        props = set(_fields(man, gid))
        assert {"edgecolor", "linewidth"} <= props, gid
    # 线组那一族对外叫 `color`（Line2D 口径），描边能力是同一件事、名字不同
    assert {"color", "linewidth"} <= set(_fields(man, "axes_0.linecoll_5"))


def test_mapped_line_collections_leave_the_linecoll_family(hot):
    """标量映射的 LineCollection 走**通用 collection**，登记与 dispatch 同一个判据。

    这是把两条判据分开写的必然结局（Codex 在 PR #48 上报的 P2）：登记那头按
    `get_array()` 把它放进 `collections_j`，`_cls_key` 却无条件回 `linecoll`
    ——于是元素表说它是通用 collection，检查器按线组给了 `color`，而
    `HANDLERS[("linecoll", …)]` 根本不在这个元素上，那个控件一个像素都改不动。
    判据现在只有 `overrides.is_linecoll_family` 一处。
    """
    man = _man(hot, "FamColl")
    gids = _gids(man)
    assert "axes_0.collections_6" in gids, \
        f"映射的线组没进通用 collection：{sorted(g for g in gids if 'coll' in g)}"
    assert "axes_0.linecoll_6" not in gids, "映射的线组被当成线组登记了"

    props = set(_fields(man, "axes_0.collections_6"))
    assert {"cmap", "vmin", "vmax"} <= props, f"映射的线组没拿到色图控件：{props}"
    assert "color" not in props, "映射的线组给了线组那套单值 color"

    # dispatch 真的落在通用族上：改一条通用族的 prop 必须无 warning 且生效
    resp = hot.override("FamColl", [{"gid": "axes_0.collections_6",
                                     "prop": "cmap", "value": "plasma"}])
    assert not (resp.get("warnings") or []), resp["warnings"]
    got = _fields(resp["manifest"], "axes_0.collections_6")["cmap"]["value"]
    assert got == "plasma", got
    hot.override("FamColl", [])


def test_colorbar_and_its_mappable_are_one_alias_group(hot):
    """色条的 cmap / vmin / vmax 与 mappable 自己那套是**同一份状态、两个 gid**。

    Codex 在 PR #48 上报的第二条 P2，实测复现得到两个症状（`imshow` + colorbar）：

    * 两条都设过、只撤掉 mappable 那条 → 还原写回脚本原样，色条那条「值没变」
      被跳过，热态回到 viridis 而全量重放是 magma；
    * 两条**全撤** → 后采的 originals 记的是「已经被另一条改过之后」的值，
      撤销停在**中间态**（实测 plasma），用户按了撤销、图还是花的。

    第二条尤其要命：这条重叠**不是本次新开的**（`("image","cmap")` 与
    `("colorbar","cmap")` 一直在同一个 AxesImage 上），Collection 族开放
    cmap 只是把它扩到了 pcolormesh / scatter(c=z)。
    """
    base = _man(hot, "FamCbar")
    img = next(g for g in _gids(base) if ".images_" in g)
    cbar = next(g for g in _gids(base) if g.endswith(".colorbar"))
    orig = _fields(base, img)["cmap"]["value"]

    # 两条都设：图元自己那条说了算（组内次序由 _rank 定死）
    man = _man(hot, "FamCbar", [
        {"gid": img, "prop": "cmap", "value": "plasma"},
        {"gid": cbar, "prop": "cmap", "value": "cividis"},
    ])
    assert _fields(man, img)["cmap"]["value"] == "plasma"

    # 只撤掉图元那条：色条那条必须重放，不能退回脚本原样
    man = _man(hot, "FamCbar", [{"gid": cbar, "prop": "cmap", "value": "cividis"}])
    assert _fields(man, img)["cmap"]["value"] == "cividis", \
        "撤掉图元那条把共享的 mappable 写回原样了，而色条那条被跳过没重放"

    # 全撤：必须逐字回到脚本原样，不是中间态
    man = _man(hot, "FamCbar")
    assert _fields(man, img)["cmap"]["value"] == orig, \
        "撤销停在中间态——广播端没有在动手之前采下组员的脚本原样"


def test_marker_replacement_stays_a_scatter_only_contract(hot):
    """`set_paths` 对散点是换 marker，对多边形集合是把用户的几何整个换掉
    ——那是改数据。所以 marker 只给 PathCollection。"""
    man = _man(hot, "FamColl")
    assert "marker" in _fields(man, "axes_0.scatter_0")
    for gid in ("axes_0.fill_2", "axes_0.collections_3", "axes_0.linecoll_5"):
        assert "marker" not in _fields(man, gid), gid


# ---------------------------------------------------------------------------
# 3. 自定义子类 / 未知 artist
# ---------------------------------------------------------------------------
def test_custom_subclass_inherits_family_support(hot):
    """`class MyPatch(Rectangle)` / `class MyLine(Line2D)`：family 抽象的价值
    就在这——matplotlib 明天多一个 Patch 子类，这里不用改一行。"""
    patch = _fields(_man(hot, "FamPatch"), "axes_0.patches_4")
    assert {"facecolor", "edgecolor", "linewidth", "alpha", "visible"} <= set(patch)
    line = _fields(_man(hot, "FamCustom"), "axes_0.lines_0")
    assert {"color", "linewidth", "linestyle", "marker"} <= set(line)


def test_unknown_artist_neither_crashes_nor_vanishes(hot):
    """认不出来的 Artist：图照画，元素表里认得出它是什么类，
    只开 visible / zorder——两者由 draw 的公共机制兑现，一定是真的。"""
    man = _man(hot, "FamCustom")
    hits = [e for e in man["elements"] if e["role"] == "artist"]
    assert hits, f"自定义 Artist 消失了：{sorted(_gids(man))}"
    props = {f["prop"] for f in hits[0]["editable"]}
    assert props == {"visible", "zorder"}, props
    assert "alpha" not in props, "alpha 要靠每个 artist 自己在 draw 里读，基类不保证"


def test_census_reports_what_is_not_in_the_element_table(hot):
    """诊断字段：真漏掉的东西要说得出类名，否则只剩用户一句「那块点不中」。

    容器消费掉的成员（茎、误差棒的横杠、柱）**不算漏**——它们由容器代表。
    """
    man = _man(hot, "FamCont")
    missing = {row["cls"] for row in man.get("unsupported", [])}
    assert not any("LineCollection" in c for c in missing), \
        f"茎与误差棒横杠已由容器代表，不该报成漏掉：{missing}"


def test_colorbar_axes_expose_only_the_colorbar(hot):
    """把 Collection / Patch 整族打开之后，最容易顺手漏进来的就是色条自己的
    内部件：色带（`cb.solids`，一个 QuadMesh）、分隔线（`cb.dividers`，一个
    LineCollection）、`extend` 的两个延伸三角（PathPatch）。

    它们**每次 `_draw_all()` 都被删掉重建**，登记它们等于在元素表里放几个随时
    换身份的幽灵条目，而且与色条代理重复。色条轴对外只有一个元素。
    """
    man = _man(hot, "FamCbar")
    cbar_axes = {e["gid"] for e in man["elements"] if e.get("is_colorbar")}
    assert cbar_axes, "这张图上没有色条轴？"
    for ax_gid in cbar_axes:
        extra = {g for g in _gids(man)
                 if g.startswith(f"{ax_gid}.")
                 and not g.startswith((f"{ax_gid}.colorbar", f"{ax_gid}.x",
                                       f"{ax_gid}.y"))}
        assert not extra, f"色条轴上漏出了内部件：{sorted(extra)}"
    # 也不该被普查报成「漏掉了」——普查一旦开始喊狼来了，真缺口就没人看了
    assert not [r for r in man.get("unsupported", []) if r["where"] in cbar_axes], \
        man.get("unsupported")


# ---------------------------------------------------------------------------
# 4. 还原（P0）：override 是全量列表，撤销 = 把条目拿掉
# ---------------------------------------------------------------------------
def _sample_value(field):
    """按字段类型挑一个**不同**的值；挑不出来（结构化类型）返回 None 跳过。"""
    kind, cur = field.get("type"), field.get("value")
    if kind == "color":
        return "#123456" if str(cur).lower() != "#123456" else "#654321"
    if kind == "bool":
        return not bool(cur)
    if kind == "enum":
        opts = [o for o in (field.get("options") or []) if o != cur]
        return opts[0] if opts else None
    if kind == "text":
        return f"{cur}-x"
    if kind == "number":
        if cur is None:
            return None
        lo, hi = field.get("min"), field.get("max")
        step = float(field.get("step") or 1.0)
        v = float(cur) + step
        if hi is not None and v > float(hi):
            v = float(cur) - step
        if lo is not None and v < float(lo):
            return None
        return round(v, 4)
    return None      # pair / rect / order / number_list：各有各的契约，不在本表


#: 族里通用的那些属性——能力层新开放的与原本就有的一起过一遍。
#: `fontsize` 在这张表里是**并行分支带回来的一条实测**（CompatBench 侧的
#: `_set_legend_fontsize`）：图例的 fontsize getter 回的是数值、setter 却只
#: 吃 Legend 自己那套重建路径，撤销那一刻才炸——与本分支修的 linestyle 是
#: 同一类 bug（getter 回的形状 ≠ setter 吃的形状）。放进通用还原用例，
#: 那条一次性修复就变成常设看护。
_FAMILY_PROPS = ("facecolor", "edgecolor", "linewidth", "linestyle", "hatch",
                 "fill", "alpha", "visible", "zorder", "size", "marker",
                 "markersize", "color", "cmap", "vmin", "vmax", "label",
                 "fontsize")


def _roundtrip_targets(man):
    for el in man["elements"]:
        if el["role"] in ("axes", "axes3d", "figure", "ticks", "ticklabel"):
            continue
        for f in el["editable"]:
            if f["prop"] in _FAMILY_PROPS:
                yield el["gid"], f


@pytest.mark.parametrize("stem", ["FamColl", "FamPatch", "FamStairs",
                                  "FamCont", "FamCbar", "FamCustom"])
def test_every_family_prop_restores_exactly(hot, stem):
    """改一条 → 撤销 → **逐字**回到原值。

    只测「setter 跑得通」是不够的：Tavotto 的 override 是全量列表语义，
    撤销靠 originals 表把原生值放回去。还原不回去 = 用户按了撤销、图没变，
    而且再也变不回来。数组类的原生值尤其容易踩坑（不 `.copy()` 的话
    originals 与 artist 指着同一个数组，setter 就地一改原值跟着变）。
    """
    base = _man(hot, stem)
    checked = 0
    for gid, field in _roundtrip_targets(base):
        value = _sample_value(field)
        if value is None:
            continue
        before = field["value"]
        _man(hot, stem, [{"gid": gid, "prop": field["prop"], "value": value}])
        after = _man(hot, stem)          # 空列表 = 全部撤销
        now = _fields(after, gid)[field["prop"]]["value"]
        assert now == before, f"{stem} {gid}.{field['prop']}：{before!r} → 撤销后 {now!r}"
        checked += 1
    assert checked >= 5, f"{stem} 上只测到 {checked} 条属性，覆盖太薄"


@pytest.mark.parametrize("stem,gid,prop,value", [
    # 新开放的族属性里挑几条**必须看得见**变化的，防止「还原测试全绿但其实
    # 什么都没改」——两条断言合起来才叫可编辑
    ("FamPatch", "axes_0.patches_0", "facecolor", "#123456"),   # pie 的扇形
    ("FamPatch", "axes_0.patches_3", "hatch", "//"),            # Circle 的花纹
    ("FamColl", "axes_0.collections_3", "edgecolor", "#123456"),  # pcolormesh 网格线
    ("FamColl", "axes_0.collections_4", "cmap", "plasma"),      # contour 的色图
    ("FamColl", "axes_0.linecoll_5", "linewidth", 3.0),         # eventplot 线宽
    ("FamCont", "axes_0.stemseries_0", "color", "#123456"),     # 茎叶系列整体
])
def test_representative_family_edits_actually_change_the_manifest(hot, stem, gid, prop, value):
    before = _fields(_man(hot, stem), gid)[prop]["value"]
    after = _fields(_man(hot, stem, [{"gid": gid, "prop": prop, "value": value}]),
                    gid)[prop]["value"]
    assert after != before, f"{gid}.{prop} 改了却没反映到 manifest"
    back = _fields(_man(hot, stem), gid)[prop]["value"]
    assert back == before


# ---------------------------------------------------------------------------
# 5. 向后兼容与稳定性
# ---------------------------------------------------------------------------
def test_consumed_markerline_keeps_its_old_gid_as_an_alias(hot):
    """markerline 从前是 `axes_0.lines_0`，现在归 stem 容器。历史文档里针对它
    的 override 必须还落在同一个 artist 上——别名只进 index、不进元素表，
    所以界面上不会多出条目，旧文档也不会变成孤儿。"""
    resp = hot.override("FamCont", [{"gid": "axes_0.lines_0", "prop": "color",
                                     "value": "#123456"}])
    assert not resp.get("warnings"), resp["warnings"]
    hot.override("FamCont", [])


def test_removing_a_legacy_alias_override_replays_the_series(hot):
    """旧 gid 别名与系列指着**同一个 artist**——撤掉别名那条，系列那条必须重放。

    Codex 在 PR #48 上报的 P2，实测复现得到（本用例就是那个复现）：文档里同时
    留着历史的 `axes_0.lines_0.color`（markerline 容器化之前的名字）与
    `axes_0.stemseries_0.color`。只撤掉前者时，还原把 markerline 写回脚本原样，
    而系列那条「值没变」于是走了跳过的捷径——**茎是新颜色、marker 退回原色**，
    全量重放却两者都是新颜色。热态 ≠ 重放，而写回自检（`_compare_manifests`）
    只比几何、看不见颜色，坏状态会直接写进用户的原件。

    修法是把别名 gid 也算进别名组（`ALIAS_GROUPS[("stem_series", …)]` +
    `apply` 的反查表覆盖 index-only 别名），与柱形系列 / 图例字号同一套机制。
    """
    # 两条都在：窄的（别名）排在广播的（系列）之后 → marker 归别名那条管
    man = _man(hot, "FamCont", [
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#ff0000"},
        {"gid": "axes_0.stemseries_0", "prop": "color", "value": "#0000ff"},
    ])
    # 系列的 color 字段读的正是 markerline（见 manifest._stem_fields 的 probe）
    assert _fields(man, "axes_0.stemseries_0")["color"]["value"].lower() == "#ff0000"

    # 只撤掉历史那条，系列那条一个字节没变
    man = _man(hot, "FamCont", [
        {"gid": "axes_0.stemseries_0", "prop": "color", "value": "#0000ff"},
    ])
    assert _fields(man, "axes_0.stemseries_0")["color"]["value"].lower() == "#0000ff", \
        "marker 退回了脚本原色——别名的还原把系列的值盖掉了，而系列被跳过没重放"

    # 全撤：回脚本原样
    hot.override("FamCont", [])


def test_instrument_does_not_mutate_the_figure(hot):
    """零 patch 连着渲染两次，manifest 必须**逐位相同**。

    读取 editable 时顺手调 setter「规范化」一下，会让第二次渲染与第一次不同
    ——热会话与全量重放于是分岔，而写回自检正是比这两者。
    """
    for stem in ("FamColl", "FamPatch", "FamCont", "FamCbar", "FamCustom"):
        first = _man(hot, stem)
        second = _man(hot, stem)
        assert first == second, stem
