"""组图↔子图 override 同步的纯函数测试（_remap_point / _best_offset / _axes_info）。"""

import pytest

from tavotto import app as m


def test_remap_point_identity():
    bb = [0.1, 0.1, 0.4, 0.4]
    pt, clamped = m._remap_point([0.3, 0.3], bb, bb)
    assert pt == pytest.approx([0.3, 0.3])
    assert clamped is False


def test_remap_point_translates_between_axes():
    src, dst = [0.1, 0.1, 0.4, 0.4], [0.5, 0.5, 0.4, 0.4]
    pt, clamped = m._remap_point([0.3, 0.3], src, dst)  # 源框中心 → 目标框中心
    assert pt == pytest.approx([0.7, 0.7])
    assert clamped is False


def test_remap_point_scales_with_bbox():
    src, dst = [0.0, 0.0, 0.5, 0.5], [0.0, 0.0, 1.0, 1.0]
    pt, _ = m._remap_point([0.25, 0.25], src, dst)
    assert pt == pytest.approx([0.5, 0.5])


def test_remap_point_clamps_out_of_canvas():
    src, dst = [0.0, 0.0, 0.5, 0.5], [0.8, 0.8, 0.5, 0.5]
    pt, clamped = m._remap_point([0.5, 0.5], src, dst)  # 落到 1.3 → 钳回
    assert clamped is True
    assert pt == [0.98, 0.98]


def test_remap_point_zero_size_bbox_falls_back_to_center():
    pt, _ = m._remap_point([0.3, 0.3], [0.1, 0.1, 0.0, 0.0], [0.0, 0.0, 1.0, 1.0])
    assert pt == pytest.approx([0.5, 0.5])


def _ax(texts, n):
    return {"bbox": None, "texts": set(texts), "n": n}


def test_best_offset_matches_text_signature():
    big = [_ax(["A"], 3), _ax(["B"], 3), _ax(["C"], 3), _ax(["D"], 3)]
    small = [_ax(["B"], 3), _ax(["C"], 3)]
    assert m._best_offset(big, small) == 1


def test_best_offset_prefers_similar_element_count():
    big = [_ax([], 10), _ax([], 3), _ax([], 3)]
    small = [_ax([], 3), _ax([], 3)]
    assert m._best_offset(big, small) == 1


def test_axes_info_collects_bbox_texts_and_counts():
    man = {
        "elements": [
            {"gid": "axes_0", "bbox": [0, 0, 0.5, 1]},
            {"gid": "axes_0.title", "editable": [{"prop": "text", "value": "Left"}]},
            {"gid": "axes_0.line_0", "editable": []},
            {"gid": "axes_1", "bbox": [0.5, 0, 0.5, 1]},
            {"gid": "axes_1.title", "editable": [{"prop": "text", "value": "Right"}]},
            {"gid": "figure", "editable": []},
        ]
    }
    info = m._axes_info(man)
    assert len(info) == 2
    assert info[0]["bbox"] == [0, 0, 0.5, 1]
    assert info[0]["texts"] == {"Left"}
    assert info[0]["n"] == 2
    assert info[1]["texts"] == {"Right"}
    assert info[1]["n"] == 1
