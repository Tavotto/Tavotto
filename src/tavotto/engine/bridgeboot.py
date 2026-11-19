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
    "GuiBackendNeutralizer",
    "install_gui_backend_neutralizer",
    "gui_backend_events",
    "unreadable_folder_fact",
    "unreadable_folder_facts",
    "IgnoredImportPaths",
    "install_ignored_import_paths",
    "ignored_events",
    "foreign_abi_facts",
    "image_format_facts",
    "image_format_names",
    "image_mismatch_of_file",
    "sniff_image_format",
    "image_format_of_extension",
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
    "inputdeclaration",
    "cmddiag",
    "wireproto",
    "previewbudget",
    "preview_complexity",
    "preview_hybrid",
    "teedraw",
)


def make_run_original(path: str):
    """启动脚本（ADR 0194 §二）里可直接调用的 `run_original()`：在调用方那份全局里执行**用户原脚本**的代码。

    safe（worker）与 native（bridge）同一份：启动脚本按「执行另一份字节、身份仍是原脚本」装载，`__file__` 是原脚本；
    `runpy.run_path(__file__)` 在 safe 下会被「换字节」那一层再次指回启动脚本（无限递归），手写 `exec(compile(open(...)))`
    又容易写错——由 Tavotto 注入这个函数，模型只写前置的几行。读原件用内置 `open`（不经 `io.open_code` 的换字节），
    编译带 `dont_inherit=True`（不把引擎自己的 `from __future__` 传给用户代码）。"""

    def run_original(globals_=None):
        g = globals_ if globals_ is not None else sys._getframe(1).f_globals
        with open(path, "rb") as fh:
            code = compile(fh.read(), path, "exec", dont_inherit=True)
        exec(code, g)

    return run_original


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


class GuiBackendNeutralizer:
    """脚本写死「交互（GUI）后端」时不切换，保持非交互后端（ADR 0195，维护者 2026-11-19 裁决）。

    Tavotto 永远不弹窗口：`matplotlib.use("TkAgg")` / `"Qt5Agg"` / `"MacOSX"` 之类在没有对应 GUI 库的环境里只会以
    `missing_dependency` 失败，有的环境里还会进 GUI 主循环。**只对 GUI 后端**这样做（与 ADR 0153 同类的有意偏离，
    native 「与 `python fig.py` 一致」的契约在此一点上让位）；Agg / pdf / svg / ps / cairo / `module://…` 等
    非交互或判不出的后端名原样交给 matplotlib。

    * **判据只有一处** `is_gui_backend`：以 matplotlib 自己的后端登记表（3.9+ `backend_registry` 的 INTERACTIVE 过滤，
      旧版 `rcsetup.interactive_bk`）为准，大小写不敏感；登记表读不到 / 为空 = 判不出 = 不动。
    * **两处入口**：`matplotlib.use`（`patch_matplotlib`）与 `pyplot.switch_backend`（`patch_pyplot`；pyplot 首次建图时
      自己也经它按 `rcParams["backend"]` 切后端，所以 `rcParams["backend"] = GUI`、`MPLBACKEND=GUI`、matplotlibrc 里写
      GUI 后端也一并被它挡住）。safe worker 装在 `import matplotlib` 之后、可信方法面基线冻结之前；native 经
      `PostImportHook` 在用户 import 到的那一刻装——**自己不 import matplotlib**。
    * **只在需要时动手**：当前后端若已是显式的非交互名（用户先 `use("pdf")`）就什么都不切；否则（safe 的 Agg、native
      未定的 auto、或已经是 GUI 名）切到 `agg`。
    * **留痕**：`events` 只含后端名（落在 GUI 登记表这个闭集里，不含路径），随回执 `runtime.gui_backends_neutralized`。
    * 包装函数定义在引擎目录里：`figcapture` 的方法面指纹对已基线的模块要求包装先于基线冻结，对首次才见到的模块
      认引擎目录来源（见 `figcapture._module_origin_ok`）——所以不会被判成「脚本 patch 了 matplotlib」。
    """

    MARK = "_tavotto_gui_backend_neutralizer"

    def __init__(self, on_event=None):
        self._on_event = on_event
        self._gui = (
            None  # frozenset | None：首次用到时才问 matplotlib（不在装钩子时 import 任何东西）
        )
        self.events: list[str] = []

    # ---- 判据（唯一出处） ----
    def _gui_names(self) -> frozenset:
        if self._gui is None:
            names: set = set()
            try:
                from matplotlib.backends import BackendFilter, backend_registry  # noqa: PLC0415

                names = {
                    str(n).lower() for n in backend_registry.list_builtin(BackendFilter.INTERACTIVE)
                }
            except Exception:  # noqa: BLE001 —— 旧版 matplotlib：回到 rcsetup 的老清单
                try:
                    from matplotlib import rcsetup  # noqa: PLC0415

                    names = {str(n).lower() for n in rcsetup.interactive_bk}
                except Exception:  # noqa: BLE001
                    names = set()
            self._gui = frozenset(names)
        return self._gui

    def is_gui_backend(self, name) -> bool:
        """`name` 是不是 matplotlib 登记的交互后端名（判不出一律 False）。"""
        return isinstance(name, str) and name.lower() in self._gui_names()

    def _current_raw(self, mpl):
        """rcParams 里**还没解析**的后端设置：显式名（str）或 None（auto / 读不到）。不触发后端解析。"""
        try:
            rc = mpl.rcParams
            raw = rc._get_backend_or_none()
        except Exception:  # noqa: BLE001
            try:
                raw = dict.__getitem__(mpl.rcParams, "backend")
            except Exception:  # noqa: BLE001
                return None
        return raw if isinstance(raw, str) else None

    def _note(self, name: str) -> None:
        key = name.lower()
        if key not in self.events:
            self.events.append(key)
        if self._on_event is not None:
            try:
                self._on_event({"backend": key})
            except Exception:  # noqa: BLE001
                pass

    def _neutralize(self, mpl, name: str, real) -> bool:
        """脚本要 GUI 后端：记一笔；当前不是显式非交互名就切 `agg`。返回 True = 已处理（调用方不再转给原函数）。"""
        self._note(name)
        cur = self._current_raw(mpl)
        if cur is not None and not self.is_gui_backend(cur):
            return True
        try:
            real("agg")
        except Exception:  # noqa: BLE001 —— 切 agg 失败也不让 GUI 名漏过去
            pass
        return True

    # ---- 装配 ----
    def patch_matplotlib(self, mpl) -> None:
        real = getattr(mpl, "use", None)
        if real is None or getattr(real, self.MARK, False):
            return
        this = self

        def use(backend, *args, **kwargs):
            if this.is_gui_backend(backend):
                this._neutralize(mpl, backend, real)
                return None
            return real(backend, *args, **kwargs)

        setattr(use, self.MARK, True)
        use.__name__ = getattr(real, "__name__", "use")
        use.__doc__ = getattr(real, "__doc__", None)
        mpl.use = use

    def patch_pyplot(self, plt) -> None:
        real = getattr(plt, "switch_backend", None)
        if real is None or getattr(real, self.MARK, False):
            return
        this = self
        mpl = sys.modules.get("matplotlib")
        if mpl is None:
            return

        def switch_backend(newbackend, *args, **kwargs):
            if this.is_gui_backend(newbackend):
                this._neutralize(mpl, newbackend, real)
                return None
            return real(newbackend, *args, **kwargs)

        setattr(switch_backend, self.MARK, True)
        switch_backend.__name__ = getattr(real, "__name__", "switch_backend")
        switch_backend.__doc__ = getattr(real, "__doc__", None)
        plt.switch_backend = switch_backend


_GUI_NEUTRALIZER: GuiBackendNeutralizer | None = None


def install_gui_backend_neutralizer(on_event=None) -> GuiBackendNeutralizer:
    """safe worker 与 native bridge 的**同一个**装配点（ADR 0195）。重复调用回同一个实例；**不 import matplotlib**。"""
    global _GUI_NEUTRALIZER
    if _GUI_NEUTRALIZER is None:
        _GUI_NEUTRALIZER = GuiBackendNeutralizer(on_event)
    return _GUI_NEUTRALIZER


def gui_backend_events() -> list[str]:
    """此刻为止被中和的 GUI 后端名（没装 / 没发生都是空表）。"""
    return list(_GUI_NEUTRALIZER.events) if _GUI_NEUTRALIZER is not None else []


def _top_module(rel: str) -> str:
    """`sys.path` 目录之下的相对路径（`/tzdata/__init__.py`、`six.py`）→ 顶层 import 名；不是标识符形状回空串。"""
    first = rel.replace("\\", "/").strip("/").split("/")[0]
    name = first.split(".")[0]
    return name if name.isidentifier() and name.isascii() else ""


def unreadable_folder_fact(
    exc: BaseException, baseline, project_roots, interpreter_dirs=(), blocked_files=None
) -> dict:
    """公开事实（只有目录名与模块名）；判据与私有部分见 `unreadable_folder_facts`。"""
    return unreadable_folder_facts(exc, baseline, project_roots, interpreter_dirs, blocked_files)[0]


def unreadable_folder_facts(
    exc: BaseException, baseline, project_roots, interpreter_dirs=(), blocked_files=None
) -> tuple[dict, dict]:
    """失败是不是「脚本加进 `sys.path` 的项目外目录读不了」：是则回 `{"folder": 目录名}`，否则 `{}`。

    判据（全部成立才算）：异常链里有 `PermissionError`（errno 13）且带文件名；文件落在某个 `sys.path` 目录下；
    该目录是脚本运行期间新加的（不在 `baseline` 里——启动时的 `sys.path` 快照）；它不在任何项目根之下，
    也不在解释器自己的包目录 / 前缀下（那种读不了换环境才可能有用）。给目录**名**与读不了的顶层模块名（`module`，
    认得出才有），不带路径。

    另需**import 失败的证据**：`PermissionError` 的文件必须是查找器当场登记过的「命中了但读不了」的模块文件
    （`blocked_files`，默认取进程里装着的那个查找器）；脚本自己读写该目录下的数据文件撞到权限错误不出这个事实。

    回 `(公开事实, 私有事实)`；不成立两个都是 `{}`。私有事实 `{"path": 目录完整路径, "file": 读不了的文件}` 只给父进程
    登记「一键修复权限」的服务端令牌（ADR 0153 修订 2026-10-30，`engine/folderrepair`）：它从不进公开投影与遥测，
    敏感运行里 worker 的白名单也不放行它。"""
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
                    private = {
                        "path": os.path.abspath(entry),
                        "file": os.path.abspath(cur.filename),
                    }
                    return fact, private
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return {}, {}


# --------------------------------------------------------------------------- ADR 0154：外来 ABI 的私有依赖目录
#
# 脚本把一个 `pip install --target` 出来的目录插到 `sys.path` 最前，里面的编译扩展是为**别的** Python 小版本
# 编的（`.cp312-win_amd64.pyd` 配 3.14 的解释器）。导入系统对这些文件视而不见，于是 `from . import _ext` 一类
# 的导入就以 ImportError 收场，而环境里本来完好的同名包被那个目录盖住了。换 Python 版本救不了（那个目录
# 永远是为别的版本编的）；让那个目录不生效才对。判据以文件系统事实为主（扩展文件名上的 ABI 标签），不解析报错文字。


def _ext_abi_of(name: str) -> tuple[int, int] | None:
    """编译扩展文件名上的 CPython ABI 标签 → (主, 次)；不是带版本标签的扩展、`abi3`、无标签的一律回 None。

    `x.cp312-win_amd64.pyd` / `x.cpython-312-x86_64-linux-gnu.so` / `x.cpython-313t-darwin.so`（自由线程的 `t` 忽略）。"""
    if not (name.endswith(".pyd") or name.endswith(".so")):
        return None
    parts = name.split(".")
    if len(parts) < 3 or "abi3" in parts[1:]:
        return None
    tag = parts[1]
    for prefix in ("cpython-", "cp"):
        if tag.startswith(prefix):
            digits = ""
            for ch in tag[len(prefix) :]:
                if not ch.isdigit():
                    break
                digits += ch
            if len(digits) >= 2:
                return int(digits[0]), int(digits[1:])
            return None
    return None


def _foreign_abis_in(path: str, limit: int = 4000) -> list[str]:
    """`path`（文件或目录，不跟随符号链接）里 ABI 标签与**当前解释器**不符的编译扩展 → 版本串（"3.12"）的有序去重表。

    只认带 CPython 版本标签的文件名（`abi3` 与无标签的 `.so` / `.pyd` 当前解释器都接受，不算）；标签等于当前
    小版本的不算。最多看 `limit` 个目录项，爆预算就用已看到的。"""
    here = tuple(sys.version_info[:2])
    found: set[tuple[int, int]] = set()

    def consider(name: str) -> None:
        tag = _ext_abi_of(name)
        if tag is not None and tag != here:
            found.add(tag)

    seen = 0
    if os.path.isfile(path):
        consider(os.path.basename(path))
    else:
        for _root, dirs, files in os.walk(path):
            seen += len(dirs) + len(files)
            for f in files:
                consider(f)
            if seen > limit:
                break
    return [f"{a}.{b}" for a, b in sorted(found)]


def foreign_abi_facts(
    exc: BaseException,
    interpreter_dirs=(),
    protected_dirs=(),
    project_roots=(),
) -> tuple[dict, dict]:
    """失败是不是「`sys.path` 上某个私有目录里的编译扩展是为别的 Python 小版本编的」：是则 `(公开事实, 私有事实)`。

    判据（全部成立才算）：异常链里有 `ImportError`；链上的文件（`ImportError.path` 与各层 traceback 的文件，内层优先）
    落在某个 `sys.path` 目录 D 下；D **不在**解释器自己的前缀 / 包目录之下（`interpreter_dirs`），也不是脚本所在目录、
    项目根或它们的祖先（`protected_dirs`——那是用户自己的模块，忽略它们会让用户同名模块失效）；D 下该文件所属的
    顶层包（或单文件扩展）里有 ABI 标签与当前解释器不符的编译扩展（`abi3` 与无标签的算兼容）。

    公开事实 `{"folder": 显示名, "abi": "3.12", "current": "3.14", "module"?: 顶层模块名}`：显示名是相对项目根的
    POSIX 路径（在项目里）或目录名（在项目外），不带绝对路径；私有事实 `{"path": D 的完整路径}` 只给父进程登记
    「跳过这个文件夹」的服务端令牌（与 `unreadable_folder_facts` 的私有事实同一条纪律）。不成立两个都是 `{}`。"""
    chain: list[BaseException] = []
    cur, seen = exc, 0
    while cur is not None and seen < 16:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
        seen += 1
    if not any(isinstance(e, ImportError) for e in chain):
        return {}, {}
    files: list[str] = []
    for e in chain:
        path = getattr(e, "path", None) if isinstance(e, ImportError) else None
        if isinstance(path, str) and path:
            files.append(path)
        frames = []
        tb = e.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame.f_code.co_filename)
            tb = tb.tb_next
        files.extend(reversed(frames))  # 内层帧优先
    interp = [r for r in (_norm(p) for p in interpreter_dirs) if r]
    protected = [r for r in (_norm(p) for p in protected_dirs) if r]
    roots = [r for r in (_norm(p) for p in project_roots) if r]

    def under(path: str, root: str) -> bool:
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    entries = []
    for e in sys.path:
        n = _norm(e)
        if not n or any(under(n, r) for r in interp):
            continue
        if any(under(p, n) for p in protected):  # D 就是（或包着）脚本目录 / 项目根
            continue
        entries.append((e, n))
    tried: set = set()
    for f in files:
        norm = _norm(f)
        if not norm or not os.path.isabs(f):
            continue
        hits = [(e, n) for e, n in entries if under(norm, n) and norm != n]
        if not hits:
            continue
        entry, base = max(hits, key=lambda h: len(h[1]))
        rel = norm[len(base) :].lstrip(os.sep)
        top = rel.split(os.sep)[0]
        if not top or (entry, top) in tried:
            continue
        tried.add((entry, top))
        abis = _foreign_abis_in(os.path.join(os.path.abspath(entry), top))
        if not abis:
            continue
        absentry = os.path.abspath(entry)
        shown = os.path.basename(absentry) or absentry
        for r in roots:
            if under(_norm(absentry) or "", r) and _norm(absentry) != r:
                shown = os.path.relpath(absentry, r).replace(os.sep, "/")
                break
        fact = {
            "folder": shown,
            "abi": abis[0] if len(abis) == 1 else ",".join(abis[:3]),
            "current": f"{sys.version_info[0]}.{sys.version_info[1]}",
        }
        module = _top_module(os.sep + top)
        if module:
            fact["module"] = module
        return fact, {"path": absentry}
    return {}, {}


# --------------------------------------------------------------------------- 图片内容与扩展名不符（TVD-3MWS）
#
# 用户把 JPEG 存成了 `x.png`：matplotlib 对 .png 扩展名直接用 PNG 解码器、不嗅探内容，于是 `plt.imread("x.png")`
# 以 `SyntaxError: not a PNG file` 收场（直接用 Python 跑同样失败）。失败那一刻把「这张图其实是 JPEG」这条事实交给失败卡和
# AI 修复，用户不必去猜是不是运行方式的问题。判据按模块 / 帧认，不解析消息文本；只读文件前 16 字节，不改任何文件。

#: 扩展名（小写、不带点）→ 格式显示名；同一格式的几种拼写归并
_IMAGE_EXTENSIONS = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "jpe": "JPEG",
    "gif": "GIF",
    "tif": "TIFF",
    "tiff": "TIFF",
    "bmp": "BMP",
    "webp": "WebP",
}
#: 只在这些模块的帧里找被读的图片路径（PIL 自己与 matplotlib 的 imread）
_IMAGE_READER_MODULES = ("PIL", "matplotlib.image")
_IMAGE_PATH_LOCALS = ("fp", "filename", "fname", "file", "path", "fobj")


def image_format_names() -> tuple[str, ...]:
    """认得的图片格式显示名（闭集，给父进程核对事实用）。"""
    return tuple(dict.fromkeys(_IMAGE_EXTENSIONS.values()))


def image_format_of_extension(ext: str) -> str | None:
    """扩展名（带不带点、大小写都行）→ 格式显示名；不是认得的图片格式回 None。"""
    return _IMAGE_EXTENSIONS.get(str(ext or "").lstrip(".").lower())


def sniff_image_format(path: str) -> str | None:
    """读文件前 16 个字节认真实格式（PNG / JPEG / GIF / TIFF / BMP / WebP）；认不出、读不了、不是普通文件回 None。"""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            head = handle.read(16)
    except Exception:  # noqa: BLE001 — 读不了就是不知道，绝不盖掉原来的脚本错误
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if head.startswith(b"BM"):
        return "BMP"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    return None


def image_mismatch_of_file(path: str) -> tuple[str, str, str] | None:
    """`(扩展名所属格式, 嗅探到的格式, 扩展名)`；一致、扩展名不认得、嗅探不出都回 None。"""
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    claimed = image_format_of_extension(ext)
    if claimed is None:
        return None
    actual = sniff_image_format(path)
    if actual is None or actual == claimed:
        return None
    return claimed, actual, ext


def _in_image_reader(frame) -> bool:
    name = frame.f_globals.get("__name__", "")
    return isinstance(name, str) and any(
        name == m or name.startswith(m + ".") for m in _IMAGE_READER_MODULES
    )


def _is_pil_decode_failure(exc: BaseException) -> bool:
    """`PIL.UnidentifiedImageError`，或抛出点（最内层帧）在 PIL 包里的 `SyntaxError`（如 "not a PNG file"）。"""
    cls = type(exc)
    if cls.__name__ == "UnidentifiedImageError" and str(cls.__module__).split(".")[0] == "PIL":
        return True
    if not isinstance(exc, SyntaxError):
        return False
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb.tb_frame
        tb = tb.tb_next
    if last is None:
        return False
    name = last.f_globals.get("__name__", "")
    return isinstance(name, str) and (name == "PIL" or name.startswith("PIL."))


def _path_candidates(chain: list) -> list[str]:
    """异常链各层 traceback 里 PIL / matplotlib.image 帧的 `fp` / `filename` / `fname` 一类局部变量 → 路径串（内层优先）。"""
    out: list[str] = []
    for e in chain:
        frames = []
        tb = e.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        for frame in reversed(frames):
            if not _in_image_reader(frame):
                continue
            local = frame.f_locals
            for key in _IMAGE_PATH_LOCALS:
                value = local.get(key)
                if value is None:
                    continue
                try:
                    if not isinstance(value, (str, bytes)) and not hasattr(value, "__fspath__"):
                        value = getattr(value, "name", None)  # 文件对象；取不到就放弃
                    value = os.fspath(value) if value is not None else None
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(value, str) and value and value not in out:
                    out.append(value)
    return out


def image_format_facts(
    exc: BaseException, project_roots=(), sensitive_tokens=(), *, sandbox_dir=None, script_dir=None
) -> dict:
    """失败是不是「图片内容与扩展名不符」（解码器按扩展名选，文件里其实是别的格式）：是则
    `{"file"?: 项目相对显示名, "claimed": 扩展名所属格式, "actual": 嗅探到的格式, "ext": 扩展名}`，否则 `{}`。

    判据（全部成立才算）：异常链（最多 16 层）里有 PIL 的解码失败（`UnidentifiedImageError`，或抛出点在 PIL 包里的
    `SyntaxError`）；PIL / `matplotlib.image` 帧里取得到被读的文件，且它在 `project_roots`（项目根、脚本目录）之内——
    项目外的一律不报；读文件前 16 字节嗅探出的格式与扩展名所属格式不同。一致 / 嗅探不出 / 读不了 / 任何意外都回 `{}`，
    绝不盖掉原来的脚本错误。敏感运行（`sensitive_tokens` 非空）里文件名与任一 token 有关（casefold 子串，任一方向）就不带
    `file`，只带格式。

    相对路径的换算只镜像 safe worker 的只读回退（`figcapture.install_relative_read_fallback`）这**一条**规则：给了
    `sandbox_dir` / `script_dir`，相对路径按此刻的 cwd 解出来落在沙盒里、而沙盒里没有这个文件——那次 open 一定被回退换成了
    `<脚本目录>/<相对路径>`，就看那一份。脚本切到别的目录、沙盒里有这份文件、或 native（不传 sandbox_dir）都不换算：
    换算不出来的不猜，宁可回 `{}` 保留原来的错误，也不点名一张没被读过的图（#1966 Codex P2）。"""
    try:
        chain: list = []
        cur, seen = exc, 0
        while cur is not None and seen < 16:
            chain.append(cur)
            cur = cur.__cause__ or cur.__context__
            seen += 1
        if not any(_is_pil_decode_failure(e) for e in chain):
            return {}
        roots = []
        for r in project_roots:
            if isinstance(r, str) and r:
                roots.append(os.path.realpath(r))
        if not roots:
            return {}

        def inside(real: str):
            return next(
                (r for r in roots if real != r and real.startswith(r.rstrip(os.sep) + os.sep)),
                None,
            )

        for candidate in _path_candidates(chain):
            real = os.path.realpath(os.path.abspath(candidate))
            base = inside(real)
            if (
                base is None
                and not os.path.isabs(candidate)
                and sandbox_dir
                and script_dir
                and not os.path.lexists(os.path.abspath(candidate))
            ):
                box = os.path.realpath(sandbox_dir)
                here = os.path.realpath(os.path.abspath(candidate))
                if here.startswith(box.rstrip(os.sep) + os.sep):
                    alt = os.path.realpath(os.path.join(script_dir, candidate))
                    if inside(alt) and os.path.isfile(alt):
                        real, base = alt, inside(alt)
            if base is None:
                continue
            mismatch = image_mismatch_of_file(real)
            if mismatch is None:
                continue
            claimed, actual, ext = mismatch
            fact = {"claimed": claimed, "actual": actual, "ext": ext}
            shown = os.path.relpath(real, base).replace(os.sep, "/")
            folded = shown.casefold()
            related = any(
                (t := str(token).casefold()) and (t in folded or folded in t)
                for token in sensitive_tokens
            )
            if not related:
                fact = {"file": shown, **fact}
            return fact
    except Exception:  # noqa: BLE001
        return {}
    return {}


class _FilteredNamespacePath:
    """命名空间包的 `__path__`：照旧随 `sys.path` 动态重算（委托原 `_NamespacePath`），每次读都去掉被忽略的目录。"""

    def __init__(self, inner, ignored) -> None:
        self._inner = inner
        self._ignored = ignored

    def _entries(self) -> list:
        return [e for e in self._inner if not self._ignored(e)]

    def __iter__(self):
        return iter(self._entries())

    def __getitem__(self, index):
        return self._entries()[index]

    def __len__(self) -> int:
        return len(self._entries())

    def __contains__(self, item) -> bool:
        return item in self._entries()

    def append(self, item) -> None:
        self._inner.append(item)

    def __repr__(self) -> str:
        return f"_FilteredNamespacePath({self._entries()!r})"


class IgnoredImportPaths:
    """`sys.meta_path` 上**紧挨 `PathFinder`（及读不了查找器）之前**的查找器：顶层导入时不去用户忽略的目录里找。

    名单是**按项目记住的用户决定**（`engine/pathignore`，经 `--ignore-paths` 交进来），只对名单里的目录生效；
    子模块随包的 `__path__` 自然不进那个目录，所以只管顶层导入（`path is None`）。内置与冻结模块的优先级不变
    （我们在它们之后）；用户的同名模块（脚本目录、项目根）不在名单里，照旧赢。

    * 名单里的目录都不在 `sys.path` 上、或被忽略的目录里压根没有这个名字：返回 None，常规路径一字不变。
    * 被忽略的目录里有这个名字：用**去掉这些目录**的 `sys.path` 副本找（用户的 `sys.path` 本身不动）；找到就用，
      找不到就抛 `ModuleNotFoundError`（不能返回 None——后面的 `PathFinder` 会把被忽略的目录再翻一遍）。
    * 每个 (模块, 目录) 记一条事件（`events`）并调 `on_ignore`。"""

    def __init__(self, paths, on_ignore=None):
        self._forms: set[str] = set()
        self.events: list[dict] = []
        self._seen: set = set()
        self._on_ignore = on_ignore
        self._local = threading.local()
        self._installed = False
        self.set_paths(paths)

    def set_paths(self, paths) -> None:
        forms: set[str] = set()
        for p in paths or ():
            n = _norm(p)
            if n:
                forms.add(n)
                try:
                    forms.add(os.path.normcase(os.path.realpath(p)))
                except (OSError, ValueError):
                    pass
        self._forms = forms

    def _ignored(self, entry) -> bool:
        n = _norm(entry)
        return n is not None and n in self._forms

    def _under_ignored(self, entry) -> bool:
        """`entry` 是被忽略的目录本身或其下的子目录（命名空间包的 `__path__` 条目是 `<根>/<包名>`）。"""
        n = _norm(entry)
        if n is None:
            return False
        return any(n == f or n.startswith(f.rstrip(os.sep) + os.sep) for f in self._forms)

    def find_spec(self, fullname, path=None, target=None):
        if path is not None or not self._forms or getattr(self._local, "busy", False):
            return None
        self._local.busy = True
        try:
            return self._find(fullname, target)
        finally:
            self._local.busy = False

    def _find(self, fullname, target):
        entries = list(sys.path)
        hits = [e for e in entries if self._ignored(e)]
        if not hits:
            return None
        pathfinder = importlib.machinery.PathFinder
        if pathfinder.find_spec(fullname, hits, target) is None:
            return None  # 被忽略的目录里没有它：常规路径的答案与过滤后的相同
        self._note(fullname, hits)
        rest = [e for e in entries if not self._ignored(e)]
        skipper = _SKIPPER
        spec = (
            skipper._find(fullname, rest, target)
            if skipper is not None
            else pathfinder.find_spec(fullname, rest, target)
        )
        if spec is None:
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
        locations = spec.submodule_search_locations
        if locations is not None and not isinstance(locations, list):
            # 命名空间包：`_NamespacePath` 在 `sys.path` 变化 / `invalidate_caches()` 后按**原始** `sys.path` 重算，
            # 被忽略的目录会重新混进 `__path__`，子模块导入（path 不为 None）又绕过本查找器。包一层过滤（Codex #1564）
            spec.submodule_search_locations = _FilteredNamespacePath(locations, self._under_ignored)
        return spec

    def _note(self, fullname: str, hits: list) -> None:
        for entry in hits:
            key = (fullname, _norm(entry))
            if key in self._seen or len(self.events) >= SKIP_EVENT_LIMIT:
                continue
            self._seen.add(key)
            event = {"module": fullname, "entry": entry, "error": "IgnoredByUser"}
            self.events.append(event)
            if self._on_ignore is not None:
                try:
                    self._on_ignore(event)
                except Exception:  # noqa: BLE001 — 记录失败绝不影响 import
                    pass

    def install(self) -> bool:
        """挂在读不了查找器（没有就是 `PathFinder`）之前；两者都不在 `meta_path` 里就不装，回 False。"""
        if self._installed:
            return True
        anchors = [_SKIPPER, importlib.machinery.PathFinder]
        for anchor in anchors:
            if anchor is None:
                continue
            try:
                index = sys.meta_path.index(anchor)
            except ValueError:
                continue
            sys.meta_path.insert(index, self)
            self._installed = True
            return True
        return False


_IGNORED: IgnoredImportPaths | None = None


def install_ignored_import_paths(paths, on_ignore=None) -> IgnoredImportPaths | None:
    """safe worker 与 native bridge 的**同一个**装配点（ADR 0154）。名单为空且没装过就什么都不装（逐字节旧行为）；
    装过之后重复调用只更新名单。"""
    global _IGNORED
    paths = [p for p in (paths or ()) if isinstance(p, str) and p]
    if _IGNORED is None:
        if not paths:
            return None
        _IGNORED = IgnoredImportPaths(paths, on_ignore)
        _IGNORED.install()
    else:
        _IGNORED.set_paths(paths)
    return _IGNORED


def ignored_events() -> list[dict]:
    """此刻为止被「跳过这个文件夹」挡掉的导入记录（没装 / 没发生都是空表）。"""
    return list(_IGNORED.events) if _IGNORED is not None else []
