"""把 `PreviewPlan` 变成 `savefig` 那一瞬的 `rasterized` 状态——**只在那一瞬**。

## 这个模块回答一个问题

> 分析器给了一份「该 rasterize 谁」的名单。怎么让它**只**改变这一次预览
> 长什么样，而用户 Figure 的真实状态一个字节都不动？

分析器（`preview_complexity`）刻意不设 `set_rasterized`：它一改，预览的表示法
就写进了常驻 Figure，而常驻 Figure 正是导出读的那一份——ADR 0022 不变量 2
（导出保真）就是这么破的。设与还原收在这里，是为了让「谁在什么时候动了
Figure」只有一个答案，且那个答案自带 `finally`。

## 三条纪律

1. **先读后写**：每个 artist 的旧值在设新值**之前**入账。`set_rasterized`
   自己抛了的话，账上记的仍是它当前的真值，还原是一次无害的重设。
2. **还原不许半途而废**：一个 artist 还原失败，其余的照样要还原。所以
   `finally` 里是「逐个 try / 收集失败」，不是一个大 try。半还原比不还原
   更坏——它让 Figure 处于一个谁都没设计过的中间态。
3. **还原失败要吵**：`RestoreFailed` 是缺陷不是运行时状况。静默吞掉它意味着
   下一次 `do_export` 会把预览用的位图化当成用户的选择写进论文里，而没有
   任何地方会报错（[[test-the-restored-moment]] 那一族）。

## 为什么两条入口共用它

桌面 worker（`figsession.render`）与浏览器 playground（`browser._render`）是
同一件事的两次实现。抄一份过去的代价不是多几行重复代码，而是**同一张图在
两个入口里预览表示法不一样**——而这类分叉只会在大图上、在用户那边发作。

自身只用标准库，但经 `preview_complexity` 传递依赖 matplotlib——所以它属于
`bridge_runner._PHASE2`（屏障之后才装），不是 `_PHASE1`。与 `worker.py` 同一条
sys.path 纪律，平铺 import。
"""

from __future__ import annotations

import collections
import contextlib
import contextvars
import hashlib
import threading
import time

import preview_complexity
import previewbudget

__all__ = ["RestoreFailed", "preview_rasterization", "preview_resample_cache", "save_preview_svg"]


class RestoreFailed(RuntimeError):
    """预览结束后没能把 `rasterized` 还原回去。**永远是缺陷，不是运行时状况。**"""


@contextlib.contextmanager
def preview_rasterization(artists):
    """在这个 `with` 里，`artists` 全部 `rasterized=True`；出去时逐个还原原值。

    `artists` 空着时这是一个货真价实的 no-op：一个 getter 都不调、一个 setter
    都不设。普通科研图每次预览都会走这一行，它必须真的不要钱。
    """
    previous: list[tuple[object, object]] = []
    try:
        for artist in artists:
            # **先入账再改**：`set_rasterized` 抛了的话账上记的仍是真值。
            previous.append((artist, artist.get_rasterized()))
            artist.set_rasterized(True)
        yield
    finally:
        failures = []
        # 逆序只是对称，`rasterized` 之间没有依赖；逐个 try 才是要点：
        # 第一个还原失败不能连累后面那些。
        for artist, old in reversed(previous):
            try:
                artist.set_rasterized(old)
            except Exception as exc:  # noqa: BLE001 - 收集而不是吞掉，下面重抛
                failures.append((artist, exc))
        if failures:
            raise RestoreFailed(
                f"{len(failures)} 个 artist 的 rasterized 没能还原——"
                f"常驻 Figure 现在带着预览专用的表示法，导出会把它写进论文里："
                + "；".join(f"{a!r}: {e!r}" for a, e in failures)
            )


def save_preview_svg(state, save, timings=None):
    """出一版 hybrid 预览 SVG，返回 `(plan, svg_bytes)`。**升档策略只有这一处。**

    `save(plan) -> svg_bytes` 由调用方给：桌面写盘后 `stat().st_size`，
    playground 写内存缓冲后 `tell()`。量的是同一个东西，落点不同。

    `timings` 非空时填 `preview_plan_ms`（分析 + 可能的升档重判）与
    `canvas_draw_ms`（`savefig`，升档时是**两遍之和**）。**计时收在这里，
    因为只有这里知道那两段各自从哪到哪**——在调用方那边掐表，掐到的会是
    「掐表到调用之间」那一段（实测 7 µs，而分析真实是 16 µs：一个看着很像
    真的、但量错了对象的数字）。

    ## 为什么要第二遍

    分析器量的是**原料**（artist 图上有多少 primitive），字节闸量的是**产物**
    ——两把尺子相互独立，这正是它的价值：估算漏了的时候，只有产物那把量得
    出来。软闸（`EDITOR_SVG_SOFT_LIMIT_BYTES`）之上仍有可 rasterize 的数据层
    留在矢量层，说明这一版的估价偏低，把它们一并收进来重画一遍。

    第二遍**最多一次**：还是超软闸就到此为止（再收也没东西可收了），交给按
    字节的硬闸兜底。代价是这类图上多付一次 `savefig`——只有真的画出了 8 MiB
    以上的矢量 SVG 才会发生，而那本来就是一张要送进浏览器 DOM 的巨图。
    """
    clock = _Clock()
    with clock.plan():
        plan = preview_complexity.plan_for_state(state)
    svg_bytes = _save_with(plan, save, clock)
    if previewbudget.wants_hybrid_escalation(svg_bytes):
        with clock.plan():
            harder = preview_complexity.escalate_plan(plan)
        if harder is not None:
            plan = harder
            svg_bytes = _save_with(plan, save, clock)
    if timings is not None:
        timings["preview_plan_ms"] = round(clock.plan_ms, 3)
        timings["canvas_draw_ms"] = round(clock.draw_ms, 3)
    return plan, svg_bytes


class _Clock:
    """两段各自的累计耗时。升档时 `savefig` 跑两遍——**两遍都算**，用户等的
    就是两遍；分析同理（第一次裁决 + 一次升档重判）。"""

    def __init__(self):
        self.plan_ms = 0.0
        self.draw_ms = 0.0

    @contextlib.contextmanager
    def _span(self, field):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            setattr(self, field, getattr(self, field) + (time.perf_counter() - t0) * 1000.0)

    def plan(self):
        return self._span("plan_ms")

    def draw(self):
        return self._span("draw_ms")


def _save_with(plan, save, clock):
    # `set_rasterized` 那两下算进 draw：它们是「画这一版」的成本，不是分析的
    with clock.draw(), preview_rasterization(plan.rasterized_artists), preview_resample_cache():
        return save(plan)


# ---------------------------------------------------------------------------
# 预览 SVG 那一遍的图片重采样缓存
#
# 用户图里一张大位图（2026-09-24 实测：1005×1753 lanczos，2D + RGBA 两次）每次预览
# `savefig(svg)` 都要重采样一遍，约 170ms，而图没变时输入逐字节相同。缓存放在
# `matplotlib.image._resample` 这一层，而不是 artist 那一层：它是**纯函数**——
# norm / cmap / alpha 数组 / interpolation_stage 在进来之前已经算进 `data` 了，
# 键只需要它的实参加上它自己读的那几个 getter，不用去枚举 artist 的一堆属性
# （漏一个 = 预览显示旧图，比慢更糟）。
#
# 「它自己读了什么」随 matplotlib 版本变（3.11 多读了 `image_obj.origin`），所以
# 装之前核对 `_resample` 引用的名字：出现 `_RESAMPLE_KNOWN_NAMES` 之外的一律不装，
# 退回原路径——宁慢不旧。只在 `preview_resample_cache()` 里查缓存（contextvar，
# 别的线程的导出、manifest 那几次布局 draw 一律直通）。

# 三档（3.8.4 / 3.10.8 / 3.11.1）`_resample.__code__.co_names` 的并集。
_RESAMPLE_KNOWN_NAMES = frozenset({
    "Affine2D", "_image", "_interpd_", "abs", "array", "ceil", "diff", "dtype", "flip",
    "format", "get_filternorm", "get_filterrad", "get_interpolation", "get_resample",
    "int", "np", "origin", "resample", "scale", "shape", "transform", "translate",
    "warn", "warnings", "zeros",
})
# 小图重采样本来就不要钱，不值得付哈希与拷贝
_RESAMPLE_CACHE_MIN_ELEMENTS = 1 << 18
_RESAMPLE_CACHE_MAX_BYTES = 64 << 20
# `_resample` 在这两个边界之外会 warn 并降采样；命中缓存会吞掉那条 warning，不缓存
_RESAMPLE_MAX_ROWS = 1 << 24
_RESAMPLE_MAX_COLS = 1 << 23

_resample_cache_on: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tavotto_preview_resample_cache", default=False
)
_resample_cache: collections.OrderedDict = collections.OrderedDict()
_resample_cache_bytes = 0
_resample_lock = threading.Lock()
# None = 还没试过装；True / False = 装上了 / 这一版 matplotlib 不认、不装
_resample_installed: bool | None = None


@contextlib.contextmanager
def preview_resample_cache():
    """在这个 `with` 里（本线程 / 本上下文），大图的 `_resample` 按实参内容查缓存。"""
    _install_resample_cache()
    token = _resample_cache_on.set(True)
    try:
        yield
    finally:
        _resample_cache_on.reset(token)


def _install_resample_cache():
    global _resample_installed
    if _resample_installed is not None:
        return
    with _resample_lock:
        if _resample_installed is not None:
            return
        import matplotlib.image as mimage

        wrapped = _wrap_resample(getattr(mimage, "_resample", None))
        if wrapped is not None:
            mimage._resample = wrapped
        _resample_installed = wrapped is not None


def _wrap_resample(original):
    """给 `original` 套上缓存；它引用了认不出的名字（新版本多读了什么）就回 None。"""
    code = getattr(original, "__code__", None)
    if code is None or not set(code.co_names) <= _RESAMPLE_KNOWN_NAMES:
        return None

    def _cached_resample(image_obj, data, out_shape, transform, *args, **kwargs):
        key = _resample_key(image_obj, data, out_shape, transform, args, kwargs)
        if key is None:
            return original(image_obj, data, out_shape, transform, *args, **kwargs)
        hit = _resample_lookup(key)
        if hit is not None:
            # 调用方会就地改返回值（`out_alpha *= …`），给副本
            return hit.copy()
        out = original(image_obj, data, out_shape, transform, *args, **kwargs)
        _resample_store(key, out.copy())
        return out

    _cached_resample.__wrapped__ = original
    return _cached_resample


def _resample_key(image_obj, data, out_shape, transform, args, kwargs):
    """这次调用的完整键；有任何一样说不清就回 None（直通，不缓存）。"""
    if not _resample_cache_on.get() or args:
        return None
    import numpy as np

    # data 阶段（标量图）传进来的是 MaskedArray：数据与 mask 都进键——C 那边看不看
    # mask 都不会让键漏掉它
    if type(data) is np.ma.MaskedArray:
        mask = np.ma.getmaskarray(data)
        data = data.data
    elif type(data) is np.ndarray:
        mask = None
    else:
        return None
    if data.dtype.hasobject or data.size < _RESAMPLE_CACHE_MIN_ELEMENTS:
        return None
    if data.ndim < 2 or data.shape[0] > _RESAMPLE_MAX_ROWS or data.shape[1] > _RESAMPLE_MAX_COLS:
        return None
    if set(kwargs) - {"resample", "alpha"}:
        return None
    alpha = kwargs.get("alpha", 1)
    if not isinstance(alpha, (int, float, np.integer, np.floating)) or isinstance(alpha, bool):
        return None
    if not getattr(transform, "is_affine", False):
        return None
    resample = kwargs.get("resample")
    if resample is None:
        resample = image_obj.get_resample()
    digest = hashlib.sha256()
    digest.update(f"{data.dtype.str}|{data.shape}".encode())
    digest.update(np.ascontiguousarray(data).view(np.uint8).data)
    if mask is not None:
        digest.update(b"|mask|")
        digest.update(np.ascontiguousarray(mask).view(np.uint8).data)
    matrix = np.asarray(transform.get_matrix(), dtype=float)
    return (
        digest.digest(),
        tuple(int(n) for n in out_shape),
        matrix.tobytes(),
        float(alpha),
        bool(resample),
        str(image_obj.get_interpolation()),
        bool(image_obj.get_filternorm()),
        float(image_obj.get_filterrad()),
        str(getattr(image_obj, "origin", None)),
    )


def _resample_lookup(key):
    with _resample_lock:
        out = _resample_cache.get(key)
        if out is not None:
            _resample_cache.move_to_end(key)
        return out


def _resample_store(key, out):
    global _resample_cache_bytes
    if out.nbytes > _RESAMPLE_CACHE_MAX_BYTES:
        return
    with _resample_lock:
        old = _resample_cache.pop(key, None)
        if old is not None:
            _resample_cache_bytes -= old.nbytes
        _resample_cache[key] = out
        _resample_cache_bytes += out.nbytes
        while _resample_cache_bytes > _RESAMPLE_CACHE_MAX_BYTES:
            _, evicted = _resample_cache.popitem(last=False)
            _resample_cache_bytes -= evicted.nbytes
