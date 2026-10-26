"""`scripts/smoke_app.py::_check_control_plane` 的期望值语义。

`selected` 回答「产物里有没有 workerd」，`sessions` 回答「这次渲染实际走了谁」。
`conda-fallback` 是 nightly Conda 腿的期望：二进制在、会话却只在 Python 池上。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_smoke_app_cp", ROOT / "scripts" / "smoke_app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check(monkeypatch, expect, selected, sessions):
    mod = _load()
    monkeypatch.setattr(
        mod, "_get", lambda url: {"control_plane": {"selected": selected, "sessions": sessions}}
    )
    mod._check_control_plane("http://x", expect)
    return mod


@pytest.mark.parametrize(
    ("expect", "selected", "sessions", "ok"),
    [
        ("conda-fallback", "workerd", ["python"], True),
        ("conda-fallback", "workerd", ["workerd"], False),
        ("conda-fallback", "workerd", ["python", "workerd"], False),
        ("conda-fallback", "workerd", [], False),
        ("conda-fallback", "python", ["python"], False),
        ("workerd", "workerd", ["workerd"], True),
        ("workerd", "workerd", ["python"], False),
        ("workerd", "python", ["python"], False),
        ("python", "python", ["python"], True),
        ("python", "workerd", ["python"], False),
    ],
)
def test_control_plane_expectations(monkeypatch, expect, selected, sessions, ok):
    mod = _load()
    if ok:
        _check(monkeypatch, expect, selected, sessions)
    else:
        with pytest.raises(mod.SmokeError):
            monkeypatch.setattr(
                mod,
                "_get",
                lambda url: {"control_plane": {"selected": selected, "sessions": sessions}},
            )
            mod._check_control_plane("http://x", expect)


def test_conda_leg_of_nightly_uses_conda_fallback():
    wf = (ROOT / ".github" / "workflows" / "nightly.yml").read_text(encoding="utf-8")
    assert '{ "conda-fallback" } else { "workerd" }' in wf
