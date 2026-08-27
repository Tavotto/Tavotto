"""四路等价性矩阵：整个渲染引擎的最终验收物（Phase G）。

钉的是**一条不变式**，它是「热会话编辑 → 写回原件 → 明天重开」这条产品主线
唯一的正确性依据：

    hot_apply(patches)  ==  清空后全量重放  ==  全新 worker 重放
                        ==  写回文件后全新 worker 重放

四条路里任意两条分歧，用户看到的就是 FigS3 那类事故：写回时的样子与重开后
的样子不一样，而中间没有任何报错。比较器直接**复用 `app._compare_manifests`**
——写回事务用来放行/阻断的就是它，容差（bbox/anchor 0.5% figure 分数、
size_mm 0.01mm）与产品判据一字不差；另起一套只会让矩阵与产品各绿各的。

跑法与依赖：本进程不 import matplotlib，worker 全部经 `pool.one_shot()` 起在
科学栈解释器里（缺就整组跳过）。一份多 figure 的测试图库一次 build 捕获全部
场景（实测 build ≈0.7s），四路里每条都用**各自独立的 worker 进程**——共用一个
就把「全新 worker」这条腿变成了「清空重放」的同义词，等于少验一路。

场景 × patch 组的覆盖表见 `_SCENARIOS` / `GROUPS` 两张表的注释。
"""

import json
import os
import subprocess
from pathlib import Path

import pymupdf
import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_equivalence.py"
ENTRY = "main"

# 场景表（一个 figure 一个场景，一次 build 全捕获）：
#   EqvMulti  s1 多 axes 2D（tight_layout）+ 双图例 + errorbar + 散点
#   EqvImage  s2 imshow + colorbar（aspect=equal）+ 散点子图
#   EqvAnnot  s3 constrained_layout + annotate 箭头 + 独立 FancyArrowPatch
#   Eqv3D     s4 3D axes（文字 + 视角 + zlabel）
#   EqvMath   s5 mathtext + serif/sans 混排
#   EqvCJK    s6 中文标签（探测不到 CJK 字体时该场景的用例单独 skip）
#   EqvFam    s7 Artist family（2026-08-21）：Collection 族（fill_between /
#             pcolormesh / contour / LineCollection）、Patch 族（Wedge /
#             Rectangle / StepPatch）、stem 容器。新开放的能力必须与老的
#             一样满足四路一致——否则「写回时的样子 ≠ 重开后的样子」
#   EqvAlias  s8 柱形系列 + 带标题的图例 + 色条——三族**别名组**一次盖到
#             （广播型 prop 与它管着的窄 prop 同时被 override，见 ALIAS_GROUPS）
_SCENARIOS = (
    "EqvMulti",
    "EqvImage",
    "EqvAnnot",
    "Eqv3D",
    "EqvMath",
    "EqvCJK",
    "EqvFam",
    "EqvAlias",
)

#: s6 用的中文字体候选。matplotlib 找不到任何一个时 s6 的用例 **skip 并注明
#: 理由**——静默换成拉丁文本跑过去，等于宣称测过中文而实际没有。
_CJK_CANDIDATES = (
    "PingFang SC",
    "Heiti SC",
    "Heiti TC",
    "Songti SC",
    "STHeiti",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Microsoft YaHei",
    "SimHei",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
)

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

CJK = {cjk!r}


def main():
    # ---- s1：多 axes 2D + tight_layout + 双图例 + errorbar + 散点 ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(5.2, 2.4))
    x = np.linspace(0.2, 4.0, 12)
    ax1.errorbar(x, np.sin(x), yerr=0.12, label="sin", capsize=2)
    ax1.plot(x, np.cos(x), label="cos")
    ax1.set_title("Panel A")
    ax1.set_xlabel("x (a.u.)")
    ax1.set_ylabel("y (a.u.)")
    ax1.text(0.15, 0.80, "note-a", transform=ax1.transAxes)
    ax1.legend()
    ax2.scatter(x, np.sqrt(x), label="pts")
    ax2.plot(x, x / 4.0, label="ref")
    ax2.set_title("Panel B")
    ax2.legend()
    fig.tight_layout()
    fig.savefig("EqvMulti.pdf")

    # ---- s2：imshow + colorbar（aspect="equal"，draw 才 apply_aspect）----
    fig2, (bx1, bx2) = plt.subplots(1, 2, figsize=(5.0, 2.2))
    im = bx1.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    fig2.colorbar(im, ax=bx1)
    bx1.set_title("Map")
    bx2.scatter([0, 1, 2, 3], [1, 3, 2, 4], label="dots")
    bx2.legend()
    fig2.savefig("EqvImage.pdf")

    # ---- s3：constrained_layout + annotate（带箭头）+ 独立箭头 ----
    fig3, cx = plt.subplots(figsize=(3.4, 2.4), layout="constrained")
    t = np.linspace(0.0, 6.0, 40)
    cx.plot(t, np.exp(-t / 3.0))
    cx.annotate("decay", xy=(2.0, 0.51), xytext=(3.4, 0.80),
                arrowprops=dict(arrowstyle="->", color="#2A6F3C"))
    cx.add_patch(FancyArrowPatch(posA=(1.0, 0.20), posB=(4.5, 0.35),
                                 transform=cx.transData, arrowstyle="-|>",
                                 mutation_scale=8, color="#76008A"))
    cx.set_title("Constrained")
    fig3.savefig("EqvAnnot.pdf")

    # ---- s4：3D（文字 + 视角 + zlabel）----
    fig4 = plt.figure(figsize=(3.2, 2.6))
    dx = fig4.add_subplot(projection="3d")
    dx.plot([0, 1, 2], [0, 1, 0], [0, 1, 2])
    dx.set_title("Volume")
    dx.set_xlabel("X axis")
    dx.set_ylabel("Y axis")
    dx.set_zlabel("Z depth")
    dx.text2D(0.10, 0.85, "tag3d", transform=dx.transAxes)
    fig4.savefig("Eqv3D.pdf")

    # ---- s5：mathtext + serif/sans 混排 ----
    fig5, ex = plt.subplots(figsize=(3.4, 2.2))
    ex.plot([0, 1, 2], [2, 1, 3])
    ex.set_title(r"Raman shift $\\nu$", family="serif")
    ex.set_xlabel(r"Wavenumber (cm$^{{-1}}$)", family="serif")
    ex.set_ylabel(r"$I/I_0$", family="sans-serif")
    ex.text(0.10, 0.80, r"$\\alpha_{{2}}^{{3}}$ mixed", transform=ex.transAxes,
            family="sans-serif")
    fig5.savefig("EqvMath.pdf")

    # ---- s6：中文标签（字体由外部探测后写死，探不到就退回拉丁文本）----
    fig6, fx = plt.subplots(figsize=(3.4, 2.2))
    fx.plot([0, 1, 2], [1, 3, 2])
    if CJK:
        fx.set_title("拉曼位移谱图", fontfamily=CJK)
        fx.set_xlabel("波数 (cm$^{{-1}}$)", fontfamily=CJK)
        fx.set_ylabel("强度（任意单位）", fontfamily=CJK)
        fx.text(0.12, 0.78, "峰位标注", transform=fx.transAxes, fontfamily=CJK)
    else:
        fx.set_title("Raman shift")
        fx.set_xlabel("Wavenumber")
        fx.set_ylabel("Intensity")
        fx.text(0.12, 0.78, "peak", transform=fx.transAxes)
    fig6.savefig("EqvCJK.pdf")

    # ---- s7：Artist family（Collection / Patch / 容器）----
    fig7, (gx1, gx2) = plt.subplots(1, 2, figsize=(5.2, 2.4))
    u = np.linspace(0.5, 6.0, 24)
    M = np.arange(64).reshape(8, 8) / 64.0
    gx1.fill_between(u, 0.0, np.sin(u) + 1.2, alpha=0.3)      # fill_0
    gx1.pcolormesh(np.linspace(7, 9, 9), np.linspace(0, 1, 9), M)   # collections_1
    gx1.contour(np.linspace(7, 9, 8), np.linspace(1.4, 2.4, 8), M)  # collections_2
    gx1.stem([1.0, 2.0, 3.0], [1.8, 2.2, 1.5])               # stemseries_0
    gx1.set_xlim(0, 10)
    gx1.set_ylim(-0.4, 2.6)
    gx1.set_title("Families")
    gx2.pie([3, 4, 5])                                       # patches_0..2 (Wedge)
    fig7.savefig("EqvFam.pdf")

    # ---- s8：别名组（广播型 prop 与它管着的窄 prop 同时可编辑）----
    # 柱形系列（整组 vs 单根柱）、图例（整体字号 / 标题字号 vs 单条图例项）、
    # 色条（tick_* vs 色条轴自己的刻度组）——三族一次盖到。
    fig8, (gx, hx) = plt.subplots(1, 2, figsize=(5.4, 2.4))
    gx.bar(["a", "b", "c"], [3.0, 5.0, 2.0], label="counts")
    gx.plot([0, 1, 2], [4.0, 2.0, 5.0], label="trend")
    gx.legend(title="series")
    gx.set_title("Bars")
    im8 = hx.imshow(np.arange(36).reshape(6, 6), cmap="viridis")
    fig8.colorbar(im8, ax=hx).set_label("intensity")
    hx.set_title("Map")
    fig8.tight_layout()
    fig8.savefig("EqvAlias.pdf")
"""

_FONT_PROBE = """\
import json, sys
from matplotlib import font_manager
have = {f.name for f in font_manager.fontManager.ttflist}
print(json.dumps([n for n in sys.argv[1:] if n in have]))
"""


def _detect_cjk_font() -> str:
    """在 **worker 解释器**里问 matplotlib 有没有中文字体（本进程没有 mpl）。"""
    try:
        out = subprocess.run(
            [WORKER_PY, "-c", _FONT_PROBE, *_CJK_CANDIDATES],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        found = json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return ""
    return found[0] if found else ""


# ---------------------------------------------------------------------------
# 图库与 worker
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def cjk_font() -> str:
    return _detect_cjk_font()


@pytest.fixture(scope="session")
def library(tmp_path_factory, cjk_font) -> Path:
    """一份 6 figure 的测试图库（整个模块共用，脚本内容全程不变）。"""
    figs = tmp_path_factory.mktemp("eqv-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY.format(cjk=cjk_font), encoding="utf-8")
    return figs


def _worker(figs: Path):
    """一条**从零起的** worker：`one_shot` 不进池、目录独立、用完即毁。

    走 `one_shot` 而不是 `pool.get()` 是刻意的：池按 (项目, 脚本) 复用会话，
    workerd 还按 spawn 规格哈希复用——两者都会把「全新 worker」这条腿悄悄
    换成「热会话」，而那正是本文件要区分的两件事。
    """
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


# ---------------------------------------------------------------------------
# 比较：直接复用写回事务的判据
# ---------------------------------------------------------------------------
def _compare(a: dict, b: dict) -> tuple[list, int]:
    from tavotto.app import _compare_manifests

    return _compare_manifests(a, b)


def _assert_same(name_a: str, man_a: dict, name_b: str, man_b: dict) -> None:
    diffs, compared = _compare(man_a, man_b)
    assert compared > 0, f"{name_a} / {name_b} 之间没有可比元素（manifest 空？）"
    if diffs:
        lines = [
            f"{d['gid'] or '<figure>'}.{d['field']}: {name_a}={d['hot']} {name_b}={d['fresh']}"
            for d in diffs[:12]
        ]
        raise AssertionError(
            f"{name_a} 与 {name_b} 的 manifest 分歧 {len(diffs)} 处"
            f"（比过 {compared} 个元素）:\n  " + "\n  ".join(lines)
        )


#: 「这个元素上根本没有这个可编辑字段」——与 `value` 真的是 None 区分开
_MISSING = object()


def _el(man: dict, gid: str) -> dict:
    return next(e for e in man["elements"] if e["gid"] == gid)


def _field(man: dict, gid: str, prop: str):
    el = _el(man, gid)
    return next((f["value"] for f in el.get("editable", []) if f["prop"] == prop), _MISSING)


def _assert_effect(man: dict, patches: list) -> None:
    """这组 patch **真的落到图上了**——否则四路一致只是「四路都没干活」。

    位置类三个 prop 不在 editable 里（它们是 manifest 的 anchor /
    arrow_endpoints），单独换算；其余按同名可编辑字段逐条核对。
    """
    for p in patches:
        gid, prop, want = p["gid"], p["prop"], p["value"]
        if prop == "size_mm":
            got = man.get("size_mm")
            assert got == pytest.approx(want, abs=0.05), (prop, want, got)
            continue
        if prop in ("pos_frac", "loc_frac"):
            got = _el(man, gid).get("anchor")
            assert got is not None, f"{gid} 没有 anchor（拖不动？）"
            assert got == pytest.approx(want, abs=0.02), (gid, prop, want, got)
            continue
        if prop == "endpoints_frac":
            pts = _el(man, gid).get("arrow_endpoints")
            assert pts, f"{gid} 没有 arrow_endpoints"
            got = [pts[0][0], pts[0][1], pts[1][0], pts[1][1]]
            assert got == pytest.approx(want, abs=0.02), (gid, prop, want, got)
            continue
        got = _field(man, gid, prop)
        assert got is not _MISSING, f"{gid} 没有可编辑字段 {prop}"
        if isinstance(want, (int, float)) and not isinstance(want, bool):
            assert got == pytest.approx(want, rel=0.02, abs=0.02), (gid, prop, want, got)
        elif isinstance(want, str):
            assert str(got).lower() == want.lower(), (gid, prop, want, got)
        else:
            assert got == want, (gid, prop, want, got)


def _no_apply_warnings(resp: dict, arm: str) -> dict:
    warns = resp.get("warnings") or []
    assert not warns, f"{arm} 上有 override 未生效: {warns}"
    return resp


# ---------------------------------------------------------------------------
# 三路（热 / 清空重放 / 全新 worker）；第四路（写回后重开）另有专门用例
# ---------------------------------------------------------------------------
def _three_ways(figs: Path, stem: str, steps: list[list], expect_cls=None) -> dict:
    """`steps` 是**逐步累积**的 patch 列表（模拟用户一下一下地改），末条即全量。

    返回三条腿的 manifest。热路与清空重放共用一条 worker（它们本来就是同一
    会话上的两件事），全新 worker 单独起一条。
    """
    full = steps[-1]
    hot = _worker(figs)
    if expect_cls is not None:
        assert isinstance(hot, expect_cls), (
            f"控制面不对：期望 {expect_cls.__name__}，实际 {type(hot).__name__}"
        )
    try:
        man_hot = {}
        for step in steps:
            man_hot = _no_apply_warnings(hot.override(stem, step), "热路")["manifest"]
        # 清空 = 全量列表语义下的「撤销全部」，再一次性重放
        _no_apply_warnings(hot.override(stem, []), "清空")
        man_replay = _no_apply_warnings(hot.override(stem, full), "清空重放")["manifest"]
    finally:
        pool.discard(hot)

    fresh = _worker(figs)
    try:
        man_fresh = _no_apply_warnings(fresh.override(stem, full), "全新 worker")["manifest"]
    finally:
        pool.discard(fresh)

    _assert_effect(man_hot, full)
    _assert_same("热路", man_hot, "清空重放", man_replay)
    _assert_same("热路", man_hot, "全新 worker", man_fresh)
    _assert_same("清空重放", man_replay, "全新 worker", man_fresh)
    return {"hot": man_hot, "replay": man_replay, "fresh": man_fresh}


def _base_getter(figs: Path, stem: str):
    """惰性取「什么都没改」的 manifest（只有需要现有几何的组合才会起这条 worker）。"""
    box: list = []

    def get() -> dict:
        if not box:
            w = _worker(figs)
            try:
                box.append(w.override(stem, [])["manifest"])
            finally:
                pool.discard(w)
        return box[0]

    return get


# ---------------------------------------------------------------------------
# patch 组合表：场景 × 用户点名的编辑动作
#
# 每个组装函数拿一个 `getbase`（**惰性**取原始 manifest 的函数）——只有真的要
# 参照现有几何的组合才会为此多起一条 worker。
# ---------------------------------------------------------------------------
def _cumulative(*patches: dict) -> list[list]:
    """一条条加上去（热路要的是**增量历史**，不是一次性全量）。"""
    return [list(patches[: i + 1]) for i in range(len(patches))]


def _g_text_then_axes(_getbase):
    """文字 pos_frac 移动 → 子图 position 移动（FigS3 事故的原型组合）。"""
    return _cumulative(
        {"gid": "axes_0.texts_0", "prop": "pos_frac", "value": [0.18, 0.30]},
        {"gid": "axes_0", "prop": "position", "value": [0.12, 0.22, 0.34, 0.62]},
    )


def _g_text_then_size(_getbase):
    """文字移动 → 图幅 size_mm 变化（分数锚点与物理尺寸无关）。"""
    return _cumulative(
        {"gid": "axes_0.texts_0", "prop": "pos_frac", "value": [0.30, 0.35]},
        {"gid": "figure", "prop": "size_mm", "value": [110.0, 72.0]},
    )


def _g_labels_and_title(_getbase):
    """xlabel / ylabel / title 拖动：走的是 set_label_coords 与 _autotitlepos
    两条特殊路径（普通 set_position 会被 matplotlib 每帧覆盖）。"""
    return _cumulative(
        {"gid": "axes_0.xlabel", "prop": "pos_frac", "value": [0.55, 0.93]},
        {"gid": "axes_0.ylabel", "prop": "pos_frac", "value": [0.045, 0.45]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.38, 0.09]},
    )


def _g_legend_move_and_reorder(_getbase):
    """图例拖动（loc_frac）+ 条目重排（entry_order 是**重建型**）。"""
    return _cumulative(
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.28, 0.42]},
        {"gid": "axes_0.legend", "prop": "entry_order", "value": [1, 0]},
    )


def _g_annotation_text_move(_getbase):
    """annotate 文字移动。arrow_patch 的端点由注释机制每帧重定位，
    manifest 里**绝不能**出现 arrow_endpoints（出了用户拖完就弹回）。"""
    return _cumulative(
        {"gid": "axes_0.texts_0", "prop": "pos_frac", "value": [0.62, 0.24]},
        {"gid": "axes_0.texts_0", "prop": "fontsize", "value": 11},
    )


def _g_arrow_endpoints(getbase):
    """独立箭头端点拖动（endpoints_frac，figure 分数、y 向下）+ 换样式。"""
    pts = _el(getbase(), "axes_0.arrows_0")["arrow_endpoints"]
    moved = [
        round(pts[0][0] + 0.08, 4),
        round(pts[0][1] - 0.05, 4),
        round(pts[1][0] + 0.05, 4),
        round(pts[1][1] + 0.06, 4),
    ]
    return _cumulative(
        {"gid": "axes_0.arrows_0", "prop": "endpoints_frac", "value": moved},
        {"gid": "axes_0.arrows_0", "prop": "arrowstyle", "value": "->"},
    )


def _g_view3d_and_visible(_getbase):
    """3D 视角（elev/azim 一动，盒内所有元素的落位全变）+ 文字 visible。"""
    return _cumulative(
        {"gid": "axes_0", "prop": "elev", "value": 18.0},
        {"gid": "axes_0", "prop": "azim", "value": -52.0},
        {"gid": "axes_0.texts_0", "prop": "visible", "value": False},
    )


def _g_colorbar_range(_getbase):
    """色条 vmin/vmax + 刻度字号（刻度字号一变，色条轴的 bbox 跟着变）。"""
    return _cumulative(
        {"gid": "axes_2.colorbar", "prop": "vmin", "value": 8.0},
        {"gid": "axes_2.colorbar", "prop": "vmax", "value": 50.0},
        {"gid": "axes_2.colorbar", "prop": "tick_fontsize", "value": 6.5},
    )


def _g_scatter_marker(_getbase):
    """散点 marker 整体替换（set_paths，首改前缓存原始路径）。"""
    return _cumulative(
        {"gid": "axes_1.scatter_0", "prop": "marker", "value": "s"},
        {"gid": "axes_1.scatter_0", "prop": "size", "value": 42.0},
    )


def _g_collection_family(_getbase):
    """Collection 族：填充 / 彩色网格 / 等值线各改一组样式。

    `pcolormesh` 与 `contour` 从前根本进不了元素表，所以这一格既是能力的
    证明，也是它们**不会**把热态与全量重放拉开的证明——颜色映射类的
    facecolors 每次 draw 由 `update_scalarmappable()` 重算，如果哪天有人把
    facecolor 加回这些元素上，这里会当场分岔。
    """
    return _cumulative(
        {"gid": "axes_0.fill_0", "prop": "facecolor", "value": "#B34700"},
        {"gid": "axes_0.fill_0", "prop": "hatch", "value": "//"},
        {"gid": "axes_0.collections_1", "prop": "edgecolor", "value": "#333333"},
        {"gid": "axes_0.collections_1", "prop": "cmap", "value": "plasma"},
        {"gid": "axes_0.collections_2", "prop": "linewidth", "value": 1.8},
    )


def _g_patch_family_and_stem(_getbase):
    """Patch 族（pie 的扇形）+ stem 容器，再叠一次图幅变化。

    图幅一变所有分数坐标的物理落点都变（`_apply_rank` 的 0 档），三条腿在
    这之后仍要收敛到同一个几何——新元素也不例外。
    """
    return _cumulative(
        {"gid": "axes_1.patches_0", "prop": "facecolor", "value": "#2A6F3C"},
        {"gid": "axes_1.patches_1", "prop": "hatch", "value": "xx"},
        {"gid": "axes_0.stemseries_0", "prop": "color", "value": "#76008A"},
        {"gid": "axes_0.stemseries_0", "prop": "markersize", "value": 9.0},
        {"gid": "figure", "prop": "size_mm", "value": [148.0, 66.0]},
    )


def _g_axes_range_scale_and_ticks(_getbase):
    """坐标轴范围 / 缩放类型 / spine + 刻度定位模型（Locator + Formatter）。

    最容易分岔的一组：`set_xscale` 会把该轴的 locator/formatter 整套换掉，
    所以「换刻度类型」必须先于「配置刻度定位」，而刻度定位又必须先于
    「改单条刻度文字」（冻结整条轴是最后一步）。这些先后关系是
    `overrides._apply_rank` 的规范档位，本组就是它的压力测试。
    """
    return _cumulative(
        {"gid": "axes_0", "prop": "xlim", "value": [0.4, 4.4]},
        {"gid": "axes_0", "prop": "ylim", "value": [-1.4, 1.4]},
        {"gid": "axes_0", "prop": "xscale", "value": "log"},
        {"gid": "axes_0", "prop": "spine_top", "value": False},
        {"gid": "axes_0", "prop": "spine_linewidth", "value": 1.4},
        # 「全部」与「逐条」是两条会互相盖写的 setter：一起出现才验得出
        # 「谁先谁后都是同一张图」
        {"gid": "axes_0", "prop": "spine_left_linewidth", "value": 2.2},
        {"gid": "axes_0", "prop": "spine_bottom_color", "value": "#B34700"},
        {"gid": "axes_0.yticks", "prop": "major_mode", "value": "step"},
        {"gid": "axes_0.yticks", "prop": "major_step", "value": 0.5},
        {"gid": "axes_0.yticks", "prop": "minor_visible", "value": True},
        {"gid": "axes_0.yticks", "prop": "format", "value": "%.2f"},
        {"gid": "axes_0.yticks", "prop": "minor_format", "value": "%.1f"},
    )


def _g_fixed_ticks_and_label_text(getbase):
    """固定刻度位置 + 直接改其中一条刻度的文字。

    冻结整条轴（FixedLocator + FixedFormatter）是最后一档，先冻的会被后来的
    locator 换掉——顺序反了，热会话与全量重放会得到两组不同的刻度文字。
    """
    # 刻度文字刻意用 ASCII：这条用例最后要从写回的 PDF 里把它读出来，而中文
    # 得先有 CJK 字体（s6 为此专门做了字体探测）。要验的是「文字留住了没有」，
    # 不是字体，别把两件事绑在一起。
    xticks = [
        e["gid"]
        for e in getbase()["elements"]
        if e["role"] == "ticklabel" and e["gid"].startswith("axes_0.xtick")
    ]
    return _cumulative(
        {"gid": "axes_0.xticks", "prop": "major_mode", "value": "fixed"},
        {"gid": "axes_0.xticks", "prop": "major_values", "value": [0.5, 1.5, 2.5, 3.5]},
        {"gid": "axes_0.xticklabels_1", "prop": "text", "value": "mid-tick"},
        {"gid": xticks[0], "prop": "text", "value": "start"},
    )


def _g_nonlinear_scales(_getbase):
    """symlog / logit：界面里出得来，就必须真的跑得起来并且四路一致。"""
    return _cumulative(
        {"gid": "axes_0", "prop": "yscale", "value": "symlog"},
        {"gid": "axes_1", "prop": "xscale", "value": "logit"},
    )


def _g_legend_item_text(_getbase):
    """单个图例项的文字 + 图例标题 + 重建型布局（ncol）。

    三件事必须分得清：改图例项文字、改源曲线 label、触发图例重建。ncol 是
    重建型的（文字对象整批换新），重建之后已应用的文字 override 必须被重放到
    新对象上，否则「改过的图例项在换列数之后自己变回去」。
    """
    return _cumulative(
        {"gid": "axes_0.legend.texts_0", "prop": "text", "value": "sin(x)"},
        {"gid": "axes_0.legend", "prop": "title", "value": "Series"},
        {"gid": "axes_0.legend", "prop": "ncol", "value": 2},
    )


def _g_colorbar_orientation(_getbase):
    """色条方向翻转 + 两端延伸三角（两次就地结构改造）+ 既有属性一并保留。"""
    return _cumulative(
        {"gid": "axes_2.colorbar", "prop": "orientation", "value": "horizontal"},
        # extend 与方向是两次结构改造，且**必须**方向在前（方向要拿色条当前
        # 的矩形反解厚度与间距）——把它们放进同一组才验得到这条先后
        {"gid": "axes_2.colorbar", "prop": "extend", "value": "both"},
        {"gid": "axes_2.colorbar", "prop": "label", "value": "intensity"},
        {"gid": "axes_2.colorbar", "prop": "tick_fontsize", "value": 6.0},
        {"gid": "axes_2.colorbar", "prop": "vmin", "value": 6.0},
    )


def _g_mixed(_getbase):
    """混合大组合：12 条 patch 跨 figure / 子图 / 文字 / 标签 / 图例 / 数据系列 /
    刻度七类，且几何与 figure 锚定属性交错——重放顺序规范化真正的压力测试。"""
    return _cumulative(
        {"gid": "axes_0.texts_0", "prop": "pos_frac", "value": [0.20, 0.28]},
        {"gid": "axes_0.title", "prop": "pos_frac", "value": [0.24, 0.08]},
        {"gid": "axes_0", "prop": "position", "value": [0.10, 0.24, 0.33, 0.58]},
        {"gid": "figure", "prop": "size_mm", "value": [150.0, 70.0]},
        {"gid": "axes_0.legend", "prop": "loc_frac", "value": [0.13, 0.52]},
        {"gid": "axes_0.legend", "prop": "entry_order", "value": [1, 0]},
        {"gid": "axes_0.xlabel", "prop": "pos_frac", "value": [0.26, 0.95]},
        {"gid": "axes_1", "prop": "position", "value": [0.58, 0.20, 0.36, 0.62]},
        {"gid": "axes_1.scatter_0", "prop": "marker", "value": "^"},
        {"gid": "axes_0.errorbar_0", "prop": "color", "value": "#B34700"},
        {"gid": "axes_0.lines_3", "prop": "linewidth", "value": 2.4},
        {"gid": "axes_1.xticks", "prop": "fontsize", "value": 7.0},
    )


#: (用例 id, stem, 组装函数)。**每个场景至少一条**留在默认集里。
def _g_alias_legend(_getbase):
    """图例的**广播型** prop 与它管着的**窄** prop 叠加。

    `legend.fontsize` 写的是每一条图例项的 Text，`legend.title_fontsize` 写的
    是标题那个 Text——而这三个 Text 各自都是登记元素、各自都有 `fontsize`。
    两者叠加时 `originals` 的快照是顺序相关的：单条那次记下的「原样」会是被
    整体改过的值，撤销就回不到脚本原样（见 overrides.ALIAS_GROUPS）。

    这一组的意义在于：**它一定要经过四路**。热态是「先整体后单条」的增量，
    全量重放是一次性给全表，两条路只有在广播先于窄的、且窄的在广播之后被
    重放时才会收敛到同一张图。

    窄端刻意取 `texts_1` 而不是 `texts_0`：manifest 的 `legend.fontsize` 报的
    是 `sizes[0]`（第一条图例项的字号），覆盖第 0 条会让「广播落没落」与
    「窄的落没落」在 manifest 上分不开，`_assert_effect` 也就失去意义。

    **别名组分两种形状，这里只放得下第一种**：
      * 一对多（整组 vs 其中一个）—— `legend.fontsize` → `texts_j`、
        `bar_series.*` → `bar_k`。两边能同时落在不同成员上，四路可比。
      * 一对一 / 一对全（两个名字指同一批 artist）—— `legend.title_fontsize`
        → `legend.title`、`colorbar.tick_*` → 色条轴刻度组。后应用的必然盖掉
        前一个，manifest 也只报得出一个值，`_assert_effect` 表达不了「两个都
        落地」。第二种的**还原**语义同样会坏，由 test_worker_roundtrip 的别名
        用例看着。
    """
    return _cumulative(
        {"gid": "axes_0.legend", "prop": "fontsize", "value": 7.5},
        {"gid": "axes_0.legend.texts_1", "prop": "fontsize", "value": 9.5},
        {"gid": "axes_0.legend.texts_1", "prop": "color", "value": "#804000"},
    )


def _g_alias_bars(_getbase):
    """柱形系列整组样式 + 单根柱覆盖（与图例同一类别名语义）。

    刻意让**中间那根**柱与整组不同：只改第一根的话，`_bar_handler` 的
    「按成员列表还原」恰好也能凑对，掩盖顺序问题。
    """
    return _cumulative(
        {"gid": "axes_0.barseries_0", "prop": "facecolor", "value": "#775599"},
        {"gid": "axes_0.barseries_0.bar_1", "prop": "facecolor", "value": "#22AA44"},
        {"gid": "axes_0.barseries_0", "prop": "alpha", "value": 0.55},
        {"gid": "axes_0.barseries_0.bar_1", "prop": "alpha", "value": 0.9},
    )


def _g_alias_mixed(_getbase):
    """三族别名 + 一个与别名无关的 prop 混在一起，且**窄的排在广播之前**。

    `apply` 是全量列表语义：同一组 patch 无论列表序怎么排都必须落成同一张图。
    这一组就是把顺序故意排反，逼出排序而不是运气。

    窄端一律避开成员 0（见 `_g_alias_legend` 的说明：整组字段报的是成员 0）。
    """
    return _cumulative(
        {"gid": "axes_0.legend.texts_1", "prop": "fontsize", "value": 12.0},
        {"gid": "axes_0.barseries_0.bar_2", "prop": "facecolor", "value": "#118844"},
        {"gid": "axes_0.legend", "prop": "fontsize", "value": 8.0},
        {"gid": "axes_0.barseries_0", "prop": "facecolor", "value": "#CC7722"},
        {"gid": "axes_0.title", "prop": "fontsize", "value": 13.0},
    )


GROUPS = [
    ("s1-text-then-axes", "EqvMulti", _g_text_then_axes),
    ("s1-labels-and-title", "EqvMulti", _g_labels_and_title),
    ("s1-legend-reorder", "EqvMulti", _g_legend_move_and_reorder),
    ("s1-mixed-12-patches", "EqvMulti", _g_mixed),
    ("s1-range-scale-ticks", "EqvMulti", _g_axes_range_scale_and_ticks),
    ("s1-fixed-ticks-text", "EqvMulti", _g_fixed_ticks_and_label_text),
    ("s1-nonlinear-scales", "EqvMulti", _g_nonlinear_scales),
    ("s1-legend-item-text", "EqvMulti", _g_legend_item_text),
    ("s2-colorbar-range", "EqvImage", _g_colorbar_range),
    ("s2-colorbar-orientation", "EqvImage", _g_colorbar_orientation),
    ("s2-scatter-marker", "EqvImage", _g_scatter_marker),
    ("s3-annotation-move", "EqvAnnot", _g_annotation_text_move),
    ("s3-arrow-endpoints", "EqvAnnot", _g_arrow_endpoints),
    ("s4-view3d-visible", "Eqv3D", _g_view3d_and_visible),
    ("s5-mathtext-labels", "EqvMath", _g_labels_and_title),
    ("s7-collection-family", "EqvFam", _g_collection_family),
    ("s7-patch-and-stem", "EqvFam", _g_patch_family_and_stem),
    # 色条的 `tick_*` 与色条轴刻度组**刻意不在这里**：它俩覆盖的是同一批
    # 标签（不是「整组 vs 其中一个」），后应用的必然盖掉前一个，manifest 也
    # 只报得出一个值——`_assert_effect` 表达不了「两个都落地」。它的还原语义
    # 由 tests/test_worker_roundtrip.py 的别名组用例看着。
    ("s8-alias-legend", "EqvAlias", _g_alias_legend),
    ("s8-alias-bars", "EqvAlias", _g_alias_bars),
    ("s8-alias-mixed-reversed", "EqvAlias", _g_alias_mixed),
]


@pytest.mark.parametrize("case_id,stem,builder", GROUPS, ids=[g[0] for g in GROUPS])
def test_three_ways_agree(library, case_id, stem, builder):
    """热路 / 清空重放 / 全新 worker 三条腿在 manifest 几何上必须逐位一致。"""
    steps = builder(_base_getter(library, stem))
    _three_ways(library, stem, steps)


def test_annotation_arrow_never_exposes_endpoints(library):
    """annotate 的箭头即使在文字被拖走之后也不出端点（拖了会被下一帧弹回）。

    这条与 `s3-annotation-move` 那格是同一组 patch 的两个断言面：那边看几何
    四路是否一致，这边看**暴露给前端的能力**在移动之后有没有跑出来。
    """
    full = _g_annotation_text_move(None)[-1]
    w = _worker(library)
    try:
        man = _no_apply_warnings(w.override("EqvAnnot", full), "热路")["manifest"]
    finally:
        pool.discard(w)
    assert "arrow_endpoints" not in _el(man, "axes_0.texts_0.arrow")
    # 同一张图上的独立箭头照常有端点——「不出端点」是注释箭头独有的约定
    assert _el(man, "axes_0.arrows_0").get("arrow_endpoints")


def test_cjk_scenario_three_ways_agree(library, cjk_font):
    """中文标签场景：文字移动 → 图幅变化。

    没有任何 CJK 字体时 **skip 并说明**，不静默换成拉丁文本混过去。
    """
    if not cjk_font:
        pytest.skip(
            f"matplotlib 找不到任何中文字体（找过：{', '.join(_CJK_CANDIDATES)}）——s6 无法如实验证"
        )
    _three_ways(library, "EqvCJK", _g_text_then_size(None))


# ---------------------------------------------------------------------------
# 第四路：写回文件之后，全新 worker 重放仍与热态一致
# ---------------------------------------------------------------------------
REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {
            SCRIPT_NAME: {"entry": ENTRY, "cost": "light", "notes": "", "stems": list(_SCENARIOS)},
        },
    }
)


def _flask_project(tmp_path, monkeypatch, library: Path):
    """把测试图库接成一个真项目（Flask test client）。

    图库目录不能直接用 session 级的那份：写回会**原地替换**里面的 PDF，
    污染了后面所有用例的输入。这里整份拷贝一次。
    """
    from tavotto import app as m

    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / SCRIPT_NAME).write_text(
        (library / SCRIPT_NAME).read_text(encoding="utf-8"), encoding="utf-8"
    )
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    # 写回覆盖的是磁盘上**已有**的原件（真实图库里它由脚本跑出来）。
    # 只给真的会被写回的那几个 stem 造占位——`WRITE_BACK_GROUPS` 里出现过
    # 的都要在这儿，漏一个的表现是 404 而不是断言失败，很容易误读成产品问题。
    for stem in ("EqvMulti", "EqvImage", "EqvFam", "EqvAlias"):
        doc = pymupdf.open()
        doc.new_page(width=200, height=100)
        doc.save(figs / f"{stem}.pdf")
        doc.close()

    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    m.open_project(str(figs))
    return m, m.app.test_client(), figs


@pytest.fixture
def project(tmp_path, monkeypatch, library):
    from tavotto import app as m

    ctx = _flask_project(tmp_path, monkeypatch, library)
    try:
        yield ctx
    finally:
        m.reset_projects()
        pool.shutdown_all(figures_dir=str(ctx[2]), wait=True)
        pool.stop_watcher()


def _render(client, stem, patches, **extra):
    resp = client.post(
        "/api/engine/render", json={"id": f"{stem}.pdf", "patches": patches, **extra}
    )
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _all_pairs_agree(arms: dict) -> None:
    """四路**两两**比较（6 对），一次把分歧全指出来。"""
    names = list(arms)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            _assert_same(a, arms[a], b, arms[b])


#: 第四路（写回后重开）跑哪几组。混合大组合是 FigS3 事故的形状；后面三组是
#: 新增能力里**改结构**的那些（换刻度类型 / 重建刻度 / 翻转色条方向），
#: 它们才是「热态所见 ≠ 重开后重放」最可能重新出现的地方。
WRITE_BACK_GROUPS = [
    ("s1-mixed-12-patches", "EqvMulti", _g_mixed, ("Panel A", "sin")),
    ("s1-range-scale-ticks", "EqvMulti", _g_axes_range_scale_and_ticks, ("Panel A",)),
    ("s1-fixed-ticks-text", "EqvMulti", _g_fixed_ticks_and_label_text, ("mid-tick", "start")),
    ("s1-legend-item-text", "EqvMulti", _g_legend_item_text, ("sin(x)", "Series")),
    ("s2-colorbar-orientation", "EqvImage", _g_colorbar_orientation, ("intensity",)),
    # 新开放的 family 也要走一遍**写回原件 → 重开**：能改却写不回去，
    # 等于给用户一个下次打开就消失的编辑（§49）
    ("s7-collection-family", "EqvFam", _g_collection_family, ("Families",)),
    # 别名组：广播型 prop 与窄 prop 叠加。写回这条腿尤其要紧——热态是
    # 「先整体后单条」的增量，写回校验拿的是**全量重放**，两者不一致时
    # 事务会回 409 replay_divergence，正是这个 bug 当初现形的地方。
    ("s8-alias-mixed-reversed", "EqvAlias", _g_alias_mixed, ("counts", "series")),
]


@pytest.mark.parametrize(
    "case_id,stem,builder,markers", WRITE_BACK_GROUPS, ids=[g[0] for g in WRITE_BACK_GROUPS]
)
def test_write_back_then_reopen_matches_the_hot_session(
    project, library, case_id, stem, builder, markers
):
    """完整四路：热态 / 清空重放 / 全新 worker / **写回原件后重开**，两两一致。

    第四条腿走的是产品路径 `POST /api/engine/update_source`——它内部已有一次性
    worker 的干净重放校验，这里在它之外再验一遍「写回之后」：写回不得反过来
    改动热会话，也不得让下一次冷启动落到另一个状态上。
    """
    _m, client, figs = project
    steps = builder(_base_getter(library, stem))
    full = steps[-1]

    for step in steps:  # 热态：一下一下地改
        body = _render(client, stem, step)
    man_hot = body["manifest"]
    _assert_effect(man_hot, full)

    # 第二路：清空 → 一次性全量重放（同一条热会话上）
    _render(client, stem, [])
    man_replay = _render(client, stem, full)["manifest"]

    # 第三路：写回**之前**的全新 worker（此时磁盘上还是原件）
    pre = _worker(figs)
    try:
        man_fresh = _no_apply_warnings(pre.override(stem, full), "全新 worker")["manifest"]
    finally:
        pool.discard(pre)

    # 热会话被上面两步动过，写回前先把它放回热态那一组 patch
    _assert_same("热态", man_hot, "写回前热态", _render(client, stem, full)["manifest"])

    resp = client.post(
        "/api/engine/update_source",
        json={
            "id": f"{stem}.pdf",
            "patches": full,
            "expected_mtime": int((figs / f"{stem}.pdf").stat().st_mtime),
        },
    )
    assert resp.status_code == 200, resp.get_json()
    wb = resp.get_json()
    assert wb["verification"]["replay"] == "ok", wb["verification"]
    assert wb["warnings"] == []

    # 写回没有污染热会话：同一组 patch 再渲染一次，manifest 逐位不变
    _assert_same("热态", man_hot, "写回后热态", _render(client, stem, full)["manifest"])

    # 第四路本身：文件已被替换，脚本没变 → 全新 worker 全量重放
    post = _worker(figs)
    try:
        man_reopened = _no_apply_warnings(post.override(stem, full), "写回后重开")["manifest"]
    finally:
        pool.discard(post)

    _all_pairs_agree(
        {
            "热态": man_hot,
            "清空重放": man_replay,
            "全新 worker": man_fresh,
            "写回后重开": man_reopened,
        }
    )

    # 落盘的那份就是热态所见：页面尺寸对上 manifest，文字仍是矢量
    with pymupdf.open(figs / f"{stem}.pdf") as doc:
        page = doc[0]
        w_mm = page.rect.width / 72.0 * 25.4
        h_mm = page.rect.height / 72.0 * 25.4
        assert [w_mm, h_mm] == pytest.approx(man_hot["size_mm"], abs=0.2)
        text = page.get_text()
    for marker in markers:
        assert marker in text, (marker, text[:400])


# ===========================================================================
# workerd 控制面（ADR 0004）：核心场景再走一遍四路 + Phase F 点名的四条
# ===========================================================================
def _workerd_binary() -> str | None:
    """忽略 conftest 的默认禁用开关，只看 cargo 产物在不在。"""
    saved = os.environ.pop("TAVOTTO_WORKERD", None)
    try:
        from tavotto.engine import workerd_client

        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["TAVOTTO_WORKERD"] = saved


WORKERD_EXE = _workerd_binary()
needs_workerd = pytest.mark.skipif(
    WORKERD_EXE is None, reason="没有 tavotto-workerd 产物（先在 workerd/ 里 cargo build）"
)


@pytest.fixture
def workerd(monkeypatch):
    """把本用例的渲染控制面切到 Rust supervisor 上。"""
    from tavotto.engine import workerd_client

    monkeypatch.setenv("TAVOTTO_WORKERD", WORKERD_EXE or "0")
    workerd_client.reset_client()
    try:
        yield
    finally:
        workerd_client.reset_client()


@needs_workerd
@pytest.mark.parametrize(
    "case_id,stem,builder",
    [
        ("s1-mixed-12-patches", "EqvMulti", _g_mixed),
        ("s2-colorbar-range", "EqvImage", _g_colorbar_range),
    ],
    ids=["s1-mixed-12-patches", "s2-colorbar-range"],
)
def test_three_ways_agree_on_workerd(library, workerd, case_id, stem, builder):
    """核心场景在 workerd 控制面上的同一条不变式。

    两条控制面在**渲染语义**上必须逐条一致——有一条不一致，用户就会在
    「装没装 workerd」之间看到不同的图。
    """
    steps = builder(_base_getter(library, stem))
    _three_ways(library, stem, steps, expect_cls=pool.WorkerdWorker)


@needs_workerd
def test_workerd_inline_svg_is_byte_identical_to_the_file_it_just_wrote(workerd, project):
    """响应里内联的 SVG 与这一次写到磁盘上的那份**逐字节相同**。

    前端只认响应里这一份（第二跳 GET 会被别的变体挤掉），所以两者不同就是
    「元素框对不上图」。跨渲染逐字节比 SVG 没有意义（defs id 与 dc:date 每次
    都变），只在**同一次响应内**比才成立。
    """
    _m, client, figs = project
    stem = "EqvMulti"
    worker = pool.get(SCRIPT_NAME, str(figs), ENTRY)
    assert isinstance(worker, pool.WorkerdWorker), "应当走 workerd 控制面"

    body = _render(
        client,
        stem,
        [{"gid": "axes_0.title", "prop": "text", "value": "Inline SVG"}],
        inline_svg=True,
    )
    assert "svg" in body
    on_disk = worker.svg_path(stem).read_text(encoding="utf-8")
    assert body["svg"] == on_disk, "内联 SVG 与本次写盘的那份不是同一份"
    assert "Inline SVG" in body["svg"]


@needs_workerd
def test_workerd_render_without_inline_svg_has_no_svg_key(workerd, project):
    """没要就一个字段都不加——响应形状对老调用方一字不变。"""
    _m, client, figs = project
    body = _render(client, "EqvMulti", [])
    assert "svg" not in body
    assert isinstance(pool.get(SCRIPT_NAME, str(figs), ENTRY), pool.WorkerdWorker)


@needs_workerd
def test_workerd_preview_png_is_state_neutral_across_variants(workerd, project):
    """`preview_png` 按给定 patches 出图，与热会话当前是哪个变体无关。"""
    _m, client, figs = project
    assert isinstance(pool.get(SCRIPT_NAME, str(figs), ENTRY), pool.WorkerdWorker)
    a = [{"gid": "axes_0.title", "prop": "text", "value": "Variant AAA"}]
    b = [{"gid": "axes_0.title", "prop": "text", "value": "Variant BBB"}]

    def png(patches):
        resp = client.post(
            "/api/engine/preview_png", json={"id": "EqvMulti.pdf", "patches": patches, "w": 400}
        )
        assert resp.status_code == 200, resp.get_json()
        return resp.data

    _render(client, "EqvMulti", a)
    b_while_hot_is_a = png(b)
    _render(client, "EqvMulti", b)
    b_while_hot_is_b = png(b)
    a_while_hot_is_b = png(a)

    assert b_while_hot_is_a == b_while_hot_is_b, "同一组 patches 必须得到同一张图"
    assert a_while_hot_is_b != b_while_hot_is_b, "不同变体不能出同一张图"
    # 出图不许污染热会话
    man = _render(client, "EqvMulti", b)["manifest"]
    assert _field(man, "axes_0.title", "text") == "Variant BBB"


@needs_workerd
def test_workerd_write_back_of_one_variant_still_verifies(workerd, project):
    """画布上两个同文件不同 override 的面板轮流渲染，写回其中一个仍 verify ok。

    写回的干净重放走的是**一次性会话**（另一条 spawn 规格）；热会话上另一个
    变体的状态一个字节都不该漏进去。
    """
    _m, client, figs = project
    assert isinstance(pool.get(SCRIPT_NAME, str(figs), ENTRY), pool.WorkerdWorker)
    stem = "EqvMulti"
    other = [{"gid": "axes_0.title", "prop": "text", "value": "Other Variant"}]
    mine = _g_mixed(None)[-1]

    _render(client, stem, other)  # 另一个变体先占住热会话
    man_hot = _render(client, stem, mine)["manifest"]
    _render(client, stem, other)  # 再切回去
    man_hot2 = _render(client, stem, mine)["manifest"]
    _assert_same("首次热态", man_hot, "轮流之后", man_hot2)

    resp = client.post(
        "/api/engine/update_source",
        json={
            "id": f"{stem}.pdf",
            "patches": mine,
            "expected_mtime": int((figs / f"{stem}.pdf").stat().st_mtime),
        },
    )
    assert resp.status_code == 200, resp.get_json()
    wb = resp.get_json()
    assert wb["verification"]["replay"] == "ok", wb["verification"]
    assert wb["warnings"] == []
    with pymupdf.open(figs / f"{stem}.pdf") as doc:
        assert "Panel A" in doc[0].get_text()

    # 写回之后另一个变体照常可渲染（会话没被写回带走）
    assert (
        _field(_render(client, stem, other)["manifest"], "axes_0.title", "text") == "Other Variant"
    )
