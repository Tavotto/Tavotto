"""显式文件选择归一与批准 remap 的整份代次校验，不扩大本机读取授权。"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto.engine import declaredinputs, figcapture, inputremap, projectenv
from test_declared_input_settings import FILE, SCRIPT, declare, link_directory, project as project


@pytest.mark.parametrize("form", ["relative", "absolute", "normalized", "native"])
def test_selected_regular_file_returns_portable_project_identity(project, form, monkeypatch):
    selected = {
        "relative": FILE,
        "absolute": str(project / FILE),
        "normalized": "数据/../数据/值.csv",
        "native": FILE.replace("/", os.sep),
    }[form]
    authority = projectenv.contained_path
    calls = []

    def resolve(root, value):
        calls.append((root, value))
        return authority(root, value)

    def forbidden(*_a, **_k):
        pytest.fail("File selection must not hash content or scan directories")

    monkeypatch.setattr(projectenv, "contained_path", resolve)
    monkeypatch.setattr(figcapture, "hash_file", forbidden)
    monkeypatch.setattr(os, "walk", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)
    assert declaredinputs.resolve_project_file(project, selected) == FILE
    assert calls == [(project, selected)]
    assert not declaredinputs.store_path(project).exists()


@pytest.mark.parametrize("selection", ["missing.csv", "数据", "", None, 1, "bad\x00name"])
def test_non_regular_or_invalid_selection_cannot_be_declared(project, selection):
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.resolve_project_file(project, selection)
    assert not declaredinputs.store_path(project).exists()


def test_outside_file_and_external_link_do_not_grant_project_access(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.csv"
    target.write_bytes(b"private")
    link_directory(project / "outside-link", outside)
    for selected in [str(target), "../outside/secret.csv", "outside-link/secret.csv"]:
        with pytest.raises(declaredinputs.DeclaredInputsError):
            declaredinputs.resolve_project_file(project, selected)
    assert target.read_bytes() == b"private"
    assert not declaredinputs.store_path(project).exists()


def test_inside_link_normalizes_to_real_project_file(project):
    link_directory(project / "inside-link", project / "数据")
    identity = declaredinputs.resolve_project_file(project, "inside-link/值.csv")
    assert identity == FILE
    view = declare(project, files=[identity])
    assert view["declarations"][0]["files"] == [FILE]


def _declare_at_generation(root, generation, *, revision=None, remaps=()):
    return declaredinputs.declare(
        root,
        script=SCRIPT,
        cwd_mode="project_root",
        files=[FILE],
        remaps=remaps,
        expected_revision=revision,
        expected_remap_generation=generation,
    )


@pytest.mark.parametrize("generation", [None, True, False, -1, 1.0, "0", [], {}])
def test_invalid_remap_generation_is_rejected_before_store_guard(project, monkeypatch, generation):
    def forbidden(*_a, **_k):
        pytest.fail("Invalid generation must be rejected before acquiring store resources")

    monkeypatch.setattr(declaredinputs, "_guard", forbidden)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        _declare_at_generation(project, generation)


def test_omitted_generation_preserves_legacy_no_remap_snapshot(project, monkeypatch):
    def forbidden(*_a, **_k):
        pytest.fail("Legacy file-only declaration must not read approved remaps")

    monkeypatch.setattr(inputremap, "snapshot", forbidden)
    view = declare(project)
    assert view["declarations"][0]["files"] == [FILE]


def test_explicit_generation_checks_single_snapshot_under_store_guard(project, monkeypatch):
    generation, _ = inputremap.snapshot(project)
    snapshot, guard = inputremap.snapshot, declaredinputs._guard
    events = []
    locked = False

    @contextmanager
    def guarded(root):
        nonlocal locked
        with guard(root):
            locked = True
            events.append("store_guard")
            try:
                yield
            finally:
                locked = False

    def observed(root):
        assert locked, "Remap mutex must be acquired after the declaration store guard"
        events.append("remap_snapshot")
        return snapshot(root)

    monkeypatch.setattr(declaredinputs, "_guard", guarded)
    monkeypatch.setattr(inputremap, "snapshot", observed)
    view = _declare_at_generation(project, generation)
    assert events == ["store_guard", "remap_snapshot"]
    assert view["declarations"][0]["remaps"] == []


def test_same_remap_source_retarget_requires_reloading_generation_and_never_overwrites(
    project, tmp_path
):
    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    original = {"kind": "file", "from": "../old.csv", "to": str(first)}
    inputremap.add_rule(project, original)
    generation, _ = inputremap.snapshot(project)
    view = _declare_at_generation(project, generation, remaps=[original["from"]])
    before = declaredinputs.store_path(project).read_bytes()

    replacement = {**original, "to": str(second)}
    inputremap.add_rule(project, replacement)
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        _declare_at_generation(
            project, generation, revision=view["revision"], remaps=[original["from"]]
        )
    assert declaredinputs.store_path(project).read_bytes() == before
    latest, _ = inputremap.snapshot(project)
    accepted = _declare_at_generation(
        project, latest, revision=view["revision"], remaps=[original["from"]]
    )
    assert accepted["revision"] != view["revision"]
    stored = json.loads(declaredinputs.store_path(project).read_text(encoding="utf-8"))
    assert stored["declarations"][0]["remaps"][0]["path"] == str(second.resolve())


def test_stale_store_cas_does_not_read_remaps_or_replace_another_selection(project, monkeypatch):
    view = declare(project)
    generation, _ = inputremap.snapshot(project)
    declare(project, revision=view["revision"], files=[])
    before = declaredinputs.store_path(project).read_bytes()

    def forbidden(*_a, **_k):
        pytest.fail("Stale declaration CAS must fail before acquiring remap mutex")

    monkeypatch.setattr(inputremap, "snapshot", forbidden)
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        _declare_at_generation(project, generation, revision=view["revision"])
    assert declaredinputs.store_path(project).read_bytes() == before


def test_even_empty_remap_selection_rejects_changed_approval_generation(project, tmp_path):
    generation, _ = inputremap.snapshot(project)
    external = tmp_path / "external.csv"
    external.write_bytes(b"new")
    inputremap.add_rule(project, {"kind": "file", "from": "old.csv", "to": str(external)})
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        _declare_at_generation(project, generation)
    assert not declaredinputs.store_path(project).exists()
