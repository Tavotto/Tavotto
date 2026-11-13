"""色阶/网格绑定测试（SG09 / ADR-XXXX）：网格数据 → imshow 片段 → 组合。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import chartbind, chartdata, chartmodel, svgchart
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
from tavotto.svgcompose import compose_scene

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "support" / "svg_corpus"

ENGINE = {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"}

GRID_TEXT = "0.1 0.3 0.6\n0.9 0.4 0.2\n0.5 0.8 0.1\n"


def heatmap_request(**overrides):
    request = {
        "axes_id": "ax1",
        "rect": [24.28, 24.32, 231.14, 165.1],
        "x": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 3.0, "scale": "linear"},
        "y": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 3.0, "scale": "linear"},
        "series": [
            {
                "node": "imagebdc8d2b4da",
                "kind": "image",
                "style": {"cmap": "viridis", "vmin": 0.0, "vmax": 1.0},
            }
        ],
        "scene_digest": "d" * 64,
        "nodes": [{"id": "imagebdc8d2b4da", "tag": "image"}, {"id": "cbar", "tag": "g"}],
        "completeness": "self-contained",
        "dataset_text": "unused\n",
        "grid_text": GRID_TEXT,
    }
    request.update(overrides)
    return request


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------


def test_parse_grid_rectangular_and_typed():
    parsed = chartdata.parse_grid(GRID_TEXT)
    assert parsed["issues"] == []
    assert parsed["values"] == [[0.1, 0.3, 0.6], [0.9, 0.4, 0.2], [0.5, 0.8, 0.1]]


def test_parse_grid_locates_bad_cells_and_ragged_rows():
    parsed = chartdata.parse_grid("0.1 x\n0.3 0.4 9\n")
    codes = {(i["code"], i["row"]) for i in parsed["issues"]}
    assert ("data_not_numeric", 1) in codes
    assert ("data_row_length", 2) in codes


# ---------------------------------------------------------------------------
# 确认产物
# ---------------------------------------------------------------------------


def test_heatmap_binding_builds_grid_model():
    out = chartbind.build_model_and_binding(heatmap_request(), engine_identity=ENGINE)
    model = out["model"]
    assert chartmodel.validate_model(model) == model
    assert model["series"][0]["kind"] == "image"
    assert model["series"][0]["grid"] == "g1"
    assert model["series"][0]["style"]["cmap"] == "viridis"
    assert model["dataset"]["grids"]["g1"]["values"][0][0] == 0.1
    # 摘要覆盖网格：改一格摘要就变
    tampered = chartbind.build_model_and_binding(
        heatmap_request(grid_text=GRID_TEXT.replace("0.6", "0.7")), engine_identity=ENGINE
    )
    assert model["data_digest"] != tampered["model"]["data_digest"]


def test_image_series_requires_a_grid():
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(heatmap_request(grid_text=""), engine_identity=ENGINE)
    assert ei.value.code == "bind_evidence_invalid"


# ---------------------------------------------------------------------------
# 脚手架与组合
# ---------------------------------------------------------------------------

HEAT_FRAME = {"view_box": [0.0, 0.0, 324.0, 216.0], "w_pt": 324.0, "h_pt": 216.0, "preserve": None}


def test_heatmap_scaffold_renders_image_and_colorbar():
    out = chartbind.build_model_and_binding(heatmap_request(), engine_identity=ENGINE)
    frag = svgchart.scaffold_fragment(out["model"], HEAT_FRAME)["fragment"]
    root = parse_svg(strip_matplotlib_doctype(frag))
    images = [e for e in root.iter() if e.tag.endswith("image")]
    assert images, "imshow 产物是内嵌位图 image 元素"
    # 角色标记：主轴 tv-axes-<id> 与新色条 tv-colorbar-<sid>（R02 组合合同）
    role_ids = {
        e.attrib.get("id", "") for e in root.iter() if (e.attrib.get("id") or "").startswith("tv-")
    }
    assert any(i.startswith("tv-axes-") for i in role_ids), role_ids
    assert any(i.startswith("tv-colorbar-") for i in role_ids), role_ids


def test_heatmap_scaffold_honors_vmin_vmax():
    out = chartbind.build_model_and_binding(heatmap_request(), engine_identity=ENGINE)
    a = svgchart.scaffold_fragment(out["model"], HEAT_FRAME)["fragment"]
    tighter = chartbind.build_model_and_binding(
        heatmap_request(
            series=[
                {
                    "node": "imagebdc8d2b4da",
                    "kind": "image",
                    "style": {"cmap": "viridis", "vmin": 0.2, "vmax": 0.8},
                }
            ]
        ),
        engine_identity=ENGINE,
    )
    b = svgchart.scaffold_fragment(tighter["model"], HEAT_FRAME)["fragment"]
    assert a != b  # 色阶范围变化必须改变产物（联动，不是只改文字）


def test_heatmap_compose_replaces_image_node():
    out = chartbind.build_model_and_binding(heatmap_request(), engine_identity=ENGINE)
    frag = svgchart.scaffold_fragment(out["model"], HEAT_FRAME)["fragment"]
    scene = (CORPUS / "heatmap_colorbar.svg").read_bytes()
    binding = {
        "model_rev": 1,
        "scene_digest": "d" * 64,
        "entries": [{"object": "series:s1", "node": "imagebdc8d2b4da"}],
    }
    composed = compose_scene(scene, binding, frag, out["model"])
    root = parse_svg(strip_matplotlib_doctype(composed))
    # 产物过 svgcheck（引用闭合），且包含 image（组合后的网格）
    images = [e for e in root.iter() if e.tag.endswith("image")]
    assert images
