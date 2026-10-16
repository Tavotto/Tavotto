"""用户自己的 Python 环境从哪来（ADR 0079）：**只发现，不体检、不决策**。

内置环境缺包时，最便宜的出路往往不是装包，而是用户平时跑这个脚本的那个 Python——它多半
早就装齐了。ADR 0018 §三 / ADR 0044 当时把 Conda / pyenv 推迟，理由是「要问它们的 CLI 才知道
环境在哪，可能要几秒」。这里一个 CLI 都不问，只读它们自己落在磁盘上的记录：

1. **项目里的线索**（离用户意图最近，排最前）：`.vscode/settings.json` 的
   `python.defaultInterpreterPath` / `python.pythonPath`、`.python-version`（pyenv 版本名）、
   `environment.yml` 的 `name:`（Conda 环境名）、脚本第一行的 shebang（写死了解释器路径时）。
   只在「脚本所在目录 → 项目根」这条链上找，不上溯到项目之外（ADR 0018 §三同一条边界）。
2. **终端里默认的 python**：桌面版由 GUI 启动，PATH 很短（`/usr/bin:/bin:…`），`shutil.which`
   只找得到系统自带的那个。用户在终端里敲 `python3` 得到的那个——Conda 的自动激活、pyenv 的
   shim、Homebrew——要问他的**交互式登录 shell** 才知道（Conda 的 init 写在 `.zshrc`，只有交互式
   shell 读它）。只在 POSIX 上问；有超时；输出按标记行取，rc 文件打印的杂讯不影响。
3. **Conda 的全部环境**：Conda 自己把每个建过的环境前缀记在 `~/.conda/environments.txt`；再加上
   常见安装根（`~/anaconda3`、`~/opt/anaconda3`、`~/miniforge3`……）与它们的 `envs/*`。
4. **pyenv 的全部版本**：`$PYENV_ROOT/versions/*`（pyenv-win 在 `pyenv-win/versions/*`）。

每一条都是 `{"python", "source", "label"}`：`python` 是**确实存在的文件**，路径不 realpath
（`.venv/bin/python` 是软链接，按终点去重会把 venv 与它的基础解释器判成同一个——ADR 0044 §四，
这是那条老坑的第四次）。体检与「装没装齐」由 `deprepair.user_environment_offer()` 做。
"""

from __future__ import annotations

import glob
import logging
import ntpath
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import launcher, projectenv, runtime, scanbudget

LOG = logging.getLogger("tavotto.userenvs")

SOURCE_VSCODE = "vscode"
SOURCE_PYTHON_VERSION = "python_version_file"
SOURCE_ENVIRONMENT_YML = "environment_yml"
SOURCE_SHEBANG = "shebang"
SOURCE_LOGIN_SHELL = "login_shell"
#: 项目自带的 venv（`projectenv.discover`）。检测 / 确认模式（ADR 0114）下它和用户的其它环境一起进候选表——
#: 旧模式（`legacy`）由 `pool` 第 4 档单独处理，不在这张表里
SOURCE_PROJECT_VENV = "project_venv"
SOURCE_CONDA = "conda"
SOURCE_PYENV = "pyenv"
#: 顺序即优先级（`discover()` 按它拼表）
SOURCES = (
    SOURCE_PROJECT_VENV,
    SOURCE_VSCODE,
    SOURCE_PYTHON_VERSION,
    SOURCE_ENVIRONMENT_YML,
    SOURCE_SHEBANG,
    SOURCE_LOGIN_SHELL,
    SOURCE_CONDA,
    SOURCE_PYENV,
)

#: 项目自己声明 / 编辑器指向的那几类来源（`rank()` 的第 0 档）：检测模式下默认链条能跑这个脚本时，只有它们还排在
#: 默认链条前面（ADR 0057 FO11：项目带了自己的环境就用它；ADR 0114 §六）
PROJECT_SOURCES = frozenset(
    {
        SOURCE_PROJECT_VENV,
        SOURCE_VSCODE,
        SOURCE_PYTHON_VERSION,
        SOURCE_ENVIRONMENT_YML,
        SOURCE_SHEBANG,
    }
)

#: 路径由项目文件**直接写出**的来源（`.vscode/settings.json` 的 `python.defaultInterpreterPath`、脚本 shebang、项目里的 venv）。
#: `.python-version` / `environment.yml` 只给版本名 / 环境名，路径是在用户自己的 pyenv / Conda 里查出来的——不在此列
PROJECT_PATH_SOURCES = frozenset({SOURCE_PROJECT_VENV, SOURCE_VSCODE, SOURCE_SHEBANG})


def is_project_controlled(cand: dict, root: str | Path) -> bool:
    """候选解释器是不是**项目说了算**的（唯一判据；Codex 安全 #820 r4232804805）：来源是项目文件直接写出路径的
    （`PROJECT_PATH_SOURCES`：项目 venv、`.vscode`、shebang），或者它的路径（原样 / 解
    符号链接后）落在项目根里。攻击者提供的项目可以让这样的路径指向一个会执行任意代码的文件，所以用户点「运行」之前不
    起它（体检就是起它）。"""
    if cand.get("source") in PROJECT_PATH_SOURCES:
        return True
    python = cand.get("python") or ""
    if not python:
        return False
    # 分类不得先 realpath：项目里被重定向的路径（junction / 符号链接）一经 realpath 就会去探目标。逐级 lstat 发现重定向
    # 直接判"项目说了算"，不碰目标
    if scanbudget.redirected_component(root, python, allow_final_link=True) is not None:
        return True
    base = os.path.abspath(str(root))
    for path in (os.path.abspath(python), os.path.realpath(python)):
        try:
            if os.path.commonpath(
                [os.path.normcase(base), os.path.normcase(path)]
            ) == os.path.normcase(base):
                return True
        except ValueError:  # 不同盘符
            continue
    return False


#: 问登录 shell 最多等多久。交互式 shell 要读 rc 文件（Conda init、oh-my-zsh……），冷启动一两秒常见
LOGIN_SHELL_TIMEOUT_S = 8.0
_MARK = "__TAVOTTO_PY__"

#: 登录 shell 答了空表（rc 文件慢 / 超时 / 那一刻 PATH 还没就绪）之后，多久内不再重问。有结论的答案不因时间失效
EMPTY_LOGIN_SHELL_TTL_S = 30.0
_monotonic = time.monotonic

_lock = threading.Lock()
_login_shell_cache: dict[str, list[str]] = {}
#: 空答案是什么时候得到的（`_monotonic()` 时刻）；有结论的 shell 不在这张表里
_login_shell_empty_at: dict[str, float] = {}


def reset_cache() -> None:
    """用户点「重新检查」= 推翻旧结论（与 `projectenv.reset_cache()` 一起清）。"""
    with _lock:
        _login_shell_cache.clear()
        _login_shell_empty_at.clear()
        _probe_cache.clear()


def forget_empty_login_shell() -> None:
    """明确的检查动作之前调：只忘掉**空**的登录 shell 答案（有结论的保留），下一次 `login_shell_pythons()` 重问。"""
    with _lock:
        for shell in list(_login_shell_empty_at):
            _login_shell_cache.pop(shell, None)
            _login_shell_empty_at.pop(shell, None)


def _home() -> str:
    return os.path.expanduser("~")


def _local_hint(path: str) -> bool:
    """Lexically reject network/device namespaces before filesystem discovery.

    Normalize separators as text on every host: a Windows project can be scanned
    on POSIX too. Do not resolve/stat a rejected hint, since even existence checks
    may contact SMB/WebDAV and send the user's network authentication on Windows.
    """
    value = path.replace("\\", "/")
    return not value.startswith(("//", "/??/"))


#: 项目内线索文件（`.vscode/settings.json` / `.python-version` / `environment.yml`）的读取上限。
#: 与 `scanbudget.MAX_FILE_BYTES` 同值：超过就当读不了，不是「读一半」。
MAX_HINT_BYTES = scanbudget.MAX_FILE_BYTES


def _read_hint_text(base: Path, *parts: str, no_follow: bool = False) -> str:
    """读项目里的一个线索文件：唯一读法在 `scanbudget.read_regular_text`（依赖声明共用），上限 `MAX_HINT_BYTES`。"""
    return scanbudget.read_regular_text(base, *parts, no_follow=no_follow, max_bytes=MAX_HINT_BYTES)


def _walker(budget: scanbudget.Budget | None = None, walker=None, *, nt: bool | None = None):
    """扫描里构造候选路径用的逐级不跟随检查（Windows 语义才生效，见 `scanbudget.PathWalker`）。"""
    return walker if walker is not None else scanbudget.PathWalker(nt=nt, budget=budget)


def _is_python_file(path: str, *, nt: bool | None = None, walker=None) -> bool:
    if not _local_hint(path):
        return False
    try:
        # 构造出的候选可能在任何一级祖先上是指向断线共享的 junction：先逐级不跟随地验，再让 `is_file` 碰它
        if _walker(None, walker, nt=nt).redirected(path) is not None:
            return False
        p = Path(path)
        return p.name.lower().startswith("python") and p.is_file()
    except (OSError, ValueError):
        return False


def _prefix_python(prefix: str, *, nt: bool | None = None, walker=None) -> str | None:
    """环境前缀（Conda / pyenv / venv）→ 里面的解释器；没有回 None。

    Windows：Conda / pyenv-win 的解释器在前缀根，venv 的在 `Scripts\\python.exe`。`nt` 仅供测试在别的平台上求值路径组合。"""
    if not _local_hint(prefix):
        return None
    nt = os.name == "nt" if nt is None else nt
    walker = _walker(None, walker, nt=nt)
    if (
        walker.redirected(prefix) is not None
    ):  # 前缀自己或祖先是重定向：下面的 `Scripts` / `python.exe` 一级都不碰
        return None
    names = ("python.exe", "Scripts/python.exe") if nt else ("bin/python3", "bin/python")
    join = ntpath.join if nt else os.path.join
    for name in names:
        cand = join(prefix, *name.split("/"))
        if _is_python_file(cand, nt=nt, walker=walker):
            return cand
    return None


# ------------------------------------------------------------------ 项目里的线索


def _hint_dirs(root_real: str, script_path: str | None) -> list[Path]:
    """脚本所在目录 → 项目根（含两端），近的在前。

    `script_path` 必须是 `projectenv.contained_path()` 的**输出**（已 realpath、已钉在项目内）：`script`
    可以来自请求体（`PATCH /api/engine/environment` 的 `script`），不净化就拼路径、open、resolve 是
    CodeQL `py/path-injection`，也真能读到项目外文件的第一行。往上走只做字符串运算，止于项目根。"""
    out: list[Path] = []
    cur = os.path.dirname(script_path) if script_path else root_real
    for _ in range(64):
        out.append(Path(cur))
        if cur == root_real or len(cur) <= len(root_real):
            break
        cur = os.path.dirname(cur)
    return out


_VSCODE_KEY = re.compile(r'"python\.(?:defaultInterpreterPath|pythonPath)"\s*:\s*"([^"]+)"')


def _vscode_pythons(dirs: list[Path], *, no_follow: bool = False) -> list[str]:
    # settings.json 是 JSONC（注释、尾逗号）；只要这一个键，正则比「先把 JSONC 洗成 JSON」可靠
    out: list[str] = []
    for d in dirs:
        try:
            text = _read_hint_text(d, ".vscode", "settings.json", no_follow=no_follow)
        except OSError:
            continue
        for raw in _VSCODE_KEY.findall(text):
            value = raw.replace("${workspaceFolder}", str(d)).replace("\\\\", "\\")
            value = os.path.expanduser(value)
            if not _local_hint(value):
                continue
            if not os.path.isabs(value):
                value = str(d / value)
            out.append(value)
    return out


def _pyenv_root(path=os.path) -> str:
    return os.environ.get("PYENV_ROOT") or path.join(_home(), ".pyenv")


def _pyenv_version_dirs(*, nt: bool | None = None) -> list[str]:
    """pyenv 的 versions 目录。pyenv-win 的 `PYENV_ROOT` 通常**已经**指到 `…\\.pyenv\\pyenv-win`，不能再追加一层
    `pyenv-win`；只给到 `…\\.pyenv` 时才补。`nt` 仅供测试在别的平台上用 ntpath 求值，不是 Windows 真机。"""
    nt = os.name == "nt" if nt is None else nt
    if nt:
        root = _pyenv_root(ntpath).rstrip("\\/")
        base = (
            root if ntpath.basename(root).lower() == "pyenv-win" else ntpath.join(root, "pyenv-win")
        )
        return [ntpath.join(base, "versions")]
    return [os.path.join(_pyenv_root(), "versions")]


def _python_version_pythons(dirs: list[Path], *, no_follow: bool = False) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for d in dirs:
        try:
            lines = _read_hint_text(d, ".python-version", no_follow=no_follow).split()
        except OSError:
            continue
        for name in lines:
            if not name or "/" in name or "\\" in name or name.startswith("."):
                continue
            for vdir in _pyenv_version_dirs():
                py = _prefix_python(os.path.join(vdir, name))
                if py:
                    out.append((py, name))
        break  # pyenv 自己也只认最近的那一份
    return out


_ENV_NAME = re.compile(r"^name:\s*['\"]?([A-Za-z0-9_.\-]+)['\"]?\s*$", re.M)


def _environment_yml_pythons(
    dirs: list[Path],
    *,
    no_follow: bool = False,
    budget: scanbudget.Budget | None = None,
    walker=None,
) -> list[tuple[str, str]]:
    walker = _walker(budget, walker)
    out: list[tuple[str, str]] = []
    for d in dirs:
        for fname in ("environment.yml", "environment.yaml"):
            try:
                text = _read_hint_text(d, fname, no_follow=no_follow)
            except OSError:
                continue
            m = _ENV_NAME.search(text)
            if not m:
                continue
            name = m.group(1)
            for prefix in _conda_prefixes(budget, walker):
                if os.path.basename(prefix.rstrip("/\\")) == name:
                    py = _prefix_python(prefix, walker=walker)
                    if py:
                        out.append((py, name))
    return out


def _shebang_python(script_path: str | None, *, no_follow: bool = False) -> str | None:
    """`script_path` 同 `_hint_dirs`：只认 `contained_path()` 的输出。只读普通文件的前 512 字节。"""
    if not script_path:
        return None
    try:
        if no_follow and scanbudget.is_redirect(os.lstat(script_path)):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        if no_follow:
            flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(script_path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            first = os.read(fd, 512).split(b"\n", 1)[0]
        finally:
            os.close(fd)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", "replace").strip().split()
    # `#!/usr/bin/env python3` 说的就是「PATH 上那个」——登录 shell 那一条会找到它
    if not parts or os.path.basename(parts[0]) == "env":
        return None
    return parts[0]


# ------------------------------------------------------------------ 终端里默认的 python


def _login_shell() -> str | None:
    if os.name == "nt":
        return None
    shell = os.environ.get("SHELL") or ""
    if not shell:
        try:
            import pwd

            shell = pwd.getpwuid(os.getuid()).pw_shell
        except (ImportError, KeyError, OSError):
            shell = ""
    if not shell or not os.path.isabs(shell) or not os.access(shell, os.X_OK):
        return None
    return shell


def _ask_login_shell(shell: str) -> list[str]:
    """交互式登录 shell 里 `command -v python3` / `python` 的结果（按这个顺序，去空）。"""
    if os.path.basename(shell) == "fish":
        cmd = (
            f'printf "{_MARK}%s\\n" (command -v python3); printf "{_MARK}%s\\n" (command -v python)'
        )
    else:
        cmd = (
            f'printf "{_MARK}%s\\n" "$(command -v python3)"; '
            f'printf "{_MARK}%s\\n" "$(command -v python)"'
        )
    try:
        proc = subprocess.run(  # noqa: S603 — 用户自己的登录 shell，命令是上面的字面量
            [shell, "-l", "-i", "-c", cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=LOGIN_SHELL_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
            cwd=_home(),
            creationflags=runtime.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.info("问登录 shell 失败（%s）：%s", shell, exc)
        return []
    out: list[str] = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith(_MARK):
            value = line[len(_MARK) :].strip()
            if value and os.path.isabs(value):
                out.append(value)
    return out


def cached_login_shell_pythons() -> list[str]:
    """登录 shell **已经被明确问过**的结果（没问过回空表）。纯读缓存，不启动 shell：确认模式下只有明确的
    检查动作（`envadvice.check(include_login_shell=True)`）才会问，其余读者只看它留下的答案（ADR 0114 §三）。"""
    shell = _login_shell()
    if not shell:
        return []
    with _lock:
        return list(_login_shell_cache.get(shell) or [])


def login_shell_pythons() -> list[str]:
    shell = _login_shell()
    if not shell:
        return []
    with _lock:
        hit = _login_shell_cache.get(shell)
        # 空答案只管一小段时间：一次超时 / 冷启动的空结果不该让这个进程里此后永远看不见终端里的 python
        if hit is not None and not hit:
            if _monotonic() - _login_shell_empty_at.get(shell, 0.0) >= EMPTY_LOGIN_SHELL_TTL_S:
                hit = None
    if hit is not None:
        return list(hit)
    found = _ask_login_shell(shell)
    with _lock:
        _login_shell_cache[shell] = found
        if found:
            _login_shell_empty_at.pop(shell, None)
        else:
            _login_shell_empty_at[shell] = _monotonic()
    return list(found)


# ------------------------------------------------------------------ Conda / pyenv


def _conda_roots() -> list[str]:
    home = _home()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or ""
        names = ("anaconda3", "miniconda3", "miniforge3", "mambaforge")
        roots = [os.path.join(home, n) for n in names]
        roots += [os.path.join(local, n) for n in names] if local else []
        roots += [os.path.join("C:\\ProgramData", n) for n in names]
        return roots
    names = ("anaconda3", "miniconda3", "miniforge3", "mambaforge", "micromamba")
    roots = [os.path.join(home, n) for n in names]
    roots += [os.path.join(home, "opt", n) for n in names]  # 老版 Anaconda 图形安装器
    roots += [os.path.join("/opt", n) for n in names]
    roots += [os.path.join("/opt/homebrew", n) for n in names]
    roots += [
        "/opt/homebrew/Caskroom/miniforge/base",
        "/opt/homebrew/Caskroom/miniconda/base",
        "/usr/local/Caskroom/miniforge/base",
        "/usr/local/Caskroom/miniconda/base",
        "/usr/local/anaconda3",
        "/usr/local/miniconda3",
    ]
    return roots


def _scan_children(pattern_dir: str, budget: scanbudget.Budget | None, walker=None) -> list[str]:
    """`pattern_dir/*` 的子项（排序）。有预算时用 `os.scandir` **增量**枚举：每项记一笔、到期 / 取消就收手
    （返回已枚举到的那部分，账本由 `Budget.stop_reason()` 记 time_budget / cancelled）。"""
    if (
        _walker(budget, walker).redirected(pattern_dir) is not None
    ):  # 枚举前先验：祖先是 junction 就不 scandir / glob
        return []
    if budget is None:
        return sorted(glob.glob(os.path.join(pattern_dir, "*")))
    names: list[str] = []
    try:
        with os.scandir(pattern_dir) as it:
            for entry in it:
                if not budget.charge_entry():
                    break
                if entry.name.startswith("."):  # 与 glob 的 `*` 一致：不列点开头
                    continue
                names.append(os.path.join(pattern_dir, entry.name))
    except OSError:
        pass
    return sorted(names)


def _conda_environments_txt(budget: scanbudget.Budget | None, walker=None) -> list[str]:
    path = Path(_home(), ".conda", "environments.txt")
    if _walker(budget, walker).redirected(path.parent) is not None:
        return []
    try:
        if budget is None:
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            # 有界、只认普通文件（FIFO / 超大文件不挂住扫描）：读不了记一条看不全，不是「没有」
            text = scanbudget.read_regular_text(path.parent, path.name, max_bytes=MAX_HINT_BYTES)
    except FileNotFoundError:
        return []
    except OSError:
        if budget is not None:
            budget.note(scanbudget.ISSUE_UNREADABLE_FILE, scope="environment")
        return []
    out: list[str] = []
    for ln in text.splitlines():
        if budget is not None and not budget.charge_entry():
            break
        if ln.strip():
            out.append(ln.strip())
    return out


def _conda_prefixes(budget: scanbudget.Budget | None = None, walker=None) -> list[str]:
    """Conda 知道的每一个环境前缀（base 与具名环境），去重、保序。

    `budget`（导入即扫描传）：`environments.txt` 有界读、`envs` 目录增量枚举，到期 / 取消返回已有的那部分。"""
    walker = _walker(budget, walker)
    out: list[str] = _conda_environments_txt(budget, walker)
    for root in _conda_roots():
        if budget is not None and budget.stop_reason() is not None:
            break
        if _local_hint(root) and walker.redirected(root) is None and os.path.isdir(root):
            out.append(root)
            out += _scan_children(os.path.join(root, "envs"), budget, walker)
    if budget is None or budget.stop_reason() is None:
        out += _scan_children(os.path.join(_home(), ".conda", "envs"), budget, walker)
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _conda_label(prefix: str) -> str:
    parent = os.path.basename(os.path.dirname(prefix.rstrip("/\\")))
    name = os.path.basename(prefix.rstrip("/\\"))
    return name if parent == "envs" else "base"


def _pyenv_pythons(budget: scanbudget.Budget | None = None, walker=None) -> list[tuple[str, str]]:
    walker = _walker(budget, walker)
    out: list[tuple[str, str]] = []
    for vdir in _pyenv_version_dirs():
        for prefix in sorted(_scan_children(vdir, budget, walker), reverse=True):
            if budget is not None and budget.stop_reason() is not None:
                return out
            py = _prefix_python(prefix, walker=walker)
            if py:
                out.append((py, os.path.basename(prefix)))
    return out


# ------------------------------------------------------------------ 合起来


def _key(python: str) -> tuple[str, str]:
    """去重键，实现见 `projectenv.env_key`（`pool` 也要算 id，不能反过来依赖本模块）。"""
    return projectenv.env_key(python)


def discover(
    figures_dir: str | Path,
    script: str | None = None,
    *,
    ask_login_shell: bool | None = True,
    no_follow: bool = False,
    budget: scanbudget.Budget | None = None,
) -> list[dict]:
    """按优先级排好的候选表（去重、只含存在的文件）。不起任何 Python；默认可能问一次登录 shell。

    **线索与体检分开**（T02）：`ask_login_shell=False` 时只读磁盘上的记录（项目里的线索、Conda 的
    `environments.txt` 与常见安装根、pyenv 的 versions），**不启动登录 shell**——登录 shell 要读用户的
    rc 文件（可能是任何东西）才答得出，那是 T05 的明确检查动作，不是导入时的读线索。导入即扫描永远
    传 False；默认值保持老行为（静默采用兼容开关下，准备 / 依赖门里的 `deprepair` 仍问）。
    `None`（T05）：不问、但把**已经被明确问过**的登录 shell 答案并进来（`cached_login_shell_pythons`）。

    `no_follow=True`（导入即扫描传）：项目里的线索文件（`.vscode/settings.json` / `.python-version` /
    `environment.yml` / 脚本 shebang）不跟随符号链接 / 路径替身，且无论哪种形态都只读有上限的普通文件。

    `budget`（导入即扫描传）：Conda `environments.txt` / `envs` 与 pyenv `versions` 的枚举拿得到墙钟预算与取消回调，
    到期 / 取消就停在已枚举到的部分（账本记 time_budget / cancelled → 报告 partial）。不传 = 老行为。"""
    # `script` 可能来自请求体：先钉在项目内，下游一律用净化器回的那一条；越界就当没给脚本
    root_real = os.path.realpath(os.fspath(figures_dir))
    script_path = projectenv.contained_path(root_real, script) if script else None
    dirs = _hint_dirs(root_real, script_path)
    walker = _walker(
        budget
    )  # 本次发现里所有「构造候选 → 探文件」共用：逐级不跟随检查，目录结果缓存
    raw: list[tuple[str, str, str]] = []
    raw += [(p, SOURCE_VSCODE, "") for p in _vscode_pythons(dirs, no_follow=no_follow)]
    raw += [
        (p, SOURCE_PYTHON_VERSION, n) for p, n in _python_version_pythons(dirs, no_follow=no_follow)
    ]
    raw += [
        (p, SOURCE_ENVIRONMENT_YML, n)
        for p, n in _environment_yml_pythons(
            dirs, no_follow=no_follow, budget=budget, walker=walker
        )
    ]
    shebang = _shebang_python(script_path, no_follow=no_follow)
    if shebang:
        raw.append((shebang, SOURCE_SHEBANG, ""))
    if ask_login_shell:
        raw += [(p, SOURCE_LOGIN_SHELL, "") for p in login_shell_pythons()]
    elif ask_login_shell is None:
        raw += [(p, SOURCE_LOGIN_SHELL, "") for p in cached_login_shell_pythons()]
    for pre in _conda_prefixes(budget, walker):
        if budget is not None and budget.stop_reason() is not None:
            break
        py = _prefix_python(pre, walker=walker)
        if py:
            raw.append((py, SOURCE_CONDA, _conda_label(pre)))
    if budget is None or budget.stop_reason() is None:
        raw += [(p, SOURCE_PYENV, n) for p, n in _pyenv_pythons(budget, walker)]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for python, source, label in raw:
        if budget is not None and budget.stop_reason() is not None:
            break
        # 项目派生的候选（vscode / .python-version / environment.yml / shebang 解析出的项目内路径）：**永远**——不止导入即扫描，
        # 准备会话的检查也走这里（Codex 安全 #820 r4234904358：Windows 上项目里的 junction 可以把看着像本地的线索指到攻击者的
        # SMB / WebDAV，一次 `is_file` 就发出网络认证）——在任何跟随链接的谓词（is_file / realpath）之前逐级 lstat，被重定向的
        # 丢弃并记账，不探目标。**线索文件本身**（`.vscode/settings.json` 是用户 dotfile 管理的符号链接）仍按 `no_follow` 读：
        # 老路径保持跟随用户自己的链接（tests/test_user_environment_no_follow.py 钉着）
        bad = scanbudget.redirected_component(root_real, python, allow_final_link=True)
        if bad is not None:
            if budget is not None:
                budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="file", path=bad)
            continue
        if (
            source == SOURCE_VSCODE
            and _local_hint(python)
            and walker.redirected(python) is not None
        ):
            continue  # 机器级线索（项目之外）的某一级祖先 / 叶子是重定向：`isdir` 之前就丢弃（记账本由 walker 做）
        if source == SOURCE_VSCODE and os.path.isdir(python):
            # IDE 线索可以指向环境**文件夹**（`python.defaultInterpreterPath` 两种写法都合法）：换成里面的解释器，
            # 它自己的路径也过一遍重定向检查——文件夹没被重定向不代表 `bin` 没有
            inner = _prefix_python(python, walker=walker)
            if inner is None:
                continue
            bad = scanbudget.redirected_component(root_real, inner, allow_final_link=True)
            if bad is not None:
                if budget is not None:
                    budget.note(scanbudget.ISSUE_SYMLINK_DIR, scope="file", path=bad)
                continue
            python = inner
        if not _is_python_file(python, walker=walker):
            continue
        key = _key(python)
        if key in seen:
            continue
        seen.add(key)
        out.append({"python": python, "source": source, "label": label})
    return out


# ------------------------------------------------------------------ 体检与挑选

SOURCE_SYSTEM = "system"  # 老链条枚举的系统解释器（ADR 0044 那一层），并进同一张表
#: 一次最多体检多少个候选；每个是一次子进程（import matplotlib + worker 启动链 + 脚本要的包）
PROBE_LIMIT = 12
PROBE_WORKERS = 4

#: 来源的档位（小 = 更接近用户意图）。`best()` 的第二把尺子
_SOURCE_RANK = {
    SOURCE_PROJECT_VENV: 0,
    SOURCE_VSCODE: 0,
    SOURCE_PYTHON_VERSION: 0,
    SOURCE_ENVIRONMENT_YML: 0,
    SOURCE_SHEBANG: 0,
    SOURCE_LOGIN_SHELL: 1,
    # 2 = 名字与项目目录对得上的 Conda 环境（`best()` 里现算）
    SOURCE_CONDA: 3,
    SOURCE_PYENV: 3,
    SOURCE_SYSTEM: 4,
}

#: 体检结论的分类（`evaluate()` 每条结果的 `probe_status`）。**只描述这一次独立探测看到了什么**，不是脚本的成败：
#: 探测通过不等于脚本能跑，探测里 import 失败也不等于脚本跑不了（SDK 常要脚本里先初始化再 import）
PROBE_UNCHECKED = "unchecked"  # 没量过（只读缓存没有结论 / 延后到运行时），不冒充装齐也不冒充没装齐
PROBE_UNLAUNCHABLE = (
    "unlaunchable"  # 解释器起不来或不满足 Tavotto 的健康要求（具体原因在 `code`），模块谈不上
)
PROBE_MODULE_MISSING = "module_missing"  # 解释器健康，但脚本要的某个模块找不到（装包能救）
PROBE_IMPORT_FAILED = (
    "import_failed"  # 模块在，独立 import 时抛了别的（初始化顺序 / 自身依赖 / ABI），装包救不了
)
PROBE_PASSED = "probe_passed"  # 解释器健康且这组 import 都得到——仅此而已
PROBE_STATUSES = (
    PROBE_UNCHECKED,
    PROBE_UNLAUNCHABLE,
    PROBE_MODULE_MISSING,
    PROBE_IMPORT_FAILED,
    PROBE_PASSED,
)

_probe_cache: dict[tuple, dict] = {}


def _dirs_fingerprint(dirs: list[str]) -> tuple:
    """目录 mtime 与 .pth 文件的 (名字, mtime_ns, size)；不存在 = None。装 / 卸包、首次建目录、原地改写
    .pth 均使结论失效。只读目录项与文件元数据，不读 / 执行文件内容，不起解释器。"""
    out = []
    for d in dict.fromkeys(dirs):
        try:
            # 原地改写 .pth 不改变父目录 mtime；两个探测方式必须同时作废旧的缺包结论。
            pths = []
            for p in sorted(Path(d).glob("*.pth")):
                try:
                    st = p.stat()
                except OSError:
                    # 一个已卸载目标留下的坏链接，不得抹掉整个 site 的目录 mtime。
                    pths.append((p.name, None))
                    continue
                pths.append((p.name, st.st_mtime_ns, st.st_size))
            out.append((d, os.stat(d).st_mtime_ns, tuple(pths)))
        except OSError:
            out.append((d, None))
    return tuple(out)


def _health_fingerprint(health: dict) -> tuple:
    """一条体检结论的"站点内容"指纹：**体检进程自己报的目录表**（`site_dirs`——两种方式都报：隔离体检是它真正放进 `sys.path`
    的那些，import 方式是正常启动之后 site / .pth 给出的，含启用时的用户 site 和还不存在的用户 site 目录）。没有这张表
    （启动就失败的结论，没有可看的站点）回空指纹：失败结论不因站点变化重测（重测一个起不来的解释器最长 60 秒）。
    两种体检均报告继承的绝对 PYTHONPATH 路径（含尚未创建的根）；它们后来新增提供者时，旧的缺失结论也要失效。"""
    dirs = health.get("site_dirs")
    if not isinstance(dirs, list):
        return ()
    return _dirs_fingerprint(list(dirs) + list(health.get("pythonpath_dirs") or []))


def _cache_key(
    python: str, modules: tuple[str, ...], bundled: bool, mode: str = "import", root: str = ""
) -> tuple:
    """体检缓存的键：路径 + **环境代**（`projectenv.environment_generation`，与池 / 项目记录同一个"代"的概念）+ 要量的 import
    + 是否内置 + 体检方式。同一路径上被重建的环境换代，旧结论不再命中（Codex #820 r4233884149）。

    `spec`（运行之前不 import 的版本）的结论取决于**项目根**——同一个可编辑安装的模块在项目 A 里解析进项目、在项目 B 里不是——
    所以键里带规范化（realpath）的项目根；`import` 的结论与项目无关，键里没有它（Codex #820 r4234612525）。"""
    from . import projectenv

    how: object = ("spec", os.path.normcase(os.path.realpath(root))) if mode == "spec" else "import"
    return (
        _key(python),
        projectenv.environment_generation(python),
        # 要量的 import 是一个**集合**：调用方（依赖门 / 明确的环境检查）各自拼出来的顺序不同，不许因此各存一份
        tuple(sorted(set(modules))),
        bundled,
        how,
        # 内置 runtime 会摘掉 PYTHONPATH；用户解释器继承它，换值后旧 import / spec 结论均不可复用。
        (os.environ.get("PYTHONPATH") or "") if not bundled else "",
        # 启动上下文（direct / conda|exe|prefix）：裸起的结论不能在激活下复用。放在末尾——前面几位有人按位置读（`key[1]` 是环境代）
        launcher.context_key(python),
    )


def _probe(
    python: str,
    modules: tuple[str, ...],
    *,
    bundled: bool = False,
    mode: str = "import",
    root: str = "",
) -> dict:
    from . import projectenv

    key = _cache_key(python, modules, bundled, mode, root)
    with _lock:
        hit = _probe_cache.get(key)
        if hit is None and mode == "spec":
            # 点过「运行」之后的 import 结论更强。
            hit = _probe_cache.get(_cache_key(python, modules, bundled, "import"))
    if hit is not None and _fresh(python, hit):
        return hit
    # 只在内置 runtime 时才带这个参数：用户环境的体检调用形状与以前逐字相同
    extra = {"bundled": True} if bundled else {}
    if mode == "spec":
        extra.update(import_mode="spec", project_root=root)
    health = projectenv.probe_environment(python, modules=modules, **extra)
    health["_site_fp"] = _health_fingerprint(health)
    if launcher.is_unavailable(health):
        return health  # 激活取环境失败是瞬时的：不缓存
    with _lock:
        _probe_cache[key] = health
    return health


def remember_probe(
    python: str,
    modules: tuple[str, ...],
    health: dict,
    *,
    bundled: bool = False,
    generation: object = None,
    context: str | None = None,
) -> None:
    """把一次**明确的环境检查**（`envadvice.check`）量到的结论写进这张缓存——覆盖度只有这一份。

    键与依赖门 / 检测读的是同一个（路径 + 环境代 + 要量的 import 集合 + 是否内置 + `import` 方式），所以检查之后门与检测
    读到的就是用户刚点的那次检查，不会各自再起一遍；环境被重建（换代）后旧结论自然对不上。不带 `modules` 的结论不存
    （健康体检另有 `envadvice` 的结论表；这里只管覆盖度）。"""
    mods = tuple(dict.fromkeys(m for m in modules if m))
    if not mods or not isinstance(health, dict) or launcher.is_unavailable(health):
        return
    key = _cache_key(python, mods, bundled)
    # `generation`：探测**之前**取的环境代。探测期间环境被重建 / 替换（换代）时，量到的是旧解释器，不许记到新代名下——
    # 键里的代是写入时现算的，站点指纹也是重建后的，新鲜度检查拦不住（Codex #906 r4236570685）；丢掉，下次重量
    if generation is not None and key[1] != generation:
        return
    # `context`：探测之前取的启动上下文（`launcher.context_key`）。conda install / 改 activate.d 不换代，但上下文变了——
    # 旧激活下量到的结论不许记到新上下文名下（键里的上下文同样是写入时现算的）
    if context is not None and key[-1] != context:
        return
    stored = {**health, "_site_fp": _health_fingerprint(health)}
    with _lock:
        _probe_cache[key] = stored


def _fresh(python: str, health: dict) -> bool:
    """缓存的结论还有效吗：打过指纹的，环境的站点内容（装 / 卸包）没变才有效；没打过的（外部塞进来的）照旧信。"""
    fp = health.get("_site_fp")
    return fp is None or fp == _health_fingerprint(health)


def imports_missing(
    python: str, modules: list[str], *, bundled: bool = False, root: str = ""
) -> list[str]:
    """`modules` 里此刻这个解释器**确实** import 不到的那几个（ADR 0079 修订 2026-09-25）。

    与 `evaluate()` 同一条体检（同一个缓存）。判不出的不算缺：体检起不来、结果里没有这一项
    （`modules_ok` 只有真 import 过的才有 True / False）——拿「没量到」去触发发现，就是在一个
    根本没问过的解释器上替用户换环境。`bundled`：它是内置 runtime，按 worker 的环境与参数量
    （`projectenv.probe_environment(bundled=True)`）。

    `root` 给了（运行之前）= 不 import、只 `find_spec`，落在项目里的（`None`）不算缺。"""
    mods = tuple(dict.fromkeys(m for m in modules if m))
    if not mods:
        return []
    probed = (
        _probe(python, mods, bundled=bundled, mode="spec", root=root)
        if root
        else _probe(python, mods, bundled=bundled)
    )
    ok_map = probed.get("modules_ok") or {}
    return [m for m in mods if ok_map.get(m) is False]


def cached_probe(
    python: str, modules: tuple[str, ...], *, bundled: bool = False, root: str = ""
) -> dict | None:
    """这个候选在这组 import 上**已有**的体检结论（只读缓存，没有回 None；不起任何进程）。"""
    with _lock:
        hit = _probe_cache.get(_cache_key(python, modules, bundled))
        if hit is None and root:
            hit = _probe_cache.get(_cache_key(python, modules, bundled, "spec", root))
    return hit if hit is not None and _fresh(python, hit) else None


def evaluate(
    candidates: list[dict],
    needed: list[dict],
    unknown: list[str],
    *,
    use_cache: bool = True,
    cache_only: bool = False,
    root: str = "",
    project_name: str = "",
) -> list[dict]:
    """逐个体检候选，回同顺序的结果表。`use_cache=False` 真起一次、不读也不写缓存：采用前的
    复核要的是此刻的环境，不是弹窗打开那一刻（或并发的另一次）体检。

    `root` 给了（点「运行」之前）= 脚本要的模块不 import、只 `find_spec`，路径落在项目里的**延后**：该候选回「未检查」
    （`checked=False`），与项目说了算的候选同一套机器（Codex 安全 #820 r4234465621）。

    `cache_only=True`（ADR 0114 §三）：**一个候选也不起**——有缓存结论的照常给，没有的回一条 `checked=False` 的
    「未检查」条目（`ok` / `satisfies` 都是 None，不冒充装齐也不冒充没装齐）；起候选解释器是明确的检查动作的事。

    `needed` 是联合计划里缺的那些（`{"import_name", "distribution"}`），`unknown` 是映射不到
    distribution 的无条件 import。**判「装没装齐」看 import 得不得到**，不看包元数据：worker 跑脚本
    时认的就是 import（Conda 装的、`pip install --user` 的、`PYTHONPATH` 上的都算），这是同一个判据。
    """
    imports = tuple(
        dict.fromkeys([n["import_name"] for n in needed if n.get("import_name")] + list(unknown))
    )
    todo = _within_budget(candidates, project_name or (Path(root).name if root else ""))

    def one(cand: dict) -> dict:
        if cache_only:
            health = cached_probe(cand["python"], imports, root=root)
            if health is None:
                return {
                    **cand,
                    "ok": None,
                    "code": "",
                    "support": "",
                    "python_version": "",
                    "matplotlib_version": "",
                    "missing": [],
                    "satisfies": None,
                    "checked": False,
                    "probe_status": PROBE_UNCHECKED,
                }
        elif use_cache:
            health = (
                _probe(cand["python"], imports, mode="spec", root=root)
                if root
                else _probe(cand["python"], imports)
            )
        else:
            from . import projectenv

            # 不经缓存、也不回写：「先删缓存再走 `_probe`」不是原子的——删完放锁到 `_probe` 再读之间，
            # 并发的一次（更早开始的）体检可以把它的旧结论塞回去，复核于是收下一次过期的「装齐」
            # （Codex #562 P2）。回写同理会让在途的旧体检与这次新结论互相覆盖，所以这里只量、不记。
            health = projectenv.probe_environment(
                cand["python"],
                modules=imports,
                **({"import_mode": "spec", "project_root": root} if root else {}),
            )
        ok_map = health.get("modules_ok") or {}
        if (
            health.get("deferred_env")
            or health.get("ok")
            and any(v is None for v in ok_map.values())
            and not any(v is False for v in ok_map.values())
        ):
            # 有模块解析到了项目里（可编辑安装指向项目）：运行之前不 import，延后——不冒充装齐也不冒充没装齐
            return {
                **cand,
                "ok": None,
                "code": "",
                "support": "",
                "python_version": health.get("python_version", ""),
                "matplotlib_version": "",
                "missing": [],
                "satisfies": None,
                "checked": False,
                "probe_status": PROBE_UNCHECKED,
                "deferred": True,
            }
        if (
            root
            and health.get("health_deferred")
            and health.get("ok")
            and all(v is True for v in ok_map.values())
        ):
            # 运行之前只读了 dist-info（没 import matplotlib / worker 启动链）：元数据在不等于能跑（坏的包 / 二进制依赖）。
            # 不可采用——和延后的候选同一套机器，运行时用 import 方式真量一次，不合格再按排序落到下一个
            # （Codex #820 r4235263575）。已经确定缺包的（有 False）照旧回"缺"：那个结论不依赖健康检查
            return {
                **cand,
                "ok": None,
                "code": "",
                "support": "",
                "python_version": health.get("python_version", ""),
                "matplotlib_version": "",
                "missing": [],
                "satisfies": None,
                "checked": False,
                "probe_status": PROBE_UNCHECKED,
                "deferred": True,
                "health_deferred": True,
            }
        detail_map = health.get("modules_detail") or {}
        missing = sorted(
            {n["distribution"] for n in needed if ok_map.get(n.get("import_name")) is not True}
        )
        missing += sorted(u for u in unknown if ok_map.get(u) is not True)
        healthy = bool(health.get("ok"))
        import_failed = sorted(m for m, d in detail_map.items() if d == "import_error")
        if not healthy:
            status = PROBE_UNLAUNCHABLE
        elif any(ok_map.get(n) is not True and n not in import_failed for n in imports):
            status = PROBE_MODULE_MISSING
        elif import_failed:
            status = PROBE_IMPORT_FAILED
        else:
            status = PROBE_PASSED
        return {
            **cand,
            "ok": healthy,
            "code": health.get("code", ""),
            "support": health.get("support", ""),
            "python_version": health.get("python_version", ""),
            "matplotlib_version": health.get("matplotlib_version") or "",
            "missing": missing if healthy else [],
            # 在、但导入时抛了错（依赖缺 / ABI / DLL）：和「没装」分开——装它救不了（Import Origin PR3）
            "import_failed": import_failed if healthy else [],
            "satisfies": healthy and not missing,
            "checked": True,
            "probe_status": status,
        }

    if not todo:
        return []
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(todo))) as pool:
        return list(pool.map(one, todo))


def system_candidates(budget: scanbudget.Budget | None = None) -> list[dict]:
    """老链条里的系统解释器（`pool.system_python_candidates`，只 `is_file`，不起进程）的候选形状。
    执行路径（`deprepair`）与证据 / 建议（`projscan` / `envadvice`）共用这一个入口。

    `budget`（导入即扫描传）：同一份清单的只读扫描版——PATH 过预算与取消、UNC / 网络路径不碰。"""
    from . import pool  # noqa: PLC0415 — pool 重，且只有要系统候选时才需要

    found = (
        pool.system_python_candidates()
        if budget is None
        else pool.system_python_candidates(budget=budget)
    )
    return [{"python": py, "source": SOURCE_SYSTEM, "label": ""} for py, _src in found]


def _version_tuple(text: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in (text or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def source_tier(entry: dict, project_name: str = "") -> int:
    """候选的来源档位（小 = 更接近用户意图）：**只看静态可得的证据**（来源、与项目目录同名的 Conda 环境），
    不起任何进程。`rank()` 的第一把尺子，也是体检预算之前排序的唯一依据。"""
    tier = _SOURCE_RANK.get(entry.get("source", ""), 5)
    if (
        entry.get("source") == SOURCE_CONDA
        and project_name
        and (entry.get("label") or "").lower() == project_name.lower()
    ):
        tier = 2
    return tier


def static_order(candidates: list[dict], project_name: str = "") -> list[dict]:
    """按静态相关性稳定排序（同档保持原顺序）。体检有上限（`PROBE_LIMIT`）：排序必须发生在截断**之前**，
    否则第 13 个唯一的项目匹配者会被前 12 个无关候选饿死。"""
    return sorted(candidates, key=lambda c: source_tier(c, project_name))


def _within_budget(candidates: list[dict], project_name: str = "") -> list[dict]:
    """超过上限时，按静态相关性挑出入选的 `PROBE_LIMIT` 个，**回表仍是输入的相对顺序**。"""
    if len(candidates) <= PROBE_LIMIT:
        return list(candidates)
    ranked = sorted(
        range(len(candidates)), key=lambda i: (source_tier(candidates[i], project_name), i)
    )
    return [candidates[i] for i in sorted(ranked[:PROBE_LIMIT])]


def rank(entry: dict, project_name: str = "") -> tuple:
    """挑选的排序键（小的更好）：来源档位 → verified 优先 → Python 新的优先 → 原顺序由稳定排序保留。"""
    tier = source_tier(entry, project_name)
    verified = 0 if entry.get("support") == "verified" else 1
    ver = _version_tuple(entry.get("python_version", ""))
    return (tier, verified, tuple(-v for v in ver))


def best(entries: list[dict], project_name: str = "") -> dict | None:
    """装齐且健康的里面最好的那个；一个都没有回 None（该问用户装包了）。"""
    ok = [e for e in entries if e.get("satisfies")]
    if not ok:
        return None
    return sorted(ok, key=lambda e: rank(e, project_name))[0]


def env_id(python: str) -> str:
    """对外的不透明身份，实现见 `projectenv.env_id`（ADR 0053 §二：HTTP 投影不带机器路径）。"""
    return projectenv.env_id(python)


#: 投影里保留的字段（`python` 不在其中）
PUBLIC_FIELDS = (
    "source",
    "label",
    "ok",
    "code",
    "support",
    "python_version",
    "matplotlib_version",
    "missing",
    "satisfies",
    "checked",
    "import_failed",
    "probe_status",
)


def public(entry: dict) -> dict:
    return {"id": env_id(entry["python"]), **{k: entry.get(k) for k in PUBLIC_FIELDS}}


def _register_reset() -> None:
    from . import projectenv

    if reset_cache not in projectenv.RESET_HOOKS:
        projectenv.RESET_HOOKS.append(reset_cache)


_register_reset()
