"""拖过的文字落在写下的 figure 分数上（QA 2026-09-24 GEO-B1 / GEO-B2，Refs #583）。

两类「manifest 说可拖、写了 `pos_frac`、落点却不对」的文字：

* **GEO-B1**：`ax.annotate(..., textcoords=<非 data>)` 的注释文字。setter 拿注释上一次 draw
  冻下来的 transform 快照去逆算（预览 SVG 按 72 dpi 画、manifest 按 figure dpi 画），
  落点按 dpi 之比错位 0.09–0.33 figure 分数。
* **GEO-B2**：`layout="constrained"` / `"tight"` 的图里拖标题、轴标签、子图里的文字、
  suptitle——布局引擎在下一次 draw 里按「文字挪过之后」的包围盒重排子图，跟着子图走的
  文字被带走，y 分量等于没写；之后改图幅再漂一次。

判据的主语：**同一个热 worker** 里一次 override 之后那份 manifest 中目标元素的 `anchor`
（figure 分数、y 向下）。期望值是**测试侧独立算的**：基线 manifest 的 anchor 加上一个写死的
位移——不调用任何生产坐标换算。另外三条合同：

1. 没拖的元素（子图框、别的文字）与「同样的图幅 / 字号改动、但什么都没拖」的对照组逐位相同
   ——拖一个标题不许让子图跳（GEO-10「拖过的保持、没拖过的继续参与自动布局」）；
2. 同一份列表再渲染一次不漂（热态不追着布局跑），一次性 worker 全量重放落在同一处
   （写回事务不变式：热态所见 == 重开后重放出来的）；
3. 撤销（空列表）逐位回到基线。

坐标系逆算不回去 / 随 dpi 漂的注释（'offset pixels'）不宣称可拖——宣称了就是静默落错。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_text_drag_anchor.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _fig(stem, figfrac=False, **kw):
    fig, ax = plt.subplots(figsize=(5.0, 3.2), **kw)
    ax.plot([0, 1, 2], [0, 1, 0])
    ax.set_title("title " + stem)
    ax.set_xlabel("x label")
    ax.set_ylabel("y label")
    ax.text(0.3, 0.8, "plain text")
    ax.annotate("ann data", xy=(1, 1), xytext=(1.5, 0.6), arrowprops={"arrowstyle": "->"})
    ax.annotate("ann axfrac", xy=(0.5, 0.5), xycoords="axes fraction", xytext=(0.2, 0.3),
                textcoords="axes fraction", arrowprops={"arrowstyle": "->"})
    ax.annotate("ann offset", xy=(0.5, 0.2), xytext=(20, 10), textcoords="offset points")
    ax.annotate("ann offpx", xy=(0.5, 0.2), xytext=(15, -12), textcoords="offset pixels")
    if figfrac:
        ax.annotate("ann figfrac", xy=(0.5, 0.5), xytext=(0.7, 0.9), textcoords="figure fraction")
    fig.suptitle("sup " + stem)
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _fig("N", figfrac=True)
    _fig("C", layout="constrained")
    _fig("T", layout="tight")
"""

#: 脚本里写定的 gid（`ax.texts` 的登记序号 = 创建顺序；suptitle 是 `fig.texts_0`）
TEXTS = {
    "plain": "axes_0.texts_0",
    "ann_data": "axes_0.texts_1",
    "ann_axfrac": "axes_0.texts_2",
    "ann_offset": "axes_0.texts_3",
    "ann_offpx": "axes_0.texts_4",
    "ann_figfrac": "axes_0.texts_5",
    "title": "axes_0.title",
    "xlabel": "axes_0.xlabel",
    "ylabel": "axes_0.ylabel",
    "suptitle": "fig.texts_0",
}
DRAGGABLE = [k for k in TEXTS if k not in ("ann_offpx", "ann_figfrac")]

#: 写死的位移（figure 分数，y 向下）：左移、下移，都不是任何网格的整数倍
DELTA = (-0.05, 0.04)
#: 锚点预算。正确实现的实测误差 ~1e-16；缺陷的误差 ≥ 0.03
TOL = 1e-6
#: 'figure fraction' 的注释只放在没有布局引擎的图里：它**没拖过**时就会让 constrained 布局
#: 每画一次挪一次子图（上游性质：钉在 figure 上的注释进了布局，边距不收敛；与拖动无关，
#: 实测零 override 连画四次子图高度 0.55 → 0.45 → 0.34 → 0.24）。
SIZE = {"gid": "figure", "prop": "size_mm", "value": [150.0, 70.0]}


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("text-drag-anchor")
    (d / SCRIPT_NAME).write_text(SCRIPT, encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def hot(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def _target(base, gid):
    a = base[gid]["anchor"]
    return [a[0] + DELTA[0], a[1] + DELTA[1]]


def _assert_lands(man, gid, target, where):
    got = man[gid]["anchor"]
    assert got[0] == pytest.approx(target[0], abs=TOL), f"{where}: x {got} vs {target}"
    assert got[1] == pytest.approx(target[1], abs=TOL), f"{where}: y {got} vs {target}"


def _others(man, gid):
    """「没拖的」那部分：别的元素的 anchor / bbox。拖的那段文字与它自己的箭头除外。"""
    return {
        g: (e.get("anchor"), e.get("bbox"))
        for g, e in man.items()
        if g != gid and g != gid + ".arrow" and e["role"] not in ("ticks", "ticklabel")
    }


def _assert_same(a: dict, b: dict, where: str, tol: float) -> None:
    assert a.keys() == b.keys(), where
    for g in a:
        for va, vb in zip(a[g], b[g], strict=True):
            if va is None or vb is None:
                assert va is vb, f"{where}: {g}"
                continue
            assert vb == pytest.approx(va, abs=tol), f"{where}: {g} {va} → {vb}"


@pytest.mark.parametrize(
    ("stem", "key"),
    [(s, k) for s in ("N", "C", "T") for k in DRAGGABLE] + [("N", "ann_figfrac")],
)
def test_dragged_text_lands_on_the_written_anchor(hot, stem, key):
    """GEO-B1 + GEO-B2：写下的 figure 分数就是 manifest 量到的落点——有没有布局引擎、
    改没改图幅都一样；同一份列表再渲染一次不漂。"""
    gid = TEXTS[key]
    base = _man(hot, stem)
    assert base[gid]["draggable"] is True and base[gid]["drag_prop"] == "pos_frac", base[gid]
    target = _target(base, gid)
    moved = {"gid": gid, "prop": "pos_frac", "value": target}
    for extra in ((), (SIZE,)):
        where = f"{stem} {key} size={bool(extra)}"
        _assert_lands(_man(hot, stem, [moved, *extra]), gid, target, where)
        _assert_lands(_man(hot, stem, [moved, *extra]), gid, target, where + " 再渲染")
    # 撤销逐位回到基线
    back = _man(hot, stem)
    assert back[gid]["anchor"] == pytest.approx(base[gid]["anchor"], abs=1e-9)
    _assert_same(_others(base, gid), _others(back, gid), f"{stem} {key} 撤销", 1e-9)


@pytest.mark.parametrize("stem", ["C", "T"])
@pytest.mark.parametrize("key", ["title", "xlabel", "ylabel", "plain", "ann_axfrac", "suptitle"])
def test_dragging_one_text_does_not_reflow_the_rest(hot, stem, key):
    """GEO-10 在布局引擎下：拖过的守住锚点，没拖过的与「什么都没拖、同样改了图幅」的对照组
    逐位相同——布局引擎的输入里看不见那次拖动。"""
    gid = TEXTS[key]
    base = _man(hot, stem)
    target = _target(base, gid)
    moved = {"gid": gid, "prop": "pos_frac", "value": target}
    for extra in ((), (SIZE,)):
        where = f"{stem} {key} size={bool(extra)}"
        # 各画两遍、比第二遍：持久 tight 图改图幅之后的**第一次**渲染与之后的不一样
        # （上游性质：ylabel 落点要多画一次才稳，零 override 实测 x0 0.1122 → 0.1270
        # 之后不再变；ADR 0042 那段记着同一件事）。比「都稳定之后」的两张才是在比拖动。
        _man(hot, stem, list(extra))
        control = _man(hot, stem, list(extra))
        _man(hot, stem, [moved, *extra])
        treated = _man(hot, stem, [moved, *extra])
        _assert_lands(treated, gid, target, where)
        _assert_same(_others(control, gid), _others(treated, gid), where, 1e-9)
    _man(hot, stem)


@pytest.mark.parametrize("stem", ["C", "T"])
def test_fresh_replay_matches_the_hot_session(hot, figs, stem):
    """写回事务不变式：热态逐步拖（先标题、再轴标签、再改图幅）与一次性 worker 全量重放
    落成同一张图。"""
    base = _man(hot, stem)
    patches = []
    hot_man = None
    for key in ("title", "xlabel", "ann_axfrac"):
        gid = TEXTS[key]
        patches.append({"gid": gid, "prop": "pos_frac", "value": _target(base, gid)})
        hot_man = _man(hot, stem, patches)
    patches.append(SIZE)
    hot_man = _man(hot, stem, patches)
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        fresh.ensure_built()
        replay = _man(fresh, stem, patches)
    finally:
        pool.discard(fresh)
    _man(hot, stem)
    for p in patches[:-1]:
        _assert_lands(replay, p["gid"], p["value"], f"{stem} 重放 {p['gid']}")
    _assert_same(
        {g: (e.get("anchor"), e.get("bbox")) for g, e in hot_man.items()},
        {g: (e.get("anchor"), e.get("bbox")) for g, e in replay.items()},
        f"{stem} 热态 vs 重放",
        1e-9,
    )


def test_annotation_in_pixel_coords_is_not_claimed_draggable(hot):
    """'offset pixels' 的注释：写进去的像素随导出 dpi 漂，manifest 不宣称可拖；硬写一条
    `pos_frac` 得到的是明确的 warning，不是静默落到别处。"""
    gid = TEXTS["ann_offpx"]
    base = _man(hot, "N")
    assert base[gid]["draggable"] is False
    resp = hot.override("N", [{"gid": gid, "prop": "pos_frac", "value": [0.5, 0.5]}])
    assert resp.get("warnings"), "不支持的坐标系必须给 warning"
    _man(hot, "N")
