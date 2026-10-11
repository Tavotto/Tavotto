"""Size-only resize must preserve script-relative placement across axes edits and replay."""

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
import json
import sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
import manifest
import overrides

coordinate, kind, change = json.loads(sys.argv[2])

def make():
    fig = plt.figure(figsize=(5, 4), dpi=100)
    ax = fig.add_axes([.1, .1, .8, .8])
    ax.set(xlim=(0, 10), ylim=(0, 10))
    origin, dimensions, pad = ((.2, .3), (.35, .22), .02) if coordinate == "axes" else ((2, 3), (3.5, 2.2), .2)
    transform = ax.transAxes if coordinate == "axes" else ax.transData
    if kind == "round":
        patch = FancyBboxPatch(origin, *dimensions, boxstyle=f"round,pad={pad}", transform=transform)
    else:
        patch = Rectangle(origin, *dimensions, transform=transform)
    ax.add_patch(patch)
    patch.set_clip_on(False)
    state = overrides.FigState(fig)
    manifest.instrument(state)
    gid = next(e["gid"] for e in state.elements if e["artist"] is patch)
    return fig, patch, state, gid

def bounds(scene):
    fig, patch, _, _ = scene
    fig.canvas.draw()
    bb = patch.get_window_extent()
    return [bb.x0 / fig.bbox.width, bb.y0 / fig.bbox.height,
            bb.width / fig.bbox.width, bb.height / fig.bbox.height]

def apply(scene, patches):
    warnings = overrides.apply(scene[2], patches)
    assert not warnings, warnings
    return bounds(scene)

hot, reference, cold, pinned = make(), make(), make(), make()
base = bounds(hot)
size = {"gid": hot[3], "prop": "size_frac", "value": [.5, round(base[3], 4)]}
axis = {"gid": "axes_0", "prop": "position" if change == "position" else "xlim",
        "value": [.2, .15, .6, .7] if change == "position" else [0, 20]}
position = {"gid": hot[3], "prop": "pos_frac", "value": [base[0], 1 - base[1]]}
apply(hot, [size])
expected = apply(reference, [axis])
changed = apply(hot, [size, axis])
replay = apply(cold, [size, axis])
restored = apply(hot, [axis])
# Counterexample: the old frontend wrote an unchanged position and anchored the box to the figure.
apply(pinned, [size, position])
wrong = apply(pinned, [size, position, axis])
print(json.dumps({"base": base, "expected": expected, "changed": changed,
                  "replay": replay, "restored": restored, "wrong": wrong, "size": size["value"]}))
plt.close("all")
"""


@pytest.mark.parametrize("kind", ["rectangle", "round"])
@pytest.mark.parametrize(
    ("coordinate", "change"), [("axes", "position"), ("data", "position"), ("data", "xlim")]
)
def test_size_only_follows_script_anchor_and_replays(kind, coordinate, change):
    proc = subprocess.run(  # noqa: S603 — 固定探针仅在已选中的 worker 解释器运行
        [WORKER_PY, "-c", PROBE, str(ENGINE_DIR), json.dumps([coordinate, kind, change])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got["changed"][:2] == pytest.approx(got["expected"][:2], abs=1e-9)
    assert got["changed"][2:] == pytest.approx(got["size"], abs=1e-9)
    assert got["replay"] == pytest.approx(got["changed"], abs=1e-9)
    assert got["restored"] == pytest.approx(got["expected"], abs=1e-9)
    assert got["wrong"][:2] == pytest.approx(got["base"][:2], abs=1e-9)
    assert got["wrong"][:2] != pytest.approx(got["expected"][:2], abs=1e-9)
