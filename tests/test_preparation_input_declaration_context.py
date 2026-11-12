"""准备报告只投影冻结选择及其实际封存回执，不给旧运行补签当前声明。"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import (
    declaredinputs,
    execspec,
    executionfact,
    figcapture,
    inputdeclaration,
    inputremap,
    preparation,
    prepsession,
    receipt,
    runconfig,
    workdir,
)
from test_declared_input_preparation import (
    INPUT,
    OTHER,
    RC,
    SCRIPT,
    runtime,
    selection,
    world as world,
)


def _session(root, *, run=None, execution="safe", script=SCRIPT):
    service = prepsession.SessionService(preparation.PreparationService())
    session, _ = service.check(
        project_id="fixture-project",
        project_root=str(root),
        target={
            "kind": preparation.TARGET_SCRIPT if script else preparation.TARGET_ASSET,
            "script": script,
            "entry": "main" if script else None,
            "asset_id": "" if script else "figure.svg",
            "execution": execution,
            **({"run": run} if run is not None else {}),
        },
    )
    return service, session


def _sealed(root, *, run=None, rules=()):
    """Use the real declaration, capture writer and receipt consumer on small real files."""
    spec = execspec.safe_spec(
        SCRIPT,
        str(root),
        "main",
        interpreter=sys.executable,
        sandbox=str(root.parent / "sandbox"),
        cwd_mode="project_root",
        input_remap=rules,
        stem_aliases={},
        **execspec.run_kwargs(run),
    )
    worker = SimpleNamespace(
        spec=spec,
        script_name=SCRIPT,
        figures_dir=str(root),
        entry="main",
        run=run,
        python=sys.executable,
        python_source="env_override",
        generation=1,
        built=True,
        script_sha1=hashlib.sha1((root / SCRIPT).read_bytes()).hexdigest(),
        child_pid=4242,
        execution_id=executionfact.new_id(),
        last_build_descriptors=[{"stem": "F"}],
        last_build_runtime=runtime(),
    )
    response = {
        "runtime": worker.last_build_runtime,
        "descriptors": worker.last_build_descriptors,
        "execution_id": worker.execution_id,
    }
    candidate = declaredinputs.payload_for(worker)["input_declaration"]
    capture = inputdeclaration.begin(
        candidate,
        script=SCRIPT,
        script_path=candidate["script_path"],
        cwd_mode="project_root",
        run_config=run.config_id if run else None,
        remap_rules=worker.spec.input_remap,
        actual_cwd=worker.spec.cwd,
        project_root=str(root),
        sandbox=worker.spec.sandbox,
    )
    assert capture is not None
    capture.entered(worker.execution_id)
    worker.out_dir = root.parent / "controlled-cache"
    worker.out_dir.mkdir()
    worker.last_captured_inputs = capture.finish(
        figcapture.hash_file(root / SCRIPT)[0],
        archive_root=worker.out_dir / inputdeclaration.ARCHIVE_DIRNAME,
    )
    worker.last_captured_input_archive = capture.archive_descriptor()
    assert worker.last_captured_inputs is not None
    assert worker.last_captured_input_archive is not None
    actual = receipt.from_worker(
        worker, response, control_plane=receipt.CONTROL_PLANE_PYTHON, grant=None
    )
    assert actual.captured_inputs is not None
    assert actual.captured_input_archive is not None
    return actual


def _attempt(service, session, actual, *, planned=None, status=preparation.STATUS_READY):
    planned = planned or session.plan
    result = service._prep.register(planned)
    result.receipt = actual
    result.captured = {"stems": ["F"], "descriptors": [{"stem": "F"}]}
    result.status = status
    session.attempts.append(
        prepsession._Attempt(
            planned.plan_id,
            "test-run",
            session.config_revision,
            planned.created_at,
            finalized=True,
            finalize={"registered": True},
        )
    )
    return result


def _context(service, session):
    return service.report(session, include_input_declaration=True)["input_declaration"]


@pytest.mark.parametrize("declared", [False, True])
def test_opt_in_reports_frozen_no_argument_scope_and_leaves_default_wire_unchanged(
    world, monkeypatch, declared
):
    if declared:
        selection(world)
    service, session = _session(world)
    frozen = session.plan.input_declaration_state
    default = service.report(session)
    context_report = service.report(session, include_input_declaration=True)
    assert context_report.pop("input_declaration") == {
        "version": 1,
        "scope": {"script": SCRIPT, "cwd_mode": "project_root", "run_config": None},
        "selection_revision": frozen["revision"],
        "input_remap_generation": session.plan.input_remap_generation,
        "unavailable_reason": None,
        "captured_inputs": None,
    }
    assert context_report == default
    assert bool(frozen["revision"]) is declared

    def forbidden(*_a, **_k):
        pytest.fail("Default report must not invoke the optional declaration projection")

    monkeypatch.setattr(prepsession, "_input_declaration_context", forbidden)
    assert service.report(session) == default


def test_parameter_scope_uses_original_run_reference_without_registering_another(world):
    runconfig.import_portable(world, [{"id": RC, "script": SCRIPT, "argv": ["--series", "中文"]}])
    run = runconfig.selection(world, RC, script=SCRIPT)
    selection(world, run=RC)
    service, session = _session(world, run=run)
    before = runconfig.store_path(world).read_bytes()
    context = _context(service, session)
    assert context["scope"] == {"script": SCRIPT, "cwd_mode": "project_root", "run_config": RC}
    assert context["selection_revision"] is not None
    assert "--series" not in json.dumps(context, ensure_ascii=False)
    assert runconfig.store_path(world).read_bytes() == before
    plain_service, plain = _session(world)
    assert _context(plain_service, plain)["selection_revision"] is None


def test_native_sandbox_preference_reports_actual_script_directory_scope(world):
    workdir.set_mode(world, workdir.MODE_SANDBOX)
    selection(world, mode="project")
    service, session = _session(world, execution="native")
    assert session.plan.native_spec.cwd_mode == "sandbox"
    context = _context(service, session)
    assert context["scope"]["cwd_mode"] == "project"
    assert context["selection_revision"] is not None


@pytest.mark.parametrize("kind", ["sensitive", "unknown", "legacy", "not_script"])
def test_unavailable_scope_is_explicit_without_inventing_parameter_identity(world, kind):
    run = None
    if kind == "sensitive":
        config = runconfig.put(world, SCRIPT, ["--secret", "private-value"], sensitive=True)
        run = runconfig.selection(world, config.id, script=SCRIPT)
    service, session = _session(world, run=run, script=None if kind == "not_script" else SCRIPT)
    if kind == "unknown":
        unknown = preparation._declaration_selection(world, SCRIPT, "project_root", run=object())
        session.plan = dataclasses.replace(session.plan, input_declaration_state=unknown)
    elif kind == "legacy":
        # Old plans did not observe a declaration scope: absence of that evidence is not no args.
        session.plan = dataclasses.replace(session.plan, input_declaration_state=None)
    assert _context(service, session) == {
        "version": 1,
        "scope": None,
        "selection_revision": None,
        "input_remap_generation": session.plan.input_remap_generation,
        "unavailable_reason": {
            "sensitive": "sensitive_run",
            "unknown": "scope_unknown",
            "legacy": "scope_unknown",
            "not_script": "not_script",
        }[kind],
        "captured_inputs": None,
    }
    assert "private-value" not in json.dumps(service.report(session))


@pytest.mark.parametrize("with_run", [False, True])
def test_actual_ready_archive_proves_matching_selection_and_polling_has_no_input_io(
    world, monkeypatch, with_run
):
    run = None
    if with_run:
        runconfig.import_portable(world, [{"id": RC, "script": SCRIPT, "argv": ["--x", "2"]}])
        run = runconfig.selection(world, RC, script=SCRIPT)
    selection(world, run=RC if with_run else None)
    service, session = _session(world, run=run)
    actual = _sealed(world, run=run)
    _attempt(service, session, actual)
    expected = dict(actual.captured_inputs)
    assert _context(service, session)["captured_inputs"] == expected
    # A current store change and deleted original bytes cannot rewrite the already frozen plan.
    selection(world, files=[OTHER], run=RC if with_run else None)
    (world / INPUT).unlink()
    (world / SCRIPT).unlink()

    def forbidden(*_a, **_k):
        pytest.fail(
            "Report projection must not reload settings, input bytes, graph or run identity"
        )

    for module, names in [
        (declaredinputs, ["state", "scope_selection", "_read", "declare"]),
        (
            inputdeclaration,
            ["validate_graph", "canonical_graph_bytes", "verify_archive", "_snapshot"],
        ),
        (runconfig, ["get", "put"]),
        (inputremap, ["snapshot", "generation"]),
        (preparation, ["plan_for"]),
        (figcapture, ["hash_file"]),
        (os, ["walk"]),
        (Path, ["open"]),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    context = _context(service, session)
    assert context["captured_inputs"] == expected
    context["scope"]["script"] = "mutated.py"
    context["captured_inputs"]["revision"] = "mutated"
    again = _context(service, session)
    assert again["scope"]["script"] == SCRIPT
    assert again["captured_inputs"] == expected


@pytest.mark.parametrize("change", ["files", "cwd", "run", "script"])
def test_old_capture_never_certifies_a_different_current_selection(world, change):
    selection(world)
    service, session = _session(world)
    original_plan = session.plan
    actual = _sealed(world)
    _attempt(service, session, actual)
    if change == "files":
        selection(world, files=[OTHER])
    elif change == "cwd":
        workdir.set_mode(world, workdir.MODE_PROJECT)
        selection(world, mode="project")
    elif change == "run":
        runconfig.import_portable(world, [{"id": RC, "script": SCRIPT, "argv": ["--x", "2"]}])
        selection(world, run=RC)
    else:
        (world / "other.py").write_text("pass\n", encoding="utf-8")
        selection(world, script="other.py")
    target = {**session.target}
    if change == "run":
        target["run"] = runconfig.selection(world, RC, script=SCRIPT)
    elif change == "script":
        target["script"] = "other.py"
    session.plan = service._plan_of(session.project_id, session.project_root, target)
    session.config_revision += 1
    context = _context(service, session)
    assert context["selection_revision"] is not None
    assert session.plan.input_declaration_state != original_plan.input_declaration_state
    assert context["captured_inputs"] is None
    # Historical receipt remains available as history; only certification of the new choice is absent.
    assert service.report(session)["result"]["receipt"]["captured_inputs"] == actual.captured_inputs


def test_same_source_remap_retarget_and_recheck_does_not_certify_old_ready_capture(world):
    first, second = world.parent / "first.csv", world.parent / "second.csv"
    first.write_bytes(b"1,2\n")
    second.write_bytes(b"3,4\n")
    rule = {"kind": "file", "from": "../old.csv", "to": str(first)}
    inputremap.add_rule(world, rule)
    declaredinputs.declare(
        world,
        script=SCRIPT,
        cwd_mode="project_root",
        files=[INPUT],
        remaps=[rule["from"]],
        expected_revision=declaredinputs.state(world)["revision"],
    )
    service, session = _session(world)
    original_plan = session.plan
    original_selection = original_plan.input_declaration_state
    original_revision = session.config_revision
    actual = _sealed(world, rules=[rule])
    result = _attempt(service, session, actual)
    assert _context(service, session)["captured_inputs"] == actual.captured_inputs

    # The current approval table changes, but the declaration still stores the A selection.
    updated = inputremap.add_rule(world, {**rule, "to": str(second)})
    assert updated["generation"] != original_plan.input_remap_generation
    assert updated["rules"][0]["to"] == str(second)
    assert (
        declaredinputs.scope_selection(world, script=SCRIPT, cwd_mode="project_root")
        == original_selection
    )
    assert _context(service, session)["captured_inputs"] == actual.captured_inputs
    assert (
        _context(service, session)["input_remap_generation"] == original_plan.input_remap_generation
    )
    checked, created = service.check(
        project_id=session.project_id,
        project_root=session.project_root,
        target=dict(session.target),
        force=True,
    )
    assert checked is session and not created
    assert session.config_revision == original_revision + 1
    assert session.plan.input_remap_generation == updated["generation"]
    assert session.plan.input_declaration_state == original_selection
    attempt_plan, retained = service._prep.get(original_plan.plan_id, session.project_id)
    assert retained is result
    assert attempt_plan.input_remap_generation == original_plan.input_remap_generation
    assert attempt_plan.input_declaration_state == original_selection
    assert result.status == preparation.STATUS_READY and result.receipt is actual
    # Recheck retains a valid historical result, not proof of the current B mapping.
    assert service.report(session)["result"]["receipt"]["captured_inputs"] == actual.captured_inputs
    assert _context(service, session)["input_remap_generation"] == updated["generation"]
    assert _context(service, session)["captured_inputs"] is None


@pytest.mark.parametrize("missing", ["current", "attempt", "both"])
def test_capture_summary_requires_known_matching_frozen_remap_generations(world, missing):
    selection(world)
    service, session = _session(world)
    actual = _sealed(world)
    attempt_plan = session.plan
    if missing in {"attempt", "both"}:
        attempt_plan = dataclasses.replace(attempt_plan, input_remap_generation=None)
    _attempt(service, session, actual, planned=attempt_plan)
    if missing in {"current", "both"}:
        session.plan = dataclasses.replace(session.plan, input_remap_generation=None)
    context = _context(service, session)
    assert context["input_remap_generation"] == session.plan.input_remap_generation
    assert context["captured_inputs"] is None
    assert service.report(session)["result"]["receipt"]["captured_inputs"] == actual.captured_inputs


@pytest.mark.parametrize("missing", ["selection", "archive", "receipt", "ready"])
def test_capture_summary_requires_existing_selection_private_attachment_and_ready_result(
    world, missing
):
    selection(world)
    service, session = _session(world)
    actual = _sealed(world)
    if missing == "selection":
        declaredinputs.remove(
            world,
            script=SCRIPT,
            cwd_mode="project_root",
            expected_revision=declaredinputs.state(world)["revision"],
        )
        session.plan = service._plan_of(session.project_id, session.project_root, session.target)
        assert session.plan.input_declaration_state["revision"] is None
    elif missing == "archive":
        actual = dataclasses.replace(actual, captured_input_archive=None)
    elif missing == "receipt":
        actual = None
    _attempt(
        service,
        session,
        actual,
        status=preparation.STATUS_CANCELLED if missing == "ready" else preparation.STATUS_READY,
    )
    assert _context(service, session)["captured_inputs"] is None
