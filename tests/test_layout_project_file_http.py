"""命名文件关联的真实 HTTP 保存链；绑定意图与用户编辑轴分别验证。

空文档用真实原子落盘；v4 用真实 worker 计算、附件捕获及生产导出。
仅在会话元信息原子写边界注入故障，不替换科学计算生产者。
"""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

import pytest

from tavotto import app as m, security
from tavotto.engine import atomicio, layoutsession, project_watch
from test_chart_documents_v4 import _document as _chart_document, _export_request, _render_request
from test_chart_native_api import _bind, project as _native_project
from test_chart_native_export import _assert_svg_color

project = _native_project


def _axis(editor="editor_A", sequence=1):
    return {"editor_id": editor, "sequence": sequence}


def _document(title="A", *, schema=3, updated=1):
    return {
        "schema": schema,
        "project": {"id": "same_project_label", "name": title},
        "canvases": [{"id": "c1", "name": "Figure 1", "page": {"w": 100, "h": 80}, "objects": []}],
        "activeCanvasId": "c1",
        "createdAt": 0,
        "updatedAt": updated,
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    m.reset_projects()
    data = tmp_path / "data"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    monkeypatch.setattr(m, "LAYOUT_DIR", data / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", data / "layouts/_autosave")
    monkeypatch.setattr(m, "VERSIONS_DIR", data / "layouts/_versions")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setitem(m.app.config, "TESTING", True)
    root = tmp_path / "project"
    root.mkdir()
    project_id = m.open_project(str(root))["id"]
    try:
        yield m.app.test_client(), root, {"X-Tavotto-Project": project_id}
    finally:
        m.reset_projects()
        project_watch.stop()


@pytest.fixture
def native_env(project, tmp_path, monkeypatch):
    client, root = project
    data = tmp_path / "data"
    monkeypatch.setattr(m, "LAYOUT_DIR", data / "layouts")
    monkeypatch.setattr(m, "AUTOSAVE_DIR", data / "layouts/_autosave")
    monkeypatch.setattr(m, "VERSIONS_DIR", data / "layouts/_versions")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    return client, root, {}


def _url(path, fmt="legacy"):
    return ("/api/documents/v4" if fmt == "v4" else "/api") + path


def _read(env, doc_id="working", *, fmt="legacy"):
    client, _root, headers = env
    response = client.get(_url(f"/layout-session/project-file/{doc_id}", fmt), headers=headers)
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    return response.get_json()


def _put(env, document, axis, base_axis, base_revision, *, doc_id="working", fmt="legacy"):
    client, _root, headers = env
    return client.put(
        _url(f"/autosave/{doc_id}", fmt),
        query_string={
            "base_revision": base_revision or "absent",
            "project_file_edit": json.dumps(
                {"axis": axis, "base_axis": base_axis, "base_revision": base_revision}
            ),
        },
        json=document,
        headers=headers,
    )


def _saved(response):
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["ok"] is True
    return result


def _slot(env, doc_id="working", *, fmt="legacy"):
    client, _root, headers = env
    response = client.get(_url(f"/autosave/{doc_id}", fmt), headers=headers)
    assert response.status_code == 200, response.get_json()
    assert response.headers["X-Tavotto-Revision"] == atomicio.revision_of(response.data)
    return response


def _reserve(env, name="A", *, doc_id="working", fmt="legacy", expected=None):
    current = _read(env, doc_id, fmt=fmt)
    client, _root, headers = env
    response = client.post(
        _url(f"/layout-session/project-file/{doc_id}/reserve", fmt),
        json={
            "name": name,
            "autosave_revision": current["edit"]["autosave_revision"],
            "axis": current["edit"]["axis"],
            "expected_binding_sequence": expected,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    result = response.get_json()
    assert result["state"] == "reserved", result
    return result["ticket"]


def _named(env, document, name="A", *, base="absent", ticket=None, doc_id="working", fmt="legacy"):
    client, _root, headers = env
    query = {"target": "project", "base_revision": base}
    if ticket is not None:
        query.update(working_doc_id=doc_id, binding_intent=str(ticket["sequence"]))
    return client.post(
        _url(f"/layouts/{quote(name)}", fmt), query_string=query, json=document, headers=headers
    )


def _confirm(env, name, ticket, revision, *, doc_id="working", fmt="legacy"):
    client, _root, headers = env
    return client.post(
        _url(f"/layout-session/project-file/{doc_id}/confirm", fmt),
        json={"name": name, "intent_sequence": ticket["sequence"], "revision": revision},
        headers=headers,
    )


def _binding(env, doc_id="working", *, fmt="legacy"):
    return _read(env, doc_id, fmt=fmt)["binding"]


def _cold_read(env, *, fmt="legacy"):
    code = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tavotto import app as m
from tavotto.engine import project_watch
m.LAYOUT_DIR = Path(sys.argv[3])
m.AUTOSAVE_DIR = Path(sys.argv[4])
m.app.config['TESTING'] = True
try:
    project_id = m.open_project(sys.argv[2])['id']
    response = m.app.test_client().get(sys.argv[5], headers={'X-Tavotto-Project': project_id})
    assert response.status_code == 200, (response.status_code, response.get_json())
    print(json.dumps(response.get_json()))
finally:
    m.reset_projects()
    project_watch.stop()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(Path(m.__file__).resolve().parents[1]),
            str(env[1]),
            str(m.LAYOUT_DIR),
            str(m.AUTOSAVE_DIR),
            _url("/layout-session/project-file/working", fmt),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def _bound(env, *, fmt="legacy"):
    document = _document(schema=4 if fmt == "v4" else 3)
    revision = _saved(_put(env, document, _axis(), None, None, fmt=fmt))["revision"]
    ticket = _reserve(env, fmt=fmt)
    named = _saved(_named(env, document, ticket=ticket, fmt=fmt))
    assert named["project_file"]["state"] == "bound", named
    return document, revision, named


def test_retitle_and_cold_http_read_keep_explicit_named_target_and_user_dirty(env):
    document, working_revision, named = _bound(env)
    initial = _binding(env)
    assert initial == {
        "sequence": 1,
        "name": "A",
        "file": "tavottofile/A.json",
        "revision": named["revision"],
        "dirty": False,
    }
    changed = deepcopy(document)
    changed["project"]["name"] = "B"
    changed["updatedAt"] = 2
    edited = _saved(_put(env, changed, _axis(sequence=2), _axis(), working_revision))
    assert edited["project_file"]["state"] == "accepted"
    cold = (m.app.test_client(), env[1], env[2])
    assert _binding(cold) == {**initial, "dirty": True}
    assert _cold_read(env)["binding"] == {**initial, "dirty": True}
    assert not (env[1] / "tavottofile/B.json").exists()

    # 派生保存改变正文 hash，但没有新用户编辑；不能自己改目标或同步轴。
    changed["updatedAt"] = 3
    _saved(_put(cold, changed, _axis(sequence=2), _axis(sequence=2), edited["revision"]))
    assert _binding(cold) == {**initial, "dirty": True}
    ticket = _reserve(cold, expected=initial["sequence"])
    saved = _saved(_named(cold, changed, base=named["revision"], ticket=ticket))
    assert saved["project_file"]["state"] == "bound"
    assert _binding(cold)["dirty"] is False
    assert _binding(cold)["name"] == "A"


def test_derived_autosave_and_late_old_axis_never_re_dirty_successful_named_save(env):
    document, revision, named = _bound(env)
    document["updatedAt"] = 2
    derived = _saved(_put(env, document, _axis(), _axis(), revision))
    assert derived["revision"] != revision
    assert _binding(env)["dirty"] is False
    document["updatedAt"] = 5
    current = _saved(_put(env, document, _axis(sequence=5), _axis(), derived["revision"]))
    ticket = _reserve(env, expected=1)
    saved = _saved(_named(env, document, base=named["revision"], ticket=ticket))
    assert saved["project_file"]["binding"]["dirty"] is False
    before = _slot(env).data
    late = _put(
        env, _document("old", updated=4), _axis(sequence=4), _axis(sequence=4), current["revision"]
    )
    assert _slot(env).data == before
    assert late.status_code == 409, late.get_json()
    assert late.get_json()["code"] == "stale_write"
    assert late.get_json()["project_file"]["state"] == "superseded"
    assert _binding(env)["dirty"] is False


def test_external_named_change_is_not_washed_into_binding_baseline(env):
    document, _working, named = _bound(env)
    target = env[1] / "tavottofile/A.json"
    external = atomicio.dumps_json(_document("external", updated=99))
    target.write_bytes(external)
    before = _binding(env)
    assert before["revision"] == named["revision"]
    ticket = _reserve(env, expected=before["sequence"])
    response = _named(env, document, base=named["revision"], ticket=ticket)
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "external_change"
    assert response.get_json()["revision"] == atomicio.revision_of(external)
    assert target.read_bytes() == external
    assert _binding(env) == before


@pytest.mark.parametrize("cancel_b", [False, True])
def test_late_named_A_receipt_does_not_bind_over_newer_B_intent(env, cancel_b):
    document, _revision, original = _bound(env)
    before = _binding(env)
    a = _reserve(env, "A", expected=before["sequence"])
    b = _reserve(env, "B", expected=before["sequence"])
    # A 的正文确实成功；元信息只能报告过期，不能抢回更晚 B 意图。
    late_a = _saved(_named(env, document, base=original["revision"], ticket=a))
    assert late_a["project_file"]["state"] == "superseded"
    assert _binding(env) == before
    if cancel_b:
        client, _root, headers = env
        cancelled = client.delete(
            _url(f"/layout-session/project-file/working/intent/{b['sequence']}"), headers=headers
        )
        assert cancelled.status_code == 200, cancelled.get_json()
        assert cancelled.get_json()["state"] == "cancelled"
        assert _binding(env) == before
    else:
        saved_b = _saved(_named(env, document, "B", ticket=b))
        assert saved_b["project_file"]["state"] == "bound"
        assert _binding(env)["name"] == "B"
    final = _binding(env)
    again = _saved(_named(env, document, base=late_a["revision"], ticket=a))
    assert again["project_file"]["state"] == "superseded"
    assert _binding(env) == final


def test_open_confirm_verifies_actual_file_and_ABA_ticket_cannot_rebind(env):
    document, _revision, original = _bound(env)
    old_a = _reserve(env, "A", expected=1)
    b = _reserve(env, "B", expected=1)
    b_file = _saved(_named(env, document, "B"))
    confirmed = _confirm(env, "B", b, b_file["revision"])
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()["state"] == "bound"
    new_a = _reserve(env, "A", expected=b["sequence"])
    confirmed_a = _confirm(env, "A", new_a, original["revision"])
    assert confirmed_a.status_code == 200, confirmed_a.get_json()
    assert confirmed_a.get_json()["state"] == "bound"
    before = _binding(env)
    late = _confirm(env, "A", old_a, original["revision"])
    assert late.status_code == 200, late.get_json()
    assert late.get_json()["state"] == "superseded"
    assert _binding(env) == before and before["sequence"] == new_a["sequence"]

    valid = _reserve(env, "A", expected=before["sequence"])
    target = env[1] / "tavottofile/A.json"
    target.write_bytes(atomicio.dumps_json(_document("external", updated=100)))
    changed = _confirm(env, "A", valid, original["revision"])
    assert _binding(env) == before
    assert changed.status_code == 409, changed.get_json()
    assert changed.get_json()["code"] == "external_change"


def test_body_saved_with_failed_binding_metadata_is_200_and_honest_partial(env, monkeypatch):
    document, _revision, original = _bound(env)
    ticket = _reserve(env, "B", expected=1)
    before = _binding(env)
    real_write = atomicio.write_json

    def fail_session(path, obj, **kwargs):
        if Path(path) == layoutsession.state_path():
            raise OSError("injected session metadata write failure")
        return real_write(path, obj, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", fail_session)
    result = _saved(_named(env, document, "B", ticket=ticket))
    assert result["project_file"]["state"] == "unavailable"
    target = env[1] / "tavottofile/B.json"
    assert json.loads(target.read_bytes()) == document
    assert atomicio.content_revision(target) == result["revision"]
    assert result["revision"] == original["revision"]
    assert _binding(env) == before


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_named_body_success_without_readable_revision_reports_unavailable_binding(
    env, monkeypatch, fmt
):
    document, _working_revision, _named_file = _bound(env, fmt=fmt)
    before = _binding(env, fmt=fmt)
    ticket = _reserve(env, "B", expected=before["sequence"], fmt=fmt)
    target = env[1] / ("tavottofile/documents/v4/B.json" if fmt == "v4" else "tavottofile/B.json")
    original_revision = atomicio.content_revision

    def unreadable_revision(path):
        return None if Path(path) == target else original_revision(path)

    monkeypatch.setattr(atomicio, "content_revision", unreadable_revision)
    result = _saved(_named(env, document, "B", ticket=ticket, fmt=fmt))
    assert result["revision"] is None
    assert result["project_file"] == {"state": "unavailable"}
    assert json.loads(target.read_bytes()) == document
    assert original_revision(target) is not None
    assert _binding(env, fmt=fmt) == before


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_confirm_named_read_error_preserves_binding_and_pending_with_revision_unreadable(
    env, monkeypatch, fmt
):
    _document_before, _working, named = _bound(env, fmt=fmt)
    before_binding = _binding(env, fmt=fmt)
    ticket = _reserve(env, expected=before_binding["sequence"], fmt=fmt)
    before_state = layoutsession.state_path().read_bytes()
    target = env[1] / before_binding["file"]
    before_body = target.read_bytes()
    existing_revision = atomicio.existing_revision

    def refuse_named_read(path):
        if Path(path) == target:
            raise PermissionError("injected named file read failure")
        return existing_revision(path)

    monkeypatch.setattr(atomicio, "existing_revision", refuse_named_read)
    response = _confirm(env, "A", ticket, named["revision"], fmt=fmt)
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "revision_unreadable"
    assert _binding(env, fmt=fmt) == before_binding
    assert layoutsession.state_path().read_bytes() == before_state
    assert target.read_bytes() == before_body


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
@pytest.mark.parametrize("invalid", [None, {"axis": {"editor_id": "A", "sequence": True}}])
def test_invalid_edit_shape_precedes_external_revision_conflict_and_keeps_all_bytes(
    env, fmt, invalid
):
    document, _working_revision, _named_file = _bound(env, fmt=fmt)
    client, _root, headers = env
    before_body = _slot(env, fmt=fmt).data
    before_state = layoutsession.state_path().read_bytes()
    response = client.put(
        _url("/autosave/working", fmt),
        query_string={"base_revision": "f" * 32, "project_file_edit": json.dumps(invalid)},
        json={**document, "updatedAt": 99},
        headers=headers,
    )
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "bad_request"
    assert _slot(env, fmt=fmt).data == before_body
    assert layoutsession.state_path().read_bytes() == before_state


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
@pytest.mark.parametrize("owner", ["none", "expired"])
def test_feature_autosave_without_valid_project_keeps_body_and_reports_metadata_honestly(
    env, fmt, owner
):
    client, root, _headers = env
    m.reset_projects()
    before_project = {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }
    headers = {} if owner == "none" else {"X-Tavotto-Project": "expired-project"}
    context = client, root, headers
    document = _document("owner unavailable", schema=4 if fmt == "v4" else 3)
    result = _saved(_put(context, document, _axis(), None, None, fmt=fmt))
    if owner == "none":
        assert result["metadata"] == "ok"
        assert result["project_file"] == {"state": "skipped"}
    else:
        assert result["metadata"] == "owner_unknown"
        assert result["project_file"] == {"state": "unavailable"}
    assert _slot(context, fmt=fmt).get_json() == document
    assert result["revision"] == atomicio.revision_of(_slot(context, fmt=fmt).data)
    if layoutsession.state_path().exists():
        state = json.loads(layoutsession.state_path().read_bytes())
        assert layoutsession.PROJECT_FILES_KEY not in state
    assert {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    } == before_project


def test_same_filename_autosave_named_save_and_confirm_share_stripe_without_nested_lock(
    env, monkeypatch
):
    client, root, headers = env
    monkeypatch.setattr(m, "DOCUMENT_LOCK_TIMEOUT_S", 1.0)
    document = _document()
    _saved(_put(env, document, _axis(), None, None))
    ticket = _reserve(env, "working")
    slot = m.AUTOSAVE_DIR / "working.json"
    named = root / "tavottofile/working.json"
    assert m._document_lock(slot)._stripe == m._document_lock(named)._stripe
    result = _saved(_named(env, document, "working", ticket=ticket))
    assert result["project_file"]["state"] == "bound"
    second = _reserve(env, "working", expected=ticket["sequence"])
    confirmed = _confirm(env, "working", second, result["revision"])
    assert confirmed.status_code == 200, confirmed.get_json()
    assert confirmed.get_json()["state"] == "bound"
    response = client.get(_url("/layout-session/project-file/working"), headers=headers)
    assert response.status_code == 200
    assert response.get_json()["binding"]["sequence"] == second["sequence"]


def test_external_autosave_drift_is_unknown_and_old_axis_cannot_write_before_body(env):
    _document_before, _revision, named = _bound(env)
    path = m.AUTOSAVE_DIR / "working.json"
    external = atomicio.dumps_json(_document("external", updated=99))
    path.write_bytes(external)
    current = _read(env)
    assert current["edit"]["known"] is False
    assert current["binding"]["dirty"] is True
    assert current["binding"]["revision"] == named["revision"]
    response = _put(
        env,
        _document("old editor", updated=100),
        _axis(sequence=2),
        _axis(),
        atomicio.revision_of(external),
    )
    assert path.read_bytes() == external
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "stale_write"
    assert response.get_json()["project_file"]["state"] == "unconfirmed"
    assert _binding(env)["revision"] == named["revision"]


def test_same_doc_multiple_editors_need_captured_base_axis_not_numeric_max(env):
    document, revision, _named_file = _bound(env)
    document["updatedAt"] = 2
    b = _saved(_put(env, document, _axis("editor_B", 1), _axis(), revision))
    before = _slot(env).data
    # 旧标签拿到最新正文revision也不能把捕获的旧base_axis偷换成新资格。
    response = _put(
        env, _document("late A", updated=999), _axis(sequence=999), _axis(), b["revision"]
    )
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["project_file"]["state"] == "superseded"
    assert _slot(env).data == before
    current = _read(env)
    assert current["edit"]["axis"] == _axis("editor_B", 1)


@pytest.mark.parametrize(
    "changes",
    [
        {"file": "../../outside.json"},
        {"axis": {"editor_id": "A", "sequence": True}},
        {"expected_binding_sequence": True},
        {"autosave_revision": "invalid"},
    ],
)
def test_reserve_closed_request_rejects_untrusted_fields_before_state_change(env, changes):
    client, _root, headers = env
    before = _read(env)
    response = client.post(
        _url("/layout-session/project-file/working/reserve"),
        json={
            "name": "A",
            "autosave_revision": None,
            "axis": None,
            "expected_binding_sequence": None,
            **changes,
        },
        headers=headers,
    )
    assert response.status_code == 400, response.get_json()
    assert _read(env) == before
    assert not layoutsession.state_path().exists()


@pytest.mark.parametrize(
    "edit",
    [
        None,
        {},
        {"axis": None, "base_axis": None},
        {"axis": None, "base_axis": None, "base_revision": None, "future": 1},
        {"axis": {"editor_id": "A", "sequence": 0}, "base_axis": None, "base_revision": None},
    ],
)
def test_invalid_edit_query_is_rejected_before_any_body_or_metadata_write(env, edit):
    client, _root, headers = env
    response = client.put(
        _url("/autosave/working"),
        query_string={"base_revision": "absent", "project_file_edit": json.dumps(edit)},
        json=_document(),
        headers=headers,
    )
    assert response.status_code == 400, response.get_json()
    assert not (m.AUTOSAVE_DIR / "working.json").exists()
    assert not layoutsession.state_path().exists()


def test_future_binding_block_reads_unavailable_and_refuses_write_before_body(env):
    document, revision, _named_file = _bound(env)
    state = json.loads(layoutsession.state_path().read_bytes())
    state[layoutsession.PROJECT_FILES_KEY]["version"] = 2
    atomicio.write_json(layoutsession.state_path(), state)
    before_state = layoutsession.state_path().read_bytes()
    before = _slot(env).data
    assert _read(env) == {"version": 1, "status": "unavailable"}
    response = _put(env, document, _axis(sequence=2), _axis(), revision)
    assert response.status_code == 503, response.get_json()
    assert response.get_json()["code"] == "session_unavailable"
    assert _slot(env).data == before
    assert layoutsession.state_path().read_bytes() == before_state


def test_same_working_id_project_targets_are_isolated_without_shared_slot_guess(env, tmp_path):
    document = _document()
    second_root = tmp_path / "second_project"
    second_root.mkdir()
    second_id = m.open_project(str(second_root))["id"]
    second = env[0], second_root, {"X-Tavotto-Project": second_id}
    for context, name in ((env, "A"), (second, "B")):
        named = _saved(_named(context, document, name))
        ticket = _reserve(context, name)
        response = _confirm(context, name, ticket, named["revision"])
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["state"] == "bound"
    assert _binding(env)["name"] == "A"
    assert _binding(second)["name"] == "B"
    assert _binding(env)["file"] == "tavottofile/A.json"
    assert _binding(second)["file"] == "tavottofile/B.json"


def test_same_working_id_two_formats_have_independent_slots_target_and_dirty(env):
    _legacy_doc, _legacy_rev, legacy_named = _bound(env)
    v4_document, v4_revision, v4_named = _bound(env, fmt="v4")
    assert _binding(env)["revision"] == legacy_named["revision"]
    v4_binding = _binding(env, fmt="v4")
    assert v4_binding["file"] == "tavottofile/documents/v4/A.json"
    assert v4_binding["revision"] == v4_named["revision"]
    v4_document["project"]["name"] = "v4 edited"
    _saved(_put(env, v4_document, _axis(sequence=2), _axis(), v4_revision, fmt="v4"))
    assert _binding(env, fmt="v4")["dirty"] is True
    assert _binding(env)["dirty"] is False
    assert _slot(env).get_json()["schema"] == 3
    assert _slot(env, fmt="v4").get_json()["schema"] == 4


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_new_binding_endpoints_obey_session_guard_including_scoped_token(env, monkeypatch, fmt):
    client, _root, headers = env
    state = security.SessionState("nonce", api_secret="full-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    path = _url("/layout-session/project-file/working", fmt)
    calls = [
        ("get", path, {}),
        ("post", path + "/reserve", {"json": {}}),
        ("post", path + "/confirm", {"json": {}}),
        ("delete", path + "/intent/1", {}),
    ]
    for method, url, kwargs in calls:
        for extra in ({}, {"X-Tavotto-Auth": state.status_token}):
            response = getattr(client, method)(url, headers={**headers, **extra}, **kwargs)
            assert response.status_code == 401, (method, url, response.get_json())
    authorized = client.get(path, headers={**headers, "X-Tavotto-Auth": "full-secret"})
    assert authorized.status_code == 200, authorized.get_json()
    assert authorized.get_json()["status"] == "ok"
    assert not layoutsession.state_path().exists()


def test_default_http_save_keeps_old_receipts_and_does_not_create_binding_block(env):
    client, _root, headers = env
    named = _saved(_named(env, _document()))
    assert "project_file" not in named
    response = client.put(
        _url("/autosave/working"),
        query_string={"base_revision": "absent"},
        json=_document(),
        headers=headers,
    )
    assert "project_file" not in _saved(response)
    state = json.loads(layoutsession.state_path().read_bytes())
    assert layoutsession.PROJECT_FILES_KEY not in state
    assert set(client.get(_url("/layout-session"), headers=headers).get_json()) == {
        "last",
        "prev",
        "pending",
        "index",
    }


def test_old_client_autosave_after_binding_is_unknown_dirty_and_keeps_named_baseline(env):
    document, revision, named = _bound(env)
    client, _root, headers = env
    document["updatedAt"] = 2
    response = client.put(
        _url("/autosave/working"),
        query_string={"base_revision": revision},
        json=document,
        headers=headers,
    )
    assert "project_file" not in _saved(response)
    current = _read(env)
    assert current["edit"]["known"] is False
    assert current["binding"]["dirty"] is True
    assert current["binding"]["name"] == "A"
    assert current["binding"]["revision"] == named["revision"]


def test_v4_real_chart_binding_survives_source_delete_and_production_export(native_env):
    client, root, _headers = native_env
    document = _chart_document(_bind(client))
    first = _saved(_put(native_env, document, _axis(), None, None, fmt="v4"))
    frozen = _slot(native_env, fmt="v4").get_json()
    panel = frozen["canvases"][0]["objects"][0]
    assert panel["chart"] == document["canvases"][0]["objects"][0]["chart"]
    assert panel["overrides"] == document["canvases"][0]["objects"][0]["overrides"]
    assert panel["chartSource"]["sourceId"] == "chart.svg"
    ticket = _reserve(native_env, "NativeA", fmt="v4")
    named = _saved(_named(native_env, frozen, "NativeA", ticket=ticket, fmt="v4"))
    assert named["project_file"]["state"] == "bound"
    original_bytes = (root / "chart.svg").read_bytes()
    (root / "chart.svg").unlink()

    rendered = client.post("/api/engine/render", json=_render_request(panel))
    assert rendered.status_code == 200, rendered.get_json()
    _assert_svg_color(rendered.get_json()["svg"], "#ff0000")
    exported = client.post("/api/export", json=_export_request(panel, name="bound-native"))
    assert exported.status_code == 200, exported.get_json()
    output = exported.get_json()
    assert output["status"] == "done", output
    actual = Path(output["export_dir"]) / output["outputs"][0]["name"]
    _assert_svg_color(actual.read_bytes(), "#ff0000")
    assert actual.read_bytes() != original_bytes

    # 同一附件继续编辑并自动保存，不得因原来源消失改绑/洗掉命名基线。
    frozen["project"]["name"] = "NativeB"
    frozen["canvases"][0]["objects"][0]["overrides"].append(
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#0000ff"}
    )
    _saved(_put(native_env, frozen, _axis(sequence=2), _axis(), first["revision"], fmt="v4"))
    cold = m.app.test_client(), root, {}
    binding = _binding(cold, fmt="v4")
    assert binding["name"] == "NativeA" and binding["dirty"] is True
    assert binding["revision"] == named["revision"]
    stored = _slot(cold, fmt="v4").get_json()["canvases"][0]["objects"][0]
    blue = cold[0].post("/api/engine/render", json=_render_request(stored))
    assert blue.status_code == 200, blue.get_json()
    _assert_svg_color(blue.get_json()["svg"], "#0000ff")
