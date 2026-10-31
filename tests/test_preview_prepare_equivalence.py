"""The faster preparation must preserve the delivered SVG and its full manifest."""

import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")


def test_preview_preparation_matches_full_measurement(tmp_path):
    """Compare independent figures, including edits, hidden legends, 3D and frames."""
    script = r"""
import io
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['svg.hashsalt'] = 'preview-equivalence'
import matplotlib.pyplot as plt
from matplotlib.transforms import Bbox
import manifest, overrides, pathgeom

def figure(kind):
    layout = {'normal': None, 'tight': 'tight', 'constrained': 'constrained', 'frame': 'tight', '3d': None, 'images': 'tight'}[kind]
    fig = plt.figure(figsize=(5, 3.5), dpi=100, layout=layout)
    ax = fig.add_subplot(121 if kind == 'images' else 111, projection='3d' if kind == '3d' else None)
    if kind == '3d':
        ax.plot([0, 1, 2], [2, 1, 0], [0, 1, 3], label='line')
        ax.scatter([0, 1], [1, 2], [1, 3], label='points')
        ax.text2D(.3, .5, 'Annotation', transform=ax.transAxes)
    else:
        ax.plot([0, 1, 2], [0, 1, 0], label='line')
        ax.scatter([.1, .5], [.4, .7], label='points')
        ax.text(.3, .5, 'Annotation', transform=ax.transAxes)
    ax.set_xlabel('Horizontal label')
    ax.set_ylabel('Vertical label')
    ax.set_title('Title')
    ax.legend(loc='upper right')
    if kind == 'images':
        ax.set_aspect('equal')
        ax.set_title(r'Energy $E=mc^2$')
        other = fig.add_subplot(122)
        other.imshow([[0., 1.], [2., 3.]], origin='lower')
        other.set_title('Image')
    if kind == 'frame':
        pathgeom.set_frame(fig, Bbox.from_bounds(-.1, -.2, 5.4, 3.9))
    state = overrides.FigState(fig)
    manifest.instrument(state)
    return fig, state

def snapshots(kind, optimized):
    fig, state = figure(kind)
    results = []
    for patches in ([], [{'gid': 'axes_0.xlabel', 'prop': 'fontsize', 'value': 18}],
                    [{'gid': 'axes_0.legend', 'prop': 'visible', 'value': False}], []):
        warnings = overrides.apply(state, patches)
        assert not warnings, (kind, patches, warnings)
        if optimized:
            manifest.prepare_preview(state)
        else:
            manifest.build_manifest(state, 'f')
        output = io.BytesIO()
        with manifest.capture_preview_manifest(state, 'f') as measured:
            fig.savefig(output, format='svg', metadata={'Date': None}, **pathgeom.output_kwargs(fig))
            result = measured()
        results.append((warnings, result, output.getvalue()))
    plt.close(fig)
    return results

for kind in ('normal', 'tight', 'constrained', 'frame', '3d', 'images'):
    old, new = snapshots(kind, False), snapshots(kind, True)
    for step, (before, after) in enumerate(zip(old, new)):
        for index, subject in enumerate(('warnings', 'manifest', 'svg')):
            assert before[index] == after[index], (kind, step, subject)
"""
    engine = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
    subprocess.run(
        [WORKER_PY, "-c", script, str(engine)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
