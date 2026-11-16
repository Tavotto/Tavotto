"""Chart tasks use the real pool and both v1 control planes."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import chartprocess, chartprofile, chartwire, execspec, pool, workerd_client

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


@pytest.fixture(params=["python", "workerd"])
def task(request, tmp_path, monkeypatch):
    pool.shutdown_all(wait=True)
    workerd_client.reset_client()
    monkeypatch.setattr(pool, "_workers", {})
    monkeypatch.setattr(pool, "_pending_retirements", {})
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "cache")
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda directory, **kw: (PYTHON, pool.SOURCE_CURRENT)
    )
    monkeypatch.setattr(pool, "ENVIRONMENT_DECIDERS", [])
    monkeypatch.setattr(pool, "COLD_ACQUIRE_OBSERVER", None)
    monkeypatch.setattr(pool, "MAX_ALIVE", 2)
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    binary = (
        ROOT
        / "workerd"
        / "target"
        / "debug"
        / ("tavotto-workerd.exe" if os.name == "nt" else "tavotto-workerd")
    )
    if request.param == "workerd":
        if not binary.is_file():
            pytest.skip(
                "workerd not built (not-run); build this checkout to exercise this control plane"
            )
        monkeypatch.setenv("TAVOTTO_WORKERD", str(binary))
    else:
        monkeypatch.setenv("TAVOTTO_WORKERD", "0")
    project = tmp_path / "project"
    project.mkdir()
    workers = []

    def get():
        w = pool.acquire_chart(project)
        assert isinstance(
            w, pool.WorkerdWorker if request.param == "workerd" else pool.EngineWorker
        )
        workers.append(w)
        return w

    def run(payload=None, timeout=20):
        get()
        return chartprocess.run_request(
            project, {"probe": True} if payload is None else payload, timeout=timeout
        )

    def fake(behavior, *, after_ping=""):
        script = tmp_path / "worker_stub.py"
        script.write_text(
            "import json,sys,time,os\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line); cmd=r['cmd']\n"
            " if cmd=='shutdown': break\n"
            " if cmd=='chart':\n"
            + "".join("  " + line + "\n" for line in (behavior or "pass").splitlines())
            + " out={k:r[k] for k in ('protocol_version','request_id','worker_generation','render_revision') if k in r}\n"
            " out.update(ok=True,engine={'matplotlib':'m','numpy':'n','tavotto':'t'})\n"
            " print(json.dumps(out),flush=True)\n"
            + (" if cmd=='ping': " + after_ping + "\n" if after_ping else ""),
            encoding="utf-8",
        )
        monkeypatch.setattr(pool, "WORKER_PY", script)

    yield SimpleNamespace(
        run=run,
        get=get,
        fake=fake,
        project=project,
        root=tmp_path,
        workers=workers,
        plane=request.param,
    )
    pool.shutdown_all(wait=True)
    workerd_client.reset_client()
    for w in workers:
        assert not w.alive()


def test_real_probe_reuses_one_pooled_task_without_user_execution(task, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    result = task.run()
    assert set(result) == {"ok", "engine"}
    first = task.get()
    assert task.run() == result
    assert task.get() is first
    assert pool.alive_count() == 1
    assert not first.built and first.execution_id is None
    assert first.spec.target_kind == execspec.TARGET_MODULE
    assert first.spec.target == execspec.CHART_MODULE
    assert first.spec.cwd == str(first.sandbox)
    assert not first.spec.argv and not first.spec.run_config


@pytest.mark.parametrize(
    "profile",
    [chartprofile.LEGACY_PROFILE, chartprofile.NATIVE_PROFILE, chartprofile.COMPLETE_PROFILE],
)
def test_real_render_profiles_and_patches_preserve_results(task, profile):
    from test_chart_native import setup_chart

    _, model, _, frame = setup_chart()
    payload = {"model": model, "frame": frame, "render_profile": profile, "patches": []}
    first = task.run(payload)
    assert first["ok"] and base64.b64decode(first["fragment_b64"]).strip()
    assert first["facts"]
    assert task.run(payload) == first
    assert not task.get().built


def test_task_rejects_build_and_does_not_touch_a_real_script(task):
    script = task.project / "plot.py"
    script.write_text("raise RuntimeError('USER CODE RAN')", encoding="utf-8")
    task.run()
    w = task.get()
    with pytest.raises(pool.WorkerError) as error:
        w.request({"cmd": "build"}) if task.plane == "python" else w._call("build", 20)
    assert error.value.code == "unknown_cmd"
    assert task.run()["ok"]
    assert w.last_build_execution_id is None


def test_chart_and_script_share_capacity_but_not_identity(task):
    task.run()
    chart = task.get()
    script = task.project / "plot.py"
    script.write_text("def main(): pass", encoding="utf-8")
    ordinary = pool.get("plot.py", str(task.project), "main")
    assert ordinary is not chart and ordinary.spec.target_kind == execspec.TARGET_SCRIPT
    assert pool.alive_count() == 2
    other = task.root / "other"
    other.mkdir()
    chart._lease_until = ordinary._lease_until = 0
    chart.last_used = 0
    extra = pool.acquire_chart(other)
    assert extra is not chart
    deadline = time.monotonic() + 10
    while chart.alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not chart.alive()
    assert ordinary.alive() and extra.alive()


def test_environment_change_rebuilds_task(task, monkeypatch):
    task.run()
    first = task.get()
    monkeypatch.setattr(pool, "generation_stale", lambda w: w is first)
    second = task.get()
    assert second is not first
    assert second.generation > first.generation
    assert task.run()["ok"]


def test_protocol_mismatch_retires_the_worker(task):
    task.fake("r['request_id']='wrong'")
    w = task.get()
    with pytest.raises(pool.WorkerError) as error:
        task.run()
    assert error.value.code == "protocol_mismatch"
    assert not w.alive()


def test_transport_limit_stops_an_unterminated_response(task, monkeypatch):
    monkeypatch.setattr(chartwire, "MAX_FRAME_BYTES", 1024)
    task.fake("sys.stdout.write('x'*4096); sys.stdout.flush(); time.sleep(15)")
    w = task.get()
    with pytest.raises(pool.WorkerError) as error:
        task.run(timeout=5)
    assert error.value.code == "svg_resource_limit"
    assert not w.alive()


def test_deadline_covers_a_child_that_stops_reading_stdin(task):
    task.fake("", after_ping="time.sleep(15)")
    w = task.get()
    if task.plane == "python":
        w.request({"cmd": "ping"}, 5)
    started = time.monotonic()
    with pytest.raises(pool.WorkerError) as error:
        task.run({"probe": False, "padding": "x" * 2**20}, timeout=0.25)
    assert error.value.code == "svg_scaffold_timeout"
    assert time.monotonic() - started < 3
    assert not w.alive()


def test_explicit_cancel_retires_a_running_chart(task):
    marker = task.root / "running"
    task.fake(f"open({str(marker)!r},'w').close(); time.sleep(15)")
    w = task.get()
    errors = []

    def run():
        try:
            task.run(timeout=10)
        except pool.WorkerError as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        w.force_kill()
    finally:
        thread.join(15)
    assert not thread.is_alive() and errors and not w.alive()


def test_domain_error_retains_old_result_shape_and_worker_remains_usable(task):
    result = task.run({"model": {}, "frame": {}})
    assert result["ok"] is False and result["code"] and result["error"]
    assert task.run()["ok"]


@pytest.mark.parametrize(
    "payload,timeout", [([], 1), ({}, 0), ({}, float("inf")), ({"x": float("nan")}, 1)]
)
def test_invalid_request_is_rejected_before_pool_acquisition(monkeypatch, payload, timeout):
    monkeypatch.setattr(
        pool, "acquire_chart", lambda *a: pytest.fail("invalid request acquired a worker")
    )
    with pytest.raises(pool.WorkerError) as error:
        chartprocess.run_request(".", payload, timeout=timeout)
    assert error.value.code == "svg_scaffold_invalid"


def test_request_budget_is_checked_before_acquisition(monkeypatch):
    monkeypatch.setattr(chartwire, "MAX_REQUEST_BYTES", 10)
    monkeypatch.setattr(
        pool, "acquire_chart", lambda *a: pytest.fail("oversize request acquired a worker")
    )
    with pytest.raises(pool.WorkerError) as error:
        chartprocess.run_request(".", {"padding": "too long"}, timeout=1)
    assert error.value.code == "svg_resource_limit"


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
    with pytest.raises(chartwire.ChartWireError) as error:
        chartwire.response(raw, probe)
    assert error.value.code == "svg_scaffold_invalid"


def test_chart_import_keeps_scientific_modules_out_of_the_parent():
    result = subprocess.run(
        [
            PYTHON,
            "-c",
            "from tavotto.engine import chartprocess; import sys; assert not ({'numpy','matplotlib'} & sys.modules.keys())",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_real_eps_packing_uses_the_same_task(task):
    import hashlib

    from tavotto.rendercore.epscontainer import SCHEMA

    ps = "0 0 moveto 10 10 lineto stroke\n"
    prepared = {
        "schema": SCHEMA,
        "ps_ascii": ps,
        "width_pt": 100,
        "height_pt": 80,
        "source_svg_sha256": "a" * 64,
        "ps_sha256": hashlib.sha256(ps.encode("ascii")).hexdigest(),
        "expanded_cost": 1,
    }
    result = task.run({"action": "pack_eps", "prepared": prepared})
    assert result["ok"]
    eps = base64.b64decode(result["eps_b64"])
    assert eps.startswith(b"%!PS-Adobe-3.0 EPSF-3.0") and b"%%BoundingBox: 0 0 100 80" in eps
    for key in ("source_svg_sha256", "ps_sha256", "width_pt", "height_pt"):
        assert result[key] == prepared[key]
    assert task.run()["ok"]


def test_actual_task_installs_shared_file_guards_before_computation(task, monkeypatch):
    protected = task.project / "keep.txt"
    protected.write_text("KEEP", encoding="utf-8")
    hook = task.root / "hook"
    hook.mkdir()
    marker = task.root / "guard-observed"
    (hook / "sitecustomize.py").write_text(
        "import sys, pathlib\n"
        "def observe(frame,event,arg):\n"
        " if event=='call' and frame.f_code.co_name=='execute_request':\n"
        f"  p=pathlib.Path({str(protected)!r}); p.unlink(); p.write_text('BAD',encoding='utf-8')\n"
        "  assert p.read_text(encoding='utf-8')=='KEEP'\n"
        f"  pathlib.Path({str(marker)!r}).write_text(str(pathlib.Path.cwd()),encoding='utf-8')\n"
        "sys.setprofile(observe)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(hook), str(ROOT / "src")]))
    assert task.run()["ok"]
    assert protected.read_text(encoding="utf-8") == "KEEP"
    assert marker.read_text(encoding="utf-8") == str(task.get().sandbox)


def test_response_preserves_structure_and_fragment_budgets(monkeypatch):
    engine = {"matplotlib": "m", "numpy": "n", "tavotto": "t"}
    raw = {"ok": True, "engine": engine, "fragment_b64": "PHN2Zy8+", "facts": {}}
    monkeypatch.setattr(chartwire, "MAX_FRAGMENT_BYTES", 4)
    with pytest.raises(chartwire.ChartWireError) as error:
        chartwire.response(json.dumps(raw).encode(), False)
    assert error.value.code == "svg_resource_limit"
    nested = 0
    for _ in range(chartprofile.MAX_JSON_DEPTH + 1):
        nested = [nested]
    with pytest.raises(chartwire.ChartWireError) as error:
        chartwire.response(json.dumps({**raw, "nested": nested}).encode(), True)
    assert error.value.code == "svg_resource_limit"


def test_unconfirmed_retirement_keeps_chart_identity_fenced(task, monkeypatch):
    task.run()
    worker = task.get()
    with monkeypatch.context() as patch:
        if task.plane == "workerd":
            patch.setattr(worker._client, "call", lambda *a, **kw: {"closed": False})
        else:
            patch.setattr(worker, "_wait_until_exited", lambda *a: False)
            patch.setattr(worker.proc, "kill", lambda: None)
        assert worker.force_kill() is False
        assert pool.is_fenced(worker)
        with pytest.raises(pool.WorkerError):
            task.get()
    assert worker.force_kill() is True


@pytest.mark.parametrize("reason", ["invalidate", "lru", "environment"])
def test_unconfirmed_chart_close_blocks_replacement(task, monkeypatch, reason):
    task.run()
    worker = task.get()
    entered = threading.Event()

    def unconfirmed():
        worker._fenced = True
        entered.set()
        return False

    try:
        with monkeypatch.context() as patch:
            patch.setattr(worker, "shutdown", unconfirmed)
            patch.setattr(worker, "force_kill", lambda: False)
            if reason == "environment":
                patch.setattr(pool, "generation_stale", lambda w: w is worker)
            elif reason == "invalidate":
                pool.invalidate("", str(task.project))
                assert entered.wait(5)
            else:
                patch.setattr(pool, "MAX_ALIVE", 1)
                worker._lease_until = 0.0
                worker.last_used = 0.0
                (task.project / "plot.py").write_text("def main(): pass", encoding="utf-8")
                pool.get("plot.py", str(task.project), "main")
                assert entered.wait(5)
            with pytest.raises(pool.WorkerError) as error:
                task.get()
            assert error.value.code == "session_dead"
    finally:
        worker.force_kill()


def test_unconfirmed_chart_retirement_still_occupies_mixed_capacity(task, monkeypatch):
    task.run()
    chart = task.get()
    first = None

    def unconfirmed():
        chart._fenced = True
        return False

    try:
        with monkeypatch.context() as patch:
            patch.setattr(chart, "shutdown", unconfirmed)
            patch.setattr(chart, "force_kill", lambda: False)
            pool.invalidate("", str(task.project))
            (task.project / "one.py").write_text("def main(): pass", encoding="utf-8")
            (task.project / "two.py").write_text("def main(): pass", encoding="utf-8")
            first = pool.get("one.py", str(task.project), "main")
            first._lease_until = 0.0
            first.last_used = 0.0
            pool.get("two.py", str(task.project), "main")
            deadline = time.monotonic() + 5
            while first.alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not first.alive()
    finally:
        chart.force_kill()
