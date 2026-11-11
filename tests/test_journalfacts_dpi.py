"""Tests for isolated source-fact DPI evaluator, not v1 preflight."""

import copy
import math

import pytest

from tavotto.engine.journalfacts_contract import FactValidationError
from tavotto.engine.journalfacts_dpi import evaluate_dpi_facts


@pytest.fixture
def fact():
    return {
        "id": "line",
        "subject": "raster_dpi",
        "relation": "gte",
        "value": 800,
        "unit": "dpi",
        "strength": "required",
        "stage": "production",
        "source": "guide",
        "quote": "Illustrative source quote.",
        "origin": "journal",
        "review": {"status": "verified", "reviewed": "2026-11-12", "method": "test fixture"},
        "conditions": {"figure_kind": "line_art", "image_origin": "scanned", "size_basis": "final"},
    }


@pytest.fixture
def context():
    return {
        "figure_kind": "line_art",
        "image_origin": "scanned",
        "size_basis": "final",
        "stage": "production",
    }


def bundle(*facts):
    return {
        "schema": 2,
        "journal_id": "fixture",
        "journal": {"label": "Fixture", "publisher_id": "publisher"},
        "sources": {
            "guide": {
                "url": "https://example.org/guide",
                "kind": "html",
                "retrieved": "2026-11-12",
            },
            "publisher": {
                "url": "https://example.org/publisher",
                "kind": "html",
                "retrieved": "2026-11-12",
            },
        },
        "facts": list(facts),
    }


def test_match_and_threshold_boundaries(fact, context):
    original = bundle(fact)
    snapshot = copy.deepcopy(original)
    for dpi, status in [
        (799, "violated"),
        (800, "satisfied_recorded_requirements"),
        (801, "satisfied_recorded_requirements"),
    ]:
        result = evaluate_dpi_facts(original, context, dpi)
        assert result["status"] == status
        assert result["catalog_completeness"] == "unknown"
        assert result["facts"][0]["id"] == "line"
    assert original == snapshot


@pytest.mark.parametrize("field", ["figure_kind", "image_origin", "size_basis", "stage"])
def test_missing_context_never_passes(fact, context, field):
    context.pop(field)
    result = evaluate_dpi_facts(bundle(fact), context, 1200)
    assert result["status"] == "unknown"
    assert result["facts"][0]["reason"] == "unknown_applicability"


@pytest.mark.parametrize(
    "field,other",
    [("figure_kind", "halftone"), ("image_origin", "digital"), ("stage", "submission")],
)
def test_known_mismatch_is_not_applicable(fact, context, field, other):
    context[field] = other
    result = evaluate_dpi_facts(bundle(fact), context, 100)
    assert result["status"] == "unknown"
    assert result["facts"][0]["status"] == "not_applicable"


def test_unknown_conditions_and_stage_are_not_unconditional(fact, context):
    for mutation in (
        {"stage": "unknown"},
        {"condition_text": "only in an unstated case"},
        {"conditions": {"paper_type": "original"}},
        {"conditions": {"figure_kind": {"relation": "in", "value": ["line_art"]}}},
    ):
        result = evaluate_dpi_facts(bundle({**fact, **mutation}), context, 1200)
        assert result["status"] == "unknown"
        assert result["facts"][0]["reason"] == "unknown_applicability"
    context["stage"] = []
    assert evaluate_dpi_facts(bundle(fact), context, 1200)["status"] == "unknown"


def test_all_stages_is_not_unknown_stage(fact, context):
    context.pop("stage")
    assert (
        evaluate_dpi_facts(bundle({**fact, "stage": "any"}), context, 800)["status"]
        == "satisfied_recorded_requirements"
    )
    assert (
        evaluate_dpi_facts(bundle({**fact, "stage": "unknown"}), context, 800)["status"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "strength", ["typical", "recommended", "preferred", "permitted", "example", "unspecified"]
)
def test_nonmandatory_statement_is_advice_not_minimum(fact, context, strength):
    result = evaluate_dpi_facts(bundle({**fact, "strength": strength, "value": 300}), context, 100)
    assert result["status"] == "unknown"
    assert result["facts"][0]["status"] == "advice"


def test_pending_and_rejected_do_not_pass_even_with_verified_fact(fact, context):
    for state in ("pending", "rejected"):
        other = {**fact, "id": "other", "review": {"status": state}}
        result = evaluate_dpi_facts(bundle(fact, other), context, 1000)
        assert result["status"] == "unknown"
        assert result["facts"][1]["reason"] == "unverified_review"


def test_unknown_relevant_fact_prevents_confident_violation(fact, context):
    unresolved = {**fact, "id": "other", "conditions": {"unrecognized": "x"}}
    assert evaluate_dpi_facts(bundle(fact, unresolved), context, 100)["status"] == "unknown"


def test_conflicting_publishers_are_not_auto_selected(fact, context):
    other = {**fact, "id": "pub", "source": "publisher", "origin": "publisher", "value": 300}
    result = evaluate_dpi_facts(bundle(fact, other), context, 500)
    assert result["status"] == "unknown"
    assert [item["status"] for item in result["facts"]] == ["conflict", "conflict"]
    assert {item["source"] for item in result["facts"]} == {"guide", "publisher"}
    identical = {**other, "value": 800}
    assert evaluate_dpi_facts(bundle(fact, identical), context, 799)["status"] == "violated"


def test_different_kind_never_conflicts(fact, context):
    other = {**fact, "id": "half", "value": 300, "conditions": {"figure_kind": "halftone"}}
    result = evaluate_dpi_facts(bundle(fact, other), context, 799)
    assert result["status"] == "violated"
    assert result["facts"][1]["status"] == "not_applicable"


@pytest.mark.parametrize("dpi", [None, 0, -1, math.nan, math.inf, True, 10**1000])
def test_bad_measurement_does_not_pass(fact, context, dpi):
    assert evaluate_dpi_facts(bundle(fact), context, dpi)["status"] == "unknown"


def test_empty_no_match_unsupported_or_broken_bundle(fact, context):
    assert evaluate_dpi_facts(bundle(), context, 1000)["status"] == "unknown"
    assert (
        evaluate_dpi_facts(bundle({**fact, "subject": "other"}), context, 1000)["status"]
        == "unknown"
    )
    assert (
        evaluate_dpi_facts(bundle({**fact, "relation": "gt"}), context, 1000)["status"] == "unknown"
    )
    assert evaluate_dpi_facts(bundle({**fact, "value": 0}), context, 1000)["status"] == "unknown"
    with pytest.raises(FactValidationError):
        evaluate_dpi_facts(bundle({**fact, "source": "missing"}), context, 1000)
