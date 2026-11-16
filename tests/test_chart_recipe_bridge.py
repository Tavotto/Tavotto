"""ChartModel ↔ 0178 配方/期望指纹的桥接（#1877 / ADR 0178）。"""

from __future__ import annotations

import copy

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_recipe as recipe_mod,
    chartbind,
    chartmodel,
    chartrecipe,
    figureverify,
    svgchart,
)
from tavotto.engine.chartprofile import NATIVE_PROFILE

FRAME = {"view_box": [0.0, 0.0, 100.0, 100.0], "w_pt": 100.0, "h_pt": 100.0, "preserve": None}
ENGINE = {"matplotlib": "3.11.2", "numpy": "2.1.0", "tavotto": "0.19.2"}


def numeric_axes(**overrides):
    axes = {
        "id": "ax1",
        "rect": [10.0, 10.0, 80.0, 80.0],
        "xlim": [0.0, 2.0],
        "ylim": [0.0, 9.0],
        "xscale": "linear",
        "yscale": "linear",
        "invert_x": False,
        "invert_y": False,
        "kind": "numeric",
    }
    axes.update(overrides)
    return axes


def categorical_axes(categories, **overrides):
    axes = numeric_axes(
        xlim=[-0.5, len(categories) - 0.5],
        ylim=[0.0, 6.0],
        kind="categorical",
        categories=categories,
    )
    axes.update(overrides)
    return axes


def make_model(series, columns, *, axes=None, grids=None):
    dataset = {"columns": columns}
    if grids:
        dataset["grids"] = grids
    return {
        "schema": chartmodel.MODEL_SCHEMA,
        "rev": 1,
        "axes": [axes or numeric_axes()],
        "series": series,
        "dataset": dataset,
        "scene_digest": "a" * 64,
        "data_digest": chartmodel.digest_of(dataset),
        "engine": dict(ENGINE),
    }


def line_model():
    return make_model(
        [
            {
                "id": "s1",
                "kind": "line",
                "axes": "ax1",
                "node": "curve",
                "x": "x",
                "y": "y",
                "style": {},
            }
        ],
        {"x": [0.0, 1.0, 2.0], "y": [1.0, 4.0, 9.0]},
    )


def bar_model(*, yerr=None):
    columns = {"condition": ["a", "b"], "response": [3.0, 5.0]}
    series = {
        "id": "s1",
        "kind": "bar",
        "axes": "ax1",
        "node": "curve",
        "x": "condition",
        "y": "response",
        "style": {},
    }
    if yerr:
        columns["err"] = yerr
        series["yerr"] = "err"
    return make_model([series], columns, axes=categorical_axes(["a", "b"]))


def scatter_model():
    return make_model(
        [
            {
                "id": "s1",
                "kind": "scatter",
                "axes": "ax1",
                "node": "curve",
                "x": "x",
                "y": "y",
                "style": {},
            }
        ],
        {"x": [0.0, 1.0, 2.0], "y": [1.0, 4.0, 9.0]},
    )


def image_model():
    return make_model(
        [{"id": "s1", "kind": "image", "axes": "ax1", "node": "curve", "grid": "g1", "style": {}}],
        {},
        grids={"g1": {"values": [[1.0, 2.0], [3.0, 4.0]]}},
    )


def errorbar_model():
    return make_model(
        [
            {
                "id": "s1",
                "kind": "errorbar",
                "axes": "ax1",
                "node": "curve",
                "x": "x",
                "y": "y",
                "yerr": "err",
                "style": {},
            }
        ],
        {"x": [0.0, 1.0, 2.0], "y": [1.0, 4.0, 9.0], "err": [0.1, 0.2, 0.3]},
    )


def render(model):
    return svgchart.scaffold_fragment(model, FRAME, render_profile=NATIVE_PROFILE)


def profile_with(tmp_path, text):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "d.csv").write_text(text, encoding="utf-8")
    return adf.profile_folder(folder)


@pytest.mark.parametrize(
    ("xscale", "yscale", "scale"),
    [
        ("linear", "linear", "linear"),
        ("log", "linear", "log_x"),
        ("linear", "log", "log_y"),
        ("log", "log", "log_xy"),
    ],
)
def test_scale_map_is_exhaustive(xscale, yscale, scale):
    assert chartrecipe.SCALE_MAP[(xscale, yscale)] == scale


def test_recipe_from_model_maps_supported_shapes():
    bar = chartrecipe.recipe_from_model(bar_model(), table="d.csv", family="comparison", why="说明")
    assert bar is not None
    assert (bar.family, bar.layers[0].mark, bar.x, bar.y) == (
        "comparison",
        "bar",
        "condition",
        ("response",),
    )

    line = chartrecipe.recipe_from_model(line_model(), table="d.csv", family="trend", why="说明")
    assert line is not None and line.layers[0].mark == "line"

    scatter = chartrecipe.recipe_from_model(
        scatter_model(), table="d.csv", family="relationship", why="说明"
    )
    assert scatter is not None and scatter.layers[0].mark == "points"


def test_recipe_from_model_refuses_what_it_cannot_express():
    assert (
        chartrecipe.recipe_from_model(image_model(), table="d.csv", family="trend", why="说明")
        is None
    )
    mixed = line_model()
    mixed["series"].append(
        {
            "id": "s2",
            "kind": "bar",
            "axes": "ax1",
            "node": "curve",
            "x": "x",
            "y": "y",
            "style": {},
        }
    )
    assert chartrecipe.recipe_from_model(mixed, table="d.csv", family="trend", why="说明") is None
    multi_x = line_model()
    multi_x["dataset"]["columns"]["x2"] = [0.0, 1.0, 2.0]
    multi_x["series"].append(
        {
            "id": "s2",
            "kind": "line",
            "axes": "ax1",
            "node": "curve",
            "x": "x2",
            "y": "y",
            "style": {},
        }
    )
    assert chartrecipe.recipe_from_model(multi_x, table="d.csv", family="trend", why="说明") is None


def test_problems_for_model_passes_structure_and_marks_value_rules_unchecked(tmp_path):
    profile = profile_with(tmp_path, "x,y\n0,1\n1,4\n2,9\n")
    out = chartrecipe.problems_for_model(
        profile,
        line_model(),
        table=profile.tables[0].path,
        family="trend",
        why="说明",
        confirmed=recipe_mod.Confirmed(),
    )
    assert out == {"problems": [], "unchecked": ["value_rules"]}


def test_problems_for_model_reports_a_structural_mismatch(tmp_path):
    profile = profile_with(tmp_path, "x,y\n0,1\n1,4\n2,9\n")
    out = chartrecipe.problems_for_model(
        profile,
        line_model(),
        table=profile.tables[0].path,
        family="comparison",
        why="说明",
        confirmed=recipe_mod.Confirmed(),
    )
    assert "column_type_mismatch" in {problem["code"] for problem in out["problems"]}


def test_problems_for_model_reports_a_value_rule(tmp_path):
    profile = profile_with(tmp_path, "condition,response\na,-1\na,2\nb,3\nb,4\n")
    path = tmp_path / "data" / "d.csv"
    out = chartrecipe.problems_for_model(
        profile,
        bar_model(),
        table=profile.tables[0].path,
        family="comparison",
        why="说明",
        confirmed=recipe_mod.Confirmed(),
        values=adf.inspect_data_source(path),
    )
    codes = {problem["code"] for problem in out["problems"]}
    assert "bar_baseline_invalid" in codes


def test_problems_for_model_reports_an_unexpressible_model(tmp_path):
    profile = profile_with(tmp_path, "x,y\n0,1\n1,4\n2,9\n")
    out = chartrecipe.problems_for_model(
        profile,
        image_model(),
        table=profile.tables[0].path,
        family="trend",
        why="说明",
        confirmed=recipe_mod.Confirmed(),
    )
    assert out == {"problems": [], "unchecked": ["model_not_expressible"]}


def test_line_scaffold_verifies_every_expected_series():
    model = line_model()
    scaffold = render(model)
    expected = chartrecipe.chart_fingerprints(model, scaffold["roles"])
    assert [(item["gid"], item["part"]) for item in expected] == [("axes_0.lines_0", "xy")]
    checks = chartrecipe.chart_checks(model, scaffold)
    assert checks and all(check.verdict == figureverify.VERIFIED for check in checks)


def test_bar_scaffold_verifies_the_bars_part():
    model = bar_model()
    scaffold = render(model)
    expected = chartrecipe.chart_fingerprints(model, scaffold["roles"])
    assert [(item["gid"], item["part"]) for item in expected] == [("axes_0.barseries_0", "bars")]
    checks = chartrecipe.chart_checks(model, scaffold)
    assert checks and all(check.verdict == figureverify.VERIFIED for check in checks)


def test_bar_with_errorbar_uses_the_primary_container_gid():
    model = bar_model(yerr=[0.5, 0.5])
    scaffold = render(model)
    expected = chartrecipe.chart_fingerprints(model, scaffold["roles"])
    # bar+yerr 的原生序号按 ax.containers：errorbar 先登记，柱容器是 barseries_1。
    assert expected[0]["gid"] == "axes_0.barseries_1"
    checks = figureverify.data_matches_expected(scaffold["manifest"], expected)
    assert all(check.verdict == figureverify.VERIFIED for check in checks)


def test_image_scaffold_verifies_the_array_part():
    model = image_model()
    scaffold = render(model)
    expected = chartrecipe.chart_fingerprints(model, scaffold["roles"])
    assert [(item["gid"], item["part"]) for item in expected] == [("axes_0.images_0", "array")]
    checks = figureverify.data_matches_expected(scaffold["manifest"], expected)
    assert all(check.verdict == figureverify.VERIFIED for check in checks)


def test_errorbar_is_not_claimed_as_verified():
    model = errorbar_model()
    scaffold = render(model)
    assert chartrecipe.chart_fingerprints(model, scaffold["roles"]) == []
    coverage = chartrecipe.chart_coverage(model)
    assert coverage["layers"][0]["checked"] is False
    assert coverage["layers"][0]["reason"] == "errorbar_has_no_label"
    assert coverage["any_checked"] is False


def test_changing_the_data_column_fails_the_check():
    model = line_model()
    scaffold = render(model)
    changed = copy.deepcopy(model)
    changed["dataset"]["columns"]["y"] = [9.0, 4.0, 1.0]
    changed["data_digest"] = chartmodel.digest_of(changed["dataset"])
    expected = chartrecipe.chart_fingerprints(changed, scaffold["roles"])
    checks = figureverify.data_matches_expected(scaffold["manifest"], expected)
    assert [(check.verdict, check.reason_code) for check in checks] == [("failed", "data_mismatch")]


def bind_request():
    return {
        "axes_id": "ax1",
        "rect": [60.0, 40.0, 300.0, 220.0],
        "x": {"p1_user": 0.0, "p1_value": 0.0, "p2_user": 1.0, "p2_value": 10.0, "scale": "linear"},
        "y": {
            "p1_user": 0.0,
            "p1_value": 0.0,
            "p2_user": 1.0,
            "p2_value": 100.0,
            "scale": "linear",
        },
        "series": [{"node": "curve", "x_col": "x", "y_col": "y"}],
        "scene_digest": "a" * 64,
        "nodes": [{"id": "frame", "tag": "rect"}, {"id": "curve", "tag": "path"}],
        "completeness": "self-contained",
        "dataset_text": "t,x,y\n0,1.0,10.0\n1,2.0,20.0\n2,3.0,30.0\n",
    }


def test_bind_recipe_context_marks_value_rules_unchecked(tmp_path):
    profile = profile_with(tmp_path, "t,x,y\n0,1.0,10.0\n1,2.0,20.0\n2,3.0,30.0\n")
    request = bind_request()
    request["recipe_context"] = {
        "profile": profile,
        "table": profile.tables[0].path,
        "family": "trend",
        "why": "说明",
    }
    out = chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert out["recipe_unchecked"] == ["value_rules"]


def test_bind_recipe_context_rejects_a_structural_mismatch(tmp_path):
    profile = profile_with(tmp_path, "t,x,y\n0,1.0,10.0\n1,2.0,20.0\n2,3.0,30.0\n")
    request = bind_request()
    request["recipe_context"] = {
        "profile": profile,
        "table": profile.tables[0].path,
        "family": "comparison",
        "why": "说明",
    }
    with pytest.raises(chartbind.BindError) as exc:
        chartbind.build_model_and_binding(request, engine_identity=ENGINE)
    assert exc.value.code == "bind_evidence_invalid"
    assert "column_type_mismatch" in exc.value.detail
