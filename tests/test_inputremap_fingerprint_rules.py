"""One remap fingerprint authority, independent of stage/configuration IO."""

from __future__ import annotations

import builtins
import copy
import io
import os
from pathlib import Path

import pytest

from tavotto.engine import config, figcapture, inputremap

UNICODE_FILE = {"kind": "file", "from": "数据.csv", "to": "/new/project/中文/数据.csv"}
MIXED = [
    {"kind": "prefix", "from": "series", "to": "/new/project/tree"},
    {"kind": "file", "from": "series/a.csv", "to": "/new/project/data.csv"},
]
# Captured from the original root API with real isolated configuration, before
# extraction. These vectors do not repeat the implementation's hash algorithm.
VECTORS = [
    ([], ""),
    ([UNICODE_FILE], "1cbb400c739ad5f702b9ee8bc25c50e295e8c888ced9ab90ca33fde614227018"),
    (MIXED, "3a9f3a4ca7d239ce3b974fa25a27aab5cbcb971bc9e67932887a108a292f05ee"),
    (MIXED[::-1], "3a9f3a4ca7d239ce3b974fa25a27aab5cbcb971bc9e67932887a108a292f05ee"),
    (
        [{"kind": "prefix", "from": "", "to": "C:/中文/new-data"}],
        "c23845e7e117de06c553b77106b2bfc155edcec2bae42937468d14d041353312",
    ),
]


@pytest.mark.parametrize(("rules", "expected"), VECTORS)
def test_fingerprint_rules_and_real_root_match_original_vectors(tmp_path, rules, expected):
    root = tmp_path / "中文项目"
    root.mkdir()
    config.set_project_settings(str(root), {inputremap.SETTINGS_KEY: {"rules": rules}})
    original = copy.deepcopy(rules)
    assert inputremap.fingerprint(root) == expected
    assert inputremap.fingerprint_rules(rules) == expected
    assert inputremap.fingerprint_rules(tuple(rules)) == expected
    assert rules == original


def test_fingerprint_root_delegates_the_exact_clean_table_once(monkeypatch, tmp_path):
    rules, seen, reads = copy.deepcopy(MIXED), [], []

    def read(root):
        reads.append(root)
        return rules

    def pure(table):
        seen.append(table)
        return "pure-authority-result"

    monkeypatch.setattr(inputremap, "rules_for", read)
    monkeypatch.setattr(inputremap, "fingerprint_rules", pure)
    assert inputremap.fingerprint(tmp_path) == "pure-authority-result"
    assert reads == [tmp_path]
    assert len(seen) == 1 and seen[0] is rules


def test_fingerprint_root_keeps_one_configuration_read_and_old_settings_cleaning(monkeypatch):
    reads = []
    raw = [None, {**UNICODE_FILE, "added_at": 42}, UNICODE_FILE, {"kind": "unknown"}]

    def project_settings(root):
        reads.append(root)
        return {inputremap.SETTINGS_KEY: {"rules": raw}}

    monkeypatch.setattr(config, "project_settings", project_settings)
    assert inputremap.fingerprint("project") == VECTORS[1][1]
    assert reads == ["project"]
    with pytest.raises(ValueError, match="canonical remap"):
        inputremap.fingerprint_rules(raw)


def test_fingerprint_rules_performs_no_configuration_or_resource_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pure fingerprint attempted IO")

    with monkeypatch.context() as guard:
        for target, name in (
            (config, "project_settings"),
            (inputremap, "rules_for"),
            (builtins, "open"),
            (io, "open"),
            (Path, "open"),
            (Path, "stat"),
            (Path, "resolve"),
            (os, "stat"),
            (os, "lstat"),
            (os, "scandir"),
            (os, "listdir"),
        ):
            guard.setattr(target, name, forbidden)
        assert inputremap.fingerprint_rules([UNICODE_FILE]) == VECTORS[1][1]
        assert inputremap.fingerprint_rules([]) == ""


@pytest.mark.parametrize(
    "rules",
    [
        None,
        {},
        "not-a-table",
        [None],
        [{}],
        [{"kind": "unknown", "from": "data.csv", "to": "/new/data.csv"}],
        [{"kind": "file", "from": 1, "to": "/new/data.csv"}],
        [{"kind": "file", "from": "data.csv", "to": "relative.csv"}],
        [{"kind": "file", "from": "", "to": "/new/data.csv"}],
        [{**UNICODE_FILE, "extra": True}],
        [UNICODE_FILE, UNICODE_FILE],
        [
            {"kind": "file", "from": str(index), "to": "/new/data.csv"}
            for index in range(figcapture.MAX_REMAP_RULES + 1)
        ],
    ],
)
def test_fingerprint_rules_rejects_noncanonical_tables_without_silent_loss(rules):
    before = copy.deepcopy(rules)
    with pytest.raises(ValueError, match="canonical remap"):
        inputremap.fingerprint_rules(rules)
    assert rules == before


def test_fingerprint_rules_keeps_absolute_target_text_in_the_identity():
    old = [UNICODE_FILE]
    new = [{**UNICODE_FILE, "to": "/different/project/中文/数据.csv"}]
    assert inputremap.fingerprint_rules(old) != inputremap.fingerprint_rules(new)
