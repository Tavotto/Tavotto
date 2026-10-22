"""Research input integrity, using synthetic bytes and no network or script execution."""

import copy
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci"))
import compat_guardian as guardian  # noqa: E402
import compat_research_sources as sources  # noqa: E402


@pytest.fixture
def project():
    return {
        "project_id": "example",
        "case_id": "research_example",
        "upstream": {"repository": "example/research", "commit": "a" * 40},
        "files": {"source/plot.py": hashlib.sha256(b"# frozen, never executed\n").hexdigest()},
        "upstream_path_map": {"figure space/plot.py": "source/plot.py"},
        "license_files": ["source/plot.py"],
    }


def test_fetch_is_commit_pinned_hash_checked_and_reuses_verified_cache(
    project, tmp_path, monkeypatch
):
    calls = []

    def download(request, timeout):
        calls.append((request.full_url, timeout))
        return io.BytesIO(b"# frozen, never executed\n")

    monkeypatch.setattr(sources.urllib.request, "urlopen", download)
    cache = tmp_path / "cache"
    root = tmp_path / "checkout"
    first = sources.fetch_project(project, cache, root=root)
    assert sources.fetch_project(project, cache, root=root) == first
    assert first["verified_files"] == 1
    assert calls == [
        (
            f"https://raw.githubusercontent.com/example/research/{'a' * 40}/figure%20space/plot.py",
            60,
        )
    ]
    assert (
        sources.verified_file(project, "source/plot.py", cache).read_bytes()
        == b"# frozen, never executed\n"
    )


def test_wrong_upstream_bytes_are_not_published(project, tmp_path, monkeypatch):
    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"changed"))
    cache = tmp_path / "cache"
    with pytest.raises(sources.SourceError, match="SHA-256"):
        sources.fetch_project(project, cache, root=tmp_path / "checkout")
    assert not list(cache.rglob("plot.py"))


def test_cache_tampering_cannot_be_used_as_scientific_input(project, tmp_path, monkeypatch):
    monkeypatch.setattr(
        sources.urllib.request,
        "urlopen",
        lambda *a, **kw: io.BytesIO(b"# frozen, never executed\n"),
    )
    sources.fetch_project(project, tmp_path / "cache", root=tmp_path / "checkout")
    path = sources.verified_file(project, "source/plot.py", tmp_path / "cache")
    path.write_bytes(b"tampered")
    with pytest.raises(sources.SourceError, match="changed"):
        sources.verified_file(project, "source/plot.py", tmp_path / "cache")
    with pytest.raises(sources.SourceError, match="changed"):
        sources.fetch_project(project, tmp_path / "cache", root=tmp_path / "checkout")


@pytest.mark.parametrize(
    "path", ["../plot.py", "/plot.py", "C:/plot.py", "a\\plot.py", "a//plot.py", "a/./plot.py"]
)
def test_noncanonical_upstream_paths_are_rejected_before_network(
    project, path, tmp_path, monkeypatch
):
    project["upstream_path_map"] = {path: "source/plot.py"}
    monkeypatch.setattr(
        sources.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("must not fetch")
    )
    with pytest.raises(sources.SourceError):
        sources.fetch_project(project, tmp_path / "cache", root=tmp_path / "checkout")


@pytest.mark.parametrize("revision", ["main", "v1.0", "a" * 39, "A" * 40])
def test_mutable_or_incomplete_revision_is_rejected(project, revision):
    project["upstream"]["commit"] = revision
    with pytest.raises(sources.SourceError, match="immutable"):
        sources.validate_project(project)


def test_source_mapping_and_license_coverage_are_required(project):
    for field in ("files", "upstream_path_map", "license_files"):
        changed = copy.deepcopy(project)
        changed[field] = {} if field != "license_files" else []
        with pytest.raises(sources.SourceError):
            sources.validate_project(changed)


def test_symlink_escape_and_repository_cache_are_rejected(project, tmp_path):
    cache = tmp_path / "cache"
    directory = sources.project_directory(project, cache)
    directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (directory / "figure space").symlink_to(outside, target_is_directory=True)
    with pytest.raises(sources.SourceError, match="escapes"):
        sources.verified_file(project, "source/plot.py", cache)
    with pytest.raises(sources.SourceError, match="outside"):
        sources.cache_root(tmp_path / "checkout/cache", root=tmp_path / "checkout")


def test_unknown_case_cannot_select_an_unregistered_project(project, tmp_path):
    path = tmp_path / sources.COHORT_REL
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"development": [project]}))
    with pytest.raises(sources.SourceError, match="registered"):
        sources.project_for_case(
            {"id": "arbitrary", "research_project_id": "example"}, root=tmp_path
        )


def test_default_registry_never_loads_or_fetches_research(monkeypatch):
    local = {"local": {"id": "local"}}
    monkeypatch.setattr(guardian, "registered", lambda _suite: local)
    monkeypatch.setattr(
        sources.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("unexpected download")
    )
    assert guardian.selected_registry("compat_engine", ()) == local
    assert guardian.selected_registry("compat_engine", ("local",)) == local


def test_changed_research_registration_is_rejected_before_staging(monkeypatch):
    monkeypatch.setattr(guardian, "registered", lambda _suite: {})
    monkeypatch.setattr(guardian, "matches_head", lambda *a: False)
    monkeypatch.setattr(
        sources.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("unexpected download")
    )
    with pytest.raises(guardian.EvidenceError, match="reviewed HEAD"):
        guardian.selected_registry("compat_engine", ("research_example",))
