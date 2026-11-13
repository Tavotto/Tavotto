"""Authenticated selection is distinct from execution-backed capture evidence."""

from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

from tavotto import app as m, security
from tavotto.engine import declaredinputs, figcapture, pool, workdir
from test_chart_native_api import project as _project
from test_chart_package_http import document, freeze_named, panel, post

project = _project
ENDPOINT = "/api/engine/input-declarations"


@pytest.fixture
def inputs(project):
    client, root = project
    (root / "plot.py").write_text(
        "import matplotlib.pyplot as plt\n"
        "from pathlib import Path\n"
        "value = float(Path('data.txt').read_text())\n"
        "fig, ax = plt.subplots(figsize=(1, 1))\n"
        "ax.plot([0, 1], [value, value])\n"
        "fig.savefig('F.svg')\n",
        encoding="utf-8",
    )
    (root / "data.txt").write_text("2\n", encoding="utf-8")
    body = {
        "script": "plot.py",
        "cwd_mode": "project_root",
        "files": ["data.txt"],
        "expected_revision": None,
    }
    return client, root, body


def test_selection_does_not_execute_or_change_project_bytes(inputs, monkeypatch):
    client, root, body = inputs
    before = {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    monkeypatch.setattr(
        pool, "build", lambda *_a, **_k: pytest.fail("Selecting files executed code")
    )
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 200, response.get_json()
    view = response.get_json()["input_declarations"]
    assert view["declarations"] == [
        {
            "scope": {"script": "plot.py", "cwd_mode": "project_root", "run_config": None},
            "files": ["data.txt"],
            "remaps": [],
        }
    ]
    assert str(root) not in json.dumps(view)
    assert {
        p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()
    } == before
    assert client.get(ENDPOINT).get_json()["input_declarations"] == view


def test_real_selection_to_actual_worker_to_application_receipt(inputs, monkeypatch, tmp_path):
    client, root, body = inputs
    assert client.post(ENDPOINT, json=body).status_code == 200
    monkeypatch.setattr(pool.workdir, "mode_for", lambda *_a, **_k: workdir.MODE_PROJECT_ROOT)
    worker = pool.EngineWorker(
        "plot.py", str(root), "__main__", base_dir=tmp_path / "capture-worker"
    )
    try:
        response = worker.ensure_built()
        receipt = m._execution_receipt(worker, "plot.py")
        assert (
            receipt.public_facts()["captured_inputs"]["revision"]
            == response["captured_inputs"]["revision"]
        )
        assert worker.execution_id == response["captured_inputs"]["execution_id"]
        # Current declaration changes cannot re-sign the existing Figure.
        current = client.get(ENDPOINT).get_json()["input_declarations"]
        deletion = {k: v for k, v in body.items() if k != "files"}
        deletion["expected_revision"] = current["revision"]
        assert client.delete(ENDPOINT, json=deletion).status_code == 200
        assert worker.ensure_built()["captured_inputs"] == response["captured_inputs"]
        assert m._execution_receipt(worker, "plot.py").captured_inputs == receipt.captured_inputs
    finally:
        worker.force_kill()


def test_new_captured_receipt_survives_real_package_export_and_inspection(inputs):
    client, root, body = inputs
    pid = m._project_id(root)
    env = client, root, root / "tavottofile/export", pid
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    assert client.post(ENDPOINT, json=body).status_code == 200
    registered = client.post(
        "/api/registry/probe?pj=" + pid,
        json={"script": "plot.py", "argv": [], "cost": "light"},
    )
    assert registered.status_code == 200 and registered.get_json()["registered"]
    worker, built = pool.build("plot.py", str(root), "__main__", allow_project_env=False)
    expected = built["captured_inputs"]["revision"]
    runtime = figcapture.runtime_asset_id("plot.py", "F")
    doc = document()
    doc["canvases"][0]["objects"].append(panel(file=runtime))
    frozen = freeze_named(env, doc)
    response = post(env, frozen)
    assert response.status_code == 200, response.get_json()
    archive = root / "tavottofile/export" / response.get_json()["name"]
    data = archive.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zipped:
        captures = [
            json.loads(zipped.read(name))
            for name in zipped.namelist()
            if "/snapshots/" in name and name.endswith(".json")
        ]
    assert len(captures) == 1
    assert captures[0]["payload"]["receipt"]["captured_inputs"] == {
        "version": 1,
        "revision": expected,
    }
    inspected = client.post(
        "/api/documents/v4/package/inspect",
        data={"package": (io.BytesIO(data), "capture.tavotto")},
        content_type="multipart/form-data",
    )
    assert inspected.status_code == 200, inspected.get_json()
    assert inspected.get_json()["doc"] == frozen
    assert inspected.get_json()["figure_snapshots"] == 1
    # The new capture evidence does not claim the still-v1 package contains a
    # complete declaration/resource graph. That producer remains a next batch.
    assert inspected.get_json()["execution_coverage"]["state"] == "incomplete"
    assert m._execution_receipt(worker, "plot.py").captured_inputs["revision"] == expected


def test_compare_and_swap_preserves_the_other_writers_selection(inputs):
    client, root, body = inputs
    first = client.post(ENDPOINT, json=body)
    assert first.status_code == 200
    view = first.get_json()["input_declarations"]
    response = client.post(ENDPOINT, json=body)
    assert (
        response.status_code == 409 and response.get_json()["code"] == "input_declaration_conflict"
    )
    assert declaredinputs.state(root) == view
    deletion = {k: v for k, v in body.items() if k != "files"}
    deletion["expected_revision"] = view["revision"]
    assert (
        client.delete(ENDPOINT, json=deletion).get_json()["input_declarations"]["declarations"]
        == []
    )
    assert client.post(ENDPOINT, json=body).status_code == 409


def test_failed_storage_does_not_blame_resource_selection_or_change_project(inputs, monkeypatch):
    client, root, body = inputs
    original = declaredinputs.state(root)

    def full_disk(*_a, **_k):
        raise OSError("injected full data volume")

    monkeypatch.setattr(declaredinputs.atomicio, "write_json", full_disk)
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 503
    assert response.get_json()["code"] == "input_declaration_unavailable"
    assert declaredinputs.state(root) == original


def test_corrupt_settings_are_not_presented_as_an_empty_declaration(inputs):
    client, root, _body = inputs
    path = declaredinputs.store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{broken")
    response = client.get(ENDPOINT)
    assert (
        response.status_code == 400 and response.get_json()["code"] == "input_declaration_invalid"
    )
    assert path.read_bytes() == b"{broken"


def test_deleted_script_declaration_can_be_revoked_without_reading_source(inputs, monkeypatch):
    client, root, body = inputs
    response = client.post(ENDPOINT, json=body)
    assert response.status_code == 200
    revision = response.get_json()["input_declarations"]["revision"]
    (root / "plot.py").unlink()

    def forbidden(*_a, **_k):
        pytest.fail("Removing stored approval tried to resolve executable source")

    monkeypatch.setattr(m, "_resolve_project_script", forbidden)
    deletion = {k: v for k, v in body.items() if k != "files"}
    deletion["expected_revision"] = revision
    response = client.delete(ENDPOINT, json=deletion)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["input_declarations"]["declarations"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"cwd_mode": []},
        {"cwd_mode": "native"},
        {"files": ["../external.txt"]},
        {"files": ["C:/secret.txt"]},
        {"files": ["missing.txt"]},
        {"run_config": "unknown"},
        {"expected_revision": 12},
        {"extra": True},
    ],
)
def test_invalid_selection_is_rejected_without_changing_the_store(inputs, changes):
    client, root, original = inputs
    before = declaredinputs.state(root)
    response = client.post(ENDPOINT, json={**original, **changes})
    assert response.status_code == 400, response.get_json()
    assert declaredinputs.state(root) == before


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_new_routes_require_full_session_authentication(inputs, monkeypatch, method):
    client, root, body = inputs
    state = security.SessionState("nonce", api_secret="declaration-" + "test-local-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    before = declaredinputs.state(root)
    for headers in ({}, {"X-Tavotto-Auth": state.status_token}):
        response = getattr(client, method)(ENDPOINT, json=body, headers=headers)
        assert response.status_code == 401
    assert declaredinputs.state(root) == before
    assert client.get(ENDPOINT, headers={"X-Tavotto-Auth": state.api_secret}).status_code == 200


@pytest.mark.parametrize("method", ["get", "post", "delete"])
def test_explicit_unknown_project_never_uses_the_default(inputs, method):
    client, root, body = inputs
    before = copy.deepcopy(declaredinputs.state(root))
    response = getattr(client, method)(
        ENDPOINT, json=body, headers={"X-Tavotto-Project": "not-open"}
    )
    assert response.status_code == 409 and response.get_json()["code"] == "no_project"
    assert declaredinputs.state(root) == before
