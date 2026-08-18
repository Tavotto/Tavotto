#!/usr/bin/env python3
"""把刚画好的图交给 Magplot：登记 → （必要时）跑脚本 → 唤起界面。

    python3 scripts/handoff.py figures/fig_removal_rate.py
    python3 scripts/handoff.py figures/Fig1_removal_rate.pdf --run never

输出一行 JSON（技能读的就是它）：

    {"ok": true, "ran": true, "project": "...", "stem": "Fig1_removal_rate",
     "parameterizable": true, "launch": "desktop", "conflicts": [],
     "dynamic_names": [], "magplot": {"source": "manifest", "cmd": "..."}}

判据只有一个：**parameterizable 为 true 才算交接成功**。false 说明这张图在
Magplot 里双击进不去——多半是脚本没跟产物放在同一个目录，或产物名要到运行期
才知道（见 SKILL.md 的约定 1 与 3）。

退出码（**不可参数化也是非零**：图出来了但只是一张死图，那不是成功，
用 0 报出去等于把「要修」写在一行 JSON 里等人自己发现）：

    0  交接成功且可参数化      3  这台机器上用不了 Magplot（见 error_code）
    1  脚本运行失败            4  交接了，但这张图不可参数化
    2  路径不对 / magplot open 失败

失败时一律带 `error_code`（稳定，可分诊）：`magplot_missing` /
`desktop_found_cli_missing` / `path_not_found` / `script_failed` /
`open_failed` / `cli_exec_failed` / `not_parameterizable`。
`open_failed` 还会把 `magplot open` 自己的 code 原样带出来（比如
`registry_write_failed`）。完整清单见 docs/handoff-protocol.md。

真正干活的是 Magplot 自己的 `magplot open`（`src/magplot/engine/handoff.py`）：
路径解析、注册表合并、唤起桌面 App 还是浏览器，全部在那边裁决。**本脚本不做
第二套判断**，它只负责「找到 magplot 命令行」「要不要先把脚本跑一遍」
和把结果整理成一行 JSON。

## 怎么找到 magplot（这里唯一的一处「判断」）

顺序，前面的赢：

    1. MAGPLOT_CLI              用户显式指定的永远第一
    2. PATH 里的 magplot        pip / pipx 装的
    3. 安装清单 install.json    桌面版装完就有（记着 CLI 的绝对路径）
    4. 已知安装位置里的 CLI     清单丢了/被策略删了照样能找到
    5. 当前解释器里的 magplot 模块

第 3、4 条是**只装了桌面版**的用户唯一能走通的路：桌面版的 `Magplot.exe`
是 GUI 子系统的可执行文件，**不能当命令行用**（没有真终端时它的 stdout 是
None，输出会被改道进 app.log，调用方 capture_output 拿到的是空的）。所以桌面
安装包里另带一个 console 版 `magplot-cli`，上面找的就是它。找到了桌面版却没有
`magplot-cli` = 用户装的是旧版本，报 `desktop_found_cli_missing` 让他升级，
**不能笼统地说「没装 Magplot」**——他明明装了。

本文件的路径规则是 `src/magplot/engine/locate.py` 的**镜像**（插件跑在用户
机器上，import 不到 magplot）。两侧由
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张
环境矩阵上逐条比对，改一边必须同步另一边。

纯标准库，Python 3.8+。

**Windows 上两处必须钉死 UTF-8**（CI 的 windows-latest 腿实测撞到）：输出的 JSON
带中文（`hint`、`magplot open` 回来的错误），而 Windows 上 stdout 一旦是管道
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

#: 与 Magplot 的静态扫描同源的产物后缀
OUT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")

# ---------------------------------------------------------------------------
# 以下常量与 src/magplot/engine/locate.py 严格同源（见模块注释）
#: 交接协议版本；清单里的 protocol 对不上就当没有这份清单
PROTOCOL = 1
#: 随桌面版一起装的 console 版 CLI
CLI_NAME = "magplot-cli.exe" if os.name == "nt" else "magplot-cli"
#: Tauri 壳自己的可执行文件（GUI，不可当 CLI 用）
DESKTOP_NAME = "Magplot.exe" if os.name == "nt" else "Magplot"
#: sidecar 在资源目录下的相对位置（tauri.conf.json 的 bundle.resources）
SIDECAR_REL = ("sidecar", "Magplot")
#: 安装清单文件名（在用户配置目录下）
MANIFEST_NAME = "install.json"
#: 显式覆盖
CLI_ENV = "MAGPLOT_CLI"
# ---------------------------------------------------------------------------

INSTALL_HINT = (
    "没找到 Magplot。桌面版在 https://github.com/erwanjun/magplot/releases 下载；"
    "命令行版 `pipx install magplot`（或 `pip install magplot`）。"
    "装好后重新执行同一条 handoff 命令即可——图已经画出来了。"
)
UPGRADE_HINT = (
    "这台机器上装着 Magplot 桌面版，但它里面没有可供外部程序调用的命令行"
    "（magplot-cli）——那是旧版本的安装包。到 "
    "https://github.com/erwanjun/magplot/releases 装一次最新版即可；"
    "急用的话也可以 `pipx install magplot`，或把 MAGPLOT_CLI 指到一个可用的 "
    "magplot 命令行。图已经画出来了，装完重新执行同一条命令。"
)


def _force_utf8() -> None:
    """把自己的 stdout/stderr 钉成 UTF-8。

    Codex 调这个脚本时 stdout 是管道，Windows 上于是退回 cp1252/cp936；
    输出里有中文（hint、magplot open 回来的错误），第一次 print 就
    UnicodeEncodeError 打死进程——调用方看到的是「脚本挂了」，不是那行 JSON。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


# --------------------------- 找到 magplot 命令行 --------------------------
def _is_win(system: str) -> bool:
    return system.startswith("win")


def _join(system: str, *parts: str) -> str:
    """按目标平台的分隔符拼路径。

    **不用 os.path.join、更不用 pathlib**：这几个函数要在 macOS/Linux 的 CI 上
    模拟 Windows 的安装布局（与 Magplot 那侧对齐的测试就是这么跑的），
    而 `Path()` 按 `os.name` 分派，在别的平台上连构造都做不到。
    """
    sep = "\\" if _is_win(system) else "/"
    head = parts[0].rstrip("/\\") if parts else ""
    return sep.join([head, *parts[1:]])


def install_roots(system: str | None = None, environ: dict | None = None) -> list[str]:
    """桌面版安装根目录的候选（按优先级）。"""
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    out: list[str] = []
    if system == "darwin":
        out.append("/Applications/Magplot.app")
        home = (env.get("HOME") or "").rstrip("/")
        if home:
            out.append(home + "/Applications/Magplot.app")
    elif _is_win(system):
        # 新装固定 %LOCALAPPDATA%\Magplot（NSIS 是 currentUser 安装）；
        # 后两条是历史上管理员装出来的位置，升级时会沿用。
        for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = (env.get(key) or "").rstrip("\\")
            if base:
                out.append(base + "\\Magplot")
    # Linux 没有桌面发行形态
    return out


def desktop_exe_for(root: str, system: str | None = None) -> str:
    system = sys.platform if system is None else system
    if system == "darwin":
        return _join(system, root, "Contents", "MacOS", "Magplot")
    return _join(system, root, "Magplot.exe" if _is_win(system) else "Magplot")


def cli_exe_for(root: str, system: str | None = None) -> str:
    system = sys.platform if system is None else system
    name = "magplot-cli.exe" if _is_win(system) else "magplot-cli"
    if system == "darwin":
        return _join(system, root, "Contents", "Resources", *SIDECAR_REL, name)
    return _join(system, root, *SIDECAR_REL, name)


def manifest_path(system: str | None = None, environ: dict | None = None) -> str:
    """安装清单的落点 = 用户配置目录（与 engine/config.config_dir() 同规则）。"""
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    override = env.get("MAGPLOT_CONFIG_DIR")
    if override:
        return _join(system, override, MANIFEST_NAME)
    home = (env.get("HOME") or "").rstrip("/")
    if system == "darwin":
        base = _join(system, home or "~", "Library", "Application Support", "Magplot")
    elif _is_win(system):
        root = (env.get("APPDATA") or env.get("USERPROFILE") or "%APPDATA%").rstrip("\\")
        base = _join(system, root, "Magplot")
    else:
        xdg = (env.get("XDG_CONFIG_HOME") or "").rstrip("/")
        base = _join(system, xdg or _join(system, home or "~", ".config"), "magplot")
    return _join(system, base, MANIFEST_NAME)


def read_manifest(system: str | None = None, environ: dict | None = None,
                  isfile=os.path.isfile) -> dict | None:
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
        return None                                   # 另一代约定：当没有
    out = {"path": path, "cli": None, "desktop": None,
           "version": data.get("version")}
    for key in ("cli", "desktop"):
        value = data.get(key)
        if isinstance(value, str) and value.strip() and isfile(value):
            out[key] = value
    return out


def find_magplot(system: str | None = None, environ: dict | None = None,
                 isfile=os.path.isfile, which=None) -> dict:
    """定位 magplot 命令行。返回

        {"cmd": [...] | None, "source": ..., "desktop": ..., "searched": [...]}

    `cmd` 为 None 而 `desktop` 不为 None，就是要单独报的那种情况：桌面版装了，
    但那一版没带 `magplot-cli`。
    """
    system = sys.platform if system is None else system
    env = os.environ if environ is None else environ
    which = shutil.which if which is None else which
    searched: list[str] = []

    override = (env.get(CLI_ENV) or "").strip()
    if override:                                      # 1. 显式覆盖
        return {"cmd": [override], "source": "env", "desktop": None,
                "searched": searched}

    found = which("magplot")
    if found:                                         # 2. PATH
        return {"cmd": [found], "source": "path", "desktop": None,
                "searched": searched}

    desktop = None
    manifest = read_manifest(system, env, isfile)     # 3. 安装清单
    if manifest:
        searched.append(manifest["path"])
        desktop = manifest["desktop"]
        if manifest["cli"]:
            return {"cmd": [manifest["cli"]], "source": "manifest",
                    "desktop": desktop, "searched": searched}

    for root in install_roots(system, env):           # 4. 已知安装位置
        cli = cli_exe_for(root, system)
        searched.append(cli)
        if isfile(cli):
            return {"cmd": [cli], "source": "install",
                    "desktop": desktop or _desktop_at(root, system, isfile),
                    "searched": searched}
        if desktop is None:
            desktop = _desktop_at(root, system, isfile)

    return {"cmd": None, "source": None, "desktop": desktop,   # 5. 本解释器
            "searched": searched}


def _desktop_at(root: str, system: str, isfile) -> str | None:
    exe = desktop_exe_for(root, system)
    return exe if isfile(exe) else None


def magplot_cmd() -> dict:
    """完整发现链，包含最后那条「当前解释器里的 magplot 模块」。"""
    found = find_magplot()
    if found["cmd"]:
        return found
    probe = subprocess.run([sys.executable, "-c", "import magplot"],
                           capture_output=True)
    if probe.returncode == 0:
        found = dict(found)
        found["cmd"] = [sys.executable, "-m", "magplot"]
        found["source"] = "module"
    return found


# ------------------------------- 调用 magplot -----------------------------
def run_magplot_open(cmd: list[str], path: str, *, launch: bool) -> dict:
    argv = [*cmd, "open", path, "--json"]
    if not launch:
        argv.append("--no-launch")
    try:
        # 参数一律走数组，不拼 shell 字符串：路径里的空格、中文、`&` `%` `^`
        # 交给 CreateProcess/execve 自己处理，经 shell 中转必然出事。
        # encoding 也必须显式：text=True 跟随系统区域编码，cp936/cp1252 下
        # `magplot open` 回来的中文 JSON 一解码就炸（Windows CI 实测）。
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        # CLI 路径指到了不存在/起不来的东西：说清楚是哪一条，别抛 traceback
        return {"ok": False, "code": "cli_exec_failed",
                "error": f"执行不了 {argv[0]}: {exc}"}
    line = (proc.stdout or "").strip().splitlines()
    try:
        return json.loads(line[-1]) if line else {
            "ok": False, "error": (proc.stderr or "").strip() or "magplot open 没有输出"}
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
        return True                       # 还不知道产物是什么：跑一遍才有得看
    product = product_of(project, stem)
    if product is None:
        return True
    return os.path.getmtime(product) < os.path.getmtime(script)


def run_script(python: str, script: str) -> tuple[bool, str]:
    """在脚本自己的目录里跑它——脚本里的相对路径按这个目录解析。"""
    proc = subprocess.run([python, script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=os.path.dirname(os.path.abspath(script)) or ".")
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
    return False, "\n".join(tail)


def _open_failure(result: dict, magplot: dict, **extra) -> dict:
    """`magplot open` 没成时的统一回报。

    「CLI 本身起不来」与「CLI 起来了但拒绝了这次交接」是两回事：前者要去看
    安装，后者要去看图库（注册表写不进去、路径不认识）。合成一个 code
    等于让调用方自己去猜。
    """
    code = result.get("code")
    return {"ok": False, "magplot": magplot,
            "error_code": "cli_exec_failed" if code == "cli_exec_failed"
                          else "open_failed",
            "code": code,
            "error": result.get("error", "magplot open 失败"), **extra}


def emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def _missing_payload(found: dict, ran: bool, script: str) -> tuple[dict, int]:
    """没有可用 CLI 时的结构化回报。**两种情况不能混为一谈。**"""
    if found.get("desktop"):
        return ({"ok": False, "error_code": "desktop_found_cli_missing",
                 "magplot_missing": True, "ran": ran, "script": script,
                 "desktop": found["desktop"], "searched": found.get("searched", []),
                 "hint": UPGRADE_HINT}, 3)
    return ({"ok": False, "error_code": "magplot_missing",
             "magplot_missing": True, "ran": ran, "script": script,
             "searched": found.get("searched", []), "hint": INSTALL_HINT}, 3)


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="脚本（.py）或它的产物（.pdf/.png…）")
    ap.add_argument("--run", choices=("auto", "always", "never"), default="auto",
                    help="是否先跑一遍脚本：auto=产物缺失或比脚本旧才跑")
    ap.add_argument("--python", default=sys.executable,
                    help="跑脚本用的解释器（默认与本脚本相同）")
    ap.add_argument("--no-launch", action="store_true",
                    help="只登记与自检，不唤起 Magplot 界面")
    args = ap.parse_args(argv)

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.exists(path):
        return emit({"ok": False, "error_code": "path_not_found",
                     "error": f"路径不存在: {path}"}, 2)

    found = magplot_cmd()
    if found["cmd"] is None:
        # Magplot 用不了：图还是要画出来，然后如实告诉调用方缺什么。
        ran = False
        if args.run != "never" and path.endswith(".py"):
            ran, err = run_script(args.python, path)
            if not ran:
                return emit({"ok": False, "error_code": "script_failed",
                             "error": "脚本运行失败", "stderr": err}, 1)
        payload, code = _missing_payload(found, ran, path)
        return emit(payload, code)

    cmd = found["cmd"]
    magplot = {"source": found["source"], "cmd": cmd[0]}

    # 1. 先问 Magplot：这是哪个项目、哪个 stem（顺手把注册表补齐）
    probe = run_magplot_open(cmd, path, launch=False)
    if not probe.get("ok"):
        return emit(_open_failure(probe, magplot), 2)

    # 2. 需要的话跑一遍脚本
    ran = False
    if needs_run(path, probe["project"], probe.get("stem"), args.run):
        ran, err = run_script(args.python, path)
        if not ran:
            return emit({"ok": False, "error_code": "script_failed",
                         "error": "脚本运行失败", "stderr": err,
                         "project": probe["project"]}, 1)

    # 3. 交接。**必须再解析一次**：脚本刚跑出来的产物可能带来新的 stem，
    #    第一次探测时它还不在磁盘上（登记与定位都会落空）。
    final = run_magplot_open(cmd, path, launch=not args.no_launch)
    if not final.get("ok"):
        return emit(_open_failure(final, magplot, ran=ran), 2)

    reg = final.get("registry", {})
    # 注册表状态取**两次调用里更有信息量的那个**。交接固定调两次
    # （先探测再交接），第一次就把注册表建好了，第二次自然回 already——
    # 直接报第二次的话，「这次新建了注册表」永远显示不出来。
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
        "magplot": magplot,
    }
    if payload["parameterizable"] is not True:
        payload["ok"] = False
        payload["error_code"] = "not_parameterizable"
        payload["hint"] = (
            "这张图没有对应脚本，在 Magplot 里只能当素材排版。"
            "把产出它的 .py 放到产物同一个目录，并让产物名是脚本里的字面量"
            "（不要来自 sys.argv / 时间戳），然后重新交接。")
        return emit(payload, 4)
    return emit(payload, 0)


if __name__ == "__main__":
    raise SystemExit(main())
