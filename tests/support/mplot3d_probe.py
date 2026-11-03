"""在 **worker 解释器**里跑的 3D 数据图元几何探针（ADR 0135）。

本进程（pytest）不 import matplotlib，几何事实只能在 worker 侧量；这里**如实报事实**，判定归
`tests/test_mplot3d_data_artists.py`——探针自己下判决的话，「多近算准」的裁决就藏进了被测的一侧。

    python tests/support/mplot3d_probe.py            # 打印 JSON 报告

报告三块：

* ``ink``     每个 3D 数据图元**单独画**时的真实墨迹包围盒，对 manifest 的 ``bbox`` 与 ``geometry``：
              bbox 与墨迹的偏差（figure 分数）、轮廓顶点落在墨迹上的比例；
* ``budget``  160×160 的曲面（25 600 个面）：有没有退回 bbox、数据面片被展开了多少个就收了手
              （按调用次数数，不按时钟——时钟判据在慢机器上会假红）；
* ``sizes``   逐点大小不同的 3D 散点：每个标记轮廓的直径是不是它自己那一点的大小
              （`Path3DCollection` 每帧把大小重排成深度序，偏移不重排，对不上号就画到别人头上）。
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
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from mpl_toolkits.mplot3d import proj3d  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402
import pathgeom as PG  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_DATA_ROLES = ("line", "fill", "scatter", "collection", "linecoll")


def _build(fig):
    state = O.FigState(fig)
    M.instrument(state)
    fig.canvas.draw()
    return state, M.build_manifest(state, "probe")


def _ink_report(fig, ax, state, man) -> list[dict]:
    """逐个元素单独画：manifest 的 bbox / geometry 对真实墨迹。"""
    W, H = float(fig.bbox.width), float(fig.bbox.height)
    els = [e for e in man["elements"] if e["role"] in _DATA_ROLES]
    arts = {e["gid"]: state.index[e["gid"]] for e in els}
    leg = ax.get_legend()
    out = []
    for e in els:
        for g, a in arts.items():
            a.set_visible(g == e["gid"])
        if leg is not None:
            leg.set_visible(False)
        ax.set_axis_off()
        fig.canvas.draw()
        img = np.asarray(fig.canvas.buffer_rgba())[..., :3].astype(int)
        ink = np.abs(img - 255).sum(axis=2) > 6
        ys, xs = np.nonzero(ink)
        row: dict = {"gid": e["gid"], "role": e["role"]}
        if len(xs):
            row["ink"] = [xs.min() / W, ys.min() / H, (xs.max() + 1) / W, (ys.max() + 1) / H]
            bx, by, bw, bh = e["bbox"]
            row["bbox"] = [bx, by, bx + bw, by + bh]
            geom = e.get("geometry")
            if geom:
                pts = [p for path in geom["paths"] for p in path["points"]]
                hit = 0
                for px, py in pts:
                    ix, iy = int(px * W), int(py * H)
                    hit += bool(ink[max(iy - 2, 0) : iy + 3, max(ix - 2, 0) : ix + 3].any())
                row["geometry_cover"] = hit / len(pts)
            else:
                row["geometry_cover"] = None
        out.append(row)
        for a in arts.values():
            a.set_visible(True)
        ax.set_axis_on()
        if leg is not None:
            leg.set_visible(True)
        fig.canvas.draw()
    return out


def ink() -> list[dict]:
    rng = np.random.RandomState(0)
    g = np.linspace(-1.0, 1.0, 16)
    gx, gy = np.meshgrid(g, g)
    gz = np.exp(-(gx**2 + gy**2) * 2.0)
    fig = plt.figure(figsize=(4.4, 3.4))
    ax = fig.add_subplot(projection="3d")
    ax.plot_surface(gx, gy, gz, cmap="viridis", linewidth=0, antialiased=False)
    ax.contourf(gx, gy, gz, zdir="z", offset=-0.3, levels=8, cmap="viridis")
    ax.plot(np.cos(g * 3), np.sin(g * 3), g, color="#b34700")
    ax.scatter(rng.rand(20) - 2.0, rng.rand(20), rng.rand(20), s=60, marker="^", color="#2a6f3c")
    ax.plot_wireframe(gx + 2.5, gy, gz * 0.5, color="#555555", rstride=3, cstride=3)
    ax.bar3d([-2.5, -2.2], [-1.0, -0.7], [0.0, 0.0], 0.2, 0.2, [0.8, 1.2], color="#4477aa")
    state, man = _build(fig)
    rows = _ink_report(fig, ax, state, man)
    plt.close(fig)
    return rows


def budget() -> dict:
    n = 160
    g = np.linspace(-1.0, 1.0, n)
    gx, gy = np.meshgrid(g, g)
    gz = np.exp(-(gx**2 + gy**2) * 2.0)
    cm = LinearSegmentedColormap.from_list("coolwarm_preview", ["#2b4c9b", "#f2f2f2", "#b3282d"])
    fig = plt.figure(figsize=(4.4, 3.4))
    ax = fig.add_subplot(projection="3d")
    ax.plot_surface(gx, gy, gz, cmap=cm, norm=Normalize(0, 1), linewidth=0, antialiased=False)
    calls = {"n": 0}
    real = PG._display_subpaths  # noqa: SLF001

    def counting(path, transform):
        calls["n"] += 1
        return real(path, transform)

    PG._display_subpaths = counting  # noqa: SLF001
    try:
        state, man = _build(fig)
    finally:
        PG._display_subpaths = real  # noqa: SLF001
    el = next(e for e in man["elements"] if e["role"] == "fill")
    faces = (n - 1) ** 2
    plt.close(fig)
    return {
        "faces": faces,
        "display_subpaths_calls": calls["n"],
        "has_geometry": "geometry" in el,
        "bbox": el["bbox"],
    }


def sizes() -> dict:
    """逐点大小不同的 3D 散点：每个轮廓的直径 == 它所在那一点的大小。"""
    rng = np.random.RandomState(3)
    n = 14
    xs, ys, zs = rng.rand(n), rng.rand(n), rng.rand(n)
    s = np.linspace(20.0, 420.0, n)
    fig = plt.figure(figsize=(4.4, 3.4), dpi=100)
    ax = fig.add_subplot(projection="3d")
    sc = ax.scatter(xs, ys, zs, s=s, marker="o", color="#2a6f3c")
    state, man = _build(fig)
    el = next(e for e in man["elements"] if e["role"] == "scatter")
    W, H = float(fig.bbox.width), float(fig.bbox.height)
    # 每个数据点此刻落在哪（display 像素，y 向上）
    vx, vy, _vz = proj3d.proj_transform(xs, ys, zs, ax.M)
    disp = ax.transData.transform(np.column_stack([vx, vy]))
    rows = []
    for path in el["geometry"]["paths"]:
        pts = np.array(path["points"]) * [W, H]
        cx, cy = pts[:, 0].mean(), H - pts[:, 1].mean()
        j = int(np.argmin(np.hypot(disp[:, 0] - cx, disp[:, 1] - cy)))
        rows.append(
            {
                "diameter_px": float(pts[:, 0].max() - pts[:, 0].min()),
                "expected_px": float(np.sqrt(s[j]) * fig.dpi / 72.0),
                "off_px": float(np.hypot(disp[j, 0] - cx, disp[j, 1] - cy)),
            }
        )
    plt.close(fig)
    del sc
    return {"markers": rows, "n": n}


def main() -> None:
    print(json.dumps({"ink": ink(), "budget": budget(), "sizes": sizes()}))


if __name__ == "__main__":
    main()
