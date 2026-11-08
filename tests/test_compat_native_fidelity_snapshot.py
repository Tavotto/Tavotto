"""The native fidelity witness must not add a layout pass to a saved figure."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool
from tests.test_initial_tight_layout import CREATE

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")
ROOT = Path(__file__).resolve().parents[1]


def _native_snapshot(tmp_path, after_save=""):
    source = CREATE + "fig.savefig('initial.png', dpi=128)\n" + after_save
    script = tmp_path / "figure.py"
    script.write_text(source, encoding="utf-8")
    request = {
        "script": str(script),
        "project": str(tmp_path),
        "engine_dir": str(ROOT / "src" / "tavotto" / "engine"),
        "entry": "__main__",
        "out_dir": str(tmp_path / "native"),
        "width": 640,
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    run = subprocess.run(
        [
            WORKER_PY,
            str(ROOT / "scripts/ci/compat_driver.py"),
            "--mode",
            "native",
            "--request",
            str(request_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["ok"], result
    return script, Path(result["shots"]["initial"])


def test_native_snapshot_matches_the_original_single_save(tmp_path):
    _, snapshot = _native_snapshot(tmp_path)
    assert snapshot.read_bytes() == (tmp_path / "initial.png").read_bytes()


def test_native_witness_matches_worker_but_detects_a_real_edit(tmp_path):
    _, snapshot = _native_snapshot(tmp_path)
    worker = pool.one_shot("figure.py", str(tmp_path), "__main__")
    try:
        worker.ensure_built()
        assert worker.render_png("initial", 640).read_bytes() == snapshot.read_bytes()
        applied = worker.override(
            "initial", [{"gid": "axes_0.lines_0", "prop": "color", "value": "#000000"}]
        )
        assert not applied["warnings"]
        assert worker.render_png("initial", 640).read_bytes() != snapshot.read_bytes()
    finally:
        pool.discard(worker)


def test_native_snapshot_keeps_changes_made_after_save(tmp_path):
    script, snapshot = _native_snapshot(tmp_path, "ax.set_title('Changed after save')\n")
    subprocess.run(
        [
            WORKER_PY,
            "-c",
            "import runpy; ns=runpy.run_path('figure.py'); ns['fig'].savefig('second.png', dpi=128)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    assert script.is_file()
    assert snapshot.read_bytes() == (tmp_path / "second.png").read_bytes()
    assert snapshot.read_bytes() != (tmp_path / "initial.png").read_bytes()


@pytest.mark.parametrize(
    "after_save",
    [
        "fig.savefig('another.png', dpi=128)\n",
        "fig.stale_callback = lambda artist, value: None\n",
    ],
    ids=["multiple-saves", "observer-replaced"],
)
def test_native_snapshot_does_not_freeze_when_save_history_is_uncertain(tmp_path, after_save):
    _, snapshot = _native_snapshot(tmp_path, after_save)
    subprocess.run(
        [
            WORKER_PY,
            "-c",
            "import runpy; ns=runpy.run_path('figure.py'); ns['fig'].savefig('second.png', dpi=128)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    assert snapshot.read_bytes() == (tmp_path / "second.png").read_bytes()
