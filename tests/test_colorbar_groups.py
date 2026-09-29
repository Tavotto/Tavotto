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


def main():
    _shared("Shared")
    _shared("SharedCL", layout="constrained")
    _single("Single")
    _plain("Plain")
    _cax("CaxShared")
    _overlap("Overlap")
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
