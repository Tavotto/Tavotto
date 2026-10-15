"""The optional cold-acquire port follows project lifecycle without a reverse import.

The subprocess checks a clean pool-only consumer. Lifecycle tests call production
start/stop; only the polling loop and external worker startup are replaced.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import pool


class Worker:
    def __init__(self, script="plot.py", entry="main"):
        self.script_name = script
        self.entry = entry
        self.python = sys.executable
        self.build_failed = False
        self.running = True
        self.last_used = 0

    def alive(self):
        return self.running

    def shutdown(self):
        self.running = False


@pytest.fixture
def isolated_pool(monkeypatch):
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {})
    monkeypatch.setattr(pool, "_last_acquired", threading.local())
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", None)
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "resolve_worker_python", lambda *a, **kw: (sys.executable, "test"))
    monkeypatch.setattr(pool, "_refuse_if_mutating", lambda *a: None)
    monkeypatch.setattr(pool.launcher_mod, "prewarm", lambda *a: None)
    monkeypatch.setattr(pool, "_schedule_prune", lambda: None)
    monkeypatch.setattr(pool, "generation_stale", lambda worker: False)
    monkeypatch.setattr(pool, "_remap_current", lambda *a: True)
    monkeypatch.setattr(pool, "_workdir_gate", lambda *a: None)
    monkeypatch.setattr(
        pool, "_new_worker", lambda script, figures_dir, entry, **kw: Worker(script, entry)
    )


@pytest.fixture
def watchers(isolated_pool, monkeypatch):
    from tavotto.engine import project_watch

    monkeypatch.setattr(project_watch, "_watchers", {})
    # Registration and close are real; no background polling races these probes.
    monkeypatch.setattr(project_watch.ProjectWatcher, "run", lambda self: None)
    yield project_watch
    project_watch.stop()


def _context(path):
    from tavotto.engine import registry

    path.mkdir()
    return SimpleNamespace(path=path, id=path.name, registry=registry.Registry())


def test_standalone_pool_cold_acquisition_does_not_load_application_or_watcher(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """\
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        sys.path.insert(0, sys.argv[1])
        from tavotto.engine import pool

        forbidden = {
            "tavotto.app", "tavotto.engine.project_watch", "tavotto.engine.project_refresh",
        }
        assert forbidden.isdisjoint(sys.modules), forbidden.intersection(sys.modules)
        assert pool.COLD_ACQUIRE_OBSERVER is None
        worker = SimpleNamespace(
            entry="main", python=sys.executable, alive=lambda: True, last_used=0,
        )
        with (
            patch.object(pool, "resolve_worker_python", return_value=(sys.executable, "test")),
            patch.object(pool, "_refuse_if_mutating"),
            patch.object(pool.launcher_mod, "prewarm"),
            patch.object(pool, "_schedule_prune"),
            patch.object(pool, "_new_worker", return_value=worker) as spawn,
        ):
            assert pool.acquire("plot.py", sys.argv[2], "main") == (worker, True)
            spawn.assert_called_once()
        assert pool.COLD_ACQUIRE_OBSERVER is None
        assert forbidden.isdisjoint(sys.modules), forbidden.intersection(sys.modules)
        print("standalone cold acquisition passed")
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(root / "src"), str(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "standalone cold acquisition passed"


@pytest.mark.parametrize("close_all", [False, True])
def test_watcher_lifecycle_owns_observer_until_last_project_closes(
    watchers, tmp_path, monkeypatch, close_all
):
    first, second = _context(tmp_path / "first"), _context(tmp_path / "second")
    first_watcher = watchers.start(first)
    callback = pool.COLD_ACQUIRE_OBSERVER
    assert callback is watchers.before_acquire
    second_watcher = watchers.start(second)
    assert pool.COLD_ACQUIRE_OBSERVER is callback

    scans = []
    take_snapshot = watchers.take_snapshot

    def scan(path):
        scans.append(path)
        return take_snapshot(path)

    monkeypatch.setattr(watchers, "take_snapshot", scan)
    watchers.stop(first.path)
    assert first_watcher.stop_event.is_set()
    assert not second_watcher.stop_event.is_set()
    assert pool.COLD_ACQUIRE_OBSERVER is callback
    callback(first.path, "plot.py")  # A previously captured callback after close is harmless.
    assert scans == []
    callback(second.path, "plot.py")
    assert scans == [second.path]

    watchers.stop(None if close_all else second.path)
    assert second_watcher.stop_event.is_set()
    assert pool.COLD_ACQUIRE_OBSERVER is None
    callback(second.path, "plot.py")
    assert scans == [second.path]
    assert watchers._watchers == {}

    reopened = watchers.start(first)
    assert reopened is not first_watcher
    assert pool.COLD_ACQUIRE_OBSERVER is callback
    scans.clear()
    pool.COLD_ACQUIRE_OBSERVER(first.path, "plot.py")
    assert scans == [first.path]
    watchers.stop(first.path)
    assert reopened.stop_event.is_set()
    assert pool.COLD_ACQUIRE_OBSERVER is None


@pytest.mark.parametrize("initial", ["cold", "dead", "failed_build"])
def test_cold_boundaries_invoke_registered_observer_once_outside_pool_lock(
    watchers, tmp_path, monkeypatch, initial
):
    ctx = _context(tmp_path / "project")
    calls = []
    before_acquire = watchers.before_acquire

    def observe(figures_dir, script):
        # A nonblocking acquisition fails immediately if the caller holds this
        # non-reentrant lock; a misplaced callback cannot hang the test suite.
        unlocked = pool._lock.acquire(blocking=False)
        assert unlocked, "cold observer ran under the pool lock"
        pool._lock.release()
        calls.append((figures_dir, script))
        before_acquire(figures_dir, script)

    monkeypatch.setattr(watchers, "before_acquire", observe)
    watchers.start(ctx)
    assert pool.COLD_ACQUIRE_OBSERVER is observe
    old = Worker()
    if initial != "cold":
        old.running = initial != "dead"
        old.build_failed = initial == "failed_build"
        pool._workers[pool._worker_key(str(ctx.path), "plot.py", None, None)] = old

    current, created = pool.acquire("plot.py", str(ctx.path), "main")
    assert calls == [(str(ctx.path), "plot.py")]
    assert created is (initial != "failed_build")
    if initial == "failed_build":
        assert current is old
    else:
        assert current is not old

    current.build_failed = False
    calls.clear()
    assert pool.acquire("plot.py", str(ctx.path), "main") == (current, False)
    assert calls == []
