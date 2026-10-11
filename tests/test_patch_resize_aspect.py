"""A size override must survive the aspect adjustment inside Matplotlib's actual draw.

Tests run in the selected Matplotlib worker, not in the Flask interpreter.  A clean
worker/cold replay and actual PNG/PDF export must agree with the edited geometry.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有可用的 matplotlib 解释器")
ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"

PROBE = r"""
import io
import json
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.transforms import Bbox
import overrides
import manifest

kind, adjustable, change = json.loads(sys.argv[2])


def scene():
    fig = plt.figure(figsize=(5, 4), dpi=100)
    ax = fig.add_axes([.1, .1, .8, .8])
    ax.set(xlim=(0, 10), ylim=(0, 10), aspect='equal', adjustable=adjustable)
    if change == 'locator':
        ax.set_axes_locator(lambda a, renderer: Bbox.from_bounds(.12, .15, .72, .65))
    if change == 'shared':
        other = fig.add_axes([.1, .84, .6, .1], sharex=ax)
        other.set_axis_off()
    patch = (FancyBboxPatch((2, 3), 3.5, 2.2, boxstyle='round,pad=.2', transform=ax.transData)
             if kind == 'round' else Rectangle((2, 3), 3.5, 2.2, transform=ax.transData))
    ax.add_patch(patch)
    patch.set_clip_on(False)
    ax.set_axis_off()
    state = overrides.FigState(fig)
    manifest.instrument(state)
    gid = next(e['gid'] for e in state.elements if e['artist'] is patch)
    fig.canvas.draw()
    return (fig, ax, patch, state, gid)


def measure(s):
    fig, _, patch, _, _ = s
    bb = patch.get_window_extent()
    return [bb.x0 / fig.bbox.width, bb.y0 / fig.bbox.height,
            bb.width / fig.bbox.width, bb.height / fig.bbox.height]


def applied(s, changes):
    warnings = overrides.apply(s[3], changes)
    assert not warnings, warnings
    s[0].canvas.draw()
    return measure(s)


def size_of(s):
    # The contract is the *drawn* outer path, not the unverified size_frac input value.
    fig, _, patch, state, gid = s
    man = manifest.build_manifest(state, 'Aspect')
    item = next(e for e in man['elements'] if e['gid'] == gid)
    assert item.get('shape_bounds') is not None, item
    return item['shape_bounds'][2:]


hot, cold, reference = scene(), scene(), scene()
size = {'gid': hot[4], 'prop': 'size_frac', 'value': [.5, .3]}
axis = {'gid': 'axes_0', 'prop': 'position' if change == 'position' else
        'ylim' if change == 'ylim' else 'xlim',
        'value': [.2, .15, .6, .7] if change == 'position' else [0, 20]}
base = measure(hot)
assert 'draw' not in vars(hot[2]), 'fixture must begin with an unmodified artist'
applied(hot, [size])
assert 'draw' in vars(hot[2]), 'a resized artist must re-place after axes aspect settles'
reference_box = applied(reference, [axis])
hot_box = applied(hot, [size, axis])
cold_box = applied(cold, [size, axis])
assert abs(hot_box[0] - reference_box[0]) < 1e-9
assert abs(hot_box[1] - reference_box[1]) < 1e-9
assert max(abs(a - b) for a, b in zip(hot_box, cold_box)) < 1e-9

checks = []
def check(stage):
    hot[0].canvas.draw()
    box = measure(hot)
    dimensions = size_of(hot)
    error = max(abs(box[i+2] - size['value'][i]) for i in (0,1))
    assert error < 3e-7, (stage, kind, adjustable, change, box, size, error)
    assert max(abs(dimensions[i] - size['value'][i]) for i in (0,1)) < 3e-7, (stage, dimensions)
    checks.append((stage, error))

check('after axis edits')
for i in range(3):
    check(f'redraw {i}')
for dpi in (72, 180, 300):
    hot[0].set_dpi(dpi)
    check(f'dpi {dpi}')
for fmt in ('png', 'pdf'):
    blob = io.BytesIO()
    hot[0].savefig(blob, format=fmt, dpi=180)
    data = blob.getvalue()
    assert len(data) > 1000
    assert data.startswith(b'%PDF-') if fmt == 'pdf' else data.startswith(b'\x89PNG\r\n\x1a\n')
    check(f'export {fmt}')

restored = applied(hot, [axis])
assert max(abs(a-b) for a,b in zip(restored, reference_box)) < 1e-9
assert 'draw' not in vars(hot[2]), 'restore must remove only our instance hook'
assert '_mm_size_draw_hook' not in vars(hot[2]), 'restore must not retain closure references'
again = applied(hot, [size, axis])
assert max(abs(again[i+2] - size['value'][i]) for i in (0,1)) < 3e-7
assert 'draw' in vars(hot[2])
print(json.dumps({'kind':kind,'adjustable':adjustable,'change':change,
                  'base':base,'reference':reference_box,'hot':hot_box,
                  'cold':cold_box,'checks':checks}))
plt.close('all')
"""


@pytest.mark.parametrize("kind", ["rectangle", "round"])
@pytest.mark.parametrize("adjustable", ["box", "datalim"])
@pytest.mark.parametrize("change", ["xlim", "ylim", "position", "locator", "shared"])
def test_fixed_aspect_artist_survives_draw_replay_and_export(kind, adjustable, change):
    proc = subprocess.run(  # noqa: S603 — 固定的测试探针在选中的 worker 解释器运行
        [WORKER_PY, "-c", PROBE, str(ENGINE_DIR), json.dumps([kind, adjustable, change])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(got["checks"]) == 9
    assert len(got["hot"]) == len(got["cold"]) == 4
