"""校验与转发的**全部业务逻辑**（纯标准库，与部署平台无关）。

对外三个路由：

    GET  /healthz     存活探针
    POST /v1/events   公开匿名端点，桌面客户端发的产品事件
    POST /v1/metrics  受 bearer token 保护，定时采集器发的发行量快照

`/v1/events` 必然是公开的（桌面应用里嵌任何「密钥」都等于公开），所以这里
**不假装做认证**，而是把滥用的性价比压低：严格 schema、只认白名单里的事件与
属性、限制请求体大小、限制字符串长度、要求 UUIDv4 形状的 distinct_id、
上游超时很短。真正的速率限制交给部署层（见 README）——那是它擅长的事，
在这里自己实现一个内存计数器在 serverless 上根本不成立（每个实例各数各的）。
"""
from __future__ import annotations

import hmac
import json
import re
import uuid

from . import posthog
from .contract import (AUTO_PROPS, EVENTS, METRICS_DISTINCT_ID, METRICS_EVENTS,
                       SCHEMA_VERSION)

#: 公开端点的请求体上限。一条合法事件不到 1 KiB，8 KiB 已经很宽松。
MAX_EVENT_BODY = 8 * 1024
#: 采集器一次可以送一批（一天的全部资产快照），所以另给一档。
MAX_METRICS_BODY = 256 * 1024
MAX_METRICS_BATCH = 500

_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+_-]{1,32}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_KEY = re.compile(r"^[A-Za-z0-9:._-]{1,120}$")


class Rejected(Exception):
    """请求不合规。`code` 是稳定标识，`message` 里**绝不回显收到的内容**。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# 值校验
# ---------------------------------------------------------------------------
def _check_value(spec: dict, value):
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise Rejected("bad_property", "property type mismatch")
        return value
    if kind == "int":
        # bool 是 int 的子类：不挡的话 downloads=True 会被当成 1 收下
        if isinstance(value, bool) or not isinstance(value, int):
            raise Rejected("bad_property", "property type mismatch")
        if value < 0 or value > spec["max"]:
            raise Rejected("bad_property", "property out of range")
        return value
    if kind == "enum":
        if not isinstance(value, str) or value not in spec["values"]:
            raise Rejected("bad_property", "property not in enum")
        return value
    if kind in ("version", "date", "key"):
        pattern = {"version": _VERSION, "date": _DATE, "key": _KEY}[kind]
        if not isinstance(value, str) or not pattern.match(value):
            raise Rejected("bad_property", "property format mismatch")
        return value
    raise Rejected("bad_property", "unsupported property kind")


def _check_properties(allowed: dict[str, dict], properties) -> dict:
    if not isinstance(properties, dict):
        raise Rejected("bad_properties", "properties must be an object")
    out: dict = {}
    for key, value in properties.items():
        if not isinstance(key, str):
            raise Rejected("bad_properties", "property names must be strings")
        spec = allowed.get(key)
        if spec is None:
            # **不静默丢弃**：未知属性说明两边契约漂开了，安静地扔掉会让
            # 「新版本发出去之后那个指标一直是 0」永远查不出来。
            raise Rejected("unknown_property", "unknown property")
        # 嵌套结构在这一步天然被拒：没有任何一个 spec 接受 dict / list
        out[key] = _check_value(spec, value)
    return out


def _check_schema_version(body: dict) -> None:
    if body.get("schema_version") != SCHEMA_VERSION:
        raise Rejected("bad_schema_version", "unsupported schema_version")


# ---------------------------------------------------------------------------
# /v1/events
# ---------------------------------------------------------------------------
def handle_event(body: dict) -> dict:
    _check_schema_version(body)
    event = body.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        raise Rejected("unknown_event", "unknown event")
    distinct_id = body.get("distinct_id")
    if not isinstance(distinct_id, str) or not _UUID4.match(distinct_id.lower()):
        # 匿名标识必须是本机随机生成的 UUIDv4。别的形状（邮箱、主机名、
        # 机器 id）一律拒——那正是我们不想收到的东西。
        raise Rejected("bad_distinct_id", "distinct_id must be a random UUIDv4")
    try:
        uuid.UUID(distinct_id)
    except ValueError:
        raise Rejected("bad_distinct_id", "distinct_id must be a random UUIDv4") from None
    allowed = {**AUTO_PROPS, **EVENTS[event]}
    props = _check_properties(allowed, body.get("properties") or {})
    posthog.send([posthog.build_event(
        event, distinct_id, props,
        anonymous=posthog.person_profiles_mode() == "anonymous")])
    return {"ok": True}


# ---------------------------------------------------------------------------
# /v1/metrics
# ---------------------------------------------------------------------------
def _authorized(header: str | None) -> bool:
    import os
    expected = os.environ.get("TAVOTTO_METRICS_TOKEN") or ""
    if not expected:
        return False                    # 没配 token = 这个端点关着，不是敞开
    prefix = "Bearer "
    if not header or not header.startswith(prefix):
        return False
    # 常量时间比较：长度不同也不能提前 return（compare_digest 自己处理）
    return hmac.compare_digest(header[len(prefix):], expected)


def handle_metrics(body: dict, authorization: str | None) -> dict:
    if not _authorized(authorization):
        # 既不回显收到的 token，也不区分「没带」和「带错了」
        raise Rejected("unauthorized", "unauthorized", status=401)
    _check_schema_version(body)
    raw = body.get("events")
    if raw is None:
        raw = [{"event": body.get("event"), "properties": body.get("properties")}]
    if not isinstance(raw, list) or not raw:
        raise Rejected("bad_batch", "events must be a non-empty array")
    if len(raw) > MAX_METRICS_BATCH:
        raise Rejected("batch_too_large", "too many events", status=413)

    batch = []
    for item in raw:
        if not isinstance(item, dict):
            raise Rejected("bad_batch", "each event must be an object")
        event = item.get("event")
        if not isinstance(event, str) or event not in METRICS_EVENTS:
            raise Rejected("unknown_event", "unknown metrics event")
        props = _check_properties(METRICS_EVENTS[event], item.get("properties") or {})
        batch.append(posthog.build_event(
            event, METRICS_DISTINCT_ID, props,
            # 发行量快照永远匿名：它们不对应任何一个人
            anonymous=True,
            snapshot_key=props.get("snapshot_key")))
    posthog.send(batch)
    return {"ok": True, "accepted": len(batch)}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def _parse_json(raw: bytes, limit: int) -> dict:
    if len(raw) > limit:
        raise Rejected("payload_too_large", "request body too large", status=413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Rejected("bad_json", "request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise Rejected("bad_json", "request body must be a JSON object")
    return body


def handle(method: str, path: str, headers: dict, raw: bytes) -> tuple[int, dict]:
    """整个服务的入口。**与部署平台无关**：Vercel 的 handler、WSGI、单测都调它。

    `headers` 的键**必须已经小写**（各平台的大小写习惯不同，归一化交给适配层）。
    回 (状态码, JSON 体)；任何情况下都不抛异常给适配层。
    """
    path = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    try:
        if path == "/healthz":
            if method != "GET":
                raise Rejected("method_not_allowed", "GET only", status=405)
            return 200, {"ok": True, "service": "tavotto-telemetry-proxy"}

        if path not in ("/v1/events", "/v1/metrics"):
            raise Rejected("not_found", "not found", status=404)
        if method != "POST":
            raise Rejected("method_not_allowed", "POST only", status=405)
        ctype = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise Rejected("bad_content_type", "Content-Type must be application/json",
                           status=415)

        if path == "/v1/events":
            return 200, handle_event(_parse_json(raw, MAX_EVENT_BODY))
        return 200, handle_metrics(_parse_json(raw, MAX_METRICS_BODY),
                                   headers.get("authorization"))
    except Rejected as exc:
        return exc.status, {"ok": False, "code": exc.code, "error": exc.message}
    except posthog.UpstreamError as exc:
        # 上游挂了要如实报（客户端会丢弃这条事件），但消息里没有密钥、
        # 没有载荷、没有上游响应体
        return 502, {"ok": False, "code": "upstream_error", "error": str(exc)}
    except Exception:                          # noqa: BLE001 — 绝不把 traceback 交给公网
        return 500, {"ok": False, "code": "internal_error", "error": "internal error"}
