"""失败时就给提示 + 试运行记录表（ADR 0197 第 1 层）。

* 提示表只点名真实存在、本会话放行的工具；每句点名的工具都声明过（缺一个，这句就不说）。
* 形状判据只有一份（`hints`），「先别报缺陷」的闸门与提示共用。
* 应用试运行后附记录表：试过什么、错误有没有变；同一个失败连着回来两次就明说别再重复；没真跑的不算一次。
* 审计只记形状码、提示 id 与工具名。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import json
import re
import sys

import pytest

from tavotto.engine.fixtools import REGISTRY, hints, run_tool
from tavotto.engine.fixtools.audit import AuditLog
from tavotto.engine.fixtools.runreport import project_run_report
from test_fixtools import box, call, make_ctx, put  # noqa: F401


def _failing(code: str, traceback: str = "") -> dict:
    return {
        "phase": "failed",
        "outcome": {"code": code},
        "result": {"error": {"code": code, "message": "", "traceback": traceback}},
    }


def test_every_tool_a_hint_names_exists():
    assert hints.tools_named() <= set(REGISTRY)


def test_every_tool_named_in_a_sentence_is_declared_for_it():
    """句子里出现的工具名都在它声明的工具里：否则「工具不可用就不说这句」会漏掉，模型被指向调不到的工具。"""
    names = set(REGISTRY)
    for hint in hints.HINTS.values():
        for tools, text in hint.lines:
            mentioned = {n for n in names if re.search(rf"\b{re.escape(n)}\b", text)}
            assert mentioned <= set(tools), (hint.id, mentioned - set(tools), text)


@pytest.mark.parametrize(
    ("code", "trace", "shape"),
    [
        ("script_error", "KeyError: 'LAB_DIR'\n  root = os.environ['LAB_DIR']", "env_var_missing"),
        (
            "script_error",
            "ImportError: attempted relative import with no known parent package",
            "relative_import",
        ),
        ("script_error", "EOFError: EOF when reading a line\n  n = int(input('n? '))", "input_eof"),
        ("script_needs_arguments", "", "script_needs_arguments"),
        ("script_error", "ValueError: boom", "script_error"),
        ("", "", ""),
    ],
)
def test_failure_shapes(code, trace, shape):
    assert hints.shape_of(code, trace) == shape


def test_a_sentence_whose_tool_is_unavailable_is_dropped():
    full = hints.hint_for("script_needs_arguments", lambda _n: True)
    without_snippet = hints.hint_for("script_needs_arguments", lambda n: n != "run_snippet")
    assert full and without_snippet and "run_snippet" in full[1]
    assert "run_snippet" not in without_snippet[1] and "set_run_arguments" in without_snippet[1]
    assert hints.hint_for("script_needs_input", lambda _n: False) is None  # 一句都不剩：没有提示
    assert hints.hint_for("no_such_shape", lambda _n: True) is None


def test_the_report_carries_the_hint_first(box):
    report = _failing("script_error", "KeyError: 'X'\n  os.environ['X']")
    # 只放行看与试：主动作调不到，这个形状就没有提示
    narrow = make_ctx(box)
    narrow.allowed_tiers = frozenset({"observe", "run"})
    assert "hint" not in json.loads(project_run_report(report, narrow).content)
    ctx = make_ctx(box)  # 修复会话放行全部档位
    r = project_run_report(report, ctx)
    doc = json.loads(r.content)
    assert next(iter(doc)) == "hint"
    assert doc["hint"]["for"] == "env_var_missing" and "write_launcher" in doc["hint"]["next"]
    assert "hint" not in json.loads(project_run_report({"phase": "completed"}, ctx).content)


def test_another_entry_point_is_pointed_to_not_run():
    """维护者 11-19 裁决（ADR 0188）：真入口在另一份脚本时，引导用户去打开那份，不用启动脚本代跑。"""
    _, text = hints.hint_for("script_is_library", lambda _n: True)
    assert "open that script instead" in text and "do not run it" in text


# ---------------------------------------------------------------- 试运行记录表
def _app_ctx(box, reports, before):
    """应用试运行的替身：每次 try_run 依次回 `reports` 里的一份；会话当前报告是 `before`（之后跟着变）。"""
    script = put(box.proj / "fig.py", "print(1)\n")
    state = {"current": before}
    queue = list(reports)

    def runner(_python, _timeout, _cancel, _approve):
        out = queue.pop(0)
        state["current"] = out.get("report") or state["current"]
        return out

    ctx = make_ctx(
        box,
        environments={"e": sys.executable},
        script_path=script,
        report=lambda: state["current"],
        application_runner=runner,
    )
    ctx.ask = lambda _card: {"answer": "yes"}
    return ctx


def test_the_ledger_records_changes_and_how_the_failure_moved(box):
    ctx = _app_ctx(
        box,
        [
            {"code": "application_run_failed", "report": _failing("script_needs_arguments")},
            {"code": "", "report": {"phase": "completed"}, "verification": None},
        ],
        before=_failing("script_needs_arguments"),
    )
    first = call(ctx, "try_run", {"env": "e"})
    assert "attempts so far:\n1. after no change: same failure" in first.content
    ctx.changes_since_try = [
        "set_run_arguments"
    ]  # 真改动由 run_tool 记；这里直接给定以免依赖应用回调
    second = call(ctx, "try_run", {"env": "e"})
    assert "2. after set_run_arguments: script_needs_arguments -> ok" in second.content
    assert [a["n"] for a in ctx.attempts] == [1, 2] and ctx.changes_since_try == []


def test_the_same_failure_twice_says_stop_repeating(box):
    fail = {"code": "application_run_failed", "report": _failing("missing_input")}
    ctx = _app_ctx(box, [fail, fail], before=_failing("missing_input"))
    call(ctx, "try_run", {"env": "e"})
    r = call(ctx, "try_run", {"env": "e"})
    assert "same failure came back twice" in r.content


def test_a_run_that_never_happened_is_not_an_attempt(box):
    ctx = _app_ctx(
        box, [{"code": "needs_approval", "report": {}}], before=_failing("missing_input")
    )
    r = call(ctx, "try_run", {"env": "e"})
    assert "attempts so far" not in r.content and ctx.attempts == []


@pytest.mark.parametrize(
    "outcome",
    [
        {"code": "preparation_plan_stale", "report": {}},
        {
            "code": "application_run_failed",
            "report": {
                "phase": "failed",
                "result": {"error": {"code": "conda_activation_failed", "executed": False}},
            },
        },
    ],
)
def test_a_run_refused_or_never_started_keeps_the_pending_changes(box, outcome):
    """准备阶段拒了（批准期间脚本变了）或明确没执行（Conda 起不来）：不算一次，攒下的改动留给下一次真跑（Codex #2079）。"""
    ctx = _app_ctx(box, [outcome], before=_failing("missing_input"))
    ctx.changes_since_try = ["set_run_arguments"]
    r = call(ctx, "try_run", {"env": "e"})
    assert "attempts so far" not in r.content and ctx.attempts == []
    assert ctx.changes_since_try == ["set_run_arguments"]


def test_real_changes_are_collected_for_the_next_attempt(box):
    from tavotto.engine import execpolicy

    ctx = make_ctx(box)
    call(ctx, "read_run_report", {})
    assert ctx.changes_since_try == []  # 看不算
    execpolicy.set_mode(str(box.proj), "safe_only")
    call(ctx, "switch_execution", {"mode": "safe"})  # 已经是这样：不算
    assert ctx.changes_since_try == []


def test_the_audit_keeps_only_codes(box, tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    ctx = _app_ctx(
        box,
        [
            {
                "code": "application_run_failed",
                "report": _failing("script_error", "KeyError: 'SECRET_X'\n os.environ['SECRET_X']"),
            }
        ],
        before=_failing("script_needs_arguments"),
    )
    run_tool(REGISTRY["try_run"], ctx, {"env": "e"}, audit)
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text("utf-8").splitlines()]
    att = rows[-1]["attempt"]
    assert att == {
        "n": 1,
        "before": "script_needs_arguments",
        "after": "env_var_missing",
        "hint": "env",
        "changes": [],
    }
    assert "SECRET_X" not in json.dumps(rows)


def test_a_change_that_resets_the_report_does_not_blank_the_failure_before(box):
    """设好参数后报告被重置成「还没跑」：第一次试运行之前的形状退到会话开始时的（评测 11-23 发现记成了空）。"""
    ctx = _app_ctx(
        box,
        [{"code": "", "report": {"phase": "completed"}, "verification": None}],
        before={"phase": "awaiting_run"},  # 改动之后的样子：读不出形状
    )
    ctx.first_shape = "script_needs_arguments"
    ctx.changes_since_try = ["set_run_arguments"]
    r = call(ctx, "try_run", {"env": "e"})
    assert "1. after set_run_arguments: script_needs_arguments -> ok" in r.content
    assert ctx.attempts[0]["before"] == "script_needs_arguments"


def test_the_first_message_records_the_starting_shape(box):
    from tavotto.engine import fixsession

    ctx = make_ctx(box, report=lambda: _failing("script_needs_arguments"))
    fixsession._first_message(ctx, "fig.py")
    assert ctx.first_shape == "script_needs_arguments"
