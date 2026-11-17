"""外部审阅：边框线 / 网格线 / 刻度线作为独立点选目标（ADR 0187 修订 2026-11-17）。

两层：引擎 manifest 的 `spines`（paint_order / stroke_pt）与 `grid` / `tickmarks`（真跑 worker，
在科学栈解释器里量），以及 `reviewshare.manifest_elements` / `layout_elements` 把它们摊成元素表。
画布不读这些字段——这里不测画布。
"""

from __future__ import annotations

import pytest

from tavotto.engine import pool, reviewshare

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ENTRY = "main"

LIBRARY = {
    "fig_grid.py": """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1, 2], [0, 1, 0.5])
    ax.grid(True)
    fig.savefig("Grid.pdf")
""",
    "fig_nogrid.py": """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1, 2], [0, 1, 0.5])
    ax.spines["right"].set_visible(False)
    ax.spines["top"].set_visible(False)
    fig.savefig("NoGrid.pdf")
""",
    "fig_dirs.py": """\
import matplotlib.pyplot as plt


def main():
    fig, ax = plt.subplots(figsize=(4.0, 3.0))
    ax.plot([0, 1, 2], [0, 1, 0.5])
    ax.tick_params(axis="x", direction="in", length=8, top=True)
    ax.tick_params(axis="y", direction="inout", length=10)
    fig.savefig("Dirs.pdf")
""",
    "fig_polar.py": """\
import matplotlib.pyplot as plt


def main():
    fig = plt.figure(figsize=(4.0, 3.0))
    p = fig.add_subplot(1, 1, 1, projection="polar")
    p.plot([0, 1, 2], [1, 2, 3])
    fig.savefig("Polar.pdf")
""",
}


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    figs = tmp_path_factory.mktemp("review-guides")
    for name, src in LIBRARY.items():
        (figs / name).write_text(src, encoding="utf-8")
    return figs


def _render(figs, script, stem):
    w = pool.one_shot(script, str(figs), ENTRY)
    w.ensure_built()
    try:
        resp = w.override(stem, [])
        assert not resp.get("warnings"), resp["warnings"]
        return resp["manifest"]
    finally:
        pool.discard(w)


def _axes(man):
    return next(e for e in man["elements"] if e["gid"] == "axes_0")


def _points(geom):
    return [p for path in geom["paths"] for p in path["points"]]


@needs_worker
def test_manifest_axes_carry_grid_and_tickmarks_inside_and_around_the_frame(library):
    ax = _axes(_render(library, "fig_grid.py", "Grid"))
    x, y, w, h = ax["bbox"]
    tol = 2e-3
    for which in ("x", "y"):
        g = ax["grid"][which]
        assert g["stroke"] and not g["fill"] and g["stroke_pt"] > 0
        assert "paint_order" in g and "clip" in g
        for px, py in _points(g):
            assert x - tol <= px <= x + w + tol and y - tol <= py <= y + h + tol
        assert len(g["paths"]) >= 3
    # x 网格线是竖线、y 网格线是横线
    for path in ax["grid"]["x"]["paths"]:
        assert abs(path["points"][0][0] - path["points"][-1][0]) < 1e-4
    for path in ax["grid"]["y"]["paths"]:
        assert abs(path["points"][0][1] - path["points"][-1][1]) < 1e-4
    # 默认 out 方向：x 刻度线从下沿往下伸（y 变大），y 刻度线从左沿往左伸
    for path in ax["tickmarks"]["x"]["paths"]:
        (_, ya), (_, yb) = path["points"][0], path["points"][-1]
        assert abs(max(ya, yb) - min(ya, yb)) > 1e-3
        assert min(ya, yb) == pytest.approx(y + h, abs=tol)
    for path in ax["tickmarks"]["y"]["paths"]:
        (xa, _), (xb, _) = path["points"][0], path["points"][-1]
        assert max(xa, xb) == pytest.approx(x, abs=tol)
    # 边框线带绘制事实
    for side in ("left", "bottom", "top", "right"):
        assert ax["spines"][side]["stroke_pt"] > 0
        assert isinstance(ax["spines"][side]["paint_order"], int)


@needs_worker
def test_tick_direction_decides_which_side_the_mark_sticks_out(library):
    ax = _axes(_render(library, "fig_dirs.py", "Dirs"))
    x, y, w, h = ax["bbox"]
    ys = [p[1] for p in _points(ax["tickmarks"]["x"])]
    # x：in（朝框里）+ top=True（上沿也有）→ 刻度线全在框内、上下沿都有
    assert min(ys) >= y - 2e-3 and max(ys) <= y + h + 2e-3
    assert min(ys) == pytest.approx(y, abs=2e-3) and max(ys) == pytest.approx(y + h, abs=2e-3)
    # y：inout → 左沿两侧都伸出
    xs = [p[0] for p in _points(ax["tickmarks"]["y"])]
    assert min(xs) < x - 1e-3 and max(xs) > x + 1e-3


@needs_worker
def test_no_grid_no_guides_and_polar_has_none(library):
    ax = _axes(_render(library, "fig_nogrid.py", "NoGrid"))
    assert "grid" not in ax
    assert set(ax["tickmarks"]) == {"x", "y"}
    pol = _axes(_render(library, "fig_polar.py", "Polar"))
    assert "grid" not in pol and "tickmarks" not in pol


# ---------------------------------------------------------------------------
# reviewshare：元素表
# ---------------------------------------------------------------------------
def _fake_manifest(**over):
    ax = {
        "gid": "axes_0",
        "role": "axes",
        "bbox": [0.1, 0.1, 0.8, 0.8],
        "spines": {
            "left": {
                "visible": True,
                "from": [0.1, 0.9],
                "to": [0.1, 0.1],
                "stroke_pt": 0.8,
                "paint_order": 7,
            },
            "bottom": {
                "visible": True,
                "from": [0.1, 0.9],
                "to": [0.9, 0.9],
                "stroke_pt": 0.8,
                "paint_order": 7,
            },
            "top": {"visible": False, "from": [0.1, 0.1], "to": [0.9, 0.1]},
            "right": {"visible": False, "from": [0.9, 0.9], "to": [0.9, 0.1]},
        },
        "grid": {
            "x": {
                "kind": "multi_path",
                "paths": [
                    {"points": [[0.3, 0.9], [0.3, 0.1]], "closed": False},
                    {"points": [[0.5, 0.9], [0.5, 0.1]], "closed": False},
                ],
                "fill": False,
                "stroke": True,
                "stroke_pt": 0.8,
                "clip": [0.1, 0.1, 0.8, 0.8],
                "paint_order": 5,
            }
        },
        "tickmarks": {
            "y": {
                "kind": "multi_path",
                "paths": [
                    {"points": [[0.1, 0.5], [0.09, 0.5]], "closed": False},
                    {"points": [[0.1, 0.7], [0.09, 0.7]], "closed": False},
                ],
                "fill": False,
                "stroke": True,
                "stroke_pt": 0.8,
                "paint_order": 6,
            }
        },
    }
    ax.update(over)
    return {"elements": [{"gid": "figure", "role": "figure", "bbox": [0, 0, 1, 1]}, ax]}


def _by_gid(items):
    return {i["gid"]: i for i in items}


def test_manifest_elements_emit_guides_as_structural_targets():
    items = _by_gid(reviewshare.manifest_elements(_fake_manifest()))
    assert set(items) == {
        "axes_0",
        "axes_0.spine_left",
        "axes_0.spine_bottom",
        "axes_0.xgrid",
        "axes_0.ytickmarks",
    }
    assert items["axes_0.spine_left"]["label"] == "左轴线"
    assert items["axes_0.spine_left"]["role"] == "spine"
    assert items["axes_0.spine_bottom"]["label"] == "下轴线"
    assert items["axes_0.xgrid"]["label"] == "X 网格线"
    assert items["axes_0.xgrid"]["role"] == "gridline"
    assert items["axes_0.ytickmarks"]["label"] == "Y 刻度线"
    assert items["axes_0.ytickmarks"]["role"] == "tickmark"
    left = items["axes_0.spine_left"]
    # 竖线给一点厚度，bbox 不被丢；几何是 from→to 的描边折线，带线宽与次序
    assert left["bbox"][2] > 0 and left["bbox"][3] == pytest.approx(0.8)
    assert left["geometry"]["stroke"] and left["geometry"]["stroke_pt"] == 0.8
    assert left["geometry"]["paint_order"] == 7 and left["paint_order"] == 7
    assert items["axes_0.xgrid"]["geometry"]["paint_order"] == 5
    # 画框之外的刻度线也保留（刻度线伸在框外、仍在画面内）
    assert items["axes_0.ytickmarks"]["bbox"][0] < 0.1
    # 隐藏的边没有目标
    assert "axes_0.spine_top" not in items and "axes_0.spine_right" not in items


def test_manifest_elements_without_guides_stay_as_before():
    man = _fake_manifest()
    for k in ("spines", "grid", "tickmarks"):
        del man["elements"][1][k]
    assert [i["gid"] for i in reviewshare.manifest_elements(man)] == ["axes_0"]


def test_guides_are_clipped_to_the_visible_picture():
    man = _fake_manifest()
    man["elements"][1]["grid"]["x"]["paths"].append(
        {"points": [[0.7, 1.4], [0.7, 0.5]], "closed": False}
    )
    items = _by_gid(reviewshare.manifest_elements(man))
    for p in items["axes_0.xgrid"]["geometry"]["paths"]:
        assert all(0 <= q[1] <= 1 for q in p["points"])


def test_layout_elements_prefix_guide_gids_and_move_them_onto_the_page():
    objs = [{"id": "o1", "x_mm": 10, "y_mm": 10, "w_mm": 100, "h_mm": 50, "type": "panel"}]
    keys = reviewshare.panel_keys(objs)
    oid = keys[0]
    els = reviewshare.layout_elements(200, 100, objs, {oid: _fake_manifest()})
    by = _by_gid(els)
    g = by[f"{oid}::axes_0.spine_left"]
    assert g["label"] == "左轴线" and g["role"] == "spine"
    pts = [q for p in g["geometry"]["paths"] for q in p["points"]]
    # 面板在页面左上：x = (10 + 0.1*100)/200 = 0.1；y 从 (10+0.9*50)/100 = 0.55 到 0.15
    assert pts[0][0] == pytest.approx(0.1, abs=1e-4)
    assert {round(q[1], 3) for q in pts} == {0.55, 0.15}
    assert f"{oid}::axes_0.xgrid" in by and f"{oid}::axes_0.ytickmarks" in by
