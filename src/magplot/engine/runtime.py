"""Magplot 内置渲染 runtime 的定位与校验（纯标准库，Flask 父进程 import）。

Windows 与 macOS 桌面版都随安装包附带一套 **Magplot 私有的** CPython + 科学栈：

    Windows: Magplot.exe → runtime\\python.exe    → engine/worker.py → 用户的脚本
    macOS:   Magplot.app → runtime/bin/python3    → engine/worker.py → 用户的脚本

这样普通用户装完就能渲染——不需要先去装 Python、不需要联网、不依赖 PATH、
Windows Store Python、Homebrew 或 Conda，也**绝不动用户已有的任何环境**。

两个平台的 runtime 来源不同（Windows 是官方 embeddable，macOS 是
python-build-standalone 的可重定位发行版），但**对外形状一致**：一个目录，
里面有解释器和 `runtime-manifest.json`。上层（pool / bootstrap / diagnostics）
只认 `runtime_root()` / `status()` / `bundled_python()`，不关心里面长什么样。

为什么单独一个模块：路径判断散在 pool / bootstrap / diagnostics 三处的话，
frozen 与源码模式的差异就会各写一遍，迟早对不上。这里是唯一出处。

runtime 从哪来：`scripts/build_worker_runtime.py` 按 `packaging/runtime-lock.json`
构建（锁定版本 + SHA-256 + 完整传递闭包），产物不进 Git。
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys

RUNTIME_DIR_NAME = "runtime"
MANIFEST_NAME = "runtime-manifest.json"
#: 认得的 manifest schema。构建脚本写的版本比这里新 = 装了个更新的包但主程序是旧的，
#: 当作损坏处理而不是硬着头皮往下跑。
#:
#: schema 2（2026-08-18）：macOS 也开始发内置 runtime，manifest 因此必须能回答
#: 「这份 runtime 是给哪个平台、哪个架构的」——schema 1 里那两个字段只是记录，
#: 没人校验，把 Windows 的 runtime 塞进 .app 也照样「valid」。
MANIFEST_SCHEMA = 2

PROBE_TIMEOUT_S = 60

# 机器可读的失败原因（前端据此提示「安装文件不完整，请重新安装」）
CODE_MISSING = "bundled_runtime_missing"
CODE_INVALID = "bundled_runtime_invalid"

#: **所有 Windows 子进程的 creationflags 唯一出处**——engine/ 下每个
#: `subprocess.Popen` / `subprocess.run` 都要传它。
#:
#: 桌面版是 GUI 子系统进程（`console=False` 打包，自己没有控制台）。它 spawn
#: 一个控制台子系统的子进程（python.exe / pip / codex）时，Windows 会现分配
#: 一个新控制台并显示出来——用户每渲染一张图就看见黑框闪一下。
#:
#: 放在这里是因为 CLAUDE.md 把本模块定为 Windows 平台判断的唯一出处；一个
#: `int` 常量不破坏「纯标准库」边界。非 Windows 上值为 0，等同于不传。
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def is_frozen() -> bool:
    """是否跑在 PyInstaller 打出的独立应用里（.app / .exe）。"""
    return bool(getattr(sys, "frozen", False))


def ships_bundled_runtime() -> bool:
    """这个安装形态**本该**带内置 runtime 吗。

    两个桌面安装包都带（见 packaging/magplot.spec）：
      * Windows NSIS —— runtime 落在 sidecar 的 `_internal\\runtime`
      * macOS .app   —— runtime 落在 `Contents/Resources/sidecar/Magplot/_internal/runtime`

    pip / 源码 / 开发模式都不带，那里 runtime 缺失是正常状态，不该报
    「安装文件不完整」；Linux 目前没有桌面发行形态，同样不该报。

    这个判断只看「形态」不看「有没有」——正因为它回答的是「本该有」，
    `status()` 才能把「该带没带」与「本来就不带」分成两种结论。
    """
    return is_frozen() and host_os() in ("windows", "macos")


# ---------------------------------------------------------------------------
# 宿主平台
#
# 拆成两个可打桩的函数，而不是在 status() 里直接读 platform.machine()：
# 「架构对不上」这条路只有在 Intel 机器上装了 arm64 包时才走得到，真机复现
# 成本极高。做成函数就能在任何一台开发机上单测它（tests/test_bundled_runtime.py）。
# ---------------------------------------------------------------------------
#: 各家对同一个架构的叫法 → 我们的规范名。锁文件、manifest、`platform.machine()`
#: 三边用词都不一样（amd64 / x86_64 / AMD64、arm64 / aarch64），不归一就等着
#: 「明明是对的却报架构不符」。
_ARCH_ALIASES = {
    "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
    "arm64": "arm64", "aarch64": "arm64",
}


def normalize_arch(name: str | None) -> str:
    """架构名归一；认不出的原样回（小写），不硬塞进某一档。"""
    low = str(name or "").strip().lower()
    return _ARCH_ALIASES.get(low, low)


def host_os() -> str:
    """当前进程跑在哪个平台：windows / macos / linux / other。

    `os.name` 与 `sys.platform` 都可被 monkeypatch，测试据此模拟另一个平台。
    """
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def host_arch() -> str:
    """当前进程的架构（已归一）。

    重点是「**当前进程**」而不是「这台机器」：Apple Silicon 上用 Rosetta 跑
    x86_64 的 Magplot 时，该配的正是 x86_64 的 runtime——`platform.machine()`
    在 Rosetta 下如实回 x86_64，正合我们要的语义。

    `MAGPLOT_RUNTIME_HOST_ARCH` 是给交叉验证用的逃生门（构建机上想确认
    「这份 runtime 会被 arm64 的 app 接受吗」），不是给用户拿来强行装错架构的。
    """
    override = os.environ.get("MAGPLOT_RUNTIME_HOST_ARCH")
    if override:
        return normalize_arch(override)
    try:
        return normalize_arch(platform.machine())
    except Exception:      # noqa: BLE001 — 取不到架构不该把启动流程带崩
        return ""


# ---------------------------------------------------------------------------
# 定位
#
# 这一段**全程用 os.path 拼字符串，一个 pathlib 都不用**：`Path(...)` 会按
# `os.name` 分派 Posix/Windows 实现，在非目标平台上构造另一半直接抛
# UnsupportedOperation——那样连「在 macOS 上单测 Windows 的定位逻辑」都做不到。
# `os.path` 在 import 时就绑定好了，改 os.name 不影响它。
# ---------------------------------------------------------------------------
def _candidate_roots() -> list[str]:
    """runtime 目录的候选位置，按可信度排序。

    冻结后 onedir 的布局是 `Magplot(.exe)` + `_internal/`，`sys._MEIPASS` 指向
    `_internal`——runtime 通过 spec 的 datas 进包，落点就是 `_internal/runtime`。
    macOS 上这一整坨又被 Tauri 收进 `Magplot.app/Contents/Resources/sidecar/Magplot/`，
    但 `_MEIPASS` 仍然指得准，所以这里不需要为 .app 单开一条分支。

    exe 同级与 `exe/_internal` 两条也留着：手工摆放产物或换 PyInstaller 版本
    导致布局变化时不至于直接失灵。
    """
    roots: list[str] = []

    def add(p: str | None) -> None:
        if p and p not in roots:
            roots.append(p)

    # 显式覆盖：CI 与单元测试用它把 runtime 指到临时目录，不必真去打包。
    #
    # **它是排他的**——指了就只认这一个，指到一个坏的/空的目录也不再往下找。
    # 「覆盖了却被别处的 runtime 悄悄顶掉」是最难查的一种：你以为在验刚构建的
    # 那份，实际验的是仓库根上一次留下的产物，而两边的日志一模一样。
    override = os.environ.get("MAGPLOT_RUNTIME_DIR")
    if override:
        return [override]

    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            add(os.path.join(meipass, RUNTIME_DIR_NAME))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        add(os.path.join(exe_dir, RUNTIME_DIR_NAME))
        add(os.path.join(exe_dir, "_internal", RUNTIME_DIR_NAME))
    else:
        # 源码树：scripts/build_worker_runtime.py 默认产出到仓库根的 runtime/
        # __file__ = <root>/src/magplot/engine/runtime.py
        here = os.path.dirname(os.path.abspath(__file__))          # engine/
        pkg = os.path.dirname(here)                                # magplot/
        src = os.path.dirname(pkg)                                 # src/
        add(os.path.join(os.path.dirname(src), RUNTIME_DIR_NAME))  # <root>/runtime
        # 包同级（少见的手工布局；wheel 里**不会**有，见 pyproject 的 exclude）
        add(os.path.join(src, RUNTIME_DIR_NAME))
    return roots


def runtime_python(root: str) -> str:
    """runtime 里的解释器路径（**按当前平台的习惯**，不碰磁盘）。

    纯函数，故意不做存在性判断：`resolve_python()` 才是「实际用哪个」。
    分离开是因为两边的用途不同——这个负责「该长什么样」（可跨平台单测），
    那个负责「磁盘上是什么样」（要真去 stat）。
    """
    if os.name == "nt":
        return root.rstrip("\\/") + "\\python.exe"
    # POSIX 布局（macOS 的 python-build-standalone 就是这个形状）
    return root.rstrip("/") + "/bin/python3"


#: 两种 runtime 布局里解释器的固定落点。**按平台猜是不够的**：构建机可以在
#: Linux 上交叉产出 Windows 的 runtime，冒烟脚本也会在 macOS 上检视一份
#: Windows 产物；只认本平台那一种的话，那些场景一律报「不完整」。
_INTERPRETER_RELPATHS = (
    ("python.exe",),          # windows-embeddable
    ("bin", "python3"),       # POSIX 的惯例名
)

#: 版本化实体名（`bin/python3.13`）。python-build-standalone 里 `bin/python`
#: 与 `bin/python3` 都只是指向它的符号链接，而**构建时我们把这两个别名剪掉了**
#: ——Tauri 复制资源会把符号链接拍平成真副本，留着它们等于白搭两份 17 MiB
#: 的解释器（见 scripts/build_worker_runtime.prune_aliases）。上游自带的
#: pip / idle / pydoc 包装脚本 exec 的也正是这个版本化名字。
#:
#: **用 glob 而不是写死 `python3.13`**：写死的话，哪天把内置 runtime 升到
#: CPython 3.14，解释器会突然找不到，而症状是「安装文件不完整」——一个
#: 与真实原因毫不相干的提示。
_INTERPRETER_GLOB = ("bin", "python3.*")


def _interpreter_candidates(root: str) -> list[str]:
    """解释器的候选路径，按可信度排序（固定落点优先，版本化实体名兜底）。"""
    import glob as _g

    cands = [os.path.join(root, *rel) for rel in _INTERPRETER_RELPATHS]
    try:
        # `python3.13-config` 是个 shell 脚本，不是解释器——按后缀排掉，
        # 否则「找到了但一跑就报语法错」，比没找到还难查。
        cands += sorted(p for p in _g.glob(os.path.join(root, *_INTERPRETER_GLOB))
                        if not p.endswith("-config"))
    except OSError:
        pass
    return cands


def resolve_python(root: str) -> str | None:
    """磁盘上**真正存在**的那个解释器；都找不到回 None。"""
    for cand in _interpreter_candidates(root):
        try:
            if os.path.isfile(cand):
                return cand
        except OSError:
            continue
    return None


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST_NAME)


def runtime_root() -> str | None:
    """找到**看起来完整**的 runtime 目录；一个都没有回 None。

    判据是「解释器在 + manifest 在」，不做深校验——深校验在 `status()` 里，
    这样「目录在但内容坏了」能报 invalid 而不是被当成 missing。
    """
    for root in _candidate_roots():
        try:
            if resolve_python(root) and os.path.isfile(manifest_path(root)):
                return root
        except OSError:
            continue
    return None


def _any_root() -> str | None:
    """候选里第一个存在的目录（哪怕内容不全）——用于区分 missing / invalid。"""
    for root in _candidate_roots():
        try:
            if os.path.isdir(root):
                return root
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def read_manifest(root: str) -> dict | None:
    """读 runtime-manifest.json；缺失/坏了/schema 不认识一律回 None。

    只校验**形状**，不校验「是不是给这台机器的」——后者是 `status()` 的事，
    因为它要分别报 invalid 的原因。诊断包也要能读出一份「架构不对」的清单
    给人看，读不出来就只剩一句「损坏」，等于什么都没说。
    """
    try:
        with open(manifest_path(root), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != MANIFEST_SCHEMA:
        return None
    if not isinstance(data.get("packages"), dict) or not data["packages"]:
        return None
    if not isinstance(data.get("python"), dict) or not data["python"].get("version"):
        return None
    plat = data.get("platform")
    # schema 2 起 platform.os / platform.arch 是**硬要求**：没有它们就无从判断
    # 这份 runtime 是不是给本机的，而「装错架构」正是要挡的那一档。
    if not isinstance(plat, dict) or not plat.get("os") or not plat.get("arch"):
        return None
    return data


def manifest() -> dict | None:
    root = runtime_root()
    return read_manifest(root) if root is not None else None


def platform_mismatch(info: dict) -> str:
    """这份 manifest 与当前进程的平台/架构对不对得上；对得上回空串。

    对不上通常意味着两件事之一，两件都得让用户看见而不是默默回退：
      * 装错了包（Intel 机器下了 arm64 的 dmg）；
      * 构建链把另一个平台的 runtime 打了进来（发出去之前必须炸在 CI 上）。
    """
    plat = info.get("platform") or {}
    want_os = str(plat.get("os") or "").strip().lower()
    want_arch = normalize_arch(plat.get("arch"))
    got_os, got_arch = host_os(), host_arch()
    if want_os and got_os and want_os != got_os:
        return f"内置渲染环境是给 {want_os} 的，当前系统是 {got_os}"
    # 认不出宿主架构时不拦（`platform.machine()` 在冷门平台上可能是空串）：
    # 把「我不知道」当成「不匹配」会让一个本来能用的 runtime 被判死刑。
    if want_arch and got_arch and want_arch != got_arch:
        return f"内置渲染环境是给 {want_arch} 的，当前进程是 {got_arch}"
    return ""


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
def status() -> dict:
    """内置 runtime 现状。

    `code` 非空 = 有问题且**用户需要知道**：
      bundled_runtime_missing  该带的没带（安装文件不完整 / 被杀毒软件删了）
      bundled_runtime_invalid  目录在但 manifest / 解释器 / 架构不对
                               （装了一半、被改坏、下错了架构的安装包）

    不该带 runtime 的形态（pip 安装、源码、Linux）里两个 code 都不给——
    那不是错误，只是这条路不适用。
    """
    root = runtime_root()
    if root is not None:
        info = read_manifest(root)
        if info is None:
            return {"present": True, "valid": False, "root": root,
                    "python": None, "manifest": None,
                    "code": CODE_INVALID,
                    "error": f"内置渲染环境的清单文件无法识别：{manifest_path(root)}"}
        bad = platform_mismatch(info)
        if bad:
            # manifest 照样交出去：诊断包与冒烟要能说清楚「拿到的是哪一份」，
            # 只回一句「损坏」的话，排障的人还得自己去翻文件。
            return {"present": True, "valid": False, "root": root,
                    "python": None, "manifest": info,
                    "code": CODE_INVALID,
                    "error": f"{bad}（{manifest_path(root)}）"}
        return {"present": True, "valid": True, "root": root,
                "python": resolve_python(root), "manifest": info,
                "code": "", "error": None}

    stray = _any_root()
    expected = ships_bundled_runtime()
    if stray is not None:
        return {"present": True, "valid": False, "root": stray,
                "python": None, "manifest": None,
                "code": CODE_INVALID if expected else "",
                "error": f"内置渲染环境不完整：{stray}"}
    return {"present": False, "valid": False, "root": None,
            "python": None, "manifest": None,
            "code": CODE_MISSING if expected else "",
            "error": ("安装包里的内置渲染环境不见了" if expected else None)}


def bundled_python() -> str | None:
    """可用的内置解释器绝对路径；没有或不完整回 None。"""
    st = status()
    return st["python"] if st["valid"] else None


def repair_hint() -> str:
    """内置 runtime 出问题时给用户的一句话——说清楚该做什么，不要甩路径。"""
    return ("Magplot 的安装文件不完整（内置渲染环境缺失或损坏）。"
            "请重新安装 Magplot；如果是杀毒软件误删，安装后把安装目录加入白名单。"
            "也可以在设置里改用你自己的 Python 环境。")


# ---------------------------------------------------------------------------
# 实测
# ---------------------------------------------------------------------------
def probe_packages(python: str, names: list[str] | None = None) -> dict[str, str | None]:
    """在指定解释器里 import 一遍并报版本；import 不到的回 None。

    「装完了但用不了」是最难查的一档（DLL 缺失、杀毒软件隔离了某个 .pyd、
    macOS 上某个 .so 没被签名于是被 Gatekeeper 拦下），只看 manifest 说装了
    什么不算数，得真去 import。
    """
    names = names or default_packages()
    if not names:
        return {}
    expr = (
        "import importlib, json, sys\n"
        "out = {}\n"
        f"for n in {names!r}:\n"
        "    try:\n"
        "        m = importlib.import_module(n)\n"
        "        out[n] = getattr(m, '__version__', '') or 'unknown'\n"
        "    except Exception:\n"
        "        out[n] = None\n"
        "sys.stdout.write(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run([python, "-c", expr], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=PROBE_TIMEOUT_S,
                              stdin=subprocess.DEVNULL,
                              creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {n: None for n in names}
    if proc.returncode != 0:
        return {n: None for n in names}
    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except ValueError:
        return {n: None for n in names}
    return {n: data.get(n) for n in names}


#: 分发包名 → import 名（只有对不上的才列在这儿）
_IMPORT_NAMES = {"pillow": "PIL", "python-dateutil": "dateutil",
                 "opencv-python": "cv2", "scikit-learn": "sklearn"}


def default_packages() -> list[str]:
    """manifest 里声明的**顶层**科学包的 import 名（传递依赖不逐个探测）。"""
    info = manifest()
    if not info:
        return []
    top = info.get("top_level") or list(info.get("packages", {}))
    return [_IMPORT_NAMES.get(n.lower(), n.replace("-", "_")) for n in top]


def child_args() -> list[str]:
    r"""用内置 runtime 起子进程时，加在解释器后面的命令行参数。

    **`-B` 是「不往安装目录写东西」这条纪律的真正保证**，不能换成
    `PYTHONDONTWRITEBYTECODE` / `PYTHONPYCACHEPREFIX`：

    * Windows 的 embeddable 发行版靠 `._pth` 定路径，而 CPython 的 getpath 在
      找到 `._pth` 时会 `use_environment = 0`（"Its presence also implies
      isolated mode"，见 CPython Modules/getpath.py 的 DETECT _pth FILE 一节），
      环境变量这条路不可靠。命令行参数任何时候都算数。
    * macOS 上后果更硬：`.app` 是**签过名**的，往里面写一个 `__pycache__`
      当场破坏代码签名，下次启动 Gatekeeper 直接拦下来——用户看到的是
      「应用已损坏」，而不是「多了几个缓存文件」。

    安装目录常在 `C:\Program Files\Magplot` 或 `/Applications/Magplot.app`：
    要么没权限，要么卸载后留一堆垃圾。
    代价（每次冷启动重新编译 .py）由构建期的预编译抵消——
    `scripts/build_worker_runtime.py` 会把 .pyc 先编好随包发出去，
    `-B` 只是禁止**写**，读现成的 .pyc 不受影响。
    """
    return ["-B"]


#: 会把内置 runtime 带跑偏的环境变量，起子进程前一律摘掉。
#:
#: Windows 上 `._pth` 的隔离模式顺手挡住了它们，**macOS 上没有任何东西挡**：
#: 用户从终端启动 Magplot 时，shell 里为 Conda / 自家项目设的 `PYTHONHOME`
#: 与 `PYTHONPATH` 会原样传给内置解释器——轻则 import 到别的 numpy，
#: 重则解释器根本起不来（PYTHONHOME 指向另一个前缀）。这类故障还只在
#: 「从终端启动」时复现，从 Finder 双击一切正常，最难查。
_HOSTILE_ENV = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE",
                "PYTHONEXECUTABLE", "PYTHONPLATLIBDIR")


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """用内置 runtime 起子进程时要额外注入的环境变量。

    `MPLCONFIGDIR` 是这里的重点：matplotlib 自己读 `os.environ`，不受 `._pth`
    的隔离影响，所以这条一定生效——否则字体缓存会落到 `~/.matplotlib`。

    另外两条在 Windows 上属于尽力而为（`._pth` 下可能被忽略，真正的保证是
    `child_args()` 里的 `-B`），在 macOS 上则实打实生效：
      PYTHONPYCACHEPREFIX  .pyc 落到数据目录
      PYTHONNOUSERSITE     不吃用户 site-packages——内置环境要可预期，
                           用户在别处 pip install 的东西不该悄悄改变它
    """
    from . import config
    env = dict(base if base is not None else os.environ)
    for key in _HOSTILE_ENV:
        env.pop(key, None)
    cache = os.path.join(str(config.data_dir()), "cache")
    env["PYTHONPYCACHEPREFIX"] = os.path.join(cache, "pycache")
    env["MPLCONFIGDIR"] = os.path.join(cache, "mpl")
    env["PYTHONNOUSERSITE"] = "1"
    for key in ("PYTHONPYCACHEPREFIX", "MPLCONFIGDIR"):
        try:
            os.makedirs(env[key], exist_ok=True)
        except OSError:
            pass
    return env
