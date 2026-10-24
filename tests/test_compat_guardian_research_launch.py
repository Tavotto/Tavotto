"""The product leg runs a registered launch (argv + working directory) the way a user would.

Real worker, fake project: a script that needs argv, and scripts that glob relative to the
launch directory (the Leaf `plot.py hppc` and Qudit relative-glob shapes). Nothing external runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_matrix as CM  # noqa: E402

NEEDS_ARGV = """\
import sys
import matplotlib.pyplot as plt

dataset = sys.argv[1]
fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label=dataset)
fig.savefig("out.png")
"""

NEEDS_RELATIVE_GLOB = """\
import glob
import matplotlib.pyplot as plt
import numpy as np

curves = [np.loadtxt(name) for name in glob.glob("{pattern}")]
x = np.concatenate(curves)  # empty glob: "need at least one array to concatenate"
fig, ax = plt.subplots()
ax.plot(x)
fig.savefig("out.png")
"""


def _case(script_dest: str, launch: dict) -> dict:
    return {
        "id": "research_fake",
        "category": "research",
        "tier": "expected",
        "expected": {},
        "stem": "out",
        "expected_figures": 1,
        "script": script_dest,
        "discovery": "discoverable",
        "reference_invocation": launch,
    }


@pytest.fixture
def fake_project(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)

    def build(files: dict[str, str], script_dest: str):
        def materialize(_group, workdir):
            project = workdir / "project"
            for rel, text in files.items():
                (project / rel).parent.mkdir(parents=True, exist_ok=True)
                (project / rel).write_text(text, encoding="utf-8")
            return project, script_dest, "__main__"

        monkeypatch.setattr(CM, "materialize", materialize)

    return build


def _run(case: dict, tmp_path: Path) -> dict:
    return CM.run_group(
        [case],
        python=sys.executable,
        root=tmp_path,
        out_dir=tmp_path,
        want_fidelity=False,
        want_browser=False,
    )[case["id"]]


def _captured(result: dict) -> bool:
    return bool(result["stages"].get("execute") and result["stages"].get("capture"))


def test_registered_argv_reaches_the_product_leg(fake_project, tmp_path):
    fake_project({"plot.py": NEEDS_ARGV}, "plot.py")
    result = _run(_case("plot.py", {"cwd": ".", "argv": ["hppc"]}), tmp_path)
    assert _captured(result), result["detail"]
    assert result["classification"] != "runner_error"


def test_missing_argv_is_a_product_leg_failure(fake_project, tmp_path):
    """Counter-check: the same project without the registered argv fails to execute."""
    fake_project({"plot.py": NEEDS_ARGV}, "plot.py")
    result = _run(_case("plot.py", {"cwd": ".", "argv": []}), tmp_path)
    assert not _captured(result)


def test_project_root_cwd_glob(fake_project, tmp_path):
    script = NEEDS_RELATIVE_GLOB.format(pattern="data/*.txt")
    fake_project({"src/plot.py": script, "data/a.txt": "1\n2\n3\n"}, "src/plot.py")
    assert _captured(_run(_case("src/plot.py", {"cwd": ".", "argv": []}), tmp_path))


def test_script_directory_cwd_glob(fake_project, tmp_path):
    script = NEEDS_RELATIVE_GLOB.format(pattern="*.txt")
    fake_project({"sub/plot.py": script, "sub/a.txt": "1\n2\n3\n"}, "sub/plot.py")
    assert _captured(_run(_case("sub/plot.py", {"cwd": "sub", "argv": []}), tmp_path))


def test_launch_cwd_the_product_cannot_express_runs_in_the_product_default_mode(
    fake_project, tmp_path
):
    """Cantera shape: the registered cwd `data` is neither mode; the data is found without the cwd."""
    script = NEEDS_RELATIVE_GLOB.format(pattern="*.txt")
    fake_project({"sub/plot.py": script, "sub/a.txt": "1\n2\n", "data/b.txt": "1\n"}, "sub/plot.py")
    result = _run(_case("sub/plot.py", {"cwd": "data", "argv": []}), tmp_path)
    assert _captured(result), result["detail"]
    assert result["detail"]["launch_context"] == {
        "registered_cwd": "data",
        "product_mode": "project",
        "substituted": True,
    }
    assert "code" not in result["detail"]["execute"]


def test_expressible_cwd_is_recorded_without_substitution(fake_project, tmp_path):
    script = NEEDS_RELATIVE_GLOB.format(pattern="data/*.txt")
    fake_project({"src/plot.py": script, "data/a.txt": "1\n2\n"}, "src/plot.py")
    result = _run(_case("src/plot.py", {"cwd": ".", "argv": []}), tmp_path)
    assert result["detail"]["launch_context"] == {
        "registered_cwd": ".",
        "product_mode": "project_root",
        "substituted": False,
    }


def test_substituted_mode_that_also_fails_reports_launch_context_unsupported(
    fake_project, tmp_path
):
    """The script needs the registered `data` directory; the substitute cannot see its files."""
    script = NEEDS_RELATIVE_GLOB.format(pattern="*.txt")
    fake_project({"sub/plot.py": script, "data/a.txt": "1\n2\n"}, "sub/plot.py")
    result = _run(_case("sub/plot.py", {"cwd": "data", "argv": []}), tmp_path)
    assert result["classification"] == "product_bug"
    assert result["stages"] == {"discover": True, "execute": False}
    assert result["detail"]["execute"]["code"] == "launch_context_unsupported"
    assert "need at least one array" in result["detail"]["execute"]["error"]
    assert result["detail"]["launch_context"]["failed_stage"] == "execute"
    assert result["detail"]["launch_context"]["substituted"] is True


def test_expressible_cwd_failure_is_not_blamed_on_the_launch_context(fake_project, tmp_path):
    script = NEEDS_RELATIVE_GLOB.format(pattern="*.txt")
    fake_project({"sub/plot.py": script}, "sub/plot.py")
    result = _run(_case("sub/plot.py", {"cwd": "sub", "argv": []}), tmp_path)
    assert result["stages"] == {"discover": True, "execute": False}
    assert "code" not in result["detail"]["execute"]
    assert "failed_stage" not in result["detail"]["launch_context"]


def test_that_failure_is_attributed_to_the_launch_directory_not_the_runner():
    import compat_guardian_taxonomy as TX

    detail = {"discover": {"code": "launch_context_unsupported"}}
    row = {
        "status": "failed",
        "raw": {"detail": detail},
        "native": {"status": "passed", "result": {}},
        "stages": {"discover": {"status": "failed"}},
    }
    got = TX.attribute(row)
    assert (got["category"], got["source_code"]) == (
        "cwd_or_data_path",
        "launch_context_unsupported",
    )
    row["raw"]["detail"] = {"discover": {}}
    assert TX.attribute(row)["source_code"] == "stage:discover"


def test_without_registered_launch_the_ordinary_path_is_unchanged(fake_project, tmp_path):
    fake_project({"plot.py": NEEDS_ARGV.replace("sys.argv[1]", "'x'")}, "plot.py")
    case = _case("plot.py", {"cwd": ".", "argv": []})
    del case["reference_invocation"]
    assert _captured(_run(case, tmp_path))


def _hosted_row():
    """Trimmed real row from hosted run 39928055867 (Cantera: launch cwd `data` not expressible)."""
    import json

    return json.loads(
        (Path(__file__).parent / "compat" / "launch-context-unsupported-row.json").read_text(
            encoding="utf-8"
        )
    )


def test_refused_launch_projects_to_a_product_failure_with_its_own_category():
    import compat_guardian as guardian

    row = _hosted_row()
    assert row["observed_before_fix"]["status"] == "unknown"  # what the hosted run produced
    row["comparable"] = False  # the product leg never launched, so no launch pair exists
    guardian.finalize_row(row)
    assert row["status"] == "failed"
    assert (row["failure_category"]["category"], row["failure_category"]["source_code"]) == (
        "cwd_or_data_path",
        "launch_context_unsupported",
    )
    assert row["stages"]["discover"]["status"] == "failed"
    assert {s["status"] for n, s in row["stages"].items() if n != "discover"} == {"not_run"}
    guardian.check_row_proof(row, mismatch=False)  # accepted by the validator, not only projected


def test_refusal_needs_native_proof_and_a_never_launched_product_leg():
    import copy

    import compat_guardian as guardian

    base = _hosted_row()
    for mutate in (
        lambda r: r["executions"].update(native=0),
        lambda r: r["executions"].update(tavotto=1),
        lambda r: r["raw"]["launch_environments"].update(
            tavotto=r["raw"]["launch_environments"]["native"]
        ),
        lambda r: r["raw"]["detail"]["discover"].update(code="other"),
        lambda r: r["stages"]["discover"].update(status="passed"),
    ):
        row = copy.deepcopy(base)
        mutate(row)
        assert not guardian.refused_before_launch(row)
