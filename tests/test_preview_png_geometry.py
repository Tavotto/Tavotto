"""Bitmap previews pair their actual Agg draw with geometry, at every display DPI."""

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")

DRIVER = r"""
import base64, io, json, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.text import Text
from PIL import Image
import numpy as np
from figsession import LiveFigureSession
import figcapture, previewbudget, pathgeom
from matplotlib.transforms import Bbox
framed = sys.argv[2] == "True"
frame_w, frame_h = (5.4, 3.5) if framed else (5, 3.2)
previewbudget.EDITOR_SVG_HARD_LIMIT_BYTES = 1
def fixture(layout):
  fig, ax = plt.subplots(figsize=(5, 3.2), dpi=100, layout=layout)
  labels = ax.bar_label(ax.bar([0, 1, 2], [2, 3, 4], label='Values'), padding=3)
  objects = [labels[1], ax.text(.3, 2.2, 'data'),
             ax.text(.25, .45, 'axes', transform=ax.transAxes),
             ax.set_title('Three bars'), ax.set_xlabel('Horizontal axis'),
             ax.set_ylabel('Vertical axis'), ax.legend(loc='upper left', fancybox=False)]
  if framed:
   pathgeom.set_frame(fig, Bbox.from_bounds(-.1, -.2, frame_w, frame_h))
  legend = objects[-1]
  legend.get_frame().set_edgecolor('red')
  legend.get_frame().set_linewidth(1)
  legend.get_frame().set_alpha(1)
  return fig, objects

results = []
for layout in [None, 'tight', 'constrained']:
 for width in [200, 400, 800, 1600, 3200]:
  fig, objects = fixture(layout)
  legend = objects[-1]
  frames = []
  def observe(event):
   W, H = fig.bbox.width, fig.bbox.height
   data = {}
   for obj in objects:
    bb = obj.get_window_extent(event.renderer)
    x, y = obj.get_transform().transform(obj.get_position()) if isinstance(obj, Text) else (bb.x0, bb.y0)
    data[obj.get_gid()] = {'anchor': [x/W, 1-y/H],
                         'bbox': [bb.x0/W, 1-bb.y1/H, bb.width/W, bb.height/H]}
   frames.append(data)
  fig.canvas.mpl_connect('draw_event', observe)
  with tempfile.TemporaryDirectory() as out:
   s = LiveFigureSession(out)
   s.add_figure('F', fig, figcapture.SOURCE_SAVEFIG)
   s.instrument_all()
   verdict = {}
   vector = s.do_render('F', [], preview=verdict)['manifest']
   engine = fig.get_layout_engine()
   callbacks = set(fig.canvas.callbacks.callbacks.get('draw_event', {}))
   state = s.snapshot('F')
   result = s.do_preview_png('F', [], width, 'paired', with_manifest=True)
   actual = frames[-1]
   manifest = {e['gid']: e for e in result['manifest']['elements']}
   for obj in objects:
    gid = obj.get_gid()
    for field in ['anchor', 'bbox']:
     assert np.allclose(manifest[gid][field], actual[gid][field], atol=1e-12, rtol=0), (layout, width, gid, field)
   # Independent pixel oracle: only the rectangular legend border is red.
   pixels = np.asarray(Image.open(io.BytesIO(base64.b64decode(result['png']))).convert('RGB'))
   yy, xx = np.where((pixels[:,:,0] > 200) & (pixels[:,:,1] < 100) & (pixels[:,:,2] < 100))
   assert len(xx)
   dpi = max(50, width / frame_w)
   bb = manifest[legend.get_gid()]['bbox']
   expected = np.array([bb[0]*frame_w*dpi, bb[1]*frame_h*dpi,
                        (bb[0]+bb[2])*frame_w*dpi, (bb[1]+bb[3])*frame_h*dpi])
   observed = np.array([xx.min(), yy.min(), xx.max(), yy.max()])
   # Antialiased stroke and PNG integer pixel boundaries, in physical pixels.
   assert np.max(np.abs(observed - expected)) <= dpi/72/2 + 1.5, (layout, width, observed, expected)
   assert pixels.shape[1] == int(frame_w*dpi)
   assert s.snapshot('F') == state and fig.dpi == 100 and fig.get_layout_engine() is engine
   assert set(fig.canvas.callbacks.callbacks.get('draw_event', {})) == callbacks
   assert not (Path(out) / 'F__paired.png').exists()
   # The next render applies a UI drag target computed from the displayed PNG.
   for obj in [objects[0], objects[1], objects[2], legend]:
    gid = obj.get_gid()
    anchor = manifest[gid]['anchor']
    target = [anchor[0] + .03, anchor[1] + .04]
    moved = s.do_preview_png('F', [{'gid':gid, 'prop':manifest[gid]['drag_prop'], 'value':target}], width, 'moved', with_manifest=True)
    after = frames[-1][gid]['anchor']
    assert np.allclose(after, target, atol=.02/360, rtol=0), (layout,width,gid,after,target)
   undone = s.do_preview_png('F', [], width, 'undo', with_manifest=True)
   # Automatic layout converges across draws even without edits (notably mpl3.10).
   # Compare undo against an untouched control given the same six PNG draws.
   control_fig, _ = fixture(layout)
   control = LiveFigureSession(Path(out) / 'control')
   control.add_figure('F', control_fig, figcapture.SOURCE_SAVEFIG)
   control.instrument_all()
   control.do_render('F', [])
   for _ in range(6):
    control_result = control.do_preview_png('F', [], width, 'control', with_manifest=True)
   control_manifest = {e['gid']:e for e in control_result['manifest']['elements']}
   plt.close(control_fig)
   restored = {e['gid']: e for e in undone['manifest']['elements']}
   for obj in objects:
    for field in ['anchor', 'bbox']:
     assert np.allclose(restored[obj.get_gid()][field], control_manifest[obj.get_gid()][field], atol=1e-12, rtol=0), (layout,width,obj.get_gid(),field,restored[obj.get_gid()][field],control_manifest[obj.get_gid()][field])
   assert s.snapshot('F') == state
   results.append({'layout':layout, 'width':width, 'mode':verdict['mode']})
  plt.close(fig)
print(json.dumps(results))
"""


@pytest.mark.parametrize("framed", [False, True])
def test_actual_png_geometry_drag_and_restore_across_layouts_and_buckets(framed):
    proc = subprocess.run(
        [WORKER_PY, "-c", DRIVER, str(Path(pool.__file__).parent), str(framed)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    assert len(rows) == 15
    assert all(row["mode"] == "raster" for row in rows)


def test_worker_png_snapshot_is_variant_local_and_preserves_vector_artifacts(tmp_path):
    """Real stdio response pairs the requested variant, including a bucket change."""
    import base64
    import io

    from PIL import Image

    (tmp_path / "figure.py").write_text(
        "import matplotlib.pyplot as plt\n"
        "def main():\n"
        "    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=100)\n"
        "    ax.bar_label(ax.bar([0, 1, 2], [2, 3, 4]), padding=3)\n"
        "    ax.set_title('original')\n"
        "    fig.savefig('F.pdf')\n",
        encoding="utf-8",
    )
    w = pool.one_shot("figure.py", str(tmp_path), "main")
    try:
        a = [{"gid": "axes_0.title", "prop": "text", "value": "Variant A"}]
        b = [{"gid": "axes_0.title", "prop": "text", "value": "Variant B"}]
        w.override("F", a)
        svg = w.svg_path("F").read_bytes()
        canonical = (Path(w.out_dir) / "F.json").read_bytes()
        revision = w.rev
        first = w.preview_png_snapshot("F", b, 400)
        high = w.preview_png_snapshot("F", b, 1600)
        again = w.preview_png_snapshot("F", b, 400)
        assert first["png"] == again["png"] != high["png"]
        assert Image.open(io.BytesIO(base64.b64decode(high["png"]))).width == 1600
        title = next(e for e in first["manifest"]["elements"] if e["gid"] == "axes_0.title")
        assert next(f["value"] for f in title["editable"] if f["prop"] == "text") == "Variant B"
        assert w.rev == revision
        assert w.svg_path("F").read_bytes() == svg
        assert (Path(w.out_dir) / "F.json").read_bytes() == canonical
        # A temporary preview did not become the editable session's variant.
        restored = w.override("F", a)["manifest"]
        assert restored == json.loads(canonical)
        title = next(e for e in restored["elements"] if e["gid"] == "axes_0.title")
        assert next(f["value"] for f in title["editable"] if f["prop"] == "text") == "Variant A"
    finally:
        pool.discard(w)
