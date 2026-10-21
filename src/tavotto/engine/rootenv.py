"""CERN ROOT 的安装发现与「叠加环境」（ADR 0079 修订 2026-10-21 b）。

脚本 `import ROOT` 时，ROOT 往往不在用户的 Python 里：Windows 上 conda-forge 没有 `root` 的 win-64 包，
ROOT 来自官方二进制安装包，**每个包只配一个 Python 小版本**；macOS / Linux 上多来自 Homebrew / 自编译，
靠终端里 `source thisroot.sh` 才进得了 `PYTHONPATH` / 库路径。从开始菜单 / Finder 启动的 Tavotto 看不到
用户只在终端里做过的那一步。

这里的做法与官方 `thisroot.bat` / `thisroot.sh` 同构：找到安装根，把它们做的几件事（`ROOTSYS`、`PATH`、
库路径、`PYTHONPATH`、`CMAKE_PREFIX_PATH`、`ROOT_INCLUDE_PATH`）作为**叠加层**交给候选解释器去试。

* 发现是纯 stat（外加读 `RVersion.h` 取版本、读注册表卸载项）；**不执行任何东西**，包括 thisroot 脚本。
* 叠加层持久化时只存 `{"kind": "root", "root": <安装根>, "version": …}`，环境变量每次起进程时现算，
  并且先重新核对一遍安装根仍像 ROOT（被卸载了就不叠加，不把过期路径塞进 `PATH`）。
* 本模块是叶子：不 import 其它引擎模块（`userenvs` / `projectenv` / `pool` 都依赖它）。
"""

from __future__ import annotations

import glob
import ntpath
import os
import re
import sys

from . import scanbudget

KIND = "root"
#: 候选来源标签（`userenvs.SOURCE_ROOT_OVERLAY` 同值）
SOURCE = "root_overlay"
#: 发现到的安装根最多留多少个
MAX_INSTALLS = 8
#: 一轮探测最多拿几个 ROOT 去和候选解释器组合（优先最新）
MAX_USED = 2
#: 一轮探测最多跑多少个「解释器 × ROOT」组合（每个是一次子进程）
MAX_COMBOS = 8
#: 脚本 import 名单里出现它才考虑叠加
IMPORT_NAME = "ROOT"

_REGISTRY_UNINSTALL = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"
_REGISTRY_MAX_KEYS = 512
#: 卸载项的 DisplayName：以 `ROOT` 开头，后面是空白 / 分隔符 / 版本号 / 结尾（不吃 `Rootkit` 之类）
_DISPLAY_NAME = re.compile(r"^ROOT(?:$|[\s_\-]|v?\d)")
_GLOB_MAX = 16
_SCAN_MAX = 400

#: Windows 上固定位置的模式（测试 monkeypatch）
_WINDOWS_FIXED = ("C:\\root", "C:\\root_v*")


def _is_nt(nt: bool | None) -> bool:
    return os.name == "nt" if nt is None else nt


def thisroot_name(nt: bool | None = None) -> str:
    return "thisroot.bat" if _is_nt(nt) else "thisroot.sh"


def _isfile(path: str) -> bool:
    try:
        return os.path.isfile(path)
    except OSError:
        return False


def _isdir(path: str) -> bool:
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def _python_dir(root: str, nt: bool) -> str | None:
    """含 PyROOT 包（`ROOT/__init__.py`）或 `ROOT.py` 的那个目录——叠加时它进 `PYTHONPATH`。

    官方 Windows 包的 thisroot.bat 把 `%ROOTSYS%\\bin` 放进 PYTHONPATH；POSIX 的包在 `lib`（Homebrew 在 `lib/root`）。
    这里不写死，按顺序找真有标志的那一个，找不到就不是可用的 PyROOT 安装。"""
    subs = [("bin",), ("lib",), ("lib", "root")] if nt else [("lib",), ("lib", "root"), ("bin",)]
    for sub in subs:
        d = os.path.join(root, *sub)
        if _isfile(os.path.join(d, "ROOT", "__init__.py")) or _isfile(os.path.join(d, "ROOT.py")):
            return d
    return None


_RELEASE = re.compile(r'ROOT_RELEASE\s+"([^"]+)"')
_NAME_VERSION = re.compile(r"(\d+\.\d+(?:[./]\d+)?)")


def _version_of(root: str) -> str:
    """`6.38.02`（新式）/ `6.26.10`（老式 `6.26/10` 折成点号）；读 `RVersion.h`，读不到就看目录名，再没有回空。"""
    for sub in (("include", "RVersion.h"), ("include", "root", "RVersion.h")):
        try:
            text = scanbudget.read_regular_text(root, *sub, max_bytes=256 * 1024)
        except (OSError, ValueError):
            continue
        m = _RELEASE.search(text)
        if m:
            return m.group(1).replace("/", ".")
    m = _NAME_VERSION.search(os.path.basename(root.rstrip("\\/")))
    if m:
        return m.group(1).replace("/", ".")
    # Homebrew：…/Cellar/root/6.38.02_1/ 的版本在目录名里（上面已处理）；opt 链接则无，留空
    return ""


def short_version(version: str) -> str:
    """`6.38.02` → `6.38`（界面标签用）。"""
    parts = (version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (version or "")


def version_tuple(version: str) -> tuple[int, ...]:
    out = []
    for part in (version or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


#: PyROOT 的 cppyy 库文件名里带着它所配的 Python 小版本（如 `libcppyy3_11.so`）。**只当排序提示**：
#: 命名规则没有拿真实 Windows 包逐文件核对过，认不出就回空集合，绝不据此排除组合——最终以真探测为准
_PY_TAG = re.compile(r"(?:cppyy|Pythonizations)(?:_backend)?3[_.](\d{1,2})")


def _python_tags(*dirs: str) -> frozenset[int]:
    tags: set[int] = set()
    for d in dirs:
        try:
            with os.scandir(d) as it:
                for i, entry in enumerate(it):
                    if i >= _SCAN_MAX:
                        break
                    m = _PY_TAG.search(entry.name)
                    if m:
                        tags.add(int(m.group(1)))
        except OSError:
            continue
    return frozenset(tags)


def inspect_root(root: str, *, nt: bool | None = None) -> dict | None:
    """这个目录是不是一个**能给 Python 用**的 ROOT 安装：有 `bin/thisroot.(bat|sh)`，并且有 PyROOT 标志
    （见 `_python_dir`）。是就回 `{"root", "version", "python_dir", "lib_dirs", "py_tags"}`，否则 None。纯 stat。"""
    nt = _is_nt(nt)
    root = os.path.normpath(root)
    if not root or not _isdir(root):
        return None
    if not _isfile(os.path.join(root, "bin", thisroot_name(nt))):
        return None
    pydir = _python_dir(root, nt)
    if pydir is None:
        return None
    libs = [d for d in (os.path.join(root, "lib"), os.path.join(root, "lib", "root")) if _isdir(d)]
    return {
        "root": root,
        "version": _version_of(root),
        "python_dir": pydir,
        "lib_dirs": libs,
        "py_tags": _python_tags(pydir, *libs),
    }


def _globbed(pattern: str) -> list[str]:
    try:
        return sorted(glob.glob(pattern))[:_GLOB_MAX]
    except OSError:
        return []


def _registry_roots(reader, budget: scanbudget.Budget | None) -> list[str]:
    """卸载项里 `DisplayName` 以 `ROOT` 开头的安装位置（`InstallLocation`，没有就取 `UninstallString` 所在目录）。
    读取器接口同 `userenvs._WinregReader`（`subkeys` / `values`）。只读注册表，条数有界。"""
    out: list[str] = []
    for hive in ("HKCU", "HKLM"):
        for view in (64, 32):
            for name in reader.subkeys(hive, view, _REGISTRY_UNINSTALL)[:_REGISTRY_MAX_KEYS]:
                if budget is not None and not budget.charge_entry():
                    return out
                vals = reader.values(hive, view, f"{_REGISTRY_UNINSTALL}\\{name}")
                if not _DISPLAY_NAME.match((vals.get("DisplayName") or "").strip()):
                    continue
                loc = (vals.get("InstallLocation") or "").strip().strip('"')
                if not loc:
                    unins = (vals.get("UninstallString") or "").strip()
                    exe = (
                        unins.split('"')[1] if unins.startswith('"') and '"' in unins[1:] else unins
                    )
                    loc = ntpath.dirname(exe.strip('"'))
                loc = os.path.expandvars(loc)
                if loc and (ntpath.isabs(loc) or os.path.isabs(loc)):
                    out.append(loc)
    return out


def discover_installs(
    environ: dict | None = None,
    *,
    nt: bool | None = None,
    registry_reader=None,
    budget: scanbudget.Budget | None = None,
    home: str | None = None,
    limit: int = MAX_INSTALLS,
) -> list[dict]:
    """发现本机的 ROOT 安装，按来源优先级（`ROOTSYS` → 注册表卸载项 → 常见位置）、去重（真实路径）、至多 `limit` 个。
    每个是 `inspect_root` 的结果加 `source`。不执行任何东西。`registry_reader` 只在 Windows 上用（测试注入）。"""
    nt = _is_nt(nt)
    env = os.environ if environ is None else environ
    home = os.path.expanduser("~") if home is None else home
    raw: list[tuple[str, str]] = []
    if env.get("ROOTSYS"):
        raw.append((env["ROOTSYS"], "rootsys"))
    if nt:
        if registry_reader is not None:
            raw += [(p, "registry") for p in _registry_roots(registry_reader, budget)]
        patterns = list(_WINDOWS_FIXED)
        for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            if env.get(var):
                patterns.append(os.path.join(env[var], "ROOT*"))
        if env.get("LOCALAPPDATA"):
            patterns.append(os.path.join(env["LOCALAPPDATA"], "root*"))
    else:
        patterns = [
            "/opt/root*",
            "/usr/local/root*",
            "/opt/homebrew/Cellar/root/*",
            "/usr/local/Cellar/root/*",
            "/home/linuxbrew/.linuxbrew/Cellar/root/*",
            os.path.join(home, "root"),
            os.path.join(home, "root_v*"),
        ]
    for pattern in patterns:
        for hit in _globbed(pattern):
            raw.append((hit, "common"))
    out: list[dict] = []
    seen: set[str] = set()
    for path, source in raw:
        if budget is not None and not budget.charge_entry():
            break
        try:
            key = os.path.normcase(os.path.realpath(path))
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        info = inspect_root(path, nt=nt)
        if info is None:
            continue
        out.append({**info, "source": source})
        if len(out) >= limit:
            break
    return out


def newest(installs: list[dict], n: int = MAX_USED) -> list[dict]:
    """版本新的在前（并列保持发现顺序），取前 `n` 个。"""
    return sorted(installs, key=lambda i: tuple(-v for v in version_tuple(i.get("version", ""))))[
        :n
    ]


# ------------------------------------------------------------------ 叠加层


def overlay_spec(install: dict) -> dict:
    """持久化 / 缓存键用的叠加层描述：只有安装根与版本，环境变量起进程时现算。"""
    return {"kind": KIND, "root": install["root"], "version": install.get("version", "")}


def valid_spec(spec: object) -> dict | None:
    """从项目记录等外部来源读回来的东西：形状不对回 None；对的话规整成只含三个字段的新字典。"""
    if not isinstance(spec, dict) or spec.get("kind") != KIND:
        return None
    root = spec.get("root")
    if not isinstance(root, str) or not root or not os.path.isabs(root) and not ntpath.isabs(root):
        return None
    version = spec.get("version")
    return {"kind": KIND, "root": root, "version": version if isinstance(version, str) else ""}


def key_of(spec: dict | None) -> str:
    """缓存键 / 身份用：空串 = 没有叠加。"""
    if not spec:
        return ""
    return f"{KIND}:{os.path.normcase(os.path.normpath(spec['root']))}"


def label_of(spec: dict) -> str:
    v = short_version(spec.get("version", ""))
    return f"ROOT {v}".strip()


def wants_root(import_names) -> bool:
    return IMPORT_NAME in set(import_names)


def _prepend(env: dict, name: str, value: str, sep: str) -> None:
    """把 `value` 放到 `env[name]`（分隔符 `sep` 的列表）最前；已有的同一项挪到最前而不是重复（幂等）。"""
    key = os.path.normcase(value)
    rest = [p for p in (env.get(name) or "").split(sep) if p and os.path.normcase(p) != key]
    env[name] = sep.join([value, *rest])


def apply_overlay(
    env: dict | None,
    spec: dict | None,
    *,
    nt: bool | None = None,
    replace_pythonpath: bool = False,
    platform: str | None = None,
) -> dict:
    """把叠加层叠到 `env`（None = 当前进程环境的拷贝）上，回**新字典**。做的就是 thisroot 做的那几件事：
    `ROOTSYS`；`PATH` 前置 `<root>/bin`；POSIX 的库路径前置 `<root>/lib`；`PYTHONPATH` 前置 PyROOT 所在目录；
    `CMAKE_PREFIX_PATH`；`ROOT_INCLUDE_PATH`。

    `replace_pythonpath`：内置 runtime 会摘掉 shell 带来的 `PYTHONPATH`（`runtime.child_env`），叠加在它**之后**，
    于是 `PYTHONPATH` 只有 ROOT 这一项而不是重新吃回用户 shell 里的那串。

    安装根此刻不再像 ROOT（被卸载 / 被挪走）就原样返回拷贝：不把过期路径塞进环境。"""
    out = dict(os.environ if env is None else env)
    valid = valid_spec(spec)
    if valid is None:
        return out
    nt = _is_nt(nt)
    info = inspect_root(valid["root"], nt=nt)
    if info is None:
        return out
    sep = ";" if nt else ":"
    root = info["root"]
    out["ROOTSYS"] = root
    _prepend(out, "PATH", os.path.join(root, "bin"), sep)
    if not nt:
        lib_var = (
            "DYLD_LIBRARY_PATH" if (platform or sys.platform) == "darwin" else "LD_LIBRARY_PATH"
        )
        for lib in reversed(info["lib_dirs"]):
            _prepend(out, lib_var, lib, sep)
    if replace_pythonpath:
        out["PYTHONPATH"] = info["python_dir"]
    else:
        _prepend(out, "PYTHONPATH", info["python_dir"], sep)
    _prepend(out, "CMAKE_PREFIX_PATH", root, sep)
    include = os.path.join(root, "include")
    if _isdir(include):
        _prepend(out, "ROOT_INCLUDE_PATH", include, sep)
    return out


def changed_keys(before: dict, after: dict) -> dict:
    """`after` 相对 `before` 新增 / 改写的键（workerd 的 spawn 规格只收增量）。"""
    return {k: v for k, v in after.items() if before.get(k) != v}
