"""「import 一个包会不会改动进程级状态」的快照判据——`figcapture.SIDE_EFFECT_FREE_IMPORTS` 的唯一尺子。

名单的意思是：脚本 `import X as Y` 却从没读 Y 时，X 缺了可以给占位（ADR 0061 §二 2026-09-24 修订）。
这只在 import X **什么都不改**时才成立——别名同样可以只为副作用而写（`import cmocean as cm` 注册色图、
`import requests as _r` 装 logging handler、改 warnings 过滤器；评审 #555 两条 P1）。所以判据不是
「碰没碰 matplotlib」，而是在**全新解释器**（`python -I`）里 import 前后比一份进程级快照，**任何一项**
变了就不进名单：

* matplotlib 进了 `sys.modules`（样式 / 色图 / rcParams / 单位转换器都住在它里面）；
* `os.environ` 整体；`warnings.filters`（唯一豁免：新加的、只管这个包**自己定义的**警告类的过滤器——
  包不在，那个类就不存在，谁也发不出这种警告）；
* logging：root 与 import 之前就在的 logger 的 handlers / level / propagate / disabled；import 之后
  **新出现的**、带 handler 的 logger（含 `NullHandler`——判据不替它判「无害」）；
* `sys.path` / `sys.meta_path` / `sys.path_hooks`；
* 所有信号的处理器；`sys.excepthook` / `sys.displayhook` / `threading.excepthook`；
* `atexit` 注册数；`builtins` 的名字与对象身份；
* `codecs.register` / `locale.setlocale`（改动型调用）被调过没有（没有公开的「列出已注册 codec」接口，
  所以在 import 之前包一层记账），以及当前 locale；
* 顺带：递归上限、线程切换间隔、存活线程数、gc 开关与阈值、trace / profile 函数、`sys.stdout` /
  `sys.stderr` 身份、cwd。

判不出的不算没有：这份快照看不见的副作用（改了某个第三方模块的全局、写了磁盘文件、开了网络连接）
仍是盲区，写在 ADR 里。

两种用法（同一份函数）：

    python -I tests/support/import_side_effects.py sympy requests …   # 实测：每个名字一行 JSON
    measure(name)                                                       # 在当前进程里量一次（只该在全新子进程里调）
"""

from __future__ import annotations

import json
import sys

ABSENT = "absent"


def _logger_state(logger) -> tuple:
    return (
        tuple(type(h).__qualname__ + "@" + str(id(h)) for h in logger.handlers),
        logger.level,
        logger.propagate,
        logger.disabled,
    )


def _snapshot() -> dict:
    import atexit
    import builtins
    import gc
    import locale
    import logging
    import os
    import signal
    import threading
    import warnings

    loggers = {"": _logger_state(logging.getLogger())}
    for name, obj in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(obj, logging.Logger):
            loggers[name] = _logger_state(obj)
    handlers = {}
    for sig in signal.valid_signals():
        try:
            handlers[int(sig)] = repr(signal.getsignal(sig))
        except (OSError, ValueError, TypeError):
            continue
    try:
        current_locale = locale.setlocale(locale.LC_ALL, None)
    except locale.Error:
        current_locale = None
    return {
        "matplotlib": sorted(
            k for k in sys.modules if k == "matplotlib" or k.startswith("matplotlib.")
        ),
        "environ": dict(os.environ),
        "warnings.filters": [
            (repr(f), getattr(f[2], "__module__", "") or "") for f in warnings.filters
        ],
        "logging": loggers,
        "sys.path": list(sys.path),
        "sys.meta_path": [repr(f) for f in sys.meta_path],
        "sys.path_hooks": [repr(h) for h in sys.path_hooks],
        "signals": handlers,
        "sys.excepthook": id(sys.excepthook),
        "sys.displayhook": id(sys.displayhook),
        "threading.excepthook": id(threading.excepthook),
        "atexit": atexit._ncallbacks() if hasattr(atexit, "_ncallbacks") else None,
        "builtins": {k: id(v) for k, v in vars(builtins).items()},
        "locale": current_locale,
        "recursionlimit": sys.getrecursionlimit(),
        "switchinterval": sys.getswitchinterval(),
        "threads": threading.active_count(),
        "gc": (gc.isenabled(), gc.get_threshold()),
        "trace": (id(sys.gettrace()), id(sys.getprofile())),
        "streams": (id(sys.stdout), id(sys.stderr)),
        "cwd": os.getcwd(),
    }


def _own(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _diff(before: dict, after: dict, package: str) -> list[str]:
    changed = []
    for key in before:
        a, b = before[key], after[key]
        if key == "warnings.filters":
            # 唯一的豁免：新加的过滤器只管**这个包自己定义的**警告类（sympy 的
            # `simplefilter("once", SymPyDeprecationWarning)`）——包不在，这个类就不存在，
            # 谁也发不出这种警告，占位不装它什么都不差。其余任何增删改都算变化。
            if [f for f in b if not (f not in a and _own(f[1], package))] != a:
                changed.append(key)
            continue
        if key == "logging":
            # 之前就在的 logger 状态变了；之后新出现的只在带 handler 时算（`getLogger(__name__)` 本身不算副作用）
            if any(a[name] != b.get(name) for name in a):
                changed.append("logging")
            elif any(name not in a and b[name][0] for name in b):
                changed.append("logging (new logger with handler)")
            continue
        if a != b:
            changed.append(key)
    return changed


def measure(name: str) -> dict:
    """在当前进程里 import `name`，回 `{"module", "changed": [...]}`；装不上回 `{"module", "absent": True}`。

    只该在全新解释器（`python -I`）里调：同一进程里之前 import 过什么都会让快照失真。"""
    import codecs
    import importlib
    import locale

    calls: list[str] = []
    real_register, real_setlocale = codecs.register, locale.setlocale

    def _register(fn):
        calls.append("codecs.register")
        return real_register(fn)

    def _setlocale(category, value=None):
        if value is not None:
            calls.append("locale.setlocale")
        return real_setlocale(category, value)

    codecs.register, locale.setlocale = _register, _setlocale
    try:
        before = _snapshot()
        try:
            importlib.import_module(name)
        except ImportError:
            return {"module": name, ABSENT: True}
        after = _snapshot()
    finally:
        codecs.register, locale.setlocale = real_register, real_setlocale
    return {"module": name, "changed": _diff(before, after, name) + sorted(set(calls))}


if __name__ == "__main__":
    # 被 spawn 时 stdout 是管道，Windows 上会退回系统区域编码——钉 UTF-8（只在作为入口时，
    # 被 import 来调 `measure()` 的进程不动它的流）
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    # 一个名字一个全新进程：同一进程里前一个包的副作用会污染后一个的「之前」
    import subprocess

    if len(sys.argv) == 2 and not sys.argv[1].startswith("--"):
        print(json.dumps(measure(sys.argv[1]), ensure_ascii=False))
    else:
        for mod in sys.argv[1:]:
            out = subprocess.run(
                [sys.executable, "-I", __file__, mod],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print(out.stdout.strip() or json.dumps({"module": mod, "error": out.stderr[-300:]}))
