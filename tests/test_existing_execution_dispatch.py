"""Bounded proposal tests. No scientific imports; real dispatcher/parent methods."""

from __future__ import annotations

import ast
import collections
import copy
import importlib.util
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import executionfact, figcapture, patchspec, pool, workerd_client

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROTOCOL = load_helper("test_worker_protocol")
WORKERD = load_helper("test_workerd_pool")


def child_namespace(base=False):
    view = ROOT
    path = view / "src/tavotto/engine/wireproto.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    ns = dict(
        collections=collections,
        sys=sys,
        traceback=traceback,
        Path=Path,
        figcapture=figcapture,
        patchspec=patchspec,
    )
    exec(compile(tree, str(path), "exec"), ns)
    if not base:
        worker = ast.parse((view / "src/tavotto/engine/worker.py").read_text(encoding="utf-8"))
        klass = next(
            (
                node
                for node in worker.body
                if isinstance(node, ast.ClassDef) and node.name == "Worker"
            )
        )
        method = next(
            (
                node
                for node in klass.body
                if isinstance(node, ast.FunctionDef) and node.name == "require_existing_execution"
            )
        )
        method_ns = {"executionfact": executionfact, "ProtocolError": ns["ProtocolError"]}
        exec(
            compile(ast.Module(body=[method], type_ignores=[]), "worker.guard.actual_ast", "exec"),
            method_ns,
        )
        ns["SafeHandler"] = type(
            "SafeHandler",
            (ns["V1Handler"],),
            {"require_existing_execution": method_ns[method.name]},
        )
    return ns


def child(*, base=False, built=True, native=False):
    ns = child_namespace(base=base)
    events = []
    session = SimpleNamespace(
        artifact_source=None,
        states={"History": SimpleNamespace(unrestored=[])},
        failures={},
        preview_dpi=100,
    )
    session.do_render = lambda *_args: (
        events.append("render") or {"manifest": {"protocol_fixture": True}, "svg": "<svg/>"}
    )
    klass = ns["V1Handler"] if base or native else ns["SafeHandler"]
    handler = klass(session)
    handler.built = built
    handler.execution_id = executionfact.new_id()
    handler.ensure_built = lambda _: events.append("build")
    handler.configure_run = lambda _: events.append("configure")
    handler.select_artifact = lambda _: events.append("artifact")
    return (ns, handler, events)


def request(handler, *, expected=None, cmd="render_existing", stem="History"):
    return handler.handle_v1(
        dict(
            protocol_version=1,
            request_id="controlled-request",
            cmd=cmd,
            stem=stem,
            payload=dict(
                patches=[], inline_svg=True, expected_execution_id=expected, require_built=True
            ),
        )
    )


def observe(worker):
    pool._execution_state(worker)
    value = executionfact.new_id()
    pool._record_execution(worker, value)
    return value


@pytest.mark.parametrize("case", ["wrong", "malformed", "cold", "stem", "native"])
def test_child_rejects_before_configuration_or_build(case):
    ns, handler, events = child(built=case != "cold", native=case == "native")
    expected = handler.execution_id
    if case == "wrong":
        expected = executionfact.new_id()
    if case == "malformed":
        expected = "A"
    with pytest.raises(ns["ProtocolError"]) as exc:
        request(handler, expected=expected, stem="absent" if case == "stem" else "History")
    assert exc.value.code == ("bad_request" if case == "malformed" else "svg_source_unavailable")
    assert events == []


def test_child_same_execution_returns_original_pair_without_build():
    _, handler, events = child()
    result = request(handler, expected=handler.execution_id)
    assert result["manifest"] == {"protocol_fixture": True}
    assert result["svg"] == "<svg/>"
    assert events == ["configure", "artifact", "render"]


def test_ordinary_render_keeps_legacy_build_behavior():
    _, handler, events = child(built=False)
    request(handler, cmd="render")
    assert events == ["configure", "artifact", "build", "render"]


def test_python_same_execution_real_request_and_no_watch_or_build(tmp_path):
    worker = PROTOCOL._worker(
        lambda env: PROTOCOL._echo(env, execution_id=expected, manifest={}, svg="<svg/>"), tmp_path
    )
    expected = observe(worker)
    worker.ensure_built = lambda: pytest.fail("Guarded request must not build")
    result = worker.override("History", [], inline_svg=True, expected_execution_id=expected)
    env = worker.proc.stdin.sent[0]
    assert env["cmd"] == "render_existing"
    assert env["payload"] == {"patches": [], "inline_svg": True, "expected_execution_id": expected}
    assert result["execution_id"] == expected
    assert worker.rev == 1


@pytest.mark.parametrize("case", ["cold", "wrong", "unknown", "evicted", "invalid"])
def test_python_precheck_refuses_without_dispatch_or_build(tmp_path, case):
    worker = PROTOCOL._worker(lambda env: PROTOCOL._echo(env), tmp_path)
    expected = observe(worker)
    if case == "cold":
        worker.built = False
    elif case == "wrong":
        expected = executionfact.new_id()
    elif case == "unknown":
        worker.execution_observation_unknown = True
    elif case == "evicted":
        worker._evicted = True
    else:
        expected = "not-a-fact"
    worker.ensure_built = lambda: pytest.fail("Must not rebuild")
    with pytest.raises(pool.WorkerError):
        worker.override("History", [], expected_execution_id=expected)
    assert worker.proc.stdin.sent == [] and worker.rev == 0


def test_python_change_while_waiting_for_dispatch_lock_is_rechecked(tmp_path, monkeypatch):
    worker = PROTOCOL._worker(lambda env: PROTOCOL._echo(env), tmp_path)
    expected = observe(worker)
    failures = []
    checked = threading.Event()
    original_check = pool._require_existing_execution

    def checked_guard(target, expected_id):
        original_check(target, expected_id)
        checked.set()

    monkeypatch.setattr(pool, "_require_existing_execution", checked_guard)
    with worker.lock:
        thread = threading.Thread(
            target=lambda: capture_error(
                failures, lambda: worker.override("History", [], expected_execution_id=expected)
            )
        )
        thread.start()
        assert checked.wait(3), "The optimistic check must complete before the state change."
        worker.built = False
    thread.join(3)
    assert not thread.is_alive()
    assert failures and failures[0].code == "svg_source_unavailable"
    assert worker.proc.stdin.sent == []


def capture_error(errors, fn):
    try:
        fn()
    except BaseException as exc:
        errors.append(exc)


@pytest.mark.parametrize("reply", ["missing", "wrong", "late"])
def test_python_reply_cannot_adopt_other_execution(tmp_path, reply):

    def respond(env):
        if reply == "late":
            pool._record_execution(worker, executionfact.new_id())
        facts = (
            {}
            if reply == "missing"
            else {"execution_id": executionfact.new_id() if reply == "wrong" else expected}
        )
        return PROTOCOL._echo(env, manifest={}, **facts)

    worker = PROTOCOL._worker(respond, tmp_path)
    expected = observe(worker)
    with pytest.raises(pool.WorkerError) as exc:
        worker.override("History", [], expected_execution_id=expected)
    assert exc.value.code == "svg_source_unavailable" and worker.rev == 0


def test_workerd_unknown_session_must_not_reopen(monkeypatch, tmp_path):
    worker, client = WORKERD._worker(
        monkeypatch,
        tmp_path,
        [
            {"ok": True, "session_id": "s-original"},
            workerd_client.WorkerdError("expired", code="unknown_session"),
        ],
    )
    worker.built = True
    expected = observe(worker)
    with pytest.raises(pool.WorkerError) as exc:
        worker.override("History", [], expected_execution_id=expected)
    assert exc.value.code == "svg_source_unavailable"
    assert [op for op, _ in client.calls] == ["open_session", "render_existing"]
    assert worker._session_id == "s-original"


@pytest.mark.parametrize("reply", ["correct", "wrong_fact", "wrong_session", "late_session"])
def test_workerd_reply_is_for_frozen_session_and_execution(monkeypatch, tmp_path, reply):
    worker, client = WORKERD._worker(
        monkeypatch, tmp_path, [{"ok": True, "session_id": "s-original"}]
    )
    worker.built = True
    expected = observe(worker)
    original_call = client.call

    def respond(op, **kwargs):
        assert kwargs["session_id"] == "s-original"
        if reply == "late_session":
            worker._session_id = "s-new"
        client.calls.append((op, copy.deepcopy(kwargs)))
        return dict(
            ok=True,
            session_id="s-other" if reply == "wrong_session" else "s-original",
            execution_id=executionfact.new_id() if reply == "wrong_fact" else expected,
            manifest={},
            svg="<svg/>",
        )

    client.call = respond
    try:
        if reply == "correct":
            assert (
                worker.override("History", [], expected_execution_id=expected)["execution_id"]
                == expected
            )
        else:
            with pytest.raises(pool.WorkerError) as exc:
                worker.override("History", [], expected_execution_id=expected)
            assert exc.value.code == "svg_source_unavailable" and worker.rev == 0
    finally:
        client.call = original_call


def test_ordinary_parent_wire_keeps_original_contract(tmp_path):
    PROTOCOL.test_envelope_carries_version_generation_revision_and_hash(tmp_path)


def test_ordinary_workerd_still_reopens_unknown_session(monkeypatch, tmp_path):
    WORKERD.test_an_expired_session_is_reopened_once_and_the_call_retried(monkeypatch, tmp_path)
