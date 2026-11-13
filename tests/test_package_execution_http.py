"""正式 v4 包的执行事实、真实资源与冷参数消费；不冒充项目安装。"""

from __future__ import annotations

import builtins
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tavotto.engine import (
    brand,
    chartpackage,
    databinding,
    documentpackage,
    figcapture,
    inputremap,
    launchctx,
    packagebudget,
    pool,
    receipt,
    registry,
    runconfig,
    workdir,
)
from test_chart_native_api import SOURCE, _bind
from test_chart_package_http import (
    document,
    env as _package_env,
    freeze_named,
    panel,
    post,
    project as _native_project,
)

env = _package_env
project = _native_project
REPO = Path(__file__).resolve().parents[1]
SCRIPT = "scripts/figure.py"
ARGV = ["--factor", "2", "--extra", "data/dynamic.csv", "--", "", "中文 空格", "-3"]
SCIENTIFIC = """import argparse
import csv
import matplotlib.pyplot as plt
from helper import scale_values
p = argparse.ArgumentParser()
p.add_argument('--factor', type=float, default=1)
p.add_argument('--extra', required=True)
p.add_argument('--token')
p.add_argument('labels', nargs='*')
args = p.parse_args()
def values(stream):
    return [float(row['y']) for row in csv.DictReader(stream)]
with open('data/series.csv', encoding='utf-8') as stream:
    primary = values(stream)
with open(args.extra, encoding='utf-8') as stream:
    dynamic = values(stream)
with open('legacy/renamed.csv', encoding='utf-8') as stream:
    remapped = values(stream)
with open('external/only.csv', encoding='utf-8') as stream:
    external = values(stream)
fig, ax = plt.subplots(figsize=(2, 1))
ax.plot(scale_values(primary, args.factor))
ax.set_title(str(sum(dynamic + remapped + external)))
fig.savefig('result.svg')
"""


def scientific(env, *, argv=ARGV, sensitive=False):
    """HTTP probe 真执行；沿实际 worker 回执记录 basis，再走正式命名保存。"""
    client, root, _output, pid = env
    sources = {
        SCRIPT: SCIENTIFIC
        if argv
        else SCIENTIFIC.replace("required=True", "default='data/dynamic.csv'"),
        "scripts/helper.py": "def scale_values(values, factor):\n    return [v * factor for v in values]\n",
        "data/series.csv": "x,y\n0,1\n1,3\n",
        "data/dynamic.csv": "x,y\n0,7\n",
        "selected/renamed.csv": "x,y\n0,11\n",
    }
    for name, text in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    outside = root.parent / "EXTERNAL_N3C_DO_NOT_ARCHIVE.csv"
    outside.write_text("x,y,EXTERNAL_DATA_CANARY_N3C\n0,13,never_archive\n", encoding="utf-8")
    workdir.set_mode(root, workdir.MODE_PROJECT_ROOT)
    inputremap.add_rule(
        root,
        {"kind": "file", "from": "legacy/renamed.csv", "to": str(root / "selected/renamed.csv")},
    )
    inputremap.add_rule(root, {"kind": "file", "from": "external/only.csv", "to": str(outside)})
    binding = databinding.binding_for(root / SCRIPT, root, workdir.MODE_PROJECT_ROOT)
    assert "data/series.csv" in binding["expected"]
    if argv:
        assert "data/dynamic.csv" not in binding["expected"]
    response = client.post(
        "/api/registry/probe?pj=" + pid,
        json={"script": SCRIPT, "argv": list(argv), "argv_sensitive": sensitive, "cost": "light"},
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json().get("registered"), response.get_json()
    run = runconfig.default_selection(root, SCRIPT)
    assert (run.argv if run else ()) == tuple(argv)
    registered = registry.open_registry(root)
    assert registered.stems_of(SCRIPT) == ["result"]
    worker, built = pool.build(
        SCRIPT, str(root), registered.entries()[SCRIPT]["entry"], run=run, allow_project_env=False
    )
    plane = (
        receipt.CONTROL_PLANE_WORKERD
        if type(worker).__name__ == "WorkerdWorker"
        else receipt.CONTROL_PLANE_PYTHON
    )
    actual = receipt.from_worker(
        worker, built, control_plane=plane, grant=workdir.grant_for(root), binding=binding
    )
    assert actual.runtime_rejected is None and actual.completeness == "complete"
    assert actual.inputs["observation"] == "partial"
    assert launchctx.record_success(root, SCRIPT, actual, run=run, update_default=True)
    record = runconfig.launch_record(root, SCRIPT)
    expected = {"data/series.csv", "data/dynamic.csv", "selected/renamed.csv", "scripts/helper.py"}
    assert expected <= record["source"]["basis"].keys()
    assert all("EXTERNAL" not in name for name in record["source"]["basis"])
    runtime = figcapture.runtime_asset_id(SCRIPT, "result", run.config_id if run else "")
    doc = document()
    doc["canvases"][0]["objects"].append(panel(file=runtime))
    frozen = freeze_named(env, doc)
    reference = frozen["canvases"][0]["objects"][0]["figureSnapshot"]
    assert reference["executionOwner"] == {
        "script": SCRIPT,
        "run_config": run.config_id if run else None,
    }
    # 保存和探针不能把旧的、参数不同的启动依据冒充这次真实执行。
    retained = runconfig.launch_record(root, SCRIPT)
    assert retained["run_config"] == record["run_config"]
    assert retained["source"] == record["source"]
    return {
        "doc": frozen,
        "runtime": runtime,
        "run": run,
        "argv": list(argv),
        "record": record,
        "registry": registered.entries(),
        "outside": outside,
        "source_bytes": {name: (root / name).read_bytes() for name in sources},
    }


def cold_export_only(env, monkeypatch, case=None):
    """只关本夹具的 worker；导出不能取得或新建任何用户脚本会话。"""
    _client, root, _output, _pid = env
    pool.shutdown_all(figures_dir=str(root), wait=True)

    def forbidden(*_args, **_kwargs):
        pytest.fail("package export executed a user script")

    for name in ("get", "build", "one_shot"):
        monkeypatch.setattr(pool, name, forbidden)
    if case is None:
        return
    outside = case["outside"].resolve()
    for module in (builtins, io):
        original = module.open

        def guarded(file, *args, _original=original, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                assert Path(file).resolve() != outside, "export read external input bytes"
            return _original(file, *args, **kwargs)

        monkeypatch.setattr(module, "open", guarded)


def read(env, response, *, has_execution=True):
    _client, _root, output, _pid = env
    body = response.get_json()
    assert response.status_code == 200, body
    keys = {"name", "url", "assets", "missing", "document_format", "chart_sources"}
    assert set(body) == keys | ({"execution_coverage"} if has_execution else set())
    assert body["name"].endswith(brand.PACKAGE_EXT)
    blob = (output / body["name"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.testzip() is None
        members = {name: archive.read(name) for name in archive.namelist()}
    budget = packagebudget.output_budget({name: len(data) for name, data in members.items()})
    with chartpackage.inspect_chart_package(blob, budget=budget) as ready:
        manifest, stored = copy.deepcopy(ready.manifest), copy.deepcopy(ready.document)
        for item in ready.resources:
            row = item.record
            assert row["sha256"] == hashlib.sha256(members[row["path"]]).hexdigest()
        if has_execution:
            assert body["execution_coverage"] == manifest["execution"]["coverage"]
            assert body["execution_coverage"]["state"] == "incomplete"
        else:
            assert "execution" not in manifest
        assert body["assets"] == sum(row["kind"] == "file" for row in manifest["assets"])
    return body, stored, manifest, members, blob


def reasons(manifest):
    return {
        row["reason"]
        for row in manifest["execution"]["coverage"]["issues"]
        if row["script"] == SCRIPT
    }


def corrupt_execution(members, transform):
    changed = dict(members)
    manifest = json.loads(changed[documentpackage.MANIFEST_MEMBER])
    transform(manifest["execution"])
    changed[documentpackage.MANIFEST_MEMBER] = json.dumps(manifest, ensure_ascii=False).encode(
        "utf-8"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in changed.items():
            archive.writestr(name, data)
    budget = packagebudget.output_budget({name: len(data) for name, data in changed.items()})
    return output.getvalue(), budget


def test_real_public_probe_observes_static_and_dynamic_data_and_helper_without_claiming_complete(
    env,
):
    case = scientific(env)
    assert case["record"]["profile"] == "safe"
    assert case["record"]["cwd_mode"] == "project_root"
    assert case["run"].config_id in case["runtime"]
    for name, digest in case["record"]["source"]["basis"].items():
        assert hashlib.sha256(case["source_bytes"][name]).hexdigest() == digest


def test_http_real_execution_package_strict_reader_and_new_root_preserve_exact_parameters(
    env, monkeypatch, tmp_path
):
    case = scientific(env)
    cold_export_only(env, monkeypatch, case)
    body, stored, manifest, members, blob = read(env, post(env, case["doc"]))
    execution = manifest["execution"]
    assert stored == case["doc"] and body["missing"] == []
    assert execution["registry"]["scripts"] == {SCRIPT: case["registry"][SCRIPT]}
    assert execution["runs"] == [{"id": case["run"].config_id, "script": SCRIPT, "argv": ARGV}]
    assert manifest["runtime_assets"][0]["id"] == case["runtime"]
    assert execution["contexts"][0]["cwd_mode"] == "project_root"
    assert execution["contexts"][0]["run_config_known"] is True
    assert {row["id"]: row["sha256"] for row in execution["contexts"][0]["basis"]} == case[
        "record"
    ]["source"]["basis"]
    assert execution["remaps"] == [
        {"kind": "file", "from": "legacy/renamed.csv", "to_resource": "selected/renamed.csv"}
    ]
    assert {"dependency_set_undeclared", "external_input_requires_relink"} <= reasons(manifest)
    assert not {"source_basis_missing", "source_basis_stale", "run_config_unresolved"} & reasons(
        manifest
    )
    for name, source in case["source_bytes"].items():
        prefix = "scripts/" if name.endswith(".py") else "assets/"
        assert members[prefix + name] == source
    assert not any(b"EXTERNAL_DATA_CANARY_N3C" in value for value in members.values())
    encoded = json.dumps(execution, ensure_ascii=False)
    assert str(case["outside"]) not in encoded and str(env[1]) not in encoded
    assert all(set(row) == {"kind", "from", "to_resource"} for row in execution["remaps"])

    # 正式 reader 的两个真实消费者：形状入口和资源关系入口都必须实际拒绝。
    bad, budget = corrupt_execution(
        members, lambda value: value.update(environment={"TOKEN": "private"})
    )
    with pytest.raises(documentpackage.PackageError) as error:
        with chartpackage.inspect_chart_package(bad, budget=budget):
            pytest.fail("strict reader accepted machine execution fields")
    assert error.value.reason == "invalid_execution"

    def change_basis(value):
        value["contexts"][0]["basis"][0]["sha256"] = "d" * 64

    bad, budget = corrupt_execution(members, change_basis)
    with pytest.raises(documentpackage.PackageError) as error:
        with chartpackage.inspect_chart_package(bad, budget=budget):
            pytest.fail("strict reader accepted contradictory source bytes")
    assert error.value.reason == "execution_resource_mismatch"

    # 测试层按生产资源计划复制，明确不把它当作已有安装 API。
    cold = tmp_path / "cold_project"
    cold.mkdir()
    grant = workdir.grant_for(cold)
    budget = packagebudget.output_budget({name: len(data) for name, data in members.items()})
    with chartpackage.inspect_chart_package(blob, budget=budget) as ready:
        for relative, item in chartpackage.plan_project_resources(ready):
            target = cold / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.source.read_bytes())
    imported = runconfig.import_portable(cold, execution["runs"])
    assert imported[0].id == case["run"].config_id
    restored_registry = registry.Registry()
    restored_registry.load_data(execution["registry"])
    from tavotto.engine import runtimeasset

    restored = runtimeasset.resolve(case["runtime"], restored_registry)
    assert restored["script"] == SCRIPT and restored["run_config"] == case["run"].config_id
    selected = runtimeasset.run_selection(cold, restored)
    assert selected.config_id == case["run"].config_id and selected.argv == tuple(ARGV)
    assert runconfig.default_selection(cold, SCRIPT) is None
    assert runconfig.launch_record(cold, SCRIPT) is None
    assert not workdir.decided(cold) and workdir.grant_for(cold) == grant
    probe = """
import json,sys
sys.path.insert(0,sys.argv[1])
from tavotto.engine import runconfig
root,reference,script=sys.argv[2:]
selected=runconfig.selection(root,reference,script=script)
assert runconfig.default_selection(root,script) is None
assert runconfig.launch_record(root,script) is None
assert 'matplotlib' not in sys.modules
print(json.dumps({'id':selected.config_id,'argv':selected.argv},ensure_ascii=False))
"""
    child = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-I",
            "-c",
            probe,
            str(REPO / "src"),
            str(cold),
            case["run"].config_id,
            SCRIPT,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {"id": case["run"].config_id, "argv": ARGV}


def test_http_missing_run_configuration_stays_an_unresolved_reference_not_empty_argv(
    env, monkeypatch
):
    case = scientific(env)
    cold_export_only(env, monkeypatch, case)
    runconfig.store_path(env[1]).unlink()
    body, _stored, manifest, _members, _blob = read(env, post(env, case["doc"]))
    assert body["missing"] == []
    assert manifest["execution"]["runs"] == []
    assert manifest["execution"]["contexts"][0]["run_config"] == case["run"].config_id
    assert "run_config_missing" in reasons(manifest)


def test_http_sensitive_run_configuration_never_leaks_tokens_even_after_forget(env, monkeypatch):
    secret = "SENSITIVE_ARGV_CANARY_N3C_7cdd"
    argv = ["--token", secret, *ARGV]
    case = scientific(env, argv=argv, sensitive=True)
    cold_export_only(env, monkeypatch, case)
    for forget in (False, True):
        if forget:
            runconfig.forget_secrets()
        body, _stored, manifest, members, _blob = read(env, post(env, case["doc"]))
        assert body["missing"] == []
        assert manifest["execution"]["runs"] == []
        assert manifest["execution"]["contexts"][0]["run_config"] == case["run"].config_id
        assert "sensitive_run_config" in reasons(manifest)
        assert all(secret.encode() not in value for value in members.values())


def test_http_stale_successful_basis_never_claims_current_helper_as_old_bytes(env, monkeypatch):
    case = scientific(env)
    cold_export_only(env, monkeypatch, case)
    (env[1] / "scripts/helper.py").write_text(
        "def scale_values(values, factor):\n    return [999]\n", encoding="utf-8"
    )
    body, _stored, manifest, members, _blob = read(env, post(env, case["doc"]))
    assert body["missing"] == []
    assert "source_basis_stale" in reasons(manifest)
    assert "scripts/helper.py" not in {
        row["id"] for row in manifest["execution"]["contexts"][0]["basis"]
    }
    assert "scripts/scripts/helper.py" not in members


def test_http_missing_observed_helper_is_a_known_missing_resource_not_an_unknown_dependency(
    env, monkeypatch
):
    case = scientific(env)
    cold_export_only(env, monkeypatch, case)
    (env[1] / "scripts/helper.py").unlink()
    body, _stored, manifest, members, _blob = read(env, post(env, case["doc"]))
    assert body["missing"] == ["scripts/helper.py"]
    assert "resource_missing" in reasons(manifest)
    assert "scripts/scripts/helper.py" not in members


def test_http_paramfree_runtime_and_unknown_disk_of_same_script_keep_both_contexts(
    env, monkeypatch
):
    case = scientific(env, argv=[])
    (env[1] / "result.svg").write_bytes(SOURCE)
    mixed = copy.deepcopy(case["doc"])
    mixed["canvases"][0]["objects"].append(panel(file="result.svg", number=2))
    cold_export_only(env, monkeypatch, case)
    body, stored, manifest, _members, _blob = read(env, post(env, mixed))
    assert body["missing"] == []
    assert len(stored["canvases"][0]["objects"]) == 2
    assert manifest["execution"]["runs"] == []
    contexts = manifest["execution"]["contexts"]
    assert {(row["script"], row["run_config"], row["run_config_known"]) for row in contexts} == {
        (SCRIPT, None, True),
        (SCRIPT, None, False),
    }
    assert "run_config_unresolved" in reasons(manifest)
    unknown = next(row for row in contexts if row["run_config_known"] is False)
    assert unknown["basis"] == []


def test_http_chart_only_and_legacy_without_scripts_keep_optional_execution_fields_absent(
    env, monkeypatch
):
    client, _root, output, pid = env
    cold_export_only(env, monkeypatch)
    read(env, post(env), has_execution=False)
    chart = _bind(client)
    read(env, post(env, document((chart, []))), has_execution=False)
    legacy = document()
    legacy["schema"] = 3
    response = client.post("/api/package?pj=" + pid, json={"stem": "legacy_n3c", "doc": legacy})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert set(body) == {"name", "url", "assets", "missing"}
    with zipfile.ZipFile(output / body["name"]) as archive:
        assert json.loads(archive.read("layout.json")) == legacy
        assert "execution" not in json.loads(archive.read("package_manifest.json"))
