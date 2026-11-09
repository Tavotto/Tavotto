"""Layer 2 of #1809: content check of conversion outputs against the project's old results.

Comparator unit tests are stdlib-only (hand-made xlsx, no openpyxl). The integration cases run the real safe
worker through `run_validation`: the candidate files are compared before the redirect area / sandbox goes."""

# ruff: noqa: F811 - imported pytest fixture
from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

import pytest

from tavotto.engine import ai_convert, convertcompare, workdir
from test_convert_shadow_validation import world  # noqa: F401


# ----------------------------------------------------------------------------- helpers
def make_xlsx(path: Path, sheets: dict[str, dict[str, object]], dimension: str = "A1") -> None:
    """A minimal workbook; `dimension` is declared but cells may lie far outside it (as real writers do)."""
    rels, wb_sheets, files = [], [], {}
    for i, (name, cells) in enumerate(sheets.items(), 1):
        rows: dict[int, list[str]] = {}
        for ref, value in cells.items():
            row = int("".join(ch for ch in ref if ch.isdigit()))
            if isinstance(value, str):
                cell = f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
            else:
                cell = f'<c r="{ref}"><v>{value}</v></c>'
            rows.setdefault(row, []).append(cell)
        body = "".join(f'<row r="{r}">{"".join(c)}</row>' for r, c in sorted(rows.items()))
        files[f"xl/worksheets/sheet{i}.xml"] = (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="{dimension}"/><sheetData>{body}</sheetData></worksheet>'
        )
        rels.append(
            f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" Type="worksheet"/>'
        )
        wb_sheets.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
    files["xl/workbook.xml"] = (
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(wb_sheets)}</sheets></workbook>"
    )
    files["xl/_rels/workbook.xml.rels"] = (
        f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rels)}</Relationships>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)


def deadline() -> float:
    return time.monotonic() + 5


def cmp(tmp_path, suffix, old, new):
    a, b = tmp_path / f"old{suffix}", tmp_path / f"new{suffix}"
    a.write_text(old, encoding="utf-8")
    b.write_text(new, encoding="utf-8")
    return convertcompare.compare_files(a, b, deadline())


# ----------------------------------------------------------------------------- comparators
def test_csv_numbers_use_tolerance_and_text_is_exact(tmp_path):
    same = cmp(tmp_path, ".csv", "a,b\n1,x\n0.1,y\n", "a,b\n1.0,x\n0.10000000000001,y\n")
    assert same["status"] == "match"
    got = cmp(tmp_path, ".csv", "a,b\n1,x\n2,y\n", "a,b\n1,x\n2.001,Y\n")
    assert got["status"] == "differs" and got["diff_count"] == 2
    assert got["diffs"] == [{"row": 3, "col": 1}, {"row": 3, "col": 2}]
    assert "2.001" not in str(got) and "Y" not in str(got), "coordinates only, never values"


def test_csv_shape_must_match_and_tsv_is_cell_based(tmp_path):
    assert cmp(tmp_path, ".csv", "a,b\n1,2\n", "a,b\n1,2\n3,4\n")["diffs"] == [{"kind": "shape"}]
    assert cmp(tmp_path, ".csv", "a,b\n1,2\n", "a,b,c\n1,2,3\n")["status"] == "differs"
    assert cmp(tmp_path, ".tsv", "a\tb\n1\t2\n", "a\tb\n1\t2.0\n")["status"] == "match"


def test_txt_and_log_are_compared_line_by_line_exactly(tmp_path):
    assert cmp(tmp_path, ".log", "a\nb\n", "a\nb\n")["status"] == "match"
    got = cmp(tmp_path, ".txt", "a\nb\nc\n", "a\nB\nc\n")
    assert got["status"] == "differs" and got["diffs"] == [{"line": 2}]
    assert cmp(tmp_path, ".txt", "a\n", "a\nb\n")["diffs"] == [{"kind": "shape"}]


def test_xlsx_cells_beyond_the_declared_dimension_are_still_compared(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    make_xlsx(a, {"Data": {"A1": "id", "H2": 5, "B3": "t"}}, dimension="A1")
    make_xlsx(b, {"Data": {"A1": "id", "H2": 5, "B3": "t"}}, dimension="A1:B3")
    assert convertcompare.compare_files(a, b, deadline())["status"] == "match"
    make_xlsx(b, {"Data": {"A1": "id", "H2": 6, "B3": "t"}}, dimension="A1")
    got = convertcompare.compare_files(a, b, deadline())
    assert got["status"] == "differs" and got["diffs"] == [{"sheet": "Data", "cell": "H2"}]


def test_xlsx_sheet_order_and_names_matter_and_extra_cells_count(tmp_path):
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    make_xlsx(a, {"One": {"A1": 1}, "Two": {"A1": 2}})
    make_xlsx(b, {"Two": {"A1": 2}, "One": {"A1": 1}})
    assert convertcompare.compare_files(a, b, deadline())["diffs"] == [{"kind": "sheets"}]
    make_xlsx(b, {"One": {"A1": 1, "C9": "x"}, "Two": {"A1": 2}})
    got = convertcompare.compare_files(a, b, deadline())
    assert got["diffs"] == [{"sheet": "One", "cell": "C9"}]


def test_unsupported_unreadable_and_oversized_files_are_incomparable(tmp_path, monkeypatch):
    assert cmp(tmp_path, ".rds", "x", "x") == {
        "status": "incomparable",
        "diff_count": 0,
        "diffs": [],
        "reason": "format",
    }
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    assert convertcompare.compare_files(bad, bad, deadline())["reason"] == "format"
    monkeypatch.setattr(convertcompare, "MAX_FILE_BYTES", 4)
    assert cmp(tmp_path, ".csv", "a,b\n1,2\n", "a,b\n1,2\n")["reason"] == "too_large"


def test_a_comparator_fault_is_incomparable_not_an_exception(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(convertcompare, "_compare_delimited", boom)
    assert cmp(tmp_path, ".csv", "a\n1\n", "a\n1\n")["reason"] == "compare_failed"


# ----------------------------------------------------------------------------- real validation
SOURCE = b'd <- read.csv("data.csv")\nwrite.csv(d, "report.csv", row.names = FALSE)\nplot(1)\n'
PLOT = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('figure.png')\n"
READ = "data = open('data.csv', encoding='utf-8').read()\n"
GOOD_CSV = "open('report.csv', 'w').write('x,y\\n1,2\\n')\n"


def old_result(root: Path, text: str | None, *, newer: bool = True):
    data = root / "sub" / "data.csv"
    if text is None:
        return
    old = root / "sub" / "report.csv"
    old.write_text(text, encoding="utf-8")
    base = data.stat().st_mtime_ns
    stamp = base + 5_000_000_000 if newer else base - 5_000_000_000
    os.utime(old, ns=(stamp, stamp))


def run(world, candidate, *, mode=workdir.MODE_PROJECT, source=SOURCE):
    root, cand, runner = world
    workdir.set_mode(root, mode)
    cand.base_bytes = source
    cand.new_bytes = candidate.encode()
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    return result["output_evidence"]["files"], result


@pytest.mark.parametrize("mode", [workdir.MODE_PROJECT, workdir.MODE_SANDBOX])
def test_fresh_identical_old_result_is_a_match(world, mode):
    old_result(world[0], "x,y\n1.0,2\n")
    files, _ = run(world, READ + GOOD_CSV + PLOT, mode=mode)
    assert files == [{"name": "report.csv", "line": 2, "state": "match"}]


@pytest.mark.parametrize("mode", [workdir.MODE_PROJECT, workdir.MODE_SANDBOX])
def test_one_changed_cell_is_a_difference_with_its_coordinate(world, mode):
    old_result(world[0], "x,y\n1,3\n")
    files, _ = run(world, READ + GOOD_CSV + PLOT, mode=mode)
    assert files[0]["state"] == "differs" and files[0]["diff_count"] == 1
    assert files[0]["diffs"] == [{"row": 2, "col": 2}]
    assert "3" not in str(files[0]["diffs"])


def test_an_old_result_older_than_the_inputs_is_stale(world):
    old_result(world[0], "x,y\n1,2\n", newer=False)
    files, _ = run(world, READ + GOOD_CSV + PLOT)
    assert (files[0]["state"], files[0]["reason"]) == ("incomparable", "stale")


def test_an_old_result_with_the_same_mtime_as_the_newest_input_is_stale(world):
    root = world[0]
    old_result(root, "x,y\n1,2\n")
    stamp = (root / "sub" / "data.csv").stat().st_mtime_ns
    os.utime(root / "sub" / "report.csv", ns=(stamp, stamp))
    files, _ = run(world, READ + GOOD_CSV + PLOT)
    assert (files[0]["state"], files[0]["reason"]) == ("incomparable", "stale")


def test_no_old_result_is_no_previous(world):
    files, _ = run(world, READ + GOOD_CSV + PLOT)
    assert (files[0]["state"], files[0]["reason"]) == ("incomparable", "no_previous")


def test_unknown_inputs_are_not_guessed(world):
    old_result(world[0], "x,y\n1,2\n")
    files, _ = run(world, GOOD_CSV + PLOT)  # the candidate read nothing the observer could see
    assert (files[0]["state"], files[0]["reason"]) == ("incomparable", "inputs_unknown")


def test_unsupported_format_and_oversize_do_not_stop_validation(world, monkeypatch):
    root = world[0]
    old_result(root, "x,y\n1,2\n")
    (root / "sub" / "obj.rds").write_bytes(b"old")
    source = SOURCE + b'saveRDS(d, "obj.rds")\n'
    code = READ + GOOD_CSV + "open('obj.rds', 'wb').write(b'new')\n" + PLOT
    files, _ = run(world, code, source=source)
    states = {f["name"]: (f["state"], f.get("reason")) for f in files}
    assert states["obj.rds"] == ("incomparable", "format")
    monkeypatch.setattr(convertcompare, "MAX_FILE_BYTES", 3)
    files, result = run(world, code, source=source)
    assert {f["name"]: f.get("reason") for f in files}["report.csv"] == "too_large"
    assert result["status"] == "ok"


def test_comparator_fault_leaves_the_validation_ok(world, monkeypatch):
    old_result(world[0], "x,y\n1,2\n")

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(convertcompare, "_compare_delimited", boom)
    files, result = run(world, READ + GOOD_CSV + PLOT)
    assert (files[0]["state"], files[0]["reason"]) == ("incomparable", "compare_failed")
    assert result["status"] == "ok"


def test_xlsx_with_a_stale_declared_range_is_compared_by_real_cells(world, tmp_path):
    root = world[0]
    old = root / "sub" / "book.xlsx"
    new = tmp_path / "new.xlsx"
    make_xlsx(old, {"S": {"A1": 1, "H2": 5}}, dimension="A1")
    make_xlsx(new, {"S": {"A1": 1, "H2": 6}}, dimension="A1")
    data = root / "sub" / "data.csv"
    stamp = data.stat().st_mtime_ns + 5_000_000_000
    os.utime(old, ns=(stamp, stamp))
    source = b'd <- read.csv("data.csv")\nwritexl::write_xlsx(d, "book.xlsx")\nplot(1)\n'
    code = READ + f"import shutil\nshutil.copyfile({str(new)!r}, 'book.xlsx')\n" + PLOT
    files, _ = run(world, code, source=source)
    assert files[0]["state"] == "differs" and files[0]["diffs"] == [{"sheet": "S", "cell": "H2"}]


def test_nothing_content_related_leaves_the_evidence_except_coordinates(world):
    old_result(world[0], "x,y\nSECRET,3\n")
    _, result = run(world, READ + GOOD_CSV + PLOT)
    assert "SECRET" not in repr(result["output_evidence"])


# ----------------------------------------------------------------------------- "让 AI 再对一次" (one bounded correction)
from test_ai_convert_app import GOOD, _start, _wait_final, project  # noqa: E402,F401

R_REPORT = 'd <- read.csv("data.csv")\nwrite.csv(d, "report.csv")\nplot(1)\n'
OLD = "x,y\n1,2\n"
WRONG = READ + "open('report.csv', 'w').write('x,y\\n1,3\\n')\n" + GOOD
RIGHT = READ + "open('report.csv', 'w').write('x,y\\n1,2\\n')\n" + GOOD


def _ready_with_difference(project, monkeypatch, later: str):
    m, client, figs, state = project
    worker_tmp = figs.parent / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(figs.parent / "localappdata"))
    (figs / "fig.R").write_bytes(R_REPORT.encode())
    (figs / "data.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (figs / "report.csv").write_text(OLD, encoding="utf-8")
    stamp = (figs / "data.csv").stat().st_mtime_ns + 5_000_000_000
    os.utime(figs / "report.csv", ns=(stamp, stamp))
    workdir.set_mode(figs, workdir.MODE_PROJECT)
    state["code"] = (
        "p = d / 'fig_matplotlib.py'; "
        f"p.write_text({later!r} if p.exists() else {WRONG!r}, encoding='utf-8')"
    )
    sid = _start(
        client, script="fig.R", library=None, source_language="r", path="out.png"
    ).get_json()["session"]
    _wait_final(client, sid)
    status = client.get(f"/api/ai/convert/{sid}").get_json()
    return client, sid, status, state, figs


def _wait_idle(client, sid):
    end = time.time() + 60
    while time.time() < end:
        got = client.get(f"/api/ai/convert/{sid}").get_json()
        if not got.get("realigning"):
            return got
        time.sleep(0.1)
    raise AssertionError("realign did not finish")


def test_a_difference_can_be_realigned_once_and_is_rechecked(project, monkeypatch):
    client, sid, status, state, _figs = _ready_with_difference(project, monkeypatch, RIGHT)
    try:
        assert status["state"] == "ready"
        files = status["comparison"]["output_evidence"]["files"]
        assert files[0]["state"] == "differs" and files[0]["diffs"] == [{"row": 2, "col": 2}]
        assert len(state["prompts"]) == 1, (
            "a difference is the user's call, never an automatic round"
        )
        assert client.post(f"/api/ai/convert/{sid}/realign").status_code == 202
        got = _wait_idle(client, sid)
        assert len(state["prompts"]) == 2
        assert "row 2 column 2" in state["prompts"][1] and "report.csv" in state["prompts"][1]
        assert "1,3" not in state["prompts"][1], "coordinates only, no data values"
        assert got["state"] == "ready"
        assert got["comparison"]["output_evidence"]["files"][0]["state"] == "match"
        assert client.post(f"/api/ai/convert/{sid}/realign").status_code == 409, (
            "nothing differs now"
        )
    finally:
        workdir.forget(_figs)


def test_a_realign_that_does_not_help_restores_the_adoptable_candidate(project, monkeypatch):
    client, sid, status, _state, figs = _ready_with_difference(
        project, monkeypatch, "this is not python (\n"
    )
    try:
        before = (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").read_bytes()
        assert client.post(f"/api/ai/convert/{sid}/realign").status_code == 202
        got = _wait_idle(client, sid)
        assert got["state"] == "ready"
        assert got["comparison"]["output_evidence"]["files"][0]["state"] == "differs"
        assert (ai_convert.CONVERT_DIR / sid / "fig_matplotlib.py").read_bytes() == before
        assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200
    finally:
        workdir.forget(figs)


def test_adopting_or_realigning_while_a_realign_runs_says_so(project, monkeypatch):
    import threading

    client, sid, _status, _state, figs = _ready_with_difference(project, monkeypatch, RIGHT)
    ai_convert._RESUMES[sid] = (
        threading.Event()
    )  # what `prepare_realign` registers for the running job
    try:
        for url in ("adopt", "realign"):
            got = client.post(f"/api/ai/convert/{sid}/{url}")
            assert got.status_code == 409 and got.get_json()["code"] == "convert_realigning"
    finally:
        ai_convert._RESUMES.pop(sid, None)
        workdir.forget(figs)
