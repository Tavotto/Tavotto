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
* **发行版默认开启入口，但每次发送仍要用户确认**（2026-10-13，G-DIAG 开放，ADR 0118 修订）。`enabled()` 的判据：
  没有硬开关 `TAVOTTO_NO_DIAG_UPLOAD=1`、没有显式关 `TAVOTTO_DIAG_UPLOAD=0`、目的地合规。目的地 = 未设
  `TAVOTTO_DIAG_ENDPOINT` 时的内置生产端点 `PRODUCTION_ENDPOINT`；设了就**必须**过白名单 `ENDPOINT_HOSTS`
  （`{tavotto.cn}`）或回环开发模式，不合规就关闭——**不回退到内置端点**，误配不会悄悄发到生产。
  `TAVOTTO_DIAG_UPLOAD` 不再是开启条件（`=1` 无效果，只有 `=0` 起作用）。
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

from . import diagsummary, logsafe, telemetry, tlstrust

LOG = logging.getLogger("tavotto.diagsend")

# ---------------------------------------------------------------------------
# 开关与目的地
# ---------------------------------------------------------------------------
ENV_ENABLE = "TAVOTTO_DIAG_UPLOAD"
ENV_HARD_OFF = "TAVOTTO_NO_DIAG_UPLOAD"
ENV_ENDPOINT = "TAVOTTO_DIAG_ENDPOINT"
ENV_DEV_LOOPBACK = "TAVOTTO_DIAG_DEV_LOOPBACK"

#: 生产端点的主机白名单（G-DIAG 开放，2026-10-13）。`TAVOTTO_DIAG_ENDPOINT` 设了就必须落在这里（或回环开发模式）。
ENDPOINT_HOSTS: frozenset[str] = frozenset({"tavotto.cn"})
#: 内置生产端点：未设 `TAVOTTO_DIAG_ENDPOINT` 时用它。主机必须 ∈ `ENDPOINT_HOSTS`（`test_diag_send.py` 钉）。
PRODUCTION_ENDPOINT = "https://tavotto.cn"
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
#: 默认上限 64 MiB（2026-10-13 起，原 10 MiB；服务端 `DIAG_MAX_REPORT_BYTES` 同步调到 64 MiB；
#: 仍以 init 返回的 `upload.max_bytes` 为准，init 之后再核一次）。
MAX_BYTES = 64 * 1024 * 1024
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
    # ---- 发送包独有的内容（ADR 0118 修订 2026-10-13）。条目名一律**扁平**（服务端 ZIP 检查不许目录 / 斜杠），
    # 多文件的类别各打成一个内层 zip ----
    "detail-errors.json": "detail_errors",
    "detail-app-log.txt": "detail_logs",
    "detail-sidecar-log.txt": "detail_logs",
    "detail-worker-log.txt": "detail_logs",
    "detail-state.zip": "detail_state",
    "detail-requests.json": "detail_requests",
    "detail-overrides.zip": "detail_overrides",
    "detail-render-env.json": "detail_render_env",
    "detail-runs.json": "detail_runs",
    "detail-shell.json": "detail_shell",
    "detail-storage.json": "detail_storage",
    "detail-frontend-console.json": "detail_console",
    "detail-display.json": "detail_display",
    "detail-network.json": "detail_network",
    "optional-source.zip": "detail_source",
    "optional-crash-reports.zip": "detail_crash_reports",
    "optional-corrupt-sessions.zip": "detail_corrupt_sessions",
}
#: 服务端 ZIP 检查：整包条目数 ≤ 32（见下方 `ZIP_RULES`）；这里留同一个数
MAX_BUNDLE_ENTRIES = 32


def kind_of(name: str) -> str:
    """包内文件 → 内容类别（精确名；没登记的是 `other`）。"""
    return ENTRY_KINDS.get(name, "other")


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
#: 全局保管预算：所有项目里已备好（prepared / failed，非发送中）的包合计不超过它。上限 64 MiB 之后若每个项目 / 窗口各留一份，
#: N 个对话框就是 N × 64 MiB 压在引擎进程里；超了就回收闲置最久的（`_enforce_budget_locked`）。
STORED_BUDGET_BYTES = 128 * 1024 * 1024
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


class _Unknown(Exception):
    """上一次尝试的下落无法确认（它的 complete 发出过）：收尾为 `unknown`，保留编号，不许再发一份。"""


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


def parse_endpoint(
    raw: str, *, dev_loopback: bool, hosts: frozenset[str] | None = None
) -> Target | None:
    """`TAVOTTO_DIAG_ENDPOINT` → `Target`；任何一处不合规一律 None（不抛、不猜、不修正）。
    `hosts` 是主机白名单，默认诊断接收端的；「联系作者」（`contactsend.py`，ADR 0145）传它自己的两主机白名单，
    其余校验（https、无用户信息 / 路径 / 查询串、回环仅开发模式）同一份实现。"""
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
    if host in (ENDPOINT_HOSTS if hosts is None else hosts):
        return Target(host, 443, False) if port in (None, 443) else None
    if dev_loopback and host == LOOPBACK_HOST and port:
        return Target(host, port, True)
    return None


def resolve_target() -> Target | None:
    """此刻允许上传的目的地；None = 功能关闭（入口不出现）。判据见模块头「发行版默认开启入口」一条。"""
    if _flag(ENV_HARD_OFF) or os.environ.get(ENV_ENABLE, "").strip() == "0":
        return None
    raw = os.environ.get(ENV_ENDPOINT)
    if raw is None or not raw.strip():
        raw = PRODUCTION_ENDPOINT
    return parse_endpoint(raw, dev_loopback=_flag(ENV_DEV_LOOPBACK))


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

    def __init__(
        self,
        spec: UploadSpec,
        data: bytes,
        cancel: threading.Event,
        on_progress,
        *,
        filename: str = "tavotto-diagnostics.zip",
        content_type: str = "application/zip",
    ) -> None:
        # `filename` / `content_type` 的默认值就是诊断包的老值；「联系作者」的附件（ADR 0145）复用同一个流式、可取消的
        # 实现，只换这两项（调用方保证是 ASCII、无引号与换行）
        self.boundary = "----tavotto" + secrets.token_hex(16)
        head = io.BytesIO()
        for key, value in spec.fields:
            head.write(f"--{self.boundary}\r\n".encode("ascii"))
            head.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
            head.write(value.encode("utf-8") + b"\r\n")
        head.write(f"--{self.boundary}\r\n".encode("ascii"))
        head.write(
            f'Content-Disposition: form-data; name="{spec.file_field}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode("ascii")
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
    sizes: tuple[int, ...] = ()  # 与 entries 一一对应：解压后的字节数
    options: dict | None = None  # manifest 的 detail_options（老包 / 导出包没有 = None）
    truncated: tuple[dict, ...] = ()  # manifest 的 truncated


#: infra 诊断服务对上传 ZIP 的检查（服务端从不解压到磁盘，只读中央目录与 manifest.json）。**出处是那份检查本身**
#: （2026-10-13 与主控核对，单条目 / 总量上限同日放宽为 128 / 256 MiB）；客户端出包后用同一组判据自检，
#: 不合规当内部错误、不发送——否则用户点了「发送」才被服务端以 `bad_bundle` 拒收。
ZIP_RULES = {
    "max_entries": 32,
    "name": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
    "max_entry_uncompressed": 128 * 1024 * 1024,
    "max_total_uncompressed": 256 * 1024 * 1024,
    "max_ratio": 200,  # file_size > 1024 且 file_size > 200 × compress_size 被拒
    "max_manifest": 64 * 1024,
}


ZIP_PROBLEMS = frozenset(
    {
        "corrupt_zip",
        "empty_zip",
        "too_many_entries",
        "unsafe_entry_name",
        "duplicate_entry",
        "encrypted_entry",
        "unsupported_compression",
        "entry_too_large",
        "compression_ratio",
        "total_too_large",
        "missing_manifest",
        "manifest_too_large",
    }
)


def zip_problem(data: bytes) -> str | None:
    """按服务端的判据过一遍；回第一条不合规的原因（短码），合规回 None。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        return "corrupt_zip"
    with z:
        infos = z.infolist()
        if not infos:
            return "empty_zip"
        if len(infos) > ZIP_RULES["max_entries"]:
            return "too_many_entries"
        seen: set[str] = set()
        total = 0
        for i in infos:
            if not ZIP_RULES["name"].fullmatch(i.filename) or i.filename in (".", ".."):
                return "unsafe_entry_name"
            if i.filename in seen:
                return "duplicate_entry"
            seen.add(i.filename)
            if i.flag_bits & 0x1:
                return "encrypted_entry"
            if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                return "unsupported_compression"
            if i.file_size > ZIP_RULES["max_entry_uncompressed"]:
                return "entry_too_large"
            if i.file_size > 1024 and i.file_size > ZIP_RULES["max_ratio"] * max(
                i.compress_size, 1
            ):
                return "compression_ratio"
            total += i.file_size
            if total > ZIP_RULES["max_total_uncompressed"]:
                return "total_too_large"
        if "manifest.json" not in seen:
            return "missing_manifest"
        if z.getinfo("manifest.json").file_size > ZIP_RULES["max_manifest"]:
            return "manifest_too_large"
    return None


def inspect_bundle(data: bytes) -> BundleInfo:
    """读 ZIP 的目录与 manifest：给界面「会发送什么」，给 init 的 `bundle_schema`。格式不对抛 `SendError`。"""
    problem = zip_problem(data)
    if problem is not None:
        LOG.warning("诊断包不符合服务端 ZIP 检查: %s", logsafe.known(problem, ZIP_PROBLEMS))
        raise SendError("bundle_invalid", 500)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            names = [i.filename for i in infos]
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
    options = manifest.get("detail_options") if isinstance(manifest, dict) else None
    options = (
        {k: v is True for k, v in options.items() if isinstance(k, str)}
        if isinstance(options, dict)
        else None
    )
    cut = manifest.get("truncated") if isinstance(manifest, dict) else None
    cut = tuple(
        {
            "kind": str(c.get("kind"))[:48],
            "original_bytes": int(c.get("original_bytes") or 0),
            "kept_bytes": int(c.get("kept_bytes") or 0),
        }
        for c in (cut if isinstance(cut, list) else [])[:32]
        if isinstance(c, dict)
    )
    return BundleInfo(
        schema,
        tuple((n, kind_of(n)) for n in names),
        tuple(i.file_size for i in infos),
        options,
        cut,
    )


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
    #: 单调的「动作代次」：cancel / discard 各加一。`start` 带着它看到的代次来，对不上就拒（乱序到达的旧 send）
    gen: int = 0
    touched: float = 0.0  # 最近一次被用到（API 调用 / 发送开始 / 终态）：TTL 只针对闲置
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
    #: 属于哪个项目上下文（#927）：注册表按它分键，发送线程 / 异步取消回调捕获的就是这个会话对象
    scope: str = field(default="", repr=False)
    #: 已被丢弃出注册表：迟到的异步答复据此忽略（取代旧的 `_CURRENT is sess` 身份判断）
    dropped: bool = field(default=False, repr=False)
    #: 改参数重试时，上一次尝试（complete 已发出）还没对账：发送线程先问服务端它的下落（I1），(report_id, token)
    reconcile: tuple[str, str] | None = field(default=None, repr=False)
    #: 最近一次「点发送」那一刻生成的 `error_summary` 声明（同意快照 + 最小事实）；None = 这次不带。
    #: 它是 init 不可变提交的一部分（服务端同一 `client_request_id` 换了它 = 409），所以与 `params` 一起决定能否复用 id
    summary: dict | None = field(default=None, repr=False)
    #: 服务端把带 `error_summary` 的 init 当请求形状错拒了（尚未升级的旧版）：本会话此后不再带它
    summary_refused: bool = False

    def public(self) -> dict:
        out: dict = {
            "id": self.id,
            "state": self.state,
            "stage": self.stage or None,
            "size": self.size,
            "sha256": self.sha256,
            "sent": self.sent,
            "total": self.total,
            "gen": self.gen,
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
#: 会话注册表，按项目上下文分键（#927）：两个窗口、两个项目各有各的备好 / 发送中的会话，互不丢弃、互不 409。
_SESSIONS: dict[str, SendSession] = {}
#: 每个项目最近一次「已送达 / 结果未知」的会话在被丢弃时留下的状态快照（只有状态与报告编号，没有包与令牌）：
#: 关对话框 / 切项目不能吞掉送达结果，用户凭编号才能要求删除（#928）。每个项目最多一份，后来的覆盖。
_OUTCOMES: dict[str, dict] = {}
#: 每个项目收到过的最大备包序号（前端每次备包带一个单调的 `seq`（由 `issue_prepare_seq` 发放））：比它旧的备包请求（关了再开 / 切走再回时晚到的那份）
#: 不许登记会话、也不许替换新会话——否则前端丢弃旧响应后，界面上的新编号指向一个不存在的会话。
_PREPARE_SEQ: dict[str, int] = {}
_PREPARE_SEQ_CAP = 64


_SEQ_ISSUED = 0


def issue_prepare_seq(scope: str) -> int:
    """发一个备包序号：引擎自己计数，不看墙钟（系统时钟被往回校正、WebView 重载都不会让新页面的序号落到旧序号后面）。
    至少比本项目已登记的大 1，所以引擎里记住的序号再大，新发的也排在它前面。进程内单调，只发放不登记（登记在 begin_prepare）。"""
    global _SEQ_ISSUED
    with _LOCK:
        _SEQ_ISSUED = max(_SEQ_ISSUED, _PREPARE_SEQ.get(scope, 0)) + 1
        return _SEQ_ISSUED


def begin_prepare(scope: str, seq: int | None) -> None:
    """备包请求到达时登记它的序号；比已登记的更旧就 409 `stale_prepare`（不必再建这份包）。
    `seq=None`（老客户端）不参与排序。"""
    if seq is None:
        return
    with _LOCK:
        if seq < _PREPARE_SEQ.get(scope, 0):
            raise SendError("stale_prepare", 409)
        _PREPARE_SEQ[scope] = seq
        while len(_PREPARE_SEQ) > _PREPARE_SEQ_CAP:
            del _PREPARE_SEQ[next(iter(_PREPARE_SEQ))]


#: 测试可换的时钟/睡眠（重试退避不该让用例真等）。
_sleep = time.sleep
_clock = time.monotonic


#: 已送达 / 结果未知的会话至少留到用户关闭对话框；闲置超过这个上限才回收（轮询每次都会刷新 `touched`，
#: 所以只要界面还开着就不会被回收）。
FINISHED_KEEP_S = 24 * 3600


def _expire_locked() -> None:
    """只回收**闲置**的会话：发送中 / 取消中永不过期；备好 / 失败 / 已取消按 `PREPARED_TTL_S`（从最近一次使用算，
    不是从备包算）；done / unknown 留 `FINISHED_KEEP_S`——送达的编号不能因为发送跨过 30 分钟就被藏起来（I3）。"""
    for cur in list(_SESSIONS.values()):
        if cur.state in ("sending", "cancelling"):
            continue
        finished = cur.state in ("done", "unknown")
        if _clock() - cur.touched > (FINISHED_KEEP_S if finished else PREPARED_TTL_S):
            if finished:  # 已送达的结果留满 24 h 没人问过才回收（I3）——回收就是回收，不再留快照
                _drop_locked(cur, archive=False)
            else:
                _discard_locked(cur)


def _stored_bytes(sess: SendSession) -> int:
    return (
        len(sess.bundle) if sess.bundle is not None and sess.state in ("prepared", "failed") else 0
    )


def _enforce_budget_locked(keep: SendSession) -> None:
    """保管的包合计超过 `STORED_BUDGET_BYTES` 就按「闲置最久」回收 prepared / failed 会话（走 `_discard_locked`：
    complete 发出过的仍按 I1 先对账再丢，不能直接吞）。发送中 / 取消中 / 已完成的不计入也绝不回收；刚备好的 `keep` 不回收。
    被回收的会话再被问到就是 404（前端「包已不在引擎里 → 重新准备」路径）。"""
    while True:
        pool_ = [s for s in _SESSIONS.values() if _stored_bytes(s)]
        if sum(_stored_bytes(s) for s in pool_) <= STORED_BUDGET_BYTES:
            return
        victims = sorted((s for s in pool_ if s is not keep), key=lambda s: s.touched)
        if not victims:
            return
        before = _stored_bytes(victims[0])
        _discard_locked(victims[0])
        if _stored_bytes(victims[0]) >= before:  # 没有进展（理论上不会）：别死循环
            return


def _archive_locked(sess: SendSession) -> None:
    """被丢弃的会话若有「已送达 / 可能已送达」的报告编号，留一份状态快照（#928）：complete 发出过而结果没定论的
    一律记 unknown——不能因为用户关了窗就把一份可能已存下的报告藏起来。"""
    if not sess.report_id:
        return
    if sess.state in ("done", "unknown"):
        snap = sess.public()
    elif sess.complete_sent and sess.state in ("prepared", "failed", "cancelling"):
        snap = {**sess.public(), "state": "unknown", "report_id": sess.report_id}
    else:
        return
    snap.pop("code", None)
    snap.pop("retryable", None)
    snap.pop("retry_after", None)
    _OUTCOMES[sess.scope] = snap


def _drop_locked(sess: SendSession, *, archive: bool = True) -> None:
    if archive:
        _archive_locked(sess)
    sess.bundle = None
    sess.token = ""
    sess.spec = None
    sess.dropped = True
    if _SESSIONS.get(sess.scope) is sess:
        del _SESSIONS[sess.scope]


def _same_id(a: str, b: object) -> bool:
    return isinstance(b, str) and secrets.compare_digest(a.encode(), b.encode("utf-8", "replace"))


def _get(pid: object, scope: str) -> SendSession:
    with _LOCK:
        _expire_locked()
        cur = _SESSIONS.get(scope)
        if cur is None or not _same_id(cur.id, pid):
            raise SendError("send_not_found", 404)
        cur.touched = _clock()
        return cur


# ---------------------------------------------------------------------------
# 对 Flask 层的接口
# ---------------------------------------------------------------------------
def prepare(bundle: bytes, *, scope: str = "", seq: int | None = None) -> dict:
    """备好一份包（**零网络**）。包超限不保管、不发送：回 `too_large` 让界面去导出到本地。
    `scope` = 请求的项目上下文：只替换**本项目**的旧会话，别的项目的会话（含发送中的）不受影响。"""
    if resolve_target() is None:
        raise SendError("send_disabled", 404)
    info = inspect_bundle(bundle)
    with _LOCK:
        if seq is not None and seq < _PREPARE_SEQ.get(scope, 0):
            raise SendError("stale_prepare", 409)  # 登记会话前再比一次：建包期间更新的请求已经到了
        _expire_locked()
        cur = _SESSIONS.get(scope)
        if cur is not None and cur.state in ("sending", "cancelling"):
            if not cur.discard_after:
                raise SendError("send_in_progress", 409)
            # 对话框已关、只等发送线程 / 取消答复收尾的会话：让位给新会话（它自己收尾并留下结果快照）
            del _SESSIONS[scope]
        elif cur is not None:
            _discard_locked(cur)
        digest = hashlib.sha256(bundle).hexdigest()
        summary = {
            "size": len(bundle),
            "sha256": digest,
            "schema": info.schema,
            "max_bytes": MAX_BYTES,
            "entries": [
                {"name": n, "kind": k, "bytes": b}
                for (n, k), b in zip(info.entries, info.sizes or (0,) * len(info.entries))
            ],
            # 勾选项的实际取值与被截短的类别（确认框据此说明）；老包 / 导出包 = 全 false、空
            "options": {
                k: bool((info.options or {}).get(k))
                for k in ("source", "crash_reports", "corrupt_sessions")
            },
            "truncated": list(info.truncated),
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
            touched=_clock(),
            scope=scope,
        )
        _SESSIONS[scope] = sess
        _enforce_budget_locked(keep=sess)
        return {**summary, "too_large": False, "id": sess.id, "gen": sess.gen}


def bundle_bytes(pid: str, *, scope: str = "") -> bytes:
    """用户要存/看的**同一份字节**。发完（done）后已释放。"""
    sess = _get(pid, scope)
    with _LOCK:
        if sess.bundle is None:
            raise SendError("send_not_found", 404)
        return sess.bundle


def status(pid: str, *, scope: str = "") -> dict:
    """会话状态。会话已被丢弃但留有送达结果快照（#928）时回那份快照——送达结果可被后续查询取回。"""
    try:
        sess = _get(pid, scope)
    except SendError:
        with _LOCK:
            snap = _OUTCOMES.get(scope)
            if snap is not None and _same_id(snap["id"], pid):
                return dict(snap)
        raise
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


def start(
    pid: str,
    *,
    confirmed: object,
    category: object,
    note: object,
    gen: object = None,
    scope: str = "",
) -> dict:
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
    sess = _get(pid, scope)
    with _LOCK:
        if sess.state in ("sending", "cancelling"):
            return sess.public()
        # 乱序防线：本机的 send 与 cancel 是两个独立的 POST，可能在不同服务线程上颠倒顺序处理。
        # 取消过的会话不可被 start 复活（要再发必须重新「准备」得到新会话）；带着旧代次的 start 一律拒。
        if sess.state == "cancelled":
            raise SendError("session_cancelled", 409)
        if gen is not None and gen != sess.gen:
            raise SendError("stale_action", 409)
        if sess.state == "unknown":
            raise SendError("result_unknown", 409)  # 可能已送达：不许在不知道的情况下再发一份
        if sess.state == "done" or sess.bundle is None:
            raise SendError("send_not_found", 404)
        params = (str(cat), text)
        # 最终点击的这一刻重新判一次同意：关掉统计后再点发送，就不会再带摘要（已提交的不撤回，这里只管之后）
        summary = None if sess.summary_refused else diagsummary.build(scope)
        reuse = bool(
            sess.client_request_id
            and sess.params == params
            and sess.summary == summary
            and sess.failure not in REOPEN_ON
            and sess.state != "cancelled"
        )
        sess.reconcile = None
        if not reuse:
            if sess.complete_sent and sess.report_id and sess.failure not in REOPEN_ON:
                # I1（Codex #923 P2）：上一次尝试 complete 发出过、编号还在——改参数重试不许静默孤立它。
                # 先问服务端它的下落（发送线程里做，不在锁里联网）：已存 → done；确认没存 → 才换新 id 重传；不确定 → unknown。
                if sess.server_open and sess.token:
                    sess.reconcile = (sess.report_id, sess.token)
                else:  # 够不着服务端（令牌已不在），而 complete 发出过：不能当「没送达」
                    sess.state, sess.stage, sess.failure = "unknown", "", ""
                    sess.bundle = None
                    raise SendError("result_unknown", 409)
            sess.client_request_id = str(uuid.uuid4())
            sess.uploaded = False
            sess.report_id = ""
            sess.server_open = False
            sess.complete_sent = False
        if not sess.uploaded:
            # 还没传上去：重新 init（同一个 client_request_id 的重放会给新的上传授权）
            sess.token, sess.spec = "", None
        sess.params = params
        sess.summary = summary
        sess.state, sess.stage, sess.failure, sess.retry_after = "sending", "init", "", None
        sess.cancel_raced = False
        sess.touched = _clock()
        sess.sent = sess.total = 0
        sess.cancel = threading.Event()
        sess.thread = threading.Thread(
            target=_run, args=(sess, target), name="tavotto-diag-send", daemon=True
        )
        sess.thread.start()
        return sess.public()


def cancel(pid: str, *, scope: str = "") -> dict:
    """取消。发送中：打断上传并尽力通知服务端；其余：丢掉服务端那份未完成的会话（若有）。"""
    sess = _get(pid, scope)
    with _LOCK:
        sess.gen += 1  # 此后任何带旧代次的 start 都被拒
        if sess.state == "sending":
            sess.state = "cancelling"
            sess.cancel.set()
        elif sess.state in ("prepared", "failed"):
            if sess.complete_sent and sess.report_id:
                # I1：complete 发出过——编号永不清空，结果由取消答复定论；够不着服务端就是 unknown
                if sess.server_open and sess.token:
                    _cancel_remote_async(sess, settle=True)
                    sess.state, sess.stage, sess.failure = "cancelling", "", ""
                else:
                    sess.state, sess.stage, sess.failure = "unknown", "", ""
                    sess.bundle = None
            else:
                _cancel_remote_async(sess)
                sess.state, sess.stage, sess.failure = "cancelled", "", ""
        return sess.public()


def discard(pid: str, *, scope: str = "") -> dict:
    """界面关了（对话框关闭 / 卸载）：不发了，释放内存里的包。发送中等价于取消。"""
    try:
        sess = _get(pid, scope)
    except SendError:
        return {"ok": True}
    with _LOCK:
        sess.gen += 1
        running = sess.thread is not None and sess.thread.is_alive()
        if sess.state == "cancelling" or (sess.state == "sending" and running):
            # 有人（发送线程 / 异步取消回调）会收尾：等它定论后再丢弃，结果快照才是真结果而不是猜的 unknown
            sess.discard_after = True
            if sess.state == "sending":
                sess.state = "cancelling"
            sess.cancel.set()
        else:
            _discard_locked(sess)
    return {"ok": True}


def _discard_locked(sess: SendSession) -> None:
    if sess.state in ("prepared", "failed"):
        if sess.complete_sent and sess.report_id and sess.server_open and sess.token:
            # I1（Codex #923 P2）：complete 发出过——丢弃前先走 settle 路径，答复回来才定论并留下结果快照，
            # 绝不「取消 + 立刻移出 + 清编号」把可能已送达的报告吞掉。会话先停在 cancelling，答复到了再收尾丢弃。
            sess.discard_after = True
            sess.state, sess.stage, sess.failure = "cancelling", "", ""
            _cancel_remote_async(sess, settle=True)
            if sess.state == "cancelling":
                return  # 答复回来由回调收尾并丢弃
        else:
            _cancel_remote_async(sess)
    _drop_locked(sess)


def reset_for_tests() -> None:
    global _sleep, _clock
    with _LOCK:
        curs = list(_SESSIONS.values())
        for cur in curs:
            cur.cancel.set()
        _SESSIONS.clear()
        _OUTCOMES.clear()
        _PREPARE_SEQ.clear()
    for cur in curs:
        th = cur.thread
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
    code, retry_after, cancelled, unknown = "", None, False, False
    try:
        _flow(sess, target)
    except _Cancelled:
        cancelled = True
    except _Unknown:
        unknown = True
    except _Failure as fail:
        code, retry_after = fail.code, fail.retry_after
    except Exception as exc:  # noqa: BLE001 — 线程里的任何意外都收成 internal，不让线程静默死掉
        LOG.error("诊断发送: 内部错误 类型=%s", type(exc).__name__)  # 不带异常文本：里面可能有地址
        code = "internal"
    _settle(sess, target, code, retry_after, cancelled, unknown)


def _settle(
    sess: SendSession,
    target: Target,
    code: str,
    retry_after: int | None,
    cancelled: bool,
    unknown: bool = False,
) -> None:
    """发送线程的**唯一**收尾点：任何终态（成功、用户取消、请求失败、校验失败、超时、进程内异常）都经这里对账。

    不变量（竞态表见 docs/rules/backend/diagnostics.md）：
    * I1 `complete` 发出过（`complete_sent`）之后，report_id **永不清空**，最终状态只能是 done / unknown /
      cancelled（仅在服务端确认取消）/ failed（仅在服务端确认没存，或失败可重试而我们没去取消）——
      「无法确认」绝不当作「确认没存」；
    * I2 用户取消一旦置位（`sess.cancel`），优先于请求自己的失败：请求结束后先看取消事件，再对账；
      服务端有未完成的会话就尽力 cancel 关掉（不管 `complete` 发没发过）；
    * 成功（flow 正常返回）永远是 done：取消事件在成功之后才到，只是 `cancel_raced`。"""
    with _LOCK:
        stage = sess.stage or "init"
        user_cancel = cancelled or sess.cancel.is_set()
        open_remote = sess.server_open and bool(sess.token) and bool(sess.report_id)
        complete_sent = sess.complete_sent
        had_id = bool(sess.report_id)
    final, raced, clear = "done", False, False
    if unknown:  # 改参数重试时对账不出上一次尝试的下落（见 `_reconcile_previous`）
        final = "unknown"
    elif code or cancelled:
        retryable = bool(code) and FAILURES.get(code, False)
        must_cancel = user_cancel or (bool(code) and not retryable)
        outcome = _cancel_remote(sess, target) if (must_cancel and open_remote) else None
        if outcome == CANCEL_COMPLETE:
            # 服务端确认已存：用户取消 → 这次发送成功了（`cancel_raced`）；非用户取消的失败（如摘要不符）→
            # 报告虽在但我们不能确认它是对的 → unknown（保留编号，如实说）
            final, raced = ("done", True) if user_cancel else ("unknown", False)
        elif outcome == CANCEL_UNKNOWN and complete_sent:
            final = "unknown"
        elif outcome is None and must_cancel and complete_sent and had_id:
            final = "unknown"  # 想取消但够不着服务端，而 complete 发出过：不能当「没送达」
        elif not must_cancel:
            final = "failed"  # 可重试的失败、没去取消：保留一切（含 complete_sent 之后的编号），用户再点会幂等重放
        else:
            # 服务端确认没存（取消 2xx / 404），或 complete 从未发出（没有什么可能已送达）
            final, clear = ("cancelled" if user_cancel else "failed"), True
    else:
        raced = sess.cancel.is_set()  # 成功收尾，取消事件只是赶在了成功之后
    with _LOCK:
        if clear:
            sess.server_open = False
            sess.token, sess.spec, sess.uploaded, sess.report_id = "", None, False, ""
        sess.stage = ""
        sess.touched = _clock()
        if final == "cancelled":
            sess.state, sess.failure = "cancelled", ""
        elif final == "unknown":
            sess.state, sess.failure = "unknown", ""
            sess.bundle, sess.token, sess.spec, sess.server_open = None, "", None, False
        elif final == "failed":
            sess.state, sess.failure, sess.retry_after = "failed", code or "internal", retry_after
        else:
            sess.state, sess.failure = "done", ""
            sess.cancel_raced = raced
            sess.bundle = None  # 发完了：释放内存里的包
            sess.token, sess.spec, sess.server_open = "", None, False
        if sess.discard_after:
            _discard_locked(sess)
    _log({"done": "ok"}.get(final, final if final != "failed" else (code or "internal")), stage)


def _flow(sess: SendSession, target: Target) -> None:
    category, note = sess.params or (DEFAULT_CATEGORY, "")
    data = sess.bundle
    if data is None:
        raise _Failure("internal")
    _check_cancel(sess)
    if sess.reconcile is not None:
        _reconcile_previous(sess, target)
        if sess.state == "done":
            return
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


def _reconcile_previous(sess: SendSession, target: Target) -> None:
    """改参数重试之前，先按 I1 对账上一次尝试（它的 `complete` 发出过，响应却丢了）——不对账就换新 id 重传，
    首份报告会被静默孤立（Codex #923 P2）。三态：服务端确认已存 → 这次发送其实早已成功（`done`，首份编号，
    不再上传第二份）；确认没存（cancel 2xx / 404）→ 才放行换新 id 重传；无法确认 → `unknown`（保留首份编号）。"""
    with _LOCK:
        old = sess.reconcile
        sess.reconcile = None
    if old is None:
        return
    snapshot = SendSession(id="", bundle=None, report_id=old[0], token=old[1])
    outcome = _cancel_remote(snapshot, target)
    if outcome == CANCEL_CANCELLED:
        return
    with _LOCK:
        sess.report_id = old[0]
        sess.complete_sent = True
        if outcome == CANCEL_COMPLETE:
            sess.state, sess.server_open = "done", False
            return
    raise _Unknown


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
    if sess.summary is not None:
        body["error_summary"] = sess.summary
    resp = _json_request(target, "/v1/reports/init", body, API_TIMEOUT_S)
    if (
        sess.summary is not None
        and resp.status in (400, 422)
        and _server_code(resp) == "bad_request"
    ):
        # 部署顺序保险：尚未升级的服务端拒绝未知字段（extra=forbid）。摘要是可选的，不能因它让报告失败——
        # 去掉它重发一次（同一个 client_request_id：旧服务端没记下被拒的 init）。本会话此后不再带。
        with _LOCK:
            sess.summary, sess.summary_refused = None, True
        del body["error_summary"]
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
    """失败态 / 备好态里的取消（没有发送线程）。回调**认会话身份**：会话已被丢弃（`dropped`）就不改它的状态——
    被丢弃 / 换成别的之后迟到的答复一律忽略。`settle=True`（complete 发出过）时会话停在 `cancelling`，
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
            if sess.dropped:
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
