"""「读懂数据」并进规则时的一条保护（ADR 0178 修订 2026-11-28）：规则选作横轴的列被模型判成「重复 / 批次编号」并建议没有横轴，
而去掉之后**既没有横轴也没有分组**——不采纳，保持规则的结果（图不该只剩一张直方图）。

判据的主语是「并进模型建议之后的默认横轴 / 纵轴 / 分组」。对照都在：还有分组列可比时照常采纳（题库 RC-01）、判成行号时照常采纳。
来源是真模型在公开数据集上的实测（`npk` 的区组号、按批次记的读数）；这里只用假模型回放，不发任何请求。
"""

from __future__ import annotations

import pytest

from tests.test_ai_data_roles import BY_ID, final, run_case

BLOCKS = "block,yield\n" + "".join(
    f"{b},{50 + b * 2 + r * 1.5:.1f}\n" for b in range(1, 7) for r in range(4)
)


def case_of(name: str, text: str) -> dict:
    return {"id": name, "title": name, "tables": {f"{name}.csv": text}, "hint": None}


def answer(block_role: str) -> dict:
    return {
        "columns": [{"name": "block", "role": block_role}, {"name": "yield", "role": "measure"}],
        "picks": {"y": ["yield"], "x": "none", "group": "none"},
    }


def test_a_replicate_unit_x_is_kept_when_dropping_it_would_leave_only_a_distribution(tmp_path):
    case = case_of("blocks", BLOCKS)
    rules = run_case(case, tmp_path, conn=None).rules
    assert rules["x"] == "block" and rules["y"] == ["yield"] and rules["group"] is None
    run, _ = final(case, tmp_path, answer("replicate_unit"))
    assert run.state == "ready"
    assert run.model == rules, "区组号被判成重复编号、又没有别的分组：横轴保留"


@pytest.mark.parametrize("role", ["row_id", "other"])
def test_an_x_judged_a_row_number_or_a_note_is_still_dropped(tmp_path, role):
    """对照：模型说它是行号 / 备注（不是可比的单位）——照常采纳「没有横轴」，这才是「流水号被当成了轴」。"""
    case = case_of("serial", BLOCKS)
    run, _ = final(case, tmp_path, answer(role))
    assert run.state == "ready" and run.model["x"] is None and run.model["y"] == ["yield"]


def test_with_a_group_left_to_compare_the_replicate_unit_x_is_dropped_as_before(tmp_path):
    """对照（题库 RC-01）：有处理组可比时，批次号不当横轴——保护不该拦这一种。"""
    case = BY_ID["RC-01"]
    run, _ = final(case, tmp_path, case["model_answer"])
    assert run.model == {"x": None, "y": ["expression"], "group": "condition"}
