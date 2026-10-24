"""`run_command`：任意命令，两档（ADR 0149 §七「#1374 修订」）。

* **第①档（默认）：加固沙盒内执行**（`cmdsandbox.py`）。可写只有命令自己的临时目录、目标 Python 环境前缀、pip / conda 用户缓存；项目与其余一切只读，
  凭据位置连读都拒；允许联网（装包要用）；宿主代理（D-Bus / Apple Events / launchd / 各种本机服务套接字）堵死。每条命令弹 approve 卡，卡上是完整命令。
  没有可用沙盒（Windows、没有 bwrap 的 Linux）→ 不执行，返回 `sandbox_unavailable` 并说明只能走第②档。
* **第②档：沙盒外执行（用户单独点头）**。模型显式 `outside_sandbox: true` + 一句理由才进；第①档因沙盒拒写失败时只在结果里**提示**可以改用，**不自动升级**。
  弹一张单独、醒目的卡（`danger: true`）：完整命令 + 风险一句话 + 理由；只对这一条命令有效。执行前备份项目内全部用户脚本
  （按后缀保守纳入、读原始字节，1000 个 / 32 MiB 封顶，超限直接拒绝第②档），**持这些脚本的锁**跑完命令，事后核对，被改 / 被删的经 `scriptlock` 恢复原字节并报 `scripts_restored`。
  这一层是**尽力而为**，不是绝对保证（见 `docs/rules/backend/ai-agent-bridge.md`「run_command」的已知盲区）。

两档共同：`tier=user_env`；不经 shell（`shlex` 拆）；cwd 默认是命令自己的临时目录（第②档可选项目目录，卡上写明）；去掉密钥类与宿主代理类环境变量；
超时默认 60 s、上限 300 s；输出有界并脱敏；执行前打快照（设置 + 目标环境 freeze）；先过**硬拦表**（命中不问用户直接拒）。
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import scriptlock
from ..agent_runner.wires import ToolResult
from . import cmdsandbox, snapshots, trial
from .base import FixContext, FixTool, err, obj, ok
from .change import _with_snapshot, approve
from .readscope import DENY_HOME_RELATIVE, scrub

COMMAND_TOKENS_MAX = 64
COMMAND_TOKEN_CHARS = 4096
COMMAND_TIMEOUT_DEFAULT_S = 60.0
COMMAND_TIMEOUT_MAX_S = 300.0
#: 命令输出给模型看的最大行数（字节另有 `trial.OUTPUT_TAIL_BYTES` 的尾部上限）
OUTPUT_LINES = 200
#: 第②档备份的上限：脚本个数 / 总字节 / 枚举时看过的目录项数。超了就**只能拒绝第②档**
SCRIPTS_MAX_FILES = 1000
SCRIPTS_MAX_BYTES = 32 * 1024 * 1024
WALK_ENTRIES_MAX = 200_000
#: 等脚本的锁最长多久（别处正占着某份脚本的锁时）
SCRIPTS_LOCK_WAIT_S = 30.0
REASON_MAX_CHARS = 300
CARD_COMMAND_CHARS = 8000

# --------------------------------------------------------------------------- 硬拦表
_SHELLS = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "cmd", "powershell", "pwsh"}
)
_PRIVILEGE = frozenset({"sudo", "doas", "su", "runas", "pkexec"})
_DISK = frozenset(
    {
        "diskpart",
        "format",
        "fdisk",
        "sfdisk",
        "parted",
        "mkswap",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
    }
)
#: 宿主代理的入口：让别的（有权限的）进程替它改东西——桌面总线、Apple Events、launchd、容器、系统服务管理、输入注入、计划任务
_AGENT_ENTRIES = frozenset(
    {
        "osascript",
        "launchctl",
        "open",
        "systemd-run",
        "systemctl",
        "loginctl",
        "machinectl",
        "busctl",
        "gdbus",
        "dbus-send",
        "dbus-launch",
        "dbus-run-session",
        "xdg-open",
        "gio",
        "xdotool",
        "docker",
        "podman",
        "nsenter",
        "chroot",
        "crontab",
        "at",
        "schtasks",
        "sc",
        "reg",
        "wmic",
        "bcdedit",
        "takeown",
        "icacls",
        "vssadmin",
    }
)
#: 转手执行另一个程序的包装器：硬拦表要看穿它们（`env sudo x`、`nohup bash -c …`）
_WRAPPERS = frozenset(
    {
        "env",
        "nice",
        "nohup",
        "time",
        "timeout",
        "command",
        "exec",
        "stdbuf",
        "setsid",
        "ionice",
        "chrt",
        "taskset",
        "xargs",
        "unbuffer",
        "start",
    }
)
_RECURSIVE_TOOLS = frozenset({"rm", "rmdir", "rd", "del", "erase", "chmod", "chown", "chgrp"})
_PIPE_TO_SHELL = re.compile(
    r"\|\s*(?:sudo\s+)?(?:\S*[/\\])?(?:sh|bash|zsh|dash|ksh|csh|tcsh|fish|cmd|powershell|pwsh)(?:\.exe)?\b",
    re.IGNORECASE,
)
_SHELL_SYNTAX = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "2>", "2>&1"})


def _program_name(argv0: str) -> str:
    base = os.path.basename(argv0.replace("\\", "/")).lower()
    return base[:-4] if base.endswith(".exe") else base


def _expand(ctx: FixContext, token: str) -> str:
    if token == "~" or token.startswith(("~/", "~\\")):
        return str(ctx.home) + token[1:]
    return token


def _dangerous_root(ctx: FixContext, token: str) -> bool:
    """这个删除 / 改权限的目标是不是根目录、盘符根、主目录或主目录的上级。"""
    raw = token.strip("\"'")
    if raw in ("*", "/*", "~/*", "\\*"):
        return True
    expanded = _expand(ctx, raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(
            str(ctx.project_root), expanded
        )  # 相对路径相对项目，不是本进程的 cwd
    path = os.path.normcase(os.path.abspath(expanded))
    home = os.path.normcase(os.path.abspath(str(ctx.home)))
    anchor = os.path.normcase(os.path.splitdrive(path)[0] + os.sep)
    if path == anchor or path == os.path.normcase(os.sep):
        return True
    if raw.startswith("$HOME") or raw.startswith("%USERPROFILE%"):
        return True
    return path == home or home.startswith(path.rstrip(os.sep) + os.sep)


def _unwrap(argv: list[str]) -> list[str]:
    """`env FOO=1 nice -n 5 sudo x` → `sudo x`：剥掉包装器及其选项 / 赋值 / 数字参数，回真正被执行的那条 argv（最多剥 4 层）。"""
    cur = argv
    for _ in range(4):
        if not cur or _program_name(cur[0]) not in _WRAPPERS:
            break
        i = 1
        while i < len(cur) and (
            cur[i].startswith("-") or "=" in cur[i] or re.fullmatch(r"\d+(\.\d+)?[smhd]?", cur[i])
        ):
            i += 1
        cur = cur[i:]
    return cur or argv


def hard_block_reason(ctx: FixContext, argv: list[str], text: str) -> str | None:
    """硬拦表：命中就不执行、也不问用户。回理由（给模型看，不含命令内容），不命中回 None。"""
    real = _unwrap(argv)
    prog = _program_name(real[0])
    if prog in _PRIVILEGE:
        return "commands that raise privileges (sudo and the like) are never run"
    if prog in _DISK or prog == "mkfs" or prog.startswith("mkfs."):
        return "disk formatting, partitioning and power commands are never run"
    if prog in _SHELLS:
        return "shells are not started; run the program itself with its arguments"
    if prog in _AGENT_ENTRIES:
        return "programs that ask another process of this computer to act for the command (desktop messages, launchd, services, containers, scheduled tasks) are never run"
    if _PIPE_TO_SHELL.search(text):
        return "piping output into a shell is never run"
    if any(t in _SHELL_SYNTAX for t in argv) or any("$(" in t or "`" in t for t in argv):
        return "shell syntax (pipes, redirects, ;, &&, substitutions) is not supported; run one program with its arguments"
    if prog == "dd" and any(t.startswith("of=/dev/") or t.startswith("of=\\\\.\\") for t in real):
        return "writing to a raw device is never run"
    if prog in _RECURSIVE_TOOLS:
        rest = real[1:]
        recursive = any(
            re.fullmatch(r"-[A-Za-z]*[rR][A-Za-z]*", t) or t in ("--recursive", "/s", "/S")
            for t in rest
        )
        targets = [t for t in rest if not t.startswith("-") and t.lower() not in ("/s", "/q", "/f")]
        if recursive and any(_dangerous_root(ctx, t) for t in targets):
            return "recursive delete / permission changes on the root, a drive or the home folder are never run"
    for token in argv[1:]:
        for part in (token, token.partition("=")[2]):
            if part and "://" not in part and not part.startswith("-"):
                probe = _expand(ctx, part)
                if not os.path.isabs(probe):
                    probe = os.path.join(str(ctx.project_root), probe)
                if ctx.scope.is_denied(probe):
                    return "that location holds credentials or private browser data"
    return None


# --------------------------------------------------------------------------- 参数
def _split_command(command: str) -> list[str]:
    if os.name == "nt":
        return [t.strip("\"'") for t in shlex.split(command, posix=False)]
    return shlex.split(command)


def _command_argv(args: dict) -> list[str] | ToolResult:
    raw_argv, command = args.get("argv"), args.get("command")
    if (raw_argv is None) == (command is None):
        return err("bad_arguments", "Give exactly one of `argv` (a list) or `command` (a string).")
    if raw_argv is not None:
        if not isinstance(raw_argv, list) or not all(isinstance(t, str) for t in raw_argv):
            return err("bad_arguments", "`argv` must be a list of strings.")
        argv = list(raw_argv)
    else:
        if not isinstance(command, str):
            return err("bad_arguments", "`command` must be a string.")
        try:
            argv = _split_command(command)
        except ValueError:
            return err("bad_arguments", "`command` could not be split (unbalanced quotes?).")
    if (
        not argv
        or not argv[0].strip()
        or len(argv) > COMMAND_TOKENS_MAX
        or any(len(t) > COMMAND_TOKEN_CHARS or "\x00" in t for t in argv)
    ):
        return err("bad_arguments", "The command is empty, too long, or contains a NUL character.")
    return argv


def _shown(argv: list[str]) -> str:
    return shlex.join(argv) if os.name != "nt" else subprocess.list2cmdline(argv)


# --------------------------------------------------------------------------- 第①档：可写清单
#: pip / conda 的用户缓存目录（相对主目录的**标准位置**；不读用户的 pip.conf / condarc）
_CACHE_DIRS = (".cache/pip", "Library/Caches/pip", ".conda")


def _python_prefix(python: str) -> str:
    p = Path(os.path.abspath(python)).parent
    return str(p.parent if p.name.lower() in ("bin", "scripts") else p)


def _contains(outer: str, inner: str) -> bool:
    outer, inner = os.path.normcase(outer), os.path.normcase(inner)
    return inner == outer or inner.startswith(outer.rstrip(os.sep) + os.sep)


def _prefix_python(prefix: str) -> str | None:
    """环境前缀里的解释器（分类要拿解释器判种类）；找不到回 None。"""
    for rel in ("bin/python", "bin/python3", "Scripts/python.exe", "python.exe"):
        cand = os.path.join(prefix, *rel.split("/"))  # 按段拼：Windows 上不留混合分隔符
        if os.path.isfile(cand):
            return cand
    return None


def _protected_roots() -> list[str]:
    """Tavotto 自己的安装目录 / 应用包 / 内置 runtime（根 AGENTS.md：不往包目录、安装目录写任何东西）。"""
    from .. import runtime

    roots = [str(Path(__file__).resolve().parents[2])]  # tavotto 包目录
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(str(meipass))
    try:
        rt = runtime.runtime_root()
        if rt:
            roots.append(rt)
        bundled = runtime.bundled_python()
        if bundled:
            roots.append(_python_prefix(bundled))
    except Exception:  # noqa: BLE001 - 取不到只是少一个保护根
        pass
    return [os.path.realpath(r) for r in roots if r]


def writable_envs(ctx: FixContext) -> list[tuple[str, str]]:
    """命令获准写入的 Python 环境 `[(真实前缀, 解释器)]`（去重）：来源是 `ctx.env_prefixes`（取前缀里的解释器）与 `ctx.environments`，
    **所有候选统一**按 `convertdeps.describe_env` 判种类——系统自带 / 内置 runtime 一律不可写；落在 Tavotto 安装目录 / 应用包 / 内置 runtime 里
    （或盖住它们）的前缀不可写；找不到解释器的前缀（分不出种类）不可写；浅到只有一两级的路径（`/usr`、`/opt`）不可写。"""
    from .. import convertdeps

    cands: list[tuple[str, str]] = []
    for p in ctx.env_prefixes:
        if os.path.isdir(str(p)):
            py = _prefix_python(str(p))
            if py:
                cands.append((str(p), py))
    for py in ctx.environments.values():
        cands.append((_python_prefix(py), py))
    protected = _protected_roots()
    out: list[tuple[str, str]] = []
    for prefix, py in cands:
        if not os.path.isdir(prefix):
            continue
        real = os.path.realpath(prefix)
        if len(Path(real).parts) <= 2 or any(r == real for r, _ in out):
            continue
        if any(_contains(real, r) or _contains(r, real) for r in protected):
            continue
        try:
            kind = convertdeps.describe_env(py, "")["kind"]
        except Exception:  # noqa: BLE001 - 分不出种类就按不可写
            continue
        if kind in (convertdeps.KIND_SYSTEM, convertdeps.KIND_BUILTIN):
            continue
        out.append((real, py))
    return out


def _writable_env_prefixes(ctx: FixContext) -> list[str]:
    return [prefix for prefix, _py in writable_envs(ctx)]


def command_writable_dirs(ctx: FixContext) -> list[str]:
    """第①档在沙盒里除了自己的临时工作目录之外**可写**的目录清单：环境前缀 + pip / conda 的用户缓存。项目目录与其余一切都不可写。"""
    found = _writable_env_prefixes(ctx)
    for rel in _CACHE_DIRS:
        c = str(ctx.home / rel)
        if os.path.isdir(c):
            real = os.path.realpath(c)
            if real not in found:
                found.append(real)
    return found


def _hidden_paths(ctx: FixContext) -> list[str]:
    """沙盒里连读都拒的凭据位置：`DENY_HOME_RELATIVE` 里实际存在的（真实路径）。"""
    out: list[str] = []
    for rel in DENY_HOME_RELATIVE:
        p = ctx.home / rel
        if os.path.lexists(p):
            real = os.path.realpath(p)
            if real not in out:
                out.append(real)
    return out


def commands_root() -> Path:
    """命令的临时工作目录的家：Tavotto 的缓存目录（`trial._remove_tree` + `.cleanup_pending` + sweep 的同一套清理）。"""
    from .. import config

    return config.data_dir() / "cache" / "fix_commands"


def _snapshot_env_ids(ctx: FixContext) -> list[str]:
    """快照要覆盖的环境 id：**命令实际获准写入的所有环境**（与可写清单 `writable_envs` 同源）。
    没登记过 id 的（例如 `change_python` 之外靠 `env_prefixes` 进来的）现场登记一个，快照才拿得到它的 freeze。"""
    out: list[str] = []
    for prefix, py in writable_envs(ctx):
        eid = next(
            (
                e
                for e, p in ctx.environments.items()
                if os.path.realpath(_python_prefix(p)) == prefix
            ),
            None,
        )
        if eid is None:
            eid = "cmd_env_" + hashlib.sha1(prefix.encode("utf-8", "replace")).hexdigest()[:8]
            ctx.environments[eid] = py
        out.append(eid)
    return out


# --------------------------------------------------------------------------- 第②档：备份项目内的用户脚本
_SCRIPT_SUFFIXES = frozenset({".py", ".pyw", ".r", ".m"})
#: 枚举时不进的目录名（环境 / 缓存 / 版本库——不是用户脚本，也会把个数撑爆）
_WALK_SKIP = trial._COPY_SKIP | {".tox", "site-packages", ".mypy_cache", ".pytest_cache", ".idea"}


def list_scripts(ctx: FixContext) -> list[Path] | None:
    """项目内的用户脚本（真实路径、去重）：**按后缀保守纳入**（`.py` / `.pyw` / `.R` / `.r` / `.m`，读原始字节、不要求能解码——
    cp1252 / GBK 等声明了编码的合法 `.py` 同样护着；判不出语言的 `.m` 也备份，宁多勿漏）。
    跟随目录符号链接（按真实路径去重防环；指向 `ReadScope` 硬拦区的不进）。超过个数 / 字节 / 目录项上限回 None（调用方只能拒绝第②档）。"""
    root = os.path.realpath(str(ctx.project_root))
    env_dirs = [os.path.realpath(str(p)) for p in ctx.env_prefixes]
    seen_dirs: set[str] = set()
    found: dict[str, Path] = {}
    total = entries = 0
    stack = [root]
    while stack:
        d = stack.pop()
        key = os.path.normcase(d)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            it = list(os.scandir(d))
        except OSError:
            continue
        for e in it:
            entries += 1
            if entries > WALK_ENTRIES_MAX:
                return None
            try:
                if e.is_dir():  # 跟随链接
                    real = os.path.realpath(e.path)
                    if e.name in _WALK_SKIP or any(_contains(x, real) for x in env_dirs):
                        continue
                    if ctx.scope.is_denied(real):
                        continue
                    stack.append(real)
                    continue
                if not e.is_file() or Path(e.name).suffix.lower() not in _SCRIPT_SUFFIXES:
                    continue
                real = os.path.realpath(e.path)
                size = os.stat(real).st_size
            except OSError:
                continue
            if real in found:
                continue
            found[real] = Path(real)
            total += size
            if len(found) > SCRIPTS_MAX_FILES or total > SCRIPTS_MAX_BYTES:
                return None
    return sorted(found.values(), key=lambda p: os.path.normcase(str(p)))


def _baseline(paths: list[Path]) -> tuple[dict[Path, tuple[bytes, int | None]], list[Path]]:
    """（锁内）读每份脚本的原字节与权限位。读不了的单独列出（命令之后无法核对它们，如实报）。"""
    base: dict[Path, tuple[bytes, int | None]] = {}
    unreadable: list[Path] = []
    for p in paths:
        try:
            st = os.stat(p)
            base[p] = (p.read_bytes(), stat.S_IMODE(st.st_mode))
        except OSError:
            unreadable.append(p)
    return base, unreadable


def _verify_and_restore(
    base: dict[Path, tuple[bytes, int | None]],
) -> tuple[list[Path], list[Path]]:
    """（锁内）命令之后逐份核对：被改 / 被删的用原字节经 `scriptlock` 写回。回 (已恢复, 没能恢复)。"""
    restored: list[Path] = []
    failed: list[Path] = []
    for p, (orig, mode) in base.items():
        try:
            cur: bytes | None = p.read_bytes()
        except FileNotFoundError:
            cur = None
        except OSError:
            cur = b"\x00unreadable"
        if cur == orig:
            continue
        if snapshots.restore_script(p, orig, mode=mode):
            restored.append(p)
        else:
            failed.append(p)
    return restored, failed


def _names(project: Path, paths: list[Path], limit: int = 8) -> str:
    shown: list[str] = []
    for p in paths[:limit]:
        try:
            shown.append(p.relative_to(os.path.realpath(project)).as_posix())
        except ValueError:
            shown.append(p.name)
    return ", ".join(shown) + (", ..." if len(paths) > limit else "")


# --------------------------------------------------------------------------- 执行
_SANDBOX_DENIED = re.compile(
    r"Operation not permitted|Read-only file system|\[Errno (?:1|13|30)\]|PermissionError|Permission denied|EPERM|EROFS",
    re.IGNORECASE,
)

_OUTSIDE_HINT = (
    "sandbox_denied: the command failed with what looks like a refused write. It ran inside the protective "
    "sandbox, which only lets it write to its own scratch folder, this project's Python environment and "
    "pip/conda caches. If it really must write somewhere else, call run_command again with "
    "`outside_sandbox: true` and a one-sentence `reason`: the user is then asked separately. "
    "Nothing was run outside the sandbox."
)

_NEEDS_OUTSIDE = (
    "A protective sandbox is not available on this computer (it needs macOS sandbox-exec or Linux bwrap), so "
    "the command was not run. If it is really needed, call run_command again with `outside_sandbox: true` and "
    "a one-sentence `reason`: the user is then asked separately and must agree to run it outside any sandbox. "
    "Otherwise use a dedicated tool (install_packages, select_environment, switch_execution, change_python, "
    "convert_script)."
)


# --------------------------------------------------------------------------- 进程树
#: 命令退出后，等它起的后台进程被结束并确认退出的上限
TREE_REAP_S = 5.0


class _WinJob:
    """Windows：把命令放进 Job Object（`KILL_ON_JOB_CLOSE`），收尾时 `TerminateJobObject` 并等 `ActiveProcesses` 归零——
    后台孙进程在直接进程退出之后也在作业里。**未在 Windows 上实测**（本机是 macOS）；起不来就退回 `taskkill /T`。
    已知窗口：进程从起来到被放进作业之间的瞬间起的子进程不在作业里。"""

    def __init__(self, proc: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        self._k = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._ct = ctypes
        k = self._k
        k.CreateJobObjectW.restype = wintypes.HANDLE
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObject failed")

        class _Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class _Ext(ctypes.Structure):
            _fields_ = [
                ("Basic", _Basic),
                ("IO", _IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = _Ext()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ) or not k.AssignProcessToJobObject(self.handle, int(proc._handle)):  # type: ignore[attr-defined]
            k.CloseHandle(self.handle)
            raise OSError("job setup failed")

    def active(self) -> int:
        ct = self._ct

        class _Acct(ct.Structure):
            _fields_ = [
                ("t", ct.c_int64 * 4),
                ("faults", ct.c_uint32),
                ("total", ct.c_uint32),
                ("active", ct.c_uint32),
                ("term", ct.c_uint32),
            ]

        a = _Acct()
        if not self._k.QueryInformationJobObject(self.handle, 1, ct.byref(a), ct.sizeof(a), None):
            return 1
        return int(a.active)

    def terminate_and_wait(self, limit_s: float) -> bool:
        self._k.TerminateJobObject(self.handle, 1)
        deadline = time.monotonic() + limit_s
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.02)
        gone = self.active() == 0
        self._k.CloseHandle(self.handle)
        return gone


def _attach_job(proc: subprocess.Popen) -> _WinJob | None:
    if os.name != "nt":
        return None
    try:
        return _WinJob(proc)
    except Exception:  # noqa: BLE001 - 退回 taskkill /T
        return None


def _group_has_live_members(pgid: int, proc_root: str = "/proc") -> bool | None:
    """Linux：进程组 `pgid` 里还有没有**能执行**的进程（扫 `/proc/<pid>/stat`，僵尸 `Z` / 已死 `X` 不算）。
    PID 1 不回收孤儿的容器里，被 SIGKILL 的后台子进程会以僵尸留在组里，`killpg` 永远不报 `ProcessLookupError`——
    僵尸改不了任何文件，不能当成「还有进程在跑」。没有 `/proc`（macOS 等）回 None，由调用方照旧按 `killpg` 判。"""
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return None
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, name, "stat"), "rb") as fh:
                raw = fh.read().decode("ascii", "replace")
        except OSError:
            continue  # 刚好退出了
        rest = raw[raw.rfind(")") + 1 :].split()
        if len(rest) < 3:
            continue
        state, pgrp = rest[0], rest[2]
        if pgrp == str(pgid) and state not in ("Z", "X", "x"):
            return True
    return False


def _reap_tree(proc: subprocess.Popen, job: _WinJob | None) -> bool:
    """命令的直接进程退出（或被叫停）之后，**结束并确认整个受管进程树退出**，回是否确认干净。
    POSIX：命令在自己的新会话 / 进程组里（`start_new_session`），`killpg(SIGKILL)` 到组里一个都不剩（`ProcessLookupError`）才算；
    Windows：Job Object 终止并等活动进程归零，作业建不起来就 `taskkill /T`（只确认直接进程）。
    不然后台孙进程会绕过时限，并在第②档核对、放锁之后才去改脚本。
    已知盲区：自己 `setsid` / 再开新会话逃出进程组的进程 POSIX 上追不到（Linux 的 bwrap 有 `--unshare-pid`，沙盒里随 init 退出全部被收掉）。"""
    if os.name == "nt":
        if job is not None:
            return job.terminate_and_wait(TREE_REAP_S)
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return proc.poll() is not None
    deadline = time.monotonic() + TREE_REAP_S
    while True:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True  # 组里一个都不剩
        except OSError:
            return False
        if _group_has_live_members(proc.pid) is False:
            return True  # 组里只剩僵尸（不回收孤儿的容器）：没有能再改文件的进程了
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _spawn(
    ctx: FixContext,
    argv: list[str],
    work: Path,
    cwd: str,
    prefix: list[str],
    timeout: float,
    project: str,
) -> tuple[ToolResult, int | None]:
    """起子进程并收输出（两档共用），回 (结果, 退出码；没起来 / 超时为 None)。`prefix` 是沙盒前缀（第②档为空）。"""
    from .. import runtime

    env = cmdsandbox.clean_env()
    scratch = str(work)
    env.update(
        TMPDIR=scratch,
        TEMP=scratch,
        TMP=scratch,
        PYTHONDONTWRITEBYTECODE="1",  # 项目只读：别让 Python 往 __pycache__ 写
        TAVOTTO_PROJECT=project,  # 命令的 cwd 默认是临时目录；项目在这儿
    )
    try:
        proc = subprocess.Popen(
            [*prefix, *argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            creationflags=runtime.CREATE_NO_WINDOW,
            start_new_session=(os.name != "nt"),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return (
            err(
                "spawn_failed",
                _with_snapshot(ctx, "The program could not be started (is it installed?)."),
            ),
            None,
        )
    job = _attach_job(proc)
    overflow = threading.Event()

    def stop() -> None:
        overflow.set()
        trial._kill_own(proc)

    cap = trial._Capture(proc.stdout, {"bytes": 0}, threading.Lock(), stop)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        trial._kill_own(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    clean = _reap_tree(proc, job)  # 后台子进程一并结束并确认，再收输出、再让调用方核对 / 放锁
    cap.thread.join(timeout=5)
    try:
        proc.stdout.close()
    except OSError:
        pass
    tail = "\n".join(cap.text().splitlines()[-OUTPUT_LINES:])
    notes = ""
    if overflow.is_set():
        notes += (
            f"\noutput_truncated: more than {trial.OUTPUT_LIMIT_BYTES // (1024 * 1024)} MiB of output, "
            "the program was stopped; only the last part is kept"
        )
    if not clean:
        notes += (
            "\nprocesses_left: some background processes the command started could not be confirmed "
            "stopped; tell the user"
        )
    body = _with_snapshot(
        ctx, f"exit code: {proc.returncode}{notes}\n--- output (last lines) ---\n{tail}"
    )
    if timed_out and not overflow.is_set():
        return (
            err(
                "timeout",
                f"The command did not finish within {int(timeout)} s and was stopped.\n{body}",
            ),
            None,
        )
    return ok(body, f"exit {proc.returncode}"), proc.returncode


def _timeout_of(args: dict) -> float:
    t = args.get("timeout_s")
    if isinstance(t, int | float) and not isinstance(t, bool):
        return float(min(max(t, 1), COMMAND_TIMEOUT_MAX_S))
    return COMMAND_TIMEOUT_DEFAULT_S


def run_command(ctx: FixContext, args: dict) -> ToolResult:
    ctx.call_meta["sandbox"] = (
        "none"  # 审计：没走到起进程之前一律是 none；起进程的那一刻改成 hardened / outside
    )
    argv = _command_argv(args)
    if isinstance(argv, ToolResult):
        return argv
    outside = args.get("outside_sandbox")
    if outside is not None and not isinstance(outside, bool):
        return err("bad_arguments", "`outside_sandbox` must be true or false.")
    reason = args.get("reason")
    if reason is not None and not isinstance(reason, str):
        return err("bad_arguments", "`reason` must be a string.")
    cwd_mode = args.get("cwd", "scratch")
    if cwd_mode not in ("scratch", "project"):
        return err("bad_arguments", "`cwd` must be `scratch` or `project`.")
    if cwd_mode == "project" and not outside:
        return err(
            "bad_arguments",
            "Only a command run outside the sandbox (`outside_sandbox: true`) can use the project folder as its "
            "working directory; inside the sandbox the project is read-only, its path is in $TAVOTTO_PROJECT.",
        )
    if outside and not (isinstance(reason, str) and reason.strip()):
        return err(
            "bad_arguments", "`outside_sandbox` needs a one-sentence `reason` the user will read."
        )
    shown = _shown(argv)
    if len(shown) > CARD_COMMAND_CHARS:
        return err(
            "command_too_long_to_show",
            f"The command is longer than the {CARD_COMMAND_CHARS} characters the approval card can show in full, "
            "so it was not run (the user must see exactly what runs). Put the long part in a file inside the "
            "environment or the scratch folder, or split the command.",
        )
    if isinstance(reason, str) and len(reason.strip()) > REASON_MAX_CHARS:
        return err("bad_arguments", f"`reason` must be at most {REASON_MAX_CHARS} characters.")
    why = hard_block_reason(ctx, argv, " ".join(argv))
    if why is not None:
        return err("hard_blocked", f"Refused: {why}.")
    project = str(ctx.project_root)
    if not os.path.isdir(project):
        return err("no_project", "The project folder is not there.")
    timeout = _timeout_of(args)
    if outside:
        return _run_outside(ctx, argv, reason.strip(), cwd_mode, timeout, project)
    return _run_hardened(ctx, argv, timeout, project)


# ----- 第①档
def _run_hardened(ctx: FixContext, argv: list[str], timeout: float, project: str) -> ToolResult:
    found = cmdsandbox.sandbox_kind()
    if found is None:
        return err("sandbox_unavailable", _NEEDS_OUTSIDE)
    writable = command_writable_dirs(ctx)
    real_project = os.path.realpath(project)
    if any(_contains(w, real_project) for w in writable):
        return err(
            "env_contains_project",
            "A Python environment's folder contains the project folder, so the project could not be kept "
            "read-only while a command runs. Nothing was run.",
        )
    # 项目内的环境（项目 .venv）只放那个子目录；项目本身与其上级永远不可写——上面已拒
    shown = _shown(argv)
    denied = approve(
        ctx,
        "run_command",
        "Run this command in a protected environment: it can read your files but cannot change your "
        "project or the rest of your computer.",
        command=scrub(shown),
        cwd="scratch",
        sandbox="hardened",
    )
    if denied is not None:
        return denied
    if snapshots.take(ctx, "run_command", _snapshot_env_ids(ctx)) is None:
        return err(
            "snapshot_failed", "Could not save a snapshot to roll back to, so nothing was changed."
        )
    work = _make_work(ctx)
    if isinstance(work, ToolResult):
        return work
    kind, tool = found
    prefix = cmdsandbox.prefix(
        kind, tool, work, writable, project=project, hidden=_hidden_paths(ctx)
    )
    ctx.call_meta["sandbox"] = "hardened"
    ctx.call_meta["command_tier"] = 1
    try:
        res, code = _spawn(ctx, argv, work, str(work), prefix, timeout, project)
    finally:
        pending = not trial._remove_tree(work)
    if pending:
        res.content += _PENDING
    res.content += f"\nos_sandbox: {kind} (hardened)"
    if code and _SANDBOX_DENIED.search(res.content):
        res.content += "\n" + _OUTSIDE_HINT
    res.mutated = True  # 命令可能改了环境
    return res


_PENDING = (
    "\ncleanup_pending: the scratch folder could not be fully deleted; it is kept in Tavotto's "
    "cache folder and will be removed later."
)


def _make_work(ctx: FixContext) -> Path | ToolResult:
    trial.sweep_stale_trials(root=commands_root())
    work = commands_root() / uuid.uuid4().hex
    try:
        work.mkdir(parents=True)
    except OSError:
        trial._remove_tree(work)
        return err("scratch_failed", _with_snapshot(ctx, "Could not make a scratch folder."))
    return work


# ----- 第②档
def _run_outside(
    ctx: FixContext, argv: list[str], reason: str, cwd_mode: str, timeout: float, project: str
) -> ToolResult:
    scripts = list_scripts(ctx)  # 审批前先做规模预检：超限不问用户，只拒
    if scripts is None:
        return err(
            "scripts_too_large",
            f"This project has too many scripts to back up (more than {SCRIPTS_MAX_FILES} files or "
            f"{SCRIPTS_MAX_BYTES // (1024 * 1024)} MiB), and running outside the sandbox is only offered "
            "when they can all be backed up. Nothing was run. Use the sandboxed mode or a dedicated tool.",
        )
    shown = _shown(argv)
    where = (
        "the project folder"
        if cwd_mode == "project"
        else "a temporary folder (the project is at $TAVOTTO_PROJECT)"
    )
    details = (
        f"It runs in {where}.\n"
        f"Tavotto backs up this project's {len(scripts)} script file(s) before it runs and puts back any that "
        "the command changes or deletes. That is a best-effort safeguard, not a guarantee: it does not cover "
        "other files, anything outside the project, or edits made by other programs while the command runs."
    )
    denied = approve(
        ctx,
        "run_command",
        "This command will run OUTSIDE the safety sandbox and can change files on your computer.",
        danger=True,
        command=scrub(shown),
        reason=scrub(reason),
        cwd=cwd_mode,
        scripts=len(scripts),
        sandbox="outside",
        details=details,
    )
    if denied is not None:
        return denied
    if snapshots.take(ctx, "run_command", _snapshot_env_ids(ctx)) is None:
        return err(
            "snapshot_failed", "Could not save a snapshot to roll back to, so nothing was changed."
        )
    work = _make_work(ctx)
    if isinstance(work, ToolResult):
        return work
    # 审批之后才取基线并持锁：审批卡开着期间的编辑已经在基线里，不会被当成命令的改动；
    # 持锁期间界面保存脚本会阻塞等待（不是失败），命令结束才落盘。编辑器等外部程序不走这把锁，见已知盲区
    try:
        with scriptlock.script_guards(scripts, timeout=SCRIPTS_LOCK_WAIT_S):
            base, unreadable = _baseline(scripts)
            ctx.call_meta["sandbox"] = "outside"
            ctx.call_meta["command_tier"] = 2
            cwd = project if cwd_mode == "project" else str(work)
            try:
                res, _code = _spawn(ctx, argv, work, cwd, [], timeout, project)
            finally:
                restored, failed = _verify_and_restore(base)
    except TimeoutError:
        trial._remove_tree(work)
        ctx.call_meta["sandbox"] = "none"
        return err(
            "scripts_busy",
            "Some scripts are in use by Tavotto right now (a save or an edit is in progress), so the backup "
            "could not be taken. Nothing was run.",
        )
    pending = not trial._remove_tree(work)
    if pending:
        res.content += _PENDING
    res.content += "\nos_sandbox: none (ran outside the sandbox, with the user's approval)"
    res.content += f"\nscripts_backed_up: {len(base)}"
    if unreadable:
        res.content += (
            f"\nscripts_not_backed_up: {len(unreadable)} script(s) could not be read ({_names(ctx.project_root, unreadable)}); "
            "if the command changed them they could not be put back"
        )
    if restored:
        res.content += (
            f"\nscripts_restored: {len(restored)} ({_names(ctx.project_root, restored)}) - the command changed or "
            "deleted these scripts; their original contents were put back"
        )
    if failed:
        res.content += (
            f"\nscripts_restore_failed: {len(failed)} ({_names(ctx.project_root, failed)}) - the command changed "
            "or deleted these scripts and the originals could NOT be put back. Tell the user."
        )
        res.is_error = True
        res.code = "scripts_restore_failed"
    res.mutated = True
    return res


# --------------------------------------------------------------------------- 描述与登记
COMMAND_TOOLS: tuple[FixTool, ...] = (
    FixTool(
        "run_command",
        "Run one program with arguments after the user approves it. PREFER the dedicated tools (install_packages, "
        "change_python, select_environment, switch_execution, convert_script); use run_command only when they "
        "cannot do it. Two modes. DEFAULT (protected): it runs inside an operating-system sandbox: the project and "
        "everything else on the computer is READ-ONLY for it (credential folders are not even readable), and it can "
        "only write to its own scratch folder (working directory and TMPDIR, deleted afterwards), the Python "
        "environment of this project and pip/conda caches; it has network access (pip/conda/R installs work) but "
        "cannot reach desktop services, Apple Events, launchd or other local services. The project path is in "
        "$TAVOTTO_PROJECT; use absolute paths to read project files. If no sandbox exists on this computer (Windows, "
        "Linux without bwrap) the answer is `sandbox_unavailable` and nothing runs. If a protected run fails because "
        "a write was refused, the result says so; you may then ask for the second mode, never automatically. SECOND "
        "MODE (`outside_sandbox: true` + a one-sentence `reason`): runs with no sandbox and can change files on the "
        "user's computer; the user gets a separate, prominent card and must agree to this one command; Tavotto backs "
        "up the project's scripts and puts back any the command changed, as a best-effort safeguard. Ask for it only "
        "when the command must write outside the environment, and say why. `cwd: project` is only for the second "
        "mode. Give `argv` (a list) or `command` (split into words; no shell: no pipes, redirects, `&&`, "
        "substitutions). Never runs sudo, shells, disk tools, desktop/launchd/service tools, piping into a shell or "
        "recursive deletes of the root or home folder; secret-looking environment variables are removed; output is "
        "cut and masked; there is a time limit. A command longer than 8000 characters is refused with "
        "`command_too_long_to_show` (the user always sees exactly what runs). Takes a snapshot of the settings and the environment's packages.",
        obj(
            {
                "argv": {"type": "array", "items": {"type": "string"}},
                "command": {"type": "string"},
                "timeout_s": {
                    "type": "integer",
                    "description": "Seconds before it is stopped (default 60, max 300).",
                },
                "outside_sandbox": {
                    "type": "boolean",
                    "description": "Second mode: run without the sandbox after a separate, prominent user approval. Needs `reason`.",
                },
                "reason": {
                    "type": "string",
                    "description": "One plain sentence for the user: why the command must run outside the sandbox.",
                },
                "cwd": {
                    "type": "string",
                    "enum": ["scratch", "project"],
                    "description": "Working directory: `scratch` (default) or, in the second mode only, `project`.",
                },
            }
        ),
        run_command,
        tier="user_env",
        waits_for_user=True,
    ),
)
