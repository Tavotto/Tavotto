# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""误差列不是测量值（sciviz G-053；ADR 0178 修订 2026-11-24）。

判据的主语：**名字在说「我是误差量」的数值列，规则不把它当纵轴 / 横轴去画**——除非用户自己点了它，或者去掉后表里一个数值列都不剩。
只看列名的词，不做子串匹配；可能是别的意思的词（`std` 标准品、`se` 硒、`err`、`ci`）要表里另有它修饰的那一列才算；名字只说 `err` 的
记 `unknown`，绝不当成 SD。
"""

from __future__ import annotations

import socket

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_propose as P, ai_data_recipe as R
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


@pytest.mark.parametrize(
    "name, others, want",
    [
        # 整个名字就是这个词：单凭名字就算
        ("sd", ["dose", "mean_response"], "sd"),
        ("SD", [], "sd"),
        ("stdev", ["x"], "sd"),
        ("stderr", [], "sem"),
        ("err", ["x", "y"], "unknown"),
        ("error", [], "unknown"),
        # 中文词
        ("标准差", [], "sd"),
        ("标准误", [], "sem"),
        ("误差", [], "unknown"),
        # 其它：要有它修饰的那一列
        ("mass_sd", ["mass"], "sd"),
        ("response_sem", ["mean_response"], "sem"),
        ("mass_se", ["mass"], "sem"),
        ("yErr", ["y"], "unknown"),
        ("sem", ["time", "mean"], "sem"),
        ("se", ["mean"], "sem"),
        ("std", ["avg_height"], "sd"),
        ("ci95", ["avg"], "ci"),
        ("SEM_depth", ["depth"], "sem"),
        # 没有搭档：不算（扫描电镜、硒、标准品……宁可认不出）
        ("sem", ["dose", "response"], None),
        ("SEM", ["AFM"], None),
        ("SEM_depth_nm", ["time"], None),
        ("Se", ["Zn", "Cu"], None),
        ("se", ["dose", "conc"], None),
        ("std", ["x"], None),
        ("std_curve", ["x"], None),
        ("selenium_se", ["x"], None),
        ("depth_sd", ["depth_nm"], None),
        ("error_rate", ["x"], None),
        # 不是词：不算
        ("residue", ["x"], None),
        ("used", [], None),
        ("semester", [], None),
        ("sdk_version", [], None),
    ],
)
def test_error_kind_reads_words_not_substrings(name, others, want):
    assert adf.error_kind(name, others) == want


def session_of(out):
    return next(s for s in P._SESSIONS.values() if s.id == out["session"])


HO18 = [["dose_uM", "mean_response", "sd"]] + [
    [d, round(10 + 8 * i, 1), round(0.8 + 0.1 * i, 2)]
    for i, d in enumerate((0.1, 0.3, 1, 3, 10, 30))
]


def test_an_sd_column_is_never_drawn_as_a_second_series(project):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", HO18)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    sess = session_of(out)
    for item in sess.items.values():
        rec = item.recipe
        assert "sd" not in rec.y and rec.x != "sd" and rec.group != "sd", rec
    first = next(iter(sess.items.values())).recipe
    assert first.y == ("mean_response",) and first.x == "dose_uM"
    assert out["roles"]["y"] == ["mean_response"]


def test_time_mean_sem_draws_only_the_mean(project):
    _, client, figs, _ = project
    put(
        figs,
        "data/ts.csv",
        [["time", "mean", "sem"]] + [[t, 1 + 0.3 * t, 0.05 * (t + 1)] for t in range(8)],
    )
    out = propose(client, render="lazy").get_json()
    sess = session_of(out)
    assert all(i.recipe.y == ("mean",) and i.recipe.x == "time" for i in sess.items.values())


def test_the_user_can_still_pick_the_error_column_explicitly(project):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", HO18)
    out = propose(client, render="lazy", answers={"y": "sd", "x": "dose_uM"}).get_json()
    sess = session_of(out)
    assert all(i.recipe.y == ("sd",) for i in sess.items.values())


def test_a_table_with_only_error_named_numbers_still_draws(project):
    """去掉误差列后一个数值列都不剩：当普通数值列，别让表画不了。"""
    _, client, figs, _ = project
    put(
        figs,
        "data/only.csv",
        [["batch", "sd"]] + [[b, v] for b in "ABC" for v in (0.5, 0.6, 0.7, 0.4)],
    )
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    assert all(i.recipe.y == ("sd",) for i in session_of(out).items.values())


def test_error_columns_lists_kinds_for_numeric_columns_only():
    class Col:
        def __init__(self, kind):
            self.kind, self.numeric, self.text_count, self.count = kind, 5, 0, 5

    class S:
        columns = ["dose", "mean_y", "sd", "note_sd"]
        per_column = {
            "dose": Col("numeric"),
            "mean_y": Col("numeric"),
            "sd": Col("numeric"),
            "note_sd": Col("text"),
        }

    got = R.error_columns(S)
    assert got.get("sd") == "sd" and "note_sd" not in got and "dose" not in got


def test_the_error_column_stays_selectable_in_the_dropdowns(project):
    """规则不拿它画，但右栏下拉里仍然列得出来——「用户自己点了才用」在界面上要成立。"""
    _, client, figs, _ = project
    put(figs, "data/dr.csv", HO18)
    out = propose(client, render="lazy").get_json()
    opts = out["roles"]["options"]
    assert "sd" in opts["y"] and "sd" in opts["x"]
    assert out["roles"]["y"] == ["mean_response"] and out["roles"]["x"] == "dose_uM"


@pytest.mark.parametrize("bad", [[["sd"]], [1, 2], [None], {"a": 1}, 3])
def test_malformed_picks_do_not_break_the_error_column_rule(project, bad):
    _, client, figs, _ = project
    put(figs, "data/dr.csv", HO18)
    r = propose(client, render="lazy", answers={"y": bad})
    assert r.status_code in (200, 400), r.get_json()
    if r.status_code == 200 and r.get_json()["kind"] == "candidates":
        assert all("sd" not in i.recipe.y for i in session_of(r.get_json()).items.values())


def test_an_instrument_column_named_sem_is_not_dropped(project):
    """`SEM_depth_nm`（扫描电镜测的深度）是测量值：没有它修饰的搭档列，不当误差列。"""
    _, client, figs, _ = project
    rows = [["time_min", "SEM_depth_nm"]] + [[t, round(3.1 * t + 2, 1)] for t in range(0, 50, 5)]
    put(figs, "data/etch.csv", rows)
    out = propose(client, render="lazy").get_json()
    assert out["kind"] == "candidates", out
    assert all(i.recipe.y == ("SEM_depth_nm",) for i in session_of(out).items.values())
