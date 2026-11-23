"""「Tavotto Agent」网关客户端的**联网实现**（ADR 0200 §四 / §十一）：端点解析、本机授权判断（`license.has("hosted-ai")`）、
开 / 关任务的设备签名 POST。唯一一个会碰 `license` / `diagsend` 的内置 AI 模块。

**为什么和 `hosted_ai` 分开**：`license` → `diagsend` → …→ `diagnostics` → `ai_bridge` → `ai_providers`，而 `ai_*` 一族都要用内置 AI 的
常量与小判断。让它们直接 import 这里会把 `ai_*` 与诊断 / 遥测 / 更新器那个旧的 import 环连成一个大环（`tests/test_import_architecture.py`）。
所以：`hosted_ai` 是**叶子**（纯常量、数据类、失败码、`Lease`，只 import 标准库与 `brand`），`ai_*` 只认它；本模块在被 import 时
把自己登记进 `hosted_ai`（`hosted_ai.register`），由入口层（`app.py`）负责 import。没人 import 本模块 = 内置 AI 整个不可用（`hosted_ai.available()` 恒假）。

其余规矩（目的地钉死、零网络、令牌不落地、`license.has` 是唯一的权益判断）全文见 `hosted_ai` 模块头。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request

from . import diagsend, hosted_ai, license

LOG = logging.getLogger("tavotto.hosted_ai")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def _endpoint_override() -> str | None:
    raw = os.environ.get(hosted_ai.ENV_ENDPOINT)
    return raw if raw is not None and raw.strip() else None


def resolve_target() -> diagsend.Target | None:
    """此刻允许连的网关；None = 功能关闭（入口不出现、不联网）。设了 `TAVOTTO_HOSTED_AI_ENDPOINT` 但不合规也是 None，不回退。"""
    if _flag(hosted_ai.ENV_HARD_OFF):
        return None
    raw = _endpoint_override()
    if raw is not None:
        return license.parse_service_endpoint(raw, hosted_ai.HOSTED_HOSTS)
    if not hosted_ai.PRODUCTION_LIVE:
        return None
    return diagsend.parse_endpoint(
        hosted_ai.PRODUCTION_ENDPOINT, dev_loopback=False, hosts=hosted_ai.HOSTED_HOSTS
    )


def available() -> bool:
    """「Tavotto Agent」连接此刻出不出现：功能没被硬关、有可连的网关（正式开关 / 开发端点）、本机授权令牌有效且带 `hosted-ai`。
    本机判断、**不联网**（真正的权益判断在网关每次开任务时）。永不抛。"""
    try:
        return resolve_target() is not None and license.has(hosted_ai.FEATURE)
    except Exception:  # noqa: BLE001 — 探测失败 = 没有这条连接
        return False


def virtual_record() -> dict | None:
    """尚未开任务的虚拟连接记录（`ai_providers.resolve` / 连接视图用）；不可用 = None。**没有密钥**：真正的通行证在开任务之后才有（`Lease.record()`）。"""
    target = resolve_target() if available() else None
    if target is None:
        return None
    return {
        "id": hosted_ai.PROVIDER_ID,
        "label": hosted_ai.label(),
        "agent": "claude",
        "base_url": target.base,
        "api_key": "",
        # 型号清单与默认型号都留空：界面不出现内部型号名 `tavotto-auto`（连接只显示产品名）；真正发给网关的型号由 `conn_of` 固定
        "models": [],
        "default_model": "",
        "protocol": "anthropic",
        "hosted": True,
    }


def _now_ms() -> int:
    return int(license._now() * 1000)


def _log(kind: str, result: str) -> None:
    kind = kind if kind in hosted_ai.KINDS else "other"
    result = result if result in hosted_ai.LOG_RESULTS else "client_error"
    LOG.info("内置 AI: 开任务 %s %s", kind, result)


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _error_fields(body: bytes) -> tuple[str, int | None]:
    """错误体 `{"error": {"code", "message", "retry_at"?}}` → `(code, retry_at)`；别的形状不认。"""
    try:
        obj = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", None
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(err, dict):
        return "", None
    code = err.get("code")
    retry = err.get("retry_at")
    return (code[:64] if isinstance(code, str) else ""), (retry if _is_int(retry) else None)


def _sane_retry_at(ms: int | None, retry_after_s: int | None) -> int | None:
    now = _now_ms()
    if ms is None and retry_after_s is not None:
        ms = now + retry_after_s * 1000
    return ms if ms is not None and now < ms < now + hosted_ai._MAX_RETRY_AHEAD_MS else None


def _classify(status: int, code: str, retry_at: int | None) -> hosted_ai.HostedError:
    if code in hosted_ai.FAILURES and code not in ("network", "disabled"):
        return hosted_ai.HostedError(code, retry_at)
    if status == 429:
        return hosted_ai.HostedError("rate_limited", retry_at)
    if status == 503:
        return hosted_ai.HostedError("paused", retry_at)
    if status >= 500 or status == 404:
        return hosted_ai.HostedError("server")
    return hosted_ai.HostedError("client_error")


def _request(target: diagsend.Target, path: str, data: bytes, headers: dict, timeout: float):
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死：白名单主机的 https（开发模式才是回环）
        target.base + path,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": hosted_ai._UA,
            **headers,
        },
    )
    return diagsend._send(req, timeout, hosted_ai.API_MAX_BYTES)


def _client_version() -> str:
    return diagsend._version()


def _parse_grant(resp, base_url: str) -> hosted_ai.TaskGrant:
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise hosted_ai.HostedError("server") from None
    if not isinstance(obj, dict):
        raise hosted_ai.HostedError("server")
    task_id, token = obj.get("task_id"), obj.get("task_token")
    lim = obj.get("limits")
    ceiling = obj.get("ceiling_micro")
    if (
        not (isinstance(task_id, str) and hosted_ai._ID_RE.fullmatch(task_id))
        or not (isinstance(token, str) and hosted_ai._TOKEN_RE.fullmatch(token))
        or not isinstance(lim, dict)
        or not _is_int(ceiling)
        or ceiling < 0
    ):
        raise hosted_ai.HostedError("server")  # 契约漂移：不猜，当网关出了问题
    limits = {}
    for key, cap in hosted_ai._LIMIT_CAPS.items():
        v = lim.get(key)
        if not _is_int(v) or v < 1:
            raise hosted_ai.HostedError("server")
        limits[key] = min(v, cap)
    remaining = obj.get("remaining")
    rem = (
        float(remaining)
        if isinstance(remaining, int | float)
        and not isinstance(remaining, bool)
        and 0 <= remaining <= 1
        else None
    )
    idle = obj.get("expires_idle_ms")
    return hosted_ai.TaskGrant(
        task_id=task_id,
        task_token=token,
        ceiling_micro=ceiling,
        limits=hosted_ai.TaskLimits(**limits),
        remaining=rem,
        base_url=base_url,
        expires_idle_ms=idle if _is_int(idle) and idle > 0 else 1_800_000,
    )


def open_task(kind: str) -> hosted_ai.TaskGrant:
    """开一个任务（设备签名的一次 POST `/v1/task`）。成功回 `hosted_ai.TaskGrant`；其余一律抛 `hosted_ai.HostedError`（闭集码，永远不是别的异常）。
    功能没开 / 本机没有有效授权 = 零网络。**调用方负责在任务结束时 `close_task`（或用 `lease()` 拿带关闭的包装）。**"""
    try:
        return _open_task(kind)
    except hosted_ai.HostedError as err:
        _log(kind, err.code)
        raise
    except Exception:  # noqa: BLE001 — 契约：只抛 hosted_ai.HostedError；原因里不放异常消息（可能含地址）
        _log(kind, "client_error")
        raise hosted_ai.HostedError("client_error") from None


def _open_task(kind: str) -> hosted_ai.TaskGrant:
    if kind not in hosted_ai.KINDS:
        raise hosted_ai.HostedError("client_error")
    target = resolve_target()
    if target is None:
        raise hosted_ai.HostedError("disabled")
    env = license.token_envelope()
    if env is None:
        raise hosted_ai.HostedError("not_entitled")  # 本机没有有效授权：网关也只会这么答，不必联网
    try:
        body = license.sign_op("ai_task", {"token": env, "kind": kind, "client": _client_version()})
    except diagsend._Failure:
        raise hosted_ai.HostedError("client_error") from None
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        resp = _request(target, "/v1/task", data, {}, hosted_ai.API_TIMEOUT_S)
    except diagsend._Failure:
        raise hosted_ai.HostedError("network") from None
    if not 200 <= resp.status < 300:
        code, retry_at = _error_fields(resp.body)
        retry_after = diagsend._retry_after(resp)
        raise _classify(resp.status, code, _sane_retry_at(retry_at, retry_after))
    grant = _parse_grant(resp, target.base)
    _log(kind, "ok")
    return grant


def close_task(grant: hosted_ai.TaskGrant) -> None:
    """通知网关这个任务结束（它立即记账）。尽力而为：任何失败都吞掉（网关 30 分钟空闲后也会自己关）。永不抛。"""
    try:
        target = resolve_target()
        if target is None or target.base != grant.base_url:
            return
        _request(
            target,
            "/v1/task/close",
            b"",
            {"Authorization": f"Bearer {grant.task_token}"},
            hosted_ai.CLOSE_TIMEOUT_S,
        )
    except Exception:  # noqa: BLE001
        LOG.debug("内置 AI: 关任务失败（忽略）")


hosted_ai.register(sys.modules[__name__])
