"""`cmap` 字段的两条只读事实：`cmap_current` / `cmap_original`。

脚本自己造的色图（`ListedColormap([...])`）在 matplotlib 里叫 `from_list`——
它不是注册表里的名字，`set_cmap("from_list")` 当场 ValueError。从前 manifest
把它原样当成一个 enum 取值发出去：界面上显示 `from_list`、渐变条是个问号、
选它写一条必然「应用失败」的 override（2026-09-13 用户反馈：图 A 的自定义
色块）。现在：

* `options` 只放**写得进 override 的名字**——注册过的留着（`Blues`），没注册
  的不放（`from_list`）；
* 名字不在 `CMAPS` 白名单里时发 `cmap_current`（自定义与否、采样出来的色标、
  离散与否），前端据此画真实渐变条、显示「自定义」；
* 换走之后发 `cmap_original`（override 之前那张的同一套事实），前端据此在
  列表里留一格「脚本原样」，选它 = 清掉 override。色条 ↔ 它的 mappable 是
  同一份色图状态的两个 gid，从哪一边改的、另一边都报得出原样。

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

SCRIPT_NAME = "fig_cmap.py"
ENTRY = "main"
STEM = "CmapFig"
IMAGE = "axes_0.images_0"  # 自定义三色 ListedColormap
CB = "axes_3.colorbar"  # 它的色条（色条轴是 axes_3，宿主 axes_0）
BLUES = "axes_1.images_0"  # 注册过、但不在白名单里的 Blues
MAGMA = "axes_2.images_0"  # 白名单里的 magma

COLORS = ["#256fa8", "#ebeef1", "#cf6a2c"]

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap


def main():
    fig, (ax, ax2, ax3) = plt.subplots(1, 3, figsize=(9.0, 3.0))
    im = ax.imshow(np.arange(9).reshape(3, 3) % 3,
                   cmap=ListedColormap(__COLORS__), vmin=0, vmax=2)
    fig.colorbar(im, ax=ax)
    ax2.imshow(np.arange(64).reshape(8, 8), cmap="Blues")
    ax3.imshow(np.arange(64).reshape(8, 8), cmap="magma")
    fig.savefig("CmapFig.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cmap-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY.replace("__COLORS__", repr(COLORS)), encoding="utf-8")
    return figs


def _render(figs, patches=()):
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    try:
        resp = w.override(STEM, list(patches))
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _cmap(man, gid):
    el = next(e for e in man["elements"] if e["gid"] == gid)
    return next(f for f in el["editable"] if f["prop"] == "cmap")


def test_custom_colormap_is_described_not_offered_as_a_writable_option(library):
    """`from_list` 是当前值、不是可选项；事实说它是自定义的、三格离散、色就是脚本
    给的那三个。"""
    f = _cmap(_render(library), IMAGE)
    assert f["value"] == "from_list"
    assert "from_list" not in f["options"], "写不进 override 的名字不许出现在选项表里"
    assert f["options"][0] == "viridis"
    cur = f["cmap_current"]
    assert cur["custom"] is True
    assert cur["discrete"] is True
    assert cur["stops"] == COLORS
    assert "cmap_original" not in f, "没有 override 时不发原样（缺席 = 与当前相同）"


def test_the_colorbar_of_a_custom_colormap_carries_the_same_facts(library):
    """色条与它的 mappable 共用一份色图：色条那边的 `cmap` 字段报同一套事实。"""
    man = _render(library)
    assert _cmap(man, CB)["cmap_current"] == _cmap(man, IMAGE)["cmap_current"]
    assert _cmap(man, CB)["value"] == "from_list"


def test_registered_but_unlisted_colormap_stays_selectable_and_gets_a_gradient(library):
    """`Blues` 注册过、写得进 override：留在选项表里（换走之后才回得来），
    但白名单外的前端离线表画不出它——事实里给九点采样、不算自定义。"""
    f = _cmap(_render(library), BLUES)
    assert f["value"] == "Blues"
    assert f["options"][0] == "Blues"
    cur = f["cmap_current"]
    assert cur["custom"] is False
    assert cur["discrete"] is False
    assert len(cur["stops"]) == 9
    assert cur["stops"][0].lower() == "#f7fbff" and cur["stops"][-1].lower() == "#08306b"


def test_whitelisted_colormap_sends_no_facts(library):
    """白名单里的名字前端有离线表，不发事实——发了就是第二份权威。"""
    f = _cmap(_render(library, [{"gid": IMAGE, "prop": "cmap", "value": "viridis"}]), IMAGE)
    assert f["value"] == "viridis"
    assert "cmap_current" not in f


def test_switching_away_reports_the_custom_original_on_both_gids(library):
    """从图像那边换成 viridis 之后：图像与它的色条**都**报 `cmap_original`
    ——名字、自定义、三格色标与换走之前的 `cmap_current` 逐字相同。"""
    before = _cmap(_render(library), IMAGE)["cmap_current"]
    man = _render(library, [{"gid": IMAGE, "prop": "cmap", "value": "viridis"}])
    for gid in (IMAGE, CB):
        f = _cmap(man, gid)
        assert f["value"] == "viridis", gid
        orig = f["cmap_original"]
        assert orig["name"] == "from_list"
        assert {k: orig[k] for k in ("custom", "stops", "discrete")} == before, gid
    # 反过来从色条那边改，图像也报得出原样
    man = _render(library, [{"gid": CB, "prop": "cmap", "value": "magma"}])
    assert _cmap(man, IMAGE)["cmap_original"]["name"] == "from_list"
    assert _cmap(man, CB)["cmap_original"]["name"] == "from_list"


def test_original_is_only_reported_when_it_is_off_the_whitelist(library):
    """脚本原样在白名单里（magma）的图元换走之后**不发** `cmap_original`：
    magma 本来就在选项表里，选它写一条普通 override 即可，事实只是噪音。
    原样注册过但不在白名单里（Blues）的照发——换走之后它就从选项表里消失了，
    没有这条事实就回不去。"""
    man = _render(
        library,
        [
            {"gid": MAGMA, "prop": "cmap", "value": "viridis"},
            {"gid": BLUES, "prop": "cmap", "value": "viridis"},
        ],
    )
    assert "cmap_original" not in _cmap(man, MAGMA)
    orig = _cmap(man, BLUES)["cmap_original"]
    assert orig["name"] == "Blues" and orig["custom"] is False and len(orig["stops"]) == 9
