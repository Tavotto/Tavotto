"""native bridge 的**引导层**：把 Tavotto 的引擎模块装进用户自己的进程，
而**不弄脏用户的顶层 import 命名空间**，并且**不提前 import pyplot**。

本模块必须是纯标准库、必须能在 3.10 上跑、必须不 import matplotlib——它是
用户进程里第一个被执行的 Tavotto 代码，任何多余的 import 都会改变用户脚本
看到的世界。

## 一、为什么不能照抄 safe worker 的 `sys.path.insert(0, HERE)`

`worker.py` 开头就把 engine 目录插到 `sys.path[0]` 然后平铺
`import manifest / overrides / patchspec`，并且**永远留在那里**。在 safe
worker 里这没问题（cwd 是沙盒、脚本由我们挑的解释器跑）。在 native bridge
里它是数据损坏级的缺陷：

    用户项目/
        manifest.py      ← 用户自己的（"实验清单"）
        overrides.py     ← 用户自己的（"参数覆盖"）
        config.py
        figure.py        →  import manifest   # 他要的是自己那份

engine 目录留在 `sys.path[0]` 的话，用户的 `import manifest` 拿到的是
Tavotto 的 manifest.py，报出来的错（AttributeError: module 'manifest' has
no attribute 'load_runs'）指向的方向与真实原因毫无关系。

所以本模块：

1. 只在**装载期间**把 engine 目录插进 `sys.path[0]`；
2. 装完把模块搬进私有包 `tavotto_bridge_runtime.*`，并把顶层名字**恢复原样**
   （原本没有就删掉）；
3. `sys.path` 逐字还原。

**延后到用户代码跑起来之后才执行的 import 不能是裸名**——那时 engine 目录已经
从 `sys.path` 收回，裸名会命中用户的文件。`overrides` 曾为此经 `_sibling()` 按
包前缀反取 `manifest._ordered_axes`；2026-09-17 遍历权威提成 `axestraversal`
之后，`overrides` 与 `manifest` 都在模块层平铺 import 它（装载期解析，与
`import pathgeom` 同一条路），引擎里不再有 late import。将来要加的话，按
`__name__` 的包前缀解析，别写裸名。

## 二、为什么不能提前 import pyplot

用户脚本有权决定后端：

    import matplotlib
    matplotlib.use("Agg")          # ← 这一句只在 pyplot 还没 import 时是纯的
    import matplotlib.pyplot as plt

bridge 只要先 import 了 pyplot，`use()` 就变成 `switch_backend()`——而它的
**文档承诺会销毁我们刚捕获的 Figure**（docstring：*"all open Figures will be
closed via ``plt.close('all')``"*）。matplotlib 3.10.8 实测**并不会**（函数体
里根本没有那个调用，只有 docstring 里有），也就是这一版的文档与实现不一致。
依赖"实现碰巧不销毁"而不是"文档说会销毁"，是下一次发版就塌的赌注。

所以钩子靠 `sys.meta_path` 上的一个**后置 import 钩子**安装：它不 import
任何东西，只在别人 import 到 `matplotlib.figure` / `matplotlib.pyplot`
**完成的那一刻**回调。用户什么时候 import，钩子什么时候生效；用户从不
import matplotlib，钩子一辈子不响。
"""

from __future__ import annotations

import importlib
import importlib.machinery
import os
import sys
import threading
import types

__all__ = [
    "PRIVATE_PKG",
    "ENGINE_SIBLINGS",
    "PostImportHook",
    "UnreadableImportSkipper",
    "install_unreadable_import_skipper",
    "skip_events",
    "unreadable_folder_fact",
    "drop_script_dir_from_sys_path",
    "load_engine_modules",
]

#: 引擎模块在用户进程里的私有包名。**刻意长且带前缀**——它必须不可能与
#: 任何用户项目的顶层模块重名（`tavotto` 这个名字本身都嫌短：用户完全
#: 可能有一个叫 tavotto 的包）。
PRIVATE_PKG = "tavotto_bridge_runtime"

#: 需要装进用户进程的引擎模块（装载顺序无关，import 系统自己解依赖）。
#: `figcapture` / `patchspec` 是纯标准库；其余三个要 matplotlib/numpy，
#: 所以它们**只在捕获之后**才装（见 `bridge_runner` 的两阶段装载）。
ENGINE_SIBLINGS = (
    "figcapture",
    "patchspec",
    "pathgeom",
    "axestraversal",
    "spinemodel",
    "tickmodel",
    "colorbarmodel",
    "legendmodel",
    "overrides",
    "manifest",
)

#: 装载后必须还给用户的顶层名字（= ENGINE_SIBLINGS + **它们平铺 import 的
#: 整条传递闭包**）。
#:
#: 漏一个的两种下场都很难查：用户项目里恰好有同名模块时，我们拿到的是**他
#: 那份**（import 系统先查 sys.modules，根本不会走 sys.path），表现是一个
#: 指向完全错误方向的 AttributeError；用户没有同名模块时，Tavotto 的那份会
#: **留在他的顶层 `sys.modules` 里**——他之后 import 同名模块就再也拿不到
#: 自己那份了。
#:
#: **这张表不靠人记得改**：`test_bridge_namespace.py` 从 AST 反推闭包并逐个
#: 比对（与 `test_runtime_build.py` 对 PyInstaller spec 的判据同一形状）。
#: 2026-08-28 加 `previewbudget` 时这张表漏过一次，是评审抓的不是门禁抓的
#: ——那条门禁当时只查 `ENGINE_SIBLINGS`，查不到 figsession 平铺进来的这一层。
_TOPLEVEL_TO_RESTORE = (
    *ENGINE_SIBLINGS,
    "figsession",
    "executionfact",
    "wireproto",
    "previewbudget",
    "preview_complexity",
    "preview_hybrid",
    "teedraw",
)


def drop_script_dir_from_sys_path(here: str) -> bool:
    """把「脚本自己的目录」从 `sys.path[0]` 摘掉，返回是否真的摘了。

    `python <engine>/bridge_runner.py …` 时 CPython **自动**把 `<engine>`
    放进 `sys.path[0]`——那正是本模块第一节要防的那件事，而且它发生在我们
    的第一行代码执行之前。`-P`（3.11+）能从源头关掉它，但下界是 3.10，
    所以只能在这里显式收回。

    只摘首位、且只在它确实等于 `here` 时摘：用户自己 `PYTHONPATH` 里恰好
    有同一个目录时（不该发生，但不是我们能禁止的），摘掉它会改变用户的
    import 语义。
    """
    if sys.path and os.path.abspath(sys.path[0]) == os.path.abspath(here):
        del sys.path[0]
        return True
    return False


def load_engine_modules(engine_dir: str, names) -> types.ModuleType:
    """把 `names` 装进私有包 `tavotto_bridge_runtime`，还原顶层命名空间。

    返回私有包对象（`pkg.manifest` / `pkg.figsession` … 取子模块）。
    可多次调用（两阶段装载）：已装过的不重复装。

    **三条不变量**（看护 `tests/bridge/test_bridge_namespace.py`）：

    1. 调用前后 `sys.path` 逐项相同；
    2. 调用后 `sys.modules` 里没有多出任何顶层的 `manifest` / `overrides` /
       `config` / `runtime` 之类的名字（原本有的原样保留）；
    3. 引擎模块之间互相引用的仍是**同一批对象**（不会出现两份 manifest）。
    """
    pkg = sys.modules.get(PRIVATE_PKG)
    if pkg is None:
        pkg = types.ModuleType(PRIVATE_PKG)
        pkg.__path__ = [engine_dir]  # 让 import 系统认它是包（诊断更友好）
        pkg.__doc__ = "Tavotto native bridge 私有引擎命名空间（见 bridgeboot）"
        sys.modules[PRIVATE_PKG] = pkg

    todo = [n for n in names if not hasattr(pkg, n)]
    if not todo:
        return pkg

    saved_path = list(sys.path)
    # **装载窗口里不写字节码**（QA REL-01-B1）：`engine_dir` 就是安装目录（macOS 上在签过名的
    # `.app` 里），SourceFileLoader 会把 `__pycache__` 写在源码旁边——一个 .pyc 就让
    # `codesign --verify` 失败、下次启动「应用已损坏」。内置 runtime 有 `-B` 挡着，**用户的
    # 解释器没有**，而我们不能给它加 `-B`（那会关掉他整个进程的字节码缓存，native 档更是明令
    # 不加任何标志）。所以只关这一段、finally 里还原：用户自己的模块照常缓存。
    # 看护：tests/test_install_dir_bytecode_free.py（两条边都钉）。
    saved_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    saved_top = {n: sys.modules.get(n) for n in _TOPLEVEL_TO_RESTORE}
    saved_present = {n: (n in sys.modules) for n in _TOPLEVEL_TO_RESTORE}
    sys.path.insert(0, engine_dir)
    # 装载窗口内，把每个平铺名摆成**我们要的那一份**：
    #
    # * 已经装过的（上一阶段）→ 摆回去。分两阶段装是有意的（第一阶段不许碰
    #   matplotlib），代价是第一阶段结束时顶层名字已经被收回了——第二阶段的
    #   `figsession` 里那句 `import figcapture` 于是会**再装一份**。两份
    #   figcapture 不会当场报错（常量字符串相等），它会在别处以「捕获表对不上」
    #   的形状出现，而那时没人会想到模块身份。
    # * **用户已经 import 过的同名模块 → 挪开**。用户项目里就可能有一个
    #   `figsession.py`，他 `import figsession` 之后 `sys.modules` 里坐着的是
    #   他那份；我们再 `importlib.import_module("figsession")` 拿到的**也是
    #   他那份**（import 系统先查 sys.modules，根本不会走 sys.path）。表现是
    #   `AttributeError: module 'tavotto_bridge_runtime.figsession' has no
    #   attribute 'LiveFigureSession'`——指向完全错误的方向。窗口结束时逐个
    #   还原，用户那份一个字节没动。
    #
    # 看护：test_bridge_namespace.py 的 two_phase / user_modules_win 两条。
    for name in _TOPLEVEL_TO_RESTORE:
        already = getattr(pkg, name, None)
        if already is not None:
            sys.modules[name] = already
        else:
            sys.modules.pop(name, None)
    try:
        # 平铺 import：引擎模块之间就是这么互相引用的（`manifest` 里
        # `import pathgeom`、`from overrides import …`）。装载期让它们照旧
        # 解析，装完再整体搬家——比逐个改成相对 import 的侵入面小得多，
        # 而且 safe worker 那条路一个字节都不用动。
        loaded = {n: importlib.import_module(n) for n in todo}
        for name, mod in loaded.items():
            # `__name__` 改成带包前缀的：`overrides._sibling()` 按它解析兄弟
            # 模块，traceback 里也如实显示这是 Tavotto 的私有副本而不是用户的。
            mod.__name__ = f"{PRIVATE_PKG}.{name}"
            sys.modules[f"{PRIVATE_PKG}.{name}"] = mod
            setattr(pkg, name, mod)
    finally:
        # **还原走 finally，不走顺序执行。** 装载抛异常（缺 numpy、matplotlib
        # 版本不兼容、磁盘错误……）是完全可能的，而那时用户的 `sys.path` 与
        # 顶层模块名还被我们挪着——他之后的 `import manifest` 会拿不到自己
        # 那份，报出来的错与真实原因（"引擎没装起来"）毫无关系。
        sys.path[:] = saved_path
        sys.dont_write_bytecode = saved_dont_write
        # 顶层名字逐个还原（原本没有的删掉）。到这一步为止引擎模块之间的
        # 引用早已绑进各自的 globals，删名字不影响它们；唯一会在运行期再查
        # 名字的是 overrides 那两处 late import，它们走 `_sibling()`。
        for name in _TOPLEVEL_TO_RESTORE:
            if saved_present.get(name):
                sys.modules[name] = saved_top[name]
            else:
                sys.modules.pop(name, None)
    return pkg


class PostImportHook:
    """「某个模块 import 完成的那一刻」回调——**自己不 import 任何东西**。

    实现：往 `sys.meta_path` 最前面放一个 finder，它对目标名字向**后面的**
    finder 要 spec（跳过自己，不递归），然后把 spec 的 loader 包一层，在
    `exec_module` 之后调回调。

    为什么不 patch `builtins.__import__`：`importlib.import_module()` 不经过
    它（matplotlib 内部大量使用），钩子会在最需要的时候安静地不响。
    meta_path 是唯一一条**所有** import 都必经的路。

    为什么不轮询 `sys.modules`：`plt.show()` 是在脚本执行**中间**调用的，
    等脚本跑完再看已经晚了。
    """

    def __init__(self, callbacks: dict):
        self._callbacks = dict(callbacks)
        self._loading: set = set()
        self._installed = False

    # ---- MetaPathFinder ----
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._callbacks or fullname in self._loading:
            return None
        self._loading.add(fullname)
        try:
            for finder in sys.meta_path:
                if finder is self:
                    continue
                found = getattr(finder, "find_spec", None)
                if found is None:
                    continue
                spec = found(fullname, path, target)
                if spec is not None:
                    break
            else:
                return None
        finally:
            self._loading.discard(fullname)
        if spec is None or spec.loader is None:
            return None
        cb = self._callbacks[fullname]
        spec.loader = _LoaderProxy(spec.loader, cb)
        return spec

    # ---- 生命周期 ----
    def install(self) -> None:
        """挂上钩子，并对**已经 import 过的**目标立即补一次回调。

        补这一次是必须的：sitecustomize 形态下 `site` 可能已经拉起了某个
        目标模块，而 bridge runner 形态下用户环境的 `usercustomize` 也可能
        提前 import 了 matplotlib。少补这一次的表现是「有时候钩不上」。
        """
        if self._installed:
            return
        sys.meta_path.insert(0, self)
        self._installed = True
        for name, cb in list(self._callbacks.items()):
            mod = sys.modules.get(name)
            if mod is not None:
                cb(mod)

    def uninstall(self) -> None:
        """摘掉钩子。**必须可卸载**——留一个 finder 在用户进程里，就等于让
        Tavotto 参与用户之后每一次 import 的解析。"""
        if not self._installed:
            return
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        self._installed = False


class _LoaderProxy:
    """转发给真 loader，`exec_module` 之后调回调。

    只实现 import 系统真正会用到的那几个方法并把其余属性转发过去：
    loader 的接口在不同 finder 上并不齐（zipimport / 命名空间包 / 各种
    第三方 finder），照单转发比列白名单稳。
    """

    def __init__(self, real, callback):
        self._real = real
        self._callback = callback

    def create_module(self, spec):
        return self._real.create_module(spec)

    def exec_module(self, module):
        self._real.exec_module(module)
        self._callback(module)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ---------------------------------------------------------------------------
# 跳过「找到了但读不了」的 import 位置（ADR 0153）
# ---------------------------------------------------------------------------
#: CPython 在 `sys.path` 某处找到了模块、却读不了它（`PermissionError`）时**不会**接着往后找，而是直接失败；
#: 同一进程里第二次 import 还会撞上半初始化的父包。用户脚本往 `sys.path` 里塞了一个当前账户没权限读的项目外目录
#: （别的账户建的依赖夹）时，`import pandas` 就这样挂在 `tzdata\__init__.py` 上，而 `python fig.py` 一样会挂——
#: 这里是**有意偏离**那条契约（用户裁决，ADR 0153）：只在「spec 找到了、origin 打开抛 PermissionError」时生效。

#: 一次进程里最多留这么多条跳过记录（同一模块同一位置只记一次）
SKIP_EVENT_LIMIT = 64

_FILE_LOADERS = (
    importlib.machinery.SourceFileLoader,
    importlib.machinery.SourcelessFileLoader,
    importlib.machinery.ExtensionFileLoader,
)


def _norm(path) -> str | None:
    """路径的比较形（绝对化 + 大小写/分隔符规整）；`sys.path` 里不是字符串的条目回 None。"""
    if not isinstance(path, str):
        return None
    try:
        return os.path.normcase(os.path.abspath(path or "."))
    except (OSError, ValueError):
        return None


def _unreadable(path) -> bool:
    """`path` 打开读是否抛 `PermissionError`。别的错误（不存在 / 是目录 / 坏盘）一律不算——交回原来的 import 路径。"""
    try:
        with open(path, "rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _hit_entry(spec, entries) -> str | None:
    """`spec` 是从 `entries` 里哪一条命中的（比较形）；认不出回 None。

    单文件模块：origin 的目录就是条目；包 / 子包（`submodule_search_locations` 非空）：origin 的上上级
    才是条目。命名空间包没有 origin，不会走到这里。"""
    origin = _norm(spec.origin)
    if origin is None:
        return None
    where = os.path.dirname(origin)
    if spec.submodule_search_locations is not None:
        where = os.path.dirname(where)
    return where if where in {_norm(e) for e in entries} else None


def _cache_usable(spec) -> bool:
    """源码读不了时，`spec.cached` 的字节码 CPython 能不能真的用上（`SourceLoader.get_code` 的规则）。

    读得了头、magic 对、marshal 解得开，并且：时间戳型要和源码的 mtime / size 对得上（源码 `stat` 不了当失效）；
    hash 型 `checked` 要读源码重算——源码读不了所以无效；`unchecked` 直接有效。用不上就照常跳过。"""
    cached = getattr(spec, "cached", None)
    if not isinstance(cached, str):
        return False
    try:
        with open(cached, "rb") as f:
            data = f.read()
    except OSError:
        return False
    import importlib.util
    import marshal

    if len(data) < 16 or data[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(data[4:8], "little")
    if flags & 0b1:  # hash 型
        if flags & 0b10:  # checked：要读源码重算 hash，而源码读不了
            return False
    else:
        try:
            st = os.stat(spec.origin)
        except OSError:
            return False
        if int.from_bytes(data[8:12], "little") != (
            int(st.st_mtime) & 0xFFFFFFFF
        ) or int.from_bytes(data[12:16], "little") != (st.st_size & 0xFFFFFFFF):
            return False
    try:
        marshal.loads(data[16:])
    except (EOFError, ValueError, TypeError):
        return False
    return True


class UnreadableImportSkipper:
    """`sys.meta_path` 上、**紧挨 `PathFinder` 之前**的查找器：命中的位置读不了就换下一个位置继续找。

    放在 `BuiltinImporter` / `FrozenImporter` 之后：内置与冻结模块的优先级一字不变。其余情况它只是替
    `PathFinder` 做同一次查找（`PathFinder.find_spec(name, path)` 本来就是紧接着的那一步），找到的 spec
    原样返回——只多一次 `open(origin)`。

    * 只在命中的 spec 是文件加载器（源码 / 无源码字节码 / 扩展）、且 origin 打开抛 `PermissionError`、
      且它的缓存字节码也读不了时才生效；能读的路径、找不到的模块、命名空间包、别的异常一律不碰。
    * 去掉候选是在**副本**上做：用户的 `sys.path` / 包的 `__path__` 本身不动。
    * 跳过之后后面没有可读的位置了：返回**最初**命中的那个 spec，让 loader 原样抛最初的 `PermissionError`。
    * 每次成功跳过记一条事件（`events`，模块名 + 被跳过的位置 + 错误类别），并调 `on_skip`。
    """

    def __init__(self, on_skip=None):
        self.events: list[dict] = []
        self._seen: set = set()
        #: 查找器当场登记的「命中了但读不了」的模块文件（origin 与缓存的比较形）：失败事实只认这些文件
        #: （脚本自己读写该目录下的数据文件撞到权限错误不是 import 失败）
        self.blocked_files: set = set()
        self._on_skip = on_skip
        self._local = threading.local()
        self._installed = False

    # ---- MetaPathFinder ----
    def find_spec(self, fullname, path=None, target=None):
        if getattr(self._local, "busy", False):
            return None
        self._local.busy = True
        try:
            return self._find(fullname, path, target)
        finally:
            self._local.busy = False

    def _find(self, fullname, path, target):
        pathfinder = importlib.machinery.PathFinder
        # 常规路径与 `PathFinder` 紧接着做的那一次查找逐字相同（`path` 原样传，None 时它自己读 `sys.path`）
        first = pathfinder.find_spec(fullname, path, target)
        if first is None or not self._blocked(first):
            return first
        entries = list(sys.path if path is None else path)
        spec = first
        skipped: list[str] = []
        # 每一轮至少去掉一个条目，所以轮数不会超过条目数
        for _ in range(len(entries)):
            hit = _hit_entry(spec, entries)
            if hit is None:
                return first
            skipped.append(hit)
            entries = [e for e in entries if _norm(e) != hit]
            spec = pathfinder.find_spec(fullname, entries, target)
            if spec is None:
                return first
            if not self._blocked(spec):
                self._note(fullname, skipped)
                return spec
        return first

    def _blocked(self, spec) -> bool:
        if not isinstance(spec.origin, str) or not isinstance(spec.loader, _FILE_LOADERS):
            return False
        if not _unreadable(spec.origin):
            return False
        # 源码读不了但缓存字节码按 CPython 自己的规则仍然用得上时，加载本来就能成：不当成读不了
        if _cache_usable(spec):
            return False
        for path in (spec.origin, getattr(spec, "cached", None)):
            norm = _norm(path)
            if norm:
                self.blocked_files.add(norm)
        return True

    def _note(self, fullname: str, skipped: list[str]) -> None:
        for entry in skipped:
            key = (fullname, entry)
            if key in self._seen or len(self.events) >= SKIP_EVENT_LIMIT:
                continue
            self._seen.add(key)
            event = {"module": fullname, "entry": entry, "error": "PermissionError"}
            self.events.append(event)
            if self._on_skip is not None:
                try:
                    self._on_skip(event)
                except Exception:  # noqa: BLE001 — 记录失败绝不影响 import
                    pass

    # ---- 生命周期 ----
    def install(self) -> bool:
        """挂在 `PathFinder` 之前；`meta_path` 里没有 `PathFinder`（被人换掉了）就不装，回 False。"""
        if self._installed:
            return True
        try:
            index = sys.meta_path.index(importlib.machinery.PathFinder)
        except ValueError:
            return False
        sys.meta_path.insert(index, self)
        self._installed = True
        return True

    def uninstall(self) -> None:
        if not self._installed:
            return
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        self._installed = False


_SKIPPER: UnreadableImportSkipper | None = None


def install_unreadable_import_skipper(on_skip=None) -> UnreadableImportSkipper:
    """safe worker 与 native bridge 的**同一个**装配点（ADR 0153）。重复调用回同一个实例。"""
    global _SKIPPER
    if _SKIPPER is None:
        _SKIPPER = UnreadableImportSkipper(on_skip)
        _SKIPPER.install()
    return _SKIPPER


def skip_events() -> list[dict]:
    """此刻为止的跳过记录（没装 / 没发生都是空表）。"""
    return list(_SKIPPER.events) if _SKIPPER is not None else []


def _top_module(rel: str) -> str:
    """`sys.path` 目录之下的相对路径（`/tzdata/__init__.py`、`six.py`）→ 顶层 import 名；不是标识符形状回空串。"""
    first = rel.replace("\\", "/").strip("/").split("/")[0]
    name = first.split(".")[0]
    return name if name.isidentifier() and name.isascii() else ""


def unreadable_folder_fact(
    exc: BaseException, baseline, project_roots, interpreter_dirs=(), blocked_files=None
) -> dict:
    """失败是不是「脚本加进 `sys.path` 的项目外目录读不了」：是则回 `{"folder": 目录名}`，否则 `{}`。

    判据（全部成立才算）：异常链里有 `PermissionError`（errno 13）且带文件名；文件落在某个 `sys.path` 目录下；
    该目录是脚本运行期间新加的（不在 `baseline` 里——启动时的 `sys.path` 快照）；它不在任何项目根之下，
    也不在解释器自己的包目录 / 前缀下（那种读不了换环境才可能有用）。给目录**名**与读不了的顶层模块名（`module`，
    认得出才有），不带路径。

    另需**import 失败的证据**：`PermissionError` 的文件必须是查找器当场登记过的「命中了但读不了」的模块文件
    （`blocked_files`，默认取进程里装着的那个查找器）；脚本自己读写该目录下的数据文件撞到权限错误不出这个事实。"""
    import errno

    evidence = (
        blocked_files
        if blocked_files is not None
        else (_SKIPPER.blocked_files if _SKIPPER is not None else set())
    )
    base = {_norm(p) for p in baseline}
    roots = [r for r in (_norm(p) for p in project_roots) if r]
    skip_under = [r for r in (_norm(p) for p in interpreter_dirs) if r]

    def under(path: str, root: str) -> bool:
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    cur, seen = exc, 0
    while cur is not None and seen < 16:
        if isinstance(cur, PermissionError) and cur.errno == errno.EACCES:
            target = _norm(cur.filename) if isinstance(cur.filename, str) else None
            # 证据：这个文件是查找器当场登记过的「命中了但读不了」的模块文件；脚本自己读写数据文件撞到的不算
            if target and target in evidence:
                for entry in list(sys.path):
                    norm = _norm(entry)
                    if not norm or norm in base or not under(target, norm):
                        continue
                    if any(under(norm, r) or under(r, norm) for r in roots + skip_under):
                        continue
                    fact = {"folder": os.path.basename(os.path.abspath(entry)) or norm}
                    module = _top_module(os.path.abspath(cur.filename)[len(norm) :])
                    if module:
                        fact["module"] = module
                    return fact
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return {}
