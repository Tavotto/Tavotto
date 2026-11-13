"""UI reads and saves consume existing project and approval authorities."""

from __future__ import annotations

import pytest

from tavotto import app as m, security
from tavotto.engine import declaredinputs, inputremap, pool
from test_declared_input_http import inputs as inputs, project as project

ENDPOINT = "/api/engine/input-declarations"


def test_selected_project_file_read_is_normalized_without_settings_or_execution(
    inputs, monkeypatch
):
    client, root, _body = inputs
    before = declaredinputs.state(root)

    def forbidden(*_a, **_k):
        pytest.fail("Resolving one selected file executed or persisted settings")

    monkeypatch.setattr(pool, "build", forbidden)
    monkeypatch.setattr(declaredinputs.atomicio, "write_json", forbidden)
    for selected in ("data.txt", str(root / "data.txt")):
        response = client.get(ENDPOINT, query_string={"project_file": selected})
        assert response.status_code == 200, response.get_json()
        assert response.get_json() == {
            "ok": True,
            "input_declarations": before,
            "project_file": "data.txt",
        }
    assert client.get(ENDPOINT).get_json() == {"ok": True, "input_declarations": before}


def test_opt_in_row_identity_uses_the_same_metadata_snapshot_without_resource_io(
    inputs, monkeypatch
):
    client, root, body = inputs
    assert client.post(ENDPOINT, json=body).status_code == 200
    ordinary = client.get(ENDPOINT).get_json()
    expected = declaredinputs.scope_selection(
        root, script=body["script"], cwd_mode=body["cwd_mode"]
    )["revision"]

    def forbidden(*_a, **_k):
        pytest.fail("Projecting settings identity probed selected resources")

    monkeypatch.setattr(declaredinputs, "_file", forbidden)
    monkeypatch.setattr(inputremap, "snapshot", forbidden)
    opt_in = client.get(ENDPOINT, query_string={"include_selection_revisions": "1"})
    assert opt_in.status_code == 200, opt_in.get_json()
    enriched = opt_in.get_json()
    row = enriched["input_declarations"]["declarations"][0]
    assert row.pop("selection_revision") == expected
    assert enriched == ordinary
    assert client.get(ENDPOINT).get_json() == ordinary


@pytest.mark.parametrize("selected", ["", "../outside.txt", "missing.txt", "."])
def test_invalid_project_file_read_does_not_change_selection(inputs, selected):
    client, root, _body = inputs
    before = declaredinputs.state(root)
    response = client.get(ENDPOINT, query_string={"project_file": selected})
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "input_declaration_invalid"
    assert declaredinputs.state(root) == before


def test_actual_remap_generation_conflict_does_not_approve_the_new_target(inputs, tmp_path):
    client, root, body = inputs
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    shown = inputremap.add_rule(root, {"kind": "prefix", "from": "", "to": str(first)})
    inputremap.add_rule(root, {"kind": "prefix", "from": "", "to": str(second)})
    before = declaredinputs.state(root)
    selection = {**body, "remaps": [""], "expected_remap_generation": shown["generation"]}
    response = client.post(ENDPOINT, json=selection)
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "input_declaration_conflict"
    assert declaredinputs.state(root) == before
    selection["expected_remap_generation"] = inputremap.state(root)["generation"]
    response = client.post(ENDPOINT, json=selection)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["input_declarations"]["declarations"][0]["remaps"] == [""]


@pytest.mark.parametrize("generation", [None, True, -1, "1"])
def test_explicit_invalid_generation_is_not_absent(inputs, generation):
    client, root, body = inputs
    before = declaredinputs.state(root)
    response = client.post(ENDPOINT, json={**body, "expected_remap_generation": generation})
    assert response.status_code == 400, response.get_json()
    assert declaredinputs.state(root) == before


def test_original_post_keeps_the_original_engine_call_shape(inputs, monkeypatch):
    client, root, body = inputs
    seen = []
    original = declaredinputs.declare

    def observe(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(declaredinputs, "declare", observe)
    assert client.post(ENDPOINT, json=body).status_code == 200
    assert len(seen) == 1 and "expected_remap_generation" not in seen[0]


def test_preparation_projection_is_explicit_and_project_owned(inputs, monkeypatch):
    client, root, _body = inputs
    seen = []
    session = object()

    def get_session(sid, pid):
        assert sid == "shown-session" and pid == m._project_id(root)
        return session

    def report(ctx, sess, **kwargs):
        assert sess is session and str(ctx.path) == str(root)
        seen.append(kwargs)
        return {"session_id": "shown-session"}

    monkeypatch.setattr(m.engine_prepsession.SESSIONS, "session", get_session)
    monkeypatch.setattr(m, "_session_report", report)
    for suffix in ("", "?include_input_declaration=1", "?include_input_declaration=0"):
        response = client.get("/api/engine/preparation-sessions/shown-session" + suffix)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert seen == [{}, {"include_input_declaration": True}, {}]


def test_opt_in_paths_keep_original_auth_and_explicit_project_guards(inputs, monkeypatch):
    client, root, _body = inputs
    state = security.SessionState("nonce", api_secret="input-ui-local-test-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    urls = [
        ENDPOINT + "?project_file=data.txt",
        "/api/engine/preparation-sessions/absent?include_input_declaration=1",
    ]
    for url in urls:
        assert client.get(url).status_code == 401
        response = client.get(
            url, headers={"X-Tavotto-Auth": state.api_secret, "X-Tavotto-Project": "not-open"}
        )
        assert response.status_code == 409 and response.get_json()["code"] == "no_project"
