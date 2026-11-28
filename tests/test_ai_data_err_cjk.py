# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""中文的「均值 / 标准差」配成一对（ADR 0178 修订 2026-11-28）。

判据的主语：**误差列找到的搭档**（`adf.error_match` 的第二项）与由此得到的推荐图。「标准差」这些词本来就认得是误差量，缺的是搭档：
原来只找名字里带 `mean` / `avg` / `average` 的列，中文的「均值」配不上，现成的误差列就没被当成误差线用。
反例都在：旁边没有均值列不配、核心不同不配、「标准品」这种不是误差词的不认。
"""

from __future__ import annotations

import socket

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_propose as P, ai_data_recipe as ar
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import project  # noqa: F401


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
    ("name", "others", "kind", "partners"),
    [
        ("标准差", ["处理", "均值", "n"], "sd", ["均值"]),
        ("标准误", ["组", "平均值"], "sem", ["平均值"]),
        ("标准差", ["组", "mean"], "sd", ["mean"]),  # 中英混着写
        ("产量标准差", ["品种", "产量", "株高"], "sd", ["产量"]),
        ("产量标准差", ["品种", "产量均值"], "sd", ["产量均值"]),
        ("产量标准差", ["品种", "平均产量"], "sd", ["平均产量"]),
        ("株高（标准差）", ["株高", "产量"], "sd", ["株高"]),
        ("产量_标准差", ["产量"], "sd", ["产量"]),
        ("误差", ["x", "平均"], "unknown", ["平均"]),  # 种类说不清的仍是 unknown，绝不当成 SD
        ("置信区间", ["组", "均数"], "ci", ["均数"]),
    ],
)
def test_a_chinese_error_column_finds_its_mean_column(name, others, kind, partners):
    assert adf.error_match(name, others) == (kind, partners)


@pytest.mark.parametrize(
    ("name", "others"),
    [
        ("标准差", ["组", "value"]),  # 旁边没有说自己是均值的列：不配
        ("标准差", ["处理", "n"]),
        ("产量标准差", ["品种", "株高"]),  # 核心不同
        ("产量标准差", ["品种", "株高均值"]),
        ("标准差", ["标准误"]),  # 误差列不互相当搭档
    ],
)
def test_without_a_matching_mean_column_there_is_no_partner(name, others):
    kind, partners = adf.error_match(name, others)
    assert kind == "sd" and partners == []


def test_two_candidate_mean_columns_stay_ambiguous():
    assert adf.error_match("标准差", ["均值", "产量均值"]) == ("sd", ["均值", "产量均值"])


@pytest.mark.parametrize("name", ["标准品", "标准曲线", "平均值", "差值"])
def test_names_that_only_look_similar_are_not_error_columns(name):
    assert adf.error_match(name, ["均值", "浓度"]) == (None, [])


def test_english_pairing_is_unchanged():
    assert adf.error_match("mass_sd", ["mass", "id"]) == ("sd", ["mass"])
    assert adf.error_match("sem", ["group", "mean"]) == ("sem", ["mean"])
    assert adf.error_match("SEM_depth_nm", ["sample"]) == (None, [])
    assert adf.error_match("sd", ["group", "value"]) == ("sd", [])


SUMMARY = "处理,均值,标准差,n\n对照,12.4,1.1,6\nA,15.8,1.6,6\nB,19.2,2.0,6\nC,14.1,0.9,6\n"


def test_a_chinese_summary_table_is_drawn_as_means_with_its_own_error_bars(project, tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "summary.csv").write_text(SUMMARY, encoding="utf-8", newline="")
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    assert resp["kind"] == "candidates" and resp["items"], resp
    rec = next(i for i in resp["items"] if i.get("recommended"))
    v = rec["values"]
    assert (v.get("x"), v.get("y")) == ("处理", ["均值"])
    assert rec["title_code"] == "points_err"  # 用表里现成的标准差画误差线，不是拿 n 当纵轴


def test_the_same_table_without_the_mean_word_is_as_before(project, tmp_path):
    """对照：纵轴列不叫「均值」时，「标准差」没有搭档，不凭空配。"""
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "summary.csv").write_text(
        SUMMARY.replace("均值", "读数"), encoding="utf-8", newline=""
    )
    resp = P.propose(root, project="p", folder="data", render="lazy", understand=False)
    codes = [i["title_code"] for i in resp.get("items") or []]
    assert "points_err" not in codes


@pytest.mark.parametrize(
    ("label", "owner"),
    [
        ("压力标准差 (SD)", "压力均值"),
        ("压力标准差（sd）", "压力均值"),
        ("压力标准差", "压力均值"),
        ("标准差 (SD)", None),
        ("标准差", None),
        ("位移标准差 (SD)", None),
    ],
)
def test_cjk_sd_abbreviation_preserves_the_measure_in_rule_candidates(tmp_path, label, owner):
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    path = data / "two_measures.csv"
    path.write_text(
        f"时间,压力均值,temperature_mean,{label},n\n"
        "2026-01-01,101.0,24.1,0.11,6\n"
        "2026-01-02,102.0,25.3,0.29,6\n"
        "2026-01-03,103.0,24.7,0.43,6\n"
        "2026-01-04,104.0,26.0,0.58,6\n",
        encoding="utf-8",
    )
    values = adf.inspect_data_source(path, v2=True)
    for y in ("压力均值", "temperature_mean"):
        expected = label if y == owner else None
        assert ar.paired_error(values, y, ("时间",)) == expected
        out = P.propose(
            root,
            project="p",
            folder="data",
            render="lazy",
            understand=False,
            answers={"x": "时间", "y": [y]},
        )
        assert out["kind"] == "candidates", out
        candidate = next(i for i in out["items"] if i.get("recommended"))
        assert candidate["values"]["y"] == [y]
        assert candidate["title_code"] == ("line_err" if expected else "line_points")


# --------------------------------------------------------------------------- 括号里的单位不是测量量的名字（Codex #2392 P1）
@pytest.mark.parametrize(
    ("name", "others", "partners"),
    [
        ("标准差 (kPa)", ["时间", "压力均值 (kPa)"], ["压力均值 (kPa)"]),
        ("标准差（kPa）", ["时间", "压力均值（kPa）"], ["压力均值（kPa）"]),
        ("标准差[%]", ["组", "平均值[%]"], ["平均值[%]"]),
        ("压力标准差 (kPa)", ["时间", "压力均值 (kPa)", "温度均值 (K)"], ["压力均值 (kPa)"]),
        ("标准差 (kPa)", ["时间", "压力 (kPa)"], []),  # 旁边没有说自己是均值的列：仍不配
        (
            "标准差（株高）",
            ["均值（株高）", "均值（产量）"],
            ["均值（株高）"],
        ),  # 括号里是汉字 = 在点名测量量
    ],
)
def test_a_unit_in_brackets_is_not_a_measure_name(name, others, partners):
    assert adf.error_match(name, others) == ("sd", partners)


@pytest.mark.parametrize(
    "name", ["标准差 (kPa)", "标准差（%）", "标准差 [mg/L]", "标准差 (SD)", "标准差"]
)
def test_an_error_column_with_only_a_unit_names_no_measure(name):
    assert adf._cjk_error_core(name) == ""


def test_error_bars_with_units_in_the_headers_are_still_drawn(project, tmp_path):
    """评审举的那张表：`时间, 压力均值 (kPa), 标准差 (kPa)`——main 上推荐带误差线的折线，不能因为单位括号把现成的标准差丢掉。"""
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "p.csv").write_text(
        "时间,压力均值 (kPa),标准差 (kPa)\n"
        "2026-01-01,101.0,0.11\n2026-01-02,102.0,0.29\n2026-01-03,103.0,0.43\n2026-01-04,104.0,0.58\n",
        encoding="utf-8",
        newline="",
    )
    values = adf.inspect_data_source(root / "data" / "p.csv", v2=True)
    assert ar.paired_error(values, "压力均值 (kPa)", ("时间",)) == "标准差 (kPa)"
    out = P.propose(
        root,
        project="p",
        folder="data",
        render="lazy",
        understand=False,
        answers={"x": "时间", "y": ["压力均值 (kPa)"]},
    )
    rec = next(i for i in out["items"] if i.get("recommended"))
    assert rec["title_code"] == "line_err" and rec["values"]["y"] == ["压力均值 (kPa)"]
