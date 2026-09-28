"""`tavotto codex install / doctor / uninstall`（ADR 0012）。**纯标准库。**

## 为什么是一条命令，而不是一份文档

首次使用体验重构把普通用户的安装收敛到了「两条 Codex 命令 + 一条引擎命令 + 新开
会话」。那仍是四个手工步骤，且失败分诊靠用户读输出。桌面设置页也想要一个「安装
Codex 集成」按钮——**如果按钮另写一套安装器，它就会与 README 的命令漂移**（本仓库
最忌讳的第二权威）。所以先有这条命令，按钮以后 spawn 它。

## 三条纪律

* **幂等，缺什么补什么。** 每一步都带 `skipped`：重跑一次必须能看出「什么都没做」。
  健康状态下不重装任何组件（与 SKILL 会话入口同一契约）。
* **只报告，不代劳。** 不自动装/升级 Codex CLI 本身；找不到就给安装指引。
* **`--json` 时失败也必须是一行 JSON**，带稳定 `error_code`（与 `tavotto open`
  同一条纪律）。只往 stderr 写一句中文，调用方就只能去匹配字符串。

安装参数（marketplace 源、sparse 路径、插件引用）全部从 `brand.py` 派生——README
与这条命令共用同一份，看护在 `tests/test_codex_install_cli.py`。

## 问 Codex 之前先让它跑得起来（2026-09-06，用户反馈 05）

判据的主语是「**Codex 自己**认为登记 / 装了没有」，问法是起一个 `codex` 子进程。
桌面壳从 Finder / Dock 启动时继承的是 launchd 的最小 PATH（macOS 实测
`/usr/bin:/bin:/usr/sbin:/sbin`），`tavotto-cli codex doctor` 从壳里 spawn 出来拿的
也是这份环境：`find_codex()` 靠兜底目录找得到 `/opt/homebrew/bin/codex`，但它是 npm
shim（`#!/usr/bin/env node`），子进程里解析不到 `node`——退出码 127、输出
`env: node: No such file or directory`。于是「问不到」，界面上就是「插件市场登记
失败」，而用户的 Codex 里明明装着、启用着。所以起 codex 一律用
`ai_agents.spawn_env()` 补过常见安装目录的 PATH（与 AI 桥同一份，不抄第二份）。
**只补 codex 那几跳**：插件启动命令那一步量的是「Codex 会怎么起它」，不能替它补环境。

## 只装了 Codex 桌面版、没有 git 的机器（2026-09-29，#722）

真 Windows 11 + Codex Desktop 26.924 上两件事同时成立：桌面版自带的 CLI 在
`%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<构建哈希>\\codex.exe`、不在 PATH 上；机器上没有 git，
`codex plugin marketplace add Tavotto/Tavotto` 报 `failed to run git clone … program not found`。
所以：

* `find_codex()` 在 PATH 与常见目录之后，Windows 上再找桌面版自带的那一份（要 `--version`
  跑得起来才算，新到旧）；
* marketplace 那一步仍先走 README 主路（git 市场）。**只有** Codex 说它起不来 git
  （`failed to run git …`——codex-rs 起子进程失败时的原文；git 起来了但克隆失败不是这一句）时，
  才改从 GitHub 下载发行分支的源码压缩包：解压、按随包清单逐文件核对、收据的 content_digest
  与同一次 release 附带的构建清单对上，再作为**本地市场**登记（`codex plugin marketplace add
  <目录>`，本地市场不需要 git）。目录在 `config.data_dir()` 下，Tavotto 自己的数据，不碰 `~/.codex`。
* 本地市场没有 `marketplace upgrade`（Codex 回「not configured as a Git marketplace」）：升级是
  `tavotto codex upgrade`——重新下载、核对、换掉目录，再 `codex plugin add` 一次（真 CLI 实测，
  同名插件再 add 会把缓存换成新版本）。
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import http.client
import io
import json
import lzma
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from . import ai_agents, atomicio, brand, config, pluginmanifest, tlstrust
from .runtime import CREATE_NO_WINDOW, probe_args

#: 每一步的稳定 code。message 随时可改，code 不许改（调用方按它分诊）。
ERR_CODEX_MISSING = "codex_cli_missing"
ERR_MARKETPLACE = "marketplace_add_failed"
ERR_PLUGIN = "plugin_add_failed"
ERR_PROVISION = "provision_failed"
ERR_INTERPRETER = "interpreter_unusable"
ERR_PIN = "pin_failed"
ERR_HEALTH = "health_failed"
ERR_UNINSTALL = "uninstall_failed"
#: 「不知道」是独立一档，不并进「没有」：`codex plugin … list` 本身失败时，登记状态
#: 与安装状态都答不上来，这时候跑 add 是在盲改。
ERR_MARKETPLACE_UNKNOWN = "marketplace_state_unknown"
ERR_PLUGIN_UNKNOWN = "plugin_state_unknown"
#: marketplace 已登记，但 Codex 没把 tavotto 列出来：市场清单里的来源类型这个客户端
#: 不认识（`git-subdir` 需要较新的 Codex），或快照太旧。跑 `plugin add` 只会失败。
ERR_SOURCE_UNSUPPORTED = "plugin_source_unsupported"
#: 定位不到**唯一**的已装副本（多份同名缓存、或客户端没报路径）：不按版本号 / mtime 猜
ERR_PLUGIN_AMBIGUOUS = "plugin_install_ambiguous"
#: 已装副本的画布缺失 / 空 / 损坏，或与随包清单的摘要不符
ERR_CANVAS = "canvas_incomplete"
#: 引擎版本低于已装插件要求的最低版本
ERR_ENGINE_OLD = "engine_too_old"
#: 引擎在（pip / pipx 装的），桥却 import 不全，而说不出它是不是太旧（插件没带构建清单 /
#: 装残了）。与插件降级 server 的同名 code 同义（#721）；「不知道」不并进「太旧」
ERR_ENGINE_INCOMPATIBLE = "engine_incompatible"
#: 插件体检报出的、要原样沿用的引擎版本类 code（降级 server 与本命令口径一致）
_ENGINE_VERSION_CODES = (ERR_ENGINE_OLD, ERR_ENGINE_INCOMPATIBLE)

#: 单条 Codex 命令的上限。marketplace add 要拉一次稀疏检出，给宽一点；
#: 但必须有上限——没有网络时它会一直挂着，而调用方在等那行 JSON。
_TIMEOUT = 180


# ------------------------------ 探测 ------------------------------
def codex_home() -> Path:
    """Codex 自己的配置目录（`CODEX_HOME` 是官方覆盖变量，测试也用它重定向）。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _search_dirs() -> list[Path]:
    """PATH 之外还值得找的地方（与 `ai_agents` 的探测思路同源，独立实现在纯标准库层）。"""
    home = Path.home()
    out = [home / ".codex" / "bin", home / ".local" / "bin", home / "bin"]
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            out.append(Path(appdata) / "npm")
    else:
        out += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return out


def _is_windows() -> bool:
    """单独一个函数：桌面版自带 CLI 的落点只在 Windows 上找，用例要能在 POSIX 上走这条分支。"""
    return os.name == "nt"


def find_codex() -> tuple[str | None, list[str]]:
    """找 `codex` 可执行文件。返回 (路径 or None, 找过哪些位置)。

    **找过哪些位置要如实报出来**：用户装在别处时，「找不到」这三个字什么忙都帮不上，
    而一串路径他一眼就能看出该往哪儿指（与 AI 桥的 `diagnostics.searched` 同一纪律）。
    """
    searched: list[str] = ["PATH"]
    hit = shutil.which("codex")
    if hit:
        return hit, searched
    for d in _search_dirs():
        exe = d / ("codex.cmd" if os.name == "nt" else "codex")
        searched.append(str(d))
        if exe.is_file() and os.access(exe, os.X_OK):
            return str(exe), searched
    if _is_windows():
        # 只装了 Codex 桌面版的机器上，这是唯一一份 codex（#722）
        # 候选与排序只有 `ai_agents.desktop_codex_candidates` 一份（AI 桥的 Codex 适配器
        # 用的也是它，#726）：两边对「这台机器上有没有 codex」的回答不许分叉。
        local = os.environ.get("LOCALAPPDATA")
        searched.append(ai_agents.desktop_codex_glob(local))
        for exe in ai_agents.desktop_codex_candidates(local):
            rc, _out = _run([exe, "--version"], timeout=60)
            if rc == 0:
                return exe, searched
    return None, searched


def _run(
    argv: list[str], timeout: int = _TIMEOUT, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """跑一条命令，回 (退出码, 合并输出)。绝不抛——失败也是一种结论。"""
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return 127, f"找不到可执行文件：{argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"超过 {timeout}s 没有返回：{' '.join(argv)}"
    except OSError as exc:
        return 126, f"{type(exc).__name__}: {exc}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def codex_env(codex: str) -> dict[str, str]:
    """起 `codex` 子进程用的环境：**把常见安装目录补进 PATH**（只补缺，不改顺序）。

    `find_codex()` 找到的是文件，能不能**跑**是另一件事：npm 装的 codex 是
    `#!/usr/bin/env node` 的脚本，`node` 得在**子进程的** PATH 上。桌面壳从 Finder
    启动时 PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`（本机 `ps -E` 实测），于是同一台
    机器上终端里全绿、设置页里「问不到」。与 AI 桥起 codex/claude CLI 用的是同一份
    `spawn_env`：CLI 自己所在目录 + Homebrew / npm 全局 / nvm / volta 等落点。
    """
    return ai_agents.spawn_env(codex)


def _codex_run(codex: str, args: list[str], timeout: int = _TIMEOUT) -> tuple[int, str]:
    """跑一条 `codex …`。**所有问 Codex 的地方都走这里**，没有裸的 `_run([codex, …])`。"""
    return _run([codex, *args], timeout=timeout, env=codex_env(codex))


#: `codex` 起不来时最常见的那一句：npm shim 在子进程里解析不到 node。
#: macOS / Linux 是 `env: node: No such file or directory`，Windows 的 .cmd 外壳是
#: `'node' is not recognized as an internal or external command`（中文系统是
#: 「不是内部或外部命令」）。认出它就把处方说出口，别让用户对着一句 env 报错猜。
_NODE_MISSING = re.compile(
    r"\bnode\b.*(No such file|not found|not recognized|不是内部或外部命令)", re.I
)


def _unknown_hint(detail: str, noun: str) -> str:
    """「问不到」那一档的处方。`noun` 是「登记」/「装」——两步的否定词不一样。"""
    tail = f"。这不等于没{noun}：是问不到，不是 Codex 答了「没有」。"
    if _NODE_MISSING.search(detail or ""):
        return (
            "。codex 是 npm 装的脚本，启动时要在 PATH 上找到 node；这次已经把常见安装目录"
            "（Homebrew、npm 全局、nvm、volta）补进 PATH 仍没找到——把 node 所在目录加进"
            " PATH（或用原生安装包装 Codex）后重试" + tail
        )
    return tail


#: 探测一个可执行文件是不是**真的能跑的 Python** 时让它回显的记号。
_PY_PROBE = "tavotto-python-ok"


def _last_json(text: str) -> dict | None:
    """输出里最后一行能解析成对象的 JSON（插件的 `--health` 就回这么一行）。"""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _json_output(text: str) -> dict | None:
    """`codex … --json` 的输出：整段 pretty-printed JSON（多行），前面可能混着 stderr。

    先整段解析；不行就从第一个 `{` 起解析；再不行退回「最后一行」（`--health` 那种）。
    """
    stripped = text.strip()
    for candidate in (stripped, stripped[stripped.find("{") :] if "{" in stripped else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return _last_json(text)


def _runs_python(candidate: str) -> bool:
    """`candidate` 是不是一个**跑得起来**的 Python——判据是跑一遍，不是 which。

    `shutil.which("python3")` 回答的是「PATH 里有没有这个名字」。Windows 上这两件
    事分得开：`%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python3.exe` 是微软商店的
    App Execution Alias，没装商店版 Python 时启动它既不是「找不到命令」也不是一个
    Python——它打开商店并回 9009（issue #172 的现场报告）。
    退出码才是真话，所以这里跑一遍并要回显记号。
    """
    # `-B`：只读探测，不往这个（多半是用户的）解释器的安装目录写 .pyc（`runtime.probe_args`）
    rc, out = _run([candidate, *probe_args(), "-c", f"print('{_PY_PROBE}')"], timeout=60)
    return rc == 0 and _PY_PROBE in out


def _health_env() -> dict[str, str]:
    """跑插件 `server.py --health` 用的环境：`PYTHONDONTWRITEBYTECODE=1`。

    `--health` 自己还会起孙进程（插件 resolver 逐个探候选解释器、问引擎版本）；命令行的 `-B` 不传给
    孙进程，环境变量传——只读体检的整棵进程树都不往被探的解释器里写 .pyc（Codex #717 P2）。
    `launcher_starts` 跑的是 `.mcp.json` 里那条命令本身（可能是启动脚本，塞不进 `-B`），只能靠它。"""
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def launcher_starts(command: str, server: Path) -> tuple[bool, str]:
    """`command` 能不能把插件的启动器跑起来——**Codex 起 MCP server 的那一跳**。

    这是本模块唯一有资格回答「这台机器上这条命令行不行」的判据，且它只信执行
    结果：跑 `<command> <plugin>/mcp/server.py --health`，要求输出里有一行能解析
    的体检 JSON。退出码 0（引擎可用）与 3（降级）都算**起得来**——降级 server 在
    Codex 里是有工具的（`tavotto_health` + 每个工具名的结构化错误），而起不来的
    表现是「插件启用了却一个工具都没有」，用户看不到任何线索。

    代价说清楚：命令若真是商店别名，跑这一次可能会弹一次商店窗口。那正是 Codex
    每次起 server 时已经在发生的事，这里花一次把它换掉。
    """
    try:
        p = subprocess.run(
            [command, str(server), "--health"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=_health_env(),
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return False, f"退出码 127，找不到可执行文件：{command}"
    except subprocess.TimeoutExpired:
        return False, "退出码 124，超过 120s 没有返回"
    except OSError as exc:
        return False, f"退出码 126，{type(exc).__name__}: {exc}"
    rc, out = p.returncode, (p.stdout or "")
    # stdout 必须**恰好**是一行体检 JSON：Codex 把 server 的 stdout 当协议流，第一帧前多出来的
    # 任何一行（0.17.0 的双语启动器让 cmd 回显 shebang；空行；别的 JSON 形状的调试输出）在真
    # Windows + Codex Desktop 上就是握手失败、零工具（#266）。只看「最后一行是 JSON」或「每行都是
    # JSON」都会把那种启动器判成起得来、不去钉（Codex #720）。
    lines = out.splitlines()
    healthy = [ln for ln in lines if "ok" in (_last_json(ln) or {})]
    if len(lines) == 1 and healthy:
        return True, f"退出码 {rc}，启动器回了体检 JSON"
    if healthy:
        others = [ln for ln in lines if ln not in healthy] or healthy[1:]
        return (
            False,
            f"退出码 {rc}，stdout 在体检 JSON 之外还有输出（Codex 会因此断连）："
            f"{(others[0][:160] or '（空行）')}",
        )
    tail = (out + (p.stderr or "")).strip()
    return False, f"退出码 {rc}，没有体检 JSON：{(tail[-160:] or '（零输出）')}"


def plugin_relative_command(
    plugin_dir: Path, command: str, *, windows: bool | None = None, pathext: str | None = None
) -> str:
    """`.mcp.json` 的 `command` → Codex 在这台机器上**实际会执行的那个文件**（#266）。

    `./` 开头的相对 command 按插件根（Codex 起 server 时的 `cwd`）解析，不按本进程的 cwd。
    Windows 上 Codex 的 program resolver 给无扩展名的路径按 PATHEXT 补扩展名、**先于**无扩展名
    的原名——真 Windows 11 + Codex Desktop（codex-cli 0.158）实测：`mcp/launch` 与
    `mcp/launch.cmd` 同在时 `./mcp/launch` 被解析成 `launch.cmd`。这里照做，否则会拿 POSIX 半边
    的 sh 脚本去 CreateProcess（「不是有效的 Win32 应用程序」），把一个好好的启动器判成起不来。
    """
    if not command.startswith("./"):
        return command
    path = plugin_dir / command[2:]
    if windows is None:
        windows = os.name == "nt"
    if windows and not path.suffix:
        exts = pathext if pathext is not None else os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        for ext in (e for e in exts.split(";") if e):
            candidate = path.with_name(path.name + ext.lower())
            if candidate.is_file():
                return str(candidate)
    return str(path)


def _is_our_plugin_dir(path: Path) -> bool:
    manifest = path / ".codex-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("name") == brand.CODEX_PLUGIN_NAME


def cached_plugin_dirs() -> list[Path]:
    """Codex 缓存里**我们这个 marketplace 名下**的插件副本（每个版本一个目录）。

    只看 `plugins/cache/<marketplace>/<plugin>/*`——别的 marketplace（用户自己的
    fork、指向工作副本的本地市场）里同名的 plugin.json 不是我们的对象。
    """
    base = (
        codex_home() / "plugins" / "cache" / brand.CODEX_MARKETPLACE_NAME / brand.CODEX_PLUGIN_NAME
    )
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and _is_our_plugin_dir(p))


def _marketplace_state(codex: str) -> dict:
    """`codex plugin marketplace list` 里我们这条的**状态**（不是布尔）。

    三档：`registered` / `absent` / `unknown`——命令本身失败时是 unknown，不是 absent，
    否则 install 会对着一个答不上来的问题跑 `marketplace add`。优先 `--json`（能读到
    来源类型与快照根目录）；老客户端没有 `--json` 时退回文本表，按 MARKETPLACE 列
    **整列相等**判（ROOT 列是路径，里面出现 tavotto 太容易了）。
    """
    rc, out = _codex_run(codex, ["plugin", "marketplace", "list", "--json"])
    data = _json_output(out) if rc == 0 else None
    if isinstance(data, dict) and isinstance(data.get("marketplaces"), list):
        for entry in data["marketplaces"]:
            if not isinstance(entry, dict) or entry.get("name") != brand.CODEX_MARKETPLACE_NAME:
                continue
            src = entry.get("marketplaceSource") or {}
            return {
                "state": "registered",
                "source_type": src.get("sourceType"),
                "source": src.get("source"),
                "root": entry.get("root"),
            }
        return {"state": "absent", "source_type": None, "source": None, "root": None}
    rc, out = _codex_run(codex, ["plugin", "marketplace", "list"])
    if rc != 0:
        return {
            "state": "unknown",
            "detail": out[-300:],
            "source_type": None,
            "source": None,
            "root": None,
        }
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == brand.CODEX_MARKETPLACE_NAME:
            root = line[len(parts[0]) :].strip() or None
            return {"state": "registered", "source_type": None, "source": None, "root": root}
    return {"state": "absent", "source_type": None, "source": None, "root": None}


def _plugin_state(codex: str) -> dict:
    """`codex plugin list -m tavotto` 里我们这条的状态：`installed` / `available` /
    `absent` / `unknown`，加版本、来源与（文本表里的）安装路径。

    坑（Codex 在 PR #169 上指出）：marketplace 加好但插件还没装时，`plugin list`
    **照样会列出** `tavotto@tavotto`，只是 STATUS 是「not installed」——那是 `available`，
    不是 `installed`。列都不列（`absent`）又是另一件事：这个客户端不认识市场清单里的
    来源类型，或快照太旧。
    """
    state: dict = {
        "state": "unknown",
        "version": None,
        "enabled": None,
        "source": None,
        "path": None,
    }
    rc, out = _codex_run(codex, ["plugin", "list", "-m", brand.CODEX_MARKETPLACE_NAME, "--json"])
    data = _json_output(out) if rc == 0 else None
    if isinstance(data, dict) and isinstance(data.get("installed"), list):
        hit = None
        for entry in data.get("installed", []) + data.get("available", []):
            if isinstance(entry, dict) and entry.get("pluginId") == brand.CODEX_PLUGIN_REF:
                hit = entry
                break
        if hit is None:
            state["state"] = "absent"
        else:
            state["state"] = "installed" if hit.get("installed") else "available"
            state["version"] = hit.get("version")
            state["enabled"] = hit.get("enabled")
            state["source"] = hit.get("source")
    rc, out = _codex_run(codex, ["plugin", "list", "-m", brand.CODEX_MARKETPLACE_NAME])
    if rc != 0:
        if state["state"] == "unknown":
            state["detail"] = out[-300:]
        return state
    path_col = None
    for line in out.splitlines():
        if line.startswith("PLUGIN") and "PATH" in line:
            path_col = line.index("PATH")
            continue
        parts = line.split()
        if not parts or parts[0] != brand.CODEX_PLUGIN_REF:
            continue
        status = line[len(parts[0]) :].strip().lower()
        # JSON 说「没有」不作数，文本表列出来了就以它为准：codex 0.157 的 `plugin list --json`
        # 对**还没装**的插件 installed / available 两个数组都是空的，文本表却列着
        # `tavotto@tavotto  not installed`（git 市场与本地市场都这样，真 CLI 实测，#722）。
        # 以前 JSON 的「absent」一锤定音，全新机器上 install 停在 plugin_source_unsupported。
        if state["state"] in ("unknown", "absent"):
            state["state"] = "installed" if status.startswith("installed") else "available"
        if path_col is not None and len(line) > path_col:
            state["path"] = line[path_col:].strip() or None
        if state["version"] is None and len(parts) >= 4:
            # 文本表：PLUGIN STATUS VERSION PATH（STATUS 可能是「installed, enabled」）
            for token in parts[1:]:
                if re.fullmatch(r"\d+\.\d+\.\d+", token):
                    state["version"] = token
                    break
        break
    else:
        if state["state"] == "unknown":
            state["state"] = "absent"
    return state


def locate_installed_plugin(state: dict) -> tuple[Path | None, str, str]:
    """已装副本在哪：(目录, 说明, 错误码)。

    先认缓存里 Codex 报的那个版本（`cache/<marketplace>/<plugin>/<版本>`）——**本地来源也一样**：
    codex 0.157 的 `plugin add` 把本地市场里的插件复制进缓存、从缓存起 server（"Installed plugin
    root: …/plugins/cache/tavotto/tavotto/0.17.0"），`plugin list` 的 `source.path` 仍报来源目录。
    钉、体检都得落在缓存那份上，否则 command 钉在来源、Codex 照旧起缓存里没改过的启动器（Codex #725 P1）。
    缓存里没有那个版本（老客户端直接从来源加载）才信报的路径；再报不出来时缓存里**恰好一个**才认。
    多个版本并存（升级后旧缓存还在）时不按最高版本号猜：Codex 用哪个由它说了算，报歧义并把候选列出来。
    """
    cached = cached_plugin_dirs()
    version = state.get("version")
    # 1. 缓存里 Codex 报的那个版本（git / npm / 本地来源都装进这里；目录名 == 版本号，codex 0.151/0.157 实测）
    if version:
        by_version = [p for p in cached if p.name == version]
        if len(by_version) == 1:
            return by_version[0], f"Codex 启用的版本 {version}：{by_version[0]}", ""
    # 2. 缓存里没有：报的路径确实是一份插件目录时才信（**git 来源时它是来源描述**，
    #    `file://…, path \`codex-plugin\`, ref …`，codex 0.151 实测，不是路径）
    reported = state.get("path")
    if reported:
        p = Path(reported)
        if p.is_dir() and _is_our_plugin_dir(p):
            return p, f"Codex 报的安装路径：{p}", ""
    # 3. 版本也报不出来：缓存里恰好一份才认；零份或多份都是歧义，不猜
    if len(cached) == 1:
        return cached[0], f"缓存里唯一一份：{cached[0]}", ""
    if not cached:
        return (
            None,
            "Codex 没报出能定位的安装路径或版本，缓存里也没有 tavotto 插件"
            + (f"（PATH 列：{reported}）" if reported else ""),
            ERR_PLUGIN_AMBIGUOUS,
        )
    return (
        None,
        "Codex 没报出能定位的安装路径或版本，缓存里有多份同名插件，不按版本号或时间猜："
        + "、".join(str(p) for p in cached),
        ERR_PLUGIN_AMBIGUOUS,
    )


def installed_plugin_dir() -> Path | None:
    """兼容入口：缓存里**唯一**的一份；零份或多份都回 None（歧义不猜）。"""
    cached = cached_plugin_dirs()
    return cached[0] if len(cached) == 1 else None


def plugin_channel(marketplace_root: str | None) -> dict:
    """marketplace 快照里 tavotto 条目的来源形状 → 这份安装走的是哪条通道。

    * `stable`：`git-subdir` 指向官方仓库的发行分支（ADR 0043 的目标形态）；
    * `stable-archive`：发行分支的压缩包解出来的本地市场（没有 git 时的装法，#722）——
      `local ./codex-plugin`，且根上有发行分支的收据；升级走 `tavotto codex upgrade`；
    * `legacy-local`：`local ./codex-plugin`（把仓库本体当插件装，画布靠版本库里那份）；
    * `custom`：别的仓库 / 别的 ref / 本地工作副本——用户自己的选择，不改；
    * `unknown`：读不到快照。
    """
    if not marketplace_root:
        return {"channel": "unknown", "source": None}
    p = Path(marketplace_root) / ".agents" / "plugins" / "marketplace.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        entry = next(e for e in data.get("plugins", []) if e.get("name") == brand.CODEX_PLUGIN_NAME)
    except (OSError, ValueError, StopIteration, AttributeError):
        return {"channel": "unknown", "source": None}
    src = entry.get("source")
    if isinstance(src, str):
        src = {"source": "local", "path": src}
    if not isinstance(src, dict):
        return {"channel": "unknown", "source": None}
    kind = src.get("source")
    if kind == "local":
        legacy = src.get("path") in (f"./{brand.CODEX_PLUGIN_SUBDIR}", brand.CODEX_PLUGIN_SUBDIR)
        if legacy and _stable_receipt(Path(marketplace_root)) is not None:
            return {"channel": "stable-archive", "source": src}
        return {"channel": "legacy-local" if legacy else "custom", "source": src}
    if (
        kind == "git-subdir"
        and src.get("url") in (brand.CODEX_PLUGIN_SOURCE_URL, brand.REPO_URL)
        and src.get("ref") == brand.CODEX_PLUGIN_STABLE_BRANCH
    ):
        return {"channel": "stable", "source": src}
    return {"channel": "custom", "source": src}


# ------------------- 没有 git：发行分支压缩包 → 本地市场（#722） -------------------
#: Codex 没能**起** git 时的原文（codex-rs：`failed to run git <参数>: <系统错误>`；Windows 上
#: 系统错误是 `program not found`，macOS 是 `No such file or directory (os error 2)`，两台真机
#: 实测）。git 起来了但克隆失败（没网、仓库不对）不是这一句，那种失败原样报，不改道下载。
_GIT_NOT_RUNNABLE = re.compile(r"failed to run git\b", re.I)
#: 压缩包上限。0.17.0 的分支压缩包约 0.7 MB；上限只防「下来的不是那个东西」把磁盘写满。
_ARCHIVE_MAX_BYTES = 64 << 20
_ARCHIVE_MAX_UNPACKED = 256 << 20
#: 条目数上限：发行分支只有几十个文件（0.17.0 是 36 个），上限只防空文件铺满磁盘
_ARCHIVE_MAX_ENTRIES = 5_000
_NETWORK_TIMEOUT = 60


class ArchiveError(Exception):
    """压缩包这条路上任何一步不成立：下载、解包、核对、换目录。消息是给人看的整句。"""


def archive_marketplace_dir() -> Path:
    """`tavotto codex install` 管理的本地市场目录（Tavotto 自己的数据目录下，不在 `~/.codex`）。"""
    return config.data_path("codex-marketplace", brand.CODEX_PLUGIN_STABLE_ARCHIVE_DIR)


def _fetch(url: str, *, limit: int) -> bytes:
    """GET 一个 https 地址，最多 `limit` 字节。失败抛 ArchiveError（带地址与原因）。"""
    from .. import __version__

    req = urllib.request.Request(
        url, headers={"User-Agent": f"{brand.PRODUCT_NAME}/{__version__} codex-install"}
    )
    # 出站 HTTPS 一律经 `tlstrust`（平台原生校验；#711 / #714 的规矩）：干净 Windows 缺根证书时 OpenSSL
    # 的默认信任库会把 GitHub 的压缩包下载报成网络失败
    opener = urllib.request.build_opener(tlstrust.https_handler(tlstrust.client_context()))
    try:
        with opener.open(req, timeout=_NETWORK_TIMEOUT) as resp:
            data = resp.read(limit + 1)
    except (
        urllib.error.URLError,
        http.client.HTTPException,  # 分块响应被截断 / 畸形：IncompleteRead 不是 OSError（Codex #725）
        TimeoutError,
        OSError,
        ValueError,
    ) as exc:
        raise ArchiveError(f"下载 {url} 失败：{exc}") from exc
    if len(data) > limit:
        raise ArchiveError(f"{url} 超过 {limit} 字节，不像是发行分支的压缩包")
    return data


def _unpack(data: bytes, into: Path) -> tuple[Path, str | None, dict[str, str]]:
    """把 GitHub 源码压缩包解进 `into`，回 (唯一顶层目录, zip 注释里的提交 SHA, 各文件的 git 模式)。

    先整份检查、再写盘：绝对路径、`..`、盘符、符号链接、多个顶层目录、解开后过大，任何一条
    不成立就一个字节都不写。可执行位按 zip 里记的 git 模式恢复（POSIX 上 `mcp/launch` 要 755，
    随包清单会核对它）。模式按顶层目录下的相对路径回（`100755` / `100644`），交给
    `_modes_match_manifest` 与随包清单逐条比。
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ArchiveError(f"下载下来的不是 zip：{exc}") from exc
    if len(infos) > _ARCHIVE_MAX_ENTRIES:
        # 只按字节算的上限挡不住几十万个空文件（Codex #725）：条目数也设上限，写盘之前就拒
        raise ArchiveError(f"压缩包条目数 {len(infos)} 超过 {_ARCHIVE_MAX_ENTRIES}，不像是发行分支")
    tops: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if (
            not parts
            or name.startswith("/")
            or "\\" in name
            or ".." in parts
            or ":" in name
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise ArchiveError(f"压缩包里有不该有的条目：{name!r}")
        total += info.file_size
        tops.add(parts[0])
    if total > _ARCHIVE_MAX_UNPACKED:
        raise ArchiveError(f"压缩包解开后超过 {_ARCHIVE_MAX_UNPACKED} 字节")
    if len(tops) != 1:
        raise ArchiveError(f"压缩包顶层应当恰好一个目录，实际是：{sorted(tops)}")
    modes: dict[str, str] = {}
    for info in infos:
        parts = PurePosixPath(info.filename).parts
        target = into.joinpath(*parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = zf.read(info)
        except (
            zipfile.BadZipFile,
            zlib.error,
            lzma.LZMAError,  # ZIP_LZMA 的条目坏了（Codex #725）
            OSError,  # bz2 的坏数据报 OSError
            EOFError,
            NotImplementedError,
            RuntimeError,
        ) as exc:
            # 目录表完好、条目本身坏了（CRC 不符 / 截断 / 加密 / 不认识的压缩法）：同样是「这份压缩包
            # 不能用」，走 ArchiveError 的一行 JSON 失败，不让 traceback 逃出安装流程（Codex #725）
            raise ArchiveError(f"压缩包里的 {info.filename!r} 读不出来：{exc}") from exc
        target.write_bytes(payload)
        executable = bool((info.external_attr >> 16) & 0o111)
        modes["/".join(parts[1:])] = "100755" if executable else "100644"
        if os.name != "nt" and executable:
            target.chmod(0o755)
    comment = zf.comment.decode("ascii", "replace").strip()
    commit = comment if re.fullmatch(r"[0-9a-f]{40}", comment) else None
    return into / tops.pop(), commit, modes


def _modes_match_manifest(top: Path, modes: dict[str, str]) -> None:
    """压缩包里记的 git 模式要等于随包清单逐文件声明的模式；不等抛 ArchiveError。

    `verify_dir` 按清单自己声明的模式重算 content_digest，不看解出来的文件是什么模式——
    只动模式（例如 `mcp/launch` 在压缩包里是 100644、清单仍写 100755）的一份会整份通过，
    装上的启动器却不可执行（Codex #725）。比的是压缩包里的记录而不是磁盘上的权限位：
    Windows 没有可执行位，按磁盘比就只在 POSIX 上管用。"""
    manifest = pluginmanifest.read_manifest(top / brand.CODEX_PLUGIN_SUBDIR)
    prefix = f"{brand.CODEX_PLUGIN_SUBDIR}/"
    drift = [
        f"{e['path']}（清单 {e['mode']}，压缩包 {modes.get(prefix + e['path'])}）"
        for e in (manifest or {}).get("files", [])
        if modes.get(prefix + e["path"]) != e["mode"]
    ]
    if drift:
        raise ArchiveError("插件文件的模式与随包清单对不上：" + "；".join(drift)[:600])


def _stable_receipt(root: Path) -> dict | None:
    """`root` 上发行分支的收据（`plugin-release.json`）——是本渠道的那份才回，否则 None。"""
    try:
        data = json.loads((root / pluginmanifest.RELEASE_RECEIPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("branch") != brand.CODEX_PLUGIN_STABLE_BRANCH
        or data.get("plugin") != brand.CODEX_PLUGIN_NAME
    ):
        return None
    return data


def verify_stable_snapshot(root: Path) -> dict:
    """一份解开的发行分支是不是**完整、自洽的那次发行**。回收据；不成立抛 ArchiveError。

    三层，各答各的问题：
    1. 市场清单：名字是 `tavotto`、插件条目是 `local ./codex-plugin`——Codex 会照它装；
    2. 收据：是 `plugin-stable` 上 `tavotto` 的收据，带版本、content_digest、release tag；
    3. 插件目录：按随包清单逐文件核对 sha256 / 模式 / 不多不少（`pluginmanifest.verify_dir`，
       与 `tavotto codex doctor` 体检已装副本是同一份实现），版本与 content_digest 都要等于收据。
    与 release 附件的交叉核对要联网，在 `fetch_stable_snapshot` 里做。
    """
    try:
        mk = json.loads(
            (root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        entry = next(e for e in mk.get("plugins", []) if e.get("name") == brand.CODEX_PLUGIN_NAME)
    except (OSError, ValueError, StopIteration, AttributeError, TypeError) as exc:
        # TypeError：`plugins` 是 null 之类不可迭代的值（Codex #725）
        raise ArchiveError(f"压缩包里没有可用的市场清单：{exc!r}") from exc
    src = entry.get("source") if isinstance(entry, dict) else None
    if (
        mk.get("name") != brand.CODEX_MARKETPLACE_NAME
        or not isinstance(src, dict)
        or src.get("source") != "local"
        or src.get("path") != f"./{brand.CODEX_PLUGIN_SUBDIR}"
    ):
        raise ArchiveError(f"压缩包里的市场清单不是发行分支的形状：{mk.get('name')!r} / {src!r}")
    receipt = _stable_receipt(root)
    if receipt is None:
        raise ArchiveError(
            f"压缩包根上没有 {brand.CODEX_PLUGIN_STABLE_BRANCH} 的收据 {pluginmanifest.RELEASE_RECEIPT}"
        )
    for key in ("version", "content_digest", "release_tag"):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise ArchiveError(f"收据 {pluginmanifest.RELEASE_RECEIPT} 缺 {key}")
    problems = pluginmanifest.verify_dir(
        root / brand.CODEX_PLUGIN_SUBDIR,
        version=receipt["version"],
        expect_content_digest=receipt["content_digest"],
        # 要的是「与发出去的那份逐字节一致」；发行件 command 的形态规矩归发布那一刻
        command_policy=False,
    )
    if problems:
        raise ArchiveError("插件与随包清单对不上：" + "；".join(problems)[:600])
    return receipt


#: 本地市场目录旁的锁文件：同一时刻只许一个 install / upgrade 动一次性目录与换目录。
_MARKETPLACE_LOCK = ".tavotto-marketplace.lock"


class _MarketplaceBusy(Exception):
    """另一个进程正持有本地市场的锁。"""


@contextlib.contextmanager
def _marketplace_lock(base: Path):
    """本地市场的**内核**排他锁（非阻塞）：两个 install / upgrade 重叠（设置页一次、终端一次）时，
    后来的那个不能把前一个正在解的 `.staging-*`、正在换的 `.old-*` 当残留收掉（Codex #725）。
    用操作系统文件锁（POSIX `flock`、Windows `msvcrt.locking` 锁第 0 字节）：持有者崩溃或被杀，
    内核在它退出时释放，不会留下过期锁——与插件重装锁同一个做法（codex-plugin/mcp/server.py
    `_try_lock_fd`）。别人持有时抛 `_MarketplaceBusy`；锁子系统自己的错照常抛 OSError。"""
    base.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(base / _MARKETPLACE_LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)):
                    raise _MarketplaceBusy() from exc
                raise
            try:
                yield
            finally:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    raise _MarketplaceBusy() from exc
                raise
            yield  # 关 fd 即释放 flock
    finally:
        os.close(fd)


def fetch_stable_snapshot() -> dict:
    """持本地市场的锁跑 `_fetch_stable_snapshot_locked`；另一个 install / upgrade 正在动它时直接失败。"""
    base = archive_marketplace_dir().parent
    try:
        with _marketplace_lock(base):
            return _fetch_stable_snapshot_locked()
    except _MarketplaceBusy:
        raise ArchiveError(
            f"另一个 tavotto codex install / upgrade 正在更新本地市场 {base}：等它结束后重跑本命令"
        ) from None
    except OSError as exc:
        raise ArchiveError(f"拿不到本地市场 {base} 的锁：{exc}") from exc


def _fetch_stable_snapshot_locked() -> dict:
    """下载发行分支压缩包 → 核对 → 换进 `archive_marketplace_dir()`。回这次装的是什么。

    核对全过之前，目标目录一个字节都不动：解在同级的 staging 里，最后一步才 `os.replace`
    换进去（旧目录先挪开，换失败就挪回来）。回的 `changed` 说内容与原来那份是否不同——
    `tavotto codex upgrade` 据此决定要不要让 Codex 重装。
    """
    dest = archive_marketplace_dir()
    base = dest.parent
    leftovers: list[str] = []
    try:
        base.mkdir(parents=True, exist_ok=True)
        # 上一次删不掉的一次性目录（杀软占着文件等）先收掉：泄漏不跨次累积（Codex #725）；
        # 上一次在两次 replace 之间被杀、只剩备份时先挪回去，不当垃圾删
        leftovers += _remove_one_shot_dirs(base, dest)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=base))
    except OSError as exc:
        raise ArchiveError(f"建不了本地市场目录 {base}：{exc}") from exc
    try:
        data = _fetch(brand.CODEX_PLUGIN_STABLE_ARCHIVE_URL, limit=_ARCHIVE_MAX_BYTES)
        try:
            top, commit, modes = _unpack(data, staging)
        except OSError as exc:
            raise ArchiveError(f"解包写不进 {staging}：{exc}") from exc
        receipt = verify_stable_snapshot(top)
        _modes_match_manifest(top, modes)
        tag = receipt["release_tag"]
        asset_url = f"{brand.RELEASES_URL}/download/{tag}/{brand.CODEX_PLUGIN_BUILD_ASSET}"
        try:
            asset = json.loads(_fetch(asset_url, limit=8 << 20).decode("utf-8"))
            asset_digest = asset.get("content_digest") if isinstance(asset, dict) else None
        except (ValueError, UnicodeDecodeError) as exc:
            raise ArchiveError(f"{asset_url} 不是合法 JSON：{exc}") from exc
        if asset_digest != receipt["content_digest"]:
            raise ArchiveError(
                f"发行分支的 content_digest（{receipt['content_digest'][:12]}…）与 {tag} 附带的"
                f"构建清单（{str(asset_digest)[:12]}…）对不上——不装一份来历对不上的插件"
            )
        before = _stable_receipt(dest)
        # 挪开旧目录用一个**新建即独占**的名字：不预先删同名目录（删不掉会被吞掉、随后的
        # os.replace 撞上它失败——PID 复用、杀软占着文件；Codex #725）。mkdtemp 建的是空目录，
        # Windows 上 os.replace 不能盖目录，先把它删掉再用这个名字
        try:
            old = Path(tempfile.mkdtemp(prefix=".old-", dir=base))
            old.rmdir()
            if dest.exists():
                os.replace(dest, old)
            try:
                os.replace(top, dest)
            except OSError:
                if old.exists() and not dest.exists():
                    os.replace(old, dest)
                raise
        except OSError as exc:
            raise ArchiveError(f"换不进 {dest}：{exc}") from exc
    except (ArchiveError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        # 失败这一路也要把删不掉的一次性目录说出来——不然只有成功时才报，失败反复发生时会静默堆积；
        # 核对阶段的读写失败（杀软临时拒读解出来的文件）同样是一行 JSON 失败，不是 traceback；
        # 压缩包是外来输入，里面 JSON 的形状不对（该是列表 / 对象的地方是 null）引出的 TypeError 等
        # 也归到这里——逐个字段补校验补不完（Codex #725）
        stuck = _remove_one_shot_dirs(base, dest)
        if isinstance(exc, ArchiveError):
            msg = str(exc)
        elif isinstance(exc, OSError):
            msg = f"核对或换进 {dest} 时读写失败：{exc}"
        else:
            msg = f"压缩包内容的形状不对，核对不下去：{exc!r}"
        if stuck:
            msg += "；另有删不掉的一次性目录（下次运行开头再收）：" + "、".join(stuck)
        raise ArchiveError(msg) from exc
    except BaseException:
        _remove_one_shot_dirs(base, dest)
        raise
    # 一次性目录用完就删，删不掉**说出来**（回在 `leftovers` 里、marketplace 一步照报），下一次开头再收
    leftovers += _remove_one_shot_dirs(base, dest)
    return {
        "root": str(dest),
        "commit": commit,
        "version": receipt["version"],
        "content_digest": receipt["content_digest"],
        "release_tag": tag,
        "changed": (before or {}).get("content_digest") != receipt["content_digest"],
        "leftovers": sorted(set(leftovers)),
    }


def _remove_one_shot_dirs(base: Path, dest: Path) -> list[str]:
    """删掉 `base` 下的一次性目录（`.staging-*` / `.old-*`）；回删不掉的那些（路径）。

    `dest` 不在而有 `.old-*` 时，那是上一次在「旧目录挪开」与「新目录换进」之间被杀（或换进失败、
    挪回也失败）留下的**唯一一份**已核对的市场：先把最新的那份挪回 `dest`，再删其余的——不然离线
    重试会把能恢复的安装删掉，Codex 登记的目录从此不在（Codex #725）。"""
    stuck: list[str] = []
    try:
        entries = [p for p in base.iterdir() if p.name.startswith((".staging-", ".old-"))]
    except FileNotFoundError:
        return stuck
    except OSError:
        # 列不出来 ≠ 收干净了：一个都没看就报「没有残留」会让调用方说成功（Codex #725）
        return [str(base)]

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    backups = sorted((p for p in entries if p.name.startswith(".old-")), key=mtime)
    if backups and not dest.exists():
        # 挪回失败时这份备份仍是唯一能恢复的那份：不进下面的删除，报成没收干净（Codex #725）
        keep = backups[-1]
        entries.remove(keep)
        try:
            os.replace(keep, dest)
        except OSError:
            stuck.append(str(keep))
    for p in entries:
        try:
            shutil.rmtree(p)
        except FileNotFoundError:
            continue
        except OSError:
            stuck.append(str(p))
    return stuck


def _installed_lags_archive(st: dict, archive: dict | None) -> bool:
    """压缩包渠道：Codex 里装着的版本与本地市场收据的版本对不上。

    按**版本**判，不按已装副本的 content_digest：`codex plugin list` 报的路径是来源（本地市场里那份，
    已经换成新的），不一定是 Codex 缓存里真装着的那份；而插件版本与引擎版本同号、同一版本号不重发，
    版本不同就是没装上。任一侧读不到版本时不判（回 False），不在每次命令里无条件重装。"""
    if not archive:
        return False
    have, want = st.get("version"), archive.get("version")
    return bool(have) and bool(want) and have != want


def _describe_snapshot(info: dict) -> str:
    commit = (info.get("commit") or "?")[:12]
    return (
        f"发行分支 {brand.CODEX_PLUGIN_STABLE_BRANCH} 的压缩包（提交 {commit}，插件 {info['version']}，"
        f"已按随包清单逐文件核对，content_digest 与 {info['release_tag']} 附带的构建清单一致），"
        f"本地市场目录 {info['root']}"
        + (
            # 一次性目录删不掉（多半被杀毒软件占着）时说出来，下次运行开头再收（Codex #725）
            "；这些一次性目录没删掉，下次运行会再清：" + "、".join(info["leftovers"])
            if info.get("leftovers")
            else ""
        )
    )


_ARCHIVE_MANUAL = (
    "。也可以手动装：从 "
    + brand.CODEX_PLUGIN_STABLE_ARCHIVE_URL
    + " 下载压缩包、解压，然后 `codex plugin marketplace add <解压出的 "
    + brand.CODEX_PLUGIN_STABLE_ARCHIVE_DIR
    + " 目录>` 与 `codex plugin add "
    + brand.CODEX_PLUGIN_REF
    + "`（README「在 Codex 中第一次使用 Tavotto」有 Windows 上的逐行步骤）"
)


def _archive_marketplace_step(codex: str, *, why: str, summary: dict) -> dict:
    """git 起不来时的 marketplace 步：下载 → 核对 → 登记本地市场。"""
    try:
        info = fetch_stable_snapshot()
    except ArchiveError as exc:
        return _step(
            "marketplace",
            ok=False,
            code=ERR_MARKETPLACE,
            detail=f"Codex 起不来 git（{why}），改从 GitHub 下载发行分支压缩包也没成：{exc}"
            + _ARCHIVE_MANUAL,
        )
    summary["archive"] = info
    rc, out = _codex_run(codex, ["plugin", "marketplace", "add", info["root"]])
    if rc != 0:
        return _step(
            "marketplace",
            ok=False,
            code=ERR_MARKETPLACE,
            detail=f"压缩包已下载并核对，但 Codex 没能把它登记成本地市场：{out[-400:]}",
        )
    mk = _marketplace_state(codex)
    summary["marketplace"].update(
        {
            "registered": mk["state"] == "registered",
            "state": mk["state"],
            "source_type": mk.get("source_type"),
            "source": mk.get("source"),
            "root": mk.get("root"),
        }
    )
    summary["channel"] = plugin_channel(mk.get("root"))
    return _step(
        "marketplace",
        ok=True,
        detail="本机没有能用的 git，已改用" + _describe_snapshot(info),
    )


def plugin_python() -> str | None:
    """跑插件脚本（`--health` / `--provision`）该用哪个解释器。

    **不能无脑用 `sys.executable`。** 桌面版的 `tavotto-cli` 是 PyInstaller 冻结出来
    的可执行文件：把它当 python 使（`<tavotto-cli> server.py --health`）只会被
    `packaging/entry.py` 当成 Tavotto 的命令行参数解析掉，插件脚本根本不会跑——
    插件自己的 `server.py` 也明写着「冻结的 CLI 不能当解释器」。

    冻结形态下退回 PATH 上的真 python；`TAVOTTO_MCP_PYTHON` 优先（那是插件自己
    认的覆盖变量，用户指过就该听他的）。找不到就回 None——**说清楚比装作能跑好**。

    PATH 上的候选**要跑过才算数**（`_runs_python`）：`shutil.which()` 回答的是
    「PATH 里有没有这个名字」，在 Windows 上那答不了「能不能跑」——见 issue #172。
    """
    override = os.environ.get("TAVOTTO_MCP_PYTHON")
    if override and Path(override).is_file():
        return override
    if not getattr(sys, "frozen", False):
        return sys.executable
    # `py` 排在最后：Windows 的 Python Launcher（`C:\Windows\py.exe`）不是商店
    # 别名，往往是这台机器上最稳的一个入口；POSIX 上 which 通常直接回 None，
    # 万一有个同名的别的东西，`_runs_python` 会把它挡掉——不靠平台分支，靠跑一遍。
    for name in ("python3", "python", "py"):
        hit = shutil.which(name)
        if hit and _runs_python(hit):
            return hit
    return None


def engine_importable() -> bool:
    """这个解释器能不能 `import tavotto.engine`——pip/pipx 形态天然满足。"""
    try:
        import tavotto.engine  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------- 启动命令：钉到一个真能跑的解释器 ----------------------
def _yaml_scalar(value: str) -> str:
    """写进 YAML 的纯量。带空格的绝对路径 plain scalar 容得下，但 `#`、`: `、
    引号与开头的指示符会把它变成别的东西——那时候加单引号。"""
    risky = (
        not value
        or value != value.strip()
        or value[:1] in "-?:,[]{}#&*!|>'\"%@`"
        or " #" in value
        or ": " in value
    )
    if risky:
        return "'" + value.replace("'", "''") + "'"
    return value


def _replace_dependency_command(text: str, command: str) -> str:
    """把 `openai.yaml` 的 `dependencies.tools[].command` 换成 `command`。

    只在 `dependencies:` 块里换——`interface:` 与 `policy:` 一个字都不许动。

    **逐行扫，不用跨行正则。** 原来那版是 `^dependencies:\n…`（`re.M | re.S`），
    在 CRLF 行尾下**永不匹配**：`^dependencies:` 后面是 `\r` 不是 `\n`，于是整个
    函数静默返回原文——`.mcp.json` 钉上了、`openai.yaml` 没动，正是本改动要避免的
    半套状态，而且连错都不报。Git for Windows 默认 `core.autocrlf=true`，Codex 用
    git sparse-checkout 拉插件，用户机器上那份大概率就是 CRLF（这条是在合并队列的
    windows-latest 腿上第一次现形的）。每行的行尾原样带回去，不把文件改成混合行尾。
    """
    out: list[str] = []
    in_deps = False
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        eol = line[len(body) :]
        if re.match(r"\w", body):  # 顶格的 key = 新的顶层块（空行与注释不算）
            in_deps = body.startswith("dependencies:")
        elif in_deps:
            m = re.match(r"(\s*command:\s*)", body)
            if m:
                body = m.group(1) + _yaml_scalar(command)
        out.append(body + eol)
    return "".join(out)


def _resolved(path: Path) -> Path:
    """跟着符号链接走到真正那个文件。

    `os.replace()` 换的是**路径本身**：目标是个符号链接时，替换掉的是链接、而不是
    它指向的文件——旧内容原封不动留在那头，工程结构还被改了（PR #254 上因此吃过
    一条 P1）。这里先解析，tmp 也落在解析后的同一个目录里（跨设备 rename 不原子）。
    """
    return Path(os.path.realpath(path))


def _pin_plan(plugin_dir: Path, command: str) -> list[tuple[Path, bytes, bytes, str]]:
    """算出要落的两份内容——**全在内存里**，这一步失败磁盘一个字节都没碰过。"""
    plan: list[tuple[Path, bytes, bytes, str]] = []
    mcp_path = plugin_dir / pluginmanifest.mcp_config_rel(plugin_dir)
    old = mcp_path.read_bytes()
    data = json.loads(old.decode("utf-8"))
    for entry in data.get("mcpServers", {}).values():
        if isinstance(entry, dict) and "command" in entry:
            entry["command"] = command
    new = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    plan.append((_resolved(mcp_path), old, new, mcp_path.name))
    for yaml_path in sorted(plugin_dir.glob("skills/*/agents/openai.yaml")):
        old = yaml_path.read_bytes()
        new_text = _replace_dependency_command(old.decode("utf-8"), command)
        # **换没换上去要当场验，别信正则**：这一行原本静默失配了整整一个平台。
        # 用一条与上面那个扫描器无关的判据——目标行必须真的出现在文件里。
        wanted = "command: " + _yaml_scalar(command)
        if not any(ln.strip() == wanted for ln in new_text.splitlines()):
            raise atomicio.AtomicWriteError(
                "write_failed",
                f"{yaml_path.name} 里的依赖 command 没能换成 {command}（同源对会只剩一侧）",
                yaml_path,
            )
        new = new_text.encode("utf-8")
        if new != old:
            plan.append(
                (_resolved(yaml_path), old, new, yaml_path.relative_to(plugin_dir).as_posix())
            )
    return plan


def pin_launcher_command(plugin_dir: Path, command: str) -> list[str]:
    """把**已装副本**的启动命令钉到 `command`，`.mcp.json` 与 `openai.yaml` 一起改。

    这两处是根 `AGENTS.md` 列的严格同源对：Codex 的 stdio 依赖按 `command` 做规范键
    匹配，只改一侧的话技能声明的依赖对不上插件自带的 server，Codex 会把它当成「还没
    装」——用户每装一次就被告知一次没装。改的是**已装副本**，仓库里那份不动。

    **「一起改」必须是事务性的，不只是「两条写在一起」**：一份落了、另一份抛了，留下
    的正是上面那个坏状态。所以分两段——

    1. 两份新内容全在内存里算好，各写成同目录 tmp（序列化错、磁盘满、权限不足都在
       这一段暴露，此时磁盘上两份原文一字未动）；
    2. 连续 `publish_file` 换上去。第 k 份换失败时，把已经换掉的前 k-1 份按原字节
       写回去，再抛 `AtomicWriteError`。

    落盘一律走 `engine/atomicio`（ADR 0023：文档类写入只有一份实现），**不在这里写
    第二份 tmp+replace**。
    """
    plan = _pin_plan(plugin_dir, command)
    staged: list[tuple[Path, Path]] = []
    try:
        for dest, _old, new, _label in plan:
            tmp = dest.with_name(f"{dest.name}.{os.getpid()}.pin.tmp")
            with open(tmp, "wb") as fh:
                fh.write(new)
            staged.append((tmp, dest))
    except OSError as exc:
        for tmp, _dest in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise atomicio.AtomicWriteError("write_failed", f"临时文件写不出来：{exc}", plan[0][0])

    done: list[tuple[Path, bytes]] = []
    for (tmp, dest), (_d, old, _new, _label) in zip(staged, plan):
        try:
            atomicio.publish_file(tmp, dest)
        except OSError:
            for later_tmp, _later_dest in staged[len(done) + 1 :]:
                try:
                    later_tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            for applied, original in reversed(done):
                atomicio.write_bytes(applied, original)  # 回滚：换回原字节
            raise
        done.append((dest, old))
    return [label for _d, _o, _n, label in plan]


def _verified_interpreter(server: Path, py: str | None) -> str | None:
    """挑一个**验证过起得来启动器**的解释器绝对路径。

    首选插件自己 `--health` 解析出来的那个：解释器定位的权威只有
    `mcp/server.py` 的 resolver 一份（显式覆盖 → worker 环境 → 自管 runtime →
    从 CLI 反推 → PATH），这里问它要结论，不抄第二份候选链。它答不上来（机器上
    压根没有引擎）时退回跑得动体检的 `py` 本身——降级 server 也是有工具的。
    """
    candidates: list[str] = []
    if py:
        rc, out = _run([py, *probe_args(), str(server), "--health"], timeout=120, env=_health_env())
        report = _last_json(out) or {}
        chosen = report.get("python")
        if isinstance(chosen, str) and chosen.strip():
            candidates.append(chosen)
        candidates.append(py)
    for cand in candidates:
        ok, _detail = launcher_starts(cand, server)
        if ok:
            return cand
    return None


# ------------------------------ 步骤 ------------------------------
def _step(name: str, *, ok: bool, skipped: bool = False, detail: str = "", code: str = "") -> dict:
    out = {"step": name, "ok": ok, "skipped": skipped}
    if detail:
        out["detail"] = detail
    if code:
        out["error_code"] = code
    return out


def _marketplace_configured(codex: str) -> bool:
    """兼容入口：登记了才 True（unknown 也是 False——调用方要分档就用 `_marketplace_state`）。"""
    return _marketplace_state(codex)["state"] == "registered"


def _plugin_installed(codex: str) -> bool:
    """兼容入口：STATUS 是「已安装」才 True。"""
    return _plugin_state(codex)["state"] == "installed"


def _describe_source(mk: dict, channel: str | None = None) -> str:
    st, src = mk.get("source_type"), mk.get("source")
    if not src:
        return "已登记"
    official = src in (brand.CODEX_PLUGIN_SOURCE_URL, brand.REPO_URL, brand.CODEX_MARKETPLACE)
    if channel == "stable-archive":
        tag = "官方发行分支的压缩包"
    else:
        tag = "官方源" if official else "自定义来源（不改）"
    return f"已登记：{st or '?'} {src}（{tag}）"


def _upgrade_marketplace_step(codex: str, mk: dict, summary: dict) -> dict:
    """`tavotto codex upgrade` 的 marketplace 步：按这份安装走的通道各自刷新。

    * 压缩包本地市场（没有 git 时的装法）：重新下载、核对、换目录；内容变了就要求插件步重装；
    * git 市场：`codex plugin marketplace upgrade tavotto`（Codex 自己换插件缓存）；
    * 别的本地市场 / 自定义来源：用户自己的选择，不替他升级。
    """
    channel = plugin_channel(mk.get("root"))
    summary["channel"] = channel
    if channel["channel"] == "stable-archive":
        root = mk.get("root") or ""
        if os.path.normcase(os.path.realpath(root)) != os.path.normcase(
            os.path.realpath(archive_marketplace_dir())
        ):
            return _step(
                "marketplace",
                ok=False,
                code=ERR_MARKETPLACE,
                detail=f"这份本地市场（{root}）不是 `tavotto codex install` 建的，不替你覆盖。"
                "重新下载解压到同一个目录，再 `codex plugin add "
                + brand.CODEX_PLUGIN_REF
                + "`"
                + _ARCHIVE_MANUAL,
            )
        try:
            info = fetch_stable_snapshot()
        except ArchiveError as exc:
            return _step("marketplace", ok=False, code=ERR_MARKETPLACE, detail=str(exc))
        summary["archive"] = info
        summary["reinstall"] = info["changed"]
        if not info["changed"]:
            return _step(
                "marketplace", ok=True, skipped=True, detail="已是最新：" + _describe_snapshot(info)
            )
        return _step("marketplace", ok=True, detail="已更新为" + _describe_snapshot(info))
    if mk.get("source_type") == "local" or channel["channel"] == "custom":
        return _step(
            "marketplace",
            ok=True,
            skipped=True,
            detail=_describe_source(mk) + "；不是官方发行通道，不替你升级",
        )
    rc, out = _codex_run(codex, ["plugin", "marketplace", "upgrade", brand.CODEX_MARKETPLACE_NAME])
    if rc != 0:
        return _step("marketplace", ok=False, code=ERR_MARKETPLACE, detail=out[-400:])
    return _step("marketplace", ok=True, detail=out[-200:] or "已刷新 git 市场快照")


def _recover_managed_marketplace(root: str | None) -> tuple[bool, list[str]]:
    """Codex 登记的是 `tavotto codex install` 建的那个本地市场、目录却不在：上一次换目录时在两次
    replace 之间被杀，已核对的那份还在 `.old-*` 里。先挪回去，再判通道——不然目录不在、收据读不到，
    通道判成 unknown / custom，`upgrade` 永远「不替你升级」，恢复那一步也走不到（Codex #725）。
    回 (是否挪回了, 挪不回 / 删不掉的目录)——后者非空时调用方要报失败，不能当成「没什么可恢复」。"""
    if not root:
        return False, []
    dest = archive_marketplace_dir()
    same = os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(dest))
    if not same or dest.exists():
        return False, []
    try:
        with _marketplace_lock(dest.parent):
            stuck = _remove_one_shot_dirs(dest.parent, dest)
    except _MarketplaceBusy:
        # 另一个 install / upgrade 正在换目录：dest 不在可能只是它两次 replace 之间的一瞬。不插手，但也
        # 不能当成「没什么要恢复」往下走——目录缺着会被判成自定义来源跳过、靠缓存里的旧插件报成功（Codex #725）
        return False, [
            f"{dest.parent}（另一个 tavotto codex install / upgrade 正在更新它，等它结束后重跑）"
        ]
    except OSError as exc:
        return False, [f"{dest.parent}（拿不到锁：{exc}）"]
    return dest.exists(), stuck


def _marketplace_step(codex: str, *, apply: bool, summary: dict, upgrade: bool = False) -> dict:
    mk = _marketplace_state(codex)
    # 只读的 doctor 不挪目录；install / upgrade 才恢复
    recovered, stuck = (
        _recover_managed_marketplace(mk.get("root"))
        if (apply or upgrade) and mk["state"] == "registered"
        else (False, [])
    )
    summary["marketplace"] = {
        "registered": mk["state"] == "registered",
        "state": mk["state"],
        "source_type": mk.get("source_type"),
        "source": mk.get("source"),
        "root": mk.get("root"),
        "recovered_from_backup": bool(recovered),
    }
    if stuck and not recovered:
        # 登记的托管市场不在、备份又挪不回：照常往下走会把缺目录判成自定义来源跳过，缓存里的旧插件
        # 让后面几步照样过，命令报成功而市场仍然缺着（Codex #725）——这里就报失败
        return _step(
            "marketplace",
            ok=False,
            code=ERR_MARKETPLACE,
            detail=f"Codex 登记的本地市场 {archive_marketplace_dir()} 不在，恢复不了："
            + "、".join(stuck)
            + "。多半是另一个 install / upgrade 正在进行，或杀毒软件占着备份：等它结束 / 关掉占用后重跑本命令。",
        )
    if mk["state"] == "unknown":
        # 「不知道」不是「没有」：这时候跑 add 是盲改
        return _step(
            "marketplace",
            ok=False,
            code=ERR_MARKETPLACE_UNKNOWN,
            detail="`codex plugin marketplace list` 跑不出结论，登记状态不明："
            + (mk.get("detail") or "（零输出）")
            + _unknown_hint(mk.get("detail") or "", "登记"),
        )
    if mk["state"] == "registered":
        if upgrade:
            return _upgrade_marketplace_step(codex, mk, summary)
        channel = plugin_channel(mk.get("root"))
        summary["channel"] = channel
        detail = _describe_source(mk, channel["channel"])
        if channel["channel"] == "legacy-local":
            detail += (
                "；快照里的插件条目仍是旧的本地来源（把仓库本体当插件装）。"
                "跑 `codex plugin marketplace upgrade tavotto` 刷新快照即可换到发行通道"
            )
        elif channel["channel"] == "stable":
            detail += f"；插件来源 = 发行分支 {brand.CODEX_PLUGIN_STABLE_BRANCH}"
        elif channel["channel"] == "stable-archive":
            detail += (
                f"；插件来源 = 发行分支 {brand.CODEX_PLUGIN_STABLE_BRANCH} 的压缩包（本地市场，"
                "没有 git 时的装法）。升级用 `tavotto codex upgrade`——本地市场没有 "
                "`codex plugin marketplace upgrade`"
            )
        return _step("marketplace", ok=True, skipped=True, detail=detail)
    if not apply:
        return _step("marketplace", ok=False, detail="未登记", code=ERR_MARKETPLACE)
    argv = ["plugin", "marketplace", "add", brand.CODEX_MARKETPLACE]
    for sparse in brand.CODEX_SPARSE_PATHS:
        argv += ["--sparse", sparse]
    rc, out = _codex_run(codex, argv)
    if rc != 0:
        if _GIT_NOT_RUNNABLE.search(out):
            # 只装了 Codex 桌面版、没有 git 的机器（#722）：改走不需要 git 的来源
            return _archive_marketplace_step(codex, why=out[-200:], summary=summary)
        return _step("marketplace", ok=False, detail=out[-400:], code=ERR_MARKETPLACE)
    mk = _marketplace_state(codex)
    summary["marketplace"].update(
        {"registered": mk["state"] == "registered", "state": mk["state"], "root": mk.get("root")}
    )
    summary["channel"] = plugin_channel(mk.get("root"))
    return _step("marketplace", ok=True, detail="已登记")


def _plugin_step(codex: str, *, apply: bool, summary: dict) -> dict:
    st = _plugin_state(codex)
    summary["plugin"] = {
        "state": st["state"],
        "version": st.get("version"),
        "enabled": st.get("enabled"),
        "source": st.get("source"),
        "path": st.get("path"),
    }
    if st["state"] == "unknown":
        return _step(
            "plugin",
            ok=False,
            code=ERR_PLUGIN_UNKNOWN,
            detail="`codex plugin list` 跑不出结论，安装状态不明："
            + (st.get("detail") or "（零输出）")
            + _unknown_hint(st.get("detail") or "", "装"),
        )
    if (
        st["state"] == "installed"
        and not summary.get("reinstall")
        and _installed_lags_archive(st, summary.get("archive"))
    ):
        # 压缩包上次已换成新的、那次 `plugin add` 却没成：本地市场「已是最新」（changed=False），
        # Codex 里装着的仍是旧的。按装着的版本与收据的版本判，不靠上一次的 changed（Codex #725）
        summary["reinstall"] = True
    if st["state"] == "installed" and not summary.get("reinstall"):
        # **健康状态下不重装。** 升级归 `codex plugin marketplace upgrade`（压缩包本地市场是
        # `tavotto codex upgrade`，它把 summary["reinstall"] 置真），由用户自己决定什么时候做；
        # 这条命令的职责是「缺什么补什么」。
        return _step(
            "plugin",
            ok=True,
            skipped=True,
            detail=f"已安装 {st.get('version') or ''}".strip()
            + (f"（{st['path']}）" if st.get("path") else ""),
        )
    if st["state"] == "absent":
        return _step(
            "plugin",
            ok=False,
            code=ERR_SOURCE_UNSUPPORTED,
            detail="marketplace 已登记，但 Codex 没有列出 tavotto 插件——多半是这个 Codex "
            "版本不认识市场清单里的来源类型（发行通道用 git-subdir，需要较新的 Codex），"
            "或本机快照太旧。先 `codex plugin marketplace upgrade tavotto`；仍然没有就升级 Codex。",
        )
    if not apply:
        return _step("plugin", ok=False, detail="未安装", code=ERR_PLUGIN)
    # 本地市场的目录内容换了之后，同名插件再 add 一次 Codex 就把缓存换成新版本（codex 0.157 实测：
    # 旧版本目录被删、新版本目录建出来）
    rc, out = _codex_run(codex, ["plugin", "add", brand.CODEX_PLUGIN_REF])
    if rc != 0:
        return _step("plugin", ok=False, detail=out[-400:], code=ERR_PLUGIN)
    before = st.get("version") if st["state"] == "installed" else None
    st = _plugin_state(codex)
    summary["plugin"].update(
        {
            "state": st["state"],
            "version": st.get("version"),
            "enabled": st.get("enabled"),
            "path": st.get("path"),
        }
    )
    if before is not None:
        return _step("plugin", ok=True, detail=f"已重装：{before} → {st.get('version') or '?'}")
    return _step("plugin", ok=True, detail="已安装")


def _locate_step(summary: dict) -> tuple[Path | None, dict | None]:
    """定位唯一的已装副本；歧义时给一条失败步骤而不是猜一个。"""
    plugin_dir, detail, code = locate_installed_plugin(summary.get("plugin") or {})
    summary.setdefault("plugin", {})["install_dir"] = str(plugin_dir) if plugin_dir else None
    if plugin_dir is None:
        return None, _step("plugin", ok=False, code=code, detail=detail)
    return plugin_dir, None


def _canvas_step(plugin_dir: Path | None, summary: dict) -> dict:
    """已装副本的画布**完整吗**（不是「文件在不在」）。

    随包清单在时按清单核对（允许两份启动清单一起钉 command，其余文件逐字节比）；
    旧发行件没有清单时至少验画布本身合格、启动清单没有第二份实现改过它。这一步
    **只读**：不重装、不修补——画布不完整的处方是重新装插件（先 `codex plugin remove`），
    不是在这里悄悄补文件。
    """
    if plugin_dir is None:
        summary["canvas"] = {"complete": False, "reason": "找不到已装的插件"}
        return _step("canvas", ok=False, code=ERR_CANVAS, detail="找不到已装的插件，无从检查画布")
    try:
        manifest = pluginmanifest.read_manifest(plugin_dir)
    except pluginmanifest.PluginManifestError as exc:
        summary["canvas"] = {"complete": False, "reason": str(exc)}
        return _step("canvas", ok=False, code=ERR_CANVAS, detail=f"随包清单坏了：{exc}")
    if manifest is None:
        problems = pluginmanifest.verify_dir(plugin_dir, legacy=True, installed=True)
        kind = "旧发行件（没有随包清单），只验画布本身与启动清单"
    else:
        problems = pluginmanifest.verify_dir(plugin_dir, installed=True)
        kind = f"按随包清单核对（{manifest.get('plugin_version')} · content {str(manifest.get('content_digest'))[:12]}）"
    summary["canvas"] = {
        "complete": not problems,
        "reason": "；".join(problems) if problems else None,
        "verified_against_manifest": manifest is not None,
        "min_tavotto_version": (manifest or {}).get("min_tavotto_version"),
        "content_digest": (manifest or {}).get("content_digest"),
        "source_sha": (manifest or {}).get("source_sha"),
    }
    if problems:
        return _step(
            "canvas",
            ok=False,
            code=ERR_CANVAS,
            detail=kind
            + "："
            + "；".join(problems)[:600]
            + "。处方：`codex plugin remove tavotto@tavotto` 后重新 `codex plugin add tavotto@tavotto`，"
            "再跑一次 `tavotto codex install`。",
        )
    return _step("canvas", ok=True, skipped=True, detail=kind + "：完整")


def _engine_step(plugin_dir: Path | None, py: str | None, *, apply: bool) -> dict:
    if py is None:
        return _step(
            "engine",
            ok=False,
            code=ERR_PROVISION,
            detail="PATH 上找不到真的 python3/python。桌面版的 tavotto-cli 是"
            "冻结产物，不能当解释器用；装一个 Python 或用 "
            "TAVOTTO_MCP_PYTHON 指一个。",
        )
    # **冻结形态下 `engine_importable()` 答的是错的问题**：冻结包自己当然 import 得到
    # 引擎，但插件的 MCP server 用的是另一个解释器。那时候该问的是插件自己的
    # `--health`——只有它知道 server 会挑哪个环境（Codex 在 PR #169 上指出）。
    if not getattr(sys, "frozen", False) and engine_importable():
        return _step("engine", ok=True, skipped=True, detail="当前解释器已能 import tavotto.engine")
    if plugin_dir is None:
        return _step("engine", ok=False, detail="插件还没装好，无从 provision", code=ERR_PROVISION)
    server = plugin_dir / "mcp" / "server.py"
    if not server.is_file():
        return _step("engine", ok=False, detail=f"插件里没有 {server}", code=ERR_PROVISION)
    rc, out = _run([py, *probe_args(), str(server), "--health"], timeout=90, env=_health_env())
    if rc == 0:
        return _step("engine", ok=True, skipped=True, detail="插件已能解析到引擎")
    # 插件已经判出「引擎在、版本对不上」（engine_too_old / engine_incompatible）：在这里就原样转述，
    # 不当成「需要 provision」——否则冻结的桌面 CLI 上 doctor 在这一步就以 provision_failed 收场、
    # 走不到 health 那一步的转述，install 还会另建一个环境盖住真正的原因（Codex #724 P1）
    verdict = _engine_version_verdict("engine", _last_json(out) or {})
    if verdict is not None:
        return verdict
    if not apply:
        return _step("engine", ok=False, detail="需要 provision", code=ERR_PROVISION)
    # **复用插件自己的 --provision**，不抄第二份：那份实现知道该建在哪、装什么版本
    rc, out = _run([py, str(server), "--provision"])
    if rc != 0:
        return _step("engine", ok=False, detail=out[-400:], code=ERR_PROVISION)
    return _step("engine", ok=True, detail="已准备匹配版本的引擎")


def _interpreter_step(plugin_dir: Path | None, py: str | None, *, apply: bool) -> dict:
    """已装副本里那条启动命令，**在这台机器上真起得来吗**（issue #172）。

    `.mcp.json` 里钉的是 `python3`——POSIX 上它是唯一靠得住的名字，Windows 上它
    往往指向微软商店的 App Execution Alias：命令「存在」、退出码 9009、Codex 那边
    一个工具都没有，连降级 server 都起不来（起不来就没人能说话）。Codex 的
    `.mcp.json` 没有按平台分支的字段、没有候选链、`command` 也不过 shell，所以这
    件事只能在**安装时**解决：跑一遍，起不来就把已装副本的 command 换成一个验证
    过的解释器绝对路径。

    判据是执行，不是 `shutil.which` / `os.name`——「PATH 里有没有 python3」在
    Windows 上答不了「能不能跑」，而按平台分支只会把同一个错误换个地方犯。
    """
    if plugin_dir is None:
        return _step(
            "interpreter", ok=False, code=ERR_INTERPRETER, detail="插件还没装好，无从检查启动命令"
        )
    server = plugin_dir / "mcp" / "server.py"
    try:
        # 新版叫 codex.mcp.json、已装旧版叫 .mcp.json：由 Codex 清单指向决定（ADR 0109）
        mcp_path = plugin_dir / pluginmanifest.mcp_config_rel(plugin_dir)
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        command = next(iter(data["mcpServers"].values()))["command"]
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        StopIteration,
        pluginmanifest.PluginManifestError,
    ):
        return _step(
            "interpreter",
            ok=False,
            code=ERR_INTERPRETER,
            detail=f"读不出 {plugin_dir} 的 Codex MCP 配置里的 mcpServers[...].command",
        )
    # 发行件的 command 是插件自带的 `./mcp/launch`（#266）：Codex 按 `.mcp.json` 的
    # `cwd`（插件根）解析它、Windows 上再按 PATHEXT 落到 `launch.cmd`，这里照同一条路解析——
    # 按本进程的 cwd 或不补扩展名，都会把一个好好的启动器判成「起不来」，然后把它换掉。
    runnable = plugin_relative_command(plugin_dir, command)
    ok, detail = launcher_starts(runnable, server)
    if ok:
        return _step("interpreter", ok=True, skipped=True, detail=f"`{command}`：{detail}")
    if not apply:
        return _step(
            "interpreter",
            ok=False,
            code=ERR_INTERPRETER,
            detail=f"`{command}` 起不来启动器（{detail}）。Windows 上 `python3` 常常是"
            "微软商店的 App Execution Alias：命令存在、退出码 9009、Codex 里一个工具"
            "都没有。跑 `tavotto codex install` 把它钉到一个真能跑的解释器。",
        )
    chosen = _verified_interpreter(server, py)
    if chosen is None:
        return _step(
            "interpreter",
            ok=False,
            code=ERR_INTERPRETER,
            detail=f"`{command}` 起不来启动器（{detail}），也没找到能替它的解释器。"
            "装一个 Python（或用 TAVOTTO_MCP_PYTHON 指一个）再重跑。",
        )
    try:
        changed = pin_launcher_command(plugin_dir, chosen)
    except OSError as exc:
        # 事务失败：两份清单都还是原样（见 pin_launcher_command 的两段式）
        return _step(
            "interpreter",
            ok=False,
            code=ERR_PIN,
            detail=f"两份清单没能一起换上去，已回滚到原样：{exc}",
        )
    return _step(
        "interpreter",
        ok=True,
        detail=f"启动命令由 `{command}` 换成 `{chosen}`（改了 {'、'.join(changed)}）",
    )


def _health_step(plugin_dir: Path | None, py: str | None, summary: dict) -> dict:
    if plugin_dir is None:
        return _step("health", ok=False, detail="找不到已装的插件", code=ERR_HEALTH)
    if py is None:
        return _step(
            "health",
            ok=False,
            code=ERR_HEALTH,
            detail="PATH 上找不到真的 python3/python，跑不了插件的体检",
        )
    server = plugin_dir / "mcp" / "server.py"
    rc, out = _run([py, *probe_args(), str(server), "--health"], timeout=90, env=_health_env())
    report = _last_json(out) or {}
    engine_version = report.get("engine_version")
    required = (summary.get("canvas") or {}).get("min_tavotto_version")
    have = pluginmanifest.semver(engine_version if isinstance(engine_version, str) else None)
    want = pluginmanifest.semver(required)
    satisfied = None if (have is None or want is None) else have >= want
    summary["engine"] = {
        "version": engine_version,
        "min_required": required,
        "satisfied": satisfied,
        "python": report.get("python"),
        "mode": report.get("mode"),
    }
    verdict = _engine_version_verdict("health", report) if rc != 0 else None
    if verdict is not None:
        return verdict
    if rc != 0:
        return _step("health", ok=False, detail=out[-400:], code=ERR_HEALTH)
    if satisfied is False:
        return _step(
            "health",
            ok=False,
            code=ERR_ENGINE_OLD,
            detail=f"引擎 {engine_version} 低于已装插件要求的最低版本 {required}——插件的桥 import "
            f"不动这么老的引擎。" + _upgrade_hint(report.get("pip_index"), required),
        )
    return _step("health", ok=True, detail=out[-400:])


def _engine_version_verdict(step: str, report: dict) -> dict | None:
    """插件体检（`server.py --health` 的 JSON）判出的引擎版本类结论 → 一步失败；不是这类回 None。

    插件的降级诊断已经判出「引擎在、版本对不上」：话术（两个版本号、升级命令、镜像提示）**只在插件
    那一份里写**，这里原样转述，不写第二份（#721：两边口径一致）。engine / health 两步共用这一份。"""
    code = report.get("code")
    if code not in _ENGINE_VERSION_CODES or not isinstance(report.get("error"), str):
        return None
    recovery = [r for r in report.get("recovery") or [] if isinstance(r, str)]
    return _step(
        step,
        ok=False,
        code=code,
        detail=report["error"] + ("\n恢复步骤：\n- " + "\n- ".join(recovery) if recovery else ""),
    )


def _upgrade_hint(index: object, required: str | None) -> str:
    """引擎太老时「怎么升」的那句。pip 指向镜像时（探测只在插件那侧做：`server.pip_index`）整句换成
    绕开镜像的命令，**不给**裸的 `pipx upgrade tavotto`——它照样去问那个镜像、装回旧版（Codex #724；
    与插件 `upgrade_commands` 同一口径）。"""
    tail = "或把插件退回与引擎匹配的版本。"
    if not isinstance(index, dict) or not index.get("mirror"):
        return f"升级引擎（pipx upgrade tavotto / 升级桌面版），{tail}"
    pin = f'"tavotto[worker]=={required}"' if required else '"tavotto[worker]"'
    return (
        f"pip 的 index-url 指向镜像 {index.get('url')}（来自 {index.get('source')}），"
        f"镜像可能还没同步到新版，照常升级会装回旧版。绕开镜像升级引擎：pipx install --force {pin} "
        f"--index-url https://pypi.org/simple（或升级桌面版），{tail}"
    )


# ------------------------------ 三个子命令 ------------------------------
def _codex_or_fail(steps: list[dict]) -> str | None:
    codex, searched = find_codex()
    if codex is None:
        steps.append(
            _step(
                "codex_cli",
                ok=False,
                code=ERR_CODEX_MISSING,
                detail="找不到 codex 命令。找过："
                + "、".join(searched)
                + "。请先安装 Codex CLI（本命令不代装），装好后重跑。",
            )
        )
        return None
    steps.append(_step("codex_cli", ok=True, detail=codex))
    return codex


def _run_pipeline(*, apply: bool, upgrade: bool = False) -> tuple[bool, list[dict], dict]:
    """安装 / 诊断流水线。回 (ok, steps, summary)。

    `summary` 回答四个问题：装的是哪份插件（版本、路径）、来自哪里（marketplace
    来源与通道）、画布完整吗、引擎版本满足要求吗。它是 `--json` 输出的补充字段，
    `steps` 的契约一个字不变。
    """
    steps: list[dict] = []
    summary: dict = {
        "marketplace": None,
        "channel": None,
        "plugin": None,
        "canvas": None,
        "engine": None,
    }
    codex = _codex_or_fail(steps)
    if codex is None:
        return False, steps, summary
    steps.append(_marketplace_step(codex, apply=apply, summary=summary, upgrade=upgrade))
    if not steps[-1]["ok"]:
        return False, steps, summary
    steps.append(_plugin_step(codex, apply=apply, summary=summary))
    if not steps[-1]["ok"]:
        return False, steps, summary
    plugin_dir, failure = _locate_step(summary)
    if failure is not None:
        steps.append(failure)
        return False, steps, summary
    py = plugin_python()
    steps.append(_engine_step(plugin_dir, py, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps, summary
    # 引擎之后、体检之前：自管 runtime 这时候才存在，解释器该从它里面挑
    steps.append(_interpreter_step(plugin_dir, py, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps, summary
    # 钉完启动命令再验完整性：允许的本地修改正是刚才那一步做的
    steps.append(_canvas_step(plugin_dir, summary))
    if not steps[-1]["ok"]:
        return False, steps, summary
    steps.append(_health_step(plugin_dir, py, summary))
    return steps[-1]["ok"], steps, summary


def uninstall_steps() -> tuple[bool, list[dict]]:
    """移除插件与 marketplace 项。**不碰引擎**——它可能还有别的用处。"""
    steps: list[dict] = []
    codex = _codex_or_fail(steps)
    if codex is None:
        return False, steps
    if _plugin_installed(codex):
        rc, out = _codex_run(codex, ["plugin", "remove", brand.CODEX_PLUGIN_REF])
        steps.append(
            _step(
                "plugin",
                ok=rc == 0,
                detail=out[-400:] or "已移除",
                code="" if rc == 0 else ERR_UNINSTALL,
            )
        )
    else:
        steps.append(_step("plugin", ok=True, skipped=True, detail="本来就没装"))
    if _marketplace_configured(codex):
        # **收的是配置后的 marketplace 名，不是源。** 给 `Tavotto/Tavotto` 会被
        # 直接拒（`/` 不是合法名字），于是插件删掉了、marketplace 却永远留着。
        rc, out = _codex_run(
            codex, ["plugin", "marketplace", "remove", brand.CODEX_MARKETPLACE_NAME]
        )
        steps.append(
            _step(
                "marketplace",
                ok=rc == 0,
                detail=out[-400:] or "已移除",
                code="" if rc == 0 else ERR_UNINSTALL,
            )
        )
    else:
        steps.append(_step("marketplace", ok=True, skipped=True, detail="本来就没登记"))
    return all(s["ok"] for s in steps), steps


def _emit(
    ok: bool, action: str, steps: list[dict], *, as_json: bool, summary: dict | None = None
) -> int:
    failed = next((s for s in steps if not s["ok"]), None)
    if as_json:
        payload = {"ok": ok, "action": action, "steps": steps}
        if summary is not None:
            payload["summary"] = summary
        if failed and failed.get("error_code"):
            payload["error_code"] = failed["error_code"]
            payload["error"] = failed.get("detail", "")
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if ok else 1
    for s in steps:
        mark = "跳过" if s.get("skipped") else ("✓" if s["ok"] else "✗")
        line = f"{mark} {s['step']}"
        if s.get("detail"):
            line += f"：{s['detail']}"
        print(line, file=sys.stdout if s["ok"] else sys.stderr)
    if ok and action in ("install", "upgrade"):
        # 刻意**只说这一句**：旧会话里验不出工具来，试图验证只会给出误导性的结论
        print(
            "\n" + ("装好了" if action == "install" else "升级完成") + "。请新开一个 Codex 会话。"
        )
    return 0 if ok else 1


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="tavotto codex",
        description="安装 / 诊断 / 升级 / 移除 Tavotto 的 Codex 集成（ADR 0012）",
    )
    ap.add_argument("action", choices=("install", "doctor", "upgrade", "uninstall"))
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    if args.action == "uninstall":
        ok, steps = uninstall_steps()
        summary = None
    else:
        ok, steps, summary = _run_pipeline(
            apply=args.action in ("install", "upgrade"), upgrade=args.action == "upgrade"
        )
    return _emit(ok, args.action, steps, as_json=args.json, summary=summary)
