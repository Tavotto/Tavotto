"""色条的两项结构改造：**方向**与**两端的延伸三角（extend）**。

两者都不是普通 setter——改一个属性不会让图上有任何变化，必须连带重算布局、
重建色带、把刻度换到另一条轴上。

不许出现的几种假修复（这些用例逐条把它们钉死）：

* 只写 `cb.orientation` —— 图上什么都不变，manifest 却报 horizontal；
* 销毁重建色条轴 —— `fig.axes` 换了对象，全图 `axes_i` 编号跟着漂，
  已有 override 与撤销全废；
* 只翻 manifest 不翻刻度 —— 刻度还留在原来那条轴上。

实现走第三条：同一个 Axes 对象原地改造（换 orientation/ticklocation → 重算
落位 → `_reset_locator_formatter_scale` + `_draw_all` → 长轴标签搬家）。
`fig.axes` 顺序一个字节不动 → gid 稳定 → 撤销 / 写回 / 重开全链路照旧。

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

SCRIPT_NAME = "fig_cbar.py"
ENTRY = "main"
STEM = "CbarFig"
CB = "axes_1.colorbar"          # 色条伪元素（宿主是 axes_0，色条轴是 axes_1）
CBAX = "axes_1"

#: 第二张图与第一张**除了 extend 之外一模一样**：`extend` 事务做完的落位
#: 必须与「一开始就这么建」逐位相同，这张图就是那把尺子。
STEM_NATIVE = "CbarNative"

#: 色条在**左边**的那张。`fig.colorbar(location="left")` 是完全合法的写法，
#: 而翻转的落位规则一度只会算出 right/bottom。
STEM_LEFT = "CbarLeft"

LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt


def _draw(stem, **kw):
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    im = ax.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    cb = fig.colorbar(im, ax=ax, **kw)
    cb.set_label("intensity")
    ax.set_title("Map")
    fig.savefig(stem + ".pdf")


def main():
    _draw("CbarFig")
    _draw("CbarNative", extend="both")
    _draw("CbarLeft", location="left")
'''


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cbar-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    return w


def _render(figs, patches=(), stem=STEM):
    w = _worker(figs)
    try:
        resp = w.override(stem, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _field(man, gid, prop):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == prop)


def _tick_gids(man, ax_gid, which):
    return [e["gid"] for e in man["elements"]
            if e["role"] == "ticklabel" and e["gid"].startswith(f"{ax_gid}.{which}tick")]


HORIZONTAL = [{"gid": CB, "prop": "orientation", "value": "horizontal"}]


# ---------------------------------------------------------------------------
# 真的翻过去了
# ---------------------------------------------------------------------------
def test_vertical_to_horizontal_changes_the_actual_axes(library):
    base = _render(library)
    assert _field(base, CB, "orientation") == "vertical"
    bx0, by0, bw0, bh0 = _el(base, CBAX)["bbox"]
    assert bh0 > bw0, "竖色条应当是细高的"

    man = _render(library, HORIZONTAL)
    assert _field(man, CB, "orientation") == "horizontal"
    bx, by, bw, bh = _el(man, CBAX)["bbox"]
    assert bw > bh, "翻成横向之后色条轴必须是扁宽的（只改字段没改布局 = 假修复）"

    host = _el(man, "axes_0")["bbox"]
    assert by > host[1] + host[3] - 1e-3, "横色条应当落在宿主下方"
    assert bw == pytest.approx(host[2], abs=2e-3), "长度跟宿主同宽"


def test_ticks_move_to_the_other_axis(library):
    """刻度必须换到新的长轴上：还留在原来那条轴上就是「翻了个寂寞」。"""
    base = _render(library)
    assert _tick_gids(base, CBAX, "y") and not _tick_gids(base, CBAX, "x")
    man = _render(library, HORIZONTAL)
    assert _tick_gids(man, CBAX, "x"), "横色条的刻度应当在 x 轴上"
    assert not _tick_gids(man, CBAX, "y"), "旧长轴上不该还留着刻度"


def test_existing_colorbar_properties_survive_the_flip(library):
    man = _render(library, [
        *HORIZONTAL,
        {"gid": CB, "prop": "cmap", "value": "plasma"},
        {"gid": CB, "prop": "vmin", "value": 8.0},
        {"gid": CB, "prop": "vmax", "value": 50.0},
        {"gid": CB, "prop": "tick_fontsize", "value": 6.0},
        {"gid": CB, "prop": "tick_color", "value": "#B34700"},
        {"gid": CB, "prop": "outline_width", "value": 1.4},
        {"gid": CB, "prop": "label", "value": "counts"},
    ])
    assert _field(man, CB, "orientation") == "horizontal"
    assert _field(man, CB, "cmap") == "plasma"
    assert _field(man, CB, "vmin") == pytest.approx(8.0)
    assert _field(man, CB, "vmax") == pytest.approx(50.0)
    assert _field(man, CB, "tick_fontsize") == pytest.approx(6.0)
    assert _field(man, CB, "tick_color").lower() == "#b34700"
    assert _field(man, CB, "outline_width") == pytest.approx(1.4)
    assert _field(man, CB, "label") == "counts"


def test_label_does_not_stay_behind_on_the_old_long_axis(library):
    """长轴标签要**搬家**，不是复制一份：旧轴那份不清，横过来之后左边还挂着
    一行竖排文字。"""
    man = _render(library, HORIZONTAL)
    labels = [e["label"] for e in man["elements"]
              if e["role"] == "axis_label" and e["gid"].startswith(CBAX)]
    assert len(labels) == 1, labels
    assert "intensity" in labels[0]


# ---------------------------------------------------------------------------
# gid / follow / 撤销
# ---------------------------------------------------------------------------
def test_gids_are_untouched_by_the_flip(library):
    """就地改造的全部意义：`fig.axes` 顺序不动，所以 gid 集合一个都不变。

    重建色条轴那条路会让 `axes_1` 变成别的对象、后面的编号整体漂——已有
    override 与撤销当场全废，这条断言就是防它偷偷回来。
    """
    before = {e["gid"] for e in _render(library)["elements"]}
    after = {e["gid"] for e in _render(library, HORIZONTAL)["elements"]}
    # 刻度与长轴标签**本来就该**换一条轴（横色条的刻度在 x 上、标签是 xlabel），
    # 那不是编号漂移；其余每一个 gid 都必须逐个对上。
    def strip(gids):
        return {g for g in gids
                if not any(k in g for k in ("ticklabels_", "ticks", "label"))}

    assert strip(after) == strip(before)
    # 而且色条轴自己的编号纹丝不动
    assert CBAX in after and CB in after


def test_semantic_identity_is_stable_and_points_at_the_host(library):
    """`axes_i.colorbar` 是按邻居排序编出来的名字；语义身份才是「这是谁的色条」。"""
    for patches in ([], HORIZONTAL):
        man = _render(library, patches)
        el = _el(man, CB)
        assert el["colorbar_key"] == "cbar:axes_0:0"
        assert el["host_gid"] == "axes_0"


def test_axes_follow_still_links_host_and_colorbar(library):
    """拖宿主时色条要跟着走——翻转之后这条随行关系必须重算并仍然成立。"""
    man = _render(library, HORIZONTAL)
    assert _el(man, "axes_0").get("follow_gids") == [CBAX]


def test_undo_restores_the_original_layout_exactly(library):
    """撤销 = 原样放回（方向 / 刻度侧 / 落位 / 长宽比 / 锚点 / 标签）。"""
    base = _render(library)
    w = _worker(library)
    try:
        flipped = w.override(STEM, HORIZONTAL)
        assert not flipped["warnings"], flipped["warnings"]
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    man = back["manifest"]
    assert _field(man, CB, "orientation") == "vertical"
    assert _el(man, CBAX)["bbox"] == pytest.approx(_el(base, CBAX)["bbox"], abs=1e-9)
    assert _el(man, "axes_0")["bbox"] == pytest.approx(_el(base, "axes_0")["bbox"], abs=1e-9)
    assert _tick_gids(man, CBAX, "y") and not _tick_gids(man, CBAX, "x")


def test_flip_twice_is_idempotent_and_reversible(library):
    """竖 → 横 → 竖：落位逐位回到原点（厚度与间距都能从对侧反解出来）。"""
    base = _render(library)
    w = _worker(library)
    try:
        w.override(STEM, HORIZONTAL)
        vert = w.override(STEM, [{"gid": CB, "prop": "orientation", "value": "vertical"}])
        assert not vert["warnings"], vert["warnings"]
    finally:
        pool.discard(w)
    assert _el(vert["manifest"], CBAX)["bbox"] == pytest.approx(
        _el(base, CBAX)["bbox"], abs=1e-9)


# ---------------------------------------------------------------------------
# 热 / 清空重放 / 全新 worker
# ---------------------------------------------------------------------------
def _bboxes(man):
    return {e["gid"]: e["bbox"] for e in man["elements"] if "ticklabels_" not in e["gid"]}


def test_hot_replay_and_fresh_worker_agree(library):
    """热会话一步步改 == 同一会话清空后全量重放 == 全新 worker 全量重放。"""
    steps = [
        HORIZONTAL,
        [*HORIZONTAL, {"gid": CB, "prop": "label", "value": "counts"}],
        [*HORIZONTAL, {"gid": CB, "prop": "label", "value": "counts"},
         {"gid": CB, "prop": "tick_fontsize", "value": 6.0}],
    ]
    full = steps[-1]
    hot = _worker(library)
    try:
        for step in steps:
            resp = hot.override(STEM, step)
            assert not resp["warnings"], resp["warnings"]
        man_hot = resp["manifest"]
        hot.override(STEM, [])
        replay = hot.override(STEM, full)
        assert not replay["warnings"], replay["warnings"]
        man_replay = replay["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _render(library, full)

    assert _bboxes(man_hot) == pytest.approx(_bboxes(man_replay), abs=5e-3)
    assert _bboxes(man_hot) == pytest.approx(_bboxes(man_fresh), abs=5e-3)


def test_flip_composes_with_an_explicit_position_override(library):
    """用户自己摆过色条轴时，位置归 position override，方向事务不抢它。

    两条 patch 的先后（结构改造在前、落位在后）是**规范化**的，所以热会话
    与全量重放落在同一处——否则「先拖后翻」和「先翻后拖」会得到两张图。
    """
    rect = [0.18, 0.06, 0.6, 0.05]
    patches = [*HORIZONTAL, {"gid": CBAX, "prop": "position", "value": rect}]
    man = _render(library, patches)
    assert _field(man, CB, "orientation") == "horizontal"
    assert _field(man, CBAX, "position") == pytest.approx(rect)

    hot = _worker(library)
    try:
        hot.override(STEM, [{"gid": CBAX, "prop": "position", "value": rect}])
        man_hot = hot.override(STEM, patches)["manifest"]
    finally:
        pool.discard(hot)
    assert _bboxes(man_hot) == pytest.approx(_bboxes(man), abs=5e-3)


def test_flip_follows_a_host_move_made_in_the_same_batch(library):
    """同一批里宿主也被挪走时，色条按**改完之后**的宿主落位算。

    只看此刻的实况会分岔：热会话里 position 可能已经先改过，全量重放里它
    还没轮到——同一份文档于是有两个样子。
    """
    host = [0.10, 0.30, 0.50, 0.55]
    patches = [*HORIZONTAL, {"gid": "axes_0", "prop": "position", "value": host}]
    man = _render(library, patches)
    bx, _by, bw, _bh = _el(man, CBAX)["bbox"]
    # 对齐的是宿主**被请求的**落位，不是它画出来之后的框：这张图的宿主是
    # aspect="equal" 的 imshow，matplotlib 会在 draw 时把它按长宽比再收一次。
    # 拿收完的框当参照就得先 draw 一次，而那一步的结果取决于此刻应用到哪儿了
    # ——热会话与全量重放会算出两个答案。宁可对齐请求值，也不要不确定性。
    assert bx == pytest.approx(host[0], abs=2e-3)
    assert bw == pytest.approx(host[2], abs=2e-3)

    hot = _worker(library)
    try:
        hot.override(STEM, [{"gid": "axes_0", "prop": "position", "value": host}])
        man_hot = hot.override(STEM, patches)["manifest"]
    finally:
        pool.discard(hot)
    assert _bboxes(man_hot) == pytest.approx(_bboxes(man), abs=5e-3)


# ---------------------------------------------------------------------------
# 两端的延伸三角（extend）
# ---------------------------------------------------------------------------
EXTENDS = ["neither", "both", "min", "max"]


def _ext(v):
    return [{"gid": CB, "prop": "extend", "value": v}]


def test_extend_options_match_matplotlib(library):
    man = _render(library)
    opts = next(f["options"] for f in _el(man, CB)["editable"] if f["prop"] == "extend")
    assert opts == EXTENDS
    assert _field(man, CB, "extend") == "neither"


@pytest.mark.parametrize("v", ["both", "min", "max"])
def test_extend_makes_room_for_the_triangles(library, v):
    """延伸三角要占地方：色条轴沿长轴收一收，短边纹丝不动。

    只写 `cb.extend` 而不动 `cb._inside` 的话，`_draw_all()` 会拿 259 条边界
    去配 256 块颜色，当场 TypeError——那条才是这个属性真正的实现难点。
    """
    base = _el(_render(library), CBAX)["bbox"]
    man = _render(library, _ext(v))
    assert _field(man, CB, "extend") == v
    box = _el(man, CBAX)["bbox"]
    assert box[3] < base[3], "竖色条开了 extend 之后应当变短（给三角让地方）"
    assert box[2] == pytest.approx(base[2], abs=1e-9), "短边不该动"
    # bbox 是 **top-origin**：`min` 的三角长在数值小的那头（竖色条的下端），
    # 所以顶边不动；`max` / `both` 会把顶边往下推
    if v == "min":
        assert box[1] == pytest.approx(base[1], abs=1e-9)
    else:
        assert box[1] > base[1]


def test_extend_matches_a_natively_built_colorbar(library):
    """事务做完的落位与「一开始就 extend='both'」**逐位相同**。

    这是这条属性有没有做对的硬判据：差一点点就说明我们在自己算布局，
    而不是让 matplotlib 按它自己的规则算。
    """
    ours = _el(_render(library, _ext("both")), CBAX)["bbox"]
    native = _el(_render(library, (), STEM_NATIVE), CBAX)["bbox"]
    assert ours == pytest.approx(native, abs=1e-9), (ours, native)


def test_extend_undo_returns_to_the_exact_original(library):
    """来回切之后回到 neither，落位逐位归原。

    matplotlib 的色条 locator 在 extend=='neither' 时提前 return，**不会**把
    它自己改过的 box_aspect 收回去——不管这一点的话，「开了又关」的色条会
    比从没开过的宽 10%，而且再也回不去。
    """
    base = _el(_render(library), CBAX)["bbox"]
    w = _worker(library)
    try:
        for v in ("both", "min", "max", "both"):
            resp = w.override(STEM, _ext(v))
            assert not resp["warnings"], resp["warnings"]
        back = w.override(STEM, [])
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    assert _el(back["manifest"], CBAX)["bbox"] == pytest.approx(base, abs=1e-9)


def test_extend_is_idempotent_across_values(library):
    """每一档的落位只取决于档位本身，与之前切过哪些档无关。"""
    seen = {}
    w = _worker(library)
    try:
        for v in ("both", "min", "neither", "max", "both", "neither", "min"):
            man = w.override(STEM, _ext(v))["manifest"]
            box = _el(man, CBAX)["bbox"]
            if v in seen:
                assert box == pytest.approx(seen[v], abs=1e-9), f"{v} 漂移了"
            seen[v] = box
    finally:
        pool.discard(w)


def test_extend_triangles_are_not_user_shapes(library):
    """延伸三角是 `PathPatch`，而且每次 `_draw_all()` 都被删掉重建。

    色条轴上的 patch 一律不登记成可编辑形状——登记了的话元素表里会多出两个
    随时换身份的幽灵条目，用户还能选中它们改颜色（改完下一帧就没了）。
    """
    for patches in ([], _ext("both"), [*HORIZONTAL, *_ext("both")]):
        man = _render(library, patches)
        ghosts = [e["gid"] for e in man["elements"]
                  if e["role"] == "patch" and e["gid"].startswith(CBAX)]
        assert not ghosts, ghosts


def test_extend_composes_with_the_orientation_flip(library):
    """横过来之后再开 extend：沿**新的**长轴收缩，厚度不变，来回可逆。"""
    flat = _el(_render(library, HORIZONTAL), CBAX)["bbox"]
    man = _render(library, [*HORIZONTAL, *_ext("both")])
    box = _el(man, CBAX)["bbox"]
    assert _field(man, CB, "orientation") == "horizontal"
    assert _field(man, CB, "extend") == "both"
    assert box[2] < flat[2], "横色条开 extend 之后应当变短"
    assert box[3] == pytest.approx(flat[3], abs=1e-9), "厚度不该动"

    w = _worker(library)
    try:
        w.override(STEM, [*HORIZONTAL, *_ext("both")])
        back = w.override(STEM, HORIZONTAL)
        assert not back["warnings"], back["warnings"]
    finally:
        pool.discard(w)
    assert _el(back["manifest"], CBAX)["bbox"] == pytest.approx(flat, abs=1e-9)


def test_extend_keeps_the_other_colorbar_properties(library):
    man = _render(library, [
        *_ext("both"),
        {"gid": CB, "prop": "cmap", "value": "plasma"},
        {"gid": CB, "prop": "vmin", "value": 8.0},
        {"gid": CB, "prop": "tick_fontsize", "value": 6.0},
        {"gid": CB, "prop": "label", "value": "counts"},
    ])
    assert _field(man, CB, "extend") == "both"
    assert _field(man, CB, "cmap") == "plasma"
    assert _field(man, CB, "vmin") == pytest.approx(8.0)
    assert _field(man, CB, "tick_fontsize") == pytest.approx(6.0)
    assert _field(man, CB, "label") == "counts"


def test_extend_hot_replay_and_fresh_worker_agree(library):
    steps = [_ext("both"),
             [*_ext("both"), {"gid": CB, "prop": "label", "value": "counts"}],
             [*HORIZONTAL, *_ext("both"),
              {"gid": CB, "prop": "label", "value": "counts"}]]
    full = steps[-1]
    hot = _worker(library)
    try:
        for step in steps:
            resp = hot.override(STEM, step)
            assert not resp["warnings"], resp["warnings"]
        man_hot = resp["manifest"]
        hot.override(STEM, [])
        replay = hot.override(STEM, full)
        assert not replay["warnings"], replay["warnings"]
        man_replay = replay["manifest"]
    finally:
        pool.discard(hot)
    man_fresh = _render(library, full)
    assert _bboxes(man_hot) == pytest.approx(_bboxes(man_replay), abs=5e-3)
    assert _bboxes(man_hot) == pytest.approx(_bboxes(man_fresh), abs=5e-3)


# ---------------------------------------------------------------------------
# 非默认那一侧（location="left"）：翻过去再翻回来必须回到左边
# ---------------------------------------------------------------------------
def _bbox(man, gid):
    return _el(man, gid)["bbox"]


def test_left_colorbar_flips_to_the_top_not_the_bottom(library):
    """左侧竖色条翻成横向应当去**上方**，不是下方。

    宿主的相对位置是脚本作者的排版意图。无论从哪一侧出发都往 right/bottom
    落，等于翻一次就把色条搬了家——而用户以为自己只是换了个方向。
    """
    base = _render(library, stem=STEM_LEFT)
    assert _field(base, CB, "orientation") == "vertical"
    host = _bbox(base, "axes_0")
    cbx, _cby, cbw, _cbh = _bbox(base, CBAX)
    assert cbx + cbw <= host[0] + 1e-3, "这张图的色条本来就在宿主左边"

    man = _render(library, HORIZONTAL, stem=STEM_LEFT)
    assert _field(man, CB, "orientation") == "horizontal"
    host = _bbox(man, "axes_0")
    _x, y, w, h = _bbox(man, CBAX)
    assert w > h, "翻成横向后必须是扁宽的"
    # manifest 的 bbox 是 y 向下的 figure 分数：在宿主上方 = y+h 不超过宿主顶边
    assert y + h <= host[1] + 1e-3, "左侧色条翻横向应当落在宿主上方"


def test_flipping_back_returns_a_left_colorbar_to_the_left(library):
    """来回翻一次要逐位回到原样——**方向转回原值，图也得转回原样**。

    这条与「撤销」是两条不同的路：撤销走 `_restore_cb_orientation`（按快照
    放回，一直是对的），这里走的是「把 orientation 显式设回 vertical」，
    旧实现在这条路上把色条永久搬到了右边，刻度也跟着换边。
    """
    base = _render(library, stem=STEM_LEFT)
    back = _render(library, [{"gid": CB, "prop": "orientation", "value": "horizontal"},
                             {"gid": CB, "prop": "orientation", "value": "vertical"}],
                   stem=STEM_LEFT)
    assert _field(back, CB, "orientation") == "vertical"
    for i, axis in enumerate("xywh"):
        assert _bbox(back, CBAX)[i] == pytest.approx(_bbox(base, CBAX)[i], abs=2e-3), \
            f"翻回来之后 bbox.{axis} 变了：色条被搬了家"
    # 刻度也要回到左边（右边不该有）
    assert _tick_gids(back, CBAX, "y"), "竖色条的刻度应当在 y 轴上"


# ═══════════════════════════════════════════════════════════════════════
# 多宿主色条：1.0 的 guard（issue #69）
#
# `fig.colorbar(im, ax=[a1, a2])` 的色条视觉上横跨两个子图，而我们记的宿主
# 只有一个——`_cb_target_rect()` 拿到的是 `cb.mappable.axes`，也就是第一个。
# 翻转方向之后色条被缩到一图宽。实测（3.10.8 / 3.11.1 一致）：
#
#     a1 的 x 跨度  (0.125, 0.407)
#     a2 的 x 跨度  (0.463, 0.745)
#     翻转后        x0=0.125  宽 0.282     ← 只跨 a1
#     应当          x0=0.125  宽 0.620
#
# 1.0 不修落位模型（那要把宿主从一个 axes 改成一组，`_cb_place` /
# `_cb_target_rect` / `axes_follow` 三处按并集算）。这一轮做的是 guard：
# **不宣称这条能力**，并给出稳定的 reason。
#
# 顺序是 detect → guard/hide → unsupported reason → issue → v1.1 fix
# （docs/engineering/review-severity-policy.md §3）。
# ═══════════════════════════════════════════════════════════════════════

MULTI_SCRIPT = "fig_multi_cbar.py"
MULTI_STEM = "CbarMulti"
SINGLE_STEM = "CbarSingleForContrast"

MULTI_LIBRARY = '''\
import numpy as np
import matplotlib.pyplot as plt


def main():
    # 多宿主：一条色条横跨两个子图
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    im = a1.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    a2.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    fig.colorbar(im, ax=[a1, a2])
    fig.savefig("CbarMulti.pdf")

    # 同一张脚本里的**单宿主对照**：guard 只该收走多宿主那条，
    # 不该顺手把常规色条的方向能力也藏了。
    fig2, ax = plt.subplots(figsize=(4.0, 3.0))
    im2 = ax.imshow(np.arange(64).reshape(8, 8), cmap="viridis")
    fig2.colorbar(im2, ax=ax)
    fig2.savefig("CbarSingleForContrast.pdf")
'''


@pytest.fixture(scope="module")
def multi_library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("multi-cbar-figures")
    (figs / MULTI_SCRIPT).write_text(MULTI_LIBRARY, encoding="utf-8")
    return figs


def _multi_render(figs, stem, patches=()):
    w = pool.one_shot(MULTI_SCRIPT, str(figs), "main")
    try:
        w.ensure_built()
        return w.override(stem, list(patches))
    finally:
        pool.discard(w)


def _colorbar_el(man):
    els = [e for e in man["elements"] if e.get("role") == "colorbar"
           or e["gid"].endswith(".colorbar")]
    assert els, f"这张图里没有色条元素：{[e['gid'] for e in man['elements']]}"
    return els[0]


def test_the_multi_host_predicate_matches_matplotlib(multi_library):
    """判据本身先量一遍：`parents` 的长度就是宿主个数。

    **不照抄注释里的结论，自己量。** 六种建法在开发机上逐个量过
    （3.10.8）：`ax=ax` → 1，`ax=[a1,a2]` → 2，`ax=[a,b,c]` → 3，
    `cax=` → 没有 `_colorbar_info`（按 1 算），独立 mappable 两种 → 1 / 2。
    这里在 worker 的解释器里复量一次，免得两边的 matplotlib 版本不同。
    """
    probe = '''
import json, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable

def hosts(cb):
    info = getattr(cb.ax, "_colorbar_info", None)
    parents = info.get("parents") if isinstance(info, dict) else None
    return len(parents) if parents else 1

out = {}
fig, ax = plt.subplots(); im = ax.imshow(np.arange(9).reshape(3,3))
out["single"] = hosts(fig.colorbar(im, ax=ax)); plt.close(fig)

fig, (a,b) = plt.subplots(1,2); im = a.imshow(np.arange(9).reshape(3,3))
out["two"] = hosts(fig.colorbar(im, ax=[a,b])); plt.close(fig)

fig, axs = plt.subplots(1,3); im = axs[0].imshow(np.arange(9).reshape(3,3))
out["three"] = hosts(fig.colorbar(im, ax=list(axs))); plt.close(fig)

fig, ax = plt.subplots(); im = ax.imshow(np.arange(9).reshape(3,3))
cax = fig.add_axes([0.9,0.1,0.03,0.8])
out["explicit_cax"] = hosts(fig.colorbar(im, cax=cax)); plt.close(fig)

fig, (a,b) = plt.subplots(1,2)
sm = ScalarMappable(cmap="viridis"); sm.set_array([])
out["standalone_two"] = hosts(fig.colorbar(sm, ax=[a,b])); plt.close(fig)

print(json.dumps(out))
'''
    import json
    import subprocess
    r = subprocess.run([WORKER_PY, "-c", probe], capture_output=True,
                       text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-800:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {"single": 1, "two": 2, "three": 3,
                   "explicit_cax": 1, "standalone_two": 2}, got


def test_multi_host_colorbar_does_not_offer_orientation(multi_library):
    """**能力表不许宣称一个会把排版弄坏的开关。**

    一个「点了就把图排版弄坏」的控件，比一个不存在的控件糟糕得多——
    用户没有理由预料到它会那样。
    """
    man = _multi_render(multi_library, MULTI_STEM)["manifest"]
    el = _colorbar_el(man)
    props = {f["prop"] for f in el["editable"]}
    assert "orientation" not in props, (
        "多宿主色条仍然宣称 orientation——那条改了会把色条缩到一图宽")
    # 其余能力一条都不许受牵连：guard 收的是**一条 prop**，不是整个元素
    for keep in ("label", "extend", "tick_fontsize", "visible"):
        assert keep in props, f"guard 顺手把 {keep} 也收走了"


def test_the_manifest_says_why_the_capability_is_missing(multi_library):
    """少一个控件而不给理由，用户只会以为是漏了或者是坏了。

    reason 是**稳定 code**（`multi_host_colorbar`），不是一句中文文案——
    界面按 code 翻译，文案随时可改。
    """
    man = _multi_render(multi_library, MULTI_STEM)["manifest"]
    el = _colorbar_el(man)
    rows = el.get("unsupported_props")
    assert rows, "多宿主色条没有说明为什么没有 orientation"
    row = next(r for r in rows if r["prop"] == "orientation")
    assert row["reason"] == "multi_host_colorbar"
    assert row["detail"]["hosts"] == 2


def test_a_single_host_colorbar_still_offers_orientation(multi_library):
    """**对照组。** guard 只该收走多宿主那条。

    没有这条的话，「把 orientation 整个删掉」也能让上面两条绿——
    而那是把一个真能力藏起来，正是 `Arc` 那次的教训。
    """
    man = _multi_render(multi_library, SINGLE_STEM)["manifest"]
    el = _colorbar_el(man)
    props = {f["prop"] for f in el["editable"]}
    assert "orientation" in props, "单宿主色条的方向能力被误伤了"
    assert not el.get("unsupported_props"), \
        f"单宿主色条不该有 unsupported_props：{el.get('unsupported_props')}"


def test_an_old_document_cannot_sneak_the_patch_through(multi_library):
    """**第二个消费点。**

    manifest 不宣称挡不住一份旧文档：用户在 1.0 之前存过一条 orientation
    override，重开时它照样会被发过来。只修一处等于没修。

    这里要的是 **warning**（→ 写回阻断），不是静默忽略——
    「写回成功了，但图和屏幕上不一样」是这条链上最不能接受的失败。
    """
    resp = _multi_render(multi_library, MULTI_STEM,
                         [{"gid": _colorbar_el(
                             _multi_render(multi_library, MULTI_STEM)["manifest"]
                         )["gid"], "prop": "orientation", "value": "horizontal"}])
    warns = resp.get("warnings") or []
    assert warns, "旧文档里的 orientation 补丁被静默吃掉了"
    assert any("multi_host_colorbar" in str(w) for w in warns), warns


def test_the_host_count_predicate_has_one_implementation():
    """判据只能有一处。两处必然漂开，而漂开的表现是
    「manifest 不宣称，setter 却照改」——用户点不到，旧文档却能改坏。
    """
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
    tree = ast.parse((src / "overrides.py").read_text(encoding="utf-8"))
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "colorbar_host_count"]
    assert len(defs) == 1, "colorbar_host_count 有多份定义"

    # 两个消费方都必须调它，而不是各自去读 `_colorbar_info`
    for fname, func in (("overrides.py", "_set_cb_orientation"),
                        ("manifest.py", "_colorbar_fields")):
        t = ast.parse((src / fname).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(t)
                  if isinstance(n, ast.FunctionDef) and n.name == func)
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "colorbar_host_count" in called, \
            f"{fname}::{func} 没有走那份唯一判据"
