# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""第 2 步之一：不该连线的折线（G-018 / G-052；ADR 0178 修订 2026-11-22）。

判据的主语：**候选里有没有「趋势类」、第一名是不是关系类，以及这条判断是谁做的**——
* 规则（不调模型）只看到「这一列是数值」，**单凭数据事实分不出「有序自变量」与「第二个独立测量值」**（同一张形状的表：温度 4 / 25 / 37 与批号 1 / 2 / 3，
  考卷 P08A / P08B；晶粒尺寸在 HO-04 是有序量、在 P04B 是测量值），所以规则臂**不变**：没有 `x_measure` 时响应逐字与从前相同；
* 模型把规则选作横轴的那一列判成 `measure` → `answers.x_measure = 列名`；`propose` 据此不出趋势类、关系类排第一，`roles` 与候选一致、被去掉的记进 `dropped`；
* 判成 `time` / `ordered_x`（或没给角色）→ 现状；用户换了横轴（键指向的不是当前横轴）→ 生效不了，折线照常；
* 同一个 x 有重复观测时不逐点连线（G-052，`line_duplicate_x_unconfirmed`，规则一直有）。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_cases as D  # noqa: E402
import data_figure_roles as DR  # noqa: E402

from tavotto.engine import (  # noqa: E402
    ai_data_figure as adf,
    ai_data_propose as P,
    ai_data_recipe as R,
    ai_data_roles as ROLES,
)
from tavotto.engine.agent_runner import wires  # noqa: E402
from tests.test_ai_data_propose import project, propose, put  # noqa: E402, F401

# 两个独立测量值（P04B 的形状）：横轴不单调、几乎都不重复
MEASURES = [
    ["grain_size_um", "yield_MPa"],
    [50.2, 370],
    [27.3, 288],
    [35.2, 271],
    [42.5, 330],
    [51.2, 364],
    [39.2, 343],
    [31.3, 265],
    [43.0, 278],
    [13.3, 217],
    [25.6, 281],
]
# 时间序列：同一形状（两个数值列），横轴有序
SERIES = [["time_min", "od600"], *[[t, 0.1 + 0.07 * i] for i, t in enumerate(range(0, 55, 5))]]
# 每个 day 两个平行样（P09B）
REPEATED = [["day", "od600"], *[[d, 0.2 * d + e] for d in (1, 2, 3, 4, 5) for e in (0.0, 0.05)]]


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
    ROLES.reset_for_tests()
    yield
    ROLES.reset_for_tests()


def write(tmp_path: Path, rows, name="t.csv") -> Path:
    d = tmp_path / "prof"
    d.mkdir(exist_ok=True)
    (d / name).write_text(
        "\n".join(",".join(str(c) for c in r) for r in rows) + "\n", encoding="utf-8"
    )
    return d


def cands(tmp_path, rows, picks=None):
    d = write(tmp_path, rows)
    prof = adf.profile_folder(d)
    values = adf.inspect_data_source(d / "t.csv", v2=True)
    picks = dict(picks or {})
    key_of = {"pick_value": "y", "pick_group": "group", "pick_x": "x"}
    for _ in range(4):  # 要问的问题都答「不确定」：与 `propose(render="lazy")` 同一口径
        res = R.default_candidates(
            prof, table="t.csv", confirmed=R.Confirmed(), values=values, picks=picks
        )
        key = key_of.get(getattr(res, "question_code", ""))
        if key is None:
            break
        picks[key] = "unsure"
    return res, prof, values


def codes(res) -> list[str]:
    return [c.code for c in res.items]


def judged(tmp_path, rows, roles: dict[str, str], picks=None):
    """模型对每列的判断（已过形状校验）→ 真的 `interpret` 的结果。"""
    d = write(tmp_path, rows)
    prof = adf.profile_folder(d)
    values = adf.inspect_data_source(d / "t.csv", v2=True)
    shape = ROLES.validate_roles_shape(
        {"columns": [{"name": c, "role": r} for c, r in roles.items()], "picks": picks or {}}
    )
    return ROLES.interpret(prof.tables[0], values, shape, None), prof.tables[0]


# =========================================================================== 规则臂：不变
def test_the_rules_cannot_tell_a_measure_axis_from_an_ordered_one_so_they_stay_as_before(tmp_path):
    """同样两列数值：规则给折线类排第一，不论它们其实是什么——客观数据事实分不出来，所以不硬加启发式（宁可少做，不把时间序列改成散点）。"""
    res, *_ = cands(tmp_path, MEASURES)
    assert codes(res)[0] in ("line_points", "line_band")
    assert [d for d in res.dropped if d.problems[0].detail == "x_is_measure"] == []
    res2, *_ = cands(tmp_path, SERIES)
    assert codes(res2)[0] == "line_points"


def test_without_the_fact_the_response_is_byte_for_byte_the_old_one(tmp_path):
    a, *_ = cands(tmp_path, MEASURES)
    b, *_ = cands(tmp_path, MEASURES, picks={"x_measure": "no_such_column"})  # 指向别的列 = 没有
    assert a == b


# =========================================================================== 模型判横轴是测量值
def test_x_judged_a_measure_drops_every_trend_candidate_and_leads_with_the_scatter(tmp_path):
    res, *_ = cands(tmp_path, MEASURES, picks={"x_measure": "grain_size_um"})
    assert codes(res) == ["scatter_fit", "scatter"]
    assert all(c.recipe.family == "relationship" for c in res.items)
    # 去掉的趋势类有记录，复用 roles_invalid（不新增错误码、不新增界面文案）
    assert [
        (d.problems[0].code, d.problems[0].field, d.problems[0].detail) for d in res.dropped
    ] == [("roles_invalid", "x", "x_is_measure")]
    assert res.roles["x"] == "grain_size_um" and res.roles["y"] == ["yield_MPa"]
    for c in res.items:  # roles 与候选一致：横轴 / 纵轴就是配方里的那两列
        assert (c.recipe.x, list(c.recipe.y)) == (res.roles["x"], res.roles["y"])
    assert R.CANDIDATE_CODES and set(codes(res)) <= set(R.CANDIDATE_CODES)  # 理由码复用现有的


def test_the_fact_is_bound_to_the_column_a_user_who_changes_the_axis_gets_lines_again(tmp_path):
    """用户在右栏换了横轴：带着的 `x_measure` 指向的不是当前横轴，不生效——用户的选择优先。"""
    rows = [
        ["dose_uM", "grain_size_um", "yield_MPa"],
        *[
            [d, g, y]
            for d, (g, y) in zip(
                (1, 2, 3, 4, 5, 6, 7, 8),
                [
                    (50.2, 370),
                    (27.3, 288),
                    (35.2, 271),
                    (42.5, 330),
                    (51.2, 364),
                    (39.2, 343),
                    (31.3, 265),
                    (43.0, 278),
                ],
                strict=True,
            )
        ],
    ]
    kept, *_ = cands(
        tmp_path, rows, picks={"y": "yield_MPa", "x": "dose_uM", "x_measure": "grain_size_um"}
    )
    assert codes(kept)[0].startswith("line_")
    dropped, *_ = cands(
        tmp_path, rows, picks={"y": "yield_MPa", "x": "grain_size_um", "x_measure": "grain_size_um"}
    )
    assert codes(dropped)[0] == "scatter_fit"
    # 用户带了 x、没带这个键 = 与从前相同（折线照常）
    plain, *_ = cands(tmp_path, rows, picks={"y": "yield_MPa", "x": "grain_size_um"})
    assert codes(plain)[0].startswith("line_")


def test_a_date_or_missing_relationship_keeps_the_old_result_and_never_goes_empty(tmp_path):
    """x 不是数值（日期）时没有关系图可换：不拿掉趋势类。"""
    rows = [["when", "y"], *[[f"2026-01-{d:02d}", 1.5 * d] for d in range(1, 9)]]
    res, *_ = cands(tmp_path, rows, picks={"x_measure": "when"})
    assert res.items and codes(res)[0].startswith("line_")


def test_the_scatter_fallback_is_not_listed_twice_when_the_fit_line_is_not_allowed(tmp_path):
    """点太少（< 6）拟合线不合格：同一组退到 scatter，第二组的 scatter 不重复出，也不记 dropped。"""
    rows = [["a", "b"], [3.1, 5.2], [1.4, 2.2], [2.2, 4.1], [4.5, 3.0], [0.7, 0.9]]
    res, *_ = cands(tmp_path, rows, picks={"x_measure": "a"})
    assert codes(res) == ["scatter"]
    assert all(d.problems[0].code != "duplicate_candidate" for d in res.dropped)


def test_repeated_measures_question_is_not_asked_when_no_trend_will_be_drawn(tmp_path):
    rows = [["subject", "grain_size_um", "yield_MPa"]]
    for i, (g, y) in enumerate([(50.2, 370), (27.3, 288), (35.2, 271), (42.5, 330)] * 2):
        rows.append([f"S{i % 4}", g + i * 0.01, y])
    res, *_ = cands(tmp_path, rows, picks={"x_measure": "grain_size_um"})
    assert isinstance(res, R.Candidates)  # 不是 `pick_design` 的选择题


# =========================================================================== 判成有序量：现状
@pytest.mark.parametrize("role", ["time", "ordered_x"])
def test_x_judged_ordered_or_time_is_the_same_as_today(tmp_path, role):
    und, _entry = judged(tmp_path, SERIES, {"time_min": role, "od600": "measure"})
    assert und.x_measure is None and "x_measure" not in und.to_payload("t.csv")["answers"]
    res, *_ = cands(tmp_path, SERIES, picks=und.picks)
    base, *_ = cands(tmp_path, SERIES)
    assert res == base and codes(res)[0] == "line_points"


def test_no_role_for_the_axis_column_is_not_a_veto_either(tmp_path):
    und, _ = judged(tmp_path, MEASURES, {"yield_MPa": "measure"})
    assert und.x_measure is None


# =========================================================================== 事实怎么带过去
def test_the_model_s_role_for_the_chosen_axis_travels_as_answers_x_measure(tmp_path):
    und, _entry = judged(tmp_path, MEASURES, {"grain_size_um": "measure", "yield_MPa": "measure"})
    assert und.x_measure == "grain_size_um"
    payload = und.to_payload("t.csv")
    assert payload["answers"] == {"x_measure": "grain_size_um"}  # 不是对 x / y / group 下拉的改动
    assert not und.empty


def test_the_fact_names_the_axis_the_rules_settle_on_for_a_wide_table(tmp_path):
    """宽表（第一列是数值）规则自己落定「第一列当横轴、其余当纵轴」，没有要问的；事实指向这个横轴，不是下拉的改动。"""
    rows = [["a", "b", "c"], *[[i * 1.3 % 7, i * 2.1 % 5, i * 0.7 % 3] for i in range(1, 11)]]
    und, _ = judged(tmp_path, rows, {"a": "measure", "b": "measure", "c": "measure"})
    assert und.picks == {} and und.x_measure == "a"


def test_a_user_fixed_axis_wins_over_the_model(tmp_path):
    d = write(tmp_path, MEASURES)
    prof = adf.profile_folder(d)
    values = adf.inspect_data_source(d / "t.csv", v2=True)
    shape = ROLES.validate_roles_shape(
        {"columns": [{"name": "grain_size_um", "role": "measure"}], "picks": {}}
    )
    und = ROLES.interpret(prof.tables[0], values, shape, None, fixed={"x": "grain_size_um"})
    assert und.x_measure is None


def test_parse_answers_accepts_the_new_key_and_it_is_not_a_dropdown_touch():
    ans = P.parse_answers({"x_measure": "grain_size_um", "bogus": "x"})
    assert ans == {"x_measure": "grain_size_um"}
    conf, picks = P.confirmed_from(ans)
    assert conf == R.Confirmed() and picks == {"x_measure": "grain_size_um"}
    assert {
        k for k in picks if k in ("x", "y", "group")
    } == set()  # 前端的「用户动过下拉」只看这三个
    with pytest.raises(P.ProposeError):
        P.parse_answers({"x_measure": 3})
    # 只带这一个键的请求不会再起一个模型任务（建议已经在手里）
    assert P._user_took_over({"x_measure": "grain_size_um"})  # noqa: SLF001
    assert P._answers_digest({}) != P._answers_digest({"x_measure": "a"})  # noqa: SLF001


# =========================================================================== 经 propose（懒模式）
def test_propose_lazy_with_the_fact_returns_scatter_first_and_reports_the_drop(project):
    _m, client, figs, _state = project
    put(figs, "data/t.csv", MEASURES)
    plain = propose(client, render="lazy").get_json()
    assert plain["items"][0]["title_code"].startswith("line_")
    got = propose(client, render="lazy", answers={"x_measure": "grain_size_um"}).get_json()
    assert [i["title_code"] for i in got["items"]] == ["scatter_fit", "scatter"]
    assert got["items"][0]["recommended"] is True
    assert got["roles"]["x"] == "grain_size_um"
    assert {"reason_code": "roles_invalid"} in got["dropped"]
    # 判成时间 / 有序量时请求里没有这个键，响应与从前逐字相同
    again = propose(client, render="lazy").get_json()
    assert [i["title_code"] for i in again["items"]] == [i["title_code"] for i in plain["items"]]


def test_the_front_end_can_hand_back_extra_keys_in_the_old_way(project):
    """前端把 `understanding.answers` 里它认得的键并回；不认得的键它丢掉、也不会出错——这时响应与从前相同。"""
    _m, client, figs, _state = project
    put(figs, "data/t.csv", MEASURES)
    a = propose(client, render="lazy").get_json()
    b = propose(client, render="lazy", answers={"y": "yield_MPa", "x": "grain_size_um"}).get_json()
    assert [i["title_code"] for i in a["items"]] == [i["title_code"] for i in b["items"]]


def test_end_to_end_through_the_fake_model_the_understanding_carries_the_fact(tmp_path):
    """真协议层的假模型：模型说两列都是测量值 → `understanding.answers` 里有 `x_measure`；把它并回 `propose` 就是散点第一，没有趋势类。"""
    case = {
        "id": "XM-1",
        "tables": {"t.csv": "\n".join(",".join(str(c) for c in r) for r in MEASURES) + "\n"},
        "model_answer": {
            "columns": [
                {"name": "grain_size_um", "role": "measure"},
                {"name": "yield_MPa", "role": "measure"},
            ],
            "picks": {"y": "yield_MPa", "x": "grain_size_um", "group": "none"},
        },
    }
    folder = DR.materialize(case, tmp_path / "proj")
    ROLES.set_overrides(conn=D.fake_conn(), sync=True, post=DR.scripted(case))
    first = P.propose(tmp_path / "proj", project="p", folder=folder, render="lazy")
    assert first["items"][0]["title_code"].startswith("line_")  # 先出的是规则的结果
    und = first["understanding"]
    assert und["state"] == "ready" and und["answers"]["x_measure"] == "grain_size_um"
    # 前端把建议并回再出一次：请求带了模型的建议，不会再起模型任务
    again = P.propose(
        tmp_path / "proj", project="p", folder=folder, render="lazy", answers=und["answers"]
    )
    assert [i["title_code"] for i in again["items"]] == ["scatter_fit", "scatter"]
    assert "understanding" not in again


# =========================================================================== G-052：同一个 x 有重复观测时不逐点连线
def test_duplicate_x_never_gets_a_pointwise_line_and_the_summary_line_leads(tmp_path):
    res, *_ = cands(tmp_path, REPEATED)
    assert "line_points" not in codes(res)
    assert codes(res)[0] == "line_band"
    layers = {(lay.mark, lay.summary) for lay in res.items[0].recipe.layers}
    assert ("band", "mean_sd") in layers and (
        "points",
        "none",
    ) in layers  # 汇总线 + 误差带 + 原始点
    assert any(
        d.problems[0].code == "line_duplicate_x_unconfirmed" for d in res.dropped
    )  # 被去掉的逐点连线有记录


def test_one_value_per_x_still_gets_the_plain_line(tmp_path):
    res, *_ = cands(tmp_path, SERIES)
    assert codes(res)[0] == "line_points"
    assert not any(d.problems[0].code == "line_duplicate_x_unconfirmed" for d in res.dropped)


def test_duplicate_x_only_inside_a_colour_group_is_not_a_duplicate(tmp_path):
    """分组做颜色时，同一个 x 在不同组里各有一个值不算「同一个 x 有重复」。"""
    rows = [["day", "arm", "od600"]]
    for d in range(1, 7):
        rows += [[d, "ctrl", 0.1 * d], [d, "drug", 0.2 * d]]
    res, *_ = cands(tmp_path, rows)
    assert codes(res)[0] == "line_points"


def test_multi_y_keeps_every_selected_column_even_when_x_is_judged_a_measure(tmp_path):
    """多列纵轴只有趋势类画得全：这时 `x_measure` 不生效，选好的列一列都不丢（Codex 评审 #2159）。"""
    rows = [["a", "b", "c"]] + [[i * 1.5 + 0.2, 2 + 0.7 * i, 9 - 0.4 * i] for i in range(10)]
    base, *_ = cands(tmp_path, rows, picks={"x": "a", "y": ["b", "c"]})
    res, *_ = cands(tmp_path, rows, picks={"x": "a", "y": ["b", "c"], "x_measure": "a"})
    assert res.roles["y"] == ["b", "c"] == base.roles["y"]
    assert res.items and all(tuple(c.recipe.y) == ("b", "c") for c in res.items)
    assert codes(res) == codes(base)
