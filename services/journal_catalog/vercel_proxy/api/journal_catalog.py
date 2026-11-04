"""海外入口：`https://tavotto.com/v1/journal-catalog/*` → 中国站后端（Vercel Python 函数，纯标准库，WSGI）。

**一个后端、两个入口，计数只有一份**：这里不存任何状态、不数任何东西，只把请求原样转给中国站上的
`services.journal_catalog`（sqlite 计数在那边）。它做三件事：

1. **只转白名单路径**：`index.signed.json`、`device`、`journals/<id>`、`snapshots/<sha256>.webp`；别的一律 404。
   方法只许 GET / POST。
2. **带上真实客户端 IP 与共享密钥**：Vercel 会覆盖进来的 `X-Forwarded-For`，所以它的第一段（或 `X-Real-IP`）
   是真实客户端地址；转发时放进 `X-Tavotto-Client-IP`，并附 `X-Tavotto-Proxy-Secret`（环境变量
   `TAVOTTO_JC_PROXY_SECRET`，不进仓库）。后端只在密钥对得上时才采信这个 IP，否则按连接 IP 算——
   所以直连后端的人伪造这个头无效。客户端自己带来的这两个头会被**丢掉**，不转发。
3. **只缓存签名索引**：`index.signed.json` 回 `public, s-maxage=300`；令牌签发、刊包、快照**每次回源**
   （首次读取的计数与令牌校验都在源站，刊包若被边缘缓存就绕过了计数）。

路径怎么到这里：`vercel.json` 的 rewrite 把 `/v1/journal-catalog/:path*` 改写到本函数并带上 `?p=:path*`
（Vercel 的内部 rewrite 交给函数的是改写后的路径，见 `services/telemetry_proxy/README.md` 的教训，所以**不靠路径、
靠 `p` 参数**）。原请求的查询串（如 `rev=5`）随后原样带上。部署后的第一件事是在 staging 上验这一步（DEPLOYMENT.md）。

看护：`tests/test_journal_catalog_service.py` 里的反代用例（本模块经文件路径加载）。
"""

from __future__ import annotations

import ipaddress
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from http import HTTPStatus

ORIGIN_ENV = "TAVOTTO_JC_ORIGIN"
SECRET_ENV = "TAVOTTO_JC_PROXY_SECRET"
DEFAULT_ORIGIN = "https://tavotto.cn"
UPSTREAM_PREFIX = "/v1/journal-catalog/"
TIMEOUT_S = 10
MAX_BODY = 8 << 20

_ALLOWED = re.compile(
    r"^(?:index\.signed\.json|device|journals/[a-z0-9][a-z0-9_.-]{0,95}|snapshots/[0-9a-f]{64}\.webp)$"
)
#: 只转这些请求头（别的、尤其是客户端自带的 X-Tavotto-Proxy-Secret / X-Tavotto-Client-IP 一律丢弃）
_FORWARD_HEADERS = {
    "HTTP_X_TAVOTTO_DEVICE": "X-Tavotto-Device",
    "HTTP_X_TAVOTTO_CLIENT": "X-Tavotto-Client",
    "HTTP_USER_AGENT": "User-Agent",
    "HTTP_ACCEPT": "Accept",
}
_BACK_HEADERS = ("Content-Type", "Retry-After")

Fetcher = Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]


def _fetch(method: str, url: str, headers: dict[str, str], body: bytes | None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — 目标由配置固定为源站
            return resp.status, dict(resp.headers.items()), resp.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read(MAX_BODY + 1)


def client_ip(environ: dict) -> str | None:
    """Vercel 覆盖 `X-Forwarded-For`：第一段是真实客户端；没有就用 `X-Real-IP`。不是合法地址返回 None。"""
    xff = (environ.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    for cand in (xff, (environ.get("HTTP_X_REAL_IP") or "").strip()):
        try:
            ipaddress.ip_address(cand)
            return cand
        except ValueError:
            continue
    return None


def _reply(status: int, body: bytes = b"", headers: list[tuple[str, str]] | None = None):
    return (
        status,
        headers or [("Content-Type", "application/json"), ("Cache-Control", "no-store")],
        body,
    )


def proxy(environ: dict, env: dict[str, str] | None = None, fetch: Fetcher | None = None):
    """纯函数：WSGI environ → (状态码, 头, 体)。`env` / `fetch` 供测试注入。"""
    env = os.environ if env is None else env
    fetch = fetch or _fetch
    secret = env.get(SECRET_ENV, "")
    if not secret:
        return _reply(503, b'{"code":"proxy_unconfigured"}')
    method = environ.get("REQUEST_METHOD", "GET")
    if method not in ("GET", "POST"):
        return _reply(405, b'{"code":"method_not_allowed"}')
    query = urllib.parse.parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    path = next((v for k, v in query if k == "p"), "")
    if not _ALLOWED.fullmatch(path):
        return _reply(404, b'{"code":"not_found"}')
    rest = urllib.parse.urlencode([(k, v) for k, v in query if k != "p"])
    origin = (env.get(ORIGIN_ENV) or DEFAULT_ORIGIN).rstrip("/")
    url = f"{origin}{UPSTREAM_PREFIX}{path}" + (f"?{rest}" if rest else "")
    headers = {dst: environ[src] for src, dst in _FORWARD_HEADERS.items() if environ.get(src)}
    headers["X-Tavotto-Proxy-Secret"] = secret
    ip = client_ip(environ)
    if ip:
        headers["X-Tavotto-Client-IP"] = ip
    body = None
    if method == "POST":
        try:
            n = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        body = environ["wsgi.input"].read(min(max(n, 0), 4096)) if n else b""
    try:
        status, up_headers, data = fetch(method, url, headers, body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return _reply(502, b'{"code":"origin_unreachable"}')
    if len(data) > MAX_BODY:
        return _reply(502, b'{"code":"origin_too_large"}')
    lower = {k.lower(): v for k, v in up_headers.items()}
    out = [(h, lower[h.lower()]) for h in _BACK_HEADERS if h.lower() in lower]
    if status == 200 and path == "index.signed.json":
        out.append(("Cache-Control", "public, max-age=300, s-maxage=300"))
    else:
        out.append(("Cache-Control", "private, no-store"))
    return status, out, data


def application(environ, start_response):
    status, headers, body = proxy(environ)
    start_response(
        f"{status} {HTTPStatus(status).phrase}", [*headers, ("Content-Length", str(len(body)))]
    )
    return [body]


app = application  # Vercel 的 Python 运行时认 `app`
