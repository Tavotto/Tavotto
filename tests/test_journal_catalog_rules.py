"""期刊目录服务的限流规则可调（ADR 0155「限流可调」，用户 2026-11-05）：默认值、网段例外、紧急开关、
热更新（TTL）、审计、可观测、管理命令。fixture 全在 tmp_path。"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "services" / "journal_catalog").is_dir():
    pytest.skip("没有 services/journal_catalog（wheel/sdist 里不含）", allow_module_level=True)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.journal_catalog import admin, core  # noqa: E402
from services.journal_catalog.core import Config, Service  # noqa: E402
from services.journal_catalog.store import Limits, Store  # noqa: E402

T0 = 1_800_000_000
P = core.PREFIX


class Clock:
    def __init__(self, t: int = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def env(tmp_path):
    d = tmp_path / "data"
    (d / "r5" / "journals").mkdir(parents=True)
    for i in range(30):
        (d / "r5" / "journals" / f"k{i}.json").write_bytes(b"{}")
    (d / "index.signed.json").write_bytes(b"{}")
    clk = Clock()
    s = Service(
        Config(d, tmp_path / "db.sqlite", b"k" * 16, Limits(ip_tokens_per_day=3)), clock=clk
    )
    return s, clk, tmp_path / "db.sqlite"


def device(s, ip):
    return s.handle("POST", P + "/device", "", {}, ip)


def run_admin(db, *argv, clock=None, by="tester"):
    out = io.StringIO()
    rc = admin.run(["--db", str(db), "--by", by, *argv], env={}, clock=clock or Clock(), out=out)
    return rc, out.getvalue()


def test_builtin_defaults_are_wide_for_a_shared_exit_ip():
    d = Limits()
    assert (d.ip_tokens_per_day, d.ip_reads_per_day) == (200, 2000)
    assert (d.reads_per_hour, d.reads_per_day) == (10, 20)  # 设备维度不变


def test_cidr_exception_hits_and_misses(env):
    s, clk, db = env
    assert run_admin(db, "rules", "exempt-cidr", "203.0.113.0/24", "--tokens", "5")[0] == 0
    clk.t += core.RULES_TTL_S
    for _ in range(5):
        assert device(s, "203.0.113.9")[0] == 200  # 命中：放宽到 5
    assert device(s, "203.0.113.9")[0] == 429
    for _ in range(3):
        assert device(s, "198.51.100.1")[0] == 200  # 没命中：仍是默认 3
    assert device(s, "198.51.100.1")[0] == 429


def test_full_exemption_and_longest_prefix_wins(env):
    s, clk, db = env
    run_admin(db, "rules", "exempt-cidr", "203.0.113.0/24")
    run_admin(db, "rules", "exempt-cidr", "203.0.113.128/25", "--tokens", "2")
    clk.t += core.RULES_TTL_S
    for _ in range(10):
        assert device(s, "203.0.113.5")[0] == 200  # /24 豁免
    assert device(s, "203.0.113.200")[0] == 200
    assert device(s, "203.0.113.200")[0] == 200
    assert device(s, "203.0.113.200")[0] == 429  # 更具体的 /25 只放宽到 2


def test_ipv6_prefix_rule(env):
    s, clk, db = env
    run_admin(db, "rules", "exempt-cidr", "2001:db8:aa::/48", "--tokens", "6")
    clk.t += core.RULES_TTL_S
    for i in range(6):
        assert device(s, f"2001:db8:aa:{i}::1")[0] == 200  # 同 /48 内不同 /64 各自的桶，规则都命中
    for _ in range(6):
        assert device(s, "2001:db8:aa:9::1")[0] == 200
    assert device(s, "2001:db8:aa:9::2")[0] == 429  # 同一 /64 共用桶，上限 6
    assert [device(s, "2001:db8:bb::1")[0] for _ in range(4)] == [
        200,
        200,
        200,
        429,
    ]  # /48 之外仍默认


def test_ipv4_mapped_ipv6_matches_ipv4_rule(env):
    s, clk, db = env
    run_admin(db, "rules", "exempt-cidr", "203.0.113.0/24")
    clk.t += core.RULES_TTL_S
    for _ in range(6):
        assert device(s, "::ffff:203.0.113.7")[0] == 200


def test_kill_switch_turns_off_ip_limits_only(env):
    s, clk, db = env
    for _ in range(3):
        assert device(s, "198.51.100.1")[0] == 200
    assert device(s, "198.51.100.1")[0] == 429
    run_admin(db, "kill-switch", "on")
    clk.t += core.RULES_TTL_S
    for _ in range(10):
        assert device(s, "198.51.100.1")[0] == 200
    # 设备维度照常：同一个令牌 20 本 / 天
    tok = json.loads(device(s, "198.51.100.1")[2])["token"]
    codes = [
        s.handle("GET", P + f"/journals/k{i}", "rev=5", {"x-tavotto-device": tok}, "198.51.100.1")[
            0
        ]
        for i in range(22)
    ]
    assert codes[:10] == [200] * 10 and codes[10] == 429  # reads_per_hour=10
    run_admin(db, "kill-switch", "off")
    clk.t += core.RULES_TTL_S
    assert device(s, "198.51.100.1")[0] == 429  # 前面已签了 10+ 个，回到默认 3 的上限


def test_rules_hot_reload_after_ttl_without_restart(env):
    s, clk, db = env
    for _ in range(3):
        device(s, "198.51.100.1")
    assert device(s, "198.51.100.1")[0] == 429
    run_admin(db, "rules", "set", "ip_tokens_per_day", "50")
    clk.t += core.RULES_TTL_S - 1
    assert device(s, "198.51.100.1")[0] == 429  # TTL 内仍是旧快照
    clk.t += 1
    assert device(s, "198.51.100.1")[0] == 200  # TTL 过期即生效
    run_admin(db, "rules", "clear", "ip_tokens_per_day")
    clk.t += core.RULES_TTL_S
    assert device(s, "198.51.100.1")[0] == 429  # 清除后回到环境变量 / 内置默认（这里 3）


def test_rule_table_default_overrides_env_value_and_env_is_the_fallback(env):
    s, clk, db = env
    assert s.rules().defaults == {}  # 表里没写 → 用 Config.limits（环境变量）
    assert s.config.limits.ip_tokens_per_day == 3
    run_admin(db, "rules", "set", "reads_per_hour", "4")
    clk.t += core.RULES_TTL_S
    assert s._limits_for("1.2.3.4").reads_per_hour == 4
    assert s._limits_for("1.2.3.4").ip_tokens_per_day == 3


def test_audit_log_has_actor_time_change_and_no_client_ip(env):
    s, clk, db = env
    device(s, "203.0.113.77")
    device(s, "203.0.113.77")
    clk_admin = Clock(T0 + 5)
    run_admin(db, "rules", "set", "ip_tokens_per_day", "99", clock=clk_admin, by="alice")
    run_admin(
        db,
        "rules",
        "exempt-cidr",
        "203.0.113.0/24",
        "--note",
        "校园网",
        clock=clk_admin,
        by="alice",
    )
    run_admin(db, "kill-switch", "on", clock=clk_admin, by="bob")
    rows = Store(db).audit_log()
    assert [(r[0], r[1], r[2]) for r in reversed(rows)] == [
        (T0 + 5, "alice", "set"),
        (T0 + 5, "alice", "exempt-cidr"),
        (T0 + 5, "bob", "kill-switch"),
    ]
    assert "203.0.113.0/24" in rows[1][3]  # 管理员写的网段可以记
    # 整个库（所有表）里都不出现请求过的明文 IP
    con = sqlite3.connect(db)
    dump = "\n".join(con.iterdump())
    con.close()
    assert "203.0.113.77" not in dump


def test_actor_defaults_to_user_env(env):
    _, _, db = env
    out = io.StringIO()
    admin.run(["--db", str(db), "kill-switch", "on"], env={"USER": "carol"}, clock=Clock(), out=out)
    assert Store(db).audit_log()[0][1] == "carol"


def test_admin_rejects_bad_input(env):
    _, _, db = env
    assert run_admin(db, "rules", "exempt-cidr", "0.0.0.0/0")[0] == 2
    assert run_admin(db, "rules", "exempt-cidr", "not-a-cidr")[0] == 2
    assert run_admin(db, "rules", "set", "ip_tokens_per_day", "0")[0] == 2
    assert run_admin(db, "rules", "set", "ip_tokens_per_day", "abc")[0] == 2
    assert Store(db).audit_log() == []  # 失败不留审计


def test_rules_list_shows_everything(env):
    _, _, db = env
    run_admin(db, "rules", "set", "ip_reads_per_day", "3000")
    run_admin(db, "rules", "exempt-cidr", "198.51.100.0/24")
    rc, text = run_admin(db, "rules", "list")
    assert (
        rc == 0
        and "ip_reads_per_day = 3000" in text
        and "198.51.100.0/24" in text
        and "完全豁免" in text
    )


def test_top_counts_distinct_devices_and_denials_with_8_char_key_prefix(env):
    s, clk, db = env
    for _ in range(3):
        device(s, "198.51.100.1")
    for _ in range(2):
        assert device(s, "198.51.100.1")[0] == 429
    device(s, "198.51.100.2")
    rc, text = run_admin(db, "top", "--n", "5", clock=clk)
    lines = text.strip().splitlines()
    assert rc == 0 and len(lines) == 2
    key1 = core.ip_key(b"k" * 16, "198.51.100.1")
    assert lines[0].startswith(key1[:8]) and "设备 3" in lines[0] and "ip_tokens=2" in lines[0]
    assert key1[:9] not in text  # 只显示前 8 位
    assert "设备 1" in lines[1]
    rc, one = run_admin(db, "top", "--n", "1", clock=clk)
    assert len(one.strip().splitlines()) == 1


def test_observability_rows_expire_after_48h(env):
    s, clk, _ = env
    for _ in range(4):
        device(s, "198.51.100.1")
    assert s.purge(T0 + 48 * 3600 - 1)["ip_events"] == 0
    s.purge(T0 + 48 * 3600)
    con = sqlite3.connect(s.config.db_path)
    assert con.execute("select count(*) from ip_devices").fetchone()[0] == 0
    assert con.execute("select count(*) from ip_denials").fetchone()[0] == 0
    con.close()


def test_schema_adds_only_portable_syntax():
    sql = (ROOT / "services" / "journal_catalog" / "schema.sql").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")).upper()
    for banned in ("PRAGMA", "WITHOUT ROWID", "AUTOINCREMENT"):
        assert banned not in code


def test_devices_behind_a_capped_exit_are_still_counted(tmp_path):
    """出口已被读取限额卡住时，背后每台持有效令牌的设备照样记进当天设备数（重复读、被拒都算出现过）。"""
    d = tmp_path / "data"
    (d / "r5" / "journals").mkdir(parents=True)
    for i in range(3):
        (d / "r5" / "journals" / f"k{i}.json").write_bytes(b"{}")
    (d / "index.signed.json").write_bytes(b"{}")
    clk = Clock()
    s = Service(Config(d, tmp_path / "db.sqlite", b"k" * 16, Limits(ip_reads_per_day=1)), clock=clk)
    toks = [json.loads(device(s, f"192.0.2.{i}")[2])["token"] for i in range(1, 4)]
    exit_ip = "198.51.100.9"

    def read(tok, j):
        return s.handle("GET", P + f"/journals/{j}", "rev=5", {"x-tavotto-device": tok}, exit_ip)[0]

    assert read(toks[0], "k0") == 200
    assert read(toks[0], "k0") == 200  # 重复读
    assert read(toks[1], "k1") == 429
    assert read(toks[2], "k2") == 429
    rc, text = run_admin(tmp_path / "db.sqlite", "top", "--n", "1", clock=clk)
    key = core.ip_key(b"k" * 16, exit_ip)
    line = text.strip().splitlines()[0]
    assert rc == 0 and line.startswith(key[:8]) and "设备 3" in line and "ip_day=2" in line
