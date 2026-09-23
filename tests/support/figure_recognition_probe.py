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
    arr = np.asarray(im.get_array())
    return [[round(float(c), 4) for c in arr[r, c][:3]] for r, c in points]


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


def main() -> None:
    report = {
        "axis_visibility": axis_visibility(),
        "area_fields": area_fields(),
        "raster_fields": raster_fields(),
        "equal_scales": equal_scales(),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
