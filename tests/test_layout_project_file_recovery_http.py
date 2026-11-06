"""用户明确裁决的实际 AS producer：完整证据CAS、新分支、部分成功及v4来源。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from tavotto import app as m
from tavotto.engine import atomicio, documentformat, layoutsession
from test_chart_documents_v4 import _document as _chart_document, _export_request, _render_request
from test_chart_native_api import _bind, project as _native_project
from test_chart_native_export import _assert_svg_color
from test_layout_project_file_http import (
    _axis,
    _binding,
    _bound,
    _document,
    _named,
    _put,
    _read,
    _reserve,
    _saved,
    _slot,
    _url,
    env as _env,
    native_env as _native_env,
)

env = _env
project = _native_project
native_env = _native_env


def _branch():
    return _axis(f"recovery_{uuid4().hex}")


def _path(fmt="legacy"):
    return documentformat.autosave_dir(m.LAYOUT_DIR, fmt) / "working.json"


def _proof(env, *, fmt="legacy"):
    actual = _slot(env, fmt=fmt)
    return {"revision": actual.headers["X-Tavotto-Revision"], "expected": _read(env, fmt=fmt)}


def _snapshot(fmt="legacy"):
    paths = (_path(fmt), layoutsession.state_path(), layoutsession.backup_path())
    return tuple(path.read_bytes() if path.exists() else None for path in paths)


def _recover(env, document, proof, axis, *, fmt="legacy", actual_base=None):
    client, _root, headers = env
    base = proof["revision"] if actual_base is None else actual_base
    return client.put(
        _url("/autosave/working", fmt),
        query_string={
            "base_revision": base or "absent",
            "project_file_edit": json.dumps(
                {
                    "axis": axis,
                    "base_axis": proof["expected"]["edit"]["axis"],
                    "base_revision": base,
                }
            ),
            "project_file_recovery": json.dumps(proof),
        },
        json=document,
        headers=headers,
    )


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
@pytest.mark.parametrize("cause", ["external", "old_client"])
@pytest.mark.parametrize("choice", ["overwrite", "reload"])
def test_explicit_unknown_recovery_then_normal_edit_preserves_target_baseline(
    env, fmt, cause, choice
):
    document, revision, named = _bound(env, fmt=fmt)
    initial_binding = _binding(env, fmt=fmt)
    external = _document("external choice", schema=document["schema"], updated=99)
    if cause == "external":
        _path(fmt).write_bytes(atomicio.dumps_json(external))
    else:
        client, _root, headers = env
        _saved(
            client.put(
                _url("/autosave/working", fmt),
                query_string={"base_revision": revision},
                json=external,
                headers=headers,
            )
        )
    proof = _proof(env, fmt=fmt)
    assert proof["expected"]["edit"]["known"] is False
    chosen = deepcopy(external if choice == "reload" else document)
    before = _snapshot(fmt)
    rejected = _put(env, chosen, _axis(sequence=2), _axis(), proof["revision"], fmt=fmt)
    assert _snapshot(fmt) == before
    assert rejected.status_code == 409, rejected.get_json()
    assert rejected.get_json()["project_file"]["state"] == "unconfirmed"

    branch = _branch()
    frozen_proof = deepcopy(proof)
    result = _saved(_recover(env, chosen, proof, branch, fmt=fmt))
    assert proof == frozen_proof
    assert result["metadata"] == "ok"
    assert result["project_file"]["state"] == "accepted"
    assert result["project_file"]["edit"] == {
        "autosave_revision": result["revision"],
        "axis": branch,
        "known": True,
    }
    assert _slot(env, fmt=fmt).get_json() == chosen
    assert _slot(env, fmt=fmt).headers["X-Tavotto-Revision"] == result["revision"]
    assert _binding(env, fmt=fmt) == {**initial_binding, "dirty": True}
    assert _binding(env, fmt=fmt)["revision"] == named["revision"]
    chosen["updatedAt"] += 1
    continued = _saved(
        _put(
            env,
            chosen,
            {**branch, "sequence": 2},
            branch,
            result["revision"],
            fmt=fmt,
        )
    )
    assert continued["project_file"]["state"] == "accepted"
    assert _binding(env, fmt=fmt)["revision"] == named["revision"]
    assert _binding(env, fmt=fmt)["dirty"] is True


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_explicit_recovery_of_observed_missing_autosave_uses_actual_null_evidence(env, fmt):
    document, _revision, _named_file = _bound(env, fmt=fmt)
    baseline = _binding(env, fmt=fmt)
    _path(fmt).unlink()
    proof = {"revision": None, "expected": _read(env, fmt=fmt)}
    assert proof["expected"]["edit"]["known"] is False
    before = _snapshot(fmt)
    refused = _put(env, document, _axis(sequence=2), _axis(), None, fmt=fmt)
    assert _snapshot(fmt) == before
    assert refused.status_code == 409
    result = _saved(_recover(env, document, proof, _branch(), fmt=fmt))
    assert result["project_file"]["state"] == "accepted"
    assert result["project_file"]["edit"]["known"] is True
    assert _slot(env, fmt=fmt).get_json() == document
    assert _binding(env, fmt=fmt) == {**baseline, "dirty": True}


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_known_cross_editor_stale_write_requires_explicit_new_branch(env, fmt):
    document, revision, _named_file = _bound(env, fmt=fmt)
    baseline = _binding(env, fmt=fmt)
    remote_document = _document("peer", schema=document["schema"], updated=2)
    peer = _saved(_put(env, remote_document, _axis("peer"), _axis(), revision, fmt=fmt))
    before = _snapshot(fmt)
    refused = _put(env, document, _axis(sequence=2), _axis(), peer["revision"], fmt=fmt)
    assert _snapshot(fmt) == before
    assert refused.status_code == 409
    assert refused.get_json()["project_file"]["state"] == "superseded"
    proof = _proof(env, fmt=fmt)
    incoming = _branch()
    result = _saved(_recover(env, document, proof, incoming, fmt=fmt))
    assert result["project_file"]["edit"]["axis"] == incoming
    assert _binding(env, fmt=fmt) == {**baseline, "dirty": True}


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
@pytest.mark.parametrize("change", ["actual_revision", "intent", "named_ABA", "JSON_type"])
def test_explicit_recovery_stale_full_evidence_is_rejected_before_body(env, fmt, change):
    document, _revision, named = _bound(env, fmt=fmt)
    if change == "actual_revision":
        _path(fmt).write_bytes(
            atomicio.dumps_json(_document("external1", schema=document["schema"]))
        )
    proof = _proof(env, fmt=fmt)
    if change == "actual_revision":
        _path(fmt).write_bytes(
            atomicio.dumps_json(_document("external2", schema=document["schema"]))
        )
        # The tracked unknown view is identical; the frozen actual revision must still be checked.
        assert _read(env, fmt=fmt) == proof["expected"]
    elif change == "intent":
        _reserve(env, "B", expected=1, fmt=fmt)
    elif change == "named_ABA":
        b = _reserve(env, "B", expected=1, fmt=fmt)
        _saved(_named(env, document, "B", ticket=b, fmt=fmt))
        a = _reserve(env, "A", expected=b["sequence"], fmt=fmt)
        _saved(_named(env, document, "A", base=named["revision"], ticket=a, fmt=fmt))
    else:
        proof["expected"]["version"] = True
    before = _snapshot(fmt)
    actual = _slot(env, fmt=fmt).headers["X-Tavotto-Revision"]
    response = _recover(env, document, proof, _branch(), fmt=fmt, actual_base=actual)
    assert _snapshot(fmt) == before
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "stale_write"
    assert response.get_json()["project_file"]["state"] == "unconfirmed"


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "null",
        "{}",
        '{"revision":null}',
        '{"revision":null,"expected":null}',
        '{"revision":null,"expected":{},"extra":true}',
        '{"revision":"bad","expected":{}}',
        '{"revision":null,"expected":{"bad":NaN}}',
    ],
)
def test_invalid_recovery_query_rejects_before_body_even_when_document_already_exists(env, raw):
    document, revision, _named_file = _bound(env)
    client, _root, headers = env
    before = _snapshot()
    response = client.put(
        _url("/autosave/working"),
        query_string={
            "base_revision": revision,
            "project_file_edit": json.dumps(
                {
                    "axis": _branch(),
                    "base_axis": _axis(),
                    "base_revision": revision,
                }
            ),
            "project_file_recovery": raw,
        },
        json={**document, "updatedAt": 500},
        headers=headers,
    )
    assert _snapshot() == before
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "bad_request"


@pytest.mark.parametrize("owner", ["valid_without_edit", "none", "expired"])
def test_recovery_without_edit_or_explicit_project_is_400_and_no_body_write(env, owner):
    document, revision, _named_file = _bound(env)
    proof = _proof(env)
    client, _root, headers = env
    query = {"base_revision": revision, "project_file_recovery": json.dumps(proof)}
    if owner != "valid_without_edit":
        query["project_file_edit"] = json.dumps(
            {
                "axis": _branch(),
                "base_axis": _axis(),
                "base_revision": revision,
            }
        )
        m.reset_projects()
        headers = {} if owner == "none" else {"X-Tavotto-Project": "expired-project"}
    before = _snapshot()
    response = client.put(
        _url("/autosave/working"),
        query_string=query,
        json={**document, "updatedAt": 999},
        headers=headers,
    )
    assert _snapshot() == before
    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "bad_request"


@pytest.mark.parametrize("fmt", ["legacy", "v4"])
def test_recovery_metadata_failure_keeps_saved_body_revision_and_original_named_baseline(
    env, monkeypatch, fmt
):
    document, _revision, named = _bound(env, fmt=fmt)
    _path(fmt).write_bytes(atomicio.dumps_json(_document("external", schema=document["schema"])))
    proof = _proof(env, fmt=fmt)
    before_state = layoutsession.state_path().read_bytes()
    original_write = atomicio.write_json

    def fail_metadata(path, obj, **kwargs):
        if Path(path) == layoutsession.state_path():
            raise OSError("injected recovery metadata publication failure")
        return original_write(path, obj, **kwargs)

    monkeypatch.setattr(atomicio, "write_json", fail_metadata)
    chosen = {**document, "updatedAt": 500}
    result = _saved(_recover(env, chosen, proof, _branch(), fmt=fmt))
    assert result["metadata"] == "failed"
    assert result["project_file"] == {"state": "unavailable"}
    actual = _slot(env, fmt=fmt)
    assert actual.get_json() == chosen
    assert result["revision"] == actual.headers["X-Tavotto-Revision"]
    assert result["revision"] == atomicio.revision_of(actual.data)
    assert layoutsession.state_path().read_bytes() == before_state
    binding = _binding(env, fmt=fmt)
    assert binding["name"] == "A" and binding["revision"] == named["revision"]
    assert binding["dirty"] is True
    assert _read(env, fmt=fmt)["edit"]["known"] is False


def test_recovery_cannot_bypass_future_metadata_version(env):
    document, _revision, _named_file = _bound(env)
    proof = _proof(env)
    future = json.loads(layoutsession.state_path().read_bytes())
    future[layoutsession.PROJECT_FILES_KEY]["version"] = 2
    atomicio.write_json(layoutsession.state_path(), future)
    before = _snapshot()
    response = _recover(env, document, proof, _branch())
    assert _snapshot() == before
    assert response.status_code == 503, response.get_json()
    assert response.get_json()["code"] == "session_unavailable"


def test_recovery_v4_real_chart_replays_attachment_after_original_deleted_then_produces_svg(
    native_env,
):
    client, root, _headers = native_env
    document = _chart_document(_bind(client))
    _saved(_put(native_env, document, _axis(), None, None, fmt="v4"))
    stored = _slot(native_env, fmt="v4").get_json()
    pending = _reserve(native_env, "NativeA", fmt="v4")
    named = _saved(_named(native_env, stored, "NativeA", ticket=pending, fmt="v4"))
    named_path = root / named["file"]
    named_bytes = named_path.read_bytes()
    (root / "chart.svg").unlink()
    external = deepcopy(stored)
    external["project"]["name"] = "External NativeB"
    external["canvases"][0]["objects"][0]["overrides"].append(
        {
            "gid": "axes_0.lines_0",
            "prop": "color",
            "value": "#0000ff",
        }
    )
    atomicio.write_json(_path("v4"), external)
    proof = _proof(native_env, fmt="v4")
    assert proof["expected"]["edit"]["known"] is False
    before = _snapshot("v4")
    rejected = _put(native_env, external, _axis(sequence=2), _axis(), proof["revision"], fmt="v4")
    assert _snapshot("v4") == before
    assert rejected.status_code == 409
    incoming = _branch()
    recovered = _saved(_recover(native_env, external, proof, incoming, fmt="v4"))
    assert recovered["project_file"]["state"] == "accepted"
    assert recovered["project_file"]["edit"]["axis"] == incoming
    panel = _slot(native_env, fmt="v4").get_json()["canvases"][0]["objects"][0]
    assert panel["chart"] == stored["canvases"][0]["objects"][0]["chart"]
    assert panel["overrides"] == external["canvases"][0]["objects"][0]["overrides"]
    assert panel["chartSource"] == stored["canvases"][0]["objects"][0]["chartSource"]
    assert named_path.read_bytes() == named_bytes
    assert _binding(native_env, fmt="v4")["revision"] == named["revision"]
    assert _binding(native_env, fmt="v4")["dirty"] is True
    rendered = client.post("/api/engine/render", json=_render_request(panel))
    assert rendered.status_code == 200, rendered.get_json()
    _assert_svg_color(rendered.get_json()["svg"], "#0000ff")
    exported = client.post("/api/export", json=_export_request(panel, name="explicit-recovered"))
    assert exported.status_code == 200, exported.get_json()
    output = exported.get_json()
    assert output["status"] == "done", output
    produced = Path(output["export_dir"]) / output["outputs"][0]["name"]
    _assert_svg_color(produced.read_bytes(), "#0000ff")
