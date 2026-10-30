"""「项目外文件夹读不了」的一键修复权限（ADR 0153 修订 2026-10-30）——Flask 父进程这一侧，纯标准库。

场景：脚本把项目外的文件夹插进 `sys.path`，那个文件夹是别的账户（Codex 的 Windows 沙箱账户）经 `pip --target` 建的，
属主是对方、不继承父目录权限，当前账户读不了。用户点一下「修复权限」，经 **一次 UAC 提权**对**这一个目录**做
`icacls <目录> /setowner *S-1-5-32-544 /T /C /L /Q`（属主设成 Administrators，SID 不依赖语言），再 `icacls <目录> /reset /T /C /L /Q` 让它重新继承父目录的权限；两条都带 `/L`、不跟随链接，目录里有链接则整个拒绝。不改父目录，不碰目录外任何东西。

三道结构性防线：

* **令牌，不是路径**：完整路径只活在本进程内存里（`_RECORDS`，从不序列化、不进公开投影与遥测、不写日志）。
  前端拿到的是不透明令牌；修复接口只收令牌。令牌绑定（项目、目录的真实路径）、过期、一次性
  （修复成功 / 修复失败即作废；用户取消提权或没拿到提权不作废，可再点一次——每次点击至多一次 UAC）。
* **路径在动手前再验一遍**（`validate_target`，每一条拒绝有自己的原因码、各有用例与变异）：
  本地绝对路径、是目录、不是盘符根、不是用户主目录本身 / 它的祖先 / 主目录下的标准文件夹、不是系统目录
  （Windows、Program Files、ProgramData）、仍在项目之外且不包住项目、真实路径与登记时一致（挡掉登记后换成联接点）。
* **动作只有 Windows**，提权由 `shell_execute_runner` 发起（ShellExecuteEx `runas`）；`run_repair` 的 runner 与
  「此刻还读不读得了」都可注入，测试用假的，不真提权。
"""

from __future__ import annotations

import base64
import ntpath
import os
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import projectenv

#: 令牌寿命（秒）。失败卡一直开着，超时后再点会得到 `repair_token_invalid`，重新运行一次即出新令牌。
TOKEN_TTL = 900.0
#: 同时登记的上限（超出丢最旧的）：令牌只由失败产生，正常用量远低于它。
MAX_RECORDS = 32
#: 等提权进程跑完的上限（秒）：大目录 `takeown /r` 与 `icacls /t` 可能要一会儿。
RUN_TIMEOUT = 600.0
#: 提权前预检遍历的条目上限：超了就拒绝（递归命令要在时限内跑完，也不替用户处理一棵巨树）。
MAX_ENTRIES = 20000
#: 提权脚本自己的退出码：动手前复查发现重解析点 / 复查本身没跑成（与 icacls 的 0、1 区分开）。
EXIT_LINKS = 201
EXIT_CHECK_FAILED = 202
EXIT_TOO_MANY = 203
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

#: 稳定错误码（协议契约；`tests/test_error_codes.py` 读 `ERROR_CODES`）。
ERROR_UNSUPPORTED = "repair_unsupported"
ERROR_TOKEN_INVALID = "repair_token_invalid"
ERROR_TARGET_REJECTED = "repair_target_rejected"
ERROR_IN_PROGRESS = "repair_in_progress"
ERROR_NOT_CONFIRMED = "repair_not_confirmed"
ERROR_CANCELLED = "repair_cancelled"
ERROR_NOT_ELEVATED = "repair_not_elevated"
ERROR_FAILED = "repair_failed"
ERROR_HAS_LINKS = "repair_has_links"
ERROR_TOO_LARGE = "repair_too_large"
ERROR_CODES = (
    ERROR_UNSUPPORTED,
    ERROR_TOKEN_INVALID,
    ERROR_TARGET_REJECTED,
    ERROR_IN_PROGRESS,
    ERROR_NOT_CONFIRMED,
    ERROR_CANCELLED,
    ERROR_NOT_ELEVATED,
    ERROR_FAILED,
    ERROR_HAS_LINKS,
    ERROR_TOO_LARGE,
)

#: `validate_target` 的拒绝原因（闭集；只进服务端日志与测试，不进响应——响应统一 `repair_target_rejected`）。
REASON_NOT_ABSOLUTE = "not_absolute"
REASON_UNC = "unc"
REASON_NOT_DIR = "not_dir"
REASON_DRIVE_ROOT = "drive_root"
REASON_HOME = "home"
REASON_HOME_ANCESTOR = "home_ancestor"
REASON_HOME_KNOWN_FOLDER = "home_known_folder"
REASON_SYSTEM_DIR = "system_dir"
REASON_INSIDE_PROJECT = "inside_project"
REASON_CONTAINS_PROJECT = "contains_project"
REASON_CHANGED = "changed"
REASONS = (
    REASON_NOT_ABSOLUTE,
    REASON_UNC,
    REASON_NOT_DIR,
    REASON_DRIVE_ROOT,
    REASON_HOME,
    REASON_HOME_ANCESTOR,
    REASON_HOME_KNOWN_FOLDER,
    REASON_SYSTEM_DIR,
    REASON_INSIDE_PROJECT,
    REASON_CONTAINS_PROJECT,
    REASON_CHANGED,
)

#: 主目录下不许整个重置的标准文件夹（小写比较）。
_HOME_KNOWN_FOLDERS = frozenset(
    {
        "appdata",
        "desktop",
        "documents",
        "downloads",
        "music",
        "onedrive",
        "pictures",
        "videos",
        "contacts",
        "favorites",
        "links",
        "saved games",
        "searches",
    }
)
#: 系统目录的环境变量（目录本身与其下一切都不许动）。
_SYSTEM_ENV_VARS = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)


class RepairError(Exception):
    """带稳定 code 的失败；`reason` 只给日志 / 测试（`REASONS`）。"""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


def supported(platform: str | None = None) -> bool:
    """只有 Windows 有这个动作（takeown / icacls / UAC 都是 Windows 的）。"""
    return (platform or sys.platform) == "win32"


# --------------------------------------------------------------------------- 路径


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _real(path: str) -> str:
    """真实路径（解开符号链接 / 联接点）；去掉 Windows 的 `\\\\?\\` 长路径前缀（UNC 形态保留）。"""
    real = os.path.realpath(path)
    if real.startswith("\\\\?\\") and not real.upper().startswith("\\\\?\\UNC\\"):
        real = real[4:]
    return real


def _under(path: str, root: str) -> bool:
    """`path` 是 `root` 本身或其下（两边都已 `_norm`）。"""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _env_dirs(env) -> list[str]:
    out = []
    for name in _SYSTEM_ENV_VARS:
        value = env.get(name)
        if value:
            out.append(_norm(value))
    return out


def validate_target(
    path: str,
    project: str,
    *,
    home: str | None = None,
    env=None,
    expected_real: str | None = None,
) -> str:
    """修复前对目录再验一遍；通过回**真实路径**（命令只对它动手），不通过抛 `RepairError(ERROR_TARGET_REJECTED, 原因)`。

    `expected_real` 是登记那一刻的真实路径：与此刻不一致（登记后目录被换成联接点 / 挪走再建同名）拒绝。
    """
    if not isinstance(path, str) or not path or "\0" in path:
        raise RepairError(ERROR_TARGET_REJECTED, REASON_NOT_ABSOLUTE)
    if path.startswith("\\\\") or path.startswith("//"):
        # 网络共享：takeown / icacls 对它没有意义，也不该替用户去改别人机器上的权限
        raise RepairError(ERROR_TARGET_REJECTED, REASON_UNC)
    if not os.path.isabs(path):
        raise RepairError(ERROR_TARGET_REJECTED, REASON_NOT_ABSOLUTE)
    if not os.path.isdir(path):
        raise RepairError(ERROR_TARGET_REJECTED, REASON_NOT_DIR)
    real = _real(path)
    if real.startswith("\\\\"):
        raise RepairError(ERROR_TARGET_REJECTED, REASON_UNC)
    if expected_real is not None and _norm(real) != _norm(expected_real):
        raise RepairError(ERROR_TARGET_REJECTED, REASON_CHANGED)
    target = _norm(real)
    if os.path.dirname(target) == target:
        raise RepairError(ERROR_TARGET_REJECTED, REASON_DRIVE_ROOT)
    home_dir = _norm(_real(home if home is not None else os.path.expanduser("~")))
    if target == home_dir:
        raise RepairError(ERROR_TARGET_REJECTED, REASON_HOME)
    if _under(home_dir, target):
        # 主目录的祖先（`C:\Users`、盘符根下的任何包住主目录的目录）：重置它等于重置所有人的主目录
        raise RepairError(ERROR_TARGET_REJECTED, REASON_HOME_ANCESTOR)
    if (
        os.path.dirname(target) == home_dir
        and os.path.basename(target).lower() in _HOME_KNOWN_FOLDERS
    ):
        raise RepairError(ERROR_TARGET_REJECTED, REASON_HOME_KNOWN_FOLDER)
    for system_dir in _env_dirs(os.environ if env is None else env):
        if _under(target, system_dir) or _under(system_dir, target):
            raise RepairError(ERROR_TARGET_REJECTED, REASON_SYSTEM_DIR)
    if project:
        project_dir = _norm(_real(project))
        if _under(target, project_dir):
            raise RepairError(ERROR_TARGET_REJECTED, REASON_INSIDE_PROJECT)
        if _under(project_dir, target):
            raise RepairError(ERROR_TARGET_REJECTED, REASON_CONTAINS_PROJECT)
    return real


def still_unreadable(file: str) -> bool:
    """登记的那个读不了的文件此刻是否仍读不了（`PermissionError`）。读得了 / 没了都是 False：没有什么要修的。"""
    try:
        with open(file, "rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- 令牌


@dataclass
class _Record:
    project: str
    path: str
    real: str
    file: str
    expires: float
    in_flight: bool = False


_RECORDS: dict[str, _Record] = {}
_LOCK = threading.Lock()


def _project_key(project) -> str:
    return projectenv._key(project) if project else ""


def _purge(now: float) -> None:
    for token in [t for t, r in _RECORDS.items() if r.expires <= now]:
        del _RECORDS[token]
    while len(_RECORDS) > MAX_RECORDS:
        del _RECORDS[next(iter(_RECORDS))]


def issue(
    project, path, file="", *, platform: str | None = None, now: float | None = None
) -> str | None:
    """失败那一刻登记（`pool.unreadable_folder_error`）：回不透明令牌；非 Windows、缺项目 / 路径回 `None`（不出按钮）。

    登记不验路径（失败当时只是记事实）；验在兑现的那一刻（`validate_target`）。同一 (项目, 目录) 重复登记换新令牌、旧的作废。"""
    if not supported(platform) or not project or not isinstance(path, str) or not path:
        return None
    if not isinstance(file, str):
        file = ""
    t = time.monotonic() if now is None else now
    project_key = _project_key(project)
    try:
        real = _real(path)
    except (OSError, ValueError):
        return None
    token = secrets.token_urlsafe(24)
    with _LOCK:
        for old in [
            k
            for k, r in _RECORDS.items()
            if r.project == project_key and _norm(r.real) == _norm(real) and not r.in_flight
        ]:
            del _RECORDS[old]
        _RECORDS[token] = _Record(project_key, path, real, file, t + TOKEN_TTL)
        _purge(t)
    return token


def reset() -> None:
    """清空全部令牌与「读不了的模块」记账（测试之间）。"""
    with _LOCK:
        _RECORDS.clear()
        _UNREADABLE_MODULES.clear()


#: 项目 → 在那里撞过「读不了的项目外文件夹」的顶层模块名。联合计划据此不把它们当表外依赖去问包名
#: （文件就在那个文件夹里，多半是用户自己的模块）。进程内、只有模块名，不落盘、不含路径。
_UNREADABLE_MODULES: dict[str, set[str]] = {}


def note_unreadable_module(project, module: str) -> None:
    if project and isinstance(module, str) and module:
        with _LOCK:
            _UNREADABLE_MODULES.setdefault(_project_key(project), set()).add(module)


def unreadable_modules(project) -> frozenset[str]:
    if not project:
        return frozenset()
    with _LOCK:
        return frozenset(_UNREADABLE_MODULES.get(_project_key(project), ()))


def _claim(token, project, now: float) -> _Record:
    """校验令牌并占位（同一令牌同时只有一次修复在进行）；不存在 / 过期 / 绑的是别的项目一律 `repair_token_invalid`。"""
    with _LOCK:
        _purge(now)
        record = _RECORDS.get(token) if isinstance(token, str) else None
        if record is None or record.project != _project_key(project):
            raise RepairError(ERROR_TOKEN_INVALID)
        if record.in_flight:
            raise RepairError(ERROR_IN_PROGRESS)
        record.in_flight = True
        return record


def _release(token: str, *, consumed: bool) -> None:
    with _LOCK:
        if consumed:
            _RECORDS.pop(token, None)
        else:
            record = _RECORDS.get(token)
            if record is not None:
                record.in_flight = False


# --------------------------------------------------------------------------- 命令


def _system32(env) -> str:
    root = env.get("SystemRoot") or env.get("windir") or "C:\\Windows"
    return ntpath.join(root, "System32")  # Windows 的系统工具，路径永远是 Windows 写法


def _is_reparse(entry) -> bool:
    """符号链接 / 联接点 / 其它重解析点（不跟随：只看 lstat 与 `is_symlink`）。"""
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True  # 看不清就当有：宁可拒绝
    return bool(attrs & _REPARSE_POINT)


def scan_for_links(folder: str, limit: int | None = None) -> None:
    """提权**之前**遍历目录（不跟随任何链接）：发现重解析点抛 `repair_has_links`，条目超限抛 `repair_too_large`。

    递归的属主 / ACL 命令若碰到指向目录外的链接会连目标一起改，所以链接一律拒绝。当前账户读不了的子目录
    （正是要修的）这里列不出来，跳过——提权脚本在动手前会以管理员身份再复查一遍整棵树。"""
    cap = MAX_ENTRIES if limit is None else limit
    seen = 0
    stack = [folder]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    seen += 1
                    if seen > cap:
                        raise RepairError(ERROR_TOO_LARGE)
                    if _is_reparse(entry):
                        raise RepairError(ERROR_HAS_LINKS)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except PermissionError:
            continue


def build_commands(folder: str, env=None) -> list[list[str]]:
    """提权后依次执行的两条命令，argv 精确：只对 `folder` 这一个目录；系统工具用绝对路径（不吃 PATH）。

    `/setowner *S-1-5-32-544`：属主设成 Administrators（SID，不依赖系统语言；提权令牌的 SeRestorePrivilege 由 icacls 自己启用），
    随后 `/reset` 恢复继承。两条都带 `/L`：对链接本身操作、不跟随目标。"""
    icacls = ntpath.join(_system32(os.environ if env is None else env), "icacls.exe")
    return [
        [icacls, folder, "/setowner", "*S-1-5-32-544", "/T", "/C", "/L", "/Q"],
        [icacls, folder, "/reset", "/T", "/C", "/L", "/Q"],
    ]


def _ps_quote(arg: str) -> str:
    return "'" + arg.replace("'", "''") + "'"


#: 提权脚本里内联编译的 C# 遍历器（Windows PowerShell 5.1 自带 `Add-Type`，无外部依赖；比让提权进程去找一个 Python 稳）。
#: **备份语义**：先启用 SeBackupPrivilege（提权令牌持有、默认未启用），再用
#: `CreateFileW(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)` 打开每一层目录——绕过 DACL 的访问检查，所以连
#: Administrators 都被排除在 DACL 之外的受限目录也能列；`OPEN_REPARSE_POINT` 保证不跟随链接。用
#: `GetFileInformationByHandleEx(FileAttributeTagInfo)` 看本层属性，`FileIdBothDirectoryInfo` 列子项（子项属性里就带重解析标记，
#: 不必再逐个打开）。遍历不改任何 ACL；返回 0 干净、201 有重解析点、203 条目超限、202 遍历本身失败。
_WALK_SOURCE = r"""
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class RepairWalk {
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  struct LuidAndAttributes { public long Luid; public uint Attributes; }
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  struct TokenPrivileges { public uint Count; public LuidAndAttributes P; }

  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  [DllImport("advapi32.dll", SetLastError = true)]
  static extern bool OpenProcessToken(IntPtr proc, uint access, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  static extern bool LookupPrivilegeValue(string system, string name, out long luid);
  [DllImport("advapi32.dll", SetLastError = true)]
  static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAll, ref TokenPrivileges state, uint len, IntPtr prev, IntPtr retLen);
  [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError = true)]
  static extern bool GetFileInformationByHandleEx(SafeFileHandle h, int cls, IntPtr buf, uint size);

  static bool Enable(string name) {
    IntPtr token;
    if (!OpenProcessToken(GetCurrentProcess(), 0x28, out token)) return false;
    try {
      TokenPrivileges tp = new TokenPrivileges();
      tp.Count = 1;
      tp.P.Attributes = 2;
      if (!LookupPrivilegeValue(null, name, out tp.P.Luid)) return false;
      if (!AdjustTokenPrivileges(token, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero)) return false;
      return Marshal.GetLastWin32Error() == 0;
    } finally { CloseHandle(token); }
  }

  public static int Run(string root, int limit) {
    try {
      if (!Enable("SeBackupPrivilege")) return 202;
      Enable("SeRestorePrivilege");
      int count = 0;
      Stack<string> stack = new Stack<string>();
      stack.Push(root);
      IntPtr buf = Marshal.AllocHGlobal(65536);
      try {
        while (stack.Count > 0) {
          string dir = stack.Pop();
          using (SafeFileHandle h = CreateFileW(@"\\?\" + dir, 0x81, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero)) {
            if (h.IsInvalid) return 202;
            if (!GetFileInformationByHandleEx(h, 9, buf, 8)) return 202;
            uint self = (uint)Marshal.ReadInt32(buf);
            if ((self & 0x400) != 0) return 201;
            if ((self & 0x10) == 0) return 202;
            int cls = 11;
            while (true) {
              if (!GetFileInformationByHandleEx(h, cls, buf, 65536)) {
                if (Marshal.GetLastWin32Error() == 18) break;
                return 202;
              }
              cls = 10;
              int off = 0;
              while (true) {
                int next = Marshal.ReadInt32(buf, off);
                uint attr = (uint)Marshal.ReadInt32(buf, off + 56);
                int nlen = Marshal.ReadInt32(buf, off + 60);
                string name = Marshal.PtrToStringUni(IntPtr.Add(buf, off + 104), nlen / 2);
                if (name != "." && name != "..") {
                  count++;
                  if (count > limit) return 203;
                  if ((attr & 0x400) != 0) return 201;
                  if ((attr & 0x10) != 0) stack.Push(dir.TrimEnd('\\') + "\\" + name);
                }
                if (next == 0) break;
                off += next;
              }
            }
          }
        }
      } finally { Marshal.FreeHGlobal(buf); }
      return 0;
    } catch (Exception) { return 202; }
  }
}
"""


def build_elevated_launch(commands: list[list[str]], env=None) -> tuple[str, str]:
    """把命令列表包成**一次**提权启动：`(exe, parameters)`。

    PowerShell 脚本里每个参数都是单引号字面量（不经 cmd 的 `%` / `&` / `^` 展开），经 `-EncodedCommand`（UTF-16LE base64）
    交给 `powershell.exe`；这样两条命令只弹一次 UAC。**先**用 `_WALK_SOURCE` 以备份语义把整棵树遍历检查完
    （任何 ACL 都还没动）：有重解析点 `exit 201`、条目超限 `exit 203`、遍历自己失败 `exit 202`，都什么也不改；
    遍历全部通过后才执行 icacls，任何一条退出码非 0 立即以它收场。"""
    folder = commands[0][1]
    lines = [
        "try { Add-Type -TypeDefinition @'",
        *_WALK_SOURCE.strip("\n").split("\n"),
        f"'@ }} catch {{ exit {EXIT_CHECK_FAILED} }}",
        f"$r = [RepairWalk]::Run({_ps_quote(folder)}, {MAX_ENTRIES})",
        "if ($r -ne 0) { exit $r }",
    ]
    for argv in commands:
        lines.append("& " + " ".join(_ps_quote(a) for a in argv) + " | Out-Null")
        lines.append("if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }")
    lines.append("exit 0")
    script = "\n".join(lines)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    exe = ntpath.join(
        _system32(os.environ if env is None else env), "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    params = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"
    return exe, params


@dataclass
class RunOutcome:
    """runner 的结果：`status` 为 `done`（有退出码）/ `cancelled`（用户在 UAC 上取消）/ `unavailable`（没拿到提权）。"""

    status: str
    exit_code: int = 0


Runner = Callable[[list[list[str]]], RunOutcome]

_ERROR_CANCELLED = 1223  # ERROR_CANCELLED：用户在 UAC 上点了「否」/ 关了凭据框


def shell_execute_runner(commands: list[list[str]]) -> RunOutcome:
    """真正的提权执行（仅 Windows）：ShellExecuteEx `runas` 启动 `build_elevated_launch`，等它结束取退出码。

    UAC 取消 → `cancelled`；其余启动失败（非管理员且没有可用凭据、策略禁止……）→ `unavailable`。
    只有 Windows 真机能验证（见 PR 说明的验收步骤）；其余平台的测试用假 runner。"""
    import ctypes
    from ctypes import wintypes

    exe, params = build_elevated_launch(commands)

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
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_nocloseprocess = 0x00000040
    see_mask_noasync = 0x00000100
    sw_hide = 0
    wait_object_0 = 0
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
    info.fMask = see_mask_nocloseprocess | see_mask_noasync
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = sw_hide
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        return RunOutcome("cancelled" if code == _ERROR_CANCELLED else "unavailable", code)
    handle = info.hProcess
    if not handle:
        return RunOutcome("unavailable", 0)
    try:
        if kernel32.WaitForSingleObject(handle, int(RUN_TIMEOUT * 1000)) != wait_object_0:
            return RunOutcome("done", -1)  # 超时：当作失败，不去杀已提权的进程
        code = wintypes.DWORD(1)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return RunOutcome("done", -1)
        return RunOutcome("done", int(code.value))
    finally:
        kernel32.CloseHandle(handle)


# --------------------------------------------------------------------------- 主流程


def run_repair(
    token,
    project,
    *,
    runner: Runner | None = None,
    platform: str | None = None,
    now: float | None = None,
    home: str | None = None,
    env=None,
    unreadable: Callable[[str], bool] | None = None,
) -> dict:
    """兑现令牌：验证 → 一次提权跑两条命令 → 复核。成功回 `{"repaired": bool}`（`False` = 其实已经读得了，没动任何东西）；
    失败抛 `RepairError`（code 见 `ERROR_CODES`）。"""
    if not supported(platform):
        raise RepairError(ERROR_UNSUPPORTED)
    unreadable = unreadable or still_unreadable
    t = time.monotonic() if now is None else now
    record = _claim(token, project, t)
    consumed = False
    try:
        try:
            real = validate_target(
                record.path, project, home=home, env=env, expected_real=record.real
            )
        except RepairError:
            consumed = True  # 被拒的目标不留着再试：重新运行取新令牌
            raise
        if record.file and not unreadable(record.file):
            # 已经读得了（别处修过 / 文件没了）：不弹 UAC，不动任何权限；令牌用完即废
            consumed = True
            return {"repaired": False}
        try:
            scan_for_links(real)
        except RepairError:
            consumed = True  # 链接 / 超限不会自己变好：重新运行取新令牌
            raise
        outcome = (runner or shell_execute_runner)(build_commands(real, env))
        if outcome.status == "cancelled":
            raise RepairError(ERROR_CANCELLED)
        if outcome.status != "done":
            raise RepairError(ERROR_NOT_ELEVATED)
        consumed = True  # 命令已经跑过：成功与失败都是终局，要再来就重新运行取新令牌
        if outcome.exit_code == EXIT_LINKS:
            raise RepairError(ERROR_HAS_LINKS)  # 提权脚本动手前复查发现了链接，什么都没改
        if outcome.exit_code == EXIT_TOO_MANY:
            raise RepairError(ERROR_TOO_LARGE)  # 同上：条目超限，什么都没改
        if outcome.exit_code != 0 or (record.file and unreadable(record.file)):
            raise RepairError(ERROR_FAILED)
        return {"repaired": True}
    finally:
        _release(token, consumed=consumed)
