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
