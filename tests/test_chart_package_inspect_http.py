"""真实上传检视：核完整资源与配对，零脚本执行、零项目/正文/配置写入。"""

from __future__ import annotations

import builtins
import copy
import errno
import hashlib
import io
import json
import struct
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict
from werkzeug.test import EnvironBuilder

from tavotto import app as m, security
from tavotto.engine import chartpackage, config, documentpackage, figcapture, packagebudget, pool
from test_chart_native_api import _bind
from test_chart_package_http import (
    document,
    env as _package_env,
    freeze_named,
    panel,
    post,
    project as _native_project,
)
from test_package_execution_http import ARGV, SCRIPT, cold_export_only, scientific

env = _package_env
project = _native_project
ENDPOINT = "/api/documents/v4/package/inspect"
LAYOUT = documentpackage.LAYOUT_MEMBER
MANIFEST = documentpackage.MANIFEST_MEMBER


def _zip(members, *, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return stream.getvalue()


def _members(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def _exported(env, doc=None):
    response = post(env, doc)
    assert response.status_code == 200, response.get_json()
    blob = (env[2] / response.get_json()["name"]).read_bytes()
    members = _members(blob)
    return blob, members, json.loads(members[LAYOUT]), json.loads(members[MANIFEST])


def _upload(client, blob, *, pj=None, **kwargs):
    suffix = "" if pj is None else "?pj=" + pj
    return client.post(
        ENDPOINT + suffix,
        data={"package": (io.BytesIO(blob), "fixture.tavotto")},
        content_type="multipart/form-data",
        **kwargs,
    )


def _result(response):
    assert response.status_code == 200, response.get_json()
    assert response.headers["X-Tavotto-Document-Format"] == "v4"
    result = response.get_json()
    assert set(result) == {
        "document_format",
        "doc",
        "manifest",
        "resources",
        "missing",
        "warnings",
        "figure_snapshots",
        "execution_coverage",
    }
    assert result["document_format"] == "v4"
    assert all(set(item) == {"target", "record"} for item in result["resources"])
    assert all(set(item) == {"reason", "id"} for item in result["warnings"])
    assert all(
        isinstance(item["reason"], str) and item["reason"] and isinstance(item["id"], str)
        for item in result["warnings"]
    )
    return result


def _invalid(response, *, status=422, reason=None):
    assert response.status_code == status, response.get_json()
    value = response.get_json()
    assert value["code"] == "package_invalid", value
    assert isinstance(value["params"]["reason"], str) and value["params"]["reason"], value
    if reason is not None:
        assert value["params"]["reason"] == reason, value


def _files(root):
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _no_execution(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only package inspection tried to execute or recompute a source")

    for name in ("get", "build", "one_shot"):
        monkeypatch.setattr(pool, name, forbidden)
    monkeypatch.setattr(m, "_render_chart_snapshot", forbidden)


def _temporary_directories(monkeypatch):
    original = tempfile.TemporaryDirectory
    created = []

    def record(*args, **kwargs):
        context = original(*args, **kwargs)
        path = Path(context.name)
        assert path.is_dir(), "temporary observation must see an actual live stage"
        assert path.is_relative_to(config.data_dir())
        created.append(path)
        return context

    monkeypatch.setattr(tempfile, "TemporaryDirectory", record)
    return created


def _resign_member(members, name, data):
    """Change actual bytes and the outer hash, leaving inner semantic authority intact."""
    changed = dict(members)
    changed[name] = data
    manifest = json.loads(changed[MANIFEST])
    row = next(row for row in manifest["assets"] if row["path"] == name)
    row.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
    changed[MANIFEST] = json.dumps(manifest, ensure_ascii=False).encode()
    return changed


def _add_resource(members, kind, logical_id, data):
    changed = dict(members)
    prefix = "scripts/" if kind == "script" else "assets/"
    name = prefix + logical_id
    assert name not in changed
    changed[name] = data
    manifest = json.loads(changed[MANIFEST])
    manifest["assets"].append(
        {
            "kind": kind,
            "id": logical_id,
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
    )
    changed[MANIFEST] = json.dumps(manifest, ensure_ascii=False).encode()
    return changed


def test_empty_package_can_be_inspected_without_an_open_project(env, monkeypatch):
    blob, _members_, doc, manifest = _exported(env)
    m.reset_projects(wait=True)
    _no_execution(monkeypatch)
    created = _temporary_directories(monkeypatch)
    result = _result(_upload(env[0], blob))
    assert result["doc"] == doc and result["manifest"] == manifest
    assert result["resources"] == result["missing"] == result["warnings"] == []
    assert result["figure_snapshots"] == 0 and result["execution_coverage"] is None
    assert created and all(not path.exists() for path in created)


def test_ordinary_missing_assets_remain_an_honest_incomplete_backup(env, monkeypatch):
    doc = document()
    doc["canvases"][0]["objects"] = [panel(file="missing.png")]
    blob, _members_, stored, manifest = _exported(env, doc)
    _no_execution(monkeypatch)
    result = _result(_upload(env[0], blob, pj=env[3]))
    assert result["doc"] == stored and result["manifest"] == manifest
    assert result["missing"] == ["missing.png"]
    assert result["execution_coverage"] is None and result["figure_snapshots"] == 0


def test_older_v4_script_resources_without_execution_are_not_run_or_rejected(env, monkeypatch):
    _blob, members, stored, _manifest = _exported(env)
    members = _add_resource(
        members, "script", "scripts/old.py", b"raise RuntimeError('MUST NOT EXECUTE')"
    )
    members = _add_resource(members, "file", "data/old.csv", b"x,y\n1,2\n")
    _no_execution(monkeypatch)
    before = (_files(env[1]), _files(config.config_dir()), _files(config.data_dir()))
    result = _result(_upload(env[0], _zip(members)))
    assert result["doc"] == stored and result["execution_coverage"] is None
    assert {item["target"] for item in result["resources"]} == {"scripts/old.py", "data/old.csv"}
    assert result["figure_snapshots"] == 0 and result["missing"] == []
    assert (_files(env[1]), _files(config.config_dir()), _files(config.data_dir())) == before


def test_identical_cross_namespace_resource_targets_are_safely_deduplicated(env, monkeypatch):
    _blob, members, _doc, _manifest = _exported(env)
    members = _add_resource(members, "file", "shared.txt", b"same bytes")
    members = _add_resource(members, "script", "shared.txt", b"same bytes")
    _no_execution(monkeypatch)
    result = _result(_upload(env[0], _zip(members)))
    assert len(result["resources"]) == 1 and result["resources"][0]["target"] == "shared.txt"
    assert len(result["manifest"]["assets"]) == 2


@pytest.mark.parametrize("declared_missing", [False, True])
def test_deleted_original_chart_is_read_only_and_all_stages_are_cleaned(
    env,
    monkeypatch,
    declared_missing,
):
    client, root, _output, pid = env
    state = _bind(client)
    edits = [{"gid": "axes_0.lines_0", "prop": "linewidth", "value": 5}]
    frozen = freeze_named(env, document((state, edits)))
    (root / "chart.svg").unlink()
    blob, members, stored, manifest = _exported(env, frozen)
    if declared_missing:
        manifest["missing_at_pack_time"] = ["chart.svg"]
        members[MANIFEST] = json.dumps(manifest).encode()
        blob = _zip(members)
    _no_execution(monkeypatch)
    before = (_files(root), _files(config.config_dir()), _files(config.data_dir()))
    created = _temporary_directories(monkeypatch)
    result = _result(_upload(client, blob, pj=pid))
    assert result["doc"] == stored and result["manifest"] == manifest
    assert result["missing"] == [] and result["figure_snapshots"] == 0
    assert result["execution_coverage"] is None
    assert [item["record"] for item in result["resources"]] == manifest["assets"]
    ref = frozen["canvases"][0]["objects"][0]["chartSource"]
    assert any(ref["bytesSha256"] + ".svg" in item["target"] for item in result["resources"])
    assert all(item["record"]["path"] in members for item in result["resources"])
    assert (_files(root), _files(config.config_dir()), _files(config.data_dir())) == before
    assert created and all(not path.exists() for path in created)
    assert not (root / "chart.svg").exists()


def test_missing_observed_helper_is_reported_then_cleared_when_actually_carried(
    env,
    monkeypatch,
):
    case = scientific(env)
    cold_export_only(env, monkeypatch, case)
    helper = "scripts/helper.py"
    (env[1] / helper).unlink()
    blob, members, _stored, manifest = _exported(env, case["doc"])
    assert manifest["missing_at_pack_time"] == [helper]
    assert any(
        row["reason"] == "resource_missing" for row in manifest["execution"]["coverage"]["issues"]
    )
    assert "scripts/" + helper not in members
    _no_execution(monkeypatch)
    before = (_files(env[1]), _files(config.config_dir()), _files(config.data_dir()))
    result = _result(_upload(env[0], blob, pj=env[3]))
    assert result["missing"] == [helper]
    restored = _add_resource(members, "script", helper, case["source_bytes"][helper])
    present = _result(_upload(env[0], _zip(restored), pj=env[3]))
    assert present["manifest"]["missing_at_pack_time"] == [helper]
    assert present["missing"] == []
    assert present["execution_coverage"] == manifest["execution"]["coverage"]
    assert (_files(env[1]), _files(config.config_dir()), _files(config.data_dir())) == before


@pytest.mark.parametrize("opaque", [False, True])
def test_runtime_descriptor_script_is_a_missing_candidate_without_registration(
    env,
    monkeypatch,
    opaque,
):
    script = "scripts/legacy.py"
    runtime_id = (
        "runtime:opaque-historical-figure"
        if opaque
        else figcapture.runtime_asset_id(script, "result")
    )
    doc = document()
    value = panel(file=runtime_id)
    value.update(fileKind="runtime", figureFrame=1)
    doc["canvases"][0]["objects"] = [value]
    path = env[2] / "descriptor-only.tavotto"
    chartpackage.write_chart_package(
        path,
        doc,
        env[1],
        budget_factory=packagebudget.output_budget,
        metadata={
            "runtime_assets": [
                {"id": runtime_id, "kind": "runtime", "script": script, "stem": "result"}
            ]
        },
    )
    _no_execution(monkeypatch)
    before = (_files(env[1]), _files(config.config_dir()), _files(config.data_dir()))
    result = _result(_upload(env[0], path.read_bytes(), pj=env[3]))
    assert "scripts" not in result["manifest"] and "execution" not in result["manifest"]
    assert result["resources"] == [] and result["execution_coverage"] is None
    assert result["missing"] == [script]
    assert {"reason": "execution_unrecorded", "id": script} in result["warnings"]
    assert (_files(env[1]), _files(config.config_dir()), _files(config.data_dir())) == before


def test_declared_resource_already_present_in_the_plan_is_not_missing(env, monkeypatch):
    _blob, members, stored, manifest = _exported(env)
    resource_id = "data/old.csv"
    manifest["missing_at_pack_time"] = [resource_id]
    members[MANIFEST] = json.dumps(manifest).encode()
    members = _add_resource(members, "file", resource_id, b"x,y\n1,2\n")
    _no_execution(monkeypatch)
    result = _result(_upload(env[0], _zip(members)))
    assert result["doc"] == stored
    assert result["manifest"]["missing_at_pack_time"] == [resource_id]
    assert result["missing"] == []


@pytest.mark.parametrize("declaration", ["missing_at_pack_time", "scripts", "runtime_assets"])
@pytest.mark.parametrize("spelling", ["relative_escape", "absolute", "backslash"])
def test_untrusted_declared_resource_is_preserved_as_warning_without_external_reads(
    env,
    monkeypatch,
    declaration,
    spelling,
):
    _blob, members, _stored, manifest = _exported(env)
    canary = (
        env[1] / "data" / "declared.csv"
        if spelling == "backslash"
        else env[1].parent / "must-not-read.py"
    )
    canary.parent.mkdir(parents=True, exist_ok=True)
    secret = b"OUTSIDE_DECLARATION_READ_CANARY"
    canary.write_bytes(secret)
    logical_id = {
        "relative_escape": "../" + canary.name,
        "absolute": canary.as_posix(),
        "backslash": "data\\declared.csv",
    }[spelling]
    manifest[declaration] = (
        [{"id": "runtime:opaque", "script": logical_id, "stem": "result"}]
        if declaration == "runtime_assets"
        else [logical_id]
    )
    members[MANIFEST] = json.dumps(manifest).encode()
    _no_execution(monkeypatch)
    attempted = []
    with monkeypatch.context() as guards:
        for module in (builtins, io):
            original = module.open

            def guarded(file, *args, _original=original, **kwargs):
                if isinstance(file, (str, Path)) and Path(file).resolve() == canary.resolve():
                    attempted.append(str(file))
                    pytest.fail("untrusted manifest declaration authorized an external read")
                return _original(file, *args, **kwargs)

            guards.setattr(module, "open", guarded)
        result = _result(_upload(env[0], _zip(members)))
    assert result["manifest"][declaration] == manifest[declaration]
    assert result["missing"] == []
    assert {"reason": "declared_resource_unresolved", "id": logical_id} in result["warnings"]
    assert attempted == [] and canary.read_bytes() == secret


def test_declared_runtime_id_stays_an_unresolved_nonfile_declaration(env, monkeypatch):
    _blob, members, _stored, manifest = _exported(env)
    runtime_id = "runtime:opaque-historical-source"
    manifest["missing_at_pack_time"] = [runtime_id]
    members[MANIFEST] = json.dumps(manifest).encode()
    _no_execution(monkeypatch)
    result = _result(_upload(env[0], _zip(members)))
    assert result["manifest"]["missing_at_pack_time"] == [runtime_id]
    assert result["missing"] == [] and result["execution_coverage"] is None
    assert {"reason": "declared_resource_unresolved", "id": runtime_id} in result["warnings"]


def test_real_runtime_archive_and_execution_are_inspected_cold_without_machine_authority(
    env,
    monkeypatch,
):
    case = scientific(env)
    blob, _members_, stored, manifest = _exported(env, case["doc"])
    cold_export_only(env, monkeypatch, case)
    _no_execution(monkeypatch)
    before = (_files(env[1]), _files(config.config_dir()), _files(config.data_dir()))
    created = _temporary_directories(monkeypatch)
    result = _result(_upload(env[0], blob, pj=env[3]))
    assert result["doc"] == stored and result["manifest"] == manifest
    assert result["figure_snapshots"] == 1 and result["missing"] == []
    assert result["execution_coverage"] == manifest["execution"]["coverage"]
    assert result["execution_coverage"]["state"] == "incomplete"
    assert manifest["execution"]["runs"][0]["argv"] == ARGV
    assert {row["id"] for row in manifest["execution"]["contexts"][0]["basis"]} >= {
        "data/series.csv",
        "data/dynamic.csv",
        "selected/renamed.csv",
        "scripts/helper.py",
    }
    assert all(not Path(item["target"]).is_absolute() for item in result["resources"])
    assert any(item["target"] == SCRIPT for item in result["resources"])
    assert (_files(env[1]), _files(config.config_dir()), _files(config.data_dir())) == before
    assert created and all(not path.exists() for path in created)


def test_runtime_inner_semantics_are_checked_even_after_outer_resource_hashes_are_resigned(
    env,
    monkeypatch,
):
    case = scientific(env)
    _blob, members, stored, manifest = _exported(env, case["doc"])
    cold_export_only(env, monkeypatch, case)
    _no_execution(monkeypatch)
    created = _temporary_directories(monkeypatch)
    before = (_files(env[1]), _files(config.config_dir()), _files(config.data_dir()))
    attacks = {}
    for key in ("patchHash", "snapshotId"):
        changed = copy.deepcopy(stored)
        changed["canvases"][0]["objects"][0]["figureSnapshot"][key] = (
            "sha256:" if key == "patchHash" else ""
        ) + "0" * 64
        attacks["reference-" + key] = {**members, LAYOUT: json.dumps(changed).encode()}
    changed = copy.deepcopy(stored)
    changed["canvases"][0]["objects"][0]["overrides"] = [
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 9}
    ]
    attacks["reference-versus-current-patches"] = {**members, LAYOUT: json.dumps(changed).encode()}
    for row in manifest["assets"]:
        if row["kind"] != "figure_snapshot":
            continue
        name = row["path"]
        if "/blobs/" in name:
            attacks["resigned-" + Path(name).suffix] = _resign_member(
                members, name, b"not the archived artifact"
            )
        elif any(
            "/" + directory + "/" in name for directory in ("snapshots", "entries", "sources")
        ):
            payload = json.loads(members[name])
            payload["sha256"] = "0" * 64
            attacks["resigned-" + name.split("/")[-2]] = _resign_member(
                members, name, json.dumps(payload).encode()
            )
    changed = dict(members)
    orphan = next(row["path"] for row in manifest["assets"] if "/blobs/" in row["path"])
    orphan = orphan.rpartition("/")[0] + "/" + "0" * 64 + ".svg"
    assert orphan not in changed
    changed[orphan] = b"unreferenced but falsely content-addressed blob"
    orphan_manifest = copy.deepcopy(manifest)
    orphan_manifest["assets"].append(
        {
            "kind": "figure_snapshot",
            "path": orphan,
            "sha256": hashlib.sha256(changed[orphan]).hexdigest(),
            "bytes": len(changed[orphan]),
        }
    )
    changed[MANIFEST] = json.dumps(orphan_manifest).encode()
    attacks["unreferenced-invalid-blob"] = changed
    # These blobs satisfy both outer SHA/length and inner content addresses.
    # Only parsing every archived artifact can reject them, including orphans.
    for suffix, data in (
        ("svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
        ("pdf", b"%PDF-1.7\nnot a readable PDF document"),
    ):
        changed = dict(members)
        member = orphan.rpartition("/")[0] + "/" + hashlib.sha256(data).hexdigest() + "." + suffix
        assert member not in changed
        changed[member] = data
        changed_manifest = copy.deepcopy(manifest)
        changed_manifest["assets"].append(
            {
                "kind": "figure_snapshot",
                "path": member,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
        changed[MANIFEST] = json.dumps(changed_manifest).encode()
        attacks["semantic-orphan-" + suffix] = changed
    changed = copy.deepcopy(manifest)
    changed["execution"]["contexts"][0]["basis"][0]["sha256"] = "0" * 64
    attacks["execution-basis-versus-resources"] = {
        **members,
        MANIFEST: json.dumps(changed).encode(),
    }
    changed = copy.deepcopy(manifest)
    changed["execution"]["environment"] = {"SECRET": "must not become a machine setting"}
    attacks["execution-machine-fields"] = {**members, MANIFEST: json.dumps(changed).encode()}
    assert len(attacks) >= 13, "all existing archive kinds must actually be attacked"
    for label, changed in attacks.items():
        response = _upload(env[0], _zip(changed), pj=env[3])
        assert response.status_code == 422, (label, response.status_code, response.get_json())
        _invalid(response)
    assert (_files(env[1]), _files(config.config_dir()), _files(config.data_dir())) == before
    assert created and all(not path.exists() for path in created)


@pytest.mark.parametrize("which", ["chart_source", "file", "script"])
def test_all_resource_bytes_are_validated_not_just_the_metadata(env, monkeypatch, which):
    state = _bind(env[0]) if which == "chart_source" else None
    _blob, members, _doc, manifest = _exported(env, document((state, [])))
    if which == "chart_source":
        name = next(row["path"] for row in manifest["assets"] if row["kind"] == which)
    else:
        members = _add_resource(members, which, "resource.txt", b"approved bytes")
        name = ("scripts/" if which == "script" else "assets/") + "resource.txt"
    original = members[name]
    assert original
    members[name] = bytes([original[0] ^ 1]) + original[1:]
    assert len(members[name]) == len(original) and members[name] != original
    _no_execution(monkeypatch)
    created = _temporary_directories(monkeypatch)
    _invalid(_upload(env[0], _zip(members)), reason="resource_mismatch")
    assert created and all(not path.exists() for path in created)


@pytest.mark.parametrize(
    "name", ["../escape", "assets/../escape", "assets/C:/escape", "assets/a\\b"]
)
def test_unsafe_zip_paths_are_rejected_without_target_writes(env, monkeypatch, name):
    _blob, members, _doc, _manifest = _exported(env)
    members[name] = b"must remain in the untrusted upload"
    _no_execution(monkeypatch)
    before = _files(env[1])
    blob = _zip(members)
    if "\\" in name:
        # ZipInfo rewrites the platform separator on Windows at write time.
        # Keep the hostile spelling in both actual wire headers, not just the fixture dict.
        raw = name.encode()
        normalized = name.replace("\\", "/").encode()
        if raw not in blob:
            assert blob.count(normalized) == 2
            blob = blob.replace(normalized, raw)
        assert blob.count(raw) == 2
    _invalid(_upload(env[0], blob), reason="unsafe_member")
    assert _files(env[1]) == before


@pytest.mark.parametrize("alias", ["same.txt", "SAME.txt", "same.txt/child"])
def test_project_target_conflicts_are_rejected_across_package_namespaces(env, monkeypatch, alias):
    _blob, members, _doc, _manifest = _exported(env)
    members = _add_resource(members, "file", "same.txt", b"data")
    members = _add_resource(members, "script", alias, b"different script")
    _no_execution(monkeypatch)
    _invalid(_upload(env[0], _zip(members)), reason="project_target_conflict")


def test_bad_crc_is_not_hidden_by_unchanged_manifest(env, monkeypatch):
    _blob, members, _doc, _manifest = _exported(env)
    blob = bytearray(_zip(members, compression=zipfile.ZIP_STORED))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        info = archive.getinfo(LAYOUT)
        name_size, extra_size = struct.unpack_from("<HH", blob, info.header_offset + 26)
        offset = info.header_offset + 30 + name_size + extra_size
        assert blob[offset : offset + 1] == members[LAYOUT][:1]
        blob[offset] ^= 1
    _no_execution(monkeypatch)
    created = _temporary_directories(monkeypatch)
    _invalid(_upload(env[0], bytes(blob)), reason="invalid_member")
    assert created and all(not path.exists() for path in created)


@pytest.mark.parametrize(
    "field",
    [
        "max_archive_bytes",
        "max_members",
        "max_directory_bytes",
        "max_total_bytes",
        "max_layout_bytes",
        "max_manifest_bytes",
    ],
)
def test_input_budget_is_enforced_with_small_real_archives(env, monkeypatch, field):
    blob, _members_, _doc, _manifest = _exported(env)
    budget = packagebudget.input_budget()
    monkeypatch.setattr(packagebudget, "input_budget", lambda: replace(budget, **{field: 1}))
    _no_execution(monkeypatch)
    _invalid(_upload(env[0], blob), status=413)


@pytest.mark.parametrize("over", [False, True])
def test_multipart_without_content_length_remains_bounded(env, monkeypatch, over):
    blob, _members_, _doc, _manifest = _exported(env)
    budget = packagebudget.input_budget()
    limit = len(blob) - 1 if over else len(blob)
    monkeypatch.setattr(
        packagebudget, "input_budget", lambda: replace(budget, max_archive_bytes=limit)
    )
    _no_execution(monkeypatch)
    builder = EnvironBuilder(
        path=ENDPOINT, method="POST", data={"package": (io.BytesIO(blob), "upload")}
    )
    try:
        environ = builder.get_environ()
        environ.pop("CONTENT_LENGTH", None)
        environ["wsgi.input_terminated"] = True
        response = env[0].open(environ)
    finally:
        builder.close()
    if over:
        _invalid(response, status=413, reason="archive_budget")
    else:
        _result(response)


@pytest.mark.parametrize("known_length", [True, False])
@pytest.mark.parametrize("over", [False, True])
def test_actual_multipart_body_cap_includes_equality_with_legal_epilogue(
    env,
    monkeypatch,
    tmp_path,
    known_length,
    over,
):
    blob, _members_, _doc, _manifest = _exported(env)
    budget = packagebudget.input_budget()
    builder = EnvironBuilder(
        path=ENDPOINT,
        method="POST",
        data={"package": (io.BytesIO(blob), "upload")},
    )
    try:
        encoded = builder.get_environ()
        prefix = encoded["wsgi.input"].read()
        content_type = encoded["CONTENT_TYPE"]
    finally:
        builder.close()
    cap = len(blob) + packagebudget.INPUT_MULTIPART_BYTES
    padding = cap - len(prefix) + int(over)
    assert 0 < padding < packagebudget.INPUT_MULTIPART_BYTES
    body = prefix + b"e" * padding
    assert len(body) == cap + int(over)
    (tmp_path / "multipart-wire.bin").write_bytes(body)
    (tmp_path / "wire-facts.json").write_text(
        json.dumps(
            {
                "archive_bytes": len(blob),
                "actual_body_bytes": len(body),
                "request_limit": cap,
                "known_content_length": known_length,
                "over_by_one": over,
                "form_memory_budget_unchanged": packagebudget.INPUT_MULTIPART_BYTES,
                "content_type": content_type,
            }
        ),
        encoding="utf-8",
    )
    _no_execution(monkeypatch)

    def send():
        request_builder = EnvironBuilder(
            path=ENDPOINT,
            method="POST",
            input_stream=io.BytesIO(body),
            content_type=content_type,
            content_length=len(body),
        )
        try:
            environ = request_builder.get_environ()
            if not known_length:
                environ.pop("CONTENT_LENGTH", None)
                environ["wsgi.input_terminated"] = True
            return env[0].open(environ)
        finally:
            request_builder.close()

    # Same actual multipart wire must first be valid with the original budget;
    # this separates a LimitedStream equality bug from invalid epilogue/memory use.
    _result(send())
    monkeypatch.setattr(
        packagebudget,
        "input_budget",
        lambda: replace(budget, max_archive_bytes=len(blob)),
    )
    response = send()
    if over:
        _invalid(response, status=413, reason="archive_budget")
    else:
        _result(response)


def test_missing_upload_uses_the_existing_missing_file_code(env):
    response = env[0].post(ENDPOINT, data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.get_json()["code"] == "package_file_missing"


@pytest.mark.parametrize(
    "shape", ["duplicate", "extra_file", "extra_form", "unknown_file", "json", "broken_boundary"]
)
def test_upload_envelope_is_closed(env, shape):
    blob, _members_, _doc, _manifest = _exported(env)
    if shape == "json":
        response = env[0].post(ENDPOINT, json={"package": "not multipart"})
    elif shape == "broken_boundary":
        response = env[0].post(
            ENDPOINT,
            data=b"not a multipart boundary",
            content_type="multipart/form-data; boundary=expected",
        )
    else:
        data = MultiDict([("package", (io.BytesIO(blob), "upload"))])
        if shape == "unknown_file":
            data = MultiDict([("archive", (io.BytesIO(blob), "upload"))])
        elif shape == "duplicate":
            data.add("package", (io.BytesIO(blob), "upload2"))
        elif shape == "extra_file":
            data.add("other", (io.BytesIO(b"extra"), "other"))
        else:
            data.add("project", "must not install or bind the document")
        response = env[0].post(ENDPOINT, data=data, content_type="multipart/form-data")
    _invalid(response, status=400, reason="invalid_upload")


@pytest.mark.parametrize("address", ["query", "header"])
def test_unknown_project_does_not_fall_back_to_the_open_project(env, address):
    blob, _members_, _doc, _manifest = _exported(env)
    before = _files(env[1])
    response = (
        _upload(env[0], blob, pj="unknown-project")
        if address == "query"
        else _upload(env[0], blob, headers={"X-Tavotto-Project": "unknown-project"})
    )
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "no_project"
    assert _files(env[1]) == before


def test_new_inspection_route_is_behind_the_existing_authentication_guard(env, monkeypatch):
    blob, _members_, _doc, _manifest = _exported(env)
    state = security.SessionState("inspection-nonce", api_secret="inspection-api-secret")
    monkeypatch.setitem(m.app.config, security.STATE_KEY, state)
    response = _upload(env[0], blob)
    assert response.status_code == 401 and response.get_json()["code"] == "session_auth_required"
    response = _upload(env[0], blob, headers={"X-Tavotto-Auth": state.status_token})
    assert response.status_code == 401
    _result(_upload(env[0], blob, headers={"X-Tavotto-Auth": "inspection-api-secret"}))


def test_nonobject_decorations_keep_the_existing_document_skeleton_compatibility(env):
    _blob, members, stored, _manifest = _exported(env)
    stored["canvases"][0]["objects"] = [None, "old decorative payload", 7, {"type": "shape"}]
    members[LAYOUT] = json.dumps(stored).encode()
    assert _result(_upload(env[0], _zip(members)))["doc"] == stored


@pytest.mark.parametrize("bad", [None, "", True, [], {}])
def test_panel_without_a_valid_source_id_is_stably_rejected(env, bad):
    _blob, members, stored, _manifest = _exported(env)
    value = {"type": "panel"}
    if bad is not None:
        value["fileId"] = bad
    stored["canvases"][0]["objects"] = [value]
    members[LAYOUT] = json.dumps(stored).encode()
    _invalid(_upload(env[0], _zip(members)))


@pytest.mark.parametrize("bad", [{}, [], "not a ref", True, {"version": 1, "sourceId": "x.svg"}])
def test_malformed_figure_reference_is_stably_rejected_instead_of_crashing(env, bad):
    _blob, members, stored, _manifest = _exported(env)
    stored["canvases"][0]["objects"] = [{"type": "panel", "fileId": "x.svg", "figureSnapshot": bad}]
    members[LAYOUT] = json.dumps(stored).encode()
    _invalid(_upload(env[0], _zip(members)))


def test_snapshot_count_comes_from_checked_archives_not_the_manifest_claim(env):
    _blob, members, _stored, manifest = _exported(env)
    manifest["figure_snapshots"] = 999
    members[MANIFEST] = json.dumps(manifest).encode()
    result = _result(_upload(env[0], _zip(members)))
    assert result["manifest"]["figure_snapshots"] == 999
    assert result["figure_snapshots"] == 0


def test_unavailable_data_directory_is_a_stable_503_without_touching_user_drafts(
    env,
    monkeypatch,
    tmp_path,
):
    blob, _members_, _stored, _manifest = _exported(env)
    data_root = config.data_dir()
    config_root = config.config_dir()
    before = (_files(env[1]), _files(config_root), _files(data_root))
    blocked = tmp_path / "cannot-create-inspection-data"
    original = Path.mkdir
    attempted = []

    def denied(path, *args, **kwargs):
        if path == blocked:
            attempted.append(path)
            raise PermissionError(errno.EACCES, "fixture denies the actual data-directory mkdir")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(config, "data_dir", lambda: blocked)
    monkeypatch.setattr(Path, "mkdir", denied)
    _invalid(_upload(env[0], blob), status=503, reason="inspection_unavailable")
    assert attempted == [blocked] and not blocked.exists()
    assert (_files(env[1]), _files(config_root), _files(data_root)) == before
