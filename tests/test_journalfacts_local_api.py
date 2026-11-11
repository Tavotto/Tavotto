"""Local Flask v2 facts use the signed client and never mutate project rules."""

import json

import pytest

from tavotto import app as appmod, security
from tavotto.engine import journalfacts_remote as facts
from test_journalfacts_remote import fact_bundle, index_for, signed

pytest_plugins = ["test_journalfacts_remote"]


@pytest.fixture
def http_client(monkeypatch):
    state = security.SessionState("test-nonce", api_secret="test-secret")
    state.port = 54321
    monkeypatch.setitem(appmod.app.config, security.STATE_KEY, state)
    return appmod.app.test_client(), {"Host": "127.0.0.1:54321", "X-Tavotto-Auth": "test-secret"}


def test_v2_endpoints_require_real_session_guard(http_client, monkeypatch):
    client, headers = http_client
    monkeypatch.setattr(
        facts, "search", lambda *args, **kwargs: pytest.fail("unauthenticated read")
    )
    for path in ("/api/journal-facts/search", "/api/journal-facts/journal"):
        res = client.post(path, json={}, headers={"Host": headers["Host"]})
        assert res.status_code == 401 and res.json["code"] == "session_auth_required"


def test_real_signed_reader_preserves_sources_inspection_and_revision(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    bundle["facts"][0]["context"] = [{"source": "s", "quote": "section heading"}]
    bundle["facts"].append(dict(bundle["facts"][0], id="second"))
    raw = json.dumps(bundle).encode()
    current = {"revision": 2}

    def request(method, url, cap, request_headers):
        if url.endswith("index.signed.json"):
            return 200, signed(key, index_for(raw, current["revision"]))
        return 200, raw

    monkeypatch.setattr(facts, "_request", request)
    result = client.post("/api/journal-facts/search", json={"query": "fixture"}, headers=headers)
    assert result.status_code == 200 and result.json["journals"][0]["id"] == "fx-journal"
    assert result.headers["Cache-Control"] == "no-store"
    selection = {"object_label": "Figure 1", "figure_kind": "line_art", "stage": "production"}
    body = {"journal_id": "fx-journal", "limit": 1, "inspection": selection}
    result = client.post("/api/journal-facts/journal", json=body, headers=headers)
    out = result.json
    assert result.status_code == 200 and out["catalog_rev"] == 2 and out["next_offset"] == 1
    assert out["facts"][0] == bundle["facts"][0] and out["sources"]["s"] == bundle["sources"]["s"]
    assert (
        out["catalog_completeness"] == "unknown" and out["semantic_review"] == "not_authenticated"
    )
    assert out["inspection"]["facts"][0]["status"] == "not_verifiable"
    assert out["inspection"]["facts"][0]["structured_conditions"]["status"] == "unknown"
    assert (
        client.post(
            "/api/journal-facts/journal", json={**body, "offset": 1}, headers=headers
        ).status_code
        == 400
    )
    current["revision"] = 3
    changed = client.post(
        "/api/journal-facts/journal",
        json={**body, "offset": 1, "expected_catalog_rev": 2},
        headers=headers,
    )
    assert changed.status_code == 409 and changed.json["code"] == "catalog_changed"
    assert changed.json["expected_catalog_rev"] == 2 and changed.json["current_catalog_rev"] == 3
    assert "facts" not in changed.json


def test_bad_signature_or_bundle_hash_never_returns_facts(http_client, isolated, monkeypatch):
    client, headers = http_client
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    index = index_for(raw)
    monkeypatch.setattr(facts, "_request", lambda *args: (200, b"{}"))
    response = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert response.status_code == 503 and response.json["code"] in {
        "signature_invalid",
        "index_invalid",
    }
    assert "facts" not in response.json
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, req_headers: (
            (200, signed(key, index)) if url.endswith("index.signed.json") else (200, raw + b" ")
        ),
    )
    response = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert response.status_code == 503 and response.json["code"] == "bundle_invalid"
    assert "facts" not in response.json


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"journal_id": "fx-journal", "offset": True},
        {"journal_id": "fx-journal", "offset": 1},
        {"journal_id": "fx-journal", "inspection": None},
        {"journal_id": "fx-journal", "inspection": {"object_label": "one", "reviewed": True}},
        {"journal_id": "fx-journal", "unexpected": 1},
    ],
)
def test_invalid_request_rejected_before_network(http_client, monkeypatch, body):
    client, headers = http_client
    monkeypatch.setattr(facts, "_request", lambda *args: pytest.fail("unexpected network"))
    result = client.post("/api/journal-facts/journal", json=body, headers=headers)
    assert result.status_code == 400 and result.json["code"] == "bad_request"


def test_actual_flask_encoded_response_is_bounded(http_client, monkeypatch):
    client, headers = http_client
    page = {"catalog_rev": 1, "facts": [{"id": "x", "quote": "图" * 100}], "next_offset": None}
    monkeypatch.setattr(facts, "get_facts", lambda *args, **kwargs: dict(page))
    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", 100)
    result = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert result.status_code == 413 and result.json["code"] == "result_too_large"
    assert "facts" not in result.json and result.headers["Cache-Control"] == "no-store"


def test_offline_cache_and_rollback_keep_state_explicit(http_client, isolated, monkeypatch):
    from tavotto.engine import journalremote

    client, headers = http_client
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    current = {"revision": 5}
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, req_headers: (
            (200, signed(key, index_for(raw, current["revision"])))
            if url.endswith("index.signed.json")
            else (200, raw)
        ),
    )
    first = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert first.status_code == 200 and first.json["catalog_rev"] == 5
    current["revision"] = 4
    rollback = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert rollback.status_code == 200 and rollback.json["catalog_rev"] == 5
    assert (
        rollback.json["sync_state"] == "rejected" and rollback.json["sync_code"] == "rev_rollback"
    )
    assert rollback.json["semantic_review"] == "not_authenticated"
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    cached = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert cached.status_code == 200 and cached.json["sync_state"] == "disabled"
    missing = client.post(
        "/api/journal-facts/journal", json={"journal_id": "not-in-cache"}, headers=headers
    )
    assert missing.status_code == 503 and missing.json["code"] == "disabled"
    assert missing.json["sync_state"] == "disabled" and "facts" not in missing.json


def test_search_pages_also_require_revision_and_reject_changes(http_client, isolated, monkeypatch):
    client, headers = http_client
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    current = {"revision": 1}
    monkeypatch.setattr(
        facts, "_request", lambda *args: (200, signed(key, index_for(raw, current["revision"])))
    )
    first = client.post("/api/journal-facts/search", json={"limit": 1}, headers=headers)
    assert first.status_code == 200 and first.json["catalog_rev"] == 1
    invalid = client.post("/api/journal-facts/search", json={"offset": 1}, headers=headers)
    assert invalid.status_code == 400
    current["revision"] = 2
    changed = client.post(
        "/api/journal-facts/search", json={"offset": 1, "expected_catalog_rev": 1}, headers=headers
    )
    assert changed.status_code == 409 and changed.json["code"] == "catalog_changed"
    assert "journals" not in changed.json


def test_untrusted_origin_and_unavailable_v2_do_not_fall_back(http_client, isolated, monkeypatch):
    client, headers = http_client
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    index = index_for(raw)
    index["journals"] = {}
    monkeypatch.setattr(facts, "_request", lambda *args: (200, signed(key, index)))
    denied = client.post(
        "/api/journal-facts/search",
        json={},
        headers={**headers, "Origin": "https://untrusted.invalid"},
    )
    assert denied.status_code == 403
    absent = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert absent.status_code == 404 and absent.json["code"] == "unknown_journal"
    assert "facts" not in absent.json and "profile" not in absent.json


def test_empty_or_whitespace_search_browses_signed_index(http_client, isolated, monkeypatch):
    client, headers = http_client
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    envelope = signed(key, index_for(raw, 4))
    requests = []

    def request(method, url, cap, req_headers):
        requests.append(url)
        assert url.endswith("index.signed.json")
        return 200, envelope

    monkeypatch.setattr(facts, "_request", request)
    for body in ({}, {"query": ""}, {"query": " \t "}):
        result = client.post("/api/journal-facts/search", json=body, headers=headers)
        assert result.status_code == 200 and result.json["catalog_rev"] == 4
        assert [journal["id"] for journal in result.json["journals"]] == ["fx-journal"]
    assert len(requests) == 3


@pytest.mark.parametrize("query", [None, True, 1, [], {}])
def test_invalid_search_types_and_missing_journal_do_not_request_network(
    http_client, monkeypatch, query
):
    client, headers = http_client
    monkeypatch.setattr(facts, "_request", lambda *args: pytest.fail("unexpected network"))
    search = client.post("/api/journal-facts/search", json={"query": query}, headers=headers)
    assert search.status_code == 400 and search.json["code"] == "bad_request"
    missing = client.post("/api/journal-facts/journal", json={}, headers=headers)
    assert missing.status_code == 400 and missing.json["code"] == "bad_request"
