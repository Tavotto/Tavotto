#!/usr/bin/env python3
"""给非 Codex 的本地 MCP 宿主生成 Tavotto 的接入片段——**只打印，不写任何文件**。

    python3 <完整包>/integrations/configure.py --host vscode --project-root /abs/project
    python3 <完整包>/integrations/configure.py --host claude-desktop --project-root D:\\论文\\figs
    python3 <完整包>/integrations/configure.py --host cursor --project-root ... --diagnose

`<完整包>` 是 GitHub Release 上的 `codex-plugin-<版本>.zip` 解出来的那个目录（名字
里带 codex 是历史原因：同一份包、同一个启动器 `mcp/server.py`、同一份 Skill，Codex
与其他宿主共用，ADR 0043 的构建 / 摘要 / 验证链一条不多）。

## 它做什么

1. 从**本文件自己的位置**找到同包的启动器 `mcp/server.py`（不从 PATH、不从源码
   checkout、不从任何厂商缓存里找）。
2. 用**真的执行**确认你给的（或当前的）解释器能把启动器跑起来：
   `<python> <包>/mcp/server.py --health`，要求回一行体检 JSON——与
   `tavotto codex install` 的 `launcher_starts()` 同一把尺（判据是执行结果，不是
   `which` 也不是文件名；Windows 商店别名「命令存在、零输出」在这里就会露馅）。
   探针的 cwd 是一个与仓库、包都无关的临时目录，环境里只带宿主会带的那几个变量。
3. 体检回来的引擎结论原样告诉你（stderr）：引擎可用 / 只装了桌面版 / 引擎太旧 /
   什么都没有，以及**启动器自己给的**那几条恢复步骤。本工具不 provision、不 pip、
   不联网——恢复是你显式跑的那一步。
4. 按 `--host` 的**那一家**官方 schema 序列化同一份启动描述（command / args / env），
   打印到 stdout；往哪个文件合并、怎样确认宿主真的加载了，写到 stderr。

## 三个解释器，别混

* **启动器解释器**（配置里的 `command`）：只要能跑纯标准库的 `mcp/server.py`；
* **引擎解释器**：启动器按自己的候选链找能 `import tavotto.engine` 的那个并交棒。
  只有当体检发现它是靠 PATH / 当前 shell 的环境变量才找到的（GUI 宿主的最小 PATH
  里可能没有），才把它钉进 `TAVOTTO_MCP_PYTHON`；`--engine-python` 可以显式指定；
* **渲染解释器**（跑用户绘图脚本的科学栈）：归 Tavotto 自己的设置，本工具不碰。

## 授权边界

`--project-root` 是**用户明确选的**那个目录，写进 `TAVOTTO_MCP_ROOTS`——它在
`RootAuthority` 里是最高权威（宿主的 roots / 确认框都不能把它扩宽）。文件系统根、
HOME 本身、完整包自己的目录一律拒绝：用户级配置 ≠ 允许读整个用户目录。

退出码：0 = 片段已打印（引擎没就绪也是 0，stderr 里有恢复步骤；配置本身是对的，
降级 server 会在宿主里如实说缺什么）；2 = 参数错；3 = 启动器起不来 / 包不完整。

纯标准库，Python 3.8+（与启动器同一下限）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
#: 完整包的根：本文件在 `<包>/integrations/` 下
PACKAGE_DIR = os.path.dirname(HERE)
SERVER = os.path.join(PACKAGE_DIR, "mcp", "server.py")
PLUGIN_JSON = os.path.join(PACKAGE_DIR, ".codex-plugin", "plugin.json")
CANVAS = os.path.join(PACKAGE_DIR, "mcp", "widget", "canvas.html")
BUILD_MANIFEST = os.path.join(PACKAGE_DIR, "plugin-build.json")
SKILL_DIR = os.path.join(PACKAGE_DIR, "skills", "tavotto-figure")
#: 真 server 起来要 import 的包内模块（`mcp/server.py` 交棒后 `import tavotto_mcp.server`，
#: 它再 import 其余几个）。`--health` 不 import 它们，所以只看启动器在不在不够：半截解包
#: 会拿到一份「成功」的配置，宿主一起就 ImportError（Codex 在 #559 上指出）。
RUNTIME_FILES = tuple(
    os.path.join(PACKAGE_DIR, "mcp", "tavotto_mcp", name)
    for name in (
        "__init__.py",
        "server.py",
        "bridge.py",
        "roots.py",
        "rpc.py",
        "widget.py",
        "sessionjournal.py",
    )
)

#: 配置里的 MCP server 名。与 Codex 的 `.mcp.json` 同一个 key。
SERVER_NAME = "tavotto"
#: `RootAuthority` 的显式根变量（`tavotto_mcp/roots.py` 的 `ROOTS_ENV`，同名）
ROOTS_ENV = "TAVOTTO_MCP_ROOTS"
#: 启动器的显式引擎解释器变量（`mcp/server.py` 的 `MCP_PYTHON_ENV`，同名）
ENGINE_ENV = "TAVOTTO_MCP_PYTHON"
#: 体检里这几个来源依赖「生成配置这一刻的 shell 环境」，GUI 宿主多半没有——
#: 引擎落在它们上面时要把解释器钉进配置。`current` / `managed` / `configured`
#: 与环境无关（当前解释器自己就有引擎 / 配置目录下的自管 venv / Tavotto 设置）。
ENV_DEPENDENT_SOURCES = ("discovered", "worker_env", "mcp_env")
#: 探针里保留的环境变量：宿主进程一般都会有的那几个（定位配置目录要用）。
#: 其余一律不带——GUI 宿主从 Dock / 开始菜单启动时拿不到你的 shell 环境。
PROBE_ENV_KEEP = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "SystemRoot",
    "TEMP",
    "TMP",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "TAVOTTO_CONFIG_DIR",
    "TAVOTTO_DATA_DIR",
    "TAVOTTO_NO_TELEMETRY",
    "LANG",
    "LC_ALL",
)
#: GUI 宿主常见的最小 PATH（macOS launchd 的默认值就是这一串）
MINIMAL_POSIX_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
PROBE_TIMEOUT = 180
#: 探针里禁写 .pyc：`--health` 会 import 包里的 handoff.py，默认会在包目录里落
#: `__pycache__`——一个声称「不写任何文件」的工具不许改动包（Codex 在 #559 上指出）
NO_BYTECODE_ENV = "PYTHONDONTWRITEBYTECODE"
#: 生成配置的这台机器是不是 Windows（序列化里只有 Windows 专属的 env 透传看它；测试可替换）
IS_WINDOWS = os.name == "nt"


class ConfigureError(Exception):
    """参数或包不成立。`code` 是稳定机器码，`rc` 是退出码。"""

    def __init__(self, code: str, message: str, rc: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.rc = rc


# ------------------------------------------------------------------ 宿主表
#: 每个宿主**只保存真正不同的那部分**：配置顶层 key、server 条目额外的字段、
#: 格式、落点、确认加载的办法、Skill 入口。启动描述（command / args / env）只有
#: 一份，由 `launch_descriptor()` 给出。文档证明不了的字段一个都不加。
HOSTS: "dict[str, dict]" = {
    "cursor": {
        "label": "Cursor（本地 Agent）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {},
        "timeout": None,
        "evidence": "search_snippet",
        "target": ["<项目>/.cursor/mcp.json（项目级，推荐）", "~/.cursor/mcp.json（全局）"],
        "verify": [
            "Cursor Settings → MCP / Tools & MCP：tavotto 显示为已连接、列出工具",
            "在 Agent 对话里让它调用 tavotto_health，看到引擎版本与允许的项目根",
        ],
        "skill": "native",
        "skill_dirs": [
            "<项目>/.cursor/skills/tavotto-figure/",
            "<项目>/.agents/skills/tavotto-figure/",
        ],
    },
    "zcode": {
        "label": "ZCode（本地 MCP）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {},
        "timeout": None,
        "evidence": "search_snippet",
        "target": [
            "<项目>/.agents/mcp.json（注意：同一作用域的 .zcode 配置里只要定义了任何 MCP"
            " 服务，这个文件就整份被跳过）",
            "或 ZCode 的 MCP 设置界面手动添加",
        ],
        "verify": [
            "ZCode Settings 里 tavotto 为已连接状态并能看到工具",
            "在对话里让它调用 tavotto_health",
        ],
        "skill": "instruction_fallback",
        "skill_dirs": [],
    },
    "dsh": {
        "label": "DeepSeek Harness（@deepseek-ai/dsh-mcp-client，stdio）",
        "format": "dsh_yaml",
        "top_key": None,
        "extra": {},
        # dsh-mcp-client 的 toolCallTimeoutMs：毫秒，默认 60000——导出大图不够
        "timeout": ("toolCallTimeoutMs", "ms"),
        "evidence": "official_source",
        "target": [
            "单次：dsh web --patch tavotto.cordis.yml",
            "长期：合并进 $DSH_HOME/profiles/<名字>/cordis.patch.yml（或 $DSH_HOME/cordis.patch.yml；"
            "$DSH_HOME 默认 ~/.dsh）",
        ],
        "verify": [
            "新开一个 DSH 会话，等 mcp__tavotto__* 工具出现（发现是异步的）",
            "调用 mcp__tavotto__tavotto_health",
        ],
        "skill": "native",
        "skill_dirs": [
            "<项目>/.dsh/skills/tavotto-figure/",
            "<项目>/.agents/skills/tavotto-figure/",
        ],
    },
    "workbuddy": {
        "label": "WorkBuddy（本地 MCP）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {},
        "timeout": None,
        "evidence": "search_snippet",
        "target": [
            "WorkBuddy：插件 → MCP Server → 配置 MCP（编辑 mcp.json），只合并 tavotto 这一项",
            "（不要改 CodeBuddy 的 ~/.codebuddy/mcp.json——那是另一个产品）",
        ],
        "verify": ["WorkBuddy 的 MCP 列表里 tavotto 为已连接，对话里调用 tavotto_health"],
        "skill": "instruction_fallback",
        "skill_dirs": [],
    },
    "claude-code": {
        "label": "Claude Code（本地 CLI）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {"type": "stdio"},
        # .mcp.json 的 per-server timeout：毫秒，下限 1000
        "timeout": ("timeout", "ms"),
        "evidence": "official_source",
        "target": [
            "<项目>/.mcp.json（project 作用域；交互会话里第一次用会请你批准）",
            "不要同时再用 claude mcp add 登记同名 tavotto：local / user 作用域会遮蔽它",
        ],
        "verify": [
            "在 Claude Code 里运行 /mcp：tavotto 为 connected，并能看到工具",
            "让它调用 tavotto_health：structuredContent.server.package_dir 应是这份包",
        ],
        "skill": "native",
        "skill_dirs": ["<项目>/.claude/skills/tavotto-figure/", "~/.claude/skills/tavotto-figure/"],
    },
    "claude-desktop": {
        "label": "Claude Desktop（本地聊天）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {},
        "timeout": None,
        "evidence": "official_source",
        # 官方排障：Windows 上 Claude Desktop 给 server 的环境里可能没有 APPDATA，
        # 要在 env 里写展开后的值。Tavotto 的配置目录在 Windows 上正是 %APPDATA%\Tavotto。
        "windows_env": ("APPDATA",),
        "target": [
            "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json",
            "Windows: %APPDATA%\\Claude\\claude_desktop_config.json",
            "（Settings → Developer → Edit Config 打开的就是这份；已有 mcpServers 就只加 tavotto 一项）",
        ],
        "verify": [
            "完全退出并重开 Claude Desktop；Settings → Developer 里 tavotto 为 running",
            "对话框「+」→ Connectors 里能看到 tavotto；让它调用 tavotto_health",
            "日志：macOS ~/Library/Logs/Claude/mcp*.log，Windows %APPDATA%\\Claude\\logs",
            "不要拿 `claude mcp add` 成功、Code 标签页或 Artifacts 预览当作这里的证据",
        ],
        "skill": "instruction_fallback",
        "skill_dirs": [],
    },
    "trae": {
        "label": "Trae / TraeCode（本地 IDE）",
        "format": "json",
        "top_key": "mcpServers",
        "extra": {},
        "timeout": None,
        "evidence": "search_snippet",
        "target": [
            "Trae：MCP 窗口 → 添加 → 手动添加，粘贴本 JSON",
            "或项目级 <项目>/.trae/mcp.json",
        ],
        "verify": [
            "MCP 列表里 tavotto 为已连接、能展开工具",
            "把 tavotto 加进你要用的智能体（自定义智能体的 MCP 一栏，或 Builder with MCP）"
            "——只登记不等于该智能体能调用",
            "在该智能体里调用 tavotto_health；CN 版 / 国际版、IDE / SOLO 各自单独验",
        ],
        "skill": "instruction_fallback",
        "skill_dirs": [],
    },
    "vscode": {
        "label": "VS Code（GitHub Copilot Agent 模式的原生 MCP）",
        "format": "json",
        "top_key": "servers",
        "extra": {"type": "stdio"},
        "timeout": None,
        "evidence": "official_source",
        "target": [
            "<项目>/.vscode/mcp.json（工作区级；顶层是 servers，不是 mcpServers）",
            "不要写进工作区根的 .mcp.json：那是另一种 schema（mcpServers），也是 Claude Code 的项目文件",
        ],
        "verify": [
            "命令面板 MCP: List Servers → tavotto → Show Output，状态为 Running（受限模式的工作区不会启动）",
            "Chat 切到 Agent 模式，Configure Tools 里勾上 tavotto 的工具，调用 tavotto_health",
            "内嵌画布需要 chat.mcp.apps.enabled；组织策略禁用时请找管理员，本工具不替你改设置",
            "终端里跑 claude / 装了 Claude 扩展都不是 VS Code Copilot 的证据",
        ],
        "skill": "native",
        "skill_dirs": [
            "<项目>/.github/skills/tavotto-figure/",
            "<项目>/.agents/skills/tavotto-figure/",
        ],
    },
}

#: 本工具承诺覆盖的宿主闭集（测试里有一份独立书写的期望对拍，漏一个会红）
HOST_IDS = tuple(HOSTS)


# ------------------------------------------------------------------ 包与路径
def package_state() -> dict:
    """这份完整包自己的状态：启动器 / 清单 / 画布 / Skill 在不在，版本多少。"""
    version = None
    try:
        with open(PLUGIN_JSON, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        v = data.get("version") if isinstance(data, dict) else None
        version = v if isinstance(v, str) and v.strip() else None
    except (OSError, ValueError):
        pass
    return {
        "dir": PACKAGE_DIR,
        "version": version,
        "launcher": os.path.isfile(SERVER),
        "missing_runtime": [p for p in RUNTIME_FILES if not os.path.isfile(p)],
        "skill": os.path.isfile(os.path.join(SKILL_DIR, "SKILL.md")),
        "canvas": os.path.isfile(CANVAS) and os.path.getsize(CANVAS) > 0,
        # 发行件（staging / zip 解包）才有构建清单；源码 checkout 没有，画布也没有
        "release_build": os.path.isfile(BUILD_MANIFEST),
    }


def _within(path: str, root: str) -> bool:
    try:
        common = os.path.commonpath([path, root])
    except ValueError:  # Windows 跨盘符
        return False
    return os.path.normcase(common) == os.path.normcase(root)


def _is_fs_root(path: str) -> bool:
    return os.path.normcase(os.path.dirname(path)) == os.path.normcase(path)


def _home_dirs() -> "list[str]":
    """当前账户的主目录（规范路径）。**不只信环境变量**：`env -i` 或服务启动器下
    HOME / USERPROFILE 可能不在，那样就一个都查不到、整个主目录被放行（Codex 在 #559 上
    指出）。所以再问操作系统的账户数据库（POSIX 的 pwd），几处都收。"""
    found: "list[str]" = []
    cands = [os.environ.get("HOME"), os.environ.get("USERPROFILE")]
    try:
        import pwd  # noqa: PLC0415 — Windows 上没有

        cands.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, AttributeError):
        pass
    expanded = os.path.expanduser("~")
    if expanded != "~":
        cands.append(expanded)
    for cand in cands:
        if cand and os.path.isdir(cand):
            real = os.path.realpath(cand)
            if real not in found:
                found.append(real)
    return found


def validate_project_root(raw: str) -> str:
    """用户选的项目目录 → 规范绝对路径；不成立就抛 `ConfigureError`。

    与 `RootAuthority._paths_from_config` 同样拒绝文件系统根与包目录；此外拒绝
    HOME 本身——配置是生成给宿主长期用的，「整个用户目录」不是一个项目。
    """
    if not raw or not raw.strip():
        raise ConfigureError("bad_project_root", "--project-root 不能为空")
    if raw.strip().startswith("~"):
        # 宿主不做 ~ 展开；这里也不替用户猜是哪个 HOME
        raise ConfigureError(
            "bad_project_root", "--project-root 请给绝对路径（宿主不会展开 ~，本工具也不替你展开）"
        )
    if not os.path.isabs(raw):
        raise ConfigureError("bad_project_root", f"--project-root 必须是绝对路径：{raw}")
    real = os.path.realpath(raw)
    if not os.path.isdir(real):
        raise ConfigureError("bad_project_root", f"--project-root 不存在或不是目录：{raw}")
    if os.pathsep in real:
        # TAVOTTO_MCP_ROOTS 按 os.pathsep 切成多个根：`/tmp/a:/etc` 这样的目录名会被读成
        # 两个根，把用户没选的目录也放进来（Codex 在 #559 上指出）。没有无歧义的写法，只能拒绝。
        raise ConfigureError(
            "bad_project_root",
            f"项目目录的路径里含有 {os.pathsep!r}，它在 {ROOTS_ENV} 里是多个目录的分隔符，"
            "会被拆成别的目录——请换一个路径里没有这个字符的目录",
        )
    if _is_fs_root(real):
        raise ConfigureError("bad_project_root", "不接受文件系统根目录作为授权范围")
    homes = _home_dirs()
    if not homes and os.name != "nt":
        raise ConfigureError(
            "home_unknown",
            "查不到当前账户的主目录，没法确认所选目录没有把整个主目录放进来——请在正常的登录环境里运行",
        )
    for home in homes:
        if _within(home, real):
            raise ConfigureError(
                "bad_project_root",
                "不接受用户主目录（或包含它的上级目录）作为授权范围——请选具体的项目目录"
                "（例如 ~/论文/figures 的绝对路径）",
            )
    if _within(real, os.path.realpath(PACKAGE_DIR)):
        raise ConfigureError("bad_project_root", "项目目录不能在 Tavotto 完整包里面")
    return real


def resolve_python(raw: "str | None") -> str:
    """启动器解释器 → 绝对路径（**不 realpath**：venv 的 python 是符号链接，
    解析掉就换了一个环境）。没给就用正在跑本文件的解释器。"""
    if raw is None:
        return os.path.abspath(sys.executable)
    if not os.path.isabs(raw):
        # 裸名字按 PATH 解析一次，结果写成绝对路径：宿主不过 shell，也不一定有你的 PATH
        import shutil

        found = shutil.which(raw)
        if not found:
            raise ConfigureError("python_not_found", f"PATH 上找不到 {raw}；请给解释器的绝对路径")
        return os.path.abspath(found)
    if not os.path.isfile(raw):
        raise ConfigureError("python_not_found", f"解释器不存在：{raw}")
    return raw


# ------------------------------------------------------------------ 启动探针
def probe_env(project_root: str, engine_python: "str | None") -> "dict[str, str]":
    """探针的环境：只留宿主一般会带的那几个，PATH 换成 GUI 的最小值。"""
    env = {k: v for k, v in os.environ.items() if k in PROBE_ENV_KEEP}
    if os.name == "nt":
        windir = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
        env["PATH"] = os.pathsep.join([os.path.join(windir, "System32"), windir])
    else:
        env["PATH"] = MINIMAL_POSIX_PATH
    env[ROOTS_ENV] = project_root
    env[NO_BYTECODE_ENV] = "1"
    if engine_python:
        env[ENGINE_ENV] = engine_python
    return env


def _last_json(text: str) -> "dict | None":
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return None


def probe_launcher(
    python: str, project_root: str, engine_python: "str | None" = None, env: "dict | None" = None
) -> dict:
    """`<python> <包>/mcp/server.py --health`，在无关的临时 cwd 里、用最小环境跑。

    回 `{"starts": bool, "rc": int|None, "detail": str, "health": dict|None}`。
    退出码 0（引擎可用）与 3（降级）都算**起得来**：降级 server 在宿主里有
    `tavotto_health` 与每个工具名的结构化错误；起不来才是「宿主里一个工具都没有」。
    """
    run_env = env if env is not None else probe_env(project_root, engine_python)
    with tempfile.TemporaryDirectory(prefix="tavotto-configure-") as cwd:
        try:
            proc = subprocess.run(
                [python, SERVER, "--health"],
                cwd=cwd,
                env=run_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PROBE_TIMEOUT,
            )
        except OSError as exc:
            return {"starts": False, "rc": None, "detail": f"起不来：{exc}", "health": None}
        except subprocess.TimeoutExpired:
            return {
                "starts": False,
                "rc": None,
                "detail": f"{PROBE_TIMEOUT}s 内没有回体检结果",
                "health": None,
            }
    health = _last_json(proc.stdout)
    if health is None:
        tail = (proc.stderr or proc.stdout or "").strip()[-240:] or "（零输出）"
        detail = f"退出码 {proc.returncode}，没有体检 JSON：{tail}"
        if proc.returncode == 9009 or (os.name == "nt" and "WindowsApps" in python):
            detail += "——这多半是 Windows 商店的 python 别名：命令存在，但起不来"
        return {"starts": False, "rc": proc.returncode, "detail": detail, "health": None}
    return {
        "starts": True,
        "rc": proc.returncode,
        "detail": "启动器回了体检 JSON",
        "health": health,
    }


# ------------------------------------------------------------------ 启动描述
def launch_descriptor(python: str, project_root: str, engine_python: "str | None" = None) -> dict:
    """所有宿主共用的那一份启动描述。**宿主差异不在这里**。

    * `command` / `args` 都是绝对路径：宿主不过 shell、不展开 ~、cwd 不确定；
    * `env` 只放 Tavotto 需要的：授权根，以及（必要时）引擎解释器。
    """
    env = {ROOTS_ENV: project_root}
    if engine_python:
        env[ENGINE_ENV] = engine_python
    return {"name": SERVER_NAME, "command": python, "args": [SERVER], "env": env}


def tool_timeout_sec() -> "int | None":
    """工具超时的唯一出处：包里 Codex `.mcp.json` 的 `tool_timeout_sec`（秒）。

    其他宿主有自己的字段与单位（DSH `toolCallTimeoutMs`、Claude Code `timeout`
    都是毫秒），序列化时按单位换算，**不把 Codex 的字段名原样抄过去**。
    """
    try:
        with open(os.path.join(PACKAGE_DIR, ".mcp.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        value = data["mcpServers"][SERVER_NAME]["tool_timeout_sec"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, int) and value > 0 else None


def _entry(host: str, desc: dict) -> dict:
    profile = HOSTS[host]
    entry: dict = dict(profile["extra"])
    entry["command"] = desc["command"]
    entry["args"] = list(desc["args"])
    env = dict(desc["env"])
    if IS_WINDOWS:
        for name in profile.get("windows_env", ()):
            value = os.environ.get(name)
            if value:
                env[name] = value
    entry["env"] = env
    timeout = profile.get("timeout")
    seconds = tool_timeout_sec()
    if timeout and seconds:
        field, unit = timeout
        entry[field] = seconds * 1000 if unit == "ms" else seconds
    return entry


def serialize(host: str, desc: dict) -> "dict | list":
    """启动描述 → 这一家宿主的配置对象（只含一个 server 的合并片段）。"""
    profile = HOSTS[host]
    entry = _entry(host, desc)
    if profile["format"] == "dsh_yaml":
        # DSH：Cordis patch——一条 insert，插件名是官方的 @deepseek-ai/dsh-mcp-client
        config = {"serverName": desc["name"], "transport": "stdio"}
        config.update(entry)
        return [
            {
                "insert": [
                    {
                        "id": f"mcp-{desc['name']}",
                        "name": "@deepseek-ai/dsh-mcp-client",
                        "config": config,
                    }
                ]
            }
        ]
    return {profile["top_key"]: {desc["name"]: entry}}


def _yaml(value, indent: int = 0) -> "list[str]":
    """极小的 YAML 发射器：只会本工具产生的形状（dict / list / str / int / bool）。

    字符串一律写成 JSON 双引号标量——那也是合法的 YAML，Windows 反斜杠与中文空格
    路径不会被误读；不产生任何 `!!js` 标签（本工具不生成要宿主执行的代码）。
    """
    pad = "  " * indent
    lines: "list[str]" = []
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.extend(_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)) and item:
                sub = _yaml(item, indent + 1)
                lines.append(f"{pad}- {sub[0].lstrip()}")
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    return lines


def _yaml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dict):
        return "{}"
    if isinstance(v, list):
        return "[]"
    return json.dumps(str(v), ensure_ascii=False)


def render(host: str, config) -> str:
    if HOSTS[host]["format"] == "dsh_yaml":
        return "\n".join(_yaml(config)) + "\n"
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


# ------------------------------------------------------------------ 主流程
def _engine_summary(health: "dict | None") -> dict:
    if not health:
        return {"ok": False}
    out = {
        "ok": bool(health.get("ok")),
        "mode": health.get("mode"),
        "python": health.get("python"),
        "source": health.get("source"),
        "version": health.get("engine_version"),
    }
    if not health.get("ok"):
        out["code"] = health.get("code")
        out["error"] = health.get("error")
        out["recovery"] = health.get("recovery") or []
    return out


def build(host: str, project_root: str, python: "str | None", engine_python: "str | None") -> dict:
    """参数 → 完整结论（配置对象 + 诊断）。不打印、不写盘，测试直接调它。"""
    if host not in HOSTS:
        raise ConfigureError("bad_host", f"不认识的 --host {host!r}；可选：{', '.join(HOST_IDS)}")
    pkg = package_state()
    if not pkg["launcher"]:
        raise ConfigureError("package_incomplete", f"包里没有启动器 {SERVER}", rc=3)
    if pkg["missing_runtime"]:
        raise ConfigureError(
            "package_incomplete",
            "包不完整（解压中断？）：缺 "
            + "、".join(pkg["missing_runtime"])
            + "——请重新解压完整包",
            rc=3,
        )
    root = validate_project_root(project_root)
    if engine_python:
        # 显式引擎解释器**直接当启动命令**：启动器先试「当前解释器」，只把它塞进
        # TAVOTTO_MCP_PYTHON 的话，启动器解释器自己装着引擎时它会被静默忽略
        # （Codex 在 #559 上指出）。它能跑纯标准库的启动器是当然的。
        if python and resolve_python(python) != resolve_python(engine_python):
            raise ConfigureError(
                "bad_args", "--engine-python 会直接作为启动命令，不要再给另一个 --python"
            )
        return _build_with_engine(host, root, resolve_python(engine_python), pkg)
    launcher_python = resolve_python(python)
    probe = probe_launcher(launcher_python, root)
    if not probe["starts"]:
        raise ConfigureError(
            "launcher_unstartable",
            f"{launcher_python} 跑不起 Tavotto 启动器：{probe['detail']}。"
            "请用 --python 指一个真的能运行的 Python 3.8+ 的绝对路径"
            "（Windows 上不要用 WindowsApps 下的 python.exe 别名）。",
            rc=3,
        )
    engine = _engine_summary(probe["health"])
    pinned = None
    candidate = None
    if engine["ok"] and engine.get("source") in ENV_DEPENDENT_SOURCES:
        candidate = engine.get("python")
    elif not engine["ok"]:
        # 最小环境里找不到引擎，但你的 shell 里也许有（pipx 的 ~/.local/bin、conda
        # 激活的环境……）。用完整环境再问一次。只收「一个具体的解释器」这种结论：
        # 靠 PYTHONPATH 才 import 得到的（来源 current）钉了也没用，宿主没有那个变量。
        full = dict(os.environ)
        full[ROOTS_ENV] = root
        full[NO_BYTECODE_ENV] = "1"
        found = _engine_summary(probe_launcher(launcher_python, root, env=full)["health"])
        if found["ok"] and found.get("source") in ENV_DEPENDENT_SOURCES:
            candidate = found.get("python")
    if candidate:
        # 引擎是靠这一刻 shell 的 PATH / 环境变量找到的：宿主（尤其从 Dock / 开始菜单
        # 启动的 GUI）多半没有它——钉住，并在最小环境里再验一遍钉住之后真的可用。
        recheck = _engine_summary(probe_launcher(launcher_python, root, candidate)["health"])
        if recheck["ok"]:
            pinned, engine = candidate, recheck
    desc = launch_descriptor(launcher_python, root, pinned)
    if engine["ok"]:
        _require_server_starts(desc)
    return _result(host, pkg, launcher_python, probe, engine, pinned, root, desc)


def _build_with_engine(host: str, root: str, engine_python: str, pkg: dict) -> dict:
    """`--engine-python`：用它直接启动，并验证体检报的就是它（来源 current）。"""
    probe = probe_launcher(engine_python, root)
    engine = _engine_summary(probe["health"])
    same = engine.get("python") and os.path.normcase(
        os.path.abspath(engine["python"])
    ) == os.path.normcase(os.path.abspath(engine_python))
    if not probe["starts"] or not engine["ok"] or engine.get("source") != "current" or not same:
        raise ConfigureError(
            "engine_python_unusable",
            f"--engine-python {engine_python} 不能直接跑出引擎（体检来源 "
            f"{engine.get('source')!r}）：它多半 import 不了 tavotto.engine",
            rc=3,
        )
    desc = launch_descriptor(engine_python, root, None)
    _require_server_starts(desc)
    return _result(host, pkg, engine_python, probe, engine, None, root, desc)


def _require_server_starts(desc: dict) -> None:
    """按这份启动描述真起一次 server 做 initialize：要回的是**真 server**（serverInfo.version
    不是降级的 "0"）。`--health` 不 import `tavotto_mcp`，只有握手才证明整条启动路径通。"""
    env = probe_env(desc["env"][ROOTS_ENV], desc["env"].get(ENGINE_ENV))
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "tavotto-configure", "version": "1"},
        },
    }
    with tempfile.TemporaryDirectory(prefix="tavotto-configure-") as cwd:
        try:
            proc = subprocess.run(
                [desc["command"], *desc["args"]],
                input=json.dumps(msg) + "\n",
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigureError("server_unstartable", f"MCP server 起不来：{exc}", rc=3) from None
    reply = _last_json(proc.stdout) or {}
    version = ((reply.get("result") or {}).get("serverInfo") or {}).get("version")
    if version in (None, "0"):
        tail = (proc.stderr or "").strip()[-300:] or "（没有输出）"
        raise ConfigureError(
            "server_unstartable",
            f"体检说引擎可用，但按这份配置起的 MCP server 没有正常握手（退出码 {proc.returncode}）：{tail}",
            rc=3,
        )


def _result(host, pkg, launcher_python, probe, engine, pinned, root, desc) -> dict:
    config = serialize(host, desc)
    return {
        "host": host,
        "profile": HOSTS[host]["label"],
        "package": pkg,
        "launcher": {
            "python": launcher_python,
            "starts": True,
            "rc": probe["rc"],
            "detail": probe["detail"],
        },
        "engine": engine,
        "engine_pinned": pinned,
        "project_root": root,
        "descriptor": desc,
        "config": config,
        "target": HOSTS[host]["target"],
        "verify": HOSTS[host]["verify"],
        "skill": {
            "mode": HOSTS[host]["skill"],
            "source": SKILL_DIR,
            "install_to": HOSTS[host]["skill_dirs"],
        },
    }


def _shell_join(argv: "list[str]") -> str:
    """给人复制粘贴的一行命令（按本机 shell 的引号规则）。"""
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    import shlex

    return shlex.join(argv)


def claude_cli_argv(config: dict) -> "list[str]":
    """与 Claude Code `.mcp.json` **逐字段相同**的 CLI 登记：`claude mcp add-json`。

    不用 `claude mcp add`：它没有按服务器设超时的选项，走那条路会丢掉 `timeout`，
    长时间的渲染 / 导出就退回 Claude Code 的默认超时（Codex 在 #560 上指出）。
    add-json 收的就是 `.mcp.json` 里那一条的 JSON，所以两条路是同一份条目。
    """
    ((name, entry),) = config["mcpServers"].items()
    payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
    return ["claude", "mcp", "add-json", "--scope", "project", name, payload]


def _notes(result: dict) -> "list[str]":
    """给人看的说明（stderr）。"""
    pkg, engine = result["package"], result["engine"]
    lines = [
        f"# Tavotto {pkg['version'] or '?'} → {result['profile']}",
        f"# 完整包：{pkg['dir']}",
        "# 合并到：" + " / ".join(result["target"]),
        "#   只合并 tavotto 这一项；本工具不写任何文件，不覆盖你已有的 MCP 服务。",
        f"# 授权目录：{result['project_root']}（只有它；想放开别的目录就重新生成）",
    ]
    if not pkg["canvas"]:
        lines.append("# ! 这份包没有画布产物（源码 checkout？）——工具照常可用，内嵌画布不可用")
    if engine["ok"]:
        lines.append(
            f"# 引擎：tavotto {engine.get('version') or '?'}（{engine.get('source')}：{engine.get('python')}）"
        )
        if result["engine_pinned"]:
            lines.append(
                f"#   已把引擎解释器钉进 {ENGINE_ENV}：它是靠当前 shell 环境找到的，宿主里未必找得到"
            )
    else:
        lines.append(f"# ! 引擎未就绪（{engine.get('code')}）：{engine.get('error') or ''}")
        lines.append(
            "#   配置仍然有效：宿主里会出现 tavotto_health，并说出缺什么。恢复步骤（显式执行）："
        )
        for step in engine.get("recovery") or []:
            lines.append(f"#   - {step}")
    if result["host"] == "claude-code":
        lines.append("# 或者用 CLI 登记（与上面的 .mcp.json 二选一，本工具不替你执行）：")
        lines.append("#   " + _shell_join(claude_cli_argv(result["config"])))
    lines.append("# 确认宿主真的加载了：")
    for step in result["verify"]:
        lines.append(f"#   - {step}")
    skill = result["skill"]
    if skill["mode"] == "native":
        lines.append(
            "# Skill：把整个目录（不是只有 SKILL.md）复制到 "
            + " 或 ".join(skill["install_to"])
            + f"\n#   来源：{skill['source']}"
        )
    else:
        lines.append(
            "# Skill：这个宿主没有经核实的原生 Skill 入口（instruction_fallback）；"
            f"技能正文在 {skill['source']}，本版本不自动生成等价说明"
        )
    return lines


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        prog="configure.py",
        description="生成 Tavotto MCP 的宿主接入片段（stdout），说明写到 stderr。不写任何文件。",
    )
    ap.add_argument("--host", required=True, choices=HOST_IDS, help="目标宿主 profile")
    ap.add_argument(
        "--project-root", required=True, help="用户明确选择的项目目录（绝对路径；唯一授权范围）"
    )
    ap.add_argument("--python", help="MCP 启动器解释器（绝对路径；默认 = 运行本工具的解释器）")
    ap.add_argument(
        "--engine-python",
        help="显式指定能 import tavotto 的引擎解释器（配置直接用它启动；与 --python 二选一）",
    )
    ap.add_argument(
        "--diagnose", action="store_true", help="改为输出一份机器可读的诊断 JSON（不输出配置）"
    )
    args = ap.parse_args(argv)
    try:
        result = build(args.host, args.project_root, args.python, args.engine_python)
    except ConfigureError as exc:
        if args.diagnose:
            print(
                json.dumps({"ok": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False)
            )
        print(f"错误（{exc.code}）：{exc}", file=sys.stderr)
        return exc.rc
    if args.diagnose:
        report = {k: v for k, v in result.items() if k != "config"}
        report["ok"] = True
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    sys.stdout.write(render(args.host, result["config"]))
    print("\n".join(_notes(result)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
