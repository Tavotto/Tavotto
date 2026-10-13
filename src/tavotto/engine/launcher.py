"""Launcher（P09）：用户环境需要「激活」才能跑时，worker 怎么被**包一层**起起来。

解释器选择与启动方式是两件事：同一个 `python` 路径，裸起与经环境管理器激活后起，看到的环境变量 / DLL 搜索路径 /
`activate.d` 钩子并不相同。Conda 环境里的受控 SDK（厂商 SDK 常在 `activate.d` 里设变量）单起 `bin/python` 会 import 失败，
而 `conda run` 起的同一解释器成功。本模块只做**结构化的启动包装**，不做环境管理：

* `CondaLauncher` 是不可变的结构化描述（conda 可执行文件 + prefix），`wrap()` 产出 **argv 数组**，
  绝不拼 shell 字符串。语义按官方 `conda run`（<https://docs.conda.io/projects/conda/en/stable/commands/run.html>，
  2026-10-13 核对）：`-p/--prefix PATH` 指环境、`-s/--no-capture-output/--live-stream` 不捕获 stdout/stderr
  （worker 的 v1 协议跑在 stdout 上，必须直通）、`--cwd CWD` 设工作目录。**不带 `--` 分隔符**：`conda run` 的命令部分是
  argparse 的 REMAINDER，从第一个非选项的位置参数（可执行文件）起，后面的参数——含 `-u`、`-X`、`-c`、`--flag` 这类以 `-` 开头的——
  都原样透传；而较老的 conda（实测 24.5.0，conda/conda#13639）不认 `--`，会把它当成可执行文件报 `--: command not found`。
  所以可执行文件必须是绝对路径、不以 `-` 开头（`wrap()` 强制）。
* 持久化的只有**结构化引用**（`public()`：种类 + conda 路径 + prefix），**激活后的环境变量一个都不存**——那是秘密面
  （SDK 的许可证串 / 令牌常在里面）；激活由 conda 在每次启动时重新做。
* `GroupPopen`：包装后的进程树是「conda → 激活包装脚本 → 用户 Python」。只杀 conda 会留下仍在跑的用户 Python，
  所以包装 worker 自成进程组（POSIX 新会话 / Windows 新进程组），`kill()` 杀整组，`wait()` 在 wrapper 退出后确认组里
  没有残留（有就补杀）。`pool.EngineWorker` 的每一处 `proc.kill()` / `proc.wait()` 因此都自动覆盖整棵树。
* 只在 Python 池（`EngineWorker`）上用：workerd（Rust supervisor）的杀进程语义不认进程组，`WorkerdWorker` 遇到需要
  launcher 的解释器抛 `WorkerdUnavailable`，由 `_new_worker` 回退到 Python 池。

不碰：Windows `conda.bat`（需要 `cmd.exe` 引号规则，不拼 shell）、mamba / micromamba（选项集不同，另列样本门）、
内置 runtime 与 Tavotto 自己的受管环境（venv，没有 `conda-meta`）。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from . import config, runtime

LOG = logging.getLogger("tavotto.engine")

KIND_DIRECT = "direct"
KIND_CONDA = "conda"
KINDS = (KIND_DIRECT, KIND_CONDA)

#: 关掉自动 Conda 激活（排障 / 回到 P09 之前的裸起行为）
ENV_DISABLE = "TAVOTTO_CONDA_ACTIVATION"
_OFF_VALUES = frozenset({"0", "off", "false", "no"})

#: wrapper 退出后等「组里没有残留」的上限（秒）。超过只记日志，不无限等。
GROUP_REAP_TIMEOUT = 5.0


class LauncherUnavailable(RuntimeError):
    """记录说该经 Conda 激活启动、现在启动器不可用。`code` 是稳定码，pool 把它转成 `WorkerError`。"""

    code = "conda_launcher_unavailable"


def _is_windows() -> bool:
    return os.name == "nt"


def disabled() -> bool:
    return os.environ.get(ENV_DISABLE, "").strip().lower() in _OFF_VALUES


def _is_executable(path: str | os.PathLike) -> bool:
    p = Path(path)
    return p.is_file() and os.access(p, os.X_OK)


def conda_prefix_of(python: str | os.PathLike) -> str | None:
    """解释器所在的 Conda 环境 prefix；不是 Conda 环境返回 None。纯 `stat`，不起进程。

    判据是 prefix 下有 `conda-meta/` 目录（`conda create` 必建；venv / uv / 内置 runtime 没有）。
    POSIX 解释器在 `<prefix>/bin/python*`，Windows 在 `<prefix>/python.exe`。**不 resolve 软链接**：
    我们要的是用户指给我们的那条路径所在的环境，不是它指向的基础解释器。"""
    p = Path(os.path.abspath(str(python)))
    candidates = [p.parent] if os.name == "nt" else [p.parent.parent]
    if os.name == "nt":
        candidates.append(p.parent.parent)  # Scripts\python.exe 之类
    for prefix in candidates:
        if (prefix / "conda-meta").is_dir():
            return str(prefix)
    return None


def _conda_candidates(prefix: str) -> list[str]:
    """prefix 对应的 conda 可执行文件候选（按可信度排序）。"""
    pre = Path(prefix)
    # `<root>/envs/<name>` → root；否则 prefix 自己可能就是 base
    roots = [pre.parent.parent] if pre.parent.name == "envs" else []
    roots.append(pre)
    out: list[str] = []
    for root in roots:
        if os.name == "nt":
            out.append(str(root / "Scripts" / "conda.exe"))
        else:
            out.append(str(root / "bin" / "conda"))
    env_exe = os.environ.get("CONDA_EXE")
    if env_exe:
        out.append(env_exe)
    which = shutil.which("conda")
    if which:
        out.append(which)
    return out


def find_conda_exe(prefix: str) -> str | None:
    for cand in _conda_candidates(prefix):
        # Windows 的 `conda.bat` 需要 cmd.exe 引号规则，本期不支持（不拼 shell）
        if cand.lower().endswith((".bat", ".cmd")):
            continue
        if _is_executable(cand):
            return os.path.abspath(cand)
    return None


def prefix_state(prefix: str) -> str:
    """环境 prefix 的「版本」：`conda-meta/history` 每次事务（装 / 卸 / 升降包）都会追加，取它的 (size, mtime_ns)
    + `conda-meta` 目录的 mtime_ns。装包 / 升级换值；纯读不换。不含路径。读不到回空串。"""
    meta = Path(prefix) / "conda-meta"
    parts: list = []
    for p in (meta / "history", meta):
        try:
            st = os.stat(p)
        except OSError:
            parts.append(None)
            continue
        parts.append((st.st_size if p.name == "history" else 0, st.st_mtime_ns))
    if all(x is None for x in parts):
        return ""
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class CondaLauncher:
    """`conda run` 的结构化描述。不可变；只含路径，不含环境变量。"""

    conda_exe: str
    prefix: str
    kind: str = KIND_CONDA

    def __post_init__(self) -> None:
        if not self.conda_exe or not self.prefix:
            raise ValueError("CondaLauncher 需要 conda_exe 与 prefix")

    def wrap(self, argv: list[str] | tuple[str, ...], *, cwd: str | None = None) -> list[str]:
        """被执行命令 `argv` → 经 `conda run` 的完整 argv 数组（每个 token 一项；可执行文件之后原样透传）。

        `--no-capture-output` 恒带：worker 的协议在 stdout 上，被 conda 捕获 = 协议死锁。不带 `--`（见模块头）；
        `argv[0]` 必须是绝对路径——它是 REMAINDER 的起点，以 `-` 开头会被 conda 当成它自己的选项。"""
        if not argv:
            raise ValueError("wrap 需要非空 argv")
        exe = str(argv[0])
        if not os.path.isabs(exe) or exe.startswith("-"):
            raise ValueError(f"wrap 需要绝对路径的可执行文件（不能以 - 开头）: {exe!r}")
        out = [self.conda_exe, "run", "--no-capture-output", "--prefix", self.prefix]
        if cwd:
            out += ["--cwd", str(cwd)]
        out.extend(str(a) for a in argv)
        return out

    def public(self) -> dict:
        """可持久化的结构化引用（**没有**激活后的环境变量）。只用来对账，**不得**读回后当可执行路径：
        要执行的 conda 永远由 `detect()` 从解释器现场找。"""
        return {"kind": self.kind, "conda_exe": self.conda_exe, "prefix": self.prefix}


def detect(python: str | os.PathLike) -> CondaLauncher | None:
    """这条解释器要不要、能不能经 Conda 激活起：是 Conda 环境且找得到 conda 可执行文件 → launcher；否则 None
    （None = 裸起，与 P09 之前一致）。`TAVOTTO_CONDA_ACTIVATION=0` 关闭。"""
    if disabled():
        return None
    if _is_windows():
        # 本期 Windows 不做 Conda 包装：没有真机验证（conda.exe 经中间 shell 起 Python 时的回执 / 进程树 / DLL 搜索路径），
        # 也没有可靠的进程组核验，包装了回执只会恒 partial。Windows 保持 P09 之前的裸起，真机验证后再开。
        return None
    prefix = conda_prefix_of(python)
    if prefix is None:
        return None
    exe = find_conda_exe(prefix)
    if exe is None:
        return None
    return CondaLauncher(conda_exe=exe, prefix=prefix)


# ---------------------------------------------------------------------------
# 预检探针：与 worker 同一个启动上下文
# ---------------------------------------------------------------------------
#: 本次探针该用的启动决策（`(python) -> CondaLauncher | None`）。由知道「这是哪个项目的哪个脚本」的调用方设置
#: （`pool._resolve_for_worker`、`deprepair.decide_environment_pinned`），探针与 worker 才用同一个「每脚本启动决策」
#: （记录说裸起成功过的，预检也裸起）。没设置 = 只按解释器现场检测（`detect`）。
_PROBE_DECIDER: contextvars.ContextVar = contextvars.ContextVar(
    "tavotto_probe_launcher", default=None
)


@contextlib.contextmanager
def probe_scope(decider):
    token = _PROBE_DECIDER.set(decider)
    try:
        yield
    finally:
        _PROBE_DECIDER.reset(token)


#: 被包装的探针的引导：把 fd 1 指到结果文件、再原样执行探针源码。探针（`-c` 片段）本来就把结果写在 stdout（有的 dup
#: 出一份 fd 再 `os.write`），重定向之后它们写进文件；`conda run` 的 activate.d / deactivate.d 钩子打印的横幅留在真正的
#: stdout 上，不会混进结果。`sys.argv` 与 `__main__` 全局同原来的 `-c` 一致。
_PROBE_BOOT = (
    "import os, sys\n"
    "_rf, _src = sys.argv[1], sys.argv[2]\n"
    "sys.argv = ['-c'] + sys.argv[3:]\n"
    "_fd = os.open(_rf, os.O_WRONLY | os.O_TRUNC)\n"
    "os.dup2(_fd, 1)\n"
    "os.close(_fd)\n"
    "exec(compile(_src, '<string>', 'exec'), {'__name__': '__main__', '__builtins__': __builtins__})\n"
)


def effective_launcher(python) -> CondaLauncher | None:
    """此刻对 `python` 生效的启动方式：`probe_scope` 里的每脚本决策，否则现场 `detect`；可执行文件不是绝对路径（没法安全
    包装）按裸起。`run_probe` 与 `context_key` 共用这一处，所以「实际怎么起」和「缓存按什么分区」不会分叉。"""
    decider = _PROBE_DECIDER.get()
    lau = decider(python) if decider is not None else detect(python)
    if lau is not None:
        try:
            lau.wrap([str(python)])
        except ValueError:
            return None
    return lau


def context_key(python) -> str:
    """启动上下文的稳定投影，给**按解释器缓存**的结论分区用：同一个解释器裸起得出的事实 / 体检结论，换成 Conda 激活
    （或反过来、或换了 conda / prefix）之后不能原样复用。`direct`，或 `conda|<exe>|<prefix>`。"""
    lau = effective_launcher(python)
    return "direct" if lau is None else f"conda|{lau.conda_exe}|{lau.prefix}"


def run_probe(argv, python, *, timeout=None, **kwargs):
    """`subprocess.run` 的替身，给「要在 worker 将要使用的那个解释器里跑一小段探针」的预检用（`_has_matplotlib`、
    `projectenv.probe_environment`、依赖计划的目标事实、导入核验、版本盘点……）。

    启动决策与 worker 同一个：调用方在 `probe_scope` 里（知道项目 / 脚本）就用它给的决策，否则按解释器现场 `detect`。
    需要 Conda 激活的：经 `conda run --no-capture-output --prefix …` 包装起，自成进程组（超时整棵树收掉）。
    **结果不走 stdout**：`activate.d` 钩子在命令之前打印、`deactivate.d` 钩子在命令之后打印，都会直通进 stdout，尾部横幅会让
    任何「从 stdout 里找 JSON」的办法失效。所以由我们建一个 0600 的临时结果文件，探针的 fd 1 被引导代码指进去，父进程
    只读这个文件；真正的 stdout / stderr 一律当日志。其余（内置 runtime、受管 venv、Windows、没找到 conda、开关关掉）：
    原样 `subprocess.run`，行为一字不变。只改「怎么起」，不改探针的内容、超时、env、cwd。"""
    lau = effective_launcher(python)
    if lau is not None:
        try:
            lau.wrap(list(argv))  # 再校验一遍实际命令（argv[0] 不一定就是 python）
        except ValueError:
            lau = None
    if lau is None:
        kwargs.pop("creationflags", None)  # 探针一律是 GUI 拥有的隐藏子进程
        return subprocess.run(
            argv, timeout=timeout, creationflags=runtime.CREATE_NO_WINDOW, **kwargs
        )
    kw = dict(kwargs)
    kw.pop("creationflags", None)
    if kw.pop("capture_output", False):
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
    cmd = list(argv)
    result_path = ""
    if "-c" in cmd and cmd.index("-c") + 1 < len(cmd):
        i = cmd.index("-c")
        src, rest = cmd[i + 1], cmd[i + 2 :]
        results = config.data_dir() / "probe-results"
        results.mkdir(parents=True, exist_ok=True)
        fd, result_path = tempfile.mkstemp(prefix="probe-", suffix=".json", dir=results)
        os.close(fd)  # mkstemp 建出来就是 0600
        cmd = [*cmd[:i], "-c", _PROBE_BOOT, result_path, src, *rest]
    wrapped = lau.wrap(cmd)
    try:
        proc = GroupPopen(wrapped, creationflags=runtime.CREATE_NO_WINDOW, **kw)
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            proc.wait()
            raise subprocess.TimeoutExpired(wrapped, timeout) from None
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        proc.wait()  # 确认组内没有残留
        if result_path:
            try:
                data = Path(result_path).read_bytes()
            except OSError:
                data = b""
            if isinstance(out, str):
                out = data.decode(kw.get("encoding") or "utf-8", kw.get("errors") or "strict")
            else:
                out = data
        return subprocess.CompletedProcess(wrapped, proc.returncode, out, err)
    finally:
        if result_path:
            try:
                os.unlink(result_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 进程树
# ---------------------------------------------------------------------------
def group_members(pgid: int) -> list[tuple[int, int, str]]:
    """进程组 `pgid` 里此刻的成员 `[(pid, ppid, 状态首字母)]`（POSIX；读不出回 `[]`）。

    Linux 读 `/proc/<pid>/stat`（字段 3 状态、4 ppid、5 组 id；`comm` 里可能有空格 / 括号，从最后一个 `)` 之后切）；
    macOS / BSD 用 `ps -axo pid=,ppid=,pgid=,stat=`。僵尸（状态 `Z`）也在列表里——调用方要自己区分「还活着」和「只是没被收尸」。"""
    if os.name == "nt":
        return []
    out: list[tuple[int, int, str]] = []
    proc = Path("/proc")
    if (proc / "self" / "stat").exists():
        try:
            entries = [p for p in proc.iterdir() if p.name.isdigit()]
        except OSError:
            entries = []
        for p in entries:
            try:
                text = (p / "stat").read_text(encoding="utf-8", errors="replace")
                rest = text[text.rindex(")") + 2 :].split()
                state, ppid, group = rest[0], int(rest[1]), int(rest[2])
            except (OSError, ValueError, IndexError):
                continue
            if group == pgid:
                out.append((int(p.name), ppid, state[:1]))
        return out
    try:
        done = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,stat="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and all(x.isdigit() for x in parts[:3]) and int(parts[2]) == pgid:
            out.append((int(parts[0]), int(parts[1]), parts[3][:1]))
    return out


def _state_of(pid: int) -> str:
    """进程状态首字母（Linux 读 `/proc/<pid>/stat`，其余用 `ps`）；读不出回空串。"""
    stat = Path("/proc") / str(pid) / "stat"
    try:
        text = stat.read_text(encoding="utf-8", errors="replace")
        return text[text.rindex(")") + 2 :].split()[0][:1]
    except (OSError, ValueError, IndexError):
        pass
    try:
        done = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()[:1]


def is_running(pid: int) -> bool:
    """`pid` 此刻在**运行**吗：存在且不是僵尸。僵尸不再执行任何代码，只是还没被它的父进程收尸。"""
    if os.name == "nt":
        # Windows 上 `os.kill(pid, 0)` 会终止进程（#523）；这个函数只服务 POSIX 的进程组确认，Windows 不探测
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return _state_of(pid) != "Z"


class GroupPopen(subprocess.Popen):
    """自成进程组的 Popen；`kill()` 按进程树**从叶子往上**终止，`wait()` 确认组内没有残留。仅在包装 worker 时用。

    包装 worker 的进程树是「conda → 临时 shell → 用户 Python」。对整组一起发 SIGKILL 时 shell 可能先死、来不及回收
    Python，Python 就成了孤儿，交给 init；PID 1 不收尸的容器里它会永远留成僵尸。所以顺序是：由 ppid 建出树，**先杀最深的
    一层，等它的父进程把它回收掉（pid 消失，而不是变成 `Z`），再往上一层**，最后杀 wrapper 并由我们自己 `wait` 收尸。
    每一层都有等待上限；超时的层继续往上走，残留如实记在 `unreaped` 里并告警，不伪装成已死。

    确认「残留」时：僵尸只有在它的父进程是 PID 1（已经被 init 接管，我们和任何别的进程都收不了尸）时才算已死；
    父进程还在（不是 init）的僵尸仍算残留——那个父进程会收尸，等它。"""

    #: 每一层等「父进程把它回收掉」的上限（秒）
    LEVEL_GRACE = 2.0

    def __init__(self, *args, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
            )
        else:
            kwargs["start_new_session"] = True
        super().__init__(*args, **kwargs)
        #: 终止后仍留在组里、没能回收的 `[(pid, 状态)]`（空 = 干净）
        self.unreaped: list[tuple[int, str]] = []

    def _taskkill(self) -> None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
                creationflags=runtime.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _signal_pid(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _kill_group(self) -> None:
        """整组一起杀（兜底，只在叶子往上走完之后补刀用）。"""
        if os.name == "nt":
            self._taskkill()
            return
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _levels(self) -> list[list[tuple[int, str]]]:
        """组内除 wrapper 以外的成员 `[(pid, 状态)]`，按到 wrapper 的深度分层，**最深的一层在前**。父进程不在组里的
        （被 init 接管的孤儿）算最浅一层——它们没有我们能等的父进程。

        已经是僵尸（`Z`）的成员**留在树里**：它不能再被 signal，但它是「它的父进程还没收尸」的证据，父进程必须等它被回收
        （或超过上限）才能被杀——否则父 shell 被当叶子先杀，这个僵尸就成了永久孤儿。"""
        members = {pid: (ppid, state) for pid, ppid, state in group_members(self.pid)}
        members.pop(self.pid, None)
        depth: dict[int, int] = {}

        def of(pid: int, trail: tuple = ()) -> int:
            if pid not in depth:
                parent = members[pid][0]
                inside = parent in members and parent not in trail and parent != pid
                depth[pid] = 1 + (of(parent, (*trail, pid)) if inside else 0)
            return depth[pid]

        by_depth: dict[int, list[tuple[int, str]]] = {}
        for pid, (_ppid, state) in members.items():
            by_depth.setdefault(of(pid), []).append((pid, state))
        return [by_depth[d] for d in sorted(by_depth, reverse=True)]

    def _kill_tree_leaves_first(self) -> None:
        for level in self._levels():
            for pid, state in level:
                if state != "Z":
                    self._signal_pid(pid)
            pids = {pid for pid, _ in level}
            deadline = time.monotonic() + self.LEVEL_GRACE
            while time.monotonic() < deadline:
                if not pids & {m[0] for m in group_members(self.pid)}:
                    break  # 它们（含僵尸）的父进程已经把它们回收掉了（pid 消失）
                time.sleep(0.02)

    def kill(self) -> None:
        if os.name == "nt":
            self._taskkill()
            super().kill()
            return
        self._kill_tree_leaves_first()
        self._kill_group()  # 补刀：叶子往上杀的过程中新冒出来的 / 超时没回收的
        super().kill()

    def _residue(self) -> list[tuple[int, str]]:
        """组里还算「残留」的成员：活着的，或虽是僵尸但父进程还在（不是 PID 1）、迟早会收尸的。
        被 PID 1 接管的僵尸没有任何人能回收：不算活（它不再执行代码），但留在 `unreaped` 里如实报。"""
        out = []
        for pid, ppid, state in group_members(self.pid):
            if state == "Z" and ppid == 1:
                continue
            out.append((pid, state))
        return out

    def _group_alive(self) -> bool:
        if os.name == "nt":
            return False  # taskkill /T 已按树杀；Windows 没有可探测的组
        if group_members(self.pid):
            return bool(self._residue())
        # 列表是空的：要么真没人了，要么读不出成员（没有 /proc 也没有 ps）——用 killpg 探测分辨
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True

    def wait(self, timeout=None):
        code = super().wait(timeout=timeout)
        # wrapper 走了不代表用户 Python 走了（conda 先死、孙进程成孤儿）：先叶子往上杀，再确认
        deadline = time.monotonic() + GROUP_REAP_TIMEOUT
        while self._group_alive():
            self._kill_tree_leaves_first()
            self._kill_group()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        self.unreaped = [(pid, state) for pid, _ppid, state in group_members(self.pid)]
        if self.unreaped:
            LOG.warning(
                "包装 worker 进程组终止后仍有 %d 个成员没被回收（pid=%s）",
                len(self.unreaped),
                [pid for pid, _ in self.unreaped],
            )
        return code
