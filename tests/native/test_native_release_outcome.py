"""A possibly dispatched continue revokes its old receipt, not execution facts.

The peer really receives socket bytes. A live barrier EOF also releases the
runner, while a known refusal before sending cannot manufacture execution.
"""

from __future__ import annotations

import concurrent.futures
import errno
import json
import select
import socket
import sys
import threading

import pytest

from support import desktopkit, nativekit
from tavotto.engine import desktopproc, executionfact, nativesession, pool, receipt, runcodes
from tavotto.engine.runcodes import RunError


@pytest.fixture
def connected(tmp_path):
    parent, peer = socket.socketpair()
    peer.settimeout(3)
    session = nativesession.NativeSession(
        session_id="release-outcome",
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
    session._on_frame({"bridge_event": "barrier", "stems": ["B"]})
    session.transport = nativesession.NativeTransport(
        parent, on_event=session._on_frame, on_close=session._on_transport_closed
    )
    session.transport.start()
    try:
        yield session, peer
    finally:
        session.transport.close()
        # All peer reads/makefiles have finished before teardown. Close this
        # fixture-owned endpoint directly: it releases a pending Windows recv
        # without waiting for a reverse FIN that macOS need not have sent.
        peer.close()
        session.transport.join_reader(3)
        assert not session.transport._reader.is_alive()
        session.transport._rfile.close()


def _receipt_id(session):
    return receipt.from_native_session(session, "plot.py").receipt_id


def _reply(peer, request, **fields):
    peer.sendall(
        json.dumps(
            {
                "protocol_version": pool.PROTOCOL_VERSION,
                "request_id": request["request_id"],
                **fields,
            }
        ).encode()
        + b"\n"
    )


def _serve(peer, action):
    with peer.makefile("rb") as stream:
        request = json.loads(stream.readline())
        assert request["cmd"] == "continue"
        action(request)
    return request


def _close_peer(peer, *, eof_received):
    try:
        peer.shutdown(socket.SHUT_RDWR)
    except OSError as exc:
        # macOS can report ENOTCONN after the other side's SHUT_WR and
        # an observed EOF. Other errors, or ENOTCONN without EOF, are failures.
        if not eof_received or exc.errno != errno.ENOTCONN:
            raise
    peer.close()


@pytest.mark.parametrize(
    ("eof_received", "shutdown_errno"),
    [(True, errno.ENOTCONN), (False, errno.ENOTCONN), (True, errno.EBADF), (True, errno.EINVAL)],
)
def test_peer_close_accepts_only_enotconn_after_observed_eof(
    connected, eof_received, shutdown_errno
):
    session, peer = connected
    if eof_received:
        session.transport.sock.shutdown(socket.SHUT_WR)
        assert peer.recv(1) == b""

    class ShutdownFailure:
        def shutdown(self, how):
            assert how == socket.SHUT_RDWR
            raise OSError(shutdown_errno, "injected peer shutdown failure")

        def close(self):
            peer.close()

    if eof_received and shutdown_errno == errno.ENOTCONN:
        _close_peer(ShutdownFailure(), eof_received=eof_received)
        session.transport.join_reader(3)
        assert not session.transport._reader.is_alive()
        assert session.transport.closed and session.transport.poison is None
        assert session.state == nativesession.ENDED
    else:
        with pytest.raises(OSError) as error:
            _close_peer(ShutdownFailure(), eof_received=eof_received)
        assert error.value.errno == shutdown_errno
        assert peer.fileno() >= 0


@pytest.mark.parametrize("already_closed", [False, True])
def test_fixture_closes_peer_before_waiting_for_pending_receive(
    tmp_path, monkeypatch, already_closed
):
    socketpair = socket.socketpair

    class PendingReceiveSocket:
        def __init__(self, sock):
            self.sock = sock

        def shutdown(self, how):
            # Keep the real receive pending until the peer sends EOF, as a
            # Winsock receive already in progress can outlive local shutdown.
            self.sock.shutdown(socket.SHUT_WR if how == socket.SHUT_RDWR else how)

        def __getattr__(self, name):
            return getattr(self.sock, name)

    def pair():
        parent, peer = socketpair()
        return PendingReceiveSocket(parent), peer

    monkeypatch.setattr(socket, "socketpair", pair)
    fixture = connected.__wrapped__(tmp_path)
    session, peer = next(fixture)
    reader_file = session.transport._rfile

    class ReaderFile:
        def readline(self):
            return reader_file.readline()

        def close(self):
            # Fail before acquiring BufferedReader's lock if teardown regresses.
            assert peer.fileno() == -1, "peer must close before the reader file"
            assert not session.transport._reader.is_alive()
            reader_file.close()

    session.transport._rfile = ReaderFile()
    try:
        if already_closed:
            session.transport.close()
        assert session.transport._reader.is_alive()
        with pytest.raises(StopIteration):
            next(fixture)
        assert peer.fileno() == -1
        assert not session.transport._reader.is_alive()
        assert reader_file.closed
    finally:
        # Also release the real receive if the ordering assertion fails.
        session.transport.close()
        peer.close()
        session.transport.join_reader(3)
        reader_file.close()


def test_fixture_closes_peer_without_reverse_fin_after_reader_eof(tmp_path, monkeypatch):
    socketpair = socket.socketpair
    shutdown_attempts = []

    class ReceivedEofSocket:
        def __init__(self, sock):
            self.sock = sock

        def shutdown(self, how):
            shutdown_attempts.append(how)
            # macOS may reject shutdown after receiving the peer's EOF. The
            # makefile still owns the descriptor, so close alone sends no FIN.
            raise OSError(errno.ENOTCONN, "injected shutdown after reader EOF")

        def __getattr__(self, name):
            return getattr(self.sock, name)

    def pair():
        parent, peer = socketpair()
        return ReceivedEofSocket(parent), peer

    monkeypatch.setattr(socket, "socketpair", pair)
    fixture = connected.__wrapped__(tmp_path)
    session, peer = next(fixture)
    reader_file = session.transport._rfile
    epoch, old_receipt = session.snapshot_epoch, _receipt_id(session)
    try:
        peer.shutdown(socket.SHUT_WR)
        session.transport.join_reader(3)
        assert not session.transport._reader.is_alive()
        assert session.transport.closed and session.transport.poison is None
        assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
        session.transport.close()
        assert shutdown_attempts == [socket.SHUT_RDWR]
        assert not reader_file.closed
        assert select.select([peer], [], [], 0)[0] == []  # No reverse data or EOF exists.
        with pytest.raises(StopIteration):
            next(fixture)
        assert shutdown_attempts == [socket.SHUT_RDWR, socket.SHUT_RDWR]
        assert peer.fileno() == -1 and reader_file.closed
        assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    finally:
        peer.close()
        session.transport.close()
        session.transport.join_reader(3)
        reader_file.close()


@pytest.mark.parametrize("action", ["resume", "detach"])
@pytest.mark.parametrize(
    "failure", ["eof", "timeout", "bad_frame", "bad_echo", "wrong_id", "bad_result", "bad_error"]
)
def test_dispatched_release_with_unknown_result_revokes_only_receipt(
    connected, monkeypatch, action, failure
):
    session, peer = connected
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 0.15)
    old_receipt, epoch, execution_id = (
        _receipt_id(session),
        session.snapshot_epoch,
        session.execution_id,
    )
    observations = []
    session._on_change = lambda current, event: observations.append(
        (current.snapshot_epoch, current.execution_id)
    )

    def respond(request):
        if failure == "eof":
            peer.shutdown(socket.SHUT_WR)
        elif failure == "bad_frame":
            peer.sendall(b"{broken json\n")
        elif failure == "bad_echo":
            _reply(peer, request, ok=True, protocol_version=99)
        elif failure == "wrong_id":
            _reply(peer, request, ok=True, request_id="different-request")
        elif failure == "bad_result":
            _reply(peer, request, ok="not-a-boolean")
        elif failure == "bad_error":
            _reply(peer, request, ok=False, error="not-an-error-object")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        with pytest.raises(RunError):
            getattr(session, action)()
        sent.result(timeout=3)
    assert session.snapshot_epoch == epoch + 1
    assert _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id
    assert observations[-1] == (epoch + 1, execution_id)
    assert session.figure_snapshot_invalidation is None
    if failure != "eof":
        assert session.transport.poison is not None


def test_partial_socket_send_is_unknown_even_when_sendall_raises(connected):
    session, peer = connected
    epoch, old_receipt, execution_id = (
        session.snapshot_epoch,
        _receipt_id(session),
        session.execution_id,
    )
    real = session.transport.sock

    class PartialSend:
        def sendall(self, data):
            real.sendall(data[: len(data) // 2])
            raise BrokenPipeError("failed after writing bytes")

        def __getattr__(self, name):
            return getattr(real, name)

    session.transport.sock = PartialSend()
    with pytest.raises(RunError):
        session.resume()
    assert peer.recv(8192)  # Real bytes reached the other socket, not a mocked dispatch claim.
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id
    assert session.transport.poison is not None


def test_known_release_event_then_lost_response_does_not_revoke_twice(connected):
    session, peer = connected
    old_receipt, epoch = _receipt_id(session), session.snapshot_epoch
    execution_id = executionfact.new_id()

    def respond(request):
        peer.sendall(
            json.dumps(
                {"bridge_event": "released", "execution_id": execution_id, "script": "plot.py"}
            ).encode()
            + b"\n"
        )
        peer.shutdown(socket.SHUT_WR)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        with pytest.raises(RunError):
            session.resume()
        sent.result(timeout=3)
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id


def test_failed_waiter_and_late_close_callback_revoke_same_barrier_only_once(connected):
    session, peer = connected
    epoch, old_receipt = session.snapshot_epoch, _receipt_id(session)
    close_callback_waiting, allow_close_callback = threading.Event(), threading.Event()

    def delayed_close(reason):
        close_callback_waiting.set()
        assert allow_close_callback.wait(3)
        session._on_transport_closed(reason)

    session.transport._on_close = delayed_close
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: peer.shutdown(socket.SHUT_WR))
        try:
            with pytest.raises(RunError):
                session.resume()
            assert close_callback_waiting.wait(3)
            assert session.snapshot_epoch == epoch + 1
            assert session.state == nativesession.BARRIER  # close callback is deliberately pending
        finally:
            allow_close_callback.set()
        sent.result(timeout=3)
    session.transport.join_reader(3)
    assert session.state == nativesession.ENDED
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt


@pytest.mark.parametrize("action", ["resume", "detach"])
def test_explicit_restore_refusal_preserves_barrier_receipt(connected, action):
    session, peer = connected
    old_receipt, epoch, execution_id = (
        _receipt_id(session),
        session.snapshot_epoch,
        session.execution_id,
    )

    def respond(request):
        _reply(
            peer,
            request,
            ok=False,
            error={"code": runcodes.NATIVE_FIGURE_INCONSISTENT, "message": "not restored"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        with pytest.raises(RunError) as error:
            getattr(session, action)()
        sent.result(timeout=3)
    assert error.value.code == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert session.snapshot_epoch == epoch and _receipt_id(session) == old_receipt
    assert session.execution_id == execution_id and session.state == nativesession.BARRIER


@pytest.mark.parametrize(
    "failure", ["closed", "poisoned", "not_at_barrier", "inconsistent", "encode"]
)
def test_known_before_send_failure_does_not_revoke_receipt(connected, monkeypatch, failure):
    session, peer = connected
    old_receipt, epoch = _receipt_id(session), session.snapshot_epoch
    if failure == "closed":
        session.transport._closed = True
    elif failure == "poisoned":
        session.transport.poison = {"code": runcodes.NATIVE_RELAY_FAILED}
    elif failure == "not_at_barrier":
        session._set_state(nativesession.CONTINUING)
    elif failure == "inconsistent":
        session.inconsistent["B"] = "unrestored"
    else:
        original = pool.build_envelope
        monkeypatch.setattr(
            pool, "build_envelope", lambda *a, **k: {**original(*a, **k), "bad": set()}
        )
    with pytest.raises((RunError, TypeError)):
        session.resume()
    assert session.snapshot_epoch == epoch and _receipt_id(session) == old_receipt
    peer.settimeout(0.05)
    with pytest.raises(TimeoutError):
        peer.recv(1)
    assert not session.transport._waiters


def test_channel_closes_while_waiting_for_sender_lock_revokes_only_for_eof(connected, monkeypatch):
    session, peer = connected
    epoch, old_receipt = session.snapshot_epoch, _receipt_id(session)
    envelope_ready = threading.Event()
    original = pool.build_envelope

    def envelope(*args, **kwargs):
        value = original(*args, **kwargs)
        envelope_ready.set()
        return value

    monkeypatch.setattr(pool, "build_envelope", envelope)
    session.transport._send_lock.acquire()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        attempted = executor.submit(session.resume)
        try:
            assert envelope_ready.wait(3)
            session.transport.close()
        finally:
            session.transport._send_lock.release()
        with pytest.raises(RunError):
            attempted.result(timeout=3)
    assert peer.recv(1) == b""
    # This command did not send, but EOF itself releases the live barrier.
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt


def test_live_barrier_disconnect_without_continue_revokes_current_receipt(connected):
    session, peer = connected
    epoch, old_receipt, execution_id = (
        session.snapshot_epoch,
        _receipt_id(session),
        session.execution_id,
    )
    peer.shutdown(socket.SHUT_WR)
    assert session.wait_for_state([nativesession.ENDED], 3) == nativesession.ENDED
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id


@pytest.mark.parametrize("event", ["exit", "shutdown"])
def test_confirmed_runner_end_then_eof_does_not_revoke_receipt(connected, event):
    session, peer = connected
    epoch, old_receipt = session.snapshot_epoch, _receipt_id(session)
    session._on_frame({"bridge_event": event, "code": 0, "reason": "terminate"})
    peer.shutdown(socket.SHUT_WR)
    session.transport.join_reader(3)
    assert session.snapshot_epoch == epoch and _receipt_id(session) == old_receipt


@pytest.mark.parametrize("action", ["shutdown", "force_kill", "registry_shutdown"])
def test_controller_shutdown_revokes_before_optimistic_detached_state(connected, action):
    session, peer = connected
    epoch, old_receipt, execution_id = (
        session.snapshot_epoch,
        _receipt_id(session),
        session.execution_id,
    )
    detached_epochs = []

    def observed(current, event):
        if event["state"] == nativesession.DETACHED:
            detached_epochs.append(current.snapshot_epoch)

    if action == "registry_shutdown":
        registry = nativesession.NativeSessionRegistry()
        registry.on_change = observed
        registry.adopt(session)
        registry.shutdown_all()
    else:
        session._on_change = observed
        getattr(session, action)()
    assert peer.recv(1) == b""
    assert session.state == nativesession.DETACHED
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id
    assert detached_epochs and all(value == epoch + 1 for value in detached_epochs)
    _close_peer(peer, eof_received=True)
    session.transport.join_reader(3)
    assert not session.transport._reader.is_alive()
    session.shutdown()
    assert session.snapshot_epoch == epoch + 1


@pytest.mark.parametrize("delivery", ["failed", "delivered"])
@pytest.mark.parametrize("close", ["peer", "controller"])
def test_unacknowledged_terminate_then_eof_cannot_preserve_old_receipt(connected, delivery, close):
    session, peer = connected
    epoch, old_receipt, execution_id = (
        session.snapshot_epoch,
        _receipt_id(session),
        session.execution_id,
    )
    observed = []
    session._on_change = lambda current, event: observed.append(current.snapshot_epoch)
    close_events = []

    def closed(reason):
        close_events.append((reason, threading.current_thread()))
        session._on_transport_closed(reason)

    session.transport._on_close = closed
    if delivery == "failed":
        # Actual socket half-close: send_oneway suppresses the resulting error,
        # but the reader remains alive and no runner acknowledgement exists.
        session.transport.sock.shutdown(socket.SHUT_WR)
    session.terminate()
    assert session.state == nativesession.ENDED  # Only the controller's optimistic state.
    if delivery == "delivered":
        with peer.makefile("rb") as stream:
            assert json.loads(stream.readline())["cmd"] == "terminate"
    else:
        assert peer.recv(1) == b""
    assert session.transport._reader.is_alive()
    if close == "peer":
        _close_peer(peer, eof_received=delivery == "failed")
    else:
        session.shutdown()
        # Assert controller revocation before the fixture peer acknowledges EOF.
        assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
        assert peer.recv(1) == b""
        _close_peer(peer, eof_received=True)
    session.transport.join_reader(3)
    assert not session.transport._reader.is_alive()
    assert session.transport.closed and session.transport.poison is None
    assert len(close_events) == 1 and close_events[0][0] is None
    if close == "peer":
        assert close_events[0][1] is session.transport._reader
    assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
    assert session.execution_id == execution_id
    assert observed[-1] == epoch + 1


@pytest.mark.parametrize("action", ["resume", "detach"])
def test_late_continue_reply_never_clears_the_next_barrier(connected, action):
    session, peer = connected
    epoch = session.snapshot_epoch
    execution_id = executionfact.new_id()
    next_barrier = threading.Event()

    def observed(current, event):
        if current.state == nativesession.BARRIER and current.snapshot_epoch == epoch + 2:
            next_barrier.set()

    session._on_change = observed

    def respond(request):
        for frame in (
            {"bridge_event": "released", "execution_id": execution_id, "script": "plot.py"},
            {"bridge_event": "barrier", "stems": ["B"]},
        ):
            peer.sendall(json.dumps(frame).encode() + b"\n")
        assert next_barrier.wait(3)
        _reply(peer, request, ok=True, released=True, execution_id=execution_id, script="plot.py")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        getattr(session, action)()
        sent.result(timeout=3)
    if action == "resume":
        assert session.state == nativesession.BARRIER and session.snapshot_epoch == epoch + 2
        peer.shutdown(socket.SHUT_WR)
        session.transport.join_reader(3)
    else:
        assert session.state == nativesession.DETACHED
    assert session.snapshot_epoch == epoch + 3
    assert session.execution_id == execution_id


@nativekit.needs_user_python
@pytest.mark.parametrize("close", ["transport", "shutdown"])
def test_real_runner_eof_without_continue_resumes_and_changes_figure(tmp_path, close):
    session = desktopproc.start(
        desktopkit.launch_for(
            tmp_path,
            desktopkit.FIGURE_HEADER
            + "from pathlib import Path\n"
            + "fig, ax = plt.subplots()\nplt.show()\n"
            + "ax.set_title('changed after EOF')\n"
            + "Path('resumed-after-eof').write_text(ax.get_title(), encoding='utf-8')\n",
        ),
        registry=nativesession.NativeSessionRegistry(),
    )
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        epoch, old_receipt = session.snapshot_epoch, _receipt_id(session)
        execution_id = session.execution_id
        # No continue request or trusted released event. The runner observes
        # actual socket EOF and follows its existing release_barrier path.
        if close == "transport":
            session.transport.close()
        else:
            session.shutdown()
        desktopkit.wait_until(lambda: session.owned.finished)
        assert (tmp_path / "work" / "resumed-after-eof").read_text(encoding="utf-8") == (
            "changed after EOF"
        )
        assert session.snapshot_epoch == epoch + 1 and _receipt_id(session) != old_receipt
        assert session.execution_id == execution_id
    finally:
        if not session.owned.finished:
            session.cancel_owned()
        session.shutdown()
