"""「外部审阅」的本机分享记录（ADR 0187 §七）：分享出去的链接、管理令牌、各版本的元素表与快照、意见副本、结论、游标、未读数。

这是 `reviewshare.py` 的**存储半边**，纯标准库、零网络。文件在 `config.data_dir()/review/`：

* `shares.json`：所有记录（权限 0600，尽力而为）；
* `snapshots/<share_id>/<v>.png`：各版本快照 PNG（目录 0700、文件 0600）——「原位置」缩略图读它，不联网；
* （设备密钥 `device.key` 由 `reviewshare` 管，同一目录。）

**`owner_token` 只在这个文件里**：`public()` / `detail()` / 日志都没有它，只有 `owner_token_of()` 给 `reviewshare` 发请求时取。
链接到期（或停止分享）后意见副本仍留在本机，直到到期 + `KEEP_AFTER_EXPIRY_DAYS` 天或用户删除。
没完成投递（`delivered=False`：init 拿到了令牌但 complete 没成）的记录只留 `UNDELIVERED_KEEP_S`，不出现在列表里、不被轮询。
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import atomicio, config

LOG = logging.getLogger("tavotto.reviewinbox")

DIR_NAME = "review"
FILE_NAME = "shares.json"
SNAPSHOT_DIR = "snapshots"
SCHEMA_VERSION = 1
RETENTION_DAYS = 30
KEEP_AFTER_EXPIRY_DAYS = 30
#: 与服务端 `client_*_id` 的 24 小时幂等重放窗口一致：init 已拿到管理令牌、本机却没走完（complete 成了但落盘失败 / 中途断了）的
#: 记录留这么久，期间用户重试能补投递，不会连令牌一起丢掉一条已发布的链接
UNDELIVERED_KEEP_S = 24 * 3600
MAX_SHARES = 200
KINDS = ("figure", "layout")

# 与服务端契约一致的上限（wire-api / ADR 0187 §五）
TITLE_MAX = 80
NOTE_MAX = 200
TEXT_MAX = 500
AUTHOR_MAX = 20
MAX_COMMENTS = 500
MAX_REPLIES_PER_COMMENT = 200
MAX_VERDICTS = 500
MAX_VERSIONS = 64

SHARE_ID = re.compile(r"^rs_[A-Za-z2-7]{16}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OWNER_TOKEN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_TARGET_ID = re.compile(r"^[^\x00-\x1f]{1,512}$")

_LOCK = threading.RLock()

# ---------------------------------------------------------------------------
# 跨进程锁（ADR 0205 §四）：桌面与插件的 MCP 进程可能同时写同一份 `shares.json`。
# 写路径一律「进程内锁 → 文件锁 → 重新读盘 → 改 → 原子写回」；只读路径不加文件锁。
# ---------------------------------------------------------------------------
LOCK_NAME = "shares.lock"
LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_S = 0.02
#: 本进程持有文件锁的重入深度；只在持有 `_LOCK` 的线程里读写，所以不必另加锁
_depth = 0


def lock_path() -> Path:
    return dir_path() / LOCK_NAME


def _try_lock(fh) -> bool:
    """非阻塞地试一次；拿到 True，被别的进程占着 False。"""
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):
        return False
    return True


def _unlock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass  # 关文件时内核也会放


@contextlib.contextmanager
def file_lock(timeout: float | None = None):
    """`review/shares.lock` 上的跨进程互斥（进程内可重入）。超时抛 `TimeoutError`（是 `OSError`）——
    调用方按写盘失败处理，绝不跳过锁直接写。"""
    global _depth
    with _LOCK:
        if _depth:
            _depth += 1
            try:
                yield
            finally:
                _depth -= 1
            return
        p = lock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.parent.chmod(0o700)
        except OSError:
            pass
        fh = open(p, "a+b")  # noqa: SIM115 — 生命周期跨 yield
        try:
            deadline = time.monotonic() + (LOCK_TIMEOUT_S if timeout is None else timeout)
            while not _try_lock(fh):
                if time.monotonic() >= deadline:
                    raise TimeoutError("review inbox lock timeout")
                time.sleep(_LOCK_POLL_S)
            _depth = 1
            try:
                yield
            finally:
                _depth = 0
                _unlock(fh)
        finally:
            fh.close()


def dir_path() -> Path:
    return config.data_path(DIR_NAME)


def path() -> Path:
    return dir_path() / FILE_NAME


def snapshot_path(share_id: str, v: int) -> Path:
    return dir_path() / SNAPSHOT_DIR / share_id / f"{int(v)}.png"


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: object, default: float) -> float:
    """服务端 ISO 8601 UTC → epoch 秒；坏值回 `default`。"""
    if not isinstance(value, str) or not value or len(value) > 40:
        return default
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _blank() -> dict:
    return {"version": SCHEMA_VERSION, "shares": []}


# ---------------------------------------------------------------------------
# 形状清洗（意见副本来自网络：逐项校验、文字封顶、当纯文本存）
# ---------------------------------------------------------------------------
def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.replace("\x00", "")
    return clean[:limit]


def _box(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out.append(float(v))
    return out


def clean_anchor(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind == "overall":
        return {"kind": "overall"}
    box = _box(raw.get("box"))
    if box is None:
        return None
    if kind == "region":
        return {"kind": "region", "box": box}
    gid = raw.get("gid")
    if kind == "element" and isinstance(gid, str) and 0 < len(gid) <= 256:
        out: dict = {"kind": "element", "gid": gid, "box": box}
        pt = _point(raw.get("pt"))
        if pt is not None:
            out["pt"] = pt  # 审阅者点在元素上的那一处（画面比例坐标）；老网页没有
        return out
    return None


def _point(value: object) -> list[float] | None:
    """画面比例坐标的一个点 `[x, y]`，夹进 [0,1]²；不是两个有限数 None。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out.append(round(min(1.0, max(0.0, float(v))), 5))
    return out


def clean_reply(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    rid, frm = raw.get("id"), raw.get("from")
    author, text, created = (
        raw.get("author"),
        _text(raw.get("text"), TEXT_MAX),
        raw.get("created_at"),
    )
    if (
        not isinstance(rid, str)
        or not _ID.match(rid)
        or frm not in ("reviewer", "owner")
        or not isinstance(author, str)
        or text is None
        or not isinstance(created, str)
        or len(created) > 40
    ):
        return None
    return {
        "id": rid,
        "from": frm,
        "author": author[:AUTHOR_MAX],
        "text": text,
        "created_at": created,
    }


def clean_comment(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    cid, v = raw.get("id"), raw.get("v")
    anchor = clean_anchor(raw.get("anchor"))
    author, text = raw.get("author"), _text(raw.get("text"), TEXT_MAX)
    created = raw.get("created_at")
    if (
        not isinstance(cid, str)
        or not _ID.match(cid)
        or isinstance(v, bool)
        or not isinstance(v, int)
        or v < 1
        or anchor is None
        or not isinstance(author, str)
        or text is None
        or not isinstance(created, str)
        or len(created) > 40
    ):
        return None
    replies: list[dict] = []
    seen: set[str] = set()
    raw_replies = raw.get("replies")
    for item in (raw_replies if isinstance(raw_replies, list) else [])[:MAX_REPLIES_PER_COMMENT]:
        r = clean_reply(item)
        if r is not None and r["id"] not in seen:
            seen.add(r["id"])
            replies.append(r)
    return {
        "id": cid,
        "v": v,
        "anchor": anchor,
        "author": author[:AUTHOR_MAX],
        "text": text,
        "resolved": raw.get("resolved") is True,
        "created_at": created,
        "replies": replies,
    }


def clean_verdict(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    v, decision, author, at = raw.get("v"), raw.get("decision"), raw.get("author"), raw.get("at")
    note = _text(raw.get("note", ""), NOTE_MAX)
    if (
        isinstance(v, bool)
        or not isinstance(v, int)
        or v < 1
        or decision not in ("approved", "changes")
        or not isinstance(author, str)
        or note is None
        or not isinstance(at, str)
        or len(at) > 40
    ):
        return None
    return {"v": v, "decision": decision, "note": note, "author": author[:AUTHOR_MAX], "at": at}


#: 元素表里的角色名（manifest `role`）：小写字母 + 下划线
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
_GEOM_KINDS = frozenset({"polyline", "path", "multi_path"})


def _order(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def clean_geometry(raw: object) -> dict | None:
    """元素的真实形状（与 manifest `geometry` 同形，ADR 0187 修订 2026-11-16）：只认已知字段，坐标须是有限数。
    路径少于 2 个点的丢掉；一条都不剩 None（该元素退回按 bbox 点选）。"""
    if not isinstance(raw, dict) or raw.get("kind") not in _GEOM_KINDS:
        return None
    paths: list[dict] = []
    for p in raw.get("paths") if isinstance(raw.get("paths"), list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("points"), list):
            continue
        pts = [q for q in (_point_raw(v) for v in p["points"]) if q is not None]
        if len(pts) >= 2 and len(pts) == len(p["points"]):
            paths.append({"points": pts, "closed": p.get("closed") is True})
    if not paths:
        return None
    out: dict = {
        "kind": raw["kind"],
        "paths": paths,
        "fill": raw.get("fill") is True,
        "stroke": raw.get("stroke") is True,
    }
    sp = raw.get("stroke_pt")
    if (
        isinstance(sp, (int, float))
        and not isinstance(sp, bool)
        and math.isfinite(sp)
        and 0 <= sp <= 100
    ):
        out["stroke_pt"] = round(float(sp), 3)
    clip = _box(raw.get("clip"))
    if clip is not None:
        out["clip"] = [round(v, 5) for v in clip]
    order = _order(raw.get("paint_order"))
    if order is not None:
        out["paint_order"] = order
    fa = raw.get("fill_alpha")
    if isinstance(fa, (int, float)) and not isinstance(fa, bool) and 0 <= fa <= 1:
        out["fill_alpha"] = round(float(fa), 3)
    return out


def _point_raw(value: object) -> list[float] | None:
    """不夹值的点（路径点已由上传端裁进画面）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    out: list[float] = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out.append(float(v))
    return out


def clean_elements(raw: object) -> list[dict]:
    """`[{gid, label, bbox}]` + 可选 `role` / `paint_order` / `arrow_endpoints` / `geometry`（点选细到真实形状，
    ADR 0187 修订 2026-11-16）。多余字段一律丢。"""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        gid, label, box = item.get("gid"), item.get("label"), _box(item.get("bbox"))
        if not (
            isinstance(gid, str) and gid and len(gid) <= 256 and isinstance(label, str) and box
        ):
            continue
        el: dict = {"gid": gid, "label": label[:120], "bbox": box}
        role = item.get("role")
        if isinstance(role, str) and _ROLE.match(role):
            el["role"] = role
        order = _order(item.get("paint_order"))
        if order is not None:
            el["paint_order"] = order
        ends = item.get("arrow_endpoints")
        if isinstance(ends, list) and len(ends) == 2:
            pts = [_point(v) for v in ends]
            if all(pts):
                el["arrow_endpoints"] = pts
        geom = clean_geometry(item.get("geometry"))
        if geom is not None:
            el["geometry"] = geom
        out.append(el)
    return out


def _clean_version(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    v, created = raw.get("v"), raw.get("created_at")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1 or not isinstance(created, str):
        return None
    frame = raw.get("frame") if raw.get("frame") in ("page", "figure") else "figure"
    return {
        "v": v,
        "created_at": created[:40],
        "frame": frame,
        "digest": raw["digest"]
        if isinstance(raw.get("digest"), str) and re.fullmatch(r"[0-9a-f]{64}", raw["digest"])
        else "",
        "elements": clean_elements(raw.get("elements")),
    }


def _int(value: object, default: int = 0) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default
    )


def _num(value: object, default: float) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else default


def _valid_record(rec: object) -> bool:
    return (
        isinstance(rec, dict)
        and isinstance(rec.get("share_id"), str)
        and bool(SHARE_ID.match(rec["share_id"]))
        and isinstance(rec.get("owner_token"), str)
        and bool(_OWNER_TOKEN.match(rec["owner_token"]))
        and isinstance(rec.get("review_url"), str)
        and rec.get("kind") in KINDS
        and isinstance(rec.get("target_id"), str)
        and bool(_TARGET_ID.match(rec["target_id"]))
    )


def _normalize(rec: dict) -> dict:
    created = _num(rec.get("created_ts"), _now())
    comments: list[dict] = []
    seen: set[str] = set()
    for c in (rec.get("comments") if isinstance(rec.get("comments"), list) else [])[:MAX_COMMENTS]:
        cc = clean_comment(c)
        if cc is not None and cc["id"] not in seen:
            seen.add(cc["id"])
            comments.append(cc)
    verdicts = [
        x
        for x in (clean_verdict(v) for v in (rec.get("verdicts") or [])[:MAX_VERDICTS])
        if x is not None
    ]
    versions = [
        x
        for x in (_clean_version(v) for v in (rec.get("versions") or [])[:MAX_VERSIONS])
        if x is not None
    ]
    latest = max([_int(rec.get("latest_version"), 1), *(v["v"] for v in versions)])
    return {
        "share_id": rec["share_id"],
        "owner_token": rec["owner_token"],
        "review_url": rec["review_url"],
        "kind": rec["kind"],
        "target_id": rec["target_id"],
        "project": rec["project"][:64] if isinstance(rec.get("project"), str) else "",
        "title": (_text(rec.get("title", ""), TITLE_MAX) or ""),
        "note": (_text(rec.get("note", ""), NOTE_MAX) or ""),
        "created_ts": created,
        "updated_ts": _num(rec.get("updated_ts"), created),
        "expires_ts": _num(rec.get("expires_ts"), created + RETENTION_DAYS * 86400),
        "latest_version": latest,
        "revoked": rec.get("revoked") is True,
        "delivered": rec.get("delivered") is True,
        "versions": versions,
        "comments": comments,
        "verdicts": verdicts,
        "cursor": rec["cursor"][:512] if isinstance(rec.get("cursor"), str) else "",
        "unread": _int(rec.get("unread")),
        "last_unread_author": (_text(rec.get("last_unread_author", ""), AUTHOR_MAX) or ""),
        "last_unread_ts": _num(rec.get("last_unread_ts"), 0.0),
    }


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------
def _load_locked() -> dict:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _blank()
    if not isinstance(raw, dict) or not isinstance(raw.get("shares"), list):
        return _blank()
    return {
        "version": SCHEMA_VERSION,
        "shares": [_normalize(r) for r in raw["shares"] if _valid_record(r)],
    }


def _save_locked(data: dict, *, strict: bool = False) -> bool:
    """落盘。`strict=True`（登记令牌 / 登记版本这两处，远端已经发布了）写不下去就**抛 OSError**，由调用方把这次分享判失败；
    其余（未读数、游标之类可重建的东西）只记日志返回 False。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        atomicio.write_bytes(path(), payload, mode=0o600)
    except OSError:
        LOG.warning("外部审阅: 本机分享记录写入失败")  # 不带路径与异常文本
        if strict:
            raise
        return False
    return True


def _remove_snapshots(share_id: str) -> None:
    if not SHARE_ID.match(share_id):
        return
    shutil.rmtree(dir_path() / SNAPSHOT_DIR / share_id, ignore_errors=True)


def _prune_locked(data: dict, *, remove_files: bool = True) -> None:
    now = _now()
    keep = []
    for rec in data["shares"]:
        if now > rec["expires_ts"] + KEEP_AFTER_EXPIRY_DAYS * 86400:
            continue
        if not rec["delivered"] and now - rec["created_ts"] > UNDELIVERED_KEEP_S:
            continue
        keep.append(rec)
    keep = keep[-MAX_SHARES:]
    live = {r["share_id"] for r in keep}
    for rec in data["shares"]:
        if remove_files and rec["share_id"] not in live:
            _remove_snapshots(rec["share_id"])
    data["shares"] = keep


def _load_pruned_locked() -> dict:
    data = _load_locked()
    before = len(data["shares"])
    probe = {"shares": list(data["shares"])}
    _prune_locked(probe, remove_files=False)
    if len(probe["shares"]) == before:
        return data
    # 修剪要写盘：读路径本身不加文件锁，要写才在锁里重读一次再修剪。拿不到锁就只给这次的内存视图，不落盘（下次再剪）。
    try:
        with file_lock():
            data = _load_locked()
            before = len(data["shares"])
            _prune_locked(data)
            if len(data["shares"]) != before:
                _save_locked(data)
    except OSError:
        _prune_locked(data, remove_files=False)
    return data


def _find(data: dict, share_id: str) -> dict | None:
    return next((r for r in data["shares"] if r["share_id"] == share_id), None)


def _unread_cap(rec: dict) -> int:
    return len(rec["comments"]) + sum(len(c["replies"]) for c in rec["comments"])


# ---------------------------------------------------------------------------
# 公开形状（local-api.md 的 ShareSummary / ShareDetail；没有 owner_token）
# ---------------------------------------------------------------------------
def is_expired(rec: dict, now: float | None = None) -> bool:
    return (_now() if now is None else now) >= rec["expires_ts"]


def summary(rec: dict) -> dict:
    return {
        "share_id": rec["share_id"],
        "review_url": rec["review_url"],
        "kind": rec["kind"],
        "target_id": rec["target_id"],
        "title": rec["title"],
        "note": rec["note"],
        "latest_version": rec["latest_version"],
        "created_at": _iso(rec["created_ts"]),
        "updated_at": _iso(rec["updated_ts"]),
        "expires_at": _iso(rec["expires_ts"]),
        "revoked": rec["revoked"],
        "expired": is_expired(rec),
        "comment_count": len(rec["comments"]),
        "open_count": sum(1 for c in rec["comments"] if not c["resolved"]),
        "unread": min(rec["unread"], _unread_cap(rec)),
    }


def detail(rec: dict) -> dict:
    out = summary(rec)
    out["versions"] = [
        {"v": v["v"], "created_at": v["created_at"], "frame": v["frame"], "elements": v["elements"]}
        for v in sorted(rec["versions"], key=lambda x: x["v"])
    ]
    out["comments"] = rec["comments"]
    out["verdicts"] = rec["verdicts"]
    return out


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def get(share_id: str) -> dict | None:
    """一份记录的拷贝（含 owner_token，只给 `reviewshare` 内部用；对外一律 `summary` / `detail`）。"""
    with _LOCK:
        rec = _find(_load_pruned_locked(), share_id)
        return json.loads(json.dumps(rec)) if rec is not None else None


def owner_token_of(share_id: str) -> str:
    rec = get(share_id)
    return rec["owner_token"] if rec else ""


def listed() -> list[dict]:
    """列表里可见的记录（已投递的）。"""
    with _LOCK:
        return [r for r in _load_pruned_locked()["shares"] if r["delivered"]]


def for_target(kind: str, target_id: str, project: str = "") -> dict | None:
    """这个 target 的分享记录：优先仍有效的，其次最近更新的；没有 None。图 id / 排版 id 在不同项目里会重名，
    所以按**分享时所在的项目**（`project`，没开项目 = `""`）一起匹配。"""
    with _LOCK:
        recs = [
            r
            for r in _load_pruned_locked()["shares"]
            if r["delivered"]
            and r["kind"] == kind
            and r["target_id"] == target_id
            and r["project"] == project
        ]
    if not recs:
        return None
    live = [r for r in recs if not r["revoked"] and not is_expired(r)]
    pool = live or recs
    return max(pool, key=lambda r: r["updated_ts"])


def pollable() -> list[dict]:
    """要问 feed 的记录：已投递、没停止、没到期。**空 = 一次网络都不发。**"""
    now = _now()
    return [r for r in listed() if not r["revoked"] and not is_expired(r, now)]


def unread_summary() -> dict:
    with _LOCK:
        recs = [
            summary(r) | {"_ts": r["last_unread_ts"], "_a": r["last_unread_author"]}
            for r in listed()
        ]
    total = sum(r["unread"] for r in recs)
    withnew = [r for r in recs if r["unread"] > 0]
    latest = None
    if withnew:
        top = max(withnew, key=lambda r: r["_ts"])
        latest = {"share_id": top["share_id"], "author": top["_a"], "count": top["unread"]}
    return {"total": total, "latest": latest}


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def add_pending(
    *,
    share_id: str,
    owner_token: str,
    review_url: str,
    kind: str,
    target_id: str,
    title: str,
    note: str,
    expires_ts: float,
    project: str = "",
) -> bool:
    """init 一拿到令牌就登记（complete 之前）：complete 的响应丢了也不会丢管理令牌。幂等：同编号已有则不动。"""
    now = _now()
    rec = {
        "share_id": share_id,
        "owner_token": owner_token,
        "review_url": review_url,
        "kind": kind,
        "target_id": target_id,
        "project": project,
        "title": title,
        "note": note,
        "created_ts": now,
        "updated_ts": now,
        "expires_ts": expires_ts,
        "latest_version": 1,
        "delivered": False,
    }
    if not _valid_record(rec):
        return False
    with file_lock():
        data = _load_pruned_locked()
        if _find(data, share_id) is not None:
            return True
        data["shares"].append(_normalize(rec))
        return _save_locked(data, strict=True)


def add_version(
    share_id: str,
    v: int,
    elements: list[dict],
    png: bytes,
    *,
    expires_ts: float,
    note: str | None = None,
    frame: str = "figure",
    target_id: str | None = None,
    digest: str = "",
) -> dict | None:
    """一个版本 complete 之后：存元素表与快照、更新最新版本号与到期时间；首版同时把记录转正（`delivered`）。"""
    with file_lock():
        data = _load_pruned_locked()
        rec = _find(data, share_id)
        if rec is None:
            return None
        saved = _write_snapshot(share_id, v, png)
        now = _now()
        rec["versions"] = [x for x in rec["versions"] if x["v"] != v]
        rec["versions"].append(
            {
                "v": v,
                "created_at": _iso(now),
                "frame": frame if frame in ("page", "figure") else "figure",
                "digest": digest,
                "elements": clean_elements(elements),
            }
        )
        rec["versions"] = sorted(rec["versions"], key=lambda x: x["v"])[-MAX_VERSIONS:]
        rec["latest_version"] = max(rec["latest_version"], v)
        rec["expires_ts"] = expires_ts
        rec["updated_ts"] = now
        rec["delivered"] = True
        if note is not None:
            rec["note"] = note[:NOTE_MAX]
        if target_id is not None and _TARGET_ID.match(target_id):
            rec["target_id"] = target_id
        if not saved:
            LOG.warning("外部审阅: 快照写入失败")
        _save_locked(data, strict=True)
        return json.loads(json.dumps(rec))


def _write_snapshot(share_id: str, v: int, png: bytes) -> bool:
    if not SHARE_ID.match(share_id):
        return False
    target = snapshot_path(share_id, v)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        atomicio.write_bytes(target, png, mode=0o600)
    except OSError:
        return False
    return True


def snapshot_file(share_id: str, v: int) -> Path | None:
    if not SHARE_ID.match(share_id) or not isinstance(v, int) or v < 1:
        return None
    p = snapshot_path(share_id, v)
    return p if p.is_file() else None


def mark_revoked(share_id: str) -> bool:
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        if rec is None:
            return False
        rec["revoked"] = True
        rec["updated_ts"] = _now()
        return _save_locked(data)


def forget(share_id: str) -> str:
    """删本机记录：`ok` / `missing` / `active`（还没 revoke 也没到期，不许删）。"""
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        if rec is None:
            return "missing"
        if not rec["revoked"] and not is_expired(rec):
            return "active"
        data["shares"] = [r for r in data["shares"] if r["share_id"] != share_id]
        _save_locked(data)
        _remove_snapshots(share_id)
        return "ok"


def mark_read(share_id: str) -> bool:
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        if rec is None:
            return False
        if rec["unread"]:
            rec["unread"] = 0
            _save_locked(data)
        return True


def merge_feed(share_id: str, feed: dict) -> dict | None:
    """把一次 feed 合并进记录：新意见 / 回复 / 删除 / 结论 / 已处理，游标，到期与停止状态，未读数。
    返回合并后的记录拷贝；没有这份记录 None。`feed` 已由 `reviewshare` 验过顶层形状；事件逐条再清洗。"""
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        if rec is None:
            return None
        events = feed.get("events")
        new_items = 0
        last_author = ""
        for ev in events if isinstance(events, list) else []:
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            if kind == "comment":
                c = clean_comment(ev.get("comment"))
                if c is None or len(rec["comments"]) >= MAX_COMMENTS and not _has(rec, c["id"]):
                    continue
                old = _has(rec, c["id"])
                if old is None:
                    rec["comments"].append(c)
                    new_items += 1
                    last_author = c["author"]
                else:
                    keep = {r["id"] for r in c["replies"]}
                    c["replies"] += [r for r in old["replies"] if r["id"] not in keep]
                    old.update(c)
            elif kind == "reply":
                cid, r = ev.get("comment_id"), clean_reply(ev.get("reply"))
                target = _has(rec, cid) if isinstance(cid, str) else None
                if (
                    target is None
                    or r is None
                    or any(x["id"] == r["id"] for x in target["replies"])
                ):
                    continue
                if len(target["replies"]) < MAX_REPLIES_PER_COMMENT:
                    target["replies"].append(r)
                    if r["from"] == "reviewer":
                        new_items += 1
                        last_author = r["author"]
            elif kind == "comment_deleted":
                cid = ev.get("comment_id")
                rec["comments"] = [c for c in rec["comments"] if c["id"] != cid]
            elif kind == "verdict":
                vd = clean_verdict(ev.get("verdict"))
                if vd is not None:
                    rec["verdicts"] = [
                        x
                        for x in rec["verdicts"]
                        if (x["v"], x["author"]) != (vd["v"], vd["author"])
                    ][-(MAX_VERDICTS - 1) :] + [vd]
            elif kind == "resolved":
                cid = ev.get("comment_id")
                target = _has(rec, cid) if isinstance(cid, str) else None
                if target is not None and isinstance(ev.get("resolved"), bool):
                    target["resolved"] = ev["resolved"]
        cursor = feed.get("cursor")
        if isinstance(cursor, str) and len(cursor) <= 512:
            rec["cursor"] = cursor
        share = feed.get("share")
        if isinstance(share, dict):
            if share.get("revoked") is True:
                rec["revoked"] = True
            exp = share.get("expires_at")
            if isinstance(exp, str):
                rec["expires_ts"] = parse_iso(exp, rec["expires_ts"])
            lv = share.get("latest_version")
            if isinstance(lv, int) and not isinstance(lv, bool) and lv > rec["latest_version"]:
                rec["latest_version"] = lv
        if new_items:
            rec["unread"] += new_items
            rec["last_unread_author"] = last_author
            rec["last_unread_ts"] = _now()
        rec["unread"] = min(rec["unread"], _unread_cap(rec))
        _save_locked(data)
        return json.loads(json.dumps(rec))


def _has(rec: dict, cid: object) -> dict | None:
    return next((c for c in rec["comments"] if c["id"] == cid), None)


def apply_reply(share_id: str, comment_id: str, reply: dict) -> dict | None:
    """作者自己的回复（服务端 201 之后）：并进副本，不计未读。返回该条意见。"""
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        r = clean_reply(reply)
        if rec is None or r is None:
            return None
        c = _has(rec, comment_id)
        if c is None:
            return None
        if not any(x["id"] == r["id"] for x in c["replies"]):
            c["replies"].append(r)
        _save_locked(data)
        return json.loads(json.dumps(c))


def apply_comment(share_id: str, comment: dict) -> dict | None:
    """服务端回的整条意见（PATCH resolved 之后）覆盖副本里的那一条。"""
    with file_lock():
        data = _load_locked()
        rec = _find(data, share_id)
        c = clean_comment(comment)
        if rec is None or c is None:
            return None
        cur = _has(rec, c["id"])
        if cur is None:
            return None
        keep = {r["id"] for r in c["replies"]}
        c["replies"] += [r for r in cur["replies"] if r["id"] not in keep]
        cur.update(c)
        _save_locked(data)
        return json.loads(json.dumps(cur))


def comment_of(share_id: str, comment_id: str) -> dict | None:
    rec = get(share_id)
    return _has(rec, comment_id) if rec else None
