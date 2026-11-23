# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「没画的行」要有计数和规则（sciviz G-055；ADR 0178 修订 2026-11-24）。

判据的主语：**生成的脚本里每一处跳过一行的地方，`unplotted_rows` 都有对应的一条**——用户看到的「有 N 行没画」与图上实际少的行数一致。
比法：同一张表、同一个配方，拿脚本自己的数据准备函数（`comparison_data` / `trend_data` / `relationship_data`，生成的脚本运行时调的就是它们）
数出画进去了多少行，与「总行数 − 没画的行数」对；再逐条看原因码。
"""

from __future__ import annotations

import socket

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_propose as P, ai_data_template as tpl
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


def recipes(client, **body):
    """出一轮备选，回 `(摘要, {family: 第一个该类配方})`。"""
    out = propose(client, render="lazy", **body).get_json()
    assert out["kind"] == "candidates", out
    sess = next(s for s in P._SESSIONS.values() if s.id == out["session"])
    by = {}
    for item in sess.items.values():
        by.setdefault(item.recipe.family, item)
    return out, sess, by


def summary(figs, rel):
    return adf.inspect_data_source(figs / rel)


def reasons(values, recipe):
    return [(r["row"], r["column"], r["reason"]) for r in P.unplotted_rows(values, recipe)]


# 一张比较类的表：第 3 行分组空、第 5 行类别空、第 7 行纵轴空、第 9 行纵轴 NaN
COMPARISON = [
    ["treatment", "sex", "weight"],
    ["a", "f", 1.0],
    ["a", "", 1.1],
    ["b", "m", 1.2],
    ["", "f", 1.3],
    ["b", "f", 1.4],
    ["a", "m", ""],
    ["a", "m", 1.6],
    ["b", "m", "NaN"],
    ["a", "f", 1.8],
    ["b", "f", 1.9],
    ["a", "m", 2.0],
    ["b", "m", 2.1],
]


def test_comparison_counts_empty_category_empty_group_and_missing_y(project):
    _, client, figs, _ = project
    put(figs, "data/c.csv", COMPARISON)
    _out, _sess, by = recipes(client, answers={"x": "treatment", "y": "weight", "group": "sex"})
    item = by["comparison"]
    rec = item.recipe
    assert {rec.x, rec.group} == {"treatment", "sex"} and rec.y == ("weight",)
    values = summary(figs, "data/c.csv")
    got = reasons(values, rec)

    def why(col):  # 哪一列当类别、哪一列当分组由规则定；原因码跟着角色走
        return "category_empty" if col == rec.x else "group_empty"

    assert got == [
        (3, "sex", why("sex")),
        (5, "treatment", why("treatment")),
        (7, "weight", "y_missing"),
        (9, "weight", "y_missing"),
    ]
    assert item.unplotted == 4
    # 脚本自己的数据准备函数画进去的行数 = 总行数 − 没画的
    d = tpl.comparison_data(
        values.columns, values.rows, rec.x, rec.y[0], rec.group, rec.facet, rec.order
    )
    drawn = sum(len(v) for v in d["cells"].values())
    assert drawn == len(values.rows) - len(got) == 8


TREND = (
    [["time", "signal", "sample"]]
    + [[t, 1.0 + 0.5 * t, "A"] for t in range(3)]
    + [[3, 2.5, ""]]  # 第 5 行：分组空
    + [[4, "", "A"]]  # 纵轴空：缺失（线断开），不算没画
    + [[t, 1.0 + 0.5 * t, "A"] for t in range(5, 9)]
    + [["", 9.9, "B"]]  # 第 11 行：横轴没有值
    + [[t, 2.0 + 0.5 * t, "B"] for t in range(9)]
)


def test_trend_counts_empty_group_and_keeps_missing_y_as_a_gap(project):
    _, client, figs, _ = project
    put(figs, "data/t.csv", TREND)
    _out, _sess, by = recipes(client, answers={"x": "time", "y": "signal", "group": "sample"})
    rec = by["trend"].recipe
    assert (rec.x, rec.group) == ("time", "sample")
    values = summary(figs, "data/t.csv")
    assert reasons(values, rec) == [(5, "sample", "group_empty"), (11, "time", "x_not_a_value")]
    assert by["trend"].unplotted == 2


RELATIONSHIP = [
    ["height", "mass", "site"],
    [1.50, 50.1, "n"],
    [1.61, "", "n"],  # 第 3 行：纵轴空
    [1.72, 61.3, ""],  # 第 4 行：分组空
    ["", 70.0, "s"],  # 第 5 行：横轴没有值
    [1.83, 72.9, "s"],
    [1.94, 80.2, "s"],
    [1.55, 52.0, "n"],
    [1.66, 58.4, "n"],
    [1.77, 66.6, "s"],
    [1.88, 75.5, "s"],
]


def test_relationship_counts_missing_y_and_empty_group(project):
    _, client, figs, _ = project
    put(figs, "data/r.csv", RELATIONSHIP)
    _out, _sess, by = recipes(client, answers={"x": "height", "y": "mass", "group": "site"})
    rec = by["relationship"].recipe
    values = summary(figs, "data/r.csv")
    got = reasons(values, rec)
    assert got == [
        (3, "mass", "y_missing"),
        (4, "site", "group_empty"),
        (5, "height", "x_not_a_value"),
    ]
    d = tpl.relationship_data(values.columns, values.rows, rec.x, rec.y[0], rec.group, rec.facet)
    drawn = sum(len(xs) for xs, _ys in d["pairs"].values())
    assert drawn == len(values.rows) - len(got) == 7


def test_a_clean_table_reports_nothing(project):
    _, client, figs, _ = project
    clean = [r for r in COMPARISON if all(str(c) not in ("", "NaN") for c in r)]
    put(figs, "data/ok.csv", clean)
    out, sess, _by = recipes(client)
    values = summary(figs, "data/ok.csv")
    assert all(P.unplotted_rows(values, i.recipe) == [] for i in sess.items.values())
    assert all(i["unplotted"] == {"count": 0, "code": None} for i in out["items"])


def test_distribution_counts_missing_y_and_empty_group(project):
    _, client, figs, _ = project
    put(figs, "data/c.csv", COMPARISON)
    _out, _sess, by = recipes(client, answers={"x": "treatment", "y": "weight", "group": "sex"})
    rec = by["distribution"].recipe
    values = summary(figs, "data/c.csv")
    got = P.unplotted_rows(values, rec)
    d = tpl.comparison_data(
        values.columns, values.rows, None, rec.y[0], rec.group, rec.facet, "as_given"
    )
    drawn = sum(len(v) for v in d["cells"].values())
    assert drawn == len(values.rows) - len(got)
    assert {r["reason"] for r in got} <= {"group_empty", "facet_empty", "y_missing"}
    assert sum(r["reason"] == "y_missing" for r in got) == 2


def test_one_empty_group_cell_does_not_cost_the_whole_trend_chart(project):
    """分组列有一格是空的：那一行不画、计数里有它，但**不能**被当成「只有一个点的一组」把整张趋势图判成画不了。"""
    _, client, figs, _ = project
    put(figs, "data/t.csv", TREND)
    out, _sess, by = recipes(client, answers={"x": "time", "y": "signal", "group": "sample"})
    assert "trend" in by
    assert "trend_too_few_x" not in [d["reason_code"] for d in out.get("dropped", [])]
