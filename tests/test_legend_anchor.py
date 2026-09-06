"""图例外侧锚点（ADR 0034 的 2026-09-07 修订）。

matplotlib 把图例放到子图外面靠 `bbox_to_anchor` + `loc` 两件东西一起说：
`loc='upper left', bbox_to_anchor=(1.02, 1)` 是「图例的左上角贴在子图右边缘
往外 2% 的那条线上」。Tavotto 把它做成一条独立 override `loc_anchor`
（父容器分数坐标里的一个点，`null` = 没有锚框 = 回到子图内侧）。

本文件钉住的合同：

1. manifest 发当前锚点（override 之后的渲染态）与能力（`editable` 里有它）；
2. 写一个锚点 → 图例真的跑到子图外面；写 `null` → 回到内侧；
3. **应用顺序不影响结果**：`loc` 与 `loc_anchor` 两条 patch 换个顺序发，
   画出来的像素逐字节相同（三条位置 prop 共用一个模型的全部理由）；
4. 撤掉锚点那条 = 退回脚本原样（脚本自己写了锚框的图，回到脚本那个锚框，
   而不是「没有锚框」）；
5. 热态 == 全新 worker 一次性重放（写回自检的前提）；
6. 表达不出来的锚框（4 元组 / 非父容器变换）**不发字段**，改发一条
   `unsupported_props`；
7. 外侧图例超出图幅时预检 `element-outside-figure` 命中，收回来就不命中。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool, preflight, profiles

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_legend_anchor.py"
ENTRY = "main"

#: 三个 stem 分别是三种「脚本原样」：图例在内侧、脚本自己钉了外侧锚点、
#: 脚本用了这个模型摆不出来的锚框形状。
PLAIN, ANCHORED, EXOTIC = "Plain", "Anchored", "Exotic"
LEG = "axes_0.legend"

LIBRARY = """\
import matplotlib.pyplot as plt


def _axes(name):
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0.0, 1.0], [0.0, 1.0], label="rise")
    ax.plot([0.0, 1.0], [1.0, 0.0], label="fall")
    return fig, ax


def main():
    fig, ax = _axes("plain")
    ax.legend()
    fig.savefig("Plain.pdf")

    fig, ax = _axes("anchored")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
    fig.savefig("Anchored.pdf")

    fig, ax = _axes("exotic")
    # 4 元组锚框：这个模型只认「一个点」，摆不出来
    ax.legend(loc="upper left", bbox_to_anchor=(0.1, 0.1, 0.5, 0.5))
    fig.savefig("Exotic.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("legend-anchor")
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


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _fresh(figs, stem, patches=()):
    w = _worker(figs)
    try:
        return _man(w, stem, patches)
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


def _png(worker, stem, patches, tag):
    return worker.preview_png(stem, list(patches), 380, tag).read_bytes()


def _anchor(gid, value):
    return {"gid": gid, "prop": "loc_anchor", "value": value}


def _loc(gid, value):
    return {"gid": gid, "prop": "loc", "value": value}


# ---------------------------------------------------------------------------
# 1. 能力与当前值
# ---------------------------------------------------------------------------
def test_manifest_reports_the_anchor_as_a_point_in_container_fractions(hot):
    """脚本没写锚框 → `null`；脚本写了 `(1.02, 1)` → 原样报出来。

    单位是**父容器（子图）的分数坐标**，不是 figure 分数——`(1.02, 1)` 的
    「1.02」正是「子图右边缘往外 2%」。
    """
    plain = _fields(_man(hot, PLAIN), LEG)["loc_anchor"]
    assert plain["value"] is None
    assert plain["type"] == "pair"

    anchored = _man(hot, ANCHORED)
    assert _val(anchored, LEG, "loc_anchor") == [1.02, 1.0]
    assert _val(anchored, LEG, "loc") == "upper left"


def test_the_capability_sits_next_to_loc_because_they_are_one_control(hot):
    """`loc_anchor` 紧跟着 `loc`：界面上它们是同一个控件的内 / 外两带。"""
    props = [f["prop"] for f in _el(_man(hot, PLAIN), LEG)["editable"]]
    assert props[:2] == ["loc", "loc_anchor"]


# ---------------------------------------------------------------------------
# 2. 写进去真的动
# ---------------------------------------------------------------------------
def test_writing_an_anchor_moves_the_legend_outside_the_axes(hot):
    """锚点 + loc 一起写 → 图例的框跑到子图右边缘外面。"""
    base = _man(hot, PLAIN)
    inside = _el(base, LEG)["bbox"]
    axes_box = _el(base, "axes_0")["bbox"]

    out = _man(hot, PLAIN, [_loc(LEG, "upper left"), _anchor(LEG, [1.02, 1.0])])
    outside = _el(out, LEG)["bbox"]
    assert _val(out, LEG, "loc_anchor") == [1.02, 1.0]
    # 图例左边缘在子图右边缘之外（figure 分数，x 向右）
    assert outside[0] > axes_box[0] + axes_box[2]
    assert outside[0] > inside[0]
    _man(hot, PLAIN)  # 全撤，别把状态留给下一个用例


def test_writing_null_brings_a_script_anchored_legend_back_inside(hot):
    """`null` 是一个**取值**（不要锚框），不是「没表态」。

    脚本自己钉了外侧锚点的图，只有写 `null` 才回得到子图内侧——「没表态」
    会落回脚本那个锚框。
    """
    axes_box = _el(_man(hot, ANCHORED), "axes_0")["bbox"]
    back = _man(hot, ANCHORED, [_anchor(LEG, None)])
    box = _el(back, LEG)["bbox"]
    assert _val(back, LEG, "loc_anchor") is None
    assert box[0] + box[2] <= axes_box[0] + axes_box[2] + 1e-6, "图例应回到子图内侧"
    _man(hot, ANCHORED)


# ---------------------------------------------------------------------------
# 3. 应用顺序无关（三条位置 prop 共用一个模型的全部理由）
# ---------------------------------------------------------------------------
def test_patch_order_does_not_change_the_picture(hot):
    """`loc` 与 `loc_anchor` 换个顺序发，画出来的必须逐字节相同。

    模型化之前它们是两个互相盖写的 setter（`set_loc` 前要清锚框），谁在
    列表里靠后谁赢——热会话的增量应用与全量重放于是在这里分叉，而写回自检
    的几何容差（0.5% figure 分数）未必拦得住。
    """
    a = [_loc(LEG, "upper left"), _anchor(LEG, [1.02, 1.0])]
    b = [_anchor(LEG, [1.02, 1.0]), _loc(LEG, "upper left")]
    assert _png(hot, PLAIN, a, "order-a") == _png(hot, PLAIN, b, "order-b")


def test_a_drag_wins_over_the_anchor_and_clears_it(hot):
    """拖动是绝对定位：`loc_frac` 在场时锚框强制清掉。

    留着锚框的话那个点会被解释成「相对锚框」，图例当场飞出画面——这正是
    模型把优先级写在一处、而不是写在三个 setter 里的原因。
    """
    both = _man(
        hot,
        PLAIN,
        [_anchor(LEG, [1.02, 1.0]), {"gid": LEG, "prop": "loc_frac", "value": [0.2, 0.3]}],
    )
    only_drag = _man(hot, PLAIN, [{"gid": LEG, "prop": "loc_frac", "value": [0.2, 0.3]}])
    assert _el(both, LEG)["bbox"] == pytest.approx(_el(only_drag, LEG)["bbox"], abs=1e-9)
    assert _val(both, LEG, "loc_anchor") is None
    _man(hot, PLAIN)


# ---------------------------------------------------------------------------
# 4. 撤销：退回脚本原样，不是退回「没有锚框」
# ---------------------------------------------------------------------------
def test_undoing_the_anchor_returns_to_the_script_not_to_no_anchor(hot):
    """脚本原样是「外侧锚点」的图，撤掉 override 要回到那个锚点。"""
    base = _el(_man(hot, ANCHORED), LEG)["bbox"]
    _man(hot, ANCHORED, [_anchor(LEG, [1.30, 0.5])])
    back = _man(hot, ANCHORED)
    assert _val(back, LEG, "loc_anchor") == [1.02, 1.0]
    assert _el(back, LEG)["bbox"] == pytest.approx(base, abs=1e-9)


def test_undoing_loc_alone_keeps_the_anchor_the_user_asked_for(hot):
    """两条槽位互不牵连：撤掉 `loc`，锚点还在（回到脚本的 loc + 用户的锚点）。"""
    man = _man(hot, PLAIN, [_loc(LEG, "lower left"), _anchor(LEG, [1.02, 0.0])])
    assert _val(man, LEG, "loc") == "lower left"
    man = _man(hot, PLAIN, [_anchor(LEG, [1.02, 0.0])])
    assert _val(man, LEG, "loc_anchor") == [1.02, 0.0]
    assert _val(man, LEG, "loc") == "best", "loc 退回未表态 = 脚本原样"
    _man(hot, PLAIN)


# ---------------------------------------------------------------------------
# 5. 热态 == 全新重放（写回自检的前提）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "case_id, patches",
    [
        ("anchor-only", [_anchor(LEG, [1.02, 1.0])]),
        ("anchor+loc", [_loc(LEG, "center left"), _anchor(LEG, [1.02, 0.5])]),
        ("null-anchor", [_anchor(LEG, None)]),
    ],
)
def test_hot_equals_a_fresh_replay(hot, library, case_id, patches):
    for stem in (PLAIN, ANCHORED):
        hot_boxes = {e["gid"]: e["bbox"] for e in _man(hot, stem, patches)["elements"]}
        cold_boxes = {e["gid"]: e["bbox"] for e in _fresh(library, stem, patches)["elements"]}
        assert hot_boxes.keys() == cold_boxes.keys(), f"{stem} {case_id}"
        for gid, box in hot_boxes.items():
            assert box == pytest.approx(cold_boxes[gid], abs=1e-9), f"{stem} {case_id} {gid}"
        _man(hot, stem)


# ---------------------------------------------------------------------------
# 6. 摆不出来的锚框：不发字段，但说得出为什么
# ---------------------------------------------------------------------------
def test_an_anchor_shape_this_model_cannot_express_is_declared_unsupported(hot):
    """4 元组锚框：字段让出来 + 一条 `unsupported_props`。

    显示成「没有锚点」是个**语义错的精确值**——用户会以为图例在内侧。
    """
    el = _el(_man(hot, EXOTIC), LEG)
    assert "loc_anchor" not in {f["prop"] for f in el["editable"]}
    assert el["unsupported_props"] == [{"prop": "loc_anchor", "reason": "legend_anchor_box"}]
    # 脚本原样照常渲染：没人写 override 就没人动它
    assert _val(_man(hot, EXOTIC), LEG, "loc") == "upper left"


# ---------------------------------------------------------------------------
# 7. 放到外面会超出图幅——预检要说出来
# ---------------------------------------------------------------------------
def _outside_ids(man):
    p = profiles.load("lab-publication-v1")
    spec = preflight.spec_from_manifest(man)
    return [i for i in preflight.run(spec, p) if i["id"] == "element-outside-figure"]


def test_an_outside_legend_that_runs_past_the_page_is_flagged(hot):
    """外侧图例很容易探出图幅，导出时那一块会被**静默**裁掉。

    `element-outside-figure`（审计 T14，error 级）就是那句话；判据是元素的
    bbox 超出 [0, 1]，与锚点没有任何专门耦合——这里验的是**这条路真的会
    走到它**，不是它的实现。
    """
    outside = _man(hot, PLAIN, [_loc(LEG, "upper left"), _anchor(LEG, [1.02, 1.0])])
    hits = _outside_ids(outside)
    assert len(hits) == 1, f"该报一条图例超出图幅，实际：{hits}"
    # 图例与它的两条项探出的是同一块，预检按「最糟的那一边」并成一条，
    # gids 里三个都在（点哪一个都定位得到）
    assert LEG in hits[0]["gids"]
    assert hits[0]["severity"] == "error"
    assert hits[0]["detail"]["side"] == "right"

    # 反例：同一张图，锚点收回子图内侧 → 不报
    inside = _man(hot, PLAIN, [_loc(LEG, "upper left"), _anchor(LEG, [0.55, 0.95])])
    assert _outside_ids(inside) == []
    _man(hot, PLAIN)
