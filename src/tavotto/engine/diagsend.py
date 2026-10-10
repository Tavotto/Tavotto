"""把诊断包发给 Tavotto 维护者（国内诊断接收服务 `diagnostics-api` 的**客户端**，ADR 0118）。

这是「诊断包不自动上传」规则的**唯一例外通道**，它的形状就是为了把例外关在最小的笼子里：

* **逐次授权**。上传只由一次用户点击（`start(confirmed=True)`）触发；没有「记住我的选择」、没有自动重试到
  下一次启动、没有后台补发。取消、关窗、进程退出之后什么都不会自己继续——状态只在内存里，不落盘。
  遥测同意（`engine/telemetry.py`）不推出诊断同意，反过来也不。
* **先看再发**。`prepare()` 只在本机生成并保管那一份 ZIP（零网络），把「会发送哪几类内容」交给界面；用户能把
  **同一份字节**存成文件。`start()` 发的就是这份字节（SHA-256 与 `prepare` 时一致），不是点发送那一刻重新采的。
* **本机引擎进程直传**。WebView 不碰网络：上传由本模块在后台线程里做——`POST /v1/reports/init` → 按服务端给的
  `upload{url,fields,file_field}` 把 ZIP 以 multipart 流式 POST 给 COS（不经 Tavotto VPS）→ `POST /v1/reports/complete`。
  契约出处：`Tavotto/infra` 仓库 diagnostics-api 文档目录里的 openapi.json（v1.0.0）与同目录说明文档的接入要求一节。
* **默认关闭**。`enabled()` 要同时满足：`TAVOTTO_DIAG_UPLOAD=1`、没有硬开关 `TAVOTTO_NO_DIAG_UPLOAD=1`、
  `TAVOTTO_DIAG_ENDPOINT` 指向**白名单主机**。生产主机白名单 `ENDPOINT_HOSTS` 在 G-DIAG（真实 COS C1–C16 与
  运维门禁）通过、维护者批准之前是**空集**——发行版里没有任何办法打开它，界面也就不会出现入口。
  开发/验收用本机回环：另设 `TAVOTTO_DIAG_DEV_LOOPBACK=1` 才放行 `127.0.0.1`（仍是 HTTPS，证书照样校验）。
* **目的地由代码钉死，不由响应决定**（SSRF）：端点主机 ∈ 白名单；`upload.url` 必须是 `https://<桶>.cos.<区域>.myqcloud.com/`
  （回环开发模式下才允许本端点的 `/v1/local-upload`），不许带用户信息 / 查询串 / 端口 / IP 字面量；**不跟随任何重定向**
  （含 307/308——POST 体不会被转交到别处）；字段名只许是 token 字符（不能夹带换行或引号来拆 multipart）。
  已知的盲点：不做解析后 IP 的内网判定——TUN / fake-ip 代理下合法域名会解析到 198.18.0.0/15，判了反而误杀；
  防线是「主机名白名单 + 证书校验 + 不跟随重定向」。
* **证书**只走 `tlstrust.client_context()`（平台原生校验）；代理沿用 `build_opener` 按环境变量现建的那一套。
* **秘密不落地**：`complete_token`、上传 URL / 表单字段、用户填的说明文字只在内存里的 `SendSession` 私有字段中；
  状态对象（`public()`）、日志、诊断包、遥测里都没有它们。日志模板是字面量，参数只有闭集里的阶段名与结果码；
  异常文本不进日志（里面可能有 URL）。
* **不裁剪**。包超过上限（默认 10 MiB，以服务端 `upload.max_bytes` 为准）就告诉用户留在本地，不删内容凑大小。

纯标准库，Flask 父进程 import 链上的模块。线程：每次发送一个守护线程；同一时刻只有一个会话。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field

from . import logsafe, telemetry, tlstrust

LOG = logging.getLogger("tavotto.diagsend")

# ---------------------------------------------------------------------------
# 开关与目的地
# ---------------------------------------------------------------------------
ENV_ENABLE = "TAVOTTO_DIAG_UPLOAD"
ENV_HARD_OFF = "TAVOTTO_NO_DIAG_UPLOAD"
ENV_ENDPOINT = "TAVOTTO_DIAG_ENDPOINT"
ENV_DEV_LOOPBACK = "TAVOTTO_DIAG_DEV_LOOPBACK"

#: 生产端点的主机白名单。**空集 = 发行版里打不开**；G-DIAG 通过、维护者批准后由专门的 PR 填上真实主机名。
ENDPOINT_HOSTS: frozenset[str] = frozenset()
#: 开发回环模式放行的主机（只有这一个字面量；`localhost` 会解析到 IPv6，证书也没签它）。
LOOPBACK_HOST = "127.0.0.1"
LOCAL_UPLOAD_PATH = "/v1/local-upload"

#: COS 桶的公网域名：`<名字>-<APPID>.cos.<区域>.myqcloud.com`（服务端 `storage/cos.py` 同一个拼法）。
_COS_HOST = re.compile(
    r"^[a-z0-9][a-z0-9-]{0,62}-[0-9]{5,12}\.cos\.[a-z0-9-]{3,32}\.myqcloud\.com$"
)
_FIELD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REPORT_ID = re.compile(r"^TVD-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_OK = re.compile(r"^[0-9A-Za-z.+_-]{1,32}$")

# ---------------------------------------------------------------------------
# 契约常量（逐字取自 openapi.json v1.0.0；改动先对 infra 的 versioning.md）
# ---------------------------------------------------------------------------
API_MAJOR = 1
API_VERSION_HEADER = "Tavotto-Diagnostics-Api"
#: `InitBody.category` 的闭集（界面下拉与之逐项对应）。
CATEGORIES = (
    "render_failure",
    "export_failure",
    "environment",
    "editing",
    "install_update",
    "performance",
    "crash",
    "other",
)
DEFAULT_CATEGORY = "other"
#: 默认上限 10 MiB（服务端 `DIAG_MAX_REPORT_BYTES`；以 `upload.max_bytes` 为准，init 之后再核一次）。
MAX_BYTES = 10 * 1024 * 1024
#: 说明文字上限：服务端默认 `note_max_chars` = 1000（openapi 只写了 4000 的硬顶；取严的）。
NOTE_MAX_CHARS = 1000
RETENTION_DAYS = 30

#: 诊断包里的文件 → 给用户看的「内容类别」（闭集 id；界面按 id 翻译）。没登记的文件是 `other`。
ENTRY_KINDS = {
    "report.json": "environment",
    "app.log": "log",
    "config.json": "settings",
    "frontend-state.json": "ui_state",
    "interaction-trace.jsonl": "ui_trace",
    "task-diagnostics.json": "task",
    "manifest.json": "manifest",
    "README.txt": "readme",
}
MAX_BUNDLE_ENTRIES = 64

# ---------------------------------------------------------------------------
# 超时与重试（同一次授权之内的有界重试；超出一律交还用户去点）
# ---------------------------------------------------------------------------
API_TIMEOUT_S = 15
UPLOAD_TIMEOUT_S = 60
#: Caddy 对 /v1/reports/* 的 response_header_timeout 是 60 s（complete 要从存储下载并校验）。
COMPLETE_TIMEOUT_S = 70
CANCEL_TIMEOUT_S = 5
#: complete 丢了响应 / 对端 in_progress / 暂未看到对象时，同一次发送内最多尝试几次（含第一次）。
COMPLETE_ATTEMPTS = 4
COMPLETE_BACKOFF_S = (1.0, 2.0, 4.0)
RETRY_AFTER_CAP_S = 10
#: 没被动用的会话多久后自动丢弃（内存里的 ≤10 MiB）。
PREPARED_TTL_S = 30 * 60
MAX_RESPONSE_BYTES = 64 * 1024
MAX_UPLOAD_FIELDS = 32
MAX_FIELD_VALUE = 4096

#: 阶段（日志里明文的闭集）。
STAGES = frozenset({"init", "upload", "complete", "cancel"})

#: 失败码（对界面稳定的闭集；日志里明文）。值 = 能不能由用户再点一次「发送」。
#: 注意：这是**客户端**的码，由 (HTTP 状态, 服务端 code, 阶段) 归并而来，不是服务端 code 的镜像。
FAILURES: dict[str, bool] = {
    "network": True,  # 连不上 / 断线
    "timeout": True,
    "tls": True,  # 证书校验失败（平台信任库不认）
    "bad_response": True,  # 响应不是契约里的形状
    "api_incompatible": False,  # 服务端主版本不是 v1
    "rate_limited": True,  # 429 rate_limited
    "daily_quota_exceeded": True,  # 429 daily_quota_exceeded
    "uploads_disabled": True,  # 503 uploads_disabled（运维关了入口）
    "storage_unavailable": True,  # 503 storage_unavailable / storage_error / busy
    "too_large": False,  # 413（含本机预判）：留在本地
    "expired": True,  # 410：重新开始（新的 client_request_id）
    "session_closed": True,  # 409 session_closed / cancelled / idempotency_conflict：重新开始
    "notes_disabled": True,  # 422：去掉说明文字再发
    "rejected": False,  # 422 rejected：服务端拒收这份包
    "client_error": False,  # 400 / 415 / 422 bad_request 等：程序错误，不重试
    "not_found": True,  # 404：令牌/编号对不上，重新开始
    "upload_denied": True,  # COS 403：授权过期或策略不符，重新 init 能拿到新的
    "upload_rejected": False,  # COS 400 / 413：表单被拒
    "upload_unavailable": True,  # COS 5xx
    "upload_missing": True,  # complete 看不到对象：重新上传
    "upload_url_rejected": False,  # 服务端给的上传地址不在白名单：不上传
    "redirect_blocked": False,  # 对端想重定向：不跟
    "digest_mismatch": False,  # 服务端核实的摘要与本机不一致
    "unexpected_response": True,
    "internal": False,
}
#: 日志里「结果」位明文放行的闭集（`logsafe.known` 要求模块常量）。
LOG_RESULTS = frozenset(FAILURES) | {"ok", "cancelled", "unknown"}
#: 失败后需要换一个新的 `client_request_id` 才能再发（服务端会话已关 / 过期 / 令牌对不上）。
REOPEN_ON = frozenset({"expired", "session_closed", "not_found"})


class SendError(Exception):
    """本模块对 Flask 层抛的错：`code` 稳定、`status` 是本机 HTTP 状态。"""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _Failure(Exception):
    """发送线程里的一次失败（已归并成 `FAILURES` 的码）。"""

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


class _Cancelled(Exception):
    """用户取消：从上传的读回调里抛出，打断正在进行的 POST。"""


# ---------------------------------------------------------------------------
# 开关
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    """已通过白名单的服务端点。`host` 是小写主机名，`port` 是 443 或（仅回环开发）显式端口。"""

    host: str
    port: int
    loopback: bool

    @property
    def base(self) -> str:
        return f"https://{self.host}" if self.port == 443 else f"https://{self.host}:{self.port}"


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def parse_endpoint(raw: str, *, dev_loopback: bool) -> Target | None:
    """`TAVOTTO_DIAG_ENDPOINT` → `Target`；任何一处不合规一律 None（不抛、不猜、不修正）。"""
    try:
        parts = urllib.parse.urlsplit((raw or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port  # 非法端口在这里抛 ValueError
    except ValueError:
        return None
    if parts.scheme != "https" or not host:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    if host in ENDPOINT_HOSTS:
        return Target(host, 443, False) if port in (None, 443) else None
    if dev_loopback and host == LOOPBACK_HOST and port:
        return Target(host, port, True)
    return None


def resolve_target() -> Target | None:
    """此刻允许上传的目的地；任何一个条件不满足都是 None（= 功能关闭）。"""
    if _flag(ENV_HARD_OFF) or not _flag(ENV_ENABLE):
        return None
    return parse_endpoint(os.environ.get(ENV_ENDPOINT, ""), dev_loopback=_flag(ENV_DEV_LOOPBACK))


def enabled() -> bool:
    return resolve_target() is not None


def capability() -> dict:
    """给界面的能力说明。**不含**端点地址（界面不需要，也不该知道）。"""
    if resolve_target() is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "max_bytes": MAX_BYTES,
        "note_max_chars": NOTE_MAX_CHARS,
        "retention_days": RETENTION_DAYS,
        "categories": list(CATEGORIES),
    }


# ---------------------------------------------------------------------------
# 上传目的地校验（SSRF）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UploadSpec:
    url: str
    fields: tuple[tuple[str, str], ...]
    file_field: str
    max_bytes: int
    expires_at: int

    def __repr__(self) -> str:  # 表单里有签名与临时密钥：任何误打印都只见形状
        return f"UploadSpec(fields={len(self.fields)}, max_bytes={self.max_bytes})"


def validate_upload_url(url: object, target: Target) -> str:
    """服务端 `upload.url` → 规范化后的 URL；不在白名单就抛 `_Failure("upload_url_rejected")`。"""
    bad = _Failure("upload_url_rejected")
    if not isinstance(url, str) or not url or len(url) > 512 or not url.isascii():
        raise bad
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise bad from None
    if parts.scheme != "https" or not host or parts.username is not None or "@" in parts.netloc:
        raise bad
    if parts.query or parts.fragment or any(c in url for c in "\\\r\n\t "):
        raise bad
    if target.loopback:
        # 本地适配器：只许回到本端点的 /v1/local-upload
        if host == target.host and (port or 443) == target.port and parts.path == LOCAL_UPLOAD_PATH:
            return url
        raise bad
    if _COS_HOST.match(host) and port in (None, 443) and parts.path in ("", "/"):
        return f"https://{host}/"
    raise bad


def parse_upload_spec(raw: object, target: Target, size: int) -> UploadSpec:
    """`InitResponse.upload` → `UploadSpec`。形状不对 → `bad_response`；地址不对 → `upload_url_rejected`。"""
    if not isinstance(raw, dict):
        raise _Failure("bad_response")
    if raw.get("method") != "POST" or raw.get("content_type") != "application/zip":
        raise _Failure("bad_response")
    url = validate_upload_url(raw.get("url"), target)
    fields_raw = raw.get("fields")
    file_field = raw.get("file_field")
    max_bytes = raw.get("max_bytes")
    expires_at = raw.get("expires_at")
    if (
        not isinstance(fields_raw, dict)
        or len(fields_raw) > MAX_UPLOAD_FIELDS
        or not isinstance(file_field, str)
        or not _FIELD_NAME.match(file_field)
        or file_field in fields_raw
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
    ):
        raise _Failure("bad_response")
    fields: list[tuple[str, str]] = []
    for key, value in fields_raw.items():  # 保持服务端给的顺序（JSON 对象在 Python 里有序）
        if not isinstance(key, str) or not _FIELD_NAME.match(key):
            raise _Failure("bad_response")
        if not isinstance(value, str) or len(value) > MAX_FIELD_VALUE:
            raise _Failure("bad_response")
        fields.append((key, value))
    if max_bytes < size:
        raise _Failure("too_large")
    return UploadSpec(url, tuple(fields), file_field, max_bytes, expires_at)


# ---------------------------------------------------------------------------
# multipart（流式、可被取消）
# ---------------------------------------------------------------------------
class _MultipartBody:
    """`urllib` 的 `data=`：file-like，按块读；每次读之前查取消标志。长度在构造时就是确定的。"""

    CHUNK = 64 * 1024

    def __init__(self, spec: UploadSpec, data: bytes, cancel: threading.Event, on_progress) -> None:
        self.boundary = "----tavotto" + secrets.token_hex(16)
        head = io.BytesIO()
        for key, value in spec.fields:
            head.write(f"--{self.boundary}\r\n".encode("ascii"))
            head.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
            head.write(value.encode("utf-8") + b"\r\n")
        head.write(f"--{self.boundary}\r\n".encode("ascii"))
        head.write(
            f'Content-Disposition: form-data; name="{spec.file_field}"; '
            f'filename="tavotto-diagnostics.zip"\r\nContent-Type: application/zip\r\n\r\n'.encode(
                "ascii"
            )
        )
        self._head = head.getvalue()
        self._data = data
        self._tail = f"\r\n--{self.boundary}--\r\n".encode("ascii")
        self.length = len(self._head) + len(data) + len(self._tail)
        self._pos = 0
        self._cancel = cancel
        self._on_progress = on_progress

    @property
    def content_type(self) -> str:
        return f"multipart/form-data; boundary={self.boundary}"

    def read(self, amt: int = -1) -> bytes:
        if self._cancel.is_set():
            raise _Cancelled
        remaining = self.length - self._pos
        if remaining <= 0:
            return b""
        n = (
            min(self.CHUNK, remaining)
            if amt is None or amt < 0
            else min(amt, remaining, self.CHUNK)
        )
        out = self._slice(self._pos, self._pos + n)
        self._pos += n
        self._on_progress(self._pos, self.length)
        return out

    def _slice(self, start: int, end: int) -> bytes:
        a, b = len(self._head), len(self._head) + len(self._data)
        parts = []
        if start < a:
            parts.append(self._head[start : min(end, a)])
        if end > a and start < b:
            parts.append(self._data[max(start, a) - a : min(end, b) - a])
        if end > b:
            parts.append(self._tail[max(start, b) - b : end - b])
        return b"".join(parts)


# ---------------------------------------------------------------------------
# HTTP（所有出站都经这一个 opener 工厂）
# ---------------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """任何重定向（301/302/303/307/308）都不跟。

    **必须抛，不能返回 None**：返回 None 时 urllib 会继续问链上的下一个处理器，而 `build_opener` 自带的默认
    `HTTPRedirectHandler`（order 500）会把 301/302/303 的 POST 改成 GET 跟过去。本处理器排在它前面（order 400），
    抛出的 `HTTPError` 终止这条链，`_send` 把它当 3xx 响应交回。"""

    handler_order = 400

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _opener() -> urllib.request.OpenerDirector:
    """出站 HTTPS 的唯一 opener：平台原生证书校验（`tlstrust`）+ 不跟随重定向。"""
    ctx = tlstrust.client_context()
    opener = urllib.request.build_opener(tlstrust.https_handler(ctx))
    opener.add_handler(_NoRedirect())
    return opener


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes


def _lower(headers) -> dict[str, str]:
    return {k.lower(): v for k, v in (headers.items() if headers else [])}


def _send(req: urllib.request.Request, timeout: float) -> _Response:
    """发一次请求。2xx / 3xx / 4xx / 5xx 都回 `_Response`（重定向不跟）；传输层失败抛 `_Failure`。"""
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return _Response(resp.status, _lower(resp.headers), resp.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as err:
        try:
            body = err.read(MAX_RESPONSE_BYTES)
        except Exception:  # noqa: BLE001 — 读错误体失败不改变「这是个 HTTP 错误」
            body = b""
        return _Response(err.code, _lower(err.headers), body)
    except _Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — urllib / ssl / socket / http.client 的失败形状很多，统一归并
        raise _transport_failure(exc) from None


def _transport_failure(exc: BaseException) -> _Failure:
    if tlstrust.cert_verification_error(exc) is not None:
        return _Failure("tls")
    root = tlstrust.root_cause(exc)
    if isinstance(root, TimeoutError):  # socket.timeout 自 3.10 是 TimeoutError 的别名
        return _Failure("timeout")
    return _Failure("network")


def _json_request(target: Target, path: str, payload: dict, timeout: float) -> _Response:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死为白名单主机的 https
        target.base + path,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Tavotto-diagnostics-client/1",
        },
    )
    return _send(req, timeout)


def _retry_after(resp: _Response) -> int | None:
    raw = resp.headers.get("retry-after", "")
    if raw.isdigit():
        return min(int(raw), 7 * 86400)
    return None


def _json_body(resp: _Response) -> dict:
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _Failure("bad_response") from None
    if not isinstance(obj, dict):
        raise _Failure("bad_response")
    return obj


def _check_api_version(resp: _Response) -> None:
    raw = resp.headers.get(API_VERSION_HEADER.lower())
    if raw is None:
        return
    head = raw.split(".", 1)[0]
    if not head.isdigit() or int(head) != API_MAJOR:
        raise _Failure("api_incompatible")


def _server_code(resp: _Response) -> str:
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if isinstance(obj, dict) and isinstance(obj.get("code"), str):
        return obj["code"][:64]
    return ""


def classify(status: int, server_code: str) -> str:
    """服务端错误 (HTTP 状态, code) → 客户端失败码（`FAILURES` 的键）。服务端的状态码表见 infra 说明文档的错误码一节。"""
    if status == 429:
        return "daily_quota_exceeded" if server_code == "daily_quota_exceeded" else "rate_limited"
    if status == 503:
        return "uploads_disabled" if server_code == "uploads_disabled" else "storage_unavailable"
    if status == 413:
        return "too_large"
    if status == 410:
        return "expired"
    if status == 404:
        return "not_found"
    if status == 409:
        if server_code == "upload_missing":
            return "upload_missing"
        if server_code in ("idempotency_conflict", "session_closed", "cancelled"):
            return "session_closed"
        return "unexpected_response"  # in_progress / already_complete 由 complete 阶段先行处理
    if status == 422:
        if server_code == "notes_disabled":
            return "notes_disabled"
        if server_code == "rejected":
            return "rejected"
        return "client_error"
    if status in (400, 415):
        return "client_error"
    if 300 <= status < 400:
        return "redirect_blocked"
    return "unexpected_response"


def _server_error(resp: _Response) -> tuple[str, int | None]:
    """非 2xx 响应 → (归并后的失败码, Retry-After)。"""
    _check_api_version(resp)
    return classify(resp.status, _server_code(resp)), _retry_after(resp)


# ---------------------------------------------------------------------------
# 包检视
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BundleInfo:
    schema: int
    entries: tuple[tuple[str, str], ...]  # (文件名, 内容类别)


def inspect_bundle(data: bytes) -> BundleInfo:
    """读 ZIP 的目录与 manifest：给界面「会发送什么」，给 init 的 `bundle_schema`。格式不对抛 `SendError`。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if not names or len(names) > MAX_BUNDLE_ENTRIES or "manifest.json" not in names:
                raise SendError("bundle_invalid", 500)
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
    except SendError:
        raise
    except (zipfile.BadZipFile, ValueError, KeyError, OSError):
        raise SendError("bundle_invalid", 500) from None
    schema = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if isinstance(schema, bool) or not isinstance(schema, int) or not 1 <= schema <= 99:
        raise SendError("bundle_invalid", 500)
    return BundleInfo(schema, tuple((n, ENTRY_KINDS.get(n, "other")) for n in names))


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@dataclass
class SendSession:
    """一份已备好的包 + 它的发送状态。**所有秘密字段都在这里，且不进 `public()`。**"""

    id: str
    bundle: bytes | None = field(repr=False)
    size: int = 0
    sha256: str = ""
    info: BundleInfo | None = None
    created: float = 0.0
    state: str = "prepared"  # prepared | sending | cancelling | done | failed | cancelled | unknown
    stage: str = ""
    failure: str = ""
    retry_after: int | None = None
    report_id: str = ""
    sent: int = 0
    total: int = 0
    # ---- 私有：不进 public() / 日志 ----
    client_request_id: str = field(default="", repr=False)
    params: tuple[str, str] | None = field(default=None, repr=False)  # (category, note)
    token: str = field(default="", repr=False)
    spec: UploadSpec | None = field(default=None, repr=False)
    uploaded: bool = False
    server_open: bool = False  # 服务端有一个未完成的会话（取消时要通知它）
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    discard_after: bool = False
    complete_sent: bool = False  # `complete` 至少发出过一次（哪怕响应丢了）：服务端那份可能已经收下
    cancel_raced: bool = False  # 用户取消时报告其实已送达（cancel 回 409 already_complete）
    thread: threading.Thread | None = field(default=None, repr=False)

    def public(self) -> dict:
        out: dict = {
            "id": self.id,
            "state": self.state,
            "stage": self.stage or None,
            "size": self.size,
            "sha256": self.sha256,
            "sent": self.sent,
            "total": self.total,
        }
        if self.report_id and self.state in ("sending", "done", "unknown"):
            out["report_id"] = self.report_id
        if self.state == "done" and self.cancel_raced:
            out["cancel_raced"] = True
        if self.state == "failed":
            out["code"] = self.failure
            out["retryable"] = FAILURES.get(self.failure, False)
            out["retry_after"] = self.retry_after
        return out


_LOCK = threading.RLock()
_CURRENT: SendSession | None = None

#: 测试可换的时钟/睡眠（重试退避不该让用例真等）。
_sleep = time.sleep
_clock = time.monotonic


def _expire_locked() -> None:
    cur = _CURRENT
    if cur and cur.state in ("prepared", "failed", "cancelled", "done", "unknown"):
        if _clock() - cur.created > PREPARED_TTL_S:
            _discard_locked(cur)


def _drop_locked(sess: SendSession) -> None:
    global _CURRENT
    sess.bundle = None
    sess.token = ""
    sess.spec = None
    if _CURRENT is sess:
        _CURRENT = None


def _get(pid: object) -> SendSession:
    with _LOCK:
        _expire_locked()
        cur = _CURRENT
        if (
            cur is None
            or not isinstance(pid, str)
            or not secrets.compare_digest(cur.id.encode(), pid.encode("utf-8", "replace"))
        ):
            raise SendError("send_not_found", 404)
        return cur


# ---------------------------------------------------------------------------
# 对 Flask 层的接口
# ---------------------------------------------------------------------------
def prepare(bundle: bytes) -> dict:
    """备好一份包（**零网络**）。包超限不保管、不发送：回 `too_large` 让界面去导出到本地。"""
    global _CURRENT
    if resolve_target() is None:
        raise SendError("send_disabled", 404)
    info = inspect_bundle(bundle)
    with _LOCK:
        _expire_locked()
        cur = _CURRENT
        if cur is not None and cur.state in ("sending", "cancelling"):
            raise SendError("send_in_progress", 409)
        if cur is not None:
            _discard_locked(cur)
        digest = hashlib.sha256(bundle).hexdigest()
        summary = {
            "size": len(bundle),
            "sha256": digest,
            "schema": info.schema,
            "max_bytes": MAX_BYTES,
            "entries": [{"name": n, "kind": k} for n, k in info.entries],
        }
        if len(bundle) > MAX_BYTES:
            return {**summary, "too_large": True, "id": None}
        sess = SendSession(
            id=secrets.token_urlsafe(16),
            bundle=bundle,
            size=len(bundle),
            sha256=digest,
            info=info,
            created=_clock(),
        )
        _CURRENT = sess
        return {**summary, "too_large": False, "id": sess.id}


def bundle_bytes(pid: str) -> bytes:
    """用户要存/看的**同一份字节**。发完（done）后已释放。"""
    sess = _get(pid)
    with _LOCK:
        if sess.bundle is None:
            raise SendError("send_not_found", 404)
        return sess.bundle


def status(pid: str) -> dict:
    sess = _get(pid)
    with _LOCK:
        return sess.public()


def _clean_note(note: object) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise SendError("note_invalid", 422)
    text = note.replace("\x00", "").strip()
    if len(text) > NOTE_MAX_CHARS:
        raise SendError("note_too_long", 422)
    return text


def start(pid: str, *, confirmed: object, category: object, note: object) -> dict:
    """**用户点了「发送」**：开始 init → 上传 → complete。这是唯一会产生远程请求的入口。

    `confirmed` 必须是字面 `True`（界面的确认动作；缺省 / 其他任何值都不发）。已在发送中 → 回当前状态，
    不开第二条线程（双击）。失败 / 取消后的再次调用 = 用户又点了一次发送：同样的内容复用
    `client_request_id`（幂等），内容变了或服务端会话已关则换新的。"""
    if confirmed is not True:
        raise SendError("not_confirmed", 400)
    target = resolve_target()
    if target is None:
        raise SendError("send_disabled", 404)
    cat = DEFAULT_CATEGORY if category in (None, "") else category
    if cat not in CATEGORIES:
        raise SendError("category_invalid", 422)
    text = _clean_note(note)
    sess = _get(pid)
    with _LOCK:
        if sess.state in ("sending", "cancelling"):
            return sess.public()
        if sess.state == "unknown":
            raise SendError("result_unknown", 409)  # 可能已送达：不许在不知道的情况下再发一份
        if sess.state == "done" or sess.bundle is None:
            raise SendError("send_not_found", 404)
        params = (str(cat), text)
        reuse = bool(
            sess.client_request_id
            and sess.params == params
            and sess.failure not in REOPEN_ON
            and sess.state != "cancelled"
        )
        if not reuse:
            sess.client_request_id = str(uuid.uuid4())
            sess.uploaded = False
            sess.report_id = ""
            sess.server_open = False
            sess.complete_sent = False
        if not sess.uploaded:
            # 还没传上去：重新 init（同一个 client_request_id 的重放会给新的上传授权）
            sess.token, sess.spec = "", None
        sess.params = params
        sess.state, sess.stage, sess.failure, sess.retry_after = "sending", "init", "", None
        sess.cancel_raced = False
        sess.sent = sess.total = 0
        sess.cancel = threading.Event()
        sess.thread = threading.Thread(
            target=_run, args=(sess, target), name="tavotto-diag-send", daemon=True
        )
        sess.thread.start()
        return sess.public()


def cancel(pid: str) -> dict:
    """取消。发送中：打断上传并尽力通知服务端；其余：丢掉服务端那份未完成的会话（若有）。"""
    sess = _get(pid)
    with _LOCK:
        if sess.state == "sending":
            sess.state = "cancelling"
            sess.cancel.set()
        elif sess.state in ("prepared", "failed"):
            pending = (
                sess.complete_sent and sess.server_open
            )  # 服务端那份可能已 complete：等取消的答复再定论
            _cancel_remote_async(sess, settle=pending)
            sess.state, sess.stage, sess.failure = (
                ("cancelling" if pending else "cancelled"),
                "",
                "",
            )
        return sess.public()


def discard(pid: str) -> dict:
    """界面关了（对话框关闭 / 卸载）：不发了，释放内存里的包。发送中等价于取消。"""
    try:
        sess = _get(pid)
    except SendError:
        return {"ok": True}
    with _LOCK:
        running = sess.thread is not None and sess.thread.is_alive()
        if sess.state in ("sending", "cancelling") and running:
            sess.discard_after = True
            if sess.state == "sending":
                sess.state = "cancelling"
            sess.cancel.set()
        else:
            _discard_locked(sess)
    return {"ok": True}


def _discard_locked(sess: SendSession) -> None:
    if sess.state in ("prepared", "failed"):
        _cancel_remote_async(sess)
    _drop_locked(sess)


def reset_for_tests() -> None:
    global _CURRENT, _sleep, _clock
    with _LOCK:
        cur = _CURRENT
        if cur is not None:
            cur.cancel.set()
        _CURRENT = None
    th = cur.thread if cur is not None else None
    if th is not None and th.is_alive():
        th.join(5)
    _sleep, _clock = time.sleep, time.monotonic


# ---------------------------------------------------------------------------
# 发送线程
# ---------------------------------------------------------------------------
def _log(result: str, stage: str) -> None:
    LOG.info(
        "诊断发送: 阶段=%s 结果=%s",
        logsafe.known(stage, STAGES),
        logsafe.known(result, LOG_RESULTS),
    )


def _set_stage(sess: SendSession, stage: str) -> None:
    with _LOCK:
        sess.stage = stage


def _check_cancel(sess: SendSession) -> None:
    if sess.cancel.is_set():
        raise _Cancelled


def _run(sess: SendSession, target: Target) -> None:
    code, retry_after = "", None
    try:
        _flow(sess, target)
    except _Cancelled:
        code = "cancelled"
    except _Failure as fail:
        code, retry_after = fail.code, fail.retry_after
    except Exception as exc:  # noqa: BLE001 — 线程里的任何意外都收成 internal，不让线程静默死掉
        LOG.error("诊断发送: 内部错误 类型=%s", type(exc).__name__)  # 不带异常文本：里面可能有地址
        code = "internal"
    _finish(sess, target, code, retry_after)


def _finish(sess: SendSession, target: Target, code: str, retry_after: int | None) -> None:
    with _LOCK:
        stage = sess.stage or "init"
        open_remote = sess.server_open and bool(sess.token) and bool(sess.report_id)
    outcome = "cancelled"
    if code == "cancelled" or (code and not FAILURES.get(code, False)):
        # 取消，或这一份发不出去（不是用户再点一次能解决的）：通知服务端作废那份未完成的会话。
        # 取消结果是三态（`_cancel_remote`）：确认已取消 / 确认已送达 / 无法确认——「无法确认」绝不当作「确认取消」
        if open_remote:
            outcome = _cancel_remote(sess, target)
            if code == "cancelled":
                if outcome == CANCEL_COMPLETE:
                    # complete 其实已经到了服务端（响应丢了）：这次发送**成功了**，保留报告编号、按成功收尾
                    code = ""
                    with _LOCK:
                        sess.cancel_raced = True
                elif outcome == CANCEL_UNKNOWN and sess.complete_sent:
                    code = "unknown"  # complete 发出过而取消结果不明：结果未知，保留报告编号
            if code != "" and code != "unknown":
                with _LOCK:
                    sess.server_open = False
                    sess.token, sess.spec, sess.uploaded, sess.report_id = "", None, False, ""
    with _LOCK:
        if code == "cancelled":
            sess.state, sess.stage, sess.failure = "cancelled", "", ""
            sess.token, sess.spec, sess.uploaded, sess.report_id = "", None, False, ""
            sess.server_open = False
        elif code == "unknown":
            sess.state, sess.stage, sess.failure = "unknown", "", ""
            sess.bundle, sess.token, sess.spec, sess.server_open = None, "", None, False
        elif code:
            sess.state, sess.failure, sess.retry_after = "failed", code, retry_after
            sess.stage = ""
        else:
            sess.state, sess.stage, sess.failure = "done", "", ""
            sess.bundle = None  # 发完了：释放内存里的包
            sess.token, sess.spec, sess.server_open = "", None, False
        if sess.discard_after:
            _discard_locked(sess)
    _log(code or "ok", stage)


def _flow(sess: SendSession, target: Target) -> None:
    category, note = sess.params or (DEFAULT_CATEGORY, "")
    data = sess.bundle
    if data is None:
        raise _Failure("internal")
    _check_cancel(sess)
    if not sess.token:
        _init(sess, target, category, note, len(data))
        if sess.state == "done":
            return
    if not sess.uploaded and sess.spec is not None:
        _check_cancel(sess)
        _set_stage(sess, "upload")
        _upload(sess, data)
        with _LOCK:
            sess.uploaded = True
    _check_cancel(sess)
    _set_stage(sess, "complete")
    _complete(sess, target)


def _version() -> str:
    from .. import __version__

    return __version__ if _VERSION_OK.match(__version__) else "unknown"


def _init(sess: SendSession, target: Target, category: str, note: str, size: int) -> None:
    _set_stage(sess, "init")
    body: dict = {
        "client_request_id": sess.client_request_id,
        "tavotto_version": _version(),
        "os": telemetry._platform(),
        "arch": telemetry._arch(),
        "distribution": telemetry._distribution(),
        "category": category,
        "bundle_schema": sess.info.schema if sess.info else 1,
        "size": size,
        "sha256": sess.sha256,
    }
    if note:
        body["note"] = note
    resp = _json_request(target, "/v1/reports/init", body, API_TIMEOUT_S)
    if resp.status not in (200, 201):
        code, retry_after = _server_error(resp)
        raise _Failure(code, retry_after)
    _check_api_version(resp)
    obj = _json_body(resp)
    report_id, state = obj.get("report_id"), obj.get("state")
    if (
        obj.get("ok") is not True
        or not isinstance(report_id, str)
        or not _REPORT_ID.match(report_id)
    ):
        raise _Failure("bad_response")
    if state == "complete":  # 幂等重放：这次发送早已成功（丢了 complete 的响应）
        with _LOCK:
            sess.report_id, sess.state, sess.server_open = report_id, "done", False
        return
    token = obj.get("complete_token")
    if (
        state not in ("pending", "verifying")
        or not isinstance(token, str)
        or not _TOKEN.match(token)
    ):
        raise _Failure("bad_response")
    with _LOCK:  # 令牌先登记：下面上传地址若被拒，取消 / 作废要用它通知服务端
        sess.report_id, sess.token, sess.server_open = report_id, token, True
    spec = parse_upload_spec(obj.get("upload"), target, size)
    with _LOCK:
        sess.spec = spec
        sess.uploaded = state == "verifying"  # 服务端已在核实：对象早就在那里了
        if state == "verifying":
            sess.complete_sent = True  # 有人（本机上一次）已经发过 complete


def _upload(sess: SendSession, data: bytes) -> None:
    spec = sess.spec
    target = resolve_target()
    if spec is None or target is None:  # 发送途中开关被关了：不再往外发
        raise _Cancelled

    def progress(sent: int, total: int) -> None:
        with _LOCK:
            sess.sent, sess.total = sent, total

    body = _MultipartBody(spec, data, sess.cancel, progress)
    req = urllib.request.Request(  # noqa: S310 — spec.url 已过 validate_upload_url
        spec.url,
        data=body,  # file-like；长度由 Content-Length 给
        method="POST",
        headers={
            "Content-Type": body.content_type,
            "Content-Length": str(body.length),
            "User-Agent": "Tavotto-diagnostics-client/1",
        },
    )
    resp = _send(req, UPLOAD_TIMEOUT_S)
    if 200 <= resp.status < 300:
        return
    if 300 <= resp.status < 400:
        raise _Failure("redirect_blocked")
    if resp.status in (401, 403):
        raise _Failure("upload_denied")
    if resp.status in (400, 413, 415, 422):
        raise _Failure("upload_rejected")
    if resp.status >= 500:
        raise _Failure("upload_unavailable")
    raise _Failure("unexpected_response")


def _complete(sess: SendSession, target: Target) -> None:
    payload = {"report_id": sess.report_id, "complete_token": sess.token}
    with _LOCK:
        sess.complete_sent = (
            True  # 从这一刻起，服务端那份可能已经 complete——取消结果「无法确认」不能当作「没送达」
        )
    last: _Failure | None = None
    for attempt in range(COMPLETE_ATTEMPTS):
        if attempt:
            wait = min(last.retry_after, RETRY_AFTER_CAP_S) if last and last.retry_after else 0
            _sleep(wait or COMPLETE_BACKOFF_S[min(attempt - 1, len(COMPLETE_BACKOFF_S) - 1)])
            _check_cancel(sess)
        try:
            resp = _json_request(target, "/v1/reports/complete", payload, COMPLETE_TIMEOUT_S)
        except _Failure as fail:
            if fail.code in ("network", "timeout"):  # 响应可能丢在路上：同一份令牌幂等重试
                last = fail
                continue
            raise
        if resp.status == 200:
            _check_api_version(resp)
            _verify_complete(sess, _json_body(resp))
            return
        code, retry_after = _server_error(resp)
        server_code = _server_code(resp)
        if resp.status == 409 and server_code == "already_complete":
            return
        if resp.status == 409 and server_code == "in_progress":
            last = _Failure("unexpected_response", retry_after)
            continue
        if code == "upload_missing":
            last = _Failure("upload_missing", retry_after)
            continue
        raise _Failure(code, retry_after)
    assert last is not None
    if last.code == "upload_missing":
        with _LOCK:
            sess.uploaded = False  # 用户再点发送时重新 init + 上传
    raise last


def _verify_complete(sess: SendSession, obj: dict) -> None:
    if obj.get("ok") is not True or obj.get("state") != "complete":
        raise _Failure("bad_response")
    if obj.get("report_id") != sess.report_id:
        raise _Failure("bad_response")
    sha, size = obj.get("sha256"), obj.get("size")
    if not isinstance(sha, str) or isinstance(size, bool) or not isinstance(size, int):
        raise _Failure("bad_response")
    if sha != sess.sha256 or size != sess.size:
        raise _Failure("digest_mismatch")


# ---------------------------------------------------------------------------
# 取消（尽力而为）
# ---------------------------------------------------------------------------
#: 取消结果（三态）。竞态表见 `docs/rules/backend/diagnostics.md`。
CANCEL_CANCELLED = "cancelled"  # 确认已取消（2xx 含幂等重放；404 = 服务端没有这份，也就没送达）
CANCEL_COMPLETE = "complete"  # 确认已送达（409 already_complete）
CANCEL_UNKNOWN = (
    "unknown"  # 无法确认（传输错误 / 超时 / 5xx / 429 / in_progress 重问后仍不定 / 其他）
)


def _cancel_remote(sess: SendSession, target: Target) -> str:
    """通知服务端这份会话作废，回 `CANCEL_*` 三态之一。**只有服务端明确答复才算确定**：传输错误、超时、5xx
    一律是 `CANCEL_UNKNOWN`，调用方不能把它当成「已取消」——complete 若已发出，那份报告可能已经存下。
    `in_progress`（对端正在核实）等一等再问一次，仍不定也是 unknown。不重试别的失败、不上报。"""
    payload = {"report_id": sess.report_id, "complete_token": sess.token}
    for attempt in range(2):
        try:
            resp = _json_request(target, "/v1/reports/cancel", payload, CANCEL_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — 传输层失败：无法确认
            return CANCEL_UNKNOWN
        code = _server_code(resp)
        if 200 <= resp.status < 300:
            return CANCEL_CANCELLED
        if resp.status == 404:
            return CANCEL_CANCELLED
        if resp.status == 409 and code == "already_complete":
            return CANCEL_COMPLETE
        if resp.status == 409 and code == "in_progress" and attempt == 0:
            _sleep(min(_retry_after(resp) or 3, RETRY_AFTER_CAP_S))
            continue
        return CANCEL_UNKNOWN
    return CANCEL_UNKNOWN


def _cancel_remote_async(sess: SendSession, *, settle: bool = False) -> None:
    """失败态 / 备好态里的取消（没有发送线程）。回调**认会话身份**：只有 `_CURRENT is sess` 才改它的状态——
    会话被丢弃 / 换成别的之后迟到的答复一律忽略。`settle=True`（complete 发出过）时会话停在 `cancelling`，
    由答复定论：确认取消 → cancelled；确认已送达 → done；无法确认 → unknown（保留报告编号）。"""
    target = resolve_target()
    if target is None or not (sess.server_open and sess.token and sess.report_id):
        if settle:
            sess.state = "unknown" if sess.report_id else "cancelled"
        return
    snapshot = SendSession(id="", bundle=None, report_id=sess.report_id, token=sess.token)
    report_id = sess.report_id
    complete_sent = sess.complete_sent
    sess.server_open = False
    sess.token, sess.spec, sess.uploaded = "", None, False
    if not settle:
        sess.report_id = ""

    def run() -> None:
        outcome = _cancel_remote(snapshot, target)
        with _LOCK:
            if _CURRENT is not sess:
                return  # 会话已被丢弃 / 替换：迟到的答复不碰任何现役会话
            if outcome == CANCEL_COMPLETE:
                if sess.state in ("cancelled", "cancelling", "failed"):
                    sess.report_id = report_id
                    sess.state, sess.stage, sess.failure = "done", "", ""
                    sess.cancel_raced = True
                    sess.bundle = None
            elif sess.state == "cancelling":
                if outcome == CANCEL_UNKNOWN and complete_sent:
                    sess.report_id = report_id
                    sess.state, sess.stage, sess.failure = "unknown", "", ""
                    sess.bundle = None
                else:
                    sess.report_id = ""
                    sess.state, sess.stage, sess.failure = "cancelled", "", ""
            if sess.discard_after:
                _discard_locked(sess)

    threading.Thread(target=run, name="tavotto-diag-cancel", daemon=True).start()
