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

**启动方式是解释器路径的纯函数**（`launcher_for_interpreter`：关开关 → 现场检测）：不看脚本、项目、成功记录、
来源标签，也没有 ContextVar 之类的“范围”——所有入口、所有线程对同一条解释器天然给出同一个答案。成功记录里的 launcher 只
作为事实记下，不驱动决策。**激活失败一律 fail-closed**：对 Conda 环境捕获失败 / 超时，任何调用点都抛
`LauncherUnavailable`（稳定码 `conda_launcher_unavailable`），不退回裸起，失败也不进任何缓存。

Windows（ADR 0142，默认开）：同一方案——`<根>\\Scripts\\conda.exe run --no-capture-output --prefix <p> <python.exe> -I -S
<捕获脚本文件> <结果文件>`，argv 列表直接交给 CreateProcess，**不经 cmd.exe、不拼 shell、不用 `conda.bat`**；捕获脚本在 Windows 上落成
`probe-results/` 里的临时 `.py` 文件（`conda run` 在 Windows 上把命令写进临时 `.bat`，多行 `-c` 参数会被 cmd 截断），结果仍走 UTF-8 的 JSON
结果文件，不受控制台代码页影响。超时用 `taskkill /T /F` 收整棵树。

不碰：mamba / micromamba（选项集不同，另列样本门）、内置 runtime 与 Tavotto 自己的受管环境（venv，没有 `conda-meta`）。

纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import atexit
import contextlib
import dataclasses
import hashlib
import json
import logging
import ntpath
import os
import shutil
import signal
import subprocess
import tempfile
import threading
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


#: 稳定码：Conda 环境激活取环境失败（对外只有这一个）
UNAVAILABLE_CODE = "conda_launcher_unavailable"


def is_unavailable(health: dict | None) -> bool:
    """一条体检 / 事实结论是不是“激活取环境失败”：这样的结论是**瞬时**的，不进任何 verdict / facts 缓存。"""
    return isinstance(health, dict) and health.get("code") == UNAVAILABLE_CODE


class LauncherUnavailable(runtime.ActivationUnavailable):
    """这条解释器在 Conda 环境里、也找得到 conda，但激活取环境失败（超时 / 退出码非 0 / 起不来）。`code` 是稳定码；
    入口（pool / 准备流程）把它转成 `WorkerError`。**不退回裸起**，失败也不缓存。"""

    code = UNAVAILABLE_CODE


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
    candidates = [p.parent] if _is_windows() else [p.parent.parent]
    if _is_windows():
        candidates.append(p.parent.parent)  # Scripts\python.exe 之类
    for prefix in candidates:
        if (prefix / "conda-meta").is_dir():
            return str(prefix)
    return None


#: 常见的 conda 安装位置（用户目录 / 系统目录，都不在项目里）。从 Finder / 桌面启动时没有 `CONDA_EXE`、`PATH` 也很短，
#: 靠这张表找到用户自己的 conda。只认“确实是安装根”的（`conda-meta/` + 可执行的 `bin/conda`）
_STANDARD_ROOTS = (
    "~/miniconda3",
    "~/anaconda3",
    "~/miniforge3",
    "~/mambaforge",
    "~/opt/miniconda3",
    "~/opt/anaconda3",
    "/opt/conda",
    "/opt/miniconda3",
    "/opt/anaconda3",
    "/opt/miniforge3",
    "/usr/local/miniconda3",
    "/usr/local/anaconda3",
    "/opt/homebrew/Caskroom/miniforge/base",
    "/opt/homebrew/Caskroom/miniconda/base",
    "/opt/homebrew/anaconda3",
)


#: Windows 的常见安装位置（Miniconda / Anaconda / Miniforge 的“仅我自己”装在用户目录或 `%LOCALAPPDATA%`，“所有用户”装在 `%ProgramData%`）：
#: （环境变量名, 子目录）；环境变量名为空 = 用户目录
_WINDOWS_STANDARD_ROOTS = tuple(
    (var, name)
    for var, names in (
        ("", ("miniconda3", "anaconda3", "miniforge3", "mambaforge")),
        ("LOCALAPPDATA", ("miniconda3", "anaconda3", "miniforge3")),
        ("ProgramData", ("miniconda3", "anaconda3", "miniforge3")),
    )
    for name in names
)


def _standard_conda_roots() -> list[str]:
    if _is_windows():
        out: list[str] = []
        for var, name in _WINDOWS_STANDARD_ROOTS:
            base = os.path.expanduser("~") if not var else os.environ.get(var, "")
            if base:  # 变量缺失：该项丢弃，不当字面路径
                out.append(os.path.join(base, name))
        return out
    return [os.path.expanduser(r) for r in _STANDARD_ROOTS]


#: 安装程序写的卸载项里，DisplayName 以这些开头（不区分大小写）的才算 conda 安装
_REGISTRY_NAME_PREFIXES = ("miniconda", "anaconda", "miniforge", "mambaforge", "micromamba")
#: 注册表读取的上限（卸载项 / PEP 514 子键各自最多看这么多个），防止异常机器上扫太久
_REGISTRY_MAX_ENTRIES = 512
_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
_PEP514_KEY = r"Software\Python\ContinuumAnalytics"


def _root_from_uninstall_entry(
    display_name: str | None, install_location: str | None, uninstall_string: str | None
) -> str | None:
    """一个卸载项 → 候选安装根：DisplayName 须以 conda 系名字开头；优先 `InstallLocation`，否则 `UninstallString`
    （去引号）所在目录（Miniconda 的卸载项没有 InstallLocation，只有 `"<根>\\Uninstall-Miniconda3.exe"`）。注册表值是 Windows 路径，用 ntpath 解析。"""
    if not (display_name or "").lower().startswith(_REGISTRY_NAME_PREFIXES):
        return None
    if install_location and install_location.strip().strip('"'):
        return install_location.strip().strip('"')
    if uninstall_string and uninstall_string.strip().strip('"'):
        return ntpath.dirname(uninstall_string.strip().strip('"')) or None
    return None


def _read_registry_candidates() -> list[str]:
    """（只在 Windows 真正读）安装程序写进注册表的 conda 安装目录候选：HKCU / HKLM（含 WOW6432Node）卸载项
    （DisplayName 前缀匹配；取 `InstallLocation`，没有则取 `UninstallString` 去引号后所在目录），加 PEP 514
    `HKCU|HKLM\\Software\\Python\\ContinuumAnalytics\\*\\InstallPath`。只是**候选**：必须再过 `_is_install_root`。
    测试替换这个函数注入假读取器；任何读取错误都只是少一个候选。"""
    try:
        import winreg  # noqa: PLC0415  (只在 Windows 存在)
    except ImportError:
        return []
    out: list[str] = []

    def value(key, name: str) -> str | None:
        try:
            v, _t = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return v if isinstance(v, str) and v.strip() else None

    def subkeys(root, path: str, view: int):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ | view) as k:
                for i in range(_REGISTRY_MAX_ENTRIES):
                    try:
                        yield winreg.EnumKey(k, i)
                    except OSError:
                        return
        except OSError:
            return

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            for name in subkeys(hive, _UNINSTALL_KEY, view):
                try:
                    with winreg.OpenKey(
                        hive, _UNINSTALL_KEY + "\\" + name, 0, winreg.KEY_READ | view
                    ) as k:
                        root = _root_from_uninstall_entry(
                            value(k, "DisplayName"),
                            value(k, "InstallLocation"),
                            value(k, "UninstallString"),
                        )
                        if root:
                            out.append(root)
                except OSError:
                    continue
            for tag in subkeys(hive, _PEP514_KEY, view):
                try:
                    with winreg.OpenKey(
                        hive, _PEP514_KEY + "\\" + tag + "\\InstallPath", 0, winreg.KEY_READ | view
                    ) as k:
                        loc = value(k, "")
                        if loc:
                            out.append(loc.strip().strip('"'))
                except OSError:
                    continue
    return out


def _registry_conda_roots() -> list[str]:
    if not _is_windows():
        return []
    try:
        return list(_read_registry_candidates())
    except Exception:  # noqa: BLE001  注册表读取只是增加候选，绝不能让检测崩
        return []


def _root_conda_exe(root: str) -> str:
    """安装根里的 conda 可执行文件：POSIX `bin/conda`，Windows `Scripts\\conda.exe`（真正的 exe，不是 `condabin\\conda.bat`）。"""
    if _is_windows():
        return os.path.join(root, "Scripts", "conda.exe")
    return os.path.join(root, "bin", "conda")


def _is_install_root(root: str) -> bool:
    return os.path.isdir(os.path.join(root, "conda-meta")) and _is_executable(_root_conda_exe(root))


def _raw_conda_roots() -> list[str]:
    """用户自己的 conda 安装根的**候选**（尚未核对里面有没有可执行的 conda）：`CONDA_EXE` / `PATH` 上的 conda 所在的根，
    常见安装位置，Windows 再加安装程序写的注册表卸载项。全都来自用户的环境变量、固定的安装目录或安装程序写的注册表——
    项目目录里的文件改不了它们。"""
    raw: list[str] = []
    for exe in (os.environ.get("CONDA_EXE"), shutil.which("conda")):
        if exe:
            try:
                raw.append(os.path.dirname(os.path.dirname(os.path.realpath(exe))))
            except (OSError, ValueError):
                continue
    for root in [*_standard_conda_roots(), *_registry_conda_roots()]:
        try:
            raw.append(os.path.realpath(root))
        except (OSError, ValueError):
            continue
    out: list[str] = []
    for root in raw:
        if root and root not in out:
            out.append(root)
    return out


def _user_conda_roots() -> list[str]:
    """候选里确实是安装根的（`conda-meta/` + 可执行的 conda）。"""
    return [r for r in _raw_conda_roots() if _is_install_root(r)]


def _owner_among(prefix: str, roots: list[str]) -> str | None:
    try:
        real = os.path.realpath(prefix)
    except (OSError, ValueError):
        return None
    nc = os.path.normcase  # Windows 路径不分大小写
    for root in roots:
        if nc(real) == nc(root) or nc(os.path.dirname(real)) == nc(os.path.join(root, "envs")):
            return root
    return None


def _owning_root(prefix: str) -> str | None:
    """prefix 属于哪一个用户 conda 安装：就是安装根本身（base），或在它的 `envs/` 下一层；都不是回 None。"""
    return _owner_among(prefix, _user_conda_roots())


def _unusable_owning_root(prefix: str) -> str | None:
    """prefix 属于一个**可信来源指出的安装根**（有 `conda-meta/`），但这个根里的 conda 可执行文件现在不可用（被改名 / 删除 /
    没有执行权限）→ 回那个根；否则 None。与“项目自带 conda-meta 的克隆目录 / `conda create -p` 到任意目录”不同：那些找不到任何可信根，
    仍直接启动。"""
    roots = [
        r
        for r in _raw_conda_roots()
        if os.path.isdir(os.path.join(r, "conda-meta")) and not _is_install_root(r)
    ]
    return _owner_among(prefix, roots)


def _conda_candidates(prefix: str) -> list[str]:
    """prefix 对应的 conda 可执行文件候选（按可信度排序）：它所属安装根的 `bin/conda`，再是 `CONDA_EXE` / `PATH`。"""
    out: list[str] = []
    root = _owning_root(prefix)
    if root is not None:
        out.append(_root_conda_exe(root))
    env_exe = os.environ.get("CONDA_EXE")
    if env_exe:
        out.append(env_exe)
    which = shutil.which("conda")
    if which:
        out.append(which)
    return out


def find_conda_exe(prefix: str) -> str | None:
    for cand in _conda_candidates(prefix):
        # `conda.bat` / `.cmd` 需要 cmd.exe 引号规则（不拼 shell）：Windows 只认 `Scripts\\conda.exe`
        if cand.lower().endswith((".bat", ".cmd")):
            continue
        if _is_executable(cand):
            return os.path.abspath(cand)
    return None


def _sig(path: Path) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _dir_sig(path: Path) -> tuple | None:
    """目录里的文件清单 + 各自的 (size, mtime_ns)；目录不在回 None。"""
    try:
        with os.scandir(path) as it:
            return tuple(sorted((e.name, *(_sig(Path(e.path)) or ())) for e in it))
    except OSError:
        return None


def prefix_state(prefix: str) -> str:
    """环境 prefix 的「版本」：激活结果可能随之变化的那些文件的指纹。

    * `conda-meta/history`（每次装 / 卸 / 升降包事务都追加）与 `conda-meta` 目录本身；
    * `conda-meta/state`（`conda env config vars set / unset` 写在这里：激活会导出这些变量）；
    * `etc/conda/activate.d` 与 `deactivate.d` 的文件清单与各自的 (size, mtime_ns)（新增 / 改动钩子脚本）。

    纯读不换值；不含路径。读不到任何一项回空串。"""
    pre = Path(prefix)
    meta = pre / "conda-meta"
    parts = [
        _sig(meta / "history"),
        _sig(meta),
        _sig(meta / "state"),
        _dir_sig(pre / "etc" / "conda" / "activate.d"),
        _dir_sig(pre / "etc" / "conda" / "deactivate.d"),
    ]
    if all(x is None for x in parts):
        return ""
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:16]


def env_state_of(python: str | os.PathLike) -> str:
    """解释器所在 Conda 环境的 `prefix_state`（不是 Conda 环境 = 空串）。起会话时取一次，成功记录用。"""
    prefix = conda_prefix_of(python)
    return prefix_state(prefix) if prefix else ""


@dataclasses.dataclass(frozen=True)
class CondaLauncher:
    """Conda 环境的结构化描述。不可变；只含路径，不含环境变量。"""

    conda_exe: str
    prefix: str
    kind: str = KIND_CONDA

    def __post_init__(self) -> None:
        if not self.conda_exe or not self.prefix:
            raise ValueError("CondaLauncher 需要 conda_exe 与 prefix")

    def public(self) -> dict:
        """可持久化的结构化引用（**没有**激活后的环境变量）。只用来对账 / 当事实记下，**不得**读回后当可执行路径：
        要执行的 conda 永远由 `launcher_for_interpreter()` 从解释器现场找。"""
        return {"kind": self.kind, "conda_exe": self.conda_exe, "prefix": self.prefix}


def _conda_shaped(python: str | os.PathLike) -> str | None:
    """这条解释器**看起来**在一个 Conda 环境里（关开关 / Windows / 绝对路径之外 → None；否则有 `conda-meta/` 的 prefix）。
    只看路径与目录，不执行任何东西；**不**代表会被激活（见 `_activation_wanted`）。"""
    if disabled():
        return None
    if not os.path.isabs(str(python)) or str(python).startswith("-"):
        return None  # 捕获要把解释器当 `conda run` 的可执行部分：必须是绝对路径、不以 `-` 开头
    return conda_prefix_of(python)


def _activation_wanted(python: str | os.PathLike) -> str | None:
    """这条解释器**该不该**经 Conda 激活：Conda 环境，**且**在用户自己的 conda 安装里（`_trusted_prefix`）→ 回 prefix。
    激活会 source 这个 prefix 自己的 `etc/conda/activate.d` 钩子——那是环境里的代码；不在用户 conda 安装里的 prefix
    （克隆来的项目自带的 `.venv/conda-meta/`、`conda create -p` 到任意目录）一律直接启动（与 P09 之前一致）。"""
    prefix = _conda_shaped(python)
    if prefix is None or not _trusted_prefix(prefix):
        return None
    return prefix


def _trusted_prefix(prefix: str) -> bool:
    """只有**用户自己的 conda 安装里**的环境才会被激活：安装根本身（base）或它的 `envs/<名字>`。激活会 source prefix 自己的
    `etc/conda/activate.d`——那是环境里的代码。任何“登记过 / 路径像”的推断都可能被项目目录冒充（过期的 environments.txt
    条目、克隆在同一路径上的项目），所以不用；`conda create -p <任意目录>` 建的环境直接启动（与 P09 之前一致）。"""
    return _owning_root(prefix) is not None


def launcher_for_interpreter(python: str | os.PathLike) -> CondaLauncher | None:
    """**唯一的启动决策**：解释器路径的纯函数。关开关 → 现场检测（Conda 环境 + 找得到 conda）→ launcher；
    否则 None（= 直接启动，与 P09 之前一致）。不读脚本、项目、成功记录、来源标签；任何线程、任何入口结果相同。"""
    prefix = _activation_wanted(python)
    if prefix is None:
        return None
    exe = find_conda_exe(prefix)
    if exe is None:
        return None
    return CondaLauncher(conda_exe=exe, prefix=prefix)


_warned_unlocated: set[str] = set()


def unlocated_conda_prefix(python: str | os.PathLike) -> str | None:
    """这条解释器看起来是 Conda 环境、但这次**不会被激活**（找不到 conda，或不在用户自己的 conda 安装里）→ 回 prefix；
    否则 None。只看路径，不执行任何东西：调用方据此不把一次直接启动的失败当成对环境的结论（见 `raise_if_unlocated`）。"""
    prefix = _conda_shaped(python)
    if prefix is None or launcher_for_interpreter(python) is not None:
        return None
    if prefix not in _warned_unlocated:
        _warned_unlocated.add(prefix)
        LOG.warning(
            "Conda 环境 %s 不会经激活启动（找不到 conda，或不在用户的 conda 安装里），按直接启动",
            prefix,
        )
    return prefix


def raise_if_unlocated(python: str | os.PathLike) -> None:
    """Conda 环境却找不到 conda 可执行文件时，**一次直接启动的失败不是对这个环境的结论**：在只有激活后才能 import 的环境里，
    裸起必然失败，把它记成“缺 matplotlib / 不可用”会作废记住的解释器、静默换成别的。所以调用方在把失败当结论之前调这里，
    抛 `LauncherUnavailable`（稳定码 `conda_launcher_unavailable`）。裸起成功的环境（不需要激活）不受影响，与 main 一致。"""
    prefix = unlocated_conda_prefix(python)
    if prefix is not None:
        raise LauncherUnavailable(
            "这是一个 Conda 环境，但找不到 conda 可执行文件，没法确认它能不能运行；请检查 Conda 安装，"
            "或设置 CONDA_EXE（或把 conda 放进 PATH）"
        )


def raise_if_trusted_root_unusable(python: str | os.PathLike) -> None:
    """解释器在可信安装根下的 Conda 环境里、但那个根的 conda 可执行文件现在不可用：**不能当成非 Conda 环境裸起**——这类环境
    不激活就会在运行时崩（缺 `Library\\bin` 的 DLL 路径之类），而裸起的体检可能偏偏过了。抛 `LauncherUnavailable`
    （稳定码 `conda_launcher_unavailable`，瞬时，不缓存）。找不到任何可信根的 Conda 形状目录不在此列（直接启动）。"""
    prefix = _conda_shaped(python)
    if prefix is None or launcher_for_interpreter(python) is not None:
        return
    root = _unusable_owning_root(prefix)
    if root is not None:
        raise LauncherUnavailable(
            "这个 Conda 环境所在的 Conda 安装里找不到可用的 conda 可执行文件（可能被改名或删除），没法激活它；"
            "请修复或重装 Conda"
        )


def context_key(python) -> str:
    """启动上下文的稳定投影，给**按解释器缓存**的结论分区用：直接启动得出的事实 / 体检结论，换成 Conda 激活（或反过来、
    或换了 conda / prefix / 环境版本）之后不能原样复用。`direct`（Conda 环境带 `|<env_state>`），或 `conda|<exe>|<prefix>|<env_state>`。"""
    lau = launcher_for_interpreter(python)
    if lau is None:
        # 直接启动的 Conda 环境（找不到 conda / 不激活）同样带环境版本：conda install / 改钩子之后，热会话与计划都要看得见
        prefix = conda_prefix_of(python)
        return f"direct|{prefix_state(prefix)}" if prefix else "direct"
    return f"conda|{lau.conda_exe}|{lau.prefix}|{prefix_state(lau.prefix)}"


# ---------------------------------------------------------------------------
# 激活环境的捕获与合并
# ---------------------------------------------------------------------------
#: 捕获时的缺省超时（秒）。激活脚本卡住不能拖住整个预检 / 起会话；有预算的调用方（体检）传自己的。
CAPTURE_TIMEOUT = 60.0
#: 每个进程自己都会变、不该当作「激活增量」的变量
_VOLATILE_ENV = frozenset({"_", "SHLVL", "PWD", "OLDPWD", "PS1", "PS2"})
_CAPTURE_SRC = (
    "import json, os, sys\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as fh:\n"
    "    fh.write(json.dumps(dict(os.environ)))\n"
)

_captured: dict[tuple[str, str, str], dict[str, str | None]] = {}
_captured_lock = threading.Lock()
_capture_gates: dict[tuple[str, str, str], threading.Lock] = {}
#: 结果文件含完整环境（可能有许可证串 / 令牌）：在途的路径登记在这里，退出时清掉；崩溃留下的由 `sweep_results` 扫
_inflight: set[str] = set()
_inflight_lock = threading.Lock()
#: 启动时清理的年龄下限（秒）：同一数据目录下可能有另一个正在捕获的 Tavotto 实例，别删它的在途文件
_STALE_RESULT_S = 3 * CAPTURE_TIMEOUT


def reset_captures() -> None:
    with _captured_lock:
        _captured.clear()
        _capture_gates.clear()


def _results_dir() -> Path:
    return config.data_dir() / "probe-results"


def sweep_results(*, everything: bool = False) -> None:
    """删掉 `probe-results/` 里的结果文件：启动时只删过期的（崩溃残留），`everything=True` 删全部（测试）。"""
    try:
        entries = list(os.scandir(_results_dir()))
    except OSError:
        return
    now = time.time()
    for e in entries:
        try:
            if everything or now - e.stat().st_mtime > _STALE_RESULT_S:
                os.unlink(e.path)
        except OSError:
            pass


def _cleanup_inflight() -> None:
    with _inflight_lock:
        paths = list(_inflight)
        _inflight.clear()
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


_startup_done = False


def startup() -> None:
    """引擎启动时调一次：清掉崩溃留下的结果文件，并登记退出清理。幂等。"""
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    sweep_results()
    # 被杀的上一个实例刚写下、还不到过期线的结果文件：启动那次扫不到，过了过期线再扫一次（不然长跑的实例会一直留着它）
    timer = threading.Timer(_STALE_RESULT_S + 5.0, sweep_results)
    timer.daemon = True
    timer.start()
    atexit.register(_cleanup_inflight)


#: 超时收尾（叶子先死）的总期限：不随进程树深度增长
_KILL_TREE_BUDGET_S = 1.0


def _descendant_levels(root_pid: int, *, timeout: float = 5.0) -> list[list[int]]:
    """`root_pid` 的后代，按深度分层（第 0 层 = 直接子进程）。读不到进程表回空（调用方退回按组杀）。"""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=runtime.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    levels: list[list[int]] = []
    frontier = kids.get(root_pid, [])
    seen = {root_pid}
    while frontier:
        levels.append([p for p in frontier if p not in seen])
        seen.update(frontier)
        frontier = [c for p in frontier for c in kids.get(p, []) if c not in seen]
    return [lv for lv in levels if lv]


def _kill_tree(proc: subprocess.Popen) -> None:
    """超时收尾：**叶子先死**。conda 还活着时它的子进程先被杀，由 conda（它在 wait 它们）收尸，不留僵尸；
    最后才杀 conda 本身并 wait。整组一起 SIGKILL 会让子进程在父进程来不及收尸时变成孤儿（PID 1 不收尸的容器里就是僵尸）。
    Windows 没有进程组 / 信号：`taskkill /T /F` 一次收整棵树（输出丢弃，不涉及控制台代码页）。"""
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_KILL_TREE_BUDGET_S * 3,
                creationflags=runtime.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        proc.kill()
        try:
            proc.wait(timeout=_KILL_TREE_BUDGET_S)
        except subprocess.TimeoutExpired:
            LOG.warning("Conda 激活超时收尾：进程 %s 在期限内没能收掉", proc.pid)
        return
    if os.name != "nt":
        # 整个收尾一个总期限：再深的进程链也只多花 `_KILL_TREE_BUDGET_S`，到点余下的整组一起杀（下面的 killpg 兜底）
        deadline = time.monotonic() + _KILL_TREE_BUDGET_S
        for level in reversed(_descendant_levels(proc.pid, timeout=_KILL_TREE_BUDGET_S)):
            for pid in level:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(0.1, left))  # 让还活着的父进程（conda / 它起的 shell）收掉这一层
    proc.kill()
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # 兜底：组里仍有漏网的
        except OSError:
            pass
    # 收尸也有期限：卡在不可中断 I/O 里的进程 SIGKILL 之后也不会马上可收，不能让调用方无限等（它最终退出时由
    # Popen 的回收机制收掉）
    try:
        proc.wait(timeout=_KILL_TREE_BUDGET_S)
    except subprocess.TimeoutExpired:
        LOG.warning("Conda 激活超时收尾：进程 %s 在期限内没能收掉", proc.pid)


def _capture(python: str, lau: CondaLauncher, timeout: float) -> dict[str, str | None]:
    """跑一次 `conda run … python -c <写 os.environ 到结果文件>`；任何失败 / 超时 → `LauncherUnavailable`。"""
    result_path = ""
    script_path = ""
    sweep_results()  # 顺手清过期残留（上次删不掉的、启动之后才过期的）：只删过了过期线的，在途的都还新
    try:
        results = _results_dir()
        results.mkdir(parents=True, exist_ok=True)
        fd, result_path = tempfile.mkstemp(prefix="activated-", suffix=".json", dir=results)
        os.close(fd)  # mkstemp 建出来就是 0600
        with _inflight_lock:
            _inflight.add(result_path)
        if _is_windows():
            # `conda run` 在 Windows 上把命令行写进临时 .bat：多行 / 带引号的 `-c` 参数会被 cmd 破坏。改用脚本文件，
            # argv 里只剩路径（Popen 列表 → CreateProcess，不经 cmd.exe）
            sfd, script_path = tempfile.mkstemp(prefix="capture-", suffix=".py", dir=results)
            with os.fdopen(sfd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_CAPTURE_SRC)
            with _inflight_lock:
                _inflight.add(script_path)
            script_args = [script_path, result_path]
        else:
            script_args = ["-c", _CAPTURE_SRC, result_path]
        argv = [
            lau.conda_exe,
            "run",
            "--no-capture-output",
            "--prefix",
            lau.prefix,
            python,
            # -I -S：只读 os.environ，不跑环境里的 sitecustomize / .pth / PYTHONPATH 模块——“检查”不能变成“执行环境里的代码”，
            # 也不能把 sitecustomize 对环境的改动当成激活增量（真正的 worker 起来还会再跑一次）
            "-I",
            "-S",
            *script_args,
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),  # 超时能把 conda 起的整棵树一起收掉
            creationflags=runtime.CREATE_NO_WINDOW,
        )
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise LauncherUnavailable(
                f"经 Conda 激活取环境超时（{timeout:.0f}s）；请检查这个环境的 activate.d 脚本"
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
        raise LauncherUnavailable(f"无法经 Conda 激活取环境：{exc.__class__.__name__}") from exc
    finally:
        for leftover in (result_path, script_path):
            if not leftover:
                continue
            try:
                os.unlink(leftover)
            except FileNotFoundError:
                gone = True
            except OSError:
                # 删不掉（瞬时的文件系统错误；Windows 上进程刚死文件句柄还没放）：留在在途登记里，退出时
                # `_cleanup_inflight` 再删一次；下一次捕获前的 `sweep_results` 也会扫到过期的
                gone = False
            else:
                gone = True
            if gone:
                with _inflight_lock:
                    _inflight.discard(leftover)
    delta: dict[str, str | None] = {
        str(k): str(v) for k, v in data.items() if k not in _VOLATILE_ENV and os.environ.get(k) != v
    }
    # 激活去掉的变量（`unset PYTHONPATH` 之类）：捕获进程继承了我们的环境，所以“我们有、它没有”就是被激活去掉的——
    # 用 None 表示“要从子进程环境里删掉”。每个进程自己会变的（`_VOLATILE_ENV`）永不删
    for k in os.environ:
        if k not in data and k not in _VOLATILE_ENV:
            delta[k] = None
    return delta


def activated_delta(
    python: str, lau: CondaLauncher, *, timeout: float | None = None
) -> dict[str, str | None]:
    """`python` 经 Conda 激活后相对当前进程环境的**增量**（值为 None = 激活把它去掉了）（进程内缓存；环境版本变了重新捕获）。
    失败抛 `LauncherUnavailable`，**不缓存失败**。"""
    key = (lau.prefix, prefix_state(lau.prefix), lau.conda_exe)
    with _captured_lock:
        hit = _captured.get(key)
        gate = _capture_gates.setdefault(key, threading.Lock())
    if hit is not None:
        return hit
    if getattr(_no_capture, "on", False):
        raise CaptureDeferred("激活缓存在起会话前失效了（环境刚被修改）；出锁重新预热")
    budget = CAPTURE_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + budget
    # 单飞：并发的同一把键只捕获一次，其余等它的结果；等也只等自己的预算（排在 60s 预热后面的 7s 体检不能被拖满一分钟）
    if not gate.acquire(timeout=max(budget, 0.0)):
        raise LauncherUnavailable(
            f"经 Conda 激活取环境超时（{budget:.0f}s，另一个请求正在捕获同一个环境）"
        )
    try:
        with _captured_lock:
            hit = _captured.get(key)
        if hit is not None:
            return hit
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LauncherUnavailable(f"经 Conda 激活取环境超时（{budget:.0f}s）")
        delta = _capture(str(python), lau, remaining)
        if prefix_state(lau.prefix) != key[1]:
            # 捕获期间环境被改了（装 / 卸包、改 activate.d）：这份结果可能取自改到一半的 prefix，不用也不缓存
            raise LauncherUnavailable("经 Conda 激活取环境时这个环境正在被修改；请稍后再试")
        LOG.info("Conda 激活环境已捕获：%d 个变量（不落盘）", len(delta))  # 只记个数，不记名字和值
        with _captured_lock:
            _captured[key] = delta
        return delta
    finally:
        gate.release()


def merge_env(
    lau: CondaLauncher | None, python, base: dict | None = None, *, timeout: float | None = None
) -> dict | None:
    """起子进程用的 env：没有 launcher 原样返回 `base`（None = 继承）；有就是 `base`（或当前环境）叠上激活增量。
    调用方传进来的就是它原来要用的 env（`worker_env` / `runtime.probe_env` / `owned_env`），Tavotto 自己的增量保留。"""
    if lau is None:
        return base
    env = dict(base if base is not None else os.environ)
    for key, value in activated_delta(str(python), lau, timeout=timeout).items():
        if value is None:
            env.pop(key, None)  # 激活去掉的变量：启动时同样去掉（所有启动点都经这一处）
        else:
            env[key] = value
    return env


def activate_within(python, base: dict | None, budget: float) -> tuple[dict | None, float]:
    """有总预算的探测取 env 的唯一写法：激活捕获用 `budget`，回 (env, 剩给子进程的秒数)。预算被激活用完抛
    `LauncherUnavailable`（瞬时）——子进程不能再拿一份完整的超时（那样名义 30s 的探测能拖到 90s）。"""
    deadline = time.monotonic() + budget
    env = activate_env(python, base, timeout=budget)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LauncherUnavailable("经 Conda 激活取环境用完了这次探测的时间预算；请稍后再试")
    return env, remaining


def activate_env(python, base: dict | None = None, *, timeout: float | None = None) -> dict | None:
    """所有「用目标解释器起子进程」的地方取 env 的唯一入口：`launcher_for_interpreter` 的决策 + 合并激活增量。
    Conda 环境捕获失败抛 `LauncherUnavailable`——**没有**“范围外降级成裸起”的分支；可信安装根里的 conda 不可用也一样。"""
    raise_if_trusted_root_unusable(python)
    return merge_env(launcher_for_interpreter(python), python, base, timeout=timeout)


class CaptureDeferred(LauncherUnavailable):
    """持着池锁时激活缓存没命中：不在锁里捕获，交回调用方出锁预热再来（不是对环境的结论）。"""


def is_capture_deferred(exc: BaseException | None) -> bool:
    """异常链（`__cause__` / `__context__`）上有没有 `CaptureDeferred`：中间各层可能已把它转成自己的错误码。"""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, CaptureDeferred):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


_no_capture = threading.local()


@contextlib.contextmanager
def no_capture():
    """这段代码（持着池锁起会话）里只许命中激活缓存；没命中抛 `CaptureDeferred`，绝不在锁里跑一次最长 60s 的捕获。"""
    prev = getattr(_no_capture, "on", False)
    _no_capture.on = True
    try:
        yield
    finally:
        _no_capture.on = prev


def prewarm(python, *, timeout: float | None = None) -> None:
    """（不持任何池锁时）先把这条解释器的激活增量捕获进缓存；之后锁内起会话只命中缓存。不需要激活的解释器什么都不做。"""
    raise_if_trusted_root_unusable(python)
    lau = launcher_for_interpreter(python)
    if lau is not None:
        activated_delta(str(python), lau, timeout=timeout)
