"""Versioned journal facts: validate evidence-preserving API bundles.

Facts are source statements, not executable preflight overrides. The validator
never discards conditions, infers scope, or turns examples into a closed list.
"""

from __future__ import annotations

import datetime
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

SCHEMA = 2
MAX_BUNDLE_BYTES = 4 << 20
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STRENGTH = frozenset({"required", "recommended", "typical", "example", "unspecified"})
_STAGE = frozenset({"any", "submission", "revision", "accepted", "production", "unknown"})
_RELATIONS = frozenset({"eq", "gt", "gte", "lt", "lte", "interval", "in", "not_in", "text"})
_NUMERIC = frozenset({"eq", "gt", "gte", "lt", "lte"})


class FactValidationError(ValueError):
    """Malformed or unsupported facts bundle; no partial acceptance."""


def _fail(where: str, reason: str) -> None:
    raise FactValidationError(f"{where}: {reason}")


def _object(value: object, required: set[str], optional: set[str], where: str) -> dict:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - optional
    ):
        _fail(where, "invalid fields")
    return value


def _str(value: object, where: str, limit: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(where, "expected nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FactValidationError(f"{where}: invalid Unicode") from exc
    return value


def _id(value: object, where: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _fail(where, "invalid id")
    return value


def _number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _date(value: object, where: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        _fail(where, "invalid date")
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise FactValidationError(f"{where}: invalid date") from exc


def _url(value: object, where: str) -> None:
    value = _str(value, where, 4096)
    try:
        parsed = urlsplit(value)
        # No fragment or embedded credentials; links are external https only.
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        ):
            _fail(where, "invalid https URL")
    except ValueError as exc:
        raise FactValidationError(f"{where}: invalid https URL") from exc


def _source(source: object, where: str) -> None:
    s = _object(source, {"url", "kind", "retrieved"}, {"page", "archive_url"}, where)
    _url(s["url"], where + ".url")
    if s["kind"] not in ("html", "pdf"):
        _fail(where, "invalid source kind")
    _date(s["retrieved"], where + ".retrieved")
    if "page" in s and (type(s["page"]) is not int or s["page"] <= 0 or s["kind"] != "pdf"):
        _fail(where, "invalid PDF page")
    if "archive_url" in s:
        _url(s["archive_url"], where + ".archive_url")


def _conditions(conditions: object, where: str) -> None:
    if not isinstance(conditions, dict) or len(conditions) > 32:
        _fail(where, "expected condition object")
    for key, value in conditions.items():
        if not isinstance(key, str) or _KEY.fullmatch(key) is None:
            _fail(where, "invalid condition key")
        if type(value) is bool:
            continue
        if isinstance(value, str):
            _str(value, where, 256)
            continue
        if isinstance(value, dict):
            predicate = _object(value, {"relation", "value"}, {"unit"}, where)
            _value(predicate, where)
            continue
        _fail(where, "condition value must be a string, boolean, or structured predicate")


def _value(fact: dict, where: str) -> None:
    relation, value = fact["relation"], fact["value"]
    if not isinstance(relation, str) or relation not in _RELATIONS:
        _fail(where, "unsupported relation")
    if relation in _NUMERIC:
        if not _number(value):
            _fail(where, "numeric relation requires finite number")
    elif relation == "interval":
        v = _object(value, {"lower", "upper", "lower_inclusive", "upper_inclusive"}, set(), where)
        if (
            not _number(v["lower"])
            or not _number(v["upper"])
            or type(v["lower_inclusive"]) is not bool
            or type(v["upper_inclusive"]) is not bool
            or v["lower"] > v["upper"]
            or (v["lower"] == v["upper"] and not (v["lower_inclusive"] and v["upper_inclusive"]))
        ):
            _fail(where, "invalid interval")
    elif relation in ("in", "not_in"):
        if not isinstance(value, list) or not value or len(value) > 256:
            _fail(where, "membership requires nonempty list")
        for item in value:
            _str(item, where, 256)
        if len(set(value)) != len(value):
            _fail(where, "duplicate membership value")
    elif relation == "text":
        _str(value, where, 2000)
    if relation in _NUMERIC | {"interval"}:
        _str(fact.get("unit"), where + ".unit", 64)
    elif "unit" in fact:
        _fail(where, "unit is only valid for numeric values")


def _review(value: object, require_verified: bool) -> None:
    review = _object(value, {"status"}, {"reviewed", "method", "evidence"}, "review")
    if review["status"] not in ("verified", "pending", "rejected"):
        _fail("review", "invalid status")
    if review["status"] == "verified":
        if "reviewed" not in review or "method" not in review:
            _fail("review", "verified requires reviewed date and method")
        _date(review["reviewed"], "review.reviewed")
        _str(review["method"], "review.method", 256)
    elif require_verified:
        _fail("review", "unverified record cannot be published")
    for key in ("method", "evidence"):
        if key in review:
            _str(review[key], "review." + key, 256)
    if "reviewed" in review:
        _date(review["reviewed"], "review.reviewed")


def validate_bundle(bundle: object, *, require_verified: bool = False) -> dict:
    """Return an unchanged, completely validated v2 bundle; otherwise raise.

    Duplicate facts, unknown keys, invalid sources and non-finite values are rejected.
    Semantic truth / scope of source text requires independent review, not this check.
    """
    b = _object(
        bundle, {"schema", "journal_id", "journal", "sources", "facts"}, {"coverage"}, "bundle"
    )
    if type(b["schema"]) is not int or b["schema"] != SCHEMA:
        _fail("bundle.schema", "unsupported schema")
    _id(b["journal_id"], "bundle.journal_id")
    j = _object(b["journal"], {"label", "publisher_id"}, {"issn", "eissn"}, "bundle.journal")
    _str(j["label"], "bundle.journal.label", 512)
    _id(j["publisher_id"], "bundle.journal.publisher_id")
    for key in ("issn", "eissn"):
        if key in j:
            _str(j[key], "bundle.journal." + key, 32)
    sources = b["sources"]
    if not isinstance(sources, dict) or len(sources) > 1024:
        _fail("bundle.sources", "expected source map")
    for sid, src in sources.items():
        _id(sid, "source id")
        _source(src, "source")
    facts = b["facts"]
    if not isinstance(facts, list) or len(facts) > 4096:
        _fail("bundle.facts", "expected facts list")
    ids = set()
    for fact in facts:
        f = _object(
            fact,
            {
                "id",
                "subject",
                "relation",
                "value",
                "strength",
                "stage",
                "source",
                "quote",
                "origin",
                "review",
            },
            {
                "unit",
                "conditions",
                "condition_text",
                "anchor",
                "snapshot",
                "mark",
                "derivation",
                "context",
            },
            "fact",
        )
        fid = _id(f["id"], "fact.id")
        if fid in ids:
            _fail("fact.id", "duplicate id")
        ids.add(fid)
        if not isinstance(f["subject"], str) or _KEY.fullmatch(f["subject"]) is None:
            _fail("fact.subject", "invalid subject")
        if (
            not isinstance(f["strength"], str)
            or f["strength"] not in _STRENGTH
            or not isinstance(f["stage"], str)
            or f["stage"] not in _STAGE
        ):
            _fail("fact", "invalid strength or stage")
        if f["origin"] not in ("publisher", "journal"):
            _fail("fact.origin", "invalid origin")
        _review(f["review"], require_verified)
        if not isinstance(f["source"], str) or f["source"] not in sources:
            _fail("fact.source", "unknown source")
        _str(f["quote"], "fact.quote", 2000)
        if "context" in f:
            context = f["context"]
            if not isinstance(context, list) or not context or len(context) > 16:
                _fail("fact.context", "expected nonempty context list")
            for item in context:
                item = _object(item, {"source", "quote"}, set(), "fact.context")
                if not isinstance(item["source"], str) or item["source"] not in sources:
                    _fail("fact.context.source", "unknown source")
                _str(item["quote"], "fact.context.quote", 2000)
        if "anchor" in f and (
            not isinstance(f["anchor"], str)
            or len(f["anchor"]) > 4096
            or re.fullmatch(r"#:~:text=[^\x00-\x20\x7f]+", f["anchor"]) is None
        ):
            _fail("fact.anchor", "invalid text fragment")
        if "anchor" in f:
            _str(f["anchor"], "fact.anchor", 4096)
        if "mark" in f and (
            not isinstance(f["mark"], str) or not f["mark"].strip() or f["mark"] not in f["quote"]
        ):
            _fail("fact.mark", "must be a nonempty substring of quote")
        if "derivation" in f:
            _str(f["derivation"], "fact.derivation", 1000)
        if "snapshot" in f:
            snap = _object(f["snapshot"], {"file", "width", "height"}, set(), "fact.snapshot")
            if (
                not isinstance(snap["file"], str)
                or re.fullmatch(r"snapshots/[0-9a-f]{64}\.webp", snap["file"]) is None
                or any(type(snap[k]) is not int or snap[k] <= 0 for k in ("width", "height"))
            ):
                _fail("fact.snapshot", "invalid snapshot")
        if "conditions" in f:
            _conditions(f["conditions"], "fact.conditions")
        if "condition_text" in f:
            _str(f["condition_text"], "fact.condition_text", 1000)
        _value(f, "fact.value")
    coverage = b.get("coverage", [])
    if not isinstance(coverage, list) or len(coverage) > 4096:
        _fail("bundle.coverage", "expected coverage list")
    coverage_ids = set()
    for item in coverage:
        c = _object(
            item, {"id", "subject", "result", "scope", "sources", "review"}, set(), "coverage"
        )
        cid = _id(c["id"], "coverage.id")
        if cid in coverage_ids:
            _fail("coverage.id", "duplicate id")
        coverage_ids.add(cid)
        if not isinstance(c["subject"], str) or _KEY.fullmatch(c["subject"]) is None:
            _fail("coverage.subject", "invalid subject")
        if c["result"] != "not_stated":
            _fail("coverage.result", "unsupported coverage result")
        _str(c["scope"], "coverage.scope", 1000)
        refs = c["sources"]
        if not isinstance(refs, list) or not refs or len(refs) > 1024:
            _fail("coverage.sources", "expected nonempty source IDs")
        if any(not isinstance(ref, str) or ref not in sources for ref in refs) or len(refs) != len(
            set(refs)
        ):
            _fail("coverage.sources", "unknown or duplicate source")
        _review(c["review"], require_verified)
    if require_verified and not facts and not coverage:
        _fail("bundle", "empty facts require verified scoped coverage")
    return b


def decode_bundle(raw: bytes, *, require_verified: bool = False) -> dict:
    if not isinstance(raw, bytes) or len(raw) > MAX_BUNDLE_BYTES:
        _fail("bundle", "invalid or oversized bytes")
    try:
        # A duplicate key must not silently replace a source or field.
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    _fail("bundle", "duplicate JSON key")
                out[key] = value
            return out

        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _v: _fail("bundle", "non-finite JSON number"),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise FactValidationError("bundle: invalid JSON") from exc
    return validate_bundle(value, require_verified=require_verified)


INDEX_KIND = "tavotto-journal-facts-index"


def validate_index(index: object) -> dict:
    """Validate a decoded v2 index; callers must verify its signature separately."""
    m = _object(
        index,
        {"schema", "kind", "catalog_rev", "issued", "publishers", "journals"},
        set(),
        "index",
    )
    if type(m["schema"]) is not int or m["schema"] != SCHEMA or m["kind"] != INDEX_KIND:
        _fail("index", "unsupported schema or kind")
    if type(m["catalog_rev"]) is not int or not 1 <= m["catalog_rev"] <= 9_999_999_999:
        _fail("index.catalog_rev", "invalid revision")
    _date(m["issued"], "index.issued")
    pubs, jrs = m["publishers"], m["journals"]
    if not isinstance(pubs, dict) or not isinstance(jrs, dict):
        _fail("index", "publishers and journals must be maps")
    for pid, pub in pubs.items():
        _id(pid, "index.publisher_id")
        p = _object(pub, {"label"}, set(), "index.publisher")
        _str(p["label"], "index.publisher.label", 512)
    for jid, journal in jrs.items():
        _id(jid, "index.journal_id")
        j = _object(
            journal, {"label", "publisher", "bundle"}, {"issn", "eissn", "aliases"}, "index.journal"
        )
        _str(j["label"], "index.journal.label", 512)
        if not isinstance(j["publisher"], str) or j["publisher"] not in pubs:
            _fail("index.journal.publisher", "unknown publisher")
        for key in ("issn", "eissn"):
            if key in j:
                _str(j[key], "index.journal." + key, 32)
        if "aliases" in j:
            if not isinstance(j["aliases"], list) or len(j["aliases"]) > 256:
                _fail("index.journal.aliases", "invalid aliases")
            for alias in j["aliases"]:
                _str(alias, "index.journal.aliases", 512)
        b = _object(j["bundle"], {"sha256", "bytes"}, set(), "index.journal.bundle")
        if not isinstance(b["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", b["sha256"]) is None:
            _fail("index.journal.bundle.sha256", "invalid digest")
        if type(b["bytes"]) is not int or not 0 < b["bytes"] <= MAX_BUNDLE_BYTES:
            _fail("index.journal.bundle.bytes", "invalid size")
    return m


@dataclass(frozen=True, slots=True)
class ValidatedIndex:
    """A validated immutable snapshot, reusable across many bundle checks.

    Signature verification remains the caller's responsibility. All metadata is
    copied to immutable tuples so subsequent caller mutation cannot bypass the
    initial validation or change the hash against which bundles are checked.
    """

    _entries: Mapping[str, tuple[str, str, str | None, str | None, int, str]]

    def __init__(self, index: object) -> None:
        m = validate_index(index)
        entries = MappingProxyType(
            {
                jid: (
                    entry["label"],
                    entry["publisher"],
                    entry.get("issn"),
                    entry.get("eissn"),
                    entry["bundle"]["bytes"],
                    entry["bundle"]["sha256"],
                )
                for jid, entry in m["journals"].items()
            }
        )
        object.__setattr__(self, "_entries", entries)

    def validate_bundle(self, journal_id: str, raw: bytes) -> dict:
        """Validate one bundle in O(bundle size), using the previously checked index."""
        import hashlib

        _id(journal_id, "journal_id")
        entry = self._entries.get(journal_id)
        if entry is None:
            _fail("journal_id", "unknown journal")
        if not isinstance(raw, bytes):
            _fail("bundle", "expected bytes")
        label, publisher, issn, eissn, size, digest = entry
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            _fail("bundle", "digest or size mismatch")
        b = decode_bundle(raw, require_verified=True)
        if (
            b["journal_id"] != journal_id
            or b["journal"]["label"] != label
            or b["journal"]["publisher_id"] != publisher
            or b["journal"].get("issn") != issn
            or b["journal"].get("eissn") != eissn
        ):
            _fail("bundle", "identity does not match index")
        return b


def validate_indexed_bundle(index: object, journal_id: str, raw: bytes) -> dict:
    """Check one bundle; use ValidatedIndex for many. Verify index signature separately."""
    return ValidatedIndex(index).validate_bundle(journal_id, raw)
