"""PR #812: the actual child command line, durable diagnostics and error adapter.

Sensitive script output is deliberately omitted rather than token-replaced: argparse,
repr, native writers and inherited subprocess streams can all transform/split a value.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tavotto.engine import execspec, pool, runconfig, workerd_client

SECRET = "private-argv-SENTINEL-2026"
ROOT = Path(__file__).resolve().parents[1]


def test_quoting_heavy_argv_never_enters_either_spawn_command(tmp_path):
    tokens = ['"' * 8180]
    assert runconfig.validate_argv(tokens) == tuple(tokens)
    run = execspec.RunSelection("rc_0123456789ab", tuple(tokens))
    spec = execspec.safe_spec(
        "s.py",
        str(tmp_path),
        "__main__",
        interpreter=sys.executable,
        sandbox=str(tmp_path / "sandbox"),
        argv=tokens,
        run_config=run.config_id,
    )
    command = execspec.worker_argv(spec, worker_py=pool.WORKER_PY, out_dir=tmp_path / "out")
    spawn = pool._spawn_spec(
        "s.py",
        str(tmp_path),
        "__main__",
        tmp_path / "out",
        tmp_path / "sandbox",
        tmp_path / "worker.log",
        sys.executable,
        pool.SOURCE_CURRENT,
        run=run,
    )
    for argv in (command, spawn["argv"]):
        assert "--script-argv-json" not in argv
        assert tokens[0] not in " ".join(argv)
        assert len(subprocess.list2cmdline(argv).encode("utf-16-le")) // 2 < 32767


def test_sensitive_selection_survives_reference_lookup(tmp_path):
    run = runconfig.selection_for(tmp_path, "s.py", [SECRET], sensitive=True)
    assert run.sensitive is True
    assert runconfig.selection(tmp_path, run.config_id).sensitive is True


@pytest.fixture(params=["python", "workerd"])
def worker_factory(request, tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    python = pool.find_worker_python()
    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *a, **kw: (python, pool.SOURCE_CURRENT)
    )
    client = None
    if request.param == "workerd":
        binary = ROOT / "workerd" / "target" / "debug" / workerd_client.EXE_NAME
        if not binary.is_file():
            pytest.skip("workerd binary has not been built")
        client = workerd_client.WorkerdClient(str(binary))
    workers = []

    def create(source, tokens, *, sensitive=True):
        (root / "s.py").write_text(source, encoding="utf-8")
        run = runconfig.selection_for(root, "s.py", tokens, sensitive=sensitive)
        cls = pool.EngineWorker if client is None else pool.WorkerdWorker
        kw = {} if client is None else {"client": client}
        worker = cls("s.py", str(root), "__main__", run=run, **kw)
        workers.append(worker)
        return worker

    yield create
    for worker in workers:
        worker.shutdown()
    if client is not None:
        client.shutdown()


@pytest.mark.parametrize(
    "failure",
    [
        "import argparse; p = argparse.ArgumentParser(); p.add_argument('--value', type=int); p.parse_args()",
        "import argparse; argparse.ArgumentParser().parse_args()",
        "raise RuntimeError(repr(sys.argv))",
        "sys.exit(sys.argv[-1])",
    ],
)
def test_sensitive_failures_keep_values_out_of_command_logs_and_errors(worker_factory, failure):
    worker = worker_factory("import sys\n" + failure, ["--value", SECRET])
    pid = worker.proc.pid if isinstance(worker, pool.EngineWorker) else worker.child_pid
    if sys.platform.startswith("linux"):
        assert SECRET.encode() not in Path(f"/proc/{pid}/cmdline").read_bytes()
    with pytest.raises(pool.WorkerError) as caught:
        worker.ensure_built()
    time.sleep(0.1)  # permit the output drainer to consume the real fd stream
    error = caught.value
    assert SECRET not in str(error)
    assert SECRET not in error.traceback_text
    assert SECRET not in json.dumps(error.extra)
    assert SECRET not in worker.log_path.read_text(encoding="utf-8")
    if "argparse" in failure:
        assert error.code == "script_needs_arguments"
        assert error.extra["argv_count"] == 2
        assert error.extra["parse_kind"] in ("invalid_value", "unknown")


def test_sensitive_native_and_child_output_is_suppressed_without_breaking_protocol(worker_factory):
    worker = worker_factory(
        """import sys, os, subprocess
value = sys.argv[-1]
print(value, flush=True)
print(repr(value), file=sys.stderr, flush=True)
os.write(1, value.encode())
os.write(2, value.encode())
subprocess.run([sys.executable, "-c", "import os; os.write(1, b'native-child-private-argv-SENTINEL-2026'); os.write(2, b'native-child-private-argv-SENTINEL-2026')"], check=True)
""",
        [SECRET],
    )
    assert worker.ensure_built()["ok"]
    time.sleep(0.1)
    log = worker.log_path.read_text(encoding="utf-8")
    assert SECRET not in log
    assert "sensitive" in log.lower(), "watchdog needs a content-free output marker"


def test_ordinary_output_still_has_the_original_diagnostics(worker_factory):
    worker = worker_factory(
        "import sys\nprint(sys.argv[-1], flush=True)\nraise RuntimeError(sys.argv[-1])",
        [SECRET],
        sensitive=False,
    )
    with pytest.raises(pool.WorkerError) as caught:
        worker.ensure_built()
    assert SECRET in worker.log_path.read_text(encoding="utf-8")
    assert SECRET in caught.value.traceback_text


def test_ordinary_worker_error_payload_preserves_parse_facts():
    from tavotto import app

    error = pool.WorkerError("Arguments rejected", code="script_needs_arguments")
    error.extra = {"argv_count": 2, "parse_kind": "invalid_value"}
    assert app._worker_error_payload(error)["params"] == {
        "argv_count": "2",
        "parse_kind": "invalid_value",
    }


@pytest.mark.parametrize("speaks", [True, False])
def test_sensitive_output_only_keeps_the_watchdog_alive_while_it_progresses(
    worker_factory, monkeypatch, speaks
):
    output = "print(sys.argv[-1], flush=True)" if speaks else "pass"
    worker = worker_factory(
        f"import sys, time\nfor i in range(35):\n    {output}\n    time.sleep(0.1)\n", [SECRET]
    )
    # Complete imports/handshake before measuring the script's idle budget.
    if isinstance(worker, pool.EngineWorker):
        worker.request({"cmd": "ping"}, 60)
    initial_size = worker.log_path.stat().st_size
    monkeypatch.setattr(pool, "BUILD_IDLE_TIMEOUT", 1.5)
    monkeypatch.setattr(pool, "BUILD_HARD_TIMEOUT", 20)
    if speaks:
        assert worker.ensure_built()["ok"]
        assert len(worker.log_path.read_bytes()) - initial_size < 2048
    else:
        with pytest.raises(pool.WorkerError) as caught:
            worker.ensure_built()
        assert caught.value.code == pool.BUILD_TIMEOUT_CODE
    assert SECRET not in worker.log_path.read_text(encoding="utf-8")


def test_sensitive_input_prompts_and_stdout_never_reach_the_disk_rendezvous(worker_factory):
    worker = worker_factory(
        "import sys\nprint(sys.argv[-1], flush=True)\ninput('Confirm ' + repr(sys.argv[-1]))\n",
        [SECRET],
    )
    with pytest.raises(pool.WorkerError) as caught:
        worker.ensure_built()
    assert caught.value.code == "script_needs_input"
    for path in worker.base.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path


def test_workerd_reopen_resends_the_original_private_run_without_exposing_it_at_spawn(
    tmp_path, monkeypatch
):
    run = runconfig.selection_for(tmp_path, "s.py", [SECRET], sensitive=True)
    calls = []
    results = iter(
        [
            {"ok": True, "session_id": "first"},
            {"ok": True, "stems": {}},
            workerd_client.WorkerdError("gone", code="unknown_session"),
            {"ok": True, "session_id": "second"},
            {"ok": True, "manifest": {}},
            {"ok": True},
        ]
    )

    class Client:
        def call(self, operation, **kw):
            calls.append((operation, kw))
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(
        pool, "resolve_worker_python", lambda *a, **kw: (sys.executable, pool.SOURCE_CURRENT)
    )
    worker = pool.WorkerdWorker("s.py", str(tmp_path), "__main__", run=run, client=Client())
    try:
        worker.ensure_built()
        # Updating the selected default must not affect a transparent restart.
        runconfig.selection_for(tmp_path, "s.py", ["new value"])
        worker.override("result", [])
    finally:
        worker.shutdown()
    for operation, kw in calls:
        if operation == "open_session":
            assert SECRET not in json.dumps(kw)
        elif operation in ("build", "render"):
            assert kw["payload"]["run"] == pool._run_payload(run)["run"]
    assert [op for op, _ in calls].count("render") == 2


def test_successful_sensitive_render_preserves_but_suppresses_exception_warnings(worker_factory):
    worker = worker_factory(
        """import sys
import matplotlib.pyplot as plt
fig, ax = plt.subplots()
line, = ax.plot([1, 2])
original = line.set_linewidth
def checked(value):
    if value == 7:
        raise ValueError('dataset rejected: ' + sys.argv[-1])
    return original(value)
line.set_linewidth = checked
fig.savefig('plot.pdf')
""",
        [SECRET],
    )
    worker.ensure_built()
    result = worker.override("plot", [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 7}])
    assert result["ok"] and len(result["warnings"]) == 1
    assert "sensitive" in result["warnings"][0]
    assert SECRET not in json.dumps(result, ensure_ascii=False)


def test_sensitive_native_writer_holding_the_gil_cannot_deadlock_the_drain(
    worker_factory, monkeypatch
):
    worker = worker_factory(
        """import ctypes, os, sys
library = ctypes.PyDLL('msvcrt' if os.name == 'nt' else None)
write = library._write if os.name == 'nt' else library.write
write.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint if os.name == 'nt' else ctypes.c_size_t]
data = (sys.argv[-1] * 100000).encode()
assert write(2, data, len(data)) == len(data)
""",
        [SECRET],
    )
    monkeypatch.setattr(pool, "BUILD_HARD_TIMEOUT", 20)
    monkeypatch.setattr(pool, "BUILD_IDLE_TIMEOUT", 10)
    assert worker.ensure_built()["ok"]
    assert SECRET not in worker.log_path.read_text(encoding="utf-8")


def test_private_questions_do_not_share_saved_answers_and_replay_checks_the_real_prompt(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from tavotto.engine import inputbroker, scriptanswers, scriptinput

    run = runconfig.selection_for(tmp_path, "s.py", [SECRET], sensitive=True)
    payloads = []
    records = []
    for i, prompt in enumerate((f"Column for {SECRET}", "Different question")):
        directory = tmp_path / str(i)
        channel = scriptinput.Channel(directory, "s.py", private_key=run.input_key)
        channel.reset()

        def publish(index, payload, channel=channel):
            payloads.append(payload)
            (channel.dir / scriptinput.reply_name(index)).write_text(
                '{"answer":"2"}', encoding="utf-8"
            )

        monkeypatch.setattr(channel, "_write_request", publish)
        assert channel.ask(prompt, "input") == "2"
        records.append(channel.record[0])
    first, second = payloads
    assert first["prompt"] == second["prompt"]
    assert first["prompt_id"] != second["prompt_id"]
    assert SECRET not in json.dumps([payloads, records])
    replay = inputbroker.ReplayAnswers.of([records[0]])
    assert replay.match(1, first["prompt"], "input", first["context"], first["prompt_id"]) == (
        inputbroker.MATCH_ANSWER,
        records[0],
    )
    assert (
        replay.match(1, second["prompt"], "input", second["context"], second["prompt_id"])[0]
        == inputbroker.MATCH_MISMATCH
    )

    # No durable lookup, even if a previous answer already exists under the
    # display marker. New answers in private runs must not be remembered either.
    monkeypatch.setattr(
        scriptanswers, "recall", lambda *a, **kw: pytest.fail("private answer lookup")
    )
    monkeypatch.setattr(
        scriptanswers, "remember", lambda *a: pytest.fail("private answer persisted")
    )
    monkeypatch.setattr(inputbroker, "_has_answerer", lambda *a: True)
    monkeypatch.setattr(inputbroker, "_publish", None)
    monkeypatch.setattr(inputbroker, "_pending", {})
    worker = SimpleNamespace(figures_dir=str(tmp_path), script_name="s.py", run=run)
    directory = tmp_path / "pending"
    directory.mkdir()
    inputbroker._decide(worker, directory, second)
    pending = next(iter(inputbroker._pending.values()))
    assert pending.private
    assert inputbroker.answer(pending.id, "new answer") is pending


@pytest.mark.skipif(os.name != "posix", reason="signal 0 is a read-only liveness probe on POSIX")
@pytest.mark.parametrize("hard_exit", [False, True])
def test_sensitive_output_helper_exits_with_its_worker(
    worker_factory, hard_exit, tmp_path, monkeypatch
):
    pid_file = tmp_path / "helper-pid.txt"
    monkeypatch.setenv("HELPER_PID_FILE", str(pid_file))
    # Observe the actual Popen owned by this worker; no /proc enumeration or
    # external process tools are required in restricted execution environments.
    worker = worker_factory(
        """import inspect, os
frame = inspect.currentframe()
while frame is not None:
    guard = getattr(frame.f_locals.get("self"), "_private_output", None)
    if guard is not None:
        with open(os.environ["HELPER_PID_FILE"], "w") as out:
            out.write(str(guard.pid))
        break
    frame = frame.f_back
else:
    raise AssertionError("worker has no output helper")
""",
        [SECRET],
    )
    worker.ensure_built()
    helper = int(pid_file.read_text())
    os.kill(helper, 0)  # Proven alive before the action being tested.
    pid = worker.proc.pid if isinstance(worker, pool.EngineWorker) else worker.child_pid
    if hard_exit:
        os.kill(pid, 9)
    else:
        worker.shutdown()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(helper, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("sensitive output helper outlived the worker")


def test_private_input_identity_survives_hot_build_and_frozen_replay(worker_factory, monkeypatch):
    from tavotto.engine import inputbroker, scriptanswers, scriptinput

    asked = []

    def publish(event, project, payload):
        if event == "script.input_requested":
            asked.append(payload)
            inputbroker.answer(payload["id"], "2")

    monkeypatch.setattr(inputbroker, "_publish", publish)
    monkeypatch.setattr(inputbroker, "_has_answerer", lambda *a: True)
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "5")
    hot = worker_factory(
        "import sys\nassert input('Column for ' + sys.argv[-1]) == '2'\n", [SECRET]
    )
    hot.ensure_built()
    assert len(asked) == 1
    assert "prompt_id" in hot.last_build_script_inputs[0]
    assert not scriptanswers.entries(hot.figures_dir, hot.script_name)
    for changed in (False, True):
        if changed:
            (Path(hot.figures_dir) / hot.script_name).write_text(
                "input('A different question')\n", encoding="utf-8"
            )
        fresh = pool.one_shot(
            hot.script_name,
            hot.figures_dir,
            hot.entry,
            run=hot.run,
            script_inputs=hot.last_build_script_inputs,
        )
        try:
            if changed:
                with pytest.raises(pool.WorkerError) as caught:
                    fresh.ensure_built()
                assert caught.value.code == "script_needs_input"
            else:
                assert fresh.ensure_built()["script_inputs"] == hot.last_build_script_inputs
        finally:
            pool.discard(fresh)
    assert len(asked) == 1


def test_private_context_detects_changed_menu_without_a_guessable_disk_digest(
    tmp_path, monkeypatch
):
    import io

    from tavotto.engine import inputbroker, scriptinput

    run = runconfig.selection_for(tmp_path, "s.py", [SECRET], sensitive=True)
    records = []
    for index, menu in enumerate(("1 A\n2 B", "1 B\n2 A")):
        tail = scriptinput.StdoutTail(io.StringIO())
        tail.write(menu + SECRET)
        channel = scriptinput.Channel(
            tmp_path / str(index), "s.py", tail, private_key=run.input_key
        )
        channel.reset()

        def publish(index, payload, channel=channel):
            assert payload["stdout_tail"] == ""
            assert SECRET not in json.dumps(payload)
            assert payload["context"] != scriptinput.context_digest(
                "input", "Choose", menu + SECRET, []
            )
            (channel.dir / scriptinput.reply_name(index)).write_text(
                '{"answer":"2"}', encoding="utf-8"
            )

        monkeypatch.setattr(channel, "_write_request", publish)
        assert channel.ask("Choose", "input") == "2"
        records.append(channel.record[0])
    assert records[0]["prompt_id"] == records[1]["prompt_id"]
    replay = inputbroker.ReplayAnswers.of([records[0]])
    second = records[1]
    assert (
        replay.match(1, second["prompt"], "input", second["context"], second["prompt_id"])[0]
        == inputbroker.MATCH_MISMATCH
    )


def test_sensitive_runs_keep_only_explicit_hot_replay_not_durable_transcripts(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from tavotto.engine import inputbroker, inputtranscript

    run = runconfig.selection_for(tmp_path, "s.py", [SECRET], sensitive=True)
    worker = SimpleNamespace(figures_dir=str(tmp_path), script_name="s.py", run=run)
    monkeypatch.setattr(
        inputtranscript, "lookup", lambda *a: pytest.fail("private transcript lookup")
    )
    monkeypatch.setattr(
        inputtranscript, "bind", lambda *a: pytest.fail("private transcript persisted")
    )
    assert inputbroker._frozen_policy(worker) is None
    inputbroker.finished(worker, [{"index": 1, "answer": SECRET}])
    worker.script_input_policy = inputbroker.ReplayAnswers.of([])
    assert inputbroker._frozen_policy(worker) is worker.script_input_policy
