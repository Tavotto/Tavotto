"""数据出图 v2 的「行号列」默认不当横轴 / 纵轴（ADR 0178「修订（2026-11-27）：行号列与大表退路」）。

判据的主语：

* 行号列的判定量的是**一列的名字与它的全部取值**（`adf.row_index_columns` / 大表扫描的 `_SeqTrack`）：名字在闭集里、**并且**每一行都是整数、第一行 0 或 1、逐行恰好 +1。
  两条缺一不可——单凭取值，`day` 这样的 1..n 是真的横轴；单凭名字，`index` 可能是别的量。
* 「默认不当轴」量的是 `default_candidates` 返回的 `roles`（`x` / `y`）与 `options`：命中的列不是默认的 x / y，但仍在 `options` 里、用户点名它照样能当横轴。
* 小表整份读与大表流式读对同一个文件给出**同一个** `row_index`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import ai_data_figure as adf, ai_data_recipe as recipe_mod


def _csv(tmp_path: Path, header: list[str], rows: list[list], name: str = "t.csv") -> Path:
    lines = [",".join(header), *(",".join(str(c) for c in r) for r in rows)]
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _summary(p: Path) -> adf.DataSummary:
    return adf.inspect_data_source(p, None, v2=True)


def _roles(tmp_path: Path, p: Path, picks: dict | None = None):
    """规则默认的列角色：有问题就按「不确定」答下去（与 lazy 的 `propose` 同一口径）。返回 `Candidates`。"""
    prof = adf.profile_folder(tmp_path)
    vals = _summary(p)
    picks = dict(picks or {})
    for _ in range(6):
        res = recipe_mod.default_candidates(
            prof, table=p.name, confirmed=recipe_mod.Confirmed(), values=vals, picks=picks
        )
        if isinstance(res, recipe_mod.NeedsChoice):
            picks.update(res.unsure.answer)
            continue
        return res
    raise AssertionError("问题没完没了")


def _rows(n: int, start: int = 1):
    # 一个量随一个独立变量变化：`dose` 非单调，`resp` 是测量值
    return [
        [start + i, [3, 1, 4, 1, 5, 9, 2, 6][i % 8], round(0.5 * i + (i % 3), 2)] for i in range(n)
    ]


# --------------------------------------------------------------------------- 判据本身


@pytest.mark.parametrize(
    "name",
    [
        "rownames",
        "Row Names",
        "row_names",
        "RowID",
        "index",
        "Index",
        "idx",
        "No.",
        "no",
        "#",
        "Unnamed: 0",
        "Unnamed: 0.1",
        "序号",
        "行号",
    ],
)
def test_row_index_name_closed_set_accepts(name):
    assert adf._row_index_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "day",
        "time",
        "n",
        "count",
        "dose",
        "Unnamed: 1",
        "unnamed",
        "week",
        "trial",
        "sample_mean",
        "id",
        "",
    ],
)
def test_row_index_name_closed_set_rejects(name):
    assert not adf._row_index_name(name)


@pytest.mark.parametrize(
    ("start", "expect"),
    [(1, True), (0, True), (2, False), (-1, False)],
)
def test_row_index_needs_zero_or_one_start(tmp_path, start, expect):
    p = _csv(tmp_path, ["rownames", "dose", "resp"], _rows(6, start))
    assert ("rownames" in _summary(p).row_index) is expect


def test_values_alone_are_not_enough(tmp_path):
    """列名不在闭集里的 1..n（第几天）不是行号：这是「同时要求名字与取值」的取值一侧反例。"""
    p = _csv(tmp_path, ["day", "dose", "resp"], _rows(6))
    assert _summary(p).row_index == []


@pytest.mark.parametrize(
    "ids",
    [
        [1, 2, 4, 5, 6, 7],  # 断号
        [1, 1, 2, 3, 4, 5],  # 重复
        [6, 5, 4, 3, 2, 1],  # 递减
        [1, 3, 5, 7, 9, 11],  # 步长 2
        [1, 2, 3, 4, 5, "x"],  # 夹着文字
        [1, 2, 3, "", 5, 6],  # 缺格
        [1, 2, 3, 4.5, 5.5, 6.5],  # 不是整数
        [1, 2],  # 太短
    ],
)
def test_row_index_rejects_anything_but_a_perfect_counter(tmp_path, ids):
    rows = [[i, 1, 1] for i in ids]
    p = _csv(tmp_path, ["rownames", "dose", "resp"], rows)
    assert _summary(p).row_index == []


def test_an_individual_id_column_stays_id_like_and_is_not_a_row_index(tmp_path):
    """`id` 是个体编号线索（`id_like`）：已有的机制管它，行号判据不重复收；`rownames` 不会进 `id_like`（不触发「重复测量吗」）。"""
    p = _csv(tmp_path, ["id", "rownames", "resp"], [[i, i, i % 3] for i in range(1, 7)])
    s = _summary(p)
    assert s.id_like == ["id"]
    assert s.row_index == ["rownames"]
    assert s.repeats is False


def test_small_and_large_readers_agree_on_row_index(tmp_path, monkeypatch):
    rows = [[i, 2013, (i * 7) % 5] for i in range(1, 40)]
    p = _csv(tmp_path, ["rownames", "year", "resp"], rows)
    small = adf.read_text_table(p).summary
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    large = adf.read_text_table(p).summary
    assert small is not None and small.large is None and small.row_index == ["rownames"]
    assert large is not None and large.large is not None
    assert large.row_index == small.row_index
    # 大表一侧同样不认断号 / 取值不全的列
    q = _csv(
        tmp_path,
        ["rownames", "year", "resp"],
        [[i + (i > 20), 1, 1] for i in range(1, 40)],
        "u.csv",
    )
    assert adf.read_text_table(q).summary.row_index == []


# --------------------------------------------------------------------------- 默认角色


def test_row_index_is_not_the_default_x_but_stays_selectable(tmp_path):
    p = _csv(tmp_path, ["rownames", "dose", "resp"], _rows(8))
    res = _roles(tmp_path, p)
    assert res.roles["x"] != "rownames"
    assert "rownames" not in res.roles["y"]
    assert res.roles["x"] == "dose" and res.roles["y"] == ["resp"]
    # 用户仍可手动选它（下拉里还列着）
    assert "rownames" in res.roles["options"]["x"]
    assert "rownames" in res.roles["options"]["y"]
    chosen = _roles(tmp_path, p, {"x": "rownames", "y": "resp"})
    assert chosen.roles["x"] == "rownames"
    assert all(c.recipe.x == "rownames" for c in chosen.items if c.recipe.family != "distribution")


def test_wide_table_with_row_numbers_no_longer_plots_against_the_row_number(tmp_path):
    """Rdatasets 的 `rownames, time, value`：以前横轴是行号、两列都当纵轴；现在是 time ~ value 里按规则落定的一对。"""
    p = _csv(
        tmp_path,
        ["rownames", "time", "value"],
        [[i + 1, [3, 1, 4, 1, 5, 9][i % 6], i * 2] for i in range(12)],
    )
    res = _roles(tmp_path, p)
    assert res.items
    assert res.roles["x"] != "rownames"
    assert all(c.recipe.x != "rownames" for c in res.items)


def test_without_any_other_axis_candidate_it_goes_to_the_no_axis_route(tmp_path):
    """行号之外只有一个数值列和一个分组列：没有横轴，走比较 / 分布，不报错。"""
    rows = [[i + 1, "ctl" if i % 2 else "trt", 10 + (i * 7) % 5] for i in range(12)]
    p = _csv(tmp_path, ["rownames", "group", "weight"], rows)
    res = _roles(tmp_path, p)
    assert res.items and res.reasons == ()
    assert res.roles["x"] is None and res.roles["group"] == "group"
    assert {c.recipe.family for c in res.items} <= {"comparison", "distribution"}


def test_a_table_whose_only_number_column_is_the_counter_still_draws(tmp_path):
    """去掉行号列后一个数值列都不剩：不去（别让表画不了）。"""
    p = _csv(tmp_path, ["rownames", "label"], [[i + 1, "a" if i % 2 else "b"] for i in range(8)])
    res = _roles(tmp_path, p)
    assert res.items and res.roles["y"] == ["rownames"] and res.roles["x"] is None


def test_a_real_day_counter_is_untouched(tmp_path):
    """名字不在闭集里的 1..n 仍是正常的横轴：规则行为逐字不变。"""
    p = _csv(tmp_path, ["day", "resp"], [[i + 1, (i * 7) % 11] for i in range(10)])
    res = _roles(tmp_path, p)
    assert res.roles["x"] == "day" and res.roles["y"] == ["resp"]
