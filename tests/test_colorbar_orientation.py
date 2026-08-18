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

from magplot.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（MM_WORKER_PYTHON）")

SCRIPT_NAME = "fig_cbar.py"
ENTRY = "main"
STEM = "CbarFig"
CB = "axes_1.colorbar"          # 色条伪元素（宿主是 axes_0，色条轴是 axes_1）
CBAX = "axes_1"

#: 第二张图与第一张**除了 extend 之外一模一样**：`extend` 事务做完的落位
#: 必须与「一开始就这么建」逐位相同，这张图就是那把尺子。
STEM_NATIVE = "CbarNative"

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
