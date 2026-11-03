"""科研二维图的脚本组织与常见构图：原生 matplotlib ↔ Tavotto 的分层对照（原创夹具，固定数据与构图）。

一份脚本覆盖论文作图仓库里反复出现的组织方式：同目录 helper 模块（名字恰好与一个 PyPI 包同名、
本机又没装）、一个脚本出多张 Figure、同一张图存 PNG + PDF、存完就 close、两张**不同**的图写同一个
stem 的不同格式。构图覆盖 radar（polar + 填充多边形）、图例独占一个 Axes、柱图 + 误差棒 + hatch、
heatmap 逐格注释 + colorbar、twinx + 手动布局（`subplots_adjust` / `add_axes`）。

分层判据（每层单独断言，不用「运行成功」概括）：

1. 独立原生 matplotlib（不带 Tavotto 钩子的解释器子进程）写出的 PNG；
2. Tavotto 捕获：stem 集合、每个 stem 是哪一张图（标题）、同图多格式只登记一张、不同图同 stem 不并成
   多格式保存（现有账本契约：先认领者持有 stem）；
3. 空编辑导出与原生 PNG 逐像素相同（同一 Agg、同 dpi、同 figsize、bbox 不裁）；
4. 编辑真的可见、且不碰别的图；撤销回原样；热态 == 新 worker 冷重放；
5. 静态导入来源：同目录 helper 判本地，不进缺包、不给 pip 出口（零执行扫描）。
"""

from __future__ import annotations

import subprocess

import pytest

from tavotto.engine import importscan, pool

Image = pytest.importorskip("PIL.Image")

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "panels.py"
DPI = 100

#: 同目录 helper：名字与 PyPI 上的 SciencePlots（import 名 scienceplots）相同，本机没装
HELPER = """\
import matplotlib.pyplot as plt

PALETTE = ["#1b6ca8", "#e07b39", "#3a9e5f", "#8e5ba8"]


def apply():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7, "axes.linewidth": 0.8})
"""

SCRIPT = f"""\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

import scienceplots
from scienceplots import PALETTE

DPI = {DPI}


def radar():
    labels = ["speed", "memory", "accuracy", "recall", "cost"]
    a = [0.8, 0.55, 0.9, 0.7, 0.4]
    b = [0.6, 0.85, 0.75, 0.5, 0.7]
    theta = np.linspace(0, 2 * np.pi, len(labels), endpoint=False)
    fig = plt.figure(figsize=(3.0, 3.0), dpi=DPI)
    ax = fig.add_subplot(projection="polar")
    for vals, c, name in ((a, PALETTE[0], "ours"), (b, PALETTE[1], "base")):
        t = np.append(theta, theta[0])
        v = np.append(vals, vals[0])
        ax.plot(t, v, color=c, lw=1.2, label=name)
        ax.fill(t, v, color=c, alpha=0.25)
    ax.set_thetagrids(np.degrees(theta), labels)
    ax.set_ylim(0, 1)
    ax.set_title("radar")
    fig.savefig("radar.png", dpi=DPI)
    plt.close(fig)


def bars_with_legend_axes():
    fig = plt.figure(figsize=(4.0, 2.4), dpi=DPI)
    ax = fig.add_axes([0.1, 0.15, 0.6, 0.75])
    lax = fig.add_axes([0.74, 0.15, 0.24, 0.75])
    x = np.arange(3)
    hatches = ["", "//", ".."]
    for i, (h, c) in enumerate(zip(hatches, PALETTE)):
        ax.bar(x + i * 0.25, [1.0 + i * 0.2, 1.6 - i * 0.1, 0.8 + i * 0.3], 0.25,
               yerr=[0.1, 0.15, 0.08], capsize=2, color=c, hatch=h, edgecolor="black", lw=0.6)
    ax.set_xticks(x + 0.25, ["A", "B", "C"])
    ax.set_title("bars")
    lax.axis("off")
    lax.legend(handles=[Patch(facecolor=c, hatch=h, edgecolor="black", label=f"m{{i}}")
                        for i, (h, c) in enumerate(zip(hatches, PALETTE))], loc="center", frameon=False)
    fig.savefig("bars.png", dpi=DPI)
    fig.savefig("bars.pdf")  # 同一张图两种格式：只登记一张
    plt.close(fig)


def heatmap():
    data = np.array([[0.1, 0.4, 0.7], [0.3, 0.9, 0.2], [0.6, 0.5, 0.8]])
    fig, ax = plt.subplots(figsize=(3.0, 2.6), dpi=DPI)
    im = ax.imshow(data, cmap="viridis", vmin=0, vmax=1)
    for (r, c), v in np.ndenumerate(data):
        ax.text(c, r, f"{{v:.1f}}", ha="center", va="center", color="w" if v < 0.5 else "k")
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax.set_title("heatmap")
    fig.savefig("heatmap.png", dpi=DPI)
    plt.close(fig)


def twin_manual():
    t = np.linspace(0, 10, 50)
    fig, ax = plt.subplots(figsize=(3.6, 2.4), dpi=DPI)
    fig.subplots_adjust(left=0.15, right=0.82, bottom=0.18, top=0.86)
    ax.plot(t, np.sin(t), color=PALETTE[0])
    ax2 = ax.twinx()
    ax2.plot(t, 10 + 3 * np.cos(t), color=PALETTE[1], ls="--")
    ax.set_title("twin")
    fig.savefig("twin.png", dpi=DPI)
    # 不 close：留在 pyplot 里


def same_stem_two_figures():
    first, fa = plt.subplots(figsize=(2.0, 1.6), dpi=DPI)
    fa.plot([0, 1], [0, 1])
    fa.set_title("first")
    first.savefig("panel.png", dpi=DPI)
    second, fb = plt.subplots(figsize=(2.0, 1.6), dpi=DPI)
    fb.plot([0, 1], [1, 0])
    fb.set_title("second")
    second.savefig("panel.pdf")


def main():
    scienceplots.apply()
    radar()
    bars_with_legend_axes()
    heatmap()
    twin_manual()
    same_stem_two_figures()


if __name__ == "__main__":
    main()
"""

PNG_STEMS = ("radar", "bars", "heatmap", "twin")


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("research-patterns")
    (figs / SCRIPT_NAME).write_text(SCRIPT, encoding="utf-8")
    (figs / "scienceplots.py").write_text(HELPER, encoding="utf-8")
    return figs


@pytest.fixture(scope="module")
def native(library, tmp_path_factory):
    out = tmp_path_factory.mktemp("native-out")
    # 原生：在图库目录里跑（helper 要能 import），产物写在图库目录，再挪到 out
    subprocess.run([WORKER_PY, SCRIPT_NAME], cwd=library, check=True, timeout=180)
    for p in library.iterdir():
        if p.suffix in (".png", ".pdf"):
            p.rename(out / p.name)
    return out


def _worker(figs):
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    w.ensure_built()
    return w


@pytest.fixture(scope="module")
def hot(library):
    w = _worker(library)
    try:
        yield w
    finally:
        pool.discard(w)


def _export(w, stem, patches, path):
    w.export(stem, list(patches), str(path), "png", DPI)
    return Image.open(path).convert("RGB")


def _titles(w, stem):
    man = w.override(stem, [])["manifest"]
    return [
        f["value"]
        for e in man["elements"]
        if e["role"] == "title"
        for f in e["editable"]
        if f["prop"] == "text"
    ]


def test_native_matplotlib_writes_every_file(native):
    names = sorted(p.name for p in native.iterdir())
    assert names == [
        "bars.pdf",
        "bars.png",
        "heatmap.png",
        "panel.pdf",
        "panel.png",
        "radar.png",
        "twin.png",
    ]


def test_capture_identity_per_figure(hot):
    stems = [d["stem"] for d in hot.last_build_descriptors]
    for stem in (*PNG_STEMS, "panel"):
        assert stem in stems, stems
    assert stems.count("bars") == 1, "同一张图存 PNG + PDF 只登记一张"
    for stem in PNG_STEMS:
        assert _titles(hot, stem) == [stem]
    # 不同的图写同一个 stem 的不同格式：先认领者持有 stem，不并成「同一张图的两种格式」
    assert _titles(hot, "panel") == ["first"]


@pytest.fixture(scope="module")
def ledger(library):
    w = pool.one_shot(SCRIPT_NAME, str(library), "main")
    try:
        return w.ensure_built()["savefig_observations"]
    finally:
        pool.discard(w)


def test_ledger_records_both_formats_of_one_figure_and_two_figures_for_one_stem(ledger):
    assert ledger["complete"] is True
    by_stem: dict[str, list] = {}
    for r in ledger["records"]:
        by_stem.setdefault(r["stem"], []).append(r)
    bars = by_stem["bars"]
    assert sorted(r["options"]["format"] for r in bars) == ["pdf", "png"]
    assert len({r["figure_ordinal"] for r in bars}) == 1, "PNG / PDF 是同一张图"
    panel = by_stem["panel"]
    assert len({r["figure_ordinal"] for r in panel}) == 2, "panel.png / panel.pdf 是两张不同的图"


@pytest.mark.parametrize("stem", PNG_STEMS)
def test_empty_edit_export_is_pixel_identical_to_native(hot, native, stem, tmp_path):
    ours = _export(hot, stem, [], tmp_path / f"{stem}.png")
    theirs = Image.open(native / f"{stem}.png").convert("RGB")
    assert ours.size == theirs.size
    assert ours.tobytes() == theirs.tobytes()


def _gid(w, stem, role, nth=0):
    man = w.override(stem, [])["manifest"]
    return [e["gid"] for e in man["elements"] if e["role"] == role][nth]


def test_editing_one_figure_is_visible_and_leaves_the_others_alone(hot, library, tmp_path):
    gid = _gid(hot, "bars", "bar_series")
    patch = [{"gid": gid, "prop": "facecolor", "value": "#d62728"}]
    base = {s: _export(hot, s, [], tmp_path / f"b-{s}.png").tobytes() for s in PNG_STEMS}
    hot.override("bars", patch)
    edited = _export(hot, "bars", patch, tmp_path / "e-bars.png")
    assert edited.tobytes() != base["bars"]
    assert any(px[0] > 200 and px[1] < 60 and px[2] < 60 for px in edited.getdata()), (
        "改成的红色应当可见"
    )
    for s in PNG_STEMS:
        if s != "bars":
            assert _export(hot, s, [], tmp_path / f"o-{s}.png").tobytes() == base[s], s
    cold = _worker(library)
    try:
        assert _export(cold, "bars", patch, tmp_path / "c-bars.png").tobytes() == edited.tobytes()
    finally:
        pool.discard(cold)
    hot.override("bars", [])
    assert _export(hot, "bars", [], tmp_path / "u-bars.png").tobytes() == base["bars"]


def test_heatmap_annotations_and_colorbar_are_recognised(hot):
    man = hot.override("heatmap", [])["manifest"]
    roles = [e["role"] for e in man["elements"]]
    assert roles.count("text") >= 9, roles
    assert any(r in ("colorbar", "colorbar_axes") or "colorbar" in r for r in roles), roles


def test_same_directory_helper_is_local_not_a_missing_package(library):
    res = importscan.scan(library, SCRIPT_NAME)
    by = {c.module: c for c in res.classes}
    assert by["scienceplots"].bucket == importscan.BUCKET_LOCAL
    assert by["scienceplots"].local_path == "scienceplots.py"
