"""期刊目录服务（services/journal_catalog）：线协议 v1、限额口径、隐私保留期。

服务不进 wheel/sdist；从源码树以外跑 pytest 时整个模块跳过。fixture 全在 tmp_path。
"""

from __future__ import annotations

import importlib
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

from services.journal_catalog import core  # noqa: E402
from services.journal_catalog.app import make_application  # noqa: E402
from services.journal_catalog.core import Config, ConfigError, Service  # noqa: E402
from services.journal_catalog.store import DAY, HOUR, READ_RETENTION_S, Limits  # noqa: E402

T0 = 1_800_000_000
SHA = "a" * 64
BUNDLE = b'{"journal_id":"j1"}'


class Clock:
    def __init__(self, t: int = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    (d / "r5" / "journals").mkdir(parents=True)
    (d / "r6" / "journals").mkdir(parents=True)
    (d / "snapshots").mkdir()
    (d / "index.signed.json").write_bytes(b'{"alg":"ed25519"}')
    for rev in ("r5", "r6"):
        (d / rev / "journals" / "j1.json").write_bytes(BUNDLE)
    for i in range(150):
        (d / "r5" / "journals" / f"k{i}.json").write_bytes(b"{}")
    (d / "snapshots" / f"{SHA}.webp").write_bytes(b"RIFFwebp")
    (tmp_path / "secret.txt").write_text("TOP")
    return d


def make(tmp_path, data, clock=None, **kw):
    cfg = Config(data, tmp_path / "db.sqlite", b"k" * 16, Limits(**kw))
    clk = clock or Clock()
    return Service(cfg, clock=clk), clk


@pytest.fixture
def svc(tmp_path, data):
    return make(tmp_path, data)


P = core.PREFIX


def call(s, method, path, query="", token=None, ip="1.2.3.4", client=None, xff=None):
    h = {"x-tavotto-device": token, "x-tavotto-client": client, "x-forwarded-for": xff}
    return s.handle(method, P + path, query, h, ip)


def body(r):
    return json.loads(r[2])


def new_token(s, ip="1.2.3.4", client=None):
    r = call(s, "POST", "/device", ip=ip, client=client)
    assert r[0] == 200, r
    return body(r)["token"]


def read(s, tok, jid="j1", rev=5, ip="1.2.3.4", client=None):
    return call(s, "GET", f"/journals/{jid}", f"rev={rev}", tok, ip, client)


def db_rows(s, sql):
    con = sqlite3.connect(s.store.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ---- 基本路由 ----
def test_index_no_token_and_headers(svc):
    s, _ = svc
    st, h, b = call(s, "GET", "/index.signed.json")
    assert st == 200 and b == b'{"alg":"ed25519"}'
    assert dict(h)["Cache-Control"] == "public, max-age=300"
    assert dict(h)["Content-Type"] == "application/json"
    assert db_rows(s, "select count(*) from reads") == [(0,)]


def test_device_token_stored_hashed(svc):
    s, _ = svc
    tok = new_token(s)
    assert len(tok) >= 32
    rows = db_rows(s, "select token_hash, created_at from devices")
    assert len(rows) == 1 and rows[0][0] != tok and len(rows[0][0]) == 64 and rows[0][1] == T0
    dump = json.dumps(db_rows(s, "select * from devices") + db_rows(s, "select * from ip_events"))
    assert tok not in dump and "1.2.3.4" not in dump


def test_unknown_route_and_method(svc):
    s, _ = svc
    assert call(s, "GET", "/nope")[0] == 404
    assert call(s, "GET", "/device")[0] == 405
    assert s.handle("GET", "/other", "", {}, "1.1.1.1")[0] == 404


# ---- 计数口径 ----
def test_first_read_counts_repeat_and_rev_change_do_not(svc):
    s, _ = svc
    tok = new_token(s)
    r = read(s, tok)
    assert r[0] == 200 and r[2] == BUNDLE
    assert dict(r[1])["Cache-Control"] == "private, no-store"
    assert len(db_rows(s, "select * from reads")) == 1
    assert read(s, tok)[0] == 200
    assert read(s, tok, rev=6)[0] == 200  # catalog_rev 变了重拉
    assert len(db_rows(s, "select * from reads")) == 1
    assert len(db_rows(s, "select * from ip_events where kind='read'")) == 1


def test_hour_quota_10_and_retry_after(tmp_path, data):
    s, clk = make(tmp_path, data)
    tok = new_token(s)
    for i in range(10):
        assert read(s, tok, f"k{i}")[0] == 200
        clk.t += 10
    r = read(s, tok, "k10")
    assert r[0] == 429 and body(r)["code"] == "quota_hour"
    # 最早一条在 T0，窗口 3600，现在 T0+100
    assert body(r)["retry_after_s"] == HOUR - 100
    assert dict(r[1])["Retry-After"] == str(HOUR - 100)
    assert read(s, tok, "k0")[0] == 200  # 重复读仍放行
    clk.t = T0 + HOUR  # 最早一条刚好滑出窗口
    assert read(s, tok, "k10")[0] == 200


def test_day_quota_20(tmp_path, data):
    s, clk = make(tmp_path, data)
    tok = new_token(s)
    for i in range(20):
        assert read(s, tok, f"k{i}")[0] == 200
        clk.t += 1000  # 每小时不超 10
    r = read(s, tok, "k20")
    assert r[0] == 429 and body(r)["code"] == "quota_day"
    assert body(r)["retry_after_s"] == DAY - 20 * 1000
    clk.t = T0 + DAY
    assert read(s, tok, "k20")[0] == 200


def test_ip_day_quota_100_across_tokens_and_clients(tmp_path, data):
    s, clk = make(tmp_path, data, ip_tokens_per_day=100, ip_reads_per_day=100)
    toks = [new_token(s, client="app" if i % 2 else "agent-codex") for i in range(11)]
    n = 0
    for i, tok in enumerate(toks):
        for j in range(10):
            if n == 100:
                break
            c = "app" if i % 2 else "agent-codex"
            assert read(s, tok, f"k{n}", client=c)[0] == 200
            n += 1
            clk.t += 1
    r = read(s, toks[10], "k100", client="agent-other")
    assert r[0] == 429 and body(r)["code"] == "ip_day"
    assert 0 < body(r)["retry_after_s"] <= DAY
    # 另一个 IP 不受影响
    tok2 = new_token(s, ip="9.9.9.9")
    assert read(s, tok2, "k100", ip="9.9.9.9")[0] == 200


def test_ip_token_cap(tmp_path, data):
    s, clk = make(tmp_path, data, ip_tokens_per_day=5)
    for _ in range(5):
        new_token(s)
        clk.t += 60
    r = call(s, "POST", "/device")
    assert r[0] == 429 and body(r)["code"] == "ip_tokens"
    assert body(r)["retry_after_s"] == DAY - 300
    clk.t = T0 + DAY
    assert call(s, "POST", "/device")[0] == 200


def test_ip_tokens_shared_across_client_headers(tmp_path, data):
    s, _ = make(tmp_path, data, ip_tokens_per_day=5)
    clients = ["app", "agent-a", "agent-b", "bogus!", None]
    for c in clients:
        new_token(s, client=c)
    assert call(s, "POST", "/device", client="agent-zzz")[0] == 429


def test_ipv6_same_64_shares_bucket(tmp_path, data):
    s, _ = make(tmp_path, data, ip_tokens_per_day=5)
    for i in range(5):
        new_token(s, ip=f"2001:db8:1:2::{i + 1}")
    assert call(s, "POST", "/device", ip="2001:db8:1:2:ffff::9")[0] == 429
    assert call(s, "POST", "/device", ip="2001:db8:1:3::1")[0] == 200


def test_xff_rightmost_mode(tmp_path, data):
    cfg = Config(
        data, tmp_path / "x.sqlite", b"s" * 8, Limits(ip_tokens_per_day=5), ip_mode="xff_rightmost"
    )
    s = Service(cfg, clock=Clock())
    for _ in range(5):
        call(s, "POST", "/device", ip="10.0.0.1", xff="6.6.6.6, 7.7.7.7")
    r = call(s, "POST", "/device", ip="10.0.0.2", xff="1.1.1.1, 7.7.7.7")
    assert r[0] == 429  # 最右一段相同
    assert call(s, "POST", "/device", ip="10.0.0.1", xff="6.6.6.6, 8.8.8.8")[0] == 200


# ---- client 头 ----
def test_client_header_recorded_and_invalid_is_unknown(svc):
    s, _ = svc
    t1 = new_token(s, client="agent-codex")
    t2 = new_token(s, client=None)
    t3 = new_token(s, client="Evil Host!")
    assert read(s, t1, "k1", client="agent-codex")[0] == 200
    assert read(s, t2, "k1")[0] == 200
    assert read(s, t3, "k1", client="x" * 80)[0] == 200  # 不报错
    assert sorted(r[0] for r in db_rows(s, "select client from devices")) == [
        "agent-codex",
        "app",
        "unknown",
    ]
    assert sorted(r[0] for r in db_rows(s, "select client from reads")) == [
        "agent-codex",
        "app",
        "unknown",
    ]
    assert core.parse_client("agent-" + "a" * 32) == "agent-" + "a" * 32
    assert core.parse_client("agent-" + "a" * 33) == "unknown"
    assert core.parse_client("agent-") == "unknown"
    assert core.parse_client("") == "app"


# ---- 校验与错误 ----
def test_unknown_token_401(svc):
    s, _ = svc
    assert read(s, "nope")[0] == 401
    assert body(read(s, "nope"))["code"] == "device_unknown"
    assert read(s, None)[0] == 401
    assert call(s, "GET", f"/snapshots/{SHA}.webp")[0] == 401


@pytest.mark.parametrize(
    "jid,query",
    [
        ("J1", "rev=5"),
        ("-a", "rev=5"),
        ("a b", "rev=5"),
        ("x" * 97, "rev=5"),
        ("j1", ""),
        ("j1", "rev=0"),
        ("j1", "rev=-1"),
        ("j1", "rev=abc"),
        ("j1", "rev=5&rev=6"),
        ("j1", "rev=05"),
        ("j1", "rev=1.5"),
        ("", "rev=5"),
    ],
)
def test_bad_request_400(svc, jid, query):
    s, _ = svc
    tok = new_token(s)
    r = call(s, "GET", f"/journals/{jid}", query, tok)
    assert r[0] == 400 and body(r) == {"code": "bad_request"}
    assert jid not in r[2].decode() or jid == ""


@pytest.mark.parametrize("jid", ["..", "../secret", "a/../../secret", "..%2fsecret", "a/b", "."])
def test_path_traversal_blocked(svc, jid):
    s, _ = svc
    tok = new_token(s)
    r = call(s, "GET", f"/journals/{jid}", "rev=5", tok)
    assert r[0] == 400
    assert b"TOP" not in r[2]


def test_symlink_escape_blocked(tmp_path, data):
    s, _ = make(tmp_path, data)
    (data / "r5" / "journals" / "evil.json").symlink_to(tmp_path / "secret.txt")
    tok = new_token(s)
    r = read(s, tok, "evil")
    assert r[0] == 404 and body(r)["code"] == "no_such_journal" and b"TOP" not in r[2]
    assert db_rows(s, "select * from reads where journal_id='evil'") == []


def test_missing_rev_dir_and_journal_404(svc):
    s, _ = svc
    tok = new_token(s)
    r = read(s, tok, rev=99)
    assert r[0] == 404 and body(r) == {"code": "stale_rev"}
    r = read(s, tok, "nosuch")
    assert r[0] == 404 and body(r) == {"code": "no_such_journal"}
    assert db_rows(s, "select * from reads") == []  # 404 不占额度


def test_snapshot(svc):
    s, _ = svc
    tok = new_token(s)
    st, h, b = call(s, "GET", f"/snapshots/{SHA}.webp", token=tok)
    assert st == 200 and b == b"RIFFwebp"
    assert dict(h)["Content-Type"] == "image/webp"
    assert dict(h)["Cache-Control"] == "public, max-age=31536000, immutable"
    assert db_rows(s, "select * from reads") == []
    assert call(s, "GET", f"/snapshots/{'b' * 64}.webp", token=tok)[0] == 404
    for bad in ("A" * 64 + ".webp", "abc.webp", f"{SHA}.png", "../x.webp", f"{SHA}.webp/x"):
        assert call(s, "GET", f"/snapshots/{bad}", token=tok)[0] in (400,), bad


# ---- 保留期 ----
def test_purge_reads_retention_boundary(tmp_path, data):
    s, clk = make(tmp_path, data)
    tok = new_token(s)
    read(s, tok, "k1")
    assert s.purge(T0 + READ_RETENTION_S - 1)["reads"] == 0  # 差 1 秒保留
    assert len(db_rows(s, "select * from reads")) == 1
    assert s.purge(T0 + READ_RETENTION_S)["reads"] == 1  # 到期删除
    assert db_rows(s, "select * from reads") == []


def test_purge_devices_only_after_reads_gone(tmp_path, data):
    s, _ = make(tmp_path, data)
    a = new_token(s)
    new_token(s)
    read(s, a, "k1")
    # 两个设备都满 1 年，但 a 仍有 reads（reads 与 device 同龄会在同一次被清）
    s.store.purge(T0 + READ_RETENTION_S - 1)
    assert len(db_rows(s, "select * from devices")) == 2
    out = s.purge(T0 + READ_RETENTION_S)
    assert out["reads"] == 1 and out["devices"] == 2
    assert db_rows(s, "select * from devices") == []


def test_purge_keeps_young_device_without_reads(tmp_path, data):
    s, _ = make(tmp_path, data)
    new_token(s)
    assert s.purge(T0 + 3600)["devices"] == 0


def test_purge_ip_events_48h(tmp_path, data):
    s, _ = make(tmp_path, data)
    new_token(s)
    assert s.purge(T0 + 48 * 3600 - 1)["ip_events"] == 0
    # ip_events 一行 + 可观测的 ip_devices 一行，同一个 48 小时
    assert s.purge(T0 + 48 * 3600)["ip_events"] == 2
    assert db_rows(s, "select * from ip_events") == []
    assert db_rows(s, "select * from ip_devices") == []


def test_ip_key_not_plaintext_and_secret_dependent(svc):
    s, _ = svc
    new_token(s, ip="203.0.113.7")
    (key,) = db_rows(s, "select ip_key from ip_events")[0]
    assert len(key) == 64 and "203" not in key
    assert core.ip_key(b"a", "203.0.113.7") != core.ip_key(b"b", "203.0.113.7")
    assert core.ip_key(b"a", "::ffff:203.0.113.7") == core.ip_key(b"a", "203.0.113.7")


# ---- 配置 / 入口 ----
def test_missing_secret_refuses_to_start(tmp_path, data):
    env = {"TAVOTTO_JC_DATA": str(data), "TAVOTTO_JC_DB": str(tmp_path / "d.sqlite")}
    with pytest.raises(ConfigError):
        core.config_from_env(env)
    with pytest.raises(ConfigError):
        core.config_from_env({**env, "TAVOTTO_JC_IP_SECRET": ""})
    ok = core.config_from_env(
        {**env, "TAVOTTO_JC_IP_SECRET": "s", "TAVOTTO_JC_READS_PER_HOUR": "3"}
    )
    assert ok.limits.reads_per_hour == 3 and ok.limits.reads_per_day == 20
    with pytest.raises(ConfigError):
        Config(data, tmp_path / "x", b"")


def test_wsgi_import_fails_without_secret(monkeypatch):
    monkeypatch.delenv("TAVOTTO_JC_IP_SECRET", raising=False)
    sys.modules.pop("services.journal_catalog.wsgi", None)
    with pytest.raises(ConfigError):
        importlib.import_module("services.journal_catalog.wsgi")
    sys.modules.pop("services.journal_catalog.wsgi", None)


def test_wsgi_end_to_end(svc):
    s, _ = svc
    app = make_application(s)

    def run(method, path, query="", headers=None):
        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": P + path,
            "QUERY_STRING": query,
            "REMOTE_ADDR": "4.4.4.4",
            **(headers or {}),
        }
        out = {}

        def sr(status, hdrs):
            out["status"], out["headers"] = status, dict(hdrs)

        data = b"".join(app(env, sr))
        return out["status"], out["headers"], data

    st, _, b = run("POST", "/device", headers={"HTTP_X_TAVOTTO_CLIENT": "agent-codex"})
    assert st == "200 OK"
    tok = json.loads(b)["token"]
    st, h, b = run("GET", "/journals/j1", "rev=5", {"HTTP_X_TAVOTTO_DEVICE": tok})
    assert st == "200 OK" and b == BUNDLE and h["Content-Length"] == str(len(BUNDLE))
    assert run("GET", "/journals/j1", "rev=5")[0] == "401 Unauthorized"
    assert db_rows(s, "select client from devices") == [("agent-codex",)]


def test_schema_is_d1_portable():
    sql = (ROOT / "services" / "journal_catalog" / "schema.sql").read_text(encoding="utf-8")
    sql = "\n".join(ln for ln in sql.splitlines() if not ln.lstrip().startswith("--"))
    assert "PRAGMA" not in sql.upper() and "WITHOUT ROWID" not in sql.upper()
    assert "AUTOINCREMENT" not in sql.upper()


# ---- 海外入口：反代带来的真实 IP 只在共享密钥对得上时才采信 ----
PROXY_SECRET = b"proxy-shared-secret"


def make_proxied(tmp_path, data, **kw):
    cfg = Config(data, tmp_path / "p.sqlite", b"k" * 16, Limits(**kw), proxy_secret=PROXY_SECRET)
    return Service(cfg, clock=Clock())


def proxied(
    s, method, path, query="", token=None, conn_ip="9.9.9.9", claimed="5.5.5.5", secret=PROXY_SECRET
):
    h = {
        "x-tavotto-device": token,
        "x-tavotto-proxy-secret": secret.decode() if secret is not None else None,
        "x-tavotto-client-ip": claimed,
    }
    return s.handle(method, P + path, query, h, conn_ip)


def test_proxy_secret_makes_claimed_ip_count(tmp_path, data):
    """不同的连接 IP（反代出口）+ 同一个声明的真实 IP = 同一个 IP 桶：令牌签发上限按真实 IP 算。"""
    s = make_proxied(tmp_path, data, ip_tokens_per_day=2)
    assert proxied(s, "POST", "/device", conn_ip="9.9.9.1")[0] == 200
    assert proxied(s, "POST", "/device", conn_ip="9.9.9.2")[0] == 200
    r = proxied(s, "POST", "/device", conn_ip="9.9.9.3")
    assert r[0] == 429 and body(r)["code"] == "ip_tokens"
    # 换一个真实 IP 就是另一个桶
    assert proxied(s, "POST", "/device", conn_ip="9.9.9.3", claimed="6.6.6.6")[0] == 200


@pytest.mark.parametrize("secret", [None, b"wrong-secret", b""])
def test_wrong_or_missing_secret_ignores_claimed_ip(tmp_path, data, secret):
    """伪造 `X-Tavotto-Client-IP` 绕过限额：密钥错 / 缺 → 忽略声明，按连接 IP 算（同一连接 IP 仍被限）。"""
    s = make_proxied(tmp_path, data, ip_tokens_per_day=1)
    assert proxied(s, "POST", "/device", claimed="1.1.1.1", secret=secret)[0] == 200
    r = proxied(s, "POST", "/device", claimed="2.2.2.2", secret=secret)  # 换声明的 IP 想绕过
    assert r[0] == 429 and body(r)["code"] == "ip_tokens"


def test_claimed_ip_ignored_when_service_has_no_proxy_secret(tmp_path, data):
    s, _ = make(tmp_path, data, ip_tokens_per_day=1)
    assert call(s, "POST", "/device", ip="9.9.9.9")[0] == 200
    h = {"x-tavotto-proxy-secret": "anything", "x-tavotto-client-ip": "3.3.3.3"}
    assert s.handle("POST", P + "/device", "", h, "9.9.9.9")[0] == 429


def test_invalid_claimed_ip_falls_back_to_connection_ip(tmp_path, data):
    s = make_proxied(tmp_path, data, ip_tokens_per_day=1)
    assert proxied(s, "POST", "/device", claimed="not-an-ip")[0] == 200
    assert proxied(s, "POST", "/device", claimed="also bad")[0] == 429


def test_app_and_proxy_entries_share_one_count(tmp_path, data):
    """同一个真实 IP 从直连入口和反代入口来，IP 级读取上限合计（计数只有一份）。"""
    s = make_proxied(tmp_path, data, ip_reads_per_day=3, reads_per_day=50, reads_per_hour=50)
    toks = [body(proxied(s, "POST", "/device", claimed="4.4.4.4"))["token"] for _ in range(4)]
    assert proxied(s, "GET", "/journals/k0", "rev=5", toks[0], claimed="4.4.4.4")[0] == 200
    assert proxied(s, "GET", "/journals/k1", "rev=5", toks[1], claimed="4.4.4.4")[0] == 200
    assert (
        s.handle("GET", P + "/journals/k2", "rev=5", {"x-tavotto-device": toks[2]}, "4.4.4.4")[0]
        == 200
    )
    r = proxied(s, "GET", "/journals/k3", "rev=5", toks[3], claimed="4.4.4.4")
    assert r[0] == 429 and body(r)["code"] == "ip_day"


def test_proxy_secret_from_env():
    cfg = core.config_from_env(
        {
            "TAVOTTO_JC_IP_SECRET": "s",
            "TAVOTTO_JC_DATA": "/d",
            "TAVOTTO_JC_DB": "/x.db",
            "TAVOTTO_JC_PROXY_SECRET": "abc",
        }
    )
    assert cfg.proxy_secret == b"abc"


# ---- Vercel 反代函数（services/journal_catalog/vercel_proxy/api/journal_catalog.py） ----
def _load_proxy():
    import importlib.util

    path = ROOT / "services" / "journal_catalog" / "vercel_proxy" / "api" / "journal_catalog.py"
    spec = importlib.util.spec_from_file_location("jc_vercel_proxy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Up:
    def __init__(self, status=200, headers=None, data=b"{}"):
        self.calls = []
        self.reply = (status, headers or {"Content-Type": "application/json"}, data)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.reply


def _env(query="p=index.signed.json", method="GET", **extra):
    import io

    return {
        "REQUEST_METHOD": method,
        "QUERY_STRING": query,
        "HTTP_X_FORWARDED_FOR": "203.0.113.7, 10.0.0.1",
        "wsgi.input": io.BytesIO(b""),
        **extra,
    }


CFG = {"TAVOTTO_JC_PROXY_SECRET": "sek", "TAVOTTO_JC_ORIGIN": "https://origin.example"}


def test_proxy_forwards_whitelisted_paths_with_secret_and_real_ip():
    px = _load_proxy()
    up = _Up(data=b"BUNDLE")
    st, h, b = px.proxy(
        _env(
            "p=journals%2Fj1&rev=5",
            HTTP_X_TAVOTTO_DEVICE="tok",
            HTTP_X_TAVOTTO_CLIENT="agent-codex",
        ),
        CFG,
        up,
    )
    ((method, url, headers, _),) = up.calls
    assert (st, b, method) == (200, b"BUNDLE", "GET")
    assert url == "https://origin.example/v1/journal-catalog/journals/j1?rev=5"
    assert (
        headers["X-Tavotto-Proxy-Secret"] == "sek"
        and headers["X-Tavotto-Client-IP"] == "203.0.113.7"
    )
    assert headers["X-Tavotto-Device"] == "tok" and headers["X-Tavotto-Client"] == "agent-codex"
    assert dict(h)["Cache-Control"] == "private, no-store"  # 刊包要回源计数，不缓存


def test_proxy_drops_client_supplied_trust_headers():
    px = _load_proxy()
    up = _Up()
    px.proxy(
        _env(
            "p=device",
            method="POST",
            HTTP_X_TAVOTTO_PROXY_SECRET="attacker",
            HTTP_X_TAVOTTO_CLIENT_IP="1.2.3.4",
        ),
        CFG,
        up,
    )
    headers = up.calls[0][2]
    assert headers["X-Tavotto-Proxy-Secret"] == "sek"  # 只有我们自己的密钥
    assert headers["X-Tavotto-Client-IP"] == "203.0.113.7"  # 取 Vercel 的 XFF 第一段，不是伪造值


def test_only_the_signed_index_is_edge_cached():
    px = _load_proxy()
    st, h, _ = px.proxy(_env("p=index.signed.json"), CFG, _Up())
    assert st == 200 and dict(h)["Cache-Control"] == "public, max-age=300, s-maxage=300"
    for q, m in (("p=device", "POST"), (f"p=snapshots%2F{SHA}.webp", "GET")):
        _, h, _ = px.proxy(_env(q, method=m), CFG, _Up())
        assert dict(h)["Cache-Control"] == "private, no-store"
    # 源站的错误（429 等）原样透传，且不缓存
    st, h, b = px.proxy(
        _env("p=device", method="POST"),
        CFG,
        _Up(
            429, {"Content-Type": "application/json", "Retry-After": "60"}, b'{"code":"ip_tokens"}'
        ),
    )
    assert (
        st == 429
        and dict(h)["Retry-After"] == "60"
        and dict(h)["Cache-Control"] == "private, no-store"
    )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "p=",
        "p=..%2F..%2Fetc%2Fpasswd",
        "p=journals%2F..%2Fx",
        "p=admin",
        "p=journals%2FUPPER",
        "p=snapshots%2Fx.webp",
    ],
)
def test_proxy_rejects_other_paths(query):
    px = _load_proxy()
    up = _Up()
    st, _, _ = px.proxy(_env(query), CFG, up)
    assert st == 404 and up.calls == []


def test_proxy_methods_and_config():
    px = _load_proxy()
    up = _Up()
    assert px.proxy(_env(method="DELETE"), CFG, up)[0] == 405
    assert px.proxy(_env(), {}, up)[0] == 503  # 没配密钥：拒绝工作
    assert up.calls == []


def test_proxy_origin_failure_is_502():
    import urllib.error

    px = _load_proxy()

    def boom(*a):
        raise urllib.error.URLError("down")

    assert px.proxy(_env(), CFG, boom)[0] == 502
