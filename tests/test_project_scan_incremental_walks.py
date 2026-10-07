"""Budgeted project enumeration must stop while reading a directory, not after it.

The synthetic scandir iterator bounds actual directory reads in this process; no
large fixture or wall-clock timing assumption is needed to expose eager os.walk.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tavotto.engine import discover, project_refresh, scanbudget


class _BoundedScandir:
    def __init__(self, root, max_reads, *, directories=False, names=None):
        self.root = root
        self.max_reads = max_reads
        self.directories = directories
        self.names = names
        self.reads = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def __iter__(self):
        return self

    def __next__(self):
        assert self.reads < self.max_reads, "asset walk read beyond its scan budget"
        self.reads += 1
        name = f".hidden{self.reads}" if self.directories else f"asset{self.reads}.pdf"
        if self.names is not None:
            name = self.names[self.reads - 1]
        return SimpleNamespace(
            name=name,
            path=str(self.root / name),
            is_dir=lambda **_: self.directories,
            is_symlink=lambda: False,
        )


@pytest.fixture(
    params=[project_refresh.iter_assets, discover.iter_all_scripts], ids=["assets", "scripts"]
)
def walk(request):
    return request.param


def _forbid_listdir(_):
    pytest.fail("budgeted walk must not eagerly list an entire directory")


@pytest.mark.parametrize("directories", [False, True], ids=["files", "pruned-directories"])
def test_entry_budget_bounds_actual_directory_reads(tmp_path, monkeypatch, directories, walk):
    entries = _BoundedScandir(tmp_path, 6, directories=directories)
    budget = scanbudget.Budget(limits=scanbudget.Limits(max_entries=8))
    budget.entries = 3  # The script walk already used part of the shared budget.
    with monkeypatch.context() as patch:
        patch.setattr(project_refresh.os, "scandir", lambda _: entries)
        patch.setattr(project_refresh.os, "listdir", _forbid_listdir)
        walk(tmp_path, budget=budget)

    assert entries.reads == 6
    assert entries.closed
    assert budget.entries == 9
    assert budget.stopped == scanbudget.ISSUE_ENTRIES


@pytest.mark.parametrize("reason", [scanbudget.ISSUE_TIME, scanbudget.ISSUE_CANCELLED])
def test_time_and_cancel_budgets_interrupt_directory_reads(tmp_path, monkeypatch, reason, walk):
    entries = _BoundedScandir(tmp_path, 64)
    budget = scanbudget.Budget(
        limits=scanbudget.Limits(max_seconds=3),
        clock=lambda: float(entries.reads) if reason == scanbudget.ISSUE_TIME else 0.0,
        cancel=lambda: reason == scanbudget.ISSUE_CANCELLED and entries.reads >= 3,
    )
    with monkeypatch.context() as patch:
        patch.setattr(project_refresh.os, "scandir", lambda _: entries)
        patch.setattr(project_refresh.os, "listdir", _forbid_listdir)
        walk(tmp_path, budget=budget)

    assert 0 < entries.reads <= 64
    assert entries.closed
    assert budget.stopped == reason
    assert any(issue["code"] == reason for issue in budget.issues())


def test_asset_partial_directory_does_not_override_an_unseen_pdf(tmp_path, monkeypatch):
    entries = _BoundedScandir(tmp_path, 2, names=["plot.png", "plot.pdf"])
    budget = scanbudget.Budget(limits=scanbudget.Limits(max_entries=1))
    with monkeypatch.context() as patch:
        patch.setattr(project_refresh.os, "scandir", lambda _: entries)
        found = project_refresh.iter_assets(tmp_path, budget=budget)

    assert found == []  # The directory is incomplete, so PDF precedence is unknown.
    assert entries.reads == 2 and entries.closed
    assert budget.stopped == scanbudget.ISSUE_ENTRIES


def test_already_cancelled_walk_never_opens_a_directory(tmp_path, monkeypatch, walk):
    def unexpected(_):
        pytest.fail("cancelled scan opened a directory")

    budget = scanbudget.Budget(cancel=lambda: True)
    with monkeypatch.context() as patch:
        patch.setattr(project_refresh.os, "scandir", unexpected)
        assert walk(tmp_path, budget=budget) == []
    assert budget.entries == 0
    assert budget.stopped == scanbudget.ISSUE_CANCELLED


def test_completed_walk_charges_each_entry_once_and_keeps_legacy_results(tmp_path, walk):
    extension = ".pdf" if walk is project_refresh.iter_assets else ".py"
    for name in ["z", "a", ".hidden", "sub/c", ".ignored/no"]:
        path = tmp_path / (name + extension)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    expected = walk(tmp_path)
    budget = scanbudget.Budget(limits=scanbudget.Limits(max_entries=6))

    assert walk(tmp_path, budget=budget) == expected
    assert len(expected) == 3
    assert budget.entries == 6
    assert budget.stopped is None and budget.issues() == []


def test_asset_precedence_sort_pruning_and_limit_match_legacy(tmp_path):
    for name in [
        "z.jpg",
        "a.png",
        "a.PDF",
        "sub/a.png",
        "sub/b.TIFF",
        ".hidden.pdf",
        "scripts/hidden.pdf",
        "tavottofile/hidden.pdf",
        ".git/hidden.pdf",
    ]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    expected = [
        (tmp_path / "a.PDF", "pdf"),
        (tmp_path / "sub/a.png", "raster"),
        (tmp_path / "sub/b.TIFF", "raster"),
        (tmp_path / "z.jpg", "raster"),
    ]
    assert project_refresh.iter_assets(tmp_path) == expected
    assert project_refresh.iter_assets(tmp_path, budget=scanbudget.Budget()) == expected
    budget = scanbudget.Budget(limits=scanbudget.Limits(max_assets=2))
    assert project_refresh.iter_assets(tmp_path, budget=budget) == expected[:2]
    assert budget.assets == 2
    assert any(issue["code"] == scanbudget.ISSUE_ASSETS for issue in budget.issues())


@pytest.mark.parametrize("strict", [False, True])
def test_budgeted_unreadable_directory_remains_disclosed_or_strict(
    tmp_path, monkeypatch, walk, strict
):
    (tmp_path / "locked").mkdir()
    real_scandir = project_refresh.os.scandir
    error = PermissionError("directory unavailable")  # No filename: use the known directory.

    def scandir(path):
        if Path(path).name == "locked":
            raise error
        return real_scandir(path)

    budget = scanbudget.Budget()
    with monkeypatch.context() as patch:
        patch.setattr(project_refresh.os, "scandir", scandir)
        if strict:
            with pytest.raises(PermissionError) as caught:
                walk(tmp_path, strict=True, budget=budget)
            assert caught.value is error
            assert budget.issues() == []
        else:
            assert walk(tmp_path, budget=budget) == []
            assert budget.issues() == [
                {
                    "code": scanbudget.ISSUE_UNREADABLE_DIR,
                    "severity": scanbudget.SEVERITY_PARTIAL,
                    "scope": "dir",
                    "path": "locked",
                    "count": 1,
                }
            ]


@pytest.mark.parametrize("reason", [scanbudget.ISSUE_TIME, scanbudget.ISSUE_CANCELLED])
def test_script_budget_remains_live_while_classifying_collected_entries(
    tmp_path, monkeypatch, reason
):
    for index in range(80):
        (tmp_path / f"script{index}.py").touch()
    inspected = []
    real_is_dir = Path.is_dir

    def is_dir(path):
        inspected.append(path)
        return real_is_dir(path)

    budget = scanbudget.Budget(
        limits=scanbudget.Limits(max_seconds=2),
        clock=lambda: float(len(inspected)) if reason == scanbudget.ISSUE_TIME else 0.0,
        cancel=lambda: reason == scanbudget.ISSUE_CANCELLED and len(inspected) >= 2,
    )
    with monkeypatch.context() as patch:
        patch.setattr(Path, "is_dir", is_dir)
        discover.iter_all_scripts(tmp_path, budget=budget)
    assert 0 < len(inspected) <= 4
    assert budget.stopped == reason
