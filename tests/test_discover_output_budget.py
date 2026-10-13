"""Wildcard output discovery: measure the real listing, claims and incomplete reasons (#684).

The subject is the output listing used by static registration, rather than script enumeration or
the import scan (which intentionally does no disk globbing). Temporary files and symlinks are real;
the scoped scandir seam counts attempted directory reads without including unrelated walks.
"""

from pathlib import Path

import pytest

from tavotto.engine import discover, scanbudget


def _count_listings(monkeypatch):
    listed = []
    real = discover._output_scandir

    def scandir(path):
        listed.append(Path(path))
        return real(path)

    monkeypatch.setattr(discover, "_output_scandir", scandir)
    return listed


def _symlink(path, target):
    try:
        path.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlink creation is unavailable")


def test_patterns_extensions_and_scripts_share_one_output_listing(tmp_path, monkeypatch):
    outputs = tmp_path / "panels"
    outputs.mkdir()
    for name in ("Alpha_a.pdf", "Alpha_b.png", "Beta_c.svg", "Alpha_noise.csv"):
        (outputs / name).touch()
    for script, prefix in (("one.py", "Alpha"), ("two.py", "Beta")):
        (tmp_path / script).write_text(f'def main():\n    fig.savefig(f"{prefix}_{{key}}.pdf")\n')
    listed = _count_listings(monkeypatch)
    report = discover.discover(tmp_path)
    assert report["scripts"]["one.py"]["stems"] == ["Alpha_a", "Alpha_b"]
    assert report["scripts"]["two.py"]["stems"] == ["Beta_c"]
    assert listed.count(tmp_path) == 1
    assert listed.count(outputs) == 1


def test_literals_and_import_scan_do_not_open_output_directories(tmp_path, monkeypatch):
    def forbidden(path):
        raise AssertionError(f"unexpected output listing: {path}")

    monkeypatch.setattr(discover, "_output_scandir", forbidden)
    assert discover._resolve({"known"}, tmp_path) == ({"known"}, [], {})
    assert discover._resolve({"known", "dynamic_*"}, tmp_path, glob_disk=False) == (
        {"known"},
        ["dynamic_*"],
        {},
    )


def test_budget_exhaustion_keeps_wildcards_unresolved_even_after_a_hit(tmp_path, monkeypatch):
    for name in ("Figure_hit.pdf", "data.csv", "more.csv"):
        (tmp_path / name).touch()
    budget = scanbudget.Budget(scanbudget.Limits(max_entries=1))
    index = discover._OutputIndex(tmp_path, budget)
    listed = _count_listings(monkeypatch)
    stems, unresolved, reasons = discover._resolve(
        {"literal", "Figure_*", "Other_*"}, tmp_path, output_index=index
    )
    assert stems == {"literal"}
    assert unresolved == ["Figure_*", "Other_*"]
    assert set(reasons.values()) == {scanbudget.ISSUE_ENTRIES}
    assert budget.entries == 2  # Budget counts the first item beyond the configured ceiling.
    assert listed == [tmp_path]
    discover._resolve({"Again_*"}, tmp_path, output_index=index)
    assert listed == [tmp_path]


@pytest.mark.parametrize("reason", [scanbudget.ISSUE_CANCELLED, scanbudget.ISSUE_TIME])
def test_cancellation_and_deadline_stop_before_opening_disk(tmp_path, monkeypatch, reason):
    ticks = iter([0.0, 21.0])
    budget = (
        scanbudget.Budget(cancel=lambda: True)
        if reason == scanbudget.ISSUE_CANCELLED
        else scanbudget.Budget(clock=lambda: next(ticks))
    )
    listed = _count_listings(monkeypatch)
    assert discover._resolve(
        {"literal", "Figure_*"}, tmp_path, output_index=discover._OutputIndex(tmp_path, budget)
    ) == ({"literal"}, ["Figure_*"], {"Figure_*": reason})
    assert listed == []
    assert budget.entries == 0


def test_project_external_link_is_not_opened_or_claimed(tmp_path, monkeypatch):
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "Secret_result.pdf").touch()
    _symlink(root / "external", outside)
    listed = _count_listings(monkeypatch)
    assert discover._resolve({"Secret_*"}, root) == (set(), ["Secret_*"], {})
    assert listed == [root]


def test_cached_output_matching_still_observes_cancellation(tmp_path):
    (tmp_path / "Figure_real.pdf").touch()
    cancelled = False
    budget = scanbudget.Budget(cancel=lambda: cancelled)
    index = discover._OutputIndex(tmp_path, budget)
    index.load()
    assert index.reason is None
    cancelled = True
    assert discover._resolve({"Figure_*"}, tmp_path, output_index=index) == (
        set(),
        ["Figure_*"],
        {"Figure_*": scanbudget.ISSUE_CANCELLED},
    )


def test_link_cycle_is_never_opened(tmp_path, monkeypatch):
    inner = tmp_path / "panels"
    inner.mkdir()
    (inner / "Figure_real.pdf").touch()
    _symlink(inner / "loop", tmp_path)
    listed = _count_listings(monkeypatch)
    assert discover._resolve({"Figure_*", "Missing_*"}, tmp_path) == (
        {"Figure_real"},
        ["Missing_*"],
        {},
    )
    assert listed == [tmp_path, inner]


def test_tool_directories_are_pruned_before_they_are_opened(tmp_path, monkeypatch):
    for name in (".venv", "node_modules", "tavottofile"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "Figure_noise.pdf").touch()
    (tmp_path / "Figure_real.pdf").touch()
    listed = _count_listings(monkeypatch)
    assert discover._resolve({"Figure_*"}, tmp_path) == ({"Figure_real"}, [], {})
    assert listed == [tmp_path]


def test_depth_limit_is_reported_instead_of_claiming_a_partial_match(tmp_path):
    deep = tmp_path / "panels"
    deep.mkdir()
    (tmp_path / "Figure_first.pdf").touch()
    (deep / "Figure_second.pdf").touch()
    index = discover._OutputIndex(tmp_path, scanbudget.Budget(scanbudget.Limits(max_asset_depth=0)))
    assert discover._resolve({"Figure_*"}, tmp_path, output_index=index) == (
        set(),
        ["Figure_*"],
        {"Figure_*": scanbudget.ISSUE_DEPTH},
    )


def test_scan_report_and_cli_explain_incomplete_wildcards(tmp_path, monkeypatch, capsys):
    script = tmp_path / "figure.py"
    script.write_text('def main():\n    fig.savefig(f"Figure_{key}.pdf")\n')
    (tmp_path / "data.csv").touch()
    real_index = discover._OutputIndex
    monkeypatch.setattr(
        discover,
        "_OutputIndex",
        lambda root: real_index(root, scanbudget.Budget(scanbudget.Limits(max_entries=0))),
    )
    report = discover.discover(tmp_path)
    info = report["scripts"]["figure.py"]
    assert info["stems"] == []
    assert info["unresolved"] == ["Figure_*"]
    assert info["unresolved_reasons"] == {"Figure_*": scanbudget.ISSUE_ENTRIES}
    discover._print_report(report)
    assert "Figure_* (entry_budget)" in capsys.readouterr().out
