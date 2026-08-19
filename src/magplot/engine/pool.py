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
import uuid
from pathlib import Path

from . import config, patchspec, runtime

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

# ---- worker 协议 v1（契约见 docs/adr/0003-worker-protocol-v1.md） -----------
#: 请求信封的协议版本。worker 双栈兼容（无此字段 = legacy），父进程只发 v1。
PROTOCOL_VERSION = 1

#: 池方法名 → v1 线上命令名。`override` 这个叫法留在 Python API 上（app.py
#: 一路这么叫），线上统一叫 `render`——v1 的命令表是给 Rust supervisor 看的，
#: 那里没有历史包袱，不该背我们的旧名字。
_V1_CMD = {"override": "render"}

#: (项目, 脚本) → 已经起过第几代 worker。supervisor 靠 generation 分辨
#: 「这条响应属于哪一代」：会话被超时 kill 后重建，晚到的旧响应必须能被认出来
#: 丢弃，否则新会话会被上一代的 manifest 污染。
_generations: dict[tuple[str, str], int] = {}
#: 单独一把锁：`EngineWorker.__init__` 是在 `get()` 持着 `_lock` 时调用的，
#: 在里面再抢 `_lock` 会直接自锁死（threading.Lock 不可重入）。
_gen_lock = threading.Lock()

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
#: 一次性 worker（`one_shot()`）正在用的缓存目录。它们**不在池里**，
#: `prune_engine_cache()` 却按 ENGINE_CACHE 的顶层目录清理——不登记的话，
#: 一次写回的干净重放正跑到一半，目录可能被后台清理线程整个删掉。
_oneshot_bases: set[str] = set()
_lock = threading.Lock()

#: 空 patch 列表的规范哈希（`last_patch_hash` 的初值：刚 build 完的 figure
#: 就是「一条 override 都没应用」的状态）。
_EMPTY_PATCH_HASH = patchspec.patch_hash([])


def stem_patch_hash(worker, stem: str) -> str:
    """`worker` 上**这个 stem** 最后应用的那组 patches 的规范哈希。

    必须按 stem 问，不能只看 worker 级的 `last_patch_hash`：池键是
    `(figures_dir, script_name)`、**不含 stem**，一个脚本登记多个 stem 时
    它们共用同一条会话（`examples/figures` 里的 `fig2.py` 就登记了
    `Fig2_yield` 与 `Fig2_correlation` 两个）。只看 worker 级的话，先改完
    A 再对 B 点「更新原图」，只要两次 patches 的哈希碰巧相同（最常见的就是
    两边都是空列表），写回自检就会拿 **A 的热态 manifest** 去和 B 的重放
    结果比——而那道校验正是「热态所见 == 写进文件的」这条不变式的最后一道
    防线，比错了要么误报 409、要么把真实分歧放过去。

    **账本里有记录就以它为准，不再看 `built`。** `built` 是包装对象的记账，
    而 workerd 那条路的透明重开（`_call` 撞上 `unknown_session` → `_open()`
    → 重试）会把它置回 False——即便重试的那次 render 成功了、这个 stem 的
    哈希也已经记下。拿 `built` 当前置条件的话，那次成功的热态会被当成「没有
    基准」，写回于是悄悄降级成 `fresh_only`，跳过热态与重放的分歧比对——
    而那正是这道校验存在的全部意义。

    账本里没有记录时才轮到 `built` 说话：build 过 = 这个 stem 就是脚本原样
    （空 patch 列表）；没 build 过 = 压根没有基准（回空串）。
    """
    by_stem = getattr(worker, "last_patch_hash_by_stem", None)
    if by_stem is None:                       # 没有按 stem 账本的实现（假件）
        return getattr(worker, "last_patch_hash", "")
    if stem in by_stem:
        return by_stem[stem]
    return _EMPTY_PATCH_HASH if getattr(worker, "built", False) else ""


def _merge_timings(resp: dict, queue_wait_ms: float, total_ms: float) -> dict:
    """把控制面自己的两段计时并进 worker 回来的 `timings`。

    分工：worker 的那几个数说的是「进了 worker 之后各阶段花了多少」
    （`script_build_ms` / `patch_apply_ms` / `canvas_draw_ms` / `manifest_ms`），
    这里补的两个是**父进程视角**——

    * `queue_wait_ms`：请求发出去之前排了多久。Python 池里就是抢 `w.lock` 的
      时间（同一会话上一次渲染没跑完，后来的全堵在这儿），workerd 那边由它
      自己的合并队列体现（口径差异见 ADR 0004）。
    * `total_ms`：父进程看到的整次往返。

    `total_ms − queue_wait_ms −（worker 各阶段之和）` 就是协议与管道的开销——
    没有这两个数，一次「慢」到底慢在排队、渲染还是序列化上永远说不清。
    worker 已经给出的键**一律不覆盖**：那是它那一侧的事实。
    """
    got = resp.get("timings")
    timings = dict(got) if isinstance(got, dict) else {}
    timings.setdefault("queue_wait_ms", round(queue_wait_ms, 3))
    timings["total_ms"] = round(total_ms, 3)
    resp["timings"] = timings
    return resp


def _fold_build_timings(resp: dict, build: dict | None) -> dict:
    """把「顺带触发的那次 build」的计时并进本次响应。

    协议里 build 与 render 是两条命令（`ensure_built()` 单独发一条），但用户
    等的是**一次**渲染：冷启动那一下的几十秒全在 build 里。不并过来的话，
    响应里只剩十几毫秒的 apply/draw——读数与体感对不上的性能数据比没有更糟。

    build 的往返总时长单列 `build_total_ms`，**不去改 render 自己的
    `total_ms`**：后者的定义是「这条 render 请求的往返」，混进别的命令就没法
    再和热态那些数放在一列里比。
    """
    if not build:
        return resp
    timings = resp.setdefault("timings", {})
    for key in ("script_exec_ms", "script_build_ms"):
        if key in build:
            timings[key] = build[key]
    if "total_ms" in build:
        timings["build_total_ms"] = build["total_ms"]
    return resp


def _norm_dir(figures_dir: str | Path) -> str:
    """池键里的项目标识：解析成绝对路径，大小写不敏感的**卷**上统一小写。

    按卷探测而不是按 `os.name` 判（macOS 的 APFS 同样大小写不敏感）——
    与 `app._project_id()` 共用 `config.normalize_path_identity`，两边分头
    判断的话，一个认为是同一个项目、另一个认为是两个，池与写回基线对不上。
    """
    try:
        p = str(Path(figures_dir).expanduser().resolve())
    except OSError:
        p = str(figures_dir)
    return config.normalize_path_identity(p)


def _next_generation(key: tuple[str, str]) -> int:
    """该池键的下一代序号（从 1 开始，每重建一次 +1，进程内单调）。"""
    with _gen_lock:
        gen = _generations.get(key, 0) + 1
        _generations[key] = gen
        return gen


def _cache_slug(figures_dir: str, script_name: str) -> str:
    """(项目, 脚本) → 缓存子目录名。

    以前是 `Path(script_name).stem`：不同项目 / 不同子目录下的同名脚本会共用
    同一个 out/sandbox 目录，互相覆盖 SVG 与 manifest。
    """
    digest = hashlib.sha1(figures_dir.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w.-]+", "_", script_name.replace("\\", "/").rstrip("/"))
    return f"{digest}-{safe[:60]}"


def script_sha1(figures_dir: str, script_name: str) -> str:
    """脚本文件当前内容的 sha1（读不到回空串）。

    worker 是在 spawn 那一刻把脚本 import 进内存的，之后脚本再被改（AI 桥改图、
    用户自己编辑）这条会话仍跑着旧代码——mtime watcher 有 2 秒轮询窗口。写回是
    **覆盖用户原件**的动作，那个窗口必须关死：写回前重算一次，与 spawn 时记下的
    对不上就阻断。
    """
    h = hashlib.sha1()
    try:
        with open(str(Path(figures_dir) / script_name), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


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


def _has_matplotlib(python: str, *, bundled: bool = False) -> bool:
    """真去 import 一次。manifest 说装了不算数——DLL 缺失、被杀毒软件隔离了
    某个 .pyd，都是「文件在但 import 不了」。

    **探测与真正起 worker 必须用同一套 env/args**（`bundled` 时的
    `child_env()` / `child_args()`）。Windows 上 `._pth` 的隔离模式顺手挡住了
    敌意环境变量，**macOS 上没有任何东西挡**：用户从终端启动 Magplot 时，
    shell 里为 Conda 或自家项目设的 `PYTHONHOME` / `PYTHONPATH` 会原样传给
    内置解释器，这一句 `import matplotlib` 当场失败——于是一个完全好用的
    内置 runtime 被判成「不可用」，退回别的 Python 甚至报「没有渲染环境」，
    而同一个解释器在 worker 那条路上是好的。只在「从终端启动」时复现，
    从 Finder 双击一切正常。
    """
    args = runtime.child_args() if bundled else []
    env = runtime.child_env() if bundled else None
    try:
        # stdin 必须显式断开：桌面 sidecar 的 stdin 是「父进程死亡信号」管道，
        # 绝不能被子进程继承（Windows 上实测继承它会让子解释器启动挂死 30s，
        # 症状是桌面版「渲染环境不可用」而同一解释器在终端里探测秒过）
        probe = subprocess.run([python, *args, "-c", "import matplotlib"],
                               capture_output=True, timeout=30,
                               stdin=subprocess.DEVNULL, env=env,
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
        if _has_matplotlib(cand, bundled=source == SOURCE_BUNDLED):
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
    def __init__(self, script_name: str, figures_dir: str, entry: str,
                 base_dir: Path | None = None):
        self.script_name = script_name
        self.figures_dir = figures_dir
        self.entry = entry
        # `base_dir` 只给一次性 worker（`one_shot()`）用：与热会话共用 out/
        # 会让重放的 manifest/SVG 盖掉用户正在看的那份。池里的会话永远走
        # `_cache_slug`，落点一个字节都没变。
        base = base_dir or ENGINE_CACHE / _cache_slug(_norm_dir(figures_dir), script_name)
        self.base = base
        self.out_dir = base / "out"
        self.sandbox = base / "sandbox"
        self.export_dir = base / "export"
        self.log_path = base / "worker.log"
        base.mkdir(parents=True, exist_ok=True)
        self._touched = 0.0
        self._touch()                      # mkdir 对已存在的目录不动 mtime，见 _touch
        self.rev = 0                       # 每次 override 递增，用于前端缓存穿透
        # 这一代的序号：同一 (项目, 脚本) 每重建一次 +1，随每个请求发给 worker
        # 并原样回显（worker 不理解它，校验归调用方/未来的 supervisor）。
        self.generation = _next_generation((_norm_dir(figures_dir), script_name))
        # spawn 那一刻脚本文件的内容指纹（写回前的「脚本变更防线」比对基准）
        self.script_sha1 = script_sha1(figures_dir, script_name)
        #: 这条会话上最后一次 `override()` 的规范 patch 哈希（build 之后是空列表）。
        #: 写回时据此判断「热态手里的这份 manifest 是不是同一组 patches 出的」。
        self.last_patch_hash = ""
        #: **按 stem** 记的同一件事，见 `stem_patch_hash()`。
        self.last_patch_hash_by_stem: dict[str, str] = {}
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

    def _envelope(self, obj: dict) -> dict:
        """把 `{"cmd": …, 其余参数}` 装进 v1 信封。

        `stem` 走顶层（它是「这条请求作用在哪张图上」，与命令参数不是一回事），
        其余参数进 `payload`。带 patches 的命令顺手算上 canonical hash——
        worker 会自己再算一遍对一下，两边序列化分歧当场暴露（这条自检为的是
        将来 Rust supervisor 接手时不会静默地发出「看起来一样其实不一样」的
        patch 列表）。
        """
        payload = dict(obj)
        cmd = payload.pop("cmd")
        stem = payload.pop("stem", None)
        env = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": f"r-{uuid.uuid4().hex}",
            "worker_generation": self.generation,
            "render_revision": self.rev,
            "cmd": _V1_CMD.get(cmd, cmd),
            "payload": payload,
        }
        if stem is not None:
            env["stem"] = stem
        if "patches" in payload:
            env["canonical_patch_hash"] = patchspec.patch_hash(payload["patches"])
        return env

    def _kill_now(self) -> None:
        """状态未知的会话立即杀掉（与超时同纪律，绝不复用）。"""
        try:
            self.proc.kill()
            self.proc.wait(timeout=_SHUTDOWN_JOIN_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            pass

    def _check_envelope(self, resp: dict, rid: str) -> None:
        """响应必须是对这条请求的回答，否则这个会话已经错位了。

        管道是串行的，回显对不上只有两种可能：worker 少回/多回了一条，
        或者对面根本不是我们以为的那个实现。两种都意味着**后续所有响应都
        对不上号**——继续用下去，用户会看到 A 图的 manifest 落到 B 图上。
        杀掉重建是唯一安全的处置（下一次 `get()` 自动起新的）。
        """
        got = resp.get("request_id")
        ver = resp.get("protocol_version")
        if got == rid and ver == PROTOCOL_VERSION:
            return
        self._kill_now()
        detail = (f"protocol_version={ver!r}" if got == rid
                  else f"request_id={got!r}，期待 {rid!r}")
        raise WorkerError(
            f"渲染会话协议错乱（{detail}）。会话已重启，可以重试。",
            self._log_tail(), code="protocol_mismatch")

    def _error_of(self, resp: dict) -> WorkerError:
        """v1 错误信封 → WorkerError（legacy 的扁平形状一并兼容）。"""
        err = resp.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or "worker 错误"
            tb = err.get("traceback", "")
            code = err.get("code", "")
        else:
            msg = err or "worker 错误"
            tb = resp.get("traceback", "")
            code = ""
        # missing_dependency 优先于协议 code：worker 那边它只是一个普通的
        # script_error，但对用户来说「缺包」是完全不同的一件事（有可执行出口）。
        mod = missing_module(f"{msg}\n{tb}")
        if mod:
            return WorkerError(
                f"脚本用到的 {mod} 在当前渲染环境里没有。"
                f"可以在设置 →「渲染环境」里改用你自己那套装了 {mod} 的 "
                f"Python / Conda 环境。",
                tb, code="missing_dependency", module=mod)
        return WorkerError(msg, tb, code=code)

    def request(self, obj: dict, timeout: float | None = None) -> dict:
        # None → 取模块常量的**当前**值（默认参数会在 def 时定死，测试改不动）
        timeout = REQUEST_TIMEOUT if timeout is None else timeout
        self.last_used = time.time()
        # 所有命令（build/override/export/render_png/preview_png）都经这里，
        # 是「这个会话真的被用了」覆盖面最全的一个点。
        self._touch()
        env = self._envelope(obj)
        rid = env["request_id"]
        t_req = time.perf_counter()
        with self.lock:
            # 拿到锁的那一刻 = 这条请求真正开始被处理。Python 池没有队列，
            # 「排队」全表现为在这把锁上等——所以它就是 queue_wait 的量法。
            t_lock = time.perf_counter()
            if not self.alive():
                raise WorkerError("worker 进程已退出", self._log_tail())
            self.proc.stdin.write(json.dumps(env, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self._readline(timeout)
        if not line:
            raise WorkerError("worker 进程崩溃（无响应）", self._log_tail())
        resp = json.loads(line)
        self._check_envelope(resp, rid)
        if resp.get("hash_mismatch"):
            # 不影响本次结果（worker 照常执行了），但两侧的规范化实现已经分叉
            LOG.warning("worker 报告 patch 哈希不一致: %s → %s（%s）",
                        env.get("canonical_patch_hash"),
                        resp.get("worker_patch_hash"), self.script_name)
        if not resp.get("ok"):
            raise self._error_of(resp)
        return _merge_timings(resp, (t_lock - t_req) * 1000.0,
                              (time.perf_counter() - t_req) * 1000.0)

    def ensure_built(self) -> dict:
        # build 要跑用户整个脚本（heavy 的分钟级），给最宽的一档
        resp = self.request({"cmd": "build"}, BUILD_TIMEOUT)
        self.built = True
        self.last_patch_hash = _EMPTY_PATCH_HASH
        self.last_patch_hash_by_stem.clear()      # 每个 stem 都回到脚本原样
        return resp

    def override(self, stem: str, patches: list,
                 preview_dpi: int | None = None,
                 inline_svg: bool = False) -> dict:
        build = self.ensure_built().get("timings") if not self.built else None
        payload = {"cmd": "override", "stem": stem, "patches": patches}
        # 不给就**一个字段都不加**：信封形状对既有调用方一字不变
        if preview_dpi:
            payload["preview_dpi"] = int(preview_dpi)
        if inline_svg:
            payload["inline_svg"] = True
        resp = self.request(payload, REQUEST_TIMEOUT)
        self.rev += 1
        self.last_patch_hash = patchspec.patch_hash(patches)
        self.last_patch_hash_by_stem[stem] = self.last_patch_hash
        return _fold_build_timings(resp, build)

    def export(self, stem: str, patches: list, path: str,
               fmt: str = "pdf", dpi: int = 600) -> dict:
        build = self.ensure_built().get("timings") if not self.built else None
        resp = self.request({"cmd": "export", "stem": stem, "patches": patches,
                             "path": path, "format": fmt, "dpi": dpi},
                            EXPORT_TIMEOUT)
        return _fold_build_timings(resp, build)

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

    def force_kill(self) -> None:
        """兜底硬杀（`shutdown_all(wait=True)` 在优雅关停超时后调）。"""
        try:
            self.proc.kill()
        except OSError:
            pass


# ============================================================================
# Rust supervisor 路由（magplot-workerd，契约见 docs/adr/0004-workerd-supervisor.md）
#
# 找得到二进制就把**生命周期**交给它（队列合并、超时强杀、取消、代序隔离），
# 找不到 / 显式禁用就原路走上面那套 Python 实现——那条路径**一行都没动**，
# 它同时还是 workerd 行为的参考实现（reference oracle）。
#
# 分工不许含糊：**Rust 是机制层，Python 是策略层**。解释器优先级
# （`_prioritized_candidates()`）、内置 runtime 的 env/args、超时档位、会话上限，
# 全部在这里算完再装进 spawn 规格交过去。把它们搬进 Rust 就是制造第二个权威。
# ============================================================================

#: 握手（v1 ping）期限：解释器冷启动 + import matplotlib 在慢盘上真能到十几秒。
HANDSHAKE_TIMEOUT = 60.0

#: 单会话队列上限（workerd 的有界队列）。排队无上限时一次卡顿会攒出几百条早就
#: 没人要的渲染，之后逐条跑完——用户看到的是「越用越慢」。
MAX_QUEUE = 32

#: 这些 code 意味着**这条会话的状态已经不可知**，与 Python 池里「超时/错乱就
#: kill，下一次 get() 原地重建」是同一条纪律：标记死亡 → `alive()` 回 False →
#: `get()` 建新的。
_FATAL_CODES = frozenset({
    "session_dead", "spawn_failed", "handshake_timeout", "protocol_mismatch",
    "worker_timeout", "workerd_dead", "workerd_unavailable",
})


def _spawn_spec(script_name: str, figures_dir: str, entry: str, out_dir: Path,
                sandbox: Path, log_path: Path, python: str, source: str,
                extra_env: dict | None = None) -> dict:
    """交给 workerd 的**完整** spawn 规格。

    与 `EngineWorker.__init__` 里那串 Popen 参数严格同源。刻意不去重构成共享
    helper：Python 池那条路径这次一行都不动是前提，多这几行远比让两条路径共享
    一个会被同时改到的函数安全。
    """
    bundled = source == SOURCE_BUNDLED
    args = runtime.child_args() if bundled else []
    # 只给**增量**：workerd 继承的本来就是 Flask 自己的环境，整份传过去没有意义
    env = runtime.child_env(base={}) if bundled else {}
    if extra_env:
        # env 参与 workerd 的 spec 哈希（`SpawnSpec::hash`），所以一个一次性
        # 的 salt 就足以拿到一条**必然独立**的会话，绕开「同规格复用 + 引用计数」。
        env = {**env, **extra_env}
    return {
        "argv": [python, *args, str(WORKER_PY),
                 "--script", str(Path(figures_dir) / script_name),
                 "--figures-dir", figures_dir,
                 "--out-dir", str(out_dir),
                 "--sandbox", str(sandbox),
                 "--entry", entry],
        "env": env,
        "log_path": str(log_path),
        "handshake_timeout_ms": int(HANDSHAKE_TIMEOUT * 1000),
        "label": f"{script_name}::{entry}",
    }


def _worker_error(message: str, code: str, traceback_text: str,
                  extra: dict | None = None) -> WorkerError:
    """错误三元组 → `WorkerError`，**`missing_dependency` 优先于协议 code**。

    判据与 `EngineWorker._error_of` 一致：脚本 `import rdkit` 而渲染环境没有，
    在 worker 那里只是一个普通 `script_error`，但对用户是完全不同的一件事
    （有可执行出口：换成自己的环境）。前端认的是这个 code，不能因为换了控制面
    就变成一段没人能用的通用错误。
    """
    mod = missing_module(f"{message}\n{traceback_text}")
    if mod:
        return WorkerError(
            f"脚本用到的 {mod} 在当前渲染环境里没有。"
            f"可以在设置 →「渲染环境」里改用你自己那套装了 {mod} 的 "
            f"Python / Conda 环境。",
            traceback_text, code="missing_dependency", module=mod)
    err = WorkerError(message, traceback_text, code=code)
    if extra:
        # worker 多带的字段（unknown_stem 的 `known` 之类）留给上层
        err.extra = extra
    return err


class WorkerdWorker:
    """`EngineWorker` 的等价物，但生命周期由 magplot-workerd 管。

    **对 `app.py` 完全同形**：`ensure_built` / `override` / `export` /
    `render_png` / `preview_png` / `svg_path` / `out_dir` / `rev` 的签名与返回
    结构一字不差，切控制面对上层透明。
    """

    def __init__(self, script_name: str, figures_dir: str, entry: str,
                 client=None, base_dir: Path | None = None,
                 extra_env: dict | None = None):
        from . import workerd_client

        self.script_name = script_name
        self.figures_dir = figures_dir
        self.entry = entry
        # 目录布局与 EngineWorker 完全一致：prune_engine_cache 按 base 走，
        # 换个控制面就换个落点的话，清理会把正在用的会话目录当成垃圾删掉。
        base = base_dir or ENGINE_CACHE / _cache_slug(_norm_dir(figures_dir), script_name)
        self.base = base
        self._extra_env = dict(extra_env or {})
        self.out_dir = base / "out"
        self.sandbox = base / "sandbox"
        self.export_dir = base / "export"
        self.log_path = base / "worker.log"
        base.mkdir(parents=True, exist_ok=True)
        self._touched = 0.0
        self._touch()
        self.rev = 0
        self.generation = _next_generation((_norm_dir(figures_dir), script_name))
        # 与 EngineWorker 同源：spawn 时的脚本指纹 + 最后应用的 patch 哈希
        self.script_sha1 = script_sha1(figures_dir, script_name)
        self.last_patch_hash = ""
        self.last_patch_hash_by_stem: dict[str, str] = {}
        # workerd 自己排队，这把锁只是为了与 EngineWorker 同形（调用方不该关心
        # 是哪条路径）。**绝不拿它包住一次请求**——那会把 workerd 好不容易解开的
        # 「一个慢请求占死整条会话」重新绑回来。
        self.lock = threading.Lock()
        self.built = False
        self.last_used = time.time()
        self._dead = False
        self._client = client or workerd_client.client()
        if self._client is None:
            raise WorkerdUnavailable("workerd 不可用")
        python, self.python_source = select_worker_python()
        self.python = python
        self._session_id = ""
        self._open()

    # ---------------------------------------------------------------- 会话
    def _spec(self) -> dict:
        return _spawn_spec(self.script_name, self.figures_dir, self.entry,
                           self.out_dir, self.sandbox, self.log_path,
                           self.python, self.python_source, self._extra_env)

    def _open(self) -> None:
        from . import workerd_client

        LOG.info("workerd 会话打开: %s（entry=%s，解释器来源=%s）",
                 self.script_name, self.entry, self.python_source)
        try:
            resp = self._client.call("open_session", payload=self._spec(),
                                     timeout=HANDSHAKE_TIMEOUT)
        except workerd_client.WorkerdError as exc:
            self._dead = True
            # **失败也要认领 session_id 并把它关掉。**
            # workerd 在 open 的那一刻就把会话记进了 sessions / by_hash，
            # 握手或 spawn 失败时那条记录不会自己消失；而失败响应里的
            # session_id 是唯一的线索，不认领的话它就成了谁也够不着的幽灵：
            # refs 停在 1，只能等超出 max_sessions 时被淘汰——被挤掉的往往是
            # **真正在用**的那条会话。
            # 关不掉就算了（workerd 可能已经整个没了），绝不让清理动作把
            # 真正的失败原因盖过去。
            if exc.session_id:
                try:
                    self._client.call("close_session", session_id=exc.session_id,
                                      payload={"force": True}, timeout=5.0)
                except workerd_client.WorkerdError:
                    LOG.debug("open 失败后清理会话 %s 也没成功", exc.session_id)
            raise self._to_worker_error(exc) from exc
        self._session_id = resp.get("session_id", "")
        self.built = False

    def _log_tail(self, n: int = 30) -> str:
        try:
            lines = self.log_path.read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except OSError:
            return ""

    def _to_worker_error(self, exc) -> WorkerError:
        code = exc.code or ""
        if code in _FATAL_CODES:
            # 状态未知的会话绝不复用（与 Python 池的超时/错乱路径同纪律）
            self._dead = True
        tb = exc.traceback_text or ""
        if not tb and code in _FATAL_CODES:
            tb = self._log_tail()      # 进程级失败时 worker 的 traceback 全在日志里
        return _worker_error(str(exc), code, tb, exc.extra)

    def _call(self, op: str, timeout: float, *, stem: str | None = None,
              payload: dict | None = None) -> dict:
        from . import workerd_client

        self.last_used = time.time()
        self._touch()
        t_req = time.perf_counter()
        for attempt in (0, 1):
            t_call = time.perf_counter()
            try:
                resp = self._client.call(op, session_id=self._session_id,
                                         stem=stem, payload=payload or {},
                                         timeout=timeout)
            except workerd_client.WorkerdError as exc:
                # workerd 重启过 → session_id 作废。这条**透明重开一次**：
                # 对上层来说这只是一次稍慢的渲染，没有任何语义变化。
                if exc.code == "unknown_session" and attempt == 0:
                    LOG.warning("workerd 会话已失效，重开: %s", self.script_name)
                    self._open()
                    continue
                raise self._to_worker_error(exc) from exc
            if resp.get("hash_mismatch"):
                # 本次结果照常可用（worker 执行了），但两侧的规范化实现已经分叉
                LOG.warning("worker 报告 patch 哈希不一致: %s → %s（%s）",
                            resp.get("canonical_patch_hash"),
                            resp.get("worker_patch_hash"), self.script_name)
            # queue_wait 的口径与 Python 池**不一样，这里如实标注**：真正的排队
            # 发生在 workerd 的合并队列里（Rust 侧），workerd 自报就透传它；
            # 没自报时只能给 Python 侧那段（≈0，本进程不排队），别把它当成
            # 「没排队」。差异见 ADR 0004 §6。
            reported = resp.get("queue_wait_ms")
            wait_ms = (float(reported) if isinstance(reported, (int, float))
                       and not isinstance(reported, bool)
                       else (t_call - t_req) * 1000.0)
            return _merge_timings(resp, wait_ms,
                                  (time.perf_counter() - t_req) * 1000.0)
        raise WorkerError("workerd 会话重开后仍不可用", self._log_tail(),
                          code="session_dead")

    # ---------------------------------------------------------------- 同 EngineWorker
    def alive(self) -> bool:
        return not self._dead

    def _touch(self) -> None:
        import os
        now = time.time()
        if now - self._touched < _TOUCH_INTERVAL:
            return
        self._touched = now
        try:
            os.utime(self.base, None)
        except OSError:
            pass

    def ensure_built(self) -> dict:
        resp = self._call("build", BUILD_TIMEOUT)
        self.built = True
        self.last_patch_hash = _EMPTY_PATCH_HASH
        self.last_patch_hash_by_stem.clear()      # 每个 stem 都回到脚本原样
        return resp

    def override(self, stem: str, patches: list,
                 preview_dpi: int | None = None,
                 inline_svg: bool = False) -> dict:
        build = self.ensure_built().get("timings") if not self.built else None
        payload: dict = {"patches": patches}
        if preview_dpi:
            payload["preview_dpi"] = int(preview_dpi)
        if inline_svg:
            payload["inline_svg"] = True
        resp = self._call("render", REQUEST_TIMEOUT, stem=stem, payload=payload)
        self.rev += 1
        self.last_patch_hash = patchspec.patch_hash(patches)
        self.last_patch_hash_by_stem[stem] = self.last_patch_hash
        return _fold_build_timings(resp, build)

    def export(self, stem: str, patches: list, path: str,
               fmt: str = "pdf", dpi: int = 600) -> dict:
        build = self.ensure_built().get("timings") if not self.built else None
        resp = self._call("export", EXPORT_TIMEOUT, stem=stem,
                          payload={"patches": patches, "path": path,
                                   "format": fmt, "dpi": dpi})
        return _fold_build_timings(resp, build)

    def svg_path(self, stem: str) -> Path:
        return self.out_dir / f"{stem}.svg"

    def render_png(self, stem: str, width_px: int) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self._call("render_png", REQUEST_TIMEOUT, stem=stem,
                          payload={"width": width_px})
        return Path(resp["path"])

    def preview_png(self, stem: str, patches: list, width_px: int, tag: str) -> Path:
        if not self.built:
            self.ensure_built()
        resp = self._call("preview_png", REQUEST_TIMEOUT, stem=stem,
                          payload={"patches": patches, "width": width_px,
                                   "tag": tag})
        return Path(resp["path"])

    def shutdown(self) -> None:
        """优雅关会话；workerd 收不到就当它已经没了（不许把退出流程挂住）。"""
        from . import workerd_client

        if not self._session_id:
            return
        try:
            # 退出路径不许被一个卡住的 supervisor 拖住：余量收到 5 秒
            self._client.call("close_session", session_id=self._session_id,
                              timeout=SHUTDOWN_TIMEOUT, slack=5.0)
        except workerd_client.WorkerdError:
            pass
        finally:
            self._dead = True

    def force_kill(self) -> None:
        """硬关：workerd 当场杀掉 worker，不等在飞的活跑完。"""
        from . import workerd_client

        if not self._session_id:
            return
        try:
            self._client.call("close_session", session_id=self._session_id,
                              payload={"force": True}, timeout=SHUTDOWN_TIMEOUT,
                              slack=2.0)
        except workerd_client.WorkerdError:
            pass
        finally:
            self._dead = True


class WorkerdUnavailable(RuntimeError):
    """workerd 这条路走不通（没装 / 禁用 / 起不来）——调用方回退 Python 池。"""


def workerd_path() -> str | None:
    """本次进程实际会用的 workerd 可执行文件（禁用或找不到回 None）。"""
    from . import workerd_client
    return workerd_client.find_workerd()


def control_plane() -> dict:
    """当前渲染控制面：**下一条会话会走谁** + 池里活着的会话**实际走的谁**。

    两个字段缺一不可。`_new_worker()` 在 workerd 建会话失败时会**静默回退**到
    Python 池（那是刻意的：加速件起不来不该让渲染整个不可用），所以只报
    `selected` 会把「打进去了但一直没用上」说成一切正常——而那正是「功能全在、
    只是慢」这一类最难被发现的失灵。冒烟脚本与诊断包都据此判定。
    """
    path = workerd_path()
    with _lock:
        sessions = ["workerd" if isinstance(w, WorkerdWorker) else "python"
                    for w in _workers.values()]
    return {"selected": "workerd" if path else "python",
            "path": path, "sessions": sessions}


def _new_worker(script_name: str, figures_dir: str, entry: str):
    """按可用性挑控制面。**任何失败都回退 Python 池**——渲染不能因为一个
    可选的加速件起不来就整个不可用。"""
    from . import workerd_client

    if workerd_client.find_workerd():
        try:
            return WorkerdWorker(script_name, figures_dir, entry)
        except (WorkerdUnavailable, WorkerError, OSError) as exc:
            LOG.warning("workerd 会话建立失败，回退到 Python 渲染池: %s", exc)
    return EngineWorker(script_name, figures_dir, entry)


def one_shot(script_name: str, figures_dir: str, entry: str):
    """一次性 worker：**不进池、目录独立、用完即毁**。写回前的干净重放用。

    热会话是长期活着的：build 之后经历过任意多次 override / 还原，applied 与
    originals 两表就是一份增量历史。写回要保证的是「重开这个项目、按这组
    patches 全量重放一次，得到的图与热态所见一模一样」——那就必须真的**从零
    起一个 worker 跑一遍脚本**，拿它的产物去覆盖用户原件（FigS3 那次文字全体
    错位，症状正是热会话状态 ≠ 全量重放）。

    两条控制面各有一处必须绕开的复用：

    * Python 池按 `(项目, 脚本)` 索引 —— 这里干脆不登记，调用方拿着引用用完
      `discard()`；
    * workerd 按 spawn 规格哈希复用会话（引用计数，见 ADR 0004）—— 目录不同
      argv 就不同，再加一个一次性 salt env 双保险，拿到的必然是独立会话。

    目录放在 ENGINE_CACHE 顶层（`_replay-…`）而不是数据目录别处：进程在写回
    途中被杀时，留下的空壳会被 `prune_engine_cache()` 当成最久未用的会话目录
    正常回收，不需要另写一套清理。
    """
    from . import workerd_client

    nonce = uuid.uuid4().hex
    slug = _cache_slug(_norm_dir(figures_dir), script_name)
    base = ENGINE_CACHE / f"_replay-{nonce[:8]}-{slug}"
    with _lock:
        _oneshot_bases.add(str(base))
    try:
        if workerd_client.find_workerd():
            try:
                return WorkerdWorker(script_name, figures_dir, entry,
                                     base_dir=base,
                                     extra_env={"MAGPLOT_REPLAY_NONCE": nonce})
            except (WorkerdUnavailable, WorkerError, OSError) as exc:
                LOG.warning("workerd 一次性会话建立失败，回退到 Python 渲染池: %s", exc)
        return EngineWorker(script_name, figures_dir, entry, base_dir=base)
    except BaseException:
        with _lock:
            _oneshot_bases.discard(str(base))
        shutil.rmtree(base, ignore_errors=True)
        raise


def discard(worker) -> None:
    """关掉一次性 worker 并删掉它的目录。**绝不抛**——写回的成败与它无关。"""
    try:
        worker.shutdown()
    except Exception:            # noqa: BLE001 — 收尾动作不许连累主流程
        LOG.warning("一次性 worker 关停失败: %s", worker.script_name, exc_info=True)
    with _lock:
        _oneshot_bases.discard(str(worker.base))
    shutil.rmtree(worker.base, ignore_errors=True)


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
        # 一次性 worker（写回的干净重放）不在池里，但它的目录正在被写
        busy = {str(w.base) for w in _workers.values()} | set(_oneshot_bases)
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
            w = _new_worker(script_name, figures_dir, entry)
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
                # `force_kill()` 两条控制面都有：Python 池是 `proc.kill()`，
                # workerd 是「当场关掉会话、不等在飞的活」。
                w.force_kill()
    if wait and figures_dir is None:
        # 「本进程即将退出」这条路径顺手把 supervisor 也收掉。不收也不会留孤儿
        # （父进程一走它的 stdin 就 EOF，workerd 自己会退），但那要等到父进程真的
        # 消失；显式关掉能让退出是**可观测**的，冒烟脚本才断言得出来。
        try:
            from . import workerd_client
            workerd_client.reset_client()
        except Exception:  # noqa: BLE001 — 退出路径不许因为收尾动作抛出而中断
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
