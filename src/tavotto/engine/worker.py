#!/usr/bin/env python3
"""渲染 worker 子进程（系统 python3，需 matplotlib 科学栈）。

跑一个 fig 脚本一次，把产出的 Figure 常驻内存（live-figure 会话），
之后通过 stdin/stdout 的 JSON 行协议接受指令。**两套信封并存**：

* **协议 v1**（带 `protocol_version` 字段，`pool.py` 走这条）：

      {"protocol_version":1,"request_id":"r-…","worker_generation":3,
       "render_revision":17,"cmd":"render","stem":"Fig1",
       "canonical_patch_hash":"sha256:…","payload":{"patches":[…]}}

  命令：ping / build / render / render_png / preview_png / export /
  cancel / shutdown。generation、revision、hash **worker 只回显不理解**
  （校验归 supervisor）。build / render / export 的成功响应带 `timings`
  （毫秒，见 `_TIMED_COMMANDS`）。完整契约见
  `docs/adr/0003-worker-protocol-v1.md`。

* **legacy**（无 `protocol_version`）：老的扁平信封，行为一字不改——
  手工 `echo '{"cmd":"build"}' | python worker.py …` 调试时用的就是它。

      {"cmd":"build"}                                → 导入脚本、跑入口、捕获全部 Figure
      {"cmd":"override","stem":s,"patches":[...]}    → 应用全量 override，重导出预览 SVG
      {"cmd":"export","stem":s,"patches":[...],
       "path":p,"format":"pdf","dpi":600}            → 全质量导出（供画布合成）
      {"cmd":"ping"} / {"cmd":"shutdown"}

安全措施：
  * cwd 切到沙盒目录——fig6/fig7 等脚本的相对路径写出/glob 删除不会碰真实 figures 目录
  * 拦截 Figure.savefig 与 paper_style.save（import 脚本前安装）——build 期间不写任何图文件
  * 脚本的 stdout 重定向到 stderr，保证协议通道干净
  * 引擎的兄弟模块装进**私有包** `tavotto_bridge_runtime.*`（与 native bridge 同一份
    `bridgeboot`），顶层 `manifest` / `overrides` / `figcapture` … 这些名字**还给用户**——
    用户项目里同名的模块 import 到的是他自己那份（issue #447 / FO19）
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import faulthandler
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig  # 模块层：项目目录进 sys.path 之前就拿住标准库这一份（见 `_INTERPRETER_PACKAGE_DIRS`）
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 引擎模块的装载形态（issue #447 / 统一实施包 FO19，ADR 0057）。
#
# 之前这里是 `sys.path.insert(0, HERE)` 加平铺 `import figcapture / figsession /
# wireproto`，而且 engine 目录**永远留在** `sys.path[0]`、那些模块以顶层名字**永远坐在**
# `sys.modules` 里。用户项目里恰好有 `manifest.py` / `overrides.py` / `config.py`
# 时，脚本的 `import manifest` 拿到的是 Tavotto 的那份（import 系统先查
# `sys.modules`，根本不会走 sys.path）——脚本在终端里好好的，进 Tavotto 就
# AttributeError，而报错指向完全错误的方向。native bridge 早就为此做了私有包装载
# （`bridgeboot`）；safe worker 现在走**同一份**实现：
#
# 1. 把 CPython 自动塞进来的「脚本自己的目录」（= engine 目录）从 `sys.path[0]` 摘掉；
# 2. 按文件路径装 `bridgeboot`（不能靠 sys.path——那正是它要防的事）；
# 3. `load_engine_modules()` 把整条平铺 import 闭包装进 `tavotto_bridge_runtime.*`，
#    装完 `sys.path` 逐字还原、顶层名字还给用户（原本没有就删掉）。
#
# 「装什么」的清单是 `_ENGINE_MODULES`；它必须等于 worker 侧平铺 import 的整条传递闭包
# （`tests/test_runtime_build.py` 从它反推 PyInstaller spec 的 datas，
# `tests/test_import_architecture.py` 把它登记成 worker.py 的边）。漏一个的后果与
# bridge 那边一样：那个模块以真·顶层名字留在用户进程里，或者第二个消费者再装一份。
# ---------------------------------------------------------------------------
if sys.path and os.path.realpath(sys.path[0]) == os.path.realpath(str(HERE)):
    del sys.path[0]


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _norm_path(p) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(p)))


#: 当前解释器的包目录（stdlib / purelib / platlib）。**模块加载时就算好**：`sysconfig.get_paths()` 第一次调用
#: 会惰性 import `_sysconfigdata_*`，放到 build 里项目目录已进 `sys.path` 之后再算，项目里同名的文件会被当成它
#: import——在删除守卫与 savefig 拦截装好之前执行用户代码（Codex #717）。
_INTERPRETER_PACKAGE_DIRS = tuple(
    _norm_path(p)
    for p in {sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")}
    if p
)


def _suppress_project_bytecode(roots) -> None:
    """用户**项目目录里**的源码不写字节码：脚本自己（entry 不是 `__main__` 时按模块 import）与
    它 import 的项目内模块，都不在项目里留 `__pycache__/*.pyc`。

    2026-09-28 Windows 实测：系统 Python 3.12 渲染 `fig_data.py`（entry=main）之后，项目目录里多了
    `__pycache__/fig_data.cpython-312.pyc`——`python fig_data.py` 跑主脚本从不写它。Tavotto 只是
    替用户看图，项目目录里不该因此多出任何东西。

    **只挡项目目录，不是整个进程**（所以不是全程 `-B` / `sys.dont_write_bytecode`）：
    worker 起在用户的解释器上时，脚本运行期间才 import 的 numpy / scipy 子模块照常缓存——
    没预编译的环境（uv 建的 venv 默认不编）关掉它，每次冷启动都要从源码重编
    （`test_only_the_bundled_runtime_gets_b_flag`）。判据落在 `SourceFileLoader.set_data`
    （它在 `SourceFileLoader` 里只被写字节码的那一步调用）：要写的 `.pyc` 落在项目根之下就不写；
    但落在**当前解释器的包目录**（`sysconfig` 的 stdlib / purelib / platlib）之下的照写——项目根里放着
    `.venv` 是常态，那是用户的环境，不是项目源码。只认包目录、不认整个解释器前缀：前缀包住项目
    （项目在 Conda 环境目录里、`/usr/local` 下）或前缀就是项目根（`python -m venv .`）时，项目源码照样是
    项目源码（Codex #717 两轮）。内置 runtime 本来就带 `-B`（`runtime.child_args`），这一段对它是 no-op。

    **装的时刻**：非内置解释器上由 `_startup_bytecode_guard()` 在本模块最前面装（#736：解释器启动、
    site 初始化阶段 import 的项目代码——PYTHONPATH 上的 `sitecustomize.py`、`.pth` import 的项目模块——
    早于 `build()`），`build()` 再按同样的根装一次（幂等：包的是同一个原始 `set_data`）。
    """
    from importlib.machinery import SourceFileLoader  # noqa: PLC0415

    project = tuple(_norm_path(r) for r in roots if r)
    # 只豁免解释器的**包目录**（项目里的 `.venv/lib/.../site-packages`，模块加载时算好）；前缀本身不算——
    # 它可能包住项目，也可能就是项目根
    envs = _INTERPRETER_PACKAGE_DIRS
    real_set_data = getattr(SourceFileLoader.set_data, "_tavotto_real", SourceFileLoader.set_data)

    def set_data(self, path, data, *args, **kwargs):
        try:
            target = _norm_path(path)
        except (OSError, TypeError, ValueError):
            target = ""
        if target and _inside(target, project) and not _inside(target, envs):
            return None
        return real_set_data(self, path, data, *args, **kwargs)

    set_data._tavotto_real = real_set_data
    SourceFileLoader.set_data = set_data


#: 非内置解释器起 safe worker 时带的 `-X` 标记（`runtime.WORKER_BYTECODE_XOPTION` 的镜像，
#: `tests/test_install_dir_bytecode_free.py` 对拍）：「启动期的 `-B` 是 Tavotto 为了挡住启动期 import 的
#: 项目代码加的，守卫装好就放开」。内置 runtime 不带它，`-B` 一直有效（安装目录一个字节都不写）。
_STARTUP_BYTECODE_XOPTION = "tavotto_project_bytecode"


def _argv_roots(argv: list[str]) -> tuple[str, ...]:
    """从命令行取项目根与脚本目录（`main()` 的 `--figures-dir` / `--script`，形状由
    `execspec.worker_argv` 独家产出）。与 `build()` 装守卫用的 `(figures_dir, script.parent)` 是同一对。"""
    roots = []
    for flag in ("--figures-dir", "--script"):
        try:
            value = argv[argv.index(flag) + 1]
        except (ValueError, IndexError):
            continue
        path = os.path.abspath(value)
        roots.append(os.path.dirname(path) if flag == "--script" else path)
    return tuple(roots)


def _startup_bytecode_guard() -> None:
    """#736：项目字节码守卫在**解释器启动之前**就要生效。

    `build()` 里才装守卫的话，PYTHONPATH 本来就含项目目录、项目里又有 `sitecustomize.py`（或
    site-packages 里某个 `.pth` import 了项目模块）时，这些代码在 site 初始化阶段就被 import、写下
    `__pycache__`。所以非内置解释器以 `-B` 起（`runtime.worker_args`，带本标记）：启动期整个进程
    不写字节码；走到这里（worker.py 的第一段）先按命令行上的项目根装守卫，再把字节码写入**还给
    用户**——他自己设了 `PYTHONDONTWRITEBYTECODE`（且解释器认环境变量）就保持不写，否则照常写：
    之后 import 的 numpy / matplotlib / 项目外模块照常缓存（`-B` 一路开着的话，没预编译的环境每次
    冷启动都要重编科学栈）。代价只在启动期：site 初始化时 import 的 site-packages 模块不写缓存
    （每次在内存里编一遍，都是很小的模块）。没有标记（内置 runtime、手工调试）什么都不做。"""
    if _STARTUP_BYTECODE_XOPTION not in getattr(sys, "_xoptions", {}):
        return
    _suppress_project_bytecode(_argv_roots(sys.argv[1:]))
    sys.dont_write_bytecode = bool(
        os.environ.get("PYTHONDONTWRITEBYTECODE") and not sys.flags.ignore_environment
    )


_startup_bytecode_guard()

_boot_spec = importlib.util.spec_from_file_location(
    "tavotto_bridge_boot", os.path.join(str(HERE), "bridgeboot.py")
)
bridgeboot = importlib.util.module_from_spec(_boot_spec)
sys.modules["tavotto_bridge_boot"] = bridgeboot
# 按文件装载同样会把 `__pycache__/bridgeboot.*.pyc` 写进安装目录（QA REL-01-B1）：
# 只在这一下关掉字节码写入，之后还原——理由见 `bridgeboot.load_engine_modules`。
_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    _boot_spec.loader.exec_module(bridgeboot)
finally:
    sys.dont_write_bytecode = _dont_write_bytecode

import matplotlib  # noqa: E402 —— safe 档由 Tavotto 挑解释器，backend 在装引擎之前钉死

matplotlib.use("Agg")
import matplotlib.figure as mfigure  # noqa: E402

#: safe worker 要装进私有包的引擎模块——**整条平铺 import 闭包**，一次装完
#: （safe 档不像 native 那样要分两阶段避开 matplotlib：这里 matplotlib 本来就先 import）。
_ENGINE_MODULES = (
    "executionfact",
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
    "previewbudget",
    "preview_complexity",
    "preview_hybrid",
    "figsession",
    "wireproto",
    "scriptinput",
    "runtime",
    "writetripwire",
)
_PKG = bridgeboot.load_engine_modules(str(HERE), _ENGINE_MODULES)

# Figure 捕获策略（savefig stem 怎么取、跑完之后还活着的 pyplot Figure 怎么
# 补进来、相对路径只读回退）与浏览器 playground **共用同一份实现**。抄一份
# 进来的话，同一个脚本会在两个入口里产出不同的 stem——前端按 stem 索引一切。
figcapture = _PKG.figcapture

# Figure 到手之后的编辑语义（instrument / manifest / override / 渲染 / 导出 /
# 快照还原）与**信封语义**都不是 safe worker 私有的：native bridge（ADR 0020）
# 在用户自己的进程里跑用户的脚本，捕获到 Figure 之后走的必须是同一份实现。
# 抄一份过去就是第二份 manifest builder + 第二套协议语义——总纲原则 1 明令
# 禁止，而分叉的表现是同一张图在两条入口里 gid 不一样（数据级错位）。
figsession = _PKG.figsession
wireproto = _PKG.wireproto
# 脚本里的 input() / sys.stdin / getpass 桥接（ADR 0099）：每一问经会话缓存目录里的文件会合交给父进程
scriptinput = _PKG.scriptinput
# Windows 平台判断与 CREATE_NO_WINDOW 的唯一出处（只依赖标准库，装进私有包不拖闭包）
runtime = _PKG.runtime
executionfact = _PKG.executionfact
# 写入根绊线（#898，ADR 0141）：只有命令行给了 `--write-roots`（MCP 来源）才装；纯标准库，不拖闭包
writetripwire = _PKG.writetripwire

#: 本 worker 的常驻会话。`_patched_savefig` 是模块级函数（要顶掉
#: `Figure.savefig` 这个类属性），拿不到 Worker 实例，只能走模块级引用。
SESSION: "SafeSession | None" = None

_intercept = True
_REAL_SAVEFIG = mfigure.Figure.savefig
#: 本次 build 里 `plt.show()` 时开着的 Figure 账（见 `figcapture.ShownFigures`）。show 钩子是模块级的
#: （装在 pyplot 模块上，拿不到 Worker 实例）；**每次 build 开始时重新建一本**（`_install_show_capture`）——
#: build 失败的会话会被复用、重跑脚本，上一次留下的已关闭 Figure 不许带进这一次。
_SHOWN = figcapture.ShownFigures()
_SAVEFIG_METADATA = figcapture.savefig_metadata_reader(mfigure)
#: 正在用户的 `paper_style.save(fig, stem)` 里：其间的 savefig 一律记到这个 stem 名下
#: （`save` 里存成 `f"{stem}_final.pdf"` 的图库，按文件名取 stem 会让已有 override 挂空）
_SAVE_AS: "str | None" = None
#: 守卫拦下的关键文件操作的账本（P01a）。同样要被模块级的 `_patched_savefig` 够到，所以走模块级引用；
#: build 开始时换成新的一本，脚本跑完 `stop()`。
BLOCKED_OPS: "figcapture.BlockedFileOps | None" = None

#: 协议常量的唯一出处在 `wireproto`；这里 re-export 是为了「手工 echo 一条
#: 请求进来调试」时不必知道模块拆分（`worker.PROTOCOL_VERSION` 是老口径）。
PROTOCOL_VERSION = wireproto.PROTOCOL_VERSION
V1_COMMANDS = wireproto.V1_COMMANDS
ProtocolError = wireproto.ProtocolError
_ms = figsession.ms_since


def _source_stamp(path: Path) -> tuple[int, int, int, str] | None:
    """`(mtime_ns, size, inode, sha256)`；读不了回 None。"""
    try:
        st = os.stat(path)
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino, digest)


@contextlib.contextmanager
def _capture_entry_sha(script: Path, captured: list[str]):
    """import 期间让入口脚本由 loader **只从源码编译**并记下所编译字节的 sha256（不读任何 .pyc）。

    临时包 `SourceFileLoader.get_code`（try/finally 恢复；沿用 `_tavotto_real` 约定避免叠包）。其他模块走原函数；
    `get_code` 之外的 import 流程（spec、`__file__`、`__cached__`、执行）不变——`source_to_code` 与
    `get_data` 都是 `SourceFileLoader` 自己的方法。
    """
    from importlib.machinery import SourceFileLoader  # noqa: PLC0415

    target = _norm_path(script)
    original = SourceFileLoader.get_code
    real_get_code = getattr(original, "_tavotto_real", original)

    def get_code(self, fullname):
        try:
            hit = _norm_path(self.path) == target
        except (OSError, TypeError, ValueError):
            hit = False
        if not hit:
            return real_get_code(self, fullname)
        data = self.get_data(self.path)
        captured.append(hashlib.sha256(data).hexdigest())
        return self.source_to_code(data, self.path)

    get_code._tavotto_real = real_get_code
    SourceFileLoader.get_code = get_code
    try:
        yield
    finally:
        SourceFileLoader.get_code = original


class SafeSession(figsession.LiveFigureSession):
    """safe 档的 LiveFigureSession：引擎自己写盘时摘掉 savefig 拦截。

    build 期间脚本一个图文件都不写（沙盒纪律），但引擎自己的预览 SVG /
    PNG / 导出必须写得出去——`_real_output()` 就是那个窗口。native bridge
    的 savefig 本来就是透传，它用基类那个什么都不做的上下文。
    """

    def real_output(self):
        return _real_output()


def _install_show_capture() -> None:
    """脚本里的 `plt.show()` 先把此刻开着的 Figure 记账，再照原样调原来的 show。

    pyplot 不一定已经 import（纯 OO 脚本永远不会；这里 import 一次要白付几十毫秒），所以 pyplot
    尚未载入时挂一个一次性的 import 钩子，等脚本自己 import 它的那一刻才包 `show`——
    `from matplotlib.pyplot import show` 也在包完之后才绑定。不替换成 no-op：行为（含非交互后端的
    警告）与以前一字不差，只多一笔账。
    """
    import importlib.abc  # noqa: PLC0415

    global _SHOWN
    _SHOWN = figcapture.ShownFigures()

    def wrap(plt) -> None:
        real_show = plt.show
        if getattr(real_show, "_tavotto_show_capture", False):
            return

        def _show_with_capture(*args, **kwargs):
            # 钩子每次都读模块全局 `_SHOWN`（当前这本账），不抓闭包里的旧对象
            _SHOWN.note(plt, SESSION.capture if SESSION is not None else None)
            return real_show(*args, **kwargs)

        _show_with_capture._tavotto_show_capture = True
        plt.show = _show_with_capture

    existing = sys.modules.get("matplotlib.pyplot")
    if existing is not None:
        wrap(existing)
        return

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            if name != "matplotlib.pyplot":
                return None
            sys.meta_path.remove(self)
            spec = importlib.util.find_spec(name)
            loader = getattr(spec, "loader", None)
            if loader is None or not hasattr(loader, "exec_module"):
                return spec
            real_exec = loader.exec_module

            def exec_module(module):
                real_exec(module)
                wrap(module)

            loader.exec_module = exec_module
            return spec

    sys.meta_path.insert(0, _Finder())


def _patched_savefig(self, fname, *args, **kwargs):
    """通用兜底：raw fig.savefig 的脚本也被捕获；同 stem 的 pdf/png 只记一次。

    写进文件对象（BytesIO 等）的不拦：那不是一份图产物，脚本接着要读它
    （`figcapture.savefig_targets_path`）。但在 `paper_style.save(fig, stem)` 里时照样**记账**
    到那个 stem 名下——图库的 save 先存进缓冲区再转 TIFF 时，这就是它唯一的一次 savefig，
    漏记会让 `bbox_inches="tight"` 这类参数看不见、图幅与它生成的 TIFF 对不上（#739 Codex P2）。
    """
    if not _intercept:
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)
    to_path = figcapture.savefig_targets_path(fname)
    if to_path and BLOCKED_OPS is not None:
        # 拦截不落盘：脚本若随后读这个路径（`Image.open('stage.png')`），读到的不是它刚存的（P01a，EX02）
        try:
            from matplotlib.backend_bases import FigureCanvasBase  # noqa: PLC0415

            supported = {str(k).lower() for k in FigureCanvasBase.filetypes}
        except Exception:  # noqa: BLE001
            supported = None
        BLOCKED_OPS.blocked_savefig(
            figcapture.savefig_effective_names(fname, kwargs, getattr(self, "canvas", None)),
            kwargs.get("format"),
            supported,
        )
    stem = _SAVE_AS or figcapture.savefig_stem(fname)  # 不是路径时 savefig_stem 是空串
    if SESSION is not None:
        call = figcapture.savefig_call(fname, kwargs) if stem else None
        observation = SESSION.savefig_observations.record(
            self,
            fname,
            stem,
            call,
            explicit_format=kwargs.get("format") is not None,
            backend=kwargs.get("backend"),
        )
        SESSION.savefig_observations.complete(observation, "intercepted")
        SESSION.note_artifact(observation, stem, self, kwargs.get("bbox_extra_artists"))
    if stem and SESSION is not None:
        SESSION.add_figure(stem, self, figcapture.SOURCE_SAVEFIG)
        SESSION.note_savefig(
            stem,
            self,
            call,
            kwargs.get("bbox_extra_artists"),
            target=figcapture.savefig_target_key(fname, kwargs, getattr(self, "canvas", None)),
            verify=figcapture.savefig_verifier(_REAL_SAVEFIG, self, fname, args, kwargs),
        )
    if not to_path:
        if SESSION is not None:
            SESSION.passthrough_figures.add(id(self))
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)
    return None


def _wrap_paper_style_save(native_save):
    """`paper_style.save(fig, stem, ...)` 捷径（ADR 0098 §四，同时是 ADR 0094 §五.4 的前置修正）。

    以前整个换成一个只登记 stem 的 lambda：用户那份 `save` 从没执行，里面那句
    `savefig(bbox_inches="tight", ...)` 的参数看不见，经 `Figure.savefig` 的钩子也永远不触发。
    现在**照常调用用户那份 `save`**：先按 `stem` 认领（它里面一句 savefig 都没有也照样捕获，
    与以前一样），其间的每一次 savefig 被拦截、不落盘、记到这个 `stem` 名下。`save` 里的
    `plt.close(fig)`、建输出目录（沙盒里）、改样式照常发生——与 `python fig.py` 一致。
    `save` 不是可调用对象的图库照旧只登记（脚本在终端里调用它本来就会报错）。
    """
    if not callable(native_save):

        def _register_only(fig, stem, outdir="figures"):
            return SESSION.add_figure(stem, fig, figcapture.SOURCE_SAVEFIG)

        return _register_only

    def save(fig, stem, *args, **kwargs):
        global _SAVE_AS
        SESSION.add_figure(stem, fig, figcapture.SOURCE_SAVEFIG)
        prev, _SAVE_AS = _SAVE_AS, stem
        try:
            return native_save(fig, stem, *args, **kwargs)
        finally:
            _SAVE_AS = prev

    return save


#: 命令行参数解析库：`SystemExit` 从这些模块的帧里抛出来 = 脚本要参数而 Tavotto 没给。
#: 判帧的**模块来历**（`f_globals["__name__"]` 的顶级包名），不判文件路径——用户把脚本
#: 放在叫 `click/` 或 `fire/` 的目录里，路径分量就会撒谎（评审 #443）；也不判异常文本——
#: argparse 的 usage 是打到 stderr 的，异常对象里只有一个退出码 2。
_CLI_PARSER_MODULES = frozenset(
    {"argparse", "optparse", "getopt", "click", "typer", "docopt", "fire"}
)

#: 脚本以非零 `sys.exit` 结束时的两个稳定 code（ADR 0003 §5 加 code 不升版）。
#: 两者对用户是两件事：前者是「脚本要参数，Tavotto 不带参数运行」——出路是给默认值
#: 或 `tavotto run`；后者是「脚本自己 exit 了」——出路是去掉那句 exit。
SCRIPT_NEEDS_ARGUMENTS = "script_needs_arguments"
#: 脚本要输入而没有人能答（ADR 0099 §五）/ 有一问等到超时、脚本没接住 EOF（§三）。
SCRIPT_NEEDS_INPUT = "script_needs_input"
SCRIPT_INPUT_TIMEOUT = "script_input_timeout"
#: 脚本要读的文件不存在，且说得出是哪个（ADR 0106）：界面据此请用户指认，不是笼统的 script_error。
MISSING_INPUT = "missing_input"
SCRIPT_EXITED = "script_exited"
#: safe 档的守卫拦下了脚本的关键文件操作（删除 / 改名 / 写入真实图库，或被拦截不落盘的 savefig），
#: 脚本随后又观察了受影响的路径（P01a，ADR 0003）。结果可能与直接 Python 不同：不是完整成功。
FILE_OPERATION_BLOCKED = "safe_file_operation_blocked"
#: MCP 来源的 safe 运行里，脚本想写授权根之外（写入根绊线，#898）。**绊线不是沙盒**，盲区见 `writetripwire`。
#: 不属于 `execpolicy.BLOCKED_CODES`：它不会触发「换 native 重跑」的回退（native 没有绊线，换过去等于放行）。
WRITE_OUTSIDE_ROOTS = writetripwire.CODE
#: 载荷里的「该走哪条执行路径」：保留文件语义（native bridge / `tavotto run`）。字符串是桌面侧
#: 接入依据；`adapter_available` 只说「这个 build 失败之后，桌面界面是否已经会自动换那条路径重跑」——
#: 终端里的 `tavotto run` 一直可用，但自动回退没接通之前这个值保持 false（不承诺没接通的东西）。
REQUIRED_EXECUTION_FILE_SEMANTICS = "preserve_file_semantics"


def _raised_by_cli_parser(exc: BaseException) -> bool:
    """`SystemExit` 是不是参数解析库**自己**抛的——只看抛出点（最内层）那一帧。

    看整条栈会把「控制流经过了 click」当成「click 退出的」：一个无参数的 click 命令，
    用户回调里 `sys.exit(7)`，栈里也有好几层 `click.core`（评审 #443）。argparse 的
    `parser.error()` → `self.exit(2)` → `sys.exit` 最内层在 `argparse`；click 的用法错误
    在 `click.core.main` 里 `sys.exit(e.exit_code)`，同样在最内层。
    """
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    module = str(tb.tb_frame.f_globals.get("__name__") or "")
    return module.split(".")[0] in _CLI_PARSER_MODULES


def _argparse_failure_kind(exc: BaseException) -> str | None:
    """argparse 自己报的错是哪一类——**只在有实际解析证据时才分**，否则 None（普通 `sys.exit(2)` 不猜）。

    证据 = 栈上真有 `argparse.ArgumentParser.error` 那一帧，且它的 `message` 局部变量是 argparse 自己的
    固定句式。不读 stderr、不重跑解析器、不调 `--help`。argparse 被翻译成别的语言时句式对不上 → None。
    返回值只是类别（`missing_required` / `invalid_value` / `unknown`），不含参数值。"""
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        if (
            frame.f_code.co_name == "error"
            and str(frame.f_globals.get("__name__") or "") == "argparse"
        ):
            message = frame.f_locals.get("message")
            if isinstance(message, str):
                if message.startswith("the following arguments are required"):
                    return "missing_required"
                if message.startswith("unrecognized arguments"):
                    return "unknown"
                if "invalid choice" in message or " invalid " in message or "expected " in message:
                    return "invalid_value"
            return None
        tb = tb.tb_next
    return None


def _script_exit_error(exc: SystemExit, *, argv_count: int = 0) -> ProtocolError:
    """用户脚本以非零 `sys.exit` 结束 → 结构化错误（进程不退出）。

    `SystemExit` 不是 `Exception`：不接住的话它会穿过 `ensure_built`，落到主循环
    那条给协议 `shutdown` 用的 `except SystemExit: break` 上——worker 悄无声息地
    退出，上层只看得到管道 EOF，报成「渲染进程崩溃」，而 worker.log 里最后两行
    其实是 argparse 的 `usage: …`（#435 一族里最常见的形状）。
    """
    # message 会进 app.log、再随诊断包出门：`sys.exit("patient-123 …")` 的载荷是用户的
    # 文字，不进 message（评审 #443 第十三轮）；它原样在 traceback 区的 `SystemExit: …`
    # 那一行，用户自己看得到。message 里只写 CPython 真正用的退出状态。
    status = _exit_status(exc.code)
    call = f"sys.exit({exc.code})" if isinstance(exc.code, int) else "sys.exit(<一段文字>)"
    if _raised_by_cli_parser(exc):
        extra = {"exit_code": status}
        kind = _argparse_failure_kind(exc)
        if kind is not None:
            extra["parse_kind"] = kind
        if argv_count:
            # T03：用户给了精确 token，脚本自己的解析器仍然拒绝——这是"你给的参数不对"，不是"没给参数"。
            # 码不变（旧客户端照旧当"需要参数"处理），message / extra 如实说；参数值不进 message。
            extra["argv_count"] = argv_count
            return ProtocolError(
                SCRIPT_NEEDS_ARGUMENTS,
                f"脚本自己的参数解析器拒绝了 Tavotto 传给它的 {argv_count} 个参数，解析以 {call} 结束。"
                "检查参数是否与脚本要求一致（Tavotto 按你给的 token 原样传入，没有改动）。",
                retryable=False,
                traceback_text=traceback.format_exc(),
                extra=extra,
            )
        return ProtocolError(
            SCRIPT_NEEDS_ARGUMENTS,
            f"脚本要求命令行参数，而 Tavotto 运行脚本时不带任何参数（sys.argv 只有脚本"
            f"自己），参数解析于是以 {call} 结束。给这些参数写默认值，"
            "或用 `tavotto run -- python 脚本.py 参数…` 让 Tavotto 跟着你自己的命令跑。",
            retryable=False,
            traceback_text=traceback.format_exc(),
            extra=extra,
        )
    return ProtocolError(
        SCRIPT_EXITED,
        f"脚本调用了 {call} 提前结束。Tavotto 要的是脚本跑完后留在内存里的 "
        "Figure——去掉那句 exit，或改成只在出错时 exit。",
        retryable=False,
        traceback_text=traceback.format_exc(),
        extra={"exit_code": status},
    )


def _script_frame_line(exc: BaseException, script) -> int | None:
    """异常回溯里**最内层**属于用户脚本文件的那一帧的行号（退出在脚本哪一行）；找不到是 None。"""
    try:
        want = os.path.normcase(os.path.realpath(str(script)))
        line = None
        tb = exc.__traceback__
        while tb is not None:
            name = tb.tb_frame.f_code.co_filename
            if os.path.normcase(os.path.realpath(name)) == want:
                line = tb.tb_lineno
            tb = tb.tb_next
        return line
    except (OSError, ValueError):
        return None


def _blocked_operations_error(
    ops: list[dict],
    *,
    cause: BaseException | None = None,
    fidelity: dict | None = None,
) -> ProtocolError:
    """守卫拦下的关键操作 + 脚本随后的观察 → 结构化错误。message 与载荷只有操作类别，没有路径
    （message 会进 app.log 与诊断包）。不自动换环境重跑、不改脚本、不原地放行。
    `fidelity` 是账本的 `summary()`（闭集枚举 + 计数）：失败的 build 没有回执，诊断从这里取证。"""
    names = ", ".join(o["op"] for o in ops)
    return ProtocolError(
        FILE_OPERATION_BLOCKED,
        f"脚本做了 Tavotto 安全模式不会真正执行的文件操作（{names}），随后又读取了受影响的路径，"
        "得到的结果可能与直接用 Python 运行不同，所以这次不算成功。"
        "Tavotto 没有改你的脚本，也没有换环境重跑；这类脚本需要按真实文件运行——"
        "终端里的 `tavotto run -- python 脚本.py` 就是这样跑的。",
        retryable=False,
        traceback_text=traceback.format_exc() if cause is not None else "",
        extra={
            "operations": ops,
            "required_execution": REQUIRED_EXECUTION_FILE_SEMANTICS,
            "adapter_available": False,
            **({"fidelity": fidelity} if fidelity is not None else {}),
        },
    )


def _write_outside_roots_error(found: list[dict]) -> ProtocolError:
    """写入根绊线的终局：稳定码 + 相对化的路径（项目根为基，不暴露无关的绝对路径）。

    message 会进 app.log 与诊断包：只有第一条越界的相对展示，没有授权根列表；脚本源码与路径外的内容一个字都不带。"""
    first = found[0]
    return ProtocolError(
        WRITE_OUTSIDE_ROOTS,
        f"脚本想写已授权工作区之外的位置（{first['path']}），所以这次没有运行完。"
        "经 MCP 运行的脚本只能写到工作区内——改成写到项目内，或请用户在 Tavotto 窗口里自己运行。",
        retryable=False,
        traceback_text=traceback.format_exc() if sys.exc_info()[0] is not None else "",
        extra={
            "path": first["path"],
            "event": first["event"],
            "attempts": [dict(v) for v in found],
        },
    )


#: 重放到口令那一问而没人能答（`inputbroker.REASON_MASKED_INPUT_REQUIRED`；本模块不 import 兄弟模块，按值比）
_REASON_MASKED_INPUT_REQUIRED = "secret_required"


def _needs_input_error(exc) -> ProtocolError:
    """脚本要输入而没有人能答 → 结构化错误（ADR 0099 §五）。提示原文进 message：CLI / MCP 读的就是这一句。"""
    what = exc.prompt.strip() or "（读取标准输入）"
    if exc.reason == _REASON_MASKED_INPUT_REQUIRED:
        message = (
            f"脚本需要重新输入口令：{what}。Tavotto 不保存口令，请在 Tavotto 界面里运行并重新输入。"
        )
    else:
        message = f"脚本需要输入：{what}，请在 Tavotto 界面里运行一次这个脚本并作答。"
    return ProtocolError(
        SCRIPT_NEEDS_INPUT,
        message,
        retryable=False,
        traceback_text=traceback.format_exc(),
        # `input_kind`：读取方式（getpass = 口令）。MCP 桥据此给 `input.secret`，首问的 getpass 也不例外
        extra={"prompt": exc.prompt, "reason": exc.reason, "input_kind": exc.kind},
    )


def _exit_status(code) -> int:
    """`SystemExit.code` → 进程真正会用的退出状态（CPython `handle_system_exit` 的规则）：
    None → 0，整数原样，其它载荷打印到 stderr 后以 1 退出。"""
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


@contextlib.contextmanager
def _real_output():
    global _intercept
    _intercept = False
    try:
        yield
    finally:
        _intercept = True


_MODULE_NAME_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,99}"
)  # 顶层包名；带点的是已装包的子模块，不算缺依赖
#: 读入口脚本源码做静态 import 归因的上限；超过即视为不可归因（只带分类、不带模块名）。
_STATIC_IMPORT_SCAN_LIMIT = 1_000_000


def _static_import_roots(script: Path) -> frozenset[str]:
    """入口脚本源码里静态 `import x` / `from x import …` 的顶层包名；读不了 / 太大 / 解析失败回空集。

    只读、不执行。绝对导入才算（`from . import y` 的 level > 0 不是第三方包）。"""
    try:
        with open(script, "rb") as fh:
            raw = fh.read(_STATIC_IMPORT_SCAN_LIMIT + 1)
        if len(raw) > _STATIC_IMPORT_SCAN_LIMIT:
            return frozenset()
        tree = ast.parse(raw.decode("utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
        return frozenset()
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".", 1)[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return frozenset(roots)


_PRIVATE_OUTPUT_NOTICE = "[sensitive run: script output omitted]\n"


# A separate interpreter is essential: native extensions can write while holding
# the worker's GIL, which would deadlock a Python-thread drainer on a full pipe.
# No user bytes/keys are in this program or its command line. It owns no secrets.
_PRIVATE_OUTPUT_DRAINER = r"""
import os, sys, threading, time
parent = int(sys.argv[1])
done = threading.Event()
if os.name == "nt":
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    handle = kernel.OpenProcess(0x100000, False, parent)
    def alive():
        return bool(handle) and kernel.WaitForSingleObject(handle, 0) == 258
else:
    def alive():
        return os.getppid() == parent

def drain():
    last = -float("inf")
    try:
        while os.read(0, 8192):
            now = time.monotonic()
            if now - last >= 0.25:
                os.write(1, b"[sensitive run: script output omitted]\n")
                last = now
    except OSError:
        pass
    finally:
        done.set()

threading.Thread(target=drain, daemon=True).start()
while not done.wait(0.25):
    if not alive():
        os._exit(0)
"""


def _stop_output_drainer(proc) -> None:
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _suppress_sensitive_output():
    """Discard fd 1/2 in bounded chunks, preserving content-free output activity.

    Protection lasts through deferred draws, native writes and inherited child
    output. The helper checks parent liveness even if a user subprocess keeps a
    pipe writer open; normal worker shutdown also explicitly terminates/reaps it.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    proc = subprocess.Popen(
        [sys.executable, "-I", "-S", "-u", "-c", _PRIVATE_OUTPUT_DRAINER, str(os.getpid())],
        stdin=subprocess.PIPE,
        stdout=sys.stderr,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=runtime.CREATE_NO_WINDOW,
    )
    try:
        os.dup2(proc.stdin.fileno(), 1)
        os.dup2(proc.stdin.fileno(), 2)
        if os.name == "nt":
            # CRT descriptors and Win32 standard handles are separate; Popen and
            # native libraries can inherit/use the latter directly.
            import ctypes  # noqa: PLC0415
            import msvcrt  # noqa: PLC0415

            set_handle = ctypes.windll.kernel32.SetStdHandle
            set_handle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
            set_handle.restype = ctypes.c_int
            for kind, fd in ((-11, 1), (-12, 2)):
                if not set_handle(kind, msvcrt.get_osfhandle(fd)):
                    raise OSError("Cannot protect sensitive script output")
    except BaseException:
        _stop_output_drainer(proc)
        raise
    finally:
        proc.stdin.close()
    return proc


class Worker(wireproto.V1Handler):
    """safe 档执行侧：**怎么把用户脚本跑起来**（沙盒 / 守卫 / argv / 拦截）。

    Figure 到手之后的一切交给 `SafeSession`（`figsession`），信封分派交给
    `wireproto.V1Handler`——两者都与 native bridge 共用同一份实现。
    """

    def __init__(self, args):
        global SESSION
        self.script = Path(args.script).resolve()
        self.figures_dir = Path(args.figures_dir).resolve()
        self.out_dir = Path(args.out_dir).resolve()
        self.sandbox = Path(args.sandbox).resolve()
        #: 脚本的工作目录（ADR 0047）：默认就是沙盒；`--cwd` 给了就是脚本
        #: 自己所在的目录。写入边界的**参照**始终是沙盒 / 图库目录，守卫不变。
        self.workdir = Path(args.cwd).resolve() if getattr(args, "cwd", None) else None
        self.entry = args.entry
        self.preview_dpi = args.preview_dpi
        self.built = False
        self.execution_id: str | None = None
        self._execution_watch: str | None = None
        #: 本进程 build 时实际读到并执行的脚本字节的 sha256（读失败 None）；build 前 None。
        self._source_sha256: str | None = None
        #: pyplot 兜底因上限丢掉的张数（0 = 一张没丢）。build 响应带出去。
        self.dropped_figures = 0
        #: 每张捕获 Figure 的结构化描述（figcapture.CapturedFigureDescriptor
        #: 的 payload，捕获顺序）。**build 那一刻算好钉死**：fingerprint 里的
        #: 脚本内容哈希必须是「实际被执行的那份」，之后脚本再被改，本会话
        #: 跑的还是旧代码（watcher 会作废会话，这里不追新）。
        self._descriptor_cache: list[dict] = []
        #: 回执的 `inputs`（U09）：build 那一刻定格的输入观察（`figcapture.InputObserver.report()`）。
        self._input_observer: figcapture.InputObserver | None = None
        self._inputs_report: dict | None = None
        #: 本次 build 的问答通道（ADR 0099）：build 开始时建，响应的 `script_inputs` 从它取。
        self._input_channel: "scriptinput.Channel | None" = None
        #: 数据改指（ADR 0106）：用户指认过的只读改指表（`--input-remap`，父进程按项目给），
        #: 与本次 build 里落空的只读打开——`missing_input` 靠它说出缺的是哪个。
        self.input_remap = _parse_remap(getattr(args, "input_remap", None))
        #: 写入根绊线的授权根（`--write-roots`，MCP 来源才有；#898）。None = 不装（GUI / CLI 旧行为）。
        self.write_roots = _parse_write_roots(getattr(args, "write_roots", None))
        #: Only the opaque reference is on the command line. Values arrive over
        #: the private request pipe before the first execution, then stay frozen.
        self.script_argv: list[str] = []
        self.run_config = str(getattr(args, "run_config", "") or "")
        self.sensitive_argv = False
        self._input_key = ""
        self._private_output = None
        self._run_loaded = False
        self._input_misses = figcapture.InputMisses()
        #: 守卫拦下的关键文件操作账本（P01a）：`build()` 里换成这一轮的那本，模块级 `BLOCKED_OPS` 指向同一本。
        self._blocked_ops: "figcapture.BlockedFileOps | None" = None
        SESSION = SafeSession(self.out_dir, self.preview_dpi)
        SESSION.frame_project_root = str(self.figures_dir)
        SESSION.savefig_observations = figcapture.SavefigObservations(
            project_root=self.figures_dir, execution_root=self.sandbox, metadata=_SAVEFIG_METADATA
        )
        super().__init__(SESSION)

    def configure_run(self, payload: dict) -> None:
        watch = payload.get("execution_watch")
        if watch is not None:
            if not executionfact.valid_watch(watch):
                raise ProtocolError("bad_request", "Invalid execution observation identity")
            self._execution_watch = watch
        run = payload.get("run")
        if run is None:
            if self.run_config and not self._run_loaded:
                raise ProtocolError("bad_request", "Run configuration payload is required")
            return
        if (
            not isinstance(run, dict)
            or not self.run_config
            or run.get("config_id") != self.run_config
            or not isinstance(run.get("argv"), list)
            or not run["argv"]
            or not all(isinstance(a, str) and "\x00" not in a for a in run["argv"])
            or not isinstance(run.get("sensitive"), bool)
        ):
            raise ProtocolError("bad_request", "Invalid run configuration payload")
        input_key = run.get("input_key", "")
        if run["sensitive"] and (
            not isinstance(input_key, str)
            or len(input_key) != 64
            or any(c not in "0123456789abcdef" for c in input_key)
        ):
            raise ProtocolError("bad_request", "Private input identity key is required")
        if self._run_loaded:
            if (
                run["argv"] != self.script_argv
                or run["sensitive"] != self.sensitive_argv
                or input_key != self._input_key
            ):
                raise ProtocolError("bad_request", "Run configuration cannot change in a worker")
            return
        if run["sensitive"]:
            self._private_output = _suppress_sensitive_output()
        self.script_argv = list(run["argv"])
        self.sensitive_argv = run["sensitive"]
        self._input_key = input_key
        self._run_loaded = True

    def diagnostic_response(self, resp: dict) -> dict:
        """Exceptions can quote/escape/transform argv; omit their free-form text.

        Keep structural parse evidence for UI recovery. Ordinary diagnostics and
        successful figure data are unchanged; this is not a sandbox for explicit
        writes performed by the user's own script.
        """
        if not self.sensitive_argv:
            return resp
        if resp.get("ok"):
            # Setters/custom artists can turn a caught exception into warnings
            # on a successful render/export. Preserve failure cardinality for
            # writeback verification, but never forward their free-form text.
            if resp.get("warnings"):
                return {
                    **resp,
                    "warnings": [_PRIVATE_OUTPUT_NOTICE.strip() for _ in resp["warnings"]],
                }
            return resp
        err = resp.get("error")
        if isinstance(err, dict):
            safe = {
                k: err[k]
                for k in (
                    "code",
                    "retryable",
                    "argv_count",
                    "parse_kind",
                    "missing_module",
                    "missing_dependency",
                    "execution_id",
                )
                if k in err
            }
            safe.update(
                message="Sensitive run failed; script diagnostics were omitted.",
                traceback=_PRIVATE_OUTPUT_NOTICE.strip(),
            )
            return {**resp, "error": safe}
        return {
            **resp,
            "error": "Sensitive run failed; script diagnostics were omitted.",
            "traceback": _PRIVATE_OUTPUT_NOTICE.strip(),
        }

    def handle_v1(self, req: dict) -> dict:
        try:
            result = super().handle_v1(req)
        except ProtocolError as exc:
            exc.extra["execution_id"] = self.execution_id
            raise
        except Exception as exc:  # noqa: BLE001 — same fallback as wireproto.respond
            raise ProtocolError(
                "internal",
                str(exc) or exc.__class__.__name__,
                retryable=True,
                traceback_text=traceback.format_exc(),
                extra={"execution_id": self.execution_id},
            ) from exc
        return {**result, "execution_id": self.execution_id}

    def select_artifact(self, context):
        try:
            selected = figcapture.selected_artifact_context(context)
        except ValueError as exc:
            raise ProtocolError(
                "artifact_source_unavailable", str(exc), extra={"reason": "destination_unresolved"}
            ) from exc
        if self.built and selected != self.session.artifact_source:
            raise ProtocolError(
                "artifact_source_unavailable",
                "Artifact context cannot change inside a built worker.",
                extra={"reason": "source_changed"},
            )
        self.session.artifact_source = selected

    # ---------------- build ----------------
    def _execution_entered(self) -> None:
        self.execution_id = executionfact.new_id()
        executionfact.write(self.out_dir, self._execution_watch, self.execution_id)

    def _install_write_tripwire(self) -> None:
        """装写入根绊线（#898）：白名单 = 命令行给的授权根 + 引擎自己的会话目录 + 基础设施目录。

        会话沙盒与会话缓存目录（`out/` 的上一级：out / sandbox / export / worker.log）是引擎自己的；
        matplotlib 的缓存 / 配置目录要在 matplotlib 已 import 之后才知道。其余基础设施目录（临时目录、平台缓存、
        seaborn / sklearn 数据目录）见 `writetripwire.infrastructure_roots`，每一项都带理由。"""
        extra = [self.sandbox, self.out_dir.parent]
        for getter in ("get_cachedir", "get_configdir"):
            try:
                extra.append(getattr(matplotlib, getter)())
            except Exception:  # noqa: BLE001 — 取不到就少一项白名单，不让绊线的装配挡住渲染
                pass
        writetripwire.install(
            [*self.write_roots, *writetripwire.infrastructure_roots(extra=extra)],
            display_base=str(self.figures_dir),
        )

    def build(self, timings: dict | None = None) -> dict:
        """跑一次用户脚本，把产出的 Figure 全部收进内存。

        `timings` 非空时填两个数：`script_build_ms` 是整个 build（脚本 +
        instrument + 每个 stem 的首次预览），`script_exec_ms` 只是用户脚本
        自己那一段。两者分开是因为它们的改法完全不同——前者大头在用户的
        计算里（我们无能为力），差额才是引擎自己的开销。
        """
        global BLOCKED_OPS
        t_build = time.perf_counter()
        if self.built:
            return self._stems_summary()
        self.configure_run({})
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox.mkdir(parents=True, exist_ok=True)
        # cwd：默认沙盒（写入边界）；项目级开关打开时是脚本自己所在的目录——
        # 脚本用相对路径找数据（exists / glob / C++ 读取器）的唯一成立形态。
        os.chdir(self.workdir or self.sandbox)
        # 图库根先进 sys.path（脚本 import 同目录的 paper_style / 数据模块），
        # 脚本自己所在目录再插到最前——面板脚本放 panels/ 子目录时，
        # 只加图库根会让 import_module(stem) 直接 ModuleNotFoundError。
        sys.path.insert(0, str(self.figures_dir))
        if self.script.parent != self.figures_dir:
            sys.path.insert(0, str(self.script.parent))
        # 项目目录里的源码（脚本本身按模块 import 时、它 import 的本地模块）不写 .pyc——
        # 必须在第一次 import 用户代码（paper_style / 脚本）之前装好。
        _suppress_project_bytecode((self.figures_dir, self.script.parent))

        # 删除守卫：fig6 用绝对路径删“过期输出”（ROOT/figures/...），沙盒 cwd
        # 挡不住，这里直接拒绝任何指向真实图库目录的删除（渲染用不到删除）
        real_figs = self.figures_dir
        real_unlink = Path.unlink

        # 守卫拦下的关键操作记一本账（P01a）：守卫本身一字不动，账只多回答一件事——
        # 脚本随后有没有去观察被拦下的路径。open / os.stat 的观察包装在输入观察器与读回退之后安装（最外层）。
        ledger = figcapture.BlockedFileOps()
        BLOCKED_OPS = self._blocked_ops = ledger
        # 账本的观察钩子之后才装：这里先留一份原始 lexists，守卫自己判「目标在不在」时不被当成脚本的观察
        real_lexists = os.path.lexists

        def _guarded_unlink(p, missing_ok=False):
            if missing_ok and not real_lexists(p):
                # `unlink(missing_ok=True)` 且目标不在：直跑要么删掉它、要么 no-op，随后 exists() 两边都是 False——
                # 没有可分叉的东西，不记账（Codex #952）。不带 missing_ok 的不在此列：沙盒档里「项目里没有」
                # 不等于「直跑时没有」（文件可能按相对路径写进了沙盒），那里仍按被拦记账。
                return None
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过删除真实图库文件: {p}", file=sys.stderr)
                    ledger.blocked("Path.unlink", ledger.KIND_DELETE, p)
                    return None
            except OSError:
                pass
            try:
                return real_unlink(p, missing_ok=missing_ok)
            except FileNotFoundError as exc:
                ledger.note_failed_call(
                    exc, ledger.VIA_MOVE_FAILED
                )  # 删 savefig 目标落空：直接运行不会
                raise

        Path.unlink = _guarded_unlink

        # 同一条守卫扩到 os / shutil 的删除与改名（`os.remove` / `os.unlink` /
        # `os.rmdir` / `os.rename` / `os.replace` / `shutil.rmtree` /
        # `shutil.move`）：`Path.unlink` 只是其中一个入口，脚本写
        # `os.remove("stale.png")` 一样是删真实图库里的文件——沙盒 cwd 下相对
        # 路径落在沙盒里无害，绝对路径与「在脚本目录里运行」（ADR 0047）时的
        # 相对路径就直接指向项目。判据一条：**真身落在真实图库里就跳过**。
        # 带 `dir_fd` 的调用（rmtree 内部）不判——顶层 rmtree 已经拦过了。
        # 覆盖：脚本用 open('w') / np.save / C++ 写入器**改写**已有文件不拦，
        # 那是它自己的输出（文案如实说「已有同名文件会被脚本改写」）。
        def _inside_real(path) -> bool:
            try:
                return Path(os.fspath(path)).resolve().is_relative_to(real_figs)
            except (OSError, TypeError, ValueError):
                return False

        def _guard_delete(name, real_fn, op, kind):
            # 改名 / 移动影响源与目的两条路径（目的地读到的是旧内容或没有），删除只影响自己
            moves = kind == ledger.KIND_RENAME
            src_key = "src" if moves else "path"

            def guarded(*a, **kw):
                # 按签名取参：源与目的各自可能是位置参数也可能是关键字（`dst=`、`src=` / `path=`）
                path = a[0] if a else kw.get(src_key)
                if path is not None and "dir_fd" not in kw and _inside_real(path):
                    print(f"[guard] 跳过 {name} 真实图库文件: {path}", file=sys.stderr)
                    dst = a[1] if len(a) > 1 else kw.get("dst")
                    affected = ()
                    if moves and dst is not None:
                        if op == "shutil.move":
                            # shutil.move 进已存在目录：受影响的是 dst/basename(src)，不是整个目录
                            dst = ledger.move_destination(path, dst)
                        affected = (dst,)
                    ledger.blocked(op, kind, path, *affected, dests=affected)
                    return None
                try:
                    return real_fn(*a, **kw)
                except FileNotFoundError as exc:
                    # 真实执行的改名 / 删除落空；源是被拦截的 savefig 目标时，直接运行不会落空。
                    # 带 `*dir_fd` 的路径相对那个 fd，不是 cwd：解析不了，不归因（保留原分类）
                    if ledger.uses_dir_fd(kw):
                        ledger.tag_unresolvable(exc)
                    else:
                        ledger.note_failed_call(exc, ledger.VIA_MOVE_FAILED)
                    raise

            return guarded

        os.remove = _guard_delete("删除", os.remove, "os.remove", ledger.KIND_DELETE)
        os.unlink = _guard_delete("删除", os.unlink, "os.unlink", ledger.KIND_DELETE)
        os.rmdir = _guard_delete("删除目录", os.rmdir, "os.rmdir", ledger.KIND_DELETE_DIR)
        os.rename = _guard_delete("改名", os.rename, "os.rename", ledger.KIND_RENAME)
        os.replace = _guard_delete("改名", os.replace, "os.replace", ledger.KIND_RENAME)
        shutil.rmtree = _guard_delete(
            "删除目录树", shutil.rmtree, "shutil.rmtree", ledger.KIND_DELETE_DIR
        )
        shutil.move = _guard_delete("移动", shutil.move, "shutil.move", ledger.KIND_RENAME)

        # 写入守卫：脚本的 write_caption 等用绝对路径 write_text 写真实图库
        # （fig9 的 *_caption.txt），沙盒 cwd 拦不住；导出走 savefig 不受影响
        real_write_text = Path.write_text

        def _guarded_write_text(p, *a, **kw):
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过写入真实图库文件: {p}", file=sys.stderr)
                    ledger.blocked("Path.write_text", ledger.KIND_WRITE, p)
                    return 0
            except OSError:
                pass
            return real_write_text(p, *a, **kw)

        Path.write_text = _guarded_write_text

        # 相对路径**只读**回退：cwd 在沙盒里，而 `pd.read_csv("data.csv")` 这类
        # 写法在 `python figure.py` 下是天经地义的。只读、只在沙盒里确实没有
        # 这个文件时、且换算后仍落在图库内才生效——写/删/改一个字节都不经过
        # 它，沙盒作为**写入**边界完全没有松动（语义与理由见 figcapture）。
        # 输入观察（U09，ADR 0070）：脚本经 Python `open` / numpy `DataSource.open` 只读打开的项目内文件记进回执（数据身份）。
        # **先于**只读回退装——回退换出来的那条路径经它记下，正是脚本实际读到的那份。装了就不卸：
        # 卸会把叠在外层的回退一起摘掉；观察器只记不改，留着也只是多几条项目外的忽略。
        self._input_observer = figcapture.InputObserver(str(self.figures_dir))
        self._input_observer.install()
        # 数据改指（ADR 0106）装在观察器**外**、只读回退**内**：回退先找脚本目录，找不到才轮到
        # 用户指认的改指表；改道来的读经观察器记下。没有规则也装——落空的读要记账。
        self._input_misses = figcapture.InputMisses()
        # 脚本源码给改指当证据：落在 cwd 里的绝对路径是不是相对路径规范化来的（证不出就保留绝对身份）
        try:
            script_source = self.script.read_text(encoding="utf-8", errors="replace")
        except OSError:
            script_source = ""
        figcapture.install_input_remap(
            self.input_remap,
            self._input_misses,
            str(self.workdir or self.sandbox),
            script_source=script_source,
        )
        if self.workdir is None:
            ledger.read_redirect = figcapture.install_relative_read_fallback(
                str(self.script.parent), str(self.figures_dir)
            ).resolve
        # cwd 已经是脚本目录时不装回退：相对路径本来就指向项目，回退只会在
        # 「沙盒里有同名文件」这种不可能的前提上多一层看不见的改道。
        # 账本的 open / stat 观察装在**最外层**（回退之外）：看到的必须是脚本交出的那条路径。
        # 装在回退之下，沙盒里 `savefig("stage.png")` 记下的 `<沙盒>/stage.png` 永远等不到
        # 被回退改指成 `<项目>/stage.png` 的读，旧图就被静默读走了（#952 Codex P1）。
        ledger.install()
        if self.write_roots is not None:
            self._install_write_tripwire()

        # 拦截必须发生在 import 脚本之前（多数脚本 from paper_style import save）
        mfigure.Figure.savefig = _patched_savefig
        figcapture.install_colorbar_capture(mfigure)
        _install_show_capture()

        # 脚本看到的 argv 必须是它自己的，不是 worker 的。不换的话
        # `sys.argv[1:]` 拿到的是 --script/--out-dir/--entry 这串内部参数，
        # 按参数命名输出的脚本会存出一堆叫 "--entry" 的图（试运行探测时
        # 当场撞见过）。真跑 `python fig.py` 时 argv 就只有脚本自己。
        # **排在 paper_style 之前**：那份私有模块也是用户代码，import 期间就可能
        # 解析参数（评审 #443）。
        # T03：用户给了精确 token 时跟在脚本后面，一个不改（空串 / 空格 / 中文 / `--` 原样）。仍排在
        # paper_style 与 `runpy` 之前——导入期 `parse_args()` 的脚本在第一行用户代码之前就看得到。
        sys.argv = [str(self.script), *self.script_argv]

        # 脚本 import 了却从未用到、又没装的包不挡图（ADR 0061 §二 2026-09-24 修订）：判据与
        # 父进程的联合计划同一份（`figcapture.unused_imports`）——计划没要求装它，这里就得让
        # 那一行 import 过得去。读不了 / 解析不了就不装：脚本随后会以它自己的错误结束。
        # 这份字节只给 ast.parse 用；`source_sha256` 另走执行前后括号（见下面 `pre` / `post`）。
        try:
            import ast  # noqa: PLC0415

            source_bytes = self.script.read_bytes()
            unused = figcapture.unused_imports(ast.parse(source_bytes))
        except (OSError, SyntaxError, ValueError):
            unused = frozenset()
        figcapture.install_unused_import_placeholders(str(self.script), unused)

        # 脚本里的 input() / sys.stdin / getpass（ADR 0099）：sys.stdin 此刻是协议管道，不换的话脚本会
        # 阻塞在它上面、甚至把父进程的下一条命令当答案读走。会合目录在本会话的 out_dir 里（Tavotto 自己的
        # 缓存，不是用户目录）；stdout 经 `StdoutTail` 转进 worker.log，同时留最近一段给界面看编号清单。
        try:
            rel_script = self.script.relative_to(self.figures_dir).as_posix()
        except ValueError:
            rel_script = self.script.name
        stdout_tail = scriptinput.StdoutTail(sys.stderr)
        channel = scriptinput.Channel(
            self.out_dir / scriptinput.DIRNAME,
            rel_script,
            tail=stdout_tail,
            private_key=self._input_key,
        )
        channel.reset()
        self._input_channel = channel
        scriptinput.install(channel)

        capture_threads = set(threading.enumerate())
        t_script = time.perf_counter()
        # `source_sha256`（TAF 改图验证的判据：这次 build 执行的是哪一版源码）。两条路径各自成立的理由不同：
        # - entry != "__main__"（`import_module`）：由**实际加载源码的 loader 报摘要**。`_capture_entry_sha` 在这次
        #   import 期间临时包 `SourceFileLoader.get_code`：入口脚本一律 `get_data` 读源码字节、记 sha256、
        #   `source_to_code` 编译——完全不读 `__pycache__`（外部运行遗留的 timestamp / unchecked-hash .pyc 过期也
        #   不会被执行），所以「报的摘要」与「编译执行的字节」是同一份，判据是定义上的，不靠前后戳。
        # - entry == "__main__"（`runpy.run_path`）：CPython runpy（3.10–3.14 同形）`_get_code_from_file` 对 .py 文件
        #   只做 `read_code(f)`（仅当文件本身以 .pyc magic 开头才当字节码）、否则 `compile(f.read(), ...)`，
        #   从不查 `__pycache__`，读的就是源码本身。故沿用执行前后括号：读源码那一刻必落在 pre 与 post 之间，
        #   两端 (mtime_ns, size, inode, sha) 完全相同 ⇒ 期间没被写过（刻意 utime 回拨属对抗性，不设防）；不同 → None。
        #   `_source_stamp` 只给这条路径用。
        pre = _source_stamp(self.script) if self.entry == "__main__" else None
        entry_shas: list[str] = []
        # `SystemExit` 不是 `Exception`：脚本末尾的 `sys.exit(main())` / `exit()` /
        # `quit()` 会一路穿过 `ensure_built` 的 `except Exception`，落到主循环
        # 那条给协议 `shutdown` 用的 `except SystemExit: break` 上——worker 悄无
        # 声息地退出，stdout EOF，supervisor 只能报「渲染进程崩溃」，worker.log
        # 里连一行 traceback 都没有。与 `python fig.py` 的语义对齐：退出码 0 /
        # None 就是脚本正常结束（已经画好的图照常捕获），非零才是它自己报的失败。
        # paper_style 的 import 也在这道保护里：它是用户代码，import 期间一样能 exit。
        with (
            contextlib.redirect_stdout(stdout_tail),
            executionfact.on_user_entry(self.script, self._execution_entered),
        ):
            if self.write_roots is not None:
                writetripwire.arm(True)
            try:
                # paper_style 是某些图库的私有方言，不是引擎的依赖：没有就跳过，
                # 靠 _patched_savefig 这条通用兜底捕获。曾经这里是无保护的 import，
                # 任何不带 paper_style.py 的图库都会以 ModuleNotFoundError 开局。
                try:
                    import paper_style  # noqa: PLC0415
                except ImportError:
                    pass
                else:
                    paper_style.save = _wrap_paper_style_save(paper_style.save)
                if self.entry == "__main__":
                    import runpy  # noqa: PLC0415 — 内联脚本（fig4c / fig_models）

                    runpy.run_path(str(self.script), run_name="__main__")
                else:
                    with _capture_entry_sha(self.script, entry_shas):
                        module = importlib.import_module(self.script.stem)
                    getattr(module, self.entry)()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    # 被拦下的操作之后观察到不同的世界而退出：原因是守卫，不是脚本自己想退（P01a）
                    # `savefig(p); if not exists(p): sys.exit(1)`：退出分支在直接运行里走不到——也是守卫造成的
                    if ledger.material() or (
                        not _raised_by_cli_parser(exc)
                        and ledger.explain_exit(
                            controlled=figcapture.exit_controlled_by_probe(
                                script_source, _script_frame_line(exc, self.script)
                            )
                        )
                    ):
                        raise _blocked_operations_error(
                            ledger.material(), cause=exc, fidelity=ledger.summary()
                        ) from exc
                    raise _script_exit_error(exc, argv_count=len(self.script_argv)) from exc
            except scriptinput.ScriptNeedsInput as exc:
                raise _needs_input_error(exc) from None
            except Exception as exc:
                # 脚本在被拦下的操作之后读不到东西而崩了（`FileNotFoundError`…）：根因是守卫，
                # 不是「数据找不到」（那会请用户去指认一个其实被我们拦下的路径）
                # `savefig(p)` 之后 `os.replace(p, q)` / `getsize(p)` / `Path(p).stat()` / `os.open(p)` 落空同理：
                # 这个 FileNotFoundError 是守卫不落盘造成的，不是脚本错误，更不是该请用户指认的"缺数据"
                if ledger.explains(exc) or ledger.material():
                    raise _blocked_operations_error(
                        ledger.material(), cause=exc, fidelity=ledger.summary()
                    ) from exc
                raise
            finally:
                # 脚本跑完了：之后再有谁读 stdin 一律 EOF，绝不再发问（也绝不再碰协议管道）
                channel.closed = True
                ledger.stop()
                # 无论正常结束 / SystemExit / 异常都在这里收括号（finally 不改变原有异常传播）
                if self.entry == "__main__":
                    post = _source_stamp(self.script)
                    self._source_sha256 = pre[3] if pre is not None and pre == post else None
                else:
                    # 恰好一次加载才可信；零次（入口模块此前已在 sys.modules，如被 paper_style 提前 import）或多次 → None
                    self._source_sha256 = entry_shas[0] if len(entry_shas) == 1 else None
                if self.write_roots is not None:
                    writetripwire.arm(False)
                    # 脚本 `except PermissionError` 吞掉、或以别的错误收场，事实都在：越界的写**尝试**发生过，
                    # 这次不算成功（取代原本的异常，原异常留在 `__context__`；traceback 指向越界的那一行）
                    if writetripwire.violations():
                        raise _write_outside_roots_error(writetripwire.violations())
        script_ms = _ms(t_script)
        # 脚本跑到了末尾，但被守卫拦下的关键操作之后它观察过受影响的路径（用户 `except Exception`
        # 吞掉什么都不影响这条带外事实）：结果可能与直接 Python 不同，不记成完整成功（P01a，AC-02）
        if ledger.material():
            raise _blocked_operations_error(ledger.material(), fidelity=ledger.summary())

        # pyplot 兜底：从不 savefig 的脚本（`plt.plot(...); plt.show()` 这种
        # AI 最常写的形态）也要能用。**与浏览器 playground 同一份策略**——
        # 抄一份进来的话同一个脚本会在两个入口产出不同的 stem。
        #
        # 只在脚本真的 import 过 pyplot 时才问它：没 import 过就不可能有 pyplot
        # figure，而在这里 import 一次要白付几十毫秒（还会给纯 OO API 的脚本
        # 凭空建一个 figure 管理器）。
        _plt = sys.modules.get("matplotlib.pyplot")
        if _plt is not None:
            fallback, dropped = figcapture.collect_pyplot_figures(
                self.session.capture, self.script.stem, _plt, shown=_SHOWN
            )
            for stem in fallback:
                self.session.capture_source[stem] = figcapture.SOURCE_PYPLOT
            if dropped:
                # 丢了就说，绝不静默：用户会数图。
                print(
                    f"[capture] 脚本留下的 pyplot Figure 超过 "
                    f"{figcapture.MAX_PYPLOT_FALLBACK} 张上限，"
                    f"未捕获 {dropped} 张（显式 savefig 的不受此限）",
                    file=sys.stderr,
                )
                self.dropped_figures = dropped

        try:
            if (
                self.session.artifact_source is not None
                and set(threading.enumerate()) - capture_threads
            ):
                raise figsession.ArtifactSourceUnavailable("unsupported_render_state")
            self.session.prepare_artifact()
            self.session.instrument_all(suppressed_savefig=True, raise_if_none_live=True)
            self.session.finish_artifact()
        except figsession.ArtifactSourceUnavailable as exc:
            raise ProtocolError(
                "artifact_source_unavailable", str(exc), extra={"reason": exc.reason}
            ) from exc
        self._descriptor_cache = self._build_descriptors()
        # 回执的 `inputs` 在**这一刻**定格：脚本已经跑完，之后进程里再读什么（导出时的字体缓存）都不是它的输入
        self._inputs_report = self._input_observer.report(
            local_modules=figcapture.observed_local_modules(
                str(self.figures_dir), sys.modules, exclude_dir=str(Path(__file__).resolve().parent)
            )
        )
        self.built = True
        if timings is not None:
            timings["script_exec_ms"] = script_ms
            timings["script_build_ms"] = _ms(t_build)
        return self._stems_summary()

    def _build_descriptors(self) -> list[dict]:
        """每张捕获 Figure 的统一描述——**语义全在 figcapture，这里只是装配**。

        与浏览器 playground 的 load 响应共用同一份工厂（对拍用例在
        `test_compat_capture_parity.py`）。原始产物只对 savefig 来源的 stem
        查（pyplot 捕获的图从没存过盘，磁盘上碰巧同名的文件不是它的原件，
        工厂对「pyplot + 产物」直接抛）。
        """
        try:
            rel = self.script.relative_to(self.figures_dir).as_posix()
        except ValueError:  # 脚本不在图库下（防御，不该发生）
            rel = self.script.name
        try:
            script_bytes = self.script.read_bytes()
        except OSError:
            script_bytes = b""
        fingerprint = figcapture.source_fingerprint(
            script_bytes,
            script=rel,
            entry=self.entry,
            profile=figcapture.PROFILE_SAFE,
            target_kind="script",
            argv=(),
            passthrough_savefig=False,
            matplotlib_version=matplotlib.__version__,
        )
        return self.session.descriptors(
            script=rel,
            entry=self.entry,
            execution_profile=figcapture.PROFILE_SAFE,
            source_fingerprint=fingerprint,
            project_root=str(self.figures_dir),
            run_config=self.run_config,
        )

    def _stems_summary(self) -> dict:
        # `source` / `source_sha256` 是加字段，不升协议版本（ADR 0003 §1：两侧容忍未知字段）。
        # `source_sha256` 是**本进程当初执行的版本**：去重回旧会话时二次 build 拿到的也是它，不是磁盘现状。
        return {
            **self.session.stems_summary(self.dropped_figures),
            "source_sha256": self._source_sha256,
        }

    def _render(
        self, stem: str, timings: dict | None = None, preview_dpi: int | None = None
    ) -> dict:
        """导出预览 SVG + 重建 manifest（语义在 `figsession`，这里只是老名字）。"""
        return self.session.render(stem, timings, preview_dpi)

    # ---------------- legacy 信封（无 protocol_version） ----------------
    def handle(self, req: dict) -> dict:
        """老的扁平协议。**响应形状一字不改**——手工调试与旧调用方靠它。"""
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True}
        if cmd == "shutdown":
            raise SystemExit(0)
        if req.get("artifact_source") is not None or self.session.artifact_source is not None:
            return {
                "ok": False,
                "code": "artifact_source_unavailable",
                "error": "Selected artifacts require protocol v1.",
                "reason": "unsupported_render_state",
            }
        if cmd == "build":
            # legacy 形状一字不变（test_legacy_envelope_keeps_the_old_response_shape）：`source_sha256` 只给 v1 信封
            return {"ok": True, **{k: v for k, v in self.build().items() if k != "source_sha256"}}
        if cmd == "export" and (req.get("require_built") or req.get("snapshot_only")):
            if not self.built or req.get("stem") not in self.session.states:
                return {
                    "ok": False,
                    "code": "svg_source_unavailable",
                    "error": "An already built Figure session is required for SVG capture.",
                }
        if not self.built:
            self.build()

        stem = req.get("stem", "")
        states = self.session.states
        failure = self.session.failures.get(stem)
        if stem not in states and failure is not None:
            return {
                "ok": False,
                "code": failure["code"],
                "error": f"stem {stem} 的预览没能捕获: {failure['message']}",
                "known": sorted(states),
            }
        if stem not in states:
            return {"ok": False, "error": f"stem 不存在: {stem}", "known": sorted(states)}

        if cmd == "override":
            return {"ok": True, **self.session.do_render(stem, req.get("patches", []))}
        if cmd == "preview_png":
            return {
                "ok": True,
                **self.session.do_preview_png(
                    stem,
                    req.get("patches", []),
                    int(req.get("width", 400)),
                    str(req.get("tag", "p")),
                    req.get("with_manifest", False),
                ),
            }
        if cmd == "render_png":
            return {"ok": True, **self.session.do_render_png(stem, int(req.get("width", 800)))}
        if cmd == "export":
            return {
                "ok": True,
                **self.session.do_export(
                    stem,
                    req.get("patches", []),
                    req["path"],
                    req.get("format", "pdf"),
                    int(req.get("dpi", 600)),
                    **({"svg_path": req["svg_path"]} if req.get("svg_path") is not None else {}),
                    **({"snapshot_only": True} if req.get("snapshot_only") else {}),
                ),
            }

        return {"ok": False, "error": f"未知指令: {cmd}"}

    # ---------------- 协议 v1 ----------------
    def ensure_built(self, timings: dict | None = None) -> None:
        """按需 build；用户脚本炸了报 script_error（重试没有意义）。

        `timings` 一路传下去：冷启动的 render 里 `script_build_ms` 才是那几十秒
        的去向，不带上的话响应里只剩几毫秒的 apply/draw，读数与用户的体感完全
        对不上。

        build 里除了跑脚本还有 mkdir / 预览 SVG 落盘，理论上也会因磁盘问题
        失败——这里**一律归到 script_error**：绝大多数是脚本自己的问题，
        而把两者分开需要猜 traceback 的来源，猜错比归错更难排查。
        真正的原因永远在 `error.traceback` 里原样带着。
        （`missing_dependency` 由父进程按 traceback 正则单独识别，先于 code。）
        """
        if self.built:
            return
        self.configure_run({})
        try:
            self.build(timings)
        except ProtocolError:
            # build 自己已经分好类的（脚本要命令行参数 / 脚本主动 exit）原样上抛
            raise
        except Exception as exc:  # noqa: BLE001 — 转成结构化错误，进程不退出
            fatal = wireproto.fatal_error_of(exc)
            if fatal is not None:
                raise fatal from exc  # 预览产物对读方不可信：不归 script_error
            timed_out = [
                r
                for r in (self._input_channel.record if self._input_channel else [])
                if r.get("timed_out")
            ]
            if timed_out:
                # 有一问等到超时、按 EOF 交给了脚本，脚本没接住（ADR 0099 §三）：说「没人回答」，
                # 不说笼统的「脚本执行失败」——用户的下一步是去作答，不是去改脚本
                minutes = round(scriptinput.wait_timeout() / 60, 1)
                raise ProtocolError(
                    SCRIPT_INPUT_TIMEOUT,
                    f"脚本在等输入，等了 {minutes:g} 分钟没有回答，按输入结束（EOF）处理后脚本失败了："
                    f"{exc}",
                    retryable=False,
                    traceback_text=traceback.format_exc(),
                    # 读取方式随错误带出：等到超时的若是 getpass，界面要按口令说明（Codex #973）
                    extra={
                        "prompt": timed_out[-1].get("prompt", ""),
                        "minutes": minutes,
                        "input_kind": timed_out[-1].get("kind", ""),
                    },
                ) from exc
            missing = figcapture.missing_input_of(exc, self._input_misses)
            if missing is not None:
                # 脚本要读的文件不存在、而且对得上是哪一次只读打开落空的（ADR 0106 §一）：
                # 用户的下一步是指认数据在哪，不是去读 traceback
                raise ProtocolError(
                    MISSING_INPUT,
                    # 原始异常原样接在后面：只拿到 message 的入口（准备接口）也看得到真正的报错
                    f"脚本要读取的文件不存在: {missing['requested']}（{type(exc).__name__}: {exc}）",
                    retryable=False,
                    traceback_text=traceback.format_exc(),
                    extra={"missing_input": missing},
                ) from exc
            # 不是一次落空的只读打开、但异常链里确实有「文件不存在」（C++ 读取器）：码仍是
            # script_error，只多带一份事实；对不对得上脚本里哪串常量由父进程判（ADR 0110 §一）
            enoent = figcapture.enoent_fact(exc)
            facts: dict = {"enoent": enoent} if enoent else {}
            # 缺依赖的结构化事实：敏感运行会丢掉自由文本（父进程本来从消息 / traceback 里认出缺哪个包），
            # 在丢之前先把「顶层模块名」这一个安全事实带上，依赖修复 / 环境交接才不会丢
            facts.update(self._missing_module_fact(exc))
            raise ProtocolError(
                "script_error",
                f"脚本执行失败: {exc}",
                retryable=False,
                traceback_text=traceback.format_exc(),
                extra=facts or None,
            ) from exc

    def _missing_module_fact(self, exc: BaseException) -> dict:
        """缺依赖的结构化事实：`{"missing_dependency": True, "missing_module": 顶层包名?}`；不是缺依赖回 `{}`。

        只取解释器自己填的 `ModuleNotFoundError.name`，不解析消息文本（文本里可能引了 argv）。带点的名字
        （已装包的子模块）不算缺依赖，与父进程 `pool.missing_module` 同一判据。

        **敏感运行里模块名只在能独立归因到脚本时才带出**：名字可以由敏感 argv 变形而来
        （`importlib.import_module(sys.argv[1] + ".sub")`、`--plugin=pkg` → name == "pkg"），整串比较
        认不出。所以敏感运行只有两种情况带名字：入口脚本源码里有静态 `import x` / `from x import …`
        且 x 就是这个顶层包（`_static_import_roots`），并且名字与任一敏感 token 无关（casefold 子串，
        任一方向）。否则只带分类标志 `missing_dependency`，父进程走通用缺依赖提示、不带模块名。"""
        seen = 0
        cur: BaseException | None = exc
        while cur is not None and seen < 16:
            if isinstance(cur, ModuleNotFoundError):
                name = getattr(cur, "name", None)
                if not (isinstance(name, str) and _MODULE_NAME_RE.fullmatch(name)):
                    return {}
                facts: dict = {"missing_dependency": True}
                if not self.sensitive_argv or self._module_attributable_to_script(name):
                    facts["missing_module"] = name
                return facts
            cur = cur.__cause__ or cur.__context__
            seen += 1
        return {}

    def _module_attributable_to_script(self, name: str) -> bool:
        folded = name.casefold()
        for token in self.script_argv:
            t = str(token).casefold()
            if t and (t in folded or folded in t):
                return False
        return name in _static_import_roots(self.script)

    def _fidelity_summary(self) -> dict | None:
        """本轮账本的 `fidelity` 段（闭集枚举 + 计数）；有弱信号时在 worker.log 留一行供诊断。"""
        ledger = self._blocked_ops
        if ledger is None:
            return None
        summary = ledger.summary()
        if summary["level"] != figcapture.BlockedFileOps.LEVEL_NONE_OBSERVED:
            print(f"[fidelity] {json.dumps(summary, ensure_ascii=False)}", file=sys.stderr)
        return summary

    def build_result(self, timings: dict) -> dict:
        """v1 build 响应的 body（分派逻辑在 `wireproto.V1Handler`）。

        `descriptors` 与 `runtime`（执行侧自报的解释器 / prefix / 关键包版本 /
        实际 cwd，ADR 0053）都是加字段，不升协议版本（ADR 0003 §1）；**只在 v1
        出现**，legacy 信封的形状一字不改（与 `timings` 同一条纪律）。`runtime`
        在 build 之后现量：cwd 那一项要的是脚本**真正**跑在哪个目录里。
        """
        return {
            **self._stems_summary(),
            "descriptors": self._descriptor_cache,
            "savefig_observations": self.session.savefig_observations.report(),
            **(
                {"artifact_probe": self.session.artifact_probe}
                if self.session.artifact_source is not None
                else {}
            ),
            "runtime": figsession.runtime_report(
                inputs=self._inputs_report, fidelity=self._fidelity_summary()
            ),
            # 本次 build 实际用到的每一问（ADR 0099 §二）：写回的一次性重放按它严格重放；T08 起带上下文摘要，
            # 口令那一问只有 `secret: true`、没有答案（记账里本来就没有）
            "script_inputs": [
                {
                    **{k: r.get(k) for k in ("index", "kind", "prompt", "context", "answer")},
                    **({"prompt_id": r["prompt_id"]} if "prompt_id" in r else {}),
                    **({"secret": True} if r.get("secret") else {}),
                }
                for r in (self._input_channel.record if self._input_channel else [])
            ],
        }


def _json_default(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


def _parse_write_roots(raw) -> list[str] | None:
    """`--write-roots` 的 JSON 数组；没给 = None（不装绊线）。给了却坏了**不**当成没给：
    绊线是安全边界，参数坏了宁可让这条会话起不来（`SystemExit`），也不静默退回不设防。"""
    if raw is None:
        return None
    try:
        roots = json.loads(raw)
    except ValueError:
        roots = None
    if not isinstance(roots, list) or not all(isinstance(r, str) and r for r in roots):
        raise SystemExit("--write-roots 必须是非空字符串的 JSON 数组")
    return roots


def _parse_remap(raw) -> list[dict]:
    """`--input-remap` 的 JSON → 校验过的规则；坏了就当没有（渲染照跑，只是不改道）。"""
    if not raw:
        return []
    try:
        return figcapture.clean_remap_rules(json.loads(raw))
    except ValueError:
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--figures-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sandbox", required=True)
    # ADR 0047：给了就把 cwd 切到这里（脚本目录）而不是沙盒；沙盒仍是写入边界的参照
    ap.add_argument("--cwd", default=None)
    # ADR 0106：用户指认过的只读改指表（JSON 数组）；没有规则时不出现
    ap.add_argument("--input-remap", default=None)
    # Only the opaque identity is public; exact argv arrives over the request pipe.
    ap.add_argument("--run-config", default="")
    # #898：写入根绊线的授权根（JSON 数组）；只有 MCP 来源的运行才出现，没有它 = 不装（旧 argv 逐字节不变）
    ap.add_argument("--write-roots", default=None)
    ap.add_argument("--entry", default="main")
    ap.add_argument("--preview-dpi", type=int, default=200)

    # 协议管道钉死 UTF-8。Windows 的默认 stdio 编码跟着系统区域走（常是
    # cp1252/cp936），而回应里 ensure_ascii=False——中文标签、µ、⁻¹ 这类字符
    # 一出现就 UnicodeEncodeError 把 worker 整个打死，表现为「worker 无响应」。
    # errors="replace" 是最后一道保险：宁可某个字符显示成 ? 也不能让会话崩掉。
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # 硬崩溃（access violation / segfault / abort：DLL 冲突、C 扩展里的越界）不经过
    # Python 的异常机制，进程当场消失，worker.log 里一个字都没有——父进程只看得到
    # 管道 EOF，报出来的是一句「渲染进程崩溃」，用户与我们都无从下手（issue #435
    # 的诊断包里正是 24 条这样的空记录）。faulthandler 让 CPython 在那一刻把
    # Python 栈写进 stderr（就是 worker.log）：崩在哪个 import、哪句 draw 一眼可见。
    # 装在 stderr 重配之后：它记的是**此刻**的 fd。3.14 起默认还会附一段 C 栈
    # （二三十行 `Binary file …`），把最有用的 Python 帧顶出日志尾巴——关掉它，
    # 各版本给出同一形状；老版本没有这个形参就按老样子开。
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True, c_stack=False)
    except TypeError:
        faulthandler.enable(file=sys.stderr, all_threads=True)

    worker = Worker(ap.parse_args())

    # 协议管道的引用在这里定死：build 期间 `sys.stdin` 会被换成给脚本用的桥接对象（ADR 0099），
    # 协议循环读的永远是这一个
    protocol_in = sys.stdin
    # A private stream survives sensitive-session redirection of the public fd 1.
    # It is non-inheritable, so user subprocesses only inherit the discard pipe.
    protocol_fd = os.dup(sys.stdout.fileno())
    os.set_inheritable(protocol_fd, False)
    protocol_out = os.fdopen(protocol_fd, "w", encoding="utf-8", errors="replace")
    try:
        for line in protocol_in:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError as exc:
                # 连信封都解析不出来，无从判断对方说的是哪套协议：按 v1 的错误
                # 形状回（request_id 只能是 null），至少 code 是可读的。
                resp = wireproto.v1_error({}, ProtocolError("bad_request", f"JSON 解析失败: {exc}"))
                resp = worker.diagnostic_response(resp)
                protocol_out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                protocol_out.flush()
                continue
            try:
                resp = wireproto.respond(worker, req, legacy=worker.handle)
            except SystemExit:
                break
            except Exception as exc:  # noqa: BLE001 — 结构化返回，进程不退出
                resp = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
            resp = worker.diagnostic_response(resp)
            # `allow_nan=False`：**NaN / Infinity 不是 JSON**（RFC 8259），
            # 而 Python 的 `json.dumps` 默认会把它们当字面量写出去、`json.loads`
            # 也照收——于是 Python 渲染池一路绿灯，而 workerd（Rust serde_json）
            # 严格拒收整帧、报「往协议管道里写了非 JSON 的内容」并重启会话。
            # 同一份响应，两条控制面两个结果，而症状指向的是「协议错乱」，
            # 与真实原因（某个包围盒是 inf）毫不相干。
            #
            # 几何那一层已经有总闸（`manifest._finite_geometry`），这里是**底线**：
            # 将来别处再漏一个非有限值时，它变成一条**结构化错误**（两条控制面
            # 表现一致、说得出是哪个字段），而不是一条只在其中一条上炸的坏帧。
            try:
                line = json.dumps(resp, ensure_ascii=False, default=_json_default, allow_nan=False)
            except ValueError as exc:
                line = json.dumps(
                    {
                        "ok": False,
                        "code": "non_finite_response",
                        "error": f"响应里有非有限数值，无法编成合法 JSON: {exc}",
                        "request_id": (req or {}).get("request_id")
                        if isinstance(req, dict)
                        else None,
                    },
                    ensure_ascii=False,
                )
            protocol_out.write(line + "\n")
            protocol_out.flush()
    finally:
        _stop_output_drainer(worker._private_output)
        protocol_out.close()


if __name__ == "__main__":
    main()
