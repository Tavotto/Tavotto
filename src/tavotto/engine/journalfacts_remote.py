"""Signed, isolated read-only v2 journal facts client.

This module does not produce v1 publication overrides or change preflight.
The v2 cache is independent from v1; the device token remains shared.
"""

from __future__ import annotations

import binascii
import hashlib
import json
import re
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import (
    atomicio,
    config,
    contactsend,
    journalfacts_contract as contract,
    journalremote,
    tlstrust,
)

_FACTS_URLS = (
    "https://tavotto.cn/v2/journal-catalog/",
    "https://www.tavotto.com/v2/journal-catalog/",
)
_FILE = re.compile(r"^snapshots/([0-9a-f]{64})\.webp$")
MAX_INDEX_BYTES = 16 << 20
MAX_SNAPSHOT_BYTES = 8 << 20
MAX_PAGE_BYTES = 300 << 10


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward a device token, or any other header, to a redirected origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(method: str, url: str, cap: int, headers: dict[str, str]) -> tuple[int, bytes]:
    """HTTPS using the shared TLS trust, with redirects refused at every origin."""
    if not url.startswith("https://"):
        raise journalremote.RemoteCatalogError("network", "https required")
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": journalremote._user_agent(), "Accept": "*/*", **headers},
        data=b"" if method == "POST" else None,
    )
    opener = urllib.request.build_opener(
        tlstrust.https_handler(tlstrust.client_context()), _NoRedirect()
    )
    # Trust-store and proxy setup can take time; re-check immediately before I/O.
    if journalremote.disabled():
        raise journalremote.RemoteCatalogError("disabled")
    try:
        with opener.open(req, timeout=journalremote.NETWORK_TIMEOUT_S) as response:
            status, data = response.status, response.read(cap + 1)
    except urllib.error.HTTPError as exc:
        status, data = exc.code, exc.read(65536)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise journalremote.RemoteCatalogError("network", type(exc).__name__) from exc
    if status == 200 and len(data) > cap:
        raise journalremote.RemoteCatalogError("bundle_invalid", "response too large")
    return status, data


_request = _http


def _call(method: str, url: str, cap: int, headers: dict[str, str]) -> tuple[int, bytes]:
    if journalremote.disabled():
        raise journalremote.RemoteCatalogError("disabled")
    return _request(method, url, cap, {"X-Tavotto-Client": journalremote.client_type(), **headers})


class FactsError(RuntimeError):
    def __init__(
        self,
        code: str,
        retry_after_s: int | None = None,
        *,
        sync_state: str | None = None,
        sync_code: str | None = None,
        expected_catalog_rev: int | None = None,
        current_catalog_rev: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retry_after_s = retry_after_s
        self.sync_state = sync_state
        self.sync_code = sync_code
        self.expected_catalog_rev = expected_catalog_rev
        self.current_catalog_rev = current_catalog_rev


def cache_dir() -> Path:
    return config.data_dir() / "journal-catalog-v2"


def _bases(ui_locale: str | None = None) -> tuple[str, ...]:
    locale = ui_locale or journalremote._read_json(journalremote._state_path()).get("locale")
    cn = contactsend.region_is_cn(locale if isinstance(locale, str) else None)
    return _FACTS_URLS if cn else tuple(reversed(_FACTS_URLS))


def _json(raw: bytes) -> object:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise FactsError("invalid_json")
            out[key] = value
        return out

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _v: (_ for _ in ()).throw(FactsError("invalid_json")),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise FactsError("invalid_json") from exc


def verify_index(raw: bytes) -> dict:
    """Verify signature, schema and contents; use only the v1 trusted key authority."""
    if not isinstance(raw, bytes) or len(raw) > MAX_INDEX_BYTES:
        raise FactsError("index_invalid")
    env = _json(raw)
    if not isinstance(env, dict) or set(env) != {"alg", "key_id", "payload", "sig"}:
        raise FactsError("index_invalid")
    if env["alg"] != "ed25519" or not isinstance(env["key_id"], str):
        raise FactsError("signature_invalid")
    key64 = journalremote.TRUSTED_KEYS.get(env["key_id"])
    if not key64:
        raise FactsError("key_unknown")
    try:
        key = journalremote._b64decode(key64)
        payload = journalremote._b64decode(env["payload"])
        signature = journalremote._b64decode(env["sig"])
    except (TypeError, ValueError, binascii.Error) as exc:
        raise FactsError("signature_invalid") from exc
    if not journalremote._ed25519_verify(key, payload, signature):
        raise FactsError("signature_invalid")
    try:
        return contract.validate_index(_json(payload))
    except contract.FactValidationError as exc:
        raise FactsError("index_invalid") from exc


def _connect() -> sqlite3.Connection:
    try:
        cache_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(cache_dir() / "cache.sqlite3", timeout=10, isolation_level=None)
        con.execute(
            "CREATE TABLE IF NOT EXISTS current_index (singleton INTEGER PRIMARY KEY CHECK(singleton=1), rev INTEGER NOT NULL, envelope BLOB NOT NULL)"
        )
        return con
    except (OSError, sqlite3.Error) as exc:
        raise FactsError("cache_error") from exc


def _row(con: sqlite3.Connection) -> tuple[bytes, dict] | None:
    try:
        row = con.execute("SELECT rev, envelope FROM current_index WHERE singleton=1").fetchone()
    except sqlite3.Error as exc:
        raise FactsError("cache_error") from exc
    if row is None:
        return None
    raw = bytes(row[1])
    index = verify_index(raw)
    if row[0] != index["catalog_rev"]:
        raise FactsError("cache_invalid")
    return raw, index


def cached_index() -> dict | None:
    try:
        con = _connect()
        try:
            current = _row(con)
            return current[1] if current else None
        finally:
            con.close()
    except (sqlite3.Error, OSError) as exc:
        raise FactsError("cache_error") from exc


def _save_index(raw: bytes, index: dict) -> bool:
    """Serialize compare-and-write with SQLite so concurrent clients cannot lower rev."""
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        current = _row(con)
        if current is not None:
            old = current[1]
            if index["catalog_rev"] < old["catalog_rev"]:
                raise FactsError("rev_rollback")
            if index["catalog_rev"] == old["catalog_rev"]:
                if index != old:
                    raise FactsError("index_conflict")
                con.commit()
                return False
        con.execute(
            "INSERT OR REPLACE INTO current_index VALUES (1, ?, ?)", (index["catalog_rev"], raw)
        )
        con.commit()
        return True
    except (sqlite3.Error, OSError) as exc:
        con.rollback()
        raise FactsError("cache_error") from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _remote_error(exc: journalremote.RemoteCatalogError) -> FactsError:
    return FactsError(exc.code, exc.retry_after_s)


def sync(ui_locale: str | None = None) -> dict:
    """Refresh signed index; failure preserves and returns a verified v2 cache.

    No valid v2 cache means index=None, not a v1 or seed substitution.
    """
    old = cached_index()
    if journalremote.disabled():
        return {"state": "disabled", "code": "disabled", "index": old}
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            status, raw = _call("GET", base + "index.signed.json", MAX_INDEX_BYTES, {})
            if status != 200:
                raise _remote_error(journalremote._error_of(status, raw))
            index = verify_index(raw)
            changed = _save_index(raw, index)
            return {"state": "updated" if changed else "unchanged", "code": None, "index": index}
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except FactsError as exc:
            last = exc
        # A bad signature/schema or rollback is a security failure, not a reason
        # to silently switch origins and consume a different answer.
        if last.code not in ("network", "server_error"):
            break
    old = cached_index()
    return {
        "state": "offline" if last.code in ("network", "server_error") else "rejected",
        "code": last.code,
        "index": old,
    }


def search(
    query: str = "",
    *,
    offset: int = 0,
    limit: int = 20,
    ui_locale: str | None = None,
    expected_catalog_rev: int | None = None,
) -> dict:
    """Search signed v2 index only. If unavailable, say so without substituting v1."""
    if not isinstance(query, str) or len(query) > 512:
        raise FactsError("bad_request")
    _validate_page(offset, limit)
    _validate_expected_rev(expected_catalog_rev)
    result = sync(ui_locale)
    index = result["index"]
    if index is None:
        raise FactsError(result["code"] or "offline")
    _check_expected_rev(expected_catalog_rev, index["catalog_rev"])
    import unicodedata

    needle = unicodedata.normalize("NFKC", query).casefold().strip()
    entries = []
    for jid, item in sorted(index["journals"].items()):
        names = [
            jid,
            item["label"],
            index["publishers"][item["publisher"]]["label"],
            item.get("issn", ""),
            item.get("eissn", ""),
            *item.get("aliases", []),
        ]
        if needle and not any(
            needle in unicodedata.normalize("NFKC", name).casefold() for name in names
        ):
            continue
        entries.append({"id": jid, **{k: v for k, v in item.items() if k != "bundle"}})
    total = len(entries)
    out = {
        "catalog_rev": index["catalog_rev"],
        "journals": [],
        "total": total,
        "next_offset": None,
        "source": "cache" if result["state"] in ("offline", "rejected", "disabled") else "network",
        "sync_state": result["state"],
        "sync_code": result["code"],
    }
    for entry in entries[offset : offset + limit]:
        out["journals"].append(entry)
        count = len(out["journals"])
        out["next_offset"] = offset + count if offset + count < total else None
        if not _page_fits(out):
            out["journals"].pop()
            if not out["journals"]:
                raise FactsError("result_too_large")
            out["next_offset"] = offset + len(out["journals"])
            break
    return out


def _page_fits(value: dict) -> bool:
    return (
        len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        <= MAX_PAGE_BYTES
    )


def _validate_page(offset: int, limit: int) -> None:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise FactsError("bad_request")


def _validate_expected_rev(expected: int | None) -> None:
    if expected is not None and (type(expected) is not int or not 1 <= expected <= 9_999_999_999):
        raise FactsError("bad_request")


def _check_expected_rev(expected: int | None, current: int) -> None:
    if expected is not None and expected != current:
        raise FactsError(
            "catalog_changed", expected_catalog_rev=expected, current_catalog_rev=current
        )


def _read_bundle(index: dict, jid: str) -> dict | None:
    entry = index["journals"].get(jid)
    if entry is None:
        raise FactsError("unknown_journal")
    path = cache_dir() / "bundles" / (entry["bundle"]["sha256"] + ".json")
    try:
        with path.open("rb") as stream:
            raw = stream.read(contract.MAX_BUNDLE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise FactsError("cache_error") from exc
    try:
        return contract.validate_indexed_bundle(index, jid, raw)
    except contract.FactValidationError as exc:
        raise FactsError("cache_invalid") from exc


def _cached_journal(jid: str) -> tuple[dict | None, dict | None]:
    """Read index and its cached bundle under a SQLite snapshot transaction."""
    con = _connect()
    try:
        con.execute("BEGIN")
        row = _row(con)
        if row is None:
            con.commit()
            return None, None
        index = row[1]
        cached = _read_bundle(index, jid)
        con.commit()
        return index, cached
    except (sqlite3.Error, OSError) as exc:
        con.rollback()
        raise FactsError("cache_error") from exc
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _token_call(base: str, route: str, cap: int) -> bytes:
    if journalremote.disabled():
        raise FactsError("offline")
    tok = journalremote._token(base)
    status, raw = _call("GET", base + route, cap, {"X-Tavotto-Device": tok})
    if status == 401:
        err = journalremote._error_of(status, raw)
        if err.code == "device_unknown":
            tok = journalremote._token(base, renew=True, stale=tok)
            status, raw = _call("GET", base + route, cap, {"X-Tavotto-Device": tok})
    if status != 200:
        raise journalremote._error_of(status, raw)
    return raw


def get_journal(jid: str, *, ui_locale: str | None = None, _allow_refresh: bool = True) -> dict:
    """Return source facts and provenance, never a preflight profile or v1 fallback."""
    if not isinstance(jid, str) or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", jid) is None:
        raise FactsError("bad_request")
    index, cached = _cached_journal(jid)
    if index is None:
        state = sync(ui_locale)
        if state["index"] is None:
            raise FactsError(state["code"] or "offline")
        index, cached = _cached_journal(jid)
        if index is None:
            raise FactsError("cache_invalid")
    if cached is not None:
        return {"catalog_rev": index["catalog_rev"], "source": "cache", "bundle": cached}
    if journalremote.disabled():
        raise FactsError("offline")
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            rev = index["catalog_rev"]
            raw = _token_call(
                base, f"journals/{quote(jid, safe='')}?rev={rev}", contract.MAX_BUNDLE_BYTES
            )
            try:
                contract.validate_indexed_bundle(index, jid, raw)
            except contract.FactValidationError as exc:
                raise FactsError("bundle_invalid") from exc
            # Re-read under the same write transaction used by sync: a new index
            # cannot race an older bundle into the current cache.
            con = _connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                row = _row(con)
                if row is None:
                    raise FactsError("cache_invalid")
                latest = row[1]
                try:
                    checked = contract.validate_indexed_bundle(latest, jid, raw)
                except contract.FactValidationError as exc:
                    raise FactsError("stale_rev") from exc
                digest = latest["journals"][jid]["bundle"]["sha256"]
                path = cache_dir() / "bundles" / (digest + ".json")
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomicio.write_bytes(path, raw, mode=0o600)
                con.commit()
                return {
                    "catalog_rev": latest["catalog_rev"],
                    "source": "network",
                    "bundle": checked,
                }
            finally:
                con.close()
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except (OSError, sqlite3.Error) as exc:
            raise FactsError("cache_error") from exc
        except FactsError as exc:
            last = exc
        if last.code not in ("network", "server_error"):
            break
    if last.code == "stale_rev" and _allow_refresh:
        state = sync(ui_locale)
        fresh = state["index"]
        if fresh is not None and fresh["catalog_rev"] > index["catalog_rev"]:
            return get_journal(jid, ui_locale=ui_locale, _allow_refresh=False)
    raise last


def get_facts(
    jid: str,
    *,
    offset: int = 0,
    limit: int = 20,
    ui_locale: str | None = None,
    expected_catalog_rev: int | None = None,
) -> dict:
    """Page facts and scoped coverage together, with all referenced sources."""
    _validate_page(offset, limit)
    _validate_expected_rev(expected_catalog_rev)
    if not isinstance(jid, str) or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", jid) is None:
        raise FactsError("bad_request")
    # Refresh the public signed index even when the journal is already cached.
    # No journal ID or device token is sent with this request. On failure, sync
    # returns a verified cache and exposes its status to the caller.
    state = sync(ui_locale)
    if state["index"] is not None:
        _check_expected_rev(expected_catalog_rev, state["index"]["catalog_rev"])
    if state["index"] is None or (
        jid not in state["index"]["journals"] and state["state"] not in ("updated", "unchanged")
    ):
        raise FactsError(
            state["code"] or "offline", sync_state=state["state"], sync_code=state["code"]
        )
    try:
        result = get_journal(jid, ui_locale=ui_locale)
    except FactsError as exc:
        # An index refresh can remove the journal after sync but before its
        # bundle is read. Do not misreport a version race as a missing journal.
        # Integrity/cache errors remain errors and are never hidden by a race.
        if expected_catalog_rev is not None and exc.code in ("unknown_journal", "stale_rev"):
            current = cached_index()
            if current is not None:
                _check_expected_rev(expected_catalog_rev, current["catalog_rev"])
        raise
    _check_expected_rev(expected_catalog_rev, result["catalog_rev"])
    bundle = result["bundle"]
    facts = bundle["facts"]
    coverage = bundle.get("coverage", [])
    records = [("facts", fact) for fact in facts] + [("coverage", item) for item in coverage]
    total = len(records)
    out = {
        "catalog_rev": result["catalog_rev"],
        "source": result["source"],
        "journal_id": bundle["journal_id"],
        "journal": bundle["journal"],
        "catalog_completeness": "unknown",
        "sync_state": state["state"],
        "sync_code": state["code"],
        "facts": [],
        "coverage": [],
        "sources": {},
        "facts_total": len(facts),
        "coverage_total": len(coverage),
        "total": total,
        "next_offset": None,
    }
    count = 0
    for kind, record in records[offset : offset + limit]:
        out[kind].append(record)
        refs = (
            [record["source"], *(item["source"] for item in record.get("context", []))]
            if kind == "facts"
            else record["sources"]
        )
        for sid in refs:
            out["sources"][sid] = bundle["sources"][sid]
        if not _page_fits(out):
            out[kind].pop()
            source_ids = {f["source"] for f in out["facts"]}
            source_ids.update(item["source"] for f in out["facts"] for item in f.get("context", []))
            source_ids.update(sid for item in out["coverage"] for sid in item["sources"])
            out["sources"] = {sid: bundle["sources"][sid] for sid in sorted(source_ids)}
            if not count:
                raise FactsError("result_too_large")
            break
        count += 1
    out["next_offset"] = offset + count if offset + count < total else None
    return out


def snapshot(jid: str, file: str, *, ui_locale: str | None = None) -> Path:
    """Fetch only snapshots referenced by this validated journal; verify every read."""
    match = _FILE.fullmatch(file) if isinstance(file, str) else None
    if match is None:
        raise FactsError("bad_request")
    result = get_journal(jid, ui_locale=ui_locale)
    referenced = any(f.get("snapshot", {}).get("file") == file for f in result["bundle"]["facts"])
    if not referenced:
        raise FactsError("unknown_snapshot")
    path = cache_dir() / file
    if path.is_file():
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_SNAPSHOT_BYTES + 1)
        except OSError as exc:
            raise FactsError("cache_error") from exc
        if len(data) <= MAX_SNAPSHOT_BYTES and hashlib.sha256(data).hexdigest() == match.group(1):
            return path
        raise FactsError("cache_invalid")
    if journalremote.disabled():
        raise FactsError("offline")
    last = FactsError("network")
    for base in _bases(ui_locale):
        try:
            raw = _token_call(base, file, MAX_SNAPSHOT_BYTES)
            if hashlib.sha256(raw).hexdigest() != match.group(1):
                raise FactsError("snapshot_invalid")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomicio.write_bytes(path, raw, mode=0o600)
            return path
        except journalremote.RemoteCatalogError as exc:
            last = _remote_error(exc)
        except OSError as exc:
            raise FactsError("cache_error") from exc
        except FactsError as exc:
            last = exc
        if last.code not in ("network", "server_error"):
            break
    raise last


_INSPECTION_KINDS = frozenset({"line_art", "halftone", "combination"})
_INSPECTION_STAGES = frozenset({"submission", "revision", "accepted", "production"})


def _structured_conditions(fact: dict, kind: str, stage: str) -> dict:
    """Compare a tiny explicit vocabulary; never infer the source's meaning."""
    reasons = []
    parts = []
    source_stage = fact.get("stage")
    if stage not in _INSPECTION_STAGES or source_stage not in _INSPECTION_STAGES | {"any"}:
        parts.append({"field": "stage", "status": "unknown"})
    else:
        parts.append(
            {
                "field": "stage",
                "status": "match" if source_stage in ("any", stage) else "mismatch",
            }
        )
    conditions = fact.get("conditions", {})
    if not isinstance(conditions, dict):
        reasons.append("invalid_conditions")
        conditions = {}
    for key, value in conditions.items():
        if key != "figure_kind" or kind not in _INSPECTION_KINDS:
            parts.append({"field": key, "status": "unknown"})
            continue
        if isinstance(value, str) and value in _INSPECTION_KINDS:
            candidates = [value]
        elif (
            isinstance(value, dict)
            and set(value) == {"relation", "value"}
            and value["relation"] == "in"
            and isinstance(value["value"], list)
            and value["value"]
            and all(isinstance(v, str) and v in _INSPECTION_KINDS for v in value["value"])
        ):
            candidates = value["value"]
        else:
            parts.append({"field": key, "status": "unknown"})
            continue
        parts.append({"field": key, "status": "match" if kind in candidates else "mismatch"})
    if kind not in _INSPECTION_KINDS:
        reasons.append("figure_kind_unknown")
    if fact.get("condition_text"):
        reasons.append("free_text_condition")
    if fact.get("context"):
        reasons.append("source_context_uninterpreted")
    statuses = {part["status"] for part in parts}
    status = (
        "unknown"
        if reasons or "unknown" in statuses
        else "mismatch"
        if "mismatch" in statuses
        else "match"
    )
    return {"status": status, "components": parts, "unknown_reasons": reasons}


def inspect_facts_page(page: dict, selection: dict) -> dict:
    """Compare structured fields only; signed bytes do not prove source semantics."""
    if not isinstance(selection, dict) or set(selection) - {"object_label", "figure_kind", "stage"}:
        raise FactsError("bad_request")
    label = selection.get("object_label")
    kind = selection.get("figure_kind", "unknown")
    stage = selection.get("stage", "unknown")
    if (
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 128
        or not isinstance(kind, str)
        or kind not in _INSPECTION_KINDS | {"unknown"}
        or not isinstance(stage, str)
        or stage not in _INSPECTION_STAGES | {"unknown"}
    ):
        raise FactsError("bad_request")
    if not isinstance(page, dict) or not isinstance(page.get("facts"), list):
        raise FactsError("bad_request")
    result = dict(page)
    result["inspection"] = {
        "object_label": label,
        "figure_kind": kind,
        "stage": stage,
        "semantic_review": "not_authenticated",
        "status": "not_verifiable",
        "facts": [
            {
                "id": fact["id"],
                "status": "not_verifiable",
                "reason": "semantic_review_not_authenticated",
                "structured_conditions": _structured_conditions(fact, kind, stage),
            }
            for fact in page["facts"]
        ],
    }
    return result
