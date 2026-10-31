"""manifest 元素上的 `alias_covers`：广播型字段「会盖掉谁」（`overrides.alias_covers`，唯一出处）。

前端写入口据此保证「一个量只留一个 override 地址」（`web/src/lib/aliasOverrides.ts`）：引擎里窄端永远
排在广播之后应用，两个地址同时有 override 时广播端那条被盖、控件却显示它的旧值。

形状：`{广播 prop: {窄 prop: [窄 gid, ...]}}`；排除同 gid 的项（`fill→facecolor` 不是同一个量）与 `cmap`
（色阶组由 `cmap_group_gids` 管）。本进程不 import matplotlib：worker 经 `pool.one_shot()` 起。
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

SCRIPT_NAME = "fig_alias_covers.py"
ENTRY = "main"
STEM = "Alias"

LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt


def main():
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    x = np.linspace(0.0, 6.0, 30)
    ax.plot(x, np.sin(x), label="sin")
    ax.plot(x, np.cos(x), label="cos")
    ax.legend(title="Series", title_fontsize=4.5, fontsize=7)
    ax.bar([0.5, 1.5, 2.5], [0.2, 0.3, 0.1])
    im = bx.imshow(np.arange(16.0).reshape(4, 4))
    fig.colorbar(im, ax=bx)
    ax.add_patch(plt.Rectangle((3, 0.5), 1, 0.5))
    from matplotlib.cm import ScalarMappable

    fig.colorbar(ScalarMappable(cmap="magma"), ax=ax)  # 独立 mappable：没有登记的元素
    fig.savefig("Alias.pdf")
"""


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    figs = tmp_path_factory.mktemp("alias-covers")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), ENTRY)
    w.ensure_built()
    try:
        resp = w.override(STEM, [])
        assert not (resp.get("warnings") or []), resp["warnings"]
        yield resp["manifest"]
    finally:
        pool.discard(w)


def _el(man, gid):
    hits = [e for e in man["elements"] if e["gid"] == gid]
    assert hits, f"{gid} 不在 manifest 里"
    return hits[0]


def test_legend_covers_title_and_entries(manifest):
    covers = _el(manifest, "axes_0.legend")["alias_covers"]
    # 标题字号 → 标题那个 Text 的 fontsize（1:1）；整体字号 → 每条图例项（1:多）
    assert covers["title_fontsize"] == {"fontsize": ["axes_0.legend.title"]}
    assert covers["fontsize"]["fontsize"] == ["axes_0.legend.texts_0", "axes_0.legend.texts_1"]


def test_colorbar_ticks_cover_the_colorbar_axes_tick_sets(manifest):
    cb = next(e for e in manifest["elements"] if e["role"] == "colorbar")
    covers = cb["alias_covers"]
    assert set(covers["tick_fontsize"]) == {"fontsize"}
    assert set(covers["tick_color"]) == {"color"}
    assert len(covers["tick_fontsize"]["fontsize"]) == 2  # x / y 两组刻度，所以不是 1:1
    # vmin / vmax → mappable（图像）：1:1
    assert len(covers["vmin"]["vmin"]) == 1
    assert covers["vmin"]["vmin"] == covers["vmax"]["vmax"]


def test_bar_series_covers_every_bar(manifest):
    series = next(e for e in manifest["elements"] if e["role"] == "bar_series")
    assert len(series["alias_covers"]["facecolor"]["facecolor"]) == 3


def test_cmap_and_same_element_aliases_are_not_listed(manifest):
    for e in manifest["elements"]:
        covers = e.get("alias_covers") or {}
        assert "cmap" not in covers, e["gid"]
        # fill→facecolor 是同一个元素上的别名，不是「同一个量」
        for narrow in covers.values():
            for gids in narrow.values():
                assert e["gid"] not in gids, e["gid"]
    patch = next(e for e in manifest["elements"] if e["role"] == "patch")
    assert "alias_covers" not in patch


def test_plain_elements_carry_no_field(manifest):
    line = _el(manifest, "axes_0.lines_0")
    assert "alias_covers" not in line


def test_only_registered_gids_are_listed(manifest):
    """独立 `ScalarMappable` 的合成 gid（`mappable#<对象 id>`，每个进程都不同）不是元素：发出去热态
    manifest 就 ≠ 冷启动重放（CI 的 test_hot_equals_replay_after_removal 抓到过）。
    （色条轴刻度组 `axes_k.xticks` 登记在元素表里、只是 manifest 不单列，写 override 的目标有效，所以不能拿
    manifest 元素表当参照。）"""
    seen = 0
    for e in manifest["elements"]:
        for narrow in (e.get("alias_covers") or {}).values():
            for covered in narrow.values():
                seen += len(covered)
                assert not any("#" in g for g in covered), (e["gid"], covered)
    assert seen


def test_alias_pure_lists_only_declared_pure_props(manifest):
    """`alias_pure`：可互换（纯别名）的广播 prop。依据见 `overrides.ALIAS_PURE`。"""
    legend = _el(manifest, "axes_0.legend")
    assert legend["alias_pure"] == ["title_fontsize"]
    assert "fontsize" not in legend["alias_pure"]  # 整体字号还改 Legend._fontsize 并重建布局
    cb = next(e for e in manifest["elements"] if e["role"] == "colorbar")
    assert cb["alias_pure"] == ["vmax", "vmin"]  # 刻度字号 / 颜色（tick_params 留默认值）不纯
    series = next(e for e in manifest["elements"] if e["role"] == "bar_series")
    assert "alias_pure" not in series
    for e in manifest["elements"]:
        assert set(e.get("alias_pure") or ()) <= set(e.get("alias_covers") or ()), e["gid"]
