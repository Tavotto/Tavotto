"""matplotlib 对象拖动全族排查（ADR 0100）的引擎这一侧。

排查按前端 `inFigureMoveOf` 的同一套分派，对每个家族写一次平移 override，量 manifest 里
**被移动的那个元素**的落点。修前的实测（matplotlib 3.10.8）：

* 锚定框（`AnchoredText` / `AnchoredSizeBar` / `AnchoredOffsetbox`）与 `AnnotationBbox`
  按「认不出来的 Artist」登记，**不宣称可拖**——选得中、拖不动，也不说为什么；
* 靠定位器落位的轴（`ax.inset_axes`、mpl_toolkits 的 `inset_axes`、`make_axes_locatable`
  分出来的主图与色条、`ImageGrid`）：插图不宣称，其余**宣称了、松手弹回原处**
  （误差 2.2–3.8 mm，正好是整段位移）；
* constrained 图：拖图例落点差 1.2–4.5 mm、别的子图跟着跳 1.7–2 mm；拖一个子图别的子图跳
  最多 7.8 mm；拖色条整张图画不出来（`box_aspect` 为 0）；撤销拖过的子图不回原样
  （0.3 mm，色条最多 11 mm）；形状 / 独立箭头差 0.1 mm。

判据的主语：**同一个热 worker** 里写下一条平移之后那份 manifest 中目标元素的 `anchor`
（可拖元素）或 `position` 字段（子图，figure 分数、bottom-origin）。期望值是测试侧独立算的：
基线值加上一个写死的位移，不调用任何生产坐标换算。另外三条合同：同一份列表再渲染一次不漂、
撤销（空列表）回到基线、一次性 worker 全量重放落在同一处。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_drag_coverage.py"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.offsetbox import AnchoredOffsetbox, AnchoredText, AnnotationBbox, OffsetImage, TextArea
from matplotlib.patches import FancyArrowPatch, Rectangle

X = np.linspace(0, 6, 40)


def _anchored(stem, **kw):
    from mpl_toolkits.axes_grid1.anchored_artists import AnchoredSizeBar

    fig, ax = plt.subplots(figsize=(5, 3.4), **kw)
    ax.plot(X, np.sin(X))
    ax.set_ylabel("y")
    ax.add_artist(AnchoredText("(a) anchored", loc="upper left"))
    ax.add_artist(AnchoredSizeBar(ax.transData, 1, "1 unit", loc="lower right", frameon=False))
    ax.add_artist(AnchoredOffsetbox(loc="center right", child=TextArea("offsetbox")))
    ax.add_artist(AnnotationBbox(OffsetImage(np.arange(16.0).reshape(4, 4), zoom=4), (2, 0.5),
                                 xybox=(3, -0.4), arrowprops={"arrowstyle": "->"}))
    ax.add_artist(AnnotationBbox(TextArea("ab text"), (1, -0.5)))
    ax.add_artist(AnnotationBbox(TextArea("ab px"), (4, 0.5), xybox=(10, 10),
                                 boxcoords="offset pixels"))
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def _locators(stem):
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    from mpl_toolkits.axes_grid1.inset_locator import inset_axes

    fig, axs = plt.subplots(1, 2, figsize=(7, 3.4))
    axs[0].plot(X, np.sin(X))
    ins = axs[0].inset_axes([0.6, 0.6, 0.35, 0.35])
    ins.plot(X, X)
    tk = inset_axes(axs[0], width="25%", height="25%", loc="lower left", borderpad=2)
    tk.plot(X, -X)
    im = axs[1].imshow(np.arange(64.0).reshape(8, 8))
    cax = make_axes_locatable(axs[1]).append_axes("right", size="5%", pad=0.05)
    fig.colorbar(im, cax=cax)
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def _constrained(stem):
    fig, axs = plt.subplots(1, 2, figsize=(6, 3), layout="constrained")
    axs[0].plot(X, np.sin(X), label="sin")
    axs[0].legend(loc="upper right")
    axs[0].add_patch(Rectangle((0.5, 0.3), 1, 0.4, alpha=0.5))
    axs[0].add_patch(FancyArrowPatch((0.5, -0.8), (1.5, -0.4), arrowstyle="->", mutation_scale=12))
    im = axs[1].imshow(np.arange(64.0).reshape(8, 8))
    fig.colorbar(im, ax=axs[1])
    axs[1].plot([0, 7], [0, 7], label="d")
    axs[1].legend(bbox_to_anchor=(1.3, 1), loc="upper left")
    fig.savefig(stem + ".pdf")
    plt.close(fig)


def main():
    _anchored("Anch")
    _anchored("AnchC", layout="constrained")
    _locators("Loc")
    _constrained("Cons")
"""

#: 写死的位移（figure 分数，x 右、y 下）：不是任何网格的整数倍
DELTA = (0.03, 0.02)
#: 锚点 / 落位预算。正确实现的实测误差 ~1e-12；缺陷的误差 ≥ 1e-3
TOL = 1e-6
#: constrained 排版本身的数值噪声：同一张图不同次序画出来的子图框差 ~1e-6（3.11）到
#: 1.3e-5（3.8.4，拖色条时邻居子图高度）figure 分数，≤ 0.002 mm，迭代求解器的收敛尾巴；
#: 缺陷的量级是 1e-2（变异 E3 / E9 实测 8.5e-3 起）
CONSTRAINED_TOL = 5e-5

ANCHORED = {
    "AnchoredText": "axes_0.artists_0",
    "AnchoredSizeBar": "axes_0.artists_1",
    "AnchoredOffsetbox": "axes_0.artists_2",
    "AnnotationBbox image": "axes_0.artists_3",
    "AnnotationBbox text": "axes_0.artists_4",
}
AB_PIXELS = "axes_0.artists_5"

#: `_locators` 的轴：fig.axes 序 = 左子图、右子图、mpl_toolkits 插图、append_axes 色条轴，
#: 之后才是 child_axes 里的 `ax.inset_axes`
LOCATOR_AXES = {
    "mpl_toolkits inset_axes": "axes_2",
    "append_axes 分出来的主图": "axes_1",
    "append_axes 色条轴": "axes_3",
    "ax.inset_axes 插图": "axes_4",
}


@pytest.fixture(scope="module")
def figs(tmp_path_factory):
    d = tmp_path_factory.mktemp("drag-coverage")
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


def _resp(worker, stem, patches=(), **kw):
    resp = worker.override(stem, list(patches), **kw)
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp


def _man(worker, stem, patches=()):
    return {e["gid"]: e for e in _resp(worker, stem, patches)["manifest"]["elements"]}


def _pos(man, gid):
    return next(f["value"] for f in man[gid]["editable"] if f["prop"] == "position")


def _moved_anchor(el):
    a = el["anchor"]
    return [a[0] + DELTA[0], a[1] + DELTA[1]]


def _moved_pos(man, gid):
    x, y, w, h = _pos(man, gid)
    return [x + DELTA[0], y - DELTA[1], w, h]


def _axes_boxes(man, skip=()):
    return {g: e["bbox"] for g, e in man.items() if e["role"] == "axes" and g not in skip}


def _same_boxes(a: dict, b: dict, tol: float, where: str) -> None:
    assert a.keys() == b.keys(), where
    for g in a:
        assert a[g] == pytest.approx(b[g], abs=tol), f"{where}: {g} {b[g]} → {a[g]}"


# ---------------------------------------------------------------------------
# 锚定框与 AnnotationBbox
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", ["Anch", "AnchC"])
@pytest.mark.parametrize("name", list(ANCHORED))
def test_anchored_boxes_drag_to_the_written_anchor(hot, stem, name):
    """锚定框 / 插框宣称可拖、写下的左下角就是量到的左下角；再画一次不漂；撤销逐位回基线；
    子图一动不动（constrained 图上框的包围盒参与排版，拖它不许挤动子图）。"""
    gid = ANCHORED[name]
    base = _man(hot, stem)
    el = base[gid]
    assert el["draggable"] is True and el["drag_prop"] == "pos_frac", el
    target = _moved_anchor(el)
    patch = {"gid": gid, "prop": "pos_frac", "value": target}
    for again in (False, True):
        got = _man(hot, stem, [patch])
        assert got[gid]["anchor"] == pytest.approx(target, abs=TOL), f"{stem} {name} again={again}"
        _same_boxes(_axes_boxes(got), _axes_boxes(base), 1e-9, f"{stem} {name}")
    back = _man(hot, stem)
    assert back[gid]["anchor"] == pytest.approx(el["anchor"], abs=1e-9)
    assert back[gid]["bbox"] == pytest.approx(el["bbox"], abs=1e-9)


def test_annotation_box_in_pixel_coords_is_not_claimed_draggable(hot):
    """'offset pixels' 的 AnnotationBbox：写进去的像素随导出 dpi 漂，不宣称可拖；硬写一条
    得到的是明确的 warning，不是静默落到别处（与注释文字同一张可逆表）。"""
    base = _man(hot, "Anch")
    assert base[AB_PIXELS]["draggable"] is False
    resp = hot.override("Anch", [{"gid": AB_PIXELS, "prop": "pos_frac", "value": [0.5, 0.5]}])
    assert resp.get("warnings"), "不支持的坐标系必须给 warning"
    _man(hot, "Anch")


def test_anchored_offsetbox_has_its_own_svg_group(hot):
    """`AnchoredOffsetbox.draw` 自己不开 SVG 组：没有 `id=<gid>` 的节点，前端的乐观预览找不到
    它，拖的时候框不跟手、松手才跳。引擎给它包了一层（`manifest._svg_group`）。"""
    svg = _resp(hot, "Anch", inline_svg=True)["svg"]
    for gid in ("axes_0.artists_0", "axes_0.artists_1", "axes_0.artists_2", "axes_0.artists_3"):
        assert f'id="{gid}"' in svg, f"预览 SVG 里没有 {gid} 的组"


# ---------------------------------------------------------------------------
# 靠定位器落位的轴
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(LOCATOR_AXES))
def test_locator_placed_axes_hold_the_written_position(hot, name):
    """定位器每次 draw 重算落位的轴：写下的 position 就是画出来的 position（定位器被摘下来），
    再画一次不漂，撤销逐位回基线（定位器放回去，重新跟着宿主走）。"""
    gid = LOCATOR_AXES[name]
    base = _man(hot, "Loc")
    assert base[gid]["resizable"] is True, base[gid]
    target = _moved_pos(base, gid)
    patch = {"gid": gid, "prop": "position", "value": target}
    for again in (False, True):
        got = _man(hot, "Loc", [patch])
        assert _pos(got, gid) == pytest.approx(target, abs=1e-4), f"{name} again={again}"
    back = _man(hot, "Loc")
    assert _pos(back, gid) == pytest.approx(_pos(base, gid), abs=1e-9)
    assert back[gid]["bbox"] == pytest.approx(base[gid]["bbox"], abs=1e-9)


def test_inset_names_its_host(hot):
    """`ax.inset_axes` 的插图点名宿主：前端拖宿主时带着**挪过**的插图（没挪过的由定位器带着）。"""
    base = _man(hot, "Loc")
    assert base["axes_4"].get("inset_of") == "axes_0"
    assert "inset_of" not in base["axes_2"], "mpl_toolkits 的 inset_axes 不是 child_axes"


# ---------------------------------------------------------------------------
# constrained 布局
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gid", "prop"),
    [
        ("axes_0.legend", "loc_frac"),
        ("axes_1.legend", "loc_frac"),
        ("axes_0.patches_0", "pos_frac"),
    ],
)
def test_constrained_drag_lands_and_leaves_the_axes_alone(hot, gid, prop):
    """constrained 图上拖图例 / 形状：落点就是写下的点，子图一动不动，再画一次不漂。"""
    base = _man(hot, "Cons")
    target = _moved_anchor(base[gid])
    patch = {"gid": gid, "prop": prop, "value": target}
    for again in (False, True):
        got = _man(hot, "Cons", [patch])
        assert got[gid]["anchor"] == pytest.approx(target, abs=TOL), f"{gid} again={again}"
        _same_boxes(_axes_boxes(got), _axes_boxes(base), CONSTRAINED_TOL, gid)
    _man(hot, "Cons")


def test_constrained_standalone_arrow_lands(hot):
    gid = "axes_0.arrows_1"
    base = _man(hot, "Cons")
    (a, b) = base[gid]["arrow_endpoints"]
    v = [a[0] + DELTA[0], a[1] + DELTA[1], b[0] + DELTA[0], b[1] + DELTA[1]]
    got = _man(hot, "Cons", [{"gid": gid, "prop": "endpoints_frac", "value": v}])
    assert [*got[gid]["arrow_endpoints"][0], *got[gid]["arrow_endpoints"][1]] == pytest.approx(
        v, abs=1e-4
    )
    _man(hot, "Cons")


@pytest.mark.parametrize("gid", ["axes_0", "axes_2"])
def test_constrained_axes_drag_holds_and_the_rest_stays(hot, gid):
    """constrained 图上拖子图 / 色条轴：落在写下的位置、别的子图不跳（`set_position` 把轴踢出
    排版，整张图按「少了一个子图」重排——修前实测别的子图跳 7.8 mm；色条轴修前整张图画不出来）；
    撤销逐位回到基线（修前拖过的轴从此不参与排版，差 0.3–11 mm）。"""
    base = _man(hot, "Cons")
    target = _moved_pos(base, gid)
    patch = {"gid": gid, "prop": "position", "value": target}
    for again in (False, True):
        got = _man(hot, "Cons", [patch])
        assert _pos(got, gid) == pytest.approx(target, abs=1e-4), f"{gid} again={again}"
        _same_boxes(
            _axes_boxes(got, (gid,)), _axes_boxes(base, (gid,)), CONSTRAINED_TOL, f"{gid} 别的子图"
        )
    back = _man(hot, "Cons")
    # 色条轴撤销后有 ≤ 4e-4 figure 分数（0.06 mm）的残差：constrained 的色条摆放读上一帧的
    # 装饰物包围盒，要多画几次才完全回到原处（修前 11 mm）。别的轴逐位回去
    tol = 5e-4 if gid == "axes_2" else CONSTRAINED_TOL
    _same_boxes(_axes_boxes(back), _axes_boxes(base), tol, f"{gid} 撤销")


def test_fresh_replay_matches_the_hot_session(hot, figs):
    """写回事务不变式：热态逐步拖（锚定框、插图、constrained 的图例 / 形状 / 子图）与一次性
    worker 全量重放落成同一张图。"""
    plan = {
        "AnchC": [("axes_0.artists_0", "pos_frac"), ("axes_0.artists_3", "pos_frac")],
        "Loc": [("axes_4", "position"), ("axes_2", "position"), ("axes_1", "position")],
        "Cons": [
            ("axes_0.legend", "loc_frac"),
            ("axes_0.patches_0", "pos_frac"),
            ("axes_0", "position"),
        ],
    }
    hot_state = {}
    for stem, moves in plan.items():
        base = _man(hot, stem)
        patches = []
        for gid, prop in moves:
            value = _moved_pos(base, gid) if prop == "position" else _moved_anchor(base[gid])
            patches.append({"gid": gid, "prop": prop, "value": value})
            got = _man(hot, stem, patches)
        hot_state[stem] = (patches, got)
        _man(hot, stem)
    fresh = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        fresh.ensure_built()
        for stem, (patches, got) in hot_state.items():
            replay = _man(fresh, stem, patches)
            _same_boxes(
                {g: e["bbox"] for g, e in replay.items()},
                {g: e["bbox"] for g, e in got.items()},
                CONSTRAINED_TOL,
                f"{stem} 热态 vs 重放",
            )
    finally:
        pool.discard(fresh)


# ---------------------------------------------------------------------------
# 私有 API 缺席时的回退（3.8.4 / 3.10.8 / 3.11.1 / 3.11.2 上都在；将来改名时不许崩）
# ---------------------------------------------------------------------------

ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"

#: 在 worker 那一侧的解释器里直接驱动引擎：把「拖动要用的私有属性」名单加一个不存在的名字，
#: 模拟将来某一版 matplotlib 把它改了名
_OLD_API_DRIVER = """\
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredOffsetbox, AnchoredText, AnnotationBbox, TextArea

import manifest
import overrides

for cls in (AnchoredOffsetbox, AnnotationBbox):
    overrides._OFFSETBOX_PRIVATE[cls] += ("_renamed_in_some_future_release",)

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot([0, 1], [0, 1])
ax.add_artist(AnchoredText("(a)", loc="upper left"))
ax.add_artist(AnnotationBbox(TextArea("note"), (0.5, 0.5)))
st = overrides.FigState(fig)
manifest.instrument(st)
base = {e["gid"]: e for e in manifest.build_manifest(st, "Old")["elements"]}
warnings = overrides.apply(st, [{"gid": "axes_0.artists_0", "prop": "pos_frac", "value": [0.3, 0.3]}])
after = {e["gid"]: e for e in manifest.build_manifest(st, "Old")["elements"]}
overrides.apply(st, [])
back = {e["gid"]: e for e in manifest.build_manifest(st, "Old")["elements"]}
print(json.dumps({
    "draggable": [base[g]["draggable"] for g in ("axes_0.artists_0", "axes_0.artists_1")],
    "anchor": [("anchor" in base[g]) for g in ("axes_0.artists_0", "axes_0.artists_1")],
    "warnings": [str(w) for w in (warnings or [])],
    "moved": after["axes_0.artists_0"]["bbox"] != base["axes_0.artists_0"]["bbox"],
    "back": back["axes_0.artists_0"]["bbox"] == base["axes_0.artists_0"]["bbox"],
}))
"""


def test_missing_private_api_falls_back_to_not_draggable():
    """拖动要用的私有属性缺席时：manifest 照常建出来、不宣称可拖（前端拖起来说「暂不支持」）；
    硬写一条 `pos_frac` 得到的是 warning，不是崩溃，框原地不动，撤销之后逐位原样。"""
    proc = subprocess.run(
        [WORKER_PY, "-c", _OLD_API_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["draggable"] == [False, False], got
    assert got["anchor"] == [False, False], got
    assert got["warnings"], "不支持时必须给 warning"
    assert got["moved"] is False and got["back"] is True, got


# ---------------------------------------------------------------------------
# 坏值先抛、再动 artist（Codex #681 P2）
# ---------------------------------------------------------------------------

_BAD_VALUE_DRIVER = """\
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredText, AnnotationBbox, TextArea
from matplotlib.patches import Rectangle

import manifest
import overrides

fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
ax.plot([0, 1], [0, 1], label="l")
ax.legend(loc="upper left")
ax.add_artist(AnchoredText("(a)", loc="upper right"))
ax.add_artist(AnnotationBbox(TextArea("note"), (0.5, 0.5)))
ax.add_patch(Rectangle((0.1, 0.1), 0.2, 0.2))
st = overrides.FigState(fig)
manifest.instrument(st)


def snap():
    m = {e["gid"]: e for e in manifest.build_manifest(st, "Bad")["elements"]}
    ab = st.index["axes_0.artists_1"]
    return {
        "boxes": {g: e["bbox"] for g, e in m.items()},
        "ab_align": [float(v) for v in ab._box_alignment],
        "ab_xybox": [float(v) for v in ab.xybox],
    }


base = snap()
out = {}
for gid, prop in (
    ("axes_0.artists_1", "pos_frac"),
    ("axes_0.artists_0", "pos_frac"),
    ("axes_0.legend", "loc_frac"),
    ("axes_0.patches_0", "pos_frac"),
):
    bad = [{"gid": gid, "prop": prop, "value": [0.3]}]
    # ① 第一次写就是坏值（Codex 的场景：外部改坏的项目）
    warn = overrides.apply(st, bad)
    overrides.apply(st, [])
    first = snap() == base
    # ② 先拖过一次、再被改坏
    overrides.apply(st, [{"gid": gid, "prop": prop, "value": [0.4, 0.4]}])
    warn2 = overrides.apply(st, bad)
    overrides.apply(st, [])
    same = first and snap() == base
    if prop == "loc_frac":
        # 图例的坏值要是留在位置模型的槽位里，之后改任何一条位置 prop 都会重抛
        same = same and not overrides.apply(st, [{"gid": gid, "prop": "loc", "value": "lower right"}])
        overrides.apply(st, [])
    out[gid] = {"warned": bool(warn) and bool(warn2), "same": same}
print(json.dumps(out))
"""


def test_malformed_drag_value_leaves_no_half_applied_state():
    """坏值（长度不对）落到拖动 setter 上：给 warning，而且**动 artist 之前**就抛——之后的
    空列表把图还原到逐位原样（热态 == 干净重放）。修前 AnnotationBbox 先改了对齐方式再抛，
    这条 key 不进 applied，撤销还不回来。"""
    proc = subprocess.run(
        [WORKER_PY, "-c", _BAD_VALUE_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    for gid, r in got.items():
        assert r["warned"], f"{gid}：坏值必须给 warning"
        assert r["same"], f"{gid}：坏值之后撤销没回到原样"


_VALIDATE_FIRST_DRIVER = """\
import json
import math
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.offsetbox import AnchoredText, AnnotationBbox, TextArea
from matplotlib.patches import FancyArrowPatch, Rectangle

import legendmodel
import manifest
import overrides

def build():
    fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
    ax.plot([0, 1], [0, 1], label="l")
    # 带锚框：坏值若先把模型改了（清锚框），这里量得出来
    ax.legend(loc="upper left", bbox_to_anchor=(0.02, 0.98))
    ax.add_artist(AnchoredText("(a)", loc="upper right"))
    ax.add_artist(AnnotationBbox(TextArea("note"), (0.5, 0.5)))
    ax.add_patch(Rectangle((0.1, 0.1), 0.2, 0.2))
    ax.add_patch(FancyArrowPatch((0.2, 0.7), (0.4, 0.8), arrowstyle="->", mutation_scale=10))
    ax.text(0.6, 0.2, "note text")
    st = overrides.FigState(fig)
    manifest.instrument(st)
    return st


def snap(st):
    leg = st.index["axes_0.legend"]
    try:
        m = manifest.build_manifest(st, "V")["elements"]
    except Exception as exc:  # 修前 NaN 落进 artist 之后整张图画不出来：记成一个值，逐行报
        return {"draw_error": repr(exc)}
    cfg = legendmodel.legend_pos_cfg(leg)
    return {
        "boxes": {e["gid"]: e["bbox"] for e in m},
        "slots": {k: repr(cfg[k]) for k in legendmodel._LEGEND_POS_SLOTS},
        "bbox_to_anchor": None if leg._bbox_to_anchor is None else list(leg._bbox_to_anchor.bounds),
    }


BAD = (
    [0.3],
    [0.3, math.nan],
    [0.3, math.inf],
    ["x", 0.3],
    # 形状不对、float() 却收得下的（Codex #681 第三条 P2）：字符串被逐字拆成数、布尔成 1 / 0
    "12",
    [True, False],
    ["0.3", 0.4],
)
CASES = (
    ("axes_0.legend", "loc_frac", [0.4, 0.4]),
    ("axes_0.artists_0", "pos_frac", [0.4, 0.4]),
    ("axes_0.artists_1", "pos_frac", [0.4, 0.4]),
    ("axes_0.patches_0", "pos_frac", [0.4, 0.4]),
    ("axes_0.arrows_1", "endpoints_frac", [0.3, 0.3, 0.5, 0.4]),
    ("axes_0", "position", [0.2, 0.2, 0.6, 0.6]),
    ("axes_0.texts_0", "pos_frac", [0.4, 0.4]),
)
out = []
for gid, prop, good in CASES:
    for bad in BAD:
        # 长度对、内容坏的那几种补齐到这条 prop 的长度（[0.3] 本身就是长度不对）；字符串原样
        # 传，长度与这条 prop 相同——修前它被逐字拆成一组「合法」的数
        if isinstance(bad, str):
            badv = "1234"[: len(good)]
        elif len(bad) > 1:
            badv = list(bad) + [0.5] * (len(good) - len(bad))
        else:
            badv = list(bad)
        row = {"case": f"{gid}.{prop} <- {badv!r}"}
        st = build()  # 每行一张新图：一行的污染不许拖累下一行的判据
        base = snap(st)
        # ① 第一次写就是坏值（Codex #681：外部改坏的项目）：热模型**当场**零改动
        row["warned"] = bool(overrides.apply(st, [{"gid": gid, "prop": prop, "value": badv}]))
        row["untouched"] = snap(st) == base
        overrides.apply(st, [])
        row["undo"] = snap(st) == base
        # 随后合法的操作照常：图例的 loc（修前从被污染的 loc_frac 槽里重抛）与同一条 prop
        if prop == "loc_frac":
            row["loc_ok"] = not overrides.apply(
                st, [{"gid": gid, "prop": "loc", "value": "lower right"}]
            )
            overrides.apply(st, [])
        row["good_ok"] = not overrides.apply(st, [{"gid": gid, "prop": prop, "value": good}])
        moved = snap(st)
        # ② 拖过之后被改坏：热态仍是拖过的那一版（applied 里还是它）
        overrides.apply(st, [{"gid": gid, "prop": prop, "value": badv}])
        row["kept"] = snap(st) == moved
        overrides.apply(st, [])
        row["back"] = snap(st) == base
        out.append(row)
        plt.close(st.fig)
# 正向：合法的形状照收——JSON 里 1.0 到这边是 int 1，元组来自 pin 表，numpy 数值标量也是数
import numpy as np
legit = ([0, 1], (0.25, 0.5), [np.float64(0.2), np.int64(0)], [np.float32(0.1), 1, 0.5, 0.5])
for v in legit:
    try:
        ok = overrides._drag_value(v, len(v)) == [float(x) for x in v]
    except ValueError:
        ok = False
    out.append({"case": f"legit {v!r}", "accepted": ok})
print(json.dumps(out))
"""


def test_malformed_drag_value_is_rejected_before_any_state_changes():
    """拖动类 setter 先把值校验完（list / tuple、非布尔实数、个数、有限数）再动任何状态
    （Codex #681 第二、三条 P2）：坏值给 warning，热模型**当场**零改动——图例的位置模型槽位、锚框，锚定框 / 插框 / 形状 / 独立
    箭头 / 子图 / 文字的落位都不变；之后的空列表、图例的 loc、同一条 prop 的合法值都照常。

    上一条用例在坏值之后先写了一次合法拖动，正好把被污染的 loc_frac 槽冲掉，所以没量到
    「第一次写就是坏值、紧接着改 loc」这条路；这里按 Codex 给的顺序直接走。"""
    proc = subprocess.run(
        [WORKER_PY, "-c", _VALIDATE_FIRST_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(rows) == 53, rows
    bad = [r for r in rows if not all(v for k, v in r.items() if k != "case")]
    assert not bad, json.dumps(bad, ensure_ascii=False, indent=1)


_ARROW_DRIVER = """\
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch
from matplotlib.path import Path

import manifest
import overrides

fig, ax = plt.subplots(figsize=(4, 3))
ax.plot([0, 1], [0, 1])
ax.annotate("peak", xy=(0.5, 0.5), xytext=(0.2, 0.8), arrowprops={"arrowstyle": "->"})
ax.annotate("", xy=(0.8, 0.2), xytext=(0.6, 0.4), arrowprops={"arrowstyle": "->"})
ax.annotate("", xy=(0.8, 0.8), xytext=(15, 15), textcoords="offset pixels",
            arrowprops={"arrowstyle": "->"})
ax.add_patch(FancyArrowPatch(path=Path([(0.1, 0.1), (0.3, 0.2)]), arrowstyle="->",
                             mutation_scale=10, transform=ax.transData))
st = overrides.FigState(fig)
manifest.instrument(st)
m = {e["gid"]: e for e in manifest.build_manifest(st, "Arr")["elements"]}
print(json.dumps({g: {"arrow_of": e.get("arrow_of"), "ends": "arrow_endpoints" in e}
                  for g, e in m.items() if e["role"] == "arrow_patch"}))
"""


def test_only_arrows_of_text_annotations_name_their_text():
    """`arrow_of` 只给「属于一段有字的标注」的箭头（前端据此说「拖文字」）；纯箭头注释、
    坐标系逆算不回去的纯箭头注释、`FancyArrowPatch(path=…)` 都不给——它们没有文字可拖。"""
    proc = subprocess.run(
        [WORKER_PY, "-c", _ARROW_DRIVER, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["axes_0.texts_0.arrow"] == {"arrow_of": "axes_0.texts_0", "ends": False}, got
    assert got["axes_0.texts_1.arrow"] == {"arrow_of": None, "ends": True}, got
    assert got["axes_0.texts_2.arrow"] == {"arrow_of": None, "ends": False}, got
    path_arrow = [g for g in got if ".arrows_" in g]
    assert len(path_arrow) == 1 and got[path_arrow[0]] == {"arrow_of": None, "ends": False}, got
