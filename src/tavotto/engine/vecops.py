"""画布矢量几何内核（布尔 / 偏移 / 轮廓化描边 / 简化）的后端调用方（ADR 0212 第 3 期）。

跑的是前端同一份 `web/src/lib/vecops/vecops.wasm`（Rust crate `vecops/`），经 wasmtime 加载——后端不另写一份几何。
ABI 与前端 `web/src/lib/vecops/kernel.ts` 逐条相同：`abi_version()` 必须 == `VECOPS_ABI`；`alloc(n)` 按 f64 个数、
把输入写进去；四个计算函数返回输出的 f64 个数（0 = 输入坏了 / 算不出），结果在 `out_ptr()`，下一次调用前有效；`dealloc(p, n)`。
编码格式见 `vecops/src/lib.rs` 开头，几何语义（谁的样式、id 沿用）在 `engine/vectorgeom.py`。

**绝不让调用方崩**：wasmtime 装不上 / 找不到 .wasm / ABI 对不上 / 编译失败 → `available()` 为 False、四个函数回 None；
计算中 trap（含燃料耗尽）→ 这一次回 None、实例扔掉，下次重建（线性内存的状态不可信）。

延后 import：Flask 父进程启动不加载 wasmtime（原生库约 20 MB），第一次用到才付；模块与引擎进程内缓存（编译约 0.12 s）。
"""

from __future__ import annotations

import atexit
import gc
import math
import struct
import threading
from pathlib import Path

#: 与 `vecops/src/lib.rs` 的 `ABI_VERSION`、前端 `kernel.ts` 的 `VECOPS_ABI` 同步改
VECOPS_ABI = 1
#: 每次计算的燃料上限（wasm 指令数的量级）：病态输入死循环时 trap 成「没有结果」而不是把请求线程挂住
_FUEL = 8_000_000_000

_REPO_WASM = ("web", "src", "lib", "vecops", "vecops.wasm")

_lock = threading.RLock()  # 一个实例一块线性内存，调用必须串行
_module = None  # (engine, module)
_unusable = False  # 装不上 / 找不到 / ABI 不对：本进程内不再试
_inst: tuple | None = None  # (store, exports, memory)


def wasm_path() -> Path | None:
    """内核字节的位置：优先包内那一份（前端构建把它放进 `web/assets/vecops-<hash>.wasm`，wheel 与冻结产物都带着），
    退回源码树里的那一份；都没有 = None（内核不可用）。"""
    pkg = Path(__file__).resolve().parent.parent
    built = sorted(
        (pkg / "web" / "assets").glob("vecops*.wasm"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if built:
        return built[0]
    src = pkg.parent.parent.joinpath(*_REPO_WASM)
    return src if src.is_file() else None


def _shutdown() -> None:
    """解释器退出前先放掉 wasmtime 的对象：它们的 `__del__` 要调 ctypes 绑定，等到模块全局被拆掉再析构会满屏
    `Exception ignored … 'NoneType' object is not callable`（无害但吓人）。"""
    global _module, _inst
    _inst, _module = None, None
    gc.collect()


def _load_module():
    global _module, _unusable
    if _module is not None:
        return _module
    if _unusable:
        return None
    try:
        path = wasm_path()
        if path is None:
            raise FileNotFoundError("vecops.wasm")
        import wasmtime  # 延后 import：见模块说明

        cfg = wasmtime.Config()
        cfg.consume_fuel = True
        engine = wasmtime.Engine(cfg)
        _module = (engine, wasmtime.Module(engine, path.read_bytes()))
        atexit.register(_shutdown)
    except Exception:  # noqa: BLE001 — 任何原因都当「不可用」，不抛给调用方
        _unusable = True
        return None
    return _module


def _instantiate():
    global _inst, _unusable
    mod = _load_module()
    if mod is None:
        return None
    try:
        import wasmtime

        engine, module = mod
        store = wasmtime.Store(engine)
        store.set_fuel(_FUEL)
        ex = wasmtime.Instance(store, module, []).exports(store)
        fns = {
            n: ex[n]
            for n in (
                "abi_version",
                "alloc",
                "dealloc",
                "out_ptr",
                "pathfinder",
                "offset",
                "outline",
                "simplify",
            )
        }
        if fns["abi_version"](store) != VECOPS_ABI:
            _unusable = True
            return None
        _inst = (store, fns, ex["memory"])
    except Exception:  # noqa: BLE001
        _unusable = True
        _inst = None
        return None
    return _inst


def available() -> bool:
    """内核能不能用（加载并实例化一次；之后是缓存）。"""
    with _lock:
        return (_inst or _instantiate()) is not None


def _reset_for_tests() -> None:
    global _module, _unusable, _inst
    with _lock:
        _module, _unusable, _inst = None, False, None
    gc.collect()


def _run(name: str, lead: tuple, floats: list[float], tail: tuple) -> list[float] | None:
    """`name(*lead, p, n, *tail)`：把 `floats` 写进内核内存、调用、读回输出；任何失败 = None。"""
    global _inst
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (*floats, *tail)):
        return None
    with _lock:
        inst = _inst or _instantiate()
        if inst is None:
            return None
        store, fns, mem = inst
        n_in = len(floats)
        try:
            store.set_fuel(_FUEL)
            p = fns["alloc"](store, n_in)
            if n_in:
                mem.write(store, struct.pack(f"<{n_in}d", *floats), p)
            n = fns[name](store, *lead, p, n_in, *tail)
            out = None
            if n > 0:
                q = fns["out_ptr"](store)
                out = list(struct.unpack(f"<{n}d", bytes(mem.read(store, q, q + 8 * n))))
            fns["dealloc"](store, p, n_in)
            return out
        except Exception:  # noqa: BLE001 — trap / 燃料耗尽 / 越界：线性内存不可信，扔掉实例，下次重建
            _inst = None
            return None


def pathfinder(op: int, flat: list[float]) -> list[float] | None:
    """路径查找器：`op` 0 合并 · 1 减去顶层 · 2 相交 · 3 排除重叠 · 4 分割 · 5 修边 · 6 合并同色 · 7 裁剪 · 8 减去后方；
    `flat` 是形状栈编码（从后往前）。回输出编码；算不出 / 内核不可用 = None。"""
    return _run("pathfinder", (int(op),), list(flat), ())


def offset(flat: list[float], delta: float, join: int, miter: float) -> list[float] | None:
    """偏移（delta 正 = 往外）；join 0 斜接 · 1 圆角 · 2 斜角。"""
    return _run("offset", (), list(flat), (float(delta), int(join), float(miter)))


def outline(
    flat: list[float], width: float, cap: int, join: int, miter: float
) -> list[float] | None:
    """把居中的实线描边拓成面积；cap 0 平头 · 1 圆头 · 2 方头。"""
    return _run("outline", (), list(flat), (float(width), int(cap), int(join), float(miter)))


def simplify(flat: list[float], tolerance: float) -> list[float] | None:
    """在容差内删掉多余的锚点。"""
    return _run("simplify", (), list(flat), (float(tolerance),))
