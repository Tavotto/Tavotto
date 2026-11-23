"""ADR 0205 PR 1：图内副本 `copy_of` 的引擎行为（真 matplotlib，worker 解释器）。

副本是一条普通 override——`{gid: "<宿主>.copy_<8 位>", prop: "copy_of", value: {src, text, at}}`——
重放时新建一段 `Text`（样式按引用抄源、位置与文字按快照），之后对副本 gid 的修改都走既有 text handler。
这里钉的是：

  * **热态 == 冷重放**：源 × 布局引擎（无 / tight / constrained）下，副本外框热冷一致、其余元素加副本前后
    外框不动（副本不进布局）、副本与源同尺寸；
  * **撤销 / 删除**：去掉 `copy_of` 副本消失、元素表里没有它，别的元素与没加过一样；
  * **登记**：副本只出现一次、gid 是它自己的（不是 `texts_N`）、角色沿用源；`instrument` 的按序登记不数它；
  * **副本上的修改**：fontsize / text / color 落得上；去掉 `copy_of` 而留着这些修改 → 「元素不存在」；
  * **不支持 / 不存在的源** → warning 且不崩，不留半个副本；
  * **数据不变**：加副本不动任何数据元素的指纹；
  * **写回验证门**：带副本走真的 prepare → 一次性 worker 全量重放 → 几何 + 像素，产物里有副本的字。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool
from test_worker_roundtrip import replay_bases, write_back  # noqa: F401 — 写回夹具

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

STEM = "Dup"
SCRIPT_NAME = "dup.py"
SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    kw = {"layout": LAYOUT} if LAYOUT else {}
    fig, ax = plt.subplots(figsize=(4, 3), **kw)
    ax.plot([0, 1], [0, 1], label="line")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_title("Title")
    ax.set_ylabel("Signal (a.u.)")
    ax.text(0.2, 0.8, "data text", fontsize=9, color="C3")
    ax.annotate("note", xy=(0.5, 0.5), xytext=(0.6, 0.2), arrowprops=dict(arrowstyle="->"))
    ax.text(0.7, 0.9, "boxed", bbox=dict(boxstyle="round", fc="w", ec="k"))
    ax.legend()
    fig.text(0.5, 0.02, "fig text")
    fig.savefig("Dup.pdf")
"""

#: 源 → (副本的宿主前缀, 源 gid, 落点)。落点是宿主的分数（子图 axes 分数 / figure 分数），
#: 取源附近错开的一点；热冷对拍与「同尺寸」只要求按 `at` 落，锚点怎么算是前端的事（ADR 0205 §二）
SOURCES = {
    "title": ("axes_0", "axes_0.title", [0.5, 0.7]),
    "ylabel": ("axes_0", "axes_0.ylabel", [-0.14, 0.4]),
    "datatext": ("axes_0", "axes_0.texts_0", [0.3, 0.6]),
    "annotate": ("axes_0", "axes_0.texts_1", [0.7, 0.3]),
    "boxed": ("axes_0", "axes_0.texts_2", [0.5, 0.5]),
    "figtext": ("fig", "fig.texts_0", [0.5, 0.1]),
}
LAYOUTS = {"none": None, "tight": "tight", "constrained": "constrained"}


def _script(layout: str | None) -> str:
    return SCRIPT.replace("LAYOUT", repr(layout))


def _worker(tmp_path_factory, layout: str | None):
    d = tmp_path_factory.mktemp("dup-copy")
    (d / SCRIPT_NAME).write_text(_script(layout), encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(d), "main")
    w.ensure_built()
    return w


@pytest.fixture(scope="module", params=list(LAYOUTS))
def rig(request, tmp_path_factory):
    """(布局名, 热 worker, 造一个全新冷 worker 的函数)。冷 worker 用完自己回收。"""
    layout = LAYOUTS[request.param]
    hot = _worker(tmp_path_factory, layout)
    made = []

    def cold():
        w = _worker(tmp_path_factory, layout)
        made.append(w)
        return w

    try:
        yield request.param, hot, cold
    finally:
        pool.discard(hot)
        for w in made:
            pool.discard(w)


def _copy_patch(host: str, key: str, src: str, text: str, at, cid: str = "ab12cd34") -> dict:
    return {
        "gid": f"{host}.copy_{cid}",
        "prop": "copy_of",
        "value": {"src": src, "text": text, "at": list(at)},
    }


def _els(resp) -> dict:
    return {e["gid"]: e for e in resp["manifest"]["elements"]}


def _boxes(resp, only=None) -> dict:
    return {
        g: [round(v, 6) for v in e["bbox"]]
        for g, e in _els(resp).items()
        if "bbox" in e and (only is None or g in only)
    }


def _text_of(src_key: str) -> str:
    return {
        "title": "Title",
        "ylabel": "Signal (a.u.)",
        "datatext": "data text",
        "annotate": "note",
        "boxed": "boxed",
        "figtext": "fig text",
    }[src_key]


def _cold(rig_cold, patches):
    w = rig_cold()
    try:
        return w.override(STEM, patches)
    finally:
        pool.discard(w)


@pytest.mark.parametrize("src_key", list(SOURCES))
def test_copy_hot_equals_cold_and_leaves_everything_else_alone(rig, src_key):
    """热态（先只有副本、再叠一条改样式）== 冷重放整份列表；副本不动其余元素；与源同尺寸。"""
    layout, hot, cold = rig
    host, src, at = SOURCES[src_key]
    cp = _copy_patch(host, src_key, src, _text_of(src_key), at)
    cgid = cp["gid"]

    base = hot.override(STEM, [])
    assert not base["warnings"]
    base_boxes = _boxes(base)

    step1 = hot.override(STEM, [cp])
    assert step1["warnings"] == [], step1["warnings"]
    assert cgid in _els(step1), "副本没进元素表"
    # 副本不进布局引擎：其余元素的外框与没有副本时逐位相同（布局引擎 × 副本的核心保证）
    moved = {g: b for g, b in _boxes(step1).items() if g != cgid and base_boxes.get(g) != b}
    assert not moved, f"{layout}/{src_key}：加副本挪动了别的元素 {moved}"
    # 与源同字体 → 同尺寸（竖排标题 w/h 也同）
    sb, cb = _els(step1)[src]["bbox"], _els(step1)[cgid]["bbox"]
    assert cb[2] == pytest.approx(sb[2], abs=1e-4) and cb[3] == pytest.approx(sb[3], abs=1e-4)

    # 热态再叠一条副本上的样式修改，对拍冷重放整份列表
    full = [cp, {"gid": cgid, "prop": "color", "value": "#cc0000"}]
    hot_resp = hot.override(STEM, full)
    cold_resp = _cold(cold, full)
    assert hot_resp["warnings"] == [] and cold_resp["warnings"] == []
    assert _boxes(hot_resp) == _boxes(cold_resp), f"{layout}/{src_key}：热冷外框不一致"
    assert _els(hot_resp)[cgid]["editable"] == _els(cold_resp)[cgid]["editable"]
    # 连同 value_original 等整份元素条目逐项相等
    assert _els(hot_resp) == _els(cold_resp)

    # 撤销：整份列表清空，副本消失，其余元素回到加副本之前的样子
    back = hot.override(STEM, [])
    assert back["warnings"] == []
    assert cgid not in _els(back)
    assert _boxes(back) == base_boxes
    assert _els(back) == _els(base)


def _anchor_at(base: dict, host: str, src: str) -> list[float]:
    """源**画出来的锚点**（manifest 的 `anchor`：figure 分数，自顶向下）换成宿主的分数——前端 PR 3 要做的那一步。"""
    els = _els(base)
    ax_, ay_ = els[src]["anchor"]
    if host == "fig":
        return [ax_, 1.0 - ay_]
    pos = next(f["value"] for f in els[host]["editable"] if f["prop"] == "position")
    px, py, pw, ph = pos  # figure 分数，自底向上
    return [(ax_ - px) / pw, ((1.0 - ay_) - py) / ph]


@pytest.mark.parametrize("src_key", list(SOURCES))
def test_copy_placed_at_the_sources_own_anchor_lands_exactly_on_it(rig, src_key):
    """`at` 取源画出来的锚点 → 副本外框与源外框重合：样式（含竖排标题的 `rotation_mode`）一个不漏地抄过来了。"""
    layout, hot, _c = rig
    host, src, _at = SOURCES[src_key]
    base = hot.override(STEM, [])
    cp = _copy_patch(host, src_key, src, _text_of(src_key), _anchor_at(base, host, src))
    resp = hot.override(STEM, [cp])
    assert resp["warnings"] == []
    got = _els(resp)[cp["gid"]]["bbox"]
    want = _els(base)[src]["bbox"]
    assert got == pytest.approx(want, abs=2e-3), (
        f"{layout}/{src_key}：副本 {got} 与源 {want} 不重合"
    )
    hot.override(STEM, [])


SOURCE_EDITS = [
    ("color", "#2200ff"),
    ("fontsize", 17),
    ("weight", "bold"),
    ("alpha", 0.5),
    ("style", "italic"),
    ("rotation", 20),
    ("ha", "right"),
    ("linespacing", 2.0),
    ("zorder", 9),
    ("visible", False),
    ("bbox_visible", True),
    ("bbox_facecolor", "#ffee00"),
    ("bbox_pad", 0.9),
    ("stroke_enabled", True),
]


@pytest.mark.parametrize("src_key", ["title", "datatext", "boxed", "figtext"])
def test_copy_takes_the_scripts_source_not_its_edited_state(rig, src_key):
    """热会话里源早已被改过样式、之后才加副本：副本仍是脚本原样的源（冷重放里副本先于源的修改建出）。"""
    layout, hot, cold = rig
    host, src, at = SOURCES[src_key]
    edits = [{"gid": src, "prop": p, "value": v} for p, v in SOURCE_EDITS]
    cp = _copy_patch(host, src_key, src, _text_of(src_key), at)
    base = hot.override(STEM, [])
    assert hot.override(STEM, edits)["warnings"] == []
    resp = hot.override(STEM, [*edits, cp])
    assert resp["warnings"] == []
    cold_resp = _cold(cold, [*edits, cp])
    assert cold_resp["warnings"] == []
    assert _els(resp) == _els(cold_resp), f"{layout}/{src_key}：热冷不一致"
    # 副本的字段 == 脚本原样源的字段（颜色 / 字号 / 字重 / 透明度 / 底框 / 描边都没沾到源上的修改）
    want = {f["prop"]: f["value"] for f in _els(base)[src]["editable"]}
    got = {f["prop"]: f["value"] for f in _els(resp)[cp["gid"]]["editable"]}
    for prop in tuple(p for p, _v in SOURCE_EDITS):
        assert got[prop] == want[prop], (prop, got[prop], want[prop])
    # 源本身仍带着它的修改
    assert {f["prop"]: f["value"] for f in _els(resp)[src]["editable"]}["color"] == "#2200ff"
    hot.override(STEM, [])


def test_edits_listed_before_copy_of_still_land_on_the_copy(rig):
    """排序档的看护：列表里副本上的修改写在 `copy_of` 之前，也要先建副本再改样式（热冷各一次到位）。"""
    layout, hot, cold = rig
    host, src, at = SOURCES["datatext"]
    cp = _copy_patch(host, "datatext", src, "data text", at)
    g = cp["gid"]
    patches = [
        {"gid": g, "prop": "fontsize", "value": 16},
        {"gid": g, "prop": "color", "value": "#118811"},
        {"gid": "axes_0", "prop": "position", "value": [0.2, 0.2, 0.6, 0.6]},
        cp,
    ]
    resp = hot.override(STEM, patches)
    assert resp["warnings"] == [], resp["warnings"]
    cold_resp = _cold(cold, patches)
    assert cold_resp["warnings"] == []
    fields = {f["prop"]: f["value"] for f in _els(resp)[g]["editable"]}
    assert fields["fontsize"] == 16 and fields["color"] == "#118811"
    assert _els(resp) == _els(cold_resp)
    # 子图挪了，副本（transAxes）跟着走
    base = hot.override(STEM, [])
    assert _els(resp)[g]["bbox"] != _els(base)["axes_0.texts_0"]["bbox"]
    hot.override(STEM, [])


def test_dragging_and_rotating_a_copy_matches_cold_replay_and_undoes(rig):
    """副本上的 `pos_frac`（拖动，PR 3 的必经路）与 `rotation`：热冷一致，撤销回到刚粘出来的样子。"""
    layout, hot, cold = rig
    for src_key in ("datatext", "title", "figtext"):
        host, src, at = SOURCES[src_key]
        cp = _copy_patch(host, src_key, src, _text_of(src_key), at)
        g = cp["gid"]
        fresh = hot.override(STEM, [cp])
        moved = [cp, {"gid": g, "prop": "pos_frac", "value": [0.4, 0.45]}]
        rot = [*moved, {"gid": g, "prop": "rotation", "value": 25}]
        for patches in (moved, rot):
            resp = hot.override(STEM, patches)
            assert resp["warnings"] == [], (src_key, resp["warnings"])
            cold_resp = _cold(cold, patches)
            assert cold_resp["warnings"] == []
            assert _els(resp) == _els(cold_resp), f"{layout}/{src_key}：热冷不一致"
        assert _els(resp)[g]["bbox"] != _els(fresh)[g]["bbox"]
        back = hot.override(STEM, [cp])  # 去掉拖动与旋转
        assert _els(back) == _els(fresh), f"{layout}/{src_key}：撤销没有回到刚粘出来的样子"
        hot.override(STEM, [])


@pytest.mark.parametrize(
    "at", [[1.35, 0.5], [-0.45, 0.5], [0.5, -0.4]], ids=["right", "left", "below"]
)
def test_a_copy_outside_the_axes_does_not_move_the_layout(rig, at):
    """副本落在子图外（刻度、标签的地盘）：tight / constrained 布局不把它算进边距，别的元素一个不动。"""
    layout, hot, _c = rig
    base = hot.override(STEM, [])
    cp = _copy_patch("axes_0", "datatext", "axes_0.texts_0", "data text", at)
    resp = hot.override(STEM, [cp])
    assert resp["warnings"] == []
    assert cp["gid"] in _els(resp)
    base_boxes = _boxes(base)
    moved = {g: b for g, b in _boxes(resp).items() if g != cp["gid"] and base_boxes.get(g) != b}
    assert not moved, f"{layout}：副本落在 {at} 挪动了别的元素 {moved}"
    hot.override(STEM, [])


def test_copy_registers_once_under_its_own_gid_with_the_sources_role(rig):
    layout, hot, _cold_ = rig
    patches = [
        _copy_patch("axes_0", "title", "axes_0.title", "Title", [0.5, 0.7], "aaaa1111"),
        _copy_patch("axes_0", "ylabel", "axes_0.ylabel", "Signal (a.u.)", [-0.14, 0.4], "bbbb2222"),
        _copy_patch("axes_0", "datatext", "axes_0.texts_0", "data text", [0.3, 0.6], "cccc3333"),
        _copy_patch("fig", "figtext", "fig.texts_0", "fig text", [0.5, 0.1], "dddd4444"),
    ]
    base = hot.override(STEM, [])
    resp = hot.override(STEM, patches)
    assert resp["warnings"] == []
    els = resp["manifest"]["elements"]
    gids = [e["gid"] for e in els]
    assert len(gids) == len(set(gids)), "元素表里有重复 gid"
    # 按序登记不数副本：脚本自己的文字序号一个不变，也没有多出来的 texts_N
    assert [g for g in gids if ".texts_" in g and not g.endswith(".arrow")] == [
        g
        for g in (e["gid"] for e in base["manifest"]["elements"])
        if ".texts_" in g and not g.endswith(".arrow")
    ]
    by = _els(resp)
    assert by["axes_0.copy_aaaa1111"]["role"] == "title"
    assert by["axes_0.copy_bbbb2222"]["role"] == "axis_label"
    assert by["axes_0.copy_cccc3333"]["role"] == "text"
    assert by["fig.copy_dddd4444"]["role"] == "text"
    assert by["axes_0.copy_aaaa1111"]["label"].endswith("的副本")
    assert by["axes_0.copy_aaaa1111"]["draggable"] is True
    # 没有「没登记」的残留：副本不算漏网之鱼
    assert not any(
        "Text" in row.get("cls", "") for row in resp["manifest"].get("unsupported") or []
    )
    hot.override(STEM, [])


def test_edits_on_the_copy_apply_and_a_removed_copy_orphans_them(rig):
    layout, hot, cold = rig
    cp = _copy_patch("axes_0", "datatext", "axes_0.texts_0", "data text", [0.3, 0.6])
    g = cp["gid"]
    edits = [
        {"gid": g, "prop": "fontsize", "value": 15},
        {"gid": g, "prop": "text", "value": "a much longer copied label"},
        {"gid": g, "prop": "color", "value": "#0000ff"},
    ]
    base_w = hot.override(STEM, [cp])["manifest"]
    resp = hot.override(STEM, [cp, *edits])
    assert resp["warnings"] == []
    fields = {f["prop"]: f["value"] for f in _els(resp)[g]["editable"]}
    assert fields["fontsize"] == 15
    assert fields["text"] == "a much longer copied label"
    assert fields["color"] == "#0000ff"
    assert _els(resp)[g]["bbox"][2] > _els({"manifest": base_w})[g]["bbox"][2], "改字没有撑宽副本"
    # 源没动
    assert (
        _els(resp)["axes_0.texts_0"]["bbox"] == _els({"manifest": base_w})["axes_0.texts_0"]["bbox"]
    )
    # 热 == 冷
    assert _boxes(resp) == _boxes(_cold(cold, [cp, *edits]))

    # 去掉 copy_of、保留修改：副本没了，修改报「元素不存在」，不崩
    orphan = hot.override(STEM, edits)
    assert g not in _els(orphan)
    assert any("元素不存在" in w and g in w for w in orphan["warnings"]), orphan["warnings"]
    # 副本回来：旧账不作数，修改重新落上
    again = hot.override(STEM, [cp, *edits])
    assert again["warnings"] == []
    assert {f["prop"]: f["value"] for f in _els(again)[g]["editable"]}["fontsize"] == 15
    assert _boxes(again) == _boxes(resp)
    hot.override(STEM, [])


def test_changing_the_copy_value_rebuilds_it_and_keeps_its_edits_equal_to_cold(rig):
    layout, hot, cold = rig
    cp = _copy_patch("axes_0", "datatext", "axes_0.texts_0", "data text", [0.3, 0.6])
    edit = {"gid": cp["gid"], "prop": "fontsize", "value": 13}
    hot.override(STEM, [cp, edit])
    moved = _copy_patch("axes_0", "datatext", "axes_0.texts_0", "moved", [0.6, 0.2])
    resp = hot.override(STEM, [moved, edit])
    assert resp["warnings"] == []
    assert _boxes(resp) == _boxes(_cold(cold, [moved, edit]))
    assert _els(resp) == _els(_cold(cold, [moved, edit]))
    hot.override(STEM, [])


def test_unsupported_and_missing_sources_warn_without_leaving_a_copy(rig):
    layout, hot, _c = rig
    cases = [
        # 刻度数字、图例文字、箭头、别的子图 / 宿主不匹配、副本再抄副本：第一期不支持
        _copy_patch("axes_0", "x", "axes_0.xticklabels_1", "0.0", [0.1, 0.1], "t1t1t1t1"),
        _copy_patch("axes_0", "x", "axes_0.legend.texts_0", "line", [0.1, 0.1], "t2t2t2t2"),
        _copy_patch("axes_0", "x", "axes_0.texts_1.arrow", "x", [0.1, 0.1], "t3t3t3t3"),
        _copy_patch("axes_0", "x", "axes_1.title", "Title", [0.1, 0.1], "t4t4t4t4"),
        _copy_patch("fig", "x", "axes_0.title", "Title", [0.1, 0.1], "t5t5t5t5"),
        _copy_patch("axes_0", "x", "fig.texts_0", "fig text", [0.1, 0.1], "t6t6t6t6"),
        # 形状不对的 gid（不是 8 位小写字母数字）
        {
            "gid": "axes_0.copy_BAD",
            "prop": "copy_of",
            "value": {"src": "axes_0.title", "text": "T", "at": [0.1, 0.1]},
        },
    ]
    for cp in cases:
        resp = hot.override(STEM, [cp])
        assert [w for w in resp["warnings"] if "属性不支持" in w], (cp["gid"], resp["warnings"])
        assert cp["gid"] not in _els(resp)
    # 源 gid 形状对、但脚本里没有这个元素
    miss = _copy_patch("axes_0", "x", "axes_0.texts_9", "x", [0.1, 0.1], "m1m1m1m1")
    resp = hot.override(STEM, [miss])
    assert any("元素不存在" in w and "axes_0.texts_9" in w for w in resp["warnings"]), resp[
        "warnings"
    ]
    # 宿主子图不存在
    nohost = _copy_patch("axes_7", "x", "axes_7.title", "x", [0.1, 0.1], "m2m2m2m2")
    resp = hot.override(STEM, [nohost])
    assert any("元素不存在" in w for w in resp["warnings"]), resp["warnings"]
    # 值形状不对：应用失败而不是崩
    bad = {"gid": "axes_0.copy_b1b1b1b1", "prop": "copy_of", "value": {"src": "axes_0.title"}}
    resp = hot.override(STEM, [bad])
    assert any("应用失败" in w for w in resp["warnings"]), resp["warnings"]
    # 以上都没留下半个副本；之后正常的副本照样建得出来
    final = hot.override(STEM, [_copy_patch("axes_0", "t", "axes_0.title", "Title", [0.5, 0.7])])
    assert final["warnings"] == [] and "axes_0.copy_ab12cd34" in _els(final)
    hot.override(STEM, [])


def test_copy_does_not_touch_data_fingerprints(rig):
    layout, hot, _c = rig
    base = hot.override(STEM, [])
    before = {g: e["data_fingerprint"] for g, e in _els(base).items() if "data_fingerprint" in e}
    assert before, "夹具里没有数据元素，比较会空转"
    cps = [
        _copy_patch(
            *SOURCES[k][:1], k, SOURCES[k][1], _text_of(k), SOURCES[k][2], f"f{i}f{i}f{i}f{i}"
        )
        for i, k in enumerate(SOURCES)
    ]
    resp = hot.override(STEM, cps)
    assert resp["warnings"] == []
    after = {g: e["data_fingerprint"] for g, e in _els(resp).items() if "data_fingerprint" in e}
    assert after == before
    hot.override(STEM, [])


# ---------------------------------------------------------------------------
# 不支持的宿主：3D 子图、色条轴
# ---------------------------------------------------------------------------
HOSTILE = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig = plt.figure(figsize=(6, 3))
    ax = fig.add_subplot(1, 2, 1)
    im = ax.imshow(np.arange(9).reshape(3, 3))
    ax.set_title("Flat")
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("level")
    ax3 = fig.add_subplot(1, 2, 2, projection="3d")
    ax3.plot([0, 1], [0, 1], [0, 1])
    ax3.set_title("Solid")
    ax3.set_xlabel("xx")
    fig.savefig("Hostile.pdf")
"""


def test_3d_and_colorbar_hosts_are_refused(tmp_path):
    (tmp_path / "hostile.py").write_text(HOSTILE, encoding="utf-8")
    w = pool.one_shot("hostile.py", str(tmp_path), "main")
    w.ensure_built()
    try:
        base = w.override("Hostile", [])
        gids = [e["gid"] for e in base["manifest"]["elements"]]
        cax = next(
            e["gid"] for e in base["manifest"]["elements"] if e["role"] == "colorbar"
        ).rsplit(".", 1)[0]
        td = next(e["gid"] for e in base["manifest"]["elements"] if e["role"] == "axes3d")
        assert f"{td}.xlabel" in gids and f"{cax}.ylabel" in gids, gids
        patches = [
            _copy_patch(td, "x", f"{td}.title", "Solid", [0.5, 0.9], "3d3d3d3d"),
            _copy_patch(td, "x", f"{td}.xlabel", "xx", [0.5, 0.1], "3d3d3d3e"),
            _copy_patch(cax, "x", f"{cax}.ylabel", "level", [2.0, 0.5], "cbcbcbcb"),
        ]
        resp = w.override("Hostile", patches)
        assert len([x for x in resp["warnings"] if "属性不支持" in x]) == 3, resp["warnings"]
        assert not any(".copy_" in e["gid"] for e in resp["manifest"]["elements"])
        # 同一张图里支持的宿主照常
        ok = w.override(
            "Hostile",
            [_copy_patch("axes_0", "x", "axes_0.title", "Flat", [0.5, 0.8], "okokokok")],
        )
        assert ok["warnings"] == [] and "axes_0.copy_okokokok" in {
            e["gid"] for e in ok["manifest"]["elements"]
        }
    finally:
        pool.discard(w)


# ---------------------------------------------------------------------------
# 写回验证门：带副本的真 prepare → 一次性 worker 全量重放 → 几何 + 像素
# ---------------------------------------------------------------------------
def test_write_back_with_copies_passes_the_replay_gate(write_back, replay_bases):  # noqa: F811
    import pymupdf

    m, client, figs = write_back
    resp = client.post("/api/engine/render", json={"id": "TestFig_a.pdf", "patches": []})
    assert resp.status_code == 200, resp.get_json()
    els = {e["gid"]: e for e in resp.get_json()["manifest"]["elements"]}
    assert "axes_0.title" in els and "axes_0.xlabel" in els
    patches = [
        # 几何先于副本（排序档）：挪子图，副本跟着 transAxes 走
        {"gid": "axes_0", "prop": "position", "value": [0.22, 0.20, 0.60, 0.62]},
        _copy_patch("axes_0", "t", "axes_0.title", "Original Title", [0.5, 0.9], "cp000001"),
        _copy_patch("axes_0", "x", "axes_0.xlabel", "x label", [0.5, -0.3], "cp000002"),
        {"gid": "axes_0.copy_cp000001", "prop": "text", "value": "Copied Heading"},
        {"gid": "axes_0.copy_cp000001", "prop": "fontsize", "value": 14},
        {"gid": "axes_0.copy_cp000002", "prop": "color", "value": "#aa0000"},
    ]
    r = client.post("/api/engine/render", json={"id": "TestFig_a.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["warnings"] == []
    done = client.post(
        "/api/engine/update_source",
        json={
            "id": "TestFig_a.pdf",
            "patches": patches,
            "expected_mtime": int((figs / "TestFig_a.pdf").stat().st_mtime),
        },
    )
    assert done.status_code == 200, done.get_json()
    body = done.get_json()
    assert body["verification"]["replay"] == "ok", body["verification"]
    assert body["verification"]["pixels"] == "ok", body["verification"]
    assert body["warnings"] == []
    with pymupdf.open(figs / "TestFig_a.pdf") as doc:
        text = doc[0].get_text()
    assert "Copied Heading" in text and "Original Title" in text
    # 这份列表原样在手：重新渲染不报 warning（副本是 patch，不是脚本里的东西）
    again = client.post("/api/engine/render", json={"id": "TestFig_a.pdf", "patches": patches})
    assert again.get_json()["warnings"] == []


# ---------------------------------------------------------------------------
# 引擎内部视角：排序档与「instrument 重跑时副本在世」（只有 worker 解释器里 import 得了 matplotlib）
# ---------------------------------------------------------------------------
_PROBE = """\
import json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import overrides, manifest, tickmodel
from matplotlib.figure import Figure

fig, ax = plt.subplots()
ax.set_title("T")
ax.text(0.5, 0.5, "x")
fig.text(0.1, 0.1, "f")
state = overrides.FigState(fig)
manifest.instrument(state)
rank = lambda prop, art, gid="": overrides._apply_rank(prop, art, gid)
ranks = {
    "frame": rank("frame", fig), "size": rank("size_mm", fig), "position": rank("position", ax),
    "scale": rank("xscale", ax),
    "copy": rank("copy_of", None, "axes_0.copy_ab12cd34"),
    "rest": rank("fontsize", ax.title),
    "tick_model": rank("major_step", tickmodel.TickSet(ax, "x"), "axes_0.xticks"),
    "tick_text": rank("text", tickmodel.TickLabel(ax, "x", 1), "axes_0.xticklabels_1"),
}
patches = [
    {"gid": "axes_0.copy_ab12cd34", "prop": "copy_of",
     "value": {"src": "axes_0.title", "text": "T", "at": [0.5, 0.5]}},
    {"gid": "fig.copy_ef56gh78", "prop": "copy_of",
     "value": {"src": "fig.texts_0", "text": "f", "at": [0.5, 0.5]}},
]
warnings = overrides.apply(state, patches)
before = sorted(state.index)
manifest.instrument(state)  # 副本在世时再登记一遍（native 屏障 rebase 的形状）
after = sorted(state.index)
copies_live = [t.get_text() for t in list(ax.texts) + list(fig.artists) if getattr(t, "_mm_copy", None)]
warnings2 = overrides.apply(state, patches)  # rebase 之后重放同一份列表：不许叠出第二份副本
copies_live2 = [t.get_text() for t in list(ax.texts) + list(fig.artists) if getattr(t, "_mm_copy", None)]
print(json.dumps({"ranks": ranks, "warnings": warnings, "before": before, "after": after,
                  "texts": [g for g in after if ".texts_" in g], "copies_live": copies_live, "warnings2": warnings2,
                  "copies_live2": copies_live2, "again": sorted(state.index)}))
"""


def _probe() -> dict:
    import json
    import subprocess
    from pathlib import Path

    from tavotto.engine import scriptadjust

    engine_dir = (
        Path(scriptadjust.__file__).resolve().parent
    )  # 引擎模块只在 worker 里按平铺模块 import
    out = subprocess.run(  # noqa: S603 — 测试里起 worker 解释器跑探针
        [WORKER_PY, "-c", _PROBE, str(engine_dir)],
        capture_output=True,
        encoding="utf-8",
        timeout=120,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probe():
    return _probe()


def test_copy_rank_sits_between_the_geometry_group_and_the_rest(probe):
    r = probe["ranks"]
    assert r["frame"] < r["size"] < r["position"] < r["scale"] < r["copy"] < r["rest"]
    assert r["rest"] < r["tick_model"] < r["tick_text"]


def test_instrument_rerun_with_live_copies_does_not_register_them_again(probe):
    """native 屏障 rebase 会在副本在世时重跑 `instrument`：按序登记跳过带 `_mm_copy` 的，副本不再以序号名出现。"""
    assert probe["warnings"] == []
    assert len(probe["copies_live"]) == 2
    # 脚本自己的文字序号一个不变（axes_0.texts_0 / fig.texts_0），没有 texts_1 / artists_N 之类多出来的名字
    assert probe["texts"] == ["axes_0.texts_0", "fig.texts_0"]
    assert not any(".artists_" in g for g in probe["after"])
    # 重跑 instrument 清空了登记表，副本此刻确实不在里面——它们的 gid 由 `_ensure_copy` 在下一轮 apply 重新登记
    assert (
        "axes_0.copy_ab12cd34" in probe["before"] and "axes_0.copy_ab12cd34" not in probe["after"]
    )
    # 重放同一份列表：副本回到登记表，图上仍各只有一段字（不叠第二份）
    assert probe["warnings2"] == [] and len(probe["copies_live2"]) == 2
    assert {"axes_0.copy_ab12cd34", "fig.copy_ef56gh78"} <= set(probe["again"])
