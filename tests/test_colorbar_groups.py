"""共享色条的结构归属：真实的组、`parent_gid`、`owner_gids`（manifest 的 `groups`）。

归属只认色条自己声明的宿主——matplotlib 在 `fig.colorbar(..., ax=...)` 那一刻记下的
ax 列表（`colorbarmodel.declared_parents`）。这里的每张图都是真脚本经 worker 跑出来的：

* 两个子图共享一条色条 → 一个组，成员 = 两个子图 + 色条轴；色条的颜色来源仍是它的
  mappable（`mappable_gid`），不是组；
* 单子图色条 → 色条轴挂在那个子图下，不成组；
* 互不相关的子图、`cax=` 建的色条（没有声明）、两条共享色条声明同一个子图（组互相
  重叠）→ 都不成组、保持原样，不按位置 / 颜色去猜；
* 单独挪 B：共享色条不跟着 B 走，重新布局后也不会被重新挂到 B 名下；
* 整组平移 / 缩放 = 每个成员各写一条 position，热会话与全量重放一致。

本进程不 import matplotlib：worker 经 `pool.one_shot()` 起在科学栈解释器里。
"""

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_cbar_groups.py"
ENTRY = "main"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt

Z = np.arange(64).reshape(8, 8)


def _shared(stem, **kw):
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(9.0, 3.0), **kw)
    a.plot([0, 1], [0, 1], label="A")
    a.legend()
    im = b.imshow(Z, cmap="viridis")
    c.imshow(Z.T, cmap="viridis", norm=im.norm)
    fig.colorbar(im, ax=[b, c])
    fig.savefig(stem + ".pdf")


def _single(stem):
    fig, ax = plt.subplots(figsize=(5.0, 4.0))
    im = ax.imshow(Z)
    fig.colorbar(im, ax=ax)
    fig.savefig(stem + ".pdf")


def _plain(stem):
    fig, (p, q) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    p.plot([0, 1])
    q.plot([1, 0])
    fig.savefig(stem + ".pdf")


def _cax(stem):
    # 用户自己摆的色条轴，视觉上横跨两图，但调用里没有声明宿主
    fig, (b, c) = plt.subplots(1, 2, figsize=(7.0, 3.0))
    im = b.imshow(Z)
    c.imshow(Z.T, norm=im.norm)
    fig.colorbar(im, cax=fig.add_axes([0.92, 0.15, 0.02, 0.7]))
    fig.savefig(stem + ".pdf")


def _overlap(stem):
    # 两条共享色条都声明了 b：两个组互相重叠，单父树表达不了
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(9.0, 3.0))
    im1 = a.imshow(Z)
    im2 = c.imshow(Z.T, cmap="magma")
    b.plot([0, 1])
    fig.colorbar(im1, ax=[a, b], location="bottom")
    fig.colorbar(im2, ax=[b, c], location="top")
    fig.savefig(stem + ".pdf")


def _dup(stem):
    # 同一个子图在 ax= 里写了两遍：matplotlib 照记两项，宿主只有一个
    fig, (ax, other) = plt.subplots(1, 2, figsize=(7.0, 3.0))
    im = ax.imshow(Z)
    other.plot([0, 1])
    fig.colorbar(im, ax=[ax, ax])
    fig.savefig(stem + ".pdf")


def _manual(stem, *, declared=True, standalone=False):
    # #792：GridSpec 色条轴夹在子图间；无关的 d 也共用 norm，但不属于布局组。
    from matplotlib.cm import ScalarMappable
    fig = plt.figure(figsize=(7.0, 6.0))
    grid = fig.add_gridspec(2, 3, width_ratios=[1, 1, .08])
    a = fig.add_subplot(grid[0, :2])
    ca = fig.add_subplot(grid[0, 2])
    b = fig.add_subplot(grid[1, 0])
    c = fig.add_subplot(grid[1, 1])
    bc = fig.add_subplot(grid[1, 2])
    d = fig.add_axes([.03, .9, .06, .06])
    im = a.imshow(Z, aspect="auto")
    mesh = b.pcolormesh(Z, cmap="magma")
    c.pcolormesh(Z.T, cmap="magma", norm=mesh.norm)
    d.imshow(Z, cmap="magma", norm=mesh.norm)
    scalar = ScalarMappable(norm=im.norm, cmap=im.cmap) if standalone else im
    fig.colorbar(scalar, cax=ca, **({"ax": a} if declared else {}))
    fig.colorbar(mesh, cax=bc, **({"ax": np.array([[b, c]])} if declared else {}))
    fig.savefig(stem + ".pdf")


def _cross_subfigure(standalone=False):
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    fig = plt.figure(figsize=(7, 3))
    left, right = fig.subfigures(1, 2)
    ax = right.subplots()
    if standalone:
        ax.plot([0, 1])
        im = ScalarMappable(norm=Normalize(0, 1), cmap="viridis")
    else:
        im = ax.imshow(Z)
    cax = left.add_axes([.8, .1, .05, .8])
    left.colorbar(im, cax=cax, ax=ax)
    fig.savefig(("CrossScalar" if standalone else "CrossSubFigure") + ".pdf")


def _shared_subfigure(stem, *, manual=False, mixed=False):
    fig = plt.figure(figsize=(8, 3))
    left, right = fig.subfigures(1, 2)
    a, b = right.subplots(1, 2)
    im = a.imshow(Z)
    b.imshow(Z.T, norm=im.norm)
    kw = {"cax": right.add_axes([.9, .1, .03, .8])} if manual else {}
    right.colorbar(im, ax=[a, b], **kw)
    if mixed:
        a, b = [fig.add_axes([x, .1, .12, .6]) for x in (.05, .25)]
        im = a.imshow(Z)
        b.imshow(Z.T, norm=im.norm)
        fig.colorbar(im, cax=fig.add_axes([.4, .1, .02, .6]), ax=[a, b])
    fig.savefig(stem + ".pdf")


def _scalar_layout_host(kind):
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize
    fig, (a, b) = plt.subplots(1, 2)
    norm = Normalize(0, 63)
    if kind == "shared":
        a.imshow(Z, norm=norm, cmap="viridis")
    elif kind == "equal":
        a.imshow(Z, norm=Normalize(0, 63), cmap="viridis")
    elif kind == "raster":
        a.imshow(plt.get_cmap("viridis")(norm(Z)))
    else:
        a.plot([0, 1])
    b.plot([0, 1])
    cax = fig.add_axes([.92, .2, .025, .6])
    fig.colorbar(ScalarMappable(norm=norm, cmap="viridis"), cax=cax, ax=b)
    fig.savefig("ScalarHost_" + kind + ".pdf")


def _different_color_host(*, manual=False):
    fig, (a, b) = plt.subplots(1, 2)
    im = a.imshow(Z)
    b.plot([0, 1])
    kw = {"cax": fig.add_axes([.92, .2, .025, .6])} if manual else {}
    fig.colorbar(im, ax=b, **kw)
    fig.savefig("DifferentManualHost.pdf" if manual else "DifferentHost.pdf")
    if manual:
        return
    other, ax = plt.subplots()
    cax = other.add_axes([.9, .1, .03, .8])
    other.colorbar(im, cax=cax, ax=ax)
    other.savefig("ForeignMappable.pdf")


def main():
    _dup("DupHost")
    _shared("Shared")
    _shared("SharedCL", layout="constrained")
    _single("Single")
    _plain("Plain")
    _cax("CaxShared")
    _overlap("Overlap")
    _manual("ManualCax")
    _manual("ManualScalar", standalone=True)
    _manual("ManualUnknown", declared=False)
    _cross_subfigure()
    _cross_subfigure(standalone=True)
    _different_color_host()
    _different_color_host(manual=True)
    _shared_subfigure("SharedSubFigure")
    _shared_subfigure("ManualSubFigure", manual=True)
    _shared_subfigure("MixedSubFigure", manual=True, mixed=True)
    for kind in ("empty", "shared", "equal", "raster"):
        _scalar_layout_host(kind)
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cbar-group-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    return figs


def _render(figs, stem, patches=()):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    try:
        w.ensure_built()
        resp = w.override(stem, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    return next(e for e in man["elements"] if e["gid"] == gid)


def _by_role(man, role):
    return [e for e in man["elements"] if e["role"] == role]


def _position(man, gid):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == "position")


def _field(man, gid, prop):
    return next(f["value"] for f in _el(man, gid)["editable"] if f["prop"] == prop)


@pytest.mark.parametrize("stem", ["Shared", "SharedCL"])
def test_explicitly_shared_colorbar_becomes_a_group(library, stem):
    man = _render(library, stem)
    (cb,) = _by_role(man, "colorbar")
    cax = cb["geom_gid"]
    assert cb["owner_gids"] == ["axes_1", "axes_2"]
    (group,) = man["groups"]
    assert group["gid"] == f"group:{cax}"
    assert group["kind"] == "shared_colorbar"
    assert group["members"] == ["axes_1", "axes_2", cax]
    assert group["subplot_gids"] == ["axes_1", "axes_2"]
    assert group["colorbar_gid"] == cb["gid"]
    assert group["resizable"] is True
    for g in group["members"]:
        assert _el(man, g)["parent_gid"] == group["gid"]
    # 不相关的子图 A 不进组，也没有显式父级
    assert "parent_gid" not in _el(man, "axes_0")
    # 颜色来源仍是色条的 mappable，组只转述它，不代替它
    assert cb["mappable_gid"] == "axes_1.images_0"
    assert group["mappable_gid"] == cb["mappable_gid"]
    # 组框 = 成员框的并集
    boxes = [_el(man, g)["bbox"] for g in group["members"]]
    x0 = min(b[0] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    assert group["bbox"][0] == pytest.approx(x0)
    assert group["bbox"][0] + group["bbox"][2] == pytest.approx(x1)


def test_shared_colorbar_does_not_follow_its_first_host(library):
    """横跨 B、C 的色条不挂在 B 的随行表里：单独拖 B 不会把整条色条拖走。"""
    man = _render(library, "Shared")
    cax = _by_role(man, "colorbar")[0]["geom_gid"]
    for g in ("axes_1", "axes_2"):
        assert cax not in _el(man, g).get("follow_gids", [])


def test_single_host_colorbar_nests_under_its_subplot(library):
    man = _render(library, "Single")
    (cb,) = _by_role(man, "colorbar")
    cax = cb["geom_gid"]
    assert "groups" not in man
    assert cb["owner_gids"] == ["axes_0"]
    assert _el(man, cax)["parent_gid"] == "axes_0"
    # 单宿主色条照旧随子图走
    assert cax in _el(man, "axes_0")["follow_gids"]


def test_repeated_declared_host_is_one_host_everywhere(library):
    """`ax=[ax, ax]`：宿主按去重后算 1 个，树里的挂法与拖动的随行关系、方向能力一致——
    色条轴挂在 ax 下、在 ax 的随行表里（拖 ax 色条跟着走），不成组，方向照常可改。"""
    man = _render(library, "DupHost")
    (cb,) = _by_role(man, "colorbar")
    cax = cb["geom_gid"]
    assert cb["owner_gids"] == ["axes_0"]
    assert _el(man, cax)["parent_gid"] == "axes_0"
    assert cax in (_el(man, "axes_0").get("follow_gids") or [])
    assert not man.get("groups")
    assert any(f["prop"] == "orientation" for f in _el(man, cb["gid"])["editable"])
    assert not _el(man, cb["gid"]).get("unsupported_props")


def test_unrelated_subplots_are_not_grouped(library):
    man = _render(library, "Plain")
    assert "groups" not in man
    assert all("parent_gid" not in e for e in man["elements"])


def test_cax_colorbar_without_declared_hosts_keeps_its_old_ownership(library):
    """`cax=` 没有宿主声明：即便它共用 norm、摆在两图旁边，也不成组、不改归属。"""
    man = _render(library, "CaxShared")
    (cb,) = _by_role(man, "colorbar")
    assert "groups" not in man
    assert "owner_gids" not in cb
    assert "parent_gid" not in _el(man, cb["geom_gid"])


@pytest.mark.parametrize("stem", ["ManualCax", "ManualScalar"])
def test_manual_cax_uses_explicit_hosts_not_color_scale_associations(library, stem):
    man = _render(library, stem)
    single, shared = _by_role(man, "colorbar")
    assert single["owner_gids"] == ["axes_0"]
    assert _el(man, "axes_1")["parent_gid"] == "axes_0"
    assert "axes_1" in _el(man, "axes_0")["follow_gids"]
    assert shared["owner_gids"] == ["axes_2", "axes_3"]
    (group,) = man["groups"]
    assert group["members"] == ["axes_2", "axes_3", "axes_4"]
    for gid in group["members"]:
        assert _el(man, gid)["parent_gid"] == group["gid"]
    for gid in group["subplot_gids"]:
        assert "axes_4" not in _el(man, gid).get("follow_gids", [])
    assert "parent_gid" not in _el(man, "axes_5")
    assert "axes_5.images_0" in shared["scale_gids"]  # 颜色关联存在，布局归属不扩大


def test_colorbar_axes_do_not_consume_subplot_display_numbers(library):
    man = _render(library, "ManualUnknown")
    assert [_el(man, g)["label"] for g in ("axes_0", "axes_2", "axes_3", "axes_5")] == [
        "子图 1",
        "子图 2",
        "子图 3",
        "子图 4",
    ]


def test_undeclared_manual_cax_reports_unknown_ownership_and_does_not_follow(library):
    man = _render(library, "ManualUnknown")
    assert "groups" not in man
    for cb in _by_role(man, "colorbar"):
        assert cb["owner_status"] == "undeclared"
        assert "owner_gids" not in cb
        assert "parent_gid" not in _el(man, cb["geom_gid"])
        for g in ("axes_0", "axes_2", "axes_3", "axes_5"):
            assert cb["geom_gid"] not in _el(man, g).get("follow_gids", [])


def test_cax_single_host_cannot_cross_subfigure_coordinate_spaces(library):
    man = _render(library, "CrossSubFigure")
    (cb,) = _by_role(man, "colorbar")
    assert "parent_gid" not in _el(man, cb["geom_gid"])
    assert "groups" not in man
    assert all(cb["geom_gid"] not in e.get("follow_gids", []) for e in _by_role(man, "axes"))


def test_cross_subfigure_scalar_declaration_does_not_change_orientation_placement(library):
    base = _render(library, "CrossScalar")
    (cb,) = _by_role(base, "colorbar")
    changed = _render(
        library, "CrossScalar", [{"gid": cb["gid"], "prop": "orientation", "value": "horizontal"}]
    )
    assert _position(changed, cb["geom_gid"]) == _position(base, cb["geom_gid"])


@pytest.mark.parametrize("stem", ["SharedSubFigure", "ManualSubFigure"])
def test_subfigure_group_keeps_ownership_but_does_not_offer_root_coordinate_transforms(
    library, stem
):
    man = _render(library, stem)
    (group,) = man["groups"]
    assert group["members"] == ["axes_0", "axes_1", "axes_2"]
    assert group["resizable"] is False
    assert all(_el(man, gid)["parent_gid"] == group["gid"] for gid in group["members"])


def test_mixed_root_and_subfigure_groups_check_each_own_coordinate_space(library):
    groups = _render(library, "MixedSubFigure")["groups"]
    assert len(groups) == 2
    assert sorted(g["resizable"] for g in groups) == [False, True]


def test_cax_orientation_uses_declared_layout_host_and_its_pending_position(library):
    patches = [
        {"gid": "axes_1", "prop": "position", "value": [0.5, 0.2, 0.3, 0.6]},
        {"gid": "cbar:axes_0:0", "prop": "orientation", "value": "horizontal"},
    ]
    hot = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        hot.override("DifferentManualHost", patches[:1])
        moved = hot.override("DifferentManualHost", patches)["manifest"]
        (cb,) = _by_role(moved, "colorbar")
        assert cb["colorbar_key"] == "cbar:axes_0:0"
        assert cb["owner_gids"] == ["axes_1"]
        x, _, width, _ = _position(moved, cb["geom_gid"])
        assert (x, width) == pytest.approx((0.5, 0.3), abs=1e-4)
        assert _position(
            _render(library, "DifferentManualHost", patches), cb["geom_gid"]
        ) == _position(moved, cb["geom_gid"])
    finally:
        pool.discard(hot)


@pytest.mark.parametrize("kind", ["empty", "shared", "equal", "raster"])
def test_scalar_layout_declaration_preserves_old_color_associations_and_aliases(library, kind):
    key = "cbar:?:0" if kind == "empty" else "cbar:axes_0:0"
    man = _render(library, "ScalarHost_" + kind, [{"gid": key, "prop": "label", "value": "Saved"}])
    (cb,) = _by_role(man, "colorbar")
    assert cb["colorbar_key"] == key
    assert _field(man, cb["gid"], "label") == "Saved"
    assert cb["owner_gids"] == ["axes_1"]
    assert _el(man, cb["geom_gid"])["parent_gid"] == "axes_1"
    assert cb["geom_gid"] in _el(man, "axes_1")["follow_gids"]


def test_layout_owners_do_not_change_saved_colorbar_semantic_aliases(library):
    man = _render(
        library, "DifferentHost", [{"gid": "cbar:axes_0:0", "prop": "label", "value": "Saved"}]
    )
    (cb,) = _by_role(man, "colorbar")
    assert cb["colorbar_key"] == "cbar:axes_0:0"
    assert _field(man, cb["gid"], "label") == "Saved"
    assert cb["owner_gids"] == ["axes_1"]
    assert cb["geom_gid"] in _el(man, "axes_1")["follow_gids"]
    assert cb["geom_gid"] not in _el(man, "axes_0").get("follow_gids", [])


def test_valid_cax_owner_follows_even_when_color_source_is_in_another_figure(library):
    man = _render(library, "ForeignMappable")
    (cb,) = _by_role(man, "colorbar")
    assert _el(man, cb["geom_gid"])["parent_gid"] == "axes_0"
    assert cb["geom_gid"] in _el(man, "axes_0")["follow_gids"]


@pytest.mark.parametrize("stem", ["ManualCax", "ManualScalar"])
def test_manual_group_move_scale_undo_redo_and_fresh_replay_are_pixel_exact(library, stem):
    from tavotto.app import _compare_manifests

    hot = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    fresh = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        base = hot.override(stem, [])["manifest"]
        pixels0 = hot.preview_png(stem, [], 480, "base").read_bytes()
        (group,) = base["groups"]
        members = group["members"]
        moved = []
        for gid in members:
            x, y, w, h = _position(base, gid)
            moved.append({"gid": gid, "prop": "position", "value": [x - 0.04, y + 0.02, w, h]})
        hot.override(stem, moved)
        # 挪动之后缩放，钉住左下角：复用前端写成员 position 的协议。
        x0 = min(p["value"][0] for p in moved)
        y0 = min(p["value"][1] for p in moved)
        scaled = []
        for p in moved:
            x, y, w, h = p["value"]
            scaled.append(
                dict(p, value=[x0 + (x - x0) * 0.8, y0 + (y - y0) * 0.8, w * 0.8, h * 0.8])
            )
        after = hot.override(stem, scaled)["manifest"]
        pixels1 = hot.preview_png(stem, scaled, 480, "scaled").read_bytes()
        assert pixels1 != pixels0  # 反证尺子量到真实变换
        for p in scaled:
            assert _position(after, p["gid"]) == pytest.approx(p["value"], abs=1e-4)
        for gid in ("axes_0", "axes_1", "axes_5"):
            assert _position(after, gid) == _position(base, gid)
        undone = hot.override(stem, [])["manifest"]
        assert _compare_manifests(base, undone)[0] == []
        assert hot.preview_png(stem, [], 480, "undo").read_bytes() == pixels0
        redone = hot.override(stem, scaled)["manifest"]
        replayed = fresh.override(stem, scaled)["manifest"]
        diffs, compared = _compare_manifests(redone, replayed)
        assert diffs == [] and compared > 0
        assert redone["groups"] == replayed["groups"]
        assert fresh.preview_png(stem, scaled, 480, "fresh").read_bytes() == pixels1
    finally:
        pool.discard(hot)
        pool.discard(fresh)


def test_old_manual_shared_orientation_with_explicit_position_replays_and_undoes(library):
    hot = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    fresh = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    patches = [
        {"gid": "axes_4", "prop": "position", "value": [0.12, 0.05, 0.6, 0.04]},
        {"gid": "axes_4.colorbar", "prop": "orientation", "value": "horizontal"},
    ]
    try:
        before = hot.preview_png("ManualCax", [], 480, "base").read_bytes()
        changed = hot.override("ManualCax", patches)
        assert not changed.get("warnings"), changed.get("warnings")
        man = changed["manifest"]
        (group,) = man["groups"]
        assert group["members"] == ["axes_2", "axes_3", "axes_4"]
        assert _position(man, "axes_4") == pytest.approx(patches[0]["value"], abs=1e-4)
        assert any(
            e["role"] == "ticklabel" and e["gid"].startswith("axes_4.xtick")
            for e in man["elements"]
        )
        assert not any(
            e["role"] == "ticklabel" and e["gid"].startswith("axes_4.ytick")
            for e in man["elements"]
        )
        pixels = hot.preview_png("ManualCax", patches, 480, "horizontal").read_bytes()
        assert pixels != before
        assert fresh.preview_png("ManualCax", patches, 480, "fresh").read_bytes() == pixels
        assert hot.preview_png("ManualCax", [], 480, "undo").read_bytes() == before
    finally:
        pool.discard(hot)
        pool.discard(fresh)


@pytest.mark.parametrize(
    ("stem", "cax", "positioned"), [("ManualCax", "axes_4", False), ("Shared", "axes_3", True)]
)
def test_shared_orientation_without_safe_manual_position_still_warns(
    library, stem, cax, positioned
):
    w = pool.one_shot(SCRIPT_NAME, str(library), ENTRY)
    try:
        man = w.override(stem, [])["manifest"]
        cb = _el(man, cax + ".colorbar")
        assert "orientation" not in {f["prop"] for f in cb["editable"]}
        patches = [{"gid": cb["gid"], "prop": "orientation", "value": "horizontal"}]
        if positioned:
            patches.append({"gid": cax, "prop": "position", "value": [0.12, 0.05, 0.6, 0.04]})
        resp = w.override(stem, patches)
        assert any("multi_host_colorbar" in str(warning) for warning in resp.get("warnings", []))
    finally:
        pool.discard(w)


def test_overlapping_shared_colorbars_stay_ungrouped(library):
    man = _render(library, "Overlap")
    cbs = _by_role(man, "colorbar")
    assert len(cbs) == 2
    assert "groups" not in man
    # 关系本身照报（owner_gids），只是不压成单父树
    assert sorted(tuple(cb["owner_gids"]) for cb in cbs) == [
        ("axes_0", "axes_1"),
        ("axes_1", "axes_2"),
    ]
    assert all("parent_gid" not in _el(man, cb["geom_gid"]) for cb in cbs)


#: 组外 / 没被拖的成员「不动」的容差（figure 分数，manifest 取 4 位小数）。constrained 图上
#: 拖共享色条的一个宿主，别的轴有 ≤ 7e-4（≈ 0.16 mm）的残差，连画三帧逐位相同、与 origin/main
#: （没有本 PR）逐位相同——是 ADR 0100 钉位下共享色条的摆放读宿主包围盒，不是组带来的；
#: 本 PR 要拦的缺陷（色条跟着 B 走）量级是拖动位移本身 5e-2
_STAY_TOL = {"Shared": 1e-6, "SharedCL": 1e-3}


@pytest.mark.parametrize("stem", ["Shared", "SharedCL"])
def test_moving_one_member_leaves_the_colorbar_in_place_and_in_the_group(library, stem):
    """constrained 图上同样：拖 B 被 ADR 0100 钉在写下的位置、C 与共享色条不跟也不被挤动。"""
    base = _render(library, stem)
    cax = base["groups"][0]["members"][-1]
    tol = _STAY_TOL[stem]
    b0 = _position(base, "axes_1")
    moved = [round(b0[0] - 0.05, 4), round(b0[1] + 0.05, 4), b0[2], b0[3]]
    man = _render(library, stem, [{"gid": "axes_1", "prop": "position", "value": moved}])
    assert _position(man, "axes_1") == pytest.approx(moved, abs=1e-4)
    assert _position(man, "axes_2") == pytest.approx(_position(base, "axes_2"), abs=tol)
    assert _position(man, cax) == pytest.approx(_position(base, cax), abs=tol)
    assert _position(man, "axes_0") == pytest.approx(_position(base, "axes_0"), abs=tol)
    # 重新布局之后仍是同一个组，色条没有被重新挂到 B 名下
    assert man["groups"] == [dict(base["groups"][0], bbox=man["groups"][0]["bbox"])]
    assert _el(man, cax)["parent_gid"] == base["groups"][0]["gid"]


@pytest.mark.parametrize("stem", ["Shared", "SharedCL"])
def test_group_move_keeps_relative_layout(library, stem):
    """整组平移 = 每个成员写同一个位移的 position；落下来的相对布局不变。constrained 图上
    每个成员（含色条轴）各自被 ADR 0100 钉住，组外的 A 不被挤动。"""
    base = _render(library, stem)
    members = base["groups"][0]["members"]
    patches = []
    for g in members:
        x, y, w, h = _position(base, g)
        patches.append(
            {"gid": g, "prop": "position", "value": [round(x - 0.1, 4), round(y + 0.02, 4), w, h]}
        )
    man = _render(library, stem, patches)
    for p in patches:
        assert _position(man, p["gid"]) == pytest.approx(p["value"], abs=1e-4)
    assert man["groups"][0]["members"] == members
    assert _position(man, "axes_0") == pytest.approx(_position(base, "axes_0"), abs=_STAY_TOL[stem])


def test_mappable_colormap_reaches_the_group_colorbar(library):
    """换 mappable 的色图 / 上下限：色条跟着 mappable 变（颜色来源不是组）。"""
    man = _render(
        library,
        "Shared",
        [
            {"gid": "axes_1.images_0", "prop": "cmap", "value": "magma"},
            {"gid": "axes_1.images_0", "prop": "vmax", "value": 40.0},
        ],
    )
    cb = _by_role(man, "colorbar")[0]
    cmap = _field(man, cb["gid"], "cmap")
    assert (cmap.get("name") if isinstance(cmap, dict) else cmap) == "magma"
    assert _field(man, cb["gid"], "vmax") == pytest.approx(40.0)
