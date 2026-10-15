"""Actual safe execution is independent of whether a request produced a Figure.

Real-child tests own the before/after user-code boundary. Fake transports exercise
the independent marker/reply channels and ambiguous supervisor cancellation.
"""

import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from support.bridgekit import USER_PYTHON, needs_user_python
from tavotto.engine import execspec, executionfact, pool, workerd_client
from test_worker_protocol import _echo, _worker as protocol_worker
from test_workerd_pool import _FakeClient, _worker as workerd_worker


@pytest.fixture
def observations(monkeypatch):
    seen = []
    monkeypatch.setattr(
        pool, "_execution_observer", lambda worker, fact: seen.append((worker, fact))
    )
    return seen


@pytest.fixture
def child(monkeypatch, tmp_path):
    if USER_PYTHON is None:
        pytest.skip("No scientific Python")
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", USER_PYTHON)
    monkeypatch.setattr(pool.workdir, "mode_for", lambda *a, **k: pool.workdir.MODE_SANDBOX)
    workers = []

    def make(script, entry="__main__"):
        (root / "plot.py").write_text(script, encoding="utf-8")
        worker = pool.EngineWorker("plot.py", str(root), entry, base_dir=tmp_path / "worker")
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.force_kill()


@needs_user_python
def test_zero_figure_execution_and_hot_reuse_are_one_fact(child, observations):
    worker = child("pass\n")
    response = worker.ensure_built()
    assert response["stems"] == {}
    fact = worker.execution_id
    assert executionfact.valid_id(fact)
    assert response["execution_id"] == fact
    assert worker.last_build_execution_id == fact
    worker.ensure_built()
    assert observations == [(worker, fact)]
    assert worker.execution_started_ns == pool.execution_started_ns(fact) > 0
    assert not worker.execution_observation_unknown
    assert not worker.execution_replaces_current  # Explicit isolated base.


@needs_user_python
@pytest.mark.parametrize("prefix", ["", "import matplotlib.pyplot as plt\nplt.plot([1, 2])\n"])
def test_script_error_even_after_a_figure_is_an_execution(child, observations, prefix):
    worker = child(prefix + "raise RuntimeError('user failure')\n")
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert executionfact.valid_id(worker.execution_id)
    assert observations == [(worker, worker.execution_id)]
    assert not worker.execution_observation_unknown


@needs_user_python
@pytest.mark.parametrize("failure", ["syntax", "missing"])
def test_uncompiled_target_has_not_entered_user_code(child, observations, failure):
    worker = child("def broken(:\n")
    if failure == "missing":
        (worker.base.parent / "project" / "plot.py").unlink()
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


@needs_user_python
def test_paper_style_entry_counts_before_target_compile_failure(child, observations):
    worker = child("def broken(:\n")
    (worker.base.parent / "project" / "paper_style.py").write_text(
        "def save(*args, **kwargs): pass\n", encoding="utf-8"
    )
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert executionfact.valid_id(worker.execution_id)
    assert observations == [(worker, worker.execution_id)]


@needs_user_python
def test_user_code_sees_its_original_trace_configuration(child, observations):
    worker = child("import sys\nassert sys.gettrace() is None\n")
    worker.ensure_built()
    assert observations == [(worker, worker.execution_id)]


@needs_user_python
def test_missing_cached_entry_does_not_invent_a_second_execution(child, observations):
    worker = child("pass\n", entry="missing")
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()  # The module body did execute on its first import.
    fact = worker.execution_id
    assert executionfact.valid_id(fact)
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()  # Cached module + missing callable: no user frame.
    assert worker.execution_id == fact and observations == [(worker, fact)]


def test_user_entry_hook_restores_and_forwards_existing_trace(tmp_path):
    original = sys.gettrace()
    calls, entered = [], []
    target = str(tmp_path / "user.py")

    def previous(frame, event, arg):
        if event == "call" and frame.f_code.co_filename == target:
            calls.append(event)
        return previous

    try:
        sys.settrace(previous)
        with executionfact.on_user_entry(target, lambda: entered.append(sys.gettrace())):
            exec(compile("pass", target, "exec"), {})
        assert sys.gettrace() is previous
    finally:
        sys.settrace(original)
    assert entered == [previous] and calls == ["call"]


@needs_user_python
def test_rejection_before_build_is_confirmed_non_execution(child, observations, tmp_path):
    worker = child("raise AssertionError('must not run')\n")
    with pytest.raises(pool.WorkerError, match="session"):
        worker.request(
            {
                "cmd": "export",
                "stem": "F",
                "path": str(tmp_path / "x.svg"),
                "format": "svg",
                "require_built": True,
            }
        )
    assert worker.execution_id is None
    assert observations == []
    assert not worker.execution_observation_unknown


def _break_marker(monkeypatch):
    enter = pool._ExecutionWatch.__enter__

    def broken(self):
        result = enter(self)
        if self.key:
            executionfact.marker_path(self.worker.out_dir, self.key).mkdir(parents=True)
        return result

    monkeypatch.setattr(pool._ExecutionWatch, "__enter__", broken)


@needs_user_python
def test_marker_write_failure_does_not_block_execution_or_reply_fact(
    child, monkeypatch, observations
):
    _break_marker(monkeypatch)
    worker = child("pass\n")
    response = worker.ensure_built()
    assert response["execution_id"] == worker.execution_id
    assert executionfact.valid_id(worker.execution_id)
    assert observations == [(worker, worker.execution_id)]
    assert not worker.execution_observation_unknown


@needs_user_python
@pytest.mark.parametrize("marker_fails", [False, True])
def test_kill_after_actual_start_uses_early_fact_or_explicit_unknown(
    child, monkeypatch, observations, marker_fails
):
    if marker_fails:
        _break_marker(monkeypatch)
    worker = child(
        "from pathlib import Path\nimport time\nPath('entered').write_text('yes')\ntime.sleep(60)\n"
    )
    errors = []

    def build():
        try:
            worker.ensure_built()
        except pool.WorkerError as exc:
            errors.append(exc)

    thread = threading.Thread(target=build)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not (worker.sandbox / "entered").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (worker.sandbox / "entered").exists(), worker._log_tail()
        assert thread.is_alive()
        if not marker_fails:
            while not observations and time.monotonic() < deadline:
                time.sleep(0.02)
            assert observations == [(worker, worker.execution_id)], (
                "Notify while build is in flight"
            )
            assert executionfact.valid_id(worker.execution_id)
        assert pool.execution_inflight_unobserved(worker) is marker_fails
        assert not worker.execution_observation_unknown
    finally:
        worker.force_kill()
        thread.join(10)
    assert not thread.is_alive()
    assert not pool.execution_inflight_unobserved(worker)
    assert errors
    assert worker.execution_observation_unknown is marker_fails
    if marker_fails:
        assert worker.execution_id is None and observations == []
    else:
        assert observations == [(worker, worker.execution_id)]


@needs_user_python
def test_kill_before_build_has_no_execution_or_unknown(child, observations):
    worker = child("raise AssertionError('must not run')\n")
    worker.force_kill()
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


@needs_user_python
def test_cancelled_after_acquisition_before_build_does_not_retire_owner(
    child, observations, monkeypatch
):
    worker = child("raise AssertionError('must not run')\n")
    monkeypatch.setattr(pool, "get", lambda *args, **kwargs: worker)

    def cancelled(_):
        raise pool.WorkerError("cancelled before build", code="cancelled")

    with pytest.raises(pool.WorkerError, match="cancelled before build"):
        pool.build("plot.py", worker.figures_dir, "__main__", before_build=cancelled)
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


@needs_user_python
def test_transcript_preflight_failure_is_not_actual_execution(child, observations, monkeypatch):
    worker = child("raise AssertionError('must not run')\n")

    def unavailable(_):
        raise pool.inputbroker.TranscriptUnavailable("cannot read policy")

    monkeypatch.setattr(pool.inputbroker, "serving", unavailable)
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


@needs_user_python
def test_observer_failure_cannot_fail_scientific_run(child, monkeypatch):
    def fail(*args):
        raise RuntimeError("observer is unavailable")

    monkeypatch.setattr(pool, "_execution_observer", fail)
    worker = child("pass\n")
    assert worker.ensure_built()["stems"] == {}
    assert executionfact.valid_id(worker.execution_id)


def test_confirmed_pre_send_failure_is_not_possible_execution(tmp_path, observations):
    worker = protocol_worker(lambda env: _echo(env), tmp_path)
    worker.built = False
    worker.proc.returncode = -9
    with pytest.raises(pool.WorkerError):
        worker.request({"cmd": "build"})
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


def test_write_failure_is_conservatively_unknown(tmp_path, monkeypatch, observations):
    worker = protocol_worker(lambda env: _echo(env), tmp_path)
    worker.built = False

    def fail(_):
        raise BrokenPipeError("partial send cannot be excluded")

    monkeypatch.setattr(worker.proc.stdin, "write", fail)
    with pytest.raises(pool.WorkerError):
        worker.request({"cmd": "build"})
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown


@pytest.mark.parametrize("hot", [False, True])
def test_lost_reply_after_send_only_marks_possible_build_unknown(tmp_path, observations, hot):
    worker = protocol_worker(lambda env: "", tmp_path)
    worker.built = hot
    with pytest.raises(pool.WorkerError):
        worker.request({"cmd": "render", "stem": "F", "patches": []})
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown is not hot


@pytest.mark.parametrize("extra", [{}, {"execution_id": "malformed"}])
def test_success_without_execution_evidence_is_unknown(tmp_path, observations, extra):
    worker = protocol_worker(lambda env: _echo(env, **extra), tmp_path)
    worker.built = False
    assert worker.request({"cmd": "build"})["ok"]
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown


@pytest.mark.parametrize(
    "code", ["queue_full", "queue_superseded", "spawn_failed", "cancelled", "session_dead"]
)
def test_workerd_before_start_errors_and_ambiguous_cancellation(
    monkeypatch, tmp_path, observations, code
):
    worker, _ = workerd_worker(
        monkeypatch,
        tmp_path,
        [{"session_id": "s"}, workerd_client.WorkerdError("stopped", code=code)],
    )
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown is (code in {"cancelled", "session_dead"})
    assert worker.execution_replaces_current


@pytest.mark.parametrize("sent", [False, True, None])
def test_workerd_unavailable_is_classified_by_send_evidence(
    monkeypatch, tmp_path, observations, sent
):
    worker, _ = workerd_worker(
        monkeypatch,
        tmp_path,
        [
            {"session_id": "s"},
            workerd_client.WorkerdError(
                "unavailable", code="workerd_unavailable", extra={"request_sent": sent}
            ),
        ],
    )
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown is (sent is not False)


@pytest.mark.parametrize("via", ["marker", "reply"])
def test_workerd_error_preserves_actual_execution_fact(monkeypatch, tmp_path, observations, via):
    worker, client = workerd_worker(monkeypatch, tmp_path, [{"session_id": "s"}])
    fact = executionfact.new_id()

    def call(op, **kw):
        if via == "marker":
            worker.out_dir.mkdir(exist_ok=True)
            assert executionfact.write(worker.out_dir, kw["payload"]["execution_watch"], fact)
        raise workerd_client.WorkerdError(
            "stopped", code="cancelled", extra={"execution_id": fact} if via == "reply" else {}
        )

    monkeypatch.setattr(client, "call", call)
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id == fact
    assert observations == [(worker, fact)]
    assert not worker.execution_observation_unknown


def test_transparent_reopen_produces_a_new_fact_even_if_parent_was_hot(
    monkeypatch, tmp_path, observations
):
    old, new = executionfact.new_id(), executionfact.new_id()
    worker, _ = workerd_worker(
        monkeypatch,
        tmp_path,
        [
            {"session_id": "s1"},
            {"stems": {}, "execution_id": old},
            workerd_client.WorkerdError("lost", code="unknown_session"),
            {"session_id": "s2"},
            {"manifest": {}, "execution_id": new},
        ],
    )
    worker.ensure_built()
    assert worker.last_build_execution_id == old
    worker.override("F", [])
    assert observations == [(worker, old), (worker, new)]
    assert worker.execution_id == new
    assert worker.last_build_execution_id == old, "Old build metadata must not be rebound"
    assert not worker.execution_observation_unknown


def test_failed_reopen_does_not_create_execution_uncertainty(monkeypatch, tmp_path, observations):
    worker, _ = workerd_worker(
        monkeypatch,
        tmp_path,
        [
            {"session_id": "s1"},
            workerd_client.WorkerdError("lost", code="unknown_session"),
            workerd_client.WorkerdError("open failed", code="spawn_failed"),
        ],
    )
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


def test_isolated_workerd_cannot_replace_current(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pool, "select_worker_python", lambda: ("/usr/bin/python3", pool.SOURCE_SYSTEM)
    )
    worker = pool.WorkerdWorker(
        "fig.py",
        str(tmp_path),
        "main",
        base_dir=tmp_path / "isolated",
        client=_FakeClient([{"session_id": "s"}]),
    )
    assert not worker.execution_replaces_current


def test_stale_marker_is_not_evidence_for_another_request(tmp_path, observations):
    worker = SimpleNamespace(out_dir=tmp_path, built=False)
    old = executionfact.new_id()
    assert executionfact.write(tmp_path, uuid.uuid4().hex, old)
    with pytest.raises(pool.WorkerError), pool._ExecutionWatch(worker, "build") as watch:
        watch.sent = True
        watch.sent_ns = time.monotonic_ns()
        raise pool.WorkerError("lost", code="session_dead")
    assert worker.execution_id is None and observations == []
    assert worker.execution_observation_unknown


def test_late_old_response_cannot_overwrite_newer_execution(tmp_path, observations):
    worker = SimpleNamespace(out_dir=tmp_path, built=False)
    pool._execution_state(worker)
    old, new = executionfact.new_id(), executionfact.new_id()
    pool._record_execution(worker, new)
    pool._record_execution(worker, old)
    assert worker.execution_id == new
    assert observations == [(worker, new)]
    assert pool.execution_started_ns(old) < pool.execution_started_ns(new)


def test_marker_is_bounded_opaque_and_rejects_partial_data(tmp_path):
    watch, fact = uuid.uuid4().hex, executionfact.new_id()
    assert executionfact.write(tmp_path, watch, fact)
    path = executionfact.marker_path(tmp_path, watch)
    assert path.read_bytes() == fact.encode("ascii") and len(fact) == 48
    assert executionfact.read(tmp_path, watch) == fact
    for data in (b"", fact[:-1].encode(), fact.encode() + b"x", b"\xff" * 48):
        path.write_bytes(data)
        assert executionfact.read(tmp_path, watch) is None
    assert not executionfact.write(tmp_path, "../elsewhere", fact)


def test_polling_thread_failure_preserves_reply_observation(tmp_path, monkeypatch, observations):
    worker = SimpleNamespace(out_dir=tmp_path, built=False)

    def fail(_):
        raise RuntimeError("cannot start another thread")

    monkeypatch.setattr(threading.Thread, "start", fail)
    with pool._ExecutionWatch(worker, "build") as watch:
        fact = executionfact.new_id()
        watch.acknowledge({"execution_id": fact})
    assert worker.execution_id == fact and observations == [(worker, fact)]


def test_unknown_is_ordered_by_dispatch_not_late_failure(tmp_path, observations):
    worker = SimpleNamespace(out_dir=tmp_path, built=False)
    with pool._ExecutionWatch(worker, "build") as old:
        old.sent = True
        old.sent_ns = time.monotonic_ns()
        newer = executionfact.new_id()
        pool._record_execution(worker, newer)
        old.mark_unknown()
        assert not worker.execution_observation_unknown
        with pool._ExecutionWatch(worker, "build") as current:
            current.sent = True
            current.sent_ns = time.monotonic_ns()
            current.mark_unknown()
            assert worker.execution_observation_unknown
            assert worker.execution_unknown_started_ns == current.sent_ns
            pool._record_execution(worker, newer)
            assert worker.execution_observation_unknown
            latest = executionfact.new_id()
            pool._record_execution(worker, latest)
            assert not worker.execution_observation_unknown


@pytest.mark.parametrize("hot", [False, True])
def test_inflight_uncertainty_is_temporary_and_never_invents_execution(tmp_path, observations, hot):
    worker = SimpleNamespace(out_dir=tmp_path, built=hot)
    with pool._ExecutionWatch(worker, "build") as watch:
        assert not pool.execution_inflight_unobserved(worker)
        watch.sent, watch.sent_ns = True, time.monotonic_ns()
        assert pool.execution_inflight_unobserved(worker) is not hot
        assert worker.execution_id is None and observations == []
        assert not worker.execution_observation_unknown
        watch.acknowledge({"execution_id": None})
        assert not pool.execution_inflight_unobserved(worker)
    assert not pool.execution_inflight_unobserved(worker)
    assert worker.execution_id is None and observations == []
    assert not worker.execution_observation_unknown


def test_owner_refresh_uses_trusted_owner_fields_without_acquiring_or_locking_callbacks(
    tmp_path, monkeypatch
):
    def worker(root=tmp_path, script="plot.py", run_config="", current=True):
        return SimpleNamespace(
            execution_replaces_current=current,
            spec=execspec.safe_spec(
                script,
                str(root),
                "__main__",
                interpreter="python",
                sandbox=str(tmp_path / "sandbox"),
                run_config=run_config,
            ),
        )

    normal, selected = worker(), worker()
    others = [
        worker(script="other.py"),
        worker(root=tmp_path / "other"),
        worker(run_config="cfg-1"),
        worker(current=False),
    ]
    monkeypatch.setattr(
        pool, "_workers", {str(n): w for n, w in enumerate([normal, selected, *others])}
    )
    refreshed = []

    def refresh(w):
        assert pool._lock.acquire(blocking=False)
        pool._lock.release()
        refreshed.append(w)

    monkeypatch.setattr(pool, "refresh_execution", refresh)
    monkeypatch.setattr(
        pool, "get", lambda *a, **kw: pytest.fail("Read-only refresh acquired worker")
    )
    assert pool.refresh_owner_execution(tmp_path, "plot.py") == [normal, selected]
    assert refreshed == [normal, selected]
    assert pool.refresh_owner_execution(tmp_path, "plot.py", "cfg-1") == [others[2]]


@needs_user_python
def test_retry_after_failed_build_has_new_execution_without_new_generation(child, observations):
    worker = child(
        "from pathlib import Path\np = Path('once')\nif not p.exists():\n p.write_text('yes')\n raise RuntimeError('first')\n"
    )
    with pytest.raises(pool.WorkerError):
        worker.ensure_built()
    old, generation = worker.execution_id, worker.generation
    worker.ensure_built()
    assert worker.execution_id != old
    assert worker.generation == generation
    assert observations == [(worker, old), (worker, worker.execution_id)]
