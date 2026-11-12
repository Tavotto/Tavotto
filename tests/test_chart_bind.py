"""显式建模流程测试（SG06 / ADR-XXXX）：数据入口 + 校准 + 确认产物。"""

from __future__ import annotations

import csv
import io

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


@pytest.mark.parametrize("delimiter", [",", "\t", ";"])
def test_parse_table_restores_fully_quoted_numeric_csv_and_tsv(delimiter):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
    writer.writerows([["x", "y"], ["1.25", "-2"], ["3e2", "4.5"]])
    parsed = chartdata.parse_table(stream.getvalue())
    assert parsed["issues"] == []
    assert parsed["columns"] == {"x": [1.25, 300.0], "y": [-2.0, 4.5]}


@pytest.mark.parametrize(
    ("text", "categories"),
    [
        ("category\tvalue\nA,B,C\t1\nD,E\t2\n", ["A,B,C", "D,E"]),
        ('category,value\n"A;B;C\tD",1\n"E\tF;G",2\n', ["A;B;C\tD", "E\tF;G"]),
    ],
)
def test_parse_table_uses_one_header_delimiter_even_when_data_contains_other_delimiters(
    text, categories
):
    parsed = chartdata.parse_table(text)
    assert parsed["columns"] == {"value": [1.0, 2.0]}
    assert parsed["text_columns"] == {"category": categories}
    assert {issue["code"] for issue in parsed["issues"]} == {"data_not_numeric"}


@pytest.mark.parametrize("delimiter", [",", "\t", ";"])
def test_parse_table_preserves_quoted_headers_and_category_characters(delimiter):
    headers = ["category,\tlabel", "value\nmeasure"]
    categories = ["a,b", "c\td", 'quote " here', "line1\nline2", "CR\r\nLF", "  padded  "]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows([[label, index + 1] for index, label in enumerate(categories)])
    parsed = chartdata.parse_table(stream.getvalue())
    assert parsed["columns"] == {headers[1]: [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]}
    assert parsed["text_columns"] == {headers[0]: categories}
    assert all(issue["column"] == headers[0] for issue in parsed["issues"])


@pytest.mark.parametrize(
    ("text", "row"),
    [
        ('x,y\n1,"unterminated', 2),
        ('x,y\n1,"bad"suffix', 2),
        ('x,"unterminated header\n1,2', 1),
        ('x,"unterminated header\n1', 1),
    ],
)
def test_parse_table_reports_invalid_quote_syntax_without_guessing_a_recovery_record(text, row):
    parsed = chartdata.parse_table(text)
    assert [(issue["code"], issue["row"], issue["col"]) for issue in parsed["issues"]] == [
        ("data_row_length", row, 1)
    ]


def test_parse_table_keeps_physical_cell_addresses_after_blank_and_malformed_rows():
    parsed = chartdata.parse_table("x,y\n1,2\n\n3\nbad,4\n5,\n6,1e309\n")
    assert {(i["code"], i["row"], i["col"]) for i in parsed["issues"]} == {
        ("data_row_length", 4, 2),
        ("data_not_numeric", 5, 1),
        ("data_missing", 6, 2),
        ("data_non_finite", 7, 2),
    }
    assert parsed["columns"] == {}
    assert "y" not in parsed["text_columns"]


def test_parse_table_keeps_cell_addresses_after_a_multiline_category():
    parsed = chartdata.parse_table('label,y\n"line1\nline2",1\nok,bad\n')
    assert parsed["text_columns"]["label"] == ["line1\nline2", "ok"]
    assert {(i["row"], i["col"]) for i in parsed["issues"] if i.get("column") == "y"} == {(4, 2)}


def test_parse_table_reports_quoted_thousands_and_dates_with_the_existing_codes():
    parsed = chartdata.parse_table('x,y\n"1,234.5",2\n2024-01-01,3\n')
    assert [(i["code"], i["row"], i["col"]) for i in parsed["issues"]] == [
        ("data_thousands_separator", 2, 1),
        ("data_not_numeric", 3, 1),
    ]
    assert "date-like" in parsed["issues"][1]["detail"]


def test_parse_table_preserves_numeric_whitespace_and_blank_line_handling():
    parsed = chartdata.parse_table("x\ty\r\n 1 \t 2 \r\n \r\n\t\r\n")
    assert parsed["issues"] == []
    assert parsed["columns"] == {"x": [1.0], "y": [2.0]}


def test_parse_table_keeps_legacy_unquoted_header_whitespace_normalization():
    parsed = chartdata.parse_table(" x , y \n1,2\n")
    assert parsed["issues"] == []
    assert parsed["columns"] == {"x": [1.0], "y": [2.0]}


def test_parse_table_preserves_quoted_header_whitespace_and_quote_positions():
    names = [' "x"\t', " y "]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", quoting=csv.QUOTE_ALL)
    writer.writerows([names, [1, 2]])
    parsed = chartdata.parse_table(stream.getvalue())
    assert parsed["issues"] == []
    assert parsed["columns"] == {names[0]: [1.0], names[1]: [2.0]}


@pytest.mark.parametrize("delimiter", [",", "\t", ";"])
def test_parse_table_unquoted_literal_quote_does_not_hide_the_header_delimiter(delimiter):
    parsed = chartdata.parse_table(f'a"b{delimiter}c\n1{delimiter}2\n')
    assert parsed["issues"] == []
    assert parsed["columns"] == {'a"b': [1.0], "c": [2.0]}


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


def test_categorical_request_omits_numeric_x_and_preserves_restored_tsv_categories():
    categories = ["A\tB", 'C,"D"\nE']
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(["category", "y"])
    writer.writerows(zip(categories, [1, 2]))
    request = base_request(
        x_kind="categorical",
        categories_col="category",
        dataset_text=stream.getvalue(),
        series=[{"node": "curve", "x_col": "category", "y_col": "y", "kind": "bar"}],
    )
    del request["x"]  # 与真实UI的categorical请求union一致，不能靠旧的x={}绕过必填。
    result = chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert result["model"]["axes"][0]["kind"] == "categorical"
    assert result["model"]["axes"][0]["categories"] == categories
    assert result["model"]["axes"][0]["xlim"] == [-0.5, 1.5]
    assert result["model"]["dataset"]["columns"] == {"category": categories, "y": [1.0, 2.0]}
    assert chartmodel.validate_binding(result["binding"], result["model"]) == result["binding"]


@pytest.mark.parametrize("delimiter", [",", "\t"])
def test_binder_references_exact_quoted_multiline_headers(delimiter):
    category_name, value_name = ' \tcategory,"label"\n ', ' value\r\n"measure"\t '
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
    writer.writerows([[category_name, value_name], ["A", 1], ["B", 2]])
    request = base_request(
        x_kind="categorical",
        categories_col=category_name,
        dataset_text=stream.getvalue(),
        series=[{"node": "curve", "x_col": category_name, "y_col": value_name, "kind": "bar"}],
    )
    del request["x"]
    result = chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    model = result["model"]
    assert model["dataset"]["columns"] == {category_name: ["A", "B"], value_name: [1.0, 2.0]}
    assert model["series"][0]["x"] == category_name
    assert model["series"][0]["y"] == value_name
    assert model["axes"][0]["categories"] == ["A", "B"]
    assert chartmodel.validate_model(model) == model


def test_numeric_request_still_requires_x_evidence():
    request = base_request()
    del request["x"]
    with pytest.raises(chartbind.BindError) as exc:
        chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert exc.value.code == "bind_evidence_invalid"
    assert "missing keys ['x']" in exc.value.detail


def test_categorical_request_still_refuses_numeric_x_evidence():
    request = base_request(
        x_kind="categorical",
        categories_col="category",
        dataset_text="category,y\nA,1\nB,2\n",
        series=[{"node": "curve", "x_col": "category", "y_col": "y", "kind": "bar"}],
    )
    with pytest.raises(chartbind.BindError) as exc:
        chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert exc.value.code == "bind_evidence_invalid"
    assert "not numeric evidence" in exc.value.detail


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


# ---------------------------------------------------------------------------
# N4 legend 合同：确认请求 → 绑定合同（节点资格 + series 节点 → id 翻译）
# ---------------------------------------------------------------------------


def legend_request(**overrides):
    legend = [
        {
            "id": "leg1",
            "node": "legendbox",
            "rect": [300.0, 40.0, 60.0, 40.0],
            "entries": [
                {"index": 0, "label": "curve one", "series_node": "curve"},
                {"index": 1, "label": "note"},
            ],
        }
    ]
    request = {"legend": legend, "nodes": [*NODES, {"id": "legendbox", "tag": "g"}]}
    request.update(overrides)
    return request


def test_legend_request_translates_series_nodes_to_ids():
    out = chartbind.build_model_and_binding(
        base_request(**legend_request()),
        engine_identity=ENGINE,
    )
    binding = out["binding"]
    assert binding["legend"] == [
        {
            "id": "leg1",
            "node": "legendbox",
            "rect": [300.0, 40.0, 60.0, 40.0],
            "entries": [
                {"index": 0, "label": "curve one", "series": "s1"},
                {"index": 1, "label": "note"},
            ],
        }
    ]
    # 产物过运行时消费的同一个闸
    assert chartmodel.validate_binding(binding, out["model"]) == binding


def test_without_legend_the_binding_keeps_its_old_shape():
    out = chartbind.build_model_and_binding(base_request(), engine_identity=ENGINE)
    assert "legend" not in out["binding"]


def test_legend_node_must_be_in_the_scene():
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            base_request(
                **legend_request(**{"legend": [dict(legend_request()["legend"][0], node="ghost")]})
            ),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_node_not_graphic"


def test_legend_node_must_be_graphic():
    # text 节点在场景里但不是图形元素（与系列节点同一 graphic 口径）
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            base_request(
                **legend_request(**{"legend": [dict(legend_request()["legend"][0], node="label")]})
            ),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_node_not_graphic"


def test_legend_entry_series_node_must_be_a_bound_series():
    legend = legend_request()["legend"]
    legend[0]["entries"][0] = {"index": 0, "label": "a", "series_node": "pt1"}
    with pytest.raises(chartbind.BindError) as ei:
        chartbind.build_model_and_binding(
            base_request(**legend_request(**{"legend": legend})),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "bind_node_not_graphic"


def test_legend_node_cannot_collide_with_a_bound_series_node():
    legend = legend_request()["legend"]
    legend[0]["node"] = "curve"
    with pytest.raises(chartmodel.ChartModelError) as ei:
        chartbind.build_model_and_binding(
            base_request(**legend_request(**{"legend": legend})),
            engine_identity=ENGINE,
        )
    assert ei.value.code == "chart_duplicate_id"
