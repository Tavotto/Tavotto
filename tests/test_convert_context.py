"""Dependency evidence, integrity and adopted resource paths (ADR 0143).

The subject is the bytes exposed to the agent, the unchanged original resources,
and a new process reopening the adopted file after its candidate cache is gone.
No model response or mere import success is treated as fidelity evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import ai_convert, convertcontext, inputremap, pool
from tavotto.engine.agent_runner import tools


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "plots").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "assets" / "values.csv").write_text("1,4,9\n")
    (root / "assets" / "face.ttf").write_bytes(b"font fixture\x00\xff")
    (root / "plots" / "helper.py").write_text("def scale(x):\n    return [v * 2 for v in x]\n")
    (root / "plots" / "fig.py").write_text(
        "from pathlib import Path\nfrom helper import scale\n"
        "font = '../assets/face.ttf'\n"
        "data = Path(__file__).resolve().parent / '../assets/values.csv'\n"
    )
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", tmp_path / "candidates")
    return root


def candidate(root, script="plots/fig.py", language="python"):
    return ai_convert.create_candidate(
        "abcdef012345",
        root / script,
        root,
        script,
        library="pillow" if language == "python" else None,
        source_language=language,
    )


def ready(cand):
    cand.new_bytes = b"import matplotlib.pyplot as plt\nplt.plot([1, 4, 9])\n"
    cand.target.write_bytes(cand.new_bytes)
    ai_convert.stage(cand)


def test_snapshot_preserves_nested_resource_paths_and_import_provenance(project):
    cand = candidate(project)
    rows = {r["path"]: r for r in cand.context["files"]}
    assert set(rows) == {"plots/helper.py", "assets/face.ttf", "assets/values.csv"}
    for rel, row in rows.items():
        original = (project / rel).read_bytes()
        copied = cand.dir / convertcontext.DIRECTORY / rel
        assert copied.read_bytes() == original
        assert not copied.is_symlink()
        assert row["sha256"] == hashlib.sha256(original).hexdigest()
    assert json.loads((cand.dir / convertcontext.MANIFEST).read_text()) == cand.context
    assert cand.context["coverage"] == "static_partial"
    prompt = ai_convert.prompt_for(cand)
    assert convertcontext.MANIFEST in prompt and "Path(__file__)" in prompt


def test_confirmed_external_input_policy_is_visible_without_copying_the_data(project):
    external = project.parent / "supplied.csv"
    external.write_text("4,5,6\n")
    inputremap.add_rule(project, {"kind": "file", "from": "moved.csv", "to": str(external)})
    cand = candidate(project)
    policy = cand.context["input_read_policy"]
    assert policy == {
        "project_mappings_configured": True,
        "mapping_applies_to": "missing_read_open",
        "filesystem_probes_remapped": False,
        "target_availability": "verify_by_reading_in_application",
    }
    context = (cand.dir / convertcontext.MANIFEST).read_text()
    assert str(external) not in context and external.read_text() not in context
    assert cand.context["input_remap_fingerprint"] == inputremap.fingerprint(project)
    assert convertcontext.unchanged(project, cand.context, snapshot_dir=cand.dir)


@pytest.mark.parametrize("initial_mapping", [False, True])
def test_check_and_adopt_reject_changed_input_mapping_policy(project, initial_mapping):
    external = project.parent / "supplied.csv"
    external.write_text("4,5,6\n")
    if initial_mapping:
        inputremap.add_rule(project, {"kind": "file", "from": "moved.csv", "to": str(external)})
    cand = candidate(project)
    ready(cand)
    if initial_mapping:
        inputremap.remove_rule(project, "file", "moved.csv")
    else:
        inputremap.add_rule(project, {"kind": "file", "from": "moved.csv", "to": str(external)})
    assert not convertcontext.unchanged(project, cand.context, snapshot_dir=cand.dir)
    called = []
    cand.extras["runner"] = lambda *args: called.append(args)
    assert ai_convert.agent_check(cand) == (False, "convert_context_changed")
    assert called == []
    with pytest.raises(ai_convert.ConvertError, match="convert_context_changed"):
        ai_convert.adopt(cand.sid, str(project.resolve()))
    assert not (project / "plots" / cand.target_name).exists()


@pytest.mark.parametrize("changed", [None, "st_ctime_ns", "st_ino", "st_mtime_ns"])
def test_windows_resource_read_compares_change_times_within_the_same_api(
    project, monkeypatch, changed
):
    """Actual bytes are readable despite Windows' stat/fstat ctime disagreement;
    a change on the open handle during that read is still rejected.
    """
    from types import SimpleNamespace

    real_fstat = os.fstat
    calls = 0

    def windows_fstat(fd):
        nonlocal calls
        calls += 1
        st = real_fstat(fd)
        fields = {name: getattr(st, name) for name in dir(st) if name.startswith("st_")}
        fields["st_ctime_ns"] += 1_000_000_000
        if changed and calls > 1:
            fields[changed] += 1
        return SimpleNamespace(**fields)

    # Local proxy: changing the process-wide os.name would change pathlib itself.
    monkeypatch.setattr(
        convertcontext, "os", SimpleNamespace(**{**vars(os), "name": "nt", "fstat": windows_fstat})
    )
    path = project / "assets" / "face.ttf"
    scope = convertcontext.readscope.ReadScope(home=project.parent, project_root=project)
    data, reason = convertcontext._read(project, path, scope)
    assert calls == 2
    if changed:
        assert (data, reason) == (None, "resource_changed")
    else:
        assert data == path.read_bytes() and reason == ""


def test_selected_reference_is_visible_and_bound_to_adoption(project):
    reference = project / "assets" / "colormap.csv"
    reference.write_text("0.2422,0.1504,0.6603\n0.9769,0.9839,0.0805\n")
    cand = ai_convert.create_candidate(
        "abcdef012345",
        project / "plots" / "fig.py",
        project,
        "plots/fig.py",
        library="pillow",
        reference_files=("assets/colormap.csv",),
    )
    assert cand.context["user_selected"] == ["assets/colormap.csv"]
    assert (
        cand.dir / convertcontext.DIRECTORY / "assets/colormap.csv"
    ).read_bytes() == reference.read_bytes()
    assert "user_selected" in ai_convert.prompt_for(cand)
    ready(cand)
    reference.write_text("1,0,0\n")
    with pytest.raises(ai_convert.ConvertError) as exc:
        ai_convert.adopt(cand.sid, str(project.resolve()))
    assert exc.value.code == "convert_context_changed"
    assert not (project / "plots/fig_matplotlib.py").exists()


@pytest.mark.parametrize(
    "value", ["colors.csv", [None], ["../outside.csv"], [".env"], ["missing.csv"], ["assets"]]
)
def test_selected_references_fail_closed_instead_of_disappearing(project, value):
    with pytest.raises(ValueError, match="convert_reference_invalid"):
        convertcontext.selected_paths(project, value)


def test_selected_reference_uses_privacy_and_size_policy(project, tmp_path, monkeypatch):
    secret = project / "assets" / "credentials.json"
    secret.write_text('{"password":"private"}')
    link = project / "assets" / "linked.csv"
    link.symlink_to(project / "assets" / "values.csv")
    for rel in ("assets/credentials.json", "assets/linked.csv"):
        with pytest.raises(ValueError, match="convert_reference_invalid"):
            convertcontext.selected_paths(project, [rel])
    monkeypatch.setattr(convertcontext, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="convert_reference_invalid"):
        convertcontext.selected_paths(project, ["assets/values.csv"])


@pytest.mark.parametrize(
    ("name", "source", "helper"),
    [
        ("fig.R", 'source("helper.R")\nd <- read.csv("../assets/values.csv")\n', "helper.R"),
        ("fig.m", "y = helper(1);\na = load('../assets/values.csv');\n", "helper.m"),
    ],
)
def test_foreign_helper_and_data_are_copied_without_execution(project, name, source, helper):
    (project / "plots" / name).write_text(source)
    (project / "plots" / helper).write_text("this must never execute\n")
    cand = candidate(project, "plots/" + name, "r" if name.endswith("R") else "matlab")
    assert {r["path"] for r in cand.context["files"]} == {"plots/" + helper, "assets/values.csv"}


def test_credentials_environment_large_files_and_links_are_not_exposed(
    project, tmp_path, monkeypatch
):
    (project / "assets" / "credentials.json").write_text('{"password": "sensitive"}')
    (project / "assets" / "ordinary.txt").write_text('api_key = "sk-sensitive-123456789"')
    (project / "assets" / "large.bin").write_bytes(b"x" * 100)
    (project / "env").mkdir()
    (project / "env" / "pyvenv.cfg").write_text("home = somewhere")
    (project / "env" / "data.txt").write_text("not project data")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be copied")
    try:
        (project / "assets" / "link.txt").symlink_to(outside)
        (project / "linked").symlink_to(project / "assets", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    (project / "plots" / "fig.py").write_text(
        "\n".join(
            repr(p)
            for p in (
                "../assets/credentials.json",
                "../assets/ordinary.txt",
                "../assets/large.bin",
                "../env/data.txt",
                "../assets/link.txt",
                "../linked/values.csv",
                "../../outside.txt",
            )
        )
    )
    monkeypatch.setattr(convertcontext, "MAX_FILE_BYTES", 80)
    cand = candidate(project)
    assert {r["path"] for r in cand.context["files"]} == {"assets/ordinary.txt"}
    assert (
        b"sk-sensitive-123456789"
        not in (cand.dir / "project_context/assets/ordinary.txt").read_bytes()
    )
    reasons = {r["reason"] for r in cand.context["omitted"]}
    assert {
        "sensitive_path",
        "file_budget",
        "environment",
        "redirect_or_parent_path",
    } <= reasons
    assert outside.read_text() == "must not be copied"


def test_snapshot_budget_is_explicit_and_unrelated_files_are_not_copied(project, monkeypatch):
    (project / "unrelated.txt").write_text("never nominated")
    monkeypatch.setattr(convertcontext, "MAX_FILES", 1)
    cand = candidate(project)
    assert len(cand.context["files"]) == 1
    assert any(r["reason"] == "context_budget" for r in cand.context["omitted"])
    assert not (cand.dir / convertcontext.DIRECTORY / "unrelated.txt").exists()


@pytest.mark.parametrize("where", ["source", "snapshot"])
def test_check_and_adopt_refuse_changed_resources_even_same_size_and_mtime(project, where):
    # Larger than project_fingerprint's content hash threshold: size/mtime alone
    # cannot detect this mutation; the resource manifest's SHA must reject it.
    (project / "assets" / "face.ttf").write_bytes(b"\x00\xff" * (160 * 1024))
    cand = candidate(project)
    ready(cand)
    path = (
        (project if where == "source" else cand.dir / convertcontext.DIRECTORY)
        / "assets"
        / "face.ttf"
    )
    before = path.stat()
    original = path.read_bytes()
    path.write_bytes(b"\x01" + original[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    called = []
    cand.extras["runner"] = lambda *args: called.append(args)
    assert ai_convert.agent_check(cand) == (False, "convert_context_changed")
    assert called == []
    with pytest.raises(ai_convert.ConvertError, match="convert_context_changed"):
        ai_convert.adopt(cand.sid, str(project.resolve()))
    assert not (project / "plots" / cand.target_name).exists()


def test_held_resource_baseline_cannot_be_rebased_on_resume(project):
    cand = candidate(project)
    ready(cand)
    meta = ai_convert._hold_meta(cand, ai_convert.STATE_INSTALLED)
    (project / "assets" / "face.ttf").write_bytes(b"changed font\x00\xff")
    with pytest.raises(ai_convert.ConvertError, match="convert_context_changed"):
        ai_convert._rebuild(cand.dir, meta, project)


def test_check_rechecks_snapshot_after_candidate_execution(project):
    cand = candidate(project)
    cand.target.write_text("import matplotlib.pyplot as plt\nplt.plot([1, 4])\n")

    def runner(*_args):
        (cand.dir / "project_context/assets/values.csv").write_text("2,5,10\n")
        return {"stems": ["plot"], "svg": "<svg/>"}

    cand.extras["runner"] = runner
    assert ai_convert.agent_check(cand) == (False, "convert_context_changed")


def test_context_tools_browse_nested_paths_but_never_write_or_escape(project):
    cand = candidate(project)
    task = tools.TOOLSETS["convert_to_editable"]
    env = tools.RunEnvelope(
        task_type=task.task_type,
        cwd=cand.dir,
        target="fig.py",
        output=cand.target_name,
        instruction="convert",
        limits=task.default_limits,
    )
    session = tools.ConvertSession(env)
    before = (project / "assets" / "values.csv").read_bytes()
    listing = session.list_dir({"path": "project_context/assets"})
    assert not listing.is_error and "values.csv" in listing.content
    assert session.list_dir({"path": "../"}).is_error
    assert not session.read_file({"path": "project_context/plots/helper.py"}).is_error
    assert session.create_file(
        {"path": "project_context/assets/values.csv", "content": "2"}
    ).is_error
    assert (project / "assets" / "values.csv").read_bytes() == before


def test_adopted_script_reopens_from_unrelated_cwd_after_cache_removal(project, tmp_path):
    cand = candidate(project)
    cand.new_bytes = b"""from pathlib import Path
from helper import scale
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
p = Path(__file__).resolve().parent / "../assets/values.csv"
values = scale([int(v) for v in p.read_text().strip().split(",")])
fig, ax = plt.subplots()
ax.plot(values)
print([int(v) for v in ax.lines[0].get_ydata()])
"""
    cand.target.write_bytes(cand.new_bytes)
    ai_convert.stage(cand)
    adopted = ai_convert.adopt(cand.sid, str(project.resolve()))
    shutil.rmtree(cand.dir)
    proc = subprocess.run(
        [pool.find_worker_python(), str(project / adopted["script"])],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "[2, 8, 18]" in proc.stdout


def test_actual_cache_paths_rejected_but_same_named_project_directory_allowed(project):
    cand = candidate(project)
    cand.target.write_text(
        f"import matplotlib.pyplot as plt\np = {str(cand.dir / 'project_context' / 'assets' / 'values.csv')!r}\n"
    )
    assert ai_convert.agent_check(cand)[1].startswith("transient_resource_path")
    # A project is allowed to have a real directory with the same generic name.
    cand.target.write_text("import matplotlib.pyplot as plt\np = 'project_context/values.csv'\n")
    cand.extras["runner"] = lambda *args: {"stems": ["plot"], "svg": "<svg/>"}
    assert ai_convert.agent_check(cand)[0]


@pytest.mark.parametrize("replacement", ["file_link", "directory_link", "fifo"])
def test_resource_swap_at_open_never_copies_the_replacement(
    project, tmp_path, monkeypatch, replacement
):
    path = project / "assets" / "values.csv"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "values.csv").write_text("confidential external bytes")
    scope = convertcontext.readscope.ReadScope(home=Path.home(), project_root=project)
    actual_open = os.open
    replaced = False

    def swap(name, flags, *args, **kwargs):
        nonlocal replaced
        if Path(name) == path and not replaced:
            replaced = True
            try:
                if replacement == "directory_link":
                    (project / "assets").rename(project / "original_assets")
                    (project / "assets").symlink_to(outside, target_is_directory=True)
                else:
                    path.unlink()
                    if replacement == "fifo":
                        if not hasattr(os, "mkfifo"):
                            pytest.skip("FIFO is not supported on this platform")
                        os.mkfifo(path)
                    else:
                        path.symlink_to(outside / "values.csv")
            except OSError:
                pytest.skip("link creation unavailable")
        return actual_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap)
    data, reason = convertcontext._read(project, path, scope)
    assert replaced
    assert data is None, "the bytes returned for model context must never come from the replacement"
    assert reason in {"unreadable", "not_local_regular_file", "resource_changed"}


def test_manifest_records_unprovided_dynamic_dependencies(project):
    (project / "plots" / "fig.py").write_text("import os\npath = os.environ['DATA_LOCATION']\n")
    cand = candidate(project)
    assert cand.context["files"] == []
    assert cand.context["coverage"] == "static_partial"
    assert any("Dynamic paths" in note for note in cand.context["limitations"])


@pytest.mark.parametrize("changed_side", ["source", "snapshot"])
def test_helper_text_is_visible_without_email_or_inline_key_and_both_hashes_are_checked(
    project, changed_side
):
    source = (
        "# Author contact: scientist@example.org\n"
        "api_key = 'sk-example-fixture-123456789'\n"
        "def scale(x):\n    return [v * 2 for v in x]\n"
    ).encode()
    original = project / "plots/helper.py"
    original.write_bytes(source)
    cand = candidate(project)
    row = next(r for r in cand.context["files"] if r["path"] == "plots/helper.py")
    copy = cand.dir / "project_context/plots/helper.py"
    view = copy.read_bytes()
    assert b"scientist@example.org" not in view
    assert b"sk-example-fixture-123456789" not in view
    assert row["redacted"] is True
    assert row["sha256"] == hashlib.sha256(source).hexdigest()
    assert row["snapshot_sha256"] == hashlib.sha256(view).hexdigest()
    assert row["sha256"] != row["snapshot_sha256"]
    assert original.read_bytes() == source
    env = tools.RunEnvelope(
        task_type="convert_to_editable",
        cwd=cand.dir,
        target="fig.py",
        output=cand.target_name,
        instruction="convert",
    )
    got = tools.ConvertSession(env).read_file({"path": "project_context/plots/helper.py"})
    assert not got.is_error and "def scale(x):" in got.content
    assert "scientist@example.org" not in got.content
    assert "sk-example-fixture-123456789" not in got.content
    assert convertcontext.unchanged(project, cand.context, snapshot_dir=cand.dir)
    changed = original if changed_side == "source" else copy
    changed.write_bytes(changed.read_bytes() + b"# changed\n")
    assert not convertcontext.unchanged(project, cand.context, snapshot_dir=cand.dir)


@pytest.mark.parametrize("changed_side", ["source", "snapshot"])
def test_resume_drops_invalid_candidate_and_preview_when_context_changed(project, changed_side):
    cand = candidate(project)
    ready(cand)
    (cand.dir / ai_convert.PREVIEW_NAME).write_text("<svg/>")
    ai_convert.stage_installed(cand)
    project_key = str(project.resolve())
    cdir, meta, cancel = ai_convert.prepare_resume(cand.sid, project_key)
    resource = (
        project if changed_side == "source" else cand.dir / convertcontext.DIRECTORY
    ) / "assets/values.csv"
    resource.write_text("2,5,10\n")
    called = []
    result = ai_convert.run_resume(
        cand.sid,
        project_key,
        cdir,
        meta,
        cancel,
        runner=lambda *args: called.append("runner"),
        ensure=lambda *args: called.append("ensure"),
    )
    assert result == {"state": "failed", "reason": "convert_context_changed"}
    assert called == []
    assert not cand.target.exists()
    assert not (cand.dir / ai_convert.PREVIEW_NAME).exists()
    assert (cand.dir / ai_convert.META_NAME).exists()
    assert ai_convert.status(cand.sid, project_key)["state"] == "failed"
    assert not ai_convert.resuming(cand.sid)
