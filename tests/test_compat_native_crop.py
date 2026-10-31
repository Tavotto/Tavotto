"""Native reference PNGs use the original Matplotlib save's crop, independently of Tavotto."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageChops

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[1]
WORKER = pool.find_worker_python()


def native(tmp_path, body, width=200):
    project = tmp_path / "project"
    project.mkdir()
    script = project / "figure.py"
    script.write_text(
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\nfrom matplotlib.transforms import Bbox\n" + body,
        encoding="utf-8",
    )
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "script": str(script),
                "project": str(project),
                "engine_dir": str(ROOT / "src/tavotto/engine"),
                "out_dir": str(tmp_path / "shots"),
                "width": width,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            WORKER,
            str(ROOT / "scripts/ci/compat_driver.py"),
            "--mode",
            "native",
            "--request",
            str(request),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
        check=True,
    )
    reply = json.loads(result.stdout.strip().splitlines()[-1])
    assert reply["ok"], reply
    return project, reply


@pytest.mark.parametrize(
    "save",
    [
        "fig.savefig('o.pdf', bbox_inches='tight', pad_inches=0.23)",
        "with plt.rc_context({'savefig.bbox': 'tight', 'savefig.pad_inches': 0.23}):\n"
        "    fig.savefig('o.pdf')",
        "extra = fig.text(1.2, 0.4, 'outside'); extra.set_in_layout(False)\n"
        "fig.savefig('o.pdf', bbox_inches='tight', pad_inches=0.23, bbox_extra_artists=[extra])",
        "fig.savefig('o.pdf', bbox_inches=Bbox.from_bounds(0.5, 0.25, 2, 1))",
        "fig.savefig('o.pdf')",
        "fig.savefig('o.pdf'); plt.rcParams['savefig.bbox'] = 'tight'",
    ],
    ids=["tight-pad", "rc-context", "extra-artist", "explicit-bbox", "uncropped", "later-rc"],
)
def test_png_frame_matches_the_actual_original_pdf(tmp_path, save):
    project, result = native(
        tmp_path,
        "fig, ax = plt.subplots(figsize=(4, 2))\nax.plot([0, 1], [2, 4])\n" + save + "\n",
    )
    # Independent oracle: page dimensions from the file Matplotlib actually wrote.
    with pikepdf.open(project / "o.pdf") as pdf:
        box = [float(v) for v in pdf.pages[0].MediaBox]
    inches = [(box[2] - box[0]) / 72, (box[3] - box[1]) / 72]
    assert result["size_mm"]["o"] == pytest.approx([v * 25.4 for v in inches], abs=0.01)
    dpi = max(50, 200 / inches[0])
    with Image.open(result["shots"]["o"]) as shot:
        # PDF's decimal representation and Agg's floor can differ by one pixel.
        assert shot.size == pytest.approx([int(v * dpi) for v in inches], abs=1)


def test_explicit_crop_keeps_the_pixels_and_does_not_crop_the_figure_itself(tmp_path):
    project, result = native(
        tmp_path,
        "fig, ax = plt.subplots(figsize=(4, 2), dpi=100)\n"
        "ax.plot([0, 1], [2, 4], color='red')\n"
        "fig.savefig('o.png', bbox_inches=Bbox.from_bounds(0.5, 0.25, 2, 1))\n"
        "assert tuple(fig.get_size_inches()) == (4, 2)\n",
    )
    with Image.open(project / "o.png") as original, Image.open(result["shots"]["o"]) as shot:
        assert shot.size == original.size == (200, 100)
        assert ImageChops.difference(original.convert("RGB"), shot.convert("RGB")).getbbox() is None


def test_replaced_writer_gets_its_own_crop_and_failed_save_restores_observer(tmp_path):
    project, result = native(
        tmp_path,
        "from matplotlib import _tight_bbox\n"
        "original_adjust = _tight_bbox.adjust_bbox\n"
        "a = plt.figure(figsize=(4, 2))\n"
        "a.savefig('o.pdf', bbox_inches=Bbox.from_bounds(0, 0, 3, 1))\n"
        "b = plt.figure(figsize=(4, 4))\n"
        "b.savefig('o.pdf', bbox_inches=Bbox.from_bounds(0, 0, 2, 3))\n"
        "try:\n    a.savefig('missing/o.pdf', bbox_inches='tight')\n"
        "except FileNotFoundError:\n    pass\n"
        "assert _tight_bbox.adjust_bbox is original_adjust\n",
    )
    with Image.open(result["shots"]["o"]) as shot:
        assert shot.size == (200, 300)
    assert result["size_mm"]["o"] == [50.8, 76.2]
    assert (project / "o.pdf").is_file()


def test_original_format_and_first_save_select_the_reference_crop(tmp_path):
    _, result = native(
        tmp_path,
        "fig = plt.figure(figsize=(4, 2))\n"
        "fig.savefig('o.png', bbox_inches=Bbox.from_bounds(0, 0, 1, 1))\n"
        "fig.savefig('o.pdf', bbox_inches=Bbox.from_bounds(0, 0, 2, 1))\n"
        "fig.savefig('o.pdf', bbox_inches=Bbox.from_bounds(0, 0, 3, 1))\n"
        "plt.rcParams['savefig.bbox'] = 'tight'\n",
    )
    # The protocol selects the first save of the original format (PDF before PNG).
    with Image.open(result["shots"]["o"]) as shot:
        assert shot.size == (200, 100)
    assert result["size_mm"]["o"] == [50.8, 25.4]
