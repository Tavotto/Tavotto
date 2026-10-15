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
            if self.force_failure is not None:
                raise workerd_client.WorkerdError(
                    self.force_failure, code="workerd_unavailable", retryable=True
                )
        else:
            self.close_threads.append(threading.current_thread())
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
