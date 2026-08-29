#!/usr/bin/env python3
"""Tavotto native bridge runner —— **交给用户自己那个 Python 去执行的那份代码**。

    <用户的 python>  /绝对路径/bridge_runner.py  <bridge 参数> -- <用户的参数>

用户环境里**不需要也不允许**安装 Tavotto（ADR 0020 §3）：科研项目的
`.venv` 里往往只有 matplotlib / numpy / pandas / 项目自己的包，要求
`pip install tavotto` 既是门槛也是污染。所以这份文件靠绝对路径被调用，
自己把需要的引擎模块装进一个**私有包命名空间**（见 `bridgeboot`）。

它与 safe worker（`worker.py`）的分工：

| | safe worker | native bridge runner |
|---|---|---|
| 解释器 | Tavotto 挑 | **用户 invocation 里那一个** |
| cwd | 沙盒 | **用户的 cwd 原样**（继承，不动） |
| argv | `[脚本自身]` | **用户的原样** |
| savefig | 吞掉 | **透传**（照常写文件）+ 捕获 |
| 写/删守卫 | 有 | **无**（脚本拥有用户的全部权限） |
| stdout | 重定向到 stderr | **原样是用户的**（协议走独立 socket） |
| 控制通道 | stdin/stdout 行协议 | 127.0.0.1 loopback + token |
| 协议信封 | worker v1 | **同一个 worker v1**（`wireproto`） |
| Figure 编辑语义 | `figsession` | **同一个 `figsession`** |

Tavotto adds hooks, not privileges：本 runner 不给用户脚本任何它自己敲
`python figure.py` 时没有的权限——不开网络、不放宽文件系统、不改 env、
不代它起子进程。

## 线程模型

**控制循环跑在主线程上，全程只有主线程。** 用户脚本在主线程里创建 Figure，
override / canvas.draw / savefig 也必须在主线程执行（matplotlib 不是线程
安全的）。后台线程读 socket 再回主线程投递是可行的设计，但"没有后台线程"
是更强的保证：不存在的线程不会在某次重构后开始动 Figure。
`figsession.LiveFigureSession` 另有一条线程身份断言兜底。

代价是：脚本正在跑的时候没人读 socket，父进程的请求会等到下一个屏障。
这是**诚实的**——那时候本来就还没有 Figure 可编辑。

## 屏障（barrier）

    plt.show()  →  收 Gcf  →  进屏障（服务控制循环）  →  收到 continue  →  返回 → 脚本继续
    脚本结束    →  收 Gcf  →  进屏障  →  收到 shutdown/continue  →  进程退出

屏障是**用户主线程**停在那里服务请求的一段时间，不是另一个线程。
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import traceback
import types

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 第一件事：把 CPython 自动塞进来的「脚本自己的目录」摘掉。
# 留着它 = engine 目录常驻用户的 sys.path[0] = 用户项目里的 manifest.py /
# overrides.py / config.py 被我们顶掉。理由与看护见 bridgeboot 模块头。
# ---------------------------------------------------------------------------
_ENGINE_DIR_WAS_ON_PATH = False
if sys.path and os.path.abspath(sys.path[0]) == _HERE:
    del sys.path[0]
    _ENGINE_DIR_WAS_ON_PATH = True

# bridgeboot 自己也得装进来，而且**不能**靠 sys.path（那正是它要防的事）。
# 按文件路径直接加载，模块名带 `tavotto_bridge_` 前缀——用户项目里不可能有
# 同名模块，而它会留在 sys.modules 里（钩子对象活在这条闭包上）。
_boot_spec = importlib.util.spec_from_file_location(
    "tavotto_bridge_boot", os.path.join(_HERE, "bridgeboot.py")
)
bridgeboot = importlib.util.module_from_spec(_boot_spec)
sys.modules["tavotto_bridge_boot"] = bridgeboot
_boot_spec.loader.exec_module(bridgeboot)

#: 第一阶段只装纯标准库的那两个（捕获策略 + patch 规范化）。**绝不在这里装
#: manifest / overrides**——它们在模块层 import matplotlib 与 numpy，而
#: `import matplotlib` 会当场读 cwd 下的 matplotlibrc、钉死 rcParams，
#: 用户脚本自己那句 `matplotlib.use(...)` 的语义就不一样了。
_PHASE1 = ("figcapture", "patchspec")
#: 第二阶段（屏障那一刻才装）：要 matplotlib/numpy 的那几个，而那时用户早就
#: import 过了；外加只被它们平铺 import 到的纯标准库模块。
#:
#: **平铺 import 进来的那一层也要列在这里。** `bridgeboot._TOPLEVEL_TO_RESTORE`
#: 只保证「顶层名字还得回去」，保证不了「该进私有包的都进了」——漏掉的那个
#: 会以真·顶层模块的身份装在用户进程里，`__name__` 不带包前缀、私有包里也取
#: 不到它，第二个消费者还会再装一份（`load_engine_modules` 注释里警告的「两份
#: figcapture」同款）。
_PHASE2 = (
    "pathgeom",
    "overrides",
    "manifest",
    "previewbudget",
    "preview_complexity",
    "preview_hybrid",
    "figsession",
    "wireproto",
)

_PKG = bridgeboot.load_engine_modules(_HERE, _PHASE1)
figcapture = _PKG.figcapture

#: 捕获表（第一阶段就要用，那时还没有 LiveFigureSession——它要 matplotlib）。
_CAPTURE: dict = {}
_CAPTURE_SOURCE: dict = {}
#: pyplot 兜底因上限丢掉的张数
_DROPPED = 0
#: 引擎自己写盘（预览 / 导出）时暂停捕获——否则 export 到任意路径会被
#: 当成用户脚本的一次 savefig，凭空多出一个 stem。
_CAPTURING = True
_REAL_SAVEFIG = None
_REAL_SHOW = None


# ---------------------------------------------------------------------------
# 钩子
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _no_capture():
    """引擎自己出图的那一段：透传照旧，但不记进捕获表。"""
    global _CAPTURING
    prev = _CAPTURING
    _CAPTURING = False
    try:
        yield
    finally:
        _CAPTURING = prev


def _install_savefig_hook(mfigure) -> None:
    """记录 + **透传**（native 的 `passthrough_savefig=True`）。

    与 safe worker 相反：那边吞掉写盘（沙盒纪律），这边照常写。
    `tavotto run` 的承诺是「与你自己在终端里跑这条命令完全等同」，
    而用户的命令本来就会产出那些 PDF/PNG。
    """
    global _REAL_SAVEFIG
    if _REAL_SAVEFIG is not None:
        return
    _REAL_SAVEFIG = mfigure.Figure.savefig

    def _patched_savefig(self, fname, *args, **kwargs):
        if _CAPTURING:
            stem = figcapture.savefig_stem(fname)
            if stem and stem not in _CAPTURE:
                _CAPTURE[stem] = self
                _CAPTURE_SOURCE[stem] = figcapture.SOURCE_SAVEFIG
        return _REAL_SAVEFIG(self, fname, *args, **kwargs)

    mfigure.Figure.savefig = _patched_savefig


def _install_show_hook(plt, barrier) -> None:
    """`plt.show()` = 「我画完了，看看吧」——native bridge 的天然入口。

    语义（ADR 0020 §5，与 matplotlib 自己的约定对齐）：

    * `show()` / `show(block=True)` —— 收 Gcf，**进屏障**：用户在 Tavotto 里
      编辑，点「继续」之后 show() 返回、脚本接着往下跑。这与交互式后端的
      行为同构（窗口关掉之前 show() 不返回）。
    * `show(block=False)` —— 收 Gcf，**立刻返回**。脚本明确说了不要阻塞，
      我们就不阻塞；图仍然进捕获表，脚本结束时的屏障里还在。
    * 重复 `show()` —— 每次都收一遍 Gcf（新图进表），已捕获的按 Figure 身份
      去重（`collect_pyplot_figures`），不会同一张图两个 stem。

    **不把 show 永久换成 no-op**：那会让「先 show 再接着算」的脚本行为改变，
    也会让 `block=False` 与 `block=True` 变得不可区分。
    """
    global _REAL_SHOW
    if _REAL_SHOW is not None:
        return
    _REAL_SHOW = plt.show

    def _patched_show(*args, **kwargs):
        block = kwargs.get("block")
        if block is None and args:
            block = args[0]  # 老写法 plt.show(False)
        collect_pyplot(plt)
        if block is False:
            return None
        barrier("show")
        return None

    plt.show = _patched_show


def collect_pyplot(plt) -> None:
    """把还活在 Gcf 里、没被 savefig 认领的 Figure 补进捕获表。

    策略（stem 怎么编、怎么去重、上限多少）**是 figcapture 那一份**——
    safe worker 与浏览器 playground 用的是同一个函数。在这里另写一份的
    表现是：同一个脚本在 safe 与 native 两条入口里产出不同的 stem，而
    前端按 stem 索引一切。
    """
    global _DROPPED
    stems, dropped = figcapture.collect_pyplot_figures(_CAPTURE, _SCRIPT_STEM[0], plt)
    for stem in stems:
        _CAPTURE_SOURCE[stem] = figcapture.SOURCE_PYPLOT
    if dropped:
        _DROPPED += dropped
        print(
            f"[tavotto] 脚本留下的 pyplot Figure 超过 "
            f"{figcapture.MAX_PYPLOT_FALLBACK} 张上限，未捕获 {dropped} 张"
            f"（显式 savefig 的不受此限）",
            file=sys.stderr,
        )


#: 兜底 stem 的基名（`<脚本名>`、`<脚本名>-2`…）。列表包一层是因为钩子闭包
#: 在解析目标之前就装好了。
_SCRIPT_STEM = ["figure"]


# ---------------------------------------------------------------------------
# 用户上下文（与真实 python 对拍，见 tests/bridge/test_bridge_invocation.py）
# ---------------------------------------------------------------------------
def run_script(target: str, argv: list) -> None:
    """等价于 `python <target> <argv…>`。

    **不用 `runpy.run_path`**：它把 `__file__` 设成传进去的原串、把
    `__package__` 设成 `""`，而真实 `python file.py` 给的是**绝对**
    `__file__` 与 `__package__ is None`。这两个差异不是学术问题——
    `if __package__ is None:` 是相对 import 兜底的常见写法，
    `os.path.dirname(__file__)` 更是到处都是。所以这里按 CPython 自己的
    做法组装 `__main__`：绝对 `__file__`、`__package__=None`、`__spec__=None`、
    `sys.path[0]` = 脚本所在目录（绝对）。

    对拍口径：`tests/bridge/test_bridge_invocation.py` 拿同一个解释器、
    同一份 fixture 跑 `python probe.py` 与 bridge 两次，逐字段比。
    """
    abspath = os.path.abspath(target)
    sys.argv = [target, *argv]
    sys.path.insert(0, os.path.dirname(abspath))
    with io.open_code(abspath) as f:
        source = f.read()
    # `dont_inherit=True` **是必须的**：本文件自己有 `from __future__ import
    # annotations`，而 `compile()` 默认会把**调用处生效的** future 语句一并
    # 传给被编译的代码。于是用户脚本会在不知情的情况下拿到 PEP 563 语义——
    #
    #     x: NoSuchType = 5       # 直跑 python：NameError
    #                             # bridge（dont_inherit=False）：静默通过
    #
    # 这正是 native 最不能出的那类错：脚本在 Tavotto 里的行为与他自己敲
    # `python fig.py` 不一样，而且是**朝着"更宽松"的方向**静默不一样。
    # 脚本自己写的 `from __future__ import ...` 不受影响（它在源码里）。
    code = compile(source, abspath, "exec", dont_inherit=True)
    main_mod = types.ModuleType("__main__")
    main_mod.__file__ = abspath
    main_mod.__builtins__ = builtins
    main_mod.__package__ = None
    main_mod.__spec__ = None
    main_mod.__loader__ = None
    main_mod.__cached__ = None
    main_mod.__doc__ = None
    sys.modules["__main__"] = main_mod
    exec(code, main_mod.__dict__)


def resolve_module_origin(target: str) -> str:
    """`python -m <target>` 真正会执行的那个文件（runpy 的解析口径）。

    **必须在 `run_module` 之前算好。** `runpy.run_module(alter_sys=True)` 用
    `_TempModule` 把用户的模块**临时**装成 `sys.modules["__main__"]`，返回时
    恢复原样——也就是把 runner 自己装回去。跑完再去读
    `sys.modules["__main__"].__file__`，拿到的是 `bridge_runner.py`。

    后果不是"日志里名字难看"：描述符的 `script` 与 asset id 都由它派生，
    于是用户的图会被挂到 `runtime:bridge_runner.py#Fig1` 这个不存在的脚本上，
    保存重开之后 override 全成孤儿。只 `savefig` 不 `show` 的 module 目标
    （很常见）走的正是这条——它只有脚本结束那一次屏障，那时早已恢复过了。

    `pkg` → `pkg.__main__` 的处理与 runpy 同款。解析失败一律回空串，让
    `run_module` 去报它本来该报的错，不在这里抢着报一个我们编的。
    """
    import importlib.util  # noqa: PLC0415 — 与 runpy 同属 module 目标才付的成本

    try:
        spec = importlib.util.find_spec(target)
        if spec is not None and spec.submodule_search_locations is not None:
            spec = importlib.util.find_spec(f"{target}.__main__")
    except (ImportError, AttributeError, TypeError, ValueError):
        return ""
    return getattr(spec, "origin", "") or ""


def run_module(target: str, argv: list, on_origin=None) -> None:
    """等价于 `python -m <target> <argv…>`。

    `runpy.run_module(..., run_name="__main__", alter_sys=True)` 在
    `__name__` / `__package__` / `__spec__` / `__file__` / `sys.argv[0]`
    五项上与真实 `python -m` 开箱一致（对拍用例是版本无关的：同一个解释器
    跑两遍再比）。唯一要自己补的是 `sys.path[0]`：真实 `-m` 把**当前工作
    目录**（绝对）放在最前，而 runpy 不动 sys.path——不补的话
    `python -m paper.figure` 在 bridge 里连 `paper` 都 import 不到。

    `on_origin` 在**开跑之前**收到目标模块的源文件路径（见
    `resolve_module_origin`）。
    """
    import runpy  # noqa: PLC0415 — 只有 module 目标才需要，脚本目标不必付这份 import

    sys.argv = [target, *argv]  # run_module(alter_sys=True) 会把 argv[0] 换成模块文件
    sys.path.insert(0, os.getcwd())
    if on_origin is not None:
        on_origin(resolve_module_origin(target))
    runpy.run_module(target, run_name="__main__", alter_sys=True)


# ---------------------------------------------------------------------------
# 控制通道（loopback + token）
# ---------------------------------------------------------------------------
#: 父进程用这个环境变量把一次性 token 交给子进程。
#: **不走命令行**：argv 在同机上对别的用户可见（`ps`），环境在 macOS/Linux
#: 上默认只有属主读得到。这是 bridge 唯一注入的变量，进程一起来就摘掉，
#: 用户脚本看不到它（见 `_take_token`）。
TOKEN_ENV = "TAVOTTO_BRIDGE_TOKEN"

#: 握手帧与带外事件的键。v1 的**请求/响应信封零改动**——这两个键只出现在
#: 会话级的帧上，调用方按键名区分（`wireproto` 的响应永远带 `protocol_version`）。
HELLO_KEY = "bridge_hello"
EVENT_KEY = "bridge_event"

#: 用户在 UI 里点"终止脚本"时本进程的退出码（ADR 0021 §10.2）。
#: **与 `runcodes.EXIT_TERMINATED` 严格同源**——这份文件被用户的解释器按路径
#: 执行，import 不到 `tavotto.*`，所以只能各写一份、由用例逐字节对拍
#: （`tests/native/test_run_codes.py::test_the_runner_and_the_cli_agree_on_the_terminate_code`）。
TERMINATE_EXIT = 5


def _take_token() -> str:
    """取走 token 并**从 os.environ 里摘掉**。

    用户脚本不该看见它：它是 Tavotto 与自己子进程之间的凭据，留在环境里
    等于交给脚本以及脚本起的每一个子进程。
    """
    return os.environ.pop(TOKEN_ENV, "")


class Control:
    """与父进程的一条控制连接。**只在主线程上使用。**"""

    def __init__(self, host: str, port: int, token: str):
        self.sock = socket.create_connection((host, port), timeout=30.0)
        self.sock.settimeout(None)  # 屏障里要一直等（用户在编辑，没有超时可言）
        # 通道**两侧都钉 UTF-8**（父进程那侧同样 `makefile(encoding="utf-8")`）：
        # Windows 的默认 stdio 编码跟系统区域走（常是 cp936/cp1252），而响应里
        # ensure_ascii=False——中文标签、µ、⁻¹ 一出现就 UnicodeEncodeError。
        #
        # **刻意不 reconfigure 用户的 sys.stdout/stderr**（safe worker 那边会，
        # 因为协议就跑在它的 stdout 上）。这里协议在独立 socket 上，而用户的
        # stdio 是他程序的一部分——替他改编码就不是"与你自己敲那条命令等同"了。
        self.rfile = self.sock.makefile("r", encoding="utf-8", newline="\n")
        self._send({HELLO_KEY: 1, "token": token, "pid": os.getpid(), "protocol_version": 1})
        line = self.rfile.readline()
        if not line:
            raise ConnectionError("父进程在握手时就关掉了连接")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise ConnectionError(f"握手被拒绝: {resp.get('code') or resp}")

    def _send(self, obj: dict) -> None:
        self.sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def event(self, name: str, **fields) -> None:
        self._send({EVENT_KEY: name, **fields})

    def respond(self, obj: dict) -> None:
        # `allow_nan=False`：NaN / Infinity 不是 JSON（RFC 8259）。Python 的
        # json 两头都照收，而 Rust 侧（serde_json）会拒收整帧并重启会话——
        # 同一份响应两条控制面两个结果，症状还指向「协议错乱」。与 worker.py
        # 的底线同一条。
        try:
            line = json.dumps(obj, ensure_ascii=False, default=_json_default, allow_nan=False)
        except ValueError as exc:
            line = json.dumps(
                {
                    "ok": False,
                    "code": "non_finite_response",
                    "error": f"响应里有非有限数值，无法编成合法 JSON: {exc}",
                    "request_id": obj.get("request_id"),
                },
                ensure_ascii=False,
            )
        self.sock.sendall((line + "\n").encode("utf-8"))

    def readline(self) -> str:
        return self.rfile.readline()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.rfile.close()
        with contextlib.suppress(OSError):
            self.sock.close()


def _json_default(o):
    try:
        return float(o)
    except (TypeError, ValueError):
        return str(o)


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
class BridgeRun:
    """一次 native bridge 运行的全部可变状态。"""

    def __init__(self, args):
        self.args = args
        self.control: Control | None = None
        self.session = None  # figsession.LiveFigureSession（第二阶段才有）
        self.handler = None  # wireproto.V1Handler
        self.script_error: dict | None = None
        self.released = False
        self._pkg2_loaded = False
        #: 上一次屏障离开时保存下来的 Tavotto override（stem → 全量 patch 列表）。
        #: **它不在 Figure 上**——离开屏障之前 Figure 已经被恢复成脚本原样了
        #: （ADR 0021 §8）。下一个屏障按稳定 gid 重放。
        self.saved_patches: dict = {}
        #: 上一次重放里没找到落点的那些（孤儿）。如实报给上层，**绝不落到
        #: "最像的对象"上**。
        self.rebase_warnings: dict = {}
        #: 已经进过至少一个屏障。第一个屏障没有可 rebase 的东西——states 就是
        #: 那一刻建的。
        self._had_barrier = False
        #: 上一个屏障离开时**已经存在**的 stem。这一轮新出现的图不需要 rebase
        #: （`instrument_all()` 刚给它们采过 baseline），重复一遍只是多一次
        #: savefig。
        self._known_stems: set = set()
        #: 用户在 UI 里明确点了"终止脚本"。**退出码要确定**（ADR 0021 §10.2），
        #: 所以除了抛 SystemExit 还要记一笔：用户脚本完全可能把它吞掉
        #: （`except BaseException:` / `except SystemExit:` / 一个 `finally`
        #: 里再 return），那时"终止"就变成了一次静默的普通退出。
        self.terminated = False

    # ---- 第二阶段装载 ----
    def _ensure_engine(self):
        """屏障那一刻才装 matplotlib 相关的引擎模块。

        到这里 matplotlib 必然已经被用户代码 import 过（否则不会有 Figure），
        所以这次 import 不会改变任何 backend 决策。
        """
        if self._pkg2_loaded:
            return
        bridgeboot.load_engine_modules(_HERE, _PHASE2)
        self._pkg2_loaded = True

    def _sync_captures(self) -> None:
        """把模块级捕获表里**还没进会话**的图补进去。

        钩子（savefig / show 兜底）写的是模块级的 `_CAPTURE`——它们是类属性
        级的 monkeypatch，拿不到 BridgeRun 实例。会话是在**第一个屏障**那一刻
        才建的，此后脚本继续跑、继续产图，那些图只会落进 `_CAPTURE`。

        不同步的后果：脚本 `show()` → 编辑 → 继续 → 再画一张 → 再 `show()`，
        第二个屏障里**看不到第二张图**（stems / build 响应 / 可编辑会话里都
        没有它），而脚本明明画出来了。用户会数图。

        `add_figure` 对已有 stem 返回 False 不覆盖，所以这里是幂等的；
        `instrument_all()` 也只给还没有 FigState 的图建状态——已经在编辑的
        那些带着用户的 override，重建等于把编辑丢掉。
        """
        for stem, fig in _CAPTURE.items():
            self.session.add_figure(stem, fig, _CAPTURE_SOURCE[stem])

    def _ensure_session(self):
        self._ensure_engine()
        if self.session is not None:
            self._sync_captures()
            self.session.instrument_all()
            return self.session
        figsession = _PKG.figsession
        wireproto = _PKG.wireproto

        run = self

        class _NativeSession(figsession.LiveFigureSession):
            def real_output(self):
                return _no_capture()

        class _NativeHandler(wireproto.V1Handler):
            """native 的信封处理：**只多两个命令**，其余逐字复用 v1。

            `continue` —— 放开屏障（ADR 0020）。
            `terminate` —— 用户在 UI 里明确点了"终止脚本"（ADR 0021 §10.2）。
            它**不伪装成 continue**：退出码是确定的 `TERMINATE_EXIT`，
            桌面据此把会话记成"被终止"而不是"正常结束"。
            """

            _EXTRA = frozenset({"continue", "terminate"})

            def commands(self):
                return wireproto.V1_COMMANDS | self._EXTRA

            def build_result(self, timings: dict) -> dict:
                return run.build_result()

            def handle_extra(self, cmd, req, payload):
                if cmd == "continue":
                    run.released = True
                    return {"released": True}
                if cmd == "terminate":
                    run.terminated = True
                    run.released = True
                    # 与 `shutdown` 同一条出路（`wireproto` 把 SystemExit 原样
                    # 抛出去），只是带一个确定的退出码。
                    raise SystemExit(TERMINATE_EXIT)
                raise wireproto.ProtocolError("unknown_cmd", f"未知指令: {cmd}")

        self.session = _NativeSession(self.args.out_dir, self.args.preview_dpi)
        self._sync_captures()
        self.session.instrument_all()
        self.handler = _NativeHandler(self.session)
        return self.session

    # ---- build 响应 ----
    def build_result(self) -> dict:
        self._ensure_session()
        _resolve_module_source(self.args)
        out = self.session.stems_summary(_DROPPED)
        out["descriptors"] = self.session.descriptors(
            script=self.args.rel_target,
            entry=self.args.entry,
            execution_profile=figcapture.PROFILE_NATIVE,
            source_fingerprint=self.fingerprint(),
            # native 不谈「原始产物」：passthrough savefig 写到哪由用户脚本
            # 决定，那不是 Tavotto 管的图库。写回原件在 native v1 一律不提供。
            project_root=None,
        )
        if self.script_error:
            out["script_error"] = self.script_error
        return out

    def fingerprint(self) -> str:
        try:
            with io.open_code(self.args.source_path) as f:
                script_bytes = f.read()
        except OSError:
            script_bytes = b""
        matplotlib = sys.modules.get("matplotlib")
        return figcapture.source_fingerprint(
            script_bytes,
            script=self.args.rel_target,
            entry=self.args.entry,
            profile=figcapture.PROFILE_NATIVE,
            target_kind=self.args.target_kind,
            argv=tuple(self.args.user_argv),
            passthrough_savefig=True,
            matplotlib_version=getattr(matplotlib, "__version__", ""),
        )

    # ---- 屏障基准：restore before continue, rebase at next barrier ----
    def rebase(self) -> None:
        """把用户代码此后产生的新状态当作**新 baseline**，再重放保存的 patches。

        为什么必须重新 `instrument`：脚本在两个屏障之间可以往同一张 Figure 上
        再画一条线、换掉 locator、删掉一个 artist。`state.elements` / `index`
        是上一个屏障那一刻的快照，不重建的话新画的东西在 manifest 里根本
        不存在，而界面按 gid 索引一切。

        为什么重放要在 `instrument` 之后：`overrides.apply` 会在第一次碰到
        某个 `(gid, prop)` 时**采样**它的原值。离开上一个屏障时 `apply([])`
        已经把 `originals` 清空了，所以这里采到的是**脚本此刻的值**——这正是
        "新 baseline" 的定义。反过来（先重放再 instrument）采到的会是上一次
        的旧值，撤销时就回到了一个脚本从来没有过的状态。

        找不到落点的 patch 变成**孤儿警告**照实报出去。`overrides.apply` 自己
        就报「元素不存在（脚本可能已改动）」，这里只是把它收集起来——
        **绝不去找"最像的对象"**：猜错的表现是用户的字号改到了另一条曲线上，
        而两边都没有任何提示。
        """
        if not self._had_barrier or self.session is None:
            return  # 第一个屏障：states 就是这一刻建的，没有可 rebase 的
        manifest = _PKG.manifest
        overrides = _PKG.overrides
        self.rebase_warnings = {}
        for stem, state in self.session.states.items():
            if stem not in self._known_stems:
                # 这一轮**新出现**的图：`instrument_all()` 刚给它建过状态、
                # 出过预览，baseline 就是此刻。再来一遍纯属重复渲染。
                continue
            manifest.instrument(state)
            patches = self.saved_patches.get(stem) or []
            warnings = overrides.apply(state, patches) if patches else []
            if warnings:
                self.rebase_warnings[stem] = list(warnings)
            self.session.render(stem)

    def release_barrier(self, reason: str) -> None:
        """**任何**离开屏障的路径都要经过这里（ADR 0021 §8.1）。

            continue / detach / 桌面断开 / App 崩了 / relay EOF / shutdown

        做两件事，顺序不能换：保存当前的 Tavotto patch 列表，然后把 Figure
        恢复成**本次屏障的 script baseline**（`overrides.apply(state, [])` 的
        全量列表语义天然就是"回到原样"）。

        为什么这条对**故障路径**尤其重要：不做的话，App 一崩，用户的脚本反而
        带着 Tavotto 的 override 继续跑完——故障路径上的语义比正常路径更宽松，
        而这正是最难被发现的一类不一致（谁会去测"崩溃之后脚本看到了什么"）。
        """
        if self.session is None:
            return
        overrides = _PKG.overrides
        for stem, state in self.session.states.items():
            snapshot = self.session.snapshot(stem)
            if snapshot:
                self.saved_patches[stem] = snapshot
            else:
                self.saved_patches.pop(stem, None)
            try:
                overrides.apply(state, [])
            except Exception as exc:  # noqa: BLE001 — 一张图还原不了不该拖垮脚本
                print(
                    f"[tavotto] 恢复 {stem} 的脚本原样时出错（{reason}）: {exc}",
                    file=sys.stderr,
                )
        self._known_stems = set(self.session.states)
        self._had_barrier = True

    # ---- 屏障 ----
    def barrier(self, reason: str) -> None:
        """在**拥有 Figure 的这个线程**上服务控制循环，直到 continue/shutdown。

        没有控制通道时（`--report` 形态、对拍夹具）立刻返回——那时本来就
        没人要编辑。

        进屏障先 `rebase()`（新 baseline + 重放），离开屏障必经
        `release_barrier()`（保存 + 恢复原样）。**后者在 `finally` 里**：
        `return`（父进程走了）与 `raise`（shutdown / terminate）都要经过它。
        """
        if self.control is None:
            return
        self._ensure_session()
        self.rebase()
        wireproto = _PKG.wireproto
        self.released = False
        self.control.event(
            "barrier",
            reason=reason,
            stems=list(self.session.states),
            script_error=self.script_error,
            rebase_warnings=self.rebase_warnings or None,
        )
        try:
            while not self.released:
                line = self.control.readline()
                if not line:  # 父进程走了：不能把用户的脚本永远挂在这儿
                    self.control = None
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except ValueError as exc:
                    self.control.respond(
                        wireproto.v1_error(
                            {}, wireproto.ProtocolError("bad_request", f"JSON 解析失败: {exc}")
                        )
                    )
                    continue
                try:
                    resp = wireproto.respond(self.handler, req)
                except SystemExit:
                    self.control.event(
                        "shutdown", reason="terminate" if self.terminated else "client"
                    )
                    self.control.close()
                    self.control = None
                    raise
                self.control.respond(resp)
        finally:
            self.release_barrier(reason)
        self.control.event("released", reason=reason)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _parse_args(argv: list):
    ap = argparse.ArgumentParser(prog="tavotto-bridge-runner", allow_abbrev=False)
    ap.add_argument("--target-kind", choices=("script", "module"), required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--preview-dpi", type=int, default=200)
    ap.add_argument("--control-host", default="127.0.0.1")
    ap.add_argument("--control-port", type=int, default=0)
    ap.add_argument("--report", default="")
    ap.add_argument("--project-root", default="")
    if "--" in argv:
        cut = argv.index("--")
        mine, user_argv = argv[:cut], argv[cut + 1 :]
    else:
        mine, user_argv = argv, []
    args = ap.parse_args(mine)
    args.user_argv = user_argv
    return args


def _derive_target_facts(args) -> None:
    """把 target 化成 stem / 相对路径 / 源文件路径（descriptor 与 fingerprint 用）。

    module 目标的源文件要等 import 之后才知道，这里先给一个保守值，
    `main()` 在跑完之后按 `sys.modules['__main__'].__file__` 修正。
    """
    if args.target_kind == "script":
        abspath = os.path.abspath(args.target)
        args.source_path = abspath
        stem = os.path.splitext(os.path.basename(abspath))[0]
    else:
        args.source_path = ""
        stem = args.target.rpartition(".")[2] or args.target
    _SCRIPT_STEM[0] = stem
    args.entry = "__main__"
    root = args.project_root or os.getcwd()
    try:
        rel = os.path.relpath(args.source_path or os.path.join(root, stem + ".py"), root)
    except ValueError:  # Windows 跨盘符
        rel = os.path.basename(args.source_path) or (stem + ".py")
    if rel.startswith(".."):
        # 目标在项目根之外：描述符要求项目相对路径，退回文件名（asset id 的
        # 稳定性由「id 存在哪个项目的文档里」那一维兜着）。
        rel = os.path.basename(args.source_path) or (stem + ".py")
    args.rel_target = rel.replace("\\", "/")


def main(argv: list | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.out_dir:
        # **不往用户 home 里放一个 dotdir**。产物目录归 Tavotto 管
        # （父进程按 `config.data_dir()` 算好经 `--out-dir` 传进来），
        # 而 runner 跑在用户环境里、import 不到 `tavotto.engine.config`
        # ——猜一个 `~/.tavotto-*` 就是在用户地盘上留垃圾。没给就用临时目录：
        # 会话结束由系统回收，谁都不欠。
        import tempfile  # noqa: PLC0415 — 只有「没给 out-dir」这条支线要

        args.out_dir = tempfile.mkdtemp(prefix="tavotto-bridge-")
    _derive_target_facts(args)
    run = BridgeRun(args)

    # 钩子：**自己不 import matplotlib**，只在用户 import 到的那一刻挂上去。
    hook = bridgeboot.PostImportHook(
        {
            "matplotlib.figure": _install_savefig_hook,
            "matplotlib.pyplot": lambda plt: _install_show_hook(plt, run.barrier),
        }
    )
    hook.install()

    token = _take_token()
    if args.control_port:
        run.control = Control(args.control_host, args.control_port, token)

    exit_code = 0
    try:
        if args.target_kind == "script":
            run_script(args.target, args.user_argv)
        else:
            run_module(args.target, args.user_argv, on_origin=_set_module_source(args))
    except SystemExit as exc:  # 用户脚本自己 sys.exit(...)：图仍然算数
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException as exc:  # noqa: BLE001 — 脚本炸了也要把已有的图交出去
        exit_code = 1
        run.script_error = {
            "code": "script_error",
            "message": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        traceback.print_exc()
    finally:
        hook.uninstall()

    if run.terminated:
        # **确定的退出码**，不管用户脚本对 SystemExit 做了什么。
        # `except BaseException:` / `except SystemExit:` / `finally: return`
        # 都能把上面那个 5 吃掉，而"我点了终止"与"脚本自己正常退出了"必须
        # 在退出码上分得开（ADR 0021 §10.2）。
        exit_code = TERMINATE_EXIT

    _resolve_module_source(args)

    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        collect_pyplot(plt)

    if args.report:
        _write_report(run, args, exit_code)

    if run.control is not None and _CAPTURE:
        try:
            run.barrier("script_end")
        except SystemExit:
            pass
    if run.control is not None:
        run.control.event("exit", code=exit_code, figures=len(_CAPTURE))
        run.control.close()
    return exit_code


def _set_module_source(args):
    """给 `run_module` 的回调：把开跑前解析出来的源文件记进 args。"""

    def _record(origin: str) -> None:
        if origin:
            args.source_path = origin
            _resolve_module_source(args)

    return _record


def _resolve_module_source(args) -> None:
    """把 module 目标的相对路径算出来（描述符的 `script` / asset id 用它）。

    正常路径上 `args.source_path` 已经由 `run_module` 的 `on_origin` 回调在
    **开跑之前**填好了（见 `resolve_module_origin`）。这里的 `__main__` 兜底
    只在解析失败时才用得上，而且**显式排除 runner 自己**：runpy 跑完会把
    runner 装回 `sys.modules["__main__"]`，读到的就是 `bridge_runner.py`，
    那会把用户的图挂到一个不存在的脚本上。
    """
    if args.target_kind != "module":
        return
    if not args.source_path:
        cand = getattr(sys.modules.get("__main__"), "__file__", "") or ""
        if cand and os.path.abspath(cand) != os.path.abspath(__file__):
            args.source_path = cand
    if not args.source_path:
        return
    root = args.project_root or os.getcwd()
    try:
        rel = os.path.relpath(args.source_path, root)
    except ValueError:
        rel = os.path.basename(args.source_path)
    if rel.startswith(".."):
        rel = os.path.basename(args.source_path)
    args.rel_target = rel.replace("\\", "/")


def _write_report(run, args, exit_code: int) -> None:
    """机器可读的运行小结（对拍夹具与 spike CLI 用；不是产品契约）。

    捕获到图时**顺带把引擎跑一遍**（instrument + 首帧预览）：报告里的
    `stems` / `owner_thread` 因此是真跑出来的事实，而不是一份"看起来该有"
    的清单。没有图时一行 matplotlib 相关的代码都不碰。
    """
    stems: list = []
    owner_thread = None
    if _CAPTURE:
        session = run._ensure_session()  # noqa: SLF001 — runner 自己的会话
        stems = list(session.states)
        owner_thread = session.owner_thread
    payload = {
        "stems": stems,
        "owner_thread": owner_thread,
        "main_thread": threading.get_ident(),
        # 产物**实际**落在哪。判「有没有往用户 home 里写东西」要问的是这个，
        # 而不是「home 里多了什么」——后者的主语是整台机器，不是本 runner。
        "out_dir": os.path.abspath(args.out_dir),
        "exit_code": exit_code,
        "target_kind": args.target_kind,
        "target": args.target,
        "rel_target": args.rel_target,
        "figures": [{"stem": s, "capture_source": _CAPTURE_SOURCE[s]} for s in _CAPTURE],
        "dropped_figures": _DROPPED,
        "script_error": run.script_error,
        "engine_dir_was_on_sys_path": _ENGINE_DIR_WAS_ON_PATH,
        "engine_dir_on_sys_path_now": _HERE in [os.path.abspath(p) for p in sys.path],
        # 顶层同名模块**可以存在**——用户项目里就有一份 manifest.py，他
        # `import manifest` 拿到它天经地义。判据不是"名字在不在"，而是
        # **那个名字下的文件是不是我们的**。
        "toplevel_engine_module_files": {
            n: getattr(sys.modules[n], "__file__", "") or ""
            for n in bridgeboot.ENGINE_SIBLINGS
            if n in sys.modules
        },
        "matplotlib_backend": _backend_name(),
        "tavotto_importable": _tavotto_importable(),
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)


def _backend_name() -> str:
    mpl = sys.modules.get("matplotlib")
    if mpl is None:
        return ""
    try:
        return str(mpl.get_backend())
    except Exception:  # noqa: BLE001
        return ""


def _tavotto_importable() -> bool:
    """用户环境里到底有没有装 Tavotto——**报告里如实记一笔**。

    ADR 0020 §3 的承诺是「不要求用户环境安装 Tavotto」。这个字段让
    E2E 用例能证明它：跑得通，且这里是 False。
    """
    try:
        import importlib.util as _u  # noqa: PLC0415

        return _u.find_spec("tavotto") is not None
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    sys.exit(main())
