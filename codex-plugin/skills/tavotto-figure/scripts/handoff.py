#!/usr/bin/env python3
"""把刚画好的图交给 Tavotto：登记 → （必要时）跑脚本 → 唤起界面。

    python3 scripts/handoff.py figures/fig_removal_rate.py
    python3 scripts/handoff.py figures/Fig1_removal_rate.pdf --run never

输出一行 JSON（技能读的就是它）：

    {"ok": true, "ran": true, "project": "...", "stem": "Fig1_removal_rate",
     "parameterizable": true, "launch": "desktop", "conflicts": [],
     "dynamic_names": [], "tavotto": {"source": "manifest", "cmd": "..."}}

判据只有一个：**parameterizable 为 true 才算交接成功**。false 说明这张图在
Tavotto 里双击进不去——多半是脚本没跟产物放在同一个目录，或产物名要到运行期
才知道（见 SKILL.md 的约定 1 与 3）。

退出码（**不可参数化也是非零**：图出来了但只是一张死图，那不是成功，
用 0 报出去等于把「要修」写在一行 JSON 里等人自己发现）：

    0  交接成功且可参数化      3  这台机器上用不了 Tavotto（见 error_code）
    1  脚本运行失败            4  交接了，但这张图不可参数化
    2  路径不对 / tavotto open 失败

失败时一律带 `error_code`（稳定，可分诊）。插件自己的：`tavotto_missing` /
`desktop_found_cli_missing` / `path_not_found` / `script_failed` /
`not_parameterizable`。来自 `tavotto open` 的**原样透传**（`registry_write_failed`
/ `cli_exec_failed` / `unsupported_file` …），CLI 没给 code 才回落到
`open_failed`。完整清单见
https://github.com/Tavotto/Tavotto/blob/main/docs/handoff-protocol.md
（插件包里没有仓库的 docs/，别写相对路径）。

真正干活的是 Tavotto 自己的 `tavotto open`（`src/tavotto/engine/handoff.py`）：
路径解析、注册表合并、唤起桌面 App 还是浏览器，全部在那边裁决。**本脚本不做
第二套判断**，它只负责「找到 tavotto 命令行」「要不要先把脚本跑一遍」
和把结果整理成一行 JSON。

## 怎么找到 tavotto（这里唯一的一处「判断」）

顺序，前面的赢：

    1. TAVOTTO_CLI              用户显式指定的永远第一
    2. PATH 里的 tavotto        pip / pipx 装的
    3. 安装清单 install.json    桌面版装完就有（记着 CLI 的绝对路径）
    4. 已知安装位置里的 CLI     清单丢了/被策略删了照样能找到
    5. HKCU 记着的安装位置      装在非默认目录、又没有清单时只有它知道
    6. 当前解释器里的 tavotto 模块

第 3、4 条是**只装了桌面版**的用户唯一能走通的路：桌面版的 `Tavotto.exe`
是 GUI 子系统的可执行文件，**不能当命令行用**（没有真终端时它的 stdout 是
None，输出会被改道进 app.log，调用方 capture_output 拿到的是空的）。所以桌面
安装包里另带一个 console 版 `tavotto-cli`，上面找的就是它。找到了桌面版却没有
`tavotto-cli` = 用户装的是旧版本，报 `desktop_found_cli_missing` 让他升级，
**不能笼统地说「没装 Tavotto」**——他明明装了。

本文件的路径规则是 `src/tavotto/engine/locate.py` 的**镜像**（插件跑在用户
机器上，import 不到 tavotto）。两侧由
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张
环境矩阵上逐条比对，改一边必须同步另一边。

纯标准库，Python 3.8+。

**Windows 上两处必须钉死 UTF-8**（CI 的 windows-latest 腿实测撞到）：输出的 JSON
带中文（`hint`、`tavotto open` 回来的错误），而 Windows 上 stdout 一旦是管道
（Codex 调它就是管道）就退回系统区域编码 cp1252/cp936——写出去 UnicodeEncodeError
打死进程，读回来 UnicodeDecodeError。两侧都得自己兜底，见 `_force_utf8()` 与每个
`subprocess.run` 的 `encoding=`。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

#: 与 Tavotto 的静态扫描同源的产物后缀
OUT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")

# ---------------------------------------------------------------------------
# 以下常量与 src/tavotto/engine/locate.py 严格同源（见模块注释）
#: 交接协议版本；清单里的 protocol 对不上就当没有这份清单
PROTOCOL = 1
#: 随桌面版一起装的 console 版 CLI
CLI_NAME = "tavotto-cli.exe" if os.name == "nt" else "tavotto-cli"
#: Tauri 壳自己的可执行文件（GUI，不可当 CLI 用）
DESKTOP_NAME = "Tavotto.exe" if os.name == "nt" else "Tavotto"
#: sidecar 在资源目录下的相对位置（tauri.conf.json 的 bundle.resources）
SIDECAR_REL = ("sidecar", "Tavotto")
#: 安装清单文件名（在用户配置目录下）
MANIFEST_NAME = "install.json"
#: 显式覆盖
CLI_ENV = "TAVOTTO_CLI"
#: HKCU 的卸载信息键（NSIS 模板写的就是它）。**只读、只当前用户、只当补充**
UNINSTALL_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Tavotto"
# ---------------------------------------------------------------------------

INSTALL_HINT = (
    "没找到 Tavotto。桌面版在 https://github.com/Tavotto/Tavotto/releases 下载；"
    "命令行版 `pipx install tavotto`（或 `pip install tavotto`）。"
    "装好后重新执行同一条 handoff 命令即可——图已经画出来了。"
)
UPGRADE_HINT = (
    "这台机器上装着 Tavotto 桌面版，但它里面没有可供外部程序调用的命令行"
    "（tavotto-cli）——那是旧版本的安装包。到 "
    "https://github.com/Tavotto/Tavotto/releases 装一次最新版即可；"
    "急用的话也可以 `pipx install tavotto`，或把 TAVOTTO_CLI 指到一个可用的 "
    "tavotto 命令行。图已经画出来了，装完重新执行同一条命令。"
)


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    Codex 调这个脚本时 stdout 是管道，Windows 上于是退回 cp1252/cp936；
    输出里有中文（hint、tavotto open 回来的错误），第一次 print 就
    UnicodeEncodeError 打死进程——调用方看到的是「脚本挂了」，不是那行 JSON。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# --------------------------- 找到 tavotto 命令行 --------------------------
def _is_win(system: str) -> bool:
    return system.startswith("win")


def _join(system: str, *parts: str) -> str:
    """按目标平台的分隔符拼路径。

    **不用 os.path.join、更不用 pathlib**：这几个函数要在 macOS/Linux 的 CI 上
    模拟 Windows 的安装布局（与 Tavotto 那侧对齐的测试就是这么跑的），
    而 `Path()` 按 `os.name` 分派，在别的平台上连构造都做不到。
    """
    sep = "\\" if _is_win(system) else "/"
    head = parts[0].rstrip("/\\") if parts else ""
    return sep.join([head, *parts[1:]])


def hkcu_install_dirs() -> list[str]:
    """HKCU 里记着的安装位置（Windows only）。

    **只读、只当前用户、只作为补充**：读不到（非 Windows、键不存在、被组策略
    挡住）一律回空表。它补的是「装在非默认目录 + 清单又没写成」这一格——
    安装器明确保留了历史/自定义位置，少了这条那些机器上就只剩
    tavotto_missing。与 engine/locate.hkcu_install_dirs() 同源。
    """
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
    except OSError:
        return []
    if isinstance(value, str) and value.strip():
        return [value.strip().strip('"')]
    return []


def install_roots(
    system: str | None = None, environ: dict | None = None, extra: tuple = ()
) -> list[str]:
    """桌面版安装根目录的候选（按优先级）。

    `extra` 给「从别处问出来的位置」（HKCU 的 InstallLocation），排在惯例位置
    之后——注册表可能记着一个已经被手工删掉的老安装。
    """
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    out: list[str] = []
    if system == "darwin":
        out.append("/Applications/Tavotto.app")
        home = (env.get("HOME") or "").rstrip("/")
        if home:
            out.append(home + "/Applications/Tavotto.app")
    elif _is_win(system):
        # 新装固定 %LOCALAPPDATA%\Tavotto（NSIS 是 currentUser 安装）；
        # 后两条是历史上管理员装出来的位置，升级时会沿用。
        for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = (env.get(key) or "").rstrip("\\")
            if base:
                out.append(base + "\\Tavotto")
    # Linux 没有桌面发行形态
    for path in extra:
        cleaned = (path or "").strip().strip('"').rstrip("/\\")
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def desktop_exe_for(root: str, system: str | None = None) -> str:
    system = sys.platform if system is None else system
    if system == "darwin":
        return _join(system, root, "Contents", "MacOS", "Tavotto")
    return _join(system, root, "Tavotto.exe" if _is_win(system) else "Tavotto")


def cli_exe_for(root: str, system: str | None = None) -> str:
    system = sys.platform if system is None else system
    name = "tavotto-cli.exe" if _is_win(system) else "tavotto-cli"
    if system == "darwin":
        return _join(system, root, "Contents", "Resources", *SIDECAR_REL, name)
    return _join(system, root, *SIDECAR_REL, name)


def config_dir(system: str | None = None, environ: dict | None = None) -> str:
    """Tavotto 的用户配置目录（与 engine/config.config_dir() 同规则）。

    安装清单和插件的更新检查缓存都落在这儿——两处共用同一份规则，别再抄第三遍。
    """
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    override = env.get("TAVOTTO_CONFIG_DIR")
    if override:
        return override
    home = (env.get("HOME") or "").rstrip("/")
    if system == "darwin":
        return _join(system, home or "~", "Library", "Application Support", "Tavotto")
    if _is_win(system):
        root = (env.get("APPDATA") or env.get("USERPROFILE") or "%APPDATA%").rstrip("\\")
        return _join(system, root, "Tavotto")
    xdg = (env.get("XDG_CONFIG_HOME") or "").rstrip("/")
    return _join(system, xdg or _join(system, home or "~", ".config"), "tavotto")


def manifest_path(system: str | None = None, environ: dict | None = None) -> str:
    """安装清单的落点。"""
    system = sys.platform if system is None else system
    return _join(system, config_dir(system, environ), MANIFEST_NAME)


def read_manifest(
    system: str | None = None, environ: dict | None = None, isfile=os.path.isfile
) -> dict | None:
    """读安装清单。**里面的路径要核实还在**——清单是缓存不是真相。

    卸载、手工删目录、从备份还原用户配置，都会留下一份指向不存在文件的清单。
    不核实就会拿着一条早就没了的路径去 spawn，报出来的错是「执行不了」
    而不是「没装」。
    """
    path = manifest_path(system, environ)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
        return None  # 另一代约定：当没有
    out = {"path": path, "cli": None, "desktop": None, "version": data.get("version")}
    for key in ("cli", "desktop"):
        value = data.get(key)
        if isinstance(value, str) and value.strip() and isfile(value):
            out[key] = value
    return out


def find_tavotto(
    system: str | None = None,
    environ: dict | None = None,
    isfile=os.path.isfile,
    which=None,
    reg_dirs=None,
) -> dict:
    """定位 tavotto 命令行。返回

        {"cmd": [...] | None, "source": ..., "desktop": ..., "searched": [...]}

    `cmd` 为 None 而 `desktop` 不为 None，就是要单独报的那种情况：桌面版装了，
    但那一版没带 `tavotto-cli`。
    """
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    searched: list[str] = []

    override = (env.get(CLI_ENV) or "").strip()
    if override:  # 1. 显式覆盖
        return {"cmd": [override], "source": "env", "desktop": None, "searched": searched}

    found = which("tavotto")
    if found:  # 2. PATH
        return {"cmd": [found], "source": "path", "desktop": None, "searched": searched}

    desktop = None
    manifest = read_manifest(system, env, isfile)  # 3. 安装清单
    if manifest:
        searched.append(manifest["path"])
        desktop = manifest["desktop"]
        if manifest["cli"]:
            return {
                "cmd": [manifest["cli"]],
                "source": "manifest",
                "desktop": desktop,
                "searched": searched,
            }

    extra = tuple(reg_dirs if reg_dirs is not None else hkcu_install_dirs())
    known = install_roots(system, env)
    for root in install_roots(system, env, extra):  # 4. 已知位置 / 5. HKCU
        cli = cli_exe_for(root, system)
        searched.append(cli)
        if isfile(cli):
            return {
                "cmd": [cli],
                "source": "install" if root in known else "registry",
                "desktop": desktop or _desktop_at(root, system, isfile),
                "searched": searched,
            }
        if desktop is None:
            desktop = _desktop_at(root, system, isfile)

    return {
        "cmd": None,
        "source": None,
        "desktop": desktop,  # 6. 本解释器
        "searched": searched,
    }


def _desktop_at(root: str, system: str, isfile) -> str | None:
    exe = desktop_exe_for(root, system)
    return exe if isfile(exe) else None


def tavotto_cmd() -> dict:
    """完整发现链，包含最后那条「当前解释器里的 tavotto 模块」。"""
    found = find_tavotto()
    if found["cmd"]:
        return found
    probe = subprocess.run([sys.executable, "-c", "import tavotto"], capture_output=True)
    if probe.returncode == 0:
        found = dict(found)
        found["cmd"] = [sys.executable, "-m", "tavotto"]
        found["source"] = "module"
    return found


# ------------------------------- 调用 tavotto -----------------------------
def run_tavotto_open(cmd: list[str], path: str, *, launch: bool) -> dict:
    argv = [*cmd, "open", path, "--json"]
    if not launch:
        argv.append("--no-launch")
    try:
        # 参数一律走数组，不拼 shell 字符串：路径里的空格、中文、`&` `%` `^`
        # 交给 CreateProcess/execve 自己处理，经 shell 中转必然出事。
        # encoding 也必须显式：text=True 跟随系统区域编码，cp936/cp1252 下
        # `tavotto open` 回来的中文 JSON 一解码就炸（Windows CI 实测）。
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        # CLI 路径指到了不存在/起不来的东西：说清楚是哪一条，别抛 traceback
        return {"ok": False, "code": "cli_exec_failed", "error": f"执行不了 {argv[0]}: {exc}"}
    line = (proc.stdout or "").strip().splitlines()
    try:
        return (
            json.loads(line[-1])
            if line
            else {"ok": False, "error": (proc.stderr or "").strip() or "tavotto open 没有输出"}
        )
    except ValueError:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()[:500]}


def product_of(project: str, stem: str) -> str | None:
    for ext in OUT_EXTS:
        candidate = os.path.join(project, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


def needs_run(script: str, project: str, stem: str | None, mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never" or not script.endswith(".py"):
        return False
    if stem is None:
        return True  # 还不知道产物是什么：跑一遍才有得看
    product = product_of(project, stem)
    if product is None:
        return True
    return os.path.getmtime(product) < os.path.getmtime(script)


def script_env(environ: dict | None = None) -> dict:
    """跑用户脚本时的环境：**无头 + 稳定的 matplotlib 缓存目录**。

    * `MPLBACKEND=Agg`（用户没自己设时）——出图脚本从 Agent/CI 里跑，默认
      GUI backend 在没有显示会话的环境里会崩在 AppKit/Qt 初始化上；
    * `MPLCONFIGDIR` 指到 Tavotto 配置目录下的固定位置（用户没设、且原位置
      需要兜底时）——沙箱里 HOME 只读时 matplotlib 每次都重建字体缓存，
      一次十来秒，还写不进去刷警告。固定目录 = 缓存建一次以后一直用。

    只补缺，不覆盖：用户显式设过的两个变量原样保留。判据是「设没设」而不是
    「能不能写」——探测可写性本身就要写文件，不值得。
    """
    env = dict(os.environ if environ is None else environ)
    env.setdefault("MPLBACKEND", "Agg")
    if "MPLCONFIGDIR" not in env:
        cache = _join(sys.platform, config_dir(), "mpl-cache")
        try:
            os.makedirs(cache, exist_ok=True)
            env["MPLCONFIGDIR"] = cache
        except OSError:
            pass  # 建不出来就让 matplotlib 走自己的默认
    return env


def run_script(python: str, script: str) -> tuple[bool, str]:
    """在脚本自己的目录里跑它——脚本里的相对路径按这个目录解析。

    成败**只看退出码**：matplotlib 的 UserWarning/DeprecationWarning 走 stderr，
    把 stderr 有内容当失败会把一半正常脚本误杀。
    """
    proc = subprocess.run(
        [python, script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=script_env(),
        cwd=os.path.dirname(os.path.abspath(script)) or ".",
    )
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
    return False, "\n".join(tail)


#: `tavotto open` 失败时随 code 一起透传的结构化细节（桌面启动失败的分诊线索：
#: 是「装坏了」还是「这个环境起不了 GUI」，只有 `signal` / `log_path` 说得清）
_FAILURE_DETAIL_KEYS = ("app", "exit_code", "signal", "log_path", "retryable")


def _open_failure(result: dict, tavotto: dict, **extra) -> dict:
    """`tavotto open` 没成时的统一回报。

    **CLI 给了具体 code 就原样当 error_code 用。** 调用方（SKILL.md 教 Codex 的
    就是这件事）只看一个字段分诊：`registry_write_failed` 要去改目录权限、
    `path_not_found` 要去看路径。把它们统统压成 `open_failed`、真 code 藏进
    第二层，等于让对面多猜一次——而 SKILL.md 里写的恰恰是按 `error_code`
    分支，两边当场对不上。CLI 没给 code（老版本 / 没有输出）才回 open_failed。

    `launch_failed` / `launch_timeout` 随附的细节（`exit_code`、`signal`、
    `log_path`…）**逐键透传**——桌面进程 SIGABRT 与「权限不够」在上层要走
    不同的话术，压掉细节等于让 Codex 只能对用户说「失败了」。
    """
    code = result.get("code")
    out = {
        "ok": False,
        "tavotto": tavotto,
        "error_code": code or "open_failed",
        "code": code,
        "error": result.get("error", "tavotto open 失败"),
    }
    for key in _FAILURE_DETAIL_KEYS:
        if key in result:
            out[key] = result[key]
    out.update(extra)
    return out


def update_notice(tavotto_version: str | None = None) -> dict | None:
    """插件自己有没有新版本。**任何问题都当没有**——这是个提醒，不是功能。

    实现在 update_check.py（同目录）。import 失败也要活下去：用户可能只拷了
    handoff.py 一个文件过去。
    """
    try:
        import update_check

        found = update_check.check(tavotto_version=tavotto_version)
    except Exception:  # noqa: BLE001 —— 提醒不许拖累出图
        return None
    return None if found.get("status") == "disabled" else found


def emit(payload: dict, code: int, tavotto_version: str | None = None) -> int:
    """**唯一的输出出口。** stdout 只有那一行 JSON，别的一律 stderr。

    调用方读的是 stdout 的最后一行；往里写一句「有新版本」就等于把整条链路
    弄坏（json.loads 当场炸）。所以人话走 stderr，机器读的进 JSON 字段。
    """
    update = update_notice(tavotto_version)
    if update:
        payload["update"] = update
        try:
            import update_check

            for line in (update_check.hint(update), update_check.tavotto_hint(update)):
                if line:
                    print(line, file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps(payload, ensure_ascii=False))
    return code


def _missing_payload(found: dict, ran: bool, script: str) -> tuple[dict, int]:
    """没有可用 CLI 时的结构化回报。**两种情况不能混为一谈。**"""
    if found.get("desktop"):
        return (
            {
                "ok": False,
                "error_code": "desktop_found_cli_missing",
                "tavotto_missing": True,
                "ran": ran,
                "script": script,
                "desktop": found["desktop"],
                "searched": found.get("searched", []),
                "hint": UPGRADE_HINT,
            },
            3,
        )
    return (
        {
            "ok": False,
            "error_code": "tavotto_missing",
            "tavotto_missing": True,
            "ran": ran,
            "script": script,
            "searched": found.get("searched", []),
            "hint": INSTALL_HINT,
        },
        3,
    )


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="脚本（.py）或它的产物（.pdf/.png…）")
    ap.add_argument(
        "--run",
        choices=("auto", "always", "never"),
        default="auto",
        help="是否先跑一遍脚本：auto=产物缺失或比脚本旧才跑",
    )
    ap.add_argument("--python", default=sys.executable, help="跑脚本用的解释器（默认与本脚本相同）")
    ap.add_argument("--no-launch", action="store_true", help="只登记与自检，不唤起 Tavotto 界面")
    args = ap.parse_args(argv)

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.exists(path):
        return emit(
            {"ok": False, "error_code": "path_not_found", "error": f"路径不存在: {path}"}, 2
        )

    found = tavotto_cmd()
    if found["cmd"] is None:
        # Tavotto 用不了：图还是要画出来，然后如实告诉调用方缺什么。
        ran = False
        if args.run != "never" and path.endswith(".py"):
            ran, err = run_script(args.python, path)
            if not ran:
                return emit(
                    {
                        "ok": False,
                        "error_code": "script_failed",
                        "error": "脚本运行失败",
                        "stderr": err,
                    },
                    1,
                )
        payload, code = _missing_payload(found, ran, path)
        return emit(payload, code)

    cmd = found["cmd"]
    tavotto = {"source": found["source"], "cmd": cmd[0]}
    import time

    t0 = time.monotonic()
    timings: dict = {}

    # 稳定产物（给的是 .pdf/.png…）或 `--run never` 时 `needs_run` 恒为 False：
    # 先探测再交接的两跳退化成**一跳**——探测那次除了把同一份注册表再读一遍
    # 什么都不产出，白付一次 CLI 冷启动（frozen CLI 一跳几百 ms）。
    probe: dict = {}
    ran = False
    if args.run != "never" and path.endswith(".py"):
        # 1. 先问 Tavotto：这是哪个项目、哪个 stem（顺手把注册表补齐）
        t = time.monotonic()
        probe = run_tavotto_open(cmd, path, launch=False)
        timings["probe_ms"] = int((time.monotonic() - t) * 1000)
        if not probe.get("ok"):
            return emit(_open_failure(probe, tavotto), 2)

        # 2. 需要的话跑一遍脚本
        if needs_run(path, probe["project"], probe.get("stem"), args.run):
            t = time.monotonic()
            ran, err = run_script(args.python, path)
            timings["run_ms"] = int((time.monotonic() - t) * 1000)
            if not ran:
                return emit(
                    {
                        "ok": False,
                        "error_code": "script_failed",
                        "error": "脚本运行失败",
                        "stderr": err,
                        "project": probe["project"],
                    },
                    1,
                )

    # 3. 交接。跑过脚本时**必须再解析一次**：刚跑出来的产物可能带来新的 stem，
    #    第一次探测时它还不在磁盘上（登记与定位都会落空）。
    t = time.monotonic()
    final = run_tavotto_open(cmd, path, launch=not args.no_launch)
    timings["open_ms"] = int((time.monotonic() - t) * 1000)
    timings["total_ms"] = int((time.monotonic() - t0) * 1000)
    if not final.get("ok"):
        return emit(_open_failure(final, tavotto, ran=ran, timings=timings), 2)

    version = final.get("version") or probe.get("version")
    reg = final.get("registry", {})
    # 注册表状态取**两次调用里更有信息量的那个**。走了探测那条路时，第一次
    # 就把注册表建好了，第二次自然回 already——直接报第二次的话，
    # 「这次新建了注册表」永远显示不出来。
    status = reg.get("status")
    first = (probe.get("registry") or {}).get("status")
    if status in ("already", "unchanged") and first in ("created", "merged"):
        status = first
    payload = {
        "ok": True,
        "ran": ran,
        "project": final["project"],
        "stem": final.get("stem"),
        "parameterizable": reg.get("parameterizable"),
        "registry_status": status,
        "launch": (final.get("launch") or {}).get("mode"),
        "conflicts": reg.get("conflicts", []),
        "dynamic_names": reg.get("dynamic_names", []),
        "tavotto": tavotto,
        "timings": timings,
    }
    if payload["parameterizable"] is not True:
        payload["ok"] = False
        payload["error_code"] = "not_parameterizable"
        payload["hint"] = (
            "这张图没有对应脚本，在 Tavotto 里只能当素材排版。"
            "把产出它的 .py 放到产物同一个目录，并让产物名是脚本里的字面量"
            "（不要来自 sys.argv / 时间戳），然后重新交接。"
        )
        return emit(payload, 4, version)
    return emit(payload, 0, version)


if __name__ == "__main__":
    raise SystemExit(main())
