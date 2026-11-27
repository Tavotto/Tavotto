# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「需要你确认」与「依据」的后端约定（ADR 0178 修订 2026-11-24）。

判据的主语：
* **误差线画的是什么只由用户点选**（`answers.error`）：没点 = 模板默认（均值 ± 标准差）且响应里没有 `error_confirmed`；点了就换掉每个
  备选里「均值 ± 误差」的层、其余不动，响应带 `error_confirmed: true`。模型的建议永远写不出这个键（`ai_data_roles` 只给列角色）。
* **「依据」用的数**（`facts`）就是画进去的那些行数出来的：与「没画的行」加起来等于表里的行数。
"""

from __future__ import annotations

import socket

import pytest

from tavotto.engine import ai_data_propose as P, ai_data_roles
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import project, propose, put  # noqa: F401


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


GROUPS = [["group", "value"]] + [[g, v] for g in "ABC" for v in (1.0, 1.2, 0.9, 1.1, 1.3, 0.8)]


def by_code(out):
    return {i["title_code"]: i for i in out["items"]}


def summaries(item):
    return [(layer["mark"], layer["summary"]) for layer in item["values"]["layers"]]


def test_default_error_is_sd_and_not_confirmed(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    out = propose(client, render="lazy").get_json()
    assert "error_confirmed" not in out
    bar = by_code(out)["bar_error"]
    assert bar["error"] == "sd" and ("errorbar", "mean_sd") in summaries(bar)
    # 没有误差线的备选：没有这一项
    assert "error" not in by_code(out)["ecdf_groups"]


@pytest.mark.parametrize(
    "kind, summary", [("sd", "mean_sd"), ("sem", "mean_sem"), ("ci95", "mean_ci95")]
)
def test_the_users_choice_replaces_every_mean_error_layer_and_nothing_else(project, kind, summary):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    base = propose(client, render="lazy").get_json()
    out = propose(client, render="lazy", answers={"error": kind}).get_json()
    assert out["error_confirmed"] is True
    assert [i["title_code"] for i in out["items"]] == [i["title_code"] for i in base["items"]]
    for a, b in zip(base["items"], out["items"], strict=True):
        for (ma, sa), (mb, sb) in zip(summaries(a), summaries(b), strict=True):
            assert ma == mb
            assert sb == (summary if sa == "mean_sd" else sa)
    assert by_code(out)["bar_error"]["error"] == kind
    # 脚本里真的换了
    n = [i["title_code"] for i in out["items"]].index("bar_error") + 1
    script = (P.CANDIDATE_ROOT / out["session"] / "tavotto_figures" / f"cand_{n}.py").read_text(
        encoding="utf-8"
    )
    assert f"'{summary}'" in script or f'"{summary}"' in script


@pytest.mark.parametrize("bad", ["SD", "variance", "", "unsure"])
def test_an_unknown_error_answer_is_ignored(project, bad):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS)
    out = propose(client, render="lazy", answers={"error": bad}).get_json()
    assert out["kind"] == "candidates" and "error_confirmed" not in out
    assert by_code(out)["bar_error"]["error"] == "sd"


def test_an_error_choice_alone_does_not_count_as_the_user_taking_over_the_columns():
    assert P._user_took_over({"error": "sem"}) is False
    assert P._user_took_over({"design": "independent"}) is False


def test_the_model_cannot_write_error_or_design():
    """模型的建议只有列角色：它能给的 `picks` 键是闭集 `y` / `x` / `group`（多一个键整份回答作废）；`error` / `design` / `unit` 只来自用户的点选。"""
    assert ai_data_roles._PICK_KEYS == {"y", "x", "group"}
    assert not ai_data_roles._PICK_KEYS & {"error", "design", "unit"}


def test_facts_are_counted_from_the_rows_that_are_drawn(project):
    _, client, figs, _ = project
    rows = GROUPS + [["", 9.9], ["A", ""], ["B", "nan"]]
    put(figs, "data/g.csv", rows)
    out = propose(client, render="lazy").get_json()
    item = by_code(out)["points_mean"]
    f = item["facts"]
    assert f["rows"] == 21 and f["drawn"] == 18
    assert f["rows"] - f["drawn"] == item["unplotted"]["count"] == 3
    # 三行都是缺值（分组空、纵轴空、纵轴 NaN），没有「不是数」的
    assert (f["skipped_missing"], f["skipped_other"]) == (3, 0)
    assert (f["groups"], f["n_min"], f["n_max"]) == (3, 6, 6)
    assert f["x_values"] is None


def test_facts_for_a_trend_count_series_and_x_values(project):
    _, client, figs, _ = project
    rows = [["time", "signal", "sample"]] + [[t, 1 + 0.5 * t, s] for s in "AB" for t in range(9)]
    put(figs, "data/t.csv", rows)
    out = propose(
        client, render="lazy", answers={"x": "time", "y": "signal", "group": "sample"}
    ).get_json()
    item = next(i for i in out["items"] if i["values"]["family"] == "trend")
    assert item["facts"] == {
        "rows": 18,
        "drawn": 18,
        "values": 18,
        "groups": 2,
        "n_min": 9,
        "n_max": 9,
        "x_values": 9,
        "skipped_missing": 0,
        "skipped_other": 0,
    }


def test_facts_tell_missing_cells_from_cells_that_are_not_numbers(project):
    """横轴那一格是文字（不是数）与是空的（缺值）分开数：依据句里说的原因要对得上。"""
    _, client, figs, _ = project
    rows = (
        [["dose", "resp"]] + [[d, 1 + 0.5 * d] for d in range(1, 11)] + [["", 9.0], ["high", 9.5]]
    )
    put(figs, "data/x.csv", rows)
    out = propose(client, render="lazy", answers={"x": "dose", "y": "resp"}).get_json()
    item = next(i for i in out["items"] if i["values"]["family"] == "relationship")
    f = item["facts"]
    assert (f["skipped_missing"], f["skipped_other"]) == (1, 1)
    assert f["skipped_missing"] + f["skipped_other"] == item["unplotted"]["count"]


def test_confirming_the_error_keeps_the_paired_variants_of_a_merged_figure(project):
    """合画里成对的两种画法（按文件再拆开 / 只按文件）靠 `variant` 分清；用户点选误差类型换了配方之后，这个标记不能丢（Codex 评审 #2176）。"""
    from tests.test_ai_data_merge import _arms

    _, client, figs, _ = project
    rows = _arms(lambda i, k: "p" if k % 2 == 0 else "q")
    for i, n in enumerate(("a", "b")):
        put(figs, f"data/{n}.csv", rows(i))
    tables = [{"path": "a.csv"}, {"path": "b.csv"}]
    base = propose(client, tables=tables, render="lazy", answers={"y": "resp_mV"}).get_json()
    out = propose(
        client, tables=tables, render="lazy", answers={"y": "resp_mV", "error": "sem"}
    ).get_json()
    assert out["error_confirmed"] is True
    marks = [(i["title_code"], i.get("variant")) for i in base["items"]]
    assert any(v for _c, v in marks), marks  # 这张表确实有成对的画法
    assert [(i["title_code"], i.get("variant")) for i in out["items"]] == marks
    # 成对的那几张里确实有配方被换过的（均值 ± 标准差的层变成了均值 ± 标准误），而它们的标记还在
    changed = [
        i
        for i in out["items"]
        if i.get("variant")
        and any(layer["summary"] == "mean_sem" for layer in i["values"]["layers"])
    ]
    assert changed, [summaries(i) for i in out["items"]]


def test_facts_count_only_the_points_that_are_really_drawn_on_a_scatter(project):
    """散点一行一个点：横轴不是值的行纵轴再有数也没画上，「画进去的」不能把它算进去（Codex 评审 #2176）。"""
    _, client, figs, _ = project
    rows = (
        [["dose", "resp"]] + [[d, 1 + 0.5 * d] for d in range(1, 11)] + [["", 9.0], ["high", 9.5]]
    )
    put(figs, "data/x.csv", rows)
    out = propose(client, render="lazy", answers={"x": "dose", "y": "resp"}).get_json()
    item = next(i for i in out["items"] if i["values"]["family"] == "relationship")
    f = item["facts"]
    assert f["drawn"] == 10 and item["unplotted"]["count"] == 2
    assert f["drawn"] + f["skipped_missing"] + f["skipped_other"] == f["rows"] == 12


def test_facts_with_two_y_columns_count_every_column_not_only_the_first(project):
    """宽表两列纵轴各一条线：依据数的是两列各自画进去的值，与列序无关（以前只看第一列，A 有空格时把 8 行全画的图说成「按 4 个数值」）。
    独立手算：x=1..8；A 在偶数 x 空（4 个值），B 每行都有（8 个值）→ 8 行都画了、共 12 个值、横轴 8 个取值。"""
    _, client, figs, _ = project
    rows = [["x", "A", "B"]] + [[t, "" if t % 2 == 0 else 1.0 + t, 5.0 + t] for t in range(1, 9)]
    put(figs, "data/v.csv", rows)
    swapped = [["x", "B", "A"]] + [[r[0], r[2], r[1]] for r in rows[1:]]
    put(figs, "data/w.csv", swapped)
    got = []
    for table, ys in (("v.csv", ["A", "B"]), ("w.csv", ["B", "A"]), ("v.csv", ["B", "A"])):
        out = propose(client, table=table, render="lazy", answers={"x": "x", "y": ys}).get_json()
        item = next(i for i in out["items"] if i["values"]["family"] == "trend")
        assert item["values"]["y"] == ys
        f = item["facts"]
        got.append((f["rows"], f["drawn"], f["values"], f["x_values"]))
        assert item["unplotted"]["count"] == 0
    assert got == [(8, 8, 12, 8)] * 3, got


def test_facts_values_equal_drawn_for_a_single_y(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", GROUPS + [["A", ""]])
    item = by_code(propose(client, render="lazy").get_json())["points_mean"]
    assert item["facts"]["values"] == item["facts"]["drawn"] == 18
