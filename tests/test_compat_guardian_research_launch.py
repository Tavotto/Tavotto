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


def test_launch_cwd_the_product_cannot_express_is_a_runner_error(fake_project, tmp_path):
    script = NEEDS_RELATIVE_GLOB.format(pattern="*.txt")
    fake_project({"sub/plot.py": script, "data/a.txt": "1\n2\n"}, "sub/plot.py")
    result = _run(_case("sub/plot.py", {"cwd": "data", "argv": []}), tmp_path)
    assert result["classification"] == "runner_error"
    assert "neither the script directory nor the project root" in result["detail"]["runner_error"]


def test_without_registered_launch_the_ordinary_path_is_unchanged(fake_project, tmp_path):
    fake_project({"plot.py": NEEDS_ARGV.replace("sys.argv[1]", "'x'")}, "plot.py")
    case = _case("plot.py", {"cwd": ".", "argv": []})
    del case["reference_invocation"]
    assert _captured(_run(case, tmp_path))
