"""AI 会话历史：SQLite 持久化（纯标准库，Flask 父进程 import）。

单库多项目：`cache/ai_history.sqlite3`，按 project（figures 目录）列过滤，
对外呈现即「项目级历史」。每次操作独立连接（短事务），无跨线程共享连接。

后端重启后：启动时把所有 running 行改为 interrupted——历史里绝不出现
空记录或 unknown 状态。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from . import config

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
"""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    return con


#: 建表之后追加的列（列名 → 类型）。老库没有这些列，`ALTER TABLE` 补上；
#: 新库 `_SCHEMA` 里没写它们也没关系——判据是「表里有没有」，不是版本号。
_ADDED_COLUMNS = {
    # Session 22：AI 改完代码之后统一刷新的结局（JSON），与 `changed` 分开记：
    # 代码改成了、刷新没成，是两件事，不能合成一个「成功」。
    "refresh": "TEXT",
}


def _migrate(con: sqlite3.Connection) -> None:
    have = {row["name"] for row in con.execute("PRAGMA table_info(sessions)")}
    for name, kind in _ADDED_COLUMNS.items():
        if name not in have:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {name} {kind}")


def record_start(sess: dict, db_path: Path | None = None) -> None:
    with _connect(db_path) as con:
        con.execute(
            """INSERT OR REPLACE INTO sessions
               (id, project, canvas, panel, element, provider, model, effort,
                scope, target, script, prompt, status, started_ms, snapshot_path)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )


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
    """启动时调用：上个进程留下的 running 一律转为 interrupted。"""
    with _connect(db_path) as con:
        cur = con.execute(
            "UPDATE sessions SET status='interrupted', ended_ms=? WHERE status='running'",
            (int(time.time() * 1000),),
        )
        return cur.rowcount


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


def get(sid: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    return _row_to_dict(row) if row else None
