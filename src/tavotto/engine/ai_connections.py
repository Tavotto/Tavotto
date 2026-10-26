"""「连接」视图：在 `ai.providers[]` 与两家 CLI 的官方登录态之上，给界面一份「我连了哪些 AI」的统一说法。

界面不讲「执行器 / CLI / agent」，只讲「连接 AI」（用户裁决 2026-10-23）。所以这里把底下的三种来源摊平成两张表：

* `vendors()`：能连的**厂商**（设置页 / 连接弹窗的列表），每家一条，怎么接入放在 `methods`：订阅登录（`<厂商>:login`，对应 CLI 的官方登录）、
  账号登录（`<provider>:oauth`，client_id 配了才出现）、API Key（`<catalog_id>:key`，由 `ai_catalog.json` 的行生成）。同一家的 Claude 行与 Codex 行
  按 `brand` 合成一个 Key 方式；目录行上的 `vendor` 把几个方式并进同一厂商（OpenAI = ChatGPT 订阅 + 官方 API Key；Claude = Claude 订阅 + Anthropic 官方 API Key；
  Kimi = 开放平台 Key + Kimi Code 会员 Key；百炼 = 按量 Key + Coding Plan；MiniMax（国内）= 账号登录 + API Key）。不跨 region 合并；
* `connections()`：已连接 / 需要重新登录的连接（模型选择器的一级）。订阅类 = 每个 CLI 的官方登录态（`sub:codex` / `sub:claude`）；
  Key 类 = 每条 provider 记录（一条一个连接，多条可同时连着，不再要求「每家族只激活一个」）；账号登录类 = `auth: "oauth"` 的记录。

**不迁移存储**：沿用 `ai.providers[]` 与 `ai.active_provider`（后者只作旧字段兼容，这里的解析不看它）。

**路由**（`route_for`，接口约定「路由」一节）：订阅 → 对应 CLI；Key / 自带 OAuth 凭据的连接默认用内置执行器。
显式 `ai.builtin_default=false` 保留 CLI 偏好；内置无法接入时仍在派发前退回可用的 CLI，不在执行中切换。
运维开关 `TAVOTTO_AI_BUILTIN=0|1` 强制；默认切换由维护者在 2026-10-26 授权（ADR 0147）。

依赖方向 `ai_agents ← ai_providers ← ai_connections ← ai_bridge`；谁家 CLI 可用由调用方把 `agents[]` 的探测结论传进来
（`ai_bridge.resolve_connection` 是带探测结论的入口，这里**不** import `ai_bridge`，免得成环）。纯标准库。
"""

from __future__ import annotations

import os
import re

from . import ai_agents, ai_catalog, ai_oauth, ai_providers, config, netpolicy
from .agent_runner.wires import check_base_url

#: 订阅类连接 id → (内部 agent, 厂商 id, 显示名（产品名，不翻译）, logo 键)
SUBSCRIPTIONS: dict[str, tuple[str, str, str, str]] = {
    "sub:codex": ("codex", "openai", "ChatGPT", "openai"),
    "sub:claude": ("claude", "anthropic", "Claude", "anthropic"),
}

#: 账号登录（OAuth）的 provider → 它并入的厂商 id（同一家的 API Key 厂商）；表外的 provider 自成一个厂商
_OAUTH_VENDOR = {"minimax": "minimax"}

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


def _method_vendor(mid: str, groups: dict[str, list[dict]] | None = None) -> str:
    """Key 接入方式（目录行分组的首选行 id）→ 它所属的厂商 id：行上声明了 `vendor` 就并入那一家，否则自成一家。"""
    if mid == "custom":
        return "custom"
    rows = (groups or _groups())[mid]
    return rows[0].get("vendor") or mid


def _match_method(agent: str, base_url: str) -> str | None:
    """已存记录 → Key 接入方式 id：地址（忽略末尾斜杠与大小写）和家族都对上目录某一行才算（与 `ai_catalog.icon_for` 同判据）。"""
    url = (base_url or "").strip().rstrip("/").lower()
    if not url:
        return None
    for mid, rows in _groups().items():
        for r in rows:
            if r["agent"] == agent and r["base_url"].strip().rstrip("/").lower() == url:
                return mid
    return None


def _oauth_vendor_id(provider: str) -> str:
    return _OAUTH_VENDOR.get(provider, provider)


def _oauth_connection_id(provider: str) -> str:
    """账号登录连接的 id（= `ai_oauth` 写下的那条 provider 记录的 id）。"""
    return f"{provider}-oauth"


def vendors(agents_info: dict[str, dict]) -> list[dict]:
    """能连的**厂商**，每家一条，接入方式放在 `methods`（订阅 / 账号登录在前，API Key 在后；见模块说明）。

    旧字段保留：`connect` = 首选方式（`methods[0].kind`）、`key_url` / `key_hint_key` / `needs_base_url` 取自第一个 Key 方式、
    `available` = 任一方式可用。`agents_info` = `capabilities()["agents"]` 按 id 索引（login 方式的 `available` 看对应 CLI 装没装）。"""
    groups = _groups()
    by_vendor: dict[str, dict] = {}
    keys: dict[str, list[dict]] = {}
    lead: dict[str, list[dict]] = {}
    for mid, rows in groups.items():
        vid = _method_vendor(mid, groups)
        first = rows[0]
        if vid not in by_vendor:
            # 厂商的显示名：行上的 `vendor_label`（合并后的厂商名，如 Claude / Kimi），否则品牌名；i18n 键同理
            named = bool(first.get("vendor_label"))
            by_vendor[vid] = {
                "id": vid,
                "label": first["vendor_label"]
                if named
                else ("Custom" if vid == "custom" else first["brand"]),
                "label_key": (first.get("vendor_label_key") if named else first["label_key"])
                if vid != "custom"
                else None,
                "logo": first["icon_key"],
                "region": first["region"],
            }
        method = {
            "id": f"{mid}:key",
            "kind": "key",
            "label_key": first.get("method_key") or "method.apiKey",
            "catalog_id": mid,
            "available": True,
        }
        if first["key_url"]:
            method["key_url"] = first["key_url"]
        if first.get("key_hint_key"):
            method["key_hint_key"] = first["key_hint_key"]
        if mid == "custom":
            method["needs_base_url"] = True
        keys.setdefault(vid, []).append(method)
    for cid, (agent_id, vid, label, logo) in SUBSCRIPTIONS.items():
        by_vendor.setdefault(
            vid,
            {"id": vid, "label": label, "label_key": None, "logo": logo, "region": "global"},
        )
        lead.setdefault(vid, []).append(
            {
                "id": f"{vid}:login",
                "kind": "login",
                "label_key": "method.subscription",
                "agent": agent_id,
                "connection_id": cid,
                "available": bool((agents_info.get(agent_id) or {}).get("installed")),
            }
        )
    for provider, spec in ai_oauth.available().items():
        if not spec.get("oauth_available"):
            continue  # client_id 没配：入口不出现
        vid = _oauth_vendor_id(provider)
        by_vendor.setdefault(
            vid,
            {
                "id": vid,
                "label": spec["label"],
                "label_key": None,
                "logo": provider,
                "region": spec["default_region"]
                if spec["default_region"] in ("cn", "global")
                else "global",
            },
        )
        lead.setdefault(vid, []).append(
            {
                "id": f"{provider}:oauth",
                "kind": "oauth",
                "label_key": "method.account",
                "connection_id": _oauth_connection_id(provider),
                "available": True,
            }
        )
    out: list[dict] = []
    for vid, v in by_vendor.items():
        methods = [*lead.get(vid, []), *keys.get(vid, [])]
        first_key = next((m for m in methods if m["kind"] == "key"), None)
        v = {**v, "connect": methods[0]["kind"], "available": any(m["available"] for m in methods)}
        for k in ("key_url", "key_hint_key", "needs_base_url"):
            if first_key and k in first_key:
                v[k] = first_key[k]
        v["methods"] = methods
        out.append(v)
    cn = [v for v in out if v["region"] == "cn"]
    login_global = [
        v for v in out if v["region"] == "global" and v["methods"][0]["kind"] == "login"
    ]
    other_global = [v for v in out if v["region"] == "global" and v not in login_global]
    custom = [v for v in out if v["region"] == "custom"]
    if locale_region() == "cn":
        return [*cn, *login_global, *other_global, *custom]
    return [*login_global, *other_global, *cn, *custom]


# --------------------------------------------------------------------------- 路由
def builtin_forced() -> bool | None:
    """`TAVOTTO_AI_BUILTIN=1` → True（强制内置），`=0` → False（强制不用），其余 None。"""
    val = (os.environ.get(ENV_BUILTIN) or "").strip()
    return {"1": True, "0": False}.get(val)


def builtin_default() -> bool:
    return config.ai_settings().get("builtin_default", True) is not False


def _cli_present(agents_info: dict[str, dict], agent: str) -> bool:
    """CLI 装了且启用（不看它自己有没有官方登录）：用于自带凭据的连接——凭据由我们注入，与官方登录态无关。"""
    info = agents_info.get(agent) or {}
    return bool(info.get("installed") and info.get("enabled"))


def _cli_usable(agents_info: dict[str, dict], agent: str) -> bool:
    return bool((agents_info.get(agent) or {}).get("usable"))


def route_for(rec: dict, agents_info: dict[str, dict]) -> tuple[str | None, str | None]:
    """一条 provider 记录这次走哪条路 → `(route, 原因码)`；走不通 → `(None, 原因码)`。

    route ∈ `cli:codex` / `cli:claude` / `builtin`。"""
    agent = str(rec.get("agent") or "")
    # 自带凭据的账号登录（令牌由我们注入）不看 CLI 自己有没有官方登录；Key 类沿用 CLI「可派活」的判据
    cli = (
        _cli_present(agents_info, agent)
        if rec.get("auth") == "oauth"
        else _cli_usable(agents_info, agent)
    )
    cli_route = f"cli:{agent}"
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
def _vendor_for_record(rec: dict) -> tuple[str, str]:
    """provider 记录 → (厂商 id, 接入方式 id)。"""
    if rec.get("auth") == "oauth":
        provider = (rec.get("oauth") or {}).get("provider")
        if not provider:
            return "custom", "custom:key"
        return _oauth_vendor_id(str(provider)), f"{provider}:oauth"
    mid = _match_method(str(rec.get("agent") or ""), str(rec.get("base_url") or ""))
    if mid is None:
        return "custom", "custom:key"
    return _method_vendor(mid), f"{mid}:key"


def _row_icon(method_id: str) -> str | None:
    mid = method_id[: -len(":key")] if method_id.endswith(":key") else None
    rows = _groups().get(mid) if mid else None
    return rows[0]["icon_key"] if rows else None


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
                "method": f"{vid}:login",
            }
        )
    return out


def _key_connection(
    rec: dict,
    agents_info: dict[str, dict],
    vindex: dict[str, dict],
    transient_error: str | None = None,
) -> dict:
    vid, method = _vendor_for_record(rec)
    vendor = vindex.get(vid) or {}
    vlabel = vendor.get("label") or "Custom"
    logo = _row_icon(method) or vendor.get("logo") or ai_catalog.GENERIC_ICON_KEY
    if rec.get("auth") != "oauth" and vid in {v for _a, v, _l, _g in SUBSCRIPTIONS.values()}:
        vlabel += (
            " API"  # 同一厂商还有订阅登录（OpenAI / Claude）：官方 Key 连接的名字要和订阅那条分得开
        )
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
    details = _details(models)
    if route == "cli:codex":
        info = agents_info.get(agent_id) or {}
        efforts, default_effort = list(info.get("efforts") or []), info.get("default_effort")
    elif route == ROUTE_BUILTIN:
        # 内置执行器：只给目录确认支持的档位；第三方网关不确定就不给（宁可不显示，也不发对方不认的字段）
        base = str(rec.get("base_url") or "")
        resolver = ai_providers.EffortResolver(agent_id, base)
        support = resolver.support(default_model)
        efforts, default_effort = support["efforts"], support["default"]
        # 档位按型号定：每个型号自己带 `efforts` / `default_effort`（不支持的型号为空，前端就不显示）
        details = []
        for m in models:
            sup = resolver.support(m)
            details.append({"id": m, "efforts": sup["efforts"], "default_effort": sup["default"]})
    conn = {
        "id": rec["id"],
        "vendor": vid,
        "label": vlabel,
        "logo": logo,
        "state": state,
        "models": models,
        "model_details": details,
        "default_model": default_model,
        "efforts": efforts,
        "default_effort": default_effort,
        "route": route or f"cli:{agent_id}",
        "method": method,
    }
    if error and state == "error":
        conn["error"] = error
    if netpolicy.is_insecure_lan(str(rec.get("base_url") or "")):
        conn["insecure_lan"] = True  # 明文 http 的局域网网关：前端据此提示「这个地址不加密」
    return conn


def connections(
    agents_info: dict[str, dict], *, transient: dict[str, str] | None = None
) -> list[dict]:
    """已连接 / 需要重新登录的连接，订阅类在前。`transient` = `{连接 id: 原因码}`：刚保存时试取型号清单失败的那一次（不落盘）。"""
    out = _subscription_connections(agents_info)
    vindex = {v["id"]: v for v in vendors(agents_info)}
    keys = []
    for rec in ai_providers.list_providers():
        # 没有地址的旧记录（官方 Key 留给 CLI 自己的登录态）不是一个可独立连着的 AI：不进连接视图
        if rec.get("auth") != "oauth" and not str(rec.get("base_url") or "").strip():
            continue
        keys.append(_key_connection(rec, agents_info, vindex, (transient or {}).get(rec["id"])))
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


def first_key_connection(*, refresh: bool = False) -> dict | None:
    """AI 修复用：第一个内置循环能直接用的连接（Key 或账号登录）→ `builtin_conn` 形状；没有 = None。旧版「选中」的接口排在前（老用户结论不变）。

    `refresh=True`（真要开跑时）：账号登录的记录先 `ai_oauth.ensure_fresh`（联网，可能很慢），刷不动的跳过；
    `False`（只问「有没有」，capabilities 用）绝不联网。已知被服务商拒绝（`last_error: unauthorized`）的记录一律跳过。"""
    recs = ai_providers.list_providers()
    active = {v for v in (config.ai_settings().get("active_provider") or {}).values() if v}
    for rec in sorted(recs, key=lambda r: r.get("id") not in active):
        if rec.get("last_error") == "unauthorized":
            continue  # 保存时就被服务商拒绝的密钥：不挑它去修复（会反复认证失败）；全被拒 = None，由 CLI 兜底 / 给「密钥无效」结论
        if ai_providers.builtin_conn(rec) is None:
            continue
        if refresh and rec.get("auth") == "oauth":
            try:
                rec = ai_oauth.ensure_fresh(rec) or rec
            except ai_oauth.NeedsLogin:
                continue
        conn = ai_providers.builtin_conn(rec)
        if conn is not None:
            return conn
    return None


def builtin_usable() -> bool:
    """`require_usable("builtin")` 的判据：没被运维开关强制关掉，且有可用的 Key 连接。"""
    return builtin_forced() is not False and first_key_connection() is not None


# --------------------------------------------------------------------------- 写入口
def _key_method_id(body: dict) -> str:
    """请求里的 `method`（`<catalog_id>:key`）/ `vendor` → Key 接入方式 id（目录行分组的首选行 id）。
    `vendor` 可以是接入方式的 catalog_id（如 `kimi-code`；合并前的旧厂商 id `deepseek` / `kimi-code` / `dashscope-coding` 等就是它们）
    或厂商 id（取它的第一个 Key 方式，如 `openai` → `openai-api`）。订阅 / 账号登录方式不是 Key，旧版的 `chatgpt` / `claude` /
    `minimax-oauth` 同样一律 `unknown_vendor`。"""
    groups = _groups()
    method = body.get("method")
    if method is not None:
        if not isinstance(method, str) or not method.endswith(":key") or method[:-4] not in groups:
            raise _invalid("unknown_vendor")
        return method[:-4]
    vid = body.get("vendor")
    if not isinstance(vid, str):
        raise _invalid("unknown_vendor")
    if vid in groups:
        return vid
    for mid in groups:
        if _method_vendor(mid, groups) == vid:
            return mid
    raise _invalid("unknown_vendor")


def add_connection(body: object) -> tuple[str, str | None]:
    """`POST /api/ai/connections`：保存一条 Key 连接 → `(连接 id, 试取型号清单失败的原因码|None)`。

    保存前用 `live_models` 试取一次型号清单；失败也保存（调用方据返回的原因码给 state="error"）。
    body：`{vendor, api_key, base_url?, model?, label?, wire?, id?}`——`custom` 必须给 `base_url` 与 `wire`
    （`anthropic` | `openai-responses`）；带已存在的 `id` = 换这条的 Key / 型号（其余字段缺省沿用）。"""
    if not isinstance(body, dict):
        raise _invalid("bad_body")
    existing = None
    if body.get("id") is not None:
        existing = ai_providers.get(body["id"]) if isinstance(body["id"], str) else None
        if existing is None or existing.get("auth") == "oauth":
            raise ConnectionRefused("connection_not_found", {}, 404)
    if existing is not None and body.get("vendor") is None and body.get("method") is None:
        vid = _vendor_for_record(existing)[1][
            : -len(":key")
        ]  # 更新时省略厂商 = 还是这一条自己的方式
    else:
        vid = _key_method_id(body)
    if existing is not None:
        # 已存记录的地址对得上目录里别家的方式：不是同一条，拒绝（地址被改成目录外的 = 用户自填，不算冲突）
        have = _vendor_for_record(existing)[1]
        if have != "custom:key" and have != f"{vid}:key":
            raise _invalid("vendor_mismatch")
    groups = _groups()
    rows = groups[vid]
    key = body.get("api_key")
    reused_key = key is None and existing is not None
    if reused_key:
        key = existing.get("api_key")
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key.strip()):
        raise _invalid("api_key_invalid")
    key = key.strip()
    base_url = body.get("base_url")
    if base_url is not None and not isinstance(base_url, str):
        raise _invalid("bad_base_url")
    base_url = (base_url or "").strip().rstrip("/")
    # 带 id 的更新以已存记录为底：请求里没给的字段（地址 / 协议 / 型号 / 名字）一律保留，只有显式给了才覆盖
    kept_url = str((existing or {}).get("base_url") or "").strip().rstrip("/")
    if vid == "custom":
        wire = body.get("wire")
        if wire is None and existing is not None:
            wire = {"claude": "anthropic", "codex": "openai-responses"}.get(existing.get("agent"))
        base_url = base_url or kept_url
        if wire not in _WIRE_AGENT or not base_url:
            raise _invalid("custom_needs_base_url_and_wire")
        row = next(r for r in rows if r["agent"] == _WIRE_AGENT[wire])
    else:
        # 保留已存记录的协议族（别把 Responses 连接换回目录第一行的 Anthropic 协议）
        row = next(
            (r for r in rows if existing is not None and r["agent"] == existing.get("agent")),
            rows[0],
        )
        base_url = base_url or kept_url or row["base_url"]
    refusal = ai_catalog.url_refusal(base_url)
    if refusal:
        raise _invalid(refusal)
    if reused_key and ai_catalog.origin_of(base_url) != ai_catalog.origin_of(
        str(existing.get("base_url") or "")
    ):
        # 已存密钥只发往已存地址的同源（与 `ai_providers.live_models` 同一条规则）：换了来源就必须重新输入密钥
        raise _invalid("key_origin_mismatch")
    model = body.get("model")
    if model is not None and not (isinstance(model, str) and len(model) <= _MODEL_MAX):
        raise _invalid("bad_model")
    model = (model or "").strip() or None
    if model is None and existing is not None:
        model = (
            str(existing.get("default_model") or "").strip() or None
        )  # 换 Key 不改用户选过的型号
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
    cid = {f"{v}:login": c for c, (_a, v, _l, _g) in SUBSCRIPTIONS.items()}.get(
        cid, cid
    )  # 方式 id 也认
    if cid in SUBSCRIPTIONS:
        return "cli", SUBSCRIPTIONS[cid][0]
    for provider, spec in ai_oauth.available().items():
        if cid in (_oauth_connection_id(provider), f"{provider}:oauth") and spec.get(
            "oauth_available"
        ):
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
