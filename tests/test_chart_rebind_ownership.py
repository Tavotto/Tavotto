"""重绑不把同一个原生位置 gid 的编辑套给另一科学对象。"""

import copy

import pytest

from tavotto.engine import chartbind, chartprofile, svgchart


def chart(*, reverse=False, data="x,a,b\n0,0,1\n1,1,0\n", x_max=1, renamed_columns=False):
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
    return chartbind.build_model_and_binding(
        {
            "axes_id": "main",
            "rect": [10, 10, 80, 80],
            "axes_nodes": ["axis"],
            "scene_digest": "a" * 64,
            "nodes": [{"id": name, "tag": "path"} for name in ("axis", "curve1", "curve2")],
            "x": {**evidence, "p2_value": x_max},
            "y": evidence,
            "series": series,
            "dataset_text": data,
            "completeness": "self-contained",
        },
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
