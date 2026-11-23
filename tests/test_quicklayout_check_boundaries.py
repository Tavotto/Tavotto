"""一键排版验收指标（`quicklayout.check`，ADR 0199 §三 / §六）的边界：真实科研图会有的结构，而不只是两张一样的图。

`tests/test_quicklayout.py` 钉的是「对齐的过、错位的抓」；这里补它没碰到的几处——0.1 pt 这条线本身、不等宽网格、
一张图里的 twin 轴 / inset / 色条、等比例轴、超长标签撑大的墨迹框。判据的主语是**绘图区**（主坐标轴外框的并集），
不是图框也不是墨迹框；每条用例都先说清它动的是哪一个。

全部是纯几何（手写 manifest），不起 worker、不要闭源求解器。
"""

from __future__ import annotations

import pytest

from tavotto.engine import quicklayout

PT = quicklayout.PT_MM
AXES = [0.2, 0.15, 0.75, 0.7]


def _axes(gid, bbox, *, colorbar=False, aspect="auto"):
    el = {
        "gid": gid,
        "role": "axes",
        "bbox": list(bbox),
        "editable": [{"prop": "aspect", "value": aspect}],
    }
    if colorbar:
        el["is_colorbar"] = True
    return el


def _panel(key, box, axes, *, pad=0.5, top=5.0, ink=None):
    """图框 `box`（页面 mm），图幅与图框等大；墨迹框默认在图框内侧 `pad` mm、上方留 `top` mm 给标签。"""
    p = quicklayout.Panel(
        key=key, rel_id=f"{key}.pdf", base=[], box=list(box), render=lambda _p: {}
    )
    w, h = box[2:]
    ink = ink or (pad / w, top / h, 1 - pad / w, 1 - pad / h)
    p.manifest = {
        "size_mm": [w, h],
        "elements": [
            {"gid": "figure", "role": "figure", "bbox": [0, 0, 1, 1], "ink_bbox": list(ink)},
            *axes,
        ],
    }
    p.size_mm = [w, h]
    p.equal_aspect = quicklayout._equal_aspect(p.manifest)
    return p


# 页宽 150、留白 2、图间距 3：两列图框 x = 1.5 / 76，宽 72.5（与 test_quicklayout.py 同一套数）
A_BOX = [1.5, 1.5, 72.5, 50]
B_BOX = [76.0, 1.5, 72.5, 50]
LABELS = [{"ref": "a", "x": 2, "y": 2}, {"ref": "b", "x": 76.5, "y": 2}]


def _metrics(panels, *, page_w=150, labels=LABELS, **kw):
    return sorted({m["metric"] for m in quicklayout.check(panels, page_w, 3, 2, labels, 9, **kw)})


# ---------------------------------------------------------------------------
# 0.1 pt 这条线本身：不许被悄悄放宽成参考实现那样的 1.5 pt
# ---------------------------------------------------------------------------
def test_the_alignment_tolerance_is_a_tenth_of_a_point():
    assert quicklayout.ALIGN_TOL_MM == pytest.approx(0.1 * 25.4 / 72)


@pytest.mark.parametrize(("off_pt", "caught"), [(0.08, False), (0.12, True), (1.4, True)])
def test_row_bottoms_are_held_to_a_tenth_of_a_point(off_pt, caught):
    """同一行两张图，右边那张的绘图区整体低 `off_pt`。1.4 pt 在 1.5 pt 的容差下会放过去，这里必须抓到。"""
    dy = off_pt * PT / 50  # 图高 50 mm → 比例
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    b = _panel("b", B_BOX, [_axes("axes_0", [AXES[0], AXES[1] + dy, AXES[2], AXES[3]])])
    got = _metrics([a, b])
    assert ("align_bottom" in got) is caught and ("align_top" in got) is caught, got


@pytest.mark.parametrize(("off_pt", "caught"), [(0.08, False), (0.12, True)])
def test_column_lefts_are_held_to_a_tenth_of_a_point(off_pt, caught):
    """同一列上下两张图：下面那张刻度标签更宽，绘图区左边被挤开 `off_pt`。"""
    dx = off_pt * PT / 72.5
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    c = _panel(
        "c",
        [1.5, 54.5, 72.5, 50],
        [_axes("axes_0", [AXES[0] + dx, AXES[1], AXES[2] - dx, AXES[3]])],
    )
    labels = [LABELS[0], {"ref": "c", "x": 2, "y": 55}]
    assert ("align_left" in _metrics([a, c], labels=labels)) is caught


# ---------------------------------------------------------------------------
# 不等宽网格
# ---------------------------------------------------------------------------
def test_unequal_width_columns_pass_when_edges_line_up():
    """2:1 的两列、两行：行内上下边对齐、列内左边对齐即可，不要求各列等宽。"""
    wide, narrow = 96.0, 47.0  # 1.5 + 96 + 3(-1) … 两列图框紧挨，文字外沿之间正好 3 mm
    x2 = 1.5 + wide + 2.0
    boxes = {
        "a": [1.5, 1.5, wide, 40],
        "b": [x2, 1.5, narrow, 40],
        "c": [1.5, 43.5, wide, 40],
        "d": [x2, 43.5, narrow, 40],
    }
    panels = [
        _panel(k, box, [_axes("axes_0", [12 / box[2], 0.15, 1 - 16 / box[2], 0.7])])
        for k, box in boxes.items()
    ]
    labels = [{"ref": k, "x": box[0] + 0.5, "y": box[1] + 0.5} for k, box in boxes.items()]
    assert _metrics(panels, labels=labels) == []


def test_unequal_width_columns_still_catch_a_shifted_left_edge():
    wide, narrow = 96.0, 47.0
    x2 = 1.5 + wide + 2.0
    a = _panel("a", [1.5, 1.5, wide, 40], [_axes("axes_0", [12 / wide, 0.15, 0.8, 0.7])])
    b = _panel("b", [x2, 1.5, narrow, 40], [_axes("axes_0", [12 / narrow, 0.15, 0.6, 0.7])])
    d = _panel(
        "d", [x2, 43.5, narrow, 40], [_axes("axes_0", [13 / narrow, 0.15, 0.6, 0.7])]
    )  # 左边多让了 1 mm
    labels = [
        {"ref": k, "x": p.box[0] + 0.5, "y": p.box[1] + 0.5}
        for k, p in (("a", a), ("b", b), ("d", d))
    ]
    assert "align_left" in _metrics([a, b, d], labels=labels)


# ---------------------------------------------------------------------------
# 一张图里有多个坐标轴（ADR 0199 §三）
# ---------------------------------------------------------------------------
def test_twin_axes_do_not_change_the_plot_area():
    """twin 轴与宿主外框相同：并集不变，对齐照旧通过。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES), _axes("axes_1", AXES)])
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert quicklayout._box_rect(a) == pytest.approx([0.2, 0.15, 0.95, 0.85])
    assert _metrics([a, b]) == []


def test_an_inset_is_not_part_of_the_plot_area_wherever_it_sits():
    """inset 完全落在主轴里：不参与并集——哪怕它贴着主轴的边。"""
    inset = [0.2, 0.15, 0.2, 0.2]  # 贴着主轴左上角
    a = _panel("a", A_BOX, [_axes("axes_0", AXES), _axes("axes_1", inset)])
    assert [e["gid"] for e in quicklayout._main_axes(a.manifest)] == ["axes_0"]
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert _metrics([a, b]) == []


def test_a_colorbar_does_not_widen_the_plot_area():
    """色条轴在主轴右侧、伸到主轴外：不算主轴。把它算进去的话，这一行的绘图区右边 / 上下边就比不了了。"""
    main = [0.2, 0.15, 0.62, 0.7]
    bar = [0.85, 0.1, 0.03, 0.8]  # 比主轴高：算进并集就会把上下边带偏
    a = _panel("a", A_BOX, [_axes("axes_0", main), _axes("axes_1", bar, colorbar=True)])
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert quicklayout._box_rect(a) == pytest.approx([0.2, 0.15, 0.82, 0.85])
    assert _metrics([a, b]) == []


def test_side_by_side_subplots_in_one_figure_count_as_one_plot_area():
    """一张图自己就是 1×2 的 subplots：绘图区是两个主轴的并集，与旁边单轴图比的是并集的上下边。"""
    left, right = [0.1, 0.15, 0.38, 0.7], [0.57, 0.15, 0.38, 0.7]
    a = _panel("a", A_BOX, [_axes("axes_0", left), _axes("axes_1", right)])
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert quicklayout._box_rect(a) == pytest.approx([0.1, 0.15, 0.95, 0.85])
    assert _metrics([a, b]) == []


# ---------------------------------------------------------------------------
# 等比例轴：只参与排位，不计对齐（ADR 0199 §三）——但间距、页边、重叠照查
# ---------------------------------------------------------------------------
def test_an_equal_aspect_panel_is_exempt_from_alignment_only():
    off = [AXES[0], AXES[1] + 0.06, AXES[2], AXES[3] - 0.1]  # imshow 在 position 框里收缩了
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    b = _panel("b", B_BOX, [_axes("axes_0", off, aspect="equal")])
    assert b.equal_aspect and _metrics([a, b]) == []
    # 同一张图不是等比例轴时，这个错位必须被抓到
    plain = _panel("b", B_BOX, [_axes("axes_0", off)])
    assert {"align_bottom", "align_top"} <= set(_metrics([a, plain]))
    # 豁免的只是对齐：等比例轴的图压到邻图上照样不行
    crowd = _panel("b", [73.0, 1.5, 72.5, 50], [_axes("axes_0", off, aspect="equal")])
    assert "gap" in _metrics([a, crowd])


# ---------------------------------------------------------------------------
# 超长标签：对齐看绘图区，间距与页边看墨迹框
# ---------------------------------------------------------------------------
def test_a_long_tick_label_may_widen_the_ink_without_breaking_alignment():
    """左图的纵轴刻度标签很长（墨迹框向左撑满），绘图区没动：对齐通过。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)], ink=(0.5 / 72.5, 0.1, 1 - 0.5 / 72.5, 0.99))
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert _metrics([a, b]) == []


def test_a_label_running_past_its_frame_is_caught_as_edge_and_gap():
    """横轴的超长类别标签伸出图框右边 1 mm：贴边（会被裁）、与右邻的间距不足，两条都要报。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)], ink=(0.5 / 72.5, 0.1, 1 + 1.0 / 72.5, 0.99))
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert {"edge", "gap"} <= set(_metrics([a, b]))


def test_a_label_running_off_the_bottom_of_a_kept_canvas_is_caught():
    """保持画布大小（`page_h` 给了）：最下面那张图的文字外沿离页底不足留白。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert _metrics([a, b], page_h=60) == []
    assert "page_edge" in _metrics([a, b], page_h=52)


# ---------------------------------------------------------------------------
# 量不了的输入
# ---------------------------------------------------------------------------
def test_a_figure_without_any_main_axes_takes_no_part_in_alignment():
    """纯文字 / 示意图（没有坐标轴）：没有绘图区可比，只参与排位——不因此判别的图没对齐。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    b = _panel("b", B_BOX, [])
    assert quicklayout._main_axes(b.manifest) == [] and _metrics([a, b]) == []


def test_a_missing_ink_box_falls_back_to_the_whole_frame_and_fails_the_edge_check():
    """manifest 没给墨迹框：按整张图幅算（最保守），于是「文字贴着图框」这一条不会被默认放过。"""
    a = _panel("a", A_BOX, [_axes("axes_0", AXES)])
    del a.manifest["elements"][0]["ink_bbox"]
    b = _panel("b", B_BOX, [_axes("axes_0", AXES)])
    assert "edge" in _metrics([a, b])
