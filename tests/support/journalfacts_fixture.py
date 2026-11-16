"""Shared `isolated` fixture for the journal facts v2 tests.

Registered through `pytest_plugins`, so it is available whatever the collection order.
"""

import base64

import pytest
from nacl.signing import SigningKey

from tavotto.engine import journalfacts_remote as client, journalremote


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    key = SigningKey.generate()
    monkeypatch.setattr(
        journalremote, "TRUSTED_KEYS", {"fx": base64.b64encode(bytes(key.verify_key)).decode()}
    )
    monkeypatch.setattr(client, "cache_dir", lambda: tmp_path / "v2-cache")
    monkeypatch.setattr(
        client, "_bases", lambda _locale=None: ("https://fx.invalid/v2/journal-catalog/",)
    )
    monkeypatch.setattr(journalremote, "disabled", lambda: False)
    monkeypatch.setattr(journalremote, "_token", lambda _base, **_kw: "fixture-device-token")
    return key, tmp_path
