"""`cmap` 字段的两条只读事实：`cmap_current` / `cmap_original`。

脚本自己造的色图（`ListedColormap([...])`）在 matplotlib 里只有一个默认名
（3.10 叫 `from_list`，3.11.2 起叫 `unnamed`）——它不在注册表里，
`set_cmap(name)` 当场 ValueError。从前 manifest 把它原样当成一个 enum 取值
发出去：界面上显示那个名字、渐变条是个问号、选它写一条必然「应用失败」的
override（2026-09-13 用户反馈：图 A 的自定义色块）。现在：

* `options` 只放**写得进 override 的名字**——注册过的留着（`Blues`），没注册
  的不放；
* 「自定义」的判据是**对象**不是名字（`_registered_colormap`）：名字不在注册表
  里、或名字在注册表里但取出来的不是这张（`ListedColormap([...], name="viridis")`
  / `get_cmap("viridis", 5)`）都算；用户 `register` 过的按注册表算。钉任何一个
  默认名字面量都只在一档 matplotlib 上对——这个文件里没有 `from_list` 断言。
* 自定义、或注册了但不在 `CMAPS` 白名单里时发 `cmap_current`（自定义与否、
  采样出来的色标、离散与否），前端据此画真实渐变条、显示「自定义」；
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
IMAGE = "axes_0.images_0"  # 自定义三色 ListedColormap，名字由 matplotlib 默认
CB = "axes_8.colorbar"  # 它的色条（色条轴排在八个子图之后，宿主 axes_0）
BLUES = "axes_1.images_0"  # 注册过、但不在白名单里的 Blues
MAGMA = "axes_2.images_0"  # 白名单里的 magma
NAMED_FROM_LIST = "axes_3.images_0"  # 同一张自定义色图，显式取名 from_list（3.10 的默认名）
NAMED_UNNAMED = "axes_4.images_0"  # 显式取名 unnamed（3.11.2 起的默认名）
WEARING_VIRIDIS = "axes_5.images_0"  # 自定义三色，却顶着白名单里的名字 viridis
RESAMPLED = "axes_6.images_0"  # get_cmap("viridis", 5)：名字是 viridis，查找表不是
REGISTERED_IMAGE = "axes_7.images_0"  # 脚本 register 过的自定义色图，按名使用
REGISTERED = "tavotto_test_palette"

COLORS = ["#256fa8", "#ebeef1", "#cf6a2c"]

LIBRARY = """\
import matplotlib
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

COLORS = __COLORS__
matplotlib.colormaps.register(ListedColormap(COLORS, name="__REGISTERED__"), force=True)


def main():
    fig, axs = plt.subplots(2, 4, figsize=(12.0, 6.0))
    axs = axs.ravel()
    ramp = np.arange(64).reshape(8, 8)
    im = axs[0].imshow(np.arange(9).reshape(3, 3) % 3,
                       cmap=ListedColormap(COLORS), vmin=0, vmax=2)
    axs[1].imshow(ramp, cmap="Blues")
    axs[2].imshow(ramp, cmap="magma")
    axs[3].imshow(ramp % 3, cmap=ListedColormap(COLORS, name="from_list"))
    axs[4].imshow(ramp % 3, cmap=ListedColormap(COLORS, name="unnamed"))
    axs[5].imshow(ramp % 3, cmap=ListedColormap(COLORS, name="viridis"))
    axs[6].imshow(ramp, cmap=plt.get_cmap("viridis", 5))
    axs[7].imshow(ramp % 3, cmap="__REGISTERED__")
    fig.colorbar(im, ax=axs[0])
    fig.savefig("CmapFig.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("cmap-figures")
    src = LIBRARY.replace("__COLORS__", repr(COLORS)).replace("__REGISTERED__", REGISTERED)
    (figs / SCRIPT_NAME).write_text(src, encoding="utf-8")
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


def _assert_custom_three_stops(f):
    """一张自定义三色色图的字段该长什么样：名字是当前值、不是可选项；事实说它
    是自定义的、三格离散、色就是脚本给的那三个。名字本身不断言——那是
    matplotlib 的词。"""
    cur = f["cmap_current"]
    assert cur["name"] == f["value"], "事实自己说清描述的是谁"
    assert f["value"] not in f["options"], "写不进 override 的名字不许出现在选项表里"
    assert cur["custom"] is True
    assert cur["discrete"] is True
    assert cur["stops"] == COLORS


def test_custom_colormap_is_described_not_offered_as_a_writable_option(library):
    """默认名的自定义色图：当前值就是 matplotlib 给的那个名字（哪一档都行），
    它不是可选项；事实说它是自定义的、三格离散、色就是脚本给的那三个。"""
    f = _cmap(_render(library), IMAGE)
    assert isinstance(f["value"], str) and f["value"]
    assert f["options"][0] == "viridis"
    _assert_custom_three_stops(f)
    assert "cmap_original" not in f, "没有 override 时不发原样（缺席 = 与当前相同）"


def test_custom_is_judged_by_the_object_not_by_the_default_name(library):
    """显式取名 `from_list`（3.10 的默认名）与 `unnamed`（3.11.2 的默认名）的两张
    自定义色图，与不取名的那张判得一样：三张都是自定义。判据钉任何一个字面量
    都会让其中一张在某一档 matplotlib 上漏判。"""
    man = _render(library)
    for gid, given in ((NAMED_FROM_LIST, "from_list"), (NAMED_UNNAMED, "unnamed")):
        f = _cmap(man, gid)
        assert f["value"] == given, gid
        _assert_custom_three_stops(f)
    assert _cmap(man, IMAGE)["cmap_current"]["custom"] is True


def test_the_colorbar_of_a_custom_colormap_carries_the_same_facts(library):
    """色条与它的 mappable 共用一份色图：色条那边的 `cmap` 字段报同一套事实。"""
    man = _render(library)
    assert _cmap(man, CB)["cmap_current"] == _cmap(man, IMAGE)["cmap_current"]
    assert _cmap(man, CB)["value"] == _cmap(man, IMAGE)["value"]


def test_a_custom_colormap_wearing_a_registered_name_is_still_custom(library):
    """`ListedColormap(COLORS, name="viridis")` 的名字在白名单里，对象却不是注册表
    里那张——写回 `cmap: "viridis"` 得到的是真 viridis，所以它是自定义的：事实照发
    （前端只认 `custom`，不拿名字判），`viridis` 仍留在选项表里（选它 = 换成真正
    的那张，合法）。换走之后 `cmap_original` 也照发——它回不去只能靠这条。
    `get_cmap("viridis", 5)` 同理：名字对、查找表不对。"""
    man = _render(library)
    f = _cmap(man, WEARING_VIRIDIS)
    assert f["value"] == "viridis"
    assert "viridis" in f["options"]
    cur = f["cmap_current"]
    assert cur["name"] == "viridis"
    assert cur["custom"] is True
    assert cur["discrete"] is True
    assert cur["stops"] == COLORS
    # viridis 本身是 256 格的 ListedColormap，重采成 5 格仍是 ListedColormap：五格离散
    r = _cmap(man, RESAMPLED)["cmap_current"]
    assert r["name"] == "viridis" and r["custom"] is True and r["discrete"] is True
    assert len(r["stops"]) == 5

    after = _cmap(
        _render(library, [{"gid": WEARING_VIRIDIS, "prop": "cmap", "value": "magma"}]),
        WEARING_VIRIDIS,
    )
    assert after["value"] == "magma"
    assert "cmap_current" not in after
    assert after["cmap_original"] == cur


def test_a_registered_custom_colormap_is_a_writable_value(library):
    """脚本 `matplotlib.colormaps.register(...)` 过的自定义色图按注册表算：不算
    自定义、留在选项表首位；把它写到别的图元上也真的写得进去（不报警告），
    写过去的那一头报它不算自定义、原样是那张默认名的自定义色图。"""
    f = _cmap(_render(library), REGISTERED_IMAGE)
    assert f["value"] == REGISTERED
    assert f["options"][0] == REGISTERED
    cur = f["cmap_current"]
    assert cur["name"] == REGISTERED
    assert cur["custom"] is False
    assert cur["discrete"] is True
    assert cur["stops"] == COLORS
    assert "cmap_original" not in f

    man = _render(library, [{"gid": IMAGE, "prop": "cmap", "value": REGISTERED}])
    for gid in (IMAGE, CB):
        g = _cmap(man, gid)
        assert g["value"] == REGISTERED, gid
        assert g["cmap_current"]["custom"] is False, gid
        assert g["cmap_original"]["custom"] is True, gid
        assert g["cmap_original"]["stops"] == COLORS, gid


def test_registered_but_unlisted_colormap_stays_selectable_and_gets_a_gradient(library):
    """`Blues` 注册过、写得进 override：留在选项表里（换走之后才回得来），
    但白名单外的前端离线表画不出它——事实里给九点采样、不算自定义。"""
    f = _cmap(_render(library), BLUES)
    assert f["value"] == "Blues"
    assert f["options"][0] == "Blues"
    cur = f["cmap_current"]
    assert cur["name"] == "Blues"
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
        assert f["cmap_original"] == before, gid
    # 反过来从色条那边改，图像也报得出原样
    man = _render(library, [{"gid": CB, "prop": "cmap", "value": "magma"}])
    assert _cmap(man, IMAGE)["cmap_original"] == before
    assert _cmap(man, CB)["cmap_original"] == before


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
