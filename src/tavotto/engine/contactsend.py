"""「联系作者」：用户在应用里写一封信，由本机引擎经 Tavotto 服务器代发（ADR 0145）。

服务端是 `Tavotto/infra` 的 diagnostics-api 同一进程里的 `/v1/messages/*`（契约 v1：init → 附件直传 COS → complete；
取回回信 `GET /v1/messages/{id}/replies`）。**用户不需要登录、不需要配置自己的邮箱**：全产品没有 邮件协议链接，
也不唤起系统邮件程序（`tests/test_contact_no_mailto.py` 钉）。失败时界面只给「复制内容 / 复制地址」。

形状照抄 `diagsend.py`（ADR 0118），这里只写差异：

* **逐次授权**：上传只由一次用户点击（`start()`）触发；没有记住选择、没有后台补发。附件字节只在发送那一次的内存里，
  结束（成功、失败、取消）即丢，草稿由前端保管、重试时前端重新带上。
* **本机引擎直传**：WebView 只遥控本机 `/api/contact/*`；出站 HTTPS 只经 `diagsend._send`（`tlstrust` 平台原生证书、
  不跟随任何重定向、响应体封顶），COS 上传复用 `diagsend._MultipartBody`（流式、可取消）与 `validate_upload_url`
  （`_COS_HOST` 同一条正则，不另放行 `cos.accelerate.myqcloud.com`——契约给的就是 `<桶>.cos.<区域>.myqcloud.com`）。
* **目的地由代码钉死**：线路 = `{tavotto.cn, api.tavotto.com}` 两个主机，别的主机一律不连（开发回环模式照
  诊断发送：`TAVOTTO_DIAG_DEV_LOOPBACK=1` + `TAVOTTO_CONTACT_ENDPOINT` 才放行 `127.0.0.1`，设了但不合规就整个功能
  关闭、**不回退到内置线路**）。
* **选路**：大陆环境先 `tavotto.cn` 后 `api.tavotto.com`，否则反之；传输失败（连不上 / 超时 / 证书）或 5xx 换另一条，
  init 与 complete 都可安全重放（`client_message_id` / `complete_token` 幂等）。「大陆环境」的判据只有一处——
  桌面壳 `src-tauri/src/app_update.rs::source_order`（ADR 0121）；壳在拉起引擎时把它的结论写进环境变量
  `TAVOTTO_NETWORK_REGION=cn|global`，这里读它（`region_is_cn`），**不在 Python 里再抄一份时区 / 语言表**。
  没有壳的安装（pip / 源码）没有这个信号，界面语言是简体中文（`zh-CN`，ADR 0121 判据 3）也算大陆；其余先走海外线路，
  不通自然换国内线路。
* **秘密不落地**：`reply_token` / `complete_token` / 上传表单、用户写的主题 / 正文 / 邮箱只活在内存里的会话私有字段
  （`reply_token` 另落 `contactinbox` 的 0600 文件，那是契约要求）；`public()` 状态、日志里都没有。日志模板是字面量，
  参数只有闭集里的阶段名与结果码；异常文本不进日志。
* **遥测这次不加事件**（ADR 0145）：本模块不 import `telemetry` 的 `capture`，只借 `_platform/_arch/_distribution` 三个
  枚举探测函数；范围攒齐后一次升同意版本，不在这里先加。

回信：发送成功后 `contactinbox` 存 (message_id, reply_token, route, sent_at)；后台线程在启动时与每 6 小时问一次服务端
（没有任何记录就一次网络都不发），有新回信前端亮小圆点 + 通知轨胶囊。

纯标准库，Flask 父进程 import 链上的模块。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field

from . import brand, contactinbox, diagsend, logsafe, telemetry

LOG = logging.getLogger("tavotto.contactsend")

# ---------------------------------------------------------------------------
# 开关与线路
# ---------------------------------------------------------------------------
ENV_HARD_OFF = "TAVOTTO_NO_CONTACT"
#: 开发 / 验收用：逗号分隔的端点（按顺序当作线路）。必须过 `CONTACT_HOSTS` 或回环开发模式，不合规 = 功能关闭。
ENV_ENDPOINT = "TAVOTTO_CONTACT_ENDPOINT"
#: 桌面壳写给引擎的“大陆环境”结论（`app_update::detect_order` 的第一项是不是镜像）。
ENV_REGION = "TAVOTTO_NETWORK_REGION"

#: 线路 → 主机。**白名单就是这两个主机**（`api.tavotto.com` 是 Cloudflare Worker，只转发到 `tavotto.cn`）。
ROUTE_HOSTS: dict[str, str] = {"cn": "tavotto.cn", "global": "api.tavotto.com"}
CONTACT_HOSTS: frozenset[str] = frozenset(ROUTE_HOSTS.values())

API_VERSION_HEADER = diagsend.API_VERSION_HEADER
API_MAJOR = diagsend.API_MAJOR

# ---------------------------------------------------------------------------
# 契约上限（唯一出处：contract.md v1；界面从 `capability()` 取，不手写第二份）
# ---------------------------------------------------------------------------
SUBJECT_MAX = 120
BODY_MAX = 5000
EMAIL_MAX = 254
MAX_ATTACHMENTS = 5
MAX_TOTAL_BYTES = 20 * 1024 * 1024
NAME_MAX = 128
LOCALES = ("zh-CN", "en-US")

#: 扩展名 → 契约白名单里的 content_type；服务端按魔数核对，所以这里同样按魔数核一遍。
TYPE_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
ALLOWED_TYPES = tuple(dict.fromkeys(TYPE_BY_EXT.values()))
_OFFICE_TYPES = frozenset(t for t in ALLOWED_TYPES if t.startswith("application/vnd."))

_MESSAGE_ID = re.compile(r"^TVM-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_ASCII_NAME = re.compile(r"[^A-Za-z0-9._-]")

# ---------------------------------------------------------------------------
# 超时、轮询
# ---------------------------------------------------------------------------
API_TIMEOUT_S = 15
UPLOAD_TIMEOUT_S = 60
COMPLETE_TIMEOUT_S = 70
CANCEL_TIMEOUT_S = 5
POLL_TIMEOUT_S = 15
#: 带 HTML 的回信列表的响应体上限，按**最坏线缆编码**（JSON 文本的字节数，不是原文字节数）推出（服务端回信列表没有分页）：
#: 一封信最多 `MAX_REPLIES_PER_MESSAGE`（50）条回信，每条 =
#:   2 ×（HTML 256 KiB + 正文 20000 字 × 4 字节（UTF-8 最坏））——JSON 转义最坏 2 倍：服务端响应是 `ensure_ascii=False`
#:     （非 ASCII 字符原样输出，不膨胀成 6 字节的 \uXXXX），且存库前已去掉除 \n \t 外的控制字符（否则一个控制字符会膨胀成
#:     6 字节 \u00XX）；剩下需要转义的只有 `"` 和 `\\`（1 字节 -> 2 字节）与 \n \t（同为 1 -> 2），所以最坏 2 倍；
#:   + 固定开销 1 KiB（id / created_at / 键名 / 引号）+ 20 张附图元数据 × 256 B；
#: 再加 1 MiB 余量（外层 message_id / state 等）。约 36 MB 是上限不是常态（真实回信几 KiB）。
#: **这个上界依赖服务端的两条保证**（infra）：单封信 ≤ 50 条回信、回信 HTML 与正文存库前去控制字符。任一条变了，这里必须同步改
#: （ADR 0145「第二期修订」同样写明）。超过它或 JSON 解析不了都**不当成功**：标 `poll_error`，下一轮再试。
REPLIES_MAX_BYTES = (
    contactinbox.MAX_REPLIES_PER_MESSAGE
    * (
        2 * (contactinbox.MAX_REPLY_HTML_BYTES + contactinbox.MAX_REPLY_CHARS * 4)
        + 1024
        + contactinbox.MAX_ASSETS_PER_REPLY * 256
    )
    + 1024 * 1024
)
ASSET_TIMEOUT_S = 60
#: 一轮轮询最多下多少字节 / 最多发多少次附图请求（防服务端异常或被劫持时失控；下一轮继续）
ASSET_ROUND_BYTES = 64 * 1024 * 1024
ASSET_ROUND_REQUESTS = 80
COMPLETE_ATTEMPTS = 4
COMPLETE_BACKOFF_S = (1.0, 2.0, 4.0)
#: 后台轮询回信的间隔（契约：客户端启动时 + 每 6 小时一次）与界面「刷新」的最小间隔。
POLL_INTERVAL_S = 6 * 3600
REFRESH_MIN_S = 30
POLL_STARTUP_DELAY_S = 20

STAGES = frozenset({"init", "upload", "complete", "cancel", "poll"})

#: 失败码（对界面稳定的闭集；日志里明文）。值 = 用户能不能再点一次「再试一次」。
#: 由 (HTTP 状态, 服务端 code, 阶段) 归并（`classify`），不是服务端 code 的镜像；码一旦发布不改名。
FAILURES: dict[str, bool] = {
    "network": True,
    "timeout": True,
    "tls": True,
    "bad_response": True,
    "api_incompatible": False,
    "rate_limited": True,
    "storage_unavailable": True,
    "too_large": False,
    "expired": True,
    "session_closed": True,
    "not_found": True,
    "upload_denied": True,
    "upload_rejected": False,
    "upload_unavailable": True,
    "upload_missing": True,
    "upload_url_rejected": False,
    "redirect_blocked": False,
    "client_error": False,
    "unexpected_response": True,
    "internal": False,
    "invalid_email": False,
    "attachment_invalid": True,
    "report_not_found": True,
}
LOG_RESULTS = frozenset(FAILURES) | {"ok", "cancelled"}
#: 失败后要换一个新的 `client_message_id` 才能再发（服务端会话已关 / 过期 / 附件被拒）
REOPEN_ON = frozenset({"expired", "session_closed", "not_found", "attachment_invalid"})
#: diagsend.classify 能给出、但这里并入别的码的结果
_FOLD = {
    "daily_quota_exceeded": "rate_limited",
    "uploads_disabled": "storage_unavailable",
    "notes_disabled": "client_error",
    "rejected": "client_error",
    "digest_mismatch": "client_error",
}


class ContactError(Exception):
    """对 Flask 层抛的错：`code` 稳定、`status` 是本机 HTTP 状态。"""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


_Failure = diagsend._Failure
_Cancelled = diagsend._Cancelled


# ---------------------------------------------------------------------------
# 开关 / 线路解析
# ---------------------------------------------------------------------------
def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def region_is_cn(ui_locale: str | None = None) -> bool:
    """大陆环境？壳的结论（`TAVOTTO_NETWORK_REGION=cn`）或界面语言是简体中文（ADR 0121 判据 3）。
    `global` 不否决界面语言：壳算它时还不知道界面语言。"""
    if os.environ.get(ENV_REGION, "").strip().lower() == "cn":
        return True
    return ui_locale == "zh-CN"


def route_label(target: diagsend.Target) -> str:
    return "cn" if target.host == ROUTE_HOSTS["cn"] else "global"


def resolve_targets(ui_locale: str | None = None) -> list[diagsend.Target]:
    """此刻允许连的线路，**按尝试顺序**；空 = 功能关闭（入口不出现）。"""
    if _flag(ENV_HARD_OFF):
        return []
    raw = os.environ.get(ENV_ENDPOINT)
    if raw is not None and raw.strip():
        dev = _flag(diagsend.ENV_DEV_LOOPBACK)
        out = [
            diagsend.parse_endpoint(part, dev_loopback=dev, hosts=CONTACT_HOSTS)
            for part in raw.split(",")
            if part.strip()
        ]
        # 设了就必须全部合规：误配不会悄悄发到别处，也不回退到内置线路
        return [t for t in out if t is not None] if out and all(out) else []
    order = ("cn", "global") if region_is_cn(ui_locale) else ("global", "cn")
    return [
        t
        for t in (
            diagsend.parse_endpoint(
                f"https://{ROUTE_HOSTS[r]}", dev_loopback=False, hosts=CONTACT_HOSTS
            )
            for r in order
        )
        if t is not None
    ]


def enabled() -> bool:
    return bool(resolve_targets())


def capability() -> dict:
    """给界面的能力说明与未读数（本机判断，**不联网**，不含线路地址）。"""
    if not enabled():
        return {"enabled": False}
    return {
        "enabled": True,
        "recipient": brand.FEEDBACK_EMAIL,
        "subject_max": SUBJECT_MAX,
        "body_max": BODY_MAX,
        "email_max": EMAIL_MAX,
        "max_attachments": MAX_ATTACHMENTS,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "extensions": sorted(TYPE_BY_EXT),
        "unread": contactinbox.unread_count(),
        "retention_days": contactinbox.RETENTION_DAYS,
    }


# ---------------------------------------------------------------------------
# 输入校验（本机先挡一道；服务端同一规则再挡一道）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Attachment:
    name: str  # 给服务端看的名字（它自己再净化一次）
    content_type: str
    data: bytes = field(repr=False)

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def wire_name(self) -> str:
        """multipart 头里的文件名：ASCII、无引号 / 换行（服务端只存 init 里报的净化名）。"""
        ext = os.path.splitext(self.name)[1].lower()
        return "attachment" + (ext if ext in TYPE_BY_EXT else "")


def _sniff_ok(content_type: str, data: bytes) -> bool:
    head = data[:16]
    if content_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if content_type == "application/pdf":
        return head.startswith(b"%PDF-")
    if content_type in _OFFICE_TYPES:
        return head.startswith(b"PK\x03\x04")
    return False


def clean_attachment(name: object, data: bytes) -> Attachment:
    raw = name if isinstance(name, str) else ""
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(c for c in base if c.isprintable() and c not in '"<>|:*?').strip()
    ext = os.path.splitext(base)[1].lower()
    ctype = TYPE_BY_EXT.get(ext)
    if ctype is None:
        raise ContactError("attachment_type", 422)
    if not data:
        raise ContactError("attachment_empty", 422)
    if not _sniff_ok(ctype, data):
        raise ContactError("attachment_mismatch", 422)
    if len(base) > NAME_MAX:
        stem = os.path.splitext(base)[0][: NAME_MAX - len(ext)]
        base = stem + ext
    return Attachment(base, ctype, data)


def valid_email(value: str) -> bool:
    """服务端同一条规则：只收可打印 ASCII，恰好一个 @、两侧非空，拒绝空白 / 引号 / 分号 / 逗号 / 括号，域名必须含「.」。"""
    if len(value) > EMAIL_MAX or value.count("@") != 1:
        return False
    if not all(0x21 <= ord(c) <= 0x7E for c in value) or any(c in ",;<>()[]\"'" for c in value):
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


@dataclass(frozen=True)
class Draft:
    locale: str
    subject: str
    body: str
    reply_email: str
    report_id: str
    attachments: tuple[Attachment, ...]

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for part in (self.locale, self.subject, self.body, self.reply_email, self.report_id):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        for a in self.attachments:
            h.update(a.name.encode("utf-8"))
            h.update(hashlib.sha256(a.data).digest())
        return h.hexdigest()


def build_draft(meta: object, files: list[tuple[str, bytes]]) -> Draft:
    if not isinstance(meta, dict):
        raise ContactError("meta_invalid", 400)

    def text(key: str) -> str:
        v = meta.get(key)
        if v is None:
            return ""
        if not isinstance(v, str):
            raise ContactError("meta_invalid", 400)
        return v.replace("\x00", "")

    subject, body = text("subject").strip(), text("body").strip()
    reply_email, report_id = text("reply_email").strip(), text("report_id").strip()
    locale = text("locale")
    if len(subject) > SUBJECT_MAX:
        raise ContactError("subject_too_long", 422)
    if not body:
        raise ContactError("body_required", 422)
    if len(body) > BODY_MAX:
        raise ContactError("body_too_long", 422)
    if reply_email and not valid_email(reply_email):
        raise ContactError("email_invalid", 422)
    if report_id and not diagsend._REPORT_ID.match(report_id):
        raise ContactError("report_invalid", 422)
    if len(files) > MAX_ATTACHMENTS:
        raise ContactError("attachments_limit", 422)
    atts = tuple(clean_attachment(n, d) for n, d in files)
    if sum(a.size for a in atts) > MAX_TOTAL_BYTES:
        raise ContactError("attachments_too_large", 422)
    return Draft(
        locale if locale in LOCALES else "en-US", subject, body, reply_email, report_id, atts
    )


# ---------------------------------------------------------------------------
# HTTP（出站只经 diagsend._send：平台证书、不跟重定向、响应体封顶）
# ---------------------------------------------------------------------------
_UA = "Tavotto-contact-client/1"


def _server_code(resp: diagsend._Response) -> str:
    """错误体只认 reports 一族的实际形状：顶层 `{"ok": false, "code": "...", "error": "..."}`（服务端实际行为）。
    不兼容别的形状——两种都认会掩盖契约漂移。"""
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if isinstance(obj, dict) and isinstance(obj.get("code"), str):
        return obj["code"][:64]
    return ""


def classify(status: int, server_code: str) -> str:
    """服务端错误 → 本模块失败码（`FAILURES` 的键）。"""
    if status == 422:
        if server_code in ("invalid_email", "attachment_invalid", "report_not_found"):
            return server_code
        return "client_error"
    code = diagsend.classify(status, server_code)
    code = _FOLD.get(code, code)
    return code if code in FAILURES else "unexpected_response"


def _error_of(resp: diagsend._Response) -> _Failure:
    diagsend._check_api_version(resp)
    return _Failure(classify(resp.status, _server_code(resp)), diagsend._retry_after(resp))


def _post(target: diagsend.Target, path: str, payload: dict, timeout: float):
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死为白名单主机的 https
        target.base + path,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            API_VERSION_HEADER: str(API_MAJOR),
            "User-Agent": _UA,
        },
    )
    return diagsend._send(req, timeout)


def _get(
    target: diagsend.Target,
    path: str,
    bearer: str,
    timeout: float,
    *,
    accept: str = "application/json",
    limit: int = diagsend.MAX_RESPONSE_BYTES,
):
    req = urllib.request.Request(  # noqa: S310 — 同上
        target.base + path,
        method="GET",
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {bearer}",
            API_VERSION_HEADER: str(API_MAJOR),
            "User-Agent": _UA,
        },
    )
    return diagsend._send(req, timeout, limit)


#: 传输层失败（响应没拿到）与 5xx：这条线路这会儿不通，换另一条。其余（含 429 / 4xx）是服务端明确的答复，不换。
_ROUTE_DOWN = frozenset({"network", "timeout", "tls"})


def _ordered(
    targets: list[diagsend.Target], prefer: diagsend.Target | None
) -> list[diagsend.Target]:
    if prefer is None or prefer not in targets:
        return list(targets)
    return [prefer] + [t for t in targets if t != prefer]


def _failover(targets, prefer, call, spent: list[int] | None = None):
    """按顺序在各线路上做 `call(target) -> _Response`；传输失败 / 5xx 换下一条，最后一条的结果（或失败）交回。
    返回 (响应, 作答的线路)。`spent`（可选，单元素列表）累计**每次尝试**实际读到的响应体字节（含被换掉的线路的错误体），
    调用方据此扣预算；不传则行为不变。"""
    last_fail: _Failure | None = None
    last_resp = None
    for tgt in _ordered(targets, prefer):
        try:
            resp = call(tgt)
        except _Failure as fail:
            if fail.code not in _ROUTE_DOWN:
                raise
            last_fail, last_resp = fail, None
            continue
        if spent is not None:
            spent[0] += len(resp.body)
        if resp.status >= 500:
            last_fail, last_resp = None, (resp, tgt)
            continue
        return resp, tgt
    if last_resp is not None:
        return last_resp
    assert last_fail is not None
    raise last_fail


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def _log(result: str, stage: str) -> None:
    LOG.info(
        "联系作者: 阶段=%s 结果=%s",
        logsafe.known(stage, STAGES),
        logsafe.known(result, LOG_RESULTS),
    )


@dataclass
class Session:
    """一次发送。**所有秘密字段都在这里，且不进 `public()`。**"""

    draft: Draft | None = field(repr=False)
    fingerprint: str = ""
    state: str = "sending"  # sending | cancelling | done | failed | cancelled
    stage: str = "init"
    failure: str = ""
    retry_after: int | None = None
    sent: int = 0
    total: int = 0
    message_id: str = ""
    route: str = ""
    gen: int = 0
    #: 回信令牌是否已存进本机收件记录；False = 信已送达但应用里收不到回信（数据目录不可写 / 盘满）
    inbox_saved: bool = True
    # ---- 私有 ----
    client_message_id: str = field(default="", repr=False)
    reply_token: str = field(default="", repr=False)
    complete_token: str = field(default="", repr=False)
    target: diagsend.Target | None = field(default=None, repr=False)
    specs: list = field(default_factory=list, repr=False)
    #: init 重放回了空票据（这封信早已 complete）：跳过附件上传，直接 complete
    replayed: bool = False
    complete_sent: bool = False
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)

    def public(self) -> dict:
        out: dict = {
            "state": self.state,
            "stage": self.stage if self.state in ("sending", "cancelling") else None,
            "sent": self.sent,
            "total": self.total,
            "gen": self.gen,
        }
        if self.message_id and self.state in ("done", "failed", "sending", "cancelling"):
            out["message_id"] = self.message_id
        if self.state == "done":
            out["route"] = self.route
            out["inbox_saved"] = self.inbox_saved
        if self.state == "failed":
            out["code"] = self.failure
            out["retryable"] = FAILURES.get(self.failure, False)
            out["retry_after"] = self.retry_after
        return out


_LOCK = threading.RLock()
_SESSION: Session | None = None
#: 每次 start 加一，让界面能分辨“这是我发起的那次”
_GEN = 0
#: 最近一次失败的 (指纹, client_message_id)：用户在窗口里点“再试一次”（或关窗再开、内容没变）时幂等复用同一个 id，
#: 服务端回同一个 reply_token，不会变成两封信。只存这两样，没有任何内容。
_REUSE: tuple[str, str] | None = None
#: 测试可换的睡眠
_sleep = time.sleep


def status() -> dict:
    with _LOCK:
        return _SESSION.public() if _SESSION is not None else {"state": "idle", "gen": _GEN}


def start(meta: object, files: list[tuple[str, bytes]]) -> dict:
    """**用户点了「发送」**：开始 init → 附件直传 → complete。这是唯一由用户动作引出的远程请求入口（轮询回信除外，见下）。
    已在发送中 → 409，不开第二条线程（双击）。同样的内容在失败后再发 = 复用 `client_message_id`（幂等），
    内容变了或服务端会话已关（`REOPEN_ON`）则换新的。"""
    global _SESSION, _GEN, _REUSE
    targets = resolve_targets(_locale_of(meta))
    if not targets:
        raise ContactError("disabled", 404)
    draft = build_draft(meta, files)
    fp = draft.fingerprint()
    with _LOCK:
        if _SESSION is not None and _SESSION.state in ("sending", "cancelling"):
            raise ContactError("send_in_progress", 409)
        _GEN += 1
        sess = Session(draft=draft, fingerprint=fp, gen=_GEN)
        sess.client_message_id = _REUSE[1] if _REUSE and _REUSE[0] == fp else str(uuid.uuid4())
        _REUSE = None
        sess.total = sum(a.size for a in draft.attachments)
        sess.target = targets[0]
        _SESSION = sess
        sess.thread = threading.Thread(
            target=_run, args=(sess, targets), name="tavotto-contact", daemon=True
        )
        sess.thread.start()
        return sess.public()


def _locale_of(meta: object) -> str | None:
    loc = meta.get("locale") if isinstance(meta, dict) else None
    return loc if isinstance(loc, str) else None


def cancel() -> dict:
    with _LOCK:
        cur = _SESSION
        if cur is None:
            return {"state": "idle", "gen": _GEN}
        if cur.state == "sending":
            cur.state = "cancelling"
            cur.cancel.set()
        return cur.public()


def dismiss() -> dict:
    """界面看过结果（或关了对话框）：已结束的会话丢掉展示态（失败的指纹与 id 另存在 `_REUSE`）；发送中的不丢。"""
    global _SESSION
    with _LOCK:
        if _SESSION is not None and _SESSION.state in ("done", "failed", "cancelled"):
            _SESSION = None
        return _SESSION.public() if _SESSION is not None else {"state": "idle", "gen": _GEN}


def reset_for_tests() -> None:
    global _SESSION, _sleep, _LAST_POLL, _REUSE
    with _LOCK:
        cur = _SESSION
        _SESSION = None
        _REUSE = None
    if cur is not None:
        cur.cancel.set()
        if cur.thread is not None and cur.thread.is_alive():
            cur.thread.join(5)
    _sleep = time.sleep
    _LAST_POLL = 0.0


def _check_cancel(sess: Session) -> None:
    if sess.cancel.is_set():
        raise _Cancelled


def _set_stage(sess: Session, stage: str) -> None:
    with _LOCK:
        sess.stage = stage


def _run(sess: Session, targets: list[diagsend.Target]) -> None:
    code, retry_after, cancelled = "", None, False
    try:
        _flow(sess, targets)
    except _Cancelled:
        cancelled = True
    except _Failure as fail:
        code, retry_after = fail.code, fail.retry_after
    except Exception as exc:  # noqa: BLE001 — 线程里的任何意外都收成 internal
        LOG.error("联系作者: 内部错误 类型=%s", type(exc).__name__)  # 不带异常文本：里面可能有地址
        code = "internal"
    _settle(sess, targets, code, retry_after, cancelled)


def _settle(sess: Session, targets, code, retry_after, cancelled) -> None:
    """发送线程的唯一收尾点。已经发出 complete 之后的取消不再生效（那一步不可撤回，结果以服务端答复为准）。"""
    global _REUSE
    stage = sess.stage or "init"
    open_remote = not sess.complete_sent and bool(sess.complete_token and sess.message_id)
    user_cancel = cancelled or (bool(code) and sess.cancel.is_set() and not sess.complete_sent)
    if user_cancel and not sess.complete_sent and sess.complete_token and sess.message_id:
        _cancel_remote(sess, targets)  # 尽力而为：服务端 pending 一小时后自己也会过期
    elif code and open_remote:
        # 服务端 complete 时附件缺失会把整封信置 rejected：任何一步没走完就作废这封，重试用新的 client_message_id
        _cancel_remote(sess, targets)
        sess.message_id = ""
    with _LOCK:
        sess.stage = ""
        if user_cancel:
            sess.state = "cancelled"
        elif code:
            sess.state, sess.failure, sess.retry_after = "failed", code, retry_after
            if code not in REOPEN_ON and not open_remote:
                _REUSE = (sess.fingerprint, sess.client_message_id)
        else:
            sess.state = "done"
            # 信已送达的事实不能撤回；令牌存不下就如实标出来，界面换一句话，不再承诺「回信出现在这里」
            sess.inbox_saved = contactinbox.add(
                sess.message_id,
                sess.reply_token,
                sess.route,
                delivered=True,
                **_local_copy(sess.draft),
            )
        # 附件字节与令牌到此为止：重试由前端重新带稿、重新 init 拿回同一个 reply_token
        sess.draft = None
        sess.complete_token, sess.reply_token, sess.specs = "", "", []
        # 在锁里记：状态一放出去，读状态的人（界面 / 测试）就可能紧接着看日志
        _log(
            "ok" if sess.state == "done" else ("cancelled" if sess.state == "cancelled" else code),
            stage,
        )


def _local_copy(draft: Draft | None) -> dict:
    """本机留一份用户写的主题 / 正文 / 附件**文件名**（不含附件内容与邮箱；ADR 0145 第二期修订）。"""
    if draft is None:
        return {}
    return {
        "subject": draft.subject,
        "body": draft.body,
        "attachment_names": [a.name for a in draft.attachments],
    }


def _flow(sess: Session, targets: list[diagsend.Target]) -> None:
    draft = sess.draft
    if draft is None:
        raise _Failure("internal")
    _check_cancel(sess)
    _init(sess, targets, draft)
    for index, att in enumerate(() if sess.replayed else draft.attachments):
        _check_cancel(sess)
        _set_stage(sess, "upload")
        _upload(sess, index, att)
    _check_cancel(sess)
    _set_stage(sess, "complete")
    _complete(sess, targets)


def _init_body(sess: Session, draft: Draft, route: str) -> dict:
    body: dict = {
        "client_message_id": sess.client_message_id,
        "locale": draft.locale,
        "app_version": _version(),
        "os": telemetry._platform(),
        "arch": telemetry._arch(),
        "distribution": telemetry._distribution(),
        "route": route,
        "subject": draft.subject,
        "body": draft.body,
        "attachments": [
            {"name": a.name, "content_type": a.content_type, "size": a.size}
            for a in draft.attachments
        ],
    }
    if draft.reply_email:
        body["reply_email"] = draft.reply_email
    if draft.report_id:
        body["report_id"] = draft.report_id
    return body


def _version() -> str:
    from .. import __version__

    return __version__ if diagsend._VERSION_OK.match(__version__) else "unknown"


def _init(sess: Session, targets: list[diagsend.Target], draft: Draft) -> None:
    _set_stage(sess, "init")

    def call(tgt: diagsend.Target):
        return _post(
            tgt, "/v1/messages/init", _init_body(sess, draft, route_label(tgt)), API_TIMEOUT_S
        )

    resp, tgt = _failover(targets, sess.target, call)
    if resp.status == 409 and _server_code(resp) in ("session_closed", "idempotency_conflict"):
        # 这个幂等键对应的信已被关闭 / 内容对不上：换新的 client_message_id 重新开始（只此一次）
        with _LOCK:
            sess.client_message_id = str(uuid.uuid4())
        resp, tgt = _failover(targets, sess.target, call)
    if resp.status not in (200, 201):
        raise _error_of(resp)
    diagsend._check_api_version(resp)
    obj = diagsend._json_body(resp)
    message_id, reply_token = obj.get("message_id"), obj.get("reply_token")
    complete_token, atts = obj.get("complete_token"), obj.get("attachments")
    if (
        not isinstance(message_id, str)
        or not _MESSAGE_ID.match(message_id)
        or not isinstance(reply_token, str)
        or not _TOKEN.match(reply_token)
        or not isinstance(complete_token, str)
        or not _TOKEN.match(complete_token)
        or not isinstance(atts, list)
    ):
        raise _Failure("bad_response")
    # 已 complete 的信再 init 重放：200，但 attachments 是空数组（pending 才给同样数量的票据）——附件早已收下，直接 complete
    replayed = resp.status == 200 and not atts and bool(draft.attachments)
    if not replayed and len(atts) != len(draft.attachments):
        raise _Failure("bad_response")
    specs: list[diagsend.UploadSpec | None] = [None] * len(atts)
    for item in atts:
        idx = item.get("index") if isinstance(item, dict) else None
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(atts):
            raise _Failure("bad_response")
        if specs[idx] is not None:
            raise _Failure("bad_response")
        specs[idx] = _parse_upload(item.get("upload"), tgt, draft.attachments[idx].size)
    with _LOCK:
        sess.message_id, sess.reply_token, sess.complete_token = (
            message_id,
            reply_token,
            complete_token,
        )
        sess.target, sess.route = tgt, route_label(tgt)
        sess.specs = specs
        sess.replayed = replayed


def _parse_upload(raw: object, target: diagsend.Target, size: int) -> diagsend.UploadSpec:
    """契约的附件 `upload`：`{url, fields, file_field, max_bytes}`（无 method / content_type / expires_at）。
    地址不在白名单 → `upload_url_rejected`（不上传）；形状不对 → `bad_response`。"""
    if not isinstance(raw, dict):
        raise _Failure("bad_response")
    url = diagsend.validate_upload_url(raw.get("url"), target)
    fields_raw, file_field, max_bytes = (
        raw.get("fields"),
        raw.get("file_field"),
        raw.get("max_bytes"),
    )
    if (
        not isinstance(fields_raw, dict)
        or len(fields_raw) > diagsend.MAX_UPLOAD_FIELDS
        or not isinstance(file_field, str)
        or not diagsend._FIELD_NAME.match(file_field)
        or file_field in fields_raw
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
    ):
        raise _Failure("bad_response")
    fields: list[tuple[str, str]] = []
    for key, value in fields_raw.items():
        if not isinstance(key, str) or not diagsend._FIELD_NAME.match(key):
            raise _Failure("bad_response")
        if not isinstance(value, str) or len(value) > diagsend.MAX_FIELD_VALUE:
            raise _Failure("bad_response")
        fields.append((key, value))
    if max_bytes < size:
        raise _Failure("too_large")
    return diagsend.UploadSpec(url, tuple(fields), file_field, max_bytes, 0)


def _upload(sess: Session, index: int, att: Attachment) -> None:
    spec = sess.specs[index]
    if spec is None or not enabled():  # 发送途中开关被关了：不再往外发
        raise _Cancelled
    base = sum(a.size for a in (sess.draft.attachments[:index] if sess.draft else ()))

    def progress(sent: int, total: int) -> None:
        with _LOCK:
            # 字节数按附件原文算（不含 multipart 头尾），进度条单调且到 total 为止
            sess.sent = min(base + round(att.size * sent / max(total, 1)), sess.total)

    body = diagsend._MultipartBody(
        spec, att.data, sess.cancel, progress, filename=att.wire_name, content_type=att.content_type
    )
    req = urllib.request.Request(  # noqa: S310 — spec.url 已过 validate_upload_url
        spec.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": body.content_type,
            "Content-Length": str(body.length),
            "User-Agent": _UA,
        },
    )
    resp = diagsend._send(req, UPLOAD_TIMEOUT_S)
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


def _complete(sess: Session, targets: list[diagsend.Target]) -> None:
    payload = {"message_id": sess.message_id, "complete_token": sess.complete_token}
    with _LOCK:
        sess.complete_sent = (
            True  # 从这一刻起服务端那份可能已经 complete：取消不再生效，回信令牌先存下
        )
    contactinbox.add(
        sess.message_id,
        sess.reply_token,
        sess.route,
        delivered=False,
        **_local_copy(sess.draft),
    )
    last: _Failure | None = None
    for attempt in range(COMPLETE_ATTEMPTS):
        if attempt:
            wait = min(last.retry_after, 10) if last and last.retry_after else 0
            _sleep(wait or COMPLETE_BACKOFF_S[min(attempt - 1, len(COMPLETE_BACKOFF_S) - 1)])
        try:
            resp, tgt = _failover(
                targets,
                sess.target,
                lambda t: _post(t, "/v1/messages/complete", payload, COMPLETE_TIMEOUT_S),
            )
        except _Failure as fail:
            if fail.code in ("network", "timeout", "tls"):  # 响应可能丢在路上：同一份令牌幂等重试
                last = fail
                continue
            raise
        if resp.status == 200:
            diagsend._check_api_version(resp)
            obj = diagsend._json_body(resp)
            if obj.get("message_id") != sess.message_id or obj.get("state") not in (
                "new",
                "replied",
                "closed",
            ):
                raise _Failure("bad_response")
            with _LOCK:
                sess.target, sess.route = tgt, route_label(tgt)
            return
        fail = _error_of(resp)
        if resp.status >= 500:
            last = fail
            continue
        raise fail
    assert last is not None
    raise last


def _cancel_remote(sess: Session, targets: list[diagsend.Target]) -> None:
    _set_stage(sess, "cancel")
    payload = {"message_id": sess.message_id, "complete_token": sess.complete_token}
    try:
        _failover(
            targets,
            sess.target,
            lambda t: _post(t, "/v1/messages/cancel", payload, CANCEL_TIMEOUT_S),
        )
    except Exception:  # noqa: BLE001 — 尽力而为
        return


# ---------------------------------------------------------------------------
# 回信（轮询）
# ---------------------------------------------------------------------------
_LAST_POLL = 0.0
_POLL_LOCK = threading.Lock()


@dataclass
class _AssetBudget:
    """一轮轮询的附图下载预算。"""

    bytes_left: int = field(default_factory=lambda: ASSET_ROUND_BYTES)
    requests_left: int = field(default_factory=lambda: ASSET_ROUND_REQUESTS)


def _asset_content_type(resp: diagsend._Response) -> str:
    return resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _fetch_assets(rec: dict, targets: list[diagsend.Target], budget: _AssetBudget) -> bool:
    """下载这封信里还没落盘的回信附图。每张独立：失败（404 / 类型或魔数不符 / 大小不符）只跳过这一张，下一轮再试。
    返回 False = 线路不通或被限流（本轮到此为止）。线路 / TLS / 重定向 / UA / Bearer 与问回信同一套（`_get`）。"""
    mid = rec["message_id"]
    prefer = next((t for t in targets if route_label(t) == rec["route"]), None)
    for a in contactinbox.pending_assets(mid):
        if budget.requests_left <= 0 or budget.bytes_left <= 0 or budget.bytes_left < a["size"]:
            return True
        rid, n = a["reply_id"], a["n"]
        if not contactinbox.quota_allows(mid, a["size"]):
            # 本机落盘配额（每封 / 全部）已满：不下载、不再重试，信纸里这张显示占位
            contactinbox.mark_asset_skipped(mid, rid, n, "quota")
            continue
        budget.requests_left -= 1
        # 单次读取 ≤ min(单张上限, 本轮剩余预算) + 1：多出的 1 字节用来识别「超限」而不是被静默截断
        read_cap = min(contactinbox.MAX_ASSET_BYTES, budget.bytes_left) + 1
        spent = [0]
        try:
            resp, _ = _failover(
                targets,
                prefer,
                lambda t: _get(
                    t,
                    f"/v1/messages/{mid}/replies/{rid}/assets/{n}",
                    rec["reply_token"],
                    ASSET_TIMEOUT_S,
                    accept="image/*",
                    limit=read_cap,
                ),
                spent,
            )
        except _Failure:
            budget.bytes_left -= spent[0]  # 失败前已读到的（被换掉的线路的错误体）也要扣
            return False
        budget.bytes_left -= spent[
            0
        ]  # 按每条线路实际读到的字节扣（含备用前首选的错误体），不论后面校验过不过
        if resp.status == 429:
            return False
        if resp.status != 200:
            continue
        data = resp.body
        if (
            _asset_content_type(resp) != a["content_type"]
            or len(data) != a["size"]
            or len(data) > contactinbox.MAX_ASSET_BYTES
            or not _sniff_ok(a["content_type"], data)
        ):
            continue
        contactinbox.save_asset(mid, rid, n, data)
    return True


def _poll_failed(message_id: str, error: str) -> bool:
    """这封信这一轮没取到（响应超上限 / 不是合法回信 JSON）：不当成功，记下原因给界面，下一轮照常再试。
    日志只记编号与原因，不记响应内容。"""
    LOG.warning("联系作者: 取回信失败 (%s, %s)", message_id, error)
    contactinbox.set_poll_error(message_id, error)
    return True


def _poll_one(rec: dict, targets: list[diagsend.Target], budget: _AssetBudget) -> bool:
    """问一封信的回信并补下附图；返回 False = 线路都不通（本轮到此为止）。"""
    prefer = next((t for t in targets if route_label(t) == rec["route"]), None)
    try:
        resp, _ = _failover(
            targets,
            prefer,
            lambda t: _get(
                t,
                f"/v1/messages/{rec['message_id']}/replies",
                rec["reply_token"],
                POLL_TIMEOUT_S,
                limit=REPLIES_MAX_BYTES
                + 1,  # 多读 1 字节：读满上限 + 1 = 超限，而不是被静默截成坏 JSON
            ),
        )
    except _Failure:
        _log("network", "poll")
        return False
    if resp.status == 200:
        mid = rec["message_id"]
        if len(resp.body) > REPLIES_MAX_BYTES:
            return _poll_failed(mid, "too_large")
        try:
            obj = diagsend._json_body(resp)
        except _Failure:
            return _poll_failed(mid, "bad_response")
        replies, state = obj.get("replies"), obj.get("state")
        if obj.get("message_id") == mid and isinstance(replies, list):
            contactinbox.apply_replies(mid, state if isinstance(state, str) else "", replies)
            return _fetch_assets(rec, targets, budget)
        return _poll_failed(mid, "bad_response")
    elif resp.status == 429:
        return False
    return True


def poll_once(force: bool = False) -> dict:
    """问一遍所有未关闭的信有没有新回信。没有任何记录 = 一次网络都不发。`force` 以外最少隔 `REFRESH_MIN_S`。"""
    global _LAST_POLL
    targets = resolve_targets()
    recs = contactinbox.pollable() if targets else []
    # closed 的信不再问回信，但附图没下完的要补（只发附图请求）
    stale = contactinbox.with_pending_assets({r["message_id"] for r in recs}) if targets else []
    if not recs and not stale:
        return {"polled": 0}
    with _POLL_LOCK:
        now = time.monotonic()
        if not force and _LAST_POLL and now - _LAST_POLL < REFRESH_MIN_S:
            return {"polled": 0, "throttled": True}
        _LAST_POLL = now
        n = 0
        budget = _AssetBudget()
        for rec in recs:
            if not _poll_one(rec, targets, budget):
                break
            n += 1
        else:
            for rec in stale:
                if not _fetch_assets(rec, targets, budget):
                    break
    _log("ok", "poll")
    return {"polled": n}


_poller_started = threading.Event()
poll_wake = threading.Event()  # 测试 / 前端“刷新”之外的唤醒点


def start_poller() -> None:
    """启动时一次 + 每 6 小时一次的后台轮询（守护线程；功能关闭 / 没有记录时什么网络都不发）。幂等。"""
    if _poller_started.is_set() or _flag(ENV_HARD_OFF):
        return
    _poller_started.set()

    def loop() -> None:
        poll_wake.wait(POLL_STARTUP_DELAY_S)
        while True:
            poll_wake.clear()
            try:
                poll_once(force=True)
            except Exception:  # noqa: BLE001 — 后台探测不允许把主进程带下水
                pass
            poll_wake.wait(POLL_INTERVAL_S)

    threading.Thread(target=loop, name="tavotto-contact-poll", daemon=True).start()
