# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""把几列当分组（宽表转长表；`FigureRecipe.stack`；ADR 0178 修订 2026-11-21，留出用例 HO-17 / HO-25..30）。

判据的主语：**配方里「这几列在逻辑上转成长表（一列值、一列类别 = 原列名）」这条事实在每一层的读法是否一致**——
* 客观条件只有一处实现 `ai_data_recipe.stack_ok`（2–`STACK_MAX_COLUMNS` 个纯数值列、同单位、非编号、每列 ≥ `STACK_MIN_N` 个数值格）；
* 规则永远不自己用：没有人点名（`picks.y` 数组）时配方与响应逐字不变；
* 点名（用户 `answers.y` 数组 + 没有有序横轴 / 模型把几列都判 `measure`）才进配方：`validate_recipe` 按转换后的长表看，脚本读表后 `apply_stack`，
  类别顺序 = 表里的列顺序、标签 = 列名原文；`roles.y` 就是被当分组的列，与每个备选实际画的列一致；
* 有有序横轴时仍是每列一条线；有文字分组列、合画、大表：干净地回到规则结果，不出错也不出零候选。
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


NAMES = ("ctrl", "drugA", "drugB")


def wide_rows(names=NAMES, n=8, label=None, blanks=()):
    """宽表：每列一个条件，`n` 行；`label` 给了就在最前面加一列样品名；`blanks` 是 `(行号, 列下标)`，那一格留空。"""
    rows = [([label] if label else []) + list(names)]
    for i in range(n):
        vals = [
            "" if (i, j) in blanks else round(10 + j * 3 + (i % 4) * 0.7 + (i * 0.13), 2)
            for j in range(len(names))
        ]
        rows.append(([f"S{i + 1}"] if label else []) + vals)
    return rows


def write_csv(tmp_path: Path, rows, name="t.csv") -> Path:
    d = tmp_path / "prof"
    d.mkdir(exist_ok=True)
    (d / name).write_text(
        "\n".join(",".join(str(c) for c in r) for r in rows) + "\n", encoding="utf-8"
    )
    return d


def load(tmp_path, rows):
    d = write_csv(tmp_path, rows)
    return (
        adf.profile_folder(d),
        adf.inspect_data_source(d / "t.csv", v2=True),
        adf.profile_folder(d).tables[0],
    )


def cands(tmp_path, rows, picks=None):
    prof, values, _entry = load(tmp_path, rows)
    return (
        R.default_candidates(
            prof, table="t.csv", confirmed=R.Confirmed(), values=values, picks=picks
        ),
        prof,
        values,
    )


# =========================================================================== 客观条件
def _summary(tmp_path, rows):
    return load(tmp_path, rows)[2].summary


@pytest.mark.parametrize(
    "names, kw, want",
    [
        (NAMES, {}, True),
        (("a", "b"), {}, True),  # 两列就够
        (tuple(f"c{i}" for i in range(8)), {}, True),  # 恰好上限
        (tuple(f"c{i}" for i in range(9)), {}, False),  # 超过上限
        (("ctrl_mM", "drugA_mM", "drugB_mM"), {}, True),  # 同一个单位
        (("a_mM", "b_mM", "c"), {}, True),  # 单位只认出一种（另一列没有后缀）
        (("a_mM", "b_ms"), {}, False),  # 两种单位 = 不是同一种量
        (("a_g", "b_mm", "c_K"), {}, False),
        (NAMES, {"n": 1}, False),  # 每列只有一个数值格
        (NAMES, {"blanks": [(i, 1) for i in range(1, 8)]}, False),  # 有一列只剩 1 个值
        (NAMES, {"blanks": [(i, 1) for i in range(0, 6)]}, True),  # 有一列只剩 2 个值：刚好够
    ],
)
def test_the_objective_condition(tmp_path, names, kw, want):
    rows = wide_rows(names, **({"n": 8} | kw))
    s = _summary(tmp_path, rows)
    assert R.stack_ok(s, list(names)) is want


def test_stack_ok_rejects_other_shapes(tmp_path):
    s = _summary(tmp_path, wide_rows(NAMES))
    assert not R.stack_ok(s, ["ctrl"])  # 一列不是「几列」
    assert not R.stack_ok(s, ["ctrl", "ctrl"])  # 重复
    assert not R.stack_ok(s, ["ctrl", "ghost"])  # 不在表里
    assert not R.stack_ok(s, "ctrl")
    # 文字列 / 夹着文字的列 / 编号列：不是纯数值列
    rows = wide_rows(("a", "b", "c"), label="sample")
    rows[2][2] = "n/a"
    s2 = _summary(tmp_path, rows)
    assert not R.stack_ok(s2, ["a", "b"])  # b 夹着文字
    assert not R.stack_ok(s2, ["sample", "a"])
    assert R.stack_ok(s2, ["a", "c"])
    rows = [["subject", "a", "b"], *[[i % 3 + 1, 1.0 + i, 2.0 + i] for i in range(9)]]
    s3 = _summary(tmp_path, rows)
    assert "subject" in s3.id_like and not R.stack_ok(s3, ["subject", "a"])
    # 大表没有行数据：不支持
    big = dataclasses.replace(s, large=adf.LargeInfo(8, {}, [], []))
    assert not R.stack_ok(big, list(NAMES))


def test_stack_names_carry_a_common_unit():
    assert R.stack_names(["ctrl", "drugA"]) == ("value", "group")
    assert R.stack_names(["ctrl_mM", "drugA_mM"]) == ("value_mM", "group")
    assert R.stack_names(["ctrl_mM", "drugA"]) == ("value", "group")
    assert adf.axis_label("value_mM") == "value (mM)"


# =========================================================================== 运行时：转换
def test_apply_stack_is_column_major_numeric_only_and_leaves_the_input_alone():
    header = ["x", "a", "b"]
    rows = [["1", "", "5"], ["2", "3.5", "nan"], ["3", " 4 ", "n/a"], ["4", "", "6"]]
    h, out = adf.apply_stack(header, rows, ["b", "a"], "value", "group")
    assert h == ["value", "group"]
    # 按列逐列、列顺序 = 点名顺序；空 / nan / 文字格不产生行；值去首尾空白
    assert out == [["5", "b"], ["6", "b"], ["3.5", "a"], ["4", "a"]]
    assert rows[2][1] == " 4 "  # 只改副本
    # 类别首次出现的顺序恒等于列顺序，即使第一列的第一行是空的
    d = tpl.comparison_data(h, out, "group", "value", None, None, "as_given")
    assert d["cats"] == ["b", "a"]


def test_the_common_module_source_carries_the_new_function():
    assert "def apply_stack(" in tpl.runtime_module()[1].decode("utf-8")


# =========================================================================== 配方
def stack_recipe(tmp_path, rows=None, picks=None, **kw):
    rows = rows or wide_rows()
    picks = picks or {"y": list(NAMES), "x": "none"}
    res, prof, values = cands(tmp_path, rows, picks=picks)
    assert isinstance(res, R.Candidates) and res.items, res
    return res, prof, values


def test_rules_never_stack_and_the_response_is_unchanged_without_picks(tmp_path):
    for rows in (wide_rows(), wide_rows(label="sample"), wide_rows(("t", "a", "b"))):
        for picks in (None, {"x": "none"}, {"y": "unsure"}):
            res, _p, _v = cands(tmp_path, rows, picks=picks)
            if not isinstance(res, R.Candidates):
                continue
            assert all(c.recipe.stack == () for c in res.items)
            assert all("stack" not in c.recipe.to_dict() for c in res.items)
            assert "stack" not in (res.roles or {})


def test_naming_the_columns_without_an_ordered_x_stacks_them(tmp_path):
    res, prof, values = stack_recipe(tmp_path)
    assert res.roles["y"] == list(NAMES) and res.roles["stack"] is True
    assert res.roles["x"] is None and res.roles["group"] is None
    fams = {c.recipe.family for c in res.items}
    assert fams >= {"comparison", "distribution"}
    for c in res.items:
        r = c.recipe
        assert r.stack == NAMES and r.to_dict()["stack"] == list(NAMES)
        assert r.y == ("value",) and "group" in (r.x, r.group)
        assert R.validate_recipe(prof, r, confirmed=R.Confirmed(), values=values) == []
        assert R.validate_recipe(prof, r, confirmed=R.Confirmed()) == []  # 只有结构也合格
    # 类别顺序 = 列顺序，标签 = 列名原文
    v = R.with_cells_applied(values, res.items[0].recipe)
    assert v.columns == ["value", "group"] and len(v.rows) == 8 * 3
    d = tpl.comparison_data(v.columns, v.rows, "group", "value", None, None, "as_given")
    assert d["cats"] == list(NAMES)


def test_category_order_follows_the_table_not_the_order_named(tmp_path):
    res, _p, _v = stack_recipe(tmp_path, picks={"y": ["drugB", "ctrl", "drugA"], "x": "none"})
    assert res.roles["y"] == list(NAMES) and all(c.recipe.stack == NAMES for c in res.items)


def test_a_label_column_is_ignored_and_other_columns_are_not_in_the_long_table(tmp_path):
    res, prof, values = stack_recipe(
        tmp_path,
        rows=wide_rows(label="sample"),
        picks={"y": list(NAMES), "x": "none"},
    )
    assert res.roles["y"] == list(NAMES) and res.roles["group"] is None
    v = R.with_cells_applied(values, res.items[0].recipe)
    assert v.columns == ["value", "group"]


def test_blank_cells_are_missing_not_observations(tmp_path):
    rows = wide_rows(blanks=[(0, 0), (3, 1), (4, 1)])
    res, prof, values = stack_recipe(tmp_path, rows=rows)
    v = R.with_cells_applied(values, res.items[0].recipe)
    per = {k: sum(1 for r in v.rows if r[1] == k) for k in NAMES}
    assert per == {"ctrl": 7, "drugA": 6, "drugB": 8}
    assert P.unplotted_rows(values, res.items[0].recipe) == []


def test_with_an_ordered_x_the_columns_stay_one_line_each(tmp_path):
    rows = wide_rows(("t", "a", "b", "c"))
    for picks in (
        {"y": ["a", "b", "c"]},
        {"y": ["a", "b", "c"], "x": "t"},
        {"y": ["a", "b", "c"], "x": "unsure"},
    ):
        res, _p, _v = cands(tmp_path, rows, picks=picks)
        assert res.roles["x"] == "t" and res.roles["y"] == ["a", "b", "c"]
        assert "stack" not in res.roles and all(c.recipe.stack == () for c in res.items)
        trend = [c.recipe for c in res.items if c.recipe.family == "trend"]
        assert trend and all(r.y == ("a", "b", "c") for r in trend)
    # 用户明确说没有横轴：这时才是「列当分组」
    res, _p, _v = cands(tmp_path, rows, picks={"y": ["a", "b", "c"], "x": "none"})
    assert res.roles["stack"] is True and res.roles["x"] is None


def test_a_table_with_a_text_group_column_falls_back_to_the_rule_result(tmp_path):
    rows = [["strain", "rep1", "rep2", "rep3"]]
    for g in ("WT", "KO"):
        rows += [[g, 5 + i * 0.1, 5.5 + i * 0.1, 6 + i * 0.1] for i in range(5)]
    named, _p, _v = cands(tmp_path, rows, picks={"y": ["rep1", "rep2", "rep3"], "x": "none"})
    plain, _p2, _v2 = cands(tmp_path, rows, picks={})
    assert type(named) is type(plain)
    if isinstance(named, R.Candidates):
        assert named.roles == plain.roles
        assert [c.recipe.to_dict() for c in named.items] == [
            c.recipe.to_dict() for c in plain.items
        ]
        assert all(c.recipe.stack == () for c in named.items)
    # 点名了分组列也一样：不支持「列当分组再分组」
    both, _p3, _v3 = cands(
        tmp_path, rows, picks={"y": ["rep1", "rep2", "rep3"], "x": "none", "group": "strain"}
    )
    assert isinstance(both, R.NeedsChoice) or all(c.recipe.stack == () for c in both.items)


def test_naming_a_numcat_group_together_falls_back(tmp_path):
    rows = [["code", "a", "b"], *[[i % 2, 1.0 + i * 0.3, 2.0 + i * 0.2] for i in range(10)]]
    res, _p, _v = cands(tmp_path, rows, picks={"y": ["a", "b"], "x": "none", "group": "code"})
    assert isinstance(res, R.NeedsChoice) or all(c.recipe.stack == () for c in res.items)


def test_different_units_are_not_stacked(tmp_path):
    rows = wide_rows(("mass_g", "length_mm", "temp_K"))
    y = ["mass_g", "length_mm", "temp_K"]
    res, _p, _v = cands(tmp_path, rows, picks={"y": y, "x": "none"})
    plain, _p2, _v2 = cands(tmp_path, rows, picks={"x": "none"})  # 只去掉多列纵轴这一项
    assert type(res) is type(plain)
    if isinstance(res, R.Candidates):
        assert res.roles == plain.roles and "stack" not in res.roles
        assert [c.recipe.to_dict() for c in res.items] == [c.recipe.to_dict() for c in plain.items]


def test_validate_recipe_judges_the_stack_fact(tmp_path):
    res, prof, values = stack_recipe(tmp_path)
    good = next(c.recipe for c in res.items if c.recipe.family == "comparison")
    conf = R.Confirmed()

    def problems(**kw):
        return [
            (p.code, p.field)
            for p in R.validate_recipe(
                prof, dataclasses.replace(good, **kw), confirmed=conf, values=values
            )
        ]

    assert problems() == []
    assert ("roles_invalid", "y") in problems(y=("ctrl",))  # y 必须是转换后的值列
    assert ("roles_invalid", "stack") in problems(x=None, group=None)  # 类别列得真的参与作图
    assert ("column_not_found", "stack[1]") in problems(stack=("ctrl", "ghost"))
    assert ("roles_invalid", "stack") in problems(stack=("ctrl",))  # 至少两列
    assert ("column_type_mismatch", "stack[1]") in problems(stack=("ctrl", "ctrl"))
    assert ("roles_invalid", "stack") in problems(unit="ctrl")
    assert ("roles_invalid", "stack") in problems(numcat=("ctrl",))
    assert ("roles_invalid", "stack") in problems(family="trend", x="ctrl", y=("value",))
    # 没有这条事实，原配方就是不合格的（值列 / 类别列不在原表里）
    assert any(c == "column_not_found" for c, _f in problems(stack=())), (
        "without the fact the synthetic columns do not exist"
    )


def test_stack_with_box_needs_enough_values_per_column(tmp_path):
    res, prof, values = stack_recipe(tmp_path, rows=wide_rows(n=3))
    box = dataclasses.replace(
        res.items[0].recipe,
        family="comparison",
        layers=(R.Layer("box", "none", "main"),),
        x="group",
        group=None,
    )
    got = R.validate_recipe(prof, box, confirmed=R.Confirmed(), values=values)
    assert any(p.code == "box_violin_n_small" for p in got)


def test_recipe_round_trips_and_rejects_a_malformed_stack(tmp_path):
    res, _p, _v = stack_recipe(tmp_path)
    r = res.items[0].recipe
    assert R.FigureRecipe.from_dict(json.loads(json.dumps(r.to_dict()))) == r
    plain = dataclasses.replace(r, stack=())
    assert "stack" not in plain.to_dict()
    for bad in ("ctrl", [1], [""], ["a"] * 9):
        with pytest.raises(R.RecipeError) as e:
            R.FigureRecipe.from_dict({**r.to_dict(), "stack": bad})
        assert e.value.code == "recipe_malformed"


def test_large_tables_and_merged_tables_do_not_accept_the_fact(tmp_path, monkeypatch):
    res, prof, values = stack_recipe(tmp_path)
    rec = res.items[0].recipe
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    d = tmp_path / "prof"
    big = adf.inspect_data_source(d / "t.csv", v2=True, allow_large=True)
    prof2 = adf.FolderProfile((adf.TableEntry("t.csv", None, False, big),), (), False, None)
    got = R.validate_recipe(prof2, rec, confirmed=R.Confirmed(), values=big)
    assert got and {p.code for p in got} <= {"mark_not_allowed", "column_not_found"}
    # 大表上点名 = 回到规则结果，不出错也不出零候选
    out = R.default_candidates(
        prof2,
        table="t.csv",
        confirmed=R.Confirmed(),
        values=big,
        picks={"y": list(NAMES), "x": "none"},
    )
    assert isinstance(out, (R.Candidates, R.NeedsChoice))
    assert not (isinstance(out, R.Candidates) and any(c.recipe.stack for c in out.items))


# =========================================================================== 模型点名
def _understand(tmp_path, rows, shape, hint=None):
    prof, values, entry = load(tmp_path, rows)
    return ROLES.interpret(entry, values, shape, hint), prof


def _shape(cols, y, x="none", group="none", roles=None):
    return {
        "columns": [{"name": c, "role": (roles or {}).get(c, "measure")} for c in cols],
        "picks": {"y": y, "x": x, "group": group},
    }


def test_the_model_naming_the_columns_is_adopted_when_the_rule_asks_or_settles_otherwise(tmp_path):
    # 有样品名列：规则要问「选哪个值」，采纳模型的多列
    rows = wide_rows(label="sample")
    u, _p = _understand(tmp_path, rows, _shape(rows[0], list(NAMES), roles={"sample": "row_id"}))
    assert u.picks == {"y": list(NAMES), "x": "none"} and u.dropped == 0
    # 没有样品名列：规则已落定「第一列当横轴」，模型明确判断全是测量值时也采纳
    rows = wide_rows()
    u2, _p = _understand(tmp_path, rows, _shape(rows[0], list(NAMES)))
    assert u2.picks == {"y": list(NAMES), "x": "none"}
    res, _pf, _v = cands(tmp_path, rows, picks=u2.picks)
    assert res.roles["y"] == list(NAMES) and res.roles["stack"] is True


def test_the_model_stack_is_not_adopted_when_it_contradicts_itself_or_the_columns_fail(tmp_path):
    rows = wide_rows(("t", "a", "b", "c"))
    # 另一列被模型自己判成有序横轴，却又说没有横轴：自相矛盾，不采纳
    u, _p = _understand(tmp_path, rows, _shape(rows[0], ["a", "b", "c"], roles={"t": "ordered_x"}))
    assert "y" not in u.picks or u.picks["y"] != ["a", "b", "c"]
    assert u.dropped >= 1
    # 角色否决：有一列是 error
    rows = wide_rows()
    u2, _p = _understand(tmp_path, rows, _shape(rows[0], list(NAMES), roles={"drugB": "error"}))
    assert u2.picks.get("y") != list(NAMES)
    # 不是客观上能当分组的列（夹着文字）
    rows = wide_rows()
    rows[2][1] = "n/a"
    u3, _p = _understand(tmp_path, rows, _shape(rows[0], list(NAMES)))
    assert u3.picks.get("y") != list(NAMES)
    # 超过上限
    names = tuple(f"c{i}" for i in range(9))
    rows = wide_rows(names)
    u4, _p = _understand(tmp_path, rows, _shape(rows[0], list(names)))
    assert u4.picks.get("y") != list(names)


def test_parse_answers_accepts_up_to_the_stack_limit():
    names = [f"c{i}" for i in range(R.STACK_MAX_COLUMNS)]
    assert P.parse_answers({"y": names, "x": "none"}) == {"y": names, "x": "none"}
    with pytest.raises(P.ProposeError):
        P.parse_answers({"y": [*names, "extra"]})
    with pytest.raises(P.ProposeError):
        P.parse_answers({"y": []})


# =========================================================================== 真 app + 真 safe worker
def _answers(**extra):
    return {"y": list(NAMES), "x": "none", **extra}


@needs_worker
def test_user_naming_the_columns_renders_and_verifies_end_to_end(project):
    m, client, figs, _ = project
    put(figs, "data/w.csv", wide_rows(label="sample"))
    body = propose(client, render="lazy", answers=_answers()).get_json()
    assert body["kind"] == "candidates", body
    roles = body["roles"]
    assert roles["y"] == list(NAMES) and roles["stack"] is True
    assert roles["x"] is None and roles["group"] is None
    for item in body["items"]:  # 响应里每个备选画的列 = roles.y
        assert item["values"]["y"] == roles["y"] and item["values"]["stack"] == roles["y"]
        assert item["values"]["x"] is None and item["values"]["group"] is None
    first = body["items"][0]
    thumb = client.get(first["thumbnail_url"])  # 真 worker 渲染，候选指纹核对过
    assert thumb.status_code == 200 and thumb.data.startswith(b"\x89PNG"), thumb.get_json()
    c = commit(client, body["session"], first["id"])
    assert c.status_code == 200, c.get_json()
    out = c.get_json()
    assert out["task"]["outcome"] == "completed", out
    assert out["data_check"] == "verified", out
    assert checks_of(out["task"]["task_id"])["render.figure"] == ("verified", None)
    script = (figs / out["script"]).read_text(encoding="utf-8")
    assert "STACK = ['ctrl', 'drugA', 'drugB']" in script
    assert "apply_stack(header, rows, STACK, *STACK_NAMES)" in script
    assert out["unplotted"]["count"] == 0
    # 数据文件原样（不改用户的文件）
    assert (figs / "data" / "w.csv").read_text(encoding="utf-8").startswith("sample,ctrl,drugA")


@needs_worker
@pytest.mark.parametrize("which", [0, 1, 2, 3])
def test_every_stacked_candidate_renders_and_verifies(project, which):
    m, client, figs, _ = project
    put(figs, "data/w.csv", wide_rows(blanks=[(2, 1), (5, 2)]))
    body = propose(client, render="lazy", answers=_answers()).get_json()
    assert body["kind"] == "candidates", body
    if which >= len(body["items"]):
        pytest.skip("fewer candidates")
    item = body["items"][which]
    c = commit(client, body["session"], item["id"])
    out = c.get_json()
    assert c.status_code == 200 and out["data_check"] == "verified", (item["title_code"], out)


@needs_worker
def test_the_model_naming_the_columns_renders_and_verifies_end_to_end(project, tmp_path):
    m, client, figs, _ = project
    rows = wide_rows()
    put(figs, "data/w.csv", rows)
    u, _p = _understand(tmp_path, rows, _shape(rows[0], list(NAMES)))
    body = propose(client, render="lazy", answers={**u.picks}).get_json()
    assert body["kind"] == "candidates" and body["roles"]["y"] == list(NAMES), body
    c = commit(client, body["session"], body["items"][0]["id"])
    out = c.get_json()
    assert c.status_code == 200 and out["data_check"] == "verified", out


@needs_worker
def test_xlsx_sheets_are_stacked_too(project):
    from tests.test_ai_data_profile import make_xlsx

    m, client, figs, _ = project
    (figs / "data").mkdir(parents=True, exist_ok=True)
    make_xlsx(figs / "data" / "book.xlsx", {"Data": wide_rows()})
    body = propose(client, render="lazy", answers=_answers()).get_json()
    assert body["kind"] == "candidates" and body["roles"]["stack"] is True, body
    c = commit(client, body["session"], body["items"][0]["id"])
    out = c.get_json()
    assert c.status_code == 200 and out["data_check"] == "verified", out


@needs_worker
def test_without_naming_the_response_has_no_stack(project):
    m, client, figs, _ = project
    put(figs, "data/w.csv", wide_rows(label="sample"))
    body = propose(client, render="lazy", answers={"y": "ctrl"}).get_json()
    assert body["kind"] == "candidates", body
    assert "stack" not in body["roles"]
    assert all("stack" not in i["values"] for i in body["items"])
    c = commit(client, body["session"], body["items"][0]["id"])
    script = (figs / c.get_json()["script"]).read_text(encoding="utf-8")
    assert "STACK" not in script and "apply_stack" not in script


@needs_worker
def test_text_group_column_gives_the_rule_result_end_to_end(project):
    m, client, figs, _ = project
    rows = [["strain", "rep1", "rep2", "rep3"]]
    for g in ("WT", "KO"):
        rows += [[g, 5 + i * 0.1, 5.5 + i * 0.1, 6 + i * 0.1] for i in range(5)]
    put(figs, "data/s.csv", rows)
    a = propose(client, render="lazy", answers=_answers(y=["rep1", "rep2", "rep3"])).get_json()
    b = propose(client, render="lazy").get_json()
    assert a["kind"] == b["kind"] and a["roles"] == b["roles"]
    assert [i["title_code"] for i in a["items"]] == [i["title_code"] for i in b["items"]]


def test_large_and_merged_tables_fall_back_cleanly(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/a.csv", wide_rows())
    put(figs, "data/b.csv", wide_rows())
    merged = propose(
        client, tables=[{"path": "a.csv"}, {"path": "b.csv"}], render="lazy", answers=_answers()
    )
    assert merged.status_code == 200, merged.get_json()
    mb = merged.get_json()
    assert mb["kind"] in ("candidates", "ask", "nothing") and "stack" not in (mb.get("roles") or {})
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    big = propose(client, table="a.csv", render="lazy", answers=_answers())
    assert big.status_code == 200, big.get_json()
    bb = big.get_json()
    assert bb["kind"] in ("candidates", "ask", "nothing")
    assert "stack" not in (bb.get("roles") or {})
    if bb["kind"] == "candidates":
        assert bb["items"]  # 不出零候选
