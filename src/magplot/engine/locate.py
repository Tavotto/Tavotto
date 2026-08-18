"""Magplot 装在这台机器上的哪儿：安装清单 `install.json` 与已知安装位置。

**为什么需要这个模块。** 桌面版用户装的是一个 GUI 程序：Windows 上
`%LOCALAPPDATA%\\Magplot\\Magplot.exe` 是 Tauri 壳（window 子系统，
没有 stdout），它旁边的 `sidecar\\Magplot\\Magplot.exe` 是 PyInstaller 打的
Flask 后端，同样是 `console=False`。这两个都**不能当命令行用**——
`packaging/entry.py` 在 `sys.stdout is None` 时会把输出改道到 app.log，
调用方拿到的是空的 stdout，不是那行 JSON。

于是桌面安装包里多带一个 **`magplot-cli`（console 子系统，与 sidecar 共用同一份
`_internal/`，只多一个 ~1.5 MB 的 bootloader）**：它就是外部程序（Codex 插件、
编辑器、别的 Agent）能稳定调用的那条 CLI，支持 `magplot open … --json`
与 `magplot doctor --json` 的完整协议。

**发现它的两条腿，缺一条也能走：**

1. **安装清单**（本模块的 `install.json`）——安装器装完就地写一份，应用每次
   启动再刷新一次。它记录绝对路径，所以装到哪儿、装了什么版本、有没有 CLI
   都不用猜。落点是**用户配置目录**（`engine/config.config_dir()`），不是安装
   目录：Windows 上安装目录可能在 Program Files（只读），卸载后也会被删掉。
2. **已知安装位置**（`install_roots()`）——清单丢了、被策略删了、用户从 zip
   解压出来的，照样能按平台惯例找到。Windows 上再加一条 HKCU 的
   `InstallLocation`（**只读、只当前用户、只作为补充**，不是唯一依据：企业
   策略能锁注册表，那时前两条仍然管用）。

**平台分支的路径拼接全程 os.path 字符串**（同 `engine/runtime.py` /
`engine/handoff.py`）：`Path()` 按 `os.name` 分派，在 macOS 上连构造一个
Windows 路径都做不到，那样 Windows 的安装布局就只能到 Windows 上才测得了。
纯标准库，Flask 父进程可安全 import。

清单的形状（`protocol` 变了就是不兼容改动，读的一方按它决定认不认）：

    {"protocol": 1, "product": "Magplot", "version": "0.7.0",
     "cli": "…/sidecar/Magplot/magplot-cli.exe",     # 可为 null
     "desktop": "…/Magplot.exe",                     # 可为 null
     "install_dir": "…", "source": "installer|app|module", "updated": "…"}

同一份契约的消费方还有 **Codex 插件**
（`codex-plugin/skills/magplot-figure/scripts/handoff.py`，它不能 import
magplot，只能照着读）。两侧的常量与候选路径由
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 逐条比对，
改一边必须同步另一边。
"""
from __future__ import annotations

import json
import os
import sys
import time

#: 交接协议版本。CLI 的命令行形状 / JSON 字段语义变了才 +1。
PROTOCOL_VERSION = 1
#: 清单文件名（在 config_dir() 下）
MANIFEST_NAME = "install.json"
#: 随桌面版一起装的 console 版 CLI
CLI_NAME = "magplot-cli.exe" if os.name == "nt" else "magplot-cli"
#: Tauri 壳自己的可执行文件名（GUI，不可当 CLI 用）
DESKTOP_NAME = "Magplot.exe" if os.name == "nt" else "Magplot"
#: sidecar 在资源目录下的相对位置。**与 src-tauri/tauri.conf.json 的
#: bundle.resources（"../dist/Magplot": "sidecar/Magplot"）以及
#: src-tauri/src/sidecar.rs 的 resolve_command 同源**，改一处要同步三处。
SIDECAR_REL = ("sidecar", "Magplot")
#: 环境变量覆盖（与 MAGPLOT_DESKTOP_APP 同款惯例）：指到 CLI 可执行文件
CLI_ENV = "MAGPLOT_CLI"
#: HKCU 卸载信息键（NSIS 模板写的就是它），只读、只当补充来源
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Magplot"


def _sys(system: str | None) -> str:
    return sys.platform if system is None else system


def _env(environ: dict | None) -> dict:
    return os.environ if environ is None else environ


def _is_win(system: str) -> bool:
    return system.startswith("win")


def _exe_name(base: str, system: str) -> str:
    return base + ".exe" if _is_win(system) else base


def _join(system: str, *parts: str) -> str:
    sep = "\\" if _is_win(system) else "/"
    head = parts[0].rstrip("/\\") if parts else ""
    return sep.join([head, *parts[1:]])


def _split(path: str, system: str) -> tuple[str, str]:
    r"""(目录, 文件名)。**不能用 os.path.dirname**——它按本平台分派：在 macOS 上
    切一条 `C:\...\Magplot.exe` 会原样返回（里面一个 `/` 都没有），于是
    Windows 的安装布局只能到 Windows 上才测得了，而那正是这个模块要避免的。
    Windows 侧两种分隔符都认：PyInstaller 给回来的路径混用过。
    """
    seps = ("\\", "/") if _is_win(system) else ("/",)
    idx = max(path.rfind(sep) for sep in seps)
    if idx < 0:
        return "", path
    return (path[:idx] or seps[0]), path[idx + 1:]


def _dirname(path: str, system: str) -> str:
    return _split(path, system)[0]


# ------------------------------ 已知安装位置 -----------------------------
def install_roots(*, system: str | None = None, environ: dict | None = None,
                  extra: tuple[str, ...] = ()) -> list[str]:
    """安装根目录候选（按优先级）。

    Windows 上是 NSIS 的 `$INSTDIR`；macOS 上是 `.app` 包本身。`extra` 给
    「从别处问出来的位置」（HKCU 的 InstallLocation），排在惯例位置之后
    ——注册表可能记着一个已经被手工删掉的老安装。
    """
    system = _sys(system)
    env = _env(environ)
    out: list[str] = []
    if system == "darwin":
        out.append("/Applications/Magplot.app")
        home = (env.get("HOME") or "").rstrip("/")
        if home:
            out.append(home + "/Applications/Magplot.app")
    elif _is_win(system):
        # 新装固定 %LOCALAPPDATA%\Magplot（installer.nsi 的 .onInit）；
        # 后两条是历史上管理员装出来的位置，升级时 $INSTDIR 会沿用它们。
        for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = (env.get(key) or "").rstrip("\\")
            if base:
                out.append(base + "\\Magplot")
    # Linux 没有桌面发行形态（desktop-tauri.yml 只发 macOS/Windows）
    for path in extra:
        cleaned = (path or "").strip().strip('"').rstrip("/\\")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def desktop_exe_for(root: str, *, system: str | None = None) -> str:
    """安装根 → 桌面 App 可执行文件（GUI，只能用 argv 交接，不能当 CLI）。"""
    system = _sys(system)
    if system == "darwin":
        return _join(system, root, "Contents", "MacOS", "Magplot")
    return _join(system, root, _exe_name("Magplot", system))


def resource_dir_for(root: str, *, system: str | None = None) -> str:
    """安装根 → Tauri 的资源目录（sidecar 与 CLI 都在它下面）。"""
    system = _sys(system)
    if system == "darwin":
        return _join(system, root, "Contents", "Resources")
    return root


def cli_exe_for(root: str, *, system: str | None = None) -> str:
    """安装根 → 随桌面版一起装的 console 版 CLI。"""
    system = _sys(system)
    return _join(system, resource_dir_for(root, system=system),
                 *SIDECAR_REL, _exe_name("magplot-cli", system))


def hkcu_install_dirs() -> list[str]:
    """HKCU 里记着的安装位置（Windows only）。

    **只读、只当前用户、只作为补充**：读不到（非 Windows、键不存在、被组策略
    挡住）一律回空表，调用方还有清单与惯例位置两条腿。
    """
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:                                  # pragma: no cover
        return []
    out: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return []
    if isinstance(value, str) and value.strip():
        out.append(value.strip().strip('"'))
    return out


# -------------------------------- 安装清单 -------------------------------
def manifest_dir(*, system: str | None = None, environ: dict | None = None) -> str:
    """清单所在目录 = 用户配置目录。

    这里**复刻**了 `config.config_dir()` 的规则而不是直接调它，只为一件事：
    在 macOS 上也能单测 Windows 的落点（`Path()` 做不到）。两者不许漂移，
    `tests/test_install_locate.py::test_manifest_dir_matches_config_dir` 看护。
    """
    system = _sys(system)
    env = _env(environ)
    override = env.get("MAGPLOT_CONFIG_DIR")
    if override:
        return override
    home = (env.get("HOME") or "").rstrip("/")
    if system == "darwin":
        return _join(system, home or "~", "Library", "Application Support", "Magplot")
    if _is_win(system):
        base = (env.get("APPDATA") or "").rstrip("\\")
        if not base:
            base = (env.get("USERPROFILE") or "").rstrip("\\")
        return _join(system, base or "%APPDATA%", "Magplot")
    base = (env.get("XDG_CONFIG_HOME") or "").rstrip("/")
    if not base:
        base = _join(system, home or "~", ".config")
    return _join(system, base, "magplot")


def manifest_path(*, system: str | None = None, environ: dict | None = None) -> str:
    return _join(_sys(system),
                 manifest_dir(system=system, environ=environ), MANIFEST_NAME)


def _validate(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("protocol") != PROTOCOL_VERSION:
        return None                                  # 另一代约定：当没有
    out = {"protocol": PROTOCOL_VERSION,
           "product": data.get("product") or "Magplot",
           "version": data.get("version"),
           "cli": None, "desktop": None, "install_dir": None,
           "source": data.get("source"), "updated": data.get("updated")}
    for key in ("cli", "desktop", "install_dir"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value
    return out


def read_manifest(*, system: str | None = None, environ: dict | None = None,
                  path: str | None = None, isfile=os.path.isfile) -> dict | None:
    """读清单并**核实里面的路径还在**。

    清单是缓存不是真相：卸载、手工删目录、从备份还原用户配置，都会留下一份
    指向不存在文件的清单。核实过的 `cli` / `desktop` 才留下——否则调用方会
    拿着一条早就没了的路径去 spawn，报出来的错是「执行不了」而不是「没装」。
    """
    target = path or manifest_path(system=system, environ=environ)
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    info = _validate(data)
    if info is None:
        return None
    info["path"] = target
    info["stale"] = False
    for key in ("cli", "desktop"):
        if info[key] and not isfile(info[key]):
            info[key] = None
            info["stale"] = True
    return info


def write_manifest(info: dict, *, system: str | None = None,
                   environ: dict | None = None, path: str | None = None) -> str:
    """原子写清单（先临时文件再 os.replace）。返回写到哪儿。"""
    target = path or manifest_path(system=system, environ=environ)
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    payload = {"protocol": PROTOCOL_VERSION, "product": "Magplot",
               "version": info.get("version"),
               "cli": info.get("cli"), "desktop": info.get("desktop"),
               "install_dir": info.get("install_dir"),
               "source": info.get("source"),
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, target)
    return target


def remove_manifest(*, system: str | None = None, environ: dict | None = None,
                    path: str | None = None) -> bool:
    """卸载时清掉清单。删不掉不是错误（本来就没有 / 目录只读）。"""
    target = path or manifest_path(system=system, environ=environ)
    try:
        os.remove(target)
        return True
    except OSError:
        return False


# --------------------------- 我自己是谁、装在哪 ---------------------------
def _scripts_dir(prefix: str, system: str) -> str:
    return _join(system, prefix, "Scripts" if _is_win(system) else "bin")


def describe_self(*, executable: str | None = None, frozen: bool | None = None,
                  prefix: str | None = None, system: str | None = None,
                  environ: dict | None = None, isfile=os.path.isfile,
                  version: str | None = None) -> dict:
    """当前这个进程所属的这套 Magplot：CLI 在哪、桌面壳在哪、装在哪。

    冻结（PyInstaller）时 `sys.executable` 就是 sidecar 或 CLI 自己，一切从它
    的所在目录推出来——**这样安装到哪个盘、哪个中文目录都不用猜**。源码/pip
    模式下 CLI 是解释器同级的 console script，桌面壳（如果另外装了）按惯例位置找。
    """
    system = _sys(system)
    env = _env(environ)
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    executable = sys.executable if executable is None else executable
    prefix = sys.prefix if prefix is None else prefix
    if version is None:
        from .. import __version__ as version                    # 唯一版本出处

    out = {"version": version, "cli": None, "desktop": None,
           "install_dir": None, "source": "module", "frozen": bool(frozen)}
    exe_dir, exe_name = _split(executable, system)

    if frozen:
        out["source"] = "app"
        cli = _join(system, exe_dir, _exe_name("magplot-cli", system))
        if exe_name == _exe_name("magplot-cli", system):
            out["cli"] = executable
        elif isfile(cli):
            out["cli"] = cli
        # sidecar 目录 → 资源目录 → 安装根。退的层数是 tauri.conf.json 那份
        # bundle.resources 映射（"sidecar/Magplot"）的镜像。
        resources = exe_dir
        for _ in range(len(SIDECAR_REL)):
            resources = _dirname(resources, system)
        # Windows：资源目录就是 $INSTDIR。macOS：Contents/Resources，再退两层
        # （Contents → .app）才是安装根。两条都试，认「壳真的在那儿」的那条。
        for root in (resources,
                     _dirname(_dirname(resources, system), system)):
            if not root:
                continue
            desktop = desktop_exe_for(root, system=system)
            if isfile(desktop):
                out["desktop"] = desktop
                out["install_dir"] = root
                break
        if out["install_dir"] is None:
            out["install_dir"] = exe_dir             # 开发态 dist/Magplot：没有壳
    else:
        cli = _join(system, _scripts_dir(prefix, system),
                    _exe_name("magplot", system))
        if isfile(cli):
            out["cli"] = cli
        root = find_install_root(system=system, environ=environ, isfile=isfile)
        if root:
            out["desktop"] = desktop_exe_for(root, system=system)
            out["install_dir"] = root
    return out


def find_install_root(*, system: str | None = None, environ: dict | None = None,
                      isfile=os.path.isfile, extra: tuple[str, ...] = ()
                      ) -> str | None:
    """第一个真的装着桌面 App 的安装根。"""
    system = _sys(system)
    for root in install_roots(system=system, environ=environ, extra=extra):
        if isfile(desktop_exe_for(root, system=system)):
            return root
    return None


# --------------------------------- 统一定位 -------------------------------
def find_cli(*, system: str | None = None, environ: dict | None = None,
             isfile=os.path.isfile, which=None, reg_dirs=None) -> dict:
    """**这台机器上能用的 magplot CLI 在哪**——统一定位器。

    优先级（与 Codex 插件那侧逐条同源）：

      1. `MAGPLOT_CLI` 显式覆盖——用户指定的永远第一，指错了也如实报错
      2. PATH 里的 `magplot`（pip / pipx 装的）
      3. 安装清单里的 `cli`（桌面版装完就有）
      4. 已知安装位置里的 CLI（清单丢了照样能找到）
      5. HKCU 记着的安装位置里的 CLI（只当补充）

    返回 `{"cmd": [...] | None, "source": ..., "desktop": ..., "searched": [...]}`。
    `cmd` 为 None 而 `desktop` 不为 None 就是那条要单独报的情况：**桌面版装了，
    但这一版没带 CLI**（旧安装），该提示用户升级，而不是「没装 Magplot」。
    """
    system = _sys(system)
    env = _env(environ)
    if which is None:
        import shutil
        which = shutil.which
    searched: list[str] = []

    override = (env.get(CLI_ENV) or "").strip()
    if override:
        return {"cmd": [override], "source": "env", "desktop": None,
                "searched": searched}

    found = which("magplot")
    if found:
        return {"cmd": [found], "source": "path", "desktop": None,
                "searched": searched}

    desktop = None
    manifest = read_manifest(system=system, environ=environ, isfile=isfile)
    if manifest:
        searched.append(manifest["path"])
        if manifest.get("desktop"):
            desktop = manifest["desktop"]
        if manifest.get("cli"):
            return {"cmd": [manifest["cli"]], "source": "manifest",
                    "desktop": desktop, "searched": searched}

    extra = tuple(reg_dirs if reg_dirs is not None else hkcu_install_dirs())
    known = install_roots(system=system, environ=environ)
    for root in install_roots(system=system, environ=environ, extra=extra):
        cli = cli_exe_for(root, system=system)
        searched.append(cli)
        source = "install" if root in known else "registry"
        if isfile(cli):
            return {"cmd": [cli], "source": source,
                    "desktop": desktop or _existing_desktop(root, system, isfile),
                    "searched": searched}
        if desktop is None:
            desktop = _existing_desktop(root, system, isfile)

    return {"cmd": None, "source": None, "desktop": desktop, "searched": searched}


def _existing_desktop(root: str, system: str, isfile) -> str | None:
    exe = desktop_exe_for(root, system=system)
    return exe if isfile(exe) else None


# ----------------------------- 应用自己刷新清单 ---------------------------
#: 刷新清单时「问不到就别动它」的那几个键
_KEEP_IF_UNKNOWN = ("cli", "desktop", "install_dir")


def refresh_manifest(*, source: str = "app", **kw) -> str | None:
    """启动时把清单刷成「我现在在这儿」。**失败一律不打扰用户**。

    为什么应用自己也写：安装器只在装的那一刻写得了一次，而用户会把 .app 拖到
    别处、会用免安装形态、macOS 根本没有装完跑脚本的钩子。每次启动刷一遍，
    清单就永远指着最后一次真的跑起来过的那套。

    **刷新只补充、不抹掉。** 同一台机器上可以既有桌面版（装在非惯例位置）
    又有 pip 装的 magplot：pip 那次是非冻结进程，`describe_self()` 只会去惯例
    位置找壳，找不到就是 None——无条件写下去等于把桌面版那次记下的、仍然有效
    的 `desktop` 抹成空，此后 `magplot open` 再也定位不到那个窗口，静默退回
    浏览器模式。所以这几个键**问不到就沿用上一份**（`read_manifest` 已经核实过
    里面的路径还在，沿用的不会是一条死路径）。
    """
    try:
        info = describe_self(**kw)
        info["source"] = source
        previous = read_manifest()
        if previous:
            for key in _KEEP_IF_UNKNOWN:
                if not info.get(key) and previous.get(key):
                    info[key] = previous[key]
        return write_manifest(info)
    except (OSError, ValueError, ImportError):
        return None
