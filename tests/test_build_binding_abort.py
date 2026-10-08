"""r4221675663：转录没绑上（StaleTranscriptError）时，热 worker 不能带着未绑定的结果继续被复用。"""

from __future__ import annotations

import contextlib

import pytest

from tavotto.engine import inputbroker, inputtranscript, pool


@contextlib.contextmanager
def _no_serving(_worker):
    yield


def _boom(*_a, **_k):
    raise inputtranscript.StaleTranscriptError("blocked")


def _bare(cls):
    w = object.__new__(cls)
    w.built = False
    w.build_failed = False
    w.last_build_descriptors = []
    w.last_patch_hash_by_stem = {}
    return w


@pytest.mark.parametrize("cls", [pool.EngineWorker, pool.WorkerdWorker])
def test_a_binding_abort_leaves_the_worker_unbuilt(cls, monkeypatch):
    monkeypatch.setattr(inputbroker, "serving", _no_serving)
    monkeypatch.setattr(inputbroker, "finished", _boom)
    w = _bare(cls)
    resp = {"descriptors": [{"id": 1}]}
    monkeypatch.setattr(w, "request", lambda *a, **k: resp, raising=False)
    monkeypatch.setattr(w, "_call", lambda *a, **k: resp, raising=False)
    with pytest.raises(inputtranscript.StaleTranscriptError):
        w.ensure_built()
    assert w.built is False and w.build_failed is True
