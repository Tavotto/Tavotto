"""重绑不把同一个原生位置 gid 的编辑套给另一科学对象。"""

import copy

import pytest

from tavotto.engine import chartbind, chartprofile, svgchart


def chart(
    *, reverse=False, data="x,a,b\n0,0,1\n1,1,0\n", x_max=1, renamed_columns=False, legend=False
):
    series = [
        {"node": "curve1", "x_col": "x", "y_col": "a", "kind": "line"},
        {"node": "curve2", "x_col": "x", "y_col": "b", "kind": "line"},
    ]
    if reverse:
        series.reverse()
    if renamed_columns:
        data = data.replace("x,a,b", "t,c,d")
        for item in series:
            item["x_col"] = "t"
            item["y_col"] = {"a": "c", "b": "d"}[item["y_col"]]
    evidence = {"p1_user": 0, "p1_value": 0, "p2_user": 1, "p2_value": 1, "scale": "linear"}
    request = {
        "axes_id": "main",
        "rect": [10, 10, 80, 80],
        "axes_nodes": ["axis"],
        "scene_digest": "a" * 64,
        "nodes": [{"id": name, "tag": "path"} for name in ("axis", "curve1", "curve2")]
        + ([{"id": "legendbox", "tag": "g"}] if legend else []),
        "x": {**evidence, "p2_value": x_max},
        "y": evidence,
        "series": series,
        "dataset_text": data,
        "completeness": "self-contained",
    }
    if legend:
        request["legend"] = [
            {
                "id": "leg1",
                "node": "legendbox",
                "rect": [70, 10, 20, 12],
                "entries": [
                    {"index": 0, "label": "a", "series_node": "curve1"},
                    {"index": 1, "label": "b", "series_node": "curve2"},
                ],
            }
        ]
    return chartbind.build_model_and_binding(
        request,
        engine_identity={"matplotlib": "test", "numpy": "test", "tavotto": "test"},
    )


EDITS = [{"gid": "axes_0.lines_0", "prop": "color", "value": "#ff0000"}]


@pytest.mark.parametrize(
    "change", [{"data": "x,a,b\n0,3,4\n1,5,6\n"}, {"x_max": 20}, {"renamed_columns": True}]
)
def test_data_range_and_column_changes_keep_the_same_scientific_owners(change):
    previous, current = chart(), chart(**change)
    before = copy.deepcopy([previous, current, EDITS])
    chartprofile.validate_rebound_edit_ownership(previous, current, EDITS)
    assert [previous, current, EDITS] == before


def test_reordered_series_reuse_a_native_gid_but_cannot_inherit_its_old_edits():
    previous, current = chart(), chart(reverse=True)
    assert previous["model"]["series"][0]["id"] == current["model"]["series"][0]["id"]
    assert previous["model"]["series"][0]["node"] == "curve1"
    assert current["model"]["series"][0]["node"] == "curve2"
    # The actual native setter accepts this positional gid, proving that worker
    # ownership alone does not establish continuity with the previous chart.
    rendered = svgchart.scaffold_fragment(
        current["model"],
        {"view_box": [0, 0, 100, 100], "w_pt": 100, "h_pt": 100},
        render_profile=chartprofile.NATIVE_PROFILE,
        patches=EDITS,
    )
    assert rendered["unrestored"] == 0 and not rendered["warnings"]
    with pytest.raises(chartprofile.ChartProfileError, match="chart_binding_stale"):
        chartprofile.validate_rebound_edit_ownership(previous, current, EDITS)


@pytest.mark.parametrize("previous", [None, {}, {"model": {}}])
def test_missing_previous_model_does_not_grant_old_edits_a_new_owner(previous):
    with pytest.raises(chartprofile.ChartProfileError, match="chart_binding_stale"):
        chartprofile.validate_rebound_edit_ownership(previous, chart(), EDITS)


def test_rebinding_without_existing_edits_needs_no_migration_proof():
    chartprofile.validate_rebound_edit_ownership(None, chart(reverse=True), [])


# ---------------------------------------------------------------------------
# N4 legend 归属（B12）：图例指认变了 = 所有权变了，旧 edits 不静默迁移
# ---------------------------------------------------------------------------


def legend_binding_of(result, follow_second=False):
    binding = copy.deepcopy(result["binding"])
    entries = binding["legend"][0]["entries"]
    entries[1]["series"] = "s1" if follow_second else "s2"
    return binding


def test_same_legend_attribution_keeps_edits():
    previous, current = chart(legend=True), chart(legend=True)
    chartprofile.validate_rebound_edit_ownership(previous, current, EDITS)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b.pop("legend"), id="legend-removed"),
        pytest.param(
            lambda b: b["legend"][0].__setitem__("node", "other"), id="region-node-changed"
        ),
        pytest.param(
            lambda b: b["legend"][0]["entries"][1].__setitem__("series", "s1"),
            id="entry-follows-another-series",
        ),
        pytest.param(lambda b: b["legend"][0]["entries"].pop(1), id="entry-removed"),
    ],
)
def test_legend_attribution_changes_refuse_old_edits(mutate):
    previous, current = chart(legend=True), chart(legend=True)
    mutate(current["binding"])
    with pytest.raises(chartprofile.ChartProfileError, match="chart_binding_stale"):
        chartprofile.validate_rebound_edit_ownership(previous, current, EDITS)


def test_legend_label_change_is_not_an_ownership_change():
    # 条目文字是场景事实的显示文本，不是归属；handle_* edits 的落点不变。
    previous, current = chart(legend=True), chart(legend=True)
    current["binding"]["legend"][0]["entries"][0]["label"] = "renamed"
    current["binding"]["legend"][0].pop("rect")
    current["binding"]["legend"][0]["rect"] = [70, 10, 20, 13]
    chartprofile.validate_rebound_edit_ownership(previous, current, EDITS)
