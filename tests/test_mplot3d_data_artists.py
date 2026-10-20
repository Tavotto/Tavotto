"""3D 轴上的数据图元进元素表（ADR 0135）。

用户的「三维连续场云图」（曲面 + 3D contourf 底投影 + 独立 ScalarMappable 色条）在这之前整张图
只有文字和视角能改：`Poly3DCollection` / `QuadContourSet3D` / `Path3DCollection` / `Line3D` /
`Line3DCollection` 全进了 `unsupported`，点不中、改不了色，色条换色图也只换色条自己。这里钉的是
修复必须成立的几件事：

1. **登记**：按 2D 同族登记，gid 与 2D 同一条规则（`_collection_gid_prefix` 唯一出处）；
2. **只放真的生效的字段**：被 `do_3d_projection` 每帧覆盖的（`zorder`、逐点位置）不放；不可拖、
   不代理给宿主子图；
3. **几何**：bbox 与轮廓对得上真实墨迹，超点数预算退回 bbox 且不展开整个曲面；
4. **色阶兄弟**：独立 ScalarMappable 色条与共用 Norm 实例的曲面 / contourf 成组，撤销回原样
   （含没注册的自定义色图）；
5. **热态 == 冷启动重放**，含删除；
6. 容器（3D 误差棒 / 柱 / 茎）暂不做语义系列：成员如实留在 `unsupported`。

逐字还原、能力真实、枚举可用这三条通用扫描在 `tests/test_invariants_engine.py` 里已经把
`Inv3D` / `Inv3DP` / `Inv3DS` 三张图纳入（`support/invariant_figures.py`）。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里，几何事实走
`tests/support/mplot3d_probe.py` 子进程。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from support.overridesample import _editable_targets, _fields, _patch_for
from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "tests", "support", "mplot3d_probe.py")

EXTRA_SCRIPT = "fig_mplot3d_extra.py"

#: 三张本文件自己的图。`Z3D`：`computed_zorder=False`（zorder 归用户管）；`C3D`：3D 的误差棒 /
#: 柱 / 茎（容器暂不做语义系列）；`U3D`：单色曲面 + bar3d（`set_alpha(None)` 清不掉烘进面色的
#: alpha 的那一类）。
EXTRA = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    # ---- Z3D：zorder 归用户管（脚本自己关掉了按深度算序） ----
    fig = plt.figure(figsize=(4.0, 3.2))
    ax = fig.add_subplot(projection="3d", computed_zorder=False)
    g = np.linspace(-1.0, 1.0, 8)
    gx, gy = np.meshgrid(g, g)
    ax.plot_surface(gx, gy, gx * 0 + 0.0, color="#cc3333", linewidth=0, shade=False)
    ax.plot_surface(gx * 0.8, gy * 0.8, gx * 0 + 0.0, color="#3333cc", linewidth=0, shade=False)
    fig.savefig("Z3D.pdf")

    # ---- C3D：容器 ----
    fig = plt.figure(figsize=(4.0, 3.2))
    ax = fig.add_subplot(projection="3d")
    t = np.linspace(0.0, 1.0, 6)
    ax.errorbar(t, t, t, zerr=0.1)
    ax.bar([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], zs=0, zdir="y")
    ax.plot(t, t[::-1], t, color="#aa4400")
    ax.stem(t, t, t)
    fig.savefig("C3D.pdf")

    # ---- U3D：单色曲面 + 柱（面色不经色图） ----
    fig = plt.figure(figsize=(4.0, 3.2))
    ax = fig.add_subplot(projection="3d")
    ax.plot_surface(gx, gy, gx * gy, color="#2a6f3c", linewidth=0)
    ax.bar3d([1.2, 1.6], [1.2, 1.6], [0.0, 0.0], 0.3, 0.3, [0.8, 1.2], color="#4477aa")
    fig.savefig("U3D.pdf")

    # ---- S3D：逐点大小 / 线宽 / 面色的 3D 散点（每帧被重排成深度序的那几样） ----
    fig = plt.figure(figsize=(4.0, 3.2))
    ax = fig.add_subplot(projection="3d")
    r = np.random.RandomState(5)
    n = 16
    ax.scatter(
        r.rand(n), r.rand(n), r.rand(n),
        s=np.linspace(15.0, 300.0, n), linewidths=np.linspace(0.3, 3.0, n),
        c=plt.get_cmap("tab20")(np.arange(n) / 20.0), edgecolors="#222222", marker="o",
    )
    fig.savefig("S3D.pdf")

    # ---- F3D：边色跟着面色（`edgecolor="face"`）的逐面着色曲面 ----
    fig = plt.figure(figsize=(4.0, 3.2))
    ax = fig.add_subplot(projection="3d")
    surf = ax.plot_surface(gx, gy, gx * gy, color="#2a6f3c", linewidth=1.5)
    surf.set_edgecolor("face")  # 构造时传 edgecolor="face" 在 3.11 上会因 axes 还没挂上而抛
    # 脚本事后 `set_alpha`：mplot3d 只烤进面色、边仍不透明（与构造时传 alpha 的结果不同）
    surf2 = ax.plot_surface(gx + 3.0, gy, gx * gy, color="#4477aa", edgecolor="#222222", linewidth=1.0)
    surf2.set_alpha(0.6)
    fig.savefig("F3D.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("mplot3d-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    (figs / EXTRA_SCRIPT).write_text(EXTRA, encoding="utf-8")
    return figs


def _worker(library, script):
    w = pool.one_shot(script, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture(scope="module")
def hot(library):
    w = _worker(library, SCRIPT_NAME)
    yield w
    pool.discard(w)


@pytest.fixture(scope="module")
def extra(library):
    w = _worker(library, EXTRA_SCRIPT)
    yield w
    pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(worker, stem, patches, tag):
    path = worker.preview_png(stem, list(patches), 380, tag)
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _el(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里：{sorted(e['gid'] for e in man['elements'])}"
    return hits[0]


_DATA_ROLES = ("line", "fill", "scatter", "collection", "linecoll")

#: 每张图里 3D 数据图元的 (gid, role, 字段全集)。字段表是契约：多一个是「宣称了」，少一个是
#: 「漏了」。**没有 `zorder`**（`computed_zorder` 下每帧被覆盖）、**没有位置 / 数据偏移**。
_STYLE = {"alpha", "visible"}
_STROKE = {"edgecolor", "linewidth", "linestyle"}
_MAPPED = {"cmap", "vmin", "vmax"}
EXPECTED = {
    "Inv3D": {
        # 映射的曲面：面色走色图，不给 facecolor
        "axes_0.fill_0": ("fill", _STYLE | _STROKE | _MAPPED | {"hatch"}),
        # 3D contourf（底投影）
        "axes_0.collections_1": ("collection", _STYLE | _STROKE | _MAPPED | {"hatch", "label"}),
    },
    "Inv3DP": {
        # Line3D 的 zorder 是活的：`Axes3D.draw` 只给 Collection / Patch 重写 zorder
        "axes_0.lines_0": (
            "line",
            _STYLE
            | {"zorder", "color", "linewidth", "linestyle", "label"}
            | {"marker", "markersize", "markerfacecolor", "markeredgecolor"},
        ),
        # depthshade 的散点：facecolor 是整组统一色（逐点颜色 / 深度 alpha 不放）
        "axes_0.scatter_0": (
            "scatter",
            _STYLE | _STROKE | {"facecolor", "hatch", "label", "marker", "size"},
        ),
        "axes_0.scatter_1": (
            "scatter",
            _STYLE | _STROKE | _MAPPED | {"hatch", "label", "marker", "size"},
        ),
        "axes_0.linecoll_2": ("linecoll", _STYLE | {"color", "linewidth", "linestyle"}),
    },
    "Inv3DS": {
        "axes_0.fill_0": ("fill", _STYLE | _STROKE | {"facecolor", "hatch"}),  # bar3d
        "axes_0.fill_1": ("fill", _STYLE | _STROKE | _MAPPED | {"hatch"}),  # trisurf
        "axes_0.collections_2": (
            "collection",
            _STYLE | _STROKE | _MAPPED | {"label"},
        ),  # contour 线
        "axes_0.fill_3": ("fill", _STYLE | _STROKE | {"facecolor", "hatch"}),  # 单色曲面
    },
}


# ---------------------------------------------------------------------------
# 1 + 2. 登记、gid、字段全集、不可拖
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stem", sorted(EXPECTED))
def test_3d_data_artists_are_registered_with_2d_gids_and_honest_fields(hot, stem):
    """三张 3D 图里每个数据图元都进元素表，gid / role 与 2D 同族同规则，字段只放真生效的。"""
    man = _man(hot, stem)
    assert not man.get("unsupported"), (
        f"{stem}：还有 3D 数据图元漏在 unsupported 里 {man['unsupported']}"
    )
    seen = {e["gid"] for e in man["elements"] if e["role"] in _DATA_ROLES}
    assert seen == set(EXPECTED[stem]), (seen, set(EXPECTED[stem]))
    for gid, (role, props) in EXPECTED[stem].items():
        el = _el(man, gid)
        assert el["role"] == role, (gid, el["role"])
        got = {f["prop"] for f in el["editable"]}
        assert got == props, f"{stem} {gid}：多出 {sorted(got - props)}，缺 {sorted(props - got)}"
        # 不可拖，也不把「拖它」代理成拖宿主子图（面状色图集合在 2D 的那条代理对 3D 不成立）
        assert el.get("draggable") is False
        assert "geom_gid" not in el and "resizable" not in el, gid


def test_only_collections_lose_zorder_on_a_computed_zorder_axes(hot):
    """`computed_zorder=True` 的轴上，`Axes3D.draw` 每帧把 Collection / Patch 的 zorder 按深度重写——
    宣称它就是个死开关；但 `Line3D` 不在被重写之列，它的 zorder 是活的（Codex #1159 P1）。"""
    seen_line = False
    for stem in EXPECTED:
        for e in _man(hot, stem)["elements"]:
            if e["role"] not in _DATA_ROLES:
                continue
            has = "zorder" in {f["prop"] for f in e["editable"]}
            if e["role"] == "line":
                seen_line = True
                assert has, (stem, e["gid"], "Line3D 丢了一个真生效的 zorder")
            else:
                assert not has, (stem, e["gid"])
    assert seen_line


def test_line3d_zorder_changes_pixels_and_replays_exactly(hot, library):
    """Line3D 的 zorder：set 之后像素真变（螺旋线放到散点 / 线框前面）、撤销逐字回原样、热态 == 冷重放。"""
    stem, gid = "Inv3DP", "axes_0.lines_0"
    base = _man(hot, stem)
    base_png = _png(hot, stem, [], "lz-base")
    cur = next(f["value"] for f in _el(base, gid)["editable"] if f["prop"] == "zorder")
    patch = [{"gid": gid, "prop": "zorder", "value": cur + 50}]
    assert _png(hot, stem, patch, "lz-up") != base_png, "Line3D 的 zorder 宣称了，画面没动"
    man = _man(hot, stem, patch)
    assert next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == "zorder") == cur + 50
    fresh_man, fresh_png = _fresh(library, stem, patch)
    assert man == fresh_man
    assert _png(hot, stem, patch, "lz-hot") == fresh_png
    assert _man(hot, stem) == base
    assert _png(hot, stem, [], "lz-undo") == base_png


def test_zorder_is_live_when_the_script_turned_computed_zorder_off(extra):
    """脚本自己关了 `computed_zorder`，zorder 才归用户：这时给，而且真的改得动画面。"""
    base = _man(extra, "Z3D")
    surfaces = [e for e in base["elements"] if e["role"] == "fill"]
    assert len(surfaces) == 2
    for e in surfaces:
        assert "zorder" in {f["prop"] for f in e["editable"]}, e["gid"]
    first = surfaces[0]
    cur = next(f["value"] for f in first["editable"] if f["prop"] == "zorder")
    patch = [{"gid": first["gid"], "prop": "zorder", "value": cur + 10}]
    assert _png(extra, "Z3D", patch, "z-up") != _png(extra, "Z3D", [], "z-base"), (
        "zorder 宣称了，画面一个像素没变（被覆盖或被遮住）"
    )
    got = _el(_man(extra, "Z3D", patch), first["gid"])
    assert next(f["value"] for f in got["editable"] if f["prop"] == "zorder") == cur + 10
    assert _man(extra, "Z3D") == base


def test_3d_containers_stay_unsupported_and_are_not_double_registered(extra):
    """3D 的误差棒 / 柱 / 茎不做语义系列：成员**不**当独立曲线登记，如实留在 unsupported。"""
    man = _man(extra, "C3D")
    classes = {row["cls"].rsplit(".", 1)[-1] for row in man.get("unsupported", [])}
    assert {"Line3D", "Line3DCollection", "Patch3D"} <= classes, man.get("unsupported")
    # 误差棒的数据线是 `lines_0`（容器成员，不登记）；脚本自己 `plot` 的那条才是元素
    lines = [e["gid"] for e in man["elements"] if e["role"] == "line"]
    assert lines == ["axes_0.lines_1"], lines
    # 3D stem 的 baseline（`ax.lines` 里的 Line3D）也是容器成员：不单独登记、留在 unsupported，
    # 与它的 markerline / stemlines 一致（Codex #1159 P2）。Line3D 共 3 条：误差棒的数据线 1 +
    # stem 的 markerline 1 + stem 的 baseline 1（修之前 baseline 被登记成曲线，只剩 2 条）
    rows = {r["cls"].rsplit(".", 1)[-1]: r["count"] for r in man["unsupported"]}
    assert rows.get("Line3D") == 3, man["unsupported"]


# ---------------------------------------------------------------------------
# 3. 几何：bbox / 轮廓对真实墨迹，预算早退，逐点大小对号
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def facts():
    proc = subprocess.run(
        [WORKER_PY, PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_bbox_and_outline_match_the_real_ink(facts):
    """单独画某个 3D 图元时的真实墨迹包围盒 vs manifest 的 bbox；轮廓顶点落在墨迹上。"""
    assert len(facts["ink"]) >= 6
    for row in facts["ink"]:
        # 散点的 bbox 是标记中心的范围（与 2D 同口径），不含标记半径
        tol = 0.03 if row["role"] == "scatter" else 0.015
        assert row["geometry_cover"] is None or row["geometry_cover"] >= 0.98, row
        for a, b in zip(row["ink"], row["bbox"], strict=True):
            assert abs(a - b) <= tol, (row["gid"], row["ink"], row["bbox"])


def test_huge_surface_falls_back_to_bbox_without_expanding_every_face(facts):
    """160×160 的曲面（25 281 个面）超点数预算：退回 bbox，而且是**早退**——不把每个面都展开。

    判据按展开次数数，不按时钟：时钟在慢机器上会假红，而「是不是把 2.5 万个面都展开了」才是
    拖垮 manifest 构建的那件事。"""
    b = facts["budget"]
    assert b["has_geometry"] is False
    assert b["display_subpaths_calls"] < b["faces"] // 10, b


def test_per_point_sizes_land_on_their_own_points(facts):
    """逐点大小不同的 3D 散点：每个轮廓的直径是它自己那一点的大小（不是深度序里别人的）。"""
    rows = facts["sizes"]["markers"]
    assert len(rows) == facts["sizes"]["n"]
    for r in rows:
        assert abs(r["diameter_px"] - r["expected_px"]) <= 0.05 * r["expected_px"] + 0.3, r
        assert r["off_px"] < 2.0, r


# ---------------------------------------------------------------------------
# 4. 色阶兄弟：独立 ScalarMappable 色条 ↔ 曲面 ↔ 3D contourf
# ---------------------------------------------------------------------------
def test_standalone_colorbar_is_the_scale_sibling_of_surface_and_contourf(hot):
    stem, cb, surf, cont = "Inv3D", "axes_1.colorbar", "axes_0.fill_0", "axes_0.collections_1"
    base = _man(hot, stem)
    assert set(_el(base, cb)["scale_gids"]) == {surf, cont}
    assert "mappable_gid" not in _el(base, cb), "独立 ScalarMappable 图上没有对应的图元"
    base_png = _png(hot, stem, [], "3d-base")
    orig_cmap = _fields(base, surf)["cmap"]
    assert orig_cmap["value"] == "coolwarm_preview"
    assert orig_cmap["cmap_current"]["custom"] is True  # 没注册的自定义色图

    # 从色条改：曲面和 contourf 一起变，画面真的变
    man = _man(hot, stem, [{"gid": cb, "prop": "cmap", "value": "plasma"}])
    for gid in (surf, cont, cb):
        assert _fields(man, gid)["cmap"]["value"] == "plasma", gid
    assert _png(hot, stem, [{"gid": cb, "prop": "cmap", "value": "plasma"}], "3d-cb") != base_png
    # vmin / vmax 经共用的 Norm 对象，从曲面改三者一起变
    man = _man(hot, stem, [{"gid": surf, "prop": "vmin", "value": 0.25}])
    for gid in (surf, cont, cb):
        assert _fields(man, gid)["vmin"]["value"] == pytest.approx(0.25), gid
    # 撤销：逐字回到脚本原样，包括没注册的自定义色图
    after = _man(hot, stem)
    assert after == base
    assert _fields(after, surf)["cmap"]["cmap_current"]["custom"] is True
    assert _png(hot, stem, [], "3d-after") == base_png


# 「从曲面改 cmap」整组变的用例见 tests/test_scale_group_cmap.py（用户裁决 2026-10-18 推翻了原先「窄 override 压过广播」）


# ---------------------------------------------------------------------------
# 5. 热态 == 冷启动重放（含删除）
# ---------------------------------------------------------------------------
def _fresh(library, stem, patches, script=SCRIPT_NAME):
    w = _worker(library, script)
    try:
        resp = w.override(stem, list(patches))
        assert not (resp.get("warnings") or []), resp["warnings"]
        return resp["manifest"], _png(w, stem, patches, "fresh")
    finally:
        pool.discard(w)


#: 共用同一个 Norm 对象的两个 mappable，各自的**窄** vmin / vmax override 叠在一起时，后采的那份
#: 「脚本原样」已经被先改的那个经共用的 norm 污染（2D 的 `InvShared` 同样复现：已知的存量缺口，
#: 不是 3D 引入的——见 ADR 0135「不做」）。本用例的主语是 3D 图元自己的字段，所以曲面与 contourf
#: 只留曲面那条窄的；色条广播 + 窄兄弟的组合另有 `K-3d-*` 用例。
_SHARED_NORM_SKIP = {
    ("Inv3D", "axes_0.collections_1", "vmin"),
    ("Inv3D", "axes_0.collections_1", "vmax"),
}


def _without_origin_hints(man):
    """去掉字段上的 `value_original`（「脚本原样」的显示提示）。

    它在**第一次应用那一刻**才采，同一个元素上两条互相牵动的 prop（线色 → `markerfacecolor: auto`、
    散点面色 → `edgecolor: face`）逐步累加时，后采的那条采到的是被先改的污染过的值——2D 的
    `InvMix` 逐位同样复现（存量行为，不是 3D 引入的），热态与冷启动于是在这个提示上分叉，而状态、
    像素、其余字段都一致。真正的撤销是否回到原样由逐字还原那组扫描和本用例末尾的 `== base` 管。"""
    clean = json.loads(json.dumps(man))
    for el in clean["elements"]:
        for f in el.get("editable", []):
            f.pop("value_original", None)
    return clean


@pytest.mark.parametrize("stem", ["Inv3D", "Inv3DP", "Inv3DS"])
def test_hot_equals_cold_replay_for_every_3d_prop_added_then_half_removed(hot, library, stem):
    """每个 3D 数据图元的每条字段都改一遍（逐步累加），热态 == 冷启动重放；再撤掉一半，仍然相等。"""
    base = _man(hot, stem)
    advertised = {el["gid"]: {f["prop"] for f in el["editable"]} for el in base["elements"]}
    roles = {el["gid"]: el["role"] for el in base["elements"]}
    steps: list[dict] = []
    try:
        for gid, field in _editable_targets(base):
            if roles[gid] not in _DATA_ROLES or (stem, gid, field["prop"]) in _SHARED_NORM_SKIP:
                continue
            enablers, patch = _patch_for(gid, field, advertised)
            if patch is None:
                continue
            for p in patch:
                steps = [q for q in steps if (q["gid"], q["prop"]) != (p["gid"], p["prop"])] + [p]
            hot.override(stem, list(steps))  # 逐步累加：热会话走增量应用
        assert len(steps) >= 12, f"{stem} 只扫到 {len(steps)} 条，覆盖太薄"
        for label, patches in (("全量", steps), ("撤掉一半", steps[::2])):
            hot_man = _man(hot, stem, patches)
            hot_png = _png(hot, stem, patches, f"hot-{label}")
            fresh_man, fresh_png = _fresh(library, stem, patches)
            assert _without_origin_hints(hot_man) == _without_origin_hints(fresh_man), (
                f"{stem} {label}：热态与冷启动重放的 manifest 不一致"
            )
            assert hot_png == fresh_png, f"{stem} {label}：manifest 一样但画出来不一样"
    finally:
        assert _man(hot, stem) == base  # 全撤；同时别把脏状态留给下一个用例


@pytest.mark.parametrize("stem", ["Inv3D", "Inv3DP", "Inv3DS"])
def test_hidden_3d_artists_follow_the_view_like_a_cold_replay(hot, library, stem):
    """先转视角、再把 3D 数据图元藏起来：热态 == 冷启动重放。

    `Axes3D.draw` 只对**可见**的集合重投影，藏起来的那个带着上一次可见时的投影——热会话里它
    停在转之后的视角，冷启动重放里它还停在脚本原样的视角，两边 bbox / 轮廓不同。写回自检比的
    就是这个几何，会话于是 409 `replay_divergence`（藏起来的元素仍在元素树里，要能点着取消隐藏）。"""
    base = _man(hot, stem)
    hide = [
        {"gid": e["gid"], "prop": "visible", "value": False}
        for e in base["elements"]
        if e["role"] in _DATA_ROLES
    ]
    rotate = [{"gid": "axes_0", "prop": "azim", "value": 20.0}]
    hot.override(stem, rotate)
    hot_man = _man(hot, stem, rotate + hide)
    fresh_man, fresh_png = _fresh(library, stem, rotate + hide)
    moved = {e["gid"]: e["bbox"] for e in hot_man["elements"] if e["role"] in _DATA_ROLES}
    assert moved != {e["gid"]: e["bbox"] for e in base["elements"] if e["role"] in _DATA_ROLES}, (
        "转视角没动任何几何，用例是空的"
    )
    assert hot_man == fresh_man, f"{stem}：隐藏的 3D 图元几何停在旧视角（热态 ≠ 冷启动重放）"
    assert _png(hot, stem, rotate + hide, "hidden") == fresh_png
    assert _man(hot, stem) == base


@pytest.mark.parametrize("stem", ["Inv3D", "Inv3DP", "Inv3DS"])
def test_line3d_on_a_hidden_axis_follows_the_view_like_a_cold_replay(hot, library, stem):
    """先转视角（轴可见）、再把**整条 3D 轴**藏起来、再转视角：热态 == 冷启动重放（#1159 评审）。

    Line3D 没有 `do_3d_projection`，投影在它自己的 `draw` 里；整条轴隐藏时 `Axes3D.draw` 不访问它。
    热会话里它停在第一次转动的投影，冷启动重放里停在脚本原样的投影——曲线 bbox 两套，写回 409。"""
    base = _man(hot, stem)
    lines = [e["gid"] for e in base["elements"] if e["role"] == "line"]
    if not lines:
        pytest.skip(f"{stem} 里没有 3D 曲线")
    first = [{"gid": "axes_0", "prop": "azim", "value": 20.0}]
    hot.override(stem, first)
    seq = [
        {"gid": "axes_0", "prop": "visible", "value": False},
        {"gid": "axes_0", "prop": "azim", "value": -80.0},
    ]
    hot.override(stem, [seq[0], first[0]])  # 藏起整条轴（视角仍是第一次转动）
    hot_man = _man(hot, stem, seq)  # 再转视角
    fresh_man, fresh_png = _fresh(library, stem, seq)
    box = {e["gid"]: e["bbox"] for e in hot_man["elements"] if e["gid"] in lines}
    assert box != {e["gid"]: e["bbox"] for e in base["elements"] if e["gid"] in lines}, (
        "转视角没动曲线几何，用例是空的"
    )
    assert hot_man == fresh_man, f"{stem}：隐藏轴上的 Line3D 停在旧视角（热态 ≠ 冷启动重放）"
    assert _png(hot, stem, seq, "hidden-axis") == fresh_png
    assert _man(hot, stem) == base


# ---------------------------------------------------------------------------
# 单色曲面 / 柱的 alpha：撤销要把烘进面色的 alpha 也还回去
# ---------------------------------------------------------------------------
def test_alpha_undo_on_unmapped_surface_and_bars_restores_the_pixels(extra):
    """`Poly3DCollection.set_alpha(None)` 清不掉 `_facecolor3d` 里已经烘进去的 alpha（三个版本同一份代码）：
    单色曲面 / bar3d 设 0.3 再撤销，面色停在 0.3。"""
    stem = "U3D"
    base = _man(extra, stem)
    base_png = _png(extra, stem, [], "u-base")
    fills = [e["gid"] for e in base["elements"] if e["role"] == "fill"]
    assert len(fills) == 2
    for gid in fills:
        patch = [{"gid": gid, "prop": "alpha", "value": 0.3}]
        assert _png(extra, stem, patch, f"u-{gid}") != base_png
        _man(extra, stem, patch)
        assert _man(extra, stem) == base
        assert _png(extra, stem, [], f"u-undo-{gid}") == base_png, f"{gid}：alpha 撤销后画面没回来"


def test_poly3d_edgecolor_and_alpha_do_not_depend_on_which_came_first(extra, library):
    """mplot3d 的 `set_alpha` 不碰边色、`set_edgecolor` 却按**当时的** alpha 合成：先边色后 alpha，边不透明；
    先 alpha 后边色，边半透明。热会话逐步累加（早就落了的那条被「值没变」跳过）与冷启动按**列表序**重放
    因此画出两张图（写回像素门 409）。面色 / 边色 / alpha 改完一律按原始设定 + 此刻 alpha 重建
    （`overrides._canonical_poly3d_colors`）。**列表序必须与历史序不同**才测得到：历史先边色、列表里
    alpha 排在边色前面；反过来再来一遍。"""
    stem = "U3D"
    gid = next(e["gid"] for e in _man(extra, stem)["elements"] if e["role"] == "fill")
    ec = {"gid": gid, "prop": "edgecolor", "value": "#ff00ff"}
    lw = {"gid": gid, "prop": "linewidth", "value": 2.0}
    al = {"gid": gid, "prop": "alpha", "value": 0.4}
    for first, then in (([ec, lw], [al, ec, lw]), ([al, lw], [ec, al, lw])):
        extra.override(stem, first)
        _man(extra, stem, then)
        hot_png = _png(extra, stem, then, "order-hot")
        fresh_man, fresh_png = _fresh(library, stem, then, EXTRA_SCRIPT)
        assert hot_png == fresh_png, f"{[p['prop'] for p in first]} → {[p['prop'] for p in then]}"
        _man(extra, stem)


def test_poly3d_edgecolor_face_survives_override_and_undo(extra):
    """`edgecolor="face"` 的逐面着色曲面：覆盖边色再撤销，要回到「边跟着各自的面」。`Collection.get_edgecolor`
    在 `'face'` 时回 3D 子类**深度排序后**的面色，直接写进 `_edgecolor3d` 会把逐面的边色打乱。"""
    stem = "F3D"
    base = _man(extra, stem)
    base_png = _png(extra, stem, [], "f-base")
    gid = next(e["gid"] for e in base["elements"] if e["role"] == "fill")
    patch = [{"gid": gid, "prop": "edgecolor", "value": "#ff00ff"}]
    assert _png(extra, stem, patch, "f-on") != base_png
    _man(extra, stem, patch)
    assert _man(extra, stem) == base
    assert _png(extra, stem, [], "f-undo") == base_png


def test_dashed_zero_width_edges_do_not_break_the_vector_preview(extra):
    """`linewidth=0` 的曲面（3D 的惯用写法）上先选虚线、再配边色与花纹：dash 按线宽缩放成全 0，
    SVG / PDF 的 `set_dashes` 当场抛 ValueError、预览画不出来（2D 的 `fill_between` 逐位同样复现）。"""
    stem = "U3D"
    base = _man(extra, stem)
    base_png = _png(extra, stem, [], "dash-base")
    gid = next(e["gid"] for e in base["elements"] if e["role"] == "fill")
    assert next(f["value"] for f in _el(base, gid)["editable"] if f["prop"] == "linewidth") == 0
    patch = [
        {"gid": gid, "prop": "linestyle", "value": "--"},
        {"gid": gid, "prop": "edgecolor", "value": "#ff00ff"},
        {"gid": gid, "prop": "hatch", "value": "/"},
    ]
    _man(
        extra, stem, patch
    )  # 修之前这里 WorkerError：At least one value in the dash list must be positive
    assert _png(extra, stem, patch, "dash-on") != base_png
    # 线宽再调成正数，虚线要真的出现（`_us_linestyles` 原样保留，matplotlib 自己重新缩放）
    both = [*patch, {"gid": gid, "prop": "linewidth", "value": 2.0}]
    assert _png(extra, stem, both, "dash-lw") != _png(extra, stem, patch, "dash-on")
    assert _man(extra, stem) == base
    assert _png(extra, stem, [], "dash-undo") == base_png


def test_per_point_sizes_and_linewidths_restore_in_the_script_order(extra):
    """逐点大小 / 线宽不同的 3D 散点：`get_sizes()` / `get_linewidths()` 读到的是上一帧的深度序，拿它当
    「脚本原样」撤销，逐点大小就被打乱。原样从 `_sizes3d` / `_linewidths3d` 取（`collection_sizes` / `collection_linewidths`）。"""
    stem = "S3D"
    base = _man(extra, stem)
    base_png = _png(extra, stem, [], "s-base")
    gid = next(e["gid"] for e in base["elements"] if e["role"] == "scatter")
    for prop, value in (("size", 120.0), ("linewidth", 2.5)):
        patch = [{"gid": gid, "prop": prop, "value": value}]
        assert _png(extra, stem, patch, f"s-{prop}") != base_png, f"{prop} 宣称了，画面没动"
        _man(extra, stem, patch)
        assert _man(extra, stem) == base, f"{prop}：撤销后 manifest 没回原样"
        assert _png(extra, stem, [], f"s-undo-{prop}") == base_png, (
            f"{prop}：撤销后画面不是脚本原样"
        )


def test_scatter_facecolor_value_does_not_follow_the_view(extra):
    """3D 散点的 `get_facecolor()` 是深度排序后的数组，`[0]` 随视角变；字段值读未排序的原始面色，转视角不该动它。"""
    stem = "S3D"
    base = _man(extra, stem)
    gid = next(e["gid"] for e in base["elements"] if e["role"] == "scatter")
    before = _fields(base, gid)["facecolor"]["value"]
    for azim in (20.0, 75.0, 140.0):
        man = _man(extra, stem, [{"gid": "axes_0", "prop": "azim", "value": azim}])
        assert _fields(man, gid)["facecolor"]["value"] == before, azim
    _man(extra, stem)


def test_poly3d_scripts_own_alpha_quirk_survives_override_and_undo(extra):
    """脚本事后 `set_alpha(0.6)` 留下的状态（面半透明、边不透明）是 mplot3d 的怪癖，但它就是脚本原样：
    覆盖 alpha 再撤销必须**逐位**回到它，而不是被规范化成「边也半透明」（`_POLY3D_NATIVE` 快照）。"""
    stem = "F3D"
    base = _man(extra, stem)
    base_png = _png(extra, stem, [], "q-base")
    fills = [e["gid"] for e in base["elements"] if e["role"] == "fill"]
    assert len(fills) == 2
    gid = fills[1]
    patch = [{"gid": gid, "prop": "alpha", "value": 0.3}]
    assert _png(extra, stem, patch, "q-on") != base_png
    _man(extra, stem, patch)
    assert _man(extra, stem) == base
    assert _png(extra, stem, [], "q-undo") == base_png


def test_2d_stem_baseline_is_still_an_ordinary_editable_line(hot):
    """3D 容器把 baseline 算成员，2D 不变：stem 的 baseline 仍是单独可编辑的普通曲线（`axes_i.lines_k`）。"""
    man = _man(hot, "InvCont")
    lines = [e for e in man["elements"] if e["role"] == "line"]
    assert lines, "2D stem 的 baseline 不再登记成曲线了"
    assert all("color" in {f["prop"] for f in e["editable"]} for e in lines)


def test_hidden_mapped_scatter_reads_fresh_edge_color_after_cmap_change(hot, library):
    """隐藏的映射散点（边色 `'face'`）：隐藏期间换色图，`Axes3D.draw` 不画它、`_facecolors` 停在旧映射色，
    边色字段读到的面色就是过期的（热态 '#1f77b4' vs 冷启动 '#4ac16d'）。读之前先 `update_scalarmappable`。"""
    stem, gid = "Inv3DP", "axes_0.scatter_1"
    base = _man(hot, stem)
    hide = {"gid": gid, "prop": "visible", "value": False}
    cmap = {"gid": gid, "prop": "cmap", "value": "plasma"}
    # 历史里有过 alpha：`Collection.set_alpha` 撤销时按**原始设定**重置 `_facecolors`（映射色被冲掉、要等
    # 下一次 draw 才算回来），而此刻它已经被藏起来、不会再画
    hot.override(stem, [{"gid": gid, "prop": "alpha", "value": 0.9}])
    man = _man(hot, stem, [hide, cmap])
    fresh_man, _png_fresh = _fresh(library, stem, [hide, cmap])

    def edge(m):
        return next(f["value"] for f in _el(m, gid)["editable"] if f["prop"] == "edgecolor")

    assert edge(man) == edge(fresh_man)
    assert _man(hot, stem) == base
