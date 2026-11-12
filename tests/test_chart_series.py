"""系列族扩展测试（SG08 / ADR 0181）：柱/误差/类别轴 + 图例手柄同步。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import chartbind, chartmodel, svgchart
from tavotto.rendercore.svgcheck import parse_svg, strip_matplotlib_doctype
from tavotto.svgcompose import compose_scene

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "support" / "svg_corpus"

ENGINE = {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"}

# bar_error 语料的独立数据（README/CSV 同源）
BAR_CSV = "category,value,error\nA,12.0,1.5\nB,25.0,3.0\nC,18.0,2.0\nD,31.0,2.5\n"


def bar_request(**overrides):
    request = {
        "axes_id": "ax1",
        "rect": [45.0, 13.8, 232.1, 174.4],
        "x": {},
        "y": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 35.0, "scale": "linear"},
        "x_kind": "categorical",
        "categories_col": "category",
        "series": [
            {
                "node": "bars",
                "x_col": "category",
                "y_col": "value",
                "kind": "bar",
                "yerr_col": "error",
                "style": {"facecolor": "#2ca02c", "label": "mean ± sd"},
            }
        ],
        "scene_digest": "b" * 64,
        "nodes": [
            {"id": "bars", "tag": "path"},
            {"id": "err", "tag": "path"},
            {"id": "handle", "tag": "path"},
            {"id": "legend_1", "tag": "g"},
            {"id": "frame", "tag": "rect"},
        ],
        "completeness": "self-contained",
        "dataset_text": BAR_CSV,
    }
    request.update(overrides)
    return request


# ---------------------------------------------------------------------------
# 类别轴与柱/误差
# ---------------------------------------------------------------------------


def test_categorical_axis_builds_index_positions():
    out = chartbind.build_model_and_binding(bar_request(), engine_identity=ENGINE)
    ax = out["model"]["axes"][0]
    assert ax["kind"] == "categorical"
    assert ax["categories"] == ["A", "B", "C", "D"]
    assert ax["xlim"] == [-0.5, 3.5]
    assert out["model"]["series"][0]["kind"] == "bar"
    assert out["model"]["series"][0]["yerr"] == "error"


def test_categorical_axis_refuses_numeric_evidence():
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            bar_request(
                x={
                    "p1_user": 0.0,
                    "p1_value": 0.0,
                    "p2_user": 1.0,
                    "p2_value": 10.0,
                    "scale": "linear",
                }
            ),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_evidence_invalid"


def test_text_column_as_numeric_axis_is_refused():
    request = bar_request(
        x_kind=None,
        categories_col=None,
        x={"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 10.0, "scale": "linear"},
    )
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert ei.value.code in ("bind_evidence_invalid", "bind_column_missing")


def test_errorbar_kind_carries_the_error_definition():
    request = bar_request(
        series=[
            {
                "node": "err",
                "x_col": "category",
                "y_col": "value",
                "kind": "errorbar",
                "yerr_col": "error",
            }
        ]
    )
    out = chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert out["model"]["series"][0]["kind"] == "errorbar"
    assert out["model"]["series"][0]["yerr"] == "error"


# ---------------------------------------------------------------------------
# 脚手架渲染柱/误差
# ---------------------------------------------------------------------------


def scaffold_model():
    out = chartbind.build_model_and_binding(bar_request(), engine_identity=ENGINE)
    return out["model"]


BAR_FRAME = {"view_box": [0.0, 0.0, 288.0, 216.0], "w_pt": 288.0, "h_pt": 216.0, "preserve": None}


def test_bar_scaffold_renders_bars_with_error_bars():
    m = scaffold_model()
    out = svgchart.scaffold_fragment(m, BAR_FRAME)
    frag = out["fragment"].decode("utf-8")
    assert "Rectangle" in frag or "patch_" in frag  # 柱 = 矩形 patch
    # 类别刻度标签是 A–D（matplotlib set_xticklabels）
    assert ">A<" in frag and ">D<" in frag


def test_bar_scaffold_deterministic_and_valid():
    m = scaffold_model()
    a = svgchart.scaffold_fragment(m, BAR_FRAME)["fragment"]
    b = svgchart.scaffold_fragment(m, BAR_FRAME)["fragment"]
    assert a == b
    parse_svg(strip_matplotlib_doctype(a))


def test_bar_scaffold_bar_widths_match_positions():
    m = scaffold_model()
    facts = svgchart.scaffold_fragment(m, BAR_FRAME)["facts"]
    pts = facts["series_points"]["s1"]
    # 四根柱的 x 位置 = 0,1,2,3 → 归一化后均匀分布
    xs = [p["x"] for p in pts]
    assert xs[1] - xs[0] == pytest.approx(xs[2] - xs[1], abs=1e-9)


# ---------------------------------------------------------------------------
# 图例手柄同步
# ---------------------------------------------------------------------------

LEGEND_SCENE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
    b'viewBox="0 0 100 100">'
    b'<g id="axes_1"><g id="curve"><path d="M 0 50 L 100 50" stroke="#1f77b4" '
    b'stroke-width="2" fill="none"/></g></g>'
    b'<g id="legend_1"><g id="handle"><path d="M 5 5 L 25 5" stroke="#1f77b4" '
    b'stroke-width="2" fill="none"/></g>'
    b'<text x="30" y="9">growth</text></g></svg>'
)


def binding_with_handle():
    return {
        "model_rev": 1,
        "scene_digest": "c" * 64,
        "entries": [
            {"object": "series:s1", "node": "curve"},
            {"object": "series:s1", "node": "handle"},
        ],
    }


def series_model_with_color():
    return {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [
            {
                "id": "ax1",
                "rect": [0.0, 0.0, 100.0, 100.0],
                "xlim": [0.0, 1.0],
                "ylim": [0.0, 1.0],
                "xscale": "linear",
                "yscale": "linear",
                "invert_x": False,
                "invert_y": False,
            }
        ],
        "series": [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "curve",
                "x": "x",
                "y": "y",
                "style": {"color": "#d62728"},
            }
        ],
        "dataset": {"columns": {"x": [0.0, 1.0], "y": [0.0, 1.0]}},
        "scene_digest": "c" * 64,
        "data_digest": chartmodel.digest_of({"columns": {"x": [0.0, 1.0], "y": [0.0, 1.0]}}),
        "engine": ENGINE,
    }


def test_legend_handle_follows_series_color():
    frag = svgchart.scaffold_fragment(
        series_model_with_color(),
        {"view_box": [0.0, 0.0, 100.0, 100.0], "w_pt": 100.0, "h_pt": 100.0, "preserve": None},
    )["fragment"]
    composed = compose_scene(
        LEGEND_SCENE, binding_with_handle(), frag, series_model_with_color()
    ).decode("utf-8")
    # 手柄与模型同色（#d62728），图例文字不被动
    assert "#d62728" in composed
    assert ">growth<" in composed
    # 原蓝色不再出现在手柄上
    assert 'id="handle"' in composed
    handle_part = composed.split('id="handle"')[1][:200]
    assert "#1f77b4" not in handle_part
