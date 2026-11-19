"""数据线线宽下限（`min_line_width_pt`）对**真实 matplotlib manifest** 的误报检验。

手写向量测不到「manifest 里的 linewidth 是能设的描边、不是画出来的描边」这类误报：
默认柱图没有可见描边（edgecolor `none`、linewidth 仍是 1.0），默认散点的描边在 manifest 里
与「黑边」同写成 `#000000`。这里用真 worker 跑一遍：缩小面板后，没画出来的描边一律不报，
真画出来的数据线照报，坐标轴脊线 / 刻度 / 图例不报。
"""

import pytest

from tavotto.engine import pool, preflight, profiles

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

SCRIPT_NAME = "fig_minlw.py"
STEM = "MinLw"

LIBRARY = """\
import matplotlib.pyplot as plt
import numpy as np


def main():
    fig, axs = plt.subplots(2, 3, figsize=(6, 4), dpi=100)
    x = np.arange(5)
    axs[0, 0].bar(x, x + 1)                      # 默认柱：没有可见描边
    axs[0, 1].scatter(x, x)                      # 默认散点
    axs[0, 2].fill_between(x, x, x + 1)          # 默认填充
    axs[1, 0].plot(x, x, "o")                    # 只画点
    axs[1, 1].plot(x, x, linewidth=0.5)          # 真画出来的细线
    axs[1, 2].bar(x, x + 1, edgecolor="k", linewidth=0.5)  # 有可见描边的柱
    fig.savefig("MinLw.pdf")
"""


@pytest.fixture(scope="module")
def manifest(tmp_path_factory):
    figs = tmp_path_factory.mktemp("minlw-figures")
    (figs / SCRIPT_NAME).write_text(LIBRARY, encoding="utf-8")
    w = pool.one_shot(SCRIPT_NAME, str(figs), "main")
    try:
        w.ensure_built()
        return w.override(STEM, [])["manifest"]
    finally:
        pool.discard(w)


def _spec(manifest, scale):
    rect = [0, 0, 80.0, 60.0]
    panel = {
        "id": "p1",
        "name": "p1",
        "kind": "pdf",
        "rect_mm": rect,
        "scale": scale,
        "manifest": manifest,
        "px_w": None,
        "missing": False,
        "stale": False,
        "render_error": None,
        "unapplied_overrides": 0,
        "hidden": False,
    }
    return {
        "page": {"w_mm": 80.0, "h_mm": 60.0, "margin_mm": 0.0},
        "panels": [panel],
        "texts": [],
        "objects": [{"id": "p1", "type": "panel", "rect_mm": rect, "hidden": False}],
    }


def test_only_drawn_data_lines_are_reported_on_a_real_manifest(manifest):
    profile = profiles.load("journal-base-v1", {"min_line_width_pt": 2.0})
    hits = [
        i for i in preflight.run(_spec(manifest, 0.4), profile) if i["id"] == "line-width-below-min"
    ]
    assert len(hits) == 1
    # 只有真画出来的：axes_4 的 0.5pt 折线与 axes_5 带黑边的柱（缩放后更细）；
    # 默认柱 / 默认散点 / 填充 / 只画点的线 / 坐标轴脊线都不在其中
    gids = sorted(hits[0]["gids"])
    assert any(g.startswith("axes_4.lines_") for g in gids), gids
    assert any(g.startswith("axes_5.barseries_0.bar_") for g in gids), gids
    for g in gids:
        assert not g.startswith(("axes_0.", "axes_1.", "axes_2.", "axes_3.")), gids
        assert g.split(".")[-1] not in ("xticks", "yticks") and g != "axes_0", gids
