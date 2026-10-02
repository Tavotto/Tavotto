"""Save provenance is per occurrence, independent of legacy first-stem capture.

These are metadata assertions, not evidence of a captured historical artist scene.
Real worker/playground coverage also verifies that observation writes no original files.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import figcapture
from test_compat_capture_parity import browser_load, desktop_build, write
from test_savefig_capture_params import WORKER_PY, needs_worker


class Figure:
    def __init__(self):
        self.size = [4.0, 3.0]
        self.dpi = 200.0
        self._original_dpi = 100.0

    def get_size_inches(self):
        return self.size


def observations(**kwargs):
    kwargs.setdefault("metadata", lambda fig: (fig.size, fig.dpi, fig._original_dpi))
    return figcapture.SavefigObservations(**kwargs)


def record(ledger, fig, fname="same.pdf", *, call=None, **kw):
    return ledger.record(
        fig,
        fname,
        "same",
        {"format": "pdf", "dpi": "figure", "bbox_inches": None} if call is None else call,
        **kw,
    )


def test_two_figures_with_one_stem_have_independent_occurrences(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ledger = observations(project_root=tmp_path)
    first, second = Figure(), Figure()
    for fig in (first, second, first):
        ledger.complete(record(ledger, fig), "intercepted")
    report = ledger.report()
    assert report["complete"] and report["observed_count"] == 3
    assert [r["occurrence"] for r in report["records"]] == [1, 2, 3]
    assert [r["figure_ordinal"] for r in report["records"]] == [1, 2, 1]
    assert {r["destination"]["path"] for r in report["records"]} == {"same.pdf"}


def test_save_time_metadata_and_reports_do_not_alias_later_mutations(tmp_path):
    ledger, fig = observations(project_root=tmp_path), Figure()
    call = {"format": "pdf", "dpi": "figure", "bbox_inches": [0, 0, 4, 3]}
    for n in range(3):
        record(ledger, fig, tmp_path / "same.pdf", call=call)
        fig.size[0] += 1
        fig.dpi += 50
        call["bbox_inches"][2] += 1
    rows = ledger.report()["records"]
    assert [r["figure_ordinal"] for r in rows] == [1, 1, 1]
    assert [r["figure_size_inches"] for r in rows] == [[4, 3], [5, 3], [6, 3]]
    assert [r["figure_dpi"] for r in rows] == [200, 250, 300]
    assert [r["resolved_dpi"] for r in rows] == [100, 100, 100]
    assert [r["options"]["bbox_inches"][2] for r in rows] == [4, 5, 6]
    rows[0]["options"]["bbox_inches"][2] = 999
    assert ledger.report()["records"][0]["options"]["bbox_inches"][2] == 4


def test_paths_use_the_actual_save_cwd_and_keep_sandbox_identity(tmp_path, monkeypatch):
    project, sandbox = tmp_path / "project", tmp_path / "sandbox"
    for path in (project / "nested", project / "other", sandbox):
        path.mkdir(parents=True)
    ledger, fig = (
        observations(project_root=project, execution_root=sandbox),
        Figure(),
    )
    monkeypatch.chdir(project / "nested")
    record(ledger, fig, "../other/same.pdf")
    record(ledger, fig, Path("same.pdf"))
    monkeypatch.chdir(sandbox)
    record(ledger, fig, b"same.pdf")
    record(ledger, fig, project / "same.pdf")
    record(ledger, fig, tmp_path / "outside-secret.pdf")
    rows = ledger.report()["records"]
    assert [r["destination"] for r in rows] == [
        {"scope": "project", "path": "other/same.pdf"},
        {"scope": "project", "path": "nested/same.pdf"},
        {"scope": "execution", "path": "same.pdf"},
        {"scope": "project", "path": "same.pdf"},
        {"scope": "unresolved", "path": None},
    ]
    assert str(tmp_path) not in json.dumps(ledger.report())
    assert "outside-secret" not in json.dumps(ledger.report())


@pytest.mark.parametrize(
    ("fname", "fmt", "explicit", "expected"),
    [
        ("same", "png", False, None),
        ("same.", "pdf", False, None),
        ("same.pdf", "png", True, "same.pdf"),
        ("same", "png", True, "same"),
    ],
)
def test_destination_tracks_format_without_rewriting_explicit_filenames(
    tmp_path, monkeypatch, fname, fmt, explicit, expected
):
    monkeypatch.chdir(tmp_path)
    ledger = observations(project_root=tmp_path)
    record(ledger, Figure(), fname, call={"format": fmt, "dpi": 72}, explicit_format=explicit)
    row = ledger.report()["records"][0]
    assert row["destination"] == {
        "scope": "project" if expected else "unresolved",
        "path": expected,
    }
    assert row["options"]["format"] == (fmt if explicit else None)
    assert row["resolved_dpi"] == 72


def test_observation_does_not_evaluate_custom_paths_or_representations(tmp_path):
    class UserPath:
        def __fspath__(self):
            raise AssertionError("the observer must not call this again")

    class UserValue:
        def __repr__(self):
            raise AssertionError("the observer must not represent user objects")

    ledger, fig = observations(project_root=tmp_path), Figure()
    record(ledger, fig, UserPath())
    row = ledger.report()["records"][0]
    assert row["destination"] == {"scope": "unresolved", "path": None}
    assert record(ledger, fig, call={"format": UserValue()}) is None
    assert ledger.report()["complete"] is False
    assert ledger.report()["observed_count"] == 2


def test_failed_observation_does_not_change_save_control_flow():
    class BrokenFigure(Figure):
        def get_size_inches(self):
            raise AssertionError("observation must not invoke a user getter")

    ledger = figcapture.SavefigObservations()  # no trusted adapter available
    index = record(ledger, BrokenFigure())
    ledger.complete(index, "saved")
    report = ledger.report()
    assert report["complete"] is False and report["observed_count"] == 1
    row = report["records"][0]
    assert row["figure_ordinal"] == 1 and row["result"] == "saved"
    assert row["figure_size_inches"] is row["figure_dpi"] is row["resolved_dpi"] is None


def test_streams_are_not_path_occurrences():
    ledger = observations()
    assert record(ledger, Figure(), io.BytesIO()) is None
    assert ledger.report()["observed_count"] == 0
    assert ledger.report()["complete"] is True


def test_bounded_history_never_claims_last_record_is_last_save(tmp_path):
    ledger, fig = observations(project_root=tmp_path), Figure()
    for _ in range(figcapture.MAX_SAVEFIG_OBSERVATIONS + 2):
        record(ledger, fig, tmp_path / "same.pdf")
    report = ledger.report()
    assert len(report["records"]) == figcapture.MAX_SAVEFIG_OBSERVATIONS
    assert report["observed_count"] == figcapture.MAX_SAVEFIG_OBSERVATIONS + 2
    assert report["complete"] is False


def test_oversize_or_nonfinite_records_are_incomplete():
    for call in ({"format": "x" * figcapture.MAX_SAVEFIG_OBSERVATION_BYTES}, {"dpi": float("nan")}):
        ledger = observations()
        assert record(ledger, Figure(), call=call) is None
        assert ledger.report()["complete"] is False


def test_success_failure_and_custom_backend_are_explicit():
    ledger, fig = observations(), Figure()
    first = record(ledger, fig, backend=object())
    ledger.complete(first, "failed")
    second = record(ledger, fig)
    ledger.complete(second, "saved")
    rows = ledger.report()["records"]
    assert [(r["backend"], r["result"]) for r in rows] == [
        ("custom", "failed"),
        ("default", "saved"),
    ]


SCRIPT = """\
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(4, 3), dpi=100)
ax.plot([1, 2, 3], color='red')
fig.savefig('same.pdf', bbox_inches='tight', pad_inches=0.02)
fig.set_size_inches(5, 3)
fig.set_dpi(200)
ax.lines[0].set_color('blue')
fig.savefig('same.png', dpi=300)
other, ax2 = plt.subplots(figsize=(6, 2))
ax2.plot([3, 2, 1])
other.savefig('same.pdf')
plt.close('all')
"""


@needs_worker
def test_real_worker_and_playground_observe_all_occurrences_without_changing_capture(tmp_path):
    project, workspace = tmp_path / "project", tmp_path / "browser"
    write(project, "figure.py", SCRIPT)
    original = project / "same.pdf"
    original.write_bytes(b"original file must remain unchanged")
    desktop = desktop_build(project, "figure.py")
    browser = browser_load(SCRIPT, "figure.py", workspace)
    assert browser.get("ok"), browser
    assert desktop["savefig_observations"] == browser["savefig_observations"]
    report = desktop["savefig_observations"]
    assert report["complete"] and report["observed_count"] == 3
    rows = report["records"]
    assert [r["figure_ordinal"] for r in rows] == [1, 1, 2]
    assert [r["figure_size_inches"] for r in rows] == [[4, 3], [5, 3], [6, 2]]
    assert [r["resolved_dpi"] for r in rows] == [100, 300, 100]
    assert [r["destination"] for r in rows] == [
        {"scope": "execution", "path": "same.pdf"},
        {"scope": "execution", "path": "same.png"},
        {"scope": "execution", "path": "same.pdf"},
    ]
    assert {r["result"] for r in rows} == {"intercepted"}
    for result in (desktop, browser):
        (descriptor,) = result["descriptors"]
        assert [c["format"] for c in descriptor["savefig_calls"]] == ["pdf", "png"]
    assert original.read_bytes() == b"original file must remain unchanged"
    assert not (project / "same.png").exists()
    assert not (workspace / "same.pdf").exists()


@needs_worker
def test_worker_keeps_paper_style_alias_separate_from_destination(tmp_path):
    write(
        tmp_path, "paper_style.py", "def save(fig, stem):\n    fig.savefig(stem + '_final.pdf')\n"
    )
    write(
        tmp_path,
        "figure.py",
        "import matplotlib.pyplot as plt\nimport paper_style\npaper_style.save(plt.figure(), 'alias')\n",
    )
    report = desktop_build(tmp_path, "figure.py")["savefig_observations"]
    (row,) = report["records"]
    assert row["stem"] == "alias"
    assert row["destination"] == {"scope": "execution", "path": "alias_final.pdf"}


def test_absolute_capture_alias_is_not_disclosed(tmp_path):
    ledger = observations(project_root=tmp_path)
    ledger.record(
        Figure(), tmp_path / "same.pdf", str(tmp_path / "private-alias"), {"format": "pdf"}
    )
    report = ledger.report()
    assert report["records"][0]["stem"] is None
    assert str(tmp_path) not in json.dumps(report)


@needs_worker
def test_empty_filename_is_counted_without_changing_legacy_capture(tmp_path):
    source = "import matplotlib.pyplot as plt\nplt.figure().savefig('')\n"
    project = tmp_path / "project"
    write(project, "figure.py", source)
    for result in (
        desktop_build(project, "figure.py"),
        browser_load(source, "figure.py", tmp_path / "browser"),
    ):
        report = result["savefig_observations"]
        assert report["observed_count"] == 1
        assert report["complete"] is False, "legacy capture did not observe the parameters"
        (row,) = report["records"]
        assert row["destination"] == {"scope": "unresolved", "path": None}
        assert row["result"] == "intercepted"


def test_record_budget_includes_the_final_status():
    ledger, fig = observations(), Figure()
    call = {"format": "pdf", "facecolor": ""}
    record(ledger, fig, call=call)
    size = len(json.dumps(ledger.report()["records"][0], ensure_ascii=False).encode("utf-8"))
    available = figcapture.MAX_SAVEFIG_OBSERVATION_BYTES - size - 4
    for extra in (0, 1):
        bounded = observations()
        index = record(bounded, fig, call={**call, "facecolor": "x" * (available + extra)})
        if extra:
            assert index is None and bounded.report()["complete"] is False
        else:
            bounded.complete(index, "intercepted")
            row = bounded.report()["records"][0]
            assert (
                len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                == figcapture.MAX_SAVEFIG_OBSERVATION_BYTES
            )


@needs_worker
def test_standard_metadata_adapter_uses_storage_without_custom_getters():
    # The pytest parent need not contain Matplotlib; use the discovered scientific Python.
    body = r"""
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import figcapture
import matplotlib.figure as mfigure
reader = figcapture.savefig_metadata_reader(mfigure)
figure = mfigure.Figure(figsize=(4, 3), dpi=100)
figure._original_dpi = 80
ledger = figcapture.SavefigObservations(metadata=reader)
call = {"format": "pdf", "dpi": "figure", "bbox_inches": None}
ledger.record(figure, "same.pdf", "same", call)
assert ledger.report()["complete"]
assert ledger.report()["records"][0]["figure_size_inches"] == [4, 3]
assert ledger.report()["records"][0]["resolved_dpi"] == 80
calls = []
def user_getter(*args):
    calls.append("getter")
    raise AssertionError("observation called a user getter")
with patch.object(figure, "get_size_inches", user_getter):
    assert reader(figure) is None
with patch.object(mfigure.Figure, "dpi", property(user_getter)):
    assert reader(figure) is None
with patch.object(figure.bbox_inches, "get_points", user_getter):
    assert reader(figure) is None
with patch.object(mfigure.Figure, "_dpi", property(user_getter), create=True):
    assert reader(figure) is None
class UserNumber:
    def __float__(self):
        calls.append("float")
        raise AssertionError("observation converted a user number")
figure.__dict__["_dpi"] = UserNumber()
index = ledger.record(figure, "same.pdf", "same", call)
ledger.complete(index, "saved")
row = ledger.report()["records"][-1]
assert not ledger.report()["complete"]
assert row["result"] == "saved" and row["figure_ordinal"] == 1
assert row["figure_size_inches"] is row["figure_dpi"] is row["resolved_dpi"] is None
assert calls == []
"""
    result = subprocess.run(
        [WORKER_PY, "-c", body, str(Path(figcapture.__file__).parent)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_missing_standard_metadata_adapter_is_incomplete_not_an_import_error():
    from types import SimpleNamespace

    for unknown in (
        SimpleNamespace(),
        SimpleNamespace(Figure=object, Bbox=object, np=SimpleNamespace()),
        SimpleNamespace(Figure=object, Bbox=object, np=SimpleNamespace(ndarray=list)),
    ):
        assert figcapture.savefig_metadata_reader(unknown) is None
