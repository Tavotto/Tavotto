"""引擎的五条**结构性不变式**（2026-08-21，1.0 稳定化）。

这些不是「某个 bug 的回归用例」，是「这一类 bug 不该再出现」的约束。1.0 前
的 review 已经不再主要产出独立 bug，而是同几条不变式反复被踩——把它们写成
测试，是让「修一个又冒一个」收敛的唯一办法。

    1. **能力真实**  宣称可编辑 → handler 在 → dispatch 得到 → 画面真的变
    2. **逐字还原**  改一条 → 撤销 → **像素与 manifest 都逐位回到原样**
    3. **热态 == 重放**  含**删除**的序列，热会话状态必须等于全量重放
    4. **不许静默消失**  登记范围内的 artist → elements XOR unsupported
    5. **单一权威**  family / 能力 / gid 前缀 / 别名目标各只有一处判据

与既有两套的分工：

* `tests/acceptance/` 比「Tavotto 今天 vs 昨天」，`tests/compat/`（CompatBench）
  比「原生 matplotlib vs Tavotto」。这里两者都不是——这里比的是**引擎自己
  说的话和它自己做的事对不对得上**，一张图就够，不需要 150 个 case。
* `test_equivalence_matrix.py` 已经钉了四路等价，但它的 patch 组全是**累加
  的**，判据又只有 `_compare_manifests`（只比几何）。本文件补的正是那两个
  缺口：**删除**语义，以及**像素 + 全量 manifest** 这把更严的尺子。虚线的
  dash 被放大 1.5 倍不动任何包围盒——几何尺量不到，像素量得到。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里，
不变式 4 / 5 需要引擎内部视角，走 `tests/support/engine_invariant_probe.py`
子进程。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "tests", "support", "engine_invariant_probe.py")

SCRIPT_NAME = "fig_invariants.py"
ENTRY = "main"
STEMS = ("InvMix", "InvCont", "InvCbar")

#: 一个脚本出三张图，一次 build 全捕获（build 是这套用例里唯一慢的一步）。
#: 每张图都刻意做得**元素互相重叠**——`zorder` 想被验出效果就得有东西挡；
#: 每张图都带图例——`label` 想被验出效果就得有地方显形。
LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Arc, Circle, Rectangle


def main():
    rng = np.random.RandomState(0)
    x = np.linspace(0.5, 6.0, 24)

    # ---- InvMix：Collection 族 + Patch 族 + 图例 ----
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    # marker 用 `^` / `s`：默认就是 `o` 的话，「换成 o」测不出任何变化
    ax.scatter(x, np.sin(x) + 2.0, marker="^", s=60, label="pts")
    ax.scatter(x, np.cos(x) + 2.0, c=x, marker="s", s=60, label="mapped")
    ax.fill_between(x, 0.6, np.sin(x) + 1.2, alpha=0.35, label="band")
    # 3×3 的**大格子**：8×8 那种一格才十来个像素，网格线的线型与花纹在
    # 这个尺寸下画不出可分辨的差别，于是「改了没反应」全是夹具自己挡的
    ax.pcolormesh(np.linspace(0.5, 6.0, 4), np.linspace(-1.9, -0.4, 4), rng.rand(3, 3))
    ax.contour(np.linspace(0.5, 6.0, 8), np.linspace(3.2, 4.4, 8), rng.rand(8, 8))
    ax.eventplot([[1.0, 2.0, 3.0]], lineoffsets=0.2, linelengths=0.5)
    # 映射的线组：颜色由 colormap 每次 draw 重算，**不算线组那一族**；
    # `linestyles="--"` 是有意的（Collection 的线型反查要用未缩放规格）
    ax.add_collection(LineCollection(
        [[(0.5, -2.2), (6.0, -2.2)], [(0.5, -2.0), (6.0, -2.0)]],
        array=np.array([0.2, 0.8]), cmap="viridis", linestyles="--",
        clim=(-0.5, 1.5), linewidths=3))
    # 数组在、映射**不在**：脚本自己把颜色写死了，于是两个通道都没在映射
    # （matplotlib 的 `_set_mappable_flags` 只在 facecolor 不是 'none'、或者
    # edgecolor 没被显式设过时才置位）。给它 cmap 控件 = 三个死开关。
    ax.add_collection(LineCollection(
        [[(0.5, -2.4), (6.0, -2.4)]], colors="#804000",
        array=np.array([0.5]), cmap="viridis", linewidths=3))
    # 三个形状**叠在一起**：zorder 想验出效果，必须有东西可挡
    ax.add_patch(Rectangle((1.0, -0.2), 2.2, 0.9, facecolor="#B34700"))
    ax.add_patch(Circle((2.0, 0.25), 0.5, facecolor="#2A6F3C"))
    ax.add_patch(Arc((2.0, 0.25), 1.4, 1.4, theta1=0, theta2=270))
    ax.set_xlim(0, 6.5)
    ax.set_ylim(-2.6, 4.6)
    ax.legend(loc="upper right")
    fig.savefig("InvMix.pdf")

    # ---- InvCont：三种容器 + 被消费成员的旧 gid ----
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    ax.stem([1.0, 2.0, 3.0], [1.0, 2.0, 1.5], linefmt="--", label="stems")
    ax.bar([5.0, 6.0], [1.6, 2.2], label="bars")
    ax.errorbar([8.0, 9.0], [1.0, 1.5], yerr=0.25, label="err", capsize=3)
    ax.legend(loc="upper left")
    fig.savefig("InvCont.pdf")

    # ---- InvCbar：图像 + 色条（两个 gid 一份状态） ----
    fig, ax = plt.subplots(figsize=(3.6, 2.9))
    # 512×512 画进一个两英寸的轴 = 真正的**降采样**，`interpolation` 才有
    # 意义：放大时 matplotlib 的 antialiased 本来就退化成 nearest，拿
    # 8×8 去验这条属性，测出来的只会是「改了没反应」
    im = ax.imshow(rng.rand(512, 512), cmap="magma")
    cb = fig.colorbar(im, ax=ax, extend="both")
    cb.set_label("signal")
    fig.savefig("InvCbar.pdf")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("invariant-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    w.ensure_built()
    return w


#: **每组不变式各起一个 worker**，不共用。共用过一次，代价立刻显形：能力普查
#: 会把每一条 prop 都改一遍再撤回，它留下的漂移成了「逐字还原」那组的**基准**
#: ——于是还原用例对着一个已经跑偏的原点比，全绿，而真正的漂移（图例重建）
#: 一声不响地藏了过去。测试之间的状态泄漏在这套用例里不是洁癖问题，是判据
#: 问题：这几条不变式比的全是「跟脚本原样一不一样」。
@pytest.fixture(scope="module")
def hot(library):
    w = _worker(library)
    yield w
    pool.discard(w)


@pytest.fixture(scope="module")
def hot_restore(library):
    w = _worker(library)
    yield w
    pool.discard(w)


@pytest.fixture(scope="module")
def hot_replay(library):
    w = _worker(library)
    yield w
    pool.discard(w)


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------
def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(worker, stem, patches, tag):
    """这一组 patch 画出来的样子（sha1）。

    `preview_png` 是**状态中立**的：应用自己那组 patch 出图后把 worker 恢复
    原状。所以它可以在不动会话状态的前提下回答「这一改，画面变不变」——
    而那正是「宣称可编辑」的唯一硬判据。实测同一组 patch 连画三次逐字节相同
    （6ms 一张），所以拿字节比是可靠的。
    """
    path = worker.preview_png(stem, list(patches), 380, tag)
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _fields(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return {f["prop"]: f for f in hits[0]["editable"]}


def _sample_value(field):
    """按字段类型挑一个**不同**的值；挑不出来（结构化类型）返回 None。"""
    kind, cur = field.get("type"), field.get("value")
    if kind == "color":
        return "#123456" if str(cur).lower() != "#123456" else "#654321"
    if kind == "bool":
        return not bool(cur)
    if kind == "enum":
        opts = [o for o in (field.get("options") or []) if o != cur]
        return opts[0] if opts else None
    if kind == "text":
        return f"{cur}x" if cur is not None else "x"
    if kind == "number":
        if cur is None:
            return None
        lo, hi = field.get("min"), field.get("max")
        step = float(field.get("step") or 1.0)
        # **一个 step 往往看不出来**：`vmin` 的 step 是值域的 1%，挪 1% 之后
        # 256 级色图上多半还是同一个颜色，于是「改了没反应」被误判成假支持。
        # 挑一个明显的差（有界的按值域 1/10，无界的按 5 个 step），越界再退回
        # 一个 step——这是**采样**的取舍，不是断言的松动。
        delta = (float(hi) - float(lo)) / 10.0 if (lo is not None and hi is not None) \
            else step * 5.0
        delta = max(abs(delta), abs(step))
        for cand in (float(cur) + delta, float(cur) - delta,
                     float(cur) + step, float(cur) - step):
            if (lo is None or cand >= float(lo)) and (hi is None or cand <= float(hi)):
                return round(cand, 4)
        return None
    return None      # pair / rect / order / number_list：各有各的契约


def _same_value(field, got, want) -> bool:
    if field.get("type") == "number":
        tol = max(abs(float(field.get("step") or 1.0)) / 2.0, 1e-6)
        return got is not None and abs(float(got) - float(want)) <= tol
    if field.get("type") == "color":
        return str(got).lower() == str(want).lower()
    return got == want


def _patch_for(gid, field, advertised):
    """(使能项 patches, 完整 patch 列表) —— **能力真实与逐字还原共用同一份**。

    两条扫描必须发同一组 patch。分开写过一版，代价立刻显形：还原那条只发
    单条 prop，于是「先把背景框打开、再改背景色」这个组合永远轮不到它，而
    真正的还原缺陷（第一条 bbox_* 现建出来的框摘不掉）就藏在那个组合里——
    **另一道防线恰好挡住了它**，两条用例都绿。
    """
    prop = field["prop"]
    value = _SAMPLE_OVERRIDE.get(prop, _sample_value(field))
    if value is None:
        return None, None
    enablers = [{"gid": gid, "prop": ep, "value": ev}
                for ep, ev in _ENABLERS.get(prop, ())
                if ep != prop and ep in advertised[gid]]
    return enablers, enablers + [{"gid": gid, "prop": prop, "value": value}]


def _editable_targets(man):
    """(gid, field) —— 跳过整块结构性的角色，它们各有各的专用用例。"""
    for el in man["elements"]:
        if el["role"] in ("figure", "axes", "axes3d", "ticks", "ticklabel"):
            continue
        for f in el["editable"]:
            yield el["gid"], f


# ---------------------------------------------------------------------------
# 不变式 1：能力真实（capability truthfulness）
# ---------------------------------------------------------------------------
#: **画面上看不出来是正常的**那些 prop —— 这张表必须显式、必须写清理由。
#:
#: 判据是「在这张图上，改它不动像素**是对的**」，不是「它改不动所以放过」。
#: 往里加一条之前先问：是这个 prop 天生不影响绘制，还是这张图恰好挡住了？
#: 后者要改图，不是加豁免。**没登记的一律按「必须动像素」处理**——忘了登记
#: 会让一个假支持悄悄溜过去，而那正是这条不变式要挡的东西。
_NON_VISUAL_PROPS = {
    "label": "只在图例里显形；元素自己的图上不画（图例项文字是另一个元素）",
    "zorder": "只改绘制次序；被它盖住/露出的是别的元素，本元素的像素可以不变",
}

#: **使能项**：这些 prop 要另一条 prop 开着才看得见，缺了它就是「改了没反应」。
#:
#: 这不是豁免——豁免是「它本来就不画」，使能是「它画在一个当前关着的通道上」。
#: 两者的区别在于**能不能被验**：加上使能项之后画面必须变，验不出来照样红。
#: 这也正是 matplotlib 自己的模型：`facecolor` 只在 `fill` 开着时显形，
#: 花纹画在边色上，`markersize` 要先有 marker——`Arc` 那条被误判成「画不出面」
#: 的旧结论，本质就是把「使能项没开」看成了「能力不存在」。
#:
#: 值是「(同一个元素上的 prop, 值)」，**只有该元素也宣称了那条 prop 时才附加**。
_ENABLERS: dict[str, tuple[tuple[str, object], ...]] = {
    # bbox 要**同时**有边或面才看得见：合成默认值是 lw=0 + 白底白面
    "bbox_facecolor": (("bbox_visible", True),),
    "bbox_edgecolor": (("bbox_visible", True), ("bbox_linewidth", 2.0)),
    "bbox_linewidth": (("bbox_visible", True), ("bbox_edgecolor", "#ff00ff")),
    "bbox_alpha": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    "bbox_pad": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    "bbox_rounded": (("bbox_visible", True), ("bbox_facecolor", "#ff00ff")),
    # `bbox_visible` 是这一组的**关**，不是开：`_bbox_handler` 的 setter「按需
    # 建 patch」，所以随便改一条 bbox_* 背景框就出现了。从「已经有框」的状态
    # 把它关掉才是这个开关真正管的事——`_SAMPLE_OVERRIDE` 把采样值钉成 False。
    "bbox_visible": (("bbox_facecolor", "#ff00ff"),),
    "stroke_color": (("stroke_enabled", True), ("stroke_width", 2.0)),
    "stroke_width": (("stroke_enabled", True), ("stroke_color", "#ff00ff")),
    # 花纹画在**边色**上；线宽 / 线型同样要先有一条看得见的边
    "hatch": (("edgecolor", "#ff00ff"), ("linewidth", 1.0)),
    "linewidth": (("edgecolor", "#ff00ff"),),
    "linestyle": (("edgecolor", "#ff00ff"), ("linewidth", 1.5)),
    "facecolor": (("fill", True),),
    "markersize": (("marker", "o"),),
    "markerfacecolor": (("marker", "o"), ("markersize", 8.0)),
    "markeredgecolor": (("marker", "o"), ("markersize", 8.0)),
    "title_fontsize": (("title", "T"),),
    "linespacing": (("text", "line one\nline two"),),
    "ha": (("text", "line one\nline two"),),
    "va": (("text", "line one\nline two"),),
}

#: 按 **(role, prop)** 划的豁免：同一条 prop 在不同容器里未必都画得出来。
#: `ha` / `va` 改的是文字相对**自身锚点**的对齐——标题、轴标签的锚点是自己的
#: 位置，改它文字就移动；而图例项的锚点由 HPacker 的布局定死，对齐改了也
#: 挪不动（实测 `axes_0.title.ha` 变、`legend.texts_j.ha` 不变）。这类差别
#: 只能按角色写，写成全局豁免就等于把标题那半也放过了。
#: 采样值不按 `_sample_value` 推、而是钉死的那几条（配合使能项才有意义）。
_SAMPLE_OVERRIDE = {"bbox_visible": False}

_NON_VISUAL_BY_ROLE = {
    ("legend_text", "ha"): "图例项的位置由 HPacker 布局定，对齐挪不动它",
    ("legend_text", "va"): "同上",
}


@pytest.mark.parametrize("stem", STEMS)
def test_capability_truthfulness(hot, stem):
    """宣称可编辑的每一条，都必须**真的改得动**。

    四步，缺一条就是「界面说改了、画面没动」：

        宣称 → handler 在（dispatch 无 warning）
             → manifest 读回来就是刚写进去的那个值
             → **画出来的像素真的变了**（除非在 `_NON_VISUAL_PROPS` 里）

    前三步只证明「状态设进去了」。历史上出问题的恰恰是第四步：颜色映射中的
    Collection 的 facecolor（`update_scalarmappable()` 下一帧原样覆盖回去）、
    没有面的 Collection 的 hatch、映射线组的 `color`——状态全都设得进去，
    getter 也照回，画面纹丝不动。那比「不给这个控件」坏得多：用户改了、
    以为改了、把图交出去了。
    """
    base = _man(hot, stem)
    base_png = _png(hot, stem, [], "base")
    advertised = {el["gid"]: {f["prop"] for f in el["editable"]}
                  for el in base["elements"]}
    role_of = {el["gid"]: el["role"] for el in base["elements"]}
    checked, invisible = 0, []

    for gid, field in _editable_targets(base):
        prop = field["prop"]
        enablers, patch = _patch_for(gid, field, advertised)
        if patch is None:
            continue
        value = patch[-1]["value"]

        # ① + ② dispatch 得到，且 manifest 读回来就是写进去的那个
        resp = hot.override(stem, patch)
        assert not (resp.get("warnings") or []), \
            f"{stem} {gid}.{prop} = {value!r} 报了 warning：{resp['warnings']}"
        got = _fields(resp["manifest"], gid)[prop]["value"]
        assert _same_value(field, got, value), \
            f"{stem} {gid}.{prop}：写进去 {value!r}，manifest 读回来 {got!r}"

        # ③ 画面真的变了。带了使能项的，基准也要是「只开使能项」那张图——
        # 否则比出来的差是使能项造成的，这一条 prop 照样可以是死的。
        if prop not in _NON_VISUAL_PROPS and (role_of[gid], prop) not in _NON_VISUAL_BY_ROLE:
            ref = base_png if not enablers else _png(
                hot, stem, enablers, f"{gid}.{prop}.enable")
            if _png(hot, stem, patch, f"{gid}.{prop}") == ref:
                invisible.append(f"{gid}.{prop} = {value!r}")
        checked += 1

    _man(hot, stem)      # 全撤，别把状态留给下一个用例
    assert checked >= 20, f"{stem} 上只扫到 {checked} 条属性，覆盖太薄"
    assert not invisible, (
        f"{stem}：这些属性宣称可编辑、状态也设进去了，但画出来一个像素都没变——"
        f"要么砍掉这个控件，要么按真实能力建模，要么给 _NON_VISUAL_PROPS 补一条"
        f"写得出理由的豁免：\n  " + "\n  ".join(sorted(invisible)))


#: 枚举里那些**故意不可回灌**的值——它们是显示占位，不是可设的值。
_ENUM_PLACEHOLDERS = {
    "original",   # 散点 marker：「脚本原始路径」，还原用，不是一个 marker 名
    "custom",     # 箭头样式：识别不出的自定义样式，选它 = 不动
}


@pytest.mark.parametrize("stem", STEMS)
def test_every_enum_option_can_actually_be_applied(hot, stem):
    """下拉框里列出来的每一个选项，**选了都得能用**。

    不变式 1 只验「挑一个不同的值」能不能落地，验不到第 3、第 5 个选项。而
    枚举的有效值**随 matplotlib 版本变**：`interpolation` 的 `"auto"` 是 3.9
    才加的，我们的**最低支持运行时 3.8.4 上根本不存在**——界面照样把它列出来,
    用户一点，`set_interpolation` 抛 ValueError → 收成一条 warning →
    **而一条 warning 就阻断写回**，提示还与真实原因毫不相干。

    这个缺口是把 CompatBench 的最低运行时那一档接进不变式扫描之后当场逮到的。
    修法不是把 `"auto"` 从表里删掉（那样 3.10+ 就少一个能用的档位），是**有效
    值一律问 matplotlib 要**（`_interpolation_options`）。本用例是它的通用形态：
    **凡是我们列出来的，就得是这一版 matplotlib 认的**。

    只验「设得进去、不报 warning」，不验像素——一个枚举里多数选项之间的差别
    本来就可能小到一张图上看不出来，那是不变式 1 该管的事（它验的是这条 prop
    整体有没有效果）。
    """
    base = _man(hot, stem)
    rejected = []
    for gid, field in _editable_targets(base):
        if field.get("type") != "enum":
            continue
        for opt in field.get("options") or []:
            if opt in _ENUM_PLACEHOLDERS:
                continue
            resp = hot.override(stem, [{"gid": gid, "prop": field["prop"], "value": opt}])
            if resp.get("warnings"):
                rejected.append(f"{gid}.{field['prop']} = {opt!r} → {resp['warnings'][0]}")
    _man(hot, stem)
    assert not rejected, (
        f"{stem}：这些选项列在界面上，选了却报错——而 warning 一条就阻断写回。"
        f"有效值要问 matplotlib 要，不要写死一张随版本漂移的表：\n  "
        + "\n  ".join(rejected))


def test_colormap_controls_appear_only_while_the_mapping_is_live(hot):
    """cmap / vmin / vmax 只在**映射此刻真的在决定颜色**时才出现。

    「有数组」不等于「在映射」。matplotlib 的 `_set_mappable_flags()` 只在
    facecolor 不是 `'none'`、或者 edgecolor 没被显式设过时才置位。两种情况
    会让数组在、映射不在（实测两条都让 `set_cmap` 改动 **0** 个像素）：

      1. 脚本自己写死了颜色 —— `LineCollection(..., colors=..., array=z)`；
      2. **用户设过我们自己开放的 `edgecolor`** —— 映射的线组被设了边色之后
         进入同一个状态。

    第 2 条是第 1 条的动态版本，而且是我们自己造成的：`edgecolor` 这个控件
    确实有效（它真的改颜色），但它一生效，同一个元素上的三个色图控件就变成
    死的。不摘掉的话，用户会对着三个「点了没反应」的下拉框琢磨半天。撤掉
    边色 override 之后它们必须自己回来。

    能力真实那条扫的是单个 prop，看不见这种**组合态**——所以单列一条。
    """
    base = _man(hot, "InvMix")
    dead = _fields(base, "axes_0.collections_7")      # 脚本写死颜色的那条
    assert "cmap" not in dead, "数组在、映射不在，却给了色图控件"
    assert {"edgecolor", "linewidth"} <= set(dead), "边色反而该给——颜色归用户了"

    live_gid = "axes_0.collections_6"                  # 真的在映射的那条
    assert {"cmap", "vmin", "vmax"} <= set(_fields(base, live_gid))

    # 设了边色之后映射就断了，三个色图控件必须跟着消失
    man = _man(hot, "InvMix", [{"gid": live_gid, "prop": "edgecolor", "value": "#123456"}])
    after = set(_fields(man, live_gid))
    assert not ({"cmap", "vmin", "vmax"} & after), \
        f"边色 override 生效期间还摆着色图控件，而它们此刻一个像素都改不动：{sorted(after)}"

    # 撤掉就回来（`_get_coll_edgecolor` 回灌的是 `_original_edgecolor`）
    assert {"cmap", "vmin", "vmax"} <= set(_fields(_man(hot, "InvMix"), live_gid))


# ---------------------------------------------------------------------------
# 不变式 2：逐字还原（exact restore）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stem", STEMS)
def test_exact_restore_is_pixel_and_manifest_identical(hot_restore, stem):
    """改一条 → 撤销 → **像素与 manifest 都逐位回到原样**。

    只比显示字符串是不够的，这条不变式的价值全在这儿：线组的 dash 被
    `set_linestyle()` 二次缩放之后，显示值可能还是 `"--"`，而画出来的虚线
    一次比一次疏（实测每撤销一次 ×1.5）。**下游没有任何门禁拦得住**——写回
    自检的 `_compare_manifests` 只比几何，dash 变了不动任何包围盒；四路等价
    矩阵用的是同一个比较器；`apply` 也不报 warning，因为 setter 没抛。

    像素比是这里唯一量得到那个偏差的尺子。两把尺子一起上：manifest 抓显示层
    的漂移，像素抓画面层的漂移，各能抓到对方漏掉的一半。
    """
    base = _man(hot_restore, stem)
    base_png = _png(hot_restore, stem, [], "restore-base")
    advertised = {el["gid"]: {f["prop"] for f in el["editable"]}
                  for el in base["elements"]}
    drifted, checked = [], 0

    for gid, field in _editable_targets(base):
        prop = field["prop"]
        if prop in _LEGEND_REBUILD_PROPS:
            continue        # 已知缺陷，单独由下面那条用例钉着
        _enablers, patch = _patch_for(gid, field, advertised)
        if patch is None:
            continue
        # **带上使能项**：还原要能把使能项造成的副作用也还回去。第一条
        # bbox_* 会**现建**一个背景框——只发单条 prop 的话，扫描顺序恰好
        # 先开了框，后面每条都落在「框已存在」的分支上，那个缺陷永远碰不到。
        hot_restore.override(stem, patch)
        after = _man(hot_restore, stem)                    # 空列表 = 全量撤销
        if after != base:
            diff = [e["gid"] for e in after["elements"]
                    if e not in base["elements"]]
            drifted.append(f"{gid}.{prop}（+{[e['prop'] for e in _enablers]}）"
                           f" → manifest 变了：{diff[:4]}")
        elif _png(hot_restore, stem, [], f"restore-{gid}.{prop}") != base_png:
            drifted.append(f"{gid}.{prop} → manifest 一样但**画面**变了")
        checked += 1

    assert checked >= 20, f"{stem} 上只扫到 {checked} 条属性，覆盖太薄"
    assert not drifted, (
        f"{stem}：撤销之后没有逐字回到脚本原样（getter 回的形状 ≠ setter 吃的"
        f"形状，是这类 bug 的通用成因）：\n  " + "\n  ".join(drifted))


def test_undoing_a_background_edit_removes_the_box_it_created(hot_restore):
    """只改「文字背景色」再撤销，**框要跟着消失**。

    背景框那六条 prop 写的是**同一个 patch**，而那个 patch 可能是被第一条
    override 现建出来的（`_bbox_ensure`，产品约定就是「首次改任何背景属性即
    出现背景框」）。老实现撤销时把值写回默认、却把框留下了：

        文字本来没有背景框 → 只改背景色 → 撤销 →
        底色回去了，**框还在**，`bbox_visible` 从 False 变成 True 且回不去

    两层坑，第二层才是真正难查的那个：

      1. 还原要能表达「本来就没有框」——用默认值表达不了（有一个白色的框，
         与没有框，数值上一模一样），所以 getter 回哨兵 `_NO_BBOX`；
      2. **`originals` 的采样时机靠不住**。六条 prop 里谁先被应用谁就把框建
         出来了，后一条采到的「脚本原样」已经是「框存在之后」的值。所以摘框
         的判据是 `_bbox_ensure` 留下的记号，不是 `orig is _NO_BBOX`。
         这与 ALIAS_GROUPS 那条「广播端要在动手之前替组员采原样」是同一个坑。

    之所以一直没被扫出来：扫描顺序恰好先把 `bbox_visible` 设成 True 建了框，
    后面每一条都落在「框已存在」的分支上——**另一道防线恰好挡住了它**。
    现在两条扫描共用 `_patch_for`，发的是同一组 patch，这个组合躲不掉了。
    """
    gid = "axes_0.legend.texts_0"
    base = _fields(_man(hot_restore, "InvCont"), gid)
    assert base["bbox_visible"]["value"] is False, "夹具里这条文字本来就不该有框"

    # 只改背景色（**不碰 bbox_visible**）——产品约定：框会因此出现
    on = _fields(_man(hot_restore, "InvCont",
                      [{"gid": gid, "prop": "bbox_facecolor", "value": "#123456"}]), gid)
    assert on["bbox_visible"]["value"] is True, "改了背景色却没出现框？产品约定变了"

    back = _fields(_man(hot_restore, "InvCont"), gid)
    assert back["bbox_visible"]["value"] is False, "撤销之后框还在——它再也关不掉了"
    assert back["bbox_facecolor"]["value"] == base["bbox_facecolor"]["value"]

    # 组里还有别的生效时**不许**把框摘掉：只撤 pad，背景色还在
    two = [{"gid": gid, "prop": "bbox_facecolor", "value": "#123456"},
           {"gid": gid, "prop": "bbox_pad", "value": 0.8}]
    _man(hot_restore, "InvCont", two)
    left = _fields(_man(hot_restore, "InvCont", two[:1]), gid)
    assert left["bbox_visible"]["value"] is True, "还有一条背景 override 生效，框不该被摘掉"
    assert left["bbox_facecolor"]["value"].lower() == "#123456"
    assert left["bbox_pad"]["value"] == base["bbox_pad"]["value"], "撤掉的那条没写回默认"
    _man(hot_restore, "InvCont")


#: **已知缺陷**：图例的「重建型」prop 撤销之后回不到脚本原样。
#:
#: 它们的 setter 靠 `Legend._init_legend_box(handles, labels)` 让 matplotlib
#: 重排整个图例盒，而**那个操作不是幂等的、也不是恒等的**（实测 3.10.8）：
#:
#:   * 喂回 `leg.legend_handles` 时，误差棒那条图例的 handle 从
#:     `LineCollection` 变成 `Line2D`——图例上的误差棒示意消失，只剩一条线
#:     （改 ncol=1→2→1 之后，图例区域 393 个像素与原样不同，而**包围盒一个
#:     像素没动**）；
#:   * 喂回 `ax.get_legend_handles_labels()` 那份原始容器倒是幂等了
#:     （连做三次结果不变），但**第一次就把图例盒撑高了 21px**，也不是恒等。
#:
#: 两条路都回不去，说明这不是「喂错了 handles」，是 `_init_legend_box` 本身
#: 复现不出 `Legend.__init__` 当初那次布局。真修法是让撤销**还原保存下来的
#: `_legend_box`**、并把这五条 prop 建模成互相覆盖的一组——那是一次图例重建
#: 路径的改造，1.0 稳定化期刻意不做（见 docs/1.0-release-readiness.md 的
#: Post-1.0 backlog）。
#:
#: **豁免不等于不管**：`test_legend_rebuild_drift_stays_where_it_is` 把这个
#: 缺陷的边界钉死——只许发生在图例上、只许改像素不许改几何。它一旦扩大，
#: 那条用例会红。
_LEGEND_REBUILD_PROPS = frozenset({
    "ncol", "borderpad", "labelspacing", "handlelength", "entry_order"})


@pytest.mark.parametrize("stem", ["InvCont"])
def test_legend_rebuild_drift_stays_where_it_is(hot_restore, stem):
    """已知缺陷的**边界**：图例重建的漂移只许留在图例里、只许是像素级。

    这条不是「证明它是对的」，是「证明它没有变得更坏」。三条边界：

      1. 撤销之后 **manifest 一个字节都不变**——所以它不会污染写回自检、
         不会让四路等价矩阵报假分歧、不会改任何元素的几何；
      2. 漂移**收敛**：重建两次之后再重建，画面不再继续变（不是复利的）；
      3. 出问题的确实只有图例那几条 prop——别的 prop 撤销后画面逐位回原样，
         那由上面那条用例保证。

    第 1 条是它今天还能被划成 P2 的全部理由：写回落盘的是全新 worker 重放
    出来的那份（= 脚本原样），用户屏幕上那份才是跑偏的那个。方向是安全的，
    但**「所见 == 所写」这条不变式确实破了**，所以它在 backlog 里带着编号。
    """
    base = _man(hot_restore, stem)
    gid = "axes_0.legend"
    field = _fields(base, gid)["ncol"]
    value = _sample_value(field)

    hot_restore.override(stem, [{"gid": gid, "prop": "ncol", "value": value}])
    after = _man(hot_restore, stem)
    assert after == base, "图例重建的漂移跑到 manifest 上了——那就不再是 P2 了"

    once = _png(hot_restore, stem, [], "legend-drift-1")
    hot_restore.override(stem, [{"gid": gid, "prop": "ncol", "value": value}])
    _man(hot_restore, stem)
    twice = _png(hot_restore, stem, [], "legend-drift-2")
    assert once == twice, "漂移是复利的——每撤销一次再坏一点，那就不止 P2 了"


# ---------------------------------------------------------------------------
# 不变式 3：热态 == 全量重放（含**删除**）
# ---------------------------------------------------------------------------
def _fresh(library, stem, patches):
    """一条**从零起**的 worker：只见过这一组 patch，没有任何历史。

    走 `one_shot` 是刻意的：池按 (项目, 脚本) 复用会话，复用了就把「全新
    worker」这条腿变成了「清空重放」的同义词，等于少验一路。
    """
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(stem, list(patches))
        assert not (resp.get("warnings") or []), resp["warnings"]
        return resp["manifest"], _png(w, stem, patches, "fresh")
    finally:
        pool.discard(w)


#: 每格 = (用例名, stem, 一串「这一步的全量 patch 列表」)。
#:
#: 全部围绕**重叠**设计：两个 gid 指着同一份状态（别名 / 广播 ↔ 窄）。撤掉
#: 其中一侧时，还原会把共享的那个 artist 写回脚本原样，而另一侧「值没变」
#: 于是走了「跳过」的捷径——热态与重放就在这里分岔，而写回自检看不见。
_LEGEND = "axes_0.legend"
_LEG_T1 = "axes_0.legend.texts_1"
_STEM = "axes_0.stemseries_0"
_STEM_MARKER_LEGACY = "axes_0.lines_0"      # 容器化之前 markerline 的 gid
_STEM_LINES_LEGACY = "axes_0.linecoll_0"    # 容器化之前 stemlines 的 gid
_BARS, _BAR1 = "axes_0.barseries_1", "axes_0.barseries_1.bar_1"

_A_LEG = {"gid": _LEGEND, "prop": "fontsize", "value": 7.5}
_B_LEG = {"gid": _LEG_T1, "prop": "fontsize", "value": 11.5}
_A_STEM = {"gid": _STEM, "prop": "color", "value": "#0000ff"}
_B_STEM = {"gid": _STEM_MARKER_LEGACY, "prop": "color", "value": "#ff0000"}
_A_SLW = {"gid": _STEM, "prop": "linewidth", "value": 3.0}
_B_SLW = {"gid": _STEM_LINES_LEGACY, "prop": "linewidth", "value": 5.0}
_A_BAR = {"gid": _BARS, "prop": "facecolor", "value": "#775599"}
_B_BAR = {"gid": _BAR1, "prop": "facecolor", "value": "#22AA44"}

REMOVAL_CASES = [
    # ---- Case A：设 A、设 B、**撤掉 A** ----
    ("A-legend-drop-broadcast", "InvCont", [[_A_LEG], [_A_LEG, _B_LEG], [_B_LEG]]),
    ("A-stem-drop-broadcast", "InvCont", [[_A_STEM], [_A_STEM, _B_STEM], [_B_STEM]]),
    ("A-stemlw-drop-broadcast", "InvCont", [[_A_SLW], [_A_SLW, _B_SLW], [_B_SLW]]),
    ("A-bars-drop-broadcast", "InvCont", [[_A_BAR], [_A_BAR, _B_BAR], [_B_BAR]]),
    # ---- Case B：设 A、设 B、**撤掉 B** ----
    ("B-legend-drop-narrow", "InvCont", [[_A_LEG], [_A_LEG, _B_LEG], [_A_LEG]]),
    ("B-stem-drop-narrow", "InvCont", [[_A_STEM], [_A_STEM, _B_STEM], [_A_STEM]]),
    ("B-stemlw-drop-narrow", "InvCont", [[_A_SLW], [_A_SLW, _B_SLW], [_A_SLW]]),
    ("B-bars-drop-narrow", "InvCont", [[_A_BAR], [_A_BAR, _B_BAR], [_A_BAR]]),
    # ---- Case C：两条都撤 → 必须精确回到脚本原样（不是中间态） ----
    ("C-legend-drop-both", "InvCont", [[_A_LEG, _B_LEG], []]),
    ("C-stem-drop-both", "InvCont", [[_A_STEM, _B_STEM], []]),
    ("C-stemlw-drop-both", "InvCont", [[_A_SLW, _B_SLW], []]),
    ("C-bars-drop-both", "InvCont", [[_A_BAR, _B_BAR], []]),
    # 色条 ↔ mappable：**这条重叠不是新开的**，一直在同一个 AxesImage 上
    ("C-colorbar-drop-both", "InvCbar",
     [[{"gid": "axes_0.images_0", "prop": "cmap", "value": "plasma"},
       {"gid": "axes_1.colorbar", "prop": "cmap", "value": "cividis"}], []]),
    ("A-colorbar-drop-mappable", "InvCbar",
     [[{"gid": "axes_0.images_0", "prop": "cmap", "value": "plasma"},
       {"gid": "axes_1.colorbar", "prop": "cmap", "value": "cividis"}],
      [{"gid": "axes_1.colorbar", "prop": "cmap", "value": "cividis"}]]),
]


@pytest.mark.parametrize("case_id,stem,steps", REMOVAL_CASES,
                         ids=[c[0] for c in REMOVAL_CASES])
def test_hot_equals_replay_after_removal(hot_replay, library, case_id, stem, steps):
    """`HOT(P) == REPLAY(P)` —— 尤其是 P 是**删出来**的那种。

    `test_equivalence_matrix.py` 已经钉了四路等价，但那边的 patch 组全是
    累加的：只有加、没有减。而 override 是全量列表语义，撤销就是**减**，
    减出来的那个 P 走的是完全不同的代码路径（`state.applied` 里有、新列表里
    没有 → 还原 → 组内其他成员要不要重放）。历史上翻车的正是这一支。

    判据比等价矩阵严：那边用 `app._compare_manifests`（只比几何，与写回放行
    同一把尺），这里比**整份 manifest + 像素**——颜色、线宽、dash 都不动
    包围盒，只有这把尺量得到。
    """
    for step in steps:
        resp = hot_replay.override(stem, list(step))
        assert not (resp.get("warnings") or []), (case_id, resp["warnings"])
    final = steps[-1]
    hot_man = _man(hot_replay, stem, final)
    hot_png = _png(hot_replay, stem, final, f"hot_replay-{case_id}")

    fresh_man, fresh_png = _fresh(library, stem, final)
    assert hot_man == fresh_man, (
        f"{case_id}：热态与全新 worker 重放的 manifest 不一致——"
        f"用户「写回时的样子」与「重开后的样子」会不同，而写回自检只比几何、"
        f"看不见这个差")
    assert hot_png == fresh_png, f"{case_id}：manifest 一样但**画出来**不一样"
    _man(hot_replay, stem)


#: Case D：同一组 patch，列表序不同，结果必须一样。
#: 优先级由 `_apply_rank` 的七档规范顺序 + 组内次序定死，**不由 list 的插入
#: 顺序偶然决定**——热会话里用户先点哪个是随机的，全量重放里的顺序又是文档
#: 存下来的那个，两者不同序却必须落成同一张图。
ORDER_CASES = [
    ("D-legend", "InvCont", [_A_LEG, _B_LEG]),
    ("D-stem", "InvCont", [_A_STEM, _B_STEM]),
    ("D-stem-linewidth", "InvCont", [_A_SLW, _B_SLW]),
    ("D-bars", "InvCont", [_A_BAR, _B_BAR]),
]


@pytest.mark.parametrize("case_id,stem,patches", ORDER_CASES,
                         ids=[c[0] for c in ORDER_CASES])
def test_patch_order_does_not_change_the_result(hot_replay, stem, patches, case_id):
    """`[A, B]` 与 `[B, A]` 必须给出同一张图。"""
    forward = _man(hot_replay, stem, patches)
    fwd_png = _png(hot_replay, stem, patches, f"fwd-{case_id}")
    _man(hot_replay, stem)
    backward = _man(hot_replay, stem, list(reversed(patches)))
    bwd_png = _png(hot_replay, stem, list(reversed(patches)), f"bwd-{case_id}")
    _man(hot_replay, stem)
    assert forward == backward, f"{case_id}：列表序换一下结果就变了"
    assert fwd_png == bwd_png, f"{case_id}：manifest 一样但画面随列表序变"


# ---------------------------------------------------------------------------
# 不变式 4 / 5：完整性与单一权威（引擎内部视角，走探针子进程）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def probe():
    proc = subprocess.run([WORKER_PY, PROBE], capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def test_no_artist_vanishes_between_elements_and_unsupported(probe):
    """登记范围内的 artist：**要么进元素表、要么进 unsupported**，不许两头都没有。

    这条曾经真的漏过：`instrument` 认为支持 → `census` 判「已知」用的正是
    登记表 → 而 `build_manifest` 量不出几何把它丢掉。于是它在 `elements` 与
    `unsupported` 两头都不出现，用户只剩一句「我图里那块东西点不中」，
    诊断里一个字都没有。
    """
    c = probe["completeness"]
    assert not c["orphans"], (
        "这些 artist 登记过、却既没进元素表也没报进 unsupported：\n  "
        + "\n  ".join(f"{o['gid']} ({o['cls']})" for o in c["orphans"]))
    assert not c["unseen"], (
        "这些 artist 画在图上、普查也没报出来：\n  "
        + "\n  ".join(f"{u['where']} {u['cls']}" for u in c["unseen"]))
    # `unsupported` 只报类名、不报对象 id，所以「同一个 artist 同时在两边」
    # 从 manifest 侧判不出来（`Rectangle` 既是柱、又是插图背景 patch）。XOR
    # 的这一半由上面两条合起来保证：登记过的必须有代表 + 树里的必须被看见。


def test_the_churn_exemption_stays_small_and_named(probe):
    """豁免表不许变成垃圾桶。

    「登记了但这一轮没进 manifest」只有两个正当理由：刻度不是常驻 artist
    （换 locator / 改 xlim / 翻色条方向都会让整组重来），空文字的标题与轴
    标签本来就不该进元素树。多出第三种理由 = 有人在拿豁免掩盖真问题，
    而 unsupported 一旦开始喊狼来了，真缺口就没人看了。
    """
    reasons = {row["why"] for row in probe["completeness"]["churn"]}
    assert reasons <= {"tick_churn", "empty_text"}, \
        f"豁免表里冒出了没有登记过的理由：{sorted(reasons - {'tick_churn', 'empty_text'})}"


def test_unsupported_says_what_and_why(probe):
    """报出来的每一条都要说得出类名与原因——说不出来就等于没报。"""
    for row in probe["completeness"]["unsupported"]:
        assert row.get("cls"), row
        assert row.get("where"), row
    ghosts = [r for r in probe["completeness"]["unsupported"]
              if "GhostArtist" in r["cls"]]
    assert ghosts and all(g.get("reason") == "no_geometry" for g in ghosts), \
        f"量不出几何的自定义 artist 没被报出来：{probe['completeness']['unsupported']}"
    # **插图里的那个也要报出来**。`inset_axes` / `secondary_[xy]axis` 挂在
    # `ax.child_axes` 上、不在 `fig.axes` 里——普查只走 fig.axes 的时候，插图
    # 里漏掉的 artist 在报告里一个字都不出现，而报告照样说「没漏」。
    assert len(ghosts) >= 2, f"插图里那个量不出几何的 artist 没被普查看见：{ghosts}"
    # 而插图自己的结构件（背景 patch、四条边框）**不许**被报成漏掉——
    # 它们由 `axes_i` 代表，报出来就是普查在喊狼来了
    noise = [r for r in probe["completeness"]["unsupported"]
             if r["cls"].endswith(("patches.Rectangle", "spines.Spine")) and not r.get("reason")]
    assert not noise, f"axes 的结构件被报成漏掉的 artist：{noise}"


def test_family_classification_has_a_single_authority(probe):
    """「这个 artist 属于哪一族」只能有一处判据。

    映射的 LineCollection 就是这么坏的：登记那头按 `get_array()` 把它放进
    `collections_j`，`_cls_key` 却无条件回 `linecoll`——元素表说它是通用
    collection，检查器按线组给了 `color`，而 `HANDLERS[("linecoll", …)]`
    根本不在这个元素上。那个控件一个像素都改不动，而且不报错。
    """
    a = probe["single_authority"]
    assert not a["family_conflicts"], (
        "登记时挑的 role 与 dispatch 时算的 family 对不上：\n  "
        + "\n  ".join(f"{c['gid']} role={c['role']} cls_key={c['cls_key']} "
                      f"（应为 {c['expected']}）" for c in a["family_conflicts"]))
    assert not a["prefix_conflicts"], (
        "gid 前缀与 dispatch family 对不上（`_collection_gid_prefix` 与 "
        "`is_linecoll_family` 必须是同一个判据）：\n  "
        + "\n  ".join(str(c) for c in a["prefix_conflicts"]))


def test_every_advertised_prop_has_a_handler(probe):
    """元素表宣称的每一条 prop，dispatch 侧都必须有 handler。

    没有 handler 的字段在界面上长得和别的一模一样，点下去 `apply` 报一条
    「不支持的属性」——而 warning 一条就阻断写回。这是「能力真实」在**静态**
    这一侧的那一半：不变式 1 验的是它改不改得动，这里验的是它有没有人接。
    """
    missing = probe["single_authority"]["missing_handlers"]
    assert not missing, (
        "宣称了却没有 handler：\n  "
        + "\n  ".join(f"{m['gid']} {m['cls_key']}.{m['prop']}" for m in missing))


def test_the_mesh_stroke_style_table_still_holds(probe):
    """`honours_stroke_style` 那张实测表**每次跑都重新渲染核对一遍**。

    那是一条按类名写死的例外（网格类的渲染原语不接花纹与虚线），而写死的例外
    最怕悄悄过期。这条用例不看类名，只看像素：

      * 判据说「不认」的，hatch 与 linestyle 的像素改动必须都是 **0**——
        真是 0 才说明这个开关给了也白给；
      * 判据说「认」的，至少 linestyle 要真的动像素（花纹另需有面，
        `LineCollection` 没有面，所以只验虚线）。

    哪天 matplotlib 给 `draw_quad_mesh` 补上花纹，这条会红——那时该做的是
    放开能力，不是改这条断言。`Arc` 那次的教训正是「例外没人复测」。
    """
    rows = probe["stroke_style_table"]
    assert len(rows) >= 6, rows
    for r in rows:
        if r["predicate"]:
            assert r["dash_px"] > 0, \
                f"{r['case']}（{r['cls']}）判据说认线型，实测改了 0 个像素：{r}"
        else:
            assert r["hatch_px"] == 0 and r["dash_px"] == 0, (
                f"{r['case']}（{r['cls']}）现在**认**花纹/线型了（"
                f"hatch={r['hatch_px']} dash={r['dash_px']}）——matplotlib 补上了，"
                f"该放开 `honours_stroke_style` 的例外，而不是改这条断言")


def test_alias_groups_are_self_consistent(probe):
    """别名表里的广播端自己得有 handler，否则那一组永远解析不出来。"""
    a = probe["single_authority"]
    assert not a["alias_without_handler"], a["alias_without_handler"]
    assert a["alias_group_count"] >= 15, \
        f"别名组只剩 {a['alias_group_count']} 条，是不是有人整批删了？"
