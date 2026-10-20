"""写入根绊线（#898，ADR 0141）：MCP 来源的 safe worker 里，脚本写根外就在**事件发生时**失败。

**它是绊线，不是沙盒。** 目标只有一个：Agent 经 MCP 打开一份「合作但有 bug / 被误导」的脚本时，
`os.chdir(...)` 之后的相对写、跨文件 helper 里切目录再写这类静态读源码证明不了的路径，不再悄悄落到授权根之外。
判据用 `sys.addaudithook` 的 `open` 事件，在写发生的那一刻按**真实 cwd** 解开符号链接再比对白名单——
静态证明（#898、#1086 的共同根因）换成运行期授权。

挡得住的（真 worker 里用例看护）：`open` / `Path.write_text` / `os.open` / numpy·PIL·matplotlib 的 Python 层写 /
pickle / `shutil.copyfile` / 根内符号链接指向根外 / 线程与 fork 子进程里的 Python 写，以及 `+ext` 这一组的
`os.rename|remove|rmdir|mkdir|truncate|symlink|link`、`shutil.copy*|move|rmtree`。

**挡不住的（用例里断言「不挡」，防止后人误宣称）**：

* `ctypes` 直调 libc、`subprocess` / `os.system` 起的外部进程、C 层 `fopen`（h5py / netCDF / sqlite3 的 C 层打开）；
* `dir_fd` 相对打开（事件里没有目录 fd 的路径）；
* 脚本自己调 `writetripwire.arm(False)` 关掉开关——钩子装上就摘不掉（PEP 578），但开关在脚本够得到的进程里；
* 绊线窗口（脚本 build）之外：脚本留下的线程、`atexit`、后续协议请求里的写（引擎自己导出到任意临时目录，
  不能拦）；
* `os.chmod|utime|setxattr` 这类改属性的事件没纳入。

纯标准库、**不 import 任何兄弟模块**：它装进 worker 的私有包（`worker._ENGINE_MODULES`）。
"""

from __future__ import annotations

import errno
import os
import re
import sys
import tempfile
import threading

#: 稳定错误码（worker `ProtocolError.code` → 池 `WorkerError.code` → MCP `BridgeError.code`）。
CODE = "write_outside_roots"

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

#: 非 `open` 事件：event → (要检查的路径下标 ...)，以及该路径对应的 dir_fd 下标（None = 没有）。
#: 只列**产生 / 改动 / 删除**文件的事件。`os.link` 源也查：把根外文件硬链接进根内再写，等于改了根外文件
#: （不去追踪 inode，源在根外即违规）；`os.symlink` 的 src 只是链接里的文字，不查。
_MUTATION_EVENTS: dict[str, tuple[tuple[int, int | None], ...]] = {
    "os.rename": ((0, 2), (1, 3)),
    "os.remove": ((0, 1),),
    "os.rmdir": ((0, 1),),
    "os.mkdir": ((0, 2),),
    "os.truncate": ((0, None),),
    "os.symlink": ((1, 2),),
    "os.link": ((0, 2), (1, 3)),
    "shutil.copyfile": ((1, None),),
    "shutil.copymode": ((1, None),),
    "shutil.copystat": ((1, None),),
    "shutil.copytree": ((1, None),),
    "shutil.move": ((0, None), (1, None)),
    "shutil.rmtree": ((0, 1),),
}

#: importlib 写字节码的形状：`__pycache__/x.cpython-313.pyc`，以及先写再换名的 `….pyc.<id>`。
_PYC_NAME = re.compile(r"\.pyc(\.\d+)?$")

#: 记下的违规条数上限（只为报告；超出的丢弃，不无限增长）。
_MAX_RECORDED = 8

_state: dict = {
    "roots": (),
    "display_base": "",
    "armed": False,
    "installed": False,
    "violations": [],
}
_tls = threading.local()


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _real(p) -> str:
    return _norm(os.fsdecode(os.fspath(p)))


#: 目录 → 它所在的卷是否不区分大小写（按目录缓存；探测在装钩子时做，不在钩子里做）。
_FOLD_CACHE: dict[str, bool] = {}


def _probe_existing(directory: str) -> bool | None:
    """对一个**存在的**目录探测它所在的卷是否不区分大小写：翻转路径里最后一个有大小写的字母所在的那一级名字，
    父目录的 `os.listdir` 里两种写法**都字面存在** → 各是一个条目 → 区分；否则翻转后的路径存在且 `samefile`
    → 不区分。路径里没有大小写字母 / 探测出错 → None（说不清）。与 `figcapture._dir_folds_case` 等价的本地
    副本——本模块在 worker 的私有包里，不 import 兄弟模块；用例可替换它来模拟区分大小写的卷。"""
    try:
        parts = os.path.normpath(directory).split(os.sep)
        for k in range(len(parts) - 1, -1, -1):
            name = parts[k]
            if name.lower() == name.upper():
                continue
            flipped_name = name.swapcase()
            parent = os.sep.join(parts[:k]) or os.sep
            listed = os.listdir(parent)
            if name in listed and flipped_name in listed:
                return False
            flipped = os.sep.join(parts[:k] + [flipped_name] + parts[k + 1 :]) or os.sep
            return os.path.exists(flipped) and os.path.samefile(flipped, directory)
    except OSError:
        pass
    return None


def folds_case(directory: str) -> bool:
    """这个根所在的卷是否不区分大小写（按目录缓存；装钩子时探测，钩子里不做文件系统探测）。

    根不存在（`~/.cache`、`~/seaborn-data` 这类白名单根常常还没建）就向上找**最近存在的祖先**，在它所在的卷上
    探测——新建的子目录与祖先同卷。找不到存在的祖先、或探测不出 → **fail closed**：按区分大小写处理（不折叠）。
    绝不用平台默认值：区分大小写的 macOS 卷上，默认值会把 `~/SEABORN-DATA` 当成白名单根 `~/seaborn-data`
    之下的路径放行（Codex #1224 复核）。误判成「区分」的代价只是一次可恢复的误拦。"""
    if directory in _FOLD_CACHE:
        return _FOLD_CACHE[directory]
    probe = directory
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            probe = ""
            break
        probe = parent
    result = bool(probe) and _probe_existing(probe) is True
    _FOLD_CACHE[directory] = result
    return result


def _under(path: str, roots) -> bool:
    for r in roots:
        base = r.rstrip(os.sep) + os.sep
        if path == r or path.startswith(base):
            return True
        # macOS 默认卷等不区分大小写：`/users/x` 与根 `/Users/x` 是同一处，只按根所在的卷折叠
        if folds_case(r) and (path.lower() == r.lower() or path.lower().startswith(base.lower())):
            return True
    return False


def is_bytecode_path(path: str) -> bool:
    """`__pycache__` 里的 `.pyc` 文件（含 importlib 的临时名 `….pyc.<id>`）。解释器 / 站点包的字节码缓存写不该
    算越界，但**只**放行这一种形状：不是把 `sys.prefix` 整个放行（那会放行整个环境目录）。"""
    return os.path.basename(os.path.dirname(path)).lower() == "__pycache__" and bool(
        _PYC_NAME.search(os.path.basename(path))
    )


def allowed(real: str, roots, event: str = "open") -> bool:
    """`real`（已 realpath + normcase）按 `event` 做这件事算不算在白名单里。纯函数，用例直接问它。

    字节码的例外**按事件收窄**：`os.mkdir` 建 `__pycache__` 目录本身，以及对 `__pycache__/*.pyc` 形状文件的
    写打开 / 换名 / 删除（importlib 先写 `.pyc.<id>` 再 `os.replace`，失败还要 `unlink` 临时文件）。
    目录本身的 rmdir / rmtree / 换名、别的名字的文件，一律不例外。"""
    if _under(real, roots):
        return True
    if event == "os.mkdir" and os.path.basename(real).lower() == "__pycache__":
        return True
    if event in ("open", "os.rename", "os.remove") and is_bytecode_path(real):
        return True
    if os.name != "nt" and (real == "/dev" or real.startswith("/dev/")):
        return True  # 设备文件（/dev/null、/dev/stdout…）不是文件落盘
    return real == os.path.normcase(os.devnull)


def display(real: str, base: str) -> str:
    """违规路径的展示：相对项目根（不暴露无关的绝对路径）；跨盘符时只留末两段。"""
    try:
        if base:
            return os.path.relpath(real, base).replace(os.sep, "/")
    except ValueError:
        pass
    parts = [p for p in real.replace("\\", "/").split("/") if p]
    return ".../" + "/".join(parts[-2:]) if parts else real


def infrastructure_roots(
    env=None, *, home: str | None = None, platform: str | None = None, extra=()
) -> list[str]:
    """不属于任何授权根、但引擎与科学库合法要写的位置（每一项的理由写在旁边）。

    只列**目录**；`extra` 给调用方已知的：会话沙盒 / 会话缓存目录 / matplotlib 的缓存与配置目录。
    不含：`sys.prefix`（只放行字节码形状，见 `is_bytecode_path`）、整个用户数据目录（里面有配置与密钥，
    会话缓存已由调用方的 `extra` 给出）。"""
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    platform = sys.platform if platform is None else platform
    roots: list[str] = [*extra]
    # 临时文件：tempfile / 库的中间产物（TMPDIR 等已由 gettempdir 读过）
    roots.append(tempfile.gettempdir())
    # matplotlib 自己的配置 / 字体缓存（受管环境把它改道到 Tavotto 的缓存目录就是靠这个变量）
    if env.get("MPLCONFIGDIR"):
        roots.append(env["MPLCONFIGDIR"])
    # 平台用户缓存目录：库的下载 / 编译缓存（pooch、huggingface、numba、pip 的 HTTP 缓存…）按平台惯例落在这里
    if env.get("XDG_CACHE_HOME"):
        roots.append(env["XDG_CACHE_HOME"])
    if platform == "darwin":
        roots.append(os.path.join(home, "Library", "Caches"))
    if platform == "win32":
        # 只列缓存性质的子目录，不放行整个 %LOCALAPPDATA%（Tavotto 的数据目录就在它下面）
        local = env.get("LOCALAPPDATA")
        if local:
            roots += [os.path.join(local, n) for n in ("Temp", "pip", "matplotlib")]
    else:
        roots.append(os.path.join(home, ".cache"))
    # 已知科学库的数据目录：加载示例数据集 / 下载数据集的默认缓存位置，不在任何缓存目录里
    # seaborn.load_dataset：SEABORN_DATA 或 ~/seaborn-data（C0 实测的真误拦）
    roots.append(env.get("SEABORN_DATA") or os.path.join(home, "seaborn-data"))
    # scikit-learn 的 fetch_* 数据集：SCIKIT_LEARN_DATA 或 ~/scikit_learn_data
    roots.append(env.get("SCIKIT_LEARN_DATA") or os.path.join(home, "scikit_learn_data"))
    return [r for r in roots if r]


def _record(event: str, real: str) -> None:
    items = _state["violations"]
    if len(items) < _MAX_RECORDED:
        items.append({"event": event, "path": display(real, _state["display_base"])})


def _violation(event: str, real: str) -> None:
    _record(event, real)
    shown = display(real, _state["display_base"])
    raise PermissionError(
        errno.EACCES,
        f"[{CODE}] 写入落在授权根之外（Tavotto 只允许经 MCP 运行的脚本写到工作区内）",
        shown,
    )


def _hook(event: str, args: tuple) -> None:
    if not _state["armed"] or getattr(_tls, "busy", False):
        return
    if event == "open":
        path, mode, flags = args[0], args[1], args[2]
        if isinstance(path, int) or not isinstance(path, (str, bytes, os.PathLike)):
            return
        if isinstance(flags, int):
            writing = bool(flags & _WRITE_FLAGS)
        else:
            writing = isinstance(mode, str) and any(c in mode for c in "wax+")
        if not writing:
            return
        targets = [(path, None)]
    elif event in _MUTATION_EVENTS:
        targets = []
        for idx, fd_idx in _MUTATION_EVENTS[event]:
            if idx >= len(args) or not isinstance(args[idx], (str, bytes, os.PathLike)):
                continue
            targets.append(
                (args[idx], args[fd_idx] if fd_idx is not None and fd_idx < len(args) else None)
            )
    else:
        return
    _tls.busy = True
    try:
        for path, dir_fd in targets:
            raw = os.fsdecode(os.fspath(path))
            # 相对 `dir_fd` 的相对路径解析不出（盲区，见模块头）；绝对路径时 dir_fd 被操作系统忽略
            if dir_fd not in (None, -1) and not os.path.isabs(raw):
                continue
            real = _norm(raw)
            if not allowed(real, _state["roots"], event):
                _violation(event, real)
    finally:
        _tls.busy = False


def install(roots, *, display_base: str = "") -> None:
    """装钩子并设白名单。钩子装上就摘不掉（PEP 578）；`arm()` 决定它此刻管不管。"""
    _state["roots"] = tuple(sorted({_real(r) for r in roots if r}))
    for r in _state["roots"]:
        folds_case(r)  # 在钩子装上、窗口打开之前探测并缓存（钩子里不做文件系统探测）
    _state["display_base"] = _real(display_base) if display_base else ""
    _state["violations"] = []
    if not _state["installed"]:
        sys.addaudithook(_hook)
        _state["installed"] = True


def arm(on: bool) -> None:
    """开 / 关绊线窗口（脚本 build 期间开；其余时间引擎自己的协议请求要写任意临时目录，不拦）。"""
    _state["armed"] = bool(on)


def violations() -> list[dict]:
    """本次窗口里记下的违规（`{event, path}`，path 已相对化；至多 `_MAX_RECORDED` 条）。"""
    return [dict(v) for v in _state["violations"]]


def reset() -> None:
    _state["violations"] = []
