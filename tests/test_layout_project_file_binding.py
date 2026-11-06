"""项目/格式/工作副本关联；意图顺序与正文 revision、用户编辑谱系分别核对。"""

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
    return directory


def _axis(editor="editor_A", sequence=1):
    return {"editor_id": editor, "sequence": sequence}


def _revision(value):
    return atomicio.revision_of(value.encode())


def _state():
    return json.loads(sessions.state_path().read_bytes())


def _bytes():
    return sessions.state_path().read_bytes(), sessions.backup_path().read_bytes()


def _call(fmt, function, *args, **kwargs):
    with sessions.using_namespace(None if fmt == "legacy" else fmt):
        return getattr(sessions, function)(*args, **kwargs)


def _read(fmt="legacy", project="/p", doc="d"):
    return _call(fmt, "read_project_file", project, doc)


def _edit(axis, base_axis, base_revision):
    return {"axis": axis, "base_axis": base_axis, "base_revision": base_revision}


def _produce(path, body, edit, fmt="legacy", project="/p", doc="d"):
    # Real bytes and existing atomic writer. app owns the surrounding document lock.
    actual = atomicio.existing_revision(path)
    prepared = _call(
        fmt, "prepare_project_file_edit", doc, project, edit, actual_base_revision=actual
    )
    if prepared["state"] != "accepted":
        return prepared
    atomicio.write_bytes(path, body.encode())
    revision = atomicio.existing_revision(path)
    result = _call(
        fmt,
        "commit_slot",
        doc,
        project,
        revision,
        project_file_edit=edit,
        actual_base_revision=actual,
    )
    return {**result, "state": result["project_file"]["state"]}


def _reserve(name="A", fmt="legacy", project="/p", doc="d", expected=None):
    current = _read(fmt, project, doc)
    result = _call(
        fmt,
        "reserve_project_file",
        project,
        doc,
        name=name,
        file=f"tavottofile/{name}.json",
        autosave_revision=current["edit"]["autosave_revision"],
        axis=current["edit"]["axis"],
        expected_binding_sequence=expected,
    )
    assert result["state"] == "reserved", result
    return result["ticket"]


def _bind(name="A", fmt="legacy", project="/p", doc="d", expected=None):
    ticket = _reserve(name, fmt, project, doc, expected)
    result = _call(fmt, "commit_project_file", project, doc, ticket, revision=_revision(name))
    assert result["state"] == "bound"
    return result["binding"]


def test_unused_feature_keeps_default_payload_and_no_new_block():
    sessions.set_last("/p", "d", "Doc")
    sessions.commit_slot("d", "/p", _revision("working"))
    before = _bytes()
    assert sessions.PROJECT_FILES_KEY not in _state()
    result = _read()
    assert result["binding"] is None
    assert result["edit"]["known"] is False
    assert _bytes() == before
    assert set(sessions.view_for("/p")) == {"last", "prev", "pending", "index"}
    assert set(sessions.commit_slot("d", "/p", _revision("working"))) == {"owner", "promoted"}
    assert _bytes() == before


def test_reserve_does_not_retarget_edit_axis_or_open_intent_then_commit_binds():
    sessions.set_last("/p", "d", "Doc")
    before_open = sessions.view_for("/p", include_open_intent=True)
    before = _read()
    ticket = _reserve()
    during = _read()
    assert during["binding"] == before["binding"] is None
    assert during["edit"] == before["edit"]
    assert during["intent_sequence"] == 1
    assert sessions.view_for("/p", include_open_intent=True) == before_open
    result = sessions.commit_project_file("/p", "d", ticket, revision=_revision("A"))
    assert result["binding"] == {
        "sequence": 1,
        "name": "A",
        "file": "tavottofile/A.json",
        "revision": _revision("A"),
        "dirty": False,
    }
    assert _read()["binding"] == result["binding"]


@pytest.mark.parametrize("b_succeeds", [False, True])
@pytest.mark.parametrize("initially_bound", [False, True])
def test_A_inflight_B_reserved_A_finishes_first_never_wins_even_if_B_fails(
    b_succeeds, initially_bound
):
    first = _bind("Original") if initially_bound else None
    expected = first["sequence"] if first else None
    a = _reserve("A", expected=expected)
    b = _reserve("B", expected=expected)
    before = _bytes()
    assert (
        sessions.commit_project_file("/p", "d", a, revision=_revision("A"))["state"] == "superseded"
    )
    assert _bytes() == before and _read()["binding"] == first
    if b_succeeds:
        assert (
            sessions.commit_project_file("/p", "d", b, revision=_revision("B"))["state"] == "bound"
        )
        assert _read()["binding"]["name"] == "B"
    else:
        assert sessions.cancel_project_file("/p", "d", b)["state"] == "cancelled"
        assert _read()["binding"] == first
    after = _bytes()
    assert (
        sessions.commit_project_file("/p", "d", a, revision=_revision("A"))["state"] == "superseded"
    )
    assert _bytes() == after


def test_none_is_unbound_CAS_and_ABA_receipt_cannot_match_rebound_file():
    a = _bind("A")
    assert (
        sessions.reserve_project_file(
            "/p", "d", name="B", file="tavottofile/B.json", autosave_revision=None, axis=None
        )["state"]
        == "superseded"
    )
    old_a = _reserve("A", expected=a["sequence"])
    b = _bind("B", expected=a["sequence"])
    new_a = _bind("A", expected=b["sequence"])
    before = _bytes()
    assert (
        sessions.commit_project_file("/p", "d", old_a, revision=_revision("old A"))["state"]
        == "superseded"
    )
    assert _read()["binding"] == new_a and _bytes() == before


@pytest.mark.parametrize(
    "field,bad",
    [
        ("name", "B"),
        ("file", "tavottofile/B.json"),
        ("axis", {"editor_id": "other", "sequence": 3}),
        ("autosave_revision", "f" * 32),
        ("binding_sequence", 7),
    ],
)
def test_ticket_must_equal_complete_server_pending(field, bad):
    ticket = _reserve()
    modified = {**ticket, field: bad}
    before = _bytes()
    assert (
        sessions.commit_project_file("/p", "d", modified, revision=_revision("A"))["state"]
        == "superseded"
    )
    assert _bytes() == before and _read()["binding"] is None
    assert (
        sessions.commit_project_file("/p", "d", ticket, revision=_revision("A"))["state"] == "bound"
    )


def test_save_axis5_then_late_axis4_cannot_write_old_body_or_dirty_the_binding(tmp_path):
    path = tmp_path / "working.json"
    t3, t4, t5 = _axis(sequence=3), _axis(sequence=4), _axis(sequence=5)
    assert _produce(path, "body3", _edit(t3, None, None))["project_file"]["state"] == "accepted"
    r3 = atomicio.existing_revision(path)
    old_request = _edit(t4, t3, r3)
    assert _produce(path, "body5", _edit(t5, t3, r3))["project_file"]["state"] == "accepted"
    binding = _bind()
    assert not binding["dirty"]
    before = path.read_bytes(), _bytes()
    assert _produce(path, "stale body4", old_request)["state"] == "superseded"
    # Even laundering the body revision/base axis cannot make an older same-editor snapshot newer.
    rebased = _edit(t4, t5, atomicio.existing_revision(path))
    assert _produce(path, "stale body4", rebased)["state"] == "superseded"
    assert (path.read_bytes(), _bytes()) == before
    assert _read()["binding"] == binding


def test_two_editors_sequence5_are_a_fork_not_max5_and_explicit_rebase_is_required(tmp_path):
    path = tmp_path / "working.json"
    a5, b5 = _axis("editor_A", 5), _axis("editor_B", 5)
    assert _produce(path, "A5", _edit(a5, None, None))["project_file"]["state"] == "accepted"
    original = _bind()
    revision = atomicio.existing_revision(path)
    before = path.read_bytes(), _bytes()
    assert _produce(path, "B5", _edit(b5, None, revision))["state"] == "superseded"
    assert (path.read_bytes(), _bytes()) == before
    assert (
        _produce(path, "B5 explicitly chosen", _edit(b5, a5, revision))["project_file"]["state"]
        == "accepted"
    )
    current = _read()
    assert current["edit"]["axis"] == b5
    assert current["binding"]["dirty"] is True
    assert current["binding"]["revision"] == original["revision"]


def test_derived_autosave_reuses_user_axis_and_does_not_change_target_baseline(tmp_path):
    path = tmp_path / "working.json"
    axis = _axis(sequence=5)
    _produce(path, "user5", _edit(axis, None, None))
    original = _bind()
    before_open = sessions.view_for("/p", include_open_intent=True)
    revision = atomicio.existing_revision(path)
    _produce(path, "derived metadata changed", _edit(axis, axis, revision))
    assert _read()["binding"] == original
    assert sessions.view_for("/p", include_open_intent=True) == before_open
    # Observe an external named-file modification without ever giving it to metadata as a save receipt.
    named = tmp_path / "A.json"
    named.write_bytes(b"external edit")
    assert atomicio.existing_revision(named) != original["revision"]
    assert _read()["binding"]["revision"] == original["revision"]


def test_edit_during_named_save_keeps_new_target_dirty(tmp_path):
    path = tmp_path / "working.json"
    t1, t2 = _axis(sequence=1), _axis(sequence=2)
    _produce(path, "body1", _edit(t1, None, None))
    ticket = _reserve()
    _produce(path, "body2", _edit(t2, t1, atomicio.existing_revision(path)))
    result = sessions.commit_project_file("/p", "d", ticket, revision=_revision("named body1"))
    assert result["binding"]["dirty"] is True
    assert result["edit"]["axis"] == t2


def test_external_autosave_drift_returns_unconfirmed_before_overwriting_body(tmp_path):
    path = tmp_path / "working.json"
    t1, t2 = _axis(sequence=1), _axis(sequence=2)
    _produce(path, "body1", _edit(t1, None, None))
    original = _bind()
    path.write_bytes(b"external AS body")
    actual = atomicio.existing_revision(path)
    before = path.read_bytes(), _bytes()
    assert (
        _produce(path, "user force request without valid lineage", _edit(t2, t1, actual))["state"]
        == "unconfirmed"
    )
    assert (path.read_bytes(), _bytes()) == before
    assert _read()["binding"]["revision"] == original["revision"]
    observed = sessions.read_project_file("/p", "d", actual_autosave_revision=actual)
    assert observed["edit"]["known"] is False and observed["binding"]["dirty"] is True
    assert observed["binding"]["revision"] == original["revision"]
    assert _bytes() == before[1]


def test_legacy_client_write_marks_axis_unknown_and_dirty_without_retargeting(tmp_path):
    path = tmp_path / "working.json"
    t1 = _axis()
    _produce(path, "body1", _edit(t1, None, None))
    original = _bind()
    atomicio.write_bytes(path, b"old client body")
    result = sessions.commit_slot("d", "/p", atomicio.existing_revision(path))
    assert set(result) == {"owner", "promoted"}
    current = _read()
    assert not current["edit"]["known"] and current["binding"]["dirty"]
    assert current["binding"]["revision"] == original["revision"]
    before = _bytes()
    assert (
        sessions.reserve_project_file(
            "/p",
            "d",
            name="B",
            file="tavottofile/B.json",
            autosave_revision=atomicio.existing_revision(path),
            axis=t1,
            expected_binding_sequence=original["sequence"],
        )["state"]
        == "unconfirmed"
    )
    assert _bytes() == before


def test_clear_client_state_and_restart_inherits_axis_until_first_real_edit(tmp_path):
    path = tmp_path / "working.json"
    old = _axis("old_editor", 5)
    _produce(path, "body5", _edit(old, None, None))
    original = _bind()
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import json,sys;sys.path.insert(0,sys.argv[1]);"
            "from tavotto.engine import layoutsession as ls;"
            "print(json.dumps(ls.read_project_file('/p','d')))",
            str(Path(__file__).resolve().parents[1] / "src"),
        ],
        env=dict(os.environ),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    restored = json.loads(process.stdout)
    assert restored["edit"]["axis"] == old and restored["binding"] == original
    # New client first sends a derived autosave using the inherited identity.
    _produce(path, "derived after restart", _edit(old, old, atomicio.existing_revision(path)))
    assert not _read()["binding"]["dirty"]
    new = _axis("new_editor", 1)
    _produce(path, "first actual new edit", _edit(new, old, atomicio.existing_revision(path)))
    assert _read()["binding"]["dirty"] and _read()["edit"]["axis"] == new


def test_same_working_id_in_projects_and_formats_has_independent_associations():
    a = _bind("A", project="/project_A")
    b = _bind("B", project="/project_B")
    c = _bind("C", fmt="v4", project="/project_A")
    assert _read(project="/project_A")["binding"] == a
    assert _read(project="/project_B")["binding"] == b
    assert _read("v4", "/project_A")["binding"] == c


def test_metadata_failure_after_body_success_preserves_target_and_does_not_undo_body(
    tmp_path, monkeypatch
):
    path = tmp_path / "working.json"
    t1, t2 = _axis(sequence=1), _axis(sequence=2)
    _produce(path, "body1", _edit(t1, None, None))
    original = _bind()
    actual = atomicio.existing_revision(path)
    edit = _edit(t2, t1, actual)
    assert (
        sessions.prepare_project_file_edit("d", "/p", edit, actual_base_revision=actual)["state"]
        == "accepted"
    )
    atomicio.write_bytes(path, b"new body really saved")
    before = _bytes()
    original_write = atomicio.write_json

    def failure(target, *args, **kwargs):
        if target == sessions.state_path():
            raise OSError("metadata publication failed")
        return original_write(target, *args, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", failure)
    with pytest.raises(OSError):
        sessions.commit_slot(
            "d",
            "/p",
            atomicio.existing_revision(path),
            project_file_edit=edit,
            actual_base_revision=actual,
        )
    assert path.read_bytes() == b"new body really saved"
    assert _bytes() == before and _read()["binding"] == original
    actual_view = sessions.read_project_file(
        "/p", "d", actual_autosave_revision=atomicio.existing_revision(path)
    )
    assert not actual_view["edit"]["known"] and actual_view["binding"]["dirty"]


def test_read_actual_absence_is_distinct_from_evidence_not_provided(tmp_path):
    path = tmp_path / "working.json"
    _produce(path, "body", _edit(_axis(), None, None))
    _bind()
    before = _bytes()
    assert _read()["edit"]["known"] is True
    missing = sessions.read_project_file("/p", "d", actual_autosave_revision=None)
    assert missing["edit"]["known"] is False and missing["binding"]["dirty"] is True
    assert _bytes() == before


def test_pending_lookup_uses_server_ticket_and_canonical_endpoint_target_only():
    ticket = _reserve()
    before = _bytes()
    found = sessions.pending_project_file(
        "/p", "d", ticket["sequence"], name="A", file="tavottofile/A.json"
    )
    assert found == {"state": "pending", "ticket": ticket}
    found["ticket"]["axis"] = _axis("tampered", 99)
    assert (
        sessions.pending_project_file("/p", "d", ticket["sequence"], name="A", file="B")["state"]
        == "superseded"
    )
    newer = _reserve("B")
    assert (
        sessions.pending_project_file(
            "/p", "d", ticket["sequence"], name="A", file="tavottofile/A.json"
        )["state"]
        == "superseded"
    )
    assert (
        sessions.pending_project_file(
            "/p", "d", newer["sequence"], name="B", file="tavottofile/B.json"
        )["ticket"]
        == newer
    )
    assert before != _bytes()


def test_named_body_success_but_metadata_failure_is_independent_and_can_retry_receipt(
    tmp_path, monkeypatch
):
    original = _bind()
    ticket = _reserve("B", expected=original["sequence"])
    named = tmp_path / "B.json"
    atomicio.write_bytes(named, b"named B really saved")
    revision = atomicio.existing_revision(named)
    before = _bytes()
    real_write = atomicio.write_json
    with monkeypatch.context() as patch:

        def fail(target, *args, **kwargs):
            if target == sessions.state_path():
                raise OSError("binding metadata failed")
            return real_write(target, *args, **kwargs)

        patch.setattr(atomicio, "write_json", fail)
        with pytest.raises(OSError):
            sessions.commit_project_file("/p", "d", ticket, revision=revision)
    assert named.read_bytes() == b"named B really saved"
    assert _bytes() == before and _read()["binding"] == original
    server_ticket = sessions.pending_project_file(
        "/p", "d", ticket["sequence"], name="B", file="tavottofile/B.json"
    )["ticket"]
    result = sessions.commit_project_file("/p", "d", server_ticket, revision=revision)
    assert result["binding"]["name"] == "B" and result["binding"]["revision"] == revision


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"axis": None, "base_revision": None},
        {"axis": None, "base_axis": None, "base_revision": None, "extra": 1},
        {"axis": {"editor_id": "A", "sequence": True}, "base_axis": None, "base_revision": None},
        {"axis": None, "base_axis": None, "base_revision": "bad"},
    ],
)
def test_invalid_edit_is_rejected_before_any_session_write(bad):
    before = sessions.state_path().exists()
    with pytest.raises(ValueError):
        sessions.prepare_project_file_edit("d", "/p", bad, actual_base_revision=None)
    with pytest.raises(ValueError):
        sessions.commit_slot(
            "d", "/p", _revision("body"), project_file_edit=bad, actual_base_revision=None
        )
    assert sessions.state_path().exists() == before


def test_returned_ticket_and_binding_are_deep_copies():
    ticket = _reserve()
    changed = copy.deepcopy(ticket)
    changed["file"] = "B"
    assert (
        sessions.commit_project_file("/p", "d", changed, revision=_revision("A"))["state"]
        == "superseded"
    )
    sessions.commit_project_file("/p", "d", ticket, revision=_revision("A"))
    result = _read()
    result["binding"]["file"] = "B"
    assert _read()["binding"]["file"] == "tavottofile/A.json"


@pytest.mark.parametrize("kind", ["future", "extra", "bad_axis"])
def test_future_or_invalid_block_is_readonly_for_old_and_new_writers(kind):
    _bind()
    data = _state()
    block = data[sessions.PROJECT_FILES_KEY]
    if kind == "future":
        block["version"] = 2
    elif kind == "extra":
        block["unknown"] = True
    else:
        block["projects"]["/p"]["legacy"]["d"]["axis"] = {"editor_id": "E", "sequence": 0}
    atomicio.write_json(sessions.state_path(), data)
    before = _bytes()
    assert _read() == {"version": 1, "status": "unavailable"}
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.set_last("/p", "d", "Name")
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.commit_slot("d", "/p", _revision("new"))
    assert _bytes() == before


def test_delete_working_copy_invalidates_binding_tickets_and_keeps_other_format():
    a = _bind()
    pending = _reserve("B", expected=a["sequence"])
    other = _bind("Other", fmt="v4")
    sessions.forget_documents(["d"])
    assert _read()["binding"] is None
    assert (
        sessions.commit_project_file("/p", "d", pending, revision=_revision("B"))["state"]
        == "superseded"
    )
    assert _read("v4")["binding"] == other


def test_future_binding_only_in_backup_does_not_get_overwritten_as_first_run():
    _bind()
    future = _state()
    future[sessions.PROJECT_FILES_KEY]["version"] = 2
    atomicio.write_json(sessions.backup_path(), future)
    sessions.state_path().unlink()
    before = sessions.backup_path().read_bytes()
    assert _read()["status"] == "unavailable"
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.set_last("/p", "d", "Name")
    assert not sessions.state_path().exists()
    assert sessions.backup_path().read_bytes() == before


def test_prepare_and_commit_use_identical_lineage_decision_without_owner_or_axis_regression(
    tmp_path,
):
    path = tmp_path / "working.json"
    current = _axis("editor_A", 5)
    _produce(path, "A5", _edit(current, None, None))
    before = _bytes()
    actual = atomicio.existing_revision(path)
    bad = _edit(_axis("editor_B", 5), None, actual)
    assert (
        sessions.prepare_project_file_edit("d", "/p", bad, actual_base_revision=actual)["state"]
        == "superseded"
    )
    result = sessions.commit_slot(
        "d",
        "/p",
        _revision("not actually accepted"),
        project_file_edit=bad,
        actual_base_revision=actual,
    )
    assert result == {
        "owner": "skipped",
        "promoted": False,
        "project_file": {"state": "superseded"},
    }
    assert _bytes() == before


def test_cancel_intent_clears_only_current_pending_preserving_target_axis_and_open_intent(tmp_path):
    path = tmp_path / "working.json"
    _produce(path, "body1", _edit(_axis(), None, None))
    original = _bind()
    sessions.set_last("/p", "d", "Current")
    pending = _reserve("B", expected=original["sequence"])
    expected = _state()
    expected[sessions.PROJECT_FILES_KEY]["projects"]["/p"]["legacy"]["d"]["pending"] = None
    before_open = sessions.view_for("/p", include_open_intent=True)
    before_view = _read()
    assert sessions.cancel_project_file_intent("/p", "d", pending["sequence"]) == {
        "state": "cancelled"
    }
    assert _state() == expected
    assert _read() == before_view
    assert sessions.view_for("/p", include_open_intent=True) == before_open
    before = _bytes()
    assert sessions.cancel_project_file_intent("/p", "d", pending["sequence"]) == {
        "state": "superseded"
    }
    assert _bytes() == before


def test_cancel_intent_late_A_cannot_cancel_B_or_revive_after_B_cancelled():
    a = _reserve("A")
    b = _reserve("B")
    before = _bytes()
    assert sessions.cancel_project_file_intent("/p", "d", a["sequence"])["state"] == "superseded"
    assert _bytes() == before
    assert (
        sessions.pending_project_file(
            "/p", "d", b["sequence"], name="B", file="tavottofile/B.json"
        )["ticket"]
        == b
    )
    assert sessions.cancel_project_file_intent("/p", "d", b["sequence"])["state"] == "cancelled"
    before = _bytes()
    assert (
        sessions.commit_project_file("/p", "d", a, revision=_revision("A"))["state"] == "superseded"
    )
    assert sessions.cancel_project_file_intent("/p", "d", a["sequence"])["state"] == "superseded"
    assert _bytes() == before
    c = _reserve("C")
    assert c["sequence"] == b["sequence"] + 1


def test_cancel_intent_is_scoped_by_project_format_and_working_id():
    contexts = [("legacy", "/p", "d"), ("v4", "/p", "d"), ("legacy", "/other", "d")]
    tickets = [_reserve(fmt=fmt, project=key, doc=doc) for fmt, key, doc in contexts]
    assert {ticket["sequence"] for ticket in tickets} == {1}
    before = _bytes()
    assert sessions.cancel_project_file_intent("/p", "other_doc", 1)["state"] == "superseded"
    assert _bytes() == before
    assert sessions.cancel_project_file_intent("/p", "d", 1)["state"] == "cancelled"
    for (fmt, key, doc), ticket in zip(contexts[1:], tickets[1:]):
        result = _call(
            fmt, "pending_project_file", key, doc, 1, name="A", file="tavottofile/A.json"
        )
        assert result["ticket"] == ticket


@pytest.mark.parametrize("sequence", [None, True, False, 0, -1, 2**53, 1.0, "1"])
def test_cancel_intent_invalid_sequence_rejected_before_read_or_write(sequence):
    with pytest.raises(ValueError):
        sessions.cancel_project_file_intent("/p", "d", sequence)
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()


def test_cancel_intent_missing_record_does_not_create_feature_block():
    assert sessions.cancel_project_file_intent("/p", "d", 1) == {"state": "superseded"}
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()


def test_cancel_intent_future_block_refuses_write_and_preserves_pending_bytes():
    _reserve()
    future = _state()
    future[sessions.PROJECT_FILES_KEY]["version"] = 2
    atomicio.write_json(sessions.state_path(), future)
    before = _bytes()
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.cancel_project_file_intent("/p", "d", 1)
    assert _bytes() == before


def test_full_ticket_cancel_still_rejects_altered_ticket_then_intent_cancel_uses_server_ticket():
    ticket = _reserve()
    before = _bytes()
    modified = {**ticket, "name": "B", "file": "tavottofile/B.json"}
    assert sessions.cancel_project_file("/p", "d", modified) == {"state": "superseded"}
    assert _bytes() == before
    assert sessions.cancel_project_file_intent("/p", "d", ticket["sequence"]) == {
        "state": "cancelled"
    }


@pytest.mark.parametrize("revision", [None, "a" * 32])
def test_public_edit_validator_is_pure_and_preserves_closed_valid_input(revision):
    edit = _edit(_axis(), None, revision)
    before = copy.deepcopy(edit)
    assert sessions.validate_project_file_edit(edit, revision) is None
    assert edit == before
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()


@pytest.mark.parametrize(
    "edit,actual",
    [
        (None, None),
        ({}, None),
        ({"axis": None, "base_axis": None}, None),
        ({"axis": None, "base_axis": None, "base_revision": None, "extra": 1}, None),
        (_edit({"editor_id": "A", "sequence": True}, None, None), None),
        (_edit(None, {"editor_id": "A", "sequence": 0}, None), None),
        (_edit(None, None, "bad"), None),
        (_edit(None, None, None), "bad"),
    ],
)
def test_public_edit_validator_rejects_invalid_input_without_project_or_state(edit, actual):
    with pytest.raises(ValueError):
        sessions.validate_project_file_edit(edit, actual)
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()


@pytest.mark.parametrize("actual", [None, "f" * 32])
def test_reserve_actual_revision_mismatch_is_unconfirmed_and_zero_write(tmp_path, actual):
    path = tmp_path / "working.json"
    _produce(path, "body", _edit(_axis(), None, None))
    current = _read()
    original = _bind()
    existing = _reserve("Pending", expected=original["sequence"])
    before = _bytes(), _read()
    response = sessions.reserve_project_file(
        "/p",
        "d",
        name="B",
        file="tavottofile/B.json",
        autosave_revision=current["edit"]["autosave_revision"],
        axis=current["edit"]["axis"],
        expected_binding_sequence=original["sequence"],
        actual_autosave_revision=actual,
    )
    assert response == {"state": "unconfirmed"}
    assert (_bytes(), _read()) == before
    assert (
        sessions.pending_project_file(
            "/p", "d", existing["sequence"], name="Pending", file="tavottofile/Pending.json"
        )["ticket"]
        == existing
    )


def test_reserve_explicit_real_absence_is_not_omitted_evidence_and_does_not_create_record():
    result = sessions.reserve_project_file(
        "/p",
        "d",
        name="A",
        file="tavottofile/A.json",
        autosave_revision=None,
        axis=None,
        actual_autosave_revision=_revision("real but not tracked body"),
    )
    assert result == {"state": "unconfirmed"}
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()
    reserved = sessions.reserve_project_file(
        "/p",
        "d",
        name="A",
        file="tavottofile/A.json",
        autosave_revision=None,
        axis=None,
        actual_autosave_revision=None,
    )
    assert reserved["state"] == "reserved" and reserved["ticket"]["sequence"] == 1


def test_reserve_matching_real_revision_reserves_exact_captured_axis(tmp_path):
    path = tmp_path / "working.json"
    _produce(path, "body", _edit(_axis(), None, None))
    actual = atomicio.existing_revision(path)
    response = sessions.reserve_project_file(
        "/p",
        "d",
        name="A",
        file="tavottofile/A.json",
        autosave_revision=actual,
        axis=_axis(),
        actual_autosave_revision=actual,
    )
    assert response["state"] == "reserved"
    assert response["ticket"]["autosave_revision"] == actual
    assert response["ticket"]["axis"] == _axis()


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"file": ""},
        {"axis": {"editor_id": "A", "sequence": True}},
        {"expected_binding_sequence": True},
        {"autosave_revision": "invalid"},
        {"actual_autosave_revision": "invalid"},
    ],
)
def test_reserve_invalid_fields_raise_before_mismatch_can_return_unconfirmed(changes):
    request = {
        "name": "A",
        "file": "tavottofile/A.json",
        "autosave_revision": None,
        "axis": None,
        "actual_autosave_revision": _revision("mismatch"),
        **changes,
    }
    with pytest.raises(ValueError):
        sessions.reserve_project_file("/p", "d", **request)
    assert not sessions.state_path().exists()
    assert not sessions.backup_path().exists()


def test_reserve_actual_mismatch_cannot_bypass_future_metadata_guard():
    _reserve()
    future = _state()
    future[sessions.PROJECT_FILES_KEY]["version"] = 2
    atomicio.write_json(sessions.state_path(), future)
    before = _bytes()
    with pytest.raises(sessions.SessionStateUnavailable):
        sessions.reserve_project_file(
            "/p",
            "d",
            name="A",
            file="tavottofile/A.json",
            autosave_revision=None,
            axis=None,
            actual_autosave_revision=_revision("mismatch"),
        )
    assert _bytes() == before
