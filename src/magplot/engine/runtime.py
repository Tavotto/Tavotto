"""Magplot 内置渲染 runtime 的定位与校验（纯标准库，Flask 父进程 import）。

Windows 桌面版随安装包附带一套 **Magplot 私有的** CPython + 科学栈：

    Magplot.exe → runtime/python.exe → engine/worker.py → 用户的图表脚本

这样普通用户装完就能渲染——不需要先去装 Python、不需要联网、不依赖 PATH、
Windows Store Python 或 Conda，也**绝不动用户已有的任何环境**。

为什么单独一个模块：路径判断散在 pool / bootstrap / diagnostics 三处的话，
frozen 与源码模式的差异就会各写一遍，迟早对不上。这里是唯一出处，别处
一律调 `runtime_root()` / `status()`。

runtime 从哪来：`scripts/build_worker_runtime.py` 按 `packaging/runtime-lock.json`
构建（官方 embeddable 发行版 + 锁定版本的 wheel），产物不进 Git。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

RUNTIME_DIR_NAME = "runtime"
MANIFEST_NAME = "runtime-manifest.json"
#: 认得的 manifest schema。构建脚本写的版本比这里新 = 装了个更新的包但主程序是旧的，
#: 当作损坏处理而不是硬着头皮往下跑。
MANIFEST_SCHEMA = 1

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

    只有 Windows 桌面安装包会附带（见 packaging/magplot.spec）。macOS 的 .app
    与 pip/源码安装都不带，那里 runtime 缺失是正常状态，不该报「安装文件不完整」。
    """
    return is_frozen() and os.name == "nt"


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

    冻结后 onedir 的布局是 `Magplot.exe` + `_internal/`，`sys._MEIPASS` 指向
    `_internal`——runtime 通过 spec 的 datas 进包，落点就是 `_internal/runtime`。
    exe 同级与 `exe/_internal` 两条也留着：手工摆放产物或换 PyInstaller 版本
    导致布局变化时不至于直接失灵。
    """
    roots: list[str] = []

    def add(p: str | None) -> None:
        if p and p not in roots:
            roots.append(p)

    # 显式覆盖：CI 与单元测试用它把 runtime 指到临时目录，不必真去打包
    add(os.environ.get("MAGPLOT_RUNTIME_DIR"))

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
    """runtime 里的解释器路径（不保证存在）。"""
    if os.name == "nt":
        return root.rstrip("\\/") + "\\python.exe"
    # 非 Windows 目前不发内置 runtime；留着这一支是为了在 mac/Linux 上
    # 也能跑通定位与校验的单元测试（CI 的后端矩阵是三平台）。
    return root.rstrip("/") + "/bin/python3"


def manifest_path(root: str) -> str:
    return os.path.join(root, MANIFEST_NAME)


def runtime_root() -> str | None:
    """找到**看起来完整**的 runtime 目录；一个都没有回 None。

    判据是「解释器在 + manifest 在」，不做深校验——深校验在 `status()` 里，
    这样「目录在但内容坏了」能报 invalid 而不是被当成 missing。
    """
    for root in _candidate_roots():
        try:
            if (os.path.isfile(runtime_python(root))
                    and os.path.isfile(manifest_path(root))):
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
    """读 runtime-manifest.json；缺失/坏了/schema 不认识一律回 None。"""
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
    return data


def manifest() -> dict | None:
    root = runtime_root()
    return read_manifest(root) if root is not None else None


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
def status() -> dict:
    """内置 runtime 现状。

    `code` 非空 = 有问题且**用户需要知道**：
      bundled_runtime_missing  该带的没带（安装文件不完整 / 被杀毒软件删了）
      bundled_runtime_invalid  目录在但 manifest 或解释器不对（装了一半 / 被改坏）

    不该带 runtime 的形态（macOS 桌面版、pip 安装、源码）里两个 code 都不给——
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
        return {"present": True, "valid": True, "root": root,
                "python": runtime_python(root), "manifest": info,
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

    「装完了但用不了」是最难查的一档（DLL 缺失、杀毒软件隔离了某个 .pyd），
    只看 manifest 说装了什么不算数，得真去 import。
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
    `PYTHONDONTWRITEBYTECODE` / `PYTHONPYCACHEPREFIX`：embeddable 发行版靠
    `._pth` 定路径，而 CPython 的 getpath 在找到 `._pth` 时会
    `use_environment = 0`（"Its presence also implies isolated mode"，
    见 CPython Modules/getpath.py 的 DETECT _pth FILE 一节），环境变量这条路
    不可靠。命令行参数任何时候都算数。

    安装目录常在 `C:\Program Files\Magplot`：往 site-packages 旁边写
    `__pycache__` 要么没权限、要么卸载后留一堆垃圾。
    代价（每次冷启动重新编译 .py）由构建期的预编译抵消——
    `scripts/build_worker_runtime.py` 会把 .pyc 先编好随包发出去，
    `-B` 只是禁止**写**，读现成的 .pyc 不受影响。
    """
    return ["-B"]


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """用内置 runtime 起子进程时要额外注入的环境变量。

    `MPLCONFIGDIR` 是这里的重点：matplotlib 自己读 `os.environ`，不受 `._pth`
    的隔离影响，所以这条一定生效——否则字体缓存会落到 `~/.matplotlib`。

    另外两条属于尽力而为（`._pth` 下可能被忽略，真正的保证是 `child_args()`
    里的 `-B`），留着是因为它们表达了意图，而且换成别的运行方式时仍然有用：
      PYTHONPYCACHEPREFIX  .pyc 落到数据目录
      PYTHONNOUSERSITE     不吃用户 site-packages——内置环境要可预期，
                           用户在别处 pip install 的东西不该悄悄改变它
    """
    from . import config
    env = dict(base if base is not None else os.environ)
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
