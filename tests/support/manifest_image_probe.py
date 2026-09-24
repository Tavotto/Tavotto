"""在 **worker 解释器**里跑的探针：「只为布局」的 draw 有没有把图片重采样（ADR 0079 之后的性能修复）。

`tests/` 跑在 Flask 的 .venv 里、import 不动 matplotlib；这里在 worker 那一侧如实报事实，
判定归 `tests/test_manifest_image_pixels.py`。退出码恒为 0（除非探针自己崩了）。
"""

from __future__ import annotations

import io
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.image as mimage  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.image import _ImageBase  # noqa: E402
from matplotlib.offsetbox import AnnotationBbox, OffsetImage  # noqa: E402

import manifest as M  # noqa: E402
import overrides as O  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 数的是**真正干活的** `matplotlib.image._resample`：`AxesImage` 等子类各自重写了 `make_image`，
#: 在基类上数永远是 0（第一版探针就是这么空转的，变异「拿掉跳过」照样绿）
_REAL_RESAMPLE = mimage._resample
CALLS = {"resample": 0}


def _counting_resample(*a, **k):
    CALLS["resample"] += 1
    return _REAL_RESAMPLE(*a, **k)


class _counting:
    def __enter__(self):
        CALLS["resample"] = 0
        mimage._resample = _counting_resample
        return CALLS

    def __exit__(self, *exc):
        mimage._resample = _REAL_RESAMPLE
        return False


def _figures():
    rng = np.random.default_rng(0)
    out = []
    f, ax = plt.subplots()
    im = ax.imshow(rng.random((300, 400)), aspect="equal", interpolation="lanczos")
    f.colorbar(im)
    ax.set_title("imshow + colorbar")
    out.append(("imshow_colorbar", f))
    f = plt.figure()
    f.figimage(rng.random((50, 60)), xo=10, yo=20)
    f.add_subplot().plot([1, 2], label="l")
    out.append(("figimage", f))
    f, ax = plt.subplots()
    ax.add_artist(AnnotationBbox(OffsetImage(rng.random((30, 30)), zoom=2), (0.5, 0.5)))
    ax.plot([0, 1], label="line")
    ax.legend()
    out.append(("offsetimage", f))
    f, axs = plt.subplots(1, 2, layout="constrained")
    axs[0].imshow(np.eye(50))
    axs[1].pcolormesh(rng.random((20, 20)), rasterized=True)
    out.append(("constrained", f))
    return out


def _max_float_diff(a, b) -> tuple[float, int]:
    worst = [0.0, 0]

    def walk(x, y):
        if isinstance(x, dict):
            if set(x) != set(y):
                worst[1] += 1
                return
            for k in x:
                walk(x[k], y[k])
        elif isinstance(x, list):
            if len(x) != len(y):
                worst[1] += 1
                return
            for u, v in zip(x, y):
                walk(u, v)
        elif isinstance(x, float) and isinstance(y, (int, float)):
            worst[0] = max(worst[0], abs(x - y))
        elif x != y:
            worst[1] += 1

    walk(a, b)
    return worst[0], worst[1]


def main() -> None:
    report = {"cases": {}}
    real_skip = O.image_pixels_skipped
    for name, fig in _figures():
        state = O.FigState(fig)
        M.instrument(state)
        # 对照：不跳过，连着两次（constrained layout 每次 draw 在上一次结果上再迭代，本身有浮点噪声）
        O.image_pixels_skipped = M.image_pixels_skipped = lambda fig: _null()
        ref1 = json.loads(json.dumps(M.build_manifest(state, "S"), default=str))
        with _counting():
            ref2 = json.loads(json.dumps(M.build_manifest(state, "S"), default=str))
        resample_unskipped = CALLS["resample"]
        O.image_pixels_skipped = M.image_pixels_skipped = real_skip
        with _counting():
            got = json.loads(json.dumps(M.build_manifest(state, "S"), default=str))
        images = fig.findobj(match=_ImageBase)
        leftover = sum(1 for im in images if "draw" in vars(im) or "make_image" in vars(im))
        svg = io.BytesIO()
        fig.savefig(svg, format="svg")
        report["cases"][name] = {
            "elements": len(got["elements"]),
            "images": len(images),
            "resample_calls_in_manifest": CALLS["resample"],
            "resample_calls_unskipped": resample_unskipped,
            "control_noise": _max_float_diff(ref1, ref2)[0],
            "diff_vs_unskipped": _max_float_diff(ref2, got),
            "leftover_instance_patches": leftover,
            "svg_image_tags": svg.getvalue().count(b"<image"),
        }

    # 另一张 figure 不受牵连：在 A 的上下文里，B 的图片仍是类上的实现
    fa, axa = plt.subplots()
    axa.imshow(np.eye(5))
    fb, axb = plt.subplots()
    imb = axb.imshow(np.eye(5))
    with O.image_pixels_skipped(fa):
        report["other_figure_untouched"] = "make_image" not in vars(imb) and "draw" not in vars(imb)

    # 自定义图片（Codex #526）：子类在**类上**重写 draw / make_image 的不许被换掉——它的 draw 可能更新几何；
    # 什么都没重写的普通子类照样跳过（证明判据没有把优化整个关掉）
    from matplotlib.image import AxesImage

    class _GeomImage(AxesImage):
        draws = 0

        def draw(self, renderer, *a, **k):
            _GeomImage.draws += 1
            self.set_extent((0, 10 + _GeomImage.draws, 0, 5))  # 在 draw 里更新几何
            return super().draw(renderer, *a, **k)

    class _MakeImage(AxesImage):
        def make_image(self, renderer, magnification=1.0, unsampled=False):
            return super().make_image(renderer, magnification, unsampled)

    class _Plain(AxesImage):
        pass

    import functools

    class _Wrapped(AxesImage):
        # functools.wraps 把 __module__ 抄成 matplotlib.image：按函数的模块判会把它认成原版（Codex #535）
        @functools.wraps(AxesImage.draw)
        def draw(self, renderer, *a, **k):
            _Wrapped.draws += 1
            return super().draw(renderer, *a, **k)

        draws = 0

    fc, axc = plt.subplots()
    custom = {}
    for key, cls in (
        ("class_draw", _GeomImage),
        ("class_make_image", _MakeImage),
        ("wrapped_draw", _Wrapped),
        ("plain", _Plain),
    ):
        im = cls(axc)
        im.set_data(np.random.default_rng(2).random((50, 50)))
        axc.add_image(im)
        custom[key] = im
    with O.image_pixels_skipped(fc):
        seen = {k: ("draw" in vars(im)) for k, im in custom.items()}
        before = _GeomImage.draws
        fc.canvas.draw()
        custom_draws = _GeomImage.draws - before
    report["custom_images"] = {
        "patched": seen,
        "class_draw_ran_in_layout_draw": custom_draws,
        "wrapped_draw_module": _Wrapped.draw.__module__,
        "wrapped_draw_ran": _Wrapped.draws,
        "extent_after_layout_draw": list(custom["class_draw"].get_extent()),
        "expected_extent": [0, 10 + _GeomImage.draws, 0, 5],
    }

    # 几何 override 之后那次布局刷新（`overrides.apply` 里的 draw_without_rendering）也不重采样
    f, ax = plt.subplots()
    ax.imshow(np.random.default_rng(1).random((200, 200)))
    ax.set_title("t")
    state = O.FigState(f)
    M.instrument(state)
    M.build_manifest(state, "S")
    axes = next(e for e in state.elements if e["role"] == "axes")
    man = M.build_manifest(state, "S")
    fs_gid = next(
        e["gid"]
        for e in man["elements"]
        if e["role"] != "axes" and any(x["prop"] == "fontsize" for x in e.get("editable") or [])
    )
    pos = [round(v, 4) for v in ax.get_position().bounds]
    pos[0] += 0.01
    O.image_pixels_skipped = M.image_pixels_skipped = lambda fig: _null()
    with _counting():
        O.apply(
            state,
            [
                {"gid": axes["gid"], "prop": "position", "value": [pos[0] + 0.01, *pos[1:]]},
                {"gid": fs_gid, "prop": "fontsize", "value": 12},
            ],
        )
    report["geometry_apply_resample_unskipped"] = CALLS["resample"]
    O.image_pixels_skipped = M.image_pixels_skipped = real_skip
    with _counting():
        O.apply(
            state,
            [
                {"gid": axes["gid"], "prop": "position", "value": pos},
                {"gid": fs_gid, "prop": "fontsize", "value": 13},
            ],
        )
    report["geometry_apply_resample_calls"] = CALLS["resample"]
    print(json.dumps(report))


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    main()
