"""A suppressed single save must not acquire extra tight-layout iterations.

The oracle is the script's own first SVG, read independently of the manifest.
SVG-only comparisons intentionally make no claim about PDF/PNG renderer metrics.
All matplotlib work stays in scientific-stack subprocesses, like the real worker.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")
ENGINE = Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine"
TOL_PT = 0.02
STEM = "initial"
SCRIPT_NAME = "figure.py"
COLORS = {
    "spines": "#284e74",
    "curve": "#d62728",
    "title": "#80338c",
    "xlabel": "#007f5f",
    "ylabel": "#e07800",
    "xticks": "#0044dd",
    "yticks": "#bb0077",
    "data": "#773311",
    "axes": "#0099bb",
    "suptitle": "#222299",
}
CREATE = """\
import matplotlib
matplotlib.use('Agg')
matplotlib.rcParams['svg.fonttype'] = 'none'
import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots(figsize=(5, 3.2), dpi=100, layout='tight')
x = np.linspace(0, 6, 31)
ax.plot(x, np.sin(x) * .5 + 1, color='#d62728')
ax.set(xlim=(0, 6), ylim=(0, 2))
ax.set_title('Internal positions', color='#80338c', fontsize=12)
ax.set_xlabel('Time (s)', color='#007f5f', fontsize=10)
ax.set_ylabel('Response (a.u.)', color='#e07800', fontsize=10)
ax.tick_params(axis='x', colors='#0044dd', labelsize=9)
ax.tick_params(axis='y', colors='#bb0077', labelsize=9)
ax.text(2.5, .5, 'data anchor', color='#773311', fontsize=8.5)
ax.text(.72, .2, 'axes anchor', transform=ax.transAxes, color='#0099bb', fontsize=8.5)
for spine in ax.spines.values():
    spine.set_color('#284e74')
fig.suptitle('Repro figure', fontsize=12, color='#222299')
"""


def _run(code, cwd, *args, payload=None):
    result = subprocess.run(
        [WORKER_PY, "-c", code, str(ENGINE), *map(str, args)],
        cwd=cwd,
        input=None if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _project(tmp_path, *, crop=False, create=CREATE):
    kwargs = {"bbox_inches": "tight", "pad_inches": 0.04} if crop else {}
    source = create + f"fig.savefig('{STEM}.svg', **{kwargs!r})\nplt.close(fig)\n"
    (tmp_path / SCRIPT_NAME).write_text(source, encoding="utf-8")
    # Save #2 is only a negative control in the reference process. The source
    # consumed by Tavotto still has exactly one observed save for this Figure.
    _run(
        "import json, runpy, sys\n"
        "ns = runpy.run_path('figure.py', run_name='__main__')\n"
        "ns['fig'].savefig('native-second.svg', **json.loads(sys.argv[2]))\n",
        tmp_path,
        json.dumps(kwargs),
    )
    return source


def _numbers(value):
    return [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", value)]


def _svg_geometry(svg):
    """Read actual colored paths and text anchors, independent of Tavotto gids."""
    root = ET.fromstring(svg)
    result = {"viewBox": _numbers(root.attrib["viewBox"])}
    by_color = {color: role for role, color in COLORS.items()}
    counts = {}
    for element in root.iter():
        style = dict(
            (key.strip(), value.strip())
            for part in element.get("style", "").split(";")
            if ":" in part
            for key, value in [part.split(":", 1)]
        )
        if element.tag.endswith("}path") and style.get("stroke") in {
            COLORS["spines"],
            COLORS["curve"],
        }:
            role = by_color[style["stroke"]]
            number = counts.get(role, 0)
            counts[role] = number + 1
            result[f"{role}:{number}"] = _numbers(element.attrib["d"])
        elif element.tag.endswith("}text") and style.get("fill") in by_color:
            role = by_color[style["fill"]]
            label = "".join(element.itertext())
            key = f"{role}:{label}"
            # For rotated labels, SVG writes the anchor in transform instead of
            # x/y. Include both forms and the actual rotation, never a manifest.
            result[key] = _numbers(
                " ".join(element.get(attr, "") for attr in ("x", "y", "transform"))
            )
    assert counts.get("spines") == 4 and counts.get("curve") == 1, result
    assert all(any(key.startswith(role + ":") for key in result) for role in COLORS), result
    return result


def _assert_same_svg(actual, expected):
    actual, expected = _svg_geometry(actual), _svg_geometry(expected)
    assert actual.keys() == expected.keys(), "The delivered SVG changed its drawn elements"
    for key, values in expected.items():
        assert actual[key] == pytest.approx(values, abs=TOL_PT), key


def _axes_box(svg):
    geometry = _svg_geometry(svg)
    points = [
        value for key, values in geometry.items() if key.startswith("spines:") for value in values
    ]
    return min(points[::2]), min(points[1::2]), max(points[::2]), max(points[1::2])


@contextmanager
def _worker(project):
    worker = pool.one_shot(SCRIPT_NAME, str(project), "__main__")
    try:
        worker.ensure_built()
        yield worker
    finally:
        pool.discard(worker)


def _svg(worker, patches=None):
    if patches is not None:
        response = worker.override(STEM, patches)
        assert not response["warnings"], response["warnings"]
    return worker.svg_path(STEM).read_text(encoding="utf-8")


@pytest.mark.parametrize("crop", [False, True], ids=["figsize", "tight-frame"])
def test_first_worker_svg_preserves_first_native_svg_and_repeated_renders(tmp_path, crop):
    """Cold build, not an empty warm-up render, must preserve internal positions."""
    _project(tmp_path, crop=crop)
    original = (tmp_path / f"{STEM}.svg").read_text(encoding="utf-8")
    untouched = {path: path.read_bytes() for path in tmp_path.iterdir()}
    if not crop:
        second = (tmp_path / "native-second.svg").read_text(encoding="utf-8")
        # Preconditions for this regression: native save #2 really changes the
        # margin (5.72625 pt on Matplotlib 3.11.1). This is not a converged fixture.
        assert abs(_axes_box(original)[0] - _axes_box(second)[0]) > 1
    with _worker(tmp_path) as worker:
        _assert_same_svg(_svg(worker), original)
        for _ in range(3):
            _assert_same_svg(_svg(worker, []), original)
    assert {path: path.read_bytes() for path in untouched} == untouched


EDITS = [
    pytest.param([{"gid": "axes_0.ylabel", "prop": "fontsize", "value": 22}], id="fontsize"),
    pytest.param(
        [{"gid": "axes_0.yticks", "prop": "fontfamily", "value": "DejaVu Sans Mono"}],
        id="font-family",
    ),
    pytest.param(
        [{"gid": "axes_0.ylabel", "prop": "text", "value": "Response\nsecond line"}],
        id="label",
    ),
    pytest.param(
        [
            {"gid": "axes_0.yticks", "prop": "major_mode", "value": "step"},
            {"gid": "axes_0.yticks", "prop": "major_step", "value": 0.125},
        ],
        id="tick-locator",
    ),
    pytest.param([{"gid": "figure", "prop": "size_mm", "value": [152.4, 101.6]}], id="figure-size"),
]


@pytest.mark.parametrize("patches", EDITS)
def test_edits_reflow_and_undo_matches_first_svg_and_fresh_replay(tmp_path, patches):
    _project(tmp_path)
    original = (tmp_path / f"{STEM}.svg").read_text(encoding="utf-8")
    untouched = {path: path.read_bytes() for path in tmp_path.iterdir()}
    with _worker(tmp_path) as hot:
        # Enter the final list through a different edit to expose history leakage.
        _svg(hot, [{"gid": "axes_0.xlabel", "prop": "fontsize", "value": 18}])
        edited = _svg(hot, patches)
        assert max(abs(a - b) for a, b in zip(_axes_box(edited), _axes_box(original))) > 0.1
        _assert_same_svg(_svg(hot, patches), edited)
        with _worker(tmp_path) as fresh:
            _assert_same_svg(_svg(fresh, patches), edited)
        _assert_same_svg(_svg(hot, []), original)
        _assert_same_svg(_svg(hot, []), original)
    assert {path: path.read_bytes() for path in untouched} == untouched


def test_axes_position_pin_survives_layout_and_undo_restores_first_svg(tmp_path):
    _project(tmp_path)
    original = (tmp_path / f"{STEM}.svg").read_text(encoding="utf-8")
    bounds = [0.23, 0.20, 0.60, 0.53]
    pin = {"gid": "axes_0", "prop": "position", "value": bounds}
    patches = [pin, {"gid": "axes_0.ylabel", "prop": "fontsize", "value": 18}]
    with _worker(tmp_path) as hot:
        _svg(hot, [pin])
        edited = _svg(hot, patches)
        left, bottom, width, height = bounds
        assert _axes_box(edited) == pytest.approx(
            (left * 360, (1 - bottom - height) * 230.4, (left + width) * 360, (1 - bottom) * 230.4),
            abs=TOL_PT,
        )
        _assert_same_svg(_svg(hot, patches), edited)
        with _worker(tmp_path) as fresh:
            _assert_same_svg(_svg(fresh, patches), edited)
        _assert_same_svg(_svg(hot, []), original)


def test_browser_first_open_and_repeated_renders_preserve_the_first_svg(tmp_path):
    source = _project(tmp_path)
    original = (tmp_path / f"{STEM}.svg").read_text(encoding="utf-8")
    driver = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import browser
request = json.load(sys.stdin)
out = []
for command in request:
    response = json.loads(browser.handle(json.dumps(command)))
    assert response['ok'], response
    assert not response.get('warnings'), response
    out.append(response)
out.append(Path(request[0]['workspace'], request[0]['filename']).read_text(encoding='utf-8'))
print(json.dumps(out))
"""
    commands = [
        {
            "cmd": "load",
            "workspace": str(tmp_path / "browser"),
            "filename": SCRIPT_NAME,
            "source": source,
        },
        {"cmd": "open", "stem": STEM},
        {"cmd": "render", "stem": STEM, "patches": []},
        {"cmd": "render", "stem": STEM, "patches": []},
    ]
    result = json.loads(_run(driver, tmp_path, payload=commands).strip().splitlines()[-1])
    for response in result[1:-1]:
        _assert_same_svg(response["svg"], original)
    assert result[-1] == source


@pytest.mark.parametrize("entry", ["worker", "browser"])
def test_real_file_object_save_excludes_figure_from_stabilization(tmp_path, entry):
    """Exercise capture wiring, not just the helper's explicit passthrough input."""
    source = (
        CREATE
        + """
import io
import sys
pathgeom = next(module for name, module in list(sys.modules.items())
                if name in ('pathgeom', 'tavotto_bridge_runtime.pathgeom'))
fig.savefig(io.BytesIO(), format='svg')
fig.savefig('initial.svg')
plt.close(fig)

# A fixture-only observer checks the boundary after the real capture hooks ran.
# It neither fabricates capture flags nor changes the helper's return value.
original_stabilize = pathgeom.stabilize_captured_tight_layouts
original_engine = fig.get_layout_engine()
original_execute = original_engine.execute
def checked_stabilize(capture, calls, passthrough=()):
    assert id(fig) in passthrough, 'The actual file-object save was not recorded'
    result = original_stabilize(capture, calls, passthrough)
    assert fig.get_layout_engine() is original_engine
    assert original_engine.execute == original_execute
    return result
pathgeom.stabilize_captured_tight_layouts = checked_stabilize
"""
    )
    if entry == "worker":
        path = tmp_path / SCRIPT_NAME
        path.write_text(source, encoding="utf-8")
        with _worker(tmp_path) as worker:
            _svg_geometry(_svg(worker))
        assert path.read_text(encoding="utf-8") == source
    else:
        driver = """
import json, sys
sys.path.insert(0, sys.argv[1])
import browser
source = json.load(sys.stdin)
session = browser.BrowserSession(sys.argv[2])
response = session.load('figure.py', source)
assert response['ok'], response
response = session.open_figure('initial')
assert response['ok'], response
print(json.dumps(response))
"""
        result = json.loads(
            _run(driver, tmp_path, tmp_path / "browser", payload=source).strip().splitlines()[-1]
        )
        _svg_geometry(result["svg"])


@pytest.mark.parametrize("warm", [False, True], ids=["before-first-draw", "after-render"])
def test_external_subplot_adjustment_becomes_the_new_seed(tmp_path, warm):
    driver = """
import io, json, sys
sys.path.insert(0, sys.argv[1])
import pathgeom
source = json.load(sys.stdin)

def make():
    ns = {}
    exec(source, ns)
    return ns['fig']

def save(fig):
    out = io.StringIO()
    fig.savefig(out, format='svg')
    return out.getvalue()

fig, reference = make(), make()
engine = fig.get_layout_engine()
settings = engine.get().copy()
pathgeom.stabilize_captured_tight_layouts({'f': fig}, {'f': [{}]})
hook = engine.execute
pathgeom.stabilize_captured_tight_layouts({'f': fig}, {'f': [{}]})
assert fig.get_layout_engine() is engine and engine.execute is hook
assert engine.get() == settings
before = save(make())
if sys.argv[2] == 'True':
    save(fig)
    save(fig)
# Changed outside the engine before the first draw or after rendering; it must
# not be overwritten by the original seed. The reference has never been drawn.
for target in (fig, reference):
    target.subplots_adjust(left=.4, bottom=.4, right=.95, top=.85, hspace=.35, wspace=.4)
expected = save(reference)
actual = [save(fig), save(fig), save(fig)]
print(json.dumps({'before': before, 'expected': expected, 'actual': actual}))
"""
    result = json.loads(_run(driver, tmp_path, warm, payload=CREATE).strip().splitlines()[-1])
    assert _axes_box(result["expected"]) != pytest.approx(_axes_box(result["before"]), abs=0.1)
    for svg in result["actual"]:
        _assert_same_svg(svg, result["expected"])


@pytest.mark.parametrize(
    "case",
    [
        "unobserved",
        "pyplot",
        "multiple-calls",
        "multiple-stems",
        "passthrough",
        "subclass",
        "instance-execute",
        "manual-position",
        "add-axes",
        "subfigure",
        "constrained",
        "none",
    ],
)
def test_unsupported_capture_retains_its_native_layout_behavior(tmp_path, case):
    driver = """
import io, json, sys
sys.path.insert(0, sys.argv[1])
import pathgeom
from matplotlib.layout_engine import TightLayoutEngine
source = json.load(sys.stdin)
case = sys.argv[2]

class CustomTight(TightLayoutEngine):
    def execute(self, fig):
        return super().execute(fig)

def make():
    ns = {}
    exec(source, ns)
    fig = ns['fig']
    if case == 'subclass':
        fig.set_layout_engine(CustomTight(pad=2))
    elif case == 'instance-execute':
        engine = fig.get_layout_engine()
        native = engine.execute
        engine.execute = lambda target: native(target)
    elif case in ('none', 'constrained'):
        fig.set_layout_engine(case)
    elif case == 'manual-position':
        fig.set_size_inches(2, 1)
        ns['ax'].set_position([.3, .3, .6, .6])
        ns['ax'].xaxis.label.set_fontsize(50)
        ns['ax'].yaxis.label.set_fontsize(50)
    elif case == 'add-axes':
        fig.add_axes([.3, .3, .2, .2])
    elif case == 'subfigure':
        fig.delaxes(ns['ax'])
        fig.subfigures().subplots()
    return fig

fig, reference = make(), make()
engine = fig.get_layout_engine()
execute = engine.execute if engine is not None else None
capture = {'f': fig}
calls = {'f': [{}]}
passthrough = ()
if case == 'unobserved': calls['f'] = None
if case == 'pyplot': calls['f'] = []
if case == 'multiple-calls': calls['f'] = [{}, {}]
if case == 'multiple-stems': capture['other'] = fig; calls['other'] = [{}]
if case == 'passthrough': passthrough = (id(fig),)
pathgeom.stabilize_captured_tight_layouts(capture, calls, passthrough)
assert fig.get_layout_engine() is engine
assert (engine.execute if engine is not None else None) == execute
for _ in range(3):
    fig.savefig(io.BytesIO(), format='svg')
    reference.savefig(io.BytesIO(), format='svg')
    assert tuple(fig.axes[0].get_position().bounds) == tuple(reference.axes[0].get_position().bounds)
print(json.dumps({'native': True}))
"""
    result = json.loads(_run(driver, tmp_path, case, payload=CREATE).strip().splitlines()[-1])
    assert result == {"native": True}


def test_manual_source_position_survives_tight_layout_refusal(tmp_path):
    create = (
        CREATE.replace("figsize=(5, 3.2)", "figsize=(2, 1)")
        + """
ax.set_position([.3, .3, .6, .6])
ax.xaxis.label.set_fontsize(50)
ax.yaxis.label.set_fontsize(50)
"""
    )
    _project(tmp_path, create=create)
    original = (tmp_path / f"{STEM}.svg").read_text(encoding="utf-8")
    assert _axes_box(original) == pytest.approx((43.2, 7.2, 129.6, 50.4), abs=TOL_PT)
    with _worker(tmp_path) as worker:
        _assert_same_svg(_svg(worker), original)
        _assert_same_svg(_svg(worker, []), original)


@pytest.mark.parametrize("failure", ["refusal", "exception"])
def test_failed_layout_restores_prepass_axes_and_subplot_parameters(tmp_path, failure):
    driver = """
import io, json, sys, warnings
sys.path.insert(0, sys.argv[1])
import pathgeom
import matplotlib.layout_engine as layout_engine
source = json.load(sys.stdin)
ns = {}
exec(source, ns)
fig, ax = ns['fig'], ns['ax']
engine = fig.get_layout_engine()
native_execute = engine.execute
pathgeom.stabilize_captured_tight_layouts({'f': fig}, {'f': [{}]})
assert engine.execute != native_execute, 'Fixture must exercise an eligible, installed hook'

def save():
    out = io.StringIO()
    fig.savefig(out, format='svg')
    return out.getvalue()

def snapshot():
    return (
        tuple(getattr(fig.subplotpars, key) for key in ('left', 'right', 'top', 'bottom', 'hspace', 'wspace')),
        tuple(ax.get_position(original=True).bounds),
        tuple(ax.get_position().bounds),
    )

before_svg = save()
before = snapshot()
native_adjust = fig.subplots_adjust
refused = []
if sys.argv[2] == 'refusal':
    ax.xaxis.label.set_fontsize(250)
    ax.yaxis.label.set_fontsize(250)
    for _ in range(2):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            refused.append(save())
        assert any('Tight layout not applied' in str(w.message) for w in caught), caught
        assert snapshot() == before
    ax.xaxis.label.set_fontsize(10)
    ax.yaxis.label.set_fontsize(10)
else:
    native_layout = layout_engine.get_tight_layout_figure
    def fail(*args, **kwargs):
        raise RuntimeError('layout failure injected by test')
    layout_engine.get_tight_layout_figure = fail
    try:
        try:
            save()
        except RuntimeError as exc:
            assert str(exc) == 'layout failure injected by test'
        else:
            raise AssertionError('The layout failure was not exercised')
    finally:
        layout_engine.get_tight_layout_figure = native_layout
    assert snapshot() == before
assert fig.subplots_adjust == native_adjust
assert fig.get_layout_engine() is engine
print(json.dumps({'before': before_svg, 'refused': refused, 'after': save()}))
"""
    result = json.loads(_run(driver, tmp_path, failure, payload=CREATE).strip().splitlines()[-1])
    for svg in result["refused"]:
        assert _axes_box(svg) == pytest.approx(_axes_box(result["before"]), abs=TOL_PT)
        assert _svg_geometry(svg)["curve:0"] == pytest.approx(
            _svg_geometry(result["before"])["curve:0"], abs=TOL_PT
        )
    _assert_same_svg(result["after"], result["before"])


def test_live_session_default_does_not_stabilize_native_figures(tmp_path):
    driver = """
import json, sys
sys.path.insert(0, sys.argv[1])
import figcapture
import figsession
source = json.load(sys.stdin)
ns = {}
exec(source, ns)
fig = ns['fig']
engine = fig.get_layout_engine()
execute = engine.execute
session = figsession.LiveFigureSession(sys.argv[2])
session.add_figure('f', fig, figcapture.SOURCE_SAVEFIG)
session.note_savefig('f', fig, figcapture.savefig_call('f.svg', {}))
session.instrument_all()
assert fig.get_layout_engine() is engine and engine.execute == execute
session.do_render('f', [])
assert fig.get_layout_engine() is engine and engine.execute == execute
print(json.dumps({'native': True}))
"""
    result = json.loads(
        _run(driver, tmp_path, tmp_path / "native", payload=CREATE).strip().splitlines()[-1]
    )
    assert result == {"native": True}
