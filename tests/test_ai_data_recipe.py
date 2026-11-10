"""TAF 数据出图 v2 的「画法配方」层（ADR 0178）：配方结构的严格解析、`FAMILIES` 表自身的一致性、资格判定、排序护栏与选图准则。

判据的主语：配方 / 资格判定量的是**表**（`FAMILIES` / `MARK_RULES` / `REJECTIONS`），准则文字是从表渲染出来的；
所以这里钉两件事——每条规则在行为上拒得对（对着真 CSV 摘要），以及准则里的每个码、族、图元都能在表里找到、表里的每个码都在准则里有句子。"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_recipe as R, ai_prompt_pack

ROOT = Path(__file__).resolve().parent.parent
NONE = R.Confirmed()


# --------------------------------------------------------------------------- 夹具：真 CSV → 真摘要
def _write(tmp: Path, name: str, rows: list[list]) -> None:
    (tmp / name).write_text(
        "\n".join(",".join(str(c) for c in r) for r in rows) + "\n", encoding="utf-8"
    )


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    # long.csv：3 组 × 6 只，同一批小鼠（mouse）在每组都测了
    _write(
        tmp_path,
        "long.csv",
        [["condition", "mouse", "response"]]
        + [
            [c, f"m{i}", 10 * k + i + 0.5]
            for k, c in enumerate(["ctrl", "drugA", "drugB"])
            for i in range(1, 7)
        ],
    )
    # small.csv：每组只有 4 个值
    _write(
        tmp_path,
        "small.csv",
        [["condition", "response"]]
        + [[c, v] for c in ("ctrl", "drug") for v in (1.0, 2.0, 3.0, 4.0)],
    )
    # neg.csv：含负数
    _write(
        tmp_path,
        "neg.csv",
        [["condition", "response"]]
        + [[c, v] for c in ("ctrl", "drug") for v in (-1.0, 2.0, 3.0, 4.0, 5.0, 6.0)],
    )
    # wide.csv：时间 + 两个量纲不同的列（列名后缀是单位）
    _write(
        tmp_path,
        "wide.csv",
        [["time_s", "temp_C", "pressure_kPa", "flow"]]
        + [[t, 20 + t, 100 + t, 1.5 * t] for t in range(1, 9)],
    )
    # rep.csv：同一个时间点有 3 次重复（subject 是编号）
    _write(
        tmp_path,
        "rep.csv",
        [["time", "subject", "y"]]
        + [[t, f"s{j}", t * 2 + j] for t in range(1, 5) for j in range(1, 4)],
    )
    # paired.csv：5 只小鼠，pre / post 各一次
    _write(
        tmp_path,
        "paired.csv",
        [["phase", "mouse", "value"]]
        + [
            [ph, f"m{i}", i * (1 if ph == "pre" else 2)]
            for ph in ("pre", "post")
            for i in range(1, 6)
        ],
    )
    # matrix.csv：对称矩阵
    _write(
        tmp_path,
        "matrix.csv",
        [["name", "a", "b", "c"], ["a", 1, 0.5, 0.2], ["b", 0.5, 1, 0.3], ["c", 0.2, 0.3, 1]],
    )
    # comp.csv / comp_bad.csv：宽格式构成
    _write(
        tmp_path,
        "comp.csv",
        [["site", "p1", "p2", "p3"], ["A", 1, 2, 3], ["B", 2, 2, 2], ["C", 5, 1, 1]],
    )
    _write(
        tmp_path,
        "comp_bad.csv",
        [["site", "p1", "p2", "p3"], ["A", -1, 2, 3], ["B", 0, 0, 0], ["C", 5, 1, 1]],
    )
    # interval.csv / interval_bad.csv
    _write(
        tmp_path,
        "interval.csv",
        [
            ["gene", "est", "lo", "hi"],
            ["g1", 1, 0.5, 1.5],
            ["g2", 2, 1.0, 3.0],
            ["g3", 0.2, 0.1, 0.4],
        ],
    )
    _write(
        tmp_path,
        "interval_bad.csv",
        [["gene", "est", "lo", "hi"], ["g1", 1, 1.5, 0.5], ["g2", 2, 1.0, 3.0]],
    )
    # dose.csv：含 0 剂量 / dose_ok.csv
    _write(tmp_path, "dose.csv", [["dose", "resp"], [0, 1], [1, 2], [10, 4], [100, 8], [1000, 9]])
    _write(tmp_path, "dose_ok.csv", [["dose", "resp"], [1, 1], [10, 2], [100, 4], [1000, 8]])
    # many.csv：40 只个体 × 2 个时间点
    _write(
        tmp_path,
        "many.csv",
        [["t", "id", "y"]] + [[t, f"s{i}", i + t] for t in (1, 2, 3) for i in range(40)],
    )
    # 12 个分面取值 / 多分组
    _write(
        tmp_path,
        "levels.csv",
        [["site", "arm", "response"]] + [[f"s{i % 7}", f"a{i % 9}", i] for i in range(63)],
    )
    return tmp_path


@pytest.fixture
def profile(folder: Path) -> adf.FolderProfile:
    return adf.profile_folder(folder)


def vals(folder: Path, name: str) -> adf.DataSummary:
    return adf.inspect_data_source(folder / name)


def entry(profile: adf.FolderProfile, name: str) -> adf.TableEntry:
    (e,) = [t for t in profile.tables if t.path == name]
    return e


def rec(table: str, family: str, layers, y, why="一句话", **kw) -> R.FigureRecipe:
    ls = [
        {"mark": m}
        if isinstance(m, str)
        else {"mark": m[0], "summary": m[1], **({"role": m[2]} if len(m) > 2 else {})}
        for m in layers
    ]
    return R.FigureRecipe.from_dict(
        {"table": table, "family": family, "layers": ls, "y": list(y), "why": why, **kw}
    )


def codes(problems) -> set[str]:
    return {p.code for p in problems}


def check(profile, folder, r, *, confirmed=NONE, with_values=True):
    v = vals(folder, r.table) if with_values else None
    return R.validate_recipe(profile, r, confirmed=confirmed, values=v)


GOOD = dict(x="condition", group=None)


def good_comparison(**kw):
    return rec(
        "long.csv",
        "comparison",
        ["jitter", ("hline_summary", "mean_sd", "overlay")],
        ["response"],
        x="condition",
        **kw,
    )


# --------------------------------------------------------------------------- 配方：严格解析
def test_recipe_roundtrip():
    r = good_comparison()
    assert R.FigureRecipe.from_dict(r.to_dict()) == r
    assert json.loads(json.dumps(r.to_dict())) == r.to_dict()


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda d: d.update(bogus=1), "recipe_unknown_key"),
        (lambda d: d["layers"][0].update(color="red"), "recipe_unknown_key"),
        (lambda d: d["layers"][0].update(mark="pie"), "recipe_unknown_value"),
        (lambda d: d["layers"][0].update(summary="mean_se"), "recipe_unknown_value"),
        (lambda d: d["layers"][0].update(role="top"), "recipe_unknown_value"),
        (lambda d: d.update(family="sankey"), "recipe_unknown_value"),
        (lambda d: d.update(coord="3d"), "recipe_unknown_value"),
        (lambda d: d.update(order="random"), "recipe_unknown_value"),
        (lambda d: d.update(scale="log"), "recipe_unknown_value"),
        (lambda d: d.update(layers=[]), "recipe_layer_count"),
        (lambda d: d.update(layers=[{"mark": "points"}] * 5), "recipe_layer_count"),
        (lambda d: d.update(why=""), "recipe_why_invalid"),
        (lambda d: d.update(why="x" * (R.MAX_WHY + 1)), "recipe_why_invalid"),
        (lambda d: d.update(why="两行\n内容"), "recipe_why_invalid"),
        (lambda d: d.update(paired=1), "recipe_malformed"),
        (lambda d: d.update(y="response"), "recipe_malformed"),
        (lambda d: d.update(y=[]), "recipe_malformed"),
        (lambda d: d.update(x=5), "recipe_malformed"),
        (lambda d: d.pop("why"), "recipe_malformed"),
        (lambda d: d.update(table=" "), "recipe_malformed"),
        (lambda d: d["layers"].append("points"), "recipe_malformed"),
    ],
)
def test_recipe_from_dict_is_strict(mutate, code):
    d = good_comparison().to_dict()
    mutate(d)
    with pytest.raises(R.RecipeError) as ei:
        R.FigureRecipe.from_dict(d)
    assert ei.value.code == code


def test_recipe_from_dict_rejects_non_objects():
    for bad in (None, [], "x", 3):
        with pytest.raises(R.RecipeError) as ei:
            R.FigureRecipe.from_dict(bad)
        assert ei.value.code == "recipe_malformed"


def test_unregistered_codes_cannot_be_built():
    with pytest.raises(ValueError):
        R.RecipeError("not_a_code")
    with pytest.raises(ValueError):
        R.Problem("not_a_code")


# --------------------------------------------------------------------------- 表自身的一致性（唯一权威）
def test_tables_are_consistent():
    assert set(R.MARKS) == set(R.MARK_RULES) and len(R.MARKS) == 18
    assert len(R.FAMILIES) == 12 and {f.batch for f in R.FAMILIES.values()} == {1, 2, 3}
    seen: dict[str, str] = {}
    for f in R.FAMILIES.values():
        assert set(f.marks) <= set(R.MARKS) and set(f.primary) <= set(f.marks), f.key
        assert set(f.x_free_marks) <= set(f.marks)
        assert f.y_min <= f.y_max and f.unit in ("no", "optional", "required")
        for c in f.rejects:
            assert c in R.REJECTIONS and c not in seen, f"{c} 属于 {seen.get(c)} 又属于 {f.key}"
            seen[c] = f.key
    assert set(R.ERROR_CODES) == set(R.REJECTIONS)
    # 每个码要么通用、要么配方格式、要么恰属一个族
    assert set(R.COMMON_REJECTIONS) | set(R.RECIPE_REJECTIONS) | set(seen) == set(R.REJECTIONS)
    assert not set(R.COMMON_REJECTIONS) & set(seen)
    assert R.VALUE_RULES <= set(R.REJECTIONS)
    assert R.MARK_RULES["errorbar"].summary == "required"


def test_renderable_is_a_subset_of_batch_one():
    assert {k for k in R.FAMILIES if R.renderable(k)} == {
        "comparison",
        "trend",
        "relationship",
        "distribution",
    }
    assert all(R.FAMILIES[k].batch == 1 for k in R.FAMILIES if R.renderable(k))
    with pytest.raises(ValueError):
        R.renderable("sankey")


def test_module_is_stdlib_only():
    """Flask 父进程 import：只许标准库 + 同包相对 import（process-boundaries）。"""
    tree = ast.parse((ROOT / "src/tavotto/engine/ai_data_recipe.py").read_text(encoding="utf-8"))
    import sys

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 1 or node.module.split(".")[0] in sys.stdlib_module_names, (
                node.module
            )


# --------------------------------------------------------------------------- validate：结构
def test_good_recipes_pass(profile, folder):
    assert check(profile, folder, good_comparison()) == []
    r = rec(
        "rep.csv", "trend", [("line", "mean_sd"), ("band", "mean_sd", "overlay")], ["y"], x="time"
    )
    assert check(profile, folder, r) == []


def test_table_must_exist(profile):
    r = rec("nope.csv", "comparison", ["points"], ["response"], x="condition")
    assert codes(R.validate_recipe(profile, r, confirmed=NONE)) == {"table_not_found"}
    r = rec("long.csv", "comparison", ["points"], ["response"], x="condition", sheet="Sheet1")
    assert codes(R.validate_recipe(profile, r, confirmed=NONE)) == {"table_not_found"}


@pytest.mark.parametrize(
    "r, want",
    [
        (rec("long.csv", "comparison", ["points"], ["nope"], x="condition"), "column_not_found"),
        (rec("long.csv", "comparison", ["points"], ["response"], x="nope"), "column_not_found"),
        (
            rec("long.csv", "comparison", ["points"], ["response"], x="response"),
            "column_type_mismatch",
        ),
        (
            rec("long.csv", "comparison", ["points"], ["condition"], x="condition"),
            "column_type_mismatch",
        ),
        (rec("long.csv", "comparison", ["points"], ["response"]), "roles_invalid"),
        (
            rec("long.csv", "comparison", ["points"], ["response", "response"], x="condition"),
            "roles_invalid",
        ),
        (rec("long.csv", "comparison", ["hist"], ["response"], x="condition"), "mark_not_allowed"),
        (
            rec(
                "long.csv",
                "comparison",
                [("errorbar", "none", "overlay")],
                ["response"],
                x="condition",
            ),
            "summary_missing",
        ),
        (
            rec("long.csv", "comparison", [("points", "mean_sd")], ["response"], x="condition"),
            "summary_misplaced",
        ),
        (
            rec(
                "long.csv",
                "comparison",
                [("errorbar", "mean_sd", "overlay")],
                ["response"],
                x="condition",
            ),
            "no_primary_mark",
        ),
        (
            rec(
                "long.csv",
                "comparison",
                [("points", "none", "overlay")],
                ["response"],
                x="condition",
            ),
            "layer_role_invalid",
        ),
        (
            rec("long.csv", "comparison", ["points", "points"], ["response"], x="condition"),
            "layer_role_invalid",
        ),
        (
            rec("long.csv", "comparison", ["points"], ["response"], x="condition", coord="polar"),
            "coord_not_allowed",
        ),
        (
            rec("long.csv", "comparison", ["points"], ["response"], x="condition", paired=True),
            "roles_invalid",
        ),
        (
            rec("long.csv", "relationship", ["points"], ["response"], x="condition"),
            "column_type_mismatch",
        ),
        (rec("long.csv", "distribution", ["hist"], ["response"], x="condition"), "roles_invalid"),
    ],
)
def test_structural_rejections(profile, r, want):
    assert want in codes(R.validate_recipe(profile, r, confirmed=NONE))


def test_hist_by_group_without_x_is_fine(profile, folder):
    r = rec("long.csv", "distribution", ["hist"], ["response"], group="condition")
    assert check(profile, folder, r, with_values=False) == []


def test_unit_needs_user_confirmation_not_id_like(profile, folder):
    s = entry(profile, "long.csv").summary
    assert "mouse" in s.id_like and s.repeats is True  # 线索齐了
    r = rec("long.csv", "comparison", ["jitter"], ["response"], x="condition", unit="mouse")
    assert "unit_unconfirmed" in codes(check(profile, folder, r))
    # 确认的是别的列，也不算
    assert "unit_unconfirmed" in codes(
        check(profile, folder, r, confirmed=R.Confirmed("repeated", "condition"))
    )
    # 只确认了「独立」
    assert "unit_unconfirmed" in codes(
        check(profile, folder, r, confirmed=R.Confirmed("independent", "mouse"))
    )
    assert "unit_unconfirmed" not in codes(
        check(profile, folder, r, confirmed=R.Confirmed("repeated", "mouse"))
    )
    # 不用个体编号的族不许带
    r2 = rec("long.csv", "relationship", ["points"], ["response"], x="response", unit="mouse")
    assert "roles_invalid" in codes(
        check(profile, folder, r2, confirmed=R.Confirmed("repeated", "mouse"))
    )


def test_paired_and_trajectories_need_confirmed_unit(profile, folder):
    r = rec(
        "paired.csv",
        "paired",
        ["paired_lines", "points"],
        ["value"],
        x="phase",
        unit="mouse",
        paired=True,
    )
    assert "paired_unconfirmed" in codes(check(profile, folder, r))
    assert "paired_unconfirmed" in codes(
        check(profile, folder, r, confirmed=R.Confirmed("repeated", "mouse"))
    )
    assert check(profile, folder, r, confirmed=R.Confirmed("paired", "mouse")) == []
    no_unit = rec("paired.csv", "paired", ["paired_lines"], ["value"], x="phase", paired=True)
    assert "unit_required" in codes(
        check(profile, folder, no_unit, confirmed=R.Confirmed("paired", "mouse"))
    )
    not_flagged = rec("paired.csv", "paired", ["paired_lines"], ["value"], x="phase", unit="mouse")
    assert "roles_invalid" in codes(
        check(profile, folder, not_flagged, confirmed=R.Confirmed("paired", "mouse"))
    )
    tr = rec("many.csv", "trajectories", ["line"], ["y"], x="t")
    assert "unit_required" in codes(check(profile, folder, tr))


def test_facet_and_group_level_limits(profile):
    s = entry(profile, "levels.csv").summary
    assert s.groups == {"site": 7, "arm": 9}
    r = rec("levels.csv", "comparison", ["points"], ["response"], x="site", facet="arm")
    assert "facet_limit_exceeded" in codes(R.validate_recipe(profile, r, confirmed=NONE))
    r = rec("levels.csv", "comparison", ["points"], ["response"], x="site", group="arm")
    assert "group_limit_exceeded" in codes(R.validate_recipe(profile, r, confirmed=NONE))
    ok = rec(
        "levels.csv", "comparison", ["points"], ["response"], x="arm", group="site", facet=None
    )
    assert "facet_limit_exceeded" not in codes(R.validate_recipe(profile, ok, confirmed=NONE))


def test_no_dual_y_axis_for_different_units(profile, folder):
    r = rec("wide.csv", "trend", ["line"], ["temp_C", "pressure_kPa"], x="time_s")
    assert "dual_axis_unsupported" in codes(check(profile, folder, r))
    same = rec(
        "wide.csv", "trend", ["line"], ["temp_C", "flow"], x="time_s"
    )  # flow 没有单位后缀：不能断定
    assert "dual_axis_unsupported" not in codes(check(profile, folder, same))


def test_matrix_size_ranking_and_composition_structure(profile, tmp_path):
    ok = rec("matrix.csv", "matrix", ["heatmap"], ["a", "b", "c"], x="name")
    assert R.validate_recipe(profile, ok, confirmed=NONE) == []
    big = tmp_path / "big"
    big.mkdir()
    n = R.MAX_MATRIX_ROWS + 1
    _write(
        big,
        "m.csv",
        [["row"] + [f"r{i}" for i in range(n)]] + [[f"r{i}"] + [1] * n for i in range(n)],
    )
    bp = adf.profile_folder(big)
    r = rec("m.csv", "matrix", ["heatmap"], [f"r{i}" for i in range(6)], x="row")
    assert "matrix_too_large" in codes(R.validate_recipe(bp, r, confirmed=NONE))
    rk = rec("comp.csv", "ranking", ["bar"], ["p1"], x="site")
    assert "ranking_needs_order" in codes(R.validate_recipe(profile, rk, confirmed=NONE))
    assert "ranking_needs_order" not in codes(
        R.validate_recipe(
            profile,
            rec("comp.csv", "ranking", ["bar"], ["p1"], x="site", order="value_desc"),
            confirmed=NONE,
        )
    )
    many = tmp_path / "many"
    many.mkdir()
    _write(many, "r.csv", [["gene", "v"]] + [[f"g{i}", i] for i in range(R.MAX_RANKING_ITEMS + 1)])
    mp = adf.profile_folder(many)
    r = rec("r.csv", "ranking", ["bar"], ["v"], x="gene", order="value_desc")
    assert "ranking_too_many_items" in codes(R.validate_recipe(mp, r, confirmed=NONE))
    parts = tmp_path / "parts"
    parts.mkdir()
    _write(
        parts,
        "c.csv",
        [["site"] + [f"p{i}" for i in range(R.MAX_COMPOSITION_PARTS + 1)]]
        + [[s] + [1] * (R.MAX_COMPOSITION_PARTS + 1) for s in ("A", "B")],
    )
    pp = adf.profile_folder(parts)
    r = rec(
        "c.csv",
        "composition",
        ["stacked_bar"],
        [f"p{i}" for i in range(R.MAX_COMPOSITION_PARTS + 1)],
        x="site",
    )
    assert "composition_too_many_parts" in codes(R.validate_recipe(pp, r, confirmed=NONE))
    r2 = rec("comp.csv", "composition", ["stacked_bar"], ["p1", "p2"], x="site", group="site")
    assert "roles_invalid" in codes(
        R.validate_recipe(profile, r2, confirmed=NONE)
    )  # 宽格式不给 group


# --------------------------------------------------------------------------- validate：取值（需要行数据）
def test_values_are_only_checked_when_given(profile, folder):
    r = rec("small.csv", "comparison", ["points", ("box", "none")], ["response"], x="condition")
    assert R.validate_recipe(profile, r, confirmed=NONE) == []  # 没给取值：不判
    assert "box_violin_n_small" in codes(check(profile, folder, r))


def test_box_violin_threshold_is_five(profile, folder, tmp_path):
    r = rec("small.csv", "comparison", [("violin", "none")], ["response"], x="condition")
    assert "box_violin_n_small" in codes(check(profile, folder, r))  # 每组 4 个
    five = tmp_path / "five"
    five.mkdir()
    _write(five, "f.csv", [["g", "v"]] + [[c, i] for c in ("a", "b") for i in range(R.MIN_N_BOX)])
    fp = adf.profile_folder(five)
    ok = rec("f.csv", "comparison", ["box"], ["v"], x="g")
    assert (
        R.validate_recipe(fp, ok, confirmed=NONE, values=adf.inspect_data_source(five / "f.csv"))
        == []
    )


def test_values_mismatching_the_profile_table_are_a_programming_error(profile, folder):
    r = good_comparison()
    with pytest.raises(ValueError):
        R.validate_recipe(profile, r, confirmed=NONE, values=vals(folder, "small.csv"))


def test_hist_density_and_fit_line_need_enough_points(profile, folder):
    r = rec("long.csv", "distribution", ["hist"], ["response"], group="condition")
    assert "hist_density_n_small" in codes(check(profile, folder, r))  # 每组 6 < 10
    r = rec("wide.csv", "relationship", ["points", "fit_line"], ["flow"], x="time_s")
    assert "fit_line_n_small" not in codes(check(profile, folder, r))  # 8 个点
    r = rec("rep.csv", "relationship", ["points", "fit_line"], ["y"], x="time", group="subject")
    assert "fit_line_n_small" in codes(check(profile, folder, r))  # 每个 subject 4 个点 < 6


def test_bar_needs_nonnegative_values_and_linear_axis(profile, folder):
    r = rec(
        "neg.csv",
        "comparison",
        [("bar", "mean_sd"), ("errorbar", "mean_sd", "overlay")],
        ["response"],
        x="condition",
    )
    assert "bar_baseline_invalid" in codes(check(profile, folder, r))
    r = rec(
        "long.csv", "comparison", [("bar", "mean_sd")], ["response"], x="condition", scale="log_y"
    )
    assert "bar_baseline_invalid" in codes(check(profile, folder, r))
    r = rec("long.csv", "comparison", [("bar", "mean_sd")], ["response"], x="condition")
    assert "bar_baseline_invalid" not in codes(check(profile, folder, r))


def test_bar_without_summary_needs_one_value_per_position(profile, folder):
    r = rec("long.csv", "comparison", ["bar"], ["response"], x="condition")
    assert "bar_needs_summary" in codes(check(profile, folder, r))  # 一组 6 行
    r = rec("comp.csv", "ranking", ["bar"], ["p1"], x="site", order="value_desc")
    assert "bar_needs_summary" not in codes(check(profile, folder, r))


def test_pointwise_line_with_duplicate_x_needs_confirmed_unit(profile, folder):
    r = rec("rep.csv", "trend", ["line"], ["y"], x="time")
    assert "line_duplicate_x_unconfirmed" in codes(check(profile, folder, r))
    summarized = rec(
        "rep.csv", "trend", [("line", "mean_sd"), ("band", "mean_sd", "overlay")], ["y"], x="time"
    )
    assert "line_duplicate_x_unconfirmed" not in codes(check(profile, folder, summarized))
    per_unit = rec("rep.csv", "trend", ["line"], ["y"], x="time", unit="subject")
    assert "line_duplicate_x_unconfirmed" in codes(check(profile, folder, per_unit))  # 没确认
    ok = check(profile, folder, per_unit, confirmed=R.Confirmed("repeated", "subject"))
    assert "line_duplicate_x_unconfirmed" not in codes(ok)
    no_dup = rec("wide.csv", "trend", ["line"], ["flow"], x="time_s")
    assert check(profile, folder, no_dup) == []


def test_log_axes_reject_nonpositive_values(profile, folder):
    r = rec("dose.csv", "dose_response", ["points"], ["resp"], x="dose", scale="log_x")
    assert "log_nonpositive" in codes(check(profile, folder, r))
    r = rec("dose_ok.csv", "dose_response", ["points"], ["resp"], x="dose", scale="log_xy")
    assert check(profile, folder, r) == []


def test_dose_must_be_positive_even_on_a_linear_axis(profile, folder):
    r = rec("dose.csv", "dose_response", ["points"], ["resp"], x="dose")
    assert codes(check(profile, folder, r)) == {"dose_nonpositive"}


def test_trend_needs_three_distinct_x(profile, folder, tmp_path):
    two = tmp_path / "two"
    two.mkdir()
    _write(two, "t.csv", [["t", "v"]] + [[t, t * 2] for t in (1, 2)])
    _write(two, "pad.csv", [["t", "v", "w"]] + [[t, t, t] for t in (1, 2)])
    tp = adf.profile_folder(two)
    r = rec("pad.csv", "trend", ["line"], ["v"], x="t")
    p = R.validate_recipe(tp, r, confirmed=NONE, values=adf.inspect_data_source(two / "pad.csv"))
    assert "trend_too_few_x" in codes(p)
    assert "trend_too_few_x" not in codes(
        check(profile, folder, rec("wide.csv", "trend", ["line"], ["flow"], x="time_s"))
    )


def test_paired_needs_complete_pairs(profile, folder, tmp_path):
    conf = R.Confirmed("paired", "mouse")
    r = rec(
        "paired.csv", "paired", ["paired_lines"], ["value"], x="phase", unit="mouse", paired=True
    )
    assert check(profile, folder, r, confirmed=conf) == []
    bad = tmp_path / "bad"
    bad.mkdir()
    _write(
        bad,
        "p.csv",
        [["phase", "mouse", "v"]]
        + [["pre", f"m{i}", i] for i in range(1, 5)]
        + [["post", f"m{i}", i] for i in range(1, 4)]
        + [["post", "m9", 1], ["pre", "m9", 1], ["pre", "m9", 2]],
    )
    bp = adf.profile_folder(bad)
    p = R.validate_recipe(
        bp,
        r.__class__.from_dict({**r.to_dict(), "table": "p.csv", "y": ["v"]}),
        confirmed=conf,
        values=adf.inspect_data_source(bad / "p.csv"),
    )
    assert "paired_incomplete" in codes(p)


def test_composition_parts_must_be_nonnegative_with_positive_total(profile, folder):
    ok = rec("comp.csv", "composition", ["stacked_bar"], ["p1", "p2", "p3"], x="site")
    assert check(profile, folder, ok) == []
    bad = rec("comp_bad.csv", "composition", ["stacked_bar"], ["p1", "p2", "p3"], x="site")
    got = codes(check(profile, folder, bad))
    assert {"composition_negative", "composition_total_not_positive"} <= got
    assert "bar_baseline_invalid" in got  # 堆叠柱同样要求非负


def test_interval_bounds_and_summary_semantics(profile, folder):
    ok = rec("interval.csv", "interval", ["errorbar", "points"], ["est", "lo", "hi"], x="gene")
    assert check(profile, folder, ok) == []  # 区间由列给出：errorbar 不带 summary
    with_summary = rec(
        "interval.csv", "interval", [("errorbar", "mean_sd")], ["est", "lo", "hi"], x="gene"
    )
    assert "summary_misplaced" in codes(check(profile, folder, with_summary))
    bad = rec("interval_bad.csv", "interval", ["errorbar"], ["est", "lo", "hi"], x="gene")
    assert "interval_bounds_invalid" in codes(check(profile, folder, bad))


def test_trajectories_count_limit(profile, folder):
    conf = R.Confirmed("repeated", "id")
    r = rec("many.csv", "trajectories", ["line"], ["y"], x="t", unit="id")
    assert "trajectories_limit_exceeded" in codes(check(profile, folder, r, confirmed=conf))
    r = rec("rep.csv", "trajectories", ["line"], ["y"], x="time", unit="subject")
    assert check(profile, folder, r, confirmed=R.Confirmed("repeated", "subject")) == []


# --------------------------------------------------------------------------- eligible
def fam(el, key):
    return next(e for e in el if e.family == key)


def test_eligible_lists_all_twelve_in_table_order(profile):
    el = R.eligible(entry(profile, "long.csv"), NONE)
    assert [e.family for e in el] == list(R.FAMILIES)
    assert fam(el, "comparison").status == "ok" and fam(el, "comparison").renderable
    assert not fam(el, "paired").renderable


def test_eligible_structure_reasons(profile):
    el = R.eligible(entry(profile, "long.csv"), NONE)
    assert fam(el, "relationship").status == "no" and fam(el, "relationship").reasons == (
        "no_usable_columns",
    )
    assert fam(el, "matrix").status == "no"
    el = R.eligible(entry(profile, "wide.csv"), NONE)
    assert fam(el, "trend").status == "ok" and fam(el, "comparison").status == "no"
    assert fam(el, "relationship").status == "ok"
    assert entry(profile, "matrix.csv").summary.layout == "matrix"
    assert fam(R.eligible(entry(profile, "matrix.csv"), NONE), "matrix").status == "ok"


def test_eligible_unit_families_are_unlocked_only_by_user_confirmation(profile):
    e = entry(profile, "paired.csv")
    assert e.summary.id_like == ["mouse"] and e.summary.repeats is True
    el = R.eligible(e, NONE)
    assert fam(el, "paired").status == "no" and fam(el, "paired").reasons == ("paired_unconfirmed",)
    assert fam(el, "trajectories").status == "no"  # x 不是有顺序的列
    assert set(fam(el, "paired").reasons) <= R.ASK_FIRST
    assert fam(R.eligible(e, R.Confirmed("repeated", "mouse")), "paired").status == "no"
    assert fam(R.eligible(e, R.Confirmed("paired", "mouse")), "paired").status == "ok"
    assert fam(R.eligible(e, R.Confirmed("paired", "nope")), "paired").status == "no"
    r = entry(profile, "rep.csv")
    assert fam(R.eligible(r, NONE), "trajectories").reasons == ("unit_required",)
    assert fam(R.eligible(r, R.Confirmed("repeated", "subject")), "trajectories").status == "ok"


def test_eligible_reports_unchecked_value_rules_and_rejected_marks(profile, folder):
    e = entry(profile, "small.csv")
    c = fam(R.eligible(e, NONE), "comparison")
    assert c.status == "ok" and "box_violin_n_small" in c.unchecked
    c = fam(R.eligible(e, NONE, values=vals(folder, "small.csv")), "comparison")
    assert c.status == "partial" and c.unchecked == ()
    assert c.rejected_marks == {"box": "box_violin_n_small", "violin": "box_violin_n_small"}
    assert "bar_before_raw_points" in c.order_rules
    ok = fam(
        R.eligible(entry(profile, "long.csv"), NONE, values=vals(folder, "long.csv")), "comparison"
    )
    assert ok.status == "ok" and ok.rejected_marks == {}
    neg = fam(
        R.eligible(entry(profile, "neg.csv"), NONE, values=vals(folder, "neg.csv")), "comparison"
    )
    assert neg.rejected_marks == {"bar": "bar_baseline_invalid"}


def test_eligible_matrix_size_limit(tmp_path):
    n = R.MAX_MATRIX_ROWS + 1
    _write(
        tmp_path,
        "m.csv",
        [["row"] + [f"r{i}" for i in range(n)]] + [[f"r{i}"] + [1] * n for i in range(n)],
    )
    p = adf.profile_folder(tmp_path)
    (e,) = p.tables
    assert e.summary.layout in ("matrix", "wide")
    assert fam(R.eligible(e, NONE), "matrix").reasons == ("matrix_too_large",)


# --------------------------------------------------------------------------- 排序护栏
def bar_only():
    return rec(
        "long.csv",
        "comparison",
        [("bar", "mean_sd"), ("errorbar", "mean_sd", "overlay")],
        ["response"],
        x="condition",
        why="柱",
    )


def raw_points():
    return rec(
        "long.csv",
        "comparison",
        ["jitter", ("hline_summary", "mean_sd", "overlay")],
        ["response"],
        x="condition",
        why="点",
    )


def box_raw():
    return rec("long.csv", "comparison", ["box", "jitter"], ["response"], x="condition", why="箱")


def test_bar_candidates_never_precede_raw_point_candidates(profile, folder):
    res = R.enforce_order(
        profile,
        [bar_only(), raw_points(), box_raw()],
        confirmed=NONE,
        values=vals(folder, "long.csv"),
    )
    assert [r.why for r in res.kept] == ["点", "箱", "柱"]
    assert [m.code for m in res.moves] == ["bar_before_raw_points"] and res.moves[0].detail == "0"
    assert not res.short and res.dropped == ()


def test_order_is_untouched_when_nothing_violates_it(profile, folder):
    cands = [box_raw(), raw_points(), bar_only()]
    res = R.enforce_order(profile, cands, confirmed=NONE, values=vals(folder, "long.csv"))
    assert list(res.kept) == cands and res.moves == ()
    only_bar = [
        bar_only(),
        rec("long.csv", "comparison", ["box"], ["response"], x="condition", why="箱"),
    ]
    res = R.enforce_order(profile, only_bar, confirmed=NONE)
    assert [r.why for r in res.kept] == ["柱", "箱"]  # 没有含原始点的备选：不凭空调序


def test_invalid_candidates_are_dropped_with_reasons_and_never_padded(profile, folder):
    bad = rec("long.csv", "comparison", ["hist"], ["response"], x="condition", why="坏")
    res = R.enforce_order(
        profile, [bad, raw_points()], confirmed=NONE, values=vals(folder, "long.csv")
    )
    assert [r.why for r in res.kept] == ["点"] and res.short
    assert res.dropped[0].index == 0 and "mark_not_allowed" in codes(res.dropped[0].problems)
    empty = R.enforce_order(profile, [], confirmed=NONE)
    assert empty.kept == () and empty.short


def test_duplicates_and_overflow_are_dropped(profile, folder):
    r = raw_points()
    res = R.enforce_order(profile, [r, r], confirmed=NONE)
    assert len(res.kept) == 1 and res.dropped[0].problems[0].code == "duplicate_candidate"
    many = [
        rec("long.csv", "comparison", [m], ["response"], x="condition", why=m)
        for m in ("points", "jitter", "box", "violin", "bar", "hline_summary")[:5]
    ]
    many = [m for m in many if m.layers[0].mark != "bar"] + [bar_only()]
    five = many + [
        rec(
            "long.csv",
            "comparison",
            ["points"],
            ["response"],
            x="condition",
            group="condition",
            why="g",
        )
    ]
    res = R.enforce_order(profile, five, confirmed=NONE)
    assert len(res.kept) == R.MAX_CANDIDATES
    assert res.dropped[-1].problems[0].code == "too_many_candidates"


# --------------------------------------------------------------------------- 选图准则
G = ai_prompt_pack.data_figure_guidance()


def test_guidance_has_a_sentence_for_every_rejection_code():
    for code, text in R.REJECTIONS.items():
        assert f"`{code}`：{text}" in G, code


def test_guidance_is_rendered_from_the_family_table():
    for f in R.FAMILIES.values():
        assert f"## `{f.key}`（{f.title}，批次 {f.batch}）" in G
        assert f.when_use in G and f.when_not in G
        for m in f.marks:
            assert f"`{m}`" in G
    for m, rule in R.MARK_RULES.items():
        assert rule.meaning in G
    for s in R.SUMMARIES:
        assert f"`{s}`" in G


def test_guidance_names_nothing_the_tables_do_not_have():
    allowed = (
        set(R.FAMILIES)
        | set(R.MARKS)
        | set(R.SUMMARIES)
        | set(R.COORDS)
        | set(R.REJECTIONS)
        | set(R.ORDERS)
        | {
            "x",
            "y",
            "group",
            "facet",
            "unit",
            "layers",
            "family",
            "why",
            "summary",
            "order",
            "ask_user",
            "main",
        }
    )
    names = set(re.findall(r"`([A-Za-z_0-9]+)`", G))
    assert names <= allowed, sorted(names - allowed)
    # 反过来：每个族、图元都出现
    assert set(R.FAMILIES) <= names and set(R.MARKS) <= names


def test_guidance_fixed_questions_and_output_requirements():
    for needle in (
        "要得出什么结论",
        "独立，还是同一个对象",
        "横轴有没有顺序",
        "ask_user",
        "不猜",
        "落选",
        "不把只有柱加误差线的画法排在",
    ):
        assert needle in G
    assert "只有：`comparison`、`trend`、`relationship`、`distribution`" in G
    assert "SD、SEM 还是 95% CI" in G  # 误差线必须写明
    assert "连线暗示两点之间的值可内插" in G and "轴必须含 0" in G


def test_thresholds_in_guidance_come_from_named_constants(monkeypatch):
    # 阈值改了，准则文字跟着变（不是另抄一份数字）
    assert str(R.MIN_N_BOX) in R.REJECTIONS["box_violin_n_small"]
    assert str(R.MAX_MATRIX_ROWS) in R.FAMILIES["matrix"].when_not
    assert str(R.MAX_TRAJECTORIES) in R.REJECTIONS["trajectories_limit_exceeded"]


def test_guidance_has_no_internal_jargon_in_user_facing_error_texts():
    """错误码文案是给用户看的：不出现「配方 / 图族 / 执行器」。"""
    for loc in ("zh-CN", "en-US"):
        table = json.loads(
            (ROOT / "web/src/i18n/locales" / loc / "errors.json").read_text(encoding="utf-8")
        )["backend"]
        for code in R.ERROR_CODES:
            assert code in table, (loc, code)
            if loc == "zh-CN":
                assert not re.search("配方|图族|执行器", table[code]), code
