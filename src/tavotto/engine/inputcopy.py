"""把用户指认过的数据复制到脚本要读的原路径（ADR 0173）。

只在**桌面自有 native 执行**确证「只读打开某个绝对路径落空」、并且那条路径已经有用户亲手确认过的数据身份
（改指表命中它、目标是一份存在的普通文件）时提议。native 本身不装输入改指（ADR 0106 §三），所以这里是另一条出路：
经用户点一下，把那份文件**复制**过去，再重跑。纯标准库（Flask 父进程 import 链上）。

主语：**Flask 父进程**，用户点按钮的那一刻；源 = 改指表推出的那份文件（绝不取自请求），目标 = 脚本读的那一串。

边界（每一条都有看护，见 ADR 0173 §二）：

* 只复制**文件**；源不是普通文件、目标的父目录不存在 → 不复制、不建目录，终态 `auth_failed`（卡片 c4）。
* 目标**已存在**（含悬空符号链接以外的一切）→ 不复制、不覆盖，视作成功直接重跑。
* 先用普通权限试；原子写——同目录临时名写满、fsync，再以「不覆盖」的方式发布（POSIX `os.link` + 删临时名，
  不支持硬链接的盘退成「查无再 rename」；Windows 的 `os.rename` 本身不覆盖）。
* 没权限（`EACCES`；Windows `ERROR_ACCESS_DENIED`）→ `denied`（c2）：用户点「授权后复制」才走提权，
  提权**只做这一次复制**：Windows 拉起应用自身可执行文件的固定子命令（`--elevated-copy <源> <目标>`，壳里再校验一次），
  macOS 用 `osascript … with administrator privileges` 跑一条 `cp -n`，路径经 `argv` 传入、AppleScript 的
  `quoted form` 转义，绝不拼进脚本文本。
* macOS 受保护文件夹（桌面 / 文稿 / 下载 / iCloud Drive / 外置卷）得到 `EPERM` → `macos_protected`（c3）：
  管理员密码解决不了 TCC，只指路去「完全磁盘访问」。
* 只读介质、写满、网络盘拒绝、提权被取消 / 失败 → `auth_failed`（c4）。
* 记一条**本机**可撤销记录（`input_copies`，项目设置，不进诊断包、不进项目文件）；撤销只删「还是我们当时复制的那份」。
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import secrets
import subprocess
import sys
import threading
import time

from . import config, figcapture, inputremap
from .runtime import CREATE_NO_WINDOW

SETTINGS_KEY = "input_copies"
MAX_RECORDS = 50

# 卡片的四个终态（前端 `MissingInputOffer.copy.state` 同源）
STATE_ASK = "ask"
STATE_DENIED = "denied"
STATE_PROTECTED = "macos_protected"
STATE_FAILED = "auth_failed"
# 只出现在接口结果里（卡片不会停在这两档：成功就重跑）
STATE_PENDING = "pending"  # 复制记录的状态：发布前先记 pending，核对写完再改 done
STATE_DONE = "done"
RESULT_DONE = "done"
RESULT_EXISTS = "exists"

#: 提权助手（Rust 壳的 `--elevated-copy`）的退出码；与 `src-tauri/src/elevated_copy.rs` 严格同源
HELPER_OK = 0
HELPER_EXISTS = 10
HELPER_INVALID = 11
HELPER_DENIED = 12
HELPER_FAILED = 13

#: 壳在起 sidecar 时放进环境的自身可执行文件路径（只有 Windows 提权用得到）
SHELL_EXE_ENV = "TAVOTTO_SHELL_EXE"
HELPER_FLAG = "--elevated-copy"
#: 等用户点系统授权窗口的上限（秒）；到点只是不再等，拉起的那一次由助手自己做完或被用户关掉
ELEVATION_TIMEOUT_S = 180.0

_CHUNK = 1 << 20
_LOCK = threading.Lock()


class CopyError(Exception):
    """复制没做成：`state` 是卡片终态（denied / macos_protected / auth_failed），`reason` 是稳定的短码。"""

    def __init__(self, state: str, reason: str):
        super().__init__(f"{state}:{reason}")
        self.state = state
        self.reason = reason


# ---------------------------------------------------------------- 提议


def is_native_absolute(path: str) -> bool:
    """脚本读的那串是不是**本机意义上的**绝对路径（POSIX 上 `C:\\x` 不是；Windows 上 `/x` 没有盘符，也不收）。"""
    if not isinstance(path, str) or not path or "\x00" in path or len(path) > 4096:
        return False
    if any(ord(c) < 32 for c in path):
        return False
    if os.name == "nt":
        drive, rest = os.path.splitdrive(path)
        return bool(drive) and rest[:1] in ("\\", "/")
    return path.startswith("/")


def protected_kind(directory: str) -> str | None:
    """macOS 上目标目录落在哪类 TCC 受保护位置：`desktop` / `documents` / `downloads` / `icloud` / `volume`；不是回 None。

    只按路径判（不探测权限）：用于把 `EPERM` 归到「系统保护」而不是「没有权限」。非 macOS 恒为 None。
    """
    if sys.platform != "darwin":
        return None
    try:
        real = os.path.realpath(directory)
    except OSError:
        return None
    home = os.path.realpath(os.path.expanduser("~"))
    table = (
        ("desktop", os.path.join(home, "Desktop")),
        ("documents", os.path.join(home, "Documents")),
        ("downloads", os.path.join(home, "Downloads")),
        ("icloud", os.path.join(home, "Library", "Mobile Documents")),
        ("icloud", os.path.join(home, "Library", "CloudStorage")),
    )
    for kind, base in table:
        if real == base or real.startswith(base + os.sep):
            return kind
    # 外置卷 / 网络卷：/Volumes/<名字>（启动盘在那里是指回 `/` 的符号链接，realpath 之后不再以 /Volumes 打头）
    if real.startswith("/Volumes/"):
        return "volume"
    return None


#: 提议令牌 → 服务端当时观察到的 `(项目根, 脚本, 读取路径)`。路径值永远来自服务端自己的观察（native 运行时的读取事实），
#: 请求里只有一个不透明令牌，只当字典键用：用户可控的字符串到不了任何文件系统调用（CodeQL py/path-injection 的污点断在这里）。
_OFFERS: dict[str, tuple[str, str, str]] = {}
_OFFERS_MAX = 64


def _mint(root: str | os.PathLike, script: str, requested: str) -> str:
    key = (str(root), script, requested)
    with _LOCK:
        for token, value in _OFFERS.items():
            if value == key:
                return token
        token = secrets.token_urlsafe(16)
        _OFFERS[token] = key
        while len(_OFFERS) > _OFFERS_MAX:
            _OFFERS.pop(next(iter(_OFFERS)))
        return token


def lookup(root: str | os.PathLike, token: object) -> str | None:
    """令牌对应的读取路径（服务端自己存的那一份）；令牌未知、或属于别的项目回 None。"""
    if not isinstance(token, str):
        return None
    with _LOCK:
        value = _OFFERS.get(token)
    if value is None or value[0] != str(root):
        return None
    return value[2]


def offer(root: str | os.PathLike, requested: str, script: str = "") -> dict | None:
    """native 读 `requested` 落空时的复制提议；不该提议回 None（走原有出路）。

    条件：`requested` 是本机绝对路径；改指表命中它；命中的那份**存在**。其余形状都不提议：
    相对路径、没有规则、规则指向的文件已不在。能判出「复制注定做不成」的（源是目录、父目录不存在）给 c4，
    让卡片一开始就只摆「改脚本里的路径」「让 AI 修复」，不叫用户白点一次。
    """
    if not is_native_absolute(requested):
        return None
    source = figcapture.remap_target(_rules(root), requested)
    source = (
        os.path.normpath(source) if source else source
    )  # 规则里的 `to` 可能是正斜杠写法（Windows）
    if not source or not is_native_absolute(source) or not os.path.exists(source):
        return None
    directory = os.path.dirname(requested)
    base = {
        "token": _mint(root, script, requested),
        "source": source,
        "target": requested,
        "dir": directory,
        "dir_name": os.path.basename(directory.rstrip("\\/")) or directory,
    }
    kind = protected_kind(directory)
    if kind:
        base["protected_kind"] = kind
        if kind == "volume":  # 受保护的是这一整块盘：卡片上说它的名字
            base["protected_name"] = os.path.realpath(directory).split("/")[2]
    if not os.path.isfile(source):
        return {**base, "state": STATE_FAILED, "reason": "source_not_file"}
    if not os.path.isdir(directory):
        return {**base, "state": STATE_FAILED, "reason": "no_parent"}
    return {**base, "state": STATE_ASK}


def _rules(root: str | os.PathLike) -> list[dict]:
    return inputremap.rules_for(root)


def resolve(root: str | os.PathLike, requested: str) -> tuple[str, str]:
    """按改指表推出（源, 目标）；对不上抛 `CopyError(auth_failed, no_source)`。源永远来自规则，不来自请求。"""
    found = offer(root, requested)
    if found is None:
        raise CopyError(STATE_FAILED, "no_source")
    if found["state"] == STATE_FAILED:
        raise CopyError(STATE_FAILED, found["reason"])
    return found["source"], found["target"]


# ---------------------------------------------------------------- 普通权限复制


def _validate(source: str, target: str) -> None:
    """助手与本机两条路径共用的前置判据：都是绝对路径；源是普通文件；父目录存在。目标已存在由调用方先判。"""
    if not (is_native_absolute(source) and is_native_absolute(target)):
        raise CopyError(STATE_FAILED, "not_absolute")
    if not os.path.isfile(source):
        raise CopyError(STATE_FAILED, "source_not_file")
    if not os.path.isdir(os.path.dirname(target)):
        raise CopyError(STATE_FAILED, "no_parent")


def _classify(exc: OSError, directory: str) -> CopyError:
    """一次失败的 `OSError` → 卡片终态。macOS 的 TCC 拒绝是 `EPERM`，权限位拒绝是 `EACCES`。"""
    code = exc.errno
    winerror = getattr(exc, "winerror", None)
    if code in (errno.EROFS,) or winerror in (19,):  # 只读介质 / ERROR_WRITE_PROTECT
        return CopyError(STATE_FAILED, "read_only")
    if code in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)) or winerror in (
        112,
        39,
    ):  # 写满
        return CopyError(STATE_FAILED, "no_space")
    if code in (errno.EACCES, errno.EPERM) or winerror == 5:
        if code == errno.EPERM and protected_kind(directory):
            return CopyError(STATE_PROTECTED, "tcc")
        return CopyError(STATE_DENIED, "access_denied")
    return CopyError(STATE_FAILED, "io_error")


def _publish_no_overwrite(tmp: str, target: str) -> None:
    """把写满的临时文件放到 `target`，**绝不覆盖**。目标此刻存在 → `FileExistsError`。"""
    if os.name == "nt":
        os.rename(tmp, target)  # Windows 的 rename 目标存在即报错
        return
    try:
        os.link(tmp, target)  # 原子且不覆盖
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOSPC):
            raise
        # 盘不支持硬链接（exFAT / SMB 等）：rename 在 POSIX 上会覆盖，不能用「查无再 rename」（两步之间
        # 别的进程建的同名文件会被换掉）。改由内核独占创建目标（O_EXCL，原子且不覆盖）再把内容写进去
        _publish_by_exclusive_create(tmp, target)
        return
    os.unlink(tmp)


def _publish_by_exclusive_create(tmp: str, target: str) -> None:
    """不支持硬链接时的发布：`O_CREAT | O_EXCL` 由内核保证「目标已在就失败」，写失败只删自己建的那个。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o644)  # 目标此刻存在 → FileExistsError，别人的文件一字不动
    try:
        with os.fdopen(fd, "wb") as out, open(tmp, "rb") as src:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(target)  # 只可能是自己刚独占建出来的这一份
        raise
    os.unlink(tmp)


def copy_no_overwrite(source: str, target: str) -> str:
    """普通权限复制一份文件到 `target`；成功回 sha256。已存在抛 `FileExistsError`，其余抛 `CopyError`。"""
    _validate(source, target)
    if os.path.lexists(target):
        if not os.path.exists(target):
            raise CopyError(STATE_FAILED, "dangling_target")  # 悬空符号链接：不穿过它去写
        raise FileExistsError(errno.EEXIST, "exists", target)
    directory = os.path.dirname(target)
    tmp = os.path.join(
        directory, f".{os.path.basename(target)}.{os.getpid()}.{secrets.token_hex(4)}.tavotto-copy"
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    digest = hashlib.sha256()
    created = False
    try:
        try:
            fd = os.open(tmp, flags, 0o644)
            created = True
            with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        except OSError as exc:
            raise _classify(exc, directory) from exc
        try:
            _publish_no_overwrite(tmp, target)
            created = False
        except FileExistsError:
            raise
        except OSError as exc:
            raise _classify(exc, directory) from exc
    finally:
        if created:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return digest.hexdigest()


# ---------------------------------------------------------------- 提权复制（只做这一次复制）


def windows_command_line(source: str, target: str) -> str:
    """给 `ShellExecuteExW` 的参数串：固定子命令 + 两个路径。

    `ShellExecute` 只收一整串，所以按 MSVC 的 `CommandLineToArgvW` 规则逐个加引号（`subprocess.list2cmdline`
    同一套），且先拒掉任何引号 / 控制字符——Windows 路径里本来就不许有，出现就是有人在构造参数。
    """
    for item in (source, target):
        if not is_native_absolute(item) or '"' in item:
            raise CopyError(STATE_FAILED, "bad_path")
    return subprocess.list2cmdline([HELPER_FLAG, source, target])


def _shell_execute_runas(exe: str, parameters: str, timeout: float) -> int:
    """Windows：UAC 提权拉起 `exe parameters`，等它结束，回退出码。取消授权 → `CopyError(auth_failed, cancelled)`。"""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100
    SW_HIDE = 0
    ERROR_CANCELLED = 1223
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = parameters
    info.nShow = SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.get_last_error() == ERROR_CANCELLED:
            raise CopyError(STATE_FAILED, "cancelled")
        raise CopyError(STATE_FAILED, "elevation_failed")
    handle = info.hProcess
    if not handle:
        raise CopyError(STATE_FAILED, "elevation_failed")
    try:
        if kernel32.WaitForSingleObject(handle, int(timeout * 1000)) != 0:  # WAIT_OBJECT_0
            raise CopyError(STATE_FAILED, "timeout")
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise CopyError(STATE_FAILED, "elevation_failed")
        return int(code.value)
    finally:
        kernel32.CloseHandle(handle)


def _elevate_windows(source: str, target: str) -> None:
    exe = os.environ.get(SHELL_EXE_ENV, "")
    if not exe or not os.path.isabs(exe) or not os.path.isfile(exe):
        raise CopyError(STATE_FAILED, "no_helper")
    code = _shell_execute_runas(exe, windows_command_line(source, target), ELEVATION_TIMEOUT_S)
    if code == HELPER_OK:
        return
    if code == HELPER_EXISTS:
        raise FileExistsError(errno.EEXIST, "exists", target)
    raise CopyError(STATE_FAILED, "helper_denied" if code == HELPER_DENIED else "helper_failed")


#: AppleScript：`argv` 的两项经 `quoted form of` 转义后接进 shell 命令；路径从不出现在脚本文本里。
#: `cp -n` 不覆盖；`chown` 让复制出来的文件归当前用户（否则是 root 的，用户自己的脚本反而读不了 / 删不掉）
_APPLESCRIPT = (
    "on run argv",
    "set src to quoted form of item 1 of argv",
    "set dst to quoted form of item 2 of argv",
    "set who to item 3 of argv",
    'do shell script "/bin/cp -n " & src & " " & dst & " && /usr/sbin/chown " & who & " " & dst'
    " with administrator privileges",
    "end run",
)


def macos_osascript_argv(source: str, target: str, uid: int, gid: int) -> list[str]:
    """`osascript` 的完整 argv（列表形式、不经 shell）：脚本是固定文本，路径作为位置参数。"""
    for item in (source, target):
        if not is_native_absolute(item):
            raise CopyError(STATE_FAILED, "bad_path")
    out = ["/usr/bin/osascript"]
    for line in _APPLESCRIPT:
        out += ["-e", line]
    return [*out, source, target, f"{int(uid)}:{int(gid)}"]


def _elevate_macos(source: str, target: str) -> None:
    argv = macos_osascript_argv(source, target, os.getuid(), os.getgid())
    try:
        proc = subprocess.run(  # noqa: S603 — 固定可执行文件 + 列表参数，无 shell
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ELEVATION_TIMEOUT_S,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:  # run() 超时已经杀了自己起的那个子进程
        raise CopyError(STATE_FAILED, "timeout") from exc
    except OSError as exc:
        raise CopyError(STATE_FAILED, "elevation_failed") from exc
    if proc.returncode != 0:
        # -128 = 用户在系统窗口里点了取消；其余（密码错、cp 失败）一并归到「没做成」
        reason = "cancelled" if "-128" in (proc.stderr or "") else "elevation_failed"
        raise CopyError(STATE_FAILED, reason)


#: 提权写入不该去的地方 / 不该是的类型：数据文件不是这些。Windows 助手（`elevated_copy.rs`）自己也判一次，扩展名表同源
REFUSED_EXTENSIONS = frozenset(
    "exe dll sys scr com cpl msi bat cmd ps1 psm1 vbs vbe js jse wsf wsh hta lnk reg pif url inf gadget msc".split()
)
_MAC_SYSTEM_DIRS = (
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/private/etc",
    "/Library/LaunchDaemons",
    "/Library/LaunchAgents",
    "/Library/StartupItems",
    "/Library/Extensions",
)


def elevation_refused(target: str) -> bool:
    """这个目标不许被提权写入（扩展名像可执行 / 脚本 / 快捷方式，或在系统目录里）。"""
    ext = os.path.splitext(target)[1].lstrip(".").lower()
    if ext in REFUSED_EXTENSIONS:
        return True
    norm = os.path.normcase(os.path.normpath(target))
    if os.name == "nt":
        root = os.environ.get("SystemRoot", "")
        return bool(root) and (norm + os.sep).startswith(
            os.path.normcase(os.path.normpath(root)) + os.sep
        )
    real = os.path.realpath(os.path.dirname(target))
    return (
        any(real == d or real.startswith(d + "/") for d in _MAC_SYSTEM_DIRS)
        or "/LaunchAgents" in real
    )


def _elevate(source: str, target: str) -> None:
    if elevation_refused(target):
        raise CopyError(STATE_FAILED, "refused_target")
    if os.name == "nt":
        _elevate_windows(source, target)
    elif sys.platform == "darwin":
        _elevate_macos(source, target)
    else:
        raise CopyError(STATE_FAILED, "no_elevation")


# ---------------------------------------------------------------- 入口与记录

#: macOS 系统设置里「完全磁盘访问」的固定地址（c3 的主按钮；没有参数、不收任何来自请求的字符串）
FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def open_full_disk_access() -> bool:
    """打开「隐私与安全性 → 完全磁盘访问」。只在 macOS 有意义；成功拉起回 True。"""
    if sys.platform != "darwin":
        return False
    try:
        proc = subprocess.run(  # noqa: S603 — 固定可执行文件 + 固定地址
            ["/usr/bin/open", FULL_DISK_ACCESS_URL],
            capture_output=True,
            timeout=15,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def run(root: str | os.PathLike, token: object, *, elevate: bool = False) -> dict:
    """点了「复制过去再运行」/「授权后复制」：返回 `{state, reason?, …}`。

    `state` ∈ `done`（复制好了）/ `exists`（目标本来就在，没动它）/ `denied` / `macos_protected` / `auth_failed`。
    前两档前端直接重跑；后三档就是卡片 c2 / c3 / c4。**任何分支都不抛**：每一种结果都有一个终态。

    发布前先记一条 `pending` 记录、核对写完后改成 `done`：写到一半进程被杀（不支持硬链接的盘上是独占创建目标再写内容）
    会留下残缺目标，下次点击时靠这条记录认出它是**我们自己没写完的那份**，删掉重来，绝不拿残缺数据重跑成「已存在」。
    没有记录的同名文件一律是用户自己的，绝不删。
    """
    requested = lookup(root, token)  # 路径值是服务端当时观察到的那一份，不是请求里的字符串
    if requested is None:
        return {"state": STATE_FAILED, "reason": "no_source"}
    try:
        source, target = resolve(root, requested)
    except CopyError as exc:
        return _result(exc.state, exc.reason, requested)
    try:
        src_sha, src_size = _sha256_of(source), os.path.getsize(source)
    except OSError as exc:
        err = _classify(exc, os.path.dirname(target))
        return _result(err.state, err.reason, target)
    try:
        if os.path.lexists(target):
            verdict = _existing_target(root, target, src_sha, src_size)
            if verdict == "exists":
                return {"state": RESULT_EXISTS, "target": target}
        recorded = _put_record(
            root, source, target, src_sha, src_size, via="normal", state=STATE_PENDING
        )
    except CopyError as exc:
        return _result(exc.state, exc.reason, target)
    try:
        try:
            digest = copy_no_overwrite(source, target)
            via = "normal"
        except CopyError as exc:
            if not (elevate and exc.state == STATE_DENIED):
                raise
            _elevate(source, target)
            if not os.path.isfile(target):
                raise CopyError(STATE_FAILED, "not_created") from exc
            digest = _sha256_of(target)
            if digest != src_sha:  # `cp -n` 遇到突然出现的同名文件会静默跳过：那不是我们复制的
                raise CopyError(STATE_FAILED, "not_created") from exc
            via = "elevated"
    except FileExistsError:
        _drop_pending(root, recorded)  # 别人抢先建了：那不是我们的，记录作废
        return {"state": RESULT_EXISTS, "target": target}
    except CopyError as exc:
        _drop_pending(root, recorded)
        return _result(exc.state, exc.reason, target)
    except OSError as exc:  # 兜底：绝不让一个没分类的异常变成卡片上的「报错」
        _drop_pending(root, recorded)
        err = _classify(exc, os.path.dirname(target))
        return _result(err.state, err.reason, target)
    try:
        _put_record(
            root, source, target, digest, os.path.getsize(target), via=via, state=STATE_DONE
        )
    except (CopyError, OSError):
        pass  # 复制已完成且核对过；记录只是撤销用，留着 pending 也会被下次判成「内容对得上」
    return {"state": RESULT_DONE, "target": target, "via": via}


def _existing_target(root: str | os.PathLike, target: str, src_sha: str, src_size: int) -> str:
    """目标此刻已存在：是用户的文件（或我们完整写好的）→ `"exists"`；是我们上次没写完的残缺文件 → 删掉并回 `"removed"`。

    判据只有记录：有一条针对这个目标、`state == pending`、而内容与源对不上的 → 残缺；pending 但内容完整（只差改状态）→ 补记 done、算已存在；
    没有记录 / done → 用户的文件，不碰。删不掉 → `CopyError(auth_failed)`，绝不拿残缺数据重跑。
    """
    with _LOCK:
        rows = _records(root)
        row = next((r for r in rows if r.get("target") == target), None)
    if row is None or row.get("state", STATE_DONE) != STATE_PENDING:
        if not os.path.exists(target):
            raise CopyError(STATE_FAILED, "dangling_target")  # 悬空符号链接：不穿过它去写
        return "exists"
    path = row["target"]  # 只用记录里存的那一份做路径运算
    try:
        complete = (
            os.path.isfile(path)
            and not os.path.islink(path)
            and os.path.getsize(path) == row.get("size") == src_size
            and _sha256_of(path) == row.get("sha256") == src_sha
        )
    except OSError:
        complete = False
    if complete:
        with contextlib.suppress(CopyError, OSError):
            _put_record(
                root,
                row.get("source", ""),
                path,
                src_sha,
                src_size,
                via=row.get("via", "normal"),
                state=STATE_DONE,
            )
        return "exists"
    if os.path.islink(path) or not os.path.isfile(path):
        # 记录说是我们写的，但那里现在不是一份普通文件（被换成了别的东西）：不删，也不当作完整
        raise CopyError(STATE_FAILED, "stale_partial_not_removed")
    try:
        os.unlink(path)
    except OSError as exc:
        raise CopyError(STATE_FAILED, "stale_partial_not_removed") from exc
    return "removed"


def _result(state: str, reason: str, target: str) -> dict:
    out = {"state": state, "reason": reason}
    kind = protected_kind(os.path.dirname(target)) if state == STATE_PROTECTED else None
    if kind:
        out["protected_kind"] = kind
    return out


def _put_record(
    root: str | os.PathLike,
    source: str,
    target: str,
    digest: str,
    size: int,
    *,
    via: str,
    state: str,
) -> dict:
    """写（或替换）这个目标的复制记录；`pending` 写不进去就不复制（没有记录就认不出残缺文件）。"""
    row = {
        "target": target,
        "source": source,
        "sha256": digest,
        "size": size,
        "via": via,
        "state": state,
        "at": time.time(),
    }
    try:
        with _LOCK:
            rows = [r for r in _records(root) if r.get("target") != target]
            rows.append(row)
            config.set_project_settings(str(root), {SETTINGS_KEY: rows[-MAX_RECORDS:]})
    except OSError as exc:
        raise CopyError(STATE_FAILED, "record_failed") from exc
    return row


def _drop_pending(root: str | os.PathLike, row: dict) -> None:
    """这次复制没做成：撤掉刚写的 pending 记录（别人的 / 已有记录不动）。"""
    with _LOCK, contextlib.suppress(OSError):
        rows = _records(root)
        keep = [
            r for r in rows if not (r.get("target") == row["target"] and r.get("at") == row["at"])
        ]
        if len(keep) != len(rows):
            config.set_project_settings(str(root), {SETTINGS_KEY: keep or None})


def _records(root: str | os.PathLike) -> list[dict]:
    raw = config.project_settings(str(root)).get(SETTINGS_KEY)
    return (
        [r for r in raw if isinstance(r, dict) and isinstance(r.get("target"), str)]
        if isinstance(raw, list)
        else []
    )


def records(root: str | os.PathLike) -> list[dict]:
    return _records(root)


def undo(root: str | os.PathLike, target: str) -> dict:
    """撤销一次复制：只删「还是我们当时复制的那份」（大小与指纹都对得上）；用户后来改过的不动。

    回 `{state: removed | missing | changed | not_recorded | failed}`。macOS 提权复制出来的文件归用户（`chown`），
    所在目录用户没有写权限时删不掉 → `failed`，原样说，不替用户再提权一次。
    """
    with _LOCK:
        rows = _records(root)
        row = next((r for r in rows if r["target"] == target), None)
        if row is None:
            return {"state": "not_recorded"}
        target = row["target"]  # 此后只用设置里存的那一份，不用请求里的字符串
        if not os.path.lexists(target):
            _drop(root, rows, target)
            return {"state": "missing"}
        try:
            same = (
                os.path.isfile(target)
                and not os.path.islink(target)
                and os.path.getsize(target) == row.get("size")
                and _sha256_of(target) == row.get("sha256")
            )
        except OSError:
            return {"state": "failed"}
        if not same:
            return {"state": "changed"}
        try:
            os.unlink(target)
        except OSError:
            return {"state": "failed"}
        _drop(root, rows, target)
        return {"state": "removed"}


def _drop(root: str | os.PathLike, rows: list[dict], target: str) -> None:
    config.set_project_settings(
        str(root), {SETTINGS_KEY: [r for r in rows if r["target"] != target] or None}
    )
