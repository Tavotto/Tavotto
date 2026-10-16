"""AI 会话历史：SQLite 持久化（纯标准库，Flask 父进程 import）。

单库多项目：`cache/ai_history.sqlite3`，按 project（figures 目录）列过滤，
对外呈现即「项目级历史」。每次操作独立连接（短事务），无跨线程共享连接。

后端重启后：启动时把所有 running 行改为 interrupted——历史里绝不出现
空记录或 unknown 状态。
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import time
from pathlib import Path

from . import config
from .procalive import pid_alive

DB_PATH = config.data_dir() / "cache" / "ai_history.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  canvas TEXT, panel TEXT, element TEXT,
  provider TEXT NOT NULL, model TEXT, effort TEXT,
  scope TEXT, target TEXT, script TEXT,
  prompt TEXT NOT NULL,
  transcript TEXT NOT NULL DEFAULT '[]',
  diff TEXT NOT NULL DEFAULT '',
  changed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error TEXT,
  started_ms INTEGER NOT NULL,
  ended_ms INTEGER,
  snapshot_path TEXT,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, started_ms DESC);

-- 幂等账本：**独立于用户可删的历史**。用户在任务历史里删掉一行，不能让「这条请求已经执行过」
-- 这个事实跟着消失——否则请求跨后端重启仍未确认时，查询得 404、前端放开同 id 重发、同一修改再执行一次。
-- 只存 id / 会话 id / 时间：**不存提示词**，不存脚本名。
CREATE TABLE IF NOT EXISTS ai_requests (
  project TEXT NOT NULL,
  client_request_id TEXT NOT NULL,
  session TEXT NOT NULL,
  created_ms INTEGER NOT NULL,
  PRIMARY KEY (project, client_request_id)
);

-- 助手输入框的**未发送草稿**与**待确认提交**（ADR 0132）：存后端，不存浏览器——桌面端口一变 origin 就换、
-- localStorage 整个清空，用户写了一半的指令不该因此消失。主语是 (项目, fileId[, rid])，按项目隔离。
-- 与 ai_history 同库同边界：用户本机 data_dir 下，不进项目目录、不进诊断包 / 遥测 / 日志。
CREATE TABLE IF NOT EXISTS ai_drafts (
  project TEXT NOT NULL,
  file_id TEXT NOT NULL,
  text TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_ms INTEGER NOT NULL,
  stamp INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project, file_id)
);
CREATE TABLE IF NOT EXISTS ai_pending (
  project TEXT NOT NULL,
  file_id TEXT NOT NULL,
  rid TEXT NOT NULL,
  text TEXT NOT NULL,
  sent TEXT NOT NULL,
  script TEXT,
  draft_stamp INTEGER,
  created_ms INTEGER NOT NULL,
  PRIMARY KEY (project, file_id, rid)
);
"""

#: 草稿 / 待确认账本：单稿上限（UTF-8 字节）、每项目份数上限、保留期
#: **指令（草稿 / 待确认提交的原文）的最大长度（UTF-8 字节），草稿与账本用同一个上限**——两个上限不一致是
#: 「快照写草稿 413、发送却照样继续」的根：账本能收的比草稿多，发出去的指令草稿却存不下、清不掉。
#: ↔ `web/src/lib/aiDraft.ts` 的同名常量严格同源（`tests/test_ai_drafts.py::test_instruction_limit_is_one_number_on_both_sides`）。
INSTRUCTION_MAX_BYTES = 20 * 1024
DRAFT_MAX_BYTES = INSTRUCTION_MAX_BYTES
PENDING_MAX_BYTES = INSTRUCTION_MAX_BYTES
DRAFT_MAX_PER_PROJECT = 30
PENDING_MAX_PER_FILE = 8
DRAFT_KEEP_DAYS = 30


class PendingLimit(ValueError):
    """这个面板未解决的待确认提交已满：**拒绝新的、不删旧的**（旧的可能正是后端已接受、等着被查询的那条，
    删了它就查不到、可能被同一条指令换新 id 再执行一遍）。调用方回 409 `ai_pending_limit`。"""

    def __init__(self, count: int):
        super().__init__(count)
        self.count = count


class DraftTooLarge(ValueError):
    """文本超过上限：调用方回 413 `ai_draft_too_large`，**不截断**（截断会让恢复出来的是一句被砍掉的指令）。"""

    def __init__(self, limit: int, stamp: int | None = None):
        super().__init__(limit)
        self.limit = limit
        #: 超限写入在服务端留了墓碑（带戳）时，那一行现在的戳；调用方原样带回，客户端据此退出「基线未知」
        self.stamp = stamp


#: running 行超过这么久还没结束就当属主已死（pid 会被复用，不能只信 pid 存活）：绝不让一条僵尸行永久锁住脚本
RUNNING_OWNER_MAX_AGE_MS = 24 * 3600 * 1000


#: `ai_requests.session` 的哨兵值：这个 (项目, rid) 被**新指令取代**（`supersedes`）——它是一个「墓碑预留」，不是真实会话。
#: 之后带这个 rid 的 run 走原子预留必然撞上唯一约束、拿到它，被明确拒绝，不 spawn。真实会话 id 是 12 位十六进制，不会与它相撞
SUPERSEDED = "~superseded"


class SupersedeConflict(Exception):
    """要取代的旧 rid 已经被真实预留（旧请求其实已经到达后端并登记了）：不能再当成「没收到」取代它。"""

    def __init__(self, rid: str):
        super().__init__(rid)
        self.rid = rid


class ScriptBusy(Exception):
    """同一 (项目, 脚本) 上已有一个**属主进程还活着**的 running 会话（可能在另一个 Tavotto 服务进程里）。"""

    def __init__(self, script: str):
        super().__init__(script)
        self.script = script


def owner_alive(owner_pid: int | None, started_ms: int | None, now_ms: int | None = None) -> bool:
    """这条 running 行的属主还活着吗。属主未知（NULL）/ 就是本进程（它在内存里另有判据，磁盘上的是上一实例留下的）/
    pid 已死 / 行太老 → False；量不了 → 保守当活着（宁可多挡一次，不让两个进程同时改同一个脚本）。"""
    if owner_pid is None or owner_pid == os.getpid():
        return False
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if started_ms is not None and now_ms - started_ms > RUNNING_OWNER_MAX_AGE_MS:
        return False
    return pid_alive(owner_pid) is not False


#: 幂等账本的保留期；超龄才清（会话那时早已终态：启动时 running 一律转 interrupted）
REQUEST_KEEP_DAYS = 30


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    # 迁移里的回填是个写语句：不提交的话这条连接从一打开就占着写锁（所有 `with _connect()` 写入者因此被**碰巧**串行化，
    # 而读-判-写的原子性不该靠这种巧合）。提交掉，需要原子性的写入者自己用 `_immediate`
    con.commit()
    return con


#: 建表之后追加的列（列名 → 类型）。老库没有这些列，`ALTER TABLE` 补上；
#: 新库 `_SCHEMA` 里没写它们也没关系——判据是「表里有没有」，不是版本号。
_ADDED_COLUMNS = {
    # Session 22：AI 改完代码之后统一刷新的结局（JSON），与 `changed` 分开记：
    # 代码改成了、刷新没成，是两件事，不能合成一个「成功」。
    "refresh": "TEXT",
    # 前端点「发送」时生成的请求 id（UUID）：同一条 AI 修改被无意重发时据此去重。
    # 老库、老前端（不带 id）为 NULL，不参与唯一约束。
    "client_request_id": "TEXT",
    # 登记这个会话的**服务进程**的 pid：两个 Tavotto 进程共用同一数据目录时，靠它判「同一脚本上的 running 会话属主是否还活着」
    # （进程内的 SESSIONS / 脚本锁管不到对方）。老行为 NULL = 属主未知，不算忙。
    "owner_pid": "INTEGER",
}

#: (project, client_request_id) 唯一：部分索引，NULL 不算。建在 `_migrate` 里（列补上之后），
#: 老库同样走这条路——判据是「索引有没有」，不是版本号。
_REQUEST_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_request ON sessions(project, client_request_id) "
    "WHERE client_request_id IS NOT NULL"
)


def _add_column(con: sqlite3.Connection, ddl: str) -> None:
    """补列。两个连接（两个服务进程，或同一进程的两个线程）同时迁移同一个库时，「先查有没有再 ALTER」不是原子的：
    后到的那个会撞上 `duplicate column name`——列已经被对方补上了，这正是我们要的结果，吞掉；别的错照抛。"""
    try:
        con.execute(ddl)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise


def _migrate(con: sqlite3.Connection) -> None:
    have = {row["name"] for row in con.execute("PRAGMA table_info(sessions)")}
    for name, kind in _ADDED_COLUMNS.items():
        if name not in have:
            _add_column(con, f"ALTER TABLE sessions ADD COLUMN {name} {kind}")
    con.execute(_REQUEST_INDEX)
    # 草稿写入戳：旧表没有，补列（见 `put_draft` 的「旧戳不覆盖新戳」）
    if "stamp" not in {row["name"] for row in con.execute("PRAGMA table_info(ai_drafts)")}:
        _add_column(con, "ALTER TABLE ai_drafts ADD COLUMN stamp INTEGER NOT NULL DEFAULT 0")
    # 待确认账本按**脚本**也要查得到（发起它的面板可能已被删 / 换图）：`script` 是后补的列
    pend_cols = {row["name"] for row in con.execute("PRAGMA table_info(ai_pending)")}
    if "script" not in pend_cols:
        _add_column(con, "ALTER TABLE ai_pending ADD COLUMN script TEXT")
    # 提交时草稿的写入戳：确认之后「草稿在提交之后没被改过」按它判，不按文本比（trim / 规范化的差异无关）
    if "draft_stamp" not in pend_cols:
        _add_column(con, "ALTER TABLE ai_pending ADD COLUMN draft_stamp INTEGER")
    # 老库 / 本改动之前登记的会话：账本补齐（幂等）
    con.execute(
        "INSERT OR IGNORE INTO ai_requests (project, client_request_id, session, created_ms) "
        "SELECT project, client_request_id, id, started_ms FROM sessions "
        "WHERE client_request_id IS NOT NULL"
    )


@contextlib.contextmanager
def _immediate(db_path: Path | None = None):
    """`BEGIN IMMEDIATE` 事务：开始就拿库的写锁，**跨进程**互斥（两个 Tavotto 服务进程共用同一个数据目录时，
    进程内的锁管不到对方）。正常退出提交，异常回滚。"""
    con = _connect(db_path)
    con.isolation_level = None  # 手动事务
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        else:
            con.execute("COMMIT")
    finally:
        con.close()


def _reserve(con: sqlite3.Connection, project: str, rid: str, sid: str) -> tuple[bool, str]:
    """在已开的事务里预留 (项目, 请求 id)：**普通 INSERT**（不加 OR IGNORE / OR REPLACE），唯一约束冲突 = 别人已预留，
    读出既有会话 id。回 `(是否是本次预留的, 会话 id)`。"""
    try:
        con.execute(
            "INSERT INTO ai_requests (project, client_request_id, session, created_ms) VALUES (?,?,?,?)",
            (project, rid, sid, int(time.time() * 1000)),
        )
    except sqlite3.IntegrityError:
        row = con.execute(
            "SELECT session FROM ai_requests WHERE project=? AND client_request_id=?",
            (project, rid),
        ).fetchone()
        return False, row["session"]
    return True, sid


def reserve_request(
    project: str, client_request_id: str, sid: str, db_path: Path | None = None
) -> tuple[bool, str]:
    """**跨进程原子**地预留请求：恰好一个调用者拿到 `(True, sid)`，其余拿到 `(False, 既有会话 id)`。
    必须在启动 CLI **之前**完成（`record_start` 在同一个事务里先预留再写会话行，就是这一步）。"""
    with _immediate(db_path) as con:
        return _reserve(con, project, client_request_id, sid)


def record_start(sess: dict, db_path: Path | None = None) -> tuple[bool, str]:
    """登记一个新会话。带请求 id 时先**原子预留**（见 `reserve_request`）：已被别的调用者（含另一个进程）预留 →
    **不写会话行**，回 `(False, 既有会话 id)`，调用方不得 spawn。预留与会话行在同一个事务里，崩溃不会留下
    「账本有、会话无」的半条记录。会话行用普通 INSERT（sid 唯一），不再顶替同请求 id 的行。"""
    rid = sess.get("client_request_id")
    with _immediate(db_path) as con:
        if rid:
            reserved, existing = _reserve(con, sess.get("project", ""), rid, sess["id"])
            if not reserved:
                return False, existing
        # **同一事务里**认领脚本：别的进程里属主还活着的 running 会话在同一 (项目, 脚本) 上 → 拒绝（回滚，连同上面的预留）。
        # 不能只靠进程内的脚本锁：两个服务进程用不同 rid 改同一个用户脚本时它管不到对方
        if sess.get("script"):
            for other in con.execute(
                "SELECT owner_pid, started_ms FROM sessions WHERE project=? AND script=? AND status='running'",
                (sess.get("project", ""), sess["script"]),
            ):
                if owner_alive(other["owner_pid"], other["started_ms"]):
                    raise ScriptBusy(sess["script"])
        con.execute(
            """INSERT INTO sessions
               (id, project, canvas, panel, element, provider, model, effort,
                scope, target, script, prompt, status, started_ms, snapshot_path,
                client_request_id, owner_pid)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sess["id"],
                sess.get("project", ""),
                sess.get("canvas"),
                sess.get("panel"),
                sess.get("element"),
                sess["provider"],
                sess.get("model"),
                sess.get("effort"),
                sess.get("scope"),
                sess.get("target"),
                sess.get("script"),
                sess.get("prompt", ""),
                "running",
                int(time.time() * 1000),
                sess.get("snapshot_path"),
                sess.get("client_request_id"),
                sess.get("owner_pid", os.getpid()),
            ),
        )
    return True, sess["id"]


def record_end(
    sid: str,
    status: str,
    diff: str = "",
    changed: bool = False,
    error: str | None = None,
    transcript: list | None = None,
    db_path: Path | None = None,
    refresh: dict | None = None,
) -> None:
    with _connect(db_path) as con:
        con.execute(
            """UPDATE sessions SET status=?, diff=?, changed=?, error=?,
               transcript=?, ended_ms=?, refresh=? WHERE id=?""",
            (
                status,
                diff,
                int(bool(changed)),
                error,
                json.dumps(transcript or [], ensure_ascii=False),
                int(time.time() * 1000),
                None if refresh is None else json.dumps(refresh, ensure_ascii=False),
                sid,
            ),
        )


def update_status(sid: str, status: str, db_path: Path | None = None) -> None:
    with _connect(db_path) as con:
        con.execute("UPDATE sessions SET status=? WHERE id=?", (status, sid))


def mark_interrupted_running(db_path: Path | None = None) -> int:
    """启动时调用：上个进程留下的 running 转为 interrupted。**只标属主已死的**（`owner_alive`）：共用同一数据目录的
    另一个服务进程里仍在跑的会话不能被这次启动标掉（否则它的脚本被当成空闲、再被另一个进程改写）。
    属主未知（老行）/ 就是本进程的旧实例 / pid 已死 / 行太老 → 标。"""
    now = int(time.time() * 1000)
    with (
        _immediate(db_path) as con
    ):  # 读出死的 → 逐个标：同一个事务，期间别的进程把某行 record_end 了也不会被我们改回 interrupted
        dead = [
            r["id"]
            for r in con.execute(
                "SELECT id, owner_pid, started_ms FROM sessions WHERE status='running'"
            )
            if not owner_alive(r["owner_pid"], r["started_ms"], now)
        ]
        for sid in dead:
            con.execute(
                "UPDATE sessions SET status='interrupted', ended_ms=? WHERE id=?", (now, sid)
            )
        return len(dead)


def set_pinned(sid: str, pinned: bool, db_path: Path | None = None) -> bool:
    with _connect(db_path) as con:
        cur = con.execute("UPDATE sessions SET pinned=? WHERE id=?", (int(pinned), sid))
        return cur.rowcount > 0


def delete(sid: str, db_path: Path | None = None) -> bool:
    with _connect(db_path) as con:
        cur = con.execute("DELETE FROM sessions WHERE id=?", (sid,))
        return cur.rowcount > 0


def purge(keep_days: int, db_path: Path | None = None) -> int:
    """保留期限：删除超龄且未固定的记录（pinned 永久保留）。"""
    # <= 而非 <：keep_days=0 表示「一个不留」，同毫秒内建的记录也必须删掉，
    # 否则边界结果取决于插入与 purge 是否落在同一毫秒（曾是间歇性失败的用例）。
    cutoff = int((time.time() - keep_days * 86400) * 1000)
    with _connect(db_path) as con:
        cur = con.execute("DELETE FROM sessions WHERE pinned=0 AND started_ms <= ?", (cutoff,))
        return cur.rowcount


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["changed"] = bool(d["changed"])
    d["pinned"] = bool(d["pinned"])
    try:
        d["transcript"] = json.loads(d["transcript"])
    except (ValueError, TypeError):
        d["transcript"] = []
    try:
        refresh = json.loads(d.get("refresh") or "null")
    except (ValueError, TypeError):
        refresh = None
    d["refresh"] = refresh if isinstance(refresh, dict) else None
    snap = d.get("snapshot_path")
    d["revert_available"] = bool(snap) and Path(snap).is_file()
    return d


def list_sessions(
    project: str,
    query: str = "",
    status: str = "",
    pinned_only: bool = False,
    limit: int = 20,
    offset: int = 0,
    db_path: Path | None = None,
) -> dict:
    """分页 + 搜索（prompt/target 子串）+ 状态筛选。返回 {total, sessions}。"""
    where = ["project = ?"]
    args: list = [project]
    if query:
        where.append("(prompt LIKE ? OR target LIKE ?)")
        args += [f"%{query}%", f"%{query}%"]
    if status:
        where.append("status = ?")
        args.append(status)
    if pinned_only:
        where.append("pinned = 1")
    clause = " AND ".join(where)
    with _connect(db_path) as con:
        total = con.execute(f"SELECT COUNT(*) FROM sessions WHERE {clause}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM sessions WHERE {clause} "
            f"ORDER BY pinned DESC, started_ms DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return {"total": total, "sessions": [_row_to_dict(r) for r in rows]}


def find_by_request(
    project: str, client_request_id: str, db_path: Path | None = None
) -> dict | None:
    """按 (项目, 请求 id) 找已登记的会话。只在**本项目**内找：别的项目用了同一个 id 也看不到。"""
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT * FROM sessions WHERE project=? AND client_request_id=?",
            (project, client_request_id),
        ).fetchone()
    return _row_to_dict(row) if row else None


def find_request_record(
    project: str, client_request_id: str, db_path: Path | None = None
) -> dict | None:
    """幂等账本里 (项目, 请求 id) 对应的 `{session, created_ms}`；历史行被用户删了也在。"""
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT session, created_ms FROM ai_requests WHERE project=? AND client_request_id=?",
            (project, client_request_id),
        ).fetchone()
    return dict(row) if row else None


def purge_requests(keep_days: int = REQUEST_KEEP_DAYS, db_path: Path | None = None) -> int:
    """账本的有界清理：只清超龄的。与 `purge`（历史，pinned 豁免）互不相干。"""
    cutoff = int((time.time() - keep_days * 86400) * 1000)
    with _connect(db_path) as con:
        return con.execute("DELETE FROM ai_requests WHERE created_ms <= ?", (cutoff,)).rowcount


def get(sid: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# 草稿与待确认账本（ADR 0132）
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _prune_drafts(con: sqlite3.Connection, project: str, now: int) -> None:
    """写入时的有界清理：过期的（30 天）与超出每项目份数的最老的。"""
    cutoff = now - DRAFT_KEEP_DAYS * 86400 * 1000
    con.execute("DELETE FROM ai_drafts WHERE updated_ms < ?", (cutoff,))
    con.execute("DELETE FROM ai_pending WHERE created_ms < ?", (cutoff,))
    con.execute(
        "DELETE FROM ai_drafts WHERE project=? AND file_id NOT IN "
        "(SELECT file_id FROM ai_drafts WHERE project=? ORDER BY updated_ms DESC LIMIT ?)",
        (project, project, DRAFT_MAX_PER_PROJECT),
    )


def purge_drafts(db_path: Path | None = None) -> int:
    """启动时：清掉过期的草稿与账本。"""
    cutoff = _now_ms() - DRAFT_KEEP_DAYS * 86400 * 1000
    with _connect(db_path) as con:
        n = con.execute("DELETE FROM ai_drafts WHERE updated_ms < ?", (cutoff,)).rowcount
        n += con.execute("DELETE FROM ai_pending WHERE created_ms < ?", (cutoff,)).rowcount
        return n


def get_draft(project: str, file_id: str, db_path: Path | None = None) -> dict:
    """`{draft: {text, revision, updated_ms} | None, pendings: [{rid, text, sent, created_ms}]}`（老 → 新）。"""
    with _connect(db_path) as con:
        row = con.execute(
            "SELECT text, revision, updated_ms, stamp FROM ai_drafts WHERE project=? AND file_id=?",
            (project, file_id),
        ).fetchone()
        pend = con.execute(
            "SELECT rid, text, sent, created_ms FROM ai_pending WHERE project=? AND file_id=? "
            "ORDER BY created_ms ASC",
            (project, file_id),
        ).fetchall()
    pendings = []
    for r in pend:
        try:
            sent = json.loads(r["sent"])
        except (ValueError, TypeError):
            continue
        pendings.append(
            {"rid": r["rid"], "text": r["text"], "sent": sent, "created_ms": r["created_ms"]}
        )
    # `stamp`：这一行（含墓碑）的写入戳——客户端读到就抬高自己的戳下限，之后发出的写入一定比它新
    # （墙钟回拨、新开页面时本页戳从 0 起步也不会被判 stale）
    draft = dict(row) if row and row["text"] != "" else None
    return {"draft": draft, "pendings": pendings, "stamp": row["stamp"] if row else 0}


def _now_stamp() -> int:
    """服务端自己产生的写入戳（微秒量级的墙钟）：删除类写入用它，保证「删除之前发出的旧写入」排在它后面被拒。"""
    return _now_ms() * 1000


def put_draft(
    project: str,
    file_id: str,
    text: str,
    db_path: Path | None = None,
    stamp: int | None = None,
) -> dict:
    """全文覆盖。**每次写入带一个单调的写入戳（客户端墙钟微秒 + 本标签页内单调自增），服务端只接受比已存最新戳更新的写入**——
    旧写入（PUT A 在途时用户又写了 B，keepalive 的 B 先落地、A 后落地）一定被拒（`stale: True`，不覆盖），
    「旧请求赢」在服务器端就不可能发生；前端的排队救不了 pagehide 的 keepalive（拿不到响应）。
    跨标签页同理：写入戳最新者为准。不带戳（老前端）= 视为最新（戳取「现在」与已存戳+1 的较大者）。

    空文本 = 删，但**留一条墓碑**（空文本 + 戳）：否则删除之后迟到的旧写入会因为「没有已存戳」而复活。
    超限抛 `DraftTooLarge`，旧稿也删（同样留墓碑）。返回 `{revision, stamp}` 或 `{stale: True, revision, stamp}`。"""
    now = _now_ms()
    too_large = len(text.encode("utf-8")) > DRAFT_MAX_BYTES
    rev = 0
    # 读旧戳 → 判断 → 写 **整个在一个 `BEGIN IMMEDIATE` 事务里**：否则两个连接（两个服务进程）都先读到旧戳，
    # 新的先提交，旧的随后用过期的 prev_stamp 判断并覆盖回去
    with _immediate(db_path) as con:
        prev = con.execute(
            "SELECT revision, stamp FROM ai_drafts WHERE project=? AND file_id=?",
            (project, file_id),
        ).fetchone()
        prev_stamp = prev["stamp"] if prev else -1
        if stamp is None:
            stamp = max(_now_stamp(), prev_stamp + 1)
        elif stamp <= prev_stamp:
            return {"stale": True, "revision": prev["revision"], "stamp": prev_stamp}
        rev = (prev["revision"] if prev else 0) + 1
        body = "" if (text == "" or too_large) else text
        con.execute(
            "INSERT OR REPLACE INTO ai_drafts (project, file_id, text, revision, updated_ms, stamp) VALUES (?,?,?,?,?,?)",
            (project, file_id, body, rev, now, stamp),
        )
        _prune_drafts(con, project, now)
    if too_large:
        raise DraftTooLarge(DRAFT_MAX_BYTES, stamp=stamp)
    return {"revision": rev, "stamp": stamp}


def delete_draft_if(project: str, file_id: str, if_text: str, db_path: Path | None = None) -> bool:
    """**条件删除**：只有库里的文本仍等于 `if_text` 才删——另一个标签页后写的新稿不能被这次删除抹掉。
    删 = 清空文本、推进戳（墓碑），所以此前发出的旧写入随后落地也会被拒。"""
    with _connect(db_path) as con:
        return (
            con.execute(
                "UPDATE ai_drafts SET text='', revision=revision+1, updated_ms=?, stamp=MAX(stamp+1, ?) "
                "WHERE project=? AND file_id=? AND text=? AND text != ''",
                (_now_ms(), _now_stamp(), project, file_id, if_text),
            ).rowcount
            > 0
        )


def list_pendings(project: str, db_path: Path | None = None) -> list[dict]:
    """本项目全部未落定的提交（跨面板，老 → 新），带 `file` 与 `script`：发起它的面板已不在时也找得回来。"""
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT file_id, rid, text, sent, script, created_ms FROM ai_pending WHERE project=? "
            "ORDER BY created_ms ASC",
            (project,),
        ).fetchall()
    out = []
    for r in rows:
        try:
            sent = json.loads(r["sent"])
        except (ValueError, TypeError):
            continue
        out.append(
            {
                "file": r["file_id"],
                "rid": r["rid"],
                "text": r["text"],
                "sent": sent,
                "script": r["script"],
                "created_ms": r["created_ms"],
            }
        )
    return out


def put_pending(
    project: str,
    file_id: str,
    rid: str,
    text: str,
    sent: dict,
    db_path: Path | None = None,
    script: str | None = None,
    draft_stamp: int | None = None,
    supersedes: list[str] | None = None,
) -> None:
    """记一条待确认提交（按 rid 一条）。每 (项目, 面板) 最多 8 条未解决的；满了**拒绝新的**（`PendingLimit`），绝不淘汰旧的。

    `draft_stamp`：提交那一刻草稿的写入戳（前端发送前先把草稿写成功、拿到它）。`accept_pending` 据此判
    「草稿在提交之后有没有被改过」；没有（None）= 不知道，确认时不动草稿。"""
    if len(text.encode("utf-8")) > PENDING_MAX_BYTES:
        raise DraftTooLarge(PENDING_MAX_BYTES)
    now = _now_ms()
    with _immediate(db_path) as con:  # 数名额与写入同一个事务：并发提交不会同时挤过上限
        # `supersedes`：用户放弃了旧的等待（「改写后再发」）、这条用新 rid 发。旧行**在写新行的同一个事务里**删——
        # 客户端不单方面删（它对「这一行不存在 / 已不需要」的判断可能是错的：原请求可能正在路上）；新写入失败 = 整个事务回滚 = 旧行还在
        for old in supersedes or []:
            if old == rid:
                continue
            # 删行还不够：原 POST 可能只是慢，在删除之后才到达、照样登记并执行——于是旧指令和改写后的指令都执行了。
            # 同一个事务里给旧 rid 写一条**墓碑预留**（session=SUPERSEDED）：之后它的 run 走原子预留必撞唯一约束，被拒绝。
            # 旧 rid 已被真实预留（旧请求其实已经登记）则整个事务回滚——不能再当成「没收到」取代它
            try:
                con.execute(
                    "INSERT INTO ai_requests (project, client_request_id, session, created_ms) VALUES (?,?,?,?)",
                    (project, old, SUPERSEDED, now),
                )
            except sqlite3.IntegrityError:
                existing = con.execute(
                    "SELECT session FROM ai_requests WHERE project=? AND client_request_id=?",
                    (project, old),
                ).fetchone()
                if existing is None or existing["session"] != SUPERSEDED:
                    raise SupersedeConflict(old) from None
            con.execute(
                "DELETE FROM ai_pending WHERE project=? AND file_id=? AND rid=?",
                (project, file_id, old),
            )
        # 同 rid 的重写（重试写账本）不占新名额；其它 rid 满了就拒绝
        exists = con.execute(
            "SELECT 1 FROM ai_pending WHERE project=? AND file_id=? AND rid=?",
            (project, file_id, rid),
        ).fetchone()
        if not exists:
            n = con.execute(
                "SELECT COUNT(*) FROM ai_pending WHERE project=? AND file_id=?", (project, file_id)
            ).fetchone()[0]
            if n >= PENDING_MAX_PER_FILE:
                raise PendingLimit(n)
        con.execute(
            "INSERT OR REPLACE INTO ai_pending (project, file_id, rid, text, sent, script, draft_stamp, created_ms) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                project,
                file_id,
                rid,
                text,
                json.dumps(sent, ensure_ascii=False),
                script,
                draft_stamp,
                now,
            ),
        )
        _prune_drafts(con, project, now)


def clear_pending(project: str, file_id: str, rid: str, db_path: Path | None = None) -> bool:
    """明确被拒绝：只清自己那条（别的 rid 不动）。"""
    with _connect(db_path) as con:
        return (
            con.execute(
                "DELETE FROM ai_pending WHERE project=? AND file_id=? AND rid=?",
                (project, file_id, rid),
            ).rowcount
            > 0
        )


def drop_rejected_pending(project: str, rid: str, db_path: Path | None = None) -> bool:
    """`/api/ai/run` **明确拒绝**了这个 rid（一定没执行）：后端自己把它的待确认行删掉（项目内按 rid，一个事务）。
    账本行的删除只发生在后端事务里，客户端不再自己 DELETE——它的删除可能失败，旧行随后被还原成「未确认」，
    而新 rid 已经执行过，同一条指令就执行了两次。"""
    with _immediate(db_path) as con:
        return (
            con.execute("DELETE FROM ai_pending WHERE project=? AND rid=?", (project, rid)).rowcount
            > 0
        )


def accept_pending(project: str, file_id: str, rid: str, db_path: Path | None = None) -> dict:
    """请求已被后端接受：清这条账本；**草稿自提交之后没被改过**（草稿当前的写入戳等于提交时记下的 `draft_stamp`）
    才一并删（一个事务里判与删）。**不比文本**：提交文本是 trim 过的、草稿是原样，文本比较会让已被接受的指令
    留在输入框里再被发一次。账本没记戳 = 不知道，不动草稿。返回 `{cleared, draft_deleted}`。"""
    with _immediate(db_path) as con:  # 读账本 → 删 → 按戳删草稿，同一个事务
        pend = con.execute(
            "SELECT draft_stamp FROM ai_pending WHERE project=? AND file_id=? AND rid=?",
            (project, file_id, rid),
        ).fetchone()
        if pend is None:
            return {"cleared": False, "draft_deleted": False}
        con.execute(
            "DELETE FROM ai_pending WHERE project=? AND file_id=? AND rid=?",
            (project, file_id, rid),
        )
        n = 0
        if pend["draft_stamp"] is not None:
            n = con.execute(
                "UPDATE ai_drafts SET text='', revision=revision+1, updated_ms=?, stamp=MAX(stamp+1, ?) "
                "WHERE project=? AND file_id=? AND stamp=? AND text != ''",
                (_now_ms(), _now_stamp(), project, file_id, pend["draft_stamp"]),
            ).rowcount
    return {"cleared": True, "draft_deleted": n > 0}
