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
IP_EVENT_RETENTION_S = 48 * 3600  # ip_events / ip_devices / ip_denials 保留 48 小时
RULE_NAMES = ("ip_tokens_per_day", "reads_per_hour", "reads_per_day", "ip_reads_per_day")


@dataclass(frozen=True)
class Limits:
    """限额（全部可配置：环境变量是缺省，规则表 `rules` / `ip_cidr_rules` 可在运行时覆盖，见 core / admin）。

    IP 维度默认放得很宽（用户 2026-11-05 拍板）：学校 / 公司 / 宿舍整片人共用一个出口 IP，「每 IP 每天 5 个新令牌」
    会让第 6 个同学起拿不到令牌。200 个新令牌 / 天 ≈ 一个出口背后约 200 台设备；200 台 × 每台约 10 本的日均首次读取
    （设备自己的上限是 20 本 / 天，日均远低于上限）≈ 2000 本 / 天。IP 维度只防「一个出口大规模刷」，不是防个人；
    设备维度（10 / 小时、20 / 天）不变。`None` = 不限（规则表里的豁免与紧急开关把 IP 维度置 None）。
    """

    ip_tokens_per_day: int | None = 200
    reads_per_hour: int = 10
    reads_per_day: int = 20
    ip_reads_per_day: int | None = 2000


def _retry(oldest_ts: int, window: int, now: int) -> int:
    return max(1, oldest_ts + window - now)


def _note_denial(con: sqlite3.Connection, ip_key: str, code: str, now: int) -> None:
    con.execute(
        "INSERT INTO ip_denials (ip_key, day, code, n, ts) VALUES (?,?,?,1,?) "
        "ON CONFLICT(ip_key, day, code) DO UPDATE SET n=n+1, ts=excluded.ts",
        (ip_key, now // DAY, code, now),
    )


def _note_device(con: sqlite3.Connection, ip_key: str, dev_tag: str, now: int) -> None:
    if dev_tag:
        con.execute(
            "INSERT OR IGNORE INTO ip_devices (ip_key, day, dev_tag, ts) VALUES (?,?,?,?)",
            (ip_key, now // DAY, dev_tag, now),
        )


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
        self,
        token_hash: str,
        ip_key: str,
        now: int,
        limits: Limits,
        client: str = "app",
        dev_tag: str = "",
    ) -> tuple[bool, int]:
        """返回 (是否签发, retry_after_s)。"""
        con = self._tx()
        try:
            rows = con.execute(
                "SELECT ts FROM ip_events WHERE ip_key=? AND kind='token' AND ts>?",
                (ip_key, now - DAY),
            ).fetchall()
            if limits.ip_tokens_per_day is not None and len(rows) >= limits.ip_tokens_per_day:
                _note_denial(con, ip_key, "ip_tokens", now)
                con.execute("COMMIT")
                return False, _retry(min(r[0] for r in rows), DAY, now)
            _note_device(con, ip_key, dev_tag, now)
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
        dev_tag: str = "",
    ) -> tuple[str, int]:
        """返回 (结果, retry_after_s)。结果：ok / repeat / unknown /
        quota_hour / quota_day / ip_day。repeat 与 ok 都表示放行。"""
        con = self._tx()
        try:
            known = con.execute("SELECT 1 FROM devices WHERE token_hash=?", (token_hash,))
            if known.fetchone() is None:
                con.execute("ROLLBACK")
                return "unknown", 0
            # 令牌有效就记一次「这台设备在这个出口出现过」——放在任何提前返回（重复读、限额拒绝）之前，
            # 否则出口被限额卡住时，背后真实的设备数会被低估，`top` 区分共享出口与刷量的判据就失真。
            _note_device(con, ip_key, dev_tag, now)
            seen = con.execute(
                "SELECT 1 FROM reads WHERE token_hash=? AND journal_id=?",
                (token_hash, journal_id),
            )
            if seen.fetchone() is not None:
                con.execute("COMMIT")
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
                    _note_denial(con, ip_key, code, now)
                    con.execute("COMMIT")
                    return code, _retry(min(r[0] for r in rows), window, now)
            rows = con.execute(
                "SELECT ts FROM ip_events WHERE ip_key=? AND kind='read' AND ts>?",
                (ip_key, now - DAY),
            ).fetchall()
            if limits.ip_reads_per_day is not None and len(rows) >= limits.ip_reads_per_day:
                _note_denial(con, ip_key, "ip_day", now)
                con.execute("COMMIT")
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
            for table in ("ip_devices", "ip_denials"):
                e += con.execute(
                    f"DELETE FROM {table} WHERE ts<=?",  # noqa: S608 - 表名是上面的常量
                    (now - IP_EVENT_RETENTION_S,),
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

    # ------------------------------------------------------------------ 规则（运行时可调）
    def load_rules(self) -> dict:
        """读规则表：`{"defaults": {名: int}, "kill_switch": bool, "cidrs": [(cidr, exempt, tokens|None, reads|None)]}`。"""
        con = self._connect()
        try:
            kv = dict(con.execute("SELECT key, value FROM rules").fetchall())
            cidrs = con.execute(
                "SELECT cidr, exempt, ip_tokens_per_day, ip_reads_per_day FROM ip_cidr_rules"
            ).fetchall()
        finally:
            con.close()
        defaults: dict[str, int] = {}
        for k, v in kv.items():
            if k.startswith("default.") and k[len("default.") :] in RULE_NAMES:
                try:
                    defaults[k[len("default.") :]] = int(v)
                except ValueError:
                    continue
        return {
            "defaults": defaults,
            "kill_switch": kv.get("kill_switch") == "1",
            "cidrs": [(c, bool(x), t, r) for c, x, t, r in cidrs],
        }

    def _audited(self, actor: str, action: str, now: int, fn) -> str:
        """一个事务里改规则 + 写审计。`fn(con)` 返回审计 detail。"""
        con = self._tx()
        try:
            detail = fn(con)
            con.execute(
                "INSERT INTO rule_audit (ts, actor, action, detail) VALUES (?,?,?,?)",
                (now, actor, action, detail),
            )
            con.execute("COMMIT")
            return detail
        except BaseException:
            _rollback(con)
            raise
        finally:
            con.close()

    def set_default(self, name: str, value: int, actor: str, now: int) -> str:
        def run(con):
            old = con.execute(
                "SELECT value FROM rules WHERE key=?", (f"default.{name}",)
            ).fetchone()
            con.execute(
                "INSERT INTO rules (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (f"default.{name}", str(value), now),
            )
            return f"default.{name}: {old[0] if old else '(未设)'} -> {value}"

        return self._audited(actor, "set", now, run)

    def clear_default(self, name: str, actor: str, now: int) -> str:
        def run(con):
            old = con.execute(
                "SELECT value FROM rules WHERE key=?", (f"default.{name}",)
            ).fetchone()
            con.execute("DELETE FROM rules WHERE key=?", (f"default.{name}",))
            return (
                f"default.{name}: {old[0] if old else '(未设)'} -> (清除，回到环境变量 / 内置默认)"
            )

        return self._audited(actor, "clear", now, run)

    def set_kill_switch(self, on: bool, actor: str, now: int) -> str:
        def run(con):
            old = con.execute("SELECT value FROM rules WHERE key='kill_switch'").fetchone()
            con.execute(
                "INSERT INTO rules (key, value, updated_at) VALUES ('kill_switch',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("1" if on else "0", now),
            )
            return f"kill_switch: {'on' if old and old[0] == '1' else 'off'} -> {'on' if on else 'off'}"

        return self._audited(actor, "kill-switch", now, run)

    def set_cidr(
        self,
        cidr: str,
        exempt: bool,
        tokens: int | None,
        reads: int | None,
        note: str,
        actor: str,
        now: int,
    ) -> str:
        def run(con):
            con.execute(
                "INSERT INTO ip_cidr_rules (cidr, exempt, ip_tokens_per_day, ip_reads_per_day, note, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(cidr) DO UPDATE SET exempt=excluded.exempt, "
                "ip_tokens_per_day=excluded.ip_tokens_per_day, ip_reads_per_day=excluded.ip_reads_per_day, "
                "note=excluded.note, updated_at=excluded.updated_at",
                (cidr, 1 if exempt else 0, tokens, reads, note, now),
            )
            what = "完全豁免 IP 维度" if exempt else f"tokens={tokens} reads={reads}"
            return f"cidr {cidr}: {what}"

        return self._audited(actor, "exempt-cidr", now, run)

    def clear_cidr(self, cidr: str, actor: str, now: int) -> str:
        def run(con):
            n = con.execute("DELETE FROM ip_cidr_rules WHERE cidr=?", (cidr,)).rowcount
            return f"cidr {cidr}: 删除（{'有' if n else '本来就没有'}）"

        return self._audited(actor, "clear", now, run)

    def audit_log(self, n: int = 50) -> list[tuple[int, str, str, str]]:
        con = self._connect()
        try:
            return con.execute(
                "SELECT ts, actor, action, detail FROM rule_audit ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        finally:
            con.close()

    def top_ips(self, now: int, n: int) -> list[tuple[str, int, dict[str, int]]]:
        """当天（UTC）按 ip_key 的不同设备数排序：`[(ip_key, 设备数, {code: 被拒次数})]`。"""
        day = now // DAY
        con = self._connect()
        try:
            devs = dict(
                con.execute(
                    "SELECT ip_key, COUNT(*) FROM ip_devices WHERE day=? GROUP BY ip_key", (day,)
                ).fetchall()
            )
            den: dict[str, dict[str, int]] = {}
            for k, code, c in con.execute(
                "SELECT ip_key, code, n FROM ip_denials WHERE day=?", (day,)
            ).fetchall():
                den.setdefault(k, {})[code] = c
        finally:
            con.close()
        keys = set(devs) | set(den)
        rows = [(k, devs.get(k, 0), den.get(k, {})) for k in keys]
        rows.sort(key=lambda r: (-r[1], -sum(r[2].values()), r[0]))
        return rows[:n]
