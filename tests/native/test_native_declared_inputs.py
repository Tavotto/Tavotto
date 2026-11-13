"""当前 native 屏障输入事实：真实控制 socket，真实 common capture，不执行用户代码。"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import select
import socket
import sys
import threading
import time

import pytest

from tavotto.engine import (
    execspec,
    executionfact,
    inputdeclaration,
    nativesession,
    pool,
    receipt,
    runcodes,
)
from tavotto.engine.runcodes import RunError

SCRIPT = "plots/plot.py"


def _fact(root, execution_id, *, script=SCRIPT, mode="project_root", run_config=None):
    source = root / script
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"print('capture entry')\n")
    data = root / "private-input.csv"
    data.write_bytes(b"1,2\n")
    sandbox = root / "box"
    sandbox.mkdir(exist_ok=True)
    cwd = sandbox if mode == "sandbox" else execspec.desktop_cwd(root, script, mode)
    candidate = {
        "version": 1,
        "scope": {"script": script, "cwd_mode": mode, "run_config": run_config},
        "script_path": str(source),
        "files": [{"id": data.name, "path": str(data)}],
        "prefixes": [],
        "mappings": [],
        "rules": [],
        "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
    }
    capture = inputdeclaration.begin(
        candidate,
        script=script,
        script_path=str(source),
        cwd_mode=mode,
        run_config=run_config,
        actual_cwd=str(cwd),
        project_root=str(root),
        sandbox=str(sandbox),
    )
    assert capture is not None
    capture.entered(execution_id)
    fact = capture.finish(hashlib.sha256(source.read_bytes()).hexdigest())
    assert fact is not None
    return fact


def _wait_sequence(session, previous):
    deadline = time.monotonic() + 3
    with session._changed:
        while session.sequence <= previous:
            remaining = deadline - time.monotonic()
            assert remaining > 0, session.state
            session._changed.wait(remaining)


def _event(session, peer, frame):
    previous = session.sequence
    peer.sendall(json.dumps(frame).encode() + b"\n")
    _wait_sequence(session, previous)


def _barrier(session, peer, fact=None, **extra):
    frame = {"bridge_event": "barrier", "stems": ["Figure"]}
    if fact is not None:
        frame.update(execution_id=fact["execution_id"], script=SCRIPT, captured_inputs=fact)
    frame.update(extra)
    _event(session, peer, frame)


@pytest.fixture
def connected(tmp_path):
    parent, peer = socket.socketpair()
    peer.settimeout(3)
    session = nativesession.NativeSession(
        session_id="declared-inputs-control",
        descriptor={
            "out_dir": str(tmp_path / "out"),
            "metadata": {
                "project_root": str(tmp_path),
                "cwd": str(tmp_path),
                "interpreter": sys.executable,
                "target_kind": "script",
                "arg_count": 0,
            },
        },
    )
    session._set_state(nativesession.STARTING_PYTHON)
    session.transport = nativesession.NativeTransport(
        parent, on_event=session._on_frame, on_close=session._on_transport_closed
    )
    session.transport.start()
    _event(
        session,
        peer,
        {
            "bridge_event": "execution_started",
            "execution_id": executionfact.new_id(),
            "script": SCRIPT,
        },
    )
    try:
        yield session, peer
    finally:
        session.transport.close()
        peer.close()
        session.transport.join_reader(3)
        assert not session.transport._reader.is_alive()
        session.transport._rfile.close()


def _current(session):
    return session.captured_inputs_for_receipt(script=SCRIPT)


@pytest.mark.parametrize("mode", ["project", "project_root"])
@pytest.mark.parametrize("owner", ["terminal", "desktop", "desktop_config"])
def test_first_matching_barrier_freezes_private_fact_before_publication(
    connected, tmp_path, mode, owner
):
    session, peer = connected
    run_config = "rc_0123456789ab" if owner == "desktop_config" else None
    cwd = execspec.desktop_cwd(tmp_path, SCRIPT, mode)
    session.cwd = cwd
    if owner != "terminal":
        session.spec = execspec.desktop_native_spec(
            str(tmp_path / SCRIPT),
            interpreter=sys.executable,
            cwd=cwd,
            project_root=str(tmp_path),
            argv=("--seed", "-1") if run_config else (),
            run_config=run_config or "",
        )
        # The private immutable spec wins over public descriptor hints.
        session.run_config, session.arg_count = "untrusted-hint", 77
        session.cwd = str(tmp_path / "stale-descriptor")
    fact = _fact(tmp_path, session.execution_id, mode=mode, run_config=run_config)
    assert _current(session) is None
    seen = []
    published = threading.Event()

    def changed(current, event):
        seen.append(_current(current))
        published.set()

    session._on_change = changed
    _barrier(session, peer, fact)
    assert published.wait(3)
    assert seen == [fact]
    assert _current(session) == fact
    session.process_pid = 4242
    session.last_build = {"runtime": {"report_origin": "build", "pid": 4242}}
    rcpt = receipt.from_native_session(session, SCRIPT)
    summary = inputdeclaration.public_fact(fact)
    assert rcpt.captured_inputs == summary
    assert rcpt.public_facts()["captured_inputs"] == summary
    assert rcpt.to_payload()["captured_inputs"] == summary
    assert session.captured_inputs_for_receipt(script="other.py") is None
    returned = _current(session)
    returned["declaration"]["files"][0]["sha256"] = "0" * 64
    assert _current(session) == fact
    public = json.dumps([session.public_state(), list(session.events)])
    assert "private-input.csv" not in public
    assert "captured_inputs" not in public


@pytest.mark.parametrize("original_fact", [False, True])
def test_duplicate_barrier_neither_upgrades_unknown_nor_overwrites_fact(
    connected, tmp_path, original_fact
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact if original_fact else None)
    epoch = session.snapshot_epoch
    _barrier(session, peer, fact)
    assert _current(session) == (fact if original_fact else None)
    _barrier(session, peer, {**fact, "revision": "sha256:" + "0" * 64})
    assert session.snapshot_epoch == epoch
    assert _current(session) == (fact if original_fact else None)


@pytest.mark.parametrize(
    "bad",
    [
        "wire_id",
        "wire_script",
        "missing_id",
        "missing_script",
        "fact_id",
        "content",
        "scope_script",
        "scope_rc",
        "sandbox",
        "cwd_mode",
        "cwd",
        "terminal_args",
        "terminal_rc",
        "owned_missing_spec",
        "module",
    ],
)
def test_invalid_or_unproven_barrier_never_obtains_capture(connected, tmp_path, bad):
    session, peer = connected
    kw = {}
    if bad == "fact_id":
        execution_id = executionfact.new_id()
    else:
        execution_id = session.execution_id
    if bad == "scope_script":
        kw["script"] = "other.py"
    elif bad in {"scope_rc", "terminal_rc"}:
        kw["run_config"] = "rc_0123456789ab"
    elif bad == "sandbox":
        kw["mode"] = "sandbox"
    elif bad == "cwd_mode":
        kw["mode"] = "project"
    fact = _fact(tmp_path, execution_id, **kw)
    frame = {
        "bridge_event": "barrier",
        "stems": ["Figure"],
        "execution_id": session.execution_id,
        "script": SCRIPT,
        "captured_inputs": fact,
    }
    if bad == "wire_id":
        frame["execution_id"] = executionfact.new_id()
    elif bad == "wire_script":
        frame["script"] = "other.py"
    elif bad == "missing_id":
        del frame["execution_id"]
    elif bad == "missing_script":
        del frame["script"]
    elif bad == "content":
        fact["declaration"]["files"][0]["sha256"] = "0" * 64
    elif bad == "cwd":
        session.cwd = str(tmp_path / "unrelated")
    elif bad == "terminal_args":
        session.arg_count = 2
    elif bad == "terminal_rc":
        session.run_config = "rc_0123456789ab"
    elif bad == "owned_missing_spec":
        session.owned = type(
            "Owner", (), {"finished": True, "confirmed": True, "cancelled": False}
        )()
    elif bad == "module":
        session.target_kind = execspec.TARGET_MODULE
    _event(session, peer, frame)
    assert session.state == nativesession.BARRIER
    assert _current(session) is None


def test_new_epoch_without_fact_cannot_inherit_old_evidence(connected, tmp_path):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    _event(
        session,
        peer,
        {"bridge_event": "released", "execution_id": executionfact.new_id(), "script": SCRIPT},
    )
    assert _current(session) is None
    _barrier(session, peer)
    assert _current(session) is None


def test_owned_configuration_cannot_be_inferred_from_descriptor_hint(connected, tmp_path):
    session, peer = connected
    session.spec = execspec.desktop_native_spec(
        str(tmp_path / SCRIPT),
        interpreter=sys.executable,
        cwd=str(tmp_path),
        project_root=str(tmp_path),
        argv=("--seed", "-1"),
        run_config="rc_0123456789ab",
    )
    session.run_config = "rc_abcdef012345"
    fact = _fact(tmp_path, session.execution_id, run_config=session.run_config)
    _barrier(session, peer, fact)
    assert _current(session) is None


def test_barrier_before_actual_target_start_cannot_claim_capture(connected, tmp_path):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    session.execution_id = None
    session.execution_script = None
    _barrier(session, peer, fact)
    assert _current(session) is None


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
        action(request)


@pytest.mark.parametrize("action", ["resume", "detach"])
def test_late_continue_reply_cannot_clear_new_barrier_fact(connected, tmp_path, action):
    session, peer = connected
    first = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, first)
    next_id = executionfact.new_id()
    second = _fact(tmp_path, next_id)

    def respond(request):
        _event(
            session, peer, {"bridge_event": "released", "execution_id": next_id, "script": SCRIPT}
        )
        _barrier(session, peer, second)
        assert _current(session) == second
        _reply(peer, request, ok=True, released=True, execution_id=next_id, script=SCRIPT)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        getattr(session, action)()
        sent.result(timeout=3)
    assert _current(session) == (second if action == "resume" else None)


def test_dispatched_unknown_release_revokes_capture_without_new_execution(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 0.1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: None)
        with pytest.raises(RunError):
            session.resume()
        sent.result(timeout=3)
    assert _current(session) is None
    assert session.execution_id == fact["execution_id"]


def test_explicit_restore_refusal_keeps_current_barrier_fact(connected, tmp_path):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)

    def refuse(request):
        _reply(
            peer,
            request,
            ok=False,
            error={"code": runcodes.NATIVE_FIGURE_INCONSISTENT, "message": "not restored"},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, refuse)
        with pytest.raises(RunError) as error:
            session.resume()
        sent.result(timeout=3)
    assert error.value.code == runcodes.NATIVE_FIGURE_INCONSISTENT
    assert _current(session) == fact


@pytest.mark.parametrize("close", ["eof", "shutdown", "exit"])
def test_controller_or_runner_end_hides_fact_without_historical_rewrite(connected, tmp_path, close):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    saved = _current(session)
    if close == "shutdown":
        session.shutdown()
    else:
        if close == "exit":
            _event(session, peer, {"bridge_event": "exit", "code": 0})
        peer.shutdown(socket.SHUT_WR)
        session.transport.join_reader(3)
    assert _current(session) is None
    assert saved == fact


def test_eof_epoch_revokes_before_terminal_state_publication(connected, tmp_path, monkeypatch):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    observed = []
    release = session._release_lease

    def release_lease():
        # The real EOF has revoked the epoch, but ENDED is not published yet.
        observed.append((session.state, _current(session)))
        release()

    monkeypatch.setattr(session, "_release_lease", release_lease)
    peer.shutdown(socket.SHUT_WR)
    session.transport.join_reader(3)
    assert observed == [(nativesession.BARRIER, None)]


@pytest.mark.parametrize("had_fact", [False, True])
def test_build_reply_cannot_manufacture_or_replace_barrier_evidence(connected, tmp_path, had_fact):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact if had_fact else None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(
            _serve, peer, lambda request: _reply(peer, request, ok=True, captured_inputs=fact)
        )
        session.ensure_built()
        sent.result(timeout=3)
    assert _current(session) == (fact if had_fact else None)


def test_getter_reads_sealed_fact_after_resources_disappear(connected, tmp_path):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    (tmp_path / SCRIPT).unlink()
    (tmp_path / "private-input.csv").unlink()
    assert _current(session) == fact


def _owned_spec(session, root):
    session.spec = execspec.desktop_native_spec(
        str(root / SCRIPT),
        interpreter=sys.executable,
        cwd=str(root),
        project_root=str(root),
        stem_aliases=(),
    )
    session.process_pid = 4242


def _build_reply(peer, request):
    assert request["cmd"] == "build"
    _reply(peer, request, ok=True, runtime={"report_origin": "build", "pid": 4242})


def test_old_build_body_cannot_borrow_next_barriers_captured_evidence(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    monkeypatch.setattr(nativesession, "BUILD_TIMEOUT", 3)
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 3)
    _owned_spec(session, tmp_path)
    old_id = session.execution_id
    first = _fact(tmp_path, old_id)
    _barrier(session, peer, first)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: _build_reply(peer, request))
        old_body = session.ensure_built()
        sent.result(timeout=3)
    assert old_body["execution_id"] == old_id
    initial = receipt.from_worker(
        session, old_body, control_plane=receipt.CONTROL_PLANE_NATIVE, grant=None
    )
    assert initial.captured_inputs == inputdeclaration.public_fact(first)
    unmarked = {k: v for k, v in old_body.items() if k != "execution_id"}
    assert (
        receipt.from_worker(
            session, unmarked, control_plane=receipt.CONTROL_PLANE_NATIVE, grant=None
        ).captured_inputs
        is None
    )
    next_id = executionfact.new_id()
    second = _fact(tmp_path, next_id)

    def resume(request):
        assert request["cmd"] == "continue"
        _event(
            session, peer, {"bridge_event": "released", "execution_id": next_id, "script": SCRIPT}
        )
        _barrier(session, peer, second)
        _reply(peer, request, ok=True, released=True, execution_id=next_id, script=SCRIPT)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, resume)
        session.resume()
        sent.result(timeout=3)
    assert _current(session) == second
    stale = receipt.from_worker(
        session, old_body, control_plane=receipt.CONTROL_PLANE_NATIVE, grant=None
    )
    assert stale.instance == old_id
    assert stale.captured_inputs is None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: _build_reply(peer, request))
        current_body = session.ensure_built()
        sent.result(timeout=3)
    assert current_body["execution_id"] == next_id
    assert receipt.from_worker(
        session, current_body, control_plane=receipt.CONTROL_PLANE_NATIVE, grant=None
    ).captured_inputs == inputdeclaration.public_fact(second)


def test_pending_build_serializes_continue_on_existing_figure_lock(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    first = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, first)
    original_lock = session._figure_lock
    first_lock, second_lock = threading.Event(), threading.Event()

    class ObservedLock:
        def __init__(self):
            self.count = 0
            self.counter_lock = threading.Lock()

        def __enter__(self):
            with self.counter_lock:
                self.count += 1
                (first_lock if self.count == 1 else second_lock).set()
            original_lock.acquire()

        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(session, "_figure_lock", ObservedLock())
    monkeypatch.setattr(nativesession, "BUILD_TIMEOUT", 3)
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 3)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        building = executor.submit(session.ensure_built)
        assert first_lock.wait(3)
        with peer.makefile("rb") as stream:
            request = json.loads(stream.readline())
            assert request["cmd"] == "build"
            continuing = executor.submit(session.resume)
            assert second_lock.wait(3)  # The actual resume actor has reached acquire.
            assert not continuing.done()
            assert not select.select([peer], [], [], 0)[0]
            _build_reply(peer, request)
            body = building.result(timeout=3)
            assert body["execution_id"] == first["execution_id"]
            request = json.loads(stream.readline())
            assert request["cmd"] == "continue"
            _reply(
                peer,
                request,
                ok=True,
                released=True,
                execution_id=executionfact.new_id(),
                script=SCRIPT,
            )
            continuing.result(timeout=3)
    finally:
        session.transport.close()
        executor.shutdown(wait=True, cancel_futures=True)


def test_eof_after_positive_getter_cannot_stamp_old_build_response(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    monkeypatch.setattr(nativesession, "BUILD_TIMEOUT", 3)
    _barrier(session, peer, _fact(tmp_path, session.execution_id))
    read = session.captured_inputs_for_receipt

    def close_after_read(*, script):
        fact = read(script=script)
        assert fact is not None
        peer.shutdown(socket.SHUT_WR)
        session.transport.join_reader(3)
        return fact

    monkeypatch.setattr(session, "captured_inputs_for_receipt", close_after_read)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: _build_reply(peer, request))
        body = session.ensure_built()
        sent.result(timeout=3)
    assert "execution_id" not in body
    assert session.state == nativesession.ENDED


def test_default_build_keeps_original_body_and_request_call_shape(connected, monkeypatch):
    session, peer = connected
    _barrier(session, peer)
    body = {"runtime": {"report_origin": "build", "pid": 4242}, "stems": {"中文": {}}}
    wire = json.dumps(body, ensure_ascii=False)
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return body

    monkeypatch.setattr(session, "_request", request)
    assert session.ensure_built() is body
    assert session.last_build is body
    assert calls == [(({"cmd": "build"}, nativesession.BUILD_TIMEOUT), {})]
    assert json.dumps(body, ensure_ascii=False) == wire
