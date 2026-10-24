"""「连接」视图：在 `ai.providers[]` 与两家 CLI 的官方登录态之上，给界面一份「我连了哪些 AI」的统一说法。

界面不讲「执行器 / CLI / agent」，只讲「连接 AI」（用户裁决 2026-10-23）。所以这里把底下的三种来源摊平成两张表：

* `vendors()`：能连的厂商目录（设置页 / 连接弹窗的列表）。由 `ai_catalog.json` 生成——同一家的 Claude 行（Anthropic Messages）
  与 Codex 行（Responses）按 `brand` 合成**一家**；再加订阅类 `chatgpt` / `claude`（官方登录）与账号登录类（MiniMax，client_id 配了才出现）；
* `connections()`：已连接 / 需要重新登录的连接（模型选择器的一级）。订阅类 = 每个 CLI 的官方登录态（`sub:codex` / `sub:claude`）；
  Key 类 = 每条 provider 记录（一条一个连接，多条可同时连着，不再要求「每家族只激活一个」）；账号登录类 = `auth: "oauth"` 的记录。

**不迁移存储**：沿用 `ai.providers[]` 与 `ai.active_provider`（后者只作旧字段兼容，这里的解析不看它）。

**路由**（`route_for`，接口约定「路由」一节）：订阅 → 对应 CLI；Key 类 → 该家族 CLI 装着且可用、并且 `ai.builtin_default` 不为 true
（默认 false，FigureBench 过线后改 true）→ CLI + 注入 provider（现状）；否则 → 内置执行器（`agent_runner`，ADR 0147 第二阶段）。
没装任何 CLI 的用户因此直接可用。运维开关 `TAVOTTO_AI_BUILTIN=0|1` 强制。账号登录类（Bearer 令牌）内置循环不走，只能 CLI。

依赖方向 `ai_agents ← ai_providers ← ai_connections ← ai_bridge`；谁家 CLI 可用由调用方把 `agents[]` 的探测结论传进来
（`ai_bridge.resolve_connection` 是带探测结论的入口，这里**不** import `ai_bridge`，免得成环）。纯标准库。
"""

from __future__ import annotations

import os
import re

from . import ai_agents, ai_catalog, ai_oauth, ai_providers, config
from .agent_runner.wires import check_base_url

#: 订阅类连接 id → (内部 agent, 厂商 id, 显示名（产品名，不翻译）, logo 键)
SUBSCRIPTIONS: dict[str, tuple[str, str, str, str]] = {
    "sub:codex": ("codex", "chatgpt", "ChatGPT", "openai"),
    "sub:claude": ("claude", "claude", "Claude", "anthropic"),
}

ROUTE_BUILTIN = "builtin"
ENV_BUILTIN = "TAVOTTO_AI_BUILTIN"

#: 保存连接时对 Key 的形状要求：可打印 ASCII、无空白（Key 会进 HTTP 头与环境变量，换行 / 空格都是注入面）
_KEY_RE = re.compile(r"[\x21-\x7e]{1,4096}")
_MODEL_MAX = 200

#: 内置循环支持的线上协议 → 内部家族（自定义厂商用）
_WIRE_AGENT = {"anthropic": "claude", "openai-responses": "codex"}


class ConnectionRefused(Exception):
    """连接层的明确拒绝：稳定 `code` + `params` + HTTP 状态（app 层转成 JSON）。"""

    def __init__(self, code: str, params: dict | None = None, status: int = 400):
        super().__init__(code)
        self.code = code
        self.params = params or {}
        self.status = status


def _invalid(reason: str) -> ConnectionRefused:
    return ConnectionRefused("endpoint_invalid", {"reason": reason}, 400)


# --------------------------------------------------------------------------- 厂商目录
def _groups() -> dict[str, list[dict]]:
    """目录行按 `brand` 合并成「一家」：{厂商 id: [行…]}，Claude（Anthropic Messages）行在前。
    官方登录行（`base_url` 为空）不进来——那是订阅类；`custom` 区域的两行合成一家 `custom`。"""
    by_brand: dict[str, list[dict]] = {}
    for row in ai_catalog.load_vendors():
        if row["region"] == "custom":
            key = "\0custom"
        elif not row["base_url"]:
            continue
        else:
            key = row["brand"]
        by_brand.setdefault(key, []).append(row)
    out: dict[str, list[dict]] = {}
    for key, rows in by_brand.items():
        rows = sorted(
            rows, key=lambda r: r["agent"] != "claude"
        )  # 稳定排序：首选 wire = Anthropic Messages
        out["custom" if key == "\0custom" else rows[0]["id"]] = rows
    return out


def _vendor_of_row_match(agent: str, base_url: str) -> str | None:
    """已存记录 → 厂商 id：地址（忽略末尾斜杠与大小写）和家族都对上目录某一行才算（与 `ai_catalog.icon_for` 同判据）。"""
    url = (base_url or "").strip().rstrip("/").lower()
    if not url:
        return None
    for vid, rows in _groups().items():
        for r in rows:
            if r["agent"] == agent and r["base_url"].strip().rstrip("/").lower() == url:
                return vid
    return None


def locale_region() -> str:
    """系统语言给的排序提示：中文系统 → `cn`（国内厂商在前），其余 `global`。前端取自己的界面语言为准。"""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = (os.environ.get(var) or "").lower()
        if val:
            return "cn" if val.startswith("zh") else "global"
    try:
        if os.name == "nt":
            import ctypes
            import locale

            lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined]
        else:
            import locale

            lang = locale.getlocale()[0]
    except Exception:  # noqa: BLE001 - 取不到就当 global，前端自己有语言
        lang = None
    return "cn" if (lang or "").lower().startswith("zh") else "global"


def vendors(agents_info: dict[str, dict]) -> list[dict]:
    """能连的厂商（见模块说明）。`agents_info` = `capabilities()["agents"]` 按 id 索引（订阅类的 `available` 看对应 CLI 装没装）。"""
    keyed: list[dict] = []
    for vid, rows in _groups().items():
        first = rows[0]
        v = {
            "id": vid,
            "label": "Custom" if vid == "custom" else first["brand"],
            "label_key": None if vid == "custom" else first["label_key"],
            "logo": first["icon_key"],
            "region": first["region"],
            "connect": "key",
            "available": True,
        }
        if first["key_url"]:
            v["key_url"] = first["key_url"]
        if first.get("key_hint_key"):
            v["key_hint_key"] = first["key_hint_key"]
        if vid == "custom":
            v["needs_base_url"] = True
        keyed.append(v)
    subs = []
    for agent_id, vid, label, logo in SUBSCRIPTIONS.values():
        info = agents_info.get(agent_id) or {}
        subs.append(
            {
                "id": vid,
                "label": label,
                "label_key": None,
                "logo": logo,
                "region": "global",
                "connect": "login",
                "available": bool(info.get("installed")),
            }
        )
    oauth = [
        {
            "id": _oauth_vendor_id(provider),
            "label": spec["label"],
            "label_key": None,
            "logo": provider,
            "region": spec["default_region"]
            if spec["default_region"] in ("cn", "global")
            else "global",
            "connect": "oauth",
            "available": True,
        }
        for provider, spec in ai_oauth.available().items()
        if spec.get("oauth_available")
    ]
    cn = [v for v in keyed if v["region"] == "cn"] + [v for v in oauth if v["region"] == "cn"]
    glob = [v for v in keyed if v["region"] == "global"] + [
        v for v in oauth if v["region"] == "global"
    ]
    custom = [v for v in keyed if v["region"] == "custom"]
    if locale_region() == "cn":
        return [*cn, *subs, *glob, *custom]
    return [*subs, *glob, *cn, *custom]


def _oauth_vendor_id(provider: str) -> str:
    return f"{provider}-oauth"


def _vendor(vid: str) -> dict | None:
    return next((v for v in vendors({}) if v["id"] == vid), None)


# --------------------------------------------------------------------------- 路由
def builtin_forced() -> bool | None:
    """`TAVOTTO_AI_BUILTIN=1` → True（强制内置），`=0` → False（强制不用），其余 None。"""
    val = (os.environ.get(ENV_BUILTIN) or "").strip()
    return {"1": True, "0": False}.get(val)


def builtin_default() -> bool:
    return config.ai_settings().get("builtin_default") is True


def _cli_usable(agents_info: dict[str, dict], agent: str) -> bool:
    return bool((agents_info.get(agent) or {}).get("usable"))


def route_for(rec: dict, agents_info: dict[str, dict]) -> tuple[str | None, str | None]:
    """一条 provider 记录这次走哪条路 → `(route, 原因码)`；走不通 → `(None, 原因码)`。

    route ∈ `cli:codex` / `cli:claude` / `builtin`。"""
    agent = str(rec.get("agent") or "")
    cli = _cli_usable(agents_info, agent)
    cli_route = f"cli:{agent}"
    if rec.get("auth") == "oauth":
        # 令牌是 Bearer：内置循环不走，只能 CLI
        return (cli_route, None) if cli else (None, "cli_unavailable")
    viable = ai_providers.builtin_conn(rec) is not None
    forced = builtin_forced()
    if forced is True:
        return (ROUTE_BUILTIN, None) if viable else (None, "builtin_unavailable")
    if forced is False:
        return (cli_route, None) if cli else (None, "cli_unavailable")
    if cli and not builtin_default():
        return cli_route, None
    if viable:
        return ROUTE_BUILTIN, None
    if cli:  # 内置走不通（例如明文 http 网关）但 CLI 可用：退回 CLI
        return cli_route, None
    return None, _why_not_builtin(rec)


def _why_not_builtin(rec: dict) -> str:
    """内置循环用不了这条记录、又没有 CLI 兜底时，给界面一个稳定原因码。"""
    if not (str(rec.get("default_model") or "").strip() or rec.get("models")):
        return "no_model"
    bad = check_base_url(str(rec.get("base_url") or ""))
    return bad or "cli_unavailable"


# --------------------------------------------------------------------------- 连接视图
def _vendor_for_record(rec: dict) -> str:
    if rec.get("auth") == "oauth":
        provider = (rec.get("oauth") or {}).get("provider")
        return _oauth_vendor_id(str(provider)) if provider else "custom"
    return (
        _vendor_of_row_match(str(rec.get("agent") or ""), str(rec.get("base_url") or ""))
        or "custom"
    )


def _vendor_meta(vid: str) -> tuple[str, str]:
    """→ (显示名, logo 键)。"""
    if vid.endswith("-oauth"):
        spec = ai_oauth.available().get(vid[: -len("-oauth")])
        return (spec["label"] if spec else vid), vid[: -len("-oauth")]
    rows = _groups().get(vid)
    if rows is None:
        return "Custom", ai_catalog.GENERIC_ICON_KEY
    return ("Custom" if vid == "custom" else rows[0]["brand"]), rows[0]["icon_key"]


def _details(models: list[str]) -> list[dict]:
    return [{"id": m} for m in models]


def _subscription_connections(agents_info: dict[str, dict]) -> list[dict]:
    out = []
    for cid, (agent_id, vid, label, logo) in SUBSCRIPTIONS.items():
        info = agents_info.get(agent_id)
        if not info or not info.get("installed") or not info.get("enabled"):
            continue
        agent = ai_agents.get_agent(agent_id)
        caps = agent.model_capabilities() if agent else ai_agents.ModelCapabilities()
        state = (
            "needs_auth"
            if (info.get("diagnostics") or {}).get("readiness") == "needs_auth"
            else "ready"
        )
        models = list(caps.models)
        details = [
            {"id": d.id, **({"display_name": d.display_name} if d.display_name else {})}
            for d in caps.model_details
        ] or _details(models)
        out.append(
            {
                "id": cid,
                "vendor": vid,
                "label": label,
                "logo": logo,
                "state": state,
                "models": models,
                "model_details": details,
                "default_model": caps.default_model,
                "efforts": list(caps.efforts) if agent and agent.supports_effort_selection else [],
                "default_effort": caps.default_effort,
                "route": f"cli:{agent_id}",
            }
        )
    return out


def _key_connection(
    rec: dict, agents_info: dict[str, dict], transient_error: str | None = None
) -> dict:
    vid = _vendor_for_record(rec)
    vlabel, logo = _vendor_meta(vid)
    route, why = route_for(rec, agents_info)
    models = [str(m) for m in (rec.get("models") or [])]
    default_model = str(rec.get("default_model") or "").strip() or (models[0] if models else None)
    error = None
    if ai_providers.incompatibility(rec):
        error = ai_providers.incompatibility(rec)
    elif rec.get("auth") == "oauth":
        signed_in = bool((rec.get("oauth") or {}).get("access"))
        if not signed_in:
            error = "needs_auth"
        elif route is None:
            error = why
    elif not str(rec.get("api_key") or "").strip():
        error = "needs_auth"
    elif transient_error or rec.get("last_error"):
        error = transient_error or rec.get("last_error")
    elif route is None:
        error = why
    elif route == ROUTE_BUILTIN and not default_model:
        error = "no_model"
    state = "ready" if error is None else ("needs_auth" if error == "needs_auth" else "error")
    agent_id = str(rec.get("agent") or "")
    efforts = []
    default_effort = None
    if route == "cli:codex":
        info = agents_info.get(agent_id) or {}
        efforts, default_effort = list(info.get("efforts") or []), info.get("default_effort")
    conn = {
        "id": rec["id"],
        "vendor": vid,
        "label": vlabel,
        "logo": logo,
        "state": state,
        "models": models,
        "model_details": _details(models),
        "default_model": default_model,
        "efforts": efforts,
        "default_effort": default_effort,
        "route": route or f"cli:{agent_id}",
    }
    if error and state == "error":
        conn["error"] = error
    return conn


def connections(
    agents_info: dict[str, dict], *, transient: dict[str, str] | None = None
) -> list[dict]:
    """已连接 / 需要重新登录的连接，订阅类在前。`transient` = `{连接 id: 原因码}`：刚保存时试取型号清单失败的那一次（不落盘）。"""
    out = _subscription_connections(agents_info)
    keys = []
    for rec in ai_providers.list_providers():
        # 没有地址的旧记录（官方 Key 留给 CLI 自己的登录态）不是一个可独立连着的 AI：不进连接视图
        if rec.get("auth") != "oauth" and not str(rec.get("base_url") or "").strip():
            continue
        keys.append(_key_connection(rec, agents_info, (transient or {}).get(rec["id"])))
    # 同厂商多条：显示名带上区分（用户起的名字 / 密钥尾四位）
    by_vendor: dict[str, int] = {}
    for c in keys:
        by_vendor[c["vendor"]] = by_vendor.get(c["vendor"], 0) + 1
    recs = {r["id"]: r for r in ai_providers.list_providers()}
    for c in keys:
        if by_vendor[c["vendor"]] > 1:
            rec = recs[c["id"]]
            name = str(rec.get("label") or "").strip()
            tail = ai_providers.public(rec).get("key_hint") or ""
            extra = name if name and name != c["label"] else tail
            if extra:
                c["label"] = f"{c['label']} · {extra}"
    return out + keys


# --------------------------------------------------------------------------- 解析（改图 / 修复请求用）
def resolve_connection(cid: str, agents_info: dict[str, dict]) -> tuple[str, dict | None]:
    """连接 id → `(agent_id, endpoint 记录 | None)`。订阅类 → `(codex|claude, None)`（None = 官方登录态）；
    Key / 账号登录类 → 路由决定的 `(codex|claude|builtin, 那一条记录)`。

    **直接解析到那条 provider，不看 `ai.active_provider`**（那只作旧字段兼容）。找不到 → `ConnectionRefused("connection_not_found")`；
    走不通 → `ai_agent_not_usable`。"""
    if cid in SUBSCRIPTIONS:
        return SUBSCRIPTIONS[cid][0], None
    rec = ai_providers.get(cid) if isinstance(cid, str) else None
    if rec is None:
        raise ConnectionRefused("connection_not_found", {}, 404)
    route, _why = route_for(rec, agents_info)
    if route is None:
        raise ConnectionRefused("ai_agent_not_usable", {"agent": str(rec.get("agent") or "")}, 409)
    return (ai_agents.BUILTIN_ID if route == ROUTE_BUILTIN else route.split(":", 1)[1]), rec


def first_key_connection() -> dict | None:
    """AI 修复用：第一个内置循环能直接用的 Key 连接 → `builtin_conn` 形状；没有 = None。旧版「选中」的接口排在前（老用户结论不变）。"""
    recs = ai_providers.list_providers()
    active = {v for v in (config.ai_settings().get("active_provider") or {}).values() if v}
    for rec in sorted(recs, key=lambda r: r.get("id") not in active):
        conn = ai_providers.builtin_conn(rec)
        if conn is not None:
            return conn
    return None


def builtin_usable() -> bool:
    """`require_usable("builtin")` 的判据：没被运维开关强制关掉，且有可用的 Key 连接。"""
    return builtin_forced() is not False and first_key_connection() is not None


# --------------------------------------------------------------------------- 写入口
def add_connection(body: object) -> tuple[str, str | None]:
    """`POST /api/ai/connections`：保存一条 Key 连接 → `(连接 id, 试取型号清单失败的原因码|None)`。

    保存前用 `live_models` 试取一次型号清单；失败也保存（调用方据返回的原因码给 state="error"）。
    body：`{vendor, api_key, base_url?, model?, label?, wire?, id?}`——`custom` 必须给 `base_url` 与 `wire`
    （`anthropic` | `openai-responses`）；带已存在的 `id` = 换这条的 Key / 型号（其余字段缺省沿用）。"""
    if not isinstance(body, dict):
        raise _invalid("bad_body")
    vid = body.get("vendor")
    groups = _groups()
    if not isinstance(vid, str) or vid not in groups:
        raise _invalid("unknown_vendor")
    rows = groups[vid]
    existing = None
    if body.get("id") is not None:
        existing = ai_providers.get(body["id"]) if isinstance(body["id"], str) else None
        if existing is None or existing.get("auth") == "oauth":
            raise ConnectionRefused("connection_not_found", {}, 404)
    key = body.get("api_key")
    if key is None and existing is not None:
        key = existing.get("api_key")
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key.strip()):
        raise _invalid("api_key_invalid")
    key = key.strip()
    base_url = body.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise _invalid("bad_base_url")
    base_url = (base_url or "").strip().rstrip("/")
    if vid == "custom":
        wire = body.get("wire")
        if wire not in _WIRE_AGENT or not base_url:
            raise _invalid("custom_needs_base_url_and_wire")
        row = next(r for r in rows if r["agent"] == _WIRE_AGENT[wire])
    else:
        row = rows[0]
        base_url = base_url or row["base_url"]
    refusal = ai_catalog.url_refusal(base_url)
    if refusal:
        raise _invalid(refusal)
    model = body.get("model")
    if model is not None and not (isinstance(model, str) and len(model) <= _MODEL_MAX):
        raise _invalid("bad_model")
    model = (model or "").strip() or None
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        raise _invalid("bad_label")
    label = (
        (label or "").strip()[:60]
        or (existing or {}).get("label")
        or ("Custom" if vid == "custom" else row["brand"])
    )

    hints = [] if vid == "custom" else list(row["models_hint"])
    live = ai_providers.live_models(None, base_url, key)
    live_list = list(live.get("models") or []) if live.get("source") != "none" else []
    reason = live.get("reason") if live.get("source") == "none" else None
    models = live_list or hints or ([model] if model else [])
    if live_list:
        default = model or next((h for h in hints if h in live_list), None) or live_list[0]
    else:
        default = model or (hints[0] if hints else None)
    if default and default not in models:
        models = [default, *models]
    rec = {
        "label": label,
        "agent": row["agent"],
        "base_url": base_url,
        "api_key": key,
        "models": models,
        "default_model": default,
        "wire_api": "responses",
        # 只有「被服务商拒绝」才落盘（断网这类暂时性失败只在这次返回里说，下次读取照常算可用）
        "last_error": "unauthorized" if reason == "unauthorized" else "",
    }
    if existing is not None:
        rec["id"] = existing["id"]
        ai_providers.save(rec)
        return existing["id"], reason
    created = ai_providers.create({**rec, "id": vid})
    return created["id"], reason


def delete_connection(cid: str) -> None:
    """`DELETE /api/ai/connections/<id>`：删一条 Key / 账号登录连接。订阅类不可删（只能在 CLI 里退出）。"""
    if cid in SUBSCRIPTIONS:
        raise ConnectionRefused("connection_not_deletable", {}, 409)
    if ai_providers.get(cid) is None:
        raise ConnectionRefused("connection_not_found", {}, 404)
    ai_providers.delete(cid)


def login_target(cid: str) -> tuple[str, str]:
    """`POST /api/ai/connections/<id>/login` 该走哪条流程 → `("cli", agent_id)` 或 `("oauth", provider)`。"""
    if cid in SUBSCRIPTIONS:
        return "cli", SUBSCRIPTIONS[cid][0]
    for provider, spec in ai_oauth.available().items():
        if cid == _oauth_vendor_id(provider) and spec.get("oauth_available"):
            return "oauth", provider
    raise ConnectionRefused("connection_not_found", {}, 404)


__all__ = [
    "ROUTE_BUILTIN",
    "SUBSCRIPTIONS",
    "ConnectionRefused",
    "add_connection",
    "builtin_usable",
    "connections",
    "delete_connection",
    "first_key_connection",
    "locale_region",
    "login_target",
    "resolve_connection",
    "route_for",
    "vendors",
]
