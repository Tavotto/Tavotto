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

`overrides` 里那两处 late import（`from manifest import _ordered_axes`）
因此不能是裸名——它们在**用户代码跑起来之后**才执行，裸名会命中用户的文件。
那两处走 `overrides._sibling()`，按模块自己的包前缀解析（safe worker 的
平铺形态下前缀为空，行为一个字节没变）。

## 二、为什么不能提前 import pyplot

用户脚本有权决定后端：

    import matplotlib
    matplotlib.use("Agg")          # ← 这一句只在 pyplot 还没 import 时是纯的
    import matplotlib.pyplot as plt

bridge 只要先 import 了 pyplot，`use()` 就变成 `switch_backend()`——语义、
告警、乃至（视 matplotlib 版本而定）活着的 Figure 都可能不一样。实测
matplotlib 3.10.8 的 `switch_backend` 源码里确实有 `close("all")`，只是当前
版本走不到那条分支；**这种"当前版本碰巧没事"的事实不该被依赖**。

所以钩子靠 `sys.meta_path` 上的一个**后置 import 钩子**安装：它不 import
任何东西，只在别人 import 到 `matplotlib.figure` / `matplotlib.pyplot`
**完成的那一刻**回调。用户什么时候 import，钩子什么时候生效；用户从不
import matplotlib，钩子一辈子不响。
"""

from __future__ import annotations

import importlib
import os
import sys
import types

__all__ = [
    "PRIVATE_PKG",
    "ENGINE_SIBLINGS",
    "PostImportHook",
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
ENGINE_SIBLINGS = ("figcapture", "patchspec", "pathgeom", "overrides", "manifest")

#: 装载后必须还给用户的顶层名字（= ENGINE_SIBLINGS + 它们内部会平铺 import 的）
_TOPLEVEL_TO_RESTORE = (*ENGINE_SIBLINGS, "figsession", "wireproto")


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
    finally:
        sys.path[:] = saved_path

    for name, mod in loaded.items():
        # `__name__` 改成带包前缀的：`overrides._sibling()` 按它解析兄弟模块，
        # traceback 里也如实显示这是 Tavotto 的私有副本而不是用户的模块。
        mod.__name__ = f"{PRIVATE_PKG}.{name}"
        sys.modules[f"{PRIVATE_PKG}.{name}"] = mod
        setattr(pkg, name, mod)

    # 顶层名字**逐个还原**（原本没有的删掉）。到这一步为止引擎模块之间的
    # 引用早已绑进各自的 globals，删名字不影响它们；唯一会在运行期再查名字
    # 的是 overrides 那两处 late import，它们走 `_sibling()`。
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
