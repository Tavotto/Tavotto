"""Actual native transport/session dispatch and actual child handler AST; no science imports."""

from __future__ import annotations

import ast
import collections
import concurrent.futures
import json
import socket
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import executionfact, figcapture, nativesession, patchspec, pool, runcodes
from tavotto.engine.runcodes import RunError

ROOT = Path(__file__).resolve().parents[1]


def child_handler():
    tree = ast.parse((ROOT / "src/tavotto/engine/wireproto.py").read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    ns = dict(
        collections=collections,
        sys=sys,
        traceback=traceback,
        Path=Path,
        figcapture=figcapture,
        patchspec=patchspec,
    )
    exec(compile(tree, "actual-wireproto-ast", "exec"), ns)
    wireproto = SimpleNamespace(**ns)
    events = []
    session = SimpleNamespace(
        states={"History": SimpleNamespace(unrestored=[])},
        failures={},
        artifact_source=None,
        preview_dpi=100,
    )
    session.do_render = lambda *_: (
        events.append("render") or {"manifest": {"scope": "controlled"}, "svg": "<svg/>"}
    )
    run = SimpleNamespace(
        session=session,
        execution_id=executionfact.new_id(),
        control=object(),
        released=False,
        _barrier_released=False,
        terminated=False,
    )
    tree = ast.parse((ROOT / "src/tavotto/engine/bridge_runner.py").read_text(encoding="utf-8"))
    node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "_NativeHandler"
    )
    env = {"run": run, "wireproto": wireproto, "_PKG": SimpleNamespace(executionfact=executionfact)}
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "actual-native-handler-ast", "exec"), env
    )
    handler = env["_NativeHandler"](session)
    handler.configure_run = lambda _: events.append("configure")
    handler.select_artifact = lambda _: events.append("artifact")
    handler.ensure_built = lambda _: events.append("build")
    return wireproto, handler, run, events


def child_request(handler, expected, *, command="render_existing", stem="History"):
    return handler.handle_v1(
        dict(
            protocol_version=1,
            request_id="native-fixture",
            cmd=command,
            stem=stem,
            payload={"patches": [], "inline_svg": True, "expected_execution_id": expected},
        )
    )


def test_child_existing_capture_needs_no_fabricated_safe_build_flag():
    _, handler, run, events = child_handler()
    assert not hasattr(handler, "built")
    result = child_request(handler, run.execution_id)
    assert result["execution_id"] == run.execution_id and result["svg"] == "<svg/>"
    assert events == ["configure", "artifact", "render"]
    assert not hasattr(handler, "built")


@pytest.mark.parametrize(
    "case",
    [
        "wrong",
        "invalid",
        "released",
        "barrier_released",
        "terminated",
        "disconnected",
        "session",
        "stem",
    ],
)
def test_child_rejects_changed_run_before_any_renderer_or_configuration(case):
    wire, handler, run, events = child_handler()
    expected = run.execution_id
    if case == "wrong":
        run.execution_id = executionfact.new_id()
    elif case == "invalid":
        expected = "not-an-execution-fact"
    elif case in {"released", "terminated"}:
        setattr(run, case, True)
    elif case == "barrier_released":
        run._barrier_released = True
    elif case == "disconnected":
        run.control = None
    elif case == "session":
        run.session = object()
    with pytest.raises(wire.ProtocolError) as exc:
        child_request(handler, expected, stem="absent" if case == "stem" else "History")
    assert exc.value.code == ("bad_request" if case == "invalid" else "svg_source_unavailable")
    assert events == []


def test_child_reply_is_actual_fact_and_ordinary_reply_shape_is_unchanged():
    _, handler, run, events = child_handler()
    expected = run.execution_id
    actual = executionfact.new_id()

    def render(*_):
        run.execution_id = actual
        return {"manifest": {}, "svg": "<svg/>"}

    handler.session.do_render = render
    assert child_request(handler, expected)["execution_id"] == actual
    ordinary = child_request(handler, expected, command="render")
    assert "execution_id" not in ordinary
    assert "build" in events


def reply(peer, request, **fields):
    peer.sendall(
        json.dumps(
            {"protocol_version": 1, "request_id": request["request_id"], "ok": True, **fields}
        ).encode()
        + b"\n"
    )


def serve(peer, action):
    with peer.makefile("rb") as stream:
        request = json.loads(stream.readline())
        action(request)
    return request


@pytest.fixture
def connected(tmp_path):
    parent, peer = socket.socketpair()
    peer.settimeout(3)
    session = nativesession.NativeSession(
        session_id="native-existing-fixture",
        descriptor={
            "out_dir": str(tmp_path / "out"),
            "metadata": {
                "project_root": str(tmp_path),
                "cwd": str(tmp_path),
                "interpreter": sys.executable,
                "target_kind": "script",
            },
        },
    )
    session._set_state(nativesession.STARTING_PYTHON)
    session._on_frame(
        {
            "bridge_event": "execution_started",
            "execution_id": executionfact.new_id(),
            "script": "plot.py",
        }
    )
    session._on_frame({"bridge_event": "barrier", "stems": ["History"]})
    transport = nativesession.NativeTransport(
        parent, on_event=session._on_frame, on_close=session._on_transport_closed
    )
    session.transport = transport
    transport.start()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(
            serve, peer, lambda req: reply(peer, req, stems={"History": {}}, runtime={})
        )
        session.ensure_built()
        assert task.result(timeout=3)["cmd"] == "build"
    assert session.built  # Established by the original ensure_built contract above.
    try:
        yield session, transport, peer
    finally:
        transport.close()
        peer.close()
        transport.join_reader(3)
        transport._rfile.close()
        assert not transport._reader.is_alive()


def guarded(session, *, expected=None, epoch=None, patches=None):
    return session.override(
        "History",
        patches or [],
        inline_svg=True,
        expected_execution_id=expected if expected is not None else session.execution_id,
        expected_snapshot_epoch=epoch if epoch is not None else session.snapshot_epoch,
    )


def assert_no_wire(peer):
    peer.settimeout(0.03)
    with pytest.raises(TimeoutError):
        peer.recv(1)


def test_parent_valid_guard_uses_original_socket_transport_and_actual_reply(connected):
    session, _, peer = connected
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(
            serve,
            peer,
            lambda req: reply(
                peer,
                req,
                execution_id=session.execution_id,
                manifest={},
                svg="<svg/>",
                unrestored=0,
            ),
        )
        result = guarded(session)
        request = task.result(timeout=3)
    assert result["execution_id"] == session.execution_id
    assert request["cmd"] == "render_existing"
    assert request["payload"] == {
        "patches": [],
        "inline_svg": True,
        "expected_execution_id": session.execution_id,
    }
    assert session._session_list["History"] == patchspec.patch_hash([])


@pytest.mark.parametrize(
    "case", ["wrong_fact", "epoch", "unknown_epoch", "running", "unbuilt", "revoked", "poison"]
)
def test_parent_invalid_context_never_sends(connected, case):
    session, transport, peer = connected
    expected, epoch = session.execution_id, session.snapshot_epoch
    if case == "wrong_fact":
        expected = executionfact.new_id()
    elif case == "epoch":
        epoch += 1
    elif case == "unknown_epoch":
        epoch = True
    elif case == "running":
        session._set_state(nativesession.RUNNING_SCRIPT)
    elif case == "unbuilt":
        session.built = False
    elif case == "revoked":
        with session._lock:
            session._revoke_pending_barrier()
    else:
        transport.poison = {"code": "controlled"}
    rev = session.rev
    with pytest.raises(pool.WorkerError) as exc:
        guarded(session, expected=expected, epoch=epoch)
    assert exc.value.code == (
        "bad_request" if case == "unknown_epoch" else "svg_source_unavailable"
    )
    assert session.rev == rev and not session._session_list
    assert_no_wire(peer)


def test_epoch_is_required_for_a_guarded_native_call(connected):
    session, _, peer = connected
    with pytest.raises(pool.WorkerError) as exc:
        session.override("History", [], expected_execution_id=session.execution_id)
    assert exc.value.code == "bad_request"
    assert_no_wire(peer)


def test_waiting_for_original_figure_lock_does_not_adopt_new_epoch(connected):
    session, _, peer = connected
    expected, epoch = session.execution_id, session.snapshot_epoch
    entered = threading.Event()

    def dispatch():
        entered.set()
        return guarded(session, expected=expected, epoch=epoch)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        with session._figure_lock:
            task = executor.submit(dispatch)
            assert entered.wait(3)
            with session._lock:
                session._revoke_pending_barrier()
        with pytest.raises(pool.WorkerError):
            task.result(timeout=3)
    assert_no_wire(peer)


def test_state_change_while_waiting_for_real_send_lock_is_rechecked(connected, monkeypatch):
    session, transport, peer = connected
    entered = threading.Event()
    original = transport.request
    received = []
    expected = session.execution_id

    def receive_if_sent():
        peer.settimeout(0.2)
        try:
            request = serve(
                peer,
                lambda req: reply(
                    peer, req, execution_id=expected, manifest={}, svg="<svg/>", unrestored=0
                ),
            )
            received.append(request)
        except TimeoutError:
            pass

    def dispatch(*args, **kwargs):
        entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "request", dispatch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        with transport._send_lock:
            task = executor.submit(guarded, session)
            assert entered.wait(3)
            with session._lock:
                session._revoke_pending_barrier()
        receiver = executor.submit(receive_if_sent)
        with pytest.raises(pool.WorkerError):
            task.result(timeout=3)
        receiver.result(timeout=3)
    assert received == []
    assert not transport._waiters


@pytest.mark.parametrize(
    "case", ["missing", "wrong_fact", "released", "epoch", "transport", "generation", "pid"]
)
def test_late_or_unproven_reply_does_not_update_original_edit_ledger(connected, case):
    session, transport, peer = connected
    expected = session.execution_id

    def respond(request):
        fields = (
            {}
            if case == "missing"
            else {"execution_id": executionfact.new_id() if case == "wrong_fact" else expected}
        )
        if case == "released":
            peer.sendall(
                json.dumps(
                    {
                        "bridge_event": "released",
                        "execution_id": executionfact.new_id(),
                        "script": "plot.py",
                    }
                ).encode()
                + b"\n"
            )
        elif case == "epoch":
            with session._lock:
                session._revoke_pending_barrier()
        elif case == "transport":
            session.transport = SimpleNamespace(closed=False, poison=None)
        elif case == "generation":
            session.generation += 1
        elif case == "pid":
            session.process_pid = 1234567
        reply(peer, request, **fields, manifest={}, svg="<svg/>", unrestored=2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(serve, peer, respond)
        with pytest.raises(pool.WorkerError) as exc:
            guarded(session)
        assert exc.value.code == "svg_source_unavailable"
        task.result(timeout=3)
    session.transport = transport
    assert session._session_list == {} and session.inconsistent == {}


def test_same_list_inconsistency_recovery_remains_available(connected):
    session, _, peer = connected
    session.inconsistent["History"] = patchspec.patch_hash([])
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(
            serve,
            peer,
            lambda req: reply(
                peer,
                req,
                execution_id=session.execution_id,
                manifest={},
                svg="<svg/>",
                unrestored=0,
            ),
        )
        guarded(session)
        task.result(timeout=3)
    assert not session.inconsistent
    session.inconsistent["History"] = patchspec.patch_hash(
        [{"gid": "x", "prop": "linewidth", "value": 2}]
    )
    with pytest.raises(RunError) as exc:
        guarded(session)
    assert exc.value.code == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert_no_wire(peer)


def test_ordinary_native_wire_and_reply_need_no_new_identity(connected):
    session, _, peer = connected
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(serve, peer, lambda req: reply(peer, req, manifest={}, unrestored=0))
        response = session.override("History", [], 110, inline_svg=True)
        request = task.result(timeout=3)
    assert request["cmd"] == "render"
    assert request["payload"] == {"patches": [], "preview_dpi": 110, "inline_svg": True}
    assert "execution_id" not in response
