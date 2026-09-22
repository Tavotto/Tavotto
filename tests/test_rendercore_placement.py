"""面板落位的几何合同（统一实施包 U07，ADR 0065）——纯模型，不需要候选包。

期望**手算**（不调 `placement.place()` 反推，RC-095）：每条用例先写出「源里的这个点应该落到页面的
哪里」，再拿矩阵去映，比的是点的落点，不是矩阵的六个数长得像不像。
"""

from __future__ import annotations

import math

import pytest

from tavotto.rendercore import placement
from tavotto.rendercore.ir import apply


def _at(m, x, y):
    px, py = apply(m, x, y)
    return (round(px, 6), round(py, 6))


# ---------------------------------------------------------------- 可见框


def test_visible_box_is_the_bbox_when_the_form_has_no_matrix():
    assert placement.visible_box((15, 10, 285, 170), None) == (15.0, 10.0, 270.0, 160.0)


def test_visible_box_folds_rotate_and_userunit_from_the_form_matrix_once():
    """qpdf 对 CropBox [15 10 285 170] + /Rotate 90 + /UserUnit 2 给出 /Matrix [0 -2 2 0 0 540]
    （本机 pikepdf 10.13 实测）：可见框 320 × 540、原点 (20, −30)——旋转与 UserUnit 都在矩阵里，
    本函数只算包围盒，不再乘一次。"""
    vb = placement.visible_box((15, 10, 285, 170), (0.0, -2.0, 2.0, 0.0, 0.0, 540.0))
    assert vb == (20.0, -30.0, 320.0, 540.0)


# ---------------------------------------------------------------- 落位顺序


def test_plain_fit_maps_source_corners_to_rect_corners():
    """源可见框 (15,10,270×160) → 目标 (20,30,135×80)：缩放 0.5，源左下 → 目标左下，源右上 → 目标右上。"""
    pl = placement.place((15, 10, 270, 160), (20, 30, 135, 80))
    assert _at(pl.matrix, 15, 10) == (20, 30)
    assert _at(pl.matrix, 285, 170) == (155, 110)
    assert _at(pl.matrix, 40, 40) == (32.5, 45)  # 蓝矩形左下角
    assert pl.clip == (15, 10, 270, 160)
    assert pl.bbox == (20, 30, 155, 110)


def test_crop_selects_a_top_origin_sub_rectangle_and_maps_it_to_the_whole_rect():
    """crop (0.5, 0, 0.5, 0.5) = 源的右上四分之一（顶原点）。源可见框 (0,0,200×100)：那一块是
    x∈[100,200]、y∈[50,100]（y 向上）。它要填满目标 (10,10,50×50)：源 (100,50) → (10,10)，
    源 (200,100) → (60,60)；clip 就是那一块。"""
    pl = placement.place((0, 0, 200, 100), (10, 10, 50, 50), crop=(0.5, 0.0, 0.5, 0.5))
    assert pl.clip == (100.0, 50.0, 100.0, 50.0)
    assert _at(pl.matrix, 100, 50) == (10, 10)
    assert _at(pl.matrix, 200, 100) == (60, 60)


def test_rotate_90_cw_swaps_the_content_box_and_fills_the_rect():
    """目标 (0,0,50×100)，旋转 90° 顺时针：内容框是 100×50（宽高对调）。源左上角（顶行、左列）
    顺时针转 90° 后落到目标**右上**；源右上 → 目标右下；源左下 → 目标左上。y 向上的坐标里
    「上」是大 y。"""
    pl = placement.place((0, 0, 200, 100), (0, 0, 50, 100), rotate_cw_deg=90)
    assert _at(pl.matrix, 0, 100) == (50, 100)  # 源左上 → 目标右上
    assert _at(pl.matrix, 200, 100) == (50, 0)  # 源右上 → 目标右下
    assert _at(pl.matrix, 0, 0) == (0, 100)  # 源左下 → 目标左上
    assert tuple(round(v, 6) for v in pl.bbox) == (0, 0, 50, 100)  # 填满目标框


def test_rotate_270_cw_sends_the_top_left_to_the_bottom_left():
    pl = placement.place((0, 0, 200, 100), (0, 0, 50, 100), rotate_cw_deg=270)
    assert _at(pl.matrix, 0, 100) == (0, 0)  # 源左上 → 目标左下


def test_rotate_180_keeps_the_box_and_flips_both_axes():
    pl = placement.place((0, 0, 200, 100), (10, 20, 200, 100), rotate_cw_deg=180)
    assert _at(pl.matrix, 0, 100) == (210, 20)  # 源左上 → 目标右下


def test_flip_h_mirrors_x_about_the_center_before_rotation():
    """先翻后转（`PanelView` 的 `rotate(r) scale(-1, 1)`）：flip_h + 90° 时，源左上先镜像成右上，
    再顺时针转 90° 落到目标右下。只翻不转：源左上 → 目标右上。"""
    pl = placement.place((0, 0, 200, 100), (0, 0, 200, 100), flip_h=True)
    assert _at(pl.matrix, 0, 100) == (200, 100)
    assert _at(pl.matrix, 200, 100) == (0, 100)
    pl = placement.place((0, 0, 200, 100), (0, 0, 50, 100), rotate_cw_deg=90, flip_h=True)
    assert _at(pl.matrix, 0, 100) == (50, 0)  # 源左上 → 目标右下


def test_flip_v_mirrors_y_and_keeps_x():
    pl = placement.place((0, 0, 200, 100), (0, 0, 200, 100), flip_v=True)
    assert _at(pl.matrix, 0, 100) == (0, 0)
    assert _at(pl.matrix, 0, 0) == (0, 100)


def test_both_flips_equal_a_180_rotation_of_the_content():
    a = placement.place((0, 0, 200, 100), (0, 0, 200, 100), flip_h=True, flip_v=True)
    b = placement.place((0, 0, 200, 100), (0, 0, 200, 100), rotate_cw_deg=180)
    for x, y in ((0, 0), (200, 100), (37, 61)):
        assert _at(a.matrix, x, y) == _at(b.matrix, x, y)


def test_rotate_then_crop_of_a_unit_square_places_the_image_sub_block():
    """位图的可见框是单位正方形。crop 左半 (0,0,0.5,1) + 90°：内容框 = 目标的高×宽；源左半的
    左上 (0,1) → 目标右上，源左半的右下 (0.5,0) → 目标左下。"""
    pl = placement.place((0, 0, 1, 1), (0, 0, 40, 80), crop=(0, 0, 0.5, 1), rotate_cw_deg=90)
    assert pl.clip == (0.0, 0.0, 0.5, 1.0)
    assert _at(pl.matrix, 0, 1) == (40, 80)
    assert _at(pl.matrix, 0.5, 0) == (0, 0)


def test_rotation_swaps_is_the_odd_multiple_of_ninety():
    assert [placement.rotation_swaps(d) for d in (0, 90, 180, 270, 360, 450)] == [
        False,
        True,
        False,
        True,
        False,
        True,
    ]


def test_matrix_is_invertible_for_every_combination():
    for rot in (0, 90, 180, 270):
        for fh in (False, True):
            for fv in (False, True):
                pl = placement.place(
                    (3, 4, 50, 20), (1, 2, 7, 9), rotate_cw_deg=rot, flip_h=fh, flip_v=fv
                )
                a, b, c, d, *_ = pl.matrix
                assert not math.isclose(a * d - b * c, 0.0), (rot, fh, fv)


@pytest.mark.parametrize("bad", [(0, 0, 0, 10), (0, 0, 10, 0)])
def test_a_degenerate_source_box_is_a_zero_division_not_a_silent_identity(bad):
    with pytest.raises(ZeroDivisionError):
        placement.place(bad, (0, 0, 10, 10))
