"""A cancelled preparation attempt must not retire a newer attempt's worker."""

import threading
from types import SimpleNamespace

import pytest

from tavotto.engine import execspec, pool, preparation


def test_cancel_after_a_rerun_replaced_the_worker_preserves_the_replacement(tmp_path, monkeypatch):
    """Script and asset sessions can rerun the same script independently.

    Pause both builds: the second rerun replaces the first worker before the
    first returns and observes its cancellation. Use the real pool retirement
    functions so killing the replacement cannot be hidden by a call-count stub.
    """
    service = preparation.PreparationService()
    root = str(tmp_path)
    key = pool._worker_key(root, "fig.py")
    workers = {}
    started = {name: threading.Event() for name in ("first", "second")}
    release = {name: threading.Event() for name in started}

    class Worker:
        generation = 1
        built = False
        killed = False

        def alive(self):
            return not self.killed

        def shutdown(self):
            # Graceful retirement may wait until this worker's build returns.
            pass

        def force_kill(self):
            self.killed = True

    def runner(plan, **_kwargs):
        worker = workers[plan.plan_id] = Worker()
        with pool._lock:
            pool._workers[key] = worker
        started[plan.plan_id].set()
        assert release[plan.plan_id].wait(10)
        return worker, {"stems": {"fig": {}}, "descriptors": []}, True

    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(service, "_stale_reason", lambda plan: None)
    monkeypatch.setattr(
        preparation.receipt,
        "from_worker",
        lambda *args, **kwargs: SimpleNamespace(
            completeness="partial",
            runtime_rejected=None,
            inputs=None,
            binding_check=lambda: {"matched": True},
        ),
    )
    try:
        for name, target in (("first", "script"), ("second", "asset")):
            plan = preparation.PreparationPlan(
                plan_id=name,
                project_id="project",
                project_root=root,
                interpreter="",
                asset_id="fig.pdf" if target == "asset" else "",
                stem="fig",
                script="fig.py",
                entry="__main__",
                static_source=None,
                environment={},
                python_requirement={},
                dependency_intents=(),
                dependency_conflicts=(),
                launch_context=None,
                grant={},
                budget={},
                created_at=0,
                target=target,
            )
            service.register(plan, force_rebuild=True)
            service.start(name, runner=runner)
            assert started[name].wait(10)

        assert service.cancel("first", "project")["accepted"]
        release["first"].set()
        assert service.wait("first", 10)
        assert service.get("first", "project")[1].status == preparation.STATUS_CANCELLED
        assert not workers["second"].killed
        assert pool.peek("fig.py", root) is workers["second"]

        release["second"].set()
        assert service.wait("second", 10)
        assert service.get("second", "project")[1].status == preparation.STATUS_READY
    finally:
        for gate in release.values():
            gate.set()
        for name in started:
            assert service.wait(name, 10)


@pytest.mark.parametrize("configured", [False, True])
def test_explicit_rerun_retires_only_its_frozen_run_configuration(
    tmp_path, monkeypatch, configured
):
    runs = [None, execspec.RunSelection("rc_a", ("a",)), execspec.RunSelection("rc_b", ("b",))]
    killed = []
    root = str(tmp_path)
    workers = {
        pool._worker_key(root, "fig.py", None, run): SimpleNamespace(
            force_kill=lambda index=index: killed.append(index)
        )
        for index, run in enumerate(runs)
    }
    monkeypatch.setattr(pool, "_workers", workers)
    selected = 1 if configured else 0
    pool.invalidate("fig.py", root, runs[selected], only_run=True, force=True)
    assert killed == [selected]
    assert set(workers) == {
        pool._worker_key(root, "fig.py", None, run)
        for index, run in enumerate(runs)
        if index != selected
    }


def test_a_stop_accepted_just_before_the_ready_commit_ends_as_cancelled(tmp_path, monkeypatch):
    """Stop lands after the last cancel check, before `_finish(READY)`: accepted means terminal."""
    service = preparation.PreparationService()
    root = str(tmp_path)

    class Worker:
        generation = 1
        built = True
        killed = False

        def alive(self):
            return not self.killed

        def force_kill(self):
            self.killed = True

    worker = Worker()

    def runner(plan, **_kwargs):
        return worker, {"stems": {"fig": {}}, "descriptors": []}, True

    accepted = {}

    def stop_between_check_and_commit(entry, *_args, **_kwargs):
        # runs right after the cancel check, right before the READY commit
        accepted.update(service.cancel(entry.plan.plan_id, "project"))
        return False

    monkeypatch.setattr(service, "_stale_reason", lambda plan: None)
    monkeypatch.setattr(service, "_stem_missing", stop_between_check_and_commit)
    monkeypatch.setattr(
        preparation.receipt,
        "from_worker",
        lambda *args, **kwargs: SimpleNamespace(
            completeness="partial",
            runtime_rejected=None,
            inputs=None,
            binding_check=lambda: {"matched": True},
        ),
    )
    plan = preparation.PreparationPlan(
        plan_id="late",
        project_id="project",
        project_root=root,
        interpreter="",
        asset_id="fig.pdf",
        stem="fig",
        script="fig.py",
        entry="__main__",
        static_source=None,
        environment={},
        python_requirement={},
        dependency_intents=(),
        dependency_conflicts=(),
        launch_context=None,
        grant={},
        budget={},
        created_at=0,
        target="asset",
    )
    service.register(plan, force_rebuild=True)
    service.start("late", runner=runner)
    assert service.wait("late", 10)
    assert accepted["accepted"] is True
    result = service.get("late", "project")[1]
    assert result.status == preparation.STATUS_CANCELLED
    assert result.cancel_requested_at is not None


_LATE_BRANCHES = (
    "ready_fresh",
    "ready_reused",
    "stem_missing",
    "binding_mismatch",
    "stale_plan_at_start",
    "stale_before_retry",
    "worker_error",
    "worker_error_needs_confirmation",
    "worker_error_needs_dependency",
    "internal_error",
)


@pytest.mark.parametrize("branch", _LATE_BRANCHES)
def test_every_terminal_commit_yields_to_an_accepted_stop(tmp_path, monkeypatch, branch):
    """Every terminal commit (ready / error / needs_input) funnels through `_finish`, which re-checks the
    cancel flag under the entry lock: a Stop accepted after the last check ends as cancelled, never as
    ready / error / needs_input, and no error / input payload leaks onto the cancelled result."""
    service = preparation.PreparationService()
    root = str(tmp_path)
    accepted = {}

    class Worker:
        generation = 1
        built = True
        killed = False

        def alive(self):
            return not self.killed

        def force_kill(self):
            self.killed = True

    worker = Worker()

    def stop(*_a, **_k):
        accepted.update(service.cancel("late", "project"))

    receipt_matched = branch != "binding_mismatch"

    def binding_check():
        if branch == "binding_mismatch":
            stop()
        return {"matched": receipt_matched, "changed": ["x"]}

    class Late(pool.WorkerError):
        """A WorkerError whose attributes are read after the last cancel check: Stop lands on access."""

        def __init__(self, hook):
            super().__init__("boom", code="boom")
            self._hook = hook

        @property
        def confirmation(self):
            if self._hook == "confirmation":
                stop()
                return {"code": "workdir_confirmation_required"}
            return None

        @property
        def dependency_preparation(self):
            if self._hook == "dependency":
                stop()
                return {"code": "dependency_preparation_required"}
            return None

        @property
        def input_facts(self):
            if self._hook == "error":
                stop()
            return None

    def runner(plan, **_kwargs):
        if branch == "stale_before_retry":
            stop()
            raise preparation._StaleBeforeRetry(preparation.STALE_GRANT)
        if branch == "worker_error":
            raise Late("error")
        if branch == "worker_error_needs_confirmation":
            raise Late("confirmation")
        if branch == "worker_error_needs_dependency":
            raise Late("dependency")
        if branch == "internal_error":
            stop()
            raise RuntimeError("kaput")
        return worker, {"stems": {"fig": {}}, "descriptors": []}, True

    if branch == "stale_plan_at_start":

        def stale(plan):
            stop()
            return preparation.STALE_GRANT, {}

        monkeypatch.setattr(service, "_stale_reason", stale)
    else:
        monkeypatch.setattr(service, "_stale_reason", lambda plan: None)
    if branch == "stem_missing":

        def missing(w, stem, known):
            stop()
            return pool.WorkerError("no figure", code="no_figures_captured")

        monkeypatch.setattr(pool, "missing_stem_error", missing)
    elif branch == "ready_reused":
        monkeypatch.setattr(pool, "peek", lambda *a, **k: worker)
        monkeypatch.setattr(service, "_reusable", lambda existing, plan: {"descriptors": []})

        def hook(entry, *_a, **_k):
            stop()
            return False

        monkeypatch.setattr(service, "_stem_missing", hook)
    elif branch == "ready_fresh":

        def hook(entry, *_a, **_k):
            stop()
            return False

        monkeypatch.setattr(service, "_stem_missing", hook)
    monkeypatch.setattr(
        preparation.receipt,
        "from_worker",
        lambda *args, **kwargs: SimpleNamespace(
            completeness="partial",
            runtime_rejected=None,
            inputs=None,
            binding_check=binding_check,
        ),
    )
    plan = preparation.PreparationPlan(
        plan_id="late",
        project_id="project",
        project_root=root,
        interpreter="",
        asset_id="fig.pdf",
        stem="fig",
        script="fig.py",
        entry="__main__",
        static_source=None,
        environment={},
        python_requirement={},
        dependency_intents=(),
        dependency_conflicts=(),
        launch_context=None,
        grant={},
        budget={},
        created_at=0,
        target="asset",
    )
    service.register(plan, force_rebuild=branch != "ready_reused")
    service.start("late", runner=runner)
    assert service.wait("late", 10)
    assert accepted.get("accepted") is True, "the Stop must have been accepted mid-flight"
    result = service.get("late", "project")[1]
    assert result.status == preparation.STATUS_CANCELLED
    assert result.error is None
    assert result.required_input is None
    assert result.missing_input is None
    assert result.cancel_requested_at is not None


def test_terminal_status_is_only_written_by_finish():
    """Structural guard: outside `register` (pre-entry results) and `_finish`, nothing writes a terminal
    status, so the single cancel re-check in `_finish` covers every late commit."""
    import inspect
    import re

    src = inspect.getsource(preparation.PreparationService)
    writes = re.findall(r"\.status = (\S+)", src)
    assert sorted(writes) == sorted(
        ["STATUS_STATIC", "STATUS_NEEDS_INPUT", "STATUS_RUNNING", "status"]
    )
