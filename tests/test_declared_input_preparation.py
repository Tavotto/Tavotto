"""计划核声明选择，启动记实际捕获：不把当前设置补签到旧 Figure。"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import sys
from types import SimpleNamespace

import pytest

from support import desktopkit, nativekit
from tavotto.engine import (
    databinding,
    declaredinputs,
    desktopproc,
    execspec,
    executionfact,
    figcapture,
    inputdeclaration,
    launchctx,
    nativesession,
    pool,
    preparation,
    prepsession,
    project_watch,
    receipt,
    runconfig,
    workdir,
)

SCRIPT = "plot.py"
INPUT = "selected.csv"
OTHER = "other.csv"
RC = "rc_0123456789ab"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "private-data"))
    root = tmp_path / "中文项目"
    root.mkdir()
    (root / SCRIPT).write_text("def main():\n    pass\n", encoding="utf-8")
    (root / INPUT).write_bytes(b"1,2\n")
    (root / OTHER).write_bytes(b"3,4\n")
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    monkeypatch.setattr(
        preparation.deprepair,
        "decide_environment_pinned",
        lambda *_a, **_k: pool.EnvironmentDecision(),
    )
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *_a, **_k: (sys.executable, "env_override")
    )
    monkeypatch.setattr(pool, "first_open_outcome", lambda *_a, **_k: None)
    monkeypatch.setattr(pool, "consume_invalidated", lambda *_a, **_k: None)
    monkeypatch.setattr(preparation.envadvice, "recommend", lambda *_a, **_k: None)
    monkeypatch.setattr(preparation.deprepair, "gate", lambda *_a, **_k: None)
    monkeypatch.setattr(preparation.deprepair, "preparation_offer", lambda *_a, **_k: None)
    monkeypatch.setattr(pool, "peek", lambda *_a, **_k: None)
    monkeypatch.setattr(project_watch, "before_acquire", lambda *_a, **_k: None)
    return root


def selection(root, *, files=None, mode="project_root", script=SCRIPT, run=None):
    current = declaredinputs.state(root)
    return declaredinputs.declare(
        root,
        script=script,
        cwd_mode=mode,
        run_config=run,
        files=[INPUT] if files is None else files,
        expected_revision=current["revision"],
    )


def plan(root, *, run=None, execution="safe", entry="main"):
    return preparation.plan_for(
        project_id="fixture-project",
        project_root=str(root),
        asset_id="",
        stem="",
        script=SCRIPT,
        entry=entry,
        original_artifact=None,
        target=preparation.TARGET_SCRIPT,
        run=run,
        execution=execution,
    )


def entry_for(planned):
    return preparation._Entry(planned, preparation.PreparationResult())


def runtime():
    return {
        "runtime_report_version": 1,
        "report_origin": "build",
        "pid": 4242,
        "python_version": "3.13.11",
        "python_implementation": "CPython",
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "platform": "fixture",
        "machine": "fixture",
        "cwd": "fixture",
        "argv0": SCRIPT,
        "packages": {},
        "inputs": {"observation": "partial", "files": [], "local_modules": []},
    }


def captured_worker(root, *, run=None):
    spec = execspec.safe_spec(
        SCRIPT,
        str(root),
        "main",
        interpreter=sys.executable,
        sandbox=str(root.parent / "sandbox"),
        cwd_mode="project_root",
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
    candidate = declaredinputs.payload_for(worker)["input_declaration"]
    capture = inputdeclaration.begin(
        candidate,
        script=SCRIPT,
        script_path=candidate["script_path"],
        cwd_mode="project_root",
        run_config=run.config_id if run else None,
        actual_cwd=spec.cwd,
        project_root=str(root),
        sandbox=spec.sandbox,
    )
    assert capture is not None
    capture.entered(worker.execution_id)
    worker.last_captured_inputs = capture.finish(figcapture.hash_file(root / SCRIPT)[0])
    assert worker.last_captured_inputs is not None
    response = {
        "runtime": worker.last_build_runtime,
        "descriptors": worker.last_build_descriptors,
        "execution_id": worker.execution_id,
    }
    rcpt = receipt.from_worker(
        worker,
        response,
        control_plane=receipt.CONTROL_PLANE_PYTHON,
        grant=workdir.grant_for(root),
        binding=databinding.binding_for(root / SCRIPT, root, "project_root"),
    )
    assert rcpt.captured_inputs is not None
    return worker, response, rcpt


def test_plan_records_absence_privately_without_default_payload_or_fingerprint_change(world):
    planned = plan(world)
    assert planned.input_declaration_state["revision"] is None
    assert planned.input_declaration_state["scope"] == {
        "script": SCRIPT,
        "cwd_mode": "project_root",
        "run_config": None,
    }
    legacy = dataclasses.replace(planned, input_declaration_state=None)
    assert planned.to_payload() == legacy.to_payload()
    assert prepsession._fingerprint(planned) == prepsession._fingerprint(legacy)
    assert "input_declaration" not in prepsession._fingerprint_parts(planned)
    assert preparation.stale_reason(planned) is None
    with pytest.raises(TypeError):
        planned.input_declaration_state["scope"]["script"] = "other.py"


@pytest.mark.parametrize("change", ["add", "replace", "remove"])
def test_changed_selection_is_stale_before_any_runner_code(world, change):
    if change != "add":
        selection(world)
    planned = plan(world)
    if change == "remove":
        declaredinputs.remove(
            world,
            script=SCRIPT,
            cwd_mode="project_root",
            expected_revision=declaredinputs.state(world)["revision"],
        )
    else:
        selection(world, files=[OTHER])
    assert preparation.stale_reason(planned) == (preparation.STALE_INPUT_DECLARATION, {})

    def forbidden(*_a, **_k):
        pytest.fail("stale preparation executed user code")

    entry = entry_for(planned)
    preparation.PreparationService()._execute(entry, forbidden)
    assert entry.result.status == preparation.STATUS_ERROR
    assert entry.result.error["reason"] == preparation.STALE_INPUT_DECLARATION
    assert entry.result.error["executed"] is False


def test_unrelated_script_cwd_and_run_selection_do_not_stale_target(world):
    selection(world)
    planned = plan(world)
    before = planned.input_declaration_state
    selection(world, script="other.py", files=[OTHER])
    selection(world, mode="project", files=[OTHER])
    runconfig.import_portable(world, [{"id": RC, "script": SCRIPT, "argv": ["--value", "2"]}])
    selection(world, run=RC, files=[OTHER])
    assert preparation.stale_reason(planned) is None
    assert plan(world).input_declaration_state == before
    assert prepsession._fingerprint(plan(world)) == prepsession._fingerprint(planned)


def test_matching_declaration_changes_only_its_private_fingerprint(world):
    without = plan(world)
    selection(world)
    first = plan(world)
    assert first.input_declaration_state["revision"]
    assert prepsession._fingerprint(first) != prepsession._fingerprint(without)
    selection(world, files=[OTHER])
    second = plan(world)
    assert first.input_declaration_state != second.input_declaration_state
    assert prepsession._fingerprint(second) != prepsession._fingerprint(first)
    assert first.to_payload()["launch_context"] == second.to_payload()["launch_context"]
    assert "input_declaration_state" not in second.to_payload()


def test_legacy_unobserved_plan_does_not_acquire_a_new_stale_check(world, monkeypatch):
    legacy = dataclasses.replace(plan(world), input_declaration_state=None)

    def forbidden(*_a, **_k):
        pytest.fail("legacy manually constructed plan queried new declaration state")

    monkeypatch.setattr(declaredinputs, "scope_selection", forbidden)
    assert preparation.stale_reason(legacy) is None


def test_declaration_check_does_not_hash_selected_resources(world, monkeypatch):
    selection(world)
    planned = plan(world)

    def forbidden(*_a, **_k):
        pytest.fail("metadata plan check scanned or hashed input resources")

    monkeypatch.setattr(inputdeclaration, "current_public_fact", forbidden)
    monkeypatch.setattr(figcapture, "hash_file", forbidden)
    assert preparation.stale_reason(planned) is None


def test_unreadable_declaration_never_becomes_confirmed_absence(world):
    planned = plan(world)
    path = declaredinputs.store_path(world)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{broken")
    assert preparation.stale_reason(planned) == (preparation.STALE_INPUT_DECLARATION, {})


def test_environment_replan_cannot_absorb_declaration_change(world):
    selection(world)
    planned = plan(world)
    entry = entry_for(planned)
    selection(world, files=[OTHER])
    assert preparation.PreparationService._replan_in_place(entry, planned) is None
    assert entry.plan is planned


def test_retry_detects_declaration_change_and_does_not_run_second_attempt(world):
    selection(world)
    planned = plan(world)
    calls = []

    def retrying(_planned, *, before_retry):
        calls.append("first")
        selection(world, files=[OTHER])
        before_retry()
        calls.append("second")
        pytest.fail("retry ran with a different declaration")

    entry = entry_for(planned)
    preparation.PreparationService()._execute(entry, retrying)
    assert calls == ["first"]
    assert entry.result.error["reason"] == preparation.STALE_INPUT_DECLARATION
    assert entry.result.error["executed"] is True


def test_changed_declaration_during_build_is_not_published_as_the_old_plan(world):
    selection(world)
    planned = plan(world)
    worker, response, actual = captured_worker(world)
    edits = [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 6}]
    worker.user_edits = copy.deepcopy(edits)

    def runner(_planned, **_kwargs):
        selection(world, files=[OTHER])
        return worker, response, True

    entry = entry_for(planned)
    preparation.PreparationService()._execute(entry, runner)
    assert entry.result.error["reason"] == preparation.STALE_INPUT_DECLARATION
    assert entry.result.error["executed"] is True
    assert entry.result.receipt.captured_inputs == actual.captured_inputs
    assert worker.user_edits == edits and worker.built


def test_new_plan_may_reuse_old_hot_figure_without_resigning_capture_or_clearing_edits(
    world, monkeypatch
):
    selection(world)
    old_worker, _response, actual = captured_worker(world)
    old_worker.user_edits = [{"gid": "figure", "prop": "frame", "value": "figsize"}]
    selection(world, files=[OTHER])
    planned = plan(world)
    monkeypatch.setattr(pool, "peek", lambda *_a, **_k: old_worker)
    entry = entry_for(planned)

    def forbidden(*_a, **_k):
        pytest.fail("cached Figure was unnecessarily rebuilt")

    preparation.PreparationService()._execute(entry, forbidden)
    assert entry.result.status == preparation.STATUS_READY
    assert entry.result.receipt.captured_inputs == actual.captured_inputs
    assert old_worker.user_edits == [{"gid": "figure", "prop": "frame", "value": "figsize"}]
    assert "input_declaration" not in entry.result.receipt.to_payload()


def test_success_record_keeps_only_actual_capture_and_never_reads_current_settings(
    world, monkeypatch
):
    selection(world)
    _worker, _response, actual = captured_worker(world)
    selection(world, files=[OTHER])

    def forbidden(*_a, **_k):
        pytest.fail("success record re-signed an old Figure with current declaration")

    monkeypatch.setattr(declaredinputs, "scope_selection", forbidden)
    record = launchctx.build_record(world, SCRIPT, actual)
    assert record["captured_inputs"] == actual.captured_inputs
    assert set(record["captured_inputs"]) == {"version", "revision"}
    assert "input_declaration_state" not in record
    assert str(world) not in str(record["captured_inputs"])


@pytest.mark.parametrize("change", ["data", "selection", "remove"])
def test_launch_record_checks_actual_capture_against_current_authorized_resources(world, change):
    selection(world)
    _worker, _response, actual = captured_worker(world)
    record = launchctx.build_record(world, SCRIPT, actual)
    arguments = dict(
        project_root=world,
        script=SCRIPT,
        python=sys.executable,
        python_source="env_override",
        facets=(launchctx.FACET_CAPTURED_INPUTS,),
    )
    assert launchctx.verify(record, **arguments) is None
    if change == "data":
        (world / INPUT).write_bytes(b"new bytes")
    elif change == "selection":
        selection(world, files=[OTHER])
    else:
        declaredinputs.remove(
            world,
            script=SCRIPT,
            cwd_mode="project_root",
            expected_revision=declaredinputs.state(world)["revision"],
        )
    assert launchctx.verify(record, **arguments) == launchctx.REASON_CAPTURED_INPUTS


def test_captured_record_survives_real_runconfig_success_storage_and_refuses_parameter_reuse_after_change(
    world,
):
    runconfig.import_portable(world, [{"id": RC, "script": SCRIPT, "argv": ["--series", "中文"]}])
    run = runconfig.selection(world, RC, script=SCRIPT)
    selection(world, run=RC)
    _worker, _response, actual = captured_worker(world, run=run)
    assert launchctx.record_success(world, SCRIPT, actual, run=run)
    stored = runconfig.launch_record(world, SCRIPT)
    assert stored["captured_inputs"] == actual.captured_inputs
    assert (
        launchctx.reusable_run_config(
            world, SCRIPT, python=sys.executable, python_source="env_override"
        )
        == RC
    )
    (world / INPUT).write_bytes(b"changed unobserved selected data")
    assert (
        launchctx.reusable_run_config(
            world, SCRIPT, python=sys.executable, python_source="env_override"
        )
        is None
    )


def test_old_record_never_queries_new_capture_or_gains_new_field(world, monkeypatch):
    selection(world)
    _worker, _response, actual = captured_worker(world)
    legacy = dataclasses.replace(actual, captured_inputs=None)

    def forbidden(*_a, **_k):
        pytest.fail("legacy success consulted or acquired current capture evidence")

    monkeypatch.setattr(declaredinputs, "matches_current_public", forbidden)
    record = launchctx.build_record(world, SCRIPT, legacy)
    assert "captured_inputs" not in record
    assert (
        launchctx.verify(
            record,
            project_root=world,
            script=SCRIPT,
            python=sys.executable,
            python_source="env_override",
        )
        is None
    )


def test_native_basis_uses_actual_result_receipt_only_and_keeps_legacy_default(world):
    selection(world)
    _worker, _response, actual = captured_worker(world)
    native = plan(world, execution="native")
    assert (
        launchctx.basis_of(SimpleNamespace(receipt=actual), native, False).captured_inputs is None
    )
    actual = dataclasses.replace(actual, profile="native", control_plane="native")
    basis = launchctx.basis_of(
        SimpleNamespace(receipt=actual),
        native,
        False,
        native_inputs={"files": [], "local_modules": []},
    )
    assert basis.captured_inputs == actual.captured_inputs
    record = launchctx.build_native_record(
        world, SCRIPT, native, inputs=basis.inputs, captured_inputs=basis.captured_inputs
    )
    assert record["captured_inputs"] == actual.captured_inputs
    legacy = launchctx.basis_of(SimpleNamespace(receipt=None), native, False)
    assert legacy.captured_inputs is None
    assert "captured_inputs" not in launchctx.build_native_record(world, SCRIPT, native)
    assert launchctx.basis_of(SimpleNamespace(receipt=actual), native, True) is None


def test_native_scope_uses_actual_desktop_directory_mapping(world):
    # The user's sandbox selection becomes the explicit script-parent cwd for desktop native.
    workdir.set_mode(world, workdir.MODE_SANDBOX)
    selection(world, mode="project")
    native = plan(world, execution="native")
    assert native.input_declaration_state["scope"]["cwd_mode"] == "project"
    assert native.input_declaration_state["revision"] is not None
    assert preparation.stale_reason(native) is None


@nativekit.needs_user_python
def test_real_safe_execution_uses_one_capture_and_success_record_without_rebuilding_old_figure(
    world, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *_a, **_k: (nativekit.USER_PYTHON, "env_override")
    )
    (world / SCRIPT).write_text(
        "from pathlib import Path\n"
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    counter_name = ''.join(['execution-', 'count.txt'])\n"
        "    with open(counter_name, 'a', encoding='utf-8') as counter:\n"
        "        counter.write('run\\n')\n"
        "    data = Path(''.join(['selected', '.csv'])).read_text()\n"
        "    fig, ax = plt.subplots(figsize=(1, 1))\n"
        "    ax.plot([0, 1], [len(data), len(data)])\n",
        encoding="utf-8",
    )
    selection(world)
    planned = plan(world)
    actual_worker = pool.EngineWorker(
        SCRIPT, str(world), "main", base_dir=tmp_path / "one-real-worker"
    )
    try:
        entry = entry_for(planned)

        def runner(_planned, **_kwargs):
            return actual_worker, actual_worker.ensure_built(), True

        preparation.PreparationService()._execute(entry, runner)
        assert entry.result.status == preparation.STATUS_READY, entry.result.to_payload()
        captured = entry.result.receipt.captured_inputs
        assert captured is not None
        assert launchctx.record_success(world, SCRIPT, entry.result.receipt)
        stored = runconfig.launch_record(world, SCRIPT)
        assert stored["captured_inputs"] == captured
        assert (
            launchctx.verify(
                stored,
                project_root=world,
                script=SCRIPT,
                python=actual_worker.python,
                python_source=actual_worker.python_source,
            )
            is None
        )
        selection(world, files=[OTHER])
        monkeypatch.setattr(pool, "peek", lambda *_a, **_k: actual_worker)
        reused = entry_for(plan(world))

        def forbidden(*_a, **_k):
            pytest.fail("new declaration ran an already built Figure again")

        preparation.PreparationService()._execute(reused, forbidden)
        assert reused.result.status == preparation.STATUS_READY
        assert reused.result.receipt.captured_inputs == captured
        assert (world / "execution-count.txt").read_text(encoding="utf-8") == "run\n"
        assert (
            launchctx.verify(
                stored,
                project_root=world,
                script=SCRIPT,
                python=actual_worker.python,
                python_source=actual_worker.python_source,
                facets=(launchctx.FACET_CAPTURED_INPUTS,),
            )
            == launchctx.REASON_CAPTURED_INPUTS
        )
    finally:
        actual_worker.force_kill()


@nativekit.needs_user_python
def test_real_native_preparation_result_keeps_actual_capture_in_success_record(world, monkeypatch):
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *_a, **_k: (nativekit.USER_PYTHON, "env_override")
    )
    (world / SCRIPT).write_text(
        desktopkit.FIGURE_HEADER
        + "from pathlib import Path\n"
        + "counter_name = ''.join(['native-', 'count.txt'])\n"
        + "with open(counter_name, 'a', encoding='utf-8') as counter:\n"
        + "    counter.write('run\\n')\n"
        + "data = Path(''.join(['selected', '.csv'])).read_text()\n"
        + "plt.plot([0, 1], [len(data), len(data)])\n"
        + "plt.show()\n",
        encoding="utf-8",
    )
    selection(world)
    planned = plan(world, execution="native", entry=None)
    sessions = []

    def native_runner(actual_plan, **_kwargs):
        session = desktopproc.start(
            desktopproc.OwnedLaunch(
                actual_plan.native_spec,
                policy=desktopkit.FAST,
                input_declaration_state=actual_plan.input_declaration_state,
            )
        )
        sessions.append(session)
        nativekit.wait_state(session, [nativesession.BARRIER])
        return session, session.ensure_built(), True

    try:
        entry = entry_for(planned)
        service = preparation.PreparationService()
        service._execute(entry, native_runner)
        assert entry.result.status == preparation.STATUS_READY, entry.result.to_payload()
        session = sessions[0]
        actual = entry.result.receipt
        assert actual.profile == "native" and actual.captured_inputs is not None
        direct = receipt.from_native_session(session, SCRIPT)
        assert actual.captured_inputs == direct.captured_inputs
        basis = launchctx.basis_of(
            entry.result, planned, False, native_inputs=session.observed_inputs
        )
        assert basis.captured_inputs == actual.captured_inputs
        assert launchctx.record_success(world, SCRIPT, basis)
        record = runconfig.launch_record(world, SCRIPT)
        assert record["captured_inputs"] == actual.captured_inputs
        arguments = dict(
            project_root=world,
            script=SCRIPT,
            python=nativekit.USER_PYTHON,
            python_source=None,
            facets=(launchctx.FACET_CAPTURED_INPUTS,),
        )
        assert launchctx.verify(record, **arguments) is None
        assert (world / "native-count.txt").read_text(encoding="utf-8") == "run\n"
        (world / INPUT).write_bytes(b"changed after actual native capture")
        assert launchctx.verify(record, **arguments) == launchctx.REASON_CAPTURED_INPUTS
        assert session.captured_inputs_for_receipt(script=SCRIPT) is not None
        desktopkit.finish(session)
    finally:
        for session in sessions:
            if not session.owned.finished:
                session.cancel_owned()
