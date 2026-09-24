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
import contextlib
import faulthandler
import importlib
import importlib.util
import json
import os
import shutil
import sys
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

_boot_spec = importlib.util.spec_from_file_location(
    "tavotto_bridge_boot", os.path.join(str(HERE), "bridgeboot.py")
)
bridgeboot = importlib.util.module_from_spec(_boot_spec)
sys.modules["tavotto_bridge_boot"] = bridgeboot
_boot_spec.loader.exec_module(bridgeboot)

import matplotlib  # noqa: E402 —— safe 档由 Tavotto 挑解释器，backend 在装引擎之前钉死

matplotlib.use("Agg")
import matplotlib.figure as mfigure  # noqa: E402

#: safe worker 要装进私有包的引擎模块——**整条平铺 import 闭包**，一次装完
#: （safe 档不像 native 那样要分两阶段避开 matplotlib：这里 matplotlib 本来就先 import）。
_ENGINE_MODULES = (
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

#: 本 worker 的常驻会话。`_patched_savefig` 是模块级函数（要顶掉
#: `Figure.savefig` 这个类属性），拿不到 Worker 实例，只能走模块级引用。
SESSION: "SafeSession | None" = None

_intercept = True
_REAL_SAVEFIG = mfigure.Figure.savefig

#: 协议常量的唯一出处在 `wireproto`；这里 re-export 是为了「手工 echo 一条
#: 请求进来调试」时不必知道模块拆分（`worker.PROTOCOL_VERSION` 是老口径）。
PROTOCOL_VERSION = wireproto.PROTOCOL_VERSION
V1_COMMANDS = wireproto.V1_COMMANDS
ProtocolError = wireproto.ProtocolError
_ms = figsession.ms_since


class SafeSession(figsession.LiveFigureSession):
    """safe 档的 LiveFigureSession：引擎自己写盘时摘掉 savefig 拦截。

    build 期间脚本一个图文件都不写（沙盒纪律），但引擎自己的预览 SVG /
    PNG / 导出必须写得出去——`_real_output()` 就是那个窗口。native bridge
    的 savefig 本来就是透传，它用基类那个什么都不做的上下文。
    """

    def real_output(self):
        return _real_output()


def _patched_savefig(self, fname, *args, **kwargs):
    """通用兜底：raw fig.savefig 的脚本也被捕获；同 stem 的 pdf/png 只记一次。"""
    if not _intercept:
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)
    stem = figcapture.savefig_stem(fname)
    if stem and SESSION is not None:
        SESSION.add_figure(stem, self, figcapture.SOURCE_SAVEFIG)
    return None


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
SCRIPT_EXITED = "script_exited"


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


def _script_exit_error(exc: SystemExit) -> ProtocolError:
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
        return ProtocolError(
            SCRIPT_NEEDS_ARGUMENTS,
            f"脚本要求命令行参数，而 Tavotto 运行脚本时不带任何参数（sys.argv 只有脚本"
            f"自己），参数解析于是以 {call} 结束。给这些参数写默认值，"
            "或用 `tavotto run -- python 脚本.py 参数…` 让 Tavotto 跟着你自己的命令跑。",
            retryable=False,
            traceback_text=traceback.format_exc(),
            extra={"exit_code": status},
        )
    return ProtocolError(
        SCRIPT_EXITED,
        f"脚本调用了 {call} 提前结束。Tavotto 要的是脚本跑完后留在内存里的 "
        "Figure——去掉那句 exit，或改成只在出错时 exit。",
        retryable=False,
        traceback_text=traceback.format_exc(),
        extra={"exit_code": status},
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
        SESSION = SafeSession(self.out_dir, self.preview_dpi)
        super().__init__(SESSION)

    # ---------------- build ----------------
    def build(self, timings: dict | None = None) -> dict:
        """跑一次用户脚本，把产出的 Figure 全部收进内存。

        `timings` 非空时填两个数：`script_build_ms` 是整个 build（脚本 +
        instrument + 每个 stem 的首次预览），`script_exec_ms` 只是用户脚本
        自己那一段。两者分开是因为它们的改法完全不同——前者大头在用户的
        计算里（我们无能为力），差额才是引擎自己的开销。
        """
        t_build = time.perf_counter()
        if self.built:
            return self._stems_summary()
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

        # 删除守卫：fig6 用绝对路径删“过期输出”（ROOT/figures/...），沙盒 cwd
        # 挡不住，这里直接拒绝任何指向真实图库目录的删除（渲染用不到删除）
        real_figs = self.figures_dir
        real_unlink = Path.unlink

        def _guarded_unlink(p, missing_ok=False):
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过删除真实图库文件: {p}", file=sys.stderr)
                    return None
            except OSError:
                pass
            return real_unlink(p, missing_ok=missing_ok)

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

        def _guard_delete(name, real_fn):
            def guarded(path, *a, **kw):
                if "dir_fd" not in kw and _inside_real(path):
                    print(f"[guard] 跳过 {name} 真实图库文件: {path}", file=sys.stderr)
                    return None
                return real_fn(path, *a, **kw)

            return guarded

        os.remove = _guard_delete("删除", os.remove)
        os.unlink = _guard_delete("删除", os.unlink)
        os.rmdir = _guard_delete("删除目录", os.rmdir)
        os.rename = _guard_delete("改名", os.rename)
        os.replace = _guard_delete("改名", os.replace)
        shutil.rmtree = _guard_delete("删除目录树", shutil.rmtree)
        shutil.move = _guard_delete("移动", shutil.move)

        # 写入守卫：脚本的 write_caption 等用绝对路径 write_text 写真实图库
        # （fig9 的 *_caption.txt），沙盒 cwd 拦不住；导出走 savefig 不受影响
        real_write_text = Path.write_text

        def _guarded_write_text(p, *a, **kw):
            try:
                if p.resolve().is_relative_to(real_figs):
                    print(f"[guard] 跳过写入真实图库文件: {p}", file=sys.stderr)
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
        if self.workdir is None:
            figcapture.install_relative_read_fallback(
                str(self.script.parent), str(self.figures_dir)
            )
        # cwd 已经是脚本目录时不装回退：相对路径本来就指向项目，回退只会在
        # 「沙盒里有同名文件」这种不可能的前提上多一层看不见的改道。

        # 拦截必须发生在 import 脚本之前（多数脚本 from paper_style import save）
        mfigure.Figure.savefig = _patched_savefig

        # 脚本看到的 argv 必须是它自己的，不是 worker 的。不换的话
        # `sys.argv[1:]` 拿到的是 --script/--out-dir/--entry 这串内部参数，
        # 按参数命名输出的脚本会存出一堆叫 "--entry" 的图（试运行探测时
        # 当场撞见过）。真跑 `python fig.py` 时 argv 就只有脚本自己。
        # **排在 paper_style 之前**：那份私有模块也是用户代码，import 期间就可能
        # 解析参数（评审 #443）。
        sys.argv = [str(self.script)]

        # 脚本 import 了却从未用到、又没装的包不挡图（ADR 0061 §二 2026-09-24 修订）：判据与
        # 父进程的联合计划同一份（`figcapture.unused_imports`）——计划没要求装它，这里就得让
        # 那一行 import 过得去。读不了 / 解析不了就不装：脚本随后会以它自己的错误结束。
        try:
            import ast  # noqa: PLC0415

            unused = figcapture.unused_imports(ast.parse(self.script.read_bytes()))
        except (OSError, SyntaxError, ValueError):
            unused = frozenset()
        figcapture.install_unused_import_placeholders(str(self.script), unused)

        t_script = time.perf_counter()
        # `SystemExit` 不是 `Exception`：脚本末尾的 `sys.exit(main())` / `exit()` /
        # `quit()` 会一路穿过 `ensure_built` 的 `except Exception`，落到主循环
        # 那条给协议 `shutdown` 用的 `except SystemExit: break` 上——worker 悄无
        # 声息地退出，stdout EOF，supervisor 只能报「渲染进程崩溃」，worker.log
        # 里连一行 traceback 都没有。与 `python fig.py` 的语义对齐：退出码 0 /
        # None 就是脚本正常结束（已经画好的图照常捕获），非零才是它自己报的失败。
        # paper_style 的 import 也在这道保护里：它是用户代码，import 期间一样能 exit。
        with contextlib.redirect_stdout(sys.stderr):
            try:
                # paper_style 是某些图库的私有方言，不是引擎的依赖：没有就跳过，
                # 靠 _patched_savefig 这条通用兜底捕获。曾经这里是无保护的 import，
                # 任何不带 paper_style.py 的图库都会以 ModuleNotFoundError 开局。
                try:
                    import paper_style  # noqa: PLC0415
                except ImportError:
                    pass
                else:
                    # 与 `_patched_savefig` 同一条来源记账：paper_style.save 是显式
                    # 「保存这张图」，来源就是 savefig（以前这里不记来源，靠读取端
                    # `.get(stem, SOURCE_SAVEFIG)` 兜底——结果一样，现在是显式的）。
                    paper_style.save = lambda fig, stem, outdir="figures": SESSION.add_figure(
                        stem, fig, figcapture.SOURCE_SAVEFIG
                    )
                if self.entry == "__main__":
                    import runpy  # noqa: PLC0415 — 内联脚本（fig4c / fig_models）

                    runpy.run_path(str(self.script), run_name="__main__")
                else:
                    module = importlib.import_module(self.script.stem)
                    getattr(module, self.entry)()
            except SystemExit as exc:
                if exc.code not in (None, 0):
                    raise _script_exit_error(exc) from exc
        script_ms = _ms(t_script)

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
                self.session.capture, self.script.stem, _plt
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

        self.session.instrument_all()
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
        )

    def _stems_summary(self) -> dict:
        # `source` 是加字段，不升协议版本（ADR 0003 §1：两侧容忍未知字段）。
        return self.session.stems_summary(self.dropped_figures)

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
        if cmd == "build":
            return {"ok": True, **self.build()}
        if not self.built:
            self.build()

        stem = req.get("stem", "")
        states = self.session.states
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
        try:
            self.build(timings)
        except ProtocolError:
            # build 自己已经分好类的（脚本要命令行参数 / 脚本主动 exit）原样上抛
            raise
        except Exception as exc:  # noqa: BLE001 — 转成结构化错误，进程不退出
            raise ProtocolError(
                "script_error",
                f"脚本执行失败: {exc}",
                retryable=False,
                traceback_text=traceback.format_exc(),
            ) from exc

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
            "runtime": figsession.runtime_report(inputs=self._inputs_report),
        }


def _json_default(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--figures-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sandbox", required=True)
    # ADR 0047：给了就把 cwd 切到这里（脚本目录）而不是沙盒；沙盒仍是写入边界的参照
    ap.add_argument("--cwd", default=None)
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

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            # 连信封都解析不出来，无从判断对方说的是哪套协议：按 v1 的错误
            # 形状回（request_id 只能是 null），至少 code 是可读的。
            resp = wireproto.v1_error({}, ProtocolError("bad_request", f"JSON 解析失败: {exc}"))
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        try:
            resp = wireproto.respond(worker, req, legacy=worker.handle)
        except SystemExit:
            break
        except Exception as exc:  # noqa: BLE001 — 结构化返回，进程不退出
            resp = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
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
                    "request_id": (req or {}).get("request_id") if isinstance(req, dict) else None,
                },
                ensure_ascii=False,
            )
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
