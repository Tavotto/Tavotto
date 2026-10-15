"""Actual executions retire an owner's entire output set, not only returned figures."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

import test_export_svg as svg_cases
from tavotto import app as m
from tavotto.engine import executionfact, pool, svgexecution
from test_export_svg import document, spec


@pytest.fixture
def project(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tavotto")
    yield from svg_cases.project.__wrapped__(tmp_path, monkeypatch)


SCRIPT = """import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from pathlib import Path
import sys, time
class Broken(Artist):
    def draw(self, renderer): raise RuntimeError("broken B draw")
def main():
    mode = Path("mode.txt").read_text(encoding="utf-8").strip()
    if mode == "wait":
        print("EXECUTION_STARTED", file=sys.stderr, flush=True)
        time.sleep(120)
    if mode == "zero": return
    if mode == "raise": raise RuntimeError("user execution failed")
    names = ["A", "B"] if mode in ("both", "partial") else (["C"] if mode == "rename" else ["A"])
    for name in names:
        fig, ax = plt.subplots(figsize=(3, 2)); ax.plot([0, 1], [1, 0]); ax.set_title(name + mode)
        if mode == "partial" and name == "B": ax.add_artist(Broken())
        fig.savefig(name + ".pdf")
"""


def run(client, root, mode, **options):
    (root / "mode.txt").write_text(mode, encoding="utf-8")
    response = client.post("/api/registry/probe", json={"script": "plot.py", **options})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def save(client, source, name="saved", reference=None):
    doc = document(source, [])
    if reference:
        doc["canvases"][0]["objects"][0]["figureSnapshot"] = reference
    response = client.post(f"/api/layouts/{name}?target=project", json=doc)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def saved_reference(client, source, name="saved"):
    saved = save(client, source, name)
    assert "panel" in saved["figure_snapshots"], saved
    return saved["figure_snapshots"]["panel"]


def exported(client, source, reference):
    response = client.post(
        "/api/export",
        json=spec(source, scope="original", formats=("pdf", "svg"), reference=reference),
    )
    return response.get_json()


@pytest.mark.parametrize("mode", ["one", "rename", "zero", "raise", "partial"])
def test_actual_execution_retires_omitted_failed_and_zero_outputs(project, mode):
    client, root = project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    first = run(client, root, "both")
    source = next(d["asset_id"] for d in first["descriptors"] if d["stem"] == "B")
    reference = saved_reference(client, source)
    assert reference["executionOwner"] == {"script": "plot.py", "run_config": None}
    next_run = run(client, root, mode)
    fact = next_run["figure_snapshot_invalidation"]
    assert fact["scope"] == "owner" and fact["script"] == "plot.py"
    assert fact["run_config"] is None and fact["source_ids"] == []
    assert reference["receiptId"] in fact["receipt_ids"]
    assert exported(client, source, reference)["status"] == "failed"
    saved = save(client, source, "current", reference)
    assert saved["figure_snapshots"] == {}
    assert saved["svg_snapshot_warnings"] == [source]
    if mode in ("one", "rename", "partial"):
        assert next_run["registered"]
    if mode == "partial":
        assert next_run["partial"] and next_run["failed_figures"][0]["stem"] == "B"


def test_other_configuration_and_explicit_cold_history_remain_valid(project, monkeypatch, tmp_path):
    client, root = project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    first = run(client, root, "both")
    source = next(d["asset_id"] for d in first["descriptors"] if d["stem"] == "B")
    old = saved_reference(client, source)
    other_run = run(client, root, "both", argv=["--other"])
    other_source = next(d["asset_id"] for d in other_run["descriptors"] if d["stem"] == "B")
    other = saved_reference(client, other_source, "other")
    run(client, root, "one")
    assert exported(client, source, old)["status"] == "failed"
    assert exported(client, other_source, other)["status"] == "done"
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "empty-cache"))
    m.open_project(str(moved))
    monkeypatch.setattr(pool, "get", lambda *a, **k: pytest.fail("historical export executed code"))
    assert exported(client, source, old)["status"] == "done"
    assert exported(client, other_source, other)["status"] == "done"


@pytest.mark.parametrize("observation", ["background", "synchronous", "unobserved"])
def test_inflight_and_started_cancellation_revoke_before_probe_finishes(
    project, monkeypatch, observation
):
    client, root = project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    first = run(client, root, "both")
    source = next(d["asset_id"] for d in first["descriptors"] if d["stem"] == "B")
    reference = saved_reference(client, source)
    (root / "mode.txt").write_text("wait", encoding="utf-8")
    results = []
    if observation == "synchronous":
        monkeypatch.setattr(pool._ExecutionWatch, "_poll", lambda self: self.stop.wait(60))
    elif observation == "unobserved":
        monkeypatch.setattr(executionfact, "read", lambda *args: None)

    def execute():
        with m.app.test_client() as other:
            results.append(other.post("/api/registry/probe", json={"script": "plot.py"}).get_json())

    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            worker = pool.peek("plot.py", root)
            if worker and "EXECUTION_STARTED" in worker.log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail("user code did not start")
        assert thread.is_alive()
        assert exported(client, source, reference)["status"] == "failed"
        response = client.post("/api/registry/probe/cancel", json={"script": "plot.py"})
        assert response.get_json()["cancelling"]
        thread.join(20)
        assert not thread.is_alive()
        if observation == "unobserved":
            assert "figure_snapshot_invalidation" not in results[0]
        else:
            assert results[0]["figure_snapshot_invalidation"]["scope"] == "owner"
        assert exported(client, source, reference)["status"] == "failed"
    finally:
        if thread.is_alive():
            client.post("/api/registry/probe/cancel", json={"script": "plot.py"})
            thread.join(20)


def test_ledger_unknown_is_ordered_not_a_fake_execution():
    ledger = svgexecution.Ledger()
    owner = svgexecution.owner("plot.py")
    old, newer, newest = object(), object(), object()
    ledger.uncertain(owner, old, 10)
    assert ledger.accepts(owner, "old") is False
    ledger.observe(owner, "a", 20, old)
    assert ledger.admit(owner, old, "a", "a-receipt")
    ledger.uncertain(owner, newer, 30)
    assert ledger.accepts(owner, "a-receipt") is False
    ledger.observe(owner, "c", 40, newest)
    assert ledger.admit(owner, newest, "c", "c-receipt")
    ledger.uncertain(owner, newer, 30)
    assert ledger.accepts(owner, "c-receipt") is True


def test_one_shot_observer_does_not_retire_current_owner(project):
    _, root = project
    worker = SimpleNamespace(
        spec=SimpleNamespace(project_root=str(root), target="plot.py", run_config=""),
        execution_replaces_current=False,
    )
    m._svg_execution_observed(worker, executionfact.new_id())
    assert m.current_ctx().svg_executions.owners == {}
    assert not hasattr(worker, "figure_snapshot_invalidation")


def test_unknown_new_attempt_blocks_cold_reference_without_claiming_execution(project):
    client, root = project
    first = svg_cases.run_script(client)
    source = first["descriptors"][0]["asset_id"] if isinstance(first, dict) else first
    reference = saved_reference(client, source)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    worker = SimpleNamespace(
        spec=SimpleNamespace(project_root=str(root), target="plot.py", run_config=""),
        execution_replaces_current=True,
        execution_observation_unknown=True,
        execution_unknown_started_ns=time.monotonic_ns(),
    )
    pool.refresh_execution(worker)
    worker.execution_observation_unknown = True
    worker.execution_unknown_started_ns = time.monotonic_ns()
    assert m._svg_execution_result(worker) == {}
    assert exported(client, source, reference)["status"] == "failed"


def test_late_archive_scan_and_obsolete_worker_cannot_publish_retirement(project, monkeypatch):
    from tavotto.engine import svgstore

    _, root = project
    owner = svgexecution.owner("plot.py")
    old_id, new_id = f"{10:016x}" + "a" * 32, f"{20:016x}" + "b" * 32

    def worker(eid):
        return SimpleNamespace(
            spec=SimpleNamespace(project_root=str(root), target="plot.py", run_config=""),
            execution_replaces_current=True,
            execution_id=eid,
        )

    old, new = worker(old_id), worker(new_id)
    emitted = []
    monkeypatch.setattr(
        m,
        "sse_publish",
        lambda name, value: (
            emitted.append(value) if name == "figure.snapshot_invalidated" else None
        ),
    )
    once = []

    def rows(*args, **kwargs):
        if not once:
            once.append(True)
            m._svg_execution_observed(new, new_id)
            m.current_ctx().svg_executions.admit(owner, new, new_id, "new-receipt")
            return [{"source_id": "shared.pdf", "receipt_id": "new-receipt", "current": True}]
        return []

    monkeypatch.setattr(svgstore, "source_records_for_owner", rows)
    m._svg_execution_observed(old, old_id)
    assert emitted[-1]["execution_id"] == new_id
    assert all("new-receipt" not in event["receipt_ids"] for event in emitted)
    assert m._svg_execution_result(old) == {}


def native_session(root, *, arg_count=0):
    import sys

    from tavotto.engine import nativesession

    session = nativesession.NativeSession(
        session_id="native-execution-matrix",
        descriptor={
            "out_dir": str(root / "native-out"),
            "metadata": {
                "project_root": str(root),
                "interpreter": sys.executable,
                "cwd": str(root),
                "target_kind": "script",
                "target_display": "plot.py",
                "arg_count": arg_count,
            },
        },
    )
    session._on_change = m._native_session_event
    session.note_hello({"pid": 42})
    return session


def test_native_actual_start_and_each_release_retire_owner_even_without_outputs(project):
    from tavotto.engine import nativesession

    _, root = project
    ctx = m.current_ctx()
    session = native_session(root)
    identity = svgexecution.owner("plot.py")
    old = {"execution_owner": identity, "receipt": {"receipt_id": "old-receipt"}}
    # Hello/launch is not proof of user code; explicit historical refs survive it.
    session.note_hello({"pid": 42})
    assert m._svg_snapshot_matches_current(root, "old-B", old, explicit=True)
    session._on_frame(
        {
            "bridge_event": "execution_started",
            "script": "plot.py",
            "execution_id": executionfact.new_id(),
        }
    )
    assert not m._svg_snapshot_matches_current(root, "old-B", old, explicit=True)
    assert session.figure_snapshot_invalidation["scope"] == "owner"
    assert session.figure_snapshot_invalidation["source_ids"] == []
    for _ in range(2):
        session._on_frame({"bridge_event": "barrier", "stems": []})
        receipt_id = m._execution_receipt(session, "plot.py").receipt_id
        assert ctx.svg_executions.admit(identity, session, session.execution_id, receipt_id)
        record = {"execution_owner": identity, "receipt": {"receipt_id": receipt_id}}
        assert m._svg_snapshot_matches_current(root, "old-B", record, explicit=True)
        previous = session.execution_id
        session._on_frame(
            {
                "bridge_event": "released",
                "script": "plot.py",
                "execution_id": executionfact.new_id(),
            }
        )
        assert session.state == nativesession.RUNNING_SCRIPT
        assert session.execution_id != previous
        assert not m._svg_snapshot_matches_current(root, "old-B", record, explicit=True)


def test_native_unknown_configuration_only_revokes_its_accepted_exact_sources(project):
    _, root = project
    ctx = m.current_ctx()
    session = native_session(root, arg_count=2)
    session._on_frame(
        {
            "bridge_event": "execution_started",
            "script": "plot.py",
            "execution_id": executionfact.new_id(),
        }
    )
    assert ctx.svg_executions.owners == {}
    assert session.figure_snapshot_invalidation["scope"] == "sources"
    assert session.figure_snapshot_invalidation["source_ids"] == []
    session._on_frame({"bridge_event": "barrier", "stems": ["B"]})
    ctx.svg_snapshot_native_sources["accepted-B"] = (session, "plot.py")
    ctx.svg_snapshot_receipts["accepted-B"] = m._execution_receipt(session, "plot.py").receipt_id
    other_session = native_session(root)
    ctx.svg_snapshot_native_sources["other-B"] = (other_session, "plot.py")
    ctx.svg_snapshot_receipts["other-B"] = "other-receipt"
    session._on_frame(
        {"bridge_event": "released", "script": "plot.py", "execution_id": executionfact.new_id()}
    )
    fact = session.figure_snapshot_invalidation
    assert fact["scope"] == "sources" and fact["source_ids"] == []
    assert fact["receipt_ids"] == [ctx.svg_snapshot_receipts["accepted-B"]]
    assert ctx.svg_executions.owners == {}
    # Another owner may subsequently acquire the same static source name.
    ctx.svg_snapshot_native_sources["accepted-B"] = (other_session, "plot.py")
    session._on_frame({"bridge_event": "barrier", "stems": []})
    session._on_frame(
        {"bridge_event": "released", "script": "plot.py", "execution_id": executionfact.new_id()}
    )
    assert session.figure_snapshot_invalidation["source_ids"] == []
    assert session.figure_snapshot_invalidation["receipt_ids"] == fact["receipt_ids"]


def test_native_conflicting_unaccepted_session_cannot_retire_another_owner(project, monkeypatch):
    from tavotto.engine import nativesession

    _, root = project
    session = native_session(root)
    monkeypatch.setattr(nativesession.REGISTRY, "script_route_conflict", lambda *a, **kw: True)
    session._on_frame(
        {
            "bridge_event": "execution_started",
            "script": "plot.py",
            "execution_id": executionfact.new_id(),
        }
    )
    assert session.execution_id is not None
    assert m.current_ctx().svg_executions.owners == {}
    assert session.figure_snapshot_invalidation is None


def test_legacy_ownerless_runtime_history_only_revoked_by_proven_matching_owner(
    project, monkeypatch
):
    client, root = project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    original_save = m._save_svg_snapshot

    def legacy_save(frozen, **kwargs):
        return original_save(frozen, **{**kwargs, "execution_owner": None})

    monkeypatch.setattr(m, "_save_svg_snapshot", legacy_save)
    first = run(client, root, "both")
    source = next(d["asset_id"] for d in first["descriptors"] if d["stem"] == "B")
    reference = saved_reference(client, source)
    other_run = run(client, root, "both", argv=["--other"])
    other_source = next(d["asset_id"] for d in other_run["descriptors"] if d["stem"] == "B")
    other_ref = saved_reference(client, other_source, "other")
    assert "executionOwner" not in reference and "executionOwner" not in other_ref
    monkeypatch.setattr(m, "_save_svg_snapshot", original_save)
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    m.open_project(str(root))
    assert exported(client, source, reference)["status"] == "done"
    assert exported(client, other_source, other_ref)["status"] == "done"
    run(client, root, "one")
    # B disappeared from the current registry. Its cached descriptor must still
    # positively recompute the exact runtime ID; no opaque-ID parsing is allowed.
    assert m.engine_runtimeasset.resolve(source, m.current_registry()) is None
    assert exported(client, source, reference)["status"] == "failed"
    assert exported(client, other_source, other_ref)["status"] == "done"
    unrelated = {"receipt": {"receipt_id": "unrelated historical receipt"}}
    assert m._svg_snapshot_matches_current(root, "external.pdf", unrelated, explicit=True)
    monkeypatch.setattr(
        m.engine_runtimeasset,
        "load_metadata",
        lambda *args: {"descriptor": {"script": "plot.py", "stem": "different"}},
    )
    assert m._svg_legacy_runtime_owner(root, source) is None


@pytest.fixture
def manual_watch_project(tmp_path, monkeypatch, caplog):
    from tavotto.engine import project_watch

    monkeypatch.setattr(project_watch.ProjectWatcher, "run", lambda self: self.stop_event.wait())
    caplog.set_level(logging.INFO, logger="tavotto")
    yield from svg_cases.project.__wrapped__(tmp_path, monkeypatch)


def test_late_watcher_notifications_preserve_both_captured_figures(
    manual_watch_project, monkeypatch
):
    """Drive the real script and registry notification outlets between A and B."""
    from tavotto.engine import project_watch

    client, root = manual_watch_project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    watcher = project_watch.watcher_of(root)
    clock = [0.0]
    watcher.clock = lambda: clock[0]
    capture = m._capture_svg_snapshot
    observed = []

    def capture_then_poll(worker, descriptor):
        capture(worker, descriptor)
        if descriptor["stem"] == "A" and not observed:
            observed.append(worker)
            watcher.poll()
            clock[0] += 1
            watcher.poll()
            assert pool.peek("plot.py", root) is worker
            assert worker.alive()

    monkeypatch.setattr(m, "_capture_svg_snapshot", capture_then_poll)
    first = run(client, root, "both")
    assert len(observed) == 1
    for descriptor in first["descriptors"]:
        reference = saved_reference(client, descriptor["asset_id"], name=descriptor["stem"])
        assert reference["executionOwner"] == {"script": "plot.py", "run_config": None}
    # A later real edit must still retire this worker; the earlier notification
    # being harmless does not authorize future source changes.
    (root / "plot.py").write_text(SCRIPT + "\n# after successful capture\n", encoding="utf-8")
    watcher.poll()
    clock[0] += 1
    watcher.poll()
    assert pool.peek("plot.py", root) is None


def test_unchanged_preparation_reuses_current_multifigure_worker(manual_watch_project):
    client, root = manual_watch_project
    (root / "plot.py").write_text(SCRIPT, encoding="utf-8")
    first = run(client, root, "both")
    assert len(first["descriptors"]) == 2
    worker = pool.peek("plot.py", root)
    execution = worker.execution_id
    report = client.post("/api/engine/preparation-sessions", json={"script": "plot.py"}).get_json()
    (action,) = [item for item in report["actions"] if item["kind"] == "run"]
    url = f"/api/engine/preparation-sessions/{report['session_id']}"
    response = client.post(
        f"{url}/actions",
        json={"action_id": action["id"], "expected_config_revision": report["config_revision"]},
    )
    assert response.status_code == 202, response.get_json()
    deadline = time.monotonic() + 30
    while True:
        final = client.get(url).get_json()
        if final["phase"] not in {"running", "starting_runtime"}:
            break
        assert time.monotonic() < deadline, final
        time.sleep(0.02)
    assert final["phase"] == "completed", final
    assert final["result"]["created_runtime"] is False
    assert pool.peek("plot.py", root) is worker
    assert worker.execution_id == execution
