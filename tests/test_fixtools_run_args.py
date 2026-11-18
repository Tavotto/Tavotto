"""AI 修复填运行参数（`set_run_arguments`，与界面「运行参数」框同一份运行配置 T03）。

评测里内置 AI 推断得出脚本要的参数，却只能让用户自己去填、甚至劝用户改脚本加默认值。现在它能填：验证运行换到带参数的准备会话，
修好后界面把参数填进「运行参数」再重跑。原脚本不动。"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用
from __future__ import annotations

import pytest

from tavotto import app as app_module
from tavotto.engine import pool as engine_pool, prepsession
from tavotto.engine.fixtools import FixContext
from test_fix_application_verification import _application_ctx
from test_fixtools import box, call, make_ctx  # noqa: F401
from test_preparation_api import client, fake_pool  # noqa: F401
from test_preparation_session import sessions  # noqa: F401


def _ctx(box, seen):
    return make_ctx(box, on_run_arguments_changed=lambda tokens: seen.append(tokens))


def test_sets_the_arguments_and_records_them(box):
    seen = []
    ctx = _ctx(box, seen)
    r = call(ctx, "set_run_arguments", {"argv": ["--data", "a b.csv", "-n", "3"]})
    assert not r.is_error, r.content
    assert seen == [("--data", "a b.csv", "-n", "3")]  # 原样 token，不拆空格
    assert ctx.run_arguments == ("--data", "a b.csv", "-n", "3") and ctx.run_arguments_changed
    assert (
        call(ctx, "set_run_arguments", {"argv": ["--data", "a b.csv", "-n", "3"]}).summary
        == "no change"
    )


@pytest.mark.parametrize("bad", ["--data x.csv", None, [1], ["a\x00b"]])
def test_rejects_malformed_arguments_without_applying(box, bad):
    seen = []
    ctx = _ctx(box, seen)
    assert call(ctx, "set_run_arguments", {"argv": bad}).code == "bad_arguments"
    assert seen == [] and ctx.run_arguments is None


def test_without_an_application_it_says_so(box):
    ctx = make_ctx(box)
    assert call(ctx, "set_run_arguments", {"argv": ["x"]}).code == "not_available"


def test_rollback_restores_the_previous_arguments(box):
    seen = []
    ctx = _ctx(box, seen)
    call(ctx, "set_run_arguments", {"argv": ["one"]})
    r = call(ctx, "set_run_arguments", {"argv": ["two"]})
    snap = r.content.split("snapshot: ", 1)[1].split()[0]
    assert not call(ctx, "rollback", {"snapshot": snap}).is_error
    assert ctx.run_arguments == ("one",) and seen[-1] == ("one",)
    first = make_ctx(box, on_run_arguments_changed=lambda t: seen.append(t))
    r = call(first, "set_run_arguments", {"argv": ["x"]})
    snap = r.content.split("snapshot: ", 1)[1].split()[0]
    call(first, "rollback", {"snapshot": snap})
    assert first.run_arguments is None and seen[-1] == ()  # 原来没有参数：回滚 = 清掉


def test_the_application_run_uses_the_new_arguments(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """真准备会话登记表：设了参数之后 try_run 的 build 拿到这组参数，修复会话的报告也换到新会话。"""
    _unused, project, session = _application_ctx(client, tmp_path)
    box_ = {"sess": session}  # 与 `api_ai_fix` 同一种接法：报告与验证都跟着 box 里那条会话
    ctx = FixContext.for_fix_session(
        "repair-args",
        project.path,
        home=tmp_path,
        environments={"e": session.plan.interpreter},
        script_path=project.path / "fig.py",
        report=lambda: app_module._session_report(project, box_["sess"]),
        **app_module._fix_application_verification(project, box_, desktop=False),
    )
    ctx.ask = lambda _card: {"answer": "yes"}
    runs = []
    inner = engine_pool.build_owned

    def build_owned(script, root, entry, **kw):
        run = kw.get("run")
        runs.append(tuple(run.argv) if run is not None else ())
        return inner(script, root, entry, **kw)

    monkeypatch.setattr(engine_pool, "build_owned", build_owned)
    # 共用替身的 peek 不认 `run=`（真 pool.peek 认）：带参数的会话要它
    monkeypatch.setattr(engine_pool, "peek", lambda *_a, **_k: fake_pool["peek"])
    assert ctx.run_arguments is None
    r = call(ctx, "set_run_arguments", {"argv": ["--data", "measurements.csv"]})
    assert not r.is_error, r.content
    result = call(ctx, "try_run", {"env": "e"})
    assert not result.is_error, result.content
    assert runs[-1] == ("--data", "measurements.csv")
    raw = ctx.report()
    assert (
        raw["session_id"] == box_["sess"].session_id != session.session_id
    )  # 报告来自带参数的那条会话
    assert box_["sess"].target["run"].argv == ("--data", "measurements.csv")
    assert not call(
        ctx, "finish", {"outcome": "fixed", "summary": "Set the data argument."}
    ).is_error


def test_fixed_state_carries_the_arguments_for_the_ui(box):
    from tavotto.engine import fixsession

    ctx = FixContext.for_fix_session("s", box.proj, home=box.home)
    ctx.run_arguments, ctx.run_arguments_changed = ("--data", "m.csv"), True
    s = fixsession.FixSession.__new__(fixsession.FixSession)
    fields = {f: None for f in getattr(fixsession.FixSession, "__dataclass_fields__", {})}
    for k, v in fields.items():
        setattr(s, k, v)
    s.id, s.state, s.script, s.prep_session_id = "fx", fixsession.STATE_FINISHED, "fig.py", "p"
    s.card, s.summary, s.reason, s.repair_token, s.steps, s.seq = None, "", "", None, 0, 0
    s.ctx = ctx
    s.outcome = fixsession.OUTCOME_FIXED
    assert fixsession.FixSessions.public(s)["run_argv"] == ["--data", "m.csv"]
    s.outcome = fixsession.OUTCOME_NOT_FIXED
    assert "run_argv" not in fixsession.FixSessions.public(s)
    s.outcome, ctx.run_arguments_changed = fixsession.OUTCOME_FIXED, False
    assert "run_argv" not in fixsession.FixSessions.public(s)


def test_application_callback_switches_the_verified_session(client, tmp_path, fake_pool, sessions):
    _ctx_unused, project, session = _application_ctx(client, tmp_path)
    box_ = {"sess": session}
    hooks = app_module._fix_application_verification(project, box_, desktop=False)
    hooks["on_run_arguments_changed"](("a",))
    assert box_["sess"] is not session and box_["sess"].target["run"].argv == ("a",)
    hooks["on_run_arguments_changed"](())
    assert box_["sess"].target.get("run") is None
    assert prepsession.SESSIONS.session(box_["sess"].session_id, project.id) is box_["sess"]


def test_after_the_repair_open_and_run_reuses_the_arguments(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    """修好之后再点「打开并运行」（`reuse_last_success`）沿用 AI 验证通过的那组参数——不靠前端回填：
    AI 的验证运行走的是正常的成功收口，最近一次成功记下的正是它的运行配置。"""
    _unused, project, session = _application_ctx(client, tmp_path)
    box_ = {"sess": session}
    ctx = FixContext.for_fix_session(
        "repair-args-reuse",
        project.path,
        home=tmp_path,
        environments={"e": session.plan.interpreter},
        script_path=project.path / "fig.py",
        report=lambda: app_module._session_report(project, box_["sess"]),
        **app_module._fix_application_verification(project, box_, desktop=False),
    )
    ctx.ask = lambda _card: {"answer": "yes"}
    monkeypatch.setattr(engine_pool, "peek", lambda *_a, **_k: fake_pool["peek"])
    assert not call(ctx, "set_run_arguments", {"argv": ["--data", "m.csv"]}).is_error
    assert not call(ctx, "try_run", {"env": "e"}).is_error
    from tavotto.engine import runconfig

    # 「打开并运行」沿用的依据：最近一次成功记下的就是 AI 验证用的那份运行配置（依据核对另由 #982 的用例看护；
    # 这里的替身 pool 回执不带真实脚本摘要，核对那一步在替身上判不了）
    rec = runconfig.launch_record(project.path, "fig.py")
    assert rec is not None and rec["run_config"] == box_["sess"].target["run"].config_id
    assert tuple(runconfig.get(project.path, rec["run_config"], script="fig.py").argv) == (
        "--data",
        "m.csv",
    )
