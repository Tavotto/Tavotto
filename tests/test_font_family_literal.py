"""字体族名的字符不授予 Fontconfig 文件选择权；原生字体/回放语义仍保留。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def font_probe(tmp_path_factory):
    try:
        worker = pool.find_worker_python()
    except pool.WorkerError:
        pytest.skip("no matplotlib worker")
    temp = tmp_path_factory.mktemp("font-family-literal")
    script = r"""
import io, json, os, shutil, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager, _mathtext
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import overrides, manifest

out = {"patterns": [], "edits": [], "font_requests": []}
root = Path(sys.argv[2])
font = font_manager.findfont(font_manager.FontProperties(family=["DejaVu Sans"]))
canary = root / "unregistered-canary.ttf"
shutil.copyfile(font, canary)
canary_name = str(canary).replace("\\", "/").replace(":", "\\:")
payload = "DejaVu Sans:file=" + canary_name
families = ["DejaVu Sans", "sans-serif", "Name, With: Punctuation-\\tail"]
for family in families:
    for style, weight in [(None, None), ("italic", None), (None, "bold")]:
        pattern = overrides._mathtext_font_name(family, style=style, weight=weight)
        parsed = font_manager.FontProperties(pattern)
        expected = matplotlib.rcParams["font.sans-serif"][0] if family == "sans-serif" else family
        out["patterns"].append({
            "family":family, "expected":expected, "actual":parsed.get_family(),
            "file":parsed.get_file(), "style":parsed.get_style(), "weight":parsed.get_weight(),
            "requested_style":style, "requested_weight":weight,
        })

original_get_font = _mathtext.get_font
def tracked_font(path, *args, **kwargs):
    out["font_requests"].append(os.fspath(path))
    return original_get_font(path, *args, **kwargs)
_mathtext.get_font = tracked_font

def make():
    fig = Figure(figsize=(3,2), dpi=72)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([.2,.2,.7,.65])
    ax.set(xlim=(1,10), ylim=(1,10), xscale="log")
    title = ax.set_title(r"$x+\alpha$")
    state = overrides.FigState(fig)
    manifest.instrument(state)
    return state, title

for gid, value in [
    ("axes_0.title", payload),
    ("axes_0.title", {"primary":payload,"fallback":"DejaVu Sans"}),
    ("axes_0.xticks", payload),
]:
    state, title = make()
    warnings = overrides.apply(state,[{"gid":gid,"prop":"fontfamily","value":value}])
    if warnings:
        raise AssertionError(warnings)
    state.fig.canvas.draw()
    patterns = {k:matplotlib.rcParams["mathtext."+k] for k in ("rm","it","bf","sf")}
    out["edits"].append({
        "gid":gid, "patterns":{k:{"family":font_manager.FontProperties(v).get_family(),
                                      "file":font_manager.FontProperties(v).get_file()}
                                for k,v in patterns.items()},
    })

def svg(state):
    stream=io.BytesIO()
    with matplotlib.rc_context({"svg.hashsalt":"font-literal","svg.fonttype":"path"}):
        state.fig.savefig(stream,format="svg",metadata={"Date":None,"Creator":None})
    return stream.getvalue()

state, title = make()
before = svg(state)
patches = [{"gid":"axes_0.title","prop":"fontfamily","value":"DejaVu Serif"}]
assert not overrides.apply(state,patches)
hot = svg(state)
fresh, _ = make()
assert not overrides.apply(fresh,patches)
assert hot == svg(fresh), "font edit hot and cold replay differ"
assert not overrides.apply(state,[])
assert before == svg(state), "font undo did not restore source"
out["hot_cold_equal"] = True
out["undo_equal"] = True
out["canary"] = str(canary)
out["canary_opened"] = any(os.path.normcase(os.path.abspath(p)) ==
                            os.path.normcase(os.path.abspath(canary)) for p in out["font_requests"])
print(json.dumps(out))
"""
    proc = subprocess.run(
        [worker, "-c", script, str(ROOT / "src/tavotto/engine"), str(temp)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "MPLCONFIGDIR": str(temp / "mpl"), "TAVOTTO_NO_TELEMETRY": "1"},
        timeout=90,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_literal_families_cannot_supply_fontconfig_attributes(font_probe):
    assert len(font_probe["patterns"]) == 9
    for result in font_probe["patterns"]:
        assert result["actual"] == [result["expected"]], result
        assert result["file"] is None, result
        if result["requested_style"]:
            assert result["style"] == result["requested_style"], result
        if result["requested_weight"]:
            assert result["weight"] == result["requested_weight"], result


def test_real_text_tick_and_family_selection_never_read_the_canary(font_probe):
    assert len(font_probe["edits"]) == 3
    assert font_probe["font_requests"], "mathtext font selection was not actually observed"
    assert font_probe["canary_opened"] is False, font_probe
    for edit in font_probe["edits"]:
        for pattern in edit["patterns"].values():
            assert pattern["file"] is None, edit


def test_literal_family_keeps_full_edit_replay_and_undo(font_probe):
    assert font_probe["hot_cold_equal"] is True
    assert font_probe["undo_equal"] is True
