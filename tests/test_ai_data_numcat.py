# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""整数编码的分组列（`knockout` 0 / 1、`treatment` 1 / 2 / 3；ADR 0178 修订 2026-11-21，留出用例 HO-20）。

判据的主语：**配方里「这一列是整数编码、按类别用」这条事实（`FigureRecipe.numcat`）在每一层的读法是否一致**——
* 摘要（`DataSummary.numcat`，客观条件的唯一实现 `adf.numcat_levels`）：非空值全是整数、2–6 个取值、有取值重复、不是编号样的列；
* 规则永远不自己把它当分组：没有人点名时，配方与响应逐字不变（只有 `roles.options.group` 多一个可选项）；
* 点名（用户选 / 模型判 `group`）才进配方：`validate_recipe` 把它当类别列，脚本读表后 `apply_numcat`、类别按数值升序、标签是整数写法；
* 条件不满足 = 丢弃这项、回到规则结果；大表与合画暂不支持，同样干净地回到规则结果。
"""

from __future__ import annotations

import dataclasses
import json
import socket
from pathlib import Path

import pytest

from tavotto.engine import (
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_roles as ROLES,
    ai_data_template as tpl,
)
from tavotto.engine.agent_runner import wires
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


def ko_rows(first=1):
    """HO-20 的形状：`knockout`（0 / 1）+ `growth_rate`，各 8 行；`first` 决定文件里先出现哪个取值（顺序要被数值升序盖过）。"""
    rows = [["knockout", "growth_rate"]]
    vals = {
        0: [1.016, 1.057, 1.075, 0.989, 1.084, 0.934, 1.075, 0.987],
        1: [0.726, 0.599, 0.625, 0.625, 0.727, 0.626, 0.737, 0.718],
    }
    for k in (first, 1 - first):
        rows += [[k, v] for v in vals[k]]
    return rows


def write_csv(tmp_path: Path, rows, name="t.csv") -> Path:
    d = tmp_path / "prof"
    d.mkdir(exist_ok=True)
    (d / name).write_text(
        "\n".join(",".join(str(c) for c in r) for r in rows) + "\n", encoding="utf-8"
    )
    return d


def summary_of(tmp_path, cells, header="code"):
    """一列待判的 `cells` + 一列测量值，返回 v2 摘要。"""
    rows = [[header, "y"], *[[c, i + 1.5] for i, c in enumerate(cells)]]
    d = write_csv(tmp_path, rows)
    return adf.inspect_data_source(d / "t.csv", v2=True)


def cands(tmp_path, rows, *, picks=None, numcat_off=False):
    d = write_csv(tmp_path, rows)
    prof = adf.profile_folder(d)
    values = adf.inspect_data_source(d / "t.csv", v2=True)
    if numcat_off:  # 「改前」：摘要里没有这条事实
        prof = dataclasses.replace(
            prof,
            tables=tuple(
                dataclasses.replace(t, summary=dataclasses.replace(t.summary, numcat={}))
                for t in prof.tables
            ),
        )
        values = dataclasses.replace(values, numcat={})
    return (
        R.default_candidates(
            prof, table="t.csv", confirmed=R.Confirmed(), values=values, picks=picks
        ),
        prof,
        values,
    )


# =========================================================================== 客观条件
@pytest.mark.parametrize(
    "cells, want",
    [
        (["0", "0", "1", "1"], 2),  # 0 / 1 编码
        (["1", "2", "3", "1", "2", "3"], 3),
        (["1.0", "2.0", "1.0", "2.0"], 2),  # 写成 1.0 的整数也是整数
        (["1", "1.0", "01", "2"], 2),  # 1 / 1.0 / 01 同一个取值
        (["-1", "0", "1", "1"], 3),  # 负数可以（-1 / 0 / 1 编码）
        (["0", "", "1", "1", "0"], 2),  # 空格子不算取值，也不影响判定
        (["1", "2", "3", "4", "5", "6", "1"], 6),  # 恰好 6 个取值
        (["1", "2", "3", "4", "5", "6", "7", "1"], None),  # 7 个取值：太多
        (["1", "2", "3", "4"], None),  # 每行都不同：不是类别
        (["1", "1", "1"], None),  # 只有一个取值
        (["0", "1.5", "1", "1"], None),  # 含小数
        (["0", "1", "nan", "1"], None),  # 含 NaN 字面量
        (["0", "1", "n/a", "1"], None),  # 夹着文字：混合列
        (["a", "b", "a"], None),  # 文字列是 `groups` 的事，不在这里
        (["1e20", "1", "1"], None),  # 大到不是安全整数
    ],
)
def test_the_objective_condition(tmp_path, cells, want):
    s = summary_of(tmp_path, cells)
    assert s.numcat.get("code") == want
    assert "numcat" not in s.to_dict()  # 对外摘要逐字不变


def test_id_like_integer_columns_are_not_categories(tmp_path):
    s = summary_of(tmp_path, ["1", "2", "1", "2"], header="subject")
    assert "subject" in s.id_like and s.numcat == {}


def test_v1_summary_and_large_tables_carry_no_numcat(tmp_path, monkeypatch):
    d = write_csv(tmp_path, ko_rows())
    assert adf.inspect_data_source(d / "t.csv").numcat == {}  # v1 读法
    assert adf.inspect_data_source(d / "t.csv", v2=True).numcat == {"knockout": 2}
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    big = adf.inspect_data_source(d / "t.csv", v2=True, allow_large=True)
    assert big.large is not None and big.numcat == {}


# =========================================================================== 运行时：规范写法与顺序
def test_apply_numcat_normalises_and_distinct_sorts_numerically():
    header = ["g", "y"]
    rows = [["2.0", "1"], ["1", "2"], ["", "3"], ["10", "4"], ["-1", "5"], ["01", "6"]]
    out = adf.apply_numcat(header, rows, ["g"])
    assert [str(r[0]) for r in out] == ["2", "1", "", "10", "-1", "1"]
    assert all(isinstance(r[0], adf.NumCat) for r in out if r[0] != "")
    assert out[0][0].strip() is out[0][0]  # 取数处写 cell.strip()，标记要留住
    assert rows[0][0] == "2.0"  # 只改副本
    d = tpl.comparison_data(header, out, "g", "y", None, None, "as_given")
    assert d["cats"] == ["-1", "1", "2", "10"]  # 数值升序，不是首次出现的顺序；空格子那行不画
    assert d["cats"][1] == "1" and "1.0" not in d["cats"]
    # 没套 apply_numcat（普通文字格）时仍是首次出现的顺序——别的路径逐字不变
    assert tpl.distinct(["b", "a", "b"]) == ["b", "a"]
    assert tpl.distinct(["2", "1", "10"]) == ["2", "1", "10"]


def test_the_common_module_source_carries_the_new_functions():
    src = tpl.runtime_module()[1].decode("utf-8")
    assert "class NumCat(str)" in src and "def apply_numcat(" in src


# =========================================================================== 配方：规则从不点名、点名才进配方
def test_rules_never_group_by_an_integer_column_and_the_response_is_otherwise_unchanged(tmp_path):
    # 三个水平的整数列（4 / 25 / 37）：有没有 numcat 这条事实，规则给的备选逐字相同。两个水平的（0 / 1）自 2026-11-27 起不再当默认横轴
    # （ADR 0178「条件列与测量列」，`test_ai_data_design_measure` 锁它），所以「其余不变」拿三个水平的表来量；「规则从不拿整数列分组」两张表都量
    three = [["knockout", "growth_rate"]]
    for k, base in ((4, 1.0), (25, 0.7), (37, 0.4)):
        three += [[k, round(base + i * 0.013, 3)] for i in range(8)]
    for rows, same in ((ko_rows(), False), (three, True)):
        for picks in (None, {"y": "growth_rate"}):
            _check_rules_never_group(tmp_path, rows, picks, same)


def _check_rules_never_group(tmp_path, rows, picks, same):
    if True:
        new, _p, _v = cands(tmp_path, rows, picks=picks)
        old, _p2, _v2 = cands(tmp_path, rows, picks=picks, numcat_off=True)
        assert type(new) is type(old)
        if isinstance(new, R.NeedsChoice):
            return
        assert all(c.recipe.numcat == () and "numcat" not in c.recipe.to_dict() for c in new.items)
        assert all(c.recipe.group is None for c in new.items)
        assert "knockout" in new.roles["options"]["group"] and old.roles["options"]["group"] == []
        if not same:
            return
        assert [c.recipe.to_dict() for c in new.items] == [c.recipe.to_dict() for c in old.items]
        assert all(c.recipe.numcat == () and "numcat" not in c.recipe.to_dict() for c in new.items)
        assert all(c.recipe.group is None for c in new.items)
        assert new.dropped == old.dropped and new.reasons == old.reasons
        a, b = dict(new.roles), dict(old.roles)
        # 唯一的差别：下拉里多了可选项（默认选择不变）
        assert "knockout" in a["options"]["group"] and b["options"]["group"] == []
        a["options"] = {k: v for k, v in a["options"].items() if k != "group"}
        b["options"] = {k: v for k, v in b["options"].items() if k != "group"}
        assert a == b


def test_naming_the_column_makes_it_a_group(tmp_path):
    res, prof, values = cands(tmp_path, ko_rows(first=1), picks={"group": "knockout"})
    assert not isinstance(res, R.NeedsChoice) and res.items
    assert res.roles["group"] == "knockout" and res.roles["x"] is None
    assert res.roles["y"] == ["growth_rate"]
    for c in res.items:
        r = c.recipe
        assert r.numcat == ("knockout",) and "knockout" in (r.x, r.group, r.facet)
        assert r.to_dict()["numcat"] == ["knockout"]
        assert R.validate_recipe(prof, r, confirmed=R.Confirmed(), values=values) == []
    assert {c.recipe.family for c in res.items} >= {"comparison"}
    # 同一份配方去掉那条事实就不合格：没有它这一列不是类别列
    bare = dataclasses.replace(res.items[0].recipe, numcat=())
    assert any(
        p.code == "column_type_mismatch"
        for p in R.validate_recipe(prof, bare, confirmed=R.Confirmed(), values=values)
    )


def test_naming_a_column_that_fails_the_condition_falls_back_to_the_rule_result(tmp_path):
    rows = [["dose", "resp"], *[[i % 8, 1.0 + i * 0.1] for i in range(16)]]  # 8 个取值：太多
    named, _p, _v = cands(tmp_path, rows, picks={"group": "dose", "y": "resp"})
    plain, _p2, _v2 = cands(tmp_path, rows, picks={"y": "resp"})
    assert [c.recipe.to_dict() for c in named.items] == [c.recipe.to_dict() for c in plain.items]
    assert all(c.recipe.numcat == () for c in named.items)
    rows = [["code", "resp"], *[[i % 2, 1.0 + i * 0.13] for i in range(10)]]
    rows[3][0] = 1.5  # 含小数
    named, _p, _v = cands(tmp_path, rows, picks={"group": "code", "y": "resp"})
    assert all(c.recipe.numcat == () and c.recipe.group is None for c in named.items)


def test_the_measure_column_is_never_taken_as_the_group(tmp_path):
    # y 也是 0 / 1 编码的计数：点名 y 同时点名分组时，不会把唯一的数值列拿去分组
    rows = [["n", "m"], *[[i % 2, (i // 2) % 2] for i in range(12)]]
    res, _p, _v = cands(tmp_path, rows, picks={"group": "n", "y": "n"})
    assert isinstance(res, (R.NeedsChoice, R.Candidates))


def test_validate_recipe_judges_the_numcat_fact(tmp_path):
    res, prof, values = cands(tmp_path, ko_rows(), picks={"group": "knockout"})
    good = res.items[0].recipe
    conf = R.Confirmed()

    def problems(**kw):
        return [
            (p.code, p.field)
            for p in R.validate_recipe(
                prof, dataclasses.replace(good, **kw), confirmed=conf, values=values
            )
        ]

    assert problems() == []
    assert ("column_type_mismatch", "numcat[0]") in problems(
        numcat=("growth_rate",)
    )  # 不是整数编码列
    assert ("column_type_mismatch", "numcat[1]") in problems(
        numcat=("knockout", "knockout")
    )  # 重复
    assert any(
        c == "roles_invalid"
        for c, _f in problems(
            numcat=("knockout",),
            x=None,
            group=None,
            facet=None,
            family="distribution",
            layers=(R.Layer("hist", "none", "main"),),
        )
    )
    # 趋势的横轴是有序的数值：写进 numcat 是自相矛盾
    trend = dataclasses.replace(
        good,
        family="trend",
        layers=(R.Layer("line", "none", "main"),),
        x="knockout",
        group=None,
        numcat=("knockout",),
    )
    assert ("roles_invalid", "numcat[0]") in [
        (p.code, p.field) for p in R.validate_recipe(prof, trend, confirmed=conf, values=values)
    ]


def test_recipe_round_trips_and_rejects_a_malformed_numcat(tmp_path):
    res, _p, _v = cands(tmp_path, ko_rows(), picks={"group": "knockout"})
    r = res.items[0].recipe
    assert R.FigureRecipe.from_dict(json.loads(json.dumps(r.to_dict()))) == r
    plain = dataclasses.replace(r, numcat=())
    assert "numcat" not in plain.to_dict()
    for bad in ("knockout", [1], [""], ["a"] * 4):
        with pytest.raises(R.RecipeError) as e:
            R.FigureRecipe.from_dict({**r.to_dict(), "numcat": bad})
        assert e.value.code == "recipe_malformed"


def test_large_tables_do_not_accept_the_fact(tmp_path, monkeypatch):
    res, prof, values = cands(tmp_path, ko_rows(), picks={"group": "knockout"})
    rec = res.items[0].recipe
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    d = tmp_path / "prof"
    big = adf.inspect_data_source(d / "t.csv", v2=True, allow_large=True)
    entry = adf.TableEntry("t.csv", None, False, big)
    prof2 = adf.FolderProfile((entry,), (), False, None)
    got = R.validate_recipe(prof2, rec, confirmed=R.Confirmed(), values=big)
    assert got and all(p.code in ("column_type_mismatch", "mark_not_allowed") for p in got)
    # 大表上点名 = 丢弃、回到规则结果，不出错不出零候选
    d2, _p, _v = (
        R.default_candidates(
            prof2, table="t.csv", confirmed=R.Confirmed(), values=big, picks={"group": "knockout"}
        ),
        None,
        None,
    )
    assert not isinstance(d2, str)


# =========================================================================== 模型点名
def _understand(tmp_path, rows, shape):
    d = write_csv(tmp_path, rows)
    prof = adf.profile_folder(d)
    entry = prof.tables[0]
    values = adf.inspect_data_source(d / "t.csv", v2=True)
    return ROLES.interpret(entry, values, shape, None)


def test_the_model_naming_group_is_adopted_only_when_objectively_sound(tmp_path):
    shape = {
        "columns": [
            {"name": "knockout", "role": "group"},
            {"name": "growth_rate", "role": "measure"},
        ],
        "picks": {"y": ["growth_rate"], "x": "none", "group": "knockout"},
    }
    u = _understand(tmp_path, ko_rows(), shape)
    assert u.picks.get("group") == "knockout" and ("knockout", "group") in u.roles
    # 角色是 measure 却被点名当分组：丢
    bad = {
        **shape,
        "columns": [
            {"name": "knockout", "role": "measure"},
            {"name": "growth_rate", "role": "measure"},
        ],
    }
    u2 = _understand(tmp_path, ko_rows(), bad)
    assert "group" not in u2.picks and u2.dropped >= 1
    # 含小数的列：角色合不上、点名也丢
    rows = ko_rows()
    rows[2][0] = 0.5
    u3 = _understand(tmp_path, rows, shape)
    assert "group" not in u3.picks and u3.dropped >= 1
    # 同一个模型判断落到规则：HO-20 要的默认
    res, _p, _v = cands(tmp_path, ko_rows(), picks=u.picks)
    assert (
        res.roles["group"] == "knockout"
        and res.roles["x"] is None
        and res.roles["y"] == ["growth_rate"]
    )


# =========================================================================== 真 app + 真 safe worker
def _group_answers(**extra):
    return {"y": "growth_rate", "group": "knockout", **extra}


@needs_worker
def test_user_naming_the_column_renders_and_verifies_end_to_end(project):
    m, client, figs, _ = project
    put(figs, "data/ko.csv", ko_rows(first=1))  # 文件里先出现 1：类别顺序要按数值升序，不是首次出现
    body = propose(client, render="lazy", answers=_group_answers()).get_json()
    assert body["kind"] == "candidates", body
    assert body["roles"]["group"] == "knockout" and body["roles"]["y"] == ["growth_rate"]
    assert body["roles"]["x"] is None and "knockout" in body["roles"]["options"]["group"]
    first = body["items"][0]
    assert first["values"]["x"] == "knockout"
    thumb = client.get(first["thumbnail_url"])  # 真 worker 渲染，候选指纹核对过
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    c = commit(client, body["session"], first["id"])
    assert c.status_code == 200, c.get_json()
    out = c.get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["data_check"] == "verified", out
    checks = checks_of(out["task"]["task_id"])
    assert checks["render.figure"] == ("verified", None)
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "NUMCATS = ['knockout']" in script and "apply_numcat(header, rows, NUMCATS)" in script
    assert (
        (figs / "data" / "ko.csv")
        .read_text(encoding="utf-8")
        .startswith("knockout,growth_rate\n1,")
    )


@needs_worker
def test_the_model_naming_the_column_renders_and_verifies_end_to_end(project, tmp_path):
    m, client, figs, _ = project
    put(figs, "data/ko.csv", ko_rows(first=0))
    shape = {
        "columns": [
            {"name": "knockout", "role": "group"},
            {"name": "growth_rate", "role": "measure"},
        ],
        "picks": {"y": ["growth_rate"], "x": "none", "group": "knockout"},
    }
    u = _understand(tmp_path, ko_rows(first=0), shape)
    answers = {**u.picks}
    body = propose(client, render="lazy", answers=answers).get_json()
    assert body["kind"] == "candidates" and body["roles"]["group"] == "knockout", body
    c = commit(client, body["session"], body["items"][0]["id"])
    out = c.get_json()
    assert c.status_code == 200 and out["data_check"] == "verified", out


@needs_worker
def test_without_naming_the_response_has_no_numcat_and_the_option_is_only_listed(project):
    m, client, figs, _ = project
    put(figs, "data/ko.csv", ko_rows())
    body = propose(client, render="lazy", answers={"y": "growth_rate"}).get_json()
    assert body["kind"] == "candidates", body
    assert body["roles"]["group"] is None  # 默认选择不变
    assert "knockout" in body["roles"]["options"]["group"]  # 只是多了一个可选项
    first = body["items"][0]
    c = commit(client, body["session"], first["id"])
    script = (figs / c.get_json()["script"]).read_text(encoding="utf-8")
    assert "NUMCATS" not in script and "apply_numcat" not in script


@needs_worker
def test_naming_a_column_that_does_not_qualify_gives_the_rule_result(project):
    m, client, figs, _ = project
    rows = ko_rows()
    rows[5][0] = 0.5
    put(figs, "data/ko.csv", rows)
    a = propose(client, render="lazy", answers={"y": "growth_rate", "group": "knockout"}).get_json()
    b = propose(client, render="lazy", answers={"y": "growth_rate"}).get_json()
    assert a["kind"] == b["kind"] and a["roles"] == b["roles"]
    assert [i["title_code"] for i in a["items"]] == [i["title_code"] for i in b["items"]]


def test_large_tables_and_merged_tables_fall_back_cleanly(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/a.csv", ko_rows(first=0))
    put(figs, "data/b.csv", ko_rows(first=1))
    merged = propose(
        client,
        tables=[{"path": "a.csv"}, {"path": "b.csv"}],
        render="lazy",
        answers=_group_answers(),
    )
    assert merged.status_code == 200, merged.get_json()
    mb = merged.get_json()
    assert mb["kind"] == "candidates" and mb["roles"]["group"] != "knockout", mb["roles"]
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    big = propose(client, table="a.csv", render="lazy", answers=_group_answers())
    assert big.status_code == 200, big.get_json()
    assert big.get_json()["kind"] in ("candidates", "nothing")
