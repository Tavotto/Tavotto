"""三维与大图的边界：产物与能力声明一致、原分辨率不被悄悄降低（原创有界夹具）。

* **三维**：预览 / 导出与独立原生 matplotlib 逐像素相同；曲面、散点这类 3D 数据图元只给样式字段，
  不把「几何 / 数据」包装成二维可编辑对象（能力层实况见 `tests/test_mplot3d_data_artists.py`，这里只钉
  原生对照与字段白名单）。本轮不扩展三维编辑。
* **大图（中等规模）**：worker 直接导出（`figsession.do_export` → `Figure.savefig`）在请求的 dpi 下出
  **原尺寸**位图并与原生逐像素相同——不降 dpi 冒充通过。RenderCore 最终导出的条带栅格与像素预算是另一条路径，
  由 `tests/test_rendercore_renderchild.py` 看护；两条路径的保护互不替代（worker 直出前面**没有**像素预算，
  见交付报告的剩余限制）。
"""

from __future__ import annotations

import subprocess

import pytest

from tavotto.engine import pool

Image = pytest.importorskip("PIL.Image")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

DPI = 100
BIG_DPI = 300
BIG_IN = (10.0, 7.5)

SCRIPT = f"""\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    plt.rcParams.update({{"font.family": "DejaVu Sans", "font.size": 7}})
    fig = plt.figure(figsize=(3.2, 3.0), dpi={DPI})
    ax = fig.add_subplot(projection="3d")
    u = np.linspace(-2, 2, 24)
    X, Y = np.meshgrid(u, u)
    Z = np.exp(-(X ** 2 + Y ** 2) / 2)
    ax.plot_surface(X, Y, Z, cmap="viridis", linewidth=0, antialiased=False)
    rng = np.random.default_rng(7)
    p = rng.uniform(-2, 2, size=(30, 2))
    ax.scatter(p[:, 0], p[:, 1], np.full(30, 1.1), color="#d62728", s=8)
    ax.view_init(elev=28, azim=-60)
    ax.set_title("surface")
    fig.savefig("surf3d.png", dpi={DPI})

    big, bax = plt.subplots(2, 3, figsize={BIG_IN!r}, dpi={DPI})
    t = np.linspace(0, 20, 4000)
    for i, a in enumerate(bax.flat):
        a.plot(t, np.sin(t * (i + 1) / 3) * np.exp(-t / 15), lw=0.6)
        a.set_title(f"panel {{i}}")
    big.savefig("big.png", dpi={BIG_DPI})


if __name__ == "__main__":
    main()
"""

#: 3D 数据图元允许出现的字段：样式与排列。出现别的（尤其是坐标 / 数据 / 几何）即是把 3D 当 2D 包装
STYLE_PROPS = {
    "label",
    "color",
    "facecolor",
    "edgecolor",
    "linewidth",
    "linestyle",
    "alpha",
    "visible",
    "zorder",
    "cmap",
    "vmin",
    "vmax",
    "size",
    "marker",
    "hatch",
}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("bounds")
    (figs / "bounds.py").write_text(SCRIPT, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def native(library, tmp_path_factory):
    out = tmp_path_factory.mktemp("native")
    subprocess.run([WORKER_PY, str(library / "bounds.py")], cwd=out, check=True, timeout=300)
    return out


@pytest.fixture(scope="module")
def hot(library):
    w = pool.one_shot("bounds.py", str(library), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def test_3d_export_is_pixel_identical_to_native(hot, native, tmp_path):
    hot.export("surf3d", [], str(tmp_path / "s.png"), "png", DPI)
    ours = Image.open(tmp_path / "s.png").convert("RGB")
    theirs = Image.open(native / "surf3d.png").convert("RGB")
    assert ours.size == theirs.size
    assert ours.tobytes() == theirs.tobytes()


def test_3d_data_artists_expose_style_only(hot):
    man = hot.override("surf3d", [])["manifest"]
    # 3D 轴上的数据图元 role 与 `manifest._DATA_ROLES_3D` 同一张表（曲面登记成 fill、散点 scatter）
    data_els = [
        e
        for e in man["elements"]
        if e["role"] in ("line", "fill", "scatter", "collection", "linecoll")
    ]
    assert {e["role"] for e in data_els} >= {"fill", "scatter"}, [
        e["role"] for e in man["elements"]
    ]
    for e in data_els:
        props = {f["prop"] for f in e.get("editable", [])}
        assert props <= STYLE_PROPS, (e["gid"], props - STYLE_PROPS)


def test_moderate_large_figure_keeps_its_resolution_and_matches_native(hot, native, tmp_path):
    hot.export("big", [], str(tmp_path / "big.png"), "png", BIG_DPI)
    ours = Image.open(tmp_path / "big.png").convert("RGB")
    theirs = Image.open(native / "big.png").convert("RGB")
    expected = (round(BIG_IN[0] * BIG_DPI), round(BIG_IN[1] * BIG_DPI))
    assert ours.size == theirs.size == expected, "不得降 dpi / 缩小尺寸冒充通过"
    assert ours.tobytes() == theirs.tobytes()
