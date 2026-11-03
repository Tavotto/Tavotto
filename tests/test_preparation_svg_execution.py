"""Execution fallback keeps current-run SVG invalidation observable without SSE."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

import test_execution_fallback_e2e as fallback
import test_export_svg as svg_cases
from tavotto import app as m
from tavotto.engine import execpolicy, executionfact, runcodes, svgexecution
from test_export_svg import document, spec
from test_svg_execution_lifecycle import exported, save


@pytest.fixture
def project(tmp_path, monkeypatch):
    yield from svg_cases.project.__wrapped__(tmp_path, monkeypatch)


def test_native_preexecution_failure_preserves_safe_fact_in_polled_report(project, monkeypatch):
    client, root = project
    source = svg_cases.run_script(client)
    reference = save(client, source)["figure_snapshots"]["panel"]
    (root / "blocked.txt").write_text("1", encoding="utf-8")
    (root / "plot.py").write_text(
        "from pathlib import Path\ndef main():\n"
        '    p = Path(__file__).parent / "blocked.txt"\n'
        '    p.write_text("2")\n'
        "    p.read_text()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(m, "_desktop_initiated", lambda: True)

    def fail_before_user_code(*args, **kwargs):
        raise runcodes.RunError(runcodes.DESKTOP_RUN_LAUNCH_INVALID)

    monkeypatch.setattr(m.engine_desktopproc, "start", fail_before_user_code)
    report = client.post("/api/engine/preparation-sessions", json={"script": "plot.py"}).get_json()
    (run,) = [action for action in report["actions"] if action["kind"] == "run"]
    url = f"/api/engine/preparation-sessions/{report['session_id']}"
    claimed = client.post(
        f"{url}/actions",
        json={"action_id": run["id"], "expected_config_revision": report["config_revision"]},
    )
    assert claimed.status_code == 202, claimed.get_json()
    deadline = time.monotonic() + 60
    while True:
        final = client.get(url).get_json()
        if final["phase"] in fallback.SETTLED:
            break
        assert time.monotonic() < deadline, final
        time.sleep(0.02)
    safe, native = final["attempt_history"]
    assert (safe["profile"], safe["code"]) == ("safe", "safe_file_operation_blocked")
    assert (native["profile"], native["code"]) == ("native", "desktop_run_launch_invalid")
    assert exported(client, source, reference)["status"] == "failed"
    # No SSE consumer is present. The latest provider has no start, but safe did execute.
    fact = final["figure_snapshot_invalidation"]
    assert (
        fact["execution_id"]
        == m.current_ctx().svg_execution_attempts[safe["attempt_id"]][
            "figure_snapshot_invalidation"
        ]["execution_id"]
    )
    assert native["attempt_id"] not in m.current_ctx().svg_execution_attempts
    assert reference["receiptId"] in fact["receipt_ids"]


@fallback.nativekit.needs_user_python
def test_real_desktop_fallback_saves_and_exports_svg_without_another_execution(tmp_path):
    proj, log = fallback._project(tmp_path, fallback.REPLACE_SCRIPT)
    with fallback.sidecar_app(proj, tmp_path / "work", env_overrides=fallback.ENV) as app:
        initial = fallback._create(app)
        final = fallback._settled(app, fallback._run(app, initial))
        assert final["phase"] == "completed", final
        assert fallback._runs(log) == 2
        fact = final["figure_snapshot_invalidation"]
        live = [
            session
            for session in fallback._native_sessions(app, proj)
            if session["state"] not in ("ended", "failed")
        ]
        assert len(live) == 1
        assert live[0]["figure_snapshot_invalidation"]["execution_id"] == fact["execution_id"]
        source = final["captured"][0]["asset_id"]
        _, saved = app.call("/api/layouts/saved?target=project", document(source, []))
        reference = saved["figure_snapshots"]["panel"]
        assert reference["executionOwner"] == {"script": "fig.py", "run_config": None}
        _, result = app.call(
            "/api/export", spec(source, scope="original", formats=("svg",), reference=reference)
        )
        assert result["status"] == "done", result
        assert result["outputs"][0]["status"] == "done", result
        assert fallback._runs(log) == 2


def _context():
    ctx = SimpleNamespace(
        id="project-A", svg_executions=svgexecution.Ledger(), svg_execution_attempts={}
    )
    sess = SimpleNamespace(
        session_id="session-A",
        project_id=ctx.id,
        target={"script": "plot.py", "run": SimpleNamespace(config_id="config-A")},
        config_revision=1,
        observation_seq=7,
        ledger=execpolicy.AttemptLedger(),
        lock=threading.RLock(),
    )
    sess.ledger.begin_run()
    return ctx, sess


def _attempt(sess, attempt_id, profile="safe"):
    sess.ledger.record(attempt_id, execpolicy.SpecKey(profile, "interpreter", "launch"))


def _fact(ctx, attempt_id, *, script="plot.py", config="config-A"):
    identity = svgexecution.owner(script, config)
    execution_id = executionfact.new_id()
    state = ctx.svg_executions.observe(
        identity, execution_id, executionfact.started_ns(execution_id), object()
    )
    fact = ctx.svg_executions.fact(state)
    ctx.svg_execution_attempts[attempt_id] = {"figure_snapshot_invalidation": fact}
    return fact


def test_later_attempt_without_start_keeps_safe_then_accepts_native_current_identity():
    ctx, sess = _context()
    _attempt(sess, "safe")
    safe = _fact(ctx, "safe")
    _attempt(sess, "native", "native")
    assert m._preparation_execution_result(ctx, sess)["figure_snapshot_invalidation"] == safe
    native = _fact(ctx, "native")
    # A late safe callback cannot replace the accepted native execution.
    ctx.svg_execution_attempts["safe"] = {"figure_snapshot_invalidation": safe}
    assert m._preparation_execution_result(ctx, sess)["figure_snapshot_invalidation"] == native


@pytest.mark.parametrize("phase", ["before_registration", "registered", "discarded"])
def test_new_explicit_run_without_start_does_not_replay_previous_run(phase):
    ctx, sess = _context()
    _attempt(sess, "old")
    _fact(ctx, "old")
    sess.ledger.begin_run()
    if phase != "before_registration":
        _attempt(sess, "new")
    if phase == "discarded":
        sess.ledger.discard("new")
    assert m._preparation_execution_result(ctx, sess) == {}


@pytest.mark.parametrize(
    "mismatch", ["project", "script", "configuration", "superseded", "unknown"]
)
def test_facts_require_the_current_project_source_owner_and_execution(mismatch):
    ctx, sess = _context()
    _attempt(sess, "safe")
    _fact(ctx, "safe")
    if mismatch == "project":
        sess.project_id = "project-B"
    elif mismatch == "script":
        sess.target["script"] = "other.py"
    elif mismatch == "configuration":
        sess.target["run"].config_id = "config-B"
    elif mismatch == "superseded":
        _fact(ctx, "another-session")
    else:
        ctx.svg_executions.uncertain(
            svgexecution.owner("plot.py", "config-A"), object(), time.monotonic_ns()
        )
    assert m._preparation_execution_result(ctx, sess) == {}


def test_session_hint_uses_same_current_run_fact_without_advancing_report(monkeypatch):
    ctx, sess = _context()
    _attempt(sess, "safe")
    safe = _fact(ctx, "safe")
    _attempt(sess, "native", "native")
    monkeypatch.setitem(m.PROJECTS, ctx.id, ctx)
    monkeypatch.setattr(m.engine_prepsession.SESSIONS, "get", lambda sid, pj: sess)
    events = []
    monkeypatch.setattr(m, "sse_publish", lambda kind, payload: events.append((kind, payload)))
    hint = {"session_id": sess.session_id, "project_id": ctx.id, "config_revision": 1}
    m._publish_preparation_session(hint)
    assert events[-1][1]["figure_snapshot_invalidation"] == safe
    assert "provider" not in events[-1][1] and sess.observation_seq == 7
    sess.ledger.begin_run()
    _attempt(sess, "new")
    m._publish_preparation_session(hint)
    assert "figure_snapshot_invalidation" not in events[-1][1]
    sess.config_revision += 1
    _fact(ctx, "new")
    m._publish_preparation_session(hint)
    assert "figure_snapshot_invalidation" not in events[-1][1]
