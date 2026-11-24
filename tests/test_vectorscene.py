"""`engine/vectorscene`（ADR 0208）的单元用例：画布换算与前端同源、求解的错误码、整批不落地。
端到端（worker → 求解 → 校验 → 导出，量成品）在 `tests/test_vectorscene_e2e.py`。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import agentobjects, vectorscene

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "vectorscene_vectors.json").read_text("utf-8"))


def test_vectors_are_fresh():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_vectorscene_vectors.py")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_rect_to_page_matches_vectors():
    for c in VECTORS["cases"]:
        for box, want in zip(VECTORS["boxes"], c["rects"]):
            got = vectorscene.panel_rect_to_page(c["panel"], box)
            assert got == pytest.approx((want["x"], want["y"], want["w"], want["h"]), abs=1e-12)


def test_unrotated_uncropped_panel_is_a_plain_scale():
    panel = {"x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}
    assert vectorscene.panel_point_to_page(panel, 0.25, 0.5) == pytest.approx((30.0, 40.0))


PANEL = {"x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}
MANIFEST = {
    "size_mm": [80, 40],
    "elements": [{"gid": "axes_0", "role": "axes", "bbox": [0.1, 0.1, 0.8, 0.7]}],
}
REQ = {"axes": "axes_0", "x": 1, "y": 2}


def _panels(points=None, **panel_kw):
    return {
        "P1": {
            "panel": {**PANEL, **panel_kw},
            "draw_manifest": MANIFEST,
            "requests": [REQ],
            "data_points": [{"status": "ok", "xy": [0.5, 0.5], "in_view": True}]
            if points is None
            else points,
        }
    }


def _measure(text, size, bold, italic, family):
    return 10.0  # pt；单元用例不起字体引擎


DATA = {"data": {"panel": "P1", **REQ}}


def test_arrow_to_a_data_point_and_text_beside_it():
    items = [
        {
            "kind": "arrow",
            "name": "a",
            "from": {"anchor": DATA, "offset_mm": [10, -5]},
            "to": {"anchor": DATA},
        },
        {
            "kind": "text",
            "name": "t",
            "text": "peak",
            "at": {"anchor": {"item": {"name": "a", "point": "ne"}}},
        },
        {"kind": "shape", "name": "c", "at": {"anchor": DATA}, "size_mm": [2, 2]},
        {
            "kind": "shape",
            "name": "corner",
            "shape": "rect",
            "at": {"anchor": {"panel": {"id": "P1", "point": "nw"}}},
            "size_mm": [3, 3],
            "attach": "nw",
        },
        {
            "kind": "text",
            "name": "lbl",
            "text": "(a)",
            "at": {"anchor": {"element": {"panel": "P1", "gid": "axes_0", "point": "nw"}}},
            "attach": "se",
        },
    ]
    r = vectorscene.solve(items, _panels(), measure=_measure)
    assert r["ok"], r["errors"]
    a, t, c, corner, lbl = r["objects"]
    # 数据点 (0.5, 0.5) → 画布 (50, 40)；箭头终点就在那里
    assert a["x"] + a["end"]["rx"] * a["w"] == pytest.approx(50.0)
    assert a["y"] + a["end"]["ry"] * a["h"] == pytest.approx(40.0)
    assert (c["x"] + c["w"] / 2, c["y"] + c["h"] / 2) == pytest.approx((50.0, 40.0))
    assert (corner["x"], corner["y"]) == pytest.approx((10.0, 20.0))
    # 文字左边中点贴在箭头外接框的右上角
    assert (t["x"], t["y"] + t["h"] / 2) == pytest.approx((60.0, 35.0))
    # 子图标签右下角对到坐标轴框左上角 (18, 24)
    assert (lbl["x"] + lbl["w"], lbl["y"] + lbl["h"]) == pytest.approx((18.0, 24.0))
    assert [x["item"] for x in r["anchors"]] == ["a", "a", "t", "c", "corner", "lbl"]
    # 解出来的对象过得了严格校验
    assert agentobjects.validate_new_objects(r["objects"])["ok"]


@pytest.mark.parametrize(
    ("items", "panels", "code"),
    [
        (
            [
                {
                    "kind": "arrow",
                    "name": "a",
                    "from": {"anchor": DATA},
                    "to": {"anchor": {"data": {"panel": "P2", **REQ}}},
                }
            ],
            None,
            "unknown_ref",
        ),
        (
            [{"kind": "arrow", "name": "a", "from": {"anchor": DATA}, "to": {"anchor": DATA}}],
            _panels(points=[{"status": "unresolvable", "reason": "out_of_domain"}]),
            "anchor_unresolvable",
        ),
        (
            [{"kind": "arrow", "name": "a", "from": {"anchor": DATA}, "to": {"anchor": DATA}}],
            _panels(rotation=90),
            "panel_transformed",
        ),
        (
            [{"kind": "arrow", "name": "a", "from": {"anchor": DATA}, "to": {"anchor": DATA}}],
            _panels(points=[]),
            "anchor_unresolvable",
        ),
        (
            [
                {
                    "kind": "text",
                    "name": "t",
                    "text": "x",
                    "at": {"anchor": {"item": {"name": "later"}}},
                }
            ],
            None,
            "unknown_ref",
        ),
        (
            [
                {"kind": "shape", "name": "s", "at": {"anchor": DATA}, "size_mm": [1, 1]},
                {"kind": "shape", "name": "s", "at": {"anchor": DATA}, "size_mm": [1, 1]},
            ],
            None,
            "duplicate_name",
        ),
        ([{"kind": "spiral", "name": "x"}], None, "bad_item"),
        (
            [
                {
                    "kind": "shape",
                    "name": "s",
                    "at": {"anchor": DATA},
                    "size_mm": [1, 1],
                    "attach": "middle",
                }
            ],
            None,
            "bad_point",
        ),
        (
            [
                {
                    "kind": "arrow",
                    "name": "a",
                    "from": {"anchor": DATA, "offset_mm": [1, float("nan")]},
                    "to": {"anchor": DATA},
                }
            ],
            None,
            "bad_item",
        ),
    ],
)
def test_errors_reject_the_whole_batch(items, panels, code):
    r = vectorscene.solve(items, panels or _panels(), measure=_measure)
    assert not r["ok"] and r["objects"] == [] and r["errors"][0]["code"] == code


def test_required_data_points_groups_and_dedupes():
    items = [
        {"kind": "arrow", "name": "a", "from": {"anchor": DATA}, "to": {"anchor": DATA}},
        {
            "kind": "shape",
            "name": "c",
            "at": {"anchor": {"data": {"panel": "P2", **REQ}}},
            "size_mm": [1, 1],
        },
    ]
    assert vectorscene.required_data_points(items) == {"P1": [REQ], "P2": [REQ]}


def test_error_codes_are_a_closed_set():
    with pytest.raises(AssertionError):
        vectorscene.SceneError("made_up")


def test_unknown_key_rejects_the_batch():
    item = {
        "kind": "arrow",
        "name": "a",
        "from": {"anchor": DATA},
        "to": {"anchor": DATA},
        "dash": "dashed",
    }
    r = vectorscene.solve([item], _panels(), measure=_measure)
    assert not r["ok"] and r["errors"][0] == {
        "code": "bad_item",
        "item": "a",
        "detail": "unknown_key",
    }


def test_element_anchor_needs_ink_geometry():
    manifest = {
        "size_mm": [80, 40],
        "elements": [
            {"gid": "axes_0", "role": "axes", "bbox": [0.1, 0.1, 0.8, 0.7]},
            # 散点：bbox 只是数据范围（不含标记大小），没有 geometry → 不许锚
            {"gid": "axes_0.collections_0", "role": "scatter", "bbox": [0.2, 0.2, 0.1, 0.1]},
            # 有真实轮廓的线：锚轮廓点的范围，不锚撑大过的 bbox
            {
                "gid": "axes_0.lines_0",
                "role": "line",
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "geometry": {"paths": [{"points": [[0.25, 0.5], [0.75, 0.25]]}]},
            },
        ],
    }
    panels = {"P1": {"panel": PANEL, "draw_manifest": manifest, "requests": [], "data_points": []}}

    def at(gid, point):
        return {"anchor": {"element": {"panel": "P1", "gid": gid, "point": point}}}

    r = vectorscene.solve(
        [{"kind": "shape", "name": "s", "at": at("axes_0.collections_0", "n"), "size_mm": [1, 1]}],
        panels,
        measure=_measure,
    )
    assert r["errors"][0] == {
        "code": "anchor_unresolvable",
        "item": "s",
        "detail": "no_ink_geometry",
    }
    r = vectorscene.solve(
        [{"kind": "shape", "name": "s", "at": at("axes_0.lines_0", "ne"), "size_mm": [1, 1]}],
        panels,
        measure=_measure,
    )
    s = r["objects"][0]
    # ne = (0.75, 0.25) → 画布 (10 + 60, 20 + 10)
    assert (s["x"] + s["w"] / 2, s["y"] + s["h"] / 2) == pytest.approx((70.0, 30.0))


def test_statistical_texts_are_listed_for_the_user_even_with_provenance():
    items = [
        {
            "kind": "text",
            "name": "stars",
            "text": "**",
            "provenance": "user",
            "at": {"anchor": DATA},
        },
        {"kind": "text", "name": "plain", "text": "peak", "at": {"anchor": DATA}},
    ]
    r = vectorscene.solve(items, _panels(), measure=_measure)
    assert r["ok"] and r["statistical"] == [{"item": "stars", "provenance": "user"}]
