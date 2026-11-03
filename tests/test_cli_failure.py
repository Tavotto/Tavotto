"""CLI stderr contributes only bounded diagnostic categories."""

import io

from tavotto.engine.cli_failure import Classifier, consume


def test_chunk_boundary_and_ambiguous_evidence():
    classifier = Classifier()
    classifier.feed("private content invalid_")
    classifier.feed("api_key secret")
    assert classifier.result() == "auth"
    assert classifier._overlap == ""
    assert consume(io.StringIO("invalid_api_key and ECONNREFUSED")) == "other"
    assert consume(io.StringIO("file not found: /private/anything")) == "other"
