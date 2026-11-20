"""便携包安装的拒绝门与单点发布（#1921 接入前置）。

判据的主语是**目标目录**与**发布点**：目标已存在时一个字节都不动；资源缺失
在 `mkdtemp` 之前就拒；重放不通过不发布；发布只经
`atomicio.publish_directory_if_absent` 一次；发布之后的配置失败保留已发布
目录并如实报 `configuration_error`。他机 remap 指纹的快照装完带重链警告，
但目标项目不继承那张规则表。
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from tavotto import chartbundle
from tavotto.engine import (
    atomicio,
    chartpackage as packages,
    config,
    documentassets as assets,
    documentpackage as container,
    figcapture,
    inputremap,
    packageinspection,
    packageinstall,
    patchspec,
    runconfig,
    svgstore,
)
from test_chart_document_assets import SCENE_DIGEST, SOURCE, SOURCE_ID, chart, document

FIGURE_SOURCE = "源图.pdf"
FIGURE_PDF = (
    Path(__file__).resolve().parent / "fixtures" / "foundation" / "pdf_png_assets" / "page.pdf"
)
FIGURE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20"><path d="M0 0L30 20"/></svg>'
)


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
def package_path(tmp_path, monkeypatch, budget):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "源项目" / config.PROJECT_STORE_DIRNAME
    source = chartbundle.embed_chart(SOURCE, SOURCE, chart())
    ref = assets.freeze_source(root, SOURCE_ID, source, scene_digest=SCENE_DIGEST)
    path = tmp_path / "便携包.tavotto"
    packages.write_chart_package(path, document(ref), root, budget=budget)
    return path


@pytest.fixture
def prepared(package_path, budget):
    with packages.inspect_chart_package(package_path.read_bytes(), budget=budget) as ready:
        yield ready


@pytest.fixture
def execution_package_path(tmp_path, monkeypatch, budget):
    """带最小 execution 表的包：发布后才会走到 `runconfig.import_portable`。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    root = tmp_path / "源项目" / config.PROJECT_STORE_DIRNAME
    source = chartbundle.embed_chart(SOURCE, SOURCE, chart())
    ref = assets.freeze_source(root, SOURCE_ID, source, scene_digest=SCENE_DIGEST)
    path = tmp_path / "便携包-配置.tavotto"
    script = (
        b'def main():\n    raise RuntimeError("Package installation must not run this script")\n'
    )
    config_id = "rc_0123456789ab"
    packages.write_chart_package(
        path,
        document(ref),
        root,
        budget=budget,
        resources=[
            packages.ResourceFile(
                {
                    "kind": "script",
                    "id": "source.py",
                    "path": "scripts/source.py",
                    "sha256": hashlib.sha256(script).hexdigest(),
                    "bytes": len(script),
                },
                script,
            )
        ],
        metadata={
            "execution": {
                "version": 1,
                "registry": {"scripts": {"source.py": {"entry": "main", "stems": ["F"]}}},
                "runs": [{"id": config_id, "script": "source.py", "argv": ["--series", "2"]}],
                "contexts": [
                    {
                        "script": "source.py",
                        "run_config": config_id,
                        "run_config_known": True,
                        "cwd_mode": "project_root",
                        "profile": None,
                        "basis": [],
                    }
                ],
                "remaps": [],
                "coverage": {"state": "complete", "issues": []},
            }
        },
    )
    return path


@pytest.fixture
def remap_package(tmp_path, monkeypatch, budget):
    """源机带非空改指指纹的 Figure 快照包。

    快照身份里记录的是源机的 `remap_fingerprint`；目标机既没有这张规则表，
    也不该在安装时把快照静默重绑到本机。包内 panel 不声明 `chart`，所以
    安装的 verify 不触发渲染，测试只钉安装/警告/映射继承三件事。
    """
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "源项目"
    project.mkdir()
    moved = tmp_path / "他机数据"
    moved.mkdir()
    inputremap.add_rule(project, {"kind": "prefix", "from": "data", "to": str(moved)})
    fingerprint = inputremap.fingerprint(project)
    assert fingerprint

    pdf = FIGURE_PDF.read_bytes()
    patch_hash = patchspec.patch_hash([])
    artifact = figcapture.SourceArtifact(
        source_id=FIGURE_SOURCE,
        origin=figcapture.ORIGIN_EXECUTION,
        kind="pdf",
        bytes_sha256=hashlib.sha256(pdf).hexdigest(),
        size_bytes=len(pdf),
        receipt_id="sha256:" + "a" * 64,
        receipt_identity="sha256:" + "b" * 64,
        generation=1,
        patch_hash=patch_hash,
    )
    store = project / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    saved = svgstore.save(
        project,
        FIGURE_SOURCE,
        patch_hash,
        pdf=pdf,
        svg=FIGURE_SVG,
        artifact=artifact.to_payload(),
        receipt={
            "receipt_id": artifact.receipt_id,
            "receipt_identity": artifact.receipt_identity,
            "generation": artifact.generation,
            "source_revision": "archived source",
        },
        remap_fingerprint=fingerprint,
        storage_root=store,
    )
    chart_document = {
        "schema": 4,
        "canvases": [
            {
                "objects": [
                    {
                        "id": "panel-1",
                        "type": "panel",
                        "fileId": FIGURE_SOURCE,
                        "fileKind": "pdf",
                        "x": 0,
                        "y": 0,
                        "w": 30,
                        "h": 20,
                        "figureSnapshot": saved["snapshot_ref"],
                    }
                ]
            }
        ],
    }
    prefix = f"assets/{config.PROJECT_STORE_DIRNAME}/{svgstore.STORE_DIRNAME}/"
    members = svgstore.inspect_portable_store(storage_root=store)["members"]
    resources = [
        packages.ResourceFile(
            {
                "kind": "figure_snapshot",
                "path": prefix + name,
                "sha256": figcapture.hash_file(store / name)[0],
                "bytes": (store / name).stat().st_size,
            },
            store / name,
        )
        for name in members
        if name != ".lock"
    ]
    resources.append(
        packages.ResourceFile(
            {
                "kind": "file",
                "id": FIGURE_SOURCE,
                "path": "assets/" + FIGURE_SOURCE,
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "bytes": len(pdf),
            },
            pdf,
        )
    )
    path = tmp_path / "带他机映射.tavotto"
    packages.write_chart_package(
        path,
        chart_document,
        project / config.PROJECT_STORE_DIRNAME,
        budget=budget,
        resources=resources,
    )
    return path, fingerprint, saved["snapshot_ref"], patch_hash


def _install(prepared, destination, verify=None):
    return packageinstall.install_project_package(
        prepared,
        destination=destination,
        verify=verify or (lambda document, stage: None),
    )


def test_existing_destination_is_refused_and_left_untouched(tmp_path, prepared):
    destination = tmp_path / "已有项目"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("mine", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _install(prepared, destination)

    assert marker.read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in destination.iterdir()) == ["keep.txt"]


def test_symlinked_destination_is_refused(tmp_path, prepared):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "链接"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without developer mode
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(FileExistsError):
        _install(prepared, link)

    assert list(target.iterdir()) == []


def test_missing_resources_are_refused_before_any_stage(tmp_path, prepared, monkeypatch):
    def missing_report(_prepared):
        return {
            "document_format": "v4",
            "missing": ["assets/缺失.png"],
            "warnings": [],
            "execution_coverage": None,
        }

    monkeypatch.setattr(packageinspection, "inspect_project_package", missing_report)
    destination = tmp_path / "新项目"

    with pytest.raises(packages.PackageError) as caught:
        _install(prepared, destination)

    assert caught.value.reason == "installation_resources_missing"
    assert not destination.exists()
    assert list(tmp_path.glob(".Tavotto-install-*")) == []


def test_failed_replay_does_not_publish(tmp_path, prepared):
    destination = tmp_path / "新项目"

    def verify(_document, _stage):
        raise packages.PackageError("chart_render_unsupported", "replay failed")

    with pytest.raises(packages.PackageError):
        _install(prepared, destination, verify)

    assert not destination.exists()
    assert list(tmp_path.glob(".Tavotto-install-*")) == []


def test_install_publishes_once_and_keeps_the_document(tmp_path, prepared):
    destination = tmp_path / "新项目"
    seen = {}

    def verify(value, stage):
        seen["document"] = value
        assert (stage / config.PROJECT_STORE_DIRNAME).is_dir()

    result = _install(prepared, destination, verify)

    assert result["state"] == "resources_installed"
    assert result["configuration"] in {"ready", "needs_review"}
    assert result["document_saved"] is False
    assert result["doc"] == seen["document"]
    assert Path(result["path"]) == destination
    assert destination.is_dir()
    assert list(tmp_path.glob(".Tavotto-install-*")) == []
    assert "references" not in result
    assert "remap_fingerprint" not in result


def test_publication_error_after_the_rename_is_reported_as_unconfirmed(
    tmp_path, prepared, monkeypatch
):
    """发布点之后才失败：目录已经在那里，如实报「未确认」，不回滚也不删除。"""
    import errno

    destination = tmp_path / "新项目"
    real = atomicio.publish_directory_if_absent

    def publish_then_fail(stage, target):
        real(stage, target)
        raise OSError(errno.EIO, "directory fsync failed")

    monkeypatch.setattr(atomicio, "publish_directory_if_absent", publish_then_fail)

    result = _install(prepared, destination)

    assert result["state"] == "publication_unconfirmed"
    assert result["path"] == str(destination)
    assert destination.is_dir()


def test_install_publishes_at_one_point(tmp_path, prepared, monkeypatch):
    """成功安装只经过 `atomicio.publish_directory_if_absent` 一次。"""
    calls = []
    real = atomicio.publish_directory_if_absent

    def counted(stage, target):
        calls.append((stage, target))
        return real(stage, target)

    monkeypatch.setattr(atomicio, "publish_directory_if_absent", counted)

    result = _install(prepared, tmp_path / "新项目")

    assert result["state"] == "resources_installed"
    assert len(calls) == 1


def test_configuration_failure_after_publish_keeps_the_directory(
    tmp_path, execution_package_path, budget, monkeypatch
):
    """发布后的运行配置导入失败：目录保留，如实报 `configuration_error`。"""
    destination = tmp_path / "新项目"

    def fail(*_args, **_kwargs):
        raise runconfig.RunConfigUnreadable(
            "portable store write failed", reason="portable_write_failed"
        )

    monkeypatch.setattr(packageinstall.runconfig, "import_portable", fail)

    with packages.inspect_chart_package(
        execution_package_path.read_bytes(), budget=budget
    ) as prepared:
        result = _install(prepared, destination)

    assert result["state"] == "resources_installed"
    assert result["configuration"] == "pending"
    assert result["configuration_error"] == "run_config_unreadable"
    assert destination.is_dir()
    assert list(tmp_path.glob(".Tavotto-install-*")) == []


def test_local_run_conflict_publishes_resources_and_keeps_local_argv(
    tmp_path, execution_package_path, budget
):
    destination = tmp_path / "新项目"
    config_id = "rc_0123456789ab"
    runconfig.import_portable(
        destination, [{"id": config_id, "script": "source.py", "argv": ["local"]}]
    )
    with packages.inspect_chart_package(
        execution_package_path.read_bytes(), budget=budget
    ) as prepared:
        result = _install(prepared, destination)
    assert result["state"] == "resources_installed"
    assert result["configuration"] == "needs_review"
    assert result["runconfig_skipped"] == {"conflict": 1, "capacity": 0}
    assert runconfig.get(destination, config_id, script="source.py").argv == ("local",)
    assert (destination / "source.py").is_file()


def test_foreign_remap_snapshot_installs_with_warning_and_is_not_inherited(
    tmp_path, remap_package, budget
):
    """带他机 remap 指纹的快照：装完、带重链警告，但不把规则继承到目标项目。"""
    path, fingerprint, reference, patch_hash = remap_package
    expected_warning = {"reason": "snapshot_remap_requires_relink", "id": FIGURE_SOURCE}
    destination = tmp_path / "新项目"

    with packages.inspect_chart_package(path.read_bytes(), budget=budget) as prepared:
        report = packageinspection.inspect_project_package(prepared)
        assert expected_warning in report["warnings"]
        result = _install(prepared, destination)

    assert result["state"] == "resources_installed"
    assert expected_warning in result["warnings"]
    assert destination.is_dir()
    assert inputremap.rules_for(destination) == []
    assert inputremap.fingerprint(destination) == ""

    dest_store = destination / config.PROJECT_STORE_DIRNAME / svgstore.STORE_DIRNAME
    archived = svgstore.inspect_portable_snapshot(reference, storage_root=dest_store)
    assert archived["identity"]["remap_fingerprint"] == fingerprint
    assert (
        svgstore.load_snapshot(
            destination,
            reference,
            source_id=FIGURE_SOURCE,
            patch_hash=patch_hash,
            remap_fingerprint="",
            storage_root=dest_store,
        )
        is None
    )


def test_remap_warning_roundtrips_through_package_http_endpoints(tmp_path, remap_package):
    """端点上真实的 open → inspect → install：重链警告与「不继承」都成立。"""
    from tavotto import app as m

    m.app.config["TESTING"] = True
    client = m.app.test_client()
    package_bytes = remap_package[0].read_bytes()
    expected_warning = {"reason": "snapshot_remap_requires_relink", "id": FIGURE_SOURCE}

    def upload():
        return {"package": (io.BytesIO(package_bytes), "带他机映射.tavotto")}

    opened = client.post("/api/package/open", data=upload(), content_type="multipart/form-data")
    assert opened.status_code == 200, opened.get_json()
    assert opened.get_json() == {"document_format": "v4"}

    inspected = client.post(
        "/api/package/inspect", data=upload(), content_type="multipart/form-data"
    )
    assert inspected.status_code == 200, inspected.get_json()
    assert expected_warning in inspected.get_json()["warnings"]

    destination = tmp_path / "新项目"
    installed = client.post(
        "/api/package/install",
        data={
            "package": (io.BytesIO(package_bytes), "带他机映射.tavotto"),
            "options": json.dumps({"destination": str(destination)}),
        },
        content_type="multipart/form-data",
    )
    assert installed.status_code == 201, installed.get_json()
    receipt = installed.get_json()
    assert receipt["state"] == "resources_installed"
    assert expected_warning in receipt["warnings"]
    assert destination.is_dir()
    assert inputremap.rules_for(destination) == []
    assert inputremap.fingerprint(destination) == ""
