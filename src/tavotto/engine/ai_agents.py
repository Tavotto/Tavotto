"""编码 Agent 的注册表与适配层（纯标准库，Flask 父进程 import）。

这里是**「Tavotto 支持哪些编码 Agent」的唯一权威**：候选探测、启动验证、
无副作用就绪检查、命令构造、流式输出分类、一键安装包名，全部按 Agent 各自
的适配器给出。`ai_bridge` 只负责会话编排（快照 / SSE / diff / revert /
history），`ai_providers` 只负责第三方接口的存取与注入——两边都遍历这里的
`AGENT_REGISTRY`，谁都不再写 `if agent == "codex"` 这种分支。

依赖方向是单向的：

    ai_agents.py  ←  ai_providers.py  ←  ai_bridge.py

`ai_agents` **不 import `ai_providers` / `ai_bridge`**。第三方接口的注入结果
（追加参数 + 追加环境变量）由 `ai_bridge` 算好，经 `RunContext` 传进来——
适配器只管把它拼到自己那套命令行的正确位置上。

路径拼接一律用字符串，不用 pathlib（`os.name` 一变 `Path` 就分派到另一半
实现，连跨平台测这段分支都做不到——与 `pool._candidate_pythons` 同一条约定）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .runtime import CREATE_NO_WINDOW

#: 候选来源。**只是诊断信息，不参与「能不能用」的判断**——能不能用只看
#: `--version` 启动验证的结果。
Source = str
SOURCES: tuple[str, ...] = (
    "custom",  # 用户在设置里指定的可执行文件
    "path",  # 系统 PATH
    "homebrew",  # Homebrew 前缀
    "common_location",  # 各平台常见安装目录（bun / volta / scoop / winget / choco…）
    "npm_global",  # npm 全局前缀
    "chatgpt_bundle",  # macOS ChatGPT 应用内置的 codex
    "codex_desktop_bundled",  # Windows Codex 桌面版自带的 codex（%LOCALAPPDATA%\OpenAI\Codex\bin）
    "windows_alias",  # %LOCALAPPDATA%\Microsoft\WindowsApps 执行别名
    "windows_store",  # MSIX 包体内的真身
    "package_binary",  # npm 包内部的平台原生二进制
)

#: 无副作用就绪检查的上限。到点就当 unknown——设置页绝不能被一个卡住的
#: CLI 拖成转圈。
READINESS_TIMEOUT_S = 10
VERSION_TIMEOUT_S = 10


@dataclass(frozen=True)
class SearchLocation:
    """一个候选目录 + 它的来源标签。"""

    path: str
    source: Source


@dataclass(frozen=True)
class CliCandidate:
    """一个候选可执行文件 + 它是从哪儿翻出来的。"""

    path: str
    source: Source


@dataclass(frozen=True)
class ModelDetail:
    """一个模型型号的展示信息与它自己支持的推理强度（加字段协议：旧前端忽略）。

    `efforts_known` 区分「未知」与「明确没有档位」：False（默认）= 目录里没有这个型号
    （只在 config.toml 里有 / 目录读不到），`efforts` 为空只表示“不知道”，前端回落到
    Agent 级 `efforts`；True = 目录明确给了这个型号，`efforts` 就是它的全部档位，
    为空 = 明确没有档位（不暴露选择、不传 `--effort`）。
    """

    id: str
    display_name: str
    description: str = ""
    efforts: list[str] = field(default_factory=list)
    default_effort: str | None = None
    efforts_known: bool = False


@dataclass(frozen=True)
class ModelCapabilities:
    """该 Agent 当前能提供的模型 / 推理强度选项。

    `models` 仍是型号 id 的有序清单（旧前端只认它）；`model_details` 是并行的
    新字段，按 id 带显示名 / 说明 / 每型号自己的强度档位。
    """

    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    efforts: list[str] = field(default_factory=list)
    default_effort: str | None = None
    model_details: list[ModelDetail] = field(default_factory=list)


@dataclass(frozen=True)
class ReadinessResult:
    """无副作用就绪检查的结论。

    `state` 只有三种：
      ready      — 官方本地状态命令明确说「已登录」
      needs_auth — 官方本地状态命令明确说「未登录」
      unknown    — 不支持 / 超时 / 输出看不懂。**映射为「已安装」**，
                   绝不据此说「可用」，也绝不据此说「需要登录」。
    """

    state: str = "unknown"
    #: 稳定的诊断串（不含任何账号信息）；只在详情页的诊断折叠区显示。
    detail: str | None = None


@dataclass(frozen=True)
class InstallSpec:
    """一键安装的固定规格。**包名写死在适配器里**，绝不来自请求体。"""

    method: str
    package: str


def _effort_supported(details: list[ModelDetail], model: str | None, effort: str | None) -> bool:
    """所选型号在目录里且 `efforts_known` 时，请求档位必须在其档位内（含明确无档位 = 一律不传）。

    没选型号 / 目录里没有该型号 = 未知，沿用请求。Claude 与 Codex 的 `build_command` 共用。
    """
    if not model:
        return True
    d = next((x for x in details if x.id == model), None)
    if d is None or not d.efforts_known:
        return True
    return effort in d.efforts


@dataclass(frozen=True)
class LoginSpec:
    """「官方登录按钮」要启动的那条官方命令。**子命令写死在适配器里**，绝不来自请求体。

    `mode`：
      background — 官方命令自己开浏览器、自己在本机起回调，不需要终端交互；
                   Tavotto 后台启动它、不读它的输出（`codex login`）
      terminal   — 官方命令需要交互，只能交给用户的系统终端（`claude auth login`）

    `account` 是按钮上的账号名（产品名，不翻译）：「登录 ChatGPT」「登录 Claude」。
    Tavotto 不读、不存、不刷新、不转发该命令产生的任何登录凭据。
    """

    argv_suffix: tuple[str, ...]
    mode: str
    account: str
    #: 非空 = 后台命令会把「浏览器没打开时请手动访问」的授权地址打在输出里，Tavotto 只从输出里
    #: 取**第一条主机精确命中这里的 https 地址**（授权地址只含 state / PKCE challenge，不是凭据），
    #: 其余输出一概不存、不进日志。空 = 输出直接丢弃。
    url_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunContext:
    """构造一次任务命令所需的全部输入。"""

    argv: list[str]  # 已通过启动验证的 CLI 启动 argv
    prompt: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    #: 第三方接口的注入（由 ai_bridge 经 ai_providers 算好）
    endpoint_args: list[str] = field(default_factory=list)
    endpoint_env: dict[str, str] = field(default_factory=dict)
    #: 目标脚本（相对 `cwd`）。只有内置执行器要（它的写权限收窄到这一个文件）；两家 CLI 靠提示词指目标，忽略它
    target: str | None = None
    #: 内置执行器的任务（`figure_edit` | `convert_to_editable`）与转换任务要新建的文件名
    task: str = "figure_edit"
    output: str | None = None
    resume_output_sha256: str = ""
    checks_used: int = 0
    #: 内置执行器：`check` 经 stdout/stdin 回调父进程（改图任务只在有 TAF 候选时才给；转换任务恒给）
    check_ipc: bool = False


@dataclass(frozen=True)
class SpawnSpec:
    """→ (完整命令行, 需要追加到环境的变量)。"""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


def toml_string(text: str) -> str:
    """TOML 基本字符串。不用 `json.dumps`：它对非 BMP 字符（🧪）输出 UTF-16 代理对转义，TOML 不接受。
    这里：`\\` `"` 与全部控制字符（含 DEL）转义，其余 Unicode 原样（命令行经 UTF-8 传递）。"""
    out = ['"']
    for ch in text:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif o in (8, 9, 10, 12, 13):
            out.append({8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r"}[o])
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def toml_value(value: object) -> str:
    if isinstance(value, str):
        return toml_string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    raise TypeError(type(value).__name__)


@dataclass(frozen=True)
class FixRunContext:
    """「使用你的 AI 来修复」经 MCP 起 CLI 的输入（ADR 0149 D3）。**这里没有任何密钥**：令牌只以环境变量**名**出现，
    值由调用方放进子进程环境（`SpawnSpec.env` 之外的那一份），绝不进 argv。"""

    argv: list[str]  # 已通过启动验证的 CLI 启动 argv
    prompt: str
    cwd: str
    #: 本地 MCP 修复模式服务的启动命令（可执行文件 + 参数，不含令牌）
    mcp_command: list[str]
    #: MCP 服务在 CLI 配置里的名字；工具在 Claude 里叫 `mcp__<名字>__<工具>`
    mcp_name: str
    #: 令牌所在的环境变量名（CLI 要把它转交给 MCP 子进程）
    token_env: str
    #: 单次工具调用的上限秒数（要比弹卡等用户的上限长）
    tool_timeout_s: int
    #: 硬拦表（`fixtools.readscope.deny_spec()` 的结果）；适配器把它翻成自家的权限语法
    deny: dict = field(default_factory=dict)
    endpoint_args: list[str] = field(default_factory=list)
    endpoint_env: dict[str, str] = field(default_factory=dict)
    #: 用户给这个 Agent 选了第三方接口时的型号（接口自己的型号名）；官方登录态为 None（CLI 用自己的默认）
    model: str | None = None


# ---------------------------------------------------------------------------
# 候选探测（与 Agent 无关的那一半）
# ---------------------------------------------------------------------------
def search_locations(name: str) -> list[SearchLocation]:
    """按平台列出该命令的常见落点（PATH 之外的兜底），带来源标签。

    Windows 上以前一个候选都没有——`shutil.which` 找不到就直接判定「未安装」。
    可 PATH 恰恰是 Windows 最不可靠的地方：npm 全局目录要重开终端才进 PATH，
    从桌面快捷方式启动的进程拿的又是启动那一刻的旧环境块。所以这里把
    npm / winget / scoop / bun / volta / 官方安装器的落点全部直接翻一遍。
    """
    home = os.path.expanduser("~")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or home + r"\AppData\Roaming"
        local = os.environ.get("LOCALAPPDATA") or home + r"\AppData\Local"
        programs = os.environ.get("ProgramFiles") or r"C:\Program Files"
        raw: list[tuple[str, str]] = [
            (appdata + r"\npm", "npm_global"),  # npm 全局（最常见）
            (local + r"\npm", "npm_global"),
            # 微软商店 / MSIX 安装（OpenAI.Codex 就是这么发的）。真身在
            # C:\Program Files\WindowsApps\OpenAI.Codex_…\app，那个目录受 ACL
            # 保护、普通进程连列都列不了——**能用的入口是这里的执行别名**
            # （0 字节 reparse point，只能执行不能读）。少了这一条，商店版
            # codex 在系统里就是「找不到」。
            (local + r"\Microsoft\WindowsApps", "windows_alias"),
            (home + r"\.local\bin", "common_location"),  # 官方安装脚本
            (home + r"\.bun\bin", "common_location"),
            (local + r"\Volta\bin", "common_location"),
            (home + r"\scoop\shims", "common_location"),
            (local + r"\Microsoft\WinGet\Links", "common_location"),
            (r"C:\ProgramData\chocolatey\bin", "common_location"),
            (local + rf"\Programs\{name}", "common_location"),
            (local + rf"\Programs\{name}\bin", "common_location"),
            (programs + r"\nodejs", "common_location"),
            (home + r"\.codex\bin", "common_location"),
            (home + r"\.claude\bin", "common_location"),
            (home + rf"\.{name}\bin", "common_location"),
        ]
        return [SearchLocation(p, s) for p, s in raw]
    raw = [
        ("/opt/homebrew/bin", "homebrew"),
        ("/usr/local/bin", "common_location"),
        ("/usr/bin", "common_location"),
        (f"{home}/.local/bin", "common_location"),
        (f"{home}/.bun/bin", "common_location"),
        (f"{home}/.volta/bin", "common_location"),
        (f"{home}/.npm-global/bin", "npm_global"),
        (f"{home}/.{name}/bin", "common_location"),
        ("/opt/homebrew/opt/node/bin", "homebrew"),
    ]
    return [SearchLocation(p, s) for p, s in raw]


def _is_windows() -> bool:
    """单独一个函数：Windows 专属的候选要能在 POSIX 用例里走到（与 codexinstall 同一做法）。"""
    return os.name == "nt"


def desktop_codex_glob(localappdata: str | None) -> str:
    """Codex 桌面版自带 CLI 的落点模式（也是 `searched` 诊断里写出来的那一行）。"""
    return os.path.join(
        localappdata or "%LOCALAPPDATA%", "OpenAI", "Codex", "bin", "*", "codex.exe"
    )


def desktop_codex_candidates(localappdata: str | None) -> list[str]:
    """Codex 桌面版（Windows）自带的 CLI：`<LOCALAPPDATA>\\OpenAI\\Codex\\bin\\<哈希>\\codex.exe`。

    **唯一实现**：`tavotto codex install` 的 `codexinstall.find_codex()` 与 AI 桥
    Codex 适配器的 `extra_candidates()` 都从这里取（#722 / #726），两边的发现口径
    不许各写一份。

    桌面版每次更新换一个哈希目录，旧目录可能还留着——按修改时间**新到旧**排，调用方逐个
    跑 `--version`，第一个起得来的才用（与「能不能用只看启动验证」同一条判据）。
    这里只列文件、不判能不能跑；单个条目读不了 mtime 就跳过它，不连累其余的。
    """
    if not localappdata:
        return []
    import glob as _glob

    hits: list[tuple[float, str]] = []
    # 只让哈希那一段当通配：LOCALAPPDATA 本身可能含 `[` 之类的字面字符（改道过的用户目录），
    # 当成模式就一个都找不到（Codex #727）。`searched` 里写的仍是 `desktop_codex_glob` 那一行
    base = _glob.escape(os.path.join(localappdata, "OpenAI", "Codex", "bin"))
    for p in _glob.glob(os.path.join(base, "*", "codex.exe")):
        try:
            if os.path.isfile(p):
                hits.append((os.path.getmtime(p), p))
        except OSError:
            continue
    hits.sort(key=lambda t: t[0], reverse=True)
    return [p for _m, p in hits]


def search_dirs(name: str) -> list[str]:
    """`search_locations` 的路径视图（`spawn_env` 补 PATH 时用）。"""
    return [loc.path for loc in search_locations(name)]


def resolve_shim(path: str) -> list[str] | None:
    """npm 的 `.cmd`/`.ps1` 外壳 → 真正的可执行文件（Windows）。

    npm 全局安装留下的是 `codex.cmd` 这类批处理外壳，经 cmd.exe 中转会带来
    参数再解析问题：提示词里的 `%`、`&`、`^`、`<`、`>`、`|` 会被 cmd 吃掉或
    截断——中文提示里写个「透明度调到 50%」就足以让任务跑错。外壳内部指向的
    平台原生 `.exe`（或 `xxx.js` + node）拿出来直接跑，就完全绕开了 cmd.exe。
    """
    p = Path(path)
    if p.suffix.lower() not in (".cmd", ".bat", ".ps1"):
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    base = p.parent
    for m in re.finditer(r'["\']?%~?dp0%?[\\/]?([^"\'\s]+\.(?:exe|js))', text, re.I):
        target = (base / m.group(1).replace("\\", "/")).resolve()
        if not target.is_file():
            continue
        if target.suffix.lower() == ".exe":
            return [str(target)]
        node = shutil.which("node")
        if node:
            return [node, str(target)]
    return None


def spawn_env(cli_path: str | None, extra: dict | None = None) -> dict:
    """CLI 子进程的环境：把常见安装目录并进 PATH（不改排序、只补缺）。

    桌面壳从 Finder / 开始菜单启动时继承的是 GUI 的最小 PATH，里面没有
    /opt/homebrew/bin 这类目录：npm shim 的 `#!/usr/bin/env node` 在子进程里
    解析不到 node，报 `env: node: No such file or directory`——CLI 明明装着、
    路径也找到了，一启动就死。把 CLI 自己所在目录 + 各常见安装目录接到
    PATH 末尾，`env node` 就能像在用户终端里一样解析。
    """
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    home = os.path.expanduser("~")
    candidates = []
    if cli_path:
        candidates.append(os.path.dirname(cli_path))
    candidates += search_dirs("node")
    if os.name != "nt":
        # nvm 没有稳定的 current 目录：把装过的版本目录挑最新的补上
        import glob as _glob

        vers = sorted(_glob.glob(home + "/.nvm/versions/node/*/bin"))
        if vers:
            candidates.append(vers[-1])
    for d in candidates:
        if d and d not in parts and os.path.isdir(d):
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts)
    if extra:
        env.update(extra)
    return env


def probe_version_detailed(argv: list[str]) -> tuple[str | None, str | None]:
    """真的把它启动一次（`--version`）→ (版本串, 失败原因)。

    **只有这一步成功才算「装了」。** 失败原因分开报，是因为「探测超时」与
    「这个文件根本不是可执行文件」对用户是两件事：前者建议重试，后者是路径
    填错了。两者都不该覆盖用户原来有效的设置。
    """
    try:
        out = subprocess.run(
            [*argv, "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=spawn_env(argv[-1]),
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "launch_failed"
    # 退出码非零不算起得来——哪怕它打了一行字（坏掉的桌面版 codex.exe 会打错误信息再退出）。与
    # `codexinstall.find_codex` 的「rc == 0 才算」同一条判据，两条路对「哪一份能用」回答相同（Codex #727）
    if out.returncode != 0:
        return None, "launch_failed"
    line = (out.stdout or out.stderr).strip().splitlines()
    if not line:
        return None, "launch_failed"
    return line[0][:80], None


def probe_version(argv: list[str]) -> str | None:
    return probe_version_detailed(argv)[0]


def _run_probe(argv: list[str], timeout: int = READINESS_TIMEOUT_S) -> tuple[int, str] | None:
    """跑一条**无副作用**的本地状态命令；→ (返回码, 合并输出) 或 None。

    `stdin=DEVNULL` 是硬要求：任何一条命令一旦想问点什么，我们要它当场失败
    而不是把设置页挂死在那儿等输入。
    """
    try:
        out = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=spawn_env(argv[0]),
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.returncode, ((out.stdout or "") + "\n" + (out.stderr or ""))


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class AgentDefinition:
    """一个编码 Agent 的全部特有知识。子类只覆写自己需要的那几处。"""

    id: str = ""
    display_name: str = ""
    #: 前端 `AgentIcon` 用的稳定图标键（不是路径、不是资源地址）
    icon_key: str = ""
    #: 可执行文件名（按优先级）；探测会逐个翻
    command_names: tuple[str, ...] = ()
    #: 第三方接口的协议族；None = 该 Agent 不支持接第三方接口
    endpoint_family: str | None = None
    #: 一键安装规格；None = 不给安装按钮
    install_spec: InstallSpec | None = None
    supports_model_selection: bool = False
    supports_effort_selection: bool = False
    #: 档位来自官方目录：接第三方接口（网关）时网关后面的型号未必认，界面不给档位、命令不传。
    #: 默认 False（Codex 现状不变，网关下仍可传）
    effort_official_only: bool = False
    #: 该族的第三方接口要不要选 wire api（OpenAI 兼容那一族才有 responses/chat
    #: 两种协议）。**由适配器声明**，前端不该靠 `id === "codex"` 猜。
    supports_wire_api: bool = False
    #: 有没有官方的、无副作用的本地登录状态命令
    supports_readiness_probe: bool = False
    #: 内部执行器（Tavotto 自己的进程，没有可探测的 CLI）：不进 `AGENT_REGISTRY`、不出现在 capabilities 的 `agents[]`，
    #: 只经 `get_agent` 被改图执行路径找到（ADR 0147 第二阶段）
    internal: bool = False

    # -- 探测 --------------------------------------------------------------
    def extra_search_locations(self) -> list[SearchLocation]:
        """该 Agent 独有的落点（排在通用落点之后）。"""
        return []

    def extra_candidates(self) -> list[CliCandidate]:
        """该 Agent 独有的**具体可执行文件**（落点不是一个固定目录、要按规则挑的那种）。

        排在全部目录落点之后、npm 包内二进制与 MSIX 包体兜底之前；与其它候选一样要过
        `--version` 启动验证才算数。
        """
        return []

    def extra_searched(self) -> list[str]:
        """`extra_candidates` 找过的位置（写进 `diagnostics.searched`，找不到时告诉用户找过哪儿）。"""
        return []

    # -- 能力 --------------------------------------------------------------
    def model_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

    # -- 就绪 --------------------------------------------------------------
    def readiness(self, argv: list[str]) -> ReadinessResult:
        """无副作用就绪检查。**默认什么都不做**——不支持就老实说 unknown。

        允许做的事只有一件：跑官方 CLI 明确提供的本地状态命令。不发模型
        请求、不建会话、不产生 Token 或费用、不改登录状态、严格超时。
        **绝不因为「配置目录存在」就宣布已登录。**
        """
        return ReadinessResult()

    # -- 登录 --------------------------------------------------------------
    def login_spec(self) -> LoginSpec | None:
        """官方登录命令的规格。**默认不支持**——没有官方登录子命令就不画按钮。"""
        return None

    # -- 运行 --------------------------------------------------------------
    def build_command(self, ctx: RunContext) -> SpawnSpec:
        raise NotImplementedError

    def build_fix_command(self, ctx: FixRunContext) -> SpawnSpec | None:
        """AI 修复会话里起 CLI 的命令（MCP 接法，ADR 0149 D3）。**默认 None = 不支持**。"""
        return None

    def fix_event_parser(self) -> Callable[[str], list[dict]] | None:
        """修复会话读 CLI 结构化输出（stdout 一行一个事件）用的解析器工厂；每次会话取一个新的（可带状态）。
        解析器把一行变成 0..n 个 `{"kind": "mcp_begin"|"mcp_end"|"approval"|"denied"|"output", "tool": 工具名(不带前缀)}`。
        **事件只用来显示进度和发现卡住，从不当结论**（结论只认 `finish` 工具）。默认 None = 不解析。"""
        return None

    def classify_event(self, line: str, state: dict) -> list[tuple[str, str]]:
        raise NotImplementedError


class CodexAgent(AgentDefinition):
    id = "codex"
    display_name = "Codex"
    icon_key = "codex"
    command_names = ("codex",)
    endpoint_family = "openai"
    install_spec = InstallSpec(method="npm", package="@openai/codex")
    supports_model_selection = True
    supports_effort_selection = True
    supports_wire_api = True
    supports_readiness_probe = True

    def login_spec(self) -> LoginSpec | None:
        # `codex login` 自己开浏览器、自己在本机起回调：后台启动即可
        return LoginSpec(("login",), "background", "ChatGPT", ("auth.openai.com",))

    #: 推理强度档位。**模型名故意不写死**——OpenAI 换代时旧名会被服务端直接
    #: 拒绝（"The 'gpt-5' model is not supported when using Codex with a
    #: ChatGPT account."），本机 config.toml 里用户自己选定的才是可用的。
    EFFORTS = ["minimal", "low", "medium", "high", "max"]

    def extra_search_locations(self) -> list[SearchLocation]:
        if sys.platform != "darwin":
            # Linux 上没有 /Applications 这套布局；Windows 版 ChatGPT 不带 codex
            return []
        # macOS 的 ChatGPT 桌面应用自带一份能用的 codex CLI（issue #89）——
        # 它不在 PATH 上，装了 ChatGPT 却没单独装 codex 的用户以前会被判成
        # 「未安装」。排在常规安装位置之后：单独装的 codex 通常更新。
        # 候选与其它落点一样要过 --version 启动验证才算数。
        home = os.path.expanduser("~")
        return [
            SearchLocation("/Applications/ChatGPT.app/Contents/Resources", "chatgpt_bundle"),
            SearchLocation(home + "/Applications/ChatGPT.app/Contents/Resources", "chatgpt_bundle"),
        ]

    def extra_candidates(self) -> list[CliCandidate]:
        if not _is_windows():
            return []
        # Windows 上只装了 Codex 桌面版的用户，唯一一份 codex 在
        # %LOCALAPPDATA%\OpenAI\Codex\bin\<哈希>\codex.exe、不在 PATH 上（#726）。
        # 与 `tavotto codex install` 同一份探测（desktop_codex_candidates），新到旧；
        # 旧哈希目录里那份起不来时 resolve() 自然落到下一个。
        local = os.environ.get("LOCALAPPDATA")
        return [CliCandidate(p, "codex_desktop_bundled") for p in desktop_codex_candidates(local)]

    def extra_searched(self) -> list[str]:
        if not _is_windows():
            return []
        return [desktop_codex_glob(os.environ.get("LOCALAPPDATA"))]

    # -- codex 的模型清单来自它自己的 config.toml ---------------------------
    @staticmethod
    def _home() -> Path:
        """codex 自己的配置目录（CODEX_HOME 是其官方覆盖变量，测试也用它重定向）。"""
        return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))

    @classmethod
    def _config(cls) -> dict:
        """从 codex 的 config.toml 读模型与默认推理强度。

        顶层 model 是用户当前在用的，profiles 里的作为备选一并列出。读不到就
        返回空——前端只显示「跟随 CLI 默认」，绝不给一个我们猜的模型名。
        """
        out: dict = {"models": [], "default_model": None, "default_effort": None}
        try:
            text = (cls._home() / "config.toml").read_text(encoding="utf-8")
        except OSError:
            return out
        try:
            import tomllib  # 3.11+

            data = tomllib.loads(text)
            out["default_model"] = data.get("model") or None
            out["default_effort"] = data.get("model_reasoning_effort") or None
            names = [data.get("model")]
            profiles = data.get("profiles")
            if isinstance(profiles, dict):
                names += [p.get("model") for p in profiles.values() if isinstance(p, dict)]
        except (ImportError, ValueError):
            # tomllib 缺席（3.10）或 TOML 有语法问题：退化成逐行取值，
            # 首个 model 视为默认。宁可少给，也不要报错让整个面板不可用。
            names = re.findall(r'^\s*model\s*=\s*["\']([^"\']+)["\']', text, re.M)
            efforts = re.findall(
                r'^\s*model_reasoning_effort\s*=\s*["\']([^"\']+)["\']', text, re.M
            )
            out["default_model"] = names[0] if names else None
            out["default_effort"] = efforts[0] if efforts else None
        seen: list[str] = []
        for n in names:
            if isinstance(n, str) and n and n not in seen:
                seen.append(n)
        out["models"] = seen
        return out

    @classmethod
    def _catalog(cls) -> list[ModelDetail]:
        """codex CLI 自己的模型目录缓存 `$CODEX_HOME/models_cache.json` 里 `visibility == "list"` 的型号。

        只读、只取我们认得的字段；文件缺失 / 损坏 / 结构变了一律返回空表（调用方退回
        只用 config.toml 的老行为），绝不抛。`hide` 的型号（内部 / 已下架）不列。
        按 `priority` 升序（缺省沿用原序，稳定排序）。
        """
        try:
            data = json.loads((cls._home() / "models_cache.json").read_text(encoding="utf-8"))
            raw = data["models"]
            if not isinstance(raw, list):
                return []
        except (OSError, ValueError, KeyError, TypeError):
            return []
        rows: list[tuple[float, int, ModelDetail]] = []
        for i, m in enumerate(raw):
            if not isinstance(m, dict) or m.get("visibility") != "list":
                continue
            slug = m.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            name = m.get("display_name")
            desc = m.get("description")
            levels = m.get("supported_reasoning_levels")
            efforts: list[str] = []
            if isinstance(levels, list):
                for lv in levels:
                    e = lv.get("effort") if isinstance(lv, dict) else lv
                    if isinstance(e, str) and e and e not in efforts:
                        efforts.append(e)
            default = m.get("default_reasoning_level")
            prio = m.get("priority")
            has_prio = isinstance(prio, (int, float)) and not isinstance(prio, bool)
            rows.append(
                (
                    float(prio) if has_prio else float("inf"),
                    i,
                    ModelDetail(
                        id=slug,
                        display_name=name if isinstance(name, str) and name else slug,
                        description=desc if isinstance(desc, str) else "",
                        efforts=efforts,
                        default_effort=default if isinstance(default, str) and default else None,
                        efforts_known=True,
                    ),
                )
            )
        rows.sort(key=lambda r: (r[0], r[1]))
        return [r[2] for r in rows]

    def model_capabilities(self) -> ModelCapabilities:
        cfg = self._config()
        catalog = self._catalog()
        by_id = {d.id: d for d in catalog}
        # config.toml 当前在用的（含 profiles）在前，其后是 CLI 目录里可选的型号
        ids = list(cfg["models"])
        for d in catalog:
            if d.id not in ids:
                ids.append(d.id)
        details = [by_id.get(i) or ModelDetail(id=i, display_name=i) for i in ids]
        efforts = list(self.EFFORTS)
        extra = [cfg["default_effort"]] + [e for d in details for e in d.efforts]
        for e in extra:
            if e and e not in efforts:
                efforts.append(e)
        return ModelCapabilities(
            models=ids,
            default_model=cfg["default_model"],
            efforts=efforts,
            default_effort=cfg["default_effort"] or "medium",
            model_details=details,
        )

    def readiness(self, argv: list[str]) -> ReadinessResult:
        """`codex login status` —— 官方的本地状态子命令，只读 auth 文件。

        它**不是** `codex login`：后者会开浏览器登录（那是副作用）。子命令
        名写死在这里，永远不拼用户输入。老版本没有这个子命令时会以非 0 退出
        并打印用法，正好落到 unknown。
        """
        probe = _run_probe([*argv, "login", "status"])
        if probe is None:
            return ReadinessResult("unknown", "probe_failed")
        code, text = probe
        low = text.lower()
        # 「not logged in」必须先判：它自身含有 "logged in" 子串
        if "not logged in" in low or "not signed in" in low:
            return ReadinessResult("needs_auth", "cli_reports_signed_out")
        if code == 0 and ("logged in" in low or "signed in" in low):
            return ReadinessResult("ready", "cli_reports_signed_in")
        return ReadinessResult("unknown", "unrecognised_output")

    def build_command(self, ctx: RunContext) -> SpawnSpec:
        cmd = [
            *ctx.argv,
            "exec",
            "-C",
            ctx.cwd,
            "--json",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
        ]
        cmd += ctx.endpoint_args
        if ctx.model:
            cmd += ["-m", ctx.model]
        # 目录明确给了所选型号的档位时，请求档位必须在其中；明确无档位就不传（Codex 会拒绝）
        if ctx.effort and _effort_supported(self._catalog(), ctx.model, ctx.effort):
            cmd += ["-c", f"model_reasoning_effort={ctx.effort}"]
        return SpawnSpec(cmd + [ctx.prompt], dict(ctx.endpoint_env))

    def build_fix_command(self, ctx: FixRunContext) -> SpawnSpec | None:
        """修复会话：`--sandbox read-only`（Codex 自带的命令行读不受限、写被沙盒拦——维护者已知并接受，ADR 0149 D3），
        MCP 服务只出现在**本次**的 `-c mcp_servers.<名>.…` 里（TOML 值；绝不写 `~/.codex/config.toml`）。令牌不在 argv：
        `env_vars` 只列变量**名**，值由 Codex 从它自己的环境转给 MCP 子进程。"""
        pre = f"mcp_servers.{ctx.mcp_name}"

        def toml(value: object) -> str:
            return toml_value(value)

        cmd = [
            *ctx.argv,
            "exec",
            "-C",
            ctx.cwd,
            "--json",  # JSONL 事件：只用来显示进度 / 发现卡住，不当结论
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-c",
            f"{pre}.command={toml(ctx.mcp_command[0])}",
            "-c",
            f"{pre}.args={toml(list(ctx.mcp_command[1:]))}",
            "-c",
            f"{pre}.env_vars={toml([ctx.token_env])}",
            "-c",
            f"{pre}.tool_timeout_sec={int(ctx.tool_timeout_s)}",
            "-c",
            f"{pre}.startup_timeout_sec=60",
            # 本服务的工具一律免 Codex 自己的审批（exec 模式没人点，不放行就一直挂着——10-24 真机跑出来的）；
            # 要用户点头的改动由 Tavotto 自己弹卡（fixtools 的档位 + ctx.ask），不靠 Codex 这一层
            "-c",
            f'{pre}.default_tools_approval_mode="approve"',
            "-c",
            'approval_policy="never"',
        ]
        cmd += ctx.endpoint_args
        if ctx.endpoint_args:
            # Tavotto 已把 provider（-c model_provider…）和型号都显式给了：不再加载用户的 config.toml——
            # 去掉用户自己的 MCP 服务与 hooks（它们会被拉进修复会话），登录态仍在 CODEX_HOME，Tavotto 不读。
            # 官方登录态（没有 endpoint_args）不加：默认型号 / 认证方式可能就写在用户 config.toml 里，忽略它会换掉用户的设定
            cmd += ["--ignore-user-config"]
            if ctx.model:
                cmd += ["-m", ctx.model]
        return SpawnSpec(cmd + [ctx.prompt], dict(ctx.endpoint_env))

    def fix_event_parser(self) -> Callable[[str], list[dict]] | None:
        def parse(line: str) -> list[dict]:
            try:
                ev = json.loads(line)
            except ValueError:
                return []
            if not isinstance(ev, dict):
                return []
            etype = str(ev.get("type", ""))
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            itype = str(item.get("type") or item.get("item_type") or "")
            tool = str(item.get("tool") or ev.get("tool") or "")
            low = (etype + " " + itype).lower()
            if "approval" in low or "elicitation" in low:
                return [{"kind": "approval", "tool": tool}]
            if "mcp" in itype.lower() and str(item.get("server") or "tavotto_fix") == "tavotto_fix":
                if etype.endswith(".started") or etype.endswith("begin"):
                    return [{"kind": "mcp_begin", "tool": tool}]
                if etype.endswith(".completed") or etype.endswith("end"):
                    return [{"kind": "mcp_end", "tool": tool}]
            return [{"kind": "output", "tool": ""}]

        return parse

    def classify_event(self, line: str, state: dict) -> list[tuple[str, str]]:
        try:
            ev = json.loads(line)
        except ValueError:
            return []  # 横幅等非 JSON 噪音
        etype = str(ev.get("type", ""))
        item = ev.get("item") or {}
        itype = item.get("type") or item.get("item_type") or ""
        if itype == "agent_message":
            text = str(item.get("text") or "")
            if etype == "item.completed":
                state.pop("msg_buf", None)
                return [("message", text)] if text else []
            # item.updated 若携带累计文本 → 发增量
            prev = state.get("msg_buf", "")
            if text.startswith(prev) and len(text) > len(prev):
                state["msg_buf"] = text
                return [("delta", text[len(prev) :])]
            return []
        if itype == "reasoning":
            if etype != "item.completed":
                return []
            return [("thinking", str(item.get("text") or item.get("summary") or "思考中…"))]
        if itype in ("command_execution", "local_shell_call"):
            if etype != "item.completed":
                return []
            return [("action", "$ " + str(item.get("command") or ""))]
        if itype in ("file_change", "patch_apply"):
            return [("action", "✎ 修改文件")]
        if etype in ("error", "turn.failed"):
            return [("message", str(ev.get("error") or ev.get("message") or "出错"))]
        return []


class ClaudeAgent(AgentDefinition):
    id = "claude"
    display_name = "Claude Code"
    icon_key = "claude"
    command_names = ("claude",)
    endpoint_family = "anthropic"
    install_spec = InstallSpec(method="npm", package="@anthropic-ai/claude-code")
    supports_model_selection = True
    #: claude CLI 有 `--effort <level>`；档位来自它自己的模型目录缓存（读不到就不暴露档位）
    supports_effort_selection = True
    effort_official_only = True
    supports_readiness_probe = True

    #: 模型目录缓存不可用时的退路：三个别名，行为与引入目录之前完全一致
    MODELS = ["sonnet", "opus", "haiku"]

    @staticmethod
    def _home() -> Path:
        """Claude Code 自己的配置目录（`CLAUDE_CONFIG_DIR` 是其官方覆盖变量，测试也用它重定向）。"""
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))

    @classmethod
    def _catalog(cls) -> tuple[list[ModelDetail], dict[str, str], dict[str, str]]:
        """读 `<home>/cache/model-catalog/*.json`（CLI 自己缓存的模型目录），只读。

        返回 (`section == "main"` 的型号按文件原序, id→family, 别名→id)。文件名里带账号
        uuid、可能有多份：取 `fetchedAt` 最大且**可解析、main 段非空**的一份。缺失 / 损坏 /
        结构变了一律返回空（调用方退回三个别名），绝不抛、不猜。`overflow` 段是旧型号，不列。
        """
        best: tuple[float, list, dict] | None = None
        try:
            files = sorted((cls._home() / "cache" / "model-catalog").glob("*.json"))
        except OSError:
            return [], {}, {}
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                cfg = data["catalog"]["config"]
                raw = cfg["models"]
                fetched = data.get("fetchedAt")
                if not isinstance(raw, list) or isinstance(fetched, bool):
                    continue
                stamp = float(fetched)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if best is None or stamp > best[0]:
                if any(isinstance(m, dict) and m.get("section") == "main" for m in raw):
                    best = (stamp, raw, cfg)
        if best is None:
            return [], {}, {}
        details: list[ModelDetail] = []
        families: dict[str, str] = {}
        for m in best[1]:
            if not isinstance(m, dict) or m.get("section") != "main":
                continue
            mid = m.get("id")
            if not isinstance(mid, str) or not mid or any(d.id == mid for d in details):
                continue
            name = m.get("name")
            desc = m.get("description")
            rt = m.get("runtime") if isinstance(m.get("runtime"), dict) else {}
            levels = rt.get("effort_levels")
            efforts = (
                [e for e in levels if isinstance(e, str) and e] if isinstance(levels, list) else []
            )
            default = rt.get("default_effort")
            fam = rt.get("family")
            if isinstance(fam, str) and fam:
                families[mid] = fam
            details.append(
                ModelDetail(
                    id=mid,
                    display_name=name if isinstance(name, str) and name else mid,
                    description=desc if isinstance(desc, str) else "",
                    efforts=list(dict.fromkeys(efforts)),
                    default_effort=default if isinstance(default, str) and default else None,
                    efforts_known=True,
                )
            )
        aliases: dict[str, str] = {}
        pat = best[2].get("provider_alias_targets")
        if isinstance(pat, dict):
            for alias, spec in pat.items():
                target = spec.get("default") if isinstance(spec, dict) else None
                if isinstance(alias, str) and isinstance(target, str):
                    aliases[alias] = target
        return details, families, aliases

    @classmethod
    def _user_model(cls) -> str | None:
        """用户 `settings.json` 里的 `model`（只读）；`[1m]` 之类方括号后缀去掉。"""
        try:
            data = json.loads((cls._home() / "settings.json").read_text(encoding="utf-8"))
            value = data["model"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(value, str):
            return None
        return re.sub(r"\[[^\]]*\]$", "", value.strip()) or None

    def login_spec(self) -> LoginSpec | None:
        # `claude auth login` 需要交互：只能开在用户的系统终端里
        return LoginSpec(("auth", "login"), "terminal", "Claude")

    def model_capabilities(self) -> ModelCapabilities:
        details, families, aliases = self._catalog()
        if not details:
            return ModelCapabilities(
                models=list(self.MODELS),
                default_model="sonnet",
                model_details=[ModelDetail(id=m, display_name=m.capitalize()) for m in self.MODELS],
            )
        ids = [d.id for d in details]
        wanted = self._user_model()
        default = ids[0]
        if wanted:
            if wanted in ids:
                default = wanted
            elif aliases.get(wanted) in ids:
                default = aliases[wanted]
            else:
                default = next((i for i in ids if families.get(i) == wanted), ids[0])
        by_id = {d.id: d for d in details}
        efforts: list[str] = []
        for d in details:
            for e in d.efforts:
                if e not in efforts:
                    efforts.append(e)
        return ModelCapabilities(
            models=ids,
            default_model=default,
            efforts=efforts,
            default_effort=by_id[default].default_effort,
            model_details=details,
        )

    def readiness(self, argv: list[str]) -> ReadinessResult:
        """`claude auth status` —— 官方的本地状态子命令，回一段 JSON。

        **只取 `loggedIn` 这一个布尔值**：同一段 JSON 里还有邮箱、组织名、
        订阅档位——那些既不该进日志，也不该进 capabilities，更不该进遥测。
        """
        probe = _run_probe([*argv, "auth", "status"])
        if probe is None:
            return ReadinessResult("unknown", "probe_failed")
        code, text = probe
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except ValueError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("loggedIn"), bool):
                return (
                    ReadinessResult("ready", "cli_reports_signed_in")
                    if data["loggedIn"]
                    else ReadinessResult("needs_auth", "cli_reports_signed_out")
                )
        low = text.lower()
        if "not logged in" in low or "not authenticated" in low:
            return ReadinessResult("needs_auth", "cli_reports_signed_out")
        if code != 0:
            return ReadinessResult("unknown", "probe_unsupported")
        return ReadinessResult("unknown", "unrecognised_output")

    def build_command(self, ctx: RunContext) -> SpawnSpec:
        # stream-json + partial messages → 逐 token 流式（kind="delta"）
        cmd = [
            *ctx.argv,
            "-p",
            ctx.prompt,
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        cmd += ctx.endpoint_args
        if ctx.model:
            cmd += ["--model", ctx.model]
        # 网关（注入了接口参数 / 环境）后面的型号未必认这个档位：宁可不传
        # 目录明确给了所选型号的档位时，请求的档位必须在其中；明确无档位就不传
        if ctx.effort and not (ctx.endpoint_args or ctx.endpoint_env) and self._effort_ok(ctx):
            cmd += ["--effort", ctx.effort]
        return SpawnSpec(cmd, dict(ctx.endpoint_env))

    #: 修复会话里允许 Claude 自带命令行跑的只读命令（`Bash(<命令>)` 与 `Bash(<命令> *)` 各一条）。**故意不含 cat / head / tail / grep**：
    #: 它们能绕过 `Read` 的 deny 去读凭据位置，读内容走 `Read` / `Grep` / `Glob`（有 deny 表）或 MCP 的 read_file。
    FIX_READONLY_COMMANDS = (
        "ls",
        "pwd",
        "which",
        "file",
        "stat",
        "wc",
        "uname",
        "pip list",
        "pip freeze",
        "pip show",
        "conda list",
        "conda env list",
    )
    #: 精确匹配的只读命令（带参数就不是它了）
    FIX_READONLY_EXACT = (
        "python --version",
        "python3 --version",
        "python -V",
        "pip --version",
    )

    def fix_permissions(self, deny: dict, mcp_name: str) -> dict:
        """`--settings` 的 `permissions`：允许 MCP 工具与只读的读文件 / 只读命令，其余（写文件、联网、其它命令）一律拒；
        `deny` 里的凭据位置不许 `Read`（Claude 把 Read 规则也用在 Grep / Glob 上）。表是 `readscope.deny_spec()` 现生成的，
        不在这里抄第二份。`~/x` = 主目录相对，`//x` = 绝对路径，`**/x` = 相对工作目录的任意深度；文件名类再给 `//**/x` 覆盖工作目录之外。"""
        rules: list[str] = []
        for rel in deny.get("home_relative", ()):
            rules += [f"Read(~/{rel})", f"Read(~/{rel}/**)"]
        for pat in deny.get("names", ()):
            rules += [f"Read(**/{pat})", f"Read(//**/{pat})"]
        for ab in deny.get("absolute", ()):
            rules.append("Read(//" + str(ab).replace("\\", "/").lstrip("/") + ")")
        rules += [
            "Edit",
            "Write",
            "NotebookEdit",
            "WebFetch",
            "WebSearch",
        ]  # Claude Code 2.1 已没有 MultiEdit（写了会报「matches no known tool」）
        rules += ["Bash(*>*)", "Bash(*tee *)", "Bash(*sed -i*)", "Bash(*-exec*)"]
        allow = [f"mcp__{mcp_name}__*", "Read", "Grep", "Glob"]
        for c in self.FIX_READONLY_COMMANDS:
            allow += [f"Bash({c})", f"Bash({c} *)"]
        allow += [f"Bash({c})" for c in self.FIX_READONLY_EXACT]
        return {"allow": allow, "deny": rules}

    def build_fix_command(self, ctx: FixRunContext) -> SpawnSpec | None:
        """修复会话：权限与 MCP 配置都只经**本次**启动参数（`--settings` / `--mcp-config` JSON 字符串，加 `--strict-mcp-config`
        不读别处的 MCP），不写 `~/.claude`。`dontAsk` = 没明确允许的工具一律拒、不弹确认。令牌只以 `${变量名}` 出现在 MCP 的 env 里，
        值由 Claude 从它自己的环境展开。"""
        mcp = {
            "mcpServers": {
                ctx.mcp_name: {
                    "type": "stdio",
                    "command": ctx.mcp_command[0],
                    "args": list(ctx.mcp_command[1:]),
                    "env": {ctx.token_env: "${" + ctx.token_env + "}"},
                }
            }
        }
        settings = {"permissions": self.fix_permissions(ctx.deny, ctx.mcp_name)}
        cmd = [
            *ctx.argv,
            "-p",
            ctx.prompt,
            "--output-format",
            "stream-json",  # 一行一个事件：只用来显示进度 / 发现权限拒绝，不当结论
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--allowedTools=mcp__" + ctx.mcp_name + "__*",
            "--settings",
            json.dumps(settings),
            "--mcp-config",
            json.dumps(mcp),
            "--strict-mcp-config",
        ]
        cmd += ctx.endpoint_args
        env = {"MCP_TOOL_TIMEOUT": str(int(ctx.tool_timeout_s) * 1000), **ctx.endpoint_env}
        if ctx.model and ctx.endpoint_env:
            cmd += ["--model", ctx.model]
        return SpawnSpec(cmd, env)

    def fix_event_parser(self) -> Callable[[str], list[dict]] | None:
        prefix = "mcp__tavotto_fix__"
        names: dict[str, str] = {}  # tool_use id -> 工具名

        def parse(line: str) -> list[dict]:
            try:
                ev = json.loads(line)
            except ValueError:
                return []
            if not isinstance(ev, dict):
                return []
            out: list[dict] = []
            t = str(ev.get("type", ""))
            content = (
                (ev.get("message") or {}).get("content")
                if isinstance(ev.get("message"), dict)
                else None
            )
            for block in content if isinstance(content, list) else ():
                if not isinstance(block, dict):
                    continue
                if t == "assistant" and block.get("type") == "tool_use":
                    name = str(block.get("name") or "")
                    names[str(block.get("id") or "")] = name
                    if name.startswith(prefix):
                        out.append({"kind": "mcp_begin", "tool": name[len(prefix) :]})
                elif t == "user" and block.get("type") == "tool_result":
                    name = names.get(str(block.get("tool_use_id") or ""), "")
                    text = block.get("content")
                    text = text if isinstance(text, str) else json.dumps(text)[:400]
                    if block.get("is_error") and "permission" in text.lower():
                        out.append({"kind": "denied", "tool": name.removeprefix(prefix)})
                    elif name.startswith(prefix):
                        out.append({"kind": "mcp_end", "tool": name[len(prefix) :]})
            for d in ev.get("permission_denials") or ():
                if isinstance(d, dict):
                    out.append(
                        {
                            "kind": "denied",
                            "tool": str(d.get("tool_name") or "").removeprefix(prefix),
                        }
                    )
            return out or [{"kind": "output", "tool": ""}]

        return parse

    def _effort_ok(self, ctx: RunContext) -> bool:
        details, _families, _aliases = self._catalog()
        return _effort_supported(details, ctx.model, ctx.effort)

    def classify_event(self, line: str, state: dict) -> list[tuple[str, str]]:
        try:
            ev = json.loads(line)
        except ValueError:
            return []
        t = str(ev.get("type", ""))
        if t == "stream_event":
            inner = ev.get("event") or {}
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    return [("delta", str(delta["text"]))]
            return []
        if t == "assistant":
            out: list[tuple[str, str]] = []
            for block in (ev.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    out.append(("message", str(block["text"])))  # 终结流式气泡
                elif btype == "tool_use":
                    name = str(block.get("name") or "工具")
                    target = ""
                    inp = block.get("input") or {}
                    for key in ("file_path", "path", "command", "pattern"):
                        if inp.get(key):
                            target = str(inp[key])
                            break
                    if len(target) > 60:
                        target = "…" + target[-57:]
                    out.append(("action", f"✎ {name} {target}".rstrip()))
                elif btype == "thinking" and block.get("thinking"):
                    out.append(("thinking", str(block["thinking"])))
            return out
        if t == "result" and ev.get("is_error"):
            return [("message", str(ev.get("result") or "出错"))]
        return []  # system / rate_limit 等噪音


#: 内置执行器的 id。**不是用户可选的「执行器」**：界面只讲「连接」，它只经 connections 的 `route` 体现。
BUILTIN_ID = "builtin"

#: 内置执行器的密钥环境变量名（与 `agent_runner.__main__.ENV_API_KEY` 同值，`tests/test_ai_connections.py` 钉死）
BUILTIN_KEY_ENV = "TAVOTTO_AGENT_API_KEY"


def tavotto_launcher() -> list[str]:
    """拉起 Tavotto 自己的子命令（`fix-mcp` / `agent-run`）的命令前缀。冻结产物里 `sys.executable` 就是 Tavotto 自己，
    优先用同目录的 console 版 `tavotto-cli`（GUI 子系统的 exe 没有可靠的 stdio）；源码 / pip 形态走 `-m tavotto`。"""
    if getattr(sys, "frozen", False):
        from . import locate

        cli = Path(sys.executable).with_name(locate.CLI_NAME)
        return [str(cli if cli.is_file() else sys.executable)]
    return [sys.executable, "-m", "tavotto"]


class BuiltinAgent(AgentDefinition):
    """内置执行器（ADR 0147）：`tavotto agent-run`（= `python -m tavotto.engine.agent_runner`）子进程，自己跑「模型 ↔ 工具」循环。

    与两家 CLI 同一套会话生命周期（候选工作区 / TAF 校验 / 事件流 / 取消 / 历史库）；区别只在命令怎么拼：
    提示词走 **stdin**，密钥只在子进程**环境变量**里，argv 里只有地址 / 型号 / 协议名。
    `endpoint_args` / `endpoint_env` 由 `ai_providers.spawn_overrides("builtin", …)` 算好。"""

    id = BUILTIN_ID
    display_name = "Tavotto"
    internal = True
    supports_model_selection = True

    def build_command(self, ctx: RunContext) -> SpawnSpec:
        cmd = [
            *tavotto_launcher(),
            "agent-run",
            "--cwd",
            ctx.cwd,
            "--target",
            ctx.target or "",
            *ctx.endpoint_args,
        ]
        if ctx.task == "convert_to_editable":
            # 转换任务：`check` 经 stdin/stdout 回调父进程的校验流水（提示词是 stdin 的第一行 JSON）
            cmd += ["--task", ctx.task, "--output", ctx.output or "", "--check-ipc"]
            if ctx.resume_output_sha256:
                cmd += ["--resume-output-sha256", ctx.resume_output_sha256]
            if ctx.checks_used:
                cmd += ["--checks-used", str(ctx.checks_used)]
        elif ctx.check_ipc:
            # 改图任务：同一条 IPC，父进程在候选副本上跑提交前同一套检查（`ai_figure_edit.agent_check`）。
            # 两家 CLI 没有这个入口——它们的 Agent 看不到校验结果，只有提交后被 TAF 拦下时的有界修正一轮
            cmd += ["--check-ipc"]
        return SpawnSpec(cmd, dict(ctx.endpoint_env))

    #: `done.reason` → 给对话里看的一句话（不带内容、不带密钥）
    _FAILURES = {
        "auth": "密钥无效或已过期",
        "quota": "额度不足或被限流",
        "refused": "模型拒绝了这次请求",
        "network": "连不上模型服务",
        "budget": "这次修改用量超出上限，已停止",
        "max_turns": "这次修改步骤过多，已停止",
        "tool_error_loop": "反复失败，已停止",
        "internal": "出错",
    }

    def classify_event(self, line: str, state: dict) -> list[tuple[str, str]]:
        try:
            ev = json.loads(line)
        except ValueError:
            return []
        if not isinstance(ev, dict):
            return []
        t = ev.get("type")
        if t == "text" and ev.get("text"):
            return [("message", str(ev["text"]))]
        if t == "thinking":
            return [("thinking", "思考中…")]
        if t == "tool":
            summary = str(ev.get("summary") or "")
            if len(summary) > 80:
                summary = summary[:77] + "…"
            return [("action", f"✎ {ev.get('name') or '工具'} {summary}".rstrip())]
        if (
            t == "done"
            and not ev.get("ok")
            and ev.get("reason") not in (None, "completed", "cancelled")
        ):
            # Preserve only closed short codes, never model/provider error text.
            reason = str(ev["reason"])
            if reason in self._FAILURES:
                state["runner_error"] = (
                    "agent_output_truncated"
                    if reason == "budget" and ev.get("detail") == "output_truncated"
                    else "agent_" + reason
                )
            return [("message", self._FAILURES.get(str(ev["reason"]), "出错"))]
        return []


#: **生产注册表**。顺序即界面顺序。
#:
#: 这里只放当前真的能跑起来的 Agent。架构允许再加第三个而不用改前后端的
#: 任何一条分支，但「架构支持」不等于「假装已经支持」——没有实现的条目一律
#: 不进这张表，界面上也就不会出现「即将推出」这种占位行。
AGENT_REGISTRY: tuple[AgentDefinition, ...] = (CodexAgent(), ClaudeAgent())


def agents() -> tuple[AgentDefinition, ...]:
    return AGENT_REGISTRY


def agent_ids() -> tuple[str, ...]:
    return tuple(a.id for a in AGENT_REGISTRY)


#: 内部执行器：`get_agent` 找得到，`agents()` / `agent_ids()` / 注册表里没有（用户可选的 Agent 只在 `AGENT_REGISTRY`）
INTERNAL_AGENTS: tuple[AgentDefinition, ...] = (BuiltinAgent(),)


def get_agent(agent_id: str) -> AgentDefinition | None:
    """按 id 取适配器。**未知 id 一律回 None**，绝不把它继续往下传。"""
    for a in (*AGENT_REGISTRY, *INTERNAL_AGENTS):
        if a.id == agent_id:
            return a
    return None


def endpoint_agents() -> tuple[str, ...]:
    """支持接第三方接口的 Agent id（`ai_providers` 的白名单来源）。"""
    return tuple(a.id for a in AGENT_REGISTRY if a.endpoint_family)


# ---------------------------------------------------------------------------
# 解析：候选 → 启动验证 → 就绪检查
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Resolution:
    """一个 Agent 在这台机器上的探测结论。

    `argv` **只在后端内部用**（要么是 `[exe]`，要么是 `[node, script.js]`）；
    前端没有消费者，capabilities 不公开它。
    """

    argv: list[str] | None = None
    path: str | None = None
    version: str | None = None
    source: Source | None = None
    broken_path: str | None = None
    searched: list[str] = field(default_factory=list)
    readiness: ReadinessResult = field(default_factory=ReadinessResult)
    #: 校验失败的稳定原因（not_a_file / timeout / launch_failed）；成功时 None
    error: str | None = None


def agent_search_locations(agent: AgentDefinition) -> list[SearchLocation]:
    """该 Agent 的全部候选目录：通用落点在前，Agent 独有的在后。

    顺序有意义：单独装的 CLI 通常比某个应用内置的那份新。
    """
    out: list[SearchLocation] = []
    seen: set[str] = set()
    for name in agent.command_names:
        for loc in search_locations(name):
            if loc.path not in seen:
                seen.add(loc.path)
                out.append(loc)
    for loc in agent.extra_search_locations():
        if loc.path not in seen:
            seen.add(loc.path)
            out.append(loc)
    return out


def path_override(agent_id: str) -> str | None:
    """用户在设置里指定的可执行文件（最高优先级）。"""
    rec = config.ai_agent_settings().get(agent_id) or {}
    value = str(rec.get("path_override") or "").strip()
    return value or None


def candidates(agent: AgentDefinition, override: str | None = None) -> list[CliCandidate]:
    """该 Agent 的候选（按优先级、按实际路径去重）：设置 → PATH → 常见位置 →
    Agent 独有的具体文件（Codex 桌面版自带那份）→ npm 包内二进制 → MSIX 包体。

    刻意返回**全部**候选而不是第一个：Windows 上
    `%LOCALAPPDATA%\\Microsoft\\WindowsApps` 里的执行别名可能存在却根本启动不了
    （商店应用没装全 / 别名被禁用 / 残留的 0 字节 reparse point）——第一候选
    坏了必须能落到下一个（比如 npm 全局目录里那个真的能跑的）。
    不含任何私人硬编码路径。
    """
    out: list[CliCandidate] = []
    seen: set[str] = set()

    def add(p: str | None, source: Source) -> None:
        if p and p not in seen:
            seen.add(p)
            out.append(CliCandidate(p, source))

    custom = override if override is not None else path_override(agent.id)
    if custom:
        p = Path(custom).expanduser()
        if p.is_file():
            add(str(p), "custom")
    for name in agent.command_names:
        add(shutil.which(name), "path")
    # PATH 里没有：把常见安装目录当成 PATH 再 which（Windows 上 PATHEXT 的
    # .cmd/.exe 匹配也交给 which 处理，不手写扩展名组合）。逐目录 which，
    # 保住「一个目录里的候选坏了还有下一个目录」的语义。
    locs = [loc for loc in agent_search_locations(agent) if Path(loc.path).is_dir()]
    for loc in locs:
        for name in agent.command_names:
            add(shutil.which(name, path=loc.path), loc.source)
    # Agent 独有的具体文件（Windows Codex 桌面版自带的 CLI，#726）。排在全部目录落点
    # 之后：用户单独装的 CLI（PATH / npm / 商店别名）是他自己选的那份，通常也更新；
    # 排在下面两手兜底之前：桌面版那份是完整可用的安装，不是残缺安装的补救。
    for cand in agent.extra_candidates():
        add(cand.path, cand.source)
    # npm 包内部的平台原生二进制（外层 .cmd 外壳缺失时的最后一手）
    for loc in locs:
        for name in agent.command_names:
            for sub in Path(loc.path).glob(f"node_modules/**/bin/{name}*"):
                if sub.is_file() and sub.suffix.lower() in ("", ".exe"):
                    add(str(sub), "package_binary")
    if os.name == "nt":
        # MSIX 包体本身。正常情况下走执行别名就够了（见 search_locations），
        # 这里只是别名被用户关掉时的兜底；目录读不了就当没有，绝不报错。
        for root in (os.environ.get("ProgramFiles") or r"C:\Program Files",):
            for name in agent.command_names:
                try:
                    for sub in Path(root, "WindowsApps").glob(f"*{name}*/app/{name}.exe"):
                        if sub.is_file():
                            add(str(sub), "windows_store")
                except OSError:
                    pass
    return out


_RESOLVE_CACHE: dict[str, Resolution] = {}
#: 解析缓存代数（与 ai_bridge 的能力缓存代数同一个道理，#512 评审）：clear_cache
#: 加一；一次 resolve 只有在它起跑时的代数仍是当前代数时才写回缓存——诊断超预算
#: 留在后台的那次探测读的是改路径之前的设置，跑完不许把旧候选盖回去，否则
#: `_cmd()` 直接读缓存，会启动用户已经换掉的那个可执行文件。
_RESOLVE_GEN = 0
_RESOLVE_LOCK = threading.Lock()


def clear_cache() -> None:
    global _RESOLVE_GEN
    with _RESOLVE_LOCK:
        _RESOLVE_GEN += 1
        _RESOLVE_CACHE.clear()


def resolve(agent: AgentDefinition, probe_readiness: bool = True) -> Resolution:
    """逐个候选做启动验证（`--version`），第一个真能跑起来的才算数。

    以前拿到第一个候选就宣布「已安装」，用户在别人电脑上撞到的正是这一步：
    WindowsApps 的执行别名存在但无法被子进程启动，探测说装了、运行必失败。
    现在候选启动不了就换下一个；全都不行时把**第一个**坏候选记在 broken_path，
    界面据此把「安装不可用」与「未安装」分开说。
    """
    with _RESOLVE_LOCK:
        cached = _RESOLVE_CACHE.get(agent.id)
        if cached is not None:
            return cached
        gen = _RESOLVE_GEN
    searched = [loc.path for loc in agent_search_locations(agent)] + agent.extra_searched()
    broken: str | None = None
    found: CliCandidate | None = None
    argv: list[str] | None = None
    version: str | None = None
    for cand in candidates(agent):
        cand_argv = resolve_shim(cand.path) or [cand.path]
        got = probe_version(cand_argv)
        if got is not None:
            found, argv, version = cand, cand_argv, got
            break
        if broken is None:
            broken = cand.path
    if argv is None:
        res = Resolution(
            searched=searched, broken_path=broken, error="launch_failed" if broken else "not_found"
        )
    else:
        readiness = ReadinessResult()
        if probe_readiness and agent.supports_readiness_probe:
            readiness = agent.readiness(argv)
        res = Resolution(
            argv=argv,
            path=argv[-1],
            version=version,
            source=found.source if found else None,
            searched=searched,
            readiness=readiness,
        )
    with _RESOLVE_LOCK:
        if gen == _RESOLVE_GEN:
            _RESOLVE_CACHE[agent.id] = res
    return res


def names_this_agent(agent: AgentDefinition, filename: str) -> bool:
    """这个文件名看起来是不是**这个 Agent** 的可执行文件。

    用户在设置里做的事情是「告诉 Tavotto 我的 codex 在哪儿」，所以那个文件
    的名字里总该有 `codex`。判据放得很松（只要求包含，`codex.exe`、
    `codex.cmd`、`codex-cli`、`run-codex.sh` 全过），但它挡掉的是
    「把 Tavotto 指向 /bin/sh」这一整类——那不是「指错了路径」，
    那是把一个任意可执行文件喂给会被 spawn 的位置。
    """
    stem = os.path.basename(filename).lower()
    return any(name.lower() in stem for name in agent.command_names)


def validate_executable(agent: AgentDefinition, path: str) -> Resolution:
    """校验用户指定的可执行文件：与自动探测**同一套** shim 解析 + 启动验证。

    只接受一个文件路径，不接受任意 shell 命令串（不 split、不 `shell=True`）。
    验证不过就不返回可用 argv——调用方据此拒绝保存，不覆盖原来有效的设置。

    **这个路径来自 HTTP 请求体，最终会被 spawn**，所以「是个文件」远远不够
    （CodeQL py/path-injection 报的正是这一点）。四道闸依次是：
      ① 不含 NUL、不为空；
      ② `realpath` 归一化——`..` 与符号链接在这里解掉，之后所有判断都打在
        真身上，而不是打在一个还能再跳一次的字符串上；
      ③ 必须是**存在的普通文件**且当前用户可执行；
      ④ 文件名必须指向这个 Agent（见 `names_this_agent`）。
    过了这四道才轮到 shim 解析与 `--version`。

    **③ 里的可执行位在 Windows 上等于没有**：那个平台没有可执行位语义，
    `os.access(X_OK)` 对任何存在的文件都为真。这不是缺陷，是那里没东西可查
    ——两个平台上真正兜底的都是最后那次 `--version` 启动验证。别把这道闸
    当成安全边界，它只是 POSIX 上一条便宜的早退。
    （看护：`test_windows_regressions.py::test_executable_bit_gate_is_a_noop_on_windows`）
    """
    raw = (path or "").strip()
    if not raw or "\x00" in raw:
        return Resolution(broken_path=raw, error="not_a_file")
    # expanduser 在前、realpath 在后：`~` 要先变成真目录，`..` 与符号链接
    # 才有得解。之后一律用 resolved，不再碰用户给的原串。
    resolved = os.path.realpath(os.path.expanduser(raw))
    p = Path(resolved)
    if not p.is_file():
        return Resolution(broken_path=resolved, error="not_a_file")
    if not os.access(resolved, os.X_OK):
        return Resolution(broken_path=resolved, error="not_executable")
    if not names_this_agent(agent, resolved):
        return Resolution(broken_path=resolved, error="not_this_agent")
    argv = resolve_shim(resolved) or [resolved]
    version, failure = probe_version_detailed(argv)
    if version is None:
        return Resolution(broken_path=resolved, error=failure or "launch_failed")
    readiness = agent.readiness(argv) if agent.supports_readiness_probe else ReadinessResult()
    return Resolution(
        argv=argv, path=argv[-1], version=version, source="custom", readiness=readiness
    )
