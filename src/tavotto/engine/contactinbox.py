"""「联系作者」的本机收件记录（ADR 0145）：发出去的信的编号、回信令牌，以及取回的回信。

这是 `contactsend.py` 的**存储半边**，纯标准库、零网络。一条记录 = 一封已发（或已发出 complete）的信：

* `message_id`（`TVM-…`）、`reply_token`（64 位 hex，取回回信的唯一凭证）、`route`（成功的那条线路）、
  `sent_at`（epoch 秒）——契约要求客户端存的四样；
* 取回的回信（`id` / `created_at` / `body`：作者写给用户的话）与「已读」的回信 id——让「我的来信」断网也能看、
  看过的不再亮红点。

**不存**用户写的正文、主题、回信邮箱、附件（它们只在发送那一次的内存里；邮箱的“本机记住上次填写”是前端
的 localStorage，与这里无关）。文件在 `config.data_dir()` 下，权限 0600（尽力而为：Windows 上 chmod 只管只读位，
所以另一道防线是——令牌对泄漏的后果只是“有人能读到作者对你的回信”，且服务端 365 天后删除）。

保留期：`RETENTION_DAYS`（365）天后本机记录也删，与服务端同期。没完成投递（`delivered=False`）的记录只留
`UNDELIVERED_KEEP_S`：complete 的响应丢了又不知道服务端收没收，先存着，等轮询证实（200）再转正，否则到点清掉。
"""

from __future__ import annotations

import json
import logging
import os
import re
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

_MESSAGE_ID = re.compile(r"^TVM-[0-9A-HJKMNP-TV-Z]{4}(?:-[0-9A-HJKMNP-TV-Z]{4}){3}$")
_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_REPLY_ID = re.compile(r"^r_[0-9A-Za-z]{1,40}$")
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
            out.append({"id": rid, "created_at": created, "body": body[:MAX_REPLY_CHARS]})
    return out


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
    data["messages"] = keep[-MAX_MESSAGES:]


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
def add(message_id: str, reply_token: str, route: str, *, delivered: bool) -> bool:
    """登记一封信（幂等：同编号已有则只升级 `delivered`、不覆盖令牌与已取回的回信）。
    返回令牌此刻是否已在本机落盘；写不下去（目录不可写 / 盘满）返回 False，调用方据此告诉用户「应用里收不到回信」。"""
    rec = {
        "message_id": message_id,
        "reply_token": reply_token,
        "route": route,
        "sent_at": _now(),
    }
    if not _valid_record(rec):
        return False
    with _LOCK:
        data = _load_pruned_locked()
        for cur in data["messages"]:
            if cur["message_id"] == message_id:
                if delivered and not cur["delivered"]:
                    cur["delivered"] = True
                    return _save_locked(data)
                return True
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
    """给界面的列表：**不含令牌**，也不含任何用户写的内容（我们本来就没存）。新的在前。"""
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
                "replies": [{**r, "read": r["id"] in seen} for r in m["replies"]],
            }
        )
    out.sort(key=lambda x: x["sent_at"], reverse=True)
    return out


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
