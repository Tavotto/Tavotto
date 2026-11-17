"""便携包安装的拒绝门与单点发布（#1921 接入前置）。

判据的主语是**目标目录**与**发布点**：目标已存在时一个字节都不动；资源缺失
在 `mkdtemp` 之前就拒；重放不通过不发布；发布只经
`atomicio.publish_directory_if_absent` 一次；发布之后的配置失败保留已发布
目录并如实报 `configuration_error`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto import chartbundle
from tavotto.engine import (
    chartpackage as packages,
    config,
    documentassets as assets,
    documentpackage as container,
    packageinspection,
    packageinstall,
)
from test_chart_document_assets import SCENE_DIGEST, SOURCE, SOURCE_ID, chart, document


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

    from tavotto.engine import atomicio

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
