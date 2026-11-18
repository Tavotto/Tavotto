"""AI 修复的两道护栏（ADR 0194 路线；兼容性评测 `scripts/eval/fix_compat` 的发现）。

* 用户这一侧就能补上的失败（缺参数 / 缺输入 / 数据挪走 / 缺包 / `os.environ` KeyError / 相对导入），在**一个改动都没试过**
  之前不许起草产品缺陷——评测里内置 AI 把两份这类问题当缺陷发给了维护者。
* 预算过半时循环追加「别再巡视，动手」的提醒（`FixToolset.budget_hint`）：两次「用完预算」都是巡视到底。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import pytest

from tavotto.engine import fixsession
from test_fixasks import box, call, make_ctx  # noqa: F401

ISSUE = {
    "title": "t",
    "reproduction": "r",
    "expected": "e",
    "actual": "a",
    "evidence": "ev",
    "attempts": "at",
    "product_reason": "p",
}


def _report(code="", traceback=""):
    return lambda: {
        "outcome": {"code": code},
        "result": {"error": {"code": code, "message": "", "traceback": traceback}},
    }


@pytest.mark.parametrize(
    ("code", "traceback"),
    [
        ("script_needs_arguments", ""),
        ("script_needs_input", ""),
        ("missing_input", ""),
        ("script_error", "KeyError: 'LAB_DATA_DIR'\n  root = os.environ['LAB_DATA_DIR']"),
        ("script_error", "ImportError: attempted relative import with no known parent package"),
    ],
)
def test_a_user_side_failure_is_not_reported_as_a_defect_before_any_fix(box, code, traceback):
    ctx, cards = make_ctx(box, report=_report(code, traceback))
    r = call(ctx, "prepare_diagnostics", {"summary": "s", "issue": ISSUE})
    assert r.code == "not_a_product_defect" and cards == []  # 没弹「发送」卡


def test_after_a_real_attempt_the_defect_can_be_reported(box):
    ctx, cards = make_ctx(box, {"answer": "no"}, report=_report("script_needs_arguments"))
    ctx.changes_attempted = 1
    r = call(ctx, "prepare_diagnostics", {"summary": "s", "issue": ISSUE})
    assert r.code != "not_a_product_defect" and cards and cards[0]["kind"] == "send"


def test_a_product_side_failure_can_be_reported_right_away(box):
    ctx, cards = make_ctx(
        box, {"answer": "no"}, report=_report("no_figure_captured", "exit 0, nothing captured")
    )
    r = call(ctx, "prepare_diagnostics", {"summary": "s", "issue": ISSUE})
    assert r.code != "not_a_product_defect" and cards


def test_a_plain_summary_without_a_defect_draft_is_never_blocked(box):
    ctx, cards = make_ctx(box, {"answer": "no"}, report=_report("script_needs_arguments"))
    r = call(ctx, "prepare_diagnostics", {"summary": "what I found"})
    assert r.code != "not_a_product_defect" and cards


def test_only_real_change_attempts_are_counted(box):
    ctx, _ = make_ctx(box, report=_report("script_needs_arguments"))
    call(ctx, "read_run_report", {})
    call(ctx, "list_dir", {"path": "."})
    assert ctx.changes_attempted == 0  # 看 / 问不算
    call(ctx, "switch_execution", {"mode": "nonsense"})
    assert ctx.changes_attempted == 0  # 参数都不对的调用不算
    call(ctx, "switch_execution", {"mode": "safe"})
    assert ctx.changes_attempted == 1


def test_the_repair_loop_gets_a_budget_reminder():
    assert "try_run" in fixsession.FixToolset.budget_hint
    assert "not_fixed" in fixsession.FixToolset.budget_hint
