"""Native evidence uses approved input bytes at real start/barrier boundaries."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace

import pytest

from support import desktopkit, nativekit
from tavotto.engine import (
    declaredinputs,
    desktopproc,
    execspec,
    executionfact,
    inputdeclaration,
    inputremap,
    nativesession,
    receipt,
    runconfig,
)

SCRIPT = "plots/figure.py"
RC = "rc_0123456789ab"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "local-state"))
    root = tmp_path / "中文项目"
    (root / "plots").mkdir(parents=True)
    (root / SCRIPT).write_bytes(b"# actual source\n")
    (root / "data.csv").write_bytes(b"1,2\n")
    return root


def _declare(root, *, mode="project_root", run_config=None, files=None, remaps=()):
    return declaredinputs.declare(
        root,
        script=SCRIPT,
        cwd_mode=mode,
        run_config=run_config,
        files=["data.csv"] if files is None else files,
        remaps=list(remaps),
        expected_revision=declaredinputs.state(root)["revision"],
    )


def _spec(root, *, mode="project_root", run=None, terminal=False):
    kwargs = {
        "interpreter": nativekit.USER_PYTHON or sys.executable,
        "cwd": execspec.desktop_cwd(root, SCRIPT, mode),
        "project_root": str(root),
        "stem_aliases": (),
    }
    if terminal:
        return execspec.native_spec(str(root / SCRIPT), **kwargs)
    return execspec.desktop_native_spec(str(root / SCRIPT), **kwargs, **execspec.run_kwargs(run))


def _capture(root, *, spec=None, **kwargs):
    spec = spec or _spec(root)
    candidate = declaredinputs.payload_for_native(spec, **kwargs)["input_declaration"]
    capture = inputdeclaration.begin(
        candidate,
        script=SCRIPT,
        script_path=str(root / SCRIPT),
        cwd_mode=candidate["scope"]["cwd_mode"],
        run_config=candidate["scope"]["run_config"],
        actual_cwd=spec.cwd,
        project_root=str(root),
    )
    assert capture is not None
    digest = hashlib.sha256((root / SCRIPT).read_bytes()).hexdigest()
    return capture, digest


def test_absence_and_unknown_arguments_do_not_inspect_resources(project, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No known declaration must not inspect a resource")

    monkeypatch.setattr(declaredinputs, "_candidate", forbidden)
    assert declaredinputs.payload_for_native(_spec(project)) == {}
    _declare(project)
    terminal = replace(_spec(project, terminal=True), argv=("--data", "secret.csv"))
    assert declaredinputs.payload_for_native(terminal) == {}


def test_native_uses_actual_cwd_instead_of_sandbox_placeholder(project):
    _declare(project, mode="sandbox")
    assert declaredinputs.payload_for_native(_spec(project, mode="sandbox")) == {}
    _declare(project, mode="project")
    candidate = declaredinputs.payload_for_native(_spec(project, mode="sandbox"))[
        "input_declaration"
    ]
    assert candidate["scope"]["cwd_mode"] == "project"
    assert candidate["rules"] == candidate["mappings"] == []
    assert declaredinputs.payload_for_native(_spec(project)) == {}


def test_exact_scope_selection_observes_absence_without_invalidating_other_scopes(project):
    before = declaredinputs.scope_selection(
        project, script=SCRIPT, cwd_mode="sandbox", execution="native"
    )
    assert before == {
        "scope": {"script": SCRIPT, "cwd_mode": "project", "run_config": None},
        "revision": None,
    }
    _declare(project, mode="project_root")
    assert (
        declaredinputs.scope_selection(
            project, script=SCRIPT, cwd_mode="sandbox", execution="native"
        )
        == before
    )
    _declare(project, mode="project")
    after = declaredinputs.scope_selection(
        project, script=SCRIPT, cwd_mode="sandbox", execution="native"
    )
    assert after["scope"] == before["scope"]
    assert after["revision"].startswith("sha256:")
    _declare(project, mode="project_root", files=[])
    assert (
        declaredinputs.scope_selection(
            project, script=SCRIPT, cwd_mode="sandbox", execution="native"
        )
        == after
    )


def test_launch_cannot_upgrade_observed_absence_or_replace_selected_declaration(project):
    spec = _spec(project)
    absent = declaredinputs.scope_selection(project, script=SCRIPT, cwd_mode="project_root")
    assert declaredinputs.payload_for_native(spec, expected_selection=absent) == {}
    _declare(project)
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        declaredinputs.payload_for_native(spec, expected_selection=absent)
    selected = declaredinputs.scope_selection(project, script=SCRIPT, cwd_mode="project_root")
    _declare(project, files=[])
    with pytest.raises(declaredinputs.DeclaredInputsConflict):
        declaredinputs.payload_for_native(spec, expected_selection=selected)


def test_known_configuration_keeps_exact_tokens_and_rejects_sensitive_or_changed(project):
    runconfig.import_portable(
        project, [{"id": RC, "script": SCRIPT, "argv": ["--value", "中文", ""]}]
    )
    run = runconfig.selection(project, RC, script=SCRIPT)
    _declare(project, run_config=RC)
    spec = _spec(project, run=run)
    candidate = declaredinputs.payload_for_native(spec, run=run)["input_declaration"]
    assert candidate["scope"]["run_config"] == RC
    assert "argv" not in json.dumps(candidate)
    assert declaredinputs.payload_for_native(replace(spec, argv=("different",))) == {}
    assert declaredinputs.payload_for_native(spec, run=replace(run, sensitive=True)) == {}
    assert declaredinputs.payload_for_native(_spec(project, terminal=True)) == {}


def test_approved_external_prefix_is_evidence_without_applying_native_remaps(project):
    external = project.parent / "external"
    (external / "empty").mkdir(parents=True)
    (external / "a.csv").write_bytes(b"4,5\n")
    inputremap.add_rule(project, {"kind": "prefix", "from": "external-inputs", "to": str(external)})
    _declare(project, remaps=["external-inputs"])
    candidate = declaredinputs.payload_for_native(_spec(project))["input_declaration"]
    assert candidate["prefixes"][0]["path"] == str(external.resolve())
    assert candidate["rules"] == candidate["mappings"] == []
    capture, digest = _capture(project)
    capture.entered(executionfact.new_id())
    fact = capture.finish(digest)
    assert fact["declaration"]["prefixes"][0]["directories"] == ["", "empty"]
    assert fact["declaration"]["prefixes"][0]["members"][0]["suffix"] == "a.csv"


def test_continue_preserves_original_baseline_and_uses_distinct_execution_ids(project):
    _declare(project)
    capture, digest = _capture(project)
    initial = executionfact.new_id()
    capture.entered(initial)
    first = capture.finish(digest)
    continued = executionfact.new_id()
    assert capture.resume(continued, digest)
    second = capture.finish(digest)
    assert first["revision"] == second["revision"]
    assert first["execution_id"] == initial
    assert second["execution_id"] == continued
    (project / "data.csv").write_bytes(b"9,8\n")
    assert not capture.resume(executionfact.new_id(), digest)
    (project / "data.csv").write_bytes(b"1,2\n")
    assert capture.finish(digest) is None
    assert not capture.resume(executionfact.new_id(), digest)


@pytest.mark.parametrize("before_finish", [True, False])
def test_continue_requires_successful_barrier_and_a_new_execution_id(project, before_finish):
    _declare(project)
    capture, digest = _capture(project)
    current = executionfact.new_id()
    capture.entered(current)
    if not before_finish:
        assert capture.finish(digest) is not None
    assert not capture.resume(current if not before_finish else executionfact.new_id(), digest)
    assert capture.finish(digest) is None


def test_current_comparison_cannot_mint_execution_or_requalify_changed_inputs(project):
    _declare(project)
    capture, digest = _capture(project)
    capture.entered(executionfact.new_id())
    fact = capture.finish(digest)
    public = inputdeclaration.public_fact(fact)
    candidate = declaredinputs.payload_for_native(_spec(project))["input_declaration"]
    assert inputdeclaration.current_public_fact(candidate) == public
    assert set(public) == {"version", "revision"}
    kwargs = dict(script=SCRIPT, cwd_mode="project_root", run=None, captured_inputs=public)
    assert declaredinputs.matches_current_public(project, execution="native", **kwargs)
    assert declaredinputs.matches_current_public(project, execution="safe", **kwargs)
    (project / "data.csv").write_bytes(b"9,8\n")
    assert not declaredinputs.matches_current_public(project, execution="native", **kwargs)
    _declare(project, files=[])
    assert not declaredinputs.matches_current_public(project, execution="native", **kwargs)
    assert fact["revision"] == public["revision"]


TWO_SHOWS = (
    desktopkit.FIGURE_HEADER
    + """\
from pathlib import Path
data = Path('data.csv').read_text(encoding='utf-8')
fig, ax = plt.subplots()
ax.plot([0, 1], [0, int(data.split(',')[1])])
plt.show()
ax.set_title('continued')
plt.show()
"""
)


@nativekit.needs_user_python
@pytest.mark.parametrize("change", [False, True])
def test_real_owned_launch_ack_barriers_and_receipts(project, change):
    (project / SCRIPT).write_bytes(TWO_SHOWS.encode("utf-8"))
    _declare(project)
    selection = declaredinputs.scope_selection(project, script=SCRIPT, cwd_mode="project_root")
    session = desktopproc.start(
        desktopproc.OwnedLaunch(
            _spec(project), policy=desktopkit.FAST, input_declaration_state=selection
        )
    )
    try:
        nativekit.wait_state(session, [nativesession.BARRIER])
        session.ensure_built()
        first = receipt.from_native_session(session, SCRIPT)
        assert first.pid_check in (receipt.PID_CHECK_OK, receipt.PID_CHECK_LAUNCHER)
        assert first.captured_inputs is not None
        assert set(first.captured_inputs) == {"version", "revision"}
        assert "data.csv" not in json.dumps(first.public_facts())
        old_epoch, old_id = session.snapshot_epoch, session.execution_id
        if change:
            (project / "data.csv").write_bytes(b"9,8\n")
            session.ensure_built()
            assert (
                receipt.from_native_session(session, SCRIPT).captured_inputs
                == first.captured_inputs
            )
        session.resume()
        nativekit.wait_state(session, [nativesession.BARRIER])
        assert session.snapshot_epoch > old_epoch and session.execution_id != old_id
        session.ensure_built()
        second = receipt.from_native_session(session, SCRIPT)
        if change:
            assert second.captured_inputs is None
        else:
            assert second.captured_inputs == first.captured_inputs
        assert first.receipt_id != second.receipt_id
        desktopkit.finish(session)
    finally:
        if not session.owned.finished:
            session.cancel_owned()


@nativekit.needs_user_python
@pytest.mark.parametrize("argv", [(), ("--unknown",)])
def test_real_terminal_ack_only_qualifies_positively_parameter_free(project, argv):
    (project / SCRIPT).write_bytes(TWO_SHOWS.encode("utf-8"))
    _declare(project)
    with nativekit.product_run(
        nativekit.USER_PYTHON,
        SCRIPT,
        *argv,
        cwd=project,
        tavotto_args=("--project", str(project)),
    ) as (
        session,
        proc,
        _,
    ):
        nativekit.wait_state(session, [nativesession.BARRIER])
        session.ensure_built()
        rcpt = receipt.from_native_session(session, SCRIPT)
        captured = rcpt.captured_inputs
        assert (captured is not None) == (not argv), json.dumps(
            {
                "pid_check": rcpt.pid_check,
                "rejected": rcpt.runtime_rejected,
                "script": session.execution_script,
                "kind": session.target_kind,
                "private_slot_present": session._barrier_captured_inputs is not None,
                "getter_present": session.captured_inputs_for_receipt(script=SCRIPT) is not None,
                "parent_candidate_present": bool(
                    declaredinputs.payload_for_native(_spec(project, terminal=True))
                ),
            }
        )
        code, _stdout, stderr = nativekit.finish(session, proc)
        assert code == 0, stderr
