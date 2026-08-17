"""Worker 池（Flask 父进程侧，纯标准库——.venv 里没有 matplotlib）。

每个 (项目, 脚本) 一个常驻子进程；LRU 淘汰，超出 MAX_ALIVE 的最久未用者关停。

**池键必须带项目路径**：多个标签页可以各自打开不同的图库，两个项目里
同名的 fig1.py 是两个完全不同的脚本，只按脚本名索引会把 A 项目的会话
交给 B 项目用（画面对不上，还会把 override 写到别人的 Figure 上）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config, runtime

LOG = logging.getLogger("mm.engine")

ENGINE_CACHE = config.data_dir() / "cache" / "engine"
WORKER_PY = Path(__file__).resolve().parent / "worker.py"
MAX_ALIVE = 3  # 同时存活的 worker 会话数（每个都端着整套 Figure 的内存）

# ---- 引擎缓存治理（对照 app.prune_render_cache / prune_backups） ------------
#: cache/engine/ 全部会话目录的总预算。比渲染缓存的 500MB 宽：这里躺着
#: 600dpi 的导出件与预览 SVG，一个会话就顶得上一堆缩略图。
ENGINE_CACHE_MAX_BYTES = 1024 * 1024 * 1024
#: 会话目录数上限。光有容量上限治不了「删掉的项目 / 改过名的脚本」留下的空壳：
#: 每个都小得可怜，加起来永远撞不到容量线，却会一直堆到几百个。
ENGINE_CACHE_KEEP = 40
#: 「最后使用时间」落盘的节流窗口（秒）——每次 override 都 utime 一遍纯属浪费。
_TOUCH_INTERVAL = 60.0
#: 两次清理之间的最小间隔（秒）：清理要遍历整棵缓存树，不能挂在每次渲染上。
_PRUNE_INTERVAL = 3600.0
_last_prune = 0.0

#: `shutdown_all(wait=True)` 等一个 worker 优雅关停的上限（秒）。
#: 提成常量是为了让测试能改短——真等 10 秒的用例没人愿意跑。
_SHUTDOWN_JOIN_TIMEOUT = 10.0

# ---- 单次请求的超时上限（秒） ----------------------------------------------
#: 无超时的 `readline()` 是会话级死锁的源头：脚本写了死循环（或某个 C 扩展
#: 卡住），发出请求的线程就持着 `w.lock` 永远等下去，这个会话从此谁也用不了，
#: 连 `shutdown()` 都抢不到锁。宁可杀掉重来——超时或状态未知的 worker 一律
#: 不复用（kill 之后下一次 `get()` 自动重建）。
#: 各档按「正常情况下最坏要多久」给：build 要跑用户整个脚本（heavy 分钟级），
#: 导出是 600dpi 全质量出图，override / 预览是热态操作。
BUILD_TIMEOUT = 900.0
REQUEST_TIMEOUT = 300.0     # override / render_png / preview_png
EXPORT_TIMEOUT = 600.0
#: 优雅关停：worker 收到就 SystemExit，等不到 5 秒说明它根本没在读 stdin。
SHUTDOWN_TIMEOUT = 5.0

#: 解释器来源（环境状态 API 与诊断包都用这套字符串，别在别处另起名字）
SOURCE_ENV = "env_override"       # MM_WORKER_PYTHON
SOURCE_CONFIGURED = "configured"  # 用户在设置里指定的
SOURCE_MANAGED = "managed_venv"   # Magplot 在源码模式下自建的 venv
SOURCE_BUNDLED = "bundled"        # Windows 桌面版随包附带的私有 runtime
SOURCE_CURRENT = "current_process"  # Flask 自己这个解释器（pip install magplot[worker]）
SOURCE_SYSTEM = "system"          # 探测到的系统 Python / Conda

#: 给人看的来源名（诊断包与日志用；前端有自己的一份文案）
SOURCE_LABELS = {
    SOURCE_ENV: "环境变量 MM_WORKER_PYTHON",
    SOURCE_CONFIGURED: "你在设置里指定的环境",
    SOURCE_MANAGED: "Magplot 自建的环境",
    SOURCE_BUNDLED: "Magplot 内置环境",
    SOURCE_CURRENT: "Magplot 自身的解释器",
    SOURCE_SYSTEM: "系统 Python / Conda",
}

_worker_python: str | None = None
_worker_source: str = ""
_workers: dict[tuple[str, str], "EngineWorker"] = {}
_lock = threading.Lock()


def _norm_dir(figures_dir: str | Path) -> str:
    """池键里的项目标识：解析成绝对路径，大小写不敏感的平台统一小写。"""
    try:
        p = str(Path(figures_dir).expanduser().resolve())
    except OSError:
        p = str(figures_dir)
    import os
    return p.lower() if os.name == "nt" else p


def _cache_slug(figures_dir: str, script_name: str) -> str:
    """(项目, 脚本) → 缓存子目录名。

    以前是 `Path(script_name).stem`：不同项目 / 不同子目录下的同名脚本会共用
    同一个 out/sandbox 目录，互相覆盖 SVG 与 manifest。
    """
    digest = hashlib.sha1(figures_dir.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w.-]+", "_", script_name.replace("\\", "/").rstrip("/"))
    return f"{digest}-{safe[:60]}"


class WorkerError(RuntimeError):
    def __init__(self, message: str, traceback_text: str = "", code: str = "",
                 module: str = ""):
        super().__init__(message)
        self.traceback_text = traceback_text
        # 机器可读的原因；前端据此换成对应的引导界面而不是干甩一段错误文字
        self.code = code
        # code == "missing_dependency" 时是缺的那个模块名
        self.module = module


_MISSING_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")


def missing_module(text: str) -> str:
    """从 traceback 里认出「缺哪个包」，认不出回空串。

    内置 runtime 只带常用科学栈；用户脚本 import 了 rdkit/astropy 这类包时，
    甩一段 ModuleNotFoundError 的 traceback 等于什么都没说。认出包名才能给出
    「内置环境里没有 rdkit，去高级设置换成你自己的环境」这种可执行的提示。

    **本阶段刻意不自动 pip install**：往内置 runtime 里随便装东西会让它不再
    可复现，也让「重装就能修」这条退路失效。
    """
    m = _MISSING_RE.search(text or "")
    return m.group(1).split(".")[0] if m else ""


def is_frozen() -> bool:
    """是否跑在 PyInstaller 打出的独立应用里（.app / .exe）。"""
    return runtime.is_frozen()


def _configured_source(path: str) -> str:
    """用户配置里的那条解释器是「他自己挑的」还是「Magplot 自己建的」。

    两者都排在内置 runtime 前面（用户的显式选择优先），但报给界面和诊断包时
    要分得清：managed_venv 是我们该负责的，configured 是用户自己的环境。
    """
    from . import bootstrap
    try:
        managed = str(bootstrap.venv_python())
    except (OSError, ValueError):
        return SOURCE_CONFIGURED
    return SOURCE_MANAGED if same_python(path, managed) else SOURCE_CONFIGURED


def _prioritized_candidates() -> list[tuple[str, str]]:
    """(解释器路径, 来源) 按优先级排列——**解释器选择顺序的唯一出处**。

    1. `MM_WORKER_PYTHON`      —— 环境变量，最高优先级的应急/高级覆盖
    2. 用户在设置里指定的      —— 他明确挑过的环境，任何时候都压过我们的默认
    3. Magplot 内置 runtime    —— Windows 桌面版随包附带（装完即用，不联网）
    4. 自身                    —— `pip install magplot[worker]` 单环境安装
    5. 系统 Python / Conda     —— 兼容回退，桌面版的老行为

    **打包成独立应用时必须跳过 sys.executable**：那时它是 Magplot 自己那个
    可执行文件，不是 Python 解释器；拿它去跑 `-c "import matplotlib"` 会以
    莫名其妙的参数把应用再启动一次。

    第 5 条留着不是摆设：用户的论文脚本可能要 import 内置 runtime 里没有的包
    （rdkit、astropy、自家实验室的库），那时他自己那套环境才是对的。
    """
    import os
    import sys

    cands: list[tuple[str | None, str]] = [
        (os.environ.get("MM_WORKER_PYTHON"), SOURCE_ENV),
    ]
    configured = config.worker_python()
    if configured:
        cands.append((configured, _configured_source(configured)))
    cands.append((runtime.bundled_python(), SOURCE_BUNDLED))
    if not is_frozen():
        cands.append((sys.executable, SOURCE_CURRENT))

    # 一律用字符串拼路径：pathlib.Path 会按 os.name 分派 Posix/Windows 实现，
    # 在非目标平台上构造另一半会直接抛 UnsupportedOperation。
    home = os.path.expanduser("~")
    system: list[str | None] = []
    if os.name == "nt":
        system += [shutil.which("python"), shutil.which("python3")]
        # python.org 与 conda 的常见落点。内置 runtime 缺失/损坏时全靠这里
        # 把用户已有的环境翻出来，值得多找几个地方。
        local = os.environ.get("LOCALAPPDATA")
        roots = ([local + r"\Programs\Python"] if local else []) + ["C:\\"]
        for root in roots:
            system += _glob(root + r"\Python*\python.exe")
        system += [f"{home}\\{n}\\python.exe" for n in ("anaconda3", "miniconda3")]
    else:
        system += [
            "/opt/homebrew/opt/python@3.13/libexec/bin/python3",  # macOS Homebrew
            "/opt/homebrew/bin/python3",
            shutil.which("python3"),
            "/usr/bin/python3",
        ]
        # python.org 的 framework 安装（新版优先）与 conda
        system += _glob("/Library/Frameworks/Python.framework/Versions/*/bin/python3")
        system += [f"{home}/{n}/bin/python3"
                   for n in ("anaconda3", "miniconda3", "mambaforge")]
    cands += [(p, SOURCE_SYSTEM) for p in system]
    return [(p, src) for p, src in cands if p]


def _candidate_pythons() -> list[str | None]:
    """按优先级列出可能装了 matplotlib 的解释器（跨平台）。

    只是 `_prioritized_candidates()` 丢掉来源标签的视图，保留给不关心来源的
    调用方（bootstrap 的基础解释器探测、updater 的安装方式判断）。
    """
    return [p for p, _ in _prioritized_candidates()]


def _glob(pattern: str) -> list[str]:
    """新版优先的安全 glob：目录不存在/没权限时回空表，不把启动流程带崩。"""
    import glob as _g
    try:
        return sorted(_g.glob(pattern), reverse=True)
    except OSError:
        return []


def _has_matplotlib(python: str) -> bool:
    """真去 import 一次。manifest 说装了不算数——DLL 缺失、被杀毒软件隔离了
    某个 .pyd，都是「文件在但 import 不了」。"""
    try:
        # stdin 必须显式断开：桌面 sidecar 的 stdin 是「父进程死亡信号」管道，
        # 绝不能被子进程继承（Windows 上实测继承它会让子解释器启动挂死 30s，
        # 症状是桌面版「渲染环境不可用」而同一解释器在终端里探测秒过）
        probe = subprocess.run([python, "-c", "import matplotlib"],
                               capture_output=True, timeout=30,
                               stdin=subprocess.DEVNULL,
                               creationflags=runtime.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def _no_python_error() -> "WorkerError":
    """一个可用解释器都没有时，报哪种错。

    Windows 桌面版**本该**自带 runtime，所以那里的失败不是「你没装 Python」，
    而是「安装文件不完整」——两者要给用户的动作完全不同（重装 vs 去装 Python），
    code 也必须分开，否则前端只能给一句谁也用不上的通用提示。
    """
    st = runtime.status()
    if st["code"]:
        return WorkerError(runtime.repair_hint(), code=st["code"])
    # 前端认这个 code，据此弹「自动安装渲染环境」而不是把这段话直接甩给用户
    return WorkerError(
        "找不到装有 matplotlib 的 Python。可在设置里让 Magplot 自动装一个，"
        "或指定你已有的解释器（环境变量 MM_WORKER_PYTHON 同样有效）。",
        code="no_worker_python")


def select_worker_python() -> tuple[str, str]:
    """挑一个装了 matplotlib 的解释器，回 (路径, 来源)。

    来源是给人看的（环境状态 API / 诊断包 / 冒烟断言）：同样一条路径，
    「内置」和「你自己的 conda」在排障时的含义天差地别。
    """
    global _worker_python, _worker_source
    if _worker_python:
        return _worker_python, _worker_source
    seen: set[str] = set()
    for cand, source in _prioritized_candidates():
        if cand in seen:
            continue
        seen.add(cand)   # 同一个解释器不重复探测（每次探测最多 30s）
        try:
            if not Path(cand).exists():
                continue
        except OSError:
            continue
        if _has_matplotlib(cand):
            _worker_python, _worker_source = cand, source
            LOG.info("渲染解释器: %s（来源 %s）", cand, source)
            return cand, source
    raise _no_python_error()


def find_worker_python() -> str:
    """找一个装了 matplotlib 的解释器（Flask 自己的 .venv 可能没有）。"""
    return select_worker_python()[0]


def same_python(a: str | None, b: str | None) -> bool:
    """两条路径是不是同一个解释器。

    Windows 上大小写不敏感且 `/` 与 `\\` 等价：
    `C:/Users/张三/python.exe` 与 `C:\\Users\\张三\\Python.exe` 是同一个文件，
    按字符串比会当成两个，来源标签立刻错位。
    """
    if not a or not b:
        return False
    import os

    def norm(p: str) -> str:
        try:
            s = os.path.normpath(os.path.abspath(os.path.expanduser(p)))
        except (OSError, ValueError):
            s = p
        return os.path.normcase(s)

    return norm(a) == norm(b)


def source_of(python: str) -> str:
    """这条解释器路径**属于**哪个来源——不做任何 import 探测。

    先认本次进程已经选中的那个（`select_worker_python()` 缓存了来源），
    否则按位置归类。之所以要能脱离缓存单独判断：环境状态 API 允许上层先拿到
    路径再问来源，而探测一次最多 30s，不能为了贴个标签再跑一遍。
    """
    import os
    import sys
    if _worker_python and same_python(python, _worker_python) and _worker_source:
        return _worker_source
    if same_python(python, os.environ.get("MM_WORKER_PYTHON")):
        return SOURCE_ENV
    configured = config.worker_python()
    if same_python(python, configured):
        return _configured_source(python)
    if same_python(python, runtime.bundled_python()):
        return SOURCE_BUNDLED
    if not is_frozen() and same_python(python, sys.executable):
        return SOURCE_CURRENT
    return SOURCE_SYSTEM


def worker_source() -> str:
    """当前解释器的来源；还没选过就现选一次（选不出来回空串）。"""
    try:
        return select_worker_python()[1]
    except WorkerError:
        return ""


def reset_worker_python() -> None:
    """丢弃已缓存的解释器选择——改了设置或刚装完环境后必须调用，
    否则本次进程会一直用着旧的（或继续认为「找不到」）。"""
    global _worker_python, _worker_source
    with _lock:
        _worker_python = None
        _worker_source = ""


class EngineWorker:
    def __init__(self, script_name: str, figures_dir: str, entry: str):
        self.script_name = script_name
        self.figures_dir = figures_dir
        self.entry = entry
        base = ENGINE_CACHE / _cache_slug(_norm_dir(figures_dir), script_name)
        self.base = base
        self.out_dir = base / "out"
        self.sandbox = base / "sandbox"
        self.export_dir = base / "export"
        self.log_path = base / "worker.log"
        base.mkdir(parents=True, exist_ok=True)
        self._touched = 0.0
        self._touch()                      # mkdir 对已存在的目录不动 mtime，见 _touch
        self.rev = 0                       # 每次 override 递增，用于前端缓存穿透
        self.lock = threading.Lock()
        self.built = False
        self.last_used = time.time()
        self._log = open(self.log_path, "ab", buffering=0)
        python, self.python_source = select_worker_python()
        self.python = python
        # 内置 runtime 装在安装目录里（可能是 Program Files），一个字节都不往
        # 那儿写：.pyc 与 matplotlib 字体缓存改道到数据目录。用户自己的环境
        # 不动——那是他的地盘，我们没资格替他改 MPLCONFIGDIR。
        bundled = self.python_source == SOURCE_BUNDLED
        env = runtime.child_env() if bundled else None
        # `-B`：内置 runtime 装在安装目录里（可能是 Program Files），
        # 一个 .pyc 都不往那儿写。.pyc 已在构建期编好随包发出，`-B` 只禁写不禁读。
        args = runtime.child_args() if bundled else []
        LOG.info("worker 启动: %s（entry=%s，解释器来源=%s）",
                 script_name, entry, self.python_source)
        self.proc = subprocess.Popen(
            [python, *args, str(WORKER_PY),
             "--script", str(Path(figures_dir) / script_name),
             "--figures-dir", figures_dir,
             "--out-dir", str(self.out_dir),
             "--sandbox", str(self.sandbox),
             "--entry", entry],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            env=env,
            text=True, bufsize=1,
            # 显式 UTF-8：text=True 默认跟随系统区域编码，Windows 上是 cp1252/
            # cp936，读 worker 回来的中文/µ/⁻¹ 会解码失败。worker 侧同样钉死。
            encoding="utf-8", errors="replace",
            creationflags=runtime.CREATE_NO_WINDOW,
        )

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _touch(self) -> None:
        """把「最后使用时间」落到缓存根目录的 mtime 上（节流 _TOUCH_INTERVAL）。

        `last_used` 只活在内存里，进程一退就没了；而 `mkdir(exist_ok=True)` 对
        已存在的目录是空操作，**不更新 mtime**——不落盘的话目录 mtime 永远停在
        第一次创建那一刻，`prune_engine_cache()` 会把用了几个月的高频项目判成
        「最久未用」优先删掉，比不清理更糟。
        写不进去（只读介质 / 权限）就算了：清理是治理手段，不值得让渲染失败。
        """
        import os
        now = time.time()
        if now - self._touched < _TOUCH_INTERVAL:
            return
        self._touched = now
        try:
            os.utime(self.base, None)
        except OSError:
            pass

    def _log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def _readline(self, timeout: float) -> str:
        """带超时读一行回应；超时即杀掉 worker 并抛 `worker_timeout`。

        超时用「读线程 + join」而不是 `select`：Windows 的 select 只接受 socket，
        对管道直接 WinError 10038，而这条路径必须跨平台一致。

        kill 之后读线程会立刻读到 EOF 退出，不会泄漏；被杀的 worker 留在池里
        也无妨——下一次 `get()` 看到它已死就地重建（状态未知的会话绝不复用）。
        """
        box: list[str] = []

        def read() -> None:
            try:
                box.append(self.proc.stdout.readline())
            except (OSError, ValueError):      # 进程被杀后管道关闭
                box.append("")

        t = threading.Thread(target=read, daemon=True, name="mm-worker-read")
        t.start()
        t.join(timeout)
        if t.is_alive():
            LOG.warning("worker 请求超时（%.0fs），强制 kill: %s",
                        timeout, self.script_name)
            try:
                self.proc.kill()
                self.proc.wait(timeout=_SHUTDOWN_JOIN_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                pass
            raise WorkerError(
                f"渲染超时（等了 {int(timeout)} 秒）。脚本可能陷入死循环，"
                f"或这一步本身极慢；渲染会话已重启，可以重试。"
                f"若每次都卡在同一步，请检查 {self.script_name} 里的耗时代码。",
                self._log_tail(), code="worker_timeout")
        return box[0] if box else ""

    def request(self, obj: dict, timeout: float | None = None) -> dict:
        # None → 取模块常量的**当前**值（默认参数会在 def 时定死，测试改不动）
        timeout = REQUEST_TIMEOUT if timeout is None else timeout
        self.last_used = time.time()
        # 所有命令（build/override/export/render_png/preview_png）都经这里，
        # 是「这个会话真的被用了」覆盖面最全的一个点。
        self._touch()
        with self.lock:
            if not self.alive():
                raise WorkerError("worker 进程已退出", self._log_tail())
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self._readline(timeout)
        if not line:
            raise WorkerError("worker 进程崩溃（无响应）", self._log_tail())
        resp = json.loads(line)
        if not resp.get("ok"):
            tb = resp.get("traceback", "")
            msg = resp.get("error", "worker 错误")
            mod = missing_module(f"{msg}\n{tb}")
            if mod:
                raise WorkerError(
                    f"脚本用到的 {mod} 在当前渲染环境里没有。"
                    f"可以在设置 →「渲染环境」里改用你自己那套装了 {mod} 的 "
                    f"Python / Conda 环境。",
                    tb, code="missing_dependency", module=mod)
            raise WorkerError(msg, tb)
        return resp

    def ensure_built(self) -> dict:
        # build 要跑用户整个脚本（heavy 的分钟级），给最宽的一档
        resp = self.request({"cmd": "build"}, BUILD_TIMEOUT)
        self.built = True
        return resp

    def override(self, stem: str, patches: list) -> dict:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "override", "stem": stem, "patches": patches},
                            REQUEST_TIMEOUT)
        self.rev += 1
        return resp

    def export(self, stem: str, patches: list, path: str,
               fmt: str = "pdf", dpi: int = 600) -> dict:
        if not self.built:
            self.ensure_built()
        return self.request({"cmd": "export", "stem": stem, "patches": patches,
                             "path": path, "format": fmt, "dpi": dpi},
                            EXPORT_TIMEOUT)

    def svg_path(self, stem: str) -> Path:
        return self.out_dir / f"{stem}.svg"

    def render_png(self, stem: str, width_px: int) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "render_png", "stem": stem, "width": width_px},
                            REQUEST_TIMEOUT)
        return Path(resp["path"])

    def preview_png(self, stem: str, patches: list, width_px: int, tag: str) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self.request({"cmd": "preview_png", "stem": stem, "patches": patches,
                             "width": width_px, "tag": tag},
                            REQUEST_TIMEOUT)
        return Path(resp["path"])

    def shutdown(self) -> None:
        try:
            if self.alive():
                self.request({"cmd": "shutdown"}, SHUTDOWN_TIMEOUT)
        except (WorkerError, OSError):
            pass
        finally:
            if self.alive():
                self.proc.kill()
            self._log.close()


def _dir_size(path: Path) -> int:
    """目录占用字节数；读不动的条目跳过（宁可少算也不能把清理带崩）。"""
    import os
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def prune_engine_cache(max_bytes: int = ENGINE_CACHE_MAX_BYTES,
                       keep: int = ENGINE_CACHE_KEEP) -> int:
    """会话缓存目录按最后使用时间从旧到新删至预算内，返回删除数。

    口径与 `app.prune_render_cache()`（容量）+ `app.prune_backups()`（份数）
    一致，两条线谁先触发算谁的。排序依据是 `_touch()` 落盘的 mtime，不是创建
    时间——两者在这里差着几个月。

    **池里挂着的 worker 用的目录一律豁免**：删掉它正在写的 out/sandbox，下一次
    override 会以「文件不存在」的形式炸在用户脸上。豁免目录同样占着目录名额
    （它们本来就是最近用过的），但不计入容量账——按它们算容量只会让可删的那些
    被多删几个，白删。
    """
    with _lock:
        # 池里挂着的一律豁免，不筛 alive()：崩掉的那个键还留在池里，下一次
        # 请求会**原地重建**（`get()`），期间把目录删了正好撞上重建的 mkdir。
        busy = {str(w.base) for w in _workers.values()}
    try:
        entries = [p for p in ENGINE_CACHE.iterdir() if p.is_dir()]
    except OSError:      # 缓存目录还没建起来
        return 0
    items = []
    for p in entries:
        if str(p) in busy:
            continue
        try:
            items.append((p.stat().st_mtime, p, _dir_size(p)))
        except OSError:
            continue
    items.sort(key=lambda it: it[0])   # 最久未用的排前面
    total = sum(size for _, _, size in items)
    count = len(entries)
    removed = 0
    for _mtime, path, size in items:
        if total <= max_bytes and count <= keep:
            break
        try:
            shutil.rmtree(path)
        except OSError:  # Windows 上被占用/无权限：跳过这一个，别中断整体
            LOG.warning("引擎缓存目录删除失败，跳过: %s", path)
            continue
        total -= size
        count -= 1
        removed += 1
    if removed:
        LOG.info("引擎缓存清理: 删除 %d 个会话目录（预算 %dMB / %d 个）",
                 removed, max_bytes // (1024 * 1024), keep)
    return removed


def _schedule_prune() -> None:
    """后台清一次引擎缓存（最多每 _PRUNE_INTERVAL 一次）。

    挂在「新建了一个会话目录」上——与 `prune_render_cache()` 挂在「刚写了一个
    新缓存文件」上同一个道理：只有增量出现时才值得回收。放后台线程是因为
    要遍历整棵缓存树，不能让用户的第一次渲染多等这一趟磁盘。
    """
    global _last_prune
    now = time.time()
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now
    threading.Thread(target=prune_engine_cache, daemon=True,
                     name="mm-engine-cache-prune").start()


def get(script_name: str, figures_dir: str, entry: str) -> EngineWorker:
    """取（或重建）某脚本的 worker；崩溃的自动换新；超出 MAX_ALIVE 按 LRU 淘汰。"""
    key = (_norm_dir(figures_dir), script_name)
    created = False
    with _lock:
        w = _workers.get(key)
        if w is not None and (not w.alive() or w.entry != entry):
            LOG.warning("worker %s，重建: %s",
                        "已死" if not w.alive() else "入口已变", script_name)
            w.shutdown()
            w = None
        if w is None:
            w = EngineWorker(script_name, figures_dir, entry)
            _workers[key] = w
            created = True
        w.last_used = time.time()
        alive = [(k, x) for k, x in _workers.items() if x.alive()]
        if len(alive) > MAX_ALIVE:
            stale = sorted(alive, key=lambda kv: kv[1].last_used)
            for vkey, victim in stale[: len(alive) - MAX_ALIVE]:
                if victim is not w:
                    LOG.info("worker LRU 淘汰: %s", victim.script_name)
                    _workers.pop(vkey, None)
                    threading.Thread(target=victim.shutdown, daemon=True).start()
    if created:      # 出锁再清：prune 要遍历磁盘，不能占着 _lock
        _schedule_prune()
    return w


def invalidate(script_name: str, figures_dir: str | None = None) -> None:
    """脚本文件变更后作废其会话（下次请求自动重建）。

    不给 figures_dir 就作废所有项目里的同名脚本——watcher 回调走这条路，
    宁可多关一个也不能让某个项目留着过期会话。
    """
    with _lock:
        if figures_dir is None:
            keys = [k for k in _workers if k[1] == script_name]
        else:
            keys = [k for k in ((_norm_dir(figures_dir), script_name),)
                    if k in _workers]
        victims = [_workers.pop(k) for k in keys]
    for w in victims:
        threading.Thread(target=w.shutdown, daemon=True).start()


def shutdown_all(figures_dir: str | None = None, wait: bool = False) -> None:
    """关闭 worker（进程退出前；给 figures_dir 则只收某个项目的）。
    异步优雅关停 + 兜底 kill。

    `wait=True` 用于**本进程即将退出**的场合：关停跑在 daemon 线程里，
    父进程一走它们就没了，worker 子进程会变成用户机器上的僵尸 python.exe。

    脚本写了死循环时优雅关停根本走不通：`request()` 持着 `w.lock` 等回应
    （现在有超时了，但 build 那一档就是 15 分钟），`shutdown()` 抢同一把锁
    要一直等到它超时，永远走不到 finally 里的 `proc.kill()`。join 超时只是
    不再等，子进程照样活着——所以 `wait=True` 这条「进程即将退出」的路径
    必须硬杀一次兜底。
    （LRU 淘汰路径不动：那里 worker 还可能是正常在跑的慢脚本。）
    """
    with _lock:
        if figures_dir is None:
            victims = list(_workers.values())
            _workers.clear()
        else:
            target = _norm_dir(figures_dir)
            keys = [k for k in _workers if k[0] == target]
            victims = [_workers.pop(k) for k in keys]
    threads = [threading.Thread(target=w.shutdown, daemon=True) for w in victims]
    for t in threads:
        t.start()
    if wait:
        for t, w in zip(threads, victims):
            t.join(timeout=_SHUTDOWN_JOIN_TIMEOUT)
            if t.is_alive():
                LOG.warning("worker 关停超时（可能卡在死循环脚本里），强制 kill: %s",
                            w.script_name)
                try:
                    w.proc.kill()
                except OSError:
                    pass


# 每个项目一个 watcher：多标签页各开各的项目时，两个图库都要被盯着。
_watchers: dict[str, threading.Event] = {}


def stop_watcher(figures_dir: str | None = None) -> None:
    """停掉 watcher 线程；不给目录则全停（进程退出）。"""
    with _lock:
        if figures_dir is None:
            events = list(_watchers.values())
            _watchers.clear()
        else:
            ev = _watchers.pop(_norm_dir(figures_dir), None)
            events = [ev] if ev is not None else []
    for ev in events:
        ev.set()


def watched_dirs() -> list[str]:
    with _lock:
        return list(_watchers)


def start_watcher(figures_dir: str, scripts: list[str], on_change, interval: float = 2.0) -> None:
    """轮询脚本 mtime；变更即作废会话并回调（paper_style 变更作废全部）。
    同一目录重复调用自动替换旧 watcher（注册表变了要重新盯新脚本）。"""
    key = _norm_dir(figures_dir)
    stop_watcher(figures_dir)
    stop = threading.Event()
    with _lock:
        _watchers[key] = stop
    fig_dir = Path(figures_dir)
    tracked: dict[str, float | None] = {}
    for s in [*scripts, "paper_style.py"]:
        try:
            tracked[s] = (fig_dir / s).stat().st_mtime
        except OSError:
            tracked[s] = None

    def loop():
        while not stop.wait(interval):
            changed = []
            for s in tracked:
                try:
                    mt = (fig_dir / s).stat().st_mtime
                except OSError:
                    continue
                if tracked[s] is not None and mt != tracked[s]:
                    changed.append(s)
                tracked[s] = mt
            if not changed:
                continue
            LOG.info("脚本变更: %s，作废会话", changed)
            if "paper_style.py" in changed:      # 共享样式变了，本项目全作废
                with _lock:
                    victims = [k[1] for k in _workers if k[0] == key]
            else:
                victims = changed
            for name in victims:
                invalidate(name, figures_dir)     # 只动本项目的会话
            try:
                on_change(changed)
            except Exception:  # noqa: BLE001 — watcher 不允许因回调挂掉
                pass

    threading.Thread(target=loop, daemon=True,
                     name=f"mm-script-watcher-{key[-24:]}").start()
