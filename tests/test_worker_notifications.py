"""Delayed filesystem/registry notifications retire their original instances."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tavotto.engine import atomicio, discover, pool, probe, project_refresh, project_watch, registry


class Worker:
    def __init__(self, script="plot.py", generation=1):
        self.script_name = script
        self.generation = generation
        self.entry = "main"
        self.execution_id = f"execution-{generation}"
        self.last_build_execution_id = self.execution_id
        self.built = True
        self.closed = threading.Event()

    def shutdown(self):
        self.closed.set()


@pytest.fixture
def subject(tmp_path, monkeypatch):
    (tmp_path / "plot.py").write_text("# first\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("# other\n", encoding="utf-8")
    discover.write_config(
        tmp_path,
        {
            "version": 1,
            "scripts": {
                "plot.py": {"entry": "main", "cost": "medium", "notes": "", "stems": ["old"]}
            },
        },
    )
    reg = registry.Registry()
    reg.load(tmp_path)
    ctx = SimpleNamespace(path=tmp_path, id="test", registry=reg)
    project_refresh.seed_state(ctx)
    clock = [0.0]
    watcher = project_watch.ProjectWatcher(ctx, clock=lambda: clock[0])
    watcher.prime()
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(project_watch, "_watchers", {project_watch._key(tmp_path): watcher})
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", project_watch.before_acquire)
    return ctx, watcher, clock


def install(ctx, worker, variant="a"):
    key = (pool.norm_dir(ctx.path), worker.script_name, variant)
    pool._workers[key] = worker
    return key


def settle(watcher, clock):
    watcher.poll()
    clock[0] += 1
    watcher.poll()


def test_old_delta_does_not_retire_replacement_or_other_project(subject):
    ctx, watcher, clock = subject
    old_a, old_b, replacement, elsewhere = Worker(), Worker(), Worker(), Worker()
    key_a = install(ctx, old_a)
    key_b = install(ctx, old_b, "b")
    foreign = ("elsewhere", "plot.py", "a")
    pool._workers[foreign] = elsewhere
    (ctx.path / "plot.py").write_text("# second, observed before replacement\n")
    watcher.poll()  # binds both old variants
    pool._workers[key_a] = replacement
    clock[0] += 1
    watcher.poll()
    assert pool._workers[key_a] is replacement
    assert key_b not in pool._workers
    assert old_b.closed.wait(1)
    assert pool._workers[foreign] is elsewhere
    assert not replacement.closed.is_set()
    assert not elsewhere.closed.is_set()


def test_cold_boundary_settles_all_old_variants_but_not_unrelated_pending(subject):
    ctx, watcher, clock = subject
    old_a, old_b = Worker(), Worker()
    key_a, key_b = install(ctx, old_a), install(ctx, old_b, "b")
    (ctx.path / "other.py").write_text("# unrelated edit\n")
    watcher.poll()
    (ctx.path / "plot.py").write_text("# edited just before explicit run\n")
    project_watch.before_acquire(ctx.path, "plot.py")
    assert key_a not in pool._workers and key_b not in pool._workers
    assert "other.py" in watcher._pending.scripts
    replacement = Worker(generation=2)
    install(ctx, replacement)
    settle(watcher, clock)
    assert pool._workers[key_a] is replacement
    (ctx.path / "plot.py").write_text("# genuinely newer edit after replacement\n")
    settle(watcher, clock)
    assert key_a not in pool._workers
    assert replacement.closed.wait(1)


def test_style_notification_is_bound_to_original_project_instances(subject):
    ctx, watcher, clock = subject
    (ctx.path / "paper_style.py").write_text("# old style\n")
    watcher.prime()
    old, other, replacement = Worker(), Worker("other.py"), Worker(generation=2)
    key = install(ctx, old)
    other_key = install(ctx, other)
    (ctx.path / "paper_style.py").write_text("# revised style\n")
    watcher.poll()
    pool._workers[key] = replacement
    clock[0] += 1
    watcher.poll()
    assert pool._workers[key] is replacement
    assert other_key not in pool._workers
    (ctx.path / "paper_style.py").write_text("# newer style\n")
    settle(watcher, clock)
    assert key not in pool._workers


def register_output(ctx, worker):
    with project_refresh.publishing_execution(
        ctx, worker, worker.last_build_execution_id
    ) as on_registered:
        return probe.register_probed(
            ctx.path,
            worker.script_name,
            {
                "stems": ["A", "B"] if worker.script_name == "plot.py" else ["C"],
                "entry": "main",
                "remap_generation": None,
            },
            on_registered=on_registered,
        )


def refresh(ctx):
    return project_refresh.refresh_project_index(ctx, reason="watcher", allow_static_merge=False)


def test_own_registry_publication_preserves_only_exact_successful_instance(subject):
    ctx, _, _ = subject
    current, old = Worker(generation=2), Worker()
    key, other_key = install(ctx, current), install(ctx, old, "b")
    assert register_output(ctx, current)["registered"]
    assert refresh(ctx)["registry"]["changed_scripts"] == ["plot.py"]
    assert pool._workers[key] is current
    assert other_key not in pool._workers
    assert not project_refresh.state_of(ctx).publications
    assert project_refresh.state_of(ctx).publication_revision is None


@pytest.mark.parametrize(
    "change",
    ["entry", "stems", "external_bytes", "replacement", "new_execution", "unknown_execution"],
)
def test_registry_publication_cannot_exempt_other_facts(subject, change):
    ctx, _, _ = subject
    current = Worker(generation=2)
    key = install(ctx, current)
    register_output(ctx, current)
    if change in ("entry", "stems"):
        config = registry.Registry()
        config.load(ctx.path)
        data = {"version": 1, "scripts": config.entries()}
        data["scripts"]["plot.py"][change] = "render" if change == "entry" else ["C"]
        discover.write_config(ctx.path, data)
    elif change == "external_bytes":
        path = registry.registry_path(ctx.path)
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "replacement":
        pool._workers[key] = Worker(generation=3)
    elif change == "new_execution":
        current.execution_id = "a-new-execution"
    else:
        current.execution_id = None
    refresh(ctx)
    assert key not in pool._workers


def test_failed_registration_never_installs_publication(subject, monkeypatch):
    ctx, _, _ = subject
    current = Worker()
    install(ctx, current)

    def fail(*args, **kwargs):
        raise OSError("deliberate registry write failure")

    monkeypatch.setattr(atomicio, "write_bytes", fail)
    with pytest.raises(OSError, match="deliberate"):
        register_output(ctx, current)
    assert not project_refresh.state_of(ctx).publications
    assert project_refresh.state_of(ctx).publication_revision is None


def test_hot_acquisition_does_not_observe_or_flush_watcher(subject, monkeypatch):
    ctx, watcher, _ = subject
    current = Worker()
    current.python = "/test/python"
    current.last_used = 0
    current.alive = lambda: True
    key = pool._worker_key(str(ctx.path), "plot.py", None, None)
    pool._workers[key] = current
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "resolve_worker_python", lambda *a, **kw: (current.python, "test"))
    monkeypatch.setattr(pool, "_refuse_if_mutating", lambda *a: None)
    monkeypatch.setattr(pool, "same_python", lambda a, b: a == b)
    monkeypatch.setattr(pool, "generation_stale", lambda worker: False)
    monkeypatch.setattr(pool, "_remap_current", lambda *a: True)
    monkeypatch.setattr(pool.launcher_mod, "prewarm", lambda *a: None)

    def forbidden(*args, **kwargs):
        pytest.fail("hot acquisition must not scan or flush filesystem notifications")

    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", forbidden)
    monkeypatch.setattr(project_watch, "take_snapshot", forbidden)
    assert pool.acquire("plot.py", str(ctx.path), "main") == (current, False)


def test_publication_facts_are_bounded_even_for_unreaped_pool_entries(subject):
    ctx, _, _ = subject
    state = project_refresh.state_of(ctx)
    for index in range(pool.MAX_ALIVE + 2):
        current = Worker(generation=index + 1)
        install(ctx, current, str(index))
        register_output(ctx, current)
        assert len(state.publications) <= pool.MAX_ALIVE


def test_target_generation_changed_in_place_is_not_an_old_event_target(subject):
    ctx, watcher, clock = subject
    current = Worker()
    key = install(ctx, current)
    (ctx.path / "plot.py").write_text("# edit before generation transition\n")
    watcher.poll()
    current.generation += 1
    clock[0] += 1
    watcher.poll()
    assert pool._workers[key] is current


def test_execution_that_changes_during_registration_gets_no_publication(subject, monkeypatch):
    ctx, _, _ = subject
    current = Worker()
    key = install(ctx, current)
    write = atomicio.write_json

    def execute_again(*args, **kwargs):
        revision = write(*args, **kwargs)
        current.execution_id = current.last_build_execution_id = "later-execution"
        return revision

    monkeypatch.setattr(atomicio, "write_json", execute_again)
    register_output(ctx, current)
    assert not project_refresh.state_of(ctx).publications
    refresh(ctx)
    assert key not in pool._workers


def test_failure_after_atomic_write_still_does_not_publish(subject, monkeypatch):
    ctx, _, _ = subject
    current = Worker()
    install(ctx, current)

    def fail(*args, **kwargs):
        raise RuntimeError("registration reload failed")

    monkeypatch.setattr(registry, "load", fail)
    with pytest.raises(RuntimeError, match="registration reload failed"):
        register_output(ctx, current)
    assert not project_refresh.state_of(ctx).publications


def test_external_replacement_immediately_after_write_does_not_gain_exemption(subject, monkeypatch):
    ctx, _, _ = subject
    current = Worker()
    key = install(ctx, current)
    write = atomicio.write_json

    def externally_replace(path, *args, **kwargs):
        revision = write(path, *args, **kwargs)
        path.write_bytes(path.read_bytes() + b"\n")
        return revision

    monkeypatch.setattr(atomicio, "write_json", externally_replace)
    register_output(ctx, current)
    refresh(ctx)
    assert key not in pool._workers


def test_probe_publication_uses_build_response_execution_not_later_worker_state(
    subject, monkeypatch
):
    ctx, _, _ = subject
    current = Worker()
    current.execution_id = current.last_build_execution_id = "later-worker-state"
    monkeypatch.setattr(
        pool,
        "build",
        lambda *a, **kw: (
            current,
            {"stems": ["A"], "descriptors": [], "execution_id": "actual-build-response"},
        ),
    )
    observed = []

    def register(result, worker, execution):
        observed.append((worker, execution))
        return {**result, "registered": True}

    result = probe.probe_and_register(ctx.path, "plot.py", register_result=register)
    assert result["registered"]
    assert observed == [(current, "actual-build-response")]
    assert "execution_id" not in result


@pytest.mark.parametrize("during_settlement", [False, True])
def test_cancelled_preparation_does_not_execute_or_settle_after_prior_cancel(
    subject, monkeypatch, during_settlement
):
    from tavotto.engine import preparation

    ctx, _, _ = subject
    cancel = threading.Event()
    if not during_settlement:
        cancel.set()
    plan = SimpleNamespace(script="plot.py", project_root=str(ctx.path), run=None, plan_id="plan")
    result = preparation.PreparationResult()
    entry = SimpleNamespace(plan=plan, result=result, cancel=cancel)
    observed = []

    def settle(*args):
        observed.append("settled")
        cancel.set()

    def finish(entry, status, **kwargs):
        result.status = status

    def forbidden(*args, **kwargs):
        pytest.fail("cancelled preparation reached user execution or environment work")

    service = preparation.PreparationService()
    monkeypatch.setattr(service, "_finish", finish)
    monkeypatch.setattr(project_watch, "before_acquire", settle)
    monkeypatch.setattr(preparation.deprepair, "decide_environment_pinned", forbidden)
    service._execute(entry, forbidden)
    assert result.status == preparation.STATUS_CANCELLED
    assert observed == (["settled"] if during_settlement else [])


def test_distinct_script_publications_do_not_discard_an_unchanged_output_owner(subject):
    ctx, _, _ = subject
    first, second = Worker(), Worker("other.py")
    first_key, second_key = install(ctx, first), install(ctx, second)
    register_output(ctx, first)
    register_output(ctx, second)
    refresh(ctx)
    assert pool._workers[first_key] is first
    assert pool._workers[second_key] is second
    assert not project_refresh.state_of(ctx).publications
