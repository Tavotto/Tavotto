"""真实会话/槽位HTTP链：打开顺序不依赖时钟，后台补推不重排用户意图。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tavotto.engine import atomicio, layoutsession
from test_layout_recovery_backend import PD, _pj, _project, client as _client, data_dir as _data_dir

client = _client
data_dir = _data_dir
V4 = "/api/documents/v4"


@pytest.fixture
def env(client, tmp_path):
    project = _project(client, tmp_path, "打开顺序")
    return client, _pj(project["id"]), project


def _body(document_format="legacy"):
    body = deepcopy(PD)
    if document_format == "v4":
        body["schema"] = 4
    return body


def _prefix(document_format):
    return V4 if document_format == "v4" else "/api"


def _open(env, document_format, doc_id, *, repair=None):
    client, headers, _ = env
    body = {"doc_id": doc_id, "name": doc_id}
    if repair is not None:
        body["repair"] = repair
    return client.put(f"{_prefix(document_format)}/layout-session/last", json=body, headers=headers)


def _save(env, document_format, doc_id):
    client, headers, _ = env
    response = client.put(
        f"{_prefix(document_format)}/autosave/{doc_id}?base_revision=absent",
        json=_body(document_format),
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _view(env, document_format, *, include=True):
    client, headers, _ = env
    response = client.get(
        f"{_prefix(document_format)}/layout-session"
        + ("?include_open_intent=1" if include else ""),
        headers=headers,
    )
    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    return response.get_json()


def _pair(env):
    legacy, v4 = (_view(env, fmt) for fmt in ("legacy", "v4"))
    assert "open_intent" in legacy and "open_intent" in v4
    assert legacy["open_intent"] == v4["open_intent"]
    return legacy, v4, legacy["open_intent"]


def _session_bytes():
    path = layoutsession.state_path()
    return path.read_bytes(), path.with_suffix(path.suffix + ".bak").read_bytes()


def test_shared_order_is_opt_in_and_reads_do_not_create_session_state(env):
    for fmt in ("legacy", "v4"):
        assert _view(env, fmt, include=False) == {
            "last": None,
            "prev": None,
            "pending": None,
            "index": "missing",
        }
    _, _, intent = _pair(env)
    assert set(intent) == {"version", "status", "snapshot"}
    assert intent["status"] == "untracked" and len(intent["snapshot"]) == 64
    assert not layoutsession.state_path().exists()
    assert _open(env, "legacy", "d_old").status_code == 200
    new_intent = _pair(env)[2]
    assert new_intent["status"] == "untracked"
    assert new_intent["snapshot"] != intent["snapshot"]
    for fmt in ("legacy", "v4"):
        assert set(_view(env, fmt, include=False)) == {"last", "prev", "pending", "index"}


def test_v4_list_reports_its_authoritative_save_directory_without_creating_it(env):
    client, headers, project = env
    directory = Path(project["figures_dir"]) / "tavottofile" / "documents" / "v4"
    assert not directory.exists()
    assert client.get("/api/layouts", headers=headers).get_json() == {
        "layouts": [],
        "modified": {},
    }
    response = client.get(f"{V4}/layouts", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {
        "layouts": [],
        "modified": {},
        "document_format": "v4",
        "document_dir": str(directory),
    }
    assert not directory.exists()


def test_clock_rollback_with_identical_doc_ids_uses_latest_format(env, monkeypatch):
    _save(env, "legacy", "d_same")
    monkeypatch.setattr(layoutsession, "_now_ms", lambda: 10000)
    assert _open(env, "legacy", "d_same").get_json()["state"] == "durable"
    _save(env, "v4", "d_same")
    monkeypatch.setattr(layoutsession, "_now_ms", lambda: 500)
    assert _open(env, "v4", "d_same").get_json()["state"] == "durable"
    legacy, v4, intent = _pair(env)
    assert legacy["last"]["at"] > v4["last"]["at"]
    assert intent["status"] == "ok"
    assert intent["latest"] == {
        "document_format": "v4",
        "doc_id": "d_same",
        "pointer": "last",
    }
    assert set(_view(env, "v4", include=False)) == {"last", "prev", "pending", "index"}


def test_late_first_save_and_automatic_repair_do_not_supersede_other_format(env):
    assert _open(env, "legacy", "d_late").get_json()["state"] == "pending"
    _save(env, "v4", "d_current")
    assert _open(env, "v4", "d_current").get_json()["state"] == "durable"
    before_intent = _pair(env)[2]
    _save(env, "legacy", "d_late")
    _, _, after_intent = _pair(env)
    assert after_intent["status"] == "ok"
    assert after_intent["sequence"] == before_intent["sequence"]
    assert after_intent["latest"] == before_intent["latest"]
    before = _session_bytes()
    assert _open(env, "legacy", "d_late", repair=True).get_json()["state"] == "superseded"
    assert _open(env, "v4", "d_current", repair=True).get_json()["state"] == "durable"
    assert _session_bytes() == before
    assert _pair(env)[2] == after_intent


def test_explicit_same_pending_reopen_allocates_a_new_shared_sequence(env):
    assert _open(env, "v4", "d_pending").get_json()["state"] == "pending"
    first = _pair(env)[2]
    assert _open(env, "v4", "d_pending").get_json()["state"] == "pending"
    second = _pair(env)[2]
    assert second["sequence"] == first["sequence"] + 1
    assert second["latest"] == first["latest"]
    assert second["snapshot"] != first["snapshot"]


def test_metadata_failure_can_be_repaired_without_a_new_open_intent(env, monkeypatch):
    assert _open(env, "v4", "d_pending").get_json()["state"] == "pending"
    first = _pair(env)[2]

    def fail_metadata(*args, **kwargs):
        raise layoutsession.SessionStateUnavailable("injected metadata publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(layoutsession, "commit_slot", fail_metadata)
        saved = _save(env, "v4", "d_pending")
    assert saved["metadata"] == "failed"
    client, headers, _ = env
    body_before = client.get(f"{V4}/autosave/d_pending", headers=headers).data
    assert _pair(env)[2] == first
    repaired = _open(env, "v4", "d_pending", repair=True)
    assert repaired.status_code == 200 and repaired.get_json()["state"] == "durable"
    assert client.get(f"{V4}/autosave/d_pending", headers=headers).data == body_before
    intent = _pair(env)[2]
    assert intent["status"] == "ok" and intent["sequence"] == first["sequence"]
    assert intent["latest"]["pointer"] == "last"
    assert intent["latest"]["doc_id"] == "d_pending"


def test_first_v4_automatic_repair_cannot_invent_an_open_intent(env):
    _save(env, "v4", "d_saved")
    before = _session_bytes()
    response = _open(env, "v4", "d_saved", repair=True)
    assert response.status_code == 200 and response.get_json()["state"] == "unconfirmed"
    assert _session_bytes() == before
    assert _pair(env)[2]["status"] == "untracked"
    assert _open(env, "v4", "d_saved").get_json()["state"] == "durable"
    assert _pair(env)[2]["latest"]["doc_id"] == "d_saved"


def test_old_program_pointer_change_is_stale_and_repair_does_not_launder_it(env):
    assert _open(env, "v4", "d_new").status_code == 200
    client, headers, project = env
    path = layoutsession.state_path()
    state = json.loads(path.read_bytes())
    key = layoutsession.project_key(project["figures_dir"])
    # 模拟不知道共享账本的旧写入者：保留未知顶层键，只更新自己的真实指针。
    state["projects"][key] = {"pending": {"doc_id": "d_old", "name": "older client", "at": 999999}}
    atomicio.write_json(path, state)
    before = _session_bytes()
    assert _pair(env)[2]["status"] == "stale"
    response = _open(env, "v4", "d_new", repair=True)
    assert response.status_code == 200 and response.get_json()["state"] == "unconfirmed"
    assert _session_bytes() == before
    assert client.get(f"{V4}/layout-session", headers=headers).status_code == 200
    assert _open(env, "v4", "d_new").status_code == 200
    assert _pair(env)[2]["status"] == "ok"


def test_future_open_intent_is_unavailable_and_never_rewritten(env):
    assert _open(env, "v4", "d_future").status_code == 200
    path = layoutsession.state_path()
    state = json.loads(path.read_bytes())
    state["document_open_intents"]["version"] = 2
    atomicio.write_json(path, state)
    before = _session_bytes()
    for fmt in ("legacy", "v4"):
        assert _view(env, fmt)["open_intent"] == {"version": 1, "status": "unavailable"}
        assert set(_view(env, fmt, include=False)) == {"last", "prev", "pending", "index"}
        response = _open(env, fmt, "d_future", repair=True)
        assert response.status_code == 503
        assert response.get_json()["code"] == "session_unavailable"
    assert _session_bytes() == before


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_repair_flag_rejects_non_booleans_without_writing(env, value):
    client, headers, _ = env
    response = client.put(
        f"{V4}/layout-session/last",
        headers=headers,
        json={"doc_id": "d_invalid", "name": "x", "repair": value},
    )
    assert response.status_code == 400 and response.get_json()["code"] == "bad_request"
    assert not layoutsession.state_path().exists()


def test_unavailable_order_is_reported_only_when_explicitly_requested(env, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("injected read failure")

    monkeypatch.setattr(layoutsession, "view_for", unavailable)
    for fmt in ("legacy", "v4"):
        default = _view(env, fmt, include=False)
        assert default == {"last": None, "prev": None, "pending": None, "index": "degraded"}
        assert _view(env, fmt)["open_intent"] == {"version": 1, "status": "unavailable"}
