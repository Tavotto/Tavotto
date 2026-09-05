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
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import atomicio, brand, pluginmanifest
from .runtime import CREATE_NO_WINDOW

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
    return None, searched


def _run(argv: list[str], timeout: int = _TIMEOUT) -> tuple[int, str]:
    """跑一条命令，回 (退出码, 合并输出)。绝不抛——失败也是一种结论。"""
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return 127, f"找不到可执行文件：{argv[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"超过 {timeout}s 没有返回：{' '.join(argv)}"
    except OSError as exc:
        return 126, f"{type(exc).__name__}: {exc}"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


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
    rc, out = _run([candidate, "-c", f"print('{_PY_PROBE}')"], timeout=60)
    return rc == 0 and _PY_PROBE in out


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
    rc, out = _run([command, str(server), "--health"], timeout=120)
    if _last_json(out) is not None:
        return True, f"退出码 {rc}，启动器回了体检 JSON"
    return False, f"退出码 {rc}，没有体检 JSON：{(out[-160:] or '（零输出）')}"


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
    rc, out = _run([codex, "plugin", "marketplace", "list", "--json"])
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
    rc, out = _run([codex, "plugin", "marketplace", "list"])
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
    rc, out = _run([codex, "plugin", "list", "-m", brand.CODEX_MARKETPLACE_NAME, "--json"])
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
    rc, out = _run([codex, "plugin", "list", "-m", brand.CODEX_MARKETPLACE_NAME])
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
        if state["state"] == "unknown":
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

    先信客户端自己报的路径（`plugin list` 的 PATH 列）；报不出来时才看缓存——而且
    只看我们 marketplace 名下那一层，**恰好一个**才认。多个版本并存（升级后旧缓存
    还在）时不按最高版本号猜：Codex 用哪个由它说了算，这里报歧义并把候选列出来。
    """
    # 1. 本地来源（local marketplace 里的插件目录）：PATH 列就是它加载的那个目录。
    #    **git 来源时 PATH 列是来源描述**（`file://…, path \`codex-plugin\`, ref …`，
    #    codex 0.151 实测），不是路径——所以只在它确实是一份插件目录时才信。
    reported = state.get("path")
    if reported:
        p = Path(reported)
        if p.is_dir() and _is_our_plugin_dir(p):
            return p, f"Codex 报的安装路径：{p}", ""
    # 2. git / npm 来源：Codex 装进 cache/<marketplace>/<plugin>/<版本>，`plugin list`
    #    报的 version 就是它此刻启用的那一份（目录名 == 版本号，codex 0.151 实测）。
    cached = cached_plugin_dirs()
    version = state.get("version")
    if version:
        by_version = [p for p in cached if p.name == version]
        if len(by_version) == 1:
            return by_version[0], f"Codex 启用的版本 {version}：{by_version[0]}", ""
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
        return {"channel": "legacy-local" if legacy else "custom", "source": src}
    if (
        kind == "git-subdir"
        and src.get("url") in (brand.CODEX_PLUGIN_SOURCE_URL, brand.REPO_URL)
        and src.get("ref") == brand.CODEX_PLUGIN_STABLE_BRANCH
    ):
        return {"channel": "stable", "source": src}
    return {"channel": "custom", "source": src}


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
    mcp_path = plugin_dir / ".mcp.json"
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
        rc, out = _run([py, str(server), "--health"], timeout=120)
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


def _describe_source(mk: dict) -> str:
    st, src = mk.get("source_type"), mk.get("source")
    if not src:
        return "已登记"
    official = src in (brand.CODEX_PLUGIN_SOURCE_URL, brand.REPO_URL, brand.CODEX_MARKETPLACE)
    tag = "官方源" if official else "自定义来源（不改）"
    return f"已登记：{st or '?'} {src}（{tag}）"


def _marketplace_step(codex: str, *, apply: bool, summary: dict) -> dict:
    mk = _marketplace_state(codex)
    summary["marketplace"] = {
        "registered": mk["state"] == "registered",
        "state": mk["state"],
        "source_type": mk.get("source_type"),
        "source": mk.get("source"),
        "root": mk.get("root"),
    }
    if mk["state"] == "unknown":
        # 「不知道」不是「没有」：这时候跑 add 是盲改
        return _step(
            "marketplace",
            ok=False,
            code=ERR_MARKETPLACE_UNKNOWN,
            detail="`codex plugin marketplace list` 跑不出结论，登记状态不明："
            + (mk.get("detail") or "（零输出）"),
        )
    if mk["state"] == "registered":
        channel = plugin_channel(mk.get("root"))
        summary["channel"] = channel
        detail = _describe_source(mk)
        if channel["channel"] == "legacy-local":
            detail += (
                "；快照里的插件条目仍是旧的本地来源（把仓库本体当插件装）。"
                "跑 `codex plugin marketplace upgrade tavotto` 刷新快照即可换到发行通道"
            )
        elif channel["channel"] == "stable":
            detail += f"；插件来源 = 发行分支 {brand.CODEX_PLUGIN_STABLE_BRANCH}"
        return _step("marketplace", ok=True, skipped=True, detail=detail)
    if not apply:
        return _step("marketplace", ok=False, detail="未登记", code=ERR_MARKETPLACE)
    argv = [codex, "plugin", "marketplace", "add", brand.CODEX_MARKETPLACE]
    for sparse in brand.CODEX_SPARSE_PATHS:
        argv += ["--sparse", sparse]
    rc, out = _run(argv)
    if rc != 0:
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
            + (st.get("detail") or "（零输出）"),
        )
    if st["state"] == "installed":
        # **健康状态下不重装。** 升级归 `codex plugin marketplace upgrade`，
        # 由用户自己决定什么时候做；这条命令的职责是「缺什么补什么」。
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
    rc, out = _run([codex, "plugin", "add", brand.CODEX_PLUGIN_REF])
    if rc != 0:
        return _step("plugin", ok=False, detail=out[-400:], code=ERR_PLUGIN)
    st = _plugin_state(codex)
    summary["plugin"].update(
        {
            "state": st["state"],
            "version": st.get("version"),
            "enabled": st.get("enabled"),
            "path": st.get("path"),
        }
    )
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
    rc, _out = _run([py, str(server), "--health"], timeout=90)
    if rc == 0:
        return _step("engine", ok=True, skipped=True, detail="插件已能解析到引擎")
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
    mcp_path = plugin_dir / ".mcp.json"
    server = plugin_dir / "mcp" / "server.py"
    try:
        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        command = next(iter(data["mcpServers"].values()))["command"]
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return _step(
            "interpreter",
            ok=False,
            code=ERR_INTERPRETER,
            detail=f"读不出 {mcp_path} 里的 mcpServers[...].command",
        )
    ok, detail = launcher_starts(command, server)
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
    rc, out = _run([py, str(server), "--health"], timeout=90)
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
    if rc != 0:
        return _step("health", ok=False, detail=out[-400:], code=ERR_HEALTH)
    if satisfied is False:
        return _step(
            "health",
            ok=False,
            code=ERR_ENGINE_OLD,
            detail=f"引擎 {engine_version} 低于已装插件要求的最低版本 {required}——插件的桥 import "
            f"不动这么老的引擎。升级引擎（pipx upgrade tavotto / 升级桌面版），"
            f"或把插件退回与引擎匹配的版本。",
        )
    return _step("health", ok=True, detail=out[-400:])


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


def _run_pipeline(*, apply: bool) -> tuple[bool, list[dict], dict]:
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
    steps.append(_marketplace_step(codex, apply=apply, summary=summary))
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
        rc, out = _run([codex, "plugin", "remove", brand.CODEX_PLUGIN_REF])
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
        rc, out = _run([codex, "plugin", "marketplace", "remove", brand.CODEX_MARKETPLACE_NAME])
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
    if ok and action == "install":
        # 刻意**只说这一句**：旧会话里验不出工具来，试图验证只会给出误导性的结论
        print("\n装好了。请新开一个 Codex 会话。")
    return 0 if ok else 1


def cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="tavotto codex", description="安装 / 诊断 / 移除 Tavotto 的 Codex 集成（ADR 0012）"
    )
    ap.add_argument("action", choices=("install", "doctor", "uninstall"))
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    if args.action == "uninstall":
        ok, steps = uninstall_steps()
        summary = None
    else:
        ok, steps, summary = _run_pipeline(apply=args.action == "install")
    return _emit(ok, args.action, steps, as_json=args.json, summary=summary)
