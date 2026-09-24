"""图上看得见的东西，Tavotto 要认得对（2026-09-24，用户的两张 PRB 图）。

* Figure 1：两张 imshow 位图 `set_axis_off()` 拼成双联图——manifest 仍发两组刻度与
  边框命中区，x 刻度文字落在图外、右图的 y 刻度文字压在左图右缘上，点左图选中的是
  一个看不见的「Y 刻度文字」；
* Figure 2 (b)(c)：`pcolormesh` 铺满子图，点哪儿都选中网格，网格不可拖——子图拖不动；
  网格的 bbox 还按未裁剪的数据范围伸进了上一排的 (a)；
* Figure 2 (a)：离线渲染好的 COMSOL 场图（RGB）+ 独立 `ScalarMappable` 的色条——两者
  在 matplotlib 眼里毫无关系，从色条换色图，位图纹丝不动；那条色条也没有宿主，拖 (a)
  时不跟着走。

引擎内部的事实由 `tests/support/figure_recognition_probe.py` 在 worker 解释器里采，
这里只下判断；热会话 == 全量重放那一条走真 worker（`pool.one_shot`）。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "tests", "support", "figure_recognition_probe.py")


@pytest.fixture(scope="module")
def facts() -> dict:
    proc = subprocess.run(
        [WORKER_PY, PROBE], capture_output=True, text=True, encoding="utf-8", timeout=300
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _roles(summary: dict, gid_prefix: str) -> set[str]:
    return {e["role"] for g, e in summary.items() if g.startswith(gid_prefix + ".")}


def _inside(inner, outer, tol=1e-6) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    return (
        ix >= ox - tol and iy >= oy - tol and ix + iw <= ox + ow + tol and iy + ih <= oy + oh + tol
    )


# ============================================================ 看不见的元素
def test_axis_off_panels_publish_only_what_is_drawn(facts):
    """`set_axis_off()` 的子图：刻度组、单条刻度、轴标签一个都不发，边框命中区也不发——
    剩下的就是图上真有的：子图本身与那张位图。"""
    off = facts["axis_visibility"]["off"]
    for ax in ("axes_0", "axes_1"):
        assert _roles(off, ax) == {"image"}, sorted(g for g in off if g.startswith(ax))
        assert off[ax]["spines"] is None
    # 反证落点：所有元素都在图幅之内（改动前右图的 y 刻度文字 x 落在左图上、x 刻度文字 y > 1）
    for g, e in off.items():
        x, y, w, h = e["bbox"]
        assert x >= -1e-6 and y >= -1e-6 and x + w <= 1 + 1e-6 and y + h <= 1 + 1e-6, g


def test_axis_off_panels_do_not_offer_knobs_that_draw_nothing(facts):
    """刻度线四边、网格、边框、背景色在关掉坐标轴的子图上一个像素都不画——不给旋钮；
    落位、可见性、数据范围照给（位图的 extent 由它们决定）。"""
    props = set(facts["axis_visibility"]["off"]["axes_0"]["props"])
    assert {"position", "visible", "xlim", "ylim"} <= props
    assert not {p for p in props if p.startswith(("ticks_", "grid_", "spine_"))}
    assert "facecolor" not in props


def test_hidden_axis_drops_its_ticks_label_and_spine_sides(facts):
    """`xaxis.set_visible(False)`：x 那一侧的刻度、轴标签、上下两条边框命中区都不发，
    y 那一侧原样在。"""
    part = facts["axis_visibility"]["partial"]
    assert "axes_0.xticks" not in part and "axes_0.xlabel" not in part
    assert "axes_0.yticks" in part and "axes_0.ylabel" in part
    assert set(part["axes_0"]["spines"]) == {"left", "right"}
    props = set(part["axes_0"]["props"])
    assert "ticks_left" in props and "ticks_bottom" not in props and "grid_x" not in props


def test_frame_off_keeps_ticks_but_says_the_spines_are_not_drawn(facts):
    """`set_frame_on(False)`：边框与背景不画、刻度照画——四侧命中区在（刻度控得了），
    `visible` 如实报 False；边框与背景色的旋钮不给。"""
    part = facts["axis_visibility"]["partial"]
    spines = part["axes_1"]["spines"]
    assert set(spines) == {"bottom", "top", "left", "right"}
    assert not any(s["visible"] for s in spines.values())
    assert "axes_1.xticks" in part and "axes_1.yticks" in part
    props = set(part["axes_1"]["props"])
    assert not {p for p in props if p.startswith("spine_")} and "facecolor" not in props
    assert "ticks_bottom" in props


# ============================================================ 面状色图集合
@pytest.mark.parametrize("ax", ["axes_0", "axes_1"])
def test_area_fields_hand_geometry_to_their_host(facts, ax):
    """`pcolormesh` / `contourf` 铺满子图：命中它等于命中子图——几何代理给宿主，
    bbox 收在宿主框里（数据范围比 ylim 宽的网格从前伸出子图一截）。"""
    area = facts["area_fields"]
    el = area[f"{ax}.collections_0"]
    assert el["resizable"] is True and el["geom_gid"] == ax
    assert _inside(el["bbox"], area[ax]["bbox"]), (el["bbox"], area[ax]["bbox"])


def test_lines_and_series_are_not_area_fields(facts):
    """不填充的等值线与散点是线 / 数据系列：子图里有空白可点，不代理。"""
    area = facts["area_fields"]
    assert area["axes_2.collections_0"]["geom_gid"] is None
    assert area["axes_3.scatter_0"]["geom_gid"] is None


@pytest.mark.parametrize("gid", ["axes_6.collections_0", "axes_7.images_0"])
def test_proxy_is_not_claimed_when_the_host_cannot_move(facts, gid):
    """插图的落位归父级 locator：网格与位图都不宣称代理（与色条同一条判据）。
    位图从前无条件宣称，前端拿着它发一个落不下去的 position。"""
    el = facts["area_fields"][gid]
    assert el["resizable"] is None and el["geom_gid"] is None


# ============================================================ 色条 ↔ 位图
def test_standalone_colorbar_finds_the_raster_it_describes(facts):
    """独立 mappable 的色条：`mappable_gid` 指向那张已成色的位图，宿主是位图所在的子图
    （拖它时色条跟着走）。"""
    s = facts["raster_fields"]["summary"]
    assert s["axes_1.colorbar"]["mappable_gid"] == "axes_0.images_0"
    assert s["axes_1.colorbar"]["host_gid"] == "axes_0"
    assert s["axes_0"]["follow_gids"] == ["axes_1"]


def test_raster_follows_the_colorbar_and_keeps_its_overlays(facts):
    """换色图：场的像素变成新色图下同一个数值的颜色，流线原样；改上限同理；
    没动过与撤销之后都是脚本原件逐位不变。"""
    r = facts["raster_fields"]
    assert r["untouched_is_original"] is True
    field, line, edge = r["recolored"]
    assert field == pytest.approx(r["expected"][0], abs=2e-2)
    assert edge == pytest.approx(r["expected"][1], abs=2e-2)
    # 重着色写进的是 uint8 缓冲：原样保留的像素只差 8 位量化（≤ 1/255）
    assert line == pytest.approx(r["overlay"], abs=1 / 255 + 1e-6)
    # 整张场图逐像素：与「同一数值在新色图下的颜色」的误差（实测平均 0.002、p99.9 约 0.01）
    assert r["field_err_mean"] < 0.005, r["field_err_mean"]
    assert r["field_err_p999"] < 0.03, r["field_err_p999"]
    # 流线的抗锯齿边缘：底色的变化按覆盖率带过去，不留一圈旧色图的光晕
    assert r["antialiased"] == pytest.approx(r["antialiased_expected"], abs=2e-2)
    assert r["vmax_pixel"] == pytest.approx(r["vmax_expected"], abs=2e-2)
    assert r["undo_is_original"] is True


@pytest.mark.parametrize("case", ["noise", "boundary", "flat"])
def test_raster_binding_needs_a_real_field(facts, case):
    """随机噪声不是这条色图画的；BoundaryNorm 反解不出数值；整片贴在色图起点上的
    「白底照片」铺不开色图全长——三者都不配对。"""
    cb = facts["raster_fields"][case]["axes_1.colorbar"]
    assert cb["mappable_gid"] is None


# ============================================================ 数值相同的 norm
def test_standalone_colorbar_adopts_an_identical_scale(facts):
    """`ScalarMappable(Normalize(0, 1), "viridis")` 的色条旁边是 `imshow(vmin=0, vmax=1,
    cmap="viridis")`：两者是同一个色阶，色条换色图图像跟着换；上下限不同的、自己有
    色条的都不被认领。"""
    es = facts["equal_scales"]
    cb = es["summary"][es["colorbar"]]
    assert cb["scale_gids"] == ["axes_0.images_0"]
    assert cb["host_gid"] == "axes_0"
    assert es["image_cmap_after"] == "magma"
    assert es["control_cmap_after"] == "viridis"
    assert es["own_colorbar_image_cmap_after"] == "viridis"


def _bars(summary: dict) -> dict:
    return {g: e for g, e in summary.items() if e["role"] == "colorbar"}


def test_declared_host_limits_what_a_standalone_colorbar_adopts(facts):
    """#527 评审 P1：`ax=ax0` 的独立色条只认领 ax0 上的图，ax1 上恰好同色阶的无关图像
    不归它；两条各挂一边的色条各认各的，先处理的那条不再把两张都拿走。"""
    (one,) = _bars(facts["orphan_scopes"]["one_bar"]).values()
    assert one["scale_gids"] == ["axes_0.images_0"] and one["host_gid"] == "axes_0"
    two = sorted(
        (e["host_gid"], e["scale_gids"]) for e in _bars(facts["orphan_scopes"]["two_bars"]).values()
    )
    assert two == [("axes_0", ["axes_0.images_0"]), ("axes_1", ["axes_1.images_0"])]


def test_cax_colorbar_still_covers_every_panel_it_describes(facts):
    """对照：`cax=` 建的色条没有声明宿主，一条色条共用给一格图是常见写法——整组认领照旧。"""
    (bar,) = _bars(facts["orphan_scopes"]["shared_cax"]).values()
    assert sorted(bar["scale_gids"]) == ["axes_0.images_0", "axes_1.images_0"]


def test_declared_host_claims_before_the_generic_colorbar(facts):
    """`cax=` 的通用色条即使先建（排在前面），也只拿有宿主的色条挑剩下的：`ax=a1` 的那条
    认领 a1，通用那条只剩 a0。"""
    got = sorted(
        (e["host_gid"], e["scale_gids"]) for e in _bars(facts["orphan_scopes"]["mixed"]).values()
    )
    assert got == [("axes_0", ["axes_0.images_0"]), ("axes_1", ["axes_1.images_0"])]


def test_a_bound_raster_really_recolours_even_with_a_photo_inside(facts):
    """#527 评审 P2 的根：从前按唯一颜色暴力反解，嵌着照片的大图要么先报已绑定、换色图时
    默默失败，要么只能拒绝配对。颜色查表（`_ColourTube`）与图大小、唯一颜色数都无关——
    配对了就真的重着色：热图部分换成新色图，照片部分原样（随机颜色落进色图容差带的
    那一两成不计）。"""
    ph = facts["orphan_scopes"]["photo"]
    (bar,) = _bars(ph["summary"]).values()
    assert bar["mappable_gid"] == "axes_0.images_0"
    assert ph["heat_pixel"] == pytest.approx(ph["heat_expected"], abs=2e-2)
    assert ph["photo_unchanged"] > 0.97, ph["photo_unchanged"]


def test_large_rasters_bind_and_recolour(facts):
    """没有像素数上限：900 万像素（旧上限 600 万之上）的场图照样绑定，换色图后像素是新色图的颜色。"""
    big = facts["orphan_scopes"]["big"]
    (bar,) = _bars(big["summary"]).values()
    assert bar["mappable_gid"] == "axes_0.images_0"
    assert big["pixel"] == pytest.approx(big["expected"], abs=2e-2)


def test_recolour_memory_grows_only_by_the_output_itself(facts):
    """重着色的内存随像素数的增长斜率 ≈ 输出缓冲本身（RGB uint8 = 3 B/像素）加稀疏的边缘；
    旧实现是约 96 B/像素的临时量外加 float32 输出。常数项（32 MiB 直查表、分块临时量）在
    两档相减里抵消。"""
    bpp = facts["orphan_scopes"]["bytes_per_pixel"]
    assert bpp < 6, bpp


def test_extremely_wide_rasters_stay_within_a_tile(facts):
    """#538 评审第二轮：`(4, 3_000_000)` 这种极宽的图，按行分块时一块就是整张图。二维分块后，
    除了输出缓冲本身，额外的峰值只有块大小那一量级。"""
    extra = facts["orphan_scopes"]["wide_extra_bytes"]
    assert extra < 32 * 2**20, extra


def test_single_row_rasters_bind_and_really_recolour(facts):
    """#538 评审第三轮：单行色带每个像素最多两个邻居。连片判据按实际邻居数封顶、配对与
    重着色问同一个判据——绑定了就真的换得了色（改动前：报已绑定，换色图后 0 个像素变）。"""
    thin = facts["orphan_scopes"]["thin"]
    (bar,) = _bars(thin["summary"]).values()
    assert bar["mappable_gid"] == "axes_0.images_0"
    assert thin["changed"] > 0.9, thin["changed"]
    assert thin["end_pixel"] == pytest.approx(thin["end_expected"], abs=2e-2)
    # 反例：67% 在色图上却连不成片——重着色换不了一个像素，配对问同一个判据，就不报已绑定
    (speck,) = _bars(thin["speckled"]).values()
    assert speck["mappable_gid"] is None


def test_huge_colormaps_cost_nothing_without_a_raster(facts):
    """#538 评审第三轮：6 万格的自定义色图，改动前 instrument 一张没有位图的图也要建 N×729
    的候选（实测 4.3 s / 1.3 GB）。现在没有候选位图就不建；有位图时按 1024 格封顶建，照样
    绑定、照样重着色。"""
    hc = facts["orphan_scopes"]["huge_cmap"]
    assert hc["no_raster_peak"] < 16 * 2**20, hc["no_raster_peak"]
    assert hc["bind_peak"] < 64 * 2**20, hc["bind_peak"]
    (bar,) = _bars(hc["summary"]).values()
    assert bar["mappable_gid"] == "axes_0.images_0"
    assert hc["end_pixel"] == pytest.approx(hc["end_expected"], abs=2e-2)


def test_fit_sampling_spans_the_whole_image_within_budget(facts):
    """配对抽样的连续窗口：任何形状下总像素不超抽样上限，行、列都覆盖到两端的那一格。
    按扁平序号隔 k 格取时，k 恰是列数的倍数就全落在最左一列（3000² 实测色图只铺开 2%）。"""
    o = facts["orphan_scopes"]
    limit, win = o["sample_limit"], o["window"]
    for shape, f in o["windows"].items():
        assert f["pixels"] <= limit, shape
        assert f["first_row"] == 0 and f["first_col"] == 0, shape
        assert f["last_row"] > f["h"] - win and f["last_col"] > f["w"] - win, shape


def test_discrete_palettes_keep_every_colour(facts):
    """#538 评审第四轮：按固定格数重取样会跳过 `ListedColormap` 里真实存在的颜色（2048 色的
    调色板丢一半、配对失败）。查色表按去重后的真实颜色建：色带绑定、每个像素换成新色图下
    它那一格的颜色；多段建表与一次排序建表的结果相同。"""
    lst = facts["orphan_scopes"]["listed"]
    (bar,) = _bars(lst["summary"]).values()
    assert bar["mappable_gid"] == "axes_0.images_0"
    assert lst["max_err"] <= 1 / 255 + 1e-6, lst["max_err"]
    assert lst["tube_keys_equal"] is True
    assert lst["tube_entry_agree"] > 0.999, lst["tube_entry_agree"]
    (many,) = _bars(lst["too_many"]).values()
    assert many["mappable_gid"] is None
    # 去重时同色一组取平台中点（实测 0.007）；取第一次出现的位置偏低（0.013）
    assert lst["plateau_mean_err"] < 0.01, lst["plateau_mean_err"]


def test_colormap_sampling_is_bounded_whatever_its_length(facts):
    """#538 评审第五轮：一次按全长取样，名义 500 万格的色图峰值 375 MB，要拒绝的那种还得先
    全部算完。分段取样、边取边去重：平滑的照常得到它的真实颜色，峰值只跟段长有关；颜色
    太多的在第一段就停；格数超过 8 位颜色空间的只看 N 就不配对。"""
    h = facts["orphan_scopes"]["huge_n"]
    assert h["smooth"]["distinct"] is not None and h["smooth"]["peak"] < 32 * 2**20, h["smooth"]
    assert h["noisy"]["distinct"] is None and h["noisy"]["peak"] < 32 * 2**20, h["noisy"]
    assert h["over_n"]["distinct"] is None and h["over_n"]["peak"] < 2**20, h["over_n"]


# ============================================================ 热会话 == 全量重放
SCRIPT = "fig_recognition.py"
LIBRARY = """\
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib.cm import ScalarMappable

CMAP = mcolors.LinearSegmentedColormap.from_list(
    "field", ["#fffef5", "#fff27f", "#ffe426", "#e5bd00"], N=512
)


def main():
    yy, xx = np.mgrid[0:90, 0:120]
    v = np.clip(6.0 / (np.hypot(xx - 60, yy - 45) + 3.0), 0, 1)
    rgb = CMAP(mcolors.PowerNorm(0.35, 0, 1)(v))[..., :3]
    rgb[20, 10:100] = (0.2, 0.21, 0.18)
    fig = plt.figure(figsize=(5.0, 5.0))
    ax = fig.add_axes([0.08, 0.55, 0.7, 0.4])
    ax.imshow(rgb, interpolation="nearest")
    cax = fig.add_axes([0.82, 0.55, 0.03, 0.4])
    fig.colorbar(ScalarMappable(norm=mcolors.PowerNorm(0.35, 0, 1), cmap=CMAP), cax=cax)
    ax2 = fig.add_axes([0.1, 0.08, 0.6, 0.38])
    ax2.pcolormesh(np.linspace(0, 1, 11), np.linspace(0, 1, 9), np.random.RandomState(0).rand(9, 11),
                   shading="nearest")
    ax2.set_ylim(0.1, 0.9)
    fig.savefig("Recog.pdf")
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("figure-recognition")
    (figs / SCRIPT).write_text(LIBRARY, encoding="utf-8")
    return figs


def test_recolor_and_proxy_drag_replay_identically(library):
    """色条换色图 + 拖网格代理出来的子图：一次性全量重放与热会话逐步改出来的像素逐字节
    相同（写回事务的前提），且确实与原件不同（不是恒等成立）。"""
    patches = [
        {"gid": "axes_1.colorbar", "prop": "cmap", "value": "viridis"},
        {"gid": "axes_2", "prop": "position", "value": [0.15, 0.06, 0.55, 0.36]},
    ]
    hot = pool.one_shot(SCRIPT, str(library), "main")
    try:
        hot.ensure_built()
        man = hot.override("Recog", [])["manifest"]
        mesh = next(e for e in man["elements"] if e["gid"] == "axes_2.collections_0")
        assert mesh["geom_gid"] == "axes_2"
        base = hot.preview_png("Recog", [], 400, "base").read_bytes()
        hot.override("Recog", patches[:1])
        resp = hot.override("Recog", patches)
        assert not resp.get("warnings"), resp["warnings"]
        hot_png = hot.preview_png("Recog", patches, 400, "hot").read_bytes()
    finally:
        pool.discard(hot)
    fresh = pool.one_shot(SCRIPT, str(library), "main")
    try:
        fresh.ensure_built()
        cold_png = fresh.preview_png("Recog", patches, 400, "cold").read_bytes()
    finally:
        pool.discard(fresh)
    assert hot_png == cold_png
    assert hot_png != base
