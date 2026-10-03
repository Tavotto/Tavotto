"""Explicit cax owners are observations: native matplotlib pixels/layout stay exact."""

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib interpreter unavailable")
ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"

PROBE = """
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.figure as mfigure
import matplotlib.pyplot as plt
import numpy as np
import figcapture
from colorbarmodel import declared_parents

kind = sys.argv[2]

def make():
    fig = plt.figure(figsize=(4, 3), dpi=80)
    grid = fig.add_gridspec(1, 3, width_ratios=[1, 1, .08])
    a, b, cax = [fig.add_subplot(grid[0, i]) for i in range(3)]
    data = np.arange(36).reshape(6, 6)
    im = a.imshow(data, aspect="auto")
    b.imshow(data.T, norm=im.norm, aspect="auto")
    array = im.get_array()
    owner = {"single": a, "list": [a, b], "tuple": (a, b),
             "array": np.array([[a, b]]), "duplicate": [a, a],
             "positional": [a, b], "none": None,
             "iterator": iter([a, b]), "invalid": [object()]}[kind]
    if kind == "positional":
        cb = fig.colorbar(im, cax, owner, False, extend="both")
    else:
        cb = fig.colorbar(im, cax=cax, ax=owner, extend="both")
    parents = declared_parents(cb)
    fig.canvas.draw()
    out = {"pixels": hashlib.sha256(bytes(fig.canvas.buffer_rgba())).hexdigest(),
           "positions": [ax.get_position().bounds for ax in (a, b, cax)],
           "data_unchanged": im.get_array() is array,
           "mpl_layout_metadata": hasattr(cax, "_colorbar_info"),
           "parents": None if parents is None else [0 if p is a else 1 for p in parents]}
    if kind == "iterator":
        out["iterator_remaining"] = len(list(owner))
    plt.close(fig)
    return out

plain = make()
original = mfigure.FigureBase.colorbar
figcapture.install_colorbar_capture(mfigure)
hook = mfigure.FigureBase.colorbar
figcapture.install_colorbar_capture(mfigure)
assert mfigure.FigureBase.colorbar is hook
assert hook.__wrapped__ is original
captured = make()
print(json.dumps([plain, captured]))
"""


@pytest.mark.parametrize(
    ("kind", "owners"),
    [
        ("single", [0]),
        ("list", [0, 1]),
        ("tuple", [0, 1]),
        ("array", [0, 1]),
        ("duplicate", [0]),
        ("positional", [0, 1]),
        ("none", None),
        ("iterator", None),
        ("invalid", None),
    ],
)
def test_cax_capture_preserves_native_pixels_layout_data_and_call_shapes(kind, owners):
    proc = subprocess.run(
        [WORKER_PY, "-c", PROBE, str(ENGINE), kind],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    plain, captured = json.loads(proc.stdout.strip().splitlines()[-1])
    assert plain.pop("parents") is None
    assert captured.pop("parents") == owners
    assert captured == plain
    assert captured["data_unchanged"] is True
    assert captured["mpl_layout_metadata"] is False
