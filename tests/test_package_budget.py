"""预算来自真实输出计划；用独立 stdlib ZIP 产物测边界，不重复公式求期望。"""

from __future__ import annotations

import io
import random
import zipfile

import pytest

from tavotto.engine import documentpackage as packages
from tavotto.engine.packagebudget import output_budget


def metadata(schema=2):
    return {
        packages.LAYOUT_MEMBER: f'{{"schema":{schema},"name":"中文","objects":[]}}'.encode(),
        packages.MANIFEST_MEMBER: b'{"kind":"tavotto-package","version":1,"assets":[]}',
    }


def samples(kind):
    members = metadata()
    if kind == "empty":
        members["assets/empty.dat"] = b""
    elif kind == "unicode-long":
        members["assets/中文/" + "子目录/" * 40 + "曲线.svg"] = "中文数据".encode() * 30
    elif kind == "compressible":
        members["assets/zeros.dat"] = b"\0" * (1024 * 1024)
    elif kind in {"incompressible", "zip64"}:
        members["assets/noise.dat"] = random.Random(173).randbytes(1024 * 1024)
    elif kind == "many":
        members.update({f"scripts/script_{n}.py": b"# inert\n" for n in range(300)})
    else:
        raise AssertionError(kind)
    return members


@pytest.mark.parametrize(
    "kind", ["empty", "unicode-long", "compressible", "incompressible", "many", "zip64"]
)
def test_budget_contains_independent_real_zip_and_actual_container(tmp_path, monkeypatch, kind):
    members = samples(kind)
    if kind == "zip64":
        monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 128)
    budget = output_budget({name: len(data) for name, data in members.items()})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    raw = output.getvalue()
    assert len(raw) <= budget.max_archive_bytes
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        assert len(raw) - archive.start_dir <= budget.max_directory_bytes
        assert sum(info.file_size for info in archive.infolist()) == budget.max_total_bytes
        assert len(archive.infolist()) == budget.max_members
        assert {name: archive.read(name) for name in members} == members
        if kind == "incompressible":
            info = archive.getinfo("assets/noise.dat")
            assert info.compress_size > info.file_size, "没有真的测试压缩膨胀"
        if kind == "zip64":
            assert b"PK\x06\x06" in raw
    target = tmp_path / "output.tavotto"
    packages.write_package(target, members, budget=budget)
    assert target.stat().st_size <= budget.max_archive_bytes
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in members} == members


def test_plan_uses_exact_encoded_metadata_and_stat_sizes_for_approved_canonical_source(tmp_path):
    source = tmp_path / "原始文件.pdf"
    source.write_bytes(random.Random(123).randbytes(16_385))
    members = metadata(schema=3)
    canonical = source.resolve()
    name = zipfile.ZipInfo.from_file(canonical, "assets/原始文件.pdf").filename
    plan = {**{key: len(data) for key, data in members.items()}, name: canonical.stat().st_size}
    budget = output_budget(plan)
    assert budget.max_layout_bytes == len(members[packages.LAYOUT_MEMBER])
    assert budget.max_manifest_bytes == len(members[packages.MANIFEST_MEMBER])
    target = tmp_path / "canonical.tavotto"
    with packages.staged_package(target, budget=budget) as archive:
        for key, data in members.items():
            archive.writestr(key, data)
        archive.write(canonical, name)
    with zipfile.ZipFile(target) as archive:
        assert archive.read(name) == source.read_bytes()


@pytest.mark.parametrize("bad", [True, False, -1, 1.5, "12", None])
def test_rejects_invalid_size_without_allocating_or_reading_files(bad):
    with pytest.raises(packages.PackageError) as error:
        output_budget({packages.LAYOUT_MEMBER: 1, packages.MANIFEST_MEMBER: 1, "assets/a": bad})
    assert error.value.reason == "invalid_output"


@pytest.mark.parametrize("bad", [None, [], {}, {packages.LAYOUT_MEMBER: 10}])
def test_plan_requires_both_metadata_members(bad):
    with pytest.raises(packages.PackageError) as error:
        output_budget(bad)
    assert error.value.reason == "missing_metadata"


def test_large_sizes_are_arbitrary_precision_without_new_legacy_capacity_limit():
    # 数学容量资格，不假称本用例在磁盘写出了几十 GiB。
    large = (1 << 35) + 17
    plan = {packages.LAYOUT_MEMBER: 10, packages.MANIFEST_MEMBER: 11, "assets/huge.pdf": large}
    budget = output_budget(plan)
    assert budget.max_total_bytes == large + 21
    assert budget.max_archive_bytes > large
    assert budget.max_layout_bytes == 10 and budget.max_manifest_bytes == 11
    larger = output_budget({**plan, "assets/huge.pdf": 1 << 63})
    assert larger.max_archive_bytes > budget.max_archive_bytes
    assert larger.max_total_bytes == (1 << 63) + 21


def test_zero_byte_members_still_have_positive_container_limits(tmp_path):
    members = {packages.LAYOUT_MEMBER: b"", packages.MANIFEST_MEMBER: b"", "assets/a": b""}
    budget = output_budget(dict.fromkeys(members, 0))
    assert budget.max_total_bytes == budget.max_layout_bytes == budget.max_manifest_bytes == 1
    packages.write_package(tmp_path / "opaque.zip", members, budget=budget)


def test_path_rules_stay_in_container_and_plan_cannot_grant_a_member_name(tmp_path):
    members = {**metadata(), "assets/../escape": b"inert"}
    budget = output_budget({name: len(data) for name, data in members.items()})
    with pytest.raises(packages.PackageError, match="path"):
        packages.write_package(tmp_path / "unsafe.zip", members, budget=budget)
    assert not (tmp_path / "unsafe.zip").exists()


def test_normalization_must_precede_the_final_plan(tmp_path):
    source = tmp_path / "input.pdf"
    source.write_bytes(b"opaque input")
    raw_name = "assets/folder/../input.pdf"
    name = zipfile.ZipInfo.from_file(source, raw_name).filename
    assert name == "assets/input.pdf"
    members = metadata()
    budget = output_budget(
        {**{key: len(data) for key, data in members.items()}, name: source.stat().st_size}
    )
    with packages.staged_package(tmp_path / "output.zip", budget=budget) as archive:
        for key, data in members.items():
            archive.writestr(key, data)
        archive.write(source, name)
    with zipfile.ZipFile(tmp_path / "output.zip") as archive:
        assert name in archive.namelist() and raw_name not in archive.namelist()


def test_metadata_growth_after_the_exact_plan_cannot_publish_an_old_destination(tmp_path):
    members = metadata()
    budget = output_budget({name: len(data) for name, data in members.items()})
    target = tmp_path / "old.zip"
    target.write_bytes(b"old complete package")
    with pytest.raises(packages.PackageError):
        with packages.staged_package(target, budget=budget) as archive:
            archive.writestr(packages.LAYOUT_MEMBER, members[packages.LAYOUT_MEMBER])
            archive.writestr(packages.MANIFEST_MEMBER, members[packages.MANIFEST_MEMBER] + b" ")
    assert target.read_bytes() == b"old complete package"
    assert list(tmp_path.glob(".*.tmp")) == []
