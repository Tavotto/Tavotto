"""Local Flask v2 facts use the signed client and never mutate project rules."""

import copy
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
    assert isinstance(result.json.get("error"), str) and result.json["error"].strip()
    assert "facts" not in result.json and result.headers["Cache-Control"] == "no-store"


def test_signed_search_pagination_counts_five_digit_cursor_and_utf8(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    idx = index_for(json.dumps(fact_bundle()).encode(), 1)
    template = next(iter(idx["journals"].values()))
    idx["journals"] = {f"j{i:05d}": dict(template) for i in range(10001)}
    for i in (9997, 9998):
        idx["journals"][f"j{i:05d}"]["aliases"] = ["界" * 170] + ["A" * 512] * 255
    aliases = idx["journals"]["j09999"]["aliases"] = []

    # Construct a valid signed index whose third record makes the final engine
    # response exactly one UTF-8 byte over the real 300 KiB budget.  The old
    # paginator measured a null placeholder instead of the 10000 cursor.
    with monkeypatch.context() as m:
        m.setattr(facts, "sync", lambda _: {"index": idx, "state": "updated", "code": None})
        m.setattr(facts, "MAX_PAGE_BYTES", 10_000_000)

        def page_size():
            page = facts.search(offset=9997, limit=3)
            return len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

        target = (300 << 10) + 1
        while target - page_size() > 515:
            aliases.append("A" * 512)
        remaining = target - page_size()
        assert 3 <= remaining <= 515
        aliases.append("A" * (remaining - 3))
        assert page_size() == target

    envelope = signed(key, idx)
    assert len(envelope) < facts.MAX_INDEX_BYTES
    monkeypatch.setattr(facts, "_request", lambda *args: (200, envelope))

    def search(offset, limit):
        return client.post(
            "/api/journal-facts/search",
            json={"offset": offset, "limit": limit, "expected_catalog_rev": 1},
            headers=headers,
        )

    first = search(9997, 3)
    assert first.status_code == 200 and len(first.data) <= facts.MAX_PAGE_BYTES
    assert [item["id"] for item in first.json["journals"]] == ["j09997", "j09998"]
    assert first.json["next_offset"] == 9999
    second = search(first.json["next_offset"], 1)
    assert second.status_code == 200 and len(second.data) <= facts.MAX_PAGE_BYTES
    assert [item["id"] for item in second.json["journals"]] == ["j09999"]
    assert second.json["next_offset"] == 10000
    last = search(second.json["next_offset"], 1)
    assert last.status_code == 200 and [item["id"] for item in last.json["journals"]] == ["j10000"]
    assert last.json["next_offset"] is None
    empty = search(10001, 1)
    assert empty.status_code == 200 and empty.json["journals"] == []
    assert empty.json["next_offset"] is None

    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", 256)
    oversized = search(9997, 1)
    assert oversized.status_code == 413 and oversized.json["code"] == "result_too_large"


def test_signed_search_paginates_actual_http_json_with_multibyte_aliases(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    idx = index_for(json.dumps(fact_bundle()).encode())
    template = next(iter(idx["journals"].values()))
    idx["journals"] = {f"j{i:05d}": {**template, "aliases": ["界" * 170] * 170} for i in range(2)}
    envelope = signed(key, idx)
    monkeypatch.setattr(facts, "_request", lambda *args: (200, envelope))

    # The engine's compact UTF-8 representation fits, while Flask's default
    # escaped representation of both records exceeds the same byte budget.
    engine_page = facts.search(limit=2)
    engine_bytes = len(json.dumps(engine_page, ensure_ascii=False, separators=(",", ":")).encode())
    assert engine_bytes < facts.MAX_PAGE_BYTES
    first = client.post("/api/journal-facts/search", json={"limit": 2}, headers=headers)
    assert first.status_code == 200 and len(first.data) <= facts.MAX_PAGE_BYTES
    assert [item["id"] for item in first.json["journals"]] == ["j00000"]
    assert first.json["next_offset"] == 1
    second = client.post(
        "/api/journal-facts/search",
        json={"limit": 2, "offset": first.json["next_offset"], "expected_catalog_rev": 1},
        headers=headers,
    )
    assert second.status_code == 200 and len(second.data) <= facts.MAX_PAGE_BYTES
    assert [item["id"] for item in second.json["journals"]] == ["j00001"]
    assert second.json["next_offset"] is None

    # Respect the app's configured encoder, and include the added response
    # fields when the page is near its budget even without ASCII escaping.
    monkeypatch.setattr(appmod.app.json, "ensure_ascii", False)
    together = client.post("/api/journal-facts/search", json={"limit": 2}, headers=headers)
    assert together.status_code == 200 and len(together.json["journals"]) == 2
    full = facts.search(limit=2)
    with appmod.app.app_context():
        size_without_status = len(appmod.jsonify(full).get_data())
        size_with_status = len(appmod.jsonify(appmod._journal_facts_payload(full)).get_data())
    assert size_without_status < size_with_status
    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", size_without_status)
    bounded = client.post("/api/journal-facts/search", json={"limit": 2}, headers=headers)
    assert bounded.status_code == 200 and len(bounded.data) <= size_without_status
    assert [item["id"] for item in bounded.json["journals"]] == ["j00000"]
    assert bounded.json["next_offset"] == 1


def test_signed_search_rejects_one_record_too_large_for_actual_http_json(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    idx = index_for(json.dumps(fact_bundle()).encode())
    idx["journals"]["fx-journal"]["aliases"] = ["界" * 200] * 256
    monkeypatch.setattr(facts, "_request", lambda *args: (200, signed(key, idx)))
    page = facts.search(limit=1)
    assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()) < (
        facts.MAX_PAGE_BYTES
    )
    response = client.post("/api/journal-facts/search", json={"limit": 1}, headers=headers)
    assert response.status_code == 413 and response.json["code"] == "result_too_large"
    assert "journals" not in response.json


@pytest.mark.parametrize("with_inspection", [False, True])
def test_signed_journal_http_pages_split_large_context_facts(
    http_client, isolated, monkeypatch, with_inspection
):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    bundle["facts"][0]["context"] = [{"source": "s", "quote": "界" * 2000} for _ in range(16)]
    second = copy.deepcopy(bundle["facts"][0])
    second["id"] = "second"
    bundle["facts"].append(second)
    raw = json.dumps(bundle).encode()
    envelope = signed(key, index_for(raw))
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            200,
            envelope if url.endswith("index.signed.json") else raw,
        ),
    )
    body = {"journal_id": "fx-journal", "limit": 2}
    if with_inspection:
        body["inspection"] = {"object_label": "Figure 1", "figure_kind": "line_art"}
    first = client.post("/api/journal-facts/journal", json=body, headers=headers)
    assert first.status_code == 200 and len(first.data) <= facts.MAX_PAGE_BYTES
    assert first.json["facts"] == bundle["facts"][:1] and first.json["next_offset"] == 1
    second = client.post(
        "/api/journal-facts/journal",
        json={**body, "offset": 1, "expected_catalog_rev": 1},
        headers=headers,
    )
    assert second.status_code == 200 and len(second.data) <= facts.MAX_PAGE_BYTES
    assert second.json["facts"] == bundle["facts"][1:] and second.json["next_offset"] is None
    assert first.json["sources"] == second.json["sources"] == bundle["sources"]
    if with_inspection:
        assert [item["id"] for item in first.json["inspection"]["facts"]] == ["line"]
        assert [item["id"] for item in second.json["inspection"]["facts"]] == ["second"]


@pytest.mark.parametrize("with_inspection", [False, True])
def test_signed_journal_http_pages_include_context_coverage_and_inspection(
    http_client, isolated, monkeypatch, with_inspection
):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    bundle["sources"]["context"] = {
        "url": "https://example.invalid/context",
        "kind": "html",
        "retrieved": "2026-11-11",
    }
    bundle["sources"]["coverage"] = {
        "url": "https://example.invalid/coverage",
        "kind": "html",
        "retrieved": "2026-11-11",
    }
    bundle["facts"][0]["context"] = [{"source": "context", "quote": "界" * 2000} for _ in range(16)]
    bundle["coverage"] = [
        {
            "id": f"coverage-{i}",
            "subject": "font_size",
            "result": "not_stated",
            "scope": "界" * 1000,
            "sources": ["coverage"],
            "review": {"status": "verified", "reviewed": "2026-11-11", "method": "fixture"},
        }
        for i in range(19)
    ]
    raw = json.dumps(bundle).encode()
    envelope = signed(key, index_for(raw))
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            200,
            envelope if url.endswith("index.signed.json") else raw,
        ),
    )
    body = {"journal_id": "fx-journal", "limit": 20}
    if with_inspection:
        body["inspection"] = {
            "object_label": "Figure 1",
            "figure_kind": "line_art",
            "stage": "production",
        }

    offset, pages, seen_facts, seen_coverage = 0, 0, [], []
    while offset is not None:
        response = client.post(
            "/api/journal-facts/journal",
            json={**body, "offset": offset, "expected_catalog_rev": 1},
            headers=headers,
        )
        assert response.status_code == 200 and len(response.data) <= facts.MAX_PAGE_BYTES
        page = response.json
        pages += 1
        seen_facts.extend(page["facts"])
        seen_coverage.extend(page["coverage"])
        referenced = {record["source"] for record in page["facts"]}
        referenced.update(
            ctx["source"] for record in page["facts"] for ctx in record.get("context", [])
        )
        referenced.update(src for record in page["coverage"] for src in record["sources"])
        assert page["sources"] == {src: bundle["sources"][src] for src in referenced}
        if with_inspection:
            assert [item["id"] for item in page["inspection"]["facts"]] == [
                item["id"] for item in page["facts"]
            ]
        next_offset = page["next_offset"]
        assert next_offset is None or next_offset > offset
        offset = next_offset
    assert pages >= 2 and seen_facts == bundle["facts"] and seen_coverage == bundle["coverage"]
    changed = client.post(
        "/api/journal-facts/journal",
        json={**body, "offset": 1, "expected_catalog_rev": 2},
        headers=headers,
    )
    assert changed.status_code == 409 and changed.json["code"] == "catalog_changed"
    empty = client.post(
        "/api/journal-facts/journal",
        json={**body, "offset": 20, "expected_catalog_rev": 1},
        headers=headers,
    )
    assert empty.status_code == 200 and empty.json["facts"] == [] and empty.json["coverage"] == []
    assert empty.json["next_offset"] is None


def test_signed_journal_single_fact_that_cannot_fit_http_returns_413(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    fact = bundle["facts"][0]
    fact["relation"] = "in"
    fact.pop("unit")
    fact["value"] = ["界" * 200 + str(i) for i in range(256)]
    raw = json.dumps(bundle).encode()
    envelope = signed(key, index_for(raw))
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            200,
            envelope if url.endswith("index.signed.json") else raw,
        ),
    )
    response = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal", "limit": 1}, headers=headers
    )
    assert response.status_code == 413 and response.json["code"] == "result_too_large"
    assert "facts" not in response.json


def test_signed_journal_budget_includes_actual_inspection_before_paging(
    http_client, isolated, monkeypatch
):
    client, headers = http_client
    key, _ = isolated
    bundle = fact_bundle()
    second = copy.deepcopy(bundle["facts"][0])
    second["id"] = "second"
    bundle["facts"].append(second)
    raw = json.dumps(bundle).encode()
    envelope = signed(key, index_for(raw))
    monkeypatch.setattr(
        facts,
        "_request",
        lambda method, url, cap, headers: (
            200,
            envelope if url.endswith("index.signed.json") else raw,
        ),
    )
    body = {"journal_id": "fx-journal", "limit": 2}
    client.post("/api/journal-facts/journal", json=body, headers=headers)  # warm cache
    plain = client.post("/api/journal-facts/journal", json=body, headers=headers)
    inspection = {"object_label": "Figure 1", "figure_kind": "line_art"}
    inspected = client.post(
        "/api/journal-facts/journal", json={**body, "inspection": inspection}, headers=headers
    )
    assert plain.status_code == inspected.status_code == 200
    assert len(plain.data) < len(inspected.data)
    monkeypatch.setattr(facts, "MAX_PAGE_BYTES", len(plain.data))
    bounded = client.post(
        "/api/journal-facts/journal", json={**body, "inspection": inspection}, headers=headers
    )
    assert bounded.status_code == 200 and len(bounded.data) <= len(plain.data)
    assert [fact["id"] for fact in bounded.json["facts"]] == ["line"]
    assert [fact["id"] for fact in bounded.json["inspection"]["facts"]] == ["line"]
    assert bounded.json["next_offset"] == 1


def test_remote_oversized_facts_error_has_fallback(http_client, monkeypatch):
    client, headers = http_client

    def oversized(*args, **kwargs):
        raise facts.FactsError("result_too_large")

    monkeypatch.setattr(facts, "get_facts", oversized)
    result = client.post(
        "/api/journal-facts/journal", json={"journal_id": "fx-journal"}, headers=headers
    )
    assert result.status_code == 413 and result.json["code"] == "result_too_large"
    assert isinstance(result.json.get("error"), str) and result.json["error"].strip()
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
