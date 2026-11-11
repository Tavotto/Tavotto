"""Experimental source-fact DPI evaluation; not connected to preflight.

This module does not authenticate a bundle or prove source/reviewer integrity.
A caller must first verify the signed index, revision, bundle hash and review
provenance. The output concerns only supplied records, never journal-wide
completeness. Unsupported conditions and conflicting facts remain unresolved.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .journalfacts_contract import validate_bundle

# This intentionally small local vocabulary is not a v2 schema expansion.
_VALUES = {
    "figure_kind": frozenset({"line_art", "halftone", "combination"}),
    "image_origin": frozenset({"scanned", "digital"}),
    "size_basis": frozenset({"final"}),
}
_STAGES = frozenset({"submission", "revision", "accepted", "production"})
_SUBJECT = "raster_dpi"


def _applicability(fact: dict, context: Mapping[str, object]) -> str:
    """Return matches / not_applicable / unknown, with conjunction semantics."""
    unknown = bool(fact.get("condition_text"))
    mismatch = False
    stage = fact["stage"]
    if stage == "unknown":
        unknown = True
    elif stage != "any":
        observed = context.get("stage")
        if not isinstance(observed, str) or observed not in _STAGES:
            unknown = True
        elif observed != stage:
            mismatch = True
    for key, expected in fact.get("conditions", {}).items():
        values = _VALUES.get(key)
        observed = context.get(key)
        if values is None or not isinstance(expected, str) or expected not in values:
            unknown = True
        elif not isinstance(observed, str) or observed not in values:
            unknown = True
        elif observed != expected:
            mismatch = True
    if unknown:
        return "unknown"
    return "not_applicable" if mismatch else "matches"


def evaluate_dpi_facts(bundle: object, context: Mapping[str, object], dpi: object) -> dict:
    """Assess supplied DPI facts, conservatively and without modifying inputs.

    ``bundle`` must already be authenticated by the caller. Validation here
    checks structure only, never signature or editorial truth. A `verified`
    review label is necessary but not sufficient evidence of independent review.
    Context is supplied for one specific measured raster object or export.
    Verdicts apply only to these records, not complete journal compliance.
    Empty, pending, unsupported, contradictory or unmeasured DPI facts do not pass.
    Every fact not numerically evaluated is listed in ``unevaluated_facts``.
    Other subjects with unit dpi force an unknown DPI verdict; other topics
    remain outside the DPI verdict and are explicitly listed as unevaluated.
    """
    data = validate_bundle(bundle)
    if not isinstance(context, Mapping):
        raise TypeError("context must be a mapping")
    try:
        valid_dpi = type(dpi) in (int, float) and math.isfinite(dpi) and dpi > 0
    except OverflowError:
        valid_dpi = False
    results = []
    skipped = []
    required = []
    for fact in data["facts"]:
        if fact["subject"] != _SUBJECT:
            omitted = {
                "id": fact["id"],
                "subject": fact["subject"],
                "reason": "unsupported_subject",
            }
            skipped.append(omitted)
            if fact.get("unit") == "dpi":
                results.append(
                    {
                        **omitted,
                        "source": fact["source"],
                        "origin": fact["origin"],
                        "status": "unknown",
                    }
                )
            continue
        result = {
            "id": fact["id"],
            "subject": fact["subject"],
            "source": fact["source"],
            "origin": fact["origin"],
        }
        if fact["review"]["status"] != "verified":
            result["status"] = "unknown"
            result["reason"] = "unverified_review"
        else:
            applicability = _applicability(fact, context)
            if fact["relation"] != "gte" or fact.get("unit") != "dpi" or fact["value"] <= 0:
                result["status"] = "unknown"
                result["reason"] = "unsupported_relation_or_unit"
            elif not valid_dpi:
                result["status"] = "unknown"
                result["reason"] = "missing_measurement"
            elif applicability != "matches":
                result["status"] = applicability
                if applicability == "unknown":
                    result["reason"] = "unknown_applicability"
            elif fact["strength"] != "required":
                # These statements remain visible without inventing a minimum.
                result["status"] = "advice"
            else:
                result["status"] = "satisfied" if dpi >= fact["value"] else "violated"
                required.append((fact["value"], result))
        results.append(result)
    # Distinct simultaneously applicable requirements may reflect conflicting
    # sources or different scopes; source precedence is not inferred here.
    if len({value for value, _ in required}) > 1:
        for _, result in required:
            result["status"] = "conflict"
            result["reason"] = "different_applicable_thresholds"
    for item in results:
        if item["subject"] == _SUBJECT and item["status"] not in {"satisfied", "violated"}:
            skipped.append(
                {
                    "id": item["id"],
                    "subject": item["subject"],
                    "reason": item.get("reason", item["status"]),
                }
            )
    statuses = {item["status"] for item in results}
    if not results or statuses & {"unknown", "conflict"}:
        overall = "unknown"
    elif "violated" in statuses:
        overall = "violated"
    elif "satisfied" in statuses:
        overall = "satisfied_evaluated_raster_dpi_facts"
    else:
        # Advice, not-applicable or a combination never proves compliance.
        overall = "unknown"
    return {
        "status": overall,
        "facts": results,
        "unevaluated_facts": skipped,
        "catalog_completeness": "unknown",
    }
