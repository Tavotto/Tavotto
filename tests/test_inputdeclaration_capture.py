"""Evidence concerns actual file bytes and execution entry, never latest labels."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import executionfact, inputdeclaration


def _candidate(root: Path, *, prefix=False):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plot.py").write_bytes(b"# declared figure\n")
    (root / "data.csv").write_bytes(b"x,y\n1,2\n")
    sandbox = root / "sandbox"
    sandbox.mkdir()
    value = {
        "version": 1,
        "scope": {"script": "plot.py", "cwd_mode": "sandbox", "run_config": None},
        "script_path": str(root / "plot.py"),
        "files": [{"id": "data.csv", "path": str(root / "data.csv")}],
        "prefixes": [],
        "mappings": [],
        "rules": [],
        "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
    }
    if prefix:
        directory = root / "inputs"
        (directory / "empty").mkdir(parents=True)
        (directory / "a.csv").write_bytes(b"3,4\n")
        value["prefixes"] = [{"id": "input-set", "path": str(directory)}]
        value["rules"] = [{"kind": "prefix", "from": "datasets", "to": str(directory)}]
        value["mappings"] = [{"kind": "prefix", "from": "datasets", "id": "input-set"}]
    return value


def _begin(candidate, remap_rules=None):
    root = Path(candidate["script_path"]).parent
    return inputdeclaration.begin(
        candidate,
        script="plot.py",
        script_path=candidate["script_path"],
        cwd_mode=candidate["scope"]["cwd_mode"],
        run_config=candidate["scope"]["run_config"],
        remap_rules=candidate["rules"] if remap_rules is None else remap_rules,
        actual_cwd=str(root / "sandbox"),
        project_root=str(root),
        sandbox=str(root / "sandbox"),
    )


def _finish(capture, root):
    return capture.finish(hashlib.sha256((root / "plot.py").read_bytes()).hexdigest())


def test_default_never_reads_a_resource(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("No declaration must not trigger resource IO")

    monkeypatch.setattr(inputdeclaration, "_snapshot", forbidden)
    assert (
        inputdeclaration.begin(
            None, script="plot.py", script_path="", cwd_mode="sandbox", run_config=None
        )
        is None
    )


def test_metadata_validation_does_not_inspect_private_paths(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    monkeypatch.setattr(
        inputdeclaration,
        "_regular",
        lambda *_a, **_k: pytest.fail("metadata validator read a file"),
    )
    checked = inputdeclaration.validate_candidate(candidate)
    checked["scope"]["script"] = "changed.py"
    assert candidate["scope"]["script"] == "plot.py"


def test_descriptor_creation_time_is_compared_to_its_own_baseline(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    original = inputdeclaration._FSTAT

    def descriptor(fd):
        value = original(fd)
        return SimpleNamespace(
            st_dev=value.st_dev,
            st_ino=value.st_ino,
            st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns,
            st_ctime_ns=0,
        )

    monkeypatch.setattr(inputdeclaration, "_FSTAT", descriptor)
    capture = _begin(candidate)
    assert capture is not None
    capture.entered(executionfact.new_id())
    assert _finish(capture, tmp_path) is not None


def test_existing_empty_prefix_mapping_is_preserved(tmp_path):
    candidate = _candidate(tmp_path, prefix=True)
    candidate["rules"][0]["from"] = ""
    candidate["mappings"][0]["from"] = ""
    capture = _begin(candidate)
    assert capture is not None
    capture.entered(executionfact.new_id())
    fact = _finish(capture, tmp_path)
    assert inputdeclaration.validate_fact(fact) == fact


def test_actual_capture_has_same_revision_after_moving_approved_inputs(tmp_path):
    first = _candidate(tmp_path / "旧项目", prefix=True)
    second = _candidate(tmp_path / "新项目", prefix=True)
    results = []
    for candidate in (first, second):
        capture = _begin(candidate)
        assert capture is not None
        capture.entered(executionfact.new_id())
        fact = _finish(capture, Path(candidate["script_path"]).parent)
        assert inputdeclaration.validate_fact(fact) == fact
        graph = fact["declaration"]
        assert graph["files"][0]["sha256"] == hashlib.sha256(b"x,y\n1,2\n").hexdigest()
        assert graph["prefixes"][0]["directories"] == ["", "empty"]
        assert str(tmp_path) not in json.dumps(fact)
        assert set(inputdeclaration.public_fact(fact)) == {"version", "revision"}
        results.append(fact)
    assert results[0]["revision"] == results[1]["revision"]
    assert results[0]["execution_id"] != results[1]["execution_id"]


def test_no_execution_entry_never_signs_old_figure(tmp_path):
    candidate = _candidate(tmp_path)
    capture = _begin(candidate)
    assert capture is not None and _finish(capture, tmp_path) is None
    capture.entered("not-an-execution")
    assert _finish(capture, tmp_path) is None


@pytest.mark.parametrize(
    "change",
    [
        "same_length",
        "missing",
        "script",
        "member_added",
        "empty_dir_added",
        "member_removed",
        "restored",
    ],
)
def test_change_after_entry_invalidates_capture(tmp_path, change):
    candidate = _candidate(tmp_path, prefix=True)
    capture = _begin(candidate)
    assert capture is not None
    capture.entered(executionfact.new_id())
    if change in {"same_length", "restored"}:
        path = tmp_path / "data.csv"
        old = path.read_bytes()
        path.write_bytes(old.replace(b"2", b"9"))
        if change == "restored":
            path.write_bytes(old)
    elif change == "missing":
        (tmp_path / "data.csv").unlink()
    elif change == "script":
        (tmp_path / "plot.py").write_bytes(b"# different figure\n")
    elif change == "member_added":
        (tmp_path / "inputs" / "new.csv").write_bytes(b"5,6\n")
    elif change == "empty_dir_added":
        (tmp_path / "inputs" / "new-empty").mkdir()
    elif change == "member_removed":
        (tmp_path / "inputs" / "a.csv").unlink()
    assert _finish(capture, tmp_path) is None


def test_actual_loaded_source_must_match_declared_script(tmp_path):
    candidate = _candidate(tmp_path)
    capture = _begin(candidate)
    assert capture is not None
    capture.entered(executionfact.new_id())
    assert capture.finish(hashlib.sha256(b"# a shadow source\n").hexdigest()) is None


def test_two_entries_cannot_share_one_capture(tmp_path):
    candidate = _candidate(tmp_path)
    capture = _begin(candidate)
    assert capture is not None
    capture.entered(executionfact.new_id())
    capture.entered(executionfact.new_id())
    assert _finish(capture, tmp_path) is None


@pytest.mark.parametrize(
    "part", ["file_budget", "byte_budget", "cwd", "remap_target", "relative_path", "duplicate_id"]
)
def test_invalid_scope_or_closed_set_does_not_obtain_evidence(tmp_path, part):
    candidate = _candidate(tmp_path, prefix=True)
    actual_rules = copy.deepcopy(candidate["rules"])
    if part == "file_budget":
        candidate["limits"]["max_files"] = 2
    elif part == "byte_budget":
        candidate["limits"]["max_bytes"] = 2
    elif part == "cwd":
        candidate["scope"]["cwd_mode"] = "project_root"
    elif part == "remap_target":
        candidate["rules"][0]["to"] = str(tmp_path / "elsewhere")
    elif part == "relative_path":
        candidate["files"][0]["path"] = "data.csv"
    elif part == "duplicate_id":
        candidate["prefixes"][0]["id"] = "data.csv"
    assert _begin(candidate, remap_rules=actual_rules) is None


def test_invalid_fact_cannot_be_promoted_without_resource_io(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    capture = _begin(candidate)
    assert capture is not None
    eid = executionfact.new_id()
    capture.entered(eid)
    fact = _finish(capture, tmp_path)
    monkeypatch.setattr(
        inputdeclaration, "_regular", lambda *_a, **_k: pytest.fail("fact checker read a path")
    )
    changed = copy.deepcopy(fact)
    changed["declaration"]["files"][0]["sha256"] = "0" * 64
    assert inputdeclaration.validate_fact(changed) is None
    assert inputdeclaration.validate_fact(fact, execution_id=executionfact.new_id()) is None
    assert inputdeclaration.validate_fact(fact, execution_id=eid) == fact


def test_empty_prefix_directory_changes_content_identity(tmp_path):
    candidate = _candidate(tmp_path, prefix=True)
    one = _begin(candidate)
    assert one is not None
    one.entered(executionfact.new_id())
    old = _finish(one, tmp_path)
    (tmp_path / "inputs" / "other-empty").mkdir()
    two = _begin(candidate)
    assert two is not None
    two.entered(executionfact.new_id())
    assert _finish(two, tmp_path)["revision"] != old["revision"]


def test_prefix_does_not_follow_a_real_directory_link_outside_the_set(tmp_path):
    from test_declared_input_settings import link_directory

    candidate = _candidate(tmp_path, prefix=True)
    outside = tmp_path / "not-declared"
    outside.mkdir()
    (outside / "private.csv").write_bytes(b"not part of the approved prefix")
    link_directory(tmp_path / "inputs" / "escape", outside)
    assert _begin(candidate) is None
