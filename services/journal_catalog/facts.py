"""Service import facade for the shared v2 fact contract (pure standard library)."""

from tavotto.engine.journalfacts_contract import (
    INDEX_KIND,
    MAX_BUNDLE_BYTES,
    SCHEMA,
    FactValidationError,
    ValidatedIndex,
    decode_bundle,
    validate_bundle,
    validate_index,
    validate_indexed_bundle,
)

__all__ = [
    "INDEX_KIND",
    "MAX_BUNDLE_BYTES",
    "SCHEMA",
    "FactValidationError",
    "ValidatedIndex",
    "decode_bundle",
    "validate_bundle",
    "validate_index",
    "validate_indexed_bundle",
]
