"""Read exported paths/pixels, not a post-export redraw that can hide frame errors."""

from __future__ import annotations

import base64
import io
import json
import subprocess
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有可用的 matplotlib 解释器")
ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"

PROBE = r"""
import base64
import io
import json
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.transforms import Bbox
import manifest
import overrides
import pathgeom

kind, frame_kind, pinned, adjustable = json.loads(sys.argv[2])
fig = plt.figure(figsize=(5, 4), dpi=100)
ax = fig.add_axes([.1, .1, .8, .8])
ax.set(xlim=(0, 10), ylim=(0, 10), aspect="equal", adjustable=adjustable)
ax.set_axis_off()
patch = (FancyBboxPatch((2, 3), 3.5, 2.2, boxstyle="round,pad=.2", transform=ax.transData)
         if kind == "round" else Rectangle((2, 3), 3.5, 2.2, transform=ax.transData))
ax.add_patch(patch)
patch.set(facecolor="red", edgecolor="none", clip_on=False)
fig.canvas.draw()
if frame_kind == "tight":
    frame = pathgeom.savefig_frame(fig, {"bbox_inches": "tight", "format": "png",
                                         "dpi": 100, "pad_inches": .2}, None, Figure.savefig)
else:
    frame = Bbox.from_bounds(*((.4, .3, 4.2, 3.4) if frame_kind == "inset" else (-.5, -.2, 6, 4.8)))
pathgeom.set_frame(fig, frame)
frame_size = list(pathgeom.frame_size_inches(fig))
state = overrides.FigState(fig)
manifest.instrument(state)
gid = next(e["gid"] for e in state.elements if e["artist"] is patch)
patches = [{"gid": gid, "prop": "size_frac", "value": [.3, .2]}]
if pinned:
    patches.append({"gid": gid, "prop": "pos_frac", "value": [.2, .65]})
warnings = overrides.apply(state, patches)
assert not warnings, warnings

def snapshot():
    man = manifest.build_manifest(state, "Frame")
    element = next(e for e in man["elements"] if e["gid"] == gid)
    return element["shape_bounds"]

expected = snapshot()
outputs = []
for dpi in (72, 180):
    for fmt in ("png", "pdf"):
        stream = io.BytesIO()
        fig.savefig(stream, format=fmt, dpi=dpi, **pathgeom.output_kwargs(fig))
        # Capture the produced bytes BEFORE another draw repairs the hot geometry.
        data = base64.b64encode(stream.getvalue()).decode("ascii")
        outputs.append({"format": fmt, "dpi": dpi, "data": data, "after": snapshot()})
        assert list(pathgeom.frame_size_inches(fig)) == frame_size
        assert "_mm_in_frame" not in vars(fig)
print(json.dumps({"expected": expected, "frame_size": frame_size, "outputs": outputs}))
plt.close(fig)
"""

FRAME_PROBE = r"""
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.transforms import Bbox
import pathgeom

fig = plt.figure(figsize=(5, 4), dpi=100)
fig.add_axes([.1, .1, .8, .8])
fig.canvas.draw()
frame = Bbox.from_bounds(*json.loads(sys.argv[2]))
pathgeom.set_frame(fig, frame)
size = pathgeom.frame_size_inches(fig)
point = pathgeom.frac_to_display(fig, .2, .65)
# Normal savefig uses adjust_bbox without Tavotto's in_frame marker.
restore = pathgeom._adjust_bbox(fig, frame, fig.canvas.get_renderer())
try:
    assert "_mm_in_frame" not in vars(fig)
    adjusted_size = pathgeom.frame_size_inches(fig)
    adjusted_point = pathgeom.frac_to_display(fig, .2, .65)
finally:
    restore()
with pathgeom.in_frame(fig):
    measured_size = pathgeom.frame_size_inches(fig)
    measured_point = pathgeom.frac_to_display(fig, .2, .65)
assert "_mm_in_frame" not in vars(fig)
assert pathgeom.frame_size_inches(fig) == size
assert pathgeom.frac_to_display(fig, .2, .65) == point
pathgeom.suppress_frame(fig, True)
plain_size = pathgeom.frame_size_inches(fig)
plain_point = pathgeom.frac_to_display(fig, .2, .65)
print(json.dumps({"size": size, "adjusted_size": adjusted_size, "measured_size": measured_size,
                  "adjusted_point": adjusted_point, "measured_point": measured_point,
                  "plain_size": plain_size, "plain_point": plain_point}))
plt.close(fig)
"""


def _run(script, arguments):
    proc = subprocess.run(  # noqa: S603 — 固定探针只在已选中的 worker 解释器运行
        [WORKER_PY, "-c", script, str(ENGINE_DIR), json.dumps(arguments)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _png_box(data):
    with Image.open(io.BytesIO(data)) as image:
        red, green, blue = image.convert("RGB").split()
        mask = ImageChops.multiply(
            red.point(lambda v: 255 if v > 240 else 0),
            green.point(lambda v: 255 if v < 20 else 0),
        )
        mask = ImageChops.multiply(mask, blue.point(lambda v: 255 if v < 20 else 0))
        bounds = mask.getbbox()
        assert bounds is not None, "exported PNG must contain the resized box"
        left, top, right, bottom = bounds
        width, height = image.size
        box = [left / width, top / height, (right - left) / width, (bottom - top) / height]
        return box, 2 / min(width, height)


def _pdf_box(data):
    with pymupdf.open(stream=data, filetype="pdf") as document:
        page = document[0]
        paths = [
            item
            for item in page.get_drawings()
            if item.get("fill") is not None
            and item["fill"][0] > 0.99
            and max(item["fill"][1:]) < 0.01
        ]
        assert len(paths) == 1, "measure the unique exported red path, not the page bbox"
        box = paths[0]["rect"]
        return [
            box.x0 / page.rect.width,
            box.y0 / page.rect.height,
            box.width / page.rect.width,
            box.height / page.rect.height,
        ]


@pytest.mark.parametrize("kind", ["rectangle", "round"])
@pytest.mark.parametrize("frame_kind", ["tight", "inset", "outset"])
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("adjustable", ["box", "datalim"])
def test_resized_patch_export_matches_measured_frame(kind, frame_kind, pinned, adjustable):
    got = _run(PROBE, [kind, frame_kind, pinned, adjustable])
    expected = got["expected"]
    assert expected[2:] == pytest.approx([0.3, 0.2], abs=1e-9)
    if pinned:
        assert expected[:2] == pytest.approx([0.2, 0.45], abs=1e-9)
    assert len(got["outputs"]) == 4
    for output in got["outputs"]:
        data = base64.b64decode(output["data"], validate=True)
        if output["format"] == "pdf":
            assert _pdf_box(data) == pytest.approx(expected, abs=2e-6)
        else:
            actual, tolerance = _png_box(data)
            assert actual == pytest.approx(expected, abs=tolerance)
        assert output["after"] == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("bounds", [(0.4, 0.3, 4.2, 3.4), (-0.5, -0.2, 6, 4.8), (0, 0, 4.2, 3.4)])
def test_frame_helpers_agree_in_savefig_and_measurement_contexts(bounds):
    got = _run(FRAME_PROBE, bounds)
    for key in ("size", "adjusted_size", "measured_size"):
        assert got[key] == pytest.approx(bounds[2:], abs=1e-12)
    expected = [0.2 * bounds[2] * 100, 0.35 * bounds[3] * 100]
    for key in ("adjusted_point", "measured_point"):
        assert got[key] == pytest.approx(expected, abs=1e-10)
    assert got["plain_size"] == [5, 4]
    assert got["plain_point"] == pytest.approx([100, 140], abs=1e-10)
