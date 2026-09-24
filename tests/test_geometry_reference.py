"""几何参考尺：拖动写下的锚点 → 权威 manifest 的落点（QA 2026-09-24 GEO-01 / GEO-02 后端半）。

前端那一半（屏幕位移 → override 值）由 `web/src/canvas/geometryReference.test.ts` 看护；
这里量的是**另一半**：override 写进 worker 之后，matplotlib 真的把元素摆在了那里，
而且只动了它。两半各自用**测试侧独立实现的坐标尺**，不调用生产的坐标换算：

    T(p)  = origin + zoom · (p · W_mm · 96 / 25.4)          （CSS 像素，y 向下）
    T⁻¹(T(p) + Δs) = p + Δs / (zoom · W_mm · 96 / 25.4)

图幅 W_mm / H_mm 取自脚本里写死的 figsize（**不读** manifest 的 size_mm），所以
「manifest 报错了图幅」也会让这里红。

判据（主语：同一个 worker 进程里、一次 override 之后的那份 manifest）：

1. 目标元素的 anchor 落在独立尺算出的 p'（预算 0.5 CSS px，按最大 zoom 换算成分数）；
2. 目标元素的 bbox 整体平移同样的量、尺寸不变（墨迹框随锚点走，不是被重排）；
3. 非目标元素的 anchor / bbox 一个不动，曲线的点序列逐位不变；
4. 目标元素除位置外的可编辑字段（字号、字体、颜色……）一个不变；
5. 多选（2 / 10 / 100 个成员）同一位移：每个成员恰好落在自己的 p'，
   两两间距不变，没被选中的不动。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import itertools

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_geo_reference.py"

#: 独立尺的常量：CSS 规定 96 px / in，1 in = 25.4 mm。不 import 前端 / 后端任何换算。
CSS_PX_PER_MM = 96.0 / 25.4
#: 图幅：脚本里写死的 figsize（英寸）→ mm。与 manifest 无关。
SIZE_MM = {"G1": (5.0 * 25.4, 3.2 * 25.4), "G2big": (6.0 * 25.4, 6.0 * 25.4)}

ZOOMS = (0.75, 1.0, 1.5)
DIRECTIONS = [(dx, dy) for dx, dy in itertools.product((-1, 0, 1), repeat=2) if (dx, dy) != (0, 0)]
STEP_PX = 37.0  # 每个方向拖的屏幕 CSS 像素（非整数倍于任何网格）
BUDGET_PX = 0.5  # 规范 §0.2 的候选预算：简单图锚点 ≤ 0.5 CSS px

LIBRARY = """\
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

X = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
Y = [0.0, 9.0, 2.0, 10.0, 4.0, 7.0]


def main():
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    fig.subplots_adjust(left=0.14, right=0.93, bottom=0.17, top=0.86)
    ax.plot(X, Y, color="#1f77b4", lw=1.5, label="signal")
    ax.set_title("G1 title", loc="left")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("value")
    ax.legend(loc="lower right")
    ax.text(0.4, 8.2, "note A", color="#123456")
    ax.add_patch(FancyBboxPatch((3.3, 0.6), 0.9, 1.4, boxstyle="round,pad=0.05",
                                fc="#ffdd99", ec="#444444"))
    fig.savefig("G1.pdf")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.0, 6.0))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    for i in range(100):
        ax.text(0.3 + (i % 10) * 0.95, 0.3 + (i // 10) * 0.95, "n%02d" % i, fontsize=6)
    fig.savefig("G2big.pdf")
    plt.close(fig)

    # G8：特殊坐标——对数 x、反转 y、twinx、axes 分数坐标的注释、figure 级文字
    fig, (a, b) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    a.set_xscale("log")
    a.plot([1.0, 10.0, 100.0], [1.0, 2.0, 3.0])
    a.invert_yaxis()
    a.text(10.0, 2.5, "logtxt")
    tw = a.twinx()
    tw.plot([1.0, 100.0], [0.0, 1.0], color="#d62728")
    tw.text(30.0, 0.8, "twintxt")
    b.plot([0.0, 1.0], [0.0, 1.0])
    b.annotate("ann", xy=(0.5, 0.5), xycoords="axes fraction", xytext=(0.2, 0.8),
               textcoords="axes fraction", arrowprops={"arrowstyle": "->"})
    fig.text(0.02, 0.02, "figtxt")
    fig.savefig("G8.pdf")
    plt.close(fig)

    # G10：无布局引擎——改图幅 / 字号后，拖过的与没拖过的文字各守各的合同
    # （constrained / tight 下拖轴标签与标题的 y 分量会被布局引擎吃掉：QA 2026-09-24
    #   product bug，复现在 docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py）
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    ax.plot(X, Y)
    ax.set_title("G10 title")
    ax.set_xlabel("time (s)")
    ax.set_ylabel("value")
    fig.savefig("G10.pdf")
    plt.close(fig)
"""

#: G1 里执行前写定的「可拖对象」（drag_prop 与脚本对照）
G1_DRAGGABLE = {
    "axes_0.title_left": "pos_frac",
    "axes_0.xlabel": "pos_frac",
    "axes_0.ylabel": "pos_frac",
    "axes_0.texts_0": "pos_frac",
    "axes_0.patches_0": "pos_frac",
    "axes_0.legend": "loc_frac",
}


def frac_delta(stem: str, zoom: float, ds: tuple[float, float]) -> tuple[float, float]:
    """独立的 T⁻¹(T(p)+Δs) − p：屏幕 CSS 像素位移 → figure 分数位移（y 向下）。"""
    w_mm, h_mm = SIZE_MM[stem]
    return (
        ds[0] / (zoom * w_mm * CSS_PX_PER_MM),
        ds[1] / (zoom * h_mm * CSS_PX_PER_MM),
    )


def budget(stem: str) -> tuple[float, float]:
    """0.5 CSS px 换成分数——按**最大** zoom 算，是三档里最严的那一档。"""
    return frac_delta(stem, max(ZOOMS), (BUDGET_PX, BUDGET_PX))


@pytest.fixture(scope="module")
def hot(tmp_path_factory):
    figs = tmp_path_factory.mktemp("geo-reference")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _by_gid(man) -> dict[str, dict]:
    return {e["gid"]: e for e in man["elements"]}


def _non_position_fields(el: dict) -> dict:
    return {
        f["prop"]: f.get("value")
        for f in el.get("editable", [])
        if f["prop"] not in ("pos_frac", "loc_frac", "loc", "position", "loc_anchor")
    }


def test_g1_fixture_is_what_the_ledger_declares(hot):
    """夹具自检：可拖对象与 drag_prop 与执行前写定的一致，图幅与脚本一致。"""
    man = _by_gid(_man(hot, "G1"))
    for gid, prop in G1_DRAGGABLE.items():
        assert gid in man, gid
        assert man[gid]["draggable"] is True, gid
        assert man[gid]["drag_prop"] == prop, gid
        assert man[gid]["anchor"] is not None, gid
    size = _man(hot, "G1")["size_mm"]
    assert size == pytest.approx(SIZE_MM["G1"], abs=1e-6)


@pytest.mark.parametrize("gid", sorted(G1_DRAGGABLE))
def test_single_drag_lands_on_the_independent_ruler(hot, gid):
    """GEO-01：8 个方向 × 3 档 zoom，锚点落在 T⁻¹(T(p)+Δs)，只有目标动了。"""
    base = _by_gid(_man(hot, "G1"))
    el0 = base[gid]
    prop = G1_DRAGGABLE[gid]
    tol_x, tol_y = budget("G1")
    worst = worst_box = 0.0
    for zoom in ZOOMS:
        for ux, uy in DIRECTIONS:
            dfx, dfy = frac_delta("G1", zoom, (ux * STEP_PX, uy * STEP_PX))
            target = [el0["anchor"][0] + dfx, el0["anchor"][1] + dfy]
            man = _by_gid(_man(hot, "G1", [{"gid": gid, "prop": prop, "value": target}]))
            el = man[gid]
            where = f"{gid} zoom={zoom} dir=({ux},{uy})"
            # 1. 锚点
            assert el["anchor"][0] == pytest.approx(target[0], abs=tol_x), where
            assert el["anchor"][1] == pytest.approx(target[1], abs=tol_y), where
            worst = max(worst, abs(el["anchor"][0] - target[0]), abs(el["anchor"][1] - target[1]))
            worst_box = max(
                worst_box,
                abs(el["bbox"][0] - el0["bbox"][0] - dfx),
                abs(el["bbox"][1] - el0["bbox"][1] - dfy),
            )
            # 2. 墨迹框整体平移、尺寸不变
            assert el["bbox"][0] - el0["bbox"][0] == pytest.approx(dfx, abs=tol_x), where
            assert el["bbox"][1] - el0["bbox"][1] == pytest.approx(dfy, abs=tol_y), where
            assert el["bbox"][2:] == pytest.approx(el0["bbox"][2:], abs=1e-6), where
            # 4. 目标除位置外的属性原样
            assert _non_position_fields(el) == _non_position_fields(el0), where
            # 3. 非目标：锚点 / 墨迹框 / 曲线点序列都不动
            for other_gid, other0 in base.items():
                if other_gid == gid or other_gid in ("figure", "axes_0"):
                    continue
                other = man[other_gid]
                if other_gid.startswith(gid + "."):
                    # 后代（图例条目文字）随父走：同一个位移，恰好一次（不是零次、也不是两次）
                    assert other["bbox"][0] - other0["bbox"][0] == pytest.approx(dfx, abs=tol_x), (
                        f"{where} 后代 {other_gid} 没跟上 / 走了两次"
                    )
                    assert other["bbox"][1] - other0["bbox"][1] == pytest.approx(dfy, abs=tol_y), (
                        f"{where} 后代 {other_gid} 没跟上 / 走了两次"
                    )
                    continue
                assert other.get("anchor") == other0.get("anchor"), f"{where} 连带动了 {other_gid}"
                assert other["bbox"] == pytest.approx(other0["bbox"], abs=1e-9), (
                    f"{where} 连带动了 {other_gid}"
                )
                if "geometry" in other0:
                    assert other.get("geometry") == other0["geometry"], (
                        f"{where} 改了 {other_gid} 的点"
                    )
    # 还原：空 patch 回到脚本原样（下一个参数化用例的基线不被污染）
    back = _by_gid(_man(hot, "G1"))
    assert back[gid]["anchor"] == pytest.approx(el0["anchor"], abs=1e-9)
    # 校准记录：实测最大误差远小于预算，预算不是为了容纳漂移而放宽的
    print(
        f"[calibration] {gid} worst |anchor - target| = {worst:.3e}, "
        f"worst bbox shift error = {worst_box:.3e} (budget x={tol_x:.3e}, y={tol_y:.3e})"
    )


@pytest.mark.parametrize("count", [2, 10, 100])
def test_group_move_moves_every_member_exactly_once(hot, count):
    """GEO-02：2 / 10 / 100 个成员同一屏幕位移——每个成员一次、间距不变、其余不动。"""
    base = _by_gid(_man(hot, "G2big"))
    texts = sorted(
        (g for g, e in base.items() if e["role"] == "text" and e.get("drag_prop") == "pos_frac"),
        key=lambda g: int(g.rsplit("_", 1)[1]),
    )
    assert len(texts) == 100, f"G2big 夹具应当有 100 个可拖文字，实际 {len(texts)}"
    # 均匀取样而不是取前 count 个：选中的散布在整张图上
    picked = texts[:: 100 // count][:count]
    dfx, dfy = frac_delta("G2big", 1.5, (23.0, -41.0))
    # 前端整组平移写的是 round4 之后的值（elementGeom.alignEntries.write），这里同样
    patches = [
        {
            "gid": g,
            "prop": "pos_frac",
            "value": [round(base[g]["anchor"][0] + dfx, 4), round(base[g]["anchor"][1] + dfy, 4)],
        }
        for g in picked
    ]
    man = _by_gid(_man(hot, "G2big", patches))
    tol_x, tol_y = budget("G2big")
    for g in picked:
        assert man[g]["anchor"][0] - base[g]["anchor"][0] == pytest.approx(dfx, abs=tol_x), g
        assert man[g]["anchor"][1] - base[g]["anchor"][1] == pytest.approx(dfy, abs=tol_y), g
    # 两两间距不变（相对第一个成员）
    ref = picked[0]
    for g in picked[1:]:
        for k in (0, 1):
            before = base[g]["anchor"][k] - base[ref]["anchor"][k]
            after = man[g]["anchor"][k] - man[ref]["anchor"][k]
            assert after == pytest.approx(before, abs=2 * max(tol_x, tol_y)), (g, k)
    # 没被选中的一个不动
    for g in texts:
        if g in picked:
            continue
        assert man[g]["anchor"] == base[g]["anchor"], f"未选中的 {g} 被挪动了"
    _man(hot, "G2big")


def test_axes_and_its_own_title_moved_together_move_the_title_once(hot):
    """GEO-03 后端半：Axes 与它的标题同时平移——标题在权威 manifest 里只走一次 Δ。

    两种写法都要成立：
    * 只挪 Axes（标题没被单独摆过）：标题作为孩子随 Axes 走 Δ；
    * Axes 写 position + 标题写 anchor+Δ（图内整组平移的实际写法）：标题仍只走 Δ，不是 2Δ。
    """
    base = _by_gid(_man(hot, "G1"))
    axes0 = next(f["value"] for f in base["axes_0"]["editable"] if f["prop"] == "position")
    title0 = base["axes_0.title_left"]
    dfx, dfy = frac_delta("G1", 1.0, (29.0, 17.0))
    tol_x, tol_y = budget("G1")
    # position 是 bottom-origin：屏幕往下 = y 变小
    moved = [round(axes0[0] + dfx, 4), round(axes0[1] - dfy, 4), axes0[2], axes0[3]]
    moved_axes = {"gid": "axes_0", "prop": "position", "value": moved}
    # 实际写进去的位移（round4 之后）
    rdx = moved[0] - axes0[0]
    rdy = axes0[1] - moved[1]

    t = _by_gid(_man(hot, "G1", [moved_axes]))["axes_0.title_left"]
    assert t["anchor"][0] - title0["anchor"][0] == pytest.approx(rdx, abs=tol_x)
    assert t["anchor"][1] - title0["anchor"][1] == pytest.approx(rdy, abs=tol_y)

    title_patch = {
        "gid": "axes_0.title_left",
        "prop": "pos_frac",
        "value": [round(title0["anchor"][0] + dfx, 4), round(title0["anchor"][1] + dfy, 4)],
    }
    t2 = _by_gid(_man(hot, "G1", [moved_axes, title_patch]))["axes_0.title_left"]
    assert t2["anchor"][0] - title0["anchor"][0] == pytest.approx(dfx, abs=tol_x)
    assert t2["anchor"][1] - title0["anchor"][1] == pytest.approx(dfy, abs=tol_y)
    _man(hot, "G1")


SIZE_MM["G8"] = (6.0 * 25.4, 3.0 * 25.4)
SIZE_MM["G10"] = (5.0 * 25.4, 3.2 * 25.4)


#: G8 里脚本写定可拖、且落点合同成立的三段文字。**注释（axes_1.texts_0，textcoords 为
#: axes fraction）不在这里**：它写 pos_frac 后落点错位（QA 2026-09-24 product bug，复现
#: `docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py`），修好之后加回来。
G8_TEXTS = ("axes_0.texts_0", "axes_2.texts_0", "fig.texts_0")


@pytest.mark.parametrize("gid", G8_TEXTS)
def test_special_coordinates_drag_is_scale_independent(hot, gid):
    """GEO-08：对数 x + 反转 y 上的文字、twinx 上的文字、figure 级文字——拖动写的是 figure
    分数锚点，落点不套数据坐标公式；数据曲线、轴属性（对数刻度、反转）一样不变。"""
    base = _by_gid(_man(hot, "G8"))
    e0 = base[gid]
    assert e0["draggable"] is True and e0["drag_prop"] == "pos_frac", e0
    lines0 = {g: e.get("geometry") for g, e in base.items() if e["role"] == "line"}
    ax_fields0 = {g: _non_position_fields(e) for g, e in base.items() if e["role"] == "axes"}
    tol_x, tol_y = budget("G8")
    for ux, uy in DIRECTIONS:
        dfx, dfy = frac_delta("G8", 1.37, (ux * STEP_PX, uy * STEP_PX))
        target = [e0["anchor"][0] + dfx, e0["anchor"][1] + dfy]
        man = _by_gid(_man(hot, "G8", [{"gid": gid, "prop": "pos_frac", "value": target}]))
        where = f"{gid} dir=({ux},{uy})"
        assert man[gid]["anchor"][0] == pytest.approx(target[0], abs=tol_x), where
        assert man[gid]["anchor"][1] == pytest.approx(target[1], abs=tol_y), where
        for g, geom in lines0.items():
            assert man[g].get("geometry") == geom, f"{where} 改了曲线 {g}"
        for g, fields in ax_fields0.items():
            assert _non_position_fields(man[g]) == fields, f"{where} 改了 {g} 的轴属性"
    _man(hot, "G8")


def test_moved_label_keeps_its_anchor_and_unmoved_text_keeps_reflowing(hot):
    """GEO-10：改图幅与字号——拖过的 y 轴标签守住自己的 figure 分数锚点，没拖过的 x 轴标签
    照常跟着排版走（与「没拖过任何东西」的对照组逐位相同）。

    只覆盖**无布局引擎**的图：constrained / tight 布局下拖标题 / 轴标签的 y 分量不生效
    （QA 2026-09-24 product bug，复现 `docs/qa/2026-09-24/geo/repro/repro_title_and_annotation_drag.py`），
    修好之后把 G10 换回 constrained。
    """
    base = _by_gid(_man(hot, "G10"))
    ylabel0 = base["axes_0.ylabel"]
    xlabel0 = base["axes_0.xlabel"]
    reflow = [
        {"gid": "figure", "prop": "size_mm", "value": [150.0, 70.0]},
        {"gid": "axes_0.xlabel", "prop": "fontsize", "value": 16.0},
        {"gid": "axes_0.ylabel", "prop": "fontsize", "value": 14.0},
    ]
    target = [round(ylabel0["anchor"][0] + 0.03, 4), round(ylabel0["anchor"][1] - 0.1, 4)]
    moved = {"gid": "axes_0.ylabel", "prop": "pos_frac", "value": target}

    control = _by_gid(_man(hot, "G10", reflow))
    treated = _by_gid(_man(hot, "G10", [moved, *reflow]))
    tol_x, tol_y = budget("G10")
    # 拖过的：锚点仍是写下的那个 figure 分数
    assert treated["axes_0.ylabel"]["anchor"][0] == pytest.approx(target[0], abs=tol_x)
    assert treated["axes_0.ylabel"]["anchor"][1] == pytest.approx(target[1], abs=tol_y)
    # 没拖过的：确实被重排了（改图幅 / 字号之后不在原处）……
    assert control["axes_0.xlabel"]["anchor"] != pytest.approx(xlabel0["anchor"], abs=1e-3)
    # ……而且拖 y 轴标签这件事没有把它锁住：与对照组逐位相同
    assert treated["axes_0.xlabel"]["anchor"] == pytest.approx(
        control["axes_0.xlabel"]["anchor"], abs=1e-9
    )
    _man(hot, "G10")
