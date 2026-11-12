"""Conversion must expose output evidence without claiming it is complete."""

# ruff: noqa: F811 - imported pytest fixture
from __future__ import annotations

from tavotto.engine import ai_convert, convertcontext, workdir
from test_convert_shadow_validation import world  # noqa: F401


def test_real_validation_discloses_source_references_and_redirected_outputs(world):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.base_bytes = (
        b'd <- read.csv("input.csv")\n'
        b'write_tab(d, "report-a.xlsx")\nwrite_tab(d, "report-b.xlsx")\n'
        b"plot(1)\n"
    )
    cand.new_bytes = (
        b"import matplotlib.pyplot as plt\n"
        b"open('report-a.xlsx', 'wb').write(b'synthetic')\n"
        b"plt.plot([1, 2])\nplt.savefig('figure.png')\n"
    )
    cand.target.write_bytes(cand.new_bytes)

    result = ai_convert.run_validation(cand, runner)
    assert result["status"] == "ok", result
    evidence = result["output_evidence"]
    assert evidence["source_references"] == ["input.csv", "report-a.xlsx", "report-b.xlsx"]
    assert evidence["write_attempts"] == ["report-a.xlsx"]
    assert evidence["completeness"] == "unknown"
    assert not (root / "sub" / "report-a.xlsx").exists()
    cand.comparison = ai_convert.comparison_of(cand, result)
    assert cand.comparison["output_evidence"] == evidence
    ai_convert.stage(cand)
    assert ai_convert.status(cand.sid, str(root))["comparison"]["output_evidence"] == evidence

    cand.extras["runner"] = runner
    passed, message = ai_convert.agent_check(cand)
    assert passed
    assert "report-b.xlsx" in message and "report-a.xlsx" in message
    assert "not proof" in message.lower()


def test_deleted_output_is_an_attempt_not_proof_of_a_retained_file(world):
    root, cand, runner = world
    workdir.set_mode(root, workdir.MODE_PROJECT)
    cand.base_bytes = b'write.csv(d, "temporary.csv")\nplot(1)\n'
    cand.new_bytes = (
        b"import os\nimport matplotlib.pyplot as plt\n"
        b"open('temporary.csv', 'w').write('x')\n"
        b"os.remove('temporary.csv')\n"
        b"plt.plot([1, 2])\nplt.savefig('figure.png')\n"
    )
    cand.target.write_bytes(cand.new_bytes)
    result = ai_convert.run_validation(cand, runner)
    # #1809: an attempted-then-deleted certain output is "missing", never a pass
    assert result["status"] == ai_convert.MISSING_OUTPUTS_STATUS, result
    assert "temporary.csv" in result["output_evidence"]["write_attempts"]
    assert result["output_evidence"]["completeness"] == "unknown"
    assert not (cand.dir / ai_convert.REDIRECT_DIRNAME / "sub" / "temporary.csv").exists()


def test_missing_worker_ledger_stays_unknown(world):
    _, cand, _ = world
    cand.base_bytes = b'write.csv(d, "report.csv")\nplot(1)\n'
    result = ai_convert.run_validation(cand, lambda *_: {"stems": ["figure"]})
    assert result["status"] == "ok"
    assert result["output_evidence"]["source_references"] == ["report.csv"]
    assert result["output_evidence"]["write_attempts"] is None
    assert result["output_evidence"]["completeness"] == "unknown"


def test_source_hints_are_bounded_and_do_not_disclose_directories():
    source = "\n".join(f'write.csv(d, "private/dir/report-{i:02}.csv")' for i in range(25))
    names, truncated = convertcontext.file_reference_hints(source.encode(), ".R")
    assert truncated
    assert len(names) == convertcontext.MAX_OUTPUT_EVIDENCE
    assert all("/" not in name and "\\" not in name for name in names)


def test_sensitive_file_names_are_not_disclosed():
    assert convertcontext.evidence_basename("private/alice@example.com") is None
    assert convertcontext.evidence_basename("private/report.csv") == "report.csv"
