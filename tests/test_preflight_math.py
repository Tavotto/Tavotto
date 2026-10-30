"""排不出来的公式进预检（ADR 0152 §七）：求值器读 `math_errors`，缓存查找只读、不起进程。

同源向量在 `tests/golden/preflight_vectors.json`（`canvas-text-math-unrenderable`）；这里补
向量不便表达的：严重度来自 profile、缓存查找不起子进程、环境问题不算「排不出」。
"""

from __future__ import annotations

import pytest

from tavotto.engine import preflight, profiles


def _spec(texts):
    return {
        "page": {"w_mm": 80.0, "h_mm": 60.0, "margin_mm": 0.0},
        "panels": [],
        "texts": texts,
        "objects": [],
    }


def _text(**kw):
    base = {
        "id": "t1",
        "text": "$\\bad$",
        "size_pt": 9.0,
        "bold": False,
        "font_family": "serif",
        "text_format": "latex",
        "rect_mm": [1.0, 1.0, 10.0, 5.0],
        "hidden": False,
    }
    base.update(kw)
    return base


@pytest.mark.parametrize("pid", ["lab-publication-v1", "free-form-v1"])
def test_math_unrenderable_severity_comes_from_the_profile(pid):
    profile = profiles.load(pid)
    issues = preflight.run(_spec([_text(math_errors=[{"src": "\\bad", "why": "x"}])]), profile)
    got = [i for i in issues if i["id"] == "math-unrenderable"]
    assert len(got) == 1
    assert got[0]["severity"] == profile["severity"]["math-unrenderable"] == "warn"
    assert got[0]["object_ids"] == ["t1"]


def test_no_errors_or_hidden_text_reports_nothing():
    profile = profiles.load("lab-publication-v1")
    for t in (
        _text(),
        _text(math_errors=[]),
        _text(hidden=True, math_errors=[{"src": "a", "why": ""}]),
    ):
        assert not [i for i in preflight.run(_spec([t]), profile) if i["id"] == "math-unrenderable"]
