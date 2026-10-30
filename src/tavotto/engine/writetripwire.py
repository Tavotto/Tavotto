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

**改道模式（ADR 0143 修订 2026-10-30，只属于 AI 转换 / 改图的校验运行）**：`write_roots` 里带一对 `redirect-project:` /
`redirect-dir:` 条目（`redirect_entries`）时，同一个窗口里**落在项目内的写**不再失败，而是被换到 `<redirect-dir>/<相对项目路径>`——
候选脚本保留「在数据旁写 CSV / 图」的原样行为，校验照样通过，用户项目文件逐字节不变。机制是在 `install` 时包装 Python 层入口
（`builtins.open` / `io.open` / `os.open` / `os.mkdir|rmdir|remove|unlink|rename|replace` / `shutil.copyfile|copymode|copystat|copytree|rmtree|move` /
`os.path.exists|isfile|isdir|getsize`），**只在 `arm(True)` 窗口里、只对落在项目内的路径**生效：换完路径再交给原函数，
审计钩子看到的已经是候选目录里的路径。项目外、也不在白名单的写照旧是 `write_outside_roots`（改道只认项目这一个前缀）。
读：打开读取时，项目内路径若在改道区有同名文件就读改道区的（写完再读回来可行）；`os.path.exists|isfile|isdir|getsize` 同理。
改道**不**覆盖（限制，不是缺陷）：`os.stat` / `os.listdir` / `os.scandir` / `glob` / `Path.exists|stat|iterdir`（读不到改道区的文件），
`os.symlink|link|truncate`（仍走绊线 → 失败），C 层写（同下面的盲区：绊线看不见，由校验后的项目指纹复核兜住 → 失败）。
改动已存在的项目文件（追加 / 读写模式）：先把原文件复制进改道区再改（写时复制），项目里的那份不动；
**白名单（whiteout）**：脚本通过改道层删除 / 改名走一个项目路径（含只存在于项目里的用户文件——项目里的原件不动），该路径记进
本窗口的白名单集合（worker 内存里，`install` 时清空；校验 worker 每次运行后退役，所以每次执行都从空集合开始），之后所有被包装的读 / 查询
（`open` 读、`os.open`、`os.path.exists|isfile|isdir|getsize`、`shutil.copyfile` 的 src）都把它当不存在；再次创建 / 写该路径会清掉标记。
目录的删除 / 改名不改道（走绊线 → 失败）；`os.stat` 等未包装的查询仍会看到项目里的原件（同上面的限制）。
`os.open` 不带 `O_CREAT` 打开项目里已有的文件（如 `O_WRONLY|O_TRUNC`）：先在改道区物化（截断 = 空文件，其余 = 复制）；原件不存在或已被白名单标记则保持 `FileNotFoundError`。

**失败即关闭（Codex #1516 复核后的原则）**：改道层只改道它能**忠实模拟**的操作；其余碰项目路径的操作一律不改道，
落回绊线 → `write_outside_roots`（与本机制出现之前的行为相同），校验失败也不批准一个采用后行为会不同的候选。
模拟的：文件的创建 / 覆盖 / 追加（写时复制）/ 读回 / 删除与改名（白名单）/ `x` 与 `O_EXCL`（项目里有同名文件 → `FileExistsError`）/
在项目目录里新建暂存子目录、删除**纯暂存**的目录或树；类型冲突按真实语义报错：往项目里的目录路径写文件 → `IsADirectoryError`，
在项目里的文件上 `mkdir` → `FileExistsError`，文件改名 / `copyfile` 到项目目录上 → `IsADirectoryError`，`copytree` 到项目里已有的目标 → `FileExistsError`。
**失败的（不改道，写入根绊线拒绝）**：项目里已有目录的 `rmdir` / `rmtree` / 目录改名（暂存副本删掉后原内容会重新露出来）、
目录改名到项目里已有的目录 / 文件上、`copytree(dirs_exist_ok=True)` 合并进项目目录、`copymode` / `copystat` 到只存在于项目里的文件、
在被删除的文件处 `mkdir`、`shutil.move` 进项目目录且同名项已在项目里、`copytree` 的源目录同时存在于项目与暂存区、`symlink|link|truncate`。
未包装、所以看不到暂存区的**读**（只能文档化，没法失败）：`os.stat|listdir|scandir`、`glob`、`Path.exists|stat|iterdir`。

纯标准库、**不 import 任何兄弟模块**：它装进 worker 的私有包（`worker._ENGINE_MODULES`）。
"""

from __future__ import annotations

import builtins
import errno
import functools
import io
import os
import re
import shutil
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

#: 改道模式的两个条目前缀（放进 `write_roots` 元组里随现有通道走：spec / argv / 池键 / 影子脚本键都不用改）。
REDIRECT_PROJECT_PREFIX = "redirect-project:"
REDIRECT_DIR_PREFIX = "redirect-dir:"

_state: dict = {
    "redirect": None,  # (项目真实路径, 项目 normcase 路径, 改道区真实路径, 改道区 normcase 路径)
    "redirect_installed": False,
    "whiteouts": set(),  # 本窗口里被删除 / 改名走的项目路径（键 = normcase 的项目相对路径）
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
                if event == "os.mkdir" and os.path.isdir(raw):
                    # Preserve mkdir's EEXIST result without allowing the syscall.
                    # Path.mkdir/os.makedirs(exist_ok=True) can then finish their
                    # no-op. Even if the directory disappears after this check,
                    # raising here cannot create anything outside the roots.
                    raise FileExistsError(
                        errno.EEXIST,
                        os.strerror(errno.EEXIST),
                        display(real, _state["display_base"]),
                    )
                _violation(event, real)
    finally:
        _tls.busy = False


def install(roots, *, display_base: str = "", redirect: tuple[str, str] | None = None) -> None:
    """装钩子并设白名单。钩子装上就摘不掉（PEP 578）；`arm()` 决定它此刻管不管。

    `redirect=(项目, 改道区)`：开改道模式（见模块头）；改道区必须在 `roots` 之内（否则改道后的写又被绊线拦下）。"""
    _state["roots"] = tuple(sorted({_real(r) for r in roots if r}))
    for r in _state["roots"]:
        folds_case(r)  # 在钩子装上、窗口打开之前探测并缓存（钩子里不做文件系统探测）
    _state["display_base"] = _real(display_base) if display_base else ""
    _state["violations"] = []
    _state["redirected"] = []
    _state["whiteouts"] = set()
    if redirect is None:
        _state["redirect"] = None
    else:
        project, directory = redirect
        if not _under(_real(directory), _state["roots"]):
            raise ValueError("改道区必须在授权根之内")
        os.makedirs(directory, exist_ok=True)
        rd = os.path.realpath(directory)
        pr = os.path.realpath(project)
        _state["redirect"] = (pr, os.path.normcase(pr), rd, os.path.normcase(rd))
        folds_case(os.path.normcase(pr))
        _install_redirect()
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


# --------------------------------------------------------------------------- 改道模式（校验运行）
def redirect_entries(project: str, directory: str) -> tuple[str, str]:
    """改道模式的两个 `write_roots` 条目：项目目录 → 改道区目录（改道区应在某个授权根之内）。"""
    return (REDIRECT_PROJECT_PREFIX + str(project), REDIRECT_DIR_PREFIX + str(directory))


def split_roots(entries) -> tuple[list[str], tuple[str, str] | None]:
    """`write_roots` → (真正的授权根, (项目, 改道区) | None)。只给一半是配置错误（ValueError）。"""
    roots: list[str] = []
    project = directory = None
    for e in entries:
        if e.startswith(REDIRECT_PROJECT_PREFIX):
            project = e[len(REDIRECT_PROJECT_PREFIX) :]
        elif e.startswith(REDIRECT_DIR_PREFIX):
            directory = e[len(REDIRECT_DIR_PREFIX) :]
        else:
            roots.append(e)
    if (project is None) != (directory is None) or (
        project is not None and not (project and directory)
    ):
        raise ValueError("改道模式需要成对的 redirect-project / redirect-dir 条目")
    return roots, (None if project is None else (project, directory))


_ORIG: dict = {}
_rtls = threading.local()
_MAX_REDIRECTED = 64


def _redirect_on() -> bool:
    return _state["armed"] and _state["redirect"] is not None and not getattr(_rtls, "busy", False)


def _resolve(raw):
    """项目内的路径 → `(真实路径, 白名单键, 改道区里的对应路径)`；不在项目内 / 本来就在改道区 / 路径无法解释 → None。无副作用。"""
    if isinstance(raw, int) or not isinstance(raw, (str, bytes, os.PathLike)):
        return None
    prev = getattr(_rtls, "busy", False)
    _rtls.busy = True
    try:
        text = os.fsdecode(os.fspath(raw))
        project, project_n, rdir, rdir_n = _state["redirect"]
        rp = os.path.realpath(text)
        rn = os.path.normcase(rp)
        if _under(rn, [rdir_n]) or not _under(rn, [project_n]):
            return None
        rel = os.path.relpath(rp, project)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None
        target = rdir if rel == "." else os.path.join(rdir, rel)
        return rp, os.path.normcase(rel), target
    except (OSError, ValueError):
        return None
    finally:
        _rtls.busy = prev


def _white(key: str) -> bool:
    return key in _state["whiteouts"]


def _prepare_write(r) -> None:
    """改道写之前：项目里的父目录真实存在时，补建改道区里的父目录（保住 `FileNotFoundError` 的原有语义）。"""
    rp, key, target = r
    prev = getattr(_rtls, "busy", False)
    _rtls.busy = True
    try:
        parent = os.path.dirname(target)
        if not _ORIG["isdir"](parent) and _ORIG["isdir"](os.path.dirname(rp)):
            _ORIG["makedirs"](parent, exist_ok=True)
        items = _state.setdefault("redirected", [])
        rel = os.path.relpath(rp, _state["redirect"][0]).replace(os.sep, "/")
        if len(items) < _MAX_REDIRECTED and rel not in items:
            items.append(rel)
    except OSError:
        pass
    finally:
        _rtls.busy = prev


def _map(raw, *, write: bool) -> str | None:
    """项目内的路径 → 改道区里的对应路径；不在项目内 / 本来就在改道区 / 路径无法解释 → None。

    `write=True` 返回无条件的改道路径（并补建父目录）；`write=False`（读 / 查询）：改道区已有该文件、或该路径已被
    白名单标成“删除过”时返回改道路径（后者在改道区里不存在，所以读它得到 `FileNotFoundError` / `exists → False`），否则 None。"""
    r = _resolve(raw)
    if r is None:
        return None
    if write:
        _prepare_write(r)
        return r[2]
    if _white(r[1]):
        return r[2]
    prev = getattr(_rtls, "busy", False)
    _rtls.busy = True
    try:
        return r[2] if _ORIG["lexists"](r[2]) else None
    finally:
        _rtls.busy = prev


def redirected() -> list[str]:
    """本窗口里被改道的项目相对路径（至多 `_MAX_REDIRECTED` 条，诊断 / 用例用）。"""
    return list(_state.get("redirected", ()))


def whiteouts() -> list[str]:
    """本窗口里被“删除 / 改名走”的项目相对路径（用例用）。"""
    return sorted(_state["whiteouts"])


def _real_exists(raw) -> bool:
    prev = getattr(_rtls, "busy", False)
    _rtls.busy = True
    try:
        return _ORIG["lexists"](os.fsdecode(os.fspath(raw)))
    except (OSError, ValueError, TypeError):
        return False
    finally:
        _rtls.busy = prev


def _cow(raw, target: str, *, empty: bool = False) -> None:
    """改动已存在的项目文件前，把原文件复制进改道区（项目里的那份不动）；`empty=True` 只物化一个空文件（O_TRUNC）。"""
    prev = getattr(_rtls, "busy", False)
    _rtls.busy = True
    try:
        if not _ORIG["lexists"](target) and _ORIG["isfile"](os.fsdecode(os.fspath(raw))):
            if empty:
                with _ORIG["open"](target, "wb"):
                    pass
            else:
                _ORIG["copyfile"](os.fsdecode(os.fspath(raw)), target)
    finally:
        _rtls.busy = prev


def _eisdir(path) -> IsADirectoryError:
    return IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path)


def _proj_dir(r) -> bool:
    """项目里这个路径当前是不是目录（改道层不模拟目录级的改动：见模块头的“失败即关闭”）。"""
    return bool(_ORIG["isdir"](r[0]))


def _enoent(path) -> FileNotFoundError:
    return FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)


def _w_open(orig):
    @functools.wraps(orig)
    def open_(file, mode="r", *args, **kwargs):
        if _redirect_on():
            m = mode if isinstance(mode, str) else "r"
            if any(c in m for c in "wax+"):
                r = _resolve(file)
                if r is not None:
                    white = _white(r[1])
                    creates = any(c in m for c in "wax")
                    if _proj_dir(r) and not _real_exists(r[2]):
                        raise _eisdir(
                            file
                        )  # 项目里这里是目录：真实语义就是 IsADirectoryError，不改道成普通文件
                    if "x" in m and not white and _real_exists(file):
                        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), file)
                    if white and not creates:
                        file = r[
                            2
                        ]  # 已删除的文件不能读写打开（r+）：改道区里没有它 → FileNotFoundError
                    else:
                        _prepare_write(r)
                        if "w" not in m and "x" not in m and not white:
                            _cow(file, r[2])
                        if creates:
                            _state["whiteouts"].discard(r[1])
                        file = r[2]
            else:
                target = _map(file, write=False)
                if target is not None:
                    file = target
        return orig(file, mode, *args, **kwargs)

    return open_


def _w_os_open(orig):
    @functools.wraps(orig)
    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            if flags & _WRITE_FLAGS:
                r = _resolve(path)
                if r is not None:
                    white = _white(r[1])
                    creates = bool(flags & os.O_CREAT)
                    if _proj_dir(r) and not _real_exists(r[2]):
                        raise _eisdir(path)
                    if creates and flags & os.O_EXCL and not white and _real_exists(path):
                        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
                    if white and not creates:
                        path = r[2]  # 已删除：没有 O_CREAT 就打不开
                    else:
                        _prepare_write(r)
                        if not white:
                            # 项目里有而改道区没有：先物化（O_TRUNC 只要空文件，其余写时复制），没有 O_CREAT 也能打开
                            _cow(path, r[2], empty=bool(flags & os.O_TRUNC))
                        if creates:
                            _state["whiteouts"].discard(r[1])
                        path = r[2]
            else:
                target = _map(path, write=False)
                if target is not None:
                    path = target
        return orig(path, flags, mode, dir_fd=dir_fd)

    return os_open


def _w_mkdir(orig):
    @functools.wraps(orig)
    def mkdir(path, mode=0o777, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            if _real_exists(path):
                r = _resolve(path)
                if r is None or not _white(r[1]):
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
                _violation("os.mkdir", r[0])  # 被“删掉”的文件处建目录：不模拟
            target = _map(path, write=True)
            if target is not None:
                path = target
        return orig(path, mode, dir_fd=dir_fd)

    return mkdir


def _w_remove(orig):
    """remove / unlink：项目里的文件“删掉”= 删改道区里的副本 + 白名单标记，项目里的那份不动（之后读它 / 查它都当不存在）。"""

    @functools.wraps(orig)
    def fn(path, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            r = _resolve(path)
            if r is not None:
                rp, key, target = r
                if _white(key):
                    raise _enoent(path)
                over = _real_exists(target)
                real = _real_exists(rp)
                if real and _ORIG["isdir"](rp):
                    _violation("os.remove", rp)
                if over:
                    orig(target)
                    if real:
                        _state["whiteouts"].add(key)
                    return None
                if real:
                    _state["whiteouts"].add(key)
                    return None
                raise _enoent(path)
        return orig(path, dir_fd=dir_fd)

    return fn


def _w_rmdir(orig):
    """rmdir：只删**纯暂存**的目录（项目里没有同名目录）；项目里有目录就不改道（走绊线 → 失败），
    否则删掉暂存目录后，项目里的原目录会重新露出来。"""

    @functools.wraps(orig)
    def fn(path, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            r = _resolve(path)
            if r is not None:
                if _proj_dir(r):
                    _violation("os.rmdir", r[0])  # 显式失败：不依赖后面的删除账本去“跳过”它
                if _real_exists(r[2]):
                    path = r[2]
        return orig(path, dir_fd=dir_fd)

    return fn


def _w_rename(orig):
    @functools.wraps(orig)
    def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None and dst_dir_fd is None and _redirect_on():
            rs = _resolve(src)
            rd = _resolve(dst)
            if rd is not None and _proj_dir(rd):
                # 目的地在项目里是目录：文件改名到目录上是 IsADirectoryError；目录对目录要合并 / 替换，不模拟 → 走绊线
                src_is_dir = (rs is not None and _ORIG["isdir"](rs[2])) or (
                    rs is None and _ORIG["isdir"](os.fsdecode(os.fspath(src)))
                )
                if not src_is_dir:
                    raise _eisdir(dst)
                _violation("os.rename", rd[0])
            if (
                rd is not None
                and _real_exists(rd[0])
                and (
                    (rs is not None and _ORIG["isdir"](rs[2]))
                    or (rs is None and _ORIG["isdir"](os.fsdecode(os.fspath(src))))
                )
            ):
                _violation("os.rename", rd[0])  # 目录改名到项目里已有的文件上：不模拟
            if rd is not None:
                _prepare_write(rd)
            d = rd[2] if rd is not None else dst
            done = False
            if rs is not None:
                rp, key, target = rs
                if _white(key):
                    raise _enoent(src)
                over = _real_exists(target)
                real = _real_exists(rp)
                if real and _ORIG["isdir"](rp):
                    _violation("os.rename", rp)  # 项目里的目录被改名走：不模拟
                elif over:
                    orig(target, d)
                    if real:
                        _state["whiteouts"].add(key)
                    done = True
                elif real:
                    # 只存在于项目里的文件被改名走：内容复制到目的地，项目里的原件不动，源路径标成已删除
                    prev = getattr(_rtls, "busy", False)
                    _rtls.busy = True
                    try:
                        _ORIG["copyfile"](rp, d)
                    finally:
                        _rtls.busy = prev
                    _state["whiteouts"].add(key)
                    done = True
                else:
                    raise _enoent(src)
            if not done:
                orig(src, d)
            if rd is not None:
                _state["whiteouts"].discard(rd[1])
            return None
        return orig(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    return rename


def _w_exists(orig):
    @functools.wraps(orig)
    def fn(path, *args, **kwargs):
        result = orig(path, *args, **kwargs)
        if not _redirect_on():
            return result
        if result:
            if _state["whiteouts"]:
                r = _resolve(path)
                if r is not None and _white(r[1]):
                    return False
            return result
        target = _map(path, write=False)
        return orig(target, *args, **kwargs) if target is not None else result

    return fn


def _w_getsize(orig):
    @functools.wraps(orig)
    def getsize(path):
        if _redirect_on():
            target = _map(path, write=False)
            if target is not None:
                path = target
        return orig(path)

    return getsize


def _w_copyfile(orig):
    """copyfile：src 读时看改道区，dst 是写（项目里 dst 是目录 → IsADirectoryError）。"""

    @functools.wraps(orig)
    def fn(src, dst, *args, **kwargs):
        if _redirect_on():
            s = _map(src, write=False)
            if s is not None:
                src = s
            rd = _resolve(dst)
            if rd is not None:
                if _proj_dir(rd) and not _real_exists(rd[2]):
                    raise _eisdir(dst)
                _prepare_write(rd)
                _state["whiteouts"].discard(rd[1])
                dst = rd[2]
        return orig(src, dst, *args, **kwargs)

    return fn


def _w_copyattr(orig):
    """copymode / copystat：dst 必须是已有对象。改道区里有它就改它；只存在于项目里就不改道（走绊线 → 失败）。"""

    @functools.wraps(orig)
    def fn(src, dst, *args, **kwargs):
        if _redirect_on():
            s = _map(src, write=False)
            if s is not None:
                src = s
            rd = _resolve(dst)
            if rd is not None and not _proj_dir(rd) and _real_exists(rd[2]):
                dst = rd[2]
        return orig(src, dst, *args, **kwargs)

    return fn


def _w_copytree(orig):
    @functools.wraps(orig)
    def copytree(src, dst, *args, **kwargs):
        if _redirect_on():
            rsrc = _resolve(src)
            if rsrc is not None and _real_exists(rsrc[2]):
                if _real_exists(rsrc[0]):
                    # 源目录一半在项目里、一半在暂存区：合并视图不模拟 → 失败即关闭
                    _violation("shutil.copytree", rsrc[0])
                src = rsrc[2]
            rd = _resolve(dst)
            if rd is not None:
                if _real_exists(rd[0]) and not _white(rd[1]):
                    if kwargs.get("dirs_exist_ok") or (len(args) >= 5 and args[4]):
                        _violation("shutil.copytree", rd[0])  # 合并进项目里已有的目录：不模拟
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
                dst = _map(dst, write=True)
        return orig(src, dst, *args, **kwargs)

    return copytree


def _w_rmtree(orig):
    """rmtree：只删纯暂存的树；项目里有这个路径（目录或文件）就不改道（走绊线 → 失败），
    否则删掉暂存副本后项目里的原内容会重新露出来。"""

    @functools.wraps(orig)
    def rmtree(path, *args, **kwargs):
        if _redirect_on():
            r = _resolve(path)
            if r is not None:
                if _real_exists(r[0]):
                    _violation("shutil.rmtree", r[0])
                if _real_exists(r[2]):
                    path = r[2]
        return orig(path, *args, **kwargs)

    return rmtree


def _w_move(orig):
    @functools.wraps(orig)
    def move(src, dst, *args, **kwargs):
        if not _redirect_on():
            return orig(src, dst, *args, **kwargs)
        rs = _resolve(src)
        rd = _resolve(dst)
        overlay_src = False
        if rs is not None:
            if _white(rs[1]):
                raise _enoent(src)
            if _real_exists(rs[2]):
                src, overlay_src = rs[2], True
        if rd is not None:
            _prepare_write(rd)
            # 项目里的 dst 是已有目录时 shutil.move 是「移进去」：改道区里补出同名目录，语义保持
            if _real_exists(dst) and _ORIG["isdir"](os.fsdecode(os.fspath(dst))):
                inner = os.path.join(
                    os.fsdecode(os.fspath(dst)), os.path.basename(os.fsdecode(os.fspath(src)))
                )
                if _real_exists(inner):
                    _violation("shutil.move", rd[0])  # 移进目录时同名项已在项目里：不模拟
                _ORIG["makedirs"](rd[2], exist_ok=True)
            dst = rd[2]
        result = orig(src, dst, *args, **kwargs)
        if overlay_src and _real_exists(rs[0]):
            _state["whiteouts"].add(rs[1])  # 改道区里的副本被移走了，项目里的原件不能重新露出来
        if rd is not None:
            _state["whiteouts"].discard(rd[1])
        return result

    return move


def _install_redirect() -> None:
    """包装 Python 层写入口（只装一次；窗口外 / 没配改道区时每个包装都是直通）。"""
    if _state["redirect_installed"]:
        return
    path = os.path
    _ORIG.update(
        open=builtins.open,
        isdir=path.isdir,
        isfile=path.isfile,
        lexists=path.lexists,
        makedirs=os.makedirs,
        copyfile=shutil.copyfile,
    )
    wrapped_open = _w_open(builtins.open)
    builtins.open = wrapped_open
    io.open = wrapped_open
    os.open = _w_os_open(os.open)
    os.mkdir = _w_mkdir(os.mkdir)
    os.remove = _w_remove(os.remove)
    os.unlink = _w_remove(os.unlink)
    os.rmdir = _w_rmdir(os.rmdir)
    os.rename = _w_rename(os.rename)
    os.replace = _w_rename(os.replace)
    for name in ("exists", "isfile", "isdir"):
        setattr(path, name, _w_exists(getattr(path, name)))
    path.getsize = _w_getsize(path.getsize)
    shutil.copyfile = _w_copyfile(shutil.copyfile)
    shutil.copymode = _w_copyattr(shutil.copymode)
    shutil.copystat = _w_copyattr(shutil.copystat)
    shutil.copytree = _w_copytree(shutil.copytree)
    shutil.rmtree = _w_rmtree(shutil.rmtree)
    shutil.move = _w_move(shutil.move)
    _state["redirect_installed"] = True
