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
from urllib.parse import urlparse

from . import ai_agents, ai_catalog, config
from .agent_runner.wires import ANTHROPIC_EFFORTS, RESPONSES_EFFORTS, check_base_url

# codex 侧临时覆盖用的 provider id 与密钥环境变量名。用 tavotto 前缀是为了
# 不和用户自己 config.toml 里的 provider 撞名（`-c` 只在本次进程生效，但
# 同名会遮蔽用户的定义，日志里也会看不明白）。
CODEX_PROVIDER_ID = "tavotto"
CODEX_KEY_ENV = "TAVOTTO_CODEX_API_KEY"

#: Codex 新版已移除 `wire_api = "chat"`（读到就整个启动失败），所以只剩 Responses。
#: 这里**结构上**不再有 chat：预设、`_clean`、`spawn_overrides` 都不可能产出它。
WIRE_APIS = ("responses",)

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


DEEPSEEK_CODEX_ID = "deepseek-codex"

# 内置预设的**唯一权威是 `engine/ai_catalog.json`**（厂商目录；规则见 ai_bridge 细则「第三方 API 接入」）。
# 只提供「接口地址 + 申请 Key 的页面 + 常见模型名」，绝不含任何密钥。模型名会过时，所以界面上
# 可自由编辑，还能对已存接口「从服务商获取」实时列表（`live_models`）。
# 加载失败在 import 时就抛 `ai_catalog.CatalogError`——宁可大声失败，也不让设置页静默没有预设。
PRESETS: list[dict] = ai_catalog.presets()

# 旧版 codex 预设（wire_api=chat）的已知厂商 → 改由 Claude Code 接入时用的预设 id。
# 只认主机名精确相等：coding.dashscope.aliyuncs.com 等是另一套 Key，不在此列。
# 百炼不在 Codex 侧迁移：官方 Responses 地址带用户的 WorkspaceId，我们推不出来。
_LEGACY_CHAT_HOST_PRESET = {
    "dashscope.aliyuncs.com": "dashscope",
}

# 旧版 codex 预设里 DeepSeek 已退役的模型名 → 新 Responses 预设的型号。
_DEEPSEEK_RETIRED_MODELS = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

MIGRATE_CODEX_RESPONSES = "codex_responses"
MIGRATE_CLAUDE = "claude"

INCOMPATIBLE_CODEX_CHAT = "codex_chat_removed"

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
    out_extra: dict = {}
    # 账号登录（设备码 OAuth，engine/ai_oauth.py）的记录：令牌只存这里，且只能由 ai_oauth 的
    # `store_oauth` / `update_oauth` 写入（`save()` 会剥掉请求体里的这两个键）
    if rec.get("auth") == "oauth" and isinstance(rec.get("oauth"), dict):
        out_extra = {"auth": "oauth", "oauth": dict(rec["oauth"])}
    # 保存时试取型号清单被服务商拒绝（密钥不对）：记下稳定原因码，连接视图据此给 state="error"；
    # 只认目录里的原因码，且只有真被拒才留（断网这类暂时性失败不落盘，免得永远显示出错）
    if rec.get("last_error") in ai_catalog.REASONS:
        out_extra = {**out_extra, "last_error": rec["last_error"]}
    return {
        **out_extra,
        "id": pid,
        "label": str(rec.get("label") or pid).strip()[:60],
        "agent": agent,
        "base_url": str(rec.get("base_url") or "").strip().rstrip("/"),
        "api_key": str(rec.get("api_key") or "").strip(),
        "models": models,
        "default_model": default_model,
        "wire_api": wire if wire in WIRE_APIS else "responses",
    }


def _ai() -> dict:
    return config.ai_settings()


def _providers_in(ai: dict) -> list[dict]:
    raw = ai.get("providers")
    return [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []


def list_providers() -> list[dict]:
    return _providers_in(_ai())


def _update(fn):
    """接口记录的读-改-写**唯一入口**：`fn(ai 快照) -> (patch, 返回值)` 在 `config.update_ai_settings`
    的同一把锁里运行，读到的列表就是写回时的基线（账号登录写令牌与接口增删改交错也不丢更新）。
    `fn` 里只许用传进来的快照，不许再调 `_ai()` / `list_providers()` / `get()`（会取同一把不可重入的锁）。
    `_harden()` 在事务之后。"""
    box: list = []

    def inner(ai: dict) -> dict:
        patch, result = fn(ai)
        box.append(result)
        return patch

    config.update_ai_settings(inner)
    _harden()
    return box[0]


def _find(providers: list[dict], pid: str | None) -> dict | None:
    return next((p for p in providers if p.get("id") == pid), None) if pid else None


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
    oauth = rec.get("oauth") if rec.get("auth") == "oauth" else None
    signed_in = bool(isinstance(oauth, dict) and oauth.get("access"))
    out = {
        # 令牌（oauth 整个子对象）一个字节都不回传：只给 auth / signed_in / 过期时刻 / 厂商 / 区域
        **{k: v for k, v in rec.items() if k not in ("api_key", "oauth")},
        "has_key": bool(key) or signed_in,
        "key_hint": f"…{key[-4:]}" if len(key) >= 4 else "",
        "icon_key": ai_catalog.icon_for(
            str(rec.get("agent") or ""), str(rec.get("base_url") or "")
        ),
        "incompatible": incompatibility(rec),
        "migratable": migration_kind(rec),
    }
    if rec.get("auth") == "oauth":
        o = oauth if isinstance(oauth, dict) else {}
        out.update(
            auth="oauth",
            signed_in=signed_in,
            oauth_expires_ms=o.get("expires_ms") if signed_in else None,
            oauth_provider=o.get("provider"),
            oauth_region=o.get("region"),
        )
    return out


def incompatibility(rec: dict | None) -> str | None:
    """这条记录现在还能不能用；不能用就返回稳定的原因码，否则 None。

    **判据只在这一处**（前端只消费 `public()` 里的 `incompatible`，`ai_bridge.run`
    在 spawn 之前拒绝）。已存的旧记录是 `_clean` 之前写下的原始值：codex +
    `wire_api="chat"` 是 Codex 已不再支持的协议，一跑就整个启动失败。
    """
    if rec and rec.get("agent") == "codex" and rec.get("wire_api") == "chat":
        return INCOMPATIBLE_CODEX_CHAT
    return None


def _host(rec: dict | None) -> str:
    return (urlparse(str((rec or {}).get("base_url") or "")).hostname or "").lower()


def migration_kind(rec: dict | None) -> str | None:
    """不兼容的旧 codex 记录能怎么一步迁移：`"codex_responses"`（该厂商支持 Responses，原地改）、
    `"claude"`（只有 Anthropic 端点，改由 Claude Code 接入）、None（对不上已知厂商）。"""
    if incompatibility(rec) is None:
        return None
    host = _host(rec)
    if host == "api.deepseek.com":
        return MIGRATE_CODEX_RESPONSES
    if host in _LEGACY_CHAT_HOST_PRESET:
        return MIGRATE_CLAUDE
    return None


def migration_preset(rec: dict | None) -> dict | None:
    """迁移的目标预设（Codex Responses 预设或 Claude Code 预设）；无法迁移 → None。"""
    kind = migration_kind(rec)
    if kind == MIGRATE_CODEX_RESPONSES:
        pid = DEEPSEEK_CODEX_ID
    elif kind == MIGRATE_CLAUDE:
        pid = _LEGACY_CHAT_HOST_PRESET.get(_host(rec))
    else:
        return None
    return next((p for p in PRESETS if p["id"] == pid), None)


def migrate(pid: str) -> dict:
    """按厂商分派：支持 Responses 的 → 原地改 Codex Responses；只有 Anthropic 端点的 → 改由 Claude Code 接入。"""
    kind = migration_kind(get(pid))
    if kind == MIGRATE_CODEX_RESPONSES:
        return _migrate_in_place(pid)
    if kind == MIGRATE_CLAUDE:
        return migrate_to_claude(pid)
    raise ValueError(f"该接口无法自动迁移: {pid}")


def _migrate_in_place(pid: str) -> dict:
    """旧 chat 记录 → 原地改成 Codex Responses：保留 id / 密钥 / 作为 codex 当前接口的激活状态；
    已退役的模型名换成新预设的型号，其余模型保留。"""

    def fix(m: str) -> str:
        return _DEEPSEEK_RETIRED_MODELS.get(m, m)

    def tx(ai: dict):
        providers = _providers_in(ai)
        old = _find(providers, pid)
        preset = migration_preset(old)
        if old is None or preset is None or migration_kind(old) != MIGRATE_CODEX_RESPONSES:
            raise ValueError(f"该接口无法自动迁移: {pid}")
        models: list[str] = []
        for m in [*(old.get("models") or []), *preset["models"]]:
            m = fix(str(m))
            if m not in models:
                models.append(m)
        default = old.get("default_model")
        providers = _save_in(
            providers,
            {
                "id": pid,
                "agent": "codex",
                "base_url": preset["base_url"],
                "wire_api": "responses",
                "models": models,
                "default_model": fix(default) if default else None,
            },
        )
        return {"providers": providers}, next(p for p in providers if p.get("id") == pid)

    return _update(tx)


def migrate_to_claude(pid: str) -> dict:
    """不兼容的旧 codex 接口 → 新建一条 claude 接口（保留原密钥、模型换成预设的）并删掉旧的。

    旧记录若是 codex 的当前接口，`delete()` 会把 codex 清回官方登录；**不替用户激活**
    新建的 claude 接口（执行器是用户自己选的）。对不上「改由 Claude Code」的厂商 → ValueError。
    """

    def tx(ai: dict):
        providers = _providers_in(ai)
        old = _find(providers, pid)
        preset = migration_preset(old)
        if old is None or preset is None or migration_kind(old) != MIGRATE_CLAUDE:
            raise ValueError(f"该接口无法自动迁移: {pid}")
        taken = {p.get("id") for p in providers}
        new_id = preset["id"]
        while new_id in taken:  # 绝不覆盖用户已有的同名接口（save 对已有 id 是合并）
            new_id = f"{new_id}-2"
        created = _clean(
            {
                "id": new_id,
                "label": preset["label"],
                "agent": preset["agent"],
                "base_url": preset["base_url"],
                "api_key": old.get("api_key", ""),
                "models": list(preset["models"]),
            },
            taken,
        )
        # 一次事务、一次落盘（tmp + replace）：中途退出 / 写盘失败 = 配置原样，
        # 不会留下「新 claude + 旧 codex」并存的半截状态。
        remaining = [p for p in providers if p.get("id") != pid]
        active = _active_without(ai, pid)
        return {"providers": [*remaining, created], "active_provider": active}, created

    return _update(tx)


def _active_without(ai: dict, pid: str) -> dict:
    active = dict(ai.get("active_provider") or {})
    for agent, cur in list(active.items()):
        if cur == pid:
            active[agent] = None
    return active


def _save_in(providers: list[dict], rec: dict) -> list[dict]:
    """把 rec 并进 providers（新增或合并更新），返回新列表；纯函数，不碰配置。"""
    providers = list(providers)
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
    return providers


def save(rec: dict) -> list[dict]:
    """新增或更新一条。api_key 缺省 = 保留原值（界面不回显密钥，也就不该要求重填）。"""
    # 令牌不接受来自请求体：登录态只由 ai_oauth 写（见 store_oauth）
    rec = {k: v for k, v in rec.items() if k not in ("auth", "oauth")}

    def tx(ai: dict):
        providers = _save_in(_providers_in(ai), rec)
        return {"providers": providers}, providers

    return _update(tx)


def delete(pid: str) -> list[dict]:
    def tx(ai: dict):
        providers = [p for p in _providers_in(ai) if p.get("id") != pid]
        return {"providers": providers, "active_provider": _active_without(ai, pid)}, providers

    return _update(tx)


def store_oauth(defaults: dict, oauth: dict) -> dict:
    """账号登录成功：把令牌写进一条接口记录（不存在就按 defaults 建）。**只给 ai_oauth 用。**

    已有同 id 记录时保留用户改过的名称 / 模型，只换登录态；api_key 清空（这条记录靠账号，不靠 Key）。
    """

    def tx(ai: dict):
        providers = _providers_in(ai)
        pid = _slug(str(defaults.get("id") or ""))
        idx = next((i for i, p in enumerate(providers) if p.get("id") == pid), -1)
        base = {**defaults, **providers[idx]} if idx >= 0 else dict(defaults)
        rec = _clean({**base, "id": pid, "api_key": "", "auth": "oauth", "oauth": oauth})
        if idx >= 0:
            providers[idx] = rec
        else:
            providers.append(rec)
        return {"providers": providers}, rec

    return _update(tx)


def update_oauth(pid: str, oauth: dict | None) -> dict | None:
    """换掉（刷新后）或清掉（oauth=None，退出登录）一条记录的令牌；记录本身保留。"""

    def tx(ai: dict):
        providers = _providers_in(ai)
        idx = next((i for i, p in enumerate(providers) if p.get("id") == pid), -1)
        if idx < 0 or providers[idx].get("auth") != "oauth":
            return {}, None
        rec = {k: v for k, v in providers[idx].items() if k != "oauth"}
        old = providers[idx].get("oauth") if isinstance(providers[idx].get("oauth"), dict) else {}
        # 退出登录：令牌清掉，厂商 / 区域留着（界面据此给「重新登录」）
        rec["oauth"] = (
            dict(oauth) if oauth else {k: old[k] for k in ("provider", "region") if k in old}
        )
        providers[idx] = _clean(rec)
        return {"providers": providers}, providers[idx]

    return _update(tx)


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

    def tx(ai: dict):
        if pid and _find(_providers_in(ai), pid) is None:
            raise ValueError(f"供应商不存在: {pid}")
        active = dict(ai.get("active_provider") or {})
        active[agent] = pid or None
        return {"active_provider": active}, active

    return _update(tx)


def resolve(agent: str, pid: str | None = None) -> dict | None:
    """本次任务实际使用的供应商：显式指定 > 该 agent 的当前选择 > 无（官方登录态）。

    `pid == ""` 是「明确要用官方登录态」，与「没指定」区分开——不然用户在
    界面上选回官方会被当成没选，继续走上一个第三方网关。
    """
    if pid == "":
        return None
    if agent == ai_agents.BUILTIN_ID:
        # 内置执行器没有「家族」：它能用任何一条 Key 连接，只认显式指定（没有「当前选中」这回事）
        return get(pid) if pid else None
    rec = get(pid) if pid else get(active_id(agent))
    if rec is None or rec.get("agent") != agent:
        return None
    return rec


def _toml(value: str) -> str:
    """codex `-c key=value` 的 value 按 TOML 解析，字符串必须带引号。
    JSON 字符串字面量与 TOML 基本字符串的转义规则在这里完全兼容。"""
    return json.dumps(value, ensure_ascii=False)


def spawn_overrides(
    agent: str, rec: dict | None, model: str | None = None, effort: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """→ (追加到命令行的参数, 追加到环境的变量)。rec 为 None 时两者都空。`effort` 只有内置执行器用。"""
    if rec is None:
        return [], {}
    if agent == ai_agents.BUILTIN_ID:
        return _builtin_overrides(rec, model, effort)
    base_url = str(rec.get("base_url") or "").strip()
    api_key = str(rec.get("api_key") or "").strip()

    family = _family(agent)

    if family == FAMILY_ANTHROPIC:
        env: dict[str, str] = {}
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if rec.get("auth") == "oauth":
            # 账号登录：访问令牌走 Bearer（`ANTHROPIC_AUTH_TOKEN` → Authorization 头）。**不设**
            # `ANTHROPIC_API_KEY`——它会让 Claude Code 发 x-api-key（Kimi 社区报告会 401）；
            # 设成空串是为了盖掉用户 shell 里继承来的真 Anthropic 密钥，别把它发给第三方网关
            access = str((rec.get("oauth") or {}).get("access") or "").strip()
            if access:
                env["ANTHROPIC_AUTH_TOKEN"] = access
            env["ANTHROPIC_API_KEY"] = ""
        elif api_key:
            # 两个名字都给：不同网关认的字段不一样，多给一个不会有副作用
            env["ANTHROPIC_AUTH_TOKEN"] = api_key
            env["ANTHROPIC_API_KEY"] = api_key
        chosen = model or rec.get("default_model")
        if chosen:
            # 第三方网关基本不认 sonnet/opus 这些别名，把三档默认模型一起
            # 钉死，`--model` 之外的内部调用（小模型任务）才不会打到不存在的模型上
            for key in (
                "ANTHROPIC_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            ):
                env[key] = chosen
        return [], env

    if family == FAMILY_OPENAI:
        if not base_url:
            return [], {}
        pid = CODEX_PROVIDER_ID
        args = [
            "-c",
            f"model_provider={_toml(pid)}",
            "-c",
            f"model_providers.{pid}.name={_toml(rec.get('label') or pid)}",
            "-c",
            f"model_providers.{pid}.base_url={_toml(base_url)}",
            "-c",
            f"model_providers.{pid}.wire_api={_toml('responses')}",
            "-c",
            f"model_providers.{pid}.env_key={_toml(CODEX_KEY_ENV)}",
        ]
        env = {CODEX_KEY_ENV: api_key} if api_key else {}
        return args, env

    return [], {}


#: 家族 → 内置执行器的线上协议名（`agent_runner.wires.WIRES` 的键）
_BUILTIN_WIRE = {FAMILY_ANTHROPIC: "anthropic", FAMILY_OPENAI: "openai-responses"}


def builtin_conn(rec: dict | None, model: str | None = None) -> dict | None:
    """这条记录内置循环能不能直接用 → `{wire, base_url, api_key, model, auth}`（带密钥 / 令牌，只给 spawn 注入 /
    `fixsession.build_model` 用）或 None。`auth` = `"key"`（x-api-key + Bearer 都给）| `"bearer"`（账号登录的访问令牌，只发 Bearer）。

    **判据只在这一处**（改图的 `spawn_overrides("builtin")`、AI 修复的 `fixsession.connection()`、连接视图的路由都问它）：
    有 Key（账号登录的记录看访问令牌）、有型号、地址合规（`check_base_url`：https 或本机回环）、不是不兼容的旧记录、家族认得。
    账号登录记录的令牌可能快过期：spawn 之前由 `ai_oauth.ensure_fresh` 刷新（这里只看有没有令牌 + 刷新令牌）。"""
    if not rec or incompatibility(rec):
        return None
    wire = _BUILTIN_WIRE.get(str(_family(str(rec.get("agent") or ""))))
    chosen = (
        str(model or "").strip()
        or str(rec.get("default_model") or "").strip()
        or next(iter(rec.get("models") or []), "")
    )
    if rec.get("auth") == "oauth":
        oauth = rec.get("oauth") if isinstance(rec.get("oauth"), dict) else {}
        key = str(oauth.get("access") or "") if oauth.get("refresh") else ""
        auth = "bearer"
    else:
        key, auth = str(rec.get("api_key") or ""), "key"
    base = str(rec.get("base_url") or "")
    if wire and chosen and key and check_base_url(base) is None:
        return {"wire": wire, "base_url": base, "api_key": key, "model": chosen, "auth": auth}
    return None


class EffortResolver:
    """内置执行器在「这个地址 + 这个型号」上的思考强度：**连接视图（展示给用户的档位）与发送前的核对共用这一个**，不分两套。

    * 只有官方 API 来源（`ai_catalog` 的 `effort_origins`）才有；第三方网关一律没有（不确定就不给，宁可不显示也不发对方不认的字段）；
    * 官方来源先查 CLI 自己的型号目录缓存（`CodexAgent._catalog()` / `ClaudeAgent._catalog()`，现有代码已在只读这两份型号元数据，
      不涉及凭据）：型号命中就**原样**用它给的档位与默认档（含 minimal / xhigh 等，CLI 目录说支持就支持；目录明确给了空 = 没有档位）；
      目录读不到 / 型号不在目录里，才退回 `ai_catalog` 里按官方文档核对过的保守表；
    * 最后按线上协议能发的取值过滤（anthropic：low/medium/high/xhigh/max；Responses：minimal/low/medium/high/xhigh）。
    构造时只读一次 CLI 目录（连接视图要对每个型号各问一次）。"""

    def __init__(self, agent: str, base_url: str):
        self.agent, self.base_url = agent, base_url
        self.official = ai_catalog.is_effort_origin(agent, base_url)
        self._wire = _BUILTIN_WIRE.get(str(_family(agent)))
        self._cli: dict[str, ai_agents.ModelDetail] = {}
        if self.official:
            cat = ai_agents.get_agent(agent)
            try:
                if isinstance(cat, ai_agents.CodexAgent):
                    details = cat._catalog()
                elif isinstance(cat, ai_agents.ClaudeAgent):
                    details = cat._catalog()[0]
                else:
                    details = []
            except Exception:  # noqa: BLE001 - 读不到 = 退回文档表
                details = []
            self._cli = {d.id: d for d in details if d.efforts_known}

    def support(self, model: str | None) -> dict:
        """→ `{"efforts": [...], "default": 默认档位 | None}`。"""
        if not self.official:
            return {"efforts": [], "default": None}
        hit = self._cli.get(str(model or "").strip())
        if hit is not None:
            efforts, default = list(hit.efforts), hit.default_effort
        else:
            sup = ai_catalog.effort_support(self.agent, self.base_url, model)
            efforts, default = sup["efforts"], sup["default"]
        allowed = ANTHROPIC_EFFORTS if self._wire == "anthropic" else RESPONSES_EFFORTS
        efforts = [e for e in efforts if e in allowed]
        return {"efforts": efforts, "default": default if default in efforts else None}


def builtin_effort_args(rec: dict, effort: str | None, model: str | None = None) -> list[str]:
    """思考强度 → 内置执行器的参数。只有 `EffortResolver` 对「这个地址 + 这个型号」确认支持的档位才发，其余一律不发
    （不支持 adaptive thinking 的型号带上 `thinking` 会 400）：
    anthropic wire → `--effort X --thinking adaptive`（`output_config.effort` + `thinking:{type:adaptive}`，这些型号都是自适应思考；不发 `disabled`）；
    openai-responses → `--effort X`（`reasoning.effort`）。"""
    if not effort:
        return []
    agent, base = str(rec.get("agent") or ""), str(rec.get("base_url") or "")
    chosen = (
        str(model or "").strip()
        or str(rec.get("default_model") or "").strip()
        or next(iter(rec.get("models") or []), "")
    )
    resolver = EffortResolver(agent, base)
    if effort not in resolver.support(chosen)["efforts"]:
        return []
    if resolver._wire == "anthropic":
        return ["--effort", effort, "--thinking", "adaptive"]
    return ["--effort", effort]


def _builtin_overrides(
    rec: dict, model: str | None, effort: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """内置执行器的注入：地址 / 型号 / 协议名进 argv，**密钥（或账号登录的令牌）只进环境变量**（不进 argv / 日志 / 事件）。不可用 → 两者皆空。"""
    conn = builtin_conn(rec, model)
    if conn is None:
        return [], {}
    args = ["--wire", conn["wire"], "--base-url", conn["base_url"], "--model", conn["model"]]
    if conn["auth"] == "bearer":
        args += ["--auth", "bearer"]
    args += builtin_effort_args(rec, effort, conn["model"])
    return args, {ai_agents.BUILTIN_KEY_ENV: conn["api_key"]}


def create(rec: dict) -> dict:
    """**新增**一条（不合并进同 id 的旧记录）：id 已被占用就顺延成 `<id>-2`。→ 规范化后的记录（带密钥，内部用）。"""
    rec = {k: v for k, v in rec.items() if k not in ("auth", "oauth")}

    def tx(ai: dict):
        providers = _providers_in(ai)
        created = _clean(rec, {p.get("id") for p in providers})
        return {"providers": [*providers, created]}, created

    return _update(tx)


def models_for(agent: str, pid: str | None = None) -> list[str]:
    rec = resolve(agent, pid)
    return list(rec.get("models") or []) if rec else []


def live_models(
    pid: str | None, base_url: str | None, api_key: str | None, *, refresh: bool = False
) -> dict:
    """对一条接口取服务商实时型号列表（`GET {base_url}/v1/models`，见 `ai_catalog.live_models`）。

    `pid` 指向已存接口时，没给的 `base_url` / `api_key` 用已存值补（界面从不回显密钥）；
    都给了就用给的（新建接口还没保存时也能先试）。拿不到 → `source: "none"` + 原因码，
    调用方退回预设的 `models` / 用户已填的列表；永不抛。"""
    saved = get(pid) or {}
    url = str(base_url or "").strip() or str(saved.get("base_url") or "")
    key = str(api_key or "").strip()
    if not key:
        # 已存密钥只发往已存地址的同源（scheme + host + 端口）：改了地址就必须重新填密钥，
        # 否则「改 URL 后点获取」会把旧密钥送到另一个主机。后端判，不靠前端。
        saved_key = str(saved.get("api_key") or "")
        if saved_key:
            want = ai_catalog.origin_of(url)
            if want is None or want != ai_catalog.origin_of(str(saved.get("base_url") or "")):
                return {"models": [], "source": "none", "reason": "key_origin_mismatch"}
            key = saved_key
    return ai_catalog.live_models(str(pid or ""), url, key, refresh=refresh)
