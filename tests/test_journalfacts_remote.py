"""Signed v2 client: isolation, rollback, snapshots and fail-closed transport."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from nacl.signing import SigningKey

from tavotto.engine import journalfacts_remote as client, journalremote, tlstrust


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


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    key = SigningKey.generate()
    monkeypatch.setattr(
        journalremote, "TRUSTED_KEYS", {"fx": base64.b64encode(bytes(key.verify_key)).decode()}
    )
    monkeypatch.setattr(client, "cache_dir", lambda: tmp_path / "v2-cache")
    monkeypatch.setattr(
        client, "_bases", lambda _locale=None: ("https://fx.invalid/v2/journal-catalog/",)
    )
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setattr(journalremote, "_token", lambda _base, **_kw: "fixture-device-token")
    return key, tmp_path


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
        status, _ = client._http(
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
