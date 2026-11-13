"""同项目跨格式的明确打开序；正文保存、旧 writer 与迟到补推没有新的打开权。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import atomicio, layoutsession as sessions


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    directory = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(directory))
    monkeypatch.setattr(sessions, "_now_ms", lambda: 1000)
    yield directory
    assert sessions._NAMESPACE.get() is None


def _raw():
    return json.loads(sessions.state_path().read_bytes())


def _bytes():
    return sessions.state_path().read_bytes(), sessions.backup_path().read_bytes()


def _write_raw(value):
    atomicio.write_json(sessions.state_path(), value, indent=1)


def _call(fmt, function, *args, **kwargs):
    with sessions.using_namespace(None if fmt == "legacy" else fmt):
        return getattr(sessions, function)(*args, **kwargs)


def _view(fmt="legacy", project="/p"):
    return _call(fmt, "view_for", project, include_open_intent=True)


def _intent(fmt="legacy", project="/p"):
    return _view(fmt, project)["open_intent"]


def _open(fmt, doc="d_chart", project="/p", **kwargs):
    return _call(fmt, "set_last", project, doc, doc, **kwargs)


def _row(project="/p"):
    ledger = _raw()[sessions.OPEN_INTENTS_KEY]
    return ledger["no_project"] if project is None else ledger["projects"][project]


def _drift_legacy():
    value = _raw()
    value["projects"]["/p"]["last"] = {"doc_id": "d_old_writer", "name": "旧版", "at": 9000}
    _write_raw(value)


def test_legacy_only_bytes_default_dictionary_and_explicit_untracked(data_dir):
    _open("legacy", "d_legacy")
    expected = {
        "version": 1,
        "projects": {"/p": {"last": {"doc_id": "d_legacy", "name": "d_legacy", "at": 1000}}},
        "no_project": {},
        "owners": {},
        "owner_revisions": {},
    }
    assert sessions.state_path().read_bytes() == atomicio.dumps_json(expected, indent=1)
    ordinary = sessions.view_for("/p")
    assert set(ordinary) == {"last", "prev", "pending", "index"}
    before = _bytes()
    explicit = _view()
    assert {key: value for key, value in explicit.items() if key != "open_intent"} == ordinary
    assert set(explicit["open_intent"]) == {"version", "status", "snapshot"}
    assert explicit["open_intent"]["status"] == "untracked"
    assert _intent("v4") == explicit["open_intent"]
    assert before == _bytes()
    assert sessions.OPEN_INTENTS_KEY not in _raw()


@pytest.mark.parametrize("project", ["/p", None])
def test_rollback_orders_explicit_opens_across_formats_and_same_id(monkeypatch, project):
    monkeypatch.setattr(sessions, "_now_ms", lambda: 10000)
    _open("legacy", "d_same", project)
    monkeypatch.setattr(sessions, "_now_ms", lambda: 500)
    _open("v4", "d_same", project)
    first = _intent("legacy", project)
    assert first == _intent("v4", project)
    assert first["latest"] == {"document_format": "v4", "doc_id": "d_same", "pointer": "last"}
    assert first["sequence"] == 1
    assert _view("legacy", project)["last"]["at"] > _view("v4", project)["last"]["at"]
    _open("legacy", "d_same", project)
    second = _intent("legacy", project)
    assert second["sequence"] == 2
    assert second["latest"]["document_format"] == "legacy"
    assert first["snapshot"] != second["snapshot"]
    assert "open_intent" not in _call("v4", "view_for", project)


@pytest.mark.parametrize("pending_fmt,later_fmt", [("legacy", "v4"), ("v4", "legacy")])
def test_late_first_save_never_allocates_or_steals_open_intent(monkeypatch, pending_fmt, later_fmt):
    _open(pending_fmt, "d_pending", slot_ok=False)
    _open(later_fmt, "d_later")
    prior = _intent()
    monkeypatch.setattr(sessions, "_now_ms", lambda: 50000)
    assert _call(pending_fmt, "commit_slot", "d_pending", "/p", "body-revision")["promoted"]
    after = _intent()
    assert after["status"] == "ok"
    assert after["sequence"] == prior["sequence"]
    assert after["latest"] == prior["latest"]
    assert after["snapshot"] != prior["snapshot"]
    assert _view(pending_fmt)["last"]["at"] > _view(later_fmt)["last"]["at"]


def test_reopening_identical_pending_after_other_format_is_a_new_intent():
    _open("legacy", "d_pending", slot_ok=False)
    _open("v4", "d_later")
    previous = _intent()
    previous_local = _view()["pending"]
    _open("legacy", "d_pending", slot_ok=False)
    current = _intent()
    assert current["sequence"] == previous["sequence"] + 1
    assert current["latest"] == {
        "document_format": "legacy",
        "doc_id": "d_pending",
        "pointer": "pending",
    }
    assert _view()["pending"] == previous_local


def test_untracked_existing_v4_then_explicit_legacy_open_establishes_order():
    _open("v4", "d_chart")
    value = _raw()
    del value[sessions.OPEN_INTENTS_KEY]
    _write_raw(value)
    untracked = _intent()
    assert untracked["status"] == "untracked"
    _open("legacy", "d_legacy")
    assert _intent()["latest"]["document_format"] == "legacy"
    assert _intent()["sequence"] == 1


def test_untracked_snapshot_changes_for_actual_other_namespace_pointers():
    _open("legacy", "d_a")
    prior = _intent("v4")
    _open("legacy", "d_b")
    assert _intent("v4")["status"] == "untracked"
    assert _intent("v4")["snapshot"] != prior["snapshot"]


def test_owner_only_updates_and_other_projects_do_not_stale_or_change_snapshot():
    _open("v4")
    prior = _intent()
    _call("legacy", "record_owner", "d_legacy", "/p", "revision")
    _call("v4", "commit_slot", "d_chart", "/p", "revision-a")
    _call("v4", "commit_slot", "d_chart", "/p", "revision-b")
    _open("legacy", "d_other", "/other")
    assert _intent() == prior


def test_old_pointer_write_is_stale_and_ordinary_save_does_not_launder_it():
    _open("legacy", "d_legacy")
    _open("v4")
    prior = copy.deepcopy(_row())
    _drift_legacy()
    stale = _intent()
    assert set(stale) == {"version", "status", "snapshot"}
    assert stale["status"] == "stale"
    _call("v4", "commit_slot", "d_chart", "/p", "new-body")
    assert _row() == prior
    assert _intent() == stale
    _open("v4")
    assert _intent()["status"] == "ok"
    assert _intent()["sequence"] == prior["sequence"] + 1


def test_matching_pending_save_confirms_same_sequence():
    _open("v4", slot_ok=False)
    prior = _intent()
    _call("v4", "commit_slot", "d_chart", "/p", "body")
    after = _intent()
    assert after["sequence"] == prior["sequence"]
    assert after["latest"]["pointer"] == "last"
    assert after["snapshot"] != prior["snapshot"]


def test_repair_confirms_pending_without_new_sequence_and_durable_repair_is_noop():
    _open("v4", slot_ok=False)
    seq = _intent()["sequence"]
    result = _open("v4", repair=True)
    assert result["state"] == "durable"
    assert _intent()["sequence"] == seq
    assert _intent()["latest"]["pointer"] == "last"
    before = _bytes()
    assert _open("v4", repair=True)["state"] == "durable"
    assert _bytes() == before


def test_repair_slot_check_runs_under_real_session_lock_and_cannot_publish_missing_slot():
    _open("v4", slot_ok=False)
    before = _bytes()

    def vanished():
        assert sessions._LOCK._fd is not None
        return False

    result = _open("v4", repair=True, slot_check=vanished)
    assert result["state"] == "pending"
    assert result["last"] is None
    assert _bytes() == before


@pytest.mark.parametrize("fmt,doc", [("legacy", "d_chart"), ("v4", "d_other")])
def test_late_repair_cannot_steal_newer_target_even_with_same_doc_id(fmt, doc):
    _open("legacy", "d_chart", slot_ok=False)
    _open("v4", "d_chart")
    before = _bytes()
    assert _open(fmt, doc, repair=True)["state"] == "superseded"
    assert before == _bytes()


@pytest.mark.parametrize("kind", ["never_arrived", "untracked", "stale"])
def test_repair_cannot_establish_first_or_untrusted_v4_intent(kind):
    if kind != "never_arrived":
        _open("v4")
        if kind == "untracked":
            value = _raw()
            del value[sessions.OPEN_INTENTS_KEY]
            _write_raw(value)
        else:
            _open("legacy", "d_legacy")
            _drift_legacy()
    before = _bytes() if sessions.state_path().exists() else None
    assert _open("v4", repair=True)["state"] == "unconfirmed"
    assert (_bytes() if sessions.state_path().exists() else None) == before


def test_legacy_only_repair_keeps_old_behavior_despite_another_projects_v4():
    _open("v4", "d_other", "/other")
    other = _intent("v4", "/other")
    _open("legacy", "d_legacy", slot_ok=False, repair=True)
    assert _view()["pending"]["doc_id"] == "d_legacy"
    assert "/p" not in _raw()[sessions.OPEN_INTENTS_KEY]["projects"]
    assert _open("legacy", "d_legacy", repair=True)["state"] == "durable"
    assert _intent("v4", "/other") == other


@pytest.mark.parametrize("method", ["forget_documents", "delete_slot", "forget_project"])
def test_explicit_delete_clears_latest_and_late_repair_never_resurrects(method):
    _open("legacy", "d_legacy")
    _open("v4")
    seq = _intent()["sequence"]
    if method == "forget_documents":
        _call("v4", method, ["d_chart"])
    elif method == "delete_slot":
        _call("v4", method, "d_chart", lambda: True)
    else:
        _call("v4", method, "/p", ["d_chart"])
    assert _intent()["status"] == "ok"
    assert _intent()["latest"] is None
    assert _intent()["sequence"] == seq
    before = _bytes()
    assert _open("v4", repair=True)["state"] == "superseded"
    assert _bytes() == before


def test_failed_primary_publish_keeps_old_pointers_and_ledger(monkeypatch):
    _open("v4")
    before = _bytes()
    original = atomicio.write_json

    def fail(path, *args, **kwargs):
        if path == sessions.state_path():
            raise OSError("injected primary failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", fail)
    with pytest.raises(OSError):
        _open("legacy", "d_later")
    assert _bytes() == before


def test_pointer_and_intent_are_in_same_existing_publish(monkeypatch):
    _open("legacy", "d_before")
    original = atomicio.write_json
    published = []

    def observe(path, value, **kwargs):
        assert sessions._LOCK._fd is not None
        published.append((path, copy.deepcopy(value)))
        return original(path, value, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", observe)
    _open("v4")
    assert [path for path, _ in published] == [sessions.state_path(), sessions.backup_path()]
    assert published[0][1] == published[1][1]
    value = published[0][1]
    assert value["document_namespaces"]["v4"]["projects"]["/p"]["last"]["doc_id"] == "d_chart"
    assert value[sessions.OPEN_INTENTS_KEY]["projects"]["/p"]["latest"]["doc_id"] == "d_chart"


BAD_LEDGER = [
    None,
    [],
    {},
    {"version": 2, "projects": {}, "no_project": None},
    {"version": True, "projects": {}, "no_project": None},
    {"version": 1.0, "projects": {}, "no_project": None},
    {"version": 1, "projects": {"/p": None}, "no_project": None},
    {"version": 1, "projects": {}, "no_project": None, "future": True},
]


@pytest.mark.parametrize("bad", BAD_LEDGER)
@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_future_or_malformed_ledger_fails_closed_for_default_and_repair(fmt, bad):
    _open("v4")
    value = _raw()
    value[sessions.OPEN_INTENTS_KEY] = bad
    _write_raw(value)
    before = _bytes()
    assert _intent(fmt) == {"version": 1, "status": "unavailable"}
    for repair in (False, True):
        with pytest.raises(sessions.SessionStateUnavailable):
            _open(fmt, "d_new", repair=repair)
        assert _bytes() == before
    with pytest.raises(sessions.SessionStateUnavailable):
        _call(fmt, "record_owner", "d_new", "/p", "revision")
    assert _bytes() == before


@pytest.mark.parametrize(
    "field,bad",
    [
        ("sequence", True),
        ("sequence", 0),
        ("sequence", 1.0),
        ("sequence", 2**53),
        ("witness", "x" * 64),
        ("latest", {}),
        ("latest", {"document_format": "v5", "doc_id": "d", "pointer": "last"}),
        ("latest", {"document_format": "v4", "doc_id": "../d", "pointer": "last"}),
        ("latest", {"document_format": "v4", "doc_id": "d", "pointer": "prev"}),
    ],
)
def test_invalid_closed_record_is_never_normalized_into_old_behavior(field, bad):
    _open("v4")
    value = _raw()
    value[sessions.OPEN_INTENTS_KEY]["projects"]["/p"][field] = bad
    _write_raw(value)
    before = _bytes()
    assert _intent()["status"] == "unavailable"
    with pytest.raises(sessions.SessionStateUnavailable):
        _open("legacy", "d_new")
    assert _bytes() == before


def test_exhausted_sequence_refuses_new_open_but_allows_confirming_existing_intent():
    _open("v4", slot_ok=False)
    value = _raw()
    value[sessions.OPEN_INTENTS_KEY]["projects"]["/p"]["sequence"] = 2**53 - 1
    _write_raw(value)
    before = _bytes()
    with pytest.raises(sessions.SessionStateUnavailable):
        _open("legacy", "d_new")
    assert _bytes() == before
    assert _open("v4", repair=True)["state"] == "durable"
    assert _intent()["sequence"] == 2**53 - 1


def test_missing_primary_future_ledger_backup_is_not_overwritten():
    _open("v4")
    value = _raw()
    value[sessions.OPEN_INTENTS_KEY]["version"] = 2
    atomicio.write_json(sessions.backup_path(), value)
    sessions.state_path().unlink()
    before = sessions.backup_path().read_bytes()
    with pytest.raises(sessions.SessionStateUnavailable):
        _open("legacy", "d_new")
    assert not sessions.state_path().exists()
    assert sessions.backup_path().read_bytes() == before


def test_future_ledger_does_not_execute_delete_callback():
    _open("v4")
    value = _raw()
    value[sessions.OPEN_INTENTS_KEY]["version"] = 2
    _write_raw(value)
    before = _bytes()
    removed = []
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.delete_slot("d_chart", lambda: removed.append(True))
    assert removed == []
    assert _bytes() == before


def test_final_view_retries_when_only_other_namespace_changes_during_revision_read():
    _open("legacy", "d_legacy")
    _open("v4", "d_first")
    calls = []

    def revision(doc):
        calls.append(doc)
        if len(calls) == 1:
            _open("v4", "d_second")
        return "body"

    result = sessions.view_for("/p", revision, include_open_intent=True)
    assert len(calls) >= 2
    assert result["index"] == "ok"
    assert result["open_intent"] == _intent("v4")
    assert result["open_intent"]["latest"]["doc_id"] == "d_second"


def test_continually_moving_other_namespace_is_unavailable_not_mixed():
    _open("legacy", "d_legacy")
    _open("v4", "d_first")
    counter = 0

    def revision(_doc):
        nonlocal counter
        counter += 1
        _open("v4", f"d_{counter}")
        return "body"

    result = sessions.view_for("/p", revision, include_open_intent=True)
    assert result["index"] == "degraded"
    assert result["open_intent"] == {"version": 1, "status": "unavailable"}


PROCESS = r"""
import pathlib,sys,time
sys.path.insert(0,sys.argv[1])
from tavotto.engine import layoutsession as ls
marker=pathlib.Path(sys.argv[3])
deadline=time.monotonic()+10
while not marker.exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
with ls.using_namespace(None if sys.argv[2]=='legacy' else 'v4'):
    ls._now_ms=lambda: 1
    for i in range(6):
        ls.set_last('/p','d_'+sys.argv[2]+'_'+str(i),'name')
"""


def test_two_real_processes_allocate_one_serial_order_with_existing_lock(tmp_path):
    _open("v4", "d_initial")
    root = Path(__file__).resolve().parents[1]
    marker = tmp_path / "go"
    processes = []
    try:
        for fmt in ("legacy", "v4"):
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-I", "-c", PROCESS, str(root / "src"), fmt, str(marker)],
                    env=dict(os.environ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            )
        marker.write_bytes(b"go")
        for process in processes:
            _out, errors = process.communicate(timeout=30)
            assert process.returncode == 0, errors
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
    intent = _intent()
    assert intent["status"] == "ok"
    assert intent["sequence"] == 13
    assert intent == _intent("v4")
    assert sessions.state_path().read_bytes() == sessions.backup_path().read_bytes()
    assert {p.name for p in sessions.state_path().parent.iterdir()} == {
        "layout-sessions.json",
        "layout-sessions.json.bak",
        "layout-sessions.lock",
    }
