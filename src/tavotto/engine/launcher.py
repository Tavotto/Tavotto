"""Launcher（P09）：用户环境需要「激活」才能跑时，怎么拿到激活后的环境变量。

解释器选择与启动方式是两件事：同一个 `python` 路径，裸起与经环境管理器激活后起，看到的环境变量（`PATH`、`CONDA_PREFIX`、
`activate.d` 钩子设的许可证 / DLL 搜索路径变量……）并不相同。Conda 环境里的受控 SDK 单起 `bin/python` 会 import 失败。

做法（VS Code Python 扩展等工具的通行做法）：**激活一次，捕获环境，直接启动**。

* `activated_delta(python, launcher)`：对 Conda 环境只跑**一次** `conda run --no-capture-output --prefix <p> <python 绝对路径> -c …`，
  让那个 Python 把 `os.environ` 写进一个 0600 的临时结果文件（`activate.d` / `deactivate.d` 往 stdout 打印什么都不影响结果）；
  取它相对于当前进程环境的**增量**。不带 `--` 分隔符：`conda run` 的命令部分是 argparse REMAINDER，较老的 conda（实测
  24.5.0，conda/conda#13639）不认 `--`；所以可执行文件必须是绝对路径、不以 `-` 开头。
* 增量只放**进程内存**，键为 `(prefix, prefix_state, conda_exe)`：环境变了（装 / 升降包）就重新捕获。**绝不持久化**：不进
  runconfig、记录、回执、日志、诊断（日志最多记变量个数）——那是秘密面（SDK 的许可证串 / 令牌常在里面）。
* 所有「用目标解释器起子进程」的地方（worker、预检探针、依赖门、`worker_self_test`、模块版本盘点）都**直接**起目标解释器
  （我们的直接子进程，取消 / 超时 / 协议 / 回执 pid 都与没有 Conda 时一样），只是把 env 换成「当前环境 + 激活增量」。

限制：只捕获环境变量。激活脚本里环境变量以外的副作用（启动 license 服务、`ulimit`、shell 函数）不会在每次启动时重放，
`deactivate.d` 不再执行——与 IDE 的通行做法一致。

不碰：Windows（本期没有真机验证，`detect()` 恒 None；捕获方案本身可直接扩到 Windows）、mamba / micromamba（选项集不同，
另列样本门）、内置 runtime 与 Tavotto 自己的受管环境（venv，没有 `conda-meta`）。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

from . import config, runtime

LOG = logging.getLogger("tavotto.engine")

KIND_DIRECT = "direct"
KIND_CONDA = "conda"
KINDS = (KIND_DIRECT, KIND_CONDA)

#: 关掉自动 Conda 激活（排障 / 回到 P09 之前的裸起行为）
ENV_DISABLE = "TAVOTTO_CONDA_ACTIVATION"
_OFF_VALUES = frozenset({"0", "off", "false", "no"})


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
# 激活环境的捕获与合并
# ---------------------------------------------------------------------------
#: 本次探针该用的启动决策（`(python) -> CondaLauncher | None`）。由知道「这是哪个项目的哪个脚本」的调用方设置
#: （`pool.launch_scope`），探针与 worker 才用同一个「每脚本启动决策」。没设置 = 只按解释器现场检测（`detect`）。
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


def effective_launcher(python) -> CondaLauncher | None:
    """此刻对 `python` 生效的启动方式：`probe_scope` 里的每脚本决策，否则现场 `detect`；可执行文件不是绝对路径
    （捕获需要绝对路径的可执行文件）按直接启动。"""
    decider = _PROBE_DECIDER.get()
    lau = decider(python) if decider is not None else detect(python)
    if lau is not None and (not os.path.isabs(str(python)) or str(python).startswith("-")):
        return None
    return lau


def context_key(python) -> str:
    """启动上下文的稳定投影，给**按解释器缓存**的结论分区用：直接启动得出的事实 / 体检结论，换成 Conda 激活（或反过来、
    或换了 conda / prefix / 环境版本）之后不能原样复用。`direct`，或 `conda|<exe>|<prefix>|<env_state>`。"""
    lau = effective_launcher(python)
    if lau is None:
        return "direct"
    return f"conda|{lau.conda_exe}|{lau.prefix}|{prefix_state(lau.prefix)}"


#: 捕获时的超时（秒）。激活脚本卡住不能拖住整个预检 / 起会话。
CAPTURE_TIMEOUT = 60.0
#: 每个进程自己都会变、不该当作「激活增量」的变量
_VOLATILE_ENV = frozenset({"_", "SHLVL", "PWD", "OLDPWD", "PS1", "PS2"})
_CAPTURE_SRC = (
    "import json, os, sys\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps(dict(os.environ)))\n"
)

_captured: dict[tuple[str, str, str], dict[str, str]] = {}
_captured_lock = threading.Lock()


def reset_captures() -> None:
    with _captured_lock:
        _captured.clear()


def _capture(python: str, lau: CondaLauncher) -> dict[str, str]:
    """跑一次 `conda run … python -c <写 os.environ 到结果文件>`；失败 / 超时 → `LauncherUnavailable`。"""
    results = config.data_dir() / "probe-results"
    results.mkdir(parents=True, exist_ok=True)
    fd, result_path = tempfile.mkstemp(prefix="activated-", suffix=".json", dir=results)
    os.close(fd)  # mkstemp 建出来就是 0600
    argv = [
        lau.conda_exe,
        "run",
        "--no-capture-output",
        "--prefix",
        lau.prefix,
        python,
        "-c",
        _CAPTURE_SRC,
        result_path,
    ]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),  # 超时能把 conda 起的整棵树一起收掉
            creationflags=runtime.CREATE_NO_WINDOW,
        )
        try:
            code = proc.wait(timeout=CAPTURE_TIMEOUT)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            proc.kill()
            proc.wait()
            raise LauncherUnavailable(
                f"经 Conda 激活取环境超时（{CAPTURE_TIMEOUT:.0f}s）；请检查这个环境的 activate.d 脚本"
            ) from None
        try:
            data = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if code != 0 or not isinstance(data, dict):
            raise LauncherUnavailable(
                f"经 Conda 激活取环境失败（conda run 退出码 {code}）；请检查 Conda 安装与这个环境"
            )
    except OSError as exc:
        raise LauncherUnavailable(f"无法启动 conda：{exc.__class__.__name__}") from exc
    finally:
        try:
            os.unlink(result_path)
        except OSError:
            pass
    return {
        str(k): str(v) for k, v in data.items() if k not in _VOLATILE_ENV and os.environ.get(k) != v
    }


def activated_delta(python: str, lau: CondaLauncher) -> dict[str, str]:
    """`python` 经 Conda 激活后相对当前进程环境的**增量**（进程内缓存；环境版本变了重新捕获）。"""
    key = (lau.prefix, prefix_state(lau.prefix), lau.conda_exe)
    with _captured_lock:
        hit = _captured.get(key)
    if hit is not None:
        return hit
    delta = _capture(str(python), lau)
    LOG.info("Conda 激活环境已捕获：%d 个变量（不落盘）", len(delta))  # 只记个数，不记名字和值
    with _captured_lock:
        _captured[key] = delta
    return delta


def merge_env(lau: CondaLauncher | None, python, base: dict | None = None) -> dict | None:
    """起子进程用的 env：没有 launcher 原样返回 `base`（None = 继承）；有就是 `base`（或当前环境）叠上激活增量。
    调用方传进来的就是它原来要用的 env（`worker_env` / `runtime.probe_env` / `owned_env`），Tavotto 自己的增量保留。"""
    if lau is None:
        return base
    env = dict(base if base is not None else os.environ)
    env.update(activated_delta(str(python), lau))
    return env


def activate_env(python, base: dict | None = None) -> dict | None:
    """预检探针 / 自检 / 模块盘点取 env 的入口：按 `effective_launcher` 的决策合并激活增量。

    在 `probe_scope`（知道项目 / 脚本）里捕获失败就抛 `LauncherUnavailable`（由 `pool.launch_scope` 转成稳定码）；
    不在范围里（没有项目上下文的诊断类调用）捕获失败只告警、按直接启动——不把一次环境状态查询变成 500。"""
    lau = effective_launcher(python)
    if lau is None:
        return base
    try:
        return merge_env(lau, python, base)
    except LauncherUnavailable:
        if _PROBE_DECIDER.get() is None:
            LOG.warning("经 Conda 激活取环境失败，这次按直接启动处理")
            return base
        raise
