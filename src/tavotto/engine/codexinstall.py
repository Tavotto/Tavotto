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

from . import brand
from .runtime import CREATE_NO_WINDOW

#: 每一步的稳定 code。message 随时可改，code 不许改（调用方按它分诊）。
ERR_CODEX_MISSING = "codex_cli_missing"
ERR_MARKETPLACE = "marketplace_add_failed"
ERR_PLUGIN = "plugin_add_failed"
ERR_PROVISION = "provision_failed"
ERR_INTERPRETER = "interpreter_unusable"
ERR_HEALTH = "health_failed"
ERR_UNINSTALL = "uninstall_failed"

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


def installed_plugin_dir() -> Path | None:
    """已装插件在 Codex 那边的落点（`$CODEX_HOME/plugins/**/.codex-plugin/plugin.json`）。

    按**清单里的 name** 认，不按目录名：目录名带缓存哈希，会随版本变。
    """
    root = codex_home() / "plugins"
    if not root.is_dir():
        return None
    for manifest in sorted(root.rglob(".codex-plugin/plugin.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("name") == brand.CODEX_PLUGIN_NAME:
            return manifest.parent.parent
    return None


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
#: `dependencies:` 顶层块（缩进行都算它的，遇到下一个顶格 key 为止）。
_DEPS_BLOCK = re.compile(r"^dependencies:\n(?:.*?)(?=^\w|\Z)", re.M | re.S)


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
    """
    m = _DEPS_BLOCK.search(text)
    if not m:
        return text
    block = re.sub(
        r"^(\s*command:\s*).*$",
        lambda mm: mm.group(1) + _yaml_scalar(command),
        m.group(0),
        flags=re.M,
    )
    return text[: m.start()] + block + text[m.end() :]


def pin_launcher_command(plugin_dir: Path, command: str) -> list[str]:
    """把**已装副本**的启动命令钉到 `command`，`.mcp.json` 与 `openai.yaml` 一起改。

    这两处是根 `AGENTS.md` 列的严格同源对：Codex 的 stdio 依赖按 `command` 做
    规范键匹配，只改一侧的话技能声明的依赖对不上插件自带的 server，Codex 会把它
    当成「还没装」再弹一次安装提示。改的是**已装副本**，仓库里那份不动——仓库里
    钉的是跨机器的引导默认值，绝对路径只属于这一台机器。
    """
    changed: list[str] = []
    mcp_path = plugin_dir / ".mcp.json"
    data = json.loads(mcp_path.read_text(encoding="utf-8"))
    for entry in data.get("mcpServers", {}).values():
        if isinstance(entry, dict) and "command" in entry:
            entry["command"] = command
    mcp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed.append(mcp_path.name)
    for yaml_path in sorted(plugin_dir.glob("skills/*/agents/openai.yaml")):
        text = yaml_path.read_text(encoding="utf-8")
        new = _replace_dependency_command(text, command)
        if new != text:
            yaml_path.write_text(new, encoding="utf-8")
            changed.append(yaml_path.relative_to(plugin_dir).as_posix())
    return changed


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
    """`codex plugin marketplace list` 的 MARKETPLACE 列里有没有我们这条。

    按**整列相等**判，不是子串：ROOT 那一列是路径，里面出现 `tavotto` 太容易了
    （用户的目录名、缓存路径都可能带上它），子串匹配会把「没登记」判成「已登记」，
    然后 `plugin add` 找不到源而失败——症状离原因很远。
    """
    rc, out = _run([codex, "plugin", "marketplace", "list"])
    if rc != 0:
        return False
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == brand.CODEX_MARKETPLACE_NAME:
            return True
    return False


def _plugin_installed(codex: str) -> bool:
    """`codex plugin list` 里我们这条的 **STATUS 列**是不是「已安装」。

    这里踩过一个坑（Codex 在 PR #169 上指出）：marketplace 加好但插件还没装时，
    `plugin list` **照样会列出** `tavotto@tavotto`，只是 STATUS 是「not installed」。
    拿「输出里有没有 tavotto」当判据，全新安装会被判成「已装」而跳过 `plugin add`，
    后面的 cache 查找与 health 全挂——**主流程反而走不通**。
    """
    rc, out = _run([codex, "plugin", "list", "-m", brand.CODEX_MARKETPLACE_NAME])
    if rc != 0:
        return False
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] != brand.CODEX_PLUGIN_REF:
            continue
        status = line[len(parts[0]) :].strip().lower()
        return status.startswith("installed")
    return False


def _marketplace_step(codex: str, *, apply: bool) -> dict:
    if _marketplace_configured(codex):
        return _step("marketplace", ok=True, skipped=True, detail="已登记")
    if not apply:
        return _step("marketplace", ok=False, detail="未登记", code=ERR_MARKETPLACE)
    argv = [codex, "plugin", "marketplace", "add", brand.CODEX_MARKETPLACE]
    for sparse in brand.CODEX_SPARSE_PATHS:
        argv += ["--sparse", sparse]
    rc, out = _run(argv)
    if rc != 0:
        return _step("marketplace", ok=False, detail=out[-400:], code=ERR_MARKETPLACE)
    return _step("marketplace", ok=True, detail="已登记")


def _plugin_step(codex: str, *, apply: bool) -> dict:
    if _plugin_installed(codex):
        # **健康状态下不重装。** 升级归 `codex plugin marketplace upgrade`，
        # 由用户自己决定什么时候做；这条命令的职责是「缺什么补什么」。
        return _step("plugin", ok=True, skipped=True, detail="已安装")
    if not apply:
        return _step("plugin", ok=False, detail="未安装", code=ERR_PLUGIN)
    rc, out = _run([codex, "plugin", "add", brand.CODEX_PLUGIN_REF])
    if rc != 0:
        return _step("plugin", ok=False, detail=out[-400:], code=ERR_PLUGIN)
    return _step("plugin", ok=True, detail="已安装")


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
    changed = pin_launcher_command(plugin_dir, chosen)
    return _step(
        "interpreter",
        ok=True,
        detail=f"启动命令由 `{command}` 换成 `{chosen}`（改了 {'、'.join(changed)}）",
    )


def _health_step(plugin_dir: Path | None, py: str | None) -> dict:
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
    if rc != 0:
        return _step("health", ok=False, detail=out[-400:], code=ERR_HEALTH)
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


def _run_pipeline(*, apply: bool) -> tuple[bool, list[dict]]:
    steps: list[dict] = []
    codex = _codex_or_fail(steps)
    if codex is None:
        return False, steps
    steps.append(_marketplace_step(codex, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    steps.append(_plugin_step(codex, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    plugin_dir = installed_plugin_dir()
    py = plugin_python()
    steps.append(_engine_step(plugin_dir, py, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    # 引擎之后、体检之前：自管 runtime 这时候才存在，解释器该从它里面挑
    steps.append(_interpreter_step(plugin_dir, py, apply=apply))
    if not steps[-1]["ok"]:
        return False, steps
    steps.append(_health_step(plugin_dir, py))
    return steps[-1]["ok"], steps


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


def _emit(ok: bool, action: str, steps: list[dict], *, as_json: bool) -> int:
    failed = next((s for s in steps if not s["ok"]), None)
    if as_json:
        payload = {"ok": ok, "action": action, "steps": steps}
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
    else:
        ok, steps = _run_pipeline(apply=args.action == "install")
    return _emit(ok, args.action, steps, as_json=args.json)
