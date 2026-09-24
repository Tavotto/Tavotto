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


def data_stage():
    # 标量数据在 data 阶段重采样：进 `_resample` 的是 MaskedArray，另有一次 mask 的重采样，
    # 调用方会就地改那次的返回值（`out_alpha[out_mask] = 1`）
    # 遮一整块而不是随机散点：lanczos 会把 mask 的 NaN 扩散到核覆盖的每个输出像素，散点 mask
    # 让整张图全透明，改什么都「看不出变化」
    f, ax = plt.subplots(figsize=(4, 3))
    a = np.ma.array(_rng().random((700, 900)))
    a[100:250, 200:450] = np.ma.masked
    im = ax.imshow(a, cmap="viridis", interpolation="lanczos", interpolation_stage="data")
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
    "data_stage": data_stage,
    "figimage": figimage,
}


# ---- 改动：缓存键的每一维各一条（漏一维 = 那一条变旧） ------------------------------------

def _inplace_data(f, im):
    a = im.get_array()
    a[100:400, 100:500] = 0.0
    im.set_data(a)


def _position(f, im):
    im.axes.set_position([0.3, 0.3, 0.4, 0.4])


def _subpixel_shift(f, im):
    # 只动变换里的平移：输出尺寸不变（图整个在轴内），重采样的相位变了
    left, right, bottom, top = im.get_extent()
    im.set_extent((left + 0.37, right + 0.37, bottom, top))


def _roomy(f, im):
    # 给 `subpixel_shift` 留边：轴比图宽，平移不改变裁剪后的输出尺寸
    im.axes.set_xlim(-100, 1000)
    im.axes.set_ylim(800, -100)


MUTATIONS = {
    "cmap": lambda f, im: im.set_cmap("plasma"),
    "clim": lambda f, im: im.set_clim(0.2, 0.6),
    "alpha_scalar": lambda f, im: im.set_alpha(0.4),
    "alpha_array": lambda f, im: im.set_alpha(np.linspace(1, 0.1, im.get_array().size).reshape(im.get_array().shape)),
    "interpolation": lambda f, im: im.set_interpolation("bicubic"),
    # 3.8 默认就是 data、3.10 起默认 auto（缩小时落到 rgba）：换成与当前不同的那一个
    "interpolation_stage": lambda f, im: im.set_interpolation_stage(
        "rgba" if getattr(im, "get_interpolation_stage", lambda: im._interpolation_stage)() == "data" else "data"
    ),
    "resample": lambda f, im: im.set_resample(False),
    "filternorm": lambda f, im: im.set_filternorm(False),
    "filterrad": lambda f, im: im.set_filterrad(1.0),
    "origin": lambda f, im: setattr(im, "origin", "lower"),
    "extent": lambda f, im: im.set_extent((0, 450, 0, 700)),
    "axes_position": _position,
    "subpixel_shift": _subpixel_shift,
    "data_inplace": _inplace_data,
    "mask": lambda f, im: im.set_data(np.ma.masked_where(im.get_array() > 0.5, im.get_array())),
}
#: 每个改动在两张底图上各做一次（rgba 阶段 / data 阶段各走 `_make_image` 的一半）；
#: 两张都得「与不开缓存相同」，至少一张得「真的改变了画面」
MUTATION_BASES = ("scalar_masked", "data_stage")
#: 改动之前先摆好的场景（不算改动本身）
MUTATION_PREP = {"subpixel_shift": _roomy}


def _base_case(name):
    f, im = BASES[name]()
    off1, calls_off = _svg(f, cached=False)
    on_miss, calls_miss = _svg(f, cached=True)
    on_hit, calls_hit = _svg(f, cached=True)
    # 第二次命中：命中时若直接交出缓存里那份，调用方就地一改，这一遍就是脏的
    on_hit2, _ = _svg(f, cached=True)
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
        "hit2_same_as_uncached": on_hit2 == off1,
        "uncached_stable": off1 == off2,
        "dpi_same_as_uncached": dpi_on == dpi_off,
        "dpi_changed_picture": dpi_on != on_hit,
    }


def _mutation_case(name):
    out = {}
    for base in MUTATION_BASES:
        f, im = BASES[base]()
        if name in MUTATION_PREP:
            MUTATION_PREP[name](f, im)
        before, _ = _svg(f, cached=True)  # 暖缓存：改动之前那一版已经在缓存里
        MUTATIONS[name](f, im)
        after_on, _ = _svg(f, cached=True)
        after_off, _ = _svg(f, cached=False)
        plt.close(f)
        out[base] = {"same_as_uncached": after_on == after_off, "changed_picture": after_off != before}
    return out


# ---- 直接调 `_resample`：matplotlib 自己从不传的实参（`alpha=`）也得在键里 -----------------

def _direct():
    """缓存的是一个函数，契约就按函数的实参逐维验：每一维单独扰动一次，开缓存与原函数逐元素相同。"""
    from matplotlib.transforms import Affine2D

    wrapped = mimage._resample
    original = wrapped.__wrapped__
    f, ax = plt.subplots()
    im = ax.imshow(np.zeros((2, 2)), interpolation="lanczos")
    rng = _rng()
    data = rng.random((600, 700)).astype(np.float32)
    base = {
        "data": data,
        "out_shape": (200, 230),
        "transform": Affine2D().scale(230 / 700, 200 / 600),
        "kwargs": {},
    }

    def bump(a):
        b = a.copy()
        b[300, 350] += 0.5
        return b

    masked = np.ma.masked_where(data > 0.9, data)
    perturb = {
        "data": lambda c: {**c, "data": bump(c["data"])},
        "dtype": lambda c: {**c, "data": c["data"].astype(np.float64)},
        "mask": lambda c: {**c, "data": np.ma.masked_where(data > 0.5, data)},
        "out_shape": lambda c: {**c, "out_shape": (201, 230)},
        "transform": lambda c: {**c, "transform": c["transform"] + Affine2D().translate(0.37, 0)},
        "alpha_kw": lambda c: {**c, "kwargs": {"alpha": 0.5}},
        "resample_kw": lambda c: {**c, "kwargs": {"resample": False}},
    }
    knobs = {
        "interpolation": (lambda: im.set_interpolation("bicubic"), lambda: im.set_interpolation("lanczos")),
        "filternorm": (lambda: im.set_filternorm(False), lambda: im.set_filternorm(True)),
        "filterrad": (lambda: im.set_filterrad(1.0), lambda: im.set_filterrad(4.0)),
        "resample_getter": (lambda: im.set_resample(False), lambda: im.set_resample(None)),
        "origin": (lambda: setattr(im, "origin", "lower"), lambda: setattr(im, "origin", "upper")),
    }
    # 3.11 起 `_resample` 只在 nearest 时按 origin 翻数据，且只在输出像素正好落在两个输入像素的
    # 分界上时结果才不同：整 2 倍缩小让每个输出像素都落在分界上
    nearest = {
        "origin_nearest": (lambda: setattr(im, "origin", "lower"), lambda: setattr(im, "origin", "upper")),
    }

    def run(fn, c):
        return fn(im, c["data"], c["out_shape"], c["transform"], **c["kwargs"])

    def same(a, b):
        return a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a, b, equal_nan=True)

    out = {}
    for start in ("plain", "masked"):
        start_case = dict(base) if start == "plain" else {**base, "data": masked}
        for name, fn in perturb.items():
            with ph.preview_resample_cache():
                before = run(wrapped, start_case)  # 暖缓存
                changed_case = fn(start_case)
                cached = run(wrapped, changed_case)
            truth = run(original, changed_case)
            out[f"{start}:{name}"] = {"same": same(cached, truth), "changed": not same(truth, before)}
        for name, (set_, reset) in knobs.items():
            with ph.preview_resample_cache():
                before = run(wrapped, start_case)
                set_()
                cached = run(wrapped, start_case)
            truth = run(original, start_case)
            reset()
            out[f"{start}:{name}"] = {"same": same(cached, truth), "changed": not same(truth, before)}
        im.set_interpolation("nearest")
        on_edges = {**start_case, "out_shape": (300, 350), "transform": Affine2D().scale(0.5)}
        for name, (set_, reset) in nearest.items():
            with ph.preview_resample_cache():
                before = run(wrapped, on_edges)
                set_()
                cached = run(wrapped, on_edges)
            truth = run(original, on_edges)
            reset()
            out[f"{start}:{name}"] = {"same": same(cached, truth), "changed": not same(truth, before)}
        im.set_interpolation("lanczos")
    plt.close(f)
    return out


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
        "direct": _direct(),
        "reads_origin": "origin" in mimage._resample.__wrapped__.__code__.co_names,
        "gate": _gate(),
    }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
