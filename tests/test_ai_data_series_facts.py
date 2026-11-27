# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""多个 y 的趋势图：统计依据按**每条线**数，不用第一个 y 冒充整张图（ADR 0178 修订 2026-11-27）。

判据的主语：**每个 y 各自的有效值**——谁的、哪个 y、哪些行。比法是拿出图用的数据准备函数（`tpl.trend_data` / `trend_error_points`）直接数一遍，
再与 `series_facts` 对；真脚本那一层把生成的脚本跑起来，量 `Axes.errorbar` 收到的坐标 / 误差个数。
"""

from __future__ import annotations

import dataclasses
import math
import socket

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_err_column import read, run_script
from tests.test_ai_data_propose import needs_worker, project, propose, put  # noqa: F401


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
HEAD = ["time", "a_mean", "a_sd", "b_mean", "b_sem"]
# 审计反例 A（最小表只够显式配方；真实候选要够行数，见 A_LONG）
A = [HEAD, [1, 10, 0.1, "", ""], [2, "", "", 20, 0.2], [3, 12, "", 22, 0.3], [4, "", 0.9, 24, ""]]
# 同一份形状加两行（a 有 3 个有效值、b 有 4 个）：够行数，真实候选生成会出 line_err / line_points
A_LONG = A + [[5, 13, 0.1, "", ""], [6, "", "", 26, 0.3]]
B = [
    ["time", "a_mean", "a_sd", "b_mean", "b_sd"],
    [1, 10, 0.1, "", ""],
    [2, 11, 0.1, "", ""],
    [3, "", "", 20, 0.2],
    [4, "", "", 21, 0.2],
]
C = [
    ["time", "group", "a_mean", "a_sd", "b_mean", "b_sd"],
    [1, "Control", 10, 0.1, 20, 0.2],
    [2, "Control", 11, 0.1, "", ""],
    [1, "Treated", "", "", 25, 0.2],
    [2, "Treated", "", "", 26, 0.3],
]
# 维护者给的端到端表：审计反例 A 再加两整行（a 有 4 个有效值、b 有 5 个；各缺一处误差）——真实候选生成会出 line_err / line_points
A_PLUS = A + [[5, 14, 0.14, 25, 0.25], [6, 15, 0.15, 26, 0.26]]
FULL = [HEAD] + [[t, float(t), 0.1, 2.0 * t, 0.2] for t in range(1, 7)]


def donor(tmp_path, rows=None, code="line_err", **kw):
    """真实候选生成出的配方（要够行数）；低层用例拿它换到别的表上。"""
    profile, values, _ = read(tmp_path / "donor", rows or FULL)
    res = R.default_candidates(profile, confirmed=R.Confirmed(), values=values, **kw)
    assert isinstance(res, R.Candidates), res
    return next(c for c in res.items if c.code == code)


def sf(tmp_path, rows, rec, tag="v"):
    _, values, _ = read(tmp_path / tag, rows)
    return values, P.series_facts(values, rec)


def by_y(facts):
    return {s["y"]: s for s in facts["series"]}


def drawn_points(values, rec):
    """出图用的数据准备函数直接数：每个 y `(有效值个数, 有效 x 个数, 误差可画个数)`。"""
    d = tpl.trend_data(
        values.columns,
        values.rows,
        rec.x,
        list(rec.y),
        rec.group,
        rec.facet,
        rec.unit,
        values.per_column[rec.x].kind == "date",
        list(rec.err_cols) or None,
    )
    out = {}
    for y in rec.y:
        pairs = []
        for p in d["panels"]:
            for g in d["groups"]:
                xs, vals = tpl.trend_pool(d, p, g, y)
                pairs += [(x, v) for x, v in zip(xs, vals) if v == v]
        e = 0
        if rec.err_cols:
            for p in d["panels"]:
                for g in d["groups"]:
                    e += len(tpl.trend_error_points(d, p, g, y)[0])
        out[y] = (len(pairs), len({x for x, _ in pairs}), e)
    return out


def oracle(rows, x="time", ys=("a_mean", "b_mean"), errs=("a_sd", "b_sem")):
    """独立的人工点数（只用单元格判据 `adf.parse_cell`，不经任何绘图数据准备函数）：整表按每个 y `{n, xs, err_drawn, err_missing}` 与行级 `rows_any / rows_all`。"""
    head = rows[0]

    def num(r, c):
        return adf.parse_cell(str(r[head.index(c)]))

    out = {y: {"n": 0, "xs": set(), "err_drawn": 0, "err_missing": 0} for y in ys}
    any_ = all_ = 0
    for r in rows[1:]:
        if num(r, x)[0] != "numeric":
            continue
        ok = 0
        for y, e in zip(ys, errs, strict=True):
            if num(r, y)[0] != "numeric":
                continue
            ok += 1
            out[y]["n"] += 1
            out[y]["xs"].add(num(r, x)[1])
            ke, ve = num(r, e)
            out[y]["err_drawn" if ke == "numeric" and ve >= 0 else "err_missing"] += 1
        any_ += ok > 0
        all_ += ok == len(ys)
    return out, any_, all_


# =========================================================================== 1. 已核实的三个实例（旧：第一列的数冒充整张图）
def test_instance_maintainers_table_a_plus_two_full_rows(tmp_path):
    """维护者的端到端表：旧 `facts` 对两条线都写 4（`drawn: 4`），b 实际 5 个有效值——真实候选生成路径。"""
    profile, values, _ = read(tmp_path, A_PLUS)
    res = R.default_candidates(profile, confirmed=R.Confirmed(), values=values)
    assert [c.code for c in res.items] == ["line_err", "line_points"]
    for c in res.items:
        assert P.candidate_facts(values, c.recipe) is None
        got = P.series_facts(values, c.recipe)
        assert [(s["y"], s["n"], s["x_values"]) for s in got["series"]] == [
            ("a_mean", 4, 4),
            ("b_mean", 5, 5),
        ]
        assert (got["rows"], got["rows_any"], got["rows_all"]) == (6, 6, 3)
    err = P.series_facts(values, res.items[0].recipe)["series"]
    assert [(s["err_drawn"], s["err_missing"]) for s in err] == [(3, 1), (4, 1)]


def test_instance_error_columns_each_line_has_its_own_count(tmp_path):
    rows = [HEAD] + [
        [1, 1.0, 0.1, 2.0, 0.2],
        [2, 2.0, 0.1, 3.0, 0.2],
        [3, "", "", 4.0, 0.2],
        [4, 4.0, 0.1, 5.0, 0.2],
        [5, "", "", 6.0, 0.2],
        [6, 6.0, 0.1, 7.0, 0.2],
    ]
    profile, values, _ = read(tmp_path, rows)
    res = R.default_candidates(profile, confirmed=R.Confirmed(), values=values)
    assert [c.code for c in res.items] == ["line_err", "line_points"]
    for c in res.items:
        assert (
            P.candidate_facts(values, c.recipe) is None
        )  # 旧：{"drawn": 4, ...}，对 b（6 个点）是错的
        got = P.series_facts(values, c.recipe)
        assert [(s["y"], s["n"]) for s in got["series"]] == [("a_mean", 4), ("b_mean", 6)]
        assert (got["rows"], got["rows_any"], got["rows_all"]) == (6, 6, 4)
    err = P.series_facts(values, res.items[0].recipe)
    assert [(s["err_drawn"], s["err_missing"]) for s in err["series"]] == [(4, 0), (6, 0)]
    assert "err_drawn" not in P.series_facts(values, res.items[1].recipe)["series"][0]


def test_instance_missing_cell_in_the_second_line_is_not_counted_for_it(tmp_path):
    rows = [HEAD] + [[t, float(t), 0.1, "" if t == 3 else 2.0 * t, 0.2] for t in range(1, 7)]
    profile, values, _ = read(tmp_path, rows)
    rec = R.default_candidates(profile, confirmed=R.Confirmed(), values=values).items[0].recipe
    got = P.series_facts(values, rec)  # 旧：drawn 6，b 实际只有 5 个点
    assert [(s["y"], s["n"], s["x_values"]) for s in got["series"]] == [
        ("a_mean", 6, 6),
        ("b_mean", 5, 5),
    ]
    assert (got["rows_any"], got["rows_all"]) == (6, 5)
    # b 缺中心值的那一行误差格有数（0.2）：没有中心值就不画，也不算「缺误差」
    assert by_y(got)["b_mean"]["err_drawn"] == 5 and by_y(got)["b_mean"]["err_missing"] == 0


def test_instance_plain_multi_column_line_points(tmp_path):
    rows = [["time", "a", "b"]] + [
        [t, float(t), "" if t in (3, 5) else 2.0 * t] for t in range(1, 9)
    ]
    profile, values, _ = read(tmp_path, rows)
    cand = R.default_candidates(profile, confirmed=R.Confirmed(), values=values).items[0]
    assert cand.code == "line_points" and len(cand.recipe.y) == 2
    assert P.candidate_facts(values, cand.recipe) is None  # 旧：drawn 8，b 只有 6 个点
    got = P.series_facts(values, cand.recipe)
    assert [(s["y"], s["n"]) for s in got["series"]] == [("a", 8), ("b", 6)]
    assert (got["rows"], got["rows_any"], got["rows_all"]) == (8, 8, 6)


def test_a_single_y_keeps_the_old_facts_exactly(tmp_path):
    rows = [["time", "a"]] + [[t, float(t)] for t in range(1, 8)]
    profile, values, _ = read(tmp_path, rows)
    cand = R.default_candidates(
        profile, confirmed=R.Confirmed(), values=values, picks={"y": "a"}
    ).items[0]
    assert P.series_facts(values, cand.recipe) is None
    assert P.candidate_facts(values, cand.recipe) == {
        "rows": 7,
        "drawn": 7,
        "groups": None,
        "n_min": None,
        "n_max": None,
        "x_values": 7,
        "skipped_missing": 0,
        "skipped_other": 0,
    }


# =========================================================================== 2. 审计反例 A / B / C（显式配方）
def test_audit_a_asymmetric_missing_with_different_error_kinds(tmp_path):
    rec = donor(tmp_path).recipe
    values, got = sf(tmp_path, A, rec)
    assert (got["rows"], got["eligible_rows"], got["rows_any"], got["rows_all"]) == (4, 4, 4, 1)
    a, b = by_y(got)["a_mean"], by_y(got)["b_mean"]
    assert (a["n"], a["x_values"], a["err_drawn"], a["err_missing"]) == (2, 2, 1, 1)
    assert (b["n"], b["x_values"], b["err_drawn"], b["err_missing"]) == (3, 3, 2, 1)
    # 旧：drawn 2、x_values 2，对 b（3 个有效值）是错的
    assert sum(s["n"] for s in got["series"]) == 5
    assert drawn_points(values, rec) == {"a_mean": (2, 2, 1), "b_mean": (3, 3, 2)}


def test_audit_b_same_counts_but_different_positions(tmp_path):
    rec = dataclasses.replace(donor(tmp_path).recipe, err=("a_sd", "b_sd"), err_kind=("sd", "sd"))
    values, got = sf(tmp_path, B, rec)
    assert [s["n"] for s in got["series"]] == [2, 2]
    assert (got["rows_any"], got["rows_all"]) == (4, 0)  # 数量相同，但不是同一批行
    assert sum(s["n"] for s in got["series"]) == 4


def test_audit_c_group_coverage_differs_and_no_phantom_cells(tmp_path):
    rec = dataclasses.replace(
        donor(tmp_path).recipe, group="group", err=("a_sd", "b_sd"), err_kind=("sd", "sd")
    )
    values, got = sf(tmp_path, C, rec)
    a, b = by_y(got)["a_mean"], by_y(got)["b_mean"]
    assert (a["n"], b["n"]) == (2, 3)  # a 在 Treated 组没有数据：不虚构成 0 个值之外的东西
    assert (a["x_values"], b["x_values"]) == (2, 2)
    assert (a["err_drawn"], b["err_drawn"]) == (2, 3)
    assert (got["rows_any"], got["rows_all"]) == (4, 1)
    assert drawn_points(values, rec) == {"a_mean": (2, 2, 2), "b_mean": (3, 2, 3)}


def test_x_is_deduplicated_by_the_parsed_value(tmp_path):
    rows = (
        [["time", "a", "b"]]
        + [["1", 1.0, 2.0], ["1.0", 1.5, 2.5], ["1e0", 1.6, 2.6]]
        + [[t, float(t), 2.0 * t] for t in range(2, 7)]
    )
    profile, values, _ = read(tmp_path, rows)
    rec = R.default_candidates(profile, confirmed=R.Confirmed(), values=values).items[0].recipe
    got = P.series_facts(values, rec)
    assert [s["n"] for s in got["series"]] == [8, 8]
    assert [s["x_values"] for s in got["series"]] == [6, 6]  # 1、1.0、1e0 是同一个坐标


# =========================================================================== 3. 不变量
def test_swapping_the_y_order_changes_nothing_but_the_order(tmp_path):
    rec = donor(tmp_path).recipe
    swapped = dataclasses.replace(
        rec, y=rec.y[::-1], err=rec.err[::-1], err_kind=rec.err_kind[::-1]
    )
    _, one = sf(tmp_path, A, rec, "v1")
    _, two = sf(tmp_path, A, swapped, "v2")
    assert [s["y"] for s in two["series"]] == ["b_mean", "a_mean"]
    assert by_y(one) == by_y(two)
    for k in ("rows", "eligible_rows", "rows_any", "rows_all"):
        assert one[k] == two[k]


def test_editing_one_value_of_b_leaves_a_untouched(tmp_path):
    rec = donor(tmp_path).recipe
    _, before = sf(tmp_path, A_LONG, rec, "v1")
    edited = [list(r) for r in A_LONG]
    edited[3][3] = ""  # b 在 time=3 的值改成缺失
    edited[3][4] = ""
    _, after = sf(tmp_path, edited, rec, "v2")
    assert by_y(before)["a_mean"] == by_y(after)["a_mean"]
    assert by_y(after)["b_mean"]["n"] == by_y(before)["b_mean"]["n"] - 1
    assert by_y(after)["b_mean"]["err_drawn"] == by_y(before)["b_mean"]["err_drawn"] - 1


@pytest.mark.parametrize("name", ["A", "A_LONG", "A_PLUS", "B", "FULL"])
def test_series_facts_equal_an_independent_hand_count(tmp_path, name):
    rows = {"A": A, "A_LONG": A_LONG, "A_PLUS": A_PLUS, "B": B, "FULL": FULL}[name]
    rec = donor(tmp_path).recipe
    errs = ("a_sd", "b_sem")
    if name == "B":
        rec = dataclasses.replace(rec, err=("a_sd", "b_sd"))
        errs = ("a_sd", "b_sd")
    values, got = sf(tmp_path, rows, rec)
    want, any_, all_ = oracle(rows, errs=errs)
    for s in got["series"]:
        w = want[s["y"]]
        assert (s["n"], s["x_values"], s["err_drawn"], s["err_missing"]) == (
            w["n"],
            len(w["xs"]),
            w["err_drawn"],
            w["err_missing"],
        )
        assert (
            s["err_drawn"] + s["err_missing"] == s["n"]
        )  # 误差可画 + 缺误差 = 有效值（两边各自独立数出来的）
    assert (got["rows_any"], got["rows_all"]) == (any_, all_)
    assert got["rows"] - got["rows_any"] == len(rows) - 1 - any_  # 全无效的行数
    assert sum(s["n"] for s in got["series"]) == sum(w["n"] for w in want.values())
    # 与出图用的数据准备函数整表直接数一遍也一致
    assert {y: v[0] for y, v in drawn_points(values, rec).items()} == {
        s["y"]: s["n"] for s in got["series"]
    }


def test_zero_error_is_drawable_and_error_without_a_center_is_not_counted(tmp_path):
    rows = [list(r) for r in FULL]
    rows[2][2] = 0  # a 在 time=2 的误差是 0：合法，画
    rows[3][1] = ""  # a 在 time=3 没有中心值，但误差格有数（0.1）：不计
    rec = donor(tmp_path).recipe
    values, got = sf(tmp_path, rows, rec)
    a = by_y(got)["a_mean"]
    assert (a["n"], a["err_drawn"], a["err_missing"]) == (5, 5, 0)
    assert drawn_points(values, rec)["a_mean"] == (5, 5, 5)
    assert (
        0.0
        in tpl.trend_error_points(
            tpl.trend_data(
                values.columns,
                values.rows,
                "time",
                list(rec.y),
                None,
                None,
                None,
                False,
                list(rec.err_cols),
            ),
            "",
            "",
            "a_mean",
        )[2]
    )


def test_no_series_facts_for_single_y_large_or_stack(tmp_path):
    rec = donor(tmp_path).recipe
    _, values, _ = read(tmp_path / "x", FULL)
    single = dataclasses.replace(rec, y=rec.y[:1], err=rec.err[0], err_kind=rec.err_kind[0])
    assert P.series_facts(values, single) is None
    assert P.series_facts(dataclasses.replace(values, large=object()), rec) is None
    assert P.series_facts(values, dataclasses.replace(rec, stack=("a_mean", "b_mean"))) is None
    assert P.candidate_facts(values, rec) is None  # 多个 y：旧的歧义 facts 不再给


# =========================================================================== 4. 审计另外几条
def test_audit_d_same_local_row_number_in_two_files_does_not_collide(tmp_path):
    """合画：A 的第 3 行横轴无效，B 的第 3 行是好行——旧 `gone` 只按行号，把 B 的第 3 行也排掉了。"""
    rows = [["time", "a"]] + [[t, float(t)] for t in range(1, 8)]
    profile, values, _ = read(tmp_path, rows)
    rec = (
        R.default_candidates(profile, confirmed=R.Confirmed(), values=values, picks={"y": "a"})
        .items[0]
        .recipe
    )
    bad = [list(r) for r in values.rows]
    bad[1][0] = ""
    merged = dataclasses.replace(
        values, rows=bad, row_numbers=[2, 3, 4, 2, 3, 4, 5], row_sources=["A"] * 3 + ["B"] * 4
    )
    facts = P.candidate_facts(merged, rec)
    assert (facts["rows"], facts["drawn"], facts["x_values"]) == (7, 6, 6)  # 旧：drawn 5
    assert facts["skipped_missing"] == 1
    assert [(i["file"], i["row"]) for i in P.unplotted_rows(merged, rec)] == [("A", 3)]


def test_audit_d_multi_y_merged_keeps_the_good_row_of_the_other_file(tmp_path):
    rec = donor(tmp_path).recipe
    profile, values, _ = read(tmp_path / "m", FULL)
    bad = [list(r) for r in values.rows]
    bad[1][0] = ""
    merged = dataclasses.replace(
        values, rows=bad, row_numbers=[2, 3, 4, 2, 3, 4], row_sources=["A"] * 3 + ["B"] * 3
    )
    got = P.series_facts(merged, rec)
    assert got["rows"] == 6 and got["eligible_rows"] == 5
    assert [s["n"] for s in got["series"]] == [5, 5]
    assert got["skipped_missing"] == 1


def test_audit_e_a_text_cell_in_one_y_does_not_drop_the_other_y_of_the_row(tmp_path):
    rec = donor(tmp_path).recipe
    _, values, _ = read(tmp_path / "x", FULL)
    mixed = [list(r) for r in values.rows]
    mixed[1][3] = "n/a"  # 第 2 行 b 是文字：a 仍要算
    mixed = dataclasses.replace(values, rows=mixed)
    got = P.series_facts(mixed, dataclasses.replace(rec, err=None, err_kind=None))
    assert [(s["y"], s["n"]) for s in got["series"]] == [("a_mean", 6), ("b_mean", 5)]
    assert (got["rows_any"], got["rows_all"]) == (6, 5)


def test_audit_b_two_text_cells_in_one_row_are_one_unplotted_row(tmp_path):
    rec = dataclasses.replace(donor(tmp_path).recipe, err=None, err_kind=None)
    _, values, _ = read(tmp_path / "x", FULL)
    mixed = [list(r) for r in values.rows]
    mixed[1][1] = "x"
    mixed[1][3] = "y"
    mixed = dataclasses.replace(values, rows=mixed)
    items = P.unplotted_rows(mixed, rec)
    assert (
        len({i["row"] for i in items}) == 1 and len(items) == 1
    )  # 旧：同一行记两条（每个 y 一条）
    got = P.series_facts(mixed, rec)
    assert got["skipped_other"] == 1 and got["skipped_missing"] == 0
    assert [s["n"] for s in got["series"]] == [5, 5]


# =========================================================================== 5. 响应
def test_response_carries_series_facts_and_no_ambiguous_facts(project):
    _, client, figs, _ = project
    put(figs, "data/m.csv", A_LONG)
    out = propose(client, render="lazy").get_json()
    top = out["items"][0]
    assert top["title_code"] == "line_err"
    assert "facts" not in top
    got = top["series_facts"]
    assert [(s["y"], s["n"], s["err_drawn"], s["err_missing"]) for s in got["series"]] == [
        ("a_mean", 3, 2, 1),
        ("b_mean", 4, 3, 1),
    ]
    assert got["rows"] == 6


def test_single_y_response_is_unchanged(project):
    _, client, figs, _ = project
    put(figs, "data/m.csv", [["time", "a"]] + [[t, float(t)] for t in range(1, 8)])
    out = propose(client, render="lazy", answers={"y": "a"}).get_json()
    top = out["items"][0]
    assert top["facts"]["drawn"] == 7 and "series_facts" not in top


# =========================================================================== 6. 真脚本：传给 errorbar 的坐标 / 误差个数与统计一致
@needs_worker
def test_the_generated_script_agrees_with_series_facts(tmp_path):
    profile, values, _ = read(tmp_path / "c", A_PLUS)
    res = R.default_candidates(profile, confirmed=R.Confirmed(), values=values)
    rec = next(c for c in res.items if c.code == "line_err").recipe
    got = P.series_facts(values, rec)
    calls, pdf = run_script(tmp_path, A_PLUS, rec)
    assert pdf and len(calls) == 2
    a, b = by_y(got)["a_mean"], by_y(got)["b_mean"]
    assert [len(c["x"]) for c in calls] == [a["err_drawn"], b["err_drawn"]]
    assert [len(c["yerr"]) for c in calls] == [a["err_drawn"], b["err_drawn"]]
    assert calls[0]["x"] == [1.0, 5.0, 6.0] and calls[0]["y"] == [10.0, 14.0, 15.0]
    assert calls[0]["yerr"] == [0.1, 0.14, 0.15]
    assert calls[1]["x"] == [2.0, 3.0, 5.0, 6.0] and calls[1]["yerr"] == [0.2, 0.3, 0.25, 0.26]
    assert all(not math.isnan(v) for c in calls for v in c["yerr"])
    assert (a["n"], b["n"], a["err_missing"], b["err_missing"]) == (4, 5, 1, 1)


# =========================================================================== 7. 合画走真接口：A 文件第 3 行横轴空，不连累 B 文件的第 3 行
def test_merged_propose_keeps_the_other_files_same_numbered_row(project):
    _, client, figs, _ = project
    a = [["time", "v"]] + [[t, float(t)] for t in range(1, 8)]
    a[2][0] = ""  # A 的文件行号 3：横轴空，画不了
    b = [["time", "v"]] + [[t, float(t) + 10] for t in range(1, 8)]
    put(figs, "data/a.csv", a)
    put(figs, "data/b.csv", b)
    out = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        answers={"y": "v"},
        render="lazy",
    ).get_json()
    assert out["kind"] == "candidates", out
    top = out["items"][0]
    assert top["unplotted"]["count"] == 1
    assert (
        top["facts"]["rows"] == 14 and top["facts"]["drawn"] == 13
    )  # 旧：12（B 的第 3 行被一并排掉）


def test_unplotted_unmet_still_fires_for_a_row_with_text_in_several_y(tmp_path):
    """每行只记一条没改变 `goal.rows_plotted` 的判据：有文字格的行仍是「有数据却没画上」。"""
    rec = dataclasses.replace(donor(tmp_path).recipe, err=None, err_kind=None)
    _, values, _ = read(tmp_path / "x", FULL)
    rows = [list(r) for r in values.rows]
    rows[1][1] = rows[1][3] = "x"
    items = P.unplotted_rows(dataclasses.replace(values, rows=rows), rec)
    assert P.unplotted_unmet(items) == 1 and P.unplotted_total(items) == 1
    clean = P.unplotted_rows(values, rec)
    assert P.unplotted_unmet(clean) == 0


def test_other_families_with_several_y_keep_the_old_facts(tmp_path):
    """只有多个 y 的**趋势图**改走 `series_facts`；别的族（构成 / 区间 / 矩阵……）照旧。"""
    rec = dataclasses.replace(donor(tmp_path).recipe, family="composition", err=None, err_kind=None)
    _, values, _ = read(tmp_path / "x", FULL)
    assert P.series_facts(values, rec) is None
    assert P.candidate_facts(values, rec) is not None
