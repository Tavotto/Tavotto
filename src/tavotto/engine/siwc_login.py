"""Sign in with ChatGPT（SIWC）登录：授权码 + PKCE、一次性本机回调、换令牌、ID 令牌验签、刷新、撤销、型号清单（ADR 0209 §一 / §三）。

官方文档（`developers.openai.com/siwc/token-sharing-open-source/*`，2026-11-25 读取）与 OIDC 发现文档
（`https://auth.openai.com/.well-known/openid-configuration`，同日读取）定下的硬规则，在这里逐条落地：

* 首次注册 `client_id=dynamic_agent_client` + `agent_name_hint` + `ext_agent_host_id`；回调带回 `oaiapp_…` 才算注册完成，
  `dynamic_agent_client` **永不**存成已签发 id。再次登录同一账号用存下的 client_id、不发 `agent_name_hint`、带 `id_token_hint`；
  回调的 client_id 与所选不一致 → 拒绝。
* 每次尝试新的 `state` / `nonce` / PKCE（S256）；`redirect_uri` 只用 `http://127.0.0.1:<端口>/auth/callback`（不用 localhost），授权与换令牌发同一个。
* 回调监听器是一次性的 `http.server`（**不是 Flask 路由**：不提供任何 Tavotto 能力，只把 code 交回本进程，凭 `state` 认人；ADR 0209 §一），
  只绑 127.0.0.1、只认 `GET /auth/callback`，收到一次 / 超时 / 取消即关；先起监听、再开浏览器。端口先试 1455，被占用换系统分配的空闲端口。
* ID 令牌：RS256 签名对 JWKS（成熟库 pycryptodomex 验签，延后 import；import 不了 = 验签失败 = 登录失败，**不降级成不验**）、`iss`、`aud` == 已签发 client_id、
  `exp`（5 分钟时钟偏差）、`nonce`。身份 = 验过的 `sub`，email 只作显示。
* 授权范围没有 `chatgpt.tokens.use.direct` = 已登录但不能用订阅额度：记录照存，状态 `plan_disabled`，不进推理、不换计费方式。
* 退出登录：撤销刷新令牌（空 200 = 成功），没确认也清本机令牌并如实回报；保留 client_id 与 host id。

令牌 / code / verifier 一个字节都不进日志、事件、`public_flow()`；日志只记闭集原因码与异常类型。
出站 HTTPS 只经 `_opener`（`tlstrust` + 不跟随重定向；`tests/test_outbound_https_trust.py` 登记）。
纯标准库（验签那一处除外），Flask 父进程可 import。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import logging
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace

from . import brand, siwc_auth, siwc_store, tlstrust
from .agent_runner import siwc_wire

LOG = logging.getLogger("tavotto.siwc_login")

# --------------------------------------------------------------------------- 端点（OIDC 发现文档，2026-11-25）
ISSUER = siwc_store.ISSUER
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
REVOKE_URL = "https://auth.openai.com/api/accounts/oauth/revoke"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
RESOURCE = siwc_wire.BASE_URL
MODELS_URL = RESOURCE + "/models"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
PREFERRED_PORT = 1455

HTTP_TIMEOUT_S = 30.0
OK_BODY_LIMIT = 256 * 1024
#: 型号清单单独放宽：真服务的 `/v1/models` 每个型号带一整套元数据，256 KB 装不下（2026-11-25 真服务核对：超限被当成空清单）
MODELS_BODY_LIMIT = 4 * 1024 * 1024
ERROR_BODY_LIMIT = 8 * 1024
FLOW_TIMEOUT_S = 600.0
CLOCK_SKEW_S = 300
FLOW_KEEP = 8

# 流程状态（闭集）
WAITING = "waiting"  # 浏览器里还没完成
EXCHANGING = "exchanging"  # 回调到了，正在换令牌 / 验签 / 取型号
SIGNED_IN = "signed_in"
PLAN_DISABLED = "plan_disabled"  # 登录了，但没给订阅额度的授权
DENIED = "denied"
CANCELLED = "cancelled"
EXPIRED = "expired"
FAILED = "failed"
_TERMINAL = frozenset({SIGNED_IN, PLAN_DISABLED, DENIED, CANCELLED, EXPIRED, FAILED})

#: `failed` 的原因（闭集，前端据此给文案；不含任何服务端原文）
REASONS = frozenset(
    {
        "port_unavailable",  # 本机回调端口一个都起不来
        "state_mismatch",
        "registration_incomplete",  # 首次注册的回调没带回 oaiapp_… client_id
        "client_mismatch",  # 再次登录回调的 client_id 与所选账号不一致
        "exchange_failed",  # 换令牌失败（网络 / 服务端 / invalid_grant）
        "id_token_invalid",  # 验签或声明不过
        "verify_unavailable",  # 验签库不可用
        "account_mismatch",  # 再次登录换成了另一个账号
        "network",
        "store_failed",
        "needs_login",  # 刷新型号时：没登录 / 已失效
    }
)


class LoginError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# --------------------------------------------------------------------------- 传输
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何重定向都不跟（令牌 / 刷新令牌绝不转交到别处）。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener() -> urllib.request.OpenerDirector:
    """出站 HTTPS 的唯一 opener：平台原生证书校验（`tlstrust`）+ 不跟随重定向。"""
    ctx = tlstrust.client_context()
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    opener.add_handler(_NoRedirect())
    return opener


def _user_agent() -> str:
    from .. import __version__

    return f"{brand.PRODUCT_NAME}/{__version__}"


@dataclass
class _Reply:
    status: int  # 0 = 传输层失败
    body: dict = field(default_factory=dict)


def _send(req: urllib.request.Request, ok_limit: int = OK_BODY_LIMIT) -> _Reply:
    req.add_header("User-Agent", _user_agent())
    req.add_header("Accept", "application/json")
    try:
        with _opener().open(req, timeout=HTTP_TIMEOUT_S) as resp:
            status, raw, limit = resp.status, resp.read(ok_limit + 1), ok_limit
    except urllib.error.HTTPError as err:
        status, limit = err.code, ERROR_BODY_LIMIT
        try:
            raw = err.read(limit + 1)
        except Exception:  # noqa: BLE001
            raw = b""
    except Exception as exc:  # noqa: BLE001 - urllib / ssl / socket 的失败形状很多
        LOG.info("SIWC 请求传输失败（%s）", type(tlstrust.root_cause(exc)).__name__)
        return _Reply(0)
    if len(raw) > limit:
        LOG.info("SIWC 响应超过上限（%d 字节）", limit)
        return _Reply(
            0 if 200 <= status < 300 else status
        )  # 2xx 超限 = 没拿到，不能当成「空的成功」
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        parsed = {}
    return _Reply(status, parsed if isinstance(parsed, dict) else {})


def _post_form(url: str, fields: dict) -> _Reply:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    return _send(req)


def _get_json(url: str, bearer: str | None = None, ok_limit: int = OK_BODY_LIMIT) -> _Reply:
    req = urllib.request.Request(url, method="GET")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    return _send(req, ok_limit)


def _oauth_error(body: dict) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        err = err.get("code")
    return err if isinstance(err, str) else ""


# --------------------------------------------------------------------------- ID 令牌
_JWKS: dict[str, dict] = {}
_JWKS_LOCK = threading.Lock()


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _jwk(kid: str) -> dict | None:
    """按 `kid` 取公钥；缓存里没有就重取一次 JWKS（官方轮换签名密钥）。"""
    with _JWKS_LOCK:
        if kid in _JWKS:
            return _JWKS[kid]
        reply = _get_json(JWKS_URL)
        if reply.status != 200:
            return None
        for k in reply.body.get("keys") or []:
            if isinstance(k, dict) and isinstance(k.get("kid"), str) and k.get("kty") == "RSA":
                _JWKS[k["kid"]] = k
        return _JWKS.get(kid)


def _verify_rs256(signing_input: bytes, signature: bytes, jwk: dict) -> bool:
    try:
        # 成熟库 pycryptodomex（BSD / 公有领域，三平台都有 abi3 轮子；`Cryptodome` 命名空间不与用户环境里的旧 PyCrypto 撞名）。
        # 不用 pyca cryptography（Intel Mac 无轮子，见 pyproject），不用 `rsa`（2025-04 起已归档、不再维护）。只在这里延后 import
        from Cryptodome.Hash import SHA256
        from Cryptodome.PublicKey import RSA
        from Cryptodome.Signature import pkcs1_15
    except ImportError as exc:
        raise LoginError("verify_unavailable") from exc
    try:
        n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
        pkcs1_15.new(RSA.construct((n, e))).verify(SHA256.new(signing_input), signature)
    except (KeyError, ValueError, TypeError):
        return False
    return True


def verify_id_token(
    id_token: str, *, client_id: str, nonce: str, now_s: float | None = None
) -> dict:
    """验签 + 验声明 → claims；任何一条不过 → `LoginError("id_token_invalid")`（验签库缺 → `verify_unavailable`）。"""
    try:
        h64, p64, s64 = id_token.split(".")
        header = json.loads(_b64url_decode(h64))
        claims = json.loads(_b64url_decode(p64))
        signature = _b64url_decode(s64)
    except (ValueError, TypeError):
        raise LoginError("id_token_invalid") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise LoginError("id_token_invalid")
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise LoginError("id_token_invalid")
    jwk = _jwk(header["kid"])
    if jwk is None or not _verify_rs256(f"{h64}.{p64}".encode("ascii"), signature, jwk):
        raise LoginError("id_token_invalid")
    now = time.time() if now_s is None else now_s
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    exp = claims.get("exp")
    ok = (
        claims.get("iss") == ISSUER
        and client_id in auds
        and isinstance(exp, (int, float))
        and now <= exp + CLOCK_SKEW_S
        and isinstance(claims.get("nonce"), str)
        and hmac.compare_digest(claims["nonce"], nonce)
        and isinstance(claims.get("sub"), str)
        and bool(claims["sub"])
    )
    if not ok:
        raise LoginError("id_token_invalid")
    return claims


# --------------------------------------------------------------------------- 令牌响应
def _expiry_ms(value, now_ms: int) -> int | None:
    """`expires_in` / `earliest_refresh_at`：< 1e9 当相对秒，否则绝对秒（官方未定义后者的单位，保守处理）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(now_ms + value * 1000) if value < 1_000_000_000 else int(value * 1000)


def _token_set(
    body: dict, now_ms: int, previous_scopes: frozenset[str] = frozenset()
) -> siwc_auth.TokenSet | None:
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        return None
    expires = _expiry_ms(body.get("expires_in"), now_ms)
    if expires is None:
        return None
    scope = body.get("scope")
    scopes = (
        frozenset(scope.split()) if isinstance(scope, str) and scope.strip() else previous_scopes
    )
    return siwc_auth.TokenSet(
        access=access,
        refresh=refresh,
        expires_at_ms=expires,
        scopes=scopes,
        earliest_refresh_at_ms=_expiry_ms(body.get("earliest_refresh_at"), now_ms),
    )


def refresh_tokens(acc: siwc_store.Account, current: siwc_auth.TokenSet) -> siwc_auth.TokenSet:
    """`StoreProvider` 的刷新函数：已签发 client_id + 刷新令牌 + resource；省略 scope（保持原授权）。"""
    reply = _post_form(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "client_id": acc.client_id,
            "refresh_token": current.refresh,
            "resource": RESOURCE,
        },
    )
    if reply.status == 0:
        raise siwc_auth.RefreshError("network")
    if reply.status >= 500:
        raise siwc_auth.RefreshError("server")
    if reply.status != 200:
        raise siwc_auth.RefreshError(_oauth_error(reply.body) or "server")
    ts = _token_set(reply.body, siwc_auth._now_ms(), current.scopes)
    if ts is None:
        raise siwc_auth.RefreshError("server")
    return ts


_PROVIDER: siwc_store.StoreProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def provider() -> siwc_auth.TokenProvider:
    """真正的令牌提供方：测试换上的 > 开关关 = 恒未登录 > 落盘账号 + 联网刷新。

    **进程内只有一个** `StoreProvider`：`mark_rejected` 记下的「下次先刷新」与进程内刷新锁都在实例上，每次新建就丢了。"""
    global _PROVIDER
    if siwc_auth._OVERRIDE is not None:
        return siwc_auth._OVERRIDE
    if not siwc_wire.enabled():
        return siwc_auth.DisabledProvider()
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = siwc_store.StoreProvider(refresh_tokens)
        return _PROVIDER


# --------------------------------------------------------------------------- 型号清单
def fetch_models(access: str) -> list[dict] | None:
    """`GET /v1/models` → `[{"id": slug, "display_name"?}]`（只要 `visibility == "list"`，服务端顺序）；拿不到 → None。"""
    reply = _get_json(MODELS_URL, access, MODELS_BODY_LIMIT)
    if reply.status != 200:
        return None
    out: list[dict] = []
    for m in reply.body.get("models") or []:
        if not isinstance(m, dict) or m.get("visibility", "list") != "list":
            continue
        slug = m.get("slug") or m.get("id")
        if (
            isinstance(slug, str)
            and slug
            and len(slug) <= 200
            and all(x["id"] != slug for x in out)
        ):
            item = {"id": slug}
            if isinstance(m.get("display_name"), str) and m["display_name"].strip():
                item["display_name"] = m["display_name"].strip()[:200]
            out.append(item)
    return out


def refresh_models() -> dict:
    """连接管理里「刷新型号」：用当前账号的令牌重取一次清单并缓存。→ `public_status()`。"""
    if not siwc_wire.enabled():
        raise LoginError("disabled")
    try:
        access = provider().access_token(min_valid_s=60)
    except siwc_auth.NeedsLogin:
        raise LoginError("needs_login") from None
    except siwc_auth.TokenUnavailable:
        raise LoginError("network") from None
    models = fetch_models(access)
    if models is None:
        raise LoginError("network")
    with siwc_store.refresh_lock():
        acc = siwc_store.active()
        if acc is not None:
            siwc_store.save(replace(acc, models=tuple(models)))
    return siwc_store.public_status()


# --------------------------------------------------------------------------- 流程
@dataclass
class _Flow:
    flow_id: str
    authorize_url: str = ""
    state: str = WAITING
    reason: str | None = None
    started_s: float = field(default_factory=time.monotonic)
    # --- 私有（public_flow 不回传） ---
    oauth_state: str = field(default="", repr=False)
    nonce: str = field(default="", repr=False)
    verifier: str = field(default="", repr=False)
    redirect_uri: str = ""
    client_id: str = ""  # 再次登录：所选账号的已签发 id；首次注册：空
    subject: str = ""  # 再次登录：所选账号的 sub
    cancel: threading.Event = field(default_factory=threading.Event)
    server: http.server.HTTPServer | None = field(default=None, repr=False)


_LOCK = threading.RLock()
_FLOWS: dict[str, _Flow] = {}


def public_flow(flow: _Flow) -> dict:
    out = {"flow_id": flow.flow_id, "state": flow.state, "authorize_url": flow.authorize_url}
    if flow.reason:
        out["reason"] = flow.reason
    if flow.state in (SIGNED_IN, PLAN_DISABLED):
        out["account"] = siwc_store.public_status()
    return out


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _bind() -> http.server.HTTPServer:
    """先 1455，被占用换系统分配的空闲端口（官方：只有端口可以变）。"""
    for port in (PREFERRED_PORT, 0):
        try:
            srv = _CallbackServer(("127.0.0.1", port), _CallbackHandler)
        except OSError:
            continue
        return srv
    raise LoginError("port_unavailable")


class _CallbackServer(http.server.HTTPServer):
    allow_reuse_address = False
    flow: _Flow | None = None
    result: dict | None = None
    got: threading.Event

    def server_bind(self):
        # Windows 上 SO_EXCLUSIVEADDRUSE：别的进程不能抢同一端口听我们的回调
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


#: 回调页的文案：一个标题 + 一句话，按浏览器语言只给一种（不中英并排）
_PAGE_TEXT = {
    "zh": {
        True: ("已连接 ChatGPT", "可以关掉这个页面，回到 {p} 了。"),
        False: ("未能连接 ChatGPT", "回到 {p} 再试一次。"),
    },
    "en": {
        True: ("Connected to ChatGPT", "You can close this tab and go back to {p}."),
        False: ("Couldn’t connect to ChatGPT", "Go back to {p} and try again."),
    },
}


def _page_lang(accept_language: str) -> str:
    """`Accept-Language` 里第一个认得的语言（zh / en），都不认得给 en。"""
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("zh"):
            return "zh"
        if tag.startswith("en"):
            return "en"
    return "en"


def done_page(ok: bool, accept_language: str = "") -> bytes:
    """浏览器里登录完成后看到的那一页：品牌标志 + 一个标题 + 一句话，浅 / 深色跟随系统。
    纯静态（无脚本、无外链资源），标志路径与配色取自生成的 `brand_mark`（唯一出处 scripts/build_brand_assets.py）。"""
    import html

    from . import brand_mark

    lang = _page_lang(accept_language)
    title, line = _PAGE_TEXT[lang][ok]
    p = brand.PRODUCT_NAME
    light, dark = brand_mark.PALETTES["surface"], brand_mark.PALETTES["reverse"]
    paths = "".join(
        f'<path class="{role}" d="{d}" fill-rule="evenodd"/>' for role, d in brand_mark.PATHS
    )
    box = f"{brand_mark.BOX:g}"
    # 标签页图标：同一个标志（浅底配色），内联免得向回调端口多要一次 /favicon.ico
    icon_paths = "".join(
        f"<path d='{d}' fill='{light[role]}' fill-rule='evenodd'/>" for role, d in brand_mark.PATHS
    )
    favicon = "data:image/svg+xml," + urllib.parse.quote(
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {box} {box}'>{icon_paths}</svg>"
    )
    # 失败：标志退成灰（不加色块、不加红字），一眼和成功页分开（agy 审稿 2026-11-25）
    muted_mark = "" if ok else " .ink { fill: var(--muted); }"
    return f"""<!doctype html>
<html lang="{"zh-CN" if lang == "zh" else "en"}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" type="image/svg+xml" href="{favicon}">
<title>{html.escape(title)} · {html.escape(p)}</title>
<style>
  :root {{ --bg: #f7f6f2; --ink: {light["ink"]}; --piece: {light["piece"]}; --muted: #6b6a63; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1b1b18; --ink: {dark["ink"]}; --piece: {dark["piece"]}; --muted: #a3a299; }}
  }}
  body {{
    margin: 0; min-height: 100dvh; box-sizing: border-box;
    padding: 32px 24px clamp(48px, 10vh, 96px);
    display: flex; align-items: center; justify-content: center;
    background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  main {{ text-align: center; }}
  svg {{ width: 44px; height: 44px; display: block; margin: 0 auto 20px; }}
  .ink {{ fill: var(--ink); }} .piece {{ fill: var(--piece); }}{muted_mark}
  h1 {{ font-size: 20px; font-weight: 500; line-height: 1.35; letter-spacing: 0.01em; margin: 0 0 8px; }}
  :lang(en) h1 {{ letter-spacing: -0.015em; }}
  p {{ font-size: 14px; line-height: 1.65; color: var(--muted); margin: 0; }}
  :lang(zh-CN) p {{ padding-left: 0.45em; }}
</style>
</head>
<body>
<main>
  <svg viewBox="0 0 {box} {box}" role="img" aria-label="{html.escape(p)}">{paths}</svg>
  <h1>{html.escape(title)}</h1>
  <p>{html.escape(line.format(p=p))}</p>
</main>
</body>
</html>
""".encode("utf-8")


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def log_message(self, *args):  # 查询串里有 code：一个字都不写进日志
        pass

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlsplit(self.path)
        flow = self.server.flow
        if u.path != CALLBACK_PATH or flow is None:
            self.send_error(404)
            return
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        got_state = q.get("state", "")
        if not got_state or not hmac.compare_digest(got_state, flow.oauth_state):
            self.send_error(400)  # 不是这次登录的回调：不收、也不结束流程
            return
        ok = "error" not in q and bool(q.get("code"))
        body = done_page(ok, self.headers.get("Accept-Language", ""))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if self.server.result is None:
            self.server.result = q
            self.server.got.set()


def start(*, new_account: bool = False) -> dict:
    """发起一次登录 → `public_flow`（带 `authorize_url`，前端用系统浏览器打开）。同时只有一个流程：新的作废旧的。

    不是 `new_account` 且有记录（当前账号；退出过就取最近保存的那份）→ 再次登录同一账号（存下的 client_id + `id_token_hint`，
    官方：退出不新建 client、下次登录复用）；否则首次注册。"""
    if not siwc_wire.enabled():
        raise LoginError("disabled")
    with _LOCK:
        for old in list(_FLOWS.values()):
            if old.state in (WAITING, EXCHANGING):
                _finish(old, CANCELLED)
    acc = None
    if not new_account:
        acc = siwc_store.active() or max(
            siwc_store.accounts(), key=lambda a: a.saved_at, default=None
        )
    flow = _Flow(flow_id=uuid.uuid4().hex)
    flow.oauth_state = _b64url(secrets.token_bytes(24))
    flow.nonce = _b64url(secrets.token_bytes(24))
    flow.verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(flow.verifier.encode("ascii")).digest())
    srv = _bind()
    srv.flow, srv.result, srv.got = flow, None, threading.Event()
    flow.server = srv
    flow.redirect_uri = f"http://127.0.0.1:{srv.server_address[1]}{CALLBACK_PATH}"
    params = {
        "response_type": "code",
        "redirect_uri": flow.redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": flow.oauth_state,
        "nonce": flow.nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "ext_agent_host_id": siwc_store.host_id(),
    }
    if acc is not None:
        flow.client_id, flow.subject = acc.client_id, acc.subject
        params["client_id"] = acc.client_id
        if acc.id_token:
            params["id_token_hint"] = acc.id_token
        if acc.email:
            params["login_hint"] = acc.email
    else:
        params["client_id"] = siwc_store.DYNAMIC_CLIENT_ID
        params["agent_name_hint"] = brand.PRODUCT_NAME
    flow.authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    with _LOCK:
        _FLOWS[flow.flow_id] = flow
        _prune()
    # 先起监听、再交出地址（官方：start the listener before opening the browser）
    threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.2}, name="siwc-callback", daemon=True
    ).start()
    threading.Thread(target=_wait, args=(flow,), name="siwc-login", daemon=True).start()
    LOG.info("SIWC 登录流程开始（%s）", "再次登录" if acc else "首次注册")
    return public_flow(flow)


def _prune() -> None:
    done = [f for f in _FLOWS.values() if f.state in _TERMINAL]
    for f in done[:-FLOW_KEEP]:
        _FLOWS.pop(f.flow_id, None)


def _close(flow: _Flow) -> None:
    srv, flow.server = flow.server, None
    if srv is not None:
        threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()


def _finish(flow: _Flow, state: str, reason: str | None = None) -> None:
    with _LOCK:
        if flow.state in _TERMINAL:
            return
        flow.state, flow.reason = state, reason
        flow.cancel.set()
        flow.verifier = flow.nonce = flow.oauth_state = ""
    _close(flow)
    LOG.info("SIWC 登录流程结束：%s%s", state, f"（{reason}）" if reason else "")


def _wait(flow: _Flow) -> None:
    srv = flow.server
    if srv is None:
        return
    deadline = time.monotonic() + FLOW_TIMEOUT_S
    while not srv.got.wait(0.5):
        if flow.cancel.is_set():
            return
        if time.monotonic() > deadline:
            _finish(flow, EXPIRED)
            return
    q = srv.result or {}
    _close(flow)
    with _LOCK:
        if flow.state != WAITING:
            return
        flow.state = EXCHANGING
    try:
        _complete(flow, q)
    except LoginError as exc:
        _finish(flow, FAILED, exc.code if exc.code in REASONS else "exchange_failed")
    except siwc_auth.TokenUnavailable:
        _finish(flow, FAILED, "store_failed")
    except Exception as exc:  # noqa: BLE001 - 后台线程：任何意外都落成一个闭集结论，不留在 exchanging
        LOG.warning("SIWC 登录意外失败（%s）", type(exc).__name__)
        _finish(flow, FAILED, "exchange_failed")


def _complete(flow: _Flow, q: dict) -> None:
    if q.get("error"):
        _finish(flow, DENIED)  # access_denied 等：不换令牌
        return
    issued = q.get("client_id") or ""
    if flow.client_id:
        if issued and issued != flow.client_id:
            raise LoginError("client_mismatch")
        client_id = flow.client_id
    else:
        if not siwc_store.valid_client_id(issued):
            raise LoginError("registration_incomplete")
        client_id = issued
    reply = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": q.get("code", ""),
            "code_verifier": flow.verifier,
            "redirect_uri": flow.redirect_uri,
            "resource": RESOURCE,
        },
    )
    if reply.status == 0:
        raise LoginError("network")
    if reply.status != 200:
        raise LoginError("exchange_failed")
    now_ms = siwc_auth._now_ms()
    ts = _token_set(reply.body, now_ms)
    id_token = reply.body.get("id_token")
    if ts is None or not isinstance(id_token, str):
        raise LoginError("exchange_failed")
    claims = verify_id_token(id_token, client_id=client_id, nonce=flow.nonce)
    sub = claims["sub"]
    if flow.subject and sub != flow.subject:
        raise LoginError("account_mismatch")
    if flow.cancel.is_set():
        return
    plan = siwc_auth.REQUIRED_SCOPE in ts.scopes
    models = fetch_models(ts.access) if plan else None
    old = siwc_store.load(client_id)
    acc = siwc_store.Account(
        client_id=client_id,
        subject=sub,
        email=claims.get("email") if isinstance(claims.get("email"), str) else "",
        ext_agent_host_id=siwc_store.host_id(),
        id_token=id_token,
        models=tuple(models) if models is not None else (old.models if old else ()),
    ).with_tokens(ts)
    # 先拿刷新锁（可能要等另一个进程刷新完）、再拿流程锁：反过来会让等锁期间的状态轮询一起卡住
    try:
        with siwc_store.refresh_lock(), _LOCK:
            if flow.cancel.is_set() or flow.state != EXCHANGING:
                return  # 用户在换令牌途中点了取消：不落盘
            siwc_store.save(acc)
            siwc_store.set_active(client_id)
    except (OSError, ValueError):
        raise LoginError("store_failed") from None
    _finish(flow, SIGNED_IN if plan else PLAN_DISABLED)


def status(flow_id: str) -> dict | None:
    with _LOCK:
        flow = _FLOWS.get(flow_id)
        return public_flow(flow) if flow else None


def cancel(flow_id: str) -> dict | None:
    with _LOCK:
        flow = _FLOWS.get(flow_id)
        if flow is None:
            return None
        _finish(flow, CANCELLED)
        return public_flow(flow)


def logout() -> dict:
    """退出当前账号：撤销刷新令牌，清本机令牌（保留 client_id / host id）。→ `{"revoked": bool}`。"""
    with siwc_store.refresh_lock():
        acc = siwc_store.active()
        if acc is None:
            return {"revoked": True}
        revoked = True
        if acc.refresh_token:
            revoked = False
            for attempt in range(3):
                reply = _post_form(
                    REVOKE_URL,
                    {
                        "token": acc.refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": acc.client_id,
                    },
                )
                if reply.status == 200:
                    revoked = True
                    break
                if reply.status != 0 and reply.status < 500:
                    break  # 4xx：不再重试（令牌已无效时服务端也回 200）
                time.sleep(0.5 * (attempt + 1))
        siwc_store.save(acc.signed_out())
        siwc_store.set_active(None)
    LOG.info("SIWC 已退出登录（远端撤销%s）", "已确认" if revoked else "未确认")
    return {"revoked": revoked}


def reset_for_tests() -> None:
    global _PROVIDER
    with _PROVIDER_LOCK:
        _PROVIDER = None
    with _LOCK:
        for f in list(_FLOWS.values()):
            _finish(f, CANCELLED)
        _FLOWS.clear()
    with _JWKS_LOCK:
        _JWKS.clear()


__all__ = [
    "LoginError",
    "cancel",
    "fetch_models",
    "logout",
    "provider",
    "refresh_models",
    "refresh_tokens",
    "reset_for_tests",
    "start",
    "status",
    "verify_id_token",
]
