"""远程期刊目录（`engine/journalremote`）：验签、rev 单调、整份校验、离线回退、按需下载与限额。

服务端是假的：`FakeServer` 按线协议（ADR 0155 §10）回字节，请求计数供断言。真服务的计数口径在
`tests/test_journal_catalog_service.py`。夹具签名只在 `tests/journal_catalog_signing.py`。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from journal_catalog_signing import (
    OTHER_SEED,
    TEST_SEED,
    b64,
    build_site,
    canon,
    envelope,
    public_key,
)
from tavotto.engine import journalremote as jr

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "golden" / "journal_vectors.json").read_text("utf-8"))
BASE = "https://catalog.example.invalid/v1/"
SNAP_BYTES = b"RIFF-fake-webp"
SNAP_SHA = hashlib.sha256(SNAP_BYTES).hexdigest()


def _catalog(rev: int = 5) -> dict:
    cat = copy.deepcopy(VECTORS["catalog"])
    cat["catalog_rev"] = rev
    return cat


class FakeServer:
    def __init__(self, catalog: dict | None = None, rev: int = 5):
        self.files = build_site(catalog or _catalog(rev), rev=rev)
        self.rev = rev
        self.calls: list[tuple[str, str, dict]] = []
        self.offline = False
        self.known_tokens: set[str] = set()
        self.quota: dict | None = None  # {"code":..., "retry_after_s":...}
        self.snapshots = {SNAP_SHA: SNAP_BYTES}
        self.reads: set[tuple[str, str]] = set()

    def __call__(self, method, url, cap, headers):
        self.calls.append((method, url, dict(headers)))
        if self.offline:
            raise jr.RemoteCatalogError("network", "offline")
        assert url.startswith(BASE)
        path = url[len(BASE) :]
        if method == "POST" and path == "device":
            tok = f"tok{len(self.known_tokens):04d}" + "x" * 20
            self.known_tokens.add(tok)
            return 200, json.dumps({"token": tok}).encode()
        if path == "index.signed.json":
            return 200, self.files[path]
        tok = headers.get("X-Tavotto-Device")
        if tok not in self.known_tokens:
            return 401, b'{"code":"device_unknown"}'
        m = re.fullmatch(r"journals/([^?]+)\?rev=(\d+)", path)
        if m:
            jid, rev = m.group(1), int(m.group(2))
            if rev != self.rev:
                return 404, b'{"code":"stale_rev"}'
            if (tok, jid) not in self.reads and self.quota:
                return 429, json.dumps(self.quota).encode()
            self.reads.add((tok, jid))
            return 200, self.files[f"r{rev}/journals/{jid}.json"]
        m = re.fullmatch(r"snapshots/([0-9a-f]{64})\.webp", path)
        if m and m.group(1) in self.snapshots:
            return 200, self.snapshots[m.group(1)]
        return 404, b'{"code":"no_such_journal"}'

    def count(self, what: str) -> int:
        return sum(1 for _, u, _ in self.calls if what in u)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("TAVOTTO_NO_JOURNAL_SYNC", raising=False)
    monkeypatch.setattr(jr, "CATALOG_URLS", (BASE,))
    # 生产代码只信 prod-1；夹具签名用测试钥，只在这里注入
    monkeypatch.setattr(jr, "TRUSTED_KEYS", {"test-1": b64(public_key(TEST_SEED))})
    monkeypatch.setattr(jr, "_SNAPSHOT_FAILED", {})
    monkeypatch.setattr(jr, "seed_catalog", lambda: {"publishers": {}, "journals": {}})
    monkeypatch.setattr(jr, "seed_rev", lambda: 1)
    server = FakeServer()
    monkeypatch.setattr(jr, "_request", server)
    return server


# ------------------------------ 索引：成功路径 ------------------------------
def test_sync_installs_signed_index_and_caches(env):
    out = jr.sync(force=True)
    assert (out["fetch"], out["code"], out["rev"]) == ("updated", None, 5)
    cat = out["catalog"]
    assert set(cat["journals"]) == set(_catalog()["journals"])
    # 骨架：没下载的刊只有名称，没有规则；已下载的刊才有
    assert "rules" not in cat["journals"]["fx-beta"]
    assert out["loaded"] == []
    assert (jr.cache_dir() / "index.signed.json").is_file()


def test_second_sync_is_unchanged_and_skips_catalog_when_caller_is_current(env):
    jr.sync(force=True)
    out = jr.sync(force=True, have=5)
    assert (out["fetch"], out["rev"], out["catalog"]) == ("unchanged", 5, None)
    assert jr.sync(force=True, have=0)["catalog"] is not None  # 前端缓存空时取回缓存


def test_sync_is_throttled_within_a_day_but_force_overrides(env):
    jr.sync(now=1_000.0)
    n = len(env.calls)
    out = jr.sync(now=1_000.0 + 3600)
    assert out["fetch"] == "throttled" and len(env.calls) == n
    assert jr.sync(now=1_000.0 + 3600, force=True)["fetch"] == "unchanged"


def test_failed_sync_retries_after_an_hour(env):
    env.offline = True
    assert jr.sync(now=1_000.0)["fetch"] == "offline"
    assert jr.sync(now=1_000.0 + 60)["fetch"] == "throttled"
    env.offline = False
    assert jr.sync(now=1_000.0 + 3601)["fetch"] == "updated"


def test_request_headers_carry_only_client_type_and_token(env):
    jr.sync(force=True)
    jr.ensure_journal("fx-beta")
    for _method, url, headers in env.calls:
        assert "?" not in url or re.search(r"\?rev=\d+$", url)
        assert set(headers) <= {"User-Agent", "Accept", "X-Tavotto-Client", "X-Tavotto-Device"}
        assert headers["X-Tavotto-Client"] == "app"


# ------------------------------ 索引：被拒绝 ------------------------------
def _tamper_payload(raw: bytes) -> bytes:
    env_ = json.loads(raw)
    payload = bytearray(jr._b64decode(env_["payload"]))
    payload[10] ^= 1
    env_["payload"] = __import__("base64").b64encode(bytes(payload)).decode()
    return json.dumps(env_).encode()


def test_bad_signature_is_rejected_and_cache_is_kept(env):
    jr.sync(force=True)
    env.files["index.signed.json"] = _tamper_payload(env.files["index.signed.json"])
    out = jr.sync(force=True)
    assert (out["fetch"], out["code"]) == ("rejected", "signature_invalid")
    assert out["rev"] == 5  # 缓存原样可用


def test_wrong_private_key_is_rejected(env):
    env.files["index.signed.json"] = build_site(_catalog(), seed=OTHER_SEED)["index.signed.json"]
    out = jr.sync(force=True)
    assert (out["fetch"], out["code"], out["catalog"]) == ("rejected", "signature_invalid", None)


def test_missing_signature_is_rejected(env):
    doc = json.loads(env.files["index.signed.json"])
    del doc["sig"]
    env.files["index.signed.json"] = json.dumps(doc).encode()
    assert jr.sync(force=True)["code"] == "signature_missing"
    env.files["index.signed.json"] = json.dumps(
        {"alg": "ed25519", "key_id": "test-1", "payload": "e30="}
    ).encode()
    assert jr.sync(force=True)["code"] == "signature_missing"


def test_unknown_key_id_and_unsigned_plain_index_are_rejected(env):
    index = json.loads(jr._b64decode(json.loads(env.files["index.signed.json"])["payload"]))
    env.files["index.signed.json"] = envelope(index, key_id="rogue-9")
    assert jr.sync(force=True)["code"] == "key_unknown"
    env.files["index.signed.json"] = canon(index)  # 没有信封的裸索引
    assert jr.sync(force=True)["code"] == "signature_missing"


def test_rev_rollback_is_rejected(env):
    jr.sync(force=True)
    env.files = build_site(_catalog(4), rev=4)
    out = jr.sync(force=True)
    assert (out["fetch"], out["code"], out["rev"]) == ("rejected", "rev_rollback", 5)


def test_rev_below_seed_is_rejected(env, monkeypatch):
    monkeypatch.setattr(jr, "seed_rev", lambda: 9)
    out = jr.sync(force=True)
    assert (out["code"], out["catalog"]) == ("rev_rollback", None)


def test_malformed_index_is_rejected(env):
    index = json.loads(jr._b64decode(json.loads(env.files["index.signed.json"])["payload"]))
    bad = copy.deepcopy(index)
    bad["journals"]["fx-beta"]["bundle"]["sha256"] = "zz"
    env.files["index.signed.json"] = envelope(bad)
    assert jr.sync(force=True)["code"] == "index_invalid"
    bad = copy.deepcopy(index)
    bad["journals"]["fx-beta"]["publisher"] = "nope"
    env.files["index.signed.json"] = envelope(bad)
    assert jr.sync(force=True)["code"] == "index_invalid"


# ------------------------------ 离线 ------------------------------
def test_offline_without_cache_returns_nothing_for_seed_fallback(env):
    env.offline = True
    out = jr.sync(force=True)
    assert (out["fetch"], out["code"], out["rev"], out["catalog"]) == (
        "offline",
        "network",
        None,
        None,
    )


def test_offline_with_cache_serves_cache(env):
    jr.sync(force=True)
    env.offline = True
    out = jr.sync(force=True)
    assert (out["fetch"], out["rev"]) == ("offline", 5) and out["catalog"] is not None


def test_only_the_journal_sync_switch_stops_requests(env, monkeypatch):
    monkeypatch.setenv("TAVOTTO_NO_JOURNAL_SYNC", "1")
    assert jr.sync(force=True)["fetch"] == "disabled"
    assert jr.ensure_journal("fx-beta")["state"] == "offline"
    assert env.calls == []


def test_no_telemetry_switch_does_not_stop_catalog_sync(env, monkeypatch):
    """与 updater 的先例一致：`TAVOTTO_NO_TELEMETRY` 只管遥测，不代管目录同步（用户 2026-11-04 定）。"""
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    assert jr.sync(force=True)["fetch"] == "updated"


def test_corrupt_cache_is_treated_as_missing(env):
    jr.sync(force=True)
    (jr.cache_dir() / "index.signed.json").write_bytes(b"garbage")
    env.offline = True
    assert jr.sync(force=True)["catalog"] is None


# ------------------------------ 刊包按需下载 ------------------------------
def test_ensure_downloads_once_and_second_use_is_local(env):
    jr.sync(force=True)
    out = jr.ensure_journal("fx-beta")
    assert out["state"] == "ready" and out["bundle"]["journal"]["rules"]
    reads = env.count("journals/")
    assert jr.ensure_journal("fx-beta")["state"] == "ready"
    assert env.count("journals/") == reads  # 本地命中：不再请求（服务端也就不计数）
    cached = jr.load_cached()
    assert "rules" in cached.catalog["journals"]["fx-beta"] and cached.loaded_ids() == ["fx-beta"]
    assert env.count("device") == 1  # 令牌只领一次，存盘复用


def test_bundle_survives_index_update_without_redownload(env):
    jr.sync(force=True)
    jr.ensure_journal("fx-beta")
    env.files = build_site(_catalog(6), rev=6)
    env.rev = 6
    assert jr.sync(force=True)["fetch"] == "updated"
    reads = env.count("journals/")
    assert "fx-beta" in jr.load_cached().loaded_ids()
    assert jr.ensure_journal("fx-beta")["state"] == "ready" and env.count("journals/") == reads


def test_bundle_with_wrong_hash_is_rejected_and_not_stored(env):
    jr.sync(force=True)
    path = "r5/journals/fx-beta.json"
    env.files[path] = env.files[path].replace(b"Fixture", b"Fixturf", 1)
    out = jr.ensure_journal("fx-beta")
    assert (out["state"], out["code"]) == ("rejected", "bundle_invalid")
    assert not list((jr.cache_dir() / "bundles").glob("*.json"))


def test_bundle_failing_catalog_validation_is_discarded(env):
    """签名索引给出了哈希，但内容过不了目录校验（出处缺失）：整包作废，不部分采信。"""
    cat = _catalog()
    bad = copy.deepcopy(cat)
    rule = next(iter(bad["journals"]["fx-beta"]["rules"].values()))
    rule["src"] = "nonexistent-source"
    env.files = build_site(bad, rev=5)
    jr.sync(force=True)
    out = jr.ensure_journal("fx-beta")
    assert (out["state"], out["code"]) == ("rejected", "catalog_invalid")
    assert jr.load_cached().loaded_ids() == []


def test_quota_exceeded_reports_wait_and_keeps_picker_usable(env):
    jr.sync(force=True)
    env.quota = {"code": "quota_day", "retry_after_s": 4242}
    out = jr.ensure_journal("fx-beta")
    assert (out["state"], out["code"], out["retry_after_s"]) == ("quota", "quota_day", 4242)
    assert jr.load_cached().loaded_ids() == []
    env.quota = {"code": "ip_day", "retry_after_s": 10}
    assert jr.ensure_journal("fx-gamma")["code"] == "ip_day"


def test_unknown_device_token_is_reissued_once(env):
    jr.sync(force=True)
    jr.ensure_journal("fx-alpha")
    env.known_tokens.clear()  # 服务端换库：旧令牌不认了
    assert jr.ensure_journal("fx-beta")["state"] == "ready"
    assert env.count("device") == 2


def test_stale_rev_triggers_one_index_refresh_and_retry(env):
    jr.sync(force=True)
    env.files = build_site(_catalog(6), rev=6)
    env.rev = 6
    out = jr.ensure_journal("fx-beta")
    assert out["state"] == "ready"
    assert jr.load_cached().rev == 6


def test_offline_download_without_bundle_reports_offline(env):
    jr.sync(force=True)
    env.offline = True
    out = jr.ensure_journal("fx-beta")
    assert (out["state"], out["code"]) == ("offline", "network")


def test_ensure_without_any_index_is_offline(env):
    assert jr.ensure_journal("fx-beta")["state"] == "offline"


def test_unknown_and_malformed_ids(env):
    jr.sync(force=True)
    assert jr.ensure_journal("no-such")["state"] == "unknown"
    assert jr.ensure_journal("../etc/passwd")["state"] == "unknown"
    assert env.count("journals/") == 0


def test_seed_journals_are_never_downloaded(env, monkeypatch):
    seed = _catalog(1)
    monkeypatch.setattr(
        jr, "seed_catalog", lambda: {"publishers": seed["publishers"], "journals": seed["journals"]}
    )
    jr.sync(force=True)
    out = jr.ensure_journal("fx-beta")
    assert out["state"] == "ready" and env.count("journals/") == 0 and env.count("device") == 0
    assert "fx-beta" in jr.load_cached().loaded_ids()


# ------------------------------ 快照 ------------------------------
def test_snapshot_fetch_is_content_addressed(env):
    jr.sync(force=True)
    p = jr.snapshot_path(f"snapshots/{SNAP_SHA}.webp")
    assert p is not None and p.read_bytes() == SNAP_BYTES
    n = len(env.calls)
    assert jr.snapshot_path(f"snapshots/{SNAP_SHA}.webp") == p and len(env.calls) == n


def test_snapshot_with_wrong_content_or_bad_name_is_refused(env):
    jr.sync(force=True)
    other = hashlib.sha256(b"other").hexdigest()
    env.snapshots[other] = b"not what the name says"
    assert jr.snapshot_path(f"snapshots/{other}.webp") is None
    assert jr.snapshot_path("snapshots/../../x.webp") is None
    assert jr.snapshot_path("snapshots/ABC.webp") is None


def test_snapshot_offline_is_none(env):
    jr.sync(force=True)
    env.offline = True
    assert jr.snapshot_path(f"snapshots/{SNAP_SHA}.webp") is None


# ------------------------------ 来源与发布闸门 ------------------------------
def test_production_code_does_not_trust_the_test_key(monkeypatch):
    """生产代码里只有 prod-1：测试钥（公钥与私钥种子）只许出现在 tests/ 里。"""
    import importlib

    mod = importlib.reload(jr)  # 撤掉 env 夹具注入的测试钥，看真实常量
    assert set(mod.TRUSTED_KEYS) == {"prod-1"}
    assert mod.TRUSTED_KEYS["prod-1"] == "fYO5adiBp/EH0ug9cRcsC3bhij5l1V4Em4rcfYGLW5Q="
    test_pub = b64(public_key(TEST_SEED))
    for f in (ROOT / "src").rglob("*.py"):
        assert test_pub not in f.read_text("utf-8"), f
    assert mod.CATALOG_URLS == (
        "https://tavotto.cn/v1/journal-catalog/",
        "https://tavotto.com/v1/journal-catalog/",
    )


def test_override_url_accepts_https_only(monkeypatch):
    monkeypatch.setenv(jr.URL_ENV, "https://x.invalid/c")
    assert jr.base_urls() == ("https://x.invalid/c/",)
    monkeypatch.setenv(jr.URL_ENV, "http://127.0.0.1:9/c/")
    assert jr.base_urls() == ()
    monkeypatch.setenv(jr.URL_ENV, "http://evil.invalid/c/")
    assert jr.base_urls() == ()


# ------------------------------ Flask 端点（薄壳） ------------------------------
@pytest.fixture
def client(env):
    from tavotto import app as appmod

    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_endpoints_sync_journal_and_asset(client, env):
    r = client.post("/api/journal-catalog/sync", json={"force": True, "have": 0}).get_json()
    assert r["fetch"] == "updated" and r["rev"] == 5 and r["catalog"]["journals"]
    r = client.post("/api/journal-catalog/journal", json={"id": "fx-beta"}).get_json()
    assert r["state"] == "ready" and r["bundle"]["journal_id"] == "fx-beta"
    img = client.get(f"/api/journal-catalog/assets/snapshots/{SNAP_SHA}.webp")
    assert img.status_code == 200 and img.data == SNAP_BYTES and img.mimetype == "image/webp"


def test_endpoints_never_error_for_bad_input(client, env):
    assert client.post("/api/journal-catalog/sync", data="not json").status_code == 200
    assert (
        client.post("/api/journal-catalog/journal", json={"id": 7}).get_json()["state"] == "unknown"
    )
    assert client.post("/api/journal-catalog/journal").get_json()["state"] == "unknown"
    assert client.get("/api/journal-catalog/assets/snapshots/../../etc/passwd").status_code == 404
    assert client.get("/api/journal-catalog/assets/snapshots/nothex.webp").status_code == 404


# ------------------------------ app 与 Agent 共享令牌与缓存 ------------------------------
def test_second_party_reuses_token_and_downloaded_bundles(env, monkeypatch):
    """先用的一方领令牌、下刊包；后来的一方（Agent，另一个进程）读盘复用：不再领令牌，也不再下载。"""
    jr.sync(force=True)
    jr.ensure_journal("fx-beta")
    monkeypatch.setenv(jr.CLIENT_ENV, "agent-codex")
    jr.ensure_journal("fx-beta")
    jr.ensure_journal("fx-alpha")  # 新刊用同一个令牌下
    assert env.count("device") == 1
    assert {h["X-Tavotto-Client"] for _, _, h in env.calls} == {"app", "agent-codex"}
    toks = {h["X-Tavotto-Device"] for _, u, h in env.calls if "journals/" in u}
    assert len(toks) == 1


def test_client_type_is_a_closed_shape(monkeypatch):
    for good in ("app", "agent-codex", "agent-claude-code"):
        monkeypatch.setenv(jr.CLIENT_ENV, good)
        assert jr.client_type() == good
    for bad in ("", "agent-", "Agent-X!", "root", "agent-" + "a" * 40):
        monkeypatch.setenv(jr.CLIENT_ENV, bad)
        assert jr.client_type() == "app"


def test_lost_or_corrupt_token_file_reissues_once(env):
    jr.sync(force=True)
    jr.ensure_journal("fx-beta")
    (jr.cache_dir() / "device.json").write_bytes(b"\x00garbage")
    jr.ensure_journal("fx-alpha")
    assert env.count("device") == 2
    (jr.cache_dir() / "device.json").unlink()
    jr.ensure_journal("fx-gamma")
    assert env.count("device") == 3


def test_stale_lock_file_does_not_block(env):
    import os
    import time

    jr.cache_dir().mkdir(parents=True, exist_ok=True)
    lock = jr.cache_dir() / "lock"
    lock.write_bytes(b"")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert jr.sync(force=True)["fetch"] == "updated"
    assert not lock.exists()


# ------------------------------ 两个入口：.cn 与 .com，先后按大陆环境 ------------------------------
CN = "https://tavotto.cn/v1/journal-catalog/"
COM = "https://tavotto.com/v1/journal-catalog/"


def test_two_entries_ordered_by_the_shared_region_rule(env, monkeypatch):
    """先后只认 `contactsend.region_is_cn`（壳的 `TAVOTTO_NETWORK_REGION=cn` 或界面语言 zh-CN），不另起一套。"""
    monkeypatch.setattr(jr, "CATALOG_URLS", (CN, COM))
    monkeypatch.delenv("TAVOTTO_NETWORK_REGION", raising=False)
    assert jr.base_urls("zh-CN") == (CN, COM)
    assert jr.base_urls("en-US") == (COM, CN)
    assert jr.base_urls(None) == (COM, CN)
    monkeypatch.setenv("TAVOTTO_NETWORK_REGION", "cn")
    assert jr.base_urls("en-US") == (CN, COM)
    monkeypatch.setenv("TAVOTTO_NETWORK_REGION", "global")
    assert jr.base_urls(None) == (COM, CN)


def test_last_seen_locale_orders_calls_without_a_locale(env, monkeypatch):
    monkeypatch.setattr(jr, "CATALOG_URLS", (CN, COM))
    monkeypatch.delenv("TAVOTTO_NETWORK_REGION", raising=False)
    jr.remember_locale("zh-CN")
    assert jr.base_urls() == (CN, COM)
    jr.remember_locale("en-US")
    assert jr.base_urls() == (COM, CN)
    jr.remember_locale("<script>")  # 不合形状的不记
    assert jr.base_urls() == (COM, CN)


def test_first_entry_down_falls_over_to_the_second(env, monkeypatch):
    monkeypatch.setattr(jr, "CATALOG_URLS", (CN, COM))
    monkeypatch.delenv("TAVOTTO_NETWORK_REGION", raising=False)
    served = []

    def two_hosts(method, url, cap, headers):
        served.append(url.split("/v1/")[0])
        if url.startswith(CN):
            raise jr.RemoteCatalogError("network", "cn down")
        return env(method, BASE + url[len(COM) :], cap, headers)

    monkeypatch.setattr(jr, "_request", two_hosts)
    out = jr.sync(force=True, ui_locale="zh-CN")
    assert out["fetch"] == "updated"
    assert served[0] == "https://tavotto.cn" and served[-1] == "https://tavotto.com"
    # 令牌与刊包同样按先后换入口；两个入口背后是同一个后端，所以同一枚令牌通用
    assert jr.ensure_journal("fx-beta", ui_locale="zh-CN")["state"] == "ready"


def test_quota_from_the_first_entry_does_not_try_the_second(env, monkeypatch):
    """额度用完是后端的结论，两个入口背后是同一份计数：换入口没有意义，也不该多打一次请求。"""
    monkeypatch.setattr(jr, "CATALOG_URLS", (CN, COM))
    monkeypatch.delenv("TAVOTTO_NETWORK_REGION", raising=False)
    hosts = []

    def two_hosts(method, url, cap, headers):
        hosts.append(url.split("/v1/")[0])
        return env(method, BASE + url.split("/v1/journal-catalog/")[1], cap, headers)

    monkeypatch.setattr(jr, "_request", two_hosts)
    jr.sync(force=True, ui_locale="zh-CN")
    env.quota = {"code": "quota_day", "retry_after_s": 5}
    hosts.clear()
    out = jr.ensure_journal("fx-beta", ui_locale="zh-CN")
    assert out["state"] == "quota" and set(hosts) == {"https://tavotto.cn"}
