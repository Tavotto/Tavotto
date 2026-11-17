"""Unresolved source scope is representable in drafts but cannot be published."""

import copy
import json

import pytest

from tavotto.engine.journalfacts_contract import (
    FactValidationError,
    decode_bundle,
    validate_bundle,
    validate_indexed_bundle,
)
from tavotto.engine.journalfacts_dpi import evaluate_dpi_facts
from test_journalfacts_remote import fact_bundle, index_for


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_unknown_origin_round_trips_in_unverified_drafts(status):
    draft = fact_bundle()
    draft["facts"][0]["origin"] = "unknown"
    draft["facts"][0]["review"] = {"status": status}
    before = copy.deepcopy(draft)
    assert validate_bundle(draft) == before
    assert decode_bundle(json.dumps(draft).encode()) == before
    assert draft == before
    with pytest.raises(FactValidationError):
        decode_bundle(json.dumps(draft).encode(), require_verified=True)


def test_unknown_origin_cannot_be_promoted_by_review_label_alone():
    draft = fact_bundle()
    draft["facts"][0]["origin"] = "unknown"
    for required in (False, True):
        with pytest.raises(FactValidationError, match="unknown origin"):
            validate_bundle(draft, require_verified=required)


def test_mixed_bundle_does_not_partially_admit_unresolved_origin():
    draft = fact_bundle()
    unresolved = copy.deepcopy(draft["facts"][0])
    unresolved.update(id="unresolved", origin="unknown", review={"status": "pending"})
    draft["facts"].append(unresolved)
    raw = json.dumps(draft).encode()
    assert len(validate_bundle(draft)["facts"]) == 2
    with pytest.raises(FactValidationError):
        validate_indexed_bundle(index_for(raw), "fx-journal", raw)


@pytest.mark.parametrize("origin", [None, "other", "", [], {}])
def test_unknown_is_explicit_and_other_origins_are_rejected(origin):
    draft = fact_bundle()
    draft["facts"][0]["origin"] = origin
    draft["facts"][0]["review"] = {"status": "pending"}
    with pytest.raises(FactValidationError, match="origin"):
        validate_bundle(draft)


def test_python_string_subclass_cannot_impersonate_verified_origin():
    class Impostor(str):
        def __eq__(self, other):
            return other == "publisher"

    draft = fact_bundle()
    draft["facts"][0]["origin"] = Impostor("unknown")
    with pytest.raises(FactValidationError, match="origin"):
        validate_bundle(draft, require_verified=True)


def test_unknown_origin_draft_does_not_become_dpi_advice_or_compliance():
    draft = fact_bundle()
    draft["facts"][0]["origin"] = "unknown"
    draft["facts"][0]["review"] = {"status": "pending"}
    draft["facts"][0]["subject"] = "raster_dpi"
    result = evaluate_dpi_facts(draft, {}, 1200)
    assert result["status"] == "unknown"
    assert result["facts"][0]["reason"] == "unverified_review"


@pytest.mark.parametrize("origin", ["publisher", "journal"])
def test_existing_v2_verified_origins_still_validate(origin):
    published = fact_bundle()
    published["facts"][0]["origin"] = origin
    assert validate_bundle(published, require_verified=True) == published
