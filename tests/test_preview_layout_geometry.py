"""Preview geometry must describe the SVG the user actually sees.

Oracle: initial/final SVG coordinates, not the post-patch manifest compared with
its input. Read the cold-build artifact directly: an extra empty render hides the
first tight-layout tick-locator change that caused half-distance label drags.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")
TOL_PT = 0.02
SCRIPT = """\
import matplotlib.pyplot as plt


def main():
    for stem, layout in [('N', None), ('T', 'tight'), ('C', 'constrained')]:
        fig, ax = plt.subplots(figsize=(5, 3.2), dpi=100, layout=layout)
        bars = ax.bar([0, 1, 2], [2, 3, 4], label='Values')
        ax.bar_label(bars, padding=3)
        fig.savefig(stem + '.pdf')
        plt.close(fig)
    for stem, layout in [('DN', None), ('DT', 'tight'), ('DC', 'constrained')]:
        fig, ax = plt.subplots(figsize=(5, 3.2), dpi=100, layout=layout)
        bars = ax.bar([0, 1, 2], [2, 3, 4], label='Values')
        ax.bar_label(bars, padding=3)
        ax.text(.3, 2.2, 'data')
        ax.text(.25, .45, 'axes', transform=ax.transAxes)
        ax.annotate('ann data', xy=(1, 2), xytext=(1, 2.5), textcoords='data')
        ax.annotate('ann axes', xy=(1, 2), xytext=(.7, .5), textcoords='axes fraction')
        ax.set_title('Three bars')
        ax.set_xlabel('Horizontal axis')
        ax.set_ylabel('Vertical axis')
        ax.legend(loc='upper left')
        fig.suptitle('Figure title')
        fig.savefig(stem + '.pdf')
        plt.close(fig)
"""


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    path = tmp_path_factory.mktemp("preview-layout-geometry")
    (path / "fig_geometry.py").write_text(SCRIPT, encoding="utf-8")
    return path


@pytest.fixture
def worker(library):
    w = pool.one_shot("fig_geometry.py", str(library), "main")
    w.ensure_built()
    try:
        yield w
    finally:
        pool.discard(w)


def _cold(w, stem):
    # Deliberately no w.override(stem, []): that would warm up layout.
    manifest = json.loads((Path(w.out_dir) / f"{stem}.json").read_text(encoding="utf-8"))
    return {e["gid"]: e for e in manifest["elements"]}, w.svg_path(stem).read_text(encoding="utf-8")


def _svg_position(svg, gid):
    root = ET.fromstring(svg)
    group = next(el for el in root.iter() if el.get("id") == gid)
    if gid.endswith(".legend"):
        path = next(el for el in group.iter() if el.tag.endswith("}path"))
        values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", path.get("d"))]
        return min(values[0::2]), min(values[1::2])
    for el in group.iter():
        match = re.match(r"translate\(([-\d.e]+)[ ,]+([-\d.e]+)\)", el.get("transform", ""))
        if match:
            return float(match[1]), float(match[2])
    pytest.fail(f"No SVG text origin for {gid}")


@pytest.mark.parametrize("stem", ["N", "T", "C"])
@pytest.mark.parametrize("dx_pt", [1.4173228346456694, 10.8])
def test_first_bar_label_move_matches_the_visible_svg(worker, stem, dx_pt):
    """Small nudge and drag: the cold SVG moves by the requested distance."""
    base, svg = _cold(worker, stem)
    gid = "axes_0.texts_1"
    before = _svg_position(svg, gid)
    anchor = base[gid]["anchor"]
    response = worker.override(
        stem, [{"gid": gid, "prop": "pos_frac", "value": [anchor[0] + dx_pt / 360, anchor[1]]}]
    )
    assert not response["warnings"]
    after = _svg_position(worker.svg_path(stem).read_text(encoding="utf-8"), gid)
    assert after == pytest.approx((before[0] + dx_pt, before[1]), abs=TOL_PT)


@pytest.mark.parametrize("stem", ["DN", "DT", "DC"])
@pytest.mark.parametrize(
    "gid",
    [
        "axes_0.texts_3",
        "axes_0.texts_4",
        "axes_0.texts_5",
        "axes_0.texts_6",
        "axes_0.title",
        "axes_0.xlabel",
        "axes_0.ylabel",
        "axes_0.legend",
        "fig.texts_0",
    ],
)
def test_first_other_element_move_matches_the_visible_svg(worker, stem, gid):
    base, svg = _cold(worker, stem)
    before = _svg_position(svg, gid)
    element = base[gid]
    target = [element["anchor"][0] + 0.03, element["anchor"][1] + 0.04]
    response = worker.override(stem, [{"gid": gid, "prop": element["drag_prop"], "value": target}])
    assert not response["warnings"]
    after = _svg_position(worker.svg_path(stem).read_text(encoding="utf-8"), gid)
    assert after == pytest.approx((before[0] + 10.8, before[1] + 9.216), abs=TOL_PT)


def test_resize_manifest_legend_matches_actual_svg(worker):
    result = worker.override("DT", [{"gid": "figure", "prop": "size_mm", "value": [152.4, 101.6]}])
    assert not result["warnings"]
    el = next(e for e in result["manifest"]["elements"] if e["gid"] == "axes_0.legend")
    actual = _svg_position(worker.svg_path("DT").read_text(encoding="utf-8"), "axes_0.legend")
    expected = el["bbox"][0] * 432, el["bbox"][1] * 288
    assert expected == pytest.approx(actual, abs=TOL_PT)


@pytest.mark.parametrize("fail_measurement", [False, True])
def test_capture_restores_transient_state_and_disconnects_on_error(tmp_path, fail_measurement):
    """The snapshot must not leave DPI/frame changes, disable layout, or leak callbacks."""
    import subprocess

    script = r"""
import io
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.transforms import Bbox
import manifest
import overrides
import pathgeom
fig, ax = plt.subplots(figsize=(5, 3.2), dpi=144, layout='tight')
ax.bar_label(ax.bar([0, 1, 2], [2, 3, 4]), padding=3)
pathgeom.set_frame(fig, Bbox.from_bounds(-.1, -.2, 5.4, 3.5))
state = overrides.FigState(fig)
manifest.instrument(state)
engine = fig.get_layout_engine()
callbacks = set(fig.canvas.callbacks.callbacks.get('draw_event', {}))
native_measure = manifest._measure_manifest

def snapshot():
    return (float(fig.dpi), tuple(fig.get_size_inches()), tuple(fig.bbox.bounds),
            tuple(fig.bbox_inches.bounds), tuple(ax.get_position().bounds),
            pathgeom.frame_outsets(fig), tuple(ax.texts[0].get_position()))

def fail(*args, **kwargs):
    raise ValueError('measurement failed intentionally')

with manifest.capture_preview_manifest(state, 'f') as measured:
    fig.savefig(io.BytesIO(), format='svg', **pathgeom.output_kwargs(fig))
    before = snapshot()
    if sys.argv[2] == 'True':
        manifest._measure_manifest = fail
    try:
        result = measured()
        assert all(
            abs(a-b) < 1e-8 for a,b in zip(result['size_mm'], [137.16, 88.9]))
    except ValueError as exc:
        assert str(exc) == 'measurement failed intentionally'
        assert sys.argv[2] == 'True'
    finally:
        manifest._measure_manifest = native_measure
    assert snapshot() == before, (snapshot(), before)
assert fig.get_layout_engine() is engine
assert set(fig.canvas.callbacks.callbacks.get('draw_event', {})) == callbacks
# Subsequent ordinary drawing/export still executes the same active layout engine.
fig.savefig(io.BytesIO(), format='pdf')
assert fig.dpi == 144 and fig.get_layout_engine() is engine
print(json.dumps({'restored': True}))
"""
    engine = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
    result = subprocess.run(
        [WORKER_PY, "-c", script, str(engine), str(fail_measurement)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"restored": True}


def test_hybrid_retry_manifest_uses_the_final_svg(tmp_path):
    """The byte gate really saves twice; the delivered geometry matches save #2."""
    import subprocess

    script = r"""
import io
import json
import re
import sys
sys.path.insert(0, sys.argv[1])
import preview_hybrid_probe as probe
import previewbudget
fig = probe._fig_small_meshes()
with probe.tempfile.TemporaryDirectory() as out:
    session = probe._session(out, fig)
    session.instrument_all()
    calls = []
    save = fig.savefig
    def record(*args, **kwargs):
        calls.append(1)
        return save(*args, **kwargs)
    fig.savefig = record
    previewbudget.EDITOR_SVG_SOFT_LIMIT_BYTES = 200_000
    preview = {}
    result = session.do_render(probe.fx.STEM, [], inline_svg=True, preview=preview)
    assert len(calls) == 2, calls
    assert preview['mode'] == 'hybrid', preview
    svg = result['svg']
    group = re.search(r'<g id="axes_3\.legend">(.*?)</g>', svg, re.S).group(1)
    path = re.search(r'<path d="([^"]+)"', group).group(1)
    values = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?(?:e-?\d+)?', path)]
    actual = [min(values[::2]), min(values[1::2])]
    box = next(e['bbox'] for e in result['manifest']['elements'] if e['gid'] == 'axes_3.legend')
    viewbox = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    expected = [box[0] * float(viewbox[1]), box[1] * float(viewbox[2])]
    assert max(abs(x-y) for x,y in zip(actual, expected)) < .02, (actual, expected)
    assert all(not a.get_rasterized() for a in probe._meshes(fig))
    print(json.dumps({'saves': len(calls), 'error_pt': [x-y for x,y in zip(actual, expected)]}))
"""
    support = Path(__file__).resolve().parent / "support"
    result = subprocess.run(
        [WORKER_PY, "-c", script, str(support)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1])["saves"] == 2


@pytest.mark.parametrize("dpi", [72, 100, 144, 200])
def test_svg_measurement_preserves_hit_padding_and_axes_precision(tmp_path, dpi):
    """SVG points must not enlarge pixel hit targets or quantize an axes drag."""
    import subprocess

    script = r"""
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import figcapture
from figsession import LiveFigureSession
fig, (a, b, c) = plt.subplots(1, 3, figsize=(9, 3), dpi=int(sys.argv[2]), layout='constrained')
line, = a.plot([0, 1], [.5, .5])
scatter = a.scatter([.3, .5], [.2, .200001])
im = b.imshow(np.arange(64).reshape(8, 8))
c.imshow(np.arange(64).reshape(8, 8).T, norm=im.norm)
fig.colorbar(im, ax=[b, c])
seen = []
def observe(event):
    if type(getattr(event.renderer, '_renderer', event.renderer)).__name__ == 'RendererSVG':
        bb = b.bbox
        seen.append([bb.x0 / fig.bbox.width, bb.y0 / fig.bbox.height,
                     bb.width / fig.bbox.width, bb.height / fig.bbox.height])
fig.canvas.mpl_connect('draw_event', observe)
session = LiveFigureSession(sys.argv[3])
session.add_figure('F', fig, figcapture.SOURCE_SAVEFIG)
session.instrument_all()
base = session.do_render('F', [])['manifest']
def element(man, gid):
    return next(e for e in man['elements'] if e['gid'] == gid)
def position(man):
    return next(f['value'] for f in element(man, 'axes_1')['editable'] if f['prop'] == 'position')
for artist in (line, scatter):
    height = element(base, artist.get_gid())['bbox'][3]
    assert abs(height - 4 / (3 * int(sys.argv[2]))) < 1e-12, (artist.get_gid(), height)
initial = position(base)
assert max(abs(x-y) for x,y in zip(initial, seen[-1])) < 1e-12, (initial, seen[-1])
target = [initial[0] - .05, initial[1] + .05, initial[2], initial[3]]
result = session.do_render('F', [{'gid': 'axes_1', 'prop': 'position', 'value': target}])
assert not result['warnings'], result['warnings']
assert max(abs(x-y) for x,y in zip(seen[-1], target)) < 1e-12, (seen[-1], target)
assert max(abs(x-y) for x,y in zip(position(result['manifest']), seen[-1])) < 1e-12
print(json.dumps({'geometry_consistent': True}))
"""
    engine = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
    result = subprocess.run(
        [WORKER_PY, "-c", script, str(engine), str(dpi), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"geometry_consistent": True}
