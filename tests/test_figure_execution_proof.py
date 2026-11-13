"""The opt-in `figure_execution` field proves the execution of *this* render.

The proof answers exactly one question: which live execution produced the
response just returned, applying which ordered patch list. It never replaces
the full source check, never grants an archive current eligibility, and never
turns an ordinary successful render into a failure when evidence is missing.

Real workers (safe Python, Workerd, native) are started by the existing
`test_figure_origin_execution` fixture; the Flask test process never imports
Matplotlib.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import test_export_svg as svg_cases
import test_figure_origin_execution as origin_cases
from tavotto import app as m
from tavotto.engine import patchspec, pool, workdir
from test_figure_origin_execution import _json_ok, _render, _started

BACKENDS = origin_cases.BACKENDS
STEM = origin_cases.STEM


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Same real safe/Workerd/native world as `test_figure_origin_execution`."""
    worker_python = pool.find_worker_python()
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(pool, "ENGINE_CACHE", tmp_path / "engine")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "preview")
    for client, root in svg_cases.project.__wrapped__(tmp_path, monkeypatch):
        monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", worker_python)
        workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
        yield client, root, tmp_path / "actual-executions.txt", worker_python


def _render_with_proof(world, patches):
    return world.client.post(
        "/api/engine/render",
        json={
            "id": world.source,
            "patches": patches,
            "inline_svg": True,
            "include_figure_execution": True,
        },
    )


def _expected_execution_id(worker):
    if m.engine_enginesession.is_native(worker):
        return worker.execution_id
    return worker.last_build_execution_id


@pytest.mark.parametrize("backend", BACKENDS)
def test_opt_in_proof_binds_the_render_it_actually_performed(project, monkeypatch, backend):
    with _started(project, monkeypatch, backend) as world:
        body = _json_ok(_render_with_proof(world, world.p))
        worker = world.worker
        proof = body["figure_execution"]
        assert proof == {
            "version": 1,
            "sourceId": world.source,
            "receiptId": m._execution_receipt(worker, "plot.py").receipt_id,
            "executionOwner": {"script": "plot.py", "run_config": None},
            "patchHash": patchspec.patch_hash(world.p),
        }
        # The proof names the execution which already exists; asking for it
        # must not run the scientific script again.
        assert _expected_execution_id(worker)
        assert world.counter.read_bytes() == b"run\n"
        # The ordered list, not just its canonical hash, is what was applied.
        assert body["manifest"]["elements"]
        assert proof["patchHash"] == patchspec.patch_hash(world.p)


@pytest.mark.parametrize("backend", BACKENDS)
def test_default_response_shape_keeps_no_proof(project, monkeypatch, backend):
    with _started(project, monkeypatch, backend) as world:
        body = _json_ok(_render(world, world.p, origin=False))
        assert "figure_execution" not in body
        assert "execution_id" not in body


def test_flag_must_be_a_real_boolean(project, monkeypatch):
    with _started(project, monkeypatch, "python") as world:
        for value in ("true", 1, {"on": True}, [True]):
            response = world.client.post(
                "/api/engine/render",
                json={"id": world.source, "patches": [], "include_figure_execution": value},
            )
            assert response.status_code == 400, (value, response.get_json())
            assert response.get_json()["code"] == "invalid_include_figure_execution"


def test_native_proof_is_withdrawn_when_the_barrier_moves(project, monkeypatch):
    with _started(project, monkeypatch, "native") as world:
        session = world.worker
        proof = session._proof_context()
        response = {"manifest": {}, "execution_id": session.execution_id}
        stamped = session._proof_response(dict(response), proof)
        assert stamped["execution_id"] == session.execution_id
        assert stamped["figure_execution_epoch"] == session.snapshot_epoch
        # A release/continue advances the epoch: the same response can no
        # longer be presented as proof for the old barrier.
        with session._lock:
            session.snapshot_epoch += 1
        withdrawn = session._proof_response(dict(response), proof)
        assert "execution_id" not in withdrawn
        assert "figure_execution_epoch" not in withdrawn


class _Ledger:
    def __init__(self, state):
        self.state = state

    def get(self, identity):
        return self.state


def _proof_worker(**changes):
    worker = SimpleNamespace(
        generation=3,
        last_build_execution_id="0000000000000001" + "a" * 32,
        execution_observation_unknown=False,
        spec=SimpleNamespace(target="plot.py", run_config=None),
    )
    for key, value in changes.items():
        setattr(worker, key, value)
    return worker


def _evidence(monkeypatch, worker, *, receipt_instance=None, state=None):
    identity = {"script": "plot.py", "run_config": None}
    monkeypatch.setattr(m, "_svg_worker_owner", lambda wk, script=None: identity)
    monkeypatch.setattr(
        m, "_svg_worker_ctx", lambda wk: SimpleNamespace(svg_executions=_Ledger(state))
    )
    monkeypatch.setattr(
        m,
        "_execution_receipt",
        lambda wk, script: SimpleNamespace(
            receipt_id="r-1",
            runtime_rejected=False,
            instance=receipt_instance
            if receipt_instance is not None
            else worker.last_build_execution_id,
        ),
    )
    return m._figure_execution_evidence(
        worker,
        source_id="Fig1.pdf",
        script="plot.py",
        patches=[{"gid": "g", "prop": "linewidth", "value": 2}],
        resp={"execution_id": worker.last_build_execution_id},
        dispatch_worker=worker,
        generation=3,
        native_epoch=None,
    )


def _state(**changes):
    state = {
        "unknown": False,
        "worker": None,
        "execution_id": "0000000000000001" + "a" * 32,
        "receipts": set(),
    }
    state.update(changes)
    return state


def test_evidence_requires_the_same_live_execution(monkeypatch):
    worker = _proof_worker()
    good = _evidence(monkeypatch, worker, state=_state(worker=worker))
    assert good["receiptId"] == "r-1"
    assert good["sourceId"] == "Fig1.pdf"
    assert good["executionOwner"] == {"script": "plot.py", "run_config": None}

    # Each fact is load-bearing: changing any one withdraws the proof.
    assert _evidence(monkeypatch, worker, state=_state(worker=SimpleNamespace())) is None
    assert _evidence(monkeypatch, worker, state=_state(worker=worker, unknown=True)) is None
    # A response from a different worker object is never signed with this one.
    assert (
        m._figure_execution_evidence(
            worker,
            source_id="Fig1.pdf",
            script="plot.py",
            patches=[],
            resp={"execution_id": worker.last_build_execution_id},
            dispatch_worker=_proof_worker(),
            generation=3,
            native_epoch=None,
        )
        is None
    )
    assert (
        _evidence(
            monkeypatch,
            worker,
            state=_state(worker=worker, execution_id="0000000000000002" + "b" * 32),
        )
        is None
    )
    assert _evidence(monkeypatch, _proof_worker(generation=4), state=_state(worker=worker)) is None
    assert (
        _evidence(
            monkeypatch,
            _proof_worker(execution_observation_unknown=True),
            state=_state(worker=worker),
        )
        is None
    )
    assert (
        _evidence(
            monkeypatch,
            _proof_worker(last_build_execution_id="0000000000000002" + "b" * 32),
            state=_state(worker=worker),
        )
        is None
    )
    assert (
        _evidence(monkeypatch, worker, receipt_instance="other", state=_state(worker=worker))
        is None
    )
