"""A native target starts at compiled-code exec, not spawn/hello/preflight.

Module resolution can execute parent-package imports. Those side effects do not
claim the requested target's owner: only that target's compiled code crossing
its exec boundary emits execution_started. This limitation is deliberate.
"""

from __future__ import annotations

import errno
import json
import socket
import subprocess
import sys

import pytest

from support import desktopkit, nativekit
from tavotto.engine import bridge, desktopproc, executionfact, nativesession, pool, runcodes
from tavotto.engine.runcodes import RunError


def _run(
    project,
    target,
    *,
    kind="script",
    argv=(),
    python=None,
    continue_barriers=False,
    expected_abrupt_exit=None,
):
    """Real runner and authenticated-shaped control handshake; no scientific stack."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(15)
        port = listener.getsockname()[1]
        command = [
            python or sys.executable,
            str(bridge.RUNNER_PY),
            "--target-kind",
            kind,
            "--target",
            target,
            "--project-root",
            str(project),
            "--out-dir",
            str(project / "runner-output"),
            "--control-port",
            str(port),
            "--",
            *argv,
        ]
        with subprocess.Popen(
            command, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        ) as process:
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(15)
                    with connection.makefile("rb") as stream:
                        hello = json.loads(stream.readline())
                        assert hello["bridge_hello"] == 1
                        connection.sendall(b'{"ok":true}\n')
                        events = []
                        while True:
                            try:
                                line = next(stream)
                            except StopIteration:
                                break
                            except ConnectionResetError:
                                # os._exit can end the Windows socket with RST,
                                # not EOF. Only the explicit abrupt-exit probe
                                # permits this; keep every frame actually read.
                                if expected_abrupt_exit is None:
                                    raise
                                break
                            frame = json.loads(line)
                            events.append(frame)
                            if continue_barriers and frame.get("bridge_event") == "barrier":
                                connection.sendall(
                                    json.dumps(
                                        {
                                            "protocol_version": 1,
                                            "request_id": f"continue-{len(events)}",
                                            "cmd": "continue",
                                            "payload": {},
                                        }
                                    ).encode()
                                    + b"\n"
                                )
                stdout, stderr = process.communicate(timeout=15)
                if expected_abrupt_exit is not None:
                    assert process.returncode == expected_abrupt_exit, (
                        f"expected abrupt exit {expected_abrupt_exit}, got {process.returncode}"
                    )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=15)
        return process.returncode, events, stdout.decode(), stderr.decode()


def _starts(events):
    return [item for item in events if item.get("bridge_event") == "execution_started"]


@pytest.mark.parametrize(
    "source,exit_code", [("pass\n", 0), ("raise RuntimeError('after-start')\n", 1)]
)
def test_real_script_zero_outputs_and_runtime_failure_still_emit_actual_start(
    tmp_path, source, exit_code
):
    (tmp_path / "plot.py").write_text(source, encoding="utf-8")
    code, events, _, _ = _run(tmp_path, "plot.py", argv=("--secret", "do-not-publish"))
    assert code == exit_code
    [start] = _starts(events)
    assert set(start) == {"bridge_event", "execution_id", "script"}
    assert executionfact.valid_id(start["execution_id"])
    assert executionfact.started_ns(start["execution_id"]) > 0
    assert start["script"] == "plot.py"
    assert events[0] == start and events[-1]["bridge_event"] == "exit"
    assert "do-not-publish" not in json.dumps(events)


@pytest.mark.parametrize("source", ["if broken syntax\n", None])
def test_script_compile_or_open_failure_never_emits_start(tmp_path, source):
    if source is not None:
        (tmp_path / "plot.py").write_text(source, encoding="utf-8")
    code, events, _, _ = _run(tmp_path, "plot.py")
    assert code == 1
    assert _starts(events) == []
    assert events[-1]["bridge_event"] == "exit"


def _inject_terminal_control_error(monkeypatch, error):
    """Replace only the real socket's EOF/RST, never its received event bytes."""
    readinto = socket.SocketIO.readinto
    injected = []

    def fail_at_end(stream, buffer):
        try:
            size = readinto(stream, buffer)
        except ConnectionResetError:
            size = 0
        if size == 0:
            injected.append(error)
            raise error
        return size

    monkeypatch.setattr(socket.SocketIO, "readinto", fail_at_end)
    return injected


@pytest.mark.parametrize("inject_reset", [False, True])
def test_start_event_is_before_first_user_statement(tmp_path, monkeypatch, inject_reset):
    # The first statement forces process exit without runner cleanup. Only a
    # pre-exec event can reach the controller in this case.
    (tmp_path / "plot.py").write_text("import os\nos._exit(17)\n", encoding="utf-8")
    if inject_reset:
        injected = _inject_terminal_control_error(
            monkeypatch, ConnectionResetError(errno.ECONNRESET, "injected terminal reset")
        )
    code, events, _, _ = _run(tmp_path, "plot.py", expected_abrupt_exit=17)
    if inject_reset:
        assert len(injected) == 1
    assert code == 17
    [start] = _starts(events)
    assert executionfact.valid_id(start["execution_id"])
    assert start["script"] == "plot.py"
    assert events == [start]


@pytest.mark.parametrize(
    "error_type,expected_abrupt_exit",
    [
        (ConnectionResetError, None),
        (ConnectionAbortedError, 17),
        (BrokenPipeError, 17),
        (TimeoutError, 17),
        (OSError, 17),
    ],
)
def test_runner_fixture_rejects_unexpected_control_errors(
    tmp_path, monkeypatch, error_type, expected_abrupt_exit
):
    source = "pass\n" if expected_abrupt_exit is None else "import os\nos._exit(17)\n"
    (tmp_path / "plot.py").write_text(source, encoding="utf-8")
    error = error_type("injected terminal error")
    injected = _inject_terminal_control_error(monkeypatch, error)
    with pytest.raises(error_type) as caught:
        _run(tmp_path, "plot.py", expected_abrupt_exit=expected_abrupt_exit)
    assert caught.value is error and injected == [error]


def test_runner_fixture_reset_requires_the_real_expected_exit_code(tmp_path, monkeypatch):
    (tmp_path / "plot.py").write_text("import os\nos._exit(18)\n", encoding="utf-8")
    error = ConnectionResetError(errno.ECONNRESET, "injected terminal reset")
    injected = _inject_terminal_control_error(monkeypatch, error)
    with pytest.raises(AssertionError, match="expected abrupt exit 17, got 18"):
        _run(tmp_path, "plot.py", expected_abrupt_exit=17)
    assert injected == [error]


PROBE = """import json, sys
print(json.dumps({
    'argv': sys.argv, 'name': __name__, 'package': __package__,
    'spec': [__spec__.name, __spec__.origin, __spec__.parent],
    'file': __file__, 'path0': sys.path[0],
    'main_is_me': sys.modules['__main__'].__dict__ is globals(),
}))
"""


@pytest.mark.parametrize("target,file", [("pkg.plot", "plot.py"), ("pkg", "__main__.py")])
def test_actual_start_module_keeps_real_python_invocation_semantics(tmp_path, target, file):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / file).write_text(PROBE, encoding="utf-8")
    direct = subprocess.run(
        [sys.executable, "-m", target, "A", "arg with spaces"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    code, events, stdout, stderr = _run(
        tmp_path, target, kind="module", argv=("A", "arg with spaces")
    )
    assert code == 0, stderr
    assert json.loads(stdout) == json.loads(direct.stdout)
    [start] = _starts(events)
    assert start["script"] == "pkg/" + file
    assert executionfact.valid_id(start["execution_id"])


@pytest.mark.parametrize("failure", ["syntax", "missing", "parent"])
def test_module_resolution_or_syntax_failure_does_not_claim_target_started(tmp_path, failure):
    package = tmp_path / "pkg"
    package.mkdir()
    init = "from pathlib import Path\nPath('parent-imported').write_text('yes')\n"
    if failure == "parent":
        init += "raise RuntimeError('parent failed before target')\n"
    (package / "__init__.py").write_text(init, encoding="utf-8")
    if failure != "missing":
        (package / "plot.py").write_text("if broken syntax\n", encoding="utf-8")
    code, events, _, _ = _run(tmp_path, "pkg.plot", kind="module")
    assert code == 1
    assert (tmp_path / "parent-imported").read_text() == "yes"
    assert _starts(events) == []


@nativekit.needs_user_python
@pytest.mark.parametrize("valid_target", [False, True])
def test_parent_import_barrier_release_cannot_claim_or_swallow_target_start(tmp_path, valid_target):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        desktopkit.FIGURE_HEADER + "plt.subplots()\nplt.show()\n", encoding="utf-8"
    )
    (package / "plot.py").write_text("pass\n" if valid_target else "if syntax error\n")
    code, events, _, stderr = _run(
        tmp_path,
        "pkg.plot",
        kind="module",
        python=nativekit.USER_PYTHON,
        continue_barriers=True,
    )
    assert code == (0 if valid_target else 1), stderr
    assert events[0]["bridge_event"] == "barrier"
    release = next(item for item in events if item.get("bridge_event") == "released")
    assert "execution_id" not in release
    response = next(item for item in events if item.get("released"))
    assert "execution_id" not in response
    starts = _starts(events)
    if valid_target:
        assert len(starts) == 1 and starts[0]["script"] == "pkg/plot.py"
        assert events.index(starts[0]) > events.index(release)
    else:
        assert starts == []


def test_external_target_execution_does_not_fabricate_project_owner(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "outside.py"
    external.write_text("pass\n", encoding="utf-8")
    code, events, _, _ = _run(project, str(external))
    assert code == 0
    [start] = _starts(events)
    assert executionfact.valid_id(start["execution_id"])
    assert start["script"] is None
    assert str(external) not in json.dumps(events)


def test_execution_fact_bootstrap_does_not_shadow_user_module(tmp_path):
    (tmp_path / "executionfact.py").write_text("USER_VALUE = 'user-owned'\n", encoding="utf-8")
    (tmp_path / "plot.py").write_text(
        "import executionfact\nassert executionfact.USER_VALUE == 'user-owned'\n", encoding="utf-8"
    )
    code, events, _, stderr = _run(tmp_path, "plot.py")
    assert code == 0, stderr
    assert len(_starts(events)) == 1


def _session(tmp_path, name="session"):
    session = nativesession.NativeSession(
        session_id=name,
        descriptor={
            "out_dir": str(tmp_path / "out"),
            "metadata": {
                "project_root": str(tmp_path),
                "interpreter": sys.executable,
                "target_kind": "script",
                "target_display": "plot.py",
                "arg_count": 2,
            },
        },
    )
    session._set_state(nativesession.STARTING_PYTHON)
    return session


def _start_frame(**fields):
    return {
        "bridge_event": "execution_started",
        "execution_id": executionfact.new_id(),
        "script": "plot.py",
        **fields,
    }


def test_native_session_records_start_before_observer_and_preserves_barrier_id(tmp_path):
    session = _session(tmp_path)
    session.note_hello({"pid": 42})
    assert session.execution_id is None
    seen = []
    session._on_change = lambda current, event: seen.append(
        (current.execution_id, current.execution_script, current.state, event)
    )
    frame = _start_frame()
    session._on_frame(frame)
    assert session.execution_id == frame["execution_id"]
    assert session.execution_script == "plot.py"
    assert session.execution_started_ns == executionfact.started_ns(frame["execution_id"])
    assert seen[0][:3] == (frame["execution_id"], "plot.py", nativesession.RUNNING_SCRIPT)
    assert seen[0][3]["execution_started"] is True
    session._on_frame(frame)
    session._on_frame(_start_frame())
    assert len(seen) == 1  # Duplicate starts never manufacture a newer segment.
    session._on_frame({"bridge_event": "barrier", "stems": []})
    assert session.execution_id == frame["execution_id"]
    # Old bridge releases remain compatible but cannot fabricate a new fact.
    session._on_frame({"bridge_event": "released"})
    assert session.execution_id == frame["execution_id"]
    assert session.snapshot_epoch == 2


def test_each_trusted_release_advances_execution_before_notifying_observer(tmp_path):
    session = _session(tmp_path)
    session._on_frame(_start_frame())
    seen = []
    session._on_change = lambda current, event: seen.append(current.execution_id)
    for _ in range(2):
        previous = session.execution_id
        session._on_frame({"bridge_event": "barrier", "stems": []})
        assert session.execution_id == previous
        frame = _start_frame(bridge_event="released")
        session._on_frame(frame)
        assert session.execution_id == frame["execution_id"] != previous
        assert seen[-1] == frame["execution_id"]
        assert session.state == nativesession.RUNNING_SCRIPT
        session._on_frame(frame)
        assert session.execution_id == frame["execution_id"]


@pytest.mark.parametrize("failure", ["sidecar", "runner", "none"])
def test_continue_never_creates_execution_fact_from_response_alone(tmp_path, monkeypatch, failure):
    session = _session(tmp_path)
    session._on_frame(_start_frame())
    session._on_frame({"bridge_event": "barrier", "stems": ["B"]})
    identity, epoch = session.execution_id, session.snapshot_epoch
    sent = []

    def request(*args):
        sent.append(args)
        if failure == "runner":
            error = pool.WorkerError("not restored")
            error.code = runcodes.NATIVE_FIGURE_INCONSISTENT
            raise error
        return {"ok": True, "released": True}

    monkeypatch.setattr(session, "_request", request)
    if failure == "sidecar":
        session.inconsistent["B"] = {"unrestored": 1}
    if failure != "none":
        with pytest.raises(RunError):
            session.resume()
        assert session.state == nativesession.BARRIER and session.snapshot_epoch == epoch
    else:
        session.resume()
        assert session.snapshot_epoch == epoch + 1
        session._on_frame(_start_frame(bridge_event="released"))
        assert session.execution_id != identity
        return
    assert session.execution_id == identity
    assert bool(sent) == (failure == "runner")


@pytest.mark.parametrize("change", ["identity", "script", "stale"])
def test_release_rejects_invalid_or_stale_execution_identity(tmp_path, change):
    session = _session(tmp_path)
    stale_id = executionfact.new_id()
    session._on_frame(_start_frame())
    session._on_frame({"bridge_event": "barrier", "stems": []})
    original, sequence = session.execution_id, session.sequence
    frame = _start_frame(bridge_event="released")
    if change == "identity":
        frame["execution_id"] = "not-valid"
    elif change == "script":
        frame["script"] = "other.py"
    else:
        frame["execution_id"] = stale_id
    session._on_frame(frame)
    assert session.execution_id == original
    assert session.sequence == sequence and session.state == nativesession.BARRIER


@pytest.mark.parametrize("event_first", [False, True])
@pytest.mark.parametrize("action", ["resume", "detach"])
def test_release_response_and_event_share_fact_in_either_order(
    tmp_path, monkeypatch, action, event_first
):
    session = _session(tmp_path)
    session._on_frame(_start_frame())
    session._on_frame({"bridge_event": "barrier", "stems": []})
    previous = session.execution_id
    frame = _start_frame(bridge_event="released")

    def request(*args):
        if event_first:
            session._on_frame(frame)
        return {"ok": True, "released": True, **{k: frame[k] for k in ("execution_id", "script")}}

    monkeypatch.setattr(session, "_request", request)
    seen = []
    session._on_change = lambda current, event: seen.append(current.execution_id)
    getattr(session, action)()
    assert session.execution_id == frame["execution_id"] != previous
    assert seen[-1] == frame["execution_id"]
    session._on_frame(frame)
    assert session.execution_id == frame["execution_id"]
    if action == "detach":
        assert session.state == nativesession.DETACHED


@pytest.mark.parametrize(
    "fields",
    [
        {"execution_id": None},
        {"execution_id": "a" * 47},
        {"execution_id": "A" * 48},
        {"script": "/private/plot.py"},
        {"script": "../plot.py"},
        {"script": "./plot.py"},
        {"script": "dir\\plot.py"},
        {"script": "a//plot.py"},
        {"script": "bad\0.py"},
        {"script": []},
        {"script": "x" * 16385},
    ],
)
def test_native_session_ignores_invalid_start_provenance(tmp_path, fields):
    session = _session(tmp_path)
    sequence = session.sequence
    session._on_frame(_start_frame(**fields))
    assert session.execution_id is None
    assert session.sequence == sequence


@pytest.mark.parametrize(
    "state",
    [nativesession.ENDED, nativesession.DETACHED, nativesession.FAILED, nativesession.BARRIER],
)
def test_late_start_cannot_reactivate_terminal_or_barrier_session(tmp_path, state):
    session = _session(tmp_path)
    session._set_state(state)
    session._on_frame(_start_frame())
    assert session.execution_id is None and session.state == state


def test_public_state_only_exposes_application_invalidation_not_launch_identity(tmp_path):
    session = _session(tmp_path)
    session._on_frame(_start_frame())
    state = session.public_state()
    assert "execution_id" not in state and "execution_script" not in state
    assert "figure_snapshot_invalidation" not in state
    session.figure_snapshot_invalidation = {"version": 1, "source_ids": ["source-B"]}
    state = session.public_state()
    assert state["figure_snapshot_invalidation"] == session.figure_snapshot_invalidation
    state["figure_snapshot_invalidation"]["source_ids"].append("changed")
    assert session.figure_snapshot_invalidation["source_ids"] == ["source-B"]
    assert "figure_snapshot_invalidation" not in session.diagnostics()


def test_registry_route_conflict_is_exact_alive_and_never_takes_over(tmp_path):
    registry = nativesession.NativeSessionRegistry()
    holder = _session(tmp_path, "holder")
    holder.transport = object()  # Only alive() is needed; no socket or process is owned.
    holder.descriptors = [{"script": "plot.py", "stem": "B"}]
    registry.adopt(holder)
    assert registry.bind_assets(holder) == []
    assert registry.script_route_conflict(str(tmp_path), "plot.py")
    assert not registry.script_route_conflict(str(tmp_path), "plot.py", exclude_session_id="holder")
    assert not registry.script_route_conflict(str(tmp_path), "other.py")
    assert not registry.script_route_conflict(str(tmp_path / "other"), "plot.py")
    before = dict(registry._routes)
    newcomer = _session(tmp_path, "newcomer")
    newcomer.transport = object()
    registry.adopt(newcomer)
    assert registry.script_route_conflict(str(tmp_path), "plot.py", exclude_session_id="newcomer")
    assert registry._routes == before
    assert registry.route_for(str(tmp_path), "plot.py", "B") is holder
    # A stale route row must not count after the holder exits, even before
    # registry event cleanup has removed the row.
    holder.state = nativesession.ENDED
    assert not registry.script_route_conflict(str(tmp_path), "plot.py")


def test_registry_route_conflict_never_infers_script_from_opaque_source(tmp_path):
    registry = nativesession.NativeSessionRegistry()
    holder = _session(tmp_path)
    holder.transport = object()
    holder.descriptors = [{"script": "plots/#a~b.py", "stem": "name#with~delimiters"}]
    registry.adopt(holder)
    registry.bind_assets(holder)
    assert registry.script_route_conflict(str(tmp_path), "plots/#a~b.py")
    assert not registry.script_route_conflict(str(tmp_path), "plots/#a.py")
    with pytest.raises(ValueError):
        registry.script_route_conflict(str(tmp_path), "../plots/#a~b.py")


def test_execution_script_keeps_in_project_alias_consistent_with_descriptors(tmp_path):
    source = tmp_path / "real.py"
    source.write_text("pass\n", encoding="utf-8")
    alias = tmp_path / "alias.py"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("symlinks unavailable")
    code, events, _, _ = _run(tmp_path, "alias.py")
    assert code == 0
    assert _starts(events)[0]["script"] == "alias.py"


@pytest.mark.parametrize("source", ["pass\n", "raise RuntimeError('after-start')\n"])
def test_owned_zero_output_start_reaches_observer_even_when_exit_precedes_adoption(
    tmp_path, source
):
    registry = nativesession.NativeSessionRegistry()
    seen = []
    registry.on_change = lambda current, event: seen.append(current.execution_id)
    session = desktopproc.start(
        desktopkit.launch_for(tmp_path, source, interpreter=sys.executable), registry=registry
    )
    try:
        desktopkit.wait_until(lambda: session.owned.finished)
        assert executionfact.valid_id(session.execution_id)
        assert session.execution_id in seen
        assert session.execution_script == "figure.py"
    finally:
        if not session.owned.finished:
            session.cancel_owned()
        session.shutdown()


@nativekit.needs_user_python
def test_real_two_barrier_releases_each_have_new_execution_identity(tmp_path):
    registry = nativesession.NativeSessionRegistry()
    observed = []
    registry.on_change = lambda current, event: observed.append(
        (current.state, current.execution_id, current.execution_script)
    )
    session = desktopproc.start(
        desktopkit.launch_for(
            tmp_path,
            desktopkit.FIGURE_HEADER
            + "fig, ax = plt.subplots()\nplt.show()\nax.set_title('next')\nplt.show()\n",
        ),
        registry=registry,
    )
    try:
        identities = []
        for _ in range(2):
            nativekit.wait_state(session, [nativesession.BARRIER])
            identities.append(session.execution_id)
            assert executionfact.valid_id(identities[-1])
            session.ensure_built()
            assert session.execution_id == identities[-1]
            response = session.resume()
            desktopkit.wait_until(lambda: session.execution_id != identities[-1])
            assert response["execution_id"] == session.execution_id
            assert response["script"] == "figure.py"
        assert identities[0] != identities[1]
        assert executionfact.started_ns(identities[0]) < executionfact.started_ns(identities[1])
        assert any(
            state in {nativesession.CONTINUING, nativesession.RUNNING_SCRIPT}
            and identity == session.execution_id
            for state, identity, _ in observed
        )
        assert all(script == "figure.py" for _, identity, script in observed if identity)
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()
        session.shutdown()


@nativekit.needs_user_python
def test_real_continue_response_and_released_event_have_identical_execution_fact(tmp_path):
    (tmp_path / "plot.py").write_text(
        desktopkit.FIGURE_HEADER + "plt.subplots()\nplt.show()\nplt.show()\n", encoding="utf-8"
    )
    code, frames, _, stderr = _run(
        tmp_path, "plot.py", python=nativekit.USER_PYTHON, continue_barriers=True
    )
    assert code == 0, stderr
    previous = _starts(frames)[0]["execution_id"]
    response = None
    releases = 0
    for frame in frames:
        if frame.get("released"):
            assert frame["execution_id"] != previous
            response = frame
        elif frame.get("bridge_event") == "released":
            assert response is not None
            assert frame["execution_id"] == response["execution_id"]
            assert frame["script"] == response["script"] == "plot.py"
            assert executionfact.started_ns(frame["execution_id"]) > executionfact.started_ns(
                previous
            )
            previous = frame["execution_id"]
            response = None
            releases += 1
    assert releases >= 2 and response is None
