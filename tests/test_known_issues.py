"""已知问题表（ADR 0221 §二）：表的 schema、匹配器、挂进修复工具结果与反馈说明。

判据的主语：表文件本身的结构；匹配器只看结构化字段；工具结果里的 `known_issue` 与反馈说明里的 `known_issue_id`。
一切在 tmp_path 的假主目录里；平台与版本用 monkeypatch，不碰真实环境。
"""

from __future__ import annotations

import json
from importlib import resources

import pytest
from packaging.version import Version

import tavotto
from tavotto.engine import envdiag, knownissues
from tavotto.engine.fixtools import REGISTRY, runreport
from tests.test_fixasks import make_ctx as ask_ctx
from tests.test_fixtools import call, make_ctx
from tests.test_fixtools_change import (  # noqa: F401
    FakeEnv,
    box as box,  # noqa: F401
    change_ctx,
    fake_python,
)

SELFTEST = {"stage": "error_reply", "worker_code": "safe_file_operation_blocked", "exit_code": 1}
CODE = "dependency_worker_selftest_failed"


def _entries() -> list[dict]:
    raw = resources.files("tavotto").joinpath("resources", "known_issues.json").read_text("utf-8")
    return json.loads(raw)["issues"]


# =========================================================================== 表的 schema
def test_every_entry_is_well_formed_and_ids_are_unique():
    entries = _entries()
    assert entries
    assert len({e["id"] for e in entries}) == len(entries)
    for e in entries:
        assert set(e["match"]) <= knownissues.MATCH_KEYS
        assert isinstance(e["match"].get("error_code"), str) and e["match"]["error_code"]
        for key in ("summary_zh", "summary_en"):
            assert isinstance(e[key], str) and e[key].strip()
        work = e["workaround"]
        assert all(t in REGISTRY for t in work["tools"])
        assert work["user_steps_zh"].strip() and work["user_steps_en"].strip()


def test_fixed_in_is_never_earlier_than_the_upper_bound_of_the_affected_versions():
    for e in _entries():
        top = (e["match"].get("versions") or {}).get("max")
        if top is not None and e.get("fixed_in") is not None:
            assert Version(e["fixed_in"]) > Version(top), e["id"]


def test_first_entry_names_the_real_button_text():
    e = next(x for x in _entries() if x["id"] == "selftest-guard-blocks-own-output")
    assert "换用我的 Python" in e["workaround"]["user_steps_zh"]
    assert "Use my own Python" in e["workaround"]["user_steps_en"]


# =========================================================================== 匹配器
def test_matches_on_structured_fields():
    hits = knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="0.21.3")
    assert [h["id"] for h in hits] == ["selftest-guard-blocks-own-output"]
    assert knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="0.20.0")


def test_no_hit_outside_the_version_range_or_on_another_platform():
    assert not knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="1.0.0")
    assert not knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="0.21.4")
    assert not knownissues.match(CODE, selftest=SELFTEST, os_name="macos", version="0.21.3")
    assert not knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version=None)
    assert not knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="dev?")


def test_no_hit_on_other_codes_stages_or_without_the_selftest_shape():
    kw = {"os_name": "windows", "version": "0.21.3"}
    assert not knownissues.match("dependency_install_failed", selftest=SELFTEST, **kw)
    assert not knownissues.match(CODE, selftest={**SELFTEST, "stage": "timeout"}, **kw)
    assert not knownissues.match(CODE, selftest={**SELFTEST, "worker_code": None}, **kw)
    assert not knownissues.match(CODE, **kw)
    assert not knownissues.match("", selftest=SELFTEST, **kw)


def test_fact_follows_the_ui_language():
    e = knownissues.match(CODE, selftest=SELFTEST, os_name="windows", version="0.21.3")[0]
    zh, en = knownissues.fact(e, "zh"), knownissues.fact(e, "en")
    assert zh["id"] == en["id"] and zh["fixed_in"] == "1.0.0"
    assert "已知" in zh["summary"] and "Known issue" in en["summary"]
    assert zh["workaround"]["tools"] == ["change_python"]
    assert knownissues.language_of("zh-CN") == "zh" and knownissues.language_of("en-US") == "en"
    assert knownissues.language_of(None) == "en"


# =========================================================================== 工具结果
@pytest.fixture
def affected(monkeypatch):
    """装成「0.21.3 的 Windows、最近一代自检被守卫拦」。"""
    monkeypatch.setattr(tavotto, "__version__", "0.21.3")
    monkeypatch.setattr(knownissues, "os_name", lambda: "windows")
    monkeypatch.setattr(envdiag, "recent", lambda _p: [{"error_code": CODE, "selftest": SELFTEST}])


def _report(code: str) -> dict:
    return {"phase": "failed", "outcome": {"code": code}, "result": {"error": {"code": code}}}


def test_read_run_report_carries_the_known_issue_in_the_session_language(box, affected):
    ctx = make_ctx(box, report=lambda: _report(CODE), locale="zh-CN")
    r = call(ctx, "read_run_report", {})
    body = json.loads(r.content)
    assert body["known_issue"]["id"] == "selftest-guard-blocks-own-output"
    assert "已知" in body["known_issue"]["summary"]
    assert list(body)[0] == "known_issue"
    en = call(make_ctx(box, report=lambda: _report(CODE)), "read_run_report", {})
    assert "Known issue" in json.loads(en.content)["known_issue"]["summary"]


def test_read_run_report_has_no_known_issue_for_other_failures_or_fixed_versions(
    box, affected, monkeypatch
):
    other = call(make_ctx(box, report=lambda: _report("script_error")), "read_run_report", {})
    assert "known_issue" not in json.loads(other.content)
    monkeypatch.setattr(tavotto, "__version__", "1.0.0")
    fixed = call(make_ctx(box, report=lambda: _report(CODE)), "read_run_report", {})
    assert "known_issue" not in json.loads(fixed.content)


def test_failed_change_tools_carry_the_known_issue(box, affected):
    def fail(*_a):
        return {"ok": False, "code": CODE, "done": []}

    env = FakeEnv()
    env.installer = fail
    ctx = change_ctx(box, env, python_changer=fail, locale="en-US")
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert r.is_error and r.code == CODE
    assert "known_issue:" in r.content and "selftest-guard-blocks-own-output" in r.content
    c = call(ctx, "change_python", {})
    assert c.is_error and "selftest-guard-blocks-own-output" in c.content


def test_failed_change_tool_with_an_unrelated_code_has_no_known_issue(box, affected):
    env = FakeEnv()
    env.installer = lambda *_a: {"ok": False, "code": "dependency_not_found", "done": []}
    ctx = change_ctx(box, env)
    r = call(ctx, "install_packages", {"env": "e", "packages": ["seaborn"]})
    assert "known_issue" not in r.content


def test_explicit_selftest_shape_from_the_executor_is_used(box, monkeypatch):
    monkeypatch.setattr(tavotto, "__version__", "0.21.3")
    monkeypatch.setattr(knownissues, "os_name", lambda: "windows")
    monkeypatch.setattr(envdiag, "recent", lambda _p: [])
    ctx = make_ctx(box)
    assert runreport.known_issue_text(ctx, CODE) == ""
    assert "selftest-guard" in runreport.known_issue_text(ctx, CODE, SELFTEST)


# =========================================================================== 反馈说明
def test_prepare_diagnostics_adds_the_known_issue_id_to_the_note(box, affected):
    ctx, cards = ask_ctx(box, {"answer": "no"}, report=lambda: _report(CODE))
    assert not call(ctx, "prepare_diagnostics", {"summary": "自检被拦"}).is_error
    assert cards[0]["summary"].startswith("known_issue_id: selftest-guard-blocks-own-output\n")


def test_prepare_diagnostics_adds_nothing_for_unknown_failures(box, affected):
    ctx, cards = ask_ctx(box, {"answer": "no"}, report=lambda: _report("script_error"))
    call(ctx, "prepare_diagnostics", {"summary": "s"})
    assert "known_issue_id" not in cards[0]["summary"]


def test_system_prompt_tells_the_model_what_to_do_with_a_known_issue():
    from tavotto.engine import fixsession

    assert "known_issue" in fixsession.SYSTEM_PROMPT and "fixed_in" in fixsession.SYSTEM_PROMPT
