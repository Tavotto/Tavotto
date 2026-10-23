"""「联系作者」的本机收件记录（ADR 0145）：发出去的信的编号、回信令牌，以及取回的回信。

这是 `contactsend.py` 的**存储半边**，纯标准库、零网络。一条记录 = 一封已发（或已发出 complete）的信：

* `message_id`（`TVM-…`）、`reply_token`（64 位 hex，取回回信的唯一凭证）、`route`（成功的那条线路）、
  `sent_at`（epoch 秒）——契约要求客户端存的四样；
* 取回的回信（`id` / `created_at` / `body`：作者写给用户的话；有 HTML 时另有清洗过的 `html` 与附图元数据 `assets`）
  与「已读」的回信 id——让「我的来信」断网也能看、看过的不再亮红点；
* 第二期（ADR 0145 第二期修订）：用户自己写的 `subject` / `body` 与附件**文件名**（不含附件内容），让「我的来信」
  能把信与回信放在一起看；回信里的附图由 `contactsend` 下载后存在 `contact_assets/<message_id>/<reply_id>/<n>`，
  打开回信时 `reply_doc` 把它们内联成 `data:` URI，**不联网**。

**不存**回信邮箱与附件内容（邮箱的“本机记住上次填写”是前端的 localStorage，与这里无关）。文件在 `config.data_dir()`
下，权限 0600、资产目录 0700（尽力而为：Windows 上 chmod 只管只读位，所以另一道防线是——令牌对泄漏的后果只是
“有人能读到作者对你的回信”，且服务端 365 天后删除）。

保留期：`RETENTION_DAYS`（365）天后本机记录也删，与服务端同期。没完成投递（`delivered=False`）的记录只留
`UNDELIVERED_KEEP_S`：complete 的响应丢了又不知道服务端收没收，先存着，等轮询证实（200）再转正，否则到点清掉。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import atomicio, config

LOG = logging.getLogger("tavotto.contactinbox")

FILE_NAME = "contact_messages.json"
SCHEMA_VERSION = 1
RETENTION_DAYS = 365
UNDELIVERED_KEEP_S = 2 * 3600
#: 单封信最多缓存多少条回信 / 每条回信正文多长（服务端回信是作者手写的，不会大；这里只是防御性封顶）
MAX_REPLIES_PER_MESSAGE = 200
MAX_REPLY_CHARS = 20000
MAX_MESSAGES = 200
#: 本机留的原文上限（与 `contactsend.SUBJECT_MAX` / `BODY_MAX` 同值，`test_contact_inbox_limits_match_the_contract` 钉）
MAX_SUBJECT_CHARS = 120
MAX_BODY_CHARS = 5000
MAX_ATTACHMENT_NAMES = 5
MAX_ATTACHMENT_NAME_CHARS = 120
#: 回信 HTML（服务端已清洗）本机最多留多少字节；超了就只留纯文字 `body`
MAX_REPLY_HTML_BYTES = 256 * 1024
#: 附图：每封回信至多几张 / 单张字节上限 / 允许的类型（契约：image/png|jpeg|gif|webp）
MAX_ASSETS_PER_REPLY = 20
MAX_ASSET_BYTES = 10 * 1024 * 1024
ASSET_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
ASSET_DIR = "contact_assets"

_MESSAGE_ID = re.compile(r"^TVM-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_REPLY_ID = re.compile(r"^r_[0-9A-Za-z]{1,40}$")
_ASSET_N = re.compile(r"^(?:0|[1-9][0-9]?)$")
ROUTES = ("cn", "global")
_STATES = ("new", "replied", "closed")

_LOCK = threading.RLock()


def path() -> Path:
    return config.data_path(FILE_NAME)


def _now() -> float:
    return time.time()


def _blank() -> dict:
    return {"version": SCHEMA_VERSION, "messages": []}


def _valid_record(rec: object) -> bool:
    return (
        isinstance(rec, dict)
        and isinstance(rec.get("message_id"), str)
        and bool(_MESSAGE_ID.match(rec["message_id"]))
        and isinstance(rec.get("reply_token"), str)
        and bool(_TOKEN.match(rec["reply_token"]))
        and rec.get("route") in ROUTES
        and isinstance(rec.get("sent_at"), (int, float))
        and not isinstance(rec.get("sent_at"), bool)
    )


def _clean_replies(raw: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw[:MAX_REPLIES_PER_MESSAGE]:
        if not isinstance(item, dict):
            continue
        rid, created, body = item.get("id"), item.get("created_at"), item.get("body")
        if (
            isinstance(rid, str)
            and _REPLY_ID.match(rid)
            and isinstance(created, str)
            and len(created) <= 40
            and isinstance(body, str)
        ):
            reply = {"id": rid, "created_at": created, "body": body[:MAX_REPLY_CHARS]}
            html = item.get("html")
            if isinstance(html, str) and html and len(html.encode("utf-8")) <= MAX_REPLY_HTML_BYTES:
                assets = _clean_assets(item.get("assets"))
                reply["html"] = html
                reply["assets"] = assets
            out.append(reply)
    return out


def _clean_assets(raw: object) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    if not isinstance(raw, list):
        return out
    for a in raw[:MAX_ASSETS_PER_REPLY]:
        if not isinstance(a, dict):
            continue
        n, ctype, size = a.get("n"), a.get("content_type"), a.get("size")
        if (
            isinstance(n, int)
            and not isinstance(n, bool)
            and 0 <= n < MAX_ASSETS_PER_REPLY
            and n not in seen
            and ctype in ASSET_TYPES
            and isinstance(size, int)
            and not isinstance(size, bool)
            and 0 < size <= MAX_ASSET_BYTES
        ):
            seen.add(n)
            out.append({"n": n, "content_type": ctype, "size": size})
    return out


def _clean_names(raw: object) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    names = [x[:MAX_ATTACHMENT_NAME_CHARS] for x in raw if isinstance(x, str) and x]
    return names[:MAX_ATTACHMENT_NAMES]


def _normalize(rec: dict) -> dict:
    replies = _clean_replies(rec.get("replies"))
    ids = {r["id"] for r in replies}
    read = [x for x in (rec.get("read") or []) if isinstance(x, str) and x in ids]
    state = rec.get("state")
    return {
        "message_id": rec["message_id"],
        "reply_token": rec["reply_token"],
        "route": rec["route"],
        "sent_at": float(rec["sent_at"]),
        "subject": rec["subject"][:MAX_SUBJECT_CHARS]
        if isinstance(rec.get("subject"), str)
        else "",
        "body": rec["body"][:MAX_BODY_CHARS] if isinstance(rec.get("body"), str) else "",
        "attachment_names": _clean_names(rec.get("attachment_names")),
        "delivered": rec.get("delivered") is True,
        "state": state if state in _STATES else "",
        "replies": replies,
        "read": read,
    }


def _load_locked() -> dict:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list):
        return _blank()
    msgs = [_normalize(r) for r in raw["messages"] if _valid_record(r)]
    return {"version": SCHEMA_VERSION, "messages": msgs}


def _prune_locked(data: dict) -> None:
    now = _now()
    keep = []
    for rec in data["messages"]:
        age = now - rec["sent_at"]
        if age > RETENTION_DAYS * 86400:
            continue
        if not rec["delivered"] and age > UNDELIVERED_KEEP_S:
            continue
        keep.append(rec)
    kept = keep[-MAX_MESSAGES:]
    # 记录删了，它的附图目录一并删（保留期 / 条数上限 / 未送达到期都走这里）
    live = {m["message_id"] for m in kept}
    for rec in data["messages"]:
        if rec["message_id"] not in live:
            _remove_assets(rec["message_id"])
    data["messages"] = kept


def _save_locked(data: dict) -> bool:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        atomicio.write_bytes(path(), payload, mode=0o600)
    except OSError:
        # 写不下去不影响这次发送的结果：最坏是这封信没法在应用里收回信（邮箱那条路仍然通）。日志不带路径与异常文本。
        LOG.warning("联系作者: 本机收件记录写入失败")
        return False
    return True


def _load_pruned_locked() -> dict:
    data = _load_locked()
    before = len(data["messages"])
    _prune_locked(data)
    if len(data["messages"]) != before:
        _save_locked(data)
    return data


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def add(
    message_id: str,
    reply_token: str,
    route: str,
    *,
    delivered: bool,
    subject: str = "",
    body: str = "",
    attachment_names: tuple[str, ...] | list[str] = (),
) -> bool:
    """登记一封信（幂等：同编号已有则只升级 `delivered`、补上还没存的原文，不覆盖令牌与已取回的回信）。
    `subject` / `body` / `attachment_names`：用户写的原文与附件**文件名**（上限见 `MAX_*`，超出截断）。
    返回令牌此刻是否已在本机落盘；写不下去（目录不可写 / 盘满）返回 False，调用方据此告诉用户「应用里收不到回信」。"""
    rec = {
        "message_id": message_id,
        "reply_token": reply_token,
        "route": route,
        "sent_at": _now(),
        "subject": subject,
        "body": body,
        "attachment_names": list(attachment_names),
    }
    if not _valid_record(rec):
        return False
    with _LOCK:
        data = _load_pruned_locked()
        for cur in data["messages"]:
            if cur["message_id"] == message_id:
                changed = False
                if delivered and not cur["delivered"]:
                    cur["delivered"] = changed = True
                fresh = _normalize({**rec, "delivered": delivered})
                for key in ("subject", "body", "attachment_names"):
                    if not cur[key] and fresh[key]:
                        cur[key], changed = fresh[key], True
                return _save_locked(data) if changed else True
        data["messages"].append(_normalize({**rec, "delivered": delivered}))
        return _save_locked(data)


def mark_delivered(message_id: str) -> None:
    with _LOCK:
        data = _load_locked()
        for cur in data["messages"]:
            if cur["message_id"] == message_id and not cur["delivered"]:
                cur["delivered"] = True
                _save_locked(data)
                return


def apply_replies(message_id: str, state: str, replies: list[dict]) -> int:
    """把一次轮询取回的回信并进记录，返回新增条数。已有的回信（按 id）不重复。"""
    with _LOCK:
        data = _load_locked()
        for cur in data["messages"]:
            if cur["message_id"] != message_id:
                continue
            have = {r["id"] for r in cur["replies"]}
            fresh = [r for r in _clean_replies(replies) if r["id"] not in have]
            changed = (
                bool(fresh) or not cur["delivered"] or (state in _STATES and cur["state"] != state)
            )
            cur["delivered"] = True
            cur["replies"] = (cur["replies"] + fresh)[-MAX_REPLIES_PER_MESSAGE:]
            if state in _STATES:
                cur["state"] = state
            if changed:
                _save_locked(data)
            return len(fresh)
    return 0


def mark_read(message_id: str) -> bool:
    """这封信现有的回信全部标已读（看过即已读）。"""
    with _LOCK:
        data = _load_locked()
        for cur in data["messages"]:
            if cur["message_id"] == message_id:
                ids = [r["id"] for r in cur["replies"]]
                if set(ids) <= set(cur["read"]):
                    return False
                cur["read"] = ids
                _save_locked(data)
                return True
    return False


def forget(message_id: str) -> bool:
    """从本机记录里删掉一封信（只删本机；服务端仍按 365 天保留期处理）。"""
    with _LOCK:
        data = _load_locked()
        before = len(data["messages"])
        data["messages"] = [m for m in data["messages"] if m["message_id"] != message_id]
        if len(data["messages"]) != before:
            _save_locked(data)
            _remove_assets(message_id)
            return True
    return False


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def pollable() -> list[dict]:
    """需要去服务端问回信的记录（含令牌——只在引擎内部用，**不得**进任何 HTTP 响应 / 日志）。"""
    with _LOCK:
        return [dict(m) for m in _load_pruned_locked()["messages"] if m["state"] != "closed"]


def _unread(rec: dict) -> int:
    seen = set(rec["read"])
    return sum(1 for r in rec["replies"] if r["id"] not in seen)


def public_messages() -> list[dict]:
    """给界面的列表：**不含令牌**。含用户自己写的主题 / 正文 / 附件文件名（本机存的那一份，旧记录没有就是空），
    回信只带 `has_html`（HTML 本体走 `reply_doc`，不进列表）。新的在前。"""
    with _LOCK:
        data = _load_pruned_locked()
    out = []
    for m in data["messages"]:
        if not m["delivered"]:
            continue
        seen = set(m["read"])
        out.append(
            {
                "message_id": m["message_id"],
                "sent_at": int(m["sent_at"]),
                "route": m["route"],
                "state": m["state"] or "new",
                "unread": _unread(m),
                "subject": m["subject"],
                "body": m["body"],
                "attachment_names": list(m["attachment_names"]),
                "replies": [
                    {
                        "id": r["id"],
                        "created_at": r["created_at"],
                        "body": r["body"],
                        "has_html": "html" in r,
                        "read": r["id"] in seen,
                    }
                    for r in m["replies"]
                ],
            }
        )
    out.sort(key=lambda x: x["sent_at"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# 回信附图（`contact_assets/<message_id>/<reply_id>/<n>`；目录 0700、文件 0600）
# ---------------------------------------------------------------------------
def _assets_root() -> Path:
    return config.data_path(ASSET_DIR)


def asset_file(message_id: str, reply_id: str, n: int) -> Path | None:
    """附图的本机路径；任何一段不合规（路径穿越的唯一防线）返回 None。"""
    if not (_MESSAGE_ID.match(message_id) and _REPLY_ID.match(reply_id) and _ASSET_N.match(str(n))):
        return None
    return _assets_root() / message_id / reply_id / str(n)


def _remove_assets(message_id: str) -> None:
    if not _MESSAGE_ID.match(message_id):
        return
    shutil.rmtree(_assets_root() / message_id, ignore_errors=True)


def save_asset(message_id: str, reply_id: str, n: int, data: bytes) -> bool:
    """原子写一张附图。调用方已核过类型 / 魔数 / 大小；这里只管落盘与权限。"""
    dest = asset_file(message_id, reply_id, n)
    if dest is None or len(data) > MAX_ASSET_BYTES:
        return False
    with _LOCK:
        # 信已被删（forget / 保留期）就不再写：下载途中被用户删掉的信不该留下孤儿目录
        if not any(m["message_id"] == message_id for m in _load_locked()["messages"]):
            return False
        try:
            for d in (dest.parent.parent, dest.parent):
                d.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(d, 0o700)
                except OSError:
                    pass
            atomicio.write_bytes(dest, data, mode=0o600)
        except OSError:
            LOG.warning("联系作者: 回信附图写入失败")
            return False
    return True


def pending_assets(message_id: str) -> list[dict]:
    """这封信里元数据有、本机文件还没有的附图：`{reply_id, n, content_type, size}`。"""
    with _LOCK:
        data = _load_locked()
    out = []
    for m in data["messages"]:
        if m["message_id"] != message_id:
            continue
        for r in m["replies"]:
            for a in r.get("assets", []):
                f = asset_file(message_id, r["id"], a["n"])
                if f is not None and not f.is_file():
                    out.append({"reply_id": r["id"], **a})
    return out


def with_pending_assets(skip: set[str]) -> list[dict]:
    """已 closed（不再问回信）但附图没下完的记录——只为补下载；`skip` 是已经在轮询名单里的编号。"""
    with _LOCK:
        recs = [
            dict(m)
            for m in _load_pruned_locked()["messages"]
            if m["state"] == "closed" and m["message_id"] not in skip
        ]
    return [m for m in recs if pending_assets(m["message_id"])]


# ---------------------------------------------------------------------------
# 信纸：把清洗过的回信 HTML 拼成可以直接放进沙盒 iframe 的完整文档
# ---------------------------------------------------------------------------
#: 文档自带的内容安全策略：什么都不许连，图只认 `data:`（附图已内联），样式允许内联（回信用 style 属性）。
REPLY_DOC_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
_REPLY_DOC_STYLE = (
    "html{color-scheme:light}"
    "body{margin:0;padding:16px;overflow-wrap:anywhere;background:#fff;color:#1f2328;"
    'font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",'
    "Roboto,Helvetica,Arial,sans-serif}"
    "a{color:#0b57d0}"
    "img{max-width:100%!important;height:auto!important}"
    "table{max-width:100%!important}"
    "pre,code{white-space:pre-wrap;word-break:break-word}"
)
#: 附图缺失（还没下完）时的占位：1x1 透明 GIF，`alt` 仍在
_PLACEHOLDER = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
_ASSET_SRC = re.compile(r"""(\bsrc\s*=\s*)(["'])asset:([0-9]{1,2})\2""", re.IGNORECASE)


def reply_doc(message_id: str, reply_id: str) -> str | None:
    """这封回信的完整 HTML 文档；回信不存在或没有 HTML 返回 None。纯本机读盘，不联网。"""
    with _LOCK:
        data = _load_pruned_locked()
    for m in data["messages"]:
        if m["message_id"] != message_id:
            continue
        for r in m["replies"]:
            if r["id"] != reply_id or "html" not in r:
                continue
            types = {a["n"]: a["content_type"] for a in r["assets"]}

            def inline(mo: re.Match[str]) -> str:
                n = int(mo.group(3))
                f = asset_file(message_id, reply_id, n)
                uri = _PLACEHOLDER
                if f is not None and n in types:
                    try:
                        raw = f.read_bytes()
                    except OSError:
                        raw = b""
                    if raw and len(raw) <= MAX_ASSET_BYTES:
                        uri = f"data:{types[n]};base64,{base64.b64encode(raw).decode('ascii')}"
                return f'{mo.group(1)}"{uri}"'

            return (
                '<!doctype html><html><head><meta charset="utf-8">'
                f'<meta http-equiv="Content-Security-Policy" content="{REPLY_DOC_CSP}">'
                # 服务器已给链接强制 target=_blank；<base> 兜底没带 target 的链接（否则会在 iframe 里就地导航）
                f'<base target="_blank"><style>{_REPLY_DOC_STYLE}</style></head><body>'
                f"{_ASSET_SRC.sub(inline, r['html'])}</body></html>"
            )
    return None


def unread_count() -> int:
    with _LOCK:
        data = _load_pruned_locked()
    return sum(_unread(m) for m in data["messages"] if m["delivered"])


def reset_for_tests() -> None:
    with _LOCK:
        try:
            os.remove(path())
        except OSError:
            pass
