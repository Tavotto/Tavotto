"""Signed v2 client: isolation, rollback, snapshots and fail-closed transport."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tavotto.engine import journalfacts_remote as client, journalremote, tlstrust

pytest_plugins = ("support.journalfacts_fixture",)


def fact_bundle(snapshot_bytes=b"fixture snapshot"):
    name = hashlib.sha256(snapshot_bytes).hexdigest()
    return {
        "schema": 2,
        "journal_id": "fx-journal",
        "journal": {"label": "Fixture", "publisher_id": "fx-pub", "eissn": "1234-5678"},
        "sources": {
            "s": {"url": "https://example.invalid/guide", "kind": "html", "retrieved": "2026-11-11"}
        },
        "facts": [
            {
                "id": "line",
                "subject": "raster_resolution",
                "relation": "gte",
                "value": 1200,
                "unit": "dpi",
                "strength": "required",
                "stage": "production",
                "source": "s",
                "quote": "Line art: 1200 dpi.",
                "origin": "journal",
                "conditions": {"figure_kind": "line", "fine_line": True},
                "review": {"status": "verified", "reviewed": "2026-11-11", "method": "fixture"},
                "snapshot": {"file": f"snapshots/{name}.webp", "width": 1, "height": 1},
            }
        ],
    }


def signed(key, index):
    def b64(value):
        return base64.b64encode(value).decode("ascii")

    payload = json.dumps(index, ensure_ascii=False, sort_keys=True).encode()
    return json.dumps(
        {
            "alg": "ed25519",
            "key_id": "fx",
            "payload": b64(payload),
            "sig": b64(key.sign(payload).signature),
        }
    ).encode()


def index_for(raw, rev=1):
    return {
        "schema": 2,
        "kind": "tavotto-journal-facts-index",
        "catalog_rev": rev,
        "issued": "2026-11-11",
        "publishers": {"fx-pub": {"label": "Fixture Publisher"}},
        "journals": {
            "fx-journal": {
                "label": "Fixture",
                "publisher": "fx-pub",
                "eissn": "1234-5678",
                "bundle": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)},
            }
        },
    }


def test_signed_index_on_demand_bundle_and_snapshot_offline(isolated, monkeypatch):
    key, _ = isolated
    snap = b"fixture snapshot"
    obj = fact_bundle(snap)
    raw = json.dumps(obj).encode()
    index = index_for(raw, 3)
    paths = []

    def request(method, url, cap, headers):
        paths.append(url)
        if url.endswith("index.signed.json"):
            return 200, signed(key, index)
        if "/journals/" in url:
            assert headers["X-Tavotto-Device"] == "fixture-device-token"
            return 200, raw
        if "/snapshots/" in url:
            assert headers["X-Tavotto-Device"] == "fixture-device-token"
            return 200, snap
        raise AssertionError(url)

    monkeypatch.setattr(client, "_request", request)
    result = client.search("1234-5678")
    assert result["catalog_rev"] == 3 and len(result["journals"]) == 1
    result = client.get_journal("fx-journal")
    assert result["source"] == "network" and result["bundle"] == obj
    saved = client.snapshot("fx-journal", obj["facts"][0]["snapshot"]["file"])
    assert saved.read_bytes() == snap
    n = len(paths)
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    result = client.get_journal("fx-journal")
    assert result["source"] == "cache" and result["bundle"] == obj
    assert client.snapshot("fx-journal", obj["facts"][0]["snapshot"]["file"]).read_bytes() == snap
    assert client.search("fixture")["source"] == "cache" and len(paths) == n
    saved.write_bytes(b"corrupt")
    with pytest.raises(client.FactsError, match="cache_invalid"):
        client.snapshot("fx-journal", obj["facts"][0]["snapshot"]["file"])
    assert len(paths) == n


def test_rollback_and_same_revision_conflict_keep_verified_cache(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    newer = index_for(raw, 4)
    doc = signed(key, newer)
    monkeypatch.setattr(client, "_request", lambda *args: (200, doc))
    assert client.sync()["state"] == "updated"
    lower = signed(key, index_for(raw, 3))
    monkeypatch.setattr(client, "_request", lambda *args: (200, lower))
    assert client.sync()["code"] == "rev_rollback" and client.cached_index()["catalog_rev"] == 4
    altered = copy.deepcopy(newer)
    altered["journals"]["fx-journal"]["label"] = "Changed"
    monkeypatch.setattr(client, "_request", lambda *args: (200, signed(key, altered)))
    assert (
        client.sync()["code"] == "index_conflict"
        and client.cached_index()["journals"]["fx-journal"]["label"] == "Fixture"
    )
    forged = bytearray(doc)
    forged[-8] ^= 1
    monkeypatch.setattr(client, "_request", lambda *args: (200, bytes(forged)))
    assert client.sync()["state"] == "rejected" and client.cached_index()["catalog_rev"] == 4


def test_no_v1_seed_substitution_and_unknown_snapshot(isolated, monkeypatch):
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    with pytest.raises(client.FactsError, match="disabled"):
        client.search("fx")
    with pytest.raises(client.FactsError, match="disabled"):
        client.get_journal("fx-journal")
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    index = index_for(raw)
    client._save_index(signed(key, index), index)
    monkeypatch.setattr(client, "_request", lambda method, url, cap, headers: (200, raw))
    with pytest.raises(client.FactsError, match="unknown_snapshot"):
        client.snapshot("fx-journal", "snapshots/" + "b" * 64 + ".webp")


def test_bad_bundle_never_written_and_quota_never_retried_other_origin(isolated, monkeypatch):
    key, tmp = isolated
    raw = json.dumps(fact_bundle()).encode()
    index = index_for(raw)
    client._save_index(signed(key, index), index)
    monkeypatch.setattr(client, "_request", lambda *args: (200, b"wrong bytes"))
    with pytest.raises(client.FactsError, match="bundle_invalid"):
        client.get_journal("fx-journal")
    assert not (tmp / "v2-cache" / "bundles").exists()
    monkeypatch.setattr(
        client, "_bases", lambda _locale=None: ("https://a.invalid/", "https://b.invalid/")
    )
    calls = []

    def quota(method, url, cap, headers):
        calls.append(url)
        return 429, b'{"code":"quota_day","retry_after_s":60}'

    monkeypatch.setattr(client, "_request", quota)
    with pytest.raises(client.FactsError) as err:
        client.get_journal("fx-journal")
    assert err.value.code == "quota_day" and err.value.retry_after_s == 60 and len(calls) == 1


def test_verified_index_store_is_serialized_and_cannot_decrease(isolated):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    errors = []
    barrier = threading.Barrier(2)

    def update(rev):
        idx = index_for(raw, rev)
        doc = signed(key, idx)
        barrier.wait()
        try:
            client._save_index(doc, idx)
        except client.FactsError as exc:
            errors.append(exc.code)

    a = threading.Thread(target=update, args=(8,))
    b = threading.Thread(target=update, args=(9,))
    a.start()
    b.start()
    a.join()
    b.join()
    assert client.cached_index()["catalog_rev"] == 9 and set(errors) <= {"rev_rollback"}


def test_https_redirect_cannot_forward_device_token(tmp_path, monkeypatch):
    import ssl

    from support.tls_certs import make_pki

    cert = make_pki().write(tmp_path)
    received = []

    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("X-Tavotto-Device"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"https://127.0.0.1:{dst.server_port}/collect")
            self.end_headers()

        def log_message(self, *args):
            pass

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(str(cert["cert"]), str(cert["key"]))
    dst = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    dst.socket = tls.wrap_socket(dst.socket, server_side=True)
    src = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    src.socket = tls.wrap_socket(src.socket, server_side=True)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (src, dst)]
    for t in threads:
        t.start()
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(
        tlstrust, "client_context", lambda: ssl.create_default_context(cafile=str(cert["ca"]))
    )
    try:
        status, *_ = client._http(
            "GET",
            f"https://127.0.0.1:{src.server_port}/start",
            1024,
            {"X-Tavotto-Device": "fixture-secret"},
        )
        assert status == 302 and received == []
    finally:
        for server in (src, dst):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()


def test_stale_revision_refreshes_once_and_returns_new_fact(isolated, monkeypatch):
    key, _ = isolated
    old_raw = json.dumps(fact_bundle()).encode()
    old = index_for(old_raw, 4)
    client._save_index(signed(key, old), old)
    new_bundle = fact_bundle()
    new_bundle["facts"][0]["value"] = 1400
    new_raw = json.dumps(new_bundle).encode()
    new = index_for(new_raw, 5)
    urls = []

    def request(method, url, cap, headers):
        urls.append(url)
        if url.endswith("index.signed.json"):
            return 200, signed(key, new)
        if "rev=4" in url:
            return 404, b'{"code":"stale_rev"}'
        if "rev=5" in url:
            return 200, new_raw
        raise AssertionError(url)

    monkeypatch.setattr(client, "_request", request)
    result = client.get_journal("fx-journal")
    assert result["catalog_rev"] == 5 and result["bundle"]["facts"][0]["value"] == 1400
    assert len(urls) == 3 and "rev=4" in urls[0] and "rev=5" in urls[2]


def test_facts_pagination_includes_corresponding_sources(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    fact2 = copy.deepcopy(obj["facts"][0])
    fact2["id"] = "photo"
    fact2["conditions"] = {"figure_kind": "photo"}
    fact2["value"] = 300
    obj["facts"].append(fact2)
    raw = json.dumps(obj).encode()
    idx = index_for(raw, 2)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    first = client.get_facts("fx-journal", limit=1)
    second = client.get_facts("fx-journal", offset=first["next_offset"], limit=1)
    assert first["total"] == 2 and first["facts"][0]["id"] == "line"
    assert first["sources"]["s"]["url"] == obj["sources"]["s"]["url"]
    assert second["facts"][0]["id"] == "photo" and second["next_offset"] is None
    with pytest.raises(client.FactsError, match="bad_request"):
        client.get_facts("fx-journal", limit=21)


def test_unverified_or_missing_cache_never_falls_back_to_v1(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    obj["facts"][0]["review"] = {"status": "pending"}
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    with pytest.raises(client.FactsError, match="bundle_invalid"):
        client.get_journal("fx-journal")
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    with pytest.raises(client.FactsError, match="offline"):
        client.get_journal("fx-journal")


def test_mcp_page_preserves_scoped_coverage_and_sources(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    obj["coverage"] = [
        {
            "id": "figure_section_dpi",
            "subject": "font_size",
            "result": "not_stated",
            "scope": "The Figures section of the captured author guide",
            "sources": ["s"],
            "review": {"status": "verified", "reviewed": "2026-11-11", "method": "fixture"},
        }
    ]
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    first = client.get_facts("fx-journal", limit=1)
    assert first["facts_total"] == 1 and first["coverage_total"] == 1
    assert first["facts"] and not first["coverage"] and first["next_offset"] == 1
    second = client.get_facts("fx-journal", offset=1, limit=1)
    assert second["coverage"][0]["result"] == "not_stated"
    assert second["coverage"][0]["scope"] == obj["coverage"][0]["scope"]
    assert second["sources"]["s"] == obj["sources"]["s"] and second["next_offset"] is None


def test_hard_switch_rechecked_after_tls_setup(monkeypatch):
    state = {"disabled": False, "opened": False}
    monkeypatch.setattr(journalremote, "disabled", lambda: state["disabled"])

    def context():
        state["disabled"] = True
        return object()

    monkeypatch.setattr(tlstrust, "client_context", context)
    monkeypatch.setattr(tlstrust, "https_handler", lambda _context: object())

    class Opener:
        def open(self, *_args, **_kwargs):
            state["opened"] = True
            raise AssertionError("network was attempted after the switch")

    monkeypatch.setattr(client.urllib.request, "build_opener", lambda *_args: Opener())
    with pytest.raises(journalremote.RemoteCatalogError) as err:
        client._http("GET", "https://example.invalid/v2", 100, {"X-Tavotto-Device": "secret"})
    assert err.value.code == "disabled" and not state["opened"]


def test_tampered_signed_index_is_rejected_before_cache_write(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    idx = index_for(raw, 7)
    envelope = json.loads(signed(key, idx))
    payload = json.loads(base64.b64decode(envelope["payload"]))
    payload["catalog_rev"] = 8
    envelope["payload"] = base64.b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setattr(client, "_request", lambda *args: (200, json.dumps(envelope).encode()))
    result = client.sync()
    assert result["state"] == "rejected" and result["code"] == "signature_invalid"
    assert client.cached_index() is None


def test_byte_budget_paginates_without_silent_loss(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    for i in range(1, 4):
        fact = copy.deepcopy(obj["facts"][0])
        fact["id"] = f"fact-{i}"
        fact["quote"] = "X" * 1400
        obj["facts"].append(fact)
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    monkeypatch.setattr(client, "MAX_PAGE_BYTES", 2600)
    offset, seen = 0, []
    while offset is not None:
        page = client.get_facts("fx-journal", offset=offset)
        seen.extend(f["id"] for f in page["facts"])
        offset = page["next_offset"]
    assert seen == ["line", "fact-1", "fact-2", "fact-3"]


def test_facts_page_includes_sources_for_noncontiguous_context(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    obj["sources"]["intro"] = {
        "url": "https://example.invalid/intro",
        "kind": "html",
        "retrieved": "2026-11-11",
    }
    obj["facts"][0]["context"] = [
        {"source": "intro", "quote": "Authors are encouraged to follow these guidelines."}
    ]
    obj["facts"][0]["conditions"]["purpose"] = "online_color"
    obj["facts"][0]["strength"] = "recommended"
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    page = client.get_facts("fx-journal")
    assert page["facts"][0]["context"][0]["source"] == "intro"
    assert page["facts"][0]["strength"] == "recommended"
    assert page["facts"][0]["conditions"]["purpose"] == "online_color"
    assert page["sources"]["intro"] == obj["sources"]["intro"]


def test_unknown_structured_conditions_are_returned_intact_not_evaluated(isolated, monkeypatch):
    key, _ = isolated
    obj = fact_bundle()
    obj["facts"][0]["conditions"]["width"] = {"relation": "gt", "value": 100, "unit": "mm"}
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    page = client.get_facts("fx-journal")
    assert page["facts"][0]["conditions"]["width"] == {"relation": "gt", "value": 100, "unit": "mm"}
    assert "override" not in page and "preflight" not in page
    assert page["catalog_completeness"] == "unknown"


def test_nonexclusive_permission_and_preference_are_returned_without_projection(
    isolated, monkeypatch
):
    key, _ = isolated
    obj = fact_bundle()
    preferred = obj["facts"][0]
    preferred.update(
        subject="figure_file_format", relation="in", value=["EPS"], strength="preferred"
    )
    preferred.pop("unit", None)
    permitted = copy.deepcopy(preferred)
    permitted.update(id="office", value=["MS Office"], strength="permitted", conditions={})
    obj["facts"].append(permitted)
    raw = json.dumps(obj).encode()
    idx = index_for(raw)
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, raw))
    page = client.get_facts("fx-journal")
    assert [(f["strength"], f["value"]) for f in page["facts"]] == [
        ("preferred", ["EPS"]),
        ("permitted", ["MS Office"]),
    ]
    assert "override" not in page and "preflight" not in page


def test_direct_facts_refreshes_index_and_reports_verified_offline_cache(isolated, monkeypatch):
    key, _ = isolated
    original = fact_bundle()
    updated = copy.deepcopy(original)
    updated["facts"][0]["value"] = 1400
    updated["facts"][0]["quote"] = "Line art: 1400 dpi."
    bodies = {1: json.dumps(original).encode(), 2: json.dumps(updated).encode()}
    indices = {rev: index_for(raw, rev) for rev, raw in bodies.items()}
    current = {"rev": 1, "offline": False}
    requests = []

    def request(method, url, cap, headers):
        requests.append((url, headers))
        if current["offline"]:
            raise journalremote.RemoteCatalogError("network")
        if url.endswith("index.signed.json"):
            assert "fx-journal" not in url and "X-Tavotto-Device" not in headers
            return 200, signed(key, indices[current["rev"]])
        return 200, bodies[current["rev"]]

    monkeypatch.setattr(client, "_request", request)
    first = client.get_facts("fx-journal")
    assert first["catalog_rev"] == 1 and first["facts"][0]["value"] == 1200
    current["rev"] = 2
    second = client.get_facts("fx-journal")
    assert second["catalog_rev"] == 2 and second["facts"][0]["value"] == 1400
    assert second["sync_state"] == "updated" and second["sync_code"] is None
    current["offline"] = True
    cached = client.get_facts("fx-journal")
    assert cached["catalog_rev"] == 2 and cached["facts"][0]["value"] == 1400
    assert cached["source"] == "cache" and cached["sync_state"] == "offline"
    assert cached["sync_code"] == "network"
    n = len(requests)
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    assert client.get_facts("fx-journal")["sync_state"] == "disabled"
    assert len(requests) == n


def test_direct_facts_finds_journal_added_after_cached_index(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    old = index_for(raw, 1)
    old["journals"] = {}
    client._save_index(signed(key, old), old)
    new = index_for(raw, 2)

    def request(method, url, cap, headers):
        if url.endswith("index.signed.json"):
            return 200, signed(key, new)
        return 200, raw

    monkeypatch.setattr(client, "_request", request)
    result = client.get_facts("fx-journal")
    assert result["journal_id"] == "fx-journal" and result["catalog_rev"] == 2
    assert result["sync_state"] == "updated" and result["facts_total"] == 1


def test_invalid_journal_id_does_not_trigger_index_request(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "_request", lambda *args: calls.append(args))
    with pytest.raises(client.FactsError, match="bad_request"):
        client.get_facts("invalid/id")
    assert calls == []


def test_missing_journal_requires_successful_index_refresh(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    idx = index_for(raw)
    idx["journals"] = {}
    client._save_index(signed(key, idx), idx)
    monkeypatch.setattr(client, "_request", lambda *args: (200, signed(key, idx)))
    with pytest.raises(client.FactsError, match="unknown_journal") as missing:
        client.get_facts("new-journal")
    assert missing.value.code == "unknown_journal"

    def offline(*args):
        raise journalremote.RemoteCatalogError("network")

    monkeypatch.setattr(client, "_request", offline)
    with pytest.raises(client.FactsError, match="network") as unavailable:
        client.get_facts("new-journal")
    assert unavailable.value.sync_state == "offline" and unavailable.value.sync_code == "network"
    monkeypatch.setattr(journalremote, "disabled", lambda: True)
    with pytest.raises(client.FactsError, match="disabled") as disabled:
        client.get_facts("new-journal")
    assert disabled.value.sync_state == "disabled" and disabled.value.sync_code == "disabled"
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setattr(client, "_request", lambda *args: (200, b"{}"))
    with pytest.raises(client.FactsError, match="index_invalid") as rejected:
        client.get_facts("new-journal")
    assert rejected.value.sync_state == "rejected" and rejected.value.sync_code == "index_invalid"


def test_pagination_revision_guard_rejects_cross_revision_and_allows_offline_cache(
    isolated, monkeypatch
):
    key, _ = isolated
    first_bundle = fact_bundle()
    second_fact = copy.deepcopy(first_bundle["facts"][0])
    second_fact.update(id="photo", conditions={"figure_kind": "photo"}, value=300)
    first_bundle["facts"].append(second_fact)
    newer_bundle = copy.deepcopy(first_bundle)
    added = copy.deepcopy(newer_bundle["facts"][0])
    added.update(id="new-before", conditions={"figure_kind": "color"})
    newer_bundle["facts"].insert(0, added)
    bodies = {1: json.dumps(first_bundle).encode(), 2: json.dumps(newer_bundle).encode()}
    indices = {rev: index_for(raw, rev) for rev, raw in bodies.items()}
    state = {"rev": 1, "offline": False}

    def request(method, url, cap, headers):
        if state["offline"]:
            raise journalremote.RemoteCatalogError("network")
        if url.endswith("index.signed.json"):
            return 200, signed(key, indices[state["rev"]])
        return 200, bodies[state["rev"]]

    monkeypatch.setattr(client, "_request", request)
    first = client.get_facts("fx-journal", limit=1)
    assert first["catalog_rev"] == 1 and first["facts"][0]["id"] == "line"
    state["offline"] = True
    cached = client.get_facts("fx-journal", offset=1, limit=1, expected_catalog_rev=1)
    assert cached["catalog_rev"] == 1 and cached["facts"][0]["id"] == "photo"
    assert cached["sync_state"] == "offline"
    state.update(rev=2, offline=False)
    # A legacy offset-only client would silently repeat "line" from the new revision.
    legacy = client.get_facts("fx-journal", offset=1, limit=1)
    assert legacy["catalog_rev"] == 2 and legacy["facts"][0]["id"] == "line"
    with pytest.raises(client.FactsError, match="catalog_changed") as changed:
        client.get_facts("fx-journal", offset=1, limit=1, expected_catalog_rev=1)
    assert changed.value.expected_catalog_rev == 1 and changed.value.current_catalog_rev == 2
    restarted = client.get_facts("fx-journal", limit=1)
    assert restarted["catalog_rev"] == 2 and restarted["facts"][0]["id"] == "new-before"


def test_revision_guard_catches_concurrent_refresh_during_journal_read(isolated, monkeypatch):
    key, _ = isolated
    raw1 = json.dumps(fact_bundle()).encode()
    newer = fact_bundle()
    newer["facts"][0]["value"] = 1400
    raw2 = json.dumps(newer).encode()
    idx1, idx2 = index_for(raw1, 1), index_for(raw2, 2)
    client._save_index(signed(key, idx1), idx1)

    def request(method, url, cap, headers):
        return (200, signed(key, idx1)) if url.endswith("index.signed.json") else (200, raw2)

    monkeypatch.setattr(client, "_request", request)
    original = client._cached_journal

    def concurrent_refresh(jid):
        client._save_index(signed(key, idx2), idx2)
        return original(jid)

    monkeypatch.setattr(client, "_cached_journal", concurrent_refresh)
    with pytest.raises(client.FactsError, match="catalog_changed") as changed:
        client.get_facts("fx-journal", offset=1, expected_catalog_rev=1)
    assert changed.value.current_catalog_rev == 2
    assert client.cached_index()["catalog_rev"] == 2
    # A new first page binds to the actual bundle revision even after a race.
    first = client.get_facts("fx-journal", limit=1)
    assert first["catalog_rev"] == 2 and first["facts"][0]["value"] == 1400


def test_search_page_can_be_bound_to_catalog_revision(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    rev = {"value": 1}
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args: (200, signed(key, idx1 if rev["value"] == 1 else idx2)),
    )
    assert client.search("fixture")["catalog_rev"] == 1
    rev["value"] = 2
    with pytest.raises(client.FactsError, match="catalog_changed"):
        client.search("fixture", offset=1, expected_catalog_rev=1)
    for invalid in (True, 0, -1, 10_000_000_000, "1"):
        with pytest.raises(client.FactsError, match="bad_request"):
            client.search("fixture", expected_catalog_rev=invalid)


def test_guard_reports_revision_change_if_concurrent_index_removes_journal(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    idx2["journals"] = {}
    client._save_index(signed(key, idx1), idx1)
    monkeypatch.setattr(client, "_request", lambda *args: (200, signed(key, idx1)))
    original = client._cached_journal

    def concurrent_removal(jid):
        client._save_index(signed(key, idx2), idx2)
        return original(jid)

    monkeypatch.setattr(client, "_cached_journal", concurrent_removal)
    with pytest.raises(client.FactsError, match="catalog_changed") as changed:
        client.get_facts("fx-journal", offset=1, expected_catalog_rev=1)
    assert changed.value.expected_catalog_rev == 1 and changed.value.current_catalog_rev == 2


@pytest.mark.parametrize("code", ["stale_rev", "cache_invalid", "bundle_invalid"])
def test_guard_preserves_integrity_errors_during_concurrent_revision(isolated, monkeypatch, code):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    idx1, idx2 = index_for(raw, 1), index_for(raw, 2)
    client._save_index(signed(key, idx1), idx1)
    monkeypatch.setattr(client, "_request", lambda *args: (200, signed(key, idx1)))

    def interrupted(*args, **kwargs):
        client._save_index(signed(key, idx2), idx2)
        raise client.FactsError(code)

    monkeypatch.setattr(client, "get_journal", interrupted)
    expected_error = "catalog_changed" if code == "stale_rev" else code
    with pytest.raises(client.FactsError, match=expected_error):
        client.get_facts("fx-journal", offset=1, expected_catalog_rev=1)


# ---- 省流量：ETag / 304、gzip、5 分钟新鲜期 --------------------------------------------------------
def _index_server(key, monkeypatch, index_doc, etag='"e1"'):
    """假服务器：记录每次索引请求的头；If-None-Match 命中回 304，否则 200 + ETag。"""
    calls = []

    def request(method, url, cap, headers):
        assert url.endswith("index.signed.json")
        calls.append(dict(headers))
        if etag is not None and headers.get("If-None-Match") == etag:
            return 304, b"", {}
        return 200, index_doc, ({"etag": etag} if etag else {})

    monkeypatch.setattr(client, "_request", request)
    return calls


def test_sync_sends_conditional_gzip_request_and_reuses_cache_on_304(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    doc = signed(key, index_for(raw, 6))
    calls = _index_server(key, monkeypatch, doc)
    first = client.sync()
    assert first["state"] == "updated" and first["index"]["catalog_rev"] == 6
    assert calls[0]["Accept-Encoding"] == "gzip" and "If-None-Match" not in calls[0]
    second = client.sync()
    assert second["state"] == "unchanged" and second["code"] is None
    assert second["index"] == first["index"] and len(calls) == 2
    assert calls[1]["If-None-Match"] == '"e1"'  # 服务端给的 ETag 原样回
    assert client.cached_index() == first["index"]


def test_new_content_after_etag_changes_updates_the_cache(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    docs = {"v": signed(key, index_for(raw, 6)), "etag": '"e1"'}

    def request(method, url, cap, headers):
        if headers.get("If-None-Match") == docs["etag"]:
            return 304, b"", {}
        return 200, docs["v"], {"etag": docs["etag"]}

    monkeypatch.setattr(client, "_request", request)
    assert client.sync()["state"] == "updated"
    docs.update(v=signed(key, index_for(raw, 7)), etag='"e2"')
    result = client.sync()
    assert result["state"] == "updated" and result["index"]["catalog_rev"] == 7
    assert client.sync()["state"] == "unchanged"


def test_304_never_replaces_a_missing_or_foreign_cache(isolated, monkeypatch):
    """没有缓存就不会带 If-None-Match；服务端无缘无故回 304 当失败，不当「沿用缓存」。"""
    calls = []

    def request(method, url, cap, headers):
        calls.append(dict(headers))
        return 304, b"", {}

    monkeypatch.setattr(client, "_request", request)
    result = client.sync()
    assert "If-None-Match" not in calls[0]
    assert result["index"] is None and result["state"] in ("offline", "rejected")


def test_etag_is_bound_to_the_stored_envelope(isolated, monkeypatch):
    """信封被别处换掉后，旧 ETag 不能再用（否则会对着新缓存说「没变」）。"""
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    calls = _index_server(key, monkeypatch, signed(key, index_for(raw, 6)))
    client.sync()
    newer = index_for(raw, 7)
    client._save_index(signed(key, newer), newer)  # 别的进程落了新信封，没有 ETag
    client.sync()
    assert "If-None-Match" not in calls[-1]


def test_fresh_window_skips_the_request_and_force_bypasses_it(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    calls = _index_server(key, monkeypatch, signed(key, index_for(raw, 6)))
    clock = {"t": 1000.0}
    monkeypatch.setattr(client, "_now", lambda: clock["t"])
    monkeypatch.setattr(client, "INDEX_FRESH_S", 300)
    assert client.sync()["state"] == "updated"
    clock["t"] += 299
    assert client.sync()["state"] == "unchanged" and len(calls) == 1  # 新鲜期内不发请求
    assert client.sync(force=True)["state"] == "unchanged" and len(calls) == 2
    clock["t"] += 301
    assert (
        client.sync()["state"] == "unchanged" and len(calls) == 3
    )  # 过期：带 If-None-Match 重新确认
    assert calls[-1]["If-None-Match"] == '"e1"'
    clock["t"] -= 10_000  # 时钟被往回拨：当作过期，不是永远新鲜
    client.sync()
    assert len(calls) == 4


def test_failed_refresh_keeps_verified_cache_and_does_not_extend_freshness(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    state = {"fail": False}

    def request(method, url, cap, headers):
        if state["fail"]:
            raise journalremote.RemoteCatalogError("network", "down")
        return 200, signed(key, index_for(raw, 6)), {"etag": '"e1"'}

    monkeypatch.setattr(client, "_request", request)
    clock = {"t": 1000.0}
    monkeypatch.setattr(client, "_now", lambda: clock["t"])
    monkeypatch.setattr(client, "INDEX_FRESH_S", 300)
    client.sync()
    clock["t"] += 400
    state["fail"] = True
    result = client.sync()
    assert result["state"] == "offline" and result["index"]["catalog_rev"] == 6
    clock["t"] += 1  # 失败没有刷新「上次确认」：仍然过期，继续尝试
    assert client.sync()["state"] == "offline"


def test_tampered_200_is_still_rejected_and_rollback_still_blocked(isolated, monkeypatch):
    key, _ = isolated
    raw = json.dumps(fact_bundle()).encode()
    good = signed(key, index_for(raw, 6))
    monkeypatch.setattr(client, "_request", lambda *a: (200, good, {"etag": '"e1"'}))
    client.sync()
    lower = signed(key, index_for(raw, 5))
    monkeypatch.setattr(client, "_request", lambda *a: (200, lower, {"etag": '"e0"'}))
    assert client.sync()["code"] == "rev_rollback" and client.cached_index()["catalog_rev"] == 6
    bad = json.loads(good)
    bad["sig"] = bad["sig"][:-4] + "AAAA"
    monkeypatch.setattr(client, "_request", lambda *a: (200, json.dumps(bad).encode(), {}))
    assert client.sync()["code"] == "signature_invalid"


def test_gzip_index_is_decoded_and_capped_after_decompression(isolated, monkeypatch):
    import gzip

    # _gunzip 单测：正常、超限（压缩炸弹：很小的压缩体解出超过上限）、坏数据
    body = b"x" * 1000
    assert client._gunzip(gzip.compress(body), 2000) == body
    bomb = gzip.compress(b"\0" * (4 << 20))
    assert len(bomb) < 10_000
    with pytest.raises(journalremote.RemoteCatalogError, match="too large"):
        client._gunzip(bomb, 1 << 20)
    with pytest.raises(journalremote.RemoteCatalogError):
        client._gunzip(b"not gzip", 100)


def _serve_tls(handler, cert):
    import ssl

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(str(cert["cert"]), str(cert["key"]))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.socket = tls.wrap_socket(srv.socket, server_side=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


@pytest.mark.parametrize("mode", ["gzip", "bomb", "br", "plain"])
def test_http_transport_content_encoding(monkeypatch, mode, tmp_path):
    """真 HTTPS 往返：gzip 解压后封顶；未知编码拒绝；明文照旧；ETag 带回。"""
    import gzip
    import ssl

    from support.tls_certs import make_pki

    cert = make_pki().write(tmp_path)
    payload = b"y" * 500
    sent = {"inm": None}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            sent["inm"] = self.headers.get("Accept-Encoding")
            body = {
                "gzip": gzip.compress(payload),
                "bomb": gzip.compress(b"\0" * (4 << 20)),
                "br": payload,
                "plain": payload,
            }[mode]
            self.send_response(200)
            if mode in ("gzip", "bomb"):
                self.send_header("Content-Encoding", "gzip")
            if mode == "br":
                self.send_header("Content-Encoding", "br")
            self.send_header("ETag", '"abc"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv, thread = _serve_tls(H, cert)
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.setattr(
        tlstrust, "client_context", lambda: ssl.create_default_context(cafile=str(cert["ca"]))
    )
    try:
        url = f"https://127.0.0.1:{srv.server_port}/x"
        if mode in ("gzip", "plain"):
            status, data, got = client._http("GET", url, 1 << 20, {"Accept-Encoding": "gzip"})
            assert (status, data, got) == (200, payload, {"etag": '"abc"'})
            assert sent["inm"] == "gzip"
        else:
            with pytest.raises(journalremote.RemoteCatalogError):
                client._http("GET", url, 1 << 20, {"Accept-Encoding": "gzip"})
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()
