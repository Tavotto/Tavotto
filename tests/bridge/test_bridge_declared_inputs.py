"""Actual runner source/start/barrier boundaries, with no mocked scientific producer."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import socket
import subprocess

import pytest

from support.bridgekit import child_env
from tavotto.engine import bridge, executionfact, inputdeclaration, pool

HEADER = """from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
"""
FIGURE = """value = float(Path('data.txt').read_text())
fig, ax = plt.subplots(figsize=(2, 1))
ax.plot([0, 1], [value, value])
fig.savefig('F.svg')
"""
SOURCE = HEADER + FIGURE + "plt.show()\n"
LEGACY_BARRIER_KEYS = {
    "bridge_event",
    "reason",
    "stems",
    "failed_stems",
    "figure_facts",
    "script_error",
    "rebase_warnings",
}
CAPTURE_KEYS = {"execution_id", "script", "captured_inputs"}


class Runner:
    """Owned child plus a real token handshake and synchronous parent socket."""

    def __init__(
        self, root, *, candidate=None, target="plot.py", kind="script", argv=(), rc=None, cwd=None
    ):
        self.root = root
        self.candidate = candidate
        self.target = target
        self.kind = kind
        self.argv = argv
        self.rc = rc
        self.cwd = root if cwd is None else cwd
        self.frames = []
        self.counter = 0
        self.at_barrier = False
        self.connection = self.stream = self.process = None

    def __enter__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(30)
        token = secrets.token_hex(24)
        command = [
            pool.find_worker_python(),
            str(bridge.RUNNER_PY),
            "--target-kind",
            self.kind,
            "--target",
            self.target,
            "--project-root",
            str(self.root),
            "--out-dir",
            str(self.root / "runner-output"),
            "--control-port",
            str(self.listener.getsockname()[1]),
        ]
        if self.rc:
            command += ["--run-config", self.rc]
        command += ["--", *self.argv]
        self.command = command
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.cwd,
                env=child_env({"TAVOTTO_BRIDGE_TOKEN": token, "TAVOTTO_NO_TELEMETRY": "1"}),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.connection = self.listener.accept()[0]
            self.connection.settimeout(30)
            self.stream = self.connection.makefile("rb")
            self.hello = json.loads(self.stream.readline())
            assert self.hello["bridge_hello"] == 1
            assert self.hello["token"] == token
            ack = {"ok": True}
            if self.candidate is not None:
                ack["input_declaration"] = self.candidate
            self.send(ack)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc):
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.stream is not None:
            self.stream.close()
        if self.connection is not None:
            self.connection.close()
        self.listener.close()
        if self.process is not None:
            try:
                self.stdout, self.stderr = self.process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.stdout, self.stderr = self.process.communicate(timeout=10)

    def send(self, value):
        self.connection.sendall(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")

    def read(self):
        line = self.stream.readline()
        assert line, f"runner closed before the expected frame; previous frames: {self.frames}"
        frame = json.loads(line)
        self.frames.append(frame)
        if frame.get("bridge_event") == "barrier":
            self.at_barrier = True
        elif frame.get("bridge_event") == "released":
            self.at_barrier = False
        return frame

    def event(self, name):
        while True:
            frame = self.read()
            if frame.get("bridge_event") == name:
                return frame
            assert frame.get("bridge_event") != "exit", frame

    def call(self, command, **fields):
        self.counter += 1
        request = {
            "protocol_version": 1,
            "request_id": f"capture-{self.counter}",
            "cmd": command,
            "payload": {},
            **fields,
        }
        self.send(request)
        while True:
            response = self.read()
            if response.get("request_id") == request["request_id"]:
                assert response.get("ok"), response
                if command == "continue":
                    self.at_barrier = False
                return response
            assert response.get("bridge_event") != "exit", response

    def finish(self, expected_code=0):
        if self.at_barrier:
            self.call("continue")
        while True:
            frame = self.read()
            if frame.get("bridge_event") == "barrier":
                self.call("continue")
            elif frame.get("bridge_event") == "exit":
                assert frame["code"] == expected_code, frame
                break
        self.stdout, self.stderr = self.process.communicate(timeout=30)
        assert self.process.returncode == expected_code, self.stderr.decode("utf-8", "replace")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    root = tmp_path / "project"
    root.mkdir()
    (root / "plot.py").write_text(SOURCE, encoding="utf-8")
    (root / "data.txt").write_bytes(b"1\n")
    return root


def candidate(root, *, script="plot.py", cwd_mode="project_root", rc=None):
    return inputdeclaration.validate_candidate(
        {
            "version": inputdeclaration.VERSION,
            "scope": {"script": script, "cwd_mode": cwd_mode, "run_config": rc},
            "script_path": str(root / script),
            "files": [{"id": "data.txt", "path": str(root / "data.txt")}],
            "prefixes": [],
            "mappings": [],
            "rules": [],
            "limits": {"max_files": 100, "max_bytes": 1024 * 1024},
        }
    )


def capture(frame, *, script="plot.py"):
    assert set(frame) == LEGACY_BARRIER_KEYS | CAPTURE_KEYS
    fact = frame["captured_inputs"]
    assert inputdeclaration.validate_fact(fact, execution_id=frame["execution_id"]) == fact
    assert executionfact.valid_id(frame["execution_id"])
    assert frame["script"] == fact["declaration"]["scope"]["script"] == script
    return fact


def no_capture(frame):
    assert set(frame) == LEGACY_BARRIER_KEYS
    assert frame["stems"] == ["F"]


def test_actual_script_source_start_and_barrier_bind_the_same_frozen_inputs(project):
    source = (project / "plot.py").read_bytes()
    with Runner(project, candidate=candidate(project)) as runner:
        start = runner.event("execution_started")
        first = runner.event("barrier")
        fact = capture(first)
        assert fact["execution_id"] == start["execution_id"]
        assert fact["declaration"]["script"]["sha256"] == hashlib.sha256(source).hexdigest()
        assert fact["declaration"]["files"] == [
            {"id": "data.txt", "sha256": hashlib.sha256(b"1\n").hexdigest(), "size_bytes": 2}
        ]
        assert str(project) not in json.dumps(runner.frames)
        built = runner.call("build")
        assert list(built["stems"]) == ["F"]
        assert "captured_inputs" not in built
        rendered = runner.call("render", stem="F", payload={"patches": [], "inline_svg": True})
        assert "<svg" in rendered["svg"]
        assert rendered["manifest"]["elements"]
        runner.finish()


def test_no_declaration_keeps_the_existing_start_barrier_and_build_shape(project):
    with Runner(project) as runner:
        start = runner.event("execution_started")
        assert set(start) == {"bridge_event", "execution_id", "script"}
        no_capture(runner.event("barrier"))
        assert "input_declaration" not in runner.command
        assert "captured_inputs" not in runner.call("build")
        runner.finish()


@pytest.mark.parametrize("failure", ["missing", "syntax"])
def test_source_open_or_compile_failure_cannot_publish_a_capture(project, failure):
    approved = candidate(project)
    if failure == "missing":
        (project / "plot.py").unlink()
    else:
        (project / "plot.py").write_text("if broken syntax\n", encoding="utf-8")
    with Runner(project, candidate=approved) as runner:
        runner.finish(expected_code=1)
        assert not any(frame.get("bridge_event") == "execution_started" for frame in runner.frames)
        assert not any("captured_inputs" in frame for frame in runner.frames)


def test_parent_cannot_sign_inputs_changed_after_the_real_start_event(project):
    wait = """import time
deadline = time.monotonic() + 10
while not Path('go').exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
"""
    (project / "plot.py").write_text(
        HEADER + wait + FIGURE + "Path('used.txt').write_text(str(value))\nplt.show()\n",
        encoding="utf-8",
    )
    with Runner(project, candidate=candidate(project)) as runner:
        runner.event("execution_started")
        (project / "data.txt").write_bytes(b"2\n")
        (project / "go").write_bytes(b"ready")
        no_capture(runner.event("barrier"))
        assert (project / "used.txt").read_text() == "2.0"
        assert list(runner.call("build")["stems"]) == ["F"]
        runner.finish()


def test_script_path_replacement_after_compile_does_not_sign_the_new_disk_bytes(project):
    wait = """import time
deadline = time.monotonic() + 10
while not Path('go').exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
"""
    original = HEADER + wait + FIGURE + "Path('old-code').write_bytes(b'ran')\nplt.show()\n"
    (project / "plot.py").write_text(original, encoding="utf-8")
    with Runner(project, candidate=candidate(project)) as runner:
        runner.event("execution_started")
        (project / "plot.py").write_text(
            "raise AssertionError('new disk code')\n", encoding="utf-8"
        )
        (project / "go").write_bytes(b"ready")
        no_capture(runner.event("barrier"))
        assert (project / "old-code").read_bytes() == b"ran"
        runner.finish()


def test_each_unchanged_continuation_keeps_the_lineage_revision_and_has_a_new_id(project):
    (project / "plot.py").write_text(SOURCE + "plt.show()\n", encoding="utf-8")
    with Runner(project, candidate=candidate(project)) as runner:
        first = capture(runner.event("barrier"))
        released = runner.call("continue")
        second = capture(runner.event("barrier"))
        assert second["execution_id"] == released["execution_id"] != first["execution_id"]
        assert second["revision"] == first["revision"]
        assert second["declaration"] == first["declaration"]
        runner.finish()


def test_changed_inputs_between_barriers_revoke_the_lineage_without_blocking_editing(project):
    (project / "plot.py").write_text(SOURCE + "plt.show()\nplt.show()\n", encoding="utf-8")
    with Runner(project, candidate=candidate(project)) as runner:
        first = copy.deepcopy(capture(runner.event("barrier")))
        (project / "data.txt").write_bytes(b"2\n")
        assert list(runner.call("build")["stems"]) == ["F"]
        assert (
            "<svg"
            in runner.call("render", stem="F", payload={"patches": [], "inline_svg": True})["svg"]
        )
        assert (
            capture(next(frame for frame in runner.frames if "captured_inputs" in frame)) == first
        )
        runner.call("continue")
        no_capture(runner.event("barrier"))
        (project / "data.txt").write_bytes(b"1\n")
        runner.call("continue")
        no_capture(runner.event("barrier"))
        runner.finish()


def test_deleted_unused_declared_file_does_not_break_the_hot_figure(project):
    extra = project / "unused.txt"
    extra.write_bytes(b"unused resource\n")
    approved = candidate(project)
    approved["files"].append({"id": "unused.txt", "path": str(extra)})
    (project / "plot.py").write_text(SOURCE + "plt.show()\n", encoding="utf-8")
    with Runner(project, candidate=approved) as runner:
        capture(runner.event("barrier"))
        extra.unlink()
        assert list(runner.call("build")["stems"]) == ["F"]
        assert (
            "<svg"
            in runner.call("render", stem="F", payload={"patches": [], "inline_svg": True})["svg"]
        )
        runner.call("continue")
        no_capture(runner.event("barrier"))
        runner.finish()


def test_candidate_sent_during_a_later_request_cannot_certify_an_existing_figure(project):
    (project / "plot.py").write_text(SOURCE + "plt.show()\n", encoding="utf-8")
    with Runner(project) as runner:
        no_capture(runner.event("barrier"))
        response = runner.call("build", payload={"input_declaration": candidate(project)})
        assert "captured_inputs" not in response
        runner.call("continue", payload={"input_declaration": candidate(project)})
        no_capture(runner.event("barrier"))
        runner.finish()


@pytest.mark.parametrize("known_config", [False, True])
def test_native_argument_tokens_require_the_original_verified_configuration(project, known_config):
    rc = "rc_123456abcdef" if known_config else None
    with Runner(
        project,
        candidate=candidate(project, rc=rc),
        argv=("--private-value", "do-not-publish"),
        rc=rc,
    ) as runner:
        frame = runner.event("barrier")
        if known_config:
            assert capture(frame)["declaration"]["scope"]["run_config"] == rc
        else:
            no_capture(frame)
        assert "do-not-publish" not in json.dumps(runner.frames)
        runner.finish()


@pytest.mark.parametrize("invalid", ["future", "extra", "null-scope", "sandbox", "remap"])
def test_unusable_optional_ack_never_changes_ordinary_native_execution(project, invalid):
    approved = candidate(project)
    if invalid == "future":
        approved["version"] += 1
    elif invalid == "extra":
        approved["extra"] = "not allowed"
    elif invalid == "null-scope":
        approved["scope"] = None
    elif invalid == "sandbox":
        approved["scope"]["cwd_mode"] = "sandbox"
    elif invalid == "remap":
        approved["rules"] = [{"kind": "file", "from": "alias", "to": str(project / "data.txt")}]
        approved["mappings"] = [{"kind": "file", "from": "alias", "id": "data.txt"}]
    with Runner(project, candidate=approved) as runner:
        no_capture(runner.event("barrier"))
        assert list(runner.call("build")["stems"]) == ["F"]
        runner.finish()


def test_candidate_workdir_enum_is_checked_against_the_actual_native_directory(project):
    directory = project / "scripts"
    directory.mkdir()
    (directory / "plot.py").write_text(SOURCE, encoding="utf-8")
    with Runner(
        project,
        target="scripts/plot.py",
        candidate=candidate(project, script="scripts/plot.py", cwd_mode="project"),
    ) as runner:
        no_capture(runner.event("barrier"))
        runner.finish()


@pytest.mark.parametrize("cwd_mode", ["project", "project_root"])
def test_nested_script_captures_the_explicit_native_workdir_scope(project, cwd_mode):
    directory = project / "scripts"
    directory.mkdir()
    script = directory / "plot.py"
    script.write_text(SOURCE, encoding="utf-8")
    (directory / "data.txt").write_bytes(b"2\n")
    approved = candidate(project, script="scripts/plot.py", cwd_mode=cwd_mode)
    if cwd_mode == "project":
        approved["files"] = [{"id": "scripts/data.txt", "path": str(directory / "data.txt")}]
    with Runner(
        project,
        target=str(script),
        candidate=approved,
        cwd=directory if cwd_mode == "project" else project,
    ) as runner:
        fact = capture(runner.event("barrier"), script="scripts/plot.py")
        assert fact["declaration"]["scope"]["cwd_mode"] == cwd_mode
        [file] = fact["declaration"]["files"]
        assert file["id"] == ("scripts/data.txt" if cwd_mode == "project" else "data.txt")
        assert (
            file["sha256"]
            == hashlib.sha256(b"2\n" if cwd_mode == "project" else b"1\n").hexdigest()
        )
        runner.finish()


def test_module_parent_import_barrier_cannot_claim_the_target_declaration(project):
    package = project / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(SOURCE, encoding="utf-8")
    (package / "plot.py").write_text(
        "import matplotlib.pyplot as plt\nplt.show()\n", encoding="utf-8"
    )
    with Runner(
        project,
        target="pkg.plot",
        kind="module",
        candidate=candidate(project, script="pkg/plot.py"),
    ) as runner:
        no_capture(runner.event("barrier"))
        assert not any(frame.get("bridge_event") == "execution_started" for frame in runner.frames)
        runner.call("continue")
        start = runner.event("execution_started")
        assert start["script"] == "pkg/plot.py"
        no_capture(runner.event("barrier"))
        runner.finish()


def test_private_bootstrap_preserves_the_users_same_named_inputdeclaration_module(project):
    (project / "inputdeclaration.py").write_text("SENTINEL = 'user-owned'\n", encoding="utf-8")
    source = """import sys
assert 'matplotlib' not in sys.modules
import inputdeclaration
assert inputdeclaration.SENTINEL == 'user-owned'
"""
    (project / "plot.py").write_text(source + SOURCE, encoding="utf-8")
    with Runner(project, candidate=candidate(project)) as runner:
        capture(runner.event("barrier"))
        assert (project / "__pycache__").is_dir()
        runner.finish()
