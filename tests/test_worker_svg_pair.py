"""Full-quality PDF/SVG pairs share one owner-thread override snapshot."""

import hashlib
import json
import subprocess
import threading
import xml.etree.ElementTree as ET

import pymupdf
import pytest

from support.bridgekit import USER_PYTHON, child_env, needs_user_python
from tavotto.engine import bridge, nativesession, pool


def test_peek_selects_the_existing_artifact_session_without_building(monkeypatch, tmp_path):
    selected = {"bytes_sha256": "selected-artifact"}
    seen = []
    worker = type("ExistingWorker", (), {"alive": lambda self: True})()

    def key(root, script, artifact_source, run):
        seen.append((root, script, artifact_source, run))
        return "selected-session" if artifact_source is selected else "other-session"

    monkeypatch.setattr(pool, "_worker_key", key)
    monkeypatch.setattr(pool, "_workers", {"selected-session": worker})
    assert pool.peek("figure.py", tmp_path, "run", artifact_source=selected) is worker
    assert pool.peek("figure.py", tmp_path, "run") is None
    assert seen == [
        (tmp_path, "figure.py", selected, "run"),
        (tmp_path, "figure.py", None, "run"),
    ]


@pytest.mark.parametrize(
    "worker_type",
    [pool.EngineWorker, pool.WorkerdWorker, nativesession.NativeSession, bridge.BridgeSession],
)
@pytest.mark.parametrize("paired", [False, True])
@pytest.mark.parametrize("restricted", [False, True])
def test_export_transports_keep_the_pair_in_one_request(worker_type, paired, restricted):
    """Every parent-side control plane sends one command; absence is byte-compatible."""
    worker = object.__new__(worker_type)
    worker.built = True
    worker._figure_lock = threading.RLock()
    worker._require_consistent = lambda stem: None
    worker._note_temporary = lambda stem, response: None
    sent = []

    def request(payload, timeout):
        sent.append(payload)
        return {"path": "out.pdf"}

    def call(cmd, timeout, *, stem, payload):
        return request({"cmd": cmd, "stem": stem, **payload}, timeout)

    worker.request = worker._request = request
    worker._call = call
    kwargs = {"svg_path": "out.svg"} if paired else {}
    if restricted:
        kwargs.update(require_built=True, snapshot_only=True)
    worker.export("F", [], "out.pdf", dpi=300, **kwargs)
    expected = {
        "cmd": "export",
        "stem": "F",
        "patches": [],
        "path": "out.pdf",
        "format": "pdf",
        "dpi": 300,
    }
    if paired:
        expected["svg_path"] = "out.svg"
    if restricted:
        expected.update(require_built=True, snapshot_only=True)
    assert sent == [expected]


PROBE = r"""
import hashlib, importlib.util, json, sys
from pathlib import Path
engine, directory, mode = sys.argv[1:]
windows_newlines = mode.endswith("_newlines")
if windows_newlines:
    mode = mode.removesuffix("_newlines")
spec = importlib.util.spec_from_file_location("boot", Path(engine) / "bridgeboot.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
pkg = boot.load_engine_modules(engine, ("figcapture", "patchspec", "pathgeom", "axestraversal",
    "spinemodel", "tickmodel", "colorbarmodel", "legendmodel", "overrides", "manifest",
    "preview_hybrid", "figsession", "wireproto"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.transforms import Bbox

out = Path(directory)
fig, ax = plt.subplots(figsize=(3, 2), dpi=100, layout=mode if mode in {"tight", "constrained"} else None)
ax.plot([0, 1, 2], [0, 1, 0])
ax.imshow([[0., 1.], [1., 0.]], extent=(.2, .8, .2, .8))
if mode == "collections":
    ax.errorbar([0, 1], [0, 1], yerr=[.1, .2], capsize=2, label="Errors")
    ax.legend()
    assert any(artist.get_path_effects() is None for artist in fig.findobj())
title = ax.set_title("Script title")
pkg.pathgeom.set_frame(fig, Bbox.from_bounds(-.3, -.2, 4, 3))
session = pkg.figsession.LiveFigureSession(out / "preview", preview_dpi=40)
session.add_figure("F", fig, pkg.figcapture.SOURCE_PYPLOT)
session.instrument_all()
assert "F" in session.states
hot = [{"gid": title.get_gid(), "prop": "text", "value": "Hot title"}]
pkg.overrides.apply(session.states["F"], hot)
previous = session.snapshot("F")
patches = [{"gid": title.get_gid(), "prop": "text", "value": "Export title"}]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["svg.image_inline"] = False
matplotlib.rcParams["pdf.fonttype"] = 3
matplotlib.rcParams["ps.fonttype"] = 3
before_rc = dict(matplotlib.rcParams)
before_dpi = fig.dpi
before_size = list(fig.get_size_inches())
before_frame = list(pkg.pathgeom.frame_bbox(fig).bounds)
before_layout = fig.get_layout_engine()
calls, saves = [], []
real_apply = pkg.overrides.apply
real_savefig = fig.savefig

def apply(state, values):
    calls.append(values)
    return real_apply(state, values)

def observe_savefig(frame, event, value):
    # Observe the real serializer without replacing the method pair eligibility checks.
    if frame.f_code is not real_savefig.__func__.__code__:
        return
    kw = frame.f_locals["kwargs"]
    if event == "call":
        path = frame.f_locals["fname"]
        if windows_newlines and kw["format"] == "svg":
            assert isinstance(path, io.TextIOBase) and not path.closed
            assert path.encoding.lower().replace("-", "") == "utf8"
        saves.append({"format": kw["format"], "title": title.get_text(), "dpi": kw["dpi"],
                      "frame": list(kw["bbox_inches"].bounds), "fonttype": matplotlib.rcParams["svg.fonttype"],
                      "pdf_fonttype": matplotlib.rcParams["pdf.fonttype"]})
    elif event == "return":
        saves[-1]["axes_after"] = list(ax.get_position().bounds)
        if mode == "failure" and kw["format"] == "svg":
            raise RuntimeError("companion save failed")

def forbid_preview(*args, **kw):
    raise AssertionError("full-quality export used preview_hybrid")

pkg.overrides.apply = apply
pkg.preview_hybrid.save_preview_svg = forbid_preview
sys.setprofile(observe_savefig)
pdf, svg = out / "pair.pdf", out / "pair.svg"
svg_opens = []
if windows_newlines:
    import builtins, io, os
    def windows_open(original):
        def open_file(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
            if isinstance(file, (str, os.PathLike)) and Path(file) == svg and "w" in mode and "b" not in mode:
                svg_opens.append({"encoding": encoding, "newline": newline})
                if newline is None:
                    newline = "\r\n"
            return original(file, mode, buffering, encoding, errors, newline, closefd, opener)
        return open_file
    builtins.open = windows_open(builtins.open)
    io.open = windows_open(io.open)
    # Python 3.10's pathlib accessor captures io.open before these patches.
    def path_open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        return io.open(self, mode, buffering, encoding, errors, newline)
    Path.open = path_open
if mode in {"callback", "snapshot_callback"}:
    def change_after_draw(event):
        title.set_text("Draw mutation")
    fig.canvas.mpl_connect("draw_event", change_after_draw)
elif mode in {"custom_draw", "snapshot_custom"}:
    line = ax.lines[0]
    native_draw = line.draw
    def custom_draw(renderer):
        title.set_text("Custom draw mutation")
        native_draw(renderer)
    line.draw = custom_draw
elif mode in {"formatter", "snapshot_formatter", "standalone_formatter"}:
    from matplotlib.ticker import FuncFormatter
    counter = [0]
    def changing_formatter(value, pos):
        counter[0] += 1
        return str(counter[0])
    ax.xaxis.set_major_formatter(FuncFormatter(changing_formatter))
elif mode == "standalone_artist":
    from matplotlib.artist import Artist
    from matplotlib.path import Path as MplPath
    counter = [0]
    class CustomArtist(Artist):
        def draw(self, renderer):
            counter[0] += 1
            gc = renderer.new_gc()
            try:
                gc.set_foreground("magenta")
                renderer.draw_path(gc, MplPath([[.2, .2], [.8, .7]]), ax.transData)
            finally:
                gc.restore()
    ax.add_artist(CustomArtist())
elif mode in {"locator", "snapshot_locator"}:
    from matplotlib.ticker import MaxNLocator
    counter = [0]
    class ChangingLocator(MaxNLocator):
        def __call__(self):
            counter[0] += 1
            return super().__call__()
    ax.xaxis.set_major_locator(ChangingLocator())
elif mode in {"scale", "snapshot_scale", "transform", "snapshot_transform"}:
    from matplotlib.scale import FuncTransform
    from matplotlib.transforms import blended_transform_factory
    counter = [0]
    def forward(values):
        counter[0] += 1
        return values
    def inverse(values):
        return values
    if mode.endswith("scale"):
        ax.set_xscale("function", functions=(forward, inverse))
    else:
        ax.lines[0].set_transform(blended_transform_factory(FuncTransform(forward, inverse), ax.transData))
    counter[0] = 0
elif mode in {"annotation", "snapshot_annotation"}:
    counter = [0]
    def coords(renderer):
        counter[0] += 1
        return ax.transAxes
    ax.annotate("Callback", (.5, .5), xycoords=coords)
elif mode in {"effect", "snapshot_effect"}:
    from matplotlib.patheffects import AbstractPathEffect
    counter = [0]
    class ChangingEffect(AbstractPathEffect):
        def draw_path(self, renderer, gc, tpath, affine, rgbFace=None):
            counter[0] += 1
            renderer.draw_path(gc, tpath, affine, rgbFace)
    ax.lines[0].set_path_effects([ChangingEffect()])
elif mode == "annotations":
    from matplotlib.patheffects import Stroke, Normal
    ax.annotate("Standard", (.5, .5), xycoords="axes fraction")
    ax.lines[0].set_path_effects([Stroke(linewidth=2, foreground="black"), Normal()])
elif mode == "fixed_labels":
    ax.set_xticks([0, 1], ["zero", "one"])
elif mode == "mkdir_failure":
    (out / "blocker").write_text("not a directory")
    svg = out / "blocker" / "pair.svg"
result = None
if mode == "invalid":
    handler = pkg.wireproto.V1Handler(session)
    for i, (value, fmt) in enumerate([("", "pdf"), (True, "pdf"), (str(svg), "png"), (str(pdf), "pdf")]):
        response = pkg.wireproto.respond(handler, {"protocol_version": 1, "request_id": str(i),
            "cmd": "export", "stem": "F", "payload": {"path": str(pdf), "svg_path": value, "format": fmt}})
        assert not response["ok"] and response["error"]["code"] == "bad_request", response
    text_svg = out / "t.svg"
    for i, bad in enumerate([
        {"format": "pdf", "svg_fonttype": "none"},
        {"format": "svg", "svg_fonttype": "bold"},
        {"format": "pdf", "svg_text_path": str(text_svg)},
        {"format": "pdf", "svg_path": str(svg), "svg_text_path": ""},
        {"format": "pdf", "svg_path": str(svg), "svg_text_path": str(svg)},
        {"format": "pdf", "svg_path": str(svg), "svg_text_path": str(pdf)},
    ]):
        response = pkg.wireproto.respond(handler, {"protocol_version": 1, "request_id": f"t{i}",
            "cmd": "export", "stem": "F", "payload": {"path": str(pdf), **bad}})
        assert not response["ok"] and response["error"]["code"] == "bad_request", (bad, response)
    assert not calls and not saves
elif mode == "fonttype_none":
    result = session.do_export("F", patches, str(svg), "svg", 300, svg_fonttype="none")
    assert calls == [patches, previous]
elif mode.startswith("standalone"):
    result = session.do_export("F", patches, str(svg), "svg", 300)
    assert calls == [patches, previous]
    assert "svg" not in result
    if mode != "standalone":
        assert counter[0] > 0
else:
    handler = pkg.wireproto.V1Handler(session)
    response = pkg.wireproto.respond(handler, {"protocol_version": 1, "request_id": "paired",
        "worker_generation": 7, "render_revision": 12,
        "cmd": "export", "stem": "F", "payload": {"patches": patches, "path": str(pdf),
        "format": "pdf", "dpi": 300, "svg_path": str(svg),
        "require_built": True, "snapshot_only": mode.startswith("snapshot_"),
        **({"svg_text_path": str(out / "pair.text.svg")} if mode == "pair_text" else {})}})
    if mode.startswith("snapshot_") and mode != "snapshot_ok":
        assert not response["ok"] and response["error"]["code"] == "svg_source_unavailable", response
        assert response["error"]["retryable"] is False
        assert not saves and not pdf.exists() and not svg.exists()
    else:
        assert response["ok"], response
        assert response["worker_generation"] == 7 and response["render_revision"] == 12
        assert response["unrestored"] == 0
    if mode in {"failure", "mkdir_failure", "callback", "custom_draw", "formatter", "locator", "scale", "transform", "annotation", "effect"}:
        assert "svg" not in response and response["svg_error"]["code"] == "svg_export_failed", response
        assert pdf.is_file() and not svg.is_file()
    result = response
    assert calls == [patches, previous], calls
if mode in {"snapshot_formatter", "snapshot_locator", "snapshot_scale", "snapshot_transform", "snapshot_annotation", "snapshot_effect"}:
    assert counter[0] == 0
assert session.snapshot("F") == previous
assert title.get_text() == "Hot title"
assert dict(matplotlib.rcParams) == before_rc
assert fig.dpi == before_dpi and list(fig.get_size_inches()) == before_size
assert list(pkg.pathgeom.frame_bbox(fig).bounds) == before_frame
assert fig.get_layout_engine() is before_layout
print(json.dumps({"result": result, "saves": saves, "svg_opens": svg_opens}))
"""


@pytest.fixture(scope="module", autouse=True)
def _shared_mplconfig(tmp_path_factory):
    # One MPLCONFIGDIR per module: a fresh one makes every probe rebuild Matplotlib's
    # font cache (~12 s per subprocess on macOS), which pushed a backend-platforms
    # shard past its timeout. The cache only indexes installed fonts, so sharing it
    # changes no font resolution.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("MPLCONFIGDIR", str(tmp_path_factory.mktemp("mpl")))
        yield


def _probe(tmp_path, mode):
    completed = subprocess.run(
        [USER_PYTHON, "-c", PROBE, str(pool.WORKER_PY.parent), str(tmp_path), mode],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


@needs_user_python
@pytest.mark.parametrize(
    "mode",
    ["pair", "tight", "constrained", "fixed_labels", "annotations", "collections", "snapshot_ok"],
)
def test_paired_export_keeps_frame_full_quality_and_state(tmp_path, mode):
    result = _probe(tmp_path, mode)
    assert [save["format"] for save in result["saves"]] == ["pdf", "svg"]
    assert {save["title"] for save in result["saves"]} == {"Export title"}
    assert {save["dpi"] for save in result["saves"]} == {300}
    assert result["saves"][0]["frame"] == result["saves"][1]["frame"]
    assert result["saves"][0]["axes_after"] == result["saves"][1]["axes_after"]
    assert result["saves"][1]["fonttype"] == "path"
    assert result["saves"][0]["pdf_fonttype"] == (3 if mode == "snapshot_ok" else 42)
    svg = tmp_path / "pair.svg"
    data = svg.read_bytes()
    assert result["result"]["svg"] == {
        "path": str(svg),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    root = ET.fromstring(data)
    assert not root.findall(".//{http://www.w3.org/2000/svg}text")
    assert root.findall(".//{http://www.w3.org/2000/svg}path")
    assert root.findall(".//{http://www.w3.org/2000/svg}image")
    assert [float(root.attrib[key][:-2]) for key in ("width", "height")] == [288, 216]
    with pymupdf.open(tmp_path / "pair.pdf") as pdf:
        assert tuple(pdf[0].rect) == (0, 0, 288, 216)
        assert "Export title" in pdf[0].get_text()
        assert "Hot title" not in pdf[0].get_text()


@needs_user_python
@pytest.mark.parametrize("mode", ["failure", "mkdir_failure"])
def test_svg_failure_preserves_pdf_and_restores_state(tmp_path, mode):
    result = _probe(tmp_path, mode)
    assert result["result"]["svg_error"]["code"] == "svg_export_failed"
    with pymupdf.open(tmp_path / "pair.pdf") as pdf:
        assert "Export title" in pdf[0].get_text()


@needs_user_python
@pytest.mark.parametrize(
    "mode",
    [
        "callback",
        "custom_draw",
        "formatter",
        "locator",
        "scale",
        "transform",
        "annotation",
        "effect",
    ],
)
def test_dynamic_draw_is_not_certified_as_a_paired_snapshot(tmp_path, mode):
    result = _probe(tmp_path, mode)
    assert [save["format"] for save in result["saves"]] == ["pdf"]
    assert "svg" not in result["result"]


@needs_user_python
def test_standalone_svg_uses_temporary_path_fonts(tmp_path):
    result = _probe(tmp_path, "standalone")
    assert [save["format"] for save in result["saves"]] == ["svg"]
    assert result["saves"][0]["fonttype"] == "path"
    root = ET.fromstring((tmp_path / "pair.svg").read_bytes())
    assert not root.findall(".//{http://www.w3.org/2000/svg}text")


@needs_user_python
@pytest.mark.parametrize("mode", ["standalone_formatter", "standalone_artist"])
def test_standalone_svg_accepts_custom_draw_once_and_restores_state(tmp_path, mode):
    result = _probe(tmp_path, mode)
    assert [save["format"] for save in result["saves"]] == ["svg"]
    assert result["saves"][0]["fonttype"] == "path"
    assert result["saves"][0]["title"] == "Export title"
    assert "svg_error" not in result["result"]
    assert not (tmp_path / "pair.pdf").exists()
    data = (tmp_path / "pair.svg").read_bytes()
    root = ET.fromstring(data)
    assert not root.findall(".//{http://www.w3.org/2000/svg}text")
    assert root.findall(".//{http://www.w3.org/2000/svg}path")
    if mode == "standalone_artist":
        assert b"#ff00ff" in data


@needs_user_python
@pytest.mark.parametrize("mode", ["pair", "standalone"])
def test_export_svg_bytes_do_not_translate_newlines(tmp_path, mode):
    """Emulate Windows text defaults in the child so missing newline='' also fails on POSIX."""
    result = _probe(tmp_path, f"{mode}_newlines")
    data = (tmp_path / "pair.svg").read_bytes()
    assert b"\n" in data and b"\r\n" not in data
    assert result["svg_opens"] == [{"encoding": "utf-8", "newline": ""}]
    assert [save["format"] for save in result["saves"]] == (
        ["pdf", "svg"] if mode == "pair" else ["svg"]
    )
    if mode == "pair":
        assert result["result"]["svg"]["size_bytes"] == len(data)
        assert result["result"]["svg"]["sha256"] == hashlib.sha256(data).hexdigest()


@needs_user_python
def test_bad_pair_requests_fail_before_touching_figure(tmp_path):
    _probe(tmp_path, "invalid")


@needs_user_python
@pytest.mark.parametrize("legacy", [False, True])
def test_real_safe_worker_exports_pair_without_running_script_again(tmp_path, legacy):
    from test_worker_roundtrip import _rpc, _spawn

    figures = tmp_path / "figures"
    figures.mkdir()
    script = figures / "pair.py"
    script.write_text(
        "import matplotlib.pyplot as plt\n"
        'plt.rcParams["svg.fonttype"] = "none"\n'
        'with open("runs.txt", "a") as stream:\n    stream.write("run\\n")\n'
        "fig, ax = plt.subplots(figsize=(3, 2))\n"
        "ax.plot([0, 1], [0, 1])\n"
        'ax.set_title("Paired worker")\n'
        'fig.savefig("F.pdf")\n',
        encoding="utf-8",
    )
    proc = _spawn(script, figures, tmp_path, entry="__main__")
    try:
        _rpc(proc, {"cmd": "build"})
        payload = {
            "patches": [],
            "path": str(tmp_path / "pair.pdf"),
            "format": "pdf",
            "dpi": 300,
            "svg_path": str(tmp_path / "pair.svg"),
        }
        request = {"cmd": "export", "stem": "F"}
        if legacy:
            request.update(payload)
        else:
            request.update(protocol_version=1, request_id="pair", payload=payload)
        response = _rpc(proc, request)
        data = (tmp_path / "pair.svg").read_bytes()
        assert response["svg"]["sha256"] == hashlib.sha256(data).hexdigest()
        assert (tmp_path / "sandbox" / "runs.txt").read_text(encoding="utf-8") == "run\n"
        assert not ET.fromstring(data).findall(".//{http://www.w3.org/2000/svg}text")
        with pymupdf.open(tmp_path / "pair.pdf") as pdf:
            assert "Paired worker" in pdf[0].get_text()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


@pytest.mark.parametrize("worker_type", [pool.EngineWorker, pool.WorkerdWorker])
@pytest.mark.parametrize("flag", ["require_built", "snapshot_only"])
def test_capture_export_never_builds_a_cold_parent_worker(worker_type, flag):
    worker = object.__new__(worker_type)
    worker.built = False

    def forbid_build():
        raise AssertionError("capture tried to build")

    worker.ensure_built = forbid_build
    with pytest.raises(pool.WorkerError) as error:
        worker.export("F", [], "pair.pdf", svg_path="pair.svg", **{flag: True})
    assert error.value.code == "svg_source_unavailable"


@pytest.mark.parametrize("flag", ["require_built", "snapshot_only"])
def test_capture_export_never_reopens_expired_workerd_session(monkeypatch, tmp_path, flag):
    from tavotto.engine import workerd_client
    from test_workerd_pool import _worker

    worker, client = _worker(
        monkeypatch,
        tmp_path,
        [
            {"ok": True, "session_id": "existing"},
            workerd_client.WorkerdError("expired", code="unknown_session"),
        ],
    )
    worker.built = True
    with pytest.raises(pool.WorkerError) as error:
        worker.export("F", [], "pair.pdf", svg_path="pair.svg", **{flag: True})
    assert error.value.code == "svg_source_unavailable"
    assert [op for op, _ in client.calls] == ["open_session", "export"]
    assert not worker.alive()


@needs_user_python
@pytest.mark.parametrize(
    "mode",
    [
        "snapshot_callback",
        "snapshot_custom",
        "snapshot_formatter",
        "snapshot_locator",
        "snapshot_scale",
        "snapshot_transform",
        "snapshot_annotation",
        "snapshot_effect",
    ],
)
def test_automatic_snapshot_declines_dynamic_draw_without_drawing(tmp_path, mode):
    result = _probe(tmp_path, mode)
    assert result["saves"] == []


@needs_user_python
@pytest.mark.parametrize("legacy", [False, True])
def test_cold_execution_worker_never_runs_script_for_capture(tmp_path, legacy):
    from test_worker_roundtrip import _spawn

    figures = tmp_path / "figures"
    figures.mkdir()
    script = figures / "cold.py"
    script.write_text(
        'open("SCRIPT_RAN", "w").write("ran")\nraise RuntimeError("Must not run")\n',
        encoding="utf-8",
    )
    proc = _spawn(script, figures, tmp_path, entry="__main__")
    try:
        payload = {
            "path": str(tmp_path / "pair.pdf"),
            "svg_path": str(tmp_path / "pair.svg"),
            "require_built": True,
        }
        request = {"cmd": "export", "stem": "F"}
        if legacy:
            request.update(payload)
        else:
            request.update(protocol_version=1, request_id="cold", payload=payload)
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        lines = []
        reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout=30)
        assert not reader.is_alive() and lines and lines[0]
        response = json.loads(lines[0])
        assert response["ok"] is False
        assert (
            response.get("code") if legacy else response["error"]["code"]
        ) == "svg_source_unavailable"
        assert not (tmp_path / "sandbox" / "SCRIPT_RAN").exists()
        assert not (tmp_path / "pair.pdf").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


@needs_user_python
def test_text_variant_is_a_separate_serialization_and_the_path_baseline_is_untouched(tmp_path):
    """ADR 0144：`svg_text_path` 另写一份 `svg.fonttype=none`；path 基准字节（#1185 等价证人）仍是 path。"""
    result = _probe(tmp_path, "pair_text")
    assert [save["format"] for save in result["saves"]] == ["pdf", "svg", "svg"]
    assert [save["fonttype"] for save in result["saves"][1:]] == ["path", "none"]
    # 同一份布局：三次保存之后坐标轴位置、取景框一字不差
    assert len({tuple(save["axes_after"]) for save in result["saves"]}) == 1
    assert len({tuple(save["frame"]) for save in result["saves"]}) == 1
    baseline = (tmp_path / "pair.svg").read_bytes()
    text = (tmp_path / "pair.text.svg").read_bytes()
    ns = "{http://www.w3.org/2000/svg}text"
    assert not ET.fromstring(baseline).findall(f".//{ns}")
    texts = ET.fromstring(text).findall(f".//{ns}")
    assert any("Export title" in "".join(t.itertext()) for t in texts)
    assert result["result"]["svg"]["sha256"] == hashlib.sha256(baseline).hexdigest()
    assert result["result"]["svg_text"] == {
        "path": str(tmp_path / "pair.text.svg"),
        "sha256": hashlib.sha256(text).hexdigest(),
        "size_bytes": len(text),
    }


@needs_user_python
def test_standalone_svg_can_keep_text_without_touching_the_default(tmp_path):
    result = _probe(tmp_path, "fonttype_none")
    assert [save["fonttype"] for save in result["saves"]] == ["none"]
    root = ET.fromstring((tmp_path / "pair.svg").read_bytes())
    assert any(
        "Export title" in "".join(t.itertext())
        for t in root.findall(".//{http://www.w3.org/2000/svg}text")
    )
    # the plain standalone export (no field) stays outlines: old callers are unaffected
    (tmp_path / "plain").mkdir()
    plain = _probe(tmp_path / "plain", "standalone")
    assert [save["fonttype"] for save in plain["saves"]] == ["path"]
