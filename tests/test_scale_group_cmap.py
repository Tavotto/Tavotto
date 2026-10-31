"""色阶组整组换色（用户裁决 2026-10-18，ADR 0135 修订）。

凡与色条共用同一个 Norm 对象的 mappable 是同一个色阶（`colorbarmodel.scale_siblings`）。**从任一成员
（曲面 / 底投影 / 色条 / 2D 网格）改 cmap 都广播到整组**，不再是「兄弟的窄 override 压过色条广播」：
用户点曲面换配色，要的是整图一起变。vmin / vmax 本来就经共用 norm 联动，不在这里。

文档里存多条 cmap override 时（历史文档、MCP / 手写）**文档序里靠后的赢**——`_rank` 同档位、`sorted` 稳定，
热态逐步累加与冷启动重放同序；不报 warning、不阻断写回。

覆盖：3D（曲面 / 3D contourf / 独立 ScalarMappable 色条）、2D（PRB 三联图形态：两块 pcolormesh 共用
PowerNorm + 一条色条；没有色条的两块共用 Norm；contourf + colorbar(cf)）。每种形态：从每个成员改 → 整组变且
画面逐字相同；撤销回脚本原样（含没注册的自定义色图）；重做；冷启动重放一致；历史窄 override 文档重放。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

from __future__ import annotations

import hashlib

import pytest

from support.invariant_figures import ENTRY, LIBRARY, SCRIPT_NAME
from support.overridesample import _fields
from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCALE2D = "fig_scale2d.py"

#: 2D 三种形态。自定义色图**没注册**（`from_list` 的名字不在注册表里），撤销要回到它们的原样。
LIB2D = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, PowerNorm


def main():
    rng = np.random.RandomState(0)
    cm_a = LinearSegmentedColormap.from_list("paper_b", ["#edf5df", "#3f942c"])
    cm_b = LinearSegmentedColormap.from_list("paper_c", ["#f3e8f7", "#76008a"])
    x = np.linspace(0.5, 2.0, 7)
    y = np.linspace(1.3, 1.52, 7)

    # PRB 三联图形态：两块 pcolormesh 共用同一个 PowerNorm 对象，色条只挂在第二块旁边
    fig, (sa, sb) = plt.subplots(1, 2, figsize=(4.6, 2.4))
    shared = PowerNorm(gamma=1.45, vmin=0.0, vmax=1.0)
    ma = sa.pcolormesh(x, y, rng.rand(6, 6), cmap=cm_a, norm=shared, shading="flat")
    mb = sb.pcolormesh(x, y, rng.rand(6, 6), cmap=cm_b, norm=shared, shading="flat")
    fig.colorbar(mb, ax=sb)
    fig.savefig("Prb.pdf")

    # 没有色条、只是两块共用 Norm
    fig, (sa, sb) = plt.subplots(1, 2, figsize=(4.6, 2.4))
    shared = PowerNorm(gamma=1.2, vmin=0.0, vmax=1.0)
    sa.pcolormesh(x, y, rng.rand(6, 6), cmap=cm_a, norm=shared, shading="flat")
    sb.pcolormesh(x, y, rng.rand(6, 6), cmap=cm_b, norm=shared, shading="flat")
    fig.savefig("NoCb.pdf")

    # contourf + colorbar(cf)
    fig, ax = plt.subplots(figsize=(3.6, 2.8))
    gx, gy = np.meshgrid(np.linspace(0, 1, 20), np.linspace(0, 1, 20))
    cf = ax.contourf(gx, gy, np.sin(gx * 5) * np.cos(gy * 4), levels=8, cmap=cm_a)
    fig.colorbar(cf, ax=ax)
    fig.savefig("Cf.pdf")

    # 只有两条独立 ScalarMappable 色条共用一个 Normalize，图上没有任何图元用它（#1159 评审）
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable

    fig, ax = plt.subplots(figsize=(3.6, 2.8))
    ax.plot([0, 1], [0, 1])
    shared = Normalize(0.0, 1.0)
    fig.colorbar(ScalarMappable(norm=shared, cmap=cm_a), ax=ax)
    fig.colorbar(ScalarMappable(norm=shared, cmap=cm_b), ax=ax)
    fig.savefig("TwoSm.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("scale-group-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    (figs / SCALE2D).write_text(LIB2D, encoding="utf-8")
    return figs


def _worker(library, script):
    w = pool.one_shot(script, str(library), ENTRY)
    w.ensure_built()
    return w


@pytest.fixture(scope="module")
def hot3d(library):
    w = _worker(library, SCRIPT_NAME)
    yield w
    pool.discard(w)


@pytest.fixture(scope="module")
def hot2d(library):
    w = _worker(library, SCALE2D)
    yield w
    pool.discard(w)


def _man(worker, stem, patches=()):
    resp = worker.override(stem, list(patches))
    assert not (resp.get("warnings") or []), resp["warnings"]
    return resp["manifest"]


def _png(worker, stem, patches, tag):
    path = worker.preview_png(stem, list(patches), 380, tag)
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _fresh(library, script, stem, patches):
    w = _worker(library, script)
    try:
        return _man(w, stem, patches), _png(w, stem, patches, "fresh")
    finally:
        pool.discard(w)


def _group(man):
    """(成员 gid 们, 色条 gid 们)：有 cmap 字段的映射集合 / 位图，与色条。"""
    members, cbs = [], []
    for e in man["elements"]:
        props = {f["prop"] for f in e["editable"]}
        if "cmap" not in props:
            continue
        (cbs if e["role"] == "colorbar" else members).append(e["gid"])
    return members, cbs


#: (脚本, stem)。3D 的色条是独立 ScalarMappable（图上没有对应图元）
SHAPES = [
    pytest.param(SCRIPT_NAME, "Inv3D", id="3d-surface+contourf+standalone-cb"),
    pytest.param(SCALE2D, "Prb", id="2d-prb-two-meshes+cb"),
    pytest.param(SCALE2D, "NoCb", id="2d-two-meshes-no-cb"),
    pytest.param(SCALE2D, "Cf", id="2d-contourf+cb"),
    pytest.param(SCALE2D, "TwoSm", id="2d-two-standalone-cbs-no-artist"),
]


def _worker_of(request, script):
    return request.getfixturevalue("hot3d" if script == SCRIPT_NAME else "hot2d")


@pytest.mark.parametrize("script,stem", SHAPES)
def test_cmap_from_any_member_reaches_the_whole_group_and_undoes_exactly(
    request, library, script, stem
):
    w = _worker_of(request, script)
    base = _man(w, stem)
    base_png = _png(w, stem, [], "base")
    members, cbs = _group(base)
    origins = members + cbs
    assert len(origins) >= 2, origins
    pngs = set()
    for gid in origins:
        patch = [{"gid": gid, "prop": "cmap", "value": "plasma"}]
        man = _man(w, stem, patch)
        for g in origins:  # 从哪一个改，整组（含色条）都是 plasma
            assert _fields(man, g)["cmap"]["value"] == "plasma", f"从 {gid} 改，{g} 没跟着变"
        pngs.add(_png(w, stem, patch, f"from-{gid}"))
        # 冷启动重放一致
        fresh_man, fresh_png = _fresh(library, script, stem, patch)
        assert man == fresh_man, f"从 {gid} 改：热态 ≠ 冷启动"
        assert _png(w, stem, patch, "again") == fresh_png
        # 撤销：每个成员各回各的脚本原样（含没注册的自定义色图）；重做再整组变
        assert _man(w, stem) == base, f"从 {gid} 改再撤销没回原样"
        assert _png(w, stem, [], f"undo-{gid}") == base_png
        redo = _man(w, stem, patch)
        assert redo == man, "重做与第一次不同"
        _man(w, stem)
    assert len(pngs) == 1, "从不同成员改出的画面应逐字相同（同一个终态）"
    assert base_png not in pngs


@pytest.mark.parametrize("script,stem", SHAPES)
def test_historical_narrow_overrides_replay_last_in_document_wins(request, library, script, stem):
    """历史文档里组员各有一条不同的 cmap override：不报 warning、文档序里靠后的赢；
    热态逐步累加 / 调换顺序 / 撤掉其中一条，都与冷启动重放一致。"""
    w = _worker_of(request, script)
    base = _man(w, stem)
    members, cbs = _group(base)
    a, b = (members + cbs)[0], (members + cbs)[-1]
    assert a != b
    pa = {"gid": a, "prop": "cmap", "value": "cividis"}
    pb = {"gid": b, "prop": "cmap", "value": "plasma"}
    steps = [
        ("a→a,b", [[pa], [pa, pb]], "plasma"),
        ("b→a,b", [[pb], [pa, pb]], "plasma"),
        ("b→b,a", [[pb], [pb, pa]], "cividis"),
        ("a→b,a", [[pa], [pb, pa]], "cividis"),
        ("a,b→a", [[pa, pb], [pa]], "cividis"),
        ("a,b→b", [[pa, pb], [pb]], "plasma"),
        ("a,b→∅", [[pa, pb], []], None),
    ]
    for label, seq, winner in steps:
        for patch in seq[:-1]:
            _man(w, stem, patch)
        final = seq[-1]
        man = _man(w, stem, final)  # 不报 warning（_man 断言）
        if winner is None:
            assert man == base, label
        else:
            for g in members + cbs:
                assert _fields(man, g)["cmap"]["value"] == winner, (label, g)
        fresh_man, fresh_png = _fresh(library, script, stem, final)
        assert man == fresh_man, f"{label}：热态 ≠ 冷启动重放"
        assert _png(w, stem, final, f"hist-{label}") == fresh_png, label
        assert _man(w, stem) == base, f"{label}：全撤没回原样"


@pytest.mark.parametrize("script,stem", SHAPES)
def test_every_mapped_member_lists_its_peers_in_scale_gids(request, script, stem):
    """成员也发 `scale_gids`（组里**其它**成员）：前端据此在写 cmap 时清同组其它成员上的 override、
    「回到脚本原样」时一起清。没有同伴的（contourf + 色条）不发。"""
    man = _man(_worker_of(request, script), stem)
    members, _cbs = _group(man)
    for gid in members:
        el = next(e for e in man["elements"] if e["gid"] == gid)
        want = [g for g in members if g != gid]
        assert el.get("scale_gids", []) == want, (gid, el.get("scale_gids"))


def test_cmap_group_gids_is_the_structural_group(hot2d):
    """`cmap_group_gids`（`_cmap_alias_gids` 唯一出处）：独立色条之间互列；色条与它的 mappable 互列
    （#1159 评审）。前端写 cmap 时据此清同组其它成员的旧 override。"""
    man = _man(hot2d, "TwoSm")
    _members, cbs = _group(man)
    assert len(cbs) == 2, cbs
    for gid in cbs:
        el = next(e for e in man["elements"] if e["gid"] == gid)
        assert "mappable_gid" not in el and not el.get("scale_gids")  # 前提：独立色条、没有登记图元
        assert el.get("cmap_group_gids") == [g for g in cbs if g != gid], el
    cf = _man(hot2d, "Cf")
    members, cbs = _group(cf)
    assert len(members) == 1 and len(cbs) == 1
    by = {e["gid"]: e for e in cf["elements"]}
    assert by[members[0]].get("cmap_group_gids") == cbs
    assert by[cbs[0]].get("cmap_group_gids") == members


def test_cmap_group_keeps_a_member_whose_mapping_was_switched_off(hot2d):
    """一块网格的映射被 facecolor override 关掉（cmap 控件收起）后，它的旧 cmap override 仍按整组重放，
    所以另一块的 `cmap_group_gids` 里**仍有它**——只列此刻在映射的成员会让前端清不掉它（#1159 评审 P1）。"""
    base = _man(hot2d, "NoCb")
    members, _cbs = _group(base)
    assert len(members) == 2, members
    a, b = members
    # matplotlib 的规则（`color_mapping_is_live` 照抄）：面是 'none' 且边被显式设过 → 两个映射标志都关
    off = [
        {"gid": a, "prop": "facecolor", "value": "none"},
        {"gid": a, "prop": "edgecolor", "value": "#ff0000"},
    ]
    man = _man(hot2d, "NoCb", off)
    by = {e["gid"]: e for e in man["elements"]}
    assert "cmap" not in {f["prop"] for f in by[a]["editable"]}  # 前提：a 的映射确实被关掉了
    assert a not in (by[b].get("scale_gids") or [])  # 只看在映射的那份确实漏了它
    assert a in by[b].get("cmap_group_gids", []), by[b].get("cmap_group_gids")
    _man(hot2d, "NoCb")


def test_standalone_peer_change_offers_script_original(hot2d):
    """只有独立色条共用 norm：从色条 B 改 cmap 也改了 A，A 的 cmap 字段要给出 `cmap_original`（脚本原样是
    没注册的自定义色图，只剩「恢复到脚本」这个入口）（#1159 评审 P2）。"""
    base = _man(hot2d, "TwoSm")
    _members, cbs = _group(base)
    a, b = cbs
    man = _man(hot2d, "TwoSm", [{"gid": b, "prop": "cmap", "value": "plasma"}])
    field = _fields(man, a)["cmap"]
    assert field["value"] == "plasma"  # 前提：A 确实被 B 带着变了
    assert field.get("cmap_original", {}).get("custom") is True, field
    _man(hot2d, "TwoSm")
