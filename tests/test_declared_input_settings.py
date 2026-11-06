"""本机批准选择、整份 CAS 与真实 safe spec 的输入候选。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import (
    atomicio,
    declaredinputs,
    execspec,
    figcapture,
    inputdeclaration,
    inputremap,
    layoutsession,
    projectcopy,
    projectenv,
    runconfig,
)

SCRIPT = "scripts/科学图.py"
FILE = "数据/值.csv"
RC = "rc_0123456789ab"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "本机数据"))
    root = tmp_path / "中文项目"
    root.mkdir()
    for name, content in [
        (SCRIPT, "raise AssertionError('must not execute')"),
        (FILE, "x,y\n1,2\n"),
    ]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def worker(root, *, mode="project_root", run=None, rules=(), source=None):
    spec = execspec.safe_spec(
        SCRIPT,
        str(root),
        "__main__",
        interpreter=sys.executable,
        sandbox=str(root.parent / "sandbox"),
        env=None,
        cwd_mode=mode,
        input_remap=rules,
        stem_aliases={},
        **execspec.run_kwargs(run),
    )
    if source is not None:
        spec = replace(spec, script_source=str(source))
    return SimpleNamespace(spec=spec, script_name=SCRIPT, figures_dir=str(root), run=run)


def declare(root, *, revision=None, mode="project_root", run=None, files=None, remaps=()):
    return declaredinputs.declare(
        root,
        script=SCRIPT,
        cwd_mode=mode,
        run_config=run,
        files=[FILE] if files is None else files,
        remaps=remaps,
        expected_revision=revision,
    )


def fail_resource_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("没有匹配声明时不得探测输入、源码、cwd 或改指目标")

    monkeypatch.setattr(projectenv, "contained_path", forbidden)
    monkeypatch.setattr(inputremap, "snapshot", forbidden)
    monkeypatch.setattr(runconfig, "get", forbidden)
    monkeypatch.setattr(figcapture, "hash_file", forbidden)


def link_directory(source, target):
    """Windows 无 symlink 权限时用无管理员权限的 junction 验真实 realpath。"""
    try:
        source.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(source), str(target)],
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_absent_declaration_has_no_resource_io_or_store_write(project, monkeypatch):
    actual = worker(project)
    fail_resource_io(monkeypatch)
    assert declaredinputs.state(project) == {"version": 1, "revision": None, "declarations": []}
    assert declaredinputs.payload_for(actual) == {}
    assert not declaredinputs.store_path(project).exists()
    assert not declaredinputs.store_path(project).parent.exists()


def test_scope_is_exact_for_cwd_run_and_script_before_resource_io(project, monkeypatch):
    declare(project)
    actual = worker(project, mode="project")
    fail_resource_io(monkeypatch)
    assert declaredinputs.payload_for(actual) == {}
    assert declaredinputs.payload_for(SimpleNamespace()) == {}
    actual.script_name = "different.py"
    assert declaredinputs.payload_for(actual) == {}


def test_declare_payload_and_state_are_independent_and_never_read_content(project, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("parent must not hash or execute selected resources")

    monkeypatch.setattr(figcapture, "hash_file", forbidden)
    files = [FILE, FILE]
    view = declare(project, files=files)
    files.append("later.csv")
    assert view["declarations"][0]["files"] == [FILE]
    path = declaredinputs.store_path(project)
    assert path.is_relative_to(project.parent / "本机数据")
    assert not (project / "tavottofile").exists()
    assert view["revision"] == atomicio.revision_of(path.read_bytes())
    candidate = declaredinputs.payload_for(worker(project))["input_declaration"]
    assert candidate == inputdeclaration.validate_candidate(candidate)
    assert candidate["scope"] == {"script": SCRIPT, "cwd_mode": "project_root", "run_config": None}
    assert candidate["script_path"] == str((project / SCRIPT).resolve())
    assert candidate["files"] == [{"id": FILE, "path": str((project / FILE).resolve())}]
    assert candidate["prefixes"] == candidate["mappings"] == candidate["rules"] == []
    assert candidate["limits"] == {
        "max_files": projectcopy.MAX_FILES,
        "max_bytes": projectcopy.MAX_BYTES,
    }
    candidate["files"][0]["id"] = "mutated"
    view["declarations"][0]["files"].clear()
    assert declaredinputs.state(project)["declarations"][0]["files"] == [FILE]


def test_content_cas_preserves_other_scopes_and_retains_empty_store_revision(project):
    first = declare(project)
    second = declare(project, revision=first["revision"], mode="project")
    before = declaredinputs.store_path(project).read_bytes()
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        declare(project, revision=first["revision"], files=[])
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        declare(project, revision=None)
    assert declaredinputs.store_path(project).read_bytes() == before
    removed = declaredinputs.remove(
        project, script=SCRIPT, cwd_mode="project", expected_revision=second["revision"]
    )
    assert [row["scope"]["cwd_mode"] for row in removed["declarations"]] == ["project_root"]
    empty = declaredinputs.remove(
        project, script=SCRIPT, cwd_mode="project_root", expected_revision=removed["revision"]
    )
    assert empty["revision"] is not None and empty["declarations"] == []
    assert empty["revision"] != removed["revision"]
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        declare(project, revision=None)


@pytest.mark.parametrize(
    "identity", ["../secret.csv", "/secret.csv", "C:/secret.csv", "data\\x.csv", "data/CON"]
)
def test_explicit_files_never_grant_absolute_or_escaping_permissions(project, identity):
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, files=[identity])
    assert not declaredinputs.store_path(project).exists()


def test_missing_file_directory_and_aliases_are_not_silently_dropped(project):
    for selection in [["missing.csv"], ["数据"], [FILE, "数据/值.CSV"]]:
        with pytest.raises(declaredinputs.DeclaredInputsError):
            declare(project, files=selection)
    assert not declaredinputs.store_path(project).exists()


def test_same_script_distinct_run_configs_and_paramfree_do_not_borrow_default(project):
    runconfig.import_portable(
        project, [{"id": RC, "script": SCRIPT, "argv": ["--value", "中文", ""]}]
    )
    run = runconfig.selection(project, RC, script=SCRIPT)
    first = declare(project, run=RC)
    assert declaredinputs.payload_for(worker(project)) == {}
    second = declare(project, revision=first["revision"])
    assert len(second["declarations"]) == 2
    candidate = declaredinputs.payload_for(worker(project, run=run))["input_declaration"]
    assert candidate["scope"]["run_config"] == RC
    assert "argv" not in json.dumps(candidate)
    plain = declaredinputs.payload_for(worker(project))["input_declaration"]
    assert plain["scope"]["run_config"] is None
    mismatched = worker(project, run=run)
    mismatched.spec = replace(mismatched.spec, argv=("different",))
    assert declaredinputs.payload_for(mismatched) == {}
    mismatched = worker(project, run=run)
    mismatched.run = None
    assert declaredinputs.payload_for(mismatched) == {}


def test_unknown_sensitive_and_wrong_script_run_never_get_capture_authority(project):
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, run=RC)
    other = runconfig.put(project, "other.py", ["--value", "1"])
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, run=other.id)
    secret = runconfig.put(project, SCRIPT, ["--token", "secret-value"], sensitive=True)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, run=secret.id)
    declare(project)
    assert (
        declaredinputs.payload_for(worker(project, run=runconfig.selection(project, secret.id)))
        == {}
    )
    assert b"secret-value" not in declaredinputs.store_path(project).read_bytes()


def test_forgotten_or_changed_config_cannot_relabel_frozen_argv(project, monkeypatch):
    runconfig.import_portable(project, [{"id": RC, "script": SCRIPT, "argv": ["A"]}])
    actual = worker(project, run=runconfig.selection(project, RC, script=SCRIPT))
    declare(project, run=RC)
    original = runconfig.get(project, RC)
    monkeypatch.setattr(runconfig, "get", lambda *args, **kwargs: replace(original, argv=("B",)))
    assert declaredinputs.payload_for(actual) == {}


def test_external_exact_remap_uses_only_existing_approved_rule_and_opaque_id(project, tmp_path):
    external = tmp_path / "外部.csv"
    external.write_text("private values", encoding="utf-8")
    rule = {"kind": "file", "from": "../旧数据.csv", "to": str(external)}
    inputremap.add_rule(project, rule)
    view = declare(project, remaps=[rule["from"], rule["from"]])
    assert view["declarations"][0]["remaps"] == [rule["from"]]
    candidate = declaredinputs.payload_for(worker(project, rules=[rule]))["input_declaration"]
    row = candidate["files"][1]
    assert row["id"].startswith("remap:") and row["path"] == str(external.resolve())
    assert str(external) not in row["id"]
    assert candidate["mappings"] == [{"kind": "file", "from": rule["from"], "id": row["id"]}]
    assert candidate["rules"] == [rule]
    assert "to" not in json.dumps(declaredinputs.state(project)["declarations"][0]["remaps"])
    before = declaredinputs.store_path(project).read_bytes()
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, revision=view["revision"], remaps=[str(external)])
    assert declaredinputs.store_path(project).read_bytes() == before


def test_prefix_selection_does_not_walk_and_project_remaps_keep_relative_identity(
    project, tmp_path, monkeypatch
):
    external = tmp_path / "approved prefix"
    external.mkdir()
    (external / "a.csv").write_bytes(b"1")
    rule = {"kind": "prefix", "from": "", "to": str(external)}
    local = {"kind": "file", "from": "old.csv", "to": str(project / FILE)}
    inputremap.add_rule(project, rule)
    inputremap.add_rule(project, local)

    def forbidden(*args, **kwargs):
        pytest.fail("parent must not enumerate an approved prefix")

    monkeypatch.setattr(os, "scandir", forbidden)
    declare(project, remaps=["", "old.csv"])
    candidate = declaredinputs.payload_for(worker(project, rules=[rule, local]))[
        "input_declaration"
    ]
    assert candidate["prefixes"][0]["path"] == str(external.resolve())
    assert candidate["prefixes"][0]["id"].startswith("remap:")
    assert candidate["files"] == [{"id": FILE, "path": str((project / FILE).resolve())}]
    assert candidate["mappings"][1] == {"kind": "file", "from": "old.csv", "id": FILE}


def test_remap_same_from_changed_target_or_old_worker_rule_does_not_reuse_approval(
    project, tmp_path
):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    original = {"kind": "file", "from": "old-request.csv", "to": str(old)}
    inputremap.add_rule(project, original)
    declare(project, remaps=[original["from"]])
    changed = {**original, "to": str(new)}
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.payload_for(worker(project, rules=[changed]))
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.payload_for(worker(project))
    assert declaredinputs.payload_for(worker(project, rules=[original]))["input_declaration"][
        "files"
    ][1]["path"] == str(old.resolve())


def test_declared_file_symlink_escape_is_rechecked_before_read(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / Path(FILE).name).write_bytes(b"outside")
    source = project / FILE
    declare(project)
    source.unlink()
    source.parent.rmdir()
    link_directory(source.parent, outside)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.payload_for(worker(project))


def test_approved_remap_junction_keeps_actual_rule_and_canonical_capture_path(project, tmp_path):
    target = tmp_path / "real inputs"
    target.mkdir()
    (target / "a.csv").write_bytes(b"value")
    link = tmp_path / "approved link"
    link_directory(link, target)
    rule = {"kind": "file", "from": "old.csv", "to": str(link / "a.csv")}
    inputremap.add_rule(project, rule)
    declare(project, remaps=["old.csv"])
    candidate = declaredinputs.payload_for(worker(project, rules=[rule]))["input_declaration"]
    assert candidate["rules"] == [rule]
    assert candidate["files"][1]["path"] == str((target / "a.csv").resolve())


def test_only_owned_actual_safe_spec_can_produce_candidate(project):
    declare(project)
    actual = worker(project)
    terminal = execspec.native_spec(
        str(project / SCRIPT),
        project_root=str(project),
        interpreter=sys.executable,
        cwd=str(project),
        argv=(),
        stem_aliases={},
    )
    assert (
        declaredinputs.payload_for(
            SimpleNamespace(spec=terminal, script_name=SCRIPT, figures_dir=str(project), run=None)
        )
        == {}
    )
    fake = copy.copy(actual)
    fake.spec = SimpleNamespace(**vars(actual.spec))
    assert declaredinputs.payload_for(fake) == {}
    mismatched = copy.copy(actual)
    mismatched.figures_dir = str(project.parent)
    assert declaredinputs.payload_for(mismatched) == {}


def test_owned_shadow_script_source_is_exact_without_reading_source_content(project, tmp_path):
    source = tmp_path / "private shadow.py"
    source.write_bytes(b"raise AssertionError('not executed')")
    declare(project)
    candidate = declaredinputs.payload_for(worker(project, source=source))["input_declaration"]
    assert candidate["script_path"] == str(source.resolve())
    assert candidate["scope"]["script"] == SCRIPT


@pytest.mark.parametrize(
    "bad", [b"{", b'{"version":2,"declarations":[]}', b'{"version":true,"declarations":[]}']
)
def test_corrupt_future_settings_never_become_absence_or_get_overwritten(project, bad):
    path = declaredinputs.store_path(project)
    path.parent.mkdir(parents=True)
    path.write_bytes(bad)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.state(project)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project)
    assert path.read_bytes() == bad


def test_metadata_and_declaration_capacity_fail_before_replace(project, monkeypatch):
    first = declare(project)
    before = declaredinputs.store_path(project).read_bytes()
    monkeypatch.setattr(declaredinputs, "MAX_DECLARATIONS", 1)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, revision=first["revision"], mode="project")
    assert declaredinputs.store_path(project).read_bytes() == before
    monkeypatch.setattr(declaredinputs, "MAX_SETTINGS_BYTES", len(before))
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project, revision=first["revision"], files=[FILE, SCRIPT])
    assert declaredinputs.store_path(project).read_bytes() == before
    declaredinputs.store_path(project).write_bytes(before + b" ")
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.state(project)


def test_lock_failure_cannot_fall_back_to_unlocked_update(project, monkeypatch):
    def forbidden(*args, **kwargs):
        raise layoutsession.SessionStateUnavailable("no lock")

    monkeypatch.setattr(layoutsession, "acquire_file_lock", forbidden)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declare(project)
    assert not declaredinputs.store_path(project).exists()


def test_fresh_process_cas_observes_parent_revision_and_preserves_settings(project):
    first = declare(project)
    before = declaredinputs.store_path(project).read_bytes()
    code = """
import sys
from tavotto.engine import declaredinputs
try:
    declaredinputs.remove(sys.argv[1], script=sys.argv[2], cwd_mode='project_root', expected_revision=None)
except declaredinputs.DeclaredInputsConflict:
    print('conflict')
else:
    raise AssertionError('fresh process ignored CAS')
state = declaredinputs.state(sys.argv[1])
assert state['revision'] == sys.argv[3]
"""
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(project), SCRIPT, first["revision"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "conflict"
    assert declaredinputs.store_path(project).read_bytes() == before


def test_fresh_process_cannot_publish_while_parent_holds_actual_file_lock(project):
    first = declare(project)
    before = declaredinputs.store_path(project).read_bytes()
    code = """
import sys
from tavotto.engine import declaredinputs, layoutsession
layoutsession.LOCK_TIMEOUT_S = 0.05
try:
    declaredinputs.remove(sys.argv[1], script=sys.argv[2], cwd_mode='project_root', expected_revision=sys.argv[3])
except declaredinputs.DeclaredInputsError:
    print('locked')
else:
    raise AssertionError('process bypassed shared file lock')
"""
    path = declaredinputs.store_path(project)
    import time

    fd = layoutsession.acquire_file_lock(path.parent / (path.name + ".lock"), time.monotonic() + 1)
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(project), SCRIPT, first["revision"]],
            env={**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    finally:
        layoutsession.release_file_lock(fd)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "locked"
    assert declaredinputs.store_path(project).read_bytes() == before


@pytest.mark.parametrize("field,value", [("scope", {}), ("files", [FILE, FILE]), ("remaps", [{}])])
def test_malformed_stored_metadata_does_not_get_normalized_into_approval(project, field, value):
    declare(project)
    path = declaredinputs.store_path(project)
    raw = json.loads(path.read_bytes())
    raw["declarations"][0][field] = value
    encoded = atomicio.dumps_json(raw)
    path.write_bytes(encoded)
    with pytest.raises(declaredinputs.DeclaredInputsError):
        declaredinputs.state(project)
    assert path.read_bytes() == encoded
