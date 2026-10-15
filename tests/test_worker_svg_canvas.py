"""Pair eligibility covers the live canvas and its actual PDF/SVG serializers."""

import json
import subprocess
import xml.etree.ElementTree as ET

import pymupdf
import pytest

from support.bridgekit import USER_PYTHON, child_env, needs_user_python
from tavotto.engine import pool

CANVAS_PROBE = r"""
import functools, importlib.util, inspect, json, sys
from contextlib import contextmanager
from pathlib import Path
engine, directory, mode, hook = sys.argv[1:]
spec = importlib.util.spec_from_file_location("boot", Path(engine) / "bridgeboot.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
pkg = boot.load_engine_modules(engine, ("figcapture", "patchspec", "pathgeom", "axestraversal",
    "spinemodel", "tickmodel", "colorbarmodel", "legendmodel", "overrides", "manifest",
    "preview_hybrid", "figsession", "wireproto"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import backend_bases
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import FigureCanvasPdf
from matplotlib.backends.backend_svg import FigureCanvasSVG

out = Path(directory)
fig, ax = plt.subplots(figsize=(3, 2), dpi=100)
ax.plot([0, 1], [0, 1])
title = ax.set_title("Export title")
session = pkg.figsession.LiveFigureSession(out / "preview")
session.add_figure("F", fig, pkg.figcapture.SOURCE_PYPLOT)
session.instrument_all()
patches = [{"gid": title.get_gid(), "prop": "text", "value": "Export title"}]
calls, saves = [], []
real_savefig = fig.savefig

def mutate(fmt):
    calls.append(fmt)
    title.set_text("Canvas " + fmt.upper())

if hook == "savefig":
    @functools.wraps(real_savefig)
    def savefig(filename, **kwargs):
        mutate(kwargs["format"])
        return real_savefig(filename, **kwargs)
    fig.savefig = savefig
elif hook == "savefig_descriptor":
    def get_savefig(self):
        # Inspecting this descriptor must not trigger the real draw it requests.
        calls.append("getter")
        self.canvas.draw()
        def savefig(filename, **kwargs):
            mutate(kwargs["format"])
            return real_savefig(filename, **kwargs)
        return savefig
    type(fig).savefig = property(get_savefig)
elif hook == "class_savefig":
    @functools.wraps(real_savefig)
    def savefig(self, filename, **kwargs):
        mutate(kwargs["format"])
        return real_savefig(filename, **kwargs)
    type(fig).savefig = savefig
elif hook == "class_print_figure":
    native = fig.canvas.print_figure
    @functools.wraps(native)
    def print_figure(self, filename, **kwargs):
        mutate(kwargs["format"])
        return native(filename, **kwargs)
    FigureCanvasAgg.print_figure = print_figure
elif hook == "backend_new":
    native = FigureCanvasSVG.__new__
    def create(cls, *args, **kwargs):
        mutate("svg")
        return native(cls)
    FigureCanvasSVG.__new__ = staticmethod(create)
elif hook == "backend_constructor":
    native = FigureCanvasSVG.__init__
    @functools.wraps(native)
    def initialize(self, figure):
        mutate("svg")
        return native(self, figure)
    FigureCanvasSVG.__init__ = initialize
elif hook == "subclass":
    class CustomCanvas(FigureCanvasAgg):
        def print_figure(self, filename, **kwargs):
            mutate(kwargs["format"])
            return super().print_figure(filename, **kwargs)
    CustomCanvas(fig)
elif hook == "print_figure":
    native = fig.canvas.print_figure
    # functools.wraps copies the native module, but this is still a user hook.
    @functools.wraps(native)
    def print_figure(filename, **kwargs):
        mutate(kwargs["format"])
        return native(filename, **kwargs)
    fig.canvas.print_figure = print_figure
elif hook == "switch":
    native = fig.canvas._switch_canvas_and_return_print_method
    @contextmanager
    @functools.wraps(native)
    def switch(fmt, backend=None):
        mutate(fmt)
        with native(fmt, backend) as method:
            yield method
    fig.canvas._switch_canvas_and_return_print_method = switch
elif hook in {"print_pdf", "print_svg"}:
    (FigureCanvasPdf if hook == "print_pdf" else FigureCanvasSVG)(fig)
    native = getattr(fig.canvas, hook)
    def print_format(filename, **kwargs):
        mutate(hook.removeprefix("print_"))
        # The stock dispatcher deliberately leaves third-party kwargs untouched.
        supported = inspect.signature(native).parameters
        try:
            return native(filename, **{key: value for key, value in kwargs.items() if key in supported})
        finally:
            title.set_text("Export title")
    setattr(fig.canvas, hook, print_format)
elif hook == "draw":
    native = fig.canvas.draw
    def draw():
        mutate("draw")
        return native()
    fig.canvas.draw = draw
    # Prove this is a real draw hook, then reset before the export under test.
    fig.canvas.draw()
    assert calls == ["draw"] and title.get_text() == "Canvas DRAW"
    calls.clear()
    title.set_text("Export title")
elif hook == "registered":
    class RegisteredCanvas(FigureCanvasSVG):
        def __init__(self, figure):
            calls.append("constructor")
            super().__init__(figure)
        def print_svg(self, filename, **kwargs):
            mutate("svg")
            supported = inspect.signature(super().print_svg).parameters
            return super().print_svg(filename, **{key: value for key, value in kwargs.items() if key in supported})
    backend_bases.register_backend("svg", RegisteredCanvas)
elif hook == "registered_module":
    module = out / "custom_svg_backend.py"
    module.write_text(
        "from matplotlib.backends.backend_svg import FigureCanvasSVG\n"
        "import __main__ as probe\n"
        "probe.calls.append('import')\n"
        "class FigureCanvas(FigureCanvasSVG):\n"
        "    def __init__(self, figure):\n"
        "        probe.calls.append('constructor')\n"
        "        super().__init__(figure)\n"
        "    def print_svg(self, filename, *, bbox_inches_restore=None, metadata=None, **kwargs):\n"
        "        probe.mutate('svg')\n"
        "        return super().print_svg(filename, bbox_inches_restore=bbox_inches_restore, metadata=metadata)\n",
        encoding="utf-8")
    sys.path.insert(0, str(out))
    backend_bases.register_backend("svg", "custom_svg_backend")
elif hook == "native_partialmethod":
    # Cairo's native print_pdf/svg use this descriptor form; exercise dispatch
    # with the installed SVG writer, without requiring an optional Cairo package.
    FigureCanvasSVG.print_svg = functools.partialmethod(FigureCanvasSVG.print_svg)
elif hook.startswith("native_"):
    {"native_base": backend_bases.FigureCanvasBase, "native_agg": FigureCanvasAgg,
     "native_pdf": FigureCanvasPdf, "native_svg": FigureCanvasSVG}[hook](fig)
else:
    raise AssertionError(hook)
canvas = fig.canvas
def observe_savefig(frame, event, value):
    if event == "call" and frame.f_code is real_savefig.__func__.__code__:
        saves.append(frame.f_locals["kwargs"]["format"])

sys.setprofile(observe_savefig)
pdf, svg = out / "pair.pdf", out / "pair.svg"
if mode == "native":
    # This deliberately bypasses pair eligibility to witness the native content.
    with pkg.figsession.export_font_context("pdf"):
        fig.savefig(pdf, format="pdf")
    with pkg.figsession.export_font_context("svg"):
        fig.savefig(svg, format="svg")
    result = {}
elif mode == "standalone":
    result = session.do_export("F", patches, str(svg), "svg")
else:
    result = pkg.wireproto.respond(pkg.wireproto.V1Handler(session), {
        "protocol_version": 1, "request_id": "canvas-pair", "cmd": "export", "stem": "F",
        "payload": {"patches": patches, "path": str(pdf), "format": "pdf", "svg_path": str(svg),
            "require_built": True, "snapshot_only": mode == "snapshot"}})
assert fig.canvas is canvas
if mode != "native":
    assert title.get_text() == "Export title"
    assert session.snapshot("F") == []
print(json.dumps({"result": result, "calls": calls, "saves": saves}))
"""


def _canvas_probe(tmp_path, mode, hook):
    tmp_path.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [USER_PYTHON, "-c", CANVAS_PROBE, str(pool.WORKER_PY.parent), str(tmp_path), mode, hook],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env({"MPLCONFIGDIR": str(tmp_path / "mpl")}),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


CUSTOM_CANVASES = [
    "savefig",
    "savefig_descriptor",
    "class_savefig",
    "class_print_figure",
    "backend_constructor",
    "backend_new",
    "subclass",
    "print_figure",
    "switch",
    "print_pdf",
    "print_svg",
    "registered",
    "registered_module",
]


def _pdf_title(path):
    with pymupdf.open(path) as pdf:
        text = pdf[0].get_text()
    return next(label for label in ("Canvas PDF", "Canvas SVG", "Export title") if label in text)


def _svg_title(path):
    data = path.read_text(encoding="utf-8")
    root = ET.fromstring(data)
    assert root.findall(".//{http://www.w3.org/2000/svg}path")
    assert not root.findall(".//{http://www.w3.org/2000/svg}text")
    # Inspect referenced glyph outlines, not the writer's informational text comments.
    path_ids = {path.get("id") for path in root.findall(".//{http://www.w3.org/2000/svg}path")}
    characters = []
    for use in root.findall(".//{http://www.w3.org/2000/svg}use"):
        glyph = use.get("{http://www.w3.org/1999/xlink}href", "").removeprefix("#")
        _, separator, character = glyph.rpartition("-")
        if separator and character and all(c in "0123456789abcdef" for c in character):
            assert glyph in path_ids
            characters.append(chr(int(character, 16)))
    text = "".join(characters)
    return next(label for label in ("Canvas PDF", "Canvas SVG", "Export title") if label in text)


@needs_user_python
@pytest.mark.parametrize("hook", CUSTOM_CANVASES)
def test_canvas_hooks_really_change_pdf_and_svg_content(tmp_path, hook):
    result = _canvas_probe(tmp_path, "native", hook)
    assert result["saves"] == ["pdf", "svg"]
    assert result["calls"]
    assert _pdf_title(tmp_path / "pair.pdf") != _svg_title(tmp_path / "pair.svg")


@needs_user_python
@pytest.mark.parametrize("hook", [*CUSTOM_CANVASES, "draw"])
def test_automatic_pair_rejects_custom_canvas_before_any_serialization(tmp_path, hook):
    result = _canvas_probe(tmp_path, "snapshot", hook)
    assert result["result"]["ok"] is False
    assert result["result"]["error"]["code"] == "svg_source_unavailable"
    assert result["calls"] == result["saves"] == []
    assert not (tmp_path / "pair.pdf").exists()
    assert not (tmp_path / "pair.svg").exists()


@needs_user_python
@pytest.mark.parametrize("hook", [*CUSTOM_CANVASES, "draw"])
def test_custom_canvas_pair_preserves_canonical_pdf(tmp_path, hook):
    result = _canvas_probe(tmp_path / "paired", "pair", hook)
    assert result["result"]["ok"] is True
    assert "svg" not in result["result"]
    assert result["result"]["svg_error"]["code"] == "svg_export_failed"
    assert result["saves"] == ["pdf"]
    assert not (tmp_path / "paired" / "pair.svg").exists()
    _canvas_probe(tmp_path / "native", "native", hook)
    assert _pdf_title(tmp_path / "paired" / "pair.pdf") == _pdf_title(
        tmp_path / "native" / "pair.pdf"
    )


@needs_user_python
@pytest.mark.parametrize("hook", [*CUSTOM_CANVASES, "draw"])
def test_standalone_svg_keeps_custom_canvas_native_behavior(tmp_path, hook):
    result = _canvas_probe(tmp_path / "standalone", "standalone", hook)
    assert "svg_error" not in result["result"]
    assert result["saves"] == ["svg"]
    assert not (tmp_path / "standalone" / "pair.pdf").exists()
    _canvas_probe(tmp_path / "native", "native", hook)
    assert _svg_title(tmp_path / "standalone" / "pair.svg") == _svg_title(
        tmp_path / "native" / "pair.svg"
    )


@needs_user_python
@pytest.mark.parametrize(
    "hook", ["native_base", "native_agg", "native_pdf", "native_svg", "native_partialmethod"]
)
@pytest.mark.parametrize("mode", ["pair", "snapshot"])
def test_standard_canvases_still_export_verified_pairs(tmp_path, mode, hook):
    result = _canvas_probe(tmp_path, mode, hook)
    assert result["result"]["ok"] is True
    assert "svg" in result["result"]
    assert "svg_error" not in result["result"]
    assert result["saves"] == ["pdf", "svg"]
    assert _pdf_title(tmp_path / "pair.pdf") == _svg_title(tmp_path / "pair.svg") == "Export title"
