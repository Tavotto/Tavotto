"""Repair evidence must describe the same application run the user will reopen.

The report and dependency-cache tests fail on the pre-repair implementation. Other
cases exercise approval and the real preparation action protocol, without a model.
"""

# ruff: noqa: F811 — reuse isolated fixtures
from __future__ import annotations

import json

import pytest

from tavotto import app as app_module
from tavotto.engine import depplan, pool as engine_pool, prepsession
from tavotto.engine.fixtools import FixContext, executors
from test_fixtools import box, call, make_ctx, put  # noqa: F401
from test_preparation_api import client, fake_pool  # noqa: F401
from test_preparation_session import _create_ready, sessions  # noqa: F401


def test_report_preserves_actual_nested_worker_error(box):
    ctx = make_ctx(
        box,
        report=lambda: {
            "phase": "blocked",
            "outcome": {"kind": "failed", "code": "script_error"},
            "result": {
                "error": {
                    "code": "script_error",
                    "message": "cannot open resource",
                    "traceback": "OSError: Gidole-Regular.ttf cannot open resource",
                }
            },
            "plan": {"launch_context": {"cwd_mode": "sandbox"}},
        },
    )
    body = json.loads(call(ctx, "read_run_report", {}).content)
    assert "Gidole-Regular.ttf" in json.dumps(body)
    assert body["launch_context"]["cwd_mode"] == "sandbox"


@pytest.mark.parametrize("operation", ["install", "uninstall"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_package_mutation_retires_cached_facts_even_after_partial_failure(box, operation, succeeds):
    python = str(box.proj / ".venv/bin/python")
    key = f"{depplan._key(python)}|direct"
    old = object()
    depplan._facts_cache[key] = old
    depplan._index_cache[key] = old
    result = {"ok": succeeds, "done": ["seaborn"], "code": "" if succeeds else "interrupted"}
    ctx = make_ctx(box, installer=lambda *_: result, uninstaller=lambda *_: result)
    try:
        fn = executors.run_installer if operation == "install" else executors.run_uninstaller
        assert fn(ctx, python, ["seaborn"]) == result
        assert key not in depplan._facts_cache
        assert key not in depplan._index_cache
    finally:
        depplan.reset_cache(python)


def _report(*, finished=False, captured=False, revision=1, action=True):
    return {
        "session_id": "psess-one",
        "config_revision": revision,
        "phase": ("completed" if captured else "partial") if finished else "ready_to_run",
        "outcome": {
            "kind": (prepsession.OUTCOME_SUCCEEDED if captured else prepsession.OUTCOME_NO_FIGURE)
            if finished
            else prepsession.OUTCOME_PENDING
        },
        "facts": {"execution_finished": finished, "figure_captured": captured},
        "provider": {"plan_id": "plan-one", "attempt_id": "attempt-one" if finished else None},
        "plan": {
            "environment": {"python": "/venv/bin/python"},
            "launch_context": {"profile": "safe", "cwd_mode": "sandbox"},
        },
        "actions": [{"id": "run-one", "kind": "run", "impact": {"executes_user_script": True}}]
        if action
        else [],
    }


def test_authoritative_run_needs_current_action_approval_and_does_not_prepare():
    from tavotto.engine.fixverification import run_preparation

    claimed, stopped, cards = [], [], []

    def approve(summary, details):
        cards.append((summary, details))
        return False

    opts = dict(
        python="/venv/bin/python",
        selected_python=lambda: "/venv/bin/python",
        timeout=1,
        cancel=None,
        approve=approve,
        check=lambda: _report(),
        claim=lambda *a: claimed.append(a),
        read=lambda *_: pytest.fail("unapproved run"),
        stop=stopped.append,
    )
    assert run_preparation(**opts)["code"] == "needs_approval"
    assert not claimed and not stopped
    assert "sandbox" in cards[0][1]
    waiting = _report(action=False)
    waiting["actions"] = [
        {"id": "prepare-one", "kind": "prepare_dependencies", "impact_digest": "secret-grant"}
    ]
    opts["check"] = lambda: waiting
    assert run_preparation(**opts)["code"] == "application_not_runnable"
    assert len(cards) == 1 and not claimed


def test_candidate_environment_cannot_be_used_as_application_evidence():
    from tavotto.engine.fixverification import run_preparation

    result = run_preparation(
        python="/other/bin/python",
        selected_python=lambda: "/venv/bin/python",
        timeout=1,
        cancel=None,
        approve=lambda *_: pytest.fail("wrong environment offered"),
        check=lambda: _report(),
        claim=lambda *_: pytest.fail("changed selection"),
        read=None,
        stop=None,
    )
    assert result["code"] == "environment_not_selected"


def test_application_verification_distinguishes_execution_from_figure_capture():
    from tavotto.engine.fixverification import run_preparation

    claims = []
    result = run_preparation(
        python="/venv/bin/python",
        selected_python=lambda: "/venv/bin/python",
        timeout=1,
        cancel=None,
        approve=lambda *_: True,
        check=lambda: _report(),
        claim=lambda *a: claims.append(a) or True,
        read=lambda _: _report(finished=True),
        stop=lambda _: pytest.fail("cancelled success"),
    )
    assert result["verification"]["attempt_id"] == "attempt-one"
    assert claims == [("run-one", 1)]
    assert result["report"]["facts"] == {"execution_finished": True, "figure_captured": False}


@pytest.mark.parametrize(
    "attempt",
    [
        {"status": "error", "error": {"code": "missing_dependency"}, "missing_installable": True},
        {"status": "ready", "captured_count": 1, "finalize": {"registered": False}},
        {
            "status": "ready",
            "captured_count": 2,
            "finalize": {
                "registered": True,
                "failed_figures": [{"stem": "lost", "code": "render_failed"}],
            },
        },
    ],
    ids=["runtime_missing_dependency", "registration_failed", "partial_capture"],
)
def test_finished_failed_attempt_cannot_verify_or_finish_fixed(box, attempt):
    from tavotto.engine.fixverification import evidence, run_preparation

    # Consume the real preparation projection: all three attempts have finished,
    # but execution-finished alone does not establish a successful repair.
    derived = prepsession.derive(
        {
            "target_kind": "script",
            "has_script": True,
            "attempt": {**attempt, "finalized": True},
        }
    )
    report = _report(finished=True, captured=derived["figure_captured"])
    report.update(phase=derived["phase"], outcome=derived["outcome"])
    assert derived["execution_finished"] is True
    result = run_preparation(
        python="/venv/bin/python",
        selected_python=lambda: "/venv/bin/python",
        timeout=1,
        cancel=None,
        approve=lambda *_: True,
        check=lambda: _report(),
        claim=lambda *_: True,
        read=lambda _: report,
        stop=lambda _: pytest.fail("a terminal failure must not wait for a timeout"),
    )
    assert result["code"] == "application_run_failed"
    assert "verification" not in result and evidence(report) is None

    ctx = make_ctx(box, application_runner=lambda *_: None, application_check=lambda: report)
    # Even a previous proof with the same identity fields cannot authorize a
    # failed current outcome. finish must revalidate the authoritative report.
    ctx.verification = evidence(_report(finished=True, captured=True))
    assert ctx.verification is not None
    assert (
        call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).code
        == "application_verification_required"
    )


def test_missing_outcome_cannot_be_inferred_from_execution_finished():
    from tavotto.engine.fixverification import evidence

    report = _report(finished=True, captured=True)
    report.pop("outcome")
    assert evidence(report) is None


def test_timeout_cancels_only_the_claimed_action():
    from tavotto.engine.fixverification import run_preparation

    running = _report()
    running["phase"] = "running"
    stopped = []
    result = run_preparation(
        python="/venv/bin/python",
        selected_python=lambda: "/venv/bin/python",
        timeout=0,
        cancel=None,
        approve=lambda *_: True,
        check=lambda: _report(),
        claim=lambda *_: True,
        read=lambda _: running,
        stop=stopped.append,
    )
    assert result["code"] == "timeout" and stopped == ["run-one"]


def test_diagnostic_copy_cannot_finish_fixed_and_app_evidence_expires_on_change(box):
    from tavotto.engine.fixverification import evidence

    report = _report(finished=True)
    ctx = make_ctx(
        box,
        environments={"e": "/venv/bin/python"},
        script_path=put(box.proj / "fig.py", "pass"),
        application_runner=lambda *_: pytest.fail("diagnostic should not run in app"),
        application_check=lambda: report,
        runner=lambda *_: {"status": "ok", "returncode": 0, "figures": 1},
    )
    diagnostic = call(ctx, "try_run", {"env": "e", "mode": "diagnostic"})
    assert not diagnostic.is_error and "not application verification" in diagnostic.content
    finish = {"outcome": "fixed", "summary": "Repaired"}
    assert call(ctx, "finish", finish).code == "application_verification_required"
    ctx.verification = evidence(report)
    assert not call(ctx, "finish", finish).is_error
    report["config_revision"] += 1
    assert call(ctx, "finish", finish).code == "application_verification_required"


def test_workdir_change_requires_card_and_rollback_restores_permission(box):
    from tavotto.engine import config, workdir

    root = str(box.proj)
    workdir.set_mode(root, "sandbox")
    before = config.project_settings(root)["workdir"]
    cards, notifications = [], []

    def ask(card):
        cards.append(card)
        return {"answer": "no"}

    ctx = make_ctx(box, ask=ask, on_workdir_changed=lambda: notifications.append("changed"))
    assert call(ctx, "switch_workdir", {"mode": "project"}).code == "needs_approval"
    assert config.project_settings(root)["workdir"] == before and not notifications
    assert cards[0]["tool"] == "switch_workdir" and "writes" in cards[0]["summary"]
    ctx.ask = lambda card: {"answer": "yes"}
    result = call(ctx, "switch_workdir", {"mode": "project"})
    assert not result.is_error and workdir.mode_for(root) == "project"
    snap = ctx.call_meta["snapshot"]
    assert call(ctx, "rollback", {"snapshot": snap}).is_error is False
    assert config.project_settings(root)["workdir"] == before
    assert notifications == ["changed", "changed"]


# The adapter below uses the actual preparation registry and application callbacks;
# only worker execution is replaced by the shared protocol fixture.


def _application_ctx(client, tmp_path):
    root, pj, report = _create_ready(client, tmp_path)
    project = app_module.PROJECTS[pj]
    session = prepsession.SESSIONS.session(report["session_id"], pj)
    ctx = FixContext.for_fix_session(
        "repair-one",
        root,
        home=tmp_path,
        environments={"e": session.plan.interpreter},
        script_path=root / "fig.py",
        report=lambda: app_module._session_report(project, session),
        **app_module._fix_application_verification(project, session, desktop=False),
    )
    return ctx, project, session


def test_application_callback_uses_real_session_failure_not_scratch_success(
    client, tmp_path, fake_pool, sessions
):
    ctx, _project, session = _application_ctx(client, tmp_path)
    cards = []
    ctx.ask = lambda card: cards.append(card) or {"answer": "yes"}
    ctx.runner = lambda *_: pytest.fail("application verification fell back to a copy")
    fake_pool["error"] = engine_pool.WorkerError("cannot open resource", code="script_error")
    result = call(ctx, "try_run", {"env": "e"})
    assert result.code == "application_run_failed", result.content
    assert "cannot open resource" in result.content
    assert fake_pool["build_calls"] == 1 and len(session.attempts) == 1
    assert cards[0]["tool"] == "try_run"
    assert "sandbox" in cards[0]["details"]
    assert call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).is_error


def test_application_callback_success_is_bound_to_revision(client, tmp_path, fake_pool, sessions):
    from tavotto.engine import workdir

    ctx, _project, session = _application_ctx(client, tmp_path)
    ctx.ask = lambda _: {"answer": "yes"}
    result = call(ctx, "try_run", {"env": "e"})
    assert not result.is_error, result.content
    assert session.attempts and ctx.verification is not None
    assert call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).is_error is False
    workdir.set_mode(str(ctx.project_root), "project_root")
    assert (
        call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).code
        == "application_verification_required"
    )


def test_application_callback_rejects_installable_runtime_dependency(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    ctx, _project, session = _application_ctx(client, tmp_path)
    cards = []
    ctx.ask = lambda card: cards.append(card) or {"answer": "yes"}
    fake_pool["error"] = engine_pool.WorkerError(
        "No module named colorcet", code="missing_dependency", module="colorcet"
    )
    # Only worker execution and dependency resolution are fixtures; action
    # ownership, provider finalization and preparation reports are real.
    monkeypatch.setattr(
        prepsession.SESSIONS,
        "_observe_missing",
        lambda *_: {
            "module": "colorcet",
            "installable": True,
            "rounds_remaining": 1,
            "requirement": {"name": "colorcet"},
            "impact": {},
            "impact_digest": "missing-colorcet",
        },
    )
    result = call(ctx, "try_run", {"env": "e"})
    current = ctx.report()
    assert current["facts"]["execution_finished"] is True
    assert current["outcome"]["kind"] == prepsession.OUTCOME_NEEDS_DEPENDENCIES
    assert current["outcome"]["reason"] == "rerun_required"
    assert result.code == "application_run_failed", result.content
    assert ctx.verification is None
    assert (
        call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).code
        == "application_verification_required"
    )
    assert fake_pool["build_calls"] == len(session.attempts) == 1
    assert [card["tool"] for card in cards] == ["try_run"]


def test_settings_changed_while_run_card_open_do_not_run_new_plan(
    client, tmp_path, fake_pool, sessions
):
    from tavotto.engine import workdir

    ctx, _project, _session = _application_ctx(client, tmp_path)

    def approve_stale(_card):
        workdir.set_mode(str(ctx.project_root), "project_root")
        return {"answer": "yes"}

    ctx.ask = approve_stale
    result = call(ctx, "try_run", {"env": "e"})
    assert result.is_error and result.code == "preparation_plan_stale", result.content
    assert fake_pool["build_calls"] == 0


def test_cancel_during_application_run_retires_its_owned_attempt(
    client, tmp_path, fake_pool, sessions
):
    import threading

    ctx, _project, session = _application_ctx(client, tmp_path)
    ctx.ask = lambda _: {"answer": "yes"}
    ctx.cancel = threading.Event()
    fake_pool["gate"].clear()
    fake_pool["announce"] = True
    result = []
    thread = threading.Thread(target=lambda: result.append(call(ctx, "try_run", {"env": "e"})))
    thread.start()
    try:
        assert fake_pool["build_entered"].wait(5)
        ctx.cancel.set()
        thread.join(5)
        assert not thread.is_alive()
        assert result[0].code == "cancelled"
        assert session.cancel_requested
        assert len(fake_pool["force_cancel"]) == 1
    finally:
        fake_pool["gate"].set()
        thread.join(5)


@pytest.mark.parametrize(
    "name", ["run_command", "link_data_file", "select_environment", "change_python", "rollback"]
)
def test_even_partial_failed_mutations_revoke_old_application_proof(box, monkeypatch, name):
    from dataclasses import replace

    from tavotto.engine.fixtools import REGISTRY, run_tool
    from tavotto.engine.fixtools.base import err
    from tavotto.engine.fixverification import evidence

    report = _report(finished=True)
    ctx = make_ctx(box, application_runner=lambda *_: None, application_check=lambda: report)
    ctx.verification = evidence(report)
    key = "/venv/bin/python|direct"
    stale = object()
    depplan._facts_cache[key] = stale

    def partial_change(_ctx, _args):
        put(box.proj / "partial.txt", "operation changed state before failing")
        return err("operation_failed", "Partial change")

    tool = replace(REGISTRY[name], handler=partial_change)
    assert run_tool(tool, ctx, {}).is_error
    assert (box.proj / "partial.txt").exists()
    assert ctx.verification is None and key not in depplan._facts_cache
    assert (
        call(ctx, "finish", {"outcome": "fixed", "summary": "Done"}).code
        == "application_verification_required"
    )


@pytest.mark.parametrize("condition", ["font", "output_directory"])
def test_real_worker_project_cwd_repair_requires_approval_and_preserves_source(
    client, tmp_path, monkeypatch, condition
):
    """Native succeeds; sandbox fails; the approved existing cwd policy fixes app execution.

    Uses real Pillow C font I/O / Pillow output I/O, real pool and preparation
    service. No model or package installation, no forced worker override.
    """
    import shutil
    import subprocess

    from tavotto.engine import projectenv, workdir
    from test_preparation_api import _open

    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    python = engine_pool.find_worker_python()
    if not python:
        pytest.skip("No plotting Python is available")
    probe = subprocess.run(
        [
            python,
            "-c",
            "import pathlib, matplotlib, PIL; print(pathlib.Path(matplotlib.get_data_path()) / 'fonts/ttf/DejaVuSans.ttf')",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    root = tmp_path / condition
    root.mkdir()
    (root / "data/dst").mkdir(parents=True)
    shutil.copyfile(probe.stdout.strip().splitlines()[-1], root / "fixture.ttf")
    source = (
        "from PIL import Image, ImageFont\n"
        + ("font = ImageFont.truetype('fixture.ttf', 18)\n" if condition == "font" else "")
        + "Image.new('RGB', (8, 8), 'red').save('data/dst/result.png')\n"
    )
    script = put(root / "fig.py", source)
    assert subprocess.run([python, str(script)], cwd=root, capture_output=True).returncode == 0
    assert projectenv.remember(root, python, automatic=False, trigger="user_selected")
    workdir.set_mode(str(root), "sandbox")
    prepsession.SESSIONS.reset_for_tests()
    try:
        pj = _open(client, root)
        report = client.post(
            "/api/engine/preparation-sessions", json={"script": "fig.py", "execution": "safe"}
        ).get_json()
        session = prepsession.SESSIONS.session(report["session_id"], pj)
        project = app_module.PROJECTS[pj]
        ctx = FixContext.for_fix_session(
            "real-worker",
            root,
            home=tmp_path,
            environments={"e": python},
            script_path=script,
            **app_module._fix_application_verification(project, session, desktop=False),
        )
        cards = []
        ctx.ask = lambda card: cards.append(card) or {"answer": "yes"}
        before = call(ctx, "try_run", {"env": "e"})
        assert before.code == "application_run_failed", before.content
        assert (
            "cannot open resource" if condition == "font" else "data/dst/result.png"
        ) in before.content
        assert ctx.verification is None
        ctx.ask = lambda card: {"answer": "no"}
        assert call(ctx, "switch_workdir", {"mode": "project"}).code == "needs_approval"
        assert workdir.mode_for(str(root)) == "sandbox"
        ctx.ask = lambda card: cards.append(card) or {"answer": "yes"}
        assert not call(ctx, "switch_workdir", {"mode": "project"}).is_error
        after = call(ctx, "try_run", {"env": "e"})
        assert not after.is_error, after.content
        current = ctx.application_check()
        assert current["facts"] == {"execution_finished": True, "figure_captured": False}
        assert current["outcome"]["kind"] == "execution_finished_no_figure"
        assert script.read_text() == source
        assert [c["tool"] for c in cards] == ["try_run", "switch_workdir", "try_run"]
        assert not call(
            ctx,
            "finish",
            {"outcome": "fixed", "summary": "The environment runs now; raster conversion remains."},
        ).is_error
    finally:
        engine_pool.shutdown_all(str(root))
        prepsession.SESSIONS.reset_for_tests()


def test_expired_or_reopened_project_cannot_verify_old_session(
    client, tmp_path, fake_pool, sessions
):
    ctx, project, _session = _application_ctx(client, tmp_path)
    ctx.ask = lambda _: {"answer": "yes"}
    app_module.PROJECTS.pop(project.id)
    result = call(ctx, "try_run", {"env": "e"})
    assert result.code == "fix_project_closed" and fake_pool["build_calls"] == 0


def test_adapter_refuses_to_cancel_another_action_after_its_run_is_replaced(
    client, tmp_path, fake_pool, sessions, monkeypatch
):
    from tavotto.engine import fixverification

    ctx, _project, session = _application_ctx(client, tmp_path)
    seen = []

    def interleave(**callbacks):
        report = callbacks["check"]()
        first = next(a for a in report["actions"] if a["kind"] == "run")
        assert callbacks["claim"](first["id"], report["config_revision"])
        import time

        end = time.monotonic() + 5
        while time.monotonic() < end:
            report = callbacks["read"](first["id"])
            if report["facts"]["execution_finished"]:
                break
            time.sleep(0.01)
        assert report["facts"]["execution_finished"]
        fake_pool["gate"].clear()
        second = next(a for a in report["actions"] if a["kind"] == "run")
        assert callbacks["claim"](second["id"], report["config_revision"])
        seen.append(session.attempts[-1].attempt_id)
        callbacks["stop"](first["id"])
        pytest.fail("old action cancelled the new run")

    monkeypatch.setattr(fixverification, "run_preparation", interleave)
    try:
        result = call(ctx, "try_run", {"env": "e"})
        assert result.code == "fix_run_superseded", result.content
        assert seen and not session.cancel_requested
        assert not fake_pool["force_cancel"]
    finally:
        fake_pool["gate"].set()


def test_long_history_and_traceback_cannot_hide_current_error_and_launch_context(box):
    ctx = make_ctx(
        box,
        report=lambda: {
            "phase": "action_required",
            "facts": {"execution_finished": False, "figure_captured": False},
            "attempt_history": [{"details": "old attempt " * 4000} for _ in range(30)],
            "result": {
                "error": {
                    "code": "script_error",
                    "message": "cannot open resource",
                    "traceback": "x" * 20000 + "\nOSError: fixture.ttf cannot open resource",
                }
            },
            "plan": {"launch_context": {"profile": "safe", "cwd_mode": "sandbox"}},
        },
    )
    rendered = call(ctx, "read_run_report", {}).content
    assert "fixture.ttf cannot open resource" in rendered
    assert '"cwd_mode": "sandbox"' in rendered
    assert rendered.index('"error"') < rendered.index('"attempt_history"')
    assert rendered.index('"launch_context"') < rendered.index('"attempt_history"')
    assert len(rendered) <= 8000 + len("\n[truncated]")
