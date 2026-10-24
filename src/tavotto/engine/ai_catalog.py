"""改图助手的「厂商目录」：预设的唯一数据源 + 型号实时列表。

* **目录是数据，不是代码**：`ai_catalog.json` 一行一家厂商（接口地址、申请 Key 的页面、
  型号提示、出处）。界面名与 key 提示只存 i18n 键，不在 JSON 里写中文。加一家厂商 = 加一行 +
  两份 locale 文案，`tests/test_ai_catalog.py` 的结构用例会拦住漏项。
* **加载失败必须大声失败**：`load_vendors()` 对文件缺失 / 不是合法 JSON / 形状不对一律抛
  `CatalogError`，模块 import 时就会炸；绝不退成「空列表」让设置页静默没有任何预设。
* **型号实时取**：`live_models()` 对一条接口 `GET {base_url}/v1/models`，5 s 超时，成功的结果按
  （接口 id, base_url, 密钥指纹）缓存一天在 `config.data_dir()` 下；任何失败都只返回稳定的原因码，
  **不抛**——调用方（界面）退回目录里的 `models_hint` 或用户手填的列表。
* **密钥绝不外泄**：只放进请求头；缓存键里只有密钥的 sha256 前缀；日志只写接口 id 与原因码，
  不写 URL、头、响应体。

依赖方向 `ai_agents ← ai_providers ← ai_bridge`：本模块不 import 它们（只用 config / tlstrust），
由 `ai_providers` 在上面套一层「按已存接口取 Key」。纯标准库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, netpolicy, tlstrust

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).with_name("ai_catalog.json")

#: 目录里每一行都必须有的字段（`oauth` 是预留位：本期界面不渲染「登录」）。
REQUIRED_FIELDS = (
    "id",
    "label_key",
    "icon_key",
    "brand",
    "region",
    "agent",
    "base_url",
    "key_url",
    "models_hint",
    "oauth",
    "source",
)
#: `custom` = 官方登录态 / 自定义两类之外的「用户自填」行，界面单独成组。
REGIONS = ("cn", "global", "custom")


class CatalogError(RuntimeError):
    """目录文件缺失或形状不对。"""


def load_vendors(path: Path = CATALOG_PATH) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CatalogError(f"厂商目录读不出来: {path.name}: {exc}") from exc
    rows = raw.get("vendors") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        raise CatalogError("厂商目录没有 vendors 列表")
    for row in rows:
        if not isinstance(row, dict):
            raise CatalogError("厂商目录里有非对象的行")
        missing = [k for k in REQUIRED_FIELDS if k not in row]
        if missing:
            raise CatalogError(f"厂商 {row.get('id')!r} 缺字段: {missing}")
    return rows


def _preset(row: dict) -> dict:
    """目录行 → 对外的预设形状（`/api/ai/capabilities` 的 presets 一项）。

    `label` 是品牌名（保存成接口时的默认名、迁移时用）；界面显示的名字走 `label_key`。
    `models` 沿用旧字段名，值取 `models_hint`。"""
    out = {
        "id": row["id"],
        "label": row["brand"],
        "label_key": row["label_key"],
        "icon_key": row["icon_key"],
        "agent": row["agent"],
        "region": row["region"],
        "base_url": row["base_url"],
        "key_url": row["key_url"],
        "models": list(row["models_hint"]),
        "oauth": row["oauth"],
        "source": row["source"],
    }
    if row.get("key_hint_key"):
        out["key_hint_key"] = row["key_hint_key"]
    if row.get("wire_api"):
        out["wire_api"] = row["wire_api"]
    return out


#: 「思考强度」档位的合法取值（内置执行器 `agent_runner.wires`：anthropic 的 `output_config.effort` 多一档 max，Responses 的 `reasoning.effort` 只有三档）
_EFFORT_VALUES = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high"),
}


def _checked_efforts(owner: str, agent: str, efforts: object) -> list[str]:
    if not isinstance(efforts, list) or any(
        e not in _EFFORT_VALUES.get(agent, ()) for e in efforts
    ):
        raise CatalogError(f"{owner} 的 efforts 不合法: {efforts!r}")
    return list(efforts)


def _support(owner: str, agent: str, efforts: object, default: object) -> dict:
    checked = _checked_efforts(owner, agent, efforts)
    if default is not None and default not in checked:
        raise CatalogError(f"{owner} 的默认档位不在 efforts 里: {default!r}")
    return {"efforts": checked, "default": default if checked else None}


def _model_key(model: str | None) -> str:
    """型号名归一后拿来做前缀匹配：小写，`.` 与 `-` 视为同一个（`claude-opus-4.6` = `claude-opus-4-6`）。"""
    return (model or "").strip().lower().replace(".", "-")


def is_effort_origin(agent: str, base_url: str) -> bool:
    """这个地址是不是目录登记过的官方 API 来源（`effort_origins`）——只有它们才可能有思考强度档位。"""
    want = origin_of(base_url)
    if want is None:
        return False
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(
        o.get("agent") == agent and origin_of(o.get("origin", "")) == want
        for o in (raw.get("effort_origins") if isinstance(raw, dict) else None) or []
    )


def efforts_for(agent: str, base_url: str, model: str | None = None) -> list[str]:
    """内置执行器在这个地址、这个型号上**确认支持**的思考强度档位；不确定 = `[]`（宁可不显示，也不发对方不认的字段）。
    细节见 `effort_support`。"""
    return effort_support(agent, base_url, model)["efforts"]


def effort_support(agent: str, base_url: str, model: str | None = None) -> dict:
    """→ `{"efforts": [...], "default": 默认档位 | None}`。

    两处声明：目录行自带 `efforts`（地址和家族对上这一行，所有型号），或顶层 `effort_origins`（官方 API 的来源：scheme + host + 端口对上）。
    `effort_origins` 条目可带 `models: [{prefix, efforts}]`——**按型号前缀、先中先得**（`efforts: []` = 这个前缀明确不支持，写在更宽的前缀前面）；
    没有 `models` 的条目 = 该来源所有型号。带了 `models` 却没给型号（或没有前缀命中）= `[]`。
    出处（官方文档）写在条目的 `source` 里。"""
    none: dict = {"efforts": [], "default": None}
    url = _norm_url(base_url)
    if not url:
        return none
    for r in load_vendors(CATALOG_PATH):
        if r["agent"] == agent and _norm_url(r["base_url"]) == url and r.get("efforts"):
            return _support(r["id"], agent, r["efforts"], r.get("default_effort"))
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return none
    want = origin_of(base_url)
    for o in (raw.get("effort_origins") if isinstance(raw, dict) else None) or []:
        if o.get("agent") != agent or want is None or origin_of(o.get("origin", "")) != want:
            continue
        owner = str(o.get("origin"))
        if "models" not in o:
            return _support(owner, agent, o.get("efforts"), o.get("default"))
        key = _model_key(model)
        for rule in o["models"]:
            if key and key.startswith(_model_key(rule.get("prefix"))):
                return _support(
                    f"{owner} {rule.get('prefix')}", agent, rule.get("efforts"), rule.get("default")
                )
        return none
    return none


def presets() -> list[dict]:
    """旧版设置页的预设列表。`preset: false` 的行（官方 API Key：只作「连接」视图里某厂商的一种接入方式）不在其中。"""
    return [_preset(r) for r in load_vendors() if r.get("preset", True) is not False]


#: 不在目录里的接口（自定义）用的图标键；前端 `VendorIcon` 对表外 key 落到通用图标
GENERIC_ICON_KEY = "custom"


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def icon_for(agent: str, base_url: str) -> str:
    """已保存接口 → 图标键：地址（忽略末尾斜杠与大小写）和执行器都对上目录某一行才用它的，否则通用。

    按地址而不是 id 对：接口 id 是用户起的名字的 slug（可能是任何东西），地址才是「这是哪家」的事实。"""
    url = _norm_url(base_url)
    if url:
        for r in load_vendors():
            if r["agent"] == agent and _norm_url(r["base_url"]) == url:
                return r["icon_key"]
    return GENERIC_ICON_KEY


# --------------------------------------------------------------------------
# 型号实时列表
# --------------------------------------------------------------------------

TIMEOUT_S = 5.0
CACHE_TTL_S = 24 * 3600
_MAX_MODELS = 300
_MAX_BODY = 2 * 1024 * 1024
_CACHE_LOCK = threading.Lock()

#: 稳定的原因码（界面按它查文案，`dialogs:settings.agents.endpoint.fetchReason.*`）
REASONS = (
    "no_key",
    "key_origin_mismatch",
    "insecure_url",
    "timeout",
    "unauthorized",
    "bad_response",
    "network",
)


def url_refusal(base_url: str) -> str | None:
    """带密钥的请求能不能发往这个地址 → None（可以）或原因码。

    只许 https；明文 http 只放本机回环与私网（实验室自建的模型服务器；判据只在 `netpolicy`：主机名要所有解析结果都在放行段），
    其余一律拒绝，免得密钥在网上裸奔。"""
    try:
        parts = urllib.parse.urlsplit((base_url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return "bad_response"
    scheme = parts.scheme.lower()
    if scheme == "https" and host:
        return None
    if scheme == "http" and host:
        return None if netpolicy.plain_http_allowed(host) else "insecure_url"
    return "bad_response"


def origin_of(url: str) -> tuple[str, str, int] | None:
    """(scheme, host, port)，默认端口归一、host 小写；解析不了 → None。"""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or {"http": 80, "https": 443}.get(scheme, 0)
    except ValueError:
        return None
    if scheme not in ("http", "https") or not host:
        return None
    return scheme, host, port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向：密钥在请求头里，绝不替用户转交到另一个主机。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def models_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base + ("/models" if base.endswith("/v1") else "/v1/models")


def _opener(*, direct: bool = False) -> urllib.request.OpenerDirector:
    """`direct=True`：不经任何代理（回环 http 用）。`build_opener` 默认装环境变量里的
    `ProxyHandler`——回环例外只核了 URL 的 host，真正的网络目的地却可能是 HTTP_PROXY，
    密钥会以明文交给代理。https 照旧可走代理（隧道里是 TLS）。"""
    if direct:
        opener = urllib.request.build_opener(
            tlstrust.https_handler(tlstrust.client_context()), urllib.request.ProxyHandler({})
        )
    else:
        opener = urllib.request.build_opener(tlstrust.https_handler(tlstrust.client_context()))
    opener.add_handler(_NoRedirect())
    return opener


def _parse_models(body: bytes) -> list[str]:
    data = json.loads(body.decode("utf-8"))
    items = data.get("data") if isinstance(data, dict) else None
    if items is None and isinstance(data, dict):
        items = data.get("models")
    if not isinstance(items, list):
        raise ValueError("no model list")
    out: list[str] = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else it
        if isinstance(mid, str) and mid.strip() and mid.strip() not in out:
            out.append(mid.strip())
        if len(out) >= _MAX_MODELS:
            break
    return out


def fetch_models(base_url: str, api_key: str) -> tuple[list[str] | None, str | None]:
    """→ (型号列表, None) 或 (None, 原因码)。不抛。"""
    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    if not base_url or not api_key:
        return None, "no_key"
    refusal = url_refusal(base_url)
    if refusal:
        return None, refusal
    url = models_url(base_url)
    extra: dict[str, str] = {}
    if urllib.parse.urlsplit(url).scheme.lower() == "http":
        # 明文 http（回环 / 私网）：把判定时解析出的 IP 钉进 URL 直连，`Host` 头保留原主机名（防 DNS 重绑定）
        try:
            url, host_header = netpolicy.pin_url(url)
        except ValueError:
            return None, "insecure_url"
        extra["Host"] = host_header
    req = urllib.request.Request(  # noqa: S310 - 上面已限定 https / 回环或私网 http
        url,
        headers={
            **extra,
            # Anthropic 兼容网关认 x-api-key，OpenAI 系认 Bearer：两个都给，多给无副作用
            "x-api-key": api_key,
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        },
    )
    try:
        direct = urllib.parse.urlsplit(base_url).scheme.lower() == "http"
        with _opener(direct=direct).open(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(_MAX_BODY + 1)
        if len(body) > _MAX_BODY:
            return None, "bad_response"
        return _parse_models(body), None
    except urllib.error.HTTPError as exc:
        return None, "unauthorized" if exc.code in (401, 403) else "bad_response"
    except (TimeoutError, socket.timeout):
        return None, "timeout"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return None, "timeout"
        return None, "network"
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None, "bad_response"
    except OSError:
        return None, "network"


def _fingerprint(api_key: str) -> str:
    """缓存键里区分「换了 key」用的短指纹（落盘，所以不用裸哈希：PBKDF2 让拿到缓存文件的人没法廉价地离线验证猜测的 key）。"""
    return hashlib.pbkdf2_hmac(
        "sha256", api_key.encode("utf-8"), b"tavotto-ai-models-cache", 20_000
    ).hex()[:12]


def _cache_path() -> Path:
    return config.data_dir() / "ai_models_cache.json"


def _cache_key(pid: str, base_url: str, api_key: str) -> str:
    return f"{pid}|{base_url.strip().rstrip('/')}|{_fingerprint(api_key)}"


def _read_cache() -> dict:
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_cache(cache: dict) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ai_models_cache.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        log.warning("型号缓存写不进去，下次重新获取")


def live_models(pid: str, base_url: str, api_key: str, *, refresh: bool = False) -> dict:
    """→ `{"models": [...], "source": "live"|"cache"|"none", "reason": 原因码|None}`。永不抛。

    `source == "none"` 时 `models` 为空——由调用方退回 `models_hint` / 用户手填的列表。"""
    api_key = (api_key or "").strip()
    if not (base_url or "").strip() or not api_key:
        return {"models": [], "source": "none", "reason": "no_key"}
    refusal = url_refusal(base_url)
    if refusal:
        return {"models": [], "source": "none", "reason": refusal}
    key = _cache_key(pid, base_url, api_key)
    now = time.time()
    if not refresh:
        with _CACHE_LOCK:
            hit = _read_cache().get(key)
        if (
            isinstance(hit, dict)
            and isinstance(hit.get("models"), list)
            and isinstance(hit.get("ts"), (int, float))
            and 0 <= now - hit["ts"] < CACHE_TTL_S
        ):
            return {"models": list(hit["models"]), "source": "cache", "reason": None}
    models, reason = fetch_models(base_url, api_key)
    if models is None:
        log.info("型号列表获取失败: endpoint=%s reason=%s", pid, reason)
        return {"models": [], "source": "none", "reason": reason}
    with _CACHE_LOCK:
        cache = {
            k: v
            for k, v in _read_cache().items()
            if isinstance(v, dict)
            and isinstance(v.get("ts"), (int, float))
            and now - v["ts"] < CACHE_TTL_S
        }
        cache[key] = {"ts": now, "models": models}
        _write_cache(cache)
    return {"models": models, "source": "live", "reason": None}
