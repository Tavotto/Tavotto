"""改图助手的「账号登录」：设备码 OAuth。目前只有 MiniMax（国内 / 海外）；流程按厂商规格表驱动，可扩展。

（Kimi 不在这里：它改走官方 Kimi Code 命令行，作为独立的执行器接入。）

移植来源（只借协议细节，不含任何别家的 client_id）：

* RFC 8628 通用流程（`FLAVOR_RFC8628`，当前没有厂商使用，由用例里的合成厂商覆盖）：处理细节参照 kimi-cli 的
  `kimi_cli/auth/oauth.py`（Apache-2.0，MoonshotAI）——设备授权 → 按间隔轮询、对 authorization_pending /
  slow_down / expired_token 的处理、429 / 5xx 刷新重试。不含它的 client_id 与端点。
* MiniMax：OpenClaw 的 `extensions/minimax/oauth.ts`（MIT）——`/oauth2/device/code` +
  `/oauth2/token`、grant `urn:ietf:params:oauth:grant-type:user_code`、PKCE（S256）+ `state` 校验、
  scope `group_id profile model.completion`、过期时间「相对秒 / 绝对秒 / 绝对毫秒」三种表示、
  刷新只回到签发它的区域端点、响应体 8 KB 上限、30 s 超时。
  许可证全文与版权声明见 `docs/legal/THIRD_PARTY_PORTS.md`。

**client_id 是 Tavotto 自己的**（`CLIENT_IDS`，目前为 None = 还没申请到）：源码里不写任何别家的
client_id，也不冒充别家客户端的请求头；请求头只如实标 Tavotto 与版本。client_id 为 None 且没有
环境变量覆盖时，该厂商的「登录」入口整个不出现（`available()` 为 False），不显示占位。

令牌存放（`ai_providers.store_oauth`）：一条 `agent=claude` 的接口记录，`auth: "oauth"` +
`oauth: {access, refresh, expires_ms, token_endpoint, provider, region}`，与 api_key 一样只在用户配置里
（目录 0700、文件 0600）；`ai_providers.public()` 一个字节都不回传，诊断包按字段挑选、不含。
`ensure_fresh()` 在 `ai_bridge.run` spawn 之前刷新（剩余不足 10 分钟）；刷不动 → `NeedsLogin`，不 spawn。

纯标准库 urllib，Flask 父进程可 import；`ai_agents` 不 import 本模块（依赖方向：本模块 → ai_providers）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

from . import ai_providers, brand, tlstrust

LOG = logging.getLogger("tavotto.ai_oauth")

# ---------------------------------------------------------------------------
# client_id：集中在这一张表里
# ---------------------------------------------------------------------------
#: Tavotto 自己申请的 client_id。None = 还没拿到 → 该厂商不出现登录入口。
#: **不要把别家客户端的 client_id 写在这里**（`tests/test_ai_oauth.py` 钉：源码里没有 UUID 形状的常量）。
CLIENT_IDS: dict[str, str | None] = {"minimax": None}
#: 拿到之前联调用：环境变量覆盖（非空才算数）。
CLIENT_ID_ENV = {"minimax": "TAVOTTO_OAUTH_CLIENT_ID_MINIMAX"}

# ---------------------------------------------------------------------------
# 厂商规格（端点只在这里；测试 monkeypatch 这张表指向本机假服务器）
# ---------------------------------------------------------------------------
FLAVOR_RFC8628 = "rfc8628"
FLAVOR_MINIMAX = "minimax"

_SPECS: dict[str, dict] = {
    "minimax": {
        "flavor": FLAVOR_MINIMAX,
        "label": "MiniMax",
        "default_region": "cn",
        "device_path": "/oauth2/device/code",
        "token_path": "/oauth2/token",
        "scope": "group_id profile model.completion",
        "regions": {
            "cn": {
                "oauth_base": "https://account.minimaxi.com",
                "base_url": "https://api.minimaxi.com/anthropic",
            },
            "global": {
                "oauth_base": "https://account.minimax.io",
                "base_url": "https://api.minimax.io/anthropic",
            },
        },
    },
}
_REGION_LABELS = {"cn": "国内", "global": "海外"}

GRANT_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"
GRANT_USER_CODE = "urn:ietf:params:oauth:grant-type:user_code"

# ---------------------------------------------------------------------------
# 常量（测试会 monkeypatch 其中的时间类）
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_S = 30.0
ERROR_BODY_LIMIT = 8 * 1024  # 非 2xx 的响应体上限（OpenClaw 同值）
OK_BODY_LIMIT = 64 * 1024  # 2xx（令牌 JSON）的上限；超过当作畸形
REFRESH_MARGIN_MS = 10 * 60 * 1000  # 剩余不足 10 分钟就在 spawn 之前刷新
MIN_INTERVAL_S = 2.0  # 轮询间隔下限（RFC 8628 默认 5 s，MiniMax 默认 2 s）
DEFAULT_INTERVAL_S = 5.0
SLOW_DOWN_STEP_S = 5.0  # RFC 8628 §3.5
MAX_DEVICE_LIFETIME_S = 3600.0
DEFAULT_DEVICE_LIFETIME_S = 600.0
MAX_POLL_TRANSIENT_FAILURES = 5  # 连续这么多次传输层 / 5xx 失败才放弃
REFRESH_RETRY_SLEEPS_S = (
    1.0,
    2.0,
)  # 429 / 5xx / 传输失败：最多 3 次尝试，间隔 1 s、2 s（kimi-cli 是 2^n）
FLOW_KEEP = 8  # 内存里保留的已结束流程数（供界面最后一次轮询）

#: 相对秒 / 绝对秒 / 绝对毫秒 的分界（OpenClaw 同值）
_RELATIVE_SECONDS_BELOW = 1_000_000_000
_ABSOLUTE_MS_FROM = 1_000_000_000_000

# 流程状态
IDLE, WAITING, SIGNED_IN, EXPIRED, DENIED, CANCELLED, FAILED = (
    "idle",
    "waiting",
    "signed_in",
    "expired",
    "denied",
    "cancelled",
    "failed",
)
_TERMINAL = frozenset({SIGNED_IN, EXPIRED, DENIED, CANCELLED, FAILED})


class OAuthError(RuntimeError):
    """发起 / 管理登录流程的失败；`code` 是给 app.py 转 JSON 的稳定短码（不含服务端原文）。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class NeedsLogin(RuntimeError):
    """令牌刷不动（被拒 / 已过期且刷新失败 / 记录残缺）：必须重新登录，调用方不得 spawn。"""


# ---------------------------------------------------------------------------
# 可用性
# ---------------------------------------------------------------------------
def client_id(provider: str) -> str | None:
    env = os.environ.get(CLIENT_ID_ENV.get(provider, ""), "").strip()
    return env or CLIENT_IDS.get(provider) or None


def available() -> dict[str, dict]:
    """→ `{provider: {"label", "oauth_available": bool, "regions": [...], "default_region": str}}`，给 capabilities。

    没有 client_id 的厂商 `oauth_available` 为 False，界面据此**不出现**登录入口。
    """
    return {
        provider: {
            "label": spec["label"],
            "oauth_available": client_id(provider) is not None,
            "regions": list(spec["regions"]),
            "default_region": spec["default_region"],
        }
        for provider, spec in _SPECS.items()
    }


def _record_id(provider: str) -> str:
    return f"{provider}-oauth"


def _record_defaults(provider: str, region: str) -> dict:
    spec = _SPECS[provider]
    label = spec["label"]
    if len(spec["regions"]) > 1:
        label = f"{label}（{_REGION_LABELS.get(region, region)}）"
    # 模型名会过时：取目录里同 id 预设的，没有就留空让用户填（不在这里再抄一份）
    preset = next((p for p in ai_providers.PRESETS if p.get("id") == provider), {})
    return {
        "id": _record_id(provider),
        "label": f"{label} 账号",
        "agent": "claude",
        "base_url": spec["regions"][region]["base_url"],
        "models": list(preset.get("models") or []),
    }


# ---------------------------------------------------------------------------
# 传输（唯一出站点；`tests/test_outbound_https_trust.py` 登记 `_opener`）
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何重定向都不跟（令牌 / 刷新令牌绝不转交到别处）。必须抛而不是返回 None：见 diagsend。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener() -> urllib.request.OpenerDirector:
    """出站 HTTPS 的唯一 opener：平台原生证书校验（`tlstrust`）+ 不跟随重定向。"""
    ctx = tlstrust.client_context()
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    opener.add_handler(_NoRedirect())
    return opener


def _headers(extra: dict | None = None) -> dict:
    """如实标识：Tavotto 与版本，不冒充任何别家的客户端。"""
    from .. import __version__

    h = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": f"{brand.PRODUCT_NAME}/{__version__}",
    }
    h.update(extra or {})
    return h


@dataclass
class _Reply:
    status: int  # 0 = 传输层失败（没有 HTTP 响应）
    body: dict = field(default_factory=dict)  # 解析成 dict 的 JSON；不是 JSON 对象 = {}
    oversize: bool = False


def _post(url: str, fields: dict, extra_headers: dict | None = None) -> _Reply:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=_headers(extra_headers))
    try:
        with _opener().open(req, timeout=HTTP_TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read(OK_BODY_LIMIT + 1)
            limit = OK_BODY_LIMIT
    except urllib.error.HTTPError as err:
        status, limit = err.code, ERROR_BODY_LIMIT
        try:
            raw = err.read(limit + 1)
        except Exception:  # noqa: BLE001 — 读错误体失败不改变「这是个 HTTP 错误」
            raw = b""
    except Exception as exc:  # noqa: BLE001 — urllib / ssl / socket 的失败形状很多，统一归并
        # 只记异常类型：消息里可能带 URL
        LOG.info("账号登录请求传输失败（%s）", type(tlstrust.root_cause(exc)).__name__)
        return _Reply(0)
    if len(raw) > limit:
        return _Reply(status, {}, oversize=True)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {}
    return _Reply(status, parsed if isinstance(parsed, dict) else {})


# ---------------------------------------------------------------------------
# 令牌解析
# ---------------------------------------------------------------------------
def resolve_expiry_ms(value, now_ms: int) -> int | None:
    """过期时间的三种表示 → 绝对毫秒：< 1e9 相对秒；< 1e12 绝对秒；否则绝对毫秒。无效 → None。"""
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not num > 0 or num != num or num == float("inf"):
        return None
    if num < _RELATIVE_SECONDS_BELOW:
        return int(now_ms + num * 1000)
    if num < _ABSOLUTE_MS_FROM:
        return int(num * 1000)
    return int(num)


def _tokens_from(body: dict, now_ms: int, previous_refresh: str = "") -> dict | None:
    """令牌响应 → `{access, refresh, expires_ms}`；缺项 / 过期时间无效 → None。

    刷新响应可以不带 refresh_token（服务端不轮换）：沿用旧的（OpenClaw 同）。
    """
    access = body.get("access_token")
    refresh = body.get("refresh_token", previous_refresh) or previous_refresh
    expiry = body.get("expires_in", body.get("expired_in"))
    expires_ms = resolve_expiry_ms(expiry, now_ms)
    if not (isinstance(access, str) and access.strip()):
        return None
    if not (isinstance(refresh, str) and refresh.strip()) or expires_ms is None:
        return None
    return {"access": access, "refresh": refresh, "expires_ms": expires_ms}


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# 登录流程
# ---------------------------------------------------------------------------
@dataclass
class _Flow:
    flow_id: str
    provider: str
    region: str
    token_url: str
    cid: str
    state: str = WAITING
    user_code: str = ""
    verification_uri_complete: str = ""
    expires_at_ms: int = 0
    interval_s: float = DEFAULT_INTERVAL_S
    endpoint_id: str | None = None
    # --- 私有（status 不回传） ---
    device_code: str = ""
    verifier: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


_LOCK = (
    threading.RLock()
)  # 保护 _FLOWS 与流程状态；绝不在里面发网络请求（唯一的 I/O 是 `_store` 的本地落盘）
_START_LOCK = threading.Lock()  # 串行化 start()：设备授权请求在途时，第二次点击不会再发一个
_FLOWS: dict[str, _Flow] = {}
_REFRESH_LOCK = threading.Lock()


def _spec_region(provider: str, region: str | None) -> tuple[dict, str]:
    spec = _SPECS.get(provider)
    if spec is None:
        raise OAuthError("oauth_unavailable")
    region = region or spec["default_region"]
    if region not in spec["regions"]:
        raise OAuthError("oauth_unavailable")
    return spec, region


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


def _interval_from(value, flavor: str) -> float:
    """服务端给的轮询间隔 → 秒（下限 MIN_INTERVAL_S）。RFC 8628 是秒，MiniMax 是毫秒（OpenClaw）。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return max(MIN_INTERVAL_S, DEFAULT_INTERVAL_S if flavor == FLAVOR_RFC8628 else 2.0)
    if flavor == FLAVOR_MINIMAX:
        num /= 1000.0
    return max(MIN_INTERVAL_S, num) if num > 0 else MIN_INTERVAL_S


def public_flow(flow: _Flow) -> dict:
    """给界面的流程状态：不含 device_code / PKCE verifier / 任何令牌。"""
    out = {
        "flow_id": flow.flow_id,
        "provider": flow.provider,
        "region": flow.region,
        "state": flow.state,
        "user_code": flow.user_code,
        "verification_uri_complete": flow.verification_uri_complete,
        "expires_at": flow.expires_at_ms,
        "interval": flow.interval_s,
    }
    if flow.endpoint_id:
        out["endpoint_id"] = flow.endpoint_id
    return out


def start(provider: str, region: str | None = None) -> dict:
    """发起设备授权。同一厂商同时只有一个流程：已有进行中的同区域流程就原样返回它。"""
    spec, region = _spec_region(provider, region)
    cid = client_id(provider)
    if cid is None:
        raise OAuthError("oauth_unavailable")
    with _START_LOCK:
        with _LOCK:
            for old in list(_FLOWS.values()):
                if old.provider == provider and old.state == WAITING:
                    if old.region == region:
                        return public_flow(old)
                    _finish(old, CANCELLED)  # 换了区域：旧的作废，只留一个
        base = spec["regions"][region]["oauth_base"].rstrip("/")
        flavor = spec["flavor"]
        verifier = ""
        if flavor == FLAVOR_MINIMAX:
            verifier, challenge = _pkce()
            state = secrets.token_urlsafe(16)
            reply = _post(
                base + spec["device_path"],
                {
                    "response_type": "code",
                    "client_id": cid,
                    "scope": spec["scope"],
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": state,
                },
                {"x-request-id": str(uuid.uuid4())},
            )
        else:
            state = ""
            reply = _post(base + spec["device_path"], {"client_id": cid})
        flow = _flow_from_reply(provider, region, spec, reply, cid, base, verifier, state)
        with _LOCK:
            _FLOWS[flow.flow_id] = flow
            _prune()
        flow.thread = threading.Thread(
            target=_poll_loop, args=(flow, spec), name=f"ai-oauth-{provider}", daemon=True
        )
        flow.thread.start()
        LOG.info("账号登录流程开始: %s/%s", provider, region)
        return public_flow(flow)


def _flow_from_reply(
    provider: str,
    region: str,
    spec: dict,
    reply: _Reply,
    cid: str,
    base: str,
    verifier: str,
    state: str,
) -> _Flow:
    if reply.status == 0 or reply.oversize or not 200 <= reply.status < 300:
        raise OAuthError("oauth_start_failed")
    body = reply.body
    user_code = body.get("user_code")
    flavor = spec["flavor"]
    now = _now_ms()
    if flavor == FLAVOR_MINIMAX:
        uri = body.get("verification_uri")
        if body.get("state") != state:  # CSRF / 会话串线（OpenClaw）
            raise OAuthError("oauth_start_failed")
        expires_at = resolve_expiry_ms(body.get("expired_in"), now)  # 绝对毫秒（也容忍相对秒）
        device_code = str(user_code or "")  # MiniMax 轮询凭 user_code + code_verifier
        interval = _interval_from(body.get("interval"), flavor)
    else:
        uri = body.get("verification_uri_complete")
        device_code = str(body.get("device_code") or "")
        try:
            life = float(body.get("expires_in") or DEFAULT_DEVICE_LIFETIME_S)
        except (TypeError, ValueError):
            life = DEFAULT_DEVICE_LIFETIME_S
        expires_at = int(now + min(life, MAX_DEVICE_LIFETIME_S) * 1000)
        interval = _interval_from(body.get("interval"), flavor)
    if not (isinstance(user_code, str) and user_code and isinstance(uri, str) and uri):
        raise OAuthError("oauth_start_failed")
    if not device_code or expires_at is None:
        raise OAuthError("oauth_start_failed")
    return _Flow(
        flow_id=secrets.token_urlsafe(12),
        provider=provider,
        region=region,
        token_url=base + spec["token_path"],
        cid=cid,
        user_code=user_code,
        verification_uri_complete=uri,
        expires_at_ms=expires_at,
        interval_s=interval,
        device_code=device_code,
        verifier=verifier,
    )


def _prune() -> None:
    done = [f for f in _FLOWS.values() if f.state in _TERMINAL]
    for f in done[: max(0, len(done) - FLOW_KEEP)]:
        _FLOWS.pop(f.flow_id, None)


def _finish(flow: _Flow, state: str, endpoint_id: str | None = None) -> None:
    with _LOCK:
        if flow.state in _TERMINAL:
            return
        flow.state = state
        flow.endpoint_id = endpoint_id
        flow.device_code = ""
        flow.verifier = ""
        flow.cancel.set()
    LOG.info("账号登录流程结束: %s/%s → %s", flow.provider, flow.region, state)


def status(flow_id: str) -> dict | None:
    with _LOCK:
        flow = _FLOWS.get(flow_id)
        return public_flow(flow) if flow else None


def cancel(flow_id: str) -> dict | None:
    with _LOCK:
        flow = _FLOWS.get(flow_id)
        if flow is None:
            return None
        if flow.state == WAITING:
            _finish(flow, CANCELLED)
        return public_flow(flow)


def reset_for_tests() -> None:
    with _LOCK:
        for f in list(_FLOWS.values()):
            f.cancel.set()
        threads = [f.thread for f in _FLOWS.values() if f.thread]
        _FLOWS.clear()
    for t in threads:
        t.join(timeout=2)


# --- 轮询 -------------------------------------------------------------------
def _poll_once(flow: _Flow, spec: dict) -> tuple[str, object]:
    """→ ("success", tokens) | ("pending", None) | ("slow_down", None) | ("expired", None) |
    ("denied", None) | ("transient", None) | ("error", None)。"""
    if spec["flavor"] == FLAVOR_MINIMAX:
        fields = {
            "grant_type": GRANT_USER_CODE,
            "client_id": flow.cid,
            "user_code": flow.device_code,
            "code_verifier": flow.verifier,
        }
    else:
        fields = {
            "client_id": flow.cid,
            "device_code": flow.device_code,
            "grant_type": GRANT_DEVICE_CODE,
        }
    reply = _post(flow.token_url, fields)
    if reply.status == 0 or reply.status >= 500 or reply.status == 429:
        return "transient", None
    if reply.oversize:
        return "error", None
    body = reply.body
    if spec["flavor"] == FLAVOR_MINIMAX:
        # OpenClaw：HTTP 非 2xx / status=error → 失败；status 不是 success → 还在等
        if not 200 <= reply.status < 300 or body.get("status") == "error":
            return "error", None
        if body.get("status") != "success":
            return "pending", None
        tokens = _tokens_from(body, _now_ms())
        return ("success", tokens) if tokens else ("error", None)
    if reply.status == 200 and "access_token" in body:
        tokens = _tokens_from(body, _now_ms())
        return ("success", tokens) if tokens else ("error", None)
    code = str(body.get("error") or "")
    if code == "authorization_pending":
        return "pending", None
    if code == "slow_down":
        return "slow_down", None
    if code == "expired_token":
        return "expired", None
    if code == "access_denied":
        return "denied", None
    return "error", None


def _poll_loop(flow: _Flow, spec: dict) -> None:
    transient = 0
    try:
        while True:
            if flow.cancel.wait(flow.interval_s):  # 先等一个间隔再问（RFC 8628 §3.5）
                return
            if _now_ms() >= flow.expires_at_ms:
                _finish(flow, EXPIRED)
                return
            kind, payload = _poll_once(flow, spec)
            if flow.cancel.is_set():  # 在途请求期间被取消：结果作废，令牌一个字节都不落盘
                return
            if kind == "success":
                _store(flow, spec, payload)  # type: ignore[arg-type]
                return
            if kind == "transient":
                transient += 1
                if transient >= MAX_POLL_TRANSIENT_FAILURES:
                    _finish(flow, FAILED)
                    return
                continue
            transient = 0
            if kind == "slow_down":
                flow.interval_s += SLOW_DOWN_STEP_S
            elif kind in ("expired", "denied"):
                _finish(flow, EXPIRED if kind == "expired" else DENIED)
                return
            elif kind == "error":
                _finish(flow, FAILED)
                return
    except Exception:  # noqa: BLE001 — 后台线程不许带着未处理异常消失；界面看到的是 failed
        LOG.exception("账号登录轮询线程异常")
        _finish(flow, FAILED)


def _store(flow: _Flow, spec: dict, tokens: dict) -> None:
    oauth = {
        **tokens,
        "token_endpoint": flow.token_url,
        "provider": flow.provider,
        "region": flow.region,
    }
    # 「认领成功」与「令牌落盘」在同一个 `_LOCK` 临界区里（RLock，`_finish` 可重入）：
    # `cancel()` 要么先拿到锁（流程已 cancelled → 这里什么都不写），要么等这里写完再看到 signed_in。
    # 绝不会出现「流程报 cancelled、令牌却已落盘」。落盘是本地写，没有网络请求。
    with _LOCK:
        if flow.state != WAITING or flow.cancel.is_set():
            return
        try:
            rec = ai_providers.store_oauth(_record_defaults(flow.provider, flow.region), oauth)
        except Exception:  # noqa: BLE001 — 写配置失败（磁盘满 / 无权限）：如实失败，别假装登录了
            LOG.exception("账号登录成功但令牌写入失败")
            _finish(flow, FAILED)
            return
        _finish(flow, SIGNED_IN, rec["id"])


# ---------------------------------------------------------------------------
# 刷新 / 退出
# ---------------------------------------------------------------------------
def _known_token_endpoints(provider: str) -> set[str]:
    spec = _SPECS.get(provider) or {}
    return {
        r["oauth_base"].rstrip("/") + spec["token_path"] for r in spec.get("regions", {}).values()
    }


def ensure_fresh(rec: dict | None) -> dict | None:
    """spawn 之前调用：账号登录的记录剩余不足 10 分钟就刷新并写回，返回最新记录；其它记录原样返回。

    刷不动 → `NeedsLogin`（调用方不得 spawn）。例外：令牌**还没过期**、刷新只是遇到暂时性故障
    （断网 / 5xx / 429）时沿用现有令牌——此时 spawn 是安全的，且比白白拦住用户好。
    被服务端明确拒绝（4xx）、记录残缺、已过期且刷新失败，一律要重新登录。
    """
    if not rec or rec.get("auth") != "oauth":
        return rec
    with _REFRESH_LOCK:
        cur = ai_providers.get(rec.get("id")) or rec  # 锁内重读：别的线程可能刚刷过
        oauth = cur.get("oauth") if isinstance(cur.get("oauth"), dict) else {}
        access, refresh = oauth.get("access"), oauth.get("refresh")
        if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh):
            raise NeedsLogin("not signed in")
        expires_ms = oauth.get("expires_ms")
        now = _now_ms()
        if isinstance(expires_ms, (int, float)) and expires_ms - now > REFRESH_MARGIN_MS:
            return cur
        still_valid = isinstance(expires_ms, (int, float)) and expires_ms > now
        provider = str(oauth.get("provider") or "")
        endpoint = str(oauth.get("token_endpoint") or "")
        # 刷新令牌只发回签发它的那个区域端点；记录里的端点不在已知表里（被改过 / 旧格式）→ 不发
        if provider not in _SPECS or endpoint not in _known_token_endpoints(provider):
            raise NeedsLogin("unknown token endpoint")
        cid = client_id(provider)
        if cid is None:
            raise NeedsLogin("no client id")
        outcome, tokens = _refresh_request(endpoint, cid, refresh, now)
        if outcome == "ok":
            new = {**oauth, **tokens}
            updated = ai_providers.update_oauth(cur["id"], new)
            return updated or {**cur, "oauth": new}
        if outcome == "transient" and still_valid:
            # 日志里不带任何取自令牌记录的值（CodeQL py/clear-text-logging-sensitive-data）
            LOG.info("令牌刷新遇到暂时性故障，沿用尚未过期的令牌")
            return cur
        raise NeedsLogin("refresh failed")


def _refresh_request(endpoint: str, cid: str, refresh: str, now_ms: int) -> tuple[str, dict]:
    """→ ("ok", tokens) | ("rejected", {}) | ("transient", {})。429 / 5xx / 传输失败重试，4xx 不重试。"""
    attempts = len(REFRESH_RETRY_SLEEPS_S) + 1
    for i in range(attempts):
        reply = _post(
            endpoint, {"grant_type": "refresh_token", "client_id": cid, "refresh_token": refresh}
        )
        if reply.status == 0 or reply.status == 429 or reply.status >= 500:
            if i < attempts - 1:
                time.sleep(REFRESH_RETRY_SLEEPS_S[i])
                continue
            return "transient", {}
        body = reply.body
        # MiniMax 在 200 里也可能带 error / status=error / base_resp.status_code != 0（OpenClaw）
        base_code = (
            (body.get("base_resp") or {}).get("status_code")
            if isinstance(body.get("base_resp"), dict)
            else None
        )
        if (
            not 200 <= reply.status < 300
            or reply.oversize
            or "error" in body
            or body.get("status") == "error"
            or (base_code not in (None, 0))
        ):
            return "rejected", {}
        tokens = _tokens_from(body, now_ms, previous_refresh=refresh)
        return ("ok", tokens) if tokens else ("rejected", {})
    return "transient", {}


def logout(pid: str) -> dict:
    """退出登录：删掉这条记录里的令牌（记录与用户改过的模型保留）。

    **不调用对方的撤销接口**：两份参照实现都没有（Kimi CLI 的 logout 只删本地令牌与配置，OpenClaw 无登出），
    我们不发明一个未经确认的出站请求。要让令牌立即失效，用户到厂商网页里撤销授权。
    """
    rec = ai_providers.get(pid)
    if rec is None or rec.get("auth") != "oauth":
        raise ValueError(f"该接口不是账号登录: {pid}")
    with _REFRESH_LOCK:
        out = ai_providers.update_oauth(pid, None)
    return out or rec
