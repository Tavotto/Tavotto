# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""现成误差列的后续三块（ADR 0178 修订 2026-11-25）：柱 + 现成误差线（`bar_err`）、多个 y 各配各的误差列、合画；以及明确不支持的大表与个体编号。

判据的主语：

* **柱的资格**：线性轴、y ≥ 0、每个位置 y − 误差 ≥ 0，缺一就不给柱（宁可不给，不截须），其余候选不受影响；
* **多个 y**：每个 y 各配唯一的误差列才算已汇总，缺一个整体不给；单个 y 的配方 / 脚本 / 响应与从前逐字相同；
* **合画**：按合并后的那张表照同一口径判，某一份有重复测量 = 整体不是已汇总；
* **不支持的形状**（大表、个体编号）：不触发，也不连带剔掉别的备选。
"""

from __future__ import annotations

import json
import socket

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_err_column import (  # noqa: F401
    candidates,
    codes,
    first,
    read,
    run_script,
    session_of,
)
from tests.test_ai_data_propose import (  # noqa: F401
    checks_of,
    commit,
    needs_worker,
    project,
    propose,
    put,
)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")


GROUPS = [
    ["group", "mean", "sd", "n"],
    ["ctrl", 5.0, 0.2, 6],
    ["drugA", 8.0, 0.3, 6],
    ["drugB", 9.0, 0.4, 5],
]
MULTI = [["time", "a_mean", "a_sd", "b_mean", "b_sd"]] + [
    [t, float(t), 0.1, 2.0 * t, 0.2] for t in range(1, 6)
]


def dropped_codes(res):
    return [(p.code, p.detail) for d in res.dropped for p in d.problems]


# =========================================================================== A. bar_err
def test_bar_err_follows_points_err(tmp_path):
    res = candidates(tmp_path, GROUPS)
    assert codes(res) == ["points_err", "bar_err"]
    bar = res.items[1].recipe
    assert bar.family == "comparison" and bar.x == "group" and bar.y == ("mean",)
    assert [layer.mark for layer in bar.layers] == ["bar"]
    assert (bar.err, bar.err_kind) == ("sd", "sd")
    assert not [c for c in codes(res) if c in ("bar_error",)]  # 不对汇总值再算均值 ± 误差的柱


@pytest.mark.parametrize(
    "mean_ctrl, sd_ctrl, why",
    [
        (1.2, 1.5, "whisker_below_zero"),  # y − 误差 < 0：柱图的 y 轴下限固定在 0 会截掉下面的须
        (-1.2, 0.1, "negative_y"),  # 有负值：柱必须从 0 起，不适合
    ],
)
def test_no_bar_when_it_cannot_start_at_zero_without_cutting_whiskers(
    tmp_path, mean_ctrl, sd_ctrl, why
):
    rows = [list(r) for r in GROUPS]
    rows[1][1], rows[1][2] = mean_ctrl, sd_ctrl
    res = candidates(tmp_path, rows)
    assert codes(res) == ["points_err"], why  # 点 + 误差线照给；别的备选一个没被连带剔除
    assert ("summarised_not_raw", "hist") in dropped_codes(res)


def test_bar_err_is_never_offered_on_a_trend_and_a_missing_error_cell_keeps_the_bar(tmp_path):
    dose = [["dose", "mean_response", "sd"]] + [
        [d, 10.0 + i, 0.5] for i, d in enumerate((1, 2, 3, 4, 5))
    ]
    assert "bar_err" not in codes(candidates(tmp_path / "t", dose, picks={"y": "unsure"}))
    rows = [list(r) for r in GROUPS]
    rows[2][2] = ""  # 误差格空：这根柱没有误差线，柱照画
    assert codes(candidates(tmp_path / "g", rows)) == ["points_err", "bar_err"]


def test_bar_err_validates_and_negative_values_are_still_rejected_by_the_gate(tmp_path):
    profile, values, _ = read(tmp_path, GROUPS)
    rec = R.default_candidates(profile, confirmed=R.Confirmed(), values=values).items[1].recipe
    assert R.validate_recipe(profile, rec, confirmed=R.Confirmed(), values=values) == []
    from dataclasses import replace

    log = replace(rec, scale="log_y")
    assert "bar_baseline_invalid" in [
        p.code for p in R.validate_recipe(profile, log, confirmed=R.Confirmed(), values=values)
    ]


def test_bar_err_script_calls_bar_and_errorbar_with_the_tables_numbers(tmp_path):
    rec = candidates(tmp_path / "c", GROUPS).items[1].recipe
    _, values, entry = read(tmp_path / "d", GROUPS)
    script = tpl.render_recipe_script(entry, rec, data_rel="t.csv", stem="s").script
    assert "ax.bar(" in script and "yerr=errs" in script and "ax.set_ylim(bottom=0)" in script
    d = tpl.comparison_data(
        values.columns, values.rows, rec.x, rec.y[0], rec.group, rec.facet, rec.order, rec.err
    )
    cells = tpl.comparison_cells(d, "", "")
    assert tpl.comparison_bars(cells, 0, 1, "none") == ([0.0, 1.0, 2.0], [5.0, 8.0, 9.0])
    assert tpl.comparison_error_points(d, "", "", 0, 1)[2] == [0.2, 0.3, 0.4]
    cov = tpl.fingerprint_coverage(rec)
    assert {(c.mark, c.checked) for c in cov.layers} == {("bar", True), ("errorbar", False)}
    labels = {e["label"].split(" |")[0] for e in tpl.expected_fingerprints(values, rec)}
    assert labels == {"bar"}  # 柱可核（误差线没有 label，核不了）


@needs_worker
def test_bar_err_end_to_end(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    out = propose(client, render="lazy").get_json()
    assert [i["title_code"] for i in out["items"]] == ["points_err", "bar_err"]
    bar = out["items"][1]
    assert bar["error"] == "sd" and bar["error_source"] == "column" and bar["error_stated"] is True
    thumb = client.get(bar["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    done = commit(client, out["session"], bar["id"]).get_json()
    assert done["task"]["outcome"] == "completed" and done["data_check"] == "verified", done
    script = (figs / done["script"]).read_text(encoding="utf-8")
    assert "ax.bar(" in script and "comparison_error_points" in script


# =========================================================================== B1. 多个 y
def test_multi_y_each_y_pairs_with_its_own_error_column(tmp_path):
    res = candidates(tmp_path, MULTI)
    top = first(res).recipe
    assert first(res).code == "line_err"
    assert top.y == ("a_mean", "b_mean") and top.err == ("a_sd", "b_sd")
    assert top.err_kind == ("sd", "sd") and top.err_cols == ("a_sd", "b_sd")
    d = top.to_dict()
    assert d["err"] == ["a_sd", "b_sd"] and d["err_kind"] == ["sd", "sd"]
    assert R.FigureRecipe.from_dict(d) == top
    assert ("summarised_not_raw", "line:mean_sd") in dropped_codes(res)


def test_multi_y_with_one_unpaired_series_is_not_summarised(tmp_path):
    rows = [["time", "a_mean", "a_sd", "b_mean"]] + [
        [t, float(t), 0.1, 2.0 * t] for t in range(1, 6)
    ]
    res = candidates(tmp_path, rows)
    assert all(c.recipe.err is None for c in res.items)
    assert not [1 for _c, _d in dropped_codes(res) if _c == "summarised_not_raw"]
    assert res.items  # 别的备选照给


def test_multi_y_two_series_sharing_one_error_column_is_not_summarised(tmp_path):
    rows = [["time", "a_mean", "b_mean", "sd"]] + [[t, float(t), 2.0 * t, 0.1] for t in range(1, 6)]
    assert all(c.recipe.err is None for c in candidates(tmp_path, rows).items)


def test_multi_y_kinds_can_differ(tmp_path):
    rows = [["time", "a_mean", "a_sd", "b_mean", "b_sem"]] + [
        [t, float(t), 0.1, 2.0 * t, 0.2] for t in range(1, 6)
    ]
    rec = first(candidates(tmp_path, rows)).recipe
    assert rec.err_kind == ("sd", "sem")


def test_multi_y_validation(tmp_path):
    profile, values, _ = read(tmp_path, MULTI)

    def problems(**kw):
        base = {
            "table": "t.csv",
            "family": "trend",
            "layers": [{"mark": "points", "summary": "none", "role": "main"}],
            "x": "time",
            "y": ["a_mean", "b_mean"],
            "err": ["a_sd", "b_sd"],
            "err_kind": ["sd", "sd"],
            "why": "w",
        }
        base.update(kw)
        rec = R.FigureRecipe.from_dict(base)
        return [
            (p.code, p.field)
            for p in R.validate_recipe(profile, rec, confirmed=R.Confirmed(), values=values)
        ]

    assert problems() == []
    assert ("roles_invalid", "err") in problems(err=["a_sd", "a_sd"])  # 互不相同
    assert ("roles_invalid", "err") in problems(y=["a_mean"])  # 个数与 y 对不上
    assert ("summary_missing", "err_kind") in problems(err_kind="sd")  # 写法与 err 不一致
    assert ("roles_invalid", "err") in problems(family="comparison", x="time")  # 多个 y 只有趋势类
    # 个数上限 = 点名的多列纵轴上限（`MULTI_Y_MAX`，2026-11-28 起是 8）：超过一个就不是合法写法
    too_many = [f"c{i}" for i in range(R.MULTI_Y_MAX + 1)]
    for bad in ({"err": ["a_sd"]}, {"err": too_many}, {"err_kind": ["sd"]}):
        with pytest.raises(R.RecipeError):
            R.FigureRecipe.from_dict(
                {
                    "table": "t.csv",
                    "family": "trend",
                    "x": "time",
                    "y": ["a_mean", "b_mean"],
                    "why": "w",
                    "layers": [{"mark": "points", "summary": "none", "role": "main"}],
                    "err": ["a_sd", "b_sd"],
                    "err_kind": ["sd", "sd"],
                    **bad,
                }
            )


def test_multi_y_script_draws_one_errorbar_call_per_series(tmp_path):
    rec = first(candidates(tmp_path / "c", MULTI)).recipe
    calls, pdf = run_script(tmp_path, MULTI, rec)
    assert pdf and len(calls) == 2
    assert calls[0]["yerr"] == [0.1] * 5 and calls[1]["yerr"] == [0.2] * 5
    assert calls[0]["y"] == [float(t) for t in range(1, 6)] and calls[1]["y"] == [
        2.0 * t for t in range(1, 6)
    ]


def test_multi_y_response_shape_and_override(project):
    _, client, figs, _ = project
    put(figs, "data/m.csv", MULTI)
    out = propose(client, render="lazy").get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err" and top["error_source"] == "column"
    assert top["error_columns"] == [
        {"y": "a_mean", "column": "a_sd", "kind": "sd", "stated": True},
        {"y": "b_mean", "column": "b_sd", "kind": "sd", "stated": True},
    ]
    assert top["error"] == "sd"  # 种类一致时顶层照给
    assert "error_column" not in top and "error_stated" not in top
    out = propose(client, render="lazy", answers={"error": "sem"}).get_json()
    top = out["items"][0]
    assert [e["kind"] for e in top["error_columns"]] == ["sem", "sem"]
    assert [e["stated"] for e in top["error_columns"]] == [False, False]
    assert top["error"] == "sem" and out["error_confirmed"] is True


def test_multi_y_with_different_kinds_has_no_top_level_error(project):
    _, client, figs, _ = project
    rows = [["time", "a_mean", "a_sd", "b_mean", "b_sem"]] + [
        [t, float(t), 0.1, 2.0 * t, 0.2] for t in range(1, 6)
    ]
    put(figs, "data/m.csv", rows)
    top = propose(client, render="lazy").get_json()["items"][0]
    assert "error" not in top and top["error_source"] == "column"
    assert [e["kind"] for e in top["error_columns"]] == ["sd", "sem"]


def test_single_y_recipes_scripts_and_dicts_are_unchanged(tmp_path):
    """单个 y 的写法没有变：`err` / `err_kind` 仍是字符串、脚本里 `ERR = 'sd'`、响应三个标量。"""
    res = candidates(tmp_path / "c", GROUPS)
    rec = first(res).recipe
    assert rec.err == "sd" and rec.err_kind == "sd" and rec.err_cols == ("sd",)
    d = rec.to_dict()
    assert d["err"] == "sd" and d["err_kind"] == "sd"
    _, _, entry = read(tmp_path / "d", GROUPS)
    assert "ERR = 'sd'" in tpl.render_recipe_script(entry, rec, data_rel="t.csv", stem="s").script


def test_single_y_response_keeps_the_three_scalars(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    top = propose(client, render="lazy").get_json()["items"][0]
    assert (top["error"], top["error_column"], top["error_stated"]) == ("sd", "sd", True)
    assert "error_columns" not in top


# =========================================================================== B2. 合画
def put_trend(figs, name, offset):
    rows = [["time", "mean", "sd"]] + [[t, 1.0 + offset + t, 0.1] for t in range(1, 6)]
    put(figs, f"data/{name}.csv", rows)


def test_merged_summarised_trends_draw_one_line_per_file_with_column_errors(project):
    _, client, figs, _ = project
    for i, name in enumerate(("ctrl", "drugA", "drugB")):
        put_trend(figs, name, i)
    tables = [{"path": f"{n}.csv"} for n in ("ctrl", "drugA", "drugB")]
    out = propose(client, tables=tables, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    top = out["items"][0]
    assert top["title_code"] == "line_err" and top["error_source"] == "column"
    assert top["values"]["x"] == "time" and top["values"]["group"] == out["group_column"]
    assert top["error_column"] == "sd" and top["error"] == "sd"
    print("MERGED TREND:", [i["title_code"] for i in out["items"]])


def test_merged_with_one_file_having_replicates_is_not_summarised(project):
    _, client, figs, _ = project
    put_trend(figs, "ctrl", 0)
    rows = [["time", "mean", "sd"]] + [[t, 2.0 + t, 0.1] for t in range(1, 6)] + [[3, 5.5, 0.2]]
    put(figs, "data/drugA.csv", rows)
    out = propose(
        client, tables=[{"path": "ctrl.csv"}, {"path": "drugA.csv"}], render="lazy"
    ).get_json()
    assert out["kind"] == "candidates"
    assert all("error_source" not in i or i["error_source"] != "column" for i in out["items"])
    assert "summarised_not_raw" not in {d["reason_code"] for d in out["dropped"]}


def test_merged_group_summaries_candidate_list(project):
    """类别形状合画：每份是一张「组 + 均值 + 误差」的小表；横轴取合成的文件列。把候选清单打出来给人看。"""
    _, client, figs, _ = project
    for i, name in enumerate(("expA", "expB")):
        put(
            figs,
            f"data/{name}.csv",
            [["group", "mean", "sd", "n"]]
            + [[g, 5.0 + i + k, 0.2, 6] for k, g in enumerate(("ctrl", "drugA", "drugB"))],
        )
    out = propose(
        client, tables=[{"path": "expA.csv"}, {"path": "expB.csv"}], render="lazy"
    ).get_json()
    assert out["kind"] == "candidates", out
    listing = [
        (i["title_code"], i["values"]["x"], i["values"]["group"], i.get("variant"))
        for i in out["items"]
    ]
    print("MERGED CATEGORY:", listing, "group_column=", out["group_column"])
    assert [i["title_code"] for i in out["items"]] == [
        "points_err",
        "bar_err",
    ]  # 不再有对汇总值算均值 / 分布的「只按文件比较」
    first_item = out["items"][0]
    assert first_item["title_code"] == "points_err"
    # x 是文件列、分组是原来的「group」列：每个（文件, 组）一行
    assert (
        first_item["values"]["x"] == out["group_column"]
        and first_item["values"]["group"] == "group"
    )


# =========================================================================== B3 / B4：明确不支持，且不连带剔掉别的备选
def test_large_tables_do_not_get_column_errors_and_keep_their_candidates(tmp_path, monkeypatch):
    rows = [["time", "mean", "sd"]] + [[t, 1.0 * t, 0.1] for t in range(1, 40)]
    plain = candidates(tmp_path / "small", rows, picks={"y": "mean"})
    assert first(plain).code == "line_err"
    monkeypatch.setattr(adf, "SMALL_ROWS", 10)  # 行数超过小表界线 → 大表读法
    big = candidates(tmp_path / "big", rows, picks={"y": "mean"})
    assert big.items and all(c.recipe.err is None for c in big.items)
    assert "summarised_not_raw" not in [c for c, _d in dropped_codes(big)]


def test_repeated_measures_with_a_confirmed_unit_are_not_summarised(tmp_path):
    rows = [["subject_id", "time", "weight", "sd"]]
    for sid in ("m1", "m2"):
        rows += [[sid, t, 20.0 + t, 0.5] for t in range(1, 6)]
    profile, values, _ = read(tmp_path, rows)
    confirmed = R.Confirmed("repeated", "subject_id")
    res = R.default_candidates(profile, confirmed=confirmed, values=values, picks={"y": "weight"})
    assert "line_units" in codes(res)
    assert all(c.recipe.err is None for c in res.items)
    assert "summarised_not_raw" not in [c for c, _d in dropped_codes(res)]
    # 同一张表，用户明确说是独立数据时每个位置有重复：也不是已汇总
    independent = R.default_candidates(
        profile, confirmed=R.Confirmed("independent"), values=values, picks={"y": "weight"}
    )
    assert all(c.recipe.err is None for c in independent.items)


def test_old_json_still_parses(tmp_path):
    """已经存在的单 y 配方 JSON（`err` 是字符串）照旧能读。"""
    raw = json.loads(
        json.dumps(
            {
                "table": "t.csv",
                "family": "comparison",
                "layers": [{"mark": "points", "summary": "none", "role": "main"}],
                "x": "group",
                "y": ["mean"],
                "err": "sd",
                "err_kind": "sd",
                "why": "w",
            }
        )
    )
    assert R.FigureRecipe.from_dict(raw).err_cols == ("sd",)


@needs_worker
def test_merged_summarised_trend_commits_and_verifies(project):
    _, client, figs, _ = project
    for i, name in enumerate(("ctrl", "drugA")):
        put_trend(figs, name, i)
    out = propose(
        client, tables=[{"path": "ctrl.csv"}, {"path": "drugA.csv"}], render="lazy"
    ).get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err"
    thumb = client.get(top["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    done = commit(client, out["session"], top["id"]).get_json()
    assert done["task"]["outcome"] == "completed" and done["data_check"] == "verified", done
    assert "ERR = 'sd'" in (figs / done["script"]).read_text(encoding="utf-8")
