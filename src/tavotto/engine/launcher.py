"""Launcher（P09）：用户环境需要「激活」才能跑时，worker 怎么被**包一层**起起来。

解释器选择与启动方式是两件事：同一个 `python` 路径，裸起与经环境管理器激活后起，看到的环境变量 / DLL 搜索路径 /
`activate.d` 钩子并不相同。Conda 环境里的受控 SDK（厂商 SDK 常在 `activate.d` 里设变量）单起 `bin/python` 会 import 失败，
而 `conda run` 起的同一解释器成功。本模块只做**结构化的启动包装**，不做环境管理：

* `CondaLauncher` 是不可变的结构化描述（conda 可执行文件 + prefix），`wrap()` 产出 **argv 数组**，
  绝不拼 shell 字符串。语义按官方 `conda run`（<https://docs.conda.io/projects/conda/en/stable/commands/run.html>，
  2026-10-13 核对）：`-p/--prefix PATH` 指环境、`-s/--no-capture-output/--live-stream` 不捕获 stdout/stderr
  （worker 的 v1 协议跑在 stdout 上，必须直通）、`--cwd CWD` 设工作目录、`--` 分隔 conda 选项与被执行命令。
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

import dataclasses
import hashlib
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

KIND_DIRECT = "direct"
KIND_CONDA = "conda"
KINDS = (KIND_DIRECT, KIND_CONDA)

#: 关掉自动 Conda 激活（排障 / 回到 P09 之前的裸起行为）
ENV_DISABLE = "TAVOTTO_CONDA_ACTIVATION"
_OFF_VALUES = frozenset({"0", "off", "false", "no"})

#: wrapper 退出后等「组里没有残留」的上限（秒）。超过只记日志，不无限等。
GROUP_REAP_TIMEOUT = 5.0


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
        """被执行命令 `argv` → 经 `conda run` 的完整 argv 数组（每个 token 一项，`--` 之后原样）。

        `--no-capture-output` 恒带：worker 的协议在 stdout 上，被 conda 捕获 = 协议死锁。"""
        if not argv:
            raise ValueError("wrap 需要非空 argv")
        out = [self.conda_exe, "run", "--no-capture-output", "--prefix", self.prefix]
        if cwd:
            out += ["--cwd", str(cwd)]
        out.append("--")
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
    prefix = conda_prefix_of(python)
    if prefix is None:
        return None
    exe = find_conda_exe(prefix)
    if exe is None:
        return None
    return CondaLauncher(conda_exe=exe, prefix=prefix)


# ---------------------------------------------------------------------------
# 进程树
# ---------------------------------------------------------------------------
class GroupPopen(subprocess.Popen):
    """自成进程组的 Popen；`kill()` 杀整组，`wait()` 确认组里无残留。仅在包装 worker 时用。"""

    def __init__(self, *args, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
            )
        else:
            kwargs["start_new_session"] = True
        super().__init__(*args, **kwargs)

    def _kill_group(self) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(self.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            return
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def kill(self) -> None:
        self._kill_group()
        super().kill()

    def _group_alive(self) -> bool:
        if os.name == "nt":
            return False  # taskkill /T 已按树杀；Windows 没有可探测的组
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True

    def wait(self, timeout=None):
        code = super().wait(timeout=timeout)
        # wrapper 走了不代表用户 Python 走了（conda 先死、孙进程成孤儿）：组里还有人就补杀并确认
        deadline = time.monotonic() + GROUP_REAP_TIMEOUT
        while self._group_alive():
            self._kill_group()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        return code
