"""自由路径质量核对（ADR 0208 §七之二 第 3 条；6.A，`engine/pathquality.py`）：黄金用例 + 导入夹具。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tavotto.engine import pathquality as pq
from tavotto.rendercore import svgimport

FIX = Path(__file__).resolve().parent / "fixtures" / "svgimport"
K = 0.5522847498  # 四分之一圆的手柄系数


def obj(subs, w=100.0, h=100.0, **kw) -> dict:
    return {
        "type": "path",
        "x": 10.0,
        "y": 20.0,
        "w": w,
        "h": h,
        "subpaths": subs,
        "fill": "",
        **kw,
    }


def circle4() -> dict:
    """四个锚点落在上下左右极值点的圆（半径 50 mm，框 100×100），手柄共线、平滑。"""
    c, r, k = 0.5, 0.5, K * 0.5
    return {
        "closed": True,
        "nodes": [
            {"p": [c + r, c], "in": [c + r, c - k], "out": [c + r, c + k], "smooth": True},
            {"p": [c, c + r], "in": [c + k, c + r], "out": [c - k, c + r], "smooth": True},
            {"p": [c - r, c], "in": [c - r, c + k], "out": [c - r, c - k], "smooth": True},
            {"p": [c, c - r], "in": [c - k, c - r], "out": [c + k, c - r], "smooth": True},
        ],
    }


def codes(res: dict) -> list[str]:
    return [i["code"] for i in res["issues"]]


def test_clean_circle_and_polyline():
    assert pq.check_path(obj([circle4()]))["verdict"] == "clean"
    tri = {"closed": True, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [0.5, 1]}]}
    assert pq.check_path(obj([tri], fill="#ff0000"))["verdict"] == "clean"
    assert (
        pq.check_path(obj([{"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 1]}]}]))["verdict"]
        == "clean"
    )


def test_does_not_mutate_input():
    o = obj([circle4()])
    before = copy.deepcopy(o)
    pq.check_path(o)
    assert o == before


@pytest.mark.parametrize(
    "o",
    [
        {"type": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
        obj([circle4()], glyph={"kind": "membrane", "params": {}, "version": 1}),
        {"type": "path", "x": 0, "y": 0, "w": 1, "h": 1},
        "x",
    ],
)
def test_skipped(o):
    assert pq.check_path(o)["verdict"] == "skipped"


def test_two_anchor_semicircles_miss_extrema():
    """两个锚点的圆：每段在上下方向凸出半径那么多 → 锚点没落在极值点，fail。"""
    c, r, k = 0.5, 0.5, K * 0.5
    two = {
        "closed": True,
        "nodes": [
            {"p": [c + r, c], "in": [c + r, c - 2 * k], "out": [c + r, c + 2 * k]},
            {"p": [c - r, c], "in": [c - r, c + 2 * k], "out": [c - r, c - 2 * k]},
        ],
    }
    # 两个半圆的手柄长度 2k 并不精确，但内部极值 ≈ 半径 × 0.75 以上，远超 1 mm
    res = pq.check_path(obj([two]))
    assert res["verdict"] == "fail"
    assert set(codes(res)) == {"extremum_off_anchor"}
    assert {i["anchor"] for i in res["issues"]} == {0, 1}
    assert all(i["value"] > pq.EXTREMUM_FAIL_MM for i in res["issues"])


def test_small_extremum_overshoot_is_acceptable():
    # 一段近乎水平的曲线，中间多凸出 ~0.3 mm（框高 100 mm → 比例 0.003）
    sub = {
        "closed": False,
        "nodes": [
            {"p": [0, 0.5], "out": [0.3, 0.5 - 0.004]},
            {"p": [1, 0.5], "in": [0.7, 0.5 - 0.004]},
        ],
    }
    res = pq.check_path(obj([sub]))
    assert res["verdict"] == "acceptable"
    assert [i["severity"] for i in res["issues"]] == ["warn"]


def test_smooth_handles_not_collinear():
    o = obj([circle4()])
    o["subpaths"][0]["nodes"][0]["out"] = [1.0, 0.5 + 0.27]  # 与 in（正上方）夹角 0 → 先保持共线
    assert pq.check_path(o)["verdict"] == "clean"
    # 出手柄略偏：偏 ~2.5°（warn）
    o["subpaths"][0]["nodes"][0]["out"] = [1.0 + 0.012, 0.5 + 0.27]
    res = pq.check_path(o)
    assert codes(res) == ["smooth_handles_not_collinear"]
    assert res["verdict"] == "acceptable"
    # 明显折了 → fail
    o["subpaths"][0]["nodes"][0]["out"] = [1.0 + 0.1, 0.5 + 0.27]
    assert pq.check_path(o)["verdict"] == "fail"


def test_non_smooth_node_may_have_bent_handles():
    o = obj([circle4()])
    n = o["subpaths"][0]["nodes"][0]
    n["smooth"] = False
    n["out"] = [1.1, 0.77]
    assert "smooth_handles_not_collinear" not in codes(pq.check_path(o))


def test_zero_length_and_coincident_anchors():
    zero = {
        "closed": False,
        "nodes": [{"p": [0, 0]}, {"p": [0.5, 0.5]}, {"p": [0.5, 0.5]}, {"p": [1, 1]}],
    }
    res = pq.check_path(obj([zero]))
    assert res["verdict"] == "fail" and codes(res) == ["zero_length_segment"]
    assert (res["issues"][0]["subpath"], res["issues"][0]["anchor"]) == (0, 1)
    # 锚点重合、手柄不同：成环的重合锚点
    loop = {
        "closed": False,
        "nodes": [
            {"p": [0, 0]},
            {"p": [0.5, 0.5], "out": [0.7, 0.9]},
            {"p": [0.5, 0.5], "in": [0.9, 0.7]},
            {"p": [1, 1]},
        ],
    }
    assert "coincident_anchors" in codes(pq.check_path(obj([loop])))


def test_closed_duplicate_last_anchor():
    sq = {
        "closed": True,
        "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 1]}, {"p": [0, 0]}],
    }
    res = pq.check_path(obj([sq]))
    assert res["verdict"] == "fail"
    assert [(i["code"], i["anchor"]) for i in res["issues"]] == [("zero_length_segment", 4)]


def test_not_really_closed_and_filled_open():
    sq = {"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}, {"p": [0, 0]}]}
    res = pq.check_path(obj([sq]))
    assert codes(res) == ["not_really_closed"] and res["verdict"] == "fail"
    gap = {"closed": False, "nodes": [{"p": [0, 0]}, {"p": [1, 0]}, {"p": [1, 1]}]}
    assert pq.check_path(obj([gap]))["verdict"] == "clean"
    res = pq.check_path(obj([gap], fill="#00ff00"))
    assert codes(res) == ["filled_but_open"] and res["verdict"] == "acceptable"


def test_too_few_anchors():
    res = pq.check_path(obj([{"closed": False, "nodes": [{"p": [0, 0]}]}]))
    assert codes(res) == ["too_few_anchors"] and res["verdict"] == "fail"


def test_too_many_anchors():
    # 一条几乎直的折线，60 个锚点、没有拐角 → 上限 8
    n = 60
    sub = {
        "closed": False,
        "nodes": [{"p": [i / (n - 1), 0.5 + 0.001 * (i % 2)]} for i in range(n)],
    }
    res = pq.check_path(obj([sub]))
    assert res["verdict"] == "fail" and "too_many_anchors" in codes(res)
    # 同样多的锚点在一个确实有很多拐角的锯齿上：上限随拐角数放宽
    saw = {"closed": False, "nodes": [{"p": [i / 29, 0.0 if i % 2 else 1.0]} for i in range(30)]}
    assert (
        pq.corner_count(
            {
                "closed": False,
                "nodes": [
                    {
                        "p": (i * 3.0, 0.0 if i % 2 else 9.0),
                        "in": (i * 3.0, 0.0 if i % 2 else 9.0),
                        "out": (i * 3.0, 0.0 if i % 2 else 9.0),
                        "smooth": False,
                    }
                    for i in range(30)
                ],
            }
        )
        == 28
    )
    assert "too_many_anchors" not in codes(pq.check_path(obj([saw])))


def test_anchor_limit_formula():
    assert pq.anchor_limit(0) == 8 and pq.anchor_limit(4) == 16


def test_per_subpath_indices():
    bad = {"closed": False, "nodes": [{"p": [0, 0]}, {"p": [0, 0]}, {"p": [1, 1]}]}
    res = pq.check_path(obj([circle4(), bad]))
    assert [(i["subpath"], i["anchor"]) for i in res["issues"]] == [(1, 0)]
    assert "第 2 个子路径" in res["issues"][0]["message"]


def test_rotation_does_not_change_verdict():
    assert pq.check_path(obj([circle4()], rotationDeg=33))["verdict"] == "clean"


def _imported_paths(name: str) -> list[dict]:
    res = svgimport.import_svg((FIX / name).read_text())
    objs = res["objects"] if isinstance(res, dict) else res
    return [o for o in objs if o.get("type") == "path"]


def test_svgimport_illustrator_fixture_is_clean():
    paths = _imported_paths("illustrator.svg")
    assert paths and all(pq.check_path(o)["verdict"] == "clean" for o in paths)


def test_svgimport_inkscape_fixture_flags_two_anchor_arcs():
    paths = _imported_paths("inkscape.svg")
    verdicts = [pq.check_path(o)["verdict"] for o in paths]
    assert verdicts == ["fail", "fail", "clean", "fail"]
    assert all(set(codes(pq.check_path(o))) <= {"extremum_off_anchor"} for o in paths)
