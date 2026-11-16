"""Existing execution selection and original export/PNG dispatch contracts."""

from __future__ import annotations

import concurrent.futures

import pytest

import test_existing_execution_dispatch as dispatch
import test_native_existing_execution as native
from tavotto.engine import (
    enginesession,
    execspec,
    executionfact,
    nativesession,
    pool,
    workerd_client,
)

connected = native.connected
OPERATIONS = ("export", "preview_png", "preview_png_snapshot")


def consume(worker, operation, tmp_path, **kwargs):
    if operation == "export":
        return worker.export("History", [], str(tmp_path / "out.pdf"), **kwargs)
    if operation == "preview_png":
        return worker.preview_png("History", [], 200, "guard", **kwargs)
    return worker.preview_png_snapshot("History", [], 200, **kwargs)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("fault", ["none", "wrong", "cold"])
def test_child_consumers_check_actual_execution_before_any_side_effect(operation, fault):
    ns, handler, events = dispatch.child(built=fault != "cold")
    handler.session.do_export = lambda *_a, **_k: events.append("export") or {"path": "x.pdf"}
    handler.session.do_preview_png = lambda *_a, **_k: events.append("preview") or {"path": "x.png"}
    expected = executionfact.new_id() if fault == "wrong" else handler.execution_id
    command = "export_existing" if operation == "export" else "preview_png_existing"
    req = dict(
        protocol_version=1,
        request_id="consumer",
        cmd=command,
        stem="History",
        payload=dict(patches=[], path="x.pdf", width=200, expected_execution_id=expected),
    )
    if fault == "none":
        result = handler.handle_v1(req)
        assert result["path"] == ("x.pdf" if operation == "export" else "x.png")
        assert events == ["configure", "artifact", "export" if operation == "export" else "preview"]
    else:
        with pytest.raises(ns["ProtocolError"]) as exc:
            handler.handle_v1(req)
        assert exc.value.code == "svg_source_unavailable"
        assert events == []


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("reply_fact", ["current", "missing", "wrong"])
def test_python_consumers_guard_original_dispatch_and_reply(tmp_path, operation, reply_fact):
    worker = dispatch.PROTOCOL._worker(
        lambda env: dispatch.PROTOCOL._echo(
            env,
            path="x.png",
            **(
                {"execution_id": expected if reply_fact == "current" else executionfact.new_id()}
                if reply_fact != "missing"
                else {}
            ),
        ),
        tmp_path,
    )
    expected = dispatch.observe(worker)
    worker.ensure_built = lambda: pytest.fail("Guarded consumer must not build")
    if reply_fact == "current":
        consume(worker, operation, tmp_path, expected_execution_id=expected)
    else:
        with pytest.raises(pool.WorkerError, match="execution"):
            consume(worker, operation, tmp_path, expected_execution_id=expected)
    sent = worker.proc.stdin.sent[-1]
    assert sent["cmd"] == ("export_existing" if operation == "export" else "preview_png_existing")
    assert sent["payload"]["expected_execution_id"] == expected
    assert "execution_watch" not in sent["payload"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_workerd_consumers_never_reopen_unknown_session(tmp_path, monkeypatch, operation):
    worker, client = dispatch.WORKERD._worker(
        monkeypatch,
        tmp_path,
        [
            {"ok": True, "session_id": "s-original"},
            workerd_client.WorkerdError("expired", code="unknown_session"),
        ],
    )
    worker.built = True
    expected = dispatch.observe(worker)
    worker.ensure_built = lambda: pytest.fail("Guarded consumer must not build")
    with pytest.raises(pool.WorkerError) as exc:
        consume(worker, operation, tmp_path, expected_execution_id=expected)
    assert exc.value.code == "svg_source_unavailable"
    assert [op for op, _ in client.calls] == [
        "open_session",
        "export_existing" if operation == "export" else "preview_png_existing",
    ]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("released", [False, True])
def test_native_consumer_uses_original_transport_and_rejects_late_epoch(
    connected, tmp_path, operation, released
):
    session, _, peer = connected
    expected, epoch = session.execution_id, session.snapshot_epoch

    def answer(req):
        if released:
            with session._lock:
                session._revoke_pending_barrier()
        native.reply(peer, req, path="x.png", execution_id=expected, unrestored=1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(native.serve, peer, answer)
        if released:
            with pytest.raises(pool.WorkerError):
                consume(
                    session,
                    operation,
                    tmp_path,
                    expected_execution_id=expected,
                    expected_snapshot_epoch=epoch,
                )
            assert session.inconsistent == {}
        else:
            consume(
                session,
                operation,
                tmp_path,
                expected_execution_id=expected,
                expected_snapshot_epoch=epoch,
            )
            assert "History" in session.inconsistent  # Original restoration debt is still honored.
        req = task.result(timeout=3)
    assert req["cmd"] == ("export_existing" if operation == "export" else "preview_png_existing")
    assert req["payload"]["expected_execution_id"] == expected
    assert "expected_snapshot_epoch" not in req["payload"]


def safe_candidate(tmp_path, monkeypatch):
    candidate = dispatch.PROTOCOL._worker(lambda env: dispatch.PROTOCOL._echo(env), tmp_path)
    candidate.run = execspec.RunSelection("rc_known", ("--value", "4"))
    candidate.artifact_source = None
    candidate.write_roots = (str(tmp_path / "allowed"),)
    candidate.alive = lambda: not candidate._dead
    candidate._dead = False
    candidate._evicted = False
    key = pool._worker_key(
        candidate.figures_dir, candidate.script_name, None, candidate.run, candidate.write_roots
    )
    monkeypatch.setattr(pool, "_workers", {key: candidate})
    monkeypatch.setattr(
        pool, "get", lambda *_a, **_k: pytest.fail("Existing selection cannot acquire")
    )
    return candidate


@pytest.mark.parametrize(
    "fault",
    ["none", "root", "script", "entry", "unbuilt", "dead", "evicted", "replaced", "run", "native"],
)
def test_resolver_reuses_exact_registered_safe_variant(tmp_path, monkeypatch, fault):
    candidate = safe_candidate(tmp_path, monkeypatch)
    request = dict(
        project_root=str(tmp_path),
        script=candidate.script_name,
        entry=candidate.entry,
        stem="History",
        existing_only=True,
        existing_worker=candidate,
    )
    if fault in {"root", "script", "entry"}:
        request[{"root": "project_root", "script": "script", "entry": "entry"}[fault]] = "other"
    elif fault in {"unbuilt", "dead", "evicted"}:
        setattr(
            candidate,
            {"unbuilt": "built", "dead": "_dead", "evicted": "_evicted"}[fault],
            fault != "unbuilt",
        )
    elif fault == "replaced":
        monkeypatch.setattr(pool, "_workers", {})
    elif fault == "run":
        request["run"] = execspec.RunSelection("rc_other", ("5",))
    elif fault == "native":
        request["execution_profile"] = enginesession.PROFILE_NATIVE
    assert enginesession.resolve(**request) is (candidate if fault == "none" else None)


def test_explicit_worker_requires_existing_only(tmp_path, monkeypatch):
    candidate = safe_candidate(tmp_path, monkeypatch)
    with pytest.raises(pool.WorkerError) as exc:
        enginesession.resolve(
            project_root=str(tmp_path),
            script=candidate.script_name,
            entry=candidate.entry,
            stem="History",
            existing_worker=candidate,
        )
    assert exc.value.code == "bad_request"


def test_native_resolver_cannot_bypass_original_registered_route(connected, monkeypatch):
    session, _, _ = connected
    registry = nativesession.NativeSessionRegistry()
    monkeypatch.setattr(nativesession, "REGISTRY", registry)
    args = dict(
        project_root=session.project_root,
        script="plot.py",
        entry="__main__",
        stem="History",
        execution_profile=enginesession.PROFILE_NATIVE,
        existing_only=True,
        existing_worker=session,
    )
    assert enginesession.resolve(**args) is None
    registry._sessions[session.session_id] = session
    registry._routes[registry.route_key(session.project_root, "plot.py", "History")] = (
        session.session_id
    )
    assert enginesession.resolve(**args) is session
    session.execution_script = "other.py"
    assert enginesession.resolve(**args) is None


@pytest.mark.parametrize("operation", OPERATIONS)
def test_ordinary_python_consumer_wire_has_no_guard_fields(tmp_path, operation):
    worker = dispatch.PROTOCOL._worker(
        lambda env: dispatch.PROTOCOL._echo(env, path="x.png"), tmp_path
    )
    consume(worker, operation, tmp_path)
    sent = worker.proc.stdin.sent[-1]
    assert sent["cmd"] == ("export" if operation == "export" else "preview_png")
    assert "expected_execution_id" not in sent["payload"]
