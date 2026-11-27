"""数值列「像条件 / 顺序」还是「像测量」的取值结构线索，与默认横轴 / 纵轴（ADR 0178「修订（2026-11-27）：条件列与测量列」）。

判据的主语：

* 线索量的是**一列数值按行序的取值结构**（`adf.design_kind`）：不看列名、不看数据集。三种「像条件 / 顺序」：块内按行不减、块与块之间重置（`block`）、整列按行不减（`monotone`）、
  少量取值各自重复出现（`levels`）；其余是「像测量」。
* 默认角色量的是 `default_candidates` 返回的 `roles`：默认横轴只从像条件的列里挑，默认纵轴只从像测量的列里挑；用户 / 模型点名（`picks`）一律不动，`options` 不变。
* 每条线索各有一条反证：去掉对应的取值判断，对应的用例变红。
"""

from __future__ import annotations

import random
from pathlib import Path

from tavotto.engine import ai_data_figure as adf, ai_data_recipe as recipe_mod


def _csv(tmp_path: Path, header: list[str], rows: list[list], name: str = "t.csv") -> Path:
    p = tmp_path / name
    p.write_text("\n".join([",".join(header), *(",".join(map(str, r)) for r in rows)]) + "\n")
    return p


def _roles(tmp_path: Path, p: Path, picks: dict | None = None):
    prof = adf.profile_folder(tmp_path)
    vals = adf.inspect_data_source(p, None, v2=True)
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


def _rand(n: int, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [round(rng.uniform(0, 100), 2) for _ in range(n)]


# --------------------------------------------------------------------------- 线索本身


def test_block_resets_inside_each_subject():
    v = [t for _ in range(6) for t in (0, 2, 4, 6, 8, 10)]
    assert adf.design_kind(v) == "block"


def test_block_allows_replicates_at_each_time_point_and_a_few_short_blocks():
    v = [t for _ in range(2) for t in (0, 0, 0, 7, 7, 7, 14, 14, 14, 21, 21, 21)]
    assert adf.design_kind(v) == "block"
    ragged = [t for _ in range(10) for t in (0, 2, 4, 6, 8, 10)] + [0, 2]  # 一个个体只测了两次
    assert adf.design_kind(ragged) == "block"


def test_monotone_column_and_two_level_sorted_column():
    assert adf.design_kind(list(range(1, 21))) == "monotone"
    assert (
        adf.design_kind([1] * 10 + [2] * 10) == "levels"
    )  # 只有两个取值：不算整列单调，按少量水平
    assert adf.design_kind([5.0] * 20) is None  # 常数


def test_levels_need_every_level_to_repeat():
    dose = [1.0, 0.5, 2.0] * 8  # 乱序出现：不是块、不是整列单调
    assert adf.design_kind(dose) == "levels"
    assert (
        adf.design_kind([1.0, 0.5, 2.0, 3.0, 4.0, 5.0, 6.0] + dose[:17]) is None
    )  # 有取值只出现一次


def test_growth_that_starts_somewhere_different_for_each_subject_is_not_a_block():
    """体重一类的测量值：每个个体各自的曲线递增、个体之间重置，但起点各不相同（时间每个个体都从同一个值起）。"""
    growth = [40 + s + 5 * k for s in range(8) for k in range(6)]
    assert len(set(growth)) < len(growth)  # 取值有重叠，其余条件都满足
    assert adf.design_kind(growth) is None
    schedule = [5 * k for _ in range(8) for k in range(6)]
    assert adf.design_kind(schedule) == "block"


def test_random_measurements_are_not_design_like():
    assert adf.design_kind(_rand(60)) is None
    assert (
        adf.design_kind([round(x) for x in _rand(60, 3)]) is None
    )  # 取整的随机数：取值多于 n/4 且乱序


def test_summary_records_design_for_clean_numeric_columns_only(tmp_path):
    rows = [[t, ["a", "b"][i // 6], (i * 7) % 11] for i, t in enumerate([0, 1, 2, 3, 4, 5] * 2)]
    p = _csv(tmp_path, ["time", "g", "v"], rows)
    s = adf.inspect_data_source(p, None, v2=True)
    assert s.design == {"time": "block"}
    # 横轴缺一格：那一行本来就画不了，不让整列失去线索；缺得多（> 20%）才不判
    rows2 = [[("" if i == 3 else t), "a", i] for i, t in enumerate([0, 1, 2, 3, 4, 5] * 2)]
    s2 = adf.inspect_data_source(_csv(tmp_path, ["time", "g", "v"], rows2, "u.csv"), None, v2=True)
    assert s2.design.get("time") == "block"
    rows3 = [[("" if i % 3 == 0 else t), "a", i] for i, t in enumerate([0, 1, 2, 3, 4, 5] * 2)]
    s3 = adf.inspect_data_source(_csv(tmp_path, ["time", "g", "v"], rows3, "w.csv"), None, v2=True)
    assert "time" not in s3.design


def test_large_tables_have_no_design_clue(tmp_path, monkeypatch):
    p = _csv(tmp_path, ["time", "g", "v"], [[t, "a", i % 5] for i, t in enumerate(range(1, 41))])
    monkeypatch.setattr(adf, "SMALL_BYTES", 0)
    s = adf.read_text_table(p).summary
    assert s.large is not None and s.design == {}


# --------------------------------------------------------------------------- 默认角色


def _dose_table(n_per: int = 10):
    """ToothGrowth 形状：分组 supp、测量 len（连续）、条件 dose（三个剂量各重复）；len 在 dose 之前。"""
    rng = random.Random(7)
    rows = []
    for supp in ("VC", "OJ"):
        for dose in (0.5, 1.0, 2.0):
            for _ in range(n_per):
                rows.append([round(rng.uniform(4, 35), 1), supp, dose])
    return ["len", "supp", "dose"], rows


def test_the_condition_column_is_x_and_the_measurement_is_y(tmp_path):
    h, rows = _dose_table()
    res = _roles(tmp_path, _csv(tmp_path, h, rows))
    assert res.roles["x"] == "dose" and res.roles["y"] == ["len"] and res.roles["group"] == "supp"
    # 下拉不变：两个数值列都列得出来
    assert res.roles["options"]["y"] == ["len", "dose"]


def test_users_choices_are_never_overridden(tmp_path):
    h, rows = _dose_table()
    p = _csv(tmp_path, h, rows)
    res = _roles(tmp_path, p, {"y": "dose", "x": "len"})
    assert res.roles["x"] == "len" and res.roles["y"] == ["dose"]
    res = _roles(tmp_path, p, {"x": "none"})
    assert res.roles["x"] is None and res.roles["y"] == ["len"]


def _repeated_measures(n_subj: int = 6):
    """个体 × 时间（块内递增、块间重置）× 两个测量；测量列在时间之前、个体之后。"""
    rng = random.Random(3)
    rows = []
    for s in range(1, n_subj + 1):
        for t in (0, 1, 2, 4, 8, 12):
            rows.append([s, round(rng.uniform(1, 9), 2), t, round(rng.uniform(1, 90), 2)])
    return ["unit", "dose_mg", "time", "conc"], rows


def test_block_beats_monotone_beats_levels_and_the_rest_is_leftmost(tmp_path):
    h, rows = _repeated_measures()
    p = _csv(tmp_path, h, rows)
    s = adf.inspect_data_source(p, None, v2=True)
    assert s.design["time"] == "block" and s.design["unit"] == "monotone"
    res = _roles(tmp_path, p)
    # `unit`（整列单调）在 `time`（块内递增）之前，但块优先；纵轴取像测量的最后一列
    assert res.roles["x"] == "time" and res.roles["y"] == ["conc"]


def test_a_lone_measurement_like_candidate_is_not_x_when_there_is_a_group(tmp_path):
    rng = random.Random(5)
    rows = [[g, round(rng.uniform(40, 80), 1), round(rng.uniform(40, 80), 1)] for g in "abc" * 8]
    res = _roles(tmp_path, _csv(tmp_path, ["grp", "pre", "post"], rows))
    assert res.roles["x"] is None and res.roles["group"] == "grp" and res.roles["y"] == ["post"]
    assert {c.recipe.family for c in res.items} <= {"comparison", "distribution"}


def test_without_a_group_two_measurements_keep_the_scatter(tmp_path):
    rng = random.Random(6)
    rows = [[round(rng.uniform(1, 9), 2), round(rng.uniform(1, 9), 2)] for _ in range(20)]
    res = _roles(tmp_path, _csv(tmp_path, ["a", "b"], rows))
    assert res.roles["x"] == "a" and res.roles["y"] == ["b"]


def test_integer_coded_few_level_columns_are_not_the_default_y_nor_x(tmp_path):
    rng = random.Random(8)
    rows = [[round(rng.uniform(1, 9), 2), 1 + i % 2] for i in range(20)]
    res = _roles(tmp_path, _csv(tmp_path, ["extra", "group"], rows))
    assert res.roles["y"] == ["extra"] and res.roles["x"] is None
    assert "group" in res.roles["options"]["y"]


def test_when_every_numeric_column_looks_like_a_condition_the_old_behaviour_stays(tmp_path):
    rows = [[i + 1, [0.5, 1.0, 2.0][i % 3]] for i in range(24)]
    res = _roles(tmp_path, _csv(tmp_path, ["step", "dose"], rows))
    assert res.items  # 不报错、不空手
    assert res.roles["y"] == ["dose"]  # 没有像测量的列：回到「最后一个数值列」
