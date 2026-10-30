"""Bounded, content-free observations from a CLI's stderr.

This is diagnostic evidence, not an authority on the cause of a failure. A
CLI may quote arbitrary content, and a category must never change task state.
"""

from __future__ import annotations

import re

CATEGORIES = ("auth", "network", "not_installed", "bad_config", "crash", "other")
_PATTERNS = {
    "auth": re.compile(
        r"\b(?:authentication_error|invalid_api_key|unauthorized|invalid api key)\b", re.I
    ),
    "network": re.compile(
        r"\b(?:econnrefused|enotfound|network is unreachable|connection refused)\b", re.I
    ),
    "not_installed": re.compile(r"\b(?:claude|codex|node): command not found\b", re.I),
    "bad_config": re.compile(r"\b(?:invalid configuration|invalid config|unknown option)\b", re.I),
    "crash": re.compile(r"\b(?:segmentation fault|fatal error|uncaught exception)\b", re.I),
}


class Classifier:
    """Consume bounded chunks and retain only category names and a short overlap."""

    def __init__(self) -> None:
        self._matches: set[str] = set()
        self._overlap = ""

    def feed(self, chunk: str) -> None:
        text = self._overlap + chunk
        self._matches.update(name for name, pattern in _PATTERNS.items() if pattern.search(text))
        self._overlap = text[-128:]

    def result(self) -> str:
        self._overlap = ""
        return next(iter(self._matches)) if len(self._matches) == 1 else "other"


def consume(stream) -> str:
    classifier = Classifier()
    try:
        while chunk := stream.read(4096):
            classifier.feed(chunk)
    except (OSError, ValueError):
        return "other"
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass
    return classifier.result()
