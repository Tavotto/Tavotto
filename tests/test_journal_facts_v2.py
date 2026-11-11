"""v2 fact API behaviour
fixture values are synthetic, never real journals."""

from __future__ import annotations

import copy
import json
import sqlite3
from io import BytesIO

import pytest

from services.journal_catalog.app import make_application
from services.journal_catalog.core import Config, Service
from services.journal_catalog.facts import FactValidationError, decode_bundle, validate_bundle
from tavotto.engine import journalremote, journals


def bundle():
    source = {"url": "https://example.invalid/authors", "kind": "html", "retrieved": "2026-11-11"}

    def item(fid, subject, relation, value, quote, **kw):
        return {
            "id": fid,
            "subject": subject,
            "relation": relation,
            "value": value,
            "strength": "required",
            "stage": "production",
            "source": "guide",
            "quote": quote,
            "origin": "journal",
            "review": {"status": "verified", "reviewed": "2026-11-11", "method": "fixture"},
            **kw,
        }

    return {
        "schema": 2,
        "journal_id": "fx-journal",
        "journal": {"label": "Fixture Journal", "publisher_id": "fx-pub"},
        "sources": {"guide": source},
        "facts": [
            item(
                "line",
                "raster_resolution",
                "gte",
                1200,
                "Line art at least 1200 dpi.",
                unit="dpi",
                conditions={"figure_kind": "line", "fine_line": True},
            ),
            item(
                "combination",
                "raster_resolution",
                "gte",
                800,
                "Combination 800 dpi.",
                unit="dpi",
                conditions={"figure_kind": "combination"},
            ),
            item(
                "gray",
                "raster_resolution",
                "gte",
                300,
                "Grayscale 300 dpi.",
                unit="dpi",
                conditions={"figure_kind": "halftone", "color": "grayscale"},
            ),
            item(
                "color",
                "raster_resolution",
                "gte",
                400,
                "Color 400 dpi.",
                unit="dpi",
                conditions={"figure_kind": "halftone", "color": "color"},
            ),
            item(
                "range",
                "raster_resolution",
                "interval",
                {"lower": 300, "upper": 600, "lower_inclusive": True, "upper_inclusive": False},
                "At least 300 and below 600 dpi.",
                unit="dpi",
            ),
            item("forbidden", "file_format", "not_in", ["GIF", "JPEG"], "No GIF or JPEG."),
            {
                **item(
                    "example",
                    "font_family",
                    "in",
                    ["Arial", "Helvetica"],
                    "Use a sans serif such as Arial or Helvetica.",
                ),
                "strength": "example",
            },
            item("exact_font_size", "font_size", "eq", 8, "Text must be exactly 8 pt.", unit="pt"),
            item(
                "conditional_jpeg",
                "file_format",
                "in",
                ["JPEG"],
                "JPEG is permitted in this condition.",
                conditions={"image_tones": "limited"},
            ),
            item(
                "conditional_text",
                "color_mode",
                "text",
                "Use CMYK if feasible.",
                "Use CMYK if feasible.",
                condition_text="If feasible",
            ),
        ],
    }


def test_fact_bundle_preserves_figure_kinds_and_other_semantics():
    obj = bundle()
    raw = json.dumps(obj).encode()
    result = decode_bundle(raw)
    assert result == obj
    dpi = [f for f in result["facts"] if f["subject"] == "raster_resolution"]
    assert len(dpi) == 5
    assert dpi[0]["conditions"] == {"figure_kind": "line", "fine_line": True}
    assert dpi[2]["conditions"] == {"figure_kind": "halftone", "color": "grayscale"}
    assert dpi[3]["conditions"] == {"figure_kind": "halftone", "color": "color"}
    assert dpi[4]["value"]["upper_inclusive"] is False
    assert next(f for f in result["facts"] if f["id"] == "forbidden")["relation"] == "not_in"
    assert next(f for f in result["facts"] if f["id"] == "example")["strength"] == "example"
    assert (
        next(f for f in result["facts"] if f["id"] == "conditional_text")["condition_text"]
        == "If feasible"
    )
    assert next(f for f in result["facts"] if f["id"] == "exact_font_size")["relation"] == "eq"
    assert next(f for f in result["facts"] if f["id"] == "conditional_jpeg")["conditions"] == {
        "image_tones": "limited"
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b["facts"][0].update(id=b["facts"][1]["id"]),
        lambda b: b["facts"][0].update(source="unknown"),
        lambda b: b["facts"][0].update(quote=" "),
        lambda b: b["facts"][0].update(conditions={"figure_kind": ["line", "photo"]}),
        lambda b: b["facts"][0].update(strength=[]),
        lambda b: b["facts"][0].update(relation=[]),
        lambda b: b["facts"][0].update(value=float("nan")),
        lambda b: b["facts"][0].update(extra="silently discard"),
        lambda b: b["facts"][4]["value"].update(upper=100),
        lambda b: b["facts"][5].update(value=["GIF", "GIF"]),
        lambda b: b["sources"]["guide"].update(url="https://user:pass@example.invalid/"),
        lambda b: b["sources"]["guide"].update(retrieved="2026-02-30"),
    ],
)
def test_malformed_bundle_is_rejected_whole(mutate):
    obj = bundle()
    mutate(obj)
    with pytest.raises(FactValidationError):
        validate_bundle(obj)


def test_duplicate_json_keys_and_nan_are_rejected():
    for raw in (b'{"schema":2,"schema":2}', b'{"schema":NaN}'):
        with pytest.raises(FactValidationError):
            decode_bundle(raw)


def request(app, path, method="GET", query="", token=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "REMOTE_ADDR": "198.51.100.4",
        "wsgi.input": BytesIO(),
        "CONTENT_LENGTH": "0",
    }
    if token:
        env["HTTP_X_TAVOTTO_DEVICE"] = token
    result = {}

    def start(status, headers):
        result.update(status=int(status.split()[0]), headers=dict(headers))

    result["body"] = b"".join(app(env, start))
    return result


def test_v2_api_is_isolated_and_uses_existing_token_and_quota(tmp_path):
    data = tmp_path / "data"
    (data / "r1" / "journals").mkdir(parents=True)
    (data / "v2" / "r2" / "journals").mkdir(parents=True)
    (data / "index.signed.json").write_bytes(b"v1-index")
    (data / "v2" / "index.signed.json").write_bytes(b"v2-index")
    v1 = b'{"journal_id":"fx-journal"}'
    (data / "r1" / "journals" / "fx-journal.json").write_bytes(v1)
    v2 = json.dumps(bundle()).encode()
    (data / "v2" / "r2" / "journals" / "fx-journal.json").write_bytes(v2)
    service = Service(
        Config(data, tmp_path / "db.sqlite", b"fixture-ip-secret"), clock=lambda: 1800000000
    )
    app = make_application(service)
    assert request(app, "/v1/journal-catalog/index.signed.json")["body"] == b"v1-index"
    assert request(app, "/v2/journal-catalog/index.signed.json")["body"] == b"v2-index"
    token = json.loads(request(app, "/v1/journal-catalog/device", "POST")["body"])["token"]
    assert request(app, "/v2/journal-catalog/journals/fx-journal", query="rev=2")["status"] == 401
    r = request(app, "/v2/journal-catalog/journals/fx-journal", query="rev=2", token=token)
    assert (
        r["status"] == 200
        and r["body"] == v2
        and r["headers"]["Cache-Control"] == "private, no-store"
    )
    r = request(app, "/v1/journal-catalog/journals/fx-journal", query="rev=1", token=token)
    assert r["status"] == 200 and r["body"] == v1
    assert (
        request(app, "/v1/journal-catalog/journals/fx-journal", query="rev=2", token=token)[
            "status"
        ]
        == 404
    )
    assert (
        request(app, "/v2/journal-catalog/journals/fx-journal", query="rev=1", token=token)[
            "status"
        ]
        == 404
    )
    with sqlite3.connect(service.store.db_path) as con:
        assert con.execute("select count(*) from reads").fetchone()[0] == 1


def test_v2_rejects_bad_or_misidentified_bundle_before_quota(tmp_path):
    data = tmp_path / "data"
    journal_dir = data / "v2" / "r2" / "journals"
    journal_dir.mkdir(parents=True)
    service = Service(
        Config(data, tmp_path / "db.sqlite", b"fixture-ip-secret"), clock=lambda: 1800000000
    )
    app = make_application(service)
    token = json.loads(request(app, "/v2/journal-catalog/device", "POST")["body"])["token"]
    for invalid in (b'{"schema":NaN}', json.dumps({**bundle(), "journal_id": "wrong"}).encode()):
        (journal_dir / "fx-journal.json").write_bytes(invalid)
        result = request(app, "/v2/journal-catalog/journals/fx-journal", query="rev=2", token=token)
        assert result["status"] == 503 and json.loads(result["body"])["code"] == "catalog_invalid"
    with sqlite3.connect(service.store.db_path) as con:
        assert con.execute("select count(*) from reads").fetchone()[0] == 0


def test_unverified_facts_cannot_be_served(tmp_path):
    data = tmp_path / "data"
    journal_dir = data / "v2" / "r2" / "journals"
    journal_dir.mkdir(parents=True)
    obj = bundle()
    obj["facts"][0]["review"] = {"status": "pending"}
    validate_bundle(obj)
    (journal_dir / "fx-journal.json").write_text(json.dumps(obj))
    service = Service(
        Config(data, tmp_path / "db.sqlite", b"fixture-ip-secret"), clock=lambda: 1800000000
    )
    app = make_application(service)
    token = json.loads(request(app, "/v2/journal-catalog/device", "POST")["body"])["token"]
    response = request(app, "/v2/journal-catalog/journals/fx-journal", query="rev=2", token=token)
    assert response["status"] == 503 and json.loads(response["body"])["code"] == "catalog_invalid"
    with sqlite3.connect(service.store.db_path) as con:
        assert con.execute("select count(*) from reads").fetchone()[0] == 0


def test_old_catalog_and_index_reject_v2():
    with pytest.raises(journals.JournalCatalogError) as err:
        journals.validate_catalog(bundle())
    assert err.value.code == "journal_catalog_schema"
    index = {
        "schema": 2,
        "kind": "tavotto-journal-facts-index",
        "catalog_rev": 2,
        "issued": "2026-11-11",
        "publishers": {},
        "journals": {},
    }
    with pytest.raises(journalremote.RemoteCatalogError) as err:
        journalremote._check_index(index)
    assert err.value.code == "index_invalid"


def test_v2_indexed_bundle_checks_digest_identity_and_review():
    import hashlib

    from services.journal_catalog.facts import validate_index, validate_indexed_bundle

    obj = bundle()
    raw = json.dumps(obj, ensure_ascii=False).encode()
    index = {
        "schema": 2,
        "kind": "tavotto-journal-facts-index",
        "catalog_rev": 42,
        "issued": "2026-11-11",
        "publishers": {"fx-pub": {"label": "Fixture Publisher"}},
        "journals": {
            "fx-journal": {
                "label": "Fixture Journal",
                "publisher": "fx-pub",
                "bundle": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            }
        },
    }
    assert validate_index(index) == index
    assert validate_indexed_bundle(index, "fx-journal", raw) == obj
    with pytest.raises(FactValidationError):
        validate_indexed_bundle(index, "fx-journal", raw + b" ")
    altered = copy.deepcopy(index)
    altered["journals"]["fx-journal"]["label"] = "Wrong title"
    with pytest.raises(FactValidationError):
        validate_indexed_bundle(altered, "fx-journal", raw)
    altered = copy.deepcopy(obj)
    altered["facts"][0]["review"] = {"status": "pending"}
    raw = json.dumps(altered, ensure_ascii=False).encode()
    index["journals"]["fx-journal"]["bundle"] = {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    with pytest.raises(FactValidationError):
        validate_indexed_bundle(index, "fx-journal", raw)


def test_validated_index_reuses_immutable_snapshot_for_many_bundles():
    import hashlib
    from dataclasses import FrozenInstanceError

    from services.journal_catalog.facts import ValidatedIndex

    obj = bundle()
    raw = json.dumps(obj, ensure_ascii=False).encode()
    meta = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    index = {
        "schema": 2,
        "kind": "tavotto-journal-facts-index",
        "catalog_rev": 42,
        "issued": "2026-11-11",
        "publishers": {"fx-pub": {"label": "Fixture Publisher"}},
        "journals": {
            f"fx-{i}": {"label": "Fixture", "publisher": "fx-pub", "bundle": dict(meta)}
            for i in range(9000)
        },
    }
    index["journals"]["fx-journal"] = {
        "label": "Fixture Journal",
        "publisher": "fx-pub",
        "bundle": dict(meta),
    }
    checked = ValidatedIndex(index)
    index["journals"]["fx-journal"]["bundle"]["sha256"] = "a" * 64
    assert checked.validate_bundle("fx-journal", raw) == obj
    with pytest.raises(FrozenInstanceError):
        checked._entries = {}  # type: ignore[misc]
    with pytest.raises(FactValidationError):
        checked.validate_bundle("fx-journal", raw + b" ")
    index["schema"] = 1
    with pytest.raises(FactValidationError):
        ValidatedIndex(index)


def test_indexed_bundle_preserves_and_matches_eissn():
    import hashlib

    from services.journal_catalog.facts import ValidatedIndex

    obj = bundle()
    obj["journal"]["eissn"] = "1234-5678"
    raw = json.dumps(obj).encode()
    index = {
        "schema": 2,
        "kind": "tavotto-journal-facts-index",
        "catalog_rev": 42,
        "issued": "2026-11-11",
        "publishers": {"fx-pub": {"label": "Fixture Publisher"}},
        "journals": {
            "fx-journal": {
                "label": "Fixture Journal",
                "publisher": "fx-pub",
                "eissn": "1234-5678",
                "bundle": {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
            }
        },
    }
    assert (
        ValidatedIndex(index).validate_bundle("fx-journal", raw)["journal"]["eissn"] == "1234-5678"
    )
    index["journals"]["fx-journal"]["eissn"] = "8765-4321"
    with pytest.raises(FactValidationError):
        ValidatedIndex(index).validate_bundle("fx-journal", raw)


def test_fact_provenance_fields_are_preserved_and_strict():
    obj = bundle()
    fact = obj["facts"][0]
    fact.update(
        anchor="#:~:text=1200%20dpi",
        mark="1200 dpi",
        derivation="1200 dpi as stated",
        snapshot={"file": "snapshots/" + "a" * 64 + ".webp", "width": 600, "height": 300},
    )
    assert decode_bundle(json.dumps(obj).encode()) == obj
    for key, bad in (
        ("anchor", "javascript:alert(1)"),
        ("mark", "text absent from source"),
        ("derivation", " "),
        ("snapshot", {"file": "snapshots/../../bad.webp", "width": 1, "height": 1}),
        ("snapshot", {"file": "snapshots/" + "a" * 64 + ".webp", "width": True, "height": 1}),
    ):
        altered = copy.deepcopy(obj)
        altered["facts"][0][key] = bad
        with pytest.raises(FactValidationError):
            validate_bundle(altered)


def test_v2_snapshot_isolated_and_hash_checked(tmp_path):
    import hashlib

    data = tmp_path / "data"
    snapshot_dir = data / "v2" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    body = b"fixture snapshot bytes"
    name = hashlib.sha256(body).hexdigest() + ".webp"
    (snapshot_dir / name).write_bytes(body)
    service = Service(
        Config(data, tmp_path / "db.sqlite", b"fixture-ip-secret"), clock=lambda: 1800000000
    )
    app = make_application(service)
    token = json.loads(request(app, "/v2/journal-catalog/device", "POST")["body"])["token"]
    assert request(app, "/v2/journal-catalog/snapshots/" + name)["status"] == 401
    response = request(app, "/v2/journal-catalog/snapshots/" + name, token=token)
    assert response["status"] == 200 and response["body"] == body
    assert request(app, "/v1/journal-catalog/snapshots/" + name, token=token)["status"] == 404
    (snapshot_dir / name).write_bytes(b"corrupted")
    response = request(app, "/v2/journal-catalog/snapshots/" + name, token=token)
    assert response["status"] == 503 and json.loads(response["body"])["code"] == "catalog_invalid"
    assert response["headers"]["Cache-Control"] == "no-store"


def test_empty_bundle_needs_verified_scoped_coverage_to_publish():
    obj = bundle()
    obj["facts"] = []
    validate_bundle(obj)
    with pytest.raises(FactValidationError):
        validate_bundle(obj, require_verified=True)
    obj["coverage"] = [
        {
            "id": "figure_section_dpi",
            "subject": "raster_resolution",
            "result": "not_stated",
            "scope": "The Figures section of the captured author guide",
            "sources": ["guide"],
            "review": {"status": "pending"},
        }
    ]
    validate_bundle(obj)
    with pytest.raises(FactValidationError):
        validate_bundle(obj, require_verified=True)
    obj["coverage"][0]["review"] = {
        "status": "verified",
        "reviewed": "2026-11-11",
        "method": "fixture",
    }
    assert validate_bundle(obj, require_verified=True) == obj
    for mutation in (
        lambda b: b["coverage"][0].update(scope=" "),
        lambda b: b["coverage"][0].update(sources=[]),
        lambda b: b["coverage"][0].update(sources=["unknown"]),
        lambda b: b["coverage"][0].update(result="not_stated_anywhere"),
        lambda b: b["coverage"].append(copy.deepcopy(b["coverage"][0])),
    ):
        altered = copy.deepcopy(obj)
        mutation(altered)
        with pytest.raises(FactValidationError):
            validate_bundle(altered, require_verified=True)


def test_noncontiguous_context_keeps_its_own_source_and_text():
    obj = bundle()
    obj["sources"]["intro"] = {
        "url": "https://example.invalid/introduction",
        "kind": "html",
        "retrieved": "2026-11-11",
    }
    obj["facts"][0]["context"] = [
        {"source": "intro", "quote": "Authors are encouraged to follow these guidelines."}
    ]
    obj["facts"][0]["conditions"]["purpose"] = "online_color"
    obj["facts"][0]["strength"] = "recommended"
    assert decode_bundle(json.dumps(obj).encode()) == obj
    for mutation in (
        lambda b: b["facts"][0]["context"][0].update(source="absent"),
        lambda b: b["facts"][0]["context"][0].update(quote=" "),
        lambda b: b["facts"][0].update(context=[]),
        lambda b: b["facts"][0]["context"][0].update(unexpected="value"),
    ):
        altered = copy.deepcopy(obj)
        mutation(altered)
        with pytest.raises(FactValidationError):
            validate_bundle(altered)


def test_revision_and_acceptance_remain_distinct_with_latex_condition():
    obj = bundle()
    first = copy.deepcopy(obj["facts"][5])
    first.update(
        id="revision_files",
        subject="file_organization",
        relation="text",
        value="Separate figure files in revision",
        stage="revision",
        quote="In revision figures are separate files.",
    )
    second = copy.deepcopy(first)
    second.update(
        id="accepted_latex",
        subject="file_format",
        relation="in",
        value=["EPS", "TIFF"],
        stage="accepted",
        conditions={"submission_system": "latex"},
        quote="For accepted LaTeX submissions, use EPS or TIFF.",
    )
    obj["facts"].extend([first, second])
    result = decode_bundle(json.dumps(obj).encode(), require_verified=True)
    assert result["facts"][-2]["stage"] == "revision"
    assert result["facts"][-1]["stage"] == "accepted"
    assert result["facts"][-1]["conditions"] == {"submission_system": "latex"}
    assert result["facts"][-2]["stage"] != result["facts"][-1]["stage"]


def test_structured_condition_preserves_strict_bounds_and_units():
    obj = bundle()
    obj["facts"][0]["conditions"] = {
        "figure_kind": "line",
        "width": {
            "relation": "interval",
            "value": {"lower": 80, "upper": 180, "lower_inclusive": False, "upper_inclusive": True},
            "unit": "mm",
        },
        "fine_line": True,
    }
    assert decode_bundle(json.dumps(obj).encode()) == obj
    condition = obj["facts"][0]["conditions"]["width"]
    for mutation in (
        lambda c: c.pop("unit"),
        lambda c: c.update(unit=" "),
        lambda c: c.update(unit=None),
        lambda c: c.update(unit=123),
        lambda c: c["value"].update(lower=float("inf")),
        lambda c: c["value"].update(lower_inclusive="false"),
        lambda c: c["value"].update(lower=180, upper=180, lower_inclusive=False),
        lambda c: c["value"].update(lower=200),
        lambda c: c.update(relation="unknown-comparison"),
        lambda c: c.update(unexpected="silently ignored"),
    ):
        altered = copy.deepcopy(obj)
        mutation(altered["facts"][0]["conditions"]["width"])
        with pytest.raises(FactValidationError):
            validate_bundle(altered)
    assert condition["value"]["lower_inclusive"] is False
