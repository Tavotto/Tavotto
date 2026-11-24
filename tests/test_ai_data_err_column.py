# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""已汇总的表：把表里现成的误差列画成误差线（sciviz G-053 / G-065 / G-055；ADR 0178 修订 2026-11-24）。

判据的主语：

* **配对**（`paired_error`）：只有名字规则认得出、且配得唯一的才配；歧义 = 不配（宁可不画误差线，不猜）；
* **误差线画的就是那一列的数**：执行脚本里的数据准备函数 / 真执行生成的脚本，量 `ax.errorbar` 收到的 `yerr`，不看中间变量；
* **绝不把 `unknown` 当成 SD**：名字只说 `err` 的，响应里 `error == "unknown"`、不算 `error_stated`；用户点选的覆盖名字；
* **已汇总的表不再汇总**：均值 ± 误差、箱线、分布、均值线一个都不给，`dropped` 里记 `summarised_not_raw`；
* 有重复测量的表**行为不变**（误差列照旧被忽略，不画）。
"""

from __future__ import annotations

import csv
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_model as model,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import (  # noqa: F401
    WORKER_PY,
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


# =========================================================================== 材料
def write_csv(folder: Path, rows, name="t.csv") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    return path


def read(tmp_path, rows):
    """`(文件夹摘要, 带行数据的摘要, 条目)`。"""
    folder = tmp_path / "d"
    path = write_csv(folder, rows)
    profile = adf.profile_folder(folder)
    values = adf.inspect_data_source(path, None, v2=True, allow_large=True)
    return profile, values, profile.tables[0]


def candidates(tmp_path, rows, **kw):
    profile, values, _ = read(tmp_path, rows)
    res = R.default_candidates(profile, confirmed=R.Confirmed(), values=values, **kw)
    assert isinstance(res, R.Candidates), res
    return res


def codes(res):
    return [c.code for c in res.items]


DOSE = [["dose_uM", "mean_response", "sd"]] + [
    [d, round(10 + 8 * i, 1), round(0.8 + 0.1 * i, 2)]
    for i, d in enumerate((0.1, 0.3, 1, 3, 10, 30))
]
GROUPS = [
    ["group", "mean", "sd", "n"],
    ["ctrl", 1.2, 0.2, 6],
    ["drugA", 2.4, 0.3, 6],
    ["drugB", 3.1, 0.4, 5],
]
XYERR = [["x", "y", "err"]] + [
    [i, round(2.0 * i + 1, 2), round(0.1 * (i + 1), 2)] for i in range(6)
]


def with_column(rows, name, values):
    return [rows[0] + [name]] + [r + [v] for r, v in zip(rows[1:], values)]


# =========================================================================== 1. 配对
@pytest.mark.parametrize(
    "rows, y, axes, want",
    [
        (DOSE, "mean_response", ("dose_uM",), "sd"),
        (DOSE, "mean_response", (), "sd"),  # 名字里的 mean 也能唯一配上
        ([["mass", "mass_sd"], [1.0, 0.1], [2.0, 0.2], [3.0, 0.1]], "mass", (), "mass_sd"),
        (
            [["t", "mean_y", "y_sem"], [1, 2.0, 0.1], [2, 3.0, 0.1], [3, 4.0, 0.2]],
            "mean_y",
            ("t",),
            "y_sem",
        ),
        (XYERR, "y", ("x",), "err"),
        (XYERR, "y", (), None),  # x 与 y 都可能是测量列：歧义 = 不配
        (
            [["mean", "sd", "sem"], [1.0, 0.1, 0.05], [2.0, 0.2, 0.1], [3.0, 0.1, 0.05]],
            "mean",
            (),
            None,
        ),  # 两个误差列抢同一列
        (
            [["mean_a", "mean_b", "sd"], [1.0, 2.0, 0.1], [2.0, 3.0, 0.2], [3.0, 4.0, 0.1]],
            "mean_a",
            (),
            None,
        ),  # 谁的 sd？
        (
            [["time_min", "SEM_depth_nm"], [0, 1.0], [5, 2.0], [10, 3.0]],
            "SEM_depth_nm",
            ("time_min",),
            None,
        ),  # 扫描电镜：不是误差列
        (GROUPS, "mean", (), "sd"),
        (GROUPS, "n", (), None),  # n 是样本量，不是测量列
        (DOSE, "sd", ("dose_uM",), None),  # 误差列自己不是任何列的测量列
    ],
)
def test_paired_error(tmp_path, rows, y, axes, want):
    _, values, _ = read(tmp_path, rows)
    assert R.paired_error(values, y, axes) == want


def test_error_kind_shares_the_companion_logic_with_pairing():
    """配对读的是 `adf.error_match`，与 `error_kind` 同一份名字规则：二者对「是不是误差列」不会分歧。"""
    for name, others in (
        ("mass_sd", ["mass"]),
        ("sd", ["mean"]),
        ("SEM_depth_nm", ["time"]),
        ("err", []),
    ):
        assert adf.error_match(name, others)[0] == adf.error_kind(name, others)
    assert adf.error_match("mass_sd", ["mass", "x"]) == ("sd", ["mass"])
    assert adf.error_match("sd", ["mean", "x"]) == ("sd", ["mean"])
    assert adf.error_match("sem", ["dose", "response"]) == (None, [])


def test_sample_size_columns_only_count_next_to_an_error_column(tmp_path):
    _, values, _ = read(tmp_path, GROUPS)
    assert R.sample_size_columns(values) == ["n"]
    _, plain, _ = read(tmp_path, [["group", "n"], ["a", 3], ["b", 4], ["c", 5]])
    assert R.sample_size_columns(plain) == []  # 没有误差列陪着：照旧是普通数值列
    _, upper, _ = read(
        tmp_path, [["g", "mean", "sd", "Count"], ["a", 1.0, 0.1, 3], ["b", 2.0, 0.1, 3]]
    )
    assert R.sample_size_columns(upper) == ["Count"]


# =========================================================================== 2. 配方：字段 / 往返 / 逐字不变
def base_recipe(**kw):
    d = {
        "table": "t.csv",
        "family": "trend",
        "layers": [{"mark": "points", "summary": "none", "role": "main"}],
        "x": "dose_uM",
        "y": ["mean_response"],
        "why": "w",
    }
    d.update(kw)
    return R.FigureRecipe.from_dict(d)


def test_recipe_round_trips_err_and_err_kind():
    rec = base_recipe(err="sd", err_kind="sem")
    assert rec.err == "sd" and rec.err_kind == "sem"
    out = rec.to_dict()
    assert out["err"] == "sd" and out["err_kind"] == "sem"
    assert R.FigureRecipe.from_dict(out) == rec


def test_no_err_means_identical_dict_and_script(tmp_path):
    rec = base_recipe()
    assert "err" not in rec.to_dict() and "err_kind" not in rec.to_dict()
    _, _, entry = read(tmp_path, DOSE)
    script = tpl.render_recipe_script(entry, rec, data_rel="t.csv", stem="s").script
    assert "ERR" not in script and "errorbar" not in script and "comparison_error" not in script
    assert "trend_data(header, rows, X, YS, GROUP, FACET, UNIT, X_IS_DATE)" in script
    assert "err" not in json.dumps(tpl.parse_source_comment(script) or {})
    # 来源注释里的配方：没有 err 就没有这两个键
    line = next(ln for ln in script.splitlines() if ln.startswith(tpl.SOURCE_COMMENT_PREFIX))
    assert '"err"' not in line and '"err_kind"' not in line


def test_err_is_written_into_the_source_comment_and_the_script(tmp_path):
    rec = base_recipe(err="sd", err_kind="sd")
    _, _, entry = read(tmp_path, DOSE)
    script = tpl.render_recipe_script(entry, rec, data_rel="t.csv", stem="s").script
    line = next(ln for ln in script.splitlines() if ln.startswith(tpl.SOURCE_COMMENT_PREFIX))
    meta = json.loads(line[len(tpl.SOURCE_COMMENT_PREFIX) :])
    assert meta["recipe"]["err"] == "sd" and meta["recipe"]["err_kind"] == "sd"
    assert (
        "ERR = 'sd'" in script
        and "trend_data(header, rows, X, YS, GROUP, FACET, UNIT, X_IS_DATE, ERR)" in script
    )
    assert "fmt='none'" in script and "yerr=ee" in script


@pytest.mark.parametrize(
    "extra, code",
    [
        ({"err": 3}, "recipe_malformed"),
        ({"err": "sd", "err_kind": "se"}, "recipe_unknown_value"),
        ({"err": "sd", "err_kind": 1}, "recipe_malformed"),
        ({"err": ""}, "recipe_malformed"),
    ],
)
def test_malformed_err_fields_are_rejected(extra, code):
    with pytest.raises(R.RecipeError) as exc:
        base_recipe(**extra)
    assert exc.value.code == code


# =========================================================================== 3. 校验
def problems(tmp_path, rows, **kw):
    profile, values, _ = read(tmp_path, rows)
    return [
        (p.code, p.field)
        for p in R.validate_recipe(
            profile, base_recipe(**kw), confirmed=R.Confirmed(), values=values
        )
    ]


def test_a_valid_err_recipe_passes(tmp_path):
    assert problems(tmp_path, DOSE, err="sd", err_kind="sd") == []


@pytest.mark.parametrize(
    "kw, want",
    [
        ({"err": "sd"}, ("summary_missing", "err_kind")),  # G-065：误差线必须写明哪一种
        ({"err_kind": "sd"}, ("roles_invalid", "err_kind")),
        ({"err": "nope", "err_kind": "sd"}, ("column_not_found", "err")),
        ({"err": "dose_uM", "err_kind": "sd"}, ("roles_invalid", "err")),  # 不能是 x
        ({"err": "mean_response", "err_kind": "sd"}, ("roles_invalid", "err")),  # 不能是 y
        (
            {"err": "sd", "err_kind": "sd", "y": ["mean_response", "dose_uM"]},
            ("roles_invalid", "err"),
        ),  # 只许一个 y
        (
            {
                "err": "sd",
                "err_kind": "sd",
                "layers": [
                    {"mark": "points", "summary": "none", "role": "main"},
                    {"mark": "band", "summary": "mean_sd", "role": "overlay"},
                ],
            },
            ("mark_not_allowed", "layers[1].mark"),  # 不能再叠算出来的误差带
        ),
        (
            {
                "err": "sd",
                "err_kind": "sd",
                "layers": [{"mark": "line", "summary": "mean_sd", "role": "main"}],
            },
            ("mark_not_allowed", "layers[0].mark"),  # 带汇总 = 对汇总值再汇总
        ),
    ],
)
def test_err_structure_rejections(tmp_path, kw, want):
    got = problems(tmp_path, DOSE, **kw)
    assert want in got, got


def test_err_must_be_a_numeric_column(tmp_path):
    rows = [r + ["lo" if i else "note"] for i, r in enumerate(DOSE)]
    got = problems(tmp_path, rows, err="note", err_kind="sd")
    assert ("column_type_mismatch", "err") in got


def test_err_family_and_exclusive_facts(tmp_path):
    got = problems(tmp_path, DOSE, family="relationship", err="sd", err_kind="sd")
    assert ("roles_invalid", "err") in got
    got = problems(tmp_path, DOSE, err="sd", err_kind="sd", paired=True)
    assert ("roles_invalid", "err") in got


def test_negative_error_values_are_rejected_but_empty_ones_are_not(tmp_path):
    neg = [list(r) for r in DOSE]
    neg[3][2] = -0.2
    assert ("error_values_negative", "err") in problems(tmp_path, neg, err="sd", err_kind="sd")
    gap = [list(r) for r in DOSE]
    gap[3][2] = ""
    gap[4][2] = "NaN"
    assert problems(tmp_path, gap, err="sd", err_kind="sd") == []
    # y 缺失的行不画，它的误差是什么都不管
    ymiss = [list(r) for r in DOSE]
    ymiss[2][1] = ""
    ymiss[2][2] = -5.0
    assert problems(tmp_path, ymiss, err="sd", err_kind="sd") == []


def test_replicates_are_rejected(tmp_path):
    rows = [list(r) for r in DOSE] + [[0.1, 11.0, 0.9]]
    assert ("error_cells_not_unique", "err") in problems(tmp_path, rows, err="sd", err_kind="sd")


def test_comparison_err_accepts_a_unique_label_column_and_rejects_repeats(tmp_path):
    rec = dict(family="comparison", x="group", y=["mean"], err="sd", err_kind="sd")
    profile, values, _ = read(tmp_path, GROUPS)
    ok = R.validate_recipe(profile, base_recipe(**rec), confirmed=R.Confirmed(), values=values)
    assert ok == []
    # 同一个类别两行：每个位置只能有一行
    profile, values, _ = read(tmp_path, GROUPS + [["ctrl", 1.3, 0.2, 6]])
    bad = R.validate_recipe(profile, base_recipe(**rec), confirmed=R.Confirmed(), values=values)
    assert ("error_cells_not_unique", "err") in [(p.code, p.field) for p in bad]
    # 不带 err 的同一份配方：文字列不在 groups 里，仍是列类型不对（放宽只对 err 配方）
    plain = dict(rec)
    plain.pop("err"), plain.pop("err_kind")
    profile, values, _ = read(tmp_path, GROUPS)
    out = R.validate_recipe(profile, base_recipe(**plain), confirmed=R.Confirmed(), values=values)
    assert ("column_type_mismatch", "x") in [(p.code, p.field) for p in out]


# =========================================================================== 4. 备选
def first(res):
    return res.items[0]


def test_dose_mean_sd_gives_line_err_first_with_the_sd_kind(tmp_path):
    res = candidates(tmp_path, DOSE, picks={"y": "unsure"})
    top = first(res)
    assert top.code == "line_err" and top.recipe.family == "trend"
    assert (top.recipe.err, top.recipe.err_kind) == ("sd", "sd")
    assert top.recipe.y == ("mean_response",) and top.recipe.x == "dose_uM"
    assert [c.code for c in res.items][:1] == ["line_err"]
    # 汇总值不再汇总：没有任何带汇总方式 / 分布的备选
    for c in res.items:
        assert R.summarising_mark(c.recipe) is None or c.recipe.err is not None, c.code
    drops = [(p.code, p.detail) for d in res.dropped for p in d.problems]
    assert ("summarised_not_raw", "line:mean_sd") in drops
    # 只画给定点的备选仍在（散点 / 折线），排在 `*_err` 之后
    assert {"line_points", "scatter_fit"} & set(codes(res))


def test_group_mean_sd_n_gives_points_err_first_and_n_is_never_an_axis(tmp_path):
    res = candidates(tmp_path, GROUPS)
    assert codes(res) == ["points_err"]
    rec = first(res).recipe
    assert rec.family == "comparison" and rec.x == "group" and rec.y == ("mean",)
    assert (rec.err, rec.err_kind) == ("sd", "sd")
    for c in res.items:
        assert "n" not in c.recipe.y and c.recipe.x != "n"
    drops = {p.detail for d in res.dropped for p in d.problems if p.code == "summarised_not_raw"}
    assert {"hist", "ecdf", "box", "density"} <= drops
    assert res.roles["y"] == ["mean"]


def test_n_named_column_is_never_y_or_x_even_when_it_is_the_only_other_number(tmp_path):
    rows = [
        ["group", "mean", "sd", "n"],
        ["a", 1.0, 0.1, 3],
        ["b", 2.0, 0.1, 4],
        ["c", 3.0, 0.2, 5],
    ]
    profile, _, entry = read(tmp_path, rows)
    roles = R.resolve_roles(entry, None, {})
    assert isinstance(roles, R.Roles) and roles.ys == ("mean",) and roles.x_ord is None
    # 但用户自己点了 n 就照点
    named = R.resolve_roles(entry, None, {"y": "n"})
    assert isinstance(named, R.Roles) and named.ys == ("n",)


def test_unknown_error_kind_is_kept_unknown(tmp_path):
    res = candidates(tmp_path, XYERR, picks={"y": "unsure"})
    top = first(res).recipe
    assert top.err == "err" and top.err_kind == "unknown"  # 绝不是 sd


@pytest.mark.parametrize(
    "name, kind",
    [("sd", "sd"), ("stderr", "sem"), ("标准差", "sd"), ("err", "unknown"), ("误差", "unknown")],
)
def test_the_kind_comes_from_the_column_name_only(tmp_path, name, kind):
    rows = [["dose", "mean_y", name]] + [[i, 1.0 + i, 0.1] for i in range(1, 7)]
    res = candidates(tmp_path, rows, picks={"y": "unsure"})
    assert first(res).recipe.err == name and first(res).recipe.err_kind == kind


def test_a_table_with_replicates_is_unchanged(tmp_path):
    """有重复测量：误差列照旧被忽略、不画——候选与「没有这一列」的同一张表逐个相同。"""
    rows = [["group", "value_mV"]]
    for gi, g in enumerate(("ctrl", "drugA", "drugB")):
        for k in range(6):
            rows.append([g, round(2 + gi * 1.5 + k * 0.3, 3)])
    plain = candidates(tmp_path / "a", rows)
    sd = [round(0.1 + 0.01 * i, 3) for i in range(len(rows) - 1)]
    noisy = candidates(tmp_path / "b", with_column(rows, "sd", sd))
    assert codes(plain) == codes(noisy) and codes(plain)
    assert all(c.recipe.err is None for c in noisy.items)
    assert [(c.recipe.y, c.recipe.x) for c in plain.items] == [
        (c.recipe.y, c.recipe.x) for c in noisy.items
    ]
    assert not [p for d in noisy.dropped for p in d.problems if p.code == "summarised_not_raw"]
    # 趋势：同一个 x 有重复测量
    trend = [["time", "y"]]
    for t in range(1, 6):
        for k in range(3):
            trend.append([t, 1.0 * t + 0.1 * k])
    plain_t = candidates(tmp_path / "c", trend, picks={"y": "y"})
    noisy_t = candidates(
        tmp_path / "d", with_column(trend, "sd", [0.2] * (len(trend) - 1)), picks={"y": "y"}
    )
    assert codes(plain_t) == codes(noisy_t)
    assert all(c.recipe.err is None for c in noisy_t.items)


def test_pairing_ambiguity_draws_nothing_extra(tmp_path):
    rows = [["x", "mean_a", "mean_b", "sd"]] + [[i, 1.0 + i, 2.0 + i, 0.1] for i in range(1, 7)]
    res = candidates(tmp_path, rows, picks={"y": "mean_a"})
    assert all(c.recipe.err is None for c in res.items)
    assert not [p for d in res.dropped for p in d.problems if p.code == "summarised_not_raw"]


def test_two_categories_one_row_each_cell_also_counts_as_summarised(tmp_path):
    rows = [["drug", "dose", "mean", "sd"]]
    for d in ("A", "B"):
        for dose in ("lo", "hi", "max"):
            rows.append([d, dose, 1.0 + len(d) + len(dose), 0.1])
    # `drug` / `dose` 各自有重复取值（是分组列）；每个（drug, dose）只有一行
    res = candidates(tmp_path, rows, picks={"y": "mean", "group": "unsure"})
    assert first(res).code == "points_err"
    assert first(res).recipe.x in ("drug", "dose") and first(res).recipe.group in ("drug", "dose")


# =========================================================================== 4b. 经 propose 端点：响应里的字段
def session_of(out):
    return next(s for s in P._SESSIONS.values() if s.id == out["session"])


def test_propose_reports_the_error_facts_for_a_column_error(project):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", DOSE)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    top = out["items"][0]
    assert top["title_code"] == "line_err"
    assert top["error"] == "sd" and top["error_source"] == "column"
    assert top["error_column"] == "sd" and top["error_stated"] is True
    assert "error_confirmed" not in out  # 用户没点过
    for item in out["items"][1:]:
        assert "error" not in item and "error_source" not in item and "error_stated" not in item
    codes_dropped = {d["reason_code"] for d in out["dropped"]}
    assert "summarised_not_raw" in codes_dropped
    # 数：依据里画了 6 行，没有没画的行
    assert top["facts"]["drawn"] == 6 and top["unplotted"]["count"] == 0


def test_unknown_kind_is_not_stated_and_never_sd(project):
    _, client, figs, _ = project
    put(figs, "data/xy.csv", XYERR)
    out = propose(client, render="lazy").get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err" and top["error"] == "unknown"
    assert top["error_source"] == "column" and top["error_column"] == "err"
    assert "error_stated" not in top  # 名字没说：要用户确认


def test_the_users_choice_overrides_the_name_and_confirms(project):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", DOSE)
    out = propose(client, render="lazy", answers={"error": "sem"}).get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err" and top["error"] == "sem"
    assert (
        top["error_source"] == "column" and "error_stated" not in top
    )  # 现在是用户点的，不是列名说的
    assert out["error_confirmed"] is True
    rec = next(iter(session_of(out).items.values())).recipe
    assert rec.err == "sd" and rec.err_kind == "sem"  # 列还是那一列，只是写明了是什么
    # 名字没说的，用户点 sd 才是 sd
    put(figs, "data/xy.csv", XYERR)
    out = propose(client, render="lazy", answers={"error": "sd", "table": "xy.csv"}).get_json()
    assert out["items"][0]["error"] == "sd" and out["error_confirmed"] is True


def test_group_summary_table_propose(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    assert [i["title_code"] for i in out["items"]] == ["points_err"]
    item = out["items"][0]
    assert item["values"]["x"] == "group" and item["values"]["y"] == ["mean"]
    assert item["error"] == "sd" and item["error_stated"] is True
    assert item["facts"]["drawn"] == 3 and item["facts"]["groups"] == 3
    assert out["roles"]["y"] == ["mean"]


def test_a_missing_error_cell_still_draws_the_point_and_is_not_unplotted(project):
    _, client, figs, _ = project
    rows = [list(r) for r in DOSE]
    rows[3][2] = ""
    rows[5][2] = "NaN"
    put(figs, "data/dr.csv", rows)
    out = propose(client, render="lazy").get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err" and top["unplotted"]["count"] == 0
    assert top["facts"]["drawn"] == 6 and top["facts"]["skipped_missing"] == 0
    item = next(iter(session_of(out).items.values()))
    assert P.unplotted_rows(adf.inspect_data_source(figs / "data/dr.csv"), item.recipe) == []


def test_a_missing_value_row_is_unplotted_but_its_error_does_not_matter(project):
    _, client, figs, _ = project
    rows = [list(r) for r in DOSE]
    rows[2][1] = ""  # y 缺失：这一行没有点
    put(figs, "data/dr.csv", rows)
    top = propose(client, render="lazy").get_json()["items"][0]
    assert top["title_code"] == "line_err"
    assert top["facts"]["drawn"] == 5


# =========================================================================== 5. 误差线画的就是那一列的数
def draw_data(tmp_path, rows, recipe):
    """父进程用来算期望值的同一批数据准备函数——脚本运行时调的就是它们。"""
    _, values, _ = read(tmp_path, rows)
    if recipe.family == "trend":
        d = tpl.trend_data(
            values.columns,
            values.rows,
            recipe.x,
            list(recipe.y),
            recipe.group,
            recipe.facet,
            recipe.unit,
            False,
            recipe.err,
        )
        return d, values
    d = tpl.comparison_data(
        values.columns,
        values.rows,
        recipe.x,
        recipe.y[0],
        recipe.group,
        recipe.facet,
        recipe.order,
        recipe.err,
    )
    return d, values


def test_trend_error_points_are_the_columns_values(tmp_path):
    rows = [list(r) for r in DOSE]
    rows[3][2] = ""  # 缺失：点在、没有误差线
    res = candidates(tmp_path, rows, picks={"y": "unsure"})
    d, values = draw_data(tmp_path, rows, first(res).recipe)
    ex, ey, ee = tpl.trend_error_points(d, "", "", "mean_response")
    assert ee == [
        r[2] for i, r in enumerate(rows[1:]) if i != 2
    ]  # 第 3 个点（行 index 2）没有误差线
    assert ey == [r[1] for i, r in enumerate(rows[1:]) if i != 2]
    assert ex == [r[0] for i, r in enumerate(rows[1:]) if i != 2]
    # 点那一层不受影响：6 个点都在
    xs, ys = tpl.finite_pairs(*tpl.trend_pool(d, "", "", "mean_response"))
    assert len(xs) == 6


def test_comparison_error_points_are_the_columns_values(tmp_path):
    res = candidates(tmp_path, GROUPS)
    d, _ = draw_data(tmp_path, GROUPS, first(res).recipe)
    pos, centers, errs = tpl.comparison_error_points(d, "", "", 0, 1)
    assert pos == [0.0, 1.0, 2.0]
    assert centers == [1.2, 2.4, 3.1] and errs == [0.2, 0.3, 0.4]


def test_negative_or_text_error_cells_make_no_bar_at_run_time(tmp_path):
    """校验在出图前拒绝过负数；数据之后又被改了，脚本也不会让 matplotlib 因为负的 yerr 崩掉。"""
    rows = [list(r) for r in GROUPS]
    rows[1][2] = -0.2
    rows[2][2] = "n/a"
    rec = first(candidates(tmp_path, GROUPS)).recipe
    d, _ = draw_data(tmp_path, rows, rec)
    assert tpl.comparison_error_points(d, "", "", 0, 1)[2] == [0.4]


def run_script(tmp_path, rows, recipe):
    """真执行生成的脚本，记下 `Axes.errorbar` 收到的参数（不看中间变量）。"""
    folder = tmp_path / "run"
    write_csv(folder, rows, "t.csv")
    profile = adf.profile_folder(folder)
    entry = profile.tables[0]
    rendered = tpl.render_recipe_script(entry, recipe, data_rel="t.csv", stem="fig")
    (folder / rendered.runtime_name).write_bytes(rendered.runtime)
    (folder / "fig.py").write_text(rendered.script, encoding="utf-8")
    driver = (
        "import json, runpy, sys\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "from matplotlib.axes import Axes\n"
        "calls = []\n"
        "orig = Axes.errorbar\n"
        "def spy(self, *a, **k):\n"
        "    calls.append({'x': [float(v) for v in a[0]], 'y': [float(v) for v in a[1]],\n"
        "                  'yerr': [float(v) for v in k['yerr']], 'fmt': k.get('fmt')})\n"
        "    return orig(self, *a, **k)\n"
        "Axes.errorbar = spy\n"
        f"sys.path.insert(0, {str(folder)!r})\n"
        f"runpy.run_path({str(folder / 'fig.py')!r}, run_name='__main__')\n"
        "print('CALLS' + json.dumps(calls))\n"
    )
    done = subprocess.run(
        [str(WORKER_PY or sys.executable), "-c", driver],
        cwd=str(folder),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    line = next(ln for ln in done.stdout.splitlines() if ln.startswith("CALLS"))
    return json.loads(line[len("CALLS") :]), (folder / "fig.pdf").is_file()


@needs_worker
def test_the_generated_trend_script_calls_errorbar_with_the_columns_values(tmp_path):
    rows = [list(r) for r in DOSE]
    rows[3][2] = ""
    rec = first(candidates(tmp_path / "c", rows, picks={"y": "unsure"})).recipe
    calls, pdf = run_script(tmp_path, rows, rec)
    assert pdf
    assert len(calls) == 1 and calls[0]["fmt"] == "none"
    assert calls[0]["yerr"] == [r[2] for i, r in enumerate(rows[1:]) if i != 2]
    assert calls[0]["y"] == [r[1] for i, r in enumerate(rows[1:]) if i != 2]


@needs_worker
def test_the_generated_comparison_script_calls_errorbar_with_the_columns_values(tmp_path):
    rec = first(candidates(tmp_path / "c", GROUPS)).recipe
    calls, pdf = run_script(tmp_path, GROUPS, rec)
    assert pdf and len(calls) == 1
    assert calls[0]["yerr"] == [0.2, 0.3, 0.4] and calls[0]["y"] == [1.2, 2.4, 3.1]
    assert calls[0]["x"] == [0.0, 1.0, 2.0]


# =========================================================================== 5b. 指纹：核什么、不核什么
def test_coverage_says_the_column_error_bars_are_not_verified(tmp_path):
    rec = first(candidates(tmp_path, DOSE, picks={"y": "unsure"})).recipe
    cov = tpl.fingerprint_coverage(rec)
    by_mark = {(c.mark, c.checked, c.reason) for c in cov.layers}
    assert ("points", True, "") in by_mark and ("line", True, "") in by_mark
    assert ("errorbar", False, "errorbar_has_no_label") in by_mark
    assert cov.raw_checked and cov.any_checked
    # 期望指纹只有点与线（误差线没有 label，不核）；与没有 err 的同一份配方逐项相同
    _, values, _ = read(tmp_path, DOSE)
    got = tpl.expected_fingerprints(values, rec)
    from dataclasses import replace

    assert got == tpl.expected_fingerprints(values, replace(rec, err=None, err_kind=None))
    assert {e["label"].split(" |")[0] for e in got} == {"points", "line"}


def test_template_refuses_what_it_cannot_align(tmp_path):
    rec = first(candidates(tmp_path, DOSE, picks={"y": "unsure"})).recipe
    _, _, entry = read(tmp_path, DOSE)
    from dataclasses import replace

    with pytest.raises(tpl.TemplateError):
        tpl.render_recipe_script(
            entry, replace(rec, family="relationship"), data_rel="t.csv", stem="s"
        )
    with pytest.raises(tpl.TemplateError):
        tpl.render_recipe_script(entry, replace(rec, err="nope"), data_rel="t.csv", stem="s")


# =========================================================================== 6. 端到端（真 app + 真 safe worker）
@needs_worker
def test_line_err_end_to_end_renders_commits_and_verifies_the_points(project):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", DOSE)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    top = out["items"][0]
    assert top["title_code"] == "line_err"
    thumb = client.get(top["thumbnail_url"])  # 真 worker 渲染；点与线的指纹核对过
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    c = commit(client, out["session"], top["id"])
    assert c.status_code == 200, c.get_json()
    done = c.get_json()
    assert done["task"]["outcome"] == "completed", done
    # 点与线是可核的层 → verified；误差线本身核不了（没有 label），不折算成「误差线已核」
    assert done["data_check"] == "verified", done
    layers = {(layer["mark"], layer["checked"]) for layer in top["coverage"]["layers"]}
    assert ("errorbar", False) in layers and ("points", True) in layers
    script = (figs / done["script"]).read_text(encoding="utf-8")
    assert "ERR = 'sd'" in script and "yerr=ee" in script
    assert checks_of(done["task"]["task_id"])["render.figure"] == ("verified", None)
    assert done["unplotted"]["count"] == 0


@needs_worker
def test_points_err_end_to_end(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    out = propose(client, render="lazy").get_json()
    top = out["items"][0]
    thumb = client.get(top["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    done = commit(client, out["session"], top["id"]).get_json()
    assert done["task"]["outcome"] == "completed" and done["data_check"] == "verified", done
    assert "comparison_error_points" in (figs / done["script"]).read_text(encoding="utf-8")


# =========================================================================== 7. 算出来的误差线里 n < 2 的位置
def test_error_missing_for_lists_the_groups_with_a_single_value(project):
    _, client, figs, _ = project
    rows = [["group", "value_mV"]]
    for g, n in (("ctrl", 6), ("drugA", 1), ("drugB", 5), ("drugC", 1)):
        rows += [[g, round(1.0 + 0.1 * k, 2)] for k in range(n)]
    put(figs, "data/m.csv", rows)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    by = {i["title_code"]: i for i in out["items"]}
    assert by["bar_error"]["error_source"] == "computed"
    assert by["bar_error"]["error_missing_for"] == ["drugA", "drugC"]
    for code, item in by.items():
        if "error_missing_for" in item:
            assert item["error_source"] == "computed", code
    # 没有误差线的备选没有这一项，也没有 error_source
    plain = by.get("box_points") or by.get("points_mean")
    assert plain is not None and "error_missing_for" not in plain


def test_error_missing_for_trend_names_the_x_values(tmp_path):
    rows = [["time", "y"]]
    for t, n in ((1, 3), (2, 1), (3, 3), (4, 3)):
        rows += [[t, 1.0 * t + 0.1 * k] for k in range(n)]
    _, values, _ = read(tmp_path, rows)
    rec = R.FigureRecipe.from_dict(
        {
            "table": "t.csv",
            "family": "trend",
            "layers": [
                {"mark": "line", "summary": "mean_sd", "role": "main"},
                {"mark": "band", "summary": "mean_sd", "role": "overlay"},
            ],
            "x": "time",
            "y": ["y"],
            "why": "w",
        }
    )
    assert P.error_missing_for(values, rec) == ["2"]
    # 同一份配方换成 SEM / CI：n < 2 都没有误差带
    for kind in ("sem", "ci95"):
        assert P.error_missing_for(values, P.with_error_summary(rec, kind)) == ["2"]
    # 算好的误差（取自列）与不带误差的：没有
    from dataclasses import replace

    assert P.error_missing_for(values, replace(rec, layers=rec.layers[:1])) == []


def test_error_missing_for_every_group_has_enough_values_is_omitted(project):
    _, client, figs, _ = project
    rows = [["group", "value_mV"]] + [[g, 1.0 + 0.1 * k] for g in ("a", "b", "c") for k in range(4)]
    put(figs, "data/m.csv", rows)
    out = propose(client, render="lazy").get_json()
    assert all("error_missing_for" not in i for i in out["items"])


# =========================================================================== 8. 图例与 fit_fonts 认得新的 artist
def test_legend_sees_column_error_bars_as_data_and_fit_fonts_keeps_working():
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    import warnings

    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    def make(width_mm):
        fig, ax = plt.subplots(figsize=(width_mm / 25.4, width_mm / 25.4 * 0.75))
        xs = [0.0, 1.0, 2.0, 3.0]
        ys = [1.0, 2.0, 3.0, 4.0]
        ax.scatter(xs, ys, s=9, color="C0")
        ax.errorbar(xs, ys, yerr=[0.6] * 4, fmt="none", ecolor="C0", elinewidth=1, capsize=2)
        ax.legend(handles=[Patch(facecolor="C0", label="group A")], frameon=False)
        fig.tight_layout()
        return fig, ax

    with plt.rc_context({"font.size": 9, "legend.fontsize": 9}), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fig, ax = make(80)
        # 误差线的竖线（LineCollection）与帽（Line2D 记号）都算数据：把图例摆到误差线上就算压到了
        leg = ax.legend(["group A"], loc="center", bbox_to_anchor=(0.5, 0.5), frameon=False)
        ax.figure.draw_without_rendering()
        assert tpl.legend_hits_data(ax, leg) is True
        # 只有误差线的竖线压着图例时同样算（去掉散点与线）
        fig2, ax2 = plt.subplots(figsize=(3, 2.25))
        dense = [0.3 + 0.01 * i for i in range(41)]  # 密密的竖线：图例的文字一定落在某几根上
        ax2.errorbar(dense, [1.0] * 41, yerr=[0.9] * 41, fmt="none", ecolor="k")
        ax2.set_xlim(0, 1)
        ax2.set_ylim(0, 2)
        hit = ax2.legend(["x"], loc="center", frameon=False)
        fig2.draw_without_rendering()
        assert tpl.legend_hits_data(ax2, hit) is True
        # `fit_fonts` 把压到误差线的图例挪开，之后不再压
        fig3, ax3 = make(80)
        ax3.legend(["group A"], loc="center", bbox_to_anchor=(0.5, 0.5), frameon=False)
        tpl.fit_fonts(fig3)
        fig3.draw_without_rendering()
        assert not tpl.legend_hits_data(ax3, ax3.get_legend())
        tpl.fit_fonts(fig3, 6.0)  # 给了字号下限的收尾同样能跑完
        plt.close("all")


# =========================================================================== 9. 模型写不出 err
def test_a_model_recipe_with_err_is_dropped(tmp_path):
    profile, values, _ = read(tmp_path, DOSE)
    raw = base_recipe(err="sd", err_kind="sd").to_dict()
    value = {"recipes": [raw], "extra": 0}
    kept, dropped, _moves = model.parse_recipes(
        profile, value, confirmed=R.Confirmed(), values=values
    )
    assert kept == () and [d.codes for d in dropped] == [("recipe_unknown_key",)]


# =========================================================================== 10. 样本量列只在「误差列明确配着另一列」时才让位
def test_count_that_is_the_measurement_stays_a_measurement(tmp_path):
    rows = [["group", "count", "sd"]]
    for gi, g in enumerate(("a", "b", "c")):
        rows += [[g, 10 + gi + k, 0.5] for k in range(4)]
    _, values, entry = read(tmp_path, rows)
    assert R.sample_size_columns(values) == []
    roles = R.resolve_roles(entry, None, {})
    assert isinstance(roles, R.Roles) and roles.ys == ("count",)  # sd 没有被放回来


def test_time_count_sd_keeps_time_as_x_and_does_not_guess_the_pairing(tmp_path):
    rows = [["time", "count", "sd"]] + [[t, 5 + t, 0.5] for t in range(1, 7)]
    _, values, _ = read(tmp_path, rows)
    assert R.sample_size_columns(values) == []
    res = candidates(tmp_path, rows, picks={"y": "unsure"})
    top = first(res).recipe
    assert (
        top.y == ("count",) and top.x == "time" and top.err is None
    )  # 唯一的测量列叫 count：不替它配误差列


def test_unique_label_axis_with_a_negative_error_falls_back_to_old_behaviour(tmp_path):
    rows = [list(r) for r in GROUPS]
    rows[2][2] = -0.3
    res = candidates(tmp_path, rows)
    assert all(c.recipe.err is None for c in res.items)
    assert not [p for d in res.dropped for p in d.problems if p.code == "summarised_not_raw"]
