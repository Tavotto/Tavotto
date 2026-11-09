"""A valid but incomplete Matplotlib font cache must not block the built-in default font."""

from __future__ import annotations

import os
import subprocess

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="Matplotlib worker unavailable")


@pytest.mark.parametrize("stale_default", [False, True], ids=["missing", "stale-path"])
def test_incomplete_font_cache_recovers_in_worker_without_modifying_the_cache(
    tmp_path, monkeypatch, stale_default
):
    """The files exist, but the cached default faces are absent or point nowhere."""
    project = tmp_path / "project"
    project.mkdir()
    cache = tmp_path / "font-cache"
    cache.mkdir()
    seed_cache = tmp_path / "seed-cache"
    seed_cache.mkdir()
    # Create the actual worker interpreter's own cache format, without importing
    # Matplotlib into the Flask/pytest interpreter or touching the user's cache.
    seed = """\
import copy, dataclasses, sys
from pathlib import Path
import matplotlib as mpl
from matplotlib import font_manager as fm
assert (Path(mpl.get_data_path()) / 'fonts' / 'ttf' / 'DejaVuSans-Bold.ttf').is_file()
manager = copy.copy(fm.fontManager)
default_face = copy.copy(next(e for e in manager.ttflist if e.name == manager.defaultFamily['ttf']))
manager.ttflist = [e for e in manager.ttflist if e.name.startswith('STIX')][:9]
assert len(manager.ttflist) == 9
assert not any(e.name == manager.defaultFamily['ttf'] for e in manager.ttflist)
if sys.argv[2] == 'True':
    manager.ttflist.append(dataclasses.replace(default_face, fname=str(Path(sys.argv[1]) / 'missing-default.ttf')))
manager.afmlist = []
fm.json_dump(manager, Path(sys.argv[1]) / ('fontlist-v' + str(fm.FontManager.__version__) + '.json'))
"""
    seed_env = {**os.environ, "MPLCONFIGDIR": str(seed_cache), "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [WORKER_PY, "-c", seed, str(cache), str(stale_default)],
        env=seed_env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    cache_files = {p.name: p.read_bytes() for p in cache.iterdir()}
    healthy_cache_files = {p.name: p.read_bytes() for p in seed_cache.iterdir()}
    assert len(cache_files) == 1
    (project / "plot.py").write_text(
        "from pathlib import Path\n"
        "from matplotlib import font_manager as fm\n"
        "import matplotlib.pyplot as plt\n"
        "assert any(e.name.startswith('STIX') for e in fm.fontManager.ttflist)\n"
        "p = fm.findfont(fm.FontProperties(family=['DejaVu Sans'], weight='bold'), "
        "fallback_to_default=False, rebuild_if_missing=False)\n"
        "assert Path(str(p)).name == 'DejaVuSans-Bold.ttf', p\n"
        "plt.plot([1, 2]); plt.title('test', fontweight='bold')\n"
        "plt.savefig('figure.png')\n",
        encoding="utf-8",
    )

    def render_with(mpl_cache):
        monkeypatch.setenv("MPLCONFIGDIR", str(mpl_cache))
        worker = pool.one_shot("plot.py", str(project), "__main__")
        try:
            result = worker.ensure_built()
            assert "figure" in result["stems"]
            return worker.render_png("figure", 640).read_bytes()
        finally:
            pool.discard(worker)

    healthy = render_with(seed_cache)
    repaired = render_with(cache)
    assert repaired == healthy
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == cache_files
    assert {p.name: p.read_bytes() for p in seed_cache.iterdir()} == healthy_cache_files


def test_healthy_registry_is_not_rescanned_and_failed_recovery_is_not_claimed(tmp_path):
    """A healthy cache is left alone; a rescan without a usable default changes nothing."""
    cache = tmp_path / "font-cache"
    cache.mkdir()
    check = """\
from types import SimpleNamespace
from matplotlib import font_manager as fm
from tavotto.engine import worker

manager = fm.fontManager
calls = []
def empty_rescan():
    calls.append(True)
    return SimpleNamespace(ttflist=[], afmlist=[])
fm.FontManager = empty_rescan
assert worker._repair_incomplete_font_registry() is False
assert calls == []

default = manager.defaultFamily['ttf'].casefold()
manager.ttflist[:] = [e for e in manager.ttflist if e.name.casefold() != default]
ttf, afm = list(manager.ttflist), list(manager.afmlist)
assert worker._repair_incomplete_font_registry() is False
assert calls == [True]
assert manager.ttflist == ttf and manager.afmlist == afm
assert fm.fontManager is manager
"""
    env = {**os.environ, "MPLCONFIGDIR": str(cache), "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [WORKER_PY, "-c", check],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
