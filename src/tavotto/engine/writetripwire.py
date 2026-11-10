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

**暂存覆盖层（#1813，ADR 0127 修订 2026-11-09，`install_overlay`）**：同一套改道视图的另一个用法——safe worker 的常规运行里，守卫原本静默跳过的
项目内 `Path.write_text` / 改名 / 删除改道进本次运行的暂存区，读回看到一致的视图；覆盖层模拟不了的情形**交回原守卫**（不是 `write_outside_roots`）。
细节见 `install_overlay` 的文档与 `docs/rules/backend/worker-protocol-and-lifecycle.md`。

**改道模式（ADR 0143 修订 2026-10-30，只属于 AI 转换 / 改图的校验运行）**：`write_roots` 里带一对 `redirect-project:` /
`redirect-dir:` 条目（`redirect_entries`）时，同一个窗口里**落在项目内的写**不再失败，而是被换到 `<redirect-dir>/<相对项目路径>`——
候选脚本保留「在数据旁写 CSV / 图」的原样行为，校验照样通过，用户项目文件逐字节不变。机制是在 `install` 时包装 Python 层入口
（`builtins.open` / `io.open` / `os.open` / `os.mkdir|rmdir|remove|unlink|rename|replace` / `shutil.copyfile|copymode|copystat|copytree|rmtree|move` /
`os.path.exists|isfile|isdir|getsize`），**只在 `arm(True)` 窗口里、只对落在项目内的路径**生效：换完路径再交给原函数，
审计钩子看到的已经是候选目录里的路径。项目外、也不在白名单的写照旧是 `write_outside_roots`（改道只认项目这一个前缀）。
读：打开读取时，项目内路径若在改道区有同名文件就读改道区的（写完再读回来可行）；`os.path.exists|isfile|isdir|getsize` 和
`Path.exists|is_file|is_dir` 同理。`follow_symlinks=False` 的查询保留链接本身的语义。
改道**不**覆盖（限制，不是缺陷）：`os.stat` / `os.listdir` / `os.scandir` / `glob` / `Path.stat|iterdir`（读不到改道区的文件），
`os.symlink|link|truncate`（仍走绊线 → 失败），C 层写（同下面的盲区：绊线看不见，由校验后的项目指纹复核兜住 → 失败）。
改动已存在的项目文件（追加 / 读写模式）：先把原文件复制进改道区再改（写时复制），项目里的那份不动；
**白名单（whiteout）**：脚本通过改道层删除 / 改名走一个项目路径（含只存在于项目里的用户文件——项目里的原件不动），该路径记进
本窗口的白名单集合（worker 内存里，`install` 时清空；校验 worker 每次运行后退役，所以每次执行都从空集合开始），之后所有被包装的读 / 查询
（`open` 读、`os.open`、`os.path.exists|isfile|isdir|getsize`、`Path.exists|is_file|is_dir`、`shutil.copyfile` 的 src）都把它当不存在；再次创建 / 写该路径会清掉标记。
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
未包装、所以看不到暂存区的**读**（只能文档化，没法失败）：`os.stat|listdir|scandir`、`glob`、`Path.stat|iterdir`。

**隔离重放（QA-WIN-51，只属于写回校验）**：`install_isolation(暂存目录, 写回目标们)` 复用上面这套包装，但只改道那几个精确文件，
没有授权根绊线；审计钩子只盯这些目标，改道层不能忠实模拟的写（`os.truncate|symlink|link`、目录级改动）在落到原件之前失败。
条目 `isolate-dir:` / `isolate-file:` 同样放进 `write_roots`（`isolate_entries` / `split_isolation`），不与授权根混用。

纯标准库、**不 import 任何兄弟模块**：它装进 worker 的私有包（`worker._ENGINE_MODULES`）。
"""

from __future__ import annotations

import builtins
import errno
import functools
import io
import os
import pathlib
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
    # Windows、CPython 3.12+：`shutil.copy2` 直接调 `_winapi.CopyFile2(src, dst, flags)`，不发 `shutil.copyfile` 事件。
    # 该事件是否存在、参数顺序是否 (src, dst, ...) 凭 CPython 源码记忆，**未在 Windows 上验证**；事件不存在则这一行无副作用。
    "_winapi.CopyFile2": ((1, None),),
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
#: 隔离重放模式（写回校验用，见 `install_isolation`）：一个暂存目录 + 若干个「写回目标文件」。同样放进 `write_roots` 元组随现有通道走。
ISOLATE_DIR_PREFIX = "isolate-dir:"
ISOLATE_FILE_PREFIX = "isolate-file:"

_state: dict = {
    "only": None,  # 隔离模式：{目标 normcase 真实路径: 目标真实路径}；None = 普通（项目级）改道
    "only_fold": {},  # 隔离模式里目录不区分大小写的目标：小写路径 → 上面的键
    "redirect": None,  # (项目真实路径, 项目 normcase 路径, 改道区真实路径, 改道区 normcase 路径)
    "redirect_installed": False,
    "overlay": False,  # 暂存覆盖层模式（`install_overlay`）：safe worker 常规运行里项目内的写 / 改名 / 删除落进本次运行的覆盖层
    "overlay_installed": False,
    "overlay_armed": False,  # 覆盖层的窗口开关（与绊线的 `armed` 分开：覆盖层模式不装审计钩子，不能借 `armed` 去开它）
    "touched": False,  # 覆盖层里此刻有没有任何东西（写过 / 白名单）：没有就读路径零开销
    "suspended": None,  # 覆盖层的「暂停」谓词（账本自己在记账 / 解析回退路径时为真：那时的探测要看真实的文件系统）
    "on_handled": None,  # 覆盖层成功接住一次操作后的回调 `(op, kind, paths, dests)`（worker 用它给守卫账本记一笔）
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
            if _state.get("only") is not None:
                # 隔离重放：只有写回目标本身算越界（能改道的写到这里时路径已经是暂存区里的）
                real = _norm(raw)
                if _match_target(real) is not None:
                    _violation(event, real)
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
    _state["only"] = None
    _state["only_fold"] = {}
    _state["overlay"] = False
    _state["touched"] = False
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


def install_isolation(directory: str, files, *, display_base: str = "") -> None:
    """隔离重放（写回校验）：脚本对 `files`（写回目标，项目里的原件）的**写**换到 `directory` 里的副本，别的写原样。

    与项目级改道（`install(redirect=…)`）共用同一套包装（写时复制 / 读回 / 白名单 / 失败即关闭），区别只有两点：

    * 只改道 `files` 这一小撮**精确路径**（规范化真实路径比较；不区分大小写的卷按小写折叠），项目里其余文件的写行为一字不变，
      也**没有**授权根绊线（不会把别的写变成 `write_outside_roots`）；
    * 审计钩子只盯这些目标：改道层没法忠实模拟的写（`os.truncate|symlink|link`、目录级改动……）落到原件之前就在审计事件里失败
      （`write_outside_roots`），写回宁可整体失败也不让重放改动原件。C 层写 / 外部进程 / 硬链接别名看不见（同绊线盲区）。"""
    real_files: dict[str, str] = {}
    fold: dict[str, str] = {}
    for f in files:
        rp = os.path.realpath(os.fspath(f))
        key = os.path.normcase(rp)
        real_files[key] = rp
        if folds_case(os.path.dirname(key)):
            fold[key.lower()] = key
    if not real_files:
        raise ValueError("隔离重放需要至少一个目标文件")
    dirs = {os.path.dirname(rp) for rp in real_files.values()}
    try:
        project = os.path.commonpath(sorted(dirs))
    except ValueError as exc:  # 跨盘符：没有共同父目录
        raise ValueError("隔离重放的目标必须在同一个盘上") from exc
    _state["roots"] = ()
    _state["display_base"] = _real(display_base) if display_base else ""
    _state["violations"] = []
    _state["redirected"] = []
    _state["whiteouts"] = set()
    os.makedirs(directory, exist_ok=True)
    rd = os.path.realpath(directory)
    pr = os.path.realpath(project)
    _state["redirect"] = (pr, os.path.normcase(pr), rd, os.path.normcase(rd))
    _state["only"] = real_files
    _state["only_fold"] = fold
    _state["overlay"] = False
    _state["touched"] = False
    folds_case(os.path.normcase(pr))
    _install_redirect()
    if not _state["installed"]:
        sys.addaudithook(_hook)
        _state["installed"] = True


def isolate_entries(directory: str, files) -> tuple[str, ...]:
    """隔离重放的 `write_roots` 条目：一个暂存目录 + 每个写回目标一条。"""
    return (ISOLATE_DIR_PREFIX + str(directory), *(ISOLATE_FILE_PREFIX + str(f) for f in files))


def split_isolation(entries) -> tuple[list[str], tuple[str, list[str]] | None]:
    """`write_roots` → (其余条目, (暂存目录, 目标文件们) | None)。有文件没目录（或反之）是配置错误（ValueError）。"""
    rest: list[str] = []
    directory = None
    files: list[str] = []
    for e in entries:
        if e.startswith(ISOLATE_DIR_PREFIX):
            directory = e[len(ISOLATE_DIR_PREFIX) :]
        elif e.startswith(ISOLATE_FILE_PREFIX):
            files.append(e[len(ISOLATE_FILE_PREFIX) :])
        else:
            rest.append(e)
    if (directory is None) != (not files) or (directory is not None and not directory):
        raise ValueError("隔离重放需要一个 isolate-dir 和至少一个 isolate-file 条目")
    return rest, (None if directory is None else (directory, files))


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
    on = _state["armed"] or (_state["overlay"] and _state["overlay_armed"])
    if not on or _state["redirect"] is None or getattr(_rtls, "busy", False):
        return False
    suspended = _state["suspended"]
    return not (_state["overlay"] and suspended is not None and suspended())


class _Unsupported(Exception):
    """覆盖层模式：这次操作覆盖层无法忠实模拟——交回原来的守卫（跳过 + 记账），行为与没有覆盖层时一字不差。"""


def _unsupported(event: str, real: str) -> None:
    """改道层「失败即关闭」的出口。校验 / 隔离重放里是 `write_outside_roots`；覆盖层里是交回守卫。"""
    if _state["overlay"]:
        raise _Unsupported(event)
    _violation(event, real)


def in_internal() -> bool:
    """此刻是不是本模块自己在探测文件系统（账本据此不把引擎的探测当成脚本的观察）。"""
    return bool(getattr(_rtls, "busy", False))


def _ov_view() -> bool:
    """覆盖层模式下读路径要不要查覆盖层：窗口开着、覆盖层里有东西、且不是本模块内部的探测。"""
    return _state["overlay"] and _state["touched"] and _redirect_on()


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
        if _state.get("only") is not None:
            # 隔离重放：只认写回目标（精确文件）；键与路径都取登记时的那一份，大小写不同的写法落到同一个暂存副本
            key = _match_target(rn)
            if key is None:
                return None
            rp = _state["only"][key]
            rel = os.path.relpath(rp, project)
            return rp, key, os.path.join(rdir, rel)
        rel = os.path.relpath(rp, project)
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return None
        key = os.path.normcase(rel)
        if _state["overlay"] and folds_case(project_n):
            # 不区分大小写的卷（Windows / macOS 默认卷）：`Final.CSV` 与 `final.csv` 是同一个文件，键与暂存路径都折成小写
            key = rel = key.lower()
        target = rdir if rel == "." else os.path.join(rdir, rel)
        return rp, key, target
    except (OSError, ValueError):
        return None
    finally:
        _rtls.busy = prev


def _match_target(rn: str) -> str | None:
    """隔离模式：规范化路径 `rn` 是不是写回目标；是就回目标的规范键。不区分大小写的卷按小写折叠。"""
    only = _state.get("only")
    if only is None:
        return None
    if rn in only:
        return rn
    return _state.get("only_fold", {}).get(rn.lower())


def _white(key: str) -> bool:
    return key in _state["whiteouts"]


def _whiteout(key: str) -> None:
    _state["touched"] = True
    _state["whiteouts"].add(key)


def _note(op: str, kind: str, *paths, dests=()) -> None:
    """覆盖层成功接住了一次操作：交给 worker 的回调（守卫账本记它影响的路径，覆盖层够不到的观察仍会命中）。永不抛。"""
    cb = _state["on_handled"]
    if cb is None or not _state["overlay"]:
        return
    try:
        cb(op, kind, tuple(paths), tuple(dests))
    except Exception:  # noqa: BLE001 —— 记账失败就少一条，不影响脚本
        pass


def _ov_has(r) -> bool:
    """覆盖层里这个项目路径此刻有自己的状态（写过 / 被删过）：这时真实的项目路径不再是脚本该看到的那份。"""
    return _white(r[1]) or _real_exists(r[2])


def _trip_if_project_link(op: str, raw) -> None:
    """删除 / 改名 / 移走作用在**链接本身**（最后一段不跟随）：`_resolve` 走 realpath 会落到链接目标上，
    把「删别名」模拟成「删目标」。项目里的符号链接不模拟，失败即关闭。"""
    if isinstance(raw, int) or not isinstance(raw, (str, bytes, os.PathLike)):
        return
    if _state.get("only") is not None and _resolve(raw) is None:
        return  # 隔离重放：只有指向写回目标的链接才算
    try:
        lexical = os.path.abspath(os.fsdecode(os.fspath(raw)))
        inside = _ORIG["islink"](lexical) and _under(
            os.path.normcase(os.path.realpath(os.path.dirname(lexical))), [_state["redirect"][1]]
        )
    except (OSError, ValueError):
        return
    if inside:
        _unsupported(op, lexical)  # 在 try 外：它抛的 PermissionError 也是 OSError，不能被上面吞掉


def _prepare_write(r) -> None:
    """改道写之前：项目里的父目录真实存在时，补建改道区里的父目录（保住 `FileNotFoundError` 的原有语义）。"""
    rp, key, target = r
    _state["touched"] = True
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
        if _state["overlay"]:
            raise _enoent(raw)  # 脚本自己删掉的文件：与直接运行一样落空（且不记成「缺输入」）
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


def _open_target(file, mode):
    """`open(file, mode)` 实际该打开哪条路径（改道 / 覆盖层之后）；写时复制与已删除的语义都在这里。"""
    m = mode if isinstance(mode, str) else "r"
    if any(c in m for c in "wax+"):
        r = _resolve(file)
        if r is not None:
            if _state["overlay"] and not _ov_has(r):
                # 覆盖层没接管过这条路径：`open('w')` 改写已有文件不拦的契约不变（改道只管覆盖层里已有状态的路径，
                # 免得写进真实文件之后读回的却是覆盖层里更早的那份）
                return file
            white = _white(r[1])
            creates = any(c in m for c in "wax")
            if _proj_dir(r) and not _real_exists(r[2]):
                raise _eisdir(
                    file
                )  # 项目里这里是目录：真实语义就是 IsADirectoryError，不改道成普通文件
            if "x" in m and not white and _real_exists(file):
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), file)
            if white and not creates:
                # 已删除的文件不能读写打开（r+）：改道区里没有它 → FileNotFoundError
                if _state["overlay"]:
                    raise _enoent(file)
                return r[2]
            _prepare_write(r)
            if "w" not in m and "x" not in m and not white:
                _cow(file, r[2])
            if creates:
                _state["whiteouts"].discard(r[1])
            return r[2]
        return file
    if _state["overlay"] and not _ov_view():
        return file
    target = _map(file, write=False)
    return file if target is None else target


def _w_open(orig):
    @functools.wraps(orig)
    def open_(file, mode="r", *args, **kwargs):
        if _redirect_on():
            file = _open_target(file, mode)
        return orig(file, mode, *args, **kwargs)

    return open_


def _w_path_open(orig):
    """`Path.open`（含 `read_text` / `read_bytes` 与 3.10 的 `write_text`）：账本把观察装在 `Path.open` 上，
    不在这里先换成覆盖层里的路径，账本看到的就是项目里那条（脚本读回覆盖层里写的东西被当成观察）。"""

    @functools.wraps(orig)
    def open_(self, mode="r", *args, **kwargs):
        if _redirect_on() and kwargs.get("opener") is None:
            target = _open_target(self, mode)
            if target is not self:
                return orig(pathlib.Path(target), mode, *args, **kwargs)
        return orig(self, mode, *args, **kwargs)

    return open_


def _w_os_open(orig):
    @functools.wraps(orig)
    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            if flags & _WRITE_FLAGS:
                r = _resolve(path)
                if r is not None and _state["overlay"] and not _ov_has(r):
                    r = None  # 覆盖层没接管过这条路径：写照旧落在项目里（与 `open('w')` 同一个契约）
                if r is not None:
                    white = _white(r[1])
                    creates = bool(flags & os.O_CREAT)
                    if _proj_dir(r) and not _real_exists(r[2]):
                        raise _eisdir(path)
                    if creates and flags & os.O_EXCL and not white and _real_exists(path):
                        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), path)
                    if white and not creates:
                        if _state["overlay"]:
                            raise _enoent(path)
                        path = r[2]  # 已删除：没有 O_CREAT 就打不开
                    else:
                        _prepare_write(r)
                        if not white:
                            # 项目里有而改道区没有：先物化（O_TRUNC 只要空文件，其余写时复制），没有 O_CREAT 也能打开
                            _cow(path, r[2], empty=bool(flags & os.O_TRUNC))
                        if creates:
                            _state["whiteouts"].discard(r[1])
                        path = r[2]
            elif not _state["overlay"] or _ov_view():
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
                _unsupported("os.mkdir", r[0])  # 被“删掉”的文件处建目录：不模拟
            target = _map(path, write=True)
            if target is not None:
                path = target
        return orig(path, mode, dir_fd=dir_fd)

    return mkdir


def _w_remove(orig, op="os.remove"):
    """remove / unlink：项目里的文件“删掉”= 删改道区里的副本 + 白名单标记，项目里的那份不动（之后读它 / 查它都当不存在）。"""

    @functools.wraps(orig)
    def fn(path, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            _trip_if_project_link("os.remove", path)
            r = _resolve(path)
            if r is not None:
                rp, key, target = r
                if _white(key):
                    raise _enoent(path)
                over = _real_exists(target)
                real = _real_exists(rp)
                if real and _ORIG["isdir"](rp):
                    _unsupported("os.remove", rp)
                if over:
                    orig(target)
                    if real or _state["overlay"]:
                        _whiteout(
                            key
                        )  # 覆盖层里：连只存在于覆盖层的路径也记「已删」，之后的探路由覆盖层作答
                    _note(op, "delete", rp)
                    return None
                if real:
                    _whiteout(key)
                    _note(op, "delete", rp)
                    return None
                if _state["overlay"]:
                    raise _Unsupported(op)  # 哪里都没有它：交回守卫（与没有覆盖层时一样跳过并记账）
                raise _enoent(path)
        return orig(path, dir_fd=dir_fd)

    return fn


def _w_rmdir(orig):
    """rmdir：只删**纯暂存**的目录（项目里没有同名目录）；项目里有目录就不改道（走绊线 → 失败），
    否则删掉暂存目录后，项目里的原目录会重新露出来。"""

    @functools.wraps(orig)
    def fn(path, *, dir_fd=None):
        if dir_fd is None and _redirect_on():
            _trip_if_project_link("os.rmdir", path)
            r = _resolve(path)
            if r is not None:
                if _proj_dir(r):
                    _unsupported("os.rmdir", r[0])  # 显式失败：不依赖后面的删除账本去“跳过”它
                if _real_exists(r[2]):
                    path = r[2]
                    _note("os.rmdir", "delete_dir", r[0])
        return orig(path, dir_fd=dir_fd)

    return fn


def _rename_core(orig, op, src, dst):
    """改名 / 移动一个文件的改道逻辑（`os.rename|replace` 与覆盖层里的 `shutil.move` 共用）。

    覆盖层模式下覆盖层够不着的情形抛 `_Unsupported`，调用方交回守卫；其余模式下是 `write_outside_roots`。"""
    _trip_if_project_link("os.rename", src)
    _trip_if_project_link(
        "os.rename", dst
    )  # 目标是链接时 rename 替换的是链接本身，不是它指向的文件
    rs = _resolve(src)
    rd = _resolve(dst)
    if _state["overlay"]:
        if rs is None and not (rd is not None and _ov_has(rd)):
            # 源不在项目里、目的地也没被覆盖层接管过：与没有覆盖层时一样直接做（不去改项目里别的东西）
            return orig(src, dst)
        if (
            op == "os.rename"
            and os.name == "nt"
            and rd is not None
            and not _white(rd[1])
            and (_real_exists(rd[2]) or _real_exists(rd[0]))
        ):
            # Windows 的 os.rename 目的地已存在就 FileExistsError（os.replace 才覆盖）
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), dst)
    if rd is not None and _proj_dir(rd):
        # 目的地在项目里是目录：文件改名到目录上是 IsADirectoryError；目录对目录要合并 / 替换，不模拟 → 走绊线
        src_is_dir = (rs is not None and _ORIG["isdir"](rs[2])) or (
            rs is None and _ORIG["isdir"](os.fsdecode(os.fspath(src)))
        )
        if not src_is_dir:
            raise _eisdir(dst)
        _unsupported("os.rename", rd[0])
    if (
        rd is not None
        and _real_exists(rd[0])
        and (
            (rs is not None and _ORIG["isdir"](rs[2]))
            or (rs is None and _ORIG["isdir"](os.fsdecode(os.fspath(src))))
        )
    ):
        _unsupported("os.rename", rd[0])  # 目录改名到项目里已有的文件上：不模拟
    if rs is not None and _state["overlay"] and not _white(rs[1]):
        if not _real_exists(rs[2]) and not _real_exists(rs[0]):
            raise _Unsupported(op)  # 源哪里都没有：交回守卫（与没有覆盖层时一样跳过并记账）
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
            _unsupported("os.rename", rp)  # 项目里的目录被改名走：不模拟
        elif over:
            orig(target, d)
            if real or _state["overlay"]:
                _whiteout(key)
            done = True
        elif real:
            # 只存在于项目里的文件被改名走：内容复制到目的地，项目里的原件不动，源路径标成已删除
            prev = getattr(_rtls, "busy", False)
            _rtls.busy = True
            try:
                _ORIG["copyfile"](rp, d)
            finally:
                _rtls.busy = prev
            _whiteout(key)
            done = True
        else:
            raise _enoent(src)
    if not done:
        orig(src, d)
    if rd is not None:
        _state["whiteouts"].discard(rd[1])
    if rs is not None or rd is not None:
        moved = tuple(r[0] for r in (rs, rd) if r is not None)
        _note(op, "rename", *moved, dests=(rd[0],) if rd is not None else ())
    return None


def _w_rename(orig, op="os.rename"):
    @functools.wraps(orig)
    def rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if src_dir_fd is None and dst_dir_fd is None and _redirect_on():
            return _rename_core(orig, op, src, dst)
        return orig(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    return rename


def _w_exists(orig):
    @functools.wraps(orig)
    def fn(path, *args, **kwargs):
        if _state["overlay"]:
            # 覆盖层模式：对**覆盖层里有状态的路径**，存在性探测仍照旧让账本看见（问一次项目里那条路径，命中就按老规矩记
            # material → 回退 native）。覆盖层只保证「读内容」的入口一致；探测说「在」之后脚本常去走 C 层读（h5py / sqlite…），
            # 那条路径覆盖层够不着，读到旧值或缺失就是静默错图——所以不能用覆盖层视图作答后当没事。答案本身仍取覆盖层视图。
            result = orig(path, *args, **kwargs)
            if _ov_view():
                r = _resolve(path)
                if r is not None:
                    if _white(r[1]):
                        return False
                    if _real_exists(r[2]):
                        return True
            return result
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


def _w_path_check(orig):
    """Make pathlib's boolean checks see the same overlay as open()."""

    @functools.wraps(orig)
    def fn(path, *args, **kwargs):
        if _state["overlay"]:
            result = orig(path, *args, **kwargs)  # 同 `_w_exists`：账本照旧看见对项目路径的探测
            if _ov_view() and not (
                kwargs.get("follow_symlinks") is False and _ORIG["islink"](path)
            ):
                r = _resolve(path)
                if r is not None:
                    if _white(r[1]):
                        return False
                    if _real_exists(r[2]):
                        return True
            return result
        result = orig(path, *args, **kwargs)
        if not _redirect_on():
            return result
        # A no-follow query concerns the link itself, not its redirected target.
        if kwargs.get("follow_symlinks") is False and _ORIG["islink"](path):
            return result
        r = _resolve(path)
        if r is None:
            return result
        if _white(r[1]):
            return False
        if result:
            return True
        return orig(pathlib.Path(r[2]), *args, **kwargs) if _real_exists(r[2]) else False

    return fn


def _w_getsize(orig):
    @functools.wraps(orig)
    def getsize(path):
        if _redirect_on() and not _state["overlay"]:
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
            rs, rd = _resolve(src), _resolve(dst)
            if rs is not None and rd is not None and rs[1] == rd[1]:
                # 源与目标是同一个项目文件：改道后两边成了不同路径，会绕过 copyfile 的同文件检查
                raise shutil.SameFileError(f"{src!r} and {dst!r} are the same file")
            s = _map(src, write=False)
            if s is not None:
                src = s
            if rd is not None:
                if _proj_dir(rd) and not _real_exists(rd[2]):
                    raise _eisdir(dst)
                _prepare_write(rd)
                _state["whiteouts"].discard(rd[1])
                dst = rd[2]
        return orig(src, dst, *args, **kwargs)

    return fn


def _w_copy2(orig):
    """copy2：CPython 3.12+ 在 Windows 上直接调 `_winapi.CopyFile2`（shutil.py 的 `copy2` 里 `hasattr(_winapi, "CopyFile2")`
    分支），不经过 `shutil.copyfile` / `shutil.copystat`，改道和同文件检查都会被绕过、直接写进项目文件。
    改道期间改走 `copy2` 的 Python 回退路径（POSIX 上本来就是它）：目录目的地展开 → `shutil.copyfile`（已包装）→
    `shutil.copystat`（已包装）。Windows 上真实的 `copy2(x, x)` 抛共享冲突之类的 OSError，这里抛 `SameFileError`（也是 OSError）。"""

    @functools.wraps(orig)
    def copy2(src, dst, *, follow_symlinks=True):
        if not _redirect_on():
            return orig(src, dst, follow_symlinks=follow_symlinks)
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))
        if _state.get("only") is not None and _resolve(src) is None and _resolve(dst) is None:
            return orig(
                src, dst, follow_symlinks=follow_symlinks
            )  # 隔离重放：与写回目标无关的拷贝原样
        shutil.copyfile(src, dst, follow_symlinks=follow_symlinks)
        shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
        return dst

    return copy2


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
                    _unsupported("shutil.copytree", rsrc[0])
                src = rsrc[2]
            rd = _resolve(dst)
            if rd is not None:
                if _real_exists(rd[0]) and not _white(rd[1]):
                    if kwargs.get("dirs_exist_ok") or (len(args) >= 5 and args[4]):
                        _unsupported("shutil.copytree", rd[0])  # 合并进项目里已有的目录：不模拟
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
            _trip_if_project_link("shutil.rmtree", path)
            r = _resolve(path)
            if r is not None:
                if _real_exists(r[0]):
                    _unsupported("shutil.rmtree", r[0])
                if _real_exists(r[2]):
                    path = r[2]
                    _note("shutil.rmtree", "delete_dir", r[0])
        return orig(path, *args, **kwargs)

    return rmtree


def _overlay_move(orig, src, dst, *args, **kwargs):
    """覆盖层里的 `shutil.move`：只模拟「文件改名」（含改名到另一个目录下），目录级的移动交回守卫。"""
    _trip_if_project_link("shutil.move", src)
    _trip_if_project_link("shutil.move", dst)
    rs = _resolve(src)
    rd = _resolve(dst)
    if rs is None and not (rd is not None and _ov_has(rd)):
        return orig(src, dst, *args, **kwargs)
    if args or kwargs:
        raise _Unsupported("shutil.move")  # copy_function 之类的自定义：不模拟
    src_s, dst_s = os.fsdecode(os.fspath(src)), os.fsdecode(os.fspath(dst))
    if rd is not None:
        if _proj_dir(rd) or (_real_exists(rd[2]) and _ORIG["isdir"](rd[2])):
            raise _Unsupported("shutil.move")  # 移进目录：不模拟
    elif _ORIG["isdir"](dst_s):
        dst_s = os.path.join(dst_s, os.path.basename(src_s.rstrip("/" + (os.sep or "/"))))
    if rs is not None and _real_exists(rs[0]) and _ORIG["isdir"](rs[0]):
        raise _Unsupported("shutil.move")  # 项目里的目录被整个移走：不模拟
    return _rename_core(_ORIG["replace"], "shutil.move", src_s, dst_s)


def _w_move(orig):
    @functools.wraps(orig)
    def move(src, dst, *args, **kwargs):
        if not _redirect_on():
            return orig(src, dst, *args, **kwargs)
        if _state["overlay"]:
            return _overlay_move(orig, src, dst, *args, **kwargs)
        _trip_if_project_link("shutil.move", src)
        _trip_if_project_link("shutil.move", dst)
        rs = _resolve(src)
        rd = _resolve(dst)
        overlay_src = False
        if rs is not None:
            if _white(rs[1]):
                raise _enoent(src)
            if _real_exists(rs[0]) and _ORIG["isdir"](rs[0]):
                # 项目里已有的目录被整个移走：改道区只有它的一部分（新写的文件），只移暂存副本会让
                # 原有文件留在原处、目标里缺一块——不模拟，与 rename / rmtree 同一条失败即关闭
                _unsupported("shutil.move", rs[0])
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
                    _unsupported("shutil.move", rd[0])  # 移进目录时同名项已在项目里：不模拟
                _ORIG["makedirs"](rd[2], exist_ok=True)
            dst = rd[2]
        result = orig(src, dst, *args, **kwargs)
        if overlay_src and _real_exists(rs[0]):
            _whiteout(rs[1])  # 改道区里的副本被移走了，项目里的原件不能重新露出来
        if rd is not None:
            _state["whiteouts"].discard(rd[1])
        return result

    return move


def _not_in_overlay(orig, wrapper):
    """覆盖层模式不接管的入口（mkdir / copy*）：直通，保持它们今天的行为（写照旧落在项目里）。"""

    @functools.wraps(orig)
    def fn(*args, **kwargs):
        if _state["overlay"]:
            return orig(*args, **kwargs)
        return wrapper(*args, **kwargs)

    return fn


def _quiet(fn):
    """本模块自己的探测：期间账本不记观察、改道包装直通。"""

    @functools.wraps(fn)
    def quiet(*args, **kwargs):
        prev = getattr(_rtls, "busy", False)
        _rtls.busy = True
        try:
            return fn(*args, **kwargs)
        finally:
            _rtls.busy = prev

    return quiet


def _capture_orig() -> None:
    path = os.path
    _ORIG.update(
        open=_quiet(builtins.open),
        isdir=_quiet(path.isdir),
        isfile=_quiet(path.isfile),
        islink=_quiet(path.islink),
        lexists=_quiet(path.lexists),
        makedirs=_quiet(os.makedirs),
        copyfile=_quiet(shutil.copyfile),
        replace=os.replace,
    )


def _install_redirect() -> None:
    """包装 Python 层写入口（只装一次；窗口外 / 没配改道区时每个包装都是直通）。"""
    if _state["redirect_installed"]:
        return
    path = os.path
    _capture_orig()
    wrapped_open = _w_open(builtins.open)
    builtins.open = wrapped_open
    io.open = wrapped_open
    os.open = _w_os_open(os.open)
    os.mkdir = _not_in_overlay(os.mkdir, _w_mkdir(os.mkdir))
    os.remove = _w_remove(os.remove)
    os.unlink = _w_remove(os.unlink, "os.unlink")
    os.rmdir = _w_rmdir(os.rmdir)
    os.rename = _w_rename(os.rename)
    os.replace = _w_rename(os.replace, "os.replace")
    for name in ("exists", "isfile", "isdir"):
        setattr(path, name, _w_exists(getattr(path, name)))
    for name in ("exists", "is_file", "is_dir"):
        setattr(pathlib.Path, name, _w_path_check(getattr(pathlib.Path, name)))
    path.getsize = _w_getsize(path.getsize)
    shutil.copyfile = _not_in_overlay(shutil.copyfile, _w_copyfile(shutil.copyfile))
    shutil.copy2 = _not_in_overlay(shutil.copy2, _w_copy2(shutil.copy2))
    shutil.copymode = _not_in_overlay(shutil.copymode, _w_copyattr(shutil.copymode))
    shutil.copystat = _not_in_overlay(shutil.copystat, _w_copyattr(shutil.copystat))
    shutil.copytree = _not_in_overlay(shutil.copytree, _w_copytree(shutil.copytree))
    shutil.rmtree = _w_rmtree(shutil.rmtree)
    shutil.move = _w_move(shutil.move)
    # Python < 3.11：pathlib 的 `_NormalAccessor` 在 import 时把 `io.open` / `os.mkdir|unlink|rmdir|rename|replace` 绑成类属性，
    # 上面对模块属性的替换到不了它们（Path.write_bytes / rename 会绕过改道，被绊线拦下）；3.11+ 的 pathlib 调用时才查 `os.*`。
    accessor = getattr(pathlib, "_NormalAccessor", None)
    if accessor is not None:
        for name, fn in (
            ("open", wrapped_open),
            ("mkdir", os.mkdir),
            ("unlink", os.unlink),
            ("rmdir", os.rmdir),
            ("rename", os.rename),
            ("replace", os.replace),
        ):
            if hasattr(accessor, name):
                setattr(accessor, name, staticmethod(fn))
    _state["redirect_installed"] = True


# --------------------------------------------------------------------------- 暂存覆盖层（safe worker 的常规运行，#1813）
def _w_write_text(orig):
    """`Path.write_text`：safe worker 的守卫原本把项目内的这类写直接跳过；覆盖层里它落进本次运行的暂存区，
    之后脚本读回（open / `Path.read_*` / `exists`）看到的就是它写的。覆盖层接不了的（目标是项目里的目录）交回守卫。"""

    @functools.wraps(orig)
    def write_text(self, data, *args, **kwargs):
        if _redirect_on():
            r = _resolve(self)
            if r is not None:
                if _proj_dir(r) and not _real_exists(r[2]):
                    raise _Unsupported("Path.write_text")
                _prepare_write(r)
                _state["whiteouts"].discard(r[1])
                try:
                    result = orig(pathlib.Path(r[2]), data, *args, **kwargs)
                except FileNotFoundError as exc:
                    # 项目里的父目录不存在：直接运行一样 FileNotFoundError，指的是脚本交出的路径，不是暂存区里的
                    raise FileNotFoundError(exc.errno, exc.strerror, os.fspath(self)) from None
                _note("Path.write_text", "write", r[0])
                return result
        return orig(self, data, *args, **kwargs)

    return write_text


def _w_path_unlink(orig):
    @functools.wraps(orig)
    def unlink(self, missing_ok=False):
        if _redirect_on():
            r = _resolve(self)
            if r is not None:
                _trip_if_project_link("Path.unlink", self)
                rp, key, target = r
                if _white(key):
                    if missing_ok:
                        return None
                    raise _enoent(self)
                over = _real_exists(target)
                real = _real_exists(rp)
                if (real and _ORIG["isdir"](rp)) or (over and _ORIG["isdir"](target)):
                    raise _Unsupported("Path.unlink")
                if over:
                    orig(pathlib.Path(target))
                    _whiteout(key)
                    _note("Path.unlink", "delete", rp)
                    return None
                if real:
                    _whiteout(key)
                    _note("Path.unlink", "delete", rp)
                    return None
                # 哪里都没有它：交给守卫（`missing_ok` 的空操作 / 没有 missing_ok 时跳过并记账，与没有覆盖层时一样）
        return orig(self, missing_ok=missing_ok)

    return unlink


def _w_datasource_open(orig):
    """numpy 的 `DataSource.open`（`np.loadtxt` / `genfromtxt` 经它）：它的打开器在 numpy 载入时就绑了原来的 `io.open`，
    我们换进去的 open 包装够不着，不在这里换路径它读的就是项目里没改过的那份。"""

    @functools.wraps(orig)
    def open_(self, path, mode="r", *args, **kwargs):
        if _redirect_on():
            path = _open_target(path, mode)
        return orig(self, path, mode, *args, **kwargs)

    return open_


def _ov(orig, fn):
    """覆盖层包装的统一出口：`fn` 抛 `_Unsupported` 就交回 `orig`（守卫：跳过并记账——与没有覆盖层时一字不差）。"""

    @functools.wraps(orig)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _Unsupported:
            return orig(*args, **kwargs)

    return wrapped


def install_overlay(project: str, directory: str, *, on_handled=None, suspended=None) -> None:
    """装暂存覆盖层（ADR 0063 之外的 safe worker 常规运行；#1813）。

    safe worker 的守卫把项目（真实图库）内的 `Path.write_text` / 改名 / 删除静默跳过，脚本随后读回受影响路径就是「看到的世界与
    直接运行不同」。覆盖层把这几类操作改道进 `directory`（本次运行自己的暂存区，运行结束丢弃），读路径（`open` / `io.open` /
    `Path.open|read_*` / `os.open` / numpy `DataSource.open`）
    看到覆盖后的视图；`exists|isfile|isdir|getsize` 等探测对覆盖层有状态的路径答覆盖层视图、但仍让账本看见（命中记 material，防 C 层读静默错图）；用户项目一个字节不改。与校验运行的改道层（ADR 0143）同一套视图实现，区别只有：

    * 只接管 `Path.write_text`、`os.remove|unlink|rmdir|rename|replace`、`Path.unlink`、`shutil.rmtree|move`；`open('w')` 改写已有文件
      不拦的契约不变（只有覆盖层里**已有状态**的路径才继续走覆盖层，免得读回更早的那份）；`mkdir` / `copy*` / `write_bytes` 原样；
    * 覆盖层模拟不了的情形（目录级改动、项目里的符号链接、哪里都没有的源）不失败，而是交回守卫——跳过并记账，行为与以前一字不差；
    * `suspended`：账本自己在记账 / 解析只读回退路径时为真，那时覆盖层让路（账本要对账的是**真实**文件系统上的路径）；
    * 覆盖层够不着的观察（`os.stat` / `listdir` / `scandir` / `glob` / C 层读 / 子进程）仍按守卫账本的老规矩命中：`on_handled`
      让 worker 把每次被覆盖层接住的操作也记进账本。

    不装审计钩子、没有授权根：只在 `arm(True)` 的窗口里、只对落在项目内的路径起作用。"""
    discard_overlay(directory)
    os.makedirs(directory, exist_ok=True)
    rd = os.path.realpath(directory)
    pr = os.path.realpath(project)
    _state["redirect"] = (pr, os.path.normcase(pr), rd, os.path.normcase(rd))
    _state["only"] = None
    _state["only_fold"] = {}
    _state["whiteouts"] = set()
    _state["redirected"] = []
    _state["overlay"] = True
    _state["touched"] = False
    _state["on_handled"] = on_handled
    _state["suspended"] = suspended
    folds_case(os.path.normcase(pr))
    _install_overlay_wrappers()


def discard_overlay(directory: str) -> None:
    """丢弃覆盖层的暂存区（每次运行开始前、结束后各一次）；目录本来就不在是正常的。**永不抛**：Windows 上脚本还开着
    暂存文件的句柄（全局 `f = p.open()`）时删不掉，那只是磁盘上多留一会儿——它在运行结束的 `finally` 里，抛出来会顶掉
    脚本真实的结局。每次运行用自己的新目录（worker 按运行取名），删不掉的旧目录读不进下一次运行，下次开跑前再清。"""
    shutil.rmtree(directory, ignore_errors=True)


def disable_overlay() -> None:
    """关覆盖层模式（用例 / 同进程里换别的模式用）：包装还在，但模式开关一关全部直通。"""
    _state["overlay"] = False
    _state["overlay_armed"] = False
    _state["touched"] = False
    _state["on_handled"] = None
    _state["suspended"] = None
    _state["whiteouts"] = set()
    _state["redirect"] = None


def arm_overlay(on: bool) -> None:
    """开 / 关覆盖层窗口（脚本 build 期间开；之后引擎自己的读写不经过覆盖层）。"""
    _state["overlay_armed"] = bool(on)


def overlay_active() -> bool:
    return bool(_state["overlay"])


def _install_overlay_wrappers() -> None:
    """包装覆盖层接管的入口。每次 `install_overlay` 都重装一遍（装在此刻最外层）：账本每次 build 都重新装，
    覆盖层的包装必须在它外面——先把路径换成覆盖层里的，账本看到的才不是项目里那条。重装叠出来的旧层只是多一次直通。"""
    path = os.path
    _capture_orig()
    wrapped_open = _w_open(builtins.open)
    builtins.open = wrapped_open
    io.open = wrapped_open
    pathlib.Path.open = _w_path_open(pathlib.Path.open)
    os.open = _w_os_open(os.open)
    os.remove = _ov(os.remove, _w_remove(os.remove, "os.remove"))
    os.unlink = _ov(os.unlink, _w_remove(os.unlink, "os.unlink"))
    os.rmdir = _ov(os.rmdir, _w_rmdir(os.rmdir))
    os.rename = _ov(os.rename, _w_rename(os.rename, "os.rename"))
    os.replace = _ov(os.replace, _w_rename(os.replace, "os.replace"))
    shutil.rmtree = _ov(shutil.rmtree, _w_rmtree(shutil.rmtree))
    shutil.move = _ov(shutil.move, _w_move(shutil.move))
    pathlib.Path.write_text = _ov(pathlib.Path.write_text, _w_write_text(pathlib.Path.write_text))
    pathlib.Path.unlink = _ov(pathlib.Path.unlink, _w_path_unlink(pathlib.Path.unlink))
    for name in ("exists", "isfile", "isdir"):
        setattr(path, name, _w_exists(getattr(path, name)))
    for name in ("exists", "is_file", "is_dir"):
        setattr(pathlib.Path, name, _w_path_check(getattr(pathlib.Path, name)))
    path.getsize = _w_getsize(path.getsize)
    datasource = getattr(sys.modules.get("numpy.lib._datasource"), "DataSource", None)
    if datasource is not None and hasattr(datasource, "open"):
        datasource.open = _w_datasource_open(datasource.open)
    _state["overlay_installed"] = True
