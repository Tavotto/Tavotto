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
import os
import re
import subprocess
import threading
from pathlib import Path

from . import projectenv, runtime

LOG = logging.getLogger("tavotto.userenvs")

SOURCE_VSCODE = "vscode"
SOURCE_PYTHON_VERSION = "python_version_file"
SOURCE_ENVIRONMENT_YML = "environment_yml"
SOURCE_SHEBANG = "shebang"
SOURCE_LOGIN_SHELL = "login_shell"
SOURCE_CONDA = "conda"
SOURCE_PYENV = "pyenv"
#: 顺序即优先级（`discover()` 按它拼表）
SOURCES = (
    SOURCE_VSCODE,
    SOURCE_PYTHON_VERSION,
    SOURCE_ENVIRONMENT_YML,
    SOURCE_SHEBANG,
    SOURCE_LOGIN_SHELL,
    SOURCE_CONDA,
    SOURCE_PYENV,
)

#: 问登录 shell 最多等多久。交互式 shell 要读 rc 文件（Conda init、oh-my-zsh……），冷启动一两秒常见
LOGIN_SHELL_TIMEOUT_S = 8.0
_MARK = "__TAVOTTO_PY__"

_lock = threading.Lock()
_login_shell_cache: dict[str, list[str]] = {}


def reset_cache() -> None:
    """用户点「重新检查」= 推翻旧结论（与 `projectenv.reset_cache()` 一起清）。"""
    with _lock:
        _login_shell_cache.clear()
        _probe_cache.clear()


def _home() -> str:
    return os.path.expanduser("~")


def _is_python_file(path: str) -> bool:
    try:
        p = Path(path)
        return p.name.lower().startswith("python") and p.is_file()
    except (OSError, ValueError):
        return False


def _prefix_python(prefix: str) -> str | None:
    """环境前缀（Conda / pyenv / venv）→ 里面的解释器；没有回 None。"""
    names = ("python.exe",) if os.name == "nt" else ("bin/python3", "bin/python")
    for name in names:
        cand = os.path.join(prefix, name)
        if _is_python_file(cand):
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


def _vscode_pythons(dirs: list[Path]) -> list[str]:
    # settings.json 是 JSONC（注释、尾逗号）；只要这一个键，正则比「先把 JSONC 洗成 JSON」可靠
    out: list[str] = []
    for d in dirs:
        try:
            text = (d / ".vscode" / "settings.json").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in _VSCODE_KEY.findall(text):
            value = raw.replace("${workspaceFolder}", str(d)).replace("\\\\", "\\")
            value = os.path.expanduser(value)
            if not os.path.isabs(value):
                value = str(d / value)
            out.append(value)
    return out


def _pyenv_root() -> str:
    return os.environ.get("PYENV_ROOT") or os.path.join(_home(), ".pyenv")


def _pyenv_version_dirs() -> list[str]:
    root = _pyenv_root()
    if os.name == "nt":
        return [os.path.join(root, "pyenv-win", "versions")]
    return [os.path.join(root, "versions")]


def _python_version_pythons(dirs: list[Path]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for d in dirs:
        try:
            lines = (d / ".python-version").read_text(encoding="utf-8", errors="replace").split()
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


def _environment_yml_pythons(dirs: list[Path]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for d in dirs:
        for fname in ("environment.yml", "environment.yaml"):
            try:
                text = (d / fname).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = _ENV_NAME.search(text)
            if not m:
                continue
            name = m.group(1)
            for prefix in _conda_prefixes():
                if os.path.basename(prefix.rstrip("/\\")) == name:
                    py = _prefix_python(prefix)
                    if py:
                        out.append((py, name))
    return out


def _shebang_python(script_path: str | None) -> str | None:
    """`script_path` 同 `_hint_dirs`：只认 `contained_path()` 的输出。"""
    if not script_path:
        return None
    try:
        with open(script_path, "rb") as fh:
            first = fh.readline(512)
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


def login_shell_pythons() -> list[str]:
    shell = _login_shell()
    if not shell:
        return []
    with _lock:
        hit = _login_shell_cache.get(shell)
    if hit is not None:
        return list(hit)
    found = _ask_login_shell(shell)
    with _lock:
        _login_shell_cache[shell] = found
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


def _conda_prefixes() -> list[str]:
    """Conda 知道的每一个环境前缀（base 与具名环境），去重、保序。"""
    out: list[str] = []
    try:
        text = Path(_home(), ".conda", "environments.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        out += [ln.strip() for ln in text.splitlines() if ln.strip()]
    except OSError:
        pass
    for root in _conda_roots():
        if os.path.isdir(root):
            out.append(root)
            out += sorted(glob.glob(os.path.join(root, "envs", "*")))
    out += sorted(glob.glob(os.path.join(_home(), ".conda", "envs", "*")))
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


def _pyenv_pythons() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for vdir in _pyenv_version_dirs():
        for prefix in sorted(glob.glob(os.path.join(vdir, "*")), reverse=True):
            py = _prefix_python(prefix)
            if py:
                out.append((py, os.path.basename(prefix)))
    return out


# ------------------------------------------------------------------ 合起来


def _key(python: str) -> tuple[str, str]:
    """去重键 = (所在目录, 真实文件)：同一目录下 `python3` / `python` 两个别名是同一个环境；
    **目录不 realpath**——`.venv/bin/python` 指向基础解释器，但它与基础解释器是两个环境
    （site-packages 不同），目录不同就分得开（ADR 0044 §四）。"""
    path = os.path.abspath(python)
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError):
        real = path
    return os.path.normcase(os.path.dirname(path)), os.path.normcase(real)


def discover(figures_dir: str | Path, script: str | None = None) -> list[dict]:
    """按优先级排好的候选表（去重、只含存在的文件）。不起任何 Python；可能问一次登录 shell。"""
    # `script` 可能来自请求体：先钉在项目内，下游一律用净化器回的那一条；越界就当没给脚本
    root_real = os.path.realpath(os.fspath(figures_dir))
    script_path = projectenv.contained_path(root_real, script) if script else None
    dirs = _hint_dirs(root_real, script_path)
    raw: list[tuple[str, str, str]] = []
    raw += [(p, SOURCE_VSCODE, "") for p in _vscode_pythons(dirs)]
    raw += [(p, SOURCE_PYTHON_VERSION, n) for p, n in _python_version_pythons(dirs)]
    raw += [(p, SOURCE_ENVIRONMENT_YML, n) for p, n in _environment_yml_pythons(dirs)]
    shebang = _shebang_python(script_path)
    if shebang:
        raw.append((shebang, SOURCE_SHEBANG, ""))
    raw += [(p, SOURCE_LOGIN_SHELL, "") for p in login_shell_pythons()]
    raw += [
        (p, SOURCE_CONDA, _conda_label(pre))
        for pre in _conda_prefixes()
        for p in [_prefix_python(pre)]
        if p
    ]
    raw += [(p, SOURCE_PYENV, n) for p, n in _pyenv_pythons()]
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for python, source, label in raw:
        if not _is_python_file(python):
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

_probe_cache: dict[tuple[tuple[str, str], tuple[str, ...], bool], dict] = {}


def _probe(python: str, modules: tuple[str, ...], *, bundled: bool = False) -> dict:
    from . import projectenv

    key = (_key(python), modules, bundled)
    with _lock:
        hit = _probe_cache.get(key)
    if hit is not None:
        return hit
    # 只在内置 runtime 时才带这个参数：用户环境的体检调用形状与以前逐字相同
    extra = {"bundled": True} if bundled else {}
    health = projectenv.probe_environment(python, modules=modules, **extra)
    with _lock:
        _probe_cache[key] = health
    return health


def imports_missing(python: str, modules: list[str], *, bundled: bool = False) -> list[str]:
    """`modules` 里此刻这个解释器**确实** import 不到的那几个（ADR 0079 修订 2026-09-25）。

    与 `evaluate()` 同一条体检（同一个缓存）。判不出的不算缺：体检起不来、结果里没有这一项
    （`modules_ok` 只有真 import 过的才有 True / False）——拿「没量到」去触发发现，就是在一个
    根本没问过的解释器上替用户换环境。`bundled`：它是内置 runtime，按 worker 的环境与参数量
    （`projectenv.probe_environment(bundled=True)`）。"""
    mods = tuple(dict.fromkeys(m for m in modules if m))
    if not mods:
        return []
    ok_map = _probe(python, mods, bundled=bundled).get("modules_ok") or {}
    return [m for m in mods if ok_map.get(m) is False]


def evaluate(
    candidates: list[dict], needed: list[dict], unknown: list[str], *, use_cache: bool = True
) -> list[dict]:
    """逐个体检候选，回同顺序的结果表。`use_cache=False` 真起一次、不读也不写缓存：采用前的
    复核要的是此刻的环境，不是弹窗打开那一刻（或并发的另一次）体检。

    `needed` 是联合计划里缺的那些（`{"import_name", "distribution"}`），`unknown` 是映射不到
    distribution 的无条件 import。**判「装没装齐」看 import 得不得到**，不看包元数据：worker 跑脚本
    时认的就是 import（Conda 装的、`pip install --user` 的、`PYTHONPATH` 上的都算），这是同一个判据。
    """
    imports = tuple(
        dict.fromkeys([n["import_name"] for n in needed if n.get("import_name")] + list(unknown))
    )
    todo = candidates[:PROBE_LIMIT]

    def one(cand: dict) -> dict:
        if use_cache:
            health = _probe(cand["python"], imports)
        else:
            from . import projectenv

            # 不经缓存、也不回写：「先删缓存再走 `_probe`」不是原子的——删完放锁到 `_probe` 再读之间，
            # 并发的一次（更早开始的）体检可以把它的旧结论塞回去，复核于是收下一次过期的「装齐」
            # （Codex #562 P2）。回写同理会让在途的旧体检与这次新结论互相覆盖，所以这里只量、不记。
            health = projectenv.probe_environment(cand["python"], modules=imports)
        ok_map = health.get("modules_ok") or {}
        missing = sorted(
            {n["distribution"] for n in needed if ok_map.get(n.get("import_name")) is not True}
        )
        missing += sorted(u for u in unknown if ok_map.get(u) is not True)
        healthy = bool(health.get("ok"))
        return {
            **cand,
            "ok": healthy,
            "code": health.get("code", ""),
            "support": health.get("support", ""),
            "python_version": health.get("python_version", ""),
            "matplotlib_version": health.get("matplotlib_version") or "",
            "missing": missing if healthy else [],
            "satisfies": healthy and not missing,
        }

    if not todo:
        return []
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(todo))) as pool:
        return list(pool.map(one, todo))


def _version_tuple(text: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in (text or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def rank(entry: dict, project_name: str = "") -> tuple:
    """挑选的排序键（小的更好）：来源档位 → verified 优先 → Python 新的优先 → 原顺序由稳定排序保留。"""
    tier = _SOURCE_RANK.get(entry.get("source", ""), 5)
    if (
        entry.get("source") == SOURCE_CONDA
        and project_name
        and (entry.get("label") or "").lower() == project_name.lower()
    ):
        tier = 2
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
    """对外的不透明身份：HTTP 投影不带机器路径（ADR 0053 §二），界面采用时交回这个 id，后端用自己的
    发现结果换回路径——路径只来自本模块的枚举，不接受调用方给（ADR 0044 的安全口径）。"""
    import hashlib

    d, real = _key(python)
    return hashlib.sha1(f"{d}|{real}".encode()).hexdigest()[:16]


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
)


def public(entry: dict) -> dict:
    return {"id": env_id(entry["python"]), **{k: entry.get(k) for k in PUBLIC_FIELDS}}


def _register_reset() -> None:
    from . import projectenv

    if reset_cache not in projectenv.RESET_HOOKS:
        projectenv.RESET_HOOKS.append(reset_cache)


_register_reset()
