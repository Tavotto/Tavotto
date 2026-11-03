"""Whole shaped runs survive capture, editable replay and native export.

The oracle is the user's uninstrumented Matplotlib in a separate process, with
the same bundled font. PNG pixels and positioned SVG glyph outlines are separate
assertions. A successful capture, Unicode label or SVG glyph ID is not an oracle.
Older runtimes without shaping are reported as such, never certified by a pass.
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from support.bridgekit import USER_PYTHON, child_env, needs_user_python
from tavotto.engine import pool

pytestmark = needs_user_python

CASES = (
    ("السَّلَامُ عَلَيْكُمْ", "ar"),
    ("Report 12: שלום 34 / مرحبا 56", None),
    ("office ffi fl e\u0301 A\u030a x\u0323", "en"),
)
SCRIPT = """\
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, fontManager

CASES = {cases!r}

def main(mode="native", replacement=False):
    font = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
    fontManager.addfont(font)
    family = FontProperties(fname=font).get_name()
    for i, (value, language) in enumerate(CASES):
        if replacement:
            value = value + " 78"
        if mode == "reversed":
            value = value[::-1]
        fig = plt.figure(figsize=(8, 1.2), dpi=120)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set(xlim=(0, 960), ylim=(0, 144))
        ax.set_axis_off()
        features = ["-liga"] if mode == "no_ligatures" else ["liga"]
        values = list(value) if mode == "split" else [value]
        for j, text in enumerate(values):
            ax.text(24 + j * 24, 55, text, fontsize=24, fontfamily=family,
                    fontfeatures=features, language=language, va="baseline",
                    gid="label" if j == 0 else "fragment_" + str(j))
        fig.savefig("Complex" + str(i) + ".png", dpi=120)
        fig.savefig("Complex" + str(i) + ".svg")
"""


def _child(code: str, *args: Path | str) -> str:
    result = subprocess.run(
        [USER_PYTHON, "-c", code, *map(str, args)],
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _native(script: Path, out: Path, mode="native", replacement=False):
    out.mkdir()
    _child(
        "import os,runpy,sys; os.chdir(sys.argv[2]); "
        "runpy.run_path(sys.argv[1])['main'](sys.argv[3], sys.argv[4]=='yes')",
        script,
        out,
        mode,
        "yes" if replacement else "no",
    )


def _pixels(path: Path) -> str:
    return _child(
        "import hashlib,sys; from PIL import Image; "
        "im=Image.open(sys.argv[1]).convert('RGBA'); "
        "print(im.size, hashlib.sha256(im.tobytes()).hexdigest())",
        path,
    ).strip()


def _glyphs(path: Path) -> list:
    root = ET.parse(path).getroot()
    by_id = {e.attrib["id"]: e for e in root.iter() if "id" in e.attrib}
    shapes = []

    def walk(el, transforms=()):
        transforms += (el.attrib.get("transform", ""),)
        if el.tag.rsplit("}", 1)[-1] == "use":
            target = by_id[el.attrib["{http://www.w3.org/1999/xlink}href"][1:]]
            assert target.tag.rsplit("}", 1)[-1] == "path"
            shapes.append(
                (
                    tuple(t for t in transforms if t),
                    target.attrib.get("d", ""),  # A space is a positioned empty outline.
                    tuple(sorted((k, v) for k, v in el.attrib.items() if "href" not in k)),
                )
            )
        for child in el:
            walk(child, transforms)

    walk(root)
    assert shapes, "No positioned glyph outlines were rendered"
    return shapes


@pytest.fixture(scope="module")
def shaped(tmp_path_factory):
    runtime = json.loads(
        _child(
            "import json,matplotlib; from matplotlib.text import Text; "
            "print(json.dumps({'version':matplotlib.__version__, "
            "'shaping':hasattr(Text,'set_fontfeatures') and hasattr(Text,'set_language')}))"
        )
    )
    if not runtime["shaping"]:
        pytest.skip(f"Matplotlib {runtime['version']} lacks the whole-run shaping API")
    root = tmp_path_factory.mktemp("complex-text")
    script = root / "figure.py"
    script.write_text(SCRIPT.format(cases=CASES), encoding="utf-8")
    for mode in ("native", "split", "reversed", "no_ligatures"):
        _native(script, root / mode, mode)
    _native(script, root / "edited", replacement=True)
    return root


@pytest.mark.parametrize("index", range(len(CASES)))
def test_capture_and_edit_preserve_native_shaped_pixels_and_outlines(shaped, index):
    worker = pool.one_shot("figure.py", str(shaped), "main")
    try:
        worker.ensure_built()
        stem = f"Complex{index}"
        manifest = worker.override(stem, [])["manifest"]
        labels = [
            el
            for el in manifest["elements"]
            if any(
                f.get("prop") == "text" and f.get("value") == CASES[index][0]
                for f in el.get("editable", [])
            )
        ]
        assert len(labels) == 1, "The whole logical run must remain one editable Text"
        gid = labels[0]["gid"]
        for label, patches in (
            ("native", []),
            ("edited", [{"gid": gid, "prop": "text", "value": CASES[index][0] + " 78"}]),
            ("native", []),  # Replaying empty overrides restores the original whole run.
        ):
            worker.override(stem, patches)
            for fmt in ("png", "svg"):
                target = shaped / f"worker-{index}-{label}.{fmt}"
                worker.export(stem, patches, str(target), fmt, dpi=120, svg_fonttype="path")
                expected = shaped / label / f"{stem}.{fmt}"
                measure = _pixels if fmt == "png" else _glyphs
                assert measure(target) == measure(expected), (label, index, fmt)
    finally:
        pool.discard(worker)


@pytest.mark.parametrize("index,mutation", [(0, "split"), (1, "reversed"), (2, "no_ligatures")])
def test_fidelity_oracles_reject_broken_shaping(shaped, index, mutation):
    for fmt, measure in (("png", _pixels), ("svg", _glyphs)):
        original = shaped / "native" / f"Complex{index}.{fmt}"
        broken = shaped / mutation / f"Complex{index}.{fmt}"
        assert measure(original) != measure(broken), (index, mutation, fmt)
