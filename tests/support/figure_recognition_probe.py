"""在 **worker 解释器**里跑的探针：画了什么、manifest 认出了什么（2026-09-24 用户两张 PRB 图）。

三族「图上看得见的东西在 Tavotto 里认不对」：

* **看不见的元素被当成看得见**：`set_axis_off()` / `xaxis.set_visible(False)` /
  `set_frame_on(False)` 之后刻度、轴标签、边框都不画了，它们自己的 visible 却仍是
  True——manifest 照发，于是图外与相邻面板上多出一整组看不见的命中框；
* **铺满子图的面状色图集合拖不动子图**：`pcolormesh` / `contourf` 盖住宿主，点哪儿都
  选中它，而它既不可拖也不可缩；
* **色条与它描述的图对不上**：独立 `ScalarMappable` 建的色条旁边是一张已成色的
  RGB 位图，或一块色图、上下限都相同却各拿一份 norm 的图像——从色条换色图，图不动。

退出码永远是 0（除非探针自己崩了），判定归 `tests/test_figure_recognition.py`：
这里只**如实报事实**。
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402

import colorbarmodel as C  # noqa: E402
import manifest as M  # noqa: E402
import overrides as O  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _state(fig):
    st = O.FigState(fig)
    M.instrument(st)
    return st


def _summary(st) -> dict:
    man = M.build_manifest(st, "probe")
    out = {}
    for e in man["elements"]:
        out[e["gid"]] = {
            "role": e["role"],
            "bbox": e["bbox"],
            "props": [f["prop"] for f in e["editable"]],
            "resizable": e.get("resizable"),
            "geom_gid": e.get("geom_gid"),
            "spines": e.get("spines"),
            "mappable_gid": e.get("mappable_gid"),
            "scale_gids": e.get("scale_gids"),
            "host_gid": e.get("host_gid"),
            "follow_gids": e.get("follow_gids"),
        }
    return out


# ---------------------------------------------------------------- 看不见的元素
def axis_visibility() -> dict:
    """左右两张 imshow 位图 `set_axis_off()`（用户 Figure 1 的形状）+ 两个对照。"""
    fig = plt.figure(figsize=(6.0, 3.0))
    rgb = np.dstack([np.linspace(0, 1, 40 * 60).reshape(40, 60)] * 3)
    ax_a = fig.add_axes([0.02, 0.05, 0.6, 0.9])
    ax_a.imshow(rgb)
    ax_a.set_axis_off()
    ax_b = fig.add_axes([0.66, 0.05, 0.3, 0.9])
    ax_b.imshow(rgb[:, :30])
    ax_b.set_axis_off()
    off = _summary(_state(fig))

    fig2, (ax_x, ax_f) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    ax_x.plot([0, 1], [0, 1])
    ax_x.set_xlabel("hidden x")
    ax_x.set_ylabel("shown y")
    ax_x.xaxis.set_visible(False)
    ax_f.plot([0, 1], [0, 1])
    ax_f.set_frame_on(False)
    partial = _summary(_state(fig2))
    return {"off": off, "partial": partial}


# ---------------------------------------------------------------- 面状色图集合
def area_fields() -> dict:
    fig, axs = plt.subplots(2, 3, figsize=(9.0, 5.0))
    (a_mesh, a_cf, a_c), (a_sc, a_host, a_img) = axs
    y = np.linspace(1.30, 1.52, 23)
    x = np.linspace(0.5, 2.0, 16)
    z = np.random.RandomState(0).rand(23, 16)
    # 用户 (b)(c) 的形状：频率网格比 ylim 宽、`shading="nearest"` 再各向外垫半格
    a_mesh.pcolormesh(x, y, z, shading="nearest")
    a_mesh.set_ylim(1.34, 1.51)
    a_cf.contourf(x, y, z)
    a_c.contour(x, y, z)
    a_sc.scatter(x, x, c=x)
    ins = a_host.inset_axes([0.5, 0.5, 0.45, 0.45])
    ins.pcolormesh(z)
    ins2 = a_img.inset_axes([0.5, 0.5, 0.45, 0.45])
    ins2.imshow(z)
    return _summary(_state(fig))


# ---------------------------------------------------------------- 色条 ↔ 位图
FIELD_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "probe_field", ["#fffef5", "#fff27f", "#ffe426", "#e5bd00"], N=512
)
OVERLAY = (0.20, 0.21, 0.18)


def _field_values(h=90, w=120):
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - w * 0.5, yy - h * 0.5) + 3.0
    return np.clip(6.0 / r, 0.0, 1.0)


def _raster_figure(norm, *, overlay=True, noise=False, flat=False):
    """离线渲染好的场图（RGB）+ 一条流线 + 独立 ScalarMappable 的色条（`cax=`）。"""
    values = _field_values()
    rgb = FIELD_CMAP(mcolors.PowerNorm(0.35, 0.0, 1.0)(values))[..., :3].astype(np.float32)
    if overlay:
        rgb[20, 10:100] = OVERLAY  # 一条不在色图上的「流线」
        # 它的抗锯齿边缘：一半流线色、一半底下的场
        rgb[21, 10:100] = 0.5 * np.asarray(OVERLAY, np.float32) + 0.5 * rgb[21, 10:100]
    if flat:
        # 「白底照片」：整片贴在色图的起点附近，on 比例很高，却铺不开色图全长
        rgb[:] = FIELD_CMAP(np.random.RandomState(3).rand(*values.shape) * 0.03)[..., :3]
    if noise:
        rgb = np.random.RandomState(1).rand(*rgb.shape).astype(np.float32)
    fig = plt.figure(figsize=(5.0, 3.0))
    ax = fig.add_axes([0.08, 0.1, 0.7, 0.8])
    ax.imshow(rgb, interpolation="nearest")
    cax = fig.add_axes([0.82, 0.1, 0.03, 0.8])
    fig.colorbar(ScalarMappable(norm=norm, cmap=FIELD_CMAP), cax=cax)
    return fig, values, rgb


def _pixels(im, points):
    """0–1 口径的 RGB。重着色的缓冲是 uint8（原件是什么类型就是什么类型）。"""
    arr = np.asarray(im.get_array())
    scale = 255.0 if arr.dtype.kind in "ui" else 1.0
    return [[round(float(c) / scale, 4) for c in arr[r, c][:3]] for r, c in points]


def raster_fields() -> dict:
    out: dict = {}
    fig, values, rgb = _raster_figure(mcolors.PowerNorm(0.35, 0.0, 1.0))
    st = _state(fig)
    out["summary"] = _summary(st)
    im = fig.axes[0].images[0]
    cb_gid = next(g for g, e in out["summary"].items() if e["role"] == "colorbar")
    field_pt, line_pt, edge_pt = (45, 60), (20, 50), (60, 20)
    aa_pt = (21, 50)
    fig.canvas.draw()
    out["untouched_is_original"] = bool(np.array_equal(np.asarray(im.get_array()), rgb))
    O.apply(st, [{"gid": cb_gid, "prop": "cmap", "value": "viridis"}])
    fig.canvas.draw()
    out["recolored"] = _pixels(im, [field_pt, line_pt, edge_pt])
    vir = matplotlib.colormaps["viridis"]
    pnorm = mcolors.PowerNorm(0.35, 0.0, 1.0)
    out["expected"] = [
        [round(float(c), 4) for c in vir(pnorm(values[r, c]))[:3]] for r, c in (field_pt, edge_pt)
    ]
    out["overlay"] = [round(float(c), 4) for c in OVERLAY]
    # 整张场图（去掉流线那两行）换色后与「同一数值在新色图下的颜色」的逐像素误差
    field_rows = np.ones(values.shape[0], bool)
    field_rows[[20, 21]] = False
    got_all = np.asarray(im.get_array())[field_rows, :, :3].astype(float)
    got_all /= 255.0 if np.asarray(im.get_array()).dtype.kind in "ui" else 1.0
    want_all = vir(pnorm(values[field_rows]))[..., :3]
    err = np.abs(got_all - want_all).max(-1)
    out["field_err_mean"] = float(err.mean())
    out["field_err_p999"] = float(np.quantile(err, 0.999))
    out["antialiased"] = _pixels(im, [aa_pt])[0]
    new_bg = np.asarray(vir(pnorm(values[aa_pt]))[:3])
    out["antialiased_expected"] = [
        round(float(c), 4) for c in 0.5 * np.asarray(OVERLAY) + 0.5 * new_bg
    ]
    O.apply(st, [{"gid": cb_gid, "prop": "vmax", "value": 0.5}])
    fig.canvas.draw()
    pnorm_half = mcolors.PowerNorm(0.35, 0.0, 0.5)
    out["vmax_pixel"] = _pixels(im, [field_pt])[0]
    out["vmax_expected"] = [
        round(float(c), 4) for c in FIELD_CMAP(pnorm_half(values[field_pt]))[:3]
    ]
    O.apply(st, [])
    fig.canvas.draw()
    out["undo_is_original"] = bool(np.array_equal(np.asarray(im.get_array()), rgb))

    # 反例：随机噪声不是场图；不可逆的 BoundaryNorm 反解不出数值
    fig_n, _, _ = _raster_figure(mcolors.PowerNorm(0.35, 0.0, 1.0), noise=True)
    out["noise"] = _summary(_state(fig_n))
    fig_b, _, _ = _raster_figure(mcolors.BoundaryNorm([0, 0.2, 0.5, 1.0], 512))
    out["boundary"] = _summary(_state(fig_b))
    fig_f, _, _ = _raster_figure(mcolors.PowerNorm(0.35, 0.0, 1.0), overlay=False, flat=True)
    out["flat"] = _summary(_state(fig_f))
    return out


# ---------------------------------------------------------------- 数值相同的 norm
def equal_scales() -> dict:
    z = np.random.RandomState(2).rand(10, 12)
    fig, (ax, ax2, ax3) = plt.subplots(1, 3, figsize=(9.0, 3.0))
    im = ax.imshow(z, cmap="viridis", vmin=0.0, vmax=1.0)
    fig.colorbar(ScalarMappable(norm=mcolors.Normalize(0.0, 1.0), cmap="viridis"), ax=ax)
    # 对照 1：上下限不同 → 不是同一个色阶
    ax2.imshow(z, cmap="viridis", vmin=0.0, vmax=2.0)
    # 对照 2：有自己色条的图像不被别人认领
    own = ax3.imshow(z, cmap="viridis", vmin=0.0, vmax=1.0)
    fig.colorbar(own, ax=ax3)
    st = _state(fig)
    summary = _summary(st)
    cb_gid = next(
        g for g, e in summary.items() if e["role"] == "colorbar" and not e["mappable_gid"]
    )
    fig.canvas.draw()
    before = np.asarray(im.to_rgba(im.get_array()))[0, 0][:3].tolist()
    O.apply(st, [{"gid": cb_gid, "prop": "cmap", "value": "magma"}])
    after_cmap = im.get_cmap().name
    other_cmap = ax2.images[0].get_cmap().name
    own_cmap = own.get_cmap().name
    return {
        "summary": summary,
        "colorbar": cb_gid,
        "image_cmap_after": after_cmap,
        "control_cmap_after": other_cmap,
        "own_colorbar_image_cmap_after": own_cmap,
        "pixel_before": before,
    }


def orphan_scopes() -> dict:
    """#527 评审：独立色条只在自己声明的宿主里认领；配对前量全图唯一颜色。"""
    z = np.random.RandomState(4).rand(8, 8)

    def _two(n_bars):
        fig, (a0, a1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
        a0.imshow(z, cmap="viridis", vmin=0, vmax=1)
        a1.imshow(z, cmap="viridis", vmin=0, vmax=1)
        for ax in (a0, a1)[:n_bars]:
            fig.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), ax=ax)
        return _summary(_state(fig))

    # 对照：`cax=` 的一条色条共用给一格图——没有声明宿主，整组认领照旧
    fig, axs = plt.subplots(1, 2, figsize=(6.0, 3.0))
    for ax in axs:
        ax.imshow(z, cmap="viridis", vmin=0, vmax=1)
    cax = fig.add_axes([0.92, 0.1, 0.02, 0.8])
    fig.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cax)
    shared = _summary(_state(fig))

    # 先建 `cax=` 的通用色条、再建 `ax=a1` 的：通用那条排在前面也不能把 a1 的图先拿走
    fig_m, (m0, m1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    for ax in (m0, m1):
        ax.imshow(z, cmap="viridis", vmin=0, vmax=1)
    cax_m = fig_m.add_axes([0.92, 0.1, 0.02, 0.8])
    fig_m.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cax_m)
    fig_m.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), ax=m1)
    mixed = _summary(_state(fig_m))

    # 一张 65% 色图热图 + 35% 照片的大图：抽样过得了吻合度，全图唯一颜色远超上限
    h, w = 900, 900
    yy, xx = np.mgrid[0:h, 0:w]
    v = (np.sin(xx / 37.0) * np.cos(yy / 53.0) + 1) / 2
    rgb = matplotlib.colormaps["viridis"](v)[..., :3].astype(np.float32)
    rgb[:, : int(w * 0.35)] = np.random.RandomState(5).rand(h, int(w * 0.35), 3)
    fig_p = plt.figure(figsize=(5.0, 4.0))
    fig_p.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(rgb)
    cax_p = fig_p.add_axes([0.8, 0.1, 0.03, 0.8])
    fig_p.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cax_p)
    photo_state = _state(fig_p)
    photo = {"summary": _summary(photo_state)}
    photo_im = fig_p.axes[0].images[0]
    bar_gid = next(g for g, e in photo["summary"].items() if e["role"] == "colorbar")
    O.apply(photo_state, [{"gid": bar_gid, "prop": "cmap", "value": "magma"}])
    fig_p.canvas.draw()
    after = np.asarray(photo_im.get_array())[..., :3]
    before = np.clip(np.rint(rgb * 255), 0, 255).astype(np.uint8)
    split = int(w * 0.35)
    photo["photo_unchanged"] = float((after[:, :split] == before[:, :split]).all(-1).mean())
    y, x = 450, 700
    photo["heat_pixel"] = [round(float(c) / 255, 4) for c in after[y, x]]
    photo["heat_expected"] = [
        round(float(c), 4) for c in matplotlib.colormaps["magma"](v[y, x])[:3]
    ]

    import tracemalloc  # noqa: PLC0415

    def _field_image(h, w, lines=True):
        """uint8 场图（viridis 渐变 + 每 64 行一条深色流线），绑一条 `cax=` 独立色条。"""
        row = np.rint(matplotlib.colormaps["viridis"](np.linspace(0, 1, w))[:, :3] * 255)
        img = np.empty((h, w, 3), np.uint8)
        img[:] = row.astype(np.uint8)[None]
        if lines:
            img[::64] = (40, 40, 40)
        f = plt.figure(figsize=(4.0, 3.0))
        f.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(img)
        cx = f.add_axes([0.8, 0.1, 0.03, 0.8])
        f.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cx)
        st = _state(f)
        return f, st, f.axes[0].images[0]

    def _recolor_peak(im):
        """第一次重着色（边缘反解 + 查表 + 输出缓冲）的内存峰值；减掉与图无关的直查表
        （每次重着色现建一张）。"""
        field = im._mm_field
        tracemalloc.start()
        field.cb.mappable.set_cmap("magma")
        field.sync()
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return peak - (1 << 24) * 2

    # 旧上限（600 万像素）之上的大图：照样配对、照样重着色
    big_fig, _big_state, big_im = _field_image(3000, 3000)
    big = {"summary": _summary(_big_state)}
    big_im._mm_field.cb.mappable.set_cmap("magma")
    big_fig.canvas.draw()
    big["pixel"] = [round(float(c) / 255, 4) for c in np.asarray(big_im.get_array())[1, 2999]]
    big["expected"] = [round(float(c), 4) for c in matplotlib.colormaps["magma"](1.0)[:3]]
    # 常驻 + 临时内存随像素数的增长斜率（两档之差：直查表、分块的临时量都是常数，相减抵消）
    _, _, small_im = _field_image(1024, 1024)
    _, _, large_im = _field_image(2048, 2048)
    slope = (_recolor_peak(large_im) - _recolor_peak(small_im)) / (2048**2 - 1024**2)
    # 极宽的图（#538 评审第二轮：按行分块时一行就是整张图）：峰值减掉输出缓冲本身
    _, _, wide_im = _field_image(4, 3_000_000, lines=False)
    wide_extra = _recolor_peak(wide_im) - 4 * 3_000_000 * 3
    # 单行色带（#538 评审第三轮：每个像素最多两个邻居，连片判据不封顶就一个都不算场）
    strip = matplotlib.colormaps["viridis"](np.linspace(0, 1, 2000))[None, :, :3]
    strip = strip.astype(np.float32)
    fs = plt.figure(figsize=(4.0, 3.0))
    fs.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(strip, aspect="auto")
    cxs = fs.add_axes([0.8, 0.1, 0.03, 0.8])
    fs.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cxs)
    st_s = _state(fs)
    strip_sum = _summary(st_s)
    strip_im = fs.axes[0].images[0]
    s_gid = next(g for g, e in strip_sum.items() if e["role"] == "colorbar")
    O.apply(st_s, [{"gid": s_gid, "prop": "cmap", "value": "magma"}])
    fs.canvas.draw()
    after_s = np.asarray(strip_im.get_array())[0, :, :3]
    thin = {
        "summary": strip_sum,
        "changed": float((after_s != np.rint(strip[0] * 255).astype(np.uint8)).any(-1).mean()),
        "end_pixel": [round(float(c) / 255, 4) for c in after_s[-1]],
        "end_expected": [round(float(c), 4) for c in matplotlib.colormaps["magma"](1.0)[:3]],
    }

    # 单行色带、每三个像素插一个叠加色：67% 在色图上，但没有一个场像素的两个邻居都在色图上——
    # 连不成片，重着色一个像素都换不了；配对若不问同一个判据就会报已绑定
    speck = strip.copy()
    speck[0, 2::3] = (0.2, 0.21, 0.18)
    fk = plt.figure(figsize=(4.0, 3.0))
    fk.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(speck, aspect="auto")
    cxk = fk.add_axes([0.8, 0.1, 0.03, 0.8])
    fk.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cxk)
    thin["speckled"] = _summary(_state(fk))

    # 几万格的自定义色图：没有位图时 instrument 不建查色表；有位图时按封顶的格数建
    huge = mcolors.LinearSegmentedColormap.from_list("huge", ["k", "r", "y", "w"], N=60000)
    fh = plt.figure(figsize=(4.0, 3.0))
    fh.add_axes([0.1, 0.1, 0.6, 0.8]).plot([0, 1], [0, 1])
    cxh = fh.add_axes([0.8, 0.1, 0.03, 0.8])
    fh.colorbar(ScalarMappable(mcolors.Normalize(0, 1), huge), cax=cxh)
    tracemalloc.start()
    _state(fh)
    no_raster_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    grad = huge(np.linspace(0, 1, 800))[None, :, :3].repeat(200, axis=0).astype(np.float32)
    fr = plt.figure(figsize=(4.0, 3.0))
    fr.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(grad, aspect="auto")
    cxr = fr.add_axes([0.8, 0.1, 0.03, 0.8])
    fr.colorbar(ScalarMappable(mcolors.Normalize(0, 1), huge), cax=cxr)
    tracemalloc.start()
    st_r = _state(fr)
    huge_bind_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    huge_sum = _summary(st_r)
    h_gid = next(g for g, e in huge_sum.items() if e["role"] == "colorbar")
    O.apply(st_r, [{"gid": h_gid, "prop": "cmap", "value": "viridis"}])
    fr.canvas.draw()
    huge_px = np.asarray(fr.axes[0].images[0].get_array())[100, 799, :3]
    huge_cmap = {
        "no_raster_peak": no_raster_peak,
        "bind_peak": huge_bind_peak,
        "summary": huge_sum,
        "end_pixel": [round(float(c) / 255, 4) for c in huge_px],
        "end_expected": [round(float(c), 4) for c in matplotlib.colormaps["viridis"](1.0)[:3]],
    }
    windows = {}
    for h, w in [
        (3000, 3000),
        (1, 2000),
        (4, 3_000_000),
        (1005, 1753),
        (100_000, 3),
        (64, 64),
        (100, 10_000),
        (10_000, 100),
    ]:
        ws = list(C._sample_windows(h, w))
        windows[f"{h}x{w}"] = {
            "pixels": sum((y1 - y0) * (x1 - x0) for y0, y1, x0, x1 in ws),
            "first_row": min(y0 for y0, _, _, _ in ws),
            "last_row": max(y1 for _, y1, _, _ in ws),
            "first_col": min(x0 for _, _, x0, _ in ws),
            "last_col": max(x1 for _, _, _, x1 in ws),
            "h": h,
            "w": w,
        }
    # 离散调色板（#538 评审第四轮）：2048 种随机颜色的 ListedColormap 画出的色带——每一种
    # 调色板颜色都要认得出；多段建表（> _FIELD_TUBE_CHUNK 格）与一次排序建表结果相同
    pal = mcolors.ListedColormap(np.random.RandomState(7).rand(2048, 3), name="pal2048")
    pramp = pal(np.linspace(0, 1, 2048))[None, :, :3].repeat(40, 0).astype(np.float32)
    fl = plt.figure(figsize=(4.0, 3.0))
    fl.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(pramp, aspect="auto")
    cxl = fl.add_axes([0.8, 0.1, 0.03, 0.8])
    fl.colorbar(ScalarMappable(mcolors.Normalize(0, 1), pal), cax=cxl)
    st_l = _state(fl)
    listed_sum = _summary(st_l)
    l_gid = next(g for g, e in listed_sum.items() if e["role"] == "colorbar")
    O.apply(st_l, [{"gid": l_gid, "prop": "cmap", "value": "viridis"}])
    fl.canvas.draw()
    got_l = np.asarray(fl.axes[0].images[0].get_array())[0, :, :3]
    want_l = np.rint(matplotlib.colormaps["viridis"](np.linspace(0, 1, 2048))[:, :3] * 255)
    chunked = C._ColourTube(pal)
    saved = C._FIELD_TUBE_CHUNK
    C._FIELD_TUBE_CHUNK = 10**9
    try:
        single = C._ColourTube(pal)
    finally:
        C._FIELD_TUBE_CHUNK = saved
    same_keys = np.array_equal(chunked.keys, single.keys)
    # 第九轮：两格颜色几乎相同、在调色板里隔得很远（分在不同段）——盖住同一个 8 位颜色，
    # 真实距离 0.501 对 0.499，后面那格更近
    near_cols = np.random.RandomState(3).rand(2048, 3)
    near_cols[5] = [100.499 / 255, 0.2, 0.2]
    near_cols[2000] = [100.501 / 255, 0.2, 0.2]
    near_tube = C._ColourTube(mcolors.ListedColormap(near_cols))
    near_key = (101 << 16) | (51 << 8) | 51
    near_entry = int(near_tube.entry[np.searchsorted(near_tube.keys, near_key)])
    listed = {
        "summary": listed_sum,
        "max_err": float(np.abs(got_l.astype(float) - want_l).max()) / 255,
        "tube_keys_equal": bool(same_keys),
        "tube_entry_mismatches": int((chunked.entry != single.entry).sum()) if same_keys else -1,
        "near_entry": near_entry,
    }
    # 连续色图前半段几乎不变色：好几格圆整成同一个
    # 8 位颜色，同色一组要取位置的平均值（平台中点），取第一次出现的位置会系统性偏低
    pale = mcolors.LinearSegmentedColormap.from_list(
        "pale", [(0, "#ffffff"), (0.5, "#fffaf6"), (1, "#202020")], N=4096
    )
    tp = np.linspace(0, 1, 3000)
    pramp2 = pale(tp)[None, :, :3].repeat(20, 0).astype(np.float32)
    fp2 = plt.figure(figsize=(4.0, 3.0))
    fp2.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(pramp2, aspect="auto")
    cxp = fp2.add_axes([0.8, 0.1, 0.03, 0.8])
    fp2.colorbar(ScalarMappable(mcolors.Normalize(0, 1), pale), cax=cxp)
    st_p2 = _state(fp2)
    p_gid = next(g for g, e in _summary(st_p2).items() if e["role"] == "colorbar")
    O.apply(st_p2, [{"gid": p_gid, "prop": "cmap", "value": "viridis"}])
    fp2.canvas.draw()
    got_p = np.asarray(fp2.axes[0].images[0].get_array())[0, :, :3].astype(float) / 255
    want_p = matplotlib.colormaps["viridis"](tp)[:, :3]
    listed["plateau_mean_err"] = float(np.abs(got_p - want_p).max(-1).mean())

    # 上万种不同颜色的调色板不是色阶：如实不配对
    many = mcolors.ListedColormap(np.random.RandomState(8).rand(C._FIELD_MAX_COLOURS + 500, 3))
    mramp = many(np.linspace(0, 1, 4000))[None, :, :3].repeat(40, 0).astype(np.float32)
    fm = plt.figure(figsize=(4.0, 3.0))
    fm.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(mramp, aspect="auto")
    cxm = fm.add_axes([0.8, 0.1, 0.03, 0.8])
    fm.colorbar(ScalarMappable(mcolors.Normalize(0, 1), many), cax=cxm)
    listed["too_many"] = _summary(_state(fm))

    # 名义格数极大的色图（#538 评审第五轮）：分段取样、边取边去重、超上限立刻停
    class _Procedural(mcolors.Colormap):
        """不建 LUT、颜色按 t 现算的色图（N 只是名义格数）。"""

        def __init__(self, n, noisy=False):
            super().__init__("procedural", N=n)
            self.noisy = noisy

        def __call__(self, X, alpha=None, bytes=False):
            tt = np.asarray(X, float)
            if self.noisy:
                rgb = np.stack([(tt * 7919.0) % 1, (tt * 104729.0) % 1, (tt * 1299709.0) % 1], -1)
            else:
                rgb = np.stack([tt, tt**2, 1.0 - tt], -1)
            return np.concatenate([rgb, np.ones(tt.shape + (1,))], -1)

    def _entries_cost(cm):
        tracemalloc.start()
        try:
            got = len(C._cmap_entries(cm)[0])
        except ValueError:
            got = None
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return {"distinct": got, "peak": peak}

    huge_n = {
        "smooth": _entries_cost(_Procedural(5_000_000)),
        "noisy": _entries_cost(_Procedural(5_000_000, noisy=True)),
        "over_n": _entries_cost(_Procedural(C._FIELD_MAX_N + 1)),
    }

    # 密集阴影线（#538 评审第六轮）：四行场、两行叠加——三分之一的像素是边缘
    def _hatch_image(side):
        row = np.rint(matplotlib.colormaps["viridis"](np.linspace(0, 1, side))[:, :3] * 255)
        img = np.empty((side, side, 3), np.uint8)
        img[:] = row.astype(np.uint8)[None]
        img[np.arange(side) % 6 >= 4] = (40, 40, 40)
        f = plt.figure(figsize=(4.0, 3.0))
        f.add_axes([0.1, 0.1, 0.6, 0.8]).imshow(img)
        cx = f.add_axes([0.8, 0.1, 0.03, 0.8])
        f.colorbar(ScalarMappable(mcolors.Normalize(0, 1), "viridis"), cax=cx)
        _state(f)
        return f.axes[0].images[0]

    def _hatch_cost(side):
        field = _hatch_image(side)._mm_field
        tracemalloc.start()
        field.cb.mappable.set_cmap("magma")
        field.sync()
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return cur, peak, field

    saved_cache = C._FIELD_EDGE_CACHE
    try:
        c1, p1, _ = _hatch_cost(1024)
        c2, p2, f2 = _hatch_cost(2048)
        C._FIELD_EDGE_CACHE = 2**20  # 压小上限：两档都超，缓存整份放弃
        d1, _, _ = _hatch_cost(1024)
        d2, _, g2 = _hatch_cost(2048)
        # 缓存与逐块重算两条路的输出逐字节相同（同一张 512² 的图各走一次）
        C._FIELD_EDGE_CACHE = saved_cache
        cached = _hatch_image(512)._mm_field
        cached.cb.mappable.set_cmap("magma")
        cached.sync()
        C._FIELD_EDGE_CACHE = 0
        fresh = _hatch_image(512)._mm_field
        fresh.cb.mappable.set_cmap("magma")
        fresh.sync()
        # 第二次换色走缓存 / 重算
        cached.cb.mappable.set_cmap("plasma")
        cached.sync()
        fresh.cb.mappable.set_cmap("plasma")
        fresh.sync()
    finally:
        C._FIELD_EDGE_CACHE = saved_cache
    dpx = 2048**2 - 1024**2
    hatch = {
        "peak_slope": (p2 - p1) / dpx,
        "cached_is_list": isinstance(f2._edges, list),
        "capped_dropped": g2._edges is False,
        "capped_retained_slope": (d2 - d1) / dpx,
        "paths_equal": bool(np.array_equal(np.asarray(cached._buf), np.asarray(fresh._buf))),
        "fresh_dropped": fresh._edges is False,
    }
    # 同一个独立 ScalarMappable 交给左右两个子图各建一条色条（#538 评审第七轮）
    zz = np.random.RandomState(9).rand(8, 8)
    fs2, (s0, s1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    s0.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    s1.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    shared_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    fs2.colorbar(shared_sm, ax=s0)
    fs2.colorbar(shared_sm, ax=s1)
    shared_two = _summary(_state(fs2))
    # 同一个 ScalarMappable：左边是色图图像（认领），右边是已成色的 RGB 位图（绑定）
    fs3, (u0, u1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    u0.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    vv = np.linspace(0, 1, 64)[None].repeat(32, 0)
    u1.imshow(matplotlib.colormaps["viridis"](vv)[..., :3])
    mixed_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    fs3.colorbar(mixed_sm, ax=u0)
    fs3.colorbar(mixed_sm, ax=u1)
    shared_mixed = _summary(_state(fs3))
    # 同一个 ScalarMappable：`ax=w0` 一条 + `cax=` 通用一条（第八轮）；对照：脚本自己把 norm
    # 交给了 c0 的图，通用色条不再按数值认领 c1
    fs4, (w0, w1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    w0.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    w1.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    gen_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    fs4.colorbar(gen_sm, ax=w0)
    fs4.colorbar(gen_sm, cax=fs4.add_axes([0.93, 0.1, 0.02, 0.8]))
    _state(fs4)
    fs5, (c0, c1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    ctl_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    c0.imshow(zz, cmap="viridis", norm=ctl_sm.norm)
    c1.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    fs5.colorbar(ctl_sm, cax=fs5.add_axes([0.93, 0.1, 0.02, 0.8]))
    _state(fs5)
    # 脚本把 norm 交给了 d0 的图，同一个 ScalarMappable 又用 `ax=d1` 建了一条色条：那条色条
    # 声明描述的是 d1，d0 的脚本共用者在它的作用域之外，不该挡住它认领 d1
    fs6, (d0, d1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    own_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    d0.imshow(zz, cmap="viridis", norm=own_sm.norm)
    d1.imshow(zz, cmap="viridis", vmin=0, vmax=1)
    fs6.colorbar(own_sm, ax=d1)
    _state(fs6)
    # 位图版：脚本把 norm 交给了 e0 的图，`ax=e1` 那条色条仍要把 e1 的 RGB 场图绑上
    fs7, (e0, e1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    ras_sm = ScalarMappable(mcolors.Normalize(0, 1), "viridis")
    e0.imshow(zz, cmap="viridis", norm=ras_sm.norm)
    e1.imshow(matplotlib.colormaps["viridis"](np.linspace(0, 1, 64)[None].repeat(32, 0))[..., :3])
    fs7.colorbar(ras_sm, ax=e1)
    _state(fs7)
    generic_after_scoped = {
        "scoped_raster_e1": getattr(e1.images[0], "_mm_field", None) is not None,
        "scoped_d1": d1.images[0].norm is own_sm.norm,
        "w0": w0.images[0].norm is gen_sm.norm,
        "w1": w1.images[0].norm is gen_sm.norm,
        "control_c1": c1.images[0].norm is ctl_sm.norm,
    }
    return {
        "generic_after_scoped": generic_after_scoped,
        "shared_two": shared_two,
        "shared_mixed": shared_mixed,
        "hatch": hatch,
        "huge_n": huge_n,
        "listed": listed,
        "windows": windows,
        "sample_limit": C._FIELD_SAMPLE,
        "window": C._FIELD_WINDOW,
        "thin": thin,
        "huge_cmap": huge_cmap,
        "big": big,
        "bytes_per_pixel": slope,
        "wide_extra_bytes": wide_extra,
        "one_bar": _two(1),
        "two_bars": _two(2),
        "shared_cax": shared,
        "mixed": mixed,
        "photo": photo,
    }


def main() -> None:
    report = {
        "axis_visibility": axis_visibility(),
        "area_fields": area_fields(),
        "raster_fields": raster_fields(),
        "equal_scales": equal_scales(),
        "orphan_scopes": orphan_scopes(),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
