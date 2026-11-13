"""固定 chart command 的真实子进程、环境占用与有界 I/O 合同。"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from support import procprobe
from tavotto.engine import chartprocess, desktopproc, envlease, launcher, pool
from tavotto.engine.chartprofile import NATIVE_PROFILE

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate(), "barrier did not arrive"


@pytest.fixture
def child(tmp_path, monkeypatch):
    """Each real child uses this checkout and isolated configuration/startup hook."""
    assert chartprocess.active_count() == 0
    hook = tmp_path / "startup"
    hook.mkdir()
    marker, release = tmp_path / "started", tmp_path / "release"
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(hook), str(ROOT / "src")]))
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda _directory: (PYTHON, pool.SOURCE_CURRENT)
    )
    monkeypatch.setattr(pool, "max_alive", lambda: 2)
    monkeypatch.setattr(pool, "_overlay_for", lambda *_args: None)
    monkeypatch.setattr(
        chartprocess,
        "_POLICY",
        dataclasses.replace(chartprocess._POLICY, term_grace=0.2, kill_timeout=1, linger_grace=0.1),
    )
    owned, spawned = [], []
    original_own, original_popen = desktopproc.own_process, subprocess.Popen

    def own(*args, **kwargs):
        owner = original_own(*args, **kwargs)
        owned.append(owner)
        return owner

    def popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        spawned.append((proc, args[0], kwargs))
        return proc

    monkeypatch.setattr(desktopproc, "own_process", own)
    monkeypatch.setattr(subprocess, "Popen", popen)
    threads = []

    def request(payload=None, timeout=20):
        return chartprocess.run_request(tmp_path, payload or {"probe": True}, timeout=timeout)

    def background(payload=None, timeout=20):
        box = {}

        def go():
            try:
                box["value"] = request(payload, timeout)
            except BaseException as exc:
                box["error"] = exc

        thread = threading.Thread(target=go)
        threads.append(thread)
        thread.start()
        return thread, box

    def startup(code):
        (hook / "sitecustomize.py").write_text(code, encoding="utf-8")

    barrier = (
        "from pathlib import Path\nimport time\n"
        f"Path({str(marker)!r}).touch()\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
    )
    kit = SimpleNamespace(
        request=request,
        background=background,
        startup=startup,
        barrier=barrier,
        marker=marker,
        release=release,
        owned=owned,
        spawned=spawned,
        path=tmp_path,
    )
    yield kit
    release.touch()
    for owner in owned:
        if not owner.finished:
            owner.cancel()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    wait_until(lambda: chartprocess.active_count() == 0)
    assert not envlease.native_sessions_on(PYTHON)
    for proc, _, _ in spawned:
        assert proc.poll() is not None


def native_payload():
    from test_chart_native import setup_chart

    _, model, _, frame = setup_chart()
    return {"model": model, "frame": frame, "render_profile": NATIVE_PROFILE, "patches": []}


def test_real_probe_and_native_render_return_complete_payload_and_close_resources(child):
    assert child.request()["engine"]["matplotlib"]
    result = child.request(native_payload())
    fragment = base64.b64decode(result["fragment_b64"], validate=True)
    assert b'id="axes_0.lines_0"' in fragment
    assert result["manifest"]["elements"] and result["roles"]
    assert result["coordinates"]["kind"] == "scene-viewport-figure-fraction"
    assert result["coordinates"]["scene_size_pt"] == [100, 100]
    assert child.owned[-1].exit_code == 0
    assert child.owned[-1].confirmed
    assert chartprocess.active_count() == 0
    assert envlease.state_of(PYTHON) == envlease.STATE_IDLE
    proc, argv, kwargs = child.spawned[-1]
    assert argv == [PYTHON, "-B", "-m", "tavotto.engine.svgchart"]
    assert kwargs["start_new_session"] == (os.name != "nt")
    assert kwargs["cwd"] == str(ROOT / "src")
    assert all(stream.closed for stream in (proc.stdin, proc.stdout, proc.stderr))
    assert not list((child.path / "data/cache/chart-requests").iterdir())


def test_parent_import_does_not_load_scientific_or_native_dependencies(tmp_path):
    code = (
        "import importlib.abc,sys\n"
        f"sys.path.insert(0,{str(ROOT / 'src')!r})\n"
        "class Deny(importlib.abc.MetaPathFinder):\n"
        " def find_spec(self,fullname,*args):\n"
        "  if fullname.split('.')[0] in {'matplotlib','numpy','scipy','PIL','pymupdf','fitz','cairocffi'}:"
        " raise AssertionError(fullname)\n"
        "sys.meta_path.insert(0,Deny())\n"
        "from tavotto.engine import chartprocess\n"
        "assert chartprocess.active_count()==0\n"
    )
    result = subprocess.run(
        [PYTHON, "-B", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_mutating_environment_refuses_before_spawning_any_chart_code(child):
    with envlease.mutating("chart-test-install", PYTHON):
        with pytest.raises(pool.WorkerError) as error:
            child.request()
        assert error.value.code == envlease.ENVIRONMENT_MUTATING
        assert child.spawned == []
    assert chartprocess.active_count() == 0


def test_live_chart_lease_blocks_reverse_mutation_until_owner_confirms(child):
    child.startup(child.barrier)
    thread, box = child.background()
    wait_until(child.marker.exists)
    assert chartprocess.active_count() == 1
    assert envlease.state_of(PYTHON) == envlease.STATE_NATIVE_SESSIONS
    with pytest.raises(envlease.EnvironmentBusy) as error:
        with envlease.mutating("chart-test-reverse", PYTHON):
            pytest.fail("mutating entered while the actual chart child was alive")
    assert error.value.code == envlease.ENVIRONMENT_IN_USE_BY_NATIVE
    assert child.spawned[0][0].poll() is None
    child.release.touch()
    thread.join(20)
    assert not thread.is_alive() and "error" not in box
    assert envlease.state_of(PYTHON) == envlease.STATE_IDLE


def test_chart_admission_is_global_dynamic_and_released_after_response(child, monkeypatch):
    monkeypatch.setattr(pool, "max_alive", lambda: 1)
    child.startup(child.barrier)
    thread, box = child.background()
    wait_until(child.marker.exists)
    with pytest.raises(pool.WorkerError) as error:
        chartprocess.run_request(child.path / "other-project", {"probe": True}, timeout=10)
    assert error.value.code == "chart_engine_unavailable"
    assert error.value.extra == {"reason": "capacity"}
    assert len(child.spawned) == 1
    # The same authority changing at runtime admits another chart task; this is
    # a chart-class limit, not a claim about resident/native process totals.
    monkeypatch.setattr(pool, "max_alive", lambda: 2)
    other, second = child.background()
    wait_until(lambda: len(child.spawned) == 2)
    assert chartprocess.active_count() == 2
    child.release.touch()
    for running in (thread, other):
        running.join(20)
        assert not running.is_alive()
    assert "error" not in box and "error" not in second
    assert child.request()["ok"] is True


def test_worker_launch_uses_existing_environment_authorities(child, monkeypatch):
    calls = []
    original = pool.worker_env

    def worker_env(python, source):
        calls.append(("worker_env", python, source))
        env = dict(original(python, source) or os.environ)
        env["CHART_WORKER_ENV_CANARY"] = "worker"
        return env

    def launch_env(lau, python, env, overlay, *, bundled):
        calls.append(("launch_env", python, overlay, bundled))
        assert env["CHART_WORKER_ENV_CANARY"] == "worker"
        return {**env, "CHART_ACTIVATION_CANARY": "activated"}

    monkeypatch.setattr(pool, "worker_env", worker_env)
    monkeypatch.setattr(pool, "launch_env", launch_env)
    monkeypatch.setattr(pool, "_overlay_for", lambda *_args: {"canary": "overlay"})
    child.startup(
        "import os\n"
        "assert os.environ['CHART_ACTIVATION_CANARY']=='activated'\n"
        "assert os.environ['CHART_WORKER_ENV_CANARY']=='worker'\n"
    )
    assert child.request()["ok"] is True
    assert calls == [
        ("worker_env", PYTHON, pool.SOURCE_CURRENT),
        ("launch_env", PYTHON, {"canary": "overlay"}, False),
    ]


@pytest.mark.parametrize("failure", ["activation", "popen", "owner"])
def test_spawn_failures_release_admission_and_lease_without_fallback(child, monkeypatch, failure):
    def fail(*_args, **_kwargs):
        if failure == "activation":
            raise launcher.LauncherUnavailable("test activation failed")
        raise OSError("test spawn/attach failed")

    if failure == "activation":
        monkeypatch.setattr(pool, "launch_env", fail)
    elif failure == "popen":
        monkeypatch.setattr(subprocess, "Popen", fail)
    else:
        monkeypatch.setattr(desktopproc, "_scope_for", fail)
    with pytest.raises(pool.WorkerError) as error:
        child.request()
    assert error.value.code == (
        "conda_launcher_unavailable" if failure == "activation" else "chart_engine_unavailable"
    )
    assert chartprocess.active_count() == 0
    assert envlease.state_of(PYTHON) == envlease.STATE_IDLE
    if failure != "owner":
        assert not child.spawned


def test_nonzero_exit_is_rejected_even_after_valid_engine_json(child):
    child.startup(
        "import atexit,os,sys\natexit.register(lambda:(sys.stdout.flush(),os._exit(37)))\n"
    )
    with pytest.raises(pool.WorkerError) as error:
        child.request()
    assert error.value.code == "chart_engine_unavailable"
    assert child.owned[0].exit_code == 37
    assert json.loads(child.owned[0].stdout.text())["ok"] is True


def test_complete_response_larger_than_tail_and_large_stderr_are_both_drained(child):
    child.startup("import sys\nsys.stderr.write('e'*(2*1024*1024));sys.stderr.flush()\n")
    payload = native_payload()
    # This is real rendered/chart metadata, not synthetic protocol padding.
    payload["patches"] = [
        {"gid": "axes_0.xlabel", "prop": "text", "value": "measured label " * 1200}
    ]
    result = child.request(payload)
    assert result["ok"] is True
    owner = child.owned[0]
    assert owner.stdout.total > owner.policy.tail_bytes
    assert len(owner.stdout.text().encode()) <= owner.policy.tail_bytes
    assert owner.stderr.total >= 2 * 1024 * 1024  # matplotlib may add its own diagnostics
    assert len(owner.stderr.text().encode()) == owner.policy.tail_bytes
    assert result["manifest"]["elements"]
    assert base64.b64decode(result["fragment_b64"]).endswith(b"</svg>\n")


def test_stdout_overflow_is_signalled_by_pump_and_cancelled_by_request_owner(child, monkeypatch):
    monkeypatch.setattr(chartprocess, "MAX_STDOUT_BYTES", 1024)
    child.startup(
        "import sys,time\nsys.stdout.write('x'*65536);sys.stdout.flush();time.sleep(30)\n"
    )
    cancel_threads = []
    original = desktopproc.DesktopProcess.cancel

    def cancel(owner):
        cancel_threads.append(threading.current_thread().name)
        return original(owner)

    monkeypatch.setattr(desktopproc.DesktopProcess, "cancel", cancel)
    started = time.monotonic()
    with pytest.raises(pool.WorkerError) as error:
        child.request(timeout=10)
    assert error.value.code == "svg_resource_limit"
    assert time.monotonic() - started < 5
    assert cancel_threads == [threading.current_thread().name]
    assert child.owned[0].confirmed


@pytest.mark.parametrize("probe", [False, True])
def test_timeout_includes_blocked_stdin_and_reaps_child(child, probe):
    child.startup("import time\ntime.sleep(30)\n")
    started = time.monotonic()
    with pytest.raises(pool.WorkerError) as error:
        child.request({"probe": probe, "padding": "x" * 2**20}, timeout=0.25)
    assert error.value.code == ("chart_engine_unavailable" if probe else "svg_scaffold_timeout")
    assert time.monotonic() - started < 5
    assert child.spawned[0][0].poll() is not None
    assert child.owned[0].confirmed
    assert envlease.state_of(PYTHON) == envlease.STATE_IDLE


def test_timeout_cleans_descendant_holding_protocol_pipe_without_killing_unrelated(child):
    grandchild_file = child.path / "grandchild"
    child.startup(
        "import os,subprocess,sys,time\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)'],"
        "stdin=subprocess.DEVNULL,creationflags=0x08000000 if os.name=='nt' else 0)\n"
        f"Path({str(grandchild_file)!r}).write_text(str(p.pid))\n"
        "time.sleep(30)\n"
    )
    # Deliberately not created through the fixture's observed/chart spawn path.
    unrelated = subprocess.Popen(
        [PYTHON, "-I", "-c", "import time;time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=chartprocess.runtime.CREATE_NO_WINDOW,
    )
    child.spawned.pop()  # unrelated is explicitly owned/cleaned in this test
    try:
        started = time.monotonic()
        with pytest.raises(pool.WorkerError) as error:
            child.request({"probe": True}, timeout=0.5)
        assert error.value.code == "chart_engine_unavailable"
        assert grandchild_file.exists()
        assert time.monotonic() - started < 5
        owner = child.owned[0]
        assert owner.scope_kind in (desktopproc.SCOPE_JOB, desktopproc.SCOPE_GROUP)
        assert owner.confirmed and owner.scope.empty()
        assert not procprobe.alive(int(grandchild_file.read_text()))
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


def test_unconfirmed_owner_retains_environment_and_capacity_until_real_confirmation(
    child, monkeypatch
):
    pending = {}
    proc = SimpleNamespace(stdin=None, stdout=None, stderr=None, poll=lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: proc)

    def own(_proc, _policy, *, on_exit, **_kwargs):
        pending["finish"] = on_exit
        owner = SimpleNamespace(
            start=lambda: None,
            write_stdin=lambda _data: True,
            close_stdin=lambda: None,
            cancel=lambda: SimpleNamespace(confirmed=False),
            exit_code=None,
        )
        return owner

    monkeypatch.setattr(desktopproc, "own_process", own)
    try:
        with pytest.raises(pool.WorkerError) as error:
            child.request(timeout=0.05)
        assert error.value.code == "chart_engine_unavailable"
        assert chartprocess.active_count() == 1
        assert envlease.state_of(PYTHON) == envlease.STATE_NATIVE_SESSIONS
        with pytest.raises(envlease.EnvironmentBusy):
            with envlease.mutating("chart-test-unknown-close", PYTHON):
                pytest.fail("unknown scope must remain occupied")
    finally:
        pending["finish"](0, cancelled=True)
    assert chartprocess.active_count() == 0
    assert envlease.state_of(PYTHON) == envlease.STATE_IDLE


@pytest.mark.parametrize(
    "raw,probe",
    [
        (b"not json", True),
        (b"[]", True),
        (b'{"ok":1}', True),
        (b'{"ok":true,"engine":{"matplotlib":"m","numpy":"n","tavotto":NaN}}', True),
        (b'{"ok":true,"engine":{}}', True),
        (b'{"ok":true,"engine":{"matplotlib":"m","numpy":"n","tavotto":"t"}}', False),
        (
            b'{"ok":true,"engine":{"matplotlib":"m","numpy":"n","tavotto":"t"},'
            b'"fragment_b64":"???","facts":{}}',
            False,
        ),
        (
            b'{"ok":true,"engine":{"matplotlib":"m","numpy":"n","tavotto":"t"},'
            b'"fragment_b64":"PHN2Zy8+","facts":[]}',
            False,
        ),
    ],
)
def test_malformed_response_never_becomes_success(raw, probe):
    with pytest.raises(pool.WorkerError) as error:
        chartprocess._response(raw, probe)
    assert error.value.code == "svg_scaffold_invalid"


def test_worker_error_envelope_is_preserved_for_app_semantic_handling(child):
    result = child.request({"model": {}, "frame": {}})
    assert result["ok"] is False
    assert result["code"] and result["error"]
    assert child.owned[0].exit_code == 0


def test_chart_admission_covers_complete_response_validation(child, monkeypatch):
    original = chartprocess._response

    def response(raw, probe):
        assert chartprocess.active_count() == 1
        assert child.owned[0].confirmed  # interpreter is no longer in use
        assert envlease.state_of(PYTHON) == envlease.STATE_IDLE
        return original(raw, probe)

    monkeypatch.setattr(chartprocess, "_response", response)
    assert child.request()["ok"] is True
    assert chartprocess.active_count() == 0


@pytest.mark.parametrize(
    "payload,timeout", [([], 1), ({}, 0), ({}, float("inf")), ({"x": float("nan")}, 1)]
)
def test_invalid_request_never_admits_or_spawns(child, payload, timeout):
    with pytest.raises(pool.WorkerError) as error:
        chartprocess.run_request(child.path, payload, timeout=timeout)
    assert error.value.code == "svg_scaffold_invalid"
    assert not child.spawned and chartprocess.active_count() == 0


def test_existing_json_and_svg_budgets_reject_before_launch(child, monkeypatch):
    monkeypatch.setattr(chartprocess, "MAX_REQUEST_BYTES", 10)
    with pytest.raises(pool.WorkerError) as error:
        child.request({"text": "this exceeds the injected byte budget"})
    assert error.value.code == "svg_resource_limit"
    assert not child.spawned


def test_response_uses_existing_structure_and_svg_byte_limits(monkeypatch):
    engine = {"matplotlib": "m", "numpy": "n", "tavotto": "t"}
    raw = {"ok": True, "engine": engine, "fragment_b64": "PHN2Zy8+", "facts": {}}
    monkeypatch.setattr(chartprocess, "MAX_FRAGMENT_BYTES", 4)
    with pytest.raises(pool.WorkerError) as error:
        chartprocess._response(json.dumps(raw).encode(), False)
    assert error.value.code == "svg_resource_limit"
    nested = 0
    for _ in range(chartprocess.chartprofile.MAX_JSON_DEPTH + 1):
        nested = [nested]
    with pytest.raises(pool.WorkerError) as error:
        chartprocess._response(json.dumps({**raw, "nested": nested}).encode(), True)
    assert error.value.code == "svg_resource_limit"


def test_owner_attachment_does_not_start_threads_before_callers_state_is_ready(monkeypatch):
    scope = SimpleNamespace(kind="test")
    proc = SimpleNamespace(pid=42, stdin=None)
    monkeypatch.setattr(desktopproc, "_scope_for", lambda value: scope)
    owner = desktopproc.own_process(proc, desktopproc.OwnedPolicy(), gated=True)
    assert owner.scope is scope and owner.proc is proc
    assert not owner._pumps and owner._waiter is None
    assert not owner._armed.is_set()


def test_owner_attach_failure_kills_and_reaps_before_raising(monkeypatch):
    calls = []

    def broken(_proc):
        raise OSError("attach failed")

    monkeypatch.setattr(desktopproc, "_scope_for", broken)
    proc = SimpleNamespace(kill=lambda: calls.append("kill"), wait=lambda: calls.append("wait"))
    with pytest.raises(OSError, match="attach failed"):
        desktopproc.own_process(proc, desktopproc.OwnedPolicy())
    assert calls == ["kill", "wait"]
