"""AI 桥：让 codex / claude CLI 非交互地深度修改 fig 脚本。

流程：快照脚本 → spawn CLI（cwd=figures 目录）→ stdout 逐行流式回调（SSE）→
进程结束后对比快照生成 unified diff。CLI 直接改动脚本文件；文件 watcher
随即作废渲染会话，前端重渲染即可看到效果——用户不满意可 revert 恢复快照。

纯标准库，Flask 父进程 import。
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import ai_history, ai_providers, config
from .runtime import CREATE_NO_WINDOW  # noqa: F401 — 重导出，历史调用方仍认这个名字

LOG = logging.getLogger("mm.ai")

SNAP_DIR = config.data_dir() / "cache" / "ai_snapshots"
TIMEOUT_S = 900          # 单次 AI 任务上限
SNAP_KEEP = 20           # 快照保留的最近会话数（超龄的 revert 入口早已不可见）
SESSIONS: dict[str, dict] = {}


def _prune_snapshots(keep: int = SNAP_KEEP) -> int:
    """按 sidecar mtime 保留最近 keep 个会话的快照三件套（快照/sidecar/stderr）。"""
    try:
        sidecars = sorted(SNAP_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return 0
    removed = 0
    for side in sidecars[:-keep] if keep else sidecars:
        for p in SNAP_DIR.glob(f"{side.stem}*"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _search_dirs(name: str) -> list[str]:
    """按平台列出该 CLI 的常见落点（PATH 之外的兜底）。

    Windows 上以前一个候选都没有——`shutil.which` 找不到就直接判定「未安装」。
    可 PATH 恰恰是 Windows 最不可靠的地方：npm 全局目录要重开终端才进 PATH，
    从桌面快捷方式启动的进程拿的又是启动那一刻的旧环境块。所以这里把
    npm / winget / scoop / bun / volta / 官方安装器的落点全部直接翻一遍。
    """
    # 一律用字符串拼路径（与 pool._candidate_pythons 同一条约定）：
    # pathlib.Path 会按 os.name 分派 Posix/Windows 实现，在非目标平台上构造
    # 另一半会直接抛 UnsupportedOperation——连跨平台测这段分支都做不到。
    home = os.path.expanduser("~")
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or home + r"\AppData\Roaming"
        local = os.environ.get("LOCALAPPDATA") or home + r"\AppData\Local"
        programs = os.environ.get("ProgramFiles") or r"C:\Program Files"
        return [
            appdata + r"\npm",                       # npm 全局（最常见）
            local + r"\npm",
            # 微软商店 / MSIX 安装（OpenAI.Codex 就是这么发的）。真身在
            # C:\Program Files\WindowsApps\OpenAI.Codex_…\app，那个目录受 ACL
            # 保护、普通进程连列都列不了——**能用的入口是这里的执行别名**
            # （0 字节 reparse point，只能执行不能读）。少了这一条，商店版
            # codex 在系统里就是「找不到」。
            local + r"\Microsoft\WindowsApps",
            home + r"\.local\bin",                   # 官方安装脚本
            home + r"\.bun\bin",
            local + r"\Volta\bin",
            home + r"\scoop\shims",
            local + r"\Microsoft\WinGet\Links",
            r"C:\ProgramData\chocolatey\bin",
            local + rf"\Programs\{name}",
            local + rf"\Programs\{name}\bin",
            programs + r"\nodejs",
            home + r"\.codex\bin",
            home + r"\.claude\bin",
            home + rf"\.{name}\bin",
        ]
    return [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
        f"{home}/.local/bin",
        f"{home}/.bun/bin",
        f"{home}/.volta/bin",
        f"{home}/.npm-global/bin",
        f"{home}/.{name}/bin",
        "/opt/homebrew/opt/node/bin",
    ]


def _resolve_shim(path: str) -> list[str] | None:
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


def _cli_candidates(name: str) -> list[str]:
    """该 CLI 的候选路径（按优先级、去重）：用户设置 → PATH → 常见安装位置。

    刻意返回**全部**候选而不是第一个：Windows 上 `%LOCALAPPDATA%\\Microsoft\\
    WindowsApps` 里的执行别名可能存在却根本启动不了（商店应用没装全 / 别名被
    禁用 / 残留的 0 字节 reparse point）——第一候选坏了必须能落到下一个
    （比如 npm 全局目录里那个真的能跑的）。不含任何私人硬编码路径。"""
    out: list[str] = []

    def add(p: str | None) -> None:
        if p and p not in out:
            out.append(p)

    custom = str(config.ai_settings().get(f"{name}_path") or "")
    if custom:
        p = Path(custom).expanduser()
        if p.is_file():
            add(str(p))
    add(shutil.which(name))
    # PATH 里没有：把常见安装目录当成 PATH 再 which（Windows 上 PATHEXT 的
    # .cmd/.exe 匹配也交给 which 处理，不手写扩展名组合）。逐目录 which，
    # 保住「一个目录里的候选坏了还有下一个目录」的语义。
    dirs = [d for d in _search_dirs(name) if Path(d).is_dir()]
    for d in dirs:
        add(shutil.which(name, path=d))
    # npm 包内部的平台原生二进制（外层 .cmd 外壳缺失时的最后一手）
    for d in dirs:
        for sub in Path(d).glob(f"node_modules/**/bin/{name}*"):
            if sub.is_file() and sub.suffix.lower() in ("", ".exe"):
                add(str(sub))
    if os.name == "nt":
        # MSIX 包体本身。正常情况下走执行别名就够了（见 _search_dirs），
        # 这里只是别名被用户关掉时的兜底；目录读不了就当没有，绝不报错。
        for root in (os.environ.get("ProgramFiles") or r"C:\Program Files",):
            try:
                for sub in Path(root, "WindowsApps").glob(f"*{name}*/app/{name}.exe"):
                    if sub.is_file():
                        add(str(sub))
            except OSError:
                pass
    return out


# name -> {"argv": list|None, "version": str|None, "broken_path": str|None}
_RESOLVE_CACHE: dict[str, dict] = {}


def _resolve_cli(name: str) -> dict:
    """逐个候选做启动验证（`--version`），第一个真能跑起来的才算数。

    以前拿到第一个候选就宣布「已安装」，用户在别人电脑上撞到的正是这一步：
    WindowsApps 的执行别名存在但无法被子进程启动，探测说装了、运行必失败。
    现在候选启动不了就换下一个；全都不行时把第一个坏候选记在 broken_path，
    界面据此提示「检测到不可用的安装」而不是干说「未安装」。"""
    cached = _RESOLVE_CACHE.get(name)
    if cached is not None:
        return cached
    broken: str | None = None
    result = {"argv": None, "version": None, "broken_path": None}
    for path in _cli_candidates(name):
        argv = _resolve_shim(path) or [path]
        version = _probe_version(argv)
        if version is not None:
            result = {"argv": argv, "version": version, "broken_path": None}
            break
        if broken is None:
            broken = path
    else:
        result["broken_path"] = broken
    _RESOLVE_CACHE[name] = result
    return result


def _cli_argv(name: str) -> list[str] | None:
    """CLI 的启动 argv（可能是 [exe] 或 [node, script.js]）；找不到回 None。"""
    return _resolve_cli(name)["argv"]


def _cli_path(name: str) -> str | None:
    """CLI 可执行路径（已通过启动验证的那一个）；探测/诊断用。"""
    argv = _cli_argv(name)
    return argv[-1] if argv else None


def _find_cli(name: str) -> list[str]:
    argv = _cli_argv(name)
    if argv is None:
        raise RuntimeError(f"找不到 {name} CLI（可在设置中指定其路径）")
    return argv


def _spawn_env(cli_path: str | None, extra: dict | None = None) -> dict:
    """CLI 子进程的环境：把常见安装目录并进 PATH（不改排序、只补缺）。

    桌面壳从 Finder / 开始菜单启动时继承的是 GUI 的最小 PATH，里面没有
    /opt/homebrew/bin 这类目录：npm shim 的 `#!/usr/bin/env node` 在子进程里
    解析不到 node，报 `env: node: No such file or directory`——CLI 明明装着、
    路径也找到了，一启动就死。把 CLI 自己所在目录 + 各常见安装目录接到
    PATH 末尾，`env node` 就能像在用户终端里一样解析。"""
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    home = os.path.expanduser("~")
    candidates = []
    if cli_path:
        candidates.append(os.path.dirname(cli_path))
    candidates += _search_dirs("node")
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


def _probe_version(argv: list[str]) -> str | None:
    try:
        out = subprocess.run([*argv, "--version"], capture_output=True,
                             text=True, timeout=10, encoding="utf-8",
                             errors="replace", stdin=subprocess.DEVNULL,
                             env=_spawn_env(argv[-1]),
                             creationflags=CREATE_NO_WINDOW)
        line = (out.stdout or out.stderr).strip().splitlines()
        return line[0][:80] if line else None
    except (OSError, subprocess.TimeoutExpired):
        return None


_CAPS_CACHE: dict = {}

# codex 的推理强度档位。模型名故意不写死——OpenAI 换代时旧名会被服务端直接
# 拒绝（"The 'gpt-5' model is not supported when using Codex with a ChatGPT
# account."），本机 config.toml 里用户自己选定的才是可用的。
CODEX_EFFORTS = ["minimal", "low", "medium", "high", "max"]
CLAUDE_MODELS = ["sonnet", "opus", "haiku"]


def _codex_home() -> Path:
    """codex 自己的配置目录（CODEX_HOME 是其官方覆盖变量，测试也用它重定向）。"""
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _codex_config() -> dict:
    """从 codex 的 config.toml 读模型与默认推理强度。

    返回 {"models": [...], "default_model": str|None, "default_effort": str|None}。
    顶层 model 是用户当前在用的，profiles 里的作为备选一并列出。读不到就返回
    空——前端只显示「跟随 CLI 默认」，绝不给一个我们猜的模型名。
    """
    out: dict = {"models": [], "default_model": None, "default_effort": None}
    try:
        text = (_codex_home() / "config.toml").read_text(encoding="utf-8")
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
            names += [p.get("model") for p in profiles.values()
                      if isinstance(p, dict)]
    except (ImportError, ValueError):
        # tomllib 缺席（3.10）或 TOML 有语法问题：退化成逐行取值，
        # 首个 model 视为默认。宁可少给，也不要报错让整个面板不可用。
        names = re.findall(r'^\s*model\s*=\s*["\']([^"\']+)["\']', text, re.M)
        efforts = re.findall(
            r'^\s*model_reasoning_effort\s*=\s*["\']([^"\']+)["\']', text, re.M)
        out["default_model"] = names[0] if names else None
        out["default_effort"] = efforts[0] if efforts else None
    seen: list[str] = []
    for n in names:
        if isinstance(n, str) and n and n not in seen:
            seen.append(n)
    out["models"] = seen
    return out


def capabilities(refresh: bool = False) -> dict:
    """实际探测本机 codex / claude：安装与版本；模型与推理强度按 provider
    各自给出（两家能力不同构——claude CLI 不暴露推理强度选项）。

    codex 的模型清单来自它自己的 config.toml，随用户升级 CLI 自动跟上；
    接了第三方接口时改用该接口自己填的模型清单（网关认的模型名与官方无关）。
    """
    global _CAPS_CACHE
    if _CAPS_CACHE and not refresh:
        return _CAPS_CACHE
    providers: dict[str, dict] = {}
    for name in ("codex", "claude"):
        resolved = _resolve_cli(name)
        argv = resolved["argv"]
        path = argv[-1] if argv else None
        info: dict = {"installed": argv is not None, "path": path,
                      "argv": argv,
                      "version": resolved["version"],
                      "models": [], "default_model": None,
                      "efforts": [], "default_effort": None,
                      "endpoint": None}
        if argv:
            if name == "codex":
                cfg = _codex_config()
                efforts = list(CODEX_EFFORTS)
                if cfg["default_effort"] and cfg["default_effort"] not in efforts:
                    efforts.append(cfg["default_effort"])
                info.update(models=cfg["models"],
                            default_model=cfg["default_model"],
                            efforts=efforts,
                            default_effort=cfg["default_effort"] or "medium")
            else:
                # claude CLI 支持模型别名；推理强度无 CLI 开关，不假装有
                info.update(models=list(CLAUDE_MODELS), default_model="sonnet")
            endpoint = ai_providers.resolve(name)
            if endpoint:
                info["endpoint"] = ai_providers.public(endpoint)
                if endpoint.get("models"):
                    info["models"] = list(endpoint["models"])
                    info["default_model"] = (endpoint.get("default_model")
                                             or endpoint["models"][0])
        else:
            # 没装：把找过哪些目录告诉用户，比干甩一句「未安装」有用得多；
            # 找到了却启动不了的候选（商店版执行别名的典型故障）单独指出来
            info["searched"] = _search_dirs(name)
            if resolved["broken_path"]:
                info["broken_path"] = resolved["broken_path"]
            # 一键安装的可行性：npm 在不在 + 装哪个包 + 当前安装状态
            info["install"] = {"method": "npm",
                               "package": NPM_PACKAGES.get(name),
                               "available": _npm_argv() is not None,
                               **install_status(name)}
        providers[name] = info
    _CAPS_CACHE = {"providers": providers,
                   "endpoints": [ai_providers.public(p)
                                 for p in ai_providers.list_providers()],
                   "presets": ai_providers.PRESETS,
                   "active": {a: ai_providers.active_id(a)
                              for a in ai_providers.AGENTS}}
    return _CAPS_CACHE


def invalidate_capabilities() -> None:
    """改过 CLI 路径 / 第三方接口后必须清缓存，否则界面一直是旧探测结果。"""
    global _CAPS_CACHE
    _CAPS_CACHE = {}
    _RESOLVE_CACHE.clear()


# ---------------------------------------------------------------------------
# 一键安装（npm 用户级全局包；给「根本没装过 CLI」的用户一条不出软件的路）
# ---------------------------------------------------------------------------
NPM_PACKAGES = {"codex": "@openai/codex", "claude": "@anthropic-ai/claude-code"}
INSTALL_TIMEOUT_S = 600
_INSTALLS: dict[str, dict] = {}   # agent -> {"status", "code", "log", "started"}
_INSTALL_LOCK = threading.Lock()


def _npm_argv() -> list[str] | None:
    """npm 可执行路径。npm.cmd 直接跑没有元字符问题——install 的参数全是
    我们写死的常量，不含用户输入，无须经 _resolve_shim 绕开 cmd.exe。"""
    p = shutil.which("npm")
    if not p:
        dirs = [d for d in _search_dirs("npm") if Path(d).is_dir()]
        if dirs:
            p = shutil.which("npm", path=os.pathsep.join(dirs))
    return [p] if p else None


def install_status(agent: str) -> dict:
    st = _INSTALLS.get(agent)
    if not st:
        return {"status": "idle"}
    return {k: st[k] for k in ("status", "code", "log") if k in st}


def start_install(agent: str) -> dict:
    """后台 `npm install -g <包>`；结束后重探测 capabilities 定成败。

    只装 NPM_PACKAGES 里认识的两个包名，绝不把请求体里的字符串拼进命令行。
    """
    if agent not in NPM_PACKAGES:
        raise ValueError(f"未知 agent: {agent}")
    with _INSTALL_LOCK:
        st = _INSTALLS.get(agent)
        if st and st.get("status") == "running":
            return install_status(agent)
        npm = _npm_argv()
        if npm is None:
            # 现场没有 npm：结构化告知，让界面引导用户先装 Node.js LTS——
            # 绝不静默下载 Node 安装器（那是另一个量级的越权）
            _INSTALLS[agent] = {"status": "error", "code": "npm_missing",
                                "log": "找不到 npm。请先安装 Node.js LTS"
                                       "（https://nodejs.org），再回来点一次安装。"}
            return install_status(agent)
        state: dict = {"status": "running", "started": time.time()}
        _INSTALLS[agent] = state

    def work() -> None:
        try:
            out = subprocess.run(
                [*npm, "install", "-g", NPM_PACKAGES[agent]],
                capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S,
                encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW)
            state["log"] = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()[-4000:]
            if out.returncode != 0:
                state.update(status="error", code="npm_failed")
                return
            # npm 说成了不算数：重新探测，CLI 真能 --version 才算装上
            invalidate_capabilities()
            caps = capabilities(refresh=True)
            if caps["providers"][agent]["installed"]:
                state.update(status="done")
            else:
                state.update(status="error", code="installed_but_not_found")
        except subprocess.TimeoutExpired:
            state.update(status="error", code="timeout",
                         log=f"npm install 超过 {INSTALL_TIMEOUT_S}s 未完成")
        except OSError as exc:
            state.update(status="error", code="spawn_failed", log=str(exc))
        finally:
            LOG.info("npm install %s -> %s", agent, state.get("status"))

    threading.Thread(target=work, daemon=True, name=f"ai-install-{agent}").start()
    return install_status(agent)


def _cmd(agent: str, prompt: str, cwd: str,
         model: str | None = None, effort: str | None = None,
         endpoint: dict | None = None) -> tuple[list[str], dict[str, str]]:
    """→ (命令行, 需要追加到环境的变量)。第三方接口全部走这两样注入，
    绝不改写用户自己的 ~/.claude 或 ~/.codex 配置。"""
    extra_args, extra_env = ai_providers.spawn_overrides(agent, endpoint, model)
    if agent == "codex":
        cmd = [*_find_cli("codex"), "exec", "-C", cwd, "--json",
               "--sandbox", "workspace-write", "--skip-git-repo-check"]
        cmd += extra_args
        if model:
            cmd += ["-m", model]
        if effort:
            cmd += ["-c", f"model_reasoning_effort={effort}"]
        return cmd + [prompt], extra_env
    if agent == "claude":
        # stream-json + partial messages → 逐 token 流式（kind="delta"）
        cmd = [*_find_cli("claude"), "-p", prompt,
               "--permission-mode", "acceptEdits",
               "--output-format", "stream-json", "--include-partial-messages",
               "--verbose"]
        cmd += extra_args
        if model:
            cmd += ["--model", model]
        return cmd, extra_env
    raise RuntimeError(f"未知 agent: {agent}")


def _classify(agent: str, line: str, st: dict) -> list[tuple[str, str]]:
    """把 CLI 原始输出分类为事件列表。kind：
      delta    — 当前回答的流式增量（只走 SSE，不进 transcript）
      message  — 一段完整回答（终结当前流式气泡）
      thinking / action — 过程（前端默认折叠）
    st 是每会话的解析状态（codex 增量去重用）。"""
    try:
        ev = json.loads(line)
    except ValueError:
        return []  # 横幅等非 JSON 噪音

    if agent == "codex":
        etype = str(ev.get("type", ""))
        item = ev.get("item") or {}
        itype = item.get("type") or item.get("item_type") or ""
        if itype == "agent_message":
            text = str(item.get("text") or "")
            if etype == "item.completed":
                st.pop("msg_buf", None)
                return [("message", text)] if text else []
            # item.updated 若携带累计文本 → 发增量
            prev = st.get("msg_buf", "")
            if text.startswith(prev) and len(text) > len(prev):
                st["msg_buf"] = text
                return [("delta", text[len(prev):])]
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

    # claude stream-json
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
        for block in ((ev.get("message") or {}).get("content") or []):
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


def _build_prompt(script: str, user_prompt: str, context: dict | None,
                  figures_dir: str | None = None) -> str:
    """给编程助手的规范化提示词。

    硬性约束单独成段、编号列出：Magplot 的参数化编辑靠拦截 matplotlib 的
    savefig 才成立，助手一旦改用 PIL / plotly / 手写 SVG 等其他方式出图，
    整条 live-figure 链路（manifest / override / 导出）全部失效——这必须是
    对助手的硬性要求，而不是含在叙述里的默认假设。
    图库细节（如 paper_style.py）按实际存在与否动态给出，不硬编码任何
    具体图库的私有规范。"""
    lines = [
        "你在修改一篇论文的图表脚本（Python）。",
        f"目标脚本：{script}（在当前工作目录中）。",
        "",
        "硬性要求（外部系统靠这些约定解析产物，违反任何一条都会让图表无法再编辑）：",
        "1. 图必须用 matplotlib 生成，并经 Figure.savefig(...)（或图库自带的保存封装）"
        "落盘。禁止改用 PIL/Pillow、plotly、bokeh、seaborn 之外叠加的其他渲染后端、"
        "手写 SVG/PDF、subprocess 调外部绘图工具等方式出图——外部系统靠拦截 "
        "matplotlib 的 savefig 实现参数化编辑，其他方式生成的图完全无法再编辑。",
        "2. 保持既有输出文件名（stem）与出图数量不变。",
        "3. 只修改目标脚本；不要运行脚本（渲染由外部系统负责）；"
        "不要修改其他脚本或数据文件。",
    ]
    if figures_dir and (Path(figures_dir) / "paper_style.py").is_file():
        lines.append("4. 图库有共享样式 paper_style.py：沿用它既有的字体/字号/配色/"
                     "版面规范，不要修改 paper_style.py 本身。")
    ctx = context or {}
    ctx_lines = []
    if ctx.get("stem"):
        ctx_lines.append(f"用户当前编辑的是该脚本的输出面板：{ctx['stem']}。")
    if ctx.get("gid"):
        ctx_lines.append(f"用户在界面上选中的元素：{ctx['gid']}（{ctx.get('label', '')}）。")
    if ctx.get("overrides"):
        ctx_lines.append("用户已在界面上做了这些非破坏性修改（渲染时叠加的 override，"
                         f"代表期望状态，供参考）：{ctx['overrides']}")
    if ctx_lines:
        lines += ["", *ctx_lines]
    lines += [
        "",
        f"用户需求：{user_prompt}",
    ]
    return "\n".join(lines)


def run(agent: str, script: str, user_prompt: str, figures_dir: str,
        context: dict | None = None, on_event=None,
        model: str | None = None, effort: str | None = None,
        endpoint_id: str | None = None) -> str:
    """启动一次 AI 修改任务，返回 session id。事件经 on_event(name, data) 回调。"""
    script_path = Path(figures_dir) / script
    if not script_path.is_file():
        raise RuntimeError(f"脚本不存在: {script}")
    sid = uuid.uuid4().hex[:12]
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    _prune_snapshots()
    snap = SNAP_DIR / f"{sid}__{script}"
    shutil.copy2(script_path, snap)
    # sidecar：进程重启后 revert 仍可从磁盘找回（SESSIONS 只在内存）
    (SNAP_DIR / f"{sid}.json").write_text(json.dumps({
        "id": sid, "agent": agent, "script": script,
        "script_path": str(script_path), "snapshot": str(snap),
        "prompt": user_prompt, "started": time.time(),
        "model": model, "effort": effort,
    }, ensure_ascii=False), encoding="utf-8")

    prompt = _build_prompt(script, user_prompt, context, figures_dir)
    stderr_log = open(SNAP_DIR / f"{sid}.stderr.log", "wb", buffering=0)
    endpoint = ai_providers.resolve(agent, endpoint_id)
    cmd, extra_env = _cmd(agent, prompt, figures_dir, model=model,
                          effort=effort, endpoint=endpoint)
    LOG.info("AI 任务命令: %s（接口: %s）",
             " ".join(cmd[:-1] if agent == "codex" else cmd[:5]),
             endpoint["label"] if endpoint else "CLI 默认")
    # cmd[0] 是 CLI 可执行（或 node），末尾是提示词——PATH 增强按 cmd[0] 算
    env = _spawn_env(cmd[0], extra_env)
    proc = subprocess.Popen(
        cmd, cwd=figures_dir, env=env,
        stdin=subprocess.DEVNULL,  # 桌面 sidecar 的 stdin 是父进程死亡信号管道，不外传
        stdout=subprocess.PIPE, stderr=stderr_log,  # CLI 的 hook/统计噪音不进对话
        text=True, bufsize=1,
        # 显式 UTF-8：Windows 上 text=True 跟随系统区域编码（cp936），
        # CLI 回来的中文/JSON 一解码就炸，表现为「任务刚起就结束」
        encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    sess = {
        "id": sid, "agent": agent, "script": script, "prompt": user_prompt,
        "script_path": str(script_path), "status": "running", "transcript": [],
        "diff": "", "changed": False, "model": model, "effort": effort,
        "snapshot": str(snap), "proc": proc, "started": time.time(),
    }
    SESSIONS[sid] = sess
    ctx = context or {}
    ai_history.record_start({
        "id": sid, "project": str(Path(figures_dir).resolve()),
        "canvas": ctx.get("canvas"), "panel": ctx.get("stem"),
        "element": ctx.get("gid"), "provider": agent,
        "model": model, "effort": effort,
        "scope": ctx.get("scope"), "target": ctx.get("target"),
        "script": script, "prompt": user_prompt, "snapshot_path": str(snap),
    })
    emit = on_event or (lambda *_: None)

    def _pump():
        st: dict = {}
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                for kind, text in _classify(agent, line, st):
                    if not text:
                        continue
                    if kind != "delta":  # delta 只走 SSE，transcript 存终稿
                        sess["transcript"].append({"kind": kind, "text": text})
                    emit("ai.delta", {"session": sid, "kind": kind, "text": text})
                if time.time() - sess["started"] > TIMEOUT_S:
                    proc.kill()
                    sess["status"] = "timeout"
            proc.wait()
            stderr_log.close()
            new_text = script_path.read_text(encoding="utf-8", errors="replace")
            old_text = snap.read_text(encoding="utf-8", errors="replace")
            sess["changed"] = new_text != old_text
            sess["diff"] = "".join(difflib.unified_diff(
                old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=f"{script} (修改前)", tofile=f"{script} (修改后)", n=3))
            if sess["status"] == "running":
                sess["status"] = "done" if proc.returncode == 0 else "failed"
            LOG.info("AI 会话结束: %s %s status=%s changed=%s",
                     agent, script, sess["status"], sess["changed"])
            ai_history.record_end(sid, sess["status"], diff=sess["diff"],
                                  changed=sess["changed"],
                                  transcript=sess["transcript"])
            emit("ai.done", {"session": sid, "status": sess["status"],
                             "changed": sess["changed"], "diff": sess["diff"],
                             "script": script})
        except Exception as exc:  # noqa: BLE001
            sess["status"] = "failed"
            ai_history.record_end(sid, "failed", error=str(exc),
                                  transcript=sess.get("transcript") or [])
            emit("ai.done", {"session": sid, "status": "failed",
                             "changed": False, "diff": "", "error": str(exc),
                             "script": script})

    threading.Thread(target=_pump, daemon=True, name=f"ai-{sid}").start()
    return sid


def _load_sidecar(sid: str) -> dict | None:
    p = SNAP_DIR / f"{sid}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def revert(sid: str) -> dict:
    """恢复快照（回滚 AI 修改）。watcher 会自动作废渲染会话。
    进程重启后 SESSIONS 丢失也没关系——从磁盘 sidecar 找回。"""
    sess = SESSIONS.get(sid) or _load_sidecar(sid)
    if sess is None:
        raise RuntimeError("会话不存在（快照也未找到）")
    snap = Path(sess["snapshot"])
    if not snap.is_file():
        raise RuntimeError("快照文件已丢失，无法回滚")
    shutil.copy2(snap, sess["script_path"])
    LOG.info("AI 修改已回滚: %s（session %s）", sess["script"], sid)
    if sid in SESSIONS:
        SESSIONS[sid]["status"] = "reverted"
    ai_history.update_status(sid, "reverted")
    return {"ok": True, "script": sess["script"]}


def get(sid: str) -> dict | None:
    sess = SESSIONS.get(sid)
    if sess is not None:
        return {k: v for k, v in sess.items() if k not in ("proc",)}
    side = _load_sidecar(sid)
    if side is None:
        return None
    # 后端已重启：以 SQLite 历史为准（启动时 running 已被标为 interrupted）
    hist = ai_history.get(sid)
    if hist is not None:
        return {**side, "status": hist["status"], "transcript": hist["transcript"],
                "diff": hist["diff"], "changed": hist["changed"],
                "note": "后端已重启，记录来自历史库"}
    return {**side, "status": "interrupted", "transcript": [], "diff": "",
            "changed": None, "note": "后端已重启，仅可回滚"}


def cancel(sid: str) -> bool:
    sess = SESSIONS.get(sid)
    if sess and sess["status"] == "running":
        sess["proc"].kill()
        sess["status"] = "cancelled"
        ai_history.update_status(sid, "cancelled")
        return True
    return False


def interrupt_all() -> int:
    """项目切换 / 进程收尾前：终止所有运行中的会话并标记为已中断。
    快照仍在磁盘上，revert 入口保持可用。"""
    n = 0
    for sess in SESSIONS.values():
        if sess["status"] == "running":
            try:
                sess["proc"].kill()
            except OSError:
                pass
            sess["status"] = "interrupted"
            ai_history.update_status(sess["id"], "interrupted")
            n += 1
    return n
