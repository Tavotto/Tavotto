"""包命名空间折叠后，真实项目科学 ID 的只读安装计划。"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from tavotto import chartbundle
from tavotto.engine import (
    atomicio,
    chartpackage as packages,
    config,
    documentassets as assets,
    documentpackage as container,
    packagebudget,
)
from test_chart_document_assets import SCENE_DIGEST, SOURCE, SOURCE_ID, chart, document
from test_chart_package import resource


@pytest.fixture
def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "数据暂存"))
    storage_root = tmp_path / "原项目" / config.PROJECT_STORE_DIRNAME
    input_svg = chartbundle.embed_chart(SOURCE, SOURCE, chart())
    ref = assets.freeze_source(storage_root, SOURCE_ID, input_svg, scene_digest=SCENE_DIGEST)
    doc = document(ref)
    count = 0

    def make(extras=(), *, with_chart=True):
        nonlocal count
        count += 1
        path = tmp_path / f"input-{count}.tavotto"
        selected = []

        def budget(census):
            selected.append(packagebudget.output_budget(census))
            return selected[-1]

        packages.write_chart_package(
            path,
            doc if with_chart else {"schema": 4, "canvases": [{"objects": []}]},
            storage_root,
            budget_factory=budget,
            resources=extras,
        )
        return packages.inspect_chart_package(path.read_bytes(), budget=selected[0])

    return make


def test_project_plan_preserves_all_kinds_scientific_ids_and_full_input(
    tmp_path, prepare, monkeypatch
):
    marker = tmp_path / "MUST_NOT_EXECUTE"
    script = f"from pathlib import Path\nPath({str(marker)!r}).touch()".encode()
    snapshot_target = f"{config.PROJECT_STORE_DIRNAME}/figure_snapshots/snapshots/receipt.json"
    extras = [
        resource("file", "中文数据/原图.pdf", b"inert PDF"),
        resource("file", "中文数据/bitmap.png", b"inert PNG"),
        resource("script", "计算/原脚本.py", script),
        resource("figure_snapshot", "unused", b"snapshot", path="assets/" + snapshot_target),
    ]
    with prepare(extras) as ready:
        records_before = ready.manifest["assets"]
        files_before = {item.source: item.source.read_bytes() for item in ready.resources}

        def unexpected_write(*_args, **_kwargs):
            pytest.fail("目标投影不得发布文件或再冻结来源")

        monkeypatch.setattr(atomicio, "publish_file", unexpected_write)
        monkeypatch.setattr(atomicio, "publish_file_if_absent", unexpected_write)
        monkeypatch.setattr(assets, "freeze_source", unexpected_write)
        plan = packages.plan_project_resources(ready)
        assert isinstance(plan, tuple)
        assert [name for name, _ in plan] == [
            row["path"].partition("/")[2] for row in records_before
        ]
        assert [name for name, _ in plan][1:] == [
            "中文数据/原图.pdf",
            "中文数据/bitmap.png",
            "计算/原脚本.py",
            snapshot_target,
        ]
        assert all(isinstance(item, packages.ResourceFile) for _, item in plan)
        assert plan[0][1].source.read_bytes() != SOURCE
        assert chartbundle.extract_chart(plan[0][1].source.read_bytes()).source_svg == SOURCE
        assert {item.source: item.source.read_bytes() for _, item in plan} == files_before
        assert ready.manifest["assets"] == records_before
        with pytest.raises(TypeError):
            plan[0] = ("changed", plan[0][1])
        with pytest.raises(FrozenInstanceError):
            plan[0][1].source = tmp_path / "elsewhere"
        # ResourceFile 的记录是独立副本；外部变更不能改 Prepared 或下次计划。
        plan[0][1].record.clear()
        assert ready.manifest["assets"] == records_before
        assert packages.plan_project_resources(ready)[0][1].record == records_before[0]
    assert not marker.exists()


@pytest.mark.parametrize("data", [b"", b"same approved bytes"])
def test_identical_cross_namespace_targets_deduplicate_in_manifest_order(prepare, data):
    extras = [resource("file", "same.txt", data), resource("script", "same.txt", data)]
    with prepare(extras, with_chart=False) as ready:
        assert len(ready.resources) == 2, "包内两个 namespace 原本都是合法成员"
        plan = packages.plan_project_resources(ready)
        assert len(plan) == 1 and plan[0][0] == "same.txt"
        assert plan[0][1].record == extras[0].record
        assert plan[0][1].source.read_bytes() == data
        assert len(ready.manifest["assets"]) == 2, "只去重安装动作，不改语义资源行"


def test_empty_resource_package_has_an_empty_project_plan(prepare):
    with prepare(with_chart=False) as ready:
        assert packages.plan_project_resources(ready) == ()


@pytest.mark.parametrize("reverse", [False, True])
def test_same_project_path_different_bytes_is_rejected_across_namespaces(prepare, reverse):
    extras = [resource("file", "same.txt", b"asset"), resource("script", "same.txt", b"other")]
    if reverse:
        extras.reverse()
    with prepare(extras, with_chart=False) as ready:
        assert len(ready.resources) == 2
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "project_target_conflict"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("same_bytes", [False, True])
@pytest.mark.parametrize(
    "first,second",
    [("Curve.txt", "curve.txt"), ("é.txt", "e\u0301.txt"), ("Folder/图.txt", "folder/图.txt")],
)
def test_case_and_nfc_project_aliases_are_never_deduplicated(
    prepare, first, second, same_bytes, reverse
):
    extras = [
        resource("file", first, b"approved"),
        resource("script", second, b"approved" if same_bytes else b"different"),
    ]
    if reverse:
        extras.reverse()
    with prepare(extras, with_chart=False) as ready:
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "project_target_conflict"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "first,second",
    [("A/first.txt", "a/second.txt"), ("é/first.txt", "e\u0301/second.txt")],
)
def test_different_files_cannot_silently_merge_aliased_project_directories(
    prepare, first, second, reverse
):
    extras = [resource("file", first, b"one"), resource("script", second, b"two")]
    if reverse:
        extras.reverse()
    with prepare(extras, with_chart=False) as ready:
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "project_target_conflict"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("child", ["node/child.txt", "Node/child.txt", "node/deeper/child.txt"])
def test_project_file_and_directory_collisions_reject_both_orders(prepare, child, reverse):
    extras = [resource("file", "node", b"file"), resource("script", child, b"child")]
    if reverse:
        extras.reverse()
    with prepare(extras, with_chart=False) as ready:
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "project_target_conflict"


@pytest.mark.parametrize("same_bytes", [False, True])
def test_chart_input_project_target_participates_in_cross_namespace_plan(prepare, same_bytes):
    with prepare() as first:
        row = first.resources[0].record
        input_svg = first.resources[0].source.read_bytes()
    target = row["path"].partition("/")[2]
    extra = resource("script", target, input_svg if same_bytes else b"different script")
    with prepare([extra]) as ready:
        if same_bytes:
            plan = packages.plan_project_resources(ready)
            assert len(plan) == 1 and plan[0][0] == target
            assert plan[0][1].record["kind"] == "chart_source"
        else:
            with pytest.raises(container.PackageError) as error:
                packages.plan_project_resources(ready)
            assert error.value.reason == "project_target_conflict"


@pytest.mark.parametrize("same_bytes", [False, True])
def test_figure_snapshot_project_target_participates_in_cross_namespace_plan(prepare, same_bytes):
    target = f"{config.PROJECT_STORE_DIRNAME}/figure_snapshots/blobs/source.pdf"
    extras = [
        resource("figure_snapshot", "unused", b"snapshot", path="assets/" + target),
        resource("script", target, b"snapshot" if same_bytes else b"script"),
    ]
    with prepare(extras, with_chart=False) as ready:
        if same_bytes:
            plan = packages.plan_project_resources(ready)
            assert len(plan) == 1 and plan[0][0] == target
            assert plan[0][1].record["kind"] == "figure_snapshot"
        else:
            with pytest.raises(container.PackageError) as error:
                packages.plan_project_resources(ready)
            assert error.value.reason == "project_target_conflict"


def test_all_duplicate_resources_are_verified_before_deduplicating(prepare):
    extras = [
        resource("file", "same.txt", b"original"),
        resource("script", "same.txt", b"original"),
    ]
    with prepare(extras, with_chart=False) as ready:
        ready.resources[1].source.write_bytes(b"tampered")
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "resource_mismatch"


@pytest.mark.parametrize("change", ["same_length", "deleted", "directory"])
def test_plan_requires_current_verified_extra_resources(prepare, change):
    with prepare([resource("file", "data.txt", b"original")]) as ready:
        path = ready.resources[-1].source
        assert packages.plan_project_resources(ready)
        if change == "same_length":
            path.write_bytes(b"tampered")
        else:
            path.unlink()
            if change == "directory":
                path.mkdir()
        with pytest.raises((container.PackageError, OSError)):
            packages.plan_project_resources(ready)


def test_plan_requires_current_full_chart_input(prepare):
    with prepare() as ready:
        path = ready.resources[0].source
        data = path.read_bytes()
        changed = data.replace(b"blue", b"pink")
        assert changed != data and len(changed) == len(data)
        path.write_bytes(changed)
        with pytest.raises(assets.DocumentAssetError):
            packages.plan_project_resources(ready)


def test_plan_rejects_missing_manifest_resource_in_prepared_view(prepare):
    with prepare([resource("file", "must-not-be-lost.txt", b"data")]) as ready:
        incomplete = packages.PreparedChartPackage(
            ready.document,
            ready.manifest,
            ready.storage_root,
            tuple((item.record, item.source) for item in ready.resources[:-1]),
        )
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(incomplete)
        assert error.value.reason == "resource_mismatch"


def test_closed_prepared_cannot_produce_a_new_plan(prepare):
    with prepare() as ready:
        plan = packages.plan_project_resources(ready)
        source_path = plan[0][1].source
        assert source_path.is_file()
    assert not source_path.exists(), "计划中的 Path 不延长暂存生命周期"
    with pytest.raises(container.PackageError) as error:
        packages.plan_project_resources(ready)
    assert error.value.reason == "prepared_closed"


@pytest.mark.parametrize("invalid", [None, object(), ()])
def test_plan_requires_a_prepared_chart_package(invalid):
    with pytest.raises(container.PackageError) as error:
        packages.plan_project_resources(invalid)
    assert error.value.reason == "invalid_resource"


@pytest.mark.parametrize(
    "member,expected",
    [
        ("assets/中文/Curve.pdf", "中文/curve.pdf"),
        ("scripts/中文/Curve.pdf", "中文/curve.pdf"),
        ("assets/e\u0301.txt", "é.txt"),
        ("scripts/layout.json", "layout.json"),
    ],
)
def test_project_member_comparison_key_comes_from_full_strict_member_name(member, expected):
    assert container.project_member_key(member) == expected


@pytest.mark.parametrize(
    "member",
    [
        "layout.json",
        "package_manifest.json",
        "same.txt",
        "other/same.txt",
        "assets/../escape",
        "scripts//empty.txt",
        "assets/./dot.txt",
        "assets/CON.txt",
        "scripts/name.",
        "assets/name ",
        "assets/name:stream",
        "scripts/back\\slash",
        "assets/NUL.txt",
        "assets/new\nline",
        "/assets/absolute",
        "assets/",
        "",
        "assets/truncated\x00suffix",
        None,
        True,
    ],
)
def test_project_member_key_never_relaxes_portable_path_authority(member):
    with pytest.raises(container.PackageError):
        container.project_member_key(member)


def test_project_target_collision_is_not_mistaken_for_identical_length(prepare):
    first, second = b"asset", b"other"
    assert (
        len(first) == len(second)
        and hashlib.sha256(first).digest() != hashlib.sha256(second).digest()
    )
    with prepare(
        [resource("file", "same.txt", first), resource("script", "same.txt", second)],
        with_chart=False,
    ) as ready:
        with pytest.raises(container.PackageError) as error:
            packages.plan_project_resources(ready)
        assert error.value.reason == "project_target_conflict"
