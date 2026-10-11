"""独立流程图框的原生宽高：真实几何、可逆性、坐标边界与坏值的原子性。

科研原稿不进夹具。这里的匿名矩形与文字由测试自己构造；图形库只在 worker 解释器中
导入。断言直接量 matplotlib 的路径外框 / 原生 bounds / PNG 字节，不只检查请求值回显。
写回、冷重放与项目保存重开另由等价矩阵和 runtime 集成用例覆盖。
"""

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
import hashlib
import io
import json
import sys

sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle
from matplotlib.transforms import Affine2D, Bbox, IdentityTransform
import manifest
import overrides
import pathgeom

mode, kind, arg = json.loads(sys.argv[2])
fig = plt.figure(figsize=(5, 4), dpi=100, layout=arg if mode == "layout" else None)
if kind == "subfigure":
    left, right = fig.subfigures(1, 2)
    ax = right.subplots()
else:
    ax = fig.subplots()
ax.set(xlim=(0, 10), ylim=(0, 10))
ax.set_axis_off()
text = ax.text(4, 5, "Step A", fontsize=11, color="#234567")
if kind == "inverted":
    ax.invert_xaxis()
    ax.invert_yaxis()
if kind == "log":
    ax.set_xscale("log")
    ax.set_xlim(1, 10)

class PlainBox(FancyBboxPatch):
    pass

class DynamicBox(FancyBboxPatch):
    def draw(self, renderer):
        super().draw(renderer)

transform = ax.transData
origin, dimensions, pad, radius = (2, 3), (3.5, 2.2), 0.2, 0.3
if kind in ("axes", "figure"):
    transform = ax.transAxes if kind == "axes" else fig.transFigure
    origin, dimensions, pad, radius = (.2, .3), (.35, .22), .02, .03
if kind == "shear":
    transform = Affine2D().skew_deg(12, 0) + ax.transData
if kind == "pixels":
    transform = IdentityTransform()
if kind == "pixel_offset":
    transform = ax.transData + Affine2D().translate(10, 20)
if kind in ("rectangle", "negative", "rotated", "clipped"):
    if kind == "negative":
        origin, dimensions = (6, 7), (-3.5, -2.2)
    elif kind == "clipped":
        origin, dimensions = (-2, 3), (6, 3)
    patch = Rectangle(origin, *dimensions, angle=18 if kind == "rotated" else 0,
                      transform=transform)
elif kind == "circle":
    patch = Circle((4, 4), 2)
elif kind == "bar":
    patch = ax.bar([4], [6], width=1)[0]
else:
    cls = PlainBox if kind == "inherited" else DynamicBox if kind == "dynamic" else FancyBboxPatch
    boxstyle = f"round,pad={pad},rounding_size={radius}"
    if kind == "square":
        boxstyle = f"square,pad={pad}"
    elif kind == "round4":
        boxstyle = f"round4,pad={pad}"
    patch = cls(origin, *dimensions, boxstyle=boxstyle,
                mutation_scale=2 if kind == "aspect" else 1,
                mutation_aspect=1.4 if kind == "aspect" else 1,
                transform=transform)
if kind != "bar":
    ax.add_patch(patch)
patch.set(facecolor="#deedfd", edgecolor="#205080", linewidth=1.75,
          clip_on=kind == "clipped")
style = patch.get_boxstyle() if isinstance(patch, FancyBboxPatch) else None
style_values = vars(style).copy() if style else None
if kind == "frame":
    pathgeom.set_frame(fig, Bbox.from_bounds(.4, .3, 4.2, 3.4))
state = overrides.FigState(fig)
manifest.instrument(state)
gid = next(e["gid"] for e in state.elements if e["artist"] is patch)

def edit(prop, value, target=gid):
    return {"gid": target, "prop": prop, "value": value}

def snapshot():
    man = manifest.build_manifest(state, "Box")
    el = next(e for e in man["elements"] if e["gid"] == gid)
    with pathgeom.in_frame(fig):
        bb = patch.get_window_extent()
        width, height = fig.bbox.width, fig.bbox.height
        actual = [bb.x0 / width, 1 - bb.y1 / height, bb.width / width, bb.height / height]
    native = [float(v) for v in (patch.get_x(), patch.get_y(), patch.get_width(), patch.get_height())] if hasattr(patch, "get_x") else None
    return {"actual": actual, "native": native, "shape": el.get("shape_bounds"),
            "minimum": el.get("shape_min_size"), "anchor": el.get("anchor"),
            "bbox": el["bbox"], "resizable": el.get("resizable", False),
            "size": next((f for f in el["editable"] if f["prop"] == "size_frac"), None),
            "text": [*text.get_position(), text.get_fontsize(), text.get_color()],
            "stroke": patch.get_linewidth(),
            "style_unchanged": style is None or
                (patch.get_boxstyle() is style and vars(style) == style_values)}

def png():
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=120, **pathgeom.output_kwargs(fig))
    return hashlib.sha256(out.getvalue()).hexdigest()

base = snapshot()
pos = edit("pos_frac", [.18, .68])
size = edit("size_frac", [.37, .28])
both = [pos, size]
out = {"base": base}
if mode in ("resize", "layout"):
    out["warnings"] = overrides.apply(state, [pos]) + overrides.apply(state, both)
    out["changed"] = snapshot()
    out["repeated"] = []
    for dpi in (72, 180, 100):
        fig.set_dpi(dpi)
        out["warnings"] += overrides.apply(state, both)
        out["repeated"].append(snapshot())
    out["warnings"] += overrides.apply(state, [pos])
    out["without_size"] = snapshot()
    out["warnings"] += overrides.apply(state, both)
    out["warnings"] += overrides.apply(state, [size])
    out["without_position"] = snapshot()
    out["warnings"] += overrides.apply(state, [])
    out["restored"] = snapshot()
elif mode == "unsupported":
    before = png()
    out["warnings"] = overrides.apply(state, [size])
    out["changed"] = snapshot()
    out["pixels_unchanged"] = before == png()
elif mode == "invalid":
    out["initial_warnings"] = overrides.apply(state, both)
    out["before"] = snapshot()
    before = png()
    bad = arg
    if bad == "nan":
        bad = [float("nan"), .2]
    elif bad == "inf":
        bad = [.2, float("inf")]
    elif bad == "too_small":
        bad = [out["before"]["minimum"][0] / 2, .28]
    out["warnings"] = overrides.apply(state, [pos, edit("size_frac", bad)])
    out["after"] = snapshot()
    out["pixels_unchanged"] = before == png()
elif mode == "axes":
    out["warnings"] = overrides.apply(state, both)
    final = both + [edit("xlim", [0, 15], "axes_0"), edit("invert_y", True, "axes_0")]
    out["warnings"] += overrides.apply(state, final)
    out["changed"] = snapshot()
    out["warnings"] += overrides.apply(state, [])
    out["restored"] = snapshot()
elif mode == "transition":
    out["warnings"] = overrides.apply(state, both)
    unsupported = both + [edit("xscale", "log", "axes_0"), edit("xlim", [1, 10], "axes_0")]
    out["unsupported_warnings"] = overrides.apply(state, unsupported)
    out["unsupported"] = snapshot()
    out["warnings"] += overrides.apply(state, both)
    out["changed"] = snapshot()
elif mode == "minimum":
    small = edit("size_frac", [v * 1.1 for v in base["minimum"]])
    out["warnings"] = overrides.apply(state, [pos, small])
    axes = edit("position", [.025, .025, .95, .95], "axes_0")
    final = [pos, small, axes]
    out["unsupported_warnings"] = overrides.apply(state, final)
    out["unsupported"] = snapshot()
    before = png()
    out["warnings"] += overrides.apply(state, [])
    out["replay_warnings"] = overrides.apply(state, final)
    out["replay"] = snapshot()
    out["pixels_replay"] = before == png()
    out["warnings"] += overrides.apply(state, [pos, edit("size_frac", [.4, .3]), axes])
    out["changed"] = snapshot()
elif mode == "pixels":
    before = png()
    out["warnings"] = overrides.apply(state, both)
    out["pixels_changed"] = before != png()
    out["warnings"] += overrides.apply(state, [])
    out["pixels_restored"] = before == png()
print(json.dumps(out))
"""


def _probe(mode, kind="round", arg=None):
    proc = subprocess.run(  # noqa: S603 — 使用已选中的 worker 解释器运行本测试的固定脚本
        [WORKER_PY, "-c", PROBE, str(ENGINE_DIR), json.dumps([mode, kind, arg])],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _assert_geometry(snap, expected, tolerance=1e-9):
    assert snap["actual"] == pytest.approx(expected, abs=tolerance)
    assert snap["shape"] == pytest.approx(snap["actual"], abs=1e-12)
    assert snap["size"]["value"] == pytest.approx(expected[2:], abs=tolerance)
    assert snap["anchor"] == pytest.approx([expected[0], expected[1] + expected[3]], abs=tolerance)
    assert snap["resizable"] is False, "Patch 尺寸不能进入 axes.position 的 resizable 合同"
    assert snap["stroke"] == 1.75 and snap["style_unchanged"]


@pytest.mark.parametrize(
    "kind",
    [
        "rectangle",
        "negative",
        "round",
        "square",
        "aspect",
        "inherited",
        "inverted",
        "axes",
        "figure",
        "subfigure",
        "frame",
    ],
)
def test_resize_uses_real_outer_bounds_and_preserves_style_and_independent_undo(kind):
    got = _probe("resize", kind)
    assert not got["warnings"], got["warnings"]
    expected = [0.18, 0.4, 0.37, 0.28]
    for snap in [got["changed"], *got["repeated"]]:
        _assert_geometry(snap, expected)
        assert snap["text"] == got["base"]["text"], "调整图框不缩放或移动文字"
    base = got["base"]["actual"]
    _assert_geometry(got["without_size"], [0.18, 0.68 - base[3], *base[2:]])
    _assert_geometry(got["without_position"], [base[0], base[1] + base[3] - 0.28, 0.37, 0.28])
    _assert_geometry(got["restored"], base)
    assert got["restored"]["native"] == got["base"]["native"]


@pytest.mark.parametrize("layout", ["tight", "constrained"])
def test_layout_and_export_dpi_keep_size_and_position_pinned(layout):
    got = _probe("layout", arg=layout)
    assert not got["warnings"], got["warnings"]
    for snap in [got["changed"], *got["repeated"]]:
        _assert_geometry(snap, [0.18, 0.4, 0.37, 0.28], tolerance=3e-5)


def test_clipped_selection_box_does_not_shrink_resize_reference():
    got = _probe("resize", "clipped")
    assert not got["warnings"], got["warnings"]
    base = got["base"]
    assert base["bbox"][2] < base["shape"][2]
    assert base["bbox"][0] > base["shape"][0]
    _assert_geometry(got["changed"], [0.18, 0.4, 0.37, 0.28])


@pytest.mark.parametrize(
    "kind",
    ["circle", "bar", "rotated", "shear", "pixels", "pixel_offset", "round4", "dynamic", "log"],
)
def test_unsupported_geometry_never_advertises_or_applies_a_resize(kind):
    got = _probe("unsupported", kind)
    assert got["base"]["size"] is None and got["base"]["shape"] is None
    assert got["warnings"], "硬写一个不支持的尺寸必须明确拒绝"
    assert got["changed"]["actual"] == got["base"]["actual"]
    assert got["pixels_unchanged"]


@pytest.mark.parametrize(
    "value",
    [
        [0, 0.2],
        [-0.2, 0.3],
        [True, 0.3],
        [".2", 0.3],
        [0.2],
        [0.2, 0.3, 0.4],
        "12",
        None,
        "nan",
        "inf",
        "too_small",
        [1e308, 0.3],
    ],
)
def test_invalid_size_does_not_mutate_an_existing_resize_or_move(value):
    got = _probe("invalid", arg=value)
    assert not got["initial_warnings"]
    assert got["warnings"]
    assert got["after"] == got["before"]
    assert got["pixels_unchanged"]


def test_axis_limits_and_inversion_after_resize_recompute_current_geometry():
    got = _probe("axes")
    assert not got["warnings"], got["warnings"]
    _assert_geometry(got["changed"], [0.18, 0.4, 0.37, 0.28])
    _assert_geometry(got["restored"], got["base"]["actual"])


def test_switching_to_unsupported_axis_restores_native_size_and_can_resume():
    got = _probe("transition")
    assert got["unsupported_warnings"]
    assert got["unsupported"]["shape"] is None
    assert got["unsupported"]["native"] == got["base"]["native"]
    assert not got["warnings"], got["warnings"]
    _assert_geometry(got["changed"], [0.18, 0.4, 0.37, 0.28])


def test_axes_growth_past_corner_minimum_reports_and_replays_native_bounds_then_recovers():
    got = _probe("minimum")
    assert not got["warnings"], got["warnings"]
    assert got["unsupported_warnings"] and got["replay_warnings"]
    assert got["unsupported"]["native"] == got["base"]["native"]
    assert got["replay"]["actual"] == got["unsupported"]["actual"]
    assert got["pixels_replay"]
    _assert_geometry(got["changed"], [0.18, 0.38, 0.4, 0.3])


def test_resize_changes_real_pixels_and_undo_restores_the_original_bytes():
    got = _probe("pixels")
    assert not got["warnings"]
    assert got["pixels_changed"] and got["pixels_restored"]
