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
