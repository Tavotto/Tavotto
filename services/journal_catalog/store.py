"""sqlite3（WAL）存储。所有「检查 + 写入」都在一个 `BEGIN IMMEDIATE` 事务里，
所以并发下限额是强一致的。时间由调用方传入（`now`），本模块不读时钟。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

HOUR = 3600
DAY = 86400
READ_RETENTION_S = 365 * DAY  # reads 保留 1 年
IP_EVENT_RETENTION_S = 48 * 3600  # ip_events 保留 48 小时


@dataclass(frozen=True)
class Limits:
    """限额（默认值待用户确认，全部可配置）。"""

    ip_tokens_per_day: int = 5
    reads_per_hour: int = 10
    reads_per_day: int = 20
    ip_reads_per_day: int = 100


def _retry(oldest_ts: int, window: int, now: int) -> int:
    return max(1, oldest_ts + window - now)


def _rollback(con: sqlite3.Connection) -> None:
    try:
        con.execute("ROLLBACK")
    except sqlite3.Error:
        pass


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        con = self._connect()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        # 每次操作一个连接：线程安全，WSGI 多线程 / 多进程都能用
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _tx(self) -> sqlite3.Connection:
        con = self._connect()
        con.execute("BEGIN IMMEDIATE")
        return con

    def has_device(self, token_hash: str) -> bool:
        con = self._connect()
        try:
            row = con.execute("SELECT 1 FROM devices WHERE token_hash=?", (token_hash,)).fetchone()
            return row is not None
        finally:
            con.close()

    def issue_device(
        self, token_hash: str, ip_key: str, now: int, limits: Limits, client: str = "app"
    ) -> tuple[bool, int]:
        """返回 (是否签发, retry_after_s)。"""
        con = self._tx()
        try:
            rows = con.execute(
                "SELECT ts FROM ip_events WHERE ip_key=? AND kind='token' AND ts>?",
                (ip_key, now - DAY),
            ).fetchall()
            if len(rows) >= limits.ip_tokens_per_day:
                con.execute("ROLLBACK")
                return False, _retry(min(r[0] for r in rows), DAY, now)
            con.execute(
                "INSERT INTO devices (token_hash, created_at, client) VALUES (?,?,?)",
                (token_hash, now, client),
            )
            con.execute(
                "INSERT INTO ip_events (ip_key, kind, ts) VALUES (?, 'token', ?)", (ip_key, now)
            )
            con.execute("COMMIT")
            return True, 0
        except BaseException:
            _rollback(con)
            raise
        finally:
            con.close()

    def first_read(
        self,
        token_hash: str,
        journal_id: str,
        ip_key: str,
        now: int,
        limits: Limits,
        client: str = "app",
    ) -> tuple[str, int]:
        """返回 (结果, retry_after_s)。结果：ok / repeat / unknown /
        quota_hour / quota_day / ip_day。repeat 与 ok 都表示放行。"""
        con = self._tx()
        try:
            known = con.execute("SELECT 1 FROM devices WHERE token_hash=?", (token_hash,))
            if known.fetchone() is None:
                con.execute("ROLLBACK")
                return "unknown", 0
            seen = con.execute(
                "SELECT 1 FROM reads WHERE token_hash=? AND journal_id=?",
                (token_hash, journal_id),
            )
            if seen.fetchone() is not None:
                con.execute("ROLLBACK")
                return "repeat", 0
            for code, window, cap in (
                ("quota_hour", HOUR, limits.reads_per_hour),
                ("quota_day", DAY, limits.reads_per_day),
            ):
                rows = con.execute(
                    "SELECT first_read_at FROM reads WHERE token_hash=? AND first_read_at>?",
                    (token_hash, now - window),
                ).fetchall()
                if len(rows) >= cap:
                    con.execute("ROLLBACK")
                    return code, _retry(min(r[0] for r in rows), window, now)
            rows = con.execute(
                "SELECT ts FROM ip_events WHERE ip_key=? AND kind='read' AND ts>?",
                (ip_key, now - DAY),
            ).fetchall()
            if len(rows) >= limits.ip_reads_per_day:
                con.execute("ROLLBACK")
                return "ip_day", _retry(min(r[0] for r in rows), DAY, now)
            con.execute(
                "INSERT INTO reads (token_hash, journal_id, first_read_at, client) VALUES (?,?,?,?)",
                (token_hash, journal_id, now, client),
            )
            con.execute(
                "INSERT INTO ip_events (ip_key, kind, ts) VALUES (?, 'read', ?)", (ip_key, now)
            )
            con.execute("COMMIT")
            return "ok", 0
        except BaseException:
            _rollback(con)
            raise
        finally:
            con.close()

    def purge(self, now: int) -> dict[str, int]:
        """到期删除：reads 满 1 年；ip_events 满 48 小时；
        devices 在「自身已满 1 年且已无任何 reads」后删除。"""
        con = self._tx()
        try:
            r = con.execute(
                "DELETE FROM reads WHERE first_read_at<=?", (now - READ_RETENTION_S,)
            ).rowcount
            e = con.execute(
                "DELETE FROM ip_events WHERE ts<=?", (now - IP_EVENT_RETENTION_S,)
            ).rowcount
            d = con.execute(
                "DELETE FROM devices WHERE created_at<=? AND NOT EXISTS "
                "(SELECT 1 FROM reads WHERE reads.token_hash=devices.token_hash)",
                (now - READ_RETENTION_S,),
            ).rowcount
            con.execute("COMMIT")
            return {"reads": r, "ip_events": e, "devices": d}
        except BaseException:
            _rollback(con)
            raise
        finally:
            con.close()
