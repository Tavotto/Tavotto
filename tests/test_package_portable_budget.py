"""Explicit portable output fits the existing input policy; legacy capacity stays unchanged."""

from __future__ import annotations

import io
import random
import zipfile
from dataclasses import replace

import pytest

from tavotto.engine import documentpackage, packagebudget


def members():
    return {
        documentpackage.LAYOUT_MEMBER: b'{"schema":4}',
        documentpackage.MANIFEST_MEMBER: b'{"version":1}',
        "assets/empty": b"",
        "assets/中文/input.bin": random.Random(413).randbytes(8193),
    }


def test_portable_output_is_readable_with_the_independent_existing_input_limits(tmp_path):
    values = members()
    budget = packagebudget.portable_output_budget(
        {name: len(data) for name, data in values.items()}
    )
    target = tmp_path / "portable.zip"
    documentpackage.write_package(target, values, budget=budget)
    incoming = packagebudget.input_budget()
    assert target.stat().st_size <= incoming.max_archive_bytes
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in archive.namelist()} == values
        assert sum(row.file_size for row in archive.infolist()) <= incoming.max_total_bytes


@pytest.mark.parametrize("dimension", ["bytes", "members", "layout", "manifest"])
def test_portable_policy_refuses_before_output_and_does_not_limit_legacy(monkeypatch, dimension):
    incoming = packagebudget.input_budget()
    plan = {name: len(data) for name, data in members().items()}
    if dimension == "bytes":
        plan["assets/large"] = incoming.max_total_bytes
    elif dimension == "members":
        incoming = replace(incoming, max_members=3)
    elif dimension == "layout":
        plan[documentpackage.LAYOUT_MEMBER] = incoming.max_layout_bytes + 1
    else:
        plan[documentpackage.MANIFEST_MEMBER] = incoming.max_manifest_bytes + 1
    monkeypatch.setattr(packagebudget, "input_budget", lambda: incoming)
    # Actual default output still admits this exact plan; no policy was added
    # to the old factory. No multi-GiB allocation is claimed by this check.
    assert packagebudget.output_budget(plan).max_members == len(plan)
    with pytest.raises(documentpackage.PackageError, match="shared input policy") as error:
        packagebudget.portable_output_budget(plan)
    assert error.value.reason == "output_budget"


def test_real_archive_limit_failure_preserves_existing_output(tmp_path, monkeypatch):
    values = members()
    incoming = replace(packagebudget.input_budget(), max_archive_bytes=100)
    monkeypatch.setattr(packagebudget, "input_budget", lambda: incoming)
    budget = packagebudget.portable_output_budget(
        {name: len(data) for name, data in values.items()}
    )
    target = tmp_path / "existing.zip"
    target.write_bytes(b"previous successful archive")
    with pytest.raises(documentpackage.PackageError) as error:
        documentpackage.write_package(target, values, budget=budget)
    assert error.value.reason == "archive_budget"
    assert target.read_bytes() == b"previous successful archive"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["existing.zip"]


def test_independent_zip_can_exceed_the_shared_archive_limit_even_when_plan_bytes_fit(monkeypatch):
    values = members()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in values.items():
            archive.writestr(name, data)
    actual = len(stream.getvalue())
    incoming = replace(packagebudget.input_budget(), max_archive_bytes=actual - 1)
    monkeypatch.setattr(packagebudget, "input_budget", lambda: incoming)
    budget = packagebudget.portable_output_budget(
        {name: len(data) for name, data in values.items()}
    )
    assert actual > budget.max_archive_bytes
    assert sum(map(len, values.values())) <= budget.max_total_bytes
