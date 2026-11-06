"""v4 附件包：精确 INPUT、准备/安装隔离与迁移后的真实冷重放。"""

from __future__ import annotations

import base64
import copy
import errno
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from tavotto import chartbundle
from tavotto.engine import (
    atomicio,
    chartpackage as packages,
    config,
    documentassets as assets,
    documentpackage as container,
)
from test_chart_document_assets import SCENE_DIGEST, SOURCE, SOURCE_ID, chart, document

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def budget():
    return container.PackageBudget(
        max_archive_bytes=1024 * 1024,
        max_members=32,
        max_directory_bytes=64 * 1024,
        max_total_bytes=256 * 1024,
        max_layout_bytes=64 * 1024,
        max_manifest_bytes=16 * 1024,
    )


@pytest.fixture
def prepared_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "数据暂存"))
    root = tmp_path / "原项目" / config.PROJECT_STORE_DIRNAME
    source = chartbundle.embed_chart(SOURCE, SOURCE, chart())
    ref = assets.freeze_source(root, SOURCE_ID, source, scene_digest=SCENE_DIGEST)
    doc = document(ref)
    # 保留未规范化的完整编辑序列及未知正文字段；此处仅核包，不冒称渲染通过。
    panel = doc["canvases"][0]["objects"][0]
    panel["overrides"] = [
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#e32636"},
    ]
    doc["name"] = '中文迁移\n含引号"'
    return root, doc, source


def write(tmp_path, budget, prepared_input, **kwargs):
    root, doc, source = prepared_input
    path = tmp_path / "携带完整来源.tavotto"
    packages.write_chart_package(path, doc, root, budget=budget, **kwargs)
    return path, doc, source


def rows_and_members(path):
    with zipfile.ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members[container.MANIFEST_MEMBER])
    return manifest, members


def archive_bytes(manifest, members):
    members = {**members, container.MANIFEST_MEMBER: atomicio.dumps_json(manifest)}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def resource(kind, resource_id, data, *, path=None):
    record = {
        "kind": kind,
        "path": path or ("scripts/" if kind == "script" else "assets/") + resource_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    if kind in {"file", "script"}:
        record["id"] = resource_id
    return packages.ResourceFile(record, data)


def no_replay(_document, _storage_root):
    """仅用于安装事务单元测试；真实冷重放由另一个独立进程用例覆盖。"""


def test_inspection_preserves_full_input_and_document_without_touching_project(
    tmp_path, budget, prepared_input
):
    path, doc, source = write(tmp_path, budget, prepared_input)
    before = copy.deepcopy(doc)
    target = tmp_path / "目标项目"
    target.mkdir()
    original = target / "同名.svg"
    original.write_bytes(b"user original")
    with path.open("rb") as upload, packages.inspect_chart_package(upload, budget=budget) as ready:
        stage = ready.storage_root.parent
        assert ready.document == before
        assert ready.document_bytes == atomicio.dumps_json(doc)
        ref = doc["canvases"][0]["objects"][0]["chartSource"]
        loaded = assets.load_source(ready.storage_root, ref)
        assert loaded.input_svg == source and loaded.source_svg == SOURCE
        assert source != SOURCE and b"\r\n" in SOURCE
        assert len(ready.resources) == 1
        assert ready.resources[0].source.read_bytes() == source
        ready.document["canvases"].clear()
        ready.manifest["assets"].clear()
        ready.resources[0].record.clear()
        assert ready.document == before
        assert ready.manifest["assets"]
        assert sorted(p.name for p in target.iterdir()) == ["同名.svg"]
    assert upload.closed
    assert original.read_bytes() == b"user original"
    assert not stage.exists()
    assert not list(config.data_dir().glob("chart-package-*"))
    with pytest.raises(container.PackageError, match="closed"):
        _ = ready.document
    with pytest.raises(container.PackageError) as error:
        packages.install_chart_sources(ready, target, verify=no_replay)
    assert error.value.reason == "prepared_closed"
    assert doc == before


def test_install_requires_verification_before_any_target_write(tmp_path, budget, prepared_input):
    path, doc, source = write(tmp_path, budget, prepared_input)
    target = tmp_path / "目标项目" / config.PROJECT_STORE_DIRNAME
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        with pytest.raises(TypeError):
            packages.install_chart_sources(ready, target)
        with pytest.raises(container.PackageError, match="callback"):
            packages.install_chart_sources(ready, target, verify=None)
        assert not target.exists()
        calls = []

        def verify(actual, stage):
            assert actual == doc and not target.exists()
            ref = actual["canvases"][0]["objects"][0]["chartSource"]
            assert assets.load_source(stage, ref).input_svg == source
            calls.append(stage)

        result = packages.install_chart_sources(ready, target, verify=verify)
        assert len(calls) == 1
        assert result["document"] == doc
        assert result["remaining_resources"] == []
        ref = doc["canvases"][0]["objects"][0]["chartSource"]
        assert assets.load_source(target, ref).input_svg == source
        assert list(target.glob("*.json")) == []


@pytest.mark.parametrize("mode", ["raises", "mutates_document", "mutates_input", "mutates_extra"])
def test_failed_or_tampering_verification_publishes_nothing(tmp_path, budget, prepared_input, mode):
    extra = resource("file", "图.pdf", b"approved bytes")
    path, _, _ = write(tmp_path, budget, prepared_input, resources=[extra])
    target = tmp_path / "目标"
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:

        def bad_verify(doc, _stage):
            if mode == "raises":
                raise RuntimeError("pixel comparison failed")
            if mode == "mutates_document":
                doc["name"] = "different replay subject"
            else:
                index = 0 if mode == "mutates_input" else 1
                ready.resources[index].source.write_bytes(b"tampered")

        with pytest.raises((RuntimeError, container.PackageError, assets.DocumentAssetError)):
            packages.install_chart_sources(ready, target, verify=bad_verify)
        assert not target.exists()
    assert not list(config.data_dir().glob("chart-package-*"))


def test_extra_resources_are_inert_explicit_and_not_installed(tmp_path, budget, prepared_input):
    marker = tmp_path / "SHOULD_NOT_RUN"
    script = f"from pathlib import Path\nPath({str(marker)!r}).touch()".encode()
    extras = [
        resource("file", "子目录/图.pdf", b"%PDF-1.7 arbitrary inert bytes"),
        resource("file", "图.png", b"inert bitmap"),
        resource("script", "计算.py", script),
        resource("figure_snapshot", "unused", b"snapshot", path="assets/approved_snapshot/blob"),
        resource("file", "empty.dat", b""),
    ]
    prepared_input[1]["canvases"][0]["objects"].extend(
        [
            {"type": "panel", "fileId": "子目录/图.pdf", "fileKind": "pdf", "overrides": []},
            {"type": "panel", "fileId": "图.png", "fileKind": "png"},
            {"type": "panel", "fileId": "runtime:opaque", "fileKind": "runtime"},
        ]
    )
    metadata = {"scripts": ["计算.py"], "runtime_assets": [{"id": "runtime:opaque"}]}
    path, doc, _ = write(tmp_path, budget, prepared_input, resources=extras, metadata=metadata)
    assert not marker.exists()
    target = tmp_path / "目标"
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        assert ready.manifest["runtime_assets"] == metadata["runtime_assets"]
        for actual, expected in zip(ready.resources[1:], extras, strict=True):
            assert actual.record == expected.record
            assert actual.source.read_bytes() == expected.source
            assert actual.source.name.isdecimal()
        result = packages.install_chart_sources(ready, target, verify=no_replay)
        assert result["remaining_resources"] == [item.record for item in extras]
        assert result["document"] == doc
    assert not marker.exists()
    assert sorted(p.name for p in target.iterdir()) == [assets.CHART_SOURCES_DIRNAME]


def test_install_failure_after_one_publication_keeps_immutable_blob_and_old_body(
    tmp_path, budget, prepared_input, monkeypatch
):
    root, doc, first_input = prepared_input
    second_source = SOURCE.replace(b"blue", b"green")
    second_digest = hashlib.sha256(second_source).hexdigest()
    second_ref = assets.freeze_source(root, "second.svg", second_source, scene_digest=second_digest)
    second_panel = {
        "type": "panel",
        "fileKind": "svg",
        "fileId": "second.svg",
        "chart": chart(second_source),
        "chartSource": second_ref,
    }
    doc["canvases"][0]["objects"].append(second_panel)
    path, _, _ = write(tmp_path, budget, prepared_input)
    target = tmp_path / "目标"
    target.mkdir()
    body = target / "old.json"
    body.write_bytes(b"old body")
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        real_freeze = assets.freeze_source
        count = 0

        def interrupted(storage_root, *args, **kwargs):
            nonlocal count
            if storage_root == target:
                count += 1
                if count == 2:
                    raise OSError(errno.ENOSPC, "second immutable attachment could not publish")
            return real_freeze(storage_root, *args, **kwargs)

        monkeypatch.setattr(assets, "freeze_source", interrupted)
        with pytest.raises(OSError, match="second immutable"):
            packages.install_chart_sources(ready, target, verify=no_replay)
    first_ref = doc["canvases"][0]["objects"][0]["chartSource"]
    assert assets.load_source(target, first_ref).input_svg == first_input
    assert not assets.source_path(target, second_ref).exists()
    assert body.read_bytes() == b"old body"
    assert len(list((target / assets.CHART_SOURCES_DIRNAME).iterdir())) == 1
    assert not list(config.data_dir().glob("chart-package-*"))


def test_no_chart_sources_still_keeps_generic_v4_resources_available(tmp_path, budget, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "数据"))
    doc = {"schema": 4, "canvases": [{"objects": [{"type": "panel", "fileId": "image.png"}]}]}
    extra = resource("file", "image.png", b"inert image")
    path = tmp_path / "ordinary.tavotto"
    result = packages.write_chart_package(
        path, doc, tmp_path / "absent", budget=budget, resources=[extra]
    )
    assert result["chart_sources"] == 0
    target = tmp_path / "目标"
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        assert ready.storage_root.is_dir()
        result = packages.install_chart_sources(ready, target, verify=no_replay)
        assert result["remaining_resources"] == [extra.record]
        assert result["document"] == doc
    assert not target.exists(), "普通资源没有被本 chart 附件安装器暗中安装"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m, _: m.pop("assets"),
        lambda m, _: m["assets"].clear(),
        lambda m, _: m["assets"][0].update(sha256="0" * 64),
        lambda m, _: m["assets"][0].update(bytes=True),
        lambda m, _: m["assets"][0].update(bytes=0),
        lambda m, _: m["assets"][0].update(kind=[]),
        lambda m, _: m["assets"][0].update(kind="unknown"),
        lambda m, _: m["assets"][0].update(extra="unknown"),
        lambda m, _: m["assets"].append(copy.deepcopy(m["assets"][0])),
        lambda m, _: m.update(unrecognized=True),
        lambda m, _: m.update(document_format="v3"),
        lambda m, d: d.pop(m["assets"][0]["path"]),
        lambda m, d: d.update({m["assets"][0]["path"]: b"changed bytes"}),
        lambda _m, d: d.update({"scripts/not-declared.py": b"print('never run')"}),
    ],
)
def test_bad_package_is_rejected_and_own_temporary_files_are_removed(
    tmp_path, budget, prepared_input, mutate
):
    path, _, _ = write(tmp_path, budget, prepared_input)
    manifest, members = rows_and_members(path)
    mutate(manifest, members)
    with pytest.raises((container.PackageError, assets.DocumentAssetError)):
        with packages.inspect_chart_package(archive_bytes(manifest, members), budget=budget):
            pytest.fail("invalid package became prepared")
    assert not list(config.data_dir().glob("chart-package-*"))


@pytest.mark.parametrize(
    "record_change",
    [
        {"id": "../escape", "path": "assets/../escape"},
        {"id": "a//b", "path": "assets/a//b"},
        {"id": "/absolute", "path": "assets//absolute"},
        {"id": "C:\\escape", "path": "assets/C:\\escape"},
        {"id": "different"},
        {"path": "scripts/a.pdf"},
        {"sha256": "A" * 64},
        {"bytes": -1},
        {"bytes": 1.0},
        {"kind": "figure_snapshot"},
    ],
)
def test_closed_extra_records_reject_wrong_mapping_before_publication(
    tmp_path, budget, prepared_input, record_change
):
    item = resource("file", "a.pdf", b"pdf")
    item.record.update(record_change)
    root, doc, _ = prepared_input
    output = tmp_path / "result.tavotto"
    with pytest.raises(container.PackageError):
        packages.write_chart_package(output, doc, root, budget=budget, resources=[item])
    assert not output.exists()
    assert not list(tmp_path.glob(".*.prepared"))


def test_extra_cannot_override_derived_chart_row(tmp_path, budget, prepared_input):
    path, doc, source = write(tmp_path, budget, prepared_input)
    manifest, _ = rows_and_members(path)
    item = packages.ResourceFile(manifest["assets"][0], source)
    with pytest.raises(container.PackageError, match="derived"):
        packages.write_chart_package(
            tmp_path / "new.tavotto", doc, prepared_input[0], budget=budget, resources=[item]
        )


def test_file_resource_cannot_alias_the_owned_source_namespace(tmp_path, budget):
    doc = {"schema": 4, "canvases": [{"objects": []}]}
    source_id = f"{config.PROJECT_STORE_DIRNAME.upper()}/{assets.CHART_SOURCES_DIRNAME}/foreign.svg"
    item = resource("file", source_id, b"ordinary asset would replace an owned attachment")
    with pytest.raises(container.PackageError, match="namespace"):
        packages.write_chart_package(
            tmp_path / "bad.tavotto", doc, tmp_path / "unused", budget=budget, resources=[item]
        )
    assert not (tmp_path / "bad.tavotto").exists()


@pytest.mark.parametrize("metadata", [{"assets": []}, {"document_format": "v4"}, {"future": 1}])
def test_metadata_uses_existing_strict_envelope_and_old_output_stays_unchanged(
    tmp_path, budget, prepared_input, metadata
):
    root, doc, _ = prepared_input
    output = tmp_path / "result.tavotto"
    output.write_bytes(b"old package")
    with pytest.raises(container.PackageError):
        packages.write_chart_package(
            output, doc, root, budget=budget, overwrite=True, metadata=metadata
        )
    assert output.read_bytes() == b"old package"
    assert not list(tmp_path.glob(".*.prepared"))


def test_wrong_path_source_digest_is_detected_before_final_publication(
    tmp_path, budget, prepared_input
):
    root, doc, _ = prepared_input
    extra = resource("file", "原图.pdf", b"expected")
    changed = tmp_path / "readable-but-changed.pdf"
    changed.write_bytes(b"wrong!!!")  # 等长坏摘要；长度拒绝不能替代这个反证。
    output = tmp_path / "existing.tavotto"
    output.write_bytes(b"old package")
    with pytest.raises(container.PackageError) as error:
        packages.write_chart_package(
            output,
            doc,
            root,
            budget=budget,
            overwrite=True,
            resources=[packages.ResourceFile(extra.record, changed)],
        )
    assert error.value.reason == "resource_mismatch"
    assert output.read_bytes() == b"old package"
    assert not list(tmp_path.glob(".*.prepared"))


@pytest.mark.parametrize("failure", [OSError(errno.ENOSPC, "disk full"), KeyboardInterrupt()])
def test_failed_zip_write_keeps_old_final_and_removes_only_owned_temporary_paths(
    tmp_path, budget, prepared_input, monkeypatch, failure
):
    root, doc, _ = prepared_input
    output = tmp_path / "existing.tavotto"
    output.write_bytes(b"old package")
    unrelated = tmp_path / "someone-elses.tmp"
    unrelated.write_bytes(b"keep")

    real_open = zipfile.ZipFile.open

    def fail(archive, name, mode="r", *args, **kwargs):
        member_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if mode == "w" and isinstance(member_name, str) and member_name.startswith("assets/"):
            raise failure  # metadata 已写，资源写入中断；不替换整个 writer。
        return real_open(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", fail)
    with pytest.raises(type(failure)):
        packages.write_chart_package(output, doc, root, budget=budget, overwrite=True)
    assert output.read_bytes() == b"old package" and unrelated.read_bytes() == b"keep"
    assert sorted(p.name for p in tmp_path.glob(".*")) == []


def test_no_clobber_is_default_and_overwrite_is_explicit(tmp_path, budget, prepared_input):
    root, doc, _ = prepared_input
    output = tmp_path / "existing.tavotto"
    output.write_bytes(b"old package")
    with pytest.raises(FileExistsError):
        packages.write_chart_package(output, doc, root, budget=budget)
    assert output.read_bytes() == b"old package"
    packages.write_chart_package(output, doc, root, budget=budget, overwrite=True)
    with packages.inspect_chart_package(output.read_bytes(), budget=budget) as ready:
        assert ready.document == doc


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_archive_bytes", 30),
        ("max_members", 2),
        ("max_directory_bytes", 60),
        ("max_total_bytes", 200),
        ("max_layout_bytes", 20),
        ("max_manifest_bytes", 20),
    ],
)
def test_all_six_caller_budgets_remain_effective(tmp_path, budget, prepared_input, field, value):
    root, doc, _ = prepared_input
    output = tmp_path / "result.tavotto"
    with pytest.raises(container.PackageError):
        packages.write_chart_package(output, doc, root, budget=replace(budget, **{field: value}))
    assert not output.exists()
    assert not list(tmp_path.glob(".*.prepared"))


def test_missing_or_corrupt_existing_input_never_falls_back_to_original(
    tmp_path, budget, prepared_input
):
    root, doc, _ = prepared_input
    panel = doc["canvases"][0]["objects"][0]
    panel["fileId"] = panel["chartSource"]["sourceId"] = "同名原图.svg"
    ref = panel["chartSource"]
    path = assets.source_path(root, ref)
    (root.parent / panel["fileId"]).write_bytes(SOURCE)
    path.unlink()
    with pytest.raises(assets.DocumentAssetError):
        packages.write_chart_package(tmp_path / "missing.tavotto", doc, root, budget=budget)
    path.write_bytes(b"corrupt immutable blob")
    with pytest.raises(assets.DocumentAssetError):
        packages.write_chart_package(tmp_path / "corrupt.tavotto", doc, root, budget=budget)
    assert path.read_bytes() == b"corrupt immutable blob"


def test_same_logical_name_different_sources_install_without_overwriting_user_files(
    tmp_path, budget, prepared_input
):
    source_id = "同名原图.svg"
    panel = prepared_input[1]["canvases"][0]["objects"][0]
    panel["fileId"] = panel["chartSource"]["sourceId"] = source_id
    path1, doc1, source1 = write(tmp_path, budget, prepared_input)
    bytes1 = path1.read_bytes()
    source2 = SOURCE.replace(b"blue", b"green")
    digest2 = hashlib.sha256(source2).hexdigest()
    root, _, _ = prepared_input
    ref2 = assets.freeze_source(root, source_id, source2, scene_digest=digest2)
    doc2 = document(ref2)
    doc2["canvases"][0]["objects"][0]["fileId"] = source_id
    doc2["canvases"][0]["objects"][0]["chart"] = chart(source2)
    path2 = tmp_path / "other.tavotto"
    packages.write_chart_package(path2, doc2, root, budget=budget)
    target_project = tmp_path / "新项目"
    target_project.mkdir()
    for name, data in [(source_id, b"user svg"), ("计算.py", b"user script")]:
        (target_project / name).write_bytes(data)
    target = target_project / config.PROJECT_STORE_DIRNAME
    for package, doc, expected in [(bytes1, doc1, source1), (path2.read_bytes(), doc2, source2)]:
        with packages.inspect_chart_package(package, budget=budget) as ready:
            packages.install_chart_sources(ready, target, verify=no_replay)
        ref = doc["canvases"][0]["objects"][0]["chartSource"]
        assert assets.load_source(target, ref).input_svg == expected
    assert len(list((target / assets.CHART_SOURCES_DIRNAME).iterdir())) == 2
    assert (target_project / source_id).read_bytes() == b"user svg"
    assert (target_project / "计算.py").read_bytes() == b"user script"


def test_existing_corrupt_target_is_not_repaired(tmp_path, budget, prepared_input):
    path, doc, _ = write(tmp_path, budget, prepared_input)
    target = tmp_path / "目标"
    ref = doc["canvases"][0]["objects"][0]["chartSource"]
    occupied = assets.source_path(target, ref)
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"user bytes at digest name")
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        with pytest.raises(assets.DocumentAssetError):
            packages.install_chart_sources(ready, target, verify=no_replay)
    assert occupied.read_bytes() == b"user bytes at digest name"


def test_duplicate_input_is_one_member_but_preserves_each_logical_reference(
    tmp_path, budget, prepared_input
):
    root, doc, source = prepared_input
    another = copy.deepcopy(doc["canvases"][0]["objects"][0])
    another["fileId"] = "别的逻辑名称.svg"
    another["chartSource"] = assets.freeze_source(
        root, another["fileId"], source, scene_digest=SCENE_DIGEST
    )
    doc["canvases"][0]["objects"].append(another)
    path, _, _ = write(tmp_path, budget, prepared_input)
    manifest, members = rows_and_members(path)
    assert len(manifest["assets"]) == 1 and len(members) == 3
    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as ready:
        result = packages.install_chart_sources(ready, tmp_path / "新", verify=no_replay)
        assert result["document"] == doc and len(result["chart_sources"]) == 2


def test_import_closure_remains_stdlib_only_in_a_fresh_process(tmp_path):
    script = f"""
import importlib.abc,sys
sys.path.insert(0,{str(REPO / "src")!r})
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] in {{'matplotlib','numpy','pikepdf','pypdfium2','uharfbuzz','fontTools','PIL','fitz','pymupdf'}}:
            raise RuntimeError('native/scientific import: '+fullname)
sys.meta_path.insert(0,Reject())
from tavotto.engine import chartpackage
assert chartpackage.PackageBudget
print('stdlib-only')
"""
    child = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "stdlib-only"


def test_relocated_package_replays_native_chart_in_new_process_after_sources_deleted(
    tmp_path, budget, monkeypatch
):
    """主语是搬迁后新 python -I，仅持包内 INPUT/正文；实际 native 绘制与 bundle 核验。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "数据"))
    from tavotto.engine import pool

    worker_python = pool.find_worker_python()
    script = f"""
import base64,json,sys
sys.path.insert(0,{str(REPO / "src")!r})
from tavotto import chartbundle,svgscene
from tavotto.engine import svgchart,chartprofile,documentassets
from tavotto.svgcompose import compose_native_scene
req=json.load(sys.stdin)
if 'root' in req:
    doc=req['document'];panel=doc['canvases'][0]['objects'][0]
    loaded=documentassets.load_source(__import__('pathlib').Path(req['root']),panel['chartSource'])
    incoming=chartbundle.extract_chart(loaded.input_svg)
    assert incoming is not None
    source=incoming.source_svg
    state=panel['chart'];edits=panel['overrides']
else:
    source=base64.b64decode(req['source']);state=req['chart'];edits=req['overrides']
scene=svgscene.scene_inventory(source)
frame={{'view_box':scene['view_box'],'w_pt':scene['w_pt'],'h_pt':scene['h_pt'],'preserve':None}}
out=svgchart.scaffold_fragment(state['model'],frame,render_profile=chartprofile.NATIVE_PROFILE,patches=edits)
assert not out['warnings'] and not out['unrestored'],out
composed=compose_native_scene(source,state['binding'],out,state['model'])
assert composed['composition']['composed'],composed['composition']
visible=composed['svg']
assert b'unbound' in visible and b'#e32636' in visible
if 'root' in req:
    chartbundle.verify_replay(incoming,visible)
bundle=chartbundle.embed_chart(visible,source,state,render_profile=chartprofile.NATIVE_PROFILE,overrides=edits)
print(json.dumps({{'visible':base64.b64encode(visible).decode(),'input':base64.b64encode(bundle).decode()}}))
"""

    def child(request, cwd):
        proc = subprocess.run(
            [worker_python, "-I", "-c", script],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            timeout=90,
            env={**os.environ, "TAVOTTO_NO_TELEMETRY": "1"},
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    edits = [
        {"gid": "axes_0.lines_0", "prop": "linewidth", "value": 3},
        {"gid": "axes_0.lines_0", "prop": "color", "value": "#e32636"},
    ]
    original_project = tmp_path / "原项目"
    original_project.mkdir()
    source_id = "原图.svg"
    (original_project / source_id).write_bytes(SOURCE)
    hot = child(
        {"source": base64.b64encode(SOURCE).decode(), "chart": chart(), "overrides": edits},
        original_project,
    )
    input_svg = base64.b64decode(hot["input"])
    root = original_project / config.PROJECT_STORE_DIRNAME
    ref = assets.freeze_source(root, source_id, input_svg, scene_digest=SCENE_DIGEST)
    doc = document(ref)
    doc["canvases"][0]["objects"][0]["fileId"] = source_id
    doc["canvases"][0]["objects"][0]["overrides"] = edits
    package_path = tmp_path / "搬家包.tavotto"
    packages.write_chart_package(package_path, doc, root, budget=budget)
    shutil.rmtree(original_project)
    relocated = tmp_path / "搬迁后 中文项目"
    relocated.mkdir()
    target = relocated / config.PROJECT_STORE_DIRNAME
    callbacks = []
    with packages.inspect_chart_package(package_path.read_bytes(), budget=budget) as ready:

        def verify(actual, staging):
            replay = child({"root": str(staging), "document": actual}, relocated)
            assert replay == hot
            callbacks.append(True)

        installed = packages.install_chart_sources(ready, target, verify=verify)
        assert installed["remaining_resources"] == []
        # helper 无正文提交；这里模拟 caller 已完成 verify 后的单次原子写。
        atomicio.write_bytes(target / "迁移正文.json", atomicio.dumps_json(installed["document"]))
    assert callbacks == [True]
    cold = child({"root": str(target), "document": doc}, relocated)
    assert cold == hot
    assert assets.load_source(target, ref).input_svg == input_svg
    assert not (relocated / source_id).exists() and not original_project.exists()
