"""改图助手的第三方 API 接入（用户级配置 + 启动 CLI 时的注入方式）。

思路参考 cc-switch（github.com/farion1231/cc-switch）对两家 CLI 的观察：
  * Claude Code 认 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` 环境变量，
    任何 Anthropic Messages 兼容的网关都能直接顶上去；
  * Codex 认 `config.toml` 里的 `model_provider` + `[model_providers.*]`，
    密钥从 `env_key` 指定的环境变量取。

**与 cc-switch 的关键区别：我们绝不改写用户的 `~/.claude/settings.json` 或
`~/.codex/config.toml`。** Tavotto 只是借用户已装好的 CLI 干一件小事（改图
脚本），没有资格接管他全局的 CLI 配置——用户在别的终端里跑 claude/codex
必须还是他自己那套。所以这里全部走「spawn 时注入」：环境变量 + codex 的
`-c` 临时覆盖，进程一退干干净净。

密钥存在用户配置目录的 config.json 里（明文，与两家 CLI 自己的做法一致）；
配置目录权限收到 0700，接口返回一律打码。

纯标准库，Flask 父进程 import。
"""
from __future__ import annotations

import json
import os
import re

from . import ai_agents, config

# codex 侧临时覆盖用的 provider id 与密钥环境变量名。用 tavotto 前缀是为了
# 不和用户自己 config.toml 里的 provider 撞名（`-c` 只在本次进程生效，但
# 同名会遮蔽用户的定义，日志里也会看不明白）。
CODEX_PROVIDER_ID = "tavotto"
CODEX_KEY_ENV = "TAVOTTO_CODEX_API_KEY"

WIRE_APIS = ("responses", "chat")

#: 协议族 → 该族的注入方式。**不再自己列一份 agent 名单**：谁支持接第三方
#: 接口由 `ai_agents.AGENT_REGISTRY` 的 `endpoint_family` 说了算，这里只认族。
#: 分叉过一次的教训就在眼前——注册表加了 Agent、这份忘了加，界面上那家的
#: 接口区块永远是空的，而没有任何一条用例会红。
FAMILY_ANTHROPIC = "anthropic"
FAMILY_OPENAI = "openai"


def agents() -> tuple[str, ...]:
    """支持接第三方接口的 agent id（唯一权威在 ai_agents 的注册表）。"""
    return ai_agents.endpoint_agents()


def _family(agent: str) -> str | None:
    rec = ai_agents.get_agent(agent)
    return rec.endpoint_family if rec else None


# 内置预设只提供「接口地址 + 协议 + 常见模型名」，绝不含任何密钥。
# 模型名会过时，所以在界面上可自由编辑——预设只是省去查文档。
PRESETS: list[dict] = [
    {"id": "anthropic", "label": "Anthropic 官方", "agent": "claude",
     "base_url": "", "models": ["sonnet", "opus", "haiku"],
     "note": "留空 = 用 claude CLI 自己的登录态"},
    {"id": "deepseek", "label": "DeepSeek", "agent": "claude",
     "base_url": "https://api.deepseek.com/anthropic",
     "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "moonshot", "label": "Kimi（月之暗面）", "agent": "claude",
     "base_url": "https://api.moonshot.cn/anthropic",
     "models": ["kimi-k2-turbo-preview", "kimi-k2-0905-preview"]},
    {"id": "zhipu", "label": "智谱 GLM", "agent": "claude",
     "base_url": "https://open.bigmodel.cn/api/anthropic",
     "models": ["glm-4.6", "glm-4.5-air"]},
    {"id": "openai", "label": "OpenAI 官方", "agent": "codex",
     "base_url": "", "wire_api": "responses", "models": [],
     "note": "留空 = 用 codex CLI 自己的登录态"},
    {"id": "dashscope", "label": "阿里云百炼（通义）", "agent": "codex",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "wire_api": "chat", "models": ["qwen3-coder-plus", "qwen-max"]},
    {"id": "deepseek-oai", "label": "DeepSeek（OpenAI 兼容）", "agent": "codex",
     "base_url": "https://api.deepseek.com/v1", "wire_api": "chat",
     "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "custom-claude", "label": "自定义（Anthropic 兼容）", "agent": "claude",
     "base_url": "", "models": []},
    {"id": "custom-codex", "label": "自定义（OpenAI 兼容）", "agent": "codex",
     "base_url": "", "wire_api": "chat", "models": []},
]

_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(text: str) -> str:
    return _ID_RE.sub("-", text.strip()).strip("-").lower()[:40] or "provider"


def _clean(rec: dict, existing_ids: set[str] = frozenset()) -> dict:
    """规范化一条供应商配置；非法值就地纠正，绝不写进配置一个坏形状。"""
    known = agents()
    fallback = known[0] if known else ""
    agent = rec.get("agent") if rec.get("agent") in known else fallback
    pid = _slug(str(rec.get("id") or rec.get("label") or agent))
    while pid in existing_ids:
        pid = f"{pid}-2"
    models = [str(m).strip() for m in (rec.get("models") or []) if str(m).strip()]
    default_model = str(rec.get("default_model") or "").strip() or None
    if default_model and default_model not in models:
        models.insert(0, default_model)
    wire = rec.get("wire_api")
    return {
        "id": pid,
        "label": str(rec.get("label") or pid).strip()[:60],
        "agent": agent,
        "base_url": str(rec.get("base_url") or "").strip().rstrip("/"),
        "api_key": str(rec.get("api_key") or "").strip(),
        "models": models,
        "default_model": default_model,
        "wire_api": wire if wire in WIRE_APIS else "chat",
    }


def _ai() -> dict:
    return config.ai_settings()


def list_providers() -> list[dict]:
    raw = _ai().get("providers")
    return [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []


def get(pid: str | None) -> dict | None:
    if not pid:
        return None
    for p in list_providers():
        if p.get("id") == pid:
            return p
    return None


def public(rec: dict) -> dict:
    """给界面看的形状：密钥只留「有没有」和尾四位，绝不整串回传。"""
    key = str(rec.get("api_key") or "")
    return {**{k: v for k, v in rec.items() if k != "api_key"},
            "has_key": bool(key),
            "key_hint": f"…{key[-4:]}" if len(key) >= 4 else ""}


def save(rec: dict) -> list[dict]:
    """新增或更新一条。api_key 缺省 = 保留原值（界面不回显密钥，也就不该要求重填）。"""
    providers = list_providers()
    incoming_id = _slug(str(rec.get("id") or ""))
    idx = next((i for i, p in enumerate(providers) if p.get("id") == incoming_id), -1)
    if idx >= 0:
        merged = {**providers[idx], **{k: v for k, v in rec.items() if v is not None}}
        if not str(rec.get("api_key") or "").strip():
            merged["api_key"] = providers[idx].get("api_key", "")
        providers[idx] = _clean({**merged, "id": incoming_id})
    else:
        taken = {p.get("id") for p in providers}
        providers.append(_clean(rec, taken))
    _write(providers)
    return providers


def delete(pid: str) -> list[dict]:
    providers = [p for p in list_providers() if p.get("id") != pid]
    active = dict(_ai().get("active_provider") or {})
    for agent, cur in list(active.items()):
        if cur == pid:
            active[agent] = None
    config.set_ai_settings({"providers": providers, "active_provider": active})
    _harden()
    return providers


def _write(providers: list[dict]) -> None:
    config.set_ai_settings({"providers": providers})
    _harden()


def _harden() -> None:
    """配置目录/文件收权限（存了密钥）。Windows 上 chmod 基本无效，静默跳过。"""
    if os.name == "nt":
        return
    for p in (config.config_dir(), config.config_path()):
        try:
            p.chmod(0o700 if p.is_dir() else 0o600)
        except OSError:
            pass


def active_id(agent: str) -> str | None:
    return (_ai().get("active_provider") or {}).get(agent)


def set_active(agent: str, pid: str | None) -> dict:
    if agent not in agents():
        raise ValueError(f"未知 agent: {agent}")
    if pid and get(pid) is None:
        raise ValueError(f"供应商不存在: {pid}")
    active = dict(_ai().get("active_provider") or {})
    active[agent] = pid or None
    config.set_ai_settings({"active_provider": active})
    return active


def resolve(agent: str, pid: str | None = None) -> dict | None:
    """本次任务实际使用的供应商：显式指定 > 该 agent 的当前选择 > 无（官方登录态）。

    `pid == ""` 是「明确要用官方登录态」，与「没指定」区分开——不然用户在
    界面上选回官方会被当成没选，继续走上一个第三方网关。
    """
    if pid == "":
        return None
    rec = get(pid) if pid else get(active_id(agent))
    if rec is None or rec.get("agent") != agent:
        return None
    return rec


def _toml(value: str) -> str:
    """codex `-c key=value` 的 value 按 TOML 解析，字符串必须带引号。
    JSON 字符串字面量与 TOML 基本字符串的转义规则在这里完全兼容。"""
    return json.dumps(value, ensure_ascii=False)


def spawn_overrides(agent: str, rec: dict | None,
                    model: str | None = None) -> tuple[list[str], dict[str, str]]:
    """→ (追加到命令行的参数, 追加到环境的变量)。rec 为 None 时两者都空。"""
    if rec is None:
        return [], {}
    base_url = str(rec.get("base_url") or "").strip()
    api_key = str(rec.get("api_key") or "").strip()

    family = _family(agent)

    if family == FAMILY_ANTHROPIC:
        env: dict[str, str] = {}
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if api_key:
            # 两个名字都给：不同网关认的字段不一样，多给一个不会有副作用
            env["ANTHROPIC_AUTH_TOKEN"] = api_key
            env["ANTHROPIC_API_KEY"] = api_key
        chosen = model or rec.get("default_model")
        if chosen:
            # 第三方网关基本不认 sonnet/opus 这些别名，把三档默认模型一起
            # 钉死，`--model` 之外的内部调用（小模型任务）才不会打到不存在的模型上
            for key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                        "ANTHROPIC_DEFAULT_OPUS_MODEL",
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
                env[key] = chosen
        return [], env

    if family == FAMILY_OPENAI:
        if not base_url:
            return [], {}
        pid = CODEX_PROVIDER_ID
        args = [
            "-c", f"model_provider={_toml(pid)}",
            "-c", f"model_providers.{pid}.name={_toml(rec.get('label') or pid)}",
            "-c", f"model_providers.{pid}.base_url={_toml(base_url)}",
            "-c", f"model_providers.{pid}.wire_api="
                  f"{_toml(rec.get('wire_api') or 'chat')}",
            "-c", f"model_providers.{pid}.env_key={_toml(CODEX_KEY_ENV)}",
        ]
        env = {CODEX_KEY_ENV: api_key} if api_key else {}
        return args, env

    return [], {}


def models_for(agent: str, pid: str | None = None) -> list[str]:
    rec = resolve(agent, pid)
    return list(rec.get("models") or []) if rec else []
