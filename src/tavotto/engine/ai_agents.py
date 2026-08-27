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
class ModelCapabilities:
    """该 Agent 当前能提供的模型 / 推理强度选项。"""

    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    efforts: list[str] = field(default_factory=list)
    default_effort: str | None = None


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


@dataclass(frozen=True)
class SpawnSpec:
    """→ (完整命令行, 需要追加到环境的变量)。"""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


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
    #: 该族的第三方接口要不要选 wire api（OpenAI 兼容那一族才有 responses/chat
    #: 两种协议）。**由适配器声明**，前端不该靠 `id === "codex"` 猜。
    supports_wire_api: bool = False
    #: 有没有官方的、无副作用的本地登录状态命令
    supports_readiness_probe: bool = False

    # -- 探测 --------------------------------------------------------------
    def extra_search_locations(self) -> list[SearchLocation]:
        """该 Agent 独有的落点（排在通用落点之后）。"""
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

    # -- 运行 --------------------------------------------------------------
    def build_command(self, ctx: RunContext) -> SpawnSpec:
        raise NotImplementedError

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

    def model_capabilities(self) -> ModelCapabilities:
        cfg = self._config()
        efforts = list(self.EFFORTS)
        if cfg["default_effort"] and cfg["default_effort"] not in efforts:
            efforts.append(cfg["default_effort"])
        return ModelCapabilities(
            models=list(cfg["models"]),
            default_model=cfg["default_model"],
            efforts=efforts,
            default_effort=cfg["default_effort"] or "medium",
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
        if ctx.effort:
            cmd += ["-c", f"model_reasoning_effort={ctx.effort}"]
        return SpawnSpec(cmd + [ctx.prompt], dict(ctx.endpoint_env))

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
    #: claude CLI 没有推理强度开关，不假装有
    supports_effort_selection = False
    supports_readiness_probe = True

    MODELS = ["sonnet", "opus", "haiku"]

    def model_capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(models=list(self.MODELS), default_model="sonnet")

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
        return SpawnSpec(cmd, dict(ctx.endpoint_env))

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


def get_agent(agent_id: str) -> AgentDefinition | None:
    """按 id 取适配器。**未知 id 一律回 None**，绝不把它继续往下传。"""
    for a in AGENT_REGISTRY:
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
    """该 Agent 的候选（按优先级、按实际路径去重）：设置 → PATH → 常见位置。

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


def clear_cache() -> None:
    _RESOLVE_CACHE.clear()


def resolve(agent: AgentDefinition, probe_readiness: bool = True) -> Resolution:
    """逐个候选做启动验证（`--version`），第一个真能跑起来的才算数。

    以前拿到第一个候选就宣布「已安装」，用户在别人电脑上撞到的正是这一步：
    WindowsApps 的执行别名存在但无法被子进程启动，探测说装了、运行必失败。
    现在候选启动不了就换下一个；全都不行时把**第一个**坏候选记在 broken_path，
    界面据此把「安装不可用」与「未安装」分开说。
    """
    cached = _RESOLVE_CACHE.get(agent.id)
    if cached is not None:
        return cached
    searched = [loc.path for loc in agent_search_locations(agent)]
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
