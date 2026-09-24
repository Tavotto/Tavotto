"""在 **worker 解释器**里跑的探针：预览 SVG 那一遍的重采样缓存（`preview_hybrid.preview_resample_cache`）。

`tests/` 跑在 Flask 的 .venv 里、import 不动 matplotlib；这里在 worker 那一侧如实报事实，
判定归 `tests/test_preview_resample_cache.py`。退出码恒为 0（除非探针自己崩了）。

「预览没变旧」的尺子是**同一时刻、同一张 figure 不开缓存再画一遍**，逐字节比 SVG
（`svg.hashsalt` 钉住，否则 clip-path id 每次都不一样）。每个改动还要证明它真的改变了
画面——改了个不影响像素的属性，「开缓存与不开相同」就恒等成立。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "src", "tavotto", "engine"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "tavotto-probe"
import matplotlib.image as mimage  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

import preview_hybrid as ph  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DPI = 100


class _CountingImage:
    """数**真正干活的** C 重采样 `_image.resample`：缓存装在 `_resample` 上，数它自己等于数缓存。"""

    def __init__(self, real):
        self._real = real
        self.calls = 0

    def resample(self, *a, **k):
        self.calls += 1
        return self._real.resample(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


COUNTER = _CountingImage(mimage._image)
mimage._image = COUNTER


def _svg(fig, *, cached, dpi=DPI):
    buf = io.BytesIO()
    COUNTER.calls = 0
    if cached:
        with ph.preview_resample_cache():
            fig.savefig(buf, format="svg", dpi=dpi)
    else:
        fig.savefig(buf, format="svg", dpi=dpi)
    return re.sub(rb"<dc:date>.*?</dc:date>", b"", buf.getvalue()), COUNTER.calls


def _rng():
    return np.random.default_rng(0)


# ---- 基础图：各走 `_make_image` 的一条分支 -------------------------------------------------

def rgb_uint8():
    f, ax = plt.subplots(figsize=(4, 3))
    im = ax.imshow(_rng().integers(0, 255, (700, 900, 3), dtype=np.uint8), interpolation="lanczos")
    return f, im


def scalar_masked():
    f, ax = plt.subplots(figsize=(4, 3))
    a = _rng().random((700, 900))
    a = np.ma.masked_where(a > 0.95, a)
    im = ax.imshow(a, cmap="viridis", interpolation="lanczos")
    return f, im


def alpha_array():
    f, ax = plt.subplots(figsize=(4, 3))
    a = _rng().random((700, 900))
    im = ax.imshow(a, alpha=np.linspace(0.2, 1, a.size).reshape(a.shape), interpolation="bicubic")
    return f, im


def origin_lower_nearest():
    f, ax = plt.subplots(figsize=(4, 3))
    im = ax.imshow(_rng().random((700, 900)), origin="lower", interpolation="nearest")
    return f, im


def auto_interp():
    f, ax = plt.subplots(figsize=(4, 3))
    im = ax.imshow(_rng().random((1400, 1800)))
    return f, im


def rgba_stage():
    f, ax = plt.subplots(figsize=(4, 3))
    im = ax.imshow(_rng().random((700, 900)), interpolation="lanczos", interpolation_stage="rgba")
    return f, im


def figimage():
    f = plt.figure(figsize=(4, 3))
    im = f.figimage(_rng().random((600, 800)), cmap="magma", resize=False)
    return f, im


BASES = {
    "rgb_uint8": rgb_uint8,
    "scalar_masked": scalar_masked,
    "alpha_array": alpha_array,
    "origin_lower_nearest": origin_lower_nearest,
    "auto_interp": auto_interp,
    "rgba_stage": rgba_stage,
    "figimage": figimage,
}


# ---- 改动：缓存键的每一维各一条（漏一维 = 那一条变旧） ------------------------------------

def _inplace_data(f, im):
    a = im.get_array()
    a[100:400, 100:500] = 0.0
    im.set_data(a)


def _position(f, im):
    im.axes.set_position([0.3, 0.3, 0.4, 0.4])


MUTATIONS = {
    "cmap": lambda f, im: im.set_cmap("plasma"),
    "clim": lambda f, im: im.set_clim(0.2, 0.6),
    "alpha_scalar": lambda f, im: im.set_alpha(0.4),
    "alpha_array": lambda f, im: im.set_alpha(np.linspace(1, 0.1, im.get_array().size).reshape(im.get_array().shape)),
    "interpolation": lambda f, im: im.set_interpolation("bicubic"),
    "interpolation_stage": lambda f, im: im.set_interpolation_stage("data"),
    "resample": lambda f, im: im.set_resample(False),
    "filternorm": lambda f, im: im.set_filternorm(False),
    "filterrad": lambda f, im: im.set_filterrad(1.0),
    "origin": lambda f, im: setattr(im, "origin", "lower"),
    "extent": lambda f, im: im.set_extent((0, 450, 0, 700)),
    "axes_position": _position,
    "data_inplace": _inplace_data,
    "mask": lambda f, im: im.set_data(np.ma.masked_where(im.get_array() > 0.5, im.get_array())),
}
#: `resample=False` / `filternorm` / `filterrad` 只在 lanczos 这种带核的插值上看得出来
MUTATION_BASE = "scalar_masked"


def _base_case(name):
    f, im = BASES[name]()
    off1, calls_off = _svg(f, cached=False)
    on_miss, calls_miss = _svg(f, cached=True)
    on_hit, calls_hit = _svg(f, cached=True)
    off2, calls_off2 = _svg(f, cached=False)
    # 换 dpi：输出尺寸 / 变换都变了
    dpi_on, _ = _svg(f, cached=True, dpi=150)
    dpi_off, _ = _svg(f, cached=False, dpi=150)
    plt.close(f)
    return {
        "calls_uncached": calls_off,
        "calls_miss": calls_miss,
        "calls_hit": calls_hit,
        "calls_uncached_after": calls_off2,
        "miss_same_as_uncached": on_miss == off1,
        "hit_same_as_uncached": on_hit == off1,
        "uncached_stable": off1 == off2,
        "dpi_same_as_uncached": dpi_on == dpi_off,
        "dpi_changed_picture": dpi_on != on_hit,
    }


def _mutation_case(name):
    f, im = BASES[MUTATION_BASE]()
    before, _ = _svg(f, cached=True)  # 暖缓存：改动之前那一版已经在缓存里
    MUTATIONS[name](f, im)
    after_on, _ = _svg(f, cached=True)
    after_off, _ = _svg(f, cached=False)
    plt.close(f)
    return {"same_as_uncached": after_on == after_off, "changed_picture": after_off != before}


def _gate():
    def reads_something_new(image_obj, data, out_shape, transform, *, resample=None, alpha=1):
        return image_obj.get_brand_new_knob()

    real = mimage._resample
    return {
        "installed": getattr(real, "__wrapped__", None) is not None,
        "real_resample_recognised": ph._wrap_resample(getattr(real, "__wrapped__", real)) is not None,
        "unknown_read_rejected": ph._wrap_resample(reads_something_new) is None,
        "not_a_function_rejected": ph._wrap_resample(None) is None,
    }


def main():
    # 先装一次（第一次进 `preview_resample_cache` 时装），再测
    _svg(plt.figure(), cached=True)
    out = {
        "matplotlib": matplotlib.__version__,
        "bases": {name: _base_case(name) for name in BASES},
        "mutations": {name: _mutation_case(name) for name in MUTATIONS},
        "gate": _gate(),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
