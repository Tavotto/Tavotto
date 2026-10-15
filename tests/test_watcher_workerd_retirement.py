"""A watcher-detached workerd session cannot seed the next cold acquisition.

The real pool, watcher and WorkerdWorker constructor run against a small supervisor
double. Its spawn-spec cache and reference counting reproduce the Rust boundary;
no Python child or Rust binary is needed. Events establish ordering, never sleeps.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import (
    discover,
    execspec,
    executionfact,
    inputbroker,
    pool,
    project_watch,
    registry,
    workerd_client,
)


class _Supervisor:
    """Same-spec opens retain cached builds until the exact session is closed."""

    def __init__(self, watcher):
        self.watcher = watcher
        self.opens = []
        self.forces = []
        self.close_responses = []
        self.sessions = {}
        self.by_spec = {}
        self.close_started = threading.Event()
        self.allow_close = threading.Event()
        self.close_threads = []
        self.blocked_session = None
        self.grace_failure = None
        self.force_failure = None
        self._lock = threading.Lock()
        self._next_id = 0

    def _check_close_locks(self):
        # The watcher lock is reentrant: probing from the caller would miss a
        # close accidentally performed inside before_acquire's watcher lock.
        available = []

        def probe():
            for lock in (pool._lock, self.watcher._lock):
                acquired = lock.acquire(blocking=False)
                available.append(acquired)
                if acquired:
                    lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        assert available == [True, True], "force close must run outside pool/watch locks"

    def call(self, op, **kwargs):
        if op == "open_session":
            payload = kwargs["payload"]
            spec = json.dumps(payload, sort_keys=True)
            with self._lock:
                reused = spec in self.by_spec
                if not reused:
                    self._next_id += 1
                    session_id = f"session-{self._next_id}"
                    self.by_spec[spec] = session_id
                    self.sessions[session_id] = {"spec": spec, "refs": 0, "build": None}
                session_id = self.by_spec[spec]
                self.sessions[session_id]["refs"] += 1
                self.opens.append({"payload": payload, "session_id": session_id})
                return {"ok": True, "session_id": session_id, "reused": reused}
        if op == "build":
            with self._lock:
                session = self.sessions[kwargs["session_id"]]
                if session["build"] is None:
                    argv = json.loads(session["spec"])["argv"]
                    source = Path(argv[argv.index("--script") + 1]).read_text(encoding="utf-8")
                    session["build"] = {
                        "ok": True,
                        "stems": ["F"],
                        "descriptors": [{"stem": source}],
                        "execution_id": executionfact.new_id(),
                    }
                return dict(session["build"])
        assert op == "close_session"
        session_id = kwargs["session_id"]
        force = kwargs.get("payload", {}).get("force", False)
        if force:
            self._check_close_locks()
            self.forces.append(session_id)
            assert 0 < kwargs["timeout"] <= pool.SHUTDOWN_TIMEOUT
            if isinstance(self.force_failure, dict):
                return dict(self.force_failure)
            if isinstance(self.force_failure, Exception):
                raise self.force_failure
            if self.force_failure is not None:
                raise workerd_client.WorkerdError(
                    self.force_failure, code="workerd_unavailable", retryable=True
                )
        else:
            self.close_threads.append(threading.current_thread())
            if isinstance(self.grace_failure, dict):
                return dict(self.grace_failure)
            if isinstance(self.grace_failure, Exception):
                raise self.grace_failure
            if session_id == self.blocked_session:
                self.close_started.set()
                assert self.allow_close.wait(30), "test did not release ordinary close"
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None:
                # Rust close is idempotent, including a normal close arriving
                # after a force close has already removed the old session.
                response = {"ok": True, "known": False, "closed": False}
            elif not force and session["refs"] > 1:
                session["refs"] -= 1
                response = {"ok": True, "released": True, "closed": False}
            else:
                del self.by_spec[session["spec"]]
                del self.sessions[session_id]
                response = {"ok": True, "closed": True}
            self.close_responses.append((session_id, force, response))
            return response

    def finish_closes(self):
        self.allow_close.set()
        for thread in self.close_threads:
            if thread is not threading.current_thread():
                thread.join(5)
                assert not thread.is_alive(), "retirement thread leaked"


@pytest.fixture
def retirement(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    scripts = ("fig.py", "other.py", "fresh.py")
    for name in scripts:
        (root / name).write_text("OLD", encoding="utf-8")
    discover.write_config(
        root,
        {
            "version": 1,
            "scripts": {
                name: {"entry": "main", "cost": "light", "notes": "", "stems": [name]}
                for name in scripts
            },
        },
    )
    reg = registry.Registry()
    reg.load(root)
    ctx = SimpleNamespace(path=root, id="retirement-test", registry=reg)
    watcher = project_watch.ProjectWatcher(ctx, debounce=0, clock=lambda: 0)
    watcher.prime()
    supervisor = _Supervisor(watcher)
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {}, raising=False)
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "cache")
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "SPAWN_GATES", [])
    monkeypatch.setattr(pool, "_workdir_gate", lambda *_: None)
    monkeypatch.setattr(pool, "_schedule_prune", lambda: None)
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *_, **__: (sys.executable, pool.SOURCE_SYSTEM)
    )
    monkeypatch.setattr(workerd_client, "find_workerd", lambda: "fake-workerd")
    monkeypatch.setattr(workerd_client, "client", lambda: supervisor)
    monkeypatch.setattr(project_watch, "_watchers", {project_watch._key(root): watcher})
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", project_watch.before_acquire)
    monkeypatch.setattr(inputbroker, "serving", lambda _: contextlib.nullcontext())
    monkeypatch.setattr(inputbroker, "finished", lambda *_, **__: None)
    harness = SimpleNamespace(root=root, watcher=watcher, supervisor=supervisor)
    yield harness
    supervisor.finish_closes()


def _acquire(harness, script="fig.py"):
    worker, created = pool.acquire(script, str(harness.root), "main")
    assert isinstance(worker, pool.WorkerdWorker), "test must exercise the real workerd adapter"
    return worker, created


def _detach_by_poll(harness):
    old, created = _acquire(harness)
    assert created
    assert old.ensure_built()["descriptors"] == [{"stem": "OLD"}]
    harness.supervisor.blocked_session = old._session_id
    # Changed length makes detection deterministic even on coarse-mtime disks.
    (harness.root / "fig.py").write_text("NEW SOURCE", encoding="utf-8")
    harness.watcher.poll()
    assert harness.supervisor.close_started.wait(5)
    key = pool._worker_key(str(harness.root), "fig.py")
    assert key not in pool._workers
    return old, key


def _assert_new_execution(harness, old):
    new, created = _acquire(harness)
    result = new.ensure_built()
    assert created and new is not old
    assert new._session_id != old._session_id
    assert new.generation > old.generation
    assert new.script_sha1 != old.script_sha1
    assert result["descriptors"] == [{"stem": "NEW SOURCE"}]
    assert result["execution_id"] != old.last_build_execution_id
    # Crucial premise: source contents/hash are absent from the spawn key. A
    # changed payload would make this cache regression pass without a fence.
    opens = [
        item for item in harness.supervisor.opens if item["payload"]["label"] == "fig.py::main"
    ]
    assert opens[0]["payload"] == opens[-1]["payload"]
    return new


def _acquire_without_waiting_for_close(harness, script):
    completed = threading.Event()
    result = []
    errors = []

    def acquire():
        try:
            result.append(_acquire(harness, script))
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    thread = threading.Thread(target=acquire, daemon=True)
    thread.start()
    try:
        assert completed.wait(5), "hot reuse waited for an unrelated retiring session"
    finally:
        if not completed.is_set():
            harness.supervisor.allow_close.set()
        thread.join(5)
        assert not thread.is_alive(), "hot acquisition thread leaked"
    if errors:
        raise errors[0]
    return result[0]


def test_poll_detached_worker_is_force_closed_before_same_key_cold_acquire(retirement):
    old, key = _detach_by_poll(retirement)
    new = _assert_new_execution(retirement, old)
    assert retirement.supervisor.forces == [old._session_id]
    assert key not in pool._pending_retirements
    # The late ordinary close must not affect the replacement's session.
    retirement.supervisor.finish_closes()
    assert retirement.supervisor.close_responses[-1] == (
        old._session_id,
        False,
        {"ok": True, "known": False, "closed": False},
    )
    assert new._session_id in retirement.supervisor.sessions
    execution_id = new.last_build_execution_id
    assert new.ensure_built()["execution_id"] == execution_id


@pytest.mark.parametrize(
    "failure",
    [
        "supervisor disconnected",
        "supervisor request timed out",
        {"ok": True, "released": True, "closed": False},
        {"ok": True},
    ],
    ids=["disconnected", "timeout", "released", "unconfirmed-success"],
)
def test_failed_force_close_retains_same_key_fence_until_retry(retirement, failure):
    old, key = _detach_by_poll(retirement)
    supervisor = retirement.supervisor
    supervisor.force_failure = failure
    with pytest.raises(pool.WorkerError) as caught:
        _acquire(retirement)
    assert caught.value.code == "session_dead"
    assert supervisor.forces == [old._session_id]
    assert len(supervisor.opens) == 1
    assert key not in pool._workers
    assert pool._pending_retirements[key] == [old]
    # Neither a failed attempt nor another ordinary watcher round grants a new
    # open. The exact old handle must remain available for a later retry.
    retirement.watcher.poll()
    with pytest.raises(pool.WorkerError) as repeated:
        _acquire(retirement)
    assert repeated.value.code == "session_dead"
    assert len(supervisor.opens) == 1
    supervisor.force_failure = None
    _assert_new_execution(retirement, old)
    assert supervisor.forces == [old._session_id] * 3
    assert key not in pool._pending_retirements


def test_releasing_a_reference_does_not_clear_the_retirement_fence(retirement):
    old, key = _detach_by_poll(retirement)
    supervisor = retirement.supervisor
    # Another owner retained this exact spawn. Graceful close only decrements
    # refs; the adapter being marked dead says nothing about cached child state.
    retained = supervisor.call("open_session", payload=supervisor.opens[0]["payload"])
    assert retained["session_id"] == old._session_id
    supervisor.finish_closes()
    assert supervisor.close_responses[-1][2] == {"ok": True, "released": True, "closed": False}
    assert not old.alive()
    assert pool._pending_retirements[key] == [old]
    _assert_new_execution(retirement, old)
    assert supervisor.forces == [old._session_id]


def test_confirmed_normal_close_allows_cold_acquire_without_force(retirement):
    old, key = _detach_by_poll(retirement)
    retirement.supervisor.finish_closes()
    assert retirement.supervisor.close_responses[-1][2] == {"ok": True, "closed": True}
    assert key not in pool._pending_retirements
    _assert_new_execution(retirement, old)
    assert retirement.supervisor.forces == []


def test_unrelated_hot_reuse_ignores_pending_retirement(retirement, monkeypatch):
    hot, created = _acquire(retirement, "other.py")
    assert created
    hot.ensure_built()
    old, key = _detach_by_poll(retirement)

    def unexpected_watch_sync(*_):
        pytest.fail("a hot acquisition must not settle the watcher")

    monkeypatch.setattr(project_watch, "before_acquire", unexpected_watch_sync)
    reused, created = _acquire_without_waiting_for_close(retirement, "other.py")
    assert reused is hot and not created
    assert retirement.supervisor.forces == []
    assert pool._pending_retirements[key] == [old]
    assert not retirement.supervisor.allow_close.is_set()


def test_unconfirmed_retirements_bound_cold_capacity_but_not_hot_reuse(retirement, monkeypatch):
    hot, _ = _acquire(retirement, "other.py")
    old, key = _detach_by_poll(retirement)
    monkeypatch.setattr(pool, "MAX_ALIVE", 1)
    with pytest.raises(pool.WorkerError) as caught:
        _acquire(retirement, "fresh.py")
    assert caught.value.code == "session_dead"
    assert len(retirement.supervisor.opens) == 2
    assert retirement.supervisor.forces == []
    assert pool._pending_retirements[key] == [old]
    reused, created = _acquire_without_waiting_for_close(retirement, "other.py")
    assert reused is hot and not created
    retirement.supervisor.finish_closes()
    assert key not in pool._pending_retirements
    monkeypatch.setattr(pool, "MAX_ALIVE", 2)
    fresh, created = _acquire(retirement, "fresh.py")
    assert created and fresh is not hot


def test_project_shutdown_force_closes_an_active_released_reference(retirement):
    old, _ = _acquire(retirement)
    assert old.ensure_built()["descriptors"] == [{"stem": "OLD"}]
    supervisor = retirement.supervisor
    retained = supervisor.call("open_session", payload=supervisor.opens[0]["payload"])
    assert retained["session_id"] == old._session_id
    (retirement.root / "fig.py").write_text("NEW SOURCE", encoding="utf-8")

    pool.shutdown_all(str(retirement.root), wait=True)

    assert supervisor.forces == [old._session_id]
    assert old._session_id not in supervisor.sessions
    _assert_new_execution(retirement, old)


@pytest.mark.parametrize("global_shutdown", [False, True])
def test_shutdown_drains_watcher_detached_retirements(retirement, monkeypatch, global_shutdown):
    old, key = _detach_by_poll(retirement)
    monkeypatch.setattr(workerd_client, "reset_client", lambda: None)

    pool.shutdown_all(None if global_shutdown else str(retirement.root), wait=True)

    assert retirement.supervisor.forces == [old._session_id]
    assert old._session_id not in retirement.supervisor.sessions
    assert key not in pool._pending_retirements
    new = _assert_new_execution(retirement, old)
    retirement.supervisor.finish_closes()
    assert new._session_id in retirement.supervisor.sessions


def _shutdown_and_wait(monkeypatch, root, *, wait):
    """Observe the real synchronous/background finalizer without replacing it."""
    completed = threading.Event()
    finish = pool._finish_shutdown

    def observe(closing):
        try:
            finish(closing)
        finally:
            completed.set()

    with monkeypatch.context() as patch:
        patch.setattr(pool, "_finish_shutdown", observe)
        pool.shutdown_all(root, wait=wait)
        assert completed.wait(5), "shutdown cleanup did not complete"


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize(
    "failure",
    [
        {"ok": True, "closed": False},
        {"ok": True},
        workerd_client.WorkerdError("close timed out", code="worker_timeout"),
        RuntimeError("unexpected close failure"),
    ],
    ids=["closed-false", "unconfirmed-success", "workerd-error", "unexpected-error"],
)
def test_shutdown_escalates_finished_but_unconfirmed_graceful_close(
    retirement, monkeypatch, wait, failure
):
    old, _ = _acquire(retirement)
    key = pool._worker_key(str(retirement.root), "fig.py")
    retirement.supervisor.grace_failure = failure

    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)

    assert retirement.supervisor.forces == [old._session_id]
    assert old._session_id not in retirement.supervisor.sessions
    assert key not in pool._workers
    assert key not in pool._pending_retirements


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("detached", [False, True], ids=["active", "watcher-pending"])
@pytest.mark.parametrize(
    "failure",
    [
        {"ok": True, "released": True, "closed": False},
        "supervisor disconnected",
        RuntimeError("unexpected force failure"),
    ],
    ids=["released", "workerd-error", "unexpected-error"],
)
def test_failed_shutdown_keeps_exact_fence_and_later_close_retries(
    retirement, monkeypatch, caplog, wait, detached, failure
):
    if detached:
        old, key = _detach_by_poll(retirement)
    else:
        old, _ = _acquire(retirement)
        key = pool._worker_key(str(retirement.root), "fig.py")
        retirement.supervisor.grace_failure = {"ok": True, "closed": False}
    supervisor = retirement.supervisor
    supervisor.force_failure = failure

    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)

    assert supervisor.forces == [old._session_id]
    assert old._session_id in supervisor.sessions
    assert key not in pool._workers
    assert pool._pending_retirements[key] == [old]
    assert "强制关停未确认" in caplog.text
    # Reopening cannot acquire a second reference to the old spawn, even when
    # force close throws outside the adapter's usual WorkerdError contract.
    if isinstance(failure, RuntimeError):
        supervisor.force_failure = "still unavailable"
    with pytest.raises(pool.WorkerError) as caught:
        _acquire(retirement)
    assert caught.value.code == "session_dead"
    assert len(supervisor.opens) == 1
    assert pool._pending_retirements[key] == [old]

    supervisor.force_failure = None
    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)
    assert supervisor.forces == [old._session_id] * 3
    assert old._session_id not in supervisor.sessions
    assert key not in pool._pending_retirements


def _other_project(retirement):
    root = retirement.root.parent / "other-project"
    root.mkdir()
    for script in ("other.py", "fresh.py"):
        (root / script).write_text("OTHER", encoding="utf-8")
    return SimpleNamespace(root=root, supervisor=retirement.supervisor)


@pytest.mark.parametrize("wait", [False, True])
def test_project_close_reclaims_all_three_watcher_retirement_slots(retirement, monkeypatch, wait):
    supervisor = retirement.supervisor
    old = [_acquire(retirement, script)[0] for script in ("fig.py", "other.py", "fresh.py")]
    assert pool.MAX_ALIVE == len(old) == 3
    for worker, opened in zip(old, supervisor.opens[:]):
        supervisor.call("open_session", payload=opened["payload"])
        (retirement.root / worker.script_name).write_text("NEW SOURCE", encoding="utf-8")
    retirement.watcher.poll()
    supervisor.finish_closes()
    assert sum(map(len, pool._pending_retirements.values())) == pool.MAX_ALIVE
    other = _other_project(retirement)
    with pytest.raises(pool.WorkerError) as caught:
        _acquire(other, "fresh.py")
    assert caught.value.code == "session_dead"
    opens_before = len(supervisor.opens)

    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)

    assert sorted(supervisor.forces) == sorted(worker._session_id for worker in old)
    assert pool._pending_retirements == {}
    assert all(worker._session_id not in supervisor.sessions for worker in old)
    new, created = _acquire(other, "fresh.py")
    assert created and new._session_id in supervisor.sessions
    assert len(supervisor.opens) == opens_before + 1


@pytest.mark.parametrize("global_shutdown", [False, True])
def test_shutdown_scope_includes_pending_and_active_without_cross_project_kills(
    retirement, monkeypatch, global_shutdown
):
    old, key = _detach_by_poll(retirement)
    other = _other_project(retirement)
    pending_other, _ = _acquire(other, "other.py")
    hot_other, _ = _acquire(other, "fresh.py")
    supervisor = retirement.supervisor
    supervisor.grace_failure = {"ok": True, "closed": False}
    pool.invalidate("other.py", str(other.root))
    for thread in supervisor.close_threads:
        if thread is not supervisor.close_threads[0]:
            thread.join(5)
    other_key = pool._worker_key(str(other.root), "other.py")
    assert pool._pending_retirements[other_key] == [pending_other]
    monkeypatch.setattr(workerd_client, "reset_client", lambda: None)

    pool.shutdown_all(None if global_shutdown else str(retirement.root), wait=True)

    assert key not in pool._pending_retirements
    assert old._session_id not in supervisor.sessions
    if global_shutdown:
        assert pool._workers == pool._pending_retirements == {}
        assert supervisor.sessions == {}
        assert set(supervisor.forces) == {
            old._session_id,
            pending_other._session_id,
            hot_other._session_id,
        }
    else:
        assert supervisor.forces == [old._session_id]
        assert pool._pending_retirements[other_key] == [pending_other]
        assert pending_other._session_id in supervisor.sessions
        assert hot_other._session_id in supervisor.sessions
        assert _acquire(other, "fresh.py") == (hot_other, False)


@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("already_closed", [False, True], ids=["closed", "known-false"])
def test_confirmed_project_shutdown_needs_no_force(retirement, monkeypatch, wait, already_closed):
    worker, _ = _acquire(retirement)
    supervisor = retirement.supervisor
    if already_closed:
        supervisor.sessions.clear()
        supervisor.by_spec.clear()

    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)

    assert supervisor.forces == []
    assert pool._workers == pool._pending_retirements == {}
    assert worker._session_id not in supervisor.sessions


@pytest.mark.parametrize("wait", [False, True])
def test_shutdown_timeout_forces_old_session_before_late_close(retirement, monkeypatch, wait):
    old, _ = _acquire(retirement)
    old.ensure_built()
    supervisor = retirement.supervisor
    supervisor.blocked_session = old._session_id
    monkeypatch.setattr(pool, "_SHUTDOWN_JOIN_TIMEOUT", 0)
    (retirement.root / "fig.py").write_text("NEW SOURCE", encoding="utf-8")

    _shutdown_and_wait(monkeypatch, str(retirement.root), wait=wait)

    assert supervisor.close_started.wait(5)
    assert not supervisor.allow_close.is_set()
    assert supervisor.forces == [old._session_id]
    new = _assert_new_execution(retirement, old)
    key = pool._worker_key(str(retirement.root), "fig.py")
    # This replacement itself becomes pending before the old graceful reply
    # arrives. Clearing by key instead of exact object would drop its fence.
    supervisor.grace_failure = {"ok": True, "closed": False}
    pool.invalidate("fig.py", str(retirement.root))
    for thread in supervisor.close_threads:
        if thread is not supervisor.close_threads[0]:
            thread.join(5)
            assert not thread.is_alive()
    assert pool._pending_retirements[key] == [new]

    supervisor.finish_closes()

    assert supervisor.close_responses[-1] == (
        old._session_id,
        False,
        {"ok": True, "known": False, "closed": False},
    )
    assert new._session_id in supervisor.sessions
    assert pool._pending_retirements[key] == [new]


def test_global_shutdown_does_not_forget_failed_force_after_client_reset(
    retirement, monkeypatch, caplog
):
    old, key = _detach_by_poll(retirement)
    retirement.supervisor.force_failure = "disconnected"
    resets = []
    monkeypatch.setattr(workerd_client, "reset_client", lambda: resets.append(True))

    pool.shutdown_all(wait=True)

    assert resets == [True]
    assert retirement.supervisor.forces == [old._session_id]
    assert old._session_id in retirement.supervisor.sessions
    assert pool._pending_retirements[key] == [old]
    assert "强制关停未确认" in caplog.text
    retirement.supervisor.force_failure = None
    pool.shutdown_all(wait=True)
    assert key not in pool._pending_retirements


def test_async_shutdown_force_does_not_block_other_project_hot_acquire(retirement, monkeypatch):
    old, _ = _acquire(retirement)
    other = _other_project(retirement)
    hot, _ = _acquire(other, "other.py")
    hot.ensure_built()
    supervisor = retirement.supervisor
    supervisor.grace_failure = {"ok": True, "closed": False}
    force_started, allow_force, finished = threading.Event(), threading.Event(), threading.Event()
    call, finish = supervisor.call, pool._finish_shutdown

    def delayed_force(op, **kwargs):
        if op == "close_session" and kwargs.get("payload", {}).get("force"):
            assert kwargs["session_id"] == old._session_id
            force_started.set()
            assert allow_force.wait(5), "test did not release force close"
        return call(op, **kwargs)

    def observe(closing):
        try:
            finish(closing)
        finally:
            finished.set()

    monkeypatch.setattr(supervisor, "call", delayed_force)
    monkeypatch.setattr(pool, "_finish_shutdown", observe)
    try:
        pool.shutdown_all(str(retirement.root))
        assert force_started.wait(5)
        assert not finished.is_set(), "test must hold a real in-flight force close"
        assert _acquire_without_waiting_for_close(other, "other.py") == (hot, False)
        assert hot._session_id in supervisor.sessions
    finally:
        allow_force.set()
        assert finished.wait(5)
    assert supervisor.forces == [old._session_id]
    assert pool._pending_retirements == {}


def _explicit_rerun(harness, monkeypatch, **kwargs):
    # Explicit rerun already owns the pool lock until its first bounded close
    # finishes. The ordinary acquisition retry must still pass the model's
    # outside-lock check, so relax this assertion only for this existing path.
    with monkeypatch.context() as patch:
        patch.setattr(harness.supervisor, "_check_close_locks", lambda: None)
        pool.invalidate("fig.py", str(harness.root), force=True, **kwargs)


@pytest.mark.parametrize(
    "failure",
    [
        {"ok": True, "closed": False},
        {"ok": True},
        workerd_client.WorkerdError("close timed out", code="worker_timeout"),
        "supervisor disconnected",
        RuntimeError("unexpected force failure"),
    ],
    ids=["closed-false", "ambiguous-success", "timeout", "disconnected", "unexpected-error"],
)
def test_failed_explicit_rerun_fences_the_next_ordinary_acquire(retirement, monkeypatch, failure):
    old, _ = _acquire(retirement)
    execution = old.ensure_built()["execution_id"]
    supervisor = retirement.supervisor
    supervisor.force_failure = failure
    supervisor.grace_failure = {"ok": True, "closed": False}
    key = pool._worker_key(str(retirement.root), "fig.py")
    error_type = RuntimeError if type(failure) is RuntimeError else pool.WorkerError

    for _ in range(2):
        with pytest.raises(error_type) as rerun:
            _explicit_rerun(retirement, monkeypatch)
        if error_type is pool.WorkerError:
            assert rerun.value.code == "session_dead"
        assert not old.alive()
        assert pool._workers[key] is old
    try:
        reopened, _ = _acquire(retirement)
    except error_type as caught:
        if error_type is pool.WorkerError:
            assert caught.code == "session_dead"
    else:
        actual = reopened.ensure_built()["execution_id"]
        pytest.fail(
            f"unconfirmed rerun reopened session {old._session_id} as {reopened._session_id} "
            f"and execution {execution} as {actual}"
        )
    assert len(supervisor.opens) == 1
    assert supervisor.forces == [old._session_id] * 3
    assert pool._pending_retirements[key] == [old]
    assert old._session_id in supervisor.sessions

    supervisor.force_failure = None
    new, created = _acquire(retirement)
    assert created and new is not old
    assert new._session_id != old._session_id
    assert new.ensure_built()["execution_id"] != execution
    assert old._session_id not in supervisor.sessions
    assert pool._workers[key] is new
    assert key not in pool._pending_retirements
    assert supervisor.opens[0]["payload"] == supervisor.opens[-1]["payload"]
    assert _acquire(retirement) == (new, False)
    assert supervisor.forces == [old._session_id] * 4


@pytest.mark.parametrize("already_closed", [False, True], ids=["closed", "known-false"])
def test_confirmed_explicit_rerun_has_no_pending_fence(retirement, monkeypatch, already_closed):
    old, _ = _acquire(retirement)
    execution = old.ensure_built()["execution_id"]
    supervisor = retirement.supervisor
    if already_closed:
        supervisor.sessions.clear()
        supervisor.by_spec.clear()

    _explicit_rerun(retirement, monkeypatch)

    key = pool._worker_key(str(retirement.root), "fig.py")
    assert key not in pool._workers
    assert key not in pool._pending_retirements
    assert supervisor.forces == [old._session_id]
    expected = {"ok": True, "closed": True}
    if already_closed:
        expected = {"ok": True, "known": False, "closed": False}
    assert supervisor.close_responses[-1] == (old._session_id, True, expected)
    new, created = _acquire(retirement)
    assert created and new._session_id != old._session_id
    assert new.ensure_built()["execution_id"] != execution
    assert supervisor.opens[0]["payload"] == supervisor.opens[-1]["payload"]


@pytest.mark.parametrize("already_closed", [False, True], ids=["closed", "known-false"])
def test_confirmed_explicit_retry_clears_its_previously_failed_fence(
    retirement, monkeypatch, already_closed
):
    old, _ = _acquire(retirement)
    execution = old.ensure_built()["execution_id"]
    supervisor = retirement.supervisor
    supervisor.force_failure = {"ok": True, "closed": False}
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)
    key = pool._worker_key(str(retirement.root), "fig.py")
    assert pool._pending_retirements[key] == [old]
    if already_closed:
        supervisor.sessions.clear()
        supervisor.by_spec.clear()
    supervisor.force_failure = None

    _explicit_rerun(retirement, monkeypatch)

    assert old._session_id not in supervisor.sessions
    assert key not in pool._workers
    assert key not in pool._pending_retirements
    # A later close-channel failure cannot revoke the confirmation we already
    # have, or block a fresh open behind an unnecessary third close attempt.
    supervisor.force_failure = "unexpected third close"
    new, created = _acquire(retirement)
    assert created and new._session_id != old._session_id
    assert new.ensure_built()["execution_id"] != execution
    assert supervisor.forces == [old._session_id] * 2


def test_confirmed_explicit_retry_keeps_other_pending_handles_at_the_same_key(
    retirement, monkeypatch
):
    old, _ = _acquire(retirement)
    supervisor = retirement.supervisor
    supervisor.force_failure = {"ok": True, "closed": False}
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)
    # A different entry can occupy the same logical pool key while its previous
    # supervisor session still has an outstanding retirement handle.
    other = pool.WorkerdWorker("fig.py", str(retirement.root), "other_entry", client=supervisor)
    key = pool._worker_key(str(retirement.root), "fig.py")
    with pool._lock:
        pool._remember_retirements([(key, other)])
    assert pool._pending_retirements[key] == [old, other]
    assert other._session_id != old._session_id
    supervisor.force_failure = None

    _explicit_rerun(retirement, monkeypatch)

    assert key not in pool._workers
    assert old._session_id not in supervisor.sessions
    assert other._session_id in supervisor.sessions
    assert pool._pending_retirements[key] == [other]


def test_multiconfig_rerun_keeps_only_the_unconfirmed_retirements(retirement, monkeypatch):
    runs = [execspec.RunSelection("rc_a", ("1",)), execspec.RunSelection("rc_b", ("2",))]
    workers = [pool.acquire("fig.py", str(retirement.root), "main", run=run)[0] for run in runs]
    first, second = workers
    execution = first.ensure_built()["execution_id"]
    keys = [pool._worker_key(str(retirement.root), "fig.py", run=run) for run in runs]
    supervisor = retirement.supervisor
    supervisor.force_failure = {"ok": True, "closed": False}
    for run in runs:
        with pytest.raises(pool.WorkerError):
            _explicit_rerun(retirement, monkeypatch, run=run)
    assert pool._pending_retirements == dict(zip(keys, ([first], [second])))
    supervisor.force_failure = None
    call = supervisor.call

    def second_still_fails(op, **kwargs):
        if (
            op == "close_session"
            and kwargs.get("payload", {}).get("force")
            and kwargs["session_id"] == second._session_id
        ):
            raise workerd_client.WorkerdError("second close timed out", code="worker_timeout")
        return call(op, **kwargs)

    monkeypatch.setattr(supervisor, "call", second_still_fails)
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)

    assert first._session_id not in supervisor.sessions
    assert second._session_id in supervisor.sessions
    assert keys[0] not in pool._workers
    assert pool._workers[keys[1]] is second
    assert pool._pending_retirements == {keys[1]: [second]}
    new, created = pool.acquire("fig.py", str(retirement.root), "main", run=runs[0])
    assert created and new._session_id != first._session_id
    assert new.ensure_built()["execution_id"] != execution


def test_failed_rerun_fence_does_not_block_another_hot_worker(retirement, monkeypatch):
    old, _ = _acquire(retirement)
    hot, _ = _acquire(retirement, "other.py")
    hot.ensure_built()
    supervisor = retirement.supervisor
    supervisor.force_failure = {"ok": True, "closed": False}
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)
    # The dead old handle exists in _workers as well as the retirement table.
    # It must count only once toward cold capacity, never prevent hot reuse.
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)
    assert sum(map(len, pool._pending_retirements.values())) == 1
    assert [worker for worker in pool._workers.values() if worker.alive()] == [hot]
    monkeypatch.setattr(pool, "MAX_ALIVE", 1)
    assert _acquire_without_waiting_for_close(retirement, "other.py") == (hot, False)
    with pytest.raises(pool.WorkerError) as cold:
        _acquire(retirement, "fresh.py")
    assert cold.value.code == "session_dead"
    assert len(supervisor.opens) == 2
    assert supervisor.forces == [old._session_id] * 2


def test_late_close_after_failed_rerun_preserves_the_replacements_fence(retirement, monkeypatch):
    old, _ = _acquire(retirement)
    execution = old.ensure_built()["execution_id"]
    supervisor = retirement.supervisor
    supervisor.force_failure = {"ok": True, "closed": False}
    with pytest.raises(pool.WorkerError):
        _explicit_rerun(retirement, monkeypatch)
    supervisor.blocked_session = old._session_id
    pool.invalidate("fig.py", str(retirement.root))
    assert supervisor.close_started.wait(5)
    key = pool._worker_key(str(retirement.root), "fig.py")
    assert pool._pending_retirements[key] == [old]

    supervisor.force_failure = None
    new, created = _acquire(retirement)
    assert created and new._session_id != old._session_id
    assert new.ensure_built()["execution_id"] != execution
    supervisor.grace_failure = {"ok": True, "closed": False}
    pool.invalidate("fig.py", str(retirement.root))
    for thread in supervisor.close_threads[1:]:
        thread.join(5)
        assert not thread.is_alive()
    assert pool._pending_retirements[key] == [new]

    supervisor.finish_closes()

    assert supervisor.close_responses[-1] == (
        old._session_id,
        False,
        {"ok": True, "known": False, "closed": False},
    )
    assert new._session_id in supervisor.sessions
    assert pool._pending_retirements[key] == [new]


@pytest.mark.parametrize("outcome", [None, False, RuntimeError("close failed")])
def test_explicit_rerun_preserves_non_workerd_close_contract(retirement, monkeypatch, outcome):
    calls = []

    def force():
        calls.append(True)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    # The Python worker's successful force_kill returns None. It has no remote
    # spawn cache, so it must not gain a supervisor-retirement fence.
    worker = SimpleNamespace(force_kill=force)
    key = pool._worker_key(str(retirement.root), "fig.py")
    pool._workers[key] = worker
    if outcome is None:
        _explicit_rerun(retirement, monkeypatch)
        assert key not in pool._workers
    else:
        expected = type(outcome) if isinstance(outcome, Exception) else pool.WorkerError
        with pytest.raises(expected):
            _explicit_rerun(retirement, monkeypatch)
        assert pool._workers[key] is worker
    assert calls == [True]
    assert pool._pending_retirements == {}
