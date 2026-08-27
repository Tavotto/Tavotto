"""AI 桥：让本机的编码 Agent CLI 非交互地深度修改 fig 脚本。

流程：快照脚本 → spawn CLI（cwd=figures 目录）→ stdout 逐行流式回调（SSE）→
进程结束后对比快照生成 unified diff。CLI 直接改动脚本文件；文件 watcher
随即作废渲染会话，前端重渲染即可看到效果——用户不满意可 revert 恢复快照。

**这里只管会话编排**（快照 / SSE / diff / revert / cancel / history）。
「支持哪些 Agent、怎么找到它、怎么拼命令行、输出怎么分类、装哪个 npm 包」
一律在 `engine/ai_agents.py` 的注册表里，本模块遍历它，不写
`if agent == "codex"` 这种分支。第三方接口的存取与注入在
`engine/ai_providers.py`；注入结果由本模块算好后交给适配器拼装。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import ai_agents, ai_history, ai_providers, config
from .ai_agents import spawn_env as _spawn_env  # noqa: F401 — run() 与旧调用方仍认这个名字
from .runtime import CREATE_NO_WINDOW  # noqa: F401 — 重导出，历史调用方仍认这个名字

LOG = logging.getLogger("tavotto.ai")

SNAP_DIR = config.data_dir() / "cache" / "ai_snapshots"
TIMEOUT_S = 900  # 单次 AI 任务上限
SNAP_KEEP = 20  # 快照保留的最近会话数（超龄的 revert 入口早已不可见）
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


# ---------------------------------------------------------------------------
# 稳定错误码（app 层原样转成 JSON；前端按 code 出人话，后端不猜用户语言）
# ---------------------------------------------------------------------------
class AgentError(RuntimeError):
    """带稳定 code 的 Agent 操作失败。code 一旦发布不能改名。"""

    def __init__(self, code: str, params: dict | None = None, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.params = params or {}


def require_agent(agent_id: str) -> ai_agents.AgentDefinition:
    """按 id 取适配器；**未知 id 一律当场拒绝**，绝不继续往下传。

    这是「不把请求体里的字符串拼进命令行」的那道闸：安装包名、可执行文件、
    第三方接口全部只从适配器上取，请求体只提供一个必须命中注册表的 id。
    """
    agent = ai_agents.get_agent(agent_id)
    if agent is None:
        raise AgentError("ai_agent_unknown", {"agent": agent_id})
    return agent


# ---------------------------------------------------------------------------
# 能力探测
# ---------------------------------------------------------------------------
_CAPS_CACHE: dict = {}

#: 界面状态机的六个值。语义见 docs/adr/0015。
STATES = ("ready", "installed", "needs_auth", "broken", "not_installed", "disabled")


def _state(
    installed: bool, enabled: bool, res: ai_agents.Resolution, endpoint_backed: bool = False
) -> str:
    """把探测结论收敛成界面状态。

    「没装」不是错误，「装了但起不来」才是——两者必须分开说：前者的下一步是
    去装，后者的下一步是修（或改用另一个候选）。登录状态查不准时一律说
    「已安装」，**绝不为了让那一行变绿而偷偷发一个真实 Prompt**。

    `endpoint_backed` = 第三方接口**真的接管了这次调用**（`spawn_overrides`
    确实产出了参数或环境变量）。这时 CLI 自己的登录态**与能不能派活无关**
    ——注入那套凭据的全部意义就是让 CLI 不必用官方登录跑起来。判据的主语
    是「这次运行会不会成」，不是「这个 CLI 登没登录过」；把后者当成前者，
    表现是「配好了 DeepSeek 的用户发现 Codex 从选择器里整个消失了」。
    就绪检查的原始结论仍然照实记在 diagnostics 里，只是不再当闸。
    """
    if not installed:
        return "broken" if res.broken_path else "not_installed"
    if not enabled:
        return "disabled"
    if res.readiness.state == "ready":
        return "ready"
    if res.readiness.state == "needs_auth" and not endpoint_backed:
        return "needs_auth"
    return "installed"


def _effective_enabled(saved: dict, installed: bool) -> bool:
    """`enabled` 是三态：没表过态 = 跟着「装没装」走。

    用户从没动过开关时，装上了就该能用（不该逼他先去设置里打开一次）；
    **明确关过就一直关着**，下次探测成功也不自动翻回来。
    """
    stored = saved.get("enabled")
    return bool(stored) if isinstance(stored, bool) else installed


def _agent_caps(agent: ai_agents.AgentDefinition, saved: dict, npm_available: bool) -> dict:
    res = ai_agents.resolve(agent)
    installed = res.argv is not None
    enabled = _effective_enabled(saved, installed)

    caps = agent.model_capabilities() if installed else ai_agents.ModelCapabilities()
    models = list(caps.models)
    default_model = caps.default_model

    endpoint = None
    endpoint_backed = False
    if installed and agent.endpoint_family:
        endpoint = ai_providers.resolve(agent.id)
        if endpoint and endpoint.get("models"):
            # 接了第三方接口时改用该接口自己填的模型清单（网关认的模型名与
            # 官方无关）
            models = list(endpoint["models"])
            default_model = endpoint.get("default_model") or models[0]
        # 「算不算真的接管了」**问注入方自己**，不在这里另写一份判据：
        # codex 侧 base_url 为空时 spawn_overrides 其实一个字节都不注入，
        # 那种情况 CLI 自己的登录态仍然算数。两份规则迟早分叉，而分叉的
        # 表现是「界面说可用、一跑就报未登录」。
        args, env = ai_providers.spawn_overrides(agent.id, endpoint)
        endpoint_backed = bool(args or env)

    state = _state(installed, enabled, res, endpoint_backed)

    info: dict = {
        "id": agent.id,
        "display_name": agent.display_name,
        "icon_key": agent.icon_key,
        "state": state,
        "installed": installed,
        "enabled": enabled,
        # 「能不能真的派活给它」——界面据此过滤选择器，后端 run 也照这条判。
        "usable": enabled and installed and state in ("ready", "installed"),
        "version": res.version,
        "executable_path": res.path,
        "path_override": str(saved["path_override"]) if saved.get("path_override") else None,
        "detection_source": res.source,
        "models": models,
        "default_model": default_model,
        "efforts": list(caps.efforts) if agent.supports_effort_selection else [],
        "default_effort": caps.default_effort if agent.supports_effort_selection else None,
        "endpoint": ai_providers.public(endpoint) if endpoint else None,
        "active_endpoint_id": (ai_providers.active_id(agent.id) if agent.endpoint_family else None),
        "features": {
            "third_party_endpoints": bool(agent.endpoint_family),
            "model_selection": agent.supports_model_selection,
            "effort_selection": agent.supports_effort_selection,
            "wire_api_selection": agent.supports_wire_api,
            "readiness_probe": agent.supports_readiness_probe,
        },
        # 诊断（详情页的折叠区）：找过哪儿、第一个坏候选、就绪检查的结论。
        # **`argv` 不在这里**——前端没有消费者，那就不公开。
        "diagnostics": {
            "searched": list(res.searched),
            "broken_path": res.broken_path,
            "readiness": res.readiness.state,
            "readiness_detail": res.readiness.detail,
        },
    }
    if agent.install_spec:
        info["install"] = {
            "method": agent.install_spec.method,
            "package": agent.install_spec.package,
            "available": npm_available,
            **install_status(agent.id),
        }
    return info


def capabilities(refresh: bool = False) -> dict:
    """实测本机每个已注册 Agent：安装 / 版本 / 就绪 / 模型 / 推理强度。

    遍历 `ai_agents.AGENT_REGISTRY`，一条硬编码分支都没有——加第四个 Agent
    只需要往注册表里放一个适配器。模型与推理强度由适配器各自声明（两家能力
    不同构：claude CLI 不暴露推理强度开关，就不给这一档）。
    """
    global _CAPS_CACHE
    if refresh:
        # refresh = 真的重新探测。解析缓存不清的话，改完自定义路径或点
        # 「重新检测」拿到的仍是上一次的结论——新路径根本没被 --version
        # 验过，界面于是一直说「未检测到」（issue #89 的另一半）。
        ai_agents.clear_cache()
    elif _CAPS_CACHE:
        return _CAPS_CACHE
    saved = config.ai_agent_settings()
    npm_available = _npm_argv() is not None
    _CAPS_CACHE = {
        "agents": [
            _agent_caps(a, saved.get(a.id) or {}, npm_available) for a in ai_agents.agents()
        ],
        "endpoints": [ai_providers.public(p) for p in ai_providers.list_providers()],
        "presets": ai_providers.PRESETS,
        "checked_at_ms": int(time.time() * 1000),
    }
    return _CAPS_CACHE


def invalidate_capabilities() -> None:
    """改过路径 / 开关 / 第三方接口后必须清缓存，否则界面一直是旧探测结果。"""
    global _CAPS_CACHE
    _CAPS_CACHE = {}
    ai_agents.clear_cache()


def agent_caps(agent_id: str) -> dict | None:
    """某个 Agent 的当前能力条目（不重新探测）。"""
    for info in capabilities().get("agents", []):
        if info["id"] == agent_id:
            return info
    return None


def require_usable(agent_id: str) -> dict:
    """派活之前的最后一道闸。**判据就是 `usable` 那一个字段**。

    不能只靠前端隐藏——这些状态的 Agent 仍可以被直接调 API 唤起。
    也不能在这里重列一遍「installed and enabled and …」：那是 `usable` 的
    第二份定义，而两份定义分叉的表现是「界面把它藏了、API 还放它进来」。
    先分别判 installed / enabled / needs_auth 只为**给出对得上的 code**，
    最后那道 `usable` 才是兜底——将来 usable 多一个成因，这里自动跟上。
    """
    require_agent(agent_id)
    info = agent_caps(agent_id)
    if info is None:  # 理论上到不了
        raise AgentError("ai_agent_unknown", {"agent": agent_id})
    if not info["installed"]:
        raise AgentError("ai_agent_not_installed", {"agent": agent_id})
    if not info["enabled"]:
        raise AgentError("ai_agent_disabled", {"agent": agent_id})
    if info["state"] == "needs_auth":
        # 注意这条排在 endpoint 判定**之后**（见 `_state`）：接了第三方接口
        # 的用户本来就不需要 CLI 的官方登录，不该被这道闸挡住。
        raise AgentError("ai_agent_needs_auth", {"agent": agent_id})
    if not info["usable"]:
        raise AgentError("ai_agent_not_usable", {"agent": agent_id})
    return info


# ---------------------------------------------------------------------------
# 通用 Agent 设置（启用开关 / 自定义可执行文件）
# ---------------------------------------------------------------------------
def set_agent_enabled(agent_id: str, enabled: bool) -> dict:
    """开关某个 Agent 在 Tavotto 里的使用。

    只影响 Tavotto 用不用它：**不卸载 CLI、不改 CLI 自己的任何配置**。
    完全没装、也没有有效自定义路径的 Agent 不允许打开——那只会造出一个
    「开着但一用就报错」的状态。
    """
    require_agent(agent_id)
    info = agent_caps(agent_id)
    if enabled and not (info and info["installed"]):
        raise AgentError("ai_agent_not_installed", {"agent": agent_id})
    config.set_ai_agent_settings(agent_id, {"enabled": bool(enabled)})
    invalidate_capabilities()
    return capabilities(refresh=True)


def set_agent_path_override(agent_id: str, path: str | None) -> dict:
    """指定 / 清除自定义可执行文件。

    非空值走**与自动探测同一套** shim 解析 + `--version` 启动验证：验不过
    就抛错、一个字节都不写——用户原来那份有效设置绝不能被一次手滑清掉。
    只接受一个文件路径，不接受任意 shell 命令串。
    """
    agent = require_agent(agent_id)
    value = (path or "").strip()
    if not value:
        # 显式恢复自动检测
        config.set_ai_agent_settings(agent_id, {"path_override": None})
        invalidate_capabilities()
        return capabilities(refresh=True)
    res = ai_agents.validate_executable(agent, value)
    if res.argv is None:
        # 两个 code 分开抛（而不是三元表达式选一个）：门禁按字面量扫源码，
        # 拼出来的 code 它看不见，而看不见的 code 不会被要求配双语文案。
        if res.error == "timeout":
            raise AgentError("ai_agent_probe_timeout", {"path": value})
        raise AgentError("ai_agent_executable_invalid", {"path": value})
    config.set_ai_agent_settings(agent_id, {"path_override": value})
    invalidate_capabilities()
    return capabilities(refresh=True)


# ---------------------------------------------------------------------------
# 一键安装（npm 用户级全局包；给「根本没装过 CLI」的用户一条不出软件的路）
# ---------------------------------------------------------------------------
INSTALL_TIMEOUT_S = 600
_INSTALLS: dict[str, dict] = {}  # agent -> {"status", "code", "log", "started"}
_INSTALL_LOCK = threading.Lock()


def _npm_argv() -> list[str] | None:
    """npm 可执行路径。npm.cmd 直接跑没有元字符问题——install 的参数全是
    适配器里写死的常量，不含用户输入，无须经 resolve_shim 绕开 cmd.exe。"""
    p = shutil.which("npm")
    if not p:
        dirs = [d for d in ai_agents.search_dirs("npm") if Path(d).is_dir()]
        if dirs:
            p = shutil.which("npm", path=os.pathsep.join(dirs))
    return [p] if p else None


def install_status(agent_id: str) -> dict:
    st = _INSTALLS.get(agent_id)
    if not st:
        return {"status": "idle"}
    return {k: st[k] for k in ("status", "code", "log") if k in st}


def start_install(agent_id: str) -> dict:
    """后台 `npm install -g <包>`；结束后**重新真探测**才定成败。

    包名只从适配器的固定 install spec 上取，绝不把请求体里的字符串拼进
    命令行；不用 `shell=True`，参数一律数组。
    """
    agent = require_agent(agent_id)
    spec = agent.install_spec
    if spec is None:
        raise AgentError("ai_agent_install_unsupported", {"agent": agent_id})
    with _INSTALL_LOCK:
        st = _INSTALLS.get(agent_id)
        if st and st.get("status") == "running":
            return install_status(agent_id)
        npm = _npm_argv()
        if npm is None:
            # 现场没有 npm：结构化告知，让界面引导用户先装 Node.js LTS——
            # 绝不静默下载 Node 安装器（那是另一个量级的越权）
            _INSTALLS[agent_id] = {"status": "error", "code": "npm_missing"}
            return install_status(agent_id)
        state: dict = {"status": "running", "started": time.time()}
        _INSTALLS[agent_id] = state

    def work() -> None:
        try:
            out = subprocess.run(
                [*npm, "install", "-g", spec.package],
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT_S,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            state["log"] = ((out.stdout or "") + "\n" + (out.stderr or "")).strip()[-4000:]
            if out.returncode != 0:
                state.update(status="error", code="npm_failed")
                return
            # npm 说成了不算数：重新探测，CLI 真能 --version 才算装上
            invalidate_capabilities()
            info = None
            for entry in capabilities(refresh=True).get("agents", []):
                if entry["id"] == agent_id:
                    info = entry
                    break
            if info and info["installed"]:
                state.update(status="done")
            else:
                state.update(status="error", code="installed_but_not_found")
        except subprocess.TimeoutExpired:
            state.update(
                status="error",
                code="timeout",
                log=f"npm install timed out after {INSTALL_TIMEOUT_S}s",
            )
        except OSError as exc:
            state.update(status="error", code="spawn_failed", log=str(exc))
        finally:
            LOG.info("npm install %s -> %s", agent_id, state.get("status"))

    threading.Thread(target=work, daemon=True, name=f"ai-install-{agent_id}").start()
    return install_status(agent_id)


# ---------------------------------------------------------------------------
# 命令构造与输出分类（全部委托给适配器）
# ---------------------------------------------------------------------------
def _cmd(
    agent_id: str,
    prompt: str,
    cwd: str,
    model: str | None = None,
    effort: str | None = None,
    endpoint: dict | None = None,
) -> tuple[list[str], dict[str, str]]:
    """→ (命令行, 需要追加到环境的变量)。第三方接口全部走这两样注入，
    绝不改写用户自己的 ~/.claude 或 ~/.codex 配置。"""
    agent = require_agent(agent_id)
    res = ai_agents.resolve(agent)
    if res.argv is None:
        raise AgentError("ai_agent_not_installed", {"agent": agent_id})
    extra_args, extra_env = ai_providers.spawn_overrides(agent_id, endpoint, model)
    spec = agent.build_command(
        ai_agents.RunContext(
            argv=list(res.argv),
            prompt=prompt,
            cwd=cwd,
            model=model,
            effort=effort,
            endpoint_args=list(extra_args),
            endpoint_env=dict(extra_env),
        )
    )
    return list(spec.argv), dict(spec.env)


def _classify(agent_id: str, line: str, st: dict) -> list[tuple[str, str]]:
    """把 CLI 原始输出分类为事件列表。kind：
      delta    — 当前回答的流式增量（只走 SSE，不进 transcript）
      message  — 一段完整回答（终结当前流式气泡）
      thinking / action — 过程（前端默认折叠）
    st 是每会话的解析状态（codex 增量去重用）。"""
    agent = ai_agents.get_agent(agent_id)
    return agent.classify_event(line, st) if agent else []


def _build_prompt(
    script: str, user_prompt: str, context: dict | None, figures_dir: str | None = None
) -> str:
    """给编程助手的规范化提示词。

    硬性约束单独成段、编号列出：Tavotto 的参数化编辑靠拦截 matplotlib 的
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
        "3. 只修改目标脚本；不要运行脚本（渲染由外部系统负责）；不要修改其他脚本或数据文件。",
    ]
    if figures_dir and (Path(figures_dir) / "paper_style.py").is_file():
        lines.append(
            "4. 图库有共享样式 paper_style.py：沿用它既有的字体/字号/配色/"
            "版面规范，不要修改 paper_style.py 本身。"
        )
    ctx = context or {}
    ctx_lines = []
    if ctx.get("stem"):
        ctx_lines.append(f"用户当前编辑的是该脚本的输出面板：{ctx['stem']}。")
    if ctx.get("gid"):
        ctx_lines.append(f"用户在界面上选中的元素：{ctx['gid']}（{ctx.get('label', '')}）。")
    if ctx.get("overrides"):
        ctx_lines.append(
            "用户已在界面上做了这些非破坏性修改（渲染时叠加的 override，"
            f"代表期望状态，供参考）：{ctx['overrides']}"
        )
    if ctx_lines:
        lines += ["", *ctx_lines]
    lines += [
        "",
        f"用户需求：{user_prompt}",
    ]
    return "\n".join(lines)


def run(
    agent: str,
    script: str,
    user_prompt: str,
    figures_dir: str,
    context: dict | None = None,
    on_event=None,
    model: str | None = None,
    effort: str | None = None,
    endpoint_id: str | None = None,
) -> str:
    """启动一次 AI 修改任务，返回 session id。事件经 on_event(name, data) 回调。

    **先过 `require_usable`**：未知 / 未安装 / 被用户关掉的 Agent 在这里就被
    拒绝。只靠前端隐藏是不够的——这个端点可以被直接调。
    """
    require_usable(agent)
    script_path = Path(figures_dir) / script
    if not script_path.is_file():
        raise RuntimeError(f"脚本不存在: {script}")
    sid = uuid.uuid4().hex[:12]
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    _prune_snapshots()
    snap = SNAP_DIR / f"{sid}__{script}"
    shutil.copy2(script_path, snap)
    # sidecar：进程重启后 revert 仍可从磁盘找回（SESSIONS 只在内存）
    (SNAP_DIR / f"{sid}.json").write_text(
        json.dumps(
            {
                "id": sid,
                "agent": agent,
                "script": script,
                "script_path": str(script_path),
                "snapshot": str(snap),
                "prompt": user_prompt,
                "started": time.time(),
                "model": model,
                "effort": effort,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    prompt = _build_prompt(script, user_prompt, context, figures_dir)
    stderr_log = open(SNAP_DIR / f"{sid}.stderr.log", "wb", buffering=0)
    endpoint = ai_providers.resolve(agent, endpoint_id)
    cmd, extra_env = _cmd(agent, prompt, figures_dir, model=model, effort=effort, endpoint=endpoint)
    # 提示词按**值**从日志里摘掉（它在命令行里的位置各家不同：codex 在末尾、
    # claude 在 `-p` 后面）。按 agent 写死下标那种做法，加第三个 Agent 时
    # 会静默把用户的提示词整条写进日志。
    LOG.info(
        "AI 任务命令: %s（接口: %s）",
        " ".join("<prompt>" if part == prompt else part for part in cmd),
        endpoint["label"] if endpoint else "CLI 默认",
    )
    # cmd[0] 是 CLI 可执行（或 node），末尾是提示词——PATH 增强按 cmd[0] 算
    env = _spawn_env(cmd[0], extra_env)
    proc = subprocess.Popen(
        cmd,
        cwd=figures_dir,
        env=env,
        stdin=subprocess.DEVNULL,  # 桌面 sidecar 的 stdin 是父进程死亡信号管道，不外传
        stdout=subprocess.PIPE,
        stderr=stderr_log,  # CLI 的 hook/统计噪音不进对话
        text=True,
        bufsize=1,
        # 显式 UTF-8：Windows 上 text=True 跟随系统区域编码（cp936），
        # CLI 回来的中文/JSON 一解码就炸，表现为「任务刚起就结束」
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    sess = {
        "id": sid,
        "agent": agent,
        "script": script,
        "prompt": user_prompt,
        "script_path": str(script_path),
        "status": "running",
        "transcript": [],
        "diff": "",
        "changed": False,
        "model": model,
        "effort": effort,
        "snapshot": str(snap),
        "proc": proc,
        "started": time.time(),
    }
    SESSIONS[sid] = sess
    ctx = context or {}
    ai_history.record_start(
        {
            "id": sid,
            "project": str(Path(figures_dir).resolve()),
            "canvas": ctx.get("canvas"),
            "panel": ctx.get("stem"),
            "element": ctx.get("gid"),
            "provider": agent,
            "model": model,
            "effort": effort,
            "scope": ctx.get("scope"),
            "target": ctx.get("target"),
            "script": script,
            "prompt": user_prompt,
            "snapshot_path": str(snap),
        }
    )
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
            sess["diff"] = "".join(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"{script} (修改前)",
                    tofile=f"{script} (修改后)",
                    n=3,
                )
            )
            if sess["status"] == "running":
                sess["status"] = "done" if proc.returncode == 0 else "failed"
            LOG.info(
                "AI 会话结束: %s %s status=%s changed=%s",
                agent,
                script,
                sess["status"],
                sess["changed"],
            )
            ai_history.record_end(
                sid,
                sess["status"],
                diff=sess["diff"],
                changed=sess["changed"],
                transcript=sess["transcript"],
            )
            emit(
                "ai.done",
                {
                    "session": sid,
                    "status": sess["status"],
                    "changed": sess["changed"],
                    "diff": sess["diff"],
                    "script": script,
                },
            )
        except Exception as exc:  # noqa: BLE001
            sess["status"] = "failed"
            ai_history.record_end(
                sid, "failed", error=str(exc), transcript=sess.get("transcript") or []
            )
            emit(
                "ai.done",
                {
                    "session": sid,
                    "status": "failed",
                    "changed": False,
                    "diff": "",
                    "error": str(exc),
                    "script": script,
                },
            )

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
        return {
            **side,
            "status": hist["status"],
            "transcript": hist["transcript"],
            "diff": hist["diff"],
            "changed": hist["changed"],
            "note": "后端已重启，记录来自历史库",
        }
    return {
        **side,
        "status": "interrupted",
        "transcript": [],
        "diff": "",
        "changed": None,
        "note": "后端已重启，仅可回滚",
    }


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
