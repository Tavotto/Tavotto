"""显式建模流程测试（SG06 / ADR 0166）：数据入口 + 校准 + 确认产物。"""

from __future__ import annotations

import pytest

from tavotto.engine import chartbind, chartdata, chartmodel

ENGINE = {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"}
SCENE_DIGEST = "a" * 64
NODES = [
    {"id": "frame", "tag": "rect"},
    {"id": "pt1", "tag": "circle"},
    {"id": "pt2", "tag": "circle"},
    {"id": "curve", "tag": "path"},
    {"id": "label", "tag": "text"},
]

GOOD_CSV = "t,x,y\n0,1.0,10.0\n1,2.0,20.0\n2,3.0,30.0\n"


def axis_evidence(lo_user=0.0, hi_user=1.0):
    """x: 用户 0 处是数据 0、用户 1 处是数据 10；y: 用户 1(顶) 是 100、用户 0 是 0。"""
    return {
        "p1_user": lo_user,
        "p1_value": 0.0,
        "p2_user": hi_user,
        "p2_value": 10.0,
        "scale": "linear",
    }


def y_evidence():
    return {
        "p1_user": 0.0,
        "p1_value": 0.0,
        "p2_user": 1.0,
        "p2_value": 100.0,
        "scale": "linear",
    }


def base_request(**overrides):
    request = {
        "axes_id": "ax1",
        "rect": [60.0, 40.0, 300.0, 220.0],
        "x": axis_evidence(),
        "y": y_evidence(),
        "series": [{"node": "curve", "x_col": "x", "y_col": "y"}],
        "scene_digest": SCENE_DIGEST,
        "nodes": NODES,
        "completeness": "self-contained",
        "dataset_text": GOOD_CSV,
    }
    request.update(overrides)
    return request


# ---------------------------------------------------------------------------
# 数据入口
# ---------------------------------------------------------------------------


def test_parse_table_clean_csv():
    parsed = chartdata.parse_table(GOOD_CSV)
    assert parsed["issues"] == []
    assert parsed["columns"] == {
        "t": [0.0, 1.0, 2.0],
        "x": [1.0, 2.0, 3.0],
        "y": [10.0, 20.0, 30.0],
    }


def test_parse_table_locates_bad_cells():
    parsed = chartdata.parse_table("x,y\n1.0,2.0\noops,3.0\n4.0,\n2024-01-01,5.0")
    codes = {(i["code"], i["row"], i["col"]) for i in parsed["issues"]}
    assert ("data_not_numeric", 3, 1) in codes
    assert ("data_missing", 4, 2) in codes
    assert ("data_not_numeric", 5, 1) in codes  # 日期列被点名，不是静默转换
    assert "x" not in parsed["columns"]  # 带问题的列整体缺席


def test_parse_table_thousands_separator_is_reported_not_guessed():
    # 千分位只在非逗号分隔的表里才可判：逗号表里它是列分隔歧义，走 row_length
    parsed = chartdata.parse_table("x\ty\n1,234.5\t2.0")
    assert any(i["code"] == "data_thousands_separator" for i in parsed["issues"])


def test_parse_table_tabs_and_semicolons():
    assert chartdata.parse_table("x\ty\n1\t2")["columns"] == {"x": [1.0], "y": [2.0]}
    assert chartdata.parse_table("x;y\n1;2")["columns"] == {"x": [1.0], "y": [2.0]}


def test_parse_table_row_length_mismatch_is_reported():
    parsed = chartdata.parse_table("x,y\n1.0,2.0,9.9")
    assert any(i["code"] == "data_row_length" for i in parsed["issues"])


def test_nan_literals_never_reach_columns():
    parsed = chartdata.parse_table("x\nNaN\n1.0")
    assert "x" not in parsed["columns"]


# ---------------------------------------------------------------------------
# 校准证据
# ---------------------------------------------------------------------------


def test_axis_limits_extrapolate_from_evidence():
    lo, hi, scale, inverted = chartbind.axis_limits_from_evidence(axis_evidence())
    assert (lo, hi, scale, inverted) == (0.0, 10.0, "linear", False)


def test_inverted_axis_is_detected_from_evidence():
    evidence = {
        "p1_user": 0.0,
        "p1_value": 100.0,
        "p2_user": 1.0,
        "p2_value": 0.0,
        "scale": "linear",
    }
    lo, hi, scale, inverted = chartbind.axis_limits_from_evidence(evidence)
    # 返回值是「绘图区两端的数据值」：倒轴时 user 0 处是 100、user 1 处是 0
    assert inverted is True and (lo, hi) == (100.0, 0.0)


def test_log_evidence_and_rejection_of_nonpositive():
    evidence = {"p1_user": 0.0, "p1_value": 1.0, "p2_user": 2.0, "p2_value": 100.0, "scale": "log"}
    lo, hi, _, _ = chartbind.axis_limits_from_evidence(evidence)
    assert (lo, hi) == (1.0, 10.0)  # 用户 0→数据 1、用户 2→数据 100；用户 1 是 log 中点 10
    bad = dict(evidence, p1_value=0.0)
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.axis_limits_from_evidence(bad)
    assert ei.value.code == "bind_log_needs_positive"


def test_degenerate_evidence_is_refused():
    evidence = {"p1_user": 0.5, "p1_value": 1.0, "p2_user": 0.5, "p2_value": 2.0, "scale": "linear"}
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.axis_limits_from_evidence(evidence)
    assert ei.value.code == "bind_degenerate_span"


# ---------------------------------------------------------------------------
# 确认产物
# ---------------------------------------------------------------------------


def test_build_produces_valid_model_and_binding():
    out = chartbind.build_model_and_binding(base_request(), engine_identity=ENGINE)
    model, binding = out["model"], out["binding"]
    assert chartmodel.validate_model(model) == model
    assert chartmodel.validate_binding(binding, model) == binding
    assert model["axes"][0]["xlim"] == [0.0, 10.0]
    assert model["series"][0]["node"] == "curve"
    assert binding["entries"] == [{"object": "series:s1", "node": "curve"}]


def test_text_node_cannot_be_bound_as_series():
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            base_request(series=[{"node": "label", "x_col": "x", "y_col": "y"}]),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_node_not_graphic"


def test_unknown_column_is_refused():
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            base_request(series=[{"node": "curve", "x_col": "ghost", "y_col": "y"}]),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_column_missing"


def test_dirty_dataset_blocks_confirmation():
    dirty = "x,y\n1.0,2.0\noops,3.0"
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(base_request(dataset_text=dirty), engine_identity=ENGINE)
    assert ei.value.code == "bind_evidence_invalid"


def test_ragged_columns_block_confirmation():
    ragged = "x,y\n1.0,2.0\n2.0"
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(base_request(dataset_text=ragged), engine_identity=ENGINE)
    assert ei.value.code == "bind_evidence_invalid"


def test_style_is_carried_into_the_model():
    out = chartbind.build_model_and_binding(
        base_request(
            series=[{"node": "curve", "x_col": "x", "y_col": "y", "style": {"color": "#1f77b4"}}]
        ),
        engine_identity=ENGINE,
    )
    assert out["model"]["series"][0]["style"] == {"color": "#1f77b4"}


def test_preview_deviation_reports_deltas():
    out = chartbind.build_model_and_binding(base_request(), engine_identity=ENGINE)
    model = out["model"]
    report = chartbind.preview_deviation(model, {"curve": (0.1, 0.9)})
    (row,) = report
    assert row["series"] == "s1"
    # 首行 (x=1.0, y=10.0)：x 在 [0,10] → 0.1；y 在 [0,100] → 0.1
    assert row["recomputed_first_frac"] == pytest.approx((0.1, 0.1))
    assert row["delta"] == pytest.approx([0.0, -0.8])
