"""Native archive bytes belong to one actual capture and one existing barrier epoch."""

from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
import socket
import threading

import pytest
from test_native_declared_inputs import (
    SCRIPT,
    _barrier,
    _event,
    _fact,
    _reply,
    _serve,
    connected as connected,
)

from support import desktopkit, nativekit
from tavotto.engine import (
    declaredinputs,
    desktopproc,
    execspec,
    executionfact,
    inputdeclaration,
    nativesession,
)
from tavotto.engine.runcodes import RunError

LIMITS = {"max_files": 100, "max_bytes": 1024 * 1024}


def _descriptor(fact, *, key="ab" * 24):
    # Socket tests exercise metadata ownership, never pretend these are verified bytes.
    return {"version": 1, "key": key, "revision": fact["revision"]}


def _archive(session):
    return session.captured_input_archive_for_receipt(script=SCRIPT)


@pytest.mark.parametrize(
    "bad", ["missing", "null", "future", "revision", "path", "extra", "without_fact"]
)
def test_missing_or_invalid_descriptor_cannot_upgrade_a_valid_barrier(connected, tmp_path, bad):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    descriptor = _descriptor(fact)
    if bad == "future":
        descriptor["version"] = 2
    elif bad == "revision":
        descriptor["revision"] = "sha256:" + "0" * 64
    elif bad == "path":
        descriptor["key"] = "../not-a-parent-approved-path"
    elif bad == "extra":
        descriptor["archive_root"] = str(tmp_path)
    extra = {} if bad == "missing" else {"captured_input_archive": descriptor}
    if bad == "null":
        extra["captured_input_archive"] = None
    _barrier(session, peer, None if bad == "without_fact" else fact, **extra)
    assert _archive(session) is None
    assert session.captured_inputs_for_receipt(script=SCRIPT) == (
        None if bad == "without_fact" else fact
    )


def test_metadata_getters_share_the_sealed_slot_without_reading_archive_paths(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    descriptor = _descriptor(fact)
    _barrier(session, peer, fact, captured_input_archive=descriptor)

    def forbidden(*args, **kwargs):
        raise AssertionError("Session getter must never verify archive bytes or current files")

    monkeypatch.setattr(inputdeclaration, "verify_archive", forbidden)
    (tmp_path / "private-input.csv").unlink()
    assert session.captured_inputs_for_receipt(script=SCRIPT) == fact
    assert _archive(session) == descriptor
    assert session.captured_input_archive_for_receipt(script="other.py") is None
    returned = _archive(session)
    returned["key"] = "cd" * 24
    assert _archive(session) == descriptor
    public = json.dumps([session.public_state(), list(session.events)])
    assert "captured_input_archive" not in public
    assert descriptor["key"] not in public


def test_legacy_three_member_private_slot_has_no_archive(connected, tmp_path):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    session._barrier_captured_inputs = session._barrier_captured_inputs[:3]
    assert session.captured_inputs_for_receipt(script=SCRIPT) == fact
    assert _archive(session) is None


@pytest.mark.parametrize("had_archive", [False, True])
def test_duplicate_barrier_cannot_supplement_or_replace_its_archive(
    connected, tmp_path, had_archive
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    first = _descriptor(fact)
    _barrier(session, peer, fact, **({"captured_input_archive": first} if had_archive else {}))
    epoch = session.snapshot_epoch
    _barrier(session, peer, fact, captured_input_archive=_descriptor(fact, key="cd" * 24))
    assert session.snapshot_epoch == epoch
    assert _archive(session) == (first if had_archive else None)


def test_build_reply_cannot_add_archive_to_an_existing_barrier(connected, tmp_path, monkeypatch):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact)
    monkeypatch.setattr(nativesession, "BUILD_TIMEOUT", 3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(
            _serve,
            peer,
            lambda request: _reply(
                peer, request, ok=True, captured_input_archive=_descriptor(fact)
            ),
        )
        body = session.ensure_built()
        sent.result(timeout=3)
    assert body["execution_id"] == fact["execution_id"]
    assert _archive(session) is None


@pytest.mark.parametrize("new_archive", [False, True])
def test_late_continue_reply_preserves_only_the_new_barriers_archive(
    connected, tmp_path, monkeypatch, new_archive
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    first = _descriptor(fact)
    _barrier(session, peer, fact, captured_input_archive=first)
    old_copy = _archive(session)
    second = _fact(tmp_path, executionfact.new_id())
    descriptor = _descriptor(second, key="cd" * 24)
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 3)

    def respond(request):
        _event(
            session,
            peer,
            {
                "bridge_event": "released",
                "execution_id": second["execution_id"],
                "script": SCRIPT,
            },
        )
        assert _archive(session) is None
        _barrier(
            session,
            peer,
            second,
            **({"captured_input_archive": descriptor} if new_archive else {}),
        )
        _reply(
            peer,
            request,
            ok=True,
            released=True,
            execution_id=second["execution_id"],
            script=SCRIPT,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, respond)
        session.resume()
        sent.result(timeout=3)
    assert session.captured_inputs_for_receipt(script=SCRIPT) == second
    assert _archive(session) == (descriptor if new_archive else None)
    assert old_copy == first  # A historical copy is not current eligibility.


def test_unknown_continue_revokes_archive_even_without_a_new_execution_id(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact, captured_input_archive=_descriptor(fact))
    monkeypatch.setattr(nativesession, "REQUEST_TIMEOUT", 0.1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(_serve, peer, lambda request: None)
        with pytest.raises(RunError):
            session.resume()
        sent.result(timeout=3)
    assert session.execution_id == fact["execution_id"]
    assert _archive(session) is None


def test_eof_revokes_archive_before_terminal_state_publication(connected, tmp_path, monkeypatch):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    _barrier(session, peer, fact, captured_input_archive=_descriptor(fact))
    observed = []
    release = session._release_lease

    def release_lease():
        observed.append((session.state, _archive(session)))
        release()

    monkeypatch.setattr(session, "_release_lease", release_lease)
    peer.shutdown(socket.SHUT_WR)
    session.transport.join_reader(3)
    assert observed == [(nativesession.BARRIER, None)]


def test_eof_during_descriptor_read_preserves_copy_but_revokes_next_read(
    connected, tmp_path, monkeypatch
):
    session, peer = connected
    fact = _fact(tmp_path, session.execution_id)
    descriptor = _descriptor(fact)
    _barrier(session, peer, fact, captured_input_archive=descriptor)
    copied, allow_copy, closing = (threading.Event() for _ in range(3))
    original_copy = copy.deepcopy
    original_close = session.transport._on_close
    armed = True

    def copy_descriptor(value, *args, **kwargs):
        nonlocal armed
        if armed and value == descriptor:
            armed = False
            copied.set()
            assert allow_copy.wait(3)
        return original_copy(value, *args, **kwargs)

    def close(reason):
        closing.set()  # Real socket reader reached EOF before the getter returns.
        original_close(reason)

    monkeypatch.setattr(nativesession.copy, "deepcopy", copy_descriptor)
    monkeypatch.setattr(session.transport, "_on_close", close)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        reading = executor.submit(_archive, session)
        assert copied.wait(3)
        peer.shutdown(socket.SHUT_WR)
        assert closing.wait(3)
        allow_copy.set()
        historical = reading.result(timeout=3)
    finally:
        allow_copy.set()
        executor.shutdown(wait=True, cancel_futures=True)
    session.transport.join_reader(3)
    assert historical == descriptor
    assert _archive(session) is None
    assert session.captured_inputs_for_receipt(script=SCRIPT) is None


NATIVE_SOURCE = (
    desktopkit.FIGURE_HEADER
    + """from pathlib import Path
data = Path('data.csv').read_bytes()
fig, ax = plt.subplots(figsize=(2, 1))
ax.plot([0, 1], [0, int(data.split(b',')[1])])
plt.show()
ax.set_title('continued')
plt.show()
"""
)


@pytest.fixture
def owned_project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "isolated-state"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    root = tmp_path / "中文项目"
    (root / SCRIPT).parent.mkdir(parents=True)
    (root / SCRIPT).write_bytes(NATIVE_SOURCE.encode("utf-8"))
    (root / "data.csv").write_bytes(b"1,2\n")
    return root


def _launch(root, *, declared=True):
    if declared:
        declaredinputs.declare(
            root,
            script=SCRIPT,
            cwd_mode="project_root",
            run_config=None,
            files=["data.csv"],
            remaps=[],
            expected_revision=declaredinputs.state(root)["revision"],
        )
    selection = declaredinputs.scope_selection(root, script=SCRIPT, cwd_mode="project_root")
    spec = execspec.desktop_native_spec(
        str(root / SCRIPT),
        interpreter=nativekit.USER_PYTHON,
        cwd=str(root),
        project_root=str(root),
    )
    return desktopproc.start(
        desktopproc.OwnedLaunch(spec, policy=desktopkit.FAST, input_declaration_state=selection)
    )


def _verified_bytes(session, descriptor, fact):
    archived = inputdeclaration.verify_archive(
        descriptor,
        fact,
        archive_root=session.out_dir / inputdeclaration.ARCHIVE_DIRNAME,
        limits=LIMITS,
    )
    assert archived is not None
    actual = {}
    for resource in archived.resources:
        data = resource.path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == resource.sha256
        assert len(data) == resource.size_bytes
        actual[resource.sha256] = data
    return actual


@nativekit.needs_user_python
def test_actual_barrier_archive_keeps_original_csv_after_source_deletion(owned_project):
    source = (owned_project / SCRIPT).read_bytes()
    session = _launch(owned_project)
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        fact = session.captured_inputs_for_receipt(script=SCRIPT)
        descriptor = _archive(session)
        assert fact is not None and descriptor is not None
        assert _verified_bytes(session, descriptor, fact) == {
            hashlib.sha256(source).hexdigest(): source,
            hashlib.sha256(b"1,2\n").hexdigest(): b"1,2\n",
        }
        (owned_project / "data.csv").unlink()
        assert _archive(session) == descriptor
        assert _verified_bytes(session, descriptor, fact)[hashlib.sha256(b"1,2\n").hexdigest()] == (
            b"1,2\n"
        )
        assert session.ensure_built()["execution_id"] == fact["execution_id"]
        assert "<svg" in session.override(session.stems[0], [], inline_svg=True)["svg"]
        session.resume()
        nativekit.wait_state(session, [nativesession.BARRIER])
        assert session.execution_id != fact["execution_id"]
        assert session.captured_inputs_for_receipt(script=SCRIPT) is None
        assert _archive(session) is None
        # The old bytes remain immutable, but cannot be current session evidence.
        assert (
            _verified_bytes(session, descriptor, fact)[hashlib.sha256(source).hexdigest()] == source
        )
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()


@nativekit.needs_user_python
def test_actual_two_shows_reuse_verified_archive_in_one_unchanged_lineage(owned_project):
    session = _launch(owned_project)
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        first = session.captured_inputs_for_receipt(script=SCRIPT)
        old_archive = _archive(session)
        first_bytes = _verified_bytes(session, old_archive, first)
        session.resume()
        nativekit.wait_state(session, [nativesession.BARRIER])
        second = session.captured_inputs_for_receipt(script=SCRIPT)
        new_archive = _archive(session)
        assert first["execution_id"] != second["execution_id"]
        assert first["revision"] == second["revision"]
        assert old_archive == new_archive
        assert _verified_bytes(session, new_archive, second) == first_bytes
        assert _verified_bytes(session, old_archive, first) == first_bytes
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()


@nativekit.needs_user_python
def test_actual_no_declaration_does_not_create_archive_or_add_build_fields(owned_project):
    session = _launch(owned_project, declared=False)
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        assert _archive(session) is None
        assert not (session.out_dir / inputdeclaration.ARCHIVE_DIRNAME).exists()
        body = session.ensure_built()
        assert not {"execution_id", "captured_inputs", "captured_input_archive"}.intersection(body)
        assert "<svg" in session.override(session.stems[0], [], inline_svg=True)["svg"]
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()


@nativekit.needs_user_python
def test_actual_archive_failure_keeps_the_hot_native_figure_usable(owned_project):
    path = owned_project / SCRIPT
    gate = """import time
from pathlib import Path
deadline = time.monotonic() + 15
while not Path('go').exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
"""
    path.write_bytes((gate + NATIVE_SOURCE).encode("utf-8"))
    session = _launch(owned_project)
    try:
        (session.out_dir / inputdeclaration.ARCHIVE_DIRNAME).write_bytes(b"not a directory")
        (owned_project / "go").write_bytes(b"continue actual source")
        nativekit.wait_state(session, [nativesession.BARRIER])
        assert session.captured_inputs_for_receipt(script=SCRIPT) is None
        assert _archive(session) is None
        assert "execution_id" not in session.ensure_built()
        assert "<svg" in session.override(session.stems[0], [], inline_svg=True)["svg"]
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()
