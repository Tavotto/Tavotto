"""便携包只读检视（#1921 接入前置）。

判据的主语是**目标项目**：`inspect_project_package` 只读包内事实，不建目录、
不写项目、不执行包内脚本。预算拒绝对照的是**这一次上传**实际解析出的成员。
"""

from __future__ import annotations

import pytest

from tavotto import chartbundle
from tavotto.engine import (
    chartpackage as packages,
    config,
    documentassets as assets,
    documentpackage as container,
    packageinspection,
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


def test_inspection_reports_the_v4_envelope_without_writing_a_project(
    tmp_path, package_path, budget
):
    before = {path.name for path in tmp_path.iterdir()}

    with packages.inspect_chart_package(package_path.read_bytes(), budget=budget) as prepared:
        report = packageinspection.inspect_project_package(prepared)
        assert prepared.document["schema"] == 4

    assert report["document_format"] == "v4"
    assert report["missing"] == []
    assert report["warnings"] == []
    # 只读检视不建目标项目：目录集合与检视前完全相同（数据暂存目录已在打包时存在）。
    after = {path.name for path in tmp_path.iterdir()}
    assert after == before


def test_inspection_refuses_an_archive_over_the_upload_budget(tmp_path, package_path):
    tiny = container.PackageBudget(
        max_archive_bytes=1024,
        max_members=32,
        max_directory_bytes=64 * 1024,
        max_total_bytes=256 * 1024,
        max_layout_bytes=64 * 1024,
        max_manifest_bytes=16 * 1024,
    )

    with pytest.raises(packages.PackageError) as caught:
        with packages.inspect_chart_package(package_path.read_bytes(), budget=tiny):
            pass

    assert caught.value.reason == "archive_budget"
