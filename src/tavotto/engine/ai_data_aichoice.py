"""「从数据到图」读懂数据用哪个 AI（ADR 0178 修订 2026-11-25，用户决定）：`auto`（默认）/ `off` / 指定某一个连接。

用户原话：「这里应该需要有一个小图标，点击后可以打开列表，让用户可以选择进行分析的 ai。也可以选择关闭。」

**存储**：`ai.taf.understanding`（应用级，与实验开关 `ai.taf.enabled` 同在 `ai.taf` 里）——缺省 = `auto`；`{"mode": "off"}`；
`{"mode": "connection", "connection": "<连接 id>"}`。连接 id 沿用现有的：Key / 账号登录 = provider 记录 id，订阅登录 = `sub:codex` / `sub:claude`。
**读取是保守的**：不是对象 / 不认识的 mode / `connection` 缺 id 一律当 `off`（宁可不发数据，也不把畸形配置当成 `auto`）。

**判据只在这里**（`resolve`）：`off` → 没有连接；指定连接 → 只找那一个（Key：与 `first_key_connection` 同一套跳过规则；订阅：能力缓存里
可用且适配器支持无工具单轮），此刻用不了 = 没有，**绝不换成别家**（那等于把数据发给用户没选的服务）；`auto` → 交回调用方原有的挑法，逐字不变。
清单（`options`）只看本地配置与能力缓存：不联网、不刷新令牌、不探测，不含任何密钥 / 地址 / 令牌。

本模块不 import `ai_structured`（`tests/test_ai_data_roles.py` 钉住谁能碰模型入口）。纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

from collections.abc import Callable

from . import ai_connections, ai_providers, config

MODES = ("auto", "off", "connection")


def read_choice() -> tuple[str, str | None]:
    """`(mode, connection id | None)`。缺省 auto；畸形 = off（见模块文档）。"""
    taf = config.ai_settings().get("taf")
    raw = taf.get("understanding") if isinstance(taf, dict) else None
    if raw is None:
        return "auto", None
    if not isinstance(raw, dict):
        return "off", None
    mode = raw.get("mode")
    if mode == "auto":
        return "auto", None
    if mode == "connection":
        cid = raw.get("connection")
        if isinstance(cid, str) and cid.strip():
            return "connection", cid
        return "off", None
    return "off", None


def write_choice(mode: str, connection: str | None = None) -> None:
    """落盘（调用方先核过 `mode` / 连接 id）。`auto` = 清除这个键。"""
    if mode == "auto":
        config.set_taf_understanding(None)
    elif mode == "off":
        config.set_taf_understanding({"mode": "off"})
    elif mode == "connection" and isinstance(connection, str) and connection:
        config.set_taf_understanding({"mode": "connection", "connection": connection})
    else:
        raise ValueError("bad choice")


def conn_id(conn: dict | None) -> str | None:
    """一个连接（`builtin_conn` 形状 / CLI 描述）的连接 id：Key = provider 记录 id，订阅 = `sub:*`。"""
    if not conn:
        return None
    if conn.get("kind") == "cli":
        return str(conn.get("conn_id") or "") or None
    return str(conn.get("provider_id") or "") or None


def _cached_caps() -> dict | None:
    from . import ai_bridge

    try:
        return ai_bridge.capabilities_if_cached()
    except Exception:  # noqa: BLE001 - 读缓存失败 = 没有订阅连接
        return None


def _usable() -> bool:
    return ai_connections.builtin_forced() is not False


def _key_conns() -> list[dict]:
    if not _usable():
        return []
    try:
        return list(ai_connections.key_connections(refresh=False))
    except Exception:  # noqa: BLE001 - 探测失败 = 没有
        return []


def _cli_conns() -> list[tuple[dict, dict]]:
    if not _usable():
        return []
    try:
        return ai_connections.structured_cli_connections(_cached_caps())[0]
    except Exception:  # noqa: BLE001
        return []


def connection_by_id(cid: str) -> dict | None:
    """指定的那一个连接此刻的样子；用不了 = None。**不看别的连接**。"""
    if cid in ai_connections.SUBSCRIPTIONS:
        for cap, conn in _cli_conns():
            if cap.get("id") == cid:
                return conn
        return None
    for conn in _key_conns():
        if conn_id(conn) == cid:
            return conn
    return None


def options() -> list[dict]:
    """此刻真能用于「读懂数据」的连接：`[{id, label, logo, vendor, kind: "key" | "subscription", model}]`（订阅在前，与连接视图同序）。
    label / logo / vendor 取自连接视图（`ai_connections.connections`）已有的字段；只读本地。"""
    out: list[dict] = []
    for cap, conn in _cli_conns():
        out.append(
            {
                "id": cap["id"],
                "label": cap.get("label") or cap["id"],
                "logo": cap.get("logo"),
                "vendor": cap.get("vendor"),
                "kind": "subscription",
                "model": conn["model"],
            }
        )
    keys = _key_conns()
    if keys:
        try:
            view = {c["id"]: c for c in ai_connections.connections({})}
        except Exception:  # noqa: BLE001 - 视图算不出来 = 只给 id（界面仍可选）
            view = {}
        for conn in keys:
            cid = conn_id(conn) or ""
            v = view.get(cid) or {}
            out.append(
                {
                    "id": cid,
                    "label": v.get("label") or cid,
                    "logo": v.get("logo"),
                    "vendor": v.get("vendor"),
                    # ChatGPT 登录（SIWC）走 Key 那条路，但对用户它是订阅
                    "kind": "subscription" if ai_providers.is_siwc_conn(conn) else "key",
                    "model": str(conn.get("model") or ""),
                }
            )
    return out


def resolve(auto: Callable[[], dict | None]) -> dict | None:
    """此刻「读懂数据」会用的连接。`auto` 是调用方原有的挑法（只在 auto 模式调用）。"""
    mode, cid = read_choice()
    if mode == "off":
        return None
    if mode == "connection":
        return connection_by_id(cid or "")
    return auto()


def pinned() -> tuple[str, dict | None]:
    """给「调用方要自己决定连接」的入口（`ai_data_model.propose_recipes`）：`("auto", None)`（照旧让 `call_structured` 自己挑）/ `("off", None)` /
    `("connection", 连接或 None)`。"""
    mode, cid = read_choice()
    if mode == "connection":
        return mode, connection_by_id(cid or "")
    return mode, None


def refresh_key(conn: dict) -> dict | None:
    """指定的 Key 连接真要用之前，只刷新**这一条**（账号登录令牌 / 地址少写的 `/v1`），与 `ai_structured.resolve_connection` 同一套；刷不动（要重新登录）= None。
    联网，只在后台任务线程里调。订阅连接原样返回。"""
    from . import ai_oauth

    if conn.get("kind") == "cli":
        return conn
    if ai_providers.is_siwc_conn(conn):
        # ChatGPT 登录不是存下的记录：令牌由 `ai_structured.call_structured` 按剩余时限现取（ADR 0209 §七），这里原样交回
        return conn
    rec = ai_providers.get(conn.get("provider_id"))
    if rec is None:
        return None
    try:
        if rec.get("auth") == "oauth":
            rec = ai_oauth.ensure_fresh(rec) or rec
        else:
            rec = ai_providers.ensure_api_root(rec) or rec
    except ai_oauth.NeedsLogin:
        return None
    return ai_providers.builtin_conn(rec, conn.get("model"))
