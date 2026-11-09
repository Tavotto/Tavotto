"""Conversion must not certify a candidate that silently drops a certain source output (issue #1809).

Static extraction is exercised on text only (R / MATLAB are never run). The judgement uses the real safe
worker and the #1516 redirect area; the four terminal states (written / corrected / still missing /
unknown) run through the real task pipeline with a scripted CLI.
"""

# ruff: noqa: F811 - imported pytest fixtures
from __future__ import annotations

import pytest

from tavotto.engine import ai_convert, convertsrcwrites, workdir
from test_ai_convert_app import (  # noqa: F401
    GOOD,
    WORKER_PY,
    _start,
    _wait_final,
    project,
)
from test_convert_shadow_validation import world  # noqa: F401


def names(source: str, language: str = "r") -> list[str]:
    return [w["name"] for w in convertsrcwrites.certain_writes(source.encode(), language)]


# ----------------------------------------------------------------- static extraction (no worker)
def test_r_literal_writes_are_certain_with_source_lines():
    src = (
        'd <- read.csv("input.csv")\n'
        'write.csv(d, "table.csv", row.names = FALSE)\n'
        'wb <- createWorkbook()\nsaveWorkbook(wb, file = "report-a.xlsx", overwrite = TRUE)\n'
        'writexl::write_xlsx(d, "sub/report-b.xlsx")\n'
        'd %>% readr::write_csv("piped.csv")\n'
        'cat("x", file = "log.txt", append = TRUE)\n'
        'saveRDS(d, file = "obj.rds")\n'
        'save(d, file = "all.RData")\nwriteLines("a", "note.txt")\n'
    )
    got = convertsrcwrites.certain_writes(src.encode(), "r")
    assert [w["name"] for w in got] == [
        "table.csv",
        "report-a.xlsx",
        "report-b.xlsx",
        "piped.csv",
        "log.txt",
        "obj.rds",
        "all.RData",
        "note.txt",
    ]
    assert got[0]["line"] == 2 and got[1]["line"] == 4


@pytest.mark.parametrize(
    "src",
    [
        'out <- "a.csv"\nwrite.csv(d, out)\n',  # variable
        'write.csv(d, paste0("a", i, ".csv"))\n',  # concatenation
        'write.csv(d, file.path(dir, "a.csv"))\n',  # path built by a call
        'if (flag) {\n  write.csv(d, "a.csv")\n}\n',  # branch
        'if (flag) write.csv(d, "a.csv")\n',  # one-line branch
        'if (flag)\n  write.csv(d, "a.csv")\n',  # unbraced branch on the previous line
        'for (i in 1:3) {\n  write.csv(d, "a.csv")\n}\n',  # loop body
        'save_it <- function(d) write.csv(d, "a.csv")\n',  # helper body
        'tryCatch(write.csv(d, "a.csv"), error = function(e) NULL)\n',  # wrapped call
        'ok && write.csv(d, "a.csv")\n',
        'write.csv(d, "/abs/outside.csv")\n',  # absolute: outside the redirect
        'write.csv(d, "../up/a.csv")\n',
        'write.csv(d, "~/a.csv")\n',
        '# write.csv(d, "commented.csv")\n',  # comment
        'x <- "write.csv(d, \\"quoted.csv\\")"\n',  # inside a string
        'ggsave("figure.png")\npng("a.png")\npdf("a.pdf")\nsvg("a.svg")\n',  # graphics are not outputs
        'd <- read.csv("a.csv")\nreadRDS("b.rds")\nreadLines("c.txt")\nload("d.RData")\n',  # reads
        'write.csv(d, stdout())\ncat("hello")\nsink()\n',  # no file path
    ],
)
def test_r_uncertain_or_non_output_calls_are_never_certain(src):
    assert names(src) == []


def test_matlab_literal_writes_are_certain_and_blocks_are_not():
    src = (
        "T = readtable('input.csv');\n"
        "writetable(T, 'out.xlsx');\n"
        "xlswrite('legacy.xls', M);\n"
        'writematrix(M, "m.csv");\n'
        "fid = fopen('log.txt', 'w');\nfprintf(fid, 'x');\nfclose(fid);\n"
        "fid2 = fopen('input.csv', 'r');\n"  # read mode
        "save('state.mat', 'T');\n"
        "if flag\n  writetable(T, 'branch.xlsx');\nend\n"
        "for i = 1:3, writetable(T, 'loop.xlsx'); end\n"
        "name = 'dyn.csv';\nwritetable(T, name);\n"
        "writetable(T, [prefix 'cat.csv']);\n"
        "% writetable(T, 'commented.xlsx');\n%{\nwritetable(T, 'block.xlsx');\n%}\n"
        "saveas(gcf, 'fig.png');\nexportgraphics(gcf, 'f.pdf');\nprint('-dpng', 'p.png');\n"
        "function helper(T)\n  writetable(T, 'inhelper.xlsx');\nend\n"
    )
    assert names(src, "matlab") == [
        "out.xlsx",
        "legacy.xls",
        "m.csv",
        "log.txt",
        "state.mat",
    ]


def test_other_languages_and_sensitive_names_are_unknown():
    assert names('write.csv(d, "a.csv")\n', "python") == []
    assert names('write.csv(d, "alice@example.com.csv")\n') == []


# ----------------------------------------------------------------- real validation (worker + redirect area)
R_SOURCE = (
    b"d <- data.frame(x = 1)\n"
    b'write.csv(d, "table.csv")\n'
    b'saveWorkbook(wb, "report-a.xlsx")\n'
    b'writexl::write_xlsx(d, "report-b.xlsx")\n'
    b"plot(1)\n"
)
PLOT = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('figure.png')\n"


def validate(world, candidate: str, *, source: bytes = R_SOURCE, mode=workdir.MODE_PROJECT):
    root, cand, runner = world
    workdir.set_mode(root, mode)
    cand.base_bytes = source
    cand.new_bytes = candidate.encode()
    cand.target.write_bytes(cand.new_bytes)
    return ai_convert.run_validation(cand, runner), cand


def test_plot_only_candidate_is_missing_outputs_not_ok(world):
    result, _ = validate(world, PLOT)
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result
    evidence = result["output_evidence"]
    assert evidence["missing_outputs"] == [
        {"name": "table.csv", "line": 2},
        {"name": "report-a.xlsx", "line": 3},
        {"name": "report-b.xlsx", "line": 4},
    ]
    assert evidence["output_check"] == "missing" and evidence["completeness"] == "unknown"
    assert result["stems"], "the capture is still reported; only the verdict changes"


def test_candidate_that_writes_every_certain_output_is_ok(world):
    code = (
        "open('table.csv', 'w').write('x')\n"
        "open('report-a.xlsx', 'wb').write(b'x')\n"
        "from pathlib import Path\nPath('report-b.xlsx').write_bytes(b'y')\n" + PLOT
    )
    result, cand = validate(world, code)
    assert result["status"] == "ok", result
    evidence = result["output_evidence"]
    assert evidence["missing_outputs"] == [] and evidence["output_check"] == "written"
    assert evidence["corrected"] is False
    assert not (cand.project_root / "sub" / "table.csv").exists(), "the project stays untouched"


def test_a_later_round_that_writes_the_missing_files_is_marked_corrected(world):
    first, cand = validate(world, PLOT)
    assert first["status"] == ai_convert.MISSING_OUTPUTS_STATUS
    code = (
        "open('table.csv', 'w').write('x')\nopen('report-a.xlsx', 'wb').write(b'x')\n"
        "open('report-b.xlsx', 'wb').write(b'y')\n" + PLOT
    )
    cand.new_bytes = code.encode()
    cand.target.write_bytes(cand.new_bytes)
    second = ai_convert.run_validation(cand, world[2])
    assert second["status"] == "ok", second
    assert second["output_evidence"]["corrected"] is True


def test_a_previous_rounds_file_does_not_vouch_for_the_next_run(world):
    root, cand, runner = world
    first, cand = validate(world, "open('table.csv', 'w').write('x')\n" + PLOT)
    assert first["status"] == ai_convert.MISSING_OUTPUTS_STATUS
    assert [m["name"] for m in first["output_evidence"]["missing_outputs"]] == [
        "report-a.xlsx",
        "report-b.xlsx",
    ]
    cand.new_bytes = PLOT.encode()
    cand.target.write_bytes(cand.new_bytes)
    again = ai_convert.run_validation(cand, runner)
    assert "table.csv" in [m["name"] for m in again["output_evidence"]["missing_outputs"]]


@pytest.mark.parametrize(
    "extra",
    [
        # written, then deleted
        "import os\nopen('table.csv', 'w').write('x')\nos.remove('table.csv')\n",
        # the write fails inside the candidate (a directory sits at the name)
        "import os\nos.makedirs('table.csv', exist_ok=True)\n"
        "try:\n    open('table.csv', 'w')\nexcept OSError:\n    pass\n",
    ],
)
def test_written_then_deleted_or_failed_writes_count_as_missing(world, extra):
    source = b'write.csv(d, "table.csv")\nplot(1)\n'
    result, _ = validate(world, extra + PLOT, source=source)
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result
    assert [m["name"] for m in result["output_evidence"]["missing_outputs"]] == ["table.csv"]


def test_a_write_rejected_by_the_isolation_is_never_a_pass(world, tmp_path):
    """The tripwire still fails the run itself (existing contract, unchanged): not ok, not adoptable as-is."""
    outside = tmp_path / "outside" / "table.csv"
    outside.parent.mkdir()
    code = f"try:\n    open({str(outside)!r}, 'w').write('x')\nexcept OSError:\n    pass\n" + PLOT
    result, _ = validate(world, code, source=b'write.csv(d, "table.csv")\nplot(1)\n')
    assert result["status"] == "error" and result["code"] == ai_convert.WRITE_OUTSIDE_ROOTS_CODE
    assert not outside.exists()


@pytest.mark.parametrize(
    "source",
    [
        b'out <- "table.csv"\nwrite.csv(d, out)\nplot(1)\n',
        b'write.csv(d, paste0("table", 1, ".csv"))\nplot(1)\n',
        b'if (flag) {\n  write.csv(d, "table.csv")\n}\nplot(1)\n',
        b'ggsave("table.png")\nd <- read.csv("table.csv")\nplot(1)\n',
    ],
)
def test_dynamic_conditional_graphic_and_read_calls_do_not_gate(world, source):
    result, _ = validate(world, PLOT, source=source)
    assert result["status"] == "ok", result
    assert result["output_evidence"]["missing_outputs"] is None
    assert result["output_evidence"]["output_check"] == "unknown"


def test_matlab_source_is_judged_the_same_way(world):
    source = b"writetable(T, 'out.xlsx');\nplot(1:3);\n"
    result, _ = validate(world, PLOT, source=source)
    # fixture candidate is an R candidate; flip the language the way a MATLAB conversion has it
    root, cand, runner = world
    cand.source_language = "matlab"
    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result
    assert result["output_evidence"]["missing_outputs"] == [{"name": "out.xlsx", "line": 1}]
    cand.new_bytes = ("open('out.xlsx', 'wb').write(b'x')\n" + PLOT).encode()
    cand.target.write_bytes(cand.new_bytes)
    assert ai_convert.run_validation(cand, runner)["status"] == "ok"


SANDBOX = workdir.MODE_SANDBOX
WRITES_ALL = (
    "open('table.csv', 'w').write('x')\nopen('report-a.xlsx', 'wb').write(b'x')\n"
    "open('report-b.xlsx', 'wb').write(b'y')\n"
)


def test_sandbox_cwd_plot_only_candidate_is_missing(world):
    result, _ = validate(world, PLOT, mode=SANDBOX)
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result
    assert [m["name"] for m in result["output_evidence"]["missing_outputs"]] == [
        "table.csv",
        "report-a.xlsx",
        "report-b.xlsx",
    ]


def test_sandbox_cwd_writes_count_as_written(world):
    result, cand = validate(world, WRITES_ALL + PLOT, mode=SANDBOX)
    assert result["status"] == "ok", result
    assert result["output_evidence"]["output_check"] == "written"
    assert not (cand.project_root / "sub" / "table.csv").exists()


def test_sandbox_cwd_write_then_delete_is_missing(world):
    code = "import os\nopen('table.csv', 'w').write('x')\nos.remove('table.csv')\n" + PLOT
    result, _ = validate(world, code, source=b'write.csv(d, "table.csv")\nplot(1)\n', mode=SANDBOX)
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result


def test_sandbox_report_carries_no_internal_files_and_only_names(world):
    root, cand, runner = world
    seen = []

    def spy(*args, **kwargs):
        out = runner(*args, **kwargs)
        seen.append(out)
        return out

    workdir.set_mode(root, SANDBOX)
    cand.base_bytes = R_SOURCE
    cand.new_bytes = ("open('kept.csv', 'w').write('x')\n" + PLOT).encode()
    cand.target.write_bytes(cand.new_bytes)
    ai_convert.run_validation(cand, spy)
    # the spawn bootstrap `_tavotto_shadow_spawn_main.py` lives in the sandbox too, and must not be reported
    assert seen[0]["sandbox_files"] == {"names": ["kept.csv"], "truncated": False}, seen[0]


def test_sandbox_listing_is_capped_and_hides_internal_files(tmp_path):
    from types import SimpleNamespace

    from tavotto.engine import worker

    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "m.pyc").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "kept.csv").write_text("x")
    (tmp_path / "mod.pyc").write_bytes(b"x")
    (tmp_path / "_tavotto_shadow_spawn_main.py").write_text("x")
    fake = SimpleNamespace(sandbox=tmp_path)
    assert worker.Worker._sandbox_files(fake) == {"names": ["sub/kept.csv"], "truncated": False}
    for i in range(worker._MAX_SANDBOX_FILES + 5):
        (tmp_path / f"f{i:04}.txt").write_text("x")
    listed = worker.Worker._sandbox_files(fake)
    assert listed["truncated"] and len(listed["names"]) == worker._MAX_SANDBOX_FILES


def test_a_truncated_sandbox_listing_is_not_judged(world):
    root, cand, runner = world

    def noisy(*args, **kwargs):
        out = runner(*args, **kwargs)
        return {**out, "sandbox_files": {"names": ["f1.txt"], "truncated": True}}

    workdir.set_mode(root, SANDBOX)
    cand.base_bytes = R_SOURCE
    cand.target.write_bytes(PLOT.encode())
    result = ai_convert.run_validation(cand, noisy)
    assert result["status"] == "ok" and result["output_evidence"]["missing_outputs"] is None


def test_an_old_worker_without_the_sandbox_report_is_unknown_in_sandbox_cwd(world):
    root, cand, runner = world

    def old(*args, **kwargs):
        out = dict(runner(*args, **kwargs))
        out.pop("sandbox_files", None)
        return out

    workdir.set_mode(root, SANDBOX)
    cand.base_bytes = R_SOURCE
    cand.target.write_bytes(PLOT.encode())
    result = ai_convert.run_validation(cand, old)
    assert result["status"] == "ok" and result["output_evidence"]["missing_outputs"] is None


def test_missing_worker_ledger_is_unknown_not_missing(world):
    _, cand, _ = world
    cand.base_bytes = R_SOURCE
    result = ai_convert.run_validation(cand, lambda *_: {"stems": ["figure"]})
    assert result["status"] == "ok" and result["output_evidence"]["missing_outputs"] is None


def test_agent_check_reports_missing_once_then_lets_the_user_decide(world):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.base_bytes = R_SOURCE
    cand.target.write_bytes(PLOT.encode())
    cand.extras["runner"] = runner
    ok, message = ai_convert.agent_check(cand)
    assert not ok and message.startswith("missing_outputs:") and "report-b.xlsx" in message
    ok, message = ai_convert.agent_check(cand)
    assert ok and "reported to the user" in message


# ----------------------------------------------------------------- the four terminal states, real pipeline
R_FILE = (
    'd <- data.frame(x = 1)\nwrite.csv(d, "table.csv")\n'
    'writexl::write_xlsx(d, "report-b.xlsx")\nplot(1)\n'
)
WRITES = "open('table.csv', 'w').write('x')\nopen('report-b.xlsx', 'wb').write(b'y')\n"


def _pipeline(
    project, monkeypatch, *, source: str, first: str, later: str, mode=workdir.MODE_PROJECT
):
    m, client, figs, state = project
    worker_tmp = figs.parent / "worker-tmp"
    worker_tmp.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(worker_tmp))
    monkeypatch.setenv("LOCALAPPDATA", str(figs.parent / "localappdata"))
    (figs / "fig.R").write_bytes(source.encode())
    workdir.set_mode(figs, mode)
    plot_only = GOOD
    body = {"first": first + plot_only, "later": later + plot_only}
    state["code"] = (
        "p = d / 'fig_matplotlib.py'; "
        f"p.write_text({body['later']!r} if p.exists() else {body['first']!r}, encoding='utf-8')"
    )
    try:
        sid = _start(
            client, script="fig.R", library=None, source_language="r", path="out.png"
        ).get_json()["session"]
        session = _wait_final(client, sid)
        status = client.get(f"/api/ai/convert/{sid}").get_json()
    finally:
        workdir.forget(figs)
    return client, sid, session, status, state


def test_state_written_first_time_needs_no_correction(project, monkeypatch):
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=R_FILE, first=WRITES, later=WRITES
    )
    assert session["task"]["outcome"] == "completed", session["task"]
    assert len(state["prompts"]) == 1
    evidence = status["comparison"]["output_evidence"]
    assert evidence["output_check"] == "written" and evidence["missing_outputs"] == []
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_state_missing_then_corrected_after_one_round(project, monkeypatch):
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=R_FILE, first="", later=WRITES
    )
    assert session["task"]["outcome"] == "completed", session["task"]
    assert len(state["prompts"]) == 2
    prompt = state["prompts"][1]
    assert "table.csv" in prompt and "report-b.xlsx" in prompt
    assert "data.frame" not in prompt, (
        "file names only: neither source text nor data is handed over"
    )
    evidence = status["comparison"]["output_evidence"]
    assert evidence["output_check"] == "written" and evidence["corrected"] is True
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_state_still_missing_after_the_round_is_adoptable_with_a_list(project, monkeypatch):
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=R_FILE, first="", later=""
    )
    assert len(state["prompts"]) == 2, "exactly one bounded correction"
    assert session["task"]["outcome"] == "completed", session["task"]
    assert status["state"] == "ready"
    evidence = status["comparison"]["output_evidence"]
    assert evidence["output_check"] == "missing" and evidence["corrected"] is False
    assert evidence["missing_outputs"] == [
        {"name": "table.csv", "line": 2},
        {"name": "report-b.xlsx", "line": 3},
    ]
    assert evidence["completeness"] == "unknown"
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_state_unknown_when_the_source_path_is_not_a_literal(project, monkeypatch):
    dynamic = 'out <- paste0("table", 1, ".csv")\nwrite.csv(d, out)\nplot(1)\n'
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=dynamic, first="", later=""
    )
    assert session["task"]["outcome"] == "completed", session["task"]
    assert len(state["prompts"]) == 1
    evidence = status["comparison"]["output_evidence"]
    assert evidence["output_check"] == "unknown" and evidence["missing_outputs"] is None
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_sandbox_default_still_corrects_once_and_ends_adoptable(project, monkeypatch):
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=R_FILE, first="", later="", mode=SANDBOX
    )
    assert len(state["prompts"]) == 2 and session["task"]["outcome"] == "completed"
    assert status["comparison"]["output_evidence"]["output_check"] == "missing"
    assert client.post(f"/api/ai/convert/{sid}/adopt").status_code == 200


def test_sandbox_default_corrected_to_written(project, monkeypatch):
    client, sid, session, status, state = _pipeline(
        project, monkeypatch, source=R_FILE, first="", later=WRITES, mode=SANDBOX
    )
    assert len(state["prompts"]) == 2
    evidence = status["comparison"]["output_evidence"]
    assert evidence["output_check"] == "written" and evidence["corrected"] is True
